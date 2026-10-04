"""One in-memory legacy migration on semantic model reads; no live stages."""
import copy
import hashlib
import json

import pytest
from fastapi import HTTPException

from cdpm import model as m, webui
from test_cdpm_question_editions import _doc


def _legacy(tmp_path):
    doc, q = _doc()
    q["answer"] = {"raw": "600 минут", "status": "применён", "revision": 1}
    q.pop("edition", None)
    path = tmp_path / "extract" / "model.json"
    path.parent.mkdir(parents=True)
    m.dump_model(doc, path)
    return doc, q["id"], path


def _assert_migrated(doc, qid):
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert q.get("edition"), "the semantic reader bypassed edition migration"
    assert q["answer"].get("answer_version") == {
        "fingerprint": hashlib.sha256("600 минут".encode()).hexdigest()[:12],
        "received_at": None, "book_sha256": None,
    }


@pytest.mark.parametrize("as_bytes", [False, True])
def test_text_and_file_model_readers_have_one_migration(tmp_path, as_bytes):
    original, qid, path = _legacy(tmp_path)
    raw = path.read_bytes()
    got = m.loads_model(raw if as_bytes else raw.decode())
    _assert_migrated(got, qid)
    assert got == m.load_model(path)
    assert m.loads_model(json.dumps(got)) == got
    assert path.read_bytes() == raw
    assert "edition" not in original["questions"][0]


def test_loading_preserves_existing_invalid_identity(tmp_path):
    doc, qid, path = _legacy(tmp_path)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    q["answer"]["answer_version"] = {"fingerprint": "forged"}
    q["edition"] = {"fingerprint": "forged"}
    original = copy.deepcopy(q)
    loaded = m.loads_model(json.dumps(doc))
    assert next(x for x in loaded["questions"] if x["id"] == qid) == original


@pytest.mark.parametrize("reader", ["path", "text"])
def test_model_readers_refuse_duplicate_values_before_migration(tmp_path, reader):
    raw = '{"questions": [], "params": [{"value": 3, "value": 30}]}'
    path = tmp_path / "model.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(m.DuplicateJsonKey, match="value"):
        m.load_model(path) if reader == "path" else m.loads_model(raw)
    assert path.read_text(encoding="utf-8") == raw


def test_http_book_reader_receives_migrated_legacy_before_work(tmp_path, monkeypatch):
    _, qid, path = _legacy(tmp_path)
    raw = path.read_bytes()
    captured = []
    monkeypatch.setattr(webui, "_run_dir", lambda run_id: tmp_path)
    monkeypatch.setattr(webui, "_read_status", lambda run_dir: {"state": "извлечено"})
    def book_reader(book, doc):
        captured.append(copy.deepcopy(doc))
        raise ValueError("intentional stop before any stage or channel")
    monkeypatch.setattr(webui.answers_mod, "read_book", book_reader)
    with pytest.raises(HTTPException) as error:
        webui._upload_answers_bytes("test-run", "answers.xlsx", b"synthetic book")
    assert error.value.status_code == 400
    assert len(captured) == 1
    _assert_migrated(captured[0], qid)
    assert path.read_bytes() == raw


def test_readiness_uses_migrated_model_without_writing(tmp_path, monkeypatch):
    _, qid, path = _legacy(tmp_path)
    raw = path.read_bytes()
    captured = []
    def readiness(doc):
        captured.append(copy.deepcopy(doc))
        return {}
    monkeypatch.setattr(m, "readiness", readiness)
    monkeypatch.setattr(m, "readiness_matrix", lambda doc: {})
    assert webui._readiness_of(tmp_path) is not None
    _assert_migrated(captured[0], qid)
    assert path.read_bytes() == raw


