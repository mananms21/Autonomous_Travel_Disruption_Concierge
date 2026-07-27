"""
Mock channel senders — no network, no real Twilio/FCM/SendGrid account
needed. This is the safe judged-demo default, same philosophy as the hotel
module's mock/mock combination.

Each accepts an optional `force_fail` set (of channel names) so tests can
deterministically exercise the fallback chain in channel_policy.py without
relying on real random failure.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("notification.channels.mock")

# Test hook: channels named in here will report failure on their next send.
# Cleared automatically after being consumed once, so a test can force
# exactly one failure without affecting subsequent sends.
_force_fail_once: set[str] = set()


def force_fail_once(channel: str) -> None:
    _force_fail_once.add(channel)


def _consume_force_fail(channel: str) -> bool:
    if channel in _force_fail_once:
        _force_fail_once.discard(channel)
        return True
    return False


async def send_push_mock(member, title: str, body: str, data: dict) -> bool:
    if _consume_force_fail("push"):
        logger.info(f"[MOCK push] forced failure for member={member.member_id}")
        return False
    logger.info(f"[MOCK push] to member={member.member_id} title={title!r} body={body!r}")
    return True


async def send_sms_mock(member, body: str) -> bool:
    if _consume_force_fail("sms"):
        logger.info(f"[MOCK sms] forced failure for member={member.member_id}")
        return False
    logger.info(f"[MOCK sms] to phone={member.phone_number} body={body!r}")
    return True


async def send_email_mock(member, subject: str, html_body: str) -> bool:
    if _consume_force_fail("email"):
        logger.info(f"[MOCK email] forced failure for member={member.member_id}")
        return False
    logger.info(f"[MOCK email] to={member.email_address} subject={subject!r}")
    return True


async def send_emergency_contact_alert_mock(member, event) -> bool:
    if not member.emergency_contact_phone or not member.emergency_contact_consent:
        return False
    logger.info(f"[MOCK emergency_contact] to={member.emergency_contact_phone} "
                f"re: member={member.member_id}, no trip details included")
    return True


async def send_sms_raw_mock(phone_number: str, body: str) -> bool:
    logger.info(f"[MOCK sms_raw] to phone={phone_number} body={body!r}")
    return True
