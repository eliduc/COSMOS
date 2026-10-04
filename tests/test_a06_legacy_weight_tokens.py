"""Legacy A06 weights must not silently truncate a number from source text.

All inputs are synthetic model dictionaries. The real parser, weighted-rule
check and legacy objective writer are exercised, without replacing verdicts.
No package build, translator, network, service, JVM or solver is used.
"""
import copy
import json

import pytest

from cdpm import catalog_bridge, export_solver as es, model
from test_cdpm_export_solver import _doc


BAD = [
    "вес 1.5", "вес 1,5", "weight 1e3", "weight 1E+3",
    "weight 10/2", "вес 10:30", "weight 10kg", "вес 10_000",
    "вес 1 000", "weight -10", "вес -10", "штраф 1..3",
    "вес 1.5; другой вес 7",
    "weight 10 / 2", "вес 1 – 3", "вес 1 — 3", "вес 10%",
    "вес 10 %", "вес 1\u00a0000",
    pytest.param("вес 10*2", id="arithmetic-ascii-multiply"),
    pytest.param("weight 10 * 2", id="arithmetic-spaced-multiply"),
    pytest.param("вес 10×2", id="arithmetic-times-sign"),
    pytest.param("вес 10÷2", id="arithmetic-division-sign"),
    pytest.param("вес 10⁄2", id="arithmetic-fraction-slash"),
    pytest.param("вес 10∕2", id="arithmetic-division-slash"),
    pytest.param("weight 10^2", id="arithmetic-power"),
    pytest.param("weight 10∗2", id="arithmetic-unicode-star"),
    pytest.param("weight 10⋅2", id="arithmetic-unicode-dot"),
    pytest.param("weight 10±2", id="arithmetic-plus-minus"),
    pytest.param("вес 10·2", id="punctuation-middle-dot"),
    pytest.param("вес 10 · 2", id="punctuation-spaced-middle-dot"),
    pytest.param("вес 10‰", id="punctuation-per-mille"),
    pytest.param("вес 10 ‰", id="punctuation-spaced-per-mille"),
    pytest.param("вес 10٪", id="punctuation-arabic-percent"),
    pytest.param("вес 10 ٪", id="punctuation-spaced-arabic-percent"),
    pytest.param("вес 10‱", id="punctuation-per-ten-thousand"),
    pytest.param("вес 10\u0338", id="combining-numeric-mark"),
    pytest.param("вес 10\u200b·2", id="invisible-numeric-continuation"),
    pytest.param("вес 10°", id="degree-not-scalar"),
    pytest.param("вес 10 x 2", id="latin-letter-multiply"),
    pytest.param("вес 10 х 2", id="cyrillic-letter-multiply"),
    pytest.param("вес 10…20", id="ellipsis-range"),
    pytest.param("вес 10 ½", id="spaced-fraction"),
    pytest.param("вес 10до20", id="attached-word-range"),
    pytest.param("вес 10 до 20", id="word-range"),
    pytest.param("вес 10 до́ 20", id="accented-word-range"),
]
GOOD = [
    ("weight 0", 0), ("вес 17", 17), ("вес — 17", 17),
    ("вес - 17", 17), ("вес: 17.", 17),
    ("вес 17, затем штрафы", 17),
    ("штраф величиной 10", 10), ("штраф W_night = 10", 10),
    ("penalty factor 7", 7), ("штрафом в размере 7 баллов", 7),
    ("штраф 1 за каждые полные 0:30 стыковки", 1),
    ("вес 10; затем штрафы", 10), ("(вес 10).", 10),
    ("вес 10 — фиксированный штраф", 10),
    ("вес 10 (постоянная величина)", 10),
    ("вес 10!", 10), ("вес 10\nза каждое нарушение", 10),
    ("вес 10 ба́ллов", 10),
]


def _rule(statement):
    return {
        "id": "S-W", "kind": "SOFT", "zone": "BOTH",
        "title": "Synthetic legacy weight", "statement": statement,
        "sources": [{"sid": "S1", "anchor": "p.3"}],
    }


@pytest.mark.parametrize("statement", BAD)
def test_incomplete_number_does_not_become_a_confirmed_weight(statement):
    rule = _rule(statement)
    before = copy.deepcopy(rule)
    assert es.soft_weight(rule) is None
    assert model._weighted_rules([], [rule]) == set()
    assert rule == before


