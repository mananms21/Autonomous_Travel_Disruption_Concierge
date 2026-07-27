"""
Backend selection — one env var per channel, mirroring the hotel module's
config.py pattern. Mock is the default and the safe judged-demo path;
switching any channel to its real backend is a config change, never a code
change in worker.py / channel_policy.py.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load notification/.env
load_dotenv(Path(__file__).resolve().parent / ".env")


NOTIFICATION_PUSH_BACKEND = os.getenv("NOTIFICATION_PUSH_BACKEND", "mock")     # "mock" | "fcm"
NOTIFICATION_SMS_BACKEND = os.getenv("NOTIFICATION_SMS_BACKEND", "mock")       # "mock" | "twilio"
NOTIFICATION_EMAIL_BACKEND = os.getenv("NOTIFICATION_EMAIL_BACKEND", "mock")   # "mock" | "sendgrid"
LLM_BACKEND = os.getenv("NOTIFICATION_LLM_BACKEND", "mock")                    # "mock" | "anthropic"

CARD_BRAND = os.getenv("CARD_BRAND", "Amex")


def get_send_push():
    if NOTIFICATION_PUSH_BACKEND == "fcm":
        from .channels.push_fcm import send_push_fcm
        return send_push_fcm
    from .channels.mock_channels import send_push_mock
    return send_push_mock


def get_send_sms():
    if NOTIFICATION_SMS_BACKEND == "twilio":
        from .channels.sms_twilio import send_sms_twilio
        return send_sms_twilio
    from .channels.mock_channels import send_sms_mock
    return send_sms_mock


def get_send_email():
    if NOTIFICATION_EMAIL_BACKEND == "sendgrid":
        from .channels.email_sendgrid import send_email_sendgrid
        return send_email_sendgrid
    from .channels.mock_channels import send_email_mock
    return send_email_mock


def get_send_sms_raw():
    """Used only by the emergency-contact tier — sends to a raw phone
    number rather than a member record."""
    if NOTIFICATION_SMS_BACKEND == "twilio":
        from .channels.sms_twilio import send_sms_raw_twilio
        return send_sms_raw_twilio
    from .channels.mock_channels import send_sms_raw_mock
    return send_sms_raw_mock
