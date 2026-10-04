"""CDPM adapter for the shared pinned family/set reference reader."""
from template_catalog import family_contract as shared


def snapshot(family, given_names=()):
    from cdpm import catalog_bridge
    return shared.snapshot(catalog_bridge.catalog(), family, given_names)


def read(fit, catalog=None):
    if "family_contract" not in fit:
        return {"legacy": True, "origin": None, "current": None, "issues": [],
                "differences": [], "verdict": "совместимость не доказана"}
    if catalog is None:
        from cdpm import catalog_bridge
        catalog = catalog_bridge.catalog()
    result = shared.read(fit["family_contract"], catalog,
                         family=fit.get("family"), registry_rev=fit.get("registry_rev"))
    result["legacy"] = False
    if fit.get("category") not in ("A", "B"):
        result["issues"].append("family_contract допустим только у посадки A/B")
    for field in ("family", "registry_rev"):
        if not isinstance(fit.get(field), str) or not fit[field].strip():
            result["issues"].append(f"ссылка family_contract без {field} посадки")
    if result["issues"]:
        result["verdict"] = "совместимость не доказана"
    return result
