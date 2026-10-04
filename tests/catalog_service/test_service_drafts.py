# -*- coding: utf-8 -*-
"""T4: черновики — форма файла, суд кандидата, права, отмена.

Главное правило слоя: черновик живёт ВНЕ пакета, и его сохранение не
меняет ни отпечатка каталога, ни экземпляра читателя. Отказ — только по
НОВЫМ проблемам: то, что каталог уже несёт (например, предупреждение о
записи без стендовых задач), автору правки не вменяется.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import template_catalog as tc
from tests.catalog_service.conftest import MARKER, detail, sample_family

DRAFT_SCHEMA = (Path(__file__).resolve().parents[2] / "catalog_service"
                / "schemas" / "draft.schema.json")


def _valid_draft_file(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(DRAFT_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(doc)
    return doc


def test_saving_a_draft_does_not_touch_the_catalog(service):
    instance = tc.current(service.folder)
    before = instance.fingerprint()
    rec = sample_family(instance)
    rec = dict(rec, title=rec["title"] + " (правка службой)")
    rec.pop("history", None)
    r = service.put(f"/api/drafts/families/{rec['id']}",
                    json={"record": rec, "note": "проба правки заголовка"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] is True
    assert [c["kind"] for c in body["changes"]] == ["изменена"]
    assert body["changes"][0]["level"] == "patch"
    path = service.settings.drafts_dir / "families" / f"{rec['id']}.json"
    doc = _valid_draft_file(path)
    assert doc["section"] == "families" and doc["by"] == "владелец"
    assert doc["note"] and doc["request_id"] is None
    assert "history" not in doc["record"]
    again = tc.current(service.folder)
    assert again is instance, "правка черновика дала новый экземпляр читателя"
    assert again.fingerprint() == before
    print("файлов черновиков записано: 1; отпечаток каталога не изменился")


def test_draft_with_new_error_is_refused_and_no_file_appears(service):
    cat = service.catalog()
    rec = sample_family(cat)
    rec = dict(rec, zone="НЕБО")
    rec.pop("history", None)
    r = service.put(f"/api/drafts/families/{rec['id']}",
                    json={"record": rec, "note": "зона вне словаря"})
    assert r.status_code == 422
    words = detail(r)
    assert words["code"] == "invalid-draft"
    codes = {p["code"] for p in words["problems"]}
    assert "D01" in codes
    assert any(p["where"].startswith(f"families/{rec['id']}")
               for p in words["problems"])
    path = service.settings.drafts_dir / "families" / f"{rec['id']}.json"
    assert not path.exists(), "отказ обязан не оставлять файла"


def test_removing_a_complement_is_refused_at_the_other_family(service):
    """Пара «дополняют друг друга» — решение о ДВУХ записях. Служба не
    решает за вторую: односторонний убор даёт X01 по её адресу."""
    cat = service.catalog()
    rec = next(r for r in cat.families_full() if r.get("complements"))
    other = str(rec["complements"][0])
    draft = dict(rec, complements=[c for c in rec["complements"]
                                   if c != other])
    draft.pop("history", None)
    r = service.put(f"/api/drafts/families/{rec['id']}",
                    json={"record": draft, "note": "убрать дополнение"})
    assert r.status_code == 422
    problems = detail(r)["problems"]
    assert any(p["code"] == "X01" and p["where"].startswith(f"families/{other}")
               for p in problems), problems
    print(f"перекрёстный адрес отказа: families/{other} "
          f"(правка была в families/{rec['id']})")


def test_added_complement_is_written_to_the_other_side(service):
    cat = service.catalog()
    ids = [str(x) for x in cat.families()]
    a = next(r for r in cat.families_full() if not r.get("complements"))
    b = next(i for i in ids if i != a["id"]
             and not cat.family(i).get("complements"))
    draft = dict(a, complements=[b])
    draft.pop("history", None)
    r = service.put(f"/api/drafts/families/{a['id']}",
                    json={"record": draft, "note": "новое дополнение"})
    assert r.status_code == 200, r.text
    assert any(f"families/{b} += {a['id']}" in d for d in r.json()["derived"])


def test_customer_marker_is_refused_and_absence_is_a_warning(make_service,
                                                             tmp_path):
    svc = make_service("owner")
    cat = svc.catalog()
    rec = sample_family(cat)
    draft = dict(rec, note=f"пример из {MARKER}")
    draft.pop("history", None)
    r = svc.put(f"/api/drafts/families/{rec['id']}",
                json={"record": draft, "note": "маркер заказчика в заметке"})
    assert r.status_code == 422
    assert {p["code"] for p in detail(r)["problems"]} & {"A02"}

    bare = make_service("owner", markers=False, name="без-маркеров",
                        work="work-2")
    rec2 = sample_family(bare.catalog())
    ok = dict(rec2, title=rec2["title"] + " ещё")
    ok.pop("history", None)
    got = bare.put(f"/api/drafts/families/{rec2['id']}",
                   json={"record": ok, "note": "перечня маркеров нет"})
    assert got.status_code == 200
    assert any(p["code"] == "markers-missing"
               for p in got.json()["problems"]), got.json()["problems"]
    print("случаев маркеров проверено: 2 (перечень есть, перечня нет)")


def test_status_change_is_owner_only(make_service):
    analyst = make_service("analyst")
    cat = analyst.catalog()
    rec = sample_family(cat)
    plain = dict(rec, title=rec["title"] + " от аналитика")
    plain.pop("history", None)
    assert analyst.put(f"/api/drafts/families/{rec['id']}",
                       json={"record": plain,
                             "note": "правка аналитиком"}).status_code == 200
    withdraw = dict(rec, status="снята")
    withdraw.pop("history", None)
    r = analyst.put(f"/api/drafts/families/{rec['id']}",
                    json={"record": withdraw, "note": "снять запись"})
    assert r.status_code == 403 and detail(r)["code"] == "owner-only"
    assert "владелец" in detail(r)["words"]


def test_dictionary_draft_rules(service):
    cat = service.catalog()
    key = "violation_units"
    # 0.5.0: единицы нарушения — карта «код → слово»; членство судится
    # по ключам, слово — непустая строка.
    value = dict(cat.dictionaries()[key])
    used = next(r.get("violation_unit") for r in cat.families_full()
                if r.get("violation_unit"))
    r = service.put(f"/api/drafts/dictionaries/{key}",
                    json={"value": {k: v for k, v in value.items() if k != used},
                          "note": "убрать используемую единицу"})
    assert r.status_code == 422
    problems = detail(r)["problems"]
    assert any(p["where"].startswith("families/") and p["code"] == "D01"
               for p in problems), problems
    ok = service.put(f"/api/drafts/dictionaries/{key}",
                     json={"value": dict(value, quarter="квартал"),
                           "note": "новая единица"})
    assert ok.status_code == 200
    assert ok.json()["changes"][0]["level"] == "minor"
    blank = service.put(f"/api/drafts/dictionaries/{key}",
                        json={"value": dict(value, quarter=""),
                              "note": "единица без слова"})
    assert blank.status_code == 422, blank.text
    closed = service.put("/api/drafts/dictionaries/products",
                         json={"value": ["CDPM"], "note": "проба"})
    assert closed.status_code == 422
    assert detail(closed)["code"] == "closed-list"
    assert service.put("/api/drafts/dictionaries/area_param_names",
                       json={"value": [], "note": "проба"}).status_code == 422
    assert service.put("/api/drafts/dictionaries/нетакого",
                       json={"value": [], "note": "проба"}).status_code == 404
    print("правил словарей проверено: 6")


def test_discard_draft_and_discard_all(service):
    cat = service.catalog()
    rec = sample_family(cat)
    draft = dict(rec, title=rec["title"] + " x")
    draft.pop("history", None)
    service.put(f"/api/drafts/families/{rec['id']}",
                json={"record": draft, "note": "проба"})
    service.put("/api/drafts/dictionaries/violation_units",
                json={"value": dict(cat.dictionaries()["violation_units"],
                                    quarter="квартал"), "note": "проба"})
    assert len(service.get("/api/drafts").json()["items"]) == 2
    assert service.delete(f"/api/drafts/families/{rec['id']}"
                          ).json() == {"discarded": True}
    assert service.delete(f"/api/drafts/families/{rec['id']}"
                          ).status_code == 404
    assert service.delete("/api/drafts").json() == {"discarded": 1}
    assert service.get("/api/drafts").json()["items"] == []


def test_view_mode_writes_nothing(make_service):
    svc = make_service(None)
    cat = svc.catalog()
    rec = sample_family(cat)
    draft = dict(rec, title=rec["title"] + " x")
    draft.pop("history", None)
    r = svc.put(f"/api/drafts/families/{rec['id']}",
                json={"record": draft, "note": "проба"})
    assert r.status_code == 403
    words = detail(r)
    assert words["code"] == "read-only" and "--actor owner" in words["words"]
    assert not svc.settings.work_dir.exists(), \
        "витрина не заводит рабочей папки"
    assert svc.get("/api/journal").json() == {
        "items": [], "words": "журнал ведётся только на рабочем месте"}


def test_card_of_a_brand_new_record_opens_from_its_draft(service):
    """Поток «Новая запись» (§4.1): после первого сохранения карточка
    новой записи обязана открываться — в каталоге её ещё нет, но на
    рабочем месте она уже заведена. Иначе отменить такой черновик или
    дойти до него по ссылке списка нечем."""
    cat = service.catalog()
    template = sample_family(cat)
    fresh = dict(template, id="probe_novaya_kartochka",
                 title="Пробная запись службы", params=[], param_sets=[],
                 conditions=[], complements=[], rule_form=None,
                 stand_tasks=[], subject_words=None)
    fresh.pop("history", None)
    saved = service.put("/api/drafts/families/probe_novaya_kartochka",
                        json={"record": fresh, "note": "заведена новая запись"})
    assert saved.status_code == 200, saved.text
    card = service.get("/api/records/families/probe_novaya_kartochka")
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["in_catalog"] is False
    assert body["record"]["id"] == "probe_novaya_kartochka"
    assert body["draft"] and body["draft"]["note"] == "заведена новая запись"
    assert [c["kind"] for c in body["changes"]] == ["добавлена"]
    assert body["executors"] == {} and body["stand"]["rows"] == []
    # Карточку видно и в списке с флажком «только с черновиками», и
    # отменить черновик можно из неё.
    listed = service.get("/api/records?section=families&status=all&drafts=1"
                         ).json()
    assert "probe_novaya_kartochka" in {i["id"] for i in listed["items"]}
    assert service.delete("/api/drafts/families/probe_novaya_kartochka"
                          ).json() == {"discarded": True}
    gone = service.get("/api/records/families/probe_novaya_kartochka")
    assert gone.status_code == 404 and detail(gone)["code"] == "unknown-record"
    # Показ выпуска черновиком не подменяется: в версии этой записи нет.
    from tests.catalog_service.conftest import make_release
    make_release(service.folder, "0.1.0")
    service.put("/api/drafts/families/probe_novaya_kartochka",
                json={"record": fresh, "note": "снова заведена"})
    pinned = service.get("/api/records/families/probe_novaya_kartochka"
                         "?version=0.1.0")
    assert pinned.status_code == 404
    print("карточка нового черновика: открыта, показана в списке, отменена")


def test_drafts_warn_about_the_watchman_before_the_button(service):
    """Предупреждение о стопе сторожа (B01) приходит ДО кнопки: экран
    сборки обязан показать его с флажком «продолжить осознанно», а не
    узнать об остановке двери продукта из отказа 412 после нажатия."""
    path = service.folder / "vmp-bindings.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["catalog_version"] = "0.1.0"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    cat = service.catalog()
    rec = sample_family(cat)
    draft = dict(rec, title=rec["title"] + " (перед сборкой)")
    draft.pop("history", None)
    assert service.put(f"/api/drafts/families/{rec['id']}",
                       json={"record": draft,
                             "note": "правка перед сборкой"}).status_code == 200
    got = service.get("/api/drafts").json()
    assert got["suggested"]["version"]
    stop = got["bindings_stop"]
    assert stop and stop["code"] == "bindings-stop"
    assert "B01" in stop["words"] and stop["extra"]["product"] == "COSMOS"
    # Отказ самой сборки говорит те же слова — закон один.
    refused = service.post("/api/versions/assemble", json={"note": "проба"})
    assert refused.status_code == 412
    assert detail(refused)["words"] == stop["words"]
    print("предупреждение о стопе сторожа: до кнопки и в отказе — одни слова")
