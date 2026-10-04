"""Phase-1 unit tests for the pluggable LLM backend + contour guard."""
import pytest

from webapp import llm_backend


# --------------------------------------------------------- resolve_backend

def test_local_only_forces_local_on_auto():
    assert llm_backend.resolve_backend("local-only", None) == "local"
    assert llm_backend.resolve_backend("local-only", "auto") == "local"
    assert llm_backend.resolve_backend("local-only", "local") == "local"


def test_local_only_rejects_cloud():
    with pytest.raises(llm_backend.ScopeViolation):
        llm_backend.resolve_backend("local-only", "cloud")


def test_any_scope_honors_request(monkeypatch):
    assert llm_backend.resolve_backend("any", "cloud") == "cloud"
    assert llm_backend.resolve_backend("any", "local") == "local"


def test_any_scope_auto_uses_default(monkeypatch):
    monkeypatch.setenv("CREW_LLM_DEFAULT_BACKEND", "cloud")
    assert llm_backend.resolve_backend("any", "auto") == "cloud"
    monkeypatch.setenv("CREW_LLM_DEFAULT_BACKEND", "local")
    assert llm_backend.resolve_backend("any", None) == "local"


def test_unknown_scope_and_request_raise():
    with pytest.raises(ValueError):
        llm_backend.resolve_backend("public", "auto")
    with pytest.raises(ValueError):
        llm_backend.resolve_backend("any", "gpt")


# -------------------------------------------------------- resolve_claude_cli

def test_resolve_claude_cli_falls_back_to_npm_dir(monkeypatch, tmp_path):
    """Службы площадок стартуют с урезанным PATH без %APPDATA%\\npm — спавн
    по голому имени падал WinError 2 (Test, 2026-08-06). Голое имя обязано
    дорезолвливаться через npm-каталог пользователя."""
    monkeypatch.delenv("CREW_CLAUDE_CLI", raising=False)
    monkeypatch.setattr(llm_backend.shutil, "which", lambda exe: None)
    monkeypatch.setattr(llm_backend.os, "name", "nt")
    npm = tmp_path / "npm"
    npm.mkdir()
    (npm / "claude.cmd").write_text("@echo off\n", encoding="ascii")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    got = llm_backend.resolve_claude_cli()
    assert got == str(npm / "claude.cmd")


def test_resolve_claude_cli_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("CREW_CLAUDE_CLI", raising=False)
    monkeypatch.setattr(llm_backend.shutil, "which", lambda exe: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))     # пустой каталог
    assert llm_backend.resolve_claude_cli() is None


def test_resolve_claude_cli_explicit_path_honored(monkeypatch, tmp_path):
    """Явный CREW_CLAUDE_CLI с каталогом не переизобретается: существует —
    как есть, нет — None (не подменять чужим бинарём с PATH)."""
    exe = tmp_path / "my-claude.cmd"
    exe.write_text("@echo off\n", encoding="ascii")
    monkeypatch.setenv("CREW_CLAUDE_CLI", str(exe))
    assert llm_backend.resolve_claude_cli() == str(exe)
    monkeypatch.setenv("CREW_CLAUDE_CLI", str(tmp_path / "missing.cmd"))
    assert llm_backend.resolve_claude_cli() is None


# ----------------------------------------------------------------- complete

def test_complete_local_ok(monkeypatch):
    monkeypatch.setattr(llm_backend, "_ollama_generate",
                        lambda p, t, system=None: "hello from local")
    res = llm_backend.complete("hi", backend="local", timeout_s=5)
    assert res["ok"] and res["text"] == "hello from local"
    assert res["backend"] == "local"


def test_complete_cloud_ok(monkeypatch):
    seen = {}

    def fake_cli(prompt, timeout_s, cwd=None):
        seen["prompt"] = prompt
        return "cloud reply"

    monkeypatch.setattr(llm_backend, "_cli_generate", fake_cli)
    res = llm_backend.complete("q", backend="cloud", timeout_s=5,
                               system="be terse")
    assert res["ok"] and res["text"] == "cloud reply"
    assert res["model"] == "claude-cli"
    assert seen["prompt"].startswith("be terse")   # system folded in


def test_complete_backend_failure_returns_not_ok(monkeypatch):
    def boom(*a, **k):
        raise llm_backend.LLMError("unreachable")

    monkeypatch.setattr(llm_backend, "_ollama_generate", boom)
    res = llm_backend.complete("hi", backend="local", timeout_s=1)
    assert res["ok"] is False and "unreachable" in res["error"]


