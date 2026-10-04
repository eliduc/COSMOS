# -*- coding: utf-8 -*-
"""Самотест детерминированного режима (Д1): два запуска на одном профиле
обязаны дать тождественный канонический выход; расхождение и неполные
данные молчаливым успехом не считаются."""
from vmp.run import selftest_d1 as st
from vmp import signatures as sg


def _roster(score="0hard/-5soft", pairs=(("E01", "L1", "2026-09-04T08:00"),)):
    return {"solved": {"employees": [{"id": e} for e, _l, _t in pairs],
                       "flights": [{"flightNumber": l,
                                    "departureUTCDateTime": t}
                                   for _e, l, t in pairs],
                       "score": score,
                       "flightAssignments": [
                           {"employee": {"id": e}, "flightLeg": {"id": l}}
                           for e, l, _t in pairs]},
            "constraints": {"Rest": {"score": "0hard/-5soft", "matches": 1}},
            "status": "SOLVING_COMPLETED"}


def test_two_identical_runs_pass_and_diff_is_caught():
    a = st.run_signature("rostering", _roster())
    b = st.run_signature("rostering", _roster())
    card = st.compare(a, b)
    assert card["verdict"] == "сошлось"
    other = st.run_signature("rostering", _roster(score="0hard/-9soft"))
    bad = st.compare(a, other)
    assert bad["verdict"] == "разошлось"
    assert bad["diff_hint"], bad
    # Неполные данные — «несравнимо», не успех.
    assert st.compare(a, {})["verdict"] == "несравнимо"


def test_selftest_card_requires_both_tasks():
    a = st.run_signature("rostering", _roster())
    catalog = {"selected": [{"cost": 10.0, "legs": ["L1"]}], "ip_objective": 10.0}
    p = st.run_signature("pairing", {"catalog": catalog, "status": "OK"})
    card = st.selftest_card({"rostering": [a, a], "pairing": [p, p]})
    assert card["passed"] is True
    assert card["verdicts"] == {"rostering": "сошлось", "pairing": "сошлось"}
    # Одна задача без пары запусков — самотест не пройден.
    half = st.selftest_card({"rostering": [a, a], "pairing": [p]})
    assert half["passed"] is False
    assert half["verdicts"]["pairing"] == "несравнимо"
    assert "не стартует" in half["note"]
    assert "САМОТЕСТ Д1: НЕ ПРОЙДЕН" in st.render(half)


def test_card_carries_profile_and_task_signature():
    prof = sg.deterministic_profile("rostering", seed=0, step_limit=20000,
                                    unimproved_step_limit=2000)
    a = st.run_signature("rostering", _roster())
    card = st.compare(a, a, profile=prof, task_signature={"sha256": "abc"})
    assert card["profile_stamp"] == prof["stamp"]
    assert card["task_signature"] == {"sha256": "abc"}
