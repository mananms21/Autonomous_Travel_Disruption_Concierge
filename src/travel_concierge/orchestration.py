from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import Any

from .decision import DecisionAction, DecisionAgent, MediumSeverityContext, OpenAIReActDecisionAgent
from .mct import lookup_mct
from .monitoring import Disruption, DisruptionType, FlightLeg, FlightStatusProvider, MonitoringEngine, Severity, evaluate_connection, summarize_disruption
from .rebooking import RebookingAgent, RebookingOutcome, RebookingRequest


@dataclass(frozen=True)
class DisruptionRoutingOutcome:
    leg_id: str
    snapshot: Any
    disruption: Disruption | None
    summary: str
    decision: Any
    connection_disruption: Disruption | None = None
    rebooking: RebookingOutcome | None = None


class TravelDisruptionOrchestrator:
    def __init__(self, provider: FlightStatusProvider, decision_agent: DecisionAgent | None = None, rebooking_agent: RebookingAgent | None = None):
        self.monitoring_engine = MonitoringEngine(provider)
        self.decision_agent = decision_agent or OpenAIReActDecisionAgent()
        self.rebooking_agent = rebooking_agent or RebookingAgent()

    def process_itinerary(self, legs: list[FlightLeg], card_tier: str = "PLATINUM", pnr: str = "UNKNOWN", card_member_id: str = "UNKNOWN") -> list[DisruptionRoutingOutcome]:
        results: list[DisruptionRoutingOutcome] = []
        for index, leg in enumerate(legs):
            snapshot, disruption = self.monitoring_engine.monitor_leg(leg)
            decision = None
            connection_disruption = None
            rebooking = None

            if index < len(legs) - 1:
                connection_disruption = evaluate_connection(leg, legs[index + 1])

            routed_disruption = connection_disruption or disruption
            if routed_disruption and routed_disruption.severity in {Severity.MEDIUM, Severity.LOW} and routed_disruption.type not in {DisruptionType.CANCELLED, DisruptionType.DIVERTED}:
                context = self._build_medium_context(leg, snapshot, legs[index + 1] if index < len(legs) - 1 else None, routed_disruption, card_tier)
                decision = self.decision_agent.decide(context)
            elif routed_disruption and routed_disruption.severity == Severity.HIGH:
                decision = {"action": DecisionAction.AUTO_REBOOK.value, "should_rebook": True, "summary": "High severity event auto-routed to rebooking."}

            if decision and self._should_rebook(decision):
                rebooking = self.rebooking_agent.initiate_rebooking(
                    RebookingRequest(
                        event_id=f"{leg.leg_id}:{leg.flight_iata}:{snapshot.observed_at.isoformat()}",
                        pnr=pnr,
                        card_member_id=card_member_id,
                        card_tier=card_tier,
                        disrupted_leg_id=leg.leg_id,
                        trigger_type=self._decision_action_name(decision),
                        rationale=self._decision_summary(decision),
                        confidence=self._decision_confidence(decision),
                        disrupted_leg=leg,
                        cabin_class="economy",
                    )
                )

            results.append(
                DisruptionRoutingOutcome(
                    leg_id=leg.leg_id,
                    snapshot=snapshot,
                    disruption=disruption,
                    summary=summarize_disruption(disruption),
                    decision=decision,
                    connection_disruption=connection_disruption,
                    rebooking=rebooking,
                )
            )
        return results

    @staticmethod
    def _should_rebook(decision: Any) -> bool:
        if hasattr(decision, "should_rebook"):
            return bool(decision.should_rebook)
        if isinstance(decision, dict):
            return bool(decision.get("should_rebook"))
        return False

    @staticmethod
    def _decision_action_name(decision: Any) -> str:
        if hasattr(decision, "action"):
            action = getattr(decision, "action")
            return action.value if hasattr(action, "value") else str(action)
        if isinstance(decision, dict):
            return str(decision.get("action", "AUTO_REBOOK"))
        return "AUTO_REBOOK"

    @staticmethod
    def _decision_summary(decision: Any) -> str:
        if hasattr(decision, "summary"):
            return str(getattr(decision, "summary"))
        if isinstance(decision, dict):
            return str(decision.get("summary", "Rebook triggered by disruption detection."))
        return "Rebook triggered by disruption detection."

    @staticmethod
    def _decision_confidence(decision: Any) -> float:
        if hasattr(decision, "confidence"):
            return float(getattr(decision, "confidence"))
        if isinstance(decision, dict):
            return float(decision.get("confidence", 0.9))
        return 0.9

    def _build_medium_context(
        self,
        leg: FlightLeg,
        snapshot: Any,
        next_leg: FlightLeg | None,
        routed_disruption: Disruption,
        card_tier: str,
    ) -> MediumSeverityContext:
        if next_leg is not None:
            band = lookup_mct(
                leg.arrival_airport,
                terminal_change=leg.arr_terminal != next_leg.dep_terminal,
                intl_to_domestic=leg.is_international and not next_leg.is_international,
                origin_international=leg.is_international,
                destination_international=next_leg.is_international,
            )
            buffer_minutes = max(0, int((next_leg.scheduled_departure.astimezone(timezone.utc) - snapshot.current_expected_arrival.astimezone(timezone.utc)).total_seconds() / 60))
            required_mct = band.recommended_minutes
            origin_international = leg.is_international
            destination_international = next_leg.is_international
            route = next_leg.route or f"{next_leg.departure_airport}-{next_leg.arrival_airport}"
            terminal_transfer = f"{leg.arr_terminal or 'unknown'}→{next_leg.dep_terminal or 'unknown'}"
        else:
            buffer_minutes = max(0, int((snapshot.current_expected_departure.astimezone(timezone.utc) - leg.scheduled_departure.astimezone(timezone.utc)).total_seconds() / 60))
            required_mct = 45
            origin_international = leg.is_international
            destination_international = None
            route = leg.route or f"{leg.departure_airport}-{leg.arrival_airport}"
            terminal_transfer = f"{leg.dep_terminal or 'unknown'}→{leg.arr_terminal or 'unknown'}"

        risk_probability = 0.5 if routed_disruption.severity == Severity.MEDIUM else 0.25
        if routed_disruption.type == DisruptionType.MISSED_CONNECTION_WATCH:
            risk_probability = 0.40

        return MediumSeverityContext(
            buffer_minutes=buffer_minutes,
            required_mct=required_mct,
            risk_probability=risk_probability,
            airport=leg.arrival_airport,
            terminal_transfer=terminal_transfer,
            carrier=leg.carrier or "UNKNOWN",
            card_tier=card_tier,
            route=route,
            flight_iata=leg.flight_iata,
            origin_international=origin_international,
            destination_international=destination_international,
        )
