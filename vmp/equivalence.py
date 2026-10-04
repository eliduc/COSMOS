# -*- coding: utf-8 -*-
"""Гейт эквивалентности ВМП: кандидат (выход конвейера CDPM) против
эталона (ВМП H0). Задача владельца 2026-09-01: документы авиакомпании
(DOCX/XLSX/JPEG) на входе CDPM обязаны давать на выходе ИМЕННО ВМП H0.
Байт-в-байт невозможно (идентификаторы, формулировки, метки времени
изменчивы) — сравнивается КАНОНИЧЕСКИЙ СЛОЙ, инвариантный к ним.

Сопоставление правил — по ЯКОРЯМ ЗАМОРОЗКИ (sid+anchor), как у
golden_diff; но вердикт СТРОЖЕ: H0 идеален, поэтому вид, зона, вес,
семья посадки и её параметры обязаны СОВПАСТЬ, а не быть совместимы.

Слой моделей (extract/model.json обеих сторон):
* покрытие в обе стороны — 0 пропусков эталона, 0 сверхэталонных
  среди HARD/SOFT/DEF (DATA-строки — вне сравнения, видимы отдельно);
* вид, зона — точно; вес SOFT — точно;
* семья + параметры: у кандидата — посадка template_fit (A/B), а без
  неё легаси-массив params через словарь PARAM_TO_FAMILY; значения
  канонизируются («12:00» ≡ 720; per-base «PLY:2,SVG:1» ≡ записи с
  уточнителем); цитаты (quote/as_written) НЕ сравниваются;
* целевые (objectives) по ЗОНЕ: множество (направление, метрика,
  машинный критерий, если поле criterion есть) обязано совпасть —
  критика принципов 2026-09-02: целевые сверялись только пакетом;
* анкета кандидата: 0 открытых вопросов (ВМП без ответов не полон);
  число вопросов всего — в отчёте; строгий режим (strict_questions,
  ступень ИДАК-0 «книга ответов пуста»): ЛЮБОЙ вопрос кандидата —
  нарушение (однозначный вход вопросов не порождает).
Слой пакетов (objective.json + manifest.json обеих сторон, если
поданы): по задаче — направление, метрика, состав компонентов
(якорь + вес), машинный критерий пейринга (вид, ставка; явный блок
criterion пакета первичен, JSON из применённого ответа — запас);
allowed_modes обеих задач.
Динамический слой (счёт решателей) — вне модуля: контрольный прогон
после зелёного статического вердикта.

CLI:
    python -m vmp.equivalence CAND_MODEL ETALON_MODEL
        [--candidate-package ZIP] [--etalon-package ZIP] [--out MD]
        [--strict-questions]
Код возврата: 0 = EQUIVALENT, 1 = NOT_EQUIVALENT, 2 = кривые входы.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

#: Виды, участвующие в сравнении; DATA — данные, не постановка.
KINDS_COMPARED = frozenset({"HARD", "SOFT", "DEF"})

_TIME_RE = re.compile(r"^(\d{1,3}):(\d{2})$")
#: Код базы в уточнителе ключевой ручки — та же форма, что у
#: решателя (RunThresholds.BASE_CODE): 2–4 латинских буквы/цифры.
_BASE_CODE = re.compile(r"^[A-Za-z0-9]{2,4}$")


class EquivalenceError(ValueError):
    """Кривые входы гейта — отказ словами."""


def _anchor_key(src: dict) -> str:
    return f"{src.get('sid')}|{src.get('anchor')}"


def _anchors(rule: dict) -> list[str]:
    """Якоря заморозки строки: только ДОКУМЕНТНЫЕ источники (sid +
    клауза). Источник-ответ формы ({question, revision}) якорем не
    является: иначе две строки, рождённые ответами, «совпадали» бы по
    псевдоякорю «None|None» (аудит №2)."""
    return [_anchor_key(s) for s in rule.get("sources") or ()
            if isinstance(s, dict) and s.get("sid")]


def canon_value(v):
    """Канонизация значения параметра: «12:00» -> 720; числовая строка
    -> число; число -> число; иное — строка без пробелов."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else float(v)
    if isinstance(v, (dict, list, tuple)):
        # No type-erasing repr: unknown collections keep their structure and
        # sequence order. Only a declared cell may use the set/map laws below.
        return (type(v).__name__, json.dumps(v, ensure_ascii=False,
                                           sort_keys=True, separators=(",", ":")))
    s = str(v).strip()
    m = _TIME_RE.match(s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    try:
        f = float(s.replace(",", "."))
        return int(f) if f.is_integer() else f
    except ValueError:
        return s.replace(" ", "")


def _per_base_canon(pairs: list[tuple[str, object]]) -> str:
    """Записи с уточнителем-базой -> канон К-2 «GRT:1,PLY:2,SVG:1»
    (алфавитная сортировка кодов)."""
    return ",".join(f"{q.upper()}:{canon_value(v)}"
                    for q, v in sorted(pairs, key=lambda p: p[0].upper()))


def fit_category(rule: dict) -> str | None:
    """Категория посадки на шаблон (A/B/C/D) либо None без посадки."""
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category"):
        return str(fit.get("category"))
    return None


def _cell_specs(family) -> dict:
    """Declarations, not a second hard-coded family/parameter dictionary."""
    if not family:
        return {}
    from cdpm import catalog_bridge
    record = catalog_bridge.family_records().get(str(family)) or {}
    return {str(p["name"]): p for key in ("params", "conditions")
            for p in record.get(key) or [] if isinstance(p, dict) and p.get("name")}


def _declared_value(raw, spec: dict):
    unit, qualifier = spec.get("unit"), spec.get("qualifier")
    if unit == "code_list":
        # code_list is explicitly a set (model.canon_code_list); arbitrary
        # lists are not. Reject duplicates before any ordering can hide them.
        if isinstance(raw, str):
            codes = [part.strip() for part in raw.split(",")]
        elif isinstance(raw, list):
            codes = raw[:]  # a typed code containing ',' stays ONE code
        else:
            raise ValueError("code_list требует строку перечня либо массив кодов")
        if (not codes or any(not isinstance(c, str) or not c.strip() for c in codes)
                or len(set(codes)) != len(codes)):
            raise ValueError("code_list требует непустые уникальные строковые коды")
        return ("code_list", tuple(sorted(codes)))
    if qualifier == "base_code" and unit == "count":
        if isinstance(raw, dict):
            pairs = list(raw.items())
        elif isinstance(raw, str):
            pairs = []
            for part in raw.split(","):
                if part.count(":") != 1:
                    raise ValueError("карта баз требует пары BASE:count")
                base, value = part.split(":")
                pairs.append((base.strip(), value.strip()))
        else:
            # A scalar scoped by condition is handled as a scalar, not a map.
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise ValueError("норма базы должна быть целым неотрицательным числом")
            return raw
        if not pairs:
            raise ValueError("карта баз пуста")
        seen = set()
        canonical = []
        for base, value in pairs:
            if not isinstance(base, str) or not re.fullmatch(r"[A-Z0-9]{2,4}", base):
                raise ValueError("ключ карты не канонический код базы")
            if base in seen:
                raise ValueError("повтор кода базы в карте")
            seen.add(base)
            if (isinstance(value, str) and not re.fullmatch(
                    r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value.strip())):
                raise ValueError("норма базы не является числовой записью")
            try:
                number = Decimal(str(value))
            except (InvalidOperation, ValueError):
                raise ValueError("норма базы не число") from None
            if (isinstance(value, bool) or not number.is_finite() or number < 0
                    or number != number.to_integral_value()):
                raise ValueError("норма базы должна быть целым неотрицательным числом")
            canonical.append((base, int(number)))
        return _per_base_canon(canonical)
    if isinstance(raw, (dict, list, tuple)):
        raise ValueError("структурное значение не разрешено объявленной ячейкой")
    if isinstance(raw, bool) and unit not in (None, "flag"):
        raise ValueError("логическое значение не является числом или кодом")
    if unit == "code":
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("code требует строковое обозначение")
        return raw  # opaque identity: internal spaces are significant
    return canon_value(raw)


def _fit_parameter_state(family, params):
    """Values plus semantic metadata. Missing legacy metadata uses its
    declaration; explicit metadata is never replaced or ignored."""
    specs = _cell_specs(family)
    values, contracts, problems = {}, {}, []
    if not isinstance(params, dict):
        return values, contracts, ["params не объект"]
    for name, raw_cell in params.items():
        name = str(name)
        cell = raw_cell if isinstance(raw_cell, dict) else {"value": raw_cell}
        spec = specs.get(name) or {}
        unit, relation, qualifier = (spec.get(k) for k in ("unit", "relation", "qualifier"))
        for field, expected in (("unit", unit), ("relation", relation),
                                ("qualifier", qualifier)):
            if field in cell and spec and cell[field] != expected:
                problems.append(f"{name}: {field} {cell[field]!r} ≠ объявленному {expected!r}")
        condition = cell.get("condition")
        if (qualifier == "base_code" and condition is None
                and not isinstance(cell.get("value"), (dict, str))):
            problems.append(f"{name}: скалярная норма не называет base_code")
        if condition is not None:
            if (not isinstance(condition, dict) or set(condition) != {"kind", "value"}
                    or condition.get("kind") != qualifier
                    or not isinstance(condition.get("value"), str)
                    or not condition["value"].strip()):
                problems.append(f"{name}: condition не соответствует уточнителю {qualifier!r}")
            elif qualifier == "base_code" and isinstance(cell.get("value"), dict) \
                    and set(cell["value"]) != {condition["value"]}:
                problems.append(f"{name}: condition расходится с ключами карты баз")
        contracts[name] = {
            "unit": cell.get("unit", unit), "relation": cell.get("relation", relation),
            "qualifier": cell.get("qualifier", qualifier),
            "condition": condition, "tz": cell.get("tz")}
        try:
            values[name] = _declared_value(cell.get("value"), spec)
        except (ValueError, TypeError, OverflowError) as error:
            values[name] = ("invalid", repr(cell.get("value")))
            problems.append(f"{name}: {error}")
    return values, contracts, problems


def _rule_parameter_contract(rule):
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category") in ("A", "B"):
        _values, contracts, problems = _fit_parameter_state(fit.get("family"), fit.get("params") or {})
        if "schema_version" in fit:
            from cdpm import catalog_bridge
            from template_catalog import landing
            problems += [p["words"] for p in landing.problems(rule, catalog_bridge.catalog())]
        return contracts, problems
    family, _values = family_params_of(rule)
    contracts, problems = {}, []
    specs = _cell_specs(family)
    for cell in rule.get("params") or []:
        if not isinstance(cell, dict) or not cell.get("name"):
            continue
        name = str(cell["name"])
        projected = dict(cell)
        # Legacy qualifier is a coordinate, not the qualifier KIND. It is
        # represented in the aggregate value, and cannot be silently dropped.
        coordinate = projected.pop("qualifier", None)
        if coordinate and (specs.get(name) or {}).get("qualifier") == "base_code":
            projected["value"] = {coordinate: projected.get("value")}
        _v, meta, bad = _fit_parameter_state(family, {name: projected})
        if coordinate and (specs.get(name) or {}).get("qualifier") != "base_code":
            meta[name]["legacy_qualifier"] = coordinate
        if name in contracts and contracts[name] != meta[name]:
            problems.append(f"{name}: разные метаданные повторённых ячеек")
        contracts.update(meta)
        problems.extend(bad)
    return contracts, problems


def family_params_of(rule: dict) -> tuple[str | None, dict]:
    """(семья, {параметр: каноническое значение}) правила.

    Посадка A/B первична (пакет A/B/C/D); без неё — легаси params по
    словарю PARAM_TO_FAMILY (форма свода CDPM до посадок): семья —
    единственная семья параметров, per-base записи сворачиваются."""
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category") in ("A", "B"):
        family = fit.get("family") or None
        params, _contracts, _problems = _fit_parameter_state(family, fit.get("params") or {})
        return family, params
    legacy = rule.get("params")
    if not isinstance(legacy, list) or not legacy:
        fam = (rule.get("export_solver") or {}).get("family")
        return (fam or None), {}
    from vmp.template_registry import PARAM_TO_FAMILY
    fams: list[str] = []
    plain: dict[str, object] = {}
    keyed: dict[str, list[tuple[str, object]]] = {}
    for p in legacy:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "")
        if not name:
            continue
        f = PARAM_TO_FAMILY.get(name)
        if f and f not in fams:
            fams.append(f)
        q = str(p.get("qualifier") or "").strip()
        if q and _BASE_CODE.match(q):
            # Ключевая ручка: уточнитель — код базы (per-base).
            keyed.setdefault(name, []).append((q, p.get("value")))
        else:
            # Уточнитель ОБЛАСТИ («база»/«вне базы») — деталь формы:
            # у словарных параметров область зашита в имя; несколько
            # значений одного имени — сортированный перечень.
            val = canon_value(p.get("value"))
            prev = plain.get(name)
            if prev is None:
                plain[name] = val
            elif prev != val:
                vals = prev if isinstance(prev, list) else [prev]
                if val not in vals:
                    vals.append(val)
                plain[name] = sorted(vals, key=str)
    for name, pairs in keyed.items():
        plain[name] = _per_base_canon(pairs)
    fam = fams[0] if len(fams) == 1 else (None if not fams else
                                          "+".join(sorted(fams)))
    return fam, plain


