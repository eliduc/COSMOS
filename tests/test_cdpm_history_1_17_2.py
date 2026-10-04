# -*- coding: utf-8 -*-
"""Ревизия 1.17.2 (05.09.2026, решения владельца): производный раздел
истории из «Журнала назначений», счётчики окон, полнота (full/partial/none),
три исхода анкеты и решение пользователя в пакете."""
from __future__ import annotations

import copy
import json

from cdpm import answers as ans
from cdpm import data_checks as dc
from cdpm import history_journal as hj
from cdpm import history_questions as hq
from cdpm import history_section as hs
from cdpm import model as m
from cdpm.sample import sample_model

AS_OF = "04.09.2026 00:00"
#: Блок анкеты об истории у строки набора: семья посадки — rest_floors,
#: вид чтения истории по записи каталога — boundary (каталог 0.5.0).
Q_BOUNDARY = hs.history_question_id("boundary")


def _doc_with_history_rule(kind="HARD", with_night=False):
    """Модель с одной строкой ростеринга семьи rest_floors (зависит от
    истории); with_night — плюс параметры окна ночи и недельного периода
    (только для чтения окон/ночи, контракт семьи их не знает)."""
    doc = sample_model()
    params = {"min_rest_home": {"value": 720, "quote": "720 минут"}}
    if with_night:
        params.update({"night_duty_start": {"value": 1320, "quote": "22:00"},
                       "night_duty_end": {"value": 360, "quote": "06:00"},
                       "weekly_rest_period": {"value": 10080, "quote": "7 суток"}})
    doc["rules"].append({
        "id": "R-H-REST", "kind": kind, "zone": "ROSTERING", "title": "Отдых дома",
        "statement": "отдых дома между сменами не короче 720 минут",
        "sources": [{"sid": "S1", "anchor": "п.9"}],
        # Отпечаток ДЕЙСТВУЮЩЕГО каталога, а не выдуманный: с K01-барьером
        # (12.09.2026) посадка по ПРЕЖНЕМУ каталогу, которой недостаёт
        # обязательного параметра текущей записи семьи, — «перепосадка
        # требуется», и валидатор свода о ней говорит. Недостача
        # `min_rest_outstation` в этом наборе законна (её спрашивает
        # анкета) и держит готовность с гейтом, но свод инвариантам
        # контракта не противоречит.
        "template_fit": {"category": "A", "family": "rest_floors",
                         "registry_rev": m.registry_fingerprint(),
                         "quote": "отдых дома 720", "params": params},
    })
    return doc


def _book(journal_rows=None, history_rows=None, crew=("H01", "H02", "H03")):
    tables = {
        "Экипажи": {"columns": ["Табельный номер", "База"],
                    "rows": [{"Табельный номер": e, "База": "SVG"} for e in crew]},
        "Свойства": {"columns": ["ключ", "значение"],
                     "rows": [{"ключ": "planSince", "значение": AS_OF}]},
        "Рейсы": {"columns": ["Номер рейса", "Дата"], "rows": []},
    }
    if journal_rows is not None:
        tables["Журнал назначений"] = {
            "columns": ["Табельный номер", "Начало", "Конец", "Станция начала",
                        "Станция конца", "Вид", "Налёт, мин", "Лётное время, мин",
                        "Посадок", "Ночная"],
            "rows": journal_rows}
    if history_rows is not None:
        tables["История"] = {"columns": ["Табельный номер", "Срез",
                                         "Налёт с начала месяца, мин",
                                         "Окно 7 дн: рабочих дней", "Окно 7 дн: выходных"],
                             "rows": history_rows}
    return {"tables": {"D1": tables}}


def _j(eid, start, end, kind="работа", dep="SVG", arr="SVG", block="", flight="",
       landings="", night=""):
    return {"Табельный номер": eid, "Начало": start, "Конец": end,
            "Станция начала": dep, "Станция конца": arr, "Вид": kind,
            "Налёт, мин": block, "Лётное время, мин": flight, "Посадок": landings,
            "Ночная": night}


H01_JOURNAL = [
    _j("H01", "20.08.2026 08:00", "20.08.2026 16:00", block="300", flight="300", landings="2"),
    _j("H01", "01.09.2026 08:00", "01.09.2026 18:00", block="360", flight="360", landings="4"),
    _j("H01", "02.09.2026 22:30", "03.09.2026 04:00", block="200", flight="200", landings="2",
       arr="PLY"),                                            # ночная (22:30 в окне)
    _j("H01", "03.09.2026 12:00", "03.09.2026 20:00", kind="резерв", dep="PLY", arr="PLY"),
]


