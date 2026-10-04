"""Closed, shared input contracts for catalogue stand expectations."""


_KNOB_READING_FIELDS = {"param", "threshold", "unit", "relation", "input_value",
                        "value_unit", "value", "provider_value"}
# A bounded measurement vocabulary, not a second catalogue of executable
# mechanisms. The native reader/resolver/provider and the declared stand world
# remain the sources of measurements. In particular this schema grants neither
# kotlin_constraint coverage nor admission of any task mode.
_KNOB_READINGS = {
    "workload_spread_threshold": ("workload_spread_threshold_min", "credit_hours",
                                   ("max",), "duration_minutes", "hours"),
    "crew_size": ("crew_size", "count", ("eq",), "count", "integer"),
    "fleet_types": ("fleet_types", "code_list", (None,), "code_list", "codes"),
    "reserve_crews": ("reserve_crews_min", "count", ("min", "eq"), "count", "keyed"),
    "min_rest_home": ("min_rest_home_min", "duration_minutes", ("min",),
                       "duration_minutes", "integer"),
    "min_rest_outstation": ("min_rest_outstation_min", "duration_minutes", ("min",),
                             "duration_minutes", "integer"),
    "base_tz_offset_min": ("base_tz_offset_min", "duration_minutes", ("eq",),
                           "duration_minutes", "zero"),
}
_KNOB_GROUPS = {
    frozenset(("workload_spread_threshold",)), frozenset(("crew_size",)),
    frozenset(("fleet_types",)), frozenset(("reserve_crews",)),
    frozenset(("min_rest_home", "min_rest_outstation")),
    frozenset(("base_tz_offset_min",)),
}


def _native_integer(value, minimum=1):
    # bool is an int subclass; accepting it would make False a false zero proof.
    return type(value) is int and minimum <= value <= 2147483647


def _code(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def _knob_readings_problem(expected):
    if (set(expected) != {"profile", "readings", "refusals"}
            or expected["profile"] != "typed-knob-readings/1"):
        return "kotlin_knob typed profile requires exact profile/readings/refusals"
    readings = expected["readings"]
    if not isinstance(readings, list) or not readings:
        return "kotlin_knob typed profile requires nonempty readings"
    params = set()
    for row in readings:
        if not isinstance(row, dict) or set(row) != _KNOB_READING_FIELDS:
            return "kotlin_knob reading requires exact input/resolved/provider fields"
        param = row["param"]
        if not isinstance(param, str) or param not in _KNOB_READINGS or param in params:
            return "kotlin_knob reading has unknown or repeated parameter"
        params.add(param)
        threshold, unit, relations, value_unit, shape = _KNOB_READINGS[param]
        if (row["threshold"] != threshold or row["unit"] != unit
                or row["relation"] not in relations or row["value_unit"] != value_unit):
            return "kotlin_knob reading identity/unit/relation mismatch"
        values = [row[k] for k in ("input_value", "value", "provider_value")]
        if shape == "codes":
            if any(not isinstance(v, list) or not v or any(not _code(c) for c in v)
                   or len(set(v)) != len(v) or v != sorted(v) for v in values):
                return "kotlin_knob code reading requires exact sorted unique labels"
        elif shape == "keyed":
            if any(not isinstance(v, dict) or not v
                   or any(not _code(k) or not _native_integer(n) for k, n in v.items())
                   for v in values):
                return "kotlin_knob keyed reading requires exact base/count maps"
        elif any(not _native_integer(v, 0 if shape in ("hours", "zero") else 1)
                 for v in values):
            return "kotlin_knob scalar reading requires exact native integers"
        converted = values[0] * 60 if shape == "hours" else values[0]
        if values[1] != converted or values[2] != values[1]:
            return "kotlin_knob input/resolved/provider measurements differ"
        if shape == "zero" and converted != 0:
            return "kotlin_knob nonzero time-scale offset is not implemented"
    if frozenset(params) not in _KNOB_GROUPS:
        return "kotlin_knob profile has incomplete or mixed family readings"
    refusals = expected["refusals"]
    if not isinstance(refusals, list) or refusals:
        return "kotlin_knob readings cannot embed another input's refusal"
    return None


def _knob_refusal_problem(expected):
    # A negative control has its OWN declared task/world/native bytes and is
    # imported under that identity. It cannot be attached to a healthy task's
    # reading: doing so would leave the rejected input outside the hash census.
    if (set(expected) != {"profile", "param", "unit", "relation", "input_value", "refused", "reason"}
            or expected["profile"] != "typed-knob-refusal/1"
            or expected["param"] != "base_tz_offset_min"
            or expected["unit"] != "duration_minutes" or expected["relation"] != "eq"
            or type(expected["input_value"]) is not int or expected["input_value"] != 60
            or expected["refused"] is not True or expected["reason"] != "nonzero_scale_offset"):
        return "time-scale nonzero refusal must be exact and typed"
    return None


def component_expectation_problem(kind, expected):
    """Only measured typed values/facts, never another kind's green verdict."""
    if not isinstance(expected, dict):
        return "component expectation must be an object"
    if kind == "kotlin_knob":
        if "profile" in expected:
            if expected["profile"] == "typed-knob-refusal/1":
                return _knob_refusal_problem(expected)
            return _knob_readings_problem(expected)
        # Existing immutable duty proof keeps its original six-field wire.
        if set(expected) != {"param", "threshold", "unit", "relation", "value", "provider_value"}:
            return "kotlin_knob requires exact typed/provider fields"
        if any(not isinstance(expected[k], str) or not expected[k].strip()
               for k in ("param", "threshold", "unit", "relation")):
            return "kotlin_knob requires nonempty identity/unit/relation"
        if any(type(expected[k]) is not int for k in ("value", "provider_value")):
            return "kotlin_knob requires exact integer measurements"
        return None
    if kind != "kotlin_form" or set(expected) != {"form", "rules", "twins"}:
        return "unsupported component kind or fields"
    if expected["form"] != "calendar_window_penalties":
        return "unsupported Kotlin component form"
    rules = expected["rules"]
    if not isinstance(rules, list) or not rules:
        return "calendar form requires nonempty measured rules"
    days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"rule_id", "action", "days"}:
            return "calendar rule requires exact fields"
        if any(not isinstance(rule[k], str) or not rule[k].strip() for k in ("rule_id", "action")):
            return "calendar rule identity/action missing"
        values = rule["days"]
        if (not isinstance(values, list) or not values or
                any(not isinstance(d, str) or d not in days for d in values) or len(set(values)) != len(values)):
            return "calendar days malformed"
    twins = expected["twins"]
    if not isinstance(twins, list) or len(twins) != 2:
        return "calendar form requires original and healthy shifted twin"
    for index, row in enumerate(twins):
        if not isinstance(row, dict) or set(row) != {
                "name", "flight_shift_seconds", "constraint", "hard_score", "soft_score", "matches", "package_weight"}:
            return "calendar twin fields malformed"
        if row["name"] != ("original", "all_flights_plus_one_day")[index] or row["constraint"] != "Calendar window layover penalty":
            return "calendar twin identity malformed"
        if any(type(row[k]) is not int for k in (
                "flight_shift_seconds", "hard_score", "soft_score", "matches", "package_weight")):
            return "calendar twin measurements must be exact integers"
        if row["flight_shift_seconds"] != (0, 86400)[index] or row["matches"] < 0 or row["package_weight"] <= 0:
            return "calendar twin measurement range malformed"
    return None


