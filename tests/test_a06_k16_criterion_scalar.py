"""A06/04: prose rates must be complete positive integers, not operands.

Actual draft.3 reader, no translator/solver/stand or synthetic product verdict.
"""
import copy

import pytest
import template_catalog as tc
from cdpm import catalog_bridge as cb, model
from test_a06_k15_direction_tokens import CATALOG, FINGERPRINT, _hashes
from test_cdpm_task_package import _ready_doc


@pytest.fixture(scope="module", autouse=True)
def unchanged():
    before = _hashes()
    assert tc.current(CATALOG, validated=True).fingerprint() == FINGERPRINT
    yield
    assert _hashes() == before


@pytest.fixture(autouse=True)
def catalogue(monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(CATALOG))
    cb.forget()
    yield
    cb.forget()


BAD = ["100*2", "100 · 2", "100×2", "100 + 2", "100−2", "100/2",
       "100^2", "100**2", "100.5", "100,5", "100e2", "100_000",
       "100 000", "100–200", "100 до 200", "100%", "100‰", "100x2",
       "100 x 2", "100\u200b2", "100\u03012", "-100", "−100", "+100",
       "0", "2*100", "2 × 100", "1.5 * 100", "100/0", "(100)"]


def prose(token, carrier):
    doc = _ready_doc()
    row = copy.deepcopy(doc["objectives"][0])
    row["statement"] = ("Минимум человеко-дней командировок; "
                        "цена рабочего дня — " + token + " условных единиц")
    doc["objectives"] = [row] if carrier == "objective" else []
    doc["rules"] = [] if carrier == "objective" else [dict(row, kind="SOFT")]
    return doc


@pytest.mark.parametrize("carrier", ["objective", "rule"])
@pytest.mark.parametrize("token", BAD)
def test_unknown_rate_expression_is_not_first_operand(token, carrier):
    doc = prose(token, carrier)
    before = copy.deepcopy(doc)
    assert model.derive_pairing_criterion(doc) is None
    assert doc == before


@pytest.mark.parametrize("token,rate", [("100", 100), ("123456", 123456),
                                      ("1", 1), ("00100", 100)])
@pytest.mark.parametrize("carrier", ["objective", "rule"])
def test_complete_positive_integer_keeps_full_quote(token, rate, carrier):
    doc = prose(token, carrier)
    criterion = model.derive_pairing_criterion(doc)
    assert criterion["rate_per_duty"] == rate
    assert criterion["direction"] == "minimize"
    assert criterion["source_rule"] == "O-1"
    assert criterion["as_written"] == doc["objectives" if carrier == "objective" else "rules"][0]["statement"]


def test_machine_rate_remains_authoritative_over_unparsed_prose():
    doc = prose("100 * 2", "objective")
    doc["objectives"][0]["criterion"] = {
        "kind": "min_person_days", "rate_per_duty": 200,
        "version": "v1", "effective_from": "2030-01-01"}
    assert model.derive_pairing_criterion(doc)["rate_per_duty"] == 200


@pytest.mark.parametrize("phrase,rate", [
    ("цена рабочего дня = 100 условных единиц", 100),
    ("цена рабочего дня - 100 условных единиц", 100),
    ("100 условных единиц", 100),
    ("цена рабочего дня 100 условных единиц", 100),
    ("2 * 100 условных единиц", None),
    ("2 × 100 условных единиц", None),
    ("-100 условных единиц", None),
    ("100 до 200 условных единиц", None),
    ("цена рабочего дня — - 100 условных единиц", None),
    ("цена рабочего дня — 100.5; затем 200 условных единиц", None),
])
def test_separator_and_undelimited_neighbors(phrase, rate):
    doc = prose("100", "objective")
    doc["objectives"][0]["statement"] = "Минимум человеко-дней; " + phrase
    criterion = model.derive_pairing_criterion(doc)
    assert (criterion["rate_per_duty"] if criterion else None) == rate
