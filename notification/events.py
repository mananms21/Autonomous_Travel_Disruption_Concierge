"""
Cross-module event boundary — §3's routing-back mechanism made concrete.

This module never interprets what a reply MEANS — it only knows which
service to hand it to (via origin_module) and what path to call (derived
from callback_event_type). Each origin module (hotel, flight rebooking,
policy engine, ...) owns a small listener that turns this generic callback
into whatever it actually needs internally (e.g. the hotel module's
`hotel_graph.ainvoke(Command(resume=...))`).

FLAGGED STUB: ORIGIN_MODULE_BASE_URLS is a hardcoded dev-convenience
default. In a real multi-service deployment this would come from service
discovery / an env var per module, not a literal dict here.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger("notification.events")

ORIGIN_MODULE_BASE_URLS: dict[str, str] = {
    "HOTEL": os.getenv("HOTEL_MODULE_URL", "http://localhost:8001"),
    "FLIGHT_REBOOKING": os.getenv("FLIGHT_MODULE_URL", "http://localhost:8002"),
    "POLICY_ENGINE": os.getenv("POLICY_ENGINE_URL", "http://localhost:8003"),
}

_CAMEL_TO_KEBAB_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _event_type_to_path(callback_event_type: str) -> str:
    """'CoverageVerificationResponse' -> 'coverage-verification-response' —
    matches the hotel module's actual listener endpoint naming convention."""
    return _CAMEL_TO_KEBAB_RE.sub("-", callback_event_type).lower()


async def emit_event(event_type: str, **fields: Any) -> None:
    """Routes a resolved member reply back to its origin module. `fields`
    must include origin_module (who to call) — everything else is passed
    through as the callback's JSON body."""
    origin_module = fields.get("origin_module")
    base_url = ORIGIN_MODULE_BASE_URLS.get(origin_module) if origin_module else None
    path = _event_type_to_path(event_type)

    if base_url is None:
        logger.info(f"[emit_event stub] {event_type}: {fields} (no base URL registered "
                    f"for origin_module={origin_module!r} — logging only)")
        return

    url = f"{base_url}/events/{path}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=fields)
            resp.raise_for_status()
        logger.info(f"emit_event -> {url}: {fields}")
    except Exception as e:
        # a failed callback here means a paused graph elsewhere never resumes —
        # this should NOT be swallowed silently in production; logged loudly,
        # and worth wiring to NotificationFailed / escalation once that's built
        logger.error(f"emit_event FAILED -> {url}: {fields} ({e})")
