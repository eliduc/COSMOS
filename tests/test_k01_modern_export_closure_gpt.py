"""Modern K01 export boundaries, not a catalogue release or stand approval.

Only temporary typed documents and explicitly synthetic component support.
Real completeness, serializers, producer/receiver gates and local Z3 stay real.
The finite reserve translator accepts one exact literal rule, not arbitrary
prose. No network, subprocess, service, JVM, solve or external model is used.
"""
import copy
import hashlib
import json

import pytest

from cdpm import answers, catalog_bridge as cb, family_params as fp, model as m
from cdpm import export_task_package as producer, smt_verify
from cdpm.gates import gate_task_package as gate
from template_catalog import family_contract
from webapp import task_package as receiver
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from test_k06_scope_agent import native_support


REST = "rest_floors"
RESERVE = "reserve_floors"
DUTY = "duty_extension_penalty"
TIME = "time_scale"


def cell(value, unit, relation, quote):
    return {"value": value, "unit": unit, "relation": relation,
            "condition": None, "quote": quote}


@pytest.fixture
def modern(tmp_path, monkeypatch):
    doc, _rule = typed_rule(tmp_path / "catalogue", monkeypatch, REST, {
        "min_rest_home": cell(660, "duration_minutes", "min", "660 минут"),
        "min_rest_outstation": cell(700, "duration_minutes", "min", "700 минут"),
    })
    cat = cb.catalog()
    doc.update(rules=[], objectives=[], questions=[])
    doc["meta"]["fit_contract"] = True
    support_dir = tmp_path / "explicit-synthetic-component-support"
    support_dir.mkdir()
    support = native_support(support_dir, monkeypatch, cat,
                             {("families", f) for f in (REST, RESERVE, DUTY, TIME)})
    (cat.path / "support.json").write_text(json.dumps(support), encoding="utf-8")

    def rule(family, rid, cells, statement):
        record = cat.family(family)
        cells = copy.deepcopy(cells)
        for value in cells.values():
            value["source"] = {"kind": "rule", "rule_id": rid}
        value = {"id": rid, "title": "Explicit isolated K01 component",
                 "kind": record["kind"], "zone": record["zone"],
                 "statement": statement, "sources": [{"sid": "S1", "anchor": "п.1"}],
                 "template_fit": {"category": "A", "schema_version": "landing/2",
                     "family": family, "registry_rev": cat.fingerprint(),
                     "family_contract": family_contract.snapshot(cat, family, cells),
                     "params": cells}}
        if value["kind"] == "SOFT":
            value["weight"] = 3
        return value

    rest = rule(REST, "R-REST", {
        "min_rest_home": cell(660, "duration_minutes", "min", "660 минут"),
        "min_rest_outstation": cell(700, "duration_minutes", "min", "700 минут"),
    }, "Отдых дома не менее 660 минут; вне базы не менее 700 минут.")
    reserve = rule(RESERVE, "R-RESERVE", {
        "reserve_crews": cell({"ST1": 1}, "count", "min", "ST1:1"),
    }, "На базе ST1 не менее 1 резервного экипажа.")
    duty = rule(DUTY, "R-DUTY", {
        "duty_extension_cap": cell(960, "duration_minutes", "max", "960 минут"),
        "long_duty_threshold": cell(720, "duration_minutes", "max", "720 минут"),
        "extension_bases": cell(["ST1", "ST2"], "code_list", "in", "ST1, ST2"),
        "extension_consent": cell(True, "flag", "yes", "с согласия"),
    }, "Продление смены не более 960 минут для ST1, ST2 только с согласия; мягкий порог 720 минут.")
    time = rule(TIME, "R-TIME", {
        "base_tz_offset_min": cell(0, "duration_minutes", "eq", "0 минут"),
    }, "Сдвиг шкалы времени равен 0 минут.")
    for own in (rest, reserve, duty, time):
        trial = copy.deepcopy(doc)
        trial["rules"] = [own]
        assert m.validate_schema(trial) == m.validate_template_fit(trial) == []
        assert fp.family_completeness(trial)[own["template_fit"]["family"]]["missing"] == []
        assert m.support_problems(trial, "1.20") == []
        assert producer._load_modern_package_model(json.dumps(trial).encode())["rules"] == [own]
    return doc, cat, {REST: rest, RESERVE: reserve, DUTY: duty, TIME: time}


