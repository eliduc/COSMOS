"""Actual Python certificate path; independent literal duration/scope oracles.

No gate, compiler verdict or solver is replaced. The existing legacy A/B fit
shape is a certifier input, not proof of a modern package/release acceptance.
"""
from copy import deepcopy

import pytest

from vmp.run import roster_certify as rc
from cdpm.gates import gate_certify
from test_run_certify_caps import _rule


MISSING = object()


def policy(rid="R-EXT", cap=780, **conditions):
    return _rule(rid, "duty_extension_penalty",
                 dict(long_duty_threshold=720, duty_extension_cap=cap, **conditions),
                 kind="SOFT")


def solution(*facts):
    out = {"solution": "synthetic exact duties", "assignments": [], "duties": []}
    for index, (base, end, consent) in enumerate(facts, 1):
        employee = f"E{index}"
        out["assignments"].append({"employee": employee, "flight": f"F{index}",
            "dep_time": "2026-03-02T06:00", "arr_time": end,
            "dep_airport": base, "arr_airport": base, "base": base,
            "duty_id": f"D{index}"})
        duty = {"employee": employee, "id": f"D{index}", "base": base,
                "start": "2026-03-02T06:00", "end": end}
        if consent is not MISSING:
            duty["extension_consent"] = consent
        out["duties"].append(duty)
    return out


def certify(policies, sol):
    def no_compiler(_prompt):
        raise AssertionError("all HARD checks must be compiled by actual fit_code_checks")

    svod = {"meta": {"customer": "unit", "mode": 1, "cdpm_version": "0.1"},
            "sources": [{"sid": "S1", "file": "unit.md", "sha256": "0" * 64}],
            "rules": [_rule("R-LIM", "duty_limits", {"max_duty_duration": 720}),
                      *policies], "questions": [], "objectives": []}
    report = rc.certify(svod, deepcopy(sol), no_compiler)
    # Contract 3.3 judges the real public feasibility projection, not SOFT
    # cost measurements (which remain in the original returned report).
    view = rc.feasibility_view(report)
    assert rc.dr.validate_report(view, svod) == []
    assert gate_certify.check(svod, view)[0] == []
    check = next(c for c in report["checks"] if c.get("basis", {}).get("rule") == "R-LIM")
    hard = [f for f in report["findings"] if f["class"] == "нарушение правила"]
    return report, check, hard


@pytest.mark.parametrize("requirement", [MISSING, False])
def test_absent_or_false_requirement_needs_no_actual_consent(requirement):
    conditions = {} if requirement is MISSING else {"extension_consent": requirement}
    report, check, hard = certify([policy(**conditions)], solution(
        *(('AAA', "2026-03-02T18:40", value) for value in (True, False, None, MISSING))))
    assert check["status"] == "прогнана" and check["rows_checked"] == 4
    assert hard == []
    assert report["certificate"]["result"]["feasibility"] == "confirmed"


def test_required_consent_uses_explicit_true_other_duties_keep_base_check():
    report, check, hard = certify([policy(extension_consent=True)], solution(
        *(('AAA', "2026-03-02T18:40", value) for value in (True, False, None, MISSING))))
    assert check["status"] == "прогнана" and check["rows_checked"] == 4
    assert [f["target"]["row"] for f in hard] == [3, 4, 5]
    assert all("базовый предел" in f["text"] for f in hard)
    assert report["certificate"]["violated_rules"] == ["R-LIM"]


def test_all_absent_required_consent_does_not_remove_base_hard():
    report, check, hard = certify([policy(extension_consent=True)],
        solution(("AAA", "2026-03-02T18:40", MISSING)))
    assert check["status"] == "прогнана" and check["rows_checked"] == 1
    assert len(hard) == 1 and "12 ч" in hard[0]["text"]
    assert report["certificate"]["result"]["feasibility"] == "violated"


def test_unknown_consent_keeps_base_even_when_that_policy_base_is_unknown():
    sol = solution(("AAA", "2026-03-02T18:40", MISSING))
    sol["duties"][0].pop("base")
    report, check, hard = certify([policy(extension_bases=["AAA"], extension_consent=True)], sol)
    assert check["status"] == "прогнана" and len(hard) == 1
    assert "базовый предел" in hard[0]["text"]
    assert report["certificate"]["result"]["feasibility"] == "violated"


@pytest.mark.parametrize("consent,end,violates", [
    (MISSING, "2026-03-02T18:00", False),
    (MISSING, "2026-03-02T18:01", True),
    (True, "2026-03-02T19:00", False),
    (True, "2026-03-02T19:01", True),
])
def test_base_and_authorized_cap_boundaries(consent, end, violates):
    report, check, hard = certify([policy(extension_consent=True)],
                                  solution(("AAA", end, consent)))
    assert check["status"] == "прогнана"
    assert len(hard) == (1 if violates else 0)
    assert report["certificate"]["result"]["feasibility"] == ("violated" if violates else "confirmed")


