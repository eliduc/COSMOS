"""Schema diagnostics retain the JSON path and add human rule/parameter names."""
import copy

import pytest

from cdpm import model as m
from test_cdpm_task_package import _ready_doc
from test_cdpm_a05_code_projection import rule


def _doc():
    doc = _ready_doc()
    r = rule("fleet_type", "Airbus A320")
    r["id"] = "R-SECOND"
    r.pop("template_fit")
    r["params"] = [
        {"name": "fleet_types", "unit": "code_list", "value": "Airbus A320",
         "relation": "eq", "as_written": "Airbus A320"},
        {"name": "fleet_type", "unit": "code", "value": "Airbus A320",
         "relation": "eq", "as_written": "Airbus A320"},
    ]
    doc["rules"].append(r)
    return doc


@pytest.mark.parametrize("field,bad", [
    ("relation", ""), ("relation", "bogus"), ("value", 737),
    ("value", ""), ("value", "   "), ("unit", "bogus"),
])
def test_schema_error_names_exact_rule_and_parameter_and_preserves_path(field, bad):
    doc = _doc()
    index = len(doc["rules"]) - 1
    doc["rules"][index]["params"][1][field] = bad
    before = copy.deepcopy(doc)
    problems = m.validate_schema(doc)
    located = [p for p in problems if f"rules/{index}/params/1/{field}:" in p]
    assert located
    assert all("R-SECOND" in p and "fleet_type" in p for p in located)
    assert doc == before


@pytest.mark.parametrize("broken", [
    {"rules": None}, {"rules": [None]}, {"rules": ["bad"]},
    {"rules": [{"id": None, "params": [None]}]},
    {"rules": [{"id": [], "params": [{"name": {}}]}]}, [], None,
])
def test_schema_diagnostics_still_handle_malformed_containers(broken):
    assert m.validate_schema(broken)


def test_schema_diagnostics_do_not_add_errors_to_valid_input():
    doc = _doc()
    before = copy.deepcopy(doc)
    assert m.validate_schema(doc) == []
    assert doc == before


@pytest.mark.parametrize("index,base", [(0, "PLY"), (1, "SVG"), (2, "GRT")])
def test_schema_error_names_base_when_several_records_share_parameter_name(index, base):
    doc = _doc()
    r = doc["rules"][-1]
    r["params"] = [
        {"name": "reserve_crews", "unit": "count", "value": value,
         "relation": "min", "as_written": "Резерв экипажей", "qualifier": code}
        for code, value in [("PLY", 2), ("SVG", 1), ("GRT", 0)]
    ]
    assert m.validate_schema(doc) == []
    r["params"][index]["value"] = "не число"
    problems = m.validate_schema(doc)
    assert problems and all(base in p and "reserve_crews" in p and "R-SECOND" in p
                            for p in problems)


@pytest.mark.parametrize("count", [1, 4, 10])
def test_answer_cli_does_not_silently_hide_schema_failures(count, monkeypatch, capsys, tmp_path):
    from types import SimpleNamespace
    from cdpm import answers
    doc = _doc()
    r = doc["rules"][-1]
    r["params"] = [{"name": "reserve_crews", "unit": "count", "value": 1,
        "relation": "min", "as_written": "Резерв", "qualifier": f"BASE-{i}"}
        for i in range(count)]
    assert m.validate_schema(doc) == []  # Each mutation reaches the intended validator.
    for p in r["params"]:
        p["value"] = "не число"
    failures = m.validate_schema(doc) + m.validate_model(doc)
    assert failures
    monkeypatch.setattr(m, "load_model", lambda path: doc)
    monkeypatch.setattr(answers, "read_book", lambda *a: SimpleNamespace(
        answers={"Q": "тест"}, problems=[], refused={}))
    monkeypatch.setattr(answers.llm_mod, "cosmos_llm", lambda **kw: None)
    monkeypatch.setattr(answers, "apply_answers", lambda *a, **kw: {})
    monkeypatch.setattr(answers, "apply_worker_patches", lambda *a, **kw: {})
    assert answers.main(["unused.json", "unused.xlsx", "--out-dir", str(tmp_path)]) == 1
    printed = capsys.readouterr().out
    assert all(f"НАРУШЕНИЕ: {problem}" in printed for problem in failures)
    assert all(f"BASE-{i}" in printed for i in range(count))
