"""Offline K06 scope probes; synthetic evidence is not release approval.

Use only through the reviewed offline runner. No product verdict is mocked.
Each test proves the real catalogue/schema/model and a healthy support baseline
before withdrawing one approval in a temporary native support/1 file.
"""
import copy
import json
from pathlib import Path

import jsonschema  # Require the actual schema implementation, not S06 fallback.
import pytest

from cdpm import catalog_bridge as cb, model as m, support_manifest as sm
from cdpm import export_task_package as producer
from cdpm.gates import gate_task_package as gate
from template_catalog import bindings, landing
from vmp.version import PRODUCT_VERSION
from webapp import task_package as receiver
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge


ROOT = Path(__file__).resolve().parents[1]
FAMILY = "time_scale"
AT = "2026-09-16T00:00:00Z"


def healthy_model(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, FAMILY, {
        "base_tz_offset_min": {"value": 0, "unit": "duration_minutes",
            "relation": "eq", "condition": None, "quote": "0 минут"}})
    rule.pop("params", None)
    rule.update(kind="HARD", zone="BOTH", title="Шкала времени",
                statement="Сдвиг шкалы времени данных равен 0 минут.")
    doc["meta"]["fit_contract"] = True
    doc.update(rules=[rule], objectives=[], questions=[])
    cat = cb.catalog()
    assert cat.family(FAMILY)["zone"] == "BOTH"
    assert {"builder_method", "kotlin_knob"} <= set(cat.family(FAMILY)["executor_kinds"])
    assert not [p for p in cat.validate() if p.severity == "error"]
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []
    assert landing.problems(rule, cat) == []
    return doc, cat


def native_support(tmp_path, monkeypatch, cat, records):
    paths, raw_files = {}, []
    for product, relative, variable in (
            ("COSMOS", "vmp/bindings.json", "CREW_COSMOS_BINDINGS"),
            ("CDPM", "cdpm/bindings.json", "CDPM_BINDINGS_FILE")):
        raw = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        raw.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint())
        if product == "COSMOS":
            raw["product_version"] = PRODUCT_VERSION
        for row in raw["bindings"]:
            key = row["section"], row["id"]
            if key not in records:
                continue
            rec = cat.family(row["id"]) if row["section"] == "families" else cat.criteria()[row["id"]]
            task_id = rec["stand_tasks"][0]["id"]
            # Synthetic local outcomes use genuine declared task/kind identities.
            # No stand or executor runs, and production bindings are untouched.
            row["stand_verified"] = [{"executor_kind": kind, "task": task_id,
                "result": "passed", "commit": "a" * 40, "at": AT,
                "note": "Synthetic scope fixture only; no executor was run."}
                for kind in sorted({e["kind"] for e in row["executors"] if e["state"] == "есть"})]
        path = tmp_path / (product.lower() + "-scope-bindings.json")
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv(variable, str(path))
        paths[product] = path
        raw_files.append(raw)
    problems = bindings.check_bindings(cat, *raw_files)
    assert not [p for p in problems if p.severity == "error"], problems
    assert not [p for p in problems if p.code == "S06"], problems
    support = bindings.support_table(cat, paths, approved_by="isolated scope fixture",
        approved_how="synthetic test only; not a release", computed_at=AT)
    assert bindings.support_of(cat, support)["valid"]
    for record in records:
        rec = cat.family(record[1]) if record[0] == "families" else cat.criteria()[record[1]]
        for kind in rec.get("executor_kinds", rec.get("computable_by", [])):
            # For criteria only Kotlin metric is used by these rostering tests.
            if record[0] == "criteria" and kind != "kotlin_metric":
                continue
            own = [c for c in support["cells"] if (c["section"], c["record_id"], c["kind"]) == (*record, kind)]
            assert any(c["state"] == "есть" and c["approved"] for c in own), own
    return support


def publish(cat, doc, support, records):
    assert bindings.support_of(cat, support)["valid"]
    (cat.path / "support.json").write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")
    snapshot = copy.deepcopy(support)
    snapshot["cells"] = [c for c in snapshot["cells"] if (c["section"], c["record_id"]) in records]
    manifest = {"spec_version": "1.20", "catalog": copy.deepcopy(support["catalog"]),
                "support": snapshot}
    pkg = {"model": doc, "manifest": manifest}
    assert receiver.check_support_projection(pkg) == []
    return pkg