def document(modern, *families):
    doc, _cat, rules = modern
    result = copy.deepcopy(doc)
    result["rules"] = [copy.deepcopy(rules[f]) for f in families]
    return result


def source(root, doc):
    path = root / "extract" / "model.json"
    path.parent.mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False, allow_nan=False).encode("utf-8")
    path.write_bytes(raw)
    return path, raw


def export_refusal(root, doc, code):
    before = copy.deepcopy(doc)
    path, raw = source(root, doc)
    with pytest.raises(producer.TaskPackageError) as caught:
        producer.build_package(root, spec_version="1.20")
    assert caught.value.code == code, (caught.value.code, str(caught.value))
    assert path.read_bytes() == raw and doc == before
    return caught.value


def healthy_rest(modern):
    doc = document(modern, REST)
    modes, _ = producer.allowed_modes(doc, "1.20")
    assert all("feasibility-hard" in modes[task] for task in ("pairing", "rostering")), modes
    assert producer._load_modern_package_model(json.dumps(doc).encode())["rules"] == doc["rules"]
    return doc


def test_modern_missing_parameter_blocks_every_mode_without_or_with_question(modern, tmp_path):
    doc = healthy_rest(modern)
    fit = doc["rules"][0]["template_fit"]
    pin = copy.deepcopy(fit["family_contract"])
    del fit["params"]["min_rest_outstation"]
    for phase in ("before-question", "open-question"):
        if phase == "open-question":
            assert len(fp.ensure_family_param_questions(doc)) == 1
        assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
        modes, _ = producer.allowed_modes(doc, "1.20")
        assert modes == {"pairing": [], "rostering": []}
        failure = export_refusal(tmp_path / phase, doc, "family-incomplete")
        assert "min_rest_outstation" in str(failure)
        assert "rest_floors/min_rest_outstation" in failure.items
        assert fit["family_contract"] == pin


@pytest.mark.parametrize("shape", ["typed-empty", "missing", "null", "list", "legacy-empty"])
def test_empty_typed_container_is_completeness_not_malformed_shape(modern, shape):
    doc = document(modern, REST)
    rule = doc["rules"][0]
    fit = rule["template_fit"]
    fit["params"] = {}
    if shape == "missing":
        del fit["params"]
    elif shape == "null":
        fit["params"] = None
    elif shape == "list":
        fit["params"] = []
    elif shape == "legacy-empty":
        fit.pop("schema_version")
        fit.pop("family_contract")
        fit.pop("registry_rev")
    format_problems = m.validate_template_fit(doc)
    candidate_problem = m._fit_shape_reason(fit, rule["statement"])
    if shape == "typed-empty":
        assert format_problems == []
        assert candidate_problem is None
        assert fp.family_completeness(doc)[REST]["missing"] == ["min_rest_home", "min_rest_outstation"]
        assert producer.allowed_modes(doc, "1.20")[0] == {"pairing": [], "rostering": []}
    else:
        assert format_problems and candidate_problem
        assert any("params" in p for p in format_problems)
        assert "params" in candidate_problem


@pytest.mark.parametrize("bad", [None, "", "   ", [], {}, True, False, "abc", [1], {"x": 1}],
                         ids=["null", "empty", "spaces", "list", "map", "true", "false", "word", "list-one", "map-one"])
def test_modern_finite_bad_cell_is_not_given_and_refuses_real_export(modern, tmp_path, bad):
    doc = healthy_rest(modern)
    doc["rules"][0]["template_fit"]["params"]["min_rest_outstation"]["value"] = bad
    got = fp.family_completeness(doc)[REST]
    assert got["missing"] == ["min_rest_outstation"]
    assert "min_rest_outstation" not in got["given"]
    failure = export_refusal(tmp_path / "bad-cell", doc, "landing-incomplete")
    assert "min_rest_outstation" in str(failure), str(failure)
    assert "landing-version" not in str(failure)
    healthy_rest(modern)


