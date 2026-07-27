"""
Card member interface backend — deliberately thin (§1, §7). No LLM, no
policy logic, no booking logic. Three real jobs:
  1. Resolve a PendingResponse when the member taps approve/deny (§3) —
     reuses the notification module's OWN registry, not a duplicate one.
  2. Capture an override and hand it to the owning module, unvalidated
     beyond shape (§4).
  3. Register a push token (§8c) and proxy a timeline read (§5).

Assumes this package sits next to notification/ (as delivered) so
`from notification...` resolves — swap for an HTTP client call to
a real separately-deployed notification service if these end up as two
actual processes rather than one monorepo.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# make the sibling notification package importable regardless of
# where this app happens to be launched from
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fastapi import FastAPI, HTTPException

from notification.events import emit_event
from notification.models import MemberNotificationPrefs
from notification.storage import db as notification_db
from notification.worker import retry_queued_for_member

from .models import MemberActionRequest, OverrideRequest, PushTokenRequest
from .storage import db as member_action_db

app = FastAPI(title="Card Member Interface Backend")


@app.post("/itinerary/{itinerary_id}/respond")
async def member_response(itinerary_id: str, action: MemberActionRequest):
    """§3 — a button tap is just another way a PendingResponse gets
    resolved. Same table, same resolution path an SMS reply takes."""
    pending = await notification_db.find_pending_response_by_itinerary(itinerary_id)
    if not pending or pending.expires_at < datetime.utcnow():
        raise HTTPException(404, "No response currently expected")

    await notification_db.mark_pending_response_resolved(pending.notification_id)
    await emit_event(pending.callback_event_type, thread_id=pending.origin_thread_id,
                      response="yes" if action.decision == "approve" else "no",
                      origin_module=pending.origin_module)
    await retry_queued_for_member(pending.member_id)  # §3's queued-behind-pending retry hook

    await member_action_db.log_action(
        itinerary_id=itinerary_id, member_id=pending.member_id,
        action_type="APPROVE" if action.decision == "approve" else "DENY",
        payload={"decision": action.decision})

    return {"status": "resolved", "decision": action.decision}


DOMAIN_TO_ORIGIN_MODULE = {"HOTEL": "HOTEL", "FLIGHT": "FLIGHT_REBOOKING"}


@app.post("/itinerary/{itinerary_id}/override")
async def member_override(itinerary_id: str, override: OverrideRequest, member_id: str):
    """§4 — genuinely new input, not a reply to a prompt. This module's
    job stops at capturing and validating SHAPE; it does not decide
    whether the override is allowed — that's the owning module's job,
    which re-runs its own policy/budget check on receipt."""
    await emit_event("MemberOverrideRequested",
                      origin_module=DOMAIN_TO_ORIGIN_MODULE[override.domain],
                      itinerary_id=itinerary_id,
                      domain=override.domain,
                      preferred_hotel_id=override.hotel_id,
                      preferred_flight_id=override.flight_id,
                      guest_info=override.guest_info)

    await member_action_db.log_action(
        itinerary_id=itinerary_id, member_id=member_id, action_type="OVERRIDE",
        payload=override.model_dump())

    return {"status": "override_submitted"}


@app.post("/member/{member_id}/push-token")
async def register_push_token(member_id: str, body: PushTokenRequest):
    """§8c — lands directly in the notification module's
    member_notification_prefs.push_token, since that's the one dependency
    its push channel actually needs from this app."""
    prefs = await notification_db.get_member_prefs(member_id)
    prefs.push_token = body.push_token
    await notification_db.upsert_member_prefs(prefs)
    return {"status": "registered"}


@app.get("/itinerary/{itinerary_id}/timeline")
async def get_timeline(itinerary_id: str):
    """§5 — proxies to the itinerary state module's timeline endpoint.
    FLAGGED STUB: itinerary state management isn't built yet in this
    workspace, so this falls back to this module's own member_actions log
    (approve/deny/override only — NOT the full cross-module timeline).
    Replace the body of this function with a real call to
    itinerary_state_service.get_timeline(itinerary_id) the moment that
    module exists; don't let this stub quietly become the permanent
    answer, since it can only ever show what the MEMBER did, not the
    full disruption history other modules also contributed to."""
    actions = await member_action_db.get_actions_for_itinerary(itinerary_id)
    return {
        "source": "STUB_MEMBER_ACTIONS_ONLY",
        "note": "itinerary state management module not yet available — this is member "
                "actions only, not the full cross-module timeline the real endpoint would return",
        "events": [a.model_dump() for a in actions],
    }
