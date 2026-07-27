from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from .client import AviationstackClient, parse_aviationstack_datetime
from .mct import MCTBand, lookup_mct


class DisruptionType(str, Enum):
    CANCELLED = "CANCELLED"
    DELAYED = "DELAYED"
    DIVERTED = "DIVERTED"
    OPERATIONAL_CHANGE = "OPERATIONAL_CHANGE"
    MISSED_CONNECTION_CONFIRMED = "MISSED_CONNECTION_CONFIRMED"
    MISSED_CONNECTION_HIGH_RISK = "MISSED_CONNECTION_HIGH_RISK"
    MISSED_CONNECTION_WATCH = "MISSED_CONNECTION_WATCH"


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class Disruption:
    type: DisruptionType
    severity: Severity
    requires_confirmation: bool = False
    triggers_connection_recheck: bool = False
    details: str = ""
    source: str = "layer_a"


@dataclass(frozen=True)
class FlightLeg:
    leg_id: str
    flight_iata: str
    scheduled_departure: datetime
    scheduled_arrival: datetime
    departure_airport: str
    arrival_airport: str
    dep_terminal: str | None = None
    arr_terminal: str | None = None
    is_international: bool = False
    carrier: str | None = None
    route: str | None = None


@dataclass(frozen=True)
class FlightStatusSnapshot:
    leg_id: str
    flight_iata: str
    observed_at: datetime
    status: str
    departure_scheduled: datetime | None = None
    departure_estimated: datetime | None = None
    departure_actual: datetime | None = None
    arrival_scheduled: datetime | None = None
    arrival_estimated: datetime | None = None
    arrival_actual: datetime | None = None
    dep_terminal: str | None = None
    arr_terminal: str | None = None
    dep_gate: str | None = None
    arr_gate: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def current_expected_arrival(self) -> datetime:
        return self.arrival_estimated or self.arrival_actual or self.arrival_scheduled or self.observed_at

    @property
    def current_expected_departure(self) -> datetime:
        return self.departure_estimated or self.departure_actual or self.departure_scheduled or self.observed_at


@dataclass(frozen=True)
class MonitoringPolicy:
    mct_minutes_default: int = 40
    delay_high_minutes: int = 120
    delay_medium_minutes: int = 30
    delay_low_minutes: int = 15
    high_risk_ratio: float = 0.5
    watch_ratio: float = 0.15


class FlightStatusProvider(Protocol):
    def fetch_status(self, leg: FlightLeg) -> FlightStatusSnapshot: ...


class AviationstackStatusProvider:
    def __init__(self, client: AviationstackClient):
        self._client = client

    def fetch_status(self, leg: FlightLeg) -> FlightStatusSnapshot:
        payload = self._client.fetch_status_snapshot(leg.flight_iata, flight_date=leg.scheduled_departure.date())
        if not payload:
            raise LookupError(f"No Aviationstack data returned for {leg.flight_iata}")

        record = self._best_match(payload, leg)
        observed_at = datetime.now(timezone.utc)
        departure = record.get("departure", {}) or {}
        arrival = record.get("arrival", {}) or {}
        flight = record.get("flight", {}) or {}

        return FlightStatusSnapshot(
            leg_id=leg.leg_id,
            flight_iata=leg.flight_iata,
            observed_at=observed_at,
            status=str(record.get("flight_status") or record.get("status") or "unknown").upper(),
            departure_scheduled=parse_aviationstack_datetime(departure.get("scheduled")),
            departure_estimated=parse_aviationstack_datetime(departure.get("estimated")),
            departure_actual=parse_aviationstack_datetime(departure.get("actual")),
            arrival_scheduled=parse_aviationstack_datetime(arrival.get("scheduled")),
            arrival_estimated=parse_aviationstack_datetime(arrival.get("estimated")),
            arrival_actual=parse_aviationstack_datetime(arrival.get("actual")),
            dep_terminal=departure.get("terminal"),
            arr_terminal=arrival.get("terminal"),
            dep_gate=departure.get("gate"),
            arr_gate=arrival.get("gate"),
            raw={"flight": flight, "departure": departure, "arrival": arrival, "record": record},
        )

    @staticmethod
    def _best_match(records: list[dict[str, Any]], leg: FlightLeg) -> dict[str, Any]:
        if len(records) == 1:
            return records[0]

        target_dep = leg.scheduled_departure.astimezone(timezone.utc)
        target_arr = leg.scheduled_arrival.astimezone(timezone.utc)

        def score(record: dict[str, Any]) -> float:
            departure = record.get("departure", {}) or {}
            arrival = record.get("arrival", {}) or {}
            scheduled_dep = parse_aviationstack_datetime(departure.get("scheduled"))
            scheduled_arr = parse_aviationstack_datetime(arrival.get("scheduled"))

            dep_diff = abs((scheduled_dep.astimezone(timezone.utc) - target_dep).total_seconds()) if scheduled_dep else 10**9
            arr_diff = abs((scheduled_arr.astimezone(timezone.utc) - target_arr).total_seconds()) if scheduled_arr else 10**9
            return dep_diff + arr_diff

        return min(records, key=score)


