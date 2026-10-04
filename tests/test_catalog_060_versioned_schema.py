"""Catalogue 0.7 through the real validated loader; 0.5/0.6 stay legacy.

Synthetic data in pytest tmp directories, no release or live catalogue writes.
The unit map here proves schema/loader plumbing, not production vocabulary.
The historical module name stays stable for fixture importers.
"""
import importlib
import json
from decimal import Decimal

import pytest

import template_catalog as tc
from template_catalog import writer
from test_template_catalog_validate import _broken

rules = importlib.import_module("template_catalog.validate")


def modern_docs(docs, version="0.7.0"):
    docs["catalog.json"]["version"] = version
    for row in docs["criteria.json"]["criteria"]:
        row["direction"] = "minimize"
    dictionaries = docs["dictionaries.json"]
    dictionaries["directions"] = ["maximize", "minimize"]
    dictionaries["unit_words"] = {
        code: {"canon": code, "form": "integer_nonneg", "words": [code], "scale": {}}
        for code in set(dictionaries["param_units"] + dictionaries["condition_units"])}
    dictionaries["unit_words"]["duration_minutes"] = {
        "canon": "минута", "form": "integer_nonneg",
        "words": ["минута", "час", "чч:мм"], "scale": {"час": 60}}


def first_param(docs):
    return next(row["params"][0] for row in docs["families.json"]["families"]
                if row.get("params"))


@pytest.mark.parametrize("without_jsonschema", [False, True])
@pytest.mark.parametrize("version", ["0.5.0", "0.6.0", "0.6.1", "0.6.0-draft.1",
                                     "0.7.0", "0.7.0-draft.1", "0.10.0"])
def test_real_validated_loader_accepts_each_supported_shape(
        tmp_path, monkeypatch, version, without_jsonschema):
    if without_jsonschema:
        monkeypatch.setattr(rules, "_import_jsonschema", lambda: None)
    def prepare(docs):
        docs["catalog.json"]["version"] = version
        if version not in {"0.5.0", "0.6.0", "0.6.1", "0.6.0-draft.1"}:
            modern_docs(docs, version)
            first_param(docs)["subject_words"] = ["предел"]
    folder = _broken(tmp_path, prepare)
    cat = tc.current(folder, validated=True)
    assert cat.version() == version
    assert not [p for p in cat.validate() if p.severity == "error"]
    assert tc.current(folder, validated=True) is cat


CASES = [
    ("missing_direction", "X20", ".direction"),
    ("null_direction", "X20", ".direction"),
    ("bad_direction", "X20", ".direction"),
    ("missing_directions", "D02", "dictionaries.directions"),
    ("open_directions", "D02", "dictionaries.directions"),
    ("missing_unit_words", "D02", "dictionaries.unit_words"),
    ("missing_unit", "X21", "unit_words"),
    ("extra_unit", "X21", "unit_words"),
    ("nested_word", "X21", ".words"),
    ("extra_entry_field", "X21", "unit_words"),
    ("subject_null", "X06", "subject_words"),
    ("subject_empty", "X06", "subject_words"),
    ("subject_upper", "X06", "subject_words"),
    ("subject_duplicate", "X06", "subject_words"),
]


