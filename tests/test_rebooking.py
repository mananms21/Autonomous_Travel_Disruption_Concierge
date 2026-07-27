from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from travel_concierge.monitoring import FlightLeg
from travel_concierge.rebooking import MockDuffelRebookingProvider, RebookingAgent, RebookingRequest, RebookingState


class RebookingTests(unittest.TestCase):
    def test_auto_rebooking_chooses_highest_scoring_offer(self) -> None:
        now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
        leg = FlightLeg(
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
            event_id="evt-1",
            pnr="PNR1",
            card_member_id="CM1",
            card_tier="PLATINUM",
            disrupted_leg_id=leg.leg_id,
            trigger_type="AUTO_REBOOK",
            rationale="demo",
            confidence=0.95,
            disrupted_leg=leg,
            reference_fare=260.0,
            cabin_class="economy",
        )

        outcome = RebookingAgent(MockDuffelRebookingProvider()).initiate_rebooking(request)
        self.assertEqual(outcome.state, RebookingState.CONFIRMED)
        self.assertIsNotNone(outcome.selected_offer)
        self.assertIsNotNone(outcome.booking)
        self.assertGreaterEqual(len(outcome.ranked_offers), 1)
        self.assertEqual(outcome.handoff_next, ("hotel", "notification"))


if __name__ == "__main__":
    unittest.main()