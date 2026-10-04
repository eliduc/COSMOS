"""Protocol-only synthetic evidence tests, NEVER actual Kotlin/support proof.

Temporary source/class bytes exercise identity validation, not a JVM runtime.
No rows from these fixtures may be published as actual stand evidence.
"""
import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from vmp.stand_components import load_evidence, reference, load_reference
from vmp import stand_tasks as st


TASK = "st-duty-1"
RECORD = "families/duty_limits"
KIND = "kotlin_knob"
PROBE_CLASS = "org.acme.flighcrewscheduling.solver.A06TypedComponentProbe"
PROBE_SOURCE = "kotlin-solver/src/test/kotlin/org/acme/flighcrewscheduling/solver/A06TypedComponentProbe.kt"
PROBE_BINARY = "kotlin-solver/target/test-classes/org/acme/flighcrewscheduling/solver/A06TypedComponentProbe.class"
MAIN_SOURCE = "kotlin-solver/src/main/kotlin/org/acme/flighcrewscheduling/solver/TaskPackage.kt"
MAIN_BINARY = "kotlin-solver/target/classes/org/acme/flighcrewscheduling/solver/TaskPackage.class"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True), encoding="utf-8")


class ProtocolCatalog:
    """Minimal protocol fixture, not an approved/released catalogue."""
    def __init__(self, world, expected):
        self.record = {"id": "duty_limits", "status": "есть", "executor_kinds": [KIND],
            "stand_tasks": [{"id": TASK, "world": copy.deepcopy(world),
                             "expected": {KIND: copy.deepcopy(expected)}}]}

    def version(self): return "0.7.0-draft.5"
    def fingerprint(self): return "e9cb16948ac5c8df07bf09f3a1ddfbe745e39798c797c26bdb4dc87197343aa1"
    def families_full(self, **kwargs): return [copy.deepcopy(self.record)]
    def criteria(self, **kwargs): return {}


@pytest.fixture
def case(tmp_path):
    repo = tmp_path / "protocol-only-repo"
    inputs = tmp_path / "protocol-only-inputs"
    task_dir = inputs / TASK
    task_dir.mkdir(parents=True)
    world = {"form": "schedule", "params": {"max_duty_duration": 780},
             "crews": [], "flights": [], "history": None, "horizon": None}
    expected = {"param": "max_duty_duration", "threshold": "max_duty_span_min",
                "unit": "duration_minutes", "relation": "max", "value": 780,
                "provider_value": 780}
    cat = ProtocolCatalog(world, expected)
    raw_files = {
        "kotlin-solver/pom.xml": b"<project><!-- protocol fixture, not buildable --></project>",
        MAIN_SOURCE: b"// synthetic source identity fixture only\n",
        PROBE_SOURCE: b"// synthetic probe identity fixture only\n",
        MAIN_BINARY: b"synthetic bytes, NOT a JVM class",
        PROBE_BINARY: b"synthetic probe bytes, NOT runtime evidence",
    }
    for name, raw in raw_files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("model.json", '{"rules":[]}')
        z.writestr("manifest.json", '{"protocol_only":true}')
    (task_dir / (TASK + ".zip")).write_bytes(buffer.getvalue())
    write_json(task_dir / (TASK + ".world.json"), {"world": world, "expected": {KIND: expected}})
    write_json(task_dir / (TASK + ".problem.json"), {"employees": [], "flights": []})
    files = {TASK + suffix: sha((task_dir / (TASK + suffix)).read_bytes())
             for suffix in (".zip", ".world.json", ".problem.json")}
    write_json(task_dir / "identity.json", {"catalog_version": cat.version(),
        "catalog_fingerprint": cat.fingerprint(), "task": TASK, "files": files})
    doc = {"schema": "a06-kotlin-component/1", "scope": "component-only",
        "catalog": {"version": cat.version(), "fingerprint": cat.fingerprint()},
        "producer": {"class": PROBE_CLASS, "commit": "f96e0e12e93e07fa037e1a39d8c6371a8667e679",
                     "java_version": "21.0.8"},
        "sources": {name: sha(raw) for name, raw in raw_files.items() if name.endswith((".xml", ".kt"))},
        "artifacts": {MAIN_BINARY: sha(raw_files[MAIN_BINARY])},
        "probe": {PROBE_BINARY: sha(raw_files[PROBE_BINARY])},
        "inputs": {TASK: {"zip_sha256": files[TASK + ".zip"],
            "world_sha256": files[TASK + ".world.json"], "problem_sha256": files[TASK + ".problem.json"],
            "identity_sha256": sha((task_dir / "identity.json").read_bytes())}},
        "observations": [{"record": RECORD, "task": TASK, "executor_kind": KIND,
                          "got": copy.deepcopy(expected)}],
        "guards": {"inputs_unchanged": True, "sources_unchanged": True, "artifacts_unchanged": True}}
    return {"repo": repo, "input_root": inputs, "task_dir": task_dir, "catalog": cat,
            "world": world, "expected": expected, "doc": doc, "path": tmp_path / "protocol-only-evidence.json"}


