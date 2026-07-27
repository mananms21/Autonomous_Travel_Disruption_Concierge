"""
Streamlit Card Member Interface — presentation only.

Talks to ONE backend: the Supervisor (supervisor.py). Never imports or calls
HotelReschedulingTool / NotificationTool directly — same "UI is a thin
reactor" discipline as the original Flutter design, just swapped to Streamlit
because of time. Run:  streamlit run streamlit_app.py
Requires supervisor.py running separately, e.g.:  uvicorn supervisor:app --port 8000
"""
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st

SUPERVISOR_URL = os.getenv("SUPERVISOR_URL", "http://127.0.0.1:8000")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def build_flight_delay_itinerary() -> dict:
    """Single leg, origin-city overnight stay. The 20h delay is a deliberately
    generous margin — anything shorter risks NOT crossing a local-midnight
    boundary depending on what time of day you happen to click the button,
    and delta.py's is_overnight_gap needs it to."""
    now = datetime.now(tz=ZoneInfo("UTC"))
    on_time_departure = now + timedelta(hours=3)
    delayed_departure = now + timedelta(hours=23)
    arrival = delayed_departure + timedelta(hours=15, minutes=30)
    leg = {"departure_airport": "BOM", "arrival_airport": "JFK",
           "departure_time": _iso(delayed_departure), "arrival_time": _iso(arrival)}
    original_leg = {**leg, "departure_time": _iso(on_time_departure),
                     "arrival_time": _iso(on_time_departure + timedelta(hours=15, minutes=30))}
    delay_hours = (delayed_departure - on_time_departure).total_seconds() / 3600
    return {"origin_city": "Mumbai", "origin_tz": "Asia/Kolkata",
             "passenger_ids": ["member-001"], "legs": [leg], "original_legs": [original_leg],
             "delay_hours": round(delay_hours, 1)}


def build_cancellation_itinerary() -> dict:
    """Same code path as flight delay in delta.py — there's no separate
    CANCELLED branch in compute_required_timeline, it only ever looks at the
    resulting schedule gap. Framed differently for the demo narrative
    (next available flight is the FOLLOWING day, not just delayed same-day)."""
    now = datetime.now(tz=ZoneInfo("UTC"))
    on_time_departure = now + timedelta(hours=3)
    rebooked_departure = now + timedelta(hours=30)
    arrival = rebooked_departure + timedelta(hours=15, minutes=30)
    leg = {"departure_airport": "BOM", "arrival_airport": "JFK",
           "departure_time": _iso(rebooked_departure), "arrival_time": _iso(arrival)}
    original_leg = {**leg, "departure_time": _iso(on_time_departure),
                     "arrival_time": _iso(on_time_departure + timedelta(hours=15, minutes=30))}
    delay_hours = (rebooked_departure - on_time_departure).total_seconds() / 3600
    return {"origin_city": "Mumbai", "origin_tz": "Asia/Kolkata",
             "passenger_ids": ["member-001"], "legs": [leg], "original_legs": [original_leg],
             "delay_hours": round(delay_hours, 1)}


def build_missed_connection_itinerary() -> dict:
    """Two legs, ~20h layover at the connecting airport — generous for the
    same crossing-midnight reason as above. UNVERIFIED ASSUMPTION: this
    depends on hotel_rescheduling/airports.py's get_airport_info() actually
    having an entry for 'DEL'. If it doesn't, this specific button will 500
    on a KeyError/lookup miss inside compute_required_timeline — check your
    airports seed data first, or swap DEL for a code you know is in it.
    delay_hours here is a proxy (the layover gap itself) since there's no
    'original vs. new' schedule modeled for this scenario the way the other
    two have — flagged as an assumption, not a verified value."""
    now = datetime.now(tz=ZoneInfo("UTC"))
    dep1 = now + timedelta(hours=2)
    arr1 = dep1 + timedelta(hours=2, minutes=30)
    dep2 = arr1 + timedelta(hours=20)
    arr2 = dep2 + timedelta(hours=15)
    return {"origin_city": "Mumbai", "origin_tz": "Asia/Kolkata",
             "passenger_ids": ["member-001"],
             "legs": [
                 {"departure_airport": "BOM", "arrival_airport": "DEL",
                  "departure_time": _iso(dep1), "arrival_time": _iso(arr1)},
                 {"departure_airport": "DEL", "arrival_airport": "JFK",
                  "departure_time": _iso(dep2), "arrival_time": _iso(arr2)},
             ],
             "delay_hours": 20.0}


st.set_page_config(page_title="AMEX Autonomous Travel Concierge", layout="wide")
st.title("AMEX Autonomous Travel Concierge")

if "itinerary_id" not in st.session_state:
    st.session_state.itinerary_id = "demo-itin-001"
if "last_thread_id" not in st.session_state:
    st.session_state.last_thread_id = None


