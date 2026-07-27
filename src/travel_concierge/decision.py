from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol
import json
import os
from urllib.request import Request, urlopen

from .enrichment import AirportCongestionEstimator, CarrierOtpEstimator, OpenMeteoWeatherClient


class DecisionAction(str, Enum):
    AUTO_REBOOK = "AUTO_REBOOK"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    MONITOR = "MONITOR"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class MediumSeverityContext:
    buffer_minutes: int
    required_mct: int
    risk_probability: float
    airport: str
    terminal_transfer: str
    carrier: str
    card_tier: str
    route: str = ""
    flight_iata: str = ""
    origin_international: bool | None = None
    destination_international: bool | None = None


@dataclass(frozen=True)
class DecisionEvidence:
    weather: dict[str, Any] | None = None
    congestion: dict[str, Any] | None = None
    otp: dict[str, Any] | None = None
    signals: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionResult:
    action: DecisionAction
    should_rebook: bool
    requires_approval: bool
    confidence: float
    summary: str
    evidence: DecisionEvidence
    raw: dict[str, Any] = field(default_factory=dict)


class DecisionAgent(Protocol):
    def decide(self, context: MediumSeverityContext) -> DecisionResult: ...


class HeuristicDecisionAgent:
    """Fast fallback when no LLM credentials are configured."""

    def __init__(self, weather_client: OpenMeteoWeatherClient | None = None, congestion_estimator: AirportCongestionEstimator | None = None, otp_estimator: CarrierOtpEstimator | None = None):
        self.weather_client = weather_client or OpenMeteoWeatherClient()
        self.congestion_estimator = congestion_estimator or AirportCongestionEstimator()
        self.otp_estimator = otp_estimator or CarrierOtpEstimator()

    def decide(self, context: MediumSeverityContext) -> DecisionResult:
        evidence = self._collect_evidence(context)
        storm = evidence.weather["storm_risk"] if evidence.weather else False
        congestion = evidence.congestion["congestion_score"] if evidence.congestion else 0.0
        otp = evidence.otp["on_time_probability"] if evidence.otp else 0.8
        risk_score = _heuristic_risk_score(context.risk_probability, storm, congestion, otp)

        if risk_score >= 0.85:
            return DecisionResult(DecisionAction.AUTO_REBOOK, True, False, risk_score, "High-confidence disruption. Route to rebooking now.", evidence)
        if risk_score >= 0.60:
            return DecisionResult(DecisionAction.REQUEST_APPROVAL, False, True, risk_score, "Moderate confidence. Ask the card member for approval.", evidence)
        if risk_score >= 0.35:
            return DecisionResult(DecisionAction.MONITOR, False, False, risk_score, "Insufficient evidence to rebook yet. Continue monitoring.", evidence)
        return DecisionResult(DecisionAction.ESCALATE, False, False, risk_score, "Signal quality is too weak or contradictory. Escalate to a human.", evidence)

    def _collect_evidence(self, context: MediumSeverityContext) -> DecisionEvidence:
        weather = None
        congestion = None
        otp = None

        try:
            weather_snapshot = self.weather_client.get_weather(context.airport)
            weather = {
                "airport_code": weather_snapshot.airport_code,
                "storm_risk": weather_snapshot.storm_risk,
                "storm_likelihood": weather_snapshot.storm_likelihood,
                "weather_code": weather_snapshot.weather_code,
                "temperature_c": weather_snapshot.temperature_c,
            }
        except Exception as exc:  # noqa: BLE001 - tool should degrade gracefully
            weather = {"error": str(exc)}

        try:
            congestion_snapshot = self.congestion_estimator.get_airport_congestion(context.airport, max(context.buffer_minutes, 15))
            congestion = {
                "airport_code": congestion_snapshot.airport_code,
                "congestion_score": congestion_snapshot.congestion_score,
                "label": congestion_snapshot.label,
                "rationale": congestion_snapshot.rationale,
            }
        except Exception as exc:  # noqa: BLE001
            congestion = {"error": str(exc)}

        try:
            route = context.route or f"{context.airport}-{context.flight_iata[-3:] if context.flight_iata else context.airport}"
            otp_snapshot = self.otp_estimator.get_carrier_otp_stats(context.carrier, route)
            otp = {
                "carrier": otp_snapshot.carrier,
                "route": otp_snapshot.route,
                "on_time_probability": otp_snapshot.on_time_probability,
                "recent_delay_risk": otp_snapshot.recent_delay_risk,
                "confidence": otp_snapshot.confidence,
            }
        except Exception as exc:  # noqa: BLE001
            otp = {"error": str(exc)}

        return DecisionEvidence(weather=weather, congestion=congestion, otp=otp, signals={"buffer_minutes": context.buffer_minutes, "required_mct": context.required_mct, "risk_probability": context.risk_probability})


