# -*- coding: utf-8 -*-
"""Заготовка волны 17б: свидетельство поддержки записи (K06).

ID аудита: K06 («исполнимо» выводится только из подтверждённой поддержки
ТОЧНОЙ записи одинаково у службы, CDPM и приёмника).

Контракт (oracle):
* `P0-CONTRACTS-v1.2-20260911.md` §3.5: «служба каталога считает
  диагностику по СОБСТВЕННЫМ ячейкам записи (`present_kinds` — по
  записи, не глобально); `adopt` без собственных ячеек — «не проверено»,
  не «есть»»; ячейка — `record_id × section × продукт × вид → {state ∈
  {есть, нет, не проверено}, …}`; §3.7 (критерий закрытия K06: «вид
  существует только у другой семьи → `missing` новой семьи не пуст»);
* `P0-COSMOS-SIDE-v1-20260911.md` раздел «0-бис», строка «Свидетельство
  поддержки (В-2)»: «носитель — файл `catalog/support.json` (вне
  отпечатка, как requests.json), пишет ТОЛЬКО служба каталога одной
  функцией читателя… «есть» без стенда = «не проверено»… ручка
  `GET /api/records/{section}/{id}/support` — представление того же
  файла, не второй носитель»;
* `P0-COSMOS-SIDE-v1-20260911.md` §4.2 (поля файла и ячейки: form_version,
  catalog, computed_at/computed_by, cells[] с section, record_id, kind,
  product, state, binding, bindings_file, stand, approved; обновление
  между выпусками — `POST /api/support/refresh`), §4.3 (семантика
  `adopt`: `have` — виды ячеек ЭТОЙ записи; отказ `executor-not-present`
  412 с `extra.kinds`, `extra.record`) и приложение Г (образец ячейки).

Служба поднимается обвязкой стендов службы
(`tests/catalog_service/conftest.py`): временная копия каталога, роль
владельца, сети наружу нет; живой `catalog/` дерева не трогается.

Заготовка КРАСНАЯ на d5af1f21: `adopt` судит по глобальному
`present_kinds` (вид, объявленный «есть» у ЧУЖОЙ записи, разрешает
перевод), файла `catalog/support.json` служба не пишет, ручки
`/api/records/{section}/{id}/support` нет.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import template_catalog as tc                                     # noqa: E402
from template_catalog import bindings as tb                       # noqa: E402
# Обвязка службы — та же, что у стендов службы (фикстуры ввозятся
# импортом: pytest считает ввезённую фикстуру объявленной в модуле).
from tests.catalog_copies import BINDINGS_FILE                    # noqa: E402
from tests.catalog_service.conftest import (ROOT, detail,         # noqa: E402,F401
                                            git_repo, make_service,
                                            sample_family, service)

SUPPORT_FILE = "support.json"
REFRESH = "/api/support/refresh"
#: Состояния ячейки свидетельства (контракт §3.5 и §4.2 стороны COSMOS).
CELL_STATES = {"есть", "нет", "не проверено"}
#: Поля ячейки (контракт §4.2 и приложение Г).
CELL_KEYS = ("section", "record_id", "kind", "product", "state", "binding",
             "bindings_file", "stand", "approved")
#: Поля идентичности файла привязок в ячейке (§4.2).
BINDINGS_FILE_KEYS = ("sha256", "product_version", "generated_from",
                      "catalog_version", "catalog_fingerprint")
RECORD_ID = "proba_podderzhki_w17b"


def _draft_record(cat, fid: str, kinds) -> dict:
    """Черновик заявки на НОВУЮ запись, объявляющую виды исполнителей.

    `check_templates` пуст, а вид `check_template` в перечень не берётся:
    правило X11 связывает их, и заготовка не должна ловить его вместо
    проверяемого K06."""
    rec = sample_family(cat)
    draft = {k: v for k, v in rec.items()
             if k not in ("status", "history", "stand_tasks")}
    draft.update({"id": fid, "title": "Проба записи без исполнителя",
                  "params": [], "param_sets": [], "conditions": [],
                  "complements": [], "subject_words": None,
                  "rule_form": None, "executor_kinds": list(kinds),
                  "check_templates": []})
    return draft


def _kind_present_at_another_record(svc) -> str:
    """Вид, объявленный «есть» у ЧУЖОЙ записи привязок продукта."""
    from catalog_service import executors                # noqa: PLC0415
    есть = executors.present_kinds(svc.catalog(), svc.settings)
    годные = sorted(k for k in есть if k != "check_template")
    assert годные, (
        "в копии каталога ни один вид (кроме check_template) не объявлен "
        "«есть» — стенд K06 не на чем ставить")
    return годные[0]


def _submit_and_accept(svc, kind: str) -> str:
    """Подать заявку на новую запись с этим видом и принять её."""
    draft = _draft_record(svc.catalog(), RECORD_ID, [kind])
    ответ = svc.post("/api/requests",
                     json={"section": "families", "record_id": RECORD_ID,
                           "source": {"kind": "вручную",
                                      "document_class": None,
                                      "run_class": None, "ref": None},
                           "draft": draft})
    assert ответ.status_code == 201, ответ.text
    rid = ответ.json()["request_id"]
    решение = svc.post(f"/api/requests/{rid}/decide",
                       json={"decision": "принята", "words": "нужна запись"})
    assert решение.status_code == 200, решение.text
    return rid


def _approved_keys(утверждение) -> list:
    """Ключи поля `approved` (пустой перечень, если это не объект)."""
    return sorted(утверждение) if isinstance(утверждение, dict) else []


def _support_doc(svc) -> dict:
    файл = svc.folder / SUPPORT_FILE
    assert файл.is_file(), (
        f"службой не записан носитель свидетельства {файл} (контракт "
        f"«0-бис»: файл catalog/support.json пишет только служба)")
    return json.loads(файл.read_text(encoding="utf-8"))


def test_adopt_judges_the_executor_by_the_records_own_cells(service):
    """K06, контракт §3.5 и §4.3: вид, объявленный «есть» у ДРУГОЙ
    записи, исполнимости новой записи не даёт — отказ 412 словами с
    перечнем видов и именем записи."""
    kind = _kind_present_at_another_record(service)
    rid = _submit_and_accept(service, kind)
    ответ = service.post(f"/api/requests/{rid}/adopt", json={})
    assert ответ.status_code == 412, (
        f"перевод записи прошёл: вид {kind} объявлен «есть» у чужой "
        f"записи, а служба сочла его исполнителем этой "
        f"(HTTP {ответ.status_code})")
    слова = detail(ответ)
    assert слова["code"] == "executor-not-present", слова
    extra = слова.get("extra") or {}
    assert kind in (extra.get("kinds") or []), extra
    assert kind in слова["words"], слова["words"]
    assert str(extra.get("record") or слова["words"]).find(RECORD_ID) >= 0, (
        f"отказ не называет запись {RECORD_ID}: {слова}")


def test_confirm_still_lets_the_owner_through_after_the_refusal(service):
    """K06, контракт §4.3 (положительный контроль): «осознанный обход
    `confirm` сохраняется» — отказ судит свидетельство, а не запрещает
    работу владельцу."""
    kind = _kind_present_at_another_record(service)
    rid = _submit_and_accept(service, kind)
    отказ = service.post(f"/api/requests/{rid}/adopt", json={})
    assert отказ.status_code == 412, (
        f"без свидетельства перевод обязан отказать 412, а не "
        f"{отказ.status_code} (контракт §4.3)")
    ок = service.post(f"/api/requests/{rid}/adopt",
                      json={"confirm": "исполнителя напишем следом"})
    assert ок.status_code == 200, (
        f"осознанный обход confirm не сработал: HTTP {ок.status_code}")
    assert ок.json().get("draft_written") is True, (
        "перевод по confirm не записал черновик записи")


def test_only_the_service_writes_the_support_file(make_service):
    """K06, контракт «0-бис»: носитель один и пишет его только служба;
    в режиме просмотра свидетельство не пересчитывается."""
    svc = make_service("owner")
    assert not (svc.folder / SUPPORT_FILE).exists(), (
        "копия каталога пришла со свидетельством — стенд судил бы чужой "
        "файл")
    ответ = svc.post(REFRESH, json={})
    assert ответ.status_code == 200, (
        f"ручка {REFRESH} не пересчитала свидетельство: HTTP "
        f"{ответ.status_code} {ответ.text[:120]} (контракт §4.2)")
    doc = _support_doc(svc)
    assert doc.get("form_version") == 1, doc.get("form_version")
    assert doc.get("computed_by") == "служба", doc.get("computed_by")
    assert doc.get("computed_at"), "нет времени расчёта"
    каталог = doc.get("catalog") or {}
    assert каталог.get("version") == svc.catalog().version()
    assert каталог.get("fingerprint") == svc.catalog().fingerprint()
    assert doc.get("cells"), "таблица ячеек пуста"

    смотрящий = make_service("analyst", read_only=True, name="каталог-ro",
                             work="work-ro")
    отказ = смотрящий.post(REFRESH, json={})
    assert отказ.status_code == 403, отказ.text
    assert detail(отказ)["code"] == "read-only", detail(отказ)
    assert not (смотрящий.folder / SUPPORT_FILE).exists(), (
        "режим просмотра записал свидетельство")


def test_support_cells_carry_the_contract_form(service):
    """K06, контракт §4.2 и приложение Г: ключ ячейки, состояние по
    словарю трёх слов и идентичность файла привязок."""
    обновление = service.post(REFRESH, json={})
    assert обновление.status_code == 200, (
        f"ручка {REFRESH} не пересчитала свидетельство: HTTP "
        f"{обновление.status_code} (контракт §4.2)")
    doc = _support_doc(service)
    products = service.catalog().dictionaries()["products"]
    kinds = service.catalog().dictionaries()["executor_kinds"]
    for ячейка in doc["cells"]:
        нет = [k for k in CELL_KEYS if k not in ячейка]
        assert not нет, f"в ячейке нет полей {нет}: {ячейка}"
        assert ячейка["state"] in CELL_STATES, ячейка
        assert ячейка["product"] in products, ячейка
        assert ячейка["kind"] in kinds, ячейка
        файл = ячейка.get("bindings_file") or {}
        отсутствуют = [k for k in BINDINGS_FILE_KEYS if k not in файл]
        assert not отсутствуют, f"bindings_file без полей {отсутствуют}: {ячейка}"
        утверждение = ячейка.get("approved")
        assert утверждение is None or _approved_keys(утверждение) == [
            "at", "by", "how"], ячейка


def test_a_binding_without_a_stand_is_not_checked(service):
    """K06, контракт «0-бис» и §4.2: «есть» — строка привязок И стенд без
    провалов; строка «есть» без стендового подтверждения даёт «не
    проверено», а не «есть»."""
    обновление = service.post(REFRESH, json={})
    assert обновление.status_code == 200, (
        f"ручка {REFRESH} не пересчитала свидетельство: HTTP "
        f"{обновление.status_code} (контракт §4.2)")
    doc = _support_doc(service)
    for ячейка in doc["cells"]:
        стенд = ячейка.get("stand") or {}
        прошло = int(стенд.get("passed") or 0)
        провалов = int(стенд.get("failed") or 0)
        if ячейка["state"] == "есть":
            assert прошло >= 1 and провалов == 0, (
                f"ячейка «есть» без зелёного стенда: {ячейка}")
        строка = ячейка.get("binding") or {}
        if str(строка.get("state")) == "есть" and прошло < 1:
            assert ячейка["state"] == "не проверено", (
                f"строка привязок «есть» без стенда обязана давать «не "
                f"проверено»: {ячейка}")


def test_support_handle_shows_the_same_content_as_the_file(service):
    """K06, контракт «0-бис»: ручка — ПРЕДСТАВЛЕНИЕ файла, а не второй
    носитель; ячейки записи в ответе те же, что в файле."""
    обновление = service.post(REFRESH, json={})
    assert обновление.status_code == 200, (
        f"ручка {REFRESH} не пересчитала свидетельство: HTTP "
        f"{обновление.status_code} (контракт §4.2)")
    doc = _support_doc(service)
    запись = doc["cells"][0]
    section, rid = запись["section"], запись["record_id"]
    ответ = service.get(f"/api/records/{section}/{rid}/support")
    assert ответ.status_code == 200, ответ.text
    тело = ответ.json()
    свои = [c for c in doc["cells"]
            if c["section"] == section and c["record_id"] == rid]
    ячейки = тело.get("cells") if isinstance(тело, dict) else тело
    assert isinstance(ячейки, list), тело
    ключ = lambda c: (c.get("product"), c.get("kind"))      # noqa: E731
    assert sorted(ячейки, key=ключ) == sorted(свои, key=ключ), (
        "ручка и файл разошлись — у свидетельства завёлся второй носитель")


# ───────────────── дополнения сдачи K06 (не из заготовки)

def _cells_of(doc: dict, *, state: str | None = None,
              binding_state: str | None = None) -> list:
    out = list(doc.get("cells") or ())
    if state is not None:
        out = [c for c in out if c["state"] == state]
    if binding_state is not None:
        out = [c for c in out
               if str((c.get("binding") or {}).get("state")) == binding_state]
    return out


def test_the_witness_file_is_outside_the_fingerprint(service):
    """K06, контракт «0-бис» («вне отпечатка, как requests.json»):
    запись свидетельства не меняет ни отпечатка каталога, ни вердикта
    валидатора — иначе продукт, закреплённый на отпечатке, вставал бы от
    каждого пересчёта."""
    до = service.catalog()
    отпечаток, версия = до.fingerprint(), до.version()
    ошибки_до = [p for p in до.validate() if p.severity == "error"]
    assert service.post(REFRESH, json={}).status_code == 200
    после = service.catalog()
    assert после.fingerprint() == отпечаток, (
        "свидетельство вошло в отпечаток каталога")
    assert после.version() == версия
    assert [p.code for p in после.validate() if p.severity == "error"] == \
        [p.code for p in ошибки_до], "валидатор заметил посторонний файл"
    doc = _support_doc(service)
    assert после.support() == doc, (
        "читатель отдаёт не тот файл, который написала служба")
    assert doc["catalog"]["fingerprint"] == отпечаток


def test_a_catalog_without_the_witness_answers_none(make_service):
    """K06: свидетельства нет — читатель говорит `None`, а ручка
    «не вычислено», и НИКТО не считает это поддержкой (fail-closed)."""
    svc = make_service("owner")
    assert svc.catalog().support() is None, (
        "у каталога без файла свидетельство не может быть пустой таблицей: "
        "«ячейки нет» и «ячейка со словом нет» — разные ответы")
    rec = sample_family(svc.catalog())
    ответ = svc.get(f"/api/records/families/{rec['id']}/support")
    assert ответ.status_code == 200, ответ.text
    тело = ответ.json()
    assert тело["computed"] is False and тело["cells"] == []
    assert "не вычислено" in тело["words"], тело["words"]


def test_a_stand_of_the_kind_is_what_turns_a_row_into_support(service):
    """K06, контракт §4.2: «есть» = строка привязок И стенд ЭТОГО вида
    без провалов. Стенд подменяется перечнем — так видно, что решает
    именно он, а не форма строки."""
    cat = service.catalog()
    пути = {"COSMOS": service.settings.cosmos_bindings,
            "CDPM": service.settings.cdpm_bindings}
    без_стенда = tb.support_table(cat, пути, {"COSMOS": {}, "CDPM": {}})
    объявлено = _cells_of(без_стенда, binding_state="есть")
    assert объявлено, "в копии ни одной строки привязок «есть» — судить нечего"
    assert {c["state"] for c in объявлено} == {"не проверено"}, (
        "строка «есть» без стенда обязана давать «не проверено»")
    образец = объявлено[0]
    ключ = (образец["section"], образец["record_id"])
    зелёный = {образец["product"]: {ключ: [
        {"executor_kind": образец["kind"], "task": "st-проба",
         "result": "passed", "commit": "b" * 40, "at": "2026-09-12T10:00:00+02:00"}]}}
    со_стендом = tb.support_table(cat, пути, зелёный)
    ячейка = next(c for c in со_стендом["cells"]
                  if (c["section"], c["record_id"], c["kind"],
                      c["product"]) == (образец["section"],
                                        образец["record_id"],
                                        образец["kind"], образец["product"]))
    assert ячейка["state"] == "есть", ячейка
    assert ячейка["stand"]["tasks"] == ["st-проба"], ячейка["stand"]
    красный = {образец["product"]: {ключ: [
        {"executor_kind": образец["kind"], "task": "st-проба",
         "result": "failed", "commit": "b" * 40, "at": "2026-09-12T10:00:00+02:00"}]}}
    провал = tb.support_table(cat, пути, красный)
    ячейка = next(c for c in провал["cells"]
                  if (c["section"], c["record_id"], c["kind"],
                      c["product"]) == (образец["section"],
                                        образец["record_id"],
                                        образец["kind"], образец["product"]))
    assert ячейка["state"] == "не проверено", (
        f"провал стенда объявлен поддержкой: {ячейка}")
    print(f"ячеек в таблице: {len(без_стенда['cells'])}; со строкой «есть»: "
          f"{len(объявлено)}")


def test_bindings_written_for_another_catalog_are_not_checked(service):
    """K06, контракт §4.2: привязки продукта не для этого отпечатка
    (B01 не «match») дают «не проверено» — свидетельство на 0.5.0 не
    подтверждает исполнимость на 0.6.0."""
    cat = service.catalog()
    путь = Path(service.folder) / BINDINGS_FILE
    doc = json.loads(путь.read_text(encoding="utf-8"))
    doc["catalog_fingerprint"] = "0" * 64
    путь.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                               indent=1) + "\n", encoding="utf-8")
    таблица = tb.support_table(cat, {"COSMOS": путь,
                                     "CDPM": service.settings.cdpm_bindings})
    чужие = [c for c in таблица["cells"] if c["product"] == "COSMOS"
             and str((c.get("binding") or {}).get("state")) == "есть"]
    assert чужие, "строк «есть» у COSMOS в копии нет — судить нечего"
    assert {c["state"] for c in чужие} == {"не проверено"}, (
        "привязки чужого отпечатка сочтены подтверждением")
    assert {c["bindings_file"]["catalog_fingerprint"] for c in чужие} == \
        {"0" * 64}, "ячейка не назвала отпечаток файла, по которому судила"


def test_the_release_approves_the_table_and_keeps_a_copy_by_the_version(
        service, git_repo):
    """K06, контракт §4.2: утверждает владелец АКТОМ ВЫПУСКА — выпуск
    считает таблицу заново по выпущенному телу, заполняет `approved` у
    подтверждённых ячеек и кладёт неизменяемую копию рядом с версией."""
    git_repo(service.folder)
    ответ = service.post("/api/versions/release",
                         json={"version": "0.2.0", "note": "проба выпуска"})
    assert ответ.status_code == 200, ответ.text
    тело = ответ.json()
    assert тело["support"]["approved_by"] == "владелец", тело["support"]
    doc = _support_doc(service)
    assert doc["catalog"]["version"] == "0.2.0", (
        "свидетельство выпуска написано для черновика, а не для выпуска")
    assert doc["catalog"]["fingerprint"] == service.catalog().fingerprint()
    копия = json.loads(
        (service.folder / "releases" / "0.2.0" / SUPPORT_FILE)
        .read_text(encoding="utf-8"))
    assert копия == doc, "копия выпуска разошлась с текущим свидетельством"
    подтверждённые = _cells_of(doc, state="есть")
    for ячейка in doc["cells"]:
        если_есть = ячейка["state"] == "есть"
        assert (ячейка["approved"] is not None) is если_есть, (
            f"акт выпуска подписал непроверенную ячейку либо пропустил "
            f"проверенную: {ячейка}")
        if если_есть:
            assert ячейка["approved"]["by"] == "владелец"
            assert "выпуск 0.2.0" in ячейка["approved"]["how"], ячейка
    # Снимок выпуска читается тем же читателем и несёт то же свидетельство.
    снимок = tc.load(service.folder / "releases" / "0.2.0")
    assert снимок.support() == doc
    print(f"ячеек утверждено выпуском: {len(подтверждённые)} из "
          f"{len(doc['cells'])}")


# ───────── правка по неавторскому ревью (К1, К2, С3, С4, С6)

def _support_paths(svc) -> dict:
    return {"COSMOS": svc.settings.cosmos_bindings,
            "CDPM": svc.settings.cdpm_bindings}


def _witness_cell(svc, section: str, rid: str, kind: str,
                  product: str = "COSMOS") -> dict:
    """Ячейка свидетельства ОДНОЙ клетки — пересчётом читателя."""
    таблица = tb.support_table(svc.catalog(), _support_paths(svc))
    return next(c for c in таблица["cells"]
                if (c["section"], c["record_id"], c["kind"], c["product"])
                == (section, rid, kind, product))


def _record_kind(svc, *, witness: str, row: str | None = None) -> tuple:
    """Запись и вид, у которых ячейка свидетельства COSMOS в состоянии
    `witness` (а строка привязок, если названа, — в состоянии `row`).

    Ищется по живой копии, а не задаётся литералом: имена записей в
    копии меняются со всяким выпуском каталога, а закон — нет."""
    from catalog_service import executors                  # noqa: PLC0415
    cat = svc.catalog()
    таблица = tb.support_table(cat, _support_paths(svc))
    for ячейка in таблица["cells"]:
        if ячейка["product"] != "COSMOS" or ячейка["state"] != witness:
            continue
        if row is not None \
                and str((ячейка.get("binding") or {}).get("state")) != row:
            continue
        if ячейка["section"] != "families":
            continue
        клетки = executors.record_cells(cat, svc.settings, "families",
                                        ячейка["record_id"])
        if ячейка["kind"] not in клетки:
            continue
        return ячейка["record_id"], ячейка["kind"]
    return None, None


def _ask_kind_and_accept(svc, rid: str, kind: str) -> str:
    """Заявка на ВИД исполнителя существующей записи, принятая владельцем
    (состояние, из которого зовут `adopt`)."""
    ответ = svc.post("/api/requests",
                     json={"section": "families", "record_id": rid,
                           "source": {"kind": "вручную",
                                      "document_class": None,
                                      "run_class": None, "ref": None},
                           "draft": None,
                           "executor_kinds_requested": [kind]})
    assert ответ.status_code == 201, ответ.text
    номер = ответ.json()["request_id"]
    решение = svc.post(f"/api/requests/{номер}/decide",
                       json={"decision": "принята", "words": "нужен вид"})
    assert решение.status_code == 200, решение.text
    return номер


def test_a_binding_row_without_a_stand_does_not_let_the_adopt_through(
        service):
    """К1 (ревью c450d92e), контракт §3.7: «исполнимо» выводится ОДИНАКОВО
    у службы, CDPM и приёмника. Строка привязок «есть» без стендового
    подтверждения даёт ячейку «не проверено» — и перевод в записи обязан
    отказать, а не засчитать строку файла."""
    rid, kind = _record_kind(service, witness="не проверено", row="есть")
    assert rid, ("в копии нет клетки COSMOS со строкой «есть» без стенда — "
                 "судить К1 не на чем")
    ячейка = _witness_cell(service, "families", rid, kind)
    assert ячейка["state"] == "не проверено", ячейка
    номер = _ask_kind_and_accept(service, rid, kind)
    ответ = service.post(f"/api/requests/{номер}/adopt", json={})
    assert ответ.status_code == 412, (
        f"перевод прошёл по строке привязок без стенда "
        f"(HTTP {ответ.status_code}): у службы остался второй, слабый "
        f"закон «исполнимо» рядом со свидетельством")
    слова = detail(ответ)
    assert слова["code"] == "executor-not-present", слова
    extra = слова.get("extra") or {}
    assert kind in (extra.get("kinds") or []), extra
    # `extra.support` — ЯЧЕЙКИ СВИДЕТЕЛЬСТВА, а не строки привязок: поле
    # с именем support обязано нести свидетельство (замечание Н8).
    ячейки = extra.get("support") or []
    assert ячейки and all(set(c) >= set(CELL_KEYS) for c in ячейки), ячейки
    assert {c["state"] for c in ячейки} <= CELL_STATES, ячейки
    print(f"К1: запись {rid}, вид {kind}: строка «есть» без стенда → "
          f"ячейка «не проверено» → adopt {ответ.status_code}")


def test_bindings_of_another_catalog_do_not_make_a_record_executable(
        make_service):
    """К1, идентичность ТОЧНОЙ записи: файл привязок, написанный для
    ДРУГОГО каталога, исполнимости не даёт — ни свидетельству, ни
    переводу в записи (`matches_catalog` судит версию и отпечаток)."""
    svc = make_service("owner")
    rid, kind = _record_kind(svc, witness="есть")
    assert rid, "в копии нет подтверждённой ячейки — судить не на чем"
    путь = Path(svc.folder) / BINDINGS_FILE
    doc = json.loads(путь.read_text(encoding="utf-8"))
    doc["catalog_fingerprint"] = "0" * 64
    путь.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                               indent=1) + "\n", encoding="utf-8")
    ячейка = _witness_cell(svc, "families", rid, kind)
    assert ячейка["state"] == "не проверено", ячейка
    номер = _ask_kind_and_accept(svc, rid, kind)
    ответ = svc.post(f"/api/requests/{номер}/adopt", json={})
    assert ответ.status_code == 412, (
        f"перевод засчитал строку файла привязок ЧУЖОГО отпечатка "
        f"(HTTP {ответ.status_code})")
    assert detail(ответ)["code"] == "executor-not-present", detail(ответ)
    print(f"К1: чужой отпечаток привязок → ячейка «не проверено» → adopt "
          f"{ответ.status_code}")


def test_a_witness_confirmed_by_a_stand_unblocks_the_adopt(service):
    """К1, положительный контроль: подтверждённая свидетельством ячейка
    («есть» = строка привязок И стенд по виду на этом отпечатке) перевод
    ПРОПУСКАЕТ — отказ судит свидетельство, а не запрещает работу."""
    rid, kind = _record_kind(service, witness="есть")
    assert rid, "в копии нет подтверждённой ячейки — судить не на чем"
    номер = _ask_kind_and_accept(service, rid, kind)
    ответ = service.post(f"/api/requests/{номер}/adopt", json={})
    assert ответ.status_code == 200, (
        f"перевод отказал при подтверждённой поддержке "
        f"(HTTP {ответ.status_code}): {ответ.text[:200]}")
    тело = ответ.json()
    assert тело["draft_written"] is True and тело["executors_missing"] == []
    print(f"К1: подтверждённая ячейка {rid} × {kind} → adopt 200 без confirm")


def test_a_witness_of_an_unknown_schema_version_is_not_a_witness(service):
    """К2: читатель судит ВЕРСИЮ СХЕМЫ свидетельства закрытым перечнем.
    Файл `support/9` того же отпечатка — не «годное свидетельство», а
    отказ словами (общий закон 5 П0: неизвестная мажорная версия —
    отказ)."""
    assert service.post(REFRESH, json={}).status_code == 200
    файл = service.folder / SUPPORT_FILE
    doc = json.loads(файл.read_text(encoding="utf-8"))
    doc["schema_version"] = "support/9"
    файл.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                               indent=1) + "\n", encoding="utf-8")
    rid = doc["cells"][0]["record_id"]
    section = doc["cells"][0]["section"]
    with pytest.raises(tc.CatalogFormatError) as сорвалось:
        service.catalog().support()
    assert "support/9" in str(сорвалось.value), сорвалось.value
    ответ = service.get(f"/api/records/{section}/{rid}/support")
    assert ответ.status_code == 503, (
        f"свидетельство неизвестной версии схемы отдано как годное "
        f"(HTTP {ответ.status_code}): {ответ.text[:200]}")
    слова = detail(ответ)
    assert "неизвестной версии схемы" in слова["words"], слова
    print(f"К2: support/9 → HTTP {ответ.status_code} {слова['code']}")


def test_one_reader_function_judges_the_witness_goodness(service):
    """С3 и Н2: годность свидетельства судит ОДНА функция читателя
    (`bindings.support_of`) — версия схемы, версия каталога и отпечаток;
    её зовут ручка службы, дверь COSMOS и CDPM, а не три своих сверки."""
    assert service.post(REFRESH, json={}).status_code == 200
    cat = service.catalog()
    doc = _support_doc(service)
    годно = tb.support_of(cat, doc)
    assert годно == {"valid": True, "why": None, "cells": doc["cells"]}, \
        годно.get("why")
    нет_файла = tb.support_of(cat, None)
    assert нет_файла["valid"] is False and нет_файла["cells"] == []
    assert "не вычислено" in нет_файла["why"], нет_файла["why"]
    for поле, значение, слово in (("fingerprint", "0" * 64, "написано для"),
                                  ("version", "9.9.9", "написано для")):
        чужой = json.loads(json.dumps(doc))
        чужой["catalog"][поле] = значение
        суд = tb.support_of(cat, чужой)
        assert суд["valid"] is False, f"{поле}: {суд}"
        assert слово in суд["why"], суд["why"]
        assert суд["cells"] == doc["cells"], (
            "негодное свидетельство обязано ПОКАЗЫВАТЬ ячейки: человеку "
            "нужно видеть, что записано и почему не считается")
    чужая_схема = json.loads(json.dumps(doc))
    чужая_схема["schema_version"] = "support/9"
    суд = tb.support_of(cat, чужая_схема)
    assert суд["valid"] is False and "версии схемы" in суд["why"], суд
    # Ручка судит тем же законом: свидетельство чужого отпечатка —
    # `stale: true` со словами, а не «вычислено и годно».
    файл = service.folder / SUPPORT_FILE
    подмена = json.loads(json.dumps(doc))
    подмена["catalog"]["fingerprint"] = "0" * 64
    файл.write_text(json.dumps(подмена, ensure_ascii=False, sort_keys=True,
                               indent=1) + "\n", encoding="utf-8")
    ячейка = doc["cells"][0]
    ответ = service.get(f"/api/records/{ячейка['section']}/"
                        f"{ячейка['record_id']}/support")
    assert ответ.status_code == 200, ответ.text
    тело = ответ.json()
    assert тело["stale"] is True and тело["why"], тело
    assert "исполнимостью не считаются" in тело["words"], тело["words"]


def test_the_support_handle_judges_the_record_identity(service):
    """С4: ручка свидетельства судит запись, как остальные ручки записи —
    чужой id 404 `unknown-record`, а не «вычислено, ячеек нет». Опечатка
    в имени не должна выглядеть пустой поддержкой."""
    ответ = service.get("/api/records/families/net_takoj_zapisi_w17b/support")
    assert ответ.status_code == 404, (
        f"ручка ответила {ответ.status_code} на несуществующую запись: "
        f"{ответ.text[:200]}")
    assert detail(ответ)["code"] == "unknown-record", detail(ответ)
    # Положительный контроль: у настоящей записи ручка отвечает 200.
    rec = sample_family(service.catalog())
    assert service.get(f"/api/records/families/{rec['id']}/support") \
        .status_code == 200


def test_the_witness_file_and_the_release_copy_pass_their_schema(service,
                                                                 git_repo):
    """С6: схема `support/1` ПРИМЕНЯЕТСЯ, а не лежит рядом. Файл службы
    после пересчёта и неизменяемая копия выпуска судятся ею; носитель
    схемы у читателя, у службы — байт-в-байт копия (как у квитанции)."""
    from jsonschema import validators                       # noqa: PLC0415

    носитель = ROOT / "template_catalog" / "schemas" / "support.schema.json"
    копия = ROOT / "catalog_service" / "schemas" / "support.schema.json"
    assert носитель.is_file() and копия.is_file()
    assert носитель.read_bytes() == копия.read_bytes(), (
        "копия схемы свидетельства у службы разошлась с носителем читателя")
    схема = json.loads(носитель.read_text(encoding="utf-8"))
    класс = validators.validator_for(схема)
    класс.check_schema(схема)
    проверка = класс(схема)

    def беды(doc, где: str) -> list:
        return [f"{где}/{'/'.join(str(x) for x in e.path)}: {e.message}"
                for e in проверка.iter_errors(doc)]

    assert service.post(REFRESH, json={}).status_code == 200
    после_пересчёта = _support_doc(service)
    assert not беды(после_пересчёта, "файл службы"), \
        беды(после_пересчёта, "файл службы")
    git_repo(service.folder)
    выпуск = service.post("/api/versions/release",
                          json={"version": "0.2.0", "note": "проба схемы"})
    assert выпуск.status_code == 200, выпуск.text
    for путь in (service.folder / SUPPORT_FILE,
                 service.folder / "releases" / "0.2.0" / SUPPORT_FILE):
        doc = json.loads(путь.read_text(encoding="utf-8"))
        assert not беды(doc, путь.name), беды(doc, путь.name)
    print(f"схемой судимо: файл службы и копия выпуска "
          f"({len(после_пересчёта['cells'])} ячеек)")