def withdraw(support, section, rid, kind):
    changed = []
    for cell in support["cells"]:
        if (cell["section"], cell["record_id"], cell["kind"]) == (section, rid, kind) and cell["approved"]:
            cell["approved"] = None
            changed.append(cell["product"])
    assert changed


def test_both_family_builder_approval_does_not_block_rostering(tmp_path, monkeypatch, record_property):
    doc, cat = healthy_model(tmp_path, monkeypatch)
    records = {("families", FAMILY)}
    support = native_support(tmp_path, monkeypatch, cat, records)
    pkg = publish(cat, doc, support, records)
    assert m.support_problems(doc, "1.20") == []
    for task in ("pairing", "rostering"):
        assert m.readiness_matrix(doc, "1.20")[task]["feasibility-hard"]["ready"]
        receiver.check_support_execution(pkg, task=task, mode="feasibility-hard")
    record_property("healthy_schema_model_catalogue_and_both_tasks", True)
    withdraw(support, "families", FAMILY, "builder_method")
    pkg = publish(cat, doc, support, records)
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    receiver.check_support_execution(pkg, task="rostering", mode="feasibility-hard")
    receiver_problems = sm.execution_problems(doc, pkg["manifest"], task="pairing",
        mode="feasibility-hard", product="COSMOS", product_version=PRODUCT_VERSION)
    assert receiver_problems and all(p["kind"] == "builder_method" for p in receiver_problems)
    matrix = m.readiness_matrix(doc, "1.20")
    modes = producer.allowed_modes(doc, "1.20")[0]
    observed = {"pairing_ready": matrix["pairing"]["feasibility-hard"]["ready"],
                "rostering_ready": matrix["rostering"]["feasibility-hard"]["ready"],
                "rostering_allowed": "feasibility-hard" in modes["rostering"],
                "barriers": m.support_problems(doc, "1.20")}
    record_property("after_builder_only_approval_withdrawal", json.dumps(observed, ensure_ascii=False))
    assert observed["rostering_ready"] and observed["rostering_allowed"], observed


def test_blocked_objective_does_not_become_execution_obligation(tmp_path, monkeypatch, record_property):
    doc, cat = healthy_model(tmp_path, monkeypatch)
    source = copy.deepcopy(doc["rules"][0]["sources"])
    qid = "Q-3b-GOAL"
    doc["questions"] = [{"id": qid, "part": "3b", "title": "Уточнение дополнительной целевой",
        "text": "Уточните дополнительную целевую о разбросе часов.",
        "answer_format": {"type": "registry"}, "rule_refs": [doc["rules"][0]["id"]],
        "source_refs": source}]
    doc["objectives"] = [
        {"id": "O-ACTIVE", "title": "Покрытие мест", "statement": "Минимум непокрытых мест",
         "zone": "ROSTERING", "direction": "minimize", "metric": "uncovered_seats", "sources": source},
        {"id": "O-BLOCKED", "title": "Разброс часов", "statement": "Минимум разброса кредитных часов",
         "zone": "ROSTERING", "direction": "minimize", "metric": "credit_hours_spread",
         "blockers": [qid], "sources": source}]
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    records = {("families", FAMILY), ("criteria", "uncovered_seats"), ("criteria", "credit_hours_spread")}
    support = native_support(tmp_path, monkeypatch, cat, records)
    pkg = publish(cat, doc, support, records)
    baseline = m.readiness_matrix(doc, "1.20")["rostering"]["optimization"]
    assert baseline["ready"] and baseline["objectives"] == ["O-ACTIVE"], baseline
    receiver.check_support_execution(pkg, task="rostering", mode="optimization")
    record_property("healthy_schema_model_catalogue_and_active_only_objective", True)
    withdraw(support, "criteria", "credit_hours_spread", "kotlin_metric")
    pkg = publish(cat, doc, support, records)
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    matrix = m.readiness_matrix(doc, "1.20")["rostering"]["optimization"]
    execution = sm.execution_problems(doc, pkg["manifest"], task="rostering",
        mode="optimization", product="COSMOS", product_version=PRODUCT_VERSION)
    declared = dict(pkg["manifest"], allowed_modes={"rostering": ["optimization"]})
    observed = {"ready": matrix["ready"], "active_objectives": matrix["objectives"],
                "execution_problems": execution, "producer_barriers": gate._check_barriers(doc, declared, {})}
    record_property("after_blocked_only_approval_withdrawal", json.dumps(observed, ensure_ascii=False))
    assert matrix["ready"] and not execution and not observed["producer_barriers"], observed


