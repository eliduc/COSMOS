"""A12 first machine layer: synthetic independent expected counts, no extraction.

No UI, model translator, catalogue fixture, solver or existing runtime gate is
called. A valid ledger means addressable evidence, never complete meaning.
"""
import copy
import hashlib
import json

import pytest

from cdpm import obligations as ob


CLAUSE = "Смена не более 600 минут; экипаж обязан иметь действующий допуск к типу."
CONDITION = "только при действующем допуске"
EXCEPTION = "кроме учебного рейса"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _model():
    return {"rules": [
        {"id": "R-LIMIT", "kind": "HARD", "statement": "Смена не более 600 минут",
         "params": [{"name": "max_duty_duration", "value": 600,
                     "unit": "minutes", "relation": "max"}],
         "sources": [{"sid": "S1", "anchor": "§1"}]},
        {"id": "R-LICENCE", "kind": "HARD", "statement": "Действующий допуск к типу",
         "sources": [{"sid": "S1", "anchor": "§1"}],
         "conditions": [CONDITION], "exceptions": [EXCEPTION]},
    ], "objectives": [], "questions": [
        {"id": "Q-OPEN", "text": "Как проверять допуск?", "rule_refs": ["R-LICENCE"]}]}


def _reseal(ledger):
    ledger["readers"] = [ob.seal_reader(r) for r in ledger["readers"]]


def _fixture():
    args = {"freeze_bytes": b'{"freeze":"synthetic-a12"}',
            "model_bytes": _json(_model()),
            "documents": [{"sid": "S1", "body": CLAUSE.encode("utf-8"),
                           "clauses": [{"anchor": "§1", "text": CLAUSE}]}]}
    snapshot = ob.capture_snapshot(**args)
    unit = ob.document_unit("S1", "§1")
    source = {"unit": unit, "fragments": [{"start": 0, "end": len(CLAUSE.encode("utf-8")),
                                          "quote": CLAUSE}]}
    obligations = [
        {"id": "O1", "subject": "экипаж", "modality": "requirement",
         "statement": "Предел смены 600 минут", "conditions": [], "exceptions": [],
         "sources": [source], "depends_on": []},
        {"id": "O2", "subject": "экипаж", "modality": "requirement",
         "statement": "Действующий допуск к типу", "conditions": [], "exceptions": [],
         "sources": [source], "depends_on": []},
    ]
    readers = [ob.seal_reader({"id": rid, "actor": rid, "method": "manual-" + rid,
                              "version": "1", "input_sha256": ob.fingerprint(snapshot),
                              "exposure": {"other_reader": False, "model": False},
                              "reviewed_units": [unit], "obligations": copy.deepcopy(obligations)})
               for rid in ("LEFT", "RIGHT")]
    ledger = {"schema_version": "obligation-ledger/1", "snapshot": snapshot,
              "scope": {"included": [unit], "excluded": []}, "readers": readers,
              "links": [], "comparisons": [], "expert_review": None}
    return ledger, args


def _link(ledger, reader="LEFT", oid="O1", *, component="statement",
          collection="rules", row="R-LIMIT", pointer="/params/0/value", status="declared"):
    ledger["links"].append({"reader": reader, "obligation": oid,
                            "model_sha256": ledger["snapshot"]["model_sha256"],
                            "status": status, "author": "fixture reviewer", "reason": "explicit fixture correspondence",
                            "targets": [{"collection": collection, "row_id": row,
                                         "pointer": pointer, "component": component}]})


def _agree(ledger):
    ledger["comparisons"] = [
        {"id": "M1", "left": {"reader": "LEFT", "obligations": ["O1"]},
         "right": {"reader": "RIGHT", "obligations": ["O1"]},
         "status": "agreed", "author": "fixture reviewer", "reason": "explicit decision"},
        {"id": "M2", "left": {"reader": "LEFT", "obligations": ["O2"]},
         "right": {"reader": "RIGHT", "obligations": ["O2"]},
         "status": "agreed", "author": "fixture reviewer", "reason": "explicit decision"},
    ]


def _left(report):
    assert report["form"] == "valid", report
    return report["readers"][0]


def test_two_obligations_at_one_anchor_count_separately_and_reverse_links_exist():
    ledger, args = _fixture()
    _link(ledger)
    result = ob.evaluate(ledger, **args)
    assert (_left(result)["total"], _left(result)["linked"], _left(result)["missing"]) == (2, 1, ["O2"])
    assert result["reverse_links"] == [{"collection": "rules", "row_id": "R-LIMIT",
        "pointer": "/params/0/value", "reader": "LEFT", "obligation": "O1", "component": "statement"}]
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/statement")
    result = ob.evaluate(ledger, **args)
    assert (_left(result)["total"], _left(result)["linked"], _left(result)["missing"]) == (2, 2, [])
    assert result["meaning"] == "not_proven"
    assert result["expert_review"] == {"status": "not_reviewed"}


