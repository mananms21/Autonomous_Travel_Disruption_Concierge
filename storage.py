"""
Storage abstraction backing the tables defined in the architecture doc's §4.

The doc gives the SQL schema (hotel_bookings, hotel_action_log,
hotel_policy_config, claims_ledger, airline_commitments, airports) and calls
into a bare `db.*` namespace throughout §5, but doesn't hand over connection
code — that's genuinely infra, not module logic. Per the doc's own demo
philosophy ("mock/mock is the actual judged demo, fully deterministic and
offline"), this ships an in-memory implementation of the exact same async
function signatures the doc's §5 code calls, seeded with the doc's tier
config. Swapping to real Postgres later means replacing this one module's
internals, not touching policy.py / ranking.py / tool.py.

FLAGGED ASSUMPTION: this in-memory store resets on process restart. Fine for
a demo; not fine for production, where idempotency and claims-ledger
correctness must survive a restart — that's exactly why the doc puts these
in Postgres with a UNIQUE constraint on idempotency_key.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from .models import BookingResult


@dataclass
class CardTier:
    product_code: str
    delay_threshold_hours: int
    per_trip_cap_usd: float
    max_claims_per_12mo: int


@dataclass
class AirlineCommitment:
    carrier_code: str
    commits_hotel: bool
    commits_ground_transport: bool = False
    commits_meals: bool = False
    source: str = "DOT dashboard"
    last_verified: Optional[date] = None


@dataclass
class HotelBookingRecord:
    id: str
    itinerary_id: str
    provider_booking_id: Optional[str]
    hotel_name: Optional[str]
    city_id: str
    check_in: date
    check_out: date
    nightly_rate: Optional[float]
    occupants: int
    status: str
    idempotency_key: Optional[str]
    triggered_by_event_id: Optional[str]
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


# Seeded straight from the doc's §4 example product codes.
_DEFAULT_TIERS = {
    "PLATINUM": CardTier("PLATINUM", delay_threshold_hours=3, per_trip_cap_usd=500, max_claims_per_12mo=6),
    "GOLD": CardTier("GOLD", delay_threshold_hours=4, per_trip_cap_usd=300, max_claims_per_12mo=4),
    "GREEN": CardTier("GREEN", delay_threshold_hours=6, per_trip_cap_usd=150, max_claims_per_12mo=2),
    "BIZ_PLATINUM": CardTier("BIZ_PLATINUM", delay_threshold_hours=3, per_trip_cap_usd=600, max_claims_per_12mo=8),
}

# Seeded from a handful of DOT-dashboard-style entries for demo purposes.
_DEFAULT_COMMITMENTS = {
    "AA": AirlineCommitment("AA", commits_hotel=True, last_verified=date(2026, 1, 1)),
    "DL": AirlineCommitment("DL", commits_hotel=True, last_verified=date(2026, 1, 1)),
    "UA": AirlineCommitment("UA", commits_hotel=False, last_verified=date(2026, 1, 1)),
}


class InMemoryStore:
    def __init__(self) -> None:
        self.card_tiers: dict[str, CardTier] = dict(_DEFAULT_TIERS)
        self.airline_commitments: dict[str, AirlineCommitment] = dict(_DEFAULT_COMMITMENTS)
        self.hotel_bookings: dict[str, HotelBookingRecord] = {}
        self.hotel_action_log: list[dict] = []
        self.claims_ledger: list[dict] = []
        self._idempotency_index: dict[str, str] = {}  # idempotency_key -> booking id

    # -- policy-config reads --
    async def get_card_tier(self, product_code: str) -> CardTier:
        return self.card_tiers.get(product_code, _DEFAULT_TIERS["GREEN"])

    async def get_airline_commitment(self, carrier_code: str) -> Optional[AirlineCommitment]:
        return self.airline_commitments.get(carrier_code)

    async def count_claims_this_year(self, card_id: str) -> int:
        cutoff = datetime.utcnow() - timedelta(days=365)
        return sum(1 for c in self.claims_ledger
                   if c["card_id"] == card_id and c["claimed_at"] >= cutoff)

    async def record_claim(self, card_id: str, itinerary_id: str, amount_usd: float) -> None:
        self.claims_ledger.append({
            "id": str(uuid.uuid4()), "card_id": card_id, "itinerary_id": itinerary_id,
            "claimed_at": datetime.utcnow(), "amount_usd": amount_usd,
        })

    # -- hotel_bookings --
    async def insert_hotel_booking(self, *, status: str, idempotency_key: Optional[str],
                                    **fields) -> str:
        if idempotency_key and idempotency_key in self._idempotency_index:
            return self._idempotency_index[idempotency_key]
        booking_id = str(uuid.uuid4())
        record = HotelBookingRecord(
            id=booking_id, status=status, idempotency_key=idempotency_key,
            itinerary_id=fields.get("itinerary_id", fields.get("city", "")),
            provider_booking_id=None,
            hotel_name=fields.get("hotel_name"),
            city_id=fields.get("city", fields.get("city_id", "")),
            check_in=fields.get("check_in", date.today()),
            check_out=fields.get("check_out", date.today()),
            nightly_rate=fields.get("nightly_rate"),
            occupants=fields.get("occupants", 1),
            triggered_by_event_id=fields.get("event_id"),
        )
        self.hotel_bookings[booking_id] = record
        if idempotency_key:
            self._idempotency_index[idempotency_key] = booking_id
        return booking_id

    async def update_status(self, booking_id: str, status: str) -> None:
        if booking_id in self.hotel_bookings:
            self.hotel_bookings[booking_id].status = status
            self.hotel_bookings[booking_id].updated_at = datetime.utcnow()

    async def update_hotel_booking(self, booking_id: str, *, status: str,
                                    provider_booking_id: Optional[str] = None) -> None:
        if booking_id in self.hotel_bookings:
            rec = self.hotel_bookings[booking_id]
            rec.status = status
            if provider_booking_id:
                rec.provider_booking_id = provider_booking_id
            rec.updated_at = datetime.utcnow()

    async def get_hotel_booking(self, itinerary_id: str, status: str) -> Optional[HotelBookingRecord]:
        matches = [b for b in self.hotel_bookings.values()
                   if b.itinerary_id == itinerary_id and b.status == status]
        return matches[-1] if matches else None

    async def log_action(self, *, itinerary_id: str, delta_action: str, decision: str,
                          policy_check_result: Optional[dict] = None,
                          provider_response: Optional[dict] = None) -> None:
        self.hotel_action_log.append({
            "id": str(uuid.uuid4()), "itinerary_id": itinerary_id,
            "delta_action": delta_action, "decision": decision,
            "policy_check_result": policy_check_result, "provider_response": provider_response,
            "created_at": datetime.utcnow(),
        })


# Module-level singleton — mirrors how the doc's §5 code calls a bare `db.*`
# namespace rather than passing a connection everywhere.
db = InMemoryStore()
