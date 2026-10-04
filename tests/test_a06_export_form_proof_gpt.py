"""A06 export_form evidence, deliberately split into two different claims.

Native draft.3 duty worlds prove their scalar projection and the REAL
conditions_export.build_conditions refusal: these worlds do not contain the
complete worktime_exact input contract. They are NOT native export_form PASS.
The separate complete unit fixture below explicitly supplies the same kinds
of inputs as tests/vmp/test_conditions_export.py and exercises the registered
exporter and actual Conditions reader. It is NOT inserted into a native ZIP.
No approvals, stand_verified rows, services, or general translator are used.
"""
import copy
import hashlib
import io
import json
import zipfile

import pytest

from _conditions import Conditions
from cdpm import export_task_package as tp
from cdpm.gates import gate_task_package as gate
from test_a06_native_stand_proof_agent import prepared, prepare_world  # noqa: F401
from vmp import conditions_export as ce, stand_adapters as sa, stand_solver as ss
from webapp import task_package as receiver


NATIVE_GAPS = {
    "crew_min_by_fleet", "briefing_base_min", "briefing_offbase_min",
    "debrief_base_min", "debrief_offbase_min", "sit_offbase_min",
    "rest_base_by_work_h", "rest_offbase_by_work_h", "standard_night",
    "b3_worktime_table", "worktime_exact.acclimatized_by_period",
    "worktime_exact.unknown_by_landings", "worktime_exact.extended_by_class_h",
    "worktime_exact.rest_class_by_fleet", "worktime_exact.acclimatization",
}


@pytest.fixture(params=["st-duty-1", "st-duty-3"])
def native(prepared, tmp_path, request, monkeypatch):
    monkeypatch.delenv("CREW_VMP_DETECTORS", raising=False)
    task = request.param
    base = tmp_path / task
    world, rules, _, _, _, _ = prepare_world(prepared, base, task)
    original = copy.deepcopy(world)
    body = ss.package_bytes(world, rules, stamp=ss.stamp_of(prepared),
                            base=base, catalog=prepared, family="duty_limits",
                            spec_version="1.20")
    path = tmp_path / (task + ".zip")
    path.write_bytes(body)
    assert gate.check(path)[0] == []
    pkg = receiver.load(path, run_gate=False)  # gate above is in-process
    assert all(c["approved"] is None for c in pkg["manifest"]["support"]["cells"])
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        files = {name: z.read(name) for name in z.namelist()}
    assert "threshold-forms.json" not in files
    assert world == original
    return task, world, pkg, files


def test_native_original_parameters_reach_reader_and_real_exporter_refuses_gaps(native):
    task, world, pkg, files = native
    expected = ({"max_duty_duration": 780, "max_landings_per_duty": 4,
                 "max_pairing_days": 7} if task == "st-duty-1"
                else {"max_pairing_days": 7})
    assert sa.params_of(world) == expected
    rule, = pkg["model"]["rules"]
    assert rule["id"] == "R-STAND-duty_limits"
    assert {p["name"]: p["value"] for p in rule["params"]} == expected
    assert {name: cell["value"] for name, cell in
            rule["template_fit"]["params"].items()} == expected
    # Independent declared numeric oracle; no exporter output supplies it.
    assert rule["zone"] == "PAIRING" and rule["kind"] == "HARD"
    assert not pkg["manifest"].get("crew_size")
    conditions_questions = {"Q-3a-условия-прил4", "Q-3a-условия-прил6",
                            "Q-3a-условия-акклиматизация",
                            "Q-3a-условия-размах-командировки"}
    assert not conditions_questions.intersection(
        q["id"] for q in pkg["model"].get("questions", []))
    before = copy.deepcopy(pkg)
    payload, _ = sa.world_to_builder(world)
    with pytest.raises(ce.ConditionsExportError) as refused:
        ce.build_conditions(pkg["model"], None, pkg["manifest"], payload)
    assert {g["key"] for g in refused.value.gaps} == NATIVE_GAPS
    assert all(g["missing"] and g["closure"] for g in refused.value.gaps)
    assert pkg == before
    assert pkg["manifest"]["allowed_modes"]["pairing"] == []
    with pytest.raises(receiver.TaskPackageRefused) as support:
        receiver.check_support_execution(pkg, task="pairing", mode="feasibility-hard")
    assert support.value.code == "support-missing"


def test_rehashed_invented_native_matrix_reaches_semantic_gate(native, tmp_path):
    _, _, _, original_files = native
    # A forged matrix cannot turn a scalar-only native world into an admitted
    # complete export form. Recompute EVERY inventory digest/size so checksum
    # failure cannot masquerade as the causal semantic rejection.
    for bad_limit in (780, 999):
        files = dict(original_files)
        files["threshold-forms.json"] = json.dumps({"spec": "1.10", "matrices": [{
            "threshold": "max_duty_span_min", "tz": "base-local",
            "rows": [{"start_from_min": 0, "start_to_min": 1440,
                      "sectors_from": 1, "sectors_to": 2,
                      "value_min": bad_limit, "rule": "R-STAND-duty_limits"}]}]}).encode()
        manifest = json.loads(files.pop("manifest.json"))
        manifest["files"] = [{"name": name, "sha256": hashlib.sha256(raw).hexdigest(),
                              "bytes": len(raw)} for name, raw in sorted(files.items())]
        files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        path = tmp_path / ("rehashed-matrix-%d.zip" % bad_limit)
        path.write_bytes(tp.write_zip(files))
        file_problems, _ = gate._check_files(files)
        assert file_problems == []
        problems, _ = gate.check(path)
        assert any("threshold-forms.json есть, а из модели" in p for p in problems), problems
        assert not any("sha256" in p or "контрольная сумма" in p for p in problems)


