"""K06: carry the local support/1 evidence and recheck it at the receiver.

The package is never the source of its own expected evidence. The existing
catalogue reader owns support/1 validation; this module only selects the
exact used families/criteria and compares the transported snapshot. Matching a
snapshot is NOT permission to execute: approval, live bindings and the
task/mode-specific execution decision remain distinct checks.

P0 v1.13 §11.2: manifest support begins at spec 1.20. The current producer
revision is not changed here; absent evidence in archived 1.18 is unjudged.
"""
from __future__ import annotations

import copy
import json
import re

from cdpm import catalog_bridge as bridge, model as m
from template_catalog import bindings


SPEC_FLOOR = (1, 20)
MISSING = "support-missing"
MISMATCH = "manifest-mismatch"


class SupportProjectionError(ValueError):
    def __init__(self, words, *, code=MISSING):
        super().__init__(words)
        self.code = code


def required(spec):
    """Unknown revisions cannot select an archival no-check path."""
    match = re.fullmatch(r"(\d+)\.(\d+)", str(spec or ""))
    return not match or tuple(map(int, match.groups())) >= SPEC_FLOOR


def judged(manifest):
    return "support" in manifest or required(manifest.get("spec_version"))


def _criterion_ids(doc, task=None, *, active_only=False):
    """References in model.json, including the producer's derived criterion.

    Never use submitted objective.json to decide what needs evidence.
    Snapshot all referenced criteria; execution narrows direct objective
    references to active objectives. A separately derived pairing criterion
    remains an execution obligation even if its source objective is blocked.
    """
    zones = {"PAIRING", "ROSTERING", "BOTH"} if task is None else {task.upper(), "BOTH"}
    questions = {q.get("id"): q for q in doc.get("questions") or []
                 if isinstance(q, dict)}
    found = set()
    for objective in doc.get("objectives") or []:
        if not isinstance(objective, dict) or objective.get("zone") not in zones:
            continue
        if active_only and m.objective_is_blocked(objective, questions):
            continue
        if objective.get("metric"):
            found.add(str(objective["metric"]))
        block = objective.get("criterion")
        if isinstance(block, dict) and block.get("kind"):
            found.add(str(block["kind"]))
    if task in (None, "pairing"):
        derived = m.derive_pairing_criterion(doc)
        if derived:
            found.add(str(derived["kind"]))
    return found


def project(doc):
    """A detached subset of native support/1, without creating approvals.

    Keep all products/kinds, including unsupported cells. Dropping negative
    cells would turn a faithful snapshot into an optimistic declaration.
    """
    cat = bridge.catalog()
    try:
        source = cat.support()
    except bridge.CATALOG_ERRORS as exc:
        raise SupportProjectionError(f"manifest.support: {exc}") from exc
    verdict = bindings.support_of(cat, source)
    if not verdict["valid"]:
        raise SupportProjectionError(f"manifest.support: {verdict['why']}")
    families = set(m._landed_carriers(doc))
    records = bridge.family_records()
    criteria = _criterion_ids(doc)
    criterion_records = cat.criteria()
    missing = sorted(["families/" + f for f in families - set(records)]
                     + ["criteria/" + c for c in criteria - set(criterion_records)])
    if missing:
        raise SupportProjectionError("manifest.support: нет выпущенной записи "
                                     + ", ".join(missing))
    products = cat.dictionaries().get("products") or []
    expected = {("families", f, str(k), str(p)) for f in families
                for k in bridge.executor_kinds(f)
                for p in products}
    expected.update(("criteria", c, str(k), str(p)) for c in criteria
                    for k in criterion_records[c].get("computable_by") or []
                    for p in products)
    cells = [cell for cell in source["cells"]
             if (cell["section"] == "families" and cell["record_id"] in families)
             or (cell["section"] == "criteria" and cell["record_id"] in criteria)]
    identities = [(cell["section"], cell["record_id"], cell["kind"], cell["product"])
                  for cell in cells]
    if len(identities) != len(set(identities)) or set(identities) != expected:
        raise SupportProjectionError(
            "manifest.support: неполный, повторный или чужой состав ячеек "
            + ", ".join(sorted(["families/" + f for f in families]
                               + ["criteria/" + c for c in criteria])))
    result = copy.deepcopy(source)
    result["cells"] = copy.deepcopy(sorted(cells, key=lambda c: (
        c["section"], c["record_id"], c["kind"], c["product"])))
    return result


def _canonical(value):
    # Unlike Python equality, distinguish bool from int in evidence fields.
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def check(doc, manifest):
    if not judged(manifest):
        return []

    def problem(code, words):
        return [{"code": code, "words": "manifest.support: " + words}]

    if "support" not in manifest:
        return problem(MISSING, "нет свидетельства для ревизии "
                       + str(manifest.get("spec_version")))
    declared = manifest["support"]
    if not isinstance(declared, dict):
        return problem(MISMATCH, "свидетельство не объект")
    try:
        expected = project(doc)
    except SupportProjectionError as exc:
        return [{"code": exc.code, "words": str(exc)}]
    except bridge.CATALOG_ERRORS as exc:
        return problem("catalog-unavailable", str(exc))
    if manifest.get("catalog") != expected["catalog"]:
        return problem(MISMATCH, "каталог пакета не совпадает с местным свидетельством")
    try:
        same = _canonical(declared) == _canonical(expected)
    except (TypeError, ValueError):
        same = False
    if not same:
        return problem(MISMATCH, "снимок не совпадает с пересчётом по model.json "
                       "и местному catalog/support.json")
    return []


