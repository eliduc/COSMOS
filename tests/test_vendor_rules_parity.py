"""Тесты паритета правил (Ступени 1-2, 2026-07-24): выпуск Пакета правил
из солвер-файла, ростер вендора поверх payload, валидаторы сертификата.
Синтетика вымышленного перевозчика."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from _solver_to_payload import (_pt_minutes, build_rule_pack,      # noqa: E402
                                build_vendor_solution)
from _vendor_rules_check import load_pack_values, run_checks       # noqa: E402
from tests.test_solver_adapter import make_solver_only             # noqa: E402


def test_pt_minutes():
    assert _pt_minutes("PT100H") == 6000
    assert _pt_minutes("PT12H30M") == 750
    assert _pt_minutes("PT0S") == 0
    assert _pt_minutes("garbage") is None
    assert _pt_minutes(None) is None


def test_rule_pack_shape_and_importability():
    solver = make_solver_only()
    solver["parameters"] = dict(solver["parameters"],
                                maximumPairingDays=6,
                                layoverRules={"layoverAfterGround": "PT12H"})
    solver["constraintWeightOverrides"] = {
        "WEEKLY_REST": "HIGH_HIGH", "SBY_COUNT_LIMIT": "OFF"}
    solver["employees"][0]["flightTimeLimit"] = "PT100H"
    # Страна юрисдикции — только из данных вызывающего (QQ — код
    # вымышленного перевозчика этой синтетики); голый вызов честно
    # оставляет поле пустым, обязательность держат точки входа.
    pack = build_rule_pack(solver, "pytest.json", country="QQ")
    assert pack["country"] == "QQ"
    assert build_rule_pack(solver, "pytest.json")["country"] == ""
    from _rules_import import RulesImportError, import_rules
    with pytest.raises(RulesImportError, match="country"):
        import_rules(solver, "solver-format5", "pytest.json")
    kinds = [r["constraint"]["kind"] for r in pack["rules"]]
    # 3 исполняемых (Ярус 1) + структурные записи всех параметров и весов.
    assert kinds.count("parameter") == 3
    assert kinds.count("note") == len(solver["parameters"]) + 2
    # Совместимость с валидатором Пакетов правил COSMOS: каждое правило
    # проходит _validate_rule_fields без исключений.
    from webapp.rule_packs import _validate_rule_fields
    for r in pack["rules"]:
        _validate_rule_fields(r)
    # Значения восстановимы обратно (паритет чтения).
    vparams, weights = load_pack_values(pack)
    assert vparams["maximumPairingDays"] == 6
    assert weights == {"WEEKLY_REST": "HIGH_HIGH",
                       "SBY_COUNT_LIMIT": "OFF"}


def _mini_world():
    """payload+catalog+ростер: один юнит из двух участков, два человека."""
    payload = {
        "airports": [{"code": "BSE"}, {"code": "SEC"}],
        "employees": [
            {"id": "E1", "homeAirport": "BSE", "skills": ["CA"],
             "vendorConstraints": {"flightTimeLimit": "PT1H",
                                   "doNotFlyWith": ["E2"]}},
            {"id": "E2", "homeAirport": "BSE", "skills": ["CA"]},
        ],
        "flights": [
            {"flightNumber": "F1", "departureAirport": "BSE",
             "departureUTCDateTime": "2026-08-05T03:00:00",
             "arrivalAirport": "SEC",
             "arrivalUTCDateTime": "2026-08-05T04:00:00"},
            {"flightNumber": "F2", "departureAirport": "SEC",
             "departureUTCDateTime": "2026-08-05T06:00:00",
             "arrivalAirport": "BSE",
             "arrivalUTCDateTime": "2026-08-05T07:00:00"},
        ],
        "flightAssignments": [
            {"id": "F1#0", "flight": "F1", "indexInFlight": 0,
             "requiredSkill": "CA", "employee": None},
            {"id": "F1#1", "flight": "F1", "indexInFlight": 1,
             "requiredSkill": "CA", "employee": None},
            {"id": "F2#0", "flight": "F2", "indexInFlight": 0,
             "requiredSkill": "CA", "employee": None},
            {"id": "F2#1", "flight": "F2", "indexInFlight": 1,
             "requiredSkill": "CA", "employee": None},
        ],
    }
    catalog = {"selected": [
        {"fns": ["F1", "F2"], "active_fns": ["F1", "F2"],
         "seats": ["CA", "CA"], "sourceUnit": "sequence:SQ1"}]}
    return payload, catalog


def test_checks_conflict_dnfw_and_flight_time():
    payload, catalog = _mini_world()
    roster = json.loads(json.dumps(payload))
    for a in roster["flightAssignments"]:
        a["employee"] = "E1" if a["indexInFlight"] == 0 else "E2"
    weights = {"ASSIGNMENT_CONFLICT": "HIGH_HIGH",
               "DO_NOT_FLY_WITH": "HIGH_HIGH",
               "FLIGHT_TIME_LIMIT": "HIGH_HIGH",
               "UNASSIGNED": "STD",
               "SBY_COUNT_LIMIT": "OFF"}
    cert = run_checks(payload, roster, catalog, {}, weights)
    r = cert["rules"]
    assert r["ASSIGNMENT_CONFLICT"]["violations"] == 0
    # E1 и E2 несовместимы, но летят одним юнитом.
    assert r["DO_NOT_FLY_WITH"]["violations"] == 1
    # Лимит E1 = 1 час, налёт = 2 часа -> нарушение месяца.
    assert r["FLIGHT_TIME_LIMIT"]["violations"] == 1
    assert r["UNASSIGNED"]["violations"] == 0
    # OFF вендора уважается: правило не проверяется вовсе.
    assert "SBY_COUNT_LIMIT" in cert["off_respected"]


def test_common_score_lexicographic_scale():
    """Единая шкала сравнения: HIGH_HIGH/HIGH -> жёсткие; STD/LOW/LOW_LOW
    -> мягкие с объявленными весами 1000/100/10; OFF не считается."""
    from _vendor_rules_check import common_score
    rules = {
        "A": {"weight_level": "HIGH_HIGH", "violations": 2, "checked": 10},
        "B": {"weight_level": "HIGH", "violations": 1, "checked": 10},
        "C": {"weight_level": "STD", "violations": 3, "checked": 10},
        "D": {"weight_level": "LOW", "violations": 4, "checked": 10},
        "E": {"weight_level": "LOW_LOW", "violations": 5, "checked": 10},
        "F": {"weight_level": "OFF", "violations": 99, "checked": 10},
    }
    cs = common_score(rules)
    assert cs["hard_violations"] == 3            # 2 + 1
    assert cs["weighted_soft_penalty"] == 3450   # 3*1000 + 4*100 + 5*10
    assert cs["scale"]["OFF"]["weight"] == 0


def test_vendor_solution_fills_by_duty():
    solver = make_solver_only()
    # вендор занял CA-место наряда SD1 сотрудником E001
    solver["employeeAssignments"].append(
        {"id": "vf", "duty": "SD1", "preferredRank": "CA",
         "employee": "E001", "sequence": "SQ1_CA_1"})
    import subprocess
    from tests.test_solver_adapter import run_adapter
    # payload соберём адаптером, решение — функцией напрямую
    from _solver_to_payload import convert
    payload, _, errors, _ = convert(solver, "pytest.json", "check.json")
    assert not errors
    vsol = build_vendor_solution(solver, payload)
    filled = [a for a in vsol["flightAssignments"] if a.get("employee")]
    # SD1 покрывает участок QQ100 -> одно CA-место на нём занято E001.
    assert len(filled) == 1
    assert filled[0]["flight"] == "QQ100-20260805-1"
    assert filled[0]["employee"] == "E001"
    assert vsol["vendor_filled_rows"] == 1


def test_completeness_verdict_names_what_was_not_proven():
    """«Ноль нарушений» — утверждение о том, что проверено. Пока не
    сказано, что осталось непроверенным, сертификат читается как
    доказательство соответствия, которым он не является
    (аудит 2026-07-26, P0-06)."""
    from _vendor_rules_check import completeness

    # 1. включённое ЖЁСТКОЕ правило без проверятеля
    cert = {"rules": {"ASSIGNMENT_CONFLICT": {"violations": 0,
                                              "checked": 4}},
            "not_implemented": ["GENDER_ACCOMMODATION"]}
    weights = {"ASSIGNMENT_CONFLICT": "HIGH_HIGH",
               "GENDER_ACCOMMODATION": "HIGH_HIGH"}
    c = completeness(cert, weights)
    assert c["verdict"] == "INCOMPLETE"
    assert c["hard_rules_unimplemented"] == ["GENDER_ACCOMMODATION"]

    # мягкое нереализованное правило полноту не рушит
    c2 = completeness(cert, {"ASSIGNMENT_CONFLICT": "HIGH_HIGH",
                             "GENDER_ACCOMMODATION": "LOW"})
    assert c2["verdict"] == "OK"

    # 2. правило без единой проверки
    c3 = completeness({"rules": {"WEEKLY_REST": {"violations": 0,
                                                 "checked": 0}},
                       "not_implemented": []}, {"WEEKLY_REST": "HIGH_HIGH"})
    assert c3["verdict"] == "INCOMPLETE"
    assert c3["rules_without_checks"] == ["WEEKLY_REST"]

    # 3. проверки, пропущенные из-за отсутствия данных
    c4 = completeness({"rules": {"ASSIGNMENT_REQUIREMENTS": {
        "violations": 0, "checked": 7, "skipped": 12}},
        "not_implemented": []}, {"ASSIGNMENT_REQUIREMENTS": "HIGH_HIGH"})
    assert c4["verdict"] == "INCOMPLETE"
    assert c4["checks_skipped_for_missing_data"] == {
        "ASSIGNMENT_REQUIREMENTS": 12}


def test_certificate_cli_exit_codes(tmp_path):
    """0 — полный сертификат без жёстких нарушений; 2 — есть жёсткие
    нарушения; 3 — сертификат неполон. Раньше во всех случаях был 0."""
    import subprocess

    payload, catalog = _mini_world()
    roster = json.loads(json.dumps(payload))
    for a in roster["flightAssignments"]:
        a["employee"] = "E1" if a["indexInFlight"] == 0 else "E2"

    def run(weights_rules):
        pay = tmp_path / "p.json"
        pay.write_text(json.dumps(payload), encoding="utf-8")
        ros = tmp_path / "r.json"
        ros.write_text(json.dumps(roster), encoding="utf-8")
        cat = tmp_path / "c.json"
        cat.write_text(json.dumps(catalog), encoding="utf-8")
        pack = tmp_path / "pack.json"
        pack.write_text(json.dumps({"name": "t", "rules": weights_rules}),
                        encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(REPO / "_vendor_rules_check.py"),
             "--payload", str(pay), "--roster", str(ros),
             "--catalog", str(cat), "--rules", str(pack)],
            capture_output=True, text=True, cwd=REPO,
            encoding="utf-8", errors="replace")
        return proc

    def _rule(code, level):
        return {"title": code, "constraint": {"kind": "note", "note": json.dumps(
            {"vendor_constraint": code, "weight_level": level})}}

    # E1 и E2 несовместимы и летят вместе -> жёсткое нарушение
    hard = run([_rule("DO_NOT_FLY_WITH", "HIGH_HIGH")])
    assert hard.returncode == 2, hard.stdout + hard.stderr
    # включённое жёсткое правило, которого мы не проверяем -> неполнота
    incomplete = run([_rule("GENDER_ACCOMMODATION", "HIGH_HIGH")])
    assert incomplete.returncode == 3, incomplete.stdout + incomplete.stderr
    assert "ПОЛНОТА: INCOMPLETE" in incomplete.stdout
