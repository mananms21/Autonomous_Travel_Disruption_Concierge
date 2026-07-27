"""
§9b — real SMS via Twilio, outbound send + the inbound webhook handler.
Only imported when NOTIFICATION_SMS_BACKEND=twilio (see config.py).

Setup: Twilio trial account -> free number, good enough for a demo (trial
numbers prepend "Sent from your Twilio trial account" — fine for judges).
Point the number's webhook at /webhooks/twilio/sms on your deployed app;
for local dev, ngrok tunnels your FastAPI server so Twilio can reach it.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("notification.channels.twilio")

_client = None


def _get_client():
    global _client
    if _client is None:
        from twilio.rest import Client
        _client = Client(os.environ["TWILIO_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    return _client


async def send_sms_twilio(member, body: str) -> bool:
    if not member.phone_number:
        return False
    try:
        client = _get_client()
        msg = await asyncio.to_thread(
            client.messages.create, to=member.phone_number,
            from_=os.environ["TWILIO_FROM_NUMBER"], body=body)
        return msg.status not in ("failed", "undelivered")
    except Exception as e:
        logger.warning(f"Twilio send failed for member={member.member_id}: {e}")
        return False


async def send_sms_raw_twilio(phone_number: str, body: str) -> bool:
    """Used by the emergency-contact tier — sends to a phone number that
    isn't a member_id-keyed record at all."""
    try:
        client = _get_client()
        msg = await asyncio.to_thread(
            client.messages.create, to=phone_number,
            from_=os.environ["TWILIO_FROM_NUMBER"], body=body)
        return msg.status not in ("failed", "undelivered")
    except Exception as e:
        logger.warning(f"Twilio raw send failed to {phone_number!r}: {e}")
        return False
