"""Explicit A12 action: real handlers/core/policies, fake completion transport.

No HTTP, model service, solver or semantic/expert verdict substitute. Sources
are synthetic local files frozen by the real producer. Transport replies are
authored test data, not independent expert acceptance or a live LLM measure.
"""
import copy
import hashlib
import json
import platform

import pytest
from fastapi import HTTPException

from cdpm import obligations as ob
from test_cdpm_a12_obligations_integration import _case
from test_cdpm_a12_obligations_handler import _endpoint


@pytest.fixture(autouse=True)
def _passport_cache_isolated():
    """РВ-140 п. 6: тест подменяет `run_passport._git_head` (коммит None),
    а `_stable_environment` кэширует его на весь процесс — холодный кэш,
    наполненный здесь, отравлял последующие файлы («коммит кода панели
    неизвестен» → 400 на «Новый прогон», test_cdpm_answers_audit). Кэш
    снимается до и после каждого теста файла."""
    from cdpm import run_passport
    run_passport._stable_environment.cache_clear()
    yield
    run_passport._stable_environment.cache_clear()


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _reading_case(tmp_path, monkeypatch, *, with_answer=False):
    # Journal environment prose only: cold platform.platform() can spawn
    # cmd.exe on Windows. Keep the process guard and real journal untouched.
    monkeypatch.setattr(platform, "platform", lambda: "offline-test-platform-not-probed")
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", "")
    monkeypatch.setenv("CDPM_CALL_JOURNAL", "1")
    monkeypatch.setenv("CREW_TASK_INBOX", "")
    run, ledger = _case(tmp_path, with_answer=with_answer)
    from cdpm import webui, external_llm, run_passport
    from webapp import llm_backend

    # Passport metadata is outside this handler's verdict. Keep a cold,
    # isolated selection offline too, without relying on another test's cache.
    monkeypatch.setattr(run_passport, "_git_head", lambda repo: {
        "commit": None, "short": "offline-fixture", "dirty": None})

    status = {"state": "извлечено", "files": ["synthetic-rules.md"], "data_files": [],
              "kit": {"data_class": "synthetic", "llm_scope": "any"}}
    _write(run / "status.json", status)
    _write(run / "files-config.json", {"files": {"synthetic-rules.md": {"processing": "local"}}})
    agents = {"form_version": 1, "extractor": {"channels": {
        "local": {"provider": "local", "model": "qwen3.6:8b"},
        "cloud": {"provider": "cloud", "model": "claude-sonnet-4"}}},
        "second_reader": {"channels": {
            "local": {"provider": "local", "model": "llama3:8b"},
            "cloud": {"provider": "openai", "model": "gpt-4.1"}}}}
    _write(run / "agents.json", agents)
    calls, hooks = [], []

    def transport(prompt, *, backend=None, model=None, **kwargs):
        calls.append({"prompt": prompt, "backend": backend, "model": model})
        unit = json.loads(prompt)["input"]
        # Select authored fixture content by the exact unit, never a model
        # verdict. Answer fixture keeps its declared conditional component.
        items = [copy.deepcopy(item) for item in ledger["readers"][0]["obligations"]
                 if item["sources"][0]["unit"] == unit["unit"]]
        value = {"unit": unit["unit"], "obligations": items}
        if not items:
            assert unit["text"] == "Дополнительный синтетический заголовок."
            value.update(none_found=True, reason="Authored fixture reading: this unit is a heading only")
        reply = json.dumps(value, ensure_ascii=False)
        if hooks:
            hooks.pop(0)()
        return {"ok": True, "text": reply, "backend": backend, "model": model}

    def external(provider, *, prompt, model, **kwargs):
        return transport(prompt, backend=provider, model=model)["text"]

    # Actual scope, role layout, identity stamping and call journal are kept.
    monkeypatch.setattr(llm_backend, "complete", transport)
    monkeypatch.setattr(external_llm, "complete", external)
    return {"webui": webui, "run": run, "ledger": ledger, "calls": calls,
            "hooks": hooks, "agents": agents, "status": status}


