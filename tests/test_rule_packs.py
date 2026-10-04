"""Rule Pack (сердце онбординга, Ф7): происхождение обязательно, тесты
исполняются детерминированно, дисциплина утверждения, компиляция в Ярус 1,
перенос пакета, применение к airline-config, гейты API."""
import importlib
import json

import pytest

from webapp import config_scope
from fastapi.testclient import TestClient

from webapp import rule_packs as rp
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
USER = "someone@example.com"


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "PACKS_PATH", tmp_path / "packs.json")
    yield tmp_path


def _rule(**over):
    base = {
        "title": "Минимальный отдых",
        "statement": "Отдых между нарядами не короче 600 минут.",
        "source": {"document": "Приказ №139", "section": "п. 12"},
        "constraint": {"kind": "parameter", "parameter": "min_rest_min",
                       "value": 600},
        "tests": [{"kind": "positive", "given": 650},
                  {"kind": "negative", "given": 500}],
    }
    base.update(over)
    return base


def _pack(iso_dir=None):
    return rp.create_pack({"name": "Россия базовый", "country": "ru"}, ADMIN)


# ----------------------------------------------------------- rule lifecycle

def test_rule_requires_source_and_statement(iso):
    p = _pack()
    with pytest.raises(rp.RulePackError, match="источник"):
        rp.add_rule(p["id"], _rule(source={}), ADMIN)
    with pytest.raises(rp.RulePackError):
        rp.add_rule(p["id"], _rule(statement=""), ADMIN)


def test_parameter_tests_run_deterministically(iso):
    p = _pack()
    r = rp.add_rule(p["id"], _rule(), ADMIN)
    rep = rp.rule_test_report(r)
    assert rep["pass"] == 2 and rep["fail"] == 0 and rep["ok"] is True
    # max_* direction: given above the cap must be illegal
    r2 = rp.add_rule(p["id"], _rule(
        title="Потолок плеча", constraint={
            "kind": "parameter", "parameter": "max_duty_span_min",
            "value": 840},
        tests=[{"kind": "positive", "given": 800},
               {"kind": "negative", "given": 900}]), ADMIN)
    rep2 = rp.rule_test_report(r2)
    assert rep2["ok"] is True
    # a WRONG expectation fails loudly
    r3 = rp.add_rule(p["id"], _rule(
        title="Ошибочный тест",
        tests=[{"kind": "positive", "given": 500},    # 500 < 600 -> illegal
               {"kind": "negative", "given": 500}]), ADMIN)
    rep3 = rp.rule_test_report(r3)
    assert rep3["fail"] == 1 and rep3["ok"] is False


def test_note_rules_are_manual(iso):
    p = _pack()
    r = rp.add_rule(p["id"], _rule(
        title="Справедливость",
        constraint={"kind": "note", "note": "вне Яруса 1"},
        tests=[{"kind": "positive", "description": "описан кейс"},
               {"kind": "negative", "description": "контр-кейс"}]), ADMIN)
    rep = rp.rule_test_report(r)
    assert rep["manual"] == 2 and rep["fail"] == 0 and rep["ok"] is True


def test_approval_discipline(iso):
    p = _pack()
    # no negative test -> not approvable
    r = rp.add_rule(p["id"], _rule(
        tests=[{"kind": "positive", "given": 650}]), ADMIN)
    with pytest.raises(rp.RulePackError, match="отрицательный"):
        rp.approve_rule(p["id"], r["id"], ADMIN)
    # failing test -> not approvable
    r2 = rp.add_rule(p["id"], _rule(
        title="x", tests=[{"kind": "positive", "given": 100},
                          {"kind": "negative", "given": 500}]), ADMIN)
    with pytest.raises(rp.RulePackError, match="тесты"):
        rp.approve_rule(p["id"], r2["id"], ADMIN)
    # good rule approves; edit resets the approval
    r3 = rp.add_rule(p["id"], _rule(title="ok"), ADMIN)
    out = rp.approve_rule(p["id"], r3["id"], ADMIN)
    assert out["status"] == "approved" and out["approved_by"] == ADMIN
    edited = rp.update_rule(p["id"], r3["id"], _rule(title="ok-edited"))
    assert edited["status"] == "draft" and "approved_by" not in edited


