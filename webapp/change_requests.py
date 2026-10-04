"""Change-request (CR) pipeline of the Автоулучшение section
(2026-07-15, per the user's confirmed design).

Three proposal agents feed ONE queue of change requests; the admin
decides; an implementer agent writes code. The full lifecycle:

  suggestion:  Подано -> Обработано -> Сформирован CR -> CR принят
               -> Реализовано            (author sees every transition)
  CR:          draft -> accepted -> implementing -> implemented
               | rejected (with a reason) | needs_human

Agents (LLM backend follows the install's contour: local model on
prod/stage — the suggestion texts are user data of the internal contour —
cloud CLI with web search on the external Test contour):

  - FEEDBACK: reads ALL users' suggestions, clusters similar ones, emits
    CRs with priority = number of clustered suggestions and an effort
    estimate 1..10 (both by the model, validated here);
  - REGULATORY: per-country digests (each instance carries a COUNTRY
    attribute; INTL = international rules only) of crew-scheduling
    regulation changes + CRs for anything affecting the app. Uses web
    search only where the cloud CLI is allowed; otherwise honestly labels
    the digest as model-knowledge-only;
  - UIUX: heuristic audit of our own UI plus public materials about
    industrial crew systems -> improvement CRs (source labelled).

IMPLEMENTER ("Реализовать на Тест"): CLOUD-only, so it refuses to run at
all when this checkout is the internal contour (prod/stage) — the prompt
carries the CR summary and raw suggestion texts, which are internal-contour
user data (_repo_is_internal_contour, fail-closed). Where allowed, it runs
the Claude CLI in the ISOLATED cosmos-test checkout
(evolution_runner's contour guard), on a dedicated
git branch cr-<id>, then runs pytest there and records the honest result
(diff stat + test summary). HARD RULES injected into the prompt: no new
dependencies, no secrets/credentials, no data migrations, no deploy
tooling changes — a CR that needs any of that is marked needs_human. At
most CR_IMPL_DAILY_LIMIT implementations per UTC day. Stage/Prod
promotion stays manual.

КАТАЛОГ ШАБЛОНОВ (шаг 6, 08.09.2026): у очереди появился ЧЕТВЁРТЫЙ вид
строки — «catalog-executor» с ключом записи каталога. Её рождает не
агент-предложение, а сверка заявок каталога с очередью
(webapp.catalog_pipeline) по решению владельца, принятому в службе
каталога; ключ строки детерминирован (cr_key_of(request_id)), поэтому
тот же CR открывается на любой машине без переноса очереди. У неё свой
порядок исполнения (_implement_catalog): белый список путей, коммит
работы агента ДО гейтов содержания, цепочка стенда и привязок, гейты на
временном каталоге «живой ⊕ запись кандидата», коммит свидетельства.
Машинная форма правила авиакомпании в такой строке невозможна по
построению: в ней только обезличенная спецификация записи каталога.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone

from . import help_center, llm_backend, registry, settings

CR_PATH = registry.REPO_ROOT / "webapp" / "config" / "change_requests.json"

_lock = threading.Lock()

CR_STATUSES = ("draft", "accepted", "implementing", "implemented",
               "rejected", "needs_human")
# "audit" CRs are created by webapp/audit_inbox.py (third-party audit
# reports), not by an AGENT_RUNNERS entry — the audit view has its own
# check/process buttons instead of a generic "run agent".
#
# The DETERMINISTIC loops (datahealth / override / runtime, wave 1;
# connbuf, wave 2) DO have AGENT_RUNNERS entries, but call no language
# model at all: they read their own observation stores and turn worsening
# numbers into draft CRs.
AGENTS = ("feedback", "regulatory", "uiux", "audit",
          "datahealth", "override", "runtime", "connbuf")

# Каталог шаблонов (шаг 6, 08.09.2026). Строка CR каталога рождается НЕ
# агентом-предложением, а СВЕРКОЙ заявок каталога с очередью
# (webapp.catalog_pipeline): решение по заявке принял владелец в службе
# каталога, и второй отмашки в очереди нет. Поэтому «catalog-executor»
# НЕ входит в AGENTS (это перечень агентов-предложений с AGENT_RUNNERS —
# кнопки «Запустить агента» у вида «Каталог» нет), а живёт отдельным
# перечнем для вида экрана и подписей. Вместе с ним в перечне — два
# исторических имени, под которыми строки очереди рождались до шага 6.
CATALOG_KIND = "catalog-executor"
CATALOG_AGENTS = ("catalog-executor", "vmp-gap", "template-base")
CR_IMPL_DAILY_LIMIT = 3
AGENT_TIMEOUT_S = 300.0
IMPL_CLI_TIMEOUT_S = 1800.0
#: Агенту CR с видами исполнителей решателя (0.14): его самопроверка
#: включает сборку решателя и прогон его тестов (минуты), и получаса на
#: два-три круга ему не хватает.
IMPL_CLI_TIMEOUT_KOTLIN_S = 7200.0   # измерено 08.09: час агенту на новую метрику решателя мал
IMPL_TEST_TIMEOUT_S = 2400.0   # полный набор клона ~19 мин на машине владельца (09.09)
MAX_COUNTRIES_PER_RUN = 3

# Бюджеты сборки и эфемерного решателя (§5 пакета «Kotlin-исполнители
# агентом»; числа измерены на legion 08.09.2026 и на машине владельца).
SOLVER_PROBE_TIMEOUT_S = 60.0     # обёртка без сети отвечает секундами
SOLVER_BUILD_TIMEOUT_S = 900.0    # legion ~30 с из кэша, локально 103 с
SOLVER_TEST_TIMEOUT_S = 900.0     # legion ~60 с, 367 тестов
SOLVER_START_TIMEOUT_S = 120.0    # измерено «started in 1.066s»
SOLVER_STOP_TIMEOUT_S = 30.0      # terminate 20 с → taskkill → проверка
#: Пределы кучи JVM: компилятор Kotlin идёт В ПРОЦЕССЕ Maven (демон
#: выключен), форк surefire без предела брал четверть памяти машины,
#: миры стенда — единицы рейсов и людей.
SOLVER_XMX_MAVEN = "2g"
SOLVER_XMX_FORK = "1g"
SOLVER_XMX_RUN = "1g"
#: Код возврата команды сборки «собирать нечем» (`vmp.solver_build`
#: RC_NO_TOOLCHAIN). Отделён от «собрали, и оно красное» (код 1):
#: `build.available` — про тулчейн, а не про исход суда.
SOLVER_RC_NO_TOOLCHAIN = 3

NEEDS_HUMAN_MARKER = "NEEDS_HUMAN"


class ChangeRequestError(ValueError):
    """Invalid CR operation; message is safe to show the user."""


# ----------------------------------------------------------------- storage

def _load() -> dict:
    try:
        raw = json.loads(CR_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"change-request store unreadable: {exc}") from exc


def _save_locked(data: dict) -> None:
    CR_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CR_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(CR_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_crs() -> list[dict]:
    items = _load().get("items") or []
    # newest first, then priority
    items.sort(key=lambda r: (r.get("created_at") or ""), reverse=True)
    return items


def get_cr(cr_id: str) -> dict | None:
    for r in _load().get("items") or []:
        if r.get("id") == cr_id:
            return r
    return None


def _update_cr(cr_id: str, patch: dict) -> dict:
    with _lock:
        data = _load()
        items = data.setdefault("items", [])
        for r in items:
            if r.get("id") == cr_id:
                r.update(patch)
                r["updated_at"] = _now()
                _save_locked(data)
                return r
    raise ChangeRequestError(f"no such CR: {cr_id}")


# Статусы предложения, которое ЕЩЁ ждёт решения администратора. Дубль
# ищется только среди них: отклонённое или уже реализованное предложение
# не должно навсегда затыкать контуру рот — тот же вопрос он вправе
# поднять заново.
OPEN_STATUSES = ("draft", "accepted")

# Ключ эквивалентности предложения — ВСЁ его содержимое, кроме случайного
# идентификатора, статуса и времён. Время исключено намеренно: именно оно
# делало два нажатия «Запустить агента» подряд двумя РАЗНЫМИ строками при
# побайтово одинаковом тексте. Детерминированный контур на неизменившемся
# наблюдении собирает буквально ту же строку, поэтому совпадение ключа
# означает «то же самое предложение», а не новое наблюдение; как только
# наблюдение меняется (вырос счётчик дефектов, подрос кластер), меняется
# и заголовок с описанием — ключ расходится, и предложение проходит.
_DEDUP_FIELDS = ("agent", "title", "summary", "priority", "effort",
                 "source", "country")


def _dedup_key(row: dict) -> tuple:
    # json.dumps, а не str(): различает 3 и "3" и не зависит от порядка
    # ключей во вложенных значениях.
    return tuple(json.dumps(row.get(f), ensure_ascii=False, sort_keys=True)
                 for f in _DEDUP_FIELDS)


class DuplicateChangeRequest(ValueError):
    """Заявка с этим идентификатором уже есть, но с ДРУГИМ содержанием."""


def _add_crs(rows: list[dict], *, dedup: bool = False,
             unique_by_id: bool = False) -> list[dict]:
    """Зарегистрировать черновики; вернуть строки, которые ДЕЙСТВИТЕЛЬНО
    добавлены.

    dedup=True (детерминированные контуры): строка, эквивалентная уже
    открытому предложению того же контура — или другой строке этой же
    пачки, — не добавляется. Сверка идёт ПОД тем же локом, что и запись:
    иначе два одновременных запуска агента разошлись бы между чтением
    очереди и её перезаписью и всё равно дали бы дубль.

    unique_by_id=True (приёмник аудитов): идентификатор — первичный ключ.
    Повтор с ТЕМ ЖЕ содержанием пропускается молча (это восстановление
    после сбоя), повтор с ДРУГИМ содержанием — громкий отказ (это
    коллизия, и терять одну из двух находок нельзя).

    Проверка стоит здесь — в физической точке записи, — а не в вызывающем
    (внешний аудит R9, P1-02). Прежде приёмник аудитов читал очередь
    ОТДЕЛЬНЫМ вызовом вне лока и сам решал, что добавлять: между чтением и
    записью оставалось окно, а любой другой вызывающий мог записать дубль
    вовсе без проверки.
    """
    with _lock:
        data = _load()
        items = data.setdefault("items", [])
        if unique_by_id:
            by_id = {r.get("id"): r for r in items if isinstance(r, dict)}
            fresh = []
            for row in rows:
                rid = row.get("id")
                if not rid:
                    raise ValueError("заявка без идентификатора не может быть "
                                     "записана в очередь")
                prev = by_id.get(rid)
                if prev is not None:
                    if (prev.get("title") != row.get("title")
                            or prev.get("summary") != row.get("summary")):
                        raise DuplicateChangeRequest(
                            f"идентификатор {rid} уже занят заявкой с другим "
                            f"содержанием — молчаливая потеря находки "
                            f"недопустима")
                    continue
                by_id[rid] = row
                fresh.append(row)
            items.extend(fresh)
            _save_locked(data)
            return fresh
        if dedup:
            seen = {_dedup_key(r) for r in items
                    if isinstance(r, dict) and r.get("status") in OPEN_STATUSES}
            fresh = []
            for row in rows:
                key = _dedup_key(row)
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(row)
        else:
            fresh = list(rows)
        items.extend(fresh)
        _save_locked(data)
    return fresh


# Приёмник заявок внешнего производителя (submit_external_crs, этап 2
# переноса ВМП) СНЯТ шагом 6 каталога (решения 0.6 и 0.8): он клал в
# очередь машинную форму правила авиакомпании (ключ `rule`), а строку
# очереди читает облачная модель-исполнитель. Пробел относительно записи
# каталога теперь становится ЗАЯВКОЙ КАТАЛОГА (vmp.catalog_requests), а
# строку CR открывает сверка заявок (webapp.catalog_pipeline) — в ней
# только обезличенная спецификация записи и виды исполнителей. Правка и
# снятие записи с экрана «База шаблонов» — ссылки на карточку записи в
# службе каталога, а не заявка очереди.


def _impl_daily_limit() -> int:
    """Суточная квота реализаций: CR_IMPL_DAILY_LIMIT (3) либо переменная
    CREW_CR_IMPL_DAILY_LIMIT рабочего места — на месте владельца конвейер
    пополнения каталога за день может пройти больше трёх строк (09.09.2026:
    три сквозные проверки утром исчерпали квоту до заявки владельца).
    Нечисло или пусто — умолчание; отрицательное — ноль (реализация
    выключена)."""
    raw = os.environ.get("CREW_CR_IMPL_DAILY_LIMIT")
    if raw is None or not raw.strip():
        return CR_IMPL_DAILY_LIMIT
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return CR_IMPL_DAILY_LIMIT


def _impl_quota_left() -> int:
    day = datetime.now(timezone.utc).date().isoformat()
    data = _load()
    used = (data.get("impl_counter") or {})
    n = used.get(day, 0) if isinstance(used, dict) else 0
    return max(0, _impl_daily_limit() - n)


def _impl_quota_take() -> None:
    day = datetime.now(timezone.utc).date().isoformat()
    with _lock:
        data = _load()
        counter = data.get("impl_counter")
        if not isinstance(counter, dict):
            counter = {}
        counter = {day: counter.get(day, 0)}      # drop old days
        if counter[day] >= _impl_daily_limit():
            raise ChangeRequestError(
                f"daily implementation limit reached "
                f"({_impl_daily_limit()}/day)")
        counter[day] += 1
        data["impl_counter"] = counter
        _save_locked(data)


def _impl_quota_refund() -> None:
    """Give back a taken slot when the run never completed (CLI error or
    timeout): flaky infrastructure must not burn the daily budget. Runs
    that DID complete — implemented, needs_human, diff-guard rejection —
    keep their slot (anti-abuse)."""
    day = datetime.now(timezone.utc).date().isoformat()
    with _lock:
        data = _load()
        counter = data.get("impl_counter")
        if isinstance(counter, dict) and counter.get(day, 0) > 0:
            counter[day] -= 1
            data["impl_counter"] = counter
            _save_locked(data)


# ---------------------------------------------------------- LLM plumbing

def _repo_is_internal_contour() -> bool:
    """True when this checkout IS the prod or stage contour — or when that
    cannot be established (unresolvable contour dirs count as internal).
    Fail-closed: on a misconfigured install user data must not egress.
    Note the default of COSMOS_PROD_DIR is REPO_ROOT itself, so an
    unconfigured checkout is internal by definition — the external Test
    contour is the one that explicitly points COSMOS_PROD_DIR/STAGE_DIR
    elsewhere."""
    from . import evolution_runner as ev
    here = registry.REPO_ROOT.resolve()
    try:
        internal = {ev.prod_dir().resolve(), ev.stage_dir().resolve()}
    except Exception:                                # noqa: BLE001
        return True
    return here in internal


def _agent_backend() -> str:
    """The install's default backend — FORCED to the local model when this
    checkout IS the prod or stage contour. The feedback agent sends all
    users' suggestion texts to the model; on the internal contour that text
    must never leave the machine, whatever CREW_LLM_DEFAULT_BACKEND says
    (fail-closed, mirrors evolution_runner._assert_external_contour)."""
    backend = llm_backend.default_backend()
    if backend != "local" and _repo_is_internal_contour():
        return "local"
    return backend


def _extract_json_array(text: str):
    """Tolerant JSON array extraction from an LLM reply (fenced or not)."""
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else None
    except json.JSONDecodeError:
        return None


def _clip(s, n=400) -> str:
    s = str(s or "").strip()
    return s[:n]


# ------------------------------------------------------------ feedback agent

_FEEDBACK_SYSTEM = """\
You triage user feedback for COSMOS, a crew pairing & rostering
application. Cluster the given suggestions by shared topic and emit change
requests. Reply with ONLY a JSON array, no prose:
[{"title": "...", "summary": "...", "suggestion_ids": ["s..."],
  "effort": 1-10}]
Rules: every suggestion belongs to AT MOST one cluster; a cluster may hold
one suggestion; title <= 90 chars in Russian; summary = what to change and
why, in Russian, <= 600 chars; effort = implementation difficulty for one
engineer (1 trivial .. 10 huge). Do not invent suggestions."""


def run_feedback_agent(triggered_by: str = "manual") -> dict:
    """Cluster ALL users' suggestions into CRs. Returns a run summary."""
    all_items = help_center.list_suggestions({"email": ""}, is_admin=True)
    fresh = [s for s in all_items
             if s.get("status", "new") in ("new", "triaged")]
    if not fresh:
        return {"agent": "feedback", "created": 0, "considered": 0,
                "note": "no fresh suggestions"}
    payload = [{"id": s["id"], "category": s.get("category"),
                "text": _clip(s.get("text"), 600)} for s in fresh]
    res = llm_backend.complete(
        "SUGGESTIONS:\n" + json.dumps(payload, ensure_ascii=False, indent=1),
        backend=_agent_backend(), timeout_s=AGENT_TIMEOUT_S,
        system=_FEEDBACK_SYSTEM, usage="agents")
    if not res.get("ok"):
        raise ChangeRequestError(f"feedback agent LLM failed: {res.get('error')}")
    arr = _extract_json_array(res.get("text") or "")
    if arr is None:
        raise ChangeRequestError("feedback agent returned no JSON array")

    known = {s["id"] for s in fresh}
    used: set[str] = set()
    rows = []
    for c in arr:
        if not isinstance(c, dict):
            continue
        ids = []
        for i in (c.get("suggestion_ids") or []):
            s = str(i)
            if s in known and s not in used:   # dedup within AND across
                ids.append(s)                  # clusters
                used.add(s)
        if not ids:
            continue
        try:
            effort = max(1, min(10, int(c.get("effort", 5))))
        except (TypeError, ValueError):
            effort = 5
        rows.append({
            "id": "cr" + secrets.token_hex(4),
            "agent": "feedback",
            "title": _clip(c.get("title"), 90) or "(без названия)",
            "summary": _clip(c.get("summary"), 600),
            "suggestion_ids": ids,
            "priority": len(ids),
            "effort": effort,
            "status": "draft",
            "source": None,
            "country": None,
            "created_at": _now(), "updated_at": _now(),
        })
    _add_crs(rows)
    # suggestion statuses: clustered -> cr_created (+cr link); the rest of
    # the considered batch -> triaged (agent saw them, no CR this round)
    for r in rows:
        help_center.update_suggestions_status(r["suggestion_ids"],
                                              "cr_created", r["id"])
    leftover = [s["id"] for s in fresh if s["id"] not in used]
    help_center.update_suggestions_status(leftover, "triaged")
    return {"agent": "feedback", "created": len(rows),
            "considered": len(fresh), "backend": res.get("backend"),
            "model": res.get("model"), "triggered_by": triggered_by}


# ---------------------------------------------------------- regulatory agent

_REGULATORY_SYSTEM = """\
You monitor aviation crew-scheduling regulation for COSMOS (crew pairing
and rostering optimization). For the given jurisdiction, report recent or
upcoming regulatory changes affecting flight/duty time limits, rest
requirements, vacation entitlements, crew qualification/recency rules —
anything a crew planning system must encode. Reply with ONLY a JSON
array (may be empty), no prose:
[{"title": "...", "summary": "...", "effort": 1-10, "source": "..."}]
title/summary in Russian; summary explains WHAT changes in the rules and
WHAT the application should change (rule parameters, validators, new
attributes); source = the regulation/document name. Emit at most 3 items,
only genuinely relevant ones."""


def instance_countries() -> list[str]:
    """Distinct country attributes across instances (+ INTL always)."""
    seen = {"INTL"}
    for inst in registry.list_instances():
        c = (inst.get("country") or "").strip().upper()
        if c:
            seen.add(c)
    return sorted(seen)


def run_regulatory_agent(triggered_by: str = "manual") -> dict:
    countries = instance_countries()[:MAX_COUNTRIES_PER_RUN]
    backend = _agent_backend()
    created = 0
    digests = []
    for ctry in countries:
        scope_note = ("international bodies (ICAO, EASA ORO.FTL, FAA FAR "
                      "117)" if ctry == "INTL"
                      else f"country {ctry} plus international bodies")
        prompt = (f"JURISDICTION: {scope_note}.\n"
                  "NOTE: you may not have live web access; if so, answer "
                  "from your knowledge and say so in each summary "
                  "honestly.")
        res = llm_backend.complete(prompt, backend=backend,
                                   timeout_s=AGENT_TIMEOUT_S,
                                   system=_REGULATORY_SYSTEM, usage="agents")
        if not res.get("ok"):
            digests.append({"country": ctry, "error": res.get("error")})
            continue
        arr = _extract_json_array(res.get("text") or "") or []
        rows = []
        for c in arr[:3]:
            if not isinstance(c, dict) or not c.get("title"):
                continue
            try:
                effort = max(1, min(10, int(c.get("effort", 5))))
            except (TypeError, ValueError):
                effort = 5
            rows.append({
                "id": "cr" + secrets.token_hex(4),
                "agent": "regulatory",
                "title": _clip(c.get("title"), 90),
                "summary": _clip(c.get("summary"), 600),
                "suggestion_ids": [],
                "priority": 1,
                "effort": effort,
                "status": "draft",
                "source": _clip(c.get("source"), 200),
                "country": ctry,
                "created_at": _now(), "updated_at": _now(),
            })
        _add_crs(rows)
        created += len(rows)
        digests.append({"country": ctry, "items": len(rows)})
    return {"agent": "regulatory", "created": created,
            "countries": countries, "digests": digests,
            "backend": backend, "triggered_by": triggered_by}


# --------------------------------------------------------------- uiux agent

_UIUX_SYSTEM = """\
You are a UX reviewer for COSMOS, a crew pairing & rostering web app. Given
the application overview, propose concrete UI/UX improvements grounded in
(a) usability heuristics applied to the described screens and (b) publicly
known conventions of industrial crew systems (Jeppesen, NetLine/Crew,
Sabre). Reply with ONLY a JSON array of at most 5 items, no prose:
[{"title": "...", "summary": "...", "effort": 1-10, "source": "..."}]
title/summary in Russian; source = "эвристика: <named heuristic>" or
"отраслевая практика: <system>". Only actionable, specific proposals."""


def run_uiux_agent(triggered_by: str = "manual") -> dict:
    res = llm_backend.complete(
        "APPLICATION OVERVIEW:\n" + help_center.APP_OVERVIEW,
        backend=_agent_backend(), timeout_s=AGENT_TIMEOUT_S,
        system=_UIUX_SYSTEM, usage="agents")
    if not res.get("ok"):
        raise ChangeRequestError(f"uiux agent LLM failed: {res.get('error')}")
    arr = _extract_json_array(res.get("text") or "") or []
    rows = []
    for c in arr[:5]:
        if not isinstance(c, dict) or not c.get("title"):
            continue
        try:
            effort = max(1, min(10, int(c.get("effort", 5))))
        except (TypeError, ValueError):
            effort = 5
        rows.append({
            "id": "cr" + secrets.token_hex(4),
            "agent": "uiux",
            "title": _clip(c.get("title"), 90),
            "summary": _clip(c.get("summary"), 600),
            "suggestion_ids": [],
            "priority": 1,
            "effort": effort,
            "status": "draft",
            "source": _clip(c.get("source"), 200),
            "country": None,
            "created_at": _now(), "updated_at": _now(),
        })
    _add_crs(rows)
    return {"agent": "uiux", "created": len(rows),
            "backend": res.get("backend"), "triggered_by": triggered_by}


# ------------------------------------------- deterministic loops (2026-07-20)
#
# Three thin runners over modules that do all the work themselves. Each
# module's build_crs() RETURNS draft rows and registers nothing — the
# registration is here, through the same _add_crs the LLM agents use, so
# the queue has exactly one writer. No language model is involved: these
# loops read internal-contour observations (payloads with names and staff
# numbers, override episodes, run telemetry) and must never egress.


def _visible_instance_payloads() -> list[tuple[str, dict]]:
    """(inst_id, payload) for every instance of the registry.

    Same source as the regulatory agent (registry.list_instances()): the
    agent runner is admin-only, so «visible» here is the whole registry.
    An instance whose payload is missing or unparsable is SKIPPED, not
    fatal — one broken upload must not block the snapshot of the rest.
    """
    out: list[tuple[str, dict]] = []
    for inst in registry.list_instances():
        try:
            payload = registry.load_payload(
                registry.instance_payload_path(inst))
        except Exception:                                # noqa: BLE001
            continue
        if isinstance(payload, dict):
            out.append((str(inst["id"]), payload))
    return out


