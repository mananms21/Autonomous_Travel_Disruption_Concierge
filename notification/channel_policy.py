"""
§4 — channel selection, generalized across event types. Also owns the
actual per-channel dispatch (send()) since that's tightly coupled to the
policy table right next to it.
"""
from __future__ import annotations

import logging

from .config import CARD_BRAND, get_send_email, get_send_push, get_send_sms, get_send_sms_raw
from .models import MemberNotificationPrefs, NotificationRequest

logger = logging.getLogger("notification.channel_policy")

CHANNEL_POLICY: dict[str, dict] = {
    "FlightDisruptionDetected": {"primary": "push", "fallback": ["sms"], "urgency": "high"},
    "FlightRebooked":            {"primary": "push", "fallback": ["sms"], "urgency": "normal"},
    "FlightRebookingFailed":     {"primary": "push", "fallback": ["sms"], "urgency": "high"},
    "HotelBookingConfirmed":     {"primary": "push", "fallback": ["sms"], "urgency": "normal"},
    "HotelEscalationRaised":     {"primary": "push", "fallback": ["sms", "email"], "urgency": "high"},
    "ApprovalNeeded":            {"primary": "push", "fallback": ["sms"], "urgency": "high",
                                   "requires_response": True},
    "CoverageVerificationRequested": {"primary": "push", "fallback": ["sms"], "urgency": "high",
                                       "requires_response": True},
    "EscalationUnresolved":      {"primary": "sms", "fallback": ["push", "email", "emergency_contact"],
                                   "urgency": "critical"},
    "TripDisruptionResolved":    {"primary": "email", "fallback": [], "urgency": "low"},
}

_DEFAULT_POLICY = {"primary": "sms", "fallback": [], "urgency": "high"}

# emergency_contact only ever appears as the LAST fallback on a critical
# event — never select it as anything but that.
_EMERGENCY_TIER = "emergency_contact"


def get_policy(event_type: str) -> dict:
    policy = CHANNEL_POLICY.get(event_type)
    if policy is None:
        # a producing module added a new event type without registering a policy —
        # log it loudly and fail safe to SMS rather than crash the worker
        logger.error(f"No CHANNEL_POLICY entry for event_type={event_type!r}, defaulting to sms")
        policy = _DEFAULT_POLICY
    return policy


async def select_channels(event_type: str, prefs: MemberNotificationPrefs) -> list[str]:
    policy = get_policy(event_type)
    primary = policy["primary"]
    if not getattr(prefs, primary, True):
        return [c for c in policy["fallback"] if c == _EMERGENCY_TIER or getattr(prefs, c, True)]
    return [primary]


def _label_for(event_type: str) -> str:
    return f"{CARD_BRAND} Travel Concierge: {event_type}"


async def _dispatch(channel: str, request: NotificationRequest, content: str,
                     prefs: MemberNotificationPrefs) -> bool:
    if channel == "push":
        return await get_send_push()(prefs, _label_for(request.event_type), content,
                                      {"event_type": request.event_type, "itinerary_id": request.itinerary_id})
    if channel == "sms":
        return await get_send_sms()(prefs, content)
    if channel == "email":
        return await get_send_email()(prefs, _label_for(request.event_type), f"<p>{content}</p>")
    if channel == _EMERGENCY_TIER:
        return await send_emergency_contact_alert(prefs, request)
    logger.error(f"Unknown channel {channel!r} in dispatch")
    return False


async def send_emergency_contact_alert(prefs: MemberNotificationPrefs, request: NotificationRequest) -> bool:
    """§13a — the last-resort tier. Two non-negotiable rules: consent gate
    first, and no trip details in the message — the emergency contact
    learns THAT something's wrong, never WHAT."""
    if not prefs.emergency_contact_phone or not prefs.emergency_contact_consent:
        return False  # no contact on file, or they never consented — don't invent one
    member_name = prefs.emergency_contact_name or "this member"
    generic_message = (
        f"This is an automated alert from {CARD_BRAND} Travel Concierge. "
        f"We've been unable to reach {member_name} about a travel disruption "
        f"and wanted their emergency contact to know. Please try contacting them directly."
    )
    return await get_send_sms_raw()(prefs.emergency_contact_phone, generic_message)


async def send_with_fallback(request: NotificationRequest, content: str,
                              prefs: MemberNotificationPrefs) -> tuple[bool, str]:
    """Returns (delivered, channel_actually_used)."""
    policy = get_policy(request.event_type)
    channels = await select_channels(request.event_type, prefs)
    if not channels:
        return False, ""
    first = channels[0]
    if await _dispatch(first, request, content, prefs):
        return True, first
    if policy["urgency"] in ("high", "critical"):
        for fallback in policy["fallback"]:
            if await _dispatch(fallback, request, content, prefs):
                return True, fallback
    return False, first