def poll_interval_seconds(leg: FlightLeg, now: datetime | None = None) -> int:
    current_time = now or datetime.now(timezone.utc)
    ttd = leg.scheduled_departure.astimezone(timezone.utc) - current_time.astimezone(timezone.utc)
    hours = ttd.total_seconds() / 3600
    if hours < 3:
        return 60
    if hours < 12:
        return 5 * 60
    if hours < 48:
        return 30 * 60
    return 4 * 60 * 60


def classify_event(leg: FlightLeg, snapshot: FlightStatusSnapshot, policy: MonitoringPolicy | None = None) -> Disruption | None:
    policy = policy or MonitoringPolicy()
    status = snapshot.status.upper()

    if status == "CANCELLED":
        return Disruption(DisruptionType.CANCELLED, Severity.HIGH, True, False, f"{leg.flight_iata} was cancelled", "status")

    if status == "DIVERTED":
        return Disruption(DisruptionType.DIVERTED, Severity.HIGH, True, False, f"{leg.flight_iata} was diverted", "status")

    chosen_departure = snapshot.departure_estimated or snapshot.departure_actual or snapshot.departure_scheduled
    chosen_arrival = snapshot.arrival_estimated or snapshot.arrival_actual or snapshot.arrival_scheduled

    if chosen_departure and snapshot.departure_scheduled:
        delta_minutes = int((chosen_departure - snapshot.departure_scheduled).total_seconds() / 60)
        disruption = _delay_disruption(delta_minutes, leg.flight_iata, policy)
        if disruption:
            return disruption

    if chosen_arrival and snapshot.arrival_scheduled:
        delta_minutes = int((chosen_arrival - snapshot.arrival_scheduled).total_seconds() / 60)
        disruption = _delay_disruption(delta_minutes, leg.flight_iata, policy)
        if disruption:
            return disruption

    if any([snapshot.dep_gate, snapshot.arr_gate, snapshot.dep_terminal, snapshot.arr_terminal]):
        if _operational_change_detected(leg, snapshot):
            return Disruption(
                DisruptionType.OPERATIONAL_CHANGE,
                Severity.INFO,
                False,
                True,
                f"Operational change detected for {leg.flight_iata}",
                "gate_or_terminal",
            )

    return None


def _delay_disruption(delta_minutes: int, flight_iata: str, policy: MonitoringPolicy) -> Disruption | None:
    if delta_minutes >= policy.delay_high_minutes:
        return Disruption(DisruptionType.DELAYED, Severity.HIGH, False, False, f"{flight_iata} delayed by {delta_minutes} minutes", "schedule_delta")
    if delta_minutes >= policy.delay_medium_minutes:
        return Disruption(DisruptionType.DELAYED, Severity.MEDIUM, False, False, f"{flight_iata} delayed by {delta_minutes} minutes", "schedule_delta")
    if delta_minutes >= policy.delay_low_minutes:
        return Disruption(DisruptionType.DELAYED, Severity.LOW, False, False, f"{flight_iata} delayed by {delta_minutes} minutes", "schedule_delta")
    return None


def _operational_change_detected(leg: FlightLeg, snapshot: FlightStatusSnapshot) -> bool:
    return any([
        snapshot.dep_gate and snapshot.dep_gate != leg.dep_terminal,
        snapshot.arr_gate and snapshot.arr_gate != leg.arr_terminal,
        snapshot.dep_terminal and snapshot.dep_terminal != leg.dep_terminal,
        snapshot.arr_terminal and snapshot.arr_terminal != leg.arr_terminal,
    ])