def _questions(model: dict) -> list[dict]:
    return [q for q in model.get("questions") or [] if isinstance(q, dict)]


def _open_questions(model: dict) -> list[str]:
    out = []
    for q in _questions(model):
        ans = q.get("answer")
        status = (ans or {}).get("status") if isinstance(ans, dict) \
            else None
        if status != "применён":
            out.append(str(q.get("id")))
    return out


def _rates_canon(crit: dict) -> object:
    """Ставка критерия: rate_per_duty числом либо словарь ставок rates
    ({flight, work} — r55-CDPM) кортежем (имя, значение) по алфавиту."""
    rates = crit.get("rates")
    if isinstance(rates, dict) and rates:
        return tuple((str(k), canon_value(v))
                     for k, v in sorted(rates.items()))
    return canon_value(crit.get("rate_per_duty"))


def _criterion_canon(crit) -> object:
    """Канон машинного критерия целевой: блок {kind|criterion,
    rate_per_duty | rates} -> (вид, ставка); строка — как есть; нет —
    None. Служебные поля блока (label, version, effective_from,
    as_written, source_*) — вне сравнения."""
    if isinstance(crit, dict):
        return (_kind_canon(crit.get("kind") or crit.get("criterion")),
                _rates_canon(crit))
    if crit is None or crit == "":
        return None
    return _kind_canon(crit)


