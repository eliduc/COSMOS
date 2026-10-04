"""Prepare a legacy landing upgrade without mutating evidence or writing files.

Only a resolved origin whose record still matches can be auto-confirmed.
The proposal is not an execution grant. A caller must publish the proposal
and its code-provenance changes together; raw package readers must not use it.
"""
from __future__ import annotations

import copy
import hashlib
import json

from cdpm import model as m
from template_catalog import canon, family_contract, landing, semantics, units
from template_catalog.errors import CatalogError


def _fingerprint(document):
    # Exclude our journal itself: reloads must not create an endless chain.
    evidence = {key: document.get(key) for key in ("rules", "questions")}
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def migrate_document(document, catalog=None):
    """Return a persistable model with its migration evidence, without writes.

    The journal describes historical observations, never current readiness.
    Existing malformed journal evidence is refused rather than overwritten.
    All work occurs on a copy; exceptions publish nothing to the caller.
    """
    from cdpm import catalog_bridge
    import jsonschema
    if not isinstance(document, dict) or not isinstance(document.get("rules"), list):
        return document
    meta = document.get("meta")
    if not isinstance(meta, dict):
        return document  # existing invalid shape remains diagnosable
    history = meta.get("landing_migrations", [])
    schema = json.loads(m.SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]["landing_migration_event"]
    validator = jsonschema.Draft202012Validator({"type": "array", "items": schema})
    problems = list(validator.iter_errors(history))
    if problems:
        raise ValueError("история миграции посадок повреждена: " + problems[0].message)
    legacy = []
    for index, rule in enumerate(document["rules"]):
        if not isinstance(rule, dict):
            continue
        fit = rule.get("template_fit")
        if ((isinstance(fit, dict) and fit.get("schema_version", "landing/1") == "landing/1")
                or (fit is None and rule.get("kind") in ("HARD", "SOFT", "УСЛОВНЫЙ"))):
            legacy.append((index, rule))
    if not legacy:
        return document
    try:
        catalog = catalog if catalog is not None else catalog_bridge.catalog()
        stamp = {"version": str(catalog.version()), "fingerprint": catalog.fingerprint()}
    except catalog_bridge.CATALOG_ERRORS as error:
        proposed = copy.deepcopy(document)
        items = [{"index": i, "rule": r.get("id"), "state": "unconfirmed",
                  "words": [f"каталог миграции недоступен: {error}"], "changes": []}
                 for i, r in legacy]
        stamp = None
    else:
        proposal = prepare_document(document, catalog)
        proposed = proposal["model"]
        items = [item for item in proposal["items"] if item["state"] != "unchanged"]
    event = {"schema_version": "landing-migration/1", "by": "code:landing-migration",
             "input_fingerprint": _fingerprint(document),
             "output_fingerprint": _fingerprint(proposed), "catalog": stamp, "items": items}
    problems = list(jsonschema.Draft202012Validator(schema).iter_errors(event))
    if problems:
        raise ValueError("результат миграции посадок не соответствует схеме: " + problems[0].message)
    if event not in history:
        proposed["meta"]["landing_migrations"] = [*copy.deepcopy(history), event]
    return proposed


def prepare_document(document, catalog):
    """Return a complete in-memory proposal; exceptions publish nothing.

    Status/provenance items stay outside the model until a persistence caller
    explicitly owns that contract. This function never rewrites source bytes.
    """
    proposed = copy.deepcopy(document)
    if not isinstance(document, dict) or not isinstance(document.get("rules"), list):
        raise ValueError("документ должен содержать список правил")
    items = []
    for index, rule in enumerate(document["rules"]):
        if (isinstance(rule, dict) and "template_fit" not in rule
                and rule.get("kind") not in ("HARD", "SOFT", "УСЛОВНЫЙ")):
            continue
        result = prepare_rule(rule, catalog, document=document)
        proposed["rules"][index] = result["rule"]
        items.append({"index": index, "rule": rule.get("id") if isinstance(rule, dict) else None,
                      **{key: result[key] for key in ("state", "words", "changes")}})
    return {"schema_version": "landing-migration/1", "model": proposed, "items": items}


