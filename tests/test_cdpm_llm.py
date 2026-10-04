# -*- coding: utf-8 -*-
"""Интерфейс языковой модели CDPM: распаковка конверта COSMOS.

`webapp.llm_backend.complete` возвращает конверт {ok, text, backend,
model, error} и не бросает исключений — это контракт COSMOS. Дефект
«конверт вместо текста» не ловился ни одним тестом (конвейер всюду
проверялся подменными функциями) и был пойман только первым живым
прогоном на документах заказчика (2026-08-17). Эти тесты закрепляют
шов: успех отдаёт строку текста, отказ превращается в исключение с
названными каналом и причиной."""
import pytest

from cdpm import llm as llm_mod


def _fake_complete(reply):
    def complete(prompt, *, backend, timeout_s=120.0, **kw):
        return reply
    return complete


def test_cosmos_llm_unwraps_text_from_envelope(monkeypatch):
    from webapp import llm_backend
    # РВ-140 п. 3: путь CLI без ключа требует модели из настроек площадки.
    from cdpm import providers as _prov
    monkeypatch.setattr(_prov, "load_settings",
                        lambda: {"cloud_default_model": "claude-sub-5"})
    monkeypatch.setattr(llm_backend, "complete", _fake_complete(
        {"ok": True, "text": '{"candidates": []}', "backend": "cloud",
         "model": "claude-cli", "error": None}))
    monkeypatch.setattr(llm_backend, "resolve_backend",
                        lambda scope, requested: "cloud")
    fn = llm_mod.cosmos_llm("any", requested="cloud")
    assert fn("привет") == '{"candidates": []}'


def test_cosmos_llm_retries_then_raises_named_error(monkeypatch):
    """Разовый сбой канала переживается повтором (живой прогон
    2026-08-17: один пустой ответ CLI стоил 35 минут работы); стойкий
    сбой — исключение с числом попыток, каналом и причиной."""
    from webapp import llm_backend
    # РВ-140 п. 3: путь CLI без ключа требует модели из настроек площадки.
    from cdpm import providers as _prov
    monkeypatch.setattr(_prov, "load_settings",
                        lambda: {"cloud_default_model": "claude-sub-5"})
    calls = {"n": 0}

    def flaky(prompt, *, backend, timeout_s=120.0, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "text": "", "backend": "cloud",
                    "model": "claude-cli", "error": "empty output"}
        return {"ok": True, "text": "ответ", "backend": "cloud",
                "model": "claude-cli", "error": None}
    monkeypatch.setattr(llm_backend, "complete", flaky)
    monkeypatch.setattr(llm_backend, "resolve_backend",
                        lambda scope, requested: "cloud")
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    fn = llm_mod.cosmos_llm("any", requested="cloud")
    assert fn("привет") == "ответ"
    assert calls["n"] == 2

    monkeypatch.setattr(llm_backend, "complete", _fake_complete(
        {"ok": False, "text": "", "backend": "cloud",
         "model": "claude-cli", "error": "cloud CLI timed out"}))
    fn = llm_mod.cosmos_llm("any", requested="cloud", attempts=3)
    with pytest.raises(llm_mod.LLMCallError) as e:
        fn("привет")
    assert "cloud CLI timed out" in str(e.value)
    assert "cloud/claude-cli" in str(e.value)   # модель из конверта отказа
    assert "после 3 попыток" in str(e.value)


def test_parse_json_reply_accepts_fenced_and_rejects_garbage():
    got = llm_mod.parse_json_reply('вот ответ:\n```json\n{"a": 1}\n```')
    assert got == {"a": 1}
    with pytest.raises(llm_mod.LLMReplyError):
        llm_mod.parse_json_reply("никакого объекта здесь нет")


def test_попытки_и_паузы_канала_из_среды(monkeypatch):
    """Линия UrA 2026-08-30: терпеливость канала — данные контура
    запуска (CDPM_LLM_ATTEMPTS/CDPM_LLM_BACKOFF_S); явные аргументы
    главнее среды; кривые значения — умолчания."""
    from cdpm import llm as l

    calls = {"n": 0}

    def fake_complete(prompt, **kw):
        calls["n"] += 1
        return {"ok": False, "backend": "local", "model": "m",
                "error": "обрыв"}

    import webapp.llm_backend as lb
    monkeypatch.setattr(lb, "complete", fake_complete)
    monkeypatch.setattr(lb, "resolve_backend",
                        lambda scope, req: "local")
    sleeps = []
    monkeypatch.setattr(l.time, "sleep", lambda s: sleeps.append(s))

    monkeypatch.setenv("CDPM_LLM_ATTEMPTS", "5")
    monkeypatch.setenv("CDPM_LLM_BACKOFF_S", "7")
    ch = l.cosmos_llm("local-only")
    try:
        ch("вопрос")
        raise AssertionError("канал обязан отказать")
    except l.LLMCallError as e:
        assert "после 5 попыток" in str(e)
    assert calls["n"] == 5
    assert sleeps == [7.0, 14.0, 21.0, 28.0]

    calls["n"] = 0
    sleeps.clear()
    ch2 = l.cosmos_llm("local-only", attempts=2, backoff_s=1.0)
    try:
        ch2("вопрос")
    except l.LLMCallError as e:
        assert "после 2 попыток" in str(e)
    assert calls["n"] == 2 and sleeps == [1.0]

    monkeypatch.setenv("CDPM_LLM_ATTEMPTS", "ерунда")
    monkeypatch.setenv("CDPM_LLM_BACKOFF_S", "")
    calls["n"] = 0
    ch3 = l.cosmos_llm("local-only")
    try:
        ch3("вопрос")
    except l.LLMCallError as e:
        assert "после 3 попыток" in str(e)
    assert calls["n"] == 3
