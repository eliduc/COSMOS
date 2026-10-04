"""Pure set law: independent expected names, no services or catalogue writes."""
import copy

import pytest


def record():
    return {"id": "example", "params": [{"name": n} for n in ("a", "b", "c")],
            "param_sets": [{"id": "single", "params": ["a"]},
                           {"id": "pair", "params": ["b", "c"]}]}


@pytest.mark.parametrize("given,declined,pending,set_id,missing,complete", [
    ([], [], [], "single", ["a"], False),
    (["a"], [], [], "single", [], True),
    ([], ["a"], [], "single", [], True),
    (["b"], [], [], "pair", ["c"], False),
    (["b"], ["c"], [], "pair", [], True),
    (["a", "b", "c"], [], [], "single", [], True),
    (["a"], [], ["a"], "single", [], False),
    ([], ["a"], ["a"], "single", [], False),
])
def test_ordered_choice_and_pending_are_distinct(given, declined, pending, set_id, missing, complete):
    from template_catalog.completeness import family_completeness
    rec = record()
    before = copy.deepcopy((rec, given, declined, pending))
    got = family_completeness(rec, given, declined, pending)
    assert (got["set_id"], got["missing"], got["complete"]) == (set_id, missing, complete)
    assert not got["problems"]
    assert (rec, given, declined, pending) == before


def test_named_choice_cannot_follow_an_easier_alternative():
    from template_catalog.completeness import family_completeness
    result = family_completeness(record(), ["a", "b"], set_id="pair")
    assert result["set_id"] == "pair" and result["missing"] == ["c"]
    result = family_completeness(record(), ["a", "b", "c"], set_id="absent")
    assert not result["complete"] and result["problems"] and result["set_id"] is None


def test_without_declared_sets_all_parameters_are_required():
    from template_catalog.completeness import family_completeness
    rec = record()
    rec.pop("param_sets")
    got = family_completeness(rec, ["a"])
    assert got["set_id"] == "all" and got["missing"] == ["b", "c"]


@pytest.mark.parametrize("bad", [None, "bad", {}, {"id": "bad", "params": ["unknown"]},
                                 {"id": "single", "params": ["a"]},
                                 {"id": "bad", "params": []}])
def test_broken_choices_never_make_the_record_complete(bad):
    from template_catalog.completeness import family_completeness
    rec = record()
    rec["param_sets"].append(bad)
    got = family_completeness(rec, ["a", "b", "c"])
    assert got["problems"] and not got["complete"]


def test_bad_set_is_not_silently_narrowed_to_its_known_members():
    from template_catalog.completeness import family_completeness
    rec = record()
    rec["param_sets"] = [{"id": "bad", "params": ["a", "unknown"]}]
    got = family_completeness(rec, ["a"])
    assert got["missing"] == ["b", "c"] and got["problems"]


def test_no_cdpm_dependency():
    import inspect
    from template_catalog import completeness
    assert "from cdpm" not in inspect.getsource(completeness)
    assert "import cdpm" not in inspect.getsource(completeness)


@pytest.mark.parametrize("bad", [None, {}, {"id": "example", "params": "wrong"}])
def test_missing_or_malformed_record_is_not_complete(bad):
    from template_catalog.completeness import family_completeness
    got = family_completeness(bad, [])
    assert not got["complete"] and got["problems"]


def test_family_without_parameters_has_explicit_all_identity():
    from template_catalog.completeness import family_completeness
    got = family_completeness({"id": "parameterless", "params": []}, [])
    assert got["complete"] and got["set_id"] == "all" and not got["missing"]


def test_cdpm_missing_reader_reaches_the_shared_calculation(monkeypatch):
    from cdpm import family_params as fp
    from template_catalog import completeness
    monkeypatch.setattr(fp._bridge, "family_records", lambda: {"example": record()})
    called = []
    original = completeness.family_completeness
    def traced(*args, **kwargs):
        called.append(copy.deepcopy((args, kwargs)))
        return original(*args, **kwargs)
    monkeypatch.setattr(completeness, "family_completeness", traced)
    assert fp.missing_params("example", ["b"]) == ["c"]
    assert len(called) == 1 and called[0][0][0]["id"] == "example"


