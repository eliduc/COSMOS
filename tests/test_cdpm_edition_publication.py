"""A04 publication and automatic-family wiring, temporary files/fake inputs."""
import copy
import hashlib
import json

import pytest

from cdpm import anketa_xlsx as ax, answers, model as m, family_params as fp
from cdpm import question_editions as qe, render
from test_cdpm_question_editions import _doc
from test_cdpm_family_params import _doc_with_home_floor, QID


def _complete_family_answer(doc, qid, raw):
    """Complete all issued targets, not the former first-row-only shortcut."""
    result = answers.apply_answers(doc, {qid: raw}, None)
    assert result["applied"] == 0 and result["partial"] == 1
    q = next(q for q in doc["questions"] if q["id"] == qid)
    family, param = fp.question_target(q)
    spec = fp._param_spec(family, param)
    value, quote = fp.parse_value(raw, spec["unit"], fp.default_domain(spec["unit"]))
    pending = answers._affected_rules(doc, q)
    assert pending
    patches = []
    for row in pending:
        patch = {key: copy.deepcopy(value) for key, value in row.items()
                 if key in answers._PATCH_FIELDS}
        patch.pop("blockers", None)
        patch["template_fit"]["params"][param] = fp._answer_cell(q, param, value, quote, raw)
        patches.append(patch)
    reply = json.dumps({"patches": [{"question": qid, "change": "Значение по оставшимся строкам",
                                    "rules": patches}]}, ensure_ascii=False)
    report = answers.apply_worker_patches(doc, lambda prompt: reply)
    assert report["worker_rejected"] == [] and report["worker_applied"] == 1
    assert q["answer"]["status"] == "применён"


def _family_scope_doc():
    # These probes concern an issued parameter's scope, not the sample's
    # independent unresolved modality question. The worker rightly refuses
    # to resolve another question's conditional kind as a parameter answer.
    doc, carrier = _doc_with_home_floor()
    cond = carrier.pop("conditional", None)
    carrier["kind"] = "HARD"
    if cond:
        carrier["blockers"] = [qid for qid in carrier.get("blockers", [])
                               if qid != cond["question"]]
        if not carrier["blockers"]:
            carrier.pop("blockers")
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    return doc, carrier


