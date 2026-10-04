"""K13: real button question gate, bounded before export/solver effects.

Only external transport and the next stage boundary are replaced. The model
reader, active-question selection, scope and waiting delivery are production.
Reaching a checkpoint is NOT package acceptance or solver certification.
"""
import ast
import copy
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import textwrap
import zipfile

import pytest

from cdpm import catalog_bridge as cb, export_task_package as tp, model as m
from cdpm.gates import gate_task_package as package_gate
from cdpm import question_editions as qe
from vmp.run import button


class BeforeNextStage(BaseException):
    """Explicit test boundary; never converted into a product verdict."""


class HealthOnlyTransport:
    def __init__(self):
        self.calls = []

    def health(self):
        self.calls.append("health")
        return True

    def __getattr__(self, name):
        raise AssertionError("unreviewed solver transport access: " + name)


@pytest.fixture(scope="module", autouse=True)
def optional_causal_mutation():
    mode = os.environ.get("CDPM_K13_MUTANT")
    if not mode:
        yield
        return
    assert mode in {"old_naked_gate", "no_task_filter", "skip_bad_refs"}
    module = m if mode == "skip_bad_refs" else button
    name = "question_rules" if mode == "skip_bad_refs" else "orchestrate"
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(module, name))))
    if mode == "skip_bad_refs":
        # One semantic mutation: use only the valid subset of references.
        # Empty/invalid containers and a wholly unresolvable scope still fail
        # closed; the mixed-carrier witness must reach the subset weakness.
        loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)]
        assert len(loops) == 1
        guards = [n for n in loops[0].body if isinstance(n, ast.If)]
        assert len(guards) == 2
        for guard in guards:
            assert len(guard.body) == 1 and isinstance(guard.body[0], ast.Return)
            assert isinstance(guard.body[0].value, ast.Constant) and guard.body[0].value.value is None
            guard.body = [ast.Continue()]
        final = tree.body[0].body[-1]
        assert isinstance(final, ast.Return) and ast.unparse(final.value) == "resolved"
        final.value = ast.parse("resolved or None", mode="eval").body
    else:
        targets = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "open_qs" for t in n.targets)]
        assert len(targets) == 1
        value = targets[0].value
        assert isinstance(value, ast.ListComp) and len(value.generators) == 1
        assert ast.unparse(value.generators[0].iter) == "m.open_questions(doc)"
        assert [ast.unparse(test) for test in value.generators[0].ifs] == ["task in m.question_tasks(doc, q)"]
        if mode == "old_naked_gate":
            targets[0].value = ast.parse(
                '[q for q in doc.get("questions", []) if m.is_open_question(q)]', mode="eval").body
        else:
            value.generators[0].ifs = []
    ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, "<K13-" + mode + ">", "exec"), module.__dict__, namespace)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module, name, namespace[name])
        print("K13_MUTANT: " + mode + "; real document reader and downstream delivery retained")
        yield


