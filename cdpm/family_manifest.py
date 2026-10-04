"""K01 package projection and receive-side recomputation (§3.2).

Only model cells/answers and the local catalogue supply expected values.
The manifest never supplies input to the completeness calculation. The
contract here identifies the current calculation, not a rewritten landing
pin; every original family_contract remains untouched in model.json.
"""
from __future__ import annotations

from cdpm import catalog_bridge as bridge, family_params as fp, model as m
from template_catalog import canon
from template_catalog.completeness import family_completeness


MISMATCH = "manifest-mismatch"


def _project(states):
    records = bridge.family_records()
    stamp = bridge.stamp()
    result = {}
    for family, state in states.items():
        record = records.get(family)
        selected = family_completeness(
            record, state["given"], state["declined"], state["pending"])
        result[family] = {
            "contract": {"family": family, "catalog_version": stamp["version"],
                         "record_fingerprint": (canon.record_fingerprint(record)
                                                if record is not None else None),
                         "param_set": selected["set_id"]},
            **{key: state[key] for key in ("verdict", "given", "declined", "pending", "missing")},
        }
    return result


def project(doc):
    """Project exactly the model being released, not the pre-export model."""
    return _project(fp.family_completeness(doc))


def check(doc, manifest):
    """Return named discrepancies and forbidden K01 modes, without I/O writes.

    Absent families is an undeclared legacy field, not a positive check.
    Present-but-empty/null cannot conceal any family of the supplied model.
    An honestly held task does not prevent partial transfer of another task.
    """
    if "families" not in manifest:
        return []
    problems = []

    def mismatch(words, family=None):
        problems.append({"code": MISMATCH, "family": family, "words": words})

    stamp = bridge.stamp()
    catalog = manifest.get("catalog")
    if (not isinstance(catalog, dict)
            or catalog.get("fingerprint") != stamp.get("fingerprint")
            or catalog.get("version") != stamp.get("version")):
        mismatch("manifest.catalog не совпадает с каталогом приёмного пересчёта семей")
        return problems
    states = fp.family_completeness(doc)
    expected = _project(states)
    declared = manifest["families"]
    if not isinstance(declared, dict):
        mismatch("manifest.families не объект")
    else:
        if set(declared) != set(expected):
            mismatch("manifest.families: перечень семей не совпадает с model.json")
        for family, want in expected.items():
            got = declared.get(family)
            if not isinstance(got, dict) or set(got) != set(want):
                mismatch(f"manifest.families.{family}: неверный состав полей", family)
                continue
            for field, value in want.items():
                if got[field] != value:
                    mismatch(f"manifest.families.{family}.{field} не совпадает "
                             "с пересчётом по model.json и каталогу", family)
    allowed = manifest.get("allowed_modes")
    if not isinstance(allowed, dict):
        mismatch("manifest.allowed_modes не объект — разрешения задач не доказаны")
        return problems
    for state in states.values():
        for problem in state["problems"]:
            hit = sorted({task for task, mode in m.barrier_affects(problem)
                          if mode in (allowed.get(task) or [])})
            if hit:
                problems.append(dict(problem, words=(problem["words"]
                    + f" — манифест разрешает затронутые задачи {', '.join(hit)}")))
    return problems
