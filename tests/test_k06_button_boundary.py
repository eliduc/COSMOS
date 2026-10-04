"""Real export/gate/reader/button; offline service seam never executes a job.

The default SOFT-only fixture has a real zero-HARD consistency report.
The book fixture has synthetic data and one exactly translated DATA rule
checked by local Z3. Neither fixture runs an operational solver/service.
"""
import json
from pathlib import Path

import pytest

from cdpm.gates import gate_task_package as gate
from test_cdpm_landing_v2_export import real_soft_package
from test_k01_semantic_transition import clean_bridge
from vmp.run import button
from webapp import task_package as receiver


class NoExecution:
    def health(self):
        return True  # explicit offline service seam, not a live health probe

    def start(self, *_args, **_kwargs):
        pytest.fail("A refused package must not reach a solver")

    def pairing_catalog(self, *_args, **_kwargs):
        pytest.fail("A refused package must not reach catalogue registration")


@pytest.mark.parametrize("boundary", ["load", "to_payload"])
def test_support_source_lost_after_gate_is_recorded_as_button_stop(tmp_path, monkeypatch, boundary):
    parts = real_soft_package(tmp_path, monkeypatch, "1.20")
    support_path, run, package_path = parts[3], parts[4], parts[-1]
    # Healthy package genuinely passes the same gate and receiver first.
    assert gate.check(package_path)[0] == []
    assert receiver.load(package_path, run_gate=False)["support_projection"]["judged"]
    reached = []

    def coverage(_solver, _body, _task):
        reached.append("after_gate")
        if boundary == "load":
            support_path.unlink()
        return None

    def convert(pkg):
        reached.append("to_payload")
        if boundary == "to_payload":
            support_path.unlink()
        return receiver.to_payload(pkg)

    delivery = button.orchestrate(run, NoExecution(), task="pairing",
        mode="feasibility-hard", package_path=package_path,
        coverage_fn=coverage, to_payload=convert)
    assert reached == (["after_gate"] if boundary == "load"
                       else ["after_gate", "to_payload"])
    assert delivery["code"] == "support-missing"
    assert "support" in delivery["detail"]
    assert delivery["verdict"].startswith("остановка:")
    assert delivery["journal"][-1]["step"] == "остановка"
    saved = json.loads((run / "button-pairing" / "delivery.json").read_text(encoding="utf-8"))
    assert saved == delivery


@pytest.mark.parametrize("mode", ["feasibility-hard", "feasibility-soft"])
def test_button_really_checks_selected_mode_before_readiness(tmp_path, monkeypatch, mode):
    parts = real_soft_package(tmp_path, monkeypatch, "1.20")
    cat, run, package_path = parts[1], parts[4], parts[-1]
    # Retarget only an isolated copy to the real temporary catalogue. The
    # production bindings stay untouched; no approval is created here.
    raw = json.loads((Path(__file__).resolve().parents[1] / "vmp" / "bindings.json").read_text(encoding="utf-8"))
    raw.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint())
    live = tmp_path / "button-bindings.json"
    live.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CREW_COSMOS_BINDINGS", str(live))
    # This test isolates wiring AFTER conversion. The fixture deliberately
    # has no operational book; this seam is not an end-to-end converter proof.
    delivery = button.orchestrate(run, NoExecution(), task="pairing", mode=mode,
        package_path=package_path, coverage_fn=lambda *_: None,
        to_payload=lambda _pkg: {"employees": []})
    if mode == "feasibility-soft":
        assert delivery["code"] == "support-missing", delivery
        assert "families/duty_extension_penalty" in delivery["detail"]
        assert "builder_method" in delivery["detail"]
        assert not any(row["step"] == "Ш-2м" for row in delivery["journal"])
    else:
        # Hard-only mode must not require a SOFT family's executor. The
        # genuine next gate still refuses the deliberately absent data.
        assert "матрица готовности" in delivery["verdict"], delivery
        assert any(row["step"] == "Ш-2м" for row in delivery["journal"])


@pytest.mark.parametrize("mode", ["feasibility-hard", "feasibility-soft"])
def test_real_book_conversion_checks_support_before_the_remaining_measure_lock(tmp_path, monkeypatch, mode):
    parts = real_soft_package(tmp_path, monkeypatch, "1.20", with_data=True)
    cat, run, package_path = parts[1], parts[4], parts[-1]
    raw = json.loads((Path(__file__).resolve().parents[1] / "vmp" / "bindings.json").read_text(encoding="utf-8"))
    raw.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint())
    live = tmp_path / "book-bindings.json"
    live.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CREW_COSMOS_BINDINGS", str(live))
    received = receiver.load(package_path, run_gate=False)
    payload = receiver.to_payload(received)
    assert received["manifest"]["crew_size"] == {"value": 1, "rule": "D-CREW"}
    assert len(payload["employees"]) == len(payload["flights"]) == 1
    # This synthetic book has no source-document freeze. The independent
    # formulation-measure lock must remain closed; do not fabricate its PASS
    # merely to advance to the operational executor boundary.
    assert received["manifest"]["measure"]["available"] is False
    delivery = button.orchestrate(run, NoExecution(), task="pairing",
        mode=mode, package_path=package_path,
        coverage_fn=lambda *_: None)
    if mode == "feasibility-soft":
        assert delivery["code"] == "support-missing", delivery
        assert "families/duty_extension_penalty" in delivery["detail"]
        assert "builder_method" in delivery["detail"]
    else:
        assert "измеритель или проверка постановки" in delivery["verdict"], delivery
        assert delivery["detail"] == "измеритель постановки не отработал: нет заморозки — измерять не по чему"
        assert any(row["step"] == "Ш-2м" and row["status"] == "пройден"
                   for row in delivery["journal"])
        assert any(row["step"] == "Ш-2и" and row["status"] == "остановка"
                   for row in delivery["journal"])
    assert any(row["step"] == "Ш-1к" for row in delivery["journal"])
