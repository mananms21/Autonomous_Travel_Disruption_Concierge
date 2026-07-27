"""
§3a — LangGraph orchestration, §5.6 (execution), §5.7 (member override).

HotelReschedulingTool() is the ONLY public entry point this module exposes.
search_hotels / book_hotel / cancel_hotel_booking / get_hotel_option are
deliberately plain internal functions here, NOT separate @tool-decorated
LangChain tools — an agent that could call them independently could skip
rank(), skip evaluate_hotel_policy(), or break idempotency-key derivation.
The only real tools in this whole module are the three coverage-confidence
enrichment functions in coverage_assessment.py.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional

from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command

from .config import hotel_provider
from .coverage_assessment import assess_coverage_confidence
from .delta import compute_hotel_delta, compute_required_timeline
from .display import print_top_options, print_booking_confirmation
from .events import ChangeDiff, emit_event, emit_notification_event, get_member
from .models import (
    CoverageConfidenceDecision, CoverageStatus, ExecutionResult, GuestInfo,
    HotelOption, HotelState, PolicyStatus, Stay,
)
from .policy import check_airline_coverage, estimate_cost, evaluate_hotel_policy, PRICE_DRIFT_TOLERANCE
from .ranking import MIN_ACCEPTABLE_MATCH_SCORE, rank, rank_top_n
from .storage import db

logger = logging.getLogger("hotel_rescheduling.tool")


class ProviderTimeoutError(Exception):
    pass


# --------------------------------------------------------------------------
# Plain internal functions — NOT LangChain tools. See module docstring.
# --------------------------------------------------------------------------

def constraints_from(delta_entry: dict) -> dict:
    itinerary = delta_entry.get("itinerary", {})

    arrival_airport = None

    if itinerary.get("legs"):
        arrival_airport = itinerary["legs"][-1].get("arrival_airport")

    return {
        "check_in": delta_entry.get("check_in"),
        "check_out": delta_entry.get("check_out"),
        "occupants": delta_entry.get("occupants", 1),

        # NEW
        "near_airport": arrival_airport,
    }


async def search_hotels(city: str, check_in: date, check_out: date, constraints: dict) -> list[HotelOption]:
    return await hotel_provider.search(city, check_in, check_out, constraints)


async def get_hotel_option(hotel_id: str, hint_city_id: Optional[str] = None,
                            hint_check_in: Optional[date] = None,
                            hint_check_out: Optional[date] = None) -> Optional[HotelOption]:
    return await hotel_provider.get_option(hotel_id, hint_city_id, hint_check_in, hint_check_out)


async def book_hotel(option: HotelOption, guest: GuestInfo, idempotency_key: str):
    return await hotel_provider.book(option, guest, idempotency_key)


async def cancel_hotel_booking(provider_booking_id: str, reason: str):
    return await hotel_provider.cancel(provider_booking_id, reason)


# --------------------------------------------------------------------------
# §5.6 — execution, with price re-validation and idempotency discipline
# --------------------------------------------------------------------------

async def execute_cancellation_transaction(old_booking_id: str) -> "CancelResult":
    from .models import CancelResult
    old = db.hotel_bookings.get(old_booking_id)
    if not old or not old.provider_booking_id:
        return CancelResult(success=False, error="OLD_BOOKING_NOT_FOUND")
    result = await cancel_hotel_booking(old.provider_booking_id, reason=f"replaced:{old_booking_id}")
    await db.update_status(old_booking_id, "CANCELLED" if result.success else "CANCEL_FAILED")
    return result


async def execute_booking_transaction(delta_entry: dict, guest: GuestInfo, approved_cost: float,
                                       forced_choice: Optional[HotelOption] = None,
                                       member: Optional[dict] = None) -> ExecutionResult:

    print("\n[5/6] Searching hotels...")

    if forced_choice is not None:
        print("Member override selected.")
        chosen = forced_choice
        match_score = 1.0

    else:
        candidates = await search_hotels(
            delta_entry["city"],
            delta_entry["check_in"],
            delta_entry["check_out"],
            constraints_from(delta_entry)
        )

        if not candidates:
            print("No hotels found.")
            return ExecutionResult(success=False, reason="NO_AVAILABILITY")

        print(f"Found {len(candidates)} hotels.")

        print("\nRanking hotels...")
        chosen, match_score = rank(candidates, constraints_from(delta_entry))

        if chosen is None:
            print("No suitable hotel found.")
            return ExecutionResult(success=False, reason="NO_AVAILABILITY")

        top_n = rank_top_n(candidates, constraints_from(delta_entry), n=3)

        print("\nTop 3 Recommended Hotels")
        print("-" * 60)
        print_top_options(top_n)
        print("-" * 60)

        print(f"\nSelected Hotel : {chosen.hotel_name}")
        print(f"Match Score    : {match_score:.2f}")

        if match_score < MIN_ACCEPTABLE_MATCH_SCORE:
            print("\nMember approval required.")
            return ExecutionResult(
                success=False,
                reason="NEEDS_MEMBER_CHOICE",
                candidate_options=[option for option, _score in top_n]
            )

    idempotency_key = f"{delta_entry.get('itinerary_id')}:{delta_entry.get('event_id')}:book"

    local_id = await db.insert_hotel_booking(
        status="PENDING",
        idempotency_key=idempotency_key,
        itinerary_id=delta_entry.get("itinerary_id"),
        city=delta_entry.get("city"),
        check_in=delta_entry.get("check_in"),
        check_out=delta_entry.get("check_out"),
        occupants=delta_entry.get("occupants", 1),
        hotel_name=chosen.hotel_name,
        nightly_rate=chosen.nightly_rate,
        event_id=delta_entry.get("event_id")
    )

    print("\n[6/6] Booking hotel...")

    try:
        result = await book_hotel(chosen, guest, idempotency_key)

    except ProviderTimeoutError:
        print("Booking provider timed out. Checking booking status...")

        result = await hotel_provider.get_booking_by_idempotency_key(idempotency_key)

        if result is None:
            print("Booking could not be confirmed.")
            await db.update_status(local_id, "FAILED")
            return ExecutionResult(
                success=False,
                reason="PROVIDER_TIMEOUT_UNCONFIRMED"
            )

    if not result.success:
        print(f"Booking failed : {result.error}")
        await db.update_status(local_id, "FAILED")
        return ExecutionResult(
            success=False,
            reason=result.error
        )

    if result.actual_price is not None and forced_choice is None:

        drift = result.actual_price - chosen.total_price

        if drift > PRICE_DRIFT_TOLERANCE:

            print("\nPrice changed during booking.")
            print(f"Quoted Price : {chosen.total_price}")
            print(f"Final Price  : {result.actual_price}")

            recheck = await evaluate_hotel_policy(
                {
                    **delta_entry,
                    "estimated_nightly_rate":
                        result.actual_price /
                        max((delta_entry["check_out"] - delta_entry["check_in"]).days, 1)
                },
                delta_entry.get("itinerary", {}),
                member or {}
            )

            if recheck.status != PolicyStatus.AUTO_APPROVED:

                print("Price exceeds policy. Cancelling booking...")

                await cancel_hotel_booking(
                    result.provider_booking_id,
                    f"cancel:{idempotency_key}"
                )

                await db.update_status(local_id, "FAILED")

                return ExecutionResult(
                    success=False,
                    reason="PRICE_DRIFT_EXCEEDED_POLICY"
                )

    await db.update_hotel_booking(
        local_id,
        status="CONFIRMED",
        provider_booking_id=result.provider_booking_id
    )

    print_booking_confirmation(chosen)

    print("\nBooking Successful")
    print(f"Booking ID : {local_id}")

    old_id = delta_entry.get("old_booking_id") or delta_entry.get("replaces_booking_id")

    if old_id:
        print("\nCancelling previous booking...")

        cancel_result = await execute_cancellation_transaction(old_id)

        if not cancel_result.success:
            print("Previous booking could not be cancelled.")

            await emit_event(
                "HotelEscalationRaised",
                reason="OLD_BOOKING_CANCEL_FAILED",
                booking_id=old_id
            )

    print("\nNotification sent.")

    print("\nWorkflow Complete.")

    return ExecutionResult(
        success=True,
        booking_id=local_id
    )

# --------------------------------------------------------------------------
# §5.7 — member override (reuses the same transaction)
# --------------------------------------------------------------------------

async def on_member_override(event: dict) -> None:
    """event: MemberOverrideRequested-shaped dict with domain, itinerary_id,
    preferred_hotel_id, guest_info."""
    if event.get("domain") != "HOTEL":
        return  # not this module's event to handle

    current = await db.get_hotel_booking(event["itinerary_id"], status="CONFIRMED")
    chosen = await get_hotel_option(event["preferred_hotel_id"])
    if not chosen:
        await emit_event("HotelEscalationRaised", itinerary_id=event["itinerary_id"],
                          reason="OVERRIDE_HOTEL_NO_LONGER_AVAILABLE")
        return

    guest = GuestInfo(**event["guest_info"])
    result = await execute_booking_transaction(
        delta_entry={"action": "MEMBER_OVERRIDE",
                     "old_booking_id": current.id if current else None,
                     "city": chosen.city_id, "check_in": chosen.check_in,
                     "check_out": chosen.check_out, "itinerary_id": event["itinerary_id"],
                     "event_id": event.get("event_id", "override")},
        guest=guest, approved_cost=chosen.total_price, forced_choice=chosen)
    await emit_event("HotelBookingConfirmed" if result.success else "HotelEscalationRaised",
                      **result.model_dump())


# --------------------------------------------------------------------------
# §3a — LangGraph nodes
# --------------------------------------------------------------------------

async def compute_delta_node(state: HotelState) -> HotelState:
    print("\n[2/6] Computing required hotel accommodation...")

    member = state["member"]

    required = await compute_required_timeline(state["itinerary"], member)

    print(f"Required hotel stays : {len(required)}")

    booked_records = [
        r for r in db.hotel_bookings.values()
        if r.itinerary_id == state["itinerary_id"]
        and r.status == "CONFIRMED"
    ]

    booked = [
        Stay(
            city=r.city_id,
            check_in=r.check_in,
            check_out=r.check_out,
            occupants=r.occupants,
            booking_id=r.id,
        )
        for r in booked_records
    ]

    state["delta"] = compute_hotel_delta(required, booked)

    print("\nActions Required")

    if state["delta"]:
        for i, action in enumerate(state["delta"], 1):
            print(f"  {i}. {action['action']}  |  {action['city']}")
    else:
        print("  No hotel changes required.")

    state["current_delta_index"] = 0

    return state


async def coverage_check_node(state: HotelState) -> HotelState:
    print("\n[3/6] Checking airline coverage...")

    coverage = await check_airline_coverage(state["itinerary"])

    print(f"Coverage Status : {coverage.value}")

    if coverage == CoverageStatus.LIKELY_COVERED:

        confidence = await assess_coverage_confidence(state["itinerary"])

        print(f"Confidence Decision : {confidence.decision.value}")
        print(f"Reason : {confidence.reasoning}")

        if confidence.decision == CoverageConfidenceDecision.PROCEED_AUTONOMOUS:

            print("Proceeding autonomously.")

            state["coverage_status"] = CoverageStatus.CONFIRMED_NOT_COVERED.value

            await db.log_action(
                itinerary_id=state["itinerary_id"],
                delta_action="COVERAGE_CHECK",
                decision="PROCEED_AUTONOMOUS",
                policy_check_result={"reasoning": confidence.reasoning},
            )

        else:

            print("Waiting for member confirmation...")

            thread_id = f"hotel:{state['itinerary_id']}:{state['event_id']}"

            await emit_notification_event(
                event_type="CoverageVerificationRequested",
                member_id=state["member"]["id"],
                itinerary_id=state["itinerary_id"],
                thread_id=thread_id,
                callback_event_type="CoverageVerificationResponse",
                requires_response=True,
                change_diff=ChangeDiff(
                    domain="HOTEL",
                    action="VERIFY_COVERAGE",
                    reason="airline commitment on file, confirming with member",
                    member_facing_detail={
                        "carrier": state["itinerary"]["carrier_code"],
                        "llm_reasoning": confidence.reasoning,
                    },
                ),
            )

            state["coverage_status"] = interrupt("awaiting_coverage_response")

    else:

        state["coverage_status"] = coverage.value

    return state


def route_after_coverage(state: HotelState) -> str:
    if state["coverage_status"] == CoverageStatus.CONFIRMED_COVERED.value:
        return "escalate"
    return "policy_check"


async def policy_check_node(state: HotelState) -> HotelState:
    print("\n[4/6] Evaluating hotel policy...")

    idx = state["current_delta_index"]

    delta_entry = state["delta"][idx]

    decision = await evaluate_hotel_policy(
        delta_entry,
        state["itinerary"],
        state["member"],
    )

    state["policy_decision"] = decision.model_dump()

    print(f"Policy Status : {decision.status.value}")
    print(f"Reason        : {decision.reason}")

    await db.log_action(
        itinerary_id=state["itinerary_id"],
        delta_action=delta_entry["action"],
        decision=decision.status.value,
        policy_check_result=decision.model_dump(),
    )

    return state


def route_after_policy(state: HotelState) -> str:
    status = state["policy_decision"]["status"]
    if status == PolicyStatus.AUTO_APPROVED.value:
        return "execute_booking"
    if status == PolicyStatus.NEEDS_APPROVAL.value:
        return "await_approval"
    return "escalate"


async def await_approval_node(state: HotelState) -> HotelState:
    idx = state["current_delta_index"]
    delta_entry = state["delta"][idx]
    thread_id = f"hotel:{state['itinerary_id']}:{state['event_id']}"
    await emit_notification_event(
        event_type="ApprovalNeeded", member_id=state["member"]["id"],
        itinerary_id=state["itinerary_id"], thread_id=thread_id,
        callback_event_type="ApprovalResponse", requires_response=True,
        change_diff=ChangeDiff(domain="HOTEL", action=delta_entry["action"],
                                reason=state["policy_decision"].get("reason", "")))
    response = interrupt("awaiting_approval_response")
    state["policy_decision"] = {**state["policy_decision"],
                                 "status": (PolicyStatus.AUTO_APPROVED.value if response
                                            else PolicyStatus.DENIED.value)}
    return state


async def execute_booking_node(state: HotelState) -> HotelState:
    
    idx = state["current_delta_index"]
    delta_entry = {
        **state["delta"][idx],
        "itinerary": state["itinerary"],
        "itinerary_id": state["itinerary_id"],
        "event_id": state["event_id"],
    }
    guest = GuestInfo(**state["member"].get("guest_info", {
        "first_name": state["member"].get("first_name", "Member"),
        "last_name": state["member"].get("last_name", ""),
        "phone": state["member"].get("phone", ""),
        "email": state["member"].get("email", ""),
    }))
    approved_cost = state["policy_decision"].get("budget_used") or estimate_cost(delta_entry)
    result = await execute_booking_transaction(delta_entry, guest, approved_cost, member=state["member"])
    state["execution_result"] = result.model_dump()
    return state


def route_after_execution(state: HotelState) -> str:
    return "notify_confirmed" if state["execution_result"]["success"] else "escalate"


async def notify_confirmed_node(state: HotelState) -> HotelState:
    print("\nSending notification to member...")

    await emit_event(
        "HotelBookingConfirmed",
        itinerary_id=state["itinerary_id"],
        execution_result=state["execution_result"],
    )

    print("Notification sent.")
    print("\nWorkflow Complete")
    print("=" * 70)

    return state


async def escalate_node(state: HotelState) -> HotelState:
    execution_result = state.get("execution_result") or {}
    if execution_result.get("reason") == "NEEDS_MEMBER_CHOICE":
        # Not a failure — rank()'s top pick didn't clear MIN_ACCEPTABLE_MATCH_SCORE,
        # so per §5.6 the member picks from the top 3 via the same override
        # endpoint §5.7 already exposes, rather than this being auto-booked.
        await emit_event("HotelActionRequired", itinerary_id=state["itinerary_id"],
                          reason="MEMBER_CHOICE_NEEDED",
                          candidate_options=execution_result.get("candidate_options", []))
        return state
    await emit_event("HotelEscalationRaised", itinerary_id=state["itinerary_id"],
                      coverage_status=state.get("coverage_status"),
                      policy_decision=state.get("policy_decision"),
                      execution_result=execution_result)
    return state


def _build_graph():
    graph = StateGraph(HotelState)
    graph.add_node("compute_delta", compute_delta_node)
    graph.add_node("coverage_check", coverage_check_node)
    graph.add_node("policy_check", policy_check_node)
    graph.add_node("await_approval", await_approval_node)
    graph.add_node("execute_booking", execute_booking_node)
    graph.add_node("notify_confirmed", notify_confirmed_node)
    graph.add_node("escalate", escalate_node)

    graph.set_entry_point("compute_delta")
    graph.add_edge("compute_delta", "coverage_check")
    graph.add_conditional_edges("coverage_check", route_after_coverage,
                                 {"policy_check": "policy_check", "escalate": "escalate"})
    graph.add_conditional_edges("policy_check", route_after_policy,
                                 {"execute_booking": "execute_booking",
                                  "await_approval": "await_approval", "escalate": "escalate"})
    graph.add_edge("await_approval", "execute_booking")
    graph.add_conditional_edges("execute_booking", route_after_execution,
                                 {"notify_confirmed": "notify_confirmed", "escalate": "escalate"})
    graph.add_edge("notify_confirmed", END)
    graph.add_edge("escalate", END)
    return graph


def _build_checkpointer():
    """Postgres for real deployments (per §2's tech stack); in-memory for the
    offline demo. FLAGGED ASSUMPTION: falls back to MemorySaver, which does
    NOT survive a process restart — fine for a demo, not for the "member
    replies 40 minutes later from a different server instance" case §3a
    calls out as the whole reason this is a graph and not a function."""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        return AsyncPostgresSaver.from_conn_string(database_url)
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


class HotelReschedulingTool:
    """The ONLY public entry point this module exposes. Owns
    compute_hotel_delta, the graph/interrupt() wiring, and the
    search->rank->gate->book->cancel-old sequence as one controlled path."""

    def __init__(self) -> None:
        self._checkpointer = _build_checkpointer()
        self._graph = _build_graph().compile(checkpointer=self._checkpointer)

    async def on_itinerary_updated(self, event: dict) -> dict:
        thread_id = f"hotel:{event['itinerary_id']}:{event['event_id']}"

        print("\n" + "=" * 70)
        print("AMEX HOTEL RESCHEDULING WORKFLOW")
        print("=" * 70)

        print(f"Itinerary ID : {event['itinerary_id']}")
        print(f"Event ID     : {event['event_id']}")
        print(f"Member ID    : {event['member_id']}")

        print("\n[1/6] Loading member profile...")

        result = await self._graph.ainvoke(
            {
                "itinerary_id": event["itinerary_id"],
                "event_id": event["event_id"],
                "itinerary": event["itinerary"],
                "member": await get_member(event["member_id"]),
            },
            config={"configurable": {"thread_id": thread_id}},
        )

        print("\nWorkflow Finished")
        print("=" * 70)

        return result
    async def on_coverage_verification_response(self, event: dict):
        result = await self._graph.ainvoke(
            Command(resume=event["response"]),
            config={"configurable": {"thread_id": event["thread_id"]}},
        )

        return result

    async def on_approval_response(self, event: dict):
        result = await self._graph.ainvoke(
            Command(resume=event["response"]),
            config={"configurable": {"thread_id": event["thread_id"]}},
        )

        return result
    async def on_member_override(self, event: dict) -> None:
        await on_member_override(event)