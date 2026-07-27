"""
§7a.3 — mock search provider. No network, no quota burned. This is the
`mock`/`mock` combination the doc calls out as "the actual judged demo,
fully deterministic and offline."
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import date
from pathlib import Path
from typing import Optional

from ..models import HotelOption
from . import HotelSearchProvider

DEFAULT_DATASET_PATH = Path(__file__).parent / "mock_hotels.json"


class MockHotelSearchProvider(HotelSearchProvider):
    def __init__(self, dataset_path: str | Path = DEFAULT_DATASET_PATH,
                 latency_range: tuple[float, float] = (0.05, 0.2)) -> None:
        self._latency_range = latency_range
        with open(dataset_path) as f:
            self._hotels: list[dict] = json.load(f)

    async def _latency(self) -> None:
        await asyncio.sleep(random.uniform(*self._latency_range))

    async def search(self, city_id: str, check_in: date, check_out: date,
                      constraints: dict) -> list[HotelOption]:
        await self._latency()
        nights = max((check_out - check_in).days, 1)
        candidates = [h for h in self._hotels if h["city_id"] == city_id]
        return [
            HotelOption(
                hotel_id=h["hotel_id"], hotel_name=h["hotel_name"], city_id=city_id,
                check_in=check_in, check_out=check_out,
                nightly_rate=h["nightly_rate"], total_price=h["nightly_rate"] * nights,
                star_rating=h.get("star_rating"), distance_km=h.get("distance_km"),
                brand_match=h.get("brand_match", False),
                cancellable_until=(check_in if h.get("free_cancellation") else None),
                source_provider="MOCK", raw=h,
            )
            for h in candidates
        ]

    async def get_option(self, hotel_id: str, hint_city_id: Optional[str] = None,
                          hint_check_in: Optional[date] = None,
                          hint_check_out: Optional[date] = None) -> Optional[HotelOption]:
        await self._latency()
        h = next((h for h in self._hotels if h["hotel_id"] == hotel_id), None)
        if not h:
            return None
        check_in = hint_check_in or date.today()
        check_out = hint_check_out or date.today()
        nights = max((check_out - check_in).days, 1)
        return HotelOption(
            hotel_id=h["hotel_id"], hotel_name=h["hotel_name"], city_id=h["city_id"],
            check_in=check_in, check_out=check_out,
            nightly_rate=h["nightly_rate"], total_price=h["nightly_rate"] * nights,
            star_rating=h.get("star_rating"), distance_km=h.get("distance_km"),
            brand_match=h.get("brand_match", False),
            cancellable_until=(check_in if h.get("free_cancellation") else None),
            source_provider="MOCK", raw=h,
        )
