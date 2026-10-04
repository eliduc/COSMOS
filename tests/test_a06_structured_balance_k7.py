"""K-7: structured landing values, not quotations, consume clause quantities.

Pure model/balance/repair tests with an explicit draft.2 catalogue fixture.
No bundle, extractor, service, external model call or solver is run. Repair
receives deterministic second-reading hints; the real landing and quotation
validators remain active. A refused fixture is a failure, not a skip.
"""

import copy
from pathlib import Path

import pytest

from cdpm import balance as bal
from cdpm import family_params as fp
from cdpm import model as m


@pytest.fixture(autouse=True)
def explicit_catalog(monkeypatch):
    """Select the test's actual contract after suite environment isolation."""
    from cdpm import catalog_bridge
    folder = Path(__file__).resolve().parents[1] / "catalog-integration" / "0.7.0-draft.2"
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(folder))
    catalog_bridge.forget()
    yield
    catalog_bridge.forget()


QUOTE = "Ежедневно резерв: PLY ≥ 2 экипажа, SVG ≥ 1, GRT ≥ 1"
VALUES = {"GRT": 1, "PLY": 2, "SVG": 1}
SID = "S-K7"
ANCHOR = "reserve-row"


def _entries(text=QUOTE, *, sid=SID, anchor=ANCHOR):
    return [{"sid": sid, "file": "k7-offline.txt", "sha256": "7" * 64,
             "clauses": [{"anchor": anchor, "text": text}]}]


def _doc(rule=None):
    return {"rules": [rule] if rule is not None else [],
            "objectives": [], "questions": []}


def _typed_rule(*, family="reserve_floors", name="reserve_crews",
                value=None, quote=QUOTE):
    value = VALUES if value is None else value
    candidate = {
        "id": "R-K7", "kind": "HARD", "zone": "BOTH", "statement": quote,
        "sources": [{"sid": SID, "anchor": ANCHOR}],
        "template_fit": {"category": "A", "family": family,
                         "params": {name: {"value": copy.deepcopy(value),
                                           "quote": quote}}},
    }
    before = copy.deepcopy(candidate)
    fit, _confidence, note = m.accept_fit_candidate(
        candidate, quote, new_output=True)
    assert candidate == before
    assert fit["schema_version"] == "landing/2"
    assert fit["category"] == "A", (fit, note)
    rule = {**candidate, "template_fit": fit}
    document = _doc(rule)
    assert m.validate_template_fit(document) == []
    assert m.validate_fit_quotes(document, {(SID, ANCHOR): quote}) == []
    cell = fit["params"][name]
    assert cell["source"] == {"kind": "rule", "rule_id": "R-K7"}
    assert cell["quote"] == quote
    return rule


def _report_unchanged(entries, document):
    before_entries, before_doc = copy.deepcopy(entries), copy.deepcopy(document)
    result = bal.report(entries, document)
    assert entries == before_entries
    assert document == before_doc  # No spurious question or input rewrite.
    return result


def _assert_all_slots(result, expected):
    assert result["values"] == expected
    assert result["consumed"] == expected
    assert result["unconsumed"] == 0
    assert result["findings"] == []
    assert result["held_by_text"] == []
    assert result["by_fate"] == {"слот": expected}


@pytest.mark.parametrize("value", [VALUES, {"SVG": 1, "PLY": 2, "GRT": 1}])
def test_reserve_base_map_consumes_machine_codes_and_counts(value):
    assert fp._param_spec("reserve_floors", "reserve_crews")["qualifier"] == "base_code"
    assert fp.base_map_quote_problems(fp.base_map(value), QUOTE) == []
    rule = _typed_rule(value=value)
    cell = rule["template_fit"]["params"]["reserve_crews"]
    assert cell["unit"] == "count" and cell["value"] == value
    # Three base codes, the explicit count, and two elided counts: all slots.
    _assert_all_slots(_report_unchanged(_entries(), _doc(rule)), 6)


