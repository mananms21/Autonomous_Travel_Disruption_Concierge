"""
§6 — this module's own data slice, deliberately small: just the audit log
of what the member actually clicked. No bookings table, no notifications
table — those stay owned by the modules that actually decide things.
"""
from __future__ import annotations

import uuid
from typing import Optional

from .models import MemberAction


class InMemoryMemberActionStore:
    def __init__(self) -> None:
        self.actions: dict[str, MemberAction] = {}

    async def log_action(self, itinerary_id: str, member_id: str, action_type: str,
                          payload: dict) -> MemberAction:
        action = MemberAction(id=str(uuid.uuid4()), itinerary_id=itinerary_id, member_id=member_id,
                               action_type=action_type, payload=payload)
        self.actions[action.id] = action
        return action

    async def get_actions_for_itinerary(self, itinerary_id: str) -> list[MemberAction]:
        return [a for a in self.actions.values() if a.itinerary_id == itinerary_id]


db = InMemoryMemberActionStore()
