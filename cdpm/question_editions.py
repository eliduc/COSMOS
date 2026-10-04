"""Immutable question scope (P0 §2.1, A04).

An edition is an explicit snapshot, not a cache of today's rule_refs.
Legacy questions keep their v1 identity until the publication/migration
writer explicitly freezes them. Readers never silently repair a snapshot.
"""
from __future__ import annotations

import copy
import hashlib
import json


class EditionRefusal(ValueError):
    """The published question and its immutable snapshot disagree."""


def _norm(value) -> str:
    from cdpm.model import normalized_question_text
    return normalized_question_text(str(value or ""))


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _snapshot(q: dict, revision: int) -> dict:
    fmt = q.get("answer_format") or {}
    snap = {
        "id": q["id"], "revision": revision,
        "title": _norm(q.get("title")), "text": _norm(q.get("text")),
        "type": fmt.get("type"),
        "options": [{"key": o["key"], "text": _norm(o["text"])}
                    for o in (fmt.get("options") or [])],
        "note": str(fmt.get("note") or ""),
        "targets": sorted(set(q.get("rule_refs") or [])),
        "sources": sorted(copy.deepcopy(q.get("source_refs") or []),
                          key=_json),
    }
    if "history" in q:
        snap["history"] = copy.deepcopy(q["history"])
    if "data_scope" in q:
        snap["data_scope"] = copy.deepcopy(q["data_scope"])
    if "missing_data" in q:
        snap["missing_data"] = copy.deepcopy(q["missing_data"])
    if "parameter_contract" in q:
        snap["parameter_contract"] = copy.deepcopy(q["parameter_contract"])
    return snap


def _digest(ed: dict, *, legacy: bool = False) -> str:
    parts = [ed["id"]]
    if not legacy:
        parts.append(str(ed["revision"]))
    parts += [ed["title"], ed["text"], ed["type"]]
    parts += [f"{o['key']}={o['text']}" for o in ed["options"]]
    if not legacy:
        # Encode collection boundaries explicitly: a source pair is not two
        # unrelated tokens, and one target cannot impersonate two targets.
        parts += [ed["note"], _json(ed["targets"]), _json(ed["sources"])]
        if "history" in ed:
            parts.append(_json(ed["history"]))
        if "data_scope" in ed:
            parts.append(_json({"data_scope": ed["data_scope"]}))
        if "missing_data" in ed:
            parts.append(_json({"missing_data": ed["missing_data"]}))
        if "parameter_contract" in ed:
            parts.append(_json({"parameter_contract": ed["parameter_contract"]}))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def problems(q: dict) -> list[str]:
    """Fail-closed semantic validation; accepts arbitrary JSON field shapes."""
    from cdpm.data_scope import evidence_problems
    from cdpm.missing_data import evidence_problems as missing_evidence_problems
    from cdpm import parameter_contract
    evidence_issues = (evidence_problems(q) + missing_evidence_problems(q)
                       + parameter_contract.problems(q))
    if evidence_issues:
        return [f"{q.get('id', '?')}: {issue}" for issue in evidence_issues]
    if "edition" not in q:
        if "parameter_contract" in q:
            return [f"{q.get('id', '?')}: контракт параметра не зафиксирован изданием"]
        return []
    prefix = f"{q.get('id', '?')}: издание вопроса"
    ed = q["edition"]
    if not isinstance(ed, dict):
        return [f"{prefix} — edition не объект"]
    rev = ed.get("revision")
    if type(rev) is not int or rev < 1:
        return [f"{prefix} — revision не целое ≥ 1"]
    try:
        snap = _snapshot(q, rev)
        expected = _digest(ed)
    except (KeyError, TypeError, ValueError, AttributeError):
        return [f"{prefix} — неполный или нечитаемый снимок"]
    out = []
    for field in ("id", "title", "text", "type", "options", "note", "targets"):
        if ed.get(field) != snap[field]:
            out.append(f"{prefix} — {field} изменено после издания")
    if ed.get("history") != snap.get("history"):
        out.append(f"{prefix} — смысл блока history изменён после издания")
    if ed.get("data_scope") != snap.get("data_scope"):
        out.append(f"{prefix} — точный перечень data_scope изменён после издания")
    if ed.get("missing_data") != snap.get("missing_data"):
        out.append(f"{prefix} — свидетельство missing_data изменено после издания")
    if ed.get("parameter_contract") != snap.get("parameter_contract"):
        out.append(f"{prefix} — контракт параметра изменён после издания")
    sources = ed.get("sources")
    if not isinstance(sources, list) or sources != snap["sources"]:
        out.append(f"{prefix} — изменены существенные источники издания")
    if ed.get("fingerprint") != expected:
        out.append(f"{prefix} — отпечаток не совпадает со снимком")
    if "fingerprint_v1" in ed and (
            rev != 1 or ed["fingerprint_v1"] != _digest(ed, legacy=True)):
        out.append(f"{prefix} — неверный отпечаток совместимости v1")
    return out


