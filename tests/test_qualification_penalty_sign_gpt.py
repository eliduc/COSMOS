"""Offline signed-weight contract tests, not native execution evidence.

NativeExecutionPlan supplies nonnegative SOFT override magnitudes. The
production providers use penalize(), so NativeExecutionSupport serializes
the ScoreDirector's *negative* constraint weight. Fixtures below model that
wire contract; they do not run Kotlin or manufacture qualification receipts.
"""
from __future__ import annotations

import copy

import pytest

from vmp import qualification_run as qr
from test_qualification_run_gpt import POSITIVE, ProtocolFixture, offline_only


METRIC = "Credit hours spread"
PENALTY = "Credit hours spread over threshold"
CALENDAR = "Calendar window layover penalty"
HARD = "Employee unavailable"
DISABLED = "Balance work time"
CONTEXT_SHA, SUBJECT_SHA, FINGERPRINT = "a" * 64, "b" * 64, "c" * 64


def row(*, hard=0, soft=0, matches=1, weight="0hard/-1soft"):
    return {"score": f"{hard}hard/{soft}soft", "hard": hard, "soft": soft,
            "matches": matches, "weight": weight}


def wrapped(rows):
    hard = sum(value["hard"] for value in rows.values())
    soft = sum(value["soft"] for value in rows.values())
    return {"execution_context_sha256": CONTEXT_SHA, "score_subject_sha256": SUBJECT_SHA,
            "breakdown": {"score": f"{hard}hard/{soft}soft", "hard": hard, "soft": soft,
                          "packageFingerprint": FINGERPRINT, "generated": [], "constraints": rows}}


def measure(rows, *, target=METRIC, effective=None):
    return qr._breakdown(wrapped(rows), context_sha=CONTEXT_SHA, subject_sha=SUBJECT_SHA,
                         fingerprint=FINGERPRINT, registered=list(rows),
                         expected_constraint=target,
                         effective_weights={METRIC: 1} if effective is None else effective)


@pytest.mark.parametrize("name", [METRIC, PENALTY, CALENDAR])
@pytest.mark.parametrize("magnitude", [1, 3])
@pytest.mark.parametrize("amount", [0, 7])
def test_penalize_weight_is_exact_negative_magnitude(name, magnitude, amount):
    got = measure({name: row(soft=-amount * magnitude, weight=f"0hard/{-magnitude}soft")},
                  target=name, effective={name: magnitude})
    assert got == {"constraint": name, "matches": 1, "amount": amount}


def test_metric_and_threshold_are_distinct_signed_contributions():
    rows = {METRIC: row(soft=-7), PENALTY: row(soft=-2),
            HARD: row(hard=-10, weight="-10hard/0soft"),
            DISABLED: row(matches=0, weight="0hard/0soft")}
    before = copy.deepcopy(rows)
    weights = {METRIC: 1, PENALTY: 1, DISABLED: 0}
    assert measure(rows, effective=weights)["amount"] == 7
    assert measure(rows, target=PENALTY, effective=weights)["amount"] == 2
    assert rows == before


@pytest.mark.parametrize("weight", ["0hard/1soft", "0hard/2soft", "0hard/-2soft",
                                    "0hard/0soft", "-1hard/0soft", "1hard/-1soft"])
def test_wrong_sign_magnitude_level_or_disabled_active_weight_is_refused(weight):
    with pytest.raises(qr.QualificationError, match="weight differs"):
        measure({METRIC: row(soft=-7, weight=weight)})


def test_reward_contribution_with_negative_weight_is_not_a_penalty():
    with pytest.raises(qr.QualificationError, match="non-penalty contribution"):
        measure({METRIC: row(soft=7)})


def test_changed_score_text_is_not_accepted_by_weight_correction():
    value = row(soft=-7)
    value["score"] = "0hard/-6soft"
    with pytest.raises(qr.QualificationError, match="score text differs"):
        measure({METRIC: value})


def test_correct_signed_weight_does_not_round_non_integral_measure():
    with pytest.raises(qr.QualificationError, match="non-integral"):
        measure({METRIC: row(soft=-7, weight="0hard/-2soft")}, effective={METRIC: 2})


@pytest.mark.parametrize("amount", [0, 1, 3])
def test_native_negative_hard_weight_and_disabled_soft_are_unchanged(amount):
    rows = {HARD: row(hard=-10 * amount, weight="-10hard/0soft"),
            DISABLED: row(matches=0, weight="0hard/0soft")}
    assert measure(rows, target=HARD, effective={DISABLED: 0}) == {
        "constraint": HARD, "matches": 1, "amount": amount}


@pytest.mark.parametrize("weight", ["0hard/-1soft", "0hard/1soft"])
def test_disabled_soft_cannot_acquire_either_signed_weight(weight):
    with pytest.raises(qr.QualificationError, match="weight differs"):
        measure({HARD: row(hard=-10, weight="-10hard/0soft"), DISABLED: row(weight=weight)},
                target=HARD, effective={DISABLED: 0})


def test_zero_weight_is_not_evidence_of_an_executed_target():
    with pytest.raises(qr.QualificationError, match="target constraint disabled"):
        measure({METRIC: row(matches=0, weight="0hard/0soft")}, effective={METRIC: 0})


@pytest.mark.parametrize("field", ["applied_weights", "effective_weights"])
@pytest.mark.parametrize("value", [-1, True, 1.0])
def test_signed_wire_does_not_authorize_negative_or_noninteger_context(tmp_path, monkeypatch, field, value):
    bundle = ProtocolFixture(tmp_path, monkeypatch)
    context = bundle.contents[POSITIVE]["execution_context"]
    native = next(item for item in bundle.native.values() if item["manifest"]["task"]["id"] == POSITIVE)
    task = next(item for item in bundle.catalog.rows[0]["stand_tasks"] if item["id"] == POSITIVE)
    case = next(item for item in bundle.request["cases"] if item["task"] == POSITIVE)
    fingerprint = bundle.row()["input_fingerprint"]
    qr._context(context, native, task["world"], fingerprint, case)  # healthy magnitude context
    context[field][CALENDAR] = value
    with pytest.raises(qr.QualificationError, match="invalid actual soft weights"):
        qr._context(context, native, task["world"], fingerprint, case)