def test_pack_approval_requires_all_rules(iso):
    p = _pack()
    with pytest.raises(rp.RulePackError, match="нет правил"):
        rp.approve_pack(p["id"], ADMIN)
    r = rp.add_rule(p["id"], _rule(), ADMIN)
    with pytest.raises(rp.RulePackError, match="не утверждены"):
        rp.approve_pack(p["id"], ADMIN)
    rp.approve_rule(p["id"], r["id"], ADMIN)
    out = rp.approve_pack(p["id"], ADMIN)
    assert out["status"] == "approved"
    # a new rule un-approves the pack
    rp.add_rule(p["id"], _rule(title="new"), ADMIN)
    assert rp.get_pack(p["id"])["status"] == "draft"


def test_compile_and_conflicts(iso):
    p = _pack()
    rp.add_rule(p["id"], _rule(), ADMIN)
    rp.add_rule(p["id"], _rule(
        title="note", constraint={"kind": "note", "note": "n"},
        tests=[]), ADMIN)
    comp = rp.compile_pack(rp.get_pack(p["id"]))
    assert comp["parameters"] == {"min_rest_min": 600.0}
    assert comp["outside_tier1"] == ["note"]
    assert comp["conflicts"] == []
    rp.add_rule(p["id"], _rule(title="конфликт", constraint={
        "kind": "parameter", "parameter": "min_rest_min",
        "value": 700}), ADMIN)
    comp2 = rp.compile_pack(rp.get_pack(p["id"]))
    assert len(comp2["conflicts"]) == 1


def test_import_pack_resets_approvals(iso):
    p = _pack()
    r = rp.add_rule(p["id"], _rule(), ADMIN)
    rp.approve_rule(p["id"], r["id"], ADMIN)
    rp.approve_pack(p["id"], ADMIN)
    exported = rp.pack_view(rp.get_pack(p["id"]))
    out = rp.import_pack(json.loads(json.dumps(exported)), USER)
    assert out["imported_rules"] == 1 and not out["errors"]
    новый = out["pack"]
    assert новый["status"] == "draft"
    assert all(r["status"] == "draft" for r in новый["rules"])
    assert новый["id"] != p["id"]


def test_unknown_parameter_rejected(iso):
    p = _pack()
    with pytest.raises(rp.RulePackError, match="unknown parameter"):
        rp.add_rule(p["id"], _rule(constraint={
            "kind": "parameter", "parameter": "nope", "value": 1}), ADMIN)


# --------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(rp, "PACKS_PATH", tmp_path / "packs.json")
    users.ensure_db()
    users.add(USER, "user", by=ADMIN)
    from webapp import app as app_module
    importlib.reload(app_module)
    monkeypatch.setitem(config_scope.INSTALL_FILES, config_scope.DOMAIN_RULES,
                        tmp_path / "airline.json")
    monkeypatch.setattr(app_module, "CONFIG_DIR", tmp_path)
    with TestClient(app_module.app) as c:
        yield c


def test_api_read_for_all_write_for_admin(client, monkeypatch):
    r = client.post("/api/rule-packs", json={"name": "P"})
    assert r.status_code == 200
    pid = r.json()["id"]
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert client.get("/api/rule-packs").status_code == 200      # read: all
    assert client.get(f"/api/rule-packs/{pid}").status_code == 200
    assert client.post("/api/rule-packs",
                       json={"name": "X"}).status_code == 403
    assert client.post(f"/api/rule-packs/{pid}/rules",
                       json=_rule()).status_code == 403


def test_api_full_cycle_apply_config(client):
    pid = client.post("/api/rule-packs",
                      json={"name": "RU", "country": "ru"}).json()["id"]
    r = client.post(f"/api/rule-packs/{pid}/rules", json=_rule())
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    # cannot apply an unapproved pack
    assert client.post(
        f"/api/rule-packs/{pid}/apply-config").status_code == 422
    assert client.post(
        f"/api/rule-packs/{pid}/rules/{rid}/approve").status_code == 200
    assert client.post(f"/api/rule-packs/{pid}/approve").status_code == 200
    out = client.post(f"/api/rule-packs/{pid}/apply-config")
    assert out.status_code == 200, out.text
    assert out.json()["applied"] == {"min_rest_min": 600.0}
    cfg = client.get("/api/airline-config").json()
    assert cfg["parameters"]["min_rest_min"] == 600.0
    # live test report present in the pack view
    view = client.get(f"/api/rule-packs/{pid}").json()
    assert view["rules"][0]["test_report"]["ok"] is True
    assert view["compile"]["parameters"] == {"min_rest_min": 600.0}


