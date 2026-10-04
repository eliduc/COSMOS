# -*- coding: utf-8 -*-
"""Формы истории сотрудника в доказательствах логики: шаблоны
«серия-ночных-смен» и «отдых-в-скользящем-окне» (валидация
fail-closed, исполнители по листу смен), детерминированный перевод
блоков logic по очертаниям formOf решателя и контрпримерные пары.

Охраняемое: окно ночи через полночь читается как [от, до); серия —
только соседние по времени смены; отдых от границ суток данных
считается; окно шире диапазона данных — «универсум пуст», не
молчаливый ноль; почти-форма — (None, причина); правило с такой
логикой едет в пакете с парой и НЕ попадает в logic_unproven."""
import json

import pytest

from cdpm import data_checks as dc
from cdpm import logic_proof as lp
from cdpm import model as m

COLS = ["Табельный номер", "Начало", "Конец"]
EMP_X = "employee×rolling_window"


def _ds(rows, sheet="Смены"):
    return {"sources": [{"sid": "SOL", "file": "x.json",
                         "sha256": "0" * 64, "rows_seen": len(rows)}],
            "tables": {"SOL": {sheet: {"columns": COLS, "rows": rows}}}}


def _row(emp, start, end):
    return {"Табельный номер": emp, "Начало": start, "Конец": end}


def _spec(template, **params):
    base = {"sheet": "SOL/Смены", "employee_column": "Табельный номер",
            "start_column": "Начало", "end_column": "Конец"}
    base.update(params)
    return {"id": "C-x", "basis": {"builtin": "т"}, "template": template,
            "params": base, "statement": "т", "unit": "строка",
            "severity": "КРИТИЧЕСКОЕ", "status": "скомпилирована"}


def _night_spec(nf=1320, nt=360, n=2):
    return _spec("серия-ночных-смен", night_from_min=nf, night_to_min=nt,
                 max_consecutive=n)


def _roll_spec(days=2, need=1440):
    return _spec("отдых-в-скользящем-окне", window_days=days,
                 min_rest_min=need)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _night_rule(bound=2, nf=1320, nt=360):
    return {"id": "R-N", "title": "Ночные подряд",
            "statement": f"ночных смен подряд не более {bound}",
            "zone": "ROSTERING", "kind": "HARD",
            "sources": [{"sid": "S1", "anchor": "п.9"}],
            "params": [
                {"name": "night_duty_start", "value": nf,
                 "unit": "time_of_day_minutes", "relation": "min",
                 "tz": "base-local", "as_written": _hhmm(nf)},
                {"name": "night_duty_end", "value": nt,
                 "unit": "time_of_day_minutes", "relation": "max",
                 "tz": "base-local", "as_written": _hhmm(nt)},
                {"name": "max_consecutive_night_duties", "value": bound,
                 "unit": "count", "relation": "max",
                 "as_written": str(bound)}],
            "logic": {
                "subject": "duty",
                "condition": [
                    {"predicate": "starts_in",
                     "from_param": "night_duty_start",
                     "to_param": "night_duty_end",
                     "as_written": "с 22:00 до 06:00"},
                    {"predicate": "consecutive", "unit": "duty",
                     "param": "max_consecutive_night_duties",
                     "as_written": "подряд"}],
                "demand": {"kind": "count_within", "relation": "max",
                           "param": "max_consecutive_night_duties",
                           "as_written": "не более"},
                "source_params": ["night_duty_start", "night_duty_end",
                                  "max_consecutive_night_duties"],
                "enforce": "required"}}


def _roll_rule(need=2160, period=10080, unit="duration_minutes"):
    return {"id": "R-W", "title": "Еженедельный отдых",
            "statement": f"в каждые 7 суток отдых не менее {need} мин",
            "zone": "ROSTERING", "kind": "HARD",
            "sources": [{"sid": "S1", "anchor": "п.4"}],
            "params": [
                {"name": "weekly_rest_required", "value": need,
                 "unit": "duration_minutes", "relation": "min",
                 "as_written": _hhmm(need)},
                {"name": "weekly_rest_period", "value": period,
                 "unit": unit, "relation": "window",
                 "as_written": (_hhmm(period) if unit == "duration_minutes"
                                else str(period))}],
            "logic": {
                "subject": "rest",
                "condition": [{"predicate": "longer_than",
                               "param": "weekly_rest_required",
                               "as_written": f"не менее {need} мин"}],
                "demand": {"kind": "exists_universe", "subject": "rest",
                           "universe": EMP_X, "as_written": "в каждом окне"},
                "window": {"kind": "rolling", "unit": "day",
                           "param": "weekly_rest_period"},
                "source_params": ["weekly_rest_required",
                                  "weekly_rest_period"],
                "enforce": "required"}}


# ── Валидация шаблонов: fail-closed ──

def test_validate_spec_refuses_bad_params_and_missing_columns():
    ds = _ds([_row("EX-1", "2030-01-01T22:00", "2030-01-02T02:00")])
    assert dc.validate_spec(_night_spec(), ds, {}) is None
    assert dc.validate_spec(_roll_spec(), ds, {}) is None
    assert "0..1439" in dc.validate_spec(_night_spec(nf=1500), ds, {})
    assert "0..1439" in dc.validate_spec(_night_spec(nt=-1), ds, {})
    assert "0..1439" in dc.validate_spec(_night_spec(nf=22.0), ds, {})
    assert "окно ночи пусто" in dc.validate_spec(
        _night_spec(nf=600, nt=600), ds, {})
    assert "max_consecutive не целое ≥ 0" in dc.validate_spec(
        _night_spec(n=-1), ds, {})
    assert "max_consecutive не целое ≥ 0" in dc.validate_spec(
        _night_spec(n=True), ds, {})
    assert "window_days не целое ≥ 1" in dc.validate_spec(
        _roll_spec(days=0), ds, {})
    assert "min_rest_min не целое ≥ 1" in dc.validate_spec(
        _roll_spec(need="36:00"), ds, {})
    assert "невыполнимо" in dc.validate_spec(
        _roll_spec(days=1, need=1441), ds, {})
    # Лист и колонки обязаны существовать.
    sp = _night_spec()
    sp["params"]["employee_column"] = "Нет такой"
    assert "колонки" in dc.validate_spec(sp, ds, {})
    sp2 = _roll_spec()
    sp2["params"]["sheet"] = "SOL/Назначения"
    assert "нет в наборе данных" in dc.validate_spec(sp2, ds, {})
    # Нет обязательного параметра.
    sp3 = _roll_spec()
    del sp3["params"]["min_rest_min"]
    assert "нет параметра" in dc.validate_spec(sp3, ds, {})


# ── Исполнитель «серия-ночных-смен» ──

def test_night_series_executor_wrap_and_reset():
    rows = [
        # EX-1: три ночных подряд (22:00, 23:30, 22:00) — нарушение;
        # строки нарочно вперемешку — исполнитель сортирует по времени.
        _row("EX-1", "2030-01-03T22:00", "2030-01-04T02:00"),
        _row("EX-1", "2030-01-01T22:00", "2030-01-02T02:00"),
        _row("EX-1", "2030-01-02T23:30", "2030-01-03T03:30"),
        # EX-2: две ночных, дневная, две ночных — ночных 4, но подряд ≤ 2.
        _row("EX-2", "2030-01-01T22:00", "2030-01-02T02:00"),
        _row("EX-2", "2030-01-02T22:00", "2030-01-03T02:00"),
        _row("EX-2", "2030-01-03T08:00", "2030-01-03T12:00"),
        _row("EX-2", "2030-01-03T22:00", "2030-01-04T02:00"),
        _row("EX-2", "2030-01-04T22:00", "2030-01-05T02:00"),
        # EX-3: начало после полуночи (02:00) — ночь при окне через
        # полночь [22:00, 06:00); три подряд — нарушение.
        _row("EX-3", "2030-01-01T02:00", "2030-01-01T06:00"),
        _row("EX-3", "2030-01-02T02:00", "2030-01-02T06:00"),
        _row("EX-3", "2030-01-03T02:00", "2030-01-03T06:00"),
        # EX-4: начало ровно в «до» (06:00) окну не принадлежит —
        # серия прервана.
        _row("EX-4", "2030-01-01T22:00", "2030-01-02T02:00"),
        _row("EX-4", "2030-01-02T22:00", "2030-01-03T02:00"),
        _row("EX-4", "2030-01-03T06:00", "2030-01-03T10:00"),
        _row("EX-4", "2030-01-03T22:00", "2030-01-04T02:00"),
    ]
    n, finds = dc.run_spec(_night_spec(), _ds(rows))
    assert n == len(rows)
    assert [f["target"]["row"] for f in finds] == [3, 10]
    f1 = finds[0]
    assert f1["target"] == {"sid": "SOL", "sheet": "Смены", "row": 3,
                            "column": "Начало"}
    assert "3 подряд при пределе 2" in f1["evidence"]
    assert "EX-1" in f1["evidence"] and "[22:00, 06:00)" in f1["evidence"]
    assert "больше предела 2" in f1["text"]
    # Окно без перехода через полночь [00:00, 05:00): 00:00 — ночь,
    # 05:00 — уже нет; предел 0 — любая ночная нарушает.
    rows2 = [_row("EX-5", "2030-01-01T05:00", "2030-01-01T09:00"),
             _row("EX-6", "2030-01-01T00:00", "2030-01-01T04:00")]
    _n, finds2 = dc.run_spec(_night_spec(nf=0, nt=300, n=0), _ds(rows2))
    assert [f["target"]["row"] for f in finds2] == [3]


def test_night_series_executor_fail_closed():
    with pytest.raises(dc.DataChecksError, match="универсум пуст"):
        dc.run_spec(_night_spec(), _ds([]))
    with pytest.raises(dc.DataChecksError, match="не читаются"):
        dc.run_spec(_night_spec(), _ds([
            _row("EX-1", "2030-01-01T22:00", "когда-нибудь")]))
    with pytest.raises(dc.DataChecksError, match="не читаются"):
        dc.run_spec(_night_spec(), _ds([
            _row("EX-1", "2030-01-02T22:00", "2030-01-01T02:00")]))
    with pytest.raises(dc.DataChecksError, match="без сотрудника"):
        dc.run_spec(_night_spec(), _ds([
            _row("  ", "2030-01-01T22:00", "2030-01-02T02:00")]))


# ── Исполнитель «отдых-в-скользящем-окне» ──

def test_rolling_rest_executor_boundaries_and_windows():
    rows = [
        # EX-1: два дня 20:00–22:00 — лучший отдых 1320 мин.
        _row("EX-1", "2030-01-01T20:00", "2030-01-01T22:00"),
        _row("EX-1", "2030-01-02T20:00", "2030-01-02T22:00"),
        # EX-2: отдых от начала первых суток данных до первой смены —
        # 1380 мин (граница данных считается отдыхом).
        _row("EX-2", "2030-01-01T23:00", "2030-01-01T23:30"),
        _row("EX-2", "2030-01-02T12:00", "2030-01-02T23:59"),
    ]
    _n, f1440 = dc.run_spec(_roll_spec(days=2, need=1440), _ds(rows))
    assert [f["target"]["row"] for f in f1440] == [2, 4]
    assert f1440[0]["target"] == {"sid": "SOL", "sheet": "Смены",
                                  "row": 2, "column": "Начало"}
    assert "2030-01-01–2030-01-02" in f1440[0]["evidence"]
    assert "лучший промежуток отдыха 1320 мин" in f1440[0]["evidence"]
    _n, f1380 = dc.run_spec(_roll_spec(days=2, need=1380), _ds(rows))
    assert [f["target"]["row"] for f in f1380] == [2]      # EX-2 спасён
    _n, f1381 = dc.run_spec(_roll_spec(days=2, need=1381), _ds(rows))
    assert [f["target"]["row"] for f in f1381] == [2, 4]
    # Несколько окон: три дня 08:00–20:00 при окне 2 суток — оба окна
    # нарушены, адрес — первая смена, начатая в окне.
    rows3 = [_row("EX-3", f"2030-01-0{d}T08:00", f"2030-01-0{d}T20:00")
             for d in (1, 2, 3)]
    _n, f3 = dc.run_spec(_roll_spec(days=2, need=1440), _ds(rows3))
    assert [f["target"]["row"] for f in f3] == [2, 3]
    assert "2030-01-02–2030-01-03" in f3[1]["evidence"]
    # Соблюдение: выходной день посреди — отдых 36 часов.
    rows4 = [_row("EX-4", "2030-01-01T08:00", "2030-01-01T20:00"),
             _row("EX-4", "2030-01-03T08:00", "2030-01-03T20:00")]
    _n, f4 = dc.run_spec(_roll_spec(days=3, need=2160), _ds(rows4))
    assert f4 == []


def test_rolling_rest_executor_data_boundary_fail_closed():
    # Диапазон данных короче окна — окон нет: честный отказ.
    one_day = [_row("EX-1", "2030-01-01T08:00", "2030-01-01T20:00")]
    with pytest.raises(dc.DataChecksError, match="универсум пуст"):
        dc.run_spec(_roll_spec(days=2, need=600), _ds(one_day))
    with pytest.raises(dc.DataChecksError, match="универсум пуст"):
        dc.run_spec(_roll_spec(days=2, need=600), _ds([]))
    # Сотрудник с диапазоном короче окна не судится, но лист с другим
    # сотрудником, у кого окно есть, — судится.
    two = one_day + [_row("EX-2", "2030-01-01T08:00", "2030-01-01T20:00"),
                     _row("EX-2", "2030-01-02T08:00", "2030-01-02T20:00")]
    _n, finds = dc.run_spec(_roll_spec(days=2, need=1440), _ds(two))
    assert [f["target"]["row"] for f in finds] == [3]
    with pytest.raises(dc.DataChecksError, match="не читаются"):
        dc.run_spec(_roll_spec(), _ds([
            _row("EX-1", "01.01.2030 08:00", "01.01.2030 25:00"),
            _row("EX-1", "02.01.2030 08:00", "02.01.2030 20:00")]))