def _kind_canon(kind) -> str | None:
    """Вид критерия — идентификатор словаря vmp/criteria без учёта
    регистра (MIN_PERSON_DAYS ≡ min_person_days: run-0008 читал вид
    строчными, эталон — прописными)."""
    if kind is None or kind == "":
        return None
    return str(kind).strip().casefold()


def _objective_canon(o: dict, with_criterion: bool) -> tuple:
    """Канон целевой: (зона, направление, метрика[, критерий]) —
    идентификатор и формулировка вне сравнения."""
    key = (str(o.get("zone") or ""), str(o.get("direction") or ""),
           str(o.get("metric") or ""))
    if with_criterion:
        key += (_criterion_canon(o.get("criterion")),)
    return key


def diff_objectives(candidate: dict, etalon: dict) -> tuple[dict, list]:
    """Слой моделей, целевые — тем же правилом, что и правила:
    сопоставление ПО ЯКОРЯМ заморозки. Каждая целевая эталона обязана
    быть покрыта целевой кандидата на её якоре с совпадением зоны,
    направления, метрики (и критерия, если эталон объявляет блок
    criterion: эталон без носителя в модели не может требовать его от
    кандидата — фактическую ставку судит слой пакетов); целевая
    кандидата, не сидящая ни на одном якоре эталона, — «сверх
    эталона» (число целевых не схлопывается: две целевые на разных
    якорях — две записи, а не одно множество)."""
    problems: list[str] = []
    rows: list[dict] = []
    cand = [o for o in candidate.get("objectives") or []
            if isinstance(o, dict)]
    by_anchor: dict[str, list[dict]] = {}
    for o in cand:
        for a in _anchors(o):
            by_anchor.setdefault(a, []).append(o)
    used: set[int] = set()
    for e in etalon.get("objectives") or []:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id"))
        anchors = _anchors(e)
        declared = _criterion_canon(e.get("criterion")) is not None
        e_key = _objective_canon(e, declared)
        hits: list[dict] = []
        for a in anchors:
            for h in by_anchor.get(a, ()):
                if id(h) not in {id(x) for x in hits}:
                    hits.append(h)
        row = {"etalon_id": eid, "anchors": anchors,
               "candidate_ids": [str(h.get("id")) for h in hits],
               "key": [str(x) for x in e_key]}
        if not hits:
            row["ok"] = False
            problems.append(f"целевая {eid}: не покрыта "
                            f"(якоря {', '.join(anchors) or '—'})")
            rows.append(row)
            continue
        used.update(id(h) for h in hits)
        match = next((h for h in hits
                      if _objective_canon(h, declared) == e_key), None)
        row["ok"] = match is not None
        if match is None:
            h = hits[0]
            problems.append(f"целевая {eid}: {e_key} ≠ "
                            f"{_objective_canon(h, declared)} у "
                            f"{h.get('id')}")
        rows.append(row)
    for o in cand:
        if id(o) in used:
            continue
        problems.append(f"сверх эталона: целевая {o.get('id')} "
                        f"[{o.get('zone')}] {o.get('title') or ''} "
                        f"(якоря {', '.join(_anchors(o)) or '—'})")
    detail = {"rows": rows,
              "extra": [str(o.get("id")) for o in cand
                        if id(o) not in used],
              "etalon_count": len(rows), "candidate_count": len(cand)}
    return detail, problems


