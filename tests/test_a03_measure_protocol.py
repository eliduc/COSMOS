"""Answer-origin evidence must travel in and be checked against the protocol."""
import copy
import hashlib
import io
import json
import zipfile

import pytest

from cdpm import answers, measure, provenance
from cdpm.gates import gate_task_package as gate
from test_a03_typed_origin_readers import answered_pair, PARAM
from test_k01_semantic_transition import clean_bridge


def scenario(tmp_path, monkeypatch):
    doc, rule, q, _ = answered_pair(tmp_path, monkeypatch, "duration_minutes")
    src = rule["sources"][0]
    clauses = [{"sid": src["sid"], "anchor": src["anchor"], "text": rule["statement"]}]
    return doc, rule, q, clauses


@pytest.mark.parametrize("forged", [False, True])
def test_protocol_explicitly_judges_answer_carrier(tmp_path, monkeypatch, forged):
    doc, rule, q, clauses = scenario(tmp_path, monkeypatch)
    cell = rule["template_fit"]["params"][PARAM]
    if forged:
        cell["source"]["version"]["fingerprint"] = "0" * 12
    before = copy.deepcopy(doc)
    report = measure.measure(doc, clauses)
    records = report.get("answer_carriers")
    assert isinstance(records, list) and len(records) == 1
    record = records[0]
    assert record["rule_id"] == rule["id"] and record["field"] == "params." + PARAM
    assert record["carrier"] == "answer" and record["answer"] == q["id"]
    assert record["source"] == cell["source"]
    assert record["ok"] is (not forged)
    assert bool(record["problems"]) is forged
    assert record["problems"] == provenance.cell_problems(
        doc, rule["id"], rule["template_fit"]["family"], PARAM, cell)
    # Origin judgement is explicit, not falsely attributed to a clause.
    assert report["counts"]["fields_without_carrier"] == 0
    assert doc == before


@pytest.mark.parametrize("mutation", ["omit", "empty", "false_ok", "source", "shape",
                                      "ok_number", "duplicate"])
def test_package_recount_rejects_forged_answer_protocol(tmp_path, monkeypatch, mutation):
    doc, rule, q, clauses = scenario(tmp_path, monkeypatch)
    cell = rule["template_fit"]["params"][PARAM]
    proto = measure.measure(doc, clauses)
    # Independent expected entry lets the baseline reach the consumer even
    # while its producer has not yet implemented the protocol field.
    proto["answer_carriers"] = [{
        "rule_id": rule["id"], "field": "params." + PARAM,
        "carrier": "answer", "answer": q["id"],
        "source": copy.deepcopy(cell["source"]), "ok": True, "problems": [],
    }]
    kit = [{"sid": clauses[0]["sid"], "clauses": [
        {"anchor": clauses[0]["anchor"], "text": clauses[0]["text"]}]}]
    files = {"model.json": json.dumps(doc, ensure_ascii=False).encode("utf-8")}
    assert gate._recount_problems(files, proto, kit) == []
    if mutation == "omit":
        proto.pop("answer_carriers")
    elif mutation == "empty":
        proto["answer_carriers"] = []
    elif mutation == "false_ok":
        proto["answer_carriers"][0]["ok"] = False
    elif mutation == "source":
        proto["answer_carriers"][0]["source"]["version"]["fingerprint"] = "0" * 12
    elif mutation == "ok_number":
        proto["answer_carriers"][0]["ok"] = 1  # True == 1 is not protocol equality.
    elif mutation == "duplicate":
        proto["answer_carriers"] *= 2
    else:
        proto["answer_carriers"] = 17
    problems = gate._recount_problems(files, proto, kit)
    assert problems and any("answer_carriers" in p for p in problems)
    words = " | ".join(problems)
    assert rule["id"] in words and "params." + PARAM in words


