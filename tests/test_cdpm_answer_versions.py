"""A02 receipt identity: synthetic input, in-process readers, no live channels."""
import copy
import hashlib
from datetime import datetime

import pytest

from cdpm import answers, model as m, provenance
from test_cdpm_answers import _book_with
from test_cdpm_family_answer_rows import _case
from test_cdpm_question_editions import _doc
from test_cdpm_a03m_provenance import _answered

T1 = "2026-09-14T10:00:00+00:00"
T2 = "2026-09-14T11:00:00+00:00"
BOOK1 = "a" * 64
BOOK2 = "b" * 64


def _fingerprint(raw):
    return hashlib.sha256(" ".join(raw.split()).encode()).hexdigest()[:12]


@pytest.mark.parametrize("kind", ["single", "partial", "freeform"])
def test_new_answer_persists_exact_receipt(kind):
    if kind == "freeform":
        doc, q = _doc()
    else:
        doc, q, _ = _case(count=1 if kind == "single" else 2)
    raw = "600 минут"
    answers.apply_answers(doc, {q["id"]: raw})
    version = q["answer"].get("answer_version")
    assert version, "The received answer lost its agreed persisted version"
    assert version["fingerprint"] == _fingerprint(raw)
    assert datetime.fromisoformat(version["received_at"]).utcoffset() is not None
    assert version["book_sha256"] is None, "Direct input must not invent a book"
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_rejected_reading_is_still_an_identified_received_answer():
    doc, q = _doc()
    answers.apply_answers(doc, {}, refused={q["id"]: {
        "raw": "600 минут", "reason": "книга другого издания"}})
    assert q["answer"]["status"] == "не разобран"
    assert q["answer"].get("answer_version", {}).get("fingerprint") == _fingerprint("600 минут")


def test_book_reading_carries_identity_of_exact_input_bytes(tmp_path):
    doc, q = _doc()
    path = _book_with(tmp_path, doc, {q["id"]: "600 минут"})
    original = path.read_bytes()
    reading = answers.read_book(path, doc)
    assert getattr(reading, "book_sha256", None) == hashlib.sha256(original).hexdigest()
    assert datetime.fromisoformat(reading.received_at).utcoffset() is not None


def test_forged_saved_version_is_not_ignored_by_provenance():
    doc, cell = _answered()
    q, raw = provenance.resolve_source(doc, cell["source"])
    q["answer"]["answer_version"] = {
        "fingerprint": "0" * 12, "received_at": T1, "book_sha256": BOOK1}
    assert provenance.package_problems(doc), "Valid source must not mask a forged answer version"


@pytest.mark.parametrize("kind", ["single", "partial", "freeform"])
def test_repeated_processing_does_not_change_received_version(kind):
    if kind == "freeform":
        doc, q = _doc()
    else:
        doc, q, _ = _case(count=1 if kind == "single" else 2)
    answers.apply_answers(doc, {q["id"]: "600 минут"}, received_at=T1, book_sha256=BOOK1)
    old = copy.deepcopy(q["answer"]["answer_version"])
    answers.apply_answers(doc, {q["id"]: "600 минут"}, received_at=T2, book_sha256=BOOK2)
    assert q["answer"]["answer_version"] == old


def test_retry_unparsed_changed_text_gets_new_identity():
    doc, q, _ = _case(count=1)
    answers.apply_answers(doc, {q["id"]: "не знаю"}, received_at=T1, book_sha256=BOOK1)
    assert q["answer"]["status"] == "не разобран"
    first = copy.deepcopy(q["answer"]["answer_version"])
    answers.apply_answers(doc, {q["id"]: "600 минут"}, received_at=T2, book_sha256=BOOK2)
    assert q["answer"]["answer_version"] == {
        "fingerprint": _fingerprint("600 минут"), "received_at": T2, "book_sha256": BOOK2}
    assert q["answer"]["answer_version"] != first


def test_retry_unparsed_same_text_new_book_gets_receipt_of_its_book():
    """Слово владельца 28.09 ~12:5x (Stage Н0-B run-0011): квитанция принадлежит
    книге РЕВИЗИИ ответа — выпуск сверяет book_sha256 с answers/rev-<revision>.
    Тот же текст новой книгой (новая ревизия) — квитанция новой книги. Прежнее
    ожидание «первая квитанция неизменна между ревизиями» противоречило закону
    выпуска (409 source-answer-evidence) и снято; внутри одной ревизии
    квитанция по-прежнему не передатируется (test_repeated_processing_…)."""
    doc, q, _ = _case(count=1)
    answers.apply_answers(doc, {q["id"]: "не знаю"}, received_at=T1, book_sha256=BOOK1)
    answers.apply_answers(doc, {q["id"]: "не   знаю"}, received_at=T2, book_sha256=BOOK2)
    assert q["answer"]["answer_version"] == {
        "fingerprint": _fingerprint("не знаю"), "received_at": T2, "book_sha256": BOOK2}


