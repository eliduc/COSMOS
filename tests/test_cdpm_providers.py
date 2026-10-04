# -*- coding: utf-8 -*-
"""Провайдеры, настройки и пер-блочная конфигурация ролей CDPM (УИ-4).

Охраняемое: ключ наружу не отдаётся никогда (только маска), настройки
пишутся в изолированный файл (тесты не трогают рабочий каталог данных),
конфигурация роли применяет провайдера и модель к каналу, валидация
отбрасывает чужие роли и поля с названной причиной."""
import json

import pytest
from fastapi.testclient import TestClient

from cdpm import llm as llm_mod, providers, webui
from test_cdpm_extract import fake_llm


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(webui, "llm_factory", lambda scope: fake_llm)
    # Политика файлов по умолчанию «local» ведёт извлечение через
    # локальный канал — тоже подменный (как в тестах интерфейса).
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: fake_llm)
    return TestClient(webui.app)


def test_settings_roundtrip_and_masking(client, tmp_path):
    got = client.get("/api/settings").json()
    assert got["claude_api_key_masked"] is None
    got = client.put("/api/settings",
                     json={"claude_api_key": "sk-ant-0123456789"}).json()
    assert got["claude_api_key_masked"] == "sk-a…6789"
    assert got["keys_masked"]["cloud"] == "sk-a…6789"
    # Сырой ключ на диске есть, в ответах — никогда.
    raw = json.loads((tmp_path / "cdpm-settings.json")
                     .read_text(encoding="utf-8"))
    assert raw["keys"]["cloud"] == "sk-ant-0123456789"
    listing = client.get("/api/providers").text
    assert "sk-ant-0123456789" not in listing
    # Пустое значение удаляет ключ; чужие поля отклоняются.
    got = client.put("/api/settings", json={"claude_api_key": ""}).json()
    assert got["claude_api_key_masked"] is None
    assert client.put("/api/settings",
                      json={"хакер": 1}).status_code == 400


def test_save_settings_replaces_the_file_atomically(tmp_path, monkeypatch):
    """Настройки пишутся временным файлом и заменой целиком: читатель
    никогда не видит рваный JSON, временных файлов после записи не
    остаётся, замена идёт через общий помощник с ожиданием чужого
    читателя (находка COSMOS, К5 каталога 0.5.0)."""
    from cdpm import file_replace
    target = tmp_path / "settings" / "cdpm-settings.json"
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(target))
    seen = []
    original = file_replace.replace_with_retry

    def spy(src, dst, **kw):
        seen.append((str(src), str(dst)))
        assert src.parent == dst.parent, "временный файл лежит рядом с целью"
        assert src.name.startswith(dst.name), "имя временного файла — от цели"
        return original(src, dst, **kw)

    monkeypatch.setattr(file_replace, "replace_with_retry", spy)
    providers.save_settings({"keys": {"cloud": "sk-ant-0123456789"}})
    providers.save_settings({"keys": {"cloud": "sk-ant-9876543210"}})
    assert len(seen) == 2 and all(dst == str(target) for _s, dst in seen)
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["keys"]["cloud"] == "sk-ant-9876543210"
    leftovers = [p.name for p in target.parent.iterdir() if p.name != target.name]
    assert leftovers == [], f"временные файлы не убраны: {leftovers}"


def test_external_provider_keys_through_api(client):
    """Ключи внешних провайдеров задаются по имени провайдера; наружу —
    только маски; локальная модель ключа не принимает."""
    got = client.put("/api/settings",
                     json={"provider": "openai",
                           "api_key": "sk-0123456789"}).json()
    assert got["keys_masked"]["openai"] == "sk-0…6789"
    assert "sk-0123456789" not in client.get("/api/settings").text
    for name in ("grok", "gemini"):
        got = client.put("/api/settings",
                         json={"provider": name,
                               "api_key": f"{name}-0123456789"}).json()
        assert got["keys_masked"][name].endswith("6789")
    assert client.put("/api/settings",
                      json={"provider": "local", "api_key": "х"}
                      ).status_code == 400
    assert client.put("/api/settings",
                      json={"provider": "чужой", "api_key": "х"}
                      ).status_code == 400


