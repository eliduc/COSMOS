"""Свидетельство ПОТРЕБИТЕЛЯ о применённых правилах (пятый аудит, B).

Отпечаток конфигурации доказывает, что значения решателю ПЕРЕДАЛИ. Он не
доказывает, что тот их ПРИМЕНИЛ: параметр, который никто не читает, даёт
такой же отпечаток, как работающий. Здесь проверяется вторая половина —
что сверка отличает три РАЗНЫХ исхода и что гейт видит каждый из них.
"""
from __future__ import annotations

import pytest

from _gerad_stage2_roster import (check_consumer_attestation,
                                  check_resource_attestation,
                                  gate_result_status)

EFFECTIVE = {"min_sit_min": 30, "max_landings_per_duty": 5,
             "max_duty_span_min": 840, "min_rest_min": 600,
             "max_monthly_block_min": 5100, "max_pairing_span_min": 5760,
             "comp_no_two_reserves": 0, "comp_min_pair_seniority_weight": 0,
             "comp_language_mix": 0}


def _att(applied, declared=("min_rest_min", "max_monthly_block_min"),
         **extra):
    return {"schema": "cosmos-applied-rules/v1", "profile": "GERAD2P",
            "declared": list(declared), "applied": dict(applied), **extra}


def test_declared_and_read_with_the_right_values_is_clean():
    out = check_consumer_attestation(
        _att({"min_rest_min": 600, "max_monthly_block_min": 5100}),
        EFFECTIVE)
    assert out["ok"] is True
    assert out["not_applied"] == [] and out["mismatched"] == []


def test_declared_but_never_read_is_the_audit_finding_itself():
    """«Передали, но не применили» — ровно то, что отпечаток не ловит."""
    out = check_consumer_attestation(
        _att({"min_rest_min": 600}), EFFECTIVE)
    assert out["ok"] is False
    assert out["not_applied"] == ["max_monthly_block_min"]


def test_read_a_different_value_is_worse_than_not_reading():
    """Правило работает, но не то, которое утвердил заказчик."""
    out = check_consumer_attestation(
        _att({"min_rest_min": 480, "max_monthly_block_min": 5100}),
        EFFECTIVE)
    assert out["ok"] is False
    assert out["mismatched"] == [
        {"key": "min_rest_min", "applied": 480, "effective": 600}]


def test_reading_more_than_declared_is_informational_not_fatal():
    """Контракт профиля отстал от кода — это повод поправить список, а не
    отменять прогон."""
    out = check_consumer_attestation(
        _att({"min_rest_min": 600, "max_monthly_block_min": 5100,
              "min_sit_min": 30}), EFFECTIVE)
    assert out["ok"] is True
    assert out["extra"] == ["min_sit_min"]


def test_missing_attestation_is_a_refusal_not_a_pass():
    """Молчание решателя не имеет права означать «всё применено»."""
    out = check_consumer_attestation({}, EFFECTIVE)
    assert out["ok"] is False and out["error"]
    out = check_consumer_attestation({"schema": "чужая/v9"}, EFFECTIVE)
    assert out["ok"] is False


def test_wrong_effective_value_is_caught_even_when_defaults_match():
    """Набор со своими правилами: решатель применил встроенное значение
    вместо значения набора — самый тихий вид расхождения."""
    own = dict(EFFECTIVE, min_rest_min=720)
    out = check_consumer_attestation(
        _att({"min_rest_min": 600, "max_monthly_block_min": 5100}), own)
    assert out["ok"] is False
    assert out["mismatched"][0]["key"] == "min_rest_min"


# --- ресурсные правила яруса 2в -------------------------------------------

def _res(weekly=None, evaluations=0):
    return {"schema": "cosmos-applied-rules/v1", "profile": "GERAD2P",
            "declared": [], "applied": {},
            "resourceRules": {"weeklyRest": weekly,
                              "weeklyRestEvaluations": evaluations}}


def test_resource_rule_parsed_identically_is_clean():
    sent = {"weeklyRest": {"requiredMin": 2880, "periodMin": 1440}}
    got = {"requiredMin": 2880, "periodMin": 1440, "tzOffsetMin": 0,
           "horizonFromEpochMin": 0, "horizonToEpochMin": 100}
    out = check_resource_attestation(_res(got, evaluations=17), sent)
    assert out["ok"] is True and out["evaluations"] == 17


