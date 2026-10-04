"""K01B-SEM-F1: current-tree proof of P0 v1.13 §12.16 В.

The historical 356dac51 fix is absent from the integral tree. These tests
use real release resolution, valid isolated 0.5.1 catalogues, and the actual
completeness/model/readiness/question readers. No solver or package export.
This is the early barrier, not the future transitive semantic reader.
"""
import copy

import pytest

import catalog_copies as cc
import template_catalog as tc
from cdpm import catalog_bridge as cb
from cdpm import export_task_package as tp
from cdpm import family_params as fp
from cdpm import model as m
from test_cdpm_k01_family_barrier import _doc_with, _landed


FAMILY = "duty_extension_penalty"
BASES = "extension_bases"
CELLS = {
    "duty_extension_cap": {"value": 960, "quote": "960 минут"},
    "long_duty_threshold": {"value": 720, "quote": "720 минут"},
    BASES: {"value": "ST1", "quote": "база ST1"},
    "extension_consent": {"value": True, "quote": "с согласия"},
}


@pytest.fixture(autouse=True)
def clean_bridge():
    cb.forget()
    yield
    cb.forget()


def edit_condition(**fields):
    def edit(rec):
        next(c for c in rec["conditions"] if c["name"] == BASES).update(fields)
    return edit


def remove_condition(rec):
    rec["conditions"] = [c for c in rec["conditions"] if c["name"] != BASES]


def changed_catalog(tmp_path, monkeypatch, edit, *, carry=True):
    """Keep the genuine old release; reject every catalogue error."""
    rev = m.registry_fingerprint()
    old = copy.deepcopy(cb.family_records()[FAMILY])
    folder = cc.copy_catalog(tmp_path, families={FAMILY: edit}, releases=True,
                             candidates=False, version="0.5.1")
    errors = [x for x in tc.load(folder).validate() if x.severity == "error"]
    assert not errors, errors
    cc.use_catalog(monkeypatch, folder)
    cb.forget()
    assert cb.stamp()["fingerprint"] != rev
    assert fp._release_records(rev)[FAMILY] == old
    cells = copy.deepcopy(CELLS)
    if not carry:
        cells.pop(BASES)
    rule = _landed(rid="R-DEP", zone="PAIRING", kind="SOFT",
                   family=FAMILY, cells=cells, rev=rev)
    rule.update(weight=3, statement="Продление с согласия на базе ST1; "
                "потолок 960 минут, порог 720 минут.")
    return _doc_with(rule)