@pytest.mark.parametrize("without_jsonschema", [False, True])
@pytest.mark.parametrize("case,code,where", CASES)
def test_new_contract_has_addressed_refusals_without_schema_library(
        tmp_path, monkeypatch, case, code, where, without_jsonschema):
    if without_jsonschema:
        monkeypatch.setattr(rules, "_import_jsonschema", lambda: None)
    def mutate(docs):
        modern_docs(docs)
        criterion = next(c for c in docs["criteria.json"]["criteria"] if c["status"] == "есть")
        dictionaries = docs["dictionaries.json"]
        if case == "missing_direction":
            del criterion["direction"]
        elif case == "null_direction":
            criterion["direction"] = None
        elif case == "bad_direction":
            criterion["direction"] = "sideways"
        elif case == "missing_directions":
            del dictionaries["directions"]
        elif case == "open_directions":
            dictionaries["directions"].append("sideways")
            criterion["direction"] = "sideways"
        elif case == "missing_unit_words":
            del dictionaries["unit_words"]
        elif case == "missing_unit":
            del dictionaries["unit_words"]["duration_minutes"]
        elif case == "extra_unit":
            dictionaries["unit_words"]["invented_unit"] = dictionaries["unit_words"]["duration_minutes"]
        elif case == "nested_word":
            dictionaries["unit_words"]["duration_minutes"]["words"] = [["час"]]
        elif case == "extra_entry_field":
            dictionaries["unit_words"]["duration_minutes"]["invented"] = "field"
        else:
            first_param(docs)["subject_words"] = {
                "subject_null": None, "subject_empty": [],
                "subject_upper": ["Предел"], "subject_duplicate": ["предел", "предел"]}[case]
    folder = _broken(tmp_path, mutate)
    problems = tc.load(folder).validate()
    assert not any("проверка прервана" in p.message for p in problems), problems
    assert any(p.code == code and where in p.where for p in problems), problems
    with pytest.raises(tc.CatalogInvalid):
        tc.current(folder, validated=True)


@pytest.mark.parametrize("field", ["directions", "unit_words"])
@pytest.mark.parametrize("version", ["0.5.0", "0.6.0"])
def test_legacy_shape_cannot_smuggle_new_dictionaries_without_jsonschema(
        tmp_path, monkeypatch, field, version):
    monkeypatch.setattr(rules, "_import_jsonschema", lambda: None)
    def mutate(docs):
        docs["catalog.json"]["version"] = version
        docs["dictionaries.json"][field] = ["minimize", "maximize"] if field == "directions" else {}
    folder = _broken(tmp_path, mutate)
    problems = tc.load(folder).validate()
    assert any(p.code == "S02" and p.where == "dictionaries." + field for p in problems), problems


@pytest.mark.parametrize("version", ["0.5.0", "0.6.0", "0.7.0"])
def test_future_writer_copies_the_schema_it_used_for_validation(tmp_path, version):
    def prepare(docs):
        docs["catalog.json"]["version"] = version
        if version == "0.7.0":
            modern_docs(docs, version)
    folder = _broken(tmp_path, prepare)
    cat = tc.current(folder, validated=True)
    body = cat.body()
    output = writer.write_catalog(
        tmp_path / "written", header=cat.header, families=body["families"],
        criteria=body["criteria"], dictionaries=body["dictionaries"],
        requests=cat.requests(), versions=[], families_order=list(cat.families()))
    copied = json.loads((output.path / "schema" / "catalog.schema.json").read_text(encoding="utf-8"))
    assert ("direction" in copied["$defs"]["criterion_core"]["properties"]) == (version == "0.7.0")
    assert ("unit_words" in copied["$defs"]["dictionaries_file"]["required"]) == (version == "0.7.0")
    assert not [p for p in output.validate() if p.severity == "error"]
    contract = json.loads((output.path / "schema" / "contract.json").read_text(encoding="utf-8"))
    assert contract == {"catalog_version": version,
                        "schema_contract": "catalog-0.7.schema.json" if version == "0.7.0" else "catalog.schema.json",
                        "schema_file": "catalog.schema.json"}


@pytest.mark.parametrize("value,word,expected", [(1, "час", 60), ("1,5", "час", 90), ("1:30", "чч:мм", 90)])
def test_unit_conversion_uses_the_validated_temporary_catalogue(tmp_path, value, word, expected):
    from template_catalog.units import to_canonical
    folder = _broken(tmp_path, modern_docs)
    tc.current(folder, validated=True)
    assert to_canonical(value, word, "duration_minutes", source=folder) == expected