def load(case):
    write_json(case["path"], case["doc"])
    return load_evidence(case["path"], catalog=case["catalog"], input_root=case["input_root"], repo=case["repo"])


def adjudicate(case):
    return load(case).judge(KIND, case["expected"], RECORD, TASK, case["world"])


def test_protocol_only_complete_identity_and_knob_match(case):
    before = copy.deepcopy(case["doc"])
    assert adjudicate(case) == case["expected"]
    assert case["doc"] == before
    assert "approved" not in case["doc"] and "stand_verified" not in case["doc"]


def test_import_uses_real_catalogue_reader_api_not_an_invented_full_criteria_method(case):
    import template_catalog as tc
    actual = tc.load(Path(__file__).resolve().parents[1] / "catalog-integration/0.7.0-draft.6")
    task = next(t for t in actual.family("duty_limits")["stand_tasks"] if t["id"] == TASK)
    case["catalog"] = actual
    case["world"] = task["world"]
    case["expected"] = task["expected"][KIND]
    case["doc"]["catalog"] = {"version": actual.version(), "fingerprint": actual.fingerprint()}
    world_path = case["task_dir"] / (TASK + ".world.json")
    write_json(world_path, {"world": task["world"], "expected": task["expected"]})
    identity_path = case["task_dir"] / "identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity.update(catalog_version=actual.version(), catalog_fingerprint=actual.fingerprint())
    identity["files"][world_path.name] = sha(world_path.read_bytes())
    write_json(identity_path, identity)
    case["doc"]["inputs"][TASK]["world_sha256"] = sha(world_path.read_bytes())
    case["doc"]["inputs"][TASK]["identity_sha256"] = sha(identity_path.read_bytes())
    assert adjudicate(case) == task["expected"][KIND]


@pytest.mark.parametrize("field,value", [("schema", "unknown/1"), ("scope", "support-approved"),
    ("catalog", {"version": "0.7.0-draft.4", "fingerprint": "a" * 64})])
def test_wrong_envelope_identity_refused(case, field, value):
    case["doc"][field] = value
    with pytest.raises(ValueError): load(case)


@pytest.mark.parametrize("field,value", [("class", "OtherProbe"), ("commit", "f96e0e12"),
    ("commit", "z" * 40), ("java_version", ""), ("java_version", None)])
def test_producer_identity_must_be_explicit(case, field, value):
    case["doc"]["producer"][field] = value
    with pytest.raises(ValueError): load(case)


@pytest.mark.parametrize("mapping", ["sources", "artifacts", "probe"])
@pytest.mark.parametrize("damage", ["empty", "missing", "extra", "wrong_sha", "changed_file", "new_file"])
def test_full_census_and_raw_hashes_are_checked_both_directions(case, mapping, damage):
    values = case["doc"][mapping]
    name = next(iter(values))
    if damage == "empty": case["doc"][mapping] = {}
    elif damage == "missing": del values[name]
    elif damage == "extra": values["unrelated.bin"] = "0" * 64
    elif damage == "wrong_sha": values[name] = "0" * 64
    elif damage == "changed_file": (case["repo"] / name).write_bytes(b"changed after execution")
    else:
        extra = {"sources": MAIN_SOURCE.replace("TaskPackage.kt", "Additional.kt"),
                 "artifacts": MAIN_BINARY.replace("TaskPackage.class", "Additional.class"),
                 "probe": PROBE_BINARY.replace(".class", "$Nested.class")}[mapping]
        (case["repo"] / extra).write_bytes(b"new file absent from claimed census")
    with pytest.raises(ValueError): load(case)