def require_valid(q: dict) -> None:
    found = problems(q)
    if found:
        raise EditionRefusal("; ".join(found))


def freeze(q: dict, *, legacy: bool = False, revision: int = 1) -> dict:
    """Explicit writer: freeze once; repeated publication cannot refresh it.

    ``legacy=True`` preserves the already printed v1 book identity, while
    storing the full immutable scope. A new edition has no v1 escape hatch.
    This function does not write files or change the answer.
    """
    if "edition" in q:
        require_valid(q)
        return q["edition"]
    # This typed contract did not exist in v1 books. Do not give a newly
    # generated question a case-folded legacy fingerprint escape hatch.
    legacy = (legacy and "data_scope" not in q and "missing_data" not in q
              and "parameter_contract" not in q)
    if type(revision) is not int or revision < 1 or (legacy and revision != 1):
        raise EditionRefusal("издание вопроса: недопустимая revision")
    try:
        ed = _snapshot(q, revision)
        ed["fingerprint"] = _digest(ed)
        if legacy:
            ed["fingerprint_v1"] = _digest(ed, legacy=True)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise EditionRefusal("издание вопроса: нечитаемый вопрос") from exc
    trial = dict(q, edition=ed)
    require_valid(trial)
    q["edition"] = ed
    return ed


def migrate_legacy_document(doc) -> None:
    """Freeze persisted legacy questions before any writer can enlarge them.

    Loading does not prove a model was never published: a book can have any
    name and can live elsewhere. Preserve the v1 identity conservatively.
    Existing snapshots, including invalid ones, are never repaired here.
    Malformed input stays available to the ordinary schema diagnostics.
    """
    if not isinstance(doc, dict) or not isinstance(doc.get("questions"), list):
        return
    for q in doc["questions"]:
        if not isinstance(q, dict) or "edition" in q:
            continue
        try:
            freeze(q, legacy=True)
        except EditionRefusal:
            # freeze only assigns after complete validation. Do not invent
            # missing content merely to make an invalid record migratable.
            continue


def fingerprint(q: dict) -> str:
    require_valid(q)
    ed = q["edition"]
    return ed.get("fingerprint_v1", ed["fingerprint"])


def latest(doc: dict, q: dict) -> dict:
    """Follow explicit lineage; neither list order nor an ID suffix is authority."""
    seen = set()
    while True:
        require_valid(q)
        if q.get("id") in seen:
            raise EditionRefusal("родословная изданий содержит цикл")
        seen.add(q.get("id"))
        children = [x for x in doc.get("questions", []) if isinstance(x, dict)
                    and x.get("edition_of") == q.get("id")]
        if not children:
            if len(seen) > 1:
                lineage_root(doc, q)
            return q
        if len(children) != 1:
            raise EditionRefusal("родословная изданий разветвлена")
        q = children[0]


def current_for_root(doc: dict, root_id: str) -> dict | None:
    """Resolve an exact root and its validated lineage, including legacy roots."""
    qs = [q for q in doc.get("questions", []) if isinstance(q, dict)]
    roots = [q for q in qs if q.get("id") == root_id]
    if not roots:
        return None
    if len(roots) != 1 or roots[0].get("edition_of"):
        raise EditionRefusal("корень вопроса отсутствует или неоднозначен")
    current = latest(doc, roots[0])
    if sum(q.get("id") == current.get("id") for q in qs) != 1:
        raise EditionRefusal("последнее издание вопроса неоднозначно")
    if "edition" in current:
        lineage_root(doc, current)
    return current


def targets(q: dict) -> set[str]:
    """Scope authority for readers; draft/legacy compatibility is explicit."""
    require_valid(q)
    if "edition" in q:
        return set(q["edition"]["targets"])
    return set(q.get("rule_refs") or [])