def test_providers_listing_shape(client):
    got = client.get("/api/providers").json()["providers"]
    names = {p["name"] for p in got}
    assert names == {"cloud", "local", "openai", "grok", "gemini"}
    cloud = next(p for p in got if p["name"] == "cloud")
    assert "models" in cloud and "(по умолчанию)" in cloud["models"]
    assert "params_supported" in cloud
    # Признак облачности — основа запрета для наборов local-only.
    assert {p["name"] for p in got if p["cloud"]} == \
        {"cloud", "openai", "grok", "gemini"}


def test_provider_check_endpoint(client, monkeypatch):
    from cdpm import providers as providers_mod
    monkeypatch.setattr(providers_mod, "check_provider",
                        lambda p, scope="any": {"ok": True,
                                                "detail": f"готов:{p}"})
    got = client.post("/api/providers/openai/check").json()
    assert got == {"ok": True, "detail": "готов:openai"}
    assert client.post("/api/providers/чужой/check").status_code == 404


def test_check_provider_falls_back_to_live_model_list(monkeypatch,
                                                      tmp_path):
    """Отказ модели по умолчанию у внешнего провайдера ведёт к живому
    перечню моделей ключа и повторной проверке первой доступной —
    зашитые подсказки устаревают (живой случай Gemini)."""
    import cdpm.external_llm as ext
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    providers.set_provider_key("gemini", "AIza-тест")

    def channel(scope, provider=None, model=None, **kw):
        def llm(prompt):
            if model == "настоящая-1":
                return "готов"
            raise RuntimeError("HTTP 404: модель не найдена")
        return llm
    monkeypatch.setattr(llm_mod, "channel", channel)
    monkeypatch.setattr(
        ext, "list_models",
        lambda p, key, **kw: ["настоящая-1", "настоящая-2"])
    # МП-1: перепроверка идёт по ранжированному перечню — мок обязан
    # накрывать и его (живое ранжирование ходит в сеть).
    monkeypatch.setattr(
        ext, "ranked_models",
        lambda p, key, **kw: ["настоящая-1", "настоящая-2"])

    got = providers.check_provider("gemini")
    assert got["ok"] is True
    assert "настоящая-1" in got["detail"]
    assert got["models"] == ["настоящая-1", "настоящая-2"]

    # Ни одна модель не отвечает — честный отказ с перечнем.
    monkeypatch.setattr(
        ext, "list_models", lambda p, key, **kw: ["мёртвая-1"])
    monkeypatch.setattr(
        ext, "ranked_models", lambda p, key, **kw: ["мёртвая-1"])
    got = providers.check_provider("gemini")
    assert got["ok"] is False
    assert "мёртвая-1" in got["detail"]


def test_agents_config_roundtrip_and_validation(client, declared_test_layout):
    # Раскладка объявлена явно: с шага 2 проекта «Запись о прогоне» гейт
    # «Новый прогон» не создаёт прогон при допуске, разрешающем облако,
    # и необъявленной раскладке моделей.
    from test_cdpm_webui import _upload
    run_id = _upload(client).json()["run"]
    # Новая форма: три слота модели на роль (классы данных).
    cfg = {"worker": {"channels": {
               "cloud": {"provider": "cloud", "model": "opus",
                         "temperature": 0.3},
               "local": {"model": "qwen3.8:27b"},
               "cloud-country": {}}},
           "auditor": {"channels": {"local": {}}}}
    got = client.put(f"/api/runs/{run_id}/agents", json=cfg).json()
    assert got == cfg
    assert client.get(f"/api/runs/{run_id}/agents").json() == cfg
    # Прежняя плоская форма мигрирует в слот своего класса.
    got = client.put(f"/api/runs/{run_id}/agents", json={
        "worker": {"provider": "openai", "model": "gpt-5.4"}}).json()
    assert got == {"worker": {"channels": {
        "cloud": {"provider": "openai", "model": "gpt-5.4"}}}}
    resp = client.put(f"/api/runs/{run_id}/agents",
                      json={"хакер": {"provider": "cloud"}})
    assert resp.status_code == 400
    assert "роль вне перечня" in resp.json()["detail"]
    resp = client.put(f"/api/runs/{run_id}/agents",
                      json={"worker": {"provider": "чужой"}})
    assert resp.status_code == 400
    # Слот локальной модели не принимает облачного провайдера.
    resp = client.put(f"/api/runs/{run_id}/agents", json={
        "worker": {"channels": {"local": {"provider": "openai"}}}})
    assert resp.status_code == 400
    assert "локальной модели" in resp.json()["detail"]


