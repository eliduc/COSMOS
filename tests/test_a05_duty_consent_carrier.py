"""Explicit duty facts -> payload -> exact solved-duty certificate input.

The catalogue/model are lawful temporary unit inputs. No producer acceptance,
solver execution, consent acquisition or expert approval is claimed. Actual
landing checks, conversion and solution-export functions remain in place.
"""
import copy

import pytest

from cdpm import catalog_bridge as cb, model, support_manifest
from test_a05_readiness_carrier_agent import _case
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from test_task_package import _pkg_A7D
from webapp import solution_export as se, task_package as tp


START = "2026-03-02T10:00:00"
END = "2026-03-02T12:00:00"


@pytest.fixture
def package(tmp_path, monkeypatch):
    # Real support/1 prerequisite built by the existing synthetic catalogue
    # helper; its unit stand records are not executor/release approval.
    _, doc, _, _ = _case(tmp_path, monkeypatch)
    assert model.validate_schema(doc) == model.validate_template_fit(doc) == []
    pkg = _pkg_A7D()
    pkg["model"] = doc
    cat = cb.catalog()
    pkg["manifest"].update(spec_version="1.20",
                           catalog={"version": cat.version(), "fingerprint": cat.fingerprint()},
                           support=support_manifest.project(doc), time={"time_scale": "utc"})
    tp.check_landing_projection(pkg, for_execution=True)
    tp.check_support_projection(pkg)
    return pkg


def _sheet(pkg, consent):
    pkg["clean_data"]["tables"]["D1"]["Смены"] = {
        "columns": ["Табельный номер", "Начало", "Конец", "Согласие на продление"],
        "rows": [{"Табельный номер": "E1", "Начало": START, "Конец": END,
                  "Согласие на продление": consent}]}
    return pkg["clean_data"]["tables"]["D1"]["Смены"]


def _solved(payload, employee="E1"):
    return {"flightAssignments": [{**payload["flightAssignments"][0], "employee": employee}]}


@pytest.mark.parametrize("cell,want", [("да", True), ("нет", False)])
def test_real_converter_carries_actual_consent_not_the_rule_requirement(package, cell, want):
    _sheet(package, cell)
    before = copy.deepcopy(package)
    payload = tp.to_payload(package)
    fact = payload["employees"][0]["dutyExtensionConsents"]
    assert fact == [{"start": START, "end": END, "consent": want,
                     "source": "D1/Смены: строка данных 1"}]
    assert package == before
    result = se.certifier_input(payload, _solved(payload), 600)
    assert result["duties"][0]["extension_consent"] is want
    assert result["duties"][0]["start"] == START and result["duties"][0]["end"] == END


@pytest.mark.parametrize("consent", [True, False])
def test_real_exporter_carries_exact_existing_wire_fact(package, consent):
    payload = tp.to_payload(package)
    payload["employees"][0]["dutyExtensionConsents"] = [
        {"start": START, "end": END, "consent": consent, "source": "D1/Смены: строка данных 1"}]
    solved = _solved(payload)
    before = copy.deepcopy((payload, solved))
    result = se.certifier_input(payload, solved, 600)
    assert result["duties"][0]["extension_consent"] is consent
    assert (payload, solved) == before


def test_all_declared_native_scales_are_preserved_without_guessing_utc(package):
    for declaration in ({"time_scale": "utc"},
                        {"time_scale": "base-local", "base_time_zone": "Europe/Rome"},
                        {"time_scale": "shifted", "base_tz_offset_min": 120}, None):
        pkg = copy.deepcopy(package)
        if declaration is None:
            pkg["manifest"].pop("time")
        else:
            pkg["manifest"]["time"] = declaration
        _sheet(pkg, True)
        payload = tp.to_payload(pkg)
        fact = payload["employees"][0]["dutyExtensionConsents"][0]
        assert (fact["start"], fact["end"]) == (START, END), declaration
        assert payload["flights"][0]["departureUTCDateTime"] == START
        assert se.certifier_input(payload, _solved(payload), 600)["duties"][0]["extension_consent"] is True
        report = tp.conversion_report(pkg, payload)
        assert report["duty_extension_consents"]["carried"] == 1
        assert "no UTC conversion" in report["duty_extension_consents"]["reading"]