# ── Перевод logic → шаблон и пары ──

def test_night_logic_translates_and_pair_passes_gate():
    spec, why = lp.logic_to_check_spec(_night_rule())
    assert why == "" and spec["template"] == "серия-ночных-смен"
    assert spec["params"] == {"sheet": "SOL/Смены",
                              "employee_column": "Табельный номер",
                              "start_column": "Начало",
                              "end_column": "Конец",
                              "night_from_min": 1320, "night_to_min": 360,
                              "max_consecutive": 2}
    assert spec["basis"] == {"rule": "R-N"}
    assert spec == lp.logic_to_check_spec(_night_rule())[0]   # детерминизм
    for rule in (_night_rule(), _night_rule(bound=0),
                 _night_rule(bound=3, nf=0, nt=300),
                 _night_rule(bound=1, nf=1320, nt=0)):
        pair, why = lp.generate_pair(rule)
        assert pair is not None, why
        assert set(pair) == {"dataset_bad", "dataset_good", "spec"}
        assert pair["spec"]["basis"] == {"rule": "R-N"}
        src = pair["dataset_bad"]["sources"][0]
        assert src["sid"] == "SOL" and src["file"] == "logic-example.json"
        tab = pair["dataset_bad"]["tables"]["SOL"]["Смены"]
        assert tab["columns"] == COLS
        assert src["rows_seen"] == len(tab["rows"])
        _n, bad = dc.run_spec(pair["spec"], pair["dataset_bad"])
        _n, good = dc.run_spec(pair["spec"], pair["dataset_good"])
        assert len(bad) == 1 and good == [], rule["params"]
        assert lp.check_pair(pair, "R-N") == []
        # Пара сериализуема в JSON без потерь (секция logic-examples).
        assert json.loads(json.dumps(pair, ensure_ascii=False)) == pair
    # Соблюдающее решение при пределе 2 несёт столько же ночных, сколько
    # нарушающее (3), но серия прервана дневной — пара различает
    # «подряд» и «всего».
    pair, _ = lp.generate_pair(_night_rule())
    assert len(pair["dataset_bad"]["tables"]["SOL"]["Смены"]["rows"]) == 3
    assert len(pair["dataset_good"]["tables"]["SOL"]["Смены"]["rows"]) == 4


def test_rolling_logic_translates_and_pair_passes_gate():
    spec, why = lp.logic_to_check_spec(_roll_rule())
    assert why == "" and spec["template"] == "отдых-в-скользящем-окне"
    assert spec["params"] == {"sheet": "SOL/Смены",
                              "employee_column": "Табельный номер",
                              "start_column": "Начало",
                              "end_column": "Конец",
                              "window_days": 7, "min_rest_min": 2160}
    # Окно сутками (count) читается как сутки.
    spec_c, why_c = lp.logic_to_check_spec(_roll_rule(period=7, unit="count"))
    assert why_c == "" and spec_c["params"]["window_days"] == 7
    for rule in (_roll_rule(), _roll_rule(need=9000),
                 _roll_rule(need=600), _roll_rule(need=2),
                 _roll_rule(need=1380, period=1440),
                 _roll_rule(need=2700, period=2880),
                 _roll_rule(need=720, period=3, unit="count")):
        pair, why = lp.generate_pair(rule)
        assert pair is not None, (rule["params"], why)
        assert set(pair) == {"dataset_bad", "dataset_good", "spec"}
        tab = pair["dataset_bad"]["tables"]["SOL"]["Смены"]
        assert tab["columns"] == COLS
        assert pair["dataset_bad"]["sources"][0]["rows_seen"] == \
            len(tab["rows"])
        _n, bad = dc.run_spec(pair["spec"], pair["dataset_bad"])
        _n, good = dc.run_spec(pair["spec"], pair["dataset_good"])
        assert len(bad) == 1 and good == [], rule["params"]
        assert lp.check_pair(pair, "R-W") == []
        assert json.loads(json.dumps(pair, ensure_ascii=False)) == pair
    # Пара не строится — честная причина, не догадка.
    pair, why = lp.generate_pair(_roll_rule(need=1, period=1440))
    assert pair is None and "меньше 2 минут" in why
    pair, why = lp.generate_pair(_roll_rule(need=1440, period=1440))
    assert pair is None and "почти равен окну" in why
    pair, why = lp.generate_pair(_roll_rule(need=2879, period=2880))
    assert pair is None and "почти равен окну" in why