@pytest.mark.parametrize("suffix", [".zip", ".world.json", ".problem.json"])
def test_changed_input_bytes_refused(case, suffix):
    path = case["task_dir"] / (TASK + suffix)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError): load(case)


@pytest.mark.parametrize("field", ["zip_sha256", "world_sha256", "problem_sha256", "identity_sha256"])
def test_wrong_input_digest_refused(case, field):
    case["doc"]["inputs"][TASK][field] = "0" * 64
    with pytest.raises(ValueError): load(case)


def resign_input(case, suffix, doc):
    path = case["task_dir"] / (TASK + suffix)
    write_json(path, doc)
    digest = sha(path.read_bytes())
    field = {".zip": "zip_sha256", ".world.json": "world_sha256", ".problem.json": "problem_sha256"}[suffix]
    case["doc"]["inputs"][TASK][field] = digest
    identity_path = case["task_dir"] / "identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["files"][path.name] = digest
    write_json(identity_path, identity)
    case["doc"]["inputs"][TASK]["identity_sha256"] = sha(identity_path.read_bytes())


@pytest.mark.parametrize("field", ["world", "expected"])
def test_rehashed_input_must_still_equal_actual_catalogue_task(case, field):
    path = case["task_dir"] / (TASK + ".world.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if field == "world": doc["world"]["params"]["max_duty_duration"] = 900
    else: doc["expected"][KIND]["value"] = 900
    resign_input(case, ".world.json", doc)
    with pytest.raises(ValueError): load(case)


@pytest.mark.parametrize("damage", ["catalog_version", "catalog_fingerprint", "task", "files", "extra_file"])
def test_rehashed_identity_cannot_claim_other_catalogue_task_or_inventory(case, damage):
    path = case["task_dir"] / "identity.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    if damage == "catalog_version": doc[damage] = "0.7.0-draft.4"
    elif damage == "catalog_fingerprint": doc[damage] = "0" * 64
    elif damage == "task": doc[damage] = "other-task"
    elif damage == "files": doc["files"][TASK + ".zip"] = "0" * 64
    else: doc["files"]["extra.zip"] = "0" * 64
    write_json(path, doc)
    case["doc"]["inputs"][TASK]["identity_sha256"] = sha(path.read_bytes())
    with pytest.raises(ValueError): load(case)


@pytest.mark.parametrize("field,value", [("record", "families/other"), ("task", "other-task"),
    ("executor_kind", "kotlin_form")])
def test_wrong_observation_address_or_executor_kind_refused(case, field, value):
    case["doc"]["observations"][0][field] = value
    with pytest.raises(ValueError): adjudicate(case)


@pytest.mark.parametrize("field,value", [("param", "min_rest_home"), ("threshold", "min_rest_home_min"),
    ("unit", "count"), ("relation", "min"), ("value", 900), ("provider_value", 900),
    ("value", True), ("provider_value", True)])
def test_wrong_knob_observation_never_matches(case, field, value):
    case["doc"]["observations"][0]["got"][field] = value
    with pytest.raises(ValueError): adjudicate(case)


@pytest.mark.parametrize("damage", ["missing_provider", "extra_field", "empty", "duplicate", "missing_observation"])
def test_incomplete_or_ambiguous_observation_refused(case, damage):
    rows = case["doc"]["observations"]
    if damage == "missing_provider": del rows[0]["got"]["provider_value"]
    elif damage == "extra_field": rows[0]["got"]["passed"] = True
    elif damage == "empty": rows[0]["got"] = {}
    elif damage == "duplicate": rows.append(copy.deepcopy(rows[0]))
    else: rows.clear()
    with pytest.raises(ValueError): adjudicate(case)


@pytest.mark.parametrize("guard", ["inputs_unchanged", "sources_unchanged", "artifacts_unchanged"])
@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_guard_declarations_are_strict_but_never_replace_real_hashes(case, guard, value):
    case["doc"]["guards"][guard] = value
    with pytest.raises(ValueError): load(case)


def test_guard_true_does_not_override_stale_artifact(case):
    (case["repo"] / MAIN_BINARY).write_bytes(b"stale despite all declared guards true")
    with pytest.raises(ValueError): load(case)


@pytest.mark.parametrize("damage", ["wrong_world", "wrong_expected", "wrong_task", "wrong_kind", "wrong_record"])
def test_judge_request_must_match_loaded_context(case, damage):
    evidence = load(case)
    kind, expected, record, task, world = KIND, copy.deepcopy(case["expected"]), RECORD, TASK, copy.deepcopy(case["world"])
    if damage == "wrong_world": world["params"]["max_duty_duration"] = 999
    elif damage == "wrong_expected": expected["value"] = 999
    elif damage == "wrong_task": task = "other-task"
    elif damage == "wrong_kind": kind = "kotlin_form"
    else: record = "families/other"
    with pytest.raises(ValueError): evidence.judge(kind, expected, record, task, world)


@pytest.fixture
def form_case(case):
    # Still protocol-only: the task name is an arbitrary declared identity,
    # not a claim that this is the actual catalogue's duty task or JVM score.
    expected = {"form": "calendar_window_penalties",
        "rules": [{"rule_id": "R-STAND-calendar_windows", "action": "penalize",
                   "days": ["thursday"]}],
        "twins": [{"name": "original", "flight_shift_seconds": 0,
                   "constraint": "Calendar window layover penalty", "hard_score": 0,
                   "soft_score": -1, "matches": 1, "package_weight": 1},
                  {"name": "all_flights_plus_one_day", "flight_shift_seconds": 86400,
                   "constraint": "Calendar window layover penalty", "hard_score": 0,
                   "soft_score": 0, "matches": 0, "package_weight": 1}]}
    world = {"form": "schedule", "params": {}, "rule_form": {"days": ["thursday"], "tz": "base-local"},
             "crews": [], "flights": [], "history": None, "horizon": None}
    case["world"], case["expected"] = world, expected
    case["catalog"].record = {"id": "calendar_windows", "status": "есть", "executor_kinds": ["kotlin_form"],
        "stand_tasks": [{"id": TASK, "world": copy.deepcopy(world),
                         "expected": {"kotlin_form": copy.deepcopy(expected)}}]}
    resign_input(case, ".world.json", {"world": world, "expected": {"kotlin_form": expected}})
    case["doc"]["observations"] = [{"record": "families/calendar_windows", "task": TASK,
                                   "executor_kind": "kotlin_form", "got": copy.deepcopy(expected)}]
    return case


def judge_form(case):
    return load(case).judge("kotlin_form", case["expected"], "families/calendar_windows", TASK, case["world"])


def test_protocol_only_calendar_facts_and_both_measured_twins(form_case):
    assert judge_form(form_case) == form_case["expected"]


@pytest.mark.parametrize("damage", ["missing_rules", "empty_rules", "missing_days", "empty_days",
    "unknown_day", "repeated_day", "wrong_day", "missing_action", "wrong_rule", "missing_twins",
    "missing_healthy", "missing_original", "missing_score", "wrong_score", "wrong_healthy_score",
    "wrong_matches", "boolean_score", "float_score", "wrong_weight", "zero_weight", "missing_weight",
    "wrong_shift", "wrong_twin_name", "wrong_constraint", "extra_fact", "extra_score_fact"])
def test_calendar_missing_or_changed_facts_and_twins_refused(form_case, damage):
    got = form_case["doc"]["observations"][0]["got"]
    if damage == "missing_rules": del got["rules"]
    elif damage == "empty_rules": got["rules"] = []
    elif damage == "missing_days": del got["rules"][0]["days"]
    elif damage == "empty_days": got["rules"][0]["days"] = []
    elif damage == "unknown_day": got["rules"][0]["days"] = ["someday"]
    elif damage == "repeated_day": got["rules"][0]["days"] *= 2
    elif damage == "wrong_day": got["rules"][0]["days"] = ["friday"]
    elif damage == "missing_action": del got["rules"][0]["action"]
    elif damage == "wrong_rule": got["rules"][0]["rule_id"] = "another-rule"
    elif damage == "missing_twins": del got["twins"]
    elif damage == "missing_healthy": got["twins"].pop()
    elif damage == "missing_original": got["twins"].pop(0)
    elif damage == "missing_score": del got["twins"][0]["soft_score"]
    elif damage == "wrong_score": got["twins"][0]["soft_score"] = -2
    elif damage == "wrong_healthy_score": got["twins"][1]["soft_score"] = -1
    elif damage == "wrong_matches": got["twins"][0]["matches"] = 2
    elif damage == "boolean_score": got["twins"][0]["hard_score"] = False
    elif damage == "float_score": got["twins"][0]["soft_score"] = -1.0
    elif damage == "wrong_weight": got["twins"][0]["package_weight"] = 2
    elif damage == "zero_weight": got["twins"][0]["package_weight"] = 0
    elif damage == "missing_weight": del got["twins"][0]["package_weight"]
    elif damage == "wrong_shift": got["twins"][1]["flight_shift_seconds"] = 3600
    elif damage == "wrong_twin_name": got["twins"][1]["name"] = "original"
    elif damage == "wrong_constraint": got["twins"][0]["constraint"] = "Another constraint"
    elif damage == "extra_fact": got["rules"][0]["approved"] = True
    else: got["twins"][0]["passed"] = True
    with pytest.raises(ValueError): judge_form(form_case)


def test_reference_rechecks_actual_source_artifact_input_freshness(case):
    assert adjudicate(case) == case["expected"]
    ref = reference(case["path"], case["input_root"])
    loaded = load_reference(ref, catalog=case["catalog"], repo=case["repo"])
    assert loaded.judge(KIND, case["expected"], RECORD, TASK, case["world"]) == case["expected"]
    (case["repo"] / MAIN_SOURCE).write_bytes(b"changed after reference creation")
    with pytest.raises(ValueError): load_reference(ref, catalog=case["catalog"], repo=case["repo"])


def test_reference_rejects_evidence_content_change(case):
    load(case)
    ref = reference(case["path"], case["input_root"])
    case["doc"]["observations"][0]["got"]["value"] = 900
    write_json(case["path"], case["doc"])
    with pytest.raises(ValueError): load_reference(ref, catalog=case["catalog"], repo=case["repo"])


@pytest.mark.parametrize("damage", ["extra_top", "missing_top", "extra_guard", "empty_inputs", "extra_input",
    "extra_observation_key", "extra_producer_key"])
def test_closed_envelope_and_no_unobserved_input(case, damage):
    doc = case["doc"]
    if damage == "extra_top": doc["approved"] = True
    elif damage == "missing_top": del doc["scope"]
    elif damage == "extra_guard": doc["guards"]["pretend"] = True
    elif damage == "empty_inputs": doc["inputs"] = {}
    elif damage == "extra_input": doc["inputs"]["another-task"] = copy.deepcopy(doc["inputs"][TASK])
    elif damage == "extra_observation_key": doc["observations"][0]["result"] = "passed"
    else: doc["producer"]["approved"] = True
    with pytest.raises(ValueError): load(case)


def prepare_stand_case(case):
    # Real file hashing without monkeypatching the census. These deliberately
    # inert bytes belong only to this protocol tmprepo, not executed sources.
    for name in st.CODE_FILES:
        path = case["repo"] / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# synthetic Python identity fixture only\n")
    evidence = load(case)
    rows = st.run_tasks(catalog=case["catalog"], kinds=[KIND, "kotlin_form"], component_evidence=evidence)
    ref = reference(case["path"], case["input_root"])
    doc = st.results_document(rows, case["catalog"], repo=case["repo"],
        commit=case["doc"]["producer"]["commit"], component_reference=ref)
    return rows, doc, ref


@pytest.mark.parametrize("variant", ["knob", "form"])
def test_stock_stand_protocol_component_healthy(request, variant):
    case = request.getfixturevalue("case" if variant == "knob" else "form_case")
    rows, doc, _ = prepare_stand_case(case)
    assert len(rows) == 1 and rows[0]["result"] == "passed"
    assert rows[0]["got"] == case["expected"]
    assert rows[0]["run_id"] is None and rows[0]["package_fingerprint"] is None
    assert st.stale_reasons(doc, case["catalog"], repo=case["repo"]) == []


def test_stock_stand_without_explicit_component_evidence_is_skipped(case):
    rows = st.run_tasks(catalog=case["catalog"], kinds=[KIND])
    assert len(rows) == 1
    assert rows[0]["result"] == "skipped" and rows[0]["got"] is None


def test_stock_stand_missing_measurement_is_failed_not_inherited(case):
    evidence = load(case)
    new_task = copy.deepcopy(case["catalog"].record["stand_tasks"][0])
    new_task["id"] = "not-measured-task"
    case["catalog"].record["stand_tasks"].append(new_task)
    rows = st.run_tasks(catalog=case["catalog"], kinds=[KIND], component_evidence=evidence)
    by_task = {r["task"]: r for r in rows}
    assert by_task[TASK]["result"] == "passed"
    assert by_task["not-measured-task"]["result"] == "failed"
    assert "missing observation" in by_task["not-measured-task"]["note"]


def test_stock_result_cannot_publish_pass_without_primary_reference(case):
    rows, _, _ = prepare_stand_case(case)
    with pytest.raises(st.StandRunError, match="component evidence"):
        st.results_document(rows, case["catalog"], repo=case["repo"],
                            commit=case["doc"]["producer"]["commit"])


@pytest.mark.parametrize("damage", ["wrong_got", "wrong_expected", "wrong_record", "wrong_task",
    "wrong_kind", "duplicate_row", "commit_mismatch", "missing_reference"])
def test_forged_stand_rows_cannot_pass_freshness(case, damage):
    _, doc, _ = prepare_stand_case(case)
    if damage == "wrong_got": doc["results"][0]["got"]["value"] = 999
    elif damage == "wrong_expected": doc["results"][0]["expected"]["value"] = 999
    elif damage == "wrong_record": doc["results"][0]["record"] = "families/other"
    elif damage == "wrong_task": doc["results"][0]["task"] = "another-task"
    elif damage == "wrong_kind": doc["results"][0]["executor_kind"] = "kotlin_form"
    elif damage == "duplicate_row": doc["results"].append(copy.deepcopy(doc["results"][0]))
    elif damage == "commit_mismatch": doc["commit"] = "0" * 40
    else: del doc["component_evidence"]
    assert st.stale_reasons(doc, case["catalog"], repo=case["repo"])


@pytest.mark.parametrize("damage", ["deleted_evidence", "changed_evidence", "deleted_zip",
    "changed_zip", "changed_world", "changed_problem", "changed_identity", "changed_artifact",
    "changed_source", "changed_probe", "changed_python_code"])
def test_stock_freshness_reopens_files_after_result_document(case, damage):
    _, doc, _ = prepare_stand_case(case)
    if damage == "deleted_evidence": case["path"].unlink()
    elif damage == "changed_evidence": case["path"].write_bytes(case["path"].read_bytes() + b" ")
    elif damage == "deleted_zip": (case["task_dir"] / (TASK + ".zip")).unlink()
    else:
        target = {"changed_zip": case["task_dir"] / (TASK + ".zip"),
            "changed_world": case["task_dir"] / (TASK + ".world.json"),
            "changed_problem": case["task_dir"] / (TASK + ".problem.json"),
            "changed_identity": case["task_dir"] / "identity.json",
            "changed_artifact": case["repo"] / MAIN_BINARY,
            "changed_source": case["repo"] / MAIN_SOURCE,
            "changed_probe": case["repo"] / PROBE_BINARY,
            "changed_python_code": case["repo"] / st.CODE_FILES[0]}[damage]
        target.write_bytes(target.read_bytes() + b"changed")
    assert st.stale_reasons(doc, case["catalog"], repo=case["repo"])


def test_result_document_refuses_commit_different_from_primary_measurement(case):
    rows, _, ref = prepare_stand_case(case)
    with pytest.raises(st.StandRunError, match="commit"):
        st.results_document(rows, case["catalog"], repo=case["repo"],
                            commit="0" * 40, component_reference=ref)


@pytest.mark.parametrize("flag", ["--component-evidence", "--component-inputs"])
def test_component_cli_requires_complete_pair_before_any_execution(flag, tmp_path, capsys):
    assert st.main([flag, str(tmp_path / "not-used")]) == 2
    assert "задаются вместе" in capsys.readouterr().out
