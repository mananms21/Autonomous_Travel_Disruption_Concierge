"""
Shared data models for the card member interface backend.

Deliberately small — this module has no reasoning of its own (§7), so its
models are just: what a button tap looks like, what an override looks
like, and what gets logged (§6's member_actions).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class MemberActionRequest(BaseModel):
    """§3 — approve/deny body."""
    decision: Literal["approve", "deny"]


class OverrideRequest(BaseModel):
    """§4 — override body. Field names match exactly what the hotel
    module's on_member_override handler reads (preferred_hotel_id,
    guest_info) — this shape was fixed to line up with that consumer
    rather than the two drifting independently. hotel_id/flight_id are
    mutually exclusive depending on domain; the interface doesn't validate
    which one is "correct" beyond shape — that's the owning module's job."""
    domain: Literal["HOTEL", "FLIGHT"]
    hotel_id: Optional[str] = None
    flight_id: Optional[str] = None
    guest_info: dict = Field(default_factory=dict)


class PushTokenRequest(BaseModel):
    """§8c — what lands in member_notification_prefs.push_token."""
    push_token: str


class MemberAction(BaseModel):
    """§6 — member_actions table, in-model form."""
    id: str
    itinerary_id: str
    member_id: str
    action_type: Literal["APPROVE", "DENY", "OVERRIDE"]
    payload: dict
    created_at: datetime = Field(default_factory=datetime.utcnow)
