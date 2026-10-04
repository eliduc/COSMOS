"""Actual ZIP receiver must not trust a rehashed support declaration.

Only isolated catalogue files and package bytes; no external gate, service,
solver, catalogue release or approval is performed. The local support file
is a synthetic fixture, never an assertion of production readiness.
"""
import copy
import json
import os

import pytest

from template_catalog import bindings
from cdpm import catalog_bridge as cb
from test_k01_family_manifest import manifest, receive, FAMILY
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge
from webapp import task_package as receiver
from cdpm import support_manifest


def scenario(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, FAMILY, {
        "min_days_off": {"value": 8, "unit": "count", "relation": "min",
                         "condition": None, "quote": "8"}})
    rule.pop("params", None)
    doc["rules"] = [rule]
    cat = cb.catalog()
    support = bindings.support_table(cat, {}, computed_at="2026-09-16T00:00:00Z")
    path = cat.path / "support.json"
    path.write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")
    # An independently selected subset, not project()'s answer as oracle.
    expected = copy.deepcopy(support)
    # The inherited objective explicitly derives min_person_days from prose.
    expected["cells"] = [cell for cell in expected["cells"] if
        (cell["section"] == "families" and cell["record_id"] == FAMILY) or
        (cell["section"] == "criteria" and cell["record_id"] == "min_person_days")]
    assert expected["cells"]
    declared = manifest(cat)
    declared["support"] = expected
    return doc, cat, path, declared


@pytest.mark.parametrize("mutation", ["state", "omit_cell", "duplicate", "record",
                                      "product", "fingerprint", "shape", "approval"])
def test_receiver_rejects_forged_support(tmp_path, monkeypatch, mutation):
    doc, _cat, _path, declared = scenario(tmp_path, monkeypatch)
    # Prove the unchanged typed model, checksums and family projection pass.
    assert receive(tmp_path, doc, declared)["model"] == doc
    support = declared["support"]
    if mutation == "state":
        support["cells"][0]["state"] = "есть"
    elif mutation == "omit_cell":
        support["cells"].pop()
    elif mutation == "duplicate":
        support["cells"].append(copy.deepcopy(support["cells"][0]))
    elif mutation == "record":
        support["cells"][0]["record_id"] = "duty_limits"
    elif mutation == "product":
        support["cells"][0]["product"] = "wrong-product"
    elif mutation == "fingerprint":
        support["catalog"]["fingerprint"] = "0" * 64
    elif mutation == "approval":
        support["cells"][0]["approved"] = {"by": "forged", "at": "today", "how": "guess"}
    else:
        declared["support"] = None
    with pytest.raises(receiver.TaskPackageRefused, match="support") as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "manifest-mismatch"


def test_unchanged_snapshot_is_judged_but_not_execution_approval(tmp_path, monkeypatch):
    doc, _cat, _path, declared = scenario(tmp_path, monkeypatch)
    before = copy.deepcopy(doc)
    received = receive(tmp_path, doc, declared)
    assert received["support_projection"]["judged"] is True
    assert "не допуск" in received["support_projection"]["words"]
    assert received["model"] == before == doc


def test_legacy_absence_is_explicitly_unjudged(tmp_path, monkeypatch):
    doc, _cat, _path, declared = scenario(tmp_path, monkeypatch)
    del declared["support"]
    received = receive(tmp_path, doc, declared)
    assert received["support_projection"]["judged"] is False
    assert "не судится" in received["support_projection"]["words"]


def test_modern_absence_rejected_before_objective_and_executor(tmp_path, monkeypatch):
    doc, _cat, _path, declared = scenario(tmp_path, monkeypatch)
    del declared["support"]
    declared["spec_version"] = "1.20"
    with pytest.raises(receiver.TaskPackageRefused, match="support") as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "support-missing"


def test_same_size_same_time_source_change_cannot_keep_old_snapshot(tmp_path, monkeypatch):
    doc, _cat, path, declared = scenario(tmp_path, monkeypatch)
    assert receive(tmp_path, doc, declared)["model"] == doc
    st = path.stat()
    raw = path.read_bytes()
    changed = raw.replace(b"2026-09-16T00:00:00Z", b"2026-09-16T00:00:01Z")
    assert changed != raw and len(changed) == len(raw)
    path.write_bytes(changed)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    with pytest.raises(receiver.TaskPackageRefused, match="support") as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "manifest-mismatch"


def test_missing_local_source_is_not_replaced_by_package_claim(tmp_path, monkeypatch):
    doc, _cat, path, declared = scenario(tmp_path, monkeypatch)
    assert receive(tmp_path, doc, declared)["model"] == doc
    path.unlink()
    with pytest.raises(receiver.TaskPackageRefused, match="support") as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "support-missing"


def test_preexecution_rechecks_mutated_package_instead_of_display_flag(tmp_path, monkeypatch):
    doc, _cat, _path, declared = scenario(tmp_path, monkeypatch)
    received = receive(tmp_path, doc, declared)
    received["support_projection"] = {"judged": True, "problems": []}
    received["manifest"]["support"]["cells"][0]["state"] = "есть"
    with pytest.raises(receiver.TaskPackageRefused, match="support") as error:
        receiver.to_payload(received)
    assert error.value.code == "manifest-mismatch"


@pytest.mark.parametrize("origin", ["metric", "criterion", "derived"])
def test_projection_carries_used_criteria_from_the_model_not_package(tmp_path, monkeypatch, origin):
    doc, _cat, path, declared = scenario(tmp_path, monkeypatch)
    doc["objectives"] = []
    criterion = "credit_hours_spread" if origin == "metric" else "min_person_days"
    if origin == "metric":
        doc["objectives"] = [{"id": "O-K06", "zone": "ROSTERING",
                              "direction": "minimize", "metric": criterion}]
    elif origin == "criterion":
        doc["objectives"] = [{"id": "O-K06", "zone": "PAIRING",
            "direction": "minimize", "criterion": {"kind": criterion,
            "rate_per_duty": 1, "version": "v1", "effective_from": "2030-01-01",
            "as_written": "Минимум человеко-дней — 1"}}]
    else:
        doc["rules"][0].update(zone="PAIRING", title="Минимум человеко-дней",
                              statement="Минимум человеко-дней — 3 условных единицы")
    for objective in doc["objectives"]:
        objective.update(title="Целевая", statement="Минимум выбранного критерия",
                         sources=copy.deepcopy(doc["rules"][0]["sources"]))
    native = json.loads(path.read_text(encoding="utf-8"))
    expected = copy.deepcopy(native)
    expected["cells"] = [c for c in native["cells"] if
        (c["section"] == "families" and c["record_id"] == FAMILY) or
        (c["section"] == "criteria" and c["record_id"] == criterion)]
    assert any(c["section"] == "criteria" for c in expected["cells"])
    before = copy.deepcopy(doc)
    assert support_manifest.project(doc) == expected
    declared["support"] = copy.deepcopy(expected)
    assert receive(tmp_path, doc, declared)["model"] == before
    declared["support"]["cells"] = [c for c in expected["cells"] if c["section"] != "criteria"]
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "manifest-mismatch"
