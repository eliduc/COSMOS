"""Read-only, hash-bound parameter view using the consumer's actual reader.

Temporary synthetic models and in-process HTTP; no live model or solver.
"""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from cdpm import answers, model, webui
from vmp import execution_manifest as execution


def _panel(tmp_path, monkeypatch, doc):
    run = tmp_path / "run-0042"
    path = run / "extract/model.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path))
    return path, TestClient(webui.app)


def _rule(source, value, quote=None):
    r = {"id": "R-1", "title": "Предел", "kind": "HARD", "zone": "BOTH"}
    if source == "params":
        r["params"] = [{"name": "limit", "value": value,
                        "as_written": quote, "unit": "minutes",
                        "relation": "le", "condition": "на базе"}]
    else:
        cell = value if source == "scalar" else {
            "value": value, "quote": quote, "unit": "minutes",
            "relation": "le", "condition": "на базе"}
        r["template_fit"] = {"params": {"limit": cell}}
    return r


@pytest.mark.parametrize("source", ["cell", "scalar", "params"])
@pytest.mark.parametrize("value,quote", [
    (720, "не более 12:00"), ("A320", "A320"), (0, None),
    (False, None), (["A320", "B738"], "A320, B738"),
    (None, None), ("", None),
])
def test_http_parameters_are_exactly_the_consumer_view_without_writes(
        tmp_path, monkeypatch, source, value, quote):
    rule = _rule(source, value, quote)
    doc = {"meta": {"revision": 7}, "rules": [rule]}
    path, client = _panel(tmp_path, monkeypatch, doc)
    before = path.read_bytes()
    r = client.get("/api/runs/run-0042/effective-parameters")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["run"] == "run-0042"
    assert got["model_revision"] == 7
    assert got["model_sha256"] == hashlib.sha256(before).hexdigest()
    assert got["snapshot_at"]
    assert r.headers["cache-control"] == "no-store"
    assert got["rules"][0]["params"] == execution.rule_params(rule)
    effective = got["rules"][0]["params"][0]
    assert effective["name"] == "limit"
    assert effective["value"] == value and type(effective["value"]) is type(value)
    assert effective["quote"] == (None if source == "scalar" else quote)
    assert effective["from"] == ("params" if source == "params" else "template_fit")
    assert effective["unit"] == (None if source == "scalar" else "minutes")
    assert effective["condition"] == (None if source == "scalar" else "на базе")
    assert got["rules"][0]["kind"] == "HARD"
    assert got["rules"][0]["zone"] == "BOTH"
    assert path.read_bytes() == before
    assert sorted(p.relative_to(path.parent.parent).as_posix()
                  for p in path.parent.parent.rglob("*") if p.is_file()) == [
                      "extract/model.json"]


def test_shadowed_legacy_value_is_not_shown_as_effective(tmp_path, monkeypatch):
    rule = _rule("cell", 720, "не более 12:00")
    rule["params"] = [{"name": "limit", "value": 900, "as_written": "15:00"}]
    path, client = _panel(tmp_path, monkeypatch, {"rules": [rule]})
    before = path.read_bytes()
    response = client.get("/api/runs/run-0042/effective-parameters")
    assert response.status_code == 200, response.text
    got = response.json()
    p = got["rules"][0]["params"][0]
    assert p["value"] == 720 and p["quote"] == "не более 12:00"
    assert p["from"] == "template_fit"
    assert path.read_bytes() == before


def test_question_answer_and_human_view_use_same_effective_value(tmp_path,
                                                              monkeypatch):
    from test_cdpm_k02_number_grammar import _prepared_fleet, QID_FLEET
    doc, rule, question = _prepared_fleet()
    assert answers.apply_answers(doc, {QID_FLEET: "A320, B738"})["applied"] == 1
    assert not model.is_open_question(question)
    path, client = _panel(tmp_path, monkeypatch, doc)
    response = client.get("/api/runs/run-0042/effective-parameters")
    assert response.status_code == 200, response.text
    shown = next(r for r in response.json()["rules"] if r["id"] == rule["id"])
    assert shown["params"] == execution.rule_params(rule)
    assert shown["params"][0]["value"] == "A320, B738"
    assert json.loads(path.read_text(encoding="utf-8")) == doc


def test_empty_rules_and_rule_without_parameters_are_explicit(tmp_path,
                                                             monkeypatch):
    _, client = _panel(tmp_path, monkeypatch, {"rules": [{"id": "R-EMPTY"}]})
    r = client.get("/api/runs/run-0042/effective-parameters")
    assert r.status_code == 200 and r.json()["rules"][0]["params"] == []


@pytest.mark.parametrize("bad", [[], {"rules": {}}, {"rules": [None]},
    {"rules": [{"params": {}}]},
    {"rules": [{"template_fit": {"params": [1]}}]}])
