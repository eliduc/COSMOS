"""Code decisions are separate from immutable questions and airline answers."""
from __future__ import annotations


def shape_problems(q: dict) -> list[str]:
    if "code_resolutions" not in q:
        return []
    records = q["code_resolutions"]
    if not isinstance(records, list) or not records:
        return ["решения кода должны быть непустым списком"]
    seen = set()
    refs = q.get("rule_refs") or []
    if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
        return ["неверная область вопроса для решения кода"]
    for rec in records:
        if not isinstance(rec, dict):
            return ["неверная форма решения кода"]
        fields = {"rule", "data", "reason", "revision"} if "data" in rec else {"rule", "family", "kind", "reason", "revision"}
        if set(rec) != fields:
            return ["неверная форма решения кода"]
        if any(not isinstance(rec[k], str) or not rec[k].strip()
               for k in (("rule", "reason") if "data" in rec else ("rule", "family", "reason"))):
            return ["неверные значения решения кода"]
        if "data" in rec:
            from .missing_data import proof_shape
            if not proof_shape(rec["data"], rec["rule"]):
                return ["неверное положительное свидетельство данных"]
        elif rec["kind"] not in ("HARD", "SOFT"):
            return ["неверные значения решения кода"]
        if type(rec["revision"]) is not int or rec["revision"] < 1:
            return ["неверная ревизия решения кода"]
        if rec["rule"] not in refs or rec["rule"] in seen:
            return ["решение кода повторено или вне области вопроса"]
        seen.add(rec["rule"])
    return []


def resolved_targets(q: dict) -> set[str]:
    if shape_problems(q):
        return set()
    return {r["rule"] for r in q.get("code_resolutions", [])}


def complete(q: dict) -> bool:
    if not q.get("code_resolutions") or shape_problems(q):
        return False
    refs = q.get("rule_refs")
    return isinstance(refs, list) and bool(refs) and set(refs) <= resolved_targets(q)


def problems(doc: dict, q: dict) -> list[str]:
    found = shape_problems(q)
    if found or not q.get("code_resolutions"):
        return found
    from cdpm import model as m
    rules = {r.get("id"): r for r in doc.get("rules", []) if isinstance(r, dict)}
    traits = m.template_family_traits()
    for rec in q["code_resolutions"]:
        if "data" in rec:
            from .missing_data import proof_problems
            found.extend(proof_problems(doc, q, rec))
            continue
        r = rules.get(rec["rule"])
        if r is None:
            found.append(f"нет строки решения кода: {rec['rule']}")
            continue
        fit = r.get("template_fit") or {}
        if not isinstance(fit, dict) or fit.get("category") not in ("A", "B") \
                or fit.get("family") != rec["family"] \
                or (traits.get(rec["family"]) or {}).get("kind") != rec["kind"]:
            found.append(f"решение кода не подтверждено семьёй: {rec['rule']}")
        cond = r.get("conditional") or {}
        if r.get("kind") != rec["kind"] or q.get("id") in (r.get("blockers") or []) \
                or isinstance(cond, dict) and cond.get("question") == q.get("id"):
            found.append(f"решение кода расходится с живой строкой: {rec['rule']}")
    return found


def notes(q: dict, *, doc: dict | None = None) -> list[str]:
    if doc is not None and problems(doc, q):
        return ["Решение кода не подтверждено текущей моделью: " + "; ".join(problems(doc, q))]
    if shape_problems(q):
        return ["Некорректное решение кода — требуется проверка модели"]
    return [f"{r['rule']}: вопрос снят кодом — {r['reason']} (ревизия {r['revision']})"
            for r in q.get("code_resolutions", [])]


def display_answer(q: dict, *, doc: dict | None = None) -> dict:
    """A derived display row, never an invented persisted airline answer."""
    ans = dict(q["answer"]) if isinstance(q.get("answer"), dict) else {}
    if ans.get("closed_by") == "code":
        ans["status"] = "снят кодом"
    if doc is not None and ans.get("status") == "ожидает обработчика":
        from cdpm import question_editions
        if question_editions.is_superseded(doc, q):
            ans["status"] = "не применён — вопрос переиздан"
    if not complete(q) or (doc is not None and problems(doc, q)):
        return ans
    if doc is not None and ans.get("note"):
        # Keep the airline's recorded explanation intact, but do not present
        # the effect of its old choice as the current code-resolved state.
        ans["note"] = ("Историческая заметка к прежнему ответу "
                       "(не текущее состояние): " + ans["note"])
    old_status = ans.get("status") if ans.get("closed_by") != "code" else None
    ans.setdefault("raw", "ответ авиакомпании отсутствует")
    ans.setdefault("revision", max(r["revision"] for r in q["code_resolutions"]))
    ans["status"] = "снят кодом" + (f"; прежний ответ: {old_status}" if old_status else "")
    return ans
