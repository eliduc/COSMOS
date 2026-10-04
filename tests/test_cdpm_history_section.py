# -*- coding: utf-8 -*-
"""Ревизия 1.17 (аудит A7, ступень L): лист «История» книги данных →
проверки линии данных → раздел manifest.history; гейт пересчитывает."""
import hashlib
import json
import zipfile

from cdpm import data_checks as dc
from cdpm import export_clean_data as clean_mod
from cdpm import export_task_package as tp
from cdpm import history_section as hs

HIST_COLS = ["Табельный номер", "Срез", "Налёт с начала месяца, мин",
             "Рабочее время с начала месяца, мин", "Дней отработано",
             "Последняя смена: конец", "Последняя смена: станция",
             "Последняя смена: работа, мин", "Последняя смена: ночная",
             "Серия рабочих дней", "Местоположение", "Окно 7 дн: работа, мин"]


def _row(eid, **kw):
    r = {c: "" for c in HIST_COLS}
    r["Табельный номер"] = eid
    r.update(kw)
    return r


def _dataset(hist_rows, approved=None):
    tables = {"D1": {
        "Экипажи": {"columns": ["Табельный номер", "База"],
                    "rows": [{"Табельный номер": "H01", "База": "PLY"},
                             {"Табельный номер": "H02", "База": "PLY"},
                             {"Табельный номер": "H03", "База": "SVG"}]},
        "Свойства": {"columns": ["ключ", "значение"],
                     "rows": [{"ключ": "planSince", "значение": "2026-09-04T00:00"}]},
        # Лист «Рейсы» — опора проверки закреплений (канон 1.17.1).
        "Рейсы": {"columns": ["Номер рейса", "Дата", "Вылет"],
                  "rows": [{"Номер рейса": "PL101", "Дата": "05.09.2026", "Вылет": "PLY"}]},
        "История": {"columns": HIST_COLS, "rows": hist_rows},
    }}
    if approved is not None:
        tables["D1"]["Утверждённые назначения"] = {
            "columns": ["Табельный номер", "Рейс", "Дата"], "rows": approved}
    return {"customer": "Тест", "tables": tables}


def _report(dataset):
    return dc.build_data_report("Тест", dataset, dc.builtin_checks(dataset))


def test_good_sheet_gives_section_with_nulls_and_coverage():
    ds = _dataset([
        _row("H01", **{"Срез": "04.09.2026 00:00",
                       "Налёт с начала месяца, мин": "480",
                       "Рабочее время с начала месяца, мин": "600",
                       "Дней отработано": "3",
                       "Последняя смена: конец": "03.09.2026 21:30",
                       "Последняя смена: станция": "SVG",
                       "Последняя смена: работа, мин": "540",
                       "Последняя смена: ночная": "нет",
                       "Серия рабочих дней": "3", "Местоположение": "SVG",
                       "Окно 7 дн: работа, мин": "1200"}),
        _row("H02"),                                  # строка есть, всё пусто
    ], approved=[{"Табельный номер": "H01", "Рейс": "PL101", "Дата": "05.09.2026"}])
    rep = _report(ds)
    hist_findings = [f for f in rep["findings"] if f["check"].startswith("C-история")]
    # Единственная находка — полнота: у H03 нет строки (СЕРЬЁЗНОЕ, не вычитает).
    assert [f["severity"] for f in hist_findings] == ["СЕРЬЁЗНОЕ"]
    assert "H03" in hist_findings[0]["text"]
    clean = json.loads(clean_mod.export_json(ds, rep))
    sec = hs.history_section(clean)
    assert sec["as_of"] == "2026-09-04T00:00"
    assert sec["assume_zero_when_missing"] is False
    assert sec["coverage"] == {"employees_total": 3, "with_history": 1, "kind": "partial"}
    by = {e["employee_id"]: e for e in sec["employees"]}
    assert set(by) == {"H01", "H02", "H03"}
    h1 = by["H01"]
    # Колонки, которых нет на листе (лётное время, посадки, размах), ключей
    # не дают — спецификация 1.17: поле может отсутствовать; пустая ячейка
    # существующей колонки даёт null (проверено на H02 ниже).
    assert h1["month_to_date"] == {"block_min": 480, "work_min": 600,
                                   "days_worked": 3}
    assert h1["last_duty"] == {"end": "2026-09-03T21:30", "end_station": "SVG",
                               "work_min": 540, "night": False}
    assert h1["rolling"] == {"last_7d_work_min": 1200}
    assert h1["series"]["consecutive_duty_days"] == 3
    assert h1["location"] == "SVG"
    assert h1["provenance"] == {"sid": "D1", "sheet": "История", "row": 2}
    assert h1["approved_assignments"] == [{"Рейс": "PL101", "Дата": "05.09.2026",
                                          "provenance": {"sid": "D1", "sheet": "Утверждённые назначения", "row": 2}}]
    # H02: строка есть, но все поля неизвестны — запись без истории (ревизия
    # 1.17.1: блоки null, не словари из null); провенанс строки сохранён;
    # в with_history не входит.
    assert by["H02"]["month_to_date"] is None and by["H02"]["last_duty"] is None
    assert by["H02"]["provenance"]["row"] == 3
    # H03: строки нет — блоки null, провенанса нет.
    assert by["H03"]["month_to_date"] is None and by["H03"]["provenance"] is None
    assert len(sec["data_fingerprint"]) == 64
    # Детерминизм.
    assert hs.history_section(json.loads(json.dumps(clean))) == sec


