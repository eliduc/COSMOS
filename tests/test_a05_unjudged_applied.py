"""The bridge's final aggregation must not promote an unjudged applied claim.

Pure verdict aggregation; no live bridge, JVM, HTTP or solve is executed.
"""
from webapp import threshold_gate as tg


def test_resolver_claimed_fleet_is_not_proven_by_an_incomparable_result():
    verdicts = [{"verdict": tg.INCOMPARABLE, "params": ["fleet_types"]},
                {"verdict": tg.MATCH, "params": ["max_duty_duration"]}]
    applied = [{"fromParams": ["fleet_types"]}, {"fromParams": ["max_duty_duration"]}]
    assert tg.unjudged_claimed_params(verdicts, applied) == {"fleet_types"}


def test_future_claimed_parameter_is_not_hidden_by_a_python_name_whitelist():
    assert tg.unjudged_claimed_params(
        [{"verdict": tg.INCOMPARABLE, "params": ["new_declared_parameter", "advisory"]}],
        [{"fromParams": ["new_declared_parameter"]}]) == {"new_declared_parameter"}


def test_healthy_match_and_explicitly_unapplied_advisory_are_not_new_failures():
    verdicts = [{"verdict": tg.MATCH, "params": ["fleet_types"]},
                {"verdict": tg.INCOMPARABLE, "params": ["advisory"]}]
    assert tg.unjudged_claimed_params(verdicts, [{"fromParams": ["fleet_types"]}]) == set()
    assert tg.unjudged_claimed_params(verdicts, None) == set()
