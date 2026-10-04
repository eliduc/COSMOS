"""Telegram bot unit tests: notification formatting, the admin decision
flows (approve / deny / question), the owner-only security gate and the
disabled-configuration path. NO network — webapp.telegram_bot.api_call
(the bot's single network point) is monkeypatched with a recorder that
returns canned Telegram responses, and webapp.mailer is stubbed BEFORE
the bot module is imported (the real mailer may not even exist yet; the
bot imports it lazily inside handlers, so the sys.modules stub wins
either way). The user catalog and the access-request store are
redirected to a tmp SQLite file, as in test_users_admin.py.
"""
import sys
import types

import pytest


@pytest.fixture()
def mailer_calls(monkeypatch):
    """Stub webapp.mailer with recording no-ops that report success."""
    calls = {"approved": [], "denied": [], "question": []}

    def send_approved(email):
        calls["approved"].append(email)
        return True

    def send_denied(email):
        calls["denied"].append(email)
        return True

    def send_question(email, question):
        calls["question"].append((email, question))
        return True

    stub = types.SimpleNamespace(send_approved=send_approved,
                                 send_denied=send_denied,
                                 send_question=send_question)
    monkeypatch.setitem(sys.modules, "webapp.mailer", stub)
    # If the real mailer was already imported somewhere in the session,
    # the package attribute also points at it — override both routes.
    import webapp
    monkeypatch.setattr(webapp, "mailer", stub, raising=False)
    return calls


@pytest.fixture()
def bot_env(tmp_path, monkeypatch, mailer_calls):
    """Configured bot on a tmp catalog with a recording fake api_call."""
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    monkeypatch.setenv("CREW_TG_BOT_TOKEN", "t")
    monkeypatch.setenv("CREW_TG_CHAT_ID", "42")

    from webapp import telegram_bot as bot

    calls = []

    def fake_api_call(method, params, timeout=30.0):
        calls.append((method, params))
        if method == "sendMessage":
            return {"message_id": 7}
        if method == "getUpdates":
            return []
        return {}

    monkeypatch.setattr(bot, "api_call", fake_api_call)
    bot._pending_questions.clear()
    return bot, calls, mailer_calls


def _sent(calls, method):
    return [params for m, params in calls if m == method]


def _callback(req_id, action, chat=42, sender=42, message_id=5):
    """A Telegram callback_query update as the poller would receive it."""
    return {"update_id": 1, "callback_query": {
        "id": "cb-test",
        "from": {"id": sender},
        "message": {"message_id": message_id, "chat": {"id": chat},
                    "text": "original notification text"},
        "data": f"{action}:{req_id}",
    }}


# ------------------------------------------------------------- notification

def test_notify_access_request_sends_message(bot_env):
    bot, calls, _ = bot_env
    from webapp import access_requests
    created, row = access_requests.register("new@user.io")
    assert created

    bot.notify_access_request(row)

    sends = _sent(calls, "sendMessage")
    assert len(sends) == 1
    params = sends[0]
    assert params["chat_id"] == 42
    assert "new@user.io" in params["text"]
    buttons = [button["callback_data"]
               for kb_row in params["reply_markup"]["inline_keyboard"]
               for button in kb_row]
    assert buttons == [f"a:{row['id']}", f"d:{row['id']}", f"q:{row['id']}"]


# ----------------------------------------------------------- decision flows

def test_approve_flow(bot_env):
    bot, calls, mailer_calls = bot_env
    from webapp import access_requests, users
    _, row = access_requests.register("newcomer@user.io")

    bot._handle_update(_callback(row["id"], "a"))

    user = users.get_user("newcomer@user.io")
    assert user is not None
    assert user["role"] == "user" and user["status"] == "active"
    assert access_requests.get_by_id(row["id"])["status"] == "approved"
    assert mailer_calls["approved"] == ["newcomer@user.io"]

    edits = _sent(calls, "editMessageText")
    assert len(edits) == 1
    assert edits[0]["chat_id"] == 42 and edits[0]["message_id"] == 5
    assert "АВТОРИЗОВАН" in edits[0]["text"]
    assert "письмо отправлено" in edits[0]["text"]
    answers = _sent(calls, "answerCallbackQuery")
    assert len(answers) == 1 and answers[0]["text"] == "Авторизован"