def test_role_channel_applies_provider_model_and_params(tmp_path,
                                                        monkeypatch):
    calls = {}

    def spy(scope, provider=None, model=None, **kw):
        calls.update(scope=scope, provider=provider, model=model,
                     temperature=kw.get("temperature"),
                     max_tokens=kw.get("max_tokens"))
        return fake_llm
    monkeypatch.setattr(llm_mod, "channel", spy)
    run_dir = tmp_path
    providers.save_agents(run_dir, {
        "worker": {"provider": "openai", "model": "gpt-5.4",
                   "temperature": 0.2, "max_tokens": 4096}})
    providers.role_channel("any", run_dir, "worker")
    assert calls == {"scope": "any", "provider": "openai",
                     "model": "gpt-5.4", "temperature": 0.2,
                     "max_tokens": 4096}
    # «(по умолчанию)» каналу не передаётся.
    providers.save_agents(run_dir, {
        "auditor": {"provider": "grok", "model": "(по умолчанию)"}})
    providers.role_channel("any", run_dir, "auditor",
                           fallback_requested="cloud")
    assert calls["provider"] == "grok"
    assert calls["model"] is None
    # Слот класса выбирается признаком данных: local — локальная
    # модель слота, cloud-country без провайдера — локальная модель
    # (fail-closed, строже не мягче).
    providers.save_agents(run_dir, {
        "worker": {"channels": {
            "cloud": {"provider": "openai", "model": "gpt-5.4"},
            "local": {"model": "qwen3.8:27b"},
            "cloud-country": {}}}})
    providers.role_channel("any", run_dir, "worker", klass="local")
    assert calls["provider"] == "local"
    assert calls["model"] == "qwen3.8:27b"
    providers.role_channel("any", run_dir, "worker",
                           klass="cloud-country")
    assert calls["provider"] == "local"      # канала в стране нет
    assert calls["model"] == "qwen3.8:27b"
    providers.save_agents(run_dir, {
        "worker": {"channels": {
            "cloud-country": {"provider": "gemini",
                              "model": "флагман-рф"}}}})
    providers.role_channel("any", run_dir, "worker",
                           klass="cloud-country")
    assert calls["provider"] == "gemini"     # провайдер слота выбран
    assert calls["model"] == "флагман-рф"


def test_extract_uses_worker_config(client, monkeypatch,
                                    declared_test_layout):
    """Заданная конфигурация роли применяется к извлечению: канал
    строится через role_channel, а не через общий шов. Политика файлов
    «cloud» — иначе клаузы по умолчанию идут локальным каналом.
    S06-R2: остальные роли извлечения объявлены тестовой раскладкой
    площадки — без неё облачные каналы не строятся."""
    from test_cdpm_webui import _allow_cloud, _upload, _extract_and_wait
    used: dict = {}       # роль → класс, по всем каналам извлечения

    def spy_role_channel(scope, run_dir, role, fallback=None,
                         klass="cloud", **kw):
        used[role] = klass
        return fake_llm
    monkeypatch.setattr(webui.providers_mod, "role_channel",
                        spy_role_channel)
    run_id = _upload(client).json()["run"]
    _allow_cloud(client, run_id)
    # Путём интерфейса: попап шлёт ВСЮ действующую раскладку (площадочное
    # умолчание, пока своей нет) с правкой одной роли поверх — снимок
    # прогона полный, прочие облачные роли остаются объявленными.
    layout = client.get(f"/api/runs/{run_id}/agents").json()
    assert layout and "auditor" in layout       # копия площадочного носителя
    client.put(f"/api/runs/{run_id}/agents",
               json={**layout, "worker": {"provider": "cloud"}})
    st = _extract_and_wait(client, run_id)
    assert st["state"] == "извлечено"
    # Настроенный обработчик шёл через role_channel классом «cloud»
    # (класс = политика файлов); прочие роли извлечения (например,
    # сверщик логики семейства аудитора) — тоже через role_channel, из
    # объявленной раскладки площадки, а не через общий шов.
    # Снимок прогона несёт все роли, поэтому действующая роль извлечения
    # — сам «extractor» (своя запись), а не унаследованный «worker».
    assert used.get("extractor") == "cloud" or used.get("worker") == "cloud", used
    assert all(k == "cloud" for k in used.values()), used


