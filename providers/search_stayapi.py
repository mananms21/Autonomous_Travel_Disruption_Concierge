"""
StayAPI (Booking.com-backed) search provider.

IMPORTANT: this is built against the REAL response shape captured from a
live test call, not StayAPI's own docs example — the two don't match. The
docs example flattens fields (`hotel_name`, `review_score`, `min_total_price`,
`currency_code`, `distance_from_center`) but the actual response nests them:

    {
      "hotel_id": "6966158",
      "name": "Riu Plaza Manhattan Times Square",   # NOT "hotel_name"
      "star_rating": 4,                              # can be null
      "price": {"amount": 572.58, "currency": "USD", "display": "US$573"},
      "rating": {"score": 8.9, "review_count": 9308, "display": "Excellent"},
      "distance": "0.6 miles",                       # a formatted STRING, not a number
      "free_cancellation": false,
      "is_sold_out": false,
      ...
    }

Two-step flow per StayAPI's own documented workflow:
  1. GET /v1/booking/destinations/lookup?query=<city> -> dest_id, dest_type
  2. GET /v1/booking/search?dest_id=...&dest_type=...&checkin=...&checkout=...

get_option() re-runs the same search and filters by hotel_id rather than
calling a separate per-hotel endpoint — StayAPI does list a "Hotel Prices"
endpoint separately, which would likely be cheaper/more precise, but its
schema hasn't been verified against a real response yet (same discipline
as everything else in this package: don't code against an unverified
guess). Swap in later once confirmed.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_if_result

from ..models import HotelOption
from . import HotelSearchProvider

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    logger.setLevel(logging.INFO)

BASE_URL = "https://api.stayapi.com/v1/booking"

_MILES_RE = re.compile(r"([\d.]+)\s*mile")
_KM_RE = re.compile(r"([\d.]+)\s*km")
MILES_TO_KM = 1.60934


def _parse_distance_km(distance_str: Optional[str]) -> Optional[float]:
    """StayAPI returns distance as a formatted string like '0.6 miles' or
    possibly 'X km' depending on locale/currency settings — handle both,
    default to None (not 0) when unparseable so rank()'s neutral-score
    fallback applies instead of falsely implying zero distance."""
    if not distance_str:
        return None
    miles_match = _MILES_RE.search(distance_str)
    if miles_match:
        return round(float(miles_match.group(1)) * MILES_TO_KM, 2)
    km_match = _KM_RE.search(distance_str)
    if km_match:
        return round(float(km_match.group(1)), 2)
    return None


class StayAPIHotelSearchProvider(HotelSearchProvider):
    def __init__(self, api_key: str, currency: str = "USD") -> None:
        self._api_key = api_key
        self._currency = currency
        self._client = httpx.AsyncClient(timeout=30.0)
        # dest_id lookups cost a call too — cache per city string, same
        # quota-conscious pattern as the other providers' location caches.
        self._dest_cache: dict[str, tuple[int, str]] = {}

    def _headers(self) -> dict:
        return {"x-api-key": self._api_key, "Accept": "application/json"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
    async def _resolve_destination(self, city_id: str) -> tuple[int, str]:
        if city_id in self._dest_cache:
            return self._dest_cache[city_id]

        resp = await self._client.get(f"{BASE_URL}/destinations/lookup",
                                       params={"query": city_id}, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success") or data.get("dest_id") is None:
            raise ValueError(f"StayAPI destination lookup failed for {city_id!r}: {data.get('message')}")
        resolved = (data["dest_id"], data.get("dest_type", "CITY"))
        self._dest_cache[city_id] = resolved
        return resolved

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=6),
        # Same lesson learned from the Scrappa integration: a 200 OK with
        # zero hotels can be a one-off transient issue, not a genuine
        # "nothing here" result. Retry through it before accepting it.
        retry=(retry_if_exception_type(httpx.HTTPError) | retry_if_result(lambda r: len(r) == 0)),
    )
    async def _raw_search(self, dest_id: int, dest_type: str, check_in: date,
                           check_out: date, constraints: dict) -> list[dict]:
        params = {
            "dest_id": dest_id, "dest_type": dest_type,
            "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
            "adults": constraints.get("occupants", 1), "rooms": 1,
            "currency": self._currency,
        }
        resp = await self._client.get(f"{BASE_URL}/search", params=params, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            logger.warning(f"StayAPI search returned success=false: {data.get('message')}")
            return []

        # Real response wraps hotels in data.hotels (confirmed from a live
        # call); StayAPI's own docs example shows a flat data[] array
        # instead — handle both rather than trusting either alone.
        payload = data.get("data", [])
        if isinstance(payload, dict):
            hotels = payload.get("hotels", payload.get("results", []))
        elif isinstance(payload, list):
            hotels = payload
        else:
            hotels = []

        logger.info(f"StayAPI search for dest_id={dest_id}: {len(hotels)} hotels "
                    f"(HTTP {resp.status_code})")
        return hotels

    async def search(self, city_id: str, check_in: date, check_out: date,
                      constraints: dict) -> list[HotelOption]:
        # Confirmed via a live call: StayAPI's destination-lookup resolves a
        # bare IATA code (e.g. "JFK") directly to dest_type="AIRPORT" with
        # the correct Booking.com dest_id as the first suggestion — no need
        # to build an airport-name query string ourselves. Falls back to
        # city_id when no airport context was given (e.g. a post-arrival
        # stopover stay that isn't tied to a specific airport).
        airport_code = constraints.get("near_airport")
        location_query = airport_code or city_id

        try:
            dest_id, dest_type = await self._resolve_destination(location_query)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"StayAPI destination resolution failed for {location_query!r}: {e}")
            return []

        try:
            hotels = await self._raw_search(dest_id, dest_type, check_in, check_out, constraints)
        except httpx.HTTPError as e:
            logger.warning(f"StayAPI search failed: {e}")
            return []

        results = []
        for entry in hotels:
            option = self._parse_hotel(entry, city_id, check_in, check_out)
            if option is not None:
                results.append(option)
        logger.info(f"StayAPI: {len(hotels)} hotels returned, {len(results)} parsed into usable options"
                    + (f" (dest_type={dest_type}, query={location_query!r})" if airport_code else ""))
        return results

    def _parse_hotel(self, entry: dict, city_id: str, check_in: date,
                      check_out: date) -> Optional[HotelOption]:
        hotel_id = entry.get("hotel_id")
        name = entry.get("name")
        if not hotel_id or not name:
            return None

        if entry.get("is_sold_out"):
            return None  # explicitly sold out — don't offer it as a candidate at all

        price = entry.get("price") or {}
        amount = price.get("amount")
        if amount is None:
            return None  # no usable price — skip rather than fabricate, same rule as every other provider here

        rating = entry.get("rating") or {}
        nights = max((check_out - check_in).days, 1)
        # StayAPI's price.amount is a stay total (confirmed from the real
        # response — it scales with checkin/checkout, not a flat nightly
        # rate), so derive nightly_rate rather than assuming amount IS
        # nightly. HotelOption.nightly_rate is a required field.
        nightly = float(amount) / nights

        # StayAPI's own `distance` field is often an empty string on
        # AIRPORT-type searches (confirmed from a live response) — proximity
        # is already baked into the search itself in that case, there's no
        # separate number to parse. _parse_distance_km safely returns None
        # for an unparseable/empty string, and rank() treats None as neutral
        # rather than penalizing it.
        distance_km = _parse_distance_km(entry.get("distance"))

        return HotelOption(
            hotel_id=str(hotel_id),
            hotel_name=name,
            city_id=city_id,
            check_in=check_in,
            check_out=check_out,
            nightly_rate=nightly,
            total_price=float(amount),
            star_rating=entry.get("star_rating"),
            review_count=rating.get("review_count"),
            cancellable_until=(check_in if entry.get("free_cancellation") else None),
            source_provider="STAYAPI",
            distance_km=distance_km,
            raw=entry,
        )

    async def get_option(self, hotel_id: str, hint_city_id: Optional[str] = None,
                          hint_check_in: Optional[date] = None,
                          hint_check_out: Optional[date] = None) -> Optional[HotelOption]:
        if not (hint_city_id and hint_check_in and hint_check_out):
            return None
        options = await self.search(hint_city_id, hint_check_in, hint_check_out, {})
        return next((o for o in options if o.hotel_id == hotel_id), None)

    async def aclose(self) -> None:
        await self._client.aclose()