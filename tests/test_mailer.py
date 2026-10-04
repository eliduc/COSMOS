"""Outbound notification mail (webapp/mailer.py): env-driven enablement,
message shape (bilingual RU/EN body, "COSMOS <...>" From, Reply-To), the
clarifying question appearing in both language halves, and the guarantee
that delivery failures return False instead of raising.

No network: mailer._deliver is the single transport point and every test
replaces it (with a recorder or a failing stub) via monkeypatch.
"""
import pytest

from webapp import mailer


@pytest.fixture()
def smtp_env(monkeypatch):
    """Enable the mailer with fake credentials (FROM falls back to USER)."""
    monkeypatch.setenv("CREW_SMTP_USER", "cosmos-admin@example.com")
    monkeypatch.setenv("CREW_SMTP_PASS", "app-password")
    monkeypatch.delenv("CREW_SMTP_FROM", raising=False)


@pytest.fixture()
def recorder(monkeypatch):
    """Capture EmailMessage objects instead of opening SMTP connections."""
    sent = []
    monkeypatch.setattr(mailer, "_deliver", sent.append)
    return sent


def test_disabled_without_credentials(monkeypatch, recorder):
    monkeypatch.delenv("CREW_SMTP_USER", raising=False)
    monkeypatch.delenv("CREW_SMTP_PASS", raising=False)
    assert mailer.enabled() is False
    assert mailer.send_approved("a@b.co") is False
    assert recorder == []


def test_approved_message_shape(smtp_env, recorder):
    assert mailer.send_approved("a@b.co") is True
    assert len(recorder) == 1
    msg = recorder[0]
    assert msg["To"] == "a@b.co"
    assert "COSMOS" in msg["From"]
    assert msg["Reply-To"] == "cosmos-admin@example.com"
    assert "одобрен" in msg["Subject"]
    body = msg.get_content()
    # Адрес продукта берётся из кода отправителя, а не повторяется
    # строкой: повтор пережил бы смену адреса площадки молча.
    assert mailer.APP_URL in body
    assert "Здравствуйте" in body
    assert "Hello" in body


def test_question_includes_text(smtp_env, recorder):
    question = "Какая авиакомпания?"
    assert mailer.send_question("a@b.co", question) is True
    body = recorder[0].get_content()
    ru_half, sep, en_half = body.partition("\n\n---\n\n")
    assert sep, "bilingual separator missing from the body"
    assert question in ru_half
    assert question in en_half


def test_delivery_failure_returns_false(smtp_env, monkeypatch):
    def _boom(msg):
        raise OSError("connection refused")

    monkeypatch.setattr(mailer, "_deliver", _boom)
    assert mailer.send_denied("a@b.co") is False
