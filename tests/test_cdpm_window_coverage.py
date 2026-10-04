# -*- coding: utf-8 -*-
"""Ревизия 1.18.3 (план единой версии, §3.8 «зона CDPM», граница
H12/K-15): статус «частично прогнана» и адресное покрытие окон
`window_coverage` в отчёте данных.

Охраняемое:
  1. валидатор контракта не верит счётчикам на слово — сверяет их с
     адресным списком окон; каждая подмена — названный дефект;
  2. непрерывное доказательство (critical_cells) пересчитывается из
     интервалов: ячейки покрывают область окончаний ровно один раз,
     неизвестная ячейка с исходом «нарушение» несёт нижнюю границу;
  3. гейт сертификата: `certified = true` при частично прогнанной
     проверке — дефект; находка по частично прогнанной проверке —
     допустима; правило непрерывного окна без непрерывного доказательства
     у сертификата с такими доказательствами читается как «не прогнана»;
  4. производитель (формы истории): у сотрудника без истории и без
     решения анкеты окно на границе среза неизвестно → «частично
     прогнана» с адресом; решение анкеты «продолжить» → окно доказано
     допущением нулевой истории, названным словами.
"""
from __future__ import annotations

import copy

import pytest

from cdpm import answers as ans
from cdpm import data_checks as dc
from cdpm import data_report as dr
from cdpm import history_questions as hq
from cdpm import history_section as hs
from cdpm.gates import gate_certify
from cdpm.sample import sample_model

SRC = {"sid": "D1", "file": "book.xlsx", "sha256": "a" * 64, "rows_seen": 4}


# ── Валидатор покрытия окон (granularity = windows) ──

def _window(emp, start, end, status="evaluated", outcome="pass",
            reason="доказано данными"):
    return {"employee": emp, "start_inclusive": start, "end_exclusive": end,
            "status": status, "outcome": outcome, "reason": reason}


def _coverage_partial() -> dict:
    return {"expected": 2, "evaluated": 1, "unknown": 1,
            "granularity": "windows", "expected_unit": "windows",
            "windows": [
                _window("E1", "2026-02-27T00:00", "2026-03-02T00:00"),
                _window("E2", "2026-02-27T00:00", "2026-03-02T00:00",
                        status="unknown", outcome=None,
                        reason="у сотрудника нет истории и решения анкеты нет")]}


def _doc(status="частично прогнана", coverage=None, rows_checked=4,
         reason="окна на границе среза не доказаны у E2") -> dict:
    check = {"id": "C-x-0", "basis": {"builtin": "аномалия"},
             "statement": "отдых в окне", "unit": "сотрудник × окно",
             "status": status, "rows_checked": rows_checked}
    if status != "прогнана":
        check["reason"] = reason
    if coverage is not None:
        check["window_coverage"] = coverage
    doc = {"customer": "Т", "sources": [SRC], "checks": [check],
           "findings": []}
    doc["counters"] = dr.metrics_of(doc)
    return doc


def test_partial_status_is_in_contract_and_clean_partial_report_passes():
    assert "частично прогнана" in dr.CHECK_STATUSES
    doc = _doc(coverage=_coverage_partial())
    assert dr.validate_report(doc) == []
    # «прогнана» с покрытием без неизвестных — тоже чисто
    cov = _coverage_partial()
    cov["windows"][1].update(status="evaluated", outcome="pass",
                             reason="допущение нулевой истории, решение анкеты Q")
    cov.update(evaluated=2, unknown=0)
    assert dr.validate_report(_doc(status="прогнана", coverage=cov)) == []
    # адресные окна позволяют rows_checked = 0 (окна судятся интервалами)
    assert dr.validate_report(_doc(status="прогнана", coverage=cov,
                                   rows_checked=0)) == []


def test_partial_without_coverage_or_without_reason_is_a_defect():
    probs = dr.validate_report(_doc(coverage=None))
    assert any("частичная проверка без window_coverage" in p for p in probs)
    probs = dr.validate_report(_doc(coverage=_coverage_partial(), reason=""))
    assert any("без названной причины" in p for p in probs)


