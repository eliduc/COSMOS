"""Exact, edition-bound authorization for a closed-list extension.

This is not an unrestricted natural-language entailment engine. It verifies
one explicit enumeration (membership notation or an explicit positive list)
while preserving its surrounding condition. Unverifiable rewrites stay pending.
Identifier equality is exact; Unicode diagnostics never repair identifiers.
"""
from __future__ import annotations

import json
import re
import unicodedata

from unicode_data import code_points, confusable_hint, visually_confusable


def identity_hint(value: str, candidates) -> str:
    """Shared visual matching plus the first exact differing code point.

    The common helper abbreviates long strings; still name the actual
    difference even when it falls beyond the displayed prefix.
    """
    known = {c for c in candidates if isinstance(c, str)}
    hint = confusable_hint(value, known)
    if not hint:
        return ""
    details = []
    for other in sorted(c for c in known if visually_confusable(value, c))[:3]:
        pos = next((i for i, (a, b) in enumerate(zip(value, other)) if a != b),
                   min(len(value), len(other)))
        def at(text):
            return (f"{text[pos]!r} ({code_points(text[pos])})" if pos < len(text)
                    else "конец строки")
        details.append(f"позиция {pos + 1}: в искомом {at(value)}, в перечне {at(other)}")
    return hint + " Первое посимвольное отличие: " + "; ".join(details) + "."


def wording(evidence: dict) -> dict:
    # Keep simple historical labels, but visibly delimit multiword/delimiter
    # values. The machine authority is the array, never this joined string.
    def label(value):
        return (json.dumps(value, ensure_ascii=False)
                if re.search(r'[\s,;{}"«»]', value) else value)
    values = ", ".join(label(v) for v in evidence["values"])
    return {
        "text": (f"Операционные данные содержат значения {values}, "
                 "которых нет в закрытом перечне правила "
                 f"({evidence['statement']}). Как поступить?"),
        "answer_format": {"type": "choice", "options": [
            {"key": "а", "text": f"добавить {values} в перечень свода (правило дополняется)"},
            {"key": "б", "text": f"исключить записи со значениями {values} из планирования"},
        ]},
    }


def evidence_problems(q: dict) -> list[str]:
    if "data_scope" not in q:
        return []  # Legacy editions remain valid, but cannot authorize a patch.
    if not str(q.get("id", "")).startswith(("Q-3a-данные-", "Q-3b-данные-")):
        return ["данные перечня: свидетельство вне вопроса о закрытом перечне"]
    e = q["data_scope"]
    if (not isinstance(e, dict) or set(e) != {"rule", "statement", "values"}
            or not isinstance(e.get("rule"), str) or not e["rule"]
            or not isinstance(e.get("statement"), str)
            or not isinstance(e.get("values"), list) or not e["values"]
            or any(not isinstance(v, str) or not v for v in e["values"])
            or len(set(e["values"])) != len(e["values"])):
        return ["данные перечня: нечитаемое свидетельство выбора"]
    if q.get("rule_refs") != [e["rule"]]:
        return ["данные перечня: свидетельство относится к другой области вопроса"]
    expected = wording(e)
    if any(q.get(k) != v for k, v in expected.items()):
        return ["данные перечня: показанные варианты не совпадают со свидетельством выбора"]
    return []


_AND_SEPARATOR = re.compile(r"\s+и\s+", re.IGNORECASE)
_ITEM_SEPARATOR = re.compile(r"(?:[,;]\s*(?:и\s+)?|и\s+)", re.IGNORECASE)
_SET_SEPARATOR = re.compile(r"[,;]")


def _items(text: str, *, prose: bool = False) -> set[str] | None:
    """Delimited items, with whole multiword names and quoted delimiter values.

    Whitespace outside quotes is layout; inside quotes it is identifier data.
    Quoting uses JSON strings, guillemets or simple single quotes. No NFC,
    case folding or removal of invisible characters is allowed here.
    """
    values, pos = [], 0
    decoder = json.JSONDecoder()
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos == len(text):
            return None
        if text[pos] == '"':
            try:
                value, size = decoder.raw_decode(text[pos:])
            except ValueError:
                return None
            if not isinstance(value, str):
                return None
            pos += size
        elif text[pos] in ("«", "'"):
            end = text.find("»" if text[pos] == "«" else "'", pos + 1)
            if end < 0:
                return None
            value, pos = text[pos + 1:end], end + 1
        else:
            start = pos
            while pos < len(text) and text[pos] not in ",;":
                if prose and _AND_SEPARATOR.match(text, pos):
                    break
                pos += 1
            value = text[start:pos].strip()
            if any(c in value for c in '{}[]"«»\n\r'):
                return None
            if prose and any(c in value for c in ".!?()"):
                # A sentence/qualification is not a lost identifier. Dotted
                # or parenthesized literal names need explicit quoting here.
                return None
        if not value:
            return None
        values.append(value)
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos < len(text):
            separator = (_ITEM_SEPARATOR if prose else _SET_SEPARATOR).match(text, pos)
            if separator is None:
                return None
            pos = separator.end()
            if not text[pos:].strip():
                return None
    return set(values) if values else None


