"""Shared current-format parameter projection; no writes to canonical rules."""
from __future__ import annotations

import copy
import re
from decimal import Decimal

from cdpm import model as m


class ParamProjectionError(ValueError):
    def __init__(self, words, code=None, items=None):
        super().__init__(words)
        self.code = code
        self.items = list(items or [])


def _refuse_diverging_carriers(rule):
    found = m.carrier_problems({"rules": [rule]})
    if found:
        raise ParamProjectionError(
            "; ".join(p["words"] for p in found), m.PARAM_CARRIERS_DIVERGE,
            [f"{p['rule']}/{p['param']}" for p in found])


def _project_base_map(rule, name, cell, spec, previous):
    """Expand a count/base_code map into existing qualified params records.

    The original carriers have already been compared. Their agreement with
    a subset is not evidence that the subset covers the whole current map.
    No independent scope condition can be erased to make expansion succeed.
    """
    from cdpm import family_params as fp
    address = f"{rule.get('id')}/{name}"

    def refuse(words):
        raise ParamProjectionError(f"{address}: {words}", items=[address])

    table = fp.base_map_value(cell.get("value"))
    if table is None:
        refuse("норма по базам требует непустую карту уникальных кодов и целых конечных значений")
    # Current RunThresholds.knobsFor accepts keyed bases [A-Z0-9]{2,4}.
    # Do not silently uppercase/merge distinct codes or emit an ignored key.
    if any(not re.fullmatch(r"[A-Z0-9]{2,4}", base) for base in table):
        refuse("код базы не представим текущим потребителем: нужны 2–4 символа A–Z/0–9 без подмены обозначения")
    raw_unit = cell.get("unit")
    if not isinstance(raw_unit, (str, type(None))) or fp.unit_scale(raw_unit, "count") != 1:
        refuse("единица карты норм не совместима с count")
    relation = cell.get("relation")
    if relation is None:
        relation = spec.get("relation")
    if relation != spec.get("relation") and relation != "eq":
        refuse("отношение карты норм не соответствует отношению семьи или точному равенству")
    if "condition" in cell:
        condition = cell["condition"]
        if not isinstance(condition, str) or len(table) != 1 or condition not in table:
            refuse("условие карты нельзя выразить одним уточнителем базы без потери области")
    quote_problems = fp.base_map_quote_problems(table, cell.get("quote"))
    if quote_problems:
        refuse("; ".join(quote_problems))

    by_base = {}
    whole = None
    for p in previous:
        if not isinstance(p.get("unit"), (str, type(None))) or fp.unit_scale(p.get("unit"), "count") != 1:
            refuse("единица старой карты норм не совместима с count")
        if fp.is_structural(p.get("value")) or fp.base_map_value(p.get("value")) is not None:
            if whole is not None or p.get("qualifier") is not None:
                refuse("повторная или дополнительно ограниченная структурная запись params")
            whole = p
        else:
            base = p.get("qualifier")
            if not isinstance(base, str) or base.strip() not in table:
                refuse("старая скалярная норма не называет базу текущей карты")
            base = base.strip()
            if base in by_base:
                refuse("несколько старых записей одной базы; выбор первой запрещён")
            by_base[base] = p
    if whole is not None and by_base:
        refuse("структурная и скалярные старые записи дублируют одну карту")

    out = []
    for base, value in sorted(table.items()):
        projected = copy.deepcopy(by_base.get(base) or whole or {"name": name})
        if not projected.get("as_written"):
            projected["as_written"] = cell.get("quote")
        trace_problems = fp.base_map_quote_problems({base: value}, projected.get("as_written"))
        if trace_problems:
            refuse("as_written: " + "; ".join(trace_problems))
        projected.update(value=int(value), unit="count", relation=relation, qualifier=base)
        if "tz" in cell:
            projected["tz"] = cell["tz"]
        problems = m.validate_param(projected, str(rule.get("kind") or ""))
        if problems:
            refuse("совместимая проекция невозможна: " + "; ".join(problems))
        out.append(projected)
    return out