def test_mp2_cloud_key_flows_to_execution_channel(monkeypatch,
                                                  tmp_path):
    """МП-2: ключ «cloud» из настроек CDPM прокидывается каналу
    выполнения webapp через переменную окружения процесса; файл webapp
    и заданная снаружи переменная — сильнее; удаление ключа очищает
    переменную."""
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.delenv("CREW_ANTHROPIC_API_KEY", raising=False)
    import os
    from webapp import settings as web_settings

    # Ключа нет нигде — прокидывать нечего.
    assert providers.sync_cloud_key_to_env() is False
    assert "CREW_ANTHROPIC_API_KEY" not in os.environ

    # Ключ в настройках CDPM — прокинут в память процесса.
    providers.set_provider_key("cloud", "sk-ant-тестовый")
    assert os.environ.get("CREW_ANTHROPIC_API_KEY") == "sk-ant-тестовый"
    assert providers.sync_cloud_key_to_env() is False  # webapp уже видит

    # Удаление ключа очищает переменную.
    providers.set_provider_key("cloud", "")
    assert "CREW_ANTHROPIC_API_KEY" not in os.environ

    # Файл webapp сильнее: прокидка не перетирает его путь.
    providers.set_provider_key("cloud", "sk-ant-другой")
    monkeypatch.setattr(web_settings, "has_api_key", lambda: True)
    monkeypatch.delenv("CREW_ANTHROPIC_API_KEY", raising=False)
    assert providers.sync_cloud_key_to_env() is False
    assert "CREW_ANTHROPIC_API_KEY" not in os.environ
    monkeypatch.delenv("CREW_ANTHROPIC_API_KEY", raising=False)


def test_mp3_role_thinking_flows_to_channel_effort(tmp_path,
                                                   monkeypatch):
    """МП-3: «Усилие рассуждения» роли (поле существовало в интерфейсе,
    но никуда не прокидывалось) доезжает до канала; слотовое effort —
    тонкая настройка поверх ролевого; мусорные значения отклоняются с
    названной причиной."""
    calls = {}

    def spy(scope, provider=None, model=None, **kw):
        calls.update(provider=provider, effort=kw.get("effort"))
        return fake_llm
    monkeypatch.setattr(llm_mod, "channel", spy)
    run_dir = tmp_path

    # Ролевое усилие применяется облачным слотом.
    assert providers.save_agents(run_dir, {
        "worker": {"thinking": "high", "channels": {
            "cloud": {"provider": "openai", "model": "m"}}}}) == []
    providers.role_channel("any", run_dir, "worker")
    assert calls["effort"] == "high"

    # Слотовое effort главнее ролевого.
    assert providers.save_agents(run_dir, {
        "worker": {"thinking": "low", "channels": {
            "cloud": {"provider": "openai", "model": "m",
                      "effort": "max"}}}}) == []
    providers.role_channel("any", run_dir, "worker")
    assert calls["effort"] == "max"

    # Без усилий канал получает None.
    assert providers.save_agents(run_dir, {
        "worker": {"channels": {
            "cloud": {"provider": "openai", "model": "m"}}}}) == []
    providers.role_channel("any", run_dir, "worker")
    assert calls["effort"] is None

    # Мусорные значения — отказ с причиной, и в роли, и в слоте.
    got = providers.save_agents(run_dir, {
        "worker": {"thinking": "турбо", "channels": {}}})
    assert got and "усилие рассуждения" in got[0]
    got = providers.save_agents(run_dir, {
        "worker": {"channels": {
            "cloud": {"provider": "openai", "effort": "турбо"}}}})
    assert got and "усилие рассуждения" in got[0]


