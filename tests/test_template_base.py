# -*- coding: utf-8 -*-
"""Инструмент базы шаблонов (P-N-SHD 6а): карта семей, зависимые
правила текущего ВМП, блок каталога и привязок, ссылки в службу
каталога и сводка заявок (шаг 6 каталога, решение 0.8).

Фикстуры синтетические. Дисциплина изменилась шагом 6: правка и снятие
записи идут НЕ заявкой очереди, а в СЛУЖБЕ каталога — заявка каталога у
существующей записи бывает только на вид исполнителя (правило R02
читателя), поэтому экран ведёт на карточку записи, а поимённый перечень
зависимых правил действующего ВМП остаётся у инструмента."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _stand_identity import ADMIN_EMAIL

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp.template_registry import (  # noqa: E402
    PARAM_TO_FAMILY, RULE_MECHANIC_TYPES, families_map)
from webapp import template_base as tb  # noqa: E402

#: Адрес службы каталога для стендов — вымышленный (зона `.invalid`
#: закреплена RFC 6761 и не разрешается никогда). Стенд судит
#: ФОРМУЛУ ссылки: адрес задаёт сам тест и из него же выводит
#: ожидание. Живой порт службы здесь означал бы зависимость от
#: того, что запущено на машине.
SERVICE_URL = "http://catalog.invalid"


def test_карта_семей_полна_и_несёт_контракты():
    """Карта полна, и у каждой семьи есть исполнители. С шага 4 строки
    исполнителей приходят из привязок продукта и имеют форму
    «вид · символ · файл» — пустая строка исполнителей у семьи значила
    бы, что привязки до обёртки не дошли."""
    doc = families_map()
    fams = doc["rule_families"]
    assert set(fams) == set(RULE_MECHANIC_TYPES)
    for name, spec in fams.items():
        assert spec["executors"], f"семья {name} без исполнителей"
        for line in spec["executors"]:
            parts = line.split(" · ")
            assert len(parts) == 3, f"{name}: строка исполнителя не по " \
                                    f"форме «вид · символ · файл»: {line!r}"
            assert all(p.strip() for p in parts), f"{name}: {line!r}"
        assert spec["class"] in ("core", "extension")
        assert isinstance(spec["mapped_by"]["params"], list)
    # каждый параметр закрытого словаря приписан существующей семье
    for pname, fam in PARAM_TO_FAMILY.items():
        assert fam in fams, f"параметр {pname} ведёт в несуществующую " \
                            f"семью {fam}"
        assert pname in fams[fam]["mapped_by"]["params"]
    # критерии: перечень с признаком исполнителя
    assert doc["criteria"], "критерии не перечислены"
    for kind, c in doc["criteria"].items():
        assert "has_executor" in c, kind


_MODEL = {"rules": [
    {"id": "R-1", "kind": "SOFT", "title": "ночёвки",
     "export_solver": {"family": "layover_penalty"}, "weight": 10},
    {"id": "R-2", "kind": "HARD", "title": "предел смены",
     "params": [{"name": "max_duty_duration", "value": 720}]},
    {"id": "R-3", "kind": "DEF", "title": "шкала",
     "params": [{"name": "base_tz_offset_min", "value": 0}]},
    {"id": "R-4", "kind": "DATA", "title": "данные",
     "params": [{"name": "max_duty_duration", "value": 9}]},
]}


def test_зависимые_правила_по_машинным_признакам():
    links = tb.rule_family_links(_MODEL)
    assert [d["rule_id"] for d in links["layover_penalty"]] == ["R-1"]
    assert [d["rule_id"] for d in links["duty_limits"]] == ["R-2"]
    assert [d["rule_id"] for d in links["time_scale"]] == ["R-3"]
    # DATA-правило не опирается на исполнителей
    assert not any(d["rule_id"] == "R-4"
                   for deps in links.values() for d in deps)
    assert tb.rule_family_links(None) == {}


@pytest.fixture()
def cr_queue(monkeypatch, tmp_path):
    """Очередь заявок — в tmp; текущий ВМП — синтетическая модель."""
    from webapp import change_requests as cr
    monkeypatch.setattr(cr, "CR_PATH", tmp_path / "cr.json")
    monkeypatch.setattr(tb, "_current_model", lambda: _MODEL)
    return cr


def test_правка_и_снятие_ведут_в_службу_каталога(monkeypatch):
    """Шаг 6, решение 0.8: заявки очереди больше нет — инструмент даёт
    слова и ссылку на карточку записи в службе; без адреса службы ссылки
    нет, и слова об этом честны, а не ведут в никуда."""
    monkeypatch.delenv("CREW_CATALOG_URL", raising=False)
    off = tb.change_refusal("families", "layover_penalty")
    assert off["link"] is None
    assert "службы каталога" in off["error"]
    assert "вид исполнителя" in off["error"]
    monkeypatch.setenv("CREW_CATALOG_URL", SERVICE_URL + "/")
    on = tb.change_refusal("families", "layover_penalty")
    assert on["link"] == (
        SERVICE_URL + "/ui/#/records/families/layover_penalty")
    assert tb.record_link("families", None) is None


def test_прежняя_ручка_заявки_отвечает_409_со_ссылкой(monkeypatch,
                                                      tmp_path):
    """Совместимость: ручка осталась, но заявку очереди не заводит."""
    import importlib

    from fastapi.testclient import TestClient
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    monkeypatch.setenv("CREW_CATALOG_URL", SERVICE_URL)
    from webapp import change_requests as cr
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(cr, "CR_PATH", tmp_path / "cr.json")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        r = c.post("/api/template-base/change",
                   json={"family": "layover_penalty", "action": "edit",
                         "reason": "уточнить"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["link"].endswith("/records/families/layover_penalty")
    assert cr.list_crs() == []                # очередь не тронута



def test_экран_несёт_блок_catalog(monkeypatch):
    """Шаг 2 каталога шаблонов (часть 13.6): экран базы шаблонов несёт
    блок catalog — версия и отпечаток текущего каталога, путь, число
    предупреждений проверки (ошибок у годного каталога нет) — прозрачность
    снимка; зависимые правила и блок пакета в той же форме. Состав
    блока catalog шаг 4 не меняет: привязки едут отдельным блоком."""
    import template_catalog as tc
    from vmp import catalog_access as ca
    from webapp import task_channel
    monkeypatch.setattr(tb, "_current_model", lambda: _MODEL)
    monkeypatch.setattr(task_channel, "current_package", lambda: None)
    ca.forget()
    doc = tb.overview()
    assert doc["package"] == {"present": False}
    assert doc["rule_families"]["layover_penalty"]["dependents"][0]["rule_id"] \
        == "R-1"
    cat = doc["catalog"]
    assert set(cat) == {"version", "fingerprint", "path", "warnings"}
    loaded = tc.load(HERE / "catalog")
    assert cat["version"] == loaded.version()
    assert cat["fingerprint"] == loaded.fingerprint()
    assert Path(cat["path"]).resolve() == (HERE / "catalog").resolve()
    problems = loaded.validate()
    assert not [p for p in problems if p.severity == "error"]
    assert isinstance(cat["warnings"], int) and cat["warnings"] >= 0
    assert cat["warnings"] == sum(1 for p in problems
                                  if p.severity != "error")


def test_экран_несёт_привязки_и_стенд(monkeypatch):
    """Шаг 4: экран несёт сводку привязок над таблицами и у каждой
    записи — состояния по видам и итог стенда. Числа сводки сверяются с
    самим файлом привязок (второй путь, независимый от двери), состояния
    — с записями каталога: у каждого ОБЪЯВЛЕННОГО вида записи «есть»
    обязана быть клетка, иначе экран показывал бы меньше, чем судит
    гейт."""
    import json

    from vmp import catalog_access as ca
    from webapp import task_channel
    monkeypatch.setattr(tb, "_current_model", lambda: _MODEL)
    monkeypatch.setattr(task_channel, "current_package", lambda: None)
    ca.forget()
    doc = tb.overview()
    sm = doc["bindings"]
    assert set(sm) == {"file", "product_version", "generated_from",
                       "generated_at", "catalog_version",
                       "catalog_fingerprint", "fingerprint", "state",
                       "cells", "present_rows", "absent_rows",
                       "declared_rows", "stand", "cdpm", "cdpm_note"}
    assert sm["state"] == "match", sm
    # Слово о чужих привязках — из закрытого перечня двери: файл может
    # лежать и не читаться, и тогда «объявлены» было бы неправдой.
    assert sm["cdpm"] in (ca.CDPM_YES, ca.CDPM_NO, ca.CDPM_BAD)
    assert (sm["cdpm_note"] is None) == (sm["cdpm"] != ca.CDPM_BAD), \
        "нечитаемость чужого файла обязана нести причину словами"
    # Задачи и строки — разные числа: у задачи строк столько, сколько
    # видов она судит, и одно число под двумя именами читалось бы ложью.
    assert set(sm["stand"]) == {"tasks", "rows", "passed", "failed",
                                "skipped"}
    raw = json.loads(Path(sm["file"]).read_text(encoding="utf-8"))
    rows = [ex for rec in raw["bindings"] for ex in rec["executors"]]
    assert sm["present_rows"] == sum(1 for ex in rows if ex["state"] == "есть")
    assert sm["absent_rows"] == sum(1 for ex in rows if ex["state"] == "нет")
    assert sm["catalog_version"] == doc["catalog"]["version"]
    assert sm["catalog_fingerprint"] == doc["catalog"]["fingerprint"]

    from vmp import catalog_access as _ca
    cat = _ca.current()
    declared = {("families", r["id"]): list(r.get("executor_kinds") or [])
                for r in cat.families_full()}
    declared.update({("criteria", cid): list(rec.get("computable_by") or [])
                     for cid, rec in cat.criteria().items()})
    # Экран ПОКАЗЫВАЕТ то, что сказал файл, и не судит его: зелёный ли
    # стенд — предмет гейта стенда (tests/vmp/test_stand_tasks.py), а
    # здесь доказывается верность показа, поимённо по записям.
    from webapp.template_base import stand_counts
    expected_stand = stand_counts(raw)
    for section, key in (("families", "rule_families"),
                         ("criteria", "criteria")):
        for rid, spec in doc[key].items():
            assert set(spec["stand"]) == {"passed", "failed", "skipped"}, rid
            assert spec["stand"] == expected_stand.get(
                (section, rid), tb.EMPTY_STAND), rid
            states = spec["executor_states"]
            assert set(states) == set(declared[(section, rid)]), rid
            for kind, by_product in states.items():
                assert by_product, (rid, kind)
                assert "COSMOS" in by_product, (rid, kind)
    assert sum(v["passed"] + v["failed"] + v["skipped"]
               for k in ("rule_families", "criteria")
               for v in (s["stand"] for s in doc[k].values())) \
        == sm["stand"]["passed"] + sm["stand"]["failed"] \
        + sm["stand"]["skipped"] == sm["stand"]["rows"]
    # «Задач» — число РАЗЛИЧНЫХ задач записей, не строк: у задачи строк
    # столько, сколько видов она судит.
    assert sm["stand"]["tasks"] == len(
        {(rec["section"], rec["id"], sv["task"])
         for rec in raw["bindings"] for sv in rec["stand_verified"]})
    assert sm["stand"]["tasks"] <= sm["stand"]["rows"]


def test_клетка_заявлен_несёт_карточку_заявки(monkeypatch, tmp_path):
    """Шаг 6 после ревью волны 2: у клетки «заявлен» экран знает ЗАЯВКУ.

    Руководства 13.10 и 03 §12п обещают: чип «заявлен» ведёт на карточку
    заявки. Клетка привязок номера заявки не несла, и ссылка вела в
    отфильтрованный список. Теперь заявку называет дверь
    (`bindings_requests`), а адрес карточки строит сервер той же
    формулой, что у вида «Каталог» (`catalog_pipeline.request_link`)."""
    import json

    import tests.catalog_copies as cc
    from template_catalog import requests as law
    from vmp import catalog_access as ca
    from webapp import task_channel
    monkeypatch.setattr(tb, "_current_model", lambda: _MODEL)
    monkeypatch.setattr(task_channel, "current_package", lambda: None)
    monkeypatch.setenv("CREW_CATALOG_URL", SERVICE_URL + "/")
    key = law.kind_request_key("families", "duty_limits",
                               ["kotlin_constraint"])
    rid = law.request_id_of(key)
    folder = cc.copy_catalog(tmp_path)
    (folder / "requests.json").write_text(json.dumps(
        {"form_version": 1, "requests": [
            {"request_id": rid, "state": "исполнитель заявлен",
             "section": "families", "record_id": "duty_limits",
             "submitted_at": "2026-09-12T10:15:00+03:00",
             "submitted_by": "COSMOS",
             "source": {"kind": "пробел при нажатии",
                        "document_class": None,
                        "run_class": "прогон COSMOS",
                        "ref": "run-0031#" + key[:12]},
             "draft": None,
             "executor_kinds_requested": ["kotlin_constraint"],
             "decision": {"by": "владелец",
                          "at": "2026-09-12T10:00:00+03:00",
                          "words": "нужна механика"},
             "cr_key": law.cr_key_of(rid)}]},
        ensure_ascii=False), encoding="utf-8")
    cc.use_catalog(monkeypatch, folder)
    ca.forget()
    try:
        # Дверь: заявка названа по клетке «запись × вид».
        asked = ca.bindings_requests(ca.current())
        row = asked["families"]["duty_limits"]["kotlin_constraint"]
        assert row["request_id"] == rid and row["cr_key"] == law.cr_key_of(rid)
        assert ca.bindings_states(ca.current())["families"]["duty_limits"][
            "kotlin_constraint"]["COSMOS"] == "заявлен"
        # Экран: карточка заявки, а не перечень.
        doc = tb.overview()
        cell = doc["rule_families"]["duty_limits"]["executor_requests"]
        assert cell["kotlin_constraint"]["link"] == \
            SERVICE_URL + "/ui/#/requests/" + rid
        assert set(cell["kotlin_constraint"]) == {
            "request_id", "state", "submitted_by", "cr_key", "link"}
        # Клетки без заявки в блоке не значатся — экран не выдумывает.
        assert "check_code" not in cell
    finally:
        ca.forget()


def test_счёт_стенда_по_строкам_привязок():
    """Чистая функция счёта: исходы закрытого перечня складываются по
    записям, чужое слово в счёт не идёт, запись без строк стенда даёт
    нули (на экране — «не прогонялся»)."""
    doc = {"bindings": [
        {"section": "families", "id": "duty_limits", "stand_verified": [
            {"task": "st-duty-1", "result": "passed"},
            {"task": "st-duty-2", "result": "skipped"},
            {"task": "st-duty-3", "result": "passed"},
            {"task": "st-duty-4", "result": "не_исход"}]},
        {"section": "criteria", "id": "min_person_days",
         "stand_verified": []}]}
    got = tb.stand_counts(doc)
    assert got[("families", "duty_limits")] == {"passed": 2, "failed": 0,
                                                "skipped": 1}
    assert got[("criteria", "min_person_days")] == tb.EMPTY_STAND
    assert tb.stand_counts(None) == {}
    assert tb.EMPTY_STAND == {"passed": 0, "failed": 0, "skipped": 0}


def test_зависимые_правила_остаются_на_экране(monkeypatch):
    """Перечень зависимых правил действующего ВМП службе каталога
    неизвестен и остаётся у инструмента — иначе снятие записи стало бы
    решением вслепую."""
    monkeypatch.setattr(tb, "_current_model", lambda: _MODEL)
    links = tb.rule_family_links(tb._current_model())
    assert [d["rule_id"] for d in links["layover_penalty"]] == ["R-1"]
    print(f"семей с зависимыми правилами: {len(links)}")