def test_equal_lists_need_explicit_comparison_and_never_prove_meaning():
    ledger, args = _fixture()
    result = ob.evaluate(ledger, **args)
    assert [r["unmatched"] for r in result["readers"]] == [["O1", "O2"], ["O1", "O2"]]
    _agree(ledger)
    result = ob.evaluate(ledger, **args)
    assert [r["unmatched"] for r in result["readers"]] == [[], []]
    assert [c["identical_payload"] for c in result["comparisons"]] == [True, True]
    assert [c["meaning"] for c in result["comparisons"]] == ["not_verified", "not_verified"]
    assert result["independence"] == "declared_only"
    assert result["meaning"] == "not_proven"


def test_empty_results_and_unreviewed_inputs_are_not_absence_of_obligations():
    ledger, args = _fixture()
    for reader in ledger["readers"]:
        reader["obligations"] = []
        reader["reviewed_units"] = []
    _reseal(ledger)
    result = ob.evaluate(ledger, **args)
    assert result["form"] == "valid"
    assert [r["total"] for r in result["readers"]] == [0, 0]
    assert [len(r["unreviewed_units"]) for r in result["readers"]] == [1, 1]
    assert [i["code"] for i in result["issues"]] == ["empty_extraction_unverified", "empty_extraction_unverified"]
    assert result["meaning"] == "not_proven"


def test_one_sided_obligation_and_similar_duplicate_are_not_merged():
    ledger, args = _fixture()
    ledger["readers"][1]["obligations"].pop()
    duplicate = copy.deepcopy(ledger["readers"][0]["obligations"][1])
    duplicate["id"] = "O3"
    ledger["readers"][0]["obligations"].append(duplicate)
    _reseal(ledger)
    result = ob.evaluate(ledger, **args)
    assert [r["total"] for r in result["readers"]] == [3, 1]
    assert [r["unmatched"] for r in result["readers"]] == [["O1", "O2", "O3"], ["O1"]]


def test_conditions_and_exceptions_require_separate_addressed_components():
    ledger, args = _fixture()
    item = ledger["readers"][0]["obligations"][1]
    item["conditions"] = [{"id": "C1", "text": CONDITION}]
    item["exceptions"] = [{"id": "E1", "text": EXCEPTION}]
    _reseal(ledger)
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/statement")
    result = ob.evaluate(ledger, **args)
    assert _left(result)["items"][1]["missing_components"] == ["conditions/C1", "exceptions/E1"]
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/conditions/0", component="conditions/C1")
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/exceptions/0", component="exceptions/E1")
    result = ob.evaluate(ledger, **args)
    assert _left(result)["items"][1]["status"] == "linked"
    assert result["meaning"] == "not_proven"
    _agree(ledger)
    result = ob.evaluate(ledger, **args)
    assert result["comparisons"][1]["identical_payload"] is False
    assert result["comparisons"][1]["different_parts"] == ["conditions", "exceptions"]
    assert ledger["readers"][1]["obligations"][1]["conditions"] == []


@pytest.mark.parametrize("status", ["declared", "open_question"])
def test_question_is_never_implemented_obligation(status):
    ledger, args = _fixture()
    _link(ledger, oid="O2", collection="questions", row="Q-OPEN", pointer="/text", status=status)
    result = ob.evaluate(ledger, **args)
    assert _left(result)["items"][1]["status"] == "open_question"
    assert _left(result)["linked"] == 0


def test_disputed_link_stays_disputed_despite_another_declared_link():
    ledger, args = _fixture()
    _link(ledger)
    _link(ledger, status="disputed")
    result = ob.evaluate(ledger, **args)
    assert _left(result)["items"][0]["status"] == "disputed"
    assert _left(result)["linked"] == 0


@pytest.mark.parametrize("change", ["document_bytes", "clause_bytes", "freeze", "model", "absent_document"])
def test_changed_or_missing_inputs_do_not_inherit_links(change):
    ledger, args = _fixture()
    _link(ledger)
    if change == "document_bytes":
        args["documents"][0]["body"] += b"\n"
    elif change == "clause_bytes":
        args["documents"][0]["clauses"][0]["text"] += " "
    elif change == "freeze":
        args["freeze_bytes"] += b" "
    elif change == "model":
        args["model_bytes"] += b" "
    else:
        args["documents"] = []
    result = ob.evaluate(ledger, **args)
    assert result["freshness"] == "stale_or_unresolved"
    assert _left(result)["linked"] == 0
    assert _left(result)["items"][0]["status"] == "stale"
    assert result["reverse_links"] == []


