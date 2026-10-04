"""Offline typed-value equivalence; actual catalog and landing laws, no LLM.

These tests do not change fixtures, release data, or acceptance criteria. The
same semantic cells use the two already legal legacy/typed representations.
"""
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from cdpm import catalog_bridge as cb, vmp_form as vf
from template_catalog import family_contract, landing
from vmp import equivalence as eq


@pytest.fixture(autouse=True)
def offline_catalog(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("equivalence profile must not use network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket.socket, "sendto", denied)
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(
        ROOT / "catalog-integration/0.7.0-draft.2"))
    cb.forget()
    yield
    cb.forget()


def document(family, name, value, *, typed=False):
    cat = cb.catalog()
    record = cat.family(family)
    spec = next(p for p in record["params"] if p["name"] == name)
    cell = {"value": deepcopy(value), "quote": "Объявленная норма"}
    fit = {"category": "A", "family": family, "params": {name: cell},
           "registry_rev": cat.fingerprint()}
    if typed:
        cell.update(unit=spec["unit"], relation=spec["relation"], condition=None,
                    source={"kind": "rule", "rule_id": "R"})
        fit.update(schema_version="landing/2", family_contract=family_contract.snapshot(
            cat, family, [name]))
    rule = {"id": "R", "title": "Норма", "statement": "Объявленная норма",
            "kind": record["kind"], "zone": record["zone"],
            "sources": [{"sid": "S1", "anchor": "п.1"}], "template_fit": fit}
    doc = {"meta": {"customer": "Offline comparator", "mode": 1,
                     "cdpm_version": "0.1"}, "sources": [],
           "rules": [rule], "objectives": [], "questions": []}
    if typed:
        assert landing.problems(rule, cat) == []
    return doc


PAIRS = [
    ("fleet_exclusions", "fleet_types", "Полярис-Р200", ["Полярис-Р200"]),
    ("fleet_exclusions", "fleet_types", "A B, B-700", ["B-700", "A B"]),
    ("reserve_floors", "reserve_crews", "GRT:1,PLY:2,SVG:1",
     {"SVG": 1, "PLY": 2, "GRT": 1}),
]


@pytest.mark.parametrize("family,name,old,new", PAIRS)
@pytest.mark.parametrize("reverse", [False, True])
def test_legal_legacy_and_typed_representations_compare_in_both_layers(
        family, name, old, new, reverse):
    etalon = document(family, name, old)
    candidate = document(family, name, new, typed=True)
    if reverse:
        candidate, etalon = etalon, candidate
    before = deepcopy((candidate, etalon))
    result = eq.equivalence(candidate, etalon)
    assert result["verdict"] == "EQUIVALENT", result["problems"]
    assert result["forms"] and result["models"]["problems"] == []
    assert (candidate, etalon) == before


@pytest.mark.parametrize("value", [
    ["A B", "WRONG"], ["A B"], ["A B", "B-700", "EXTRA"],
    ["A B", "B-700", "B-700"], ["AB", "B-700"], [], [True], [7],
    ["A B, B-700"], ["A B", ""],
])
def test_codes_remain_exact_and_duplicates_are_not_deduplicated(value):
    etalon = document("fleet_exclusions", "fleet_types", "A B, B-700")
    candidate = document("fleet_exclusions", "fleet_types", ["A B", "B-700"], typed=True)
    candidate["rules"][0]["template_fit"]["params"]["fleet_types"]["value"] = value
    assert eq.equivalence(candidate, etalon)["verdict"] == "NOT_EQUIVALENT"


@pytest.mark.parametrize("value", [
    {"GRT": 2, "PLY": 1, "SVG": 1}, {"GRT": 1, "ZZZ": 2, "SVG": 1},
    {"GRT": 1, "PLY": 2}, {"GRT": 1, "PLY": 2, "SVG": 1, "EX": 1},
    {"GRT": True, "PLY": 2, "SVG": 1}, {"GRT": 1, "PLY": -2, "SVG": 1},
    {"GRT": 1, "PLY": 2.5, "SVG": 1}, {"grt": 1, "PLY": 2, "SVG": 1},
    {"GRT": 1, "PLY": 2, "SVG": "1"}, {},
])
def test_base_map_keeps_every_exact_base_and_count(value):
    etalon = document("reserve_floors", "reserve_crews", "GRT:1,PLY:2,SVG:1")
    candidate = document("reserve_floors", "reserve_crews",
                         {"GRT": 1, "PLY": 2, "SVG": 1}, typed=True)
    candidate["rules"][0]["template_fit"]["params"]["reserve_crews"]["value"] = value
    assert eq.equivalence(candidate, etalon)["verdict"] == "NOT_EQUIVALENT"


@pytest.mark.parametrize("family,name,value,change", [
    ("fleet_exclusions", "fleet_types", "A B", {"unit": "count"}),
    ("fleet_exclusions", "fleet_types", "A B", {"relation": "min"}),
    ("fleet_exclusions", "fleet_types", "A B", {"condition": {"kind": "area", "value": "база"}}),
    ("fleet_exclusions", "fleet_types", "A B", {"qualifier": "base_code"}),
    ("reserve_floors", "reserve_crews", "GRT:1", {"unit": "duration_minutes"}),
    ("reserve_floors", "reserve_crews", "GRT:1", {"relation": "max"}),
    ("reserve_floors", "reserve_crews", "GRT:1", {"condition": {"kind": "base_code", "value": "PLY"}}),
    ("reserve_floors", "reserve_crews", "GRT:1", {"qualifier": "area"}),
])
def test_explicit_legacy_metadata_cannot_be_discarded(family, name, value, change):
    etalon = document(family, name, value)
    candidate = deepcopy(etalon)
    candidate["rules"][0]["template_fit"]["params"][name].update(change)
    assert eq.equivalence(candidate, etalon)["verdict"] == "NOT_EQUIVALENT"


@pytest.mark.parametrize("change", [
    {"unit": "duration_minutes"}, {"relation": "max"},
    {"condition": {"kind": "base_code", "value": "PLY"}},
    {"qualifier": "area"}, {"tz": "UTC"},
])
def test_modern_metadata_uses_existing_strict_landing_law(change):
    etalon = document("reserve_floors", "reserve_crews", {"GRT": 1}, typed=True)
    candidate = deepcopy(etalon)
    candidate["rules"][0]["template_fit"]["params"]["reserve_crews"].update(change)
    assert eq.equivalence(candidate, etalon)["verdict"] == "NOT_EQUIVALENT"


def form_row(form):
    return next(r for path, _title in eq.FORM_SECTIONS
                for r in eq._form_rows(form, path) if r["id"] == "R")


@pytest.mark.parametrize("change", [
    {"unit": "duration_minutes"}, {"relation": "max"},
    {"condition": {"kind": "base_code", "value": "PLY"}}, {"qualifier": "area"},
])
def test_actual_form_metadata_change_cannot_hide_behind_same_values(change, tmp_path):
    model = document("reserve_floors", "reserve_crews", {"GRT": 1}, typed=True)
    form = vf.to_form(model)
    row = form_row(form)
    row["machine"]["template_fit"]["param_cells"]["reserve_crews"].update(change)
    if "unit" in change:
        row["params"][0]["unit"] = change["unit"]
    package = tmp_path / "actual.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("vmp-form.json", json.dumps(form))
    result = eq.equivalence(model, model, cand_zip=package)
    assert result["verdict"] == "NOT_EQUIVALENT"
    assert result["forms"]["identity_problems"]


@pytest.mark.parametrize("family,name,value", [
    ("fleet_exclusions", "fleet_types", "A,B,B"),
    ("reserve_floors", "reserve_crews", "GRT:1,GRT:1"),
    ("reserve_floors", "reserve_crews", "GRT:1,PLY:2,PLY:9"),
])
def test_same_malformed_legacy_value_does_not_become_its_own_proof(family, name, value):
    model = document(family, name, value)
    assert eq.equivalence(model, model)["verdict"] == "NOT_EQUIVALENT"


def test_untyped_sequences_are_not_globally_sorted_or_stringified():
    assert eq.canon_value([1, 2]) != eq.canon_value([2, 1])
    assert eq.canon_value([1, 2]) != eq.canon_value("[1, 2]")


def test_base_counts_are_exact_beyond_binary_float_precision():
    etalon = document("reserve_floors", "reserve_crews", "GRT:9007199254740993")
    same = document("reserve_floors", "reserve_crews", {"GRT": 9007199254740993}, typed=True)
    assert eq.equivalence(same, etalon)["verdict"] == "EQUIVALENT"
    changed = deepcopy(same)
    changed["rules"][0]["template_fit"]["params"]["reserve_crews"]["value"]["GRT"] -= 1
    assert eq.equivalence(changed, etalon)["verdict"] == "NOT_EQUIVALENT"


def test_legacy_scalar_base_count_without_coordinate_is_not_proof():
    model = document("reserve_floors", "reserve_crews", 1)
    assert eq.equivalence(model, model)["verdict"] == "NOT_EQUIVALENT"
