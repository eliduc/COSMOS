"""Offline v2 metric protocol and actual rate-carrier tests, never run evidence.

The JVM measurements here are explicitly fabricated TEST FIXTURES behind the
same mock native-reader seam as test_qualification_run_gpt. No JVM is invoked.
Only the separate carrier/projection tests exercise actual Python serialization.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from cdpm import component_input as ci
from vmp import kotlin_tables as kt, objective_projection as op
from vmp import qualification_run as qr, stand_adapters as sa, stand_solver as ss, stand_tasks as st
from test_qualification_run_gpt import (
    COMMIT, FakeCatalog, ProtocolFixture, digest, encoded, offline_only,
)

RECORD = qr.METRIC_RECORD
POSITIVE, HEALTHY = qr.PAIRS[RECORD]
CONSTRAINT = "Credit hours spread"
PENALTY = "Credit hours spread over threshold"
RATE = {"value": 1, "as_written": "ставка 1 условная единица за час разброса кредитных часов",
        "per": "час разброса кредитных часов"}


class MetricCatalog(FakeCatalog):
    def __init__(self):
        super().__init__()
        self.rows = [
            {"id": "balanced_workload", "kind": "SOFT", "zone": "ROSTERING",
             "params": [{"name": "workload_spread_threshold"}]},
            {"id": "rest_floors", "kind": "HARD", "zone": "ROSTERING",
             "params": [{"name": "min_rest_home"}, {"name": "min_rest_outstation"}]},
        ]
        world = {"form": "schedule", "objective_rate": copy.deepcopy(RATE),
            "params": {"workload_spread_threshold": 5, "min_rest_home": 660, "min_rest_outstation": 700},
            "crews": [{"id": name, "base": "ST1", "rank": "CP", "ratings": ["T1"]} for name in ("P1", "P2")],
            "flights": [
                {"id": "F1", "from": "ST1", "to": "ST2", "fleet_type": "T1",
                 "dep": "2026-10-01T06:00", "arr": "2026-10-01T11:00"},
                {"id": "F2", "from": "ST2", "to": "ST1", "fleet_type": "T1",
                 "dep": "2026-10-02T06:00", "arr": "2026-10-02T11:00"}],
            "assignments": [{"flight": flight, "crew": "P1", "seat": 0} for flight in ("F1", "F2")]}
        healthy = copy.deepcopy(world)
        healthy["assignments"][1]["crew"] = "P2"
        self.metric = {"id": qr.METRIC, "zone": "ROSTERING", "direction": "minimize",
            "title": "Mock metric protocol fixture", "stand_tasks": [
                {"id": POSITIVE, "world": world, "expected": {"kotlin_metric": {"value": 7, "matches": 1, "refusal": False}}},
                {"id": HEALTHY, "world": healthy, "expected": {"kotlin_metric": {"value": 0, "matches": 1, "refusal": False}}}]}

    def criteria(self):
        return {qr.METRIC: copy.deepcopy(self.metric)}


class MetricFixture(ProtocolFixture):
    def __init__(self, root, monkeypatch):
        super().__init__(root, monkeypatch, catalog=MetricCatalog(), record=RECORD)
        for case in self.request["cases"]:
            task_id = case["task"]
            task = next(t for t in self.catalog.metric["stand_tasks"] if t["id"] == task_id)
            world = task["world"]
            case.update(executor_kind="kotlin_metric", mode="optimization", objective_id=qr.OBJECTIVE_ID)
            native = next(value for value in self.native.values() if value["manifest"]["task"]["id"] == task_id)
            objective = ss.objective_for(self.catalog.metric, world=world)
            native["files"]["objective.json"] = encoded({"pairing": {"objectives": [], "components": []},
                "rostering": {"objectives": [objective], "components": [
                    {"role": "soft_penalty", "family": "balanced_workload", "active": True, "weight": 1}]}})
            native["files"]["threshold-forms.json"] = b"{}"
            native["files"]["model.json"] = encoded({"rules": [
                {"id": "R-STAND-balanced_workload", "template_fit": {"params": {"workload_spread_threshold": {"value": 5}}}},
                {"id": "R-STAND-rest_floors", "template_fit": {"params": {
                    "min_rest_home": {"value": 660}, "min_rest_outstation": {"value": 700}}}}]})
            context = self.contents[task_id]["execution_context"]
            context.update(mode="optimization", calendar_rules=[], applied_weights={PENALTY: 1},
                effective_weights={**dict.fromkeys(qr._default_soft_literals(), 0), PENALTY: 1, CONSTRAINT: 1},
                literals_disabled=sorted(qr._default_soft_literals() - {PENALTY, CONSTRAINT}))
            context["coverage"] = {"qualifications": {"employeesTotal": 2}}
            context["fit"]["registered"] = [CONSTRAINT, PENALTY]
            context["provider_readings"] = {}
            context["applied"]["applied"] = []
            for name, threshold, factor, family in (
                ("workload_spread_threshold", "workload_spread_threshold_min", 60, "balanced_workload"),
                ("min_rest_home", "min_rest_home_min", 1, "rest_floors"),
                ("min_rest_outstation", "min_rest_outstation_min", 1, "rest_floors")):
                reading = world["params"][name] * factor
                context["provider_readings"][threshold] = reading
                context["applied"]["applied"].append({"threshold": threshold, "value": reading,
                    "executor": "kotlin-provider", "literal": 0, "fromParams": [name],
                    "rules": ["R-STAND-" + family], "declarations": [], "codes": [], "dutyExtensions": []})
            context["objective"] = {"id": qr.OBJECTIVE_ID, "title": objective["title"], "metric": qr.METRIC,
                "direction": "minimize", "constraint": CONSTRAINT, "executorSource": "словарь метрик §5а",
                "weight": 1, "rate": 1,
                "weightSource": "objective.json: rostering.objectives[" + qr.OBJECTIVE_ID + "].rate.value",
                "approximation": kt.metrics()[qr.METRIC]["approximation"], "sharesConstraintWithPenalty": False,
                "criterion": "целевая + Σ штрафов мягких задачи (direction=minimize)"}
            problem = json.loads(native["files"]["problem.json"])
            solution = copy.deepcopy(problem)
            solution["flightAssignments"][1]["employee"] = "P2"
            solution["score"] = "0hard/0soft"
            self.contents[task_id]["solution"] = solution
            row = self.row(task_id)
            row["execution_context"] = self.write_ref(task_id, "execution_context")
            row["solution"] = self.write_ref(task_id, "solution")
            for name, subject in (("declared_world_breakdown", digest(native["files"]["problem.json"])),
                                  ("solved_solution_breakdown", row["solution"]["sha256"])):
                measured = name == "declared_world_breakdown" and task_id == POSITIVE
                # Explicit mock protocol values, not a solver measurement.
                values = {CONSTRAINT: 7 if measured else 0, PENALTY: 2 if measured else 0}
                total = -sum(values.values())
                self.contents[task_id][name] = {
                    "execution_context_sha256": row["execution_context"]["sha256"], "score_subject_sha256": subject,
                    "breakdown": {"score": f"0hard/{total}soft", "hard": 0, "soft": total,
                        "packageFingerprint": row["input_fingerprint"], "generated": [],
                        "constraints": {key: {"score": f"0hard/{-value}soft", "hard": 0,
                            "soft": -value, "matches": 1, "weight": "0hard/-1soft"} for key, value in values.items()}}}
                row[name] = self.write_ref(task_id, name)
        self.save_request()
        self.save()


@pytest.fixture
def metric_bundle(tmp_path, monkeypatch):
    return MetricFixture(tmp_path, monkeypatch)


def test_v2_metric_uses_declared_world_not_solved_score_or_balanced_penalty(metric_bundle):
    evidence = metric_bundle.load()
    for task, value in zip(metric_bundle.catalog.metric["stand_tasks"], (7, 0)):
        result = evidence.judge("kotlin_metric", task["expected"]["kotlin_metric"], RECORD, task["id"], task["world"])
        assert result["got"] == {"value": value, "matches": 1, "refusal": False}
    positive = metric_bundle.contents[POSITIVE]
    assert positive["declared_world_breakdown"]["breakdown"]["soft"] == -9
    assert positive["solved_solution_breakdown"]["breakdown"]["soft"] == 0
    assert qr.MAX_CASES == 16 and len(qr.PAIRS) == 8
    assert qr.PAIRS["families/crew_unavailability"] == (
        "st-unavail-native-1", "st-unavail-native-healthy-1")
    assert "vmp/kotlin_tables.py" in qr.PYTHON_SOURCES


@pytest.mark.parametrize("field,value", [("executor_kind", "kotlin_constraint"), ("mode", "feasibility-soft"),
    ("objective_id", None), ("objective_id", "other")])
def test_metric_request_cannot_change_executor_mode_or_selected_objective(metric_bundle, field, value):
    metric_bundle.request["cases"][0][field] = value
    metric_bundle.save_request()
    metric_bundle.save()
    with pytest.raises(qr.QualificationError):
        metric_bundle.load()


@pytest.mark.parametrize("kind", ["request", "output"])
def test_v1_is_not_silently_promoted_to_metric_authority(metric_bundle, kind):
    if kind == "request":
        metric_bundle.request["schema"] = "a06-qualification-request/1"
        metric_bundle.save_request()
    else:
        metric_bundle.doc["schema"] = "a06-kotlin-qualification-run/1"
    metric_bundle.save()
    with pytest.raises(qr.QualificationError):
        metric_bundle.load()


@pytest.mark.parametrize("field,value", [("id", "other"), ("title", "foreign"), ("metric", "balanced_workload"),
    ("direction", "maximize"), ("constraint", PENALTY), ("executorSource", "score-only"), ("weight", 2),
    ("rate", None), ("rate", True), ("weightSource", "legacy: unit rate"), ("approximation", None),
    ("sharesConstraintWithPenalty", True), ("criterion", "penalty only")])
def test_resealed_resolved_objective_is_bound_to_native_data_and_actual_executor(metric_bundle, field, value):
    metric_bundle.contents[POSITIVE]["execution_context"]["objective"][field] = value
    metric_bundle.reseal("execution_context", task=POSITIVE)
    with pytest.raises(qr.QualificationError):
        metric_bundle.load()


@pytest.mark.parametrize("change", ["objective_missing", "mode", "effective_missing", "effective_wrong",
    "penalty_only", "metric_disabled", "rate_missing", "rate_changed", "second_objective", "pairing_objective",
    "omit_disabled", "foreign_disabled"])
def test_metric_context_and_native_carrier_cannot_be_substituted(metric_bundle, change):
    ctx = metric_bundle.contents[POSITIVE]["execution_context"]
    native = next(value for value in metric_bundle.native.values() if value["manifest"]["task"]["id"] == POSITIVE)
    objective = json.loads(native["files"]["objective.json"])
    if change == "objective_missing":
        ctx["objective"] = None
    elif change == "mode":
        ctx["mode"] = "feasibility-soft"
    elif change == "effective_missing":
        del ctx["effective_weights"][CONSTRAINT]
    elif change == "effective_wrong":
        ctx["effective_weights"][CONSTRAINT] = 2
    elif change == "penalty_only":
        ctx["objective"]["constraint"] = PENALTY
    elif change == "metric_disabled":
        ctx["effective_weights"][CONSTRAINT] = 0
        ctx["literals_disabled"] = [CONSTRAINT]
    elif change == "rate_missing":
        objective["rostering"]["objectives"][0].pop("rate")
    elif change == "rate_changed":
        objective["rostering"]["objectives"][0]["rate"]["value"] = 2
    elif change == "second_objective":
        objective["rostering"]["objectives"].append(copy.deepcopy(objective["rostering"]["objectives"][0]))
    elif change == "pairing_objective":
        objective["pairing"]["objectives"] = copy.deepcopy(objective["rostering"]["objectives"])
    elif change == "omit_disabled":
        name = ctx["literals_disabled"].pop()
        del ctx["effective_weights"][name]
    elif change == "foreign_disabled":
        ctx["literals_disabled"].append("undeclared literal")
        ctx["effective_weights"]["undeclared literal"] = 0
    native["files"]["objective.json"] = encoded(objective)
    metric_bundle.reseal("execution_context", task=POSITIVE)
    with pytest.raises(qr.QualificationError):
        metric_bundle.load()


def test_resealed_score_and_weight_cannot_hide_a_different_actual_rate(metric_bundle):
    body = metric_bundle.contents[POSITIVE]["declared_world_breakdown"]["breakdown"]
    body["constraints"][CONSTRAINT].update(weight="0hard/-2soft", score="0hard/-14soft", soft=-14)
    body.update(score="0hard/-16soft", soft=-16)
    metric_bundle.reseal("declared_world_breakdown", task=POSITIVE)
    with pytest.raises(qr.QualificationError, match="weight differs"):
        metric_bundle.load()


@pytest.mark.parametrize("name", [CONSTRAINT, PENALTY])
@pytest.mark.parametrize("subject", ["declared_world_breakdown", "solved_solution_breakdown"])
def test_resealed_positive_weight_cannot_replace_penalize_weight(metric_bundle, name, subject):
    body = metric_bundle.contents[POSITIVE][subject]["breakdown"]
    body["constraints"][name]["weight"] = "0hard/1soft"
    metric_bundle.reseal(subject, task=POSITIVE)
    with pytest.raises(qr.QualificationError, match="weight differs"):
        metric_bundle.load()


def test_resealed_metric_reward_cannot_masquerade_as_minimizing_penalty(metric_bundle):
    body = metric_bundle.contents[POSITIVE]["declared_world_breakdown"]["breakdown"]
    body["constraints"][CONSTRAINT].update(score="0hard/7soft", soft=7)
    body.update(score="0hard/5soft", soft=5)
    metric_bundle.reseal("declared_world_breakdown", task=POSITIVE)
    with pytest.raises(qr.QualificationError, match="non-penalty"):
        metric_bundle.load()


def test_metric_diagnostic_mismatch_is_preserved_and_fails_oracle(metric_bundle):
    body = metric_bundle.contents[POSITIVE]["declared_world_breakdown"]["breakdown"]
    body["constraints"][CONSTRAINT].update(score="0hard/-6soft", soft=-6)
    body.update(score="0hard/-8soft", soft=-8)
    metric_bundle.reseal("declared_world_breakdown", task=POSITIVE)
    evidence = metric_bundle.load()
    task = metric_bundle.catalog.metric["stand_tasks"][0]
    with pytest.raises(qr.QualificationError, match="differs from oracle"):
        evidence.judge("kotlin_metric", task["expected"]["kotlin_metric"], RECORD, POSITIVE, task["world"])


def test_metric_stand_proof_remains_explicit_local_and_kind_bound(metric_bundle):
    evidence = metric_bundle.load()
    rows = st.run_tasks(catalog=metric_bundle.catalog, kinds=["kotlin_metric"], qualification_evidence=evidence)
    assert len(rows) == 2 and all(row["result"] == "passed" and row["proof_source"] == qr.PROOF_SOURCE for row in rows)
    doc = st.results_document(rows, metric_bundle.catalog, commit=COMMIT, repo=metric_bundle.repo,
                              qualification_reference=evidence.reference)
    assert st.stale_reasons(doc, metric_bundle.catalog, repo=metric_bundle.repo) == []
    assert all(row["run_id"] is None and row["package_fingerprint"] is None for row in rows)
    rows[0]["executor_kind"] = "kotlin_constraint"
    assert st.stale_reasons(doc, metric_bundle.catalog, repo=metric_bundle.repo)


def test_no_metric_measurement_never_becomes_automatic_pass(metric_bundle):
    rows = st.run_tasks(catalog=metric_bundle.catalog, kinds=["kotlin_metric"])
    assert len(rows) == 2 and all(row["result"] == "skipped" and row["got"] is None for row in rows)
    from vmp import stand_components
    assert "kotlin_metric" not in stand_components.KINDS


def test_ordinary_metric_http_dispatch_and_identity_are_unchanged(metric_bundle, monkeypatch):
    calls, solver = [], object()
    def normal(kind, ctx, gate):
        calls.append((kind, gate))
        return st.Outcome("passed", ctx["expected"], "MOCK ordinary HTTP dispatch; no request")
    monkeypatch.setattr(st, "_judge_with_solver", normal)
    task = metric_bundle.catalog.metric["stand_tasks"][0]
    ctx = {"world": task["world"], "expected": task["expected"]["kotlin_metric"]}
    assert st._kind_outcome("kotlin_metric", ctx, solver=solver).result == "passed"
    assert calls == [("kotlin_metric", solver)] and "proof_source" not in ctx
    legacy = {"solver": {"url": "http://offline.invalid"}, "results": [{"record": RECORD, "task": POSITIVE,
        "executor_kind": "kotlin_metric", "result": "passed", "run_id": "normal-http", "package_fingerprint": "f" * 64}]}
    assert st._qualification_stale_reasons(legacy, metric_bundle.catalog, metric_bundle.repo) == []
    with pytest.raises(ValueError, match="separate proof sources"):
        st.run_tasks(catalog=metric_bundle.catalog, solver_url="http://forbidden.invalid",
                     qualification_evidence=metric_bundle.load())


@pytest.mark.parametrize("matches", [0, 2, True])
def test_healthy_metric_aggregate_count_is_not_invented_or_generalized(metric_bundle, matches):
    metric_bundle.catalog.metric["stand_tasks"][1]["expected"]["kotlin_metric"]["matches"] = matches
    with pytest.raises(qr.QualificationError):
        metric_bundle.load()


@pytest.mark.parametrize("rate", [None, {}, {"value": 1}, {**RATE, "value": True}, {**RATE, "value": 0},
    {**RATE, "value": 2}, {**RATE, "value": 0.5}, {**RATE, "value": "1"}, {**RATE, "per": ""}])
def test_metric_requires_explicit_reviewed_declared_rate(metric_bundle, rate):
    metric_bundle.catalog.metric["stand_tasks"][0]["world"]["objective_rate"] = rate
    with pytest.raises(qr.QualificationError):
        metric_bundle.load()


def test_rate_carrier_is_lossless_and_never_defaulted_or_oracle_driven():
    catalog = MetricCatalog()
    world = catalog.metric["stand_tasks"][0]["world"]
    original = copy.deepcopy((catalog.metric, world))
    result = ss.objective_for(catalog.metric, world=world)
    assert result["rate"] == RATE
    result["rate"]["value"] = 99
    assert (catalog.metric, world) == original
    without = copy.deepcopy(world)
    without.pop("objective_rate")
    assert "rate" not in ss.objective_for(catalog.metric, world=without)
    catalog.metric["stand_tasks"][0]["expected"]["kotlin_metric"]["value"] = 999
    assert ss.objective_for(catalog.metric, world=world)["rate"] == RATE


@pytest.mark.parametrize("rate", [None, {}, {**RATE, "value": 0.5}, {**RATE, "value": 0}, {**RATE, "value": "1"}])
def test_carrier_does_not_repair_invalid_data_before_normal_validation(rate):
    catalog = MetricCatalog()
    world = copy.deepcopy(catalog.metric["stand_tasks"][0]["world"])
    world["objective_rate"] = copy.deepcopy(rate)
    assert ss.objective_for(catalog.metric, world=world)["rate"] == rate


def test_actual_native_model_source_and_objective_projection_keep_full_rate():
    # Read-only catalogue vocabulary, actual Python source/model/projection;
    # this is not a native ZIP, SMT verdict, executor measurement or support.
    import template_catalog as tc
    catalog = tc.load(Path(__file__).resolve().parents[1] / "catalog-integration/0.8.0-draft.3")
    record = catalog.criteria()[qr.METRIC]
    world = MetricCatalog().metric["stand_tasks"][0]["world"]
    objective = ss.objective_for(record, world=world)
    meta = st._family_meta(catalog)
    rules = ss._rules_for(world, st.families_for(world, None, meta), meta)
    doc = ss.model_doc(rules, world=world, objective=objective, catalog=catalog)
    source = json.loads(ss._stand_source(world, rules, objective, catalog=catalog))
    projected = op.project(doc, catalog)
    assert doc["objectives"][0]["rate"] == projected["rostering"]["objectives"][0]["rate"] == RATE
    assert source["стенд"]["world"]["objective_rate"] == RATE
    assert source["стенд"]["objective"]["rate"] == RATE