def export_form_expectation_problem(expected):
    if isinstance(expected, dict) and expected.get("symbol") == "auto_plan_excluded_fleets":
        if set(expected) != {"symbol", "fleets"}:
            return "export_form fleet expected: нужны ровно symbol и fleets"
        fleets = expected["fleets"]
        if (not isinstance(fleets, list)
                or any(not isinstance(fleet, str) or not fleet.strip() for fleet in fleets)
                or len(set(fleets)) != len(fleets)):
            return "export_form fleets: нужен список уникальных непустых обозначений"
        return None
    if not isinstance(expected, dict) or set(expected) != {"symbol", "probes"}:
        return "export_form expected: нужны ровно symbol и probes"
    if expected["symbol"] not in ("worktime_exact", "required_layover"):
        return "export_form: неизвестный symbol %r" % expected["symbol"]
    probes = expected["probes"]
    if not isinstance(probes, list) or not probes:
        return "export_form probes: нужен непустой список"
    rest = expected["symbol"] == "required_layover"
    fields = ({"work_min", "on_base", "night", "trans", "value_min"} if rest else
              {"minute", "landings", "acclimatized", "fleet", "value_min"})
    for i, probe in enumerate(probes):
        if not isinstance(probe, dict) or set(probe) != fields:
            return "export_form probes[%d]: неверный набор полей" % i
        if rest:
            if not (type(probe["work_min"]) is int and probe["work_min"] >= 0
                    and all(type(probe[field]) is bool for field in ("on_base", "night", "trans"))
                    and type(probe["value_min"]) is int and probe["value_min"] > 0):
                return "export_form probes[%d]: неверный тип или диапазон" % i
            continue
        if not (
                type(probe["minute"]) is int and 0 <= probe["minute"] <= 1439
                and type(probe["landings"]) is int and probe["landings"] > 0
                and type(probe["acclimatized"]) is bool
                and isinstance(probe["fleet"], str) and probe["fleet"].strip()
                and type(probe["value_min"]) is int and probe["value_min"] > 0):
            return "export_form probes[%d]: неверный тип или диапазон" % i
    return None