def diff_models(candidate: dict, etalon: dict,
                strict_questions: bool = False) -> dict:
    """Слой моделей. Возврат {rows, extra, data_rows, objectives,
    open_questions, questions_total, problems}; problems — поимённые
    нарушения эквивалентности. strict_questions — любой вопрос
    анкеты у кандидата есть нарушение (ступень ИДАК-0)."""
    for name, m in (("кандидат", candidate), ("эталон", etalon)):
        if not isinstance(m, dict):
            raise EquivalenceError(f"{name}: модель не объект JSON")
    cand_rules = [r for r in candidate.get("rules") or []
                  if isinstance(r, dict)]
    by_anchor: dict[str, list[dict]] = {}
    for r in cand_rules:
        for a in _anchors(r):
            by_anchor.setdefault(a, []).append(r)

    rows: list[dict] = []
    problems: list[str] = []
    for side, model in (("кандидат", candidate), ("эталон", etalon)):
        for rule in model.get("rules") or []:
            if isinstance(rule, dict) and rule.get("kind") in KINDS_COMPARED:
                _contracts, invalid = _rule_parameter_contract(rule)
                problems.extend(f"{side} {rule.get('id')}: {p}" for p in invalid)
    used: set[str] = set()
    for e in etalon.get("rules") or []:
        if not isinstance(e, dict) or e.get("kind") not in KINDS_COMPARED:
            continue
        eid = str(e.get("id"))
        anchors = _anchors(e)
        hits: list[dict] = []
        seen: set[str] = set()
        for a in anchors:
            for h in by_anchor.get(a, ()):
                hid = str(h.get("id"))
                if h.get("kind") in KINDS_COMPARED and hid not in seen:
                    seen.add(hid)
                    hits.append(h)
        row = {"etalon_id": eid, "title": e.get("title"),
               "anchors": anchors,
               "candidate_ids": [str(h.get("id")) for h in hits],
               "checks": {}}
        if not hits:
            problems.append(f"{eid}: эталонное правило не покрыто "
                            f"(якоря {', '.join(anchors) or '—'})")
            row["covered"] = False
            rows.append(row)
            continue
        row["covered"] = True
        used.update(row["candidate_ids"])
        e_fam, e_params = family_params_of(e)
        e_contract, _invalid = _rule_parameter_contract(e)
        # Эквивалентность подтверждает ХОТЯ БЫ ОДИН кандидат по якорю
        # (две посадки на одну клаузу законны — К-1/Р3).
        best = None
        for h in hits:
            checks = {}
            checks["kind"] = (h.get("kind") == e.get("kind"))
            checks["zone"] = (h.get("zone") == e.get("zone"))
            if e.get("kind") == "SOFT":
                checks["weight"] = (canon_value(h.get("weight"))
                                    == canon_value(e.get("weight")))
            h_fam, h_params = family_params_of(h)
            h_contract, _invalid = _rule_parameter_contract(h)
            checks["family"] = (h_fam == e_fam) if e_fam else True
            checks["params"] = (h_params == e_params) if e_params \
                else True
            checks["param_contract"] = (h_contract == e_contract) if e_params else True
            # Категория посадки — тоже канон: эталон A, кандидат B (с
            # допущением) — не «один в один» (прогон №3 H0: A→B по
            # арбитру у VN-011 гейт без этой проверки не видел).
            # Кандидат без посадки (легаси-форма свода до пакета
            # посадок) категорией не судится — только семьёй и
            # параметрами.
            e_cat, h_cat = fit_category(e), fit_category(h)
            checks["fit"] = (h_cat == e_cat) if (e_cat and h_cat) else True
            score = sum(1 for v in checks.values() if v)
            if best is None or score > best[0]:
                best = (score, h, checks, h_fam, h_params)
        _s, h, checks, h_fam, h_params = best
        row["checks"] = checks
        row["matched_id"] = str(h.get("id"))
        for key, ok in checks.items():
            if ok:
                continue
            if key == "kind":
                problems.append(f"{eid}: вид {e.get('kind')} ≠ "
                                f"{h.get('kind')} у {h.get('id')}")
            elif key == "zone":
                problems.append(f"{eid}: зона {e.get('zone')} ≠ "
                                f"{h.get('zone')} у {h.get('id')}")
            elif key == "weight":
                problems.append(f"{eid}: вес {e.get('weight')} ≠ "
                                f"{h.get('weight')} у {h.get('id')}")
            elif key == "family":
                problems.append(f"{eid}: семья {e_fam} ≠ {h_fam} "
                                f"у {h.get('id')}")
            elif key == "params":
                problems.append(f"{eid}: параметры {e_params} ≠ "
                                f"{h_params} у {h.get('id')}")
            elif key == "param_contract":
                problems.append(f"{eid}: метаданные параметров (unit/relation/qualifier/condition/tz) "
                                f"расходятся у {h.get('id')}")
            elif key == "fit":
                problems.append(f"{eid}: категория посадки {e_cat} ≠ "
                                f"{fit_category(h)} у {h.get('id')}")
        rows.append(row)

    extra = []
    data_rows = []
    for r in cand_rules:
        rid = str(r.get("id"))
        if r.get("kind") not in KINDS_COMPARED:
            data_rows.append({"id": rid, "kind": r.get("kind"),
                              "title": r.get("title")})
            continue
        if rid not in used:
            extra.append({"id": rid, "kind": r.get("kind"),
                          "title": r.get("title"),
                          "anchors": _anchors(r)})
            problems.append(f"сверх эталона: {rid} [{r.get('kind')}] "
                            f"{r.get('title') or ''} "
                            f"(якоря {', '.join(_anchors(r)) or '—'})")
    obj_detail, obj_problems = diff_objectives(candidate, etalon)
    problems += obj_problems
    open_q = _open_questions(candidate)
    q_total = len(_questions(candidate))
    for q in open_q:
        problems.append(f"открытый вопрос анкеты у кандидата: {q}")
    if strict_questions and q_total:
        problems.append(f"строгий режим (ИДАК-0): у кандидата {q_total} "
                        f"вопрос(ов) анкеты — на однозначном входе "
                        f"вопросов быть не должно")
    return {"rows": rows, "extra": extra, "data_rows": data_rows,
            "objectives": obj_detail,
            "open_questions": open_q, "questions_total": q_total,
            "strict_questions": bool(strict_questions),
            "problems": problems}


# ------------------------------------------------------ слой пакетов

def _read_zip_json(zip_path: Path, name: str) -> dict | None:
    with zipfile.ZipFile(zip_path) as z:
        if name not in z.namelist():
            return None
        return json.loads(z.read(name))


def _criterion_of(task_block: dict) -> dict | None:
    """Машинный критерий пейринга: явный блок criterion пакета, иначе
    JSON-блок из применённого ответа авиакомпании."""
    crit = task_block.get("criterion")
    if isinstance(crit, dict):
        return {"kind": _kind_canon(crit.get("kind") or crit.get("criterion")),
                "rate": _rates_canon(crit)}
    raw = str((task_block.get("airline_answer") or {}).get("raw") or "")
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        blk = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return {"kind": _kind_canon(blk.get("criterion")),
            "rate": _rates_canon(blk)}


