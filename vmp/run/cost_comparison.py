"""Compare independent roster night evidence, never just aggregate prices."""
from collections import Counter
from datetime import datetime, timezone


def _moment(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat(timespec="microseconds")


def compare_roster_cost(independent, solver):
    if not isinstance(solver, dict):
        return {"verdict": "INCOMPARABLE", "known_events_match": False,
                "same_scope": False, "uncertainty_match": False,
                "reasons": ["solver_layover_cost_missing"]}
    reasons = []
    numeric_unavailable = []
    try:
        scope_keys = ("version", "scope", "night_definition", "timezone", "duty_semantics")
        same_scope = all(independent.get(k) is not None and independent.get(k) == solver.get(k)
                         for k in scope_keys)
        for key in ("scope_start", "scope_end"):
            same_scope = same_scope and independent.get(key) is not None \
                and _moment(independent.get(key)) == _moment(solver.get(key))
        independent_people, solver_people = independent.get("employees"), solver.get("employees")
        same_people = (isinstance(independent_people, list) and isinstance(solver_people, list)
                       and bool(independent_people)
                       and all(isinstance(p, str) and p for p in independent_people + solver_people)
                       and len(set(independent_people)) == len(independent_people)
                       and len(set(solver_people)) == len(solver_people)
                       and sorted(independent_people) == sorted(solver_people))
        same_scope = same_scope and same_people
        if not same_scope:
            reasons.append("accounting_scope_mismatch")

        def events(record):
            rows = record.get("known_events")
            if not isinstance(rows, list):
                raise ValueError("known_events_missing")
            keys = [(str(row["employee"]), str(row.get("night", row.get("date"))),
                     str(row.get("airport", row.get("station")))) for row in rows]
            if any("None" in row or "" in row for row in keys):
                raise ValueError("known_event_identity_missing")
            if len(set(keys)) != len(keys) or record.get("known_nights") != len(keys):
                raise ValueError("known_event_count_or_duplicate")
            return Counter(keys)

        known_match = events(independent) == events(solver)
        if not known_match:
            reasons.append("known_event_identity_mismatch")

        def unknowns(record, *, from_solver=False):
            rows = record.get("unknown_events")
            if not isinstance(rows, list) or record.get("unknown_count") != len(rows):
                raise ValueError("unknown_event_count_mismatch")
            expected_complete = not rows
            if record.get("complete") is not expected_complete or record.get("certainty") != (
                    "complete_in_declared_scope" if expected_complete else "partial"):
                raise ValueError("cost_certainty_mismatch")
            keys = []
            for row in rows:
                reason = row.get("reason")
                if not isinstance(reason, str) or not reason:
                    raise ValueError("unknown_event_reason_missing")
                if from_solver and reason == "known_cost_overflow":
                    # A numeric carrier failure is not a different physical
                    # night. It still prevents acceptance of the cost; never
                    # discard it into OK or let it hide an event/scope mismatch.
                    if (numeric_unavailable or "known_subtotal" not in record
                            or record["known_subtotal"] is not None
                            or record.get("night_cost_confirmed") is not False
                            or row.get("employee") is not None or row.get("station") is not None
                            or _moment(row.get("start")) != _moment(record.get("scope_start"))
                            or _moment(row.get("end")) != _moment(record.get("scope_end"))):
                        raise ValueError("invalid_known_cost_overflow")
                    numeric_unavailable.append("solver_known_cost_overflow")
                    continue
                # Physical employee/time/station addresses, not implementation
                # row numbers or solver-only internal seat object identities.
                keys.append((str(row.get("employee")), reason,
                             _moment(row.get("start")), _moment(row.get("end")),
                             str(row.get("station"))))
            return Counter(keys)

        uncertainty_match = unknowns(independent) == unknowns(solver, from_solver=True)
        if not uncertainty_match:
            reasons.append("unknown_event_scope_mismatch")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"verdict": "INCOMPARABLE", "known_events_match": False,
                "same_scope": False, "uncertainty_match": False,
                "reasons": ["invalid_cost_evidence", str(exc)]}
    verdict = "DIVERGE" if reasons else ("INCOMPARABLE" if numeric_unavailable else "OK")
    return {"verdict": verdict,
            "same_scope": same_scope, "known_events_match": known_match,
            "uncertainty_match": uncertainty_match, "reasons": reasons + numeric_unavailable}
