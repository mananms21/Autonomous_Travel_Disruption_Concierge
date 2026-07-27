from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from travel_concierge.monitoring import (
    DisruptionType,
    FlightLeg,
    FlightStatusSnapshot,
    MonitoringEngine,
    MonitoringPolicy,
    Severity,
    classify_event,
    evaluate_connection,
    poll_interval_seconds,
)


class MonitoringTests(unittest.TestCase):
    def test_poll_interval_scales_with_time_to_departure(self) -> None:
        now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
        leg_soon = FlightLeg("1", "AI101", now + timedelta(hours=2), now + timedelta(hours=5), "JFK", "LHR")
        leg_mid = FlightLeg("2", "AI102", now + timedelta(hours=8), now + timedelta(hours=12), "JFK", "LHR")
        leg_far = FlightLeg("3", "AI103", now + timedelta(hours=24), now + timedelta(hours=29), "JFK", "LHR")
        leg_very_far = FlightLeg("4", "AI104", now + timedelta(hours=72), now + timedelta(hours=77), "JFK", "LHR")

        self.assertEqual(poll_interval_seconds(leg_soon, now), 60)
        self.assertEqual(poll_interval_seconds(leg_mid, now), 5 * 60)
        self.assertEqual(poll_interval_seconds(leg_far, now), 30 * 60)
        self.assertEqual(poll_interval_seconds(leg_very_far, now), 4 * 60 * 60)

    def test_classifies_cancelled_flights_as_high_severity(self) -> None:
        now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
        leg = FlightLeg("1", "AI101", now + timedelta(hours=2), now + timedelta(hours=5), "JFK", "LHR")
        snapshot = FlightStatusSnapshot(leg_id="1", flight_iata="AI101", observed_at=now, status="cancelled")

        disruption = classify_event(leg, snapshot)
        self.assertIsNotNone(disruption)
        self.assertEqual(disruption.type, DisruptionType.CANCELLED)
        self.assertEqual(disruption.severity, Severity.HIGH)

    def test_evaluates_missed_connection_risk(self) -> None:
        now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
        arriving = FlightLeg("1", "AI101", now + timedelta(hours=1), now + timedelta(hours=4), "JFK", "LHR", arr_terminal="T4")
        departing = FlightLeg("2", "AI202", now + timedelta(hours=4, minutes=20), now + timedelta(hours=7), "LHR", "DXB", dep_terminal="T1", is_international=True)

        disruption = evaluate_connection(arriving, departing, MonitoringPolicy(mct_minutes_default=40))
        self.assertIsNotNone(disruption)
        self.assertEqual(disruption.type, DisruptionType.MISSED_CONNECTION_HIGH_RISK)

    def test_monitoring_engine_uses_provider(self) -> None:
        now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
        leg = FlightLeg("1", "AI101", now + timedelta(hours=2), now + timedelta(hours=5), "JFK", "LHR")

        class FakeProvider:
            def fetch_status(self, leg: FlightLeg) -> FlightStatusSnapshot:
                return FlightStatusSnapshot(leg_id=leg.leg_id, flight_iata=leg.flight_iata, observed_at=now, status="scheduled")

        engine = MonitoringEngine(FakeProvider())
        snapshot, disruption = engine.monitor_leg(leg)
        self.assertEqual(snapshot.flight_iata, "AI101")
        self.assertIsNone(disruption)


if __name__ == "__main__":
    unittest.main()