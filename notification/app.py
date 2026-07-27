"""
FastAPI app tying the notification module together:
- POST /notify           — any module enqueues a NotificationRequest
- POST /webhooks/twilio/sms — the ONE inbound SMS webhook in the whole
  system (§2 of the architecture doc: this module owns all member-facing
  communication, including replies)
- WS   /ws/itinerary/{member_id} — §8's real-time channel
- POST /itinerary/{itinerary_id}/respond — what the card member interface
  calls for an approve/deny button tap; resolves the exact same
  pending_responses row an SMS reply would

For the demo/single-process build, the worker runs as a background asyncio
task fed by an in-process queue (see worker.py's docstring) rather than a
separate process + Redis — swap when moving beyond a hackathon deployment.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket

from .events import emit_event
from .models import NotificationRequest
from .realtime import register_connection
from .reply import handle_sms_reply
from .storage import db
from .worker import expire_stale_pending_responses, notification_worker, retry_queued_for_member

logger = logging.getLogger("notification.app")

_notification_queue: asyncio.Queue = asyncio.Queue()
_EXPIRY_CHECK_INTERVAL_SECONDS = 60


async def _expiry_loop() -> None:
    while True:
        await asyncio.sleep(_EXPIRY_CHECK_INTERVAL_SECONDS)
        try:
            await expire_stale_pending_responses()
        except Exception:
            logger.exception("expire_stale_pending_responses failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(notification_worker(_notification_queue))
    expiry_task = asyncio.create_task(_expiry_loop())
    yield
    worker_task.cancel()
    expiry_task.cancel()


app = FastAPI(title="Notification Service", lifespan=lifespan)


@app.post("/notify")
async def notify(request: NotificationRequest):
    await _notification_queue.put(request)
    return {"status": "queued", "event_id": request.event_id}


@app.post("/webhooks/twilio/sms")
async def twilio_sms_webhook(request: Request):
    form = await request.form()
    await handle_sms_reply(form["From"], form["Body"])
    return Response(content="<Response></Response>", media_type="application/xml")


@app.websocket("/ws/itinerary/{member_id}")
async def itinerary_status_socket(websocket: WebSocket, member_id: str):
    await register_connection(websocket, member_id)


class RespondBody:
    decision: str  # 'approve' | 'deny'


@app.post("/itinerary/{itinerary_id}/respond")
async def member_response(itinerary_id: str, body: dict):
    """Same resolution path an SMS reply takes — a button tap is just
    another way a PendingResponse gets resolved (see the card member
    interface architecture doc, §3)."""
    pending = await db.find_pending_response_by_itinerary(itinerary_id)
    if not pending or pending.expires_at < datetime.utcnow():
        raise HTTPException(404, "No response currently expected for this itinerary")
    decision = body.get("decision")
    if decision not in ("approve", "deny"):
        raise HTTPException(400, "decision must be 'approve' or 'deny'")
    await db.mark_pending_response_resolved(pending.notification_id)
    await emit_event(pending.callback_event_type, thread_id=pending.origin_thread_id,
                      response="yes" if decision == "approve" else "no",
                      origin_module=pending.origin_module)
    await retry_queued_for_member(pending.member_id)
    return {"status": "resolved", "decision": decision}
