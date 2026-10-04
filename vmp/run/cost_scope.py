"""Independent contract-B night evidence, never a synthetic boundary charge.

Only gaps between different normative duties are rest intervals. Chain IDs
are irrelevant. The declared employee/horizon universe is preserved, including
idle people. Boundary location evidence must cover the whole interval; a
snapshot at its last instant or a home-airport departure cannot fill a gap.
"""
from datetime import datetime, timedelta, timezone


def _dt(value):
    try:
        result = datetime.fromisoformat(str(value))
        return (result.astimezone(timezone.utc).replace(tzinfo=None)
                if result.tzinfo is not None else result)
    except (ValueError, TypeError):
        return None


def _code(value):
    return str(value or "").strip() or None


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else None


def assess_roster_cost(dataset):
    tables = (dataset.get("tables") or {}).get("SOL") or {}
    assignments = (tables.get("Назначения") or {}).get("rows") or []
    context = dataset.get("cost_context") or {}
    horizon = dataset.get("window_horizon") or {}
    start, end = _dt(horizon.get("start_inclusive")), _dt(horizon.get("end_exclusive"))
    horizon_valid = start is not None and end is not None and start < end
    groups, home_values, nights, unknown, invalid_nights = {}, {}, {}, [], set()

    def uncertain(emp, reason, a=None, b=None, station=None):
        record = {"employee": emp, "reason": reason, "start": _iso(a),
                  "end": _iso(b), "station": station}
        if record not in unknown:
            unknown.append(record)

    people = dataset.get("window_employees")
    if not isinstance(people, list) or not people:
        uncertain(None, "employee_universe_unknown", start, end)
        people = []
    for person in people:
        if not isinstance(person, dict) or not _code(person.get("employee")):
            uncertain(None, "employee_identity_unknown", start, end)
            continue
        emp = _code(person["employee"])
        groups.setdefault(emp, [])
        home_values.setdefault(emp, set()).add(_code(person.get("base")))
    for i, row in enumerate(assignments, 2):
        if not isinstance(row, dict) or not _code(row.get("Табельный номер")):
            uncertain(None, "employee_identity_unknown", start, end)
            continue
        emp = _code(row["Табельный номер"])
        groups.setdefault(emp, []).append((i, row))
        home_values.setdefault(emp, set()).add(_code(row.get("База приписки")))
    locations = {}
    for i, location in enumerate(dataset.get("cost_history_intervals") or []):
        if not isinstance(location, dict) or not _code(location.get("employee")):
            uncertain(None, "history_interval_invalid", start, end)
            continue
        emp = _code(location["employee"])
        groups.setdefault(emp, [])
        a, b, station = _dt(location.get("start")), _dt(location.get("end")), _code(location.get("station"))
        if a is None or b is None or b <= a or not station:
            uncertain(emp, "history_interval_invalid", a, b, station)
            continue
        locations.setdefault(emp, []).append((a, b, station, i))

    def clip(a, b):
        if horizon_valid:
            a, b = max(a, start), min(b, end)
        return a, b

    for emp, rows in sorted(groups.items()):
        homes = home_values.get(emp, set()) - {None}
        home = next(iter(homes)) if len(homes) == 1 else None
        if home is None:
            uncertain(emp, "home_base_unknown", start, end)
        valid, invalid_time = [], False
        for i, row in rows:
            a, b = _dt(row.get("Время вылета")), _dt(row.get("Время прилёта"))
            if a is None or b is None or b < a:
                uncertain(emp, "time_unknown", a, b)
                invalid_time = True
            else:
                valid.append((a, b, i, row))
        valid.sort(key=lambda r: (r[0], r[1], str(r[3].get("Рейс"))))
        evidence = locations.get(emp, [])

        def add_nights(a, b, station, source, row=None, sheet="Назначения", evidence_rows=None):
            if not home or station == home:
                return
            a, b = clip(a, b)
            day = a.date()
            while day < b.date():
                key = (emp, day)
                prior = nights.get(key)
                if prior is not None and prior["airport"] != station:
                    invalid_nights.add(key)
                    nights.pop(key, None)
                    uncertain(emp, "location_evidence_conflict", a, b, station)
                elif key not in invalid_nights:
                    nights.setdefault(key, {
                        "employee": emp, "night": day.isoformat(), "airport": station,
                        "row": row, "sheet": sheet if row is not None else None,
                        "source": source, "evidence_rows": evidence_rows,
                        "start": a.isoformat(), "end": b.isoformat()})
                day += timedelta(days=1)

        def evidence_gap(a, b, missing_reason, *, first_station=None,
                         last_station=None, row=None):
            """Union explicit evidence; contradictory overlap is never known."""
            if a is None or b is None:
                return
            a, b = clip(a, b)
            if b <= a:
                return
            relevant = [x for x in evidence if x[0] < b and x[1] > a]
            points = sorted({a, b} | {max(a, x[0]) for x in relevant}
                            | {min(b, x[1]) for x in relevant})
            pieces = []
            for left, right in zip(points, points[1:]):
                stations = {x[2] for x in relevant if x[0] <= left and x[1] >= right}
                contributors = {x[3] for x in relevant if x[0] <= left and x[1] >= right}
                station = next(iter(stations)) if len(stations) == 1 else None
                reason = (missing_reason if not stations else
                          "location_evidence_conflict" if len(stations) > 1 else None)
                if reason is None and ((left == a and first_station and station != first_station)
                                       or (right == b and last_station and station != last_station)):
                    reason = "discontinuous_timeline"
                if pieces and pieces[-1][1] == left and pieces[-1][2:4] == [station, reason]:
                    pieces[-1][1] = right
                    pieces[-1][4].update(contributors)
                else:
                    pieces.append([left, right, station, reason, contributors])
            for previous, following in zip(pieces, pieces[1:]):
                if previous[3] is None and following[3] is None and previous[2] != following[2]:
                    uncertain(emp, "location_evidence_discontinuity", previous[1], following[0], previous[2])
            for left, right, station, reason, contributors in pieces:
                if reason:
                    uncertain(emp, reason, left, right,
                              station if station else (first_station or last_station))
                else:
                    source_rows = sorted(i + 2 for i in contributors)
                    add_nights(left, right, station, "explicit_history_union", source_rows[0],
                               "История местоположений", source_rows)

        if not valid:
            if horizon_valid:
                evidence_gap(start, end, "idle_employee_location_unknown")
            continue
        first, last = valid[0], valid[-1]
        first_station, last_station = _code(first[3].get("Аэропорт вылета")), _code(last[3].get("Аэропорт прилёта"))
        if horizon_valid and first[0] > start:
            evidence_gap(start, first[0], "initial_boundary_location_interval_unknown",
                         last_station=first_station, row=first[2])
        for left, right in zip(valid, valid[1:]):
            a, b = left[1], right[0]
            station, next_station = _code(left[3].get("Аэропорт прилёта")), _code(right[3].get("Аэропорт вылета"))
            reason = ("overlapping_assignments" if b < a else
                      "station_unknown" if not station or not next_station else
                      "discontinuous_timeline" if station != next_station else None)
            if reason:
                uncertain(emp, reason, a, b, station)
                continue
            if b <= a:
                continue
            conflicts = [x for x in evidence if x[0] < b and x[1] > a and x[2] != station]
            if conflicts:
                uncertain(emp, "location_evidence_conflict", a, b, station)
                continue
            left_duty, right_duty = _code(left[3].get("Смена")), _code(right[3].get("Смена"))
            if left_duty and left_duty == right_duty:
                continue
            count_a, count_b = clip(a, b)
            if count_b <= count_a or count_a.date() == count_b.date() or station == home:
                continue
            if not left_duty or not right_duty or invalid_time:
                uncertain(emp, "normative_duty_identity_unknown", count_a, count_b, station)
                continue
            add_nights(a, b, station, "adjacent_assignments", left[2])
        if horizon_valid and last[1] < end:
            # A shorter closed_scope(at=last.end) cannot erase the remaining
            # declared horizon. Only location evidence closes this interval.
            evidence_gap(last[1], end, "terminal_continuation_unknown",
                         first_station=last_station, row=last[2])
    if not horizon_valid:
        uncertain(None, "declared_accounting_horizon_unknown", start, end)
    for slot in context.get("unassigned_required_slots") or []:
        unknown.append({"employee": None, "reason": "unassigned_slot", "start": None,
                        "end": None, "station": None, **slot})
    return {
        "version": 2, "contract": "B", "task": "rostering",
        "scope": "actual_home_known_assignment_gaps_and_explicit_history_intervals",
        "night_definition": "calendar_date_transition_at_station",
        "duty_semantics": "normative_duty_identity", "timezone": "input_timestamp_calendar",
        "scope_start": _iso(start), "scope_end": _iso(end),
        "employees": sorted(groups),
        "known_nights": len(nights), "known_events": list(nights.values()),
        "unknown_count": len(unknown), "unknown_events": unknown,
        "complete": not unknown,
        "certainty": "complete_in_declared_scope" if not unknown else "partial",
        "full_cost_confirmed": False,  # Only nights, not all objective terms.
    }
