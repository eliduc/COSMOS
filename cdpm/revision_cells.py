"""Cell-level revision preconditions, captured only by human reconsideration.

No write, deletion, retry or synchronization may silently refresh this baseline.
The answer-version fingerprint is checked explicitly; a semantic digest also
catches edits that failed to update provenance. Neighbouring cells are excluded.
"""
import copy
import hashlib
import json

from cdpm import model as m


def _snapshot(rule: dict, param: str) -> dict:
    fit = rule.get("template_fit")
    fit = fit if isinstance(fit, dict) else {}
    cells = fit.get("params")
    cells = cells if isinstance(cells, dict) else {}
    cell = cells.get(param)
    # This marker is maintained by the revision state machine, not an edit to
    # the effective value. Everything else, including provenance, is compared.
    semantic = ({k: v for k, v in cell.items() if k != "pending_revision"}
                if isinstance(cell, dict) else cell)
    source = cell.get("source") if isinstance(cell, dict) else None
    version = source.get("version") if isinstance(source, dict) else None
    content = {"present": param in cells, "cell": semantic,
               "landing": {k: v for k, v in fit.items() if k != "params"}}
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    return {"cell_fingerprint": digest.hexdigest(),
            "answer_root": m.cell_answer_root(cell),
            "source_version_fingerprint": (
                version.get("fingerprint") if isinstance(version, dict) else None)}


def capture(doc: dict, question: dict) -> None:
    """Fix the observed target state when a human opens a parameter revision."""
    from cdpm import family_params as fp
    target = fp.question_target(question)
    if not m.is_revision_question(question) or target is None:
        return
    family, param = target
    root = fp._root_id(question)
    targets = {}
    for rule in doc.get("rules", []):
        if not isinstance(rule, dict):
            continue
        fit = rule.get("template_fit")
        fit = fit if isinstance(fit, dict) else {}
        cells = fit.get("params")
        cell = cells.get(param) if isinstance(cells, dict) else None
        if (fit.get("family") == family or rule.get("id") in question.get("rule_refs", [])
                or m.cell_answer_root(cell) == root):
            targets[str(rule["id"])] = _snapshot(rule, param)
    question["revision_cells"] = {"schema_version": "cell-cas/1",
        "family": family, "param": param, "targets": targets}


def conflicts(doc: dict, question: dict, family: str, param: str,
              touched: list[dict]) -> list[str]:
    """Dry-run ALL cells to be overwritten/deleted before the first mutation."""
    if not m.is_revision_question(question):
        return []
    baseline = question.get("revision_cells")
    if (not isinstance(baseline, dict) or baseline.get("schema_version") != "cell-cas/1"
            or baseline.get("family") != family or baseline.get("param") != param
            or not isinstance(baseline.get("targets"), dict)):
        return ["нет надёжного снимка ячеек на момент открытия пересмотра"]
    # Ownership itself may have changed. Such a cell disappears from today's
    # deletion list, but that must not turn a stale decline into a decision.
    checking = {str(rule.get("id")): rule for rule in touched}
    root = question.get("revision_of")
    for rule in doc.get("rules", []):
        if not isinstance(rule, dict):
            continue
        expected = baseline["targets"].get(str(rule.get("id")))
        if isinstance(expected, dict) and expected.get("answer_root") == root:
            checking[str(rule.get("id"))] = rule
    problems = []
    for rule in checking.values():
        rid = str(rule.get("id"))
        expected = baseline["targets"].get(rid)
        current = _snapshot(rule, param)
        if expected != current:
            problems.append(f"{rid}.template_fit.params.{param}: "
                            "ячейка, её посадка или версия источника изменена после открытия пересмотра")
    return sorted(set(problems))


def refuse(question: dict, raw: str, revision: int, problems: list[str]) -> dict:
    question["answer"] = {"raw": raw, "revision": revision,
        "status": m.REVISION_CONFLICT_STATUS, "conflicts": problems,
        "note": ("Пересмотр: конфликт — " + "; ".join(problems) +
                 ". Поздние изменения сохранены; ответ не применён. "
                 "Сверьте текущие значения и явно откройте новый пересмотр "
                 "через API reconsider, затем ответьте на новый вопрос. "
                 "Повтор прежнего ответа снимок не обновляет.")}
    return {"applied": False, "touched": []}


def worker_conflicts(doc: dict, question: dict, touched: list[dict]) -> list[str]:
    """Check a worker continuation against the original CAS plus own effects.

    A partial answer may already have removed its old cells on remaining
    rows. Reconstruct only those recorded transitions; never accept today's
    state as a fresh baseline. Neighbouring parameters are not part of CAS.
    """
    from cdpm import family_params as fp
    target = fp.question_target(question)
    if not m.is_revision_question(question) or target is None:
        return []
    family, param = target
    baseline = question.get("revision_cells")
    if (not isinstance(baseline, dict) or baseline.get("schema_version") != "cell-cas/1"
            or baseline.get("family") != family or baseline.get("param") != param
            or not isinstance(baseline.get("targets"), dict)):
        return ["нет надёжного снимка ячеек на момент открытия пересмотра"]
    effects = (question.get("answer") or {}).get("effects") or []
    problems = []
    for rule in touched:
        rid = str(rule.get("id"))
        expected = baseline["targets"].get(rid)
        valid_chain = isinstance(expected, dict)
        for effect in effects:
            if not isinstance(effect, dict) or str(effect.get("rule")) != rid:
                continue
            field = effect.get("field")
            if field not in ("template_fit", "*"):
                continue
            before = effect.get("before")
            after = effect.get("after")
            if field == "template_fit":
                before, after = {"template_fit": before}, {"template_fit": after}
            before = before if isinstance(before, dict) else {}
            after = after if isinstance(after, dict) else {}
            if expected != _snapshot(before, param):
                valid_chain = False
                break
            expected = _snapshot(after, param)
        if not valid_chain or expected != _snapshot(rule, param):
            problems.append(f"{rid}.template_fit.params.{param}: "
                            "ячейка, её посадка или версия источника изменена "
                            "вне записанных следствий этого пересмотра")
    return sorted(set(problems))


def preserve_worker_neighbors(question: dict, patch: dict,
                              sent: dict, current: dict) -> list[str]:
    """Three-way merge of neighbouring cells, never a stale whole landing.

    Unchanged copies from the worker retain concurrent edits/deletions. If
    both sides changed a neighbour differently, reject instead of choosing.
    The target cell/landing itself is checked by worker_conflicts.
    """
    from cdpm import family_params as fp
    target = fp.question_target(question)
    if not m.is_revision_question(question) or target is None:
        return []
    _, param = target
    missing = object()
    problems = []
    for row in patch["rules"]:
        fit = row.get("template_fit")
        if "unchanged" in row or not isinstance(fit, dict):
            continue
        cells = fit.get("params")
        if not isinstance(cells, dict):
            continue
        rid = row["id"]
        old = (sent.get(rid, {}).get("template_fit") or {}).get("params") or {}
        now = (current[rid].get("template_fit") or {}).get("params") or {}
        for name in set(old) | set(now) | set(cells):
            if name == param:
                continue
            previous = old.get(name, missing)
            latest = now.get(name, missing)
            proposed = cells.get(name, missing)
            if latest == previous or proposed == latest:
                continue
            if proposed != previous:
                problems.append(f"{rid}.template_fit.params.{name}: "
                                "соседняя ячейка изменена и обработчиком, и после отправки ему строк")
            elif latest is missing:
                cells.pop(name, None)
            else:
                cells[name] = copy.deepcopy(latest)
    return sorted(set(problems))
