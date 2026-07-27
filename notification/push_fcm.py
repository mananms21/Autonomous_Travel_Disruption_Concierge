"""
§9a — real push notifications via Firebase Cloud Messaging. Only imported
when NOTIFICATION_PUSH_BACKEND=fcm (see config.py) — firebase_admin isn't
a dependency of the mock demo path at all.

Setup: Firebase project -> download service account JSON -> set
FCM_SERVICE_ACCOUNT_PATH. Mobile app registers for a token on install and
posts it to your API, stored in member_notification_prefs.push_token.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("notification.channels.fcm")

_initialized = False


def _ensure_initialized() -> None:
    global _initialized
    if _initialized:
        return
    import firebase_admin
    from firebase_admin import credentials
    path = os.environ["FCM_SERVICE_ACCOUNT_PATH"]
    firebase_admin.initialize_app(credentials.Certificate(path))
    _initialized = True


async def send_push_fcm(member, title: str, body: str, data: dict) -> bool:
    from firebase_admin import messaging
    _ensure_initialized()
    if not member.push_token:
        return False
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in data.items()},  # FCM data payload must be str:str
        token=member.push_token,
        android=messaging.AndroidConfig(priority="high"),
        apns=messaging.APNSConfig(headers={"apns-priority": "10"}),
    )
    try:
        await asyncio.to_thread(messaging.send, message)
        return True
    except messaging.UnregisteredError:
        from .. import storage
        await storage.db.invalidate_push_token(member.member_id)  # expired/uninstalled — don't retry
        return False
    except Exception as e:
        logger.warning(f"FCM send failed for member={member.member_id}: {e}")
        return False
