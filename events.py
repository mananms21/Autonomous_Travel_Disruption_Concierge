"""
Cross-module event boundary — §1's Consumes/Produces contract.

This module is a stub: it prints/logs what would be published, so tool.py
is runnable and testable standalone without the other 7 hackathon modules
wired up yet. Swap `emit_event` / `emit_notification_event` / `get_member`
for real message-bus/HTTP calls to the notification and itinerary-state
modules once they exist — the call signatures here are the actual contract
those modules need to satisfy.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("hotel_rescheduling.events")


async def get_member(member_id: str) -> dict:
    """FLAGGED STUB — replace with a real member-profile lookup. Shape
    matches what policy.py / coverage_assessment.py read: card_id,
    card_product_code, card_used_for_trip, autonomous_rebooking_enabled."""
    return {
        "id": member_id,
        "card_id": member_id,
        "card_product_code": "PLATINUM",
        "card_used_for_trip": member_id,
        "autonomous_rebooking_enabled": True,
        "lounge_access_type": "PRIORITY_PASS",
    }


async def emit_event(event_type: str, **fields: Any) -> None:
    logger.info(f"[emit_event] {event_type}: {fields}")


class ChangeDiff:
    """Matches the shape referenced in §3a's coverage_check_node example."""
    def __init__(self, domain: str, action: str, reason: str,
                 member_facing_detail: Optional[dict] = None) -> None:
        self.domain = domain
        self.action = action
        self.reason = reason
        self.member_facing_detail = member_facing_detail or {}


async def emit_notification_event(*, event_type: str, member_id: str, itinerary_id: str,
                                   thread_id: str, callback_event_type: str,
                                   requires_response: bool, change_diff: ChangeDiff) -> None:
    """§3a — must be called BEFORE interrupt(), or interrupt() pauses the
    graph with no one ever notified. This module owns registering the
    PendingResponse with the notification module (stubbed here)."""
    logger.info(f"[emit_notification_event] {event_type} member={member_id} "
                f"thread={thread_id} callback={callback_event_type} "
                f"reason={change_diff.reason!r}")
