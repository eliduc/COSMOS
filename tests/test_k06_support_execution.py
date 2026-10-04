"""Selected family execution proof, using only explicit temporary evidence.

Fixture approvals and stand outcomes are synthetic test data, never a
release or assertion that an actual solver was run or approved.
"""
import copy
import json
import os
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb
from template_catalog import bindings
from vmp.version import PRODUCT_VERSION
from webapp import task_package as receiver
from test_k06_support_manifest import scenario, FAMILY
from test_k01_semantic_transition import clean_bridge


def executable(tmp_path, monkeypatch, *, criterion=None, task="rostering"):
    doc, cat, path, declared = scenario(tmp_path, monkeypatch)
    doc["rules"][0].update(kind="HARD", zone="ROSTERING")
    if criterion:
        doc["objectives"] = [{"id": "O-EXEC", "title": "Целевая", "statement": "Минимум",
            "zone": task.upper(), "direction": "minimize", "metric": criterion,
            "sources": copy.deepcopy(doc["rules"][0]["sources"])}]
    carried_criteria = {criterion or "min_person_days"}
    raw = json.loads((Path(__file__).resolve().parents[1] / "vmp" / "bindings.json").read_text(encoding="utf-8"))
    raw.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint(),
               product_version=PRODUCT_VERSION)
    raw["bindings"] = [r for r in raw["bindings"] if
        (r["section"] == "families" and r["id"] == FAMILY) or
        (r["section"] == "criteria" and r["id"] in carried_criteria)]
    for row in raw["bindings"]:
        for stand in row["stand_verified"]:
            stand["result"] = "passed"  # explicit isolated evidence fixture
    live = tmp_path / "cosmos-bindings.json"
    live.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CREW_COSMOS_BINDINGS", str(live))
    monkeypatch.setenv("CDPM_BINDINGS_FILE", str(tmp_path / "absent-cdpm.json"))
    support = bindings.support_table(cat, {"COSMOS": live}, approved_by="isolated fixture",
        approved_how="synthetic test, not release", computed_at="2026-09-16T00:00:00Z")
    path.write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")
    declared["support"] = copy.deepcopy(support)
    declared["support"]["cells"] = [c for c in support["cells"] if
        (c["section"] == "families" and c["record_id"] == FAMILY) or
        (c["section"] == "criteria" and c["record_id"] in carried_criteria)]
    cell = next(c for c in declared["support"]["cells"]
                if c["kind"] == "kotlin_logic_form" and c["product"] == "COSMOS")
    assert cell["state"] == "есть" and cell["approved"]
    assert cb.support_table()["files"]["COSMOS"]["ok"]
    pkg = {"model": doc, "manifest": declared}
    receiver.check_support_execution(pkg, task="rostering", mode="feasibility-hard")
    return pkg, support, cell, path, live


@pytest.mark.parametrize("mutation", ["approval", "state", "live_sha", "live_missing",
                                      "product_version", "foreign_product"])
def test_actual_execution_gate_rejects_unapproved_or_stale_evidence(tmp_path, monkeypatch, mutation):
    pkg, support, cell, path, live = executable(tmp_path, monkeypatch)
    if mutation == "live_sha":
        st = live.stat()
        raw = live.read_bytes()
        # Same length and mtime: content identity, not cached metadata.
        changed = raw.replace(b"1.18.5", b"1.18.4")
        assert changed != raw and len(changed) == len(raw)
        live.write_bytes(changed)
        os.utime(live, ns=(st.st_atime_ns, st.st_mtime_ns))
    elif mutation == "live_missing":
        live.unlink()
    else:
        if mutation == "approval": cell["approved"] = None
        elif mutation == "state": cell["state"] = "не проверено"
        elif mutation == "product_version": cell["bindings_file"]["product_version"] = "0.0.0"
        else:
            # The other product's valid-looking cell cannot supply COSMOS.
            cell["state"] = "нет"
            other = next(c for c in pkg["manifest"]["support"]["cells"]
                         if c["kind"] == cell["kind"] and c["product"] == "CDPM")
            other.update(state="есть", approved=copy.deepcopy(cell["approved"]))
        # Both producer snapshot and local service evidence agree, so the
        # refusal must be execution proof, not package snapshot inequality.
        by_key = {(c["section"], c["record_id"], c["kind"], c["product"]): c
                  for c in pkg["manifest"]["support"]["cells"]}
        support["cells"] = [by_key.get((c["section"], c["record_id"], c["kind"], c["product"]), c)
                            for c in support["cells"]]
        path.write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.check_support_execution(pkg, task="rostering", mode="feasibility-hard")
    assert error.value.code == "support-missing"
    assert "families/" + FAMILY in str(error.value)
    assert "kotlin_logic_form" in str(error.value)


