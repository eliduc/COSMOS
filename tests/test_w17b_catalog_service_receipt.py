# -*- coding: utf-8 -*-
"""Заготовка волны 17б: квитанция заявки в каталог (схема `receipt/1`).

ID аудита: K07 (заявка считается поданной только по прикладной квитанции
с её идентичностью).

Контракт (oracle):
* `P0-CONTRACTS-v1.2-20260911.md` §4.1: схема `receipt/1` и минимум её
  полей — `schema_version` (`receipt/1`), `request_id` (обязан совпасть
  с отправленным), `state` (слово из `request_states`), `note` («уже
  подана» при повторной выдаче 200), `catalog {version, fingerprint}`,
  `section`, `record_id`, `canon` (канон черновика `draft_canon`),
  `journal_ref`, `decision {by, at, words, version}` (три `null` и
  `version: 0`, если решения нет), `issued_at`; закон клиента: «доставлена»
  только при 201/200 с телом по схеме и совпавшим `request_id`;
* `P0-COSMOS-SIDE-v1-20260911.md` раздел «0-бис», строка «Квитанция
  (В-5, 00:4x)»: «`schema_version: "receipt/1"`… 201 новая; 200 — только
  повтор той же квитанции по тому же `request_id` (note «уже подана»);
  409 request-exists (иной черновик, `extra.existing`) / request-closed;
  422 черновик не по схеме… Схема — в
  `catalog_service/schemas/receipt.schema.json` (копия в
  `template_catalog/schemas` — по решению 8.x)»;
* `P0-CONTRACTS-v1.2-20260911.md` §11.1 (строка «Квитанция») и §11.2
  п. 5: «схема — в `template_catalog/schemas/receipt.schema.json` с
  байт-в-байт копией в `catalog_service/schemas/`»;
* `P0-COSMOS-SIDE-v1-20260911.md` приложение Д — образцы тел 201 и 200 и
  перечень «не квитанций».

Служба поднимается так же, как в существующих стендах службы
(`tests/catalog_service/conftest.py`): временная копия каталога, роль
владельца, рабочая папка во временной, сети наружу нет. Фикстуры
`make_service`/`service` берутся оттуда же импортом — второй обвязки
службы в дереве быть не должно.

Заготовка КРАСНАЯ на d5af1f21: файла схемы квитанции нет ни у читателя,
ни у службы; тело 201 несёт сегодняшние шесть ключей без версии схемы;
иной черновик той же записи отвечает кодом `duplicate-request`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from template_catalog import requests as law                      # noqa: E402
# Обвязка службы — та же, что у стендов службы: фикстуры ввозятся
# импортом (pytest считает ввезённую фикстуру объявленной в модуле).
from tests.catalog_service.conftest import (ROOT, detail,         # noqa: E402,F401
                                            make_service, sample_family,
                                            service)

READER_SCHEMA = ROOT / "template_catalog" / "schemas" / "receipt.schema.json"
SERVICE_SCHEMA = ROOT / "catalog_service" / "schemas" / "receipt.schema.json"
SCHEMA_VERSION = "receipt/1"
#: Поля тела квитанции — минимум контракта §4.1.
RECEIPT_FIELDS = ("schema_version", "request_id", "state", "note", "catalog",
                  "section", "record_id", "canon", "decision", "journal_ref",
                  "issued_at")
RECORD_ID = "proba_kvitancii_w17b"


def _draft_record(cat, fid: str) -> dict:
    """Черновик заявки: запись без `status`, `history` и `stand_tasks`
    (правило R03 схемы черновика)."""
    rec = sample_family(cat)
    draft = {k: v for k, v in rec.items()
             if k not in ("status", "history", "stand_tasks")}
    draft.update({"id": fid, "title": "Проба записи для квитанции",
                  "params": [], "param_sets": [], "conditions": [],
                  "complements": [], "subject_words": None,
                  "rule_form": None})
    return draft


def _submit(svc, draft=None, *, record_id: str = RECORD_ID):
    draft = _draft_record(svc.catalog(), record_id) if draft is None else draft
    body = {"section": "families", "record_id": record_id,
            "source": {"kind": "вручную", "document_class": None,
                       "run_class": None, "ref": None},
            "draft": draft}
    return svc.post("/api/requests", json=body), draft


def _expected_id(record_id: str, draft) -> str:
    """Номер заявки по закону читателя — ожидание считается тем же
    законом, что и служба."""
    return law.request_id_of(
        law.manual_key("families", record_id, law.draft_canon(draft)))


def _schema() -> dict:
    assert READER_SCHEMA.is_file(), (
        f"схемы квитанции нет: {READER_SCHEMA} (контракт §11.2 п. 5 — "
        f"носитель схемы у читателя каталога)")
    return json.loads(READER_SCHEMA.read_text(encoding="utf-8"))


def _schema_problems(body) -> list[str]:
    """Проблемы тела по схеме `receipt/1`."""
    from jsonschema import validators                    # noqa: PLC0415
    schema = _schema()
    класс = validators.validator_for(schema)
    класс.check_schema(schema)
    проверка = класс(schema)
    return [f"{'/'.join(str(x) for x in e.path)}: {e.message}"
            for e in sorted(проверка.iter_errors(body),
                            key=lambda e: list(e.path))]


def _contract_problems(body, *, svc, rid: str, record_id: str,
                       canon: str | None, note: str | None) -> list[str]:
    """Минимум контракта §4.1 сверх схемы: идентичность, каталог,
    решение, ссылка на журнал."""
    беды: list[str] = []
    if not isinstance(body, dict):
        return [f"тело не объект JSON: {body!r}"]
    for поле in RECEIPT_FIELDS:
        if поле not in body:
            беды.append(f"нет поля {поле}")
    if body.get("schema_version") != SCHEMA_VERSION:
        беды.append(f"schema_version = {body.get('schema_version')!r}, "
                    f"контракт требует {SCHEMA_VERSION!r}")
    if body.get("request_id") != rid:
        беды.append(f"request_id = {body.get('request_id')!r} ≠ {rid!r}")
    states = svc.catalog().dictionaries()["request_states"]
    if body.get("state") not in states:
        беды.append(f"state = {body.get('state')!r} вне словаря request_states")
    cat = svc.catalog()
    каталог = body.get("catalog")
    if not isinstance(каталог, dict):
        беды.append(f"catalog = {каталог!r} (ожидался объект version/fingerprint)")
    else:
        if каталог.get("version") != cat.version():
            беды.append(f"catalog.version = {каталог.get('version')!r} ≠ "
                        f"{cat.version()!r}")
        if каталог.get("fingerprint") != cat.fingerprint():
            беды.append("catalog.fingerprint не равен отпечатку каталога службы")
    if body.get("section") != "families":
        беды.append(f"section = {body.get('section')!r}")
    if body.get("record_id") != record_id:
        беды.append(f"record_id = {body.get('record_id')!r} ≠ {record_id!r}")
    if canon is not None and body.get("canon") != canon:
        беды.append(f"canon = {body.get('canon')!r} ≠ канона черновика {canon!r}")
    решение = body.get("decision")
    if not isinstance(решение, dict) or set(решение) != {"by", "at", "words",
                                                         "version"}:
        беды.append(f"decision = {решение!r} (контракт: by, at, words, version)")
    if not body.get("journal_ref"):
        беды.append("journal_ref пуст — ссылки на запись журнала нет")
    if not body.get("issued_at"):
        беды.append("issued_at пуст — времени выдачи нет")
    if note is not None and body.get("note") != note:
        беды.append(f"note = {body.get('note')!r} ≠ {note!r}")
    return беды


def test_receipt_schema_is_one_carrier_with_a_byte_copy_in_the_service():
    """K07, контракт §11.2 п. 5: схема живёт у читателя каталога, у
    службы — байт-в-байт копия; версия схемы закреплена словом."""
    assert READER_SCHEMA.is_file(), f"нет {READER_SCHEMA}"
    assert SERVICE_SCHEMA.is_file(), f"нет {SERVICE_SCHEMA}"
    assert READER_SCHEMA.read_bytes() == SERVICE_SCHEMA.read_bytes(), (
        "копия схемы квитанции у службы разошлась с носителем читателя")
    схема = _schema()
    текст = json.dumps(схема, ensure_ascii=False)
    assert SCHEMA_VERSION in текст, (
        f"схема не закрепляет версию {SCHEMA_VERSION!r}")
    свойства = схема.get("properties") or {}
    нет = [поле for поле in RECEIPT_FIELDS if поле not in свойства]
    assert not нет, f"в схеме нет полей контракта §4.1: {нет}"


def test_new_request_is_answered_by_a_receipt_201(service):
    """K07, контракт §4.1 и приложение Д: новая заявка — 201 с телом по
    схеме `receipt/1`, «подана» и номером по закону читателя."""
    ответ, draft = _submit(service)
    assert ответ.status_code == 201, ответ.text
    assert ответ.headers.get("content-type", "").startswith("application/json")
    тело = ответ.json()
    rid = _expected_id(RECORD_ID, draft)
    беды = _contract_problems(тело, svc=service, rid=rid, record_id=RECORD_ID,
                              canon=law.draft_canon(draft), note=None)
    assert not беды, f"тело 201 не квитанция: {беды}"
    assert тело["state"] == "подана", (
        f"201 — «подана», не «принята» (уточнение аудитора K07): "
        f"{тело['state']!r}")
    assert тело.get("note") != "уже подана", (
        "у новой заявки слова повторной выдачи неуместны")
    assert not _schema_problems(тело), _schema_problems(тело)


def test_repeat_of_the_same_request_is_the_same_receipt_200(service):
    """K07, контракт §4.1: повтор той же заявки — 200 с той же
    квитанцией, тем же номером и словами «уже подана»; дубля нет."""
    первый, draft = _submit(service)
    assert первый.status_code == 201, первый.text
    rid = первый.json().get("request_id")
    повтор, _ = _submit(service, draft)
    assert повтор.status_code == 200, повтор.text
    тело = повтор.json()
    беды = _contract_problems(тело, svc=service, rid=rid, record_id=RECORD_ID,
                              canon=law.draft_canon(draft), note="уже подана")
    assert not беды, f"тело 200 не квитанция: {беды}"
    assert not _schema_problems(тело), _schema_problems(тело)
    assert len(service.catalog().requests()) == 1, (
        "идемпотентный повтор завёл вторую заявку")


def test_another_draft_of_the_same_record_conflicts_as_request_exists(service):
    """K07, контракт «0-бис» (строка «Квитанция»): иной черновик по той
    же записи — 409 `request-exists` с `extra.existing`, а не квитанция."""
    первый, _ = _submit(service)
    assert первый.status_code == 201, первый.text
    иной = _draft_record(service.catalog(), RECORD_ID)
    иной["title"] = "Другой черновик той же записи"
    ответ, _ = _submit(service, иной)
    assert ответ.status_code == 409, ответ.text
    слова = detail(ответ)
    assert слова["code"] == "request-exists", (
        f"код конфликта {слова['code']!r}; контракт «0-бис» называет "
        f"request-exists (иной черновик)")
    assert (слова.get("extra") or {}).get("existing") == \
        первый.json()["request_id"], слова


def test_a_draft_off_schema_is_422_and_never_a_receipt(service):
    """K07, контракт §4.1 и §5.3 стороны COSMOS: черновик не по схеме —
    422 с проблемами, и такое тело квитанцией не считается."""
    кривой = _draft_record(service.catalog(), RECORD_ID)
    кривой.pop("semantics", None)
    ответ, _ = _submit(service, кривой)
    assert ответ.status_code == 422, ответ.text
    слова = detail(ответ)
    assert слова["code"] in ("invalid-request", "bad-request-id"), слова
    assert _schema_problems(ответ.json()), (
        "отказ 422 прошёл схему квитанции — клиент счёл бы заявку поданной")
    assert not service.catalog().requests(), "отказ оставил заявку в очереди"


def test_receipt_is_judged_by_the_schema_and_not_by_the_status_alone():
    """K07, приложение Д («не квитанции») и §4.1: 204, HTML и чужой
    номер квитанцией не являются — отрицательный контроль самой схемы."""
    не_квитанции = [
        {},
        {"request_id": "req-" + "0" * 20, "state": "подана"},
        {"schema_version": "1", "request_id": "req-" + "0" * 20,
         "state": "неизвестно"},
    ]
    for тело in не_квитанции:
        assert _schema_problems(тело), f"схема пропустила не-квитанцию: {тело}"


@pytest.mark.parametrize("роль, ожидание", [("owner", 201), (None, 403)])
def test_only_an_allowed_role_gets_a_receipt(make_service, роль, ожидание):
    """K07, контракт §4.1 (401/403 — канал и роль): режим просмотра
    квитанции не выдаёт."""
    svc = make_service(роль, name=f"каталог-{роль or 'view'}",
                       work=f"work-{роль or 'view'}")
    ответ, _ = _submit(svc)
    assert ответ.status_code == ожидание, ответ.text
    if ожидание == 201:
        assert not _schema_problems(ответ.json()), _schema_problems(ответ.json())
    else:
        assert detail(ответ)["code"] == "read-only"
        assert _schema_problems(ответ.json()), (
            "отказ по роли прошёл схему квитанции — клиент счёл бы "
            "заявку поданной")


# ───────────────── дополнения сдачи K07 (не из заготовки)

def test_a_closed_request_names_the_owner_decision_with_its_version(service):
    """K07/K08, контракт §4.1 и §4.2: отклонённая заявка отказывает
    `request-closed` и НАЗЫВАЕТ решение — слова, версию и время. Без них
    прогон показал бы «отклонена» без причины, а решение владельца
    обязано быть видно одинаково из любого прогона."""
    ответ, draft = _submit(service)
    assert ответ.status_code == 201, ответ.text
    rid = ответ.json()["request_id"]
    assert ответ.json()["decision"] == {"by": None, "at": None,
                                        "words": None, "version": 0}, (
        "у заявки без решения квитанция обязана нести три null и версию 0")
    решение = service.post(f"/api/requests/{rid}/decide",
                           json={"decision": "отклонена",
                                 "words": "механика покрыта соседней записью"})
    assert решение.status_code == 200, решение.text
    отказ, _ = _submit(service, draft)
    assert отказ.status_code == 409, отказ.text
    слова = detail(отказ)
    assert слова["code"] == "request-closed", слова
    extra = слова.get("extra") or {}
    assert extra.get("state") == "отклонена", extra
    решение = extra.get("decision") or {}
    assert set(решение) == {"words", "version", "at"}, решение
    assert решение["words"] == "механика покрыта соседней записью", решение
    assert решение["version"] >= 1, "решение есть, а версия его — нулевая"
    assert решение["at"], "решение без времени"
    assert _schema_problems(отказ.json()), (
        "отказ по закрытой заявке прошёл схему квитанции")


def test_a_kind_request_gets_a_receipt_without_a_canon(service):
    """K07, контракт §4.1: у заявки на ВИД исполнителя черновика нет —
    `canon` в квитанции `null`, и это всё равно квитанция."""
    cat = service.catalog()
    rec = sample_family(cat)
    kind = str(rec["executor_kinds"][0])
    ответ = service.post(
        "/api/requests",
        json={"section": "families", "record_id": rec["id"],
              "source": {"kind": "вручную", "document_class": None,
                         "run_class": None, "ref": None},
              "draft": None, "executor_kinds_requested": [kind]})
    assert ответ.status_code == 201, ответ.text
    тело = ответ.json()
    assert тело["canon"] is None, тело["canon"]
    assert тело["record_id"] == rec["id"] and тело["state"] == "подана"
    assert not _schema_problems(тело), _schema_problems(тело)
    assert тело["request_id"] == law.request_id_of(
        law.kind_request_key("families", rec["id"], [kind]))


def test_the_import_tool_counts_delivery_only_by_a_receipt(make_service,
                                                           tmp_path):
    """K07, контракт §4.1 («переходный `import_requests.one` применяет
    тот же закон»): доставка засчитывается только по квитанции. Три
    ошибочные квитанции аудита — страница входа с кодом 200, 204 без
    тела и чужой номер — исход «не принята», и слова говорят почему;
    четвёртая — ответ СТАРОЙ службы без версии схемы (fail-closed по
    замыслу: площадка обновляется одним раскатом)."""
    from catalog_service import import_requests as tool     # noqa: PLC0415

    # Канал продуктов — с токеном: инструмент импорта ходит в службу тем
    # же машинным каналом, что и CDPM.
    service = make_service("owner", machine_token="probe-token-w17b")
    draft = _draft_record(service.catalog(), RECORD_ID)
    doc = {"request_id": _expected_id(RECORD_ID, draft), "state": "подана",
           "section": "families", "record_id": RECORD_ID,
           "submitted_at": "2026-09-12T10:00:00+02:00",
           "submitted_by": "CDPM",
           "source": {"kind": "вручную", "document_class": None,
                      "run_class": None, "ref": None},
           "draft": draft, "executor_kinds_requested": None,
           "decision": {"by": None, "at": None, "words": None},
           "cr_key": None}
    файл = tmp_path / "outbox" / f"{doc['request_id']}.json"
    файл.parent.mkdir(parents=True, exist_ok=True)
    файл.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    не_квитанции = {
        "страница входа": lambda *_: (200, {"detail": {"code": "bad-form",
                                                       "words": "не JSON"}},
                                      "text/html; charset=utf-8"),
        "204 без тела": lambda *_: (204, {}, None),
        "чужой номер": lambda *_: (
            200, {"schema_version": "receipt/1",
                  "request_id": "req-" + "0" * 20, "state": "подана",
                  "note": "уже подана"}, "application/json"),
        "старая служба": lambda *_: (
            201, {"request_id": doc["request_id"], "state": "подана"},
            "application/json"),
    }
    for имя, post in не_квитанции.items():
        строка = tool.one(файл, api_url="http://127.0.0.1:1",
                          token="probe-token-w17b", post=post)
        assert строка["accepted"] is False, f"{имя}: сочтено доставкой"
        assert строка["catalog_request_id"] is None and строка["state"] is None
        assert строка["words"], имя
        assert not service.catalog().requests(), (
            f"{имя}: заявка завелась в каталоге")

    def через_службу(url, headers, body):
        путь = "/api" + url.split("/api", 1)[1]
        ответ = service.post(путь, content=body, headers=headers)
        return (ответ.status_code, ответ.json(),
                ответ.headers.get("content-type"))

    строка = tool.one(файл, api_url="http://127.0.0.1:1",
                      token="probe-token-w17b", post=через_службу)
    assert строка["accepted"] is True, строка["words"]
    assert строка["catalog_request_id"] == doc["request_id"]
    assert строка["state"] == "подана"
    assert строка["receipt"]["schema_version"] == SCHEMA_VERSION
    assert not _schema_problems(строка["receipt"])
    print(f"не квитанций отвергнуто: {len(не_квитанции)}; квитанций "
          f"принято: 1")


# ───────── правка по неавторскому ревью (С1, С2, С7, Н3, Н4)

def _queue_doc(svc, tmp_path, *, record_id: str = RECORD_ID) -> tuple:
    """Файл очереди с заявкой и он же разобранным документом."""
    draft = _draft_record(svc.catalog(), record_id)
    doc = {"request_id": _expected_id(record_id, draft), "state": "подана",
           "section": "families", "record_id": record_id,
           "submitted_at": "2026-09-12T10:00:00+02:00",
           "submitted_by": "CDPM",
           "source": {"kind": "вручную", "document_class": None,
                      "run_class": None, "ref": None},
           "draft": draft, "executor_kinds_requested": None,
           "decision": {"by": None, "at": None, "words": None},
           "cr_key": None}
    путь = tmp_path / "outbox" / f"{doc['request_id']}.json"
    путь.parent.mkdir(parents=True, exist_ok=True)
    путь.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return путь, doc


def _receipt_body(doc: dict, *, repeat: bool = False, **правки) -> dict:
    """Годная квитанция на эту заявку — образец, от которого стенды
    отступают по одному полю: так видно, ЧТО именно делает ответ не
    квитанцией."""
    тело = {"schema_version": SCHEMA_VERSION,
            "request_id": doc["request_id"], "state": "подана",
            "note": "уже подана" if repeat else None, "repeat": repeat,
            "catalog": {"version": "0.2.0-draft.1", "fingerprint": "a" * 64},
            "section": doc["section"], "record_id": doc["record_id"],
            "canon": law.draft_canon(doc["draft"]),
            "decision": {"by": None, "at": None, "words": None, "version": 0},
            "journal_ref": {"op": "request-submit",
                            "at": "2026-09-12T10:00:00+02:00",
                            "actor": "владелец"},
            "issued_at": "2026-09-12T10:00:00+02:00",
            "submitted_by": "CDPM", "words": "заявка принята",
            "problems": []}
    тело.update(правки)
    return тело


def test_the_state_words_of_the_receipt_are_the_catalog_dictionary(service):
    """С1: `state` квитанции судится ЗАКРЫТЫМ перечнем слов, и перечень
    один — `enum` схемы (клиент судит ответ службы, каталога которой у
    него может не быть). Стенд держит его равным словарю каталога: два
    перечня разошлись бы молча."""
    from template_catalog import receipts                   # noqa: PLC0415

    словарь = service.catalog().dictionaries()["request_states"]
    assert sorted(receipts.receipt_states()) == sorted(словарь), (
        f"перечень состояний схемы квитанции {receipts.receipt_states()} "
        f"разошёлся со словарём request_states каталога {словарь}")
    assert receipts.validate_receipt(
        _receipt_body({"request_id": "req-" + "0" * 20, "section": "families",
                       "record_id": "x", "draft": None})) == []


@pytest.mark.parametrize("имя, правки", [
    ("состояние вне словаря", {"state": "x"}),
    ("без обязательных полей", None),
    ("чужая запись", {"section": "criteria", "record_id": "drugaja"}),
    ("чужой канон черновика", {"canon": "b" * 64}),
    ("201 с признаком повтора", {"note": "уже подана", "repeat": True}),
])
def test_the_import_tool_applies_the_whole_client_law(make_service, tmp_path,
                                                      имя, правки):
    """С1 и С2, контракт §4.1: закон клиента применяется ЦЕЛИКОМ и ОДНОЙ
    функцией (`template_catalog.receipts.validate_receipt`) — тело по
    схеме, состояние словом закрытого перечня и идентичность заявки
    (номер, раздел, запись, канон черновика). Прежде проверялись только
    версия схемы и номер: 201 с чужой записью или состоянием «x»
    засчитывался доставкой."""
    from catalog_service import import_requests as tool     # noqa: PLC0415

    svc = make_service("owner", machine_token="probe-token-w17b")
    файл, doc = _queue_doc(svc, tmp_path)
    тело = _receipt_body(doc) if правки is not None else {
        "schema_version": SCHEMA_VERSION, "request_id": doc["request_id"],
        "state": "подана"}
    if правки:
        тело.update(правки)

    def post(*_):
        return 201, тело, "application/json"

    строка = tool.one(файл, api_url="http://127.0.0.1:1",
                      token="probe-token-w17b", post=post)
    assert строка["accepted"] is False, (
        f"{имя}: ответ засчитан доставкой — {строка['words']}")
    assert строка["catalog_request_id"] is None and строка["state"] is None
    assert "не является квитанцией" in строка["words"], строка["words"]
    assert "повтор тем же номером" in строка["words"], строка["words"]
    # Положительный контроль: та же дорога с годной квитанцией — доставка.
    годная = tool.one(файл, api_url="http://127.0.0.1:1",
                      token="probe-token-w17b",
                      post=lambda *_: (201, _receipt_body(doc),
                                       "application/json"))
    assert годная["accepted"] is True, годная["words"]
    print(f"{имя}: отвергнуто словами «{строка['words'][:70]}…»")


def test_the_journal_ref_of_a_repeat_points_at_the_journal(make_service,
                                                           tmp_path):
    """Н6: `journal_ref` — ссылка на ЗАПИСЬ ЖУРНАЛА о приёме, и у
    повторной выдачи (200) это строка о ПЕРВОМ приёме. Прежде туда
    клались поля заявки (`submitted_at`, `submitted_by`) — момент
    составления у ПРОДУКТА, а не момент записи службой; ссылкой на след
    приёма это не было."""
    svc = make_service("owner", machine_token="probe-token-w17b")
    _, doc = _queue_doc(svc, tmp_path)
    headers = {"X-Catalog-Product": "CDPM",
               "X-Catalog-Token": "probe-token-w17b"}
    первый = svc.post("/api/requests", json=doc, headers=headers)
    assert первый.status_code == 201, первый.text
    повтор = svc.post("/api/requests", json=doc, headers=headers)
    assert повтор.status_code == 200, повтор.text
    было, стало = первый.json()["journal_ref"], повтор.json()["journal_ref"]
    assert стало == было, (
        f"повторная выдача назвала другой след приёма: {стало} против {было}")
    assert стало["op"] == "request-submit", стало
    assert стало["actor"] == "владелец", (
        f"actor ссылки на журнал — {стало['actor']!r}: это податель "
        f"заявки, а не тот, кто записал её в журнал")
    assert стало["at"] and стало["at"] != doc["submitted_at"], (
        f"at ссылки на журнал равен submitted_at продукта ({стало['at']}) — "
        f"это момент составления, а не момент приёма службой")
    assert повтор.json()["submitted_by"] == "CDPM", повтор.json()


@pytest.fixture()
def receipt_server():
    """Локальная заглушка службы на ЭФЕМЕРНОМ порту (bind на 0): стенд
    судит НАСТОЯЩИЙ путь инструмента импорта — сокет, заголовки, разбор
    тела, — а не подставной `post`.

    Номер порта не литерал: занятый порт покрасил бы стенд на чужой
    беде, а живые службы (8095, 8105, 8123 …) стенд трогать не вправе.
    Сервер останавливается всегда: слушатель, переживший стенд, — та же
    грязь, что и файл, оставленный в дереве."""
    import threading                                        # noqa: PLC0415
    from http.server import (BaseHTTPRequestHandler,        # noqa: PLC0415
                             ThreadingHTTPServer)
    from types import SimpleNamespace                       # noqa: PLC0415

    ответы: list = []
    принято: list = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_POST(self):                                  # noqa: N802
            длина = int(self.headers.get("Content-Length") or 0)
            сырьё = self.rfile.read(длина) if длина else b""
            принято.append({"path": self.path,
                            "body": json.loads(сырьё or b"{}")})
            код, тип, тело = ответы.pop(0) if ответы else (
                500, "application/json", b'{"detail": {"code": "no-script"}}')
            self.send_response(код)
            if тип:
                self.send_header("Content-Type", тип)
            if тело:
                self.send_header("Content-Length", str(len(тело)))
            self.end_headers()
            if тело:
                self.wfile.write(тело)

        def log_message(self, *args):                       # тишина в логе
            return

    сервер = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    поток = threading.Thread(target=сервер.serve_forever, daemon=True)
    поток.start()
    try:
        yield SimpleNamespace(
            url=f"http://127.0.0.1:{сервер.server_address[1]}",
            ответы=ответы, принято=принято)
    finally:
        сервер.shutdown()
        сервер.server_close()
        поток.join(timeout=5)


def test_the_real_http_path_counts_delivery_only_by_a_receipt(
        make_service, tmp_path, receipt_server):
    """С7, контракт §4.1: закон квитанции судится на НАСТОЯЩЕМ пути —
    через сокет, с заголовками и кодами ответа. Прежде все стенды шли
    через `TestClient`, и разбор 204, не-JSON и типа содержимого
    (`http_post`) не исполнялся ни одним из них."""
    from catalog_service import import_requests as tool     # noqa: PLC0415

    svc = make_service("owner", machine_token="probe-token-w17b")
    файл, doc = _queue_doc(svc, tmp_path)

    def json_ответ(код, тело) -> tuple:
        return (код, "application/json",
                json.dumps(тело, ensure_ascii=False).encode("utf-8"))

    не_квитанции = {
        "HTML 200": (200, "text/html; charset=utf-8",
                     "<html><body>Вход</body></html>".encode("utf-8")),
        "204 без тела": (204, None, b""),
        "чужой номер": json_ответ(200, _receipt_body(
            doc, repeat=True, request_id="req-" + "0" * 20)),
        "старая служба": json_ответ(
            201, {"request_id": doc["request_id"], "state": "подана"}),
    }
    for имя, ответ in не_квитанции.items():
        receipt_server.ответы.append(ответ)
        строка = tool.one(файл, api_url=receipt_server.url,
                          token="probe-token-w17b")
        assert строка["accepted"] is False, f"{имя}: сочтено доставкой"
        assert строка["catalog_request_id"] is None and строка["state"] is None
        assert "не является квитанцией" in строка["words"], (
            f"{имя}: слова без общей причины — {строка['words']}")
        assert "повтор тем же номером" in строка["words"], (
            f"{имя}: слова не говорят, что делать дальше — "
            f"{строка['words']}")

    receipt_server.ответы.append(json_ответ(201, _receipt_body(doc)))
    первая = tool.one(файл, api_url=receipt_server.url,
                      token="probe-token-w17b")
    assert первая["accepted"] is True, первая["words"]
    assert первая["catalog_request_id"] == doc["request_id"]
    assert первая["receipt"]["schema_version"] == SCHEMA_VERSION

    receipt_server.ответы.append(json_ответ(
        200, _receipt_body(doc, repeat=True)))
    повтор = tool.one(файл, api_url=receipt_server.url,
                      token="probe-token-w17b")
    assert повтор["accepted"] is True, повтор["words"]
    assert повтор["receipt"]["note"] == "уже подана"

    # Служба получила ровно то, что лежало в очереди, и тем же номером.
    assert {q["path"] for q in receipt_server.принято} == {"/api/requests"}
    assert {q["body"]["request_id"] for q in receipt_server.принято} == \
        {doc["request_id"]}
    print(f"настоящий путь: не квитанций отвергнуто "
          f"{len(не_квитанции)}, квитанций принято 2, запросов "
          f"{len(receipt_server.принято)}")