def test_publication_saves_identity_before_read_only_render(tmp_path):
    doc, q = _doc()
    qid = q["id"]
    model_path = tmp_path / "model.json"
    m.publish_model(doc, model_path)
    published_bytes = model_path.read_bytes()
    before_load = copy.deepcopy(doc)
    saved = m.load_model(model_path)
    from cdpm import catalog_bridge
    cat = catalog_bridge.catalog()
    assert "landing_migrations" not in doc["meta"]
    fingerprint = hashlib.sha256(json.dumps(
        {key: doc.get(key) for key in ("rules", "questions")},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()
    event = {"schema_version": "landing-migration/1", "by": "code:landing-migration",
             "input_fingerprint": fingerprint, "output_fingerprint": fingerprint,
             "catalog": {"version": str(cat.version()), "fingerprint": cat.fingerprint()},
             "items": [
        {"index": index, "rule": rid, "state": "unconfirmed", "changes": [],
         "words": ["исходная посадка и её семья не установлены"]}
        for index, rid in ((0, "R-S1-П-1-1"), (1, "R-S1-П-1-4"),
                           (2, "R-S2-П-5-1"), (3, "R-S1-П-2-3"),
                           (4, "R-S1-П-3-2"), (6, "R-S2-П-7-1"))
    ]}
    # This sample has no landings. Loading may add exactly their initial
    # unconfirmed observations, never rewrite the published question scope.
    expected = copy.deepcopy(doc)
    expected["meta"]["landing_migrations"] = [event]
    assert doc == before_load
    assert saved == expected
    assert m.loads_model(json.dumps(saved, ensure_ascii=False)) == saved
    assert model_path.read_bytes() == published_bytes
    assert all("edition" in x for x in saved["questions"])
    published = next(x for x in saved["questions"] if x["id"] == qid)
    assert "fingerprint_v1" not in published["edition"]
    before = copy.deepcopy(doc)
    ax.render_anketa_xlsx(doc, tmp_path / "anketa.xlsx")
    assert doc == before
    m.publish_model(doc, model_path)
    assert doc == before


def test_existing_legacy_book_keeps_exact_fingerprint(tmp_path):
    doc, q = _doc()
    old = ax.question_fingerprint(q)
    ax.render_anketa_xlsx(doc, tmp_path / "anketa.xlsx")
    m.dump_model(doc, tmp_path / "model.json")
    m.publish_model(doc, tmp_path / "model.json")
    current = next(x for x in doc["questions"] if x["id"] == q["id"])
    assert ax.question_fingerprint(current) == old
    assert current["edition"]["fingerprint_v1"] == old


def test_answered_question_keeps_source_identity_in_new_directory(tmp_path):
    doc, q = _doc()
    old = ax.question_fingerprint(q)
    q["answer"] = {"raw": "600 минут", "status": "применён", "revision": 1}
    m.publish_model(doc, tmp_path / "model.json")
    current = next(x for x in doc["questions"] if x["id"] == q["id"])
    assert ax.question_fingerprint(current) == old
    assert current["answer"] == q["answer"]


def test_failure_does_not_half_freeze_callers_model(tmp_path, monkeypatch):
    doc, _ = _doc()
    before = copy.deepcopy(doc)
    def fail(*args):
        raise OSError("synthetic write failure")
    monkeypatch.setattr(m, "dump_model", fail)
    with pytest.raises(OSError):
        m.publish_model(doc, tmp_path / "model.json")
    assert doc == before


def test_all_snapshots_validated_before_any_publication_write(tmp_path):
    doc, q = _doc()
    qe.freeze(q)
    q["rule_refs"].append(doc["rules"][0]["id"])
    before = copy.deepcopy(doc)
    path = tmp_path / "model.json"
    with pytest.raises(qe.EditionRefusal):
        m.publish_model(doc, path)
    assert not path.exists()
    assert doc == before


def test_live_canon_writer_persists_editions_and_repeats(tmp_path):
    from cdpm import webui
    doc, _ = _doc()
    out = tmp_path / "extract"
    out.mkdir()
    assert webui._sync_canon(tmp_path, doc, "A04 test") == []
    saved = m.load_model(out / "model.json")
    assert all("edition" in q for q in saved["questions"])
    identity = {q["id"]: ax.question_fingerprint(q) for q in saved["questions"]}
    assert webui._sync_canon(tmp_path, saved, "A04 repeat") == []
    again = m.load_model(out / "model.json")
    assert identity == {q["id"]: ax.question_fingerprint(q) for q in again["questions"]}
    assert (out / "anketa.xlsx").is_file()


def test_family_scope_growth_uses_new_edition_and_recognizes_its_key(tmp_path):
    doc, _ = _family_scope_doc()
    fp.ensure_family_param_questions(doc)
    m.publish_model(doc, tmp_path / "model.json")
    old = next(q for q in doc["questions"] if q["id"] == QID)
    before = copy.deepcopy(old)
    carrier = next(r for r in doc["rules"] if r["id"] == old["rule_refs"][0])
    other = copy.deepcopy(carrier)
    other["id"] = "R-extra-family"
    other["blockers"] = [b for b in other.get("blockers", []) if b != QID]
    doc["rules"].append(other)
    created = fp.ensure_family_param_questions(doc)
    assert QID + "-E2" in created
    new = next(q for q in doc["questions"] if q["id"] == QID + "-E2")
    assert old == before
    assert fp.question_target(new) == ("rest_floors", "min_rest_outstation")
    assert fp.ensure_family_param_questions(doc) == []
    assert fp._live_param_question([old, new]) is new
    result = answers.apply_answers(doc, {new["id"]: "600 минут"}, None)
    assert result["applied"] == 0 and result["partial"] == 1
    assert new["answer"]["status"] == m.PARTIAL_ANSWER_STATUS
    assert len(answers._affected_rules(doc, new)) == 1


def test_e2_reconsideration_keeps_parameter_identity(tmp_path):
    doc, _ = _family_scope_doc()
    fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == QID)
    qe.freeze(q)
    carrier = next(r for r in doc["rules"] if r["id"] == q["rule_refs"][0])
    other = copy.deepcopy(carrier)
    other["id"] = "R-reconsider-extra"
    other["blockers"] = [b for b in other.get("blockers", []) if b != QID]
    doc["rules"].append(other)
    fp.ensure_family_param_questions(doc)
    new = next(x for x in doc["questions"] if x["id"] == QID + "-E2")
    _complete_family_answer(doc, new["id"], "600 минут")
    result = answers.open_revision(doc, new["id"])
    revised = next(q for q in doc["questions"] if q["id"] == result["created"])
    assert revised["id"] == QID + "-E2-R"
    assert fp.question_target(revised) == ("rest_floors", "min_rest_outstation")
    m.publish_model(doc, tmp_path / "model.json")
    _complete_family_answer(doc, revised["id"], "540 минут")
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []


def test_md_and_xlsx_share_active_questions_and_counts():
    doc, q = _doc()
    qe.freeze(q)
    new = qe.expand(doc, q, [doc["rules"][0]["id"]])
    active = m.open_questions(doc)
    assert q not in active and new in active
    md = render.render_anketa(doc)
    assert "### ВОПРОС " + q["id"] + ":" not in md
    assert new["id"] in md
    rows = dict(row for row in ax.summary_rows(doc) if len(row) == 2)
    assert rows["Открытых вопросов"] == len(active)
    assert rows["Принятых ответов"] == 0
    assert rows["Прежних изданий без ответа"] == 1


def test_published_code_closed_question_reopens_without_erasing_history():
    doc, carrier = _doc_with_home_floor()
    fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == QID)
    qe.freeze(q)
    carrier["template_fit"]["params"]["min_rest_outstation"] = {"value": 600, "quote": "600"}
    fp.ensure_family_param_questions(doc)
    before = copy.deepcopy(q)
    assert q["answer"]["raw"] == fp.CODE_CLOSED_RAW
    carrier["template_fit"]["params"].pop("min_rest_outstation")
    assert fp.ensure_family_param_questions(doc) == [QID + "-E2"]
    new = next(x for x in doc["questions"] if x["id"] == QID + "-E2")
    assert q == before
    assert m.is_open_question(new)
    assert new["id"] in carrier["blockers"]
    assert fp.ensure_family_param_questions(doc) == []
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []


def test_old_applied_answer_cannot_close_new_scope_or_win_self_healing():
    doc, carrier = _family_scope_doc()
    fp.ensure_family_param_questions(doc)
    old = next(q for q in doc["questions"] if q["id"] == QID)
    qe.freeze(old)
    assert answers.apply_answers(doc, {QID: "600 минут"}, None)["applied"] == 1
    before = copy.deepcopy(old)
    other = copy.deepcopy(carrier)
    other["id"] = "R-new-scope"
    other["blockers"] = [b for b in other.get("blockers", []) if b != QID]
    other["template_fit"]["params"].pop("min_rest_outstation")
    doc["rules"].append(other)
    assert fp.ensure_family_param_questions(doc) == [QID + "-E2"]
    new = next(x for x in doc["questions"] if x["id"] == QID + "-E2")
    assert old == before
    assert "answer" not in new
    assert all(new["id"] in r["blockers"] for r in (carrier, other))
    assert fp.ensure_family_param_questions(doc) == []
    assert "answer" not in new
    _complete_family_answer(doc, new["id"], "540 минут")
    carrier["template_fit"]["params"].pop("min_rest_outstation")
    # The complete answer now covers both rows. Lose the parameter in both
    # to exercise healing, rather than leaving a healthy family-wide carrier.
    other["template_fit"]["params"].pop("min_rest_outstation")
    fp.ensure_family_param_questions(doc)
    cell = carrier["template_fit"]["params"]["min_rest_outstation"]
    assert cell["value"] == 540
    assert cell["source"]["question"]["id"] == new["id"]
    assert old == before