def _execution_requirements(doc, task, mode):
    needed = ({"builder_method"} if task == "pairing" else
              {"kotlin_knob", "kotlin_constraint", "kotlin_logic_form"})
    requirements = []
    for family, rules in sorted(m._landed_carriers(doc).items()):
        applicable = [r for r in rules if r.get("zone") in (task.upper(), "BOTH")
                      and (mode != "feasibility-hard" or r.get("kind") != "SOFT")]
        if applicable:
            requirements.extend(("families", family, kind) for kind in
                                sorted(set(bridge.executor_kinds(family)) & needed))
    if mode == "optimization":
        criterion_kind = "builder_method" if task == "pairing" else "kotlin_metric"
        # Include the needed kind even when the record does not declare it:
        # an empty intersection is not permission to execute that criterion.
        requirements.extend(("criteria", c, criterion_kind)
                            for c in sorted(_criterion_ids(doc, task, active_only=True)))
    return requirements


def readiness_problems(doc):
    """CDPM support/1 law: each needed kind needs an approved product cell.

    Unlike the COSMOS execution gate this is any-product coverage, not a
    claim about the current COSMOS binary. Keep explicit task/mode scope so
    an unsupported optimization criterion cannot remove basic feasibility.
    """
    families = m._landed_carriers(doc)
    criteria = {task: _criterion_ids(doc, task, active_only=True)
                for task in ("pairing", "rostering")}
    if not families and not any(criteria.values()):
        return []
    required_cells = []
    for task in ("pairing", "rostering"):
        for family, rules in sorted(families.items()):
            applicable = [r for r in rules if r.get("zone") in (task.upper(), "BOTH")]
            if not applicable:
                continue
            hard = any(r.get("kind") != "SOFT" for r in applicable)
            modes = list(m.TASK_MODES) if hard else ["optimization", "feasibility-soft"]
            # Exclude only executors exclusive to the other task. Shared
            # check/export kinds and unknown kinds still require evidence.
            other_task = ({"builder_method"} if task == "rostering" else
                          {"kotlin_knob", "kotlin_constraint", "kotlin_logic_form"})
            for kind in bridge.executor_kinds(family):
                if kind in other_task:
                    continue
                required_cells.append(("families", family, kind, task, modes,
                                       [str(r.get("id")) for r in applicable]))
        kind = "builder_method" if task == "pairing" else "kotlin_metric"
        required_cells.extend(("criteria", rid, kind, task, ["optimization"], [])
                              for rid in sorted(criteria[task]))
    try:
        evidence = project(doc)
        source_error = None
    except SupportProjectionError as exc:
        evidence, source_error = {"cells": []}, str(exc)
    cells = evidence["cells"]
    problems = []
    for section, rid, kind, task, modes, rules in required_cells:
        own = [c for c in cells if (c["section"], c["record_id"], c["kind"]) ==
               (section, rid, kind)]
        if any(c["state"] == "есть" and c.get("approved") for c in own):
            continue
        record = section + "/" + rid
        words = source_error or "; ".join(
            f"{c['product']}: {c['state']}, "
            + ("выпуск утверждён" if c.get("approved") else "нет утверждения выпуска")
            for c in own) or "нет ячейки нужного вида"
        problems.append({"code": MISSING, "record": record, "item": record,
            "family": rid if section == "families" else None, "kind": kind,
            "rules": rules, "zones": [task.upper()], "modes": modes,
            "hard": "feasibility-hard" in modes, "evidence": copy.deepcopy(own),
            "words": f"support: {record}, вид {kind}, задача {task}, "
                     f"режимы {', '.join(modes)} — поддержка не подтверждена: {words}"})
    return problems


def execution_problems(doc, manifest, *, task, mode, product, product_version):
    """Judge selected family/criterion executors against approved, live evidence.

    This is the support/1 execution boundary, not the transitional 17a
    declaration test and not the data-question router. The shared
    readiness/export verdict remains a separate obligation.
    """
    if not judged(manifest):
        return []
    problems = check(doc, manifest)
    if problems:
        return problems
    if task not in ("pairing", "rostering") or mode not in (
            "optimization", "feasibility-soft", "feasibility-hard"):
        return [{"code": MISSING, "words": "support: задача или режим не названы"}]
    try:
        table = bridge.support_table()
        requirements = _execution_requirements(doc, task, mode)
    except bridge.CATALOG_ERRORS as exc:
        return [{"code": "catalog-unavailable", "words": "support: " + str(exc)}]
    live = table["files"].get(product) or {}
    cells = {(c["section"], c["record_id"], c["kind"], c["product"]): c
             for c in manifest["support"]["cells"]}
    for section, rid, kind in requirements:
        cell = cells.get((section, rid, kind, product)) or {}
        identity = cell.get("bindings_file") or {}
        reasons = []
        if cell.get("state") != "есть":
            reasons.append("состояние вида не «есть»")
        if not cell.get("approved"):
            reasons.append("нет утверждения выпуска")
        if not live.get("ok"):
            reasons.append(live.get("words") or "живые привязки не проверены")
        if not live.get("sha256") or identity.get("sha256") != live["sha256"]:
            reasons.append("отпечаток живых привязок изменился")
        if (not product_version or live.get("product_version") != product_version
                or identity.get("product_version") != product_version):
            reasons.append("версия продукта не совпадает со свидетельством")
        if reasons:
            record = section + "/" + rid
            problems.append({"code": MISSING, "record": record,
                "item": record, "kind": kind, "product": product,
                "words": f"support: исполнимость {kind} записи {record} "
                f"для {product}, задача {task}, режим {mode}, не подтверждена: "
                + "; ".join(reasons)})
    return problems
