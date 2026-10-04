# -*- coding: utf-8 -*-
"""Ревизия 1.18 (план единой версии 06.09.2026, §3.8: C-27b, K-1):
объявленный горизонт manifest.horizon из «Свойств» и свидетельства
присутствия сотрудника (лист «Присутствие» → location_evidence /
location_intervals / conflicts у записей history.employees[]).

Охраняемое: end_exclusive = end_inclusive + 1 минута считается один раз
производителем; без ключей раздела нет, а отчёт данных показывает
«горизонт не объявлен»; нечитаемые ключи — находка; границы не на сутках
— показание. Присутствие: объединение по паре «станция + вид» с
провенансом всех строк, смежные сливаются, разные станции в одно время —
конфликт без интервала, факт позже среза — дефект, объявленное в
горизонте — допустимо, вид сохраняется при наложении видов, станция вне
сети — находка, сотрудник без строк — ключей нет, with_history не
меняется; SPEC_VERSION 1.18 и гейт пакета пересчитывает горизонт."""
from __future__ import annotations

import io
import json
import zipfile

from cdpm import data_checks as dc
from cdpm import data_report as dr
from cdpm import export_clean_data as clean_mod
from cdpm import export_task_package as tp
from cdpm import history_section as hs
from cdpm import model as m
from cdpm.gates import gate_task_package as gate

PRESENCE_COLS = ["Табельный номер", "Начало", "Конец", "Станция", "Вид"]
HORIZON_PROPS = [{"ключ": "planSince", "значение": "2026-09-04T00:00"},
                 {"ключ": "planUntil", "значение": "2026-09-10T23:59"}]


def _p(eid, start, end, station, kind="факт"):
    return {"Табельный номер": eid, "Начало": start, "Конец": end,
            "Станция": station, "Вид": kind}


def _book(presence=None, history=None, props=None, crew=("H01", "H02", "H03")):
    tables = {
        "Экипажи": {"columns": ["Табельный номер", "База"],
                    "rows": [{"Табельный номер": e, "База": "PLY"} for e in crew]},
        "Рейсы": {"columns": ["Номер рейса", "Дата", "Вылет", "Прилёт"],
                  "rows": [{"Номер рейса": "PL101", "Дата": "05.09.2026",
                            "Вылет": "PLY", "Прилёт": "SVG"}]},
        "Свойства": {"columns": ["ключ", "значение"],
                     "rows": HORIZON_PROPS if props is None else props},
    }
    if presence is not None:
        tables["Присутствие"] = {"columns": PRESENCE_COLS, "rows": presence}
    if history is not None:
        tables["История"] = {
            "columns": ["Табельный номер", "Последняя смена: конец",
                        "Последняя смена: станция"],
            "rows": history}
    return {"customer": "Тест",
            "sources": [{"sid": "D1", "file": "book.xlsx", "sha256": "a" * 64,
                         "rows_seen": 9}],
            "tables": {"D1": tables}}


def _report(ds):
    return dc.build_data_report("Тест", ds, dc.builtin_checks(ds))


def _check(rep, template):
    return next(c for c in rep["checks"] if c.get("template") == template)


def _findings(rep, template):
    ids = {c["id"] for c in rep["checks"] if c.get("template") == template}
    return [f for f in rep["findings"] if f["check"] in ids]


def _prov(row):
    return {"sid": "D1", "sheet": "Присутствие", "row": row}


def _emp(sec, eid):
    return next(e for e in sec["employees"] if e["employee_id"] == eid)


# ── горизонт (1.18.1) ──

def test_horizon_from_properties_end_exclusive_is_plus_one_minute():
    h = hs.declared_horizon(_book(), tz="Europe/Moscow")
    assert h == {"start": "2026-09-04T00:00", "end_inclusive": "2026-09-10T23:59",
                 "end_exclusive": "2026-09-11T00:00", "scale": "minute",
                 "tz": "Europe/Moscow",
                 "source": {"sheet": "Свойства", "keys": ["planSince", "planUntil"],
                            "sid": "D1"}}
    # Без объявления пояса — null, ключ остаётся (читатель видит «не объявлен»).
    assert hs.declared_horizon(_book())["tz"] is None
    # Заголовки «Свойство / Значение» читаются позиционно так же.
    ds = _book(props=[{"Свойство": "planSince", "Значение": "2026-09-04T00:00"},
                      {"Свойство": "planUntil", "Значение": "2026-09-10T23:59"}])
    ds["tables"]["D1"]["Свойства"]["columns"] = ["Свойство", "Значение"]
    assert hs.declared_horizon(ds)["end_exclusive"] == "2026-09-11T00:00"
    rep = _report(_book())
    assert _check(rep, "горизонт-объявлен")["status"] == "прогнана"
    assert _findings(rep, "горизонт-объявлен") == []
    assert _findings(rep, "горизонт-сутки") == []
    assert dr.validate_report(rep) == []


