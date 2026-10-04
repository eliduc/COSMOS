"""A05 received-format gate, without migration or execution authorization.

The caller and package must BOTH opt into legacy archive reading. The same
original model bytes are judged at producer and receiver; missing/unknown
modern fields never become an invitation to normalize the evidence.
"""
from __future__ import annotations

from cdpm import catalog_bridge, model as m
from template_catalog import landing


def inspect(doc, manifest, *, landing_compat=None, for_execution=False):
    result = {"archive_only": False, "problems": []}

    def bad(code, words, rule=None):
        result["problems"].append({"code": code, "rule": rule,
                                   "words": f"{code}: {words}"})

    if not isinstance(doc, dict) or not isinstance(doc.get("rules"), list):
        bad("landing-incomplete", "model.json не содержит список rules")
        return result
    if not isinstance(manifest, dict):
        bad("landing-contract", "manifest.json не объект")
        return result
    compat = (type(landing_compat) is int and landing_compat == 1
              and type(manifest.get("landing_compat")) is int
              and manifest["landing_compat"] == 1)
    if (landing_compat is not None and (type(landing_compat) is not int or landing_compat != 1)
            or "landing_compat" in manifest and (type(manifest["landing_compat"]) is not int
                                                or manifest["landing_compat"] != 1)):
        bad("landing-version", "landing_compat допускает только целое 1")
        return result
    typed = []
    for rule in doc["rules"]:
        if not isinstance(rule, dict):
            bad("landing-incomplete", "правило не объект")
            continue
        if "template_fit" not in rule and rule.get("kind") not in ("HARD", "SOFT", "УСЛОВНЫЙ"):
            continue
        rid = rule.get("id")
        fit = rule.get("template_fit")
        if fit is not None and not isinstance(fit, dict):
            bad("landing-incomplete", "template_fit не объект", rid)
            continue
        version = fit.get("schema_version", "landing/1") if fit is not None else "landing/1"
        if version == "landing/1":
            result["archive_only"] = True
            if for_execution or not compat:
                bad("landing-version", "старая посадка требует landing_compat=1 у читателя "
                    "и манифеста; архивное чтение не разрешает исполнение", rid)
        elif version != "landing/2":
            bad("landing-version", f"неизвестная версия {version!r}; откат на legacy запрещён", rid)
        else:
            typed.append(rule)
    if result["problems"]:
        return result
    try:
        catalog = catalog_bridge.catalog()
        identity = manifest.get("catalog")
        if (not isinstance(identity, dict) or identity.get("fingerprint") != catalog.fingerprint()
                or identity.get("version") != str(catalog.version())):
            bad("landing-contract", "manifest.catalog не совпадает с фактическим каталогом читателя")
            return result
        for rule in typed:
            fit, rid = rule["template_fit"], rule.get("id")
            if (fit.get("category") not in ("A", "B", "C", "D")
                    or not isinstance(fit.get("registry_rev"), str) or not fit["registry_rev"].strip()):
                bad("landing-incomplete", "шапка landing/2 без category/registry_rev", rid)
                continue
            if fit["category"] in ("C", "D") and "family_contract" in fit:
                bad("landing-contract", "неразрешённая посадка C/D не имеет family_contract", rid)
                continue
            findings = landing.problems(rule, catalog)
            result["problems"].extend(findings)
            if not findings:
                result["problems"].extend(
                    {**p, "code": m.PARAM_CARRIERS_DIVERGE}
                    for p in m.carrier_problems({"rules": [rule]}))
        if not result["problems"]:
            for words in m.validate_schema(doc):
                bad("landing-incomplete", words)
    except catalog_bridge.CATALOG_ERRORS as error:
        bad("landing-contract", str(error))
    return result