@pytest.mark.parametrize("bad_value", [
    {}, {"PLY": True}, {"PLY": {"count": 2}}, [2, 1, 1],
    {"PLY": -2}, {"PLY": 2.5}, {"PLY": "2"}, {"": 2},
], ids=["empty", "bool", "nested-map", "numeric-list", "negative",
        "fractional", "string-number", "empty-base"])
def test_malformed_structures_do_not_hide_quantities_or_crash(bad_value):
    rule = _typed_rule()
    rule["template_fit"]["params"]["reserve_crews"]["value"] = bad_value
    document = _doc(rule)
    assert m.validate_template_fit(document)
    result = _report_unchanged(_entries(), document)
    assert result["findings"], result
    assert result["held_by_text"] == []
    assert any(f["class"] in ("count", "number") for f in result["findings"])


@pytest.mark.parametrize("bad_value", [
    {"GRT": 1, "PLY": 3, "SVG": 1},
    {"GRT": 1, "PLY": 1, "SVG": 2},
], ids=["changed-count", "same-numbers-wrong-base-binding"])
def test_wrong_base_values_stay_visible_despite_preserved_quote(bad_value):
    rule = _typed_rule()
    cell = rule["template_fit"]["params"]["reserve_crews"]
    cell["value"] = bad_value
    assert cell["quote"] == QUOTE
    assert fp.base_map_quote_problems(fp.base_map(bad_value), QUOTE)
    assert m.validate_template_fit(_doc(rule))
    result = _report_unchanged(_entries(), _doc(rule))
    assert any(f["class"] in ("count", "number") for f in result["findings"]), result
    assert result["held_by_text"] == []


def test_map_quotation_from_another_clause_is_not_machine_consumption():
    rule = _typed_rule()
    cell = rule["template_fit"]["params"]["reserve_crews"]
    # The same valid pairs occur in a different source quotation.  Checking
    # base_map_quote_problems alone would miss the missing source provenance.
    cell["quote"] = "По другому нормативу: PLY ≥ 2, SVG ≥ 1, GRT ≥ 1"
    assert fp.base_map_quote_problems(fp.base_map(cell["value"]), cell["quote"]) == []
    assert m.validate_fit_quotes(_doc(rule), {(SID, ANCHOR): QUOTE})
    result = _report_unchanged(_entries(), _doc(rule))
    assert result["findings"], result
    assert result["held_by_text"] == []


@pytest.mark.parametrize("damage", ["undeclared-name", "foreign-unit"])
def test_structures_require_their_own_declared_name_and_unit(damage):
    rule = _typed_rule()
    params = rule["template_fit"]["params"]
    if damage == "undeclared-name":
        params["k7_undeclared_reserve"] = params.pop("reserve_crews")
    else:
        params["reserve_crews"]["unit"] = "duration_minutes"
    assert m.validate_template_fit(_doc(rule))
    result = _report_unchanged(_entries(), _doc(rule))
    assert any(f["class"] in ("count", "number") for f in result["findings"]), result
    assert result["held_by_text"] == []


def test_healthy_structure_does_not_consume_an_extra_quantity():
    rule = _typed_rule()
    text = QUOTE + ". Дополнительно 7 пилотов."
    assert m.validate_fit_quotes(_doc(rule), {(SID, ANCHOR): text}) == []
    result = _report_unchanged(_entries(text), _doc(rule))
    assert result["findings"] == [{
        "sid": SID, "anchor": ANCHOR, "class": "count", "text": "7 пилотов",
        "value": 7.0, "rows": ["R-K7"], "structure_verdict": None,
        "note": "не потреблена ни одним слотом",
    }]
    assert result["held_by_text"] == []
    assert result["by_fate"] == {"слот": 6}


def _code_list_case():
    # Read the selected catalogue through the existing product contract.  Do
    # not invent a dictionary value for a numeric family or fake a catalogue.
    choices = sorted(
        (family, name)
        for family, names in m.template_families().items()
        for name in names
        if fp._param_spec(family, name).get("unit") == "code_list"
    )
    assert choices, "selected catalogue must expose a real code_list parameter"
    family, name = choices[0]
    quote = f"{family}: PLY, SVG"
    rule = _typed_rule(family=family, name=name, value=["PLY", "SVG"], quote=quote)
    assert rule["template_fit"]["params"][name]["unit"] == "code_list"
    return rule, name, quote