@pytest.mark.parametrize("forged_protocol", [False, True])
def test_actual_measure_gate_checks_serialized_answer_protocol(
        tmp_path, monkeypatch, forged_protocol):
    doc, _rule, _q, clauses = scenario(tmp_path, monkeypatch)
    proto = measure.measure(doc, clauses)
    ok, problems = measure.verdict(proto)
    assert ok and not problems
    manifest = {"freeze_clauses": {"sids": [clauses[0]["sid"]]},
                "coverage": {}, "measure": {"available": True, "ok": ok,
                    "problems": problems, "counts": copy.deepcopy(proto["counts"])}}
    if forged_protocol:
        proto["answer_carriers"][0]["source"]["version"]["fingerprint"] = "0" * 12
    kit = {"sid": clauses[0]["sid"], "clauses": [
        {"anchor": clauses[0]["anchor"], "text": clauses[0]["text"]}]}
    raw = lambda x: json.dumps(x, ensure_ascii=False).encode("utf-8")
    files = {"model.json": raw(doc), "measure-protocol.json": raw(proto),
             "freeze/clauses/" + clauses[0]["sid"] + ".json": raw(kit)}
    got = gate._check_measure(files, manifest)
    if forged_protocol:
        assert got and any("answer_carriers" in p for p in got)
    else:
        assert got == []


def test_protocol_tracks_real_revisions_without_aliasing_source(tmp_path, monkeypatch):
    doc, rule, q, clauses = scenario(tmp_path, monkeypatch)
    for raw in ("9 часов", "8 часов"):
        qid = answers.open_revision(doc, q["id"])["created"]
        q = next(q for q in doc["questions"] if q["id"] == qid)
        assert answers.apply_answers(doc, {qid: raw})["applied"] == 1
        record = measure.measure(doc, clauses)["answer_carriers"][0]
        assert record["source"]["question"]["id"] == qid
        assert record["source"]["version"]["fingerprint"] == provenance.answer_version_fingerprint(raw)
        assert record["ok"] and record["problems"] == []
        record["source"]["version"]["fingerprint"] = "0" * 12
        assert provenance.package_problems(doc) == []


def test_old_protocol_without_answer_cells_remains_compatible(tmp_path, monkeypatch):
    doc, rule, _q, clauses = scenario(tmp_path, monkeypatch)
    rule["template_fit"]["params"].pop(PARAM)
    proto = measure.measure(doc, clauses)
    assert proto.pop("answer_carriers") == []
    kit = [{"sid": clauses[0]["sid"], "clauses": [
        {"anchor": clauses[0]["anchor"], "text": clauses[0]["text"]}]}]
    files = {"model.json": json.dumps(doc, ensure_ascii=False).encode("utf-8")}
    assert gate._recount_problems(files, proto, kit) == []
    for malformed in (None, {}, False):
        proto["answer_carriers"] = malformed
        assert any("answer_carriers" in p for p in gate._recount_problems(files, proto, kit))


@pytest.mark.parametrize("mutation", [None, "value", "unit", "relation", "scope", "quote"])
def test_legacy_projection_inherits_only_agreeing_answer_carrier(tmp_path, monkeypatch, mutation):
    from cdpm.param_projection import derive_params_from_landing
    doc, rule, q, clauses = scenario(tmp_path, monkeypatch)
    rule["params"] = derive_params_from_landing(rule)
    projection = next(p for p in rule["params"] if p["name"] == PARAM)
    if mutation == "value": projection["value"] += 1
    elif mutation == "unit": projection["unit"] = "count"
    elif mutation == "relation": projection["relation"] = "max"
    elif mutation == "scope": projection["qualifier"] = "base"
    elif mutation == "quote": projection["as_written"] = "другая цитата"
    fields = measure.field_values(rule)
    actual = next(f for f in fields if f["field"] == "params[]." + PARAM)
    if mutation is None:
        assert actual["carrier_answer"] == q["id"]
        assert not any(x["field"] == "params[]." + PARAM
                       for x in measure.carriers(clauses[0]["text"], rule))
    else:
        assert "carrier_answer" not in actual
        assert any(x["field"] == "params[]." + PARAM
                   for x in measure.carriers(clauses[0]["text"], rule))


