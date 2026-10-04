# -*- coding: utf-8 -*-
"""Правила читателя, введённые шагом 4 каталога шаблонов (зона COSMOS).

Что доказывается:
  * шапка привязок несёт необязательное поле executor_kinds — ЗОНУ
    ОТВЕТСТВЕННОСТИ продукта; вид вне словаря — ошибка B01;
  * B04 судится по зонам: «есть» у одного продукта закрывает вид; явная
    строка у каждого ответственного закрывает; ответственный без строки —
    ошибка с перечнем ВСЕХ продуктов; вид без «есть» и ни в чьей зоне —
    ошибка «никем не исполняется и ни в чьей зоне»;
  * зона без поля выводится из строк файла по ВСЕМ состояниям (продукт с
    одними «нет» — в зоне);
  * состояние клетки продукта — лучшее из его строк, но только в таблице
    B04: executor_states() не меняется;
  * стоп сторожа защищает ВЫПУСКИ: младшая разница на 0.x между двумя
    ЧЕРНОВИКАМИ — предупреждение, а не остановка;
  * словарь synthetic_codes.dataset_ids (необязательный) судится схемой и
    D02; форма мира tables судится A03 по ячейкам; ключи формы schedule
    названы словарём и судятся A03;
  * X07 при включённом гейте gates.stand_tasks_required — ошибка.

Файлы правил: template_catalog/bindings.py, manifest.py, validate.py,
schemas/catalog.schema.json.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import template_catalog as tc                                      # noqa: E402
import catalog_copies as cc                                        # noqa: E402
from template_catalog import bindings as tb                        # noqa: E402
from template_catalog import manifest                              # noqa: E402
from template_catalog.canon import fingerprint_of                  # noqa: E402

CATALOG = ROOT / "catalog"
FILES = ("catalog.json", "families.json", "criteria.json",
         "dictionaries.json", "requests.json", "versions.json")

#: Дельта шага 4 — внешний свидетель прибавок (её пишет инструмент из
#: посева). Числа задач берутся ИЗ НЕЁ, а не пишутся литералом: литерал
#: пришлось бы править вручную при каждой новой задаче, и он ничего не
#: доказывал бы о том, что задачи каталога — те самые, что объявила
#: дельта. Файла нет (каталог шага 1) — счёт не судим.
DELTA_FILE = CATALOG / "transition" / "step4_additions.json"
DELTA = (json.loads(DELTA_FILE.read_text(encoding="utf-8"))
         if DELTA_FILE.is_file() else None)


@pytest.fixture(scope="module")
def cat():
    return tc.load(CATALOG)


# ------------------------------------------------------------ помощники
def _copy(tmp_path, mutate, *, name="cat"):
    """Копия каталога с правкой разделов и пересчитанным отпечатком
    (иначе любая правка доказывала бы H02, а не проверяемое правило).

    Копия — ЧЕРНОВИК: журнал версий и папки выпусков живого каталога не
    переносятся, иначе пересчитанный отпечаток разошёлся бы с записью
    журнала (H03) вместо проверяемого правила."""
    dst = Path(tmp_path) / name
    if dst.exists():
        cc.rmtree_writable(dst)
    shutil.copytree(CATALOG, dst,
                    ignore=shutil.ignore_patterns("tools", "schema",
                                                  "__pycache__", "releases"))
    docs = {n: json.loads((dst / n).read_text(encoding="utf-8")) for n in FILES}
    docs["versions.json"] = {"form_version": 1, "versions": []}
    mutate(docs)
    body = {"families": docs["families.json"].get("families", []),
            "criteria": docs["criteria.json"].get("criteria", []),
            "dictionaries": {k: v for k, v in docs["dictionaries.json"].items()
                             if k != "form_version"}}
    try:
        docs["catalog.json"]["fingerprint"] = fingerprint_of(body)
    except ValueError:
        pass
    for n, doc in docs.items():
        (dst / n).write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return dst


def _fam(docs, fid):
    return next(f for f in docs["families.json"]["families"] if f["id"] == fid)


def _crit(docs, cid):
    return next(c for c in docs["criteria.json"]["criteria"] if c["id"] == cid)


def _ex(kind, state="нет", **extra):
    row = {"kind": kind, "state": state, "lang": None, "file": None,
           "symbol": None, "note": "исполнитель другого продукта",
           "cr_key": None}
    if state == "есть":
        row.update(lang="python", file="vmp/x.py", symbol="Builder.price",
                   note=None)
    if state == "заявлен":
        row["cr_key"] = "CR-1"
    row.update(extra)
    return row


def _doc(cat, product="COSMOS", *, zone=None, rows=None):
    doc = {"product": product, "product_version": "1.18.5",
           "catalog_version": cat.version(),
           "catalog_fingerprint": cat.fingerprint(),
           "generated_at": "2026-09-08T12:00:00+03:00",
           "generated_from": "a" * 40,
           "bindings": rows if rows is not None else []}
    if zone is not None:
        doc["executor_kinds"] = list(zone)
    return doc


def _all_absent(cat, product="COSMOS", zone=None):
    """Файл продукта: по строке «нет» на каждый объявленный вид каждой
    записи — законный минимум, закрывающий B04 в своей зоне."""
    rows = []
    for rec in cat.families_full():
        rows.append({"section": "families", "id": rec["id"],
                     "stand_verified": [],
                     "executors": [_ex(k) for k in rec["executor_kinds"]]})
    for rec in cat.criteria().values():
        rows.append({"section": "criteria", "id": rec["id"],
                     "stand_verified": [],
                     "executors": [_ex(k) for k in rec["computable_by"]]})
    return _doc(cat, product, zone=zone, rows=rows)


def _row(doc, rid):
    return next(r for r in doc["bindings"] if r["id"] == rid)


def _only_kind(doc, kind):
    """Файл продукта узкой зоны: строки только своего вида."""
    for r in doc["bindings"]:
        r["executors"] = [e for e in r["executors"] if e["kind"] == kind]
    doc["bindings"] = [r for r in doc["bindings"] if r["executors"]]
    return doc


def _codes(problems, code=None, severity=None):
    return [p for p in problems
            if (code is None or p.code == code)
            and (severity is None or p.severity == severity)]


# -------------------------------------------------- B01: зона в шапке
def test_b01_executor_kinds_header_must_be_from_dictionary(cat):
    known = cat.dictionaries()["executor_kinds"]
    ok = _all_absent(cat, zone=known)
    assert not _codes(tb.check_bindings(cat, ok), "B01", "error")
    bad = _all_absent(cat, zone=list(known) + ["kotlin_telepathy"])
    errs = _codes(tb.check_bindings(cat, bad), "B01", "error")
    assert len(errs) == 1
    assert "kotlin_telepathy" in errs[0].message
    assert "вне словаря" in errs[0].message
    assert errs[0].where.endswith(".executor_kinds")


def test_zone_of_reads_header_or_rows(cat):
    """Зона — поле шапки; без поля — виды строк по ВСЕМ состояниям, иначе
    продукт с одними «нет» оказался бы вне зоны."""
    doc = _all_absent(cat)
    assert "executor_kinds" not in doc
    zone = tb.zone_of(doc)
    assert "check_template" in zone and "kotlin_knob" in zone
    assert all(ex["state"] == "нет" for r in doc["bindings"]
               for ex in r["executors"])
    narrow = _all_absent(cat, zone=["check_template"])
    assert tb.zone_of(narrow) == {"check_template"}
    assert tb.zone_of({}) == set()


# ------------------------------------------------------ B04 по зонам
def test_b04_present_at_one_product_closes_kind(cat):
    """Ветвь (а): «есть» хотя бы у одного продукта закрывает вид — второй
    продукт по нему ничего не должен."""
    cosmos = _all_absent(cat, zone=cat.dictionaries()["executor_kinds"])
    cdpm = _only_kind(_all_absent(cat, "CDPM", zone=["check_template"]),
                      "check_template")
    _row(cdpm, "duty_limits")["executors"] = [
        _ex("check_template", "есть",
            template="максимальная-длительность-часов",
            file="cdpm/data_checks.py", symbol="run_spec_ex")]
    probs = tb.check_bindings(cat, cosmos, cdpm)
    assert not _codes(probs, "B04"), "\n".join(map(str, _codes(probs, "B04")))
    # клетка закрыта именно «есть» CDPM: у COSMOS по ней «нет»
    assert tb.executor_states(cat, cosmos, cdpm)["families"]["duty_limits"] \
        ["check_template"] == {"COSMOS": "нет", "CDPM": "есть"}


def test_b04_explicit_absent_rows_close_kind(cat):
    """Ветвь (в): каждый ответственный сказал словами «нет» — вид закрыт,
    хотя исполнителя нет ни у кого."""
    cosmos = _all_absent(cat, zone=cat.dictionaries()["executor_kinds"])
    cdpm = _only_kind(_all_absent(cat, "CDPM", zone=["check_template"]),
                      "check_template")
    probs = tb.check_bindings(cat, cosmos, cdpm)
    assert not _codes(probs, "B04"), "\n".join(map(str, _codes(probs, "B04")))


def test_b04_responsible_without_row_is_error_naming_all_products(cat):
    """Ветвь (г): вид в зоне CDPM, строки у неё нет — ошибка словами, и в
    ней названы ВСЕ продукты: состояние либо «в зоне, но не объявил»."""
    cosmos = _all_absent(cat, zone=cat.dictionaries()["executor_kinds"])
    cdpm = _doc(cat, "CDPM", zone=["check_template"], rows=[])
    third = _doc(cat, "третий", zone=["builder_method"], rows=[])
    errs = _codes(tb.check_bindings(cat, cosmos, cdpm, third), "B04", "error")
    assert errs, "молчание вместо ошибки B04"
    ct = [p for p in errs if p.where.endswith("[check_template]")]
    assert ct
    msg = ct[0].message
    assert "COSMOS — нет" in msg and "исполнитель другого продукта" in msg
    assert "CDPM — в зоне, но не объявил" in msg
    # продукт вне зоны ничего не должен — так и сказано
    assert "третий — вне зоны" in msg
    # ошибка возникает у КАЖДОГО вида, за который отвечает продукт без
    # строки: у builder_method ответственный — третий продукт
    bm = [p for p in errs if p.where.endswith("[builder_method]")]
    assert bm and "третий — в зоне, но не объявил" in bm[0].message
    # прочие виды закрыты строками COSMOS: ошибок по ним нет
    assert {p.where.rsplit("[", 1)[1] for p in errs} == \
        {"check_template]", "builder_method]"}


def test_b04_kind_outside_all_zones_is_error(cat):
    """Ветвь «ни в чьей зоне»: вид объявлен записью, исполнителя нет, и ни
    один переданный продукт за него не отвечает — ОШИБКА, не молчание."""
    narrow = _only_kind(_all_absent(cat, zone=["check_template"]),
                        "check_template")
    errs = _codes(tb.check_bindings(cat, narrow), "B04", "error")
    assert errs
    msgs = [p.message for p in errs]
    assert any("никем не исполняется и ни в чьей зоне" in m for m in msgs)
    assert any("зоны: COSMOS — [check_template]" in m for m in msgs)


def test_b04_single_cosmos_file_closes_everything(cat):
    """Один файл COSMOS без cdpm/bindings.json — ноль проблем B04: зона
    COSMOS — все десять видов, ответственный есть всегда."""
    cosmos = _all_absent(cat, zone=cat.dictionaries()["executor_kinds"])
    probs = tb.check_bindings(cat, cosmos)
    assert not _codes(probs, "B04"), "\n".join(map(str, _codes(probs, "B04")))
    assert not _codes(probs, severity="error"), \
        "\n".join(map(str, _codes(probs, severity="error")))


def test_b04_best_state_of_two_rows_and_executor_states_untouched(cat):
    """У одного вида записи законны две строки одного продукта («есть»
    ручки и «нет» ручки-синонима). Клетку B04 закрывает ЛУЧШЕЕ состояние,
    а executor_states() не меняется — у него побеждает последняя строка
    (его единственный потребитель ждёт именно этого)."""
    cosmos = _all_absent(cat, zone=cat.dictionaries()["executor_kinds"])
    row = _row(cosmos, "duty_extension_penalty")
    row["executors"] = [e for e in row["executors"]
                        if e["kind"] != "kotlin_knob"]
    row["executors"] += [
        _ex("kotlin_knob", "есть", lang="kotlin",
            file="kotlin-solver/RunThresholds.kt",
            symbol="RunThresholds.applyDutyExtension",
            param="duty_extension_cap", threshold="max_duty_span_min",
            unit="duration_minutes", relation="max"),
        _ex("kotlin_knob", "нет", param="long_duty_threshold",
            note="ручки решателя нет")]
    probs = tb.check_bindings(cat, cosmos)
    assert not _codes(probs, "B04")
    assert not _codes(probs, severity="error")
    table = tb.executor_states(cat, cosmos)
    assert table["families"]["duty_extension_penalty"]["kotlin_knob"] == \
        {"COSMOS": "нет"}, "executor_states() правился — так нельзя"


# ------------------------------------------ B01: сторож версий каталога
def test_is_stop_defends_releases_not_drafts():
    cv = manifest.compare_versions
    # два черновика, младшая разница на 0.x — НЕ стоп
    assert not manifest.is_stop(cv("0.1.0-draft.3", "0.2.0-draft.1"))
    # черновик против выпуска на 0.x — стоп
    assert manifest.is_stop(cv("0.1.0", "0.2.0-draft.1"))
    assert manifest.is_stop(cv("0.1.0-draft.3", "0.2.0"))
    # патч — не стоп
    assert not manifest.is_stop(cv("0.2.1", "0.2.0"))
    # старшая разница — стоп всегда
    assert manifest.is_stop(cv("1.0.0", "2.0.0"))
    assert manifest.is_stop(cv("0.9.0", "1.0.0"))
    # младшая на 1.x — не стоп
    assert not manifest.is_stop(cv("1.2.0", "1.1.0"))
    # равные тройки — не стоп (отпечаток судится отдельно)
    assert not manifest.is_stop(cv("0.2.0-draft.1", "0.2.0"))


def test_manifest_check_words_for_two_drafts():
    b = {"catalog_version": "0.2.0-draft.1", "catalog_fingerprint": "1" * 64}
    p = manifest.check_manifest_catalog(
        {"version": "0.1.0-draft.3", "fingerprint": "0" * 64}, b)
    assert p.severity == "warning"
    assert "между двумя черновиками" in p.message
    assert "000000000000" in p.message and "111111111111" in p.message
    assert "выпуск любой из сторон сделал бы это остановкой" in p.message
    # выпуск с одной стороны — остановка
    p = manifest.check_manifest_catalog(
        {"version": "0.1.0", "fingerprint": "0" * 64}, b)
    assert p.severity == "error" and "расчёт не начинается" in p.message
    # равные выпуски с разными данными — остановка
    b2 = {"catalog_version": "1.0.0", "catalog_fingerprint": "1" * 64}
    p = manifest.check_manifest_catalog(
        {"version": "1.0.0", "fingerprint": "0" * 64}, b2)
    assert p.severity == "error" and "разные данные" in p.message


def test_b01_of_check_bindings_uses_the_same_law(cat, tmp_path):
    # Ветвь «два черновика» судится по каталогу-ЧЕРНОВИКУ: живой каталог
    # с 08.09 — выпуск 0.2.0, и младшая разница на 0.x у него уже
    # остановка («стоп защищает выпуски»). Тело копии не правится —
    # меняется только номер версии в шапке.
    def _черновиком(d):
        d["catalog.json"]["version"] = "0.2.0-draft.9"
    draft = tc.load(_copy(tmp_path, _черновиком, name="b01_draft"))
    doc = _all_absent(draft, zone=draft.dictionaries()["executor_kinds"])
    doc["catalog_version"] = "0.1.0-draft.3"
    doc["catalog_fingerprint"] = "0" * 64
    probs = tb.check_bindings(draft, doc)
    warns = _codes(probs, "B01", "warning")
    assert warns and "между двумя черновиками" in warns[0].message
    assert not _codes(probs, "B01", "error")
    doc["catalog_version"] = "0.1.0"
    assert _codes(tb.check_bindings(draft, doc), "B01", "error")
    # Каталог-ВЫПУСК: та же младшая разница на 0.x уже остановка. Номер
    # выпуска берётся копией, а не живым каталогом: в волне выпуска
    # живой каталог сам черновик, и ветвь «стоп защищает выпуски»
    # осталась бы непроверенной.
    def _выпуском(d):
        d["catalog.json"]["version"] = "0.9.0"
    released = tc.load(_copy(tmp_path, _выпуском, name="b01_release"))
    live = _all_absent(released,
                       zone=released.dictionaries()["executor_kinds"])
    live["catalog_version"] = "0.1.0-draft.3"
    live["catalog_fingerprint"] = "0" * 64
    assert _codes(tb.check_bindings(released, live), "B01", "error")


# ------------------------------------------- словарь dataset_ids (D02)
@pytest.mark.parametrize("value", [["S0"], ["X1"], ["S1", "S1"], []])
def test_dataset_ids_outside_pattern_is_error(tmp_path, value):
    def m(d):
        d["dictionaries.json"]["synthetic_codes"]["dataset_ids"] = value
    problems = tc.validate_path(_copy(tmp_path, m, name=f"c{len(value)}{value}"))
    errs = [p for p in problems if p.severity == "error"]
    assert errs, f"порча {value} не замечена"
    assert any("dataset_ids" in p.where for p in errs)


def test_dataset_ids_is_optional(tmp_path):
    def m(d):
        d["dictionaries.json"]["synthetic_codes"].pop("dataset_ids")
        # без словаря идентификаторов задачи формы tables не пишем
        for rec in d["families.json"]["families"]:
            rec["stand_tasks"] = [t for t in rec["stand_tasks"]
                                  if t["world"].get("form") != "tables"]
    problems = tc.validate_path(_copy(tmp_path, m, name="no_ids"))
    assert [str(p) for p in problems if p.severity == "error"] == []


def test_tables_task_without_dataset_ids_is_judged_by_words(tmp_path):
    """Поле необязательно, но МОЛЧАНИЯ нет: мир формы tables при словаре
    без dataset_ids судить нечем, и это говорится словами. Прежняя ветвь
    просто пропускала идентификатор книги — любой набор (в том числе
    авиакомпании) проходил зелёным."""
    def m(d):
        d["dictionaries.json"]["synthetic_codes"].pop("dataset_ids")
        _fam(d, "days_off_floor")["stand_tasks"].append({
            "id": "st-tables-9", "world": copy.deepcopy(TABLES_WORLD),
            "expected": {"check_code": {"symbol": "_days_off",
                                        "findings": 0}},
            "note": "книга набора при словаре без идентификаторов"})
    problems = tc.validate_path(_copy(tmp_path, m, name="tbl_noids"))
    a03 = [p for p in problems if p.code == "A03"]
    assert a03, "\n".join(map(str, problems))
    assert any("dataset_ids" in p.message and "судить нечем" in p.message
               for p in a03), "\n".join(map(str, a03))


# ------------------------------------------------- A03: миры и коды
def _first_schedule_task(docs):
    fam = _fam(docs, "layover_penalty")
    return fam["stand_tasks"][0]


def test_a03_schedule_foreign_codes(tmp_path):
    """Ключи мира `schedule` — и обязательные, и введённые шагом 4
    (допуски, назначения, история, посуточный календарь): данные
    авиакомпании не проходят зелёными ни в одном из них."""
    def m(d):
        t = _first_schedule_task(d)
        w = t["world"]
        w["flights"][0]["from"] = "SVO"
        w["crews"][0]["base"] = "DME"
        w["crews"][0]["id"] = "12345"
        w["flights"][0]["fleet_type"] = "A320"
        w["crews"][0]["ratings"] = ["A320"]
        w["assignments"] = [{"crew": "54321", "flight": "F1", "seat": 0}]
        w["history"] = [{"crew": "54322", "month_days_worked": 0}]
        w["history_calendar"] = [{"employee": "54323", "date": "2026-10-01",
                                  "kind": "выходной"}]
    problems = tc.validate_path(_copy(tmp_path, m, name="a03s"))
    a03 = [p for p in problems if p.code == "A03"]
    assert len(a03) == 8, "\n".join(map(str, problems))
    for part in (".flights[0].from", ".crews[0].base", ".crews[0].id",
                 ".flights[0].fleet_type", ".crews[0].ratings[0]",
                 ".assignments[0].crew", ".history[0].crew",
                 ".history_calendar[0].employee"):
        assert any(part in p.where for p in a03), part


TABLES_WORLD = {
    "form": "tables",
    "tables": {"S1/Экипажи": {
        "columns": ["Табельный номер", "База приписки"],
        "rows": [{"Табельный номер": "P1", "База приписки": "ST1"}]}}}


def test_tables_world_form_is_green(tmp_path):
    def m(d):
        fam = _fam(d, "days_off_floor")
        fam["stand_tasks"].append({
            "id": "st-tables-1", "world": copy.deepcopy(TABLES_WORLD),
            "expected": {"check_template": {
                "template": "выходных-в-месяце",
                "params": {"sheet": "Экипажи"}, "findings": 0}},
            "note": "проверка данных линии CDPM на таблицах набора"})
    problems = tc.validate_path(_copy(tmp_path, m, name="tbl_ok"))
    assert [str(p) for p in problems if p.severity == "error"] == []


@pytest.mark.parametrize("patch,where_part", [
    ({"S0/Экипажи": None}, "tables[S0/Экипажи]"),
    ({"Экипажи": None}, "tables[Экипажи]"),
])
def test_tables_world_bad_key(tmp_path, patch, where_part):
    key = next(iter(patch))

    def m(d):
        world = copy.deepcopy(TABLES_WORLD)
        world["tables"] = {key: world["tables"]["S1/Экипажи"]}
        _fam(d, "days_off_floor")["stand_tasks"].append({
            "id": "st-tables-2", "world": world,
            "expected": {"check_code": {"symbol": "_days_off", "findings": 0}},
            "note": "ключ книги не по образцу"})
    problems = tc.validate_path(_copy(tmp_path, m, name="tbl" + key[:2]))
    a03 = [p for p in problems if p.code == "A03"]
    assert a03 and any(where_part in p.where for p in a03), \
        "\n".join(map(str, problems))


def test_tables_world_bad_cells(tmp_path):
    def m(d):
        world = copy.deepcopy(TABLES_WORLD)
        world["tables"]["S1/Экипажи"]["columns"].append("Тип ВС")
        world["tables"]["S1/Экипажи"]["rows"] = [
            {"Табельный номер": "007", "База приписки": "SVO",
             "Тип ВС": "A320"}]
        _fam(d, "days_off_floor")["stand_tasks"].append({
            "id": "st-tables-3", "world": world,
            "expected": {"check_code": {"symbol": "_days_off", "findings": 0}},
            "note": "ячейки книги не синтетические"})
    problems = tc.validate_path(_copy(tmp_path, m, name="tblcell"))
    a03 = [p for p in problems if p.code == "A03"]
    assert len(a03) == 3, "\n".join(map(str, problems))
    assert any("Табельный номер" in p.where and "табельный" in p.message
               for p in a03)
    assert any("База приписки" in p.where and "не синтетический" in p.message
               for p in a03)
    assert any("Тип ВС" in p.where and "тип судна" in p.message for p in a03)


def test_schedule_optional_keys_are_allowed(tmp_path):
    """Необязательные ключи мира schedule (назначения, перегон, желаемые
    выходные, стаж, история, форма правила, условия, критерий, ручки
    стенда) законны: X05 судит только обязательные поля формы."""
    def m(d):
        t = _first_schedule_task(d)
        w = t["world"]
        w["assignments"] = [{"crew": "P1", "flight": "F1", "seat": 0}]
        w["flights"][0]["deadhead"] = True
        w["crews"][0]["preferred_days_off"] = ["2026-10-01"]
        w["crews"][0]["seniority"] = 500
        w["history"] = [{"crew": "P1", "month_days_worked": 0}]
        w["rule_form"] = {"days": ["thursday"]}
        w["conditions"] = {"extension_bases": ["ST1"]}
        w["criterion"] = {"kind": "min_person_days", "rate_per_duty": 7}
        w["stand"] = {"w_night": 1}
    problems = tc.validate_path(_copy(tmp_path, m, name="optkeys"))
    assert [str(p) for p in problems if p.severity == "error"] == []


def test_world_form_unknown_is_x05(tmp_path):
    def m(d):
        _first_schedule_task(d)["world"] = {"form": "телепатия"}
    problems = tc.validate_path(_copy(tmp_path, m, name="badform"))
    assert any(p.code == "X05" and "форма мира" in p.message
               for p in problems)


# ---------------------------------------------------- X07 при гейте
def test_x07_is_error_when_gate_is_on(tmp_path):
    def m(d):
        d["catalog.json"]["gates"]["stand_tasks_required"] = True
        _fam(d, "duty_limits")["stand_tasks"] = []
        _crit(d, "total_pairing_cost")["stand_tasks"] = []
    problems = tc.validate_path(_copy(tmp_path, m, name="gate_on"))
    x07 = [p for p in problems if p.code == "X07"]
    assert len(x07) == 2 and all(p.severity == "error" for p in x07)


def test_live_catalog_has_stand_tasks_everywhere(cat):
    """Полноту стендовых задач требует ДЕРЕВО, а не флаг: флаг остаётся
    выключенным до 1.0.0 (иначе пробные записи помощника копий и записи
    из заявок обрушили бы оба продукта на первой операции)."""
    assert cat.header["gates"]["stand_tasks_required"] is False
    records = cat.families_full() + list(cat.criteria().values())
    without = [r["id"] for r in records if not r["stand_tasks"]]
    assert without == [], f"записи без стендовых задач: {without}"
    ids = [t["id"] for r in records for t in r["stand_tasks"]]
    assert len(set(ids)) == len(ids)
    if DELTA is not None:
        want = sorted(tid for lst in DELTA["stand_tasks"].values()
                      for tid in lst)
        assert sorted(ids) == want, \
            "задачи каталога и перечень дельты разошлись"
