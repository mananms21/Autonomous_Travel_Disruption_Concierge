# Notification Service

The single human-facing gateway for the entire concierge system. Every
other module (monitoring, policy engine, flight rebooking, hotel
rescheduling, itinerary state, escalation, card member interface) talks to
the member exclusively through this one service — see the architecture doc
for the full reasoning.

## Layout

```
notification/
├── app.py              # FastAPI app: POST /notify, Twilio webhook, WebSocket, approve/deny
├── worker.py            # process_notification, idempotency, one-open-question conflict handling,
│                         # NotificationFailed emission, timeout-expiry job
├── channel_policy.py     # CHANNEL_POLICY, select_channels, send_with_fallback, emergency-contact tier
├── summary.py             # LLM summary generation + fact guardrail + template fallback
├── reply.py                # SMS reply classification (yes/no/unclear) + routing back to origin module
├── events.py                # emit_event — routes a resolved reply back to origin_module's own listener
├── realtime.py                # WebSocket channel for the card member interface's live view
├── models.py                   # ChangeDiff, NotificationRequest, PendingResponse, ...
├── storage.py                   # in-memory store backing the SQL schema (see below)
├── config.py                     # per-channel backend selection (mock vs real)
└── channels/
    ├── mock_channels.py          # offline, judged-demo default — supports injectable failure for testing
    ├── push_fcm.py                # real Firebase Cloud Messaging
    ├── sms_twilio.py               # real Twilio (outbound + inbound webhook helper)
    └── email_sendgrid.py           # real SendGrid
```

## Running it

```bash
pip install -r requirements.txt
uvicorn notification.app:app --reload
```

By default every channel is mocked (`NOTIFICATION_PUSH_BACKEND=mock`,
`NOTIFICATION_SMS_BACKEND=mock`, `NOTIFICATION_EMAIL_BACKEND=mock`,
`NOTIFICATION_LLM_BACKEND=mock`) — fully deterministic and offline. This is
the combination to run for the judged demo; switch individual channels to
real backends via env vars once you're ready:

```bash
export NOTIFICATION_PUSH_BACKEND=fcm
export FCM_SERVICE_ACCOUNT_PATH=/path/to/service-account.json

export NOTIFICATION_SMS_BACKEND=twilio
export TWILIO_SID=... TWILIO_AUTH_TOKEN=... TWILIO_FROM_NUMBER=...

export NOTIFICATION_EMAIL_BACKEND=sendgrid
export SENDGRID_API_KEY=... FROM_EMAIL=...

export NOTIFICATION_LLM_BACKEND=anthropic
export ANTHROPIC_API_KEY=...
```

Any subset can be real while the rest stay mocked — each is an independent
switch, same philosophy as the hotel module's provider config.

## How another module talks to this one

```python
import httpx
await httpx.AsyncClient().post("http://notification-service/notify", json={
    "event_id": "evt-123", "event_type": "HotelBookingConfirmed",
    "itinerary_id": "itin-1", "member_id": "mem-1", "origin_module": "HOTEL",
    "change_diff": {"domain": "HOTEL", "action": "ADD_NIGHT", "reason": "flight delayed 18h",
                     "member_facing_detail": {"hotel_name": "Grand Central Hotel", "nights": 1}},
})
```

For a two-way flow (needs a yes/no back), add `requires_response: true`,
`origin_thread_id`, and `callback_event_type` — and implement a small
listener in your own module at `/events/<kebab-case-callback-event-type>`
that turns the resolved reply into whatever your module actually needs
(e.g. the hotel module's `hotel_graph.ainvoke(Command(resume=...))`).

## Explicit assumptions (state these, don't hide them)

- **`storage.py` is in-memory**, resetting on restart — same demo
  philosophy as the hotel module. The `pending_responses(member_id) WHERE
  resolved=false` uniqueness and `notifications.idempotency_key`
  uniqueness are both enforced in code here exactly the way a real
  Postgres unique constraint/partial index would; swapping to Postgres
  means replacing this one file's internals, nothing else.
- **`events.py`'s `ORIGIN_MODULE_BASE_URLS` is a hardcoded dev-convenience
  dict**, not real service discovery. Point the env vars
  (`HOTEL_MODULE_URL`, `FLIGHT_MODULE_URL`, `POLICY_ENGINE_URL`) at
  wherever those services actually run.
- **The worker runs as an in-process asyncio task** fed by an
  `asyncio.Queue`, not a separate process + Redis `blpop` — swap
  `worker.notification_worker`'s queue argument for a real Redis-backed
  loop when moving beyond a single-process demo; `process_notification`
  itself doesn't change.
- **The "one open question per member" rule is a deliberate UX
  tradeoff** — a second `requires_response` notification for a member who
  already has one open gets queued (`QUEUED_BEHIND_PENDING`) rather than
  guessing which question a reply answers. It's automatically retried the
  moment the blocking one resolves (approve/deny/SMS reply, or a timeout).

## Verified working (see test output during development)

- Normal send (mock push, `generated_by=LLM`), full idempotency replay
  (second call with the same `event_id` is a true no-op), and the
  fallback chain (forced push failure correctly falls to SMS on a
  `high`/`critical` urgency event).
- The fact guardrail: a summary missing a required fact from
  `member_facing_detail` correctly fails and falls back to the template.
- Reply classification across ambiguous phrasings ("kinda", "not sure
  yet", "they said maybe") — all correctly resolve to `"unclear"` rather
  than a guessed direction.
- **The one-open-question conflict + retry cycle, end to end**: a second
  `requires_response` request for a member with an open question
  correctly queues instead of colliding; once the first resolves (via the
  actual SMS-reply code path), the queued one is retried and correctly
  transitions from `QUEUED_BEHIND_PENDING` to `DELIVERED` — this required
  a real fix during testing (the naive retry re-ran the whole send
  pipeline and got silently no-op'd by its own idempotency check).
- **Emergency contact tier**: correctly gated on explicit consent (a
  member with a phone number on file but `consent=False` gets nothing
  sent, even after every other channel fails); with consent, the generic
  no-trip-details message goes out.
- **The timeout-expiry job**: a `requires_response` notification that
  never gets answered correctly resolves on its own after `expires_at`
  passes, freeing the member's one-open-question slot and emitting a
  `TIMEOUT` response back to the origin module — this is what prevents a
  paused origin-module run (e.g. the hotel module's `interrupt()`) from
  hanging forever.

## Not yet exercised against a live dependency

- Real Anthropic (Haiku) calls — `generate_summary`/`classify_reply`'s
  `LLM_BACKEND=` path is unverified against the real API (no
  network access in this build/test environment).
- Real FCM/Twilio/SendGrid calls — same reason. `NOTIFICATION_LLM_BACKEND`,
  push, SMS, and email backends were all tested via their mock
  implementations, which exercise the surrounding logic (fallback,
  idempotency, guardrail) but not the actual vendor integration code.
- The real Twilio inbound webhook (`/webhooks/twilio/sms`) — tested via
  `handle_sms_reply()` called directly, not via an actual HTTP POST through
  FastAPI (this build environment doesn't have FastAPI installed to run a
  real ASGI server).
- `emit_event`'s HTTP callback to another module's listener — tested via
  a real (failing, since nothing is listening) network call, confirming
  the failure path logs loudly rather than silently swallowing it, but the
  success path (a real HOTEL/FLIGHT_REBOOKING service actually receiving
  and acting on the callback) needs those other services running to verify.
