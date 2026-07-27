"""
Storage abstraction backing §9's SQL schema.

Same demo philosophy as the hotel module's storage.py: an in-memory
implementation of the exact async function signatures the rest of this
package calls, seeded for testing, resetting on process restart. Swapping
to real Postgres later means replacing this one module's internals —
nothing in worker.py / channel_policy.py / app.py needs to change.

FLAGGED ASSUMPTION: resets on restart. Fine for a demo; not fine for
production, where the idempotency and one-open-question guarantees must
survive a restart — that's exactly why §9 puts these in Postgres with a
UNIQUE constraint (idempotency_key) and a partial unique index
(pending_responses(member_id) WHERE resolved = false).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from .models import MemberNotificationPrefs, NotificationRecord, PendingResponse


class IdempotentReplay(Exception):
    """Raised (well — actually just returned as None, see record_notification)
    when a notification with this idempotency_key already exists. Kept as a
    named exception class so callers that DO want to distinguish it can."""


class OpenPendingResponseConflict(Exception):
    """Mirrors what a real Postgres UNIQUE constraint violation on
    pending_responses(member_id) WHERE resolved=false would raise — §3's
    'one open question per member' rule, enforced here instead of silently
    allowing two ambiguous pending responses to coexist."""
    def __init__(self, member_id: str, existing_notification_id: str):
        self.member_id = member_id
        self.existing_notification_id = existing_notification_id
        super().__init__(f"member {member_id!r} already has an open pending response "
                          f"({existing_notification_id!r}) — cannot open a second one")


class InMemoryNotificationStore:
    def __init__(self) -> None:
        self.notifications: dict[str, NotificationRecord] = {}
        self._idempotency_index: dict[str, str] = {}     # idempotency_key -> notification id
        self.pending_responses: dict[str, PendingResponse] = {}   # notification_id -> PendingResponse
        self._open_by_member: dict[str, str] = {}          # member_id -> notification_id (only while unresolved)
        self.member_prefs: dict[str, MemberNotificationPrefs] = {}

    # -- member prefs --
    async def get_member_prefs(self, member_id: str) -> MemberNotificationPrefs:
        return self.member_prefs.setdefault(member_id, MemberNotificationPrefs(member_id=member_id))

    async def upsert_member_prefs(self, prefs: MemberNotificationPrefs) -> None:
        self.member_prefs[prefs.member_id] = prefs

    async def find_member_by_phone(self, phone_number: str) -> Optional[MemberNotificationPrefs]:
        for prefs in self.member_prefs.values():
            if prefs.phone_number == phone_number:
                return prefs
        return None

    async def invalidate_push_token(self, member_id: str) -> None:
        prefs = await self.get_member_prefs(member_id)
        prefs.push_token = None

    # -- notifications / idempotency (§12) --
    async def exists_by_idempotency_key(self, idempotency_key: str) -> bool:
        return idempotency_key in self._idempotency_index

    async def record_notification(self, *, itinerary_id: str, member_id: str, origin_module: str,
                                   event_type: str, channel: str, content: str,
                                   generated_by: str, status: str, requires_response: bool,
                                   idempotency_key: str) -> Optional[NotificationRecord]:
        if idempotency_key in self._idempotency_index:
            return None  # already sent — the unique constraint on idempotency_key catches this
        record = NotificationRecord(
            id=str(uuid.uuid4()), itinerary_id=itinerary_id, member_id=member_id,
            origin_module=origin_module, event_type=event_type, channel=channel, content=content,
            generated_by=generated_by, status=status, requires_response=requires_response,
            idempotency_key=idempotency_key)
        self.notifications[record.id] = record
        self._idempotency_index[idempotency_key] = record.id
        return record

    async def update_notification_status(self, notification_id: str, status: str) -> None:
        if notification_id in self.notifications:
            self.notifications[notification_id].status = status

    # -- pending responses (§3) --
    async def create_pending_response(self, *, notification_id: str, member_id: str, itinerary_id: str,
                                       origin_module: str, origin_thread_id: str,
                                       callback_event_type: str, expires_in_seconds: int) -> PendingResponse:
        existing = self._open_by_member.get(member_id)
        if existing is not None:
            raise OpenPendingResponseConflict(member_id, existing)
        pending = PendingResponse(
            notification_id=notification_id, member_id=member_id, itinerary_id=itinerary_id,
            origin_module=origin_module, origin_thread_id=origin_thread_id,
            callback_event_type=callback_event_type,
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in_seconds))
        self.pending_responses[notification_id] = pending
        self._open_by_member[member_id] = notification_id
        return pending

    async def find_open_pending_response(self, member_id: str) -> Optional[PendingResponse]:
        notification_id = self._open_by_member.get(member_id)
        if notification_id is None:
            return None
        return self.pending_responses.get(notification_id)

    async def find_pending_response_by_itinerary(self, itinerary_id: str) -> Optional[PendingResponse]:
        for pending in self.pending_responses.values():
            if pending.itinerary_id == itinerary_id and not pending.resolved:
                return pending
        return None

    async def mark_pending_response_resolved(self, notification_id: str) -> None:
        pending = self.pending_responses.get(notification_id)
        if pending is None:
            return
        pending.resolved = True
        # free up the member's "one open question" slot
        if self._open_by_member.get(pending.member_id) == notification_id:
            del self._open_by_member[pending.member_id]

    async def find_expired_unresolved_pending_responses(self) -> list[PendingResponse]:
        now = datetime.utcnow()
        return [p for p in self.pending_responses.values() if not p.resolved and p.expires_at < now]


# Module-level singleton — mirrors the hotel module's `db` pattern.
db = InMemoryNotificationStore()