def test_declared_code_list_consumes_codes_without_quote_fallback():
    rule, _name, quote = _code_list_case()
    result = _report_unchanged(_entries(quote), _doc(rule))
    assert result["findings"] == []
    assert result["held_by_text"] == []
    assert result["by_class"]["code"] == 2
    assert result["by_fate"]["слот"] >= 2
    assert result["values"] == result["consumed"]


def test_code_list_subset_cannot_consume_absent_code_from_quote():
    rule, name, quote = _code_list_case()
    rule["template_fit"]["params"][name]["value"] = ["PLY"]
    result = _report_unchanged(_entries(quote), _doc(rule))
    assert any(f["class"] == "code" and f["value"] == "SVG"
               for f in result["findings"]), result
    assert result["held_by_text"] == []


@pytest.mark.parametrize("value", [["PLY", {"code": "SVG"}], {"PLY": 1, "SVG": 1}])
def test_code_list_rejects_nested_or_mapping_shape_without_flattening(value):
    rule, name, quote = _code_list_case()
    rule["template_fit"]["params"][name]["value"] = value
    assert m.validate_template_fit(_doc(rule))
    result = _report_unchanged(_entries(quote), _doc(rule))
    assert any(f["class"] == "code" for f in result["findings"]), result
    assert result["held_by_text"] == []


def test_exact_repeated_entry_does_not_duplicate_the_balance():
    entries = _entries("Нужно 7 пилотов.")
    single = _report_unchanged(entries, _doc())
    assert single["unconsumed"] == 1
    repeated = _report_unchanged(entries + copy.deepcopy(entries), _doc())
    assert repeated == single


@pytest.mark.parametrize("field,replacement", [
    ("file", "another-k7-offline.txt"),
    ("sha256", "8" * 64),
], ids=["different-file-only", "different-sha256-only"])
def test_source_file_and_digest_are_independent_clause_identities(field, replacement):
    entries = _entries("Нужно 7 пилотов.")
    other = copy.deepcopy(entries[0])
    other[field] = replacement
    assert {key for key in other if other[key] != entries[0][key]} == {field}
    # Same source id, anchor, raw text and occurrence; only one transport
    # identity field changes. Neither distinct obligation may disappear.
    single = _report_unchanged(entries, _doc())
    result = _report_unchanged(entries + [other], _doc())
    assert single["clauses"] == single["unconsumed"] == 1
    assert result["clauses"] == result["values"] == result["unconsumed"] == 2
    assert result["consumed"] == 0
    assert result["findings"] == single["findings"] * 2
    assert result["held_by_text"] == []
    # Repeating each exact transport identity remains harmless.
    assert _report_unchanged(entries + [other] + copy.deepcopy(entries + [other]),
                             _doc()) == result


@pytest.mark.parametrize("other_sid,other_anchor", [(SID, "another-row"), ("S-OTHER", ANCHOR)])
def test_equal_text_at_distinct_source_anchors_is_not_deduplicated(other_sid, other_anchor):
    text = "Нужно 7 пилотов."
    entries = _entries(text) + _entries(text, sid=other_sid, anchor=other_anchor)
    result = _report_unchanged(entries, _doc())
    assert result["clauses"] == 2 and result["unconsumed"] == 2
    assert {(f["sid"], f["anchor"]) for f in result["findings"]} == {
        (SID, ANCHOR), (other_sid, other_anchor),
    }


def test_repeated_quantity_occurrences_inside_a_clause_are_not_deduplicated():
    text = "Нужно 7 пилотов. Дополнительно 7 пилотов."
    occurrences = [v for v in bal.values_of(text) if v["class"] == "count"]
    assert len(occurrences) == 2
    assert occurrences[0]["span"] != occurrences[1]["span"]
    result = _report_unchanged(_entries(text), _doc())
    assert result["clauses"] == 1
    assert result["values"] == result["unconsumed"] == 2
    assert [f["text"] for f in result["findings"]] == ["7 пилотов", "7 пилотов"]