def test_rule_value_int32_overflow_rejected(iso):
    # Аудит 2026-07-19 (P1-12): 3_000_000_000 проходил подпись пакета и
    # затем убивал JVM (Kotlin RulesConfig.intOf) — режем на слое пакета.
    p = _pack()
    with pytest.raises(rp.RulePackError, match="32-bit"):
        rp.add_rule(p["id"], _rule(constraint={
            "kind": "parameter", "parameter": "min_rest_min",
            "value": 3_000_000_000}), ADMIN)
    # Максимально допустимое Int32-значение остаётся валидным.
    r = rp.add_rule(p["id"], _rule(constraint={
        "kind": "parameter", "parameter": "min_rest_min",
        "value": 2_147_483_647}, tests=[]), ADMIN)
    assert r["constraint"]["value"] == 2_147_483_647


def test_airline_config_surface_includes_comp_rules(client):
    # Аудит-2 (P2-05): comp_* были невидимы GET, отвергались PUT и молча
    # терялись при мерже — правило нельзя было включить через Установки.
    cfg = client.get("/api/airline-config").json()["parameters"]
    for key in ("comp_no_two_reserves", "comp_min_pair_seniority_weight",
                "comp_language_mix"):
        assert key in cfg, key
        assert cfg[key] == 0                       # дефолт: выключено
    # PUT принимает включение правила...
    r = client.put("/api/airline-config",
                   json={"parameters": {"comp_language_mix": 1}})
    assert r.status_code == 200, r.text
    assert r.json()["parameters"]["comp_language_mix"] == 1
    # ...и НЕ теряет его при последующем частичном сохранении другого
    # параметра (ревью пакета: PUT слоится поверх текущего конфига)
    r2 = client.put("/api/airline-config",
                    json={"parameters": {"min_rest_min": 630}})
    assert r2.status_code == 200, r2.text
    assert r2.json()["parameters"]["comp_language_mix"] == 1
    assert r2.json()["parameters"]["min_rest_min"] == 630
    # диапазоны: флаг не принимает 2, порог не принимает 23
    assert client.put("/api/airline-config", json={
        "parameters": {"comp_language_mix": 2}}).status_code == 422
    assert client.put("/api/airline-config", json={
        "parameters": {"comp_min_pair_seniority_weight": 23}}).status_code \
        == 422
    # выключение обратно (0 — валидное значение, min=0)
    assert client.put("/api/airline-config", json={
        "parameters": {"comp_language_mix": 0}}).status_code == 200


# --- отбор пакетов для прогона (повторный аудит 2026-07-26, P0-04) -----------

def _write_packs(tmp_path, monkeypatch, packs):
    import json as _json

    from webapp import rule_packs as rp
    p = tmp_path / "rule_packs.json"
    p.write_text(_json.dumps({"packs": packs}, ensure_ascii=False),
                 encoding="utf-8")
    monkeypatch.setattr(rp, "PACKS_PATH", p)
    rp._load.cache_clear() if hasattr(rp._load, "cache_clear") else None
    return rp


def _resource_rule(name, status="approved", lo=720):
    return {"id": name, "status": status,
            "constraint": {"kind": "resource", "resource": name,
                           "unit": "minutes", "level": "week",
                           "window": {"min": lo}}}


def test_draft_and_foreign_packs_stay_out_of_the_run(tmp_path, monkeypatch):
    """Прежде компилировались ВСЕ записи глобального реестра — черновик
    «еженедельного отдыха» одного арендатора попадал в действующие
    правила чужого набора. Действует только approved-пакет своей (или
    всеобщей) юрисдикции, и только approved-правила в нём."""
    _write_packs(tmp_path, monkeypatch, [
        {"id": "p1", "name": "наш", "status": "approved", "country": "XA",
         "rules": [_resource_rule("weeklyRest")]},
        {"id": "p2", "name": "черновик", "status": "draft", "country": "XA",
         "rules": [_resource_rule("monthlyRest")]},
        {"id": "p3", "name": "чужой", "status": "approved", "country": "XB",
         "rules": [_resource_rule("weeklyRest", lo=999)]},
        {"id": "p4", "name": "всеобщий", "status": "approved",
         "country": None,
         "rules": [_resource_rule("weeklyRest", status="draft")]},
    ])
    from webapp import jobs
    packs = list(jobs._eligible_rule_packs({"id": "x", "country": "XA"}))
    assert [p["id"] for p in packs] == ["p1", "p4"]
    # черновичное правило внутри approved-пакета тоже отфильтровано
    assert packs[1]["rules"] == []
    # а для набора XB действует пакет XB, но не XA
    kz = [p["id"] for p in
          jobs._eligible_rule_packs({"id": "y", "country": "XB"})]
    assert kz == ["p3", "p4"]


