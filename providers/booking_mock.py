"""
§7a.3 — mock booking provider. Fully mocked, zero cost, in-memory ledger.
Deliberately can fail (failure_rate) so the retry/escalation paths in
tool.py have something real to trigger against — a booking provider that
never fails can't exercise the book-then-cancel-ordering test or the
idempotency-replay test from §9.
"""
from __future__ import annotations

import asyncio
import random
import uuid
from typing import Optional

from ..models import BookingResult, CancelResult, GuestInfo, HotelOption
from . import HotelBookingProvider


class MockHotelBookingProvider(HotelBookingProvider):
    def __init__(self, latency_range: tuple[float, float] = (0.05, 0.2),
                 failure_rate: float = 0.05) -> None:
        self._latency_range = latency_range
        self._failure_rate = failure_rate
        self._bookings: dict[str, BookingResult] = {}   # idempotency_key -> result
        self._by_id: dict[str, dict] = {}                # provider_booking_id -> record

    async def _latency(self) -> None:
        await asyncio.sleep(random.uniform(*self._latency_range))

    async def book(self, option: HotelOption, guest: GuestInfo,
                    idempotency_key: str) -> BookingResult:
        if idempotency_key in self._bookings:            # idempotency first, always
            return self._bookings[idempotency_key]

        await self._latency()
        if random.random() < self._failure_rate:
            result = BookingResult(success=False, error="MOCK_NO_AVAILABILITY")
            self._bookings[idempotency_key] = result
            return result

        provider_booking_id = f"MOCK-{uuid.uuid4().hex[:10].upper()}"
        result = BookingResult(success=True, provider_booking_id=provider_booking_id,
                                actual_price=option.total_price)
        self._bookings[idempotency_key] = result
        self._by_id[provider_booking_id] = {"status": "CONFIRMED", "option": option.model_dump(),
                                             "guest": guest.model_dump()}
        return result

    async def cancel(self, provider_booking_id: str, reason: str) -> CancelResult:
        await self._latency()
        record = self._by_id.get(provider_booking_id)
        if not record:
            return CancelResult(success=False, error="MOCK_BOOKING_NOT_FOUND")
        if random.random() < self._failure_rate:
            return CancelResult(success=False, error="MOCK_CANCEL_FAILED")
        record["status"] = "CANCELLED"
        return CancelResult(success=True)

    async def get_booking_by_idempotency_key(self, idempotency_key: str) -> Optional[BookingResult]:
        return self._bookings.get(idempotency_key)
