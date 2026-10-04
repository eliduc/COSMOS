# -*- coding: utf-8 -*-
"""Семьи истории сотрудника в базе шаблонов (путь А развилки набора
«H0-КИ-окна», решение владельца 05.09.2026 «нужно учитывать»):
night_series, rolling_rest, days_off_floor.

Стенды с известным ответом: семьи и параметры в базе; посадка A на
семью — без пробела реестра и с исполнителем (covered_full); параметры
семей для построителя пейринга — «другая задача», порогов не требуют;
сертификат судит посадку кодом без показаний решателя тем же прочтением,
что и план решателя (те же находки, что у стенда планов).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from vmp import template_registry as reg          # noqa: E402
from vmp import registry_check, registry_derive   # noqa: E402
from vmp import execution_manifest as xm          # noqa: E402
from vmp.run import roster_certify as rc          # noqa: E402
from vmp.run import fit_checks as fc              # noqa: E402
from vmp.run import logic_checks as lc            # noqa: E402
from cdpm.gates import gate_certify               # noqa: E402

FAMS = {"night_series": ["max_consecutive_night_duties", "max_night_duties_in_window",
                         "night_duty_end", "night_duty_start", "night_window_days"],
        "rolling_rest": ["weekly_rest_period", "weekly_rest_required"],
        "days_off_floor": ["days_off_window_days", "min_days_off", "min_days_off_in_window"]}


def test_families_and_params_are_in_the_base():
    fams = reg.families()
    for f, params in FAMS.items():
        assert fams[f] == params, (f, fams.get(f))
        assert reg.FAMILY_KIND_ZONE[f] == ("HARD", "ROSTERING")
        assert reg.RULE_MECHANIC_TYPES[f]["executors"]
        meta = reg.families_meta()[f]
        assert meta["kind"] == "HARD" and meta["zone"] == "ROSTERING" and meta["params"] == params
    # Шаг 3 переезда на каталог (§10 схемы, строка «3»): посадки
    # штампуются полным отпечатком каталога (64 hex), а не прежней
    # 12-значной ревизией реестра.
    assert len(reg.registry_rev()) == 64


def _fit_rule(rid, family, params, zone="ROSTERING", scope=None):
    logic = {"subject": "x", "enforce": "required",
             "demand": {"kind": "count_within", "relation": "max", "param": "p"},
             "params": [{"name": k, "value": v, "unit": "count"} for k, v in params.items()]}
    if scope:
        logic["scope"] = scope
    return {"id": rid, "title": rid, "statement": "правило " + rid,
            "zone": zone, "kind": "HARD", "logic": logic,
            "params": [{"name": k, "value": v} for k, v in params.items()],
            "template_fit": {"category": "A", "family": family,
                             "registry_rev": reg.registry_rev(),
                             "params": {k: {"value": v, "quote": f"{k} = {v}"}
                                        for k, v in params.items()},
                             "quote": "правило " + rid},
            "sources": [{"sid": "S1", "anchor": "п.1"}]}


def test_fit_on_history_family_has_no_registry_gap_and_an_executor():
    r = _fit_rule("R-N", "night_series", {"max_consecutive_night_duties": 2,
                                            "night_duty_start": 1320, "night_duty_end": 360})
    assert registry_check.fit_gap(r) is None
    rec = registry_derive.derive_rule(r)
    assert rec["verdict"] == "covered_full", rec
    assert "night_series" in rec["evidence"]
    # параметры семей у построителя пейринга — «другая задача», гапов порогов нет
    rules = [r, _fit_rule("R-W", "rolling_rest", {"weekly_rest_required": 2160,
                                                  "weekly_rest_period": 10080}),
             _fit_rule("R-D", "days_off_floor", {"min_days_off": 8})]
    assert xm.thresholds_gaps(rules, {}, "pairing") == []
    assert xm.thresholds_gaps(rules, {}, "rostering") == []
    for p in ("max_consecutive_night_duties", "weekly_rest_period", "min_days_off"):
        assert xm.PARAM_EXECUTION[p]["tasks"] == ("rostering",)


def test_plan_from_fit_mirrors_the_solver_plan():
    r = _fit_rule("R-N", "night_series", {"max_consecutive_night_duties": 2,
                                            "night_duty_start": 1320, "night_duty_end": 360})
    p = lc.plan_from_fit("night_series", r)
    assert (p["form"], p["bound"], p["nightFrom"], p["nightTo"]) == (lc.FORM_NIGHT, 2, 1320, 360)
    # сдвиг шкалы меток: местное 22:00 при базе UTC+3 — метка 19:00
    p3 = lc.plan_from_fit("night_series", r, base_tz_offset_min=180)
    assert (p3["nightFrom"], p3["nightTo"]) == (1140, 180)
    w = lc.plan_from_fit("rolling_rest", _fit_rule("R-W", "rolling_rest", {
        "weekly_rest_required": 2160, "weekly_rest_period": 10080}))
    assert (w["form"], w["bound"], w["condBound"]) == (lc.FORM_ROLLING, 2160, 7)
    w2 = lc.plan_from_fit("rolling_rest", _fit_rule("R-W", "rolling_rest", {
        "weekly_rest_required": 2160, "weekly_rest_period": 7}))
    assert w2["condBound"] == 7
    d = lc.plan_from_fit("days_off_floor", _fit_rule("R-D", "days_off_floor", {"min_days_off": 8}))
    assert (d["form"], d["bound"], d["check_suffix"], d["compiled_by"]) == (
        lc.FORM_DAYS_OFF, 8, "посадка", fc.COMPILED_BY)
    # нет параметра — плана нет (правило остаётся показаниям либо компилятору)
    assert lc.plan_from_fit("night_series", _fit_rule("R-N", "night_series", {
        "max_consecutive_night_duties": 2})) is None
    assert lc.plan_from_fit("crew_composition", r) is None


PROMPTS: list[str] = []


def _compiler(prompt):
    PROMPTS.append(prompt)
    return json.dumps({"checks": [], "skipped": []}, ensure_ascii=False)


def test_certificate_judges_fitted_history_rules_by_code_without_solver_readings():
    """Тот же стенд, что у планов решателя (tests/test_audit_logic_checks),
    но правила посажены на семьи и показаний решателя нет: те же
    нарушения найдены кодом по посадке."""
    import test_audit_logic_checks as t
    svod = t._svod()
    svod["rules"] = [
        _fit_rule("R-NIGHT", "night_series", {"max_consecutive_night_duties": 2,
                                                "night_duty_start": 1320, "night_duty_end": 360}),
        _fit_rule("R-ROLL", "rolling_rest", {"weekly_rest_required": 2160,
                                             "weekly_rest_period": 10080}, scope="PLY"),
        _fit_rule("R-DAYSOFF", "days_off_floor", {"min_days_off": 8}, scope="SVG"),
    ]
    sol = t._solution(bad=True)
    sol.pop("lastRunLogic")
    PROMPTS.clear()
    report = rc.certify(svod, sol, _compiler)
    v = report["certificate"]
    for p in PROMPTS:
        for rid in ("R-NIGHT", "R-ROLL", "R-DAYSOFF"):
            assert rid not in p, "посаженное правило ушло компилятору: " + rid
    assert v["violated_rules"] == ["R-DAYSOFF", "R-NIGHT", "R-ROLL"]
    by_rule: dict[str, list[dict]] = {}
    for f in report["findings"]:
        by_rule.setdefault(f["rule_refs"][0], []).append(f)
    assert len(by_rule["R-NIGHT"]) == 1 and "2 из истории" in by_rule["R-NIGHT"][0]["text"]
    # Те же исходы, что у форм решателя (единая версия 06.09.2026, C-45):
    # по 2 ячейки нарушения у E3/E4, месяц E5 по календарю истории.
    assert len(by_rule["R-ROLL"]) == 4
    assert len(by_rule["R-DAYSOFF"]) == 1 and "по календарю истории" in by_rule["R-DAYSOFF"][0]["text"]
    checks = {c["id"]: c for c in report["checks"]}
    for rid in ("R-NIGHT", "R-ROLL", "R-DAYSOFF"):
        assert checks[f"C-{rid}-посадка"]["compiled_by"] == fc.COMPILED_BY
        assert checks[f"C-{rid}-посадка"]["status"] == t.PARTIAL
        assert checks[f"C-{rid}-посадка"]["window_coverage"]["unknown"] > 0
    assert gate_certify.check(svod, report)[0] == []
    # Отсутствие нарушений при неизвестной истории — не сертификат.
    sol2 = t._solution(bad=False)
    sol2.pop("lastRunLogic")
    report2 = rc.certify(svod, sol2, _compiler)
    assert report2["certificate"]["certified"] is False, report2["certificate"]
    assert report2["certificate"]["findings"] == 0
    assert report2["certificate"]["window_coverage"]["unknown"] > 0
    sol3 = t._fully_known_solution()
    sol3.pop("lastRunLogic")
    report3 = rc.certify(svod, sol3, _compiler)
    assert report3["certificate"]["certified"] is True, report3["certificate"]
    # Решение continue_zero (main 1.17.2): сотрудники без записи — по
    # допущению; E4 даёт одну ячейку нарушения вместо двух (свободен до
    # горизонта), E3 с недостаточной историей остаётся неизвестным.
    sol4 = t._solution(bad=True, policy=True)
    sol4.pop("lastRunLogic")
    report4 = rc.certify(svod, sol4, _compiler)
    by_rule4: dict[str, list[dict]] = {}
    for f in report4["findings"]:
        by_rule4.setdefault(f["rule_refs"][0], []).append(f)
    assert len(by_rule4["R-ROLL"]) == 3
    roll4 = {c["id"]: c for c in report4["checks"]}["C-R-ROLL-посадка"]
    assert roll4["windows_assumed_employees"] == 1
    assert roll4["window_coverage"]["unknown"] == 2
    sol5 = t._fully_known_solution(policy=True)
    sol5.pop("lastRunLogic")
    assert rc.certify(svod, sol5, _compiler)["certificate"]["certified"] is True


def test_logic_gate_counts_fitted_history_rules_without_logic_as_logic_rules():
    """Путь А: правило семьи истории с посадкой и без блока logic решатель
    исполняет формой из посадки — гейт показаний считает его правилом с
    логикой (иначе blocksSeen разошёлся бы с моделью); посадка без одного
    из параметров — нет."""
    from webapp import logic_gate as lg
    fitted = _fit_rule("R-N", "night_series", {"max_consecutive_night_duties": 2,
                                                 "night_duty_start": 1320, "night_duty_end": 360})
    fitted.pop("logic")
    partial = _fit_rule("R-P", "night_series", {"max_consecutive_night_duties": 2})
    partial.pop("logic")
    plain = {"id": "R-X", "kind": "HARD", "zone": "ROSTERING", "title": "x", "statement": "x",
             "sources": [{"sid": "S1", "anchor": "п.1"}]}
    with_logic = _fit_rule("R-L", "days_off_floor", {"min_days_off": 8})
    got = [r["id"] for r in lg.logic_rules({"rules": [fitted, partial, plain, with_logic]})]
    assert got == ["R-N", "R-L"]
    assert lg.fitted_history_form(fitted) == "night_series"
    assert lg.fitted_history_form(partial) is None and lg.fitted_history_form(plain) is None


def test_counter_parameter_sets_of_history_families():
    """Второй набор параметров семей истории (счётчики окон, 05.09.2026):
    посадка с окном даёт форму окна и у сертификата, и у гейта показаний;
    реестр — без пробела, исполнитель есть."""
    from webapp import logic_gate as lg
    assert reg.families()["night_series"] == ["max_consecutive_night_duties", "max_night_duties_in_window",
                                              "night_duty_end", "night_duty_start", "night_window_days"]
    assert reg.families()["days_off_floor"] == ["days_off_window_days", "min_days_off",
                                                "min_days_off_in_window"]
    nw = _fit_rule("R-N3", "night_series", {"max_night_duties_in_window": 2, "night_window_days": 3,
                                             "night_duty_start": 1320, "night_duty_end": 360})
    dw = _fit_rule("R-OFF7", "days_off_floor", {"min_days_off_in_window": 2, "days_off_window_days": 7})
    for r in (nw, dw):
        assert registry_check.fit_gap(r) is None
        assert registry_derive.derive_rule(r)["verdict"] == "covered_full"
    p = lc.plan_from_fit("night_series", nw)
    assert (p["form"], p["bound"], p["condBound"], p["nightFrom"], p["nightTo"]) == (
        lc.FORM_NIGHT_ROLLING, 2, 3, 1320, 360)
    d = lc.plan_from_fit("days_off_floor", dw)
    assert (d["form"], d["bound"], d["condBound"]) == (lc.FORM_DAYS_OFF_ROLLING, 2, 7)
    for r in (nw, dw):
        r.pop("logic")
    assert [x["id"] for x in lg.logic_rules({"rules": [nw, dw]})] == ["R-N3", "R-OFF7"]
    assert xm.thresholds_gaps([nw, dw], {}, "pairing") == []
    for name in ("max_night_duties_in_window", "night_window_days",
                 "min_days_off_in_window", "days_off_window_days"):
        assert xm.PARAM_EXECUTION[name]["tasks"] == ("rostering",)