def _complete_unit_inputs():
    """Explicit isolated unit context, NOT a repair of either catalogue world.

    Values/answer forms follow the existing complete conditions unit fixture.
    No test approval, norms, or answer is added to the native package above.
    """
    rules = []
    for rid, name, value, statement in (
        ("R-BRIEF", "briefing_min", 90, "Предполётная подготовка не менее 90 минут."),
        ("R-DEBRIEF", "debriefing_min", 30, "Послеполётный разбор не менее 30 минут."),
        ("R-SIT", "min_turnaround", 50, "Стыковка не менее 50 минут."),
        ("R-REST-H", "min_rest_home", 720, "Отдых не менее предыдущей смены и не менее 12 часов."),
        ("R-REST-O", "min_rest_outstation", 600, "Отдых не менее предыдущей смены и не менее 10 часов."),
    ):
        rules.append({"id": rid, "kind": "HARD", "statement": statement,
                      "params": [{"name": name, "value": value,
                                  "unit": "duration_minutes", "relation": "min"}]})
    rules.append({"id": "R-NIGHT", "kind": "DEF", "statement": "Ночь 22:00–06:00.",
                  "params": [{"name": n, "value": v, "unit": "time_of_day_minutes",
                              "relation": "eq"} for n, v in
                             (("night_duty_start", 1320), ("night_duty_end", 360))]})
    answers = {
        "Q-3a-условия-прил4": "1-2 сектора = 11:30; 3 сектора = 11:00; 4 сектора = 10:30; 5 и более секторов = 9:30.",
        "Q-3a-условия-прил6": "T38 = класс 2; класс 2 = 17:00.",
        "Q-3a-условия-акклиматизация": "tz_from_h = 2; elapsed_b_h = 48.",
    }
    questions = [{"id": qid, "part": "3a", "title": qid, "text": "unit fixture",
                  "answer_format": {"type": "value"},
                  "answer": {"raw": raw, "status": "применён", "revision": 9}}
                 for qid, raw in answers.items()]
    rows = []
    for lo, hi, values in ((360, 900, (780, 750, 720, 540)),
                            (900, 1020, (750, 720, 690, 540)),
                            (1020, 1440, (720, 690, 660, 540)),
                            (0, 360, (690, 660, 630, 540))):
        for (sf, st), value in zip(((1, 2), (3, 3), (4, 4), (5, 10)), values):
            rows.append({"start_from_min": lo, "start_to_min": hi,
                         "sectors_from": sf, "sectors_to": st, "value_min": value,
                         "as_written": "explicit existing unit fixture", "rule": "R-MATRIX"})
    forms = {"spec": "1.10", "matrices": [{"threshold": "max_duty_span_min",
             "tz": "base-local", "rows": rows, "source_rules": ["R-MATRIX"]}]}
    payload = {"employees": [{"id": "E1", "homeAirport": "HUB"}], "flights": [{
        "flightNumber": "F1", "departureUTCDateTime": "2025-10-02T08:00:00",
        "arrivalUTCDateTime": "2025-10-02T10:00:00", "aircraftType": "T38"}]}
    return {"rules": rules, "questions": questions}, forms, {"crew_size": {"value": 2, "rule": "R-CREW"}}, payload


@pytest.mark.parametrize("minute,landings,expected", [(359, 1, 690), (360, 1, 780),
    (899, 3, 750), (900, 3, 720), (1020, 4, 660), (1439, 5, 540)])
def test_complete_unit_export_reaches_real_conditions_reader(minute, landings, expected, tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CREW_VMP_DETECTORS", raising=False)
    inputs = _complete_unit_inputs()
    before = copy.deepcopy(inputs)
    result = ce.build_conditions(*inputs)
    reader = Conditions(json.loads(json.dumps(result)))
    assert reader.has_exact_worktime and reader.limit_per_duty
    assert reader.exact_limits(minute, landings, True, "T38")[0] == expected
    assert reader.exact_limits(minute, 1, False, "T38")[0] == 690
    assert reader.crew_std("T38") == 2
    assert inputs == before


@pytest.mark.parametrize("damage,key", [("midnight", "worktime_exact.acclimatized_by_period"),
    ("missing_answer", "worktime_exact.unknown_by_landings"),
    ("wrong_floor_unit", "rest_base_by_work_h")])
def test_complete_unit_semantic_damage_reaches_exporter(damage, key, tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CREW_VMP_DETECTORS", raising=False)
    model, forms, manifest, payload = _complete_unit_inputs()
    assert Conditions(ce.build_conditions(model, forms, manifest, payload)).has_exact_worktime
    if damage == "midnight":
        forms["matrices"][0]["rows"][0]["start_from_min"] = 1380
        forms["matrices"][0]["rows"][0]["start_to_min"] = 60
    elif damage == "missing_answer":
        model["questions"] = [q for q in model["questions"] if q["id"] != "Q-3a-условия-прил4"]
    else:
        next(r for r in model["rules"] if r["id"] == "R-REST-H")["params"][0]["unit"] = "count"
    with pytest.raises(ce.ConditionsExportError) as refused:
        ce.build_conditions(model, forms, manifest, payload)
    assert key in {g["key"] for g in refused.value.gaps}
