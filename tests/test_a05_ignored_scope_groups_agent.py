"""Keep the scope of each published ignored entry when comparing thresholds.

Legacy diagnostic inputs use the existing temporary catalogue fixture and real
validators/projection. The main literal report follows RunThresholds.resolve's
actual branches and snapshot fields; it is NOT captured Kotlin execution.
Controls exercise the existing report format, including list-as-set semantics
inside one entry. No modern landing/2 enum, package gate or full button claim:
the earlier formulation contradiction gate may reject the main input already.
"""
import copy

from cdpm import model as m
from cdpm.param_projection import derive_params_from_landing
from test_a05_landing_numeric_neighbors_agent import (
    clean_bridge,  # noqa: F401
    isolated_consumer_catalog,  # noqa: F401
    landed,  # noqa: F401
)
from webapp import threshold_gate as tg


PARAM = "min_rest_home"
THRESHOLD = "min_rest_home_min"
ALL_AREAS = "в базе и вне базы"


def _model(landed, rows):
    """Rows are (rule id, parameter name, value, relation, qualifier)."""
    doc, original = landed
    model = copy.deepcopy(doc)
    rules = {}
    for rid, name, value, relation, qualifier in rows:
        if rid not in rules:
            rule = copy.deepcopy(original)
            rule.pop("template_fit")
            rule.update(id=rid, kind="HARD", title="Диагностика порога",
                        statement="Числовые требования диагностического входа.", params=[])
            rules[rid] = rule
        param = {"name": name, "value": value, "unit": "duration_minutes",
                 "relation": relation, "as_written": f"{value} минут"}
        if qualifier is not None:
            param["qualifier"] = qualifier
        assert m.validate_param(param, "HARD") == [], param
        rules[rid]["params"].append(param)
    model["rules"] = list(rules.values())
    assert m.validate_schema(model) == []
    assert m.validate_template_fit(model) == []
    pkg = {"model": model, "form": {}}
    before = copy.deepcopy(pkg)
    for rule in model["rules"]:
        assert derive_params_from_landing(rule) == rule["params"]
    assert len(tg.collect_params(pkg)) == len(rows)
    assert pkg == before
    return pkg


def _ignored(rules, qualifiers, reason):
    # RunThresholds.kt:278-283 / 936-938: independent sets within this entry.
    return {"param": PARAM, "rules": rules, "qualifiers": qualifiers, "reason": reason}


def _compare(pkg, ignored, *, readings=None, qualified=None, applied=None):
    args = {
        "readings": {THRESHOLD: 600} if readings is None else readings,
        "profile": "DEFAULT",
        "mapping": {PARAM: THRESHOLD, "max_duty_duration": "max_duty_span_min"},
        "qualified": {PARAM + "|*": THRESHOLD} if qualified is None else qualified,
        "ignored": ignored,
        "applied": [] if applied is None else applied,
    }
    before = copy.deepcopy((pkg, args))
    result = tg.compare(pkg, **args)
    assert (pkg, args) == before
    return result


def _not_applied(result):
    return [v for v in result if v["note"].startswith("решатель не применил:")]


def test_separate_ignored_reasons_do_not_remove_an_applied_cross_scope(landed):
    pkg = _model(landed, [
        ("R1", PARAM, 420, "min", ALL_AREAS),
        ("R2", PARAM, 900, "min", "вне базы"),
        ("R2", PARAM, 600, "min", ALL_AREAS),
    ])
    # DEFAULT literal = 480 (FlightCrewSchedulingConstraintProvider.kt:47).
    # RunThresholds.kt:494-510 ignores R1's weakening; :235-240 rejects
    # R2's outstation scope; :513-514 retains R2/600. :395-400 groups the
    # two different reasons separately, and :936-938 serializes these fields.
    reasons = [
        "уточнитель «в базе и вне базы» покрывает обе области, но применить "
        "ОСЛАБЛЕНИЕ 420 против литерала 480 ко всей ручке min_rest_home_min "
        "нельзя; правило НЕ исполняется",
        "уточнитель «вне базы» противоречит области параметра min_rest_home "
        "(ручка min_rest_home_min — область «база»): правило НЕ исполняется",
    ]
    ignored = [_ignored(["R1"], [ALL_AREAS], reasons[0]),
               _ignored(["R2"], ["вне базы"], reasons[1])]
    # Literal consumer inputs in snapshot schema, not observed provider data.
    applied = [{"threshold": THRESHOLD, "value": 600, "literal": 480,
                "fromParams": [PARAM], "rules": ["R2"], "declarations": [], "codes": []}]
    result = _compare(pkg, ignored, applied=applied)
    refused, = _not_applied(result)
    assert refused["verdict"] == tg.INCOMPARABLE, result
    assert refused["package_values"] == [420, 900], result
    assert all(reason in refused["note"] for reason in reasons), refused
    matched = [v for v in result if v["verdict"] == tg.MATCH]
    assert len(matched) == 1, result
    assert matched[0]["reading"] == THRESHOLD, result
    assert matched[0]["package_values"] == [600] and matched[0]["rules"] == ["R2"], result
    assert matched[0]["qualifiers"] == [ALL_AREAS], result
    assert len(result) == 2, result