def test_different_clause_text_at_same_anchor_is_not_an_exact_duplicate():
    entries = _entries("Нужно 7 пилотов.") + _entries("Нужно 8 пилотов.")
    result = _report_unchanged(entries, _doc())
    assert result["clauses"] == 2
    assert result["unconsumed"] == 2
    assert [f["value"] for f in result["findings"]] == [7.0, 8.0]


def test_repair_recognizes_existing_valid_map_without_model_calls():
    from cdpm import repair
    rule = _typed_rule()
    document = _doc(rule)
    finding = {"sid": SID, "anchor": ANCHOR, "class": "count",
               "value": 2.0, "text": "2 экипажа", "rows": [rule["id"]]}
    before = copy.deepcopy(document)

    def forbidden(_prompt):
        pytest.fail("an already populated validated map needs no model call")

    result = repair.repair(document, _entries(), [finding], forbidden)
    assert result["left"] == [] and result["attempts"] == 0
    assert len(result["fixed"]) == 1
    assert document == before


@pytest.mark.parametrize("quantity,consumed", [(2, True), (7, False)],
                         ids=["trial-consumes-map-count", "trial-does-not-consume-extra-count"])
def test_repair_second_reading_trial_uses_full_context(monkeypatch, quantity, consumed):
    from cdpm import repair

    accepted = _typed_rule()
    # Second-reading hints are extractor proposals, not already pinned
    # landing/2 cells: _hint_reply strips registry_rev before acceptance.
    # Supply the actual proposal shape and let product code create the pin.
    hint = {"fit": {"category": "A", "family": "reserve_floors",
                    "params": {"reserve_crews": {
                        "value": copy.deepcopy(VALUES), "quote": QUOTE}}}}
    text = QUOTE + ". Дополнительно 7 пилотов."
    row = {key: copy.deepcopy(value) for key, value in accepted.items()
           if key != "template_fit"}
    row["statement"] = text
    document = _doc(row)
    entries = _entries(text) + _entries("Другой исходный текст.",
                                        sid="S-CONTEXT", anchor="context-row")
    expected_context = {(SID, ANCHOR): text,
                        ("S-CONTEXT", "context-row"): "Другой исходный текст."}
    before, before_hint = copy.deepcopy(document), copy.deepcopy(hint)
    finding = next(f for f in _report_unchanged(entries, document)["findings"]
                   if f["class"] == "count" and f["value"] == quantity)
    assert finding["rows"] == [row["id"]]
    assert "template_fit" not in row  # The initial consumption path cannot win.

    observed, contexts = [], []
    real_consumed = repair._bal_consumed

    def observe_trial(f, trial, *, clause_text_by_key=None, doc=None):
        # Observe, but never substitute the consumer or its verdict. The
        # candidate must already be accepted and still be a detached trial.
        assert trial is not row and "template_fit" not in row
        assert trial["template_fit"]["schema_version"] == "landing/2"
        assert trial["template_fit"]["params"]["reserve_crews"]["value"] == VALUES
        assert m.validate_template_fit({**document, "rules": [trial]}) == []
        assert m.validate_fit_quotes({**document, "rules": [trial]}, expected_context) == []
        result = real_consumed(f, trial, clause_text_by_key=clause_text_by_key, doc=doc)
        observed.append(result)
        contexts.append((clause_text_by_key, doc))
        return result

    def forbidden(_prompt):
        pytest.fail("this second-reading trial must not call a model")

    monkeypatch.setattr(repair, "_bal_consumed", observe_trial)
    result = repair.repair(document, entries, [finding], forbidden,
                           max_attempts=0, hints={(SID, ANCHOR): [hint]})
    assert observed == [consumed], repr(result)  # Proves the post-acceptance branch ran.
    assert len(contexts) == 1 and contexts[0][0] == expected_context
    assert contexts[0][1] is document and contexts[0][1]["questions"] == []
    assert result["attempts"] == 0
    assert hint == before_hint
    assert document["questions"] == before["questions"]
    if consumed:
        assert result["left"] == [] and len(result["fixed"]) == 1
        assert "по второму чтению" in result["fixed"][0]["repair"]
        assert row["template_fit"]["params"]["reserve_crews"]["value"] == VALUES
        after = _report_unchanged(entries, document)
        assert [f["value"] for f in after["findings"]] == [7.0]
        assert after["held_by_text"] == []
    else:
        assert result["fixed"] == [] and len(result["left"]) == 1
        assert result["left"][0]["value"] == 7.0
        assert document == before  # A valid but irrelevant trial is discarded.