def lookup_mct_minutes(airport: str, terminal_change: bool, intl_to_domestic: bool, policy: MonitoringPolicy | None = None) -> int:
    band: MCTBand = lookup_mct(airport, terminal_change=terminal_change, intl_to_domestic=intl_to_domestic)
    return band.recommended_minutes


def evaluate_connection(leg_i: FlightLeg, leg_next: FlightLeg, policy: MonitoringPolicy | None = None) -> Disruption | None:
    policy = policy or MonitoringPolicy()
    effective_arrival = leg_i.scheduled_arrival.astimezone(timezone.utc)
    effective_departure = leg_next.scheduled_departure.astimezone(timezone.utc)
    buffer_minutes = int((effective_departure - effective_arrival).total_seconds() / 60)
    band = lookup_mct(
        leg_i.arrival_airport,
        terminal_change=leg_i.arr_terminal != leg_next.dep_terminal,
        intl_to_domestic=leg_i.is_international and not leg_next.is_international,
        origin_international=leg_i.is_international,
        destination_international=leg_next.is_international,
    )
    mct = band.recommended_minutes

    if buffer_minutes <= 0:
        return Disruption(DisruptionType.MISSED_CONNECTION_CONFIRMED, Severity.HIGH, False, False, f"Connection between {leg_i.flight_iata} and {leg_next.flight_iata} is already missed", "layover_math")

    risk_ratio = 1 - (buffer_minutes / mct)
    if risk_ratio >= policy.high_risk_ratio:
        return Disruption(DisruptionType.MISSED_CONNECTION_HIGH_RISK, Severity.HIGH, False, False, f"Connection buffer {buffer_minutes}m is below MCT {mct}m ({band.connection_type})", "layover_math")
    if risk_ratio >= policy.watch_ratio:
        return Disruption(DisruptionType.MISSED_CONNECTION_WATCH, Severity.MEDIUM, False, False, f"Connection buffer {buffer_minutes}m is close to MCT {mct}m ({band.connection_type})", "layover_math")
    return None


def summarize_disruption(disruption: Disruption | None) -> str:
    if disruption is None:
        return "No disruption detected."
    approval = "approval required" if disruption.requires_confirmation else "no confirmation required"
    return f"{disruption.type.value} [{disruption.severity.value}] - {approval}: {disruption.details}"


class MonitoringEngine:
    def __init__(self, provider: FlightStatusProvider, policy: MonitoringPolicy | None = None):
        self.provider = provider
        self.policy = policy or MonitoringPolicy()
        self._last_snapshots: dict[str, FlightStatusSnapshot] = {}

    def monitor_leg(self, leg: FlightLeg) -> tuple[FlightStatusSnapshot, Disruption | None]:
        snapshot = self.provider.fetch_status(leg)
        disruption = classify_event(leg, snapshot, self.policy)
        self._last_snapshots[leg.leg_id] = snapshot
        return snapshot, disruption

    def monitor_itinerary(self, legs: list[FlightLeg]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for index, leg in enumerate(legs):
            snapshot, disruption = self.monitor_leg(leg)
            result: dict[str, Any] = {"leg_id": leg.leg_id, "snapshot": snapshot, "disruption": disruption}
            if index < len(legs) - 1:
                connection_disruption = evaluate_connection(snapshot_to_leg(leg, snapshot), legs[index + 1], self.policy)
                result["connection_disruption"] = connection_disruption
            results.append(result)
        return results


def snapshot_to_leg(leg: FlightLeg, snapshot: FlightStatusSnapshot) -> FlightLeg:
    return FlightLeg(
        leg_id=leg.leg_id,
        flight_iata=leg.flight_iata,
        scheduled_departure=snapshot.current_expected_departure,
        scheduled_arrival=snapshot.current_expected_arrival,
        departure_airport=leg.departure_airport,
        arrival_airport=leg.arrival_airport,
        dep_terminal=snapshot.dep_terminal or leg.dep_terminal,
        arr_terminal=snapshot.arr_terminal or leg.arr_terminal,
        is_international=leg.is_international,
        carrier=leg.carrier,
        route=leg.route,
    )