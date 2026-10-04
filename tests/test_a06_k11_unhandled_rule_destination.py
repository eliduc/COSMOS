"""K11 coverage: unsupported scope stays a visible, non-certified obligation.

The actual accepted implementation emits an addressed not-run check instead
of sending the qualified rule to a model compiler. Production is unchanged.
"""
import ast
import copy
import inspect
import json
import os
from pathlib import Path
import textwrap

import pytest

from cdpm import catalog_bridge as cb
from template_catalog import family_contract, landing
from vmp.run import fit_checks as fc, roster_certify as rc
from webapp.threshold_gate import projected_params


@pytest.fixture(autouse=True)
def optional_destination_mutation(monkeypatch):
    mode = os.environ.get("CDPM_K11_DESTINATION_MUTANT")
    if not mode:
        return
    assert mode == "drop_not_run_check"
    source = textwrap.dedent(inspect.getsource(fc.fit_code_checks))
    tree = ast.parse(source)
    handlers = [node for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler)
                and "параметры-посадки" in ast.get_source_segment(source, node)
                and "taken.add(rid)" in ast.get_source_segment(source, node)]
    assert len(handlers) == 1, "preflight handler changed: do not guess"
    handler = handlers[0]
    targets = [node for node in handler.body if isinstance(node, ast.Expr)
               and ast.unparse(node) == "checks.append(check)"]
    assert len(targets) == 1
    # Remove only publication of the explicit diagnostic. The actual strict
    # reader, failure, taken.add, continuation and downstream verdict survive.
    handler.body.remove(targets[0])
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "_fit_records" for node in ast.walk(tree))
    ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, "<K11-drop-not-run-check>", "exec"), fc.__dict__, namespace)
    monkeypatch.setattr(fc, "fit_code_checks", namespace["fit_code_checks"])
    print("K11_DESTINATION_MUTANT: removed only preflight checks.append(check)")


@pytest.fixture
def catalogue(monkeypatch):
    folder = Path(__file__).resolve().parents[1] / "catalog-integration/0.7.0-draft.2"
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cb.forget()
    try:
        yield cb.catalog()
    finally:
        cb.forget()


def rule(cat, rid, qualifier=None):
    cells = {"min_turnaround": {
        "value": 45, "unit": "duration_minutes", "relation": "min",
        "condition": None if qualifier is None else {"kind": "area", "value": qualifier},
        "quote": "45 минут", "source": {"kind": "rule", "rule_id": rid},
    }}
    result = {"id": rid, "kind": "HARD", "zone": "BOTH", "title": rid,
              "statement": "Минимальная стыковка 45 минут.",
              "template_fit": {"schema_version": "landing/2", "category": "A",
                  "family": "connection_windows", "params": cells,
                  "registry_rev": cat.fingerprint(),
                  "family_contract": family_contract.snapshot(cat, "connection_windows", cells)}}
    assert landing.problems(result, cat) == []
    records = projected_params(result)
    assert len(records) == 1 and records[0]["value"] == 45
    assert records[0].get("qualifier") == qualifier
    assert fc.fit_param(result, "min_turnaround") == (45 if qualifier is None else None)
    return result


def solution(gap=60):
    assert gap in (30, 60)
    return {"solution": "K11 local destination witness", "assignments": [
        {"employee": "E1", "flight": "F1", "date": "2026-09-19", "duty_id": "D1",
         "dep_time": "2026-09-19T06:00", "arr_time": "2026-09-19T08:00"},
        {"employee": "E1", "flight": "F2", "date": "2026-09-19", "duty_id": "D1",
         "dep_time": "2026-09-19T08:30" if gap == 30 else "2026-09-19T09:00",
         "arr_time": "2026-09-19T10:30"},
    ]}


def svod(*rules):
    return {"meta": {"customer": "K11 local witness"}, "rules": list(rules),
            "questions": [], "objectives": []}


def forbidden_compiler(_prompt):
    raise AssertionError("these addressed deterministic checks must not call a model compiler")


def assert_not_run_check(check, rid):
    assert check["basis"] == {"rule": rid}
    assert check["template"] == "параметры-посадки"
    assert check["compiled_by"] == fc.COMPILED_BY
    assert check["status"] == "не прогнана"
    assert check["rows_checked"] == 0
    assert "уточнитель" in check["reason"]
    assert check["params"] == {}


