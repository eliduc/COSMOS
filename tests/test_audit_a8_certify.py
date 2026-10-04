# -*- coding: utf-8 -*-
"""Аудит 04.09.2026, находка A8: «нарушений не найдено» ≠ «допустимость
установлена». Класс ошибки — итог сильнее доказательства.

Стенды с известным ответом (каждый падал на коде до лечения):
1. одно непроверенное жёсткое правило → допустимость НЕ установлена,
   вердикт кнопки — «не удостоверено: непроверено N правил» с перечнем;
2. составное правило с одной из двух прогнанных проверок — не покрыто;
3. продление для базы А не поднимает предел базы Б (адресное
   исключение), условие продления вне словаря — честный отказ;
4. признак перегона (deadhead) не теряется в таблице назначений
   сертификатора; потеря ловится проверкой полноты входа;
5. нестрогий режим покрытия снят — сертификат без полного покрытия
   допустимости не удостоверяет;
6. общий контракт «трёхчастный результат» (vmp.run.verdicts)."""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from vmp.run import roster_certify as rc          # noqa: E402
from cdpm.gates import gate_certify               # noqa: E402
from test_run_certify import _compiler, _solution, _svod   # noqa: E402


def _exclude(svod: dict, rid: str) -> dict:
    """Законное исключение правила «до получения данных»."""
    for r in svod["rules"]:
        if r["id"] == rid:
            r["excluded_until_data"] = {"question": "Q-3b-ДАННЫЕ",
                                        "revision": 1}
    return svod


# ---------------------------------------------------------- стенд 1

def test_A8_непроверенное_жёсткое_правило_не_удостоверено():
    """Свод с R-QUAL без единой прогнанной проверки (справочника
    допусков в решении нет): нарушений нет, но допустимость НЕ
    установлена — certified=False, feasibility=not_established,
    непроверенное названо поимённо."""
    report = rc.certify(_svod(), _solution(True, True), _compiler)
    v = report["certificate"]
    assert v["findings"] == 0
    assert v["certified"] is False
    assert v["rules_uncovered"] == ["R-QUAL"]
    res = v["result"]
    assert res["feasibility"] == "not_established"
    assert res["coverage_complete"] is False
    assert res["uncovered"] == ["R-QUAL"]
    assert "не удостоверено" in v["note"] and "R-QUAL" in v["note"]
    assert "НЕ УДОСТОВЕРЕНО" in rc.render_certificate(report)
    assert gate_certify.check(_svod(), report)[0] == []
    # Полное покрытие (законное исключение R-QUAL) — допустимость
    # подтверждена.
    ok = rc.certify(_exclude(_svod(), "R-QUAL"), _solution(True, True),
                    _compiler)
    assert ok["certificate"]["certified"] is True
    assert ok["certificate"]["result"]["feasibility"] == "confirmed"
    assert ok["certificate"]["result"]["coverage_complete"] is True


