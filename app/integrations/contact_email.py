"""Postmark delivery for contact submissions; failure leaves a retriable inbox item."""

import os

import httpx


def deliver(submission, site_config):
    token = os.getenv("POSTMARK_SERVER_TOKEN", "") or os.getenv("POSTMARK_API_TOKEN", "")
    sender = os.getenv("FASTSHOP_CONTACT_FROM", "") or os.getenv("FROM_EMAIL", "")
    recipient = site_config.get("email", "")
    if not token or not sender or not recipient:
        return "awaiting_configuration"
    try:
        result = httpx.post("https://api.postmarkapp.com/email", headers={"X-Postmark-Server-Token": token},
                            json={"From": sender, "To": recipient, "ReplyTo": submission.email,
                                  "Subject": f"Website message from {submission.name}",
                                  "TextBody": f"Name: {submission.name}\nEmail: {submission.email}\n\n{submission.message}",
                                  "MessageStream": "outbound"}, timeout=15)
        result.raise_for_status()
        return "sent" if result.json().get("ErrorCode") == 0 else "failed"
    except (httpx.HTTPError, ValueError):
        return "failed"