def test_fabricated_answer_source_does_not_receive_machine_credit():
    rule = _typed_rule()
    rule["template_fit"]["params"]["reserve_crews"]["source"] = {
        "kind": "answer", "question": {"id": "Q-missing", "revision": 1,
        "fingerprint": "a" * 64}, "version": {"fingerprint": "b" * 64},
        "effect": {"rule": "reserve_crews", "field": "value"}, "root": "Q-missing"}
    document = _doc(rule)
    assert m.validate_fit_quotes(document, {(SID, ANCHOR): QUOTE})
    result = _report_unchanged(_entries(), document)
    assert result["findings"] and result["held_by_text"] == []


@pytest.mark.parametrize("damage", ["unknown-cell", "unknown-quote", "known-cell"])
def test_invalid_neighbor_does_not_erase_valid_structured_credit(damage):
    from cdpm import param_projection

    if damage == "known-cell":
        rule, name, quote = _code_list_case()
        assert rule["template_fit"]["family"] == "fleet_exclusions"
        neighbor = "fleet_type"
        bad = copy.deepcopy(rule["template_fit"]["params"][name])
        bad["value"] = {"bad": 7}  # Declared scalar code cell, wrong shape.
        expected = 3  # Family dictionary word plus the two literal codes.
    else:
        rule, name, quote = _typed_rule(), "reserve_crews", QUOTE
        neighbor = "undeclared_neighbor"
        bad = copy.deepcopy(rule["template_fit"]["params"][name])
        bad["value"] = {"XXX": 7}
        if damage == "unknown-quote":
            bad["quote"] = "Иной источник: XXX 7 экипажей"
        expected = 6
    baseline = _report_unchanged(_entries(quote), _doc(rule))
    assert baseline["by_fate"] == {"слот": expected}
    rule["template_fit"]["params"][neighbor] = bad
    document = _doc(rule)
    assert m.validate_template_fit(document)  # The defect is still a landing error.
    with pytest.raises(param_projection.ParamProjectionError):
        param_projection.derive_params_from_landing(rule)  # Consumer gate unchanged.
    result = _report_unchanged(_entries(quote), document)
    _assert_all_slots(result, expected)
    # An invalid neighbor's numbers cannot silently receive machine credit.
    extra = _report_unchanged(_entries(quote + ". Дополнительно 7 пилотов."), document)
    assert [f["value"] for f in extra["findings"]] == [7.0]
    assert extra["held_by_text"] == []


@pytest.mark.parametrize("damage", ["own-carrier", "global-pin"])
def test_cell_diagnostics_never_bypass_own_or_global_contract(damage):
    rule = _typed_rule()
    rule["template_fit"]["params"]["undeclared_neighbor"] = {"value": 9}
    if damage == "own-carrier":
        rule["params"] = [{"name": "reserve_crews", "value": 9, "unit": "count",
                           "relation": "ge", "qualifier": "PLY", "as_written": "9 экипажей"}]
    else:
        rule["template_fit"]["family_contract"]["catalog_fingerprint"] = "0" * 64
    result = _report_unchanged(_entries(), _doc(rule))
    assert any(f["value"] == 2 for f in result["findings"]), result
    assert result["held_by_text"] == []