@pytest.mark.parametrize("mutate, needle", [
    # expected ≠ evaluated + unknown
    (lambda c: c.update(expected=3), "expected не равен evaluated + unknown"),
    # expected ≠ len(windows)
    (lambda c: c.update(expected=3, evaluated=2),
     "expected не равен числу адресов windows"),
    # повтор адреса окна
    (lambda c: c["windows"].__setitem__(
        1, dict(c["windows"][0], status="unknown", outcome=None,
                reason="неизвестно")), "повтор адреса окна"),
    # окно без причины
    (lambda c: c["windows"][1].update(reason="  "),
     "окно без причины/доказательства"),
    # подмена счётчиков при том же неполном покрытии
    (lambda c: c.update(evaluated=2, unknown=0, expected=2),
     "счётчики evaluated/unknown расходятся с адресами"),
    # обратные границы окна
    (lambda c: c["windows"][0].update(end_exclusive="2026-02-26T00:00"),
     "неверные границы"),
    # статус окна вне перечня
    (lambda c: c["windows"][0].update(status="maybe"),
     "неизвестный статус окна"),
])
def test_window_coverage_invariants_catch_each_defect(mutate, needle):
    cov = _coverage_partial()
    mutate(cov)
    probs = dr.validate_report(_doc(coverage=cov))
    assert any(needle in p for p in probs), probs


def test_unknown_windows_require_partial_and_partial_requires_unknown():
    # unknown > 0 при статусе «прогнана»
    probs = dr.validate_report(_doc(status="прогнана",
                                    coverage=_coverage_partial()))
    assert any("требуют статуса «частично прогнана»" in p for p in probs)
    # «частично прогнана» при unknown = 0
    cov = _coverage_partial()
    cov["windows"][1].update(status="evaluated", outcome="pass")
    cov.update(evaluated=2, unknown=0)
    probs = dr.validate_report(_doc(coverage=cov))
    assert any("частичная проверка без неизвестных окон" in p for p in probs)
    # счётчики совпадают между собой, но врут против адресов — ловится
    cov2 = _coverage_partial()
    cov2.update(evaluated=0, unknown=2)
    probs = dr.validate_report(_doc(coverage=cov2))
    assert any("расходятся с адресами" in p for p in probs)


def test_finding_may_reference_partial_check_but_not_unrun_one():
    doc = _doc(coverage=_coverage_partial())
    doc["findings"].append({
        "id": "F-0001", "check": "C-x-0", "severity": "КРИТИЧЕСКОЕ",
        "class": "аномалия", "target": {"sid": "D1", "sheet": "Смены", "row": 2},
        "text": "нет отдыха", "evidence": "D1!Смены строка 2"})
    doc["counters"] = dr.metrics_of(doc)
    assert dr.validate_report(doc) == []
    doc["checks"][0].update(status="не прогнана", rows_checked=0)
    doc["checks"][0].pop("window_coverage")
    doc["counters"] = dr.metrics_of(doc)
    probs = dr.validate_report(doc)
    assert any("не «прогнана» — находка невозможна" in p for p in probs)


def test_blocked_rule_forbids_partial_status_too():
    svod = {"rules": [{"id": "R-1", "kind": "HARD", "blockers": ["Q-1"]}]}
    doc = _doc(coverage=_coverage_partial())
    doc["checks"][0]["basis"] = {"rule": "R-1"}
    probs = dr.validate_report(doc, svod)
    assert any("блокирована вопросом" in p and "«частично прогнана»" in p
               for p in probs)


def test_render_names_partial_checks_and_coverage_counts():
    md = dr.render_data_report(_doc(coverage=_coverage_partial()))
    assert "частично прогнано: 1" in md
    assert "окон: всего 2, доказано 1, неизвестно 1" in md
    # отчёт без частичных проверок — прежняя строка сводки байт-в-байт
    md0 = dr.render_data_report(dr.sample_data_report())
    assert "частично прогнано" not in md0
    assert "не применимо или не прогнано: 2 (причины — в таблице проверок)" in md0