def test_future_shifted_or_other_employee_duties_receive_no_invented_consent(package):
    payload = tp.to_payload(package)
    for start, end, owner in [
            ("2026-03-03T10:00:00", "2026-03-03T12:00:00", "E1"),
            ("2026-03-02T09:59:00", END, "E1"),
            (START, "2026-03-02T12:01:00", "E1"),
            (START, END, "E2")]:
        current = copy.deepcopy(payload)
        current["employees"][0]["dutyExtensionConsents"] = [
            {"start": start, "end": end, "consent": True, "source": "original specific duty"}]
        if owner == "E2":
            current["employees"].append({"id": "E2", "homeAirport": "PLY"})
        result = se.certifier_input(current, _solved(current, owner), 600)
        assert "extension_consent" not in result["duties"][0], (start, end, owner)


def test_missing_and_empty_source_values_remain_unknown_not_false(package):
    for value in (None, "", "  ", "missing-column"):
        pkg = copy.deepcopy(package)
        sheet = _sheet(pkg, value)
        if value == "missing-column":
            sheet["columns"].remove("Согласие на продление")
            sheet["rows"][0].pop("Согласие на продление")
        payload = tp.to_payload(pkg)
        assert "dutyExtensionConsents" not in payload["employees"][0]
        assert payload["_dutyConsentsCarry"]["unknown"] == 1
        assert "extension_consent" not in se.certifier_input(payload, _solved(payload), 600)["duties"][0]
    payload = tp.to_payload(package)
    assert payload["_dutyConsentsCarry"]["rows"] == 0


def test_source_booleans_and_existing_excel_dates_have_exact_wire_forms(package):
    for raw, want in [(True, True), (False, False), ("true", True), ("FALSE", False)]:
        pkg = copy.deepcopy(package)
        sheet = _sheet(pkg, raw)
        sheet["rows"][0].update(Начало="02.03.2026 10:00", Конец="02.03.2026 12:00")
        fact = tp.to_payload(pkg)["employees"][0]["dutyExtensionConsents"][0]
        assert (fact["start"], fact["end"], fact["consent"]) == (START, END, want)
    assert tp.duty_consent_time("02.03.2026", where="Excel midnight", source_cell=True) == "2026-03-02T00:00:00"


def test_malformed_source_cells_refuse_with_the_actual_row_address(package):
    bad = [("Согласие на продление", 1), ("Согласие на продление", "maybe"),
           ("Табельный номер", "FOREIGN"), ("Начало", "bad"),
           ("Начало", "2026-03-02T10:00:00+01:00"),
           ("Конец", "2026-03-02T09:00:00")]
    for column, value in bad:
        pkg = copy.deepcopy(package)
        _sheet(pkg, True)["rows"][0][column] = value
        with pytest.raises(tp.TaskPackageRefused) as caught:
            tp.to_payload(pkg)
        assert "D1/Смены" in str(caught.value) and "строка данных 1" in str(caught.value), (column, value)
    pkg = copy.deepcopy(package)
    _sheet(pkg, True)
    pkg["clean_data"]["tables"]["D1"]["Рейсы"]["rows"][0]["Время вылета"] = "10:00+01:00"
    with pytest.raises(tp.TaskPackageRefused, match="offset"):
        tp.to_payload(pkg)


def test_same_interval_duplicates_keep_sources_but_conflicts_refuse(package):
    sheet = _sheet(package, True)
    package["clean_data"]["tables"]["D2"] = {"Смены": copy.deepcopy(sheet)}
    payload = tp.to_payload(package)
    facts = payload["employees"][0]["dutyExtensionConsents"]
    assert [f["source"] for f in facts] == ["D1/Смены: строка данных 1", "D2/Смены: строка данных 1"]
    assert se.certifier_input(payload, _solved(payload), 600)["duties"][0]["extension_consent"] is True
    package["clean_data"]["tables"]["D2"]["Смены"]["rows"][0]["Согласие на продление"] = False
    with pytest.raises(tp.TaskPackageRefused) as caught:
        tp.to_payload(package)
    assert "конфликт" in str(caught.value) and "D1/Смены" in str(caught.value) and "D2/Смены" in str(caught.value)


def test_malformed_wire_is_refused_even_if_its_duty_was_not_solved(package):
    payload = tp.to_payload(package)
    fact = {"start": "2026-03-03T10:00:00", "end": "2026-03-03T12:00:00",
            "consent": True, "source": "original specific duty"}
    for field, value in [("consent", None), ("consent", 1), ("consent", "true"),
                         ("source", ""), ("start", START + "Z"), ("end", "2000-01-01T00:00:00")]:
        current = copy.deepcopy(payload)
        current["employees"][0]["dutyExtensionConsents"] = [{**fact, field: value}]
        with pytest.raises(se.SolutionExportError):
            se.certifier_input(current, _solved(current), 600)
    payload["employees"][0]["dutyExtensionConsents"] = None
    with pytest.raises(se.SolutionExportError, match="список"):
        se.certifier_input(payload, _solved(payload), 600)