def test_mode_and_task_do_not_borrow_or_require_unselected_executor(tmp_path, monkeypatch):
    pkg, support, cell, path, _live = executable(tmp_path, monkeypatch)
    cell["approved"] = None
    path.write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")
    # An unrelated zone does not make this family a pairing obligation.
    receiver.check_support_execution(pkg, task="pairing", mode="feasibility-hard")
    pkg["model"]["rules"][0]["kind"] = "SOFT"
    receiver.check_support_execution(pkg, task="rostering", mode="feasibility-hard")
    with pytest.raises(receiver.TaskPackageRefused, match="утверждения"):
        receiver.check_support_execution(pkg, task="rostering", mode="feasibility-soft")


def test_execution_still_rejects_unknown_mode(tmp_path, monkeypatch):
    pkg, *_ = executable(tmp_path, monkeypatch)
    with pytest.raises(receiver.TaskPackageRefused, match="режим"):
        receiver.check_support_execution(pkg, task="rostering", mode="unknown")


def test_catalog_failure_after_snapshot_check_is_a_structured_refusal(tmp_path, monkeypatch):
    pkg, *_ = executable(tmp_path, monkeypatch)

    def unavailable():
        raise cb.CatalogRequired("свидетельство стало недоступно после сверки снимка")

    monkeypatch.setattr(cb, "support_table", unavailable)
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.check_support_execution(pkg, task="rostering", mode="feasibility-hard")
    assert error.value.code == "catalog-unavailable"
    assert "после сверки снимка" in str(error.value)


@pytest.mark.parametrize("task,criterion,kind", [
    ("pairing", "total_pairing_cost", "builder_method"),
    ("rostering", "credit_hours_spread", "kotlin_metric"),
])
def test_selected_criterion_requires_its_own_approval_only_for_optimization(
        tmp_path, monkeypatch, task, criterion, kind):
    pkg, support, _family, path, _live = executable(
        tmp_path, monkeypatch, criterion=criterion, task=task)
    receiver.check_support_execution(pkg, task=task, mode="optimization")
    cell = next(c for c in pkg["manifest"]["support"]["cells"] if
                (c["section"], c["record_id"], c["kind"], c["product"]) ==
                ("criteria", criterion, kind, "COSMOS"))
    assert cell["state"] == "есть" and cell["approved"]
    cell["approved"] = None
    path.write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")
    # Snapshot and local evidence agree; a different family or mode cannot
    # supply this criterion's missing approval.
    receiver.check_support_execution(pkg, task=task, mode="feasibility-hard")
    receiver.check_support_execution(pkg, task=task, mode="feasibility-soft")
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.check_support_execution(pkg, task=task, mode="optimization")
    assert error.value.code == "support-missing"
    assert "criteria/" + criterion in str(error.value)
    assert kind in str(error.value)


def test_criterion_with_no_executor_for_selected_task_is_not_vacuously_supported(tmp_path, monkeypatch):
    pkg, _support, _cell, _path, _live = executable(
        tmp_path, monkeypatch, criterion="rank_normalized_cost", task="pairing")
    receiver.check_support_execution(pkg, task="pairing", mode="optimization")
    pkg["model"]["objectives"][0]["zone"] = "ROSTERING"
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.check_support_execution(pkg, task="rostering", mode="optimization")
    assert error.value.code == "support-missing"
    assert "criteria/rank_normalized_cost" in str(error.value)
    assert "kotlin_metric" in str(error.value)