# ── Непрерывное доказательство (critical_cells) ──

def _continuous_doc() -> dict:
    """Один сотрудник, окно 24 ч, отдых 10 ч. Область окончаний —
    сутки 02.01; известная занятость 01.01 00:00 – 03.01 00:00,
    доказанный отдых 01.01 06:00–20:00 (14 ч). Проходящие окончания:
    [02.01 00:00, 02.01 10:00]; полностью известные: [02.01 00:00,
    03.01 00:00]. Ячейки: 3 точки и 2 открытых интервала — область
    покрыта ровно один раз."""
    lo, mid, hi = "2026-01-02T00:00", "2026-01-02T10:00", "2026-01-03T00:00"

    def cell(a, b, kind, outcome):
        point = kind == "point"
        win_start = {"2026-01-02T00:00": "2026-01-01T00:00",
                     "2026-01-02T10:00": "2026-01-01T10:00",
                     "2026-01-03T00:00": "2026-01-02T00:00"}[a]
        return {"employee": "E1",
                "start_inclusive": win_start,
                "end_exclusive": b if not point else a,
                "ending_domain": {"start": a, "end": b, "kind": kind,
                                  "start_closed": point, "end_closed": point},
                "status": "evaluated", "outcome": outcome,
                "reason": "пересчёт по интервалам"}
    cells = [cell(lo, lo, "point", "pass"),
             cell(lo, mid, "open_interval", "pass"),
             cell(mid, mid, "point", "pass"),
             cell(mid, hi, "open_interval", "violation"),
             cell(hi, hi, "point", "violation")]
    # у точечной ячейки окно [t−W, t): начало на сутки раньше
    for c in cells:
        if c["ending_domain"]["kind"] == "point":
            c["end_exclusive"] = c["ending_domain"]["end"]
    cov = {"expected": 5, "evaluated": 5, "unknown": 0,
           "granularity": "continuous_critical_cells",
           "expected_unit": "critical_cells",
           "ending_domain": {"start_inclusive": lo, "end_inclusive": hi},
           "employee_proofs": {"E1": {
               "window_minutes": 1440, "required_rest_minutes": 600,
               "occupancy_basis": "normative_duties",
               "known_occupancy_intervals": [
                   {"start_inclusive": "2026-01-01T00:00",
                    "end_exclusive": "2026-01-03T00:00"}],
               "proven_rest_intervals": [
                   {"start_inclusive": "2026-01-01T06:00",
                    "end_exclusive": "2026-01-01T20:00"}],
               "passing_window_end_spans": [
                   {"start_inclusive": lo, "end_inclusive": mid}],
               "fully_known_window_end_spans": [
                   {"start_inclusive": lo, "end_inclusive": hi}]}},
           "windows": cells}
    return _doc(status="прогнана", coverage=cov, rows_checked=0)


def test_continuous_proof_positive_example_passes_contract():
    doc = _continuous_doc()
    assert dr.validate_report(doc) == [], dr.validate_report(doc)
    # адреса ячеек уникальны и обе формы ячеек присутствуют
    cells = doc["checks"][0]["window_coverage"]["windows"]
    assert len({(c["start_inclusive"], c["end_exclusive"]) for c in cells}) == 5
    assert {c["ending_domain"]["kind"] for c in cells} == {"point", "open_interval"}


def _continuous_probs(mutate) -> list[str]:
    doc = _continuous_doc()
    mutate(doc["checks"][0])
    doc["counters"] = dr.metrics_of(doc)
    return dr.validate_report(doc)


def test_continuous_proof_skipped_entirely_is_a_defect():
    # раздел granularity=continuous_critical_cells без employee_proofs
    probs = _continuous_probs(lambda c: c["window_coverage"].pop("employee_proofs"))
    assert any("непрерывное доказательство некорректно" in p for p in probs)
    # и без ending_domain
    probs = _continuous_probs(lambda c: c["window_coverage"].pop("ending_domain"))
    assert any("непрерывное доказательство некорректно" in p for p in probs)
    # неверная единица
    probs = _continuous_probs(lambda c: c["window_coverage"].update(expected_unit="windows"))
    assert any("не critical_cells" in p for p in probs)