def test_complete_unknown_backend_raises():
    with pytest.raises(ValueError):
        llm_backend.complete("hi", backend="mystery")


# ----------------------------------------------- self-hosted ollama headers

def test_ollama_headers_have_custom_ua_and_no_key_by_default(monkeypatch):
    monkeypatch.delenv("CREW_OLLAMA_KEY", raising=False)
    h = llm_backend._ollama_headers()
    # Cloudflare in front of internallm.ai rejects the default urllib UA.
    assert h["User-Agent"].startswith("cosmos-")
    assert "Authorization" not in h


def test_ollama_headers_carry_bearer_key(monkeypatch):
    monkeypatch.setenv("CREW_OLLAMA_KEY", "sk-test-123")
    h = llm_backend._ollama_headers()
    assert h["Authorization"] == "Bearer sk-test-123"


def test_ollama_401_yields_actionable_error(monkeypatch):
    import io
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized",
            hdrs=None, fp=io.BytesIO(b"Unauthorized: Invalid API key"))

    monkeypatch.setattr(llm_backend.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_backend.LLMError) as exc:
        llm_backend._ollama_generate("hi", timeout_s=5)
    assert "CREW_OLLAMA_KEY" in str(exc.value)


def test_local_model_has_no_code_default_rv119(monkeypatch):
    # РВ-119 / РВ-138, исправление 2: «модель — в настройках блока, не
    # умолчанием в коде». Прежде здесь закреплялось зашитое имя
    # qwen3.6:35b-a3b (2026-07-16) — теперь без настройки и без
    # CREW_OLLAMA_MODEL вызов отказывает словами, показ получает None.
    monkeypatch.delenv("CREW_OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(llm_backend._settings, "get_ollama_model", lambda: None)
    assert llm_backend.ollama_model_or_none() is None
    with pytest.raises(llm_backend.LocalModelNotConfigured) as exc:
        llm_backend.ollama_model()
    assert "CREW_OLLAMA_MODEL" in str(exc.value)
    assert llm_backend.available()["local_model"] is None


def test_local_model_from_launcher_then_settings_rv119(monkeypatch):
    monkeypatch.setenv("CREW_OLLAMA_MODEL", "qwen3.6:35b-a3b")
    monkeypatch.setattr(llm_backend._settings, "get_ollama_model", lambda: None)
    assert llm_backend.ollama_model() == "qwen3.6:35b-a3b"
    monkeypatch.setattr(llm_backend._settings, "get_ollama_model",
                        lambda: "qwen3.6:8b")
    assert llm_backend.ollama_model() == "qwen3.6:8b"


# --------------------------------------------- local model warm/cold probe

class _FakePs:
    def __init__(self, models):
        self._models = models
        self.status = 200

    def read(self):
        import json as _json
        return _json.dumps({"models": self._models}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_local_model_loaded_true_when_resident(monkeypatch):
    monkeypatch.setenv("CREW_OLLAMA_MODEL", "qwen3.6:35b-a3b")
    monkeypatch.setattr(llm_backend.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakePs(
                            [{"name": "qwen3.6:35b-a3b"}]))
    assert llm_backend._local_model_loaded() is True


def test_local_model_loaded_false_when_cold(monkeypatch):
    monkeypatch.setenv("CREW_OLLAMA_MODEL", "qwen3.6:35b-a3b")
    monkeypatch.setattr(llm_backend.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakePs([]))
    assert llm_backend._local_model_loaded() is False


def test_local_model_loaded_false_on_error(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("ollama down")
    monkeypatch.setattr(llm_backend.urllib.request, "urlopen", boom)
    assert llm_backend._local_model_loaded() is False


def test_available_reports_model_loaded(monkeypatch):
    monkeypatch.setenv("CREW_OLLAMA_MODEL", "qwen3.6:35b-a3b")
    monkeypatch.setattr(llm_backend.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakePs(
                            [{"name": "qwen3.6:35b-a3b"}]))
    a = llm_backend.available()
    assert a["local"] is True and a["local_model_loaded"] is True


def test_mp3_api_thinking_budget_and_degradation(monkeypatch,
                                                 tmp_path):
    """МП-3: усилие уровня роли превращается в extended-thinking
    budget Messages API (max_tokens растёт на бюджет); модель,
    отвергшая thinking, получает повтор без него."""
    import json as _json
    from webapp import llm_backend, settings

    monkeypatch.setattr(settings, "get_api_key", lambda: "sk-ant-x")
    calls: list[dict] = []

    class _R:
        def __init__(self, status, payload, text=""):
            self.status_code = status
            self._p = payload
            self.text = text or _json.dumps(payload)

        def json(self):
            return self._p

    ok = {"content": [{"type": "text", "text": "готов"}],
          "stop_reason": "end_turn"}

    class _FakeHttpx:
        class HTTPError(Exception):
            pass

        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            calls.append(json)
            return _R(200, ok)
    monkeypatch.setitem(__import__("sys").modules, "httpx", _FakeHttpx)

    got = llm_backend._api_generate("привет", model="claude-fable-5",
                                    timeout_s=30, effort="medium")
    assert got == "готов"
    assert calls[-1]["thinking"] == {"type": "enabled",
                                     "budget_tokens": 8192}
    assert calls[-1]["max_tokens"] == 16000 + 8192

    # Деградация: 400 с упоминанием thinking → повтор без него.
    calls.clear()

    class _FakeHttpx2(_FakeHttpx):
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            calls.append(json)
            if "thinking" in json:
                return _R(400, {}, text="thinking is not supported")
            return _R(200, ok)
    monkeypatch.setitem(__import__("sys").modules, "httpx",
                        _FakeHttpx2)
    got = llm_backend._api_generate("привет", model="claude-old",
                                    timeout_s=30, effort="max")
    assert got == "готов"
    assert len(calls) == 2
    assert "thinking" not in calls[1]
    assert calls[1]["max_tokens"] == 16000

    # Без усилия тело прежнее — совместимость.
    calls.clear()
    monkeypatch.setitem(__import__("sys").modules, "httpx", _FakeHttpx)
    llm_backend._api_generate("привет", model="claude-fable-5",
                              timeout_s=30)
    assert "thinking" not in calls[-1]
    assert calls[-1]["max_tokens"] == 16000


def test_mp4_thinking_total_is_clamped_and_slot_params_flow(
        monkeypatch, tmp_path):
    """МП-4: суммарный max_tokens с бюджетом мышления не превышает
    потолок класса моделей (32k); слотовые max_tokens/temperature
    доезжают до Messages API; при включённом мышлении температура не
    отправляется (API её запрещает); 400 про max_tokens при бюджете —
    повтор без мышления."""
    import json as _json
    from webapp import llm_backend, settings
    monkeypatch.setattr(settings, "get_api_key", lambda: "sk-ant-x")
    calls: list[dict] = []
    ok = {"content": [{"type": "text", "text": "готов"}],
          "stop_reason": "end_turn"}

    class _R:
        def __init__(self, status, payload, text=""):
            self.status_code = status
            self._p = payload
            self.text = text or _json.dumps(payload)

        def json(self):
            return self._p

    class _H:
        class HTTPError(Exception):
            pass

        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            calls.append(json)
            return _R(200, ok)
    monkeypatch.setitem(__import__("sys").modules, "httpx", _H)

    # Кламп: max-усилие не выпрыгивает за 32000 суммарных.
    llm_backend._api_generate("п", model="claude-opus-5",
                              timeout_s=30, effort="max")
    assert calls[-1]["max_tokens"] == 32000
    assert calls[-1]["thinking"]["budget_tokens"] == 24576

    # Слотовые параметры: температура без мышления уходит, с
    # мышлением — нет; max_tokens слота переопределяет базу.
    llm_backend._api_generate("п", model="claude-fable-5",
                              timeout_s=30, temperature=0.3,
                              max_tokens=4000)
    assert calls[-1]["temperature"] == 0.3
    assert calls[-1]["max_tokens"] == 4000
    llm_backend._api_generate("п", model="claude-fable-5",
                              timeout_s=30, temperature=0.3,
                              effort="low")
    assert "temperature" not in calls[-1]

    # 400 про max_tokens при бюджете — повтор без мышления.
    calls.clear()

    class _H2(_H):
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            calls.append(json)
            if "thinking" in json:
                return _R(400, {}, text="max_tokens: 48000 exceeds "
                                        "maximum allowed output")
            return _R(200, ok)
    monkeypatch.setitem(__import__("sys").modules, "httpx", _H2)
    got = llm_backend._api_generate("п", model="claude-opus-4-1",
                                    timeout_s=30, effort="high")
    assert got == "готов"
    assert len(calls) == 2 and "thinking" not in calls[1]


def test_пустой_ответ_локальной_модели_честный_отказ(monkeypatch):
    """Урок линии BA (31.08): думающая Ollama-модель без think:false
    кладёт ответ в thinking, response пуст — конвейер жевал пустоту.
    Труба: (а) think=false умолчанием в теле запроса; (б) пустой
    ответ = ok:False с причиной."""
    import json as _json
    import webapp.llm_backend as lb
    seen = {}

    def fake_urlopen(req, timeout=None):
        if req.data is not None:
            seen["body"] = _json.loads(req.data.decode("utf-8"))

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return _json.dumps({"version": "0.12.6"} if req.data is None
                                   else {"response": "  ", "done": True}).encode()
        return R()

    monkeypatch.setattr(lb.urllib.request, "urlopen", fake_urlopen)
    got = lb.complete("привет", backend="local", timeout_s=5)
    assert seen["body"]["think"] is False, "умолчание think=false"
    assert got["ok"] is False
    assert "empty response" in got["error"], "честный отказ трубы"
    # Явное включение слотом доезжает до тела.
    lb.complete("привет", backend="local", timeout_s=5, think=True)
    assert seen["body"]["think"] is True


# ------------------------------------------------------- расход моделей

def test_usage_разбирается_у_трёх_провайдеров():
    """Решение владельца № 1 (10.09.2026): расход — В ТОКЕНАХ по каждой
    задействованной модели, деньги не считаются. Форму ответа знает
    провайдер, и разборщик обязан взять именно его имена полей:
    Anthropic — input/output и два счётчика кэша; OpenAI (и Grok той же
    формы) — prompt/completion, кэш и размышления в подробностях;
    Gemini — usageMetadata со своими именами."""
    a = llm_backend.usage_anthropic(
        {"model": "claude-стенд",
         "usage": {"input_tokens": 1200, "output_tokens": 340,
                   "cache_creation_input_tokens": 90,
                   "cache_read_input_tokens": 800}},
        channel="api")
    assert a["provider"] == "anthropic" and a["model"] == "claude-стенд"
    assert a["counted"] is True and a["channel"] == "api"
    assert a["input_tokens"] == 1200 and a["output_tokens"] == 340
    assert a["cache_write_tokens"] == 90 and a["cache_read_tokens"] == 800
    # Размышления Anthropic отдельным счётчиком не называет — выдуманного
    # поля в записи быть не должно.
    assert "thinking_tokens" not in a

    o = llm_backend.usage_openai(
        {"model": "gpt-стенд",
         "usage": {"prompt_tokens": 10, "completion_tokens": 4,
                   "prompt_tokens_details": {"cached_tokens": 6},
                   "completion_tokens_details": {"reasoning_tokens": 3}}})
    assert o["provider"] == "openai" and o["counted"] is True
    assert (o["input_tokens"], o["output_tokens"]) == (10, 4)
    assert o["cache_read_tokens"] == 6 and o["thinking_tokens"] == 3
    # Grok держит форму OpenAI — та же ветка словаря, своё имя провайдера.
    g = llm_backend.parse_usage(
        "grok", {"usage": {"prompt_tokens": 7, "completion_tokens": 2}},
        model="grok-стенд")
    assert g["provider"] == "grok" and g["input_tokens"] == 7

    m = llm_backend.usage_gemini(
        {"modelVersion": "gemini-стенд",
         "usageMetadata": {"promptTokenCount": 55,
                           "candidatesTokenCount": 8,
                           "cachedContentTokenCount": 20,
                           "thoughtsTokenCount": 5}})
    assert m["provider"] == "gemini" and m["model"] == "gemini-стенд"
    assert (m["input_tokens"], m["output_tokens"]) == (55, 8)
    assert m["cache_read_tokens"] == 20 and m["thinking_tokens"] == 5


def test_usage_без_счётчиков_не_выдаёт_ноль():
    """Ноль читался бы как измеренная величина «модель ничего не
    потратила». Провайдер, не назвавший ни одного счётчика, даёт
    counted=false с причиной словами."""
    got = llm_backend.usage_anthropic({"usage": {}}, model="м")
    assert got["counted"] is False and got["note"]
    assert not any(k in got for k in llm_backend.USAGE_KEYS)
    # Неизвестный провайдер — тоже слова, а не молчаливый ноль.
    unknown = llm_backend.parse_usage("неведомый", {}, model="м")
    assert unknown["counted"] is False and "неведомый" in unknown["note"]


def test_канал_cli_расхода_не_отдаёт(monkeypatch):
    """Агентный CLI подписки ответного usage не возвращает: вызов
    посчитан, токены — нет, причина стоит словами."""
    class _Proc:
        returncode = 0

        def communicate(self, input=None, timeout=None):
            return ("готово", "")
    monkeypatch.setattr(llm_backend.subprocess, "Popen",
                        lambda *a, **kw: _Proc())
    monkeypatch.setattr(llm_backend, "resolve_claude_cli",
                        lambda: "claude-стенд")
    assert llm_backend._cli_generate("п", 5, model="м") == "готово"
    got = llm_backend.take_last_usage()
    assert got["counted"] is False
    assert got["note"] == llm_backend.CLI_UNCOUNTED_NOTE
    assert got["channel"] == "cli" and got["model"] == "м"
    # Читается один раз: несняток не приписывается следующему вызову.
    assert llm_backend.take_last_usage() is None


def test_конверт_complete_несёт_расход_api_и_локальной_модели(monkeypatch):
    """Расход едет и конвертом complete (для вызывающих, которые его
    читают), и трубой потока (для писателя журнала, который зовёт канал
    как `(prompt) -> str`)."""
    import json as _json
    from webapp import settings
    monkeypatch.setattr(settings, "get_api_key", lambda: "ключ-стенда")
    monkeypatch.setattr(settings, "has_api_key", lambda: True)

    class _R:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"content": [{"type": "text", "text": "готов"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 31, "output_tokens": 7}}

    class _FakeHttpx:
        class HTTPError(Exception):
            pass

        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            return _R()
    monkeypatch.setitem(__import__("sys").modules, "httpx", _FakeHttpx)
    got = llm_backend.complete("привет", backend="cloud", model="м-стенд",
                               timeout_s=5)
    assert got["ok"] and got["usage"]["counted"] is True
    assert got["usage"]["input_tokens"] == 31
    assert got["usage"]["output_tokens"] == 7
    # Труба потока держит тот же расход — конверт его не съел.
    assert llm_backend.take_last_usage()["input_tokens"] == 31

    # Локальная модель считает токены так же (prompt_eval_count/eval_count).
    def fake_urlopen(req, timeout=None):
        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return _json.dumps({"version": "0.12.6"} if req.data is None else
                                   {"response": "ответ", "done": True,
                                    "prompt_eval_count": 12,
                                    "eval_count": 3}).encode()
        return R()
    monkeypatch.setattr(llm_backend.urllib.request, "urlopen", fake_urlopen)
    local = llm_backend.complete("привет", backend="local", timeout_s=5)
    assert local["ok"] and local["usage"]["provider"] == "ollama"
    assert local["usage"]["input_tokens"] == 12
    assert local["usage"]["output_tokens"] == 3


def test_свод_расхода_складывает_счётчики_и_называет_несчитанное():
    """Свод по одной модели: токены складываются, вызовы считаются,
    несчитанные вызовы названы числом и причиной — их не смешивают с
    нулём."""
    rows = [{"counted": True, "input_tokens": 10, "output_tokens": 2},
            {"counted": True, "input_tokens": 5, "output_tokens": 1,
             "cache_read_tokens": 4},
            {"counted": False, "note": llm_backend.CLI_UNCOUNTED_NOTE}]
    got = llm_backend.merge_usage(rows)
    assert got["calls"] == 3 and got["counted"] is True
    assert got["input_tokens"] == 15 and got["output_tokens"] == 3
    assert got["cache_read_tokens"] == 4
    assert got["uncounted_calls"] == 1
    assert llm_backend.CLI_UNCOUNTED_NOTE in got["note"]
    # Ни одного считанного вызова — counted=false, а не нули.
    only_cli = llm_backend.merge_usage([rows[2]])
    assert only_cli["counted"] is False and only_cli["calls"] == 1
    assert "input_tokens" not in only_cli
