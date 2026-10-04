"""Exact decimal rates at the actual consumer, without JVM or services."""
import copy
import json

import pytest
from vmp import criteria, pairing_input
from cdpm import model
from test_a06_k17_rank_rate_domain import document
from test_a06_local_package_closure import (
    isolated, unchanged_sources, native_package, consumer, APPLIED, PAYLOAD)


@pytest.mark.parametrize("rate", [1e-12, 5e-324, 0.10000000000001,
                                  0.29999999999999, 0.75])
def test_unrepresentable_rate_is_never_silently_rounded(rate):
    with pytest.raises(criteria.CriteriaError, match="деци"):
        criteria._deci(rate, "flight")


@pytest.mark.parametrize("rate,expected", [(0.1, 1), (0.3, 3), (0.7, 7),
    (1.2, 12), (7.1, 71), (12.3, 123), (123.4, 1234), (1, 10),
    (9007199254740993, 90071992547409930)])
def test_representable_rate_is_translated_exactly(rate, expected):
    assert criteria._deci(rate, "flight") == expected


@pytest.mark.parametrize("rate", [1e-12, 0.10000000000001])
@pytest.mark.parametrize("carrier", ["document", "answer"])
def test_native_package_rate_is_refused_before_builder_execution(
        tmp_path, monkeypatch, rate, carrier):
    path, _, _, _ = native_package(tmp_path, monkeypatch, carrier,
                                 kind="rank_normalized_cost", rank_rate=rate)
    received = consumer.load(path, run_gate=False)
    criterion = received["objective"]["pairing"]["criterion"]
    assert criterion["rates"]["flight"] == rate
    verdict = criteria.coverage(criterion)
    assert verdict["computable"] is False, verdict
    assert "деци" in verdict["reason"]
    with pytest.raises(pairing_input.PairingInputError, match="деци"):
        pairing_input.builder_args(copy.deepcopy(APPLIED), copy.deepcopy(PAYLOAD),
                                   budget_s=60, criterion=criterion)


@pytest.mark.parametrize("carrier", ["document", "answer"])
@pytest.mark.parametrize("rate", [9007199254740993, 10000000000000001])
def test_model_preserves_large_integer_rate(carrier, rate, monkeypatch):
    from cdpm import catalog_bridge as cb
    from test_a06_k15_direction_tokens import CATALOG
    monkeypatch.setenv(cb.ENV_CATALOG, str(CATALOG))
    doc, _ = document(rate, carrier)
    derived = model.derive_pairing_criterion(doc)
    assert derived["rates"]["flight"] == rate
    assert type(derived["rates"]["flight"]) is int


def test_different_rank_rates_do_not_collapse_through_float():
    doc, _ = document(9007199254740992, "answer")
    answer = doc["questions"][0]["answer"]
    raw = json.loads(answer["raw"])
    raw["rates"]["FO"]["flight"] = 9007199254740993
    answer["raw"] = json.dumps(raw)
    assert model.derive_pairing_criterion(doc) is None


@pytest.mark.parametrize("carrier", ["document", "answer"])
def test_large_integer_survives_native_shared_projection(tmp_path, monkeypatch, carrier):
    rate = 9007199254740993
    path, _, _, _ = native_package(tmp_path, monkeypatch, carrier,
                                 kind="rank_normalized_cost", rank_rate=rate)
    received = consumer.load(path, run_gate=False)
    criterion = received["objective"]["pairing"]["criterion"]
    assert criterion["rates"]["flight"] == rate
    args, _ = pairing_input.builder_args(copy.deepcopy(APPLIED), copy.deepcopy(PAYLOAD),
                                         budget_s=60, criterion=criterion)
    assert args[args.index("--cost-rank-deci-work") + 1] == str(rate * 10 + 25)