_PROSE = re.compile(
    r"(?P<head>(?:разрешены|допустимы|допустимые|разрешённые|разрешенные)"
    r"\s+(?:типы|базы|коды|значения)(?:\s*:\s*|\s+))(?P<items>.+?)(?P<tail>\.)?",
    re.IGNORECASE | re.DOTALL)

_CLOSED_DEFINITION = re.compile(
    r"(?P<head>[^:—–\n]+?\s*[:—–]\s*)(?P<items>.+?)\s*"
    r"(?P<tail>\(закрытый\s+перечень\)\.?)", re.IGNORECASE)
_EXCLUSIVE_TYPES = re.compile(
    r"(?P<head>[^.;{}]+?\s+состоит\s+только\s+из\s+"
    r"(?:(?:воздушных\s+судов|судов|ВС)\s+)?тип(?:а|ов)\s+)(?P<items>.+?)"
    r"(?P<tail>(?:;\s*(?:\w+\s+)?иных\s+типов(?:\s+в\s+наборе)?\s+нет)?\.?)",
    re.IGNORECASE)
_NOMINAL_DEFINITION = re.compile(
    r"(?P<head>(?:должности|базы|типы|коды)\b[^:—–.]*[:—–]\s*)"
    r"(?P<items>.+?)(?P<tail>\.?)", re.IGNORECASE)
_NEGATIVE_HEAD = re.compile(r"\b(?:не|кроме|например|запрещ\w*|исключ\w*)\b", re.IGNORECASE)
_SET_DEFINITION_HEAD = re.compile(
    r"(?:домен|множество)\s+[^={}<>!\n]+?\s*=\s*", re.IGNORECASE)


def enumeration(statement: str) -> tuple[str, set[str], str] | None:
    # Exactly one set: a second set would require an explicit target selector.
    if statement.count("{") == statement.count("}") == 1:
        start, end = statement.index("{"), statement.index("}")
        head = statement[:start].strip()
        domain_equality = (_SET_DEFINITION_HEAD.fullmatch(head)
                           and not _NEGATIVE_HEAD.search(head))
        if start >= end or not (head.endswith("∈") or domain_equality):
            return None
        items = _items(statement[start + 1:end])
        return (statement[:start], items, statement[end + 1:]) if items is not None else None
    for form in (_PROSE, _CLOSED_DEFINITION, _EXCLUSIVE_TYPES, _NOMINAL_DEFINITION):
        match = form.fullmatch(statement.strip())
        if match and not _NEGATIVE_HEAD.search(match["head"]):
            # A named scalar after the list is context, not another code.
            # Parsing the prefix also proves the separator is outside quotes.
            for boundary in re.finditer(
                    r";\s*главная\s+база(?:\s*[=—–:]|\s+)", match["items"], re.IGNORECASE):
                prefix_items = _items(match["items"][:boundary.start()], prose=True)
                if prefix_items is not None:
                    return (match["head"], prefix_items,
                            match["items"][boundary.start():] + (match["tail"] or ""))
            items = _items(match["items"], prose=True)
            if items is not None:
                return match["head"], items, match["tail"] or ""
            if form is _PROSE:
                # An explicit residual class is a following condition, not
                # part of the last list member. Parse the prefix to prove
                # this delimiter is outside a quoted identifier.
                for boundary in re.finditer(
                        r"\.\s+(?=(?:прочие|остальные|другие|иные)\b)",
                        match["items"], re.IGNORECASE):
                    items = _items(match["items"][:boundary.start()], prose=True)
                    if items is not None:
                        return (match["head"], items,
                                match["items"][boundary.start():] + (match["tail"] or ""))
    return None


def _context(text: str) -> str:
    # Only layout outside identifiers; preserve symbols, negation and wording.
    return "".join(c for c in text if not c.isspace() and unicodedata.category(c) != "Cf")