def _task_canon(obj: dict, task: str) -> dict:
    blk = obj.get(task) or {}
    comps = set()
    for c in blk.get("components") or []:
        if not isinstance(c, dict):
            continue
        for s in c.get("sources") or []:
            comps.add((_anchor_key(s), canon_value(c.get("weight"))))
    metrics = sorted({str(o.get("metric")) for o in
                      (blk.get("objectives") or []) if o.get("metric")})
    return {"direction": blk.get("direction"),
            "metrics": metrics, "components": comps,
            "criterion": _criterion_of(blk) if task == "pairing" else None}


def diff_packages(cand_zip: Path, etalon_zip: Path) -> dict:
    """Слой пакетов: целевые и допустимые режимы по задачам."""
    problems: list[str] = []
    co = _read_zip_json(cand_zip, "objective.json") or {}
    eo = _read_zip_json(etalon_zip, "objective.json") or {}
    detail = {}
    for task in ("pairing", "rostering"):
        c, e = _task_canon(co, task), _task_canon(eo, task)
        detail[task] = {"candidate": {k: (sorted(v) if isinstance(v, set)
                                          else v) for k, v in c.items()},
                        "etalon": {k: (sorted(v) if isinstance(v, set)
                                       else v) for k, v in e.items()}}
        if c["direction"] != e["direction"]:
            problems.append(f"{task}: направление {e['direction']} ≠ "
                            f"{c['direction']}")
        if c["metrics"] != e["metrics"]:
            problems.append(f"{task}: метрики {e['metrics']} ≠ "
                            f"{c['metrics']}")
        if c["components"] != e["components"]:
            missing = sorted(e["components"] - c["components"])
            over = sorted(c["components"] - e["components"])
            problems.append(f"{task}: компоненты целевой (якорь, вес) "
                            f"расходятся — нет {missing}, лишние {over}")
        if task == "pairing" and e["criterion"] \
                and c["criterion"] != e["criterion"]:
            problems.append(f"pairing: машинный критерий "
                            f"{e['criterion']} ≠ {c['criterion']}")
    cm = (_read_zip_json(cand_zip, "manifest.json") or {}).get(
        "allowed_modes") or {}
    em = (_read_zip_json(etalon_zip, "manifest.json") or {}).get(
        "allowed_modes") or {}
    for task in ("pairing", "rostering"):
        if sorted(cm.get(task) or []) != sorted(em.get(task) or []):
            problems.append(f"{task}: allowed_modes {em.get(task)} ≠ "
                            f"{cm.get(task)}")
    return {"detail": detail, "problems": problems}


# --------------------------------------------------------------- слой форм

#: Разделы формы ВМП (структура владельца 2026-09-02): путь в форме и
#: подпись раздела. Место строки — часть канона: правило в чужом
#: разделе — нарушение, даже если поля совпали.
FORM_SECTIONS = (
    (("common", "definitions"), "общий/определения"),
    (("common", "world"), "общий/параметры мира"),
    (("pairing", "hard"), "ч.1 Пейринг/а) жёсткие"),
    (("pairing", "soft"), "ч.1 Пейринг/б) мягкие"),
    (("pairing", "criteria"), "ч.1 Пейринг/в) критерии"),
    (("rostering", "hard"), "ч.2 Ростеринг/а) жёсткие"),
    (("rostering", "soft"), "ч.2 Ростеринг/б) мягкие"),
    (("rostering", "criteria"), "ч.2 Ростеринг/в) критерии"),
)
# Приложение «снято» (OUT) — вне ВМП как постановки (спецификация
# формы §1) и вне сравнения, как OUT/CASE в слое моделей (аудит №1).

#: Класс строки по разделу: «место» строки судится только среди
#: разделов того же класса (правило не может «стоять» в разделе
#: критериев — это другая строка на том же якоре, а не перенос).
_SECTION_CLASS = {
    "definitions": "definition", "world": "rule", "hard": "rule",
    "soft": "rule", "criteria": "criterion",
}


def _form_rows(form: dict, path: tuple) -> list[dict]:
    node = form
    for key in path:
        node = (node or {}).get(key) or ([] if key == path[-1] else {})
    rows = [r for r in (node or []) if isinstance(r, dict)]
    if path[-1] == "definitions":
        # Справочники (DATA -> role registry) — данные набора, вне
        # сравнения (решение владельца: DATA не сравнивается), как
        # data_rows слоя моделей.
        rows = [r for r in rows if r.get("role") != "registry"]
    if path[-1] in ("hard", "soft", "world"):
        # Исключения (CASE) — вне сравнения, как в слое моделей
        # (KINDS_COMPARED без CASE); правило CASE зоны BOTH попадает в
        # общий раздел, поэтому фильтр стоит во всех разделах правил
        # (аудит №2).
        rows = [r for r in rows if not r.get("case")]
    return rows


def _legacy_row(row: dict) -> bool:
    """Строка кандидата без шаблона от свода до пакета посадок: семья и
    параметры сидят в machine (params / export_solver.family) — шаблон,
    категорию, параметры и единицу нарушения судит слой моделей."""
    if isinstance(row.get("template"), dict):
        return False
    mach = row.get("machine") or {}
    es = mach.get("export_solver") if isinstance(mach.get("export_solver"),
                                                 dict) else {}
    return bool(mach.get("params")) or bool(es.get("family"))


def _open_question_ids(form: dict) -> list[str]:
    """Открытые вопросы формы тем же предикатом, что слой моделей:
    статус ответа ≠ «применён» (в приложениях open_questions и
    answered_questions — «ожидает обработчика» открыт для гейта)."""
    out: list[str] = []
    for key in ("open_questions", "answered_questions"):
        for q in form.get(key) or []:
            if not isinstance(q, dict):
                continue
            ans = q.get("answer")
            status = (ans or {}).get("status") if isinstance(ans, dict) \
                else None
            if status != "применён":
                out.append(str(q.get("id")))
    return out