def test_unknown_facts_are_not_materialized_as_explicit_no_and_loss_is_detected():
    sol = solution(*(('AAA', "2026-03-02T18:00", value)
                     for value in (True, False, None, MISSING)))
    sol["assignments"][0]["deadhead"] = True
    ds = rc.build_solution_dataset(sol)
    assert sol["duties"][2]["extension_consent"] is None
    assert "extension_consent" not in sol["duties"][3]
    table = ds["tables"][rc.SOLUTION_SID]["Смены"]
    assert [r.get("Согласие на продление", "") for r in table["rows"]] == ["да", "нет", "", ""]
    assert [r["Перегон"] for r in ds["tables"][rc.SOLUTION_SID]["Назначения"]["rows"]] == ["да", "нет", "нет", "нет"]
    rc.verify_input_completeness(sol, ds)
    lost = deepcopy(ds)
    lost["tables"][rc.SOLUTION_SID]["Смены"]["rows"][1]["Согласие на продление"] = ""
    with pytest.raises(rc.CertifyError, match="Согласие на продление"):
        rc.verify_input_completeness(sol, lost)


@pytest.mark.parametrize("reverse", [False, True])
def test_multiple_disjoint_policies_preserve_scopes_and_ids(reverse):
    policies = [policy("R-AAA", 780, extension_bases=["AAA"]),
                policy("R-BBB", 840, extension_bases=["BBB"])]
    if reverse:
        policies.reverse()
    report, check, hard = certify(policies, solution(
        ("AAA", "2026-03-02T18:40", MISSING),
        ("BBB", "2026-03-02T19:20", MISSING),
        ("CCC", "2026-03-02T18:40", MISSING)))
    assert check["status"] == "прогнана"
    assert [f["target"]["row"] for f in hard] == [4]
    assert {p["rule"] for p in check["params"]["cap_policies"]} == {"R-AAA", "R-BBB"}
    assert report["certificate"]["result"]["feasibility"] == "violated"


@pytest.mark.parametrize("second_cap", [780, 840])
@pytest.mark.parametrize("reverse", [False, True])
def test_overlapping_equal_caps_are_compatible_conflicting_caps_are_not_selected(second_cap, reverse):
    policies = [policy("R-ALL", 780), policy("R-AAA", second_cap, extension_bases="AAA")]
    if reverse:
        policies.reverse()
    report, check, hard = certify(policies, solution(("AAA", "2026-03-02T18:40", MISSING)))
    if second_cap == 780:
        assert check["status"] == "прогнана" and hard == []
        assert {p["rule"] for p in check["params"]["cap_policies"]} == {"R-ALL", "R-AAA"}
        assert report["certificate"]["result"]["feasibility"] == "confirmed"
    else:
        assert check["status"] == "не прогнана"
        assert all(s in check["reason"] for s in ("R-ALL", "R-AAA", "780", "840", "строка 2"))
        assert report["certificate"]["result"]["feasibility"] == "not_established"


def test_nonapplicable_consent_policy_does_not_create_ambiguity():
    report, check, hard = certify([policy("R-FREE", 780),
        policy("R-YES", 840, extension_consent=True)],
        solution(("AAA", "2026-03-02T19:20", False)))
    assert check["status"] == "прогнана"
    assert len(hard) == 1 and "13 ч" in hard[0]["text"]
    assert report["certificate"]["result"]["feasibility"] == "violated"


@pytest.mark.parametrize("excluded", ["OUT", "excluded_until_data"])
def test_inactive_policy_does_not_raise_hard_limit(excluded):
    cap = policy()
    if excluded == "OUT":
        cap["kind"] = "OUT"
    else:
        cap[excluded] = {"question": "Q-1", "revision": 1}
    report, check, hard = certify([cap], solution(("AAA", "2026-03-02T18:40", True)))
    assert check["status"] == "прогнана" and len(hard) == 1
    assert report["certificate"]["result"]["feasibility"] == "violated"


@pytest.mark.parametrize("name,cell", [
    ("extension_consent", {"value": None}),
    ("extension_consent", {"value": "false"}),
    ("extension_consent", {"value": False, "pending_revision": "Q-1"}),
    ("extension_bases", {"value": None}),
    ("extension_bases", {"value": []}),
    ("extension_bases", {"value": ["AAA", 1]}),
    ("extension_bases", {"value": "AAA", "pending_revision": "Q-1"}),
])
def test_present_malformed_or_pending_policy_is_addressed_refusal(name, cell):
    cap = policy()
    cap["template_fit"]["params"][name] = cell
    report, check, _hard = certify([cap], solution(("AAA", "2026-03-02T18:40", False)))
    assert check["status"] == "не прогнана"
    assert "R-EXT" in check["reason"] and name in check["reason"]
    assert report["certificate"]["result"]["feasibility"] == "not_established"


@pytest.mark.parametrize("value", [1, "pending", {"value": True}])
def test_malformed_actual_consent_is_not_coerced(value):
    with pytest.raises(rc.CertifyError, match="extension_consent"):
        rc.build_solution_dataset(solution(("AAA", "2026-03-02T18:40", value)))