def roster_objectives(tmp_path, monkeypatch, *, same_metric=False):
    """Valid active and blocked goals; no product decisions are replaced."""
    doc, cat = healthy_model(tmp_path, monkeypatch)
    source = copy.deepcopy(doc["rules"][0]["sources"])
    doc["questions"] = [{"id": "Q-3b-GOAL", "part": "3b",
        "title": "Дополнительная целевая", "text": "Уточните дополнительную целевую.",
        "answer_format": {"type": "registry"},
        "rule_refs": [doc["rules"][0]["id"]], "source_refs": source}]
    doc["objectives"] = [
        {"id": "O-ACTIVE", "title": "Активная целевая", "statement": "Минимизировать метрику",
         "zone": "ROSTERING", "direction": "minimize", "sources": source,
         "metric": "credit_hours_spread" if same_metric else "uncovered_seats"},
        {"id": "O-BLOCKED", "title": "Дополнительная целевая", "statement": "Минимизировать разброс часов",
         "zone": "ROSTERING", "direction": "minimize", "sources": source,
         "metric": "credit_hours_spread", "blockers": ["Q-3b-GOAL"]}]
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    records = {("families", FAMILY)} | {("criteria", o["metric"]) for o in doc["objectives"]}
    support = native_support(tmp_path, monkeypatch, cat, records)
    pkg = publish(cat, doc, support, records)
    baseline = m.readiness_matrix(doc, "1.20")["rostering"]["optimization"]
    assert baseline["ready"] and baseline["objectives"] == ["O-ACTIVE"], baseline
    receiver.check_support_execution(pkg, task="rostering", mode="optimization")
    return doc, cat, support, records, pkg


def test_blocked_reference_remains_in_snapshot_and_missing_cells_refuse(tmp_path, monkeypatch):
    doc, cat, support, records, _pkg = roster_objectives(tmp_path, monkeypatch)
    withdraw(support, "criteria", "credit_hours_spread", "kotlin_metric")
    pkg = publish(cat, doc, support, records)
    receiver.check_support_execution(pkg, task="rostering", mode="optimization")
    snapshot = pkg["manifest"]["support"]
    cells = [c for c in snapshot["cells"] if c["section"] == "criteria"
             and c["record_id"] == "credit_hours_spread"]
    assert cells and any(c["state"] == "нет" for c in cells)
    assert all(c["approved"] is None for c in cells)
    damaged = copy.deepcopy(pkg)
    damaged["manifest"]["support"]["cells"] = [c for c in snapshot["cells"] if c not in cells]
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.check_support_projection(damaged)
    assert error.value.code == "manifest-mismatch"
    assert m.validate_schema(doc) == m.validate_model(doc) == []


@pytest.mark.parametrize("another_open", [False, True])
def test_closed_and_mixed_blockers_share_the_readiness_support_law(tmp_path, monkeypatch, another_open):
    doc, cat, support, records, _pkg = roster_objectives(tmp_path, monkeypatch)
    question = doc["questions"][0]
    question["answer"] = {"raw": "Дополнительная целевая подтверждена.",
        "status": "применён", "revision": 2, "goal_decline": False}
    if another_open:
        extra = copy.deepcopy(question)
        extra.update(id="Q-3b-GOAL-T", title="Уточнение второй части целевой",
                     text="Уточните вторую часть дополнительной целевой.")
        extra.pop("answer")
        doc["questions"].append(extra)
        doc["objectives"][1]["blockers"].append(extra["id"])
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    qmap = {q["id"]: q for q in doc["questions"]}
    assert m.objective_is_blocked(doc["objectives"][1], qmap) is another_open
    pkg = publish(cat, doc, support, records)
    receiver.check_support_execution(pkg, task="rostering", mode="optimization")
    before = m.readiness_matrix(doc, "1.20")["rostering"]["optimization"]
    assert before["ready"]
    assert before["objectives"] == (["O-ACTIVE"] if another_open else ["O-ACTIVE", "O-BLOCKED"])
    withdraw(support, "criteria", "credit_hours_spread", "kotlin_metric")
    pkg = publish(cat, doc, support, records)
    after = m.readiness_matrix(doc, "1.20")["rostering"]["optimization"]
    assert after["ready"] is another_open, after
    if another_open:
        receiver.check_support_execution(pkg, task="rostering", mode="optimization")
    else:
        with pytest.raises(receiver.TaskPackageRefused) as error:
            receiver.check_support_execution(pkg, task="rostering", mode="optimization")
        assert error.value.code == "support-missing"
        assert "criteria/credit_hours_spread" in str(error.value)