@pytest.mark.parametrize("statement", BAD)
def test_legacy_objective_keeps_unconfirmed_weight_null(statement):
    # Explicit legacy branch; no activation or fallback for a modern spec.
    assert "directions" not in catalog_bridge.catalog().dictionaries()
    doc = _doc(blocked=False)
    doc["rules"].append(_rule(statement))
    before = copy.deepcopy(doc)
    payload = json.loads(es.build_objective_json(doc, spec_version="1.18"))
    for task in ("pairing", "rostering"):
        row = next(c for c in payload[task]["components"] if c["rule_id"] == "S-W")
        assert row["weight"] is None
        assert row["statement"] == statement
        assert row["sources"] == doc["rules"][-1]["sources"]
    assert doc == before


@pytest.mark.parametrize(("statement", "expected"), GOOD)
def test_existing_integer_spelling_and_weight_source_are_preserved(statement, expected):
    rule = _rule(statement)
    assert es.soft_weight(rule) == expected
    assert model._weighted_rules([], [rule]) == {"S-W"}
    doc = _doc(blocked=False)
    doc["rules"].append(rule)
    payload = json.loads(es.build_objective_json(doc, spec_version="1.18"))
    assert payload["pairing"]["components"][0]["weight"] == expected
    assert payload["rostering"]["components"][0]["weight"] == expected


def test_explicit_machine_weight_remains_authoritative():
    for statement in ("вес 1.5", "вес 10·2", "вес 10‰", "вес 10٪"):
        rule = {**_rule(statement), "weight": 9}
        assert es.soft_weight(rule) == 9
        assert model._weighted_rules([], [rule]) == {"S-W"}


@pytest.mark.parametrize("statement", ["вес 1.5", "weight -10", "вес 1 000"])
def test_model_validator_requires_weight_evidence_for_legacy_soft_rule(statement):
    doc = _doc(blocked=False)
    doc["rules"].append(_rule(statement))
    problems = model.validate_model(doc)
    assert any("S-W" in p and "SOFT" in p for p in problems), problems
    doc["rules"][-1]["weight"] = 9
    assert model.validate_model(doc) == []


def test_cosmos_predeclared_59_spellings_with_explicit_oracle_corrections():
    # Literal census from proba-a06-znaki-COSMOS.py; no external file import.
    # COSMOS's 17 Sep 19:00:18+02:00 message corrects two older expectations:
    # +10 is safely unconfirmed, and "10 до 20" is a range, not scalar 10.
    # The author's original probe is retained unchanged.
    from vmp import objective_projection as op
    cases = [
        ("вес 10", 10), ("вес: 10", 10), ("вес - 10", 10), ("вес 10.", 10),
        ("вес 10, штраф", 10), ("вес 10 за сутки", 10), ("вес 10; далее", 10),
        ("вес 10*2", None), ("вес 10 * 2", None), ("вес 10/2", None),
        ("вес 10^2", None), ("вес 10**2", None), ("вес 10+2", None),
        ("вес 10-2", None), ("вес 10 - 2", None), ("вес 10%", None),
        ("вес 10 %", None), ("вес 10:2", None), ("вес 10x2", None),
        ("вес 10 x 2", None), ("вес 10е3", None), ("вес 10e3", None),
        ("вес 10×2", None), ("вес 10 × 2", None), ("вес 10÷2", None),
        ("вес 10−2", None), ("вес 10⋅2", None), ("вес 10·2", None),
        ("вес 10 · 2", None), ("вес 10∙2", None), ("вес 10∗2", None),
        ("вес 10⁄2", None), ("вес 10х2", None), ("вес 10 х 2", None),
        ("вес 10²", None), ("вес 10⁵", None), ("вес 10‰", None),
        ("вес 10‱", None), ("вес 10⁄₂", None), ("вес 10√2", None),
        ("вес 10∘2", None), ("вес 10°", None), ("вес 10,5", None),
        ("вес 10.5", None), ("вес 10 000", None), ("вес 10–20", None),
        ("вес 10—20", None), ("вес 10 – 20", None), ("вес 10..20", None),
        ("вес 10…20", None), ("вес 10 до 20", None), ("вес 1e3", None),
        ("вес 1E3", None), ("вес 10½", None), ("вес 10 ½", None),
        ("вес 10¼", None), ("вес -10", None), ("вес −10", None), ("вес +10", None),
    ]
    assert len(cases) == 59
    mismatches = []
    for statement, expected in cases:
        for parser in (es.soft_weight, op.soft_weight):
            actual = parser({"statement": statement})
            if actual != expected:
                mismatches.append((parser.__module__, statement, expected, actual))
    assert mismatches == []
