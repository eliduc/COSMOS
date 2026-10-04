"""One persisted result of the bounded parameter-answer grammar.

Unresolved applicability is retained verbatim, not translated to an executable
condition. Catalogue 0.6 subject evidence is checked, but mixed-subject
assignment and executable conditions remain separate obligations.
"""
from __future__ import annotations

import copy
import json


def parameter(q: dict, raw: str) -> dict:
    from cdpm import family_params as fp
    target = fp.question_target(q)
    spec = fp._param_spec(*target) if target else {}
    unit = spec.get("unit")
    out = {"subject": target[1] if target else None, "value": None,
           "unit": unit, "quote": None, "exceptions": [], "declines": [],
           "ambiguous": True, "problems": []}
    if target is None:
        out["problems"] = ["предмет вопроса параметра не определяется каталогом"]
        return out
    parts = fp._decline_parts(raw)
    qualifier = fp.applicability_qualifier(raw, unit)
    if qualifier:
        # Exact raw preserves all context; resolved=False forbids treating this
        # evidence as an executable condition or a request to delete the tail.
        out["condition"] = {"raw": raw, "resolved": False}
        if any(hit.group().lower() in ("кроме", "исключая", "за исключением")
               for hit in fp._APPLICABILITY_INTRO_RE.finditer(raw)):
            out["exceptions"] = [raw]
    if parts is not None:
        out["declines"] = [raw]
        out["ambiguous"] = bool(parts[1])
        if parts[1]:
            out["problems"] = ["отказ не является безусловным решением всего ответа"]
        return out
    from cdpm import answer_subjects
    subject_problem = answer_subjects.problem(raw, target[0], target[1], unit)
    if subject_problem:
        out["problems"] = [subject_problem]
        return out
    value_view = answer_subjects.value_text(raw, target[0], target[1], unit)
    if not fp.applicability_qualifier(value_view, unit):
        # A period/scope word belonging solely to the exact catalogue subject
        # is not a condition. Genuine extra restrictions still retain full raw.
        out.pop("condition", None)
        out["exceptions"] = []
    got = answer_subjects.read_value(raw, target[0], target[1], unit, fp.default_domain(unit))
    if got is None:
        quantity = fp._QTY_RE.fullmatch(value_view.strip()) if isinstance(value_view, str) else None
        if unit in ("duration_minutes", "time_of_day_minutes") and quantity is not None and not any(
                quantity.group(name) for name in ("h", "mi", "d")):
            out["problems"] = [
                "единица: длительность указана без единицы; добавьте единицу времени"
                if unit == "duration_minutes" else
                "единица: время суток задано одним числом; укажите его в формате ЧЧ:ММ"]
            return out
        out["problems"] = [answer_subjects.value_context_problem(raw, target[0], target[1], unit)
                           or "значение не разобрано в области параметра"]
        return out
    out.update(value=copy.deepcopy(got[0]), quote=got[1], ambiguous=False)
    return out


def problems(q) -> list[str]:
    answer = q.get("answer") if isinstance(q, dict) else None
    if not isinstance(answer, dict) or "parsed" not in answer:
        return []  # historical absence is not a newly inferred parse
    from cdpm import catalog_bridge, family_params as fp
    if not fp.is_param_question(q.get("id")):
        return ["parsed: эта форма разбора предназначена для вопроса параметра"]
    raw = answer.get("raw")
    if not isinstance(raw, str):
        return ["parsed: дословный ответ должен быть строкой"]
    try:
        expected = parameter(q, raw)
    except catalog_bridge.CATALOG_ERRORS as exc:
        return [f"parsed: разбор нельзя сверить с каталогом: {exc}"]
    # JSON comparison is type-sensitive: False != 0, True != 1.
    try:
        same = json.dumps(answer["parsed"], sort_keys=True, ensure_ascii=False,
                          allow_nan=False) == json.dumps(
                              expected, sort_keys=True, ensure_ascii=False,
                              allow_nan=False)
    except (TypeError, ValueError):
        same = False
    return [] if same else [
        "parsed: сохранённый разбор не соответствует дословному ответу "
        "и предмету вопроса; значение или ограничения изменены"]


def document_problems(doc) -> list[str]:
    questions = doc.get("questions", []) if isinstance(doc, dict) else []
    if not isinstance(questions, list):
        return []
    return [f"{q.get('id', '?')}: {p}" for q in questions if isinstance(q, dict)
            for p in problems(q)]
