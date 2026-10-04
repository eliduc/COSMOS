# -*- coding: utf-8 -*-
"""Ступень 1 моста CDPM → COSMOS: сертификация готового решения
против свода.

Охраняемое: нарушения ловятся с доказательствами двумя концами;
чистое решение сертифицируется ТОЛЬКО по прогнанным проверкам с
явным перечнем непроверенного; правило без данных в решении — честное
«не прогнана», не молчание; контракт решения fail-closed; гейт ловит
потерянное правило и расхождение шапки с телом."""
import json

import pytest

from vmp.run import roster_certify as rc
from cdpm.gates import gate_certify


def _svod():
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md",
                     "sha256": "0" * 64}],
        "rules": [
            {"id": "R-REST", "title": "Отдых между назначениями",
             "statement": "разрыв между назначениями одного члена "
                          "экипажа ≥ 12 часов",
             "zone": "ROSTERING", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
            {"id": "R-DUTY", "title": "Предел смены",
             "statement": "смена ≤ 12 часов",
             "zone": "BOTH", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.2"}]},
            {"id": "R-QUAL", "title": "Допуски",
             "statement": "допуск члена экипажа действителен на дату "
                          "рейса",
             "zone": "ROSTERING", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.3"}]},
            {"id": "R-SOFT", "title": "Ночёвки",
             "statement": "штраф за ночь вне базы: вес 10",
             "zone": "PAIRING", "kind": "SOFT",
             "sources": [{"sid": "S1", "anchor": "п.4"}]},
        ],
        "questions": [], "objectives": [],
    }


def _exclude(svod: dict, rid: str) -> dict:
    """Законное исключение правила «до получения данных по решению
    авиакомпании» — единственный честный способ снять правило с
    покрытия, не проверяя его (аудит A8, 04.09.2026)."""
    for r in svod["rules"]:
        if r["id"] == rid:
            r["excluded_until_data"] = {"question": "Q-3b-ДАННЫЕ",
                                        "revision": 1}
    return svod


def _svod_full() -> dict:
    """Свод с ПОЛНЫМ покрытием на фейковом компиляторе: R-QUAL (допуски,
    справочника в решении нет) исключено по решению авиакомпании.
    Аудит A8 (04.09.2026): сертификат допустимости выдаётся только при
    полном покрытии, поэтому стенды, чей предмет — не покрытие,
    работают на этом своде; прежний _svod с непокрытым R-QUAL честно
    даёт «допустимость не установлена»."""
    return _exclude(_svod(), "R-QUAL")


def _compiler(prompt):
    """Фейковый компилятор проверок: отдых и смены компилируются,
    допуски честно пропускаются (в решении нет справочника)."""
    assert "шаблоны и их параметры" in prompt
    return json.dumps({
        "checks": [
            {"rule": "R-REST", "template": "минимальный-разрыв-часов",
             "params": {"sheet": "SOL/Назначения",
                        "group_column": "Табельный номер",
                        "start_column": "Время вылета",
                        "end_column": "Время прилёта",
                        "min_hours": 12},
             "statement": "разрыв ≥ 12 ч", "unit": "назначение",
             "severity": "БЛОКИРУЮЩЕЕ"},
            {"rule": "R-DUTY",
             "template": "максимальная-длительность-часов",
             "params": {"sheet": "SOL/Смены",
                        "start_column": "Начало",
                        "end_column": "Конец", "max_hours": 12},
             "statement": "смена ≤ 12 ч", "unit": "смена",
             "severity": "БЛОКИРУЮЩЕЕ"},
        ],
        "skipped": [
            {"rule": "R-QUAL", "reason": "в решении нет справочника "
                                         "допусков"},
        ],
    }, ensure_ascii=False)


def _solution(rest_gap_ok: bool, duty_ok: bool):
    second_dep = "2026-01-16T04:00" if rest_gap_ok \
        else "2026-01-15T15:00"
    second_arr = "2026-01-16T06:00" if rest_gap_ok \
        else "2026-01-15T17:00"
    duty_end = "2026-01-15T14:00" if duty_ok else "2026-01-15T19:31"
    return {
        "solution": "тестовый ростер",
        "assignments": [
            {"employee": "E1", "flight": "PL101",
             "dep_time": "2026-01-15T06:00",
             "arr_time": "2026-01-15T08:00",
             "dep_airport": "PLY", "arr_airport": "SVG"},
            {"employee": "E1", "flight": "PL102",
             "dep_time": second_dep, "arr_time": second_arr,
             "dep_airport": "SVG", "arr_airport": "PLY"},
        ],
        "duties": [{"employee": "E1",
                    "start": "2026-01-15T06:00", "end": duty_end}],
    }


def test_certify_catches_violations_with_two_end_evidence():
    report = rc.certify(_svod(), _solution(False, False), _compiler)
    v = report["certificate"]
    assert v["certified"] is False
    assert v["findings"] == 2
    assert v["violated_rules"] == ["R-DUTY", "R-REST"]
    rest = next(f for f in report["findings"]
                if f["rule_refs"] == ["R-REST"])
    # Доказательство разрыва обязано называть оба конца.
    assert "конец предыдущей" in rest["evidence"]
    assert "начало следующей" in rest["evidence"]
    md = rc.render_certificate(report)
    assert "НЕ СЕРТИФИЦИРОВАНО" in md
    assert gate_certify.check(_svod(), report)[0] == []


def test_render_prints_window_coverage_line_from_certificate_numbers():
    """C-14 (план §3.8 / C-11): строка «ПОКРЫТИЕ ОКОН» в шапке
    человекочитаемого сертификата — числа из certificate.window_coverage;
    без проверок окон (window_coverage.checks пуст) строки нет."""
    report = rc.certify(_svod_full(), _solution(True, True), _compiler)
    v = report["certificate"]
    assert v["window_coverage"] == {"expected": 0, "evaluated": 0,
                                    "unknown": 0, "checks": []}
    assert "ПОКРЫТИЕ ОКОН" not in rc.render_certificate(report)
    v["window_coverage"] = {
        "expected": 14, "evaluated": 9, "unknown": 5,
        "checks": [{"check": "C-R-ROLL-форма", "rule": "R-ROLL",
                    "expected": 14, "evaluated": 9, "unknown": 5,
                    "granularity": "continuous_critical_cells",
                    "expected_unit": "critical_cells", "windows": []}]}
    md = rc.render_certificate(report)
    assert ("ПОКРЫТИЕ ОКОН: доказательных ячеек ожидается 14, проверено 9, "
            "неизвестно 5." in md)
    assert "неизвестное окно не считается выдержанным" in md
    # числа — из шапки, не из тела: при других числах строка меняется
    v["window_coverage"].update(expected=3, evaluated=3, unknown=0)
    assert "ожидается 3, проверено 3, неизвестно 0." in rc.render_certificate(report)


def test_certify_clean_solution_honest_scope():
    """Аудит A8 (04.09.2026): прежде «нарушений нет по прогнанным» давал
    certified=True при непроверенном R-QUAL — итог был сильнее
    доказательства. Теперь: нарушений нет, но допустимость НЕ
    установлена; полное покрытие (законное исключение) — подтверждена."""
    report = rc.certify(_svod(), _solution(True, True), _compiler)
    v = report["certificate"]
    assert v["certified"] is False
    assert v["result"]["feasibility"] == "not_established"
    assert v["findings"] == 0
    assert v["checks_run"] == 2
    # Допуски честно не проверены — сертификат так и говорит.
    assert v["checks_not_run"] >= 1
    not_run = [c for c in report["checks"]
               if c["status"] != "прогнана"]
    assert any("допусков" in (c.get("reason") or "")
               for c in not_run)
    assert "не удостоверено" in v["note"] and "R-QUAL" in v["note"]
    assert gate_certify.check(_svod(), report)[0] == []
    full = rc.certify(_svod_full(), _solution(True, True), _compiler)
    assert full["certificate"]["certified"] is True
    assert full["certificate"]["result"]["feasibility"] == "confirmed"
    assert "подтверждена" in full["certificate"]["note"]
    assert gate_certify.check(_svod_full(), full)[0] == []


def test_solution_contract_fail_closed():
    with pytest.raises(rc.CertifyError):
        rc.build_solution_dataset({"assignments": []})
    with pytest.raises(rc.CertifyError):
        rc.build_solution_dataset({"assignments": [
            {"employee": "E1", "flight": "PL101",
             "dep_time": "2026-01-15T06:00"}]})   # нет arr_time
    with pytest.raises(rc.CertifyError):
        rc.build_solution_dataset({"assignments": ["мусор"]})
    # Мусорные типы разметок — отказ, не падение (ревью ступени 1).
    base = _solution(True, True)
    for field, garbage in (("duties", 123), ("duties", ["строка"]),
                           ("pairings", True)):
        bad = json.loads(json.dumps(base, ensure_ascii=False))
        bad[field] = garbage
        with pytest.raises(rc.CertifyError):
            rc.build_solution_dataset(bad)
    # Числовой табельный (в т.ч. 0) — законен и последователен.
    ok = json.loads(json.dumps(base, ensure_ascii=False))
    ok["assignments"][0]["employee"] = 0
    ds = rc.build_solution_dataset(ok)
    assert ds["tables"]["SOL"]["Назначения"]["rows"][0][
        "Табельный номер"] == "0"
    # Отпечаток решения детерминирован и непуст (контракт 3.3).
    assert len(ds["sources"][0]["sha256"]) == 64


def test_excluded_until_data_is_marked_unverified_not_certified():
    """Структура ВМП (владелец 2026-09-02): правило с пометкой
    «исключено до получения данных по решению авиакомпании» не
    проверяется и не считается; сертификат называет его отдельной
    строкой «не проверено по решению авиакомпании»."""
    svod = _svod()
    soft = next(r for r in svod["rules"] if r["id"] == "R-SOFT")
    soft["excluded_until_data"] = {"question": "Q-3b-нет-данных-soft",
                                   "revision": 1}
    kept, excluded = rc.split_excluded_until_data(svod)
    assert [x["rule_id"] for x in excluded] == ["R-SOFT"]
    assert [r["id"] for r in kept["rules"]] == ["R-REST", "R-DUTY",
                                                 "R-QUAL"]
    assert rc.split_excluded_until_data(_svod()) == (_svod(), [])
    report = rc.certify(svod, _solution(True, True), _compiler)
    v = report["certificate"]
    # A8: R-QUAL в этом своде не покрыто — допустимость не установлена
    # (прежде certified=True). Исключённое мягкое названо отдельно.
    assert v["certified"] is False
    assert v["result"]["uncovered"] == ["R-QUAL"]
    assert v["excluded_until_data"] == [
        {"rule_id": "R-SOFT", "title": "Ночёвки",
         "question": "Q-3b-нет-данных-soft", "revision": 1}]
    # Исключённое мягкое не числится «вне сертификации допустимости»
    # как обычное мягкое — у него своя причина.
    assert "R-SOFT" not in (v["rules_soft_out_of_scope"] or [])
    md = rc.render_certificate(report)
    assert "НЕ ПРОВЕРЕНО ПО РЕШЕНИЮ АВИАКОМПАНИИ (1)" in md
    assert "R-SOFT (вопрос Q-3b-нет-данных-soft, ревизия 1)" in md
    assert gate_certify.check(svod, report)[0] == []
    # Реальный случай (аудит №1): исключено ЖЁСТКОЕ правило — гейт
    # полноты не считает его «потерянным молча», требует лишь, чтобы
    # сертификат его назвал; посчитанная доля его не числит
    # непосчитанным.
    svod2 = _svod()
    qual = next(r for r in svod2["rules"] if r["id"] == "R-QUAL")
    qual["excluded_until_data"] = {"question": "Q-3b-нет-данных-qual",
                                   "revision": 1}
    report2 = rc.certify(svod2, _solution(True, True), _compiler)
    v2 = report2["certificate"]
    assert v2["certified"] is True
    assert [x["rule_id"] for x in v2["excluded_until_data"]] == ["R-QUAL"]
    assert "R-QUAL" not in v2["counted_share"]["rules_uncounted"]
    assert gate_certify.check(svod2, report2)[0] == []
    # Сертификат, не назвавший исключённое правило, — «потеряно молча».
    report2["certificate"]["excluded_until_data"] = []
    assert any("не названо" in p for p in gate_certify.check(svod2,
                                                              report2)[0])


def test_timezone_times_are_normalized_not_blamed_on_rules():
    """ISO с таймзоной (Z/смещение) нормализуется в UTC — чистое
    решение не проваливается «нарушением правила» из-за формата
    (ревью ступени 1)."""
    sol = _solution(True, True)
    for a in sol["assignments"]:
        a["dep_time"] += ":00Z"
        a["arr_time"] += ":00Z"
    report = rc.certify(_svod_full(), sol, _compiler)
    assert report["certificate"]["findings"] == 0
    assert report["certificate"]["certified"] is True


def test_mass_violations_not_collapsed_by_sanity_threshold():
    """Массовые нарушения решения — штатный случай сертификации:
    каждая находка в счёте, свёртка лавины линии данных отключена."""
    assignments = []
    for i in range(60):
        assignments.append({
            "employee": "E1", "flight": f"PL{i:03d}",
            "dep_time": f"2026-01-15T{6 + (i % 12):02d}:{(i * 7) % 50:02d}",
            "arr_time": f"2026-01-15T{6 + (i % 12):02d}:{(i * 7) % 50 + 9:02d}"})
    sol = {"solution": "лавина", "assignments": assignments,
           "duties": [{"employee": "E1",
                       "start": "2026-01-15T06:00",
                       "end": "2026-01-15T14:00"}]}
    report = rc.certify(_svod(), sol, _compiler)
    v = report["certificate"]
    assert v["findings"] > 20
    assert not any("неверная нацеленность" in f.get("text", "")
                   for f in report["findings"])
    assert gate_certify.check(_svod(), report)[0] == []


def test_vacuum_certificate_is_not_certified():
    """Ни одной прогнанной проверки (все правила заблокированы) —
    certified=False, третье состояние в note (ревью ступени 1:
    вакуумный «зелёный» неотличим для потребителя моста)."""
    svod = _svod()
    for r in svod["rules"]:
        if r["kind"] in ("HARD",):
            r["blockers"] = ["Q-1-X"]
    svod["questions"] = [{
        "id": "Q-1-X", "part": "1", "title": "Х", "text": "т",
        "answer_format": {"type": "choice", "options": [
            {"key": "а", "text": "a"}, {"key": "б", "text": "b"}]},
        "rule_refs": [r["id"] for r in svod["rules"]
                      if r["kind"] == "HARD"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}]}]
    report = rc.certify(svod, _solution(True, True), _compiler)
    v = report["certificate"]
    assert v["checks_run"] == 0
    assert v["certified"] is False
    assert "НЕ состоялась" in v["note"]
    assert "НЕ СОСТОЯЛАСЬ" in rc.render_certificate(report)
    assert gate_certify.check(svod, report)[0] == []


def test_certificate_is_valid_data_report_and_lists_soft():
    """Сертификат — валидный отчёт контракта 3.3 (validate_report
    входит в гейт) и называет мягкие правила вне охвата."""
    from cdpm import data_report as drm
    report = rc.certify(_svod(), _solution(True, True), _compiler)
    assert drm.validate_report(report, _svod()) == []
    assert report["certificate"]["rules_soft_out_of_scope"] \
        == ["R-SOFT"]
    assert "R-SOFT" in rc.render_certificate(report)


def test_cli_exit_codes(tmp_path, monkeypatch):
    """CLI: 2 — вход/канал; 1 — не сертифицировано; 0 — чисто.
    Канал строится cosmos_llm(scope='any', requested=--llm) — ревью
    ступени 1: --llm уезжал в scope и CLI падал всегда."""
    from cdpm import llm as llm_mod
    assert rc.main([str(tmp_path / "нет.json"),
                    str(tmp_path / "нет2.json"),
                    str(tmp_path / "out")]) == 2

    model_p = tmp_path / "model.json"
    # A8: код 0 — только при подтверждённой допустимости (полное
    # покрытие); свод с непокрытым правилом дал бы 1.
    model_p.write_text(json.dumps(_svod_full(), ensure_ascii=False),
                       encoding="utf-8")
    sol_p = tmp_path / "sol.json"
    sol_p.write_text(json.dumps(_solution(False, False),
                                ensure_ascii=False),
                     encoding="utf-8")
    seen = {}

    def fake_cosmos(scope="any", requested=None, **kw):
        seen["scope"], seen["requested"] = scope, requested
        return _compiler

    monkeypatch.setattr(llm_mod, "cosmos_llm", fake_cosmos)
    rcode = rc.main([str(model_p), str(sol_p),
                     str(tmp_path / "out"), "--llm", "cloud"])
    assert rcode == 1                    # нарушения найдены
    assert seen == {"scope": "any", "requested": "cloud"}
    sol_p.write_text(json.dumps(_solution(True, True),
                                ensure_ascii=False),
                     encoding="utf-8")
    assert rc.main([str(model_p), str(sol_p),
                    str(tmp_path / "out2")]) == 0


def test_rule_without_solution_data_is_honest_not_run():
    """Решение без разметки смен: проверка предела смены не находит
    листа и честно остаётся непрогнанной, сертификат это перечисляет
    (правило не теряется)."""
    sol = _solution(True, True)
    sol.pop("duties")
    report = rc.certify(_svod_full(), sol, _compiler)
    duty = next(c for c in report["checks"]
                if (c.get("basis") or {}).get("rule") == "R-DUTY")
    assert duty["status"] == "не прогнана"
    # A8: непрогнанная проверка жёсткого правила — допустимость не
    # установлена (прежде certified=True закреплял дефект).
    assert report["certificate"]["certified"] is False
    assert report["certificate"]["rules_uncovered"] == ["R-DUTY"]
    assert report["certificate"]["result"]["feasibility"] == "not_established"
    assert gate_certify.check(_svod_full(), report)[0] == []


def test_gate_catches_lost_rule_and_header_drift():
    report = rc.certify(_svod(), _solution(False, False), _compiler)
    # Потеря правила: выкидываем все исходы R-QUAL.
    broken = json.loads(json.dumps(report, ensure_ascii=False))
    broken["checks"] = [c for c in broken["checks"]
                        if (c.get("basis") or {}).get("rule")
                        != "R-QUAL"]
    problems, _ = gate_certify.check(_svod(), broken)
    assert any("потеряно молча" in p for p in problems)
    # Дрейф шапки: вердикт против находок.
    drift = json.loads(json.dumps(report, ensure_ascii=False))
    drift["certificate"]["certified"] = True
    problems, _ = gate_certify.check(_svod(), drift)
    assert any("противоречит" in p for p in problems)


def test_solver_thresholds_section_in_certificate():
    """Пункт (в) моста: applied/ignored решателя входят в сертификат
    машинным разделом и человекочитаемой секцией «формализовано,
    решателем не исполнялось»; трассировка прогона — в шапке."""
    thresholds = {
        "applied": [{"threshold": "max_duty_span_min", "value": 720,
                     "literal": 840,
                     "fromParams": ["max_duty_duration"],
                     "declarations": ["ЗАМЕЩЕНИЕ: 840 → 720"]}],
        "ignored": [{"param": "crew_size", "rules": ["R-CS"],
                     "reason": "имя не известно таблице решателя"}],
        "paramToThreshold": {"max_duty_duration":
                             "max_duty_span_min"},
    }
    run_info = {"package_fingerprint": "f" * 64,
                "rules_profile": "DEFAULT",
                "source_run": "run-0036", "spec_version": "1.3",
                "mode": "optimization", "лишнее": "отбрасывается"}
    report = rc.certify(_svod(), _solution(True, True), _compiler,
                        thresholds=thresholds, run_info=run_info)
    v = report["certificate"]
    assert v["solver_applied"] == 1 and v["solver_ignored"] == 1
    assert report["solver_thresholds"]["ignored"][0]["param"] \
        == "crew_size"
    assert "лишнее" not in report["run_trace"]
    md = rc.render_certificate(report)
    assert "Пороги решателя того же прогона" in md
    assert "ФОРМАЛИЗОВАНО, РЕШАТЕЛЕМ НЕ ИСПОЛНЯЛОСЬ: 1" in md
    assert "имя не известно таблице решателя" in md
    assert "ЗАМЕЩЕНИЕ: 840 → 720" in md
    assert "run-0036" in md and "DEFAULT" in md


def test_solver_thresholds_fail_closed():
    """Неразборчивые показания — отказ с причиной, не молчаливый
    пропуск раздела; без показаний сертификат прежний."""
    import pytest
    with pytest.raises(rc.CertifyError):
        rc.certify(_svod(), _solution(True, True), _compiler,
                   thresholds=[1, 2])
    with pytest.raises(rc.CertifyError):
        rc.certify(_svod(), _solution(True, True), _compiler,
                   thresholds={"applied": "не список"})
    with pytest.raises(rc.CertifyError):
        rc.certify(_svod(), _solution(True, True), _compiler,
                   thresholds={"ignored": [42]})
    report = rc.certify(_svod(), _solution(True, True), _compiler)
    assert "solver_thresholds" not in report
    assert "solver_applied" not in report["certificate"]
    assert "Пороги решателя" not in rc.render_certificate(report)


def test_absent_optional_fields_do_not_materialize_as_columns():
    """Вход без необязательных полей (аэропорты, роль) не должен
    рождать пустые колонки: компилятор принял бы их за данные и
    прогнал проверку с ложными нарушениями «значение "" вне перечня»
    (живой случай нано, 12 ложных, 2026-08-22)."""
    sol = {"solution": "без аэропортов",
           "assignments": [
               {"employee": "E1", "flight": "PL101",
                "dep_time": "2026-01-15T06:00",
                "arr_time": "2026-01-15T08:00"}]}
    ds = rc.build_solution_dataset(sol)
    sheet = ds["tables"][rc.SOLUTION_SID]["Назначения"]
    assert "Аэропорт вылета" not in sheet["columns"]
    assert "Роль" not in sheet["columns"]
    assert "Аэропорт вылета" not in sheet["rows"][0]
    # Обязательные поля остаются всегда.
    assert {"Табельный номер", "Рейс", "Время вылета",
            "Время прилёта"} <= set(sheet["columns"])


def test_solution_contract_carries_base_and_duty_binding():
    """4б: base/duty_id назначений и id смен доезжают колонками —
    без них проверки области и состава экипажа невозможны."""
    sol = _solution(True, True)
    for a in sol["assignments"]:
        a["base"] = "PLY"
        a["duty_id"] = "E1/D1"
    sol["duties"][0]["id"] = "E1/D1"
    ds = rc.build_solution_dataset(sol)
    naz = ds["tables"][rc.SOLUTION_SID]["Назначения"]
    assert {"База приписки", "Смена"} <= set(naz["columns"])
    assert naz["rows"][0]["База приписки"] == "PLY"
    smeny = ds["tables"][rc.SOLUTION_SID]["Смены"]
    assert "Идентификатор" in smeny["columns"]
    assert smeny["rows"][0]["Идентификатор"] == "E1/D1"


def test_literals_in_force_and_rest_places_columns():
    """4г: (а) ручки, считавшие литералом профиля (пакет молчит), и
    выключатели рендерятся в сертификат — читатель видит пороги НЕ
    из постановки; (б) смены несут базу и место отдыха колонками."""
    thresholds = {
        "applied": [], "ignored": [],
        "literalsInForce": [
            {"threshold": "min_sit_outstation_min", "value": 60,
             "note": "литерал профиля; пакет о нём молчит"}],
        "offLiterals": {"max_landings_per_duty": 0},
    }
    sol = _solution(True, True)
    sol["duties"][0]["base"] = "PLY"
    sol["duties"][0]["last_arr_airport"] = "SVG"
    report = rc.certify(_svod(), sol, _compiler,
                        thresholds=thresholds)
    md = rc.render_certificate(report)
    assert "СЧИТАЛОСЬ ЛИТЕРАЛАМИ ПРОФИЛЯ" in md
    assert "min_sit_outstation_min = 60" in md
    assert "Выключено в профиле" in md \
        and "max_landings_per_duty" in md
    ds = rc.build_solution_dataset(sol)
    smeny = ds["tables"][rc.SOLUTION_SID]["Смены"]
    assert {"База приписки", "Место отдыха"} <= set(smeny["columns"])
    assert smeny["rows"][0]["Место отдыха"] == "SVG"
    # Мусорные блоки — отказ, не молчание.
    import pytest
    with pytest.raises(rc.CertifyError):
        rc.certify(_svod(), sol, _compiler,
                   thresholds={"literalsInForce": [1]})
    with pytest.raises(rc.CertifyError):
        rc.certify(_svod(), sol, _compiler,
                   thresholds={"offLiterals": [1]})


def test_solver_infeasibility_banner_and_reserve_sheet():
    """Заявка моста 2026-08-23: (а) hard < 0 в score прогона —
    сертификат допустимости не выдаётся, баннер недопустимости в
    шапке; (б) лист «Резерв» строится из reserve-строк решателя;
    (в) «выключено» — по показанию: включённая пакетом ручка
    выключателем не считается."""
    sol = _solution(True, True)
    sol["reserve"] = [
        {"base": "PLY", "day": "2000-01-01",
         "free": {"Captain": 1, "FirstOfficer": 1}, "crews": 1,
         "norm": 2, "shortfall": 1, "employees_of_base": 12}]
    thresholds = {
        "applied": [{"threshold": "max_landings_per_duty",
                     "value": 6, "literal": 0, "fromParams": []}],
        "ignored": [],
        "offLiterals": {"max_landings_per_duty": 0,
                        "comp_language_mix": 0},
    }
    run_info = {"package_fingerprint": "f" * 64,
                "rules_profile": "DEFAULT",
                "score": "-29273hard/-509799soft"}
    report = rc.certify(_svod(), sol, _compiler,
                        thresholds=thresholds, run_info=run_info)
    v = report["certificate"]
    assert v["solver_hard"] == -29273
    assert v["certified"] is False
    md = rc.render_certificate(report)
    assert "РЕШАТЕЛЬ СООБЩАЕТ НЕДОПУСТИМОСТЬ: -29273hard" in md
    # Включённая пакетом ручка — не выключатель.
    assert "comp_language_mix" in md
    line = [ln for ln in md.splitlines()
            if "Выключено в профиле" in ln][0]
    assert "max_landings_per_duty" not in line
    ds = rc.build_solution_dataset(sol)
    res = ds["tables"][rc.SOLUTION_SID]["Резерв"]
    assert {"База", "Дата", "Экипажей", "Норма",
            "Нехватка"} <= set(res["columns"])
    assert res["rows"][0]["Нехватка"] == "1"
    # Неотрицательный счёт баннера не даёт.
    report2 = rc.certify(_svod(), _solution(True, True), _compiler,
                         run_info={"score": "0hard/-10soft"})
    assert report2["certificate"]["solver_hard"] == 0
    assert "НЕДОПУСТИМОСТЬ" not in rc.render_certificate(report2)


def test_qualifications_sheet_in_contract():
    """Правило 52: лист «Допуски» с датированными окнами доезжает
    колонками — шаблон «действительность-на-дату» получил данные."""
    sol = _solution(True, True)
    sol["qualifications"] = [
        {"employee": "E1", "type": "Полярис-Р200",
         "valid_from": "1970-01-01", "valid_to": "2027-03-31"}]
    ds = rc.build_solution_dataset(sol)
    q = ds["tables"][rc.SOLUTION_SID]["Допуски"]
    assert {"Табельный номер", "Тип ВС", "Действует с",
            "Действует до"} <= set(q["columns"])
    assert q["rows"][0]["Действует до"] == "2027-03-31"


def _svod_with_logic():
    svod = _svod()
    svod["rules"][1]["params"] = [
        {"name": "max_duty_duration", "value": 720,
         "unit": "duration_minutes", "as_written": "12:00",
         "relation": "max"}]
    svod["rules"][1]["logic"] = {
        "subject": "duty",
        "source_params": ["max_duty_duration"],
        "demand": {"kind": "duration_within", "relation": "max",
                   "param": "max_duty_duration", "measure": "span",
                   "as_written": "смена ≤ 12:00"},
        "enforce": "required"}
    return svod


def _logic_report(**baskets):
    from webapp import logic_gate as lg
    out = {b: [] for b in lg.BASKETS}
    out.update(baskets)
    out["blocksSeen"] = sum(len(v) for b, v in out.items()
                            if b in lg.BASKETS)
    out.setdefault("registered", ["Max duty period"])
    return out


def _entry19(rid, state, constraints=(), considered=None,
             reason=None, enforce="required"):
    return {"rule": rid, "state": state, "enforce": enforce,
            "constraints": list(constraints),
            "considered": considered,
            "consideredMeasure": "назначений набора",
            "reason": reason, "declarations": []}


def test_certificate_logic_section_green():
    """Пункт 19: показания решателя (пять корзин) судятся гейтом
    ступени 3 и входят в сертификат разделом; зелёные показания
    сертификат не портят."""
    svod = _exclude(_svod_with_logic(), "R-QUAL")     # A8: полное покрытие
    rep = _logic_report(executedElsewhere=[
        _entry19("R-DUTY", "ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ",
                 ["Max duty period"], considered=3)])
    report = rc.certify(svod, _solution(True, True), _compiler,
                        logic_report=rep)
    v = report["certificate"]
    assert v["certified"] is True
    assert v["logic_rules"] == 1
    assert v["logic_gate_green"] is True
    assert report["solver_logic"]["green"] is True
    md = rc.render_certificate(report)
    assert "показания решателя по правилам с logic" in md
    assert "Гейт показаний: зелёный." in md
    assert gate_certify.check(svod, report)[0] == []


def test_certificate_logic_defect_revokes():
    """Красные показания (ОТВЕРГНУТО в состоявшемся прогоне)
    отзывают сертификат допустимости даже при нуле нарушений."""
    svod = _svod_with_logic()
    rep = _logic_report(refused=[
        _entry19("R-DUTY", "ОТВЕРГНУТО",
                 reason="предмет вне словаря")])
    report = rc.certify(svod, _solution(True, True), _compiler,
                        logic_report=rep)
    v = report["certificate"]
    assert v["certified"] is False
    assert v["logic_gate_green"] is False
    assert "расходятся" in v["note"]
    md = rc.render_certificate(report)
    assert "КРАСНЫЙ" in md


def test_certificate_logic_missing_report_honest():
    """Правила с logic есть, показания не переданы — честная пометка
    вместо молчания; сертификат допустимости не отзывается, но на
    исполнение логики не распространяется. Показания могут ехать и
    внутри решения ключом lastRunLogic."""
    svod = _exclude(_svod_with_logic(), "R-QUAL")     # A8: полное покрытие
    report = rc.certify(svod, _solution(True, True), _compiler)
    v = report["certificate"]
    assert v["certified"] is True
    assert v["logic_rules"] == 1
    assert "не переданы" in v["logic_note"]
    assert "logic_gate_green" not in v
    md = rc.render_certificate(report)
    assert "ПОКАЗАНИЯ СТУПЕНИ 3" in md
    # Ключ lastRunLogic внутри решения равносилен параметру.
    sol = _solution(True, True)
    sol["lastRunLogic"] = _logic_report(executedElsewhere=[
        _entry19("R-DUTY", "ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ",
                 ["Max duty period"], considered=3)])
    report2 = rc.certify(svod, sol, _compiler)
    assert report2["certificate"]["logic_gate_green"] is True


def test_counted_share_machine_field():
    """Приёмка 9г: доля «ПОСЧИТАНО» — машинное поле сертификата с
    числителем, знаменателем, перечнями и методикой; непосчитанные
    жёсткие — поимённая карта второй волны."""
    svod = _svod_with_logic()
    rep = _logic_report(executedElsewhere=[
        _entry19("R-DUTY", "ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ",
                 ["Max duty period"], considered=3)])
    report = rc.certify(svod, _solution(True, True), _compiler,
                        logic_report=rep)
    cs = report["certificate"]["counted_share"]
    # Жёсткие без блокеров: R-REST (прогнана), R-DUTY (прогнана +
    # executedElsewhere), R-QUAL (честно не прогнана).
    assert cs["of"] == 3
    assert cs["counted"] == 2
    assert cs["rules_uncounted"] == ["R-QUAL"]
    assert set(cs["rules_counted"]) == {"R-REST", "R-DUTY"}
    assert "порогами решателя" in cs["reading"]
    md = rc.render_certificate(report)
    assert "ПОСЧИТАНО: 2/3 (67%)" in md
    assert "КАРТА ВТОРОЙ ВОЛНЫ" in md and "R-QUAL" in md
    assert gate_certify.check(svod, report)[0] == []


def test_unjudgeable_check_is_not_run_not_fatal():
    """Живой красный Г-S4: месячная проверка на однодневных данных
    («универсум пуст») — честное «не прогнана: причина» ИМЕННО этой
    проверки; остальные идут, сертификация не валится целиком."""
    svod = _svod_with_logic()
    svod["rules"][1]["params"].append(
        {"name": "min_days_off", "value": 8, "unit": "count",
         "as_written": "8", "relation": "min"})

    def compiler(prompt):
        base = json.loads(_compiler(prompt))
        base["checks"].append({
            "rule": "R-DUTY", "template": "выходных-в-месяце",
            "params": {"sheet": "SOL/Смены",
                       "group_column": "Табельный номер",
                       "start_column": "Начало",
                       "end_column": "Конец", "min_days_off": 8},
            "statement": "выходных ≥ 8 в месяц", "unit": "клетка",
            "severity": "БЛОКИРУЮЩЕЕ"})
        return json.dumps(base, ensure_ascii=False)

    report = rc.certify(svod, _solution(True, True), compiler)
    v = report["certificate"]
    off = next(c for c in report["checks"]
               if c.get("template") == "выходных-в-месяце")
    assert off["status"] == "не прогнана"
    assert "универсум пуст" in off["reason"]
    assert v["checks_run"] >= 1
    # A8: сертификация не валится целиком, но и «допустимо» не
    # выдаётся — правило с непрогнанной проверкой непокрыто (прежде
    # certified=True закреплял «итог сильнее доказательства»).
    assert v["certified"] is False, v
    assert "R-DUTY" in v["rules_uncovered"]
    assert v["result"]["feasibility"] == "not_established"


def test_run_info_inside_solution_feeds_solver_hard():
    """Живой вопрос Г-S4: веб-путь несёт один файл — шапка прогона
    (runInfo) едет внутри решения, как lastRunLogic; solver_hard
    берётся из её score, отрицательный отзывает сертификат."""
    svod = _svod()
    sol = _solution(True, True)
    sol["runInfo"] = {"score": "-8674hard/-747803soft",
                      "package_fingerprint": "fp-9",
                      "spec_version": "1.6"}
    report = rc.certify(svod, sol, _compiler)
    v = report["certificate"]
    assert v["solver_hard"] == -8674
    assert v["certified"] is False
    assert "НЕДОПУСТИМОСТЬ" in rc.render_certificate(report)
    assert report["run_trace"]["package_fingerprint"] == "fp-9"


def test_gate_accepts_infeasible_by_solver_with_clean_body():
    """Живой красный Г-S4: недопустимость по счёту решателя
    (runInfo −4hard) при чистом теле (однодневные данные — находок
    нет) — законный «не сертифицирован», самогейт сертификата не
    вправе звать это противоречием шапки и тела."""
    svod = _svod()
    sol = _solution(True, True)
    sol["runInfo"] = {"score": "-4hard/-1684soft"}
    report = rc.certify(svod, sol, _compiler)
    v = report["certificate"]
    assert v["certified"] is False
    assert v["solver_hard"] == -4
    assert v["findings"] == 0
    assert gate_certify.check(svod, report)[0] == []
    md = rc.render_certificate(report)
    assert "НЕДОПУСТИМОСТЬ" in md


# ------------------------------------------- сертификация пейринга

def _pairing_svod():
    """Свод четырёх зон посадки: пейринговое правило стыковки,
    общее правило смены, ростеринговое (не должно попасть в срез),
    людское пейринговое (откладывается на ростеринг)."""
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md",
                     "sha256": "0" * 64}],
        "rules": [
            {"id": "P-SIT", "title": "Минимальная стыковка",
             "statement": "стыковка между рейсами цепочки ≥ 45 минут",
             "zone": "PAIRING", "kind": "HARD",
             "template_fit": {"category": "A",
                              "family": "connection_windows"},
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
            {"id": "B-DUTY", "title": "Предел смены",
             "statement": "смена ≤ 12 часов",
             "zone": "BOTH", "kind": "HARD",
             "template_fit": {"category": "A",
                              "family": "duty_limits"},
             "sources": [{"sid": "S1", "anchor": "п.2"}]},
            {"id": "R-REST", "title": "Отдых",
             "statement": "отдых между сменами ≥ 12 часов",
             "zone": "ROSTERING", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.3"}]},
            {"id": "P-CREW", "title": "Состав экипажа",
             "statement": "состав экипажа рейса — ровно 2 пилота",
             "zone": "PAIRING", "kind": "HARD",
             "template_fit": {"category": "A",
                              "family": "crew_composition"},
             "sources": [{"sid": "S1", "anchor": "п.4"}]},
        ],
        "questions": [], "objectives": [],
    }


def _pairing_catalog():
    return {"selected": [
        {"fns": ["PL1|2026-01-15T06:00:00", "PL2|2026-01-15T09:00:00"],
         "duty_fns": [["PL1|2026-01-15T06:00:00",
                       "PL2|2026-01-15T09:00:00"]]},
        {"fns": ["PL3|2026-01-16T06:00:00"],
         "duty_fns": [["PL3|2026-01-16T06:00:00"]]},
    ]}


def _pairing_fmap():
    return {
        "PL1|2026-01-15T06:00:00": {
            "t0": "2026-01-15T06:00", "t1": "2026-01-15T08:00",
            "dep": "AAA", "arr": "BBB"},
        "PL2|2026-01-15T09:00:00": {
            "t0": "2026-01-15T09:00", "t1": "2026-01-15T11:00",
            "dep": "BBB", "arr": "AAA"},
        "PL3|2026-01-16T06:00:00": {
            "t0": "2026-01-16T06:00", "t1": "2026-01-16T07:30",
            "dep": "AAA", "arr": "BBB"},
    }


def _pairing_compiler(prompt):
    """Фейковый компилятор среза пейринга: видит только правила зоны
    пейринга без людских; компилирует предел смены."""
    assert "шаблоны и их параметры" in prompt
    assert "B-DUTY" in prompt
    assert "R-REST" not in prompt        # зона ростеринга — вне среза
    assert "P-CREW" not in prompt        # людское — отложено
    return json.dumps({
        "checks": [
            {"rule": "B-DUTY",
             "template": "максимальная-длительность-часов",
             "params": {"sheet": "SOL/Смены",
                        "start_column": "Начало",
                        "end_column": "Конец", "max_hours": 12},
             "statement": "смена ≤ 12 ч", "unit": "смена",
             "severity": "БЛОКИРУЮЩЕЕ"},
        ],
        "skipped": [
            {"rule": "P-SIT", "reason": "нет шаблона стыковки"},
        ],
    }, ensure_ascii=False)


def test_certify_pairing_scope_contract_and_verdict():
    """Сертификат пейринга: свод сужен зоной пейринга, людские
    отложены поимённо, контракт из каталога с разметкой смен
    построителя, вердикт fail-closed, рендер несёт пометку."""
    report = rc.certify_pairing(_pairing_svod(), _pairing_catalog(),
                                _pairing_fmap(), _pairing_compiler)
    v = report["certificate"]
    assert v["task"] == "pairing"
    # Срез зоны пейринга: P-SIT дошло до компилятора и получило исход.
    assert any((c.get("basis") or {}).get("rule") == "P-SIT"
               for c in report["checks"])
    assert v["judged_on_rostering"] == [
        {"rule_id": "P-CREW", "family": "crew_composition",
         "title": "Состав экипажа"}]
    # A8: P-SIT пропущено компилятором («нет шаблона стыковки») —
    # допустимость не установлена (прежде certified=True).
    assert v["certified"] is False
    assert v["result"]["feasibility"] == "not_established"
    assert v["result"]["uncovered"] == ["P-SIT"]
    assert v["checks_run"] == 1
    md = rc.render_certificate(report)
    assert "СУДЯТСЯ НА РОСТЕРИНГЕ (1): P-CREW" in md
    # Полное покрытие среза (P-SIT исключено по решению авиакомпании)
    # — подтверждена.
    full = rc.certify_pairing(_exclude(_pairing_svod(), "P-SIT"),
                              _pairing_catalog(), _pairing_fmap(),
                              _pairing_compiler)
    assert full["certificate"]["certified"] is True
    assert full["certificate"]["result"]["feasibility"] == "confirmed"


def test_certify_pairing_input_contract():
    """Контракт из каталога: цепочка — псевдо-сотрудник, смены — от
    duty_fns построителя; плечо вне карты рейсов — отказ."""
    sol = rc.pairing_input_from_catalog(_pairing_catalog(),
                                        _pairing_fmap())
    assert len(sol["assignments"]) == 3
    assert len(sol["duties"]) == 2
    assert len(sol["pairings"]) == 2
    # A6 (05.09.2026): смена несёт базу псевдо-сотрудника (вылет первого
    # перелёта цепочки) и место отдыха (прилёт последнего перелёта смены).
    assert sol["duties"][0] == {
        "employee": "chain-0", "id": "chain-0-duty-0",
        "start": "2026-01-15T06:00", "end": "2026-01-15T11:00",
        "base": "AAA", "last_arr_airport": "AAA"}
    # Контракт Б (C-18/C-35): старт цепочки — отдельное свойство каталога
    # (chain_start_airport), не приписка несуществующего сотрудника;
    # область учёта называет базы неопределёнными и несёт подпись постановки.
    assert all(a["base"] == "AAA" and a["chain_start_airport"] == "AAA"
               for a in sol["assignments"] if a["employee"] == "chain-0")
    assert sol["accounting"]["task"] == "pairing"
    assert sol["accounting"]["boundary"] == "uncertain"
    assert sol["accounting"]["contract"] == "B"
    assert sol["accounting"]["uncertain_bases"] == [] and sol["accounting"]["config_sig"] == {}
    with_sig = rc.pairing_input_from_catalog(
        dict(_pairing_catalog(), config_sig={"bases": "BBB, AAA", "tz_offset_min": 0}),
        _pairing_fmap())
    assert with_sig["accounting"]["uncertain_bases"] == ["AAA", "BBB"]
    assert with_sig["accounting"]["config_sig"]["bases"] == "BBB, AAA"
    bad_map = dict(_pairing_fmap())
    del bad_map["PL3|2026-01-16T06:00:00"]
    with pytest.raises(rc.CertifyError):
        rc.pairing_input_from_catalog(_pairing_catalog(), bad_map)


def test_certify_pairing_needs_pairing_rules():
    svod = _pairing_svod()
    svod["rules"] = [r for r in svod["rules"]
                     if r["zone"] == "ROSTERING"]
    with pytest.raises(rc.CertifyError):
        rc.certify_pairing(svod, _pairing_catalog(), _pairing_fmap(),
                           _pairing_compiler)


def test_strict_coverage_rejects_uncovered_rule(tmp_path):
    """Д4 плана «прямой ход»: правило без единой прогнанной проверки
    отзывает сертификат — на однозначном наборе «не прогнана» есть
    красный, а не пропуск. Аудит A8 (04.09.2026): строгое покрытие —
    единственный режим; прежнее умолчание (certified=True при
    непрогнанной проверке) закрепляло дефект и снято — просьба о нём
    отказывается словами."""
    sol = _solution(True, True)
    sol.pop("duties")                      # проверка предела смены не прогонится
    with pytest.raises(rc.CertifyError):
        rc.certify(_svod(), sol, _compiler, strict_coverage=False)
    strict = rc.certify(_svod(), sol, _compiler, strict_coverage=True)
    v = strict["certificate"]
    assert v["certified"] is False
    assert v["coverage_strict"] is True
    assert "R-DUTY" in v["rules_uncovered"]
    assert "не удостоверено" in v["note"]
    assert rc.certify(_svod(), sol, _compiler)["certificate"]["certified"] is False
    assert gate_certify.check(_svod(), strict)[0] == []
    # Полное решение без листа допусков: правило допусков остаётся
    # непокрытым — в строгом режиме это красный (данных нет).
    full = rc.certify(_svod(), _solution(True, True), _compiler,
                      strict_coverage=True)
    assert full["certificate"]["rules_uncovered"] == ["R-QUAL"]
    assert full["certificate"]["certified"] is False
    assert gate_certify.check(_svod(), full)[0] == []
    # Законный путь: пометка «исключено до получения данных» по решению
    # авиакомпании снимает правило из непокрытых, сертификат чист.
    svod = _svod()
    for r in svod["rules"]:
        if r["id"] == "R-QUAL":
            r["excluded_until_data"] = {"question": "Q-3b-ДАННЫЕ", "revision": 1}
    ok = rc.certify(svod, _solution(True, True), _compiler, strict_coverage=True)
    assert ok["certificate"]["rules_uncovered"] == []
    assert ok["certificate"]["certified"] is True
    assert [x["rule_id"] for x in ok["certificate"]["excluded_until_data"]] == ["R-QUAL"]
    assert gate_certify.check(svod, ok)[0] == []


def _reconciliation(clean: bool) -> dict:
    """Итог штрафной сверки в форме vmp.penalty_reconcile.reconcile."""
    from vmp import penalty_reconcile as pr
    verdict = pr.OK if clean else pr.DIVERGE
    return {"verdict": verdict, "rows": [], "objective": None, "total": {},
            "counts": {pr.OK: 1 if clean else 0, pr.DIVERGE: 0 if clean else 1,
                       pr.INCOMPARABLE: 0}}


def test_penalty_reconciliation_enters_verdict_in_strict_mode():
    """Д4, пункт 3 (спецификация COSMOS 03.09): несошедшаяся штрафная
    сверка отзывает сертификат в строгом режиме; вне строгого — только
    показание. Гейт пересчитывает вердикт из тела."""
    sol = _solution(True, True)
    svod = _svod()
    for r in svod["rules"]:
        if r["id"] == "R-QUAL":
            r["excluded_until_data"] = {"question": "Q-3b-ДАННЫЕ", "revision": 1}
    bad = _reconciliation(False)
    soft = rc.certify(svod, sol, _compiler, penalty_reconciliation=bad)
    # A8: разошедшаяся сверка — не «показание», а отзыв флага certified
    # и «стоимость: расхождение» в трёхчастном результате; допустимость
    # (полное покрытие, нарушений нет) при этом подтверждена — части
    # независимы.
    assert soft["certificate"]["certified"] is False
    assert soft["certificate"]["result"]["cost"] == "mismatch"
    assert soft["certificate"]["result"]["feasibility"] == "confirmed"
    assert soft["certificate"]["penalty_reconciliation_clean"] is False
    assert gate_certify.check(svod, soft)[0] == []
    strict = rc.certify(svod, sol, _compiler, strict_coverage=True,
                        penalty_reconciliation=bad)
    v = strict["certificate"]
    assert v["certified"] is False
    assert "штрафная сверка" in v["note"]
    assert v["penalty_reconciliation"]["verdict"] == bad["verdict"]
    assert gate_certify.check(svod, strict)[0] == []
    # Сошедшаяся сверка сертификат не отзывает.
    ok = rc.certify(svod, sol, _compiler, strict_coverage=True,
                    penalty_reconciliation=_reconciliation(True))
    assert ok["certificate"]["certified"] is True
    assert gate_certify.check(svod, ok)[0] == []
    # Подделка признака в шапке при разошедшемся теле — гейт ловит.
    forged = rc.certify(svod, sol, _compiler, penalty_reconciliation=bad)
    forged["certificate"]["penalty_reconciliation_clean"] = True
    probs = gate_certify.check(svod, forged)[0]
    assert any("расходится с её же разбором" in p for p in probs)


def test_pairing_objective_reconciliation_enters_verdict():
    """Сверка целевой пейринга (спецификация COSMOS 03.09): сумма
    стоимости выбранных цепочек против общей стоимости отчёта; в
    строгом режиме расхождение отзывает сертификат пейринга."""
    from vmp import penalty_reconcile as pr
    catalog_ok = {"selected": [{"cost": 100.0, "legs": ["L1"]},
                               {"cost": 50.0, "legs": ["L2"]}],
                  "ip_objective": 150.0}
    catalog_bad = {"selected": [{"cost": 100.0, "legs": ["L1"]}],
                   "ip_objective": 150.0}
    # Контракт Б, P-424 (единая версия 06.09.2026): суммы без области
    # учёта (без подписи цены, расшифровок и пересчёта) — «несравнимо»,
    # не «сошлось» и не «разошлось»; расхождение сумм — «разошлось».
    only_sums = pr.reconcile_pairing(catalog_ok, {"total_cost": 150.0})
    assert not pr.is_clean(only_sums)
    assert only_sums["verdict"] == pr.INCOMPARABLE
    assert only_sums["known_subtotal_verdict"] == pr.INCOMPARABLE
    bad = pr.reconcile_pairing(catalog_bad, {"total_cost": 150.0})
    assert not pr.is_clean(bad) and bad["verdict"] == pr.DIVERGE


def _night_rule():
    """Мягкое правило семьи ночёвок с посадкой A — мера ночёвок
    считается кодом (measure_soft_rules) и питается оценкой области."""
    return {"id": "R-NIGHT", "title": "Ночёвки", "kind": "SOFT",
            "statement": "штраф за ночь вне базы", "zone": "PAIRING",
            "weight": 3, "sources": [{"sid": "S1", "anchor": "п.5"}],
            "template_fit": {"category": "A", "family": "layover_penalty",
                             "registry_rev": "x" * 12, "quote": "ночь вне базы"}}


def test_certificate_header_carries_cost_assessment_of_declared_scope():
    """H11-6 (единая версия 06.09.2026): при объявленной области учёта
    (window_horizon + window_employees) шапка несёт cost_assessment —
    известные ночи, адресные неизвестные, паритет событий с решателем;
    cost_certainty остаётся рядом; без записи решателя паритет
    «несравнимо», не зелёный; гейт от нового поля шапки не краснеет."""
    svod = _svod_full()
    svod["rules"].append(_night_rule())
    sol = _solution(True, True)
    for a, duty in zip(sol["assignments"], ("D1", "D2")):
        a["base"] = "PLY"
        a["duty_id"] = duty
    sol["window_horizon"] = {"start_inclusive": "2026-01-15T00:00",
                             "end_exclusive": "2026-01-17T00:00"}
    sol["window_employees"] = [{"employee": "E1", "base": "PLY"}]
    report = rc.certify(svod, sol, _compiler)
    v = report["certificate"]
    assert v["certified"] is True
    ca = v["cost_assessment"]
    assert ca is report["cost_assessment"]
    assert ca["task"] == "rostering" and ca["source"] == "declared_scope"
    assert ca["known_nights"] == 1 and ca["known_events"][0]["airport"] == "SVG"
    assert ca["known_events"][0]["night"] == "2026-01-15"
    assert {u["reason"] for u in ca["unknown_events"]} == {
        "initial_boundary_location_interval_unknown", "terminal_continuation_unknown"}
    assert ca["unknown_count"] == 2 and ca["certainty"] == "partial"
    assert ca["solver_comparison"]["verdict"] == "INCOMPARABLE"
    assert ca["solver_comparison"]["reasons"] == ["solver_layover_cost_missing"]
    assert v["cost_certainty"] == "partial" and v["cost_uncertain_events"] == 2
    # Гейт судит вид допустимости (без мер мягких правил), как кнопка.
    assert gate_certify.check(svod, rc.feasibility_view(report))[0] == []
    md = rc.render_certificate(report)
    assert "ОБЛАСТЬ УЧЁТА НОЧЁВОК" in md and "доказанных событий 1" in md
    assert "паритет событий с решателем: INCOMPARABLE" in md
    assert "полная стоимость не подтверждена" in md
    # Сверка P-417 по шапке: запись решателя отсутствует → «несравнимо».
    from vmp import penalty_reconcile as pr
    rec = pr.reconcile(None, {}, {"constraints": {}, "soft": 0}, report)
    assert rec["verdict"] == pr.INCOMPARABLE
    assert rec["cost_scope_comparison"]["verdict"] == "INCOMPARABLE"
    rc.attach_penalty_reconciliation(report, rec)
    assert report["certificate"]["certified"] is False
    assert report["certificate"]["result"]["cost"] == "not_checked"


def test_certificate_header_without_declared_scope_is_explicitly_partial():
    """H11-4: без window_horizon мера идёт прежним путём по «Сменам», но
    как явно частичная диагностика — область не объявлена, полного
    «подтверждена» стоимость не получает; сверка событий не проводилась,
    вердикт сверки не понижается."""
    svod = _svod_full()
    svod["rules"].append(_night_rule())
    report = rc.certify(svod, _solution(True, True), _compiler)
    v = report["certificate"]
    assert v["certified"] is True
    ca = v["cost_assessment"]
    assert ca["source"] == "legacy_duties_sheet" and ca["certainty"] == "partial"
    assert ca["solver_comparison"] is None
    reasons = [u["reason"] for u in ca["unknown_events"]]
    assert reasons[:2] == ["declared_accounting_horizon_unknown", "employee_universe_unknown"]
    assert v["cost_certainty"] == "partial"
    assert gate_certify.check(svod, rc.feasibility_view(report))[0] == []
    assert "область не объявлена" in rc.render_certificate(report)
    from vmp import penalty_reconcile as pr
    rec = pr.reconcile(None, {}, {"constraints": {}, "soft": 0}, report)
    assert rec["verdict"] == pr.OK and rec["full_cost_confirmed"] is False
    rc.attach_penalty_reconciliation(report, rec)
    assert report["certificate"]["certified"] is True
    assert report["certificate"]["result"]["cost"] == "partial"