def is_superseded(doc: dict, q: dict) -> bool:
    """Only a valid, unambiguous edition chain can retire historical work.

    Invalid successors remain visible to diagnostics; an E-looking ID or a
    dangling draft is not proof that an answer has a legitimate successor.
    """
    if "edition" not in q:
        return False
    qs = [x for x in doc.get("questions", []) if isinstance(x, dict)]
    if not any(x.get("edition_of") == q.get("id") for x in qs):
        return False
    ids = [x.get("id") for x in qs]
    if any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
        return False
    try:
        return latest(doc, q) is not q
    except EditionRefusal:
        return False


def prior_answer_notes(doc: dict, q: dict) -> list[str]:
    """Shared human trace for MD/XLSX; history is not an answer to this edition."""
    by_id = {x.get("id"): x for x in doc.get("questions", [])}
    parent = by_id.get(q.get("edition_of"))
    seen, out = set(), []
    while parent and parent.get("id") not in seen:
        seen.add(parent["id"])
        ans = parent.get("answer") or {}
        if ans.get("status") in ("применён", "применён частично"):
            out.append(f"ответ «{ans.get('raw', '')}» применён к "
                       f"изданию {parent.get('edition', {}).get('revision', 1)}")
        elif ans.get("status") == "ожидает обработчика":
            out.append(f"ответ «{ans.get('raw', '')}» получен к "
                       f"изданию {parent.get('edition', {}).get('revision', 1)}, "
                       "но не применён; для нового издания требуется новый ответ")
        parent = by_id.get(parent.get("edition_of"))
    return out


def normative_rule_sources(rules, existing=()) -> list[dict]:
    """Retain named clauses without reissuing for another excerpt of one clause.

    Keep published references byte-for-byte: a code-built witness at the same
    source address does not replace its frozen quote. Explicit normative
    question patches remain a separate writer and may change that context.
    """
    refs = copy.deepcopy(list(existing))
    addresses = {(r.get("sid"), r.get("anchor")) for r in refs}
    for rule in rules:
        for source in rule.get("sources") or []:
            if not isinstance(source, dict) or not source.get("sid") or not source.get("anchor"):
                continue
            address = (source["sid"], source["anchor"])
            if address in addresses:
                continue
            ref = {key: copy.deepcopy(source[key]) for key in ("sid", "anchor", "quote")
                   if key in source}
            refs.append(ref)
            addresses.add(address)
    return refs


def scope_change_notes(doc: dict, q: dict) -> list[str]:
    """Explain added targets even when they cite already-published sources."""
    parent = next((x for x in doc.get("questions", [])
                   if x.get("id") == q.get("edition_of")), None)
    if parent is None:
        return []
    current = targets(q)
    added = sorted(current - targets(parent))
    if not added:
        return []
    scope = (f"ко всем {len(current)} строкам" if len(current) > 1
             else "к этой строке")
    return ["Добавлены строки в область вопроса: " + ", ".join(added)
            + f". Ответ на это издание относится {scope} свода."]


def source_change_notes(doc: dict, q: dict, files: dict[str, str]) -> list[str]:
    """Name this edition's additions, not the accumulated source list."""
    parent = next((x for x in doc.get("questions", [])
                   if x.get("id") == q.get("edition_of")), None)
    if parent is None:
        return []
    previous = parent.get("source_refs") or []
    added = [r for r in q.get("source_refs") or [] if r not in previous]
    if not added:
        return []
    labels = []
    for ref in added:
        label = f"{files.get(ref['sid'], ref['sid'])}, {ref['anchor']}"
        if ref.get("quote"):
            label += f" — «{ref['quote']}»"
        if label not in labels:
            labels.append(label)
    return ["Добавлены нормативные источники: " + "; ".join(labels)]


def application_problems(doc: dict, q: dict) -> list[str]:
    """A blocker or a family-wide writer cannot enlarge a published scope."""
    from cdpm import question_resolutions
    found = problems(q) + question_resolutions.problems(doc, q)
    if question_resolutions.complete(q):
        found.append(f"{q.get('id')}: вопрос снят кодом — ответ больше не применяется")
    if found or "edition" not in q:
        return found
    if any(x.get("edition_of") == q.get("id") for x in doc.get("questions", [])):
        return [f"{q.get('id')}: вопрос уже переиздан — ответьте на последнее издание"]
    scope = targets(q)
    from cdpm.answers import _answer_targets
    from cdpm.family_params import question_target
    ids = _answer_targets(q, doc)
    param = question_target(q)
    outside = []
    for rule in doc.get("rules") or []:
        cond = rule.get("conditional") or {}
        fit = rule.get("template_fit") or {}
        touched = (ids.intersection(rule.get("blockers") or [])
                   or (isinstance(cond, dict) and cond.get("question") in ids)
                   or (param is not None and isinstance(fit, dict)
                       and fit.get("family") == param[0]))
        if touched and rule.get("id") not in scope:
            outside.append(str(rule.get("id")))
    if outside:
        return [f"{q.get('id')}: строки вне области издания: "
                + ", ".join(sorted(set(outside))) + " — требуется новое издание"]
    return []