def test_effective_parameter_view_migrates_same_bytes_it_hashes(tmp_path, monkeypatch):
    from cdpm import parameter_review
    _, qid, path = _legacy(tmp_path)
    raw = path.read_bytes()
    captured = []
    monkeypatch.setattr(webui, "_run_dir", lambda run_id: tmp_path)
    def project(doc):
        captured.append(copy.deepcopy(doc))
        return {"parameters": []}
    monkeypatch.setattr(parameter_review, "from_model", project)
    response = webui.run_effective_parameters("test-run")
    _assert_migrated(captured[0], qid)
    assert json.loads(response.body)["model_sha256"] == hashlib.sha256(raw).hexdigest()
    assert path.read_bytes() == raw


def test_effective_parameter_view_refuses_duplicate_json(tmp_path, monkeypatch):
    path = tmp_path / "extract" / "model.json"
    path.parent.mkdir()
    monkeypatch.setattr(webui, "_run_dir", lambda run_id: tmp_path)
    path.write_text('{"rules": [], "questions": [], "params": []}', encoding="utf-8")
    assert webui.run_effective_parameters("test-run").status_code == 200
    path.write_text('{"rules": [], "questions": [], "params": [], "params": []}', encoding="utf-8")
    with pytest.raises(HTTPException) as error:
        webui.run_effective_parameters("test-run")
    assert error.value.status_code == 409
    assert "params" in error.value.detail


def test_duplicate_model_refused_before_creating_answer_revision(tmp_path, monkeypatch):
    _, _, path = _legacy(tmp_path)
    path.write_text('{"rules": [], "questions": [], "meta": {}, "meta": {}}', encoding="utf-8")
    monkeypatch.setattr(webui, "_run_dir", lambda run_id: tmp_path)
    monkeypatch.setattr(webui, "_read_status", lambda run_dir: {"state": "извлечено"})
    with pytest.raises(HTTPException) as error:
        webui._upload_answers_bytes("test-run", "answers.xlsx", b"not a book")
    assert error.value.status_code == 409
    assert "meta" in error.value.detail
    assert not (tmp_path / "answers").exists()


@pytest.mark.parametrize("route", ["data_review", "data_to_anketa"])
def test_cli_writer_receives_migrated_model_before_processing(tmp_path, monkeypatch, route):
    from cdpm import data_review, data_to_anketa
    _, qid, path = _legacy(tmp_path)
    raw = path.read_bytes()
    report = tmp_path / "report.json"
    report.write_text("{}", encoding="utf-8")
    captured = []
    class StopBeforeWork(Exception):
        pass
    def stop(doc, *args, **kwargs):
        captured.append(copy.deepcopy(doc))
        raise StopBeforeWork
    args = [str(path), str(report), "--out-dir", str(tmp_path / "out")]
    if route == "data_review":
        module = data_review
        monkeypatch.setattr(module, "run_review", stop)
        monkeypatch.setattr(module.dr, "load_report", lambda path: {})
        monkeypatch.setattr(module.llm_mod, "cosmos_llm", lambda **kwargs: object())
    else:
        module = data_to_anketa
        monkeypatch.setattr(module, "promote_to_question", stop)
        args += ["--checks", "probe", "--title", "Test", "--text", "Test", "--options", "a=yes"]
    with pytest.raises(StopBeforeWork):
        module.main(args)
    _assert_migrated(captured[0], qid)
    assert path.read_bytes() == raw


def test_smt_bad_model_does_not_claim_running_or_build_channel(tmp_path, monkeypatch):
    import inspect
    _, _, path = _legacy(tmp_path)
    path.write_text('{"rules": [], "questions": [], "meta": {}, "meta": {}}', encoding="utf-8")
    states = []
    monkeypatch.setattr(webui, "_run_dir", lambda run_id: tmp_path)
    monkeypatch.setattr(webui, "_read_status", lambda run_dir: {})
    monkeypatch.setattr(webui, "_z3_problem", lambda: None)
    monkeypatch.setattr(webui, "_update_status", lambda run_dir, **state: states.append(state))
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid input must not build any live channel")
    monkeypatch.setattr(webui, "_channel_for", forbidden)
    with pytest.raises(HTTPException) as error:
        inspect.unwrap(webui.start_smt_verify)("test-run")
    assert error.value.status_code == 409
    assert "meta" in error.value.detail
    assert states == [{"smt_state": "отказ SMT-проверки"}]
