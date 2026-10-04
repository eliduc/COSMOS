# -*- coding: utf-8 -*-
"""Источник конфигурации роли и просьба/факт канала в отпечатке
(ревизия 1.18.4 спецификации, таблица S06; план единой версии §3.7,
строка «GPT-005»).

Охраняемое: пер-прогонный agents.json сильнее площадочного носителя,
и отпечаток канала называет, какой из них действует (config_source:
run-explicit / site-default / none); отпечаток несёт просьбу слота
(requested: провайдер, модель, вид канала) и действующие
провайдер/модель/вид канала (effective) по той же логике, что строит
канал (ключ → API, без ключа → командная строка, локальная модель →
local, внешний провайдер → API); смена источника при той же модели —
другой отпечаток (другой ключ кэша); журнал каналов стадий в статусе
прогона. Семантика допуска не меняется: local-only не строит облачный
слот, отказ канала не подменяет модель. Живых вызовов моделей нет."""
import json

import pytest

from cdpm import llm as llm_mod
from cdpm import providers as prov
from cdpm import webui
from webapp import llm_backend


def _layout(model: str, provider: str = "cloud", klass: str = "cloud",
            role: str = "worker", **extra) -> dict:
    slot = {"provider": provider, "model": model, **extra}
    return {role: {"channels": {klass: slot}}}


@pytest.fixture()
def run(tmp_path, monkeypatch):
    """Прогон в изолированных каталогах: настройки CDPM — во временном
    файле, ключа облака нет (командная строка), площадочного носителя
    нет (conftest ставит CDPM_AGENTS_DEFAULT пустым)."""
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(prov, "provider_key", lambda name: None)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return run_dir


def _identity(fn) -> dict:
    return json.loads(llm_mod.channel_identity(fn))


def _site_default(tmp_path, monkeypatch, layout: dict):
    path = tmp_path / "agents-default.json"
    path.write_text(json.dumps({"form_version": 1, "customer": "площадка",
                                **layout}, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", str(path))
    return path


def test_а_пер_прогонный_сильнее_площадочного(run, tmp_path, monkeypatch):
    """При обоих носителях действует пер-прогонный agents.json:
    источник «run-explicit», модель — из прогона."""
    _site_default(tmp_path, monkeypatch, _layout("m-site"))
    (run / "agents.json").write_text(json.dumps(_layout("m-run")),
                                     encoding="utf-8")
    assert prov.agents_source(run) == "run-explicit"
    ident = _identity(prov.role_channel("any", run, "worker"))
    assert ident["config_source"] == "run-explicit"
    assert ident["model"] == "m-run"
    assert ident["requested"]["model"] == "m-run"
    assert ident["effective"]["model"] == "m-run"
    # load_agents не изменился: раскладка прогона целиком, не слияние.
    assert prov.load_agents(run)["worker"]["channels"]["cloud"]["model"] \
        == "m-run"


def test_б_только_площадочный_носитель(run, tmp_path, monkeypatch):
    _site_default(tmp_path, monkeypatch, _layout("m-site"))
    assert prov.agents_source(run) == "site-default"
    ident = _identity(prov.role_channel("any", run, "worker"))
    assert ident["config_source"] == "site-default"
    assert ident["model"] == "m-site"
    # Служебные ключи носителя раскладкой не считаются: носитель из
    # одних служебных ключей = раскладки нет.
    _site_default(tmp_path, monkeypatch, {})
    assert prov.agents_source(run) == "none"
    assert prov.load_agents(run) == {}


def test_в_без_раскладки_источник_none(run, monkeypatch):
    """S06-R2 (ревизия 1.18.5): без раскладки локальный класс строится
    с источником «none» (локальная модель — своя у площадки), а
    облачный класс НЕ строится — отказ 409 словами до вызова модели,
    без молчаливой облачной Claude «по умолчанию канала»."""
    from fastapi import HTTPException
    assert prov.agents_source(run) == "none"
    assert prov.load_agents(run) == {}
    webui._write_status(run, {"state": "извлечение идёт",
                              "kit": {"data_class": "synthetic"}})
    # Локальный класс без конфигурации роли: источник «none», просьбы
    # нет, действующее — по факту канала; журнал стадии заполнен.
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: (lambda p: "ok"))
    ident = _identity(webui._channel_for("any", run, "worker", None,
                                         klass="local"))
    assert ident["config_source"] == "none"
    assert ident["requested"] == {}
    assert "role_config" not in ident
    assert ident["effective"]["provider"] == "local"
    journal = webui._read_status(run)["stage_channels"]
    assert journal["worker/local"]["config_source"] == "none"
    assert journal["worker/local"]["stage"] == "извлечение"
    # Облачный класс без раскладки — отказ словами, канал не строится
    # (подменный завод не вызывается), журнал облачной записи не получает.
    calls: list = []
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: calls.append(requested))
    with pytest.raises(HTTPException) as e:
        webui._channel_for("any", run, "worker", None, klass="cloud")
    assert e.value.status_code == 409
    words = e.value.detail
    assert "раскладка моделей ролей не объявлена" in words
    assert "источник none" in words and "«worker»" in words
    assert "agents-default.json" in words and "agents.json" in words
    assert "локальный класс раскладки не требует" in words
    assert calls == []
    assert "worker/cloud" not in webui._read_status(run)["stage_channels"]
    # Явная просьба вызывающей стороны («cloud») раскладку не заменяет.
    with pytest.raises(HTTPException):
        webui._channel_for("any", run, "worker", "cloud", klass="cloud")
    assert calls == []


