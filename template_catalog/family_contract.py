"""Pinned family/set reference (§12.16 Е), separate from cell semantics.

Readers are pure: they return the original identity and an explicit current
target, never replace the stored pin or search for a similarly shaped set.
"""
from __future__ import annotations

import copy
import re

from . import canon, semantics as sem
from .errors import CatalogError


FIELDS = {"family_id", "catalog_version", "catalog_fingerprint",
          "record_fingerprint", "param_set"}


class ReferenceError(ValueError):
    pass


def _sets(record):
    params = record.get("params") or []
    if (not isinstance(params, list) or any(not isinstance(p, dict)
            or not isinstance(p.get("name"), str) or not p["name"] for p in params)):
        raise ReferenceError("параметры записи семьи не разрешаются")
    names = [p["name"] for p in params]
    if len(set(names)) != len(names):
        raise ReferenceError("имена параметров записи семьи неоднозначны")
    declared = record.get("param_sets") or []
    if not isinstance(declared, list):
        raise ReferenceError("наборы записи семьи не список")
    if not declared:
        return {"all": ("implicit_all", sorted(names))}
    result = {}
    for item in declared:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not item["id"] or item["id"] in result
                or not isinstance(item.get("params"), list) or not item["params"]
                or any(not isinstance(n, str) or n not in names for n in item["params"])
                or len(set(item["params"])) != len(item["params"])):
            raise ReferenceError("наборы записи семьи не разрешаются однозначно")
        result[item["id"]] = ("declared", sorted(item["params"]))
    return result


def snapshot(catalog, family: str, given_names=(), *, set_id=None):
    """New landing only. Selection follows the existing minimum-missing law."""
    record = catalog.family(family)
    sets = _sets(record)
    if set_id is None:
        from .completeness import family_completeness
        set_id = family_completeness(record, given_names)["set_id"]
    if set_id not in sets:
        raise ReferenceError(f"набор {set_id!r} не объявлен у семьи {family!r}")
    return {"family_id": family, "catalog_version": str(catalog.version()),
            "catalog_fingerprint": catalog.fingerprint(),
            "record_fingerprint": canon.record_fingerprint(record),
            "param_set": set_id}


def read(pin, catalog, *, family=None, registry_rev=None):
    result = {"origin": copy.deepcopy(pin), "current": None,
              "verdict": sem.UNPROVEN, "issues": [], "differences": [],
              "old_members": [], "new_members": [], "change": None,
              "transitions": []}

    def invalid(words):
        result["issues"].append(words)
        return result

    if (not isinstance(pin, dict) or set(pin) != FIELDS
            or not all(isinstance(v, str) and v.strip() == v and v for v in pin.values())
            or any(not re.fullmatch(r"[a-f0-9]{64}", pin[k])
                   for k in ("catalog_fingerprint", "record_fingerprint"))):
        return invalid("family_contract имеет неверную форму или отпечаток")
    if family is not None and pin["family_id"] != family:
        return invalid("family_contract.family_id расходится с family посадки")
    if registry_rev is not None and pin["catalog_fingerprint"] != registry_rev:
        return invalid("family_contract.catalog_fingerprint расходится с registry_rev посадки")
    try:
        old = sem.resolve_release(catalog, fingerprint=pin["catalog_fingerprint"],
                                  version=pin["catalog_version"])
        if str(old.version()) != pin["catalog_version"]:
            return invalid("версия family_contract не совпадает с закреплённым выпуском")
        record = old.family(pin["family_id"])
        if canon.record_fingerprint(record) != pin["record_fingerprint"]:
            return invalid("отпечаток записи family_contract не совпадает с закреплённой семьёй")
        old_sets = _sets(record)
        sid = pin["param_set"]
        if sid not in old_sets:
            return invalid(f"ссылка param_set {sid!r} отсутствует в закреплённом выпуске")
        old_kind, old_members = old_sets[sid]
        result["old_members"] = old_members
    except (CatalogError, ReferenceError) as e:
        return invalid(f"закреплённая ссылка family_contract не разрешается: {e}")
    try:
        current_record = catalog.family(pin["family_id"])
        current_sets = _sets(current_record)
    except CatalogError:
        result.update(verdict=sem.INCOMPATIBLE, change="family_removed")
        result["differences"].append(f"семья ссылки {pin['family_id']!r} убрана")
        return result
    except ReferenceError as e:
        return invalid(f"действующий набор не разрешается: {e}")
    if sid not in current_sets:
        result.update(verdict=sem.INCOMPATIBLE, change="removed")
        result["differences"].append(f"идентификатор набора {sid!r} убран; похожий набор его не заменяет")
        return result
    new_kind, new_members = current_sets[sid]
    result["current"] = snapshot(catalog, pin["family_id"], set_id=sid)
    result["new_members"] = new_members
    if old_kind != new_kind:
        result.update(verdict=sem.INCOMPATIBLE, change="reassigned")
        result["differences"].append(f"ссылка {sid!r} переназначена между all и объявленным набором")
    elif set(old_members) == set(new_members):
        result.update(verdict=sem.COMPATIBLE, change="same")
    elif set(old_members) < set(new_members):
        result.update(verdict=sem.COMPATIBLE, change="expanded")
        result["transitions"].append(
            f"набор {sid!r} расширен: было {old_members}, стало {new_members}; недостачу лечит ответ")
    elif set(new_members) < set(old_members):
        result.update(verdict=sem.COMPATIBLE, change="relaxed")
        result["transitions"].append(
            f"набор {sid!r} сужен: было {old_members}, стало {new_members}; "
            "полнота читается по явно названному текущему набору, исходная ссылка сохранена")
    else:
        result.update(verdict=sem.INCOMPATIBLE, change="reassigned")
        result["differences"].append(
            f"набор {sid!r} переназначен: было {old_members}, стало {new_members}")
    return result
