"""P0 §2.5: upgrade only proven legacy provenance; retain disputed values."""
import copy
import hashlib
import json

import pytest

from cdpm import answers, family_params as fp, model as m, provenance as prov
from test_cdpm_a03m_provenance import _answered, _cell, _q, QID, PARAM


def _legacy():
    doc, cell = _answered()
    cell.pop("source")
    for q in doc["questions"]:
        q.pop("edition", None)
        if isinstance(q.get("answer"), dict):
            q["answer"].pop("answer_version", None)
            q["answer"].pop("parsed", None)
    return doc


def _load(doc):
    return m.loads_model(json.dumps(doc, ensure_ascii=False))


def _pending(doc):
    cell = _cell(doc)
    qid = cell.get("pending_revision")
    assert qid
    q = _q(doc, qid)
    assert q["revision_of"] == QID and m.is_open_question(q, doc=doc)
    assert "подтвердите значение" in q["text"].lower()
    assert qid in next(r for r in doc["rules"] if r["id"] == "R-R")["blockers"]
    assert q["revision_cells"]["targets"]["R-R"]
    assert m.pending_revision_facts(doc)
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    return q


def test_unique_legacy_source_is_upgraded_without_changing_evidence(tmp_path):
    doc = _legacy()
    original = copy.deepcopy(_cell(doc))
    path = tmp_path / "model.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    raw = path.read_bytes()
    loaded = m.load_model(path)
    cell = _cell(loaded)
    assert cell["source"] == prov.answer_source(_q(loaded, QID), PARAM,
                                                _q(loaded, QID)["answer"]["raw"])
    assert {k: v for k, v in cell.items() if k != "source"} == original
    assert prov.cell_problems(loaded, "R-R", "rest_floors", PARAM, cell) == []
    assert _q(loaded, QID)["answer"]["answer_version"]["received_at"] is None
    assert path.read_bytes() == raw
    assert _load(loaded) == loaded


@pytest.mark.parametrize("raw,quote,value", [
    ("10,5 часов", "10,5 часов", 600),
    ("10:90", "10:90", 600),
    ("10 часов только для пилотов", "10 часов", 600),
    ("10 часов", "10 ЧАСОВ", 600),
])
def test_old_bad_value_or_context_requires_confirmation(raw, quote, value):
    doc = _legacy()
    _q(doc, QID)["answer"]["raw"] = raw
    _cell(doc).update(value=value, quote=quote)
    old = copy.deepcopy(_cell(doc))
    loaded = _load(doc)
    q = _pending(loaded)
    assert {k: v for k, v in _cell(loaded).items() if k != "pending_revision"} == old
    assert _q(loaded, QID)["answer"]["raw"] == raw
    assert _q(loaded, QID)["answer"]["revised_by"] == q["id"]
    assert _load(loaded) == loaded
    fp.ensure_family_param_questions(loaded)
    assert m.is_open_question(_q(loaded, q["id"]), doc=loaded)
    assert _cell(loaded)["pending_revision"] == q["id"]


@pytest.mark.parametrize("root_revised", [False, True])
def test_revision_chain_is_not_guessed_from_one_matching_quote(root_revised):
    doc = _legacy()
    root = _q(doc, QID)
    later = copy.deepcopy(root)
    later.update(id=QID + "-R", revision_of=QID,
                 title="Пересмотр отдыха вне базы", text="Уточните прежнее значение отдыха.")
    later["answer"]["raw"] = "9 часов"
    later["answer"].pop("effects", None)
    doc["questions"].append(later)
    if root_revised:
        root["answer"].update(status="пересмотрен", revised_by=later["id"])
    loaded = _load(doc)
    q = _pending(loaded)
    assert q["id"] not in (QID, later["id"])
    assert _cell(loaded)["value"] == 600 and "source" not in _cell(loaded)
    assert _load(loaded) == loaded