@pytest.mark.parametrize("field", ["params", "param_sets"])
@pytest.mark.parametrize("bad", [False, 0, "", {}])
def test_falsey_wrong_shape_does_not_become_an_empty_list(field, bad):
    from template_catalog.completeness import family_completeness
    rec = record()
    rec[field] = bad
    got = family_completeness(rec, ["a", "b", "c"])
    assert got["problems"] and not got["complete"]


def test_real_cdpm_pending_keeps_coverage_but_never_complete(tmp_path, monkeypatch):
    from cdpm import family_params as fp
    from test_k01_param_set_reference import pair, pinned_doc, unchanged, FAMILY
    _, old, new = pair(tmp_path, monkeypatch, unchanged)
    doc = pinned_doc(old, new, monkeypatch)
    fit = doc["rules"][-1]["template_fit"]
    healthy = fp.family_completeness(doc)[FAMILY]
    assert healthy["verdict"] == fp.VERDICT_COMPLETE
    fit["params"]["min_days_off"]["pending_revision"] = {"question_id": "Q-REV"}
    before = copy.deepcopy(doc)
    result = fp.family_completeness(doc)[FAMILY]
    assert result["given"] == healthy["given"] and result["missing"] == []
    assert result["pending"] == ["min_days_off"]
    assert result["verdict"] == fp.VERDICT_INCOMPLETE
    assert any(p.get("pending_revision") for p in result["problems"])
    assert not any(p.get("proven") or p.get("reland") for p in result["problems"])
    assert doc == before


def test_fully_covered_set_precedes_a_larger_intersection():
    from template_catalog.completeness import family_completeness
    rec = {"id": "choice", "params": [{"name": n} for n in "abcdef"],
           "param_sets": [{"id": "large", "params": list("bcdef")},
                          {"id": "small", "params": ["a"]}]}
    got = family_completeness(rec, list("abc"))
    assert got["set_id"] == "small" and got["complete"]


def test_intersection_precedes_absolute_missing_count():
    from template_catalog.completeness import family_completeness
    rec = {"id": "choice", "params": [{"name": n} for n in "abcdef"],
           "param_sets": [{"id": "small", "params": ["a"]},
                          {"id": "large", "params": list("bcdef")}]}
    got = family_completeness(rec, ["b"], ["c"])
    assert got["set_id"] == "large" and got["missing"] == list("def")


def test_equal_overlap_and_missing_keep_catalogue_order():
    from template_catalog.completeness import family_completeness
    rec = {"id": "choice", "params": [{"name": n} for n in "abcd"],
           "param_sets": [{"id": "first", "params": list("ab")},
                          {"id": "second", "params": list("cd")}]}
    got = family_completeness(rec, ["a", "c"])
    assert got["set_id"] == "first" and got["missing"] == ["b"]


def test_real_catalogue_without_declared_sets_uses_all(tmp_path, monkeypatch):
    from cdpm import family_params as fp
    from test_k01_param_set_reference import pair, pinned_doc, record as family_record, FAMILY
    def remove_sets(docs):
        family_record(docs)["param_sets"] = []
    _, _old, current = pair(tmp_path, monkeypatch, remove_sets)
    doc = pinned_doc(current, current, monkeypatch)
    fit = doc["rules"][-1]["template_fit"]
    assert fit["family_contract"]["param_set"] == "all"
    got = fp.family_completeness(doc)[FAMILY]
    assert got["verdict"] == fp.VERDICT_INCOMPLETE
    assert set(got["missing"]) == {"min_days_off_in_window", "days_off_window_days"}
    assert len(fp.ensure_family_param_questions(doc)) == 2


def test_actual_revision_api_reaches_pending_completeness_without_closing_it():
    from cdpm import family_params as fp, model as m
    from test_cdpm_k04_revision_barrier import _doc_after_conflicting_revision, FAMILY, PARAM, REV_Q
    doc, _rule = _doc_after_conflicting_revision()
    q = next(q for q in doc["questions"] if q["id"] == REV_Q)
    assert m.is_open_question(q) and m.pending_revision_facts(doc)
    before = copy.deepcopy(doc)
    got = fp.family_completeness(doc)[FAMILY]
    assert PARAM in got["given"] and got["missing"] == []
    assert got["pending"] == [PARAM] and got["verdict"] == fp.VERDICT_INCOMPLETE
    assert doc == before
    fp.ensure_family_param_questions(doc)
    assert m.is_open_question(q) and m.pending_revision_facts(doc)
