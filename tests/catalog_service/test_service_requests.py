# -*- coding: utf-8 -*-
"""T6: заявки — подача, решения, ключ CR, перевод в записи.

Главное правило слоя (critical 1 журнала критики): состояние «есть» без
записи в разделе — негодный каталог для обоих продуктов (R02). Поэтому
`adopt` пишет ТОЛЬКО черновик, а «есть» и сама запись появляются одним
вызовом писателя при сборке; между двумя действиями живой каталог
остаётся годным — это здесь и проверяется.
"""
from __future__ import annotations

import json

import pytest

from template_catalog import requests as law
from template_catalog import writer
from tests.catalog_service.conftest import ROOT, detail, sample_family


def _manual_id(section: str, record_id: str, draft) -> str:
    """Номер заявки по закону читателя — ожидание считается тем же
    законом, что и служба, а не литералом: литерал шага 5 (`req-0001`)
    поправкой П1 шага 6 заменён содержательным номером."""
    return law.request_id_of(
        law.manual_key(section, record_id, law.draft_canon(draft)))


def _kind_id(section: str, record_id: str, kinds) -> str:
    return law.request_id_of(law.kind_request_key(section, record_id, kinds))


def _draft_record(cat, fid: str) -> dict:
    """Черновик заявки: запись БЕЗ status, history и stand_tasks (R03)."""
    rec = sample_family(cat)
    draft = {k: v for k, v in rec.items()
             if k not in ("status", "history", "stand_tasks")}
    draft.update({"id": fid, "title": "Пробная семья заявки",
                  "params": [], "param_sets": [], "conditions": [],
                  "complements": [], "subject_words": None,
                  "rule_form": None})
    return draft


def _submit(svc, fid="probe_zayavka", **kw):
    cat = svc.catalog()
    fid = kw.pop("record_id", fid)
    body = {"section": "families", "record_id": fid,
            "source": {"kind": "вручную", "document_class": None,
                       "run_class": None, "ref": None},
            "draft": _draft_record(cat, fid)}
    body.update(kw)
    return svc.post("/api/requests", json=body)


def test_submit_numbers_the_request_by_the_law_and_keeps_the_fingerprint(
        service):
    """Номер — содержательный (поправка П1 шага 6), а не счётчик."""
    before = service.catalog().fingerprint()
    cat0 = service.catalog()
    want = _manual_id("families", "probe_zayavka",
                      _draft_record(cat0, "probe_zayavka"))
    r = _submit(service)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["request_id"] == want and body["state"] == "подана"
    assert law.REQUEST_ID_RE.match(want), "номер не по образцу req-<20 цифр>"
    cat = service.catalog()
    assert cat.fingerprint() == before, "заявки живут вне отпечатка"
    one = cat.requests()[0]
    assert one["submitted_by"] == "владелец"
    assert one["decision"] == {"by": None, "at": None, "words": None}
    assert set(one) == {"request_id", "state", "section", "record_id",
                        "submitted_at", "submitted_by", "source", "draft",
                        "executor_kinds_requested", "decision", "cr_key"}
    assert not [p for p in cat.validate() if p.severity == "error"]
    second = _submit(service, record_id="probe_vtoraya")
    assert second.json()["request_id"] != want
    again = _submit(service)
    assert again.status_code == 200, "повтор той же заявки — не новая заявка"
    assert again.json()["note"] == "уже подана"
    assert len(service.catalog().requests()) == 2
    print(f"заявок подано: 2 (повтор не завёл третьей); номера по закону: "
          f"{want}")


def test_product_may_not_submit_from_the_screen(service):
    r = _submit(service, submitted_by="CDPM")
    assert r.status_code == 400
    words = detail(r)
    assert words["code"] == "submitter-not-allowed"
    assert "только продукт" in words["words"]


def test_duplicate_request_names_the_existing_one(service):
    """Иная заявка (иной номер) на ту же запись — отказ с именем прежней.

    Повтор ТОЙ ЖЕ заявки даёт тот же номер и «уже подана»; здесь
    проверяется другой случай — второй черновик той же записи.

    Код отказа с волны 17б — `request-exists` (П0 §4.1, редакция
    «0-бис»): прежде у этого случая был свой код `duplicate-request`, но
    «открытая заявка по записи» и «тот же номер иным каноном» — одно и
    то же столкновение СОДЕРЖАНИЯ, и клиенту важно одно: заявка по этой
    записи уже есть, и она не его. Два кода на один случай заставляли
    клиента CDPM держать два перечня (`RETRY_REFUSALS`/`EXISTS_REFUSALS`)
    там, где закон один."""
    first = _submit(service)
    assert first.status_code == 201
    cat = service.catalog()
    other = _draft_record(cat, "probe_zayavka")
    other["title"] = "Другой черновик той же записи"
    r = _submit(service, draft=other)
    assert r.status_code == 409, r.text
    words = detail(r)
    assert words["code"] == "request-exists"
    assert words["extra"]["existing"] == first.json()["request_id"]
    assert words["extra"]["state"] == "подана"
    assert "подана" in words["words"]
    print(f"открытых заявок на запись: 1 ({words['extra']['existing']})")


def test_decision_requires_words_and_the_owner(make_service):
    svc = make_service("owner")
    rid = _submit(svc).json()["request_id"]
    bad = svc.post(f"/api/requests/{rid}/decide",
                   json={"decision": "принята", "words": "  "})
    assert bad.status_code == 422 and detail(bad)["code"] == "words-required"
    ok = svc.post(f"/api/requests/{rid}/decide",
                  json={"decision": "принята", "words": "нужна семья"})
    assert ok.status_code == 200
    assert ok.json()["state"] == "принята"
    assert ok.json()["decision"]["by"] == "владелец"

    analyst = make_service("analyst", name="каталог-2", work="work-2")
    rid2 = _submit(analyst).json()["request_id"]
    r = analyst.post(f"/api/requests/{rid2}/decide",
                     json={"decision": "принята", "words": "проба"})
    assert r.status_code == 403 and detail(r)["code"] == "owner-only"
    print("решений проверено: 3 (без слов, принята, чужой ролью)")


def test_owner_may_reject_an_accepted_request_and_revise_the_refusal(
        make_service):
    """Решение 0.15: тупиков у машины состояний нет.

    «принята» → «отклонена» словами владельца и «отклонена» → «принята»
    (пересмотр) — единственный выход из тупика для того же пробела: номер
    содержательный, и другого номера у этого пробела не будет."""
    svc = make_service("owner")
    rid = _submit(svc).json()["request_id"]
    svc.post(f"/api/requests/{rid}/decide",
             json={"decision": "принята", "words": "нужна семья"})
    back = svc.post(f"/api/requests/{rid}/decide",
                    json={"decision": "отклонена",
                          "words": "механика уже покрыта соседней записью"})
    assert back.status_code == 200, back.text
    assert back.json()["state"] == "отклонена"
    again = svc.post(f"/api/requests/{rid}/decide",
                     json={"decision": "принята",
                           "words": "пересмотр: соседняя запись о другом"})
    assert again.status_code == 200, again.text
    assert again.json()["state"] == "принята"
    assert "пересмотр" in again.json()["decision"]["words"]
    # Из «есть» решать нечего: там уже запись каталога.
    cat = svc.catalog()
    rows = cat.requests()
    rows[0]["state"] = "есть"
    writer.write_requests(svc.folder, rows)
    svc.store.forget()
    stuck = svc.post(f"/api/requests/{rid}/decide",
                     json={"decision": "отклонена", "words": "поздно"})
    assert stuck.status_code == 409
    assert detail(stuck)["code"] == "bad-transition"
    print("переходов решения пройдено: принята → отклонена → принята; "
          "из «есть» — отказ")


def test_words_of_the_decision_are_judged_by_the_a_rules(make_service,
                                                         tmp_path):
    """Слова решения владельца уходят в строку CR и в задание агенту —
    значит, они судятся A-правилами и маркерами, как всякая проза."""
    from tests.catalog_service.conftest import MARKER
    svc = make_service("owner")
    rid = _submit(svc).json()["request_id"]
    bad = svc.post(f"/api/requests/{rid}/decide",
                   json={"decision": "принята",
                         "words": f"принимаю по просьбе {MARKER}"})
    assert bad.status_code == 422, bad.text
    words = detail(bad)
    assert words["code"] == "invalid-request"
    assert any("decision.words" in p["where"] for p in words["problems"]), \
        words["problems"]
    # Слова отказа — адресные: чинить нужно фразу владельца, а не форму
    # заявки, и служба говорит именно это (§5.1 проекта шага 6).
    assert "слова решения содержат данные заказчика" in words["words"]
    assert "перепишите словами о механике" in words["words"]
    assert svc.catalog().requests()[0]["state"] == "подана", \
        "отказ не меняет состояния заявки"
    print(f"проблем по адресу решения: {len(words['problems'])}; "
          f"слова отказа адресные")


def test_cr_key_is_the_law_key_and_nothing_else(service):
    """Ключ CR — проекция заявки: `cr_key_of(request_id)` и только он."""
    rid = _submit(service).json()["request_id"]
    want = law.cr_key_of(rid)
    early = service.put(f"/api/requests/{rid}/cr_key", json={"cr_key": want})
    assert early.status_code == 409          # заявка ещё не принята
    service.post(f"/api/requests/{rid}/decide",
                 json={"decision": "принята", "words": "нужна"})
    bad = service.put(f"/api/requests/{rid}/cr_key",
                      json={"cr_key": "CR-1"})
    assert bad.status_code == 422 and detail(bad)["code"] == "bad-cr-key"
    other = service.put(f"/api/requests/{rid}/cr_key",
                        json={"cr_key": "cr1a2b3c4d"})
    assert other.status_code == 422, "чужой ключ по образцу — тоже отказ"
    assert detail(other)["extra"]["expected"] == want
    ok = service.put(f"/api/requests/{rid}/cr_key", json={"cr_key": want})
    assert ok.status_code == 200
    assert ok.json()["state"] == "исполнитель заявлен"
    assert service.catalog().requests()[0]["cr_key"] == want
    twice = service.put(f"/api/requests/{rid}/cr_key", json={"cr_key": want})
    assert twice.status_code == 200, "повтор сверки — тот же ключ, не отказ"
    card = service.get(f"/api/requests/{rid}").json()
    assert card["cr_key_expected"] == want
    print(f"ключ CR по закону: {want} (проб отказа 2, повтор 1)")


def test_adopt_writes_only_a_draft_and_keeps_the_catalog_sound(service):
    """Перевод пишет ЧЕРНОВИК и только его; `requests.json` не тронут.

    Перевод идёт полем `confirm` с волны 17б (K06, П0 §3.5): у НОВОЙ
    записи собственных клеток привязок нет по построению (B02: привязки
    не знают записи, которой ещё нет), и служба честно отказывает
    `executor-not-present`, пока исполнителя не написали. Осознанный
    обход владельцем сохранён — его и проверяет этот стенд, потому что
    судит он не предпроверку исполнителей (её судит соседний стенд), а
    то, ЧТО перевод записывает на диск. Прежде вызов проходил пустым
    телом: `adopt` смотрел на ГЛОБАЛЬНУЮ таблицу видов, и вид,
    написанный у чужой семьи, разрешал перевод этой."""
    from vmp import catalog_access as ca
    rid = _submit(service).json()["request_id"]
    service.post(f"/api/requests/{rid}/decide",
                 json={"decision": "принята", "words": "нужна семья"})
    before = (service.folder / "requests.json").read_bytes()
    r = service.post(f"/api/requests/{rid}/adopt",
                     json={"confirm": "исполнителя напишем следом"})
    assert r.status_code == 200, r.text
    assert r.json()["draft_written"] is True
    assert "соберите черновик" in r.json()["next"]
    assert (service.folder / "requests.json").read_bytes() == before
    draft = json.loads((service.settings.drafts_dir / "families"
                        / "probe_zayavka.json").read_text(encoding="utf-8"))
    assert draft["request_id"] == rid
    assert draft["record"]["status"] == "есть"
    assert draft["record"]["stand_tasks"] == []
    assert draft["record"]["origin"]["kind"] == "заявка"
    assert draft["record"]["origin"]["confirmed_by"] == "владелец"
    cat = service.catalog()
    assert not [p for p in cat.validate() if p.severity == "error"], \
        "между adopt и сборкой каталог обязан быть годным"
    ca.forget()
    try:
        # Дверь продукта смотрит в ЖИВОЙ каталог репозитория, а не в
        # копию службы: adopt её не трогает и не роняет. Прежнее
        # сравнение с версией копии было совпадением — оно держалось,
        # пока живой каталог и копия были одним и тем же черновиком.
        import template_catalog as tc
        assert ca.current().version() == tc.load(ROOT / 'catalog').version()
    finally:
        ca.forget()
    print("после adopt: файлов каталога изменено 0, черновиков 1")


