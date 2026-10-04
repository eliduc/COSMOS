"""A12 K3: mechanical source grouping through the real public reader API.

Only prompt/reply channels are synthetic. Parsing, address recovery, semantic
retry guards, sealing and evaluation are real. Literal fragment coordinates
are an independent fixture contract, not output copied from the implementation.
No live model, customer corpus, semantic-completeness or audit claim is made.
"""
import copy
import hashlib
import json

import pytest

from cdpm import obligations as ob
from test_a12_obligation_readers import Channel, core, run
from test_a12_quote_address_recovery_agent import _addressed
from test_cdpm_a12_obligations import _fixture


TEXT = "alpha beta alpha end."
BETA = {"start": 6, "end": 10, "quote": "beta"}
END = {"start": 17, "end": 21, "quote": "end."}
SECOND_ALPHA = {"start": 11, "end": 16, "quote": "alpha"}
AMBIGUOUS_ALPHA = {"start": 1, "end": 3, "quote": "alpha"}
WRONG_BETA = {"start": 1, "end": 2, "quote": "beta"}
CONDITIONS = [{"id": "C1", "text": "When assigned"},
              {"id": "C2", "text": "With a current plan"}]
EXCEPTIONS = [{"id": "E1", "text": "Except training"},
              {"id": "E2", "text": "Except the named exemption"}]


def _inputs():
    _, inputs = _fixture()
    inputs["documents"][0]["body"] = TEXT.encode("utf-8")
    inputs["documents"][0]["clauses"][0]["text"] = TEXT
    return inputs


def _payload(unit, fragments, split):
    fragments = copy.deepcopy(fragments)
    refs = ([{"unit": unit, "fragments": [fragment]} for fragment in fragments]
            if split else [{"unit": unit, "fragments": fragments}])
    first = {"id": "O1", "subject": "crew", "statement": "The first obligation",
             "modality": "requirement", "conditions": copy.deepcopy(CONDITIONS),
             "exceptions": copy.deepcopy(EXCEPTIONS), "sources": refs, "depends_on": []}
    second = {"id": "O2", "subject": "crew", "statement": "The dependent obligation",
              "modality": "requirement", "conditions": [], "exceptions": [],
              "sources": [{"unit": unit, "fragments": [copy.deepcopy(BETA)]}],
              "depends_on": ["O1"]}
    return {"unit": unit, "obligations": [first, second]}


def _channel(reader, fragments, split, *, retry=None, damage=None):
    raws = []

    def complete(_unused, prompt):
        data = json.loads(prompt)
        selected = (retry if data.get("mode") == "address_clarification" and retry is not None
                    else (fragments, split))
        value = _payload(data["input"]["unit"], *selected)
        if damage:
            damage(value, data)
        raw = "\r\n " + json.dumps(value, ensure_ascii=False, indent=2) + "\r\n"
        raws.append(raw)
        return raw

    channel = Channel("claude-fixture" if reader == "LEFT" else "gpt-fixture", complete)
    channel.raws = raws
    return channel


def _evidence(evidence, left, right):
    assert evidence["schema_version"] == "obligation-readings/2"
    calls = evidence["calls"]
    for reader, channel in (("LEFT", left), ("RIGHT", right)):
        own = [call for call in calls if call["reader"] == reader]
        assert [call["prompt"] for call in own] == channel.prompts
        assert [call["raw_reply"] for call in own] == channel.raws
        for number, call in enumerate(own, 1):
            assert call["attempt"] == number
            assert call["purpose"] == ("reading" if number == 1 else "address_clarification")
            assert call["prompt_sha256"] == hashlib.sha256(call["prompt"].encode("utf-8")).hexdigest()
            assert call["reply_sha256"] == hashlib.sha256(call["raw_reply"].encode("utf-8")).hexdigest()
    return calls


