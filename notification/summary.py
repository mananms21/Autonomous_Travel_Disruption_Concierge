"""
§5 — domain-agnostic diff object + LLM summary. The LLM never invents
facts; it only rephrases `member_facing_detail`, and `passes_guardrail`
enforces that mechanically rather than trusting the model's honesty.

LLM_BACKEND=mock (default) uses a tiny deterministic stand-in so this is
fully testable offline — it still exercises the real guardrail/fallback
logic, just without a live  call. Switch to LLM_BACKEND=
for the real Haiku call.
"""
from __future__ import annotations

import asyncio
import logging
import os
import traceback
from .config import LLM_BACKEND
from .models import ChangeDiff

logger = logging.getLogger("notification.summary")

MAX_LEN = {"sms": 160, "push": 120, "email": 600}


def render_template(diff: ChangeDiff, channel: str) -> str:
    """Plain f-string fallback that never depends on the LLM call
    succeeding under time pressure — the LLM adds polish, this guarantees
    the member gets correct information regardless."""
    detail = ", ".join(f"{k}: {v}" for k, v in diff.member_facing_detail.items() if v)
    text = f"{diff.domain.title()} update — {diff.action.replace('_', ' ').lower()}. {detail}."
    max_len = MAX_LEN.get(channel, 300)
    return text[:max_len]


def passes_guardrail(text: str, diff: ChangeDiff, max_len: int) -> bool:
    if len(text) > max_len:
        return False
    for key, value in diff.member_facing_detail.items():
        if value and str(value) not in text:
            return False  # cheap guardrail: required facts must literally appear
    return True


async def _call_llm(diff: ChangeDiff, max_len: int) -> str:
    

    facts = "\n".join(
        f"{key}: {value}"
        for key, value in diff.member_facing_detail.items()
        if value
    )

    prompt = f"""
You are generating a notification for a traveller.

IMPORTANT RULES (must follow exactly):

1. Include EVERY field listed below.
2. Copy every value EXACTLY as written.
3. Do NOT shorten names.
4. Do NOT omit any field.
5. Do NOT change the date format.
6. Do NOT change currency values.
7. Do NOT add information that is not provided.
8. Produce ONE professional notification sentence.
9. Maximum length: {max_len} characters.

Required Fields:
{facts}

Domain: {diff.domain}
Action: {diff.action}
"""

    if LLM_BACKEND == "groq":
        

        from groq import AsyncGroq

        client = AsyncGroq(
            api_key=os.environ["GROQ_API_KEY"]
        )

        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0,
            max_tokens=200,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You generate customer travel notifications.\n"
                        "You MUST include every supplied field exactly as given.\n"
                        "Never abbreviate names.\n"
                        "Never omit dates.\n"
                        "Never invent facts.\n"
                        "Return only the notification."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        return response.choices[0].message.content.strip()

    # Mock backend (offline mode)
    detail = " ".join(
        str(v)
        for v in diff.member_facing_detail.values()
        if v
    )

    return (
        f"{diff.action.replace('_', ' ').title()}: {detail}"
    )[:max_len]


async def generate_summary(diff: ChangeDiff, channel: str) -> tuple[str, str]:
    max_len = MAX_LEN.get(channel, 300)

    try:
        text = await asyncio.wait_for(
            _call_llm(diff, max_len),
            timeout=2.0
        )

        

        if not passes_guardrail(text, diff, max_len):
            raise ValueError("LLM summary failed the fact guardrail")

        return text, "LLM"

    except Exception as e:
        import traceback

        

        return render_template(diff, channel), "TEMPLATE_FALLBACK"
