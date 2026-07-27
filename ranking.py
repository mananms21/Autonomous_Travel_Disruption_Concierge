"""
§5.6a — deterministic hotel ranking.

Deliberately NOT an LLM call. This is the last step before real dollars get
committed to a booking, so it lives on the deterministic side of the line
this whole architecture draws everywhere: money-spending decisions are code,
not model judgment. See the design-discussion doc for the full reasoning
(testability, latency/cost on a hot path, structured numeric inputs with no
ambiguity to resolve).

Weights (40/25/15/10/10 across price/distance/brand/rating/cancellation-
flexibility) were deliberately kept over a generic OTA-style set
(price/distance/rating/amenities/review_count) — brand_match ties to real
card/airline co-brand value, and cancellation_flexibility is specifically
justified by this system's context: a hotel booked mid-disruption is more
likely than a normal booking to need to change again. review_count was
dropped as a dimension because it's largely redundant with rating (more
reviews -> more trustworthy rating -> partially double-counts one signal
under two names).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel

from .models import HotelOption

# The one gate that decides "book automatically" vs "ask the member" —
# see §5.6 in the architecture doc. Deliberately a plain threshold, not an
# LLM judgment call, because rank() already produces the number needed.
MIN_ACCEPTABLE_MATCH_SCORE = 0.55

# A hotel with 0 rating floor filtered entirely, regardless of score —
# quality floor as a hard filter, per the design discussion's testing
# priorities (§9-style test: "quality floor as hard filter").
MIN_ACCEPTABLE_STAR_RATING = 2.0


class RankingWeights(BaseModel):
    price: float = 0.40
    distance: float = 0.25
    brand: float = 0.15
    rating: float = 0.10
    cancellation_flexibility: float = 0.10

    def total(self) -> float:
        return (self.price + self.distance + self.brand
                + self.rating + self.cancellation_flexibility)


DEFAULT_WEIGHTS = RankingWeights()


def _normalize_price(option: HotelOption, candidates: list[HotelOption]) -> float:
    """Cheaper -> higher score. Reweighted to avoid a divide-by-zero when
    every candidate is (implausibly) free — see the "zero-price reweighting"
    test from §9-style testing priorities."""
    prices = [c.total_price for c in candidates if c.total_price is not None]
    if not prices:
        return 0.0
    lo, hi = min(prices), max(prices)
    if hi == lo:
        return 1.0  # all candidates cost the same; price doesn't discriminate
    return 1.0 - ((option.total_price - lo) / (hi - lo))


def _normalize_distance(option: HotelOption, candidates: list[HotelOption]) -> float:
    """Closer -> higher score. Missing distance data scores neutral (0.5)
    rather than zero, so an option isn't unfairly punished for a provider
    that didn't return coordinates."""
    if option.distance_km is None:
        return 0.5
    dists = [c.distance_km for c in candidates if c.distance_km is not None]
    if not dists:
        return 0.5
    lo, hi = min(dists), max(dists)
    if hi == lo:
        return 1.0
    return 1.0 - ((option.distance_km - lo) / (hi - lo))


def _brand_score(option: HotelOption) -> float:
    return 1.0 if option.brand_match else 0.0


def _rating_score(option: HotelOption) -> float:
    if option.star_rating is None:
        return 0.5
    return min(option.star_rating, 5.0) / 5.0


def _cancellation_score(option: HotelOption, check_in: date) -> float:
    """More days between now and the cancellation deadline (relative to
    check-in) -> more flexible -> higher score. No cancellation info at all
    scores 0 (least flexible), not neutral — an unknown cancellation policy
    should never look as good as a verified flexible one."""
    if option.cancellable_until is None:
        return 0.0
    days_of_flex = (option.cancellable_until - date.today()).days
    days_until_checkin = max((check_in - date.today()).days, 1)
    return max(0.0, min(days_of_flex / days_until_checkin, 1.0))


def score(option: HotelOption, candidates: list[HotelOption], check_in: date,
          weights: RankingWeights = DEFAULT_WEIGHTS) -> float:
    """Pure function: same inputs -> same output, every time. This is what
    makes rank() unit-testable in a way an LLM ranking call never could be."""
    return (
        weights.price * _normalize_price(option, candidates)
        + weights.distance * _normalize_distance(option, candidates)
        + weights.brand * _brand_score(option)
        + weights.rating * _rating_score(option)
        + weights.cancellation_flexibility * _cancellation_score(option, check_in)
    ) / weights.total()


def rank(candidates: list[HotelOption], constraints: dict,
         weights: RankingWeights = DEFAULT_WEIGHTS) -> tuple[Optional[HotelOption], float]:
    """Returns (chosen, score). chosen is None if every candidate fails the
    quality floor (§5.6 gate then treats this the same as NO_AVAILABILITY)."""
    check_in = constraints.get("check_in") or (candidates[0].check_in if candidates else date.today())

    passing = [c for c in candidates
               if c.star_rating is None or c.star_rating >= MIN_ACCEPTABLE_STAR_RATING]
    if not passing:
        return None, 0.0

    scored = [(c, score(c, passing, check_in, weights)) for c in passing]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[0]


def rank_top_n(candidates: list[HotelOption], constraints: dict, n: int = 3,
               weights: RankingWeights = DEFAULT_WEIGHTS) -> list[tuple[HotelOption, float]]:
    """Backs the low-match-score member-choice path in §5.6 — when the top
    pick doesn't clear MIN_ACCEPTABLE_MATCH_SCORE, present these n instead
    of auto-booking, reusing the same override endpoint (§5.7)."""
    check_in = constraints.get("check_in") or (candidates[0].check_in if candidates else date.today())

    passing = [c for c in candidates
               if c.star_rating is None or c.star_rating >= MIN_ACCEPTABLE_STAR_RATING]
    scored = [(c, score(c, passing, check_in, weights)) for c in passing]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:n]