def test_deviating_shapes_refused_with_reason():
    # Ночная серия: почти-формы — именованная причина, пары нет.
    r = _night_rule()
    r["logic"]["condition"][1]["unit"] = "day"
    spec, why = lp.logic_to_check_spec(r)
    assert spec is None and "по сменам" in why
    pair, why2 = lp.generate_pair(r)
    assert pair is None and why2 == why
    r = _night_rule()
    r["logic"]["condition"][1]["param"] = "night_duty_end"
    assert "разные параметры" in lp.logic_to_check_spec(r)[1]
    r = _night_rule()
    r["logic"]["demand"]["relation"] = "min"
    assert "вне формы" in lp.logic_to_check_spec(r)[1]
    r = _night_rule()
    r["logic"]["demand"]["universe"] = "employee×month"
    assert "не предусмотрены" in lp.logic_to_check_spec(r)[1]
    r = _night_rule()
    r["logic"]["condition"] = r["logic"]["condition"][:1]
    assert "ровно starts_in и consecutive" in lp.logic_to_check_spec(r)[1]
    r = _night_rule(nf=600, nt=600)
    assert "окно ночи пусто" in lp.logic_to_check_spec(r)[1]
    r = _night_rule()
    r["params"][0]["value"] = 1440
    assert "0..1439" in lp.logic_to_check_spec(r)[1]
    r = _night_rule()
    r["logic"]["subject"] = "assignment"
    assert "только duty" in lp.logic_to_check_spec(r)[1]
    # Отдых в окне: почти-формы.
    r = _roll_rule()
    r["logic"]["condition"].append({"predicate": "at_base",
                                    "as_written": "в базе"})
    assert "ровно одно longer_than" in lp.logic_to_check_spec(r)[1]
    r = _roll_rule()
    r["logic"]["window"]["unit"] = "duty"
    assert "unit=day" in lp.logic_to_check_spec(r)[1]
    r = _roll_rule()
    r["logic"]["demand"]["universe"] = "employee×day"
    assert "только employee×rolling_window" in \
        lp.logic_to_check_spec(r)[1]
    r = _roll_rule()
    r["logic"]["demand"]["kind"] = "count_within"
    r["logic"]["demand"]["relation"] = "min"
    r["logic"]["demand"]["param"] = "weekly_rest_required"
    assert "только exists_universe" in lp.logic_to_check_spec(r)[1]
    r = _roll_rule()
    r["logic"]["subject"] = "duty"
    assert "только rest" in lp.logic_to_check_spec(r)[1]
    assert "не кратно суткам" in lp.logic_to_check_spec(
        _roll_rule(period=10000))[1]
    assert "невыполнимо" in lp.logic_to_check_spec(
        _roll_rule(need=10081))[1]
    assert "единица окна" in lp.logic_to_check_spec(
        _roll_rule(period=7, unit="time_of_day_minutes"))[1]
    r = _roll_rule()
    r["params"] = r["params"][1:]                  # порога нет
    assert "не даёт целых минут" in lp.logic_to_check_spec(r)[1]
    # Прежние отказы первой волны не задеты: чужое условие без
    # очертания формы — прежняя причина.
    r = _night_rule()
    r["logic"]["condition"] = [{"predicate": "at_base",
                                "as_written": "в базе"}]
    spec, why = lp.logic_to_check_spec(r)
    assert spec is None and "condition-атомы" in why


def test_package_carries_pairs_for_history_forms(tmp_path):
    """Цель: правило с такой логикой едет с парой и не попадает в
    manifest.logic_unproven; гейт пакета пары принимает."""
    from cdpm import export_task_package as tp
    from cdpm.gates import gate_task_package as gate
    doc = {"meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
           "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
           "rules": [_night_rule(), _roll_rule()],
           "questions": [], "objectives": []}
    assert m.validate_model(doc) == [], m.validate_model(doc)[:3]
    run = tmp_path / "run-9101"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    files = tp.build_package(run)
    manifest = json.loads(files["manifest.json"])
    examples = json.loads(files["logic-examples.json"])
    assert set(examples) == {"R-N", "R-W"}
    assert "logic_unproven" not in manifest
    assert examples["R-N"]["spec"]["template"] == "серия-ночных-смен"
    assert examples["R-W"]["spec"]["template"] == "отдых-в-скользящем-окне"
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(files))
    assert gate.check(pkg)[0] == []