def test_A8_кнопка_не_зовёт_непроверенное_допустимым(tmp_path):
    """Штатный путь: вердикт кнопки выводится из покрытия, не из
    флага. Одно непроверенное правило → «не удостоверено: непроверено
    1 правил (R-QUAL)», в выдаче — трёхчастный результат и перечень."""
    from test_run_button import FakeSolver, _fakes, _run_dir
    doc = _svod()
    doc["rules"] = [r for r in doc["rules"] if r["kind"] != "SOFT"]
    from vmp.run import button as btn
    d = btn.orchestrate(_run_dir(tmp_path, doc), FakeSolver(),
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"].startswith("не удостоверено: непроверено 1 правил"), d
    assert "R-QUAL" in d["verdict"]
    assert d["certified"] is False
    assert d["result"]["feasibility"] == "not_established"
    assert d["result"]["uncovered"] == ["R-QUAL"]
    assert d["result"]["cost"] in ("confirmed", "partial", "mismatch", "not_checked")
    assert d["result"]["optimality"] == "not_proven"
    assert d["result"]["search_incomplete"] is False
    delivery = json.loads((tmp_path / "button" / "delivery.json")
                          .read_text(encoding="utf-8"))
    assert delivery["result"]["feasibility"] == "not_established"


# ---------------------------------------------------------- стенд 2

def test_A8_составное_правило_одной_проверкой_не_покрыто(monkeypatch):
    """R-DUTY получает две проверки: предел смены (прогнана) и
    выходные в месяце (на однодневных данных — «не прогнана»). Правило
    покрыто только всеми своими проверками — здесь оно не покрыто.
    Компилятор CDPM по построению даёт одну проверку на правило, поэтому
    вторая спецификация подкладывается на выходе компиляции."""
    svod = _exclude(_svod(), "R-QUAL")
    real_compile = rc.dc.compile_checks

    def two_specs(svod_, dataset, llm, control=None):
        specs = real_compile(svod_, dataset, llm, control=control)
        specs.append({
            "id": "C-R-DUTY-1", "basis": {"rule": "R-DUTY"},
            "template": "выходных-в-месяце",
            "params": {"sheet": "SOL/Смены",
                       "group_column": "Табельный номер",
                       "start_column": "Начало",
                       "end_column": "Конец", "min_days_off": 8},
            "statement": "выходных ≥ 8 в месяц", "unit": "клетка",
            "severity": "БЛОКИРУЮЩЕЕ", "status": "скомпилирована"})
        return specs

    monkeypatch.setattr(rc.dc, "compile_checks", two_specs)
    report = rc.certify(svod, _solution(True, True), _compiler)
    v = report["certificate"]
    assert sum(1 for c in report["checks"]
               if (c.get("basis") or {}).get("rule") == "R-DUTY") == 2
    assert v["checks_run"] >= 2                     # одна из двух прогнана
    assert v["rules_partially_covered"] == ["R-DUTY"]
    assert "R-DUTY" in v["rules_uncovered"]
    assert v["certified"] is False
    assert v["result"]["feasibility"] == "not_established"
    assert gate_certify.check(svod, report)[0] == []


# ---------------------------------------------------------- стенд 3

def _rule(rid, fam, params, kind="HARD", zone="PAIRING"):
    return {"id": rid, "kind": kind, "zone": zone, "title": rid,
            "statement": f"правило {rid}",
            "sources": [{"sid": "S1", "anchor": rid}],
            "template_fit": {"category": "A", "family": fam,
                             "registry_rev": "x" * 12,
                             "params": {k: {"value": v, "quote": str(v)}
                                        for k, v in params.items()}}}


def _cap_svod(cap_params: dict) -> dict:
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [
            _rule("R-LIM", "duty_limits", {"max_duty_duration": 720}),
            _rule("R-EXT", "duty_extension_penalty",
                  dict({"long_duty_threshold": 720,
                        "duty_extension_cap": 780}, **cap_params),
                  kind="SOFT"),
        ],
        "questions": [], "objectives": [],
    }


def _cap_compiler(prompt):
    assert "R-LIM" in prompt
    return json.dumps({
        "checks": [
            {"rule": "R-LIM", "template": "максимальная-длительность-часов",
             "params": {"sheet": "SOL/Смены", "start_column": "Начало",
                        "end_column": "Конец", "max_hours": 12},
             "statement": "смена ≤ 12 ч", "unit": "смена",
             "severity": "БЛОКИРУЮЩЕЕ"}],
        "skipped": []}, ensure_ascii=False)


def _cap_solution() -> dict:
    """Две смены по 760 минут (базовый предел 720, потолок 780):
    E1 из базы AAA, E2 из базы BBB."""
    sol = {"solution": "продление", "assignments": [], "duties": []}
    for emp, base in (("E1", "AAA"), ("E2", "BBB")):
        sol["assignments"].append({
            "employee": emp, "flight": f"PL-{emp}",
            "dep_time": "2026-01-15T06:00", "arr_time": "2026-01-15T18:40",
            "dep_airport": base, "arr_airport": base, "base": base,
            "duty_id": f"{emp}/D1"})
        sol["duties"].append({
            "employee": emp, "id": f"{emp}/D1", "base": base,
            "start": "2026-01-15T06:00", "end": "2026-01-15T18:40"})
    return sol


def test_A8_продление_базы_А_не_поднимает_предел_базы_Б():
    """Исключение адресно: потолок продления действует только на
    смены, удовлетворяющие условию (база AAA); смена базы BBB судится
    базовым пределом и даёт нарушение."""
    report = rc.certify(_cap_svod({"extension_bases": "AAA"}),
                        _cap_solution(), _cap_compiler)
    v = report["certificate"]
    hard = [f for f in report["findings"] if f["class"] == "нарушение правила"]
    assert len(hard) == 1, hard
    assert "E2" in hard[0]["evidence"] or hard[0]["target"]["row"] == 3
    assert "базовый предел" in hard[0]["text"]
    assert v["violated_rules"] == ["R-LIM"]
    assert v["result"]["feasibility"] == "violated"
    cap = next(c for c in report["checks"]
               if (c.get("basis") or {}).get("rule") == "R-LIM")
    assert cap["status"] == "прогнана"
    assert cap["params"]["cap_hours"] == 13.0
    assert cap["params"]["max_hours"] == 12
    assert cap["params"]["conditions"] == {"extension_bases": ["AAA"]}
    # Безусловное продление (условий у правила нет) — обе смены под
    # потолком, нарушений нет.
    free = rc.certify(_cap_svod({}), _cap_solution(), _cap_compiler)
    assert free["certificate"]["violated_rules"] == []
    assert free["certificate"]["result"]["feasibility"] == "confirmed"