def _form_parameter_state(row):
    """Read visible values and the losslessly carried typed cell metadata.
    This is a projection only: neither the form nor its metadata is repaired."""
    template = row.get("template") or {}
    carried = (row.get("machine") or {}).get("template_fit") or {}
    metadata = carried.get("param_cells") or {}
    cells, issues = {}, []
    for item in row.get("params") or []:
        if not isinstance(item, dict) or not item.get("name"):
            issues.append("форма параметра не содержит name")
            continue
        name = str(item["name"])
        if name in cells:
            issues.append(f"{name}: повторённая ячейка формы")
            continue
        cell = dict(metadata.get(name) or {})
        # Visible fields are authoritative in the ordinary from_form reader.
        # An inconsistent duplicate carrier is nevertheless not equivalence.
        for key in ("unit", "relation", "condition", "qualifier", "tz"):
            if key in item and key in cell and item[key] != cell[key]:
                issues.append(f"{name}: {key} формы расходится с machine")
        cell.update({key: value for key, value in item.items() if key != "name"})
        cells[name] = cell
    values, contracts, bad = _fit_parameter_state(template.get("family"), cells)
    issues.extend(bad)
    extra = carried.get("extra") or {}
    if "schema_version" in extra:
        from cdpm import catalog_bridge
        from template_catalog import landing
        fit = dict(extra, category=template.get("category"),
                   family=template.get("family"), params=cells,
                   registry_rev=carried.get("registry_rev"))
        issues.extend(p["words"] for p in landing.problems(
            {"id": row.get("id"), "template_fit": fit}, catalog_bridge.catalog()))
    return values, contracts, issues


def _form_row_canon(row: dict, section: tuple, e_row: dict | None) -> dict:
    """Канон строки формы по её разделу: шаблон (семья, категория),
    параметры (имя -> канон значения), для мягких — коэффициент и
    единица нарушения, для параметров мира — область действия, для
    критериев — направление, метрика, вид, ставка, ярус/допуск
    (версия и дата — если эталон их объявляет)."""
    out: dict = {}
    if section[-1] == "criteria":
        out["direction"] = str(row.get("direction") or "")
        out["metric"] = str(row.get("metric") or "")
        # Вид, ставка, версия и дата критерия входят в канон только
        # когда ЭТАЛОН их объявляет (эталон без носителя критерия не
        # вправе требовать его от кандидата — ставку судит слой пакетов).
        declared = e_row is not None and bool(e_row.get("kind"))
        # Ставка строки в) сравнивается и у НЕмашинного критерия (без
        # вида): решение владельца 5 — строка в) несёт ставку в валюте
        # формы; аудит №2 показал дыру (O-ROST 1 против 7 давал
        # EQUIVALENT).
        e_rate = (e_row or {}).get("rate") or {}
        declared_rate = e_row is not None and (
            e_rate.get("value") is not None
            or isinstance(e_rate.get("rates"), dict))
        if declared:
            out["kind"] = _kind_canon(row.get("kind"))
        if declared or declared_rate:
            rate = row.get("rate") or {}
            if isinstance(rate.get("rates"), dict):
                out["rate"] = tuple((str(k), canon_value(v)) for k, v in
                                    sorted(rate["rates"].items()))
            else:
                out["rate"] = canon_value(rate.get("value")) \
                    if rate.get("value") is not None else None
            out["rate_per"] = str(rate.get("per") or "")
        if declared and e_row.get("version"):
            out["version"] = str(row.get("version") or "")
        if declared and e_row.get("effective_from"):
            out["effective_from"] = str(row.get("effective_from") or "")
        out["tier"] = int(row.get("tier") or 1)
        tol = row.get("tolerance")
        if isinstance(tol, dict):
            # Допуск яруса — структурно (значение, единица), не строкой
            # словаря с порядком ключей.
            out["tolerance"] = (canon_value(tol.get("value")),
                                str(tol.get("unit") or ""))
        else:
            out["tolerance"] = canon_value(tol) if tol is not None else None
        return out
    if section[-1] == "definitions":
        out["role"] = str(row.get("role") or "")
        # Пометка «исключено до получения данных» может стоять у строки
        # любого раздела (спецификация формы §6) — у определений она
        # тоже канон (аудит №2).
        out["excluded_until_data"] = bool(row.get("excluded_until_data"))
        return out
    tpl = row.get("template") if isinstance(row.get("template"), dict) \
        else None
    out["family"] = (tpl or {}).get("family")
    out["category"] = (tpl or {}).get("category")
    params: dict = {}
    keyed: dict[str, list] = {}
    for p in row.get("params") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        name = str(p["name"])
        q = str(p.get("qualifier") or "").strip()
        if q and _BASE_CODE.match(q):
            keyed.setdefault(name, []).append((q, p.get("value")))
        else:
            params[name] = canon_value(p.get("value"))
    for name, pairs in keyed.items():
        params[name] = _per_base_canon(pairs)
    out["params"] = params
    if tpl:
        values, contracts, invalid = _form_parameter_state(row)
        out["params"] = values
        out["param_contract"] = contracts
        out["param_errors"] = invalid
    if section[-1] == "soft":
        pen = row.get("penalty") or {}
        out["coefficient"] = canon_value(pen.get("coefficient")) \
            if pen.get("coefficient") is not None else None
        out["violation_unit"] = str(pen.get("violation_unit") or "")
    if section[-1] == "world":
        scope = row.get("scope") or {}
        out["scope"] = (str(scope.get("pairing") or ""),
                        str(scope.get("rostering") or ""))
    out["excluded_until_data"] = bool(row.get("excluded_until_data"))
    return out