def test_markdown_bold_transport_uses_the_same_clause_identity_as_reading():
    plain = _entries("Нужно 7 пилотов.")
    bold = copy.deepcopy(plain)
    bold[0]["clauses"][0]["text"] = "Нужно **7 пилотов**."
    single = _report_unchanged(plain, _doc())
    assert _report_unchanged(plain + bold, _doc()) == single
    assert _report_unchanged(bold + plain, _doc()) == single
    # Formatting normalization must not merge distinct source identities.
    bold[0]["file"] = "different-source.txt"
    result = _report_unchanged(plain + bold, _doc())
    assert result["clauses"] == result["values"] == result["unconsumed"] == 2
    assert result["findings"] == single["findings"] * 2


def test_neighbor_landing_error_does_not_send_healthy_map_to_model_repair():
    from cdpm import repair, param_projection

    rule = _typed_rule()
    rule["template_fit"]["params"]["undeclared_neighbor"] = {"value": 7}
    document = _doc(rule)
    before = copy.deepcopy(document)
    finding = {"sid": SID, "anchor": ANCHOR, "class": "count", "value": 2.0,
               "text": "2 экипажа", "rows": [rule["id"]]}

    def forbidden(_prompt):
        pytest.fail("a healthy cell must not become a question because its neighbor is defective")

    result = repair.repair(document, _entries(), [finding], forbidden)
    assert result["left"] == [] and result["attempts"] == 0
    assert len(result["fixed"]) == 1
    assert document == before and document["questions"] == []
    with pytest.raises(param_projection.ParamProjectionError):
        param_projection.derive_params_from_landing(rule)


@pytest.mark.parametrize("legacy", [
    {"reserve_crews": {"value": 2}},
    [None],
    [{}],
    [{"name": 3}],
], ids=["mapping-not-list", "non-record", "missing-name", "non-string-name"])
def test_malformed_legacy_carrier_cannot_credit_a_structured_cell(legacy):
    """Malformed carriers are refused before the diagnostic view slices them."""
    from cdpm import param_projection

    rule = _typed_rule()
    rule["params"] = []
    _assert_all_slots(_report_unchanged(_entries(), _doc(rule)), 6)
    rule["params"] = copy.deepcopy(legacy)
    with pytest.raises(param_projection.ParamProjectionError, match="params не список записей"):
        param_projection.derive_params_from_landing(rule)
    result = _report_unchanged(_entries(), _doc(rule))
    assert result["values"] == result["unconsumed"] == 6
    assert result["consumed"] == 0 and len(result["findings"]) == 6
    assert result["held_by_text"] == []


def test_non_string_neighbor_name_cannot_crash_healthy_cell_diagnostics():
    """Defensive Python-input case: JSON object names are always strings."""
    from cdpm import param_projection

    rule = _typed_rule()
    _assert_all_slots(_report_unchanged(_entries(), _doc(rule)), 6)
    rule["template_fit"]["params"][7] = copy.deepcopy(
        rule["template_fit"]["params"]["reserve_crews"])
    # A malformed neighbor still fails the whole-row consumer. Diagnostics
    # must not sort mixed int/str names or revoke the valid cell's credit.
    with pytest.raises(param_projection.ParamProjectionError):
        param_projection.derive_params_from_landing(rule)
    _assert_all_slots(_report_unchanged(_entries(), _doc(rule)), 6)


def test_unrelated_legacy_carrier_is_outside_the_selected_projection():
    """The reader's final selection already excludes other legacy names.

    This is a healthy equivalence control, not a claim that deleting the
    defensive diagnostic name filter must change observable behavior.
    """
    rule = _typed_rule()
    rule["params"] = [{"name": "unrelated_legacy", "value": 9, "unit": "count"}]
    before = copy.deepcopy(rule)
    full, full_problems = m.logic_param_records(rule, selected_names={"reserve_crews"})
    sliced = copy.deepcopy(rule)
    sliced["params"] = []
    selected, selected_problems = m.logic_param_records(
        sliced, selected_names={"reserve_crews"})
    assert not full_problems and not selected_problems
    assert full == selected and len(full) == 3
    assert {p["name"] for p in full} == {"reserve_crews"}
    assert rule == before
    _assert_all_slots(_report_unchanged(_entries(), _doc(rule)), 6)