@pytest.mark.parametrize("edit", ["duplicate_id", "unsealed", "unknown_version", "bad_condition", "bad_dependencies"])
def test_bad_form_is_addressed_and_does_not_leak_partial_counts(edit):
    ledger, args = _fixture()
    _link(ledger)
    reader = ledger["readers"][0]
    if edit == "duplicate_id":
        reader["obligations"].append(copy.deepcopy(reader["obligations"][0]))
    elif edit == "unsealed":
        reader["obligations"][0]["statement"] += " изменено"
    elif edit == "unknown_version":
        ledger["schema_version"] = "obligation-ledger/999"
    elif edit == "bad_condition":
        reader["obligations"][0]["conditions"] = None
    else:
        reader["obligations"][0]["depends_on"] = ["MISSING"]
    if edit != "unsealed":
        _reseal(ledger)
    result = ob.evaluate(ledger, **args)
    assert result["form"] == "invalid"
    assert result["readers"] == [] and result["reverse_links"] == []
    assert result["issues"][0]["code"] == "invalid_form"


@pytest.mark.parametrize("fragment", [
    {"start": True, "end": 2, "quote": "С"},
    {"start": 1, "end": 2, "quote": "С"},
    {"start": 0, "end": 2, "quote": "Д"},
    {"start": -1, "end": 2, "quote": "С"},
    {"start": 0, "end": 999, "quote": CLAUSE},
])
def test_utf8_locators_are_exact_not_character_offsets_or_normalized_quotes(fragment):
    ledger, args = _fixture()
    ledger["readers"][0]["obligations"][0]["sources"][0]["fragments"] = [fragment]
    _reseal(ledger)
    assert ob.evaluate(ledger, **args)["form"] == "invalid"


def test_disjoint_utf8_fragments_are_preserved():
    ledger, args = _fixture()
    raw = CLAUSE.encode("utf-8")
    first, last = "Смена", "типу"
    start = raw.index(last.encode("utf-8"))
    pieces = [{"start": 0, "end": len(first.encode("utf-8")), "quote": first},
              {"start": start, "end": start + len(last.encode("utf-8")), "quote": last}]
    ledger["readers"][0]["obligations"][0]["sources"][0]["fragments"] = pieces
    _reseal(ledger)
    assert ob.evaluate(ledger, **args)["form"] == "valid"
    assert len(pieces) == 2


@pytest.mark.parametrize("field,value", [("row_id", "R-FOREIGN"), ("pointer", "/params/1/value"),
                                          ("pointer", "/params/00/value"), ("pointer", "/wrong~2key"),
                                          ("collection", "unknown"), ("component", "conditions/MISSING")])
def test_exact_model_row_and_field_are_required(field, value):
    ledger, args = _fixture()
    _link(ledger)
    ledger["links"][0]["targets"][0][field] = value
    assert ob.evaluate(ledger, **args)["form"] == "invalid"


def test_sealed_results_and_all_inputs_remain_byte_for_byte_unchanged():
    ledger, args = _fixture()
    _link(ledger)
    _agree(ledger)
    before = copy.deepcopy((ledger, args))
    result = ob.evaluate(ledger, **args)
    result["readers"][0]["items"][0]["status"] = "caller edit"
    assert (ledger, args) == before
    assert ledger["snapshot"]["model_sha256"] == hashlib.sha256(args["model_bytes"]).hexdigest()


def test_exposure_and_expert_record_are_not_machine_authorization():
    ledger, args = _fixture()
    ledger["readers"][1]["exposure"]["model"] = True
    _reseal(ledger)
    ledger["expert_review"] = {"reviewer": "fixture owner", "decision": "accepted", "reason": "synthetic record",
        "snapshot_sha256": ob.fingerprint(ledger["snapshot"]), "scope_sha256": ob.fingerprint(ledger["scope"]),
        "reviewed_content_sha256": ob.fingerprint({k: v for k, v in ledger.items() if k != "expert_review"}),
        "issues": []}
    result = ob.evaluate(ledger, **args)
    assert result["independence"] == "contradicted_by_declaration"
    assert result["expert_review"]["status"] == "recorded"
    assert result["expert_review"]["authority"] == "not_verified"
    assert result["meaning"] == "not_proven"
    args["freeze_bytes"] += b" "
    assert ob.evaluate(ledger, **args)["expert_review"]["status"] == "stale"


