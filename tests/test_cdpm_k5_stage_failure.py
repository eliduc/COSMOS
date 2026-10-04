"""K-5: swallowed extraction failures retain local stack diagnostics.

Real handler, status merge, journal and stage-thread wrapper; only thread
scheduling and extraction dependencies are isolated. Synthetic temporary
files only, no model/provider/catalogue calls and no integration acceptance.
"""
import json
from types import SimpleNamespace

import pytest

from cdpm import webui


def _dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _extraction_harness(tmp_path, monkeypatch, *, failure=None):
    run = tmp_path / "runs" / "run-0001"
    _dump(run / "status.json", {
        "state": "заморожено", "customer": "Synthetic K5",
        "data_state": "данные сохранены", "unrelated": {"keep": True},
        "kit": {"data_class": "synthetic", "llm_scope": "local-only"},
    })
    _dump(run / "freeze" / "manifest.json", {"files": []})
    monkeypatch.setenv("CDPM_RUNS_DIR", str(run.parent))
    monkeypatch.delenv("CDPM_SCOPE_FORCE", raising=False)
    monkeypatch.setattr(webui, "_read_files_config", lambda _run: {})
    monkeypatch.setattr(webui, "_strictest_class", lambda _run: "local")
    monkeypatch.setattr(webui, "_extract_cache_fingerprint", lambda *a: "synthetic")
    monkeypatch.setattr(webui, "_role_config_salt", lambda *a: "synthetic")
    monkeypatch.setattr(webui, "_refresh_run_record", lambda *a, **kw: None)

    def no_channel_calls(*args, **kwargs):
        raise AssertionError("K5 diagnostics test must not invoke an LLM")

    monkeypatch.setattr(webui, "_channel_for", lambda *a, **kw: no_channel_calls)
    # Run the actual wrapper synchronously: no background thread escapes pytest.
    monkeypatch.setattr(webui.threading, "Thread",
                        lambda *, target, daemon: SimpleNamespace(start=target))
    seen = []

    def injected_extraction(freeze_dir, extract_dir, channel, customer, **kwargs):
        seen.append("extract")
        assert freeze_dir == run / "freeze" and extract_dir == run / "extract"
        assert customer == "Synthetic K5"
        kwargs["progress"]("synthetic", 1, 2, 0)
        if failure is not None:
            sensitive_local = "SYNTHETIC-PRIVATE-LOCAL-DO-NOT-LOG"
            assert sensitive_local
            raise failure
        return {"rules": 0}

    def produce_logic(*args, **kwargs):
        seen.append("logic")
        return {"checked": 0}

    monkeypatch.setattr(webui.extract, "run_extract", injected_extraction)
    monkeypatch.setattr(webui.extract, "produce_logic", produce_logic)
    monkeypatch.setattr(webui.m_mod, "load_model", lambda *a: {"rules": []})
    monkeypatch.setattr(webui.m_mod, "publish_model", lambda *a: seen.append("publish"))
    monkeypatch.setattr(webui.anketa_xlsx_mod, "render_anketa_xlsx",
                        lambda *a: seen.append("xlsx"))
    monkeypatch.setattr(webui.gate_extract, "check", lambda *a: ([], {"rules": 0}))
    response = webui.start_extract(run.name)
    assert response == {"run": run.name, "state": "извлечение идёт"}
    status = webui._read_status(run)
    journal = webui.actions_journal.read(run)
    assert [row["kind"] for row in journal] == ["start", "end"]
    assert journal[-1]["state_after"]["state"] == status["state"]
    assert webui.actions_journal.current() is None
    assert status["data_state"] == "данные сохранены"
    assert status["unrelated"] == {"keep": True}
    assert "progress" not in status
    assert "error_trace" not in status
    return run, status, journal, seen


def test_extraction_typeerror_has_local_stack_and_unchanged_refusal(
        tmp_path, monkeypatch, capsys):
    run, status, journal, seen = _extraction_harness(
        tmp_path, monkeypatch, failure=TypeError("unhashable type: 'list'"))
    assert status["state"] == "отказ извлечения"
    assert status["error"] == "unhashable type: 'list'"
    assert seen == ["extract"]
    diagnostic = capsys.readouterr().err
    assert "run-0001: state: отказ извлечения: TypeError" in diagnostic
    assert "Traceback" in diagnostic
    assert "webui.py:" in diagnostic and "in _work" in diagnostic
    assert "test_cdpm_k5_stage_failure.py:" in diagnostic
    assert "in injected_extraction" in diagnostic
    assert "unhashable type" not in diagnostic  # no exception payload
    assert "SYNTHETIC-PRIVATE-LOCAL" not in diagnostic
    assert "sensitive_local" not in diagnostic
    assert "raise failure" not in diagnostic  # no source lines
    assert str(tmp_path) not in diagnostic
    assert "Traceback" not in json.dumps(status)
    assert "Traceback" not in json.dumps(journal)
    assert "Traceback" not in (run / "status.json").read_text(encoding="utf-8")


def test_extraction_catalog_failure_keeps_catalog_stop_contract(
        tmp_path, monkeypatch, capsys):
    _, status, _, seen = _extraction_harness(
        tmp_path, monkeypatch,
        failure=webui.catalog_bridge.CatalogRequired("synthetic unavailable"))
    assert status["state"] == webui.CATALOG_STOP_STATE
    assert status["error"] == (
        f"{webui.CATALOG_STOP_STATE}: synthetic unavailable — стадия не "
        "выполнена (отказ извлечения)")
    assert seen == ["extract"]
    assert "CatalogRequired" in capsys.readouterr().err


@pytest.mark.parametrize("failure", [None, webui.StopRequested("извлечение")],
                         ids=["healthy", "operator-stop"])
def test_healthy_and_stopped_extraction_do_not_emit_failure_diagnostics(
        tmp_path, monkeypatch, capsys, failure):
    _, status, _, seen = _extraction_harness(tmp_path, monkeypatch, failure=failure)
    assert "error" not in status
    if failure is None:
        assert status["state"] == "извлечено"
        assert status["extract_summary"] == {"rules": 0, "logic": {"checked": 0}}
        assert seen == ["extract", "logic", "publish", "xlsx"]
    else:
        assert status["state"] == "остановлено: извлечение"
        assert seen == ["extract"]
    assert capsys.readouterr().err == ""


def test_unavailable_diagnostic_stream_does_not_replace_failure(monkeypatch):
    def fail_write(_text):
        raise OSError("synthetic closed stderr")

    monkeypatch.setattr(webui.sys, "stderr", SimpleNamespace(write=fail_write))
    error = TypeError("synthetic failure")
    assert webui._stage_failure("отказ проверок данных", error, "data_state") == {
        "data_state": "отказ проверок данных", "error": "synthetic failure"}
