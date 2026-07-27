"""
Supervisor — the one backend Streamlit talks to.

Wraps HotelReschedulingTool (event-driven, async, in-process LangGraph) and
NotificationTool (HTTP client to the notification service) behind plain REST
endpoints. Streamlit can't hold a long-lived asyncio/LangGraph process across
its own script reruns, so this process does — Streamlit is a thin HTTP client
to THIS, exactly like the Streamlit-architecture doc proposed.

ASSUMES the 3-line fix described alongside this file has been applied to
hotel_rescheduling/tool.py (capturing/returning ainvoke's result instead of
discarding it) — without that, this file has no way to know what happened
after triggering a disruption except waiting on a separate event bus.

WHAT'S A REAL STAND-IN, NOT CORE LOGIC: `_status_store` below is an in-memory
dict tracking "what did we last hear back for this itinerary" purely for the
dashboard to poll. The itinerary-state module (Part 7) is the real owner of
a timeline/status endpoint per the architecture doc — this is scoped
deliberately small to get the demo working now, not a replacement for that.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from hotel_booking.tool import HotelReschedulingTool
from notification.tool import NotificationTool

app = FastAPI(title="Travel Concierge Supervisor")

hotel_tool = HotelReschedulingTool()
notification_tool = NotificationTool()

# itinerary_id -> {"state": <last graph state or interrupt payload>, "updated_at": float}
# in-memory only — a restart loses history, fine for a demo, not for production
_status_store: dict[str, dict[str, Any]] = {}


def _record(itinerary_id: str, result: dict) -> None:
    _status_store[itinerary_id] = {"state": result, "updated_at": time.time()}


def _is_awaiting_response(result: dict) -> bool:
    """LangGraph's interrupt() surfaces as a distinct shape in the returned
    state rather than a normal completed state. FLAGGED ASSUMPTION: the exact
    key differs across LangGraph versions (`__interrupt__` in recent
    releases) — verify against your installed version and adjust this check
    if the dashboard shows "completed" for a run that actually paused."""
    return "__interrupt__" in result


class Leg(BaseModel):
    """Matches exactly what delta.py's compute_required_timeline reads —
    departure_time/arrival_time typed as `datetime` (not str) is the whole
    point: Pydantic parses an incoming ISO string into a real tz-aware
    datetime here, so delta.py's `(t2 - t1).total_seconds()` and
    `legs[0]["departure_time"] > original_legs[0]["departure_time"]` don't
    crash on a plain string. The ISO string MUST include a UTC offset
    (e.g. ...+05:30 or ...Z) — a naive datetime will blow up the very next
    comparison against `datetime.now(tz=ZoneInfo("UTC"))` in delta.py."""
    departure_airport: str
    arrival_airport: str
    departure_time: datetime
    arrival_time: datetime


class ItineraryPayload(BaseModel):
    origin_city: str
    origin_tz: str
    passenger_ids: list[str]
    legs: list[Leg]
    original_legs: list[Leg] | None = None
    has_post_arrival_stopover: bool = False
    final_stay: dict | None = None


class DisruptionEvent(BaseModel):
    itinerary_id: str
    event_id: str
    itinerary: ItineraryPayload
    member_id: str


class ResumeEvent(BaseModel):
    thread_id: str
    response: Any


class OverrideEvent(BaseModel):
    itinerary_id: str
    domain: str
    preferred_hotel_id: str | None = None
    preferred_flight_id: str | None = None
    guest_info: dict | None = None


@app.post("/disruption")
async def trigger_disruption(event: DisruptionEvent) -> dict:
    """What Streamlit calls to simulate/replay a flight disruption for a demo
    itinerary. In the real system this is triggered by ItineraryUpdated from
    itinerary-state, not by a person clicking a button — this endpoint exists
    specifically so the dashboard can drive a demo without that module wired up yet."""
    result = await hotel_tool.on_itinerary_updated(event.model_dump())
    _record(event.itinerary_id, result)
    return {"awaiting_response": _is_awaiting_response(result), "state": result}


@app.post("/respond/coverage")
async def respond_coverage(event: ResumeEvent) -> dict:
    result = await hotel_tool.on_coverage_verification_response(event.model_dump())
    itinerary_id = event.thread_id.split(":")[1]   # thread_id shape is "hotel:{itinerary_id}:{event_id}"
    _record(itinerary_id, result)
    return {"awaiting_response": _is_awaiting_response(result), "state": result}


@app.post("/respond/approval")
async def respond_approval(event: ResumeEvent) -> dict:
    result = await hotel_tool.on_approval_response(event.model_dump())
    itinerary_id = event.thread_id.split(":")[1]
    _record(itinerary_id, result)
    return {"awaiting_response": _is_awaiting_response(result), "state": result}


@app.post("/respond/override")
async def respond_override(event: OverrideEvent) -> dict:
    # on_member_override doesn't return the graph state (it's not a resume
    # of a paused run — see §5.7) so there's nothing to capture/return here;
    # its outcome arrives as a normal HotelBookingConfirmed/HotelEscalationRaised
    # event through whatever event bus the real system uses, not through this call
    await hotel_tool.on_member_override(event.model_dump())
    return {"submitted": True}


@app.get("/status/{itinerary_id}")
async def get_status(itinerary_id: str) -> dict:
    if itinerary_id not in _status_store:
        raise HTTPException(404, "No status recorded for this itinerary yet")
    return _status_store[itinerary_id]


@app.get("/health")
async def health() -> dict:
    return {"hotel_tool": True,  # in-process — if the supervisor is up, so is this
             "notification_tool": await notification_tool.health()}


@app.post("/notify/test")
async def send_test_notification(event: dict) -> dict:
    """Manual passthrough for demoing the notification channel directly,
    independent of a real hotel disruption."""
    return await notification_tool.notify(event)