@pytest.fixture
def reading(tmp_path, monkeypatch):
    return _reading_case(tmp_path, monkeypatch)


@pytest.fixture
def answer_reading(tmp_path, monkeypatch):
    return _reading_case(tmp_path, monkeypatch, with_answer=True)


def _protected(run):
    names = [ob.LEDGER_REL, "extract/model.json", "freeze/manifest.json",
             "freeze/clauses/S1.json", "src/synthetic-rules.md"]
    return {name: (run / name).read_bytes() for name in names}


def _read(case, **changes):
    run = case["run"]
    body = {"approval": True, "expected_sha256": hashlib.sha256((run / ob.LEDGER_REL).read_bytes()).hexdigest()}
    body.update(changes)
    return _endpoint(case["webui"], "/read", "POST")(run.name, body)


def _cloud(case, data_class="synthetic"):
    status = copy.deepcopy(case["status"])
    status["kit"] = {"data_class": data_class, "llm_scope": "any", "llm_scope_basis": "explicit fixture basis"}
    _write(case["run"] / "status.json", status)
    _write(case["run"] / "files-config.json", {"files": {"synthetic-rules.md": {"processing": "cloud"}}})


def test_actual_reading_returns_candidate_without_publishing_and_keeps_journal(reading):
    w, run = reading["webui"], reading["run"]
    existing = copy.deepcopy(reading["ledger"])
    existing["expert_review"] = {
        "reviewer": "synthetic prior reviewer", "decision": "accepted", "reason": "authored prior act",
        "snapshot_sha256": ob.fingerprint(existing["snapshot"]),
        "scope_sha256": ob.fingerprint(existing["scope"]),
        "reviewed_content_sha256": ob.review_content_sha256(existing), "issues": []}
    old = _endpoint(w, "", "GET")(run.name)
    _endpoint(w, "", "PUT")(run.name, {"text": json.dumps(existing), "expected_sha256": old["sha256"]})
    before = _protected(run)
    result = _read(reading)
    assert result["saved"] is False
    assert _protected(run) == before
    assert len(reading["calls"]) == 2
    assert {c["backend"] for c in reading["calls"]} == {"local"}
    assert {c["model"] for c in reading["calls"]} == {"qwen3.6:8b", "llama3:8b"}
    candidate = json.loads(result["text"])
    assert candidate["snapshot"] == ob.capture_run_snapshot(run)
    assert [r["id"] for r in candidate["readers"]] == ["LEFT", "RIGHT"]
    assert candidate["links"] == [] and candidate["expert_review"] is None
    assert result["report"]["form"] == "valid"
    assert result["report"]["freshness"] == "current"
    assert result["report"]["meaning"] == "not_proven"
    assert result["report"]["independence"] != "verified"
    assert result["report"]["expert_review"] == {"status": "not_reviewed"}
    assert all("R-LIMIT" not in c["prompt"] and "R-LICENCE" not in c["prompt"] for c in reading["calls"])
    assert w.call_journal.count(run) == 2
    records = [json.loads(line) for line in (run / "journal/calls/index.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["role"] for r in records} == {"extractor", "second_reader"}
    assert all(r.get("identity") for r in records)
    assert w._read_status(run)["state"] == "извлечено"
    assert "obligations_reading_token" not in w._read_status(run)
    assert result["evidence"]
    saved = _endpoint(w, "", "PUT")(run.name, {"text": result["text"], "expected_sha256": result["expected_sha256"]})
    assert saved["text"] == result["text"]  # Separate explicit CAS, not a side effect of read.


def test_get_page_prepare_and_export_never_call_readers(reading):
    w, run = reading["webui"], reading["run"]
    before = _protected(run)
    got = _endpoint(w, "", "GET")(run.name)
    _endpoint(w, "", "GET")(run.name, download=1)
    _endpoint(w, "/prepare", "POST")(run.name, {"text": got["text"]})
    page = w.obligations_page(run.name).body.decode()
    for marker in ("reading-approval", "use-candidate", "api+'/read'", "не экспертная приёмка",
                   "proprietary/unknown", "lossless-маскирования", "только явно выбранные точные версии ответов",
                   'id="answer-sources"', '"revision":1'):
        assert marker in page
    assert reading["calls"] == [] and _protected(run) == before


@pytest.mark.parametrize("body", [{"approval": False}, {"approval": 1}, {"expected_sha256": "0" * 64}])
def test_approval_and_ledger_cas_refuse_before_any_call(reading, body):
    before = _protected(reading["run"])
    with pytest.raises(HTTPException) as exc:
        _read(reading, **body)
    assert exc.value.status_code in (409, 422)
    assert reading["calls"] == [] and _protected(reading["run"]) == before


@pytest.mark.parametrize("line", ["state", "data_state", "smt_state", "catalog_state"])
def test_busy_lines_prevent_reading_without_changing_the_run(reading, line):
    status = dict(reading["status"], **{line: "другая стадия идёт"})
    _write(reading["run"] / "status.json", status)
    with pytest.raises(HTTPException) as exc:
        _read(reading)
    assert exc.value.status_code == 409
    assert reading["calls"] == []
    assert reading["webui"]._read_status(reading["run"])[line] == "другая стадия идёт"


@pytest.mark.parametrize("initial", [True, False])
def test_stop_is_not_cleared_and_prevents_next_call(reading, initial):
    stop = lambda: _write(reading["run"] / "control.json", {"command": "stop"})
    if initial:
        stop()
    else:
        reading["hooks"].append(stop)
    before = _protected(reading["run"])
    with pytest.raises(HTTPException) as exc:
        _read(reading)
    assert exc.value.status_code == 409
    assert len(reading["calls"]) == (0 if initial else 1)
    assert _protected(reading["run"]) == before
    assert reading["webui"]._read_command(reading["run"]) == "stop"
    assert reading["webui"]._read_status(reading["run"])["state"] == "извлечено"


@pytest.mark.parametrize("name", ["extract/model.json", "freeze/manifest.json", ob.LEDGER_REL,
                                 "files-config.json", "agents.json", "kit"])
def test_mid_call_input_or_policy_changes_abort_candidate_before_next_reader(reading, name):
    run = reading["run"]
    def change():
        if name == "kit":
            st = reading["webui"]._read_status(run)
            st["kit"]["llm_scope"] = "local-only"
            _write(run / "status.json", st)
        elif name == "files-config.json":
            _write(run / name, {"files": {"synthetic-rules.md": {"processing": "cloud"}}})
        elif name == "agents.json":
            changed = copy.deepcopy(reading["agents"])
            changed["extractor"]["channels"]["local"]["model"] = "qwen3.6:32b"
            _write(run / name, changed)
        else:
            path = run / name
            path.write_bytes(path.read_bytes() + b" ")
    reading["hooks"].append(change)
    with pytest.raises(HTTPException) as exc:
        _read(reading)
    assert exc.value.status_code == 409
    assert "изменил" in str(exc.value.detail)
    assert len(reading["calls"]) == 1
    assert reading["webui"]._read_status(run)["state"] == "извлечено"
    assert reading["webui"]._read_status(run)["obligations_reading"]["saved"] is False


@pytest.mark.parametrize("left,right", [
    ("qwen3.6:8b", "qwen3.6:8b"),
    ("qwen3.6:8b", "qwen3.6:32b"),
    ("qwen3.6:8b", "qwen2.5:7b"),
    ("llama3:8b", "llama3:70b"),
    ("llama3:8b", "llama3.1:70b-instruct"),
    ("Qwen/Qwen3.6-8B", "qwen3.6:32b"),
])
def test_same_actual_model_family_is_not_two_independent_readers(reading, left, right):
    agents = copy.deepcopy(reading["agents"])
    agents["extractor"]["channels"]["local"]["model"] = left
    agents["second_reader"]["channels"]["local"]["model"] = right
    _write(reading["run"] / "agents.json", agents)
    with pytest.raises(HTTPException) as exc:
        _read(reading)
    assert exc.value.status_code == 422
    assert reading["calls"] == []


def test_transport_stop_remains_a_stop_and_releases_stage_without_saving(reading):
    w, run = reading["webui"], reading["run"]
    before = _protected(run)
    def stop_in_transport():
        _write(run / "control.json", {"command": "stop"})
        raise w.StopRequested("чтение обязательств")
    reading["hooks"].append(stop_in_transport)
    with pytest.raises(HTTPException) as exc:
        _read(reading)
    assert exc.value.status_code == 409
    assert "остановлено" in exc.value.detail
    assert len(reading["calls"]) == 1
    assert _protected(run) == before
    assert w._read_command(run) == "stop"
    status = w._read_status(run)
    assert status["state"] == "извлечено"
    assert "obligations_reading_token" not in status
    assert status["obligations_reading"] == {"state": "остановлено", "saved": False}


@pytest.mark.parametrize("data_class", ["synthetic", "open"])
def test_explicit_open_cloud_policy_uses_actual_existing_role_channels(reading, data_class):
    _cloud(reading, data_class)
    result = _read(reading)
    assert result["saved"] is False
    assert {c["backend"] for c in reading["calls"]} == {"cloud", "openai"}
    assert {c["model"] for c in reading["calls"]} == {"claude-sonnet-4", "gpt-4.1"}


@pytest.mark.parametrize("data_class", ["proprietary", None])
def test_free_text_cloud_basis_does_not_invent_lossless_masking(reading, data_class):
    _cloud(reading, data_class)
    with pytest.raises(HTTPException) as exc:
        _read(reading)
    assert exc.value.status_code == 409
    assert "A12 masking unsupported" in exc.value.detail
    assert reading["calls"] == []


def test_later_cloud_unit_is_refused_before_an_earlier_local_unit_is_sent(reading):
    from cdpm import freeze
    run = reading["run"]
    second = run / "src" / "second-synthetic.md"
    second.write_text("Второй синтетический документ.\n", encoding="utf-8")
    freeze.freeze([run / "src/synthetic-rules.md", second], run / "freeze")
    st = dict(reading["status"])
    st["files"] = ["synthetic-rules.md", second.name]
    st["kit"] = {"data_class": "proprietary", "llm_scope": "any", "llm_scope_basis": "fixture basis"}
    _write(run / "status.json", st)
    _write(run / "files-config.json", {"files": {
        "synthetic-rules.md": {"processing": "local"}, second.name: {"processing": "cloud"}}})
    with pytest.raises(HTTPException) as exc:
        _read(reading)
    assert exc.value.status_code == 409 and "A12 masking unsupported" in exc.value.detail
    assert reading["calls"] == []


def test_kit_local_only_and_undeclared_cloud_layout_are_real_pre_call_refusals(reading):
    _cloud(reading)
    st = reading["webui"]._read_status(reading["run"])
    st["kit"]["llm_scope"] = "local-only"
    _write(reading["run"] / "status.json", st)
    with pytest.raises(HTTPException) as denied:
        _read(reading)
    assert denied.value.status_code == 409 and "допуск" in denied.value.detail
    st["kit"]["llm_scope"] = "any"
    _write(reading["run"] / "status.json", st)
    (reading["run"] / "agents.json").unlink()
    with pytest.raises(HTTPException) as absent:
        _read(reading)
    assert absent.value.status_code == 409 and "раскладка" in absent.value.detail
    assert reading["calls"] == []


def test_second_explicit_action_is_rejected_while_first_owns_run(reading):
    refusals = []
    def second():
        try:
            _read(reading)
        except HTTPException as exc:
            refusals.append(exc.status_code)
    reading["hooks"].append(second)
    assert _read(reading)["saved"] is False
    assert refusals == [409] and len(reading["calls"]) == 2


def test_checkpoint_construction_failure_releases_claimed_stage(reading, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic checkpoint construction failure")
    monkeypatch.setattr(reading["webui"], "_make_checkpoint", unavailable)
    before = _protected(reading["run"])
    with pytest.raises(RuntimeError, match="synthetic checkpoint construction failure"):
        _read(reading)
    status = reading["webui"]._read_status(reading["run"])
    assert status["state"] == "извлечено" and "obligations_reading_token" not in status
    assert reading["calls"] == [] and _protected(reading["run"]) == before


def _selected_answers(case):
    return [copy.deepcopy(unit["source"]) for unit in case["ledger"]["snapshot"]["units"]
            if unit["kind"] == "answer"]


def test_explicit_exact_answer_is_read_with_documents_and_keeps_its_condition(answer_reading):
    case, run = answer_reading, answer_reading["run"]
    selected = _selected_answers(case)
    # Establish a lawful producer/provenance input before exercising the API.
    actual = ob._run_inputs(run, answer_sources=selected)
    snapshot = ob.capture_snapshot(**actual)
    assert {u["kind"] for u in snapshot["units"]} == {"document", "answer"}
    before = _protected(run)
    result = _read(case, answer_sources=selected)
    candidate = json.loads(result["text"])
    assert candidate["snapshot"] == snapshot
    assert result["saved"] is False and _protected(run) == before
    assert len(case["calls"]) == 4
    for reader in candidate["readers"]:
        answer = next(item for item in reader["obligations"]
                      if item["sources"][0]["unit"] == ob.answer_unit(selected[0]))
        assert answer["conditions"] == [{"id": "C1", "text": "только при действующем допуске"}]
    prompts = [json.loads(call["prompt"])["input"] for call in case["calls"]]
    answers = [p for p in prompts if p["kind"] == "answer"]
    assert len(answers) == 2
    assert all(p["source"] == selected[0] for p in answers)
    assert all(p["text"] == "Назначение разрешено только при действующем допуске" for p in answers)
    assert result["report"]["form"] == "valid" and result["report"]["freshness"] == "current"
    assert result["report"]["meaning"] == "not_proven"
    saved = _endpoint(case["webui"], "", "PUT")(run.name, {
        "text": result["text"], "expected_sha256": result["expected_sha256"]})
    assert json.loads(saved["text"])["snapshot"] == snapshot


def test_existing_answer_is_not_selected_when_explicit_list_is_empty(answer_reading):
    result = _read(answer_reading, answer_sources=[])
    assert {u["kind"] for u in json.loads(result["text"])["snapshot"]["units"]} == {"document"}
    assert len(answer_reading["calls"]) == 2
    assert all(json.loads(c["prompt"])["input"]["kind"] == "document" for c in answer_reading["calls"])


@pytest.mark.parametrize("damage", ["revision", "edition_fingerprint", "answer_fingerprint", "unknown_id",
                                   "extra_field", "not_list", "duplicate"])
def test_selected_source_never_falls_back_to_latest_answer(answer_reading, damage):
    selected = _selected_answers(answer_reading)
    if damage == "revision": selected[0]["question"]["revision"] += 1
    elif damage == "edition_fingerprint": selected[0]["question"]["fingerprint"] = "f" * 12
    elif damage == "answer_fingerprint": selected[0]["version"]["fingerprint"] = "f" * 12
    elif damage == "unknown_id": selected[0]["question"]["id"] = "Q-NOT-EXISTING"
    elif damage == "extra_field": selected[0]["latest"] = True
    elif damage == "not_list": selected = None
    else: selected.append(copy.deepcopy(selected[0]))
    before = _protected(answer_reading["run"])
    with pytest.raises(HTTPException) as exc:
        _read(answer_reading, answer_sources=selected)
    assert exc.value.status_code in (409, 422)
    assert answer_reading["calls"] == [] and _protected(answer_reading["run"]) == before


@pytest.mark.parametrize("when", ["before", "during"])
def test_real_answer_version_change_refuses_old_identity_or_inflight_candidate(answer_reading, when):
    from cdpm import answer_versions
    run = answer_reading["run"]
    selected = _selected_answers(answer_reading)
    def revise():
        path = run / "extract/model.json"
        doc = json.loads(path.read_bytes())
        answer = doc["questions"][0]["answer"]
        old = copy.deepcopy(answer)
        answer["raw"] = "Назначение запрещено без повторного подтверждения допуска"
        answer_versions.stamp(answer, old, {"received_at": None, "book_sha256": None})
        assert answer_versions.problems(answer) == []
        _write(path, doc)
    if when == "before": revise()
    else: answer_reading["hooks"].append(revise)
    old_ledger = (run / ob.LEDGER_REL).read_bytes()
    with pytest.raises(HTTPException) as exc:
        _read(answer_reading, answer_sources=selected)
    assert exc.value.status_code == 409
    assert len(answer_reading["calls"]) == (0 if when == "before" else 1)
    assert (run / ob.LEDGER_REL).read_bytes() == old_ledger


def test_legal_new_question_edition_during_read_invalidates_captured_model(answer_reading):
    from cdpm import question_editions
    run = answer_reading["run"]
    def expand():
        path = run / "extract/model.json"
        doc = json.loads(path.read_bytes())
        new = question_editions.expand(doc, doc["questions"][0], ["R-LIMIT"],
                                       content={"text": "Уточните условие допуска для расширенной области"})
        assert new["edition"]["revision"] == 2 and question_editions.problems(new) == []
        _write(path, doc)
    answer_reading["hooks"].append(expand)
    with pytest.raises(HTTPException) as exc:
        _read(answer_reading, answer_sources=_selected_answers(answer_reading))
    assert exc.value.status_code == 409 and "изменил" in exc.value.detail
    assert len(answer_reading["calls"]) == 1


def test_answer_uses_strictest_frozen_file_policy_while_documents_keep_own_routes(answer_reading):
    from cdpm import freeze
    run = answer_reading["run"]
    second = run / "src/heading.md"
    second.write_text("Дополнительный синтетический заголовок.\n", encoding="utf-8")
    freeze.freeze([run / "src/synthetic-rules.md", second], run / "freeze")
    status = copy.deepcopy(answer_reading["status"])
    status["files"].append(second.name)
    _write(run / "status.json", status)
    _write(run / "files-config.json", {"files": {"synthetic-rules.md": {"processing": "cloud"},
                                                  second.name: {"processing": "local"}}})
    result = _read(answer_reading, answer_sources=_selected_answers(answer_reading))
    assert result["report"]["form"] == "valid" and result["saved"] is False
    assert len(answer_reading["calls"]) == 6
    for call in answer_reading["calls"]:
        unit = json.loads(call["prompt"])["input"]
        if unit["kind"] == "answer" or unit.get("sid") == "S2":
            assert call["backend"] == "local"
        else:
            assert call["backend"] in {"cloud", "openai"}
    assert json.loads(result["text"])["scope"]["excluded"] == []


def test_answer_policy_missing_frozen_file_is_refused_not_defaulted_cloud(answer_reading):
    st = copy.deepcopy(answer_reading["status"])
    st["files"] = []
    _write(answer_reading["run"] / "status.json", st)
    with pytest.raises(HTTPException) as exc:
        _read(answer_reading, answer_sources=_selected_answers(answer_reading))
    assert exc.value.status_code == 409 and "политику всех замороженных файлов" in exc.value.detail
    assert answer_reading["calls"] == []


@pytest.mark.parametrize("external", [False, True])
def test_explicit_answers_do_not_bypass_proprietary_transfer_policy(answer_reading, external):
    if external:
        _cloud(answer_reading, "proprietary")
    else:
        st = copy.deepcopy(answer_reading["status"])
        st["kit"] = {"data_class": "proprietary", "llm_scope": "local-only"}
        _write(answer_reading["run"] / "status.json", st)
    selected = _selected_answers(answer_reading)
    if external:
        with pytest.raises(HTTPException) as exc:
            _read(answer_reading, answer_sources=selected)
        assert exc.value.status_code == 409 and "masking unsupported" in exc.value.detail
        assert answer_reading["calls"] == []
    else:
        assert _read(answer_reading, answer_sources=selected)["saved"] is False
        assert len(answer_reading["calls"]) == 4
        assert {c["backend"] for c in answer_reading["calls"]} == {"local"}