@pytest.mark.parametrize("change", [None, "readers", "links", "comparisons", "metadata"])
def test_expert_review_is_bound_to_reviewed_content_not_only_source_snapshot(change):
    ledger, args = _fixture()
    _link(ledger)
    _agree(ledger)
    reviewed = ob.fingerprint({k: v for k, v in ledger.items() if k != "expert_review"})
    ledger["expert_review"] = {
        "reviewer": "fixture owner", "decision": "accepted", "reason": "reviewed original content",
        "snapshot_sha256": ob.fingerprint(ledger["snapshot"]),
        "scope_sha256": ob.fingerprint(ledger["scope"]),
        "reviewed_content_sha256": reviewed, "issues": []}
    if change == "readers":
        ledger["readers"][0]["obligations"][0]["statement"] = "A different authored obligation"
        _reseal(ledger)
    elif change == "links":
        ledger["links"][0]["status"] = "disputed"
    elif change == "comparisons":
        ledger["comparisons"][0]["status"] = "disputed"
    elif change == "metadata":
        ledger["author_note"] = "Added after this review"
    else:
        # The review itself is outside its content digest: no recursive hash.
        ledger["expert_review"]["reason"] += "; retained author annotation"
    before = copy.deepcopy(ledger)
    report = ob.evaluate(ledger, **args)
    assert report["form"] == "valid", report
    assert report["freshness"] == "current", report
    assert report["expert_review"]["status"] == ("recorded" if change is None else "stale"), report
    assert report["expert_review"]["record"]["reviewed_content_sha256"] == reviewed
    assert report["expert_review"]["authority"] == "not_verified"
    assert report["meaning"] == "not_proven"
    assert ledger == before


def test_legacy_expert_review_without_content_binding_is_retained_but_not_current():
    ledger, args = _fixture()
    ledger["expert_review"] = {
        "reviewer": "fixture owner", "decision": "accepted", "reason": "legacy record",
        "snapshot_sha256": ob.fingerprint(ledger["snapshot"]),
        "scope_sha256": ob.fingerprint(ledger["scope"]), "issues": []}
    report = ob.evaluate(ledger, **args)
    assert report["form"] == "valid", report
    assert report["expert_review"]["status"] == "stale", report
    assert report["expert_review"]["record"] == ledger["expert_review"]
    assert report["expert_review"]["authority"] == "not_verified"


def _answer_fixture():
    from cdpm import provenance, question_editions, answer_versions
    ledger, args = _fixture()
    doc = _model()
    question = {"id": "Q-CONSENT", "part": "1", "title": "Условие допуска",
                "text": "Уточните условие назначения", "answer_format": {"type": "value"},
                "rule_refs": ["R-LICENCE"], "source_refs": [{"sid": "S1", "anchor": "§1"}]}
    question_editions.freeze(question)
    question["answer"] = {"raw": "Можно назначать только при действующем допуске", "status": "применён"}
    answer_versions.stamp(question["answer"], None, {"received_at": None, "book_sha256": None})
    doc["questions"].append(question)
    source = {"kind": "answer", "question": provenance.question_edition(question),
              "version": {"fingerprint": question["answer"]["answer_version"]["fingerprint"]}}
    args["model_bytes"] = _json(doc)
    args["answer_sources"] = [source]
    ledger["snapshot"] = ob.capture_snapshot(**args)
    unit = ob.answer_unit(source)
    ledger["scope"]["included"].append(unit)
    raw = question["answer"]["raw"]
    for reader in ledger["readers"]:
        reader["input_sha256"] = ob.fingerprint(ledger["snapshot"])
        reader["reviewed_units"].append(unit)
        item = reader["obligations"][1]
        item["sources"] = [{"unit": unit, "fragments": [{"start": 0, "end": len(raw.encode("utf-8")), "quote": raw}]}]
        item["conditions"] = [{"id": "C1", "text": CONDITION}]
    _reseal(ledger)
    return ledger, args


def test_answer_uses_real_identity_resolver_and_exact_scope_not_root_guess():
    ledger, args = _answer_fixture()
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/statement")
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/conditions/0", component="conditions/C1")
    result = ob.evaluate(ledger, **args)
    assert result["freshness"] == "current"
    assert _left(result)["items"][1]["status"] == "linked"
    ledger["links"][0]["targets"][0].update(row_id="R-LIMIT", pointer="/statement")
    assert ob.evaluate(ledger, **args)["form"] == "invalid"


