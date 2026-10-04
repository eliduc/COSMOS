# -*- coding: utf-8 -*-
"""Форма ВМП: общий раздел + части «Пейринг» и «Ростеринг» × (а/б/в).

Спецификация — final-report/VMP-FORMA-CLD.md, схема —
cdpm/schemas/vmp-form.schema.json (решения владельца 2026-09-02:
структура, композиция «критерий + Σ размер×коэффициент» в одной
валюте, ярусы последовательными оптимизациями, место строки по зоне
семьи шаблона, пометка «исключено до получения данных»).

Форма — каноническая ФОРМА ВЫДАЧИ И ПРИЁМКИ ВМП и форма эталона (шаг 1
внедрения). Машинная модель CDPM (cdpm-model.schema.json) строится из
формы и в форму кодом БЕЗ ПОТЕРЬ: поля постановки имеют носители в
модели (scope, tier, tolerance, per, excluded_until_data), поля модели
вне постановки (логика, развилки, блокировки, привязки решателя,
служебные поля посадки) переносятся в `machine` строки и возвращаются.

    to_form(model)   -> form
    from_form(form)  -> model
    validate_form(form, strict=False) -> [нарушения]
    roundtrip_problems(model) -> [] при тождестве канонического слоя
    render_form_md(form) -> текст документа
    render_row_line(row, section) -> запись одной строкой
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from cdpm import model as m
from cdpm import catalog_bridge as _catalog_bridge

FORM_VERSION = 1
FORM_SCHEMA_PATH = Path(__file__).parent / "schemas" / "vmp-form.schema.json"
DEFAULT_CURRENCY = "условные единицы"
DEFAULT_SCOPE = {"pairing": "применяется", "rostering": "применяется"}

# Поля правила модели, которые форма представляет своими полями;
# всё остальное — в machine без изменений (без потерь).
_RULE_OWN = ("id", "title", "statement", "sources", "kind", "zone",
             "weight", "weight_quote", "note", "template_fit",
             "excluded_until_data", "scope")
_FIT_OWN = ("category", "family", "params", "assumption", "quote",
            "registry_rev")
_OBJ_OWN = ("id", "title", "statement", "sources", "zone", "direction",
            "metric", "criterion", "rate", "note", "tier", "tolerance",
            "origin")
PARTS = ("pairing", "rostering")
ZONE_TO_PART = {"PAIRING": "pairing", "ROSTERING": "rostering"}
PART_TO_ZONE = {"pairing": "PAIRING", "rostering": "ROSTERING"}
# ------------------------------------------------------------ вспомогательное

def _families_meta() -> dict:
    """Свойства семей по записям КАТАЛОГА шаблонов (вид, зона, природа,
    единица размера нарушения, класс) — шаг 7: обёртку решателя
    `vmp.template_registry.families_meta()` CDPM больше не читает, а
    запаса кода нет. Без каталога — отказ словами; форму его ловит
    рубеж `validate_form`."""
    return _catalog_bridge.family_traits()


def violation_units() -> tuple:
    """Закрытый словарь единиц размера нарушения КОДАМИ — словарь
    каталога шаблонов (`violation_units`, порядок словаря): текст находки
    формы перечисляет единицы одинаково на любом пути. Шаг 7: ветки
    реестра COSMOS и запаса кода в том же порядке сняты — источник один.
    Каталог 0.5.0 записывает словарь картой «код → слово»; членство
    по-прежнему судится по коду, слово даёт `violation_unit_words()`."""
    return _catalog_bridge.vocab("violation_units")


def violation_unit_words() -> dict:
    """Слова единиц размера нарушения (код → русское слово) из словаря
    каталога: ими документ ВМП называет штраф. Рукописной карты перевода
    в CDPM нет — носитель слова один, рядом с кодом."""
    return _catalog_bridge.violation_unit_words()


def unit_word(unit) -> str | None:
    """Слово единицы для документа; единица не задана — None. Единица вне
    словаря каталога отдаётся как есть: отказ по ней ставит рубеж формы
    (`validate_form`), а рендер не вправе молча её потерять."""
    if unit in (None, ""):
        return None
    return violation_unit_words().get(str(unit), str(unit))


def _violation_unit(fam, fams_meta: dict):
    spec = fams_meta.get(fam) if fam else None
    if isinstance(spec, dict):
        return spec.get("violation_unit") or None
    return None


def _norm(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def _rule_place(rule: dict) -> tuple:
    """(раздел формы, часть|None) по виду и зоне правила модели."""
    kind, zone = rule.get("kind"), rule.get("zone")
    if kind in ("DEF", "DATA"):
        return ("definitions", None)
    if kind == "OUT":
        return ("withdrawn", None)
    if zone == "BOTH":
        return ("world", None)
    part = ZONE_TO_PART.get(zone)
    if part is None:
        return ("world", None)
    return ("soft" if kind == "SOFT" else "hard", part)


def _answer_revision(model: dict, qid) -> int:
    for q in model.get("questions", []) or []:
        if isinstance(q, dict) and q.get("id") == qid:
            rev = (q.get("answer") or {}).get("revision")
            return int(rev) if isinstance(rev, int) else 1
    return 1


# ------------------------------------------------------------- модель → форма

def _fit_to_template(fit) -> tuple:
    """(template|None, gap|None, machine_fit) из template_fit модели."""
    if not isinstance(fit, dict):
        return None, None, None
    cat = fit.get("category")
    if cat in ("A", "B") and fit.get("family"):
        tpl = {"family": fit["family"], "category": cat}
        if fit.get("assumption"):
            tpl["assumption"] = fit["assumption"]
        if fit.get("quote"):
            tpl["quote"] = fit["quote"]
        extra = {k: v for k, v in fit.items() if k not in _FIT_OWN}
        mach = {}
        if fit.get("registry_rev"):
            mach["registry_rev"] = fit["registry_rev"]
        if extra:
            mach["extra"] = copy.deepcopy(extra)
        if "params" in fit:
            # The visible value/quote/unit remain authoritative. Preserve
            # the other typed carriers, including exact answer provenance,
            # and the distinction between absent and explicitly empty params.
            mach["param_cells"] = {
                name: {k: copy.deepcopy(v) for k, v in cell.items()
                       if k not in ("value", "quote")}
                for name, cell in (fit.get("params") or {}).items()
                if isinstance(cell, dict)}
        return tpl, None, (mach or None)
    if cat == "C" and set(fit.keys()) <= {"category", "gap", "registry_rev"}:
        # Чистая посадка C: template null + gap — восстанавливается из
        # самой формы (ревью r-форма: без machine строка теряла посадку).
        mach = {"registry_rev": fit["registry_rev"]} if fit.get("registry_rev") else None
        return None, (fit.get("gap") or "шаблона нет"), mach
    # D / кривая посадка — целиком в machine, шаблона в форме нет.
    return None, (fit.get("gap") or None), {"full": copy.deepcopy(fit)}


def _params_to_form(fit) -> list:
    out = []
    for name, cell in ((fit or {}).get("params") or {}).items():
        row = {"name": str(name),
               "value": cell.get("value") if isinstance(cell, dict) else cell,
               "quote": str((cell or {}).get("quote") or "")
               if isinstance(cell, dict) else ""}
        # Единица: параметр словаря либо условие семьи (каталог, шаг 2).
        unit = (cell.get("unit") if isinstance(cell, dict) and "unit" in cell
                else m.fit_param_unit((fit or {}).get("family"), name))
        if unit:
            row["unit"] = unit
        out.append(row)
    return out


def _rule_to_row(rule: dict, fams_meta: dict, world: bool = False) -> dict:
    fit = rule.get("template_fit")
    tpl, gap, mach_fit = _fit_to_template(fit)
    row = {"id": rule["id"], "title": rule.get("title"),
           "statement": rule.get("statement"),
           "sources": copy.deepcopy(rule.get("sources") or []),
           "template": tpl}
    if gap:
        row["gap"] = gap
    params = _params_to_form(fit) if tpl else []
    if params:
        row["params"] = params
    machine: dict = {}
    if rule.get("kind") == "SOFT":
        row["penalty"] = {
            "coefficient": rule.get("weight"),
            "violation_unit": _violation_unit(
                (tpl or {}).get("family"), fams_meta)}
        if row["penalty"]["coefficient"] is None:
            row["penalty"]["coefficient"] = 0
            machine["weight_missing"] = True
        if rule.get("weight_quote"):
            row["penalty"]["as_written"] = rule["weight_quote"]
    if rule.get("kind") == "CASE":
        row["case"] = True
    if rule.get("note"):
        row["note"] = rule["note"]
    if isinstance(rule.get("excluded_until_data"), dict):
        row["excluded_until_data"] = copy.deepcopy(rule["excluded_until_data"])
    if isinstance(rule.get("scope"), dict):
        row["scope"] = copy.deepcopy(rule["scope"])
    elif world:
        # Параметр мира действует в обеих задачах; область словами —
        # по умолчанию «применяется»; уточнение при приёмке уезжает в
        # модель полем scope (без потерь).
        row["scope"] = dict(DEFAULT_SCOPE)
    for k, v in rule.items():
        if k not in _RULE_OWN:
            machine[k] = copy.deepcopy(v)
    machine["kind"] = rule.get("kind")
    if mach_fit:
        machine["template_fit"] = mach_fit
    row["machine"] = machine
    return row


def _definition_to_row(rule: dict) -> dict:
    row = {"id": rule["id"], "title": rule.get("title"),
           "statement": rule.get("statement"),
           "sources": copy.deepcopy(rule.get("sources") or []),
           "role": "registry" if rule.get("kind") == "DATA" else "term"}
    if rule.get("note"):
        row["note"] = rule["note"]
    if isinstance(rule.get("excluded_until_data"), dict):
        row["excluded_until_data"] = copy.deepcopy(rule["excluded_until_data"])
    machine = {k: copy.deepcopy(v) for k, v in rule.items()
               if k not in _RULE_OWN}
    machine["zone"] = rule.get("zone")
    if rule.get("template_fit") is not None:
        machine["template_fit"] = copy.deepcopy(rule["template_fit"])
    row["machine"] = machine
    return row


def _withdrawn_to_row(rule: dict) -> dict:
    row = {"id": rule["id"], "title": rule.get("title"),
           "statement": rule.get("statement"),
           "sources": copy.deepcopy(rule.get("sources") or []),
           "reason": str(rule.get("note") or "")}
    if isinstance(rule.get("excluded_until_data"), dict):
        row["excluded_until_data"] = copy.deepcopy(rule["excluded_until_data"])
    machine = {k: copy.deepcopy(v) for k, v in rule.items()
               if k not in _RULE_OWN}
    machine["zone"] = rule.get("zone")
    if rule.get("template_fit") is not None:
        machine["template_fit"] = copy.deepcopy(rule["template_fit"])
    if rule.get("weight") is not None:
        machine["weight"] = rule["weight"]
    if isinstance(rule.get("scope"), dict):
        machine["scope"] = copy.deepcopy(rule["scope"])
    row["machine"] = machine
    return row


def _objective_to_criterion(o: dict, model: dict) -> dict:
    row = {"id": o["id"], "title": o.get("title"),
           "statement": o.get("statement"),
           "sources": copy.deepcopy(o.get("sources") or []),
           "direction": o.get("direction"),
           "tier": int(o.get("tier") or 1)}
    origin = o.get("origin")
    if isinstance(origin, dict) and origin.get("question"):
        # Источник-ответ (решение 5): целевая, рождённая ответом
        # авиакомпании, несёт его как источник рядом с якорями.
        row["sources"].append({"question": str(origin["question"]),
                               "revision": int(origin.get("revision") or 1)})
    if isinstance(o.get("tolerance"), dict):
        row["tolerance"] = copy.deepcopy(o["tolerance"])
    if o.get("metric"):
        row["metric"] = o["metric"]
    c = o.get("criterion")
    rate: dict = {}
    if isinstance(c, dict):
        row["kind"] = c.get("kind")
        if c.get("rate_per_duty") is not None:
            rate["value"] = c["rate_per_duty"]
        if c.get("per"):
            rate["per"] = c["per"]
        if isinstance(c.get("rates"), dict):
            rate["rates"] = {"flight": c["rates"].get("flight"),
                             "work": c["rates"].get("work")}
        if c.get("as_written"):
            rate["as_written"] = c["as_written"]
        if c.get("version"):
            row["version"] = c["version"]
        if c.get("effective_from"):
            row["effective_from"] = c["effective_from"]
    elif isinstance(o.get("rate"), dict):
        # Немашинный критерий: ставка — цена единицы метрики в валюте
        # формы (решение 5: строка в) несёт ставку).
        rate = copy.deepcopy(o["rate"])
    if rate:
        row["rate"] = rate
    if o.get("note"):
        row["note"] = o["note"]
    machine = {k: copy.deepcopy(v) for k, v in o.items()
               if k not in _OBJ_OWN}
    machine["zone"] = o.get("zone")
    row["machine"] = machine
    return row


def to_form(model: dict, currency: str | None = None) -> dict:
    """Форма выдачи/приёмки из машинной модели (без потерь). Валюта —
    атрибут ВМП из meta.currency (мета-предложение документа); явный
    аргумент главнее; без обоих — умолчание формы."""
    fams_meta = _families_meta()
    meta = dict(model.get("meta") or {})
    form = {
        "form_version": FORM_VERSION,
        "customer": str(meta.get("customer") or "—"),
        "currency": currency or str(meta.get("currency") or DEFAULT_CURRENCY),
        "meta": copy.deepcopy(meta),
        "sources": copy.deepcopy(model.get("sources") or []),
        "common": {"definitions": [], "world": []},
        "pairing": {"hard": [], "soft": [], "criteria": []},
        "rostering": {"hard": [], "soft": [], "criteria": []},
        "withdrawn": [],
        "open_questions": [],
        "answered_questions": [],
    }
    revs = {str((r.get("template_fit") or {}).get("registry_rev"))
            for r in model.get("rules", [])
            if isinstance(r.get("template_fit"), dict)
            and (r.get("template_fit") or {}).get("registry_rev")}
    form["registry_rev"] = (sorted(revs)[0] if len(revs) == 1
                            else m.registry_fingerprint())
    for rule in model.get("rules", []):
        section, part = _rule_place(rule)
        if section == "definitions":
            form["common"]["definitions"].append(_definition_to_row(rule))
        elif section == "withdrawn":
            form["withdrawn"].append(_withdrawn_to_row(rule))
        elif section == "world":
            form["common"]["world"].append(
                _rule_to_row(rule, fams_meta, world=True))
        else:
            form[part][section].append(_rule_to_row(rule, fams_meta))
    for o in model.get("objectives", []):
        crit = _objective_to_criterion(o, model)
        zone = o.get("zone")
        if zone == "BOTH":
            form["pairing"]["criteria"].append(copy.deepcopy(crit))
            form["rostering"]["criteria"].append(copy.deepcopy(crit))
        else:
            form[ZONE_TO_PART.get(zone, "pairing")]["criteria"].append(crit)
    for q in model.get("questions", []):
        status = (q.get("answer") or {}).get("status") \
            if isinstance(q.get("answer"), dict) else None
        # Приложение «применённые» — только внесённые ответы; всё
        # остальное (нет ответа, не разобран, ожидает обработчика) —
        # открытое: готовый ВМП такого не несёт (ревью r-форма).
        # «Пересмотрен» (A3.4) — закрытая история ответа: открыт его
        # вопрос-пересмотр, а не он сам.
        if status in ("применён", "пересмотрен"):
            form["answered_questions"].append(copy.deepcopy(q))
        else:
            form["open_questions"].append(copy.deepcopy(q))
    return form


# ------------------------------------------------------------- форма → модель

def _template_to_fit(row: dict, form_rev) -> dict | None:
    machine = row.get("machine") or {}
    mach_fit = machine.get("template_fit") or {}
    if isinstance(mach_fit, dict) and "full" in mach_fit:
        return copy.deepcopy(mach_fit["full"])
    tpl = row.get("template")
    rev = mach_fit.get("registry_rev") or form_rev or m.registry_fingerprint()
    if not isinstance(tpl, dict):
        if row.get("gap"):
            return {"category": "C", "registry_rev": rev, "gap": row["gap"]}
        return None
    fit = {"category": tpl["category"], "registry_rev": rev,
           "family": tpl["family"]}
    params = {}
    cell_meta = mach_fit.get("param_cells") or {}
    for p in row.get("params") or []:
        cell = copy.deepcopy(cell_meta.get(p["name"]) or {})
        cell.update(value=copy.deepcopy(p.get("value")), quote=p.get("quote"))
        if "unit" in cell or (mach_fit.get("extra") or {}).get("schema_version") == "landing/2":
            cell["unit"] = p.get("unit")
        params[p["name"]] = cell
    if params or "param_cells" in mach_fit:
        fit["params"] = params
    if tpl.get("assumption"):
        fit["assumption"] = tpl["assumption"]
    if tpl.get("quote"):
        fit["quote"] = tpl["quote"]
    for k, v in (mach_fit.get("extra") or {}).items():
        if k not in _FIT_OWN:
            fit[k] = copy.deepcopy(v)
    return fit


def _doc_sources(sources) -> list:
    """Источники модели — только документные {sid, anchor} (источник-
    ответ формы возвращается в origin целевой, у правил — след в note)."""
    return [copy.deepcopy(s) for s in (sources or [])
            if isinstance(s, dict) and "sid" in s]


def _row_to_rule(row: dict, kind: str, zone: str, form_rev,
                 world: bool = False) -> dict:
    machine = dict(row.get("machine") or {})
    machine.pop("zone", None)              # зона — по месту строки (§7)
    machine.pop("kind", None)
    rule = {"id": row["id"], "title": row.get("title"),
            "statement": row.get("statement"),
            "zone": zone, "kind": kind,
            "sources": _doc_sources(row.get("sources"))}
    fit = _template_to_fit(row, form_rev)
    machine.pop("template_fit", None)
    if fit is not None:
        rule["template_fit"] = fit
    pen = row.get("penalty")
    weight_missing = machine.pop("weight_missing", False)
    if kind == "SOFT" and isinstance(pen, dict) and not weight_missing:
        rule["weight"] = pen.get("coefficient")
        if pen.get("as_written"):
            rule["weight_quote"] = pen["as_written"]
    if row.get("note"):
        rule["note"] = row["note"]
    if isinstance(row.get("excluded_until_data"), dict):
        rule["excluded_until_data"] = copy.deepcopy(row["excluded_until_data"])
    if isinstance(row.get("scope"), dict) and (
            not world or row["scope"] != DEFAULT_SCOPE):
        rule["scope"] = copy.deepcopy(row["scope"])
    for k, v in machine.items():
        rule[k] = copy.deepcopy(v)
    return rule


def _definition_to_rule(row: dict) -> dict:
    machine = dict(row.get("machine") or {})
    rule = {"id": row["id"], "title": row.get("title"),
            "statement": row.get("statement"),
            "zone": machine.pop("zone", None) or "BOTH",
            "kind": "DATA" if row.get("role") == "registry" else "DEF",
            "sources": _doc_sources(row.get("sources"))}
    if row.get("note"):
        rule["note"] = row["note"]
    if isinstance(row.get("excluded_until_data"), dict):
        rule["excluded_until_data"] = copy.deepcopy(row["excluded_until_data"])
    for k, v in machine.items():
        rule[k] = copy.deepcopy(v)
    return rule


def _withdrawn_to_rule(row: dict) -> dict:
    machine = dict(row.get("machine") or {})
    rule = {"id": row["id"], "title": row.get("title"),
            "statement": row.get("statement"),
            "zone": machine.pop("zone", None) or "BOTH",
            "kind": "OUT",
            "sources": _doc_sources(row.get("sources"))}
    if row.get("reason"):
        rule["note"] = row["reason"]
    if isinstance(row.get("excluded_until_data"), dict):
        rule["excluded_until_data"] = copy.deepcopy(row["excluded_until_data"])
    for k, v in machine.items():
        rule[k] = copy.deepcopy(v)
    return rule


def _criterion_to_objective(row: dict, zone: str) -> dict:
    machine = dict(row.get("machine") or {})
    machine.pop("zone", None)
    o = {"id": row["id"], "title": row.get("title"),
         "statement": row.get("statement"),
         "zone": zone,
         "direction": row.get("direction"),
         "sources": _doc_sources(row.get("sources"))}
    answer_src = next((s for s in (row.get("sources") or [])
                       if isinstance(s, dict) and "question" in s), None)
    if answer_src is not None:
        o["origin"] = {"question": answer_src["question"],
                       "revision": int(answer_src.get("revision") or 1)}
    if row.get("metric"):
        o["metric"] = row["metric"]
    if int(row.get("tier") or 1) != 1:
        o["tier"] = int(row["tier"])
    if isinstance(row.get("tolerance"), dict):
        o["tolerance"] = copy.deepcopy(row["tolerance"])
    rate = row.get("rate") or {}
    if row.get("kind"):
        c = {"kind": row["kind"]}
        if rate.get("value") is not None:
            c["rate_per_duty"] = rate["value"]
        if rate.get("per"):
            c["per"] = rate["per"]
        if isinstance(rate.get("rates"), dict):
            c["rates"] = dict(rate["rates"])
        if rate.get("as_written"):
            c["as_written"] = rate["as_written"]
        if row.get("version"):
            c["version"] = row["version"]
        if row.get("effective_from"):
            c["effective_from"] = row["effective_from"]
        o["criterion"] = c
    elif rate:
        o["rate"] = {k: copy.deepcopy(v) for k, v in rate.items()
                     if k in ("value", "per", "as_written")}
    if row.get("note"):
        o["note"] = row["note"]
    for k, v in machine.items():
        o[k] = copy.deepcopy(v)
    return o


def from_form(form: dict) -> dict:
    """Машинная модель из формы (обратное построение без потерь): зона
    — по месту строки, вид — по разделу, вес — из penalty, критерий —
    из раздела в); целевая, стоящая в обеих частях, — зоны BOTH."""
    rev = form.get("registry_rev")
    fams_meta = _families_meta()
    model = {"meta": copy.deepcopy(form.get("meta") or {
        "customer": form.get("customer"), "mode": 1,
        "cdpm_version": "form-1"}),
        "sources": copy.deepcopy(form.get("sources") or []),
        "rules": [], "questions": [], "objectives": []}
    model["meta"].setdefault("customer", form.get("customer"))
    common = form.get("common") or {}
    for row in common.get("definitions") or []:
        model["rules"].append(_definition_to_rule(row))
    for row in common.get("world") or []:
        machine = row.get("machine") or {}
        kind = machine.get("kind")
        if kind not in ("HARD", "SOFT", "CASE"):
            fam = (row.get("template") or {}).get("family")
            kind = ((fams_meta.get(fam) or {}).get("kind") if fam else None) \
                or ("SOFT" if row.get("penalty") else "HARD")
        model["rules"].append(_row_to_rule(row, kind, "BOTH", rev, world=True))
    crit_ids_by_part = {part: {c.get("id") for c in
                               ((form.get(part) or {}).get("criteria") or [])}
                        for part in PARTS}
    seen_obj: set = set()
    for part in PARTS:
        block = form.get(part) or {}
        zone = PART_TO_ZONE[part]
        for row in block.get("hard") or []:
            kind = "CASE" if row.get("case") else "HARD"
            model["rules"].append(_row_to_rule(row, kind, zone, rev))
        for row in block.get("soft") or []:
            model["rules"].append(_row_to_rule(row, "SOFT", zone, rev))
        for row in block.get("criteria") or []:
            if row["id"] in seen_obj:
                continue
            seen_obj.add(row["id"])
            both = all(row["id"] in crit_ids_by_part[p] for p in PARTS)
            model["objectives"].append(
                _criterion_to_objective(row, "BOTH" if both else zone))
    for row in form.get("withdrawn") or []:
        model["rules"].append(_withdrawn_to_rule(row))
    model["questions"] = ([copy.deepcopy(q) for q in form.get("answered_questions") or []]
                          + [copy.deepcopy(q) for q in form.get("open_questions") or []])
    return model


# ------------------------------------------------------------------ проверки

def validate_form_schema(form: dict) -> list[str]:
    import jsonschema
    schema = json.loads(FORM_SCHEMA_PATH.read_text(encoding="utf-8"))
    v = jsonschema.Draft7Validator(schema)
    return [f"схема формы: {'/'.join(str(p) for p in e.absolute_path) or '<корень>'}: "
            f"{e.message}" for e in sorted(v.iter_errors(form), key=str)]


def validate_form(form: dict, strict: bool = False,
                  clause_texts: dict | None = None) -> list[str]:
    """Проверки формы §8 спецификации. strict — готовый ВМП: открытых
    вопросов быть не должно, каждая единица нарушения задана.
    clause_texts {(sid, anchor): текст} — тексты клауз заморозки: цитаты
    посадки и параметров судятся против формулировки строки ИЛИ клаузы-
    источника (план «прямой ход» 2.3: формулировка — формулировка ВМП,
    биекцию несёт цитата из клаузы); без них — только формулировка
    (рукописный эталон).

    РУБЕЖ (шаг 7 каталога): без каталога шаблонов словари и семьи брать
    негде — ОДНА строка проблемы словами, а не трассировка."""
    words = _catalog_bridge.problem_or_none()
    if words:
        return [words]
    problems = validate_form_schema(form)
    if problems:
        return problems
    fams = m.template_families()
    traits = m.template_family_traits()
    fams_meta = _families_meta()
    units = violation_units()
    seen_rows: set = set()        # правила, определения, снятые
    # Use the same typed/provenance judges as the model. A displayed answer
    # quote need not occur in the original clause, but must resolve to the
    # exact applied questionnaire answer and confirm this value and target.
    from cdpm import provenance
    try:
        restored = from_form(form)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        return [f"машинный слой формы не восстанавливается: {error}"]
    restored_rules = {r["id"]: r for r in restored["rules"]}
    typed = [r for r in restored["rules"]
             if "schema_version" in (r.get("template_fit") or {})]
    if typed:
        problems.extend(m.validate_template_fit({**restored, "rules": typed}))

    def _row_checks(row, where, kind_expected, zone_expected):
        rid = row.get("id")
        if rid in seen_rows:
            problems.append(f"{where}: повтор идентификатора {rid!r}")
        seen_rows.add(rid)
        tpl = row.get("template")
        if tpl is None and not row.get("gap"):
            problems.append(f"{where} {rid}: без шаблона и без описания "
                            f"пробела (gap)")
        fam = None
        if isinstance(tpl, dict):
            fam = tpl.get("family")
            if fams and fam not in fams:
                problems.append(f"{where} {rid}: семья {fam!r} вне среза "
                                f"базы шаблонов")
            tr = traits.get(fam) or {}
            if zone_expected and tr.get("zone") and tr["zone"] != zone_expected:
                problems.append(f"{where} {rid}: семья {fam!r} зоны "
                                f"{tr['zone']} стоит в разделе зоны "
                                f"{zone_expected} — место задаёт семья")
            if kind_expected and tr.get("kind") and tr["kind"] != kind_expected:
                problems.append(f"{where} {rid}: семья {fam!r} вида "
                                f"{tr['kind']} стоит в разделе вида "
                                f"{kind_expected}")
            if tpl.get("category") == "B" and not tpl.get("assumption"):
                problems.append(f"{where} {rid}: посадка B без допущения")
            if fams and fam in fams and fams[fam] and not row.get("params"):
                problems.append(f"{where} {rid}: семья {fam!r} со "
                                f"словарными параметрами без params")
        stmt = _norm(row.get("statement"))
        evidence = [stmt]
        if clause_texts:
            for s in row.get("sources") or []:
                if isinstance(s, dict):
                    t = clause_texts.get((s.get("sid"), s.get("anchor")))
                    if t:
                        evidence.append(_norm(t))
        where_quote = ("ни формулировки строки, ни клаузы-источника"
                       if clause_texts else "формулировки строки")

        def _quoted(q) -> bool:
            nq = _norm(q)
            return any(nq in ev for ev in evidence)
        conds_all = m.family_conditions()
        seen_params = set()
        restored_fit = (restored_rules.get(rid) or {}).get("template_fit") or {}
        for p in row.get("params") or []:
            name = p.get("name")
            if name in seen_params:
                problems.append(f"{where} {rid}: повтор параметра {name!r}")
            seen_params.add(name)
            cond = conds_all.get(str(name)) if name not in m.PARAMS else None
            own_cond = cond is not None and cond.get("family") == fam
            unit_dict = m.fit_param_unit(fam, name)
            if name not in m.PARAMS and cond is None:
                problems.append(f"{where} {rid}: параметр {name!r} вне словаря")
            elif cond is not None and not own_cond:
                problems.append(f"{where} {rid}: условие {name!r} принадлежит "
                                f"семье {cond.get('family')!r}, не {fam!r}")
            elif p.get("unit") and unit_dict and p["unit"] != unit_dict:
                problems.append(f"{where} {rid}: единица параметра {name!r} "
                                f"{p['unit']!r} ≠ словарной "
                                f"{unit_dict!r}")
            if own_cond and restored_fit.get("schema_version") != "landing/2":
                bad = m.condition_value_problem(name, cond.get("unit"),
                                                p.get("value"))
                if bad:
                    problems.append(f"{where} {rid}: {bad}")
            elif not own_cond and fams and fam in fams and fams[fam] and name not in fams[fam]:
                problems.append(f"{where} {rid}: параметр {name!r} не "
                                f"принадлежит семье {fam!r}")
            if isinstance(p.get("value"), (list, dict)) and restored_fit.get("schema_version") != "landing/2":
                problems.append(f"{where} {rid}: структурный параметр {name!r} "
                                "требует типизированного контракта landing/2")
            cell = (restored_fit.get("params") or {}).get(name)
            if provenance.is_answer_cell(cell):
                problems.extend(provenance.cell_problems(restored, rid, fam, name, cell))
            elif not _quoted(p.get("quote")):
                problems.append(f"{where} {rid}: цитата параметра "
                                f"{name!r} не является фрагментом "
                                f"{where_quote}")
        if isinstance(tpl, dict) and tpl.get("quote") \
                and not _quoted(tpl["quote"]):
            problems.append(f"{where} {rid}: цитата-носитель посадки не "
                            f"является фрагментом {where_quote}")
        if kind_expected == "SOFT":
            pen = row.get("penalty")
            if not isinstance(pen, dict) \
                    or (row.get("machine") or {}).get("weight_missing"):
                problems.append(f"{where} {rid}: мягкое правило без "
                                f"коэффициента штрафа")
            else:
                unit = pen.get("violation_unit")
                if unit in (None, ""):
                    problems.append(f"{where} {rid}: единица размера "
                                    f"нарушения не задана")
                elif unit not in units:
                    problems.append(f"{where} {rid}: единица размера "
                                    f"нарушения {unit!r} вне словаря "
                                    f"{list(units)}")
                else:
                    expected = _violation_unit(fam, fams_meta)
                    if expected and expected != unit:
                        problems.append(f"{where} {rid}: единица {unit!r} не "
                                        f"совпадает с единицей семьи "
                                        f"{fam!r} ({expected!r}) — единицу "
                                        f"задаёт семья, документ её только "
                                        f"подтверждает")
        elif row.get("penalty") is not None:
            problems.append(f"{where} {rid}: коэффициент штрафа у жёсткого "
                            f"правила")
        if zone_expected is None and not isinstance(row.get("scope"), dict):
            problems.append(f"{where} {rid}: параметр мира без области "
                            f"действия (scope) для задач")

    for row in (form.get("common") or {}).get("definitions") or []:
        rid = row.get("id")
        if rid in seen_rows:
            problems.append(f"общий/определения: повтор идентификатора {rid!r}")
        seen_rows.add(rid)
    for row in (form.get("common") or {}).get("world") or []:
        tpl = row.get("template") or {}
        tr = traits.get(tpl.get("family")) or {}
        kind_expected = tr.get("kind") or ((row.get("machine") or {}).get("kind"))
        _row_checks(row, "общий/параметры мира", kind_expected, None)
        if tr.get("zone") and tr["zone"] != "BOTH":
            problems.append(f"общий/параметры мира {row.get('id')}: семья "
                            f"{tpl.get('family')!r} зоны {tr['zone']} — "
                            f"место в части, не в общем разделе")
    for row in form.get("withdrawn") or []:
        rid = row.get("id")
        if rid in seen_rows:
            problems.append(f"снятые: повтор идентификатора {rid!r}")
        seen_rows.add(rid)
    crit_seen: dict = {}
    for part in PARTS:
        block = form.get(part) or {}
        zone = PART_TO_ZONE[part]
        present = any(block.get(k) for k in ("hard", "soft", "criteria"))
        if present and not block.get("hard"):
            problems.append(f"{part}: раздел а) пуст, а часть присутствует")
        for row in block.get("hard") or []:
            _row_checks(row, f"{part}/а", "HARD" if not row.get("case") else None,
                        zone)
        for row in block.get("soft") or []:
            _row_checks(row, f"{part}/б", "SOFT", zone)
        tiers = []
        for c in block.get("criteria") or []:
            cid = c.get("id")
            other = crit_seen.get(cid)
            if cid in seen_rows or cid in crit_seen.get(part, set()):
                problems.append(f"{part}/в: повтор идентификатора {cid!r}")
            elif other and other != part:
                # Целевая обеих задач законно стоит в двух частях, если
                # это одна и та же запись (без учёта служебного блока).
                twin = next((x for x in (form.get(other) or {}).get("criteria", [])
                             if x.get("id") == cid), None)
                strip = lambda x: {k: v for k, v in x.items()  # noqa: E731
                                   if k != "machine"}
                if twin is None or strip(twin) != strip(c):
                    problems.append(f"{part}/в: критерий {cid!r} стоит в "
                                    f"обеих частях с разным содержанием")
            crit_seen.setdefault(part, set()).add(cid)
            crit_seen[cid] = part if not other else other
            if c.get("metric") and c["metric"] not in m.METRICS:
                problems.append(f"{part}/в {cid}: метрика {c['metric']!r} "
                                f"вне словаря")
            rate = c.get("rate") or {}
            if rate.get("value") is None and not isinstance(rate.get("rates"), dict):
                problems.append(f"{part}/в {cid}: критерий без ставки в "
                                f"валюте формы (решение 5: строка в) несёт "
                                f"ставку)")
            if c.get("kind"):
                if c["kind"] not in m.CRITERION_KINDS.values():
                    problems.append(f"{part}/в {cid}: вид критерия "
                                    f"{c['kind']!r} вне словаря")
                if c["kind"] == "min_person_days" and rate.get("value") is None:
                    problems.append(f"{part}/в {cid}: критерий человеко-дней "
                                    f"без ставки за день")
                if c["kind"] == "rank_normalized_cost" \
                        and not isinstance(rate.get("rates"), dict):
                    problems.append(f"{part}/в {cid}: критерий без ставок "
                                    f"рангов")
                if not c.get("version") or not c.get("effective_from"):
                    problems.append(f"{part}/в {cid}: критерий без версии "
                                    f"или даты действия ставок")
            tier = int(c.get("tier") or 1)
            tiers.append(tier)
            if tier > 1 and not c.get("tolerance"):
                problems.append(f"{part}/в {cid}: ярус {tier} без допуска "
                                f"к предыдущему ярусу")
        if tiers and sorted(set(tiers)) != list(range(1, max(tiers) + 1)):
            problems.append(f"{part}/в: ярусы критериев с пропусками: "
                            f"{sorted(set(tiers))}")
        dups = sorted({t for t in tiers if tiers.count(t) > 1})
        if dups:
            problems.append(f"{part}/в: несколько критериев на одном ярусе "
                            f"{dups} — на ярусе один критерий (композиция: "
                            f"критерий яруса + штрафы)")
    if strict and form.get("open_questions"):
        problems.append(f"открытых вопросов {len(form['open_questions'])} — "
                        f"готовый ВМП вопросов не несёт")
    return problems


def roundtrip_problems(model: dict) -> list[str]:
    """Сверка «модель → форма → модель» по каноническому слою: вид, зона,
    вес, формулировка, источники, посадка (семья/категория/параметры),
    пометка, область, целевые (зона/направление/метрика/критерий/ярус/
    допуск/происхождение), вопросы."""
    back = from_form(to_form(model))
    return canonical_diff(model, back)


def _canon_rule(r: dict) -> dict:
    fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else None
    cf = None
    if fit:
        cf = {"category": fit.get("category"), "family": fit.get("family"),
              "assumption": fit.get("assumption"), "quote": fit.get("quote"),
              "gap": fit.get("gap"), "candidates": fit.get("candidates"),
              "params": {k: (str(v.get("value")) if isinstance(v, dict) else str(v),
                             (v.get("quote") if isinstance(v, dict) else None))
                         for k, v in (fit.get("params") or {}).items()}}
        # A green roundtrip must not hide loss of units, scope, pins or the
        # questionnaire proof. Keep legacy absent/empty equivalence only for
        # the old scalar projection, never for a typed landing.
        if fit.get("schema_version") == "landing/2":
            cf = copy.deepcopy(fit)
        else:
            cf["machine"] = {k: copy.deepcopy(v) for k, v in fit.items()
                             if k not in _FIT_OWN}
            cf["cell_machine"] = {
                k: {key: copy.deepcopy(value) for key, value in cell.items()
                    if key not in ("value", "quote")}
                for k, cell in (fit.get("params") or {}).items()
                if isinstance(cell, dict)}
    js = lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)  # noqa: E731
    return {"kind": r.get("kind"), "zone": r.get("zone"),
            "weight": r.get("weight"), "title": r.get("title"),
            "statement": r.get("statement"), "sources": js(r.get("sources")),
            "fit": js(cf), "logic": js(r.get("logic")),
            "export_solver": js(r.get("export_solver")),
            "blockers": js(r.get("blockers")),
            "excluded_until_data": js(r.get("excluded_until_data")),
            "scope": js(r.get("scope")), "note": r.get("note")}


def _canon_obj(o: dict) -> dict:
    js = lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)  # noqa: E731
    return {"zone": o.get("zone"), "direction": o.get("direction"),
            "metric": o.get("metric"), "statement": o.get("statement"),
            "criterion": js(o.get("criterion")), "sources": js(o.get("sources")),
            "rate": js(o.get("rate")),
            "tier": o.get("tier"), "tolerance": js(o.get("tolerance")),
            "origin": js(o.get("origin"))}


def canonical_diff(a: dict, b: dict) -> list[str]:
    out: list[str] = []
    ra = {r["id"]: _canon_rule(r) for r in a.get("rules", [])}
    rb = {r["id"]: _canon_rule(r) for r in b.get("rules", [])}
    for rid in sorted(set(ra) | set(rb)):
        if rid not in rb:
            out.append(f"правило {rid}: потеряно")
        elif rid not in ra:
            out.append(f"правило {rid}: появилось")
        else:
            for k in ra[rid]:
                if ra[rid][k] != rb[rid][k]:
                    out.append(f"правило {rid}: поле {k}: {ra[rid][k]!r} → "
                               f"{rb[rid][k]!r}")
    oa = {o["id"]: _canon_obj(o) for o in a.get("objectives", [])}
    ob = {o["id"]: _canon_obj(o) for o in b.get("objectives", [])}
    for oid in sorted(set(oa) | set(ob)):
        if oid not in ob:
            out.append(f"целевая {oid}: потеряна")
        elif oid not in oa:
            out.append(f"целевая {oid}: появилась")
        elif oa[oid] != ob[oid]:
            out.append(f"целевая {oid}: {oa[oid]} → {ob[oid]}")
    qa = {q.get("id"): json.dumps(q, ensure_ascii=False, sort_keys=True)
          for q in a.get("questions", [])}
    qb = {q.get("id"): json.dumps(q, ensure_ascii=False, sort_keys=True)
          for q in b.get("questions", [])}
    if qa != qb:
        out.append(f"вопросы: {sorted(set(qa) ^ set(qb)) or 'содержимое'} "
                   f"расходятся")
    return out


# --------------------------------------------------------------------- рендер

SECTION_TITLES = {
    ("common", "definitions"): "Общий раздел — определения",
    ("common", "world"): "Общий раздел — параметры мира",
    ("pairing", "hard"): "Часть 1. Пейринг — а) жёсткие ограничения",
    ("pairing", "soft"): "Часть 1. Пейринг — б) мягкие ограничения и коэффициенты штрафов",
    ("pairing", "criteria"): "Часть 1. Пейринг — в) критерии оптимизации",
    ("rostering", "hard"): "Часть 2. Ростеринг — а) жёсткие ограничения",
    ("rostering", "soft"): "Часть 2. Ростеринг — б) мягкие ограничения и коэффициенты штрафов",
    ("rostering", "criteria"): "Часть 2. Ростеринг — в) критерии оптимизации",
}
_EXCLUDED_TEXT = ("Пометка: исключено из расчёта до получения данных по "
                  "решению авиакомпании.")
#: Композиция целевой — одна фраза для документа и заголовка набора
#: (каноническая текстовая форма, читается кодом: cdpm/form_text.py).
COMPOSITION_TEXT = ("Целевая функция задачи = критерий яруса + сумма по "
                    "нарушениям мягких ограничений (размер нарушения × "
                    "коэффициент); ярусы — последовательными оптимизациями "
                    "с допуском.")


def _excluded_line(mark: dict) -> str:
    """Пометка строкой с вопросом и ревизией — чтобы текстовая форма
    возвращала пометку без потерь."""
    return (f"Пометка: исключено из расчёта до получения данных по решению "
            f"авиакомпании (вопрос {mark.get('question')}, ред. "
            f"{mark.get('revision')}).")


def _src_text(s: dict) -> str:
    if "sid" in s:
        return f"{s.get('sid')}: {s.get('anchor')}"
    return f"ответ {s.get('question')} (ред. {s.get('revision')})"


def render_row_line(row: dict, section: str) -> str:
    """Запись формы ОДНОЙ строкой человеческого текста — для документов
    авиакомпании (набор ИД H0-ВМП) и сверки «буква в букву». Поля
    постановки без служебных (sources, machine)."""
    parts = [f"{row['id']}. {row.get('title')}."]
    if section == "definitions":
        parts.append(str(row.get("statement") or ""))
        parts.append("Вид записи: "
                     + ("запись файла данных." if row.get("role") == "registry"
                        else "определение или перечень документа."))
        if row.get("excluded_until_data"):
            parts.append(_excluded_line(row["excluded_until_data"]))
        if row.get("note"):
            parts.append(f"Примечание: {row['note']}.")
        return " ".join(parts)
    if section == "criteria":
        parts.append(str(row.get("statement") or ""))
        seg = f"Направление: {row.get('direction')}"
        if row.get("metric"):
            seg += f"; метрика: {row['metric']}"
        seg += f"; ярус {row.get('tier', 1)}"
        if row.get("tolerance"):
            seg += (f"; допуск {row['tolerance']['value']} "
                    f"{row['tolerance']['unit']}")
        parts.append(seg + ".")
        rate = row.get("rate") or {}
        seg = ""
        if row.get("kind"):
            seg = f"Машинный критерий: {row['kind']}"
        if rate.get("value") is not None:
            seg += ("; " if seg else "") + f"ставка {rate['value']}"
            if rate.get("per"):
                seg += f" за {rate['per']}"
        if isinstance(rate.get("rates"), dict):
            seg += ("; " if seg else "") + (
                f"ставки рангов: полёт {rate['rates'].get('flight')}, "
                f"работа {rate['rates'].get('work')}")
        if row.get("version"):
            seg += f"; версия ставок {row['version']}"
        if row.get("effective_from"):
            seg += f"; действует с {row['effective_from']}"
        if seg:
            parts.append(seg[0].upper() + seg[1:] + ".")
        if row.get("note"):
            parts.append(f"Примечание: {row['note']}.")
        return " ".join(parts)
    parts.append(str(row.get("statement") or ""))
    tpl = row.get("template")
    if isinstance(tpl, dict):
        seg = f"Шаблон: {tpl['family']} (категория {tpl['category']}"
        if tpl.get("assumption"):
            seg += f"; допущение: {tpl['assumption']}"
        seg += ")"
        if tpl.get("quote"):
            seg += f"; носитель нормы: «{tpl['quote']}»"
        parts.append(seg + ".")
    elif row.get("gap"):
        parts.append(f"Шаблона нет: {row['gap']}.")
    ps = []
    for p in row.get("params") or []:
        unit = f" {p['unit']}" if p.get("unit") else ""
        ps.append(f"{p['name']} = {p.get('value')}{unit} («{p.get('quote')}»)")
    if ps:
        parts.append("Параметры: " + "; ".join(ps) + ".")
    pen = row.get("penalty")
    if isinstance(pen, dict):
        parts.append(f"Штраф: {pen.get('coefficient')} за "
                     f"{unit_word(pen.get('violation_unit')) or '— (единица не задана)'}.")
    if isinstance(row.get("scope"), dict):
        parts.append("Область: пейринг — " + str(row["scope"].get("pairing"))
                     + "; ростеринг — " + str(row["scope"].get("rostering")) + ".")
    if row.get("case"):
        parts.append("Запись листа исключений.")
    if row.get("excluded_until_data"):
        parts.append(_excluded_line(row["excluded_until_data"]))
    if row.get("note"):
        parts.append(f"Примечание: {row['note']}.")
    return " ".join(parts)


def form_sections(form: dict) -> list:
    """Разделы формы в каноническом порядке: [(часть, раздел, строки)]."""
    common = form.get("common") or {}
    out = [("common", "definitions", list(common.get("definitions") or [])),
           ("common", "world", list(common.get("world") or []))]
    for part in PARTS:
        block = form.get(part) or {}
        for sec in ("hard", "soft", "criteria"):
            out.append((part, sec, list(block.get(sec) or [])))
    return out


def _row_md(row: dict, soft: bool) -> str:
    lines = [f"**{row['id']}. {row.get('title')}**  ",
             f"{row.get('statement')}  "]
    tpl = row.get("template")
    if isinstance(tpl, dict):
        seg = f"Шаблон: `{tpl['family']}` ({tpl['category']}"
        if tpl.get("assumption"):
            seg += f"; допущение: {tpl['assumption']}"
        seg += ")"
        if tpl.get("quote"):
            seg += f"; носитель нормы: «{tpl['quote']}»"
        lines.append(seg + "  ")
    elif row.get("gap"):
        lines.append(f"Шаблона нет: {row['gap']}  ")
    for p in row.get("params") or []:
        unit = f" {p['unit']}" if p.get("unit") else ""
        lines.append(f"Параметр `{p['name']}` = {p.get('value')}{unit} "
                     f"(«{p.get('quote')}»)  ")
    if soft and isinstance(row.get("penalty"), dict):
        pen = row["penalty"]
        lines.append(f"Штраф: {pen.get('coefficient')} за "
                     f"{unit_word(pen.get('violation_unit')) or '— (единица не задана)'}  ")
    if isinstance(row.get("scope"), dict):
        lines.append("Область: " + "; ".join(
            f"{k}: {v}" for k, v in row["scope"].items()) + "  ")
    if row.get("excluded_until_data"):
        lines.append(_EXCLUDED_TEXT + "  ")
    lines.append("Источник: " + "; ".join(_src_text(s)
                                         for s in row.get("sources") or []))
    return "\n".join(lines)


def _criterion_md(c: dict) -> str:
    lines = [f"**{c['id']}. {c.get('title')}**  ", f"{c.get('statement')}  ",
             f"Направление: {c.get('direction')}"
             + (f"; метрика: `{c['metric']}`" if c.get("metric") else "")
             + f"; ярус {c.get('tier', 1)}"
             + (f"; допуск {c['tolerance']['value']} "
                f"{c['tolerance']['unit']}" if c.get("tolerance") else "")
             + "  "]
    rate = c.get("rate") or {}
    seg = f"Машинный критерий: `{c['kind']}`" if c.get("kind") else ""
    if rate.get("value") is not None:
        seg += ("; " if seg else "") + f"ставка {rate['value']}"
        if rate.get("per"):
            seg += f" за {rate['per']}"
    if isinstance(rate.get("rates"), dict):
        seg += ("; " if seg else "") + (
            f"ставки рангов: полёт {rate['rates'].get('flight')}, "
            f"работа {rate['rates'].get('work')}")
    if c.get("version"):
        seg += f"; версия {c['version']}"
    if c.get("effective_from"):
        seg += f" с {c['effective_from']}"
    if seg:
        lines.append(seg[0].upper() + seg[1:] + "  ")
    lines.append("Источник: " + "; ".join(_src_text(s)
                                         for s in c.get("sources") or []))
    return "\n".join(lines)


def render_form_md(form: dict) -> str:
    out = [f"# ВМП — {form.get('customer')}", "",
           f"Валюта ставок и коэффициентов: {form.get('currency')}. "
           f"База шаблонов: {form.get('registry_rev')}.", "",
           COMPOSITION_TEXT, ""]
    common = form.get("common") or {}
    out += ["## Общий раздел", "", "### Определения", ""]
    for d in common.get("definitions") or []:
        out.append(f"**{d['id']}. {d.get('title')}** "
                   f"({'запись файла данных' if d.get('role') == 'registry' else 'определение или перечень'})  ")
        out.append(f"{d.get('statement')}"
                   + ("  \n" + _EXCLUDED_TEXT if d.get("excluded_until_data") else ""))
        out.append("")
    out += ["### Параметры мира", ""]
    for row in common.get("world") or []:
        out += [_row_md(row, False), ""]
    for part, title in (("pairing", "Часть 1. Пейринг"),
                        ("rostering", "Часть 2. Ростеринг")):
        block = form.get(part) or {}
        out += [f"## {title}", "", "### а) Жёсткие ограничения", ""]
        for row in block.get("hard") or []:
            out += [_row_md(row, False), ""]
        out += ["### б) Мягкие ограничения и коэффициенты штрафов", ""]
        if not block.get("soft"):
            out += ["(нет)", ""]
        for row in block.get("soft") or []:
            out += [_row_md(row, True), ""]
        out += ["### в) Критерии оптимизации", ""]
        if not block.get("criteria"):
            out += ["(нет)", ""]
        for c in block.get("criteria") or []:
            out += [_criterion_md(c), ""]
    if form.get("withdrawn"):
        out += ["## Приложение. Снятые строки", ""]
        for w in form["withdrawn"]:
            out += [f"- {w['id']}. {w.get('title')}: {w.get('reason')}"]
        out.append("")
    if form.get("open_questions"):
        out += ["## Приложение. Открытые вопросы (формирование не завершено)", ""]
        for q in form["open_questions"]:
            out += [f"- {q.get('id')}: {q.get('title')}"]
        out.append("")
    return "\n".join(out)


def load_form(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_form(form: dict, path) -> None:
    Path(path).write_text(json.dumps(form, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
