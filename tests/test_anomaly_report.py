"""Отчёт об аномалиях истории (Ф6): детерминированные статистические
проверки поверх валидаций — выбросы налёта, дубликаты, «мёртвые души»,
смешанные месяцы, недоступность на весь горизонт."""
from webapp import anomaly_report


def _flight(fn, day, dep="08:00", arr="10:00", month="03"):
    return {"flightNumber": fn,
            "departureAirport": "AAA", "arrivalAirport": "BBB",
            "departureUTCDateTime": f"2026-{month}-{day:02d}T{dep}:00",
            "arrivalUTCDateTime": f"2026-{month}-{day:02d}T{arr}:00"}


def _payload(n_emps=10, per_emp_flights=3):
    """n_emps employees, each assigned per_emp_flights 2h flights spread
    over distinct days (>=10 horizon days)."""
    flights, assigns, employees = [], [], []
    fn = 0
    for e in range(n_emps):
        emp_id = f"P{e}"
        employees.append({"id": emp_id, "homeAirport": "AAA",
                          "skills": ["Captain"], "name": f"Pilot {e}"})
        for k in range(per_emp_flights):
            fn += 1
            day = (fn - 1) % 27 + 1
            flights.append(_flight(f"F{fn}", day))
            assigns.append({"id": f"a{fn}", "flight": f"F{fn}",
                            "indexInFlight": 0, "requiredSkill": "Captain",
                            "employee": emp_id})
    return {"airports": [{"code": "AAA"}, {"code": "BBB"}],
            "employees": employees, "flights": flights,
            "flightAssignments": assigns}


def test_clean_history_has_no_warnings():
    out = anomaly_report.report(_payload())
    assert out["warnings"] == []
    assert out["stats"]["employees"] == 10
    assert out["stats"]["with_assignments"] == 10
    assert out["stats"]["median_block_h"] == 6.0


def test_block_time_outlier_flagged():
    p = _payload()
    # P0 flies 10 extra long flights -> way above the IQR fence
    for k in range(10):
        fn = f"X{k}"
        p["flights"].append(_flight(fn, k + 1, dep="12:00", arr="20:00"))
        p["flightAssignments"].append(
            {"id": f"ax{k}", "flight": fn, "indexInFlight": 0,
             "requiredSkill": "Captain", "employee": "P0"})
    out = anomaly_report.report(p)
    assert any("выброс налёта: P0" in w for w in out["warnings"])


def test_dead_souls_and_duplicates():
    p = _payload()
    p["employees"].append({"id": "GHOST", "homeAirport": "AAA",
                           "skills": ["Captain"], "name": "Pilot 0"})
    p["employees"].append({"id": "P0", "homeAirport": "AAA",
                           "skills": ["Captain"], "name": "Copy"})
    out = anomaly_report.report(p)
    assert any("без единого назначения" in w and "GHOST" in w
               for w in out["warnings"])
    assert any("дубликаты табельных номеров" in w and "P0" in w
               for w in out["warnings"])
    assert any("тёзки" in w and "pilot 0" in w for w in out["warnings"])


def test_mixed_months_flagged():
    p = _payload()
    p["flights"].append(_flight("STRAY1", 5, month="04"))
    p["flights"].append(_flight("STRAY2", 6, month="04"))
    out = anomaly_report.report(p)
    assert any("вне основного месяца 2026-03" in w for w in out["warnings"])


def test_unavailable_whole_horizon_flagged():
    p = _payload()
    days = sorted({f["departureUTCDateTime"][:10] for f in p["flights"]})
    p["employees"][3]["unavailableDays"] = days          # all horizon days
    out = anomaly_report.report(p)
    assert any("P3" in w and "дней горизонта" in w for w in out["warnings"])


def test_warnings_capped():
    p = _payload()
    # 40 namesake PAIRS on distinct ids -> 40 warnings, above the cap
    for i in range(40):
        for suffix in ("a", "b"):
            p["employees"].append({"id": f"G{i}{suffix}",
                                   "homeAirport": "AAA",
                                   "skills": ["Captain"],
                                   "name": f"Ghost {i}"})
    out = anomaly_report.report(p)
    assert len(out["warnings"]) <= anomaly_report.MAX_ITEMS + 1
    assert any(w.startswith("… и ещё") for w in out["warnings"])


def test_empty_payload_is_quiet():
    out = anomaly_report.report({})
    assert out["warnings"] == []
    assert out["stats"]["employees"] == 0