def lineage_root(doc: dict, q: dict) -> str:
    """Validate the explicit edition chain before allowing repeated wording."""
    qs = doc.get("questions") or []
    by_id = {x.get("id"): x for x in qs}
    seen = set()
    cur = q
    while True:
        require_valid(cur)
        ed = cur.get("edition")
        if not isinstance(ed, dict) or cur.get("id") in seen:
            raise EditionRefusal("нарушена родословная издания вопроса")
        seen.add(cur["id"])
        if not cur.get("edition_of"):
            if ed["revision"] != 1:
                raise EditionRefusal("первое издание должно иметь revision=1")
            return cur["id"]
        parent = by_id.get(cur["edition_of"])
        if parent is None or not isinstance(parent.get("edition"), dict):
            raise EditionRefusal("предыдущее издание вопроса отсутствует")
        require_valid(parent)
        if ed["revision"] != parent["edition"]["revision"] + 1:
            raise EditionRefusal("нарушена последовательность изданий")
        if not targets(parent) <= targets(cur):
            raise EditionRefusal("новое издание теряет прежнюю область")
        if targets(parent) == targets(cur) and all(
                ed.get(field) == parent["edition"].get(field)
                for field in ("title", "text", "type", "options", "note", "history", "sources", "data_scope", "missing_data", "parameter_contract")):
            raise EditionRefusal("неизменённый вопрос не требует нового издания")
        if sum(x.get("edition_of") == parent["id"] for x in qs) != 1:
            raise EditionRefusal("родословная изданий разветвлена")
        cur = parent


def expand(doc: dict, q: dict, rule_refs: list[str], *, content: dict | None = None) -> dict:
    """Append a new edition without editing the old question or its answer.

    Only the latest edition can expand, so concurrent/stale patches do not
    fork the edition chain. Reconsideration has its own cell-CAS writer;
    its integration is deliberately not inferred here.
    """
    require_valid(q)
    if "edition" not in q:
        raise EditionRefusal("сначала требуется явное издание вопроса")
    qs = doc.get("questions") or []
    if sum(x.get("id") == q["id"] for x in qs) != 1:
        raise EditionRefusal("издание вопроса отсутствует или неоднозначно")
    if any(x.get("edition_of") == q["id"] for x in qs):
        raise EditionRefusal("вопрос уже переиздан — адресуйте последнее издание")
    if q.get("revision_of") or q.get("revision_cells"):
        raise EditionRefusal("добор области открытого пересмотра требует "
                             "согласования его снимка ячеек")
    enlarged = targets(q) | set(rule_refs)
    known = {r.get("id") for r in (doc.get("rules") or [])}
    if not enlarged <= known:
        raise EditionRefusal("издание вопроса адресует неизвестные строки")
    if content is not None and set(content) - {"title", "text", "answer_format", "history", "source_refs", "data_scope", "missing_data", "parameter_contract"}:
        raise EditionRefusal("переиздание содержит недопустимые поля вопроса")
    if enlarged == targets(q) and not content:
        return q
    revision = q["edition"]["revision"] + 1
    # Follow explicit lineage, never strip E/R-looking text from an ID.
    root = q
    seen = set()
    by_id = {x["id"]: x for x in qs}
    while root.get("edition_of"):
        if root["id"] in seen or root["edition_of"] not in by_id:
            raise EditionRefusal("нарушена родословная издания вопроса")
        seen.add(root["id"])
        root = by_id[root["edition_of"]]
    new_id = f"{root['id']}-E{revision}"
    if new_id in by_id:
        raise EditionRefusal(f"идентификатор издания {new_id} уже занят")
    new = copy.deepcopy(q)
    new.pop("answer", None)
    new.pop("code_resolutions", None)
    new.pop("edition", None)
    if content:
        new.update(copy.deepcopy(content))
    new.update(id=new_id, edition_of=q["id"], rule_refs=sorted(enlarged))
    freeze(new, revision=revision)
    lineage_root({"questions": [*qs, new]}, new)
    qs.append(new)
    return new