def _carriers(rule: dict) -> list[tuple[str, set[str] | None]]:
    """All explicit executable list carriers in this single-list rule.

    A second independent list cannot be retargeted by this question: refuse
    an ambiguous carrier instead of claiming that changing prose is enough.
    """
    from cdpm import model as m
    code_units = m._cat("CODE_UNITS")
    out = []
    def codes(value, unit):
        if isinstance(value, str):
            return set(m.split_code_list(value))
        if (unit == "code_list" and isinstance(value, list) and value
                and all(isinstance(item, str) and item.strip() for item in value)
                and len(set(value)) == len(value)):
            return set(value)  # opaque labels, not str(list) or prose
        return None

    def walk(value, path):
        if isinstance(value, dict):
            if "allowed" in value:
                vals = value["allowed"]
                out.append((path + ".allowed", set(vals) if isinstance(vals, list)
                            and all(isinstance(v, str) for v in vals) else None))
            if value.get("unit") in code_units and "value" in value:
                val = value["value"]
                # Use the executable carrier's parser, not the prose grammar.
                out.append((path + ".value", codes(val, value["unit"])))
            for key, child in value.items():
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]")
    for field in ("params", "logic", "template_fit"):
        walk(rule.get(field), field)
    fit = rule.get("template_fit") or {}
    for name, cell in (fit.get("params") or {}).items():
        unit = m.fit_param_unit(fit.get("family"), name)
        if unit in code_units:
            value = cell.get("value") if isinstance(cell, dict) else cell
            out.append((f"template_fit.params.{name}", codes(value, unit)))
    if rule.get("codes"):
        codes = [entry.get("code") for entry in rule["codes"]]
        out.append(("codes", set(codes) if all(isinstance(c, str) for c in codes) else None))
    if rule.get("formal"):
        parsed = enumeration(rule["formal"])
        out.append(("formal", parsed[1] if parsed else None))
    return out


def extension_problems(q: dict, old_rules: dict, new_rules: dict) -> list[str]:
    issues = evidence_problems(q)
    if issues:
        return issues
    e = q.get("data_scope")
    if not e:
        return ["выбор по данным не внесён: у старого вопроса нет точного свидетельства "
                "перечня; повторите проверку данных и ответьте на новое издание"]
    if set(old_rules) != {e["rule"]} or e["rule"] not in new_rules:
        return ["выбор по данным не внесён: область патча не совпадает с перечнем вопроса"]
    old, new = old_rules[e["rule"]], new_rules[e["rule"]]
    if old.get("statement") != e["statement"]:
        return ["правило изменилось после вопроса; повторите проверку данных и ответьте "
                "на новое издание, прежний ответ не перенесён"]
    before, after = enumeration(e["statement"]), enumeration(new.get("statement", ""))
    if before is None or after is None:
        return ["выбор по данным не внесён: нельзя однозначно проверить этот перечень; "
                "сохраните явный перечень вида «поле ∈ {значения}» или "
                "«Разрешены типы …» и его условие; свободный пересказ не применяется"]
    if (_context(before[0]), _context(before[2])) != (_context(after[0]), _context(after[2])):
        return ["выбор по данным не внесён: изменено условие вне перечня; "
                "этот ответ разрешает только добавление выбранных значений"]
    required = before[1] | set(e["values"])
    found = after[1]
    issues = []
    for value in sorted(required - found):
        label = "прежнее значение потеряно" if value in before[1] else "запрошенное значение не внесено"
        issues.append(f"{label}: {value!r}" + identity_hint(value, found))
    if found - required:
        issues.append("добавлены значения, которых не разрешает этот ответ: "
                      + ", ".join(repr(v) for v in sorted(found - required)))
    # Updating prose while leaving a stale executable list is not completion.
    old_carriers, new_carriers = dict(_carriers(old)), dict(_carriers(new))
    for label in old_carriers.keys() - new_carriers.keys():
        issues.append(f"{label}: прежний машинный перечень потерян")
    for label, values in old_carriers.items():
        if values != before[1]:
            issues.append(f"{label}: исходный машинный перечень неоднозначен; требуется уточнение области")
    for label, values in new_carriers.items():
        if values != required:
            issues.append(f"{label}: машинный перечень не совпадает с разрешённым расширением")
    if old.get("formal") and new.get("formal"):
        old_formal, new_formal = enumeration(old["formal"]), enumeration(new["formal"])
        if old_formal and new_formal and (
                _context(old_formal[0]), _context(old_formal[2])) != (
                    _context(new_formal[0]), _context(new_formal[2])):
            issues.append("formal: изменено условие вне перечня")
    if before[1] == found:
        issues.append("выбор по данным не внесён: перечень не расширен; изменение служебных полей не является применением")
    return issues
