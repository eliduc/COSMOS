"""K-3: actual start_extract routing, not a full extraction/gate verdict.

The handler, file policies, role inheritance/slots, channel identities and
llm.channel scope guards are real. The thread runs synchronously; the two
extraction-stage bodies only exercise their received channels. Completion
transports and post-extraction publishing/rendering/gate are isolated fakes.
No HTTP, subprocess, real model, tenant input or catalogue release is used.
"""
from copy import deepcopy
import json

import pytest

from cdpm import external_llm, llm, providers, webui
from webapp import llm_backend


def _dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _layout(*, local_only=False):
    out = {}
    for role in ("extractor", "logic_verifier", "second_reader"):
        channels = {"local": {"provider": "local", "model": "local-" + role}}
        if not local_only:
            channels["cloud-country"] = {"provider": "local", "model": "country-" + role}
            channels["cloud"] = {
                "provider": "gemini" if role == "second_reader" else "openai",
                "model": "gemini-synthetic" if role == "second_reader" else "gpt-synthetic"}
        out[role] = {"channels": channels}
    return out


def _harness(tmp_path, monkeypatch, policies, *, scope="any", agents=None):
    run = tmp_path / "runs" / "run-0001"
    run.mkdir(parents=True)
    monkeypatch.setenv("CDPM_RUNS_DIR", str(run.parent))
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", "")
    monkeypatch.setenv("CDPM_LLM_ATTEMPTS", "1")
    monkeypatch.setenv("CDPM_LLM_BACKOFF_S", "0")
    monkeypatch.delenv("CDPM_SCOPE_FORCE", raising=False)
    files = [f"source-{i}.txt" for i in range(len(policies))]
    _dump(run / "status.json", {
        "state": "заморожено", "customer": "Synthetic K3", "files": files,
        "kit": {"data_class": "synthetic", "llm_scope": scope}})
    _dump(run / "freeze" / "manifest.json", {
        "files": [{"sid": f"S{i}", "file": name} for i, name in enumerate(files)]})
    _dump(run / "files-config.json", {
        "files": {name: {"processing": policy, "note": "synthetic"}
                  for name, policy in zip(files, policies)}})
    _dump(run / "agents.json", _layout() if agents is None else agents)
    before = {name: (run / name).read_bytes()
              for name in ("agents.json", "files-config.json", "freeze/manifest.json")}
    seen, transports = {}, []

    # Keep _channel_for, role_channel, llm.channel and resolve_backend real.
    # Only completions are fakes; no API key or machine settings are read.
    monkeypatch.setattr(providers, "provider_key", lambda _provider: None)
    monkeypatch.setattr(providers, "load_settings", lambda: {})
    monkeypatch.setattr(llm_backend, "ollama_model", lambda: "synthetic-default-local")
    monkeypatch.setattr(llm_backend._settings, "has_api_key", lambda: False)

    def local_complete(prompt, **kwargs):
        transports.append((kwargs["backend"], kwargs.get("model"), prompt))
        return {"ok": True, "text": "synthetic-reply", "backend": kwargs["backend"],
                "model": kwargs.get("model")}

    def external_complete(provider, **kwargs):
        transports.append((provider, kwargs.get("model"), kwargs["prompt"]))
        return "synthetic-reply"

    monkeypatch.setattr(llm_backend, "complete", local_complete)
    monkeypatch.setattr(external_llm, "complete", external_complete)
    monkeypatch.setattr(webui, "_journal_channel", lambda _run, fn, *a, **kw: fn)
    monkeypatch.setattr(webui, "_start_stage_thread", lambda _run, work, **kw: work())

    def exercise(label, channel):
        seen[label] = json.loads(llm.channel_identity(channel))
        assert channel("synthetic-prompt:" + label) == "synthetic-reply"

    def run_extract(freeze_dir, extract_dir, channel, customer, **kwargs):
        assert freeze_dir == run / "freeze" and extract_dir == run / "extract"
        assert customer == "Synthetic K3"
        kwargs["control"]()
        exercise("run_extract", channel)
        for sid, per_file in sorted(kwargs["llm_by_sid"].items()):
            exercise("file:" + sid, per_file)
        exercise("cross", kwargs["cross_llm"])
        seen["second_note"] = kwargs["second_note"]
        if kwargs["second_llm"] is not None:
            exercise("second_reader", kwargs["second_llm"])
        return {"routing_harness": True}

    def produce_logic(extract_dir, channel, *, verifier, **kwargs):
        assert extract_dir == run / "extract"
        kwargs["control"]()
        exercise("produce_logic", channel)
        exercise("logic_verifier", verifier)
        return {"routing_harness": True}

    monkeypatch.setattr(webui.extract, "run_extract", run_extract)
    monkeypatch.setattr(webui.extract, "produce_logic", produce_logic)
    monkeypatch.setattr(webui.m_mod, "load_model", lambda _path: {"rules": []})
    monkeypatch.setattr(webui.m_mod, "publish_model", lambda *a, **kw: None)
    monkeypatch.setattr(webui.anketa_xlsx_mod, "render_anketa_xlsx", lambda *a, **kw: None)
    monkeypatch.setattr(webui.gate_extract, "check", lambda *a, **kw: ([], {}))
    # Bypass only the action-journal decorator, as existing handler tests do.
    # _work's real success/error status handling and _run_scope remain active.
    webui.start_extract.__wrapped__("run-0001")
    status = webui._read_status(run)
    assert {name: (run / name).read_bytes() for name in before} == before
    return status, seen, transports