def test_journal_derivation_month_series_windows_and_rest():
    doc = _doc_with_history_rule(with_night=True)
    sec = hs.history_section(_book(journal_rows=H01_JOURNAL), doc)
    assert sec["source"]["kind"] == "journal" and sec["source"]["sheet"] == "Журнал назначений"
    h01 = next(e for e in sec["employees"] if e["employee_id"] == "H01")
    assert h01["provenance"] == {"sid": "D1", "sheet": "Журнал назначений", "rows": [2, 3, 4, 5]}
    # Месяц среза: налёт 360+200, посадок 6, рабочих дней 01,02,03 (резерв — рабочий день).
    assert h01["month_to_date"]["block_min"] == 560
    assert h01["month_to_date"]["landings"] == 6
    assert h01["month_to_date"]["days_worked"] == 3
    assert h01["month_to_date"]["work_min"] == 600 + 330 + 480
    # Последняя смена — резерв 03.09 12:00–20:00; отдых до горизонта 240 мин.
    assert h01["last_duty"]["end"] == "2026-09-03T20:00" and h01["last_duty"]["end_station"] == "PLY"
    assert h01["rest_before_horizon_min"] == 240
    assert h01["location"] == "PLY"
    # Серии: три рабочих дня подряд до среза; ночная серия — последняя работа
    # (02.09 22:30) ночная → 1; дней с выходного — 3.
    assert h01["series"] == {"consecutive_duty_days": 3, "consecutive_night_duties": 1,
                             "days_since_day_off": 3}
    r = h01["rolling"]
    # Сутки без строк — unknown, не выходной: выходных 0, неизвестных 4.
    assert r["last_7d_duty_days"] == 3 and r["last_7d_days_off"] == 0
    assert r["last_7d_unknown_days"] == 4
    assert r["last_7d_night_duties"] == 1 and r["last_7d_block_min"] == 560
    assert r["last_28d_block_min"] == 860 and r["last_28d_duty_days"] == 4
    # Окна: канонические 7/14/28 + weekly_rest_period 10080 мин = 7 суток.
    assert hs.rule_windows_days(doc) == [7, 14, 28]
    assert hs.night_window_of(doc) == (1320, 360)
    # Полнота: строки только у H01 → partial; глубина 15 дн < требуемых 28.
    assert sec["coverage"]["with_history"] == 1 and sec["coverage"]["kind"] == "partial"
    assert sec["coverage"]["depth_days_available"] == 15
    assert sec["coverage"]["depth_days_required"] == 28
    assert sec["user_decision"] is None and sec["assume_zero_when_missing"] is False
    h02 = next(e for e in sec["employees"] if e["employee_id"] == "H02")
    assert h02["provenance"] is None and h02["month_to_date"] is None


def test_planned_rows_after_horizon_are_ignored():
    rows = H01_JOURNAL + [_j("H01", "04.09.2026 06:00", "04.09.2026 12:00", block="300")]
    sec = hs.history_section(_book(journal_rows=rows), _doc_with_history_rule())
    h01 = next(e for e in sec["employees"] if e["employee_id"] == "H01")
    assert h01["month_to_date"]["block_min"] == 560            # план не считается
    assert h01["provenance"]["rows"] == [2, 3, 4, 5, 6]


def test_sheet_has_priority_and_journal_fills_missing():
    hist = [{"Табельный номер": "H02", "Срез": AS_OF, "Налёт с начала месяца, мин": "100",
             "Окно 7 дн: рабочих дней": "2", "Окно 7 дн: выходных": "5"}]
    sec = hs.history_section(_book(journal_rows=H01_JOURNAL, history_rows=hist),
                             _doc_with_history_rule())
    assert sec["source"]["kind"] == "integrator_sheet" and sec["source"].get("mixed") is True
    by = {e["employee_id"]: e for e in sec["employees"]}
    assert by["H02"]["month_to_date"]["block_min"] == 100
    # Счётчики окон из листа (1.17.2) — те же ключи, что у журнала.
    assert by["H02"]["rolling"] == {"last_7d_duty_days": 2, "last_7d_days_off": 5}
    assert by["H01"]["provenance"]["sheet"] == "Журнал назначений"
    # Смешанный режим: глубина судится у выведенных из журнала (H01: 15 дн.).
    assert sec["coverage"] == {"employees_total": 3, "with_history": 2, "kind": "partial",
                               "depth_days_required": 28, "depth_days_available": 15}


def test_full_coverage_when_everyone_has_deep_journal():
    rows = []
    for eid in ("H01", "H02", "H03"):
        rows.append(_j(eid, "01.08.2026 08:00", "01.08.2026 16:00", block="300"))
        rows.append(_j(eid, "02.09.2026 08:00", "02.09.2026 16:00", block="300"))
    sec = hs.history_section(_book(journal_rows=rows), _doc_with_history_rule())
    assert sec["coverage"]["kind"] == "full"
    assert sec["coverage"]["depth_days_available"] == 34


