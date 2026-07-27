"""
Airport reference data — real OpenFlights dataset (5,515 airports with valid
IATA codes), not a hand-seeded sample. Was originally a 5-airport stub;
replaced once it became clear the product needs to work for any airport,
not just a demo handful.

Source: https://github.com/jpatokal/openflights (ODbL-licensed), the
same dataset multiple other flight/hotel tools in this space are built on.
Loaded once at import time from the bundled data/airports_data.json.

Note on how airport-proximity hotel search actually works in this
package: StayAPI's own destination-lookup endpoint resolves a bare IATA
code (e.g. "JFK") directly to dest_type="AIRPORT" with the correct
Booking.com dest_id — confirmed against a live call, not assumed. That
means search_stayapi.py doesn't need this module's coordinates to build
an airport-biased query; it just passes the IATA code straight through.
This module's coordinates remain useful for anything that needs real
geo-distance (e.g. scoring a Scrappa/mock result, or a future provider
that isn't airport-aware the way StayAPI is), and get_airport_info()
still backs delta.py's timezone-based overnight-gap calculation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import NamedTuple, Optional

_DATA_PATH = Path(__file__).parent / "data" / "airports_data.json"


class AirportInfo(NamedTuple):
    city_id: str
    city_name: str
    timezone: str
    full_name: str
    latitude: float
    longitude: float


def _load() -> dict[str, AirportInfo]:
    with open(_DATA_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    result = {}
    for iata, rec in raw.items():
        # city_id mirrors city name here (no separate internal city-code
        # system in this dataset) — good enough as a dict key/display value;
        # swap for a real internal city-id scheme if one exists elsewhere
        # in the broader system.
        result[iata] = AirportInfo(
            city_id=rec["city"], city_name=rec["city"], timezone=rec["tz"],
            full_name=rec["name"], latitude=rec["lat"], longitude=rec["lon"])
    return result


_AIRPORTS: dict[str, AirportInfo] = _load()


def get_airport_info(iata_code: str) -> tuple[str, str]:
    """Returns (city_id, timezone) — kept for backward compat with existing
    callers (delta.py's overnight-gap calc only needs these two)."""
    info = _AIRPORTS.get(iata_code)
    if not info:
        raise KeyError(f"Airport {iata_code!r} not found in the OpenFlights dataset "
                        f"(it covers {len(_AIRPORTS)} airports with IATA codes — "
                        f"this one may use ICAO-only, or be a very small/private field)")
    return info.city_id, info.timezone


def get_airport_geo(iata_code: str) -> Optional[AirportInfo]:
    """Full record including coordinates. Returns None (not a raise) since
    geo-proximity scoring is an enhancement, not something that should
    hard-fail the whole pipeline the way a missing timezone would."""
    return _AIRPORTS.get(iata_code)


def hotel_search_query(iata_code: str) -> Optional[str]:
    """Query string biased toward the airport itself. In practice
    search_stayapi.py doesn't need this — StayAPI resolves a bare IATA
    code to dest_type=AIRPORT directly — but kept for providers that need
    a free-text query rather than a code (e.g. Scrappa/Google Hotels)."""
    info = _AIRPORTS.get(iata_code)
    if not info:
        return None
    return f"{info.full_name}, {info.city_name}"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    r = 6371.0088  # mean Earth radius, km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return round(r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)