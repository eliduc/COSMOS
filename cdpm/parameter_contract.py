"""Exact catalogue context of a parameter question (K01).

Optional for legacy documents. A missing context is never backfilled by a
reader. Its identity belongs to the immutable question edition, not to a
clock comparison between an edition number and an opaque catalogue hash.
"""
from __future__ import annotations

import re


def snapshot(family: str, parameter: str) -> dict:
    from cdpm import catalog_bridge
    return {"family": family, "parameter": parameter,
            "registry_rev": str(catalog_bridge.stamp()["fingerprint"])}


def problems(q: dict) -> list[str]:
    if "parameter_contract" not in q:
        return []
    pin = q["parameter_contract"]
    if (not isinstance(pin, dict)
            or set(pin) != {"family", "parameter", "registry_rev"}
            or not all(isinstance(x, str) and x for x in pin.values())
            or not re.fullmatch(r"[a-f0-9]{64}", pin["registry_rev"])):
        return ["контракт параметра вопроса имеет неверную форму"]
    from cdpm import family_params
    # Historical edition integrity must not change when today's catalogue
    # removes a family/name. Current applicability is a separate check.
    target = family_params.question_target(q, families={pin["family"]: {pin["parameter"]}})
    if target != (pin["family"], pin["parameter"]):
        return ["контракт параметра не совпадает с адресатом вопроса"]
    return []


def application_problem(q: dict, family: str, parameter: str) -> str | None:
    """Do not interpret an issued old question using a changed meaning."""
    from cdpm import catalog_bridge, family_params, question_editions
    issues = question_editions.problems(q)
    if issues:
        return "; ".join(issues)
    pin = q.get("parameter_contract")
    if pin is None:  # Legacy has no newly invented proof of freshness.
        return None
    if (pin["family"], pin["parameter"]) != (family, parameter):
        return "контракт параметра не совпадает с адресатом ответа"
    if pin["registry_rev"] == str(catalog_bridge.stamp()["fingerprint"]):
        return None
    new = catalog_bridge.family_records().get(family)
    if new is None:
        return "совместимость контракта вопроса с каталогом не доказана"
    transition = family_params._semantic_transition(family, pin["registry_rev"], {parameter})
    differences = [d["words"] for d in transition["differences"]]
    if transition["verdict"] == family_params.CONTRACT_INCOMPATIBLE:
        return ("контракт выданного вопроса изменился; требуется новое издание: "
                + "; ".join(differences))
    if transition["verdict"] == family_params.CONTRACT_UNPROVEN:
        return ("совместимость контракта вопроса с каталогом не доказана; "
                "требуется новое издание: " + "; ".join(differences))
    return None


def cell_revision(doc: dict, rule: dict, family: str, name: str, cell) -> str:
    """An exact answer source can prove another origin; a tag alone cannot."""
    from cdpm import provenance
    fallback = str((rule.get("template_fit") or {}).get("registry_rev") or "").strip()
    source = provenance.cell_source(cell)
    if source is None or source.get("kind") != provenance.KIND_ANSWER:
        return fallback
    try:
        q, _raw = provenance.resolve_source(doc, source)
    except provenance.ProvenanceRefusal:
        return fallback
    pin = q.get("parameter_contract")
    if (pin is None or "edition" not in q or problems(q)
            or (pin["family"], pin["parameter"]) != (family, name)
            or provenance._source_shape_problems(q, name, cell, source)
            or provenance._target_problems(doc, q, rule.get("id"), family, name)):
        return fallback
    return pin["registry_rev"]


def reissue_needed(q: dict, family: str, parameter: str, carriers: list[dict]) -> bool:
    """Issue a new current question; never add evidence to an old edition."""
    from cdpm import catalog_bridge, question_editions
    if question_editions.problems(q):
        return False  # Invalid snapshots are diagnosed, not silently repaired.
    if "parameter_contract" in q:
        return application_problem(q, family, parameter) is not None
    current = str(catalog_bridge.stamp()["fingerprint"])
    return any(str((r.get("template_fit") or {}).get("registry_rev") or "") != current
               for r in carriers)
