"""Option-3: industry-KPI report over a solved roster.

Translates our abstract soft score into the vocabulary airline planners and
Jeppesen-class systems use. Computable from flights + assignments only.

Definitions used (all parameters stated explicitly — change at top):
  * duty day        — chain of an employee's flights where consecutive gaps
                      are < DUTY_BREAK_H (6h, mirrors RULES v2); a gap >=
                      DUTY_BREAK_H ends the duty.
  * block hours     — sum of flight (block) time in the duty.
  * duty hours      — first departure .. last arrival of the duty + report
                      REPORT_MIN before + release RELEASE_MIN after.
  * credit hours    — pay-and-credit per duty:
                      max(block, DUTY_RIG * duty_hours, MIN_GUARANTEE_H).
                      Trip rigs / monthly guarantees are out of scope for a
                      single-day dataset and stated as such.
  * TAFB            — time away from base: first report .. last release of
                      the crew member's day if it starts or ends away from
                      home base (single-day approximation).
  * hotel night     — crew member's LAST arrival airport of the day differs
                      from home base (would need a hotel that night).
  * positioning gap — duty starts at an airport different from where the
                      previous duty ended (or from home base for the first
                      duty): an unmodelled deadhead/positioning would be
                      required. We don't schedule deadheads, so we COUNT
                      the events instead.

Usage:
    python _run_kpi_report.py <solved_json> <label> [<solved_json2> <label2> ...]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --- stated KPI parameters (industry-typical, documented) -------------------
DUTY_BREAK_H = 6.0        # gap that ends a duty day (mirrors RULES v2)
REPORT_MIN = 60           # report before first departure of a duty
RELEASE_MIN = 30          # release after last arrival of a duty
DUTY_RIG = 0.5            # credit >= 1:2 of duty time
MIN_GUARANTEE_H = 5.0     # min daily guarantee per worked duty day


def _rid(v, key):
    if v is None:
        return None
    return v.get(key) if isinstance(v, dict) else v


def load(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    flights = {}
    for f in d["flights"]:
        if isinstance(f, dict):
            flights[f["flightNumber"]] = f
    emp_home = {}
    for e in d["employees"]:
        if isinstance(e, dict):
            home = e.get("homeAirport")
            emp_home[e["id"]] = _rid(home, "code")
    legs_by_emp = defaultdict(list)
    for a in d["flightAssignments"]:
        eid = _rid(a.get("employee"), "id")
        fn = _rid(a.get("flight"), "flightNumber")
        if eid is None or fn not in flights:
            continue
        f = flights[fn]
        legs_by_emp[eid].append((
            datetime.fromisoformat(f["departureUTCDateTime"]),
            datetime.fromisoformat(f["arrivalUTCDateTime"]),
            _rid(f.get("departureAirport"), "code"),
            _rid(f.get("arrivalAirport"), "code")))
    return legs_by_emp, emp_home, len(d["flightAssignments"])


def kpis(legs_by_emp, emp_home):
    tot = defaultdict(float)
    for eid, legs in legs_by_emp.items():
        legs.sort()
        home = emp_home.get(eid)
        # split into duties
        duties, cur = [], [legs[0]]
        for prev, nxt in zip(legs, legs[1:]):
            gap_h = (nxt[0] - prev[1]).total_seconds() / 3600
            if gap_h >= DUTY_BREAK_H:
                duties.append(cur)
                cur = [nxt]
            else:
                cur.append(nxt)
        duties.append(cur)

        tot["crew_active"] += 1
        tot["duties"] += len(duties)
        prev_end_ap = home
        for d in duties:
            block = sum((a - dep).total_seconds() / 3600 for dep, a, _, _ in d)
            duty_h = ((d[-1][1] - d[0][0]).total_seconds() / 3600
                      + (REPORT_MIN + RELEASE_MIN) / 60)
            credit = max(block, DUTY_RIG * duty_h, MIN_GUARANTEE_H)
            tot["block_h"] += block
            tot["duty_h"] += duty_h
            tot["credit_h"] += credit
            tot["legs"] += len(d)
            if d[0][2] != prev_end_ap:
                tot["positioning_events"] += 1
            prev_end_ap = d[-1][3]
        if legs[-1][3] != home:
            tot["hotel_nights"] += 1
        # single-day TAFB approximation
        if legs[0][2] != home or legs[-1][3] != home:
            tafb = ((legs[-1][1] - legs[0][0]).total_seconds() / 3600
                    + (REPORT_MIN + RELEASE_MIN) / 60)
            tot["tafb_h"] += tafb
    return tot


def main():
    args = sys.argv[1:]
    if len(args) < 2 or len(args) % 2:
        print(__doc__)
        return 2
    reports = []
    for i in range(0, len(args), 2):
        path, label = Path(args[i]), args[i + 1]
        legs_by_emp, emp_home, n_slots = load(path)
        t = kpis(legs_by_emp, emp_home)
        t["label"], t["slots"] = label, n_slots
        reports.append(t)

    rows = [
        ("Занято экипажа (чел.)",       lambda t: f"{int(t['crew_active'])}"),
        ("Рабочих дней (duty)",         lambda t: f"{int(t['duties'])}"),
        ("Плеч на рабочий день",        lambda t: f"{t['legs']/t['duties']:.2f}"),
        ("Лётные часы (block), всего",  lambda t: f"{t['block_h']:.0f}"),
        ("Служебные часы (duty), всего", lambda t: f"{t['duty_h']:.0f}"),
        ("Block/duty ratio",            lambda t: f"{t['block_h']/t['duty_h']:.2f}"),
        ("Кредитные часы (pay&credit)", lambda t: f"{t['credit_h']:.0f}"),
        ("Pay&credit / block, %",       lambda t: f"{100*t['credit_h']/t['block_h']-100:+.1f}%"),
        ("Block часов на человека",     lambda t: f"{t['block_h']/t['crew_active']:.2f}"),
        ("Ночёвки вне базы (отели)",    lambda t: f"{int(t['hotel_nights'])}"),
        ("TAFB, часов (прибл.)",        lambda t: f"{t['tafb_h']:.0f}"),
        ("Positioning-события (deadhead-потребность)", lambda t: f"{int(t['positioning_events'])}"),
    ]
    w0 = 46
    header = f"{'KPI':<{w0}}" + "".join(f"{t['label']:>24}" for t in reports)
    print(header)
    print("-" * len(header))
    for name, fn in rows:
        print(f"{name:<{w0}}" + "".join(f"{fn(t):>24}" for t in reports))
    print(f"\nПараметры: duty-разрыв {DUTY_BREAK_H}ч; report {REPORT_MIN}м; "
          f"release {RELEASE_MIN}м; duty rig 1:{int(1/DUTY_RIG)}; "
          f"мин. гарантия {MIN_GUARANTEE_H}ч/duty. Один день данных: месячные "
          f"гарантии и trip rig вне охвата.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
