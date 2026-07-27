"""
§5.5 — hotel policy & budget check (Amex-tiered).

Deliberately 100% deterministic, unit-testable, no I/O beyond store reads.
The coverage-confidence LLM call (§5.4a) does NOT live here on purpose —
mixing it in would undo the exact deterministic/non-deterministic boundary
this whole architecture is built around. See coverage_assessment.py.
"""
from __future__ import annotations

from typing import Optional

from .models import CoverageStatus, PolicyDecision, PolicyStatus
from .storage import db

# How far a candidate's actual booked price is allowed to drift from the
# estimate that was policy-approved before triggering a re-check (§5.6).
PRICE_DRIFT_TOLERANCE = 15.0

# "Marginally over cap" band — over the hard cap but within this multiplier
# escalates for human approval instead of an outright deny (§5.5).
OVER_CAP_ESCALATION_MULTIPLIER = 1.15


def estimate_cost(delta_entry: dict) -> float:
    """Rough pre-search cost estimate used for the policy gate, before a
    real search happens. Nights * a conservative per-night placeholder,
    swap for a real historical-average lookup once available."""
    check_in = delta_entry.get("check_in")
    check_out = delta_entry.get("check_out")
    nights = max((check_out - check_in).days, 1) if check_in and check_out else 1
    occupants = delta_entry.get("occupants", 1)
    per_night_estimate = delta_entry.get("estimated_nightly_rate", 180.0)
    occupancy_multiplier = 1.0 if occupants <= 2 else 1.0 + 0.25 * (occupants - 2)
    return round(nights * per_night_estimate * occupancy_multiplier, 2)


async def check_airline_coverage(itinerary: dict) -> CoverageStatus:
    """§5.4 — DOT-backed, three states. Kept here (rather than duplicated)
    since §5.5 calls it directly, matching the doc."""
    metadata = itinerary.get("disruption_metadata", {})
    signal = metadata.get("airline_hotel_voucher_issued")
    if signal is True:
        return CoverageStatus.CONFIRMED_COVERED
    if signal is False:
        return CoverageStatus.CONFIRMED_NOT_COVERED
    if itinerary.get("disruption_cause") != "CONTROLLABLE":
        return CoverageStatus.CONFIRMED_NOT_COVERED
    commitment = await db.get_airline_commitment(itinerary.get("carrier_code", ""))
    if commitment and commitment.commits_hotel:
        return CoverageStatus.LIKELY_COVERED
    return CoverageStatus.CONFIRMED_NOT_COVERED


async def evaluate_hotel_policy(delta_entry: dict, itinerary: dict, member: dict,
                                 remaining_trip_budget: Optional[float] = None) -> PolicyDecision:
    if not member.get("autonomous_rebooking_enabled"):
        return PolicyDecision(status=PolicyStatus.DENIED, reason="autonomy not enabled")
    if member.get("card_used_for_trip") != member.get("card_id"):
        return PolicyDecision(status=PolicyStatus.DENIED, reason="trip not booked on eligible card")

    tier = await db.get_card_tier(member.get("card_product_code", ""))

    if delta_entry.get("action") in ("ADD_NIGHT", "SHIFT_DATES"):
        if itinerary.get("delay_hours", 0) < tier.delay_threshold_hours:
            return PolicyDecision(status=PolicyStatus.DENIED, reason="below delay threshold")

    coverage = await check_airline_coverage(itinerary)
    if coverage == CoverageStatus.CONFIRMED_COVERED:
        return PolicyDecision(status=PolicyStatus.DENIED, reason="airline already covering accommodation")
    if coverage == CoverageStatus.LIKELY_COVERED:
        return PolicyDecision(status=PolicyStatus.PENDING_VERIFICATION,
                               reason="airline commitment exists, confirming with member")

    claims_used = await db.count_claims_this_year(member.get("card_id", ""))
    if claims_used >= tier.max_claims_per_12mo:
        return PolicyDecision(status=PolicyStatus.NEEDS_APPROVAL, reason="annual claim limit reached")

    est_cost = estimate_cost(delta_entry)
    cap = min(tier.per_trip_cap_usd, remaining_trip_budget if remaining_trip_budget is not None else float("inf"))

    if est_cost <= cap:
        return PolicyDecision(status=PolicyStatus.AUTO_APPROVED, budget_used=est_cost,
                               remaining_cap=cap - est_cost)
    elif est_cost <= cap * OVER_CAP_ESCALATION_MULTIPLIER:
        return PolicyDecision(status=PolicyStatus.NEEDS_APPROVAL, reason="marginally over cap")
    return PolicyDecision(status=PolicyStatus.DENIED, reason="exceeds policy cap")
