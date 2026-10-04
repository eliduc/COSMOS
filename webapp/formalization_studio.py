"""Студия формализации (Formalization Studio) — v1 внутри ядра
(2026-07-17, дорожная карта №3 документа COSMOS-DATA-FORMALIZATION-RU,
этапы Ф1/Ф5; в строгом режиме развёртывается внутри контура заказчика).

Сессия формализации ведёт заказчика по конвейеру и накапливает
формализованное знание:

  * ОПРОСНИК ИНВЕНТАРИЗАЦИИ (Ф1): карта систем-источников и источник
    истины по каждому атрибуту — отвечает заказчик, ведёт оператор;
  * ГЛОССАРИЙ СЕМАНТИКИ (Ф5): чек-лист вопросов «уровня 2» (что такое
    база, перегоны, часовые пояса, границы смен, перенос налёта) — каждый
    ответ фиксируется с автором и датой: это решения, а не заметки;
  * СЕССИЯ МАППИНГА: привязка к импортированному инстансу + сохранённый
    план маппинга и заметки (прогресс не теряется между заходами);
  * СВЯЗКА С RULE PACK (Ф7).

Продукт Студии — «Пакет маппинга заказчика» (Customer Mapping Package):
версионируемый JSON со всей цепочкой (опись источников, глоссарий,
план маппинга, полный пакет правил), переносимый между экземплярами
COSMOS и переиспользуемый как шаблон для заказчиков той же страны.

Вопросники — данные продукта (русский язык, как и весь аудит-контур);
ответы хранятся в webapp/config/studio_sessions.json (gitignored,
per-deployment). Никакого ИИ в этом модуле нет: смысл подтверждает
только человек (принцип Ф5).
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone

from . import registry

SESSIONS_PATH = (registry.REPO_ROOT / "webapp" / "config"
                 / "studio_sessions.json")

_lock = threading.Lock()

MAX_ANSWER_CHARS = 2000

# --------------------------------------------------- анкеты (данные продукта)

INVENTORY_QUESTIONS = [
    {"id": "schedule_source",
     "q": "Откуда берётся плановое расписание (система, формат: SSIM / "
          "CSV / интерфейс)?",
     "hint": "источник истины по рейсам"},
    {"id": "crew_source",
     "q": "Где живут данные о персонале (кадровая система, выгрузки)?",
     "hint": "табельные, базы, стаж, типы занятости"},
    {"id": "qual_source",
     "q": "Кто ведёт квалификации/допуски/медицину и в каком виде "
          "выгружает?",
     "hint": "сроки действия, свежесть (recency)"},
    {"id": "history_source",
     "q": "Откуда взять историю налёта и нарядов (предыдущая crew-система)?",
     "hint": "нужна для реплея и переноса налёта"},
    {"id": "pay_source",
     "q": "Где определены правила оплаты (риги, гарантия, ставки)?",
     "hint": "коллективный договор, положения об оплате"},
    {"id": "vacation_source",
     "q": "Как ведутся отпуска и заявки на отпуск?",
     "hint": "квоты, стаж, торги"},
    {"id": "docs_source",
     "q": "Какие документы задают нормы: национальные правила, РПП, "
          "коллективный договор (названия и редакции)?",
     "hint": "входы для пакета правил (Ф7)"},
    {"id": "truth_conflicts",
     "q": "Какая система побеждает при расхождении данных о сотруднике "
          "(кадры vs обучение vs предыдущая crew-система)?",
     "hint": "источник истины на атрибут — решает только заказчик"},
    {"id": "delta_flow",
     "q": "Как будут приходить регулярные изменения (SSM/ASM, повторные "
          "выгрузки, интерфейсы) и с какой частотой?",
     "hint": "этап Ф9 — эксплуатация"},
]

GLOSSARY_QUESTIONS = [
    {"id": "base_meaning",
     "q": "Что считается «базой» экипажа: город, аэропорт или юрлицо? "
          "Возможна ли мультибаза?"},
    {"id": "deadhead_time",
     "q": "Считается ли перегон (deadhead — перелёт членом экипажа "
          "пассажиром к месту работы) рабочим временем и как он "
          "оплачивается?"},
    {"id": "tz_limits",
     "q": "В каком часовом поясе выражены ограничения рабочего времени: "
          "местное время базы, местное время порта или UTC?"},
    {"id": "duty_break",
     "q": "Что прерывает рабочую смену (минимальная пауза, наземное "
          "время, гостиница)?"},
    {"id": "month_carry",
     "q": "Как переносится накопленный налёт и кредит на границе месяца "
          "(обнуление, перенос остатка, скользящее окно)?"},
    {"id": "reserve_rules",
     "q": "Как устроен резерв: виды, где начинается и заканчивается "
          "резервное дежурство, засчитывается ли в налёт?"},
    {"id": "seniority_rule",
     "q": "Как формализован стаж (seniority) и где он применяется "
          "(отпуска, предпочтения, назначения)?"},
    {"id": "shared_flights",
     "q": "Есть ли код-шеринг/совместные рейсы и чьи это наряды?"},
]

_KNOWN = {"inventory": {q["id"] for q in INVENTORY_QUESTIONS},
          "glossary": {q["id"] for q in GLOSSARY_QUESTIONS}}


class StudioError(ValueError):
    """User-facing studio failure."""


# ------------------------------------------------------------------ storage

def _load() -> dict:
    try:
        raw = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"studio store unreadable: {exc}") from exc


def _save_locked(data: dict) -> None:
    SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSIONS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(SESSIONS_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mutate(session_id: str, fn):
    with _lock:
        data = _load()
        for s in data.setdefault("sessions", []):
            if s.get("id") == session_id:
                out = fn(s)
                s["version"] = int(s.get("version") or 0) + 1
                s["updated_at"] = _now()
                _save_locked(data)
                return out if out is not None else s
        raise StudioError(f"no such studio session: {session_id}")


# ------------------------------------------------------------------ queries

def questionnaires() -> dict:
    return {"inventory": INVENTORY_QUESTIONS,
            "glossary": GLOSSARY_QUESTIONS}


def list_sessions() -> list[dict]:
    out = []
    for s in _load().get("sessions") or []:
        inv = (s.get("inventory") or {}).get("answers") or {}
        glo = (s.get("glossary") or {}).get("answers") or {}
        out.append({
            "id": s["id"], "name": s.get("name"),
            "country": s.get("country"), "version": s.get("version"),
            "updated_at": s.get("updated_at"),
            "inventory_done": len(inv),
            "inventory_total": len(INVENTORY_QUESTIONS),
            "glossary_done": len(glo),
            "glossary_total": len(GLOSSARY_QUESTIONS),
            "instance_id": (s.get("mapping") or {}).get("instance_id"),
            "rule_pack_id": s.get("rule_pack_id"),
        })
    out.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return out


def get_session(session_id: str) -> dict | None:
    for s in _load().get("sessions") or []:
        if s.get("id") == session_id:
            return s
    return None


# ----------------------------------------------------------------- commands

def create_session(body: dict, by: str) -> dict:
    name = str(body.get("name") or "").strip()[:120]
    if not name:
        raise StudioError("сессии нужно имя (обычно — название заказчика)")
    s = {"id": "fs" + secrets.token_hex(4), "name": name,
         "country": str(body.get("country") or "").strip()[:8].upper()
         or None,
         "version": 1, "created_by": by,
         "created_at": _now(), "updated_at": _now(),
         "inventory": {"answers": {}},
         "glossary": {"answers": {}},
         "mapping": {"instance_id": None, "plan": None, "notes": ""},
         "rule_pack_id": None}
    with _lock:
        data = _load()
        data.setdefault("sessions", []).append(s)
        _save_locked(data)
    return s


def delete_session(session_id: str) -> None:
    with _lock:
        data = _load()
        ses = data.get("sessions") or []
        keep = [s for s in ses if s.get("id") != session_id]
        if len(keep) == len(ses):
            raise StudioError(f"no such studio session: {session_id}")
        data["sessions"] = keep
        _save_locked(data)


def answer(session_id: str, part: str, question_id: str, text,
           by: str) -> dict:
    """Записать ответ опросника (inventory) или решение глоссария
    (glossary). Пустой текст удаляет ответ. Каждый ответ несёт автора и
    время — это зафиксированное решение, а не черновик."""
    if part not in _KNOWN:
        raise StudioError("part must be 'inventory' or 'glossary'")
    if question_id not in _KNOWN[part]:
        raise StudioError(f"unknown question {question_id!r}")
    text = str(text or "").strip()[:MAX_ANSWER_CHARS]

    def fn(s):
        answers = s.setdefault(part, {}).setdefault("answers", {})
        if not text:
            answers.pop(question_id, None)
        else:
            answers[question_id] = {"answer": text, "by": by, "at": _now()}
        return s
    return _mutate(session_id, fn)


def link_mapping(session_id: str, body: dict) -> dict:
    """Сохранить прогресс сессии маппинга: инстанс (если уже создан),
    план маппинга (JSON из analyze) и заметки оператора."""
    inst_id = body.get("instance_id")
    if inst_id is not None and registry.get_instance(inst_id) is None:
        raise StudioError(f"no such instance: {inst_id}")
    plan = body.get("plan")
    if plan is not None and not isinstance(plan, dict):
        raise StudioError("'plan' must be an object (план из analyze)")
    notes = str(body.get("notes") or "")[:MAX_ANSWER_CHARS]

    def fn(s):
        m = s.setdefault("mapping", {})
        if "instance_id" in body:
            m["instance_id"] = inst_id
        if "plan" in body:
            m["plan"] = plan
        # Only touch notes when the caller sent the field, and let an empty
        # string actually CLEAR them (a cleared note must not resurrect the
        # old text into the exported customer package).
        if "notes" in body:
            m["notes"] = notes
        else:
            m.setdefault("notes", "")
        return s
    return _mutate(session_id, fn)


def link_rule_pack(session_id: str, pack_id) -> dict:
    from . import rule_packs
    if pack_id is not None and rule_packs.get_pack(pack_id) is None:
        raise StudioError(f"no such rule pack: {pack_id}")

    def fn(s):
        s["rule_pack_id"] = pack_id
        return s
    return _mutate(session_id, fn)


# ------------------------------------------- Customer Mapping Package

def assemble_package(session_id: str) -> dict:
    """«Пакет маппинга заказчика»: версионируемый переносимый JSON со всей
    формализацией — опись источников, глоссарий решений, план маппинга,
    полный пакет правил, атрибуты инстанса (страна, llm_scope) и манифест
    Raw-зоны как отпечаток версий данных."""
    s = get_session(session_id)
    if s is None:
        raise StudioError(f"no such studio session: {session_id}")
    from . import raw_zone, rule_packs

    def qa(part, questions):
        answers = (s.get(part) or {}).get("answers") or {}
        return [{"id": q["id"], "question": q["q"],
                 **(answers.get(q["id"]) or {"answer": None})}
                for q in questions]

    inst = None
    raw_manifest = None
    inst_id = (s.get("mapping") or {}).get("instance_id")
    if inst_id:
        inst = registry.get_instance(inst_id)
        raw_manifest = raw_zone.manifest_for(inst_id)
    pack = None
    if s.get("rule_pack_id"):
        p = rule_packs.get_pack(s["rule_pack_id"])
        if p is not None:
            pack = rule_packs.pack_view(p)
    return {
        "package": "customer-mapping-package",
        "format_version": "1",
        "name": s.get("name"), "country": s.get("country"),
        "session_id": s["id"], "session_version": s.get("version"),
        "exported_at": _now(),
        "inventory": qa("inventory", INVENTORY_QUESTIONS),
        "glossary": qa("glossary", GLOSSARY_QUESTIONS),
        "mapping": {
            "instance_id": inst_id,
            "instance": ({"label": inst.get("label"),
                          "source": inst.get("source"),
                          "llm_scope": inst.get("llm_scope"),
                          "country": inst.get("country"),
                          "legs": inst.get("legs"),
                          "employees": inst.get("employees")}
                         if inst else None),
            "plan": (s.get("mapping") or {}).get("plan"),
            "notes": (s.get("mapping") or {}).get("notes"),
            "raw_manifest": raw_manifest,
        },
        "rule_pack": pack,
    }
