"""
§7a — provider adapter interfaces + facade.

Search and booking are two separate interfaces, not one, so either side can
be swapped independently (e.g. Scrappa for search, mock for booking) without
tool.py ever changing. HotelProviderFacade is the ONLY object tool.py
imports from this package.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from ..models import BookingResult, CancelResult, GuestInfo, HotelOption


class HotelSearchProvider(ABC):
    """Everything about FINDING a hotel/rate. Owns nothing about payment
    or booking."""

    @abstractmethod
    async def search(self, city_id: str, check_in: date, check_out: date,
                      constraints: dict) -> list[HotelOption]: ...

    @abstractmethod
    async def get_option(self, hotel_id: str, hint_city_id: Optional[str] = None,
                          hint_check_in: Optional[date] = None,
                          hint_check_out: Optional[date] = None) -> Optional[HotelOption]:
        """Re-fetches one specific hotel/rate by this provider's own id —
        what the member-override path (§5.7) calls with `preferred_hotel_id`.
        hint_* args exist because some search APIs can't look up a bare id
        without a city/date-range scope to search within."""
        ...


class HotelBookingProvider(ABC):
    """Everything about COMMITTING a booking. Never searches on its own —
    it's handed a HotelOption (possibly from a different vendor's search)
    and turns that into a confirmed reservation on its own platform."""

    @abstractmethod
    async def book(self, option: HotelOption, guest: GuestInfo,
                    idempotency_key: str) -> BookingResult: ...

    @abstractmethod
    async def cancel(self, provider_booking_id: str, reason: str) -> CancelResult: ...

    @abstractmethod
    async def get_booking_by_idempotency_key(self, idempotency_key: str) -> Optional[BookingResult]:
        """Lets §5.6 recover a booking's true status after a provider
        timeout instead of guessing whether it went through."""
        ...


class HotelProviderFacade:
    """Wraps a search provider + a booking provider behind the one shape
    §5.6 (execute_booking_transaction) and §5.7 (on_member_override) were
    written against — search()/book()/cancel()/get_option()/
    get_booking_by_idempotency_key() — so swapping either backend is a
    config change, never a code change in tool.py."""

    def __init__(self, search_provider: HotelSearchProvider,
                 booking_provider: HotelBookingProvider) -> None:
        self._search = search_provider
        self._booking = booking_provider

    async def search(self, city_id: str, check_in: date, check_out: date,
                      constraints: dict) -> list[HotelOption]:
        return await self._search.search(city_id, check_in, check_out, constraints)

    async def get_option(self, hotel_id: str, hint_city_id: Optional[str] = None,
                          hint_check_in: Optional[date] = None,
                          hint_check_out: Optional[date] = None) -> Optional[HotelOption]:
        return await self._search.get_option(hotel_id, hint_city_id, hint_check_in, hint_check_out)

    async def book(self, option: HotelOption, guest: GuestInfo,
                    idempotency_key: str) -> BookingResult:
        return await self._booking.book(option, guest, idempotency_key)

    async def cancel(self, provider_booking_id: str, reason: str) -> CancelResult:
        return await self._booking.cancel(provider_booking_id, reason)

    async def get_booking_by_idempotency_key(self, idempotency_key: str) -> Optional[BookingResult]:
        return await self._booking.get_booking_by_idempotency_key(idempotency_key)