def test_stage_0011_rejected_then_applied_same_text_receipt_matches_its_revision():
    """Stage Н0-B run-0011: «600 минут» книгой rev-A «не разобран» (отвергнут), тот
    же текст книгой rev-B применён — квитанция ответа = книга rev-B (иначе выпуск
    409 «книга / квитанция не соответствуют применённой ревизии»). Инверсия —
    прежнее условие сохраняло квитанцию rev-A."""
    doc, q, _ = _case(count=1)
    answers.apply_answers(doc, {q["id"]: "600 минут"}, received_at=T1, book_sha256=BOOK1)
    q["answer"]["status"] = "не разобран"            # ответ rev-A отвергнут
    answers.apply_answers(doc, {q["id"]: "600 минут"}, received_at=T2, book_sha256=BOOK2)
    a = q["answer"]
    assert a["revision"] == doc["meta"]["revision"]
    assert a["answer_version"]["book_sha256"] == BOOK2
    assert a["answer_version"]["received_at"] == T2


def test_book_receipt_survives_file_replacement_after_reading(tmp_path):
    doc, q = _doc()
    path = _book_with(tmp_path, doc, {q["id"]: "600 минут"})
    original_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    reading = answers.read_book(path, doc)
    path.write_bytes(b"not the book which was read")
    answers.apply_answers(doc, reading.answers, refused=reading.refused,
                          received_at=reading.received_at, book_sha256=reading.book_sha256)
    assert q["answer"]["answer_version"]["book_sha256"] == original_sha
    assert q["answer"]["answer_version"]["received_at"] == reading.received_at


def test_book_digest_and_parser_use_same_snapshot(tmp_path, monkeypatch):
    import openpyxl
    doc, q = _doc()
    path = _book_with(tmp_path, doc, {q["id"]: "600 минут"})
    original_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    loader = openpyxl.load_workbook
    def replace_before_parser(source, **kwargs):
        path.write_bytes(b"concurrent replacement")
        return loader(source, **kwargs)
    monkeypatch.setattr(openpyxl, "load_workbook", replace_before_parser)
    reading = answers.read_book(path, doc)
    assert reading.answers[q["id"]] == "600 минут"
    assert reading.book_sha256 == original_sha


def test_receipt_validation_happens_before_mutation():
    doc, q, _ = _case(count=1)
    before = copy.deepcopy(doc)
    with pytest.raises(ValueError, match="верси"):
        answers.apply_answers(doc, {q["id"]: "600 минут"}, book_sha256="not-a-digest")
    assert doc == before


@pytest.mark.parametrize("field,value", [
    ("fingerprint", "0" * 12), ("fingerprint", ["broken"]),
    ("received_at", "2026-09-14T10:00:00"), ("received_at", "not a date"),
    ("book_sha256", "short"), ("book_sha256", True),
])
def test_invalid_version_rejected_without_repair_or_mutation(field, value):
    doc, q, _ = _case(count=1)
    answers.apply_answers(doc, {q["id"]: "600 минут"}, received_at=T1, book_sha256=BOOK1)
    q["answer"]["answer_version"][field] = value
    saved = copy.deepcopy(doc)
    assert m.validate_model(doc)
    with pytest.raises(ValueError, match="верси"):
        answers.apply_answers(doc, {q["id"]: "600 минут"})
    assert doc == saved


def test_legacy_answer_load_adds_only_derivable_identity(tmp_path):
    doc, q, _ = _case(count=1)
    answers.apply_answers(doc, {q["id"]: "600 минут"})
    q["answer"].pop("answer_version", None)
    path = tmp_path / "model.json"
    m.dump_model(doc, path)
    got = m.load_model(path)
    ans = next(x for x in got["questions"] if x["id"] == q["id"])["answer"]
    assert ans.get("answer_version") == {
        "fingerprint": _fingerprint("600 минут"), "received_at": None, "book_sha256": None}
    assert "answer_version" not in q["answer"], "Loading must not mutate the original in-memory model"