def test_horizon_without_keys_gives_no_section_but_report_says_so():
    ds = _book(props=[{"ключ": "главная база", "значение": "PLY"}])
    assert hs.declared_horizon(ds) is None
    rep = _report(ds)
    chk = _check(rep, "горизонт-объявлен")
    assert chk["status"] == "прогнана" and chk["basis"] == {"builtin": "пропуск данных"}
    finds = _findings(rep, "горизонт-объявлен")
    assert len(finds) == 1
    assert "горизонт не объявлен" in finds[0]["text"]
    assert finds[0]["severity"] == "КРИТИЧЕСКОЕ"
    assert finds[0]["class"] == "пропуск данных"
    assert finds[0]["target"] == {"sid": "D1", "sheet": "Свойства", "row": 1}
    assert not [c for c in rep["checks"] if c.get("template") == "горизонт-сутки"]
    assert dr.validate_report(rep) == []
    # Адрес — заголовок листа: очистка строк не вычитает.
    clean = json.loads(clean_mod.export_json(ds, rep))
    assert clean["cleaning"]["rows_removed"] == 0
    assert len(clean["tables"]["D1"]["Свойства"]["rows"]) == 1
    # Один ключ из двух — тоже «не объявлен».
    one = _book(props=HORIZON_PROPS[:1])
    assert hs.declared_horizon(one) is None
    assert "planUntil" in _findings(_report(one), "горизонт-объявлен")[0]["text"]


def test_horizon_without_properties_sheet_is_addressed_to_first_sheet():
    ds = _book()
    del ds["tables"]["D1"]["Свойства"]
    rep = _report(ds)
    finds = _findings(rep, "горизонт-объявлен")
    assert len(finds) == 1 and "листа «Свойства»" in finds[0]["text"]
    assert finds[0]["target"]["row"] == 1 and finds[0]["target"]["sid"] == "D1"
    assert dr.validate_report(rep) == []


def test_horizon_unreadable_key_or_reversed_bounds_is_finding_and_no_section():
    bad = _book(props=[{"ключ": "planSince", "значение": "04.09.2026 00:00"},
                       {"ключ": "planUntil", "значение": "2026-09-10T23:59"}])
    assert hs.declared_horizon(bad) is None
    rep = _report(bad)
    finds = _findings(rep, "горизонт-объявлен")
    assert len(finds) == 1 and "planSince" in finds[0]["text"]
    assert finds[0]["target"] == {"sid": "D1", "sheet": "Свойства", "row": 2}
    assert dr.validate_report(rep) == []
    # Нечитаемая строка ключа вычитается очисткой: у пакета ключа нет.
    clean = json.loads(clean_mod.export_json(bad, rep))
    assert clean["cleaning"]["rows_removed"] == 1
    assert hs.declared_horizon(clean) is None
    # Пояс или секунды в значении — не канон; читаемо, но начало не раньше конца.
    assert hs.iso_minute("2026-09-04T00:00+03:00") is None
    assert hs.iso_minute("2026-09-04T00:00:30") is None
    rev = _book(props=[{"ключ": "planSince", "значение": "2026-09-10T23:59"},
                       {"ключ": "planUntil", "значение": "2026-09-04T00:00"}])
    assert hs.declared_horizon(rev) is None
    finds = _findings(_report(rev), "горизонт-объявлен")
    assert len(finds) == 1 and "не раньше конца" in finds[0]["text"]