def test_в2_ранний_признак_раскладки_в_статусе(run, tmp_path, monkeypatch):
    """S06-R2: статус прогона несёт `layout` — источник раскладки и
    гейт облака; при допуске «any» без раскладки — «blocked» с теми же
    словами отказа, при объявленной раскладке или допуске «только
    локально» — «open»."""
    webui._write_status(run, {"state": "заморожено",
                              "kit": {"data_class": "synthetic"}})
    gate = webui._layout_gate(run)
    assert gate["source"] == "none" and gate["cloud_gate"] == "blocked"
    assert "раскладка моделей ролей не объявлена" in gate["detail"]
    assert "облачные каналы ролей" in gate["detail"]
    # Объявлена площадкой, но только роль worker: семейства аудитора,
    # арбитра и применителя без облачного слота — заблокировано с
    # перечнем ролей (GPT-060 P1); полная раскладка семейств — открыт.
    _site_default(tmp_path, monkeypatch, _layout("m-site"))
    gate = webui._layout_gate(run)
    assert gate["source"] == "site-default" and gate["cloud_gate"] == "blocked"
    assert gate["undeclared_roles"] == ["auditor", "arbiter", "applier"]
    assert "«auditor»" in gate["detail"] and "не объявлен слот класса «cloud»" in gate["detail"]
    full = {r: {"channels": {"cloud": {"provider": "cloud", "model": "m-site"}}}
            for r in ("worker", "auditor", "arbiter", "applier")}
    _site_default(tmp_path, monkeypatch, full)
    assert webui._layout_gate(run) == {"source": "site-default", "cloud_gate": "open"}
    # Объявлена прогоном — открыт, источник прогона.
    _site_default(tmp_path, monkeypatch, {})
    (run / "agents.json").write_text(json.dumps(
        {r: {"channels": {"cloud": {"provider": "cloud", "model": "m-run"}}}
         for r in ("worker", "auditor", "arbiter", "applier")}), encoding="utf-8")
    assert webui._layout_gate(run) == {"source": "run-explicit",
                                       "cloud_gate": "open"}
    # Допуск «только локально» без раскладки — гейт открыт: облако и так
    # не строится, локальный класс раскладки не требует.
    (run / "agents.json").unlink()
    webui._write_status(run, {"kit": {"data_class": "customer",
                                      "llm_scope": "local-only"}})
    gate = webui._layout_gate(run)
    assert gate["source"] == "none" and gate["cloud_gate"] == "open"


def test_в3_роль_без_облачного_слота_не_уезжает_в_облако(run, monkeypatch):
    """GPT-060 P1 (S06-CLOUD-SLOT-FALLBACK): роль объявлена, но её
    облачный слот — нет (только локальный слот / пустые channels / одна
    инструкция): облачный класс при разрешённом облаке — отказ 409
    словами, а не «cloud, модель по умолчанию»; локальный класс той же
    роли строится; при допуске «только локально» старший — отказ допуска."""
    from fastapi import HTTPException
    webui._write_status(run, {"state": "заморожено",
                              "kit": {"data_class": "synthetic"}})
    calls: list = []
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: calls.append(requested) or (lambda p: "ok"))
    monkeypatch.setattr(llm_mod, "channel",
                        lambda scope="any", provider=None, model=None, **kw:
                        calls.append(("channel", provider, model)) or (lambda p: "ok"))
    for layout in ({"worker": {"channels": {"local": {"provider": "local", "model": "q"}}}},
                   {"worker": {"channels": {}}},
                   {"worker": {"instruction": "будь краток"}}):
        (run / "agents.json").write_text(json.dumps(layout, ensure_ascii=False),
                                         encoding="utf-8")
        with pytest.raises(HTTPException) as e:
            webui._channel_for("any", run, "extractor", None, klass="cloud")
        assert e.value.status_code == 409
        words = e.value.detail
        assert "объявлена (источник run-explicit" in words
        assert "«extractor»" in words and "«worker»" in words
        assert "не объявлен слот класса «cloud»" in words
        # Признак раскладки в статусе называет роль заранее.
        gate = webui._layout_gate(run)
        assert gate["cloud_gate"] == "blocked" and "worker" in gate["undeclared_roles"]
        assert "не объявлен слот класса «cloud»" in gate["detail"]
    assert not [c for c in calls if isinstance(c, tuple) and c[1] in (None, "cloud")], calls
    # Роль, которой в объявленной раскладке нет вовсе (auditor при
    # worker-only agents.json): слова называют настоящий источник и
    # «не настроена», а не «раскладки нет / нет agents.json» (GPT-062 P2).
    with pytest.raises(HTTPException) as e:
        webui._channel_for("any", run, "auditor", None, klass="cloud")
    assert "объявлена (источник run-explicit" in e.value.detail
    assert "роль «auditor» в ней не настроена" in e.value.detail
    assert "нет agents.json" not in e.value.detail
    # Локальный класс той же роли — строится (локальная модель объявлена).
    (run / "agents.json").write_text(json.dumps(
        {"worker": {"channels": {"local": {"provider": "local", "model": "q"}}}}),
        encoding="utf-8")
    webui._channel_for("any", run, "extractor", None, klass="local")
    # (Допуск «только локально» как старший отказ — test_ж, настоящий
    # канал; здесь llm.channel подменён.)
    # Слот с моделью без провайдера или с провайдером без модели — объявлен
    # (у всех четырёх семейств), гейт открыт.
    for slot in ({"model": "m-only"}, {"provider": "cloud"}):
        (run / "agents.json").write_text(json.dumps(
            {r: {"channels": {"cloud": dict(slot)}}
             for r in ("worker", "auditor", "arbiter", "applier")}), encoding="utf-8")
        assert webui._layout_gate(run)["cloud_gate"] == "open"


def test_г_просьба_и_факт_канала(run, monkeypatch):
    """Вид канала по той же логике, что строит канал: облако с ключом —
    API, без ключа — командная строка; локальная модель — local;
    внешний провайдер — API."""
    (run / "agents.json").write_text(json.dumps(_layout("m-x")),
                                     encoding="utf-8")
    no_key = _identity(prov.role_channel("any", run, "worker"))
    assert no_key["requested"] == {"provider": "cloud", "model": "m-x",
                                   "channel": "cli"}
    assert no_key["effective"] == {"provider": "cloud", "model": "m-x",
                                   "channel": "cli"}
    monkeypatch.setattr(prov, "provider_key",
                        lambda name: "sk-test" if name == "cloud" else None)
    # Источник ключа у исполнителя (webapp.llm_backend) — тот же мир:
    # закреплённый транспорт канала (S06-R1/IDENTITY-1) читает его,
    # а не providers.provider_key; ключ CDPM синхронизируется в среду
    # sync_cloud_key_to_env — в тесте оба источника согласованы явно.
    monkeypatch.setattr(llm_backend._settings, "has_api_key", lambda: True)
    with_key = _identity(prov.role_channel("any", run, "worker"))
    assert with_key["requested"]["channel"] == "api"
    assert with_key["effective"] == {"provider": "cloud", "model": "m-x",
                                     "channel": "api"}
    assert with_key != no_key
    # Облако без модели при ключе: просьба без модели, действующая —
    # модель умолчания площадки (та же подстановка, что у cosmos_llm).
    monkeypatch.setattr(prov, "load_settings",
                        lambda: {"cloud_default_model": "claude-default-x"})
    (run / "agents.json").write_text(json.dumps(
        {"worker": {"channels": {"cloud": {"provider": "cloud"}}}}),
        encoding="utf-8")
    dflt = _identity(prov.role_channel("any", run, "worker"))
    assert dflt["requested"] == {"provider": "cloud", "model": None,
                                 "channel": "api"}
    assert dflt["effective"] == {"provider": "cloud",
                                 "model": "claude-default-x",
                                 "channel": "api"}
    # Локальная модель.
    (run / "agents.json").write_text(json.dumps(
        _layout("qwen-x", provider="local", klass="local")),
        encoding="utf-8")
    local = _identity(prov.role_channel("any", run, "worker", klass="local"))
    assert local["requested"] == {"provider": "local", "model": "qwen-x",
                                  "channel": "local"}
    assert local["effective"] == {"provider": "local", "model": "qwen-x",
                                  "channel": "local"}
    # Внешний провайдер — только API.
    (run / "agents.json").write_text(json.dumps(
        _layout("gpt-x", provider="openai")), encoding="utf-8")
    ext = _identity(prov.role_channel("any", run, "worker"))
    assert ext["requested"] == {"provider": "openai", "model": "gpt-x",
                                "channel": "api"}
    assert ext["effective"] == {"provider": "openai", "model": "gpt-x",
                                "channel": "api"}
    # Слот «внутри страны» без провайдера — локальная модель роли
    # (строже, не мягче): просьба без провайдера, факт — local.
    (run / "agents.json").write_text(json.dumps({"worker": {"channels": {
        "cloud-country": {}, "local": {"model": "qwen-x"}}}}),
        encoding="utf-8")
    cc = _identity(prov.role_channel("any", run, "worker",
                                     klass="cloud-country"))
    assert cc["requested"] == {"provider": None, "model": None,
                               "channel": None}
    assert cc["effective"] == {"provider": "local", "model": "qwen-x",
                               "channel": "local"}