def test_deny_flow(bot_env):
    bot, calls, mailer_calls = bot_env
    from webapp import access_requests, users
    _, row = access_requests.register("refused@user.io")

    bot._handle_update(_callback(row["id"], "d"))

    assert users.get_user("refused@user.io") is None  # never added
    assert access_requests.get_by_id(row["id"])["status"] == "denied"
    assert mailer_calls["denied"] == ["refused@user.io"]

    edits = _sent(calls, "editMessageText")
    assert len(edits) == 1 and "ОТКАЗАНО" in edits[0]["text"]
    answers = _sent(calls, "answerCallbackQuery")
    assert len(answers) == 1 and answers[0]["text"] == "Отказано"


def test_question_flow(bot_env):
    bot, calls, mailer_calls = bot_env
    from webapp import access_requests
    _, row = access_requests.register("curious@user.io")

    # Step 1: the admin presses the question button -> ForceReply prompt.
    bot._handle_update(_callback(row["id"], "q"))
    prompts = _sent(calls, "sendMessage")
    assert len(prompts) == 1
    assert "curious@user.io" in prompts[0]["text"]
    assert prompts[0]["reply_markup"] == {"force_reply": True}
    # The prompt's message_id (canned: 7) is remembered for the reply.
    assert bot._pending_questions == {7: row["id"]}

    # Step 2: the admin replies to the prompt with the question text.
    calls.clear()
    question = "Какова цель доступа?"
    bot._handle_update({"update_id": 2, "message": {
        "message_id": 8,
        "chat": {"id": 42},
        "from": {"id": 42},
        "text": question,
        "reply_to_message": {"message_id": 7},
    }})

    updated = access_requests.get_by_id(row["id"])
    assert updated["status"] == "info_requested"
    assert updated["question"] == question
    assert mailer_calls["question"] == [("curious@user.io", question)]
    assert bot._pending_questions == {}

    # The follow-up keeps approve/deny at hand for the final decision.
    follow_ups = _sent(calls, "sendMessage")
    assert len(follow_ups) == 1
    assert "curious@user.io" in follow_ups[0]["text"]
    buttons = [button["callback_data"]
               for kb_row in follow_ups[0]["reply_markup"]["inline_keyboard"]
               for button in kb_row]
    assert buttons == [f"a:{row['id']}", f"d:{row['id']}"]


# --------------------------------------------------------------- security

def test_foreign_chat_ignored(bot_env):
    bot, calls, mailer_calls = bot_env
    from webapp import access_requests, users
    _, row = access_requests.register("victim@user.io")

    # A callback from a foreign chat/sender must change NOTHING.
    bot._handle_update(_callback(row["id"], "a", chat=999, sender=999))

    assert users.get_user("victim@user.io") is None
    assert access_requests.get_by_id(row["id"])["status"] == "pending"
    assert mailer_calls["approved"] == []
    assert [m for m, _ in calls] == ["answerCallbackQuery"]
    assert calls[0][1]["text"] == "не авторизовано"


