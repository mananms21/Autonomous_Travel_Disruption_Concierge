from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from .client import AviationstackClient
from .monitoring import AviationstackStatusProvider, FlightLeg, MonitoringEngine


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> None:
    access_key = os.environ.get("AVIATIONSTACK_ACCESS_KEY")
    flight_iata = os.environ.get("AVIATIONSTACK_FLIGHT_IATA")
    scheduled_departure = os.environ.get("AVIATIONSTACK_SCHEDULED_DEPARTURE")
    scheduled_arrival = os.environ.get("AVIATIONSTACK_SCHEDULED_ARRIVAL")
    departure_airport = os.environ.get("AVIATIONSTACK_DEPARTURE_AIRPORT", "JFK")
    arrival_airport = os.environ.get("AVIATIONSTACK_ARRIVAL_AIRPORT", "LHR")

    if not access_key or not flight_iata or not scheduled_departure or not scheduled_arrival:
        raise SystemExit(
            "Set AVIATIONSTACK_ACCESS_KEY, AVIATIONSTACK_FLIGHT_IATA, AVIATIONSTACK_SCHEDULED_DEPARTURE, and AVIATIONSTACK_SCHEDULED_ARRIVAL"
        )

    leg = FlightLeg(
        leg_id="leg-1",
        flight_iata=flight_iata,
        scheduled_departure=_parse_timestamp(scheduled_departure),
        scheduled_arrival=_parse_timestamp(scheduled_arrival),
        departure_airport=departure_airport,
        arrival_airport=arrival_airport,
    )

    client = AviationstackClient(access_key=access_key)
    provider = AviationstackStatusProvider(client)
    engine = MonitoringEngine(provider)

    snapshot, disruption = engine.monitor_leg(leg)
    print(json.dumps({"snapshot": snapshot.__dict__, "disruption": None if disruption is None else disruption.__dict__}, default=str, indent=2))


if __name__ == "__main__":
    main()