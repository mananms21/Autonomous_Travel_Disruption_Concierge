"""
Scrappa Google Hotels search provider.

Verified against their live docs before writing this (not from training
data): https://scrappa.co/docs/google-hotels-api/google_hotels_search
- GET https://scrappa.co/api/google-hotels/search, auth via `x-api-key` header
- Required: q, check_in_date, check_out_date (YYYY-MM-DD)
- 500 free credits/month, no card, 1 credit/request
- Location resolution: /api/google-hotels/autocomplete?q=... -> property/city tokens

CONFIRMED FROM A REAL CALL (this is no longer a guess): the bulk `/search`
listing call returns property metadata only — name, rating, amenities,
GPS, property_token — and NO pricing (`prices: []`, no top-level
`rate_per_night`/`total_rate`) on every single property, valid date range
or not. This matches the documented behavior of the same underlying scraped
data family elsewhere (e.g. Scrape.do's Google Hotels API docs state
outright: "The listing endpoint returns property-level metadata... but not
per-night pricing. Each property includes a property_token and a
property_details_link... That endpoint returns rate_per_night, total_rate,
price breakdowns"). Scrappa's own response even carries a
`scrappa_property_details_link` field pointing back at this same `/search`
endpoint with `property_token` attached — that's the "detail" call.

So this provider now does two phases:
  1. One bulk listing call -> candidate properties (metadata, no price).
  2. Up to MAX_PRICE_LOOKUPS_PER_SEARCH per-property calls (search endpoint
     + property_token filter, i.e. what get_option() already does) to
     actually pull pricing.
Each phase-2 call is +1 Scrappa credit, so this is capped rather than
enriching every candidate — tune via constraints["max_price_lookups"].
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Optional
import json
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import HotelOption
from . import HotelSearchProvider

logger = logging.getLogger(__name__)
if not logger.handlers:
    # Without this, logger.info()/warning() are silently swallowed unless
    # the calling script has already configured logging (test.py doesn't) —
    # that's exactly why the last run showed zero diagnostic output even
    # though code was actually executing. Configure once, here, so this
    # provider is self-diagnosing regardless of what the caller set up.
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    logger.setLevel(logging.INFO)

BASE_URL = "https://scrappa.co/api/google-hotels"

# Credit-cost cap: each per-property pricing lookup is +1 Scrappa credit on
# top of the 1 credit the bulk listing call already spent. 500 free
# credits/month means this needs to stay modest, not "enrich all 20".
MAX_PRICE_LOOKUPS_PER_SEARCH = 8
_ENRICHMENT_CONCURRENCY = 4


class ScrappaHotelSearchProvider(HotelSearchProvider):
    def __init__(self, api_key: str, currency: str = "USD", gl: str = "us", hl: str = "en") -> None:
        self._api_key = api_key
        self._currency = currency
        self._gl = gl
        self._hl = hl
        self._client = httpx.AsyncClient(timeout=15.0)
        # In-memory cache: location string -> resolved query, to avoid
        # burning credits re-resolving the same city repeatedly, same
        # quota-conscious pattern as the Makcorps mapping cache.
        self._location_cache: dict[str, str] = {}

    async def _headers(self) -> dict:
        return {"x-api-key": self._api_key, "Accept": "application/json"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
    async def _resolve_location(self, city_id: str) -> str:
        """city_id here is whatever this system's `airports`/city table uses
        (see architecture §4) — resolve it to a Google-Hotels-friendly query
        string via autocomplete, cached after the first resolution."""
        if city_id in self._location_cache:
            return self._location_cache[city_id]

        resp = await self._client.get(f"{BASE_URL}/autocomplete",
                                       params={"q": city_id}, headers=await self._headers())
        resp.raise_for_status()
        data = resp.json()
        suggestions = data.get("suggestions", [])
        resolved = suggestions[0]["name"] if suggestions else city_id
        self._location_cache[city_id] = resolved
        return resolved

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
    async def _bulk_listing(self, city_id: str, check_in: date, check_out: date,
                             constraints: dict) -> list[dict]:
        """Phase 1: cheap bulk call, metadata only (see module docstring —
        this confirmed does NOT return pricing on its own)."""
        params = {
            "q": city_id,  # Scrappa accepts city names directly
            "check_in_date": check_in.isoformat(),
            "check_out_date": check_out.isoformat(),
            "currency": self._currency,
            "gl": self._gl,
            "hl": self._hl,
            "adults": constraints.get("occupants", 1),
        }
        if constraints.get("max_price"):
            params["max_price"] = constraints["max_price"]
        if constraints.get("min_star_rating"):
            params["hotel_class"] = constraints["min_star_rating"]

        resp = await self._client.get(f"{BASE_URL}/search", params=params,
                                       headers=await self._headers())
        resp.raise_for_status()
        data = resp.json()

        properties = data.get("properties", [])
        logger.info(f"Scrappa bulk listing for {city_id!r}: {len(properties)} properties "
                    f"(metadata only, no pricing yet — HTTP {resp.status_code})")
        return properties

    async def search(self, city_id: str, check_in: date, check_out: date,
                      constraints: dict) -> list[HotelOption]:
        if check_out <= check_in:
            # Defense-in-depth: Google Hotels rejects this with a 422, and
            # burning an API credit to discover that isn't worth it. Whatever
            # upstream bug produced this (see delta.py's history), never
            # forward an invalid range to the vendor — log loudly and fix
            # up rather than silently eating the request.
            logger.warning(
                f"Rejecting invalid date range before calling Scrappa: "
                f"check_in={check_in} check_out={check_out} (city={city_id!r}). "
                f"Bumping check_out to check_in + 1 day.")
            check_out = check_in + timedelta(days=1)

        try:
            candidates = await self._bulk_listing(city_id, check_in, check_out, constraints)
        except httpx.HTTPError as e:
            logger.warning(f"Scrappa bulk listing failed: {e}")
            return []

        if not candidates:
            return []

        # Phase 2: enrich a capped subset with real pricing. Google Hotels
        # already returns the bulk listing in relevance order, so taking the
        # first N is a reasonable default rather than an arbitrary subset.
        cap = constraints.get("max_price_lookups", MAX_PRICE_LOOKUPS_PER_SEARCH)
        to_enrich = candidates[:cap]
        logger.info(f"Scrappa: enriching {len(to_enrich)} of {len(candidates)} candidates "
                    f"with per-property pricing lookups (cap={cap})")
        sem = asyncio.Semaphore(_ENRICHMENT_CONCURRENCY)

        async def _enrich(entry: dict) -> Optional[HotelOption]:
            token = entry.get("property_token")
            name = entry.get("name", "?")
            if not token:
                logger.info(f"  - {name}: no property_token, skipping")
                return None
            async with sem:
                try:
                    option = await self.get_option(token, city_id, check_in, check_out)
                except Exception as e:
                    logger.warning(f"  - {name}: enrichment call raised {type(e).__name__}: {e}")
                    return None
            logger.info(f"  - {name}: {'priced at $' + str(option.nightly_rate) if option else 'no price returned'}")
            return option

        enriched = await asyncio.gather(*(_enrich(e) for e in to_enrich))
        results = [option for option in enriched if option is not None]
        logger.info(f"Scrappa: {len(to_enrich)} properties looked up for pricing, "
                    f"{len(results)} returned a usable price")
        return results

    def _parse_property(
            self,
            entry: dict,
            city_id: str,
            check_in: date,
            check_out: date,
        ) -> Optional[HotelOption]:

            hotel_id = entry.get("property_token")
            hotel_name = entry.get("name")

            if not hotel_id:
                print("Missing property_token:", hotel_name)
                return None

            if not hotel_name:
                print("Missing hotel name")
                return None

            rate_per_night = entry.get("rate_per_night")
            total_rate = entry.get("total_rate")

            print("\n----------------------------")
            print("Hotel:", hotel_name)
            print("rate_per_night =", rate_per_night)
            print("total_rate      =", total_rate)

            nights = max((check_out - check_in).days, 1)

            nightly = None
            total = None

            if isinstance(rate_per_night, dict):
                nightly = rate_per_night.get("extracted_lowest")

            if isinstance(total_rate, dict):
                total = total_rate.get("extracted_lowest")

            # FALLBACK: top-level rate_per_night/total_rate are absent on
            # some real responses (confirmed against Scrappa's own docs
            # example) even though the property itself has live OTA offers
            # in `prices[]`. Each prices[] entry carries its own nested
            # rate_per_night — take the cheapest one before giving up.
            if nightly is None:
                prices_list = entry.get("prices") or []
                candidate_nightlies = [
                    p["rate_per_night"]["extracted_lowest"]
                    for p in prices_list
                    if isinstance(p, dict)
                    and isinstance(p.get("rate_per_night"), dict)
                    and p["rate_per_night"].get("extracted_lowest") is not None
                ]
                if candidate_nightlies:
                    nightly = min(candidate_nightlies)
                    print("nightly recovered from prices[] fallback =", nightly)

            print("nightly =", nightly)
            print("total   =", total)

            if nightly is None and total is not None:
                nightly = total / nights

            if total is None and nightly is not None:
                total = nightly * nights

            if nightly is None and total is None:
                print("Rejected because no price")
                return None

            print("Accepted!")

            return HotelOption(
                hotel_id=hotel_id,
                hotel_name=hotel_name,
                city_id=city_id,
                check_in=check_in,
                check_out=check_out,
                nightly_rate=float(nightly),
                total_price=float(total),
                star_rating=entry.get("extracted_hotel_class") or entry.get("overall_rating"),
                review_count=entry.get("reviews"),
                cancellable_until=(check_in if entry.get("free_cancellation") else None),
                source_provider="SCRAPPA",
                distance_km=None,
                raw=entry,
        )
    async def get_option(self, hotel_id: str, hint_city_id: Optional[str] = None,
                          hint_check_in: Optional[date] = None,
                          hint_check_out: Optional[date] = None) -> Optional[HotelOption]:
        # Scrappa's search endpoint accepts property_token as a filter, but
        # still needs a location + date scope — same constraint the Amadeus
        # example in the architecture doc flags for its own get_option.
        if not (hint_city_id and hint_check_in and hint_check_out):
            return None
        try:
            location_query = hint_city_id
            resp = await self._client.get(
                f"{BASE_URL}/search",
                params={"q": location_query, "check_in_date": hint_check_in.isoformat(),
                        "check_out_date": hint_check_out.isoformat(),
                        "property_token": hotel_id, "currency": self._currency,
                        "gl": self._gl, "hl": self._hl},
                headers=await self._headers())
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.warning(f"Scrappa get_option failed for {hotel_id!r}: {e}")
            return None

        for entry in data.get("properties", []):
            if entry.get("property_token") == hotel_id:
                return self._parse_property(entry, hint_city_id, hint_check_in, hint_check_out)
        return None

    async def aclose(self) -> None:
        await self._client.aclose()