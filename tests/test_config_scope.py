"""Конфигурация к ЭКЗЕМПЛЯРУ, а не к установке (замечание аудита P1-10).

Проверяется то, ради чего правка делалась:
  * порядок разрешения — встроенные / шаблон установки / набор / сценарий;
  * набор получает СВОЙ полный файл при создании, и правка шаблона после
    этого его не касается;
  * перенос наличных наборов ЧИСТЫЙ — значения не меняются;
  * права: чужой набор — 404, а не 403;
  * строгий режим остаётся ТОЛЬКО у сценариев (иначе перенос изменил бы
    результаты уже посчитанных наборов).
"""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

import rules_config
from webapp import config_scope, jobs, registry
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
OTHER = "someone.else@example.com"


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Все четыре уровня — во временную папку (autouse-фикстура conftest
    уже уводит их, здесь фиксируем пути явно, чтобы тест ими управлял)."""
    root = tmp_path / "config"
    (root / "instance-rules").mkdir(parents=True, exist_ok=True)
    (root / "scenario-rules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setitem(config_scope.INSTALL_FILES, config_scope.DOMAIN_RULES,
                        root / "airline_default.json")
    monkeypatch.setitem(config_scope.INSTALL_FILES, config_scope.DOMAIN_COST,
                        root / "cost_config.json")
    monkeypatch.setitem(config_scope.INSTALL_FILES, config_scope.DOMAIN_PAY,
                        root / "pay_config.json")
    for domain in config_scope.DOMAINS:
        monkeypatch.setitem(config_scope.INSTANCE_DIRS, domain,
                            root / f"instance-{domain}")
    monkeypatch.setitem(config_scope.SCENARIO_DIRS, config_scope.DOMAIN_RULES,
                        root / "scenario-rules")
    return root


def _install(root, **params):
    (root / "airline_default.json").write_text(
        json.dumps({"parameters": params}), encoding="utf-8")


# --- порядок разрешения -------------------------------------------------------

def test_defaults_when_nothing_is_stored(iso):
    path, origin = config_scope.resolve(config_scope.DOMAIN_RULES, "gerad-1")
    assert path is None and origin == config_scope.ORIGIN_DEFAULTS
    assert config_scope.effective(config_scope.DOMAIN_RULES, "gerad-1") \
        == dict(rules_config.DEFAULTS)


def test_installation_template_applies_until_the_instance_has_its_own(iso):
    _install(iso, min_sit_min=45)
    assert config_scope.origin_for(config_scope.DOMAIN_RULES, "gerad-1") \
        == config_scope.ORIGIN_INSTALLATION
    assert config_scope.effective(
        config_scope.DOMAIN_RULES, "gerad-1")["min_sit_min"] == 45


def test_instance_rules_beat_the_installation_template(iso):
    _install(iso, min_sit_min=45)
    config_scope.write_instance(
        config_scope.DOMAIN_RULES, "gerad-1",
        {**rules_config.DEFAULTS, "min_sit_min": 55})
    assert config_scope.origin_for(config_scope.DOMAIN_RULES, "gerad-1") \
        == config_scope.ORIGIN_INSTANCE
    assert config_scope.effective(
        config_scope.DOMAIN_RULES, "gerad-1")["min_sit_min"] == 55
    # соседний набор шаблоном не задет
    assert config_scope.effective(
        config_scope.DOMAIN_RULES, "gerad-2")["min_sit_min"] == 45


def test_editing_the_template_does_not_touch_a_materialized_instance(iso):
    """Суть замечания P1-10: правка администратора не имеет права менять
    нормы чужого набора задним числом."""
    _install(iso, min_sit_min=30)
    config_scope.materialize_instance("gerad-1")
    _install(iso, min_sit_min=90)            # администратор правит шаблон
    assert config_scope.effective(
        config_scope.DOMAIN_RULES, "gerad-1")["min_sit_min"] == 30
    # а новый набор берёт уже новый шаблон
    config_scope.materialize_instance("gerad-2")
    assert config_scope.effective(
        config_scope.DOMAIN_RULES, "gerad-2")["min_sit_min"] == 90


def test_unknown_domain_is_loud(iso):
    with pytest.raises(ValueError):
        config_scope.resolve("нет-такой-области", "gerad-1")


# --- перенос ------------------------------------------------------------------

def test_materialize_is_clean_and_idempotent(iso):
    _install(iso, min_sit_min=45, max_landings_per_duty=4)
    before = config_scope.effective(config_scope.DOMAIN_RULES, "gerad-1")
    assert config_scope.materialize_instance("gerad-1") \
        == list(config_scope.DOMAINS)
    after = config_scope.effective(config_scope.DOMAIN_RULES, "gerad-1")
    assert after == before, "перенос обязан быть ЧИСТЫМ: значения те же"
    # повтор ничего не пишет
    assert config_scope.materialize_instance("gerad-1") == []


def test_materialize_all_covers_every_instance(iso):
    res = config_scope.materialize_all()
    assert res["instances"] > 0 and not res["failed"]
    for inst in registry.list_instances():
        assert config_scope.origin_for(config_scope.DOMAIN_RULES, inst) \
            == config_scope.ORIGIN_INSTANCE


def test_reset_rewrites_from_the_template_not_a_live_link(iso):
    _install(iso, min_sit_min=30)
    config_scope.write_instance(
        config_scope.DOMAIN_RULES, "gerad-1",
        {**rules_config.DEFAULTS, "min_sit_min": 55})
    _install(iso, min_sit_min=70)
    config_scope.reset_instance(config_scope.DOMAIN_RULES, "gerad-1")
    assert config_scope.effective(
        config_scope.DOMAIN_RULES, "gerad-1")["min_sit_min"] == 70
    # именно ПЕРЕПИСАН, а не удалён: уровень набора остаётся
    assert config_scope.origin_for(config_scope.DOMAIN_RULES, "gerad-1") \
        == config_scope.ORIGIN_INSTANCE
    _install(iso, min_sit_min=99)
    assert config_scope.effective(
        config_scope.DOMAIN_RULES, "gerad-1")["min_sit_min"] == 70


# --- проводка в запуски -------------------------------------------------------

def test_job_env_points_at_the_instance_rules(iso):
    config_scope.write_instance(
        config_scope.DOMAIN_RULES, "gerad-1",
        {**rules_config.DEFAULTS, "min_sit_min": 55})
    env = jobs._base_env({"id": "gerad-1", "label": "inst1"})
    assert env["CREW_RULES_CONFIG"].endswith("gerad-1.json")
    assert rules_config.load_from(
        env["CREW_RULES_CONFIG"])["min_sit_min"] == 55


def test_strict_mode_stays_a_scenario_only_flag(iso):
    """Строгий режим на уровне набора включать НЕЛЬЗЯ: после переноса файл
    есть у каждого набора, предустановки профиля этапа 1 перестали бы
    применяться везде разом, и результаты разошлись бы с посчитанными."""
    config_scope.write_instance(config_scope.DOMAIN_RULES, "gerad-1",
                                dict(rules_config.DEFAULTS))
    env = jobs._base_env({"id": "gerad-1", "label": "inst1"})
    assert "CREW_RULES_STRICT" not in env
    assert config_scope.is_strict(config_scope.DOMAIN_RULES, "gerad-1") is False


def test_orphan_scenario_file_has_no_power(iso, monkeypatch):
    """Файл в каталоге сценариев без записи в реестре сценариев не должен
    командовать расчётом (семантика сохранена с 2026-07-15)."""
    (iso / "scenario-rules" / "gerad-1.json").write_text(
        json.dumps({"parameters": {"min_sit_min": 77}}), encoding="utf-8")
    assert config_scope.scenario_path(
        config_scope.DOMAIN_RULES, "gerad-1") is None


# --- точки входа и права ------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch, iso):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_instance_listing_carries_the_rules_origin(client, iso):
    """Происхождение обязано быть в САМОМ перечне: иначе экран называет его
    только для набора, который уже открывали, а остальные молчат."""
    _install(iso, min_sit_min=30)
    rows = client.get("/api/instances").json()
    assert rows and all("rules_origin" in r for r in rows)
    by_id = {r["id"]: r["rules_origin"] for r in rows}
    assert by_id["gerad-1"] == config_scope.ORIGIN_INSTALLATION
    config_scope.write_instance(config_scope.DOMAIN_RULES, "gerad-1",
                                {**rules_config.DEFAULTS, "min_sit_min": 55})
    rows2 = client.get("/api/instances").json()
    by_id2 = {r["id"]: r["rules_origin"] for r in rows2}
    assert by_id2["gerad-1"] == config_scope.ORIGIN_INSTANCE
    assert by_id2["gerad-2"] == config_scope.ORIGIN_INSTALLATION


def test_api_get_reports_origin_and_enforcement(client, iso):
    _install(iso, min_sit_min=rules_config.DEFAULTS["min_sit_min"])
    r = client.get("/api/instances/gerad-1/rules")
    assert r.status_code == 200
    body = r.json()
    assert body["instance"] == "gerad-1"
    assert body["origin"] == config_scope.ORIGIN_INSTALLATION
    assert body["values"]["min_sit_min"] == \
        rules_config.DEFAULTS["min_sit_min"]
    # различие «ищет решатель» против «только проверяет сертификат»
    assert body["enforcement"]["min_sit_min"] == "solver"
    assert body["enforcement"]["comp_language_mix"] == "certificate"


def test_api_put_is_partial_over_the_instance_not_the_defaults(client, iso):
    client.put("/api/instances/gerad-1/rules",
               json={"parameters": {"min_sit_min": 55}})
    r = client.put("/api/instances/gerad-1/rules",
                   json={"parameters": {"max_landings_per_duty": 4}})
    assert r.status_code == 200
    values = r.json()["values"]
    assert values["max_landings_per_duty"] == 4
    assert values["min_sit_min"] == 55, \
        "неупомянутый параметр не имеет права молча откатиться"
    assert r.json()["origin"] == config_scope.ORIGIN_INSTANCE
    assert "min_sit_min" in r.json()["overridden"]


def test_api_put_validates_with_the_loader_semantics(client, iso):
    bad = client.put("/api/instances/gerad-1/rules",
                     json={"parameters": {"min_sit_min": 30.5}})
    assert bad.status_code == 422
    unknown = client.put("/api/instances/gerad-1/rules",
                         json={"parameters": {"нет_такого": 1}})
    assert unknown.status_code == 422
    shape = client.put("/api/instances/gerad-1/rules", json={"parameters": 5})
    assert shape.status_code == 422


def test_api_delete_resets_to_the_template(client, iso):
    _install(iso, min_sit_min=70)
    client.put("/api/instances/gerad-1/rules",
               json={"parameters": {"min_sit_min": 55}})
    r = client.delete("/api/instances/gerad-1/rules")
    assert r.status_code == 200
    assert r.json()["values"]["min_sit_min"] == 70
    assert r.json()["overridden"] == []


def test_api_rates_are_per_instance_too(client, iso):
    """Ставки — коммерческая тайна авиакомпании: считать стоимость одного
    набора по ставкам другого нельзя."""
    r = client.put("/api/instances/gerad-1/rates",
                   json={"cost": {"block_hour_rate": 175},
                         "pay": {"monthly_guarantee_h": 80}})
    assert r.status_code == 200
    body = r.json()
    assert body["cost"]["values"]["block_hour_rate"] == 175
    assert body["pay"]["values"]["monthly_guarantee_h"] == 80.0
    assert body["cost"]["origin"] == config_scope.ORIGIN_INSTANCE
    # соседний набор не задет
    other = client.get("/api/instances/gerad-2/rates").json()
    assert other["cost"]["values"]["block_hour_rate"] != 175
    # неупомянутое поле не откатывается
    r2 = client.put("/api/instances/gerad-1/rates",
                    json={"cost": {"per_diem_hour": 4.0}})
    assert r2.json()["cost"]["values"]["block_hour_rate"] == 175
    # сброс возвращает к шаблону установки
    r3 = client.delete("/api/instances/gerad-1/rates")
    assert r3.json()["cost"]["values"]["block_hour_rate"] != 175


def test_api_rates_validation_and_shape(client, iso):
    assert client.put("/api/instances/gerad-1/rates",
                      json={"cost": {"per_diem_hour": -3}}).status_code == 422
    assert client.put("/api/instances/gerad-1/rates",
                      json={"pay": {"duty_rig": 0}}).status_code == 422
    assert client.put("/api/instances/gerad-1/rates",
                      json={"нет": 1}).status_code == 422


def test_costing_uses_the_instance_rates(client, iso):
    """Отчёт стоимости обязан идти по ставкам своего набора."""
    from webapp import costing
    base = costing.instance_costing("gerad-1")
    assert base and base.get("available")
    client.put("/api/instances/gerad-1/rates",
               json={"cost": {"block_hour_rate":
                              float(base["totals"]["pay_cost"]) and 1000.0}})
    after = costing.instance_costing("gerad-1")
    assert after["totals"]["pay_cost"] > base["totals"]["pay_cost"]


def test_api_reports_a_broken_rules_file_loudly(client, iso):
    """Битый файл правил — отказ с текстом причины, а не молчаливый откат
    к умолчаниям: расчёт по этому файлу тоже упал бы."""
    (iso / "instance-rules").mkdir(parents=True, exist_ok=True)
    (iso / "instance-rules" / "gerad-1.json").write_text(
        '{"parameters": {"min_sit_min": "тридцать"}}', encoding="utf-8")
    r = client.get("/api/instances/gerad-1/rules")
    assert r.status_code == 500 and "min_sit_min" in r.json()["detail"]


def test_api_unknown_instance_is_404_not_403(client, iso):
    assert client.get("/api/instances/upload-nope/rules").status_code == 404
    assert client.put("/api/instances/upload-nope/rules",
                      json={"parameters": {}}).status_code == 404


def test_api_foreign_instance_is_404_for_a_plain_user(tmp_path, monkeypatch,
                                                     iso):
    """Чужой набор — 404, а не 403: существование чужого набора не утекает."""
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        users.add(OTHER, "user", by="pytest-config-scope")
        monkeypatch.setenv("CREW_DEV_USER", OTHER)
        # эталонные наборы принадлежат первичному администратору
        assert c.get("/api/instances/gerad-1/rules").status_code == 404
        assert c.put("/api/instances/gerad-1/rules",
                     json={"parameters": {"min_sit_min": 45}}).status_code == 404
        assert c.delete("/api/instances/gerad-1/rules").status_code == 404