def prepare_rule(rule, catalog, *, document=None):
    original = copy.deepcopy(rule)
    result = {"state": "unconfirmed", "rule": original, "words": [], "changes": []}

    def refuse(words, *, state="required"):
        result.update(state=state, words=[str(words)], changes=[])
        return result

    if not isinstance(rule, dict):
        return refuse("правило не объект", state="unconfirmed")
    fit = rule.get("template_fit")
    if not isinstance(fit, dict):
        return refuse("исходная посадка и её семья не установлены", state="unconfirmed")
    if fit.get("schema_version", "landing/1") not in ("landing/1",):
        # An existing modern shape is never repaired by the legacy migration.
        result["state"] = "unchanged"
        return result
    if fit.get("category") not in ("A", "B"):
        return refuse("перепосадка C/D требует разрешения исходной неопределённости")
    if any(not isinstance(fit.get(key), str) or not fit[key].strip()
           for key in ("family", "registry_rev")):
        return refuse("исходная семья/ревизия не названа", state="unconfirmed")
    cells = fit.get("params", {})
    if not isinstance(cells, dict) or any(not isinstance(c, dict) for c in cells.values()):
        return refuse("ячейки исходной посадки не объекты")
    if any("pending_revision" in c for c in cells.values()):
        return refuse("пересмотр открыт: снимок ячейки не меняется", state="unconfirmed")
    family = fit.get("family")
    if document is not None:
        from cdpm import family_params
        for question in m.open_questions(document):
            if not m.is_revision_question(question):
                continue
            baseline = question.get("revision_cells")
            targets = baseline.get("targets", {}) if isinstance(baseline, dict) else {}
            target = family_params.question_target(question)
            if ((isinstance(targets, dict) and str(rule.get("id")) in targets)
                    or rule.get("id") in question.get("rule_refs", [])
                    or (target is not None and target[0] == family)):
                # Legacy reconsideration may have removed its old cell. CAS
                # still includes the landing header, so adding a pin/version
                # would invalidate the captured baseline even without a marker.
                return refuse("пересмотр открыт: контракт посадки входит в снимок ячейки",
                              state="unconfirmed")
    proposed = copy.deepcopy(rule)
    target = proposed["template_fit"]
    try:
        if "family_contract" in fit:
            reference = family_contract.read(fit["family_contract"], catalog,
                family=family, registry_rev=fit.get("registry_rev"))
            if reference["issues"]:
                return refuse("; ".join(reference["issues"]), state="unconfirmed")
            pin = fit["family_contract"]
            origin = semantics.resolve_release(catalog, fingerprint=pin["catalog_fingerprint"],
                                               version=pin["catalog_version"])
        else:
            rev = fit.get("registry_rev")
            if not isinstance(rev, str) or not rev.strip():
                return refuse("выпуск исходной посадки не назван", state="unconfirmed")
            versions = {str(row.get("version")) for row in catalog.versions() or ()
                        if isinstance(row, dict) and row.get("fingerprint") == rev}
            if catalog.fingerprint() == rev:
                versions.add(str(catalog.version()))
            if len(versions) > 1:
                return refuse("отпечаток общий для нескольких выпусков; исходная версия не установлена",
                              state="unconfirmed")
            origin = semantics.resolve_release(catalog, fingerprint=rev,
                version=next(iter(versions)) if versions else None)
        old_record, current_record = origin.family(family), catalog.family(family)
        if canon.record_fingerprint(old_record) != canon.record_fingerprint(current_record):
            return refuse("содержание исходной записи семьи изменилось; нужна перепосадка")
        # Identical record bytes do not prove unchanged referenced dictionaries.
        comparison = semantics.readings_compare(
            semantics.record_reading(origin, "families", family),
            semantics.record_reading(catalog, "families", family))
        if comparison["verdict"] != semantics.COMPATIBLE:
            return refuse("смысл словарных зависимостей исходной записи не подтверждён")

        params_only = not cells and bool(rule.get("params"))
        if params_only:
            # Existing name-based fill is safe only with the same proven record
            # and the actual catalogue used by that legacy helper.
            from cdpm import catalog_bridge
            if catalog_bridge.catalog().fingerprint() != catalog.fingerprint():
                return refuse("каталог заполнения params[] отличается от проверенного")
            target = m.fill_fit_params_from_rule(target, rule["params"])
            proposed["template_fit"] = target
            for name, cell in target.get("params", {}).items():
                carriers = [p for p in rule["params"]
                            if isinstance(p, dict) and p.get("name") == name]
                if len(carriers) != 1:
                    return refuse(f"{name}: источник params[] неоднозначен")
                for field in ("unit", "relation", "condition", "tz"):
                    if field in carriers[0]:
                        cell[field] = copy.deepcopy(carriers[0][field])
            if not target.get("params"):
                return refuse("params[] не даёт ячеек объявленной семьи")
        if "family_contract" not in target:
            target["family_contract"] = family_contract.snapshot(
                origin, family, target.get("params", {}).keys())
        target["schema_version"] = "landing/2"
        changes = []
        for name, cell in target.get("params", {}).items():
            before = copy.deepcopy(cell)
            reading = semantics.cell_reading(origin, "families", family, name)
            if reading["unresolved"]:
                return refuse(f"{name}: исходная ячейка не разрешается")
            unit = reading["fields"]["unit"]
            source_unit = cell.get("unit", unit)
            form = reading["dictionaries"]["unit"]["form"]
            from cdpm import family_params
            cell["value"] = family_params.legacy_structural_value(cell.get("value"), reading)
            base_map = reading["fields"]["qualifier"] == "base_code" and isinstance(cell.get("value"), dict)
            numeric = form in units.UNIT_FORMS and form not in units.NON_NUMERIC_FORMS and not base_map
            if numeric:
                cell["value"] = units.to_canonical(cell.get("value"), source_unit,
                                                    unit, source=origin.path)
            elif source_unit != unit:
                return refuse(f"{name}: единица структурного значения расходится с исходной записью")
            cell["unit"] = unit
            cell.setdefault("relation", reading["fields"]["relation"])
            cell.setdefault("condition", None)
            # Code provenance must not launder an already inconsistent value
            # (for example saved 30 with quote "3") into a confirmed landing.
            if not (isinstance(cell.get("source"), dict)
                    and cell["source"].get("kind") == "answer"):
                from cdpm import catalog_bridge, family_params
                if catalog_bridge.catalog().fingerprint() != catalog.fingerprint():
                    return refuse("каталог разбора цитаты отличается от проверенного")
                if base_map:
                    problems = family_params.base_map_quote_problems(cell["value"], cell.get("quote"))
                    if problems:
                        return refuse("; ".join(problems))
                elif numeric:
                    parsed = family_params.parse_rule_quote(cell.get("quote"), unit,
                                                            source=origin.path)
                    if (parsed is None or parsed[0] != cell.get("value")
                            or isinstance(parsed[0], bool) != isinstance(cell.get("value"), bool)):
                        return refuse(f"{name}: сохранённое значение не подтверждено цитатой")
                # Existing nonnumeric canonical values keep their meaning
                # under the proven unchanged origin. Do not reinterpret a
                # flag/list as a number or invent a new prose grammar. The
                # strict typed judge below checks their real JSON forms.
            if "source" not in cell:
                if "answer" in cell:
                    return refuse(f"{name}: точная версия ответа ещё не подтверждена", state="unconfirmed")
                cell["source"] = {"kind": "code", "note":
                    "Миграция сохранённой legacy-ячейки по доказанному исходному контракту семьи"}
            if not isinstance(cell["source"], dict):
                return refuse(f"{name}: источник не объект", state="unconfirmed")
            from cdpm import provenance
            if provenance.is_answer_cell(cell):
                if document is None:
                    return refuse(f"{name}: для проверки источника ответа нужен документ", state="unconfirmed")
                problems = provenance.cell_problems(document, rule.get("id"), family, name, cell)
                if problems:
                    return refuse("; ".join(problems), state="unconfirmed")
            fields = sorted(k for k in set(before) | set(cell)
                            if k not in before or k not in cell or before[k] != cell[k])
            if fields or params_only:
                changes.append({"param": name, "fields": fields,
                    "source": {"kind": "code", "note":
                        "Перенос из params[] и проверка исходного контракта" if params_only else
                        "Дополнение/нормализация метаданных по исходному контракту; источник значения сохранён"}})
        problems = landing.problems(proposed, catalog)
        problems += [{"words": p["words"]} for p in m.carrier_problems({"rules": [proposed]})]
        # Validate the standalone rule using the authoritative model schema;
        # unrelated missing top-level sections are not rule-shape failures.
        problems += [{"words": words} for words in m.validate_schema({"rules": [proposed]})
                     if words.startswith("схема: rules/")]
        if problems:
            return refuse("; ".join(p["words"] for p in problems))
    except (CatalogError, family_contract.ReferenceError, ValueError, TypeError) as error:
        return refuse(str(error), state="unconfirmed")
    result.update(state="confirmed", rule=proposed, changes=changes,
                  words=["Форма landing/2 подтверждена по исходной записи; исполнение судится отдельно"])
    return result
