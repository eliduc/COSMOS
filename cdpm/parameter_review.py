"""Read-only projection of current model parameters for a human reviewer.

This is not evidence of solver execution or a substitute for its manifest.
Carrier provenance does not identify an answer author or a revision event.
"""
import copy

from vmp import execution_manifest as execution


def from_model(doc: dict) -> dict:
    if not isinstance(doc, dict) or not isinstance(doc.get("rules"), list):
        raise ValueError("модель должна содержать список rules")
    rows = []
    for rule in doc["rules"]:
        if not isinstance(rule, dict):
            raise ValueError("строка rules модели должна быть объектом")
        fit = rule.get("template_fit")
        if isinstance(fit, dict) and fit.get("params") is not None \
                and not isinstance(fit["params"], dict):
            raise ValueError("template_fit.params модели должен быть объектом")
        if rule.get("params") is not None and not isinstance(rule["params"], list):
            raise ValueError("params модели должен быть списком")
        rows.append({"id": str(rule.get("id") or ""),
                     "title": rule.get("title"), "kind": rule.get("kind"),
                     "zone": rule.get("zone"),
                     "blockers": [str(b) for b in rule.get("blockers", [])]
                         if isinstance(rule.get("blockers"), list) else [],
                     "params": execution.rule_params(rule)})
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    from cdpm import catalog_bridge, family_params
    try:
        states = family_params.family_completeness(doc)
        family_review = {
            "judged": True, "catalog": catalog_bridge.stamp(),
            "families": [{"family": family, "verdict": state["verdict"],
                          "contract_state": state["contract_state"],
                          "transitions": state["contract"]["transitions"],
                          "missing": state["missing"], "pending": state["pending"],
                          "problems": [p["words"] for p in state["problems"]]}
                         for family, state in states.items()]}
    except catalog_bridge.CATALOG_ERRORS as error:
        # Still show the literal model values. Failure to judge the current
        # family contract must be named, never disguised as an empty success.
        family_review = {"judged": False, "families": [], "words": str(error)}
    return {"model_revision": meta.get("revision"), "rules": rows,
            "family_review": family_review,
            "landing_migrations": copy.deepcopy(meta.get("landing_migrations", []))}
