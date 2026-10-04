"""P0 §2.5: derive legacy identity only when proven; never repair a value.

Ambiguous source or a value rejected by today's grammar opens an explicit
revision with the existing cell-CAS barrier. Stored bytes are not rewritten.
Malformed modern identities and orphan markers remain ordinary diagnostics.
"""
from __future__ import annotations

import copy


def _root(q):
    return q.get("revision_of") or q.get("id")


def _answer(q):
    answer = q.get("answer")
    return answer if isinstance(answer, dict) else {}


def _open_confirmation(doc, root, chain, cells, reasons):
    from cdpm import model as m, question_editions, revision_cells

    # A malformed/missing root is not authority to invent a source question.
    try:
        question_editions.require_valid(root)
    except question_editions.EditionRefusal:
        return
    root_id = str(root["id"])
    ids = {q.get("id") for q in doc["questions"] if isinstance(q, dict)}
    qid, n = root_id + "-R", 1
    while qid in ids:
        n += 1
        qid = f"{root_id}-R-{n}"
    targets = sorted(set(root.get("rule_refs", [])) | {rid for rid, _, _, _ in cells})
    question = {
        "id": qid, "part": root.get("part"),
        "title": f"Подтвердите значение: {root.get('title', root_id)}",
        "text": ("При миграции старых ответов происхождение или значение "
                 "не подтверждено. Подтвердите значение новым ответом. "
                 "Прежние значения и тексты сохранены без исправления; "
                 "пока пересмотр открыт, они не являются новым решением. "
                 + "; ".join(sorted(set(reasons)))),
        "answer_format": copy.deepcopy(root.get("answer_format")),
        "rule_refs": targets,
        "source_refs": copy.deepcopy(root.get("source_refs", [])),
        "revision_of": root_id,
    }
    # Validate before changing any answer status, blocker or cell.
    question_editions.freeze(question)
    revision_cells.capture(doc, question)
    doc["questions"].append(question)
    for old in chain:
        answer = _answer(old)
        if answer.get("status") in ("применён", m.PARTIAL_ANSWER_STATUS):
            answer.update(status="пересмотрен", revised_by=qid)
    for _, _, _, cell in cells:
        if "pending_revision" not in cell:
            cell["pending_revision"] = qid
    for rule in doc.get("rules", []):
        if isinstance(rule, dict) and rule.get("id") in targets:
            blockers = rule.setdefault("blockers", [])
            if qid not in blockers:
                blockers.append(qid)


def migrate_document(doc) -> None:
    from cdpm import catalog_bridge, model as m, provenance as prov

    if not isinstance(doc, dict) or not isinstance(doc.get("questions"), list):
        return
    questions = [q for q in doc["questions"] if isinstance(q, dict)]
    by_id = {}
    duplicates = set()
    for q in questions:
        qid = q.get("id")
        if not isinstance(qid, str):
            continue
        if qid in by_id:
            duplicates.add(qid)
        by_id[qid] = q
    cells = list(prov.answer_cells(doc))
    groups, upgrades = {}, []
    for rid, family, name, cell in cells:
        if "pending_revision" in cell:
            continue  # never refresh an already captured revision baseline
        root_id = m.cell_answer_root(cell)
        root = by_id.get(root_id) if isinstance(root_id, str) else None
        if root is None or root_id in duplicates:
            continue
        chain = [q for q in questions if _root(q) == root_id]
        if any(q.get("id") in duplicates for q in chain):
            continue
        proposed = None
        try:
            if "source" in cell:
                # Never rebind an invalid modern identity to a convenient raw.
                q, raw = prov.resolve_source(doc, cell["source"])
                if prov._source_shape_problems(q, name, cell, cell["source"]):
                    continue
                problems = prov.cell_problems(doc, rid, family, name, cell)
            else:
                applied = [q for q in chain if _answer(q).get("status") in
                           ("применён", m.PARTIAL_ANSWER_STATUS)]
                if (len(applied) != 1 or
                        _answer(root).get("status") == "пересмотрен"):
                    problems = ["нет единственного однозначного применённого ответа корня"]
                else:
                    q = applied[0]
                    raw, quote = _answer(q).get("raw"), cell.get("quote")
                    if (not isinstance(raw, str) or not isinstance(quote, str)
                            or not quote.strip() or quote not in raw):
                        problems = ["цитата не является дословной подстрокой старого ответа"]
                    else:
                        proposed = prov.answer_source(q, name, raw)
                        candidate = {**cell, "source": proposed}
                        problems = prov.cell_problems(doc, rid, family, name, candidate)
        except (prov.ProvenanceRefusal, *catalog_bridge.CATALOG_ERRORS):
            continue  # no catalogue/identity authority: retain the diagnostic
        if problems:
            groups.setdefault(root_id, []).extend(
                f"{rid}.{name}, записано {cell.get('value')!r}: {p}" for p in problems)
        elif proposed is not None:
            upgrades.append((root_id, cell, proposed))
    for root_id, cell, source in upgrades:
        if root_id not in groups:
            cell["source"] = source
    for root_id in sorted(groups):
        related = [entry for entry in cells if m.cell_answer_root(entry[3]) == root_id]
        chain = [q for q in questions if _root(q) == root_id]
        _open_confirmation(doc, by_id[root_id], chain, related, groups[root_id])