def _drop_cell(c):
    cov = c["window_coverage"]
    cov["windows"].pop(3)                     # разрыв: нет (10:00, 03.01)
    cov.update(expected=4, evaluated=4)


def _overlap_cells(c):
    cov = c["window_coverage"]
    # перекрытие: открытая ячейка (00:00, 10:00) растянута до конца
    # области и накрывает точку 10:00 и соседний интервал
    cov["windows"][1]["ending_domain"]["end"] = "2026-01-03T00:00"
    cov["windows"][1]["end_exclusive"] = "2026-01-03T00:00"


def _duplicate_cell(c):
    cov = c["window_coverage"]
    cov["windows"].append(copy.deepcopy(cov["windows"][1]))
    cov.update(expected=6, evaluated=6)


@pytest.mark.parametrize("mutate", [_drop_cell, _overlap_cells, _duplicate_cell])
def test_continuous_cells_must_cover_domain_exactly_once(mutate):
    probs = _continuous_probs(mutate)
    assert any("не покрывают всю область окончаний точно один раз" in p
               for p in probs), probs


def test_continuous_proof_arithmetic_is_recomputed_not_trusted():
    # объединения окон в доказательстве не равны пересчёту по интервалам
    def lie(c):
        c["window_coverage"]["employee_proofs"]["E1"]["passing_window_end_spans"] = [
            {"start_inclusive": "2026-01-02T00:00", "end_inclusive": "2026-01-03T00:00"}]
    probs = _continuous_probs(lie)
    assert any("не равны пересчёту по интервалам" in p for p in probs)
    # доказанный отдых вне известных интервалов
    def outside(c):
        c["window_coverage"]["employee_proofs"]["E1"]["proven_rest_intervals"] = [
            {"start_inclusive": "2025-12-31T06:00", "end_exclusive": "2025-12-31T20:00"}]
    probs = _continuous_probs(outside)
    assert any("вне известных интервалов" in p for p in probs)
    # исход ячейки противоречит интервалам
    def wrong_outcome(c):
        c["window_coverage"]["windows"][0]["outcome"] = "violation"
    probs = _continuous_probs(wrong_outcome)
    assert any("исход ячейки не доказан" in p for p in probs)


def test_unknown_cell_with_violation_needs_proven_lower_bound():
    def unknown_cell(c):
        cov = c["window_coverage"]
        cov["windows"][4].update(status="unknown", outcome="violation")
        cov.update(evaluated=4, unknown=1)
        c.update(status="частично прогнана", reason="одна ячейка неизвестна")
    probs = _continuous_probs(unknown_cell)
    assert any("не несёт доказанной нижней границы нарушения" in p
               for p in probs), probs
    # с нижней границей (основание занятости не нормативное, ячейка
    # полностью известна и не проходит) — допустимо
    def bounded(c):
        unknown_cell(c)
        cov = c["window_coverage"]
        cov["windows"][4]["lower_bound_violation"] = True
        cov["employee_proofs"]["E1"]["occupancy_basis"] = "flights_only"
        # прочие ячейки при ненормативном основании evaluated быть не могут
        for w in cov["windows"][:4]:
            w.update(status="unknown", outcome=None)
        cov.update(evaluated=0, unknown=5)
    probs = _continuous_probs(bounded)
    assert probs == [], probs


# ── Гейт сертификата ──

def _svod_hard(extra_rules=()):
    return {"rules": [{"id": "R-1", "kind": "HARD"}, *extra_rules]}


def _certificate(checks, findings=(), certified=False):
    report = {"customer": "Т", "sources": [SRC], "checks": checks,
              "findings": list(findings)}
    report["counters"] = dr.metrics_of(report)
    n_run = sum(1 for c in checks if c["status"] == "прогнана")
    report["certificate"] = {
        "findings": len(findings),
        "violated_rules": sorted({r for f in findings for r in f.get("rule_refs", [])}),
        "certified": certified, "checks_run": n_run,
        "checks_not_run": len(checks) - n_run,
        "rules_considered": len({(c.get("basis") or {}).get("rule")
                                 for c in checks if (c.get("basis") or {}).get("rule")})}
    return report


