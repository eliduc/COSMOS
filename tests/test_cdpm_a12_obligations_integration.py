"""Real MD freeze + disk model/ledger -> coverage.run_report (A12 opt-in).

Only synthetic tmp files. No verdict mocks, conftest, transport, extraction
model or runtime gate substitution. The existing coverage measure runs too;
this does not claim a full package/semantic acceptance.
"""
import copy
import hashlib
import json

import pytest

from cdpm import coverage, freeze, model, obligations as ob
from test_cdpm_a12_obligations import CLAUSE, CONDITION, _fixture, _json, _link, _model, _reseal


def _case(tmp_path, *, with_answer=False):
    run = tmp_path / "run-0012"
    (run / "src").mkdir(parents=True)
    (run / "extract").mkdir()
    (run / "exchange").mkdir()
    original = run / "src" / "synthetic-rules.md"
    original.write_text(CLAUSE + "\n", encoding="utf-8")
    manifest = freeze.freeze([original], run / "freeze")
    assert manifest["files"][0]["clause_count"] == 1
    clauses = json.loads((run / "freeze" / "clauses" / "S1.json").read_bytes())
    assert clauses["clauses"][0]["anchor"] == "строка 1"
    assert clauses["clauses"][0]["text"] == CLAUSE
    doc = _model()
    doc["questions"] = []
    doc["sources"] = [{"sid": "S1", "file": original.name,
                       "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}]
    for rule in doc["rules"]:
        rule["sources"][0]["anchor"] = "строка 1"
    answer_sources = []
    if with_answer:
        from cdpm import answer_versions, provenance, question_editions
        question = {"id": "Q-LICENCE", "part": "1", "title": "Допуск",
                    "text": "Когда разрешено назначение?", "answer_format": {"type": "value"},
                    "rule_refs": ["R-LICENCE"],
                    "source_refs": [{"sid": "S1", "anchor": "строка 1"}]}
        question_editions.freeze(question)
        question["answer"] = {"raw": "Назначение разрешено только при действующем допуске",
                              "status": "применён"}
        answer_versions.stamp(question["answer"], None, {"received_at": None, "book_sha256": None})
        doc["questions"] = [question]
        answer_sources = [{"kind": "answer", "question": provenance.question_edition(question),
                           "version": {"fingerprint": question["answer"]["answer_version"]["fingerprint"]}}]
    # Real in-memory migration is materialized BEFORE the ledger snapshot;
    # the reader must not pretend migrated bytes are the old disk bytes.
    doc = model.loads_model(_json(doc))
    model.dump_model(doc, run / "extract" / "model.json")
    ledger, _ = _fixture()
    ledger["snapshot"] = ob.capture_run_snapshot(run, answer_sources=answer_sources)
    document_unit = ob.document_unit("S1", "строка 1")
    scope = [document_unit]
    if with_answer:
        scope.append(ob.answer_unit(answer_sources[0]))
    ledger["scope"]["included"] = scope
    for reader in ledger["readers"]:
        reader["input_sha256"] = ob.fingerprint(ledger["snapshot"])
        reader["reviewed_units"] = scope[:]
        for obligation in reader["obligations"]:
            obligation["sources"][0]["unit"] = document_unit
        if with_answer:
            raw = doc["questions"][0]["answer"]["raw"]
            obligation = reader["obligations"][1]
            obligation["sources"] = [{"unit": scope[1], "fragments": [
                {"start": 0, "end": len(raw.encode("utf-8")), "quote": raw}]}]
            obligation["conditions"] = [{"id": "C1", "text": CONDITION}]
    _reseal(ledger)
    _link(ledger)
    (run / ob.LEDGER_REL).write_bytes(_json(ledger))
    return run, ledger


def _raw_tree(run):
    return {p.relative_to(run).as_posix(): p.read_bytes() for p in run.rglob("*") if p.is_file()}


def test_actual_coverage_optin_keeps_nonlinked_obligation_and_existing_report(tmp_path):
    run, ledger = _case(tmp_path)
    before = _raw_tree(run)
    old = coverage.run_report(run)
    current = coverage.run_report(run, include_obligations=True)
    assert "obligations" not in old
    assert "measure_error" not in old, old
    assert {key: value for key, value in current.items() if key != "obligations"} == old
    assert current["clauses"] == 1 and current["covered_by_rows"] == 1
    report = current["obligations"]
    assert report["form"] == "valid" and report["freshness"] == "current"
    assert (report["readers"][0]["total"], report["readers"][0]["linked"],
            report["readers"][0]["missing"]) == (2, 1, ["O2"])
    assert report["meaning"] == "not_proven"
    assert report["ledger"]["sha256"] == hashlib.sha256(before[ob.LEDGER_REL]).hexdigest()
    assert _raw_tree(run) == before

    # Healed/package doc must be accompanied by ITS exact bytes, not by the
    # former canonical fingerprint. The caller cannot refresh old links.
    projected = model.load_model(run / "extract" / "model.json")
    projected["rules"][0]["params"][0]["value"] = 610
    without_bytes = coverage.run_report(run, doc=projected, include_obligations=True)
    assert without_bytes["obligations"]["form"] == "unavailable"
    exact = coverage.run_report(run, doc=projected, include_obligations=True,
                                obligations_model_bytes=_json(projected))
    assert exact["obligations"]["freshness"] == "stale_or_unresolved"
    assert exact["obligations"]["readers"][0]["linked"] == 0
    assert _raw_tree(run) == before

    # A malformed saved carrier is local to the opt-in report, not an empty
    # successful result and not a change to the previous coverage contract.
    (run / ob.LEDGER_REL).write_bytes(b'{"schema_version":"x","schema_version":"y"}')
    assert coverage.run_report(run) == old
    assert coverage.run_report(run, include_obligations=True)["obligations"]["form"] == "unavailable"


@pytest.mark.parametrize("changed", ["source", "clause_file_bytes", "model_bytes"])
def test_existing_run_reads_detect_stale_bytes_without_rewriting_ledger(tmp_path, changed):
    run, _ = _case(tmp_path)
    saved_ledger = (run / ob.LEDGER_REL).read_bytes()
    targets = {"source": run / "src" / "synthetic-rules.md",
               "clause_file_bytes": run / "freeze" / "clauses" / "S1.json",
               "model_bytes": run / "extract" / "model.json"}
    path = targets[changed]
    path.write_bytes(path.read_bytes() + b" ")
    mutated = _raw_tree(run)
    report = coverage.run_report(run, include_obligations=True)["obligations"]
    assert report["freshness"] == "stale_or_unresolved"
    if changed == "source":
        assert report["form"] == "unavailable"
        assert "source SHA differs" in report["issues"][0]["detail"]
        assert report["readers"] == []
    else:
        assert report["form"] == "valid"
        assert report["readers"][0]["linked"] == 0
        assert report["readers"][0]["items"][0]["status"] == "stale"
    assert (run / ob.LEDGER_REL).read_bytes() == saved_ledger
    assert _raw_tree(run) == mutated


def test_real_saved_answer_identity_is_not_replaced_by_latest_answer(tmp_path):
    from cdpm import answer_versions
    run, ledger = _case(tmp_path, with_answer=True)
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/statement")
    _link(ledger, oid="O2", row="R-LICENCE", pointer="/conditions/0", component="conditions/C1")
    (run / ob.LEDGER_REL).write_bytes(_json(ledger))
    healthy = coverage.run_report(run, include_obligations=True)["obligations"]
    assert healthy["form"] == "valid" and healthy["freshness"] == "current"
    assert healthy["readers"][0]["linked"] == 2
    saved_ledger = (run / ob.LEDGER_REL).read_bytes()
    current = model.load_model(run / "extract" / "model.json")
    answer = current["questions"][0]["answer"]
    previous = copy.deepcopy(answer)
    answer["raw"] = "Назначение запрещено"
    answer_versions.stamp(answer, previous, {"received_at": None, "book_sha256": None})
    model.dump_model(current, run / "extract" / "model.json")
    before = _raw_tree(run)
    report = coverage.run_report(run, include_obligations=True)["obligations"]
    assert report["freshness"] == "stale_or_unresolved"
    assert report["readers"][0]["linked"] == 0
    assert report["issues"][0]["code"] == "inputs_unresolved"
    assert "версия ответа" in report["issues"][0]["detail"]
    assert (run / ob.LEDGER_REL).read_bytes() == saved_ledger
    assert _raw_tree(run) == before
    (run / ob.LEDGER_REL).unlink()
    missing = ob.run_report(run)
    assert missing["form"] == "not_recorded"
    assert missing["issues"] == [{"code": "ledger_missing", "path": "exchange/obligations.json"}]
