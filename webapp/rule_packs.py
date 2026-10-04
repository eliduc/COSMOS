"""Rule Pack — правило как объект с происхождением и тестами
(2026-07-17, дорожная карта №1 документа COSMOS-DATA-FORMALIZATION-RU,
сердце онбординга, этап Ф7).

Каждое правило несёт полную цепочку происхождения:

  исходный документ (страница/пункт)
    → формальное утверждение (одна фраза без двусмысленностей)
      → исполняемое ограничение (параметр Яруса 1)
        → положительные тесты (что должно проходить)
          → отрицательные тесты (что должно отклоняться)
            → утверждение (подпись = email, дата, версия пакета)

Ярус 1 — шесть числовых параметров airline-config (rules_config.DEFAULTS);
правило вида kind=parameter указывает параметр и значение и ИСПОЛНИМО:
тест {given, expect} прогоняется детерминированно (min_* → given >= value,
max_* → given <= value). Правило kind=note — честно помеченное «пока вне
Яруса 1»: формулировка и происхождение фиксируются, тесты описательны
(runner помечает их manual), в компиляцию параметров оно не входит.

Дисциплина утверждения (человек — утверждающий, как во всём конвейере):
  * у правила должен быть >=1 положительный и >=1 отрицательный тест;
  * parameter-правило утверждается только когда ВСЕ его тесты проходят;
  * любая правка правила сбрасывает его утверждение (честность);
  * пакет утверждается только когда утверждено каждое правило;
  * apply-config (перенос в airline-config) — только утверждённый пакет
    без конфликтов параметров.

Пакет переносим: полный JSON пакета (GET) можно импортировать на другом
экземпляре (страновой шаблон — «перенос знания, а не данных»).
"""
from __future__ import annotations

import json
import math
import secrets
import threading
from datetime import datetime, timezone

import rules_config

from . import registry

PACKS_PATH = registry.REPO_ROOT / "webapp" / "config" / "rule_packs.json"

_lock = threading.Lock()

# Ярус 1: исполнимый словарь параметров. ВЫВОДИТСЯ из rules_config, а не
# переписывается: рукописная копия уже отставала — comp_* появились в
# словаре 2026-07-19 и сюда не попали, так что выразить их в пакете
# правил было нельзя, хотя оба движка их принимают.
#
# Правила состава пока исключены СОЗНАТЕЛЬНО: проверка значения ниже
# отвергает `value <= 0`, а у comp_* ноль — это «правило выключено», то
# есть законное и притом дефолтное значение. Пускать их сюда можно
# только вместе с диапазоном допустимых значений на ключ.
# min_* => given >= value, max_* => given <= value.
PARAMETERS = tuple(sorted(
    k for k in rules_config.DEFAULTS
    if k not in rules_config.COMPOSITION_KEYS))

RULE_KINDS = ("parameter", "note", "resource")
TEST_KINDS = ("positive", "negative")

# Ярус 2б (2026-07-24): вид «resource» — правило в формализме школы GERAD
# «ресурс с окном допустимых значений и функцией продления». Хранится как
# данные, солвером пока не исполняется (в compile честно попадает в
# outside_tier1); словари ниже — замкнутые перечни, не свободный текст.
RESOURCE_UNITS = ("minutes", "count", "days")
RESOURCE_LEVELS = ("leg", "duty", "pairing", "day", "week", "month",
                   "rolling28", "year", "roster")

MAX_RULES_PER_PACK = 200
MAX_TESTS_PER_RULE = 20
_CLIP = {"name": 120, "country": 8, "notes": 2000, "title": 160,
         "statement": 600, "document": 200, "section": 120, "url": 400,
         "note": 600, "description": 300, "resource": 120,
         "extension": 300}


class RulePackError(ValueError):
    """Invalid rule-pack operation; message is safe to show the user."""


# ----------------------------------------------------------------- storage

def _load() -> dict:
    try:
        raw = json.loads(PACKS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"rule-pack store unreadable: {exc}") from exc


