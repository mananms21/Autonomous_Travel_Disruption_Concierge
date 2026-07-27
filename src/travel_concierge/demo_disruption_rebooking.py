from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

from .monitoring import FlightLeg, FlightStatusSnapshot, classify_event
from .rebooking import AviationstackRouteSearchProvider, RebookingAgent, RebookingRequest, RebookingState


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def main() -> None:
    access_key = os.environ.get("AVIATIONSTACK_ACCESS_KEY")
    if not access_key:
        raise SystemExit("Set AVIATIONSTACK_ACCESS_KEY before running this demo.")

    departure_airport = os.environ.get("AVIATIONSTACK_DEPARTURE_AIRPORT", "HYD")
    arrival_airport = os.environ.get("AVIATIONSTACK_ARRIVAL_AIRPORT", "GAU")
    carrier = os.environ.get("DISRUPTED_CARRIER", "AI")
    pnr = os.environ.get("DISRUPTED_PNR", "PNR-DEMO-001")
    card_member_id = os.environ.get("CARD_MEMBER_ID", "CM-DEMO-001")
    card_tier = os.environ.get("CARD_TIER", "PLATINUM")
    cabin_class = os.environ.get("CABIN_CLASS", "economy")
    reference_fare = _env_float("REFERENCE_FARE", 260.0)
    api_only = _env_bool("API_ONLY_SEARCH", False)

    now = datetime.now(timezone.utc)
    scheduled_departure = now + timedelta(hours=1)
    scheduled_arrival = now + timedelta(hours=4)

    print("[1/5] Simulating disruption event for an active itinerary...")

    disrupted_leg = FlightLeg(
        leg_id="sim-leg-1",
        flight_iata=os.environ.get("DISRUPTED_FLIGHT_IATA", "AI203"),
        scheduled_departure=scheduled_departure,
        scheduled_arrival=scheduled_arrival,
        departure_airport=departure_airport,
        arrival_airport=arrival_airport,
        carrier=carrier,
        route=f"{departure_airport}-{arrival_airport}",
    )

    simulated_snapshot = FlightStatusSnapshot(
        leg_id=disrupted_leg.leg_id,
        flight_iata=disrupted_leg.flight_iata,
        observed_at=now,
        status="CANCELLED",
        departure_scheduled=scheduled_departure,
        arrival_scheduled=scheduled_arrival,
    )

    disruption = classify_event(disrupted_leg, simulated_snapshot)
    if disruption is None:
        raise SystemExit("Simulation did not produce a disruption.")
    print(f"[2/5] Disruption detected: {disruption.type.value} ({disruption.severity.value})")

    request = RebookingRequest(
        event_id=f"evt-{uuid4().hex[:12]}",
        pnr=pnr,
        card_member_id=card_member_id,
        card_tier=card_tier,
        disrupted_leg_id=disrupted_leg.leg_id,
        trigger_type=disruption.type.value,
        rationale=disruption.details,
        confidence=0.99,
        disrupted_leg=disrupted_leg,
        reference_fare=reference_fare,
        cabin_class=cabin_class,
    )

    api_search_error: str | None = None
    print("[3/5] Searching alternate flight options via API...")
    try:
        strict_provider = AviationstackRouteSearchProvider(access_key=access_key, fallback_to_mock=False, max_results=15)
        outcome = RebookingAgent(provider=strict_provider).initiate_rebooking(request)
        print("[3/5] API search succeeded.")
    except Exception as exc:  # noqa: BLE001 - demo should optionally degrade when provider plan blocks route search
        api_search_error = str(exc)
        print(f"[3/5] API search failed: {api_search_error}")
        if api_only:
            raise
        print("[3/5] Falling back to mock options so the booking flow can continue.")
        fallback_provider = AviationstackRouteSearchProvider(access_key=access_key, fallback_to_mock=True, max_results=15)
        outcome = RebookingAgent(provider=fallback_provider).initiate_rebooking(request)
    if outcome.state != RebookingState.CONFIRMED or outcome.selected_offer is None or outcome.booking is None:
        print("[4/5] Could not confirm a booking from available options.")
        print(
            json.dumps(
                {
                    "event_id": request.event_id,
                    "simulated_disruption": {
                        "type": disruption.type.value,
                        "severity": disruption.severity.value,
                        "details": disruption.details,
                    },
                    "state": outcome.state.value,
                    "summary": outcome.summary,
                },
                indent=2,
            )
        )
        return

    selected = outcome.selected_offer
    print("[4/5] Top option selected and booked.")
    ticket_json = {
        "event_id": request.event_id,
        "api_search_attempted": True,
        "api_search_error": api_search_error,
        "simulated_disruption": {
            "type": disruption.type.value,
            "severity": disruption.severity.value,
            "details": disruption.details,
        },
        "state": outcome.state.value,
        "selected_offer_provider": selected.provider,
        "booked_ticket": {
            "booking_id": outcome.booking.booking_id,
            "confirmation_code": outcome.booking.confirmation_code,
            "provider": outcome.booking.provider,
            "booked_at": outcome.booking.booked_at.isoformat(),
            "flight": {
                "offer_id": selected.offer_id,
                "airline": selected.airline,
                "origin": selected.origin_airport,
                "destination": selected.destination_airport,
                "departure_time": selected.departure_time.isoformat(),
                "arrival_time": selected.arrival_time.isoformat(),
                "date": selected.departure_time.date().isoformat(),
                "price": selected.price,
                "currency": selected.currency,
            },
            "provider_payload": outcome.booking.raw,
        },
        "summary": outcome.summary,
    }
    print("[5/5] Final booked ticket payload:")
    print(json.dumps(ticket_json, indent=2))


if __name__ == "__main__":
    main()
