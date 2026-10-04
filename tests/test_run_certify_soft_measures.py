# -*- coding: utf-8 -*-
"""Меры мягких правил на решении — кодом, единицами исполнителей.

Штрафная сверка Д4 (пункт 3) сравнивает совпадения по сертификату с
совпадениями решателя построчно. Пока сертификат мягких не мерил,
строка была «несравнимо» (первая живая сверка H0, 03.09). Меры не
касаются допустимости: класс «мера мягкого правила» вердикт не
трогает.

Единая версия 06.09.2026 (H11-4/H11-5/H11-13): две фикстуры — прежняя
(только лист «Смены», область учёта не объявлена → путь main как явно
частичная диагностика) и объявленная область (лист «Назначения» +
window_horizon + window_employees → независимая оценка vmp/run/cost_scope
и паритет событий с решателем). Определение ночи у обоих путей одно
(переход календарной даты между разными сменами вне базы), поэтому
ожидания по ночам совпадают."""
from vmp.run import roster_certify as rc

SOL = rc.SOLUTION_SID

HORIZON = {"start_inclusive": "2026-09-04T00:00", "end_exclusive": "2026-10-01T00:00"}


def _dataset(rows):
    """Прежняя фикстура main: только лист «Смены», без объявленной
    области учёта."""
    cols = ["Табельный номер", "Идентификатор", "Начало", "Конец",
            "База приписки", "Место отдыха"]
    return {"tables": {SOL: {"Смены": {
        "columns": cols,
        "rows": [dict(zip(cols, r)) for r in rows]}}}}


def _dataset_scope(rows, employees=None):
    """Фикстура GPT (C-37) + window_employees: лист «Назначения» —
    непрерывная линия рейсов (вылет из текущей станции, прилёт в место
    отдыха), объявленный горизонт и штат. Окончание смены само по себе
    не выдумывает ни следующую станцию, ни конечную ночь."""
    dataset = _dataset(rows)
    acols = ["Табельный номер", "Рейс", "Время вылета", "Время прилёта",
             "Аэропорт вылета", "Аэропорт прилёта", "База приписки", "Смена"]
    current = {}
    assignments = []
    for emp, duty, start, end, home, station in sorted(rows, key=lambda x: x[2]):
        assignments.append(dict(zip(acols, [emp, duty, start, end,
                                           current.get(emp, home), station, home, duty])))
        current[emp] = station
    dataset["tables"][SOL]["Назначения"] = {"columns": acols, "rows": assignments}
    dataset["window_horizon"] = dict(HORIZON)
    homes = {}
    for emp, _d, _s, _e, home, _st in rows:
        homes.setdefault(emp, home)
    dataset["window_employees"] = [{"employee": e, "base": b} for e, b in sorted(homes.items())]
    for extra in employees or []:
        dataset["window_employees"].append(extra)
    return dataset


def _soft(rid, fam, params=None, days=None):
    r = {"id": rid, "kind": "SOFT", "zone": "PAIRING", "title": rid,
         "statement": "s", "weight": 10,
         "sources": [{"sid": "S1", "anchor": rid}],
         "template_fit": {"category": "A", "family": fam,
                          "registry_rev": "x" * 12, "quote": "s"}}
    if params:
        r["template_fit"]["params"] = {k: {"value": v, "quote": str(v)}
                                       for k, v in params.items()}
    if days:
        r["export_solver"] = {"family": fam, "days": days, "tz": "base-local"}
    return r


def _counts(findings):
    counts = {}
    for f in findings:
        counts[f["rule_refs"][0]] = counts.get(f["rule_refs"][0], 0) + 1
    return counts


# Пилот H01: пятница 04.09 смена оканчивается в GRT (вне базы PLY),
# следующая смена в субботу 05.09 → одна ночь вне базы, начата в пятницу.
# Пилот H02: длинная смена 13 часов дома, ночёвок нет.
ROWS = [
    ("H01", "d1", "2026-09-04T19:00:00", "2026-09-04T20:40:00", "PLY", "GRT"),
    ("H01", "d2", "2026-09-05T07:20:00", "2026-09-05T09:00:00", "PLY", "PLY"),
    ("H02", "d3", "2026-09-04T06:00:00", "2026-09-04T19:00:00", "PLY", "PLY"),
    ("H02", "d4", "2026-09-06T06:00:00", "2026-09-06T08:00:00", "PLY", "PLY"),
]