def test_disabled_without_env(bot_env, monkeypatch):
    bot, calls, _ = bot_env
    monkeypatch.delenv("CREW_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("CREW_TG_CHAT_ID", raising=False)

    assert bot.enabled() is False
    bot.notify_access_request(
        {"id": 1, "email": "x@y.io", "created_at": "2026-07-13T00:00:00Z"})
    assert calls == []


# ------------------------------------------------------ evolution pipeline

@pytest.fixture()
def evo_env(bot_env, monkeypatch):
    """bot_env + one seeded Evolution finding. The bot now routes approvals
    through the REAL evolution_runner.approve_action (one gate, one
    _approve_lock across Telegram AND the web endpoint), so we exercise
    that path and only stub the long phase functions + threading, recording
    which phase would launch."""
    bot, calls, _mailer = bot_env
    from webapp import evolution, evolution_runner as er
    created, row = evolution.add_finding(
        "Дуальная стабилизация прайсинга",
        "Стабилизация двойственных цен ускоряет сходимость генерации колонок.",
        source_url="https://example.org/papers/dual-stabilization",
        integration_plan="План интеграции: " + "шаг; " * 200,
        expected_gain="-20% времени этапа 1")
    assert created

    # Укреплённая среда агента: эти тесты про кнопки бота, а не про
    # изоляцию (у неё отдельный тест). Заодно проверка прав не полезет в
    # подпроцесс — здесь threading.Thread подменён целиком.
    monkeypatch.setattr(er, "isolation_problems", lambda: [])

    runner_calls = {"test": [], "stage": [], "prod": []}
    monkeypatch.setattr(
        er, "run_test_phase",
        lambda imp_id, **kw: runner_calls["test"].append((imp_id, kw)))
    monkeypatch.setattr(
        er, "run_stage_phase",
        lambda imp_id, **kw: runner_calls["stage"].append(imp_id))
    monkeypatch.setattr(
        er, "deploy_prod",
        lambda imp_id, **kw: runner_calls["prod"].append(imp_id))

    class ImmediateThread:
        """threading.Thread lookalike that runs the target synchronously."""

        def __init__(self, target=None, args=(), kwargs=None,
                     daemon=None, name=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    # approve_action spawns the phase thread from inside evolution_runner.
    monkeypatch.setattr(er.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(bot.threading, "Thread", ImmediateThread)
    return bot, calls, row, runner_calls


def test_evolution_approve_test_flow(evo_env):
    bot, calls, row, runner_calls = evo_env
    from webapp import evolution

    bot._handle_update(_callback(row["id"], "ev:t"))

    updated = evolution.get(row["id"])
    assert updated["status"] == evolution.STATUS_APPROVED_FOR_TEST
    assert updated["decided_by"] == "telegram-admin"
    assert runner_calls == {"test": [(row["id"], {})], "stage": [],
                            "prod": []}

    answers = _sent(calls, "answerCallbackQuery")
    assert len(answers) == 1
    assert answers[0]["text"] == "Принято — внедряю на Test"
    edits = _sent(calls, "editMessageText")
    assert len(edits) == 1
    assert edits[0]["chat_id"] == 42 and edits[0]["message_id"] == 5
    assert edits[0]["text"].startswith("original notification text")
    assert "ОДОБРЕНО ДЛЯ TEST" in edits[0]["text"]


def test_evolution_reject_flow(evo_env):
    bot, calls, row, runner_calls = evo_env
    from webapp import evolution

    bot._handle_update(_callback(row["id"], "ev:x"))

    assert evolution.get(row["id"])["status"] == evolution.STATUS_REJECTED
    assert runner_calls == {"test": [], "stage": [], "prod": []}

    answers = _sent(calls, "answerCallbackQuery")
    assert len(answers) == 1 and answers[0]["text"] == "Отклонено"
    edits = _sent(calls, "editMessageText")
    assert len(edits) == 1 and "ОТКЛОНЕНО" in edits[0]["text"]


def test_evolution_status_gate_blocks_wrong_predecessor(evo_env):
    """A stale/duplicate press must not re-drive a finding into a second
    phase: a still-'found' finding cannot be pushed straight to Stage or
    Prod, and 'На Stage' twice runs the phase at most once."""
    bot, calls, row, runner_calls = evo_env
    from webapp import evolution

    # found -> Stage is not a valid transition.
    bot._handle_update(_callback(row["id"], "ev:s"))
    assert runner_calls["stage"] == []
    assert evolution.get(row["id"])["status"] == evolution.STATUS_FOUND
    answers = _sent(calls, "answerCallbackQuery")
    assert answers and "уже обработано" in answers[-1]["text"].lower()

    # stage_passed + «Деплой»: продвижение из телеграма теперь требует
    # контрподписи человека (аудит R10, P0-04) — кнопка токен передать не
    # может, фаза НЕ стартует, статус не меняется, ответ называет путь.
    evolution.set_status(row["id"], evolution.STATUS_STAGE_PASSED)
    bot._handle_update(_callback(row["id"], "ev:p"))
    bot._handle_update(_callback(row["id"], "ev:p"))
    assert runner_calls["prod"] == []
    assert evolution.get(row["id"])["status"] \
        == evolution.STATUS_STAGE_PASSED
    answers = _sent(calls, "answerCallbackQuery")
    assert "контрподпис" in answers[-1]["text"].lower()
    assert "countersign" in answers[-1]["text"]


def test_evolution_unknown_finding(evo_env):
    bot, calls, row, runner_calls = evo_env
    from webapp import evolution

    bot._handle_update(_callback(999999, "ev:t"))

    assert [m for m, _ in calls] == ["answerCallbackQuery"]
    # A not-found callback names the likely cause (another instance sharing
    # the bot), not a blank «not found».
    txt = calls[0][1]["text"]
    assert "не найдена" in txt and "другой инстанс" in txt
    assert runner_calls == {"test": [], "stage": [], "prod": []}
    assert evolution.get(row["id"])["status"] == evolution.STATUS_FOUND


def test_is_primary_guard(monkeypatch):
    """The poller starts ONLY on the instance marked primary — otherwise a
    stray access-mode instance with the token would steal button callbacks."""
    from webapp import telegram_bot as tb
    monkeypatch.setenv("CREW_TG_BOT_TOKEN", "x")
    monkeypatch.setenv("CREW_TG_CHAT_ID", "1")
    monkeypatch.delenv("CREW_TG_PRIMARY", raising=False)
    assert tb.is_primary() is False
    assert tb.start_polling_thread() is None        # token present, not primary
    monkeypatch.setenv("CREW_TG_PRIMARY", "1")
    assert tb.is_primary() is True


def test_notify_evolution_finding(evo_env):
    bot, calls, row, _runner_calls = evo_env

    bot.notify_evolution_finding(row)

    sends = _sent(calls, "sendMessage")
    assert len(sends) == 1
    params = sends[0]
    assert params["chat_id"] == 42
    assert row["title"] in params["text"]
    buttons = [button["callback_data"]
               for kb_row in params["reply_markup"]["inline_keyboard"]
               for button in kb_row]
    assert buttons == [f"ev:t:{row['id']}", f"ev:x:{row['id']}"]


def test_notify_evolution_report_test_truncates(evo_env):
    bot, calls, row, _runner_calls = evo_env

    bot.notify_evolution_report(row, "test", "R" * 10000)

    sends = _sent(calls, "sendMessage")
    assert len(sends) == 1
    params = sends[0]
    assert len(params["text"]) <= 4096  # Telegram's hard message cap
    assert row["title"] in params["text"]
    buttons = [button["callback_data"]
               for kb_row in params["reply_markup"]["inline_keyboard"]
               for button in kb_row]
    assert buttons == [f"ev:s:{row['id']}", f"ev:x:{row['id']}"]


def test_evolution_unisolated_offers_a_confirm_button(evo_env, monkeypatch):
    """Кнопка в боте на неукреплённой машине не упирается в глухой отказ:
    бот называет препятствия и даёт кнопку подтверждения
    (правка 2026-07-26)."""
    bot, calls, row, runner_calls = evo_env
    from webapp import evolution, evolution_runner as er
    # фикстура evo_env глушит isolation_problems -> []; здесь нужна как
    # раз НЕизолированная среда
    monkeypatch.setattr(er, "isolation_problems", lambda: ["агент работает под той же учётной записью, что и приложение (COSMOS_AGENT_ACCOUNT)"])
    monkeypatch.delenv(er._UNISOLATED_OK, raising=False)

    bot._handle_update(_callback(row["id"], "ev:t"))

    # статус не изменился, фаза не запущена
    assert evolution.get(row["id"])["status"] == row["status"]
    assert runner_calls["test"] == []
    msgs = _sent(calls, "sendMessage")
    assert msgs and "НЕ изолирована" in msgs[0]["text"]
    button = msgs[0]["reply_markup"]["inline_keyboard"][0][0]
    assert button["callback_data"] == f"ev:t!:{row['id']}"

    # подтверждающее нажатие запускает фазу и доносит признак
    bot._handle_update(_callback(row["id"], "ev:t!"))
    assert evolution.get(row["id"])["status"] == \
        evolution.STATUS_APPROVED_FOR_TEST
    assert runner_calls["test"] == [(row["id"],
                                     {"acknowledged_unisolated": True})]