def test_resource_rule_parsed_differently_is_caught():
    """Файл доставлен и отпечаток сошёлся, а число внутри решателя другое
    — отпечаток такого не видит принципиально."""
    sent = {"weeklyRest": {"requiredMin": 2880}}
    got = {"requiredMin": 1440}
    out = check_resource_attestation(_res(got), sent)
    assert out["ok"] is False
    assert out["mismatched"] == [{"key": "weeklyRest.requiredMin",
                                  "applied": 1440, "sent": 2880}]


def test_resource_rule_declared_but_solver_has_no_consumer():
    sent = {"weeklyRest": {"requiredMin": 2880}, "landings": {"max": 5}}
    out = check_resource_attestation(
        _res({"requiredMin": 2880}), sent)
    assert out["ok"] is False
    assert out["not_applied"] == ["landings"]


def test_nothing_declared_expects_nothing():
    out = check_resource_attestation(_res(None), None)
    assert out["ok"] is True and out["declared"] == []


# --- связь с гейтом --------------------------------------------------------

@pytest.mark.parametrize("not_applied", [
    ["ярус1:max_monthly_block_min"],
    ["ярус1:min_rest_min(применено 480, действует 600)"],
    ["ресурс:weeklyRest.requiredMin(применено 1440, передано 2880)"],
])
def test_gate_refuses_a_run_whose_consumer_did_not_confirm(not_applied):
    """«Ноль жёстких нарушений» под чужими значениями — утверждение о
    другой задаче ровно так же, как под отсутствующим правилом."""
    cov = {"missing_legs": [], "duplicates": 0, "unknown_flights": 0}
    assert gate_result_status("0hard/0soft", 0, cov, set(),
                              trajectory_violations=0,
                              composition_violations=0,
                              rules_not_applied=not_applied) \
        == "INFEASIBLE_INCUMBENT"


def test_gate_still_says_ok_when_the_consumer_confirmed_everything():
    cov = {"missing_legs": [], "duplicates": 0, "unknown_flights": 0}
    assert gate_result_status("0hard/0soft", 0, cov, set(),
                              trajectory_violations=0,
                              composition_violations=0,
                              rules_not_applied=[]) == "OK"


# --- ответ решателя как НЕДОВЕРЕННЫЙ вход ---------------------------------

@pytest.mark.parametrize("att", [
    {"schema": "cosmos-applied-rules/v1", "declared": ["min_rest_min"],
     "applied": {"min_rest_min": "шестьсот"}},
    {"schema": "cosmos-applied-rules/v1", "declared": ["min_rest_min"],
     "applied": {"min_rest_min": None}},
    {"schema": "cosmos-applied-rules/v1", "declared": "не список",
     "applied": "не словарь"},
    {"schema": "cosmos-applied-rules/v1", "declared": [None],
     "applied": {"min_rest_min": 600}},
])
def test_malformed_solver_answer_never_crashes_the_run(att):
    """Сверка стоит ПОСЛЕ многочасового решения и ПЕРЕД публикацией
    артефактов: исключение здесь потеряло бы весь прогон. Нечитаемое
    значение — расхождение, а не падение."""
    out = check_consumer_attestation(att, EFFECTIVE)
    assert isinstance(out, dict) and "ok" in out


def test_unreadable_value_is_treated_as_a_mismatch_not_a_pass():
    out = check_consumer_attestation(
        {"schema": "cosmos-applied-rules/v1", "declared": ["min_rest_min"],
         "applied": {"min_rest_min": "шестьсот"}}, EFFECTIVE)
    assert out["ok"] is False
    assert out["mismatched"][0]["key"] == "min_rest_min"


@pytest.mark.parametrize("att", [
    {"resourceRules": "не словарь"},
    {"resourceRules": {"weeklyRest": {"requiredMin": None},
                       "weeklyRestEvaluations": "много"}},
    {"resourceRules": {"weeklyRest": "не словарь"}},
])
def test_malformed_resource_answer_never_crashes(att):
    out = check_resource_attestation(att, {"weeklyRest": {"requiredMin": 2880}})
    assert isinstance(out, dict) and out["ok"] is False


def test_malformed_sent_document_does_not_crash():
    out = check_resource_attestation(
        {"resourceRules": {"weeklyRest": {"requiredMin": 2880},
                           "weeklyRestEvaluations": 3}},
        {"weeklyRest": "не словарь"})
    assert isinstance(out, dict)