def test_ночёвки_окно_и_продление_считаются_по_сменам():
    svod = {"rules": [
        # Порог продления — HARD-база, не самостоятельный параметр SOFT.
        dict(_soft("R-LIM", "duty_limits", params={"max_duty_duration": 720}), kind="HARD"),
        _soft("R-L", "layover_penalty"),
        _soft("R-W", "calendar_windows", days=["friday", "saturday"]),
        _soft("R-E", "duty_extension_penalty",
              params={"long_duty_threshold": 720, "duty_extension_cap": 780}),
        _soft("R-B", "balanced_workload"),
    ]}
    checks, findings = rc.measure_soft_rules(svod, _dataset_scope(ROWS))
    assert _counts(findings) == {"R-L": 1, "R-W": 1, "R-E": 1}
    status = {c["basis"]["rule"]: c["status"] for c in checks}
    assert status["R-L"] == status["R-W"] == status["R-E"] == "прогнана"
    # Лист назначений с временами рейсов есть — равномерность прогнана.
    assert status["R-B"] == "прогнана"
    assert all(f["class"] == rc.SOFT_MEASURE_CLASS for f in findings)
    # H11-5: ночь по объявленной области адресуется в строку «Назначений»
    # (левая смена разрыва), не в «Смены».
    night = next(f for f in findings if f["rule_refs"] == ["R-L"])
    assert night["target"]["sheet"] == "Назначения"
    assert night["target"]["row"] >= 2 and "ночь 2026-09-04" in night["evidence"]
    assert "смена d1" in night["evidence"]


def test_workload_without_assignments_remains_unmeasured():
    """Без листа назначений равномерность честно не прогнана (случай
    main сохранён новым тестом GPT)."""
    checks, _ = rc.measure_soft_rules({"rules": [_soft("R-B", "balanced_workload")]},
                                      _dataset(ROWS))
    assert checks[0]["status"] == "не прогнана"
    assert "назначений" in checks[0]["reason"]


def test_мера_равномерности_единицей_решателя():
    """√Σ(xᵢ−x̄)² по минутам налёта, усечено до целого (единица
    исполнителя решателя по коду провайдера, COSMOS 03.09); совокупность
    — все сотрудники справочника «Экипажи», без назначений — 0."""
    acols = ["Табельный номер", "Рейс", "Время вылета", "Время прилёта"]
    arows = [("H01", "F1", "2026-09-04T08:00:00", "2026-09-04T10:00:00"),   # 120
             ("H02", "F2", "2026-09-04T08:00:00", "2026-09-04T09:00:00"),   # 60
             ("H02", "F3", "2026-09-04T11:00:00", "2026-09-04T12:00:00")]   # +60
    ds = {"tables": {
        SOL: {"Назначения": {"columns": acols,
                              "rows": [dict(zip(acols, r)) for r in arows]}},
        "D1": {"Экипажи": {"columns": ["Табельный номер"],
                            "rows": [{"Табельный номер": e} for e in ("H01", "H02", "H03")]}},
    }}
    # Решение владельца 03.09: пилот без рейсов ВХОДИТ в разброс, порог
    # правила читается. Часы: H01 2, H02 2, H03 0 → x̄ = 4/3,
    # Σ(x−x̄)² = 2·(2/3)² + (4/3)² = 8/9 + 16/9 = 24/9, √ = 1,63 ч.
    # Порог 10 ч → единиц 0.
    svod = {"rules": [_soft("R-B", "balanced_workload",
                            params={"workload_spread_threshold": 10})]}
    checks, findings = rc.measure_soft_rules(svod, ds)
    assert findings == []
    spec = checks[0]
    assert spec["status"] == "прогнана"
    m = spec["measure"]
    assert m["kind"] == "spread_hours_over_threshold"
    assert m["n"] == 3 and m["spread_hours"] == 1.63 and m["threshold_hours"] == 10.0
    assert m["value"] == 0
    # Порог 1 ч → превышение 0,63 ч → усечено 0; порог 0 → floor(1,63) = 1.
    svod0 = {"rules": [_soft("R-B", "balanced_workload",
                             params={"workload_spread_threshold": 0})]}
    checks0, _f = rc.measure_soft_rules(svod0, ds)
    assert checks0[0]["measure"]["value"] == 1


def test_смена_в_тот_же_день_ночи_не_даёт():
    """Смена окончена вне базы утром, следующая смена того же дня
    возвращает в базу — ночёвки нет (сверка с решателем v4: пять
    «ночей» сертификата были дневными продолжениями). Одинаково у
    обоих путей."""
    rows = [
        ("H20", "d1", "2026-09-04T08:00:00", "2026-09-04T09:30:00", "PLY", "SVG"),
        ("H20", "d2", "2026-09-04T10:20:00", "2026-09-04T11:50:00", "PLY", "PLY"),
    ]
    svod = {"rules": [_soft("R-L", "layover_penalty")]}
    _checks, findings = rc.measure_soft_rules(svod, _dataset(rows))
    assert findings == []
    _checks, findings = rc.measure_soft_rules(svod, _dataset_scope(rows))
    assert findings == []