@pytest.mark.parametrize("bad", [["x"], "720", 5], ids=["list", "string", "number"])
def test_modern_bad_parameter_container_fails_closed_without_traceback(modern, tmp_path, bad):
    doc = healthy_rest(modern)
    doc["rules"][0]["template_fit"]["params"] = bad
    got = fp.family_completeness(doc)[REST]
    assert got["given"] == []
    assert set(got["missing"]) == {"min_rest_home", "min_rest_outstation"}
    failure = export_refusal(tmp_path / "bad-shape", doc, "landing-incomplete")
    assert "params" in str(failure)
    assert "landing-version" not in str(failure)


def test_conditional_decline_does_not_clear_modern_family_or_export(modern, tmp_path):
    doc = healthy_rest(modern)
    del doc["rules"][0]["template_fit"]["params"]["min_rest_outstation"]
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    answer = "Не применяется для сотрудников базы SVO; в остальных базах 10:00"
    assert not fp.apply_param_answer(doc, q, answer, 2)["applied"]
    got = fp.family_completeness(doc)[REST]
    assert got["declined"] == [] and got["missing"] == ["min_rest_outstation"]
    assert got["conditional"] == {"min_rest_outstation": qid}
    error = export_refusal(tmp_path / "conditional", doc, "family-incomplete")
    assert "отказ С УСЛОВИЕМ" in str(error)
    # Actual answer application is the healthy control, not a manual cell fill.
    applied = answers.apply_answers(doc, {qid: "700 минут"})
    assert applied["applied"] == 1, applied
    assert fp.family_completeness(doc)[REST]["missing"] == []
    assert all("feasibility-hard" in modes for modes in producer.allowed_modes(doc, "1.20")[0].values())


def test_modern_mixed_family_and_question_refusal_names_both_causes(modern, tmp_path):
    doc = document(modern, REST, TIME)
    del doc["rules"][0]["template_fit"]["params"]["min_rest_outstation"]
    doc["questions"] = [{"id": "Q-HARD-TIME", "part": "3a", "title": "Вопрос шкалы",
        "text": "Уточните шкалу.", "answer_format": {"type": "text"},
        "rule_refs": ["R-TIME"], "source_refs": [{"sid": "S1", "anchor": "п.1"}]}]
    doc["rules"][1]["blockers"] = ["Q-HARD-TIME"]
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    error = export_refusal(tmp_path / "mixed-question", doc, "family-incomplete")
    assert "min_rest_outstation" in str(error)
    assert "Прочие основания остановки" in str(error)
    assert "блокированные жёсткие строки (1)" in str(error)


@pytest.mark.parametrize("with_healable", [False, True], ids=["broken-only", "broken-plus-healable"])
def test_modern_broken_record_advice_does_not_promise_answer_cure(modern, tmp_path, monkeypatch, with_healable):
    doc = document(modern, TIME, *([REST] if with_healable else []))
    if with_healable:
        del doc["rules"][1]["template_fit"]["params"]["min_rest_outstation"]
    # Deliberate malformed INPUT record, not a mocked completeness/advice result.
    # The actual catalogue validator will not release this record. The reader
    # boundary must nevertheless be fail-closed when given malformed data.
    records = copy.deepcopy(cb.family_records())
    records[TIME]["param_sets"] = [{"id": "broken", "params": ["base_tz_offset_min", "nonexistent"],
                                  "note": "Explicit malformed component input"}]
    monkeypatch.setattr(cb, "family_records", lambda: records)
    got = fp.family_completeness(doc)
    assert got[TIME]["catalog_problems"] and not got[TIME]["curable"]
    if with_healable:
        assert got[REST]["curable"] and got[REST]["missing"] == ["min_rest_outstation"]
    error = export_refusal(tmp_path / "broken-record", doc, "family-incomplete")
    assert "исправьте запись семьи в каталоге" in str(error)
    assert ("Ответьте на вопрос анкеты о параметре" in str(error)) is with_healable
    assert "nonexistent" in str(error)