def diff_forms(cand_form: dict, etalon_form: dict,
               strict_questions: bool = False,
               rows_only: bool = False) -> dict:
    """Слой форм: две одинаково устроенные формы ВМП сравниваются по
    разделам — строка эталона обязана быть покрыта строкой кандидата
    В ТОМ ЖЕ РАЗДЕЛЕ на её якоре с совпадением канона раздела; строка
    кандидата без якоря эталона в разделе — «сверх эталона»; строка на
    якоре эталона, но в другом разделе ТОГО ЖЕ КЛАССА — «место».
    Вопросы (предикат слоя моделей: статус ≠ «применён») — нарушение;
    strict — любой вопрос. rows_only — только строки разделов (для
    сверки тождества формы пакета с проекцией модели)."""
    problems: list[str] = []
    rows_out: list[dict] = []
    by_anchor_all: dict[str, list[tuple[tuple, dict]]] = {}
    for path, _title in FORM_SECTIONS:
        for r in _form_rows(cand_form, path):
            for a in _anchors(r):
                by_anchor_all.setdefault(a, []).append((path, r))
    used: set[int] = set()
    for path, title in FORM_SECTIONS:
        klass = _SECTION_CLASS[path[-1]]
        for e in _form_rows(etalon_form, path):
            eid = str(e.get("id"))
            anchors = _anchors(e)
            same, other = [], []
            seen_rows: set[int] = set()
            for a in anchors:
                for p, r in by_anchor_all.get(a, ()):
                    if id(r) in seen_rows:
                        continue     # строка на нескольких якорях — один раз
                    seen_rows.add(id(r))
                    if p == path:
                        same.append((p, r))
                    elif _SECTION_CLASS[p[-1]] == klass:
                        other.append((p, r))
            row = {"section": title, "etalon_id": eid, "anchors": anchors,
                   "candidate_ids": [str(r.get("id")) for _p, r in same]}
            if not same:
                if other:
                    p2, r2 = other[0]
                    where = next(t for pp, t in FORM_SECTIONS if pp == p2)
                    problems.append(f"{title} {eid}: место — у кандидата "
                                    f"строка {r2.get('id')} стоит в разделе "
                                    f"«{where}»")
                    used.add(id(r2))
                else:
                    problems.append(f"{title} {eid}: не покрыта (якоря "
                                    f"{', '.join(anchors) or '—'})")
                row["ok"] = False
                rows_out.append(row)
                continue
            used.update(id(r) for _p, r in same)
            e_can = _form_row_canon(e, path, e)
            best = None
            for _p, h in same:
                h_can = _form_row_canon(h, path, e)
                skip: set[str] = set()
                if path[-1] in ("hard", "soft", "world") and _legacy_row(h):
                    # Легаси-форма свода (params-массив / export_solver
                    # без посадки): шаблон, параметры и единицу нарушения
                    # судит слой моделей по семье; форма без шаблона их
                    # не несёт.
                    skip = {"family", "category", "params", "violation_unit",
                            "param_contract", "param_errors"}
                bad = [k for k in e_can
                       if k not in skip and h_can.get(k) != e_can[k]]
                if best is None or len(bad) < len(best[1]):
                    best = (h, bad, h_can)
            h, bad, h_can = best
            row["matched_id"] = str(h.get("id"))
            invalid = [("эталон", p) for p in e_can.get("param_errors") or []]
            invalid += [("кандидат", p) for p in h_can.get("param_errors") or []]
            row["ok"] = not bad and not invalid
            problems.extend(f"{title} {eid}: {side}: {p}" for side, p in invalid)
            for k in bad:
                problems.append(f"{title} {eid}: {k} {e_can[k]!r} ≠ "
                                f"{h_can.get(k)!r} у {h.get('id')}")
            rows_out.append(row)
    extra: list[str] = []
    for path, title in FORM_SECTIONS:
        for r in _form_rows(cand_form, path):
            if id(r) in used:
                continue
            extra.append(f"{title}: {r.get('id')}")
            problems.append(f"сверх эталона в разделе «{title}»: "
                            f"{r.get('id')} {r.get('title') or ''} (якоря "
                            f"{', '.join(_anchors(r)) or '—'})")
    open_q = _open_question_ids(cand_form)
    total_q = sum(1 for key in ("open_questions", "answered_questions")
                  for q in cand_form.get(key) or [] if isinstance(q, dict))
    if not rows_only:
        for q in open_q:
            problems.append(f"форма: открытый вопрос у кандидата: {q}")
        if strict_questions and total_q:
            problems.append(f"форма, строгий режим: у кандидата {total_q} "
                            f"вопрос(ов) — готовый ВМП вопросов не несёт")
        if str(cand_form.get("currency") or "") != \
                str(etalon_form.get("currency") or ""):
            problems.append(f"форма: валюта {etalon_form.get('currency')!r} "
                            f"≠ {cand_form.get('currency')!r}")
    return {"rows": rows_out, "extra": extra, "open_questions": open_q,
            "questions_total": total_q, "problems": problems}


def _forms_of(candidate: dict, etalon: dict,
              cand_zip: Path | None = None,
              etalon_zip: Path | None = None) -> tuple | None:
    """Формы обеих сторон через cdpm.vmp_form; без модуля — None
    (слой форм пропущен, о чём говорит отчёт). Если пакет несёт
    vmp-form.json (выход конвейера, структура владельца 2026-09-02),
    судится ОН — и сверяется тождество с проекцией модели той же
    стороны: расхождение — нарушение «форма пакета ≠ проекция
    модели». Возврат: (форма кандидата, форма эталона, нарушения
    тождества)."""
    try:
        from cdpm import vmp_form as vf
    except Exception:                                     # noqa: BLE001
        return None
    identity: list[str] = []
    sides = []
    for name, model, zp in (("кандидат", candidate, cand_zip),
                            ("эталон", etalon, etalon_zip)):
        try:
            projected = vf.to_form(model)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            # Кривой вход (правило без id и т.п.) — отказ словами и
            # код 2, а не трассировка с кодом 1 (аудит №2).
            raise EquivalenceError(
                f"{name}: модель не проецируется в форму: {e!r}") from None
        packaged = _read_zip_json(Path(zp), "vmp-form.json") \
            if zp is not None else None
        if isinstance(packaged, dict):
            # Тождество — только по строкам разделов (вопросы и валюту
            # судит сам слой форм, не дважды).
            same = diff_forms(packaged, projected, rows_only=True)["problems"]
            same += [p for p in diff_forms(projected, packaged,
                                           rows_only=True)["problems"]
                     if p not in same]
            for p in same:
                identity.append(f"{name}: форма пакета ≠ проекция модели: "
                                f"{p}")
            sides.append(packaged)
        else:
            sides.append(projected)
    return sides[0], sides[1], identity


# ------------------------------------------------------------ итог

