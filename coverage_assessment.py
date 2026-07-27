"""
§5.4a — coverage-confidence assessment.

This is the ONE place in the whole module where an LLM call is appropriate,
and it's isolated in its own file on purpose so the deterministic/
non-deterministic boundary is visible in the file tree, not just in a
comment (policy.py stays 100% deterministic).

Shape mirrors the flight-monitoring doc's "dicey case" pattern: LIKELY_COVERED
used to be a flat rule (DOT dashboard says the carrier commits to hotel
coverage -> always ask the member, no matter how stale or unreliable that
signal actually is for this carrier/cause/route). Instead, pull a few
enrichment signals first, then let the model decide PROCEED_AUTONOMOUS vs
VERIFY_WITH_MEMBER — it resolves ambiguity about *whether to ask*, it never
picks a hotel or approves spend. §5.5's budget/policy check still runs
unconditionally either way, regardless of this decision.

The three enrichment functions are the ONLY real @tool-decorated LangChain
tools in this entire module. Everything in tool.py, ranking.py, and
policy.py is plain async functions an agent cannot freely re-order.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional, Any

from langchain_core.tools import tool
from pydantic import BaseModel

from .models import CoverageConfidenceDecision
from .storage import db

# Swap for your actual chat-model client; kept as a thin wrapper so this
# file has exactly one call site to point at your LLM provider of choice.
from .llm import get_llm


@tool
async def get_carrier_coverage_precedent(carrier_code: str, disruption_cause: str) -> dict:
    """Look up how reliably a specific carrier has historically followed
    through on hotel-coverage commitments for a given disruption cause
    (e.g. mechanical, weather, crew). Returns a rate 0.0-1.0 and a sample
    size so the model can weigh a thin sample appropriately."""
    # FLAGGED ASSUMPTION: seeded placeholder data. Swap for a real
    # historical-claims lookup once available.
    seed = {
        ("AA", "CONTROLLABLE"): {"coverage_rate": 0.91, "sample_size": 214},
        ("DL", "CONTROLLABLE"): {"coverage_rate": 0.86, "sample_size": 178},
    }
    result = seed.get((carrier_code, disruption_cause),
                       {"coverage_rate": None, "sample_size": 0})
    return {"carrier_code": carrier_code, "disruption_cause": disruption_cause, **result}


@tool
async def get_dot_entry_freshness(carrier_code: str) -> dict:
    """Return how stale this carrier's DOT-dashboard commitment entry is —
    an old entry is weaker evidence than a recently-verified one."""
    commitment = await db.get_airline_commitment(carrier_code)
    if not commitment or not commitment.last_verified:
        return {"carrier_code": carrier_code, "days_since_verified": None, "commits_hotel": None}
    days_since = (date.today() - commitment.last_verified).days
    return {"carrier_code": carrier_code, "days_since_verified": days_since,
            "commits_hotel": commitment.commits_hotel}


@tool
async def get_recent_complaint_signal(carrier_code: str) -> dict:
    """Weak signal: recent complaint volume specifically about hotel-voucher
    denial for this carrier. Optional evidence, not a hard gate."""
    # FLAGGED ASSUMPTION: seeded placeholder; swap for a real complaints feed.
    seed = {"AA": {"recent_denial_complaints": 3}, "DL": {"recent_denial_complaints": 1}}
    return {"carrier_code": carrier_code, **seed.get(carrier_code, {"recent_denial_complaints": None})}


ENRICHMENT_TOOLS = [get_carrier_coverage_precedent, get_dot_entry_freshness, get_recent_complaint_signal]


class CoverageConfidenceResult(BaseModel):
    decision: CoverageConfidenceDecision
    reasoning: str  # carried into the member's timeline (§5.4a's "recoverable"
                     # safety note) even when the decision turns out wrong


_SYSTEM_PROMPT = """You are assessing whether an airline is confidently expected \
to cover a member's hotel stay after a controllable disruption, using the DOT \
commitment entry plus enrichment signals you're given.

Decide PROCEED_AUTONOMOUS only when the evidence strongly and specifically \
supports this carrier following through for this cause. Otherwise decide \
VERIFY_WITH_MEMBER — when in doubt, ask the member, don't assume coverage.

You do NOT decide which hotel to book, whether to spend money, or bypass any \
budget/policy check. You only decide whether the member needs to confirm \
coverage before this module proceeds. Respond ONLY with JSON matching:
{"decision": "PROCEED_AUTONOMOUS" | "VERIFY_WITH_MEMBER", "reasoning": "<one sentence>"}
"""


async def assess_coverage_confidence(itinerary: dict, model: Optional[Any] = None
                                      ) -> CoverageConfidenceResult:
    carrier_code = itinerary.get("carrier_code", "")
    cause = itinerary.get("disruption_cause", "")

    precedent = await get_carrier_coverage_precedent.ainvoke(
        {"carrier_code": carrier_code, "disruption_cause": cause})
    freshness = await get_dot_entry_freshness.ainvoke({"carrier_code": carrier_code})
    complaints = await get_recent_complaint_signal.ainvoke({"carrier_code": carrier_code})

    model = model or get_llm() 

    payload = {"carrier_code": carrier_code, "disruption_cause": cause,
               "precedent": precedent, "dot_entry_freshness": freshness,
               "recent_complaints": complaints}

    response = await model.ainvoke([
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload)},
    ])

    try:
        parsed = json.loads(response.content)
        return CoverageConfidenceResult(**parsed)
    except (json.JSONDecodeError, ValueError, TypeError):
        # A malformed LLM response fails SAFE toward asking the member —
        # never toward silently proceeding without them.
        return CoverageConfidenceResult(
            decision=CoverageConfidenceDecision.VERIFY_WITH_MEMBER,
            reasoning="Could not parse confidence assessment; defaulting to member verification.")