def _assert_candidate(result, inputs, fragments):
    ledger = result.ledger
    unit = ledger["snapshot"]["units"][0]["id"]
    for reader in ledger["readers"]:
        first, second = reader["obligations"]
        assert [first["id"], second["id"]] == [unit + "/O1", unit + "/O2"]
        assert first["subject"] == "crew" and first["statement"] == "The first obligation"
        assert first["modality"] == "requirement"
        assert first["conditions"] == CONDITIONS and first["exceptions"] == EXCEPTIONS
        assert first["depends_on"] == [] and second["depends_on"] == [unit + "/O1"]
        assert first["sources"] == [{"unit": unit, "fragments": [
            _addressed(TEXT, fragment) for fragment in fragments]}]
        assert reader["result_sha256"] == ob.seal_reader(reader)["result_sha256"]
    assert len(ledger["comparisons"]) == 2
    assert result.evidence["comparison"]["open"] == {"LEFT": [], "RIGHT": []}
    report = ob.evaluate(ledger, **inputs)
    assert report["form"] == "valid" and report["freshness"] == "current"
    assert report["meaning"] == "not_proven" and report["independence"] == "declared_only"
    assert ledger["links"] == [] and ledger["expert_review"] is None


@pytest.mark.parametrize("split,fragments", [
    pytest.param(True, [BETA, END], id="same-unit-two-references"),
    pytest.param(False, [BETA, END], id="existing-grouped-healthy-control"),
    pytest.param(True, [END, BETA], id="preserve-order-not-coordinate-sort"),
])
def test_exact_same_unit_references_merge_without_extra_calls_or_semantic_changes(split, fragments):
    inputs = _inputs()
    before = copy.deepcopy(inputs)
    left = _channel("LEFT", fragments, split)
    right = _channel("RIGHT", fragments, False)
    result, _, _ = run(inputs, left, right)
    _assert_candidate(result, inputs, fragments)
    calls = _evidence(result.evidence, left, right)
    assert [call["status"] for call in calls] == ["valid", "valid"]
    assert len(left.prompts) == len(right.prompts) == 1
    original_refs = json.loads(left.raws[0])["obligations"][0]["sources"]
    assert len(original_refs) == (2 if split else 1)
    assert [fragment for ref in original_refs for fragment in ref["fragments"]] == fragments
    assert inputs == before


@pytest.mark.parametrize("duplicate", [BETA, WRONG_BETA], ids=["declared", "after-unique-recovery"])
def test_duplicate_fragment_across_references_is_refused_even_after_recovery(duplicate):
    inputs = _inputs()
    before = copy.deepcopy(inputs)
    left = _channel("LEFT", [BETA, duplicate], True)
    right = _channel("RIGHT", [BETA, END], False)
    with pytest.raises(core().ObligationReadError, match="duplicate fragment") as error:
        run(inputs, left, right)
    assert error.value.evidence["candidate"] is False
    assert [call["status"] for call in _evidence(error.value.evidence, left, right)] == ["invalid"]
    assert len(left.prompts) == 1 and right.prompts == [] and inputs == before


def test_identical_quote_at_two_distinct_declared_addresses_is_not_collapsed():
    fragments = [{"start": 0, "end": 5, "quote": "alpha"}, SECOND_ALPHA]
    inputs = _inputs()
    left = _channel("LEFT", fragments, True)
    right = _channel("RIGHT", fragments, False)
    result, _, _ = run(inputs, left, right)
    _assert_candidate(result, inputs, fragments)
    assert [call["status"] for call in _evidence(result.evidence, left, right)] == ["valid", "valid"]


def test_same_unit_id_in_separate_text_snapshots_never_reuses_grouped_evidence():
    first_inputs = _inputs()
    second_inputs = copy.deepcopy(first_inputs)
    other_text = "omega beta omega end."
    second_inputs["documents"][0].update(
        body=other_text.encode("utf-8"), clauses=[{"anchor": "§1", "text": other_text}])
    first, _, _ = run(first_inputs, _channel("LEFT", [BETA, END], True),
                      _channel("RIGHT", [BETA, END], False))
    saved_first = first.ledger_bytes
    second, _, _ = run(second_inputs, _channel("LEFT", [BETA, END], True),
                       _channel("RIGHT", [BETA, END], False))
    units = [result.ledger["snapshot"]["units"][0] for result in (first, second)]
    assert units[0]["id"] == units[1]["id"]
    assert units[0]["text_sha256"] != units[1]["text_sha256"]
    for result, inputs, text in ((first, first_inputs, TEXT), (second, second_inputs, other_text)):
        for reader in result.ledger["readers"]:
            fragments = reader["obligations"][0]["sources"][0]["fragments"]
            assert fragments == [_addressed(text, fragment) for fragment in (BETA, END)]
        assert ob.evaluate(result.ledger, **inputs)["form"] == "valid"
    assert first.ledger_bytes == saved_first


