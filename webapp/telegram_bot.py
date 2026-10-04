"""Telegram bot for COSMOS access-request notifications and decisions.

Design. The webapp middleware registers an access request when a visitor
passes Cloudflare's email OTP but is missing from the user catalog; it
then calls notify_access_request() in a background thread, which sends
the admin ONE Telegram message with inline decision buttons. A long-poll
daemon thread (start_polling_thread(), invoked from the FastAPI
lifespan) receives the admin's button presses and replies:

    a:<id>  approve — add/reactivate the user in the catalog, mark the
            request approved, email the visitor;
    d:<id>  deny — mark the request denied, email the visitor;
    q:<id>  ask — send the admin a ForceReply prompt; the admin's reply
            text is emailed to the visitor as a question and the request
            becomes info_requested.

The same channel drives the COSMOS Evolution pipeline (webapp/evolution.py):
notify_evolution_finding() / notify_evolution_report() push the nightly
agent's findings and the phase reports to the admin, whose button presses
arrive as callbacks "ev:<action>:<id>" (t = approve for Test, s = approve
for Stage, p = approve the Prod deploy, x = reject). Approvals advance
the finding's status and hand the id to webapp/evolution_runner.py in a
daemon thread; the runner module is imported lazily inside the handler
(it is deployed separately and must not be a hard import dependency).

Security: the bot obeys exactly one principal — updates are accepted only
when both the chat id AND the sender id equal CREW_TG_CHAT_ID (a private
chat with the owner). Everything else is refused or ignored. The single
special case is initial setup: while CREW_TG_CHAT_ID is not configured,
"/start" and "/id" reply with the sender's chat id (so the admin can
learn the value to configure) and nothing else is served.

Configuration is read from the environment AT CALL TIME (never at import
time) so tests and late-set environments behave predictably:

    CREW_TG_BOT_TOKEN   BotFather token
    CREW_TG_CHAT_ID     the admin's private chat id (integer as string)

All network traffic goes through the single api_call() function (tests
monkeypatch exactly that; no new pip dependencies — urllib only). House
rules: programming errors fail loudly, but network/Telegram-API failures
must never kill the poller thread nor propagate into the webapp — they
are logged and retried/skipped. The mailer module is imported lazily
inside handlers: it is deployed separately and must not be a hard import
dependency of this module.
"""
from __future__ import annotations

import importlib
import json
import os
import threading
import time
import traceback
import urllib.error
import urllib.request
from typing import Any

from . import access_requests, users

APP_NAME = "COSMOS"

_ENV_TOKEN = "CREW_TG_BOT_TOKEN"
_ENV_CHAT_ID = "CREW_TG_CHAT_ID"

# Decision actor recorded in the catalog and the request audit trail.
_DECIDED_BY = "telegram-admin"

# ForceReply prompt message_id -> access request id awaiting the admin's
# question text. In-memory on purpose: a restart merely drops the prompt,
# the admin can press the button again.
_pending_questions: dict[int, int] = {}

_poller_lock = threading.Lock()
_poller_thread: threading.Thread | None = None


class TelegramError(RuntimeError):
    """Base class for Telegram delivery failures (expected, non-fatal)."""


class TelegramApiError(TelegramError):
    """Telegram answered ok=false (bad request, revoked token, ...)."""


class TelegramNetworkError(TelegramError):
    """The HTTPS call itself failed (DNS, timeout, connection reset)."""


# ------------------------------------------------------------ configuration

def _token() -> str:
    return os.environ.get(_ENV_TOKEN, "").strip()


def _chat_id() -> str:
    return os.environ.get(_ENV_CHAT_ID, "").strip()


def enabled() -> bool:
    """True when both env vars are set and the chat id parses as int."""
    if not _token() or not _chat_id():
        return False
    try:
        int(_chat_id())
    except ValueError:
        return False
    return True


def _mailer() -> Any:
    """Lazy mailer import (module may not exist at import time; tests may
    stub sys.modules["webapp.mailer"]). import_module resolves through
    sys.modules first, so the stub always wins."""
    return importlib.import_module(".mailer", package=__package__)


# ------------------------------------------------------ single network point

