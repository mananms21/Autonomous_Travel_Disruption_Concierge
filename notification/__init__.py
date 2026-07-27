"""
Notification module — the single human-facing gateway for the entire
concierge system. `app` (FastAPI) is the service entry point; other
modules interact with it via POST /notify and, for two-way flows, by
implementing a small listener for whatever callback_event_type they
register when requesting a response.
"""
from .app import app
from .models import ChangeDiff, NotificationRequest, PendingResponse

__all__ = ["app", "ChangeDiff", "NotificationRequest", "PendingResponse"]