class OpenAIReActDecisionAgent:
    """LLM-backed ReAct loop using an OpenAI-compatible chat completion API."""

    def __init__(self, *, model: str | None = None, api_key: str | None = None, base_url: str | None = None, weather_client: OpenMeteoWeatherClient | None = None, congestion_estimator: AirportCongestionEstimator | None = None, otp_estimator: CarrierOtpEstimator | None = None):
        self.model = model or os.environ.get("TRAVEL_CONCIERGE_LLM_MODEL", "llama-3.1-70b-versatile")
        self.api_key = api_key or os.environ.get("TRAVEL_CONCIERGE_LLM_API_KEY")
        self.base_url = (base_url or os.environ.get("TRAVEL_CONCIERGE_LLM_BASE_URL", "https://api.groq.com/openai/v1")).rstrip("/")
        self.weather_client = weather_client or OpenMeteoWeatherClient()
        self.congestion_estimator = congestion_estimator or AirportCongestionEstimator()
        self.otp_estimator = otp_estimator or CarrierOtpEstimator()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def decide(self, context: MediumSeverityContext) -> DecisionResult:
        if not self.is_configured():
            return HeuristicDecisionAgent(self.weather_client, self.congestion_estimator, self.otp_estimator).decide(context)

        tools = self._tool_specs()
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": json.dumps(_serialize_context(context), ensure_ascii=True)},
        ]

        while True:
            response = self._chat(messages, tools)
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []

            if tool_calls:
                messages.append(message)
                for tool_call in tool_calls:
                    tool_name = tool_call["function"]["name"]
                    arguments = json.loads(tool_call["function"].get("arguments") or "{}")
                    tool_result = self._run_tool(tool_name, arguments)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_name,
                            "content": json.dumps(tool_result, ensure_ascii=True),
                        }
                    )
                continue

            content = message.get("content") or "{}"
            parsed = _safe_json(content)
            return _decision_from_llm(parsed, context)

    def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = json.dumps({"model": self.model, "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0.2}).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        with urlopen(request, timeout=40) as response:
            return json.loads(response.read().decode("utf-8"))

    def _tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Check if there is storm-like weather at an airport right now.",
                    "parameters": {"type": "object", "properties": {"airport_code": {"type": "string"}}, "required": ["airport_code"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_airport_congestion",
                    "description": "Estimate airport congestion for a time window.",
                    "parameters": {"type": "object", "properties": {"airport_code": {"type": "string"}, "time_window_minutes": {"type": "integer"}}, "required": ["airport_code", "time_window_minutes"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_carrier_otp_stats",
                    "description": "Estimate carrier on-time performance for a route.",
                    "parameters": {"type": "object", "properties": {"carrier": {"type": "string"}, "route": {"type": "string"}}, "required": ["carrier", "route"]},
                },
            },
        ]

    def _system_prompt(self) -> str:
        return (
            "You are a travel disruption decision agent. Use the provided signals and tools to decide whether a medium-severity disruption should be routed to rebooking. "
            "Return a JSON object with keys: action, should_rebook, requires_approval, confidence, summary. "
            "action must be one of AUTO_REBOOK, REQUEST_APPROVAL, MONITOR, ESCALATE."
        )

    def _run_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "get_weather":
            snapshot = self.weather_client.get_weather(arguments["airport_code"])
            return {"airport_code": snapshot.airport_code, "storm_risk": snapshot.storm_risk, "storm_likelihood": snapshot.storm_likelihood, "weather_code": snapshot.weather_code}
        if tool_name == "get_airport_congestion":
            snapshot = self.congestion_estimator.get_airport_congestion(arguments["airport_code"], int(arguments["time_window_minutes"]))
            return {"airport_code": snapshot.airport_code, "congestion_score": snapshot.congestion_score, "label": snapshot.label, "rationale": snapshot.rationale}
        if tool_name == "get_carrier_otp_stats":
            snapshot = self.otp_estimator.get_carrier_otp_stats(arguments["carrier"], arguments["route"])
            return {"carrier": snapshot.carrier, "route": snapshot.route, "on_time_probability": snapshot.on_time_probability, "recent_delay_risk": snapshot.recent_delay_risk, "confidence": snapshot.confidence}
        raise ValueError(f"Unknown tool: {tool_name}")


GroqReActDecisionAgent = OpenAIReActDecisionAgent


def _serialize_context(context: MediumSeverityContext) -> dict[str, Any]:
    return {
        "buffer_minutes": context.buffer_minutes,
        "required_mct": context.required_mct,
        "risk_probability": context.risk_probability,
        "airport": context.airport,
        "terminal_transfer": context.terminal_transfer,
        "carrier": context.carrier,
        "card_tier": context.card_tier,
        "route": context.route,
        "flight_iata": context.flight_iata,
        "origin_international": context.origin_international,
        "destination_international": context.destination_international,
    }


def _safe_json(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {"summary": content}
    except json.JSONDecodeError:
        return {"summary": content}


def _decision_from_llm(parsed: dict[str, Any], context: MediumSeverityContext) -> DecisionResult:
    action_name = str(parsed.get("action", "MONITOR")).upper()
    action = DecisionAction[action_name] if action_name in DecisionAction.__members__ else DecisionAction.MONITOR
    should_rebook = bool(parsed.get("should_rebook", action == DecisionAction.AUTO_REBOOK))
    requires_approval = bool(parsed.get("requires_approval", action == DecisionAction.REQUEST_APPROVAL))
    confidence = float(parsed.get("confidence", 0.5))
    summary = str(parsed.get("summary", "LLM decision completed."))
    evidence = DecisionEvidence(signals={"buffer_minutes": context.buffer_minutes, "required_mct": context.required_mct, "risk_probability": context.risk_probability})
    return DecisionResult(action, should_rebook, requires_approval, confidence, summary, evidence, raw=parsed)


def _heuristic_risk_score(risk_probability: float, storm: bool, congestion: float, otp: float) -> float:
    score = risk_probability * 0.6
    score += 0.15 if storm else 0.0
    score += min(0.15, congestion * 0.15)
    score += max(0.0, (0.9 - otp) * 0.3)
    return max(0.0, min(score, 1.0))