def test_bad_rows_are_blocking_and_removed_from_clean():
    ds = _dataset([
        _row("H01", **{"Налёт с начала месяца, мин": "-5"}),          # число
        _row("H09", **{"Налёт с начала месяца, мин": "10"}),          # чужой табельный
        _row("H02", **{"Последняя смена: конец": "05.09.2026 08:00"}),  # позже среза
        _row("H03", **{"Последняя смена: ночная": "иногда"}),         # не да/нет
    ])
    rep = _report(ds)
    hist = [f for f in rep["findings"] if f["check"].startswith("C-история")]
    texts = " | ".join(f["text"] for f in hist)
    assert "целое неотрицательное" in texts
    assert "H09" in texts and "отсутствует в листе «Экипажи»" in texts
    assert "позже среза" in texts
    assert "«да»/«нет»" in texts
    blocking = [f for f in hist if f["severity"] == "БЛОКИРУЮЩЕЕ"]
    assert len(blocking) == 4
    clean = json.loads(clean_mod.export_json(ds, rep))
    rows = clean["tables"]["D1"]["История"]["rows"]
    assert rows == []                                   # все четыре вычтены
    sec = hs.history_section(clean)
    assert sec["coverage"] == {"employees_total": 3, "with_history": 0, "kind": "none"}
    assert all(e["month_to_date"] is None for e in sec["employees"])


def test_no_history_sheet_gives_no_section():
    ds = _dataset([])
    del ds["tables"]["D1"]["История"]
    assert hs.history_section(ds) is None
    assert hs.check_specs(ds) == []


def test_package_carries_history_and_gate_recomputes(tmp_path):
    """Экспорт: manifest.history есть ⇔ лист есть; гейт ≥1.17 сверяет."""
    from cdpm.gates import gate_task_package as gate
    assert tp._spec_at_least(tp.SPEC_VERSION, "1.17")
    ds = _dataset([_row("H01", **{"Налёт с начала месяца, мин": "480"})])
    rep = _report(ds)
    clean_text = clean_mod.export_json(ds, rep)
    clean = json.loads(clean_text)
    want = hs.history_section(clean)
    doc = {"meta": {"customer": "Тест", "revision": 1},
           "sources": [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64}],
           "rules": [{"id": "R-H", "title": "т", "statement": "т", "zone": "PAIRING",
                      "kind": "HARD", "sources": [{"sid": "S1", "anchor": "абз.1"}]}],
           "questions": [], "objectives": []}
    tp.enrich_rules_for_package(doc)

    def problems(version, history_value, path):
        model = json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")
        cbytes = clean_text.encode("utf-8")
        manifest = {"spec_version": version, "customer": "Тест",
                    "source_run": "run-0001", "revision": 1,
                    "files": [{"name": "model.json",
                               "sha256": hashlib.sha256(model).hexdigest(),
                               "bytes": len(model)},
                              {"name": "clean-data.json",
                               "sha256": hashlib.sha256(cbytes).hexdigest(),
                               "bytes": len(cbytes)}],
                    "readiness": None, "allowed_modes": {}, "objectives": {},
                    "has_data": True, "coverage": None,
                    "data_fingerprint": tp.data_fingerprint_of(clean_text, clean),
                    "time": tp.time_declaration_of(doc, clean)}
        if history_value is not None:
            manifest["history"] = history_value
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("model.json", model)
            z.writestr("clean-data.json", cbytes)
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        probs, _ = gate.check(path)
        return [p for p in probs if "1.17" in p]

    assert problems("1.17", want, tmp_path / "ok.zip") == []
    assert problems("1.17", None, tmp_path / "missing.zip")
    wrong = dict(want, coverage={"employees_total": 3, "with_history": 3})
    assert problems("1.17", wrong, tmp_path / "wrong.zip")
    assert problems("1.16", None, tmp_path / "old.zip") == []