def test_real_answer_package_carries_protocol_and_gate_rejects_rehashed_forgery(
        tmp_path, monkeypatch):
    """Real SOFT-only export and receiver, with a real answered parameter.

    Zero hard rules are verified by the actual verifier. A translator, solver,
    external gate process and executor are not needed or permitted here.
    The frozen source is synthetic test input, not a customer's document.
    """
    from cdpm import export_task_package as producer, family_params, smt_verify
    from webapp import task_package as receiver
    from test_cdpm_content_lock import _run
    from test_cdpm_landing_v2_projection import typed_rule

    cells = {
        "duty_extension_cap": {"value": 960, "unit": "duration_minutes", "relation": "max",
                               "condition": None, "quote": "960 минут"},
        "long_duty_threshold": {"value": 720, "unit": "duration_minutes", "relation": "max",
                                "condition": None, "quote": "720 минут"},
        "extension_bases": {"value": ["ST1", "ST2"], "unit": "code_list", "relation": "in",
                            "condition": None, "quote": "ST1, ST2"},
        "extension_consent": {"value": True, "unit": "flag", "relation": "yes",
                              "condition": None, "quote": "с согласия"},
    }
    doc, rule = typed_rule(tmp_path, monkeypatch, "duty_extension_penalty", cells)
    rule.update(kind="SOFT", zone="PAIRING", weight=3,
                statement="Продление смены для ST1, ST2 только с согласия экипажа; "
                          "порог долгой смены 720 минут; вес 3.")
    doc["rules"], doc["questions"], doc["objectives"] = [rule], [], []
    del rule["template_fit"]["params"]["duty_extension_cap"]
    family_params.ensure_family_param_questions(doc)
    assert len(doc["questions"]) == 1
    q = doc["questions"][0]
    applied = answers.apply_answers(doc, {q["id"]: "16 часов"})
    assert applied["applied"] == 1, applied
    cell = rule["template_fit"]["params"]["duty_extension_cap"]
    assert cell["value"] == 960 and cell["source"]["kind"] == "answer"
    assert provenance.package_problems(doc) == []
    source = rule["sources"][0]
    run = _run(tmp_path, doc, [{"sid": source["sid"], "file": "soft.md", "sha256": "0" * 64,
                              "clauses": [{"anchor": source["anchor"], "text": rule["statement"]}]}])
    path = run / "extract" / "model.json"
    original = path.read_bytes()

    def forbidden(*_args, **_kwargs):
        pytest.fail("No translator or solver is permitted for this SOFT-only package")

    monkeypatch.setattr(smt_verify, "_run_z3", forbidden)
    monkeypatch.setattr(producer, "smt_channel_factory", forbidden)
    report = smt_verify.verify(doc, forbidden, model_sha256=smt_verify.model_fingerprint(original))
    assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(run, report)
    body = producer.export_zip(run)
    package = tmp_path / "answer-package.zip"
    package.write_bytes(body)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    actual = json.loads(files["model.json"])
    protocol = json.loads(files["measure-protocol.json"])
    actual_cell = actual["rules"][0]["template_fit"]["params"]["duty_extension_cap"]
    assert actual_cell == cell
    assert protocol["answer_carriers"] == [{
        "rule_id": rule["id"], "field": "params.duty_extension_cap", "carrier": "answer",
        "answer": q["id"], "source": cell["source"], "ok": True, "problems": [],
    }]
    problems, _ = gate.check(package)
    assert problems == [], problems
    received = receiver.load(package, run_gate=False)
    assert received["model"] == actual
    with pytest.raises(receiver.TaskPackageRefused, match="нет очищенных данных"):
        receiver.to_payload(received)
    assert path.read_bytes() == original

    # Recompute the member checksum: the refusal must be the protocol's
    # semantic disagreement with the real model, not damaged ZIP integrity.
    declared = json.loads(files["manifest.json"])
    protocol["answer_carriers"][0]["source"]["version"]["fingerprint"] = "0" * 12
    forged = json.dumps(protocol, ensure_ascii=False).encode("utf-8")
    for item in declared["files"]:
        if item["name"] == "measure-protocol.json":
            item.update(sha256=hashlib.sha256(forged).hexdigest(), bytes=len(forged))
    files["measure-protocol.json"] = forged
    files["manifest.json"] = json.dumps(declared, ensure_ascii=False).encode("utf-8")
    bad = tmp_path / "forged-answer-package.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    problems, _ = gate.check(bad)
    assert problems and any("answer_carriers" in p for p in problems), problems
    assert not any("sha256" in p.lower() for p in problems), problems
