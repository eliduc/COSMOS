"""CDPM-298/310: panel and release name invalid units and projection failures."""
import copy
import pytest
from cdpm import coverage as cov, export_task_package as etp, principles as pr
from test_cdpm_logic_param_units import _clock_in_duration
from test_cdpm_logic_proof import _rule_duration
from test_cdpm_a05_carriers_barrier import _doc_with


@pytest.mark.parametrize("qualified", [False, True])
def test_panel_reports_release_unit_refusal_without_mutating_source(qualified):
    doc = _doc_with(_clock_in_duration("earliest_departure", qualified))
    before = copy.deepcopy(doc)
    with pytest.raises(etp.TaskPackageError) as refusal:
        etp.refuse_invalid_logic_units(doc)
    panel_doc, note = cov.package_doc(doc)
    assert note == str(refusal.value)
    assert panel_doc == before and doc == before
    panel = pr.summary(doc, None, facts_doc=panel_doc, facts_notes=[note])["P4"]
    assert panel["ok"] is False and note in panel["problems"]


def test_panel_checks_units_after_healing(monkeypatch):
    doc = _doc_with(_rule_duration())
    before = copy.deepcopy(doc)
    real = etp.heal_for_package
    def corrupt(candidate):
        changed = real(candidate)
        candidate["rules"][-1] = _clock_in_duration("night_duty_start")
        return changed
    monkeypatch.setattr(etp, "heal_for_package", corrupt)
    got, note = cov.package_doc(doc)
    assert note and "time_of_day_minutes" in note and "duration_minutes" in note
    assert got == before and doc == before


def test_healthy_panel_still_returns_the_same_healed_copy():
    doc = _doc_with(_rule_duration())
    before = copy.deepcopy(doc)
    expected = copy.deepcopy(doc)
    etp.heal_for_package(expected)
    got, note = cov.package_doc(doc)
    assert note is None and got == expected and doc == before
    assert got is not doc


@pytest.mark.parametrize("with_logic", [False, True])
def test_panel_reports_unreferenced_clock_projection_refusal(with_logic):
    from test_cdpm_logic_projection_scope import _rule, CLOCK
    rule = _rule()
    if not with_logic:
        rule.pop("logic")
    doc = _doc_with(rule)
    before = copy.deepcopy(doc)
    with pytest.raises(etp.TaskPackageError) as refusal:
        etp.derive_params_from_landing(rule)
    got, note = cov.package_doc(doc)
    assert note == str(refusal.value) and CLOCK in note and "tz" in note
    assert got == before and doc == before
    panel = pr.summary(doc, None, facts_doc=got, facts_notes=[note])["P4"]
    assert panel["ok"] is False and note in panel["problems"]


@pytest.mark.parametrize("zone", ["UTC", "base-local", "acclimatized"])
def test_panel_keeps_healthy_explicit_clock_zone_and_copy_contract(zone):
    from test_cdpm_logic_projection_scope import _rule
    doc = _doc_with(_rule(tz=zone))
    before = copy.deepcopy(doc)
    expected = copy.deepcopy(doc)
    etp.heal_for_package(expected)
    got, note = cov.package_doc(doc)
    assert note is None and got == expected and doc == before
    again, note = cov.package_doc(got)
    assert again == got and note is None


def test_panel_checks_projection_after_healing_without_partial_result(monkeypatch):
    from test_cdpm_logic_projection_scope import _rule, CLOCK
    doc = _doc_with(_rule(tz="UTC"))
    before = copy.deepcopy(doc)
    real = etp.heal_for_package
    reached = []
    def corrupt(candidate):
        changed = real(candidate)
        candidate["rules"][-1]["template_fit"]["params"][CLOCK].pop("tz")
        reached.append(True)
        return changed
    monkeypatch.setattr(etp, "heal_for_package", corrupt)
    got, note = cov.package_doc(doc)
    assert reached == [True]
    assert note and CLOCK in note and "tz" in note
    assert got == before and doc == before


def test_panel_cannot_heal_away_original_clock_carrier_conflict(monkeypatch):
    from test_cdpm_logic_projection_scope import _rule, CLOCK
    rule = _rule(tz="UTC")
    rule.pop("logic")
    rule["params"] = [{"name": CLOCK, "value": 1320, "unit": "time_of_day_minutes",
                       "relation": "eq", "as_written": "22:00", "tz": "base-local"}]
    doc = _doc_with(rule)
    before = copy.deepcopy(doc)
    reached = []
    def masking_heal(candidate):
        reached.append(True)
        candidate["rules"][-1]["params"][0]["tz"] = "UTC"
        return []
    monkeypatch.setattr(etp, "heal_for_package", masking_heal)
    with pytest.raises(etp.TaskPackageError) as refusal:
        etp.refuse_diverging_carriers(doc)
    got, note = cov.package_doc(doc)
    assert note == str(refusal.value) and "tz" in note
    assert reached == []
    assert got == before and doc == before


def test_run_card_api_includes_projection_refusal_without_changing_model(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from cdpm import webui
    from test_cdpm_logic_projection_scope import _rule, CLOCK
    from test_cdpm_task_package import _run_dir
    rule = _rule()
    run = _run_dir(tmp_path, _doc_with(rule))
    model_path = run / "extract" / "model.json"
    before = model_path.read_bytes()
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    with pytest.raises(etp.TaskPackageError) as refusal:
        etp.derive_params_from_landing(rule)
    response = TestClient(webui.app).get(f"/api/runs/{run.name}")
    assert response.status_code == 200, response.text
    card = response.json()
    assert "principles_error" not in card
    p4 = card["principles"]["P4"]
    assert p4["ok"] is False
    assert str(refusal.value) in p4["problems"]
    assert CLOCK in str(refusal.value) and "tz" in str(refusal.value)
    assert model_path.read_bytes() == before
