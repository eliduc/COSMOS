"""Outbound notification mail to COSMOS access applicants (stdlib SMTP).

Sends the three admin-decision notifications of the access-request flow —
approved / denied / clarifying question — to the applicant's mailbox.
Every message is plain text with a bilingual body: Russian first, then an
English translation after a "---" separator. From is formatted as
"COSMOS <from-addr>" and Reply-To points at the configured sender so the
applicant's replies land in the admin's mailbox.

Config is read from the environment AT CALL TIME (same philosophy as
auth.py — no import-time snapshots; tests and operators may change the
env between calls):

    CREW_SMTP_HOST   SMTP server          (default "smtp.gmail.com")
    CREW_SMTP_PORT   SMTP port, STARTTLS  (default "587")
    CREW_SMTP_USER   SMTP login           (mailer disabled when empty)
    CREW_SMTP_PASS   SMTP password        (mailer disabled when empty)
    CREW_SMTP_FROM   From/Reply-To addr   (default = CREW_SMTP_USER)

House-rule exception, on purpose: unlike the rest of the webapp this
module does NOT fail loudly. Notification mail is best-effort and must
never break the calling flow (approving a user must succeed even when the
mailbox is down), so the send_* functions return bool — True sent, False
disabled-or-failed — and never raise; every failure prints exactly one
WARNING line to the server console.

_deliver() is the single transport point; tests monkeypatch it and never
touch the network.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

APP_URL = "https://cos.fregene.ai"


def _smtp_user() -> str:
    return os.environ.get("CREW_SMTP_USER", "").strip()


def _smtp_from() -> str:
    return os.environ.get("CREW_SMTP_FROM", "").strip() or _smtp_user()


def enabled() -> bool:
    """True when both CREW_SMTP_USER and CREW_SMTP_PASS are non-empty."""
    return bool(_smtp_user()) and bool(
        os.environ.get("CREW_SMTP_PASS", "").strip())


def _deliver(msg: EmailMessage) -> None:
    """Single transport point (tests monkeypatch THIS): SMTP + STARTTLS."""
    host = os.environ.get("CREW_SMTP_HOST", "").strip() or "smtp.gmail.com"
    port = int(os.environ.get("CREW_SMTP_PORT", "").strip() or "587")
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(_smtp_user(), os.environ.get("CREW_SMTP_PASS", "").strip())
        smtp.send_message(msg)


def _send(to_email: str, subject: str, ru_body: str, en_body: str) -> bool:
    """Build and deliver one bilingual notification; bool, never raises."""
    try:
        if not enabled():
            return False
        from_addr = _smtp_from()
        msg = EmailMessage()
        msg["From"] = f"COSMOS <{from_addr}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Reply-To"] = from_addr
        msg.set_content(f"{ru_body}\n\n---\n\n{en_body}")
        _deliver(msg)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort by design (see
        # module docstring): notification mail must not break the caller.
        print(f"WARNING: mailer: could not send {subject!r} to {to_email}:"
              f" {exc}", flush=True)
        return False


# ------------------------------------------------------------- public API

def send_approved(to_email: str) -> bool:
    """Notify the applicant that the admin approved their access."""
    return _send(
        to_email,
        "COSMOS: доступ одобрен / access approved",
        ("Здравствуйте!\n\n"
         "Ваш запрос на доступ к COSMOS (конвейер планирования экипажей, "
         f"{APP_URL}) одобрен администратором.\n\n"
         f"Войдите по адресу {APP_URL} — введите этот email, получите "
         "одноразовый код и подтвердите вход. Доступ активен уже сейчас."
         "\n\n— COSMOS"),
        ("Hello!\n\n"
         "Your request for access to COSMOS (the crew-scheduling pipeline "
         f"at {APP_URL}) has been approved by the administrator.\n\n"
         f"Sign in at {APP_URL} — enter this email address, receive a "
         "one-time code and confirm. Your access is already active."
         "\n\n— COSMOS"))


def send_denied(to_email: str) -> bool:
    """Notify the applicant that the admin denied their access request."""
    return _send(
        to_email,
        "COSMOS: в доступе отказано / access denied",
        ("Здравствуйте!\n\n"
         "К сожалению, ваш запрос на доступ к COSMOS "
         f"({APP_URL}) отклонён администратором.\n\n"
         "Если вы считаете это ошибкой, ответьте на это письмо."
         "\n\n— COSMOS"),
        ("Hello!\n\n"
         "Unfortunately, your request for access to COSMOS "
         f"({APP_URL}) has been declined by the administrator.\n\n"
         "If you believe this is a mistake, reply to this email."
         "\n\n— COSMOS"))


def send_question(to_email: str, question: str) -> bool:
    """Relay the admin's clarifying question to the applicant."""
    return _send(
        to_email,
        "COSMOS: запрос дополнительной информации / additional information"
        " requested",
        ("Здравствуйте!\n\n"
         f"Вы запросили доступ к COSMOS ({APP_URL}). Прежде чем принять "
         "решение, администратор просит уточнить:\n\n"
         f"{question}\n\n"
         "Ответьте на это письмо — ваш ответ попадёт к администратору."
         "\n\n— COSMOS"),
        ("Hello!\n\n"
         f"You requested access to COSMOS ({APP_URL}). Before making a "
         "decision, the administrator asks you to clarify:\n\n"
         f"{question}\n\n"
         "Reply to this email — your answer will reach the administrator."
         "\n\n— COSMOS"))