def test_ночь_вне_окна_не_в_окне_а_последняя_смена_вне_базы_ночует():
    """Прежний путь (область не объявлена): граница горизонта — один
    неопределённый вклад вида boundary; оценка области честно частичная.
    C-38 (имя по смыслу контракта Б — граница горизонта ночёвку не
    создаёт) живёт в тесте объявленной области ниже:
    test_реальная_ночь_считается_а_последняя_смена_не_создаёт_ночёвку."""
    rows = [
        # Воскресенье 06.09 → ночь вне базы, но не в окне пт/сб.
        ("H01", "d1", "2026-09-06T19:00:00", "2026-09-06T20:40:00", "PLY", "GRT"),
        ("H01", "d2", "2026-09-07T07:20:00", "2026-09-07T09:00:00", "PLY", "PLY"),
        # Последняя смена горизонта окончена вне базы: по контракту Б
        # (05.09.2026) граница горизонта — не основание ни начислить
        # ночь, ни отрицать её; продолжение неизвестно → ночь НЕ
        # начисляется автоматически, событие идёт неопределённым
        # вкладом проверки (прежде дописывалась одна ночь — выдуманная
        # ночёвка по границе, аудит 05.09.2026).
        ("H03", "d5", "2026-09-06T19:00:00", "2026-09-06T20:40:00", "PLY", "SVG"),
    ]
    svod = {"rules": [
        _soft("R-L", "layover_penalty"),
        _soft("R-W", "calendar_windows", days=["friday", "saturday"]),
    ]}
    ds = _dataset(rows)
    checks, findings = rc.measure_soft_rules(svod, ds)
    assert _counts(findings) == {"R-L": 1}
    lay = next(c for c in checks if c["basis"]["rule"] == "R-L")
    assert lay["uncertain_events"] == 1
    assert lay["uncertain_by_kind"] == {"boundary": 1}
    # H11-10: вид меры ↔ причина оценки области — одно событие одним словом.
    assert lay["uncertain_by_reason"] == {"terminal_continuation_unknown": 1}
    assert lay["uncertain_sample"][0]["employee"] == "H03"
    assert lay["uncertain_sample"][0]["reason"] == "terminal_continuation_unknown"
    assert "неопределённый вклад" in lay["note"]
    assert next(f for f in findings)["target"]["sheet"] == "Смены"
    # H11-4: без объявленной области путь main — явно частичная
    # диагностика: область не объявлена, сверка событий не проводилась.
    scope = lay["cost_scope"]
    assert scope is ds["cost_assessment"]
    assert scope["source"] == "legacy_duties_sheet" and scope["certainty"] == "partial"
    assert scope["complete"] is False and scope["full_cost_confirmed"] is False
    assert scope["solver_comparison"] is None
    assert scope["known_nights"] == 1 and scope["known_events"][0]["sheet"] == "Смены"
    reasons = [u["reason"] for u in scope["unknown_events"]]
    assert reasons == ["declared_accounting_horizon_unknown", "employee_universe_unknown",
                       "terminal_continuation_unknown"]