def _partial_check(rule="R-1", cid="C-R-1-0"):
    return {"id": cid, "basis": {"rule": rule}, "statement": "отдых в окне",
            "unit": "сотрудник × окно", "status": "частично прогнана",
            "rows_checked": 4, "reason": "окно E2 не доказано",
            "window_coverage": _coverage_partial()}


def test_gate_refuses_certified_true_with_partial_check():
    report = _certificate([_partial_check()], certified=True)
    probs, _ = gate_certify.check(_svod_hard(), report)
    assert any("certified противоречит телу" in p for p in probs), probs
    # честный сертификат: certified=false — гейт чист
    report = _certificate([_partial_check()], certified=False)
    assert gate_certify.check(_svod_hard(), report)[0] == []
    # покрытие с неизвестными окнами при статусе «прогнана» — дефект
    # контракта и неполное доказательство разом
    bad = _partial_check()
    bad.update(status="прогнана")
    bad.pop("reason")
    report = _certificate([bad], certified=True)
    probs, _ = gate_certify.check(_svod_hard(), report)
    assert any("certified противоречит телу" in p for p in probs)
    assert any("требуют статуса «частично прогнана»" in p for p in probs)


def test_gate_accepts_finding_on_partial_check():
    finding = {"id": "F-0001", "check": "C-R-1-0", "severity": "КРИТИЧЕСКОЕ",
               "class": "нарушение правила", "rule_refs": ["R-1"],
               "target": {"sid": "D1", "sheet": "Смены", "row": 2},
               "text": "в окне нет отдыха", "evidence": "D1!Смены строка 2"}
    report = _certificate([_partial_check()], findings=[finding], certified=False)
    probs, counters = gate_certify.check(_svod_hard(), report)
    assert probs == [], probs
    assert counters["findings"] == 1
    # находка по «не прогнана» — по-прежнему невозможна
    unrun = {"id": "C-R-1-0", "basis": {"rule": "R-1"}, "statement": "отдых",
             "unit": "строка", "status": "не прогнана", "rows_checked": 0,
             "reason": "листа смен нет"}
    report = _certificate([unrun], findings=[finding], certified=False)
    probs, _ = gate_certify.check(_svod_hard(), report)
    assert any("ссылается на непрогнанную проверку" in p for p in probs)


def test_gate_reads_rolling_rest_rule_without_continuous_proof_as_not_run():
    rolling = {"id": "R-W", "kind": "HARD",
               "template_fit": {"category": "A", "family": "rolling_rest"}}
    cont = _continuous_doc()["checks"][0]
    cont.update(id="C-R-1-0", basis={"rule": "R-1"})
    daily = {"id": "C-R-W-0", "basis": {"rule": "R-W"}, "statement": "отдых в окне",
             "unit": "окно", "status": "прогнана", "rows_checked": 5}
    # сертификат с непрерывными доказательствами: суточная форма у
    # правила непрерывного окна — не доказательство
    report = _certificate([cont, daily], certified=True)
    probs, _ = gate_certify.check(_svod_hard([rolling]), report)
    assert any("без непрерывного доказательства" in p and "R-W" in p for p in probs), probs
    assert any("certified противоречит телу" in p for p in probs)
    # omitted proof: доказательство пропущено ЦЕЛИКОМ (ни одного
    # непрерывного раздела в сертификате) — гейт всё равно красный
    # («красить всегда», сигнал COSMOS после появления непрерывной формы)
    report = _certificate([daily, {"id": "C-R-1-0", "basis": {"rule": "R-1"},
                                   "statement": "с", "unit": "строка",
                                   "status": "прогнана", "rows_checked": 2}],
                          certified=True)
    probs, _ = gate_certify.check(_svod_hard([rolling]), report)
    assert any("без непрерывного доказательства" in p and "R-W" in p for p in probs), probs
    assert any("certified противоречит телу" in p for p in probs)