def test_real_derived_pairing_criterion_survives_its_blocked_source(tmp_path, monkeypatch):
    from vmp import objective_projection

    doc, cat = healthy_model(tmp_path, monkeypatch)
    source = copy.deepcopy(doc["rules"][0]["sources"])
    doc["questions"] = [{"id": "Q-3a-GOAL", "part": "3a",
        "title": "Дополнительный критерий", "text": "Уточните дополнительный критерий.",
        "answer_format": {"type": "registry"},
        "rule_refs": [doc["rules"][0]["id"]], "source_refs": source}]
    doc["objectives"] = [
        {"id": "O-BLOCKED", "title": "Критерий из документа", "statement": "Минимум человеко-дней",
         "zone": "PAIRING", "direction": "minimize", "sources": source, "blockers": ["Q-3a-GOAL"],
         "criterion": {"kind": "min_person_days", "rate_per_duty": 1,
             "version": "fixture-v1", "effective_from": "2030-01-01", "as_written": "Минимум человеко-дней"}},
        {"id": "O-ACTIVE", "title": "Стоимость", "statement": "Минимум нормированной стоимости",
         "zone": "PAIRING", "direction": "minimize", "sources": source,
         "criterion": {"kind": "rank_normalized_cost", "rates": {"flight": 1, "work": 2},
             "version": "fixture-v1", "effective_from": "2030-01-01", "as_written": "Минимум нормированной стоимости"}}]
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    derived = m.derive_pairing_criterion(doc)
    assert derived["kind"] == "min_person_days" and derived["source_objective"] == "O-BLOCKED"
    # Current producer law really carries a standalone pairing criterion.
    # The K06 test must not silently change that derivation priority.
    projected = objective_projection.project(doc, cat, context={"criterion": derived})
    assert projected["pairing"]["criterion"]["kind"] == "min_person_days"
    records = {("families", FAMILY), ("criteria", "min_person_days"), ("criteria", "rank_normalized_cost")}
    support = native_support(tmp_path, monkeypatch, cat, records)
    pkg = publish(cat, doc, support, records)
    assert sm._criterion_ids(doc, "pairing", active_only=True) == {"min_person_days", "rank_normalized_cost"}
    baseline = m.readiness_matrix(doc, "1.20")["pairing"]["optimization"]
    assert baseline["ready"] and baseline["objectives"] == ["O-ACTIVE"], baseline
    receiver.check_support_execution(pkg, task="pairing", mode="optimization")
    withdraw(support, "criteria", "min_person_days", "builder_method")
    pkg = publish(cat, doc, support, records)
    assert not m.readiness_matrix(doc, "1.20")["pairing"]["optimization"]["ready"]
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.check_support_execution(pkg, task="pairing", mode="optimization")
    assert error.value.code == "support-missing"
    assert "criteria/min_person_days" in str(error.value)
    for mode in ("feasibility-hard", "feasibility-soft"):
        assert m.readiness_matrix(doc, "1.20")["pairing"][mode]["ready"]
        receiver.check_support_execution(pkg, task="pairing", mode=mode)


def test_shared_metric_of_active_and_blocked_objectives_remains_required(tmp_path, monkeypatch):
    doc, cat, support, records, _pkg = roster_objectives(tmp_path, monkeypatch, same_metric=True)
    assert sm._criterion_ids(doc, "rostering", active_only=True) == {"credit_hours_spread"}
    withdraw(support, "criteria", "credit_hours_spread", "kotlin_metric")
    pkg = publish(cat, doc, support, records)
    matrix = m.readiness_matrix(doc, "1.20")["rostering"]["optimization"]
    assert matrix["objectives"] == ["O-ACTIVE"] and not matrix["ready"], matrix
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.check_support_execution(pkg, task="rostering", mode="optimization")
    assert error.value.code == "support-missing"
    assert "criteria/credit_hours_spread" in str(error.value)