def test_migration_revision_accepts_human_correction_and_preserves_neighbour():
    doc = _legacy()
    _cell(doc)["value"] = 660
    loaded = _load(doc)
    revision = _pending(loaded)
    neighbour = copy.deepcopy(_cell(loaded, name="min_rest_home"))
    result = answers.apply_answers(loaded, {revision["id"]: "9 часов"})
    assert result["applied"] == 1 and result.get("conflicted", 0) == 0
    assert _cell(loaded)["value"] == 540
    assert "pending_revision" not in _cell(loaded)
    assert _cell(loaded, name="min_rest_home") == neighbour
    assert _cell(loaded)["source"]["question"]["id"] == revision["id"]
    before_reload = copy.deepcopy(loaded)
    history = copy.deepcopy(loaded["meta"]["landing_migrations"])
    assert len(history) == 1
    assert history[0]["items"][2]["state"] == "unconfirmed"
    assert history[0]["items"][2]["words"] == [
        "пересмотр открыт: снимок ячейки не меняется"]
    fingerprint = hashlib.sha256(json.dumps(
        {key: loaded.get(key) for key in ("rules", "questions")},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()
    assert fingerprint != history[0]["input_fingerprint"]
    event = {"schema_version": "landing-migration/1", "by": "code:landing-migration",
             "input_fingerprint": fingerprint, "output_fingerprint": fingerprint,
             "catalog": copy.deepcopy(history[0]["catalog"]), "items": [
        {"index": 0, "rule": "R-H", "state": "unconfirmed", "changes": [],
         "words": ["исходная посадка и её семья не установлены"]},
        {"index": 1, "rule": "R-S", "state": "unconfirmed", "changes": [],
         "words": ["исходная посадка и её семья не установлены"]},
        {"index": 2, "rule": "R-R", "state": "required", "changes": [],
         "words": ["смысл словарных зависимостей исходной записи не подтверждён"]},
    ]}
    # The new human answer changes the landing-migration subject. Only
    # its exact new observation may be appended; old evidence stays intact.
    expected = copy.deepcopy(loaded)
    expected["meta"]["landing_migrations"] = [*history, event]
    reloaded = _load(loaded)
    assert loaded == before_reload
    assert reloaded == expected
    assert _load(reloaded) == reloaded


def test_migration_revision_does_not_refresh_cas_after_late_edit():
    doc = _legacy()
    _cell(doc)["value"] = 660
    loaded = _load(doc)
    revision = _pending(loaded)
    _cell(loaded)["value"] = 700
    reloaded = _load(loaded)
    assert _q(reloaded, revision["id"])["revision_cells"] == revision["revision_cells"]
    assert answers.apply_answers(reloaded, {revision["id"]: "9 часов"})["conflicted"] == 1
    assert _cell(reloaded)["value"] == 700


def test_invalid_modern_identity_is_not_repaired_or_rebound():
    doc, cell = _answered()
    cell["source"]["version"]["fingerprint"] = "f" * 12
    old = copy.deepcopy(cell)
    loaded = _load(doc)
    assert _cell(loaded) == old
    assert prov.cell_problems(loaded, "R-R", "rest_floors", PARAM, _cell(loaded))


def test_known_modern_source_with_old_bad_value_also_requires_confirmation():
    doc, cell = _answered()
    cell["value"] = 660
    source = copy.deepcopy(cell["source"])
    loaded = _load(doc)
    _pending(loaded)
    assert _cell(loaded)["value"] == 660 and _cell(loaded)["source"] == source


def test_missing_legacy_question_is_not_invented():
    doc = _legacy()
    _cell(doc)["answer"] = "missing-question"
    loaded = _load(doc)
    assert _cell(loaded) == _cell(doc)
    assert len(loaded["questions"]) == len(doc["questions"])
    assert prov.cell_problems(loaded, "R-R", "rest_floors", PARAM, _cell(loaded))


def test_one_confirmation_covers_same_answer_cells_without_rewriting_them():
    doc = _legacy()
    second = copy.deepcopy(next(r for r in doc["rules"] if r["id"] == "R-R"))
    second["id"] = "R-SECOND"
    doc["rules"].append(second)
    _q(doc, QID)["rule_refs"].append("R-SECOND")
    _cell(doc)["value"] = 660
    loaded = _load(doc)
    revision = _pending(loaded)
    cells = [_cell(loaded), _cell(loaded, rid="R-SECOND")]
    assert [c["value"] for c in cells] == [660, 600]
    assert all(c["pending_revision"] == revision["id"] for c in cells)
    assert set(revision["revision_cells"]["targets"]) == {"R-R", "R-SECOND"}
    assert len([q for q in loaded["questions"] if q.get("revision_of") == QID]) == 1
    assert _load(loaded) == loaded


def test_valid_modern_source_and_original_answer_receipt_do_not_change():
    doc, cell = _answered()
    original = copy.deepcopy(cell)
    answer = copy.deepcopy(_q(doc, QID)["answer"])
    loaded = _load(doc)
    assert _cell(loaded) == original
    assert _q(loaded, QID)["answer"] == answer
    assert not m.pending_revision_facts(loaded)


def test_question_and_rule_order_do_not_choose_legacy_answer():
    doc = _legacy()
    _cell(doc)["value"] = 660
    permuted = copy.deepcopy(doc)
    permuted["rules"].reverse()
    permuted["questions"].reverse()
    first, second = _load(doc), _load(permuted)
    assert _cell(first) == _cell(second)
    assert _pending(first) == _pending(second)