def test_A8_условие_продления_вне_словаря_честный_отказ():
    """Условие, которое сертификатор проверять не умеет, не
    превращается в «применить потолок всем»: проверка предела
    «не прогнана: причина», правило непокрыто, допустимость не
    установлена."""
    report = rc.certify(_cap_svod({"extension_crew_augmented": True}),
                        _cap_solution(), _cap_compiler)
    v = report["certificate"]
    cap = next(c for c in report["checks"]
               if (c.get("basis") or {}).get("rule") == "R-LIM")
    assert cap["status"] == "не прогнана"
    assert "не поддаётся проверке" in cap["reason"]
    assert "extension_crew_augmented" in cap["reason"]
    assert v["rules_uncovered"] == ["R-LIM"]
    assert v["result"]["feasibility"] == "not_established"


def test_A8_условие_продления_без_данных_в_решении_честный_отказ():
    """Условие по базе объявлено, а у смен решения базы нет — не
    догадка «условие не выполнено», а «не прогнана: нет данных»."""
    sol = _cap_solution()
    for d in sol["duties"]:
        d.pop("base")
    report = rc.certify(_cap_svod({"extension_bases": ["AAA"]}), sol,
                        _cap_compiler)
    cap = next(c for c in report["checks"]
               if (c.get("basis") or {}).get("rule") == "R-LIM")
    assert cap["status"] == "не прогнана"
    assert "База приписки" in cap["reason"]


# ---------------------------------------------------------- стенд 4

def test_A8_признак_перегона_не_теряется_в_таблице_назначений():
    """Решение несёт deadhead у назначения — таблица назначений
    сертификатора обязана нести колонку «Перегон» у каждой строки."""
    sol = _solution(True, True)
    sol["assignments"][1]["deadhead"] = True
    ds = rc.build_solution_dataset(sol)
    naz = ds["tables"][rc.SOLUTION_SID]["Назначения"]
    assert "Перегон" in naz["columns"]
    assert [r["Перегон"] for r in naz["rows"]] == ["нет", "да"]
    # Проверка полноты входа принимает полную таблицу…
    rc.verify_input_completeness(sol, ds)
    # …и отказывает словами при потере признака.
    lost = json.loads(json.dumps(ds, ensure_ascii=False))
    lost["tables"][rc.SOLUTION_SID]["Назначения"]["columns"].remove("Перегон")
    for r in lost["tables"][rc.SOLUTION_SID]["Назначения"]["rows"]:
        r.pop("Перегон")
    with pytest.raises(rc.CertifyError) as e:
        rc.verify_input_completeness(sol, lost)
    assert "Перегон" in str(e.value) and "потерян" in str(e.value)
    # Решение без перегонов колонки не рождает (отсутствующее поле —
    # не данные).
    plain = rc.build_solution_dataset(_solution(True, True))
    assert "Перегон" not in plain["tables"][rc.SOLUTION_SID]["Назначения"]["columns"]


def test_A8_выгрузка_решения_доносит_перегон_до_сертификатора():
    """Сквозь webapp.solution_export: строка перегона едет флагом и
    доезжает колонкой до таблицы сертификатора."""
    from webapp import solution_export as se
    payload = {"flights": [
        {"flightNumber": "F1", "departureUTCDateTime": "2026-01-15T06:00",
         "arrivalUTCDateTime": "2026-01-15T08:00",
         "departureAirport": "AAA", "arrivalAirport": "BBB"},
        {"flightNumber": "F2", "departureUTCDateTime": "2026-01-15T10:00",
         "arrivalUTCDateTime": "2026-01-15T12:00",
         "departureAirport": "BBB", "arrivalAirport": "AAA"}]}
    solved = {"flightAssignments": [
        {"employee": "E1", "flight": "F1", "requiredSkill": "Captain"},
        {"employee": "E1", "flight": "F2", "requiredSkill": "Captain",
         "deadhead": True}]}
    rows = se.assignments_of(payload, solved)
    assert rows[1]["deadhead"] is True
    ds = rc.build_solution_dataset({"solution": "s", "assignments": rows})
    naz = ds["tables"][rc.SOLUTION_SID]["Назначения"]
    assert [r["Перегон"] for r in naz["rows"]] == ["нет", "да"]


