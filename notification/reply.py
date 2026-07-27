"""
§3 — the routing-back problem, and §6 — two-way SMS reply classification.

classify_reply is the one open-ended LLM call in this whole module —
everything else is deterministic routing or fact-constrained rephrasing.
The "unclear" branch matters: this output can feed directly into a real
booking/approval decision downstream, so an ambiguous reply must never get
silently defaulted to yes or no.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from .config import LLM_BACKEND, get_send_sms
from .events import emit_event
from .models import NotificationRequest
from .storage import db

logger = logging.getLogger("notification.reply")

_CLEAR_YES = {"yes", "y", "yeah", "yep", "sure", "confirm", "confirmed", "ok", "okay"}
_CLEAR_NO = {"no", "n", "nope", "nah", "cancel", "deny", "decline"}


async def _llm_classify(text: str) -> str:
    if LLM_BACKEND == "groq":
        from groq import AsyncGroq

        client = AsyncGroq(
            api_key=os.environ["GROQ_API_KEY"]
        )

        prompt = (
            "Classify the following traveller reply.\n\n"
            "Reply with EXACTLY one word:\n"
            "yes\n"
            "no\n"
            "unclear\n\n"
            f'Traveller reply: "{text}"'
        )

        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0,
            max_tokens=5,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify traveller replies. "
                        "Return exactly one word: yes, no, or unclear. "
                        "Do not explain your answer."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        result = response.choices[0].message.content.strip().lower()

        if result in ("yes", "no", "unclear"):
            return result

        return "unclear"

    # Mock backend — simple keyword match, deliberately conservative:
    # anything not a clear yes/no falls to "unclear", same fail-safe
    # direction the real LLM call falls back to on a parse error
    normalized = text.strip().lower()

    if normalized in _CLEAR_YES:
        return "yes"

    if normalized in _CLEAR_NO:
        return "no"

    return "unclear"


async def classify_reply(text: str) -> str:
    try:
        return await asyncio.wait_for(_llm_classify(text), timeout=2.0)
    except Exception as e:
        logger.info(f"classify_reply failing safe to 'unclear': {e}")
        return "unclear"


async def send_sms_clarify(pending) -> None:
    prefs = await db.get_member_prefs(pending.member_id)
    await get_send_sms()(prefs, "Sorry, I didn't quite catch that — could you reply with a "
                                 "simple YES or NO?")


async def handle_sms_reply(from_number: str, body: str) -> None:
    """§3's handle_sms_reply — the actual inbound webhook handler calls
    this. Looks up the member by phone, finds their one open pending
    response (guaranteed unique by the storage layer's one-open-per-member
    rule), classifies the reply, and routes it back to whichever module
    asked the original question."""
    member = await db.find_member_by_phone(from_number)
    if not member:
        logger.warning(f"SMS reply from unrecognized number {from_number!r}, dropping")
        return
    pending = await db.find_open_pending_response(member.member_id)
    if not pending or pending.expires_at < datetime.utcnow():
        logger.info(f"No open pending response for member={member.member_id}, dropping reply")
        return
    intent = await classify_reply(body)
    if intent == "unclear":
        await send_sms_clarify(pending)
        return
    await db.mark_pending_response_resolved(pending.notification_id)
    await emit_event(pending.callback_event_type, thread_id=pending.origin_thread_id,
                      response=intent, origin_module=pending.origin_module)
