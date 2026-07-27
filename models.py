"""
Shared data models for the hotel rescheduling module.

These match the architecture doc's §4 (data model), §5 (core functions),
and §7a (provider adapter interfaces) exactly — this file is the single
source of truth for shapes every other module in this package imports.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional, TypedDict

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# §7a.1 — provider-facing shapes
# --------------------------------------------------------------------------

class HotelOption(BaseModel):
    hotel_id: str                       # THIS PROVIDER's id for the hotel/rate —
                                         # not portable across vendors, see §7a.4
    hotel_name: str
    city_id: str
    check_in: date
    check_out: date
    nightly_rate: float
    total_price: float
    star_rating: Optional[float] = None
    cancellable_until: Optional[date] = None
    source_provider: str                # e.g. "SCRAPPA", "MOCK" — which search
                                         # backend produced this option
    raw: dict = Field(default_factory=dict)  # untouched provider payload,
                                              # kept for debugging/audit only

    # --- fields used by rank() (§5.6a) ---
    distance_km: Optional[float] = None       # distance from the original/anchor hotel
    brand_match: Optional[bool] = None        # matches member's co-brand preference
    review_count: Optional[int] = None        # kept on the model for completeness;
                                               # NOT one of our 5 ranking dimensions
                                               # (see the review_count/rating redundancy
                                               # note from the design discussion)


class BookingResult(BaseModel):
    success: bool
    provider_booking_id: Optional[str] = None
    actual_price: Optional[float] = None
    error: Optional[str] = None


class CancelResult(BaseModel):
    success: bool
    error: Optional[str] = None


class GuestInfo(BaseModel):
    title: str = "MR"
    first_name: str
    last_name: str
    phone: str
    email: str
    payment_token: Optional[str] = None


# --------------------------------------------------------------------------
# §5.1/5.2 — trigger detection / delta
# --------------------------------------------------------------------------

class Stay(BaseModel):
    city: str
    check_in: date
    check_out: date
    occupants: int = 1
    booking_id: Optional[str] = None


class DeltaAction(str, Enum):
    ADD_NIGHT = "ADD_NIGHT"
    SHIFT_DATES = "SHIFT_DATES"
    CANCEL_NIGHTS = "CANCEL_NIGHTS"
    LOUNGE_ACCESS = "LOUNGE_ACCESS"
    ESCALATE_CONFLICT = "ESCALATE_CONFLICT"
    MEMBER_OVERRIDE = "MEMBER_OVERRIDE"
    NONE = "NONE"


# --------------------------------------------------------------------------
# §5.4 / §5.4a — airline coverage
# --------------------------------------------------------------------------

class CoverageStatus(str, Enum):
    CONFIRMED_COVERED = "CONFIRMED_COVERED"
    CONFIRMED_NOT_COVERED = "CONFIRMED_NOT_COVERED"
    LIKELY_COVERED = "LIKELY_COVERED"


class CoverageConfidenceDecision(str, Enum):
    PROCEED_AUTONOMOUS = "PROCEED_AUTONOMOUS"
    VERIFY_WITH_MEMBER = "VERIFY_WITH_MEMBER"


# --------------------------------------------------------------------------
# §5.5 — policy
# --------------------------------------------------------------------------

class PolicyStatus(str, Enum):
    AUTO_APPROVED = "AUTO_APPROVED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    DENIED = "DENIED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"


class PolicyDecision(BaseModel):
    status: PolicyStatus
    reason: Optional[str] = None
    budget_used: Optional[float] = None
    remaining_cap: Optional[float] = None


# --------------------------------------------------------------------------
# §5.6 — execution
# --------------------------------------------------------------------------

class ExecutionResult(BaseModel):
    success: bool
    booking_id: Optional[str] = None
    reason: Optional[str] = None
    # Populated only when reason == "NEEDS_MEMBER_CHOICE" — the top-N ranked
    # candidates for §5.7's override flow to present, per §5.6's "present
    # the top 3 via rank_top_n() and let the member choose" behavior.
    candidate_options: list[HotelOption] = Field(default_factory=list)


# --------------------------------------------------------------------------
# §3a — LangGraph state schema (verbatim from the doc)
# --------------------------------------------------------------------------

class HotelState(TypedDict, total=False):
    itinerary_id: str
    event_id: str
    member: dict
    itinerary: dict
    delta: list[dict]
    current_delta_index: int
    coverage_status: Optional[str]
    policy_decision: Optional[dict]
    execution_result: Optional[dict]