def equivalence(candidate: dict, etalon: dict,
                cand_zip: Path | None = None,
                etalon_zip: Path | None = None,
                strict_questions: bool = False,
                forms: bool = True) -> dict:
    """Сводный вердикт: EQUIVALENT / NOT_EQUIVALENT с перечнем.
    forms — слой форм (структура владельца 2026-09-02): обе модели
    проецируются в форму и сравниваются по разделам."""
    models = diff_models(candidate, etalon,
                         strict_questions=strict_questions)
    packages = None
    if cand_zip is not None and etalon_zip is not None:
        packages = diff_packages(Path(cand_zip), Path(etalon_zip))
    form_layer = None
    forms_skipped = None
    if forms:
        pair = _forms_of(candidate, etalon, cand_zip, etalon_zip)
        if pair is not None:
            form_layer = diff_forms(pair[0], pair[1],
                                    strict_questions=strict_questions)
            form_layer["identity_problems"] = pair[2]
            form_layer["problems"] = list(pair[2]) + form_layer["problems"]
        else:
            # Слой форм запрошен, но модуль недоступен — честный отказ,
            # а не молчаливый пропуск с возможным EQUIVALENT (аудит №2).
            raise EquivalenceError(
                "слой форм запрошен, но модуль формы ВМП "
                "(cdpm.vmp_form) недоступен — сравнить формы нечем; "
                "для сравнения без него укажите --no-forms")
    else:
        forms_skipped = "отключён ключом --no-forms"
    problems = list(models["problems"])
    if packages:
        problems += packages["problems"]
    if form_layer:
        problems += form_layer["problems"]
    return {"verdict": "EQUIVALENT" if not problems else "NOT_EQUIVALENT",
            "models": models, "packages": packages, "forms": form_layer,
            "forms_skipped": forms_skipped,
            "problems": problems,
            "counts": {"etalon_rules": len(models["rows"]),
                       "covered": sum(1 for r in models["rows"]
                                      if r.get("covered")),
                       "extra": len(models["extra"]),
                       "data_rows": len(models["data_rows"]),
                       "objectives_etalon": models["objectives"]
                       ["etalon_count"],
                       "objectives_ok": sum(
                           1 for r in models["objectives"]["rows"]
                           if r.get("ok")),
                       "objectives_extra": len(models["objectives"]
                                               ["extra"]),
                       "questions_total": models["questions_total"],
                       "open_questions": len(models["open_questions"]),
                       "form_rows": (len(form_layer["rows"])
                                     if form_layer else None),
                       "form_rows_ok": (sum(1 for r in form_layer["rows"]
                                            if r.get("ok"))
                                        if form_layer else None),
                       "form_extra": (len(form_layer["extra"])
                                      if form_layer else None),
                       "form_problems": (len(form_layer["problems"])
                                         if form_layer else None),
                       "problems": len(problems)}}


def render_report(res: dict, header: str = "") -> str:
    c = res["counts"]
    lines = ["# Гейт эквивалентности ВМП: кандидат против эталона H0",
             ""]
    if header:
        lines += [header, ""]
    strict = " (строгий режим: вопросов 0)" \
        if res["models"].get("strict_questions") else ""
    lines += [f"Вердикт: **{res['verdict']}** — правил эталона "
              f"{c['etalon_rules']}, покрыто {c['covered']}, сверх "
              f"эталона {c['extra']}, строк данных вне сравнения "
              f"{c['data_rows']}, целевых эталона "
              f"{c['objectives_etalon']} (покрыто {c['objectives_ok']}, "
              f"сверх эталона {c['objectives_extra']}), вопросов анкеты "
              f"всего {c['questions_total']}, открытых "
              f"{c['open_questions']}{strict}, нарушений "
              f"{c['problems']}", ""]
    for r in res["models"]["rows"]:
        if not r.get("covered"):
            mark = "НЕ ПОКРЫТО"
        else:
            bad = [k for k, v in (r.get("checks") or {}).items() if not v]
            mark = "ОК" if not bad else "РАСХОЖДЕНИЕ: " + ", ".join(bad)
        lines.append(f"- [{mark}] {r['etalon_id']} {r.get('title')} -> "
                     f"{', '.join(r['candidate_ids']) or '—'}")
    fl = res.get("forms")
    if fl is None:
        lines += ["", "Слой форм: пропущен ("
                  + str(res.get("forms_skipped") or "причина не названа")
                  + ")"]
    else:
        lines += ["", f"Слой форм: строк эталона {c['form_rows']}, "
                      f"сошлись {c['form_rows_ok']}, сверх эталона "
                      f"{c['form_extra']}, нарушений {c['form_problems']}"]
        for r in fl["rows"]:
            mark = "ОК" if r.get("ok") else "РАСХОЖДЕНИЕ"
            lines.append(f"- [{mark}] {r['section']}: {r['etalon_id']} -> "
                         f"{', '.join(r['candidate_ids']) or '—'}")
    if res["problems"]:
        lines += ["", "Нарушения эквивалентности:"]
        lines += [f"- {p}" for p in res["problems"]]
    if res["models"]["data_rows"]:
        lines += ["", "Строки данных кандидата (вне сравнения):"]
        lines += [f"- {x['id']} [{x['kind']}] {x.get('title') or ''}"
                  for x in res["models"]["data_rows"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="Гейт эквивалентности ВМП против эталона H0")
    ap.add_argument("candidate_model")
    ap.add_argument("etalon_model")
    ap.add_argument("--candidate-package", default=None)
    ap.add_argument("--etalon-package", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--strict-questions", action="store_true",
                    help="любой вопрос анкеты у кандидата — нарушение "
                         "(ступень ИДАК-0: книга ответов пуста)")
    ap.add_argument("--no-forms", action="store_true",
                    help="без слоя форм (только модели и пакеты)")
    a = ap.parse_args(argv)
    try:
        cand = json.loads(Path(a.candidate_model)
                          .read_text(encoding="utf-8"))
        et = json.loads(Path(a.etalon_model).read_text(encoding="utf-8"))
        res = equivalence(
            cand, et,
            Path(a.candidate_package) if a.candidate_package else None,
            Path(a.etalon_package) if a.etalon_package else None,
            strict_questions=a.strict_questions,
            forms=not a.no_forms)
    except (EquivalenceError, OSError, json.JSONDecodeError,
            zipfile.BadZipFile) as e:
        print(f"ОТКАЗ: {e}")
        return 2
    text = render_report(res, header=f"Кандидат: {a.candidate_model}")
    if a.out:
        io.open(a.out, "w", encoding="utf-8").write(text)
    print(text)
    return 0 if res["verdict"] == "EQUIVALENT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