@pytest.mark.parametrize("case,index", [
    ("foreign", 0), ("foreign", 1), ("empty", 0), ("empty", 1),
    ("missing-fragments", 1), ("not-list", 1), ("unknown-key", 1),
])
def test_each_reference_is_validated_before_grouping(case, index):
    def damage(value, _data):
        ref = value["obligations"][0]["sources"][index]
        if case == "foreign": ref["unit"] = "document:foreign"
        elif case == "empty": ref["fragments"] = []
        elif case == "missing-fragments": ref.pop("fragments")
        elif case == "not-list": ref["fragments"] = {"0": copy.deepcopy(END)}
        else: ref["ignored"] = "must not disappear in grouping"

    inputs = _inputs()
    before = copy.deepcopy(inputs)
    left = _channel("LEFT", [BETA, END], True, damage=damage)
    right = _channel("RIGHT", [BETA, END], False)
    with pytest.raises(core().ObligationReadError) as error:
        run(inputs, left, right)
    assert error.value.evidence["candidate"] is False
    assert [call["status"] for call in _evidence(error.value.evidence, left, right)] == ["invalid"]
    assert len(left.prompts) == 1 and right.prompts == [] and inputs == before


@pytest.mark.parametrize("initial_split", [True, False], ids=["split-to-grouped", "grouped-to-split"])
@pytest.mark.parametrize("ambiguous_index", [0, 1], ids=["first-fragment", "second-fragment"])
def test_clarification_may_change_only_grouping_and_the_original_ambiguous_address(initial_split, ambiguous_index):
    initial, resolved = [copy.deepcopy(BETA)] * 2, [copy.deepcopy(BETA)] * 2
    initial[ambiguous_index] = AMBIGUOUS_ALPHA
    resolved[ambiguous_index] = SECOND_ALPHA
    inputs = _inputs()
    before = copy.deepcopy(inputs)
    left = _channel("LEFT", initial, initial_split, retry=(resolved, not initial_split))
    right = _channel("RIGHT", resolved, False)
    result, _, _ = run(inputs, left, right)
    _assert_candidate(result, inputs, resolved)
    calls = _evidence(result.evidence, left, right)
    assert [call["status"] for call in calls] == ["ambiguous", "valid", "valid"]
    assert len(left.prompts) == 2 and len(right.prompts) == 1
    original, clarification = map(json.loads, left.prompts)
    assert clarification["mode"] == "address_clarification"
    assert clarification["input"] == original["input"]
    assert clarification["previous_reply"] == left.raws[0] and clarification["error"]
    assert [len(json.loads(raw)["obligations"][0]["sources"]) for raw in left.raws] == (
        [2, 1] if initial_split else [1, 2])
    assert inputs == before


@pytest.mark.parametrize("case", ["statement", "conditions", "dependencies", "count",
    "order", "fixed-fragment", "other-quote", "duplicate", "foreign-source"])
def test_source_grouping_on_retry_cannot_hide_semantic_or_fragment_changes(case):
    def damage(value, data):
        if data.get("mode") != "address_clarification":
            return
        first, second = value["obligations"]
        fragments = first["sources"][0]["fragments"]
        if case == "statement": first["statement"] += " changed"
        elif case == "conditions": first["conditions"].reverse()
        elif case == "dependencies": second["depends_on"] = []
        elif case == "count": fragments.pop()
        elif case == "order": fragments.reverse()
        elif case == "fixed-fragment": fragments[0] = copy.deepcopy(END)
        elif case == "other-quote": fragments[1] = copy.deepcopy(END)
        elif case == "duplicate": fragments[1] = copy.deepcopy(BETA)
        else: first["sources"][0]["unit"] = "document:foreign"

    inputs = _inputs()
    before = copy.deepcopy(inputs)
    left = _channel("LEFT", [BETA, AMBIGUOUS_ALPHA], True,
                    retry=([BETA, SECOND_ALPHA], False), damage=damage)
    right = _channel("RIGHT", [BETA, SECOND_ALPHA], False)
    with pytest.raises(core().ObligationReadError) as error:
        run(inputs, left, right)
    assert error.value.evidence["candidate"] is False
    assert [call["status"] for call in _evidence(error.value.evidence, left, right)] == ["ambiguous", "invalid"]
    assert len(left.prompts) == 2 and right.prompts == [] and inputs == before