def _save_locked(data: dict) -> None:
    PACKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PACKS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(PACKS_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(field: str, v) -> str:
    return str(v or "").strip()[:_CLIP[field]]


# ------------------------------------------------------------- validation

def _validate_source(raw) -> dict:
    """Происхождение: документ обязателен — правило без источника это
    «историческая привычка», а не правило."""
    if not isinstance(raw, dict) or not str(raw.get("document") or "").strip():
        raise RulePackError(
            "у правила должен быть источник: {document, section?, url?}")
    return {"document": _clip("document", raw.get("document")),
            "section": _clip("section", raw.get("section")),
            "url": _clip("url", raw.get("url"))}


def _validate_constraint(raw) -> dict:
    if not isinstance(raw, dict):
        raise RulePackError("'constraint' must be an object")
    kind = str(raw.get("kind") or "")
    if kind not in RULE_KINDS:
        raise RulePackError(f"constraint.kind must be one of {RULE_KINDS}")
    if kind == "note":
        return {"kind": "note", "note": _clip("note", raw.get("note"))}
    if kind == "resource":
        return _validate_resource(raw)
    param = str(raw.get("parameter") or "")
    if param not in PARAMETERS:
        raise RulePackError(
            f"unknown parameter {param!r} (Ярус 1: {list(PARAMETERS)})")
    try:
        value = float(raw.get("value"))
    except (TypeError, ValueError):
        raise RulePackError("constraint.value must be a number")
    if not math.isfinite(value):
        raise RulePackError("constraint.value must be finite")
    if value <= 0:
        raise RulePackError("constraint.value must be positive")
    # Ярус 1 принимает только целые значения — дробный параметр роняет Kotlin
    # (M01: раньше 30.5 утверждался, но apply-config давал 422). Проверяем на
    # слое пакета, чтобы нельзя было подписать неприменимое правило.
    if value != int(value):
        raise RulePackError(
            "Ярус 1 принимает только целые значения (дробное значение "
            "нельзя применить к конфигурации правил)")
    # Аудит 2026-07-19 (P1-12): значение за пределами Int32 проходило
    # подпись пакета, а Kotlin RulesConfig.intOf() затем убивал JVM.
    if int(value) > 2_147_483_647:
        raise RulePackError(
            "constraint.value must fit a 32-bit integer "
            "(контракт Kotlin-солвера, максимум 2147483647)")
    return {"kind": "parameter", "parameter": param, "value": value}


def _window_bound(raw, key: str):
    """Граница окна ресурса: None или целое 0..Int32 (контракт как у
    Яруса 1 — дробное или отрицательное значение не подписывается)."""
    v = raw.get(key)
    if v is None:
        return None
    try:
        num = float(v)
    except (TypeError, ValueError):
        raise RulePackError(f"window.{key} must be a number or null")
    if not math.isfinite(num):
        raise RulePackError(f"window.{key} must be finite")
    if num < 0 or num != int(num) or int(num) > 2_147_483_647:
        raise RulePackError(
            f"window.{key} must be a non-negative 32-bit integer")
    return int(num)


def _validate_resource(raw) -> dict:
    """Ярус 2б: {resource, window{min,max}, unit, level, extension}."""
    resource = _clip("resource", raw.get("resource"))
    if not resource:
        raise RulePackError("resource rule needs a 'resource' name")
    unit = str(raw.get("unit") or "")
    if unit not in RESOURCE_UNITS:
        raise RulePackError(
            f"resource.unit must be one of {RESOURCE_UNITS}")
    level = str(raw.get("level") or "")
    if level not in RESOURCE_LEVELS:
        raise RulePackError(
            f"resource.level must be one of {RESOURCE_LEVELS}")
    window = raw.get("window")
    if not isinstance(window, dict):
        raise RulePackError("resource.window must be {min, max}")
    lo = _window_bound(window, "min")
    hi = _window_bound(window, "max")
    if lo is None and hi is None:
        raise RulePackError(
            "resource.window needs at least one bound (min or max)")
    if lo is not None and hi is not None and lo > hi:
        raise RulePackError("resource.window.min must not exceed max")
    return {"kind": "resource", "resource": resource,
            "window": {"min": lo, "max": hi}, "unit": unit,
            "level": level,
            "extension": _clip("extension", raw.get("extension"))}


def _validate_tests(raw) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RulePackError("'tests' must be a list")
    if len(raw) > MAX_TESTS_PER_RULE:
        raise RulePackError(f"too many tests (max {MAX_TESTS_PER_RULE})")
    out = []
    for t in raw:
        if not isinstance(t, dict):
            raise RulePackError("each test must be an object")
        kind = str(t.get("kind") or "")
        if kind not in TEST_KINDS:
            raise RulePackError(f"test.kind must be one of {TEST_KINDS}")
        rec = {"kind": kind,
               "description": _clip("description", t.get("description"))}
        if t.get("given") is not None:
            try:
                rec["given"] = float(t["given"])
            except (TypeError, ValueError):
                raise RulePackError("test.given must be a number")
            if not math.isfinite(rec["given"]):
                raise RulePackError("test.given must be finite")
        out.append(rec)
    return out


def _validate_rule_fields(body) -> dict:
    if not isinstance(body, dict):
        raise RulePackError("rule must be an object")
    title = _clip("title", body.get("title"))
    statement = _clip("statement", body.get("statement"))
    if not title:
        raise RulePackError("rule needs a title")
    if not statement:
        raise RulePackError(
            "rule needs a formal statement (одна фраза без двусмысленностей)")
    return {
        "title": title,
        "statement": statement,
        "source": _validate_source(body.get("source")),
        "constraint": _validate_constraint(body.get("constraint")),
        "tests": _validate_tests(body.get("tests")),
    }


# ------------------------------------------------------------ test runner

def _run_test(constraint: dict, test: dict) -> dict:
    """Deterministic verdict for one test. parameter rules: legality =
    (min_* -> given >= value, max_* -> given <= value); positive expects
    legal, negative expects illegal. note rules: manual."""
    if constraint["kind"] != "parameter":
        return {"status": "manual",
                "detail": "правило вне Яруса 1 — тест проверяется человеком"}
    if "given" not in test:
        return {"status": "fail",
                "detail": "исполняемому тесту нужно число given"}
    param, value, given = (constraint["parameter"], constraint["value"],
                           test["given"])
    legal = given >= value if param.startswith("min_") else given <= value
    expected_legal = (test["kind"] == "positive")
    ok = (legal == expected_legal)
    return {"status": "pass" if ok else "fail",
            "detail": f"given={given:g} vs {param}={value:g} -> "
                      f"{'легально' if legal else 'нелегально'}"}


def rule_test_report(rule: dict) -> dict:
    """{results: [...], pass, fail, manual, ok} — ok means 'approvable':
    both test kinds present and no failing executable test."""
    results = []
    counts = {"pass": 0, "fail": 0, "manual": 0}
    kinds = set()
    for t in rule.get("tests") or []:
        r = _run_test(rule["constraint"], t)
        counts[r["status"]] += 1
        kinds.add(t["kind"])
        results.append({**t, **r})
    ok = ({"positive", "negative"} <= kinds) and counts["fail"] == 0
    return {"results": results, **counts, "ok": ok,
            "has_both_kinds": {"positive", "negative"} <= kinds}


# ---------------------------------------------------------------- queries

def list_packs() -> list[dict]:
    packs = _load().get("packs") or []
    out = []
    for p in packs:
        rules = p.get("rules") or []
        out.append({
            "id": p["id"], "name": p.get("name"),
            "country": p.get("country"), "status": p.get("status"),
            "scope": p.get("scope") or SCOPE_REGULATORY,
            "version": p.get("version"), "rules": len(rules),
            "approved_rules": sum(1 for r in rules
                                  if r.get("status") == "approved"),
            "updated_at": p.get("updated_at"),
        })
    out.sort(key=lambda p: p.get("updated_at") or "", reverse=True)
    return out


def get_pack(pack_id: str) -> dict | None:
    for p in _load().get("packs") or []:
        if p.get("id") == pack_id:
            return p
    return None


def pack_view(pack: dict) -> dict:
    """Full pack + live test reports + compile preview (always fresh —
    reports are pure arithmetic)."""
    rules = []
    for r in pack.get("rules") or []:
        rules.append({**r, "test_report": rule_test_report(r)})
    return {**pack, "rules": rules, "compile": compile_pack(pack)}


def compile_pack(pack: dict) -> dict:
    """Approved parameter rules -> the Ярус-1 parameter dict. Two rules on
    one parameter with different values = a conflict the human resolves;
    note rules are listed honestly as outside tier 1."""
    params: dict[str, float] = {}
    conflicts: list[str] = []
    outside: list[str] = []
    for r in pack.get("rules") or []:
        c = r.get("constraint") or {}
        if c.get("kind") != "parameter":
            outside.append(r.get("title") or r.get("id"))
            continue
        p, v = c["parameter"], c["value"]
        if p in params and params[p] != v:
            conflicts.append(
                f"{p}: {params[p]:g} vs {v:g} (правило «{r.get('title')}»)")
        params[p] = v
    return {"parameters": params, "conflicts": conflicts,
            "outside_tier1": outside,
            "resource_rules": compile_resource_rules(pack)}


# Ресурсные правила Яруса 2б, которые решатель УМЕЕТ исполнять. Ключ —
# имя ресурса в Пакете правил, значение — как это ложится на
# CREW_RESOURCE_RULES (см. kotlin-solver ResourceRules.kt).
EXECUTABLE_RESOURCES = {"weeklyRest": "weeklyRest"}


def compile_resource_rules(pack: dict) -> dict:
    """Ярус 2б -> документ `CREW_RESOURCE_RULES` для решателя.

    До этой правки правило «еженедельный отдых» лежало в Пакете правил как
    ДАННЫЕ, флаг `--resource-rules` у этапа 2 существовал, но продуктовый
    запуск его не передавал — и решатель о правиле не знал (аудит
    2026-07-26, P0-04). Один раз это уже стоило «рекордного» ростера,
    который сертификат разнёс 595 нарушениями.

    Возвращает {"document": {...}|None, "declared": [...],
    "not_executable": [...]}: заявленное, но неисполнимое перечисляется
    поимённо — молчаливое «правило есть в пакете, но не в движке» и есть
    подмена постановки.
    """
    document: dict = {}
    declared: list[str] = []
    not_executable: list[str] = []
    for r in pack.get("rules") or []:
        c = r.get("constraint") or {}
        if c.get("kind") != "resource":
            continue
        name = str(c.get("resource") or "")
        declared.append(name)
        target = EXECUTABLE_RESOURCES.get(name)
        window = c.get("window") or {}
        lo = window.get("min")
        if target is None or lo is None or c.get("unit") != "minutes":
            not_executable.append(name)
            continue
        # Уровень задаёт период накопления ресурса; решатель считает
        # окно в минутах.
        period = {"week": 7 * 1440, "rolling28": 28 * 1440,
                  "day": 1440, "month": 30 * 1440}.get(str(c.get("level")))
        if period is None:
            not_executable.append(name)
            continue
        document[target] = {"requiredMin": int(lo), "periodMin": int(period)}
    return {"document": document or None,
            "declared": sorted(set(declared)),
            "not_executable": sorted(set(not_executable))}


# --------------------------------------------------------------- commands

def _mutate(pack_id: str, fn):
    """Run fn(pack) under the lock; bump version + timestamp; persist."""
    with _lock:
        data = _load()
        packs = data.setdefault("packs", [])
        for p in packs:
            if p.get("id") == pack_id:
                out = fn(p)
                p["version"] = int(p.get("version") or 0) + 1
                p["updated_at"] = _now()
                _save_locked(data)
                return out if out is not None else p
        raise RulePackError(f"no such rule pack: {pack_id}")


SCOPE_REGULATORY = "regulatory"
_SCOPE_INSTANCE_PREFIX = "instance:"


def normalize_scope(value) -> str:
    """Проверить и нормализовать область действия пакета.

    Допустимо: `regulatory` и `instance:<идентификатор набора>`. Всё
    прочее — громкий отказ: молча принятая незнакомая область означала бы
    пакет, который не применяется нигде и об этом не сообщает.

    ОТСУТСТВИЕ значения (`None`) и ПУСТАЯ СТРОКА — разные вещи, и это
    различие поймал тест самой точки запроса. `None` означает «поле не
    задано»: так приходят пакеты прежнего выпуска и создание без явной
    области, и для них историческое поведение — норма страны. Пустая
    строка приходит только из явного запроса «установи область в
    ничто», и это ошибка клиента: молча превращать её в норму страны
    значит менять область действия правила не тем значением, которое
    просили."""
    if value is None:
        return SCOPE_REGULATORY
    if not isinstance(value, str):
        raise RulePackError(f"scope must be a string, got {value!r}")
    v = value.strip()
    if v == SCOPE_REGULATORY:
        return SCOPE_REGULATORY
    if v.startswith(_SCOPE_INSTANCE_PREFIX) and len(v) > len(
            _SCOPE_INSTANCE_PREFIX):
        return v
    raise RulePackError(
        f"scope must be '{SCOPE_REGULATORY}' or "
        f"'{_SCOPE_INSTANCE_PREFIX}<instance-id>', got {value!r}")


def pack_applies_to(meta: dict, inst: dict | None) -> bool:
    """Действует ли пакет на этот набор.

    Пакет набора — только на свой набор. Нормативный пакет — на наборы
    своей страны (пакет без страны действует везде). Пакет прежнего
    выпуска без поля области считается нормативным: обесценивать уже
    заведённые пакеты правка не должна."""
    scope = (meta or {}).get("scope") or SCOPE_REGULATORY
    inst_id = str((inst or {}).get("id") or "")
    if scope.startswith(_SCOPE_INSTANCE_PREFIX):
        return scope[len(_SCOPE_INSTANCE_PREFIX):] == inst_id
    country = str((inst or {}).get("country") or "INTL").upper()
    pack_country = str((meta or {}).get("country") or "").upper()
    return not pack_country or pack_country in ("INTL", country)


def create_pack(body: dict, by: str) -> dict:
    name = _clip("name", body.get("name"))
    if not name:
        raise RulePackError("pack needs a name")
    country = _clip("country", body.get("country")).upper()
    pack = {
        "id": "rp" + secrets.token_hex(4),
        "name": name,
        "country": country or None,
        # Область действия (пятый аудит, R5-04): «regulatory» — норма
        # юрисдикции, действует на все наборы своей страны, как было;
        # «instance:<id>» — правила КОНКРЕТНОГО набора, чужим наборам не
        # видны. Прежде отбор шёл только по стране, и пакет одного
        # арендатора попадал в постановку другого — а при конфликте
        # значений ещё и блокировал ему запуск.
        "scope": normalize_scope(body.get("scope")),
        "notes": _clip("notes", body.get("notes")),
        "status": "draft",
        "version": 1,
        "rules": [],
        "created_by": by,
        "created_at": _now(), "updated_at": _now(),
    }
    with _lock:
        data = _load()
        data.setdefault("packs", []).append(pack)
        _save_locked(data)
    return pack


def set_scope(pack_id: str, scope: str, by: str) -> dict | None:
    """Задать область действия пакета. None — пакета нет.

    Смена области — смена ПОСТАНОВКИ: пакет начинает или перестаёт
    действовать на наборы. Поэтому она, как и правка правил, снимает
    прежнее утверждение: утверждали одно, применяться будет к другому."""
    with _lock:
        data = _load()
        for pack in data.get("packs") or []:
            if pack.get("id") != pack_id:
                continue
            if (pack.get("scope") or SCOPE_REGULATORY) != scope:
                pack["scope"] = scope
                _unapprove_pack(pack)
                pack["updated_at"] = _now()
                pack["scope_set_by"] = by
                _save_locked(data)
            return pack
    return None


def delete_pack(pack_id: str) -> None:
    with _lock:
        data = _load()
        packs = data.get("packs") or []
        keep = [p for p in packs if p.get("id") != pack_id]
        if len(keep) == len(packs):
            raise RulePackError(f"no such rule pack: {pack_id}")
        data["packs"] = keep
        _save_locked(data)


def _unapprove_pack(pack: dict) -> None:
    """Any change to a pack's rules invalidates a prior PACK approval — drop
    the signature too, not just the status, so the UI/export never show
    someone's approval attached to changed content."""
    pack["status"] = "draft"
    pack.pop("approved_by", None)
    pack.pop("approved_at", None)


def add_rule(pack_id: str, body: dict, by: str) -> dict:
    fields = _validate_rule_fields(body)
    rule = {"id": "r" + secrets.token_hex(4), **fields,
            "status": "draft", "created_by": by,
            "created_at": _now(), "updated_at": _now()}

    def fn(pack):
        rules = pack.setdefault("rules", [])
        if len(rules) >= MAX_RULES_PER_PACK:
            raise RulePackError(f"too many rules (max {MAX_RULES_PER_PACK})")
        rules.append(rule)
        _unapprove_pack(pack)             # a new rule un-approves the pack
        return rule
    return _mutate(pack_id, fn)


def update_rule(pack_id: str, rule_id: str, body: dict) -> dict:
    fields = _validate_rule_fields(body)

    def fn(pack):
        for r in pack.get("rules") or []:
            if r.get("id") == rule_id:
                r.update(fields)
                r["status"] = "draft"     # any edit resets the approval
                r.pop("approved_by", None)
                r.pop("approved_at", None)
                r["updated_at"] = _now()
                _unapprove_pack(pack)
                return r
        raise RulePackError(f"no such rule: {rule_id}")
    return _mutate(pack_id, fn)


def delete_rule(pack_id: str, rule_id: str) -> dict:
    def fn(pack):
        rules = pack.get("rules") or []
        keep = [r for r in rules if r.get("id") != rule_id]
        if len(keep) == len(rules):
            raise RulePackError(f"no such rule: {rule_id}")
        pack["rules"] = keep
        _unapprove_pack(pack)
        return pack
    return _mutate(pack_id, fn)


def approve_rule(pack_id: str, rule_id: str, by: str) -> dict:
    def fn(pack):
        for r in pack.get("rules") or []:
            if r.get("id") == rule_id:
                report = rule_test_report(r)
                if not report["has_both_kinds"]:
                    raise RulePackError(
                        "для утверждения нужны и положительный, и "
                        "отрицательный тест")
                if report["fail"]:
                    raise RulePackError(
                        "не все тесты правила проходят — утверждение "
                        "невозможно")
                r["status"] = "approved"
                r["approved_by"] = by
                r["approved_at"] = _now()
                r["updated_at"] = _now()
                return r
        raise RulePackError(f"no such rule: {rule_id}")
    return _mutate(pack_id, fn)


def approve_pack(pack_id: str, by: str) -> dict:
    def fn(pack):
        rules = pack.get("rules") or []
        if not rules:
            raise RulePackError("в пакете нет правил — утверждать нечего")
        pending = [r.get("title") for r in rules
                   if r.get("status") != "approved"]
        if pending:
            raise RulePackError(
                "не утверждены правила: " + ", ".join(map(str, pending[:5])))
        pack["status"] = "approved"
        pack["approved_by"] = by
        pack["approved_at"] = _now()
        return pack
    return _mutate(pack_id, fn)


def import_pack(body: dict, by: str) -> dict:
    """Country-template transfer: a full pack JSON (e.g. exported on our
    side from public regulation) becomes a NEW DRAFT pack here — approvals
    do not travel, the local human re-approves (подпись местная)."""
    if not isinstance(body, dict):
        raise RulePackError("pack import must be an object")
    rules = body.get("rules") or []
    if not isinstance(rules, list):
        raise RulePackError("'rules' must be a list")
    pack = create_pack({"name": body.get("name"),
                        "country": body.get("country"),
                        "notes": body.get("notes")}, by)
    errors = []
    for r in rules:
        try:
            add_rule(pack["id"], r, by)
        except RulePackError as exc:
            errors.append(str(exc))
    out = get_pack(pack["id"])
    return {"pack": out, "imported_rules": len(out.get("rules") or []),
            "errors": errors}
