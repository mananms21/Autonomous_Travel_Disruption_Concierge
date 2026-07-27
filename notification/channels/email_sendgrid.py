"""
§9c — real email via SendGrid. Only imported when
NOTIFICATION_EMAIL_BACKEND=sendgrid (see config.py). Lowest priority to
build per the doc — email is the urgency=low / trip-summary channel, not
the live-disruption path.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("notification.channels.sendgrid")


async def send_email_sendgrid(member, subject: str, html_body: str) -> bool:
    if not member.email_address:
        return False
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    message = Mail(from_email=os.environ["FROM_EMAIL"], to_emails=member.email_address,
                    subject=subject, html_content=html_body)
    try:
        response = await asyncio.to_thread(
            SendGridAPIClient(os.environ["SENDGRID_API_KEY"]).send, message)
        return response.status_code in (200, 202)
    except Exception as e:
        logger.warning(f"SendGrid send failed for member={member.member_id}: {e}")
        return False