def run_datahealth_agent(triggered_by: str = "manual") -> dict:
    """Snapshot data defects of every instance; register worsenings."""
    from . import data_health
    pairs = _visible_instance_payloads()
    try:
        rows = data_health.build_crs(pairs)
    except Exception as exc:                             # noqa: BLE001
        raise ChangeRequestError(f"data-health loop failed: {exc}") from exc
    added = _add_crs(rows, dedup=True)
    return {"agent": "datahealth", "created": len(added),
            "duplicates": len(rows) - len(added),
            "considered": len(pairs), "triggered_by": triggered_by}


def run_override_agent(triggered_by: str = "manual") -> dict:
    """Turn repeated manual board overrides into draft CRs."""
    from . import planner_feedback
    try:
        clusters = planner_feedback.clusters()
        rows = planner_feedback.build_crs()
    except Exception as exc:                             # noqa: BLE001
        raise ChangeRequestError(
            f"planner-feedback loop failed: {exc}") from exc
    added = _add_crs(rows, dedup=True)
    return {"agent": "override", "created": len(added),
            "duplicates": len(rows) - len(added),
            "considered": len(clusters), "triggered_by": triggered_by}


def run_runtime_agent(triggered_by: str = "manual") -> dict:
    """Turn the run profile of the stand into draft CRs."""
    from . import run_profile
    try:
        prof = run_profile.profile()
        rows = run_profile.build_crs()
    except Exception as exc:                             # noqa: BLE001
        raise ChangeRequestError(f"run-profile loop failed: {exc}") from exc
    added = _add_crs(rows, dedup=True)
    return {"agent": "runtime", "created": len(added),
            "duplicates": len(rows) - len(added),
            "considered": prof.get("total_runs", 0),
            "triggered_by": triggered_by}


def run_connbuf_agent(triggered_by: str = "manual") -> dict:
    """Turn a roster that got TIGHTER at the connections into draft CRs.

    Plan-side only: it counts connections whose planned ground time sits
    just above the legal minimum. No operational facts are needed, which
    is why this loop says something on day one — unlike anything keyed to
    the day-of-operations journal."""
    from . import connection_buffer
    instances = registry.list_instances()
    try:
        rows = connection_buffer.build_crs(instances)
    except Exception as exc:                             # noqa: BLE001
        raise ChangeRequestError(
            f"connection-buffer loop failed: {exc}") from exc
    added = _add_crs(rows, dedup=True)
    return {"agent": "connbuf", "created": len(added),
            "duplicates": len(rows) - len(added),
            "considered": len(instances), "triggered_by": triggered_by}


AGENT_RUNNERS = {"feedback": run_feedback_agent,
                 "regulatory": run_regulatory_agent,
                 "uiux": run_uiux_agent,
                 "datahealth": run_datahealth_agent,
                 "override": run_override_agent,
                 "runtime": run_runtime_agent,
                 "connbuf": run_connbuf_agent}


# ------------------------------------------------------------ admin actions

def accept_cr(cr_id: str, by: str) -> dict:
    cr = get_cr(cr_id)
    if cr is None:
        raise ChangeRequestError(f"no such CR: {cr_id}")
    if cr["status"] not in ("draft", "needs_human"):
        raise ChangeRequestError(
            f"CR {cr_id} is {cr['status']}, only draft/needs_human can be "
            "accepted")
    out = _update_cr(cr_id, {"status": "accepted", "accepted_by": by})
    help_center.update_suggestions_status(cr.get("suggestion_ids") or [],
                                          "cr_accepted", cr_id)
    return out


def reject_cr(cr_id: str, by: str, reason: str, *,
              catalog_mirror: bool = False) -> dict:
    """Отклонить CR. Строку каталога (`kind == CATALOG_KIND`) отклоняет
    ТОЛЬКО зеркало решения владельца по заявке (catalog_mirror=True из
    webapp.catalog_pipeline): решение по заявке — за владельцем и
    принимается в службе каталога, иначе состояние заявки и состояние
    строки разошлись бы, а следующая сверка вернула бы строку обратно."""
    cr = get_cr(cr_id)
    if cr is None:
        raise ChangeRequestError(f"no such CR: {cr_id}")
    if cr.get("kind") == CATALOG_KIND and not catalog_mirror:
        raise ChangeRequestError(
            "CR каталога закрывается только решением владельца по заявке "
            "— экран службы каталога «Заявки»")
    if cr["status"] in ("implemented",):
        raise ChangeRequestError("an implemented CR cannot be rejected")
    out = _update_cr(cr_id, {"status": "rejected", "rejected_by": by,
                             "reject_reason": _clip(reason, 400)})
    help_center.update_suggestions_status(cr.get("suggestion_ids") or [],
                                          "triaged")
    return out


# --------------------------------------------------------------- implementer

_IMPL_RULES = """\
ЖЁСТКИЕ РАМКИ (нарушение любой = остановись и выведи NEEDS_HUMAN с
причиной, ничего не меняя):
- НИКАКИХ новых зависимостей (не трогай requirements*, pyproject, pom, npm);
- НИКАКИХ секретов, ключей, учётных данных, правок run-webapp.cmd;
- НИКАКИХ миграций/удалений данных (data/, webapp/config/*.json не трогать);
- НИКАКИХ правок деплой-скриптов и CI.
Разрешено: код приложения (webapp/, static/), тесты (tests/), i18n.
Требования: пиши тесты на новое поведение; следуй стилю кодовой базы;
i18n-ключи добавляй во все 5 языков; не запускай деплой и git-команды.
В конце выведи короткое резюме изменений на русском."""


def _kotlin_prompt_block(cr: dict, kinds: list, hints: str) -> str:
    """Раздел задания «ИСПОЛНИТЕЛИ РЕШАТЕЛЯ (Kotlin)» — §3 проекта,
    буквально. Здесь нет ни слова о решении владельца и ни одной строки
    правила авиакомпании: только места в коде решателя, правила теста,
    правила стендовых задач, самопроверка и запреты."""
    from . import catalog_pipeline as cp
    rec = cr.get("record") or {}
    files = "\n".join(f"  {p}" for p in cp.KOTLIN_AGENT_FILES)
    return (
        "\nИСПОЛНИТЕЛИ РЕШАТЕЛЯ (Kotlin; решатель Timefold в kotlin-solver/, "
        "сборка Maven-обёрткой):\n"
        f"Виды: {', '.join(kinds)} — образцы строк «есть» того же вида: "
        f"{hints or 'образца нет'}.\n"
        "Открыты ровно эти файлы (точный перечень):\n" + files + "\n"
        "Где и как:\n"
        "- kotlin_constraint: новый метод `fun <имя>(cf: ConstraintFactory): "
        "Constraint` в FlightCrewSchedulingConstraintProvider.kt, включённый "
        "в profileConstraints(), с `.asConstraint(\"<Имя ограничения>\")` — "
        "ИМЯ = поле expected.kotlin_constraint.constraint стендовых задач "
        "кандидата и поле constraint строки посева; жёсткое — с подписью в "
        "hardWeights() = полю label посева. Пороги — только из пакета через "
        "RunThresholds (имя параметра = имя параметра записи каталога), ни "
        "одного числа в коде.\n"
        "- kotlin_logic_form: `const val FORM_<ФОРМА> = \"<ФОРМА>\"` в "
        "LogicForms.kt, метод формы там же (symbol посева = "
        "LogicForms.<метод>), ветвь формы в LogicCompiler.kt; порождённое "
        "ограничение получает имя `logic <8 hex от id правила>` "
        "(LogicCompiler.constraintIdFor) — так его найдёт стенд. Семья "
        "истории — в LogicModel.HISTORY_FIT_FAMILIES/UNITS по образцу.\n"
        "- kotlin_knob: потребитель ручки в том ограничении, которое её "
        "исполняет (чтение порога через RunThresholds по имени параметра "
        "записи); symbol посева = RunThresholds.<метод> либо "
        "RunThresholds.KNOBS; порог/единицу/отношение в посев НЕ писать. "
        "Если ручки в KNOBS ещё нет — добавь строку `\"<параметр>\" to "
        "Knob(<порог>, \"<единица>\", \"<отношение>\", …)` по образцу "
        "соседних: единица и отношение РОВНО как у параметра в записи "
        "каталога; в кандидате запись обязана нести этот параметр с той же "
        "единицей и отношением.\n"
        "- kotlin_form: метод разбора секции пакета в TaskPackage.kt, имя "
        "секции литералом там же; мягкая семья — пара в "
        "TaskPackage.FAMILY_TO_CONSTRAINT.\n"
        "- kotlin_metric: запись `\"<метрика>\" to MetricExecutor(constraint "
        "= \"<Имя ограничения>\", tasks = setOf(…), refusal = …)` в "
        "TaskModes.METRIC_EXECUTORS — ТОЛЬКО этот блок файла; ограничение "
        "существует у провайдера; текст отказа дословно равен тексту отказа "
        "записи критерия.\n"
        "Рост таблиц решателя (новая ручка, метрика, семья истории, мягкая "
        "семья, смена единицы) — ЗАКОНЕН, но обязан быть ОБЪЯВЛЕН записью "
        "кандидата: новая ручка — параметр записи с той же единицей и тем "
        "же отношением; новая метрика — критерий с видом kotlin_metric и "
        "дословным текстом отказа; новая семья истории или мягкая семья — "
        "запись нужной природы и вида. Это судит закон паритета на "
        "временном каталоге «живой каталог ⊕ твой кандидат» — прогони его "
        "сам: python catalog/tools/parity.py --with-candidates. Назови "
        "рост в резюме. "
        f"{NEEDS_HUMAN_MARKER} — только за невозможность самой задачи, не за "
        "рост таблиц и не за недоступность сборки в твоей среде.\n"
        "Тесты (обязательно): на каждый новый исполнитель — не меньше "
        "одного нового `@Test` в НОВОМ файле "
        "kotlin-solver/src/test/kotlin/org/acme/flighcrewscheduling/solver/"
        "<Имя>Test.kt по образцу CrewSizeConstraintTest.kt "
        "(ConstraintVerifier, пороги через RunThresholds.withThresholds, "
        "очистка в @AfterEach), минимум два случая — штраф есть / штрафа "
        "нет; в тексте теста — литерал имени ограничения / формы / "
        "параметра / метрики из строки посева; числа теста совпадают с "
        "миром и ожиданием стендовой задачи кандидата (один мир — два "
        "судьи). Существующие файлы тестов и Fixtures.kt не правь; "
        "@Disabled, @EnabledIf*, @DisabledIf* и Assumptions не используй "
        "(пропущенный тест судья считает так же, как выключенный). "
        "Запрет на чтение среды, диска и сети действует и в ТЕКСТЕ ТЕСТА "
        "(File, Files, Path.of, Paths.get, FileInputStream, URI, URL, "
        "System.getenv, ProcessBuilder, import java.io/java.nio.file/"
        "java.net): тесты исполняет сборка до остальных судей, и файловый "
        "тест переписал бы самого судью — мир теста задаётся "
        "ConstraintVerifier и RunThresholds.withThresholds, как в "
        "CrewSizeConstraintTest.kt.\n"
        "Стендовые задачи кандидата: у каждого судимого вида — не меньше "
        "двух задач с РАЗНЫМИ ожиданиями matches (0 и ≥ 1); у kotlin_knob и "
        "kotlin_form — пара задач-близнецов вида kotlin_constraint на той же "
        "записи: миры отличаются ровно одним полем (значением параметра "
        "ручки либо секцией формы), ожидания — числом matches. Числа "
        "ожиданий — только из мира задачи; ни одного числа авиакомпании; "
        "ожидание под число не подгоняй.\n"
        "Самопроверка (то же прогонит гейт; сборка идёт минутами, оффлайн "
        "из кэша Maven):\n"
        f"  python -m vmp.solver_build check --records {rec.get('record_id')}"
        "\n  — сборка из рабочего дерева в scratch-папке, тесты решателя, "
        "эфемерный решатель на свободном порту, стенд записи --with-solver "
        "по трём kotlin-видам, остановка. Если в твоей среде нет Java, "
        "Maven или кэша — сдай код, тесты, посев и кандидата без "
        "самопроверки и скажи об этом в резюме: сборку и стенд прогонит "
        "конвейер.\n"
        "Нельзя: pom.xml, mvnw*, .mvn/, src/main/resources/, rest/, "
        "PackagedRuns.kt, RulesConfig.kt, ResourceRules.kt, domain/, spp/, "
        "чужие профили (Gerad*), правки TaskModes.kt вне METRIC_EXECUTORS, "
        "новые зависимости, ручки HTTP (@Path/@GET/@POST), чтение "
        "среды/диска/сети (System.getenv, File, Files, URL, ProcessBuilder), "
        "запуск решателя своими командами (только vmp.solver_build), "
        "команды git.\n")


def _previous_attempt_block(cr: dict) -> str:
    """Итоги ПРОШЛОЙ попытки — словами судей конвейера, в задание
    следующей (принцип контура самоулучшения 08.09.2026: судьи
    добавляются, агент не подменяется человеком — значит, вердикт судей
    обязан доехать до агента). Живой случай 09.09.2026, CR
    duty_series_limit: попытка #3 — стенд «судьи символа '_duty_series' у
    стенда нет» (вид check_code, три задачи) и красный набор клона; без
    этого блока попытка #4 повторила бы то же самое. Берётся только
    обезличенное: строки стенда с заметками, имена красных тестов, слова
    гейтов; без impl прошлой попытки — пусто."""
    impl = cr.get("impl") or {}
    if not isinstance(impl, dict):
        return ""
    g = impl.get("gates_report") or {}
    lines: list[str] = []
    st = g.get("stand") if isinstance(g, dict) else None
    if isinstance(st, dict):
        for row in st.get("rows_full") or ():
            if isinstance(row, dict) and row.get("result") == "failed":
                lines.append(f"- стенд: {row.get('record')} · "
                             f"{row.get('executor_kind')} · {row.get('task')} — "
                             f"failed: {str(row.get('note') or '')[:300]}")
        if st.get("words") and not lines:
            lines.append(f"- стенд: {str(st.get('words'))[:300]}")
    if isinstance(g, dict):
        for key in ("paths", "executor_files", "tree_guard", "parity_witness",
                    "chain_live", "kotlin_tests", "candidate_reload",
                    "bindings_build", "bindings_gate", "rows_words"):
            v = g.get(key)
            if v is None or v == "ok" or (key == "rows_words" and not v):
                continue
            lines.append(f"- {key}: {str(v)[:300]}")
        par = g.get("parity")
        if isinstance(par, dict) and (par.get("counts") or {}).get("red"):
            lines.append(f"- паритет: красных {par['counts'].get('red')} — "
                         f"{str(par.get('words') or '')[:300]}")
    tail = str(impl.get("tests_tail") or "")
    failed = [ln.strip() for ln in tail.splitlines() if ln.startswith("FAILED")]
    if failed:
        # Красный тест ВНЕ зоны агента чинят судьи (стенды, помощники
        # копий, установщик), а не агент: попытка #4 (09.09.2026) была
        # красна 21 тестом службы каталога и конвейера, которые агент
        # править не вправе; сказать ему «устранить» их — послать в
        # запретную зону либо в «нужен человек» по построению. Такие
        # строки помечаются, и от агента требуется одно: не красить их
        # своей правкой.
        allowed = tuple(str(x) for x in ((cr.get("work") or {}).get("allowed_paths") or ()))
        mine, theirs = [], []
        for x in failed:
            parts = x.split()
            path = parts[1].split("::")[0] if len(parts) > 1 else ""
            (mine if any(path.startswith(a) for a in allowed) else theirs).append(x)
        if mine:
            lines.append("- pytest, красные в вашей зоне: "
                         + "; ".join(x[:140] for x in mine[:15])
                         + (f" (и ещё {len(mine) - 15})" if len(mine) > 15 else ""))
        if theirs:
            lines.append("- pytest, красные ВНЕ вашей зоны (их чинят судьи на main; "
                         "от вас — только не красить их своей правкой): "
                         + "; ".join(x[:140] for x in theirs[:15])
                         + (f" (и ещё {len(theirs) - 15})" if len(theirs) > 15 else ""))
    if not lines:
        return ""
    return ("ИТОГИ ПРОШЛОЙ ПОПЫТКИ (судьи конвейера; ветка пересоздана от "
            "main, работа делается заново, эти находки — обязательны к "
            "устранению; зелёное прошлой попытки не свидетельство):\n"
            + "\n".join(lines) + "\n")


def _catalog_impl_prompt(cr: dict) -> str:
    """Задание агенту CR каталога (проект шага 6 §3.3, буквально).

    В задание НЕ попадают: машинная форма правила, тексты правил
    авиакомпании, их источники, слова решения владельца и имена прогонов
    CDPM. Едет ТОЛЬКО обезличенная спецификация записи каталога — её
    судят правила A01–A06 читателя и перечень маркеров заказчика."""
    rec = cr.get("record") or {}
    spec = cr.get("spec") or {}
    work = cr.get("work") or {}
    body = spec.get("draft")
    if body is None:
        body = spec.get("record_now")
    requested = "\n".join(
        f"- {x.get('kind')} — {x.get('hint') or 'образца строки «есть» нет'}"
        for x in (spec.get("executors_requested") or ())) or "- (нет)"
    # Перечень «отложено» пуст с 08.09.2026 (0.1) и остаётся в задании
    # ради формы: агент видит, что откладывать нечего.
    deferred = "\n".join(
        f"- {x.get('kind')} — {x.get('why')}"
        for x in (spec.get("executors_deferred") or ())) or "- (нет)"
    kotlin = [str(x.get("kind"))
              for x in (spec.get("executors_requested") or ())
              if str(x.get("kind") or "").startswith("kotlin_")]
    kotlin_hints = "; ".join(
        str(x.get("hint")) for x in (spec.get("executors_requested") or ())
        if str(x.get("kind") or "").startswith("kotlin_") and x.get("hint"))
    kotlin_block = (_kotlin_prompt_block(cr, kotlin, kotlin_hints)
                    if kotlin else "")
    return (
        "Реализуй исполнителя записи каталога шаблонов в этом репозитории "
        "(изолированная копия COSMOS; данных авиакомпаний здесь нет).\n\n"
        f"ЗАПИСЬ: {rec.get('section')}/{rec.get('record_id')} — заявка "
        f"{rec.get('request_id')} ({rec.get('request_form')}).\n"
        "СПЕЦИФИКАЦИЯ (обезличенная запись каталога; это и есть задание — "
        "не пересказ):\n"
        + json.dumps(body, ensure_ascii=False, indent=1) + "\n"
        "ВИДЫ ИСПОЛНИТЕЛЕЙ К РЕАЛИЗАЦИИ (вид — подсказка места по образцу "
        "строк «есть» того же вида):\n" + requested + "\n"
        "ОТЛОЖЕНО (не делай):\n" + deferred + "\n"
        + kotlin_block +
        f"ЗАКОН ПРИЁМКИ: {spec.get('acceptance')}\n"
        # Закон паритета едет агенту СЛОВАМИ строки (§2.5): гейт судит
        # тем же законом, и знать его заранее — не поблажка, а условие
        # того, чтобы рост объявлялся записью, а не оставался в коде.
        + (f"ЗАКОН ПАРИТЕТА: {spec.get('parity_law')}\n"
           if spec.get("parity_law") else "")
        + _preflight_block(cr)
        + _previous_attempt_block(cr)
        + "\nЧТО СДАТЬ:\n"
        f"1. Код исполнителя ТОЛЬКО в файлах перечня исполнителей: "
        f"{work.get('executor_files')} (новый файл под vmp/ — не "
        "исполнитель: стенд и сторож дрейфа его не охраняют; нужен новый "
        f"файл — остановись и выведи {NEEDS_HUMAN_MARKER}). Тесты на новое "
        "поведение — tests/vmp/ (новые файлы tests/test_*.py допустимы; "
        "существующие тесты вне tests/vmp/ не правь).\n"
        "2. Строки посева привязок vmp/bindings_seed.json: у записи "
        f"\"{rec.get('section')}/{rec.get('record_id')}\" — по строке на "
        "каждый вид из перечня, state \"есть\", file и symbol "
        "существующие, поля вида по правилам генератора; виды из "
        f"«отложено» — state \"заявлен\", note словами, cr_key "
        f"\"{cr.get('id')}\"; при новой записи адрес добавь в "
        "pending_records.\n"
        f"3. Файл-кандидат {work.get('candidate')} по схеме "
        "template_catalog/schemas/candidate.schema.json: {\"form_version\": "
        "1, \"section\", \"id\", \"record\": <запись целиком: спецификация "
        "+ status \"есть\" + stand_tasks>, \"note\", \"by\": \"контур "
        "самоулучшения\", \"saved_at\", \"request_id\": "
        f"\"{rec.get('request_id')}\", \"cr_key\": \"{cr.get('id')}\"}}.\n"
        "   Стендовые задачи: world по словарю world_forms (коды только "
        "ST1…ST9, T1…T9, P<n>), expected по видам — "
        + json.dumps(spec.get("stand_forms") or {}, ensure_ascii=False)
        + "; числа ожиданий — только из мира задачи; ни одного числа "
        "авиакомпании; в note — без порогов с единицами времени.\n"
        "   Тесты, которым нужна запись, читают её через "
        "template_catalog.candidates.record_in_tree(<каталог>, раздел, id) — "
        "кандидатом до сборки и записью каталога после; путь файла "
        "кандидата в тестах не пиши: сборка записи службой его удаляет.\n"
        "4. Самопроверка (то же прогонит гейт): "
        + "; ".join(work.get("commands") or ()) + " — все зелёные; красную "
        "строку стенда не подгоняй под число: причину — в финальный "
        "ответ.\n"
        "5. Не трогай: " + ", ".join(work.get("forbidden_paths") or ())
        + ". Никаких зависимостей, секретов, миграций, деплой-скриптов; "
        "команд git не выполняй — коммит делает конвейер.\n"
        f"6. Два законных конца: правки в дереве либо строка "
        f"\"{NEEDS_HUMAN_MARKER}: <причина>\" без правок. В конце — "
        "короткое резюме по-русски: что реализовано, какие символы, что "
        "осталось.\n")


def _impl_prompt(cr: dict) -> str:
    if cr.get("kind") == CATALOG_KIND:
        return _catalog_impl_prompt(cr)
    sug_lines = ""
    if cr.get("suggestion_ids"):
        items = help_center.list_suggestions({"email": ""}, is_admin=True)
        by_id = {s["id"]: s for s in items}
        sug_lines = "\n".join(
            f"- {by_id[i]['text']}" for i in cr["suggestion_ids"]
            if i in by_id)
    return (f"Реализуй change request в этом репозитории.\n\n"
            f"CR: {cr['title']}\n\nОПИСАНИЕ:\n{cr['summary']}\n\n"
            + (f"ИСХОДНЫЕ ПРЕДЛОЖЕНИЯ ПОЛЬЗОВАТЕЛЕЙ:\n{sug_lines}\n\n"
               if sug_lines else "")
            + _IMPL_RULES)


# Diff guard: name-based denylist ENFORCING the prompt's HARD RULES. The
# CLI runs with the permission gate disabled, so the prompt alone is not a
# boundary — any staged path matching these prefixes/names aborts the run
# (needs_human), and the branch is discarded before anything is committed.
_GUARD_NAMES = ("pyproject.toml", "pom.xml", "package.json",
                "package-lock.json", "run-webapp.cmd", "settings.gradle",
                "build.gradle")
_GUARD_NAME_PREFIXES = ("requirements", ".env", "deploy_")
_GUARD_PATH_PREFIXES = ("webapp/config/", "data/", ".github/")


def _forbidden_paths(paths: list[str]) -> list[str]:
    bad = []
    for p in paths:
        p = p.strip().replace("\\", "/")
        if not p:
            continue
        name = p.rsplit("/", 1)[-1]
        if (name in _GUARD_NAMES
                or any(name.startswith(x) for x in _GUARD_NAME_PREFIXES)
                or any(p.startswith(x) for x in _GUARD_PATH_PREFIXES)):
            bad.append(p)
    return bad


