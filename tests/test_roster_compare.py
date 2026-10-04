"""Сравнение ростеров (Ф8): исторические цепочки → пейринги, метрики
сторон, дельта, недоступные стороны, markdown-акт."""
import pytest

from webapp import registry, roster_board, roster_compare as rc


def _payload():
    """Two employees; P1 legally flies two pairings (gap >= rest), P2 has
    an overlap; F5 stays unassigned historically."""
    def fl(fn, dep, arr, day, ac="A320"):
        return {"flightNumber": fn, "departureAirport": "AAA",
                "departureUTCDateTime": f"2026-03-{day:02d}T{dep}:00",
                "arrivalAirport": "BBB",
                "arrivalUTCDateTime": f"2026-03-{day:02d}T{arr}:00",
                "aircraftType": ac}
    flights = [fl("F1", "08:00", "10:00", 1), fl("F2", "12:00", "14:00", 3),
               fl("F3", "08:00", "10:00", 5), fl("F4", "09:00", "11:00", 5),
               fl("F5", "08:00", "10:00", 7)]
    employees = [
        {"id": "P1", "homeAirport": "AAA", "skills": ["Captain"],
         "typeRatings": ["A320"], "name": "One"},
        {"id": "P2", "homeAirport": "AAA", "skills": ["Captain"],
         "typeRatings": ["A320"], "name": "Two"}]
    assigns = [
        {"id": "a1", "flight": "F1", "indexInFlight": 0,
         "requiredSkill": "Captain", "employee": "P1"},
        {"id": "a2", "flight": "F2", "indexInFlight": 0,
         "requiredSkill": "Captain", "employee": "P1"},
        {"id": "a3", "flight": "F3", "indexInFlight": 0,
         "requiredSkill": "Captain", "employee": "P2"},
        {"id": "a4", "flight": "F4", "indexInFlight": 0,
         "requiredSkill": "Captain", "employee": "P2"},
        {"id": "a5", "flight": "F5", "indexInFlight": 0,
         "requiredSkill": "Captain", "employee": None}]
    return {"airports": [{"code": "AAA"}, {"code": "BBB"}],
            "employees": employees, "flights": flights,
            "flightAssignments": assigns}


def test_hist_records_split_on_rest():
    recs = rc._hist_records(_payload(), rest_min=600)
    by_emp = {}
    for r in recs:
        by_emp.setdefault(r["employee"], []).append(r)
    # P1: F1 (day 1) and F2 (day 3) — gap ~2 days >= rest -> two pairings
    assert len(by_emp["P1"]) == 2
    # P2: F3/F4 overlap (gap negative) -> one pairing chain
    assert len(by_emp["P2"]) == 1
    assert by_emp["P2"][0]["active"] == {"F3", "F4"}


def test_historical_side_metrics_and_overlap_violation():
    p = _payload()
    from webapp import costing
    recs = rc._hist_records(p, rest_min=600)
    crew = rc._crew_from_payload(p)
    side = rc._side_metrics(recs, p, crew, {"duty_rig": 2.0,
                                            "trip_rig": 3.5,
                                            "min_duty_credit_h": 0.0,
                                            "monthly_guarantee_h": 5.0},
                            costing.load_cost_config(), 600, hist=True)
    assert side["employees_used"] == 2
    assert side["flights_covered"] == 4 and side["flights_total"] == 5
    assert side["coverage_pct"] == 80.0
    # P2's F3/F4 overlap lives INSIDE one chain -> leg_overlap;
    # home_base/rest silent for hist
    assert side["violations"].get("leg_overlap", 0) >= 1
    assert "home_base" not in side["violations"]
    assert "rest" not in side["violations"]
    assert side["block_h"] == pytest.approx(8.0)      # 4 flights x 2h
    # guarantee 5h: each crew below -> paid = 2 x 5h = 10
    assert side["paid_h"] == pytest.approx(10.0)
    assert side["guarantee_shortfall_crews"] == 2


def test_build_report_sides_unavailable(monkeypatch):
    p = _payload()
    p["flightAssignments"] = [dict(a, employee=None)
                              for a in p["flightAssignments"]]
    monkeypatch.setattr(registry, "load_payload", lambda pth: p)
    monkeypatch.setattr(roster_board, "_resolve",
                        lambda inst: (None, None, None))
    inst = {"id": "x", "label": "x", "payload_path": "data/none.json"}
    rep = rc.build_report("x", inst)
    assert rep["ours"] == {"available": False, "reason": "no_stage2_roster"}
    assert rep["historical"]["available"] is False
    assert rep["delta"] is None
    md = rc.markdown_comparison(rep)
    # H08: retitled to a DIAGNOSTIC comparison (not a legal acceptance act)
    assert "Диагностическое сравнение ростеров" in md
    assert "диагностическое сравнение" in md.lower()   # the disclaimer
    assert "no_stage2_roster" in md
    # Аудит 2026-07-19 (P2-03): таблица подписей превращала диагностический
    # документ в акт приёмки — её больше нет.
    assert "Подписи" not in md
    # Обратная совместимость старого имени.
    assert rc.markdown_act is rc.markdown_comparison


def test_build_report_with_both_sides(monkeypatch):
    p = _payload()
    monkeypatch.setattr(registry, "load_payload", lambda pth: p)
    # fake stage-2: one pairing per flight F1..F5, all to P1 (overloaded)
    pairings = []
    art = []
    for i, f in enumerate(p["flights"]):
        fn = f["flightNumber"]
        pairings.append({
            "pid": f"p{i}", "solver_employee": "P1", "base": "AAA",
            "dep_utc": f["departureUTCDateTime"],
            "arr_utc": f["arrivalUTCDateTime"],
            "nlegs": 1, "aircraft": ["A320"],
            "days": [f["departureUTCDateTime"][:10]],
            "legs": [{"fn": fn, "dep_utc": f["departureUTCDateTime"],
                      "arr_utc": f["arrivalUTCDateTime"]}]})
        art.append({"pid": f"p{i}", "fns": [fn], "active_fns": [fn]})
    crew = rc._crew_from_payload(p)
    monkeypatch.setattr(roster_board, "_resolve",
                        lambda inst: (pairings, crew, []))
    monkeypatch.setattr(roster_board, "_assignment",
                        lambda inst: {"pairings": art})
    monkeypatch.setattr(roster_board, "_load", lambda: {})
    inst = {"id": "x", "label": "x", "payload_path": "data/none.json"}
    rep = rc.build_report("x", inst)
    assert rep["ours"]["available"] and rep["historical"]["available"]
    assert rep["ours"]["coverage_pct"] == 100.0
    assert rep["delta"]["coverage_pct"] == pytest.approx(20.0)
    # our fake roster overloads P1 on day 5 (F3+F4 overlap)
    assert rep["ours"]["violations"].get("overlap", 0) >= 1
    md = rc.markdown_comparison(rep)
    assert "| Покрытие рейсов, % | 100.0 | 80.0 |" in md
