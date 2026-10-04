# -*- coding: utf-8 -*-
"""Отказы службы: машинный код, слова, перечень проблем читателя.

Одна форма на все ручки (§3 проекта):

    {"detail": {"code": "<машинный код>", "words": "<словами>",
                "problems": [{"where","code","message","severity"}]?,
                "extra": {…}?}}

Наружу не уходит ни один абсолютный путь: имена файлов переводятся
относительно папки каталога (`relative_name`), полный текст исключения
остаётся в журнале процесса. Причина — витрина каталога стоит на
площадках и у заказчика: раскладка машины владельца не их дело.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

#: Коды, которые служба употребляет. Перечень закрыт: новый код —
#: правка контракта и стража документов T14, а не строка в обработчике.
CODES = (
    "bad-form", "submitter-not-allowed", "bad-filter",
    "read-only", "owner-only",
    "unknown-record", "unknown-key", "unknown-request", "unknown-version",
    "no-draft",
    # `duplicate-request` снят волной 17б (П0 §4.1, редакция «0-бис»):
    # открытая заявка по записи и иной канон под тем же номером — одно и
    # то же столкновение содержания, и код у него один — `request-exists`.
    "bad-transition", "drafts-pending",
    "nothing-to-assemble", "version-not-newer", "version-downgrade",
    "release-exists", "write-busy", "id-taken-by-request",
    "bindings-stop", "markers-missing", "git-dirty", "no-git",
    "executor-not-present",
    "invalid-draft", "invalid-catalog", "invalid-request",
    "invalid-single-file", "fingerprint-mismatch", "version-too-low",
    "words-required", "bad-cr-key", "closed-list",
    "catalog-unavailable", "ui-not-built",
    # Шаг 6: машинный канал заявок, закон номера, кандидат записи и две
    # предпроверки выпуска. Перечень остаётся закрытым — каждый код
    # назван в §2.1 контракта и сверяется стражем документов T14/С4.
    "machine-channel-off", "bad-token", "request-exists", "request-closed",
    "bad-request-id", "no-candidate", "candidate-mismatch",
    "candidate-pending", "executors-incomplete",
    # Пакет «паритет на временном каталоге»: двенадцатая предпроверка
    # выпуска (0.13). `parity-red` подтверждением НЕ снимается,
    # `parity-unconfirmed` («не судился» либо предупреждение судьи) —
    # снимается полем `confirm_parity: true`.
    "parity-red", "parity-unconfirmed",
)


class ServiceError(Exception):
    """Отказ службы словами. `problems` — Problem читателя (переводятся
    в объекты ответа), `extra` — машинные подробности для интерфейса."""

    def __init__(self, code: str, words: str, status: int = 400, *,
                 problems=None, extra: dict | None = None) -> None:
        super().__init__(words)
        self.code = str(code)
        self.words = str(words)
        self.status = int(status)
        self.problems = list(problems or ())
        self.extra = dict(extra or {})

    def detail(self) -> dict:
        out: dict = {"code": self.code, "words": self.words}
        if self.problems:
            out["problems"] = [problem_json(p) for p in self.problems]
        if self.extra:
            out["extra"] = self.extra
        return out

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail())


def problem_json(p) -> dict:
    """Problem читателя наружу — четыре поля и ничего больше.

    Принимает и уже переведённый словарь: перевод обязан быть
    идемпотентным, иначе двойной вызов молча отдал бы четыре пустых
    поля вместо адреса и слов."""
    if isinstance(p, dict):
        return {"where": str(p.get("where", "")),
                "code": str(p.get("code", "")),
                "message": str(p.get("message", "")),
                "severity": str(p.get("severity", "error"))}
    return {"where": str(getattr(p, "where", "")),
            "code": str(getattr(p, "code", "")),
            "message": str(getattr(p, "message", "")),
            "severity": str(getattr(p, "severity", "error"))}


def relative_name(path, base) -> str:
    """Имя файла относительно папки каталога; вне её — только имя файла.
    Абсолютных путей наружу не бывает."""
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(str(path)).name


def scrub(text: str, *paths) -> str:
    """Убрать из слов отказа абсолютные пути машины: каждый известный
    путь заменяется своим последним именем. Читателя это не меняет — он
    честно называет файл, — но наружу уходит `families.json`, а не
    `C:\\...\\catalog\\families.json`."""
    out = str(text)
    for p in paths:
        if not p:
            continue
        full = str(Path(p))
        if full and full in out:
            out = out.replace(full, Path(full).name)
        alt = full.replace("\\", "/")
        if alt and alt in out:
            out = out.replace(alt, Path(full).name)
    return out


def catalog_unavailable(exc: Exception, catalog_dir) -> ServiceError:
    """Любой CatalogError читателя — 503 словами читателя, но без путей."""
    words = scrub(f"{type(exc).__name__}: {exc}", catalog_dir)
    return ServiceError("catalog-unavailable",
                        f"каталог шаблонов недоступен: {words}", 503)