# ------------------------------------- CR каталога шаблонов (шаг 6)
#
# У строки каталога свой порядок исполнения (§3.4 проекта шага 6):
# предпосылки (код каталога в клоне, свежесть клона, тождество каталога)
# — агент — БЕЛЫЙ список путей — КОММИТ РАБОТЫ АГЕНТА — цепочка на живом
# каталоге клона — гейты на ВРЕМЕННОМ каталоге «живой ⊕ запись
# кандидата» — остановка решателя — pytest — коммит свидетельства (с
# 09.09.2026 набор идёт ПОСЛЕ гейтов: судья-решатель не живёт под
# набором, который упражняет гигиену процессов). Работа агента
# коммитится ДО гейтов содержания: красный гейт не имеет права стереть
# сделанное.

#: Предел одной команды цепочки/гейта каталога (стенд и генератор
#: привязок идут минутами).
CATALOG_CMD_TIMEOUT_S = 900.0


# ------------------------------------ сборка решателя и эфемерный решатель
#
# Пакет «Kotlin-исполнители агентом» (08.09.2026, слово владельца: в
# контуре автоулучшения всё пишет сам COSMOS). Виды исполнителей решателя
# пишет тот же агент тем же CR, а недостающих судей добавляет конвейер:
# сборка решателя ИЗ КОММИТА ветки в отдельной папке попытки, тесты
# решателя после упаковки, эфемерный решатель из собранного артефакта и
# живой стенд на временном каталоге.
#
# Всё вычисление сборки, запуска и остановки JVM живёт в ОДНОМ модуле
# `vmp/solver_build.py` — том же, что агент зовёт самопроверкой: агент
# видит те же слова, что скажет гейт. Здесь — вызовы модуля, суд над его
# отчётами и порядок цепочки, и ни одной своей команды Maven или JVM.


def _solver_module_or_words():
    """Модуль сборки решателя либо слова о том, что его нет."""
    try:
        from vmp import solver_build
    except ImportError as exc:                          # pragma: no cover
        return None, (f"модуль сборки решателя vmp.solver_build недоступен "
                      f"({exc}): сборка, тесты решателя и живой стенд не "
                      f"начинаются — приведите клон на срез с пакетом "
                      f"«Kotlin-исполнители агентом»")
    return solver_build, None


def _product_root():
    """Корень дерева ПРОДУКТА — того, из которого работает приложение.

    Судьи-подпроцессы (сборка решателя) исполняются отсюда, а судимое
    дерево называют ключом `--repo`: закон продукта не берётся из ветки,
    которую он судит. Корень определяется по файлу самого модуля сборки
    (он же исполняется), а не по догадке о рабочей папке."""
    from pathlib import Path
    mod, _words = _solver_module_or_words()
    path = getattr(mod, "__file__", None) if mod is not None else None
    if path:
        return Path(path).resolve().parents[1]
    return Path(__file__).resolve().parents[1]


#: Неотслеживаемый шум рабочей папки, который сторож дерева не считает
#: правкой: папка попыток (снята с учёта git) и следы Python и pytest.
_TREE_NOISE = ("_repro/", ".pytest_cache/", "__pycache__/", ".mypy_cache/",
               ".ruff_cache/")


def _tree_dirt(ev, repo, allowed=()) -> list:
    """Пути, изменённые в рабочем дереве клона сверх дозволенных.

    Сторож нужен между шагами: тесты решателя исполняются кодом АГЕНТА
    под учётной записью службы и до остальных судей — файловый тест
    переписал бы стенд, гейты и pytest того же клона, и все три сказали
    бы «зелено». Проверка чистоты дерева была только ДО реализации;
    здесь она делается после сборки и перед свидетельством."""
    allow = {str(x).replace("\\", "/") for x in (allowed or ()) if x}
    _rc, out = ev._git(repo, "-c", "core.quotePath=false", "status",
                       "--porcelain", "--untracked-files=all")
    dirt = []
    for line in (out or "").splitlines():
        if not line.strip():
            continue
        body = line[3:].strip() if len(line) > 3 else ""
        # У переименования путей два, и грязью считается каждый: иначе
        # снос судьи переносом на имя файла свидетельства прошёл бы.
        parts = body.split(" -> ") if " -> " in body else [body]
        for path in parts:
            path = path.strip().strip('"').replace("\\", "/")
            if not path or path in allow:
                continue
            if any(path.startswith(x) for x in _TREE_NOISE) \
                    or "/__pycache__/" in path or path.endswith(".pyc"):
                continue
            dirt.append(path)
    return sorted(set(dirt))[:10]


def _solver_handles() -> dict:
    """Собственные ручки проекта (0.10). Читаются В ПРОЦЕССЕ приложения и
    едут подпроцессам АРГУМЕНТАМИ: окружение подпроцессов — белый список
    модуля, и вычистка префикса `COSMOS_` унесла бы эти ручки из него."""
    import os
    return {
        "java_home": os.environ.get("COSMOS_SOLVER_JAVA_HOME") or None,
        "mvn": os.environ.get("COSMOS_SOLVER_MVN") or None,
        "xmx": os.environ.get("COSMOS_SOLVER_XMX") or SOLVER_XMX_RUN,
        "m2": os.environ.get("COSMOS_SOLVER_M2") or None,
    }


def _solver_factory(artifact_dir, *, cr_key: str, log_path,
                    java_home=None, xmx: str = SOLVER_XMX_RUN,
                    start_timeout_s: float = SOLVER_START_TIMEOUT_S,
                    tag: str | None = None):
    """ЕДИНСТВЕННАЯ точка рождения долгоживущей JVM решателя (0.5, 0.13).

    Тесты подменяют её подставным решателем: живой JVM в них нет, а
    порядок цепочки, адрес в командах стенда и остановка в `finally`
    судятся так же, как на настоящем прогоне."""
    mod, words = _solver_module_or_words()
    if mod is None:                                     # pragma: no cover
        raise ChangeRequestError(words)
    # `tag` — метка контура в командной строке JVM (сборщик сирот снимает
    # только свои решатели); сквозная проверка 09.09: фабрика не принимала
    # ключ, и эфемерный решатель не поднимался после зелёной сборки.
    return mod.EphemeralSolver(artifact_dir, cr_key=cr_key,
                               log_path=log_path, java_home=java_home,
                               xmx=xmx, start_timeout_s=start_timeout_s,
                               tag=tag)


def _solver_limits() -> dict:
    """Пределы живого прогона — ИМПОРТОМ из стенда (§4): литералов у
    конвейера нет, иначе свидетельство рассказывало бы о пределах, по
    которым прогон не шёл."""
    out = {}
    try:
        import inspect

        from vmp import stand_solver as ss
        out = {"spent_limit_s": ss.LIMIT_S, "wait_ready_s": ss.WAIT_S,
               "poll_s": ss.POLL_S,
               "http_timeout_s": inspect.signature(
                   ss.solver_client).parameters["timeout_s"].default}
    except Exception as exc:                            # noqa: BLE001
        out = {"words": f"пределы стенда не прочитаны: {exc}"}
    out["start_timeout_s"] = SOLVER_START_TIMEOUT_S
    return out


def _kotlin_kinds(cr: dict) -> list:
    """Виды исполнителей решателя, запрошенные этим CR."""
    from . import catalog_pipeline as cp
    spec = cr.get("spec") or {}
    return [str(x.get("kind"))
            for x in (spec.get("executors_requested") or ())
            if str(x.get("kind") or "").startswith(cp.SOLVER_KIND_PREFIX)]


def _attempt_dir(repo, cr_id: str):
    """Папка ПОПЫТКИ: своя у каждого нажатия (0.11). Общая папка отдавала
    бы `stand.json` прошлой попытки за свидетельство этой.

    Номер считается по МАКСИМУМУ существующих, а не по их количеству:
    удалённая владельцем ранняя папка (чистка диска) сдвигала счёт назад,
    и следующая попытка садилась в чужую папку — журналы дописывались к
    прошлым, а `build.json`/`stand.json` прошлой попытки читались, когда
    команда этой не успела записать своего. Если папка с этим номером
    всё же существует (имя не разбирается числом), берётся имя со
    временем: две разные попытки под одним именем — не воспроизводимость,
    а её видимость."""
    root = repo / "_repro" / f"catalog-cr-{cr_id}"
    root.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in root.iterdir():
        if not (p.is_dir() and p.name.startswith("attempt-")):
            continue
        tail = p.name[len("attempt-"):].split("-")[0]
        if tail.isdigit():
            n = max(n, int(tail))
    n += 1
    path = root / f"attempt-{n}"
    if path.exists():
        path = root / f"attempt-{n}-{datetime.now():%Y%m%d-%H%M%S}"
    if path.exists():                                    # pragma: no cover
        raise ChangeRequestError(
            f"папка попытки {path.name} уже занята: свидетельство этой "
            f"попытки смешалось бы с прошлой — уберите её вручную")
    path.mkdir(parents=True)
    return path, n


# Личность автора коммитов конвейера. Она СВОЯ и явная: в клоне, где
# `user.name`/`user.email` не настроены, git отказывает «Author identity
# unknown» с кодом 128 — и прежде этот отказ проходил молча (сквозная
# проверка 08.09.2026): работа агента не коммитилась, цепочка судила
# базовую голову, сторож дерева считал файлы агента грязью, а уборка их
# стирала. Умолчание не зависит от машины, ручки — на случай своего
# имени контура.
CR_AGENT_NAME = "COSMOS agent"
CR_AGENT_EMAIL = "agent@cosmos.local"


def _agent_identity() -> list[str]:
    """Флаги личности автора для `git commit` конвейера.

    Личность едет АРГУМЕНТАМИ вызова, а не настройкой клона: настройка —
    свойство машины, на которой развёрнут контур, и её отсутствие как раз
    и было отказом. Ручки читаются в процессе приложения, как прочие
    ручки проекта (0.10)."""
    import os
    name = (os.environ.get("COSMOS_AGENT_NAME") or "").strip()
    mail = (os.environ.get("COSMOS_AGENT_EMAIL") or "").strip()
    return ["-c", f"user.name={name or CR_AGENT_NAME}",
            "-c", f"user.email={mail or CR_AGENT_EMAIL}"]


def _commit_work(ev, repo, message: str) -> tuple[str, str]:
    """Коммит работы агента: личность автора — своя, исход — проверенный.

    Возвращает (короткий SHA, слова отказа). Отказом считается и
    ненулевой код, и «код 0, а HEAD на месте»: `git commit` с пустым
    индексом кончается ничем, и «коммит есть» нельзя выводить из
    отсутствия ошибки."""
    _rc0, before = ev._git(repo, "rev-parse", "HEAD")
    rc, out = ev._git(repo, *_agent_identity(), "commit", "-m", message)
    _rc1, after = ev._git(repo, "rev-parse", "HEAD")
    if rc != 0 or not after.strip() or after.strip() == before.strip():
        tail = " ".join((out or "").split())[-300:]
        words = (f"код {rc}" if rc else "код 0, HEAD не сдвинулся")
        return "", (f"{words}: {tail}" if tail else words)
    _rc2, short = ev._git(repo, "rev-parse", "--short", "HEAD")
    return short.strip(), ""


def _rescue_agent_work(ev, repo, where) -> str:
    """Работа агента — патчем на диск, когда коммит НЕ состоялся.

    Уборка клона (`_cleanup_tree`) делает `reset --hard` и `clean -fd`:
    без этого шага незакоммиченная работа исчезала бы вместе с отказом, и
    человеку, которому строка ушла в `needs_human`, смотреть было бы
    нечего. Папка `_repro/` закрыта `.gitignore`, поэтому `clean -fd` её
    не уносит. Патч снимается `git diff` — командой, которой личность
    автора не нужна: спасение не вправе зависеть ровно от того, что
    отказало. Неотслеживаемые файлы патч не несёт, поэтому их имена
    ложатся списком рядом."""
    try:
        where.mkdir(parents=True, exist_ok=True)
        path = where / "agent-work.patch"
        _rc, out = ev._git(repo, "-c", "core.quotePath=false", "diff",
                           "HEAD", "--binary", f"--output={path}")
        if not path.is_file():           # git без `--output` (до 2.22)
            path.write_text(out or "", encoding="utf-8")
        _rc_u, others = ev._git(repo, "-c", "core.quotePath=false",
                                "ls-files", "--others", "--exclude-standard")
        extra = [ln.strip() for ln in (others or "").splitlines()
                 if ln.strip()]
        if extra:
            (where / "agent-work-untracked.txt").write_text(
                "\n".join(extra) + "\n", encoding="utf-8")
        try:
            shown = path.relative_to(repo).as_posix()
        except ValueError:                               # pragma: no cover
            shown = path.name
        return (f"работа агента сохранена патчем {shown}"
                + (f" и списком неотслеживаемых ({len(extra)}) в "
                   "agent-work-untracked.txt" if extra else ""))
    except Exception as exc:                             # noqa: BLE001
        return f"работа агента НЕ сохранена: {exc}"


def _json_line(text: str):
    """Последняя строка вывода, которая разбирается как объект JSON:
    подпроцесс вправе написать перед ней предупреждения."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(doc, dict):
                return doc
    return None


def _tables_snapshot(ev, repo, py: str) -> tuple:
    """Снимок таблиц решателя ДЕРЕВА КЛОНА (0.9): читатель
    `vmp/kotlin_tables.py` привязан к своему модулю, поэтому команда
    исполняется в клоне, а не в продукте.

    Клон здесь — источник ДАННЫХ (тексты `kotlin-solver/`), но и код
    читателя берётся из него же, поэтому от подмены снимок держат
    запреты путей: `vmp/kotlin_tables.py`, `vmp/__init__.py` и
    `vmp/run/__init__.py` агенту закрыты, а снимок ветки снимается уже
    ПОСЛЕ белого списка путей — правка любого из них краснит гейт
    прежде, чем команда исполнится. Снимок `main` идёт по дереву без
    работы агента вовсе."""
    cmd = [py, "-c", "import json; from vmp import kotlin_tables as kt; "
                     "print(json.dumps(kt.tables(), ensure_ascii=False))"]
    rc, out = ev._run(cmd, cwd=repo, timeout_s=SOLVER_PROBE_TIMEOUT_S,
                      scrub_prefixes=ev._GATE_ENV_SCRUB)
    doc = _json_line(out)
    if rc != 0 or doc is None:
        return None, (f"таблицы решателя не читаются: код {rc} — "
                      f"{(out or '')[-300:]}")
    return doc, None


#: Свидетели закона паритета, живущие в коде исполнителя
#: `vmp/run/roster_certify.py`: по ним правила L15 (природа семьи), L16
#: (условия) и L09 (сертификатные шаблоны проверок) судят запись
#: каталога. Просьба В-2 пакета «паритет на временном каталоге»: файл
#: целиком закрыть нельзя — он исполнитель строк «есть» посева
#: (`measure_soft_rules`), — но правка ЭТИХ перечней делает «рост
#: объявлен записью» самосертифицируемым: агент меняет свидетеля, и
#: запись любой природы становится «по коду».
PARITY_WITNESS_CONSTS = ("PEOPLE_FAMILIES", "WORLD_PARAM_FAMILIES",
                         "EXTENSION_CONDITIONS", "SOFT_MEASURE_TEMPLATE",
                         "WORLD_PARAM_TEMPLATE")

_CERTIFY_SNAPSHOT_CODE = """
import json
from vmp.run import roster_certify as rc
names = %r