def test_реальная_ночь_считается_а_последняя_смена_не_создаёт_ночёвку():
    """Объявленная область (H11-4/H11-5/H11-13): ночь — из известных
    событий независимой оценки, конец последней смены — продолжение
    неизвестно (terminal_continuation_unknown ↔ boundary), запись
    решателя отсутствует → паритет «несравнимо», не зелёный."""
    rows = [
        ("H01", "d1", "2026-09-06T19:00:00", "2026-09-06T20:40:00", "PLY", "GRT"),
        ("H01", "d2", "2026-09-07T07:20:00", "2026-09-07T09:00:00", "PLY", "PLY"),
        # Terminal continuation is unknown, not an automatic extra night.
        ("H03", "d5", "2026-09-06T19:00:00", "2026-09-06T20:40:00", "PLY", "SVG"),
    ]
    svod = {"rules": [
        _soft("R-L", "layover_penalty"),
        _soft("R-W", "calendar_windows", days=["friday", "saturday"]),
    ]}
    ds = _dataset_scope(rows)
    checks, findings = rc.measure_soft_rules(svod, ds)
    assert _counts(findings) == {"R-L": 1}
    lay = next(c for c in checks if c["basis"]["rule"] == "R-L")
    scope = lay["cost_scope"]
    assert scope is ds["cost_assessment"] and scope["source"] == "declared_scope"
    assert scope["task"] == "rostering" and scope["employees"] == ["H01", "H03"]
    assert scope["scope_start"] == "2026-09-04T00:00:00"
    assert scope["known_nights"] == 1
    assert scope["known_events"][0]["employee"] == "H01"
    assert scope["known_events"][0]["night"] == "2026-09-06"
    assert scope["known_events"][0]["airport"] == "GRT"
    # Ночи меры = известные события оценки (адрес — строка «Назначений»).
    assert [f["target"]["row"] for f in findings] == [scope["known_events"][0]["row"]]
    assert findings[0]["target"]["sheet"] == "Назначения"
    # Начальная граница (до первой смены) и конец у обоих — адресные
    # неизвестные, не ноль; конец = boundary словарём меры.
    assert lay["uncertain_by_kind"] == {"boundary": 2,
                                        "initial_boundary_location_interval_unknown": 2}
    assert lay["uncertain_by_reason"]["terminal_continuation_unknown"] == 2
    h03 = next(u for u in lay["uncertain_sample"]
               if u["employee"] == "H03" and u["kind"] == "boundary")
    assert h03["airport"] == "SVG"
    assert lay["uncertain_events"] == scope["unknown_count"] == 4
    assert scope["certainty"] == "partial" and scope["full_cost_confirmed"] is False
    # Записи решателя нет — паритет событий «несравнимо» с причиной.
    assert scope["solver_comparison"]["verdict"] == "INCOMPARABLE"
    assert scope["solver_comparison"]["reasons"] == ["solver_layover_cost_missing"]


def test_незанятый_сотрудник_штата_не_ноль_а_неизвестное_местоположение():
    """Штат объявлен (window_employees): у человека без назначений
    местоположение на всём горизонте неизвестно — адресный вклад
    idle_employee_location_unknown, не нулевая цена."""
    svod = {"rules": [_soft("R-L", "layover_penalty")]}
    ds = _dataset_scope(ROWS, employees=[{"employee": "H09", "base": "PLY"}])
    checks, findings = rc.measure_soft_rules(svod, ds)
    assert _counts(findings) == {"R-L": 1}
    lay = checks[0]
    assert "H09" in lay["cost_scope"]["employees"]
    idle = [u for u in lay["cost_scope"]["unknown_events"]
            if u["reason"] == "idle_employee_location_unknown"]
    assert [u["employee"] for u in idle] == ["H09"]
    assert idle[0]["start"] == "2026-09-04T00:00:00" and idle[0]["end"] == "2026-10-01T00:00:00"
    assert lay["uncertain_by_kind"]["idle_employee_location_unknown"] == 1


def test_две_ночи_между_сменами_дают_два_совпадения():
    rows = [
        ("H01", "d1", "2026-09-04T19:00:00", "2026-09-04T20:40:00", "PLY", "GRT"),
        ("H01", "d2", "2026-09-06T07:20:00", "2026-09-06T09:00:00", "PLY", "PLY"),
    ]
    svod = {"rules": [_soft("R-L", "layover_penalty")]}
    for ds in (_dataset(rows), _dataset_scope(rows)):
        _checks, findings = rc.measure_soft_rules(svod, ds)
        assert len(findings) == 2
        assert [f["evidence"][-10:] for f in findings] == ["2026-09-04", "2026-09-05"]


def _chain_catalog(*, complete):
    """Каталог одной цепочки из двух смен (PLY→GRT в пятницу, GRT→PLY в
    субботу) с областью учёта цены контракта Б: универсум приписок
    {PLY}, полнота — параметр."""
    from datetime import timedelta
    from vmp import chain_price as cp
    scope = cp.scope_template(["PLY"], timedelta(0), eligible_bases_complete=complete)
    catalog = {"selected": [{"fns": ["F1", "F2"], "duty_fns": [["F1"], ["F2"]]}],
               "config_sig": {"price_model": cp.VERSION, "price_scope": scope,
                              "tz_offset_min": 0, "price_criterion": "weights",
                              "w_night": 10, "soft_rule_ids": "layover_penalty=R-L"}}
    fmap = {"F1": {"t0": "2026-09-04T19:00", "t1": "2026-09-04T20:40",
                   "dep": "PLY", "arr": "GRT"},
            "F2": {"t0": "2026-09-05T07:20", "t1": "2026-09-05T09:00",
                   "dep": "GRT", "arr": "PLY"}}
    return catalog, fmap


