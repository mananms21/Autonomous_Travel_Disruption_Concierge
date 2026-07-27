"""
§7a.5 — config picks each side independently. One env var per side, never a
code change in tool.py / ranking.py / policy.py.
"""
from __future__ import annotations

import os

from .providers import HotelProviderFacade

HOTEL_SEARCH_BACKEND = os.getenv("HOTEL_SEARCH_BACKEND", "mock")     # "mock" | "scrappa" | "stayapi"
HOTEL_BOOKING_BACKEND = os.getenv("HOTEL_BOOKING_BACKEND", "mock")   # "mock"


def get_search_provider():
    if HOTEL_SEARCH_BACKEND == "scrappa":
        from .providers.search_scrappa import ScrappaHotelSearchProvider
        return ScrappaHotelSearchProvider(api_key=os.environ["SCRAPPA_API_KEY"])
    if HOTEL_SEARCH_BACKEND == "stayapi":
        from .providers.search_stayapi import StayAPIHotelSearchProvider
        return StayAPIHotelSearchProvider(api_key=os.environ["STAYAPI_API_KEY"])
    from .providers.search_mock import MockHotelSearchProvider
    return MockHotelSearchProvider()


def get_booking_provider():
    # Booking stays mocked for this hackathon build — no live vendor with a
    # sandbox booking endpoint was wired up, matching §7's mocking strategy.
    from .providers.booking_mock import MockHotelBookingProvider
    return MockHotelBookingProvider()


hotel_provider = HotelProviderFacade(get_search_provider(), get_booking_provider())
# ^ the ONLY object tool.py imports from providers — swapping either
# backend is a config change on one env var, never a code change here.