# ── Производитель: формы истории с окнами на границе среза ──

COLS = ["Табельный номер", "Начало", "Конец"]
AS_OF = "01.03.2026 00:00"


def _duty(emp, start, end):
    return {"Табельный номер": emp, "Начало": start, "Конец": end}


def _dataset(history_rows=None) -> dict:
    tables = {
        "Экипажи": {"columns": ["Табельный номер", "База"],
                    "rows": [{"Табельный номер": "E1", "База": "SVG"},
                             {"Табельный номер": "E2", "База": "SVG"}]},
        "Свойства": {"columns": ["ключ", "значение"],
                     "rows": [{"ключ": "planSince", "значение": AS_OF}]},
        "Смены": {"columns": COLS, "rows": [
            # у обоих три ночные смены подряд с начала горизонта (начало
            # 02:00 и 22:00 — в окне ночи [22:00, 06:00)); отдых внутри
            # горизонта до первой смены — 2 ч, между первой и второй —
            # 8 ч: окно из 2 суток, пересекающее срез, без событий до
            # среза не доказывается (норма отдыха 10 ч)
            _duty("E1", "2026-03-01T02:00", "2026-03-01T14:00"),
            _duty("E1", "2026-03-01T22:00", "2026-03-02T10:00"),
            _duty("E1", "2026-03-02T22:00", "2026-03-03T10:00"),
            _duty("E2", "2026-03-01T02:00", "2026-03-01T14:00"),
            _duty("E2", "2026-03-01T22:00", "2026-03-02T10:00"),
            _duty("E2", "2026-03-02T22:00", "2026-03-03T10:00")]},
    }
    if history_rows is not None:
        tables["История"] = {
            "columns": ["Табельный номер", "Последняя смена: конец",
                        "Последняя смена: размах, мин", "Серия ночных смен"],
            "rows": history_rows}
    return {"sources": [SRC], "tables": {"D1": tables}}


SRC = dict(SRC, rows_seen=6)
# Последняя смена истории кончилась 28.02 10:00: отдых до первой смены
# горизонта (01.03 02:00) — 16 ч ≥ 10 ч, окно на границе доказано;
# серия ночных смен на срез — 1.
E1_HISTORY = [{"Табельный номер": "E1", "Последняя смена: конец": "28.02.2026 10:00",
               "Последняя смена: размах, мин": "720", "Серия ночных смен": "1"}]


def _spec(template, cid, **params):
    base = {"sheet": "D1/Смены", "employee_column": "Табельный номер",
            "start_column": "Начало", "end_column": "Конец"}
    base.update(params)
    return {"id": cid, "basis": {"builtin": "аномалия"}, "template": template,
            "params": base, "statement": "форма истории", "unit": "строка",
            "severity": "КРИТИЧЕСКОЕ", "status": "скомпилирована"}


def _specs():
    return [_spec("серия-ночных-смен", "C-night", night_from_min=1320,
                  night_to_min=360, max_consecutive=3),
            _spec("отдых-в-скользящем-окне", "C-rest", window_days=2,
                  min_rest_min=600)]


def _model_with_history_rule():
    doc = sample_model()
    doc["rules"].append({
        "id": "R-H-REST", "kind": "HARD", "zone": "ROSTERING", "title": "Отдых",
        "statement": "отдых дома между сменами не короче 720 минут",
        "sources": [{"sid": "S1", "anchor": "п.9"}],
        "template_fit": {"category": "A", "family": "rest_floors",
                         "registry_rev": "x" * 12, "quote": "отдых дома 720",
                         "params": {"min_rest_home": {"value": 720, "quote": "720"}}}})
    return doc