def test_mp4_role_channel_carries_long_timeout(tmp_path, monkeypatch):
    """МП-4: конфигурация роли не роняет таймаут до 300-секундного
    дефолта — каналу роли передаётся тот же длинный таймаут, что у
    канала цикла без конфигурации (живой урок UniA 2026-08-20)."""
    calls = {}

    def spy(scope, provider=None, model=None, **kw):
        calls.update(timeout_s=kw.get("timeout_s"))
        return fake_llm
    monkeypatch.setattr(llm_mod, "channel", spy)
    providers.save_agents(tmp_path, {
        "worker": {"thinking": "high", "channels": {
            "cloud": {"provider": "openai", "model": "m"}}}})
    providers.role_channel("any", tmp_path, "worker")
    assert calls["timeout_s"] == 600.0


def test_usk1_worker_effort_default_low(tmp_path, monkeypatch):
    """УСК-1: умолчание усилия вызывающей стороны применяется, когда
    роль его не задала; явная настройка роли главнее."""
    calls = {}

    def spy(scope, provider=None, model=None, **kw):
        calls.update(effort=kw.get("effort"))
        return fake_llm
    monkeypatch.setattr(llm_mod, "channel", spy)
    providers.save_agents(tmp_path, {
        "worker": {"channels": {
            "cloud": {"provider": "openai", "model": "m"}}}})
    providers.role_channel("any", tmp_path, "worker",
                           effort_default="low")
    assert calls["effort"] == "low"
    providers.save_agents(tmp_path, {
        "worker": {"thinking": "max", "channels": {
            "cloud": {"provider": "openai", "model": "m"}}}})
    providers.role_channel("any", tmp_path, "worker",
                           effort_default="low")
    assert calls["effort"] == "max"


def test_реестр_провайдеров_несёт_юрисдикцию():
    """Пакет трёх 2026-08-30, п.2: слоту «облако внутри страны»
    интерфейс фильтрует провайдеров по признаку in_country;
    зарубежные облака (включая Claude) — False, локальная — True."""
    got = {p["name"]: p.get("in_country")
           for p in providers.list_providers()}
    assert got["local"] is True
    assert got["cloud"] is False
    for name in ("openai", "grok", "gemini"):
        assert got[name] is False, name


def test_base_url_слота_локальной_роли(tmp_path, monkeypatch):
    """Принцип трёх моделей (директива владельца 2026-08-31):
    роль-на-адрес — слот local несёт base_url своей Ollama (другой
    GPU/машина); валидатор пускает его только локальному слоту и
    только http(s); канал доводит адрес до запроса."""
    from cdpm import providers as p, llm as llm_mod
    import webapp.llm_backend as lb
    # Валидатор: законный local, беззаконные формы.
    probs = []
    ok = p._check_channel("auditor", "local",
                          {"model": "qwen3.8:27b",
                           "base_url": "http://127.0.0.1:11436"},
                          probs)
    assert probs == [] and ok.get("base_url")
    probs = []
    p._check_channel("auditor", "cloud",
                     {"base_url": "http://x"}, probs)
    assert any("только слоту локальной" in x for x in probs)
    probs = []
    p._check_channel("auditor", "local",
                     {"base_url": "ftp://x"}, probs)
    assert any("не http(s)" in x for x in probs)
    # Сквозная доводка адреса до запроса Ollama.
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                import json as _json
                return _json.dumps({"version": "0.12.6"} if req.data is None
                                   else {"response": "ок", "done": True}).encode()
        return R()

    monkeypatch.setattr(lb.urllib.request, "urlopen", fake_urlopen)
    (tmp_path / "agents.json").write_text(
        '{"auditor": {"channels": {"local": {"model": "m-audit", '
        '"base_url": "http://127.0.0.1:11436"}}}}',
        encoding="utf-8")
    chan = p.role_channel("any", tmp_path, "auditor", klass="local")
    assert chan("привет") == "ок"
    assert seen["url"].startswith("http://127.0.0.1:11436/")


