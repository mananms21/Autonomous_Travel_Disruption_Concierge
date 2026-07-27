from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from .monitoring import FlightLeg
from .rebooking import RebookingAgent, RebookingRequest


def main() -> None:
    now = datetime.now(timezone.utc)
    disrupted_leg = FlightLeg(
        leg_id="leg-1",
        flight_iata="AI203",
        scheduled_departure=now + timedelta(hours=1),
        scheduled_arrival=now + timedelta(hours=4),
        departure_airport="HYD",
        arrival_airport="GAU",
        carrier="AI",
        route="HYD-GAU",
    )

    request = RebookingRequest(
        event_id="evt-demo-001",
        pnr="PNR123",
        card_member_id="CM001",
        card_tier="PLATINUM",
        disrupted_leg_id=disrupted_leg.leg_id,
        trigger_type="AUTO_REBOOK",
        rationale="Demo handoff after disruption detection.",
        confidence=0.92,
        disrupted_leg=disrupted_leg,
        reference_fare=260.0,
        cabin_class="economy",
    )

    outcome = RebookingAgent().initiate_rebooking(request)
    selected_offer = outcome.selected_offer
    if selected_offer is None or outcome.booking is None:
        print("No flight was booked.")
        return

    ticket_json = {
        "event_id": outcome.event_id,
        "state": outcome.state.value,
        "booked_ticket": {
            "booking_id": outcome.booking.booking_id,
            "confirmation_code": outcome.booking.confirmation_code,
            "provider": outcome.booking.provider,
            "booked_at": outcome.booking.booked_at.isoformat(),
            "flight": {
                "offer_id": selected_offer.offer_id,
                "airline": selected_offer.airline,
                "origin": selected_offer.origin_airport,
                "destination": selected_offer.destination_airport,
                "departure_time": selected_offer.departure_time.isoformat(),
                "arrival_time": selected_offer.arrival_time.isoformat(),
                "date": selected_offer.departure_time.date().isoformat(),
                "price": selected_offer.price,
                "currency": selected_offer.currency,
            },
            "provider_payload": outcome.booking.raw,
        },
        "summary": outcome.summary,
    }
    print(json.dumps(ticket_json, indent=2))


if __name__ == "__main__":
    main()