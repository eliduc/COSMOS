# -*- coding: utf-8 -*-
"""С1: машинный канал заявок, закон номера и кандидат конвейера (шаг 6).

Три вещи, которые здесь доказываются и которых у шага 5 не было:

1. **Канал fail-closed.** Без `CATALOG_MACHINE_TOKEN` подача от продукта
   отказывает словами, а не проходит «потому что токен пуст с обеих
   сторон». Площадки и установка заказчика — именно этот случай.
2. **Номер от содержания, а не счётчик.** Один пробел, увиденный CDPM и
   COSMOS на разных машинах без связи со службой, получает ОДИН номер;
   повтор подачи идемпотентен; иной канон под тем же номером —
   столкновение, а не тихая перезапись.
3. **Запись рождается сборкой.** `adopt {"candidate": true}` берёт запись
   из файла конвейера ВМЕСТЕ со стендовыми задачами (схема запрещает
   `stand_tasks` у черновика заявки), а сборка кандидата удаляет.

Ожидания считаются тем же законом читателя, что и служба
(`template_catalog.requests`), а не литералами: литерал устареет при
первой же правке закона, и три свидетеля разойдутся молча.
"""
from __future__ import annotations

import json

import pytest

from template_catalog import requests as law
from template_catalog import writer
from tests.catalog_service.conftest import (MARKER, detail, folder_bytes,
                                            sample_family)

#: Токен канала — ASCII: заголовки HTTP кириллицу не несут (та же
#: причина, по которой ASCII-ключом задаётся роль службы).
TOKEN = "probe-machine-channel-token"
HEADERS = {"X-Catalog-Product": "CDPM", "X-Catalog-Token": TOKEN}
#: Ключ пробного пробела: тот же материал, что у трёх свидетелей закона
#: (§2.1 проекта шага 6) — тест CDPM и тест читателя знают те же числа.
LAW_KEY = law.request_key("families", "HARD", "PAIRING",
                          "правило образец сторожа закона")
LAW_ID = law.request_id_of(LAW_KEY)


@pytest.fixture()
def machine(make_service):
    """Служба владельца с включённым машинным каналом."""
    return make_service("owner", machine_token=TOKEN)


def draft_of(cat, fid: str, title: str = "Пробная семья заявки") -> dict:
    """Черновик заявки: запись БЕЗ status, history и stand_tasks (R03)."""
    rec = sample_family(cat)
    draft = {k: v for k, v in rec.items()
             if k not in ("status", "history", "stand_tasks")}
    draft.update({"id": fid, "title": title, "params": [], "param_sets": [],
                  "conditions": [], "complements": [], "subject_words": None,
                  "rule_form": None})
    return draft


def request_doc(cat, fid: str, *, request_id: str = LAW_ID,
                by: str = "CDPM", ref: str = "run-0090#421af21358a4",
                draft: dict | None = None, kinds=None,
                title: str = "Пробная семья заявки") -> dict:
    """Запись заявки §6 схемы целиком — то, что шлёт продукт."""
    return {"request_id": request_id, "state": "подана", "section": "families",
            "record_id": fid, "submitted_at": "2026-09-01T10:15:00+03:00",
            "submitted_by": by,
            "source": {"kind": "посадка категории C",
                       "document_class": "правила планирования экипажей",
                       "run_class": "прогон CDPM", "ref": ref},
            "draft": draft if draft is not None
            else (None if kinds else draft_of(cat, fid, title)),
            "executor_kinds_requested": list(kinds) if kinds else None,
            "decision": {"by": None, "at": None, "words": None},
            "cr_key": None}


def post(svc, doc, **headers):
    head = dict(HEADERS)
    head.update(headers)
    return svc.post("/api/requests", json=doc, headers=head)


# ─────────────────────────────────────────────────────────── канал

def test_channel_is_closed_without_the_token(service):
    """Умолчание — канал выключен: `CATALOG_MACHINE_TOKEN` не задан."""
    before = folder_bytes(service.folder)
    doc = request_doc(service.catalog(), "probe_kanal")
    r = post(service, doc)
    assert r.status_code == 403, r.text
    words = detail(r)
    assert words["code"] == "machine-channel-off"
    assert "CATALOG_MACHINE_TOKEN" in words["words"]
    assert folder_bytes(service.folder) == before, "отказ тронул каталог"
    print("канал по умолчанию выключен: 403 machine-channel-off")


def test_wrong_token_and_unknown_product(machine):
    cat = machine.catalog()
    doc = request_doc(cat, "probe_kanal")
    bad = post(machine, doc, **{"X-Catalog-Token": "alien-token"})
    assert bad.status_code == 401 and detail(bad)["code"] == "bad-token"
    alien = post(machine, {**doc, "submitted_by": "ALIEN"},
                 **{"X-Catalog-Product": "ALIEN"})
    assert alien.status_code == 400
    assert detail(alien)["code"] == "submitter-not-allowed"
    assert "products" in detail(alien)["words"]
    known = set(cat.dictionaries()["products"])
    assert "ALIEN" not in known and known
    mixed = post(machine, {**doc, "submitted_by": "COSMOS"})
    assert mixed.status_code == 400, "подпись чужим именем"
    assert detail(mixed)["code"] == "bad-form"
    print(f"отказов канала проверено: 3; словарь products: "
          f"{', '.join(sorted(known))}")


def test_read_only_site_refuses_and_writes_nothing(make_service):
    """Площадка: даже с токеном — просмотр, и каталог байт в байт прежний."""
    svc = make_service(None, read_only=True, machine_token=TOKEN)
    before = folder_bytes(svc.folder)
    r = post(svc, request_doc(svc.catalog(), "probe_ploshchadka"))
    assert r.status_code == 403, r.text
    assert detail(r)["code"] == "read-only"
    assert folder_bytes(svc.folder) == before
    print("витрина с токеном: 403 read-only, записей 0")


# ─────────────────────────────────────────────────── подача и номер

def test_product_submit_writes_the_request_and_keeps_the_fingerprint(machine):
    before = machine.catalog().fingerprint()
    doc = request_doc(machine.catalog(), "probe_ot_cdpm")
    r = post(machine, doc)
    assert r.status_code == 201, r.text
    assert r.json()["request_id"] == LAW_ID
    assert r.json()["state"] == "подана"
    cat = machine.catalog()
    assert cat.fingerprint() == before, "заявки живут вне отпечатка"
    one = cat.requests()[0]
    assert one["submitted_by"] == "CDPM"
    assert one["submitted_at"] == "2026-09-01T10:15:00+03:00", \
        "момент составления подателя — часть рецепта повтора"
    assert one["cr_key"] is None
    assert not [p for p in cat.validate() if p.severity == "error"]
    cosmos = post(machine, request_doc(cat, "probe_ot_cosmos", by="COSMOS",
                                       request_id=law.request_id_of(
                                           law.manual_key(
                                               "families", "probe_ot_cosmos",
                                               None)),
                                       title="Семья от COSMOS"),
                  **{"X-Catalog-Product": "COSMOS"})
    assert cosmos.status_code == 201, cosmos.text
    assert len(machine.catalog().requests()) == 2
    print(f"подач продуктов: 2 (CDPM {LAW_ID}); отпечаток не изменился")


def test_repeat_is_idempotent_and_another_canon_collides(machine):
    cat = machine.catalog()
    doc = request_doc(cat, "probe_povtor")
    assert post(machine, doc).status_code == 201
    again = post(machine, doc)
    assert again.status_code == 200, again.text
    assert again.json()["note"] == "уже подана"
    assert len(machine.catalog().requests()) == 1
    other = request_doc(cat, "probe_povtor", ref="run-0088#421af21358a4",
                        title="Иной черновик того же пробела")
    clash = post(machine, other)
    assert clash.status_code == 409, clash.text
    words = detail(clash)
    assert words["code"] == "request-exists"
    assert words["extra"]["existing"] == LAW_ID
    assert "run-0090" in words["words"], words["words"]
    assert len(machine.catalog().requests()) == 1
    print("повтор той же заявки: 200 «уже подана»; иной канон: 409 "
          "request-exists")


def test_open_request_on_the_record_refuses_a_second_number(machine):
    cat = machine.catalog()
    assert post(machine, request_doc(cat, "probe_zanyata")).status_code == 201
    second = request_doc(cat, "probe_zanyata",
                         request_id=law.request_id_of(law.manual_key(
                             "families", "probe_zanyata", "иной")),
                         title="Второй заход на ту же запись")
    r = post(machine, second)
    assert r.status_code == 409, r.text
    # Волна 17б (П0 §4.1, редакция «0-бис»): у открытой заявки по записи
    # и у того же номера с иным каноном ОДИН код — `request-exists`;
    # отдельный `duplicate-request` снят, потому что случай один —
    # столкновение содержания, и клиент поступает с ним одинаково.
    assert detail(r)["code"] == "request-exists"
    assert detail(r)["extra"]["existing"] == LAW_ID
    print("вторая заявка на занятую запись: 409 request-exists")


def test_closed_request_refuses_until_the_owner_revises(machine):
    cat = machine.catalog()
    assert post(machine, request_doc(cat, "probe_zakryta")).status_code == 201
    assert machine.post(f"/api/requests/{LAW_ID}/decide",
                        json={"decision": "отклонена",
                              "words": "механика уже покрыта соседней "
                                       "записью"}).status_code == 200
    r = post(machine, request_doc(cat, "probe_zakryta"))
    assert r.status_code == 409, r.text
    words = detail(r)
    assert words["code"] == "request-closed"
    assert words["extra"]["state"] == "отклонена"
    assert "соседней" in words["words"], words["words"]
    assert "пересмотр" in words["words"]
    # Пересмотр решения открывает путь: тот же номер, то же содержание.
    assert machine.post(f"/api/requests/{LAW_ID}/decide",
                        json={"decision": "принята",
                              "words": "пересмотр: соседняя запись о другом"}
                        ).status_code == 200
    back = post(machine, request_doc(cat, "probe_zakryta"))
    assert back.status_code == 200 and back.json()["state"] == "принята"
    print("отклонённая заявка: 409 request-closed; после пересмотра — 200")


def test_kind_request_number_is_recomputed_by_the_service(machine):
    """Номер заявки на вид служба считает сама — и судит."""
    cat = machine.catalog()
    rec = sample_family(cat)
    kinds = [str(rec["executor_kinds"][0])]
    want = law.request_id_of(law.kind_request_key("families", rec["id"],
                                                  kinds))
    bad = post(machine, request_doc(cat, rec["id"], kinds=kinds,
                                    request_id=LAW_ID))
    assert bad.status_code == 422, bad.text
    words = detail(bad)
    assert words["code"] == "bad-request-id"
    assert words["extra"]["expected"] == want
    ok = post(machine, request_doc(cat, rec["id"], kinds=kinds,
                                   request_id=want))
    assert ok.status_code == 201, ok.text
    assert machine.catalog().requests()[0]["executor_kinds_requested"] == kinds
    # Набор видов — часть содержания: другой набор даёт другой номер.
    assert law.request_id_of(law.kind_request_key(
        "families", rec["id"], kinds + rec["executor_kinds"][1:2])) != want \
        if len(rec["executor_kinds"]) > 1 else True
    print(f"номер заявки на вид пересчитан службой: {want}")


def test_a_rules_judge_the_source_and_the_draft(machine):
    cat = machine.catalog()
    doc = request_doc(cat, "probe_marker")
    doc["source"]["ref"] = f"run-0090#{MARKER}"
    r = post(machine, doc)
    assert r.status_code == 422, r.text
    words = detail(r)
    assert words["code"] == "invalid-request"
    assert any("source.ref" in p["where"] for p in words["problems"]), \
        words["problems"]
    assert not machine.catalog().requests(), "отказ не оставляет заявки"
    print(f"проблем по адресу заявки: {len(words['problems'])}")


def test_dry_run_writes_nothing_and_names_the_number(machine):
    before = folder_bytes(machine.folder)
    doc = request_doc(machine.catalog(), "probe_suhoy")
    got = machine.post("/api/requests/dry-run", json=doc, headers=HEADERS)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["request_id"] == LAW_ID
    assert body["cr_key_expected"] == law.cr_key_of(LAW_ID)
    assert body["known"] is None
    assert folder_bytes(machine.folder) == before
    assert not machine.catalog().requests()
    print(f"сухой прогон подачи: номер {body['request_id']}, записей 0")


# ─────────────────────────────────────── словарь классов документа

def test_optional_dictionary_key_may_be_created_by_a_draft(service):
    """0.14: `document_classes` заводится черновиком, прочие — нет."""
    cat = service.catalog()
    assert "document_classes" not in cat.dictionaries()
    ok = service.put("/api/drafts/dictionaries/document_classes",
                     json={"value": list(law.DOCUMENT_CLASSES),
                           "note": "перечень классов документа шага 6"})
    assert ok.status_code == 200, ok.text
    bad = service.put("/api/drafts/dictionaries/foo",
                      json={"value": ["x"], "note": "проба"})
    assert bad.status_code == 404 and detail(bad)["code"] == "unknown-key"
    assert "document_classes" in detail(bad)["words"]
    got = service.post("/api/versions/assemble",
                       json={"note": "словарь классов документа"})
    assert got.status_code == 200, got.text
    after = service.catalog().dictionaries()
    # Канон каталога перечни сортирует — сверяется состав, а не порядок.
    assert set(after["document_classes"]) == set(law.DOCUMENT_CLASSES)
    assert len(after["document_classes"]) == len(law.DOCUMENT_CLASSES)
    print(f"классов документа в словаре: {len(law.DOCUMENT_CLASSES)}")


# ──────────────────────────────────────────── кандидат конвейера

def candidate_doc(cat, fid: str, request_id: str, *, kinds_from=None) -> dict:
    """Файл-кандидат: запись целиком со `stand_tasks` и свидетельством."""
    src = kinds_from or next(r for r in cat.families_full()
                             if r.get("stand_tasks"))
    record = {k: v for k, v in src.items() if k != "history"}
    record.update({"id": fid, "title": "Запись из кандидата конвейера",
                   "params": [], "param_sets": [], "conditions": [],
                   "complements": [], "subject_words": None,
                   "rule_form": None, "status": "есть"})
    return {"form_version": 1, "section": "families", "id": fid,
            "record": record, "note": "исполнитель написан агентом по CR",
            "by": "контур самоулучшения",
            "saved_at": "2026-09-02T18:00:00+03:00",
            "request_id": request_id, "cr_key": law.cr_key_of(request_id),
            "evidence": {
                "cr_key": law.cr_key_of(request_id),
                "branch": f"cr-{law.cr_key_of(request_id)}",
                "commit": "abc1234", "evidence_commit": "def5678",
                "at": "2026-09-02T18:00:00+03:00",
                "gates": {"paths": "ok", "pytest": "green",
                          "stand": {"passed": 3, "failed": 0, "skipped": 1},
                          "bindings_build": "ok", "bindings_gate": "ok",
                          "catalog_validate_errors": 0},
                "executors": [{"kind": k, "state": "есть",
                               "file": "_pairing_builder.py",
                               "symbol": f"probe_{k}"}
                              for k in record["executor_kinds"]],
                "isolation": "broker"}}