def _post(path: str, payload: dict) -> dict | None:
    try:
        resp = requests.post(f"{SUPERVISOR_URL}{path}", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.error(f"Supervisor call failed: {e}")
        return None


def _get_status(itinerary_id: str) -> dict | None:
    try:
        resp = requests.get(f"{SUPERVISOR_URL}/status/{itinerary_id}", timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.error(f"Supervisor call failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Workflow status strip — Supervisor / Hotel / Notification health
# ---------------------------------------------------------------------------
health_cols = st.columns(3)
try:
    health = requests.get(f"{SUPERVISOR_URL}/health", timeout=5).json()
    health_cols[0].metric("Supervisor", "Up")
    health_cols[1].metric("Hotel Tool", "Up" if health["hotel_tool"] else "Down")
    health_cols[2].metric("Notification Tool", "Up" if health["notification_tool"] else "Down")
except requests.RequestException:
    health_cols[0].metric("Supervisor", "Unreachable")

st.divider()

# ---------------------------------------------------------------------------
# Current Trip / trigger a demo disruption
# ---------------------------------------------------------------------------
st.subheader("Current Trip")
col1, col2 = st.columns(2)
with col1:
    st.text("Flight Status")
    st.caption("Flight tool not built yet — placeholder")
with col2:
    st.text("Hotel Status")
    status = _get_status(st.session_state.itinerary_id)
    if status is None:
        st.caption("No disruption triggered yet for this itinerary")
    else:
        state = status["state"]
        if state is None:
            # on_itinerary_updated/on_*_response returned None — almost
            # certainly means the ainvoke-return fix hasn't been applied to
            # hotel_rescheduling/tool.py yet (it's still discarding the
            # graph's result instead of returning it). Not a Streamlit bug.
            st.warning("Supervisor recorded no state for this run — check that "
                        "hotel_rescheduling/tool.py's on_* methods RETURN "
                        "ainvoke's result rather than discarding it.")
        elif "__interrupt__" in state:
            st.warning("Awaiting member response")
        elif "execution_result" in state:
            execution_result = state["execution_result"]
            if execution_result.get("success"):
                st.success(f"Booked — {execution_result.get('hotel_name', 'hotel confirmed')}")
            else:
                st.error(f"Not booked — {execution_result.get('reason', 'unknown')}")
        elif state.get("policy_decision", {}).get("status") == "DENIED":
            # a real, correct outcome — the policy check ran and said no.
            # Not an error, not "nothing happened" — the system is working.
            st.info(f"Policy denied hotel action — {state['policy_decision'].get('reason', 'no reason given')}")
        elif state.get("policy_decision", {}).get("status") == "NEEDS_APPROVAL":
            st.warning("Needs member approval — check for a pending coverage/approval request")
        elif not state.get("delta"):
            st.info("No hotel action was needed for this itinerary (delta came back empty).")
        else:
            st.caption(f"In progress — coverage: {state.get('coverage_status')}, "
                        f"policy: {state.get('policy_decision')}")

with st.expander("Trigger a demo disruption (real system fires this automatically — this is demo-only)"):
    member_id = st.text_input("Member ID", value="member-001")
    scenario_builders = {
        "Trigger Flight Delay": build_flight_delay_itinerary,
        "Trigger Cancellation": build_cancellation_itinerary,
        "Trigger Missed Connection": build_missed_connection_itinerary,
    }
    cols = st.columns(len(scenario_builders))
    for col, (label, builder) in zip(cols, scenario_builders.items()):
        if col.button(label):
            event_id = f"evt-{int(time.time())}"
            result = _post("/disruption", {
                "itinerary_id": st.session_state.itinerary_id, "event_id": event_id,
                "itinerary": builder(), "member_id": member_id,
            })
            if result:
                st.session_state.last_thread_id = f"hotel:{st.session_state.itinerary_id}:{event_id}"
                st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# AI Recommendation
# ---------------------------------------------------------------------------
st.subheader("AI Recommendation")
if status and status["state"] and "execution_result" in status["state"]:
    er = status["state"]["execution_result"]
    if er.get("success"):
        st.write(f"**Hotel:** {er.get('hotel_name', '—')}")
        st.write(f"**Booking ID:** {er.get('booking_id', '—')}")
    elif er.get("reason") == "NEEDS_MEMBER_CHOICE":
        st.write("No option scored confidently enough to auto-book. Choose one:")
        for opt in er.get("candidate_options", []):
            if st.button(f"Choose {opt.get('hotel_name', 'option')}", key=opt.get("hotel_id")):
                _post("/respond/override", {
                    "itinerary_id": st.session_state.itinerary_id, "domain": "HOTEL",
                    "preferred_hotel_id": opt.get("hotel_id"),
                    "guest_info": {"first_name": "Member", "last_name": "", "email": ""},
                })
                st.rerun()
    else:
        st.write(f"No booking made — {er.get('reason', 'unknown reason')}")
else:
    st.caption("Nothing to show yet")

st.divider()

# ---------------------------------------------------------------------------
# Member Decision — approve / deny / respond to coverage verification
# ---------------------------------------------------------------------------
st.subheader("Member Decision")
if st.session_state.last_thread_id:
    st.caption(f"Active thread: {st.session_state.last_thread_id}")
    dcol1, dcol2, dcol3 = st.columns(3)
    if dcol1.button("Approve"):
        _post("/respond/approval", {"thread_id": st.session_state.last_thread_id, "response": True})
        st.rerun()
    if dcol2.button("Deny"):
        _post("/respond/approval", {"thread_id": st.session_state.last_thread_id, "response": False})
        st.rerun()
    if dcol3.button("Confirm coverage (yes, airline is covering this)"):
        _post("/respond/coverage", {"thread_id": st.session_state.last_thread_id, "response": "CONFIRMED_COVERED"})
        st.rerun()
else:
    st.caption("No pending decision")

st.divider()

# ---------------------------------------------------------------------------
# Notification — manual test send, since this module has no LLM/decision
# logic of its own to visualize beyond "did the message go out"
# ---------------------------------------------------------------------------
st.subheader("Notification")
with st.expander("Send a test notification"):
    message = st.text_area("Message", value="Your hotel has been confirmed.")
    if st.button("Send"):
        result = _post("/notify/test", {"member_id": member_id, "message": message})
        if result:
            st.success("Sent")