@pytest.mark.parametrize("value,word,unit", [
    (float("nan"), "минута", "duration_minutes"),
    (float("inf"), "минута", "duration_minutes"),
    (Decimal("sNaN"), "минута", "duration_minutes"),
    ("1:30", "count", "count"),
])
def test_unit_conversion_refuses_nonfinite_or_undeclared_clock(tmp_path, value, word, unit):
    from template_catalog.units import UnitsError, to_canonical
    folder = _broken(tmp_path, modern_docs)
    with pytest.raises(UnitsError):
        to_canonical(value, word, unit, source=folder)


@pytest.mark.parametrize("version", ["0.5.0", "0.6.0"])
def test_unit_converter_does_not_invent_a_map_for_legacy_catalogue(tmp_path, version):
    from template_catalog.units import UnitsError, to_canonical
    folder = _broken(tmp_path, lambda docs: docs["catalog.json"].update(version=version))
    with pytest.raises(UnitsError, match="unit_words"):
        to_canonical(1, "час", "duration_minutes", source=folder)


@pytest.mark.parametrize("value,word,expected", [
    ("0." + "9" * 60, "час", None),
    ("123456789012345678901234567890", "минута", 123456789012345678901234567890),
    ("10,5", "час", 630),
])
def test_unit_conversion_keeps_exactness_through_the_real_catalogue(tmp_path, value, word, expected):
    from template_catalog.units import UnitsError, to_canonical
    folder = _broken(tmp_path, modern_docs)
    tc.current(folder, validated=True)
    if expected is None:
        with pytest.raises(UnitsError, match="целый"):
            to_canonical(value, word, "duration_minutes", source=folder)
    else:
        assert to_canonical(value, word, "duration_minutes", source=folder) == expected


@pytest.mark.parametrize("without_jsonschema", [False, True])
@pytest.mark.parametrize("version", ["0.6", "v0.6.0", "0.06.0", "0.6.0.1", "", None, " 0.6.0 "])
def test_bad_header_version_is_the_first_actionable_diagnostic(
        tmp_path, monkeypatch, version, without_jsonschema):
    if without_jsonschema:
        monkeypatch.setattr(rules, "_import_jsonschema", lambda: None)
    folder = _broken(tmp_path, lambda docs: modern_docs(docs, version))
    if version in (None, ""):
        # The reader already rejects an absent version before body validation.
        with pytest.raises(tc.CatalogError, match="version"):
            tc.current(folder, validated=True)
        return
    problems = [p for p in tc.load(folder).validate() if p.severity == "error"]
    assert problems and problems[0].code == "H01", problems[:5]
    assert problems[0].where == "catalog.json/version"
    assert repr(version) in problems[0].message
    assert "0.5" in problems[0].message
    with pytest.raises(tc.CatalogInvalid) as exc:
        tc.current(folder, validated=True)
    assert "версия" in str(exc.value)


@pytest.mark.parametrize("without_jsonschema", [False, True])
@pytest.mark.parametrize("version,schema_version", [
    ("0.5.0", "0.5"), ("0.6.0", "0.5"), ("0.7.0", "0.7")])
def test_shape_refusal_names_declared_version_and_selected_schema(
        tmp_path, monkeypatch, version, schema_version, without_jsonschema):
    if without_jsonschema:
        monkeypatch.setattr(rules, "_import_jsonschema", lambda: None)
    def mutate(docs):
        if version in {"0.5.0", "0.6.0"}:
            modern_docs(docs, version)  # New body claiming the old contract.
        else:
            docs["catalog.json"]["version"] = version  # Old body claiming the new contract.
    folder = _broken(tmp_path, mutate)
    problems = [p for p in tc.load(folder).validate() if p.severity == "error"]
    assert problems
    assert version in problems[0].message
    assert "схема " + schema_version in problems[0].message
    with pytest.raises(tc.CatalogInvalid) as exc:
        tc.current(folder, validated=True)
    assert version in str(exc.value) and "схема " + schema_version in str(exc.value)