def test_malformed_model_is_not_silently_an_empty_success(tmp_path, monkeypatch, bad):
    _, client = _panel(tmp_path, monkeypatch, bad)
    response = client.get("/api/runs/run-0042/effective-parameters")
    assert response.status_code == 409, response.text
    assert "модел" in response.json()["detail"]


def test_missing_model_and_invalid_run_are_not_green_empty_views(tmp_path, monkeypatch):
    path, client = _panel(tmp_path, monkeypatch, {"rules": []})
    path.unlink()
    assert client.get("/api/runs/run-0042/effective-parameters").status_code == 404
    assert client.get("/api/runs/not-a-run/effective-parameters").status_code in (400, 404)


@pytest.mark.parametrize("text", ['not-json',
    '{"rules":[{"params":[{"name":"bad","value":NaN}]}]}'])
def test_unreadable_or_nonfinite_json_is_an_explicit_model_error(tmp_path,
                                                               monkeypatch, text):
    path, client = _panel(tmp_path, monkeypatch, {"rules": []})
    path.write_text(text, encoding="utf-8")
    response = client.get("/api/runs/run-0042/effective-parameters")
    assert response.status_code == 409 and "модел" in response.json()["detail"]


def test_busy_model_returns_read_error_not_empty_success(tmp_path, monkeypatch):
    _, client = _panel(tmp_path, monkeypatch, {"rules": []})
    def busy(path):
        raise PermissionError("занят")
    monkeypatch.setattr(webui._file_replace, "read_bytes_shared", busy)
    response = client.get("/api/runs/run-0042/effective-parameters")
    assert response.status_code == 503 and "занят" in response.json()["detail"]


def test_refresh_rebinds_view_to_new_bytes_and_revision(tmp_path, monkeypatch):
    path, client = _panel(tmp_path, monkeypatch, {"meta": {"revision": 1}, "rules": []})
    a = client.get("/api/runs/run-0042/effective-parameters")
    assert a.status_code == 200
    path.write_text('{"meta":{"revision":2},"rules":[]}', encoding="utf-8")
    b = client.get("/api/runs/run-0042/effective-parameters").json()
    assert a.json()["model_sha256"] != b["model_sha256"]
    assert b["model_revision"] == 2


def test_existing_form_already_exposes_values_and_quotes(tmp_path, monkeypatch):
    # Counterexample to the audit's universal "nothing reaches the outside";
    # this existing route is retained, not credited as our new feature.
    from test_cdpm_vmp_form import _etalon_with_rates
    _, client = _panel(tmp_path, monkeypatch, _etalon_with_rates())
    response = client.get("/api/runs/run-0042/form")
    assert response.status_code == 200
    def quoted_params(node):
        if isinstance(node, dict):
            if "name" in node and "value" in node and node.get("quote"):
                yield node
            for child in node.values():
                yield from quoted_params(child)
        elif isinstance(node, list):
            for child in node:
                yield from quoted_params(child)
    assert list(quoted_params(response.json()["form"]))
    html = client.get("/api/runs/run-0042/form.md")
    assert html.status_code == 200 and "Параметр" in html.text


def test_projection_uses_shared_reader_not_its_own_precedence(monkeypatch):
    from cdpm import parameter_review
    sentinel = [{"name": "sentinel", "value": 17, "from": "params"}]
    monkeypatch.setattr(execution, "rule_params", lambda rule: copy.deepcopy(sentinel))
    assert parameter_review.from_model({"rules": [{"id": "R"}]})["rules"][0]["params"] == sentinel


def test_ui_parameter_snapshot_render_and_api_without_network():
    node = shutil.which("node")
    assert node, "Node is required for the parameter review UI profile"
    result = subprocess.run([node, "--test", "src/parameter-review.test.mjs"],
        cwd=Path(__file__).resolve().parents[1] / "cdpm-ui", capture_output=True,
        text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

def test_refused_answer_keeps_blocker_visible_with_legacy_value(tmp_path, monkeypatch):
    from test_cdpm_applicability_loss import _prepared
    doc, rule, question, name = _prepared("duration_minutes")
    raw = "не менее 720 минут для базы SVO"
    rule["params"] = [{"name": name, "value": 720, "as_written": raw}]
    result = answers.apply_answers(doc, {question["id"]: raw})
    assert result["applied"] == 0 and model.is_open_question(question)
    assert question["id"] in rule["blockers"]
    path, client = _panel(tmp_path, monkeypatch, doc)
    before = path.read_bytes()
    response = client.get("/api/runs/run-0042/effective-parameters")
    assert response.status_code == 200, response.text
    shown = next(r for r in response.json()["rules"] if r["id"] == rule["id"])
    assert shown["blockers"] == rule["blockers"]
    assert shown["params"] == execution.rule_params(rule)
    assert next(p for p in shown["params"] if p["name"] == name)["value"] == 720
    assert path.read_bytes() == before