# ---------------------------------------------------------- стенд 5

def test_A8_нестрогое_покрытие_снято():
    """Режим «нарушений нет по прогнанным = certified» снят: просьба о
    нём — отказ словами, а не тихое ослабление."""
    with pytest.raises(rc.CertifyError) as e:
        rc.certify(_svod(), _solution(True, True), _compiler,
                   strict_coverage=False)
    assert "A8" in str(e.value)
    with pytest.raises(rc.CertifyError):
        from test_run_certify import (_pairing_catalog, _pairing_compiler,
                                      _pairing_fmap, _pairing_svod)
        rc.certify_pairing(_pairing_svod(), _pairing_catalog(),
                           _pairing_fmap(), _pairing_compiler,
                           strict_coverage=False)


# ---------------------------------------------------------- стенд 6

def test_A8_контракт_трёхчастного_результата():
    """Общий контракт (раздел 5 плана): словари закрыты, вывод из
    сертификата детерминирован, слова вердикта — по допустимости."""
    from vmp.run import verdicts as vd
    assert set(vd.FEASIBILITY) == {"confirmed", "violated", "not_established"}
    # Контракт Б (05.09.2026): «partial» — сверка сошлась на области
    # учёта, но полная стоимость не установлена (неопределённый вклад).
    assert set(vd.COST) == {"confirmed", "partial", "mismatch", "not_checked"}
    assert set(vd.OPTIMALITY) == {"feasible_found", "catalog_optimum",
                                  "proven_optimum_or_bound", "not_proven"}
    base = {"certified": False, "checks_run": 3, "findings": 0,
            "rules_uncovered": ["R-1", "R-2"], "checks_not_compiled": 0,
            "rules_partially_covered": []}
    res = vd.result_of_certificate(base)
    assert res["feasibility"] == "not_established"
    assert res["coverage_complete"] is False
    assert res["uncovered"] == ["R-1", "R-2"]
    assert res["cost"] == "not_checked"
    assert res["optimality"] == "not_proven"
    assert res["search_incomplete"] is False
    assert vd.verdict_words(res) == \
        "не удостоверено: непроверено 2 правил (R-1, R-2)"
    # Нарушение сильнее пробела покрытия.
    viol = vd.result_of_certificate(dict(base, findings=2))
    assert viol["feasibility"] == "violated"
    assert vd.verdict_words(viol) == "недопустимо"
    # Подтверждено — только при полном покрытии и нуле нарушений.
    ok = vd.result_of_certificate(
        {"certified": True, "checks_run": 3, "findings": 0,
         "rules_uncovered": [], "checks_not_compiled": 0,
         "penalty_reconciliation": {"verdict": "сошлось"}},
        optimality="feasible_found")
    assert ok["feasibility"] == "confirmed"
    assert ok["cost"] == "confirmed"
    assert ok["optimality"] == "feasible_found"
    assert vd.verdict_words(ok) == "допустимо"
    # Флаг certified без покрытия — противоречие, а не доверие флагу.
    forged = vd.result_of_certificate(dict(base, certified=True))
    assert forged["feasibility"] == "not_established"
    # Ноль прогнанных проверок — не установлена, словами.
    empty = vd.result_of_certificate(
        {"certified": False, "checks_run": 0, "findings": 0,
         "rules_uncovered": [], "checks_not_compiled": 0})
    assert empty["feasibility"] == "not_established"
    assert "ни одна проверка" in vd.verdict_words(empty)
    # Значение вне словаря — отказ.
    with pytest.raises(ValueError):
        vd.validate_result(dict(ok, feasibility="maybe"))
    # Пейринг: оптимум каталога при доказанном IP, неполный поиск —
    # отдельным признаком.
    pr = vd.result_of_certificate(
        {"certified": True, "checks_run": 1, "findings": 0,
         "rules_uncovered": [], "checks_not_compiled": 0},
        optimality=vd.pairing_optimality("OPTIMAL"),
        search_incomplete=True)
    assert pr["optimality"] == "catalog_optimum"
    assert pr["search_incomplete"] is True
    assert vd.pairing_optimality("FEASIBLE") == "feasible_found"
    assert vd.pairing_optimality(None) == "not_proven"
    line = vd.result_line(pr)
    assert "допустимость: подтверждена" in line
    assert "поиск не завершён" in line