def put_candidate(svc, doc: dict):
    path = (svc.folder / "candidates" / str(doc["section"])
            / f"{doc['id']}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                               indent=1) + "\n", encoding="utf-8")
    return path


def _accepted(machine, fid: str) -> str:
    """Принятая заявка с ключом CR — состояние, из которого зовут adopt."""
    cat = machine.catalog()
    assert post(machine, request_doc(cat, fid)).status_code == 201
    assert machine.post(f"/api/requests/{LAW_ID}/decide",
                        json={"decision": "принята",
                              "words": "механика общая: принимаю"}
                        ).status_code == 200
    assert machine.put(f"/api/requests/{LAW_ID}/cr_key",
                       json={"cr_key": law.cr_key_of(LAW_ID)},
                       headers=HEADERS).status_code == 200
    return LAW_ID


def test_adopt_with_a_candidate_takes_the_stand_tasks(machine):
    fid = "probe_kandidat"
    rid = _accepted(machine, fid)
    path = put_candidate(machine, candidate_doc(machine.catalog(), fid, rid))
    got = machine.post(f"/api/requests/{rid}/adopt", json={"candidate": True})
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["draft_written"] is True
    assert body["candidate"] == f"candidates/families/{fid}.json"
    assert body["executors_missing"] == []
    assert path.is_file(), "adopt кандидата не удаляет — это делает сборка"
    draft = json.loads((machine.settings.drafts_dir / "families"
                        / f"{fid}.json").read_text(encoding="utf-8"))
    assert draft["request_id"] == rid
    assert draft["record"]["status"] == "есть"
    assert draft["record"]["stand_tasks"], \
        "стендовые задачи кандидата обязаны попасть в черновик"
    assert "cr_key" not in draft["record"] and "evidence" not in draft["record"]
    assert "стенд 3/0/1" in draft["note"], draft["note"]
    print(f"стендовых задач из кандидата: "
          f"{len(draft['record']['stand_tasks'])}")


def test_adopt_without_the_candidate_refuses_while_one_lies_there(machine):
    """Кандидат есть, а берут не его — отказ словами, и сборка чужой
    файл не сносит (ревью волны 2).

    Прежде сборка удаляла кандидата по одному лишь `request_id`
    черновика: перевод заявки БЕЗ кандидата уносил файл конвейера, а
    предпроверка выпуска 9 («кандидат не импортирован») об этом
    молчала — содержимое оставалось только в ветке CR."""
    fid = "probe_kandidat_ne_vzyat"
    rid = _accepted(machine, fid)
    path = put_candidate(machine, candidate_doc(machine.catalog(), fid, rid))
    got = machine.post(f"/api/requests/{rid}/adopt", json={})
    assert got.status_code == 412, got.text
    body = got.json()["detail"]
    assert body["code"] == "candidate-ignored"
    assert f"candidates/families/{fid}.json" in body["words"]
    assert path.is_file(), "отказ файла не трогает"

    # Осознанный обход — тем же полем confirm; черновик тогда не помечен
    # кандидатом, и сборка файл НЕ удаляет.
    got = machine.post(f"/api/requests/{rid}/adopt",
                       json={"confirm": "беру черновик заявки, кандидата "
                                        "разберу отдельно"})
    assert got.status_code == 200, got.text
    draft = json.loads((machine.settings.drafts_dir / "families"
                        / f"{fid}.json").read_text(encoding="utf-8"))
    assert draft["from_candidate"] is False
    out = machine.post("/api/versions/assemble",
                       json={"note": "запись без кандидата"})
    assert out.status_code == 200, out.text
    assert "candidates_removed" not in out.json()
    assert path.is_file(), "сборка сносит только СВОЙ кандидат"
    from catalog_service import request_queue as rq
    left = rq.pending_candidates(machine.settings, machine.catalog())
    assert [c["file"] for c in left] == \
        [f"candidates/families/{fid}.json"], left
    print(f"кандидат {path.name} остался и виден предпроверке выпуска 9")


def test_assemble_removes_the_candidate_and_names_it(machine):
    fid = "probe_kandidat_sborka"
    rid = _accepted(machine, fid)
    path = put_candidate(machine, candidate_doc(machine.catalog(), fid, rid))
    assert machine.post(f"/api/requests/{rid}/adopt",
                        json={"candidate": True}).status_code == 200
    got = machine.post("/api/versions/assemble",
                       json={"note": "запись по заявке конвейера"})
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["candidates_removed"] == [f"candidates/families/{fid}.json"]
    assert any("включите удаление в коммит" in line for line in body["next"])
    assert not path.exists(), "сборка обязана убрать файл кандидата"
    cat = machine.catalog()
    rec = cat.family(fid)
    assert rec["status"] == "есть" and rec["stand_tasks"]
    assert [h["kind"] for h in rec["history"]] == [
        "заявка", "решение владельца", "создание"]
    assert cat.requests()[0]["state"] == "есть"
    assert cat.requests()[0]["cr_key"] == law.cr_key_of(rid)
    assert not [p for p in cat.validate() if p.severity == "error"]
    print(f"кандидат убран сборкой; строк истории записи: "
          f"{len(rec['history'])}")


def test_the_service_says_words_on_every_step_not_only_on_refusal(machine):
    """Приём говорит словами так же, как отказ (§5.1, буквально).

    Продукт кладёт эти слова в квитанцию нажатия, инструмент импорта — в
    файл-ответ площадки, экран — в подсказку: пересказывать ответ своими
    словами каждому потребителю значило бы завести три разных правды."""
    fid = "probe_slova"
    cat = machine.catalog()
    first = post(machine, request_doc(cat, fid))
    assert first.status_code == 201, first.text
    said = [first.json()["words"]]
    assert said[0] == (f"заявка от продукта CDPM принята: {LAW_ID} (подана); "
                       f"решение — за владельцем")
    again = post(machine, request_doc(machine.catalog(), fid))
    assert again.status_code == 200
    said.append(again.json()["words"])
    assert "уже подана" in said[-1] and "дубль не заводится" in said[-1]
    assert machine.post(f"/api/requests/{LAW_ID}/decide",
                        json={"decision": "принята",
                              "words": "механика общая: принимаю"}
                        ).status_code == 200
    key = machine.put(f"/api/requests/{LAW_ID}/cr_key",
                      json={"cr_key": law.cr_key_of(LAW_ID)}, headers=HEADERS)
    assert key.status_code == 200, key.text
    said.append(key.json()["words"])
    assert said[-1] == (f"ключ CR {law.cr_key_of(LAW_ID)} записан по вызову "
                        f"CDPM: заявка {LAW_ID} — «исполнитель заявлен»")
    assert key.json()["cr_key"] == law.cr_key_of(LAW_ID), \
        "слова добавляются К записи заявки, а не вместо неё"
    put_candidate(machine, candidate_doc(machine.catalog(), fid, LAW_ID))
    adopted = machine.post(f"/api/requests/{LAW_ID}/adopt",
                           json={"candidate": True})
    assert adopted.status_code == 200, adopted.text
    said.append(adopted.json()["words"])
    assert said[-1].startswith(
        f"кандидат записи из CR {law.cr_key_of(LAW_ID)} ")
    assert "импортирован в черновики" in said[-1]
    assert "заявка перейдёт в «есть» той же записью" in said[-1]
    assert all(said), said
    print(f"строк слов приёма сверено: {len(said)}")


def test_candidate_of_another_request_is_refused(machine):
    fid = "probe_chuzhoy"
    rid = _accepted(machine, fid)
    doc = candidate_doc(machine.catalog(), fid, rid)
    doc["cr_key"] = "cr00000000"
    put_candidate(machine, doc)
    r = machine.post(f"/api/requests/{rid}/adopt", json={"candidate": True})
    assert r.status_code == 422, r.text
    assert detail(r)["code"] == "candidate-mismatch"
    assert law.cr_key_of(rid) in detail(r)["words"]
    print("кандидат с чужим ключом CR: 422 candidate-mismatch")


def test_candidate_path_is_built_by_the_service_only(machine, tmp_path):
    """Путь снаружи не принимается: `adopt` называет ЗАЯВКУ, а файл
    служба ищет сама по разделу и записи заявки."""
    fid = "probe_bez_kandidata"
    rid = _accepted(machine, fid)
    outside = tmp_path / "чужой-кандидат.json"
    outside.write_text(json.dumps(
        candidate_doc(machine.catalog(), fid, rid), ensure_ascii=False),
        encoding="utf-8")
    r = machine.post(f"/api/requests/{rid}/adopt",
                     json={"candidate": True, "path": str(outside),
                           "file": str(outside)})
    assert r.status_code == 404, r.text
    assert detail(r)["code"] == "no-candidate"
    assert "candidates/families" in detail(r)["words"]
    assert str(tmp_path) not in detail(r)["words"], \
        "абсолютных путей машины наружу не бывает"
    print("путь кандидата снаружи не принят: 404 no-candidate")


def test_the_request_row_carries_the_expected_cr_key_and_candidate(machine):
    fid = "probe_stroka"
    rid = _accepted(machine, fid)
    put_candidate(machine, candidate_doc(machine.catalog(), fid, rid))
    rows = machine.get("/api/requests").json()["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["cr_key_expected"] == law.cr_key_of(rid)
    assert row["candidate"] == f"candidates/families/{fid}.json"
    assert row["candidate_evidence"]["gates"]["stand"]["failed"] == 0
    by_key = machine.get(
        f"/api/requests?cr_key={law.cr_key_of(rid)}").json()["items"]
    assert [r["request_id"] for r in by_key] == [rid]
    by_ref = machine.get("/api/requests?ref=run-0090").json()["items"]
    assert [r["request_id"] for r in by_ref] == [rid]
    assert machine.get("/api/requests?ref=run-0001").json()["items"] == []
    print(f"строк заявок сверено: {len(rows)}; фильтры cr_key и ref работают")


def test_request_from_a_product_survives_a_writer_rewrite(machine):
    """Заявка от продукта читается писателем и читателем без потерь:
    номер в 20 цифр схему `$defs/request` не ломает."""
    cat = machine.catalog()
    assert post(machine, request_doc(cat, "probe_pisatel")).status_code == 201
    rows = machine.catalog().requests()
    writer.write_requests(machine.folder, rows)
    machine.store.forget()
    again = machine.catalog()
    assert [r["request_id"] for r in again.requests()] == [LAW_ID]
    assert not [p for p in again.validate() if p.severity == "error"]
    print(f"заявка пережила перезапись писателем: {LAW_ID}")
