"""
§5.1 (required timeline), §5.2 (delta diff, with the city-change link fix),
§5.3 (lounge alternative). Pure functions where the doc says pure functions —
compute_hotel_delta is highest-value-per-line per §9's testing priorities.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .models import Stay


def local_date(dt: datetime, tz: str) -> date:
    return dt.astimezone(ZoneInfo(tz)).date()


def hours_between(t1: datetime, t2: datetime) -> float:
    return abs((t2 - t1).total_seconds()) / 3600.0


def is_overnight_gap(t1: datetime, t2: datetime, tz: str, threshold_hours: int = 6) -> bool:
    """DST-safe by construction — compares local dates via zoneinfo, never
    raw hour-of-day arithmetic. See §9's DST-boundary testing priority."""
    if hours_between(t1, t2) < threshold_hours:
        return False
    return local_date(t1, tz) != local_date(t2, tz)


def overlaps(a: Stay, b: Stay) -> bool:
    return a.city == b.city and a.check_in < b.check_out and b.check_in < a.check_out


def dates_overlap(add_entry: dict, cancel_dates: tuple[date, date]) -> bool:
    add_in, add_out = add_entry.get("check_in"), add_entry.get("check_out")
    cancel_in, cancel_out = cancel_dates
    if not (add_in and add_out):
        return False
    return add_in < cancel_out and cancel_in < add_out


def compute_hotel_delta(required: list[Stay], booked: list[Stay]) -> list[dict]:
    delta: list[dict] = []
    matched_ids: set[str] = set()

    for req in required:
        candidates = [b for b in booked if b.city == req.city and overlaps(b, req)]
        if len(candidates) > 1:
            delta.append({"action": "ESCALATE_CONFLICT", "city": req.city,
                          "booking_ids": [c.booking_id for c in candidates]})
            continue
        if not candidates:
            delta.append({"action": "ADD_NIGHT", **req.model_dump()})
        else:
            match = candidates[0]
            matched_ids.add(match.booking_id)
            if match.check_in != req.check_in or match.check_out != req.check_out:
                delta.append({"action": "SHIFT_DATES", "booking_id": match.booking_id,
                              **req.model_dump()})

    for b in booked:
        if b.booking_id not in matched_ids and not any(overlaps(b, r) for r in required):
            delta.append({"action": "CANCEL_NIGHTS", "booking_id": b.booking_id,
                          "original_dates": (b.check_in, b.check_out)})

    return link_city_changes(delta)


def link_city_changes(delta: list[dict]) -> list[dict]:
    adds = [d for d in delta if d["action"] == "ADD_NIGHT"]
    cancels = [d for d in delta if d["action"] == "CANCEL_NIGHTS"]
    for add in adds:
        match = next((c for c in cancels if dates_overlap(add, c["original_dates"])), None)
        if match:
            add["replaces_booking_id"] = match["booking_id"]
            delta.remove(match)
    if not delta:
        from .storage import db
        # explicit NO_ACTION row rather than silently returning nothing —
        # per §4's hotel_action_log comment "includes NO_ACTION, logged explicitly"
        import asyncio
        if asyncio.get_event_loop().is_running():
            asyncio.create_task(db.log_action(itinerary_id="", delta_action="NONE", decision="NO_ACTION"))
    return delta


async def compute_required_timeline(itinerary: dict, member: dict) -> list[Stay]:
    """§5.1. `itinerary` is the plain-dict shape carried in HotelState."""
    from .airports import get_airport_info  # local import avoids a cycle with delta<->airports

    required: list[Stay] = []
    legs = itinerary["legs"]
    original_legs = itinerary.get("original_legs", legs)
    passenger_ids = itinerary.get("passenger_ids", [member.get("id")])

    now = datetime.now(tz=ZoneInfo("UTC"))
    origin_tz = itinerary.get("origin_tz", "UTC")
    if legs[0]["departure_time"] > original_legs[0]["departure_time"]:
        if is_overnight_gap(now, legs[0]["departure_time"], origin_tz, threshold_hours=6):
            required.append(Stay(
                city=itinerary["origin_city"], check_in=local_date(now, origin_tz),
                check_out=local_date(legs[0]["departure_time"], origin_tz),
                occupants=len(passenger_ids)))

    for i in range(len(legs) - 1):
        arrival = legs[i]["arrival_time"]
        arrival_city, tz = get_airport_info(legs[i]["arrival_airport"])
        next_departure = legs[i + 1]["departure_time"]

        if is_overnight_gap(arrival, next_departure, tz, threshold_hours=6):
            required.append(Stay(
                city=arrival_city, check_in=local_date(arrival, tz),
                check_out=local_date(next_departure, tz), occupants=len(passenger_ids)))

    if itinerary.get("has_post_arrival_stopover") and itinerary.get("final_stay"):
        required.append(Stay(**itinerary["final_stay"]))

    return required