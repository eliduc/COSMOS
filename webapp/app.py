"""FastAPI app for the pairing-rostering pipeline (API contract v1).

Run: .venv/Scripts/python.exe -m uvicorn webapp.app:app --port 8090
Static SPA at / from webapp/static/; all API under /api. The app only
orchestrates the pipeline scripts as subprocesses — see jobs.py — and
reads their artifacts — see results.py. It never edits them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from starlette.requests import HTTPConnection

import threading
import time

from . import (access_requests, activity_log, admin_api, auth, config_scope,
               demo, jobs, ops_log, ops_replay, plan_fact, registry, results,
               settings, telegram_bot, users)
from .registry import REPO_ROOT

CONFIG_DIR = REPO_ROOT / "webapp" / "config"
STATIC_DIR = REPO_ROOT / "webapp" / "static"

# Tier-1 airline rule parameters + crew-composition rules: the surface is
# the SINGLE source rules_config.DEFAULTS (audit-2 P2-05: comp_* were
# писались загрузчиком, но скрывались GET, отвергались PUT и молча
# терялись при мерже — правило нельзя было включить через Установки).
import rules_config as _rules_config
import _data_paths  # noqa: E402 — каталог данных контура

# Консоль сервера настраивается здесь же, где и у командных инструментов.
# Приложение печатает русские сообщения (перенос конфигурации, режим
# входа, предупреждения), а Windows по умолчанию отдаёт консоли cp1252 —
# и фоновый поток начальной настройки падал `UnicodeEncodeError` ровно на
# печати успешного отчёта. Урок 2026-07-26 повторился, потому что страж
# стерёг корень и `tools/`, а `webapp/` не стерёг никто.
from _cli_console import force_utf8_console, say  # noqa: E402
force_utf8_console()

AIRLINE_PARAM_DEFAULTS = dict(_rules_config.DEFAULTS)
AIRLINE_CONFIG_NOTE = (
    "параметры передаются всем этапам через CREW_RULES_CONFIG; "
    "отпечаток задачи включает правила"
)

SSIM_ADAPTER = REPO_ROOT / "_ssim_to_payload.py"
SSIM_TIMEOUT_S = 300

# Экспорт «Меридиана» — десятки мегабайт JSON; разбор + конвертация на
# реальном файле занимают ~1 минуту, потолок с запасом. Тело запроса
# ограничено (299-МБ выгрузка не должна стать способом уронить процесс),
# а временные входные файлы живут в data/tmp — внутри data/, под
# gitignore, на одном томе с raw-зоной: персональные данные исходника не
# транзитом через системный %TEMP% (ревью 2026-07-24).
MERIDIAN_ADAPTER = REPO_ROOT / "_meridian_to_payload.py"
MERIDIAN_TIMEOUT_S = 600
IMPORT_BODY_MAX_BYTES = int(os.environ.get("CREW_MAX_IMPORT_BODY_MB",
                                           "256")) * 1024 * 1024
IMPORT_TMP_DIR = REPO_ROOT / "data" / "tmp"

# Канал приёма пакета постановки (CDPM -> COSMOS): пакеты — единицы
# мегабайт, импортный потолок 256 МБ здесь избыточен — у машинного
# пути свой, меньший лимит тела.
CHANNEL_BODY_MAX_BYTES = int(os.environ.get("CREW_CHANNEL_MAX_MB",
                                            "64")) * 1024 * 1024

_SKIP_MIGRATION_ENV = "COSMOS_SKIP_CONFIG_MIGRATION"


def _materialize_instance_configs() -> None:
    """Разовый перенос конфигурации к наборам (P1-10). Идёт в фоне и не
    имеет права ронять приложение: набор без своего файла продолжает
    работать по шаблону установки, и на экране это честно подписано.

    В тестах отключается переменной COSMOS_SKIP_CONFIG_MIGRATION: фоновый
    поток, пишущий файлы параллельно проверкам, — это ровно та
    недетерминированность, на которой уже обожглись 2026-07-26. Тесту,
    которому перенос нужен, положено звать `materialize_all()` явно."""
    if os.environ.get(_SKIP_MIGRATION_ENV):
        return
    # Печать — через `say`: поток не владеет выбором stdout и не имеет
    # права умереть на сообщении о собственном успехе (см. _cli_console).
    try:
        res = config_scope.materialize_all()
    except Exception as exc:                        # noqa: BLE001
        say(f"config_scope: перенос не выполнен: {exc}")
        return
    if res["migrated"] or res["failed"]:
        say(f"config_scope: конфигурация выдана наборам "
            f"{len(res['migrated'])} из {res['instances']}"
            + (f", не удалось: {res['failed']}" if res["failed"] else ""))


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    mode = auth.get_auth_mode()
    if mode == auth.MODE_TOKEN:
        if not os.environ.get("CREW_AUTH_MODE", "").strip():
            print("WARNING: neither CREW_AUTH_MODE nor CREW_ACCESS_AUD is "
                  "set -> falling back to the legacy shared-token auth mode",
                  flush=True)
        # First start: create auth.json and print the access token once.
        auth.ensure_auth_config()
    else:
        # Access/dev modes authorize identities against the user catalog.
        users.ensure_db()
    # P1-10 (2026-07-26): у каждого набора своя конфигурация. Перенос
    # идемпотентен и чист — набору записывается то, что он и так получал,
    # поэтому отпечатки и посчитанные артефакты не меняются. Делается на
    # старте, чтобы после раската инвариант держался на всех контурах без
    # ручного шага, но ОТДЕЛЬНЫМ ПОТОКОМ: перечень наборов разбирает
    # каждую выгрузку, а их на боевом контуре сотни — старт приложения
    # ждать этого не должен.
    threading.Thread(target=_materialize_instance_configs,
                     name="config-materialize", daemon=True).start()
    if mode == auth.MODE_ACCESS:
        # Telegram access-request moderation (no-op until CREW_TG_BOT_TOKEN
        # and CREW_TG_CHAT_ID are configured).
        telegram_bot.start_polling_thread()
        # COSMOS Evolution nightly scheduler (no-op unless enabled). The
        # module is deployed separately — import it lazily HERE, never at
        # app import time.
        from . import evolution_runner
        evolution_runner.start_scheduler_thread()
        from . import change_requests
        change_requests.recover_interrupted()
        change_requests.start_scheduler_thread()
        # Third-party audit inbox poller (no-op unless CREW_AUDIT_POLL=1;
        # enabled on the Test contour only, like the CR scheduler).
        from . import audit_inbox
        audit_inbox.start_poller_thread()
    # Сторож сирот кнопки «Пакет постановки» (аудит A10, 04.09.2026):
    # расчёт живёт в нити этого процесса; после гибели процесса запись
    # «идёт» без нити заперла бы кнопку навсегда — при старте такие
    # записи помечаются «прервано перезапуском» словами.
    from . import package_runs
    for line in package_runs.recover_orphans():
        print("COSMOS: осиротевшее нажатие помечено — " + line, flush=True)
    # Сверка заявок каталога шаблонов (шаг 6): решение владельца по
    # заявке доходит до очереди CR без нажатия кнопки. ВНЕ ветки режима
    # Access: локальное рабочее место владельца идёт в режиме token, и
    # именно оно пишет ключи CR в каталог. Отдельной нитью и только при
    # настроенном CREW_CATALOG_API_URL — без машинного канала фоновых
    # походов в сеть не бывает, а старт не ждёт службы.
    from . import catalog_pipeline
    catalog_pipeline.start_sync_thread()
    yield


app = FastAPI(title="COSMOS pipeline", version="1.2", lifespan=_lifespan)


# ---------------------------------------------------------------- auth layer

# Paths that must work WITHOUT an identity: the login API, the login page
# and the stylesheet both public pages need. Everything else requires an
# identity resolved by auth.authenticate_request for the current mode.
PUBLIC_PATHS = {"/api/login", "/login", "/login.html", "/style.css",
                "/favicon.ico", "/unauthorized.html", "/logo.svg",
                # язык по умолчанию нужен и странице входа — до какой-либо
                # личности; в ответе один код языка, скрывать нечего
                "/ui-config.js"}
# Точки наблюдения. Имена с «z» намеренно: `/health` и `/version` без
# суффикса — обычные пути статики, и на них уже опирались наблюдатели,
# получая страницу приложения; новые имена не могут быть спутаны со
# старым поведением.
_OBSERVABILITY_PATHS = {"/healthz", "/readyz", "/versionz"}

# Машинный путь канала CDPM -> COSMOS: личности пользователя у машины
# нет — аутентификация токеном канала В САМОМ обработчике (env
# CREW_CHANNEL_TOKEN, сверка постоянным временем; без переменной канал
# выключен и отвечает отказом словами — fail-closed). Отдельное
# множество, НЕ PUBLIC_PATHS: те — про страницы входа человека.
_MACHINE_PATHS = {"/api/task-package/receive"}
# Ручки расчёта по пакету: личность сессии ЛИБО токен канала
# (X-Channel-Token, CREW_CHANNEL_TOKEN) — раннеры серий и ночная
# регрессия на площадках идут без сессии (заявка CDPM 04.09). Проверку
# делает сам обработчик (_user_or_channel), fail-closed.
_MACHINE_PREFIXES = ("/api/task-package/upload",
                     "/api/task-package/current/press",
                     # …/export покрывает и export-mps, и выгрузки решений
                     # (CSV/XLSX/JSON/iCal/Meridian, 07.09.2026): раннеры
                     # площадок забирают файлы без сессии — по токену.
                     "/api/task-package/current/export",
                     "/api/task-package/current/roster-map")
# Прогоны COSMOS (07.09.2026): машинный вход по токену — ТОЛЬКО к
# перечисленным парам (метод, образец пути); сессионные ручки (PUT
# current, order, archive, title, init, POST …/replay) с токеном без
# сессии получают 401/403 от промежуточного ПО, не 500. Префиксы выше
# не расширяются.
_MACHINE_ROUTES = (
    ("GET", re.compile(r"^/api/cosmos-runs$")),
    ("POST", re.compile(r"^/api/cosmos-runs$")),
    ("GET", re.compile(r"^/api/cosmos-runs/current$")),
    ("GET", re.compile(r"^/api/cosmos-runs/run-\d{4}$")),
    ("GET", re.compile(r"^/api/cosmos-runs/run-\d{4}/attempts/[A-Za-z0-9_-]+"
                       r"(?:/file/[^/]+|/exchanges(?:/\d+)?"
                       r"|/export(?:/[^/]+/[^/]+)?|/replay-report)?$")),
    # Запись о прогоне (проект 10.09.2026, раздел 10): раннер наборов и
    # панель площадки объявляют прогон, читают карточку, сшивают журнал
    # и запускают сверку с ИДАК БЕЗ сессии человека — по токену канала,
    # fail-closed. Находка 10.09.2026: сторож площадок сверку по каналу
    # запустить не мог, и «ПРОВЕРЕНО раннером» упиралось в вход.
    ("POST", re.compile(r"^/api/run/declare$")),
    ("GET", re.compile(r"^/api/run/current$")),
    # Среда COSMOS для ГОЛОВЫ записи (договор с CDPM 11.09.2026): её
    # спрашивает хозяин записи — панель площадки или раннер наборов —
    # ДО чеканки объявления, и оба идут без сессии человека.
    ("GET", re.compile(r"^/api/run/environment$")),
    ("POST", re.compile(r"^/api/run/stitch$")),
    ("POST", re.compile(r"^/api/source-check/run$")),
    ("GET", re.compile(r"^/api/source-check/last$")),
    ("GET", re.compile(r"^/api/cosmos-runs/run-\d{4}/record$")),
    # Дерево прогонов (проект 10.09.2026, раздел 12): ЧТЕНИЕ дерева и
    # перестройка индекса открыты машинному читателю — раннер наборов
    # сверяет ветвь с золотым прогоном и перестраивает индекс после
    # закрытия прогона. Ручка ЗОЛОТОГО прогона в этот перечень НЕ
    # входит: признак ставит человек (решение владельца 6.3), и токен
    # канала на ней — отказ 403.
    ("GET", re.compile(r"^/api/run-tree$")),
    ("POST", re.compile(r"^/api/run-tree/rebuild$")),
    ("GET", re.compile(r"^/api/run-tree/compare$")),
    ("GET", re.compile(r"^/api/run/current/tree$")),
    ("GET", re.compile(r"^/api/cosmos-runs/run-\d{4}/tree$")),
    # Общий экран прогонов обеих систем — тоже только чтение.
    ("GET", re.compile(r"^/api/runs-all$")),
)


def _machine_route(method: str, path: str) -> bool:
    return any(m == method and rx.match(path) for m, rx in _MACHINE_ROUTES)

# Friendly pages for access mode: the visitor passed Cloudflare's email
# OTP but is not in the app's own user catalog. The page depends on the
# visitor's access-request status; texts are bilingual (RU + EN). Served
# inline so no static asset is needed for anonymous requests.
_UNAUTHORIZED_TEXTS = {
    # status -> (ru_paragraph, en_paragraph)
    None: (
        "Ваш email прошёл проверку Cloudflare, но не включён для этого "
        "приложения (или отозван). Попросите администратора добавить ваш "
        "адрес в панели «Пользователи».",
        "Your email passed Cloudflare verification, but it is not enabled "
        "for this application (or was revoked). Ask an administrator to "
        "add your address in the admin panel."),
    access_requests.STATUS_PENDING: (
        "Запрос на доступ отправлен администратору. Когда решение будет "
        "принято, вы получите письмо на этот адрес; после одобрения просто "
        "войдите ещё раз.",
        "Your access request has been sent to the administrator. You will "
        "receive an email at this address once a decision is made; after "
        "approval simply sign in again."),
    access_requests.STATUS_INFO_REQUESTED: (
        "Администратор запросил у вас дополнительную информацию — "
        "проверьте почту (включая папку «Спам») и ответьте на письмо. "
        "После вашего ответа будет принято решение.",
        "The administrator asked you for additional information — check "
        "your mailbox (including Spam) and reply to that email. A decision "
        "will be made after your reply."),
    access_requests.STATUS_DENIED: (
        "К сожалению, в доступе отказано. Если вы считаете это ошибкой, "
        "свяжитесь с администратором.",
        "Unfortunately, access has been denied. If you believe this is a "
        "mistake, contact the administrator."),
    # approved but not yet in the catalog would be a wiring bug; the
    # generic text (None) covers it via fallback.
}


def _unauthorized_page(status: str | None) -> str:
    ru, en = _UNAUTHORIZED_TEXTS.get(status, _UNAUTHORIZED_TEXTS[None])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>COSMOS: access</title>
<link rel="icon" type="image/svg+xml" href="/logo.svg">
<link rel="stylesheet" href="/style.css"></head>
<body><main style="max-width:36rem;margin:4rem auto;font-family:sans-serif">
<div style="display:flex;align-items:center;gap:10px">
<img src="/logo.svg" alt="" style="width:36px;height:36px"><h1 style="margin:0">COSMOS</h1>
</div>
<h2>Access not granted yet</h2>
<p>{en}</p>
<p><a href="/cdn-cgi/access/logout">Sign out and try another email</a></p>
<h2>Доступ пока не предоставлен</h2>
<p>{ru}</p>
<p><a href="/cdn-cgi/access/logout">Выйти и попробовать другой email</a></p>
</main></body></html>
"""


def _register_access_request(request: Request) -> str | None:
    """Register (idempotently) an access request for a request whose
    identity was verified but is not in the catalog; notify the admin via
    Telegram exactly once (on creation). Returns the request status, or
    None when there is no verified candidate email (e.g. token mode).
    Never raises — a broken notification channel must not break the
    unauthorized page."""
    email = getattr(request.state, "denied_email", None)
    if not email:
        return None
    try:
        created, row = access_requests.register(email)
    except Exception as exc:                       # noqa: BLE001 — fail soft
        print(f"WARNING: access-request registration failed for "
              f"{email!r}: {exc}", flush=True)
        return None
    if created:
        threading.Thread(target=telegram_bot.notify_access_request,
                         args=(row,), daemon=True,
                         name="cosmos-tg-notify").start()
    return row["status"]


class RunsDashboardWSGuard:
    """The «Прогоны» Panel dashboard streams its live document — every
    tenant's runs and fleet KPIs — over a WEBSOCKET (/runs-dashboard/ws).
    @app.middleware('http') runs ONLY for http scopes, so the HTTP admin gate
    in require_identity never sees that socket: a non-admin (or any principal
    past the Access edge) could open it and receive all-tenant data. This
    pure-ASGI guard authenticates the websocket handshake and rejects
    non-admins before Panel serves a document (audit 2026-07-15). Http scopes
    pass straight through (already gated in require_identity)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket" and \
                scope.get("path", "").startswith("/runs-dashboard"):
            conn = HTTPConnection(scope)
            try:
                user, _ = auth.authenticate_request(conn)
            except Exception:                          # noqa: BLE001
                user = None
            if user is None or user.get("role") != users.ROLE_ADMIN:
                await receive()                        # consume websocket.connect
                await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


app.add_middleware(RunsDashboardWSGuard)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """Behind Cloudflare, static assets (.js/.css/.html) are edge-cached by
    default, so a redeploy would keep serving stale UI until the CDN TTL
    expires. Mark them must-revalidate so the browser and the CDN always
    check the origin (304 when unchanged) — correctness over a marginal
    caching win for a small app. Runs OUTSIDE the auth middleware."""
    response = await call_next(request)
    path = request.url.path
    if (path == "/" or path.endswith((".js", ".css", ".html"))):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.middleware("http")
async def require_identity(request: Request, call_next):
    path = request.url.path
    # Наблюдение за живостью НЕ требует входа (P2-05, R5-09): проверка,
    # которая просит аутентификации, для наблюдателя бесполезна, а общий
    # обработчик статики отдавал на эти пути страницу приложения с кодом
    # 200 — «здоров» получался всегда, включая сломанный контур. Наружу
    # уходят только состояния и отпечатки: ни путей, ни настроек, ни
    # данных заказчика.
    if path in _OBSERVABILITY_PATHS:
        return await call_next(request)
    if path == "/unauthorized.html":
        # The Access session cookie travels with this request too — resolve
        # the visitor's request status so the page says what is happening.
        user, _reason = auth.authenticate_request(request)
        if user is not None:
            return RedirectResponse(url="/", status_code=302)
        return HTMLResponse(_unauthorized_page(
            _register_access_request(request)))
    if path in PUBLIC_PATHS:
        return await call_next(request)
    if path in _MACHINE_PATHS:
        # Токен канала проверяет сам обработчик (fail-closed);
        # остальные /api/* по-прежнему требуют личность.
        return await call_next(request)
    if (path.startswith(_MACHINE_PREFIXES)
            or _machine_route(request.method, path)) \
            and request.headers.get("X-Channel-Token"):
        # Машинный вход к ручкам расчёта по пакету и чтению прогонов —
        # только при предъявленном токене; без заголовка путь идёт как
        # обычный /api/* (личность сессии).
        return await call_next(request)
    user, reason = auth.authenticate_request(request)
    if user is not None:
        # Uniform request identity for all downstream routes/tenancy.
        request.state.user = user
        # The rich «Прогоны» dashboard (Panel, mounted in-process) shows ALL
        # runs, so it is admin-only — gated HERE, where identity is already
        # re-verified, because the mount bypasses normal route dependencies.
        if path.startswith("/runs-dashboard") and \
                user.get("role") != users.ROLE_ADMIN:
            return JSONResponse(status_code=403,
                                content={"detail": "admin role required"})
        return await call_next(request)
    if path.startswith("/api/"):
        if reason == "forbidden":
            status = _register_access_request(request)
            return JSONResponse(
                status_code=403,
                content={"detail": "this account is not authorized for the "
                                   "application (missing from the user "
                                   "catalog or revoked)",
                         "request_status": status})
        return JSONResponse(status_code=401,
                            content={"detail": "authentication required"})
    if auth.get_auth_mode() == auth.MODE_TOKEN:
        return RedirectResponse(url="/login", status_code=302)
    if reason == "forbidden":
        _register_access_request(request)
    return RedirectResponse(url="/unauthorized.html", status_code=302)


@app.post("/api/login")
def api_login(body: dict = Body(...)):
    token = body.get("token")
    if not isinstance(token, str) or not auth.verify_token(token):
        raise HTTPException(401, "invalid token")
    sid = auth.create_session()
    activity_log.record(auth.TOKEN_MODE_IDENTITY["email"], "login", "token")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        auth.COOKIE_NAME, sid,
        httponly=True, samesite="lax", path="/",
        max_age=auth.SESSION_TTL_DAYS * 86400,
        # Behind Cloudflare/TLS set CREW_WEBAPP_COOKIE_SECURE=1 so the
        # cookie is never sent over plain HTTP.
        secure=os.environ.get("CREW_WEBAPP_COOKIE_SECURE") == "1")
    return resp


@app.post("/api/logout")
def api_logout(request: Request):
    auth.destroy_session(request.cookies.get(auth.COOKIE_NAME))
    activity_log.record(auth.TOKEN_MODE_IDENTITY["email"], "logout", "token")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


# ------------------------------------------------------------ activity log

# The complete set of leaf tab ids (mirrors TAB_LABEL / panel-<id> in the
# SPA). The nav beacon is validated against THIS allow-list, not a charset
# regex: str.isalnum() is Unicode-wide («боард», CJK) and a bare ASCII
# regex still admits prototype names like "constructor" — both would let an
# authenticated user pollute the admin journal (audit 2026-07-16).
_KNOWN_TABS = frozenset({
    "flow",
    # «Прогоны» COSMOS (07.09.2026) — вкладка сразу после «Конвейера».
    "cruns",
    "data", "format", "jobs", "runs", "results", "scenarios", "board",
    "vacation", "quals", "bases", "cabinet", "demo", "ask", "suggest",
    "learn", "crs", "lab", "kb", "users", "activity", "ops", "llmreg",
    "rules", "studio", "settings", "loops",
})


@app.post("/api/activity/nav")
def api_activity_nav(request: Request, body: dict = Body(...)):
    """SPA tab-switch beacon. Any authenticated user; the tab id must be a
    known leaf tab so the journal cannot be polluted with arbitrary text."""
    user = _user(request)
    tab = body.get("tab")
    if not isinstance(tab, str) or tab not in _KNOWN_TABS:
        raise HTTPException(400, "bad tab")
    activity_log.record(user["email"], "nav", tab)
    return {"ok": True}


@app.post("/api/activity/logout")
def api_activity_logout(request: Request):
    """Logout beacon for Cloudflare Access mode: the real logout happens
    at the CF edge (/cdn-cgi/access/logout), which this app never sees —
    the frontend fires this beacon first so the journal keeps the event."""
    user = _user(request)
    activity_log.record(user["email"], "logout", auth.get_auth_mode())
    return {"ok": True}


@app.get("/api/activity")
def api_activity(request: Request, limit: int = 200, email: str = "",
                 event: str = ""):
    """Admin-only journal view (newest first, filters optional)."""
    user = _user(request)
    if user.get("role") != users.ROLE_ADMIN:
        raise HTTPException(403, "admin only")
    return {"events": activity_log.recent(
        limit=limit, email=email or None, event=event or None)}


@app.get("/api/me")
def api_me(request: Request):
    """Signed-in identity for the frontend: email, role, the auth mode
    (access/token/dev) so the UI can show the right logout affordance and
    gate the admin tab, plus tour_seen for the onboarding tour (null when
    the identity has no catalog row — the token mode's shared operator)."""
    user = _user(request)
    tour_seen = None
    try:
        row = users.get_user(user["email"])
    except ValueError:
        row = None
    if row is not None:
        tour_seen = bool(row.get("tour_seen_at"))
    return {"email": user["email"], "role": user.get("role", "user"),
            "mode": auth.get_auth_mode(), "tour_seen": tour_seen,
            "is_super": _is_super(user),
            "product_version": _product_version()}


def _product_version() -> str | None:
    """Номер версии продукта для подписи внизу интерфейса (РВ-141): одна
    настройка площадки — `CREW_PRODUCT_VERSION` пусковика, общая для
    COSMOS, CDPM и каталога. Не задана — None и подписи нет: номера в
    коде нет."""
    return (os.environ.get("CREW_PRODUCT_VERSION") or "").strip() or None


@app.post("/api/me/tour-seen")
def api_tour_seen(request: Request):
    """Mark the onboarding tour as seen for the signed-in user. The token
    mode's synthetic operator has no catalog row BY DESIGN — return ok with
    tour_seen null so the frontend falls back to local storage."""
    user = _user(request)
    try:
        users.mark_tour_seen(user["email"])
    except (users.UserNotFoundError, ValueError):
        return {"ok": True, "tour_seen": None}
    return {"ok": True, "tour_seen": True}


@app.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html", media_type="text/html")


# Языки интерфейса — зеркало перечня LANGS в webapp/static/i18n.js.
_UI_LANGS = ("ru", "en", "uz", "kk", "ar")


@app.get("/ui-config.js", include_in_schema=False)
def ui_config_js():
    """Язык интерфейса по умолчанию для ЭТОЙ установки.

    Выбор языка хранится в браузере, но при первом заходе браузеру не из
    чего выбирать — прежде жёстко бралось «ru». Установщик спрашивает
    язык первым вопросом и кладёт его в CREW_DEFAULT_LANG; страницы
    подключают этот сценарий до i18n.js, и первый заход открывается на
    языке установки. Неизвестное значение тихо падает в «ru» — язык не
    та настройка, из-за которой приложение вправе не открыться.
    """
    lang = os.environ.get("CREW_DEFAULT_LANG", "").strip().lower()
    if lang not in _UI_LANGS:
        lang = "ru"
    return Response('window.CREW_DEFAULT_LANG = "%s";\n' % lang,
                    media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


# Admin user-catalog API lives in its own router (webapp/admin_api.py).
app.include_router(admin_api.router)
# Admin-only Evolution findings list (GET /api/evolution, same module).
app.include_router(admin_api.evolution_router)
# Read-only onboarding demos (webapp/demo.py): any authenticated user.
app.include_router(demo.router)

# «Прогоны» rich view: a Panel dashboard mounted IN-PROCESS at /runs-dashboard
# (same port, same Cloudflare Access edge, admin-gated in require_identity).
# Wrapped so a venv without panel/bokeh-fastapi still boots — the SPA simply
# won't offer the rich view. Verified on the exact prod stack before adoption.
try:
    from panel.io.fastapi import add_applications as _add_panel
    from .runs_dashboard import make_dashboard as _runs_dashboard
    _add_panel({"/runs-dashboard": _runs_dashboard}, app=app, title="Прогоны")
except Exception as _exc:                               # noqa: BLE001
    print(f"WARNING: «Прогоны» dashboard not mounted ({_exc})", flush=True)


# ---------------------------------------------------------------- tenancy

# Ownership model (2026-07-13): nothing is globally shared. The seed
# benchmark months (GERAD 1-7, BTS day) are owned by the primary admin;
# running a stage on one OVERWRITES its canonical artifacts (git holds the
# versions). The note warns the owner/admin about that overwrite.
SEED_OVERWRITE_NOTE = (
    "внимание: это эталонные данные — запуск этапа перезапишет их "
    "канонические артефакты (историю версий хранит git)")


def _user(request: Request) -> dict:
    """request.state.user = {email, role}: guaranteed by the auth middleware
    in EVERY mode (access/token/dev). Its absence past the middleware is a
    wiring bug — loud 500, never a silent anonymous identity."""
    user = getattr(request.state, "user", None)
    if not isinstance(user, dict) or not user.get("email"):
        raise HTTPException(
            500, "auth middleware did not attach request.state.user")
    return user


def _user_or_channel(request: Request) -> dict:
    """Личность для ручек расчёта по пакету: сессия (как _user) либо
    токен канала X-Channel-Token — тогда личность машинная
    {email: "channel", role: "machine"}. Токен предъявлен, но не
    совпал или канал выключен — 403 без детализации причины."""
    token = request.headers.get("X-Channel-Token")
    if token:
        from . import task_channel
        if task_channel.token_state(token) != "ok":
            raise HTTPException(403, "доступ запрещён")
        return {"email": "channel", "role": "machine"}
    return _user(request)


def _is_admin(user: dict) -> bool:
    return user.get("role") == "admin"


def _is_super(user: dict) -> bool:
    """The single super admin (settings.SUPER_ADMIN_EMAIL — first address
    of CREW_SUPER_ADMIN, no default in code; the token-mode operator counts
    too). Email compare — NOT a DB role, which the users.db CHECK
    constraint would reject."""
    return settings.is_super_admin(user)


def _job_visible(job: dict, user: dict) -> bool:
    """Owner-or-admin; callers answer 404 (not 403) so other users' job ids
    are indistinguishable from nonexistent ones."""
    return _is_admin(user) or job.get("owner") == user["email"]


def _bump_user_stat(email: str, stat: str) -> None:
    """Bump a per-user usage counter ('uploads' / 'jobs_run') in the user
    catalog. The token mode's synthetic operator identity has no catalog
    row BY DESIGN (a shared credential is not per-user attributable), so
    its misses are skipped; in access/dev modes the identity came from the
    catalog, so a missing row is a real wiring bug and stays loud."""
    try:
        users.bump_stat(email, stat)
    except users.UserNotFoundError:
        if auth.get_auth_mode() != auth.MODE_TOKEN:
            raise


# ------------------------------------------------------------- instances

def _titles_path():
    return _data_paths.data_root() / "instance-titles.json"


def _titles() -> dict:
    """Названия наборов, введённые человеком.

    Идентификатор строится из имени преобразованием в безопасный вид
    («Test August» -> `upload-<slug>-3b30e7--test-avgust`), и до сих пор
    само имя нигде не сохранялось — в списке показывать было нечего.
    Идентификатор нужен системе; человеку нужно его название.
    """
    # utf-8-sig, а не utf-8: файл может прийти с меткой кодировки в
    # начале (её ставят редакторы и часть консольных команд), и тогда
    # разбор падает, а названия молча исчезают — ровно это и случилось
    # при первом заполнении.
    try:
        return json.loads(_titles_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _remember_title(inst_id: str, name: str) -> None:
    t = _titles()
    t[inst_id] = name
    p = _titles_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(t, ensure_ascii=False, indent=1),
                 encoding="utf-8")


@app.get("/api/instances")
def api_instances(request: Request):
    # Shared instances + the caller's own uploads; admins see every upload
    # (each instance carries its "owner" annotation).
    items = registry.list_instances_for(_user(request))
    titles = _titles()
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and titles.get(it.get("id")):
            it["title"] = titles[it["id"]]
    return items


@app.post("/api/instances/upload")
def api_upload(request: Request, body: dict = Body(...)):
    user = _user(request)
    name = body.get("name")
    payload = body.get("payload")
    if not isinstance(name, str) or not name.strip():
        return JSONResponse(status_code=422, content={"validation": {
            "ok": False, "errors": ["'name' must be a non-empty string"]}})
    # Выгрузка авиакомпании принимается наравне с каноническим набором:
    # преобразование выполняется ЗДЕСЬ, тем же модулем, что и в командной
    # строке, поэтому «загрузили через браузер» и «посчитали вручную» не
    # могут разойтись. Формат объявляется явно, а не угадывается: файл,
    # присланный не в том виде, должен получить внятный отказ, а не
    # молча превратиться в неполное расписание.
    ingest_report = None
    if body.get("format") == "airline":
        try:
            import airline_ingest
            payload, ingest_report = airline_ingest.convert(
                json.dumps(payload).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — причина уходит клиенту
            return JSONResponse(status_code=422, content={"validation": {
                "ok": False,
                "errors": ["преобразование выгрузки: %s" % exc]}})
    # slugify_or_hash: names in any UI language (Cyrillic, Arabic, …) yield a
    # usable slug — never an empty-slug 422 (audit 2026-07-15).
    slug = registry.slugify_or_hash(name)
    inst_id, _label, target = registry.upload_target(user["email"], slug)
    validation = registry.validate_payload(payload)
    if not validation["ok"]:
        # Nothing is saved on validation failure (contract).
        return JSONResponse(status_code=422, content={"validation": validation})
    # Atomic claim: ownership check + exclusive payload write + owner row
    # under one lock. The user slug embeds an email digest, so a taken id is
    # always the caller's OWN namespace — no cross-tenant existence leak
    # (audit 2026-07-19 P0-03). Seed instances never start with 'upload-'.
    try:
        registry.claim_upload(inst_id, target, payload, user["email"])
    except registry.UploadExists:
        return JSONResponse(status_code=422, content={"validation": {
            "ok": False, "errors": [f"id '{inst_id}' already exists"]}})
    _bump_user_stat(user["email"], "uploads")
    _remember_title(inst_id, name.strip())
    out = {"id": inst_id, "validation": validation}
    if ingest_report is not None:
        out["ingest"] = ingest_report
    return out


@app.post("/api/audit/pairing")
def api_audit_pairing(request: Request, body: dict = Body(...)):
    """Приёмка каталога цепочек тем же кодом, что и в командной строке.

    Каталог и набор берутся по путям из тела запроса либо, если они не
    заданы, самые свежие из каталога данных контура. Правила — тот же
    документ постановки, по которому шёл расчёт: приёмка обязана судить
    по постановке, а не по своим представлениям о ней.
    """
    _user(request)
    import pairing_audit
    root = _data_paths.data_root()
    doc = root / "rules" / "postanovka.md"
    if not doc.exists():
        return JSONResponse(status_code=422, content={
            "error": "документ постановки не загружен — судить не по чему"})

    def newest(patterns, given):
        """Самый свежий файл по любому из имён.

        Имён несколько потому, что каталог цепочек называется по-разному
        в зависимости от линии расчёта (`pairing_*` у построителя,
        `*selected*` у линии колонок), а набор данных приходит либо
        подготовленным файлом, либо загруженным экземпляром. Одно имя в
        поиске означало бы отказ приёмки на половине прогонов — ровно
        это и случилось на первом запуске из интерфейса.
        """
        if given:
            p = Path(given)
            return p if p.is_absolute() else root / given
        found: list = []
        for pat in patterns:
            found += [p for p in root.rglob(pat) if p.is_file()]
        found.sort(key=lambda p: p.stat().st_mtime)
        return found[-1] if found else None

    cat = newest(("pairing*.json", "*selected*.json"), body.get("catalog"))
    pay = newest(("payload*.json", "uploads/*/*.json"), body.get("payload"))
    missing = [n for n, v in (("каталог цепочек", cat),
                              ("набор данных", pay)) if v is None]
    if missing:
        return JSONResponse(status_code=422, content={
            "error": "не найдено: %s. Каталог ищется по именам "
                     "pairing*.json и *selected*.json, набор — "
                     "payload*.json и uploads/*/*.json в каталоге данных "
                     "контура." % ", ".join(missing)})
    try:
        res = pairing_audit.audit(cat, pay, doc)
    except Exception as exc:  # noqa: BLE001 — причина уходит клиенту
        return JSONResponse(status_code=422,
                            content={"error": "приёмка не выполнена: %s" % exc})
    res["catalog"] = cat.name
    res["payload"] = pay.name
    return res


@app.get("/api/rules")
def api_rules_get(request: Request):
    """Действующий документ постановки контура."""
    _user(request)
    p = _data_paths.data_root() / "rules" / "postanovka.md"
    if not p.exists():
        return {"loaded": False}
    import _cos_rules
    raw = p.read_bytes()
    info = {"loaded": True, "bytes": len(raw),
            "sha": hashlib.sha256(raw).hexdigest()[:16]}
    try:
        r = _cos_rules.load_bytes(raw, where=p.name)
        ml = r.monthly_flight_time_limit_min
        # Поле None — документ правила не несёт (пакет «умолчания-в-
        # данные»): показывать словами, как месячный предел ниже, а не
        # падать на list(None) и не печатать null за число заказчика.
        _nd = "не задан документом"
        info["rules"] = {
            "минимальная стыковка, мин":
                (r.min_sit_min if r.min_sit_min is not None else _nd),
            "предел цепочки, календарных дат":
                (r.max_pairing_days
                 if r.max_pairing_days is not None else _nd),
            "посадок за смену": r.max_landings_per_duty,
            "предел смены по ярусам, мин":
                (list(r.work_time_limit_by_tier)
                 if r.work_time_limit_by_tier is not None else _nd),
            # Месячный предел — ростеринговое правило документа; отчёт
            # обязан показывать и его (проверка инструкции 2026-08-12:
            # строка была только в контракте, но не на экране).
            "месячный предел налёта, мин":
                (f"{ml} ({ml // 60} ч)" if ml else "не задан документом"),
            "забросок на рейс": r.max_dh_per_flight,
            "забросок в день на направление": r.max_dh_per_day,
            "гранула суток": r.dh_day_scope,
            "предмет счёта перевозок": r.dh_limit_counts,
            "прочитано ключей": len(r.source_keys_read),
        }
    except Exception as exc:  # noqa: BLE001 — причина уходит клиенту
        info["error"] = str(exc)
    return info


@app.post("/api/rules/upload")
def api_rules_upload(request: Request, body: dict = Body(...)):
    """Приём документа постановки (CMD-GPT-FINAL-FULL-*.md).

    Документ, который не разбирается, НЕ сохраняется: иначе расчёт
    молча ушёл бы на умолчания, а отпечаток объявил бы их правилами
    заказчика. Это тот самый класс, который уже стоил одного прогона.
    """
    _user(request)
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return JSONResponse(status_code=422,
                            content={"error": "пустой документ"})
    import _cos_rules
    raw = text.encode("utf-8")
    try:
        _cos_rules.load_bytes(raw, where="документ постановки")
    except Exception as exc:  # noqa: BLE001 — причина уходит клиенту
        return JSONResponse(status_code=422, content={
            "error": "документ отклонён: %s" % exc})
    d = _data_paths.data_root() / "rules"
    d.mkdir(parents=True, exist_ok=True)
    (d / "postanovka.md").write_bytes(raw)
    return api_rules_get(request)


@app.get("/api/instances/{inst_id}")
def api_instance(inst_id: str, request: Request):
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    payload = registry.load_payload(registry.instance_payload_path(inst))
    return {**inst, "validation": registry.validate_payload(payload)}


# --------------------------------------------------- day-of-ops plan vs fact

@app.get("/api/ops/{inst_id}")
def api_ops(inst_id: str, request: Request):
    """Plan-vs-fact report (deviations table + operational KPIs) for one
    instance. Owner-or-admin; another user's instance is 404."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    payload = registry.load_payload(registry.instance_payload_path(inst))
    return plan_fact.report(payload, ops_log.facts_for(inst_id))


@app.post("/api/ops/{inst_id}/fact")
def api_ops_fact(inst_id: str, request: Request, body: dict = Body(...)):
    """Create or replace the fact for one flight (manual day-of-ops entry).
    Body: {flightNumber, dep_delay_min?, arr_delay_min?, cancelled?,
    diverted?, cause?, note?}."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    payload = registry.load_payload(registry.instance_payload_path(inst))
    try:
        # Аудит-2 (P1-05b): ключ факта = номер+день при повторном номере;
        # повторный номер без serviceDay — 400, а не действие на все даты.
        key = ops_log.fact_key_for(payload, body.get("flightNumber"),
                                   body.get("serviceDay"))
        # P0-2: во множество допустимых ключей не входят внутриминутно-
        # коллизионные (fact_key_for их уже отверг — здесь оборона в глубину)
        valid = {k for k, v in ops_log.payload_fact_keys(payload).items()
                 if not v.get("ambiguous")}
        fact = ops_log.upsert_fact(inst_id, valid, key, body,
                                   by=user["email"])
    except ops_log.OpsLogError as exc:
        raise HTTPException(400, str(exc))
    # Ревью пакета: flightNumber ответа — настоящий номер, ключ — отдельно.
    fact = dict(fact)
    fact["flightNumber"] = key.split("@", 1)[0]
    return {"factKey": key, **fact}


@app.delete("/api/ops/{inst_id}/fact/{flight_number}")
def api_ops_fact_delete(inst_id: str, flight_number: str, request: Request,
                        day: str | None = None):
    """Remove one flight's fact (reverts it to plan). For a flight number
    that operates on several dates pass ?day=YYYY-MM-DD (audit-2 P1-05b)."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    payload = registry.load_payload(registry.instance_payload_path(inst))
    try:
        key = ops_log.fact_key_for(payload, flight_number, day)
    except ops_log.OpsLogError as exc:
        # Ревью пакета: легаси-факт под голым номером теперь-неоднозначного
        # рейса должен оставаться УДАЛЯЕМЫМ (иначе он навечно застревает в
        # хранилище) — разрешаем снос сырого ключа, если он реально есть.
        # P0-2: то же для легаси-факта под внутриминутно-коллизионным
        # ключом 'номер@день-время' — ввод таких фактов теперь запрещён,
        # но старые записи должны оставаться удаляемыми (уборка).
        candidates = [str(flight_number)]
        if day:
            candidates.append(f"{flight_number}@{day}")
        stored = ops_log.facts_for(inst_id)
        for cand in candidates:
            if cand in stored:
                return {"removed": ops_log.remove_fact(inst_id, cand),
                        "legacy_key": True}
        raise HTTPException(400, str(exc))
    return {"removed": ops_log.remove_fact(inst_id, key)}


@app.post("/api/ops/{inst_id}/clear")
def api_ops_clear(inst_id: str, request: Request):
    """Drop every fact for the instance (reset the day). Also removes the
    auto-generated «Сбой» events derived from those facts (H12), so a stale
    disruption cannot outlive the fact that produced it."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    cleared = ops_log.clear(inst_id)
    auto_removed = ops_replay.clear_auto_events(inst_id)
    return {"cleared": cleared, "auto_events_cleared": auto_removed}


_ops_import_lock = threading.Lock()   # one 280 MB BTS scan at a time
# Per-instance lock so the read-clear-insert of generate_disruptions is
# atomic against a concurrent generate on the same instance (H13).
_ops_gen_locks: dict = {}
_ops_gen_locks_guard = threading.Lock()


def _ops_gen_lock(inst_id: str) -> threading.Lock:
    with _ops_gen_locks_guard:
        return _ops_gen_locks.setdefault(inst_id, threading.Lock())


@app.post("/api/ops/{inst_id}/import-bts")
def api_ops_import_bts(inst_id: str, request: Request):
    """Import the factual day from the bundled BTS on-time data, matched to
    this instance's flights. Only meaningful for BTS-sourced instances (a
    random upload can never match BTS keys), so it is gated to source=="bts"
    — that also stops any authenticated user from triggering a pointless
    280 MB scan. A non-blocking lock rejects concurrent scans with 409
    (audit 2026-07-17)."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    if inst.get("source") != "bts":
        raise HTTPException(
            400, "BTS import is only available for BTS-sourced instances")
    if not _ops_import_lock.acquire(blocking=False):
        raise HTTPException(409, "a BTS import is already running")
    try:
        payload = registry.load_payload(registry.instance_payload_path(inst))
        try:
            facts = ops_replay.import_bts(payload)
        except ops_replay.OpsReplayError as exc:
            raise HTTPException(400, str(exc))
        # Ключи фактов: для BTS-экземпляров номера уникальны по конвенции
        # ({carrier}{num}-{HHMM}), поэтому набор ключей совпадает с набором
        # номеров; при повторном номере строка без даты честно пропускается.
        # P0-2: внутриминутно-коллизионные ключи в допустимые не входят
        written = ops_log.bulk_upsert(
            inst_id, facts,
            {k for k, v in ops_log.payload_fact_keys(payload).items()
             if not v.get("ambiguous")},
            by=user["email"])
    finally:
        _ops_import_lock.release()
    return {"imported": written,
            "summary": plan_fact.summary(payload, ops_log.facts_for(inst_id))}


@app.post("/api/ops/{inst_id}/generate-disruptions")
def api_ops_generate_disruptions(inst_id: str, request: Request,
                                 body: dict = Body(default={})):
    """Turn the current facts into «Сбой» events (cancellations + delays)
    so the neighbourhood-rebuild repair can run. Idempotent."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    threshold = body.get("delay_threshold_min",
                         ops_replay.DEFAULT_DELAY_THRESHOLD_MIN)
    try:
        threshold = max(1, int(threshold))       # <1 would abort mid-run
    except (TypeError, ValueError):
        raise HTTPException(400, "delay_threshold_min must be an integer")
    lock = _ops_gen_lock(inst_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "disruption generation already running "
                                 "for this instance")
    try:
        # Ревью пакета: события «Сбоя» строятся только из фактов с ВАЛИДНЫМИ
        # ключами плана — легаси-факт под голым номером теперь-неоднозначного
        # рейса невидим в план-факте и не должен порождать события.
        payload = registry.load_payload(registry.instance_payload_path(inst))
        keys = ops_log.payload_fact_keys(payload)
        facts = {k: v for k, v in ops_log.facts_for(inst_id).items()
                 if k in keys}
        # Трек occurrence-идентичности: события адресуют конкретное
        # вхождение (addr ключа факта), не голый номер.
        result = ops_replay.generate_disruptions(
            inst_id, inst, facts,
            delay_threshold_min=threshold, by=user["email"],
            addr_by_key={k: v.get("addr") for k, v in keys.items()})
    except disruption.DisruptionError as exc:
        raise HTTPException(400, str(exc))
    finally:
        lock.release()
    return result


@app.post("/api/import/analyze")
def api_import_analyze(request: Request, body: dict = Body(...)):
    """Universal import, step 1 (ANALYZE): parse the uploaded files, get a
    mapping plan from the scope-allowed AI model (sample only), apply it
    deterministically and return the pending token + mapping + preview +
    checks. Nothing is saved until /api/import/commit."""
    user = _user(request)
    from . import data_import, llm_backend
    scope = body.get("scope") or "local-only"
    if scope not in registry.LLM_SCOPES:
        raise HTTPException(
            422, f"'scope' must be one of {list(registry.LLM_SCOPES)}")
    items = body.get("files")
    if not isinstance(items, list) or not items:
        raise HTTPException(422, "'files' must be a non-empty list")
    try:
        return data_import.analyze(items, scope, user["email"],
                                   backend_request=body.get("backend"))
    except data_import.ImportError_ as exc:
        return JSONResponse(status_code=422, content={"errors": [str(exc)]})
    except llm_backend.ScopeViolation as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/import/commit")
def api_import_commit(request: Request, body: dict = Body(...)):
    """Universal import, step 2 (COMMIT): the human confirmed the preview —
    save the pending payload as a new instance owned by the caller, with the
    L/C attribute chosen at analyze time."""
    user = _user(request)
    from . import data_import
    token = body.get("token")
    name = body.get("name")
    if not isinstance(token, str) or not token:
        raise HTTPException(422, "'token' must be a string")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(422, "'name' must be a non-empty string")
    slug = registry.slugify_or_hash(name)      # any UI language, never empty
    entry = data_import.pop_pending(token, user["email"])
    if entry is None:
        raise HTTPException(
            404, "import session not found or expired — analyze again")
    inst_id, _label, target = registry.upload_target(user["email"], slug)
    if inst_id in {i["id"] for i in registry.list_instances()}:
        raise HTTPException(422, f"id '{inst_id}' already exists")
    # Blocking gates: canonical validation and semantic ERRORS (warnings
    # are advisory and were shown at preview time).
    if not entry["validation"]["ok"]:
        return JSONResponse(status_code=422,
                            content={"validation": entry["validation"]})
    if entry["sem_errors"]:
        return JSONResponse(status_code=422,
                            content={"errors": entry["sem_errors"]})
    # Immutable raw zone FIRST (Ф3): the exact source bytes + SHA-256
    # manifest. If saving the instance then fails, the zone is discarded —
    # a raw zone must never exist for an instance that was never created.
    from . import raw_zone
    try:
        raw_manifest = raw_zone.store(inst_id, entry.get("raw_files") or [],
                                      user["email"])
    except raw_zone.RawZoneError as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        # Atomic claim closes the check->write->own race the same way as the
        # direct upload endpoint (audit 2026-07-19 P0-03).
        registry.claim_upload(inst_id, target, entry["payload"],
                              user["email"])
        registry.set_llm_scope(inst_id, entry["scope"])
    except registry.UploadExists:
        raw_zone.discard(inst_id)
        raise HTTPException(422, f"id '{inst_id}' already exists")
    except BaseException:
        raw_zone.discard(inst_id)
        raise
    _bump_user_stat(user["email"], "uploads")
    return {"id": inst_id, "llm_scope": entry["scope"],
            "validation": entry["validation"],
            "raw_files": len((raw_manifest or {}).get("files") or [])}


@app.get("/api/instances/{inst_id}/rules")
def api_instance_rules(inst_id: str, request: Request):
    """Правила яруса 1, действующие ДЛЯ ЭТОГО НАБОРА (P1-10).

    Владелец или администратор; чужой набор — 404, а не 403, чтобы факт
    его существования не утекал. Кроме значений отдаётся происхождение
    (шаблон установки / правила набора / правила сценария), значения
    шаблона для сравнения и разметка «ищет решатель или только проверяет
    сертификат» — оператору нужно ровно это различие."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    try:
        d = config_scope.describe(config_scope.DOMAIN_RULES, inst)
    except ValueError as exc:
        # Битый файл правил — отказ ГРОМКИЙ и с текстом причины, а не
        # молчаливый откат к умолчаниям: расчёт по этому файлу тоже упал
        # бы, и подменять причину «всё в порядке» нельзя.
        raise HTTPException(500, f"правила набора не читаются: {exc}") from exc
    return {**d, "instance": inst_id, "label": inst.get("label"),
            "defaults": dict(AIRLINE_PARAM_DEFAULTS),
            "enforcement": {k: _rules_config.enforcement(k)
                            for k in d["values"]}}


@app.put("/api/instances/{inst_id}/rules")
def api_instance_rules_put(inst_id: str, request: Request,
                           body: dict = Body(...)):
    """Задать правила яруса 1 этому набору. Владелец или администратор.

    Значения проверяются ТОЙ ЖЕ `rules_config.validate_value`, что и все
    прочие писатели: собственная проверка здесь дважды расходилась с
    загрузчиком, и набор создавался, а каждый его расчёт падал на старте
    движков.

    Сценарий своими правилами командует через свой, более частный уровень;
    писать ему уровень набора бессмысленно и было бы молчаливо
    бездейственно — поэтому отказ."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    if config_scope.scenario_path(config_scope.DOMAIN_RULES, inst) is not None:
        raise HTTPException(
            409, "у сценария свои правила — правьте их через сценарий, "
                 "иначе правка не подействует")
    params = body.get("parameters") if isinstance(body, dict) else None
    if not isinstance(params, dict):
        raise HTTPException(422, "'parameters' must be a JSON object")
    errors = []
    for key, value in params.items():
        if key not in AIRLINE_PARAM_DEFAULTS:
            errors.append(f"unknown parameter '{key}'")
            continue
        msg = _rules_config.validate_value(key, value)
        if msg is not None:
            errors.append(msg)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    # Файл набора ПОЛНЫЙ: частичная правка ложится на действующие значения
    # этого набора, а не на встроенные — иначе неупомянутый параметр молча
    # откатился бы к умолчанию.
    try:
        current = config_scope.effective(config_scope.DOMAIN_RULES, inst)
    except ValueError as exc:
        raise HTTPException(500, f"правила набора не читаются: {exc}") from exc
    merged = {**current, **params}
    config_scope.write_instance(config_scope.DOMAIN_RULES, inst, merged)
    return api_instance_rules(inst_id, request)


@app.delete("/api/instances/{inst_id}/rules")
def api_instance_rules_reset(inst_id: str, request: Request):
    """Сбросить правила набора к сегодняшнему ШАБЛОНУ установки.

    Именно переписать из шаблона, а не «ходить за шаблоном каждый раз»: у
    набора всегда есть свой полный файл, иначе смысл уровня установки
    снова стал бы плавающим."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    if config_scope.scenario_path(config_scope.DOMAIN_RULES, inst) is not None:
        raise HTTPException(
            409, "у сценария свои правила — сбрасывать нечего")
    config_scope.reset_instance(config_scope.DOMAIN_RULES, inst)
    return api_instance_rules(inst_id, request)


def _instance_rates(inst) -> dict:
    return {
        "instance": inst.get("id"),
        "label": inst.get("label"),
        "cost": config_scope.describe(config_scope.DOMAIN_COST, inst),
        "pay": config_scope.describe(config_scope.DOMAIN_PAY, inst),
    }


@app.get("/api/instances/{inst_id}/rates")
def api_instance_rates(inst_id: str, request: Request):
    """Ставки стоимости и договор оплаты ДЛЯ ЭТОГО НАБОРА (P1-10).

    Ставки — коммерческая тайна авиакомпании: считать стоимость одного
    набора по ставкам, введённым для другого, нельзя. Владелец или
    администратор; чужой набор — 404."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return _instance_rates(inst)


@app.put("/api/instances/{inst_id}/rates")
def api_instance_rates_put(inst_id: str, request: Request,
                           body: dict = Body(...)):
    """Задать ставки этому набору. Тело: {"cost": {...}, "pay": {...}} —
    любая из двух областей необязательна. Частичная правка ложится на
    действующие значения НАБОРА, а не на встроенные."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    if not isinstance(body, dict) or not (
            isinstance(body.get("cost"), dict)
            or isinstance(body.get("pay"), dict)):
        raise HTTPException(422, "expected {'cost': {...}} and/or "
                                 "{'pay': {...}}")
    from . import costing, pay_credit
    errors: list[str] = []
    merged: dict[str, dict] = {}
    if isinstance(body.get("cost"), dict):
        m = {**config_scope.effective(config_scope.DOMAIN_COST, inst),
             **body["cost"]}
        errors += costing.validate_cost_config(m)
        merged[config_scope.DOMAIN_COST] = m
    if isinstance(body.get("pay"), dict):
        m = {**config_scope.effective(config_scope.DOMAIN_PAY, inst),
             **body["pay"]}
        errors += pay_credit.validate_pay_config(m)
        merged[config_scope.DOMAIN_PAY] = m
    if errors:
        raise HTTPException(422, "; ".join(errors))
    for domain, values in merged.items():
        config_scope.write_instance(domain, inst, values)
    return _instance_rates(inst)


@app.delete("/api/instances/{inst_id}/rates")
def api_instance_rates_reset(inst_id: str, request: Request):
    """Сбросить ставки набора к сегодняшнему ШАБЛОНУ установки."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    for domain in (config_scope.DOMAIN_COST, config_scope.DOMAIN_PAY):
        config_scope.reset_instance(domain, inst)
    return _instance_rates(inst)


@app.put("/api/instances/{inst_id}/llm-scope")
def api_instance_llm_scope(inst_id: str, request: Request, body: dict = Body(...)):
    """Owner/admin sets the per-dataset LLM eligibility that governs which
    backend the solver-advisor may consult for this instance
    ('local-only' | 'any'). 404 (not 403) for an instance the caller may
    not see, so existence never leaks."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    scope = body.get("scope")
    if scope not in registry.LLM_SCOPES:
        raise HTTPException(
            422, f"'scope' must be one of {list(registry.LLM_SCOPES)}")
    registry.set_llm_scope(inst_id, scope)
    return {"id": inst_id, "llm_scope": scope}


@app.post("/api/instances/{inst_id}/advise")
def api_instance_advise(inst_id: str, request: Request, body: dict = Body(...)):
    """Advisory solver recommendation for a run (item: solver-advisor
    consult). Owner/admin only. The LLM backend is chosen from the dataset's
    llm_scope; only NON-PII aggregates are sent. The recommendation is a
    suggestion the human confirms before launching — it is NOT applied here."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import llm_backend, solver_advisor
    job_type = body.get("job_type") or body.get("type") or "complete"
    if job_type not in jobs.JOB_TYPES:
        raise HTTPException(
            400, f"'job_type' must be one of {list(jobs.JOB_TYPES)}")
    rules = _read_airline_config().get("parameters") or {}
    try:
        return solver_advisor.consult(inst, rules, job_type,
                                      backend_request=body.get("backend"))
    except llm_backend.ScopeViolation as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/schema")
def api_schema():
    return registry.load_schema()


# ------------------------------------------------------- наблюдение выпуска
# Три РАЗНЫХ вопроса тремя точками (P2-05, R5-09). Без аутентификации —
# наблюдение, требующее входа, бесполезно; наружу идут только состояния и
# отпечатки.

@app.get("/healthz")
def healthz():
    """Жив ли процесс. Ничего не читает: наблюдатель не должен быть
    источником нагрузки, а живость — зависеть от диска и базы."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response):
    """Готов ли обслуживать: схема, база пользователей, разрешатель
    правил. Неготовность — код 503, а не 200 с текстом: наблюдатель
    смотрит на код."""
    from . import build_identity
    result = build_identity.readiness()
    if not result["ready"]:
        response.status_code = 503
    return result


@app.get("/versionz")
def versionz():
    """Что именно развёрнуто: срез кода, отпечаток решателя и совпадает
    ли он с манифестом сборки. Пятый аудит нашёл фактическое расхождение
    развёрнутого решателя с исходниками — теперь оно видно снаружи, а не
    выводится сравнением времён перезапуска."""
    from . import build_identity
    return build_identity.build_identity()


# --------------------------------------------------------- airline config

def _read_airline_config() -> dict:
    try:
        stored = json.loads(config_scope.install_path(config_scope.DOMAIN_RULES)
                            .read_text(encoding="utf-8"))
        params = stored.get("parameters", {})
    except (OSError, json.JSONDecodeError):
        params = {}
    merged = {**AIRLINE_PARAM_DEFAULTS,
              **{k: v for k, v in params.items() if k in AIRLINE_PARAM_DEFAULTS}}
    # Applied for real since the CREW_RULES_CONFIG wiring: every stage run
    # receives this config via the env variable and the problem fingerprint
    # includes the rules, so non-default parameters produce a different
    # fingerprint (catalogs/results are per-rule-set).
    return {"parameters": merged, "applied": True, "note": AIRLINE_CONFIG_NOTE,
            "note_code": "rules_env",
            "defaults": dict(AIRLINE_PARAM_DEFAULTS),
            # КАК добывается каждое правило: солвером или только проверкой
            # после расчёта. Правило второго рода солверу неизвестно —
            # включив его, оператор получит повторяющийся отказ вердикта и
            # без подписи прочтёт это как поломку солвера.
            "enforcement": {k: _rules_config.enforcement(k)
                            for k in merged}}


@app.get("/api/airline-config")
def api_airline_config_get():
    return _read_airline_config()


@app.put("/api/airline-config")
def api_airline_config_put(request: Request, body: dict = Body(...)):
    """ADMIN-only since 2026-07-15: this was the standing open audit finding
    (2026-07-14, ungated config write), and the pay-and-credit layer now
    derives MONEY from min_rest_min — an ungated write let any user triple
    the computed payroll."""
    user = _user(request)
    if not _is_admin(user):
        raise HTTPException(403, "admin role required")
    params = body.get("parameters")
    if not isinstance(params, dict):
        raise HTTPException(422, "'parameters' must be an object")
    # Ревью пакета: частичный PUT слоится поверх ТЕКУЩЕГО конфига (а не
    # дефолтов) — иначе ручной PUT одного параметра молча сбрасывал
    # включённые comp_* в 0.
    current = _read_airline_config().get("parameters") or {}
    return _write_airline_params(params, base=current)


def _write_airline_params(params: dict, base: dict | None = None) -> dict:
    """Validate + persist Ярус-1 parameters (shared by the config PUT and
    the rule-pack apply). Raises HTTPException(422) on bad input.

    All six tier-1 parameters are WHOLE NUMBERS (minutes / a landings
    count) — the Kotlin stage-2 solver reads them as Int and a fractional
    value crashes the JVM at runtime, so a non-integer value is rejected
    here (fixes the long-standing min_sit_min=30.5 crash on every writer,
    including the rule-pack apply path). `base` lets apply-config layer a
    partial pack over the CURRENT config instead of resetting the
    parameters it does not mention back to defaults."""
    errors = []
    for key, value in params.items():
        if key not in AIRLINE_PARAM_DEFAULTS:
            errors.append(f"unknown parameter '{key}'")
            continue
        # ЕДИНАЯ семантика значений — rules_config.validate_value (та же,
        # что у загрузчика и сценариев): Int32/isfinite/целостность для
        # Яруса 1 (P1-12), диапазоны comp_* с допуском нуля=выключено
        # (аудит-2 P2-05). Отдельный дублирующий валидатор здесь дважды
        # расходился с загрузчиком (M01, P1-12) — больше его нет.
        msg = _rules_config.validate_value(key, value)
        if msg is not None:
            errors.append(msg)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    merged = {**(base or AIRLINE_PARAM_DEFAULTS), **params}
    target = config_scope.install_path(config_scope.DOMAIN_RULES)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Запись АТОМАРНА (tmp -> replace), как у всех соседних хранилищ.
    # Прямая write_text оставляет окно, в котором файл уже усечён, но ещё
    # не дописан: попавший в него старт солвера прочитал бы обрезанный
    # JSON и упал бы с «правила недоступны» — а обе половины системы на
    # незнакомом/битом конфиге падают ГРОМКО, по контракту.
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"parameters": merged}, indent=1),
                   encoding="utf-8")
    for attempt in range(5):
        try:
            tmp.replace(target)
            break
        except PermissionError:                  # Windows: параллельное чтение
            if attempt == 4:
                raise
            time.sleep(0.05)
    return _read_airline_config()


# --------------------------------------------------------------- rule packs

def _rule_pack_visible(scope: str, user: dict) -> bool:
    """Видимость пакета правил повторяет видимость набора (внешний аудит R6,
    P0-06): нормативные пакеты читают все аутентифицированные, пакет
    конкретного набора — только те, кому виден сам набор. Чужой пакет
    неотличим от несуществующего (404, не 403)."""
    from . import rule_packs
    if scope == rule_packs.SCOPE_REGULATORY or _is_admin(user):
        return True
    inst_id = scope.split(":", 1)[1] if ":" in scope else ""
    return bool(inst_id) and registry.get_instance_for(inst_id, user) is not None


@app.get("/api/rule-packs")
def api_rule_packs_list(request: Request):
    """Пакеты правил (Rule Pack, Ф7): список для любого аутентифицированного
    пользователя (планировщики читают правила); правки — админ. Пакеты,
    привязанные к чужим наборам, в списке не появляются."""
    user = _user(request)
    from . import rule_packs
    items = [p for p in rule_packs.list_packs()
             if _rule_pack_visible(p.get("scope") or rule_packs.SCOPE_REGULATORY,
                                   user)]
    return {"items": items,
            "parameters": list(rule_packs.PARAMETERS)}


@app.post("/api/rule-packs")
def api_rule_packs_create(request: Request, body: dict = Body(...)):
    user = _admin(request)
    from . import rule_packs
    try:
        return rule_packs.create_pack(body, user["email"])
    except rule_packs.RulePackError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/api/rule-packs/{pack_id}/scope")
def api_rule_pack_scope(pack_id: str, request: Request,
                        body: dict = Body(...)):
    """Привязать пакет к набору или объявить его нормативным (R5-04).

    `{"scope": "regulatory"}` — норма юрисдикции, действует на наборы
    своей страны. `{"scope": "instance:<id>"}` — правила конкретного
    набора: чужим наборам такой пакет не виден и не может ни попасть в их
    постановку, ни заблокировать им запуск конфликтом значений.

    Привязка к несуществующему или невидимому набору отклоняется: пакет,
    не применяющийся нигде, — тихая потеря правила."""
    user = _admin(request)
    from . import rule_packs
    try:
        scope = rule_packs.normalize_scope((body or {}).get("scope"))
    except rule_packs.RulePackError as exc:
        raise HTTPException(422, str(exc)) from exc
    if scope != rule_packs.SCOPE_REGULATORY:
        inst_id = scope.split(":", 1)[1]
        if registry.get_instance_for(inst_id, user) is None:
            raise HTTPException(
                404, f"no such instance: {inst_id} — привязывать пакет "
                     f"не к чему")
    pack = rule_packs.set_scope(pack_id, scope, by=user["email"])
    if pack is None:
        raise HTTPException(404, f"no such rule pack: {pack_id}")
    return {"id": pack_id, "scope": scope}


@app.get("/api/rule-packs/{pack_id}")
def api_rule_pack_get(pack_id: str, request: Request):
    """Полный пакет + живые прогоны тестов + компиляция (превью). Этот же
    JSON служит экспортом для переноса пакета (страновой шаблон). Пакет
    чужого набора отвечает 404 — как несуществующий."""
    user = _user(request)
    from . import rule_packs
    pack = rule_packs.get_pack(pack_id)
    if pack is None or not _rule_pack_visible(
            pack.get("scope") or rule_packs.SCOPE_REGULATORY, user):
        raise HTTPException(404, f"no such rule pack: {pack_id}")
    return rule_packs.pack_view(pack)


@app.delete("/api/rule-packs/{pack_id}")
def api_rule_pack_delete(pack_id: str, request: Request):
    _admin(request)
    from . import rule_packs
    try:
        rule_packs.delete_pack(pack_id)
    except rule_packs.RulePackError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": pack_id}


@app.post("/api/rule-packs/import")
def api_rule_pack_import(request: Request, body: dict = Body(...)):
    """Импорт полного JSON пакета (перенос знания между экземплярами):
    всегда новый ЧЕРНОВОЙ пакет — утверждения не переезжают, локальный
    человек утверждает заново."""
    user = _admin(request)
    from . import rule_packs
    try:
        return rule_packs.import_pack(body, user["email"])
    except rule_packs.RulePackError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/rule-packs/{pack_id}/rules")
def api_rule_pack_add_rule(pack_id: str, request: Request,
                           body: dict = Body(...)):
    user = _admin(request)
    from . import rule_packs
    try:
        return rule_packs.add_rule(pack_id, body, user["email"])
    except rule_packs.RulePackError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/api/rule-packs/{pack_id}/rules/{rule_id}")
def api_rule_pack_update_rule(pack_id: str, rule_id: str, request: Request,
                              body: dict = Body(...)):
    _admin(request)
    from . import rule_packs
    try:
        return rule_packs.update_rule(pack_id, rule_id, body)
    except rule_packs.RulePackError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/rule-packs/{pack_id}/rules/{rule_id}")
def api_rule_pack_delete_rule(pack_id: str, rule_id: str, request: Request):
    _admin(request)
    from . import rule_packs
    try:
        rule_packs.delete_rule(pack_id, rule_id)
    except rule_packs.RulePackError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"deleted": rule_id}


@app.post("/api/rule-packs/{pack_id}/rules/{rule_id}/approve")
def api_rule_pack_approve_rule(pack_id: str, rule_id: str, request: Request):
    """Подпись под правилом: только когда есть оба вида тестов и все
    исполняемые тесты проходят."""
    user = _admin(request)
    from . import rule_packs
    try:
        return rule_packs.approve_rule(pack_id, rule_id, user["email"])
    except rule_packs.RulePackError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/rule-packs/{pack_id}/approve")
def api_rule_pack_approve(pack_id: str, request: Request):
    user = _admin(request)
    from . import rule_packs
    try:
        return rule_packs.approve_pack(pack_id, user["email"])
    except rule_packs.RulePackError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/rule-packs/{pack_id}/extract")
def api_rule_pack_extract(pack_id: str, request: Request,
                          body: dict = Body(...)):
    """Экстрактор правил (двухконтурный ИИ, Ф7): текст документа →
    черновики правил пакета. sensitivity=public допускает облачную модель,
    confidential — только локальную (громкий отказ). Админ; синхронно."""
    user = _admin(request)
    from . import llm_backend, rule_extractor
    try:
        return rule_extractor.extract(
            pack_id, body.get("text"), body.get("sensitivity"),
            body.get("document"), user["email"],
            backend_request=body.get("backend"))
    except rule_extractor.RuleExtractError as exc:
        raise HTTPException(422, str(exc)) from exc
    except llm_backend.ScopeViolation as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/rule-packs/{pack_id}/apply-config")
def api_rule_pack_apply(pack_id: str, request: Request):
    """Перенос утверждённого пакета в Ярус 1 (airline-config): только
    approved-пакет без конфликтов параметров; запись идёт через ту же
    валидацию, что и PUT /api/airline-config."""
    _admin(request)
    from . import rule_packs
    pack = rule_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(404, f"no such rule pack: {pack_id}")
    if pack.get("status") != "approved":
        raise HTTPException(422, "пакет не утверждён — сначала утвердите "
                                 "каждое правило и пакет целиком")
    comp = rule_packs.compile_pack(pack)
    if comp["conflicts"]:
        raise HTTPException(422, "конфликты параметров: "
                            + "; ".join(comp["conflicts"]))
    if not comp["parameters"]:
        raise HTTPException(422, "в пакете нет исполняемых правил Яруса 1")
    # Layer the pack's parameters over the CURRENT config, not defaults —
    # a pack that formalizes only some parameters must not silently reset
    # the rest that the admin configured separately.
    current = _read_airline_config()["parameters"]
    out = _write_airline_params(comp["parameters"], base=current)
    return {"applied": comp["parameters"], "config": out,
            "outside_tier1": comp["outside_tier1"]}


# ------------------------------------------------------------- cost config

@app.get("/api/cost-config")
def api_cost_config_get(request: Request):
    _user(request)
    from . import costing
    return costing.load_cost_config()


@app.put("/api/cost-config")
def api_cost_config_put(request: Request, body: dict = Body(...)):
    """Airline cost rates (allowances layer). ADMIN-only write — unlike the
    historical airline-config PUT (a standing audit finding), new write
    surfaces are gated from day one."""
    user = _user(request)
    if not _is_admin(user):
        raise HTTPException(403, "admin role required")
    from . import costing
    errors = costing.validate_cost_config(body)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    return costing.save_cost_config(body)


@app.get("/api/costing/{inst_id}")
def api_costing(inst_id: str, request: Request):
    """Costing + hotel/layover report for the instance's current pairing
    catalog (owner-or-admin; 404 never leaks existence)."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import costing
    # Запись уже разрешена проверкой владения — второй обход реестра
    # внутри расчёта не нужен.
    return costing.instance_costing_for(inst)


# -------------------------------------------------- pay-and-credit (gap #1)

@app.get("/api/pay-config")
def api_pay_config_get(request: Request):
    _user(request)
    from . import pay_credit
    return pay_credit.load_pay_config()


@app.put("/api/pay-config")
def api_pay_config_put(request: Request, body: dict = Body(...)):
    """Contract pay knobs (rigs, guarantees). ADMIN-only write."""
    user = _user(request)
    if not _is_admin(user):
        raise HTTPException(403, "admin role required")
    from . import pay_credit
    errors = pay_credit.validate_pay_config(body)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    return pay_credit.save_pay_config(body)


@app.get("/api/pay/{inst_id}")
def api_pay(inst_id: str, request: Request):
    """Pay-and-credit report for the instance's catalog + roster payroll
    (owner-or-admin; 404 never leaks existence)."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import pay_credit
    report = pay_credit.instance_pay(inst_id)
    if report is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return report


@app.get("/api/fairshare/{inst_id}")
def api_fairshare_plan(inst_id: str, request: Request):
    """Fair-share analysis + rebalancing plan over the current roster
    (read-only; owner-or-admin)."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import fair_share
    try:
        return fair_share.build_plan(inst_id, inst)
    except fair_share.FairShareError as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/fairshare/{inst_id}/apply")
def api_fairshare_apply(inst_id: str, request: Request):
    """Recompute the plan on the current state and write it as reversible
    board moves (owner-or-admin)."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import fair_share
    try:
        return fair_share.apply_plan(inst_id, inst, user["email"])
    except fair_share.FairShareError as exc:
        raise HTTPException(500, str(exc)) from exc


@app.get("/api/pay-reselect/{inst_id}")
def api_pay_reselect(inst_id: str, request: Request):
    """Phase B artifact: duty-proxy vs credit-cost selection comparison
    (computed offline by _pay_reselect.py; owner-or-admin)."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import pay_credit
    report = pay_credit.reselect_report(inst_id)
    if report is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return report


# ------------------------------------------------------- base balance config

@app.get("/api/base-config")
def api_base_config_get(request: Request):
    _user(request)
    from . import base_balance
    return base_balance.load_base_config()


@app.put("/api/base-config")
def api_base_config_put(request: Request, body: dict = Body(...)):
    """Base-sizing knobs (monthly block cap, target utilization). ADMIN-only
    write, gated from day one like the cost-config PUT."""
    user = _user(request)
    if not _is_admin(user):
        raise HTTPException(403, "admin role required")
    from . import base_balance
    errors = base_balance.validate_base_config(body)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    return base_balance.save_base_config(body)


@app.get("/api/base-balance/{inst_id}")
def api_base_balance(inst_id: str, request: Request):
    """Multi-base balancing + base-sizing report for the instance's current
    pairing catalog (owner-or-admin; 404 never leaks existence)."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import base_balance
    report = base_balance.instance_base_balance(inst_id)
    if report is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return report


# ----------------------------------------------------- what-if scenarios

@app.get("/api/scenarios")
def api_scenarios_list(request: Request):
    from . import scenarios
    return {"scenarios": scenarios.list_scenarios(_user(request))}


@app.post("/api/scenarios", status_code=201)
def api_scenarios_create(request: Request, body: dict = Body(...)):
    """Create an isolated what-if clone of a visible instance with its own
    rules (full tier-1 set) and optional cost-rate overrides."""
    user = _user(request)
    name = body.get("name")
    base_id = body.get("base_instance_id")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(422, "'name' must be a non-empty string")
    base = (registry.get_instance_for(base_id, user)
            if isinstance(base_id, str) else None)
    if base is None:
        raise HTTPException(404, f"no such instance: {base_id}")
    from . import scenarios
    try:
        created = scenarios.create_scenario(
            user["email"], name, base,
            body.get("rules") or {}, body.get("cost_overrides") or {})
    except scenarios.ScenarioError as exc:
        raise HTTPException(422, str(exc)) from exc
    _bump_user_stat(user["email"], "uploads")
    return created


@app.delete("/api/scenarios/{inst_id}")
def api_scenarios_delete(inst_id: str, request: Request):
    user = _user(request)
    from . import scenarios
    if not scenarios.delete_scenario(inst_id, user["email"],
                                     _is_admin(user)):
        raise HTTPException(404, f"no such scenario: {inst_id}")
    return {"ok": True}


@app.get("/api/scenarios/{inst_id}/compare")
def api_scenarios_compare(inst_id: str, request: Request):
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such scenario: {inst_id}")
    from . import scenarios
    cmp_ = scenarios.compare(inst_id)
    if cmp_ is None:
        raise HTTPException(404, f"no such scenario: {inst_id}")
    return cmp_


# ----------------------------------------------------- vacation bidding

def _vac_instance(inst_id: str, request: Request) -> dict:
    """Resolve a base instance for a bidding round (owner-or-admin; 404 on
    invisible, never 403 — same convention as scenarios/costing)."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return inst


@app.get("/api/vacation/{inst_id}")
def api_vacation_get(inst_id: str, request: Request):
    _vac_instance(inst_id, request)
    from . import vacation_bids
    return vacation_bids.meta_for(inst_id)


@app.put("/api/vacation/{inst_id}/quota")
def api_vacation_quota(inst_id: str, request: Request, body: dict = Body(...)):
    inst = _vac_instance(inst_id, request)
    from . import vacation_bids
    try:
        return vacation_bids.set_quota(inst_id, body.get("quota"),
                                       inst.get("owner"))
    except vacation_bids.VacationBidError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/api/vacation/{inst_id}/bids")
def api_vacation_bids(inst_id: str, request: Request, body: dict = Body(...)):
    inst = _vac_instance(inst_id, request)
    from . import vacation_bids
    try:
        return vacation_bids.submit_bids(inst_id, inst, body.get("bids"),
                                         inst.get("owner"))
    except vacation_bids.VacationBidError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/vacation/{inst_id}/demo")
def api_vacation_demo(inst_id: str, request: Request):
    inst = _vac_instance(inst_id, request)
    from . import vacation_bids
    try:
        bids = vacation_bids.demo_bids(inst)
        return vacation_bids.submit_bids(inst_id, inst, bids,
                                         inst.get("owner"))
    except vacation_bids.VacationBidError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/vacation/{inst_id}/award")
def api_vacation_award(inst_id: str, request: Request):
    inst = _vac_instance(inst_id, request)
    from . import vacation_bids
    try:
        return vacation_bids.award(inst_id, inst)
    except vacation_bids.VacationBidError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/vacation/{inst_id}/apply", status_code=201)
def api_vacation_apply(inst_id: str, request: Request):
    user = _user(request)
    inst = _vac_instance(inst_id, request)
    from . import vacation_bids
    try:
        created = vacation_bids.apply_award(inst_id, inst, user["email"])
    except vacation_bids.VacationBidError as exc:
        raise HTTPException(422, str(exc)) from exc
    _bump_user_stat(user["email"], "uploads")
    return created


@app.delete("/api/vacation/{inst_id}")
def api_vacation_clear(inst_id: str, request: Request):
    user = _user(request)
    _vac_instance(inst_id, request)
    from . import vacation_bids
    if not vacation_bids.clear(inst_id, user["email"], _is_admin(user)):
        raise HTTPException(404, f"no bidding round for {inst_id}")
    return {"ok": True}


# ------------------------------------------------- interactive roster board

def _board_instance(inst_id: str, request: Request) -> dict:
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return inst


@app.get("/api/board/{inst_id}")
def api_board_get(inst_id: str, request: Request):
    inst = _board_instance(inst_id, request)
    from . import roster_board
    return roster_board.board(inst_id, inst)


@app.post("/api/board/{inst_id}/move")
def api_board_move(inst_id: str, request: Request, body: dict = Body(...)):
    inst = _board_instance(inst_id, request)
    from . import roster_board
    try:
        # P0-4: необязательный body.slot адресует конкретное место пейринга
        # причина переделки (контур «Отклик планировщика»): без неё эпизод
        # не пишется — сигналом считается только правка С причиной
        return roster_board.set_move(inst_id, inst, body.get("pid"),
                                     body.get("employee"), inst.get("owner"),
                                     slot=body.get("slot"),
                                     reason_code=body.get("reason"),
                                     reason_text=body.get("reason_text"),
                                     by=_user(request)["email"])
    except roster_board.RosterBoardError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/board/{inst_id}/pin")
def api_board_pin(inst_id: str, request: Request, body: dict = Body(...)):
    inst = _board_instance(inst_id, request)
    from . import roster_board
    try:
        return roster_board.set_pin(inst_id, inst, body.get("pid"),
                                    bool(body.get("locked")),
                                    inst.get("owner"))
    except roster_board.RosterBoardError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/board/{inst_id}")
def api_board_reset(inst_id: str, request: Request):
    user = _user(request)
    _board_instance(inst_id, request)
    from . import roster_board
    if not roster_board.reset(inst_id, user["email"], _is_admin(user)):
        raise HTTPException(404, f"no manual edits for {inst_id}")
    return {"ok": True}


# ------------------------------------------------ crew self-service portal

def _cabinet_access(inst_id: str, request: Request,
                    employee: str | None = None) -> tuple[dict, str]:
    """Resolve (instance, employee_id) for cabinet endpoints.

    A CREW LINK is the capability: a linked user reads their own cabinet for
    an instance they do NOT otherwise own — deliberately bypassing the
    owner-or-admin gate for cabinet-scoped data only (my schedule / my
    clearances / my bid), never the full instance APIs. Admins and the
    instance owner may preview any employee via ?employee=. Everything else
    is 404 — an invisible instance stays indistinguishable from a missing
    one."""
    user = _user(request)
    from . import crew_portal
    inst = registry.get_instance(inst_id)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    linked = crew_portal.linked_employee(inst_id, user["email"])
    planner = registry.get_instance_for(inst_id, user) is not None
    if employee and planner:
        # planner/admin preview — the override must be a real employee, on
        # EVERY verb (the ack write used to store junk ids; audit 2026-07-15)
        emp = str(employee)
        if emp not in crew_portal._known_employees(inst):
            raise HTTPException(422, f"unknown employee_id {emp!r}")
        return inst, emp
    if linked:
        return inst, linked
    if planner:
        raise HTTPException(404, "no crew link for this instance")
    raise HTTPException(404, f"no such instance: {inst_id}")


@app.get("/api/cabinet")
def api_cabinet_links(request: Request):
    """My crew links (which instances I am a crew member of)."""
    user = _user(request)
    from . import crew_portal
    return {"links": crew_portal.links_for_email(user["email"])}


@app.get("/api/cabinet/{inst_id}")
def api_cabinet_get(inst_id: str, request: Request,
                    employee: str | None = None):
    inst, emp = _cabinet_access(inst_id, request, employee)
    from . import crew_portal
    try:
        return crew_portal.cabinet(inst_id, inst, emp)
    except crew_portal.CrewPortalError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/api/cabinet/{inst_id}/vacation")
def api_cabinet_vacation(inst_id: str, request: Request,
                         body: dict = Body(...),
                         employee: str | None = None):
    """Self-service vacation bid: upsert ONLY this crew member's windows."""
    inst, emp = _cabinet_access(inst_id, request, employee)
    from . import crew_portal, vacation_bids
    try:
        bid = crew_portal.submit_my_windows(
            inst_id, inst, emp, body.get("windows"))
    except vacation_bids.VacationBidError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "bid": bid}


@app.delete("/api/cabinet/{inst_id}/vacation")
def api_cabinet_vacation_withdraw(inst_id: str, request: Request,
                                  employee: str | None = None):
    inst, emp = _cabinet_access(inst_id, request, employee)
    from . import crew_portal
    if not crew_portal.withdraw_my_windows(inst_id, emp):
        raise HTTPException(404, "no bid to withdraw")
    return {"ok": True}


@app.post("/api/cabinet/{inst_id}/ack")
def api_cabinet_ack(inst_id: str, request: Request,
                    body: dict = Body(...), employee: str | None = None):
    """Acknowledge MY current schedule. The client sends the roster
    signature it displayed; a later reassignment makes the ack stale."""
    user = _user(request)
    inst, emp = _cabinet_access(inst_id, request, employee)
    sig = body.get("sig")
    if not isinstance(sig, str) or not sig:
        raise HTTPException(422, "'sig' (roster signature) is required")
    from . import crew_portal
    return crew_portal.acknowledge(inst_id, emp, user["email"], sig)


def _links_instance(inst_id: str, request: Request) -> dict:
    """Link management is planner-side: admin or instance owner."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return inst


@app.get("/api/crew-links/{inst_id}")
def api_crew_links_get(inst_id: str, request: Request):
    inst = _links_instance(inst_id, request)
    from . import crew_portal
    emps = crew_portal._known_employees(inst)
    return {"links": crew_portal.links_for_instance(inst_id),
            "employees": [{"id": k, "name": v.get("name") or k,
                           "base": v.get("homeAirport")}
                          for k, v in sorted(emps.items())]}


@app.put("/api/crew-links/{inst_id}")
def api_crew_links_put(inst_id: str, request: Request,
                       body: dict = Body(...)):
    inst = _links_instance(inst_id, request)
    from . import crew_portal
    try:
        return crew_portal.set_link(inst_id, inst, body.get("email"),
                                    body.get("employee_id"))
    except crew_portal.CrewPortalError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/crew-links/{inst_id}")
def api_crew_links_delete(inst_id: str, request: Request,
                          body: dict = Body(...)):
    _links_instance(inst_id, request)
    from . import crew_portal
    if not crew_portal.remove_link(inst_id, body.get("email")):
        raise HTTPException(404, "no such link")
    return {"ok": True}


# ------------------------------------------------- help center (Помощь)

@app.post("/api/ask")
def api_ask(request: Request, body: dict = Body(...)):
    """Ask AI: app-scoped question. Routing is contour-honest: the caller's
    explicit instance selection plus literal id/label mentions decide the
    involved datasets BEFORE any model sees the text; one local-only dataset
    forces the local backend."""
    user = _user(request)
    from . import help_center
    visible = registry.list_instances_for(user)
    # invisible instance ids answer 404 like every other endpoint (never a
    # different status that could tell "exists" from "not yours")
    ids = body.get("instances") or []
    if isinstance(ids, list):
        vis_ids = {i["id"] for i in visible}
        for iid in ids:
            if str(iid) not in vis_ids:
                raise HTTPException(404, f"no such instance: {iid}")
    try:
        return help_center.ask(body.get("question"), ids, user, visible,
                               prefer_cloud=bool(body.get("prefer_cloud")))
    except help_center.HelpCenterError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/ask/backends")
def api_ask_backends(request: Request):
    """Reachability probe for the Ask AI form (local/cloud availability)."""
    _user(request)
    from . import llm_backend
    return llm_backend.available()


@app.post("/api/suggestions", status_code=201)
def api_suggestion_add(request: Request, body: dict = Body(...)):
    user = _user(request)
    from . import help_center
    try:
        return help_center.add_suggestion(user, body.get("text"),
                                          body.get("category"))
    except help_center.HelpCenterError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/suggestions")
def api_suggestions_list(request: Request):
    user = _user(request)
    from . import help_center
    return {"items": help_center.list_suggestions(user, _is_admin(user)),
            "is_admin": _is_admin(user)}


@app.delete("/api/suggestions/{sid}")
def api_suggestion_delete(sid: str, request: Request):
    user = _user(request)
    from . import help_center
    if not help_center.delete_suggestion(sid, user, _is_admin(user)):
        raise HTTPException(404, f"no such suggestion: {sid}")
    return {"ok": True}


# --------------------------------------- disruption recovery (режим «Сбой»)

def _disruption_inst(inst_id: str, request: Request):
    """Owner-or-admin gate, same 404 semantics as the board."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return user, inst


@app.get("/api/disruption/{inst_id}")
def api_disruption_impact(inst_id: str, request: Request):
    """Active disruption events + the current damage picture."""
    _user_, inst = _disruption_inst(inst_id, request)
    from . import disruption
    return disruption.impact(inst_id, inst)


@app.post("/api/disruption/{inst_id}/sick")
def api_disruption_sick(inst_id: str, request: Request,
                        body: dict = Body(...)):
    """Register a sickness event (employee + inclusive day range)."""
    user, inst = _disruption_inst(inst_id, request)
    from . import disruption
    try:
        disruption.add_sick(inst_id, inst,
                            body.get("employee"),
                            str(body.get("day_from") or ""),
                            str(body.get("day_to") or ""),
                            note=str(body.get("note") or ""),
                            by=user.get("email"))
    except disruption.DisruptionError as exc:
        raise HTTPException(422, str(exc)) from exc
    return disruption.impact(inst_id, inst)


@app.delete("/api/disruption/{inst_id}/{event_id}")
def api_disruption_remove(inst_id: str, event_id: str, request: Request):
    _user_, inst = _disruption_inst(inst_id, request)
    from . import disruption
    if not disruption.remove_event(inst_id, event_id):
        raise HTTPException(404, f"no such event: {event_id}")
    return disruption.impact(inst_id, inst)


@app.post("/api/disruption/{inst_id}/event")
def api_disruption_event(inst_id: str, request: Request,
                         body: dict = Body(...)):
    """Flight-level event (Phase B): kind = flights | aircraft | delay."""
    user, inst = _disruption_inst(inst_id, request)
    from . import disruption
    kind = str(body.get("kind") or "")
    try:
        if kind == "flights":
            disruption.add_flights(inst_id, inst, body.get("fns"),
                                   note=str(body.get("note") or ""),
                                   by=user.get("email"))
        elif kind == "aircraft":
            disruption.add_aircraft(inst_id, inst,
                                    str(body.get("tail") or ""),
                                    str(body.get("day_from") or ""),
                                    str(body.get("day_to") or ""),
                                    note=str(body.get("note") or ""),
                                    by=user.get("email"))
        elif kind == "delay":
            disruption.add_delay(inst_id, inst, body.get("fns"),
                                 body.get("shift_min"),
                                 note=str(body.get("note") or ""),
                                 by=user.get("email"))
        else:
            raise HTTPException(422, f"unknown event kind: {kind!r}")
    except disruption.DisruptionError as exc:
        raise HTTPException(422, str(exc)) from exc
    return disruption.impact(inst_id, inst)


@app.post("/api/disruption/{inst_id}/rebuild")
def api_disruption_rebuild(inst_id: str, request: Request,
                           body: dict = Body(default={})):
    """Launch the Phase-B neighbourhood rebuild: derived repair instance +
    background job (colgen over surviving legs -> merged pinned catalog ->
    Timefold re-assignment, greedy fallback)."""
    user, inst = _disruption_inst(inst_id, request)
    from . import disruption
    try:
        return disruption.prepare_rebuild(
            inst_id, inst, email=user.get("email"),
            budget_s=body.get("budget_s") or 0)
    except disruption.DisruptionError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/disruption/{inst_id}/rebuild")
def api_disruption_rebuild_status(inst_id: str, request: Request):
    """Joined status of the last rebuild: job state + published report."""
    _user_, inst = _disruption_inst(inst_id, request)
    from . import disruption
    return disruption.rebuild_status(inst_id)


@app.post("/api/disruption/{inst_id}/plan")
def api_disruption_plan(inst_id: str, request: Request,
                        body: dict = Body(default={})):
    """Greedy repair plan (no writes). Optional extra_pids re-crew healthy
    pairings by explicit request (manual aircraft handling, slice A)."""
    _user_, inst = _disruption_inst(inst_id, request)
    from . import disruption
    try:
        return disruption.build_repair_plan(
            inst_id, inst, extra_pids=list(body.get("extra_pids") or []))
    except disruption.DisruptionError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/disruption/{inst_id}/apply")
def api_disruption_apply(inst_id: str, request: Request,
                         body: dict = Body(default={})):
    """Recompute + apply the repair as reversible board moves."""
    user, inst = _disruption_inst(inst_id, request)
    from . import disruption
    try:
        return disruption.apply_repair(
            inst_id, inst, email=user.get("email"),
            extra_pids=list(body.get("extra_pids") or []))
    except disruption.DisruptionError as exc:
        raise HTTPException(422, str(exc)) from exc


# ------------------------------------------- change requests (Автоулучшение)

def _admin(request: Request) -> dict:
    user = _user(request)
    if not _is_admin(user):
        raise HTTPException(403, "admin role required")
    return user


def _super_admin(request: Request) -> dict:
    """Gate for the super-admin-only Settings surface. Enforced server-side —
    the frontend gate is cosmetic only."""
    user = _user(request)
    if not _is_super(user):
        raise HTTPException(403, "super admin only")
    return user


@app.put("/api/instance-country/{inst_id}")
def api_instance_country(inst_id: str, request: Request,
                         body: dict = Body(...)):
    """Set the dataset's national jurisdiction (owner-or-admin) for the
    regulatory agent's per-country digest."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    try:
        registry.set_country(inst_id, body.get("country"))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"instance": inst_id,
            "country": registry.get_instance(inst_id)["country"]}


@app.get("/api/cr")
def api_cr_list(request: Request):
    _admin(request)
    from . import change_requests
    return {"items": change_requests.list_crs(),
            "impl_quota_left": change_requests._impl_quota_left(),
            "agents": list(change_requests.AGENTS)}


@app.get("/api/template-base")
def api_template_base(request: Request):
    """Инструмент базы шаблонов (P-N-SHD 6а): карта семей с
    контрактами и зависимыми правилами текущего ВМП (админ)."""
    _admin(request)
    from vmp import catalog_access as _ca
    from . import template_base
    try:
        return template_base.overview()
    except _ca.CATALOG_ERRORS as exc:
        # База шаблонов — проекция каталога (шаг 2): каталог недоступен
        # — 503 словами, не трассировка.
        raise HTTPException(503, detail=str(exc)) from exc


@app.post("/api/template-base/change")
def api_template_base_change(body: dict, request: Request):
    """Прежняя заявка на правку/снятие записи (шаг 6 каталога, решение
    0.8): ручка ОСТАЁТСЯ ради совместимости и отвечает 409 словами со
    ссылкой на карточку записи в службе каталога. Заявка каталога у
    существующей записи бывает только на вид исполнителя (R02
    читателя), поэтому «заявка на правку» невозможна по построению."""
    _admin(request)
    from . import template_base
    raise HTTPException(409, detail=template_base.change_refusal(
        str(body.get("section") or "families"),
        str(body.get("family") or body.get("record_id") or "") or None))


@app.post("/api/cr/agents/{agent}/run")
def api_cr_agent_run(agent: str, request: Request):
    """Run a proposal agent now (admin). Synchronous — minutes at most."""
    user = _admin(request)
    from . import change_requests
    runner = change_requests.AGENT_RUNNERS.get(agent)
    if runner is None:
        raise HTTPException(404, f"no such agent: {agent}")
    try:
        return runner(triggered_by=user["email"])
    except change_requests.ChangeRequestError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/cr/{cr_id}/accept")
def api_cr_accept(cr_id: str, request: Request):
    user = _admin(request)
    from . import change_requests
    try:
        return change_requests.accept_cr(cr_id, user["email"])
    except change_requests.ChangeRequestError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/cr/{cr_id}/reject")
def api_cr_reject(cr_id: str, request: Request, body: dict = Body(...)):
    user = _admin(request)
    from . import change_requests
    try:
        return change_requests.reject_cr(cr_id, user["email"],
                                         body.get("reason") or "")
    except change_requests.ChangeRequestError as exc:
        raise HTTPException(422, str(exc)) from exc


# ------------------------------- каталог шаблонов: заявки и CR (шаг 6)
#
# Вид «Каталог» вкладки «Изменения». Кнопки «Запустить агента» у него
# нет по замыслу: строку CR рождает не агент-предложение, а решение
# владельца по заявке, принятое в службе каталога.

@app.get("/api/cr/catalog")
def api_cr_catalog(request: Request):
    """Заявки каталога × строки CR × кандидаты × очередь COSMOS."""
    _admin(request)
    from vmp import catalog_access as _ca
    from . import catalog_pipeline
    try:
        return catalog_pipeline.catalog_view()
    except _ca.CATALOG_ERRORS as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except catalog_pipeline.CatalogPipelineError as exc:
        raise HTTPException(503, detail=str(exc)) from exc


@app.post("/api/cr/catalog/sync")
def api_cr_catalog_sync(request: Request):
    """«Сверить с каталогом»: зеркалит решения владельца по заявкам в
    очередь CR и пишет ключи CR в каталог там, где служба в правке.
    Идемпотентна — повтор ничего не меняет."""
    user = _admin(request)
    from vmp import catalog_access as _ca
    from . import catalog_pipeline
    try:
        return catalog_pipeline.sync_catalog_requests(
            by=f"сверка заявок каталога ({user['email']})")
    except _ca.CATALOG_ERRORS as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except catalog_pipeline.CatalogPipelineError as exc:
        raise HTTPException(503, detail=str(exc)) from exc


@app.post("/api/cr/catalog/deliver")
def api_cr_catalog_deliver(request: Request):
    """Повторная доставка файлов очереди заявок COSMOS в службу (те, у
    которых ответа ещё нет)."""
    _admin(request)
    from . import catalog_pipeline
    try:
        return catalog_pipeline._product().deliver_outbox()
    except catalog_pipeline.CatalogPipelineError as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(422, detail=str(exc)) from exc


@app.post("/api/cr/{cr_id}/implement")
def api_cr_implement(cr_id: str, request: Request):
    """«Реализовать на Тест»: the implementer agent writes code on branch
    cr-<id> of the isolated Test checkout and runs its tests (admin)."""
    user = _admin(request)
    from . import change_requests
    try:
        return change_requests.implement_cr(cr_id, user["email"])
    except change_requests.ChangeRequestError as exc:
        raise HTTPException(422, str(exc)) from exc


# ------------------------------------------------- loops overview («Контуры»)
#
# One read-only page over ALL self-improvement loops. Each loop answers the
# same four questions: что наблюдает, когда наблюдало в последний раз,
# какова метрика сейчас, сколько открытых предложений висит в очереди CR.
# The three deterministic loops (data health / planner overrides / stand
# reliability) also expose their own detail endpoints below.

# Loop key -> the CR agent that files its proposals. Keys ARE agent names
# by design: open_crs is counted straight off the shared queue.
LOOP_KEYS = ("feedback", "regulatory", "uiux", "audit",
             "datahealth", "override", "runtime", "connbuf")

# Контуры со СВОИМ хранилищем и своим экраном: находки «Алгоритмов» живут в
# таблице improvements, база знаний — в собственном документе, и продвигают
# их не решения по очереди CR, а кнопки их собственных вкладок. Раньше их не
# было в сводке ровно поэтому — и обзор, обещающий все контуры, показывал
# пять из семи вкладок группы. Способ хранения находки не должен решать,
# видит ли пользователь контур: ключи здесь — ключи ВКЛАДОК, и по ним же
# клиент уводит на нужный экран.
HEAVY_LOOP_KEYS = ("lab", "kb")
ALL_LOOP_KEYS = LOOP_KEYS + HEAVY_LOOP_KEYS

_LOOP_TITLES = {
    "feedback": ("Отзывы", "предложения пользователей"),
    "regulatory": ("Регуляторика", "изменения норм по странам экземпляров"),
    "uiux": ("Идеи UI/UX", "собственный интерфейс и отраслевые практики"),
    "audit": ("Аудит", "отчёты внешних аудиторов из папки-приёмника"),
    "datahealth": ("Здоровье данных", "дефекты данных экземпляров"),
    "override": ("Отклик планировщика",
                 "ручные переделки доски с указанной причиной"),
    "runtime": ("Надёжность стенда",
                "бюджет времени и отказы расчётных прогонов"),
    "connbuf": ("Буфер стыковки",
                "пересадки внутри смены с нулевым запасом времени"),
    "lab": ("Алгоритмы", "публикации и релизы по методам оптимизации"),
    "kb": ("База знаний", "публичные бенчмарки солверов"),
}


def _loop_open_crs() -> dict[str, int]:
    """agent -> number of CRs still awaiting a decision (draft/accepted)."""
    from . import change_requests
    counts = {k: 0 for k in LOOP_KEYS}
    for cr in change_requests.list_crs():
        agent = cr.get("agent")
        if agent in counts and cr.get("status") in ("draft", "accepted"):
            counts[agent] += 1
    return counts


def _loop_last_cr_at() -> dict[str, str | None]:
    from . import change_requests
    last: dict[str, str | None] = {k: None for k in LOOP_KEYS}
    for cr in change_requests.list_crs():
        agent = cr.get("agent")
        at = cr.get("created_at")
        if agent in last and at and (last[agent] or "") < at:
            last[agent] = at
    return last


def _metric(key: str, n) -> dict:
    """Metric as an i18n key + a number: the page is five-lingual, so the
    server ships the measurement, never the sentence."""
    return {"i18n": "app.loops.m." + key, "n": n}


# Тот же принцип, что у _metric, но для СПРАВОЧНИКОВ: страница
# пятиязычная, а сервер отдавал русские подписи видов дефектов и причин
# переделок — и они рисовались как есть на всех пяти языках. Контракт с
# клиентом: список {"key": <код>, "i18n": <ключ перевода>} в
# каноническом серверном порядке; подпись рисует клиент по ключу.
# Префиксы согласованы с клиентским loopCodeLabel() и словарём i18n.js.
KIND_I18N_PREFIX = "app.loops.dh.k."
REASON_I18N_PREFIX = "app.loops.reason."


def _codes(prefix: str, codes) -> list[dict]:
    return [{"key": str(c), "i18n": prefix + str(c)} for c in codes]


def _datahealth_summary() -> tuple[str | None, int]:
    """(время последнего снимка, суммарное число дефектов в последних
    снимках всех экземпляров).

    Хранилище снимков читается и разбирается РОВНО ОДИН РАЗ за запрос:
    data_health.latest() перечитывает и заново парсит ВЕСЬ файл на каждый
    экземпляр, отчего стоимость страницы росла квадратично по числу
    экземпляров (десятки экземпляров × сотни снимков в каждом)."""
    from . import data_health
    store = data_health._load()          # весь стор одним чтением
    last_at, total = None, 0
    for inst in registry.list_instances():
        rows = store.get(str(inst["id"]))
        if not isinstance(rows, list):
            continue
        # тот же защитный отбор и тот же выбор «последнего», что в
        # data_health.history()/latest(): мусорные строки игнорируются
        snaps = [r for r in rows if isinstance(r, dict) and r.get("at")]
        if not snaps:
            continue
        snap = max(snaps, key=lambda r: str(r.get("at")))
        at = str(snap.get("at") or "")
        if at and (last_at or "") < at:
            last_at = at
        counts = snap.get("counts") or {}
        total += sum(v for v in counts.values() if isinstance(v, int))
    return last_at, total


def _connbuf_summary() -> tuple[str | None, int]:
    """(время последнего снимка, тесных стыковок в последних снимках).

    Хранилище читается РОВНО ОДИН РАЗ за запрос — та же квадратика, что
    была у здоровья данных: latest() перечитывает весь файл на каждый
    экземпляр."""
    from . import connection_buffer
    store = connection_buffer._load()
    last_at, total = None, 0
    for inst in registry.list_instances():
        rows = store.get(str(inst["id"]))
        if not isinstance(rows, list):
            continue
        snaps = [r for r in rows if isinstance(r, dict) and r.get("at")]
        if not snaps:
            continue
        snap = max(snaps, key=lambda r: str(r.get("at")))
        at = str(snap.get("at") or "")
        if at and (last_at or "") < at:
            last_at = at
        total += int(snap.get("tight") or 0)
    return last_at, total


@app.get("/api/loops")
def api_loops(request: Request):
    """Summary of every self-improvement loop for the «Контуры» page."""
    _admin(request)
    from . import data_health, help_center, planner_feedback, run_profile
    from . import change_requests, connection_buffer, evolution
    from . import evolution_runner, solver_advisor

    open_crs = _loop_open_crs()
    last_cr = _loop_last_cr_at()

    fresh = [s for s in help_center.list_suggestions({"email": ""},
                                                     is_admin=True)
             if s.get("status", "new") in ("new", "triaged")]
    eps = planner_feedback.episodes()
    last_ep = max((str(e.get("at") or "") for e in eps), default="") or None
    runs = run_profile.runs()
    last_run = max((str(r.get("at") or "") for r in runs), default="") or None
    dh_at, dh_total = _datahealth_summary()

    evo_counts = evolution.counts_by_status()
    evo_total = sum(evo_counts.values())
    evo_open = sum(n for s, n in evo_counts.items()
                   if s not in evolution.TERMINAL_STATUSES)
    # «Последнее наблюдение» = конец последнего скана. Отметка появилась
    # только 2026-07-20, поэтому на установке с прежними находками её нет —
    # честнее откатиться к самой свежей находке, чем рисовать «—» над
    # непустой базой. Оба значения в одном формате (users._now_iso).
    #
    # Откат — НЕ редкий случай: отметку пишет только сам скан, а он не
    # запускается вовсе при выключенном CREW_EVOLUTION_ENABLED — то есть на
    # внутреннем контуре откат становится ПОСТОЯННЫМ путём. Поэтому здесь
    # обязан быть дешёвый MAX(created_at), а не list_all(): та тянет из базы
    # каждую строку целиком и намеренно падает на испорченном JSON метрик,
    # обрушивая всю страницу разом.
    evo_at = evolution_runner.last_scan_at() or evolution.newest_created_at()
    # updated_at базы знаний записан локальным наивным временем, остальные
    # метки — UTC с «Z». Обе разновидности браузер разбирает верно (наивную
    # — как местную, каковой она и является), поэтому не трогаем формат: он
    # уже показан на вкладке «База знаний» и сравнивать эти метки между
    # собой странице не нужно.
    kb_meta = solver_advisor.skill_meta()
    cb_at, cb_tight = _connbuf_summary()

    observed = {
        "feedback": (last_cr["feedback"], _metric("feedback", len(fresh))),
        "regulatory": (last_cr["regulatory"],
                       _metric("regulatory",
                               len(change_requests.instance_countries()))),
        "uiux": (last_cr["uiux"], _metric("uiux", open_crs["uiux"])),
        "audit": (last_cr["audit"], _metric("audit", open_crs["audit"])),
        # у детерминированных контуров «последнее наблюдение» — это их
        # собственный снимок/эпизод, а не дата последнего предложения
        "datahealth": (dh_at, _metric("datahealth", dh_total)),
        "override": (last_ep, _metric("override", len(eps))),
        "runtime": (last_run, _metric("runtime", len(runs))),
        "connbuf": (cb_at, _metric("connbuf", cb_tight)),
        "lab": (evo_at, _metric("lab", evo_total)),
        "kb": (kb_meta.get("updated_at"),
               _metric("kb", int(kb_meta.get("version") or 0))),
    }
    # «Открытых предложений» у тяжёлых контуров считается по их СОБСТВЕННЫМ
    # сущностям: у «Алгоритмов» это находки вне терминальных статусов (тоже
    # ждут решения человека), у базы знаний ожидающих решения сущностей нет
    # вовсе — None, а не 0: ноль читался бы как «всё разобрано».
    open_by_key: dict[str, int | None] = dict(open_crs)
    open_by_key["lab"] = evo_open
    open_by_key["kb"] = None

    items = []
    for key in ALL_LOOP_KEYS:
        title, observes = _LOOP_TITLES[key]
        last_at, metric = observed[key]
        items.append({"key": key, "title": title, "observes": observes,
                      "last_at": last_at, "metric": metric,
                      "open_crs": open_by_key[key]})
    return {"items": items,
            "kinds": _codes(KIND_I18N_PREFIX, data_health.KIND_LABELS),
            "reasons": _codes(REASON_I18N_PREFIX,
                              planner_feedback.REASON_LABELS)}


@app.post("/api/loops/data-health/snapshot")
def api_loops_data_health_snapshot(request: Request, instance: str = ""):
    """Take a data-health snapshot of every instance (or just ?instance=).

    Snapshots only — CR drafts are the agent runner's job
    (/api/cr/agents/datahealth/run), so the button on the page never
    silently fills the change queue."""
    _admin(request)
    from . import data_health
    instances = registry.list_instances()
    if instance:
        instances = [i for i in instances if str(i["id"]) == instance]
        if not instances:
            raise HTTPException(404, f"no such instance: {instance}")
    taken, skipped = [], []
    for inst in instances:
        try:
            payload = registry.load_payload(
                registry.instance_payload_path(inst))
        except Exception:                                # noqa: BLE001
            skipped.append(str(inst["id"]))              # битая выгрузка
            continue
        snap = data_health.take_snapshot(str(inst["id"]), payload)
        taken.append({"instance": str(inst["id"]),
                      "total": sum(snap.get("counts", {}).values())})
    return {"taken": taken, "skipped": skipped}


@app.get("/api/loops/data-health/{inst_id}")
def api_loops_data_health(inst_id: str, request: Request):
    _admin(request)
    from . import data_health
    return {"instance": inst_id,
            "history": data_health.history(inst_id),
            "trend": data_health.trend(inst_id),
            "kinds": _codes(KIND_I18N_PREFIX, data_health.KIND_LABELS)}


# Минимум размера устойчивой группы переделок: одиночная правка — это
# событие, а не повтор. Значение приходит строкой запроса, и 0 или
# отрицательное отдавало ВСЕ группы разом (каждый эпизод сам себе
# «кластер»), превращая экран в перепечатку журнала.
LOOPS_MIN_CLUSTER_SIZE = 2


@app.get("/api/loops/overrides")
def api_loops_overrides(request: Request, instance: str = "",
                        min_size: int = 3):
    _admin(request)
    from . import planner_feedback
    inst = instance or None
    eps = planner_feedback.episodes(inst)
    min_size = max(LOOPS_MIN_CLUSTER_SIZE, min_size)
    return {"clusters": planner_feedback.clusters(inst, min_size=min_size),
            "episodes": eps[-50:],
            "total_episodes": len(eps),
            "min_size": min_size,
            "reasons": _codes(REASON_I18N_PREFIX,
                              planner_feedback.REASON_LABELS)}


@app.post("/api/loops/connection-buffer/snapshot")
def api_loops_connbuf_snapshot(request: Request, instance: str = ""):
    """Снять перепись стыковок по всем экземплярам (или по ?instance=).

    Только снимки — черновики предложений заводит агент
    (/api/cr/agents/connbuf/run), чтобы кнопка не наполняла очередь
    изменений молча. Экземпляр без ростера снимка не даёт и попадает в
    skipped со своим статусом: это не ошибка, а «мерить нечего»."""
    _admin(request)
    from . import connection_buffer
    instances = registry.list_instances()
    if instance:
        instances = [i for i in instances if str(i["id"]) == instance]
        if not instances:
            raise HTTPException(404, "instance not found")
    taken, skipped = [], []
    for inst in instances:
        try:
            snap = connection_buffer.take_snapshot(inst)
        except connection_buffer.SnapshotStoreUnreadable as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:                          # noqa: BLE001
            skipped.append({"instance": str(inst["id"]),
                            "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if not snap.get("counted"):
            skipped.append({"instance": str(inst["id"]),
                            "reason": snap.get("status")})
            continue
        taken.append({"instance": str(inst["id"]),
                      "tight": snap.get("tight"),
                      "connections": snap.get("connections")})
    return {"taken": taken, "skipped": skipped}


@app.get("/api/loops/connection-buffer/{inst_id}")
def api_loops_connbuf(inst_id: str, request: Request):
    _admin(request)
    from . import connection_buffer
    inst = registry.get_instance(inst_id)
    # Неизвестный экземпляр — это пустая история, а не ошибка: тот же
    # договор, что у детального эндпоинта здоровья данных.
    census = (connection_buffer.census(inst) if inst else
              {"status": "absent", "counted": False})
    return {"instance": inst_id,
            "history": connection_buffer.history(inst_id),
            "trend": connection_buffer.trend(inst_id),
            "census": census}


@app.get("/api/loops/runtime")
def api_loops_runtime(request: Request):
    _admin(request)
    from . import run_profile
    prof = run_profile.profile()
    return {"profile": prof, "recent": run_profile.runs()[-50:]}


# ------------------------------------------- third-party audit inbox

@app.get("/api/audits")
def api_audits(request: Request):
    """Scan the audit inbox directory and return the registry picture.
    Serves BOTH the periodic UI refresh and the «Проверить наличие
    аудита» button — scan() is cheap and idempotent (admin)."""
    _admin(request)
    from . import audit_inbox
    return audit_inbox.scan()


@app.post("/api/audits/{name}/process")
def api_audit_process(name: str, request: Request):
    """«Обработать»: LLM analysis of one audit report; every finding
    becomes a CR (agent «audit») in the ordinary queue; the file flips to
    processed in the registry (admin)."""
    user = _admin(request)
    from . import audit_inbox
    try:
        return audit_inbox.process(name, user["email"])
    except audit_inbox.AuditInboxError as exc:
        raise HTTPException(422, str(exc)) from exc


# ------------------------------------- Студия формализации (Ф1/Ф5)

@app.get("/api/studio")
def api_studio_list(request: Request):
    """Сессии формализации + анкеты (опросник Ф1, глоссарий Ф5). Админ —
    Студией пользуется команда внедрения."""
    _admin(request)
    from . import formalization_studio as fs
    return {"sessions": fs.list_sessions(),
            "questionnaires": fs.questionnaires()}


@app.post("/api/studio")
def api_studio_create(request: Request, body: dict = Body(...)):
    user = _admin(request)
    from . import formalization_studio as fs
    try:
        return fs.create_session(body, user["email"])
    except fs.StudioError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/studio/{session_id}")
def api_studio_get(session_id: str, request: Request):
    _admin(request)
    from . import formalization_studio as fs
    s = fs.get_session(session_id)
    if s is None:
        raise HTTPException(404, f"no such studio session: {session_id}")
    return s


@app.delete("/api/studio/{session_id}")
def api_studio_delete(session_id: str, request: Request):
    _admin(request)
    from . import formalization_studio as fs
    try:
        fs.delete_session(session_id)
    except fs.StudioError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": session_id}


@app.put("/api/studio/{session_id}/answer")
def api_studio_answer(session_id: str, request: Request,
                      body: dict = Body(...)):
    """Зафиксировать ответ опросника или решение глоссария (с автором и
    временем). Пустой текст удаляет ответ."""
    user = _admin(request)
    from . import formalization_studio as fs
    try:
        return fs.answer(session_id, body.get("part"),
                         body.get("question_id"), body.get("text"),
                         user["email"])
    except fs.StudioError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/api/studio/{session_id}/mapping")
def api_studio_mapping(session_id: str, request: Request,
                       body: dict = Body(...)):
    _admin(request)
    from . import formalization_studio as fs
    try:
        return fs.link_mapping(session_id, body)
    except fs.StudioError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/api/studio/{session_id}/rule-pack")
def api_studio_rule_pack(session_id: str, request: Request,
                         body: dict = Body(...)):
    _admin(request)
    from . import formalization_studio as fs
    try:
        return fs.link_rule_pack(session_id, body.get("pack_id"))
    except fs.StudioError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/studio/{session_id}/package")
def api_studio_package(session_id: str, request: Request):
    """«Пакет маппинга заказчика» — переносимый экспорт всей формализации."""
    _admin(request)
    from . import formalization_studio as fs
    try:
        return fs.assemble_package(session_id)
    except fs.StudioError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------- delta intake (Ф9)

def _delta_instance(inst_id: str, request: Request) -> dict:
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return inst


@app.get("/api/instances/{inst_id}/deltas")
def api_deltas_journal(inst_id: str, request: Request):
    """Журнал принятых дельт (расписание + кадры). Owner-or-admin."""
    _delta_instance(inst_id, request)
    from . import schedule_deltas
    return {"instance": inst_id,
            "items": schedule_deltas.journal_for(inst_id)}


@app.post("/api/instances/{inst_id}/deltas/schedule")
def api_delta_schedule(inst_id: str, request: Request,
                       body: dict = Body(...)):
    """Дельта расписания (подмножество ASM/SSM). apply=false (по
    умолчанию) — предпросмотр; apply=true — запись с валидацией,
    идемпотентностью по SHA-256 и журналом."""
    user = _user(request)
    inst = _delta_instance(inst_id, request)
    from . import schedule_deltas
    try:
        return schedule_deltas.receive_schedule(
            inst, body.get("text"), user["email"],
            apply=bool(body.get("apply")))
    except schedule_deltas.DeltaError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/instances/{inst_id}/deltas/crew")
def api_delta_crew(inst_id: str, request: Request, body: dict = Body(...)):
    """Инкрементальная кадровая выгрузка (CSV/JSON с каноническими
    колонками): диф по id, добавление/обновление; пропавшие из выгрузки
    только перечисляются (без молчаливых удалений)."""
    user = _user(request)
    inst = _delta_instance(inst_id, request)
    from . import data_import, schedule_deltas
    name = str(body.get("name") or "crew-delta.csv")
    if isinstance(body.get("text"), str):
        raw = body["text"].encode("utf-8")
    elif isinstance(body.get("content_b64"), str):
        import base64
        try:
            raw = base64.b64decode(body["content_b64"], validate=True)
        except Exception as exc:                     # noqa: BLE001
            raise HTTPException(422, "content_b64 не раскодировался") \
                from exc
    else:
        raise HTTPException(422, "нужно поле text или content_b64")
    if len(raw) > schedule_deltas.MAX_CREW_BYTES:
        raise HTTPException(413, "кадровая дельта больше допустимого размера")
    try:
        return schedule_deltas.receive_crew(
            inst, name, raw, user["email"], apply=bool(body.get("apply")))
    except schedule_deltas.DeltaError as exc:
        raise HTTPException(422, str(exc)) from exc
    except data_import.ImportError_ as exc:
        raise HTTPException(422, str(exc)) from exc


# --------------------------------------------- roster comparison (Ф8)

@app.get("/api/roster-compare/{inst_id}")
def api_roster_compare(inst_id: str, request: Request):
    """Отчёт сравнения ростеров: наш (вторая стадия + правки доски)
    против исторического (flightAssignments payload). Owner-or-admin."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import roster_compare
    return roster_compare.build_report(inst_id, inst)


@app.get("/api/roster-compare/{inst_id}/act.md")
def api_roster_compare_act(inst_id: str, request: Request):
    """Markdown-акт сравнения для подписания приёмки теневого прогона."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import roster_compare
    report = roster_compare.build_report(inst_id, inst)
    return PlainTextResponse(roster_compare.markdown_comparison(report),
                             media_type="text/markdown; charset=utf-8")


# -------------------------------- raw zone + history anomaly report

@app.get("/api/instances/{inst_id}/raw-manifest")
def api_instance_raw_manifest(inst_id: str, request: Request):
    """Манифест Raw-зоны (Ф3): исходные файлы инстанса с отпечатками
    SHA-256. Owner-or-admin; null для инстансов без Raw-зоны (сиды,
    канонические загрузки, импорты до фичи)."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import raw_zone
    return {"instance": inst_id, "manifest": raw_zone.manifest_for(inst_id)}


@app.get("/api/instances/{inst_id}/anomalies")
def api_instance_anomalies(inst_id: str, request: Request):
    """Отчёт об аномалиях истории (Ф6): выбросы налёта, дубликаты,
    «мёртвые души» — детерминированно, без ИИ. Owner-or-admin."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import anomaly_report
    payload = registry.load_payload(registry.instance_payload_path(inst))
    return {"instance": inst_id, **anomaly_report.report(payload)}


# ------------------------------------------- LLM call-site registry

@app.get("/api/llm-registry")
def api_llm_registry(request: Request):
    """Реестр точек вызова ИИ (admin): every model call site with its
    routing mechanism and data class — the audit artifact of the strict
    mode (data-formalization doc §8-bis). Static product knowledge; a
    guard test keeps it in sync with the code."""
    _admin(request)
    from . import llm_call_registry
    return llm_call_registry.as_payload()


# ------------------------------------------- app settings (super admin only)

@app.get("/api/settings")
def api_settings_get(request: Request):
    """Global app settings for the super admin (settings.SUPER_ADMIN_EMAIL —
    first address of CREW_SUPER_ADMIN, no default in code). Returns the
    per-use-case Claude model choices,
    the Claude API key STATUS (never the key itself), the default backend,
    local model, and the LLM-import masking policy — plus the currently
    effective values so the UI can show placeholders."""
    _super_admin(request)
    from . import llm_backend
    data = settings.get_all()
    return {
        "is_super": True,
        "super_admin": settings.SUPER_ADMIN_EMAIL,
        "api_key": {"configured": settings.has_api_key(),
                    "hint": settings.api_key_hint(),
                    "source": settings.api_key_source()},
        "usages": [{"key": u, "cli": u in settings.USAGE_CLI}
                   for u in settings.USAGES],
        "models": data["models"],
        "default_backend": data["default_backend"],
        "ollama_model": data["ollama_model"],
        "ollama_url": data["ollama_url"],
        "mask_samples": data["mask_samples"],
        "models_cache": data["models_cache"],
        "effective": {
            "default_backend": llm_backend.default_backend(),
            "ollama_model": llm_backend.ollama_model_or_none(),
            "ollama_url": llm_backend.ollama_url(),
        },
        "backends": llm_backend.available(),
    }


@app.put("/api/settings")
def api_settings_put(request: Request, body: dict = Body(...)):
    """Persist non-secret settings (super admin). Body may carry any of:
    models {usage: model-id|null}, default_backend, ollama_model, ollama_url,
    mask_samples ("cloud"|"always"|null)."""
    user = _super_admin(request)
    patch = {k: body[k] for k in
             ("models", "default_backend", "ollama_model", "ollama_url",
              "mask_samples") if k in body}
    try:
        settings.save(patch)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    admin_api._audit(user["email"], "settings-update", "app_settings", "ok",
                     detail="keys=" + ",".join(sorted(patch)))
    return {"ok": True, "models": settings.get_all()["models"]}


@app.post("/api/settings/api-key")
def api_settings_set_key(request: Request, body: dict = Body(...)):
    """Store the Claude API key (super admin). The raw value is written to a
    gitignored file and is NEVER returned by any endpoint."""
    user = _super_admin(request)
    try:
        settings.set_api_key(str(body.get("key") or ""))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Audit the EVENT only — never the raw key (M18).
    admin_api._audit(user["email"], "settings-api-key-set", "claude_api_key",
                     "ok", detail="source=" + settings.api_key_source())
    return {"ok": True, "configured": settings.has_api_key(),
            "hint": settings.api_key_hint(), "source": settings.api_key_source()}


@app.delete("/api/settings/api-key")
def api_settings_clear_key(request: Request):
    """Remove the stored Claude API key file (super admin). Any env-var
    fallback is left untouched."""
    user = _super_admin(request)
    settings.clear_api_key()
    admin_api._audit(user["email"], "settings-api-key-clear", "claude_api_key",
                     "ok")
    return {"ok": True, "configured": settings.has_api_key(),
            "source": settings.api_key_source()}


@app.post("/api/settings/models/refresh")
def api_settings_refresh_models(request: Request,
                                body: dict | None = Body(default=None)):
    """Fetch the live Claude model catalog with the stored (or a supplied)
    API key and cache it (super admin). Sends NO customer data — only asks
    Anthropic which models exist — so it is contour-safe."""
    _super_admin(request)
    from . import llm_backend
    key = str((body or {}).get("key") or "").strip() or settings.get_api_key()
    if not key:
        raise HTTPException(422, "no Claude API key configured")
    try:
        models = llm_backend.list_models(key)
    except llm_backend.LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    from datetime import datetime, timezone
    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    settings.set_models_cache(models, fetched)
    return {"ok": True, "fetched_at": fetched, "models": models}


# -------------------------------------------- qualifications / currency

def _qual_instance(inst_id: str, request: Request) -> dict:
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return inst


@app.get("/api/qualifications/{inst_id}")
def api_qual_get(inst_id: str, request: Request):
    _qual_instance(inst_id, request)
    from . import qualifications
    return qualifications.meta_for(inst_id)


@app.get("/api/qualifications/{inst_id}/compliance")
def api_qual_compliance(inst_id: str, request: Request):
    inst = _qual_instance(inst_id, request)
    from . import qualifications
    return qualifications.compliance(inst_id, inst)


@app.put("/api/qualifications/{inst_id}/warn")
def api_qual_warn(inst_id: str, request: Request, body: dict = Body(...)):
    inst = _qual_instance(inst_id, request)
    from . import qualifications
    try:
        return qualifications.set_warn_days(inst_id, body.get("warn_days"),
                                            inst.get("owner"))
    except qualifications.QualificationError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/api/qualifications/{inst_id}/records")
def api_qual_records(inst_id: str, request: Request, body: dict = Body(...)):
    inst = _qual_instance(inst_id, request)
    from . import qualifications
    try:
        return qualifications.submit_records(inst_id, inst,
                                             body.get("records"),
                                             inst.get("owner"))
    except qualifications.QualificationError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/qualifications/{inst_id}/demo")
def api_qual_demo(inst_id: str, request: Request):
    inst = _qual_instance(inst_id, request)
    from . import qualifications
    try:
        recs = qualifications.demo_records(inst)
        return qualifications.submit_records(inst_id, inst, recs,
                                             inst.get("owner"))
    except qualifications.QualificationError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/qualifications/{inst_id}/apply", status_code=201)
def api_qual_apply(inst_id: str, request: Request):
    user = _user(request)
    inst = _qual_instance(inst_id, request)
    from . import qualifications
    try:
        created = qualifications.apply_compliance(inst_id, inst,
                                                  user["email"])
    except qualifications.QualificationError as exc:
        raise HTTPException(422, str(exc)) from exc
    _bump_user_stat(user["email"], "uploads")
    return created


@app.delete("/api/qualifications/{inst_id}")
def api_qual_clear(inst_id: str, request: Request):
    user = _user(request)
    _qual_instance(inst_id, request)
    from . import qualifications
    if not qualifications.clear(inst_id, user["email"], _is_admin(user)):
        raise HTTPException(404, f"no qualification data for {inst_id}")
    return {"ok": True}


# ----------------------------------------------------------------- KPIs

@app.get("/api/kpi/{inst_id}")
def api_kpi(inst_id: str, request: Request):
    """Cost/utilization KPIs for one instance (owner-or-admin)."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import kpi
    # Запись уже разрешена проверкой владения — считаем по ней, а не
    # разрешаем id второй и третий раз (каждое разрешение = полный обход
    # выгрузок).
    return kpi.instance_kpi_for(inst)


@app.get("/api/kpi")
def api_kpi_fleet(request: Request):
    """Fleet KPI table over the caller's visible instances (admins see
    everything, regular users their own — same tenancy as the registry)."""
    user = _user(request)
    from . import kpi
    return {"rows": kpi.fleet_kpi(user)}


# ------------------------------------------------------------- SSIM import

def _validate_ssim_request(body: dict) -> tuple[list[str], dict]:
    """Field checks for POST /api/import/ssim; returns (errors, parsed)."""
    errors: list[str] = []
    parsed: dict = {}

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("'name' must be a non-empty string")
    else:
        parsed["name"] = name

    ssim_text = body.get("ssim_text")
    if not isinstance(ssim_text, str) or not ssim_text.strip():
        errors.append("'ssim_text' must be a non-empty string "
                      "(the SSIM file content)")
    else:
        parsed["ssim_text"] = ssim_text

    for field in ("date_from", "date_to"):
        raw = body.get(field)
        if raw is None:
            parsed[field] = None
            continue
        try:
            parsed[field] = date.fromisoformat(raw).isoformat()
        except (TypeError, ValueError):
            errors.append(f"'{field}' must be YYYY-MM-DD or null, "
                          f"got {raw!r}")

    bases = body.get("bases")
    base_specs: list[str] = []
    if not isinstance(bases, list) or not bases:
        errors.append("'bases' must be a non-empty list of "
                      "{\"airport\": str, \"count\": int}")
    else:
        for i, b in enumerate(bases):
            if not isinstance(b, dict):
                errors.append(f"bases[{i}] must be an object")
                continue
            airport = b.get("airport")
            count = b.get("count")
            if (not isinstance(airport, str)
                    or len(airport.strip()) != 3
                    or not airport.strip().isalnum()):
                errors.append(f"bases[{i}].airport must be a 3-char IATA "
                              f"code, got {airport!r}")
                continue
            if (isinstance(count, bool) or not isinstance(count, int)
                    or count <= 0):
                errors.append(f"bases[{i}].count must be a positive "
                              f"integer, got {count!r}")
                continue
            base_specs.append(f"{airport.strip().upper()}:{count}")
    parsed["bases_arg"] = ",".join(base_specs)
    return errors, parsed


@app.post("/api/import/ssim")
def api_import_ssim(request: Request, body: dict = Body(...)):
    """Run the SSIM adapter (_ssim_to_payload.py) as a subprocess.

    Success: payload saved to data/uploads/<user-slug>/<slug>.json (owned
    by the caller), the conversion-check artifact next to it as
    <slug>.conversion_check.json; response {"id", "conversion_check":
    {...}}. Any adapter failure -> 422 with the adapter's output verbatim,
    and NOTHING is left in data/uploads.
    """
    user = _user(request)
    errors, parsed = _validate_ssim_request(body)
    if errors:
        return JSONResponse(status_code=422,
                            content={"errors": errors,
                                     "conversion_check": None})

    slug = registry.slugify_or_hash(parsed["name"])   # any UI language
    inst_id, _label, out_path = registry.upload_target(user["email"], slug)
    if inst_id in {i["id"] for i in registry.list_instances()}:
        return JSONResponse(status_code=422, content={
            "errors": [f"id '{inst_id}' already exists"],
            "conversion_check": None})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    check_path = out_path.with_name(f"{slug}.conversion_check.json")
    # Ревью пакета 2026-07-19 (P0-03/SSIM): адаптер писал ПРЯМО в целевой
    # payload-путь — гоночная схема check -> adapter-write -> set_owner
    # молча перезаписывала payload конкурента. Теперь адаптер пишет во
    # временные файлы, а публикация — атомарный claim_upload (эксклюзивное
    # создание + владелец под одним локом). Уникальность tmp-имён — uuid,
    # НЕ pid: у конкурентных запросов в threadpool pid общий (2026-07-24).
    import uuid
    tag = uuid.uuid4().hex[:12]
    tmp_out = out_path.with_name(f"{slug}.ssim-out-{tag}.tmp")
    tmp_check = out_path.with_name(f"{slug}.ssim-check-{tag}.tmp")

    fd, tmp_name = tempfile.mkstemp(suffix=".ssim", prefix="ssim-import-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(parsed["ssim_text"])
        cmd = [sys.executable, str(SSIM_ADAPTER), str(tmp_path),
               "--bases", parsed["bases_arg"],
               "--out", str(tmp_out), "--check", str(tmp_check)]
        if parsed["date_from"]:
            cmd += ["--from", parsed["date_from"]]
        if parsed["date_to"]:
            cmd += ["--to", parsed["date_to"]]
        try:
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=SSIM_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            tmp_out.unlink(missing_ok=True)
            tmp_check.unlink(missing_ok=True)
            return JSONResponse(status_code=422, content={
                "errors": [f"adapter timed out after {SSIM_TIMEOUT_S} s"],
                "conversion_check": None})
    finally:
        tmp_path.unlink(missing_ok=True)

    def _read_check():
        try:
            return json.loads(tmp_check.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _drop_tmp():
        tmp_out.unlink(missing_ok=True)
        tmp_check.unlink(missing_ok=True)

    if proc.returncode != 0:
        # Adapter failed loudly: return its stdout/stderr verbatim plus the
        # conversion-check artifact content, then leave nothing on disk.
        check = _read_check()
        _drop_tmp()
        lines = [ln for ln in
                 (proc.stdout.splitlines() + proc.stderr.splitlines())
                 if ln.strip()]
        return JSONResponse(status_code=422, content={
            "errors": lines or [f"adapter exited with code "
                                f"{proc.returncode} and no output"],
            "exit_code": proc.returncode,
            "conversion_check": check})

    check = _read_check()
    try:
        payload_obj = json.loads(tmp_out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload_obj = None
    if check is None or payload_obj is None:
        # Exit 0 but missing artifacts would be an adapter bug — loud 500.
        _drop_tmp()
        raise HTTPException(500, "SSIM adapter exited 0 but did not produce "
                                 "the payload and/or conversion check")
    # Immutable raw zone (Ф3): keep the exact SSIM text that was converted.
    from . import raw_zone
    try:
        raw_zone.store(inst_id,
                       [{"name": f"{slug}.ssim",
                         "data": parsed["ssim_text"].encode("utf-8")}],
                       user["email"])
    except raw_zone.RawZoneError as exc:
        _drop_tmp()
        raise HTTPException(409, str(exc)) from exc
    try:
        registry.claim_upload(inst_id, out_path, payload_obj, user["email"])
    except registry.UploadExists:
        raw_zone.discard(inst_id)
        _drop_tmp()
        return JSONResponse(status_code=422, content={
            "errors": [f"id '{inst_id}' already exists"],
            "conversion_check": check})
    except BaseException:
        raw_zone.discard(inst_id)
        _drop_tmp()
        raise
    tmp_check.replace(check_path)
    tmp_out.unlink(missing_ok=True)
    _bump_user_stat(user["email"], "uploads")
    return {"id": inst_id, "conversion_check": check}


# --------------------------------------------------------- Meridian import

def _validate_meridian_request(body: dict) -> tuple[list[str], dict]:
    """Field checks for POST /api/import/meridian; returns (errors, parsed)."""
    errors: list[str] = []
    parsed: dict = {}

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("'name' must be a non-empty string")
    else:
        parsed["name"] = name

    mer = body.get("meridian_json")
    if not isinstance(mer, str) or not mer.strip():
        errors.append("'meridian_json' must be a non-empty string "
                      "(the Meridian export file content)")
    else:
        parsed["meridian_json"] = mer

    # Структура авиакомпании (смены и цепочки из выгрузки) нужна
    # построителю цепочек для уровней свободы 1 и 2: без неё доступен
    # только уровень 3 (построение с нуля).
    keep = body.get("keep_structure")
    parsed["keep_structure"] = bool(keep) if isinstance(keep, bool) else False

    sol = body.get("solver_json")
    if sol is None:
        parsed["solver_json"] = None
    elif not isinstance(sol, str) or not sol.strip():
        errors.append("'solver_json' must be a non-empty string or null")
    else:
        parsed["solver_json"] = sol

    for field in ("date_from", "date_to"):
        raw = body.get(field)
        if raw is None:
            parsed[field] = None
            continue
        try:
            parsed[field] = date.fromisoformat(raw).isoformat()
        except (TypeError, ValueError):
            errors.append(f"'{field}' must be YYYY-MM-DD or null, "
                          f"got {raw!r}")

    no_fill = body.get("no_fill")
    if no_fill is None:
        parsed["no_fill"] = False
    elif not isinstance(no_fill, bool):
        # bool("false") == True — приняв любой тип, флаг включался бы
        # строкой "false" молча и наоборот намерению (ревью 2026-07-24).
        errors.append(f"'no_fill' must be a boolean or null, got "
                      f"{no_fill!r}")
    else:
        parsed["no_fill"] = no_fill
    return errors, parsed


@app.post("/api/import/meridian")
async def api_import_meridian(request: Request):
    """Run the Meridian adapter (_meridian_to_payload.py) as a subprocess.

    Deterministic import (layer 1): no model is ever called — the request
    carries the Meridian export (and optionally the matching Solver
    export as an aircraft-type/geo reference), the adapter converts it
    locally. Contract mirrors /api/import/ssim: success -> payload +
    conversion-check artifact + raw zone; any adapter failure -> 422 with
    the adapter's output verbatim, and NOTHING is left in data/uploads.

    Асинхронная обёртка — из-за размера тела (штатно ~95 МБ): лимит по
    Content-Length/факту, разбор JSON в пуле потоков (не на event loop),
    вся синхронная работа — тоже в пуле (ревью 2026-07-24).
    """
    user = _user(request)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > IMPORT_BODY_MAX_BYTES:
        raise HTTPException(413, f"request body over "
                                 f"{IMPORT_BODY_MAX_BYTES // 2**20} MB")
    raw = await request.body()
    if len(raw) > IMPORT_BODY_MAX_BYTES:
        raise HTTPException(413, f"request body over "
                                 f"{IMPORT_BODY_MAX_BYTES // 2**20} MB")
    from starlette.concurrency import run_in_threadpool
    try:
        body = await run_in_threadpool(json.loads, raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(400, "request body is not valid JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "request body must be a JSON object")
    return await run_in_threadpool(_import_meridian_sync, user, body)


def _import_meridian_sync(user: dict, body: dict):
    errors, parsed = _validate_meridian_request(body)
    if errors:
        return JSONResponse(status_code=422,
                            content={"errors": errors,
                                     "conversion_check": None})

    slug = registry.slugify_or_hash(parsed["name"])   # any UI language
    inst_id, _label, out_path = registry.upload_target(user["email"], slug)
    if inst_id in {i["id"] for i in registry.list_instances()}:
        return JSONResponse(status_code=422, content={
            "errors": [f"id '{inst_id}' already exists"],
            "conversion_check": None})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    check_path = out_path.with_name(f"{slug}.conversion_check.json")
    # Как и в SSIM (ревью 2026-07-19, P0-03): адаптер пишет во временные
    # файлы, публикация — атомарный claim_upload. Уникальность tmp-имён —
    # uuid, НЕ pid: sync-эндпоинты одного процесса крутятся в threadpool,
    # у конкурентных запросов pid один и тот же (ревью 2026-07-24).
    import uuid
    tag = uuid.uuid4().hex[:12]
    tmp_out = out_path.with_name(f"{slug}.meridian-out-{tag}.tmp")
    tmp_check = out_path.with_name(f"{slug}.meridian-check-{tag}.tmp")

    IMPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".json",
                                    prefix="meridian-import-",
                                    dir=str(IMPORT_TMP_DIR))
    tmp_src = Path(tmp_name)
    tmp_sol: Path | None = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(parsed["meridian_json"])
        cmd = [sys.executable, str(MERIDIAN_ADAPTER), str(tmp_src),
               "--out", str(tmp_out), "--check", str(tmp_check)]
        if parsed["solver_json"] is not None:
            fd2, tmp_name2 = tempfile.mkstemp(suffix=".json",
                                              prefix="meridian-solver-",
                                              dir=str(IMPORT_TMP_DIR))
            tmp_sol = Path(tmp_name2)
            with os.fdopen(fd2, "w", encoding="utf-8") as f:
                f.write(parsed["solver_json"])
            cmd += ["--solver", str(tmp_sol)]
        if parsed["date_from"]:
            cmd += ["--from", parsed["date_from"]]
        if parsed["date_to"]:
            cmd += ["--to", parsed["date_to"]]
        if parsed["no_fill"]:
            cmd += ["--no-fill"]
        base_catalog_path = None
        if parsed["keep_structure"]:
            base_catalog_path = (registry.FINAL_REPORT
                                 / f"pairing_base_catalog_{_label}.json")
            cmd += ["--emit-catalog", str(base_catalog_path),
                    "--catalog-label", _label]
        try:
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=MERIDIAN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # Контрактный 422 вместо неструктурированного 500; всё, что
            # убитый адаптер успел записать, вычищается (ревью 2026-07-24).
            tmp_out.unlink(missing_ok=True)
            tmp_check.unlink(missing_ok=True)
            return JSONResponse(status_code=422, content={
                "errors": [f"adapter timed out after "
                           f"{MERIDIAN_TIMEOUT_S} s"],
                "conversion_check": None})
    finally:
        tmp_src.unlink(missing_ok=True)
        if tmp_sol is not None:
            tmp_sol.unlink(missing_ok=True)

    def _read_check():
        try:
            return json.loads(tmp_check.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _drop_tmp():
        tmp_out.unlink(missing_ok=True)
        tmp_check.unlink(missing_ok=True)

    if proc.returncode != 0:
        check = _read_check()
        _drop_tmp()
        lines = [ln for ln in
                 (proc.stdout.splitlines() + proc.stderr.splitlines())
                 if ln.strip()]
        return JSONResponse(status_code=422, content={
            "errors": lines or [f"adapter exited with code "
                                f"{proc.returncode} and no output"],
            "exit_code": proc.returncode,
            "conversion_check": check})

    check = _read_check()
    try:
        payload_obj = json.loads(tmp_out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload_obj = None
    if check is None or payload_obj is None:
        _drop_tmp()
        raise HTTPException(500, "Meridian adapter exited 0 but did not "
                                 "produce the payload and/or conversion "
                                 "check")
    # Immutable raw zone (Ф3): both source files — the Solver reference
    # influenced the payload (aircraft types, geo, seat fallback).
    from . import raw_zone
    raw_files = [{"name": f"{slug}.meridian.json",
                  "data": parsed["meridian_json"].encode("utf-8")}]
    if parsed["solver_json"] is not None:
        raw_files.append({"name": f"{slug}.solver.json",
                          "data": parsed["solver_json"].encode("utf-8")})
    try:
        raw_zone.store(inst_id, raw_files, user["email"])
    except raw_zone.RawZoneError as exc:
        _drop_tmp()
        raise HTTPException(409, str(exc)) from exc
    try:
        registry.claim_upload(inst_id, out_path, payload_obj, user["email"])
    except registry.UploadExists:
        raw_zone.discard(inst_id)
        _drop_tmp()
        return JSONResponse(status_code=422, content={
            "errors": [f"id '{inst_id}' already exists"],
            "conversion_check": check})
    except BaseException:
        raw_zone.discard(inst_id)
        _drop_tmp()
        raise
    tmp_check.replace(check_path)
    tmp_out.unlink(missing_ok=True)
    _bump_user_stat(user["email"], "uploads")
    return {"id": inst_id, "conversion_check": check}


# ---------------------------------------------------------- rules import

@app.post("/api/import/rules")
async def api_import_rules(request: Request):
    """Импорт правил авиакомпании из внешнего файла (зона «Импорт правил»,
    2026-07-24): файл -> рамка приёмников _rules_import (детерминированно,
    без моделей) -> новый ЧЕРНОВОЙ Пакет правил. Закрытые форматы
    (jeppesen-rave и др.) зарегистрированы и честно отвечают, каких
    материалов не хватает. Асинхронная обёртка — файл может быть большим
    (экспорт оптимизатора ~46 МБ)."""
    user = _admin(request)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > IMPORT_BODY_MAX_BYTES:
        raise HTTPException(413, f"request body over "
                                 f"{IMPORT_BODY_MAX_BYTES // 2**20} MB")
    raw = await request.body()
    if len(raw) > IMPORT_BODY_MAX_BYTES:
        raise HTTPException(413, f"request body over "
                                 f"{IMPORT_BODY_MAX_BYTES // 2**20} MB")
    from starlette.concurrency import run_in_threadpool
    try:
        body = await run_in_threadpool(json.loads, raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(400, "request body is not valid JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "request body must be a JSON object")
    return await run_in_threadpool(_import_rules_sync, user, body)


def _import_rules_sync(user: dict, body: dict):
    fmt = body.get("format")
    # с 2026-07-24 предпочтительное поле — file_text (текстовые форматы:
    # Rave, таблицы норм CSV, XML); file_json оставлено для совместимости
    file_text = body.get("file_text")
    if not isinstance(file_text, str) or not file_text.strip():
        file_text = body.get("file_json")
    # страна юрисдикции пакета: обязательна для форматов, не несущих её
    # в файле (meridian-limits, solver-format5) — отказ формирует сам
    # приёмник (_rules_import) и уходит клиенту как 422 словами
    country = body.get("country")
    errors: list[str] = []
    if not isinstance(fmt, str) or not fmt.strip():
        errors.append("'format' must be a non-empty string")
    if not isinstance(file_text, str) or not file_text.strip():
        errors.append("'file_text' must be a non-empty string "
                      "(the rules file content)")
    if country is not None and not isinstance(country, str):
        errors.append("'country' must be a string (INTL or ISO-3166 "
                      "alpha-2) or omitted")
    if errors:
        return JSONResponse(status_code=422, content={"errors": errors})
    import _rules_import as mod        # корень репо уже на sys.path
    try:
        pack_body = mod.import_rules_text(
            file_text, fmt.strip(),
            str(body.get("source_name") or "загруженный файл"),
            country=(country.strip() or None)
            if isinstance(country, str) else None)
    except mod.RulesImportError as exc:
        return JSONResponse(status_code=422, content={"errors": [str(exc)]})
    name = body.get("name")
    if isinstance(name, str) and name.strip():
        pack_body["name"] = name.strip()[:120]
    from . import rule_packs
    try:
        result = rule_packs.import_pack(pack_body, user["email"])
    except rule_packs.RulePackError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"pack_id": result["pack"]["id"],
            "pack_name": result["pack"].get("name"),
            "imported_rules": result["imported_rules"],
            "errors": result.get("errors") or []}


# ------------------------------------------------------------------- jobs

@app.post("/api/jobs", status_code=201)
def api_jobs_post(request: Request, body: dict = Body(...)):
    user = _user(request)
    jtype = body.get("type")
    inst_id = body.get("instance_id")
    if jtype not in jobs.JOB_TYPES:
        raise HTTPException(400, f"'type' must be one of {list(jobs.JOB_TYPES)}")
    # Visibility gate first: an instance the caller may not see (another
    # user's upload, or the owner's seed data for a plain user) answers 404
    # exactly like a nonexistent id (owner-or-admin; no existence leak).
    inst = (registry.get_instance_for(inst_id, user)
            if isinstance(inst_id, str) else None)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    try:
        job_id = jobs.start_job(jtype, inst, body.get("params") or {},
                                owner=user["email"])
    except jobs.JobError as exc:
        raise HTTPException(exc.http_status, str(exc)) from exc
    _bump_user_stat(user["email"], "jobs_run")
    out = {"job_id": job_id}
    if inst.get("source") in ("gerad", "bts"):
        # note stays for API compatibility; note_code lets the frontend
        # translate the warning itself.
        out["note"] = SEED_OVERWRITE_NOTE
        out["note_code"] = "seed_overwrite"
    return out


@app.get("/api/flow")
def api_flow(request: Request):
    """Состояние конвейера для главного экрана-схемы (поручение
    владельца 2026-08-28): блоки этапов от ВМП до решений, активный
    блок и живые параметры. Доступ — как у списка задач: любой
    вошедший пользователь (агрегаты без содержимого наборов)."""
    _user(request)
    from . import flow
    st = dict(flow.build_state())
    # Предел размера пакета для загрузки с блока «Постановка (ВМП)»
    # (перетаскивание, слово владельца 06.09.2026): клиент отказывает
    # словами до запроса; число одно на обе стороны — CREW_CHANNEL_MAX_MB.
    st["upload_max_bytes"] = CHANNEL_BODY_MAX_BYTES
    return st


@app.post("/api/task-package/receive")
async def api_task_package_receive(request: Request):
    """Приём пакета постановки каналом CDPM -> COSMOS (машинный путь,
    вход по токену канала, не по личности). Пакет немедленно
    верифицируется (гейт производителя ин-процессно + матрица
    готовности), кладётся во входной каталог данных и получает краткий
    вердикт; расчёт НЕ стартует. Повтор того же sha идемпотентен —
    тот же вердикт с already_accepted=true."""
    from . import task_channel
    state = task_channel.token_state(
        request.headers.get("X-Channel-Token"))
    if state == "off":
        # Fail-closed: канал без CREW_CHANNEL_TOKEN не существует;
        # ничего не читается и не пишется.
        raise HTTPException(
            403, "канал выключен: CREW_CHANNEL_TOKEN не задан")
    if state != "ok":
        # Причина не детализируется: неверный токен и его отсутствие
        # отвечают одинаково.
        raise HTTPException(403, "доступ запрещён")
    # Лимит тела — по образцу /api/import/*: отказ по Content-Length
    # ДО чтения и по факту после.
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > CHANNEL_BODY_MAX_BYTES:
        raise HTTPException(413, f"request body over "
                                 f"{CHANNEL_BODY_MAX_BYTES // 2**20} MB")
    raw = await request.body()
    if len(raw) > CHANNEL_BODY_MAX_BYTES:
        raise HTTPException(413, f"request body over "
                                 f"{CHANNEL_BODY_MAX_BYTES // 2**20} MB")
    # Вся синхронная работа (гейт, разбор zip, верификация матрицы) —
    # в пуле потоков, не на event loop.
    from starlette.concurrency import run_in_threadpool
    status, brief = await run_in_threadpool(
        task_channel.receive, raw,
        request.headers.get("X-Package-Sha256"),
        request.headers.get("X-Spec-Version"),
        request.headers.get("X-Source-Run"))
    return JSONResponse(status_code=status, content=brief)


@app.get("/api/task-package/current")
def api_task_package_current(request: Request):
    """Карточка текущего пакета постановки (панель ВМП конвейера):
    манифест, правила по зонам, критерий с версией ставок,
    верифицированная матрица готовности, источник получения (файл
    руками / канал + время) и хвост журнала приёмов. Отдельно от
    /api/flow: тот опрашивается раз в 2 с, карточка тяжелее и
    открывается по клику. Доступ — как у /api/flow: любой вошедший."""
    _user(request)
    from . import task_channel
    return task_channel.card_state()


# --- Пакет постановки: загрузка человеком и расчёт в COSMOS ------------
# Решение владельца 04.09.2026: работа CDPM заканчивается выпуском и
# экспортом ВМП; загрузка пакета, выбор решателей, кнопки «Пейринг» и
# «Ростеринг», сертификация, выгрузка решений и карта ростера — здесь.

@app.post("/api/task-package/upload")
async def api_task_package_upload(request: Request):
    """Загрузка пакета постановки человеком (архив из CDPM — кнопкой в
    панели блока «Постановка (ВМП)» либо перетаскиванием zip на блок):
    тот же гейт производителя и матрица готовности, что у машинного
    канала; пакет становится текущим. Расчёт загрузкой НЕ стартует.
    Предел размера — CREW_CHANNEL_MAX_MB (клиент узнаёт его из
    GET /api/flow полем upload_max_bytes и отказывает словами до
    запроса); здесь три рубежа: отсев по Content-Length ДО разбора формы
    (форма multipart чуть больше файла, сравнение с запасом на
    обрамление) — работает только при наличии заголовка; тело без него
    (Transfer-Encoding: chunked — машинный клиент по токену, curl) сервер
    разбирает частями на диск, и тогда отказ идёт по размеру части,
    который известен ДО чтения файла в память (ревизия 07.09.2026: раньше
    файл целиком поднимался в память до сравнения); чтение — не больше
    предела плюс байт, лишний байт даёт тот же отказ. Файл берётся из
    формы вручную, а не параметром UploadFile: параметр заставил бы
    FastAPI разобрать всё тело до входа в обработчик, и отсев был бы
    мнимым; за это форма закрывается здесь же (finally) — иначе
    временный файл части жил бы до сборщика мусора."""
    _user_or_channel(request)
    from . import task_channel
    lim = CHANNEL_BODY_MAX_BYTES
    too_big = ("тело запроса больше предела %s — пакет не принят"
               % ("%d МБ" % (lim // 2**20) if lim >= 2**20
                  else "%d байт" % lim))
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > lim + 64 * 1024:
        raise HTTPException(413, too_big)
    form = await request.form()
    try:
        file = form.get("file")
        if file is None or not hasattr(file, "filename"):
            raise HTTPException(400, "в форме нет файла пакета (поле file)")
        if Path(file.filename or "").suffix.lower() != ".zip":
            raise HTTPException(400,
                                "пакет постановки принимается zip-файлом")
        # Размер части известен разборщику формы до чтения (Starlette
        # считает байты при записи во временный файл); при его отсутствии
        # (старший разборщик) судит ограниченное чтение ниже.
        size = getattr(file, "size", None)
        if size is not None and size > lim:
            raise HTTPException(413, too_big)
        raw = await file.read(lim + 1)
        if len(raw) > lim:
            raise HTTPException(413, too_big)
    finally:
        await form.close()
    import hashlib as _hl
    from starlette.concurrency import run_in_threadpool
    # Отпечаток — по байтам, как у машинного канала (там его несёт
    # заголовок отправителя; здесь отправитель — человек).
    status, brief = await run_in_threadpool(
        task_channel.receive, raw, _hl.sha256(raw).hexdigest(), None, None,
        "upload")
    return JSONResponse(status_code=status, content=brief)


@app.post("/api/task-package/current/press")
def api_task_package_press(request: Request, task: str = "pairing",
                           engine: str | None = None,
                           mode: str | None = None,
                           objective_id: str | None = None,
                           spentLimitSeconds: int = 60,
                           unimprovedSpentLimitSeconds: int | None = None,
                           pairingRunId: str | None = None,
                           acceptedUncovered: str | None = None,
                           pollSeconds: float | None = None,
                           moves: str | None = None,
                           deterministic: int = 0, seed: int = 0,
                           stepLimit: int | None = None,
                           unimprovedStepLimit: int | None = None,
                           deterministicTime: float | None = None):
    """Кнопка «пакет → расписание» на текущем пакете: пейринг (движок
    отбора выбирается здесь и только здесь) либо ростеринг двумя
    путями — сквозной (без pairingRunId, по рейсо-местам пакета, каталог
    button/) либо НА явно названном pairingRunId: прогон обязан быть
    утверждённым пейрингом одной из попыток ЭТОГО пакета (совместимость
    с текущим пакетом/каталогом/составом рейсов проверяется до счёта,
    каталог button-on-pairing/); недопустимый или чужой ID не
    подменяется ни «последним пейрингом», ни сквозным путём — отказ
    словами (409). Ход — в состоянии, выдача и сертификат —
    артефактами. moves — набор ходов поиска решателя (pillar |
    pillar-rr | chain | chain-seed; пусто —
    умолчание решателя), шаг 4 плана 05.09.2026. deterministic=1 —
    профиль Д1 воспроизводимости (seed, stepLimit, unimprovedStepLimit,
    deterministicTime; проект прогонов 07.09.2026, раздел 7.1):
    умолчание — стенные часы, как прежде.

    Кто нажал — из личности запроса (сессия панели, машинный канал по
    токену, режим разработчика площадки): слова актора строит
    `package_runs.actor_words`, нажатие кладёт их в запись попытки, в
    состояние линии и в отказ «занято» (разбор 09.09.2026: на Prod
    ростер нажал второй актор из браузера, и разобрать по записям, кто
    это был, было нельзя). Сам токен канала в запись не попадает —
    только 12 знаков его отпечатка."""
    user = _user_or_channel(request)
    from . import package_runs, run_gate
    actor = package_runs.actor_words(
        user, channel_token=request.headers.get("X-Channel-Token"),
        mode=auth.get_auth_mode())
    if pollSeconds is not None and not (0.5 <= pollSeconds <= 60):
        raise HTTPException(400, "pollSeconds — шаг опроса решателя "
                                 "в границах 0.5..60 секунд")
    # Гейт «от пакета» (проект «Запись о прогоне», раздел 10): нажатие
    # идёт только у ОБЪЯВЛЕННОГО прогона и только при сошедшейся среде.
    # Отказ — 409 в том же виде, что у отказов нажатия (`detail`
    # строкой), плюс код причины и слова перечнем: прежние клиенты
    # читают detail как раньше, новым есть за что взяться.
    try:
        run_gate.check_press(task=task, actor=actor)
    except run_gate.RunGateError as e:
        return JSONResponse(status_code=e.status, content=e.payload())
    profile = ({"seed": int(seed), "step_limit": stepLimit,
                "unimproved_step_limit": unimprovedStepLimit,
                "deterministic_time": deterministicTime}
               if deterministic else None)
    try:
        return package_runs.press(
            task, engine=engine, mode=mode, objective_id=objective_id,
            spent_limit_s=spentLimitSeconds,
            unimproved_spent_limit_s=unimprovedSpentLimitSeconds,
            pairing_run_id=pairingRunId,
            moves=(moves or "").strip() or None,
            accepted_uncovered=[x for x in (acceptedUncovered or "").split(",")
                                if x] or None,
            poll_s=pollSeconds, deterministic=profile, actor=actor)
    except package_runs.PackageRunError as e:
        raise HTTPException(409, str(e)) from None


@app.get("/api/task-package/current/press")
def api_task_package_press_status(request: Request,
                                  attempt: str | None = None):
    """Состояние кнопки на текущем пакете и ссылки артефактов каждой
    выполненной задачи (страница «Пакет постановки»); attempt —
    состояние одной попытки нажатия по ID (аудит A10, ступень M)."""
    _user_or_channel(request)
    from . import package_runs
    try:
        # Без attempt — прежний вызов без аргументов: подмены статуса в
        # стендах клиента (Stand.status()) не знают ключа.
        out = (package_runs.status(attempt=attempt) if attempt
               else package_runs.status())
    except package_runs.PackageRunError as e:
        raise HTTPException(404, str(e)) from None
    if attempt and isinstance(out, dict) and out.get("present") \
            and isinstance(out.get("attempt"), dict):
        # Рецепт попытки (паспорт прогона, 07.09.2026) — по требованию,
        # только у ручки одной попытки; сбой чтения — словами, не 500.
        try:
            from . import cosmos_runs, task_channel
            cur = task_channel.current_package() or {}
            sha = str(out.get("package_sha") or cur.get("sha") or "")
            wd = cosmos_runs.workdir_of(cur, sha)
            out["recipe"] = cosmos_runs.attempt_recipe(wd, out["attempt"])
        except Exception as e:                            # noqa: BLE001
            out["recipe"] = None
            out["recipe_note"] = "рецепт попытки не прочитан: %s" % str(e)[:200]
    return out


#  ─────────────────────────── Запись о прогоне (проект 10.09.2026) ──
# Гейт «от пакета» и карточка прогона: голова записи, объявление
# частного прогона от готового ВМП, состояние сшивки с хозяином записи.
# Личность — сессия ЛИБО токен канала: раннер наборов и панель площадки
# ходят сюда без сессии человека.

def _run_actor(request: Request, user: dict) -> str:
    """Актор запроса словами (та же переменная, что у нажатия)."""
    from . import package_runs
    return package_runs.actor_words(
        user, channel_token=request.headers.get("X-Channel-Token"),
        mode=auth.get_auth_mode())


@app.post("/api/run/declare")
def api_run_declare(request: Request, body: dict | None = Body(default=None)):
    """«Новый прогон от пакета»: объявление ЧАСТНОГО прогона, который
    начался входом COSMOS от готового ВМП (решение владельца № 2 от
    10.09.2026).

    Тело: {"purpose", "contour", "created_by", "models", "solvers",
    "planned_stages", "expectations", "answer_policy", "stop_after",
    "branch_of"} — все поля необязательны: чего не назвали, то берётся
    из среды площадки и из параметров кнопки по умолчанию, а слова
    сверяются со словарями записи. Ответ 200 — {"ok": true, "record":
    …}. Отказы: 409 package-missing (текущего пакета нет), 409
    already-declared (голова записи неизменна: прогон уже объявлен
    хозяином либо этим же COSMOS), 422 declaration-invalid (слово вне
    словаря или объявление не по словарю полей — слова перечнем)."""
    user = _user_or_channel(request)
    from . import run_gate
    try:
        return run_gate.declare(body, _run_actor(request, user))
    except run_gate.RunGateError as e:
        return JSONResponse(status_code=e.status,
                            content={"ok": False, **e.payload()})


@app.get("/api/run/current")
def api_run_current(request: Request):
    """Карточка прогона текущего пакета: голова записи, ЕДИНАЯ запись (у
    хозяина по `record_api`, иначе местная копия либо своя запись
    частного прогона), объявление среды COSMOS с исходом последней
    сверки, записи этапов COSMOS, состояние сшивки, состояние прогона и
    предзаполнение формы объявления. Прогон не объявлен — не отказ:
    поле `declared` = false и код причины `run-not-declared`."""
    user = _user_or_channel(request)
    from . import run_gate
    try:
        return run_gate.current_view(_run_actor(request, user))
    except run_gate.RunGateError as e:
        return JSONResponse(status_code=e.status, content=e.payload())


@app.get("/api/run/environment")
def api_run_environment(request: Request, scope: str | None = None):
    """Среда COSMOS для ГОЛОВЫ записи о прогоне (договор с CDPM
    11.09.2026): площадка, код, каталог и модели COSMOS по ролям —
    сертификатор и читатель ИДАК, каждая строка со словом
    `system: "cosmos"`.

    Голову чеканит ХОЗЯИН записи (панель CDPM либо раннер наборов), и
    своих моделей COSMOS он не знает: до 11.09.2026 они в голову не
    попадали вовсе (находка К5). Хозяин спрашивает эту ручку ДО чеканки
    (отпечаток объявления считается по полной голове); не дождался —
    кладёт в голову `models_note`, а не выдумывает модели.

    Параметр `scope` — допуск моделей НАБОРА (`any` | `local-only`): им
    выбираются сертификатор и читатель ИДАК, как при нажатии. Не назван
    или вне словаря — `local-only` (наружу ничего не уходит без явного
    допуска). Ручка ЧИТАЮЩАЯ: ничего не пишет и текущего пакета не
    требует — при объявлении пакета в COSMOS ещё нет. Ответ 200:
    `{schema: "cosmos-run-environment/v1", contour, llm_scope, code,
    catalog, models[]}`."""
    _user_or_channel(request)
    from . import run_gate
    return run_gate.environment_answer(scope)


@app.post("/api/run/stitch")
def api_run_stitch(request: Request):
    """Повтор сшивки: отправить хозяину записи очередь этапов, которая
    не ушла (панель была недоступна либо отказала). Идемпотентно —
    повтор ключа (система, этап, подэтап) у хозяина заменяет запись, а
    не удваивает её."""
    _user_or_channel(request)
    from . import package_runs, run_gate, run_journal
    res = package_runs.resolve_current()
    if res is None:
        return JSONResponse(status_code=409, content={
            "detail": "текущего пакета постановки нет — сшивать нечего",
            "code": run_gate.CODE_PACKAGE_MISSING})
    _cur, _sha, pkg_path, wd, _run = res
    head = run_gate.head_for(wd, package_runs._package_manifest(pkg_path))
    got = run_journal.flush_outbox(wd, head, explicit_retry=True)
    state = run_journal.stitch_state(wd, head)
    # Исход повтора называется словом (`outcome`), а счётчик отказов —
    # из состояния сшивки: несостоявшаяся отправка отказом НЕ считается
    # (разбор 11.09.2026), и всплывающая подсказка обязана сказать то же,
    # что карточка.
    return {"ok": not got.get("words"), "sent": got.get("sent"),
            "waiting": got.get("waiting"), "refused": state.get("refused"),
            "outcome": got.get("outcome"), "words": got.get("words") or [],
            "stitch": state}


# ------------------------------------------------------ дерево прогонов
# Раздел 12 проекта «Запись о прогоне»: база прогонов имеет вид дерева.
# Ядро дерева — общий модуль `vmp/run_tree.py` (его читают оба
# продукта), корни обхода и ручки COSMOS — `webapp/run_tree_api.py`.
# Имена ручек у обеих систем ОДНИ (12.2), поэтому здесь их менять
# нельзя, не меняя их у CDPM.

def _tree_refused(e) -> JSONResponse:
    """Отказ ручки дерева: `detail` строкой плюс код и слова перечнем —
    в том же виде, что отказ гейта записи."""
    return JSONResponse(status_code=e.status, content=e.payload())


def _person(request: Request) -> dict:
    """Личность ЧЕЛОВЕКА для ручки золотого прогона.

    Признак золотого ставится подтверждением владельца после приёмки на
    Prod (решение владельца 6.3), поэтому машинный канал эту ручку НЕ
    получает: предъявленный токен канала — отказ 403, а не молчаливый
    переход на личность сессии (fail-closed, как у машинных маршрутов)."""
    from . import run_tree_api
    if request.headers.get("X-Channel-Token"):
        raise HTTPException(403, "золотой прогон подтверждает человек: "
                                 "машинный канал этой ручки не получает "
                                 "(код %s)" % run_tree_api.CODE_GOLDEN_MACHINE)
    return _user(request)


@app.get("/api/run-tree")
def api_run_tree(request: Request, rebuild: int = 0):
    """Индекс дерева прогонов целиком (`cosmos-run-tree/v1`: `roots[]`,
    `orphans[]`, `refusals[]`, `built_at`).

    Читается ФАЙЛ индекса (`<каталог данных>/run-tree.json`): обход
    диска на каждый запрос не делается. `?rebuild=1` — перестроить
    перед ответом. Ответ — сам документ индекса, без добавок: он обязан
    сходиться со своей схемой (`vmp/run_tree.schema.json`).

    Отбор по полям головы записи идёт ЗДЕСЬ, а не в браузере (одно место
    правды): `customer`, `contour`, `state`, `declared_by`, `golden`,
    `since`, `until`, `root`, `q`. Отбор только убирает ветви, форма
    ответа не меняется. Параметр, которого ручка не знает, — отказ 422
    словами: молча показать полный список там, где просили отбор,
    значило бы соврать."""
    _user_or_channel(request)
    from . import run_tree_api
    try:
        run_tree_api.check_params(request.query_params.keys(), ("rebuild",))
        flt = run_tree_api.filters_from(request.query_params)
        return run_tree_api.filter_tree(
            run_tree_api.tree(rebuild_now=bool(rebuild)), flt)
    except run_tree_api.RunTreeApiError as e:
        return _tree_refused(e)


@app.post("/api/run-tree/rebuild")
def api_run_tree_rebuild(request: Request):
    """Перестроить индекс дерева обходом корней.

    Ответ — индекс, корни обхода со словами (какой есть на диске,
    какого нет) и перечень прогонов, ждущих слова владельца о золотом
    (площадка `prod`, состояние «завершён», признака ещё нет)."""
    _user_or_channel(request)
    from . import run_tree_api
    try:
        tree = run_tree_api.rebuild("по ручке")
        return {"ok": True, "tree": tree, "roots": run_tree_api.roots(),
                "counts": run_tree_api.counts(tree),
                "golden_pending": run_tree_api.pending_golden(tree),
                "index_file": str(run_tree_api.index_path())}
    except run_tree_api.RunTreeApiError as e:
        return _tree_refused(e)


@app.get("/api/run-tree/compare")
def api_run_tree_compare(request: Request, a: str = "", b: str = ""):
    """Сравнение двух прогонов дерева по НОМЕРАМ ЗАПИСЕЙ: что менялось в
    объявлении (модели, решатели, параметры) и что изменилось в итогах
    (вердикты, время, токены, счётчики — счётчики «справочно»)."""
    _user_or_channel(request)
    from . import run_tree_api
    try:
        return run_tree_api.compare(a, b)
    except run_tree_api.RunTreeApiError as e:
        return _tree_refused(e)


@app.get("/api/run/current/tree")
def api_run_current_tree(request: Request):
    """Дерево по записи ТЕКУЩЕГО пакета постановки: узел, путь от корня,
    соседние ветви, золотой прогон корня, сравнение с золотым и с
    предыдущим прогоном, сравнение с ожиданиями набора."""
    _user_or_channel(request)
    from . import run_tree_api
    try:
        return run_tree_api.current_view()
    except run_tree_api.RunTreeApiError as e:
        return _tree_refused(e)


@app.get("/api/runs-all")
def api_runs_all(request: Request):
    """Все прогоны ОБЕИХ систем одним списком: записи, которые знает
    COSMOS (индекс дерева), плюс прогоны хранителя-панели CDPM, которых
    в индексе ещё нет.

    `{items, total, words, keeper: {reachable, url}}`. Только чтение:
    удаления и правок на общем экране нет — архивирование остаётся у
    каждой системы в своём списке. Панель недоступна — список из своих
    записей и строка словами; отбор и поиск — те же, что у ручки дерева,
    плюс `limit`/`offset` («показать ещё»)."""
    _user_or_channel(request)
    from . import run_tree_api, runs_all_api
    try:
        return runs_all_api.view(request.query_params)
    except run_tree_api.RunTreeApiError as e:
        return _tree_refused(e)


@app.post("/api/task-package/current/press/catalog-requests")
def api_press_catalog_requests(request: Request, body: dict = Body(...)):
    """«Заявить в каталог»: подать заявки на ВИД ИСПОЛНИТЕЛЯ по пробелам
    последнего нажатия (артефакт `vmp-rule-gaps.json`, ключ `catalog`).

    Кнопка нужна там, где автоподача выключена (CREW_CATALOG_REQUESTS_AUTO
    = «0») либо доставка в службу не удалась: номера заявок содержательны,
    поэтому повтор идемпотентен — те же номера, файлы очереди не
    переписываются. Тело: {"attempt": null|id, "rule_ids": null|[...]}."""
    # Путь лежит под машинным префиксом ручек нажатия (раннеры площадок
    # ходят туда токеном канала), а подача заявки в каталог — действие
    # АДМИНИСТРАТОРА рабочего места. Без личности сессии — честный отказ
    # словами, а не 500 «личности нет».
    if not isinstance(getattr(request.state, "user", None), dict):
        raise HTTPException(
            403, "подача заявок в каталог шаблонов — действие "
                 "администратора: токен канала для неё не годится")
    _admin(request)
    from . import catalog_pipeline, package_runs
    attempt = (str(body.get("attempt")) if body.get("attempt") else None)
    only = {str(x) for x in (body.get("rule_ids") or ())}
    try:
        path, _name = package_runs.artifact_path(
            "пробелы-шаблонов-пейринга", attempt=attempt)
    except package_runs.PackageRunError as e:
        raise HTTPException(404, str(e)) from None
    try:
        art = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(422, f"артефакт пробелов не читается: {e}") \
            from None
    try:
        rows, problems = catalog_pipeline.press_rows(art, only)
    except catalog_pipeline.CatalogPipelineError as e:
        raise HTTPException(503, detail=str(e)) from None
    if not rows:
        return {"rows": {}, "problems": problems,
                "words": ("пробелов относительно записей каталога в этом "
                          "нажатии нет: заявка на вид исполнителя не "
                          "подаётся")}
    res = package_runs.resolve_current()
    run = res.run if (res is not None and isinstance(res.run, dict)) else {}
    try:
        out = catalog_pipeline._product().submit_kind_requests(
            rows, run_name=str(run.get("run") or ""), out_dir=path.parent)
    except catalog_pipeline.CatalogPipelineError as e:
        raise HTTPException(503, detail=str(e)) from None
    except Exception as e:                               # noqa: BLE001
        raise HTTPException(422, detail=str(e)) from None
    if problems:
        out = dict(out)
        out["problems"] = list(out.get("problems") or ()) + problems
    return out


@app.get("/api/task-package/current/press/file/{alias}")
def api_task_package_press_file(request: Request, alias: str,
                                attempt: str | None = None):
    """Артефакты нажатия по закрытому перечню алиасов; двоичные —
    файлом на скачивание, markdown — страницей, json — как есть;
    attempt — из снимка попытки по ID (аудит A10, ступень M)."""
    _user_or_channel(request)
    from . import package_runs
    try:
        # Без attempt — прежний вызов (подмены в стендах клиента кнопки).
        path, name = (package_runs.artifact_path(alias, attempt=attempt)
                      if attempt else package_runs.artifact_path(alias))
    except package_runs.PackageRunError as e:
        raise HTTPException(404, str(e)) from None
    if not name.endswith((".json", ".md")):
        from fastapi.responses import FileResponse
        return FileResponse(str(path), filename=f"package-{name}",
                            media_type="application/zip"
                            if name.endswith(".zip") else
                            "application/octet-stream")
    text = path.read_text(encoding="utf-8")
    if name.endswith(".md"):
        import markdown
        return HTMLResponse(
            "<meta charset='utf-8'><style>body{font-family:sans-serif;"
            "max-width:60em;margin:2em auto;padding:0 1em}table{border-"
            "collapse:collapse}td,th{border:1px solid #999;padding:4px "
            "8px;vertical-align:top}</style>" +
            markdown.markdown(text, extensions=["tables"]))
    return JSONResponse(json.loads(text))


@app.post("/api/task-package/current/export-mps")
def api_task_package_export_mps(request: Request):
    """Экспорт точной модели покрытия (MPS/LP + справочник + манифест)
    для внешнего CPLEX по артефактам последнего нажатия «Пейринг» —
    в фоне; перечень файлов появляется в состоянии кнопки."""
    _user_or_channel(request)
    from . import package_runs
    try:
        return package_runs.export_mps()
    except package_runs.PackageRunError as e:
        raise HTTPException(409, str(e)) from None


@app.get("/api/task-package/current/export-mps/file/{name}")
def api_task_package_export_mps_file(request: Request, name: str):
    """Файлы готового экспорта — только из перечня манифеста."""
    _user_or_channel(request)
    from . import package_runs
    try:
        path = package_runs.export_file(name)
    except package_runs.PackageRunError as e:
        raise HTTPException(404, str(e)) from None
    return FileResponse(str(path), filename=name)


@app.get("/api/task-package/current/roster-map")
def api_task_package_roster_map(request: Request):
    """Карта ростера по решению кнопки ростеринга на текущем пакете
    (формат общего движка карты NetMap)."""
    _user_or_channel(request)
    from . import package_runs
    try:
        return package_runs.roster_map()
    except package_runs.PackageRunError as e:
        raise HTTPException(409, str(e)) from None


# --- Выгрузки решений пакета в известных форматах (07.09.2026) --------
# Слово владельца 06.09.2026: пейринг и ростеринг выгружать в разных
# известных форматах, в том числе Meridian. Файлы строятся по требованию
# из артефактов последней выдачи (либо снимка попытки) чистыми функциями
# vmp/export_solutions.py; ничего не хранится. Отказы — словами:
# 404 (нет пакета/выдачи/попытки/сотрудника/формата), 409 (выдача-
# остановка, формат не выражает решение, Meridian несертифицированного
# без allow_uncertified=1), 400 (параметр вне словаря).

def _flag(value: str | int | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "да")


@app.get("/api/task-package/current/export")
def api_task_package_export_index(request: Request):
    """Указатель доступности выгрузок для панели «Решения»: по каждому
    решению (pairing, roster) и формату — ok и ссылка либо причина
    словами сервера; без пакета — {present: false}."""
    _user_or_channel(request)
    from . import package_runs
    return package_runs.export_index()


@app.get("/api/task-package/current/export/{solution}/{fmt}")
def api_task_package_export_file(request: Request, solution: str, fmt: str,
                                 attempt: str | None = None,
                                 path: str | None = None,
                                 employee: str | None = None,
                                 allow_uncertified: str | None = None,
                                 utc: str | None = None,
                                 roles: str | None = None,
                                 dataset_id: str | None = None,
                                 preset_id: str | None = None):
    """Файл выгрузки решения: pairing — csv|xlsx|json|meridian, roster —
    csv|xlsx|json|ics|meridian. attempt — снимок попытки; path —
    cold|on-pairing (ростер); employee — iCal одного сотрудника;
    allow_uncertified=1 — Meridian несертифицированного с пометкой;
    utc=1 — перевод меток в UTC по объявленной шкале пакета (умолчание —
    метки как есть, как в наборе CDPM); roles — gate|source (алфавит роли
    участка Meridian); dataset_id/preset_id — явные идентификаторы набора
    и пресета Meridian."""
    _user_or_channel(request)
    from . import package_runs
    from vmp import export_solutions as xs
    try:
        data, name_a, name_u, media, meta = package_runs.export_solution(
            solution, fmt, attempt=attempt, path=path, employee=employee,
            allow_uncertified=_flag(allow_uncertified), utc=_flag(utc),
            roles=roles, dataset_id=dataset_id, preset_id=preset_id)
    except xs.ExportError as e:
        raise HTTPException(e.http_status, str(e)) from None
    headers = {"Content-Disposition": xs.content_disposition(name_a, name_u),
               "X-COSMOS-Certified": "true" if meta["certified"] else "false",
               "X-COSMOS-Package": str(meta["package_sha"]),
               "X-COSMOS-Solver-Run": str(meta["solver_run_id"] or ""),
               "X-COSMOS-Time-Scale": str(meta["time_scale"]),
               "X-COSMOS-Created-Scale": str(meta["created_scale"]),
               "Cache-Control": "no-store"}
    if meta.get("form_status"):
        headers["X-COSMOS-Form-Status"] = str(meta["form_status"])
    return Response(content=data, media_type=media, headers=headers)


# --- Прогоны COSMOS (слово владельца 07.09.2026) ------------------------
# «А почему в COSMOS нет системы прогонов как в CDPM?» — список прогонов
# в том же виде, что у CDPM; пакет из CDPM ложится в ТЕКУЩИЙ прогон;
# паспорт прогона = рецепт воспроизведения. Хранилище и вся логика —
# webapp/cosmos_runs.py (записи-указатели над task-inbox и package-runs);
# здесь — ручки и доступ (пары _MACHINE_ROUTES — сессия или токен канала,
# остальное — только сессия). Отказы — словами с кодом причины
# {"detail", "reason_code"}; параметры пути проверяются до диска.

def _runs_by(user: dict) -> str:
    return "channel" if user.get("role") == "machine" \
        else "user:%s" % (user.get("email") or "?")


from . import cosmos_runs as _cosmos_runs  # noqa: E402 — ручки прогонов ниже


def _runs_refused(e) -> JSONResponse:
    """Отказ действия над прогонами: слова и код причины (клиент читает
    cruns.err.<reason_code> / cruns.note.<reason_code>); у storage-off —
    ещё cause (inbox-off | runs-dir-empty), чтобы клиент назвал настоящую
    переменную."""
    content = {"detail": str(e), "reason_code": getattr(e, "code", None)}
    if getattr(e, "cause", None):
        content["cause"] = e.cause
    return JSONResponse(status_code=int(getattr(e, "status", 409) or 409),
                        content=content)


@app.exception_handler(_cosmos_runs.RunsError)
async def _runs_error_handler(request: Request, exc: _cosmos_runs.RunsError):
    """Любой RunsError из ручки прогонов — `{detail, reason_code}` с его
    кодом HTTP (контракт раздела 4/9.2: отказ словами с кодом), в том
    числе проверки параметров пути до обращения к диску."""
    return _runs_refused(exc)


def _runs_write(fn):
    """Пишущее действие над прогонами: RunsError — словами с кодом; срок
    файлового замка (TimeoutError, «хранилище занято другим процессом
    дольше 30 с») и ошибка записи (OSError) — 503 словами с кодом
    storage-busy / storage-io, не 500 без слов (ревью 07.09)."""
    try:
        return fn()
    except _cosmos_runs.RunsError as e:
        return _runs_refused(e)
    except TimeoutError as e:
        return JSONResponse(status_code=503, content={
            "detail": str(e) or "хранилище прогонов занято другим процессом "
                                "— действие отменено",
            "reason_code": "storage-busy"})
    except OSError as e:
        return JSONResponse(status_code=503, content={
            "detail": "хранилище прогонов: ошибка записи — %s" % str(e)[:300],
            "reason_code": "storage-io"})


def _run_id_or_404(run_id: str) -> str:
    if not _cosmos_runs.RUN_ID_RE.fullmatch(str(run_id or "")):
        raise _cosmos_runs.RunsError("прогон %r не существует" % str(run_id),
                                     status=404, code="bad-run-id")
    return str(run_id)


def _attempt_id_or_400(attempt: str) -> str:
    if not _cosmos_runs.ATTEMPT_ID_RE.fullmatch(str(attempt or "")):
        raise _cosmos_runs.RunsError("идентификатор попытки не похож на свой",
                                     status=400, code="bad-attempt-id")
    return str(attempt)


def _artifact_response(path: Path, name: str):
    """Артефакт снимка попытки: .jsonl — текстом как есть; двоичные —
    файлом на скачивание; markdown — страницей; json — как есть."""
    if name.endswith(".jsonl"):
        return PlainTextResponse(path.read_bytes().decode("utf-8", "replace"),
                                 media_type="text/plain; charset=utf-8")
    if not name.endswith((".json", ".md")):
        return FileResponse(str(path), filename=f"package-{name}",
                            media_type="application/zip"
                            if name.endswith(".zip") else
                            "application/octet-stream")
    text = path.read_text(encoding="utf-8")
    if name.endswith(".md"):
        import markdown
        return HTMLResponse(
            "<meta charset='utf-8'><style>body{font-family:sans-serif;"
            "max-width:60em;margin:2em auto;padding:0 1em}table{border-"
            "collapse:collapse}td,th{border:1px solid #999;padding:4px "
            "8px;vertical-align:top}</style>" +
            markdown.markdown(text, extensions=["tables"]))
    return JSONResponse(json.loads(text))


@app.get("/api/cosmos-runs")
def api_cosmos_runs_list(request: Request):
    """Список прогонов COSMOS: записи по действующему порядку, текущий,
    состояние хранилища (off | uninitialized | ready) со сводкой легаси
    до оформления. Только чтение — ничего не пишет и не оформляет."""
    _user_or_channel(request)
    from . import cosmos_runs
    return cosmos_runs.list_runs()


@app.post("/api/cosmos-runs")
def api_cosmos_runs_create(request: Request,
                           body: dict | None = Body(default=None)):
    """Новый прогон: {"title"?, "make_current"? = true}. 409 storage-off /
    env-package / current-busy — словами с кодом; первое пишущее
    действие заводит хранилище (оформление легаси прогонами)."""
    user = _user_or_channel(request)
    from . import cosmos_runs
    body = body if isinstance(body, dict) else {}
    mc = body.get("make_current", True)
    return _runs_write(lambda: cosmos_runs.create_run(
        body.get("title"), origin="manual", created_by=_runs_by(user),
        make_current=True if mc is None else bool(mc)))


@app.post("/api/cosmos-runs/init")
def api_cosmos_runs_init(request: Request):
    """Кнопка «Оформить прогоны»: завести хранилище — прогон на каждый
    принятый пакет; нажатия и снимки на месте. Идемпотентно."""
    _user(request)
    from . import cosmos_runs
    return _runs_write(lambda: cosmos_runs.ensure_storage("init"))


@app.get("/api/cosmos-runs/current")
def api_cosmos_runs_current(request: Request):
    _user_or_channel(request)
    from . import cosmos_runs
    return cosmos_runs.current_state()


@app.put("/api/cosmos-runs/current")
def api_cosmos_runs_set_current(request: Request, body: dict = Body(...)):
    """Сделать прогон текущим: указатель и зеркало канала (у прогона без
    пакета зеркало удаляется); 400/404/409 словами с кодом."""
    user = _user(request)
    from . import cosmos_runs
    run = body.get("run") if isinstance(body, dict) else None
    return _runs_write(lambda: cosmos_runs.set_current(str(run or ""),
                                                       _runs_by(user)))


@app.get("/api/cosmos-runs/order")
def api_cosmos_runs_order(request: Request):
    _user(request)
    from . import cosmos_runs
    return {"order": cosmos_runs.effective_order()}


@app.put("/api/cosmos-runs/order")
def api_cosmos_runs_order_put(request: Request, body: dict = Body(...)):
    _user(request)
    from . import cosmos_runs
    return _runs_write(lambda: cosmos_runs.set_order(
        body.get("order") if isinstance(body, dict) else None))


@app.post("/api/cosmos-runs/archive")
def api_cosmos_runs_archive(request: Request, body: dict = Body(...)):
    """Архив выбранных прогонов (fail-closed; текущий и занятые
    отказывают словами); пакеты, нажатия и снимки ИДАК не затрагиваются."""
    _user(request)
    from . import cosmos_runs
    return _runs_write(lambda: cosmos_runs.archive(
        body.get("runs") if isinstance(body, dict) else None))


@app.put("/api/cosmos-runs/{run_id}/title")
def api_cosmos_runs_title(request: Request, run_id: str,
                          body: dict = Body(...)):
    _user(request)
    from . import cosmos_runs
    run_id = _run_id_or_404(run_id)

    def _do():
        cosmos_runs.read_run(run_id)
        return cosmos_runs.set_title(run_id, body.get("title")
                                     if isinstance(body, dict) else None)
    return _runs_write(_do)


@app.get("/api/cosmos-runs/{run_id}")
def api_cosmos_runs_card(request: Request, run_id: str):
    """Карточка прогона: запись, состояние, пакеты, попытки всех рабочих
    каталогов со ссылками области прогона, паспорт."""
    _user_or_channel(request)
    from . import cosmos_runs
    run_id = _run_id_or_404(run_id)
    try:
        return cosmos_runs.run_card(run_id)
    except cosmos_runs.RunsError as e:
        return _runs_refused(e)


@app.get("/api/cosmos-runs/{run_id}/record")
def api_cosmos_runs_record(request: Request, run_id: str):
    """Запись о прогоне по ТЕКУЩЕМУ пакету названного прогона COSMOS —
    та же единая запись, что у карточки `/api/run/current`, но по
    номеру прогона: её читают машинные читатели (раннер наборов
    проверяет ожидание «запись сшита»)."""
    _user_or_channel(request)
    from . import run_gate
    run_id = _run_id_or_404(run_id)
    try:
        return run_gate.record_of_run(run_id)
    except run_gate.RunGateError as e:
        return JSONResponse(status_code=e.status, content=e.payload())
    except _cosmos_runs.RunsError as e:
        return _runs_refused(e)


@app.get("/api/cosmos-runs/{run_id}/tree")
def api_cosmos_runs_tree(request: Request, run_id: str):
    """Дерево по записи названного прогона COSMOS (раздел 12.2): узел,
    путь от корня (входы → родитель → прогон), соседние ветви с
    колонкой «чем отличается», золотой прогон корня, сравнение с
    золотым (по умолчанию) и с предыдущим прогоном, сравнение с
    ожиданиями набора."""
    _user_or_channel(request)
    from . import run_tree_api
    run_id = _run_id_or_404(run_id)
    try:
        return run_tree_api.view_of_run(run_id)
    except run_tree_api.RunTreeApiError as e:
        return _tree_refused(e)
    except _cosmos_runs.RunsError as e:
        return _runs_refused(e)


@app.post("/api/cosmos-runs/{run_id}/golden")
def api_cosmos_runs_golden(request: Request, run_id: str,
                           body: dict | None = Body(default=None)):
    """Назвать прогон золотым (прогоном-образцом).

    Решение владельца 6.3 от 10.09.2026: признак ставится ТОЛЬКО
    подтверждением владельца после приёмки на Prod. Тело — `{"by",
    "words"}`: `words` обязательны, `by` допускается лишь как
    подтверждение личности сессии и обязано с ней совпасть. Хозяин
    записи COSMOS (частный прогон) — признак ложится в его запись;
    хозяин панель либо раннер (полный прогон) — подтверждение
    пересылается ему, и ответ хозяина отдаётся как есть.

    Отказы словами: 422 golden-without-words / golden-by-mismatch, 409
    golden-not-prod (площадка записи не `prod`) и golden-run-not-done
    (прогон не завершён), 403 у машинного канала, 502 — хозяин записи
    недоступен."""
    user = _person(request)
    from . import run_tree_api
    run_id = _run_id_or_404(run_id)
    body = body if isinstance(body, dict) else {}
    try:
        got = run_tree_api.golden(run_id, by=user.get("email"),
                                  words=body.get("words"),
                                  confirm_by=body.get("by"), mark=True)
    except run_tree_api.RunTreeApiError as e:
        return _tree_refused(e)
    except _cosmos_runs.RunsError as e:
        return _runs_refused(e)
    if got.get("owner") == run_tree_api.OWNER_REMOTE:
        return JSONResponse(status_code=got["status"], content=got["payload"])
    return got


@app.post("/api/cosmos-runs/{run_id}/golden/unmark")
def api_cosmos_runs_golden_unmark(request: Request, run_id: str,
                                  body: dict | None = Body(default=None)):
    """Снять признак золотого прогона тем же лицом: подтверждение
    отозвано (нашёлся прогон лучше либо признак поставлен по ошибке).
    Слова снимаются вместе с признаком."""
    user = _person(request)
    from . import run_tree_api
    run_id = _run_id_or_404(run_id)
    body = body if isinstance(body, dict) else {}
    try:
        got = run_tree_api.golden(run_id, by=user.get("email"),
                                  words=body.get("words"),
                                  confirm_by=body.get("by"), mark=False)
    except run_tree_api.RunTreeApiError as e:
        return _tree_refused(e)
    except _cosmos_runs.RunsError as e:
        return _runs_refused(e)
    if got.get("owner") == run_tree_api.OWNER_REMOTE:
        return JSONResponse(status_code=got["status"], content=got["payload"])
    return got


@app.get("/api/cosmos-runs/{run_id}/attempts/{attempt}")
def api_cosmos_runs_attempt(request: Request, run_id: str, attempt: str):
    """Попытка прогона: запись, рецепт воспроизведения, файлы и алиасы
    снимка."""
    _user_or_channel(request)
    from . import cosmos_runs
    run_id = _run_id_or_404(run_id)
    attempt = _attempt_id_or_400(attempt)
    try:
        sha, wd, rec = cosmos_runs.resolve_attempt(run_id, attempt)
        doc = cosmos_runs.read_run(run_id)
    except cosmos_runs.RunsError as e:
        return _runs_refused(e)
    pkg = next((p for p in doc.get("packages") or []
                if isinstance(p, dict) and p.get("sha") == sha), None)
    root = wd / cosmos_runs.ATTEMPTS_DIR / attempt
    files = sorted(str(p.relative_to(root)).replace("\\", "/")
                   for p in root.rglob("*") if p.is_file()) \
        if root.is_dir() else []
    aliases = [a for a, (sub, name) in cosmos_runs._aliases().items()
               if (root / sub / name).is_file()]
    return {"run": run_id, "package_sha": sha, "attempt": rec,
            "recipe": cosmos_runs.attempt_recipe(wd, rec, pkg),
            "files": files, "aliases": aliases}


@app.get("/api/cosmos-runs/{run_id}/attempts/{attempt}/file/{alias}")
def api_cosmos_runs_attempt_file(request: Request, run_id: str,
                                 attempt: str, alias: str):
    _user_or_channel(request)
    from . import cosmos_runs
    run_id = _run_id_or_404(run_id)
    attempt = _attempt_id_or_400(attempt)
    try:
        path, name = cosmos_runs.attempt_file(run_id, attempt, alias)
    except cosmos_runs.RunsError as e:
        return _runs_refused(e)
    return _artifact_response(path, name)


@app.get("/api/cosmos-runs/{run_id}/attempts/{attempt}/exchanges")
@app.get("/api/cosmos-runs/{run_id}/attempts/{attempt}/exchanges/{n}")
def api_cosmos_runs_attempt_exchanges(request: Request, run_id: str,
                                      attempt: str, n: int | None = None):
    """Обмены попытки с моделью-сертификатором: без n — строки индекса,
    с n — запрос/ответ/ошибка обмена (тела — байтами как есть)."""
    _user_or_channel(request)
    from . import cosmos_runs
    run_id = _run_id_or_404(run_id)
    attempt = _attempt_id_or_400(attempt)
    try:
        _sha, wd, rec = cosmos_runs.resolve_attempt(run_id, attempt)
    except cosmos_runs.RunsError as e:
        return _runs_refused(e)
    d = cosmos_runs.exchanges_dir(wd, rec)
    idx = d / "index.jsonl"
    if not idx.is_file():
        ex = rec.get("exchanges") if isinstance(rec.get("exchanges"), dict) \
            else {}
        return JSONResponse(status_code=404, content={
            "detail": "обмены попытки %s с моделью не записаны" % attempt,
            "reason_code": ex.get("reason_code") or "pre-passport"})
    rows = []
    for ln in idx.read_bytes().decode("utf-8", "replace").splitlines():
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except ValueError:
            continue
    if n is None:
        return {"run": run_id, "attempt": attempt, "rows": rows}
    row = next((r for r in rows if isinstance(r, dict) and r.get("n") == n),
               None)
    if row is None:
        return JSONResponse(status_code=404, content={
            "detail": "обмена №%d у попытки %s нет" % (n, attempt),
            "reason_code": "exchange-unknown"})
    role = str(row.get("role") or "certifier")
    stem = "%03d-%s" % (n, role)

    def _read(suffix: str) -> str | None:
        p = d / (stem + suffix)
        return p.read_bytes().decode("utf-8", "replace") if p.is_file() \
            else None
    return {"n": n, "role": role, "request": _read(".request.txt"),
            "reply": _read(".reply.txt"), "error": _read(".error.txt")}


@app.get("/api/cosmos-runs/{run_id}/attempts/{attempt}/export")
def api_cosmos_runs_attempt_export_index(request: Request, run_id: str,
                                         attempt: str):
    """Указатель выгрузок попытки прогона — как у текущего пакета, но по
    рабочему каталогу пары (прогон, пакет); ссылки — области прогона."""
    _user_or_channel(request)
    from . import cosmos_runs, package_runs
    run_id = _run_id_or_404(run_id)
    attempt = _attempt_id_or_400(attempt)
    try:
        _sha, wd, _rec = cosmos_runs.resolve_attempt(run_id, attempt)
    except cosmos_runs.RunsError as e:
        return _runs_refused(e)
    out = package_runs.export_index(wd=wd)
    base = "/api/cosmos-runs/%s/attempts/%s/export" % (run_id, attempt)
    for kind, sol in ((out or {}).get("solutions") or {}).items():
        for fmt, info in (sol.get("formats") or {}).items():
            if isinstance(info, dict):
                info["href"] = "%s/%s/%s" % (base, kind, fmt)
    out["run"] = run_id
    out["attempt"] = attempt
    return out


@app.get("/api/cosmos-runs/{run_id}/attempts/{attempt}/export/{solution}/{fmt}")
def api_cosmos_runs_attempt_export_file(request: Request, run_id: str,
                                        attempt: str, solution: str, fmt: str,
                                        path: str | None = None,
                                        employee: str | None = None,
                                        allow_uncertified: str | None = None,
                                        utc: str | None = None,
                                        roles: str | None = None,
                                        dataset_id: str | None = None,
                                        preset_id: str | None = None):
    """Файл выгрузки из снимка попытки прогона (параметры — как у
    /api/task-package/current/export/{solution}/{fmt})."""
    _user_or_channel(request)
    from . import cosmos_runs, package_runs
    from vmp import export_solutions as xs
    run_id = _run_id_or_404(run_id)
    attempt = _attempt_id_or_400(attempt)
    try:
        _sha, wd, _rec = cosmos_runs.resolve_attempt(run_id, attempt)
    except cosmos_runs.RunsError as e:
        return _runs_refused(e)
    try:
        data, name_a, name_u, media, meta = package_runs.export_solution(
            solution, fmt, attempt=attempt, path=path, employee=employee,
            allow_uncertified=_flag(allow_uncertified), utc=_flag(utc),
            roles=roles, dataset_id=dataset_id, preset_id=preset_id, wd=wd)
    except xs.ExportError as e:
        raise HTTPException(e.http_status, str(e)) from None
    headers = {"Content-Disposition": xs.content_disposition(name_a, name_u),
               "X-COSMOS-Certified": "true" if meta["certified"] else "false",
               "X-COSMOS-Package": str(meta["package_sha"]),
               "X-COSMOS-Solver-Run": str(meta["solver_run_id"] or ""),
               "X-COSMOS-Time-Scale": str(meta["time_scale"]),
               "X-COSMOS-Created-Scale": str(meta["created_scale"]),
               "X-COSMOS-Run": run_id,
               "Cache-Control": "no-store"}
    if meta.get("form_status"):
        headers["X-COSMOS-Form-Status"] = str(meta["form_status"])
    return Response(content=data, media_type=media, headers=headers)


@app.post("/api/cosmos-runs/{run_id}/attempts/{attempt}/replay")
def api_cosmos_runs_attempt_replay(request: Request, run_id: str,
                                   attempt: str, body: dict = Body(...)):
    """Повтор попытки (раздел 17 проекта): {"mode": "journal" | "fresh"}
    → новая попытка-повтор в рабочем каталоге и на пакете попытки-
    источника через package_runs.press(replay=…). Полная проверка
    доступности (deep=True: хеши тел, зонд сертификатора у «заново») —
    ОДИН раз, внутри press под запуском; ручка её не повторяет и отдаёт
    отказ press как есть — 409 словами с кодом.

    Кто нажал повтор — из личности запроса теми же словами, что у
    ручки нажатия (`package_runs.actor_words`): без этого попытка-
    повтор записывала «неизвестен: вызов без личности». Запуск повтора
    открыт ТОЛЬКО сессии панели (`_user`, машинному каналу закрыт), так
    что актор здесь всегда «user:<почта>» либо «dev:<почта>»."""
    user = _user(request)
    from . import cosmos_runs, package_runs
    actor = package_runs.actor_words(user, mode=auth.get_auth_mode())
    mode = body.get("mode") if isinstance(body, dict) else None
    if mode not in ("journal", "fresh"):
        raise cosmos_runs.RunsError("mode вне словаря: journal, fresh",
                                    status=400, code="bad-mode")
    run_id = _run_id_or_404(run_id)
    attempt = _attempt_id_or_400(attempt)
    try:
        _sha, _wd, rec = cosmos_runs.resolve_attempt(run_id, attempt)
    except cosmos_runs.RunsError as e:
        return _runs_refused(e)
    # Параметры повтора — из записи попытки-источника, не из запроса
    # (17.2); press сам перечитывает их через replay_target.
    params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
    try:
        out = package_runs.press(
            str(rec.get("task") or "pairing"), engine=rec.get("engine"),
            mode=params.get("mode"), objective_id=params.get("objective_id"),
            spent_limit_s=params.get("spent_limit_s") or 60,
            unimproved_spent_limit_s=params.get("unimproved_spent_limit_s"),
            pairing_run_id=params.get("pairing_run_id"),
            accepted_uncovered=params.get("accepted_uncovered"),
            poll_s=params.get("poll_s"), moves=params.get("moves"),
            deterministic=params.get("deterministic"), actor=actor,
            replay={"mode": mode, "run": run_id, "source_attempt": attempt})
    except package_runs.PackageRunError as e:
        return JSONResponse(status_code=409, content={
            "detail": str(e),
            "reason_code": getattr(e, "reason_code", None) or "press-refused"})
    out = dict(out or {})
    out.setdefault("attempt", out.get("attempt_id"))
    return JSONResponse(status_code=202, content=out)


@app.get("/api/cosmos-runs/{run_id}/attempts/{attempt}/replay-report")
def api_cosmos_runs_attempt_replay_report(request: Request, run_id: str,
                                          attempt: str):
    _user_or_channel(request)
    from . import cosmos_runs
    run_id = _run_id_or_404(run_id)
    attempt = _attempt_id_or_400(attempt)
    try:
        _sha, wd, rec = cosmos_runs.resolve_attempt(run_id, attempt)
    except cosmos_runs.RunsError as e:
        return _runs_refused(e)
    of = (rec.get("replay") or {}).get("of") if isinstance(
        rec.get("replay"), dict) else None
    if not isinstance(of, dict):
        return JSONResponse(status_code=404, content={
            "detail": "попытка %s не является повтором" % attempt,
            "reason_code": "not-a-replay"})
    p = wd / cosmos_runs.ATTEMPTS_DIR / attempt / "replay" / "report.json"
    if not p.is_file():
        words = ((of.get("report") or {}).get("words")
                 if isinstance(of.get("report"), dict) else None)
        return JSONResponse(status_code=404, content={
            "detail": "отчёт повтора не записан: %s" % (words or "файла нет"),
            "reason_code": "report-missing"})
    try:
        report = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return JSONResponse(status_code=404, content={
            "detail": "отчёт повтора не читается: %s" % str(e)[:200],
            "reason_code": "report-missing"})
    return {"run": run_id, "attempt": attempt,
            "source_attempt": of.get("source_attempt"), "mode": of.get("mode"),
            "report": report}


@app.get("/api/jobs")
def api_jobs_list(request: Request):
    user = _user(request)
    return [j for j in jobs.list_jobs() if _job_visible(j, user)]


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, request: Request):
    job = jobs.get_job(job_id)
    if job is None or not _job_visible(job, _user(request)):
        raise HTTPException(404, f"no such job: {job_id}")
    return job


@app.get("/api/jobs/{job_id}/log")
def api_job_log(job_id: str, request: Request):
    job = jobs.get_job(job_id)
    if job is None or not _job_visible(job, _user(request)):
        raise HTTPException(404, f"no such job: {job_id}")
    log = jobs.read_log(job_id)
    if log is None:
        raise HTTPException(404, f"no such job: {job_id}")
    return PlainTextResponse(log)


@app.get("/api/jobs/{job_id}/progress")
def api_job_progress(job_id: str, request: Request):
    """Серия схождения прогона для графика: события улучшений из
    машинного следа (runs/progress/<run_id>.jsonl), который пишут сами
    решатели. Пустая серия — честные []: у задач без следа (прежние
    прогоны, чужие типы) график просто не рисуется."""
    job = jobs.get_job(job_id)
    if job is None or not _job_visible(job, _user(request)):
        raise HTTPException(404, f"no such job: {job_id}")
    import _data_paths
    # Файл следа именуется ЛИЧНОСТЬЮ ПРОГОНА (CREW_RUN_ID), которую
    # задача передала решателю, — не публичным id задачи.
    run_id = jobs.run_id_of(job_id) or job_id
    p = _data_paths.results_dir() / "runs" / "progress" / f"{run_id}.jsonl"
    events = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue          # недописанная последняя строка — не беда
    return {"job_id": job_id, "events": events,
            **jobs.progress_meta(job_id)}


@app.delete("/api/jobs/{job_id}")
def api_job_delete(job_id: str, request: Request):
    """Удалить запись прогона из истории (владелец задачи или
    администратор). Артефакты результатов не трогаются."""
    user = _user(request)
    job = jobs.get_job(job_id)
    if job is None or not _job_visible(job, user):
        raise HTTPException(404, f"no such job: {job_id}")
    if job.get("owner") != user.get("email") \
            and user.get("role") != users.ROLE_ADMIN:
        raise HTTPException(403, "удалять может владелец задачи или "
                                 "администратор")
    try:
        return jobs.delete_job(job_id)
    except jobs.JobError as exc:
        raise HTTPException(exc.http_status, str(exc)) from exc


@app.post("/api/jobs/{job_id}/stop")
def api_job_stop(job_id: str, request: Request):
    job = jobs.get_job(job_id)
    if job is None or not _job_visible(job, _user(request)):
        raise HTTPException(404, f"no such job: {job_id}")
    try:
        return jobs.stop_job(job_id)
    except jobs.JobError as exc:
        raise HTTPException(exc.http_status, str(exc)) from exc


# ---------------------------------------------------------------- results

@app.get("/api/results/{inst_id}")
def api_results(inst_id: str, request: Request):
    # Owner-or-admin for owned instances (404 otherwise, no existence
    # leak); shared instances' results stay shared — any authenticated user.
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    return results.aggregate(inst)


@app.post("/api/instances/{inst_id}/roster/promote")
def api_roster_promote(inst_id: str, request: Request):
    """Явная промоция multirole-OK в активный ростер (аудит-2 P1-06):
    материализует pairing-документ из lane-траекторий и атомарно
    переключает active-указатель. Owner-or-admin; fail-closed при не-OK
    или расхождении отпечатков прогона."""
    user = _user(request)
    inst = registry.get_instance_for(inst_id, user)
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import active_roster
    try:
        out = active_roster.promote_multirole(inst, user["email"])
    except active_roster.PromoteError as exc:
        raise HTTPException(409, str(exc))
    return out


@app.get("/api/instances/{inst_id}/roster/manifest")
def api_roster_manifest(inst_id: str, request: Request):
    """Активный указатель ростера как есть (прозрачность провенанса)."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import active_roster
    from _roster_manifest import active_path
    doc = active_roster._load_json(active_path(inst.get("label") or ""))
    if doc is None:
        raise HTTPException(404, "no active-roster manifest (legacy label)")
    return doc


@app.get("/api/results/{inst_id}/roster")
def api_roster_view(inst_id: str, request: Request):
    """Rich roster + pairing data for the results visualization (crew×day
    grid, fairness, duty timelines, coverage). Same owner/shared gate as
    the scalar results; 404 when there is no stage-2 result to draw."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    try:
        view = results.roster_view(inst)
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(500, f"roster view failed: {exc}") from exc
    if view is None:
        raise HTTPException(404, f"no stage-2 result for {inst_id}")
    return view


@app.get("/api/results/{inst_id}/pairing")
def api_pairing_view(inst_id: str, request: Request):
    """Данные визуализации пейринга («лента цепочек» + «карта сети»):
    цепочки с рейсами и сменами, непокрытые, дуги сети — из ГОТОВОГО
    каталога этапа 1 (см. webapp/pairing_viz.py). Тот же owner/shared-гейт,
    что у скалярных результатов; 404, когда каталога цепочек ещё нет."""
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    from . import pairing_viz
    try:
        view = pairing_viz.pairing_view(inst)
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(500, f"pairing view failed: {exc}") from exc
    if view is None:
        raise HTTPException(404, f"no stage-1 catalog for {inst_id}")
    # Пакет «Карта на гео-подложке» (2026-08-30): координаты станций
    # справочником; режим «карта мира» включает человек на фронте
    # (умолчание — схема: вымышленные коды обезличенных наборов
    # случайно совпадают с настоящими, см. webapp/geo.py).
    from . import geo as geo_mod
    codes = {n.get("code") for n in (view.get("nodes") or [])}
    for a in (view.get("arcs") or []):
        codes.add(a.get("a"))
        codes.add(a.get("b"))
    # Координаты данных набора (latitude/longitude аэропортов) первичны;
    # у набора без пакета класс данных не объявлен — справочник по коду
    # остаётся добором (решение 30.08: режим «карта мира» включает
    # человек), см. webapp/geo.resolve_for.
    known: dict = {}
    try:
        known = geo_mod.known_from_airports(
            (registry.load_payload(registry.instance_payload_path(inst))
             or {}).get("airports"))
    except (OSError, ValueError, TypeError):
        known = {}
    view["geo"] = geo_mod.resolve_for(
        sorted(c for c in codes if c), data_class=None, known=known,
        fallback_reference=True)
    return view


_TASK_MAP_CACHE: dict = {}


@app.get("/api/task-package/current/map")
def api_task_package_map(request: Request):
    """Карта сети ПОСТАНОВКИ (пакет «Карта на гео-подложке»,
    2026-08-30): станции и дуги рейсов из ПРИНЯТОГО пакета — сеть
    видна до всякого счёта. Узлы — аэропорты набора решателя
    (task_package.to_payload), базы — множество баз приписки; дуги
    агрегированы парой станций без направления. Гео-координаты —
    справочником (webapp/geo.py). Гейт производителя здесь не
    перегоняется: пакет уже принят каналом, карта read-only."""
    _user(request)
    from . import task_channel, task_package, geo as geo_mod
    cur = task_channel.current_package()
    if cur is None:
        raise HTTPException(404, "пакета постановки нет")
    key = (cur.get("path"),
           cur.get("sha") or (cur.get("source") or {}).get("sha"))
    if _TASK_MAP_CACHE.get("key") == key:
        return _TASK_MAP_CACHE["net"]
    try:
        pkg = task_package.load(cur["path"], run_gate=False)
        payload = task_package.to_payload(pkg)
    except task_package.TaskPackageRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(500, f"пакет не читается: {exc}") from exc
    bases = {str(e.get("homeAirport") or "").strip()
             for e in payload.get("employees") or []}
    bases.discard("")
    pairs: dict = {}
    for f in payload.get("flights") or []:
        dep = str(f.get("departureAirport") or "").strip()
        arr = str(f.get("arrivalAirport") or "").strip()
        if not dep or not arr:
            continue
        k = tuple(sorted((dep, arr)))
        rec = pairs.setdefault(k, {"a": k[0], "b": k[1], "n": 0,
                                   "ntr": 0})
        rec["n"] += 1
        if f.get("transportOnly"):
            rec["ntr"] += 1
    codes = sorted({a.get("code") for a in payload.get("airports")
                    or []} | bases | {c for k in pairs for c in k}
                   - {None, ""})
    # Срез персонала для кнопки «Персонал» на карте постановки:
    # основные поля, прокрутка на фронте; полный экран экипажа — своя
    # вкладка, здесь только контекст сети (решение владельца 30.08).
    people = [{
        "id": str(e.get("id") or ""),
        "name": str(e.get("name") or e.get("id") or ""),
        "base": str(e.get("homeAirport") or ""),
        "roles": list(e.get("skills") or []),
        "off_days": len(e.get("unavailableDays") or []),
    } for e in payload.get("employees") or []]
    people.sort(key=lambda p: (p["base"], p["id"]))
    # Принцип координат (ночь 05→06.09.2026, общий с CDPM): координаты
    # из книги (лист «Аэропорты» → поля latitude/longitude набора)
    # первичны; справочник по коду — только при явном классе данных
    # open/proprietary из манифеста (kit.data_class); synthetic и
    # «класс не указан» — только книга (см. webapp/geo.resolve_for).
    _kit = (pkg.get("manifest") or {}).get("kit") or {}
    data_class = _kit.get("data_class") if isinstance(_kit, dict) \
        else None
    net = {
        "nodes": [{"code": c, "base": c in bases} for c in codes],
        "arcs": sorted(pairs.values(),
                       key=lambda r: (r["a"], r["b"])),
        "geo": geo_mod.resolve_for(
            codes, data_class=data_class,
            known=geo_mod.known_from_airports(payload.get("airports"))),
        "employees": people,
        "source_run": (pkg.get("manifest") or {}).get("source_run"),
        "customer": (pkg.get("manifest") or {}).get("customer"),
    }
    _TASK_MAP_CACHE.clear()
    _TASK_MAP_CACHE.update(key=key, net=net)
    return net


@app.get("/api/results/{inst_id}/roster.csv")
def api_roster_csv(inst_id: str, request: Request):
    inst = registry.get_instance_for(inst_id, _user(request))
    if inst is None:
        raise HTTPException(404, f"no such instance: {inst_id}")
    try:
        csv_text = results.build_roster_csv(inst)
    except ValueError as exc:
        # Artifact/payload mismatch is an upstream bug — fail loudly.
        raise HTTPException(500, str(exc)) from exc
    if csv_text is None:
        raise HTTPException(404, f"no stage-2 result for {inst_id}")
    return Response(
        content="\ufeff" + csv_text,  # UTF-8 BOM for Excel
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="roster_{inst_id}.csv"'})


@app.get("/api/source-check/protocol")
def api_source_check_protocol(request: Request,
                              task: str | None = None, context_key: str | None = None):
    """Протокол сверки решения с документами авиакомпании — ЧАСТЬ
    ВЫХОДНОГО КОМПЛЕКТА решения (решение владельца 2026-08-31:
    комплект выгрузки = решение + сертификат + протокол сверки).
    С task отдаёт только протокол выбранного решения; context_key
    закрепляет его личность. Без task — исторический общий протокол.
    Нет сверки выбранного решения — честный 404 словами."""
    _user(request)
    import _data_paths
    task = _source_check_task(task)
    root = Path(_data_paths.data_root()) / "source-check"
    if task:
        from . import source_check_status as scs
        try:
            snap = scs.selected_snapshot(task, context_key)
        except scs.ContextChanged as e:
            raise HTTPException(409, str(e)) from e
        if snap.get("status") != "done" or not snap.get("protocol"):
            raise HTTPException(404, "протокола выбранного решения ещё нет")
        p = Path(snap["protocol"])
    else:
        p = root / "protocol-last.md"  # explicit legacy historical endpoint
    if not p.is_file():
        raise HTTPException(
            404, "сверка решения с документами ещё не гонялась — "
                 "протокола нет (vmp.source_check_run)")
    fname = "source-check-protocol%s.md" % (f"-{task}" if task else "")
    return Response(
        content=p.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{fname}"'})


def _source_check_task(task) -> str | None:
    """Задача сверки: pairing | rostering | None (оба решения одним
    протоколом — прежний путь CLI). Чужое слово — 400 словами."""
    t = str(task or "").strip().lower() or None
    if t not in (None, "pairing", "rostering"):
        raise HTTPException(400, "task — pairing либо rostering")
    return t


def _cdpm_run_dir(source_run: str, pkg_path: Path,
                  cur: dict | None = None) -> Path | None:
    """Каталог прогона CDPM с заморозкой клауз (freeze/clauses/*.json)
    для слепого читателя. Порядок: (1) клаузы ВНУТРИ пакета постановки
    (записи freeze/clauses/*.json — пакет как единственный вход COSMOS;
    распаковываются в каталог нажатий пакета), (2) cdpm-runs корня
    данных COSMOS (одна площадка на оба продукта), (3) каталог из
    CREW_CDPM_RUNS_DIR (панель CDPM с другим корнем данных, как у
    локальных стендов). Нет нигде — None, вызывающий отказывает словами.
    Ревизия 06.09.2026 (блоки «Проверка … по ИДАК»)."""
    import zipfile

    import _data_paths
    if not source_run:
        return None
    from cdpm import source_answer_evidence as answer_sources
    if re.fullmatch(r"[\w-]+", source_run) is None:
        raise HTTPException(409, "источники ИДАК: небезопасное имя прогона")
    try:
        with zipfile.ZipFile(pkg_path) as z:
            # Validate the complete package/answer binding before any writes.
            # Missing answered-book evidence is a refusal, never local fallback.
            payloads = answer_sources.package_sources(z, source_run)
            if payloads:
                from . import cosmos_runs as _cr
                sha = _current_package_sha(cur, pkg_path)
                if answer_sources.EVIDENCE in payloads:
                    # Bind answers to THIS already-open ZIP, not a declared
                    # channel SHA naming another package or a later path read.
                    import hashlib
                    observed = hashlib.sha256()
                    z.fp.seek(0)
                    for chunk in iter(lambda: z.fp.read(1024 * 1024), b""):
                        observed.update(chunk)
                    if observed.hexdigest() != sha:
                        raise answer_sources.EvidenceError(
                            "источники ответов: SHA текущего пакета не совпадает с ZIP")
                # рабочий каталог пары (прогон, пакет), не каталог по sha
                # (второй держатель sha не читает чужой прогон)
                dest = _cr.workdir_of(cur, sha) / "cdpm-run" / source_run
                for name, body in payloads.items():
                    target = dest / name
                    if target.exists() and target.read_bytes() != body:
                        raise answer_sources.EvidenceError(
                            f"источники ИДАК: изменён уже извлечённый источник {name}")
                for name, body in payloads.items():
                    target = dest / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        target.write_bytes(body)
                return dest
    except HTTPException:
        raise
    except Exception as exc:                              # noqa: BLE001
        raise HTTPException(409, f"источники ИДАК пакета не подтверждены: {exc}") from exc
    roots = [Path(_data_paths.data_root()) / "cdpm-runs"]
    extra = os.environ.get("CREW_CDPM_RUNS_DIR")
    if extra:
        roots.append(Path(extra))
    for root in roots:
        d = root / source_run
        if (d / "freeze" / "clauses").is_dir():
            return d
    return None


def _current_package_sha(cur: dict | None,
                         pkg_path: Path | None = None) -> str:
    """Отпечаток ТЕКУЩЕГО пакета постановки — один приём для каталога
    нажатий (`_cdpm_run_dir`, `_pairing_catalog_path`) и привязки
    снимка сверки: sha из канала (cur.sha / cur.source.sha), а у
    пакета «файлом руками» (CREW_TASK_PACKAGE, без sha) — хеш файла,
    как у package_runs.status(). Нет ни sha, ни пути — пустая строка;
    файл не хешируется — 409 словами (не молчаливый уход на чужой
    каталог)."""
    from . import package_runs as _pr
    cur = cur or {}
    sha = str(cur.get("sha") or (cur.get("source") or {}).get("sha")
              or "")
    if sha:
        return sha
    path = pkg_path
    if path is None and cur.get("path"):
        path = Path(str(cur["path"]))
    if path is None:
        return ""
    try:
        return _pr._sha256(Path(path))
    except OSError as exc:
        raise HTTPException(
            409, f"пакет постановки не хешируется ({Path(path).name}): "
                 f"{exc}") from exc


def _reader_llm_scope(pkg: dict) -> str:
    """Допуск моделей пакета для читателя сверки. Закон — один, и он
    живёт в `webapp/run_gate.py` вместе с объявлением среды COSMOS:
    роль «читатель ИДАК» называется в записи о прогоне тем же законом,
    каким читатель выбирается при запуске сверки."""
    from . import run_gate
    return run_gate.reader_llm_scope(pkg)


def _source_check_reader(pkg: dict, model: str | None = None,
                         scope: str | None = None) -> dict:
    """Читатель клауз для сверки с документами — один источник правды
    с сертификатором (`package_runs.certifier_identity`), политика
    S06 плана единой версии (06.09.2026): допуск «any» → облако,
    закреплённый профиль площадки (CREW_CERTIFIER_MODEL) — УМОЛЧАНИЕ
    (config_source="default"), канал — CREW_CERTIFIER_CHANNEL
    (cli — CLI подписки с --model, api — Messages API при ключе
    COSMOS); нет модели / канал вне словаря / cli без CLI / api без
    ключа — 409 словами, никакого запасного провайдера. Явная модель
    запроса — override в пределах допуска (config_source=
    "explicit_override", model_requested/model_site в возврате).
    «local-only» либо допуск не объявлен/кривой → только локальная
    модель установки (channel="local"); облачная модель в запросе при
    local-only — 400 словами, не молчаливая подмена. scope запроса,
    отличный от допуска пакета, — 400 (расширение запросом — никогда,
    сужение — позже). Возврат: {scope, requested, model, channel,
    config_source[, model_requested, model_site]}.

    Сам закон живёт в `webapp/run_gate.py` (`source_reader`): роль
    «читатель ИДАК» объявляется в записи о прогоне тем же кодом, каким
    читатель выбирается здесь — двух реализаций одного правила быть не
    должно. Здесь остаётся перевод отказа в ответ ручки."""
    from . import run_gate
    try:
        return run_gate.source_reader(pkg, model, scope=scope)
    except run_gate.RunGateError as e:
        raise HTTPException(e.status, e.text()) from None


def _pairing_catalog_path(cur: dict, run_dir: Path) -> Path | None:
    """Каталог цепочек для сверки пейринга с документами: сначала
    нажатие «Пейринг» ТЕКУЩЕГО пакета у COSMOS (расчёт переехал в
    COSMOS 04.09.2026: package-runs/<sha>/button-pairing/
    builder-catalog.json; пакет «файлом руками» находит свой каталог
    по хешу файла), иначе — каталоги прогона CDPM (прежний путь,
    старые прогоны — только при годном отпечатке и отсутствии
    нажатия у COSMOS). Кривой отпечаток пакета — 409 словами, а не
    молчаливый уход на чужой/старый каталог (аудит GPT п.5). Нет ни
    того ни другого — None."""
    from . import cosmos_runs as _cr, package_runs as _pr
    sha = _current_package_sha(cur, None)
    if sha:
        try:
            p = _cr.workdir_of(cur, sha) / "button-pairing" \
                / "builder-catalog.json"
        except _pr.PackageRunError as exc:
            raise HTTPException(
                409, f"отпечаток текущего пакета не годится для каталога "
                     f"нажатий: {exc}") from exc
        if p.is_file():
            return p
    catalogs = sorted(run_dir.glob("button-*/builder-catalog.json"),
                      key=lambda p: p.stat().st_mtime)
    return catalogs[-1] if catalogs else None


@app.get("/api/source-check/last")
def api_source_check_last(request: Request, task: str | None = None,
                          context_key: str | None = None):
    """Снимок сверки выбранного решения по задаче (pairing |
    rostering; без task — исторический корневой снимок) плюс строки проверок из
    checks.json — для панели блока «Проверка … по ИДАК». Нет снимка —
    status idle, не 404: панель показывает кнопку запуска.

    Рядом с `reader` и `reader_channel` едет `resources` — расход
    читателя ИДАК в токенах (`models[]`: роль, модель, провайдер,
    канал, `calls`, `input_tokens`, `output_tokens`, `counted`, `note`);
    проверки из кэша — `calls: 0` с нулями и `counted: true`, канал без
    расхода — `counted: false` с причиной словами. Снимок прежней
    версии расхода не называл — `models: []` с кодом
    `snapshot-without-resources`.

    Личность — сессия ЛИБО токен канала: раннер наборов и сторож
    площадок читают снимок без сессии человека (находка 10.09.2026)."""
    _user_or_channel(request)
    import _data_paths
    task = _source_check_task(task)
    root = Path(_data_paths.data_root()) / "source-check"
    p = (root / task / "last.json") if task else (root / "last.json")
    if task:
        from . import source_check_status as scs
        try:
            snap = scs.selected_snapshot(task, context_key)
        except scs.ContextChanged as e:
            raise HTTPException(409, str(e)) from e
    else:
        if not p.is_file():
            return {"status": "idle", "task": task}
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return {"status": "idle", "task": task,
                    "error": f"протокол не читается: {e}"}
    snap.setdefault("task", task)
    # Расход читателя ИДАК — рядом с reader / reader_channel: снимок
    # несёт его сам (`vmp/source_check_run.reader_resources`), ручка
    # ничего не пересчитывает. Снимок прежней версии расхода не называл
    # — поле стоит пустым с тем же кодом пробела, каким его зовёт запись
    # попытки (`package_runs.note_source_check`), а не пропадает молча
    # (шаг 1 системы прогонов: все вызовы моделей сочтены в записи).
    if not isinstance(snap.get("resources"), dict):
        snap["resources"] = {
            "models": [],
            "models_code": "snapshot-without-resources"}
    rows: list[dict] = []
    checks_path = snap.get("checks")
    if not checks_path and snap.get("protocol"):
        checks_path = str(Path(str(snap["protocol"])).with_name(
            "checks.json"))
    if snap.get("status") == "done" and checks_path \
            and Path(checks_path).is_file():
        try:
            doc = json.loads(Path(checks_path).read_text(
                encoding="utf-8"))
            for label, results in (doc.get("results") or {}).items():
                for r in results or []:
                    rows.append({
                        "solution": label,
                        "id": r.get("check_id"), "kind": r.get("kind"),
                        "anchor": r.get("anchor"),
                        "verdict": r.get("verdict"),
                        "detail": str(r.get("detail") or "")[:400],
                        "objects": r.get("objects_seen"),
                        "violations": len(r.get("violations") or [])})
            snap["problems_list"] = [
                str(x)[:200] for x in (doc.get("problems") or [])][:20]
        except (OSError, ValueError, TypeError, AttributeError) as e:
            snap["rows_error"] = f"строки проверок не читаются: {e}"
    snap["rows"] = rows
    return snap


@app.post("/api/source-check/run")
def api_source_check_run(body: dict, request: Request):
    """Запуск ФПР из интерфейса (кнопки блоков «Проверка пейринга по
    ИДАК» / «Проверка ростера по ИДАК»): фоновый прогон по текущему
    принятому пакету. task = pairing — каталог цепочек нажатия
    «Пейринг» (цепочка — безымянная линия времени, правила о людях
    откладываются на ростеринг поимённо); task = rostering — решение
    ростеринга с решателя по solverRunId; без task — оба решения
    одним протоколом (прежний путь). Статус живёт в last.json задачи
    (running → done/error), блок Конвейера показывает его сам.
    Тело: task, solverRunId, model (override в пределах допуска),
    scope (обязан равняться допуску пакета). Ответ 202 и снимки
    running/error несут reader_requested (кого звали: scope /
    requested / model / channel / config_source) и package_sha;
    готовый снимок — наблюдённый reader_channel и привязку к выдаче
    (S06 единой версии, 06.09.2026). Снимок ОТКАЗА несёт ещё и
    `resources` — расход читателя ИДАК: сверка падает и после ответа
    читателя (ответ не разобрался компилятором), и потраченные токены
    названы там же, где у удачи.

    Этап «сверка с ИДАК» ложится в запись о прогоне на ОБОИХ путях:
    удача и отказ различаются исходом этапа («успех» /
    «предупреждение» против «сбоя» с кодом `source-check-failed`), а не
    тем, есть ли этап вовсе. Молчание записи об отказавшей сверке
    читалось бы «сверки не было», а она была — и расход читателя,
    названный снимком, едет строкой того же этапа."""
    user = _user_or_channel(request)
    import threading

    import _data_paths
    from vmp import source_check_run as scr
    from webapp import task_channel, task_package
    # Кто запустил сверку — теми же словами, что у нажатия: актор едет
    # в запись этапа «сверка с ИДАК» летописи прогона.
    actor = _run_actor(request, user)
    task = _source_check_task(body.get("task"))
    snap_dir = Path(_data_paths.data_root()) / "source-check"
    if task:
        snap_dir = snap_dir / task
    last = snap_dir / "last.json"
    if last.is_file():
        try:
            doc = json.loads(last.read_text(encoding="utf-8"))
            if doc.get("status") == "running":
                # Живой урок 2026-08-31: прерванный фон (рестарт
                # сервера) оставляет running навсегда — гейт душил
                # бы запуск вечно. Свежий running блокирует, старше
                # 30 минут — считается брошенным и перезаписывается.
                started = str(doc.get("at") or "")
                from datetime import datetime as _dt
                fresh = False
                try:
                    age = (_dt.now()
                           - _dt.fromisoformat(started))
                    fresh = age.total_seconds() < 1800
                except ValueError:
                    pass
                # Единая версия 06.09.2026 (живой урок раската: рестарт
                # приложения посреди сверки): running принадлежит
                # ПРОЦЕССУ, который его писал. Снимок другого pid (или
                # без pid — прежний код) — брошенный фон: его поток умер
                # вместе с процессом, ждать 30 минут незачем.
                import os as _os
                if doc.get("server_pid") != _os.getpid():
                    fresh = False
                if fresh:
                    raise HTTPException(409, "сверка уже идёт")
        except (OSError, ValueError):
            pass
    cur = task_channel.current_package()
    if cur is None:
        raise HTTPException(409, "принятого пакета постановки нет — "
                                 "сверять нечего")
    pkg_path = Path(cur["path"])
    try:
        pkg = task_package.load(pkg_path, run_gate=False)
    except Exception as e:                             # noqa: BLE001
        raise HTTPException(409, f"пакет не читается: {e}")
    source_run = (pkg.get("manifest") or {}).get("source_run")
    run_dir = _cdpm_run_dir(str(source_run or ""), pkg_path, cur)
    if not source_run or run_dir is None:
        raise HTTPException(
            409, f"каталог прогона {source_run!r} с заморозкой клауз не "
                 f"найден ни в пакете, ни в cdpm-runs корня данных, ни по "
                 f"CREW_CDPM_RUNS_DIR — ФПР идёт от клауз заморозки")
    solver_run_id = str(body.get("solverRunId") or "").strip()
    if task == "pairing":
        solver_run_id = ""
    model = str(body.get("model") or "").strip() or None
    req_scope = str(body.get("scope") or "").strip() or None
    reader = _source_check_reader(pkg, model, scope=req_scope)
    package_sha = _current_package_sha(cur, pkg_path)
    catalog_path = _pairing_catalog_path(cur, run_dir) \
        if task in (None, "pairing") else None
    if task == "pairing" and catalog_path is None:
        raise HTTPException(
            409, "каталога цепочек текущего пакета нет — сначала "
                 "нажмите «Пейринг»")
    if task == "rostering" and not solver_run_id:
        raise HTTPException(
            409, "нет номера прогона ростеринга (solverRunId) — "
                 "сначала нажмите «Ростеринг»")
    if catalog_path is None and not solver_run_id:
        raise HTTPException(
            409, "нет ни каталога пейринга в прогоне, ни "
                 "solverRunId ростеринга — проверять нечего")
    # Привязка снимка к прогону COSMOS и рабочему каталогу пары (прогон,
    # пакет) — проект прогонов 07.09.2026, раздел 7.7: схема отсекает
    # чужой снимок по пакету и прогону, паспорт приписывает читателя ИДАК
    # попытке только при совпадении обоих.
    from . import cosmos_runs as _cr
    cosmos_run = ((cur.get("run") or {}).get("run")
                  if isinstance(cur.get("run"), dict) else None)
    try:
        press_wd = _cr.workdir_of(cur, package_sha)
        workdir_rel = str(press_wd.resolve().relative_to(
            Path(_data_paths.data_root()).resolve())).replace("\\", "/")
    except Exception:                                      # noqa: BLE001
        press_wd, workdir_rel = None, None

    # Отпечаток каталога цепочек: им сверка привязывается к попытке
    # пейринга. Считается ОДИН раз и живёт вне удачного пути — ветка
    # отказа зовёт тот же крючок записи, а отказать сверка может и до
    # того, как сложена привязка.
    link = {"catalog_sha256": None}

    def _note_record(snap: dict) -> None:
        """Этап «сверка с ИДАК» — в запись о прогоне (и в попытку, к
        которой сверка привязалась). Зовётся на ОБОИХ путях: удача и
        отказ отличаются исходом этапа, а не тем, есть ли этап. Беда
        записи сверки не рушит — слово в журнал службы."""
        try:
            from . import package_runs as _pr3
            if press_wd is not None and task:
                _pr3.note_source_check(press_wd, task, solver_run_id or None,
                                       link["catalog_sha256"], snap,
                                       actor=actor)
        except Exception as e:                             # noqa: BLE001
            print("COSMOS: сверка по ИДАК не записана в попытку: %s"
                  % str(e)[:200], flush=True)

    def _work():
        try:
            snap_dir.mkdir(parents=True, exist_ok=True)
            last.write_text(json.dumps(
                {**(check_context or {}), "status": "running",
                 "at": task_channel._now(),
                 "run": str(source_run), "task": task,
                 "reader_requested": reader,
                 "package_sha": package_sha,
                 "cosmos_run": cosmos_run, "workdir": workdir_rel,
                 "server_pid": os.getpid()},
                ensure_ascii=False), encoding="utf-8")
            solutions = []
            roster_source = None
            if catalog_path is not None:
                catalog = json.loads(catalog_path.read_text(
                    encoding="utf-8"))
                fmap = scr.clean_flights_from_package(pkg_path)
                # SOURCE-REST-R1: граница «стыковка / отдых» пейринга
                # — --sit-max из ведомости входа построителя ЭТОГО
                # нажатия (builder-input.json рядом с каталогом), не
                # константа.
                boundary = scr.rest_boundary(
                    "pairing",
                    builder_input=scr.builder_input_beside(catalog_path),
                    note=f"рядом с {catalog_path.name} нет "
                         f"builder-input.json")
                solutions.append((
                    "пейринг (каталог цепочек)",
                    scr.catalog_to_solution(catalog, fmap),
                    "pairing", None, boundary))
            if solver_run_id:
                import httpx
                # Адрес решателя — только явная настройка площадки
                # (один источник с package_runs.solver_url; умолчания
                # порта контура нет — решение владельца 04.09).
                from . import package_runs as _pr
                try:
                    base = _pr.solver_url().rstrip("/")
                except _pr.PackageRunError as e:
                    raise HTTPException(503, str(e))
                from .source_check_result import roster_result
                solved, applied, why, roster_source = roster_result(
                    get=httpx.get, base=base, wd=press_wd,
                    package_sha=package_sha, cosmos_run=cosmos_run,
                    solver_run_id=solver_run_id)
                # SOURCE-REST-R1: показания ИМЕННО этого прогона
                # (applied-rules по runId) — граница «стыковка / отдых»
                # T для исполнителя rest_min по правилам пакета
                # (vmp.sit_boundary): day_break_threshold_min > 0 — ручка
                # max_sit_duration, иначе пол минимального отдыха тех же
                # показаний (min_rest_*); ни того ни другого — граница не
                # определена. Отказ решателя отдать показания — не
                # остановка сверки: отдых по разрывам станет
                # not_checkable с причиной.
                boundary = scr.rest_boundary(
                    "roster", applied=applied, run_id=solver_run_id,
                    note=why)
                solutions.append((
                    f"ростеринг ({solver_run_id[:8]})", solved,
                    "roster", None, boundary))
            # Привязка результата к пакету и выдаче (§3.9 п.2 плана
            # единой версии): зелёный снимок относится к этому пакету
            # (sha) и к этой выдаче (каталог цепочек по хешу, ростер
            # по номеру прогона), а не только к имени прогона CDPM.
            binding = {
                "package_sha": package_sha,
                "revision": (pkg.get("manifest") or {}).get("revision"),
                "solver_run_id": solver_run_id or None,
                "catalog_path": (str(catalog_path)
                                 if catalog_path is not None else None),
                "catalog_sha256": None,
                # После утраты памяти native — только доказанный снимок
                # точной попытки; происхождение остаётся в снимке.
                "roster_path": (roster_source or {}).get("roster_path"),
                "roster_source": roster_source,
                # прогон COSMOS и рабочий каталог пары (07.09.2026)
                "cosmos_run": cosmos_run, "workdir": workdir_rel}
            binding.update(check_context or {})
            if catalog_path is not None:
                try:
                    from . import package_runs as _pr2
                    link["catalog_sha256"] = _pr2._sha256(catalog_path)
                except OSError:
                    pass
            binding["catalog_sha256"] = link["catalog_sha256"]
            scr.run_full(run_dir, solutions, scope=reader["scope"],
                         requested=reader["requested"],
                         model=reader["model"], snap_sub=task,
                         channel=reader.get("channel"), binding=binding)
            # Сверка — в запись попытки (раздел 7.7): package_runs.
            # note_source_check находит попытку по solver_run_id ростеринга
            # либо по SHA каталога пейринга; сбой записи не рушит сверку
            # (снимок с привязкой уже на месте) — слово в журнале службы.
            try:
                done = json.loads(last.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as e:             # noqa: BLE001
                print("COSMOS: снимок удачной сверки не прочитан: %s"
                      % str(e)[:200], flush=True)
            else:
                _note_record(done)
        except BaseException as e:                     # noqa: BLE001
            # Отказавшая сверка расход ТРАТИТ: читатель мог ответить, а
            # компилятор — не разобрать ответ. Снимок отказа называет
            # этот расход тем же путём, что и снимок удачи (разобранный
            # usage последнего вызова потока → reader_resources), иначе
            # запись о прогоне говорит «снимок расхода не назвал» о
            # вызове, который был. Беда счёта расхода снимок отказа не
            # отменяет: причина отказа важнее — тогда стоит тот же код
            # пробела, каким его зовут ручка и запись попытки.
            try:
                spent = scr.failed_reader_resources(
                    model=reader.get("model"),
                    channel=reader.get("channel"))
            except BaseException:                      # noqa: BLE001
                spent = {"models": [],
                         "models_code": "snapshot-without-resources"}
            snap = {**(check_context or {}), "status": "error", "error": str(e)[:400],
                    "at": task_channel._now(), "task": task,
                    "reader_requested": reader,
                    "resources": spent,
                    "package_sha": package_sha,
                    "cosmos_run": cosmos_run, "workdir": workdir_rel}
            last.write_text(json.dumps(snap, ensure_ascii=False),
                            encoding="utf-8")
            # Отказавшая сверка — ЭТАП записи о прогоне, а не молчание.
            # Крючок тот же, что у удачи: исход этапа и код причины
            # выводит словарь этапа по снимку (`status: error` →
            # «сбой» / `source-check-failed`), а строка расхода
            # читателя едет из `resources` снимка — принцип «все вызовы
            # моделей сочтены» отказом сверки не отменяется. Крючок
            # зовётся ПОСЛЕ записи снимка: снимок — свидетельство
            # отказа, и беда записи его не отменяет.
            _note_record(snap)

    from . import source_check_status as scs
    from . import package_runs as _sc_pr
    check_context = scs.current_context(task, cur=cur) if task else None
    if task and (not check_context
                 or check_context.get("package_sha") != package_sha
                 or (task == "rostering" and check_context.get("solver_run_id") != solver_run_id)
                 or (task == "pairing" and (catalog_path is None
                     or _sc_pr._sha256(catalog_path) != check_context.get("catalog_sha256")))):
        raise HTTPException(409, "точное выбранное решение для сверки не подтверждено")
    key = scs.context_key(check_context)
    if body.get("context_key") is not None and body["context_key"] != key:
        raise HTTPException(409, "выбранное решение изменилось; откройте проверку заново")
    threading.Thread(target=_work, daemon=True).start()
    return JSONResponse(status_code=202, content={
        "started": True, "run": source_run, "task": task,
        "pairing": catalog_path is not None,
        "rostering": bool(solver_run_id),
        "reader_requested": reader, "package_sha": package_sha,
        "context": check_context, "context_key": key})


@app.get("/api/artifacts/{name}")
def api_artifact(name: str):
    # Public reference library, NOT the per-tenant workspace: this is the
    # published GERAD/BTS benchmark corpus the onboarding demos hand to every
    # authenticated user (demo.py renders these files as download links), so
    # it is intentionally not gated per-owner like /api/results. The gate is
    # the literal allowlist in results.ARTIFACT_WHITELIST, which by
    # construction can never name a tenant upload — see the classification
    # note there (audit 2026-07-15). Auth is still enforced (require_identity).
    path = results.artifact_path(name)   # literal whitelist match only
    if path is None:
        raise HTTPException(404, f"not a downloadable artifact: {name}")
    return FileResponse(path, filename=name,
                        media_type="application/octet-stream")


# ---------------------------------------------------------------- static

# Cache busting (2026-07-16): browsers cached app.js across deploys, so a
# freshly deployed feature was invisible until a hard refresh. index.html is
# served through a tiny handler that stamps every LOCAL script/style URL
# with ?v=<version>; the version is the newest mtime of the static files,
# computed once per process (the server restarts on every deploy). The
# StaticFiles mount ignores query strings, so the same files serve as-is.

import re as _re


def _static_version() -> str:
    newest = max((p.stat().st_mtime_ns for p in STATIC_DIR.glob("*")
                  if p.is_file()), default=0)
    return format(newest // 1_000_000_000, "x")   # whole seconds, hex


_INDEX_CACHE: dict[str, str] = {}


def _versioned_index() -> str:
    html = _INDEX_CACHE.get("html")
    if html is None:
        raw = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        v = _static_version()
        html = _re.sub(r'((?:src|href)=")([a-z0-9_.-]+\.(?:js|css))"',
                       r'\g<1>\g<2>?v=' + v + '"', raw)
        label = os.environ.get("CREW_CONTOUR_LABEL", "").strip()
        if label:
            # Customer-contour suffix (e.g. "DEMO"): stamp the initial <title>
            # server-side and hand the label to the page; i18n.js re-applies
            # the suffix on every language switch, so the two sides must
            # agree on the window variable name.
            html = html.replace("</title>", "/" + label + "</title>", 1)
            html = html.replace(
                "</head>",
                "<script>window.COSMOS_CONTOUR_LABEL = "
                + json.dumps(label) + ";</script></head>", 1)
        _INDEX_CACHE["html"] = html
    return html


if STATIC_DIR.is_dir():
    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def _index() -> HTMLResponse:
        # Cache-Control comes from the no_cache_static middleware; the ?v=
        # stamp additionally defeats caches that predate that middleware.
        return HTMLResponse(_versioned_index())

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True),
              name="static")
