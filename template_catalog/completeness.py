"""Pure catalogue set coverage shared by producer and consumer (§3.2).

Inputs are names whose values/provenance the caller has already checked.
This module does not interpret values, read files or decide semantic changes.
An open revision is distinct from absent data: pending does not invent a
missing parameter, but it prevents declaring a completed decision.
"""
from __future__ import annotations


def parameter_sets(record):
    """Ordered (id, members) choices and structural problems; no partial set."""
    if not isinstance(record, dict):
        return [], ["запись семьи не объект"]
    if not isinstance(record.get("id"), str) or not record["id"].strip():
        return [], ["запись семьи без идентификатора"]
    family = str(record.get("id") or "—")
    problems, names, choices = [], [], []
    params = record.get("params", [])
    if not isinstance(params, list):
        return [], [f"параметры записи семьи {family} не список"]
    for param in params:
        if (not isinstance(param, dict) or not isinstance(param.get("name"), str)
                or not param["name"].strip() or param["name"] in names):
            problems.append(f"параметр записи семьи {family} не имеет однозначного имени")
        else:
            names.append(param["name"])
    declared = record.get("param_sets", [])
    if not isinstance(declared, list):
        problems.append(f"наборы записи семьи {family} не список")
        declared = []
    seen = set()
    for index, item in enumerate(declared):
        if not isinstance(item, dict):
            problems.append(f"набор №{index + 1} записи семьи {family} не "
                            f"объект ({item!r}) — отброшен")
            continue
        sid, members = item.get("id"), item.get("params")
        if (not isinstance(sid, str) or not sid.strip() or sid in seen
                or not isinstance(members, list) or not members
                or any(not isinstance(p, str) for p in members)
                or len(set(members)) != len(members)):
            problems.append(f"набор №{index + 1} записи семьи {family} имеет неверную "
                            "форму или повторённый идентификатор — отброшен")
            continue
        seen.add(sid)
        unknown = sorted(set(members) - set(names))
        if unknown:
            problems.append(f"набор «{sid}» записи семьи {family} "
                            f"называет параметр(ы) {', '.join(unknown)}, которых у "
                            "записи нет, — набор отброшен целиком, требования "
                            "читаются по остальным (fail-closed)")
            continue
        choices.append((sid, list(members)))
    if not choices and (names or not problems):
        choices = [("all", list(names))]
    return choices, problems


def family_completeness(record, given_names, declined_names=(), pending_names=(), *, set_id=None):
    """Coverage only; an explicit set never falls back to an easier choice."""
    choices, problems = parameter_sets(record)
    given, declined, pending = (set(str(n) for n in (values or ()))
                                for values in (given_names, declined_names, pending_names))
    closed = given | declined
    selected = None
    if set_id is not None:
        selected = next((item for item in choices if item[0] == set_id), None)
        if selected is None:
            problems.append(f"у записи семьи нет выбранного набора {set_id!r}")
    elif choices:
        selected = next((item for item in choices if set(item[1]) <= closed), None)
        if selected is None:
            selected = min(choices, key=lambda item: (
                -len(set(item[1]) & closed), len(set(item[1]) - closed)))
    members = selected[1] if selected else []
    missing = [name for name in members if name not in closed]
    return {"complete": not missing and not pending and not problems,
            "missing": missing, "set_id": selected[0] if selected else None,
            "given": sorted(given), "declined": sorted(declined),
            "pending": sorted(pending), "problems": problems}