def api_call(method: str, params: dict, timeout: float = 30.0) -> Any:
    """POST one JSON request to the Telegram Bot API; return the "result".

    The ONLY function that touches the network (tests monkeypatch it).
    Raises TelegramNetworkError when the HTTPS exchange itself fails and
    TelegramApiError(description) when Telegram answers ok != true. Note:
    the result is a dict for most methods but a list for getUpdates.
    """
    url = f"https://api.telegram.org/bot{_token()}/{method}"
    request = urllib.request.Request(
        url,
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            # Telegram sends ok=false JSON bodies with 4xx/5xx codes; read
            # the body so the ok-check below raises the descriptive error.
            raw = exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TelegramNetworkError(
            f"{method}: network failure: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        # Garbage instead of JSON means a broken proxy/gateway, not a
        # Telegram-level refusal: classify as a network failure.
        raise TelegramNetworkError(
            f"{method}: non-JSON response from API") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        description = "malformed API response"
        if isinstance(payload, dict):
            description = str(payload.get("description",
                                          "no error description"))
        raise TelegramApiError(f"{method}: {description}")
    return payload.get("result")


# --------------------------------------------------------- outbound message

def _request_text(row: dict) -> str:
    """The admin notification text for one access request."""
    return (f"\U0001f6c2 {APP_NAME} — запрос доступа\n\n"
            f"Email: {row['email']}\n"
            "Прошёл проверку кода на email (Cloudflare Access), "
            "но отсутствует в каталоге пользователей.\n"
            f"Запрос №{row['id']} от {row['created_at']} UTC.")


def _decision_buttons(req_id: int) -> list[list[dict]]:
    return [[{"text": "✅ Авторизовать", "callback_data": f"a:{req_id}"},
             {"text": "⛔ Отказать", "callback_data": f"d:{req_id}"}]]


def notify_access_request(req: dict) -> None:
    """Send the admin one decision message for a fresh access request.

    Called by the webapp middleware from a background thread; therefore
    it NEVER raises — Telegram failures are logged and swallowed (the
    request row itself is already persisted, nothing is lost).
    """
    if not enabled():
        print(f"INFO: telegram bot not configured ({_ENV_TOKEN}/"
              f"{_ENV_CHAT_ID}) — access request "
              f"#{req.get('id')} not pushed to the admin", flush=True)
        return
    req_id = int(req["id"])
    keyboard = _decision_buttons(req_id)
    keyboard.append([{"text": "❓ Запросить информацию",
                      "callback_data": f"q:{req_id}"}])
    try:
        api_call("sendMessage", {
            "chat_id": int(_chat_id()),
            "text": _request_text(req),
            "reply_markup": {"inline_keyboard": keyboard},
        })
    except TelegramError as exc:
        print(f"WARNING: telegram notification for access request "
              f"#{req_id} failed: {exc}", flush=True)


# -------------------------------------------------- evolution notifications

# Telegram caps messages at 4096 chars; reports are cut well below that so
# the header and a possible edit-appended verdict always fit.
_EVO_REPORT_MAX_CHARS = 3800
_EVO_PLAN_MAX_CHARS = 500


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _finding_text(row: dict) -> str:
    """The admin triage text for one Evolution finding."""
    return (f"\U0001f52c {APP_NAME} Evolution — новая находка\n\n"
            f"{row['title']}\n\n"
            f"{row['summary']}\n"
            f"Источник: {row.get('source_url') or '—'}\n"
            f"Ожидаемый эффект: {row.get('expected_gain') or '—'}\n\n"
            f"План:\n"
            f"{_clip(row.get('integration_plan') or '—', _EVO_PLAN_MAX_CHARS)}")


def notify_evolution_finding(row: dict) -> None:
    """Send the admin one triage message for a fresh Evolution finding.

    Called from background threads (mirrors notify_access_request), so it
    NEVER raises — Telegram failures are logged and swallowed (the finding
    row itself is already persisted, nothing is lost).
    """
    if not enabled():
        print(f"INFO: telegram bot not configured ({_ENV_TOKEN}/"
              f"{_ENV_CHAT_ID}) — evolution finding "
              f"#{row.get('id')} not pushed to the admin", flush=True)
        return
    imp_id = int(row["id"])
    keyboard = [[{"text": "🧪 Внедрить на Test",
                  "callback_data": f"ev:t:{imp_id}"},
                 {"text": "🗑 Пропустить",
                  "callback_data": f"ev:x:{imp_id}"}]]
    try:
        api_call("sendMessage", {
            "chat_id": int(_chat_id()),
            "text": _finding_text(row),
            "reply_markup": {"inline_keyboard": keyboard},
        })
    except TelegramError as exc:
        print(f"WARNING: telegram notification for evolution finding "
              f"#{imp_id} failed: {exc}", flush=True)


def notify_audit_found(names: list[str]) -> None:
    """Announce freshly detected third-party audit reports to the admin.

    Called from the audit-inbox poller thread — NEVER raises (mirrors
    notify_access_request); the registry rows are already persisted, so a
    lost Telegram message costs nothing but convenience.
    """
    if not names:
        return
    if not enabled():
        print(f"INFO: telegram bot not configured ({_ENV_TOKEN}/"
              f"{_ENV_CHAT_ID}) — new audit report(s) "
              f"{', '.join(names)} not pushed to the admin", flush=True)
        return
    listing = "\n".join(f"• {n}" for n in names[:10])
    if len(names) > 10:
        listing += f"\n… и ещё {len(names) - 10}"
    try:
        api_call("sendMessage", {
            "chat_id": int(_chat_id()),
            "text": ("🕵️ Обнаружен новый отчёт аудитора:\n" + listing
                     + "\n\nОбработка: Автоулучшение → Изменения → Аудит "
                       "→ «Обработать»."),
        })
    except TelegramError as exc:
        print(f"WARNING: telegram notification for audit report(s) "
              f"failed: {exc}", flush=True)


def notify_evolution_report(row: dict, phase: str, report_md: str) -> None:
    """Send the admin one phase report for an Evolution finding.

    phase "test"/"stage" appends the next-step decision buttons; "final"
    sends report_md as-is (the ready text), no keyboard. The text is
    truncated to 3800 chars (Telegram's cap is 4096). Never raises on
    Telegram failures — mirrors notify_access_request; an unknown phase
    is a programming error and stays loud.
    """
    if phase not in ("test", "stage", "final"):
        raise ValueError(
            f"phase must be 'test', 'stage' or 'final', got {phase!r}")
    if not enabled():
        print(f"INFO: telegram bot not configured ({_ENV_TOKEN}/"
              f"{_ENV_CHAT_ID}) — evolution {phase} report for "
              f"#{row.get('id')} not pushed to the admin", flush=True)
        return
    imp_id = int(row["id"])
    if phase == "test":
        text = (f"\U0001f9ea {APP_NAME} Evolution — отчёт Test по "
                f"«{row['title']}»\n\n{report_md}")
        keyboard = [[{"text": "⬆ На Stage", "callback_data": f"ev:s:{imp_id}"},
                     {"text": "❌ Отклонить",
                      "callback_data": f"ev:x:{imp_id}"}]]
    elif phase == "stage":
        text = (f"\U0001f3d7 {APP_NAME} Evolution — отчёт Stage по "
                f"«{row['title']}»\n\n{report_md}")
        keyboard = [[{"text": "🚀 Деплой на Prod",
                      "callback_data": f"ev:p:{imp_id}"},
                     {"text": "❌ Отклонить",
                      "callback_data": f"ev:x:{imp_id}"}]]
    else:
        text = report_md
        keyboard = None
    params: dict = {"chat_id": int(_chat_id()),
                    "text": _clip(text, _EVO_REPORT_MAX_CHARS)}
    if keyboard is not None:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        api_call("sendMessage", params)
    except TelegramError as exc:
        print(f"WARNING: telegram evolution {phase} report for finding "
              f"#{imp_id} failed: {exc}", flush=True)


# ------------------------------------------------------------------- poller

def is_primary() -> bool:
    """Only the instance explicitly marked primary (CREW_TG_PRIMARY=1) may
    poll the bot. Telegram delivers each getUpdates result to exactly ONE
    caller, so two instances sharing a token split button callbacks
    unpredictably — a callback for a finding recorded on instance A lands
    on instance B and answers «not found». The flag makes exactly-one-poller
    an explicit deployment choice, not an accident of who has the token."""
    return os.environ.get("CREW_TG_PRIMARY", "").strip() == "1"


def start_polling_thread() -> threading.Thread | None:
    """Start the getUpdates daemon thread once; None when disabled or when
    this instance is not the designated primary."""
    global _poller_thread
    if not enabled():
        print(f"INFO: telegram bot not configured ({_ENV_TOKEN}/"
              f"{_ENV_CHAT_ID}) — poller not started", flush=True)
        return None
    if not is_primary():
        print("INFO: telegram poller NOT started — this instance is not the "
              "primary (set CREW_TG_PRIMARY=1 on the one production instance "
              "that should own the bot). Prevents split callbacks across "
              "instances sharing the token.", flush=True)
        return None
    with _poller_lock:
        if _poller_thread is not None and _poller_thread.is_alive():
            return _poller_thread  # already running: never start a second
        _poller_thread = threading.Thread(
            target=_poll_loop, name="cosmos-telegram-poller", daemon=True)
        _poller_thread.start()
        return _poller_thread


def _poll_loop() -> None:
    """Long-poll getUpdates forever; survives ANY exception."""
    offset: int | None = None
    while True:
        try:
            updates = api_call(
                "getUpdates",
                {"timeout": 50, "offset": offset,
                 "allowed_updates": ["message", "callback_query"]},
                timeout=70)
        except TelegramError as exc:
            msg = str(exc)
            if "Conflict" in msg or "409" in msg:
                print("CRITICAL: telegram getUpdates 409 CONFLICT — ANOTHER "
                      "poller is active on this bot token. Button callbacks "
                      "will be split unpredictably across instances and some "
                      "will answer «not found». Run the bot on EXACTLY ONE "
                      "instance (CREW_TG_PRIMARY=1 there, unset elsewhere).",
                      flush=True)
            else:
                print(f"WARNING: telegram getUpdates failed: {exc}; "
                      "retrying in 5 s", flush=True)
            time.sleep(5)
            continue
        except Exception:  # noqa: BLE001 — the poller must never die
            print("WARNING: telegram poller unexpected failure "
                  "(retrying in 5 s):\n" + traceback.format_exc(),
                  flush=True)
            time.sleep(5)
            continue
        for update in updates or ():
            try:
                offset = int(update["update_id"]) + 1
                _handle_update(update)
            except Exception:  # noqa: BLE001 — skip one bad update
                print("WARNING: telegram update handling failed "
                      "(update skipped):\n" + traceback.format_exc(),
                      flush=True)


# ----------------------------------------------------------- update handling

def _handle_update(u: dict) -> None:
    if "callback_query" in u:
        _handle_callback(u["callback_query"])
    elif "message" in u:
        _handle_message(u["message"])
    # Other update kinds are not requested via allowed_updates; ignore.


def _answer_callback(query: dict, text: str) -> None:
    """Dismiss the button spinner; a failure here is purely cosmetic."""
    try:
        api_call("answerCallbackQuery",
                 {"callback_query_id": query.get("id"), "text": text})
    except TelegramError as exc:
        print(f"WARNING: telegram answerCallbackQuery failed: {exc}",
              flush=True)


def _mail_note(mail_ok: bool) -> str:
    return ("(письмо отправлено)" if mail_ok
            else "(письмо НЕ отправлено — почта не настроена)")


def _handle_callback(query: dict) -> None:
    """One admin button press: authorize/deny/ask for one request."""
    admin_chat = _chat_id()
    message = query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    from_id = (query.get("from") or {}).get("id")
    # Security first: only the owner's private chat may drive decisions —
    # both the chat AND the presser must be the configured admin id.
    if (not admin_chat or str(chat_id) != admin_chat
            or str(from_id) != admin_chat):
        _answer_callback(query, "не авторизовано")
        return
    data = str(query.get("data") or "")
    if data.startswith("ev:"):
        _handle_evolution_callback(query, data)
        return
    action, sep, raw_id = data.partition(":")
    try:
        req_id = int(raw_id)
    except ValueError:
        req_id = -1
    if not sep or action not in ("a", "d", "q") or req_id < 0:
        _answer_callback(query, "неизвестная команда")
        return
    try:
        if action == "a":
            _do_approve(query, req_id)
        elif action == "d":
            _do_deny(query, req_id)
        else:
            _do_ask(query, req_id)
    except access_requests.RequestNotFoundError:
        _answer_callback(query, "Запрос не найден")


def _edit_evolution_message(query: dict, verdict: str) -> None:
    """Rewrite the original finding/report message so the decision is on
    record (like _edit_decision_message, without the mail note)."""
    message = query["message"]
    api_call("editMessageText", {
        "chat_id": message["chat"]["id"],
        "message_id": message["message_id"],
        "text": f"{message.get('text') or ''}\n\n{verdict}",
    })


def _handle_evolution_callback(query: dict, data: str) -> None:
    """One admin button press on an Evolution finding ("ev:<action>:<id>"):
    approve the next phase (t/s/p) or reject (x). The finding store and
    the runner are imported lazily — the runner module is deployed
    separately, and import_module resolves through sys.modules first, so
    test stubs always win (same pattern as _mailer)."""
    parts = data.split(":")
    action = parts[1] if len(parts) == 3 else ""
    try:
        imp_id = int(parts[2]) if len(parts) == 3 else -1
    except ValueError:
        imp_id = -1
    if action not in ("t", "s", "p", "x", "t!") or imp_id < 0:
        _answer_callback(query, "неизвестная команда")
        return
    # «t!» — повторное нажатие после предупреждения о неизолированной
    # среде: то же действие, но с осознанным подтверждением человека.
    confirmed = action == "t!"
    if confirmed:
        action = "t"
    # Delegate the state transition AND the phase launch to the runner's
    # approve_action: it runs the check-then-set + thread spawn under
    # _approve_lock, the SAME lock the web /action endpoint uses. Doing the
    # transition here directly (as before) left a cross-surface race — a
    # web press and a Telegram press on the same finding could both pass
    # the gate and spawn two phase threads. One gate, one lock, both
    # surfaces. Imported lazily so a deploy-skew missing module is logged,
    # not half-applied (import_module resolves test stubs via sys.modules).
    runner = importlib.import_module(".evolution_runner",
                                     package=__package__)
    res = runner.approve_action(imp_id, action, _DECIDED_BY,
                                acknowledged_unisolated=confirmed)
    if not res.get("ok"):
        if res.get("error") == "not_found":
            # A finding absent from THIS instance's DB almost always means
            # the notification came from a different instance sharing the
            # bot token (split callbacks). Say so, don't just fail blankly.
            _answer_callback(
                query, "Находка не найдена в этом инстансе COSMOS — "
                       "вероятно, её создал другой инстанс. Бот должен "
                       "работать только на боевом сервере.")
        elif res.get("error") == "isolation_unconfirmed":
            # Не отказ, а требование осознанного решения: называем
            # препятствия и даём кнопку подтверждения (правка 2026-07-26 —
            # глухой отказ просто выключал контур самоулучшения).
            problems = "; ".join(res.get("problems") or [])
            _answer_callback(
                query, "Среда агента не изолирована — нужно подтверждение")
            api_call("sendMessage", {
                "chat_id": query["message"]["chat"]["id"],
                "text": ("⚠ Среда агента внедрения НЕ изолирована:\n"
                         f"{problems}\n\n"
                         "Агент исполняет план, составленный по тексту из "
                         "интернета, под той же учётной записью, что и "
                         "приложение. Запуск возможен, но решение ваше — "
                         "оно попадёт в журнал."),
                "reply_markup": {"inline_keyboard": [[
                    {"text": "▶ Запустить всё равно",
                     "callback_data": f"ev:t!:{imp_id}"}]]},
            })
        elif res.get("error") == "countersign_required":
            # Продвижение требует контрподписи человека (аудит R10,
            # P0-04): кнопка токен передать не может — называем путь, а
            # не отвечаем глухим «недоступно».
            _answer_callback(
                query, "Нужна контрподпись: выпишите токен "
                       "(python tools/countersign.py) и подтвердите "
                       "продвижение из веб-интерфейса «Алгоритмы»")
        else:
            _answer_callback(
                query, f"Недоступно из статуса «{res.get('current')}» "
                       f"— уже обработано")
        return
    ack, banner = {
        "x": ("Отклонено", "🗑 ОТКЛОНЕНО"),
        "t": ("Принято — внедряю на Test", "🧪 ОДОБРЕНО ДЛЯ TEST"),
        "s": ("Запускаю Stage", "⬆ ОДОБРЕНО ДЛЯ STAGE"),
        "p": ("Запускаю деплой на Prod", "🚀 ОДОБРЕН ДЕПЛОЙ"),
    }[action]
    _answer_callback(query, ack)
    _edit_evolution_message(query, banner)


def _edit_decision_message(query: dict, row: dict, verdict: str,
                           mail_ok: bool) -> None:
    """Rewrite the original notification so the decision is on record."""
    message = query["message"]
    base = message.get("text") or _request_text(row)
    api_call("editMessageText", {
        "chat_id": message["chat"]["id"],
        "message_id": message["message_id"],
        "text": f"{base}\n\n{verdict}\n{_mail_note(mail_ok)}",
    })


def _do_approve(query: dict, req_id: int) -> None:
    row = access_requests.get_by_id(req_id)
    email = row["email"]
    # Mirror the decision into the user catalog: create or reactivate.
    existing = users.get_user(email)
    if existing is None:
        users.add(email, users.ROLE_USER, by=_DECIDED_BY)
    elif existing["status"] != users.STATUS_ACTIVE:
        users.set_status(email, users.STATUS_ACTIVE)
    access_requests.set_status(req_id, access_requests.STATUS_APPROVED,
                               decided_by=_DECIDED_BY)
    mail_ok = bool(_mailer().send_approved(email))
    _edit_decision_message(query, row, "✅ АВТОРИЗОВАН", mail_ok)
    _answer_callback(query, "Авторизован")


def _do_deny(query: dict, req_id: int) -> None:
    row = access_requests.get_by_id(req_id)
    access_requests.set_status(req_id, access_requests.STATUS_DENIED,
                               decided_by=_DECIDED_BY)
    mail_ok = bool(_mailer().send_denied(row["email"]))
    _edit_decision_message(query, row, "⛔ ОТКАЗАНО", mail_ok)
    _answer_callback(query, "Отказано")


def _do_ask(query: dict, req_id: int) -> None:
    """Prompt the admin (ForceReply) for the question text; the actual
    email is sent when the reply arrives — see _handle_message."""
    row = access_requests.get_by_id(req_id)
    sent = api_call("sendMessage", {
        "chat_id": int(_chat_id()),
        "text": (f"❓ Вопрос для {row['email']} (запрос №{req_id}).\n"
                 "Ответьте НА ЭТО сообщение текстом вопроса — "
                 "он уйдёт пользователю на email."),
        "reply_markup": {"force_reply": True},
    })
    _pending_questions[int(sent["message_id"])] = req_id
    _answer_callback(query, "Введите вопрос ответом на сообщение")


def _handle_message(message: dict) -> None:
    admin_chat = _chat_id()
    chat_id = (message.get("chat") or {}).get("id")
    from_id = (message.get("from") or {}).get("id")
    text = (message.get("text") or "").strip()

    if not admin_chat:
        # Initial setup: CREW_TG_CHAT_ID is not configured yet. The ONLY
        # thing the bot serves is the chat-id echo, so the admin can find
        # out what value to configure. Everything else stays silent.
        if text in ("/start", "/id"):
            api_call("sendMessage", {
                "chat_id": chat_id,
                "text": f"{APP_NAME}-бот на связи. Ваш chat id: {chat_id}",
            })
        return

    # Configured: accept messages ONLY from the owner's private chat.
    if str(chat_id) != admin_chat or str(from_id) != admin_chat:
        return

    reply_to = (message.get("reply_to_message") or {}).get("message_id")
    if reply_to in _pending_questions:
        _finish_question(message, _pending_questions.pop(reply_to))
        return

    if text in ("/start", "/id"):
        api_call("sendMessage", {
            "chat_id": chat_id,
            "text": f"{APP_NAME}-бот на связи. Ваш chat id: {chat_id}",
        })


def _finish_question(message: dict, req_id: int) -> None:
    """The admin replied to a ForceReply prompt: email the question."""
    question = (message.get("text") or "").strip()
    if not question:
        api_call("sendMessage", {"chat_id": int(_chat_id()),
                                 "text": "Пустой вопрос — отменено"})
        return
    row = access_requests.get_by_id(req_id)
    access_requests.set_status(
        req_id, access_requests.STATUS_INFO_REQUESTED,
        decided_by=_DECIDED_BY, question=question)
    mail_ok = bool(_mailer().send_question(row["email"], question))
    # Keep the decision buttons at hand so the admin can approve/deny
    # right after the visitor answers the emailed question.
    api_call("sendMessage", {
        "chat_id": int(_chat_id()),
        "text": (f"\U0001f4e8 Вопрос отправлен пользователю "
                 f"{row['email']}. {_mail_note(mail_ok)}"),
        "reply_markup": {"inline_keyboard": _decision_buttons(req_id)},
    })