def test_producer_marks_boundary_windows_partial_when_one_employee_has_no_history():
    ds = _dataset(history_rows=E1_HISTORY)
    doc = dc.build_data_report("Т", ds, _specs())
    assert dr.validate_report(doc) == [], dr.validate_report(doc)
    checks = {c["id"]: c for c in doc["checks"]}
    for cid in ("C-night", "C-rest"):
        c = checks[cid]
        assert c["status"] == "частично прогнана", c
        assert c["rows_checked"] == 6 and "E2" in c["reason"]
        cov = c["window_coverage"]
        assert (cov["expected"], cov["evaluated"], cov["unknown"]) == (2, 1, 1)
        assert cov["granularity"] == "windows" and cov["expected_unit"] == "windows"
        assert cov["horizon_start"] == "2026-03-01T00:00"
        by_emp = {w["employee"]: w for w in cov["windows"]}
        assert by_emp["E1"]["status"] == "evaluated"
        assert "история передана" in by_emp["E1"]["reason"]
        assert "лист «История» (D1), строка 2" in by_emp["E1"]["reason"]
        assert by_emp["E2"]["status"] == "unknown" and by_emp["E2"]["outcome"] is None
        assert "нет истории" in by_emp["E2"]["reason"] and "решения" in by_emp["E2"]["reason"]
        # адреса окон: единица «сотрудник × окно», границы читаемы
        assert len({(w["employee"], w["start_inclusive"], w["end_exclusive"])
                    for w in cov["windows"]}) == 2
    # серия ночных: у E1 перенос 1 + 3 в горизонте = 4 > 3 — доказанное
    # нарушение при частично прогнанной проверке сохраняется; у E2 три
    # ночных горизонта предел не превышают, перенос неизвестен — исхода нет
    night = {w["employee"]: w for w in checks["C-night"]["window_coverage"]["windows"]}
    assert night["E1"]["outcome"] == "violation"
    assert night["E1"]["start_inclusive"] == "2026-03-01T00:00"
    assert night["E1"]["end_exclusive"] == "2026-03-03T10:00"
    finds = [f for f in doc["findings"] if f["check"] == "C-night"]
    assert [f["target"]["row"] for f in finds] == [2]
    assert "4 подряд (3 в горизонте + 1 на срез по истории)" in finds[0]["evidence"]
    # отдых: у E1 отдых от 28.02 10:00 до 01.03 02:00 (16 ч) — окно на
    # границе прошло; у E2 внутри горизонта лучший отдых 8 ч, до среза
    # занятость неизвестна — окно неизвестно; внутри горизонта нарушений нет
    rest = {w["employee"]: w for w in checks["C-rest"]["window_coverage"]["windows"]}
    assert rest["E1"]["outcome"] == "pass" and "прошло 1" in rest["E1"]["reason"]
    assert "отдых до среза 840 мин" in rest["E1"]["reason"]
    assert rest["E1"]["start_inclusive"] == "2026-02-28T00:00"
    assert rest["E1"]["end_exclusive"] == "2026-03-02T00:00"
    assert "неизвестно 1" in rest["E2"]["reason"]
    assert [f for f in doc["findings"] if f["check"] == "C-rest"] == []
    # md-рендер называет частично прогнанные
    assert "частично прогнано: 2" in dr.render_data_report(doc)


def test_producer_continue_partial_decision_proves_windows_by_zero_assumption():
    ds = _dataset(history_rows=E1_HISTORY)
    model = _model_with_history_rule()
    # текущий исход — «частично» (у E1 история есть, у E2 нет)
    q_rest = hs.history_question_id("boundary")      # семья rest_floors
    assert hq.ensure_history_questions(model, {"tables": ds["tables"]}) == [q_rest]
    # до ответа — частично прогнана и со сводом
    doc0 = dc.build_data_report("Т", ds, _specs(), svod=model)
    assert {c["status"] for c in doc0["checks"]} == {"частично прогнана"}
    ans.apply_answers(model, {q_rest: "б"})
    doc = dc.build_data_report("Т", ds, _specs(), svod=model)
    assert dr.validate_report(doc, model) == [], dr.validate_report(doc, model)
    checks = {c["id"]: c for c in doc["checks"]}
    for cid in ("C-night", "C-rest"):
        c = checks[cid]
        assert c["status"] == "прогнана", c
        cov = c["window_coverage"]
        assert (cov["expected"], cov["evaluated"], cov["unknown"]) == (2, 2, 0)
        e2 = next(w for w in cov["windows"] if w["employee"] == "E2")
        assert e2["status"] == "evaluated"
        assert ("допущение нулевой истории, решение анкеты "
                f"{q_rest} (ответ б): continue_partial") in e2["reason"]
    night_e2 = next(w for w in checks["C-night"]["window_coverage"]["windows"]
                    if w["employee"] == "E2")
    assert night_e2["outcome"] == "pass"          # 0 + 3 ≤ 3
    rest_e2 = next(w for w in checks["C-rest"]["window_coverage"]["windows"]
                   if w["employee"] == "E2")
    assert rest_e2["outcome"] == "pass"
    assert "частично прогнано" not in dr.render_data_report(doc)