def reserve_report(root, doc, raw):
    """Exact one-rule finite translation; unknown text/rules are refused."""
    import z3
    hard = [r for r in doc["rules"] if r["kind"] == "HARD"]
    assert [(r["id"], r["statement"]) for r in hard] == [
        ("R-RESERVE", "На базе ST1 не менее 1 резервного экипажа.")]
    translated = {"decls": ["(declare-const reserve_ST1 Int)"],
                  "asserts": [{"rule": "R-RESERVE", "smt": "(>= reserve_ST1 1)"}]}

    def translate(prompt):
        assert "R-RESERVE | На базе ST1 не менее 1 резервного экипажа." in prompt
        return json.dumps(translated)

    for actual, expected in ((1, z3.sat), (0, z3.unsat)):
        solver = z3.Solver()
        solver.from_string("(declare-const reserve_ST1 Int) (assert (>= reserve_ST1 1))"
                           f" (assert (= reserve_ST1 {actual}))")
        assert solver.check() == expected
    report = smt_verify.verify(doc, translate, model_sha256=smt_verify.model_fingerprint(raw))
    assert report["verdict"] == "sat" and report["n_rules"] == report["n_translated"] == 1
    smt_verify.write_report(root, report)


def test_native_partial_transfer_keeps_barrier_notes_out_of_question_counts(modern, tmp_path, monkeypatch):
    doc = document(modern, DUTY, RESERVE)
    doc["rules"][1]["template_fit"]["params"] = {}
    assert m.validate_schema(doc) == []
    # Missing cells are the intended K01 obligation, not malformed typed cells.
    # Shape validation accepts the explicit container; completeness, not the
    # format reader, names the missing family parameters and affected task.
    assert m.validate_template_fit(doc) == []
    assert producer._load_modern_package_model(json.dumps(doc).encode())["rules"] == doc["rules"]
    assert "builder_method" not in cb.executor_kinds(RESERVE)
    assert m.family_consumer_tasks(RESERVE) == ("rostering",)
    assert not doc["questions"]
    barriers = m.readiness_barriers(doc, "1.20")
    family = [p for p in barriers if p.get("code") == "family-incomplete"]
    assert len(family) == 1 and family[0]["family"] == RESERVE
    assert all(p["code"] == "family-incomplete" for p in barriers), barriers
    words = family[0]["words"]
    root = tmp_path / "partial-native"
    path, raw = source(root, doc)
    reserve_report(root, doc, raw)

    def forbidden_channel(*args, **kwargs):
        raise AssertionError("No external translator; exact fresh local report already exists")

    monkeypatch.setattr(producer, "smt_channel_factory", forbidden_channel)
    files = producer.build_package(root, spec_version="1.20")
    declared = json.loads(files["manifest.json"])
    assert declared["allowed_modes"]["rostering"] == []
    assert "feasibility-soft" in declared["allowed_modes"]["pairing"]
    ladder = declared["readiness_matrix"]
    assert ladder["form_version"] == 1
    roster = ladder["columns"]["rostering"]
    assert roster["state"] == "stop"
    assert f"барьер: {words}" in roster["notes"]
    for column in ladder["columns"].values():
        assert column["grounds"]["questions"]["open_blocking"] == 0
        assert column["grounds"]["questions"]["open_soft"] == 0
    assert all(not n.startswith("барьер: ") for n in ladder["columns"]["pairing"]["notes"])
    archive = root / "partial.zip"
    archive.write_bytes(producer.write_zip(files))
    problems, counters = gate.check(archive)
    assert problems == [], problems
    assert counters.get("barriers_held") == [words]
    got = receiver.load(archive, run_gate=False)
    assert got["landing_projection"]["archive_only"] is False
    assert got["family_projection"]["judged"]
    receiver.check_support_execution(got, task="pairing", mode="feasibility-soft")
    assert path.read_bytes() == raw
    (root / "component-scope.json").write_text(json.dumps({
        "scope": "synthetic support component only; no release or execution",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "actual_manifest": declared,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
