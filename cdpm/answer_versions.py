"""Persist receipt identity without inventing legacy time or book provenance."""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _time_ok(value) -> bool:
    if value is None:
        return True  # migrated receipt: this fact is not recoverable from raw
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def context(received_at=None, book_sha256=None) -> dict:
    at = now() if received_at is None else received_at
    if not _time_ok(at):
        raise ValueError("версия ответа: время получения должно содержать часовой пояс")
    if book_sha256 is not None and (
            not isinstance(book_sha256, str) or
            re.fullmatch(r"[0-9a-f]{64}", book_sha256) is None):
        raise ValueError("версия ответа: отпечаток книги должен быть SHA-256")
    return {"received_at": at, "book_sha256": book_sha256}


def problems(answer) -> list[str]:
    if not isinstance(answer, dict) or "answer_version" not in answer:
        return []  # old in-memory documents remain readable
    from cdpm.provenance import answer_version_fingerprint
    version = answer["answer_version"]
    if not isinstance(version, dict):
        return ["версия ответа должна быть объектом"]
    out = []
    if set(version) != {"fingerprint", "received_at", "book_sha256"}:
        out.append("версия ответа должна содержать fingerprint, received_at и book_sha256")
    fingerprint = version.get("fingerprint")
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{12}", fingerprint) is None:
        out.append("версия ответа: неверный отпечаток текста")
    elif fingerprint != answer_version_fingerprint(answer.get("raw")):
        out.append("версия ответа: отпечаток не соответствует дословному тексту")
    if not _time_ok(version.get("received_at")):
        out.append("версия ответа: неверное время получения или отсутствует часовой пояс")
    book = version.get("book_sha256")
    if book is not None and (not isinstance(book, str) or re.fullmatch(r"[0-9a-f]{64}", book) is None):
        out.append("версия ответа: неверный SHA-256 исходной книги")
    return out


def document_problems(doc) -> list[str]:
    questions = doc.get("questions", []) if isinstance(doc, dict) else []
    if not isinstance(questions, list):
        return []
    return [f"{q.get('id', '?')}: {problem}"
            for q in questions if isinstance(q, dict)
            for problem in problems(q.get("answer"))]


def stamp(answer: dict, previous, receipt: dict) -> None:
    if answer.get("closed_by") == "code":
        return  # a code resolution is not a newly received human answer
    from cdpm.provenance import answer_version_fingerprint
    fingerprint = answer_version_fingerprint(answer.get("raw"))
    old = previous.get("answer_version") if isinstance(previous, dict) else None
    # Квитанция принадлежит книге ревизии ОТВЕТА: выпуск сверяет
    # answer_version.book_sha256 с answers/rev-<answer.revision> (source_answer_
    # evidence._assemble). Прежняя квитанция сохраняется лишь внутри той же
    # ревизии (повтор, обработчик, смена статуса); тот же текст новой книгой —
    # квитанция новой книги (Stage Н0-B run-0011: «600 мин» rev-002 «не разобран»
    # → rev-003 применён с квитанцией rev-002 → 409 source-answer-evidence).
    same_revision = (isinstance(previous, dict)
                     and previous.get("revision") == answer.get("revision"))
    if (isinstance(old, dict) and old.get("fingerprint") == fingerprint
            and same_revision and not problems(previous)):
        answer["answer_version"] = copy.deepcopy(old)
    else:
        answer["answer_version"] = {"fingerprint": fingerprint, **receipt}


def migrate_document(doc) -> None:
    """Only derive the content identity; unavailable receipt facts remain null."""
    from cdpm.provenance import answer_version_fingerprint
    questions = doc.get("questions", []) if isinstance(doc, dict) else []
    if not isinstance(questions, list):
        return
    for q in questions:
        answer = q.get("answer") if isinstance(q, dict) else None
        if (isinstance(answer, dict) and "answer_version" not in answer
                and isinstance(answer.get("raw"), str) and answer["raw"]
                and answer.get("closed_by") != "code"):
            answer["answer_version"] = {
                "fingerprint": answer_version_fingerprint(answer["raw"]),
                "received_at": None, "book_sha256": None,
            }