def test_producer_continue_zero_when_nobody_has_history():
    ds = _dataset()                                    # листа «История» нет
    model = _model_with_history_rule()
    q_rest = hs.history_question_id("boundary")      # семья rest_floors
    assert hq.ensure_history_questions(model, {"tables": ds["tables"]}) == [q_rest]
    doc0 = dc.build_data_report("Т", ds, _specs(), svod=model)
    cov0 = next(c for c in doc0["checks"] if c["id"] == "C-rest")["window_coverage"]
    assert cov0["unknown"] == 2 and doc0["checks"][1]["status"] == "частично прогнана"
    ans.apply_answers(model, {q_rest: "б"})
    doc = dc.build_data_report("Т", ds, _specs(), svod=model)
    assert dr.validate_report(doc, model) == []
    for c in doc["checks"]:
        assert c["status"] == "прогнана"
        assert c["window_coverage"]["unknown"] == 0
        for w in c["window_coverage"]["windows"]:
            assert "continue_zero" in w["reason"] and q_rest in w["reason"]


def test_producer_without_svod_has_no_decision_and_run_spec_stays_two_tuple():
    ds = _dataset()
    n, finds, cov = dc.run_spec_ex(_specs()[1], ds)
    assert n == 6 and finds == [] and cov["unknown"] == 2
    assert dc.run_spec(_specs()[1], ds) == (6, [])
    # без planSince срез — первые сутки листа, и это названо
    ds2 = _dataset()
    ds2["tables"]["D1"].pop("Свойства")
    _n, _f, cov2 = dc.run_spec_ex(_specs()[1], ds2)
    assert cov2["horizon_start"] == "2026-03-01T00:00"
    assert "первые сутки листа" in cov2["horizon_source"]


def test_producer_history_without_needed_field_keeps_window_unknown():
    rows = [{"Табельный номер": "E1", "Последняя смена: конец": "",
             "Последняя смена: размах, мин": "", "Серия ночных смен": "1"},
            {"Табельный номер": "E2", "Последняя смена: конец": "28.02.2026 10:00",
             "Последняя смена: размах, мин": "", "Серия ночных смен": ""}]
    doc = dc.build_data_report("Т", _dataset(history_rows=rows), _specs())
    assert dr.validate_report(doc) == []
    checks = {c["id"]: c for c in doc["checks"]}
    night = {w["employee"]: w for w in checks["C-night"]["window_coverage"]["windows"]}
    rest = {w["employee"]: w for w in checks["C-rest"]["window_coverage"]["windows"]}
    # серия: у E1 есть, у E2 не указана; отдых: у E2 есть конец смены, у E1 нет
    assert night["E1"]["status"] == "evaluated" and night["E2"]["status"] == "unknown"
    assert "серия ночных смен на срез не указана" in night["E2"]["reason"]
    assert rest["E2"]["status"] == "evaluated" and rest["E1"]["status"] == "unknown"
    assert "конец последней смены не указан" in rest["E1"]["reason"]


def test_producer_empty_sheet_stays_not_run():
    ds = _dataset()
    ds["tables"]["D1"]["Смены"]["rows"] = []
    doc = dc.build_data_report("Т", ds, _specs())
    assert {c["status"] for c in doc["checks"]} == {"не прогнана"}
    assert all("универсум пуст" in c["reason"] for c in doc["checks"])
    assert all("window_coverage" not in c for c in doc["checks"])