@pytest.mark.parametrize("change", ["answer", "revision", "fingerprint", "foreign_question", "raw_whitespace"])
def test_changed_answer_identity_or_exact_raw_does_not_reuse_old_evidence(change):
    ledger, args = _answer_fixture()
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/statement")
    if change in {"answer", "raw_whitespace"}:
        doc = json.loads(args["model_bytes"])
        doc["questions"][-1]["answer"]["raw"] += " иначе" if change == "answer" else " "
        args["model_bytes"] = _json(doc)
    elif change == "revision":
        args["answer_sources"][0]["question"]["revision"] = 2
    elif change == "fingerprint":
        args["answer_sources"][0]["version"]["fingerprint"] = "f" * 12
    else:
        args["answer_sources"][0]["question"]["id"] = "Q-FOREIGN"
    result = ob.evaluate(ledger, **args)
    assert result["freshness"] == "stale_or_unresolved"
    assert _left(result)["linked"] == 0


def test_duplicate_comparison_does_not_conceal_a_one_sided_obligation():
    ledger, args = _fixture()
    _agree(ledger)
    ledger["comparisons"][1]["left"]["obligations"] = ["O1"]
    assert ob.evaluate(ledger, **args)["form"] == "invalid"


def test_source_without_clauses_is_still_in_the_exact_snapshot():
    ledger, args = _fixture()
    args["documents"].append({"sid": "S-EMPTY", "body": b"unprocessed", "clauses": []})
    snap = ob.capture_snapshot(**args)
    assert [d["sid"] for d in snap["documents"]] == ["S-EMPTY", "S1"]
    assert snap != ledger["snapshot"]
    ledger["snapshot"] = snap
    for reader in ledger["readers"]:
        reader["input_sha256"] = ob.fingerprint(snap)
    _reseal(ledger)
    result = ob.evaluate(ledger, **args)
    assert result["scope"]["unsegmented_documents"] == ["S-EMPTY"]


def _refresh_fixture_snapshot(ledger, args):
    ledger["snapshot"] = ob.capture_snapshot(**args)
    for reader in ledger["readers"]:
        reader["input_sha256"] = ob.fingerprint(ledger["snapshot"])
    _reseal(ledger)


def test_partial_answer_generic_link_does_not_prove_the_processed_scope():
    ledger, args = _answer_fixture()
    doc = json.loads(args["model_bytes"])
    doc["questions"][-1]["answer"]["status"] = "применён частично"
    args["model_bytes"] = _json(doc)
    _refresh_fixture_snapshot(ledger, args)
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/statement")
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/conditions/0", component="conditions/C1")
    result = ob.evaluate(ledger, **args)
    assert _left(result)["items"][1]["status"] == "unverified_answer_application"
    assert _left(result)["linked"] == 0


@pytest.mark.parametrize("mutation", [None, "version", "effect", "root"])
def test_cell_provenance_is_resolved_by_existing_law_not_replaced_by_ledger(mutation):
    from cdpm import provenance
    ledger, args = _answer_fixture()
    doc = json.loads(args["model_bytes"])
    question = doc["questions"][-1]
    src = provenance.answer_source(question, "licence_required", question["answer"]["raw"])
    if mutation == "version":
        src["version"]["fingerprint"] = "f" * 12
    elif mutation == "effect":
        src["effect"]["rule"] = "other_parameter"
    elif mutation == "root":
        src["root"] = "Q-OTHER"
    doc["rules"][1]["template_fit"] = {"params": {"licence_required": {"value": True, "source": src}}}
    args["model_bytes"] = _json(doc)
    _refresh_fixture_snapshot(ledger, args)
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/template_fit/params/licence_required/value")
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/conditions/0", component="conditions/C1")
    result = ob.evaluate(ledger, **args)
    if mutation is None:
        assert _left(result)["items"][1]["status"] == "linked"
    else:
        assert result["form"] == "invalid"


def test_scope_exclusion_is_explicit_and_never_machine_authorization():
    ledger, args = _fixture()
    unit = ledger["scope"]["included"].pop()
    ledger["scope"]["excluded"] = [{"unit": unit, "author": "fixture owner", "reason": "explicit synthetic scope"}]
    for reader in ledger["readers"]:
        reader["reviewed_units"] = []
        reader["obligations"] = []
    _reseal(ledger)
    result = ob.evaluate(ledger, **args)
    assert result["scope"]["unaccounted"] == []
    assert result["scope"]["exclusion_authority"] == "not_verified"
    assert result["meaning"] == "not_proven"
    del ledger["scope"]["excluded"][0]["reason"]
    assert ob.evaluate(ledger, **args)["form"] == "invalid"
