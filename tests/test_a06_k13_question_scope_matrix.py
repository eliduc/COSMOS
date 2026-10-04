"""Question scope reaches the real readiness matrix; no service or solver."""
import ast
import copy
import os
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb, model as m, question_editions as qe


@pytest.fixture(scope="module", autouse=True)
def optional_old_matrix_probe():
    """Causal probe: exact parent function in memory, no checkout writes."""
    if not os.environ.get("CDPM_K13_OLD_MATRIX"):
        yield
        return
    parent = Path(__file__).resolve().parents[2] / "wt-gpt-audit-a06-z33-94e26606/cdpm/model.py"
    tree = ast.parse(parent.read_text(encoding="utf-8-sig"))
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "readiness_matrix"]
    assert len(funcs) == 1
    assert "resolved = [rules_by_id[rid] for rid in refs if rid in rules_by_id]" in ast.unparse(funcs[0])
    namespace = {}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), "<K13-parent94-matrix>", "exec"), m.__dict__, namespace)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(m, "readiness_matrix", namespace["readiness_matrix"])
        print("K13_OLD_MATRIX: exact parent94 function, other helpers remain current")
        yield


@pytest.fixture(autouse=True)
def real_catalogue(monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(Path(__file__).resolve().parents[1]
                                        / "catalog-integration/0.7.0-draft.2"))
    cb.forget()
    yield
    cb.forget()


def document(zone="ROSTERING", kind="DEF"):
    return {"rules": [{"id": "R", "zone": zone, "kind": kind, "weight": 1,
                       "blockers": [], "title": "Rule", "statement": "Scope"}],
            "questions": [{"id": "Q", "part": "2", "text": "Which value?",
                           "title": "Scope", "rule_refs": ["R"]}],
            "objectives": []}


@pytest.mark.parametrize("zone,tasks", [("PAIRING", ("pairing",)),
    ("ROSTERING", ("rostering",)), ("BOTH", ("pairing", "rostering"))])
@pytest.mark.parametrize("kind", ["DEF", "SOFT"])
def test_whole_known_scope_preserves_task_and_soft_modes(zone, tasks, kind):
    doc = document(zone, kind)
    q = doc["questions"][0]
    assert m.question_tasks(doc, q) == tasks
    assert m.question_rules(doc, q) == doc["rules"]
    matrix = m.readiness_matrix(doc)
    for task in ("pairing", "rostering"):
        assert matrix[task]["feasibility-hard"]["ready"] is (task not in tasks or kind == "SOFT")
        assert matrix[task]["feasibility-soft"]["ready"] is (task not in tasks)
    q["answer"] = {"status": "применён"}
    assert m.open_questions(doc) == []
    assert all(m.readiness_matrix(doc)[t]["feasibility-soft"]["ready"] for t in ("pairing", "rostering"))


@pytest.mark.parametrize("bad", ["missing", "empty", "string", "dangling",
    "mixed_dangling", "mixed_nonstring", "zone", "duplicate_id"])
def test_unknown_scope_blocks_both_real_matrix_cells(bad):
    doc = document()
    q, rule = doc["questions"][0], doc["rules"][0]
    if bad == "missing":
        q.pop("rule_refs")
    elif bad == "empty":
        q["rule_refs"] = []
    elif bad == "string":
        q["rule_refs"] = "R"
    elif bad == "dangling":
        q["rule_refs"] = ["absent"]
    elif bad == "mixed_dangling":
        q["rule_refs"].append("absent")
    elif bad == "mixed_nonstring":
        q["rule_refs"].append(3)
    elif bad == "zone":
        rule["zone"] = "unknown"
    else:
        doc["rules"].append(copy.deepcopy(rule))
    assert m.question_rules(doc, q) is None
    assert m.question_tasks(doc, q) == ("pairing", "rostering")
    matrix = m.readiness_matrix(doc)
    for task in ("pairing", "rostering"):
        cell = matrix[task]["feasibility-hard"]
        assert not cell["ready"]
        assert cell["reasons"]["открытые вопросы части 2 без привязки к строкам"] == 1


@pytest.mark.parametrize("bad", ["missing_parent", "fingerprint", "duplicate_question"])
def test_invalid_special_edition_cannot_be_scoped_away(bad):
    q = {"id": "Q-3b-GOAL", "part": "3b", "title": "Goal", "text": "Choose",
         "rule_refs": [], "answer_format": {"type": "free"}}
    doc = {"rules": [], "questions": [q], "objectives": []}
    qe.freeze(q)
    new = qe.expand(doc, q, [], content={"text": "Choose updated goal"})
    assert m.question_tasks(doc, new) == ("rostering",)
    if bad == "missing_parent":
        doc["questions"].remove(q)
    elif bad == "fingerprint":
        new["edition"]["fingerprint"] = "forged"
    else:
        doc["questions"].append(copy.deepcopy(q))
    assert m.question_tasks(doc, new) == ("pairing", "rostering")
