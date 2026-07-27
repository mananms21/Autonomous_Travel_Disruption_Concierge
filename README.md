# Hotel Rescheduling Module

Implements the full architecture doc: `HotelReschedulingTool()` is the
**only public entry point** — a LangGraph state machine that reacts to
`ItineraryUpdated` events, diffs required vs. booked hotel nights, checks
airline coverage, runs a deterministic policy/budget gate, and executes
search → rank → book → cancel-old as one controlled sequence.

## Why one tool, not several

`search_hotels`, `get_hotel_option`, `book_hotel`, `cancel_hotel_booking` in
`tool.py` are **plain async functions**, not separate LangChain `@tool`s.
An agent that could call them independently could skip `rank()`, skip
`evaluate_hotel_policy()`, or break the idempotency-key derivation that
`execute_booking_transaction` depends on. The only real tools in this
module are the three coverage-confidence enrichment functions in
`coverage_assessment.py` — that's the one place "let the model decide,
after gathering signals" is actually correct.

## Layout

```
hotel_rescheduling/
├── tool.py                  # HotelReschedulingTool() — the only public entry point
├── ranking.py                # rank(), rank_top_n() — deterministic, no LLM
├── policy.py                 # evaluate_hotel_policy() — deterministic, no LLM
├── coverage_assessment.py    # the ONE LLM call + its 3 enrichment tools
├── delta.py                  # compute_hotel_delta(), overnight-gap detection
├── models.py                 # HotelOption, BookingResult, PolicyDecision, ...
├── storage.py                 # in-memory store backing §4's SQL schema (see below)
├── airports.py                # seeded airport/timezone lookup
├── events.py                  # cross-module event stubs (notification, member profile)
├── config.py                  # HOTEL_SEARCH_BACKEND / HOTEL_BOOKING_BACKEND wiring
└── providers/
    ├── __init__.py            # HotelSearchProvider, HotelBookingProvider, HotelProviderFacade
    ├── search_scrappa.py       # real API: Google Hotels via Scrappa
    ├── search_mock.py          # no network, no quota burned
    ├── booking_mock.py         # fully mocked booking, injected failure rate
    └── mock_hotels.json
```

## Running it

```bash
pip install -r requirements.txt
```

```python
from hotel_rescheduling.tool import HotelReschedulingTool

tool = HotelReschedulingTool()
await tool.on_itinerary_updated({
    "itinerary_id": "itin-1", "event_id": "evt-1",
    "itinerary": {...}, "member_id": "member-1",
})
```

By default both search and booking are mocked — `HOTEL_SEARCH_BACKEND=mock`,
`HOTEL_BOOKING_BACKEND=mock` — fully deterministic and offline, no quota
burned. This is the combination to run for the actual judged demo; sandbox
rate limits and offer-expiry windows on a live vendor make real calls a risk
mid-presentation.

To use the real Scrappa search backend:

```bash
export HOTEL_SEARCH_BACKEND=scrappa
export SCRAPPA_API_KEY=your_key_here
```

## Two things to verify with your first real Scrappa call

1. **Response field schema.** Scrappa's own docs show `{"properties": []}`
   as the example response — no field-level schema for what's inside each
   entry. `search_scrappa.py` assumes it mirrors SerpApi's Google Hotels
   schema (`name`, `overall_rating`, `rate_per_night.extracted_lowest`,
   `total_rate.extracted_lowest`, `gps_coordinates`, `property_token`,
   `free_cancellation`) — that's the one assumption baked into
   `_parse_property()`, called out loudly in a comment there. Every field
   read is `.get()`-based specifically so a schema mismatch degrades one
   hotel's missing field to `None` instead of throwing and killing the
   whole search — but confirm the actual shape before demo day.
2. **Credit usage.** 500 free credits/month, 1 credit per search request.
   `ScrappaHotelSearchProvider` caches location resolution in memory per
   city to avoid re-burning a credit on the autocomplete step for repeat
   searches of the same city within one process lifetime.

## Other explicit assumptions (state these, don't hide them)

- **`storage.py` is in-memory, not the Postgres schema from §4.** The doc
  hands over the SQL schema but not connection code — that's genuine infra
  work. This ships an in-memory implementation of the exact same async
  function signatures §5's code calls (`insert_hotel_booking`,
  `get_card_tier`, `count_claims_this_year`, etc.), seeded with the doc's
  tier config. Swapping to real Postgres means replacing this one module's
  internals; nothing in `policy.py`/`ranking.py`/`tool.py` needs to change.
  **This resets on process restart** — fine for a demo, not fine for
  production, where idempotency and the claims ledger must survive one.
- **The LangGraph checkpointer falls back to `MemorySaver`** when
  `DATABASE_URL` isn't set, for the same offline-demo reason. Set
  `DATABASE_URL` to get real `AsyncPostgresSaver` persistence — required for
  the "member replies 40 minutes later from a different server instance"
  case §3a is built around; `MemorySaver` does not survive a restart.
- **`events.py` is a stub** for the notification / itinerary-state / card
  member-interface modules this one talks to across the event contract in
  §1. The call signatures (`emit_notification_event`, `get_member`,
  `emit_event`) are the actual contract those modules need to satisfy —
  swap the internals for real message-bus/HTTP calls once they exist.
- **`airports.py` is a tiny seeded subset**, not the full OpenFlights
  dataset — swap in the real dataset for anything beyond the 5 airports
  seeded here.
- Single-passenger occupancy is the default (per §8); `occupants` exists on
  every relevant model but multi-passenger UX wasn't built out.
- Booking stays fully mocked regardless of search backend — no live vendor
  with a sandbox booking endpoint was wired up (§7's mocking strategy).

## Verified working (see test output during development)

- Full graph run end-to-end: `compute_delta` → `coverage_check` →
  `policy_check` → `execute_booking` → `notify_confirmed`/`escalate`, with
  no LLM call on the path where the carrier isn't in the DOT commitments
  table (deterministic `CONFIRMED_NOT_COVERED`).
- The §5.6 score gate: a low-scoring candidate set correctly routes to
  `HotelActionRequired` (`MEMBER_CHOICE_NEEDED`) carrying the actual top-3
  ranked `HotelOption`s from `rank_top_n()`, rather than being silently
  discarded or treated as an opaque failure.
- The quality floor (`MIN_ACCEPTABLE_STAR_RATING`): a sub-2.0-star mock
  hotel is excluded from the candidate pool before scoring, not just
  down-ranked.

## Not yet exercised against a live dependency

- Real Scrappa API calls (schema assumption above, unverified against a
  live response).
- Postgres-backed checkpointer / storage (both default to in-memory).
- The `interrupt()`/`Command(resume=...)` pause-and-resume path itself —
  the smoke tests run only through branches where coverage and policy
  resolve without needing a human response.