def test_horizon_bounds_off_full_days_is_indication_not_ban():
    ds = _book(props=[{"ключ": "planSince", "значение": "2026-09-04T06:00"},
                      {"ключ": "planUntil", "значение": "2026-09-10T18:00"}])
    h = hs.declared_horizon(ds)
    assert h["start"] == "2026-09-04T06:00" and h["end_exclusive"] == "2026-09-10T18:01"
    rep = _report(ds)
    assert _findings(rep, "горизонт-объявлен") == []
    chk = _check(rep, "горизонт-сутки")
    assert chk["status"] == "прогнана" and chk["basis"] == {"builtin": "аномалия"}
    finds = _findings(rep, "горизонт-сутки")
    assert [f["target"]["row"] for f in finds] == [2, 3]
    assert all(f["severity"] == "РЕДАКЦИОННОЕ" and f["class"] == "аномалия" for f in finds)
    assert dr.validate_report(rep) == []
    # Показание строк не вычитает — горизонт в пакете остаётся.
    clean = json.loads(clean_mod.export_json(ds, rep))
    assert clean["cleaning"]["rows_removed"] == 0
    assert hs.declared_horizon(clean) == h


def _ready_doc():
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1", "revision": 2},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [
            {"id": "R-H", "title": "Жёсткое", "statement": "x ≤ 1",
             "zone": "BOTH", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
        ],
        "questions": [
            {"id": "Q-3a-GOAL", "part": "3a", "title": "Целевая",
             "text": "т", "answer_format": {"type": "registry"},
             "rule_refs": ["R-H"],
             "source_refs": [{"sid": "S1", "anchor": "п.1"}],
             "answer": {"raw": "любое допустимое решение", "status": "применён",
                        "revision": 2, "goal_decline": True}},
            {"id": "Q-3b-GOAL", "part": "3b", "title": "Целевая",
             "text": "т2", "answer_format": {"type": "registry"},
             "rule_refs": ["R-H"],
             "source_refs": [{"sid": "S1", "anchor": "п.1"}],
             "answer": {"raw": "любое допустимое решение", "status": "применён",
                        "revision": 2, "goal_decline": True}},
        ],
        "objectives": [],
    }


