"""Notifications for events worth telling a human about immediately.

Currently just: a new quality-issue report, emailed via Resend
(https://resend.com) once configured. Until NOTIFY_EMAIL_PROVIDER is set,
this only logs, so the feedback flow works end to end (persist + admin
review) without depending on email being configured.

Resend note: without a verified sending domain, the default "onboarding@
resend.dev" sender can only deliver to the email address the Resend account
itself was signed up with -- if NOTIFY_TO_EMAIL doesn't match that, delivery
will fail even with a valid API key.
"""
from __future__ import annotations

import html
import os

import requests

NOTIFY_TO_EMAIL = os.environ.get("NOTIFY_TO_EMAIL", "huy.trinhx@gmail.com")
RESEND_API_URL = "https://api.resend.com/emails"


def notify_feedback_submitted(*, feedback_id: str, initial_message: str, user_expectation: str) -> None:
    provider = os.environ.get("NOTIFY_EMAIL_PROVIDER")
    if not provider:
        print(
            f"[notify] no NOTIFY_EMAIL_PROVIDER configured -- would have emailed "
            f"{NOTIFY_TO_EMAIL} about feedback {feedback_id} on {initial_message!r}"
        )
        return

    if provider == "resend":
        _send_via_resend(
            feedback_id=feedback_id, initial_message=initial_message, user_expectation=user_expectation
        )
        return

    raise NotImplementedError(f"unknown NOTIFY_EMAIL_PROVIDER {provider!r}")


def _send_via_resend(*, feedback_id: str, initial_message: str, user_expectation: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not set")

    from_email = os.environ.get("NOTIFY_FROM_EMAIL", "onboarding@resend.dev")

    # User-supplied text (initial_message, user_expectation) is escaped before
    # going into the HTML body -- email clients don't execute embedded
    # scripts, but there's no reason to trust it unescaped either.
    body_html = (
        "<p>A new quality issue was reported on FHIR Bud.</p>"
        f"<p><strong>Use case:</strong> {html.escape(initial_message)}</p>"
        f"<p><strong>Expected instead:</strong> {html.escape(user_expectation)}</p>"
        f"<p><strong>Report id:</strong> {html.escape(feedback_id)}</p>"
        '<p>Open "Quality Reports" in the app to see the full conversation log.</p>'
    )

    response = requests.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "from": from_email,
            "to": [NOTIFY_TO_EMAIL],
            "subject": f"FHIR Bud: quality issue reported ({initial_message[:60]})",
            "html": body_html,
        },
        timeout=10,
    )
    response.raise_for_status()