@pytest.mark.parametrize("edit,word", [
    (edit_condition(relation="yes"), "поле «отношение»"),
    (edit_condition(unit="flag", relation="yes"), "поле «единица»"),
    (remove_condition, "из действующей записи убрано"),
])
def test_real_release_condition_changes_hold_every_reader(
        tmp_path, monkeypatch, edit, word):
    doc = changed_catalog(tmp_path, monkeypatch, edit)
    got = fp.family_completeness(doc)[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert got["verdict"] == fp.VERDICT_RELAND
    assert got["missing"] == [] and not got["curable"]
    assert any(BASES in x and word in x for x in got["contract"]["differences"])
    problem = next(x["words"] for x in got["problems"] if x.get("proven"))
    assert any(problem in x for x in m.validate_model(doc))
    assert not m.readiness(doc)["pairing_ready"]
    assert not m.readiness_matrix(doc)["pairing"]["optimization"]["ready"]
    modes, _ = tp.allowed_modes(doc)
    assert "optimization" not in modes["pairing"]
    assert "feasibility-soft" not in modes["pairing"]
    assert fp.ensure_family_param_questions(doc) == []


@pytest.mark.parametrize("carry,edit", [
    (True, edit_condition(note="Редакционное примечание")),
    (False, edit_condition(unit="flag", relation="yes")),
    (False, remove_condition),
])
def test_note_or_uncarried_condition_does_not_demand_relanding(
        tmp_path, monkeypatch, carry, edit):
    doc = changed_catalog(tmp_path, monkeypatch, edit, carry=carry)
    got = fp.family_completeness(doc)[FAMILY]
    # The common semantic reader cannot invent the absent legacy unit map.
    assert got["contract_state"] == fp.CONTRACT_UNPROVEN
    assert got["contract"]["unproven_reasons"]
    assert all("карты слов единиц" in x for x in got["contract"]["unproven_reasons"])
    assert got["verdict"] == fp.VERDICT_COMPLETE
    assert got["contract"]["differences"] == []
    assert m.validate_model(doc) == []


@pytest.mark.parametrize("direction", ["condition_to_param", "param_to_condition"])
def test_kind_change_is_named_not_misreported_as_removal(direction):
    old = copy.deepcopy(cb.family_records()[FAMILY])
    new = copy.deepcopy(old)
    src, dst, name = (("conditions", "params", BASES)
                      if direction == "condition_to_param"
                      else ("params", "conditions", "long_duty_threshold"))
    item = next(x for x in new[src] if x["name"] == name)
    new[src].remove(item)
    new[dst].append(item)
    # Pure projection oracle: no claim this unschematized record was released.
    breaks = fp._contract_breaks(fp._contract_shape(old),
                                  fp._contract_shape(new), {name})
    assert len(breaks) == 1 and name in breaks[0]
    assert "было" in breaks[0] and "стало" in breaks[0]
    assert "убран" not in breaks[0]
    assert fp._contract_breaks(fp._contract_shape(old),
                               fp._contract_shape(new), set()) == []


def test_conditions_do_not_supply_required_parameters():
    rec = cb.family_records()[FAMILY]
    shape = fp._contract_shape(rec)
    assert set(shape["conditions"]) == {BASES, "extension_consent"}
    assert shape["sets"] == [("duty_extension_cap", "long_duty_threshold")]
    assert fp.missing_params(FAMILY, {BASES, "extension_consent"}) == [
        "duty_extension_cap", "long_duty_threshold"]


def test_relation_change_has_a_positive_same_catalog_counterpart(tmp_path, monkeypatch):
    old_doc = changed_catalog(tmp_path, monkeypatch, edit_condition(relation="yes"))
    current_doc = copy.deepcopy(old_doc)
    current_doc["rules"][-1]["template_fit"]["registry_rev"] = cb.stamp()["fingerprint"]
    assert m.validate_model(current_doc) == []
    current_modes, _ = tp.allowed_modes(current_doc)
    old_modes, _ = tp.allowed_modes(old_doc)
    assert "feasibility-soft" in current_modes["pairing"]
    assert "feasibility-soft" not in old_modes["pairing"]
    assert "feasibility-hard" in current_modes["pairing"]
    assert "feasibility-hard" in old_modes["pairing"]


def test_unknown_release_does_not_invent_a_missing_parameter():
    rule = _landed(family=FAMILY, cells=copy.deepcopy(CELLS), rev="0" * 64)
    got = fp.family_completeness(_doc_with(rule))[FAMILY]
    assert got["contract_state"] == fp.CONTRACT_UNPROVEN
    assert got["missing"] == [] and got["verdict"] == fp.VERDICT_COMPLETE


def test_set_hardening_is_shortage_not_a_changed_cell_meaning():
    old = copy.deepcopy(cb.family_records()[FAMILY])
    new = copy.deepcopy(old)
    names = {p["name"] for p in old["params"]}
    new["params"].append({"name": "extra", "unit": "duration_minutes",
                          "relation": "max", "qualifier": None})
    before, after = fp._contract_shape(old), fp._contract_shape(new)
    assert fp._contract_breaks(before, after, set(CELLS)) == []
    assert fp._contract_hardening(before, after, names)
    assert fp._contract_hardening(before, after, names | {"extra"}) == []


def test_removed_carried_parameter_still_requires_relanding():
    old = copy.deepcopy(cb.family_records()[FAMILY])
    new = copy.deepcopy(old)
    new["params"] = [p for p in new["params"] if p["name"] != "long_duty_threshold"]
    breaks = fp._contract_breaks(fp._contract_shape(old),
                                  fp._contract_shape(new), set(CELLS))
    assert len(breaks) == 1 and "параметр long_duty_threshold" in breaks[0]
    assert "убран" in breaks[0]


@pytest.mark.parametrize("carried", [set(), {"long_duty_threshold"}])
def test_parameter_scope_follows_explicit_cdpm160_carried_delta(carried):
    old = copy.deepcopy(cb.family_records()[FAMILY])
    new = copy.deepcopy(old)
    next(x for x in new["params"] if x["name"] == "long_duty_threshold")["relation"] = "min"
    breaks = fp._contract_breaks(fp._contract_shape(old),
                                  fp._contract_shape(new), carried)
    assert len(breaks) == int(bool(carried))
    if carried:
        assert "изменилось отношение" in breaks[0]