def norm(v):
    if isinstance(v, (set, frozenset)):
        return sorted(str(x) for x in v)
    if isinstance(v, dict):
        return {str(k): norm(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [norm(x) for x in v]
    return v


print(json.dumps({n: norm(getattr(rc, n, None)) for n in names},
                 ensure_ascii=False, sort_keys=True))
"""


def _certify_snapshot(ev, repo, py: str) -> tuple:
    """Снимок свидетелей паритета из `vmp/run/roster_certify.py` дерева.

    Приём тот же, что у снимка таблиц решателя (`_tables_snapshot`):
    команда исполняется в клоне, потому что читаются перечни ИМЕННО его
    кода, а от подмены читателя держат запреты путей (`vmp/__init__.py`,
    `vmp/run/__init__.py` агенту закрыты) и то, что снимок ветки берётся
    уже ПОСЛЕ белого списка путей."""
    cmd = [py, "-c", _CERTIFY_SNAPSHOT_CODE % (list(PARITY_WITNESS_CONSTS),)]
    rc, out = ev._run(cmd, cwd=repo, timeout_s=SOLVER_PROBE_TIMEOUT_S,
                      scrub_prefixes=ev._GATE_ENV_SCRUB)
    doc = _json_line(out)
    if rc != 0 or doc is None:
        return None, (f"свидетели паритета не читаются: код {rc} — "
                      f"{(out or '')[-300:]}")
    return doc, None


def _contour_marker(mod, repo) -> str:
    """Метка эфемерных решателей ЭТОГО клона (контура).

    Признак контура в метке — единственное, чем сборщик сирот отличает
    свою брошенную JVM от живого судьи соседнего контура: клоны Test,
    Stage и Prod стоят на одной машине под одной учётной записью, и
    метка без признака делала снятие межконтурным."""
    fn = getattr(mod, "contour_marker", None)
    if callable(fn):
        return fn(repo)
    return getattr(mod, "MARKER", "-Dcosmos.ephemeral=")


def _solver_tag_or_none(repo) -> str | None:
    """Признак контура для метки поднимаемой JVM (пара к метке выше)."""
    mod, _words = _solver_module_or_words()
    fn = getattr(mod, "contour_tag", None) if mod is not None else None
    try:
        return fn(repo) if callable(fn) else None
    except Exception:                                   # noqa: BLE001
        return None


def _solver_java_home(repo):
    """Дом Java для эфемерного решателя — ПОРЯДКОМ ПОИСКА 0.12.

    Ручка `COSMOS_SOLVER_JAVA_HOME` — только первая ступень этого
    порядка; за ней `JAVA_HOME`, `<клон>/.tools/jdk*` установщика и
    `java` в PATH. Проба и сборка ходят порядком, и решатель обязан
    ходить им же, иначе зелёная проба кончалась бы падением старта."""
    handles = _solver_handles()
    mod, _words = _solver_module_or_words()
    order = getattr(mod, "java_home_order", None) if mod is not None else None
    if callable(order):
        try:
            return order(repo, handles["java_home"])[0]
        except Exception:                               # noqa: BLE001
            pass
    return handles["java_home"]


def _solver_preflight(ev, repo, cr: dict) -> dict:
    """Проба тулчейна ДО ветки и ДО квоты (0.12).

    «Сборка недоступна» обязана звучать словами прежде, чем потрачена
    реализация дня: без Java, обёртки Maven, кэша зависимостей или прав
    на `_repro/` ни один судья решателя не состоится."""
    mod, words = _solver_module_or_words()
    if mod is None:
        raise ChangeRequestError(words)
    handles = _solver_handles()
    try:
        out = mod.probe(repo, java_home=handles["java_home"],
                        mvn=handles["mvn"],
                        venv_python=ev._venv_python(repo))
    except Exception as exc:                            # noqa: BLE001
        raise ChangeRequestError(
            f"проба сборки решателя не выполнена: {exc}") from exc
    if not (out or {}).get("ok"):
        raise ChangeRequestError(
            "сборка решателя на этом контуре недоступна: "
            + str((out or {}).get("words")
                  or "проба не назвала причины словами"))
    try:
        # Метка — СВОЕГО контура: на производственной машине Test, Stage
        # и Prod живут под одной учётной записью, и снятие «всех
        # эфемерных решателей машины» убивало бы суд соседнего контура на
        # полпути (стенд идёт минутами).
        mod.reap_orphans(_contour_marker(mod, repo))
    except Exception:                                   # noqa: BLE001
        pass                     # сироты — забота гигиены, не предпосылка
    return out


def _seed_rows(repo, cr: dict) -> dict:
    """Строки посева ЭТОГО CR: {вид: строка}. Предмет суда (имя
    ограничения, форма, параметр, метрика) берётся отсюда — из того же
    файла, по которому генератор строит привязки."""
    rec = cr.get("record") or {}
    addr = f"{rec.get('section')}/{rec.get('record_id')}"
    try:
        doc = json.loads((repo / "vmp" / "bindings_seed.json")
                         .read_text(encoding="utf-8",
                                    errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = ((doc.get("records") or {}).get(addr)) or []
    return {str(r.get("kind")): r for r in rows if isinstance(r, dict)}


def _seed_literals(repo, cr: dict) -> list:
    """Литералы посева для гигиены тестов Kotlin (0.4): имя ограничения,
    форма, параметр, метрика. Новый тест обязан назвать хоть один из них
    — тест без предмета не про этого исполнителя."""
    out = []
    for kind, row in _seed_rows(repo, cr).items():
        if not kind.startswith("kotlin_"):
            continue
        for field in ("constraint", "form", "param", "metric", "label"):
            value = row.get(field)
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
    return sorted(set(out))


def _executor_files() -> set:
    """Перечень охраняемых файлов-исполнителей: CODE_FILES стенда ∪
    EXECUTOR_FILES сторожа дрейфа. Берётся из ПРОДУКТА этого дерева, а
    не из клона: перечень — закон продукта, а тождество клона с ним уже
    подтверждено предпосылкой (отпечаток каталога) до ветки."""
    from vmp.registry_check import EXECUTOR_FILES
    from vmp.stand_tasks import CODE_FILES
    return {p.replace("\\", "/") for p in (*CODE_FILES, *EXECUTOR_FILES)}


def _catalog_preflight(ev, repo, cr: dict) -> dict:
    """Предпосылки CR каталога. Любой отказ — словами и ДО ветки (квота
    не берётся).

    Возврат: слова о свежести клона для карточки, виды исполнителей
    решателя этого CR и снимок таблиц решателя на `main` (0.9) — по нему
    после работы агента считается класс правки. У CR с видами решателя
    сюда добавлена проба тулчейна (0.12): «сборки решателя здесь нет»
    обязано звучать словами до траты квоты."""
    import template_catalog as tc
    rec = cr.get("record") or {}
    if not (repo / "template_catalog").is_dir() \
            or not (repo / "vmp" / "stand_tasks.py").is_file():
        raise ChangeRequestError(
            "клон Test без кода каталога: реализация CR каталога "
            "невозможна — приведите клон на срез с шагами 4–6")
    rc, _ = ev._git(repo, "remote", "get-url", "origin")
    if rc == 0:
        rc_f, out_f = ev._git(repo, "fetch", "origin", "main")
        rc_c, out_c = ev._git(repo, "checkout", "main")
        rc_m, out_m = ev._git(repo, "merge", "--ff-only", "origin/main")
        if rc_f != 0 or rc_c != 0 or rc_m != 0:
            raise ChangeRequestError(
                "клон Test отстал от main и не сводится перемоткой: "
                + (out_m or out_c or out_f or "")[-300:])
        sync = "клон сведён с origin/main перемоткой"
    else:
        sync = "клон без origin: ветка от локального main"
    want = str(rec.get("catalog_fingerprint") or "")
    try:
        got = tc.load(repo / "catalog").fingerprint()
    except Exception as exc:                             # noqa: BLE001
        raise ChangeRequestError(
            f"каталог клона Test не читается: {exc}") from exc
    if want and got != want:
        raise ChangeRequestError(
            f"каталог в клоне Test ({got[:12]}…) не тот, по которому "
            f"открыт CR ({want[:12]}…): либо клон отстал от main, либо "
            f"каталог изменился после открытия CR — обновите клон или "
            f"перезаявите ключ")
    out = {"sync": sync, "kinds": _kotlin_kinds(cr), "probe": None,
           "tables_main": None, "tables_words": None,
           "certify_main": None, "certify_words": None}
    # Свидетели паритета на main — до ветки: правку перечней
    # `roster_certify` после работы агента видно только сравнением с
    # ними (просьба В-2). Снимок берётся у КАЖДОГО CR каталога: природу,
    # условия и сертификатные шаблоны судят правила L15/L16/L09, а они
    # идут не только у CR с видами решателя.
    out["certify_main"], out["certify_words"] = _certify_snapshot(
        ev, repo, ev._venv_python(repo))
    # Проба тулчейна и сборщик сирот — у КАЖДОГО CR каталога: сборка
    # решателя и эфемерный решатель идут у каждого (0.2), а `implemented`
    # требует зелёной сборки и поднятого решателя (0.16). Держать пробу
    # только за kotlin-видами значило бы на контуре без Java прогнать
    # агента полчаса, взять реализацию дня и лишь на шаге 5 сказать
    # «сборка решателя недоступна» — прямо против условия 0.12 («сборка
    # недоступна» звучит словами ДО траты квоты). Снимок таблиц остаётся
    # за kotlin-видами: класс правки нужен только им (0.9).
    out["probe"] = _solver_preflight(ev, repo, cr)
    if out["kinds"]:
        # Шаг 0-К (§1): снимок таблиц решателя на main — до ветки и до
        # квоты.
        tables, words = _tables_snapshot(ev, repo, ev._venv_python(repo))
        if tables is None:
            raise ChangeRequestError(
                f"таблицы решателя на main не читаются: {words} — класс "
                f"правки посчитать нечем, реализация не начинается")
        out["tables_main"] = tables
    return out


# ---------------------------------------- предполётная проверка задания
#
# ЗАЧЕМ. 09.09.2026 первая новая семья каталога (duty_series_limit) дошла
# до записи с ПЯТОЙ попытки агента, и четыре сорванные попытки сорвались
# не о шаблон, а о ДОПУЩЕНИЯ СУДЕЙ: судья был написан на неподвижный
# каталог — жёсткое число записей в ожидании, рукописный перечень семей,
# копия каталога без заявки-свидетеля. Каждая такая попытка стоит
# реализации дня и получаса машинного времени, а находится всё это ДО
# агента и дёшево: заготовку записи можно построить прямо из строки
# заявки на изменение и прогнать по ней тех же судей.
#
# ЧТО СУДИТСЯ. Заготовка — запись, какой она будет ДО работы агента:
# параметры с единицами и объявленные виды исполнителей есть, самих
# исполнителей и стендовых задач нет. На временном каталоге «живой ⊕
# заготовка» идут дешёвые судьи: читатель и его правила, генератор
# привязок с посевом, гейт привязок, снятие отметок ожидания, перечень
# судей стенда, чтение записи деревом и набор тестов копий каталога в
# режиме роста с ЭТОЙ заготовкой.
#
# КАК ЧИТАЮТСЯ НАХОДКИ. Судья, который НАЗВАЛ запись по имени, работает
# верно: «исполнителя нет» — ожидаемый пробел, его закрывает агент.
# Судья, который сломался или пожаловался, НЕ НАЗВАВ записи, споткнулся
# о сам рост каталога — это допущение, и строка уходит «нужен человек»
# ДО агента, с перечнем находок.
#: Вердикты предполётной проверки.
PREFLIGHT_OK = "ok"
PREFLIGHT_GAPS = "expected-gaps"
PREFLIGHT_ASSUMPTION = "judge-assumption"

#: Набор тестов копий каталога, который предполётная проверка прогоняет
#: в режиме роста с заготовкой записи. Файлы, которых в клоне нет,
#: пропускаются словами: судить нечем — не то же самое, что зелено.
PREFLIGHT_TEST_PATHS = ("tests/test_template_catalog_validate.py",
                        "tests/test_template_catalog_reader.py",
                        "tests/test_template_catalog_parity.py",
                        "tests/catalog_service",
                        "tests/vmp/test_bindings.py",
                        "tests/vmp/test_stand_tasks.py")
PREFLIGHT_TEST_TIMEOUT_S = 1200.0
PREFLIGHT_CMD_TIMEOUT_S = 300.0


def _pf_catalog(ctx) -> list:
    """Читатель и его правила на временном каталоге «живой ⊕ заготовка».

    Ошибки берутся ВСЕ (негодный временный каталог — беда, чья бы запись
    её ни вызвала), предупреждения — только о самой заготовке: чужие
    предупреждения живого каталога судит закон паритета (L11), и здесь
    они были бы находкой не про эту заявку."""
    import template_catalog as tc
    cat = tc.load(ctx["folder"])
    out = []
    for p in cat.validate():
        if p.severity == "error" or ctx["rid"] in str(p.where):
            out.append(f"{p.severity} {p.code} {p.where}: {p.message}")
    return out


def _pf_reader(ctx) -> list:
    """Запись видна дереву и читателю: `record_in_tree` отдаёт её
    (каталогом либо кандидатом), а раздел каталога знает её имя."""
    from template_catalog import candidates as tcc
    out = []
    rec, source = tcc.record_in_tree(ctx["folder"], ctx["section"],
                                     ctx["rid"])
    if not rec:
        out.append(f"{ctx['addr']}: запись дерева пуста")
    ctx["record_source"] = source
    return out


def _pf_bindings(ctx) -> list:
    """Генератор привязок и гейт привязок на временном каталоге.

    Посев — живой ⊕ строки «нет» по объявленным видам заготовки: строк
    «есть» у неё ещё нет, их пишет агент. Отказ генератора, назвавший
    запись, — ожидаемый пробел; отказ, записи не назвавший, — допущение."""
    import copy

    import template_catalog as tc
    from template_catalog import growth
    from vmp import bindings_build as bb
    from vmp import bindings_gate as bg
    out = []
    seed = copy.deepcopy(bb.load_seed())
    seed["records"] = {
        **(seed.get("records") or {}),
        **growth.probe_seed_records(growth.MODE_DRAFT,
                                    seed.get("records") or {},
                                    environ=ctx["env"])}
    cat = tc.load(ctx["folder"])
    try:
        doc = bb.build(cat, commit="0" * 40,
                       generated_at="2026-01-01T00:00:00+00:00",
                       stand_results=None, seed=seed)
    except bb.BindingsBuildError as exc:
        return [f"генератор привязок: {exc}"]
    rep = bg.gate(cat, doc, None)
    out += [f"гейт привязок: {w}" for w in rep.errors]
    ctx["bindings"] = doc
    return out


def _pf_prune_pending(ctx) -> list:
    """Снятие отметок ожидания (`--prune-pending`) на КОПИИ посева.

    Отметка ожидания — состояние, выводимое из каталога: как только
    каталог назвал запись, отметка обязана уйти сама. Судится копия
    посева, не дерево: предполётная проверка ничего в дереве не правит."""
    import copy

    from template_catalog.export import dump_json
    from vmp import bindings_build as bb
    seed = copy.deepcopy(bb.load_seed())
    seed["records"] = {
        **(seed.get("records") or {}),
        **_pf_seed_rows(ctx)}
    seed["pending_records"] = sorted(
        set(seed.get("pending_records") or []) | {ctx["addr"]})
    path = ctx["tmp"] / "preflight-seed.json"
    path.write_bytes(dump_json(seed).encode("utf-8"))
    stand = _pf_empty_stand(ctx)
    if stand is None:
        ctx["prune_pending_words"] = (
            "свежих результатов стенда для временного каталога не "
            "построить — судить нечем")
        return []
    # Отдельным процессом с дверью на ВРЕМЕННЫЙ каталог: внутри своего
    # процесса предполётная проверка переменных среды не трогает.
    rc, out = ctx["ev"]._run(
        [ctx["py"], "-m", "vmp.bindings_build",
         "--out", str(ctx["tmp"] / "preflight-bindings.json"),
         "--seed", str(path), "--stand-results", str(stand),
         "--prune-pending", "--commit", "0" * 40,
         "--generated-at", "2026-01-01T00:00:00+00:00"],
        cwd=ctx["repo"], timeout_s=PREFLIGHT_CMD_TIMEOUT_S,
        env_extra={"CREW_TEMPLATE_CATALOG": str(ctx["folder"])})
    if rc != 0:
        return [f"посев привязок: код {rc} — {(out or '')[-200:]}"]
    after = json.loads(path.read_text(encoding="utf-8"))
    if ctx["addr"] in (after.get("pending_records") or []):
        return [f"{ctx['addr']}: отметка ожидания осталась в посеве после "
                f"того, как каталог назвал запись"]
    return []


def _pf_empty_stand(ctx):
    """Файл результатов стенда для ВРЕМЕННОГО каталога: пустой, но
    свежий (отпечаток и версия — временного каталога, карта отпечатков
    кода — клона).

    Без него генератор берёт результаты с обычного места
    (`vmp/stand_results.json`), а они сняты на ЖИВОМ каталоге — и отказ
    «отпечаток каталога в результатах не совпадает» приходил бы у каждой
    заявки на новую запись. Пустой перечень честен: стенд по заготовке
    ещё не бегал, стендовых подтверждений у неё нет. Построить файл не
    вышло (обрезанное дерево) — None, судья молчит словами."""
    import template_catalog as tc
    try:
        from vmp import stand_tasks as st
        cat = tc.load(ctx["folder"])
        doc = {"form_version": 1,
               "catalog_version": cat.version(),
               "catalog_fingerprint": cat.fingerprint(),
               "code": st.code_fingerprints(ctx["repo"]),
               "at": "2026-01-01T00:00:00+00:00",
               "commit": "0" * 40, "solver": None, "results": []}
    except Exception:                                    # noqa: BLE001
        return None
    path = ctx["tmp"] / "preflight-stand.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _pf_seed_rows(ctx) -> dict:
    from template_catalog import growth
    from vmp import bindings_build as bb
    return growth.probe_seed_records(growth.MODE_DRAFT,
                                     (bb.load_seed().get("records") or {}),
                                     environ=ctx["env"])


def _pf_stand(ctx) -> list:
    """Перечень судей стенда: он обязан ВЫВОДИТЬСЯ из кода исполнителей,
    а не быть рукописным.

    У заготовки исполнителя ещё нет, и судья символа у стенда законно
    отсутствует — это ожидаемый пробел, названный именем записи. Беда
    здесь другая: перечень не читается вовсе либо стенд ломается на
    записи, о которой ничего не знает."""
    from vmp import stand_tasks as st
    symbols = st._logic_symbols()
    if not symbols:
        return ["перечень судей логических форм пуст — стенд судить нечем"]
    tasks = (ctx["record"].get("stand_tasks") or [])
    if not tasks:
        return [f"{ctx['addr']}: стендовых задач у заготовки нет — их "
                f"пишет агент вместе с исполнителем"]
    return []


def _pf_kinds(ctx) -> list:
    """Объявленные виды исполнителей ↔ строки привязок: каждый вид
    заготовки обязан быть НАЗВАН строкой (пусть «нет»), иначе гейт
    привязок скажет «в зоне, но не объявил» уже у агента."""
    doc = ctx.get("bindings")
    if not isinstance(doc, dict):
        return []
    row = next((r for r in (doc.get("bindings") or [])
                if f"{r.get('section')}/{r.get('id')}" == ctx["addr"]), None)
    if row is None:
        return [f"{ctx['addr']}: строки привязок у записи нет"]
    have = {str(e.get("kind")): str(e.get("state"))
            for e in (row.get("executors") or [])}
    out = []
    for kind in ctx["kinds"]:
        state = have.get(kind)
        if state is None:
            out.append(f"{ctx['addr']}: вид {kind} объявлен записью, строки "
                       f"привязок нет")
        elif state != "есть":
            out.append(f"{ctx['addr']}: исполнителя нет — вид {kind} в "
                       f"состоянии «{state}»")
    return out


def _pf_tests(ctx) -> list:
    """Набор тестов копий каталога в режиме роста С ЭТОЙ заготовкой.

    Переменная среды режима роста называет файл заготовки, и весь набор
    прогоняется на каталоге, который ВЫРОС ровно этой записью. Красный
    тест здесь — допущение судьи, найденное до агента."""
    from template_catalog import growth
    ev, repo, py = ctx["ev"], ctx["repo"], ctx["py"]
    paths = [p for p in PREFLIGHT_TEST_PATHS if (repo / p).exists()]
    if not paths:
        ctx["tests_words"] = ("набора тестов копий каталога в клоне нет — "
                              "судить нечем")
        return []
    rc, out = ev._run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                       *paths], cwd=repo,
                      timeout_s=PREFLIGHT_TEST_TIMEOUT_S,
                      env_extra={growth.MODE_ENV: str(ctx["cand_path"])})
    хвост = (out or "").strip().splitlines()
    ctx["tests_words"] = (хвост[-1][:300] if хвост else "")
    if rc == 0:
        return []
    failed = [ln.strip() for ln in (out or "").splitlines()
              if ln.startswith("FAILED") or ln.startswith("ERROR")]
    return [f"тесты копий каталога в режиме роста: {w[:200]}"
            for w in failed[:10]] or [
        f"тесты копий каталога в режиме роста: код {rc}"]


#: Судьи предполётной проверки: имя → функция(среда) → перечень находок
#: словами. Перечень открыт намеренно: судьи ДОБАВЛЯЮТСЯ (принцип контура
#: самоулучшения), а стенд подменяет одного судью, не трогая остальных.
PREFLIGHT_JUDGES = {
    "catalog": _pf_catalog,
    "reader": _pf_reader,
    "bindings": _pf_bindings,
    "kinds": _pf_kinds,
    "prune_pending": _pf_prune_pending,
    "stand": _pf_stand,
    "tests": _pf_tests,
}


def preflight_draft(cr: dict) -> dict:
    """Заготовка записи по строке CR — файл-кандидат, какой напишет
    агент, но БЕЗ исполнителей: черновик заявки ⊕ состояние «есть» ⊕
    пустой перечень стендовых задач."""
    from template_catalog import growth
    rec = cr.get("record") or {}
    spec = cr.get("spec") or {}
    draft = spec.get("draft")
    if draft is None:
        draft = spec.get("record_now")
    if not isinstance(draft, dict) or not draft:
        raise ChangeRequestError(
            "в строке CR нет ни черновика заявки, ни записи каталога — "
            "заготовку предполётной проверки строить не из чего")
    return growth.draft_candidate(
        section=str(rec.get("section") or "families"),
        record_id=str(rec.get("record_id") or draft.get("id") or ""),
        draft=draft,
        request_id=str(rec.get("request_id") or ""),
        cr_key=str(cr.get("id") or ""),
        note="заготовка предполётной проверки задания агенту: "
             "исполнителей ещё нет")


def preflight_catalog_cr(cr: dict, *, repo=None, py: str | None = None,
                         ev=None, tmp=None, with_tests: bool = True) -> dict:
    """Предполётная проверка задания агенту — ДО его запуска.

    Возврат — запись для строки CR: `{at, verdict, findings, gaps,
    words, judges, record, candidate}`. Вердикты: `ok` — судьи молчат;
    `expected-gaps` — судьи назвали ровно то, что обязан закрыть агент;
    `judge-assumption` — судья сломался либо пожаловался, не назвав
    записи (он написан на неподвижный каталог), ЛИБО заготовка негодна
    по читателю; строка обязана уйти «нужен человек» ДО агента, и слова
    вердикта различают эти два случая.

    Функция самостоятельна: её зовут и из реализации, и отдельно — по
    строке CR без единой правки дерева."""
    from pathlib import Path

    from template_catalog import candidates as tcc
    from template_catalog import growth
    from template_catalog.export import dump_json
    if ev is None:
        from . import evolution_runner as ev          # noqa: PLC0415
    repo = Path(repo) if repo is not None else ev.test_dir()
    py = py or ev._venv_python(repo)
    cand = preflight_draft(cr)
    section = str(cand.get("section"))
    rid = str(cand.get("id"))
    addr = f"{section}/{rid}"
    kinds = [str(x.get("kind"))
             for x in ((cr.get("spec") or {}).get("executors_requested") or ())
             if x.get("kind")]
    record = cand.get("record") or {}
    gaps = growth.expected_gaps(record, section)
    if tmp is None:
        tmp = repo / "_repro" / f"preflight-{cr.get('id')}"
    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    cand_path = tmp / "candidate.json"
    cand_path.write_bytes(dump_json(cand).encode("utf-8"))
    out = {"at": _now(), "verdict": PREFLIGHT_OK, "findings": [],
           "gaps": gaps, "judges": [], "record": addr,
           "candidate": str(cand_path), "words": ""}
    catalog_dir = repo / "catalog"
    if not catalog_dir.is_dir():
        catalog_dir = registry.REPO_ROOT / "catalog"
    try:
        tcc.materialize(catalog_dir, cand, out_dir=tmp / "catalog")
    except Exception as exc:                             # noqa: BLE001
        out["verdict"] = PREFLIGHT_ASSUMPTION
        out["findings"] = [{"judge": "materialize", "kind": "assumption",
                            "words": f"временный каталог с заготовкой не "
                                     f"собрался: {exc}"}]
        out["words"] = out["findings"][0]["words"]
        return out
    ctx = {"folder": tmp / "catalog", "repo": repo, "py": py, "ev": ev,
           "cand": cand, "cand_path": cand_path, "record": record,
           "section": section, "rid": rid, "addr": addr, "kinds": kinds,
           "tmp": tmp, "gaps": gaps,
           "env": {growth.MODE_ENV: str(cand_path)}}
    findings: list = []
    for name, judge in PREFLIGHT_JUDGES.items():
        if name == "tests" and not with_tests:
            out["judges"].append({"judge": name, "words": "не прогонялся"})
            continue
        try:
            words = judge(ctx) or []
        except Exception as exc:                         # noqa: BLE001
            findings.append({"judge": name, "kind": "assumption",
                             "words": f"судья сломался: "
                                      f"{type(exc).__name__}: {exc}"})
            out["judges"].append({"judge": name, "words": "сломался"})
            continue
        # Судья, которому судить было НЕЧЕМ, говорит это словами
        # (`<имя>_words` в среде): пропуск молчанием читался бы как
        # «возражений нет».
        out["judges"].append({
            "judge": name,
            "words": ctx.get(f"{name}_words") or f"находок {len(words)}"})
        for w in words:
            findings.append({"judge": name, "kind": _pf_kind(w, rid, addr),
                             "words": str(w)[:400]})
    out["findings"] = findings
    # Человек нужен и при допущении судьи, и при негодной заготовке:
    # первое чинится в коде судьи, второе — в заявке, и ни то, ни другое
    # не чинит агент. Вердикт у обоих один, а слова — разные.
    if any(f["kind"] != "gap" for f in findings):
        out["verdict"] = PREFLIGHT_ASSUMPTION
    elif findings:
        out["verdict"] = PREFLIGHT_GAPS
    out["words"] = _pf_words(out)
    return out


def _pf_kind(words: str, rid: str, addr: str) -> str:
    """Находка судьи: ожидаемый пробел, негодная заготовка или
    допущение?

    * `gap` — судья НАЗВАЛ запись по имени и говорит о том, чего у
      заготовки нет по замыслу: исполнителя и стендовых задач. Сказать
      это — работа судьи, а закрыть — работа агента.
    * `draft` — ОШИБКА читателя о самой записи: заготовка негодна, и
      посылать по ней агента незачем.
    * `assumption` — судья пожаловался, записи НЕ назвав, либо сломался:
      он написан на неподвижный каталог (жёсткое число записей,
      рукописный перечень семей). Это допущение, и чинится оно в коде
      судьи."""
    text = str(words or "")
    свой = (rid and rid in text) or (addr and addr in text)
    if not свой:
        return "assumption"
    return "draft" if text.startswith("error ") else "gap"


def _pf_words(report: dict) -> str:
    """Итог предполётной проверки словами — для карточки строки CR."""
    findings = report.get("findings") or []
    gaps = [f for f in findings if f.get("kind") == "gap"]
    bad = [f for f in findings if f.get("kind") == "assumption"]
    draft = [f for f in findings if f.get("kind") == "draft"]
    if report["verdict"] == PREFLIGHT_ASSUMPTION:
        начало = ("предполётная проверка нашла ДОПУЩЕНИЕ судей (судья "
                  "написан на неподвижный каталог): " if bad else
                  "предполётная проверка: заготовка записи НЕГОДНА — "
                  "посылать по ней агента незачем: ")
        return (начало
                + "; ".join(f"{f['judge']} — {f['words']}"
                            for f in (bad or draft)[:5])
                + f"; ожидаемых пробелов {len(gaps)}")
    if report["verdict"] == PREFLIGHT_GAPS:
        return ("предполётная проверка: судьи назвали только ожидаемые "
                "пробелы заготовки (их закрывает агент): "
                + "; ".join(f["words"] for f in gaps[:5]))
    return "предполётная проверка: судьи возражений не нашли"


def _preflight_block(cr: dict) -> str:
    """Ожидаемые пробелы заготовки — в задание агенту: «что обязано
    закрыться». Их назвали судьи ДО работы агента, и вернуться зелёными
    те же судьи обязаны после неё."""
    pf = cr.get("preflight")
    if not isinstance(pf, dict):
        return ""
    lines = [f"- {f['words']}" for f in (pf.get("findings") or ())
             if f.get("kind") == "gap"]
    lines += [f"- {w}" for w in (pf.get("gaps") or ())]
    if not lines:
        return ""
    return ("ЧТО ОБЯЗАНО ЗАКРЫТЬСЯ (предполётная проверка прогнала судей по "
            "ЗАГОТОВКЕ записи ДО вас; это их вердикт о том, чего у записи "
            "ещё нет — после вашей работы те же судьи обязаны молчать):\n"
            + "\n".join(dict.fromkeys(lines)) + "\n")


def _name_status(text: str) -> list:
    """Разбор `git diff --cached --name-status` в пары (буква, путь).

    У строк переименования (`R…`, `C…`) путей ДВА, и судится каждый:
    исходный — как удаление, целевой — как заведение. Иначе перенос
    судьи (`tests/test_<судья>.py`) в разрешённую папку под именем,
    которого pytest не собирает, проходил бы белый список молча —
    судья исчезал, а гейт этого не видел. Вызов идёт с
    `--no-renames`, и эта ветка — вторая линия."""
    out = []
    for line in (text or "").splitlines():
        parts = [p for p in line.split("\t") if p.strip()]
        if len(parts) < 2:
            continue
        letter = parts[0].strip()[0]
        if letter in ("R", "C") and len(parts) >= 3:
            out.append(("D", parts[1].strip().replace("\\", "/")))
            out.append(("A" if letter == "C" else "R",
                        parts[-1].strip().replace("\\", "/")))
            continue
        out.append((letter, parts[-1].strip().replace("\\", "/")))
    return out


def _kotlin_dir_problems(status: str, path: str) -> str | None:
    """Правила папок Kotlin, куда дозволены ТОЛЬКО новые файлы (§2).

    Правка существующего файла тестов решателя — путь самосертификации
    (0.4): добавленная аннотация расширения JUnit гасит утверждения
    целого класса, поэтому судится статус, а не содержание."""
    from . import catalog_pipeline as cp
    if path.startswith("kotlin-solver/src/test/"):
        if status != "A":
            return (f"{path}: существующий файл тестов решателя правится "
                    f"агентом (дозволены только новые файлы)")
    if path.startswith("kotlin-solver/src/test/kotlin/"):
        if not path.endswith("Test.kt"):
            return (f"{path}: файл тестов решателя обязан называться "
                    f"<Имя>Test.kt — иначе surefire его не исполнит")
    elif path.startswith("kotlin-solver/src/test/resources/"):
        name = path.rsplit("/", 1)[-1]
        tail = path[len("kotlin-solver/src/test/resources/"):]
        if (name in cp.KOTLIN_TEST_RESOURCE_DENY
                or any(tail.startswith(x)
                       for x in cp.KOTLIN_TEST_RESOURCE_DENY)
                or (name.endswith(".properties")
                    and any(name.startswith(x)
                            for x in cp.KOTLIN_TEST_RESOURCE_DENY_PREFIX))):
            return (f"{path}: этим ресурсом гасится весь прогон тестов "
                    f"решателя — заводить его нельзя")
    elif path.startswith(cp.KOTLIN_SOLVER_DIR):
        if status != "A":
            return (f"{path}: файл решателя вне точного белого списка "
                    f"правится агентом (дозволены только новые файлы)")
    return None


def _catalog_path_problems(cr: dict, changes: list) -> list[str]:
    """Белый список путей: каждый изменённый путь обязан начинаться с
    разрешённого начала, не попадать под сторожа очереди и под перечень
    запретов задания; под tests/ — только tests/vmp/** и НОВЫЕ файлы
    tests/test_*.py.

    У CR с видами исполнителей решателя (0.8) к началам добавлены ТОЧНЫЙ
    перечень файлов Kotlin (`allowed_files`) и папки «только новые файлы»
    (`allowed_dirs`): измеряемое не может лежать в зоне правок
    инструмента измерения, поэтому перечень закрыт, а не начинается с
    `kotlin-solver/`."""
    work = cr.get("work") or {}
    allowed = tuple(work.get("allowed_paths") or ())
    allowed_files = frozenset(work.get("allowed_files") or ())
    allowed_dirs = tuple(work.get("allowed_dirs") or ())
    forbidden = tuple(work.get("forbidden_paths") or ())
    bad = []
    for status, path in changes:
        if _forbidden_paths([path]):
            bad.append(f"{path}: путь под сторожем очереди")
            continue
        if any(path.startswith(x) for x in forbidden):
            bad.append(f"{path}: судьи, гейты и чужие зоны агентом не "
                       f"правятся")
            continue
        if path in allowed_files:
            # Точный белый список файлов (0.8). Удаление таблицы или
            # исполнителя решателя белым списком не разрешено: правка —
            # да, снос — нет.
            if status == "D":
                bad.append(f"{path}: файл таблиц решателя удалён — белый "
                           f"список открывает правку, а не снос")
            continue
        if any(path.startswith(x) for x in allowed_dirs):
            words = _kotlin_dir_problems(status, path)
            if words:
                bad.append(words)
            continue
        if not any(path.startswith(x) for x in allowed):
            bad.append(f"{path}: путь вне белого списка задания")
            continue
        if path.startswith("tests/") and status != "A":
            # Просьба В-1 пакета паритета: под `tests/` — включая
            # `tests/vmp/` — агенту дозволены только НОВЫЕ файлы. В
            # `tests/vmp/` живут судьи, на которых стоит `implemented`:
            # гейт п. 13 «ничего не потеряно» и опись `LOST_WITH_REASON`
            # (`tests/vmp/test_bindings.py`), проекции снимка заморозки
            # (`test_template_registry_wrapper.py`). Условие
            # `pytest == "green"` в клоне ничего не доказывало бы, если
            # правку судьи вносит тот, кого он судит; своё агент пишет
            # новыми файлами, и это ему открыто.
            bad.append(f"{path}: изменён существующий тест — судьи "
                       f"правятся не тем, кого они судят (новые файлы "
                       f"тестов агенту открыты)")
    return bad


def _diff_hunks(text: str) -> list:
    """Разбор `git diff -U0` в (файл, номер строки, добавленная строка).

    Номер — на НОВОЙ стороне: судьи диапазона и токенов говорят агенту
    то место, которое он видит у себя в файле.

    Имя файла берётся и без начала `b/`: настройка git `diff.noprefix`
    (или `diff.mnemonicPrefix`) печатает заголовок без него, и разбор,
    знающий одно написание, оставлял бы судью токенов без единой
    судимой строки — гейт молчал бы, не судив. Вызовы задают начала
    ключами `--src-prefix/--dst-prefix`, и эта ветка — вторая линия;
    строка, чей файл назвать не удалось, отдаётся с `path = None` и
    судится как красная."""
    out, path, line = [], None, 0
    for raw in (text or "").splitlines():
        if raw.startswith("+++ "):
            name = raw[len("+++ "):].strip()
            if name == "/dev/null":
                path = None
                continue
            if name.startswith("b/") or name.startswith("i/") \
                    or name.startswith("w/"):
                name = name.split("/", 1)[1]
            path = name.replace("\\", "/") or None
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            line = int(m.group(1)) if m else 0
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            out.append((path, line, raw[1:]))
            line += 1
    return out


def _block_bounds(text: str, marker: str, where: str) -> tuple:
    """Границы блока-объявления в тексте: (начало, конец, строка, строка).

    Якорь — ТО ЖЕ объявление, по которому блок читает `kotlin_tables`
    (`val <ИМЯ> … mapOf(`), а не первое вхождение слова: упоминание
    имени в заметке выше объявления сдвигало начало блока к этой
    заметке, и весь заголовок файла оказывался «внутри блока».
    Второе объявление того же имени — отказ словами: какое из них блок,
    неизвестно."""
    from vmp import kotlin_tables as kt
    anchor = re.compile(r"(?m)^[ \t]*(?:\w+[ \t]+)*val[ \t]+"
                        + re.escape(marker) + r"\b")
    found = list(anchor.finditer(text))
    if not found:
        raise ValueError(f"объявления `val {marker}` в {where} нет")
    if len(found) > 1:
        raise ValueError(f"объявлений `val {marker}` в {where} несколько "
                         f"({len(found)})")
    head = found[0].start()
    open_at = text.find("mapOf(", head)
    if open_at < 0 or text.count("\n", head, open_at) > 2:
        raise ValueError(f"после `val {marker}` в {where} нет `mapOf(`")
    open_at += len("mapOf(")
    inner = kt.block(text, text[head:open_at], where=where)
    end = open_at + len(inner) + 1
    return (head, end,
            text.count("\n", 0, head) + 1, text.count("\n", 0, end) + 1)


def _first_diff_line(a: str, b: str) -> int:
    """Номер строки, с которой два текста расходятся (для слов гейта)."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return a.count("\n", 0, i) + 1


def _kotlin_hunk_problems(ev, repo, cr: dict, changes: list) -> list[str]:
    """Судья ДИАПАЗОНА: файл, открытый агенту одним блоком (сегодня это
    `TaskModes.kt` с `METRIC_EXECUTORS`), правится только внутри блока.

    Границы блока считаются по тексту `main`, а текст ВНЕ блока
    сверяется побайтно: `main[:начало]` обязан быть началом файла ветки,
    `main[конец:]` — его концом. Разбор добавленных строк диффа для
    этого не годится трижды: границы, взятые по тексту ветки, агент
    двигает двумя строками заметки (`/* METRIC_EXECUTORS mapOf( */` …
    `/* ) */` — и «внутри блока» оказывается весь файл); чистое удаление
    строк вне блока не даёт ни одной добавленной строки и не судится
    вовсе; упоминание имени выше объявления сдвигает начало блока.
    Побайтная сверка закрывает все три случая разом."""
    from . import catalog_pipeline as cp
    bad = []
    for path, marker in cp.KOTLIN_BLOCK_ONLY.items():
        if not any(p == path for _s, p in changes):
            continue
        rc, base = ev._git(repo, "show", f"main:{path}")
        if rc != 0:
            bad.append(f"{path}: текст файла на main не прочитан — с чем "
                       f"сверять правку вне {marker}, неизвестно")
            continue
        try:
            text = (repo / path).read_text(encoding="utf-8",
                                           errors="replace")
        except OSError as exc:
            bad.append(f"{path}: файл ветки не читается ({exc}) — правка "
                       f"вне блока неотличима от правки внутри")
            continue
        try:
            start, end, first, last = _block_bounds(base, marker, path)
        except Exception as exc:                        # noqa: BLE001
            bad.append(f"{path}: блок {marker} на main не найден ({exc}) — "
                       f"правка вне блока неотличима от правки внутри")
            continue
        prefix, suffix = base[:start], base[end:]
        if not text.startswith(prefix):
            bad.append(f"{path}: правка вне {marker} — текст ДО блока "
                       f"отличается от main (первое расхождение — строка "
                       f"{_first_diff_line(prefix, text)}; блок на main — "
                       f"строки {first}–{last})")
        elif not text.endswith(suffix):
            tail_line = (text.count("\n", 0, max(len(text) - len(suffix), 0))
                         + 1)
            bad.append(f"{path}: правка вне {marker} — текст ПОСЛЕ блока "
                       f"отличается от main (около строки {tail_line}; "
                       f"блок на main — строки {first}–{last})")
    return bad


def _kotlin_token_problems(ev, repo, cr: dict, changes: list) -> list[str]:
    """Судья ТОКЕНОВ: в добавленных строках любого файла решателя нет
    чтения среды, диска и сети мимо пакета постановки и нет объявления
    ручки HTTP (0.8). Это граница, а не суждение о содержании.

    Судятся ОБА корня — исполнители (`src/main/kotlin/**`) и тесты
    (`src/test/**`). Тесты решателя исполняет surefire под учётной
    записью службы, в scratch-копии внутри клона и ДО остальных судей:
    один тест с записью файла переписал бы стенд, живой прогон и pytest
    того же клона — «измеряемое не может лежать в зоне правок
    инструмента измерения» (0.8) относится и к ним.

    Написание судится подстрокой И выражением: подстрока ловит
    `RandomAccessFile(`, выражение — `File (`, `System.getenv ()` и
    аннотацию полным именем (`@jakarta.ws.rs.Path(`); отдельно судится
    ИМПОРТ — его обойти труднее, чем вызов."""
    from . import catalog_pipeline as cp
    roots = tuple(getattr(cp, "KOTLIN_TOKEN_ROOTS",
                          ("kotlin-solver/src/main/kotlin/",)))
    roots = tuple(r for r in roots
                  if any(p.startswith(r) for _s, p in changes))
    if not roots:
        return []
    bad = []
    for root in roots:
        # Начала имён задаются явно: при настройке git `diff.noprefix`
        # заголовок печатается без `b/`, и разбор без имени файла
        # оставил бы судью без единой судимой строки.
        _rc, diff = ev._git(repo, "-c", "core.quotePath=false", "diff",
                            "--cached", "-U0", "--src-prefix=a/",
                            "--dst-prefix=b/", "--", root)
        for path, line, text in _diff_hunks(diff):
            if path is None:
                bad.append("судья токенов не назвал файла добавленной "
                           "строки (git печатает заголовок без начала "
                           "b/): суд по добавленным строкам не состоялся")
                break
            if not path.startswith(root):
                continue
            said = None
            for token, pattern in cp.KOTLIN_TOKEN_PATTERNS:
                if token in text or pattern.search(text):
                    said = token
                    break
            if said is None and cp.KOTLIN_IMPORT_RE.match(text):
                said = text.strip()[:60]
            if said:
                bad.append(
                    f"исполнитель читает среду, диск или сеть мимо пакета "
                    f"/ объявляет ручку HTTP ({said}): {path}:{line}")
            if len(bad) >= 10:
                break
        if len(bad) >= 10:
            break
    return bad


#: Файлы, которые СУДЯТ исполнителей, а не исполняют записи каталога:
#: стенд, его адаптеры и живой прогон решателя. Они входят в CODE_FILES
#: (их дрейф делает результаты стенда устаревшими), но объявить в них
#: исполнителя — значит объявить судью исполнителем (0.8, находка Г-2).
_JUDGE_FILES = ("vmp/stand_tasks.py", "vmp/stand_adapters.py",
                "vmp/stand_solver.py")


def _symbol_gate_files() -> set:
    """Пять файлов гейта символов: только в них живут исполнители
    решателя. Перечень берётся у САМОГО гейта — второго закона о том, где
    искать символ строки посева, в дереве нет."""
    from vmp import bindings_gate as bg
    return {bg.PROVIDER_FILE, bg.RUN_THRESHOLDS_FILE, bg.TASK_PACKAGE_FILE,
            bg.LOGIC_FORMS_FILE, bg.TASK_MODES_FILE}


def _catalog_seed_problems(repo, cr: dict) -> list[str]:
    """Строки посева этого CR: у каждой строки «есть» файл обязан быть
    из перечня охраняемых — иначе исполнителя не видят ни стенд, ни
    сторож дрейфа.

    У видов решателя перечень другой (0.8): пять файлов гейта символов.
    Судьи (`vmp/stand_tasks.py`, `stand_adapters.py`, `stand_solver.py`)
    вычитаются из охраняемых: файл, которым судят, исполнителем записи
    объявлен быть не может."""
    rec = cr.get("record") or {}
    addr = f"{rec.get('section')}/{rec.get('record_id')}"
    path = repo / "vmp" / "bindings_seed.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8",
                                        errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"vmp/bindings_seed.json не читается: {exc}"]
    rows = ((doc.get("records") or {}).get(addr)) or []
    known = _executor_files() - set(_JUDGE_FILES)
    solver_files = _symbol_gate_files()
    bad = []
    for row in rows:
        if not isinstance(row, dict) or row.get("state") != "есть":
            continue
        kind = str(row.get("kind") or "")
        f = str(row.get("file") or "").replace("\\", "/")
        if kind.startswith("kotlin_"):
            if f not in solver_files:
                bad.append(f"{addr}: исполнитель {kind} объявлен в "
                           f"{f or '(без файла)'} — файл вне перечня гейта "
                           f"символов (пять файлов таблиц решателя)")
        elif f in _JUDGE_FILES:
            bad.append(f"{addr}: исполнитель {kind} объявлен в {f} — это "
                       f"судья, а не исполнитель записи")
        elif f not in known:
            bad.append(f"{addr}: исполнитель {kind} объявлен в "
                       f"{f or '(без файла)'} — файл вне перечня охраняемых")
    if not rows:
        bad.append(f"строк посева у записи {addr} нет: привязки "
                   f"исполнителя не объявлены")
    return bad


def _catalog_chain(ev, repo, py: str, head: str,
                   solver_url: str | None = None) -> str:
    """Цепочка на ЖИВОМ каталоге клона: результаты стенда и привязки
    после правок исполнителя иначе остаются устаревшими по построению.

    Стенд идёт с адресом ЭФЕМЕРНОГО решателя (0.2): рабочий файл
    результатов несёт живые строки видов решателя, и прогон без решателя
    переписал бы их `skipped`, сняв `stand_verified` у каждой записи —
    свидетельство всего каталога не должно понижаться от того, что
    исполнитель дописан другой записи."""
    at = datetime.now().astimezone().isoformat(timespec="seconds")
    stand = [py, "-m", "vmp.stand_tasks", "--write-results",
             "vmp/stand_results.json", "--at", at, "--commit", head]
    if solver_url:
        stand += ["--with-solver", solver_url]
    steps = (
        (stand, "vmp.stand_tasks"),
        ([py, "-m", "vmp.bindings_build", "--out", "vmp/bindings.json",
          "--stand-results", "vmp/stand_results.json", "--commit", head,
          "--generated-at", at], "vmp.bindings_build"),
        ([py, "-m", "vmp.bindings_gate", "vmp/bindings.json"],
         "vmp.bindings_gate"),
    )
    for cmd, name in steps:
        rc, out = ev._run(cmd, cwd=repo, timeout_s=CATALOG_CMD_TIMEOUT_S,
                          scrub_prefixes=ev._GATE_ENV_SCRUB)
        if rc != 0:
            return f"{name}: код {rc} — {(out or '')[-300:]}"
    return "ok"


def _stand_counts(path, addr: str | None = None) -> dict:
    """Итог прогона стенда по файлу результатов (§5.5 шага 4).

    Файл результатов — свидетельство ВСЕГО каталога: сузить прогон и
    записать файл стенд отказывается словами (`--records` вместе с
    `--write-results`, `vmp/stand_tasks.py`, приложение схемы п. 24).
    Поэтому гейт прогоняет временный каталог целиком, а СУД ПО ЗАПИСИ
    делает здесь фильтр строк `record == addr`: числа и виды — про
    запись кандидата, `all` рядом — про весь временный каталог (красная
    строка у соседней записи тоже беда: временный каталог несёт правку
    словарей и порядка семей).

    Красная строка, отмеченная стендом как известная (`KNOWN_FAILED:` в
    `note`), считается ОТДЕЛЬНЫМ счётчиком (0.7): запись владельца в
    перечень известных красных строк не должна ни разблокировать гейт
    молча, ни краснить его второй раз.
    """
    def _fresh():
        return {"passed": 0, "failed": 0, "skipped": 0, "known_failed": 0}
    out = {"passed": 0, "failed": 0, "skipped": 0, "known_failed": 0,
           "rows": [], "kinds": {}, "rows_full": [], "index": {},
           "all": {"passed": 0, "failed": 0, "skipped": 0,
                   "known_failed": 0, "rows": [], "kinds": {}},
           "solver": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out["rows"] = [f"файл результатов стенда не читается: {exc}"]
        out["words"] = f"файл результатов стенда не читается: {exc}"
        return out
    out["solver"] = doc.get("solver") or {}
    for row in doc.get("results") or ():
        if not isinstance(row, dict):
            continue
        res = row.get("result")
        kind = str(row.get("executor_kind"))
        record = str(row.get("record"))
        known = (res == "failed"
                 and "KNOWN_FAILED:" in str(row.get("note") or ""))
        res_key = "known_failed" if known else res
        words = f"{record} · {kind} · {row.get('task')} — {res}"
        if res in ("passed", "failed", "skipped"):
            out["all"][res_key] += 1
            out["all"]["kinds"].setdefault(kind, _fresh())
            out["all"]["kinds"][kind][res_key] += 1
            out["index"][(record, str(row.get("task")), kind)] = res
        if res == "failed" and not known and len(out["all"]["rows"]) < 20:
            out["all"]["rows"].append(words)
        if addr is not None and record != addr:
            continue
        if res in ("passed", "failed", "skipped"):
            out[res_key] += 1
        out["kinds"].setdefault(kind, _fresh())
        if res in ("passed", "failed", "skipped"):
            out["kinds"][kind][res_key] += 1
        if len(out["rows"]) < 20:
            out["rows"].append(words)
        out["rows_full"].append(row)
    return out


def _main_stand_counts(ev, repo) -> dict:
    """Строки стенда на `main` клона — мера, против которой считается
    регрессия (0.7). Файла нет — пустая мера и слова: суд по числу тогда
    не состоится, и об этом говорится, а не молчится."""
    rc, out = ev._git(repo, "show", "main:vmp/stand_results.json")
    if rc != 0:
        return {"kinds": {}, "index": {},
                "words": "на main нет vmp/stand_results.json: мера "
                         "регрессии по соседям не с чем сравнить"}
    try:
        doc = json.loads(out)
    except json.JSONDecodeError as exc:
        return {"kinds": {}, "index": {},
                "words": f"vmp/stand_results.json с main не читается: {exc}"}
    kinds: dict = {}
    index: dict = {}
    for row in doc.get("results") or ():
        if not isinstance(row, dict):
            continue
        res = row.get("result")
        kind = str(row.get("executor_kind"))
        if res not in ("passed", "failed", "skipped"):
            continue
        known = (res == "failed"
                 and "KNOWN_FAILED:" in str(row.get("note") or ""))
        kinds.setdefault(kind, {"passed": 0, "failed": 0, "skipped": 0,
                                "known_failed": 0})
        kinds[kind]["known_failed" if known else res] += 1
        index[(str(row.get("record")), str(row.get("task")), kind)] = res
    return {"kinds": kinds, "index": index, "words": None}


def _bindings_rows(path, addr: str) -> dict:
    """Строки привязок записи из сгенерированного файла: {вид: строка}."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    section, _, rid = addr.partition("/")
    for rec in doc.get("bindings") or ():
        if str(rec.get("section")) == section and str(rec.get("id")) == rid:
            return {str(x.get("kind")): x for x in (rec.get("executors")
                                                    or ())
                    if isinstance(x, dict)}
    return {}


#: Поле ожидания, которым назван ПРЕДМЕТ строки посева у видов, которые
#: судит живой стенд (0.6): суд «по виду» пропустил бы нового
#: исполнителя по чужой зелёной задаче того же вида.
_SUBJECT_OF_KIND = {"kotlin_constraint": "constraint",
                    "kotlin_logic_form": "form"}

#: Виды, у которых своего судьи на стенде нет и предметом служит ПАРА
#: ЗАДАЧ-БЛИЗНЕЦОВ вида `kotlin_constraint` на той же записи (0.6).
_TWIN_KINDS = ("kotlin_knob", "kotlin_form")

#: Слова гигиены тестов Kotlin у CR без видов решателя: тесты решателя
#: не прогоняются, потому что исходники решателя тождественны `main`.
KOTLIN_TESTS_SKIPPED = "не прогонялись: видов решателя нет"

#: То же о самих тестах решателя в блоке сборки (§4, §1 «CR без
#: kotlin-видов»).
SOLVER_TESTS_SKIPPED = ("не прогонялись: исходники решателя тождественны "
                        "main")

#: Класс правки решателя (0.9): механическое имя того, что случилось с
#: таблицами решателя. Путь до `implemented` открыт обоим классам —
#: рост судит ЗАКОН паритета на временном каталоге «живой ⊕ кандидат»
#: (пакет «паритет на временном каталоге»), а не имя класса.
SOLVER_CLASS_EXEC = "К-исп"
SOLVER_CLASS_TABLES = "К-табл"
SOLVER_CLASS_NONE = "без правок решателя"


def _flat_world(value, prefix: str = "") -> dict:
    """Мир задачи «путь → значение»: сравнение близнецов идёт по полям, а
    не по тексту (порядок ключей в файле — не различие)."""
    out: dict = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(_flat_world(item, f"{prefix}.{key}" if prefix
                                   else str(key)))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            out.update(_flat_world(item, f"{prefix}[{i}]"))
    else:
        out[prefix] = value
    return out


def _world_diff(a: dict, b: dict) -> list:
    """Пути, которыми различаются два мира."""
    fa, fb = _flat_world(a or {}), _flat_world(b or {})
    return sorted({k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k)})


def _subject_problems(kind: str, seed: dict, rows: list) -> tuple:
    """Суд ПО ПРЕДМЕТУ строки посева и по РАЗЛИЧАЮЩИМ задачам (0.6).

    Возврат: (слова или None, сводка по предмету). Правило одно для всех
    видов, которые судит живой стенд: задач по предмету — не меньше двух,
    все зелёные, и среди ожиданий есть ноль срабатываний и ненулевое.
    Одна зелёная задача проходима всегда-истинным ограничением с
    ожиданием, списанным с его же вывода."""
    field = _SUBJECT_OF_KIND[kind]
    want = str((seed or {}).get(field) or "")
    if not want:
        return (f"{kind}: строка посева не называет предмет ({field}) — "
                f"судить нечего", {})
    mine = [r for r in rows
            if str(r.get("executor_kind")) == kind
            and str((r.get("expected") or {}).get(field) or "") == want]
    summary = {"subject": want, "passed": 0, "failed": 0,
               "matches_values": []}
    for row in mine:
        if row.get("result") == "passed":
            summary["passed"] += 1
        elif row.get("result") == "failed":
            summary["failed"] += 1
        summary["matches_values"].append(
            (row.get("expected") or {}).get("matches"))
    if not mine:
        return (f"{kind}: нет задач по предмету «{want}» строки посева — "
                f"зелёная задача чужого предмета того же вида "
                f"исполнителя не судит", summary)
    if len(mine) < 2:
        return (f"{kind}: по предмету «{want}» всего {len(mine)} задача — "
                f"нужны две с разными ожиданиями срабатываний", summary)
    if summary["failed"] or summary["passed"] != len(mine):
        return (f"{kind}: по предмету «{want}» зелёных {summary['passed']} "
                f"из {len(mine)} — красная или пропущенная строка суд не "
                f"закрывает", summary)
    values = [v for v in summary["matches_values"] if isinstance(v, int)]
    if not (any(v == 0 for v in values) and any(v >= 1 for v in values)):
        return (f"{kind}: у задач предмета «{want}» нет различающих "
                f"ожиданий (нужны ноль срабатываний и ненулевое, дано "
                f"{summary['matches_values']})", summary)
    return None, summary


def _metric_problems(seed: dict, rows: list, addr: str) -> tuple:
    """Метрика = запись критерия (0.6): достаточно одной зелёной задачи с
    ненулевым числом срабатываний, а при отказе метрики в таблице
    решателя — задачи с `refusal: true` (слова отказа сверяет стенд)."""
    mine = [r for r in rows if str(r.get("executor_kind")) == "kotlin_metric"]
    summary = {"subject": str((seed or {}).get("metric") or addr),
               "passed": sum(1 for r in mine if r.get("result") == "passed"),
               "failed": sum(1 for r in mine if r.get("result") == "failed"),
               "matches_values": [(r.get("expected") or {}).get("matches")
                                  for r in mine],
               "refusals": sum(1 for r in mine
                               if (r.get("expected") or {}).get("refusal"))}
    if not mine:
        return (f"kotlin_metric: у записи {addr} нет ни одной задачи "
                f"метрики — судить нечем", summary)
    if summary["failed"]:
        return (f"kotlin_metric: красных задач {summary['failed']} — суд "
                f"не закрыт", summary)
    good = [r for r in mine
            if r.get("result") == "passed"
            and (isinstance((r.get("expected") or {}).get("matches"), int)
                 and (r.get("expected") or {}).get("matches") >= 1
                 or (r.get("expected") or {}).get("refusal") is True)]
    if not good:
        return (f"kotlin_metric: нет зелёной задачи с ненулевым числом "
                f"срабатываний либо с ожидаемым отказом", summary)
    return None, summary


def _task_constraint(task: dict) -> str:
    """Имя ограничения, о котором задача (поле ожидания вида)."""
    return str(((task.get("expected") or {}).get("kotlin_constraint")
                or {}).get("constraint") or "")


def _twin_problems(kind: str, seed: dict, tasks: list, rows: list,
                   subject: str = "") -> tuple:
    """Близнецы (0.6) для видов без своего судьи на стенде.

    Пара задач `kotlin_constraint` на ТОЙ ЖЕ записи, миры которых
    отличаются ровно одним полем — значением параметра ручки либо
    секцией пакета, — а ожидания различаются числом срабатываний. Только
    так видно, что число из пакета доезжает до ограничения: гейт
    символов доказывает лишь существование метода.

    Пара привязана к ПРЕДМЕТУ: оба близнеца называют ОДНО ограничение, и
    если запись сеет и `kotlin_constraint`, то именно его. Иначе ручка
    «исполнялась» бы парой по чужому, давно зелёному ограничению, а
    новое ограничение эту ручку и не читало бы, — тот самый обход «по
    чужой зелёной задаче», который 0.6 закрыло для судимых видов.

    Перебираются ВСЕ пары: красное говорится, только когда ни одной
    годной пары нет. Кандидат — запись целиком со своими стендовыми
    задачами (§3.5), и близнец, сделанный копией существующей задачи,
    образует пару со своим оригиналом — «первая пара с равными
    ожиданиями» краснила бы работу, где годная пара тут же рядом."""
    param = str((seed or {}).get("param") or "")
    by_task = {str(r.get("task")): r for r in rows
               if str(r.get("executor_kind")) == "kotlin_constraint"}
    mine = [t for t in tasks
            if isinstance(t, dict)
            and "kotlin_constraint" in ((t.get("expected") or {}))]
    if subject:
        by_subject = [t for t in mine if _task_constraint(t) == subject]
        if len(by_subject) < 2:
            return (f"{kind}: у записи меньше двух задач kotlin_constraint "
                    f"по предмету «{subject}» — пара по чужому ограничению "
                    f"эту ручку (форму) не судит", None)
        mine = by_subject
    if len(mine) < 2:
        return (f"{kind}: у записи меньше двух задач вида "
                f"kotlin_constraint — пары близнецов нет", None)
    near: list = []
    almost: list = []
    for i, first in enumerate(mine):
        for second in mine[i + 1:]:
            if _task_constraint(first) != _task_constraint(second):
                # Пара про РАЗНЫЕ ограничения близнецами не бывает:
                # различие ожиданий сказало бы о разных ограничениях, а
                # не о числе, доехавшем до одного.
                continue
            diff = _world_diff(first.get("world"), second.get("world"))
            if len(diff) != 1:
                near.append((first.get("id"), second.get("id"), diff))
                continue
            path = diff[0]
            if kind == "kotlin_knob":
                want = f"params.{param}" if param else "params."
                ok_field = (path == want if param
                            else path.startswith("params."))
            else:
                ok_field = not path.startswith("params.")
            m1 = ((first.get("expected") or {})
                  .get("kotlin_constraint") or {}).get("matches")
            m2 = ((second.get("expected") or {})
                  .get("kotlin_constraint") or {}).get("matches")
            r1 = by_task.get(str(first.get("id"))) or {}
            r2 = by_task.get(str(second.get("id"))) or {}
            twin = {"kind": kind, "tasks": [first.get("id"),
                                            second.get("id")],
                    "constraint": _task_constraint(first),
                    "field": path, "matches": [m1, m2],
                    "results": [r1.get("result"), r2.get("result")]}
            if not ok_field:
                near.append((first.get("id"), second.get("id"), diff))
                continue
            if m1 == m2:
                almost.append(
                    (f"{kind}: миры задач {first.get('id')} и "
                     f"{second.get('id')} различаются полем {path}, а "
                     f"ожидания — нет: число из пакета до ограничения "
                     f"не судится", twin))
                continue
            if r1.get("result") != "passed" or r2.get("result") != "passed":
                almost.append(
                    (f"{kind}: близнецы {first.get('id')}/"
                     f"{second.get('id')} не зелены "
                     f"({r1.get('result')}/{r2.get('result')})", twin))
                continue
            return None, twin
    if almost:
        return almost[0]
    words = "; ".join(f"{a}/{b}: различий {len(d)} ({', '.join(d[:3])})"
                      for a, b, d in near[:3])
    field = ((f"параметром params.{param}" if param else "параметром ручки")
             if kind == "kotlin_knob" else "секцией пакета")
    return (f"{kind}: пары задач-близнецов нет — миры обязаны отличаться "
            f"ровно одним полем ({field}) при одном ограничении"
            + (f"; ближайшие пары — {words}" if words else ""), None)


def _regression_problems(counts: dict, main: dict) -> tuple:
    """Регрессия ЧИСЛОМ (0.7): по всему временному каталогу красных нет,
    зелёных строк каждого вида решателя не меньше, чем на `main`, и ни
    одна судимая строка не понижена до `skipped`.

    «Ноль красных» выполняется и при нуле судимых — поэтому мера числом,
    а не признаком."""
    words = []
    if main.get("words"):
        words.append(str(main["words"]))
    from vmp.stand_tasks import SOLVER_KINDS
    for kind in SOLVER_KINDS:
        was = (main.get("kinds") or {}).get(kind, {}).get("passed", 0)
        now = (counts["all"]["kinds"].get(kind) or {}).get("passed", 0)
        if now < was:
            words.append(f"вид {kind}: зелёных {now} против {was} на main "
                         f"— живое свидетельство каталога понижено")
    lost = [f"{rec} · {kind} · {task}"
            for (rec, task, kind), res in (main.get("index") or {}).items()
            if res in ("passed", "failed")
            and counts["index"].get((rec, task, kind)) == "skipped"]
    if lost:
        words.append(f"понижение до skipped у судимых на main строк: "
                     f"{'; '.join(sorted(lost)[:5])}")
    return ("; ".join(words) if words else None,
            {"main_by_kind": {k: v.get("passed", 0)
                              for k, v in (main.get("kinds") or {}).items()},
             "downgraded": len(lost)})


def catalog_gate_commands(py: str, repo, tmp, cand_path,
                          solver_url: str | None = None) -> dict:
    """Точные команды гейтов CR каталога — ОДНИМ местом.

    Гейт и контрактный тест берут argv отсюда: разойтись им негде, а
    смена ключей у стенда, генератора, гейта привязок или проверки
    кандидата краснит тест, а не молчит до первой живой реализации
    (столкновение `--records` + `--write-results` разошлось именно на
    этом шве — ревью волны 2). Адрес эфемерного решателя (0.2) едет
    стенду тем же ключом `--with-solver`, что и у самопроверки агента.

    Пятая команда — закон паритета (`catalog/tools/parity.py`, пакет
    «паритет на временном каталоге», §2.1): он судит ВРЕМЕННЫЙ каталог
    попытки его же привязками, поэтому идёт последним — и материализация,
    и `bindings.json` попытки к этому мигу уже есть."""
    from pathlib import Path
    tmp, repo = Path(str(tmp)), Path(str(repo))
    stand = [py, "-m", "vmp.stand_tasks", "--catalog",
             str(tmp / "catalog"), "--write-results", str(tmp / "stand.json")]
    if solver_url:
        stand += ["--with-solver", solver_url]
    return {
        # Стенд — на ВЕСЬ временный каталог: свидетельство целое, суд по
        # записи делает фильтр `_stand_counts(addr)`.
        "stand": stand,
        "bindings_build": [py, "-m", "vmp.bindings_build", "--out",
                           str(tmp / "bindings.json"), "--stand-results",
                           str(tmp / "stand.json")],
        # Второй путь — заведомо несуществующий файл CDPM: гейт судит
        # зону COSMOS, чужие привязки временного каталога не касаются.
        "bindings_gate": [py, "-m", "vmp.bindings_gate",
                          str(tmp / "bindings.json"),
                          str(tmp / "no-cdpm.json")],
        "candidate": [py, "-m", "template_catalog.candidates",
                      str(cand_path), "--catalog", str(repo / "catalog")],
        # Закон паритета — файлом инструмента, а не `-m`: `catalog/` —
        # данные, пакетом он не является (контракт §1). Вердикт едет
        # файлом `--json`: свидетельство CR — его КОПИЯ, а не пересказ
        # конвейером (риск 6 проекта).
        # `--candidate` называет движку файл кандидата, УЖЕ положенного в
        # судимую папку материализацией: сам он её не пересобирает, а
        # берёт адрес записи и номер заявки — без этого правило L21
        # «запись ≡ заявке» отвечало бы «кандидатов рядом с каталогом
        # нет» и единственный независимый якорь владельца (класс, вид,
        # зона, заголовок, предмет, параметры, `executor_kinds_requested`)
        # оставался бы непросуженным при зелёном вердикте.
        "parity": [py, str(repo / "catalog" / "tools" / "parity.py"),
                   "--catalog", str(tmp / "catalog"),
                   "--candidate", str(cand_path),
                   "--bindings", str(tmp / "bindings.json"),
                   "--json", str(tmp / "parity.json")],
    }


def _parity_verdict(path, rc: int, out: str) -> dict:
    """Вердикт закона паритета — КОПИЯ файла `--json` движка.

    Своего суждения о паритете у конвейера нет (риск 6 проекта): он
    читает вердикт целиком и кладёт его в свидетельство. Своё у
    конвейера ровно одно — честность связки «код возврата ↔ вердикт»:
    файла нет, он не разобран или вердикт не сходится с кодом возврата
    команды — красное словами, а не тихий зелёный. Ни один здешний
    случай не делает вердикт зеленее, чем он есть."""
    from pathlib import Path
    try:
        doc = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"rc": rc, "ok": False,
                "words": (f"вердикт паритета не прочитан ({exc}); команда "
                          f"вернула код {rc} — {(out or '')[-300:]}")}
    if not isinstance(doc, dict):
        return {"rc": rc, "ok": False,
                "words": (f"вердикт паритета не объект, а "
                          f"{type(doc).__name__}; код команды {rc}")}
    said, given = doc.get("ok"), doc.get("rc", rc)
    doc["rc"] = given
    if given != rc or (said is True) != (rc == 0):
        doc["ok"] = False
        doc["words"] = (
            f"вердикт паритета не сходится с кодом возврата команды: в "
            f"вердикте rc {given}, ok {said}, команда вернула {rc}"
            + ("; " + str(doc.get("words")) if doc.get("words") else ""))
        return doc
    # Второе своё у конвейера: правило L21 «запись ≡ заявке» обязано
    # СУДИТЬ. Оно единственное сверяет запись кандидата с независимым
    # якорем владельца — черновиком принятой заявки; всё остальное в
    # ветке пишет один агент. Пропуск здесь (кандидата движку не отдали,
    # заявки рядом нет) вердикт не красит сам по себе, и красным его
    # делает конвейер: «не судилось» — не «сошлось».
    l21 = ((doc.get("rules") or {}).get("L21") or {})
    if l21.get("ok") is None:
        doc["ok"] = False
        doc["words"] = (
            f"правило L21 «запись ≡ заявке» не судилось: "
            f"{l21.get('skipped') or 'слов пропуска нет'}; запись кандидата "
            f"осталась без независимого якоря владельца"
            + ("; " + str(doc.get("words")) if doc.get("words") else ""))
    return doc


def _solver_stand_report(ev, repo, cr: dict, cand: dict, counts: dict,
                         requested: list, addr: str) -> dict:
    """Живой суд видов решателя на временном каталоге (0.6, 0.7).

    Собирает сводку для свидетельства (`gates.solver.stand`) и слова
    красного гейта. Регрессия числом считается ВСЕГДА — она про весь
    временный каталог, а не про виды этого CR."""
    seeds = _seed_rows(repo, cr)
    tasks = ((cand.get("record") or {}).get("stand_tasks") or [])
    rows = counts.get("rows_full") or []
    solver_kinds = [k for k in requested if str(k).startswith("kotlin_")]
    # Строки стенда записи — ПАСПОРТ живого суда: задача, вид, исход,
    # ожидание, полученное, номер прогона, отпечаток пакета и адрес. Он
    # едет в ветку вместе с кандидатом (§4), поэтому в свидетельство
    # кладутся только строки видов решателя, а не весь прогон.
    header = counts.get("solver") or {}
    evidence_rows = [
        {"task": r.get("task"), "kind": r.get("executor_kind"),
         "result": r.get("result"), "expected": r.get("expected"),
         "got": r.get("got"), "run_id": r.get("run_id"),
         "package_fingerprint": header.get("package_fingerprint"),
         "url": header.get("url")}
        for r in rows if str(r.get("executor_kind") or "").startswith(
            "kotlin_")][:20]
    out: dict = {
        "by_kind": {k: (counts["kinds"].get(k) or {}) for k in solver_kinds},
        "by_subject": {}, "twins": [],
        "all_by_kind": counts["all"]["kinds"],
        "known_failed": counts["all"]["known_failed"],
        "rows": evidence_rows,
    }
    words = []
    for kind in solver_kinds:
        seed = seeds.get(kind) or {}
        if kind in _SUBJECT_OF_KIND:
            said, summary = _subject_problems(kind, seed, rows)
            out["by_subject"][f"{kind}:{summary.get('subject', '')}"] = summary
        elif kind == "kotlin_metric":
            said, summary = _metric_problems(seed, rows, addr)
            out["by_subject"][f"{kind}:{summary.get('subject', '')}"] = summary
        elif kind in _TWIN_KINDS:
            # Предмет пары — ограничение строки посева `kotlin_constraint`
            # той же записи, когда запись сеет и его: ручка судится
            # ограничением, которое её читает, а не любым зелёным.
            subject = str((seeds.get("kotlin_constraint") or {})
                          .get("constraint") or "")
            said, twin = _twin_problems(kind, seed, tasks, rows, subject)
            if twin:
                out["twins"].append(twin)
        else:                                            # pragma: no cover
            said = f"{kind}: судьи живого стенда у вида нет"
        if said:
            words.append(said)
    said, regression = _regression_problems(counts,
                                            _main_stand_counts(ev, repo))
    out.update(regression)
    if said:
        words.append(said)
    out["words"] = "; ".join(words) if words else None
    return out


def _catalog_gates(ev, repo, py: str, cr: dict, tmp=None,
                   solver_url: str | None = None) -> dict:
    """Гейты на ВРЕМЕННОМ каталоге «живой каталог клона ⊕ запись
    кандидата»: у новой записи в живом каталоге записи ещё нет, и стенд
    с генератором были бы красны по построению, а не по делу.

    Стенд прогоняется на этом каталоге ЦЕЛИКОМ (без `--records`): файл
    результатов — свидетельство всего каталога, и сужение прогона стенд
    отказывает словами (приложение схемы п. 24). Суд по записи —
    фильтром строк в `_stand_counts(addr)`; команды строит
    `catalog_gate_commands`, их же судит контрактный тест. Виды решателя
    судятся ЖИВЫМ прогоном (`--with-solver`) — по предмету строки посева,
    по различающим задачам и близнецам (0.6) — и регрессией числом по
    всему каталогу (0.7)."""
    from template_catalog import candidates as tc_candidates
    rec = cr.get("record") or {}
    spec = cr.get("spec") or {}
    work = cr.get("work") or {}
    record_id = str(rec.get("record_id") or "")
    addr = f"{rec.get('section')}/{record_id}"
    report: dict = {"stand": {"passed": 0, "failed": 0, "skipped": 0,
                              "rows": []},
                    "bindings_build": "не прогонялся",
                    "bindings_gate": "не прогонялся",
                    "catalog_validate_errors": None,
                    # Непрогнанный закон — не зелёный закон: пустое поле
                    # `_gates_green` считал бы за отсутствие возражений.
                    "parity": {"rc": None, "ok": False,
                               "words": "не прогонялся"},
                    "present_rows": []}
    cand_rel = str(work.get("candidate") or "")
    cand_path = repo / cand_rel
    if not cand_path.is_file():
        report["candidate"] = f"файла кандидата нет: {cand_rel}"
        return report
    # Папка ПОПЫТКИ, а не общая папка CR (0.11): иначе `stand.json`
    # прошлой попытки уехал бы в свидетельство этой.
    if tmp is None:
        tmp = repo / "_repro" / f"catalog-cr-{cr.get('id')}"
    tmp.mkdir(parents=True, exist_ok=True)
    report["tmp"] = str(tmp)
    try:
        cand = tc_candidates.load_candidate(cand_path)
        tc_candidates.materialize(repo / "catalog", cand,
                                  out_dir=tmp / "catalog")
    except Exception as exc:                             # noqa: BLE001
        report["candidate"] = f"кандидат не разобран: {exc}"
        return report
    report["candidate"] = "ok"
    env = {"CREW_TEMPLATE_CATALOG": str(tmp / "catalog")}
    cmds = catalog_gate_commands(py, repo, tmp, cand_path, solver_url)
    stand_path = tmp / "stand.json"
    rc, out = ev._run(cmds["stand"], cwd=repo,
                      timeout_s=CATALOG_CMD_TIMEOUT_S, env_extra=env,
                      scrub_prefixes=ev._GATE_ENV_SCRUB)
    counts = _stand_counts(stand_path, addr)
    report["stand"] = {k: counts[k] for k in ("passed", "failed", "skipped")}
    report["stand"]["rows"] = counts["rows"]
    report["stand"]["all"] = counts["all"]
    if rc != 0:
        report["stand"]["words"] = f"стенд: код {rc} — {(out or '')[-200:]}"
    elif counts["all"]["failed"]:
        # Временный каталог несёт не только запись кандидата: правка
        # словарей или порядка семей краснит соседей — молчать об этом
        # гейт не вправе.
        report["stand"]["words"] = (
            f"стенд временного каталога: failed {counts['all']['failed']} "
            f"по прочим записям — "
            f"{'; '.join(counts['all']['rows'][:3])}")
    requested = [x.get("kind") for x in (spec.get("executors_requested")
                                         or ())]
    deferred = [x.get("kind") for x in (spec.get("executors_deferred")
                                        or ())]
    from vmp.stand_tasks import PURE_KINDS
    for kind in requested:
        if kind not in PURE_KINDS:
            continue                       # судья стенда — только у чистых
        got = counts["kinds"].get(kind) or {}
        if got.get("failed") or not got.get("passed"):
            report["stand"].setdefault("words", "")
            report["stand"]["words"] = (
                (report["stand"].get("words") or "")
                + f" вид {kind}: passed {got.get('passed', 0)}, failed "
                  f"{got.get('failed', 0)} — нужна хотя бы одна зелёная "
                  f"строка без красных;").strip()
    # Виды решателя: суд по ПРЕДМЕТУ, различающим задачам и близнецам
    # (0.6) плюс регрессия числом по всему каталогу (0.7).
    report["solver_stand"] = _solver_stand_report(
        ev, repo, cr, cand, counts, requested, addr)
    if report["solver_stand"].get("words"):
        report["stand"]["words"] = (
            (report["stand"].get("words") or "")
            + " " + report["solver_stand"]["words"]).strip()
    bind_path = tmp / "bindings.json"
    rc, out = ev._run(cmds["bindings_build"], cwd=repo,
                      timeout_s=CATALOG_CMD_TIMEOUT_S, env_extra=env,
                      scrub_prefixes=ev._GATE_ENV_SCRUB)
    report["bindings_build"] = ("ok" if rc == 0
                                else f"код {rc} — {(out or '')[-200:]}")
    rows = _bindings_rows(bind_path, addr)
    words = []
    for kind in requested:
        row = rows.get(kind) or {}
        if row.get("state") == "есть":
            report["present_rows"].append(kind)
        else:
            words.append(f"{kind}: {row.get('state') or 'строки нет'} "
                         f"(ожидалось «есть»)")
    for kind in deferred:
        row = rows.get(kind) or {}
        if row.get("state") != "заявлен" or row.get("cr_key") != cr.get("id"):
            words.append(f"{kind}: ожидалась строка «заявлен» с ключом "
                         f"{cr.get('id')}, дано "
                         f"{row.get('state') or 'ничего'}/"
                         f"{row.get('cr_key')}")
    if words:
        report["rows_words"] = "; ".join(words)
    rc, out = ev._run(cmds["bindings_gate"], cwd=repo,
                      timeout_s=CATALOG_CMD_TIMEOUT_S, env_extra=env,
                      scrub_prefixes=ev._GATE_ENV_SCRUB)
    report["bindings_gate"] = ("ok" if rc == 0
                               else f"код {rc} — {(out or '')[-200:]}")
    rc, out = ev._run(cmds["candidate"], cwd=repo,
                      timeout_s=CATALOG_CMD_TIMEOUT_S,
                      scrub_prefixes=ev._GATE_ENV_SCRUB)
    report["catalog_validate_errors"] = 0 if rc == 0 else 1
    if rc != 0:
        report["catalog_validate_words"] = (out or "")[-300:]
    # Пятый гейт — ЗАКОН ПАРИТЕТА на временном каталоге (0.9): рост
    # таблиц решателя законен ровно тогда, когда его объявила запись
    # кандидата, и судит это закон, а не класс правки. Окружение — то
    # же, что у стенда и генератора: правило L10 сверяет двери
    # продуктов с судимым каталогом, и обе они читают временный.
    rc, out = ev._run(cmds["parity"], cwd=repo,
                      timeout_s=CATALOG_CMD_TIMEOUT_S, env_extra=env,
                      scrub_prefixes=ev._GATE_ENV_SCRUB)
    report["parity"] = _parity_verdict(tmp / "parity.json", rc, out)
    return report


def _gates_green(report: dict) -> bool:
    """Статус `implemented` — только при ВСЕХ условиях (0.16).

    Ни одно условие не заменяет другого: сборка без стенда — код,
    который не измерен; стенд без сборки — измерение не того кода;
    зелёные судьи без закона паритета — код, который не сверен с
    записью каталога."""
    stand = report.get("stand") or {}
    solver = report.get("solver") or {}
    build = solver.get("build") or {}
    run = solver.get("run") or {}
    # Решатель судится ПОЛЯМИ отчёта, а не словами: слова модуль пишет и
    # у зелёного прогона («эфемерный решатель поднят…»), и молчание в них
    # ничего не значит. Красные слова сборки — свои, их пишет судья
    # `_build_problems`, и там пустота осмысленна.
    solver_ok = (
        build.get("available") is True
        and not build.get("words")
        and bool(run.get("url"))
        and run.get("stopped") in ("terminated", "killed")
        # «Не проверено» — не «сошлись»: 0.16 требует для `implemented`
        # СВЕРШИВШЕЙСЯ сверки PID↔порт и свободного порта. `None` модуль
        # ставит, когда опрос слушающих сокетов не ответил, и пускать
        # такую строку в `implemented` значило бы выдавать непроверенную
        # личность решателя за проверенную.
        and run.get("port_free_after") is True
        and run.get("pid_matches_port") is True
        # Изменение артефакта за время стенда модуль записывает, а гейт
        # прежде не читал: строка с явным свидетельством подмены дерева
        # `quarkus-app/` доходила до `implemented`.
        and run.get("artifact_unchanged_after") is not False
        and not solver.get("tables_words")
        # Класс правки `implemented` больше не решает (пакет «паритет на
        # временном каталоге», 0.9): рост таблиц решателя судит ЗАКОН
        # паритета на временном каталоге «живой ⊕ кандидат» — условие
        # `report["parity"]["ok"]` ниже. Поле `solver["parity"]` осталось
        # ссылкой словами на этот вердикт и судьёй не является.
        and (solver.get("stand") or {}).get("words") in (None, "")
        and report.get("kotlin_tests") in (None, "ok", KOTLIN_TESTS_SKIPPED)
        and report.get("candidate_reload") == "ok")
    return (report.get("paths") == "ok"
            and report.get("executor_files") == "ok"
            and report.get("tree_guard") == "ok"
            # Свидетели закона паритета в коде исполнителя
            # (`vmp/run/roster_certify.py`) не тронуты работой агента:
            # иначе «природа записи по коду» самосертифицируема (В-2).
            and report.get("parity_witness") == "ok"
            and report.get("chain_live") == "ok"
            and report.get("pytest") == "green"
            and report.get("candidate") == "ok"
            and not stand.get("words")
            and not stand.get("failed")
            and report.get("bindings_build") == "ok"
            and report.get("bindings_gate") == "ok"
            and report.get("catalog_validate_errors") == 0
            and not report.get("rows_words")
            # Закон паритета — пятый гейт: `ok is True` и ничто иное
            # (пустого поля, `None` и «не прогонялся» довольно, чтобы
            # строка честно ушла в `needs_human`).
            and (report.get("parity") or {}).get("ok") is True
            and solver_ok)


def _scrubber(roots: list):
    """Очистка строк свидетельства от машинных путей (§4).

    Закон — скруббер модуля сборки: он же чистит журналы Maven и JVM.
    Модуля нет — остаётся вычитание известных корней; это не второй
    закон, а обеднённый запасной путь, и он назван словами."""
    mod, _words = _solver_module_or_words()
    named = [str(r) for r in roots if r]
    if mod is not None and hasattr(mod, "scrub"):
        return lambda text: mod.scrub(text, roots=named)

    def poor(text: str) -> str:
        out = str(text)
        for root in sorted(named, key=len, reverse=True):
            for variant in (root, root.replace("\\", "/"),
                            root.replace("/", "\\")):
                out = out.replace(variant, "<путь>")
        return out
    return poor


def _scrub_deep(value, scrub):
    """Тот же скруббер по всему дереву отчёта: машинный путь одинаково
    неуместен и в словах гейта, и в хвосте журнала."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_deep(v, scrub) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_deep(v, scrub) for v in value]
    return value


def _write_evidence(repo, cr: dict, report: dict, extra: dict,
                    scrub=None, bindings=None) -> None:
    """Блок свидетельства в файл кандидата: гейты, ветка, коммит. Это
    честный ОТЧЁТ конвейера в ветке, а не подпись (подписанное
    свидетельство — очередь укрепления, вопрос 12 проекта).

    `evidence.executors` получает `file` и `symbol` из строк временных
    привязок: схема кандидата требует их, и без них служба отказала бы
    принять запись (§4)."""
    work = cr.get("work") or {}
    path = repo / str(work.get("candidate") or "")
    if not path.is_file():
        return
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    executors = []
    for kind in report.get("present_rows") or ():
        row = (bindings or {}).get(kind) or {}
        item = {"kind": kind, "state": "есть"}
        for field in ("file", "symbol"):
            if row.get(field):
                item[field] = str(row.get(field))
        executors.append(item)
    # Путь временной папки гейта — машинная подробность рабочего места:
    # в файл, который поедет владельцу и в каталог, он не кладётся.
    gates = {k: v for k, v in report.items() if k != "tmp"}
    if scrub is not None:
        gates = _scrub_deep(gates, scrub)
    doc["evidence"] = {"cr_key": cr.get("id"), **extra,
                       "at": _now(), "gates": gates,
                       "executors": executors}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def _run_solver_build(ev, repo, py: str, attempt, commit: str,
                      with_tests: bool) -> dict:
    """Сборка решателя ИЗ КОММИТА ветки в отдельной папке попытки (0.3).

    Идёт ПОДПРОЦЕССОМ через `ev._run` — единственную точку ожидающих
    подпроцессов контура (она же точка подмены в тестах). Внутри модуль
    достаёт исходники коммита `git archive`, пакует их в scratch-копии,
    снимает хеши, при `--with-tests` прогоняет тесты ПОСЛЕ упаковки и
    снимает хеши повторно."""
    handles = _solver_handles()
    out_path = attempt / "build.json"
    cmd = [py, "-m", "vmp.solver_build", "build",
           "--repo", str(repo),
           "--attempt", str(attempt), "--source", "commit",
           "--commit", str(commit), "--json", str(out_path),
           "--xmx-maven", SOLVER_XMX_MAVEN, "--xmx-fork", SOLVER_XMX_FORK]
    if with_tests:
        cmd.append("--with-tests")
    for key, flag in (("java_home", "--java-home"), ("mvn", "--mvn"),
                      ("m2", "--m2")):
        if handles.get(key):
            cmd += [flag, str(handles[key])]
    # Судья исполняется ИЗ ПРОДУКТА, а судимое дерево называется ключом
    # `--repo`: при `cwd=<клон>` первым в пути поиска модулей стоит клон,
    # и `import vmp` исполнял бы `vmp/__init__.py` ВЕТКИ АГЕНТА внутри
    # процесса судьи — счёт surefire и личность артефакта подменялись бы
    # тремя строчками в файле, который судья не судит. Перечень
    # охраняемых файлов берётся из продукта по той же причине.
    rc, out = ev._run(cmd, cwd=_product_root(),
                      timeout_s=SOLVER_BUILD_TIMEOUT_S + SOLVER_TEST_TIMEOUT_S,
                      scrub_prefixes=ev._GATE_ENV_SCRUB)
    doc = None
    try:
        doc = json.loads(out_path.read_text(encoding="utf-8",
                                            errors="replace"))
    except (OSError, json.JSONDecodeError):
        doc = _json_line(out)
    if not isinstance(doc, dict) or not doc:
        return {"available": False, "rc_cli": rc,
                "words": (f"сборка решателя не состоялась: отчёта "
                          f"build.json нет (код {rc}) — "
                          f"{(out or '')[-300:]}"),
                "log_tail": (out or "")[-1500:]}
    doc.setdefault("available", rc == 0)
    doc["rc_cli"] = rc
    # `available` — про ТУЛЧЕЙН (Java, обёртка, кэш), и только про него:
    # код возврата модуля 3 говорит «собирать нечем», код 1 — «собрали, и
    # оно красное». Гасить `available` у красных тестов значило бы писать
    # в свидетельство, что сборки на контуре нет, когда она была, и
    # добавлять к своим словам судьи ещё и слова модуля вторым разом.
    if rc == SOLVER_RC_NO_TOOLCHAIN:
        doc["available"] = False
        doc.setdefault("words", f"тулчейн сборки недоступен (код {rc})")
    elif rc != 0:
        doc["ok"] = False
    return doc


def _build_problems(doc: dict, *, with_tests: bool, kinds: list,
                    hygiene: dict | None = None) -> list:
    """Судьи сборки и тестов Kotlin ПО МАШИННЫМ ОТЧЁТАМ, а не по словам
    (0.3, 0.4): код возврата, отчёты surefire, число `@Test` на ветке и
    на `main`, хеши артефакта до и после тестов.

    Число тестов на ветке и число исполненных surefire сводятся сборкой,
    число на `main`, новые файлы и выключенные тесты — гигиеной: тест,
    написанный тем же агентом, доказательством не является, но его
    отсутствие и его исполнение судятся машинно."""
    hyg = hygiene or {}
    words = []
    if doc.get("available") is not True:
        words.append(str(doc.get("words") or "сборка решателя недоступна"))
    if doc.get("rc_package") not in (0, None):
        words.append(f"упаковка решателя: код {doc.get('rc_package')}")
    art = doc.get("artifact") or {}
    if doc.get("available") is True and not art.get("app_jar_sha256"):
        words.append("личность артефакта не снята: SHA-256 архива "
                     "приложения нет — свидетельство о том, что "
                     "запускалось, было бы пустым")
    sf = doc.get("surefire") or {}
    ta = doc.get("test_annotations") or {}
    if with_tests:
        if doc.get("rc_test") not in (0, None):
            words.append(f"тесты решателя: код {doc.get('rc_test')}")
        if sf.get("failures") or sf.get("errors"):
            words.append(f"surefire: failures {sf.get('failures')}, "
                         f"errors {sf.get('errors')}")
        if (isinstance(sf.get("tests"), int)
                and isinstance(ta.get("branch"), int)
                and sf["tests"] != ta["branch"]):
            words.append(f"surefire исполнил {sf['tests']} тестов, а "
                         f"@Test на ветке {ta['branch']} — часть тестов "
                         f"решателя не исполнена")
        if doc.get("tests_match_sources") is False:
            words.append("сборка: число исполненных тестов не сошлось с "
                         "числом @Test в исходниках ветки")
        # Мера «пропущенных не стало больше» — из гигиены, и считает она
        # ВСЮ семью механизмов пропуска JUnit 5 (`@Disabled`, `@Ignore`,
        # условные `@EnabledIf…`, `Assumptions`), а не две аннотации: в
        # дереве нет ни одного `@Disabled`, но surefire стабильно даёт
        # `skipped=1` из-за `@EnabledIfSystemProperty(named = "bench")`,
        # и узкий счёт краснил бы КАЖДЫЙ CR с видами решателя по
        # построению, каким бы зелёным ни был код.
        skipped_main = hyg.get("skipped_main")
        if (isinstance(sf.get("skipped"), int)
                and isinstance(skipped_main, int)
                and sf["skipped"] > skipped_main):
            words.append(f"пропущенных тестов решателя стало "
                         f"{sf['skipped']} против {skipped_main} на main")
        if hyg.get("disabled_added"):
            words.append(f"в новых файлах тестов "
                         f"{hyg['disabled_added']} выключенных тестов")
        if kinds:
            main_n = hyg.get("tests_main")
            branch_n = (ta.get("branch") if isinstance(ta.get("branch"), int)
                        else hyg.get("tests_branch"))
            if not (isinstance(main_n, int) and isinstance(branch_n, int)
                    and branch_n >= main_n + 1):
                words.append(f"нового теста решателя нет: @Test на ветке "
                             f"{branch_n}, на main {main_n} — исполнитель "
                             f"сдан без своего теста")
            if not (hyg.get("new_files") or ()):
                words.append("нового файла тестов решателя нет: правка "
                             "существующего файла — путь самосертификации")
        if art.get("unchanged_after_tests") is False:
            words.append("артефакт изменился между упаковкой и тестами: "
                         "измерялся не тот код, который запускался")
    if doc.get("hs_err"):
        words.append("JVM сборки упала: "
                     + "; ".join(str(x) for x in list(doc["hs_err"])[:2]))
    # Красное, которого не назвал ни один судья выше, молчанием не
    # становится зелёным: модуль сказал «не ok» либо команда вернула
    # ненулевой код — говорим его словами один раз.
    rc_cli = doc.get("rc_cli")
    if not words and (doc.get("ok") is False
                      or (isinstance(rc_cli, int) and rc_cli != 0)):
        words.append(str(doc.get("words")
                         or f"сборка решателя красная: код {rc_cli}"))
    return words


def _artifact_dir(attempt, build: dict):
    """Папка артефакта попытки. В отчёте сборки путь ПРОСКРУБЛЕН (машинных
    путей в свидетельстве нет), поэтому настоящий путь собирается от папки
    попытки, а скрубленный служит формой."""
    raw = str((build.get("artifact") or {}).get("dir") or "")
    default = attempt / "kotlin-solver" / "target" / "quarkus-app"
    for mark in ("<попытка>", "<attempt>"):
        if raw.startswith(mark):
            rel = raw[len(mark):].lstrip("/\\")
            return (attempt / rel) if rel else default
    if raw and "<" not in raw:
        from pathlib import Path
        path = Path(raw)
        if path.is_absolute():
            return path
        return attempt / raw
    return default


def _artifact_after_stand(solver: dict, attempt, build: dict) -> None:
    """Повторный отпечаток артефакта ПОСЛЕ стенда (§1 шаг 9).

    Проверка слабая: на Windows JVM держит архивы открытыми, и её запись
    делается как есть — театром она не выдаётся. Границу держат сверка
    PID↔порт, метка процесса и отдельная папка попытки. Отпечаток
    снимает МОДУЛЬ сборки: своего счёта хешей у конвейера нет."""
    run = solver.get("run") or {}
    was = (build.get("artifact") or {}).get("tree_sha256")
    mod, _words = _solver_module_or_words()
    ident = getattr(mod, "artifact_identity", None) if mod else None
    if not callable(ident) or not was:
        run["artifact_unchanged_after"] = None
        run["artifact_words"] = ("повторный отпечаток артефакта не снят: "
                                 "модуль сборки его не отдаёт")
    else:
        try:
            now = ident(_artifact_dir(attempt, build)) or {}
            run["artifact_unchanged_after"] = (
                now.get("tree_sha256") == was)
        except Exception as exc:                         # noqa: BLE001
            run["artifact_unchanged_after"] = None
            run["artifact_words"] = (f"повторный отпечаток артефакта не "
                                     f"снят: {exc}")
    solver["run"] = run


def _drop_target(solver: dict, attempt) -> None:
    """Удаление `target/` папки попытки после остановки решателя (§1 шаг
    10): хранится РЕЦЕПТ и отпечатки, а не сотни мегабайт артефакта.
    Файлы могут остаться занятыми — тогда слова, а не исключение."""
    import shutil
    target = attempt / "kotlin-solver" / "target"
    if not target.exists():
        return
    try:
        shutil.rmtree(target)
    except OSError as exc:
        solver.setdefault("run", {})["target_words"] = (
            f"папка сборки попытки не удалена: {exc}")


def _growth_report_words(solver: dict, report: dict) -> str | None:
    """Слова конвейера о РОСТЕ таблиц решателя для отчёта строки (§2.4).

    Класс К-табл больше не приговор: рост законен, если его объявила
    запись кандидата, — и судит это закон паритета на временном
    каталоге, а не класс правки. Отчёт строки называет обе половины
    (что выросло и что сказал закон): человеку в карточке видно только
    его, а `gates.parity` лежит в свидетельстве."""
    if solver.get("class") != SOLVER_CLASS_TABLES:
        return None
    growth = solver.get("tables_growth") or {}
    names = "; ".join(f"{k}: {', '.join(str(x) for x in growth[k])}"
                      for k in sorted(growth) if growth[k])
    parity = report.get("parity") or {}
    if parity.get("ok") is True:
        return (f"рост таблиц решателя ({names}) объявлен записью "
                f"кандидата; паритет на временном каталоге зелёный "
                f"(gates.parity); на живом каталоге станет зелёным после "
                f"сборки записи службой и цепочки после сборки")
    return (f"рост таблиц решателя ({names}) записью кандидата не "
            f"объявлен либо объявлен не так: паритет на временном "
            f"каталоге красен — "
            f"{parity.get('words') or 'вердикта нет'}")


def _implement_catalog(ev, repo, cr: dict, cr_id: str, branch: str,
                       tail: str, isolation: str, pre: dict) -> dict:
    """Порядок §1 после агента: белый список и судьи Kotlin — коммит
    работы — рост таблиц — сборка решателя из коммита — эфемерный
    решатель — цепочка живого каталога — гейты временного каталога —
    остановка решателя — pytest — свидетельство (набор клона после
    гейтов и после остановки решателя — с 09.09.2026).

    Сборка, решатель и живой стенд идут у КАЖДОГО CR каталога (0.2): без
    решателя цепочка переписала бы живые строки видов решателя в
    `skipped` и сняла бы `stand_verified` у всего каталога."""
    import contextlib

    sync = str(pre.get("sync") or "")
    kinds = list(pre.get("kinds") or ())
    attempt, attempt_n = _attempt_dir(repo, cr_id)
    scrub = _scrubber([repo, attempt])
    solver: dict = {
        "kinds": kinds,
        "class": SOLVER_CLASS_NONE if not kinds else None,
        "attempt": attempt_n, "attempt_dir": attempt.name,
        "tables_growth": None, "tables_words": None,
        "build": {"available": False, "words": "не прогонялась"},
        "run": {"words": "не поднимался"},
        "stand": {}, "parity": None, "words": None,
    }
    report = {"paths": "ok", "executor_files": "ok", "tree_guard": "ok",
              "parity_witness": "не прогонялся",
              "chain_live": "ok", "pytest": "не прогонялся",
              "kotlin_tests": "ok" if kinds else KOTLIN_TESTS_SKIPPED,
              "candidate_reload": "не прогонялось",
              "solver": solver}
    impl = {"branch": branch, "isolation": isolation, "report": tail,
            # Имя модели агента — часть свидетельства (правило именованных
            # моделей; слово владельца 09.09.2026: coding = claude-opus-5).
            "agent_model": settings.cli_model("coding"),
            "sync": sync, "candidate": (cr.get("work") or {}).get("candidate")}

    def _red(key: str, words: str, *,
             prefix: str = "до сборки решателя не дошло") -> dict:
        """Красный гейт, обрывающий цепочку: живого суда не было — и
        блок решателя говорит об этом словами, а не молчит пустыми
        полями. `prefix` называет, где именно оборвано (до сборки — или
        после неё, когда дерево клона изменилось за время сборки)."""
        report[key] = words
        solver["words"] = f"{prefix}: {words}"
        if kinds and not solver.get("class"):
            solver["class"] = "не определён: снимок таблиц ветки не снят"
        impl["gates_report"] = report
        impl["needs_human"] = True
        impl["solver"] = {"class": solver["class"], "build_rc": None,
                          "tests": None, "app_jar_sha256": "",
                          "url": None, "stand_by_kind": {}}
        return _update_cr(cr_id, {"status": "needs_human", "impl": impl})

    ev._git(repo, "add", "-A")
    # `--no-renames`: с распознаванием переименований git печатает одну
    # строку `R…` с ДВУМЯ путями, и белый список судил бы только новый —
    # перенос судьи (`tests/test_<судья>.py`) в разрешённую папку под
    # именем, которого pytest не собирает, проходил бы молча. Без
    # распознавания исходник приходит как `D`, цель — как `A`.
    _, changed = ev._git(repo, "-c", "core.quotePath=false", "diff",
                         "--cached", "--no-renames", "--name-status")
    changes = _name_status(changed)
    bad = _catalog_path_problems(cr, changes)
    if kinds:
        bad += _kotlin_hunk_problems(ev, repo, cr, changes)
        bad += _kotlin_token_problems(ev, repo, cr, changes)
    if bad:
        return _red("paths", "; ".join(bad[:10]))
    seed_bad = _catalog_seed_problems(repo, cr)
    if seed_bad:
        return _red("executor_files", "; ".join(seed_bad[:10]))
    hygiene: dict = {}
    if kinds:
        # Гигиена тестов Kotlin (0.4): новый `@Test` — в НОВОМ файле, в
        # тексте которого стоит литерал предмета строки посева.
        mod, words = _solver_module_or_words()
        if mod is None:
            return _red("kotlin_tests", words)
        try:
            hyg = mod.test_hygiene(repo, "main", _seed_literals(repo, cr))
        except Exception as exc:                         # noqa: BLE001
            return _red("kotlin_tests",
                        f"гигиена тестов решателя не проверена: {exc}")
        if not (hyg or {}).get("ok"):
            words = str((hyg or {}).get("words") or "")
            problems = "; ".join(str(p) for p in
                                 ((hyg or {}).get("problems") or ())[:5])
            return _red("kotlin_tests",
                        (problems or words or "тесты решателя не сданы"))
        report["kotlin_tests"] = "ok"
        solver["test_hygiene"] = hyg
        hygiene = hyg
    # Работа агента коммитится ДО гейтов содержания: красный гейт не
    # имеет права стереть сделанное (ветка — результат для человека).
    rec = cr.get("record") or {}
    # Исход коммита ПРОВЕРЯЕТСЯ (сквозная проверка 08.09.2026): в клоне
    # без личности автора git отказывал кодом 128, цепочка шла дальше от
    # базовой головы, сторож дерева краснил файлы агента как грязь, а
    # уборка их стирала. Отказ — честный `needs_human`, и работа агента
    # при нём не пропадает: она уходит патчем в папку попытки.
    commit, commit_words = _commit_work(
        ev, repo, f"CR {cr_id}: исполнитель {rec.get('section')}/"
                  f"{rec.get('record_id')} (agent-implemented)")
    if commit_words:
        слова = (f"работа агента не закоммичена: {commit_words}; "
                 f"{_rescue_agent_work(ev, repo, attempt)}")
        # Слова — В НАЧАЛО отчёта строки: карточка показывает первые 400
        # знаков `impl.report`, и причина «нужен человек» обязана быть
        # видна там, а не только в блоке гейтов.
        impl["report"] = (слова + "\n\n"
                          + str(impl.get("report") or "")).strip()
        return _red("commit", слова)
    _, head = ev._git(repo, "rev-parse", "HEAD")
    impl["commit"] = commit
    py = ev._venv_python(repo)
    # Просьба В-2: свидетели закона паритета в коде исполнителя
    # (`vmp/run/roster_certify.py`) обязаны быть теми же, что были до
    # работы агента. Иначе «природа записи по коду» самосертифицируема:
    # правка перечня — и запись любой природы «сходится с кодом».
    certify_after, certify_words = _certify_snapshot(ev, repo, py)
    if pre.get("certify_main") is None or certify_after is None:
        return _red("parity_witness",
                    f"свидетели паритета не прочитаны: "
                    f"{pre.get('certify_words') or certify_words}")
    if certify_after != pre["certify_main"]:
        разошлись = sorted(k for k in PARITY_WITNESS_CONSTS
                           if certify_after.get(k)
                           != pre["certify_main"].get(k))
        return _red("parity_witness",
                    f"правка свидетеля паритета: {', '.join(разошлись)} в "
                    f"vmp/run/roster_certify.py — по этим перечням закон "
                    f"паритета судит природу записи, её условия и "
                    f"сертификатные шаблоны, и правит их не тот, кого они "
                    f"судят")
    report["parity_witness"] = "ok"
    # Шаг 4 (0.9): класс правки — по МЕХАНИЧЕСКОМУ сравнению снимков
    # таблиц решателя, а не по словам агента.
    if kinds:
        after, words = _tables_snapshot(ev, repo, py)
        mod, mod_words = _solver_module_or_words()
        if after is None:
            solver["tables_words"] = words
        elif mod is None:                                # pragma: no cover
            solver["tables_words"] = mod_words
        else:
            growth = mod.tables_growth(pre.get("tables_main") or {}, after)
            solver["tables_growth"] = growth
            grew = sorted(k for k, v in (growth or {}).items() if v)
            solver["class"] = (SOLVER_CLASS_TABLES if grew
                               else SOLVER_CLASS_EXEC)
            if grew:
                names = "; ".join(
                    f"{k}: {', '.join(str(x) for x in growth[k])}"
                    for k in grew)
                # Ссылка словами, а не второй закон: паритет судит
                # движок `catalog/tools/parity.py` на временном каталоге
                # «живой ⊕ кандидат», и его вердикт лежит целиком в
                # `gates.parity` (пакет «паритет на временном каталоге»).
                solver["parity"] = (
                    f"рост таблиц решателя ({names}) — судит закон "
                    f"паритета на временном каталоге; вердикт см. "
                    f"gates.parity")
    # Шаг 5: сборка из коммита ветки. Тесты решателя прогоняются, когда
    # исходники решателя ветки ОТЛИЧАЮТСЯ от main, — это проверяется
    # diff'ом, а не выводится из перечня видов (0.2): «исходники
    # тождественны main» обязано быть утверждением, а не допущением.
    rc_diff, _out_diff = ev._git(repo, "diff", "--quiet", "main...HEAD",
                                 "--", "kotlin-solver/")
    with_tests = bool(kinds) or rc_diff != 0
    build = _run_solver_build(ev, repo, py, attempt, head.strip(),
                              with_tests=with_tests)
    build_words = _build_problems(build, with_tests=with_tests,
                                  kinds=kinds, hygiene=hygiene)
    if not with_tests:
        build["tests"] = SOLVER_TESTS_SKIPPED
    if build_words:
        build["words"] = "; ".join(build_words)
    elif build.get("words"):
        build.pop("words")
    solver["build"] = build
    impl["build_tail"] = scrub(str(build.get("log_tail") or ""))[-1500:]
    # Сторож дерева ПОСЛЕ сборки: тесты решателя — код агента, и
    # исполняет их surefire под учётной записью службы в scratch-копии
    # внутри клона, до стенда, pytest и гейтов. Одна строка вида
    # `File("../../../../vmp/stand_solver.py").writeText(…)` переписала
    # бы судью, и все три сказали бы «зелено», а `reset --hard` в конце
    # стёр бы след. Дерево обязано быть тем же, каким его оставил
    # коммит работы агента.
    dirt = _tree_dirt(ev, repo)
    if dirt:
        return _red("tree_guard",
                    "дерево клона изменено во время сборки и тестов "
                    "решателя (судьи правятся измеряемым): "
                    + ", ".join(dirt),
                    prefix="суд остановлен после сборки")
    build_green = not build_words
    if not build_green:
        # Шаги 6, 7-живой и 9-живой пропускаются НЕ молча.
        solver["run"] = {"words": "живой прогон не состоялся: сборка "
                                  "красная"}
    url, solver_obj = None, None
    try:
        with contextlib.ExitStack() as stack:
            if build_green:
                try:
                    solver_obj = _solver_factory(
                        _artifact_dir(attempt, build), cr_key=cr_id,
                        log_path=attempt / "solver.log",
                        # Дом Java — по ТОМУ ЖЕ порядку, что у пробы и
                        # сборки (0.12): одна лишь ручка проекта
                        # оставляла решателю `java` из PATH, и на
                        # площадке, где Java задана через JAVA_HOME или
                        # лежит в <клон>/.tools/jdk*, проба и сборка
                        # были зелёными, а старт падал «нет такого
                        # файла» — после потраченной квоты.
                        java_home=_solver_java_home(repo),
                        xmx=_solver_handles()["xmx"],
                        start_timeout_s=SOLVER_START_TIMEOUT_S,
                        tag=_solver_tag_or_none(repo))
                    url = stack.enter_context(solver_obj)
                except Exception as exc:                 # noqa: BLE001
                    solver_obj, url = None, None
                    solver["run"] = {
                        "words": f"эфемерный решатель не поднялся: {exc}"}
            report["chain_live"] = _catalog_chain(ev, repo, py,
                                                  head.strip(), url)
            # Гейты временного каталога — СРАЗУ за живой цепочкой, пока
            # решатель поднят, и только потом полный набор клона: набор
            # идёт минутами и законно упражняет гигиену процессов
            # (`recover_interrupted` зовёт сборщика сирот СВОЕГО контура —
            # того же, что у судьи). Сквозная проверка 09.09.2026: судья
            # «умер сам» посреди pytest, и стенд временного каталога
            # судил мёртвый адрес — все строки Kotlin красные без вины
            # записи. Решатель гасится ДО набора: под набором ему жить
            # незачем.
            gates = _catalog_gates(ev, repo, py, cr, tmp=attempt,
                                   solver_url=url)
            solver["stand"] = gates.pop("solver_stand", {})
            report.update(gates)
            stack.close()
            # Ключей `--ignore` и перечня «ожидаемо красных» у этого
            # прогона нет и не будет: свидетель паритета в дереве судит
            # «живой каталог ⊕ кандидаты `catalog/candidates/**`» (0.3),
            # то есть ровно то, что несёт ветка, — и зелен по существу.
            t_rc, t_out = ev._run([py, "-m", "pytest", "-q"], cwd=repo,
                                  timeout_s=IMPL_TEST_TIMEOUT_S,
                                  scrub_prefixes=ev._GATE_ENV_SCRUB)
            report["pytest"] = "green" if t_rc == 0 else f"код {t_rc}"
            impl["tests_ok"] = (t_rc == 0)
            impl["tests_tail"] = (t_out or "")[-1500:]
    finally:
        if solver_obj is not None:
            try:
                run = dict(solver_obj.report() or {})
                # Пределы живого прогона модуль берёт импортом из стенда;
                # их отсутствие конвейер закрывает тем же импортом, а не
                # своими числами (§4).
                run.setdefault("limits", _solver_limits())
                solver["run"] = run
            except Exception as exc:                     # noqa: BLE001
                solver["run"] = {"words": f"отчёт решателя не снят: {exc}"}
            impl["solver_tail"] = scrub(
                str((solver.get("run") or {}).get("log_tail") or ""))[-1500:]
            # Повторный отпечаток артефакта — тоже в `finally` (§1 шаг
            # 10): при исключении шагов 7–9 он снимался бы после
            # остановки решателя, а прежде не снимался вовсе.
            _artifact_after_stand(solver, attempt, build)
        # Удаление `target/` — при ЛЮБОМ исходе, включая исключение
        # шагов 7–9 и неподнявшийся решатель: папка попытки иначе
        # оставалась бы с сотнями мегабайт сборки, а просьба И-2 о
        # свободном месте — не пожелание.
        _drop_target(solver, attempt)
    if not build_green:
        stand = solver.get("stand") or {}
        stand["words"] = ("живой прогон не состоялся: сборка красная"
                          + ("; " + str(stand["words"])
                             if stand.get("words") else ""))
        solver["stand"] = stand
    # Итог словами — ТОЛЬКО о красном: слова зелёного прогона решателя
    # («поднят за 1,4 с») стоят в своём блоке и итогом не притворяются.
    run = solver.get("run") or {}
    # Красным считается и НЕСОСТОЯВШАЯСЯ сверка (`None`): 0.16 требует
    # для `implemented` сошедшихся PID↔порт и свободного порта, а слова
    # «сверка не состоялась» модуль пишет — итог обязан их назвать.
    run_red = (not run.get("url")
               or run.get("stopped") not in ("terminated", "killed")
               or run.get("pid_matches_port") is not True
               or run.get("port_free_after") is not True
               or run.get("artifact_unchanged_after") is False)
    # `solver["parity"]` в итог НЕ входит: это ссылка на вердикт закона
    # (`gates.parity`), а не красные слова. Красное о паритете говорит
    # сам вердикт — и его слова уходят в отчёт строки (§2.4).
    solver["words"] = "; ".join(w for w in (
        build.get("words"),
        ((run.get("words") or "эфемерный решатель не отработал полностью")
         if run_red else None),
        (solver.get("stand") or {}).get("words"),
        solver.get("tables_words")) if w) or None
    # Рост таблиц решателя — словами В ОТЧЁТ СТРОКИ: карточку читает
    # человек, и «К-табл» без слов о том, чем кончился закон паритета,
    # значило бы для него ровно столько же, сколько молчание.
    grown = _growth_report_words(solver, report)
    if grown:
        impl["report"] = ((str(impl.get("report") or "") + "\n\n"
                           + grown).strip())
    bindings = _bindings_rows(attempt / "bindings.json",
                              f"{rec.get('section')}/{rec.get('record_id')}")
    # Второй раз сторож дерева — ПЕРЕД свидетельством: за время стенда,
    # pytest и гейтов в дереве вправе измениться только три известных
    # файла свидетельства (их перевыпускает цепочка). Всё прочее —
    # правка судьи измеряемым, и свидетельство о ней молчать не вправе.
    evidence_files = [f for f in ((cr.get("work") or {}).get("candidate"),
                                  "vmp/bindings.json",
                                  "vmp/stand_results.json") if f]
    dirt = _tree_dirt(ev, repo, allowed=evidence_files)
    if dirt:
        report["tree_guard"] = ("дерево клона изменено во время стенда и "
                                "гейтов сверх файлов свидетельства: "
                                + ", ".join(dirt))
    _write_evidence(repo, cr, report,
                    {"branch": branch, "commit": impl["commit"]},
                    scrub=scrub, bindings=bindings)
    # Кандидат перечитывается ТЕМ ЖЕ читателем, что и служба (§4): без
    # этого свидетельство писалось бы после проверки и не судилось.
    report["candidate_reload"] = _candidate_reload(repo, cr)
    # Коммит свидетельства — три ИЗВЕСТНЫХ файла поимённо (белым списком
    # он не судится: их кладёт конвейер, не агент).
    ev._git(repo, "add", "--", *evidence_files)
    # Коммит свидетельства — с той же личностью автора и с той же
    # проверкой исхода: молчаливый отказ здесь означал бы `implemented`
    # со свидетельством, которого в ветке нет.
    ev_commit, ev_words = _commit_work(
        ev, repo, f"CR {cr_id}: свидетельство гейтов")
    impl["evidence_commit"] = ev_commit
    if ev_words:
        report["evidence_commit"] = (f"свидетельство гейтов не "
                                     f"закоммичено: {ev_words}")
    _, diff_stat = ev._git(repo, "diff", "--stat", "main...HEAD")
    impl["diff_stat"] = diff_stat[-1500:]
    impl["gates_report"] = report
    # Выжимка для карточки строки: класс правки, коды сборки и тестов,
    # начало отпечатка артефакта, адрес решателя и числа стенда по видам.
    impl["solver"] = {
        "class": solver.get("class"),
        "build_rc": (solver.get("build") or {}).get("rc_package"),
        "tests": ((solver.get("build") or {}).get("tests")
                  or (solver.get("build") or {}).get("surefire")),
        "app_jar_sha256": str(((solver.get("build") or {}).get("artifact")
                               or {}).get("app_jar_sha256") or "")[:12],
        "url": (solver.get("run") or {}).get("url"),
        "stand_by_kind": (solver.get("stand") or {}).get("by_kind") or {},
    }
    ev._git(repo, "checkout", "main")
    # Ключ `evidence_commit` появляется в отчёте ТОЛЬКО при отказе
    # коммита свидетельства, поэтому судится здесь, а не в общем законе
    # зелени (его перечень — про гейты содержания).
    green = _gates_green(report) and not report.get("evidence_commit")
    if not green:
        impl["needs_human"] = True
    return _update_cr(cr_id, {"status": "implemented" if green
                              else "needs_human", "impl": impl})


def _candidate_reload(repo, cr: dict) -> str:
    """Перечитывание кандидата ТЕМ ЖЕ читателем, что и служба (§4).

    Свидетельство дописывается в файл кандидата ПОСЛЕ его проверки —
    значит сама эта дописка не судится ничем, кроме этого шага: схема
    закрыта, и лишний ключ в `evidence` отверг бы `adopt` службы уже у
    владельца."""
    from template_catalog import candidates as tc_candidates
    path = repo / str((cr.get("work") or {}).get("candidate") or "")
    if not path.is_file():
        return f"файла кандидата нет: {path.name}"
    try:
        tc_candidates.load_candidate(path)
    except Exception as exc:                             # noqa: BLE001
        return (f"кандидат со свидетельством не перечитывается: {exc} — "
                f"служба такую запись не приняла бы")
    return "ok"


def _cleanup_tree(ev, repo, branch: str) -> None:
    """Best-effort: whatever happened, leave the test checkout clean and on
    main. The work branch is deleted only when it adds no commits over main
    (a branch WITH a commit is the reviewable result — keep it)."""
    try:
        ev._git(repo, "reset", "--hard")
        ev._git(repo, "clean", "-fd")
        _, cur = ev._git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        if cur.strip() != "main":
            ev._git(repo, "checkout", "main")
        rc, extra = ev._git(repo, "rev-list", "--count", f"main..{branch}")
        if rc == 0 and extra.strip() == "0":
            ev._git(repo, "branch", "-D", branch)
    except Exception:                                # noqa: BLE001
        pass                                          # never mask the outcome


def implement_cr(cr_id: str, by: str) -> dict:
    """Run the implementer in the isolated test checkout on branch
    cr-<id>; record the honest outcome. Synchronous (the UI shows a
    spinner; runs are minutes-long)."""
    from . import evolution_runner as ev

    cr = get_cr(cr_id)
    if cr is None:
        raise ChangeRequestError(f"no such CR: {cr_id}")
    if cr["status"] != "accepted":
        raise ChangeRequestError(
            f"CR {cr_id} is {cr['status']}; accept it first")
    if (cr.get("kind") == CATALOG_KIND
            and not ((cr.get("spec") or {}).get("executors_requested"))):
        # Единственный случай отказа по видам (0.1): реализовывать
        # нечего. Виды исполнителей решателя (kotlin_*) агенту идут
        # наравне с Python-видами — слово владельца 08.09.2026.
        raise ChangeRequestError(
            "заявка не называет ни одного вида исполнителя: реализовывать "
            "нечего — назовите вид в службе каталога")
    # Contour gate (strict-mode invariant, data-formalization doc §8-bis):
    # the implementer feeds the CR summary AND the raw suggestion texts to
    # the CLOUD CLI. On the internal contour (prod/stage) those are
    # customer-perimeter user data and must never leave the machine — the
    # implementer runs only where this checkout is the external contour.
    if _repo_is_internal_contour():
        raise ChangeRequestError(
            "реализация CR вызывает облачную модель и доступна только на "
            "внешнем Тест-контуре; этот экземпляр — внутренний контур, "
            "тексты предложений пользователей не должны покидать его")
    if _impl_quota_left() <= 0:
        raise ChangeRequestError(
            f"daily implementation limit reached ({_impl_daily_limit()}/day)")
    repo = ev.test_dir()
    if not (repo / ".git").exists():
        raise ChangeRequestError(
            f"test checkout not found at {repo} — the implementer runs "
            "only where the isolated Test contour exists")
    ev._assert_external_contour()

    # The evolution pipeline (nightly scan, phase runs) mutates the SAME
    # test checkout under this lock — never run two writers concurrently.
    if not ev._pipeline_lock.acquire(blocking=False):
        raise ChangeRequestError(
            "evolution pipeline is busy in the test checkout — retry later")
    try:
        return _implement_locked(ev, cr, cr_id, by)
    finally:
        ev._pipeline_lock.release()


def _implement_locked(ev, cr: dict, cr_id: str, by: str) -> dict:
    repo = ev.test_dir()
    rc, out = ev._git(repo, "status", "--porcelain")
    if rc != 0 or out.strip():
        raise ChangeRequestError(
            "test checkout is not clean — resolve it first")

    # Предпосылки CR каталога судятся ДО ветки и ДО квоты: отказ из-за
    # неготового клона или недоступной сборки решателя — не потраченная
    # реализация дня (§1 шаги 0 и 0-К).
    catalog = (cr.get("kind") == CATALOG_KIND)
    pre: dict = {"sync": "", "kinds": []}
    if catalog:
        pre = _catalog_preflight(ev, repo, cr)
        # Предполётная проверка задания — ДО ветки, ДО квоты и ДО агента
        # (шаг 0-П): найденное допущение судей стоит одной минуты здесь и
        # пяти попыток агента там. Строка получает запись `preflight` в
        # любом исходе — она же едет в задание перечнем «что обязано
        # закрыться».
        pf = preflight_catalog_cr(cr, repo=repo, py=ev._venv_python(repo),
                                  ev=ev)
        cr = _update_cr(cr_id, {"preflight": pf})
        if pf["verdict"] == PREFLIGHT_ASSUMPTION:
            return _update_cr(cr_id, {
                "status": "needs_human",
                "impl": {"report": pf["words"], "needs_human": True,
                         "words": pf["words"]}})
    cli_timeout = (IMPL_CLI_TIMEOUT_KOTLIN_S if pre.get("kinds")
                   else IMPL_CLI_TIMEOUT_S)

    branch = f"cr-{cr_id}"
    _impl_quota_take()
    _update_cr(cr_id, {"status": "implementing", "impl_started_by": by})
    try:
        ev._git(repo, "checkout", "-B", branch)
        # Маршрут запуска — тот же, что у фазы Test (аудит R10, P0-04):
        # готовый посредник — единственный путь под отдельной учётной
        # записью агента; прямой subprocess остаётся честным фолбэком
        # под записью приложения (второй контур исполнения был вообще
        # без изоляции — ровно претензия аудитора).
        from . import agent_broker
        impl_isolation = "unisolated-subprocess"
        if agent_broker.available():
            impl_isolation = "broker"
            hand = agent_broker.launch(_impl_prompt(cr),
                                       run_id=f"cr-{cr_id}")
            rc, out, _diag = ev._broker_wait(hand, timeout_s=cli_timeout)
        else:
            # Ворота разрешений CLI выключены явно (как у агента внедрения
            # evolution_runner): иначе умолчание рабочего места владельца
            # («план») оставляет агента без права правок, и он выводит
            # план вместо работы (сквозная проверка 08.09.2026).
            rc, out = ev._run([ev.claude_cli(), "-p",
                               "--dangerously-skip-permissions",
                               "--disallowedTools", "Monitor ScheduleWakeup"]
                              + settings.cli_model_args("coding"), cwd=repo,
                              timeout_s=cli_timeout,
                              input_text=_impl_prompt(cr))
        tail = (out or "")[-4000:]
        if rc != 0 and catalog:
            # Отказ или таймаут агента у CR каталога — честный исход строки
            # словами (ветка с работой агента — результат для человека), а не
            # голая ошибка ответа, при которой строка возвращалась в accepted
            # без следа (сквозная проверка 08.09.2026). У прочих CR — прежний
            # порядок: откат и возврат квоты.
            words = (f"агент реализации завершился с кодом {rc}"
                     + (" (таймаут)" if rc == 124 else "")
                     + f": {tail[-300:].strip()}")
            return _update_cr(cr_id, {
                "status": "needs_human",
                "impl": {"report": tail, "needs_human": True,
                         "words": words, "isolation": impl_isolation,
                         "agent_model": settings.cli_model("coding")}})
        if rc != 0:
            raise ChangeRequestError(f"implementer CLI exit {rc}: "
                                     f"{tail[-300:]}")
        if NEEDS_HUMAN_MARKER in (out or ""):
            return _update_cr(cr_id, {
                "status": "needs_human",
                "impl": {"report": tail, "needs_human": True,
                         "agent_model": settings.cli_model("coding")}})
        if catalog:
            return _implement_catalog(ev, repo, cr, cr_id, branch, tail,
                                      impl_isolation, pre)
        # diff guard BEFORE tests/commit: enforce the HARD RULES on the
        # actual changed paths, not on the model's good behaviour.
        # core.quotePath=false so a non-ASCII path (e.g. Cyrillic under
        # webapp/config/) is emitted verbatim, not octal-escaped and
        # quoted — otherwise the denylist prefix match would miss it.
        ev._git(repo, "add", "-A")
        # --no-renames: иначе строка переименования называет только
        # новый путь, и удаление файла из запретной зоны переносом в
        # разрешённую сторож не увидел бы.
        _, changed = ev._git(repo, "-c", "core.quotePath=false",
                             "diff", "--cached", "--no-renames",
                             "--name-only")
        bad = _forbidden_paths(changed.splitlines())
        if bad:
            return _update_cr(cr_id, {
                "status": "needs_human",
                "impl": {"report": "diff guard: изменены запрещённые "
                                   "пути: " + ", ".join(sorted(bad)[:10]),
                         "needs_human": True}})
        # honest test run in the test repo's own venv
        py = ev._venv_python(repo)
        t_rc, t_out = ev._run([py, "-m", "pytest", "-q"], cwd=repo,
                              timeout_s=IMPL_TEST_TIMEOUT_S)
        tests_ok = (t_rc == 0)
        # Та же проверка исхода, что у CR каталога: без неё отказ git
        # (в клоне без личности автора — код 128) оставлял бы строку с
        # `commit` базовой головы и зелёными тестами, а уборка стирала
        # бы работу агента без следа.
        commit, commit_words = _commit_work(
            ev, repo, f"CR {cr_id}: {cr['title']} (agent-implemented)")
        if commit_words:
            rescue = _rescue_agent_work(ev, repo,
                                        repo / "_repro" / f"cr-{cr_id}")
            слова = (f"работа агента не закоммичена: {commit_words}; "
                     f"{rescue}")
            # Слова — в НАЧАЛО отчёта: карточка показывает первые 400
            # знаков `impl.report`.
            return _update_cr(cr_id, {
                "status": "needs_human",
                "impl": {"branch": branch, "isolation": impl_isolation,
                         "agent_model": settings.cli_model("coding"),
                         "report": (слова + "\n\n" + (tail or "")).strip(),
                         "needs_human": True, "words": слова,
                         "tests_ok": tests_ok,
                         "tests_tail": (t_out or "")[-1500:]}})
        _, diff_stat = ev._git(repo, "diff", "--stat", "main...HEAD")
        ev._git(repo, "checkout", "main")
        impl = {"branch": branch, "commit": commit,
                "tests_ok": tests_ok,
                "tests_tail": (t_out or "")[-1500:],
                "diff_stat": diff_stat[-1500:],
                # Способ запуска — часть записи (аудит R10, P0-04).
                "isolation": impl_isolation,
                # Имя модели агента — тоже часть записи (правило
                # именованных моделей; слово владельца 09.09.2026).
                "agent_model": settings.cli_model("coding"),
                "report": tail}
        if tests_ok:
            out_cr = _update_cr(cr_id, {"status": "implemented",
                                        "impl": impl})
            help_center.update_suggestions_status(
                cr.get("suggestion_ids") or [], "implemented", cr_id)
        else:
            out_cr = _update_cr(cr_id, {"status": "needs_human",
                                        "impl": impl})
        return out_cr
    except ChangeRequestError:
        _impl_quota_refund()                         # run never completed
        _update_cr(cr_id, {"status": "accepted"})    # honest rollback
        raise
    except Exception as exc:                         # noqa: BLE001
        _update_cr(cr_id, {"status": "needs_human",
                           "impl": {"report": f"internal error: {exc}",
                                    "needs_human": True}})
        raise ChangeRequestError(f"implementer failed: {exc}") from exc
    finally:
        # whatever the outcome, the shared test checkout ends clean on
        # main (a failed/timed-out run must not brick the next one)
        _cleanup_tree(ev, repo, branch)


def recover_interrupted() -> None:
    """Boot-time sweep (wired next to the scheduler): a hard crash strands
    a CR in 'implementing' — the runner is synchronous and does not survive
    a restart. Flip such rows to needs_human; accept_cr allows re-driving
    them from there.

    Вместе со строками подметается и ЖЕЛЕЗО: эфемерный решатель прошлого
    прогона мог пережить перезапуск приложения (0.5). Сирота держит
    архивы артефакта открытыми и ломает чистку папки попытки, поэтому
    сборщик сирот по метке `-Dcosmos.ephemeral=<тег контура>:` зовётся
    здесь же — один раз, отдельно от строк очереди, и никогда не мешает
    подметанию: отказ сборщика сирот только печатается словами.

    Метка — СВОЕГО контура: перезапуск этого приложения не вправе снять
    решатель, которым в эту минуту судит соседний контур той же машины."""
    try:
        from . import evolution_runner as ev
        mod, words = _solver_module_or_words()
        if mod is not None:
            mod.reap_orphans(_contour_marker(mod, ev.test_dir()))
        else:                                            # pragma: no cover
            print(f"COSMOS: сироты решателя не подметены: {words}",
                  flush=True)
    except Exception as exc:                             # noqa: BLE001
        print(f"COSMOS: сироты решателя не подметены: {exc}", flush=True)
    with _lock:
        data = _load()
        changed = False
        for r in data.get("items") or []:
            if r.get("status") == "implementing":
                r["status"] = "needs_human"
                r["updated_at"] = _now()
                impl = r.get("impl") or {}
                impl.setdefault("report", "прерван перезапуском сервера")
                impl["needs_human"] = True
                r["impl"] = impl
                changed = True
        if changed:
            _save_locked(data)


# ---------------------------------------------------------------- scheduler

def _sched_env() -> bool:
    import os
    return os.environ.get("CREW_CR_SCHEDULE", "") == "1"


def run_daily_jobs(now=None) -> dict:
    """Ночная работа планировщика ОДНИМ местом (её и зовёт нить): агент
    отзывов, по понедельникам — регуляторика, и СВЕРКА заявок каталога
    (шаг 6) — решение владельца по заявке доходит до очереди без нажатия
    кнопки, а сверка идемпотентна, повтор ничего не меняет.

    Ни один сбой не останавливает остальных: каждая работа отвечает за
    себя, а итог возвращается словами (нить его печатать не обязана)."""
    now = now or datetime.now()
    out: dict = {}
    try:
        out["feedback"] = run_feedback_agent(triggered_by="schedule")
    except Exception as exc:                         # noqa: BLE001
        out["feedback"] = f"агент отзывов не отработал: {exc}"
    if now.weekday() == 0:                           # Monday
        try:
            out["regulatory"] = run_regulatory_agent(triggered_by="schedule")
        except Exception as exc:                     # noqa: BLE001
            out["regulatory"] = f"агент регуляторики не отработал: {exc}"
    try:
        from . import catalog_pipeline
        out["catalog"] = catalog_pipeline.sync_catalog_requests()
    except Exception as exc:                         # noqa: BLE001
        out["catalog"] = f"сверка заявок каталога не выполнена: {exc}"
    return out


def start_scheduler_thread():
    """Nightly feedback run + weekly regulatory run (Mondays) + the daily
    catalog-request sync, enabled by CREW_CR_SCHEDULE=1 (set on the Test
    contour only)."""
    if not _sched_env():
        return None

    def loop():
        last_day = None
        while True:
            now = datetime.now()
            day = now.date().isoformat()
            if now.hour == 4 and 30 <= now.minute < 40 and day != last_day:
                last_day = day
                run_daily_jobs(now)
            time.sleep(300)

    t = threading.Thread(target=loop, daemon=True,
                         name="cr-agents-scheduler")
    t.start()
    return t