def test_д_отпечаток_валидный_json_со_старыми_полями(run, tmp_path,
                                                     monkeypatch):
    layout = _layout("m-run", effort="high")
    (run / "agents.json").write_text(json.dumps(layout), encoding="utf-8")
    raw = llm_mod.channel_identity(prov.role_channel("any", run, "worker"))
    got = json.loads(raw)
    assert raw == json.dumps(got, ensure_ascii=False, sort_keys=True)
    assert got["role"] == "worker" and got["klass"] == "cloud"
    assert got["role_config"] == layout["worker"]
    assert got["provider"] == "cloud" and got["model"] == "m-run"
    assert got["effort"] == "high"
    assert set(got) >= {"config_source", "requested", "effective"}
    assert "key" not in raw.lower()
    # Смена источника при той же модели — другой отпечаток (другой
    # ключ кэша стадий): та же раскладка с площадки. Второй замок
    # (отпечаток кэша извлечения, extract-cache/meta.json) — тоже.
    fp_run = webui._extract_cache_fingerprint(run, "any", {})
    (run / "agents.json").unlink()
    _site_default(tmp_path, monkeypatch, layout)
    site = json.loads(llm_mod.channel_identity(
        prov.role_channel("any", run, "worker")))
    assert site["model"] == got["model"]
    assert site["config_source"] == "site-default"
    assert site != got
    assert prov.load_agents(run) == layout
    assert webui._extract_cache_fingerprint(run, "any", {}) != fp_run


def test_е_отказ_канала_не_подменяет_модель(run, monkeypatch):
    """Отказ канала — отказ стадии с именем того же канала; модель и
    отпечаток не подменяются."""
    from webapp import llm_backend
    (run / "agents.json").write_text(json.dumps(_layout("m-run")),
                                     encoding="utf-8")
    monkeypatch.setattr(llm_backend, "complete", lambda prompt, **kw: {
        "ok": False, "text": "", "backend": kw["backend"],
        "model": kw.get("model"), "error": "cloud CLI timed out"})
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    fn = prov.role_channel("any", run, "worker")
    with pytest.raises(llm_mod.LLMCallError) as e:
        fn("привет")
    assert "cloud/m-run" in str(e.value)
    assert _identity(fn)["effective"]["model"] == "m-run"


def test_ж_local_only_не_строит_облачный_слот(run):
    from webapp.llm_backend import ScopeViolation
    (run / "agents.json").write_text(json.dumps(_layout("m-run")),
                                     encoding="utf-8")
    with pytest.raises(ScopeViolation):
        prov.role_channel("local-only", run, "worker")
    (run / "agents.json").write_text(json.dumps(
        _layout("gpt-x", provider="openai")), encoding="utf-8")
    with pytest.raises(ScopeViolation):
        prov.role_channel("local-only", run, "worker")


def test_з_журнал_стадии_из_role_channel(run):
    """Канал с конфигурацией роли тоже попадает в журнал статуса —
    с источником, просьбой и фактом; без статуса журнала нет."""
    (run / "agents.json").write_text(json.dumps(_layout("m-run")),
                                     encoding="utf-8")
    # Каталог без статуса — журнала нет (файл статуса не рождается).
    webui._note_stage_channel(run, prov.role_channel("any", run, "worker"))
    assert not (run / "status.json").exists()
    webui._write_status(run, {"state": "заморожено",
                              "kit": {"data_class": "synthetic"}})
    webui._channel_for("any", run, "extractor", None, klass="cloud")
    entry = webui._read_status(run)["stage_channels"]["worker/cloud"]
    assert entry["config_source"] == "run-explicit"
    assert entry["requested"]["model"] == "m-run"
    assert entry["effective"] == {"provider": "cloud", "model": "m-run",
                                  "channel": "cli"}
    assert entry["stage"] is None        # линия не занята