def _run_with_data(tmp_path, ds):
    run = tmp_path / "run-9018"
    (run / "extract").mkdir(parents=True)
    doc = _ready_doc()
    assert m.validate_model(doc) == []
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (run / "data-freeze").mkdir()
    (run / "data-report").mkdir()
    (run / "data-freeze" / "dataset.json").write_text(
        json.dumps(ds, ensure_ascii=False), encoding="utf-8")
    rep = _report(ds)
    (run / "data-report" / "data-report.json").write_text(
        json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return run


def test_package_spec_1_18_horizon_presence_and_gate(tmp_path):
    assert tp.SPEC_VERSION == "1.18"
    ds = _book(presence=[_p("H01", "01.09.2026 08:00", "01.09.2026 20:00", "PLY")])
    run = _run_with_data(tmp_path, ds)
    files = tp.build_package(run)
    manifest = json.loads(files["manifest.json"])
    assert manifest["spec_version"] == "1.18"
    assert manifest["horizon"] == {
        "start": "2026-09-04T00:00", "end_inclusive": "2026-09-10T23:59",
        "end_exclusive": "2026-09-11T00:00", "scale": "minute", "tz": None,
        "source": {"sheet": "Свойства", "keys": ["planSince", "planUntil"], "sid": "D1"}}
    h01 = _emp(manifest["history"], "H01")
    assert h01["location_intervals"] == [
        {"start": "2026-09-01T08:00", "end": "2026-09-01T20:00", "station": "PLY",
         "kind": "history_fact", "sources": [_prov(2)]}]
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, counters = gate.check(pkg)
    assert problems == [] and counters["spec_version"] == "1.18"
    # Подделка горизонта (минута прибавлена дважды) — красный гейт 1.18.
    bad = dict(files)
    mf = json.loads(bad["manifest.json"])
    mf["horizon"]["end_exclusive"] = "2026-09-11T00:01"
    bad["manifest.json"] = json.dumps(mf, ensure_ascii=False).encode("utf-8")
    (tmp_path / "bad.zip").write_bytes(tp.write_zip(bad))
    probs, _ = gate.check(tmp_path / "bad.zip")
    assert any("horizon" in p and "1.18" in p for p in probs), probs
    # Без ключей «Свойств» — ключа horizon нет, гейт зелёный.
    run2 = _run_with_data(tmp_path / "x", _book(props=[]))
    files2 = tp.build_package(run2)
    assert "horizon" not in json.loads(files2["manifest.json"])
    (tmp_path / "p2.zip").write_bytes(tp.write_zip(files2))
    probs2, counters2 = gate.check(tmp_path / "p2.zip")
    assert probs2 == []                      # структурно передаваем
    # GPT-022 B2: готовность по C-27b — отдельная явная отметка гейта
    assert counters2["horizon_declared"] is False
    assert any("горизонт не объявлен" in n for n in counters2["readiness_notes"])
    # …а пакет с горизонтом отметки не несёт
    (tmp_path / "p1.zip").write_bytes(tp.write_zip(files))
    probs1, counters1 = gate.check(tmp_path / "p1.zip")
    assert probs1 == [] and counters1["horizon_declared"] is True
    assert "readiness_notes" not in counters1


def test_package_zip_roundtrip_keeps_horizon(tmp_path):
    run = _run_with_data(tmp_path, _book())
    body = tp.export_zip(run)
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        manifest = json.loads(z.read("manifest.json"))
    assert manifest["horizon"]["end_exclusive"] == "2026-09-11T00:00"


# ── присутствие (1.18.2) ──

def test_presence_union_same_station_and_kind_carries_all_sources():
    ds = _book(presence=[_p("H01", "01.09.2026 08:00", "01.09.2026 20:00", "PLY"),
                         _p("H01", "01.09.2026 18:00", "02.09.2026 06:00", "ply")])
    sec = hs.history_section(ds)
    assert sec is not None
    h01 = _emp(sec, "H01")
    assert h01["location_evidence"] == [
        {"start": "2026-09-01T08:00", "end": "2026-09-01T20:00", "station": "PLY",
         "kind": "history_fact", "provenance": _prov(2)},
        {"start": "2026-09-01T18:00", "end": "2026-09-02T06:00", "station": "PLY",
         "kind": "history_fact", "provenance": _prov(3)}]
    assert h01["location_intervals"] == [
        {"start": "2026-09-01T08:00", "end": "2026-09-02T06:00", "station": "PLY",
         "kind": "history_fact", "sources": [_prov(2), _prov(3)]}]
    assert h01["conflicts"] == []
    # Сотрудник без строк — ключей нет (не пустые списки).
    h02 = _emp(sec, "H02")
    assert not any(k in h02 for k in hs.PRESENCE_RECORD_KEYS)
    # Присутствие — не история накопителей: with_history не меняется.
    assert sec["coverage"] == {"employees_total": 3, "with_history": 0, "kind": "none"}
    assert h01["provenance"] is None and h01["last_duty"] is None
    # Без листов истории и присутствия раздела нет.
    assert hs.history_section(_book()) is None


def test_presence_adjacent_intervals_merge_and_gaps_stay():
    ds = _book(presence=[_p("H01", "01.09.2026 08:00", "01.09.2026 12:00", "PLY"),
                         _p("H01", "01.09.2026 12:00", "01.09.2026 16:00", "PLY"),
                         _p("H01", "02.09.2026 08:00", "02.09.2026 10:00", "PLY")])
    h01 = _emp(hs.history_section(ds), "H01")
    assert [(i["start"], i["end"], i["sources"]) for i in h01["location_intervals"]] == [
        ("2026-09-01T08:00", "2026-09-01T16:00", [_prov(2), _prov(3)]),
        ("2026-09-02T08:00", "2026-09-02T10:00", [_prov(4)])]


def test_presence_conflict_of_stations_has_no_interval_and_is_reported():
    ds = _book(presence=[_p("H01", "01.09.2026 08:00", "01.09.2026 16:00", "PLY"),
                         _p("H01", "01.09.2026 12:00", "01.09.2026 20:00", "SVG")])
    h01 = _emp(hs.history_section(ds), "H01")
    assert h01["location_intervals"] == [
        {"start": "2026-09-01T08:00", "end": "2026-09-01T12:00", "station": "PLY",
         "kind": "history_fact", "sources": [_prov(2)]},
        {"start": "2026-09-01T16:00", "end": "2026-09-01T20:00", "station": "SVG",
         "kind": "history_fact", "sources": [_prov(3)]}]
    assert h01["conflicts"] == [
        {"start": "2026-09-01T12:00", "end": "2026-09-01T16:00",
         "stations": ["PLY", "SVG"], "rows": [_prov(2), _prov(3)]}]
    rep = _report(ds)
    finds = _findings(rep, "история-присутствие-конфликт")
    assert len(finds) == 1
    assert finds[0]["target"] == _prov(2)
    assert finds[0]["severity"] == "СЕРЬЁЗНОЕ" and finds[0]["class"] == "история"
    assert "PLY, SVG" in finds[0]["text"]
    assert dr.validate_report(rep) == []
    # Конфликт строк не вычитает — доказательства остаются в пакете.
    assert json.loads(clean_mod.export_json(ds, rep))["cleaning"]["rows_removed"] == 0


def test_presence_fact_after_as_of_is_defect_declared_in_horizon_is_allowed():
    ds = _book(presence=[_p("H01", "03.09.2026 08:00", "05.09.2026 12:00", "PLY"),
                         _p("H02", "05.09.2026 00:00", "07.09.2026 00:00", "SVG",
                            kind="объявлено")])
    rep = _report(ds)
    chk = _check(rep, "история-присутствие-срез")
    assert chk["status"] == "прогнана" and chk["rows_checked"] == 2
    finds = _findings(rep, "история-присутствие-срез")
    assert len(finds) == 1 and finds[0]["target"]["row"] == 2
    assert finds[0]["severity"] == "СЕРЬЁЗНОЕ" and "позже" in finds[0]["text"]
    assert dr.validate_report(rep) == []
    sec = hs.history_section(ds)
    # Дефект, а не молчаливая перетипизация: факт остаётся фактом.
    assert _emp(sec, "H01")["location_evidence"][0]["kind"] == "history_fact"
    assert _emp(sec, "H02")["location_intervals"] == [
        {"start": "2026-09-05T00:00", "end": "2026-09-07T00:00", "station": "SVG",
         "kind": "declared_presence", "sources": [_prov(3)]}]
    # Латинские синонимы вида принимаются.
    lat = _book(presence=[_p("H01", "01.09.2026 08:00", "01.09.2026 10:00", "PLY", "fact"),
                          _p("H01", "05.09.2026 08:00", "05.09.2026 10:00", "PLY", "declared")])
    kinds = [e["kind"] for e in _emp(hs.history_section(lat), "H01")["location_evidence"]]
    assert kinds == ["history_fact", "declared_presence"]


def test_presence_kind_is_kept_on_overlap_of_kinds_by_segmentation():
    ds = _book(presence=[_p("H01", "01.09.2026 08:00", "01.09.2026 12:00", "PLY"),
                         _p("H01", "01.09.2026 10:00", "01.09.2026 14:00", "PLY",
                            kind="объявлено")])
    h01 = _emp(hs.history_section(ds), "H01")
    assert h01["location_intervals"] == [
        {"start": "2026-09-01T08:00", "end": "2026-09-01T12:00", "station": "PLY",
         "kind": "history_fact", "sources": [_prov(2)]},
        {"start": "2026-09-01T12:00", "end": "2026-09-01T14:00", "station": "PLY",
         "kind": "declared_presence", "sources": [_prov(3)]}]
    assert h01["conflicts"] == []


def test_presence_station_outside_network_is_finding_row_kept():
    ds = _book(presence=[_p("H01", "01.09.2026 08:00", "01.09.2026 12:00", "GRT")])
    rep = _report(ds)
    finds = _findings(rep, "история-присутствие-станция")
    assert len(finds) == 1 and "GRT" in finds[0]["text"]
    assert finds[0]["severity"] == "СЕРЬЁЗНОЕ" and finds[0]["target"]["row"] == 2
    assert dr.validate_report(rep) == []
    clean = json.loads(clean_mod.export_json(ds, rep))
    assert clean["cleaning"]["rows_removed"] == 0
    assert _emp(hs.history_section(clean), "H01")["location_intervals"][0]["station"] == "GRT"
    # Сеть не определить (нет колонок вылета/прилёта) — «не прогнана» с причиной.
    nonet = _book(presence=[_p("H01", "01.09.2026 08:00", "01.09.2026 12:00", "PLY")])
    nonet["tables"]["D1"]["Рейсы"] = {"columns": ["Номер рейса", "Дата"], "rows": []}
    chk = _check(_report(nonet), "история-присутствие-станция")
    assert chk["status"] == "не прогнана" and "сеть" in chk["reason"]


def test_presence_unknown_employee_and_unreadable_values_are_subtracted():
    ds = _book(presence=[_p("H99", "01.09.2026 08:00", "01.09.2026 12:00", "PLY"),
                         _p("H01", "01.09.2026 12:00", "01.09.2026 08:00", "PLY"),
                         _p("H02", "01.09.2026 08:00", "01.09.2026 12:00", "PLY",
                            kind="командировка"),
                         _p("H03", "1 сентября", "01.09.2026 12:00", "PLY")])
    rep = _report(ds)
    tab = _findings(rep, "история-присутствие-табельный")
    assert [f["target"]["row"] for f in tab] == [2]
    assert tab[0]["severity"] == "БЛОКИРУЮЩЕЕ"
    vals = _findings(rep, "история-присутствие-значения")
    assert sorted(f["target"]["row"] for f in vals) == [3, 4, 5]
    assert dr.validate_report(rep) == []
    clean = json.loads(clean_mod.export_json(ds, rep))
    assert clean["cleaning"]["rows_removed"] == 4
    # В разделе нечитаемых строк нет; у всех сотрудников ключей присутствия нет.
    sec = hs.history_section(ds)
    assert all(not any(k in e for k in hs.PRESENCE_RECORD_KEYS) for e in sec["employees"])
    assert [e["employee_id"] for e in sec["employees"]] == ["H01", "H02", "H03"]


def test_presence_with_empty_or_missing_crew_registry_is_a_defect_not_a_bypass():
    """GPT-022 B1: пустой реестр «Экипажи» — отрицательный исход проверки
    табельного для каждой строки присутствия, а не пропуск; присутствие
    без объявленного сотрудника в раздел истории не попадает."""
    row = _p("H99", "01.09.2026 08:00", "01.09.2026 12:00", "PLY")
    # пустой реестр
    empty = _book(presence=[row], crew=())
    tab = _findings(_report(empty), "история-присутствие-табельный")
    assert [f["target"]["row"] for f in tab] == [2]
    assert "Экипажи» пуст" in tab[0]["text"] and tab[0]["severity"] == "БЛОКИРУЮЩЕЕ"
    # отсутствующий лист «Экипажи»
    missing = _book(presence=[row], crew=())
    del missing["tables"]["D1"]["Экипажи"]
    tab2 = _findings(_report(missing), "история-присутствие-табельный")
    assert [f["target"]["row"] for f in tab2] == [2]
    # экспорт: без реестра запись H99 из присутствия не создаётся
    section = hs.history_section(missing)
    assert not any(e.get("employee_id") == "H99"
                   for e in (section or {}).get("employees") or [])


def test_presence_fact_covering_last_duty_end_at_other_station_is_contradiction():
    history = [{"Табельный номер": "H01", "Последняя смена: конец": "03.09.2026 21:30",
                "Последняя смена: станция": "SVG"}]
    ds = _book(history=history,
               presence=[_p("H01", "03.09.2026 20:00", "03.09.2026 23:00", "PLY"),
                         _p("H01", "01.09.2026 08:00", "01.09.2026 12:00", "PLY")])
    rep = _report(ds)
    finds = _findings(rep, "история-присутствие-конфликт")
    assert len(finds) == 1 and finds[0]["target"]["row"] == 2
    assert "противоречие" in finds[0]["text"] and "SVG" in finds[0]["text"]
    assert dr.validate_report(rep) == []
    # Та же станция или другой прошлый интервал — не противоречие.
    ok = _book(history=history,
               presence=[_p("H01", "03.09.2026 20:00", "03.09.2026 23:00", "SVG"),
                         _p("H01", "01.09.2026 08:00", "01.09.2026 12:00", "PLY")])
    assert _findings(_report(ok), "история-присутствие-конфликт") == []
    # Запись из листа «История» и присутствие живут в одной записи сотрудника.
    h01 = _emp(hs.history_section(ds), "H01")
    assert h01["last_duty"]["end_station"] == "SVG"
    assert len(h01["location_intervals"]) == 2
    assert hs.history_section(ds)["coverage"]["with_history"] == 1


def test_presence_sheet_is_a_history_section_sheet():
    ds = _book(presence=[_p("H01", "01.09.2026 08:00", "01.09.2026 12:00", "PLY")])
    assert ("D1", "Присутствие") in dc.history_sheet_keys(ds)
    spec = {"template": "обязательное-поле", "basis": {"builtin": "дубль"},
            "params": {"sheet": "D1/Присутствие", "column": "Станция"}}
    assert "раздел истории" in dc.validate_spec(spec, ds, {})
    ids = [s["id"] for s in hs.check_specs(ds)]
    assert ids == [f"C-история-присутствие-{t}-D1"
                   for t in ("табельный", "значения", "станция", "срез", "конфликт")]
    assert all(s["basis"] == {"builtin": "история"} for s in hs.check_specs(ds))