def test_one_entry_keeps_cartesian_set_semantics_not_zipped_pairs(landed):
    pkg = _model(landed, [
        ("R1", PARAM, 420, "min", "ST1"),
        ("R1", PARAM, 450, "min", "ST2"),
        ("R2", PARAM, 600, "min", "ST1"),
        ("R2", PARAM, 900, "min", "ST2"),
    ])
    result = _compare(pkg, [_ignored(["R1", "R2"], ["ST1", "ST2"], "Одна причина")])
    refused, = result
    assert refused["verdict"] == tg.INCOMPARABLE, result
    assert refused["package_values"] == [420, 450, 600, 900], result
    assert refused["rules"] == ["R1", "R2"] and refused["qualifiers"] == ["ST1", "ST2"], result


def test_one_exact_ignored_entry_keeps_another_scope_of_the_same_rule(landed):
    pkg = _model(landed, [("R1", PARAM, 900, "min", "вне базы"),
                          ("R1", PARAM, 600, "min", "в базе")])
    result = _compare(pkg, [_ignored(["R1"], ["вне базы"], "Область не применена")])
    refused, = _not_applied(result)
    assert refused["package_values"] == [900], result
    matched, = [v for v in result if v["verdict"] == tg.MATCH]
    assert matched["package_values"] == [600] and matched["qualifiers"] == ["в базе"], result
    assert len(result) == 2, result


def test_global_ignored_without_lists_keeps_name_wide_semantics(landed):
    pkg = _model(landed, [("R1", PARAM, 420, "min", "ST1"),
                          ("R2", PARAM, 600, "min", "ST2"),
                          ("R3", "max_duty_duration", 720, "max", None)])
    result = _compare(pkg, [{"param": PARAM, "reason": "Имя не применяется целиком"}],
                      readings={THRESHOLD: 600, "max_duty_span_min": 720})
    refused, = _not_applied(result)
    assert refused["package_values"] == [420, 600], result
    matched, = [v for v in result if v["verdict"] == tg.MATCH]
    assert matched["params"] == ["max_duty_duration"] and matched["package_values"] == [720], result
    assert len(result) == 2, result


def test_ignored_matched_and_unknown_target_records_each_remain_visible_once(landed):
    pkg = _model(landed, [("R1", PARAM, 900, "min", "ST1"),
                          ("R1", PARAM, 600, "min", "в базе"),
                          ("R1", PARAM, 720, "min", "ST2")])
    # Deliberately incomplete target table: a report-format negative control,
    # not a claim that today's Kotlin snapshot omits its wildcard.
    result = _compare(pkg, [_ignored(["R1"], ["ST1"], "Только ST1 не применено")],
                      qualified={PARAM + "|в базе": THRESHOLD})
    refused, = _not_applied(result)
    assert refused["package_values"] == [900] and refused["qualifiers"] == ["ST1"], result
    matched, = [v for v in result if v["verdict"] == tg.MATCH]
    assert matched["package_values"] == [600], result
    unknown, = [v for v in result if "не нашёл цели" in v["note"]]
    assert unknown["verdict"] == tg.INCOMPARABLE, result
    assert unknown["package_values"] == [720] and unknown["qualifiers"] == ["ST2"], result
    assert len(result) == 3, result