def test_declared_resources_match_the_same_selection(tmp_path, monkeypatch):
    """Объявленное обязано совпадать с применимым тем же отбором: иначе
    черновик объявляет правило, которое никогда не будет применено, и
    каждый прогон вечно считается неполным."""
    _write_packs(tmp_path, monkeypatch, [
        {"id": "p1", "status": "approved", "country": None,
         "rules": [_resource_rule("weeklyRest")]},
        {"id": "p2", "status": "draft", "country": None,
         "rules": [_resource_rule("monthlyRest")]},
    ])
    from webapp import jobs
    assert jobs._declared_resources({"id": "x", "country": "INTL"}) \
        == ["weeklyRest"]


# --- привязка пакета к набору (пятый аудит, R5-04) ---------------------------

def test_instance_scoped_pack_is_invisible_to_other_instances(tmp_path,
                                                              monkeypatch):
    """Отбор по стране — не изоляция арендаторов: пакет одного набора
    попадал в постановку другого, а при конфликте значений ещё и
    блокировал ему запуск. Пакет с областью instance:<id> обязан быть
    невидим чужим наборам."""
    rp = _write_packs(tmp_path, monkeypatch, [
        {"id": "own", "name": "мой", "status": "approved", "country": "XA",
         "scope": "instance:upload-a",
         "rules": [_resource_rule("weeklyRest", lo=720)]},
        {"id": "alien", "name": "чужой", "status": "approved",
         "country": "XA", "scope": "instance:upload-b",
         "rules": [_resource_rule("weeklyRest", lo=999)]},
        {"id": "law", "name": "норма", "status": "approved", "country": "XA",
         "scope": "regulatory",
         "rules": [_resource_rule("monthlyRest", lo=100)]},
    ])
    from webapp import jobs
    a = [p["id"] for p in jobs._eligible_rule_packs(
        {"id": "upload-a", "country": "XA"})]
    assert a == ["own", "law"], "чужой пакет набора не имеет права попасть"
    b = [p["id"] for p in jobs._eligible_rule_packs(
        {"id": "upload-b", "country": "XA"})]
    assert b == ["alien", "law"]
    # и конфликта 409 между арендаторами больше не возникает
    monkeypatch.setattr(jobs, "RESOURCE_RULES_DIR", tmp_path / "rr")
    path, fp = jobs._resource_rules_file(
        {"id": "upload-a", "label": "a", "country": "XA"}, "run-a")
    assert path.exists() and fp
    assert rp.pack_applies_to({"scope": "instance:upload-a"},
                              {"id": "upload-a"}) is True


def test_legacy_pack_without_scope_stays_regulatory(tmp_path, monkeypatch):
    """Пакеты прежнего выпуска поля области не имеют — обесценивать их
    правка не должна."""
    rp = _write_packs(tmp_path, monkeypatch, [
        {"id": "old", "status": "approved", "country": None,
         "rules": [_resource_rule("weeklyRest")]}])
    assert rp.pack_applies_to({"country": None}, {"id": "any"}) is True
    from webapp import jobs
    assert [p["id"] for p in jobs._eligible_rule_packs(
        {"id": "any", "country": "XB"})] == ["old"]


def test_scope_value_is_validated_loudly(tmp_path, monkeypatch):
    from webapp import rule_packs as rp
    assert rp.normalize_scope(None) == rp.SCOPE_REGULATORY
    assert rp.normalize_scope("instance:upload-x") == "instance:upload-x"
    for bad in ("tenant:x", "instance:", 5, "  ", ""):
        with pytest.raises(rp.RulePackError):
            rp.normalize_scope(bad)
    # Пустая строка и ОТСУТСТВИЕ значения — разные вещи: первое приходит
    # из явного запроса «установи область в ничто» и должно падать,
    # второе означает «поле не задано» у пакета прежнего выпуска.
    assert rp.normalize_scope(None) == rp.SCOPE_REGULATORY