@pytest.mark.parametrize("policies,scope,only_local,expected", [
    (("local", "local"), "local-only", True, "local"),
    (("cloud", "local"), "any", False, "local"),
    (("cloud-country", "cloud"), "any", False, "cloud-country"),
    (("cloud", "cloud"), "any", False, "cloud"),
], ids=["local-only-declared-local-slots", "mixed-strictest-local",
        "mixed-strictest-country", "healthy-all-cloud"])
def test_start_extract_preserves_processing_class_at_all_shared_stage_calls(
        tmp_path, monkeypatch, policies, scope, only_local, expected):
    status, seen, transports = _harness(tmp_path, monkeypatch, policies,
        scope=scope, agents=_layout(local_only=only_local))
    assert status.get("error") is None, status
    assert status["state"] == "извлечено", status
    for label in ("run_extract", "cross", "produce_logic", "logic_verifier"):
        assert seen[label]["klass"] == expected, (label, seen[label])
        assert seen[label]["config_source"] == "run-explicit"
    for index, policy in enumerate(policies):
        assert seen[f"file:S{index}"]["klass"] == policy
    if expected == "cloud":
        assert seen["second_reader"]["klass"] == "cloud"
        assert seen["second_reader"]["effective"]["provider"] == "gemini"
        assert seen["second_note"] is None
    else:
        assert "second_reader" not in seen and seen["second_note"]
        prefix = "local-" if expected == "local" else "country-"
        for label in ("run_extract", "cross", "produce_logic", "logic_verifier"):
            assert seen[label]["effective"]["provider"] == "local"
            assert seen[label]["effective"]["model"].startswith(prefix)
    assert transports
    if scope == "local-only":
        assert {provider for provider, _model, _prompt in transports} == {"local"}


@pytest.mark.parametrize("policy", ["cloud", "cloud-country"])
def test_start_extract_wrong_cloud_provider_under_local_scope_is_refused_before_transport(
        tmp_path, monkeypatch, policy):
    agents = _layout()
    agents["extractor"]["channels"][policy] = {"provider": "openai", "model": "gpt-synthetic"}
    status, seen, transports = _harness(tmp_path, monkeypatch, (policy,),
                                       scope="local-only", agents=agents)
    assert status["state"] == "отказ извлечения", status
    assert "local-only" in status.get("error", ""), status
    assert seen == {} and transports == []


@pytest.mark.parametrize("agents", [{}, _layout(local_only=True)],
                         ids=["no-layout", "missing-cloud-slots"])
def test_start_extract_missing_cloud_layout_remains_fail_closed(tmp_path, monkeypatch, agents):
    status, seen, transports = _harness(tmp_path, monkeypatch, ("cloud",),
                                       agents=deepcopy(agents))
    assert status["state"] == "отказ извлечения", status
    error = status.get("error", "").lower()
    assert "раскладк" in error and ("не объявлен" in error or "не задан" in error), status
    assert seen == {} and transports == []


@pytest.mark.parametrize("policies,local_slots_only", [
    (("local",), True),
    (("local", "cloud"), False),
    (("cloud-country", "cloud"), False),
], ids=["no-cloud-slots", "mixed-local", "mixed-country"])
def test_second_reader_policy_refusal_does_not_claim_unused_cloud_family(
        tmp_path, monkeypatch, policies, local_slots_only):
    agents = _layout(local_only=local_slots_only)
    if not local_slots_only:
        agents["second_reader"]["channels"]["cloud"] = {
            "provider": "openai", "model": "gpt-synthetic-second"}
    status, seen, _ = _harness(tmp_path, monkeypatch, policies, agents=agents)
    assert status["state"] == "извлечено", status
    assert "second_reader" not in seen
    assert seen["second_note"] == (
        f"политика файлов {sorted(set(policies))}: второе чтение "
        "облачным читателем невозможно")


def test_second_reader_all_cloud_still_refuses_same_family(
        tmp_path, monkeypatch):
    agents = _layout()
    agents["second_reader"]["channels"]["cloud"] = {
        "provider": "openai", "model": "gpt-synthetic-second"}
    status, seen, _ = _harness(tmp_path, monkeypatch, ("cloud",), agents=agents)
    assert status["state"] == "извлечено", status
    assert "second_reader" not in seen
    assert "одно семейство моделей 'gpt'" in seen["second_note"]
    assert "слой 2 не независим" in seen["second_note"]
    assert "политика файлов" not in seen["second_note"]
