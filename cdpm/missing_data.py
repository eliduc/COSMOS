"""Immutable missing-data questions and per-target positive retirement."""
from __future__ import annotations
import copy

PREFIXES = ("Q-3a-нет-данных-", "Q-3b-нет-данных-")
OPTIONS = [
    {"key": "а", "text": "да — считать до получения данных; правило исключается из расчёта с пометкой и помечается в сертификате непроверенным; при поступлении данных — пересчёт с правилом"},
    {"key": "б", "text": "нет — ждать данных; расчёт не начинается, пока данные не поступят"},
]
REASONS = {
    "present": "данные поступили: проверки правила прогнаны; пометка исключения снята",
    "coverage_gap": "пробел покрытия: шаблона в каталоге нет (посадка C); правило ждёт шаблона, пакет не выдаётся",
    "executor": "правило судится решением (сертификат): у семьи каталог объявил исполнителя; исполнимость судит K06-барьер",
}

def is_question(q):
    return isinstance(q, dict) and str(q.get("id", "")).startswith(PREFIXES)

def content(entries, note=""):
    fmt = {"type": "choice", "options": copy.deepcopy(OPTIONS)}
    if note:
        fmt["note"] = note
    return {
        "title": "Данных для правил " + ", ".join(e["rule"] for e in entries) + " нет в книге",
        "text": ". ".join(f"Проверка правила {e['rule']} ({e['statement']}) не прогоняется: "
                          + "; ".join(e["reasons"]) for e in entries)
                + ". Данных для этих правил в наборе нет. Считать ли до получения данных?",
        "answer_format": fmt,
    }

def evidence_problems(q):
    if "missing_data" not in q:
        return []
    entries = q["missing_data"]
    if not is_question(q) or not isinstance(entries, list) or not entries:
        return ["неверное свидетельство недостающих данных"]
    seen = set()
    for e in entries:
        if not isinstance(e, dict) or set(e) != {"rule", "statement", "reasons"}:
            return ["неверная форма свидетельства недостающих данных"]
        if any(not isinstance(e[k], str) or not e[k] for k in ("rule", "statement")):
            return ["неверная строка свидетельства недостающих данных"]
        if (not isinstance(e["reasons"], list) or not e["reasons"]
                or any(not isinstance(s, str) or not s for s in e["reasons"])
                or len(set(e["reasons"])) != len(e["reasons"]) or e["rule"] in seen):
            return ["неверные причины недостающих данных"]
        seen.add(e["rule"])
    refs = q.get("rule_refs")
    if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs) or seen != set(refs):
        return ["свидетельство недостающих данных вне области вопроса"]
    fmt = q.get("answer_format")
    if not isinstance(fmt, dict):
        return ["неверный формат ответа на вопрос недостающих данных"]
    expected = content(entries, fmt.get("note", ""))
    return ["вопрос не совпадает со свидетельством недостающих данных"] if any(q.get(k) != v for k, v in expected.items()) else []

def proof_shape(data, rid):
    if not isinstance(data, dict) or not isinstance(data.get("outcome"), str) or data["outcome"] not in REASONS:
        return False
    required = {"outcome", "statement"}
    if data["outcome"] == "present":
        required.add("checks")
    elif data["outcome"] == "executor":
        required.add("family")
    if set(data) != required or not isinstance(data.get("statement"), str):
        return False
    if data["outcome"] == "executor":
        return isinstance(data.get("family"), str) and bool(data["family"])
    if data["outcome"] == "present":
        checks = data.get("checks")
        return (isinstance(checks, list) and bool(checks)
                and all(isinstance(c, dict) and set(c) == {"rule", "status", "id"}
                        and c["rule"] == rid and c["status"] == "прогнана"
                        and isinstance(c["id"], str) for c in checks))
    return True

def proof_problems(doc, q, rec):
    from . import model as m, question_editions as qe
    if not is_question(q) or not proof_shape(rec["data"], rec["rule"]):
        return ["некорректное положительное свидетельство данных"]
    data, rid = rec["data"], rec["rule"]
    # Historical proof remains evidence of that edition, not today's inputs.
    if qe.is_superseded(doc, q):
        return []
    r = next((r for r in doc.get("rules", []) if r.get("id") == rid), None)
    if r is None or r.get("statement", "") != data["statement"]:
        return ["решение о данных не соответствует текущей строке: " + rid]
    if q["id"] in r.get("blockers", []) or (r.get("excluded_until_data") or {}).get("question") == q["id"]:
        return ["решение о данных расходится с блокировкой/исключением: " + rid]
    fit = r.get("template_fit") or {}
    if data["outcome"] == "coverage_gap" and not (
            r.get("kind") in ("HARD", "SOFT") and isinstance(fit, dict) and fit.get("category") == "C"):
        return ["снятие по пробелу покрытия не подтверждено: " + rid]
    if data["outcome"] == "executor" and not (
            r.get("kind") in ("HARD", "SOFT") and isinstance(fit, dict)
            and fit.get("family") == data["family"] and m._executor_covers(r)):
        return ["снятие по исполнителю не подтверждено: " + rid]
    return []