def test_роль_applier_и_площадочный_дефолт(tmp_path, monkeypatch):
    """Раскладка моделей владельца (31.08): роль applier (применение
    ответов + линия данных) с честной запаской worker; площадочный
    дефолт agents-default.json наследуется прогонами БЕЗ своего
    agents.json, пер-прогонный сильнее; служебные ключи конвенции
    носителей игнорируются."""
    import json as _json
    from cdpm import providers as p
    # Дефолт площадки: applier настроен.
    default = tmp_path / "agents-default.json"
    default.write_text(_json.dumps({
        "form_version": 1, "customer": "площадка",
        "applier": {"channels": {"cloud": {"provider": "cloud",
                                           "model": "claude-opus-5"}}},
        "worker": {"channels": {"cloud": {"provider": "cloud",
                                          "model": "claude-sonnet-5"}}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", str(default))
    run = tmp_path / "run"
    run.mkdir()
    agents = p.load_agents(run)
    assert set(agents) == {"applier", "worker"}
    assert agents["applier"]["channels"]["cloud"]["model"] \
        == "claude-opus-5"
    # Пер-прогонный agents.json СИЛЬНЕЕ дефолта целиком.
    (run / "agents.json").write_text(_json.dumps({
        "worker": {"channels": {"cloud": {"provider": "cloud",
                                          "model": "своя"}}}}),
        encoding="utf-8")
    agents = p.load_agents(run)
    assert set(agents) == {"worker"}
    # applier — законная роль валидатора.
    probs = p.save_agents(run, {
        "applier": {"channels": {"cloud": {"provider": "cloud"}}}})
    assert probs == []


def test_полная_независимость_ролей_наследование(tmp_path, monkeypatch):
    """Директива владельца 2.09: каждая LLM-точка — своя роль; роль
    без конфигурации наследует родителя по цепочке, старые раскладки
    из четырёх ролей читаются без изменений."""
    from cdpm import providers as prov
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", "")
    run = tmp_path / "run"
    run.mkdir()
    assert set(prov.ROLE_PARENT) <= set(prov.ROLES)
    assert all(p in (None,) + prov.ROLES for p in prov.ROLE_PARENT.values())
    assert set(prov.ROLE_META) == set(prov.ROLES)
    # Только родители настроены — дочерние роли наследуют.
    assert prov.save_agents(run, {
        "worker": {"channels": {"cloud": {"provider": "cloud",
                                          "model": "claude-sonnet-5"}}},
        "applier": {"channels": {"cloud": {"provider": "cloud",
                                           "model": "claude-opus-5"}}},
    }) == []
    agents = prov.load_agents(run)
    assert prov.effective_role(agents, "extractor") == "worker"
    assert prov.effective_role(agents, "answer_matcher") == "applier"
    assert prov.effective_role(agents, "auditor") is None
    assert prov.effective_role(agents, "vision") is None
    # Своя конфигурация дочерней роли главнее родителя.
    assert prov.save_agents(run, {
        "worker": {"channels": {"cloud": {"provider": "cloud",
                                          "model": "claude-sonnet-5"}}},
        "extractor": {"channels": {"cloud": {"provider": "cloud",
                                             "model": "claude-fable-5"}}},
    }) == []
    agents = prov.load_agents(run)
    assert prov.effective_role(agents, "extractor") == "extractor"
    assert prov.effective_role(agents, "replier") == "worker"
    # Роль вне перечня — отказ словами.
    assert prov.save_agents(run, {"unknown_role": {"channels": {}}})


def test_канал_applier_падает_на_worker(tmp_path, monkeypatch):
    """Без конфигурации applier точка применения честно идёт каналом
    worker (fallback_role), с конфигурацией — своим."""
    import json as _json
    from cdpm import webui
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", "")
    run = tmp_path
    # Допуск моделей — по признаку комплекта прогона (03.09): тестовый
    # комплект синтетический, просьба «any» с ним совпадает.
    webui._write_status(run, {"kit": {"data_class": "synthetic"}})
    (run / "agents.json").write_text(_json.dumps({
        "worker": {"channels": {"cloud": {"provider": "cloud",
                                          "model": "m-worker"}}}}),
        encoding="utf-8")
    seen = {}
    import cdpm.providers as p_mod

    def fake_role_channel(scope, run_dir, role, *a, **kw):
        seen["role"] = role
        return lambda prompt: "ок"

    monkeypatch.setattr(p_mod, "role_channel", fake_role_channel)
    webui._channel_for("any", run, "applier",
                       fallback_role="worker")
    assert seen["role"] == "worker", "запаска без конфигурации"
    (run / "agents.json").write_text(_json.dumps({
        "applier": {"channels": {"cloud": {"provider": "cloud",
                                           "model": "m-opus"}}}}),
        encoding="utf-8")
    webui._channel_for("any", run, "applier",
                       fallback_role="worker")
    assert seen["role"] == "applier", "своя конфигурация главнее"
