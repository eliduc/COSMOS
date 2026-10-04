"""K4: local context contract through real role/channel/backend request building.

Only urllib's response boundary is fake. No token-count heuristic is an oracle:
the server is responsible for its tokenizer and enforcement of truncate/shift.
These tests prove request preservation, configuration, and failure propagation,
not that a deployed Ollama version honours those flags or fits a real corpus.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from cdpm import external_llm, llm, llm_usage, providers
from webapp import llm_backend as backend


PROMPT = "Начало синтетического документа.\n" + (
    "Правило: после смены требуется отдых; исключения перечислены полностью.\n"
    * 280
) + "Конец синтетического документа: обязательный последний пункт."
ANSWER = '{"result": "синтетический ответ"}'
MODEL = "synthetic-local-k4"
URL = "http://ollama-k4.invalid:11434"


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


@pytest.fixture
def transport(monkeypatch):
    """No provider secrets/settings, cloud, subprocess, socket or live HTTP."""
    for name in ("CREW_OLLAMA_NUM_CTX", "CREW_OLLAMA_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", "")
    monkeypatch.setenv("CDPM_LLM_ATTEMPTS", "1")
    monkeypatch.setenv("CDPM_LLM_BACKOFF_S", "0")
    monkeypatch.setattr(backend, "ollama_url", lambda: URL)
    monkeypatch.setattr(backend, "ollama_model", lambda: MODEL)
    monkeypatch.setattr(backend._settings, "model_for", lambda *_a: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("K4 local call must not read keys or use cloud")

    monkeypatch.setattr(providers, "provider_key", forbidden)
    monkeypatch.setattr(backend._settings, "has_api_key", forbidden)
    monkeypatch.setattr(backend._settings, "get_api_key", forbidden)
    monkeypatch.setattr(backend, "_cli_generate", forbidden)
    monkeypatch.setattr(backend, "_api_generate", forbidden)
    monkeypatch.setattr(external_llm, "complete", forbidden)
    monkeypatch.setattr(llm.time, "sleep", forbidden)
    state = {"requests": [], "version_requests": [], "version": "0.12.6",
             "error": None, "payload": {
        "response": ANSWER, "done": True,
        "prompt_eval_count": 7, "eval_count": 3}}

    def urlopen(request, timeout=None):
        if request.get_method() == "GET":
            assert request.full_url == URL + "/api/version"
            assert request.data is None
            state["version_requests"].append(request.full_url)
            return _Response({"version": state["version"]})
        state["requests"].append({
            "url": request.full_url,
            "method": request.get_method(),
            "body": json.loads(request.data.decode("utf-8")),
            "timeout": timeout,
        })
        if state["error"] is not None:
            # Real HTTPError parsing is exercised without an HTTP connection.
            raise urllib.error.HTTPError(
                request.full_url, 400, "Bad Request", {},
                io.BytesIO(json.dumps({"error": state["error"]}).encode()),
            )
        return _Response(state["payload"])

    monkeypatch.setattr(backend.urllib.request, "urlopen", urlopen)
    backend.take_last_usage()
    llm_usage.clear()
    yield state
    backend.take_last_usage()
    llm_usage.clear()


def _assert_request(request, prompt, num_ctx, *, temperature=0.2):
    assert request["url"] == URL + "/api/generate"
    assert request["method"] == "POST"
    body = request["body"]
    assert body["model"] == MODEL
    assert body["prompt"] == prompt  # Include first AND last source bytes.
    assert body["stream"] is False
    assert body["think"] is False
    assert body["options"]["temperature"] == temperature
    assert body["options"]["num_ctx"] == num_ctx
    assert type(body["options"]["num_ctx"]) is int
    assert body["truncate"] is False
    assert body["shift"] is False


@pytest.mark.parametrize("prompt", ["Короткий вопрос", PROMPT],
                         ids=["healthy-small", "whole-russian-prompt"])
def test_complete_default_context_and_no_truncation_request(transport, prompt):
    got = backend.complete(prompt, backend="local", model=MODEL, timeout_s=9)
    assert got["ok"] is True and got["text"] == ANSWER
    assert got["backend"] == "local" and got["error"] is None
    assert len(transport["requests"]) == 1
    _assert_request(transport["requests"][0], prompt, 32768)
    # Counts are exactly the fake server's counts, not an estimate of PROMPT.
    assert got["usage"]["input_tokens"] == 7
    assert got["usage"]["output_tokens"] == 3


@pytest.mark.parametrize("env, explicit, expected", [
    ("65536", None, 65536),
    ("65536", 16384, 16384),
    ("invalid-environment", 8192, 8192),
])
def test_complete_explicit_context_overrides_environment(
    transport, monkeypatch, env, explicit, expected,
):
    monkeypatch.setenv("CREW_OLLAMA_NUM_CTX", env)
    kwargs = {} if explicit is None else {"num_ctx": explicit}
    got = backend.complete("Вопрос", backend="local", model=MODEL, **kwargs)
    assert got["ok"] is True and got["text"] == ANSWER
    assert len(transport["requests"]) == 1
    _assert_request(transport["requests"][0], "Вопрос", expected)


@pytest.mark.parametrize("bad", [True, False, 1.5, 4096.0, 0, -1, "4096", {}])
def test_invalid_explicit_context_refused_before_transport(transport, bad):
    with pytest.raises(backend.LLMError, match="num_ctx"):
        backend._ollama_generate("Вопрос", 3, model=MODEL, num_ctx=bad)
    assert transport["requests"] == []


@pytest.mark.parametrize("bad", ["true", "4096.0", "0", "-1", "not-a-number"])
def test_invalid_environment_context_refused_before_transport(
    transport, monkeypatch, bad,
):
    monkeypatch.setenv("CREW_OLLAMA_NUM_CTX", bad)
    got = backend.complete("Вопрос", backend="local", model=MODEL)
    assert got["ok"] is False and got["text"] == ""
    assert "num_ctx" in got["error"]
    assert transport["requests"] == []


@pytest.mark.parametrize("klass", ["local", "cloud-country"])
def test_role_slot_context_reaches_real_request_and_identity(
    transport, monkeypatch, tmp_path, klass,
):
    monkeypatch.setenv("CREW_OLLAMA_NUM_CTX", "65536")
    config = {"worker": {"channels": {"local": {
        "provider": "local", "model": MODEL, "num_ctx": 16384,
    }}}}
    # cloud-country without a declared provider inherits this LOCAL slot.
    assert providers.save_agents(tmp_path, config) == []
    fn = providers.role_channel("local-only", tmp_path, "worker", klass=klass)
    assert fn(PROMPT) == ANSWER
    assert len(transport["requests"]) == 1
    _assert_request(transport["requests"][0], PROMPT, 16384)
    identity = json.loads(llm.channel_identity(fn))
    assert identity["num_ctx"] == 16384
    assert identity["provider"] == "local"
    assert identity["role_config"]["channels"]["local"]["num_ctx"] == 16384


def test_local_channel_pins_context_and_new_context_changes_cache_identity(
    transport, monkeypatch,
):
    monkeypatch.setenv("CREW_OLLAMA_NUM_CTX", "8192")
    first = llm.channel("local-only", provider="local", model=MODEL, attempts=1)
    first_identity = llm.channel_identity(first)
    monkeypatch.setenv("CREW_OLLAMA_NUM_CTX", "16384")
    second = llm.channel("local-only", provider="local", model=MODEL, attempts=1)
    assert first_identity != llm.channel_identity(second)
    assert json.loads(first_identity)["num_ctx"] == 8192
    assert json.loads(llm.channel_identity(second))["num_ctx"] == 16384
    assert first("Первый") == ANSWER
    assert second("Второй") == ANSWER
    assert len(transport["requests"]) == 2
    _assert_request(transport["requests"][0], "Первый", 8192)
    _assert_request(transport["requests"][1], "Второй", 16384)


@pytest.mark.parametrize("klass", ["cloud", "cloud-country"])
def test_explicit_local_provider_uses_its_own_slot_context(transport, tmp_path, klass):
    config = {"worker": {"channels": {klass: {
        "provider": "local", "model": MODEL, "num_ctx": 8192,
    }}}}
    assert providers.save_agents(tmp_path, config) == []
    fn = providers.role_channel("local-only", tmp_path, "worker", klass=klass)
    assert fn(PROMPT) == ANSWER
    _assert_request(transport["requests"][0], PROMPT, 8192)
    assert json.loads(llm.channel_identity(fn))["num_ctx"] == 8192


@pytest.mark.parametrize("bad", [True, 0, -1, 4096.0, "4096"])
def test_invalid_slot_context_is_not_saved(transport, tmp_path, bad):
    problems = providers.save_agents(tmp_path, {"worker": {"channels": {
        "local": {"provider": "local", "num_ctx": bad},
    }}})
    assert problems and "num_ctx" in "; ".join(problems)
    assert not (tmp_path / "agents.json").exists()
    assert transport["requests"] == []


@pytest.mark.parametrize("klass, provider", [
    ("cloud", "cloud"), ("cloud-country", "openai"),
])
def test_cloud_slot_rejects_local_only_context_option(
    transport, tmp_path, klass, provider,
):
    problems = providers.save_agents(tmp_path, {"worker": {"channels": {
        klass: {"provider": provider, "model": "synthetic-cloud", "num_ctx": 8192},
    }}})
    assert problems and "num_ctx" in "; ".join(problems)
    assert not (tmp_path / "agents.json").exists()
    assert transport["requests"] == []


@pytest.mark.parametrize("through_channel", [False, True],
                         ids=["complete-envelope", "public-local-channel"])
def test_server_context_refusal_is_preserved_without_cloud_fallback(
    transport, through_channel,
):
    # No invented character/token ratio: the fake server supplies the refusal.
    # The positive integer budget itself is legal, even when very small.
    detail = "input length exceeds context length: input=8000, num_ctx=1"
    transport["error"] = detail
    if through_channel:
        fn = llm.channel("local-only", provider="local", model=MODEL,
                         attempts=3, num_ctx=1)
        with pytest.raises(llm.LLMCallError) as exc:
            fn(PROMPT)
        assert detail in str(exc.value)
        assert "HTTP 400" in str(exc.value)
        assert "после 1 попыток" in str(exc.value)
    else:
        got = backend.complete(PROMPT, backend="local", model=MODEL, num_ctx=1)
        assert got["ok"] is False and got["text"] == ""
        assert got["backend"] == "local"
        assert detail in got["error"] and "HTTP 400" in got["error"]
    assert len(transport["requests"]) == 1
    _assert_request(transport["requests"][0], PROMPT, 1)
    assert backend.peek_last_usage() is None


@pytest.mark.parametrize("ending", [
    {"done": True, "done_reason": "length"},
    {"done": False, "done_reason": "length"},
    {"done": False},
    {"done": None},
    {"done": "true"},
])
def test_incomplete_server_response_is_not_a_success(transport, ending):
    # shift:false may end at the context boundary with HTTP 200, not an error.
    # The non-empty, syntactically valid fragment is still not a complete answer.
    transport["payload"].update(ending)
    got = backend.complete(PROMPT, backend="local", model=MODEL)
    assert got["ok"] is False and got["text"] == ""
    assert "incomplete" in got["error"]
    assert got["usage"]["input_tokens"] == 7
    assert got["usage"]["output_tokens"] == 3
    assert len(transport["requests"]) == 1


@pytest.mark.parametrize("version", [
    "0.12.5", "0.12.6-rc1", "dev", "", "0.12", "1.0.0-dev", None, {},
])
def test_unverified_server_refused_before_source_is_sent(transport, version):
    transport["version"] = version
    got = backend.complete(PROMPT, backend="local", model=MODEL)
    assert got["ok"] is False and got["text"] == ""
    assert "context controls unverified" in got["error"]
    assert transport["version_requests"] == [URL + "/api/version"]
    assert transport["requests"] == []


@pytest.mark.parametrize("version", ["0.12.6", "0.17.0", "0.34.2"])
def test_supported_server_version_then_generation(transport, version):
    transport["version"] = version
    got = backend.complete("Вопрос", backend="local", model=MODEL)
    assert got["ok"] is True and got["text"] == ANSWER
    assert transport["version_requests"] == [URL + "/api/version"]
    _assert_request(transport["requests"][0], "Вопрос", 32768)


def test_restarted_server_is_rechecked_before_next_source(transport):
    fn = llm.channel("local-only", provider="local", model=MODEL, attempts=3)
    assert fn("Первый") == ANSWER
    transport["version"] = "0.12.5"
    with pytest.raises(llm.LLMCallError, match="context controls unverified"):
        fn("Второй")
    assert len(transport["version_requests"]) == 2
    assert len(transport["requests"]) == 1