def test_no_history_without_decision_gives_no_section_and_a_question():
    doc = _doc_with_history_rule()
    book = _book()
    assert hs.history_section(book, doc) is None
    created = hq.ensure_history_questions(doc, book)
    assert created == [Q_BOUNDARY]
    q = next(x for x in doc["questions"] if x["id"] == Q_BOUNDARY)
    assert q["part"] == "3b" and q["rule_refs"] == ["R-H-REST"]
    assert [o["key"] for o in q["answer_format"]["options"]] == ["а", "б"]
    # Блок назван ВИДОМ чтения истории, полноту данных несёт он сам.
    assert q["history"] == {"read_kind": "boundary", "coverage": "none"}
    assert q["id"].startswith(hs.HISTORY_QUESTION_PREFIX)
    # Что именно нужно — словами ЗАПИСИ КАТАЛОГА семьи rest_floors.
    need = hs.history_reads()["rest_floors"]["need"]
    assert need in q["text"] and need in q["title"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    # Идемпотентно; без зависимых строк вопроса нет.
    assert hq.ensure_history_questions(doc, book) == []
    assert hq.ensure_history_questions(sample_model(), book) == []
    # Открытый вопрос части 3б по жёсткой строке — причина «не готово» у
    # ростеринга (в образцовой модели есть и другие открытые вопросы, потому
    # сравниваем счётчик причины до и после).
    key = "открытые вопросы части 3b по жёстким строкам"
    before = m.readiness_matrix(sample_model())["rostering"]["feasibility-hard"]["reasons"].get(key, 0)
    now = m.readiness_matrix(doc)["rostering"]["feasibility-hard"]["reasons"].get(key, 0)
    assert now == before + 1


def test_continue_zero_makes_section_with_flag_and_null_employees():
    doc = _doc_with_history_rule()
    book = _book()
    hq.ensure_history_questions(doc, book)
    summary = ans.apply_answers(doc, {Q_BOUNDARY: "б"})
    assert summary["applied"] == 1
    q = next(x for x in doc["questions"] if x["id"] == Q_BOUNDARY)
    assert q["answer"]["status"] == "применён" and q["answer"]["effects"] == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    sec = hs.history_section(book, doc)
    assert sec["source"]["kind"] == "none" and sec["coverage"]["kind"] == "none"
    assert sec["user_decision"] == {"question": Q_BOUNDARY, "key": "б",
                                    "decision": "continue_zero"}
    assert sec["assume_zero_when_missing"] is True
    assert all(e["month_to_date"] is None for e in sec["employees"])
    # Применённый ответ больше не держит ростеринг: причина исчезла.
    key = "открытые вопросы части 3b по жёстким строкам"
    base = m.readiness_matrix(sample_model())["rostering"]["feasibility-hard"]["reasons"].get(key, 0)
    assert m.readiness_matrix(doc)["rostering"]["feasibility-hard"]["reasons"].get(key, 0) == base


def test_enter_requested_keeps_lock_and_becomes_supplemented_when_data_arrive():
    doc = _doc_with_history_rule()
    hq.ensure_history_questions(doc, _book())
    ans.apply_answers(doc, {Q_BOUNDARY: "а"})
    sec = hs.history_section(_book(), doc)
    assert sec["user_decision"]["decision"] == "enter_requested"
    assert sec["assume_zero_when_missing"] is False and sec["coverage"]["kind"] == "none"
    rows = []
    for eid in ("H01", "H02", "H03"):
        rows += [_j(eid, "01.08.2026 08:00", "01.08.2026 16:00", block="300"),
                 _j(eid, "02.09.2026 08:00", "02.09.2026 16:00", block="300")]
    sec2 = hs.history_section(_book(journal_rows=rows), doc)
    assert sec2["coverage"]["kind"] == "full"
    assert sec2["user_decision"]["decision"] == "supplemented"


def test_partial_question_and_continue_partial():
    doc = _doc_with_history_rule()
    book = _book(journal_rows=H01_JOURNAL)
    assert hq.ensure_history_questions(doc, book) == [Q_BOUNDARY]
    q = next(x for x in doc["questions"] if x["id"] == Q_BOUNDARY)
    assert "1 из 3" in q["text"] and "15 дн." in q["text"]
    assert q["history"] == {"read_kind": "boundary", "coverage": "partial"}
    ans.apply_answers(doc, {Q_BOUNDARY: "б"})
    sec = hs.history_section(book, doc)
    assert sec["user_decision"]["decision"] == "continue_partial"
    assert sec["assume_zero_when_missing"] is False


def test_block_is_reissued_when_data_completeness_changes():
    """Полнота данных сменилась — блок ТОГО ЖЕ вида чтения переиздан:
    текст, варианты и полнота новые, вопрос снова открыт. Блок прежней
    формы (полнота в имени) линия данных снимает кодом."""
    doc = _doc_with_history_rule()
    hq.ensure_history_questions(doc, _book())            # истории нет
    q = next(x for x in doc["questions"] if x["id"] == Q_BOUNDARY)
    assert q["history"]["coverage"] == "none"
    doc["questions"].append(dict(q, id=hs.Q_HISTORY_NONE, history=None))
    doc["questions"][-1].pop("history")
    created = hq.ensure_history_questions(doc, _book(journal_rows=H01_JOURNAL))
    assert created == [Q_BOUNDARY]
    assert q["history"] == {"read_kind": "boundary", "coverage": "partial"}
    assert "1 из 3" in q["text"] and m.is_open_question(q)
    legacy = next(x for x in doc["questions"] if x["id"] == hs.Q_HISTORY_NONE)
    assert legacy["answer"]["key"] == "неприменимо"
    assert legacy["answer"]["status"] == "применён"
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    # Решение читается по актуальному вопросу; снятый кодом не решение.
    assert hs.history_decision_of(doc) is None


def test_revision_of_history_decision_is_refused():
    doc = _doc_with_history_rule()
    hq.ensure_history_questions(doc, _book())
    ans.apply_answers(doc, {Q_BOUNDARY: "б"})
    try:
        ans.open_revision(doc, Q_BOUNDARY)
    except ans.AnswersError as exc:
        assert "повторной загрузкой" in str(exc)
    else:
        raise AssertionError("пересмотр решения по истории должен отказать словами")


def test_journal_sheet_is_a_history_sheet_for_the_data_line():
    ds = _book(journal_rows=H01_JOURNAL)
    ds["sources"] = [{"sid": "D1", "file": "b.xlsx", "sha256": "a" * 64, "rows_seen": 9}]
    assert ("D1", "Журнал назначений") in dc.history_sheet_keys(ds)
    assert dc._has_solution_sheets(ds) is False
    specs = [s for s in dc.builtin_checks(ds) if s["template"].startswith("история-журнал-")]
    assert {s["template"] for s in specs} == {"история-журнал-колонки",
                                              "история-журнал-табельный",
                                              "история-журнал-значения",
                                              "история-журнал-срез",
                                              "история-журнал-план"}
    for s in specs:
        assert dc.validate_spec(s, ds, {}) is None
    bad = _book(journal_rows=H01_JOURNAL + [
        _j("H09", "01.09.2026 08:00", "01.09.2026 06:00", kind="прогул", block="x")])
    bad["sources"] = ds["sources"]
    doc = dc.build_data_report("Полярис", bad, dc.builtin_checks(bad))
    texts = [f["text"] for f in doc["findings"]]
    assert any("отсутствует в листе «Экипажи»" in t for t in texts)
    assert any("конец раньше начала" in t for t in texts)
    assert any("вне словаря" in t for t in texts)
    assert any("целое неотрицательное" in t for t in texts)
    # Находки листов истории — класса «история»; проверка горизонта (1.18,
    # класс «пропуск данных»: в книге нет planUntil) — отдельная.
    hist_checks = {c["id"] for c in doc["checks"]
                   if str(c.get("template", "")).startswith("история-")}
    assert all(f["class"] == "история" for f in doc["findings"]
               if f["check"] in hist_checks)
    assert any(f["check"] in hist_checks for f in doc["findings"])


def test_gate_recompute_with_doc_matches_export(tmp_path):
    """Экспорт и гейт зовут одну функцию с одними файлами: результат равен."""
    doc = _doc_with_history_rule()
    book = _book(journal_rows=H01_JOURNAL)
    hq.ensure_history_questions(doc, book)
    ans.apply_answers(doc, {hs.Q_HISTORY_PARTIAL: "б"})
    a = hs.history_section(book, doc)
    b = hs.history_section(json.loads(json.dumps(book)), json.loads(json.dumps(doc)))
    assert a == b
    # Без ответов в model.json решения нет — раздел детерминирован и так.
    assert hs.history_section(book, sample_model())["user_decision"] is None


def test_night_flag_from_window_when_column_empty():
    keys, _ = hj.column_map(["Табельный номер", "Начало", "Конец", "Вид", "Ночная"])
    row = {"Табельный номер": "H01", "Начало": "02.09.2026 23:10", "Конец": "03.09.2026 03:00",
           "Вид": "работа", "Ночная": ""}
    e, problems = hj.parse_entry(row, keys, (1320, 360))
    assert problems == [] and e["night"] is True
    e2, _ = hj.parse_entry(dict(row, Начало="02.09.2026 10:00"), keys, (1320, 360))
    assert e2["night"] is False
    e3, _ = hj.parse_entry(row, keys, None)
    assert e3["night"] is None


def test_calendar_from_journal_and_counters_ignore_unknown():
    """Посуточный календарь (1.17.2): известные сутки по строкам журнала,
    остальное — unknown, НЕ выходной; счётчики окон — из календаря."""
    doc = _doc_with_history_rule(with_night=True)
    sec = hs.history_section(_book(journal_rows=H01_JOURNAL), doc)
    h01 = next(e for e in sec["employees"] if e["employee_id"] == "H01")
    cal = h01["calendar"]
    assert len(cal) == 28 and cal[0]["date"] == "2026-08-07" and cal[-1]["date"] == "2026-09-03"
    by = {c["date"]: c for c in cal}
    assert by["2026-08-20"] == {"date": "2026-08-20", "kind": "work", "night": False}
    assert by["2026-09-01"]["kind"] == "work"
    assert by["2026-09-02"] == {"date": "2026-09-02", "kind": "work", "night": True}
    assert by["2026-09-03"]["kind"] == "work"          # ночная 02.09 задела 03.09 + резерв
    assert by["2026-08-25"] == {"date": "2026-08-25", "kind": "unknown", "night": None}
    r = h01["rolling"]
    # Окно 7 суток: 01–03.09 работа, остальное неизвестно — выходных 0, а не 4.
    assert r["last_7d_duty_days"] == 3 and r["last_7d_days_off"] == 0
    assert r["last_7d_unknown_days"] == 4 and r["last_7d_night_duties"] == 1
    assert r["last_28d_duty_days"] == 4 and r["last_28d_unknown_days"] == 24


def test_unknown_cells_keep_sums_unknown_not_zero():
    """«Неизвестное — не ноль»: пустой «Налёт» у одной строки работы делает
    сумму месяца/окна неизвестной; без строк работы — известный ноль."""
    rows = [_j("H01", "01.09.2026 08:00", "01.09.2026 16:00", block="300", flight="", landings="2"),
            _j("H01", "02.09.2026 08:00", "02.09.2026 16:00", block="", flight="", landings="")]
    sec = hs.history_section(_book(journal_rows=rows), _doc_with_history_rule())
    h01 = next(e for e in sec["employees"] if e["employee_id"] == "H01")
    assert h01["month_to_date"]["block_min"] is None
    assert h01["month_to_date"]["flight_time_min"] is None
    assert h01["month_to_date"]["landings"] is None
    assert h01["month_to_date"]["work_min"] == 960          # длительности известны всегда
    assert h01["rolling"]["last_7d_block_min"] is None
    only_reserve = [_j("H02", "02.09.2026 08:00", "02.09.2026 16:00", kind="резерв")]
    sec2 = hs.history_section(_book(journal_rows=only_reserve), _doc_with_history_rule())
    h02 = next(e for e in sec2["employees"] if e["employee_id"] == "H02")
    assert h02["month_to_date"]["block_min"] == 0 and h02["month_to_date"]["days_worked"] == 1
    # Признак «Ночная» неизвестен у последней смены — серия ночных неизвестна.
    unk = [_j("H03", "02.09.2026 23:00", "03.09.2026 03:00", block="100")]
    h03 = next(e for e in hs.history_section(_book(journal_rows=unk), sample_model())["employees"]
               if e["employee_id"] == "H03")
    assert h03["series"]["consecutive_night_duties"] is None


def test_journal_rows_around_horizon_are_checked():
    rows = H01_JOURNAL + [
        _j("H02", "03.09.2026 20:00", "04.09.2026 02:00", block="120"),   # пересекает срез
        _j("H03", "05.09.2026 08:00", "05.09.2026 12:00", block="100"),   # план
    ]
    ds = _book(journal_rows=rows)
    ds["sources"] = [{"sid": "D1", "file": "b.xlsx", "sha256": "a" * 64, "rows_seen": 9}]
    specs = {s["template"]: s for s in dc.builtin_checks(ds) if s["template"].startswith("история-журнал-")}
    assert {"история-журнал-срез", "история-журнал-план"} <= set(specs)
    assert specs["история-журнал-срез"]["severity"] == "БЛОКИРУЮЩЕЕ"
    assert specs["история-журнал-план"]["severity"] == "СЕРЬЁЗНОЕ"
    n, f1 = hs.run_check("история-журнал-срез", {"sheet": "D1/Журнал назначений"}, ds)
    assert [x["target"]["row"] for x in f1] == [6] and "пересекает срез" in f1[0]["text"]
    _n, f2 = hs.run_check("история-журнал-план", {"sheet": "D1/Журнал назначений"}, ds)
    assert [x["target"]["row"] for x in f2] == [7] and "план, не факт" in f2[0]["text"]
    doc = dc.build_data_report("Полярис", ds, dc.builtin_checks(ds))
    sev = {f["target"]["row"]: f["severity"] for f in doc["findings"] if f["check"].startswith("C-история-журнал-")}
    assert sev == {6: "БЛОКИРУЮЩЕЕ", 7: "СЕРЬЁЗНОЕ"}


def test_stale_decision_of_other_outcome_does_not_leak():
    """Ответ «б» на вопрос «истории нет» не действует, когда исход стал
    «частично» и его вопрос открыт: решение пользователя null, нулей нет."""
    doc = _doc_with_history_rule()
    hq.ensure_history_questions(doc, _book())
    ans.apply_answers(doc, {Q_BOUNDARY: "б"})
    book = _book(journal_rows=H01_JOURNAL)
    assert hq.ensure_history_questions(doc, book) == [Q_BOUNDARY]
    q = next(x for x in doc["questions"] if x["id"] == Q_BOUNDARY)
    assert m.is_open_question(q), "прежний ответ дан о других данных"
    sec = hs.history_section(book, doc)
    assert sec["coverage"]["kind"] == "partial"
    assert sec["user_decision"] is None and sec["assume_zero_when_missing"] is False
    # Ответ при ТЕКУЩЕЙ полноте — действует.
    ans.apply_answers(doc, {Q_BOUNDARY: "б"})
    assert hs.history_section(book, doc)["user_decision"]["decision"] == "continue_partial"


def test_code_closed_question_reopens_when_outcome_returns():
    """История дошла полностью — блок снят кодом; ушла снова — открыт
    снова с текстом по текущим данным."""
    doc = _doc_with_history_rule()
    hq.ensure_history_questions(doc, _book())                              # истории нет
    q = next(x for x in doc["questions"] if x["id"] == Q_BOUNDARY)
    full = []
    for eid in ("H01", "H02", "H03"):
        full += [_j(eid, "01.08.2026 08:00", "01.08.2026 16:00", block="300"),
                 _j(eid, "02.09.2026 08:00", "02.09.2026 16:00", block="300")]
    assert hq.ensure_history_questions(doc, _book(journal_rows=full)) == []
    assert q["answer"]["key"] == "неприменимо"                             # снят кодом
    created = hq.ensure_history_questions(doc, _book())                    # исход вернулся
    assert created == [Q_BOUNDARY]
    assert q.get("answer") is None and m.is_open_question(q)
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_inapplicable_answer_is_not_a_decision_and_keeps_rules():
    doc = _doc_with_history_rule()
    hq.ensure_history_questions(doc, _book())
    summary = ans.apply_answers(doc, {Q_BOUNDARY: "неприменимо"})
    assert summary["unparsed"] == 1 and summary["applied"] == 0
    q = next(x for x in doc["questions"] if x["id"] == Q_BOUNDARY)
    assert q["answer"]["status"] == "не разобран" and m.is_open_question(q)
    assert next(r for r in doc["rules"] if r["id"] == "R-H-REST")["kind"] == "HARD"
    assert hs.history_decision_of(doc) is None


def test_list_form_params_and_export_solver_family_are_read():
    doc = sample_model()
    doc["rules"].append({
        "id": "R-H-SOFT", "kind": "SOFT", "zone": "ROSTERING", "title": "Равномерность",
        "statement": "разброс нагрузки", "weight": 3,
        "sources": [{"sid": "S1", "anchor": "п.9"}],
        "export_solver": {"family": "balanced_workload"},
        "params": [{"name": "night_duty_start", "value": 1320, "unit": "time_of_day_minutes",
                    "tz": "base-local", "as_written": "22:00"},
                   {"name": "night_duty_end", "value": 360, "unit": "time_of_day_minutes",
                    "tz": "base-local", "as_written": "06:00"},
                   {"name": "weekly_rest_period", "value": 20160, "unit": "duration_minutes",
                    "as_written": "14 суток"}],
    })
    assert [r["id"] for r in hs.history_dependent_rules(doc)] == ["R-H-SOFT"]
    assert hs.night_window_of(doc) == (1320, 360)
    assert 14 in hs.rule_windows_days(doc)
    # Окно счётчиков в сутках (night_window_days / days_off_window_days, count;
    # имена по семьям согласованы с COSMOS 05.09 — параметр принадлежит ровно
    # одной семье) — тоже окно пакета; вопрос истории у такого правила
    # называет календарь суток за окно.
    doc["rules"][-1]["params"].append({"name": "night_window_days", "value": 3,
                                       "unit": "count", "as_written": "3 суток"})
    assert 3 in hs.rule_windows_days(doc)
    assert set(hs.WINDOW_DAY_PARAMS) == {"days_off_window_days", "night_window_days"}
    from cdpm import history_questions as hq
    block = hq.blocks_of([doc["rules"][-1]])[0]
    assert block["read_kind"] == "accumulator"        # семья balanced_workload
    assert any("календарь суток за скользящее окно 3" in n
               for n in block["needs"]), block
    # Пояс параметра не base-local — окно ночи не выводится.
    doc["rules"][-1]["params"][0]["tz"] = "UTC"
    assert hs.night_window_of(doc) is None


def test_history_family_comes_from_the_catalog_record():
    """Носитель один (каталог 0.5.0): историю читают ровно те семьи, чья
    запись несёт `reads_history` — ни перечня границы горизонта, ни
    вывода по природе записи в коде CDPM нет. Что именно нужно правилу,
    берётся из записи семьи, а не из рукописного текста."""
    reads = hs.history_reads()
    fams = hs.history_families()
    assert fams == frozenset(reads)
    assert {"span_accumulators", "rest_floors", "calendar_windows",
            "balanced_workload", "duty_series_limit", "night_series",
            "rolling_rest", "days_off_floor"} == set(fams)
    assert "duty_limits" not in fams and "crew_composition" not in fams
    # Вид чтения — слово словаря каталога, потребность — непустой текст.
    kinds = set(hs.history_read_kinds())
    assert all(r["kind"] in kinds and r["need"].strip() for r in reads.values())
    assert reads["rest_floors"]["kind"] == "boundary"
    doc = sample_model()
    rev = "ca6999759dc499261c652afc82a0f88a57f5024e6f2307b37072974a6d6a36bc"
    doc["rules"].append({
        "id": "R-H-SERIES", "kind": "HARD", "zone": "ROSTERING",
        "title": "Серия смен",
        "statement": "Рабочих смен подряд — не более 3; после серии — не менее одного свободного дня.",
        "sources": [{"sid": "S1", "anchor": "п.12"}],
        "template_fit": {"category": "A", "registry_rev": rev,
                         "family": "duty_series_limit",
                         "params": {"max_duty_series_length": {"value": 3, "quote": "не более 3"},
                                    "post_series_days_off": {"value": 1, "quote": "не менее одного свободного дня"}}},
    })
    assert [r["id"] for r in hs.history_dependent_rules(doc)] == ["R-H-SERIES"]
    block = hq.blocks_of([doc["rules"][-1]])[0]
    assert block["read_kind"] == "series"
    assert block["needs"] == [reads["duty_series_limit"]["need"]]
    assert "остатки на начало горизонта" not in block["needs"][0]
    # Строка, зависящая от истории по СВОЕМУ блоку логики: семьи с чтением
    # истории у неё нет — вида чтения каталог ей не даёт, и блок отдельный.
    only_logic = {"id": "R-X", "template_fit": {"family": "duty_limits"},
                  "logic": {"subject": "rest"}}
    tail = hq.blocks_of([only_logic])
    assert [b["read_kind"] for b in tail] == [hs.HISTORY_KIND_BY_LOGIC]
    assert tail[0]["needs"] == [hq.NEED_BY_LOGIC]


def test_one_block_per_read_kind_and_prefix_compatibility():
    """Каталог 0.5.0: анкета получает ОДИН блок на вид чтения истории,
    встретившийся среди семей ВМП, — не восемь вопросов по семьям и не
    один общий. Идентификатор блока — префикс `Q-3b-история-` плюс слово
    словаря каталога: книги ответов наборов ловят вопрос по префиксу, и
    он не менялся."""
    doc = _doc_with_history_rule()                      # rest_floors → boundary
    # Отпечаток действующего каталога (K01-барьер 12.09.2026): у обеих
    # семей здесь задан один параметр из двух, и посадка ПРЕЖНИМ
    # каталогом сделала бы из этого «перепосадку требуется» — вопрос
    # анкеты об истории судится на законном своде, а не на устаревшем.
    rev = m.registry_fingerprint()
    doc["rules"] += [
        {"id": "R-H-SERIES", "kind": "HARD", "zone": "ROSTERING",
         "title": "Серия смен", "statement": "рабочих смен подряд — не более 3",
         "sources": [{"sid": "S1", "anchor": "п.12"}],
         "template_fit": {"category": "A", "registry_rev": rev,
                          "family": "duty_series_limit",
                          "params": {"max_duty_series_length":
                                     {"value": 3, "quote": "не более 3"}}}},
        {"id": "R-H-NIGHT", "kind": "HARD", "zone": "ROSTERING",
         "title": "Ночные подряд", "statement": "ночных смен подряд — не более 2",
         "sources": [{"sid": "S1", "anchor": "п.13"}],
         "template_fit": {"category": "A", "registry_rev": rev,
                          "family": "night_series",
                          "params": {"max_consecutive_night_duties":
                                     {"value": 2, "quote": "не более 2"}}}},
    ]
    created = hq.ensure_history_questions(doc, _book())
    assert created == sorted([Q_BOUNDARY, hs.history_question_id("series")])
    assert all(qid.startswith(hs.HISTORY_QUESTION_PREFIX) for qid in created)
    by_id = {q["id"]: q for q in doc["questions"] if q["id"] in created}
    # Две строки одного вида — ОДИН блок, обе строки в его ссылках.
    series = by_id[hs.history_question_id("series")]
    assert series["rule_refs"] == ["R-H-SERIES", "R-H-NIGHT"]
    assert series["history"]["read_kind"] == "series"
    reads = hs.history_reads()
    for fam in ("duty_series_limit", "night_series"):
        assert reads[fam]["need"] in series["text"]
    # Чужая потребность в блок не попадает: виды читают разные листы.
    assert reads["rest_floors"]["need"] not in series["text"]
    assert reads["rest_floors"]["need"] in by_id[Q_BOUNDARY]["text"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    # Семья БЕЗ чтения истории вопроса не получает вовсе.
    plain = sample_model()
    plain["rules"].append({
        "id": "R-PLAIN", "kind": "HARD", "zone": "ROSTERING",
        "title": "Предел смены", "statement": "смена не длиннее 13 часов",
        "sources": [{"sid": "S1", "anchor": "п.1"}],
        "template_fit": {"category": "A", "registry_rev": rev,
                         "family": "duty_limits",
                         "params": {"max_duty_period":
                                    {"value": 780, "quote": "13 часов"}}}})
    assert hq.ensure_history_questions(plain, _book()) == []


def test_legacy_question_of_the_answer_book_is_still_a_decision():
    """Совместимость: свод, записанный до каталога 0.5.0, несёт вопрос
    прежней формы (`Q-3b-история-нет`) — его применённый ответ остаётся
    решением «истории нет», полноту называет сам идентификатор."""
    doc = _doc_with_history_rule()
    doc["questions"].append({
        "id": hs.Q_HISTORY_NONE, "part": "3b",
        "title": "Истории сотрудников в данных нет",
        "text": "Правила ростеринга R-H-REST судятся с учётом истории.",
        "answer_format": {"type": "choice",
                          "options": [{"key": "а", "text": "ввести"},
                                      {"key": "б", "text": "продолжить"}]},
        "rule_refs": ["R-H-REST"],
        "source_refs": [{"sid": "S1", "anchor": "п.9"}]})
    assert hs.is_history_question(hs.Q_HISTORY_NONE)
    assert hs.history_question_coverage(doc["questions"][-1]) == "none"
    ans.apply_answers(doc, {hs.Q_HISTORY_NONE: "б"})
    assert hs.history_decision_of(doc) == {"question": hs.Q_HISTORY_NONE,
                                           "key": "б",
                                           "decision": "continue_zero"}
    sec = hs.history_section(_book(), doc)
    assert sec["assume_zero_when_missing"] is True
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_depth_is_judged_per_employee():
    rows = [_j("H01", "01.08.2026 08:00", "01.08.2026 16:00", block="300"),
            _j("H02", "01.08.2026 08:00", "01.08.2026 16:00", block="300"),
            _j("H03", "02.09.2026 08:00", "02.09.2026 16:00", block="300")]   # мелкий журнал
    sec = hs.history_section(_book(journal_rows=rows), _doc_with_history_rule())
    assert sec["coverage"]["with_history"] == 3
    assert sec["coverage"]["depth_days_available"] == 2 and sec["coverage"]["kind"] == "partial"
    doc = _doc_with_history_rule()
    assert hq.ensure_history_questions(doc, _book(journal_rows=rows)) == [Q_BOUNDARY]
    q = next(x for x in doc["questions"] if x["id"] == Q_BOUNDARY)
    assert "глубина журнала 2 дн. меньше требуемых 28" in q["text"]


def test_calendar_sheet_for_integrator_and_explicit_days_off():
    hist = [{"Табельный номер": "H02", "Срез": AS_OF, "Налёт с начала месяца, мин": "100",
             "Окно 7 дн: рабочих дней": "", "Окно 7 дн: выходных": ""}]
    book = _book(history_rows=hist)
    book["tables"]["D1"]["Календарь истории"] = {
        "columns": ["Табельный номер", "Дата", "Вид", "Ночная"],
        "rows": [{"Табельный номер": "H02", "Дата": "01.09.2026", "Вид": "работа", "Ночная": "да"},
                 {"Табельный номер": "H02", "Дата": "02.09.2026", "Вид": "выходной", "Ночная": ""},
                 {"Табельный номер": "H02", "Дата": "03.09.2026", "Вид": "резерв", "Ночная": ""},
                 {"Табельный номер": "H03", "Дата": "03.09.2026", "Вид": "отпуск", "Ночная": ""}]}
    sec = hs.history_section(book, _doc_with_history_rule())
    by = {e["employee_id"]: e for e in sec["employees"]}
    c2 = {c["date"]: c for c in by["H02"]["calendar"]}
    assert c2["2026-09-01"] == {"date": "2026-09-01", "kind": "work", "night": True}
    assert c2["2026-09-02"] == {"date": "2026-09-02", "kind": "day_off", "night": False}
    assert c2["2026-09-03"]["kind"] == "reserve" and c2["2026-08-31"]["kind"] == "unknown"
    assert by["H02"]["rolling"]["last_7d_days_off"] == 1
    assert by["H02"]["rolling"]["last_7d_duty_days"] == 2
    # Сотрудник только с календарём — тоже с историей, провенанс — строки листа.
    assert by["H03"]["provenance"] == {"sid": "D1", "sheet": "Календарь истории", "rows": [5]}
    assert by["H03"]["calendar"][-1] == {"date": "2026-09-03", "kind": "leave", "night": False}
    assert sec["coverage"]["with_history"] == 2
    ds = dict(book, sources=[{"sid": "D1", "file": "b.xlsx", "sha256": "a" * 64, "rows_seen": 1}])
    assert ("D1", "Календарь истории") in dc.history_sheet_keys(ds)