def test_assemble_moves_the_request_to_present_with_three_history_lines(
        make_service, tmp_path):
    """Заявка от продукта: `history[0].by` — слово словаря `actors`
    («сессия CDPM»), даты не убывают, состояние «есть» и запись
    появляются одной записью писателя."""
    from tests.catalog_copies import copy_catalog
    folder = copy_catalog(tmp_path)
    svc = make_service("owner", folder=folder)
    cat = svc.catalog()
    request = {
        "request_id": "req-0007", "state": "принята", "section": "families",
        "record_id": "probe_ot_cdpm",
        "submitted_at": "2026-09-01T09:00:00+03:00", "submitted_by": "CDPM",
        "source": {"kind": "посадка категории C",
                   "document_class": "положение о режиме труда",
                   "run_class": "прогон CDPM", "ref": None},
        "draft": _draft_record(cat, "probe_ot_cdpm"),
        "executor_kinds_requested": None,
        "decision": {"by": "владелец", "at": "2026-09-02T10:00:00+03:00",
                     "words": "принимаю: механика общая"},
        "cr_key": None}
    writer.write_requests(folder, [request])
    svc.store.forget()
    assert svc.post("/api/requests/req-0007/adopt",
                    json={"confirm": "исполнителя объявим позже"}
                    ).status_code == 200
    got = svc.post("/api/versions/assemble",
                   json={"note": "принятая заявка стала записью"})
    assert got.status_code == 200, got.text
    after = svc.catalog()
    rec = after.family("probe_ot_cdpm")
    assert rec["status"] == "есть"
    assert [h["kind"] for h in rec["history"]] == [
        "заявка", "решение владельца", "создание"]
    assert rec["history"][0]["by"] == "сессия CDPM"
    assert rec["history"][0]["ref"] == "req-0007"
    assert rec["history"][1]["note"] == "принимаю: механика общая"
    assert "исполнителя объявим позже" in rec["history"][2]["note"]
    dates = [h["date"] for h in rec["history"]]
    assert dates == sorted(dates), "даты истории убывают (X08)"
    assert after.requests()[0]["state"] == "есть"
    assert not [p for p in after.validate() if p.severity == "error"]
    print("строк истории записи из заявки: 3")


def test_adopt_refuses_when_no_product_declares_the_executor(service):
    from catalog_service import executors
    cat = service.catalog()
    have = executors.present_kinds(cat, service.settings)
    absent = [k for k in cat.dictionaries()["executor_kinds"] if k not in have]
    if not absent:                                       # pragma: no cover
        pytest.skip("в копии все виды исполнителей объявлены «есть»")
    kind = absent[0]
    draft = _draft_record(cat, "probe_bez_ispolnitelya")
    # X11: перечень шаблонов проверок непуст ⇔ вид check_template объявлен.
    draft["executor_kinds"] = [kind]
    draft["check_templates"] = []
    r = service.post("/api/requests",
                     json={"section": "families",
                           "record_id": "probe_bez_ispolnitelya",
                           "source": {"kind": "вручную",
                                      "document_class": None,
                                      "run_class": None, "ref": None},
                           "draft": draft})
    assert r.status_code == 201, r.text
    rid = r.json()["request_id"]
    service.post(f"/api/requests/{rid}/decide",
                 json={"decision": "принята", "words": "нужна"})
    refused = service.post(f"/api/requests/{rid}/adopt", json={})
    assert refused.status_code == 412
    words = detail(refused)
    assert words["code"] == "executor-not-present"
    assert kind in words["words"] and kind in words["extra"]["kinds"]
    ok = service.post(f"/api/requests/{rid}/adopt",
                      json={"confirm": "исполнителя напишем следом"})
    assert ok.status_code == 200
    print(f"вид без исполнителя: {kind}")


def test_request_for_an_executor_kind_of_an_existing_record(service):
    cat = service.catalog()
    rec = sample_family(cat)
    kinds = cat.dictionaries()["executor_kinds"]
    kind = next(k for k in rec["executor_kinds"])
    r = service.post("/api/requests",
                     json={"section": "families", "record_id": rec["id"],
                           "source": {"kind": "вручную",
                                      "document_class": None,
                                      "run_class": None, "ref": None},
                           "draft": None,
                           "executor_kinds_requested": [kind]})
    assert r.status_code == 201, r.text
    assert kinds and service.catalog().requests()[0][
        "executor_kinds_requested"] == [kind]
    assert not [p for p in service.catalog().validate()
                if p.severity == "error"]


def test_source_ref_is_judged_by_the_a_rules(service):
    r = _submit(service, source={"kind": "вручную",
                                 "document_class": None,
                                 "run_class": None,
                                 "ref": "борт A320 базы"})
    assert r.status_code == 422
    words = detail(r)
    assert words["code"] == "invalid-request"
    assert any(p["code"] == "A01" and "source.ref" in p["where"]
               for p in words["problems"]), words["problems"]
    assert not service.catalog().requests(), "отказ не оставляет заявки"


def test_view_mode_refuses_every_request_action(make_service):
    svc = make_service(None)
    r = _submit(svc)
    assert r.status_code == 403 and detail(r)["code"] == "read-only"
    rid = "req-04763385720803207704"
    for url in (f"/api/requests/{rid}/decide", f"/api/requests/{rid}/adopt"):
        assert svc.post(url, json={}).status_code == 403
    assert svc.put(f"/api/requests/{rid}/cr_key",
                   json={"cr_key": law.cr_key_of(rid)}).status_code == 403
    print("действий над заявками отказано в просмотре: 4")