@pytest.mark.parametrize("gap", [30, 60])
def test_unconditional_rule_reaches_real_turnaround_check(catalogue, gap):
    current = svod(rule(catalogue, "R-CONTROL"))
    data = rc.build_solution_dataset(solution(gap))
    before = copy.deepcopy((current, data))
    specs, checks, findings, remaining = fc.fit_code_checks(current, data)
    assert specs == [] and remaining["rules"] == []
    assert len(checks) == 1
    assert checks[0]["template"] == "минимальная-стыковка-в-смене"
    assert checks[0]["basis"] == {"rule": "R-CONTROL"}
    assert checks[0]["status"] == "прогнана" and checks[0]["rows_checked"] == 2
    assert checks[0]["params"]["min_minutes"] == 45
    assert len(findings) == (1 if gap == 30 else 0)
    if findings:
        assert findings[0]["rule_refs"] == ["R-CONTROL"]
        assert findings[0]["target"]["row"] == 3
        assert "30 мин" in findings[0]["text"]
    assert (current, data) == before


@pytest.mark.parametrize("qualifier", ["база", "вне базы"])
def test_qualified_rule_remains_visible_as_addressed_not_run(catalogue, qualifier):
    current = svod(rule(catalogue, "R-QUALIFIED", qualifier))
    data = rc.build_solution_dataset(solution())
    before = copy.deepcopy((current, data))
    specs, checks, findings, remaining = fc.fit_code_checks(current, data)
    assert specs == [] and findings == []
    assert remaining["rules"] == []  # no unsafe model reinterpretation fallback
    assert len(checks) == 1, "the rejected obligation must not disappear"
    assert_not_run_check(checks[0], "R-QUALIFIED")
    assert (current, data) == before


@pytest.mark.parametrize("gap", [30, 60])
def test_unconditional_downstream_certificate_has_a_real_healthy_control(catalogue, gap):
    current = svod(rule(catalogue, "R-CONTROL"))
    report = rc.certify(current, solution(gap), forbidden_compiler)
    cert = report["certificate"]
    assert cert["checks_run"] == 1 and cert["checks_not_run"] == 0
    assert cert["rules_uncovered"] == []
    assert cert["certified"] is (gap == 60)
    assert cert["result"]["feasibility"] == ("confirmed" if gap == 60 else "violated")
    assert cert["counted_share"]["rules_counted"] == ["R-CONTROL"]


@pytest.mark.parametrize("qualifier", ["база", "вне базы"])
def test_qualified_obligation_blocks_certificate_despite_executed_healthy_control(catalogue, qualifier):
    current = svod(rule(catalogue, "R-CONTROL"), rule(catalogue, "R-QUALIFIED", qualifier))
    before = copy.deepcopy(current)
    report = rc.certify(current, solution(), forbidden_compiler)
    cert = report["certificate"]
    print(json.dumps({"qualifier": qualifier,
                      "checks": [{"rule": c["basis"]["rule"], "status": c["status"],
                                  "reason": c.get("reason")} for c in report["checks"]],
                      "certified": cert["certified"],
                      "uncovered": cert["rules_uncovered"],
                      "feasibility": cert["result"]["feasibility"]}, ensure_ascii=False))
    checks = [c for c in report["checks"] if c["basis"]["rule"] == "R-QUALIFIED"]
    assert len(checks) == 1, "the not-run diagnostic must reach the actual report"
    assert_not_run_check(checks[0], "R-QUALIFIED")
    assert cert["checks_run"] == 1 and cert["checks_not_run"] == 1
    assert cert["findings"] == 0 and cert["violated_rules"] == []
    assert cert["rules_uncovered"] == ["R-QUALIFIED"]
    assert cert["certified"] is False
    assert cert["result"]["feasibility"] == "not_established"
    assert cert["result"]["coverage_complete"] is False
    assert cert["result"]["uncovered"] == ["R-QUALIFIED"]
    assert cert["counted_share"]["rules_counted"] == ["R-CONTROL"]
    assert cert["counted_share"]["rules_uncounted"] == ["R-QUALIFIED"]
    assert current == before