def derive_params_from_landing(rule: dict, *, selected_names: set[str] | None = None) -> list[dict]:
    """Чистая совместимая проекция параметров текущей посадки.

    Сначала сверяются ИСХОДНЫЕ носители. Условия исключений, которых
    нет в словаре params[], остаются только в посадке. Старые записи
    без ячейки сохраняются; это не миграция landing/2. Непредставимая
    новая запись отказывает словами, а не сплющивается до числа.
    selected_names ограничивает только представление для читателя ссылок;
    исходные носители всё равно сверяются целиком. None — полная проекция
    выпуска, её строгость не меняется.
    """
    from cdpm import family_params as fp
    rid = str(rule.get("id"))
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and "schema_version" in fit:
        from cdpm import catalog_bridge
        from template_catalog import landing
        found = landing.problems(rule, catalog_bridge.catalog())
        if found:
            raise ParamProjectionError(
                "; ".join(p["words"] for p in found), found[0]["code"],
                [f"{p['rule']}/{p['param']}" if p["param"] else p["rule"] for p in found])
    legacy = rule.get("params")
    if legacy is None:
        legacy = []
    if not isinstance(legacy, list) or any(not isinstance(p, dict)
            or not isinstance(p.get("name"), str) for p in legacy):
        raise ParamProjectionError(f"{rid}: params не список записей — проекция невозможна", items=[rid])
    _refuse_diverging_carriers(rule)
    out = copy.deepcopy(legacy)
    if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
        return out if selected_names is None else [p for p in out if p["name"] in selected_names]
    cells = fit.get("params", {})
    if not isinstance(cells, dict):
        raise ParamProjectionError(f"{rid}: params посадки не объект — проекция невозможна", items=[rid])
    names = m.PARAMS
    for name, cell in cells.items():
        if selected_names is not None and name not in selected_names:
            continue
        if name not in names:
            continue  # условия исключений не входят в старый словарь params[]
        address = f"{rid}/{name}"
        spec = fp._param_spec(str(fit.get("family") or ""), name)
        if not spec or not isinstance(cell, dict):
            raise ParamProjectionError(f"{address}: нет читаемой ячейки/параметра семьи для проекции", items=[address])
        same = [(i, p) for i, p in enumerate(out) if p.get("name") == name]
        packed_map = isinstance(cell.get("value"), str) and ":" in cell["value"] \
            and spec.get("unit") == "count" and spec.get("qualifier") == "base_code"
        code_unit = spec.get("unit") in m.CODE_UNITS
        if (fp.is_structural(cell.get("value")) and not code_unit) or packed_map:
            if spec.get("unit") == "count" and spec.get("qualifier") == "base_code":
                view_cell = cell
                if fit.get("schema_version") == "landing/2":
                    # Only the legacy view loses the structural wrapper.
                    # The pinned cell was validated above and stays intact.
                    view_cell = copy.deepcopy(cell)
                    condition = view_cell.pop("condition")
                    if condition is not None:
                        view_cell["condition"] = condition["value"]
                records = _project_base_map(rule, name, view_cell, spec, [p for _, p in same])
                # Preserve unrelated records and deterministic placement; replace
                # the entire old group, never just its first matching base.
                insertion = same[0][0] if same else len(out)
                out = [p for p in out if p.get("name") != name]
                out[insertion:insertion] = records
                continue
            # Согласованная старая структурная пара остаётся без изменения.
            # Новую норму по базам нельзя выдавать как безусловный скаляр.
            if same:
                continue
            raise ParamProjectionError(f"{address}: структурное значение требует поддерживаемой "
                                   "машинной формы; скалярная проекция невозможна", items=[address])
        unit = spec.get("unit")
        value, problem = fp.canonical_value(cell.get("value"), cell.get("unit"), unit)
        if problem:
            raise ParamProjectionError(f"{address}: {problem}", items=[address])
        if fit.get("schema_version") == "landing/2" and code_unit:
            # The legacy comma-separated carrier cannot escape a comma
            # inside one opaque designation. Its round trip must preserve
            # the exact labels, not merely the same joined text.
            represented = (sorted(m.split_code_list(value)) if unit == "code_list" else value)
            original = (sorted(cell["value"]) if unit == "code_list" else cell["value"])
            if represented != original:
                raise ParamProjectionError(
                    f"{address}: обозначения не представимы старым носителем без изменения", items=[address])
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                value = int(value)
            else:
                converted = float(value)
                if not value.is_finite() or Decimal(str(converted)) != value:
                    raise ParamProjectionError(f"{address}: значение не представимо числом JSON без округления", items=[address])
                value = converted
        for index, previous in same or [(None, {"name": name, "as_written": cell.get("quote")})]:
            projected = copy.deepcopy(previous)
            missing_trace = not projected.get("as_written")
            if missing_trace:
                projected["as_written"] = cell.get("quote")
            relation = cell.get("relation")
            if relation is None:
                relation = spec.get("relation")
            if code_unit and spec.get("relation") is None:
                # Code/code_list are exact labels, not numeric bounds. Their
                # established params[] representation uses eq (code-parameter
                # contract), while the catalog intentionally has no bound.
                # Keep an explicit relation, including invalid values so the
                # validator refuses them; never turn empty text/False/0/[] into eq.
                relation = next((source["relation"] for source in (cell, previous)
                                 if source.get("relation") is not None), "eq")
            projected.update(value=value, unit=unit, relation=relation)
            if (index is None or missing_trace) and unit in ("duration_minutes", "time_of_day_minutes"):
                quote = cell.get("quote")
                if isinstance(quote, str) and m.canon_minutes(quote) != value:
                    parsed = fp.parse_value(quote, unit)
                    if parsed and parsed[0] == value:
                        # Старый trace — дословный числовой фрагмент;
                        # ПОЛНАЯ исходная цитата остаётся в посадке.
                        for token_value, (start, end) in fp._sentence_tokens(parsed[1], unit) or []:
                            fragment = parsed[1][start:end]
                            if token_value == value and m.canon_minutes(fragment) == value:
                                projected["as_written"] = fragment
                                break
            if "condition" in cell:
                condition = cell["condition"]
                if condition is None:
                    projected.pop("qualifier", None)
                else:
                    projected["qualifier"] = (condition["value"]
                        if fit.get("schema_version") == "landing/2" else copy.deepcopy(condition))
            if "tz" in cell:
                projected["tz"] = cell["tz"]
            # Область area может быть общей; base_code задаёт ключ нормы
            # по конкретной базе и не допускает безымянного скаляра (§3.1).
            if spec.get("qualifier") == "base_code" and not projected.get("qualifier"):
                raise ParamProjectionError(f"{address}: скаляр не называет уточнитель "
                                       f"{spec['qualifier']}; расширять область нельзя", items=[address])
            problems = m.validate_param(projected, str(rule.get("kind") or ""))
            if problems:
                raise ParamProjectionError(f"{address}: совместимая проекция невозможна: "
                                       + "; ".join(problems), items=[address])
            if index is None:
                out.append(projected)
            else:
                out[index] = projected
    candidate = copy.deepcopy(rule)
    candidate["params"] = out
    _refuse_diverging_carriers(candidate)
    return out if selected_names is None else [p for p in out if p["name"] in selected_names]