def _lineage_ids(doc, q):
    by_id = {x["id"]: x for x in doc.get("questions", [])}
    ids = {q["id"]}
    while q.get("edition_of"):
        q = by_id[q["edition_of"]]
        ids.add(q["id"])
    return ids

def retire(doc, q, rule, proof):
    from . import model as m, question_editions as qe
    # A code decision is also durable history, even without an airline answer.
    qe.freeze(q)
    ids = _lineage_ids(doc, q)
    rule["blockers"] = [b for b in rule.get("blockers", []) if b not in ids]
    if not rule["blockers"]:
        rule.pop("blockers", None)
    if (rule.get("excluded_until_data") or {}).get("question") in ids:
        rule.pop("excluded_until_data", None)
        if proof["outcome"] == "present":
            rule["note"] = (rule.get("note", "") + "; данные поступили — пометка «исключено до получения данных» снята").strip("; ")
    elif proof["outcome"] == "present":
        rule["note"] = (rule.get("note", "") + f"; данные поступили — блокировка по вопросу {q['id']} снята").strip("; ")
    m.close_question_by_code(doc, q, raw="снят кодом", note=REASONS[proof["outcome"]],
                             rule_resolution={"rule": rule["id"], "data": copy.deepcopy(proof)})

def ensure(doc, report):
    from . import model as m, question_editions as qe, question_resolutions as qr
    rules = {r["id"]: r for r in doc.get("rules", [])}
    roots = [q for q in doc.get("questions", []) if is_question(q) and not q.get("edition_of")]
    current = {q["id"]: qe.current_for_root(doc, q["id"]) for q in roots}
    for q in current.values():
        if qr.shape_problems(q):
            raise qe.EditionRefusal("; ".join(qr.shape_problems(q)))
        if not qe.targets(q) <= set(rules):
            raise qe.EditionRefusal("вопрос недостающих данных адресует отсутствующую строку")
    checks, missing = {}, {}
    for c in report.get("checks", []):
        rid = m._canon_rule_ref((c.get("basis") or {}).get("rule"))
        if rid not in rules:
            continue
        checks.setdefault(rid, []).append(c)
        reason = str(c.get("reason") or "")
        if c.get("status") != "прогнана" and any(s in reason.lower() for s in m._MISSING_DATA_MARKERS):
            missing.setdefault(rid, set()).add(reason)
    positive = {rid for rid, cs in checks.items() if cs and all(c.get("status") == "прогнана" for c in cs)}

    def outcome(rid):
        r = rules[rid]
        fit = r.get("template_fit") or {}
        if r.get("kind") in ("HARD", "SOFT"):
            if isinstance(fit, dict) and fit.get("category") == "C":
                return "coverage_gap"
            if m._executor_covers(r):
                return "executor"
        return "present" if rid in positive else None

    eligible = {rid: reasons for rid, reasons in missing.items() if not outcome(rid)}
    transitions = set()
    for root, q in current.items():
        for rec in q.get("code_resolutions", []):
            if "data" not in rec or rec["rule"] in eligible:
                continue
            rid, data = rec["rule"], rec["data"]
            kind = outcome(rid)
            changed_basis = kind and (
                kind != data["outcome"]
                or data["statement"] != rules[rid].get("statement", "")
                or kind == "executor" and data.get("family") != rules[rid]["template_fit"]["family"])
            if changed_basis:
                transitions.add(root)
            elif proof_problems(doc, q, rec):
                # An absent report cannot invent a replacement for lost proof.
                # Refuse before altering any question, answer or live rule.
                raise qe.EditionRefusal("утрачено положительное основание снятия вопроса: " + rid)
    for key, wanted in (("coverage_gap", "coverage_gap"), ("judged_on_solution", "executor")):
        rows = [{"rule": rid, "why": "; ".join(sorted(reasons)), "note": REASONS[wanted]}
                for rid, reasons in sorted(missing.items()) if outcome(rid) == wanted]
        report.pop(key, None)
        if rows:
            report[key] = rows
    owners = {}
    for root, q in current.items():
        for rid in qe.targets(q):
            if rid in owners and owners[rid] != root:
                raise qe.EditionRefusal("недостающие данные: перекрывающиеся независимые вопросы для " + rid)
            owners[rid] = root

    def family(rid):
        r = rules[rid]
        return ("3a" if r.get("zone") == "PAIRING" else "3b",
                m.normalized_question_text(r.get("statement", "")), tuple(sorted(eligible[rid])))

    family_roots = {}
    for rid in sorted(eligible):
        if rid in owners:
            family_roots.setdefault(family(rid), owners[rid])
    grouped = {}
    for rid in sorted(eligible):
        key = family(rid)
        root = owners.get(rid) or family_roots.get(key)
        if root is None:
            root = m.stable_question_id(key[0], "нет-данных-" + rid)
            family_roots[key] = root
        grouped.setdefault(root, []).append(rid)
    for root in transitions:
        grouped.setdefault(root, [])
    created = []
    for root, added in sorted(grouped.items()):
        q = current.get(root)
        refs = set(added) | (qe.targets(q) if q else set())
        if not refs <= set(rules):
            raise qe.EditionRefusal("вопрос недостающих данных адресует отсутствующую строку")
        prior = {e["rule"]: e for e in (q.get("missing_data") or [])} if q else {}
        entries = []
        for rid in sorted(refs):
            reason = eligible.get(rid)
            if not reason:
                reason = set((prior.get(rid) or {}).get("reasons") or ["Ранее заданный вопрос: " + q["text"]])
            entries.append({"rule": rid, "statement": rules[rid].get("statement", ""), "reasons": sorted(reason)})
        fmt_note = (q.get("answer_format") or {}).get("note", "") if q else ""
        if q and qr.resolved_targets(q).intersection(added):
            fmt_note = f"Данные снова отсутствуют после снятия вопроса {q['id']}; требуется новый ответ."
        if root in transitions:
            fmt_note += f" Положительное основание снятия изменилось после вопроса {q['id']}."
            fmt_note = fmt_note.strip()
        draft = {
            "id": root, "part": q["part"] if q else ("3a" if rules[added[0]].get("zone") == "PAIRING" else "3b"),
            "rule_refs": sorted(refs), "missing_data": entries, **content(entries, fmt_note),
            "source_refs": qe.normative_rule_sources([rules[r] for r in sorted(refs)], q.get("source_refs", []) if q else []),
        }
        changed, old_q, carry = q is None, q, []
        if q is None:
            q = draft
            doc.setdefault("questions", []).append(q)
            current[root] = q
            created.append(q["id"])
        else:
            if q.get("answer") and "edition" not in q:
                qe.freeze(q)
            delta = {k: v for k, v in draft.items() if k not in ("id", "part", "rule_refs") and q.get(k) != v}
            changed = bool(delta) or refs != qe.targets(q)
            if changed:
                carry = [copy.deepcopy(rec) for rec in q.get("code_resolutions", [])
                         if rec["rule"] not in eligible and "data" in rec
                         and rec["data"]["statement"] == rules[rec["rule"]].get("statement", "")]
                if "edition" in q:
                    q = qe.expand(doc, q, sorted(refs), content=delta)
                    created.append(q["id"])
                else:
                    q.update(draft)
                current[root] = q
        if changed:
            old_ids = _lineage_ids(doc, old_q) if old_q else set()
            for rid in refs:
                r = rules[rid]
                r["blockers"] = [b for b in r.get("blockers", []) if b not in old_ids]
                if (r.get("excluded_until_data") or {}).get("question") in old_ids:
                    r.pop("excluded_until_data", None)
                if q["id"] not in r["blockers"]:
                    r["blockers"].append(q["id"])
            for rec in carry:
                if not outcome(rec["rule"]):
                    retire(doc, q, rules[rec["rule"]], rec["data"])
    withdrawn, judged, retired = [], [], []
    for q in current.values():
        for rid in sorted(qe.targets(q)):
            kind = outcome(rid)
            if not kind or rid in qr.resolved_targets(q):
                continue
            proof = {"outcome": kind, "statement": rules[rid].get("statement", "")}
            if kind == "executor":
                proof["family"] = rules[rid]["template_fit"]["family"]
            elif kind == "present":
                proof["checks"] = [{"id": str(c.get("id") or ""), "rule": rid, "status": c["status"]} for c in checks[rid]]
            if m.code_close_refusal(doc, q):
                continue
            retire(doc, q, rules[rid], proof)
            retired.append({"question": q["id"], "rule": rid, "outcome": kind})
        if qr.complete(q):
            kinds = {r["data"]["outcome"] for r in q["code_resolutions"] if "data" in r}
            if kinds == {"coverage_gap"}:
                withdrawn.append(q["id"])
            if kinds == {"executor"}:
                judged.append(q["id"])
    for key, vals in (("withdrawn_no_data_questions", withdrawn), ("withdrawn_judged_on_solution", judged), ("resolved_missing_data_targets", retired)):
        report.pop(key, None)
        if vals:
            report[key] = vals
    return created