@pytest.fixture(autouse=True)
def real_catalogue(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv(cb.ENV_CATALOG, str(root / "catalog-integration/0.7.0-draft.2"))
    cb.forget()
    try:
        yield
    finally:
        cb.forget()


@pytest.fixture(params=["extract", "package_path"])
def source(request):
    return request.param


def question(qid="Q-scope", refs=None):
    return {"id": qid, "part": "2", "title": "Addressed question",
            "text": "Which parameter?", "rule_refs": ["R-scope"] if refs is None else refs}


def scoped_rule(zone="ROSTERING", rid="R-scope"):
    return {"id": rid, "zone": zone, "kind": "HARD", "blockers": [],
            "template_fit": {"category": "A", "family": "connection_windows"}}


def document(zone="ROSTERING"):
    return {"rules": [scoped_rule(zone)], "questions": [question()], "objectives": []}


def run_question_gate(tmp_path, monkeypatch, source, doc, task, expected_ids):
    """None expects next-stage checkpoint; a list expects real waiting output."""
    run = tmp_path / "run"
    run.mkdir()
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    transport = HealthOnlyTransport()
    reached = []

    def before_export(path, *args, **kwargs):
        assert Path(path) == run
        reached.append("before_export")
        raise BeforeNextStage("before_export")

    def before_package_gate(path, *args, **kwargs):
        assert Path(path).parent == run / ("button" if task == "rostering" else "button-" + task)
        with zipfile.ZipFile(path) as archive:
            assert json.loads(archive.read("model.json")) == doc
        reached.append("before_package_gate")
        raise BeforeNextStage("before_package_gate")

    # These stubs stop execution; neither returns PASS/allowed/ready.
    monkeypatch.setattr(tp, "export_zip", before_export)
    monkeypatch.setattr(package_gate, "check", before_package_gate)
    kwargs = {}
    if source == "extract":
        extract = run / "extract"
        extract.mkdir()
        (extract / "model.json").write_bytes(raw)
        expected_boundary = "before_export"
    else:
        package = tmp_path / "input.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("model.json", raw)
        kwargs["package_path"] = package
        expected_boundary = "before_package_gate"
    out = run / ("button" if task == "rostering" else "button-" + task)
    try:
        if expected_ids is None:
            with pytest.raises(BeforeNextStage, match=expected_boundary):
                button.orchestrate(run, transport, task=task, **kwargs)
            assert reached == [expected_boundary]
            assert not (out / "delivery.json").exists()
        else:
            result = button.orchestrate(run, transport, task=task, **kwargs)
            assert result["verdict"] == "ожидает ответа человека"
            assert [q["id"] for q in result["questions"]] == expected_ids
            assert json.loads((out / "delivery.json").read_text(encoding="utf-8")) == result
            assert reached == []
            assert not (out / "package.zip").exists()
        journal = json.loads((out / "log.json").read_text(encoding="utf-8"))
        assert all(row["step"] in {"Ш-0", "Ш-1"} for row in journal)
        assert not any(row["step"] == "Ш-1" and row["status"] == "пройден" for row in journal)
    finally:
        assert transport.calls == ["health"]


@pytest.mark.parametrize("task,zone", [("pairing", "ROSTERING"), ("rostering", "PAIRING")])
def test_cross_zone_open_question_reaches_next_stage(tmp_path, monkeypatch, source, task, zone):
    run_question_gate(tmp_path, monkeypatch, source, document(zone), task, None)


@pytest.mark.parametrize("task,zone", [("pairing", "PAIRING"), ("rostering", "ROSTERING"),
                                       ("pairing", "BOTH"), ("rostering", "BOTH")])
def test_own_zone_and_both_wait_for_real_answer(tmp_path, monkeypatch, source, task, zone):
    run_question_gate(tmp_path, monkeypatch, source, document(zone), task, ["Q-scope"])


@pytest.mark.parametrize("bad", ["missing_refs", "empty_refs", "wrong_refs_type", "dangling",
                                 "unknown_zone", "missing_zone", "mixed_dangling", "mixed_nonstring"])
@pytest.mark.parametrize("task", ["pairing", "rostering"])
def test_unknown_scope_cannot_disappear(tmp_path, monkeypatch, source, bad, task):
    doc = document("ROSTERING" if task == "pairing" else "PAIRING")
    q, rule = doc["questions"][0], doc["rules"][0]
    if bad == "missing_refs":
        q.pop("rule_refs")
    elif bad == "empty_refs":
        q["rule_refs"] = []
    elif bad == "wrong_refs_type":
        q["rule_refs"] = "R-scope"
    elif bad == "dangling":
        q["rule_refs"] = ["R-absent"]
    elif bad == "unknown_zone":
        rule["zone"] = "UNRECOGNIZED"
    elif bad == "missing_zone":
        rule.pop("zone")
    elif bad == "mixed_dangling":
        q["rule_refs"].append("R-absent")
    else:
        q["rule_refs"].append(17)
    run_question_gate(tmp_path, monkeypatch, source, doc, task, ["Q-scope"])


@pytest.mark.parametrize("status", ["применён", "не разобран"])
def test_answer_status_preserves_real_waiting_decision(tmp_path, monkeypatch, source, status):
    doc = document("PAIRING")
    doc["questions"][0]["answer"] = {"status": status, "raw": "45 минут"}
    ids = None if status == "применён" else ["Q-scope"]
    run_question_gate(tmp_path, monkeypatch, source, doc, "pairing", ids)


@pytest.mark.parametrize("stale", [False, True])
def test_code_answer_is_judged_against_current_document(tmp_path, monkeypatch, source, stale):
    doc = document("PAIRING")
    q = doc["questions"][0]
    q["code_resolutions"] = [{"rule": "R-scope", "family": "connection_windows",
                              "kind": "HARD", "reason": "Implemented", "revision": 1}]
    if stale:
        doc["rules"][0]["blockers"] = [q["id"]]
    # Genuine difference between shape-complete receipt and current evidence.
    assert m.is_open_question(q) is False
    assert m.is_open_question(q, doc=doc) is stale
    run_question_gate(tmp_path, monkeypatch, source, doc, "pairing", [q["id"]] if stale else None)


def test_superseded_open_edition_is_not_actionable(tmp_path, monkeypatch, source):
    doc = document("PAIRING")
    newer = copy.deepcopy(doc["questions"][0])
    newer.update(id="Q-scope-v2", edition_of="Q-scope", answer={"status": "применён"})
    doc["questions"].append(newer)
    assert m.open_questions(doc) == []
    run_question_gate(tmp_path, monkeypatch, source, doc, "pairing", None)


@pytest.mark.parametrize("task", ["pairing", "rostering"])
def test_actual_397e_model_obeys_its_task_scope(tmp_path, monkeypatch, source, task):
    evidence = Path(__file__).resolve().parents[2] / "cdpm-audit-20260911/exchange-COSMOS"
    package = evidence / "k11-zhivyom-3410-20260919/priyom-n0b-3410/package.zip"
    raw_zip = package.read_bytes()
    assert hashlib.sha256(raw_zip).hexdigest() == "397e7209a6164536c252230c71fc119528a3269e1ffd82eca3d9becd3b5f7bfb"
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        raw = archive.read("model.json")
        manifest = json.loads(archive.read("manifest.json"))
    assert hashlib.sha256(raw).hexdigest() == "a49d3bb951a0e4ed657f056ec99dabd2f6c755594324438afdb289d8fb267821"
    doc = json.loads(raw)
    qid = "Q-3b-параметр-rest_floors-min_rest_outstation"
    assert [q["id"] for q in m.open_questions(doc)] == [qid]
    assert manifest["allowed_modes"]["pairing"]
    assert not manifest["allowed_modes"]["rostering"]
    # K14 corrects the historical K13 oracle: the archived manifest still
    # says pairing allowed, but rest_floors declares a pairing builder too.
    # Its missing parameter must stop both tasks; archive bytes stay intact.
    run_question_gate(tmp_path, monkeypatch, source, doc, task, [qid])


@pytest.mark.parametrize("root_id,edition,owner", [
    ("Q-3a-GOAL", False, "pairing"),
    ("Q-3b-GOAL", True, "rostering"),
    (m.CRITERION_QUESTION_ID, False, "pairing"),
    (m.CRITERION_QUESTION_ID, True, "pairing"),
])
@pytest.mark.parametrize("task", ["pairing", "rostering"])
def test_special_goal_and_criterion_scope(tmp_path, monkeypatch, source, root_id, edition, owner, task):
    # Special questions have no ordinary rule_refs; their established ID
    # contract defines the task. A real edition is made by the product writer.
    q = question(root_id, refs=[])
    q.update(part="3a" if owner == "pairing" else "3b", answer_format={"type": "free"})
    doc = {"rules": [], "questions": [q], "objectives": []}
    if edition:
        qe.freeze(q)
        q = qe.expand(doc, q, [], content={"text": "Updated criterion question"})
        assert qe.lineage_root(doc, q) == root_id
    run_question_gate(tmp_path, monkeypatch, source, doc, task, [q["id"]] if task == owner else None)


@pytest.mark.parametrize("zone,part,wait", [("ROSTERING", "3a", False), ("PAIRING", "3b", True)])
def test_ordinary_refs_not_part_label_define_scope(tmp_path, monkeypatch, source, zone, part, wait):
    doc = document(zone)
    doc["questions"][0]["part"] = part
    run_question_gate(tmp_path, monkeypatch, source, doc, "pairing", ["Q-scope"] if wait else None)