def test_неполный_универсум_приписок_ночь_вне_баз_не_доказана_у_меры_и_пересчёта():
    """P-425/P-402 (ревью группы P): при неполном универсуме приписок
    (у сотрудника постановки нет homeAirport) станция вне объявленных баз
    ночёвкой не доказана — мера сертификата (лист «Смены» из каталога)
    и независимый пересчёт цены (vmp.chain_price) дают 0 доказанных
    ночей и одну причину incomplete_home_universe; при полном универсуме
    у обоих ровно одна ночь в GRT."""
    svod = {"rules": [_soft("R-L", "layover_penalty")]}
    catalog, fmap = _chain_catalog(complete=False)
    solution = rc.pairing_input_from_catalog(catalog, fmap)
    assert solution["accounting"]["eligible_bases_complete"] is False
    assert solution["accounting"]["uncertain_bases"] == ["PLY"]
    checks, findings = rc.measure_soft_rules(svod, rc.build_solution_dataset(solution))
    assert findings == []
    spec = checks[0]
    assert spec["status"] == "прогнана"
    assert spec["uncertain_by_kind"]["incomplete_home_universe"] == 1
    assert spec["uncertain_by_reason"]["incomplete_home_universe"] == 1
    sample = next(u for u in spec["uncertain_sample"] if u["kind"] == "incomplete_home_universe")
    assert sample["airport"] == "GRT" and sample["duty"] == "chain-0-duty-0"
    rec = rc.recompute_chain_measures(catalog, fmap)
    assert rec[0]["nights"] == 0 and rec[0]["measures"] == {"layover_penalty": 0}
    assert any("incomplete_home_universe" in (e.get("reasons") or [])
               for e in rec[0]["unknown_events"])
    # Полный универсум: ночь в GRT доказана у обоих.
    catalog_full, fmap_full = _chain_catalog(complete=True)
    solution_full = rc.pairing_input_from_catalog(catalog_full, fmap_full)
    assert solution_full["accounting"]["eligible_bases_complete"] is True
    checks_full, findings_full = rc.measure_soft_rules(
        svod, rc.build_solution_dataset(solution_full))
    assert _counts(findings_full) == {"R-L": 1} and "GRT" in findings_full[0]["text"]
    assert "incomplete_home_universe" not in (checks_full[0].get("uncertain_by_kind") or {})
    rec_full = rc.recompute_chain_measures(catalog_full, fmap_full)
    assert rec_full[0]["nights"] == 1 and rec_full[0]["measures"] == {"layover_penalty": 1}
    # Каталог без области учёта (нет price_scope): полнота не объявлена —
    # как прежде, ночь вне строки --bases считается.
    legacy = {"selected": catalog["selected"], "config_sig": {"bases": "PLY"}}
    solution_legacy = rc.pairing_input_from_catalog(legacy, fmap)
    assert solution_legacy["accounting"]["eligible_bases_complete"] is None
    _c, findings_legacy = rc.measure_soft_rules(
        svod, rc.build_solution_dataset(solution_legacy))
    assert _counts(findings_legacy) == {"R-L": 1}


def test_решатель_с_теми_же_событиями_даёт_паритет_а_другая_станция_расходится():
    """Паритет по СОБЫТИЯМ (H11-2): та же сумма при другой станции —
    расхождение, не совпадение."""
    from copy import deepcopy
    from vmp.run.cost_scope import assess_roster_cost
    svod = {"rules": [_soft("R-L", "layover_penalty")]}
    ds = _dataset_scope(ROWS)
    solver = assess_roster_cost(ds)
    # Запись решателя (Kotlin Night.report): date/station вместо night/airport.
    solver["known_events"] = [{"employee": e["employee"], "date": e["night"],
                               "station": e["airport"], "from": "seat"}
                              for e in solver["known_events"]]
    ds["solver_layover_cost"] = deepcopy(solver)
    checks, _ = rc.measure_soft_rules(svod, ds)
    assert checks[0]["cost_scope"]["solver_comparison"]["verdict"] == "OK"
    bad = deepcopy(solver)
    bad["known_events"][0]["station"] = "SVG"
    ds2 = _dataset_scope(ROWS)
    ds2["solver_layover_cost"] = bad
    checks2, _ = rc.measure_soft_rules(svod, ds2)
    cmp2 = checks2[0]["cost_scope"]["solver_comparison"]
    assert cmp2["verdict"] == "DIVERGE" and not cmp2["known_events_match"]
