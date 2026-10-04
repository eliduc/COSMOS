"""Improvement-findings store for the COSMOS Evolution pipeline
(stored in the users.db SQLite file).

The Evolution pipeline keeps COSMOS improving itself: a nightly research
agent scans the literature and the ecosystem for candidate methods and
libraries (better pricing heuristics, new solver releases, promising
crew-pairing papers) and records each candidate here as a "finding".
The admin reviews findings and approves each phase through the Telegram
bot; an orchestrator then drives the approved work through the
Test -> Stage -> Prod phases, writing its progress and metrics back into
this store. Lifecycle:

    found              registered by the nightly agent, awaiting triage
    rejected           admin declined the finding (terminal)
    approved_for_test  admin green-lit an experimental integration
    testing            orchestrator is running the test phase
    test_improved      test metrics beat the baseline
    test_no_gain       test ran fine but showed no improvement
    test_failed        test phase errored out
    approved_for_stage admin green-lit the stage rollout
    staging            orchestrator is deploying to the stage machine
    stage_passed       stage validation succeeded
    stage_failed       stage validation failed
    approved_for_prod  admin green-lit the production rollout
    deploying          orchestrator is deploying to prod
    deployed           live in production (terminal)
    deploy_failed      prod deployment failed

Only `rejected` and `deployed` are terminal; every other status is a
finding still in flight. Duplicate findings are prevented by dedup_key
(normalized source URL, or the normalized title when there is no URL);
known_findings() feeds the nightly scan prompt so the agent skips items
that are already recorded.

Connections/transactions reuse the users.py helpers so the table lives in
the same database file and tests that monkeypatch users.USERS_DB_PATH
automatically redirect this store too. Loud failures, as everywhere:
unknown ids, bad statuses/phases and corrupt stored JSON all raise.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import users

STATUS_FOUND = "found"
STATUS_REJECTED = "rejected"
STATUS_APPROVED_FOR_TEST = "approved_for_test"
STATUS_TESTING = "testing"
STATUS_TEST_IMPROVED = "test_improved"
STATUS_TEST_NO_GAIN = "test_no_gain"
STATUS_TEST_FAILED = "test_failed"
STATUS_APPROVED_FOR_STAGE = "approved_for_stage"
STATUS_STAGING = "staging"
STATUS_STAGE_PASSED = "stage_passed"
STATUS_STAGE_FAILED = "stage_failed"
STATUS_APPROVED_FOR_PROD = "approved_for_prod"
STATUS_DEPLOYING = "deploying"
STATUS_DEPLOYED = "deployed"
STATUS_DEPLOY_FAILED = "deploy_failed"

STATUSES = (
    STATUS_FOUND,
    STATUS_REJECTED,
    STATUS_APPROVED_FOR_TEST,
    STATUS_TESTING,
    STATUS_TEST_IMPROVED,
    STATUS_TEST_NO_GAIN,
    STATUS_TEST_FAILED,
    STATUS_APPROVED_FOR_STAGE,
    STATUS_STAGING,
    STATUS_STAGE_PASSED,
    STATUS_STAGE_FAILED,
    STATUS_APPROVED_FOR_PROD,
    STATUS_DEPLOYING,
    STATUS_DEPLOYED,
    STATUS_DEPLOY_FAILED,
)

# A finding leaves the pipeline only when rejected or live in prod;
# every other status (including the failed ones) is still in flight —
# a failed test/stage/deploy awaits a retry or an explicit rejection.
TERMINAL_STATUSES = (STATUS_REJECTED, STATUS_DEPLOYED)

# Valid predecessor statuses for each pipeline action (t=test, s=stage,
# p=prod, x=reject). The SINGLE source for every approval surface
# (Telegram buttons AND the web API): the gate is what stops a stale or
# duplicate press from driving the same improvement into a second
# concurrent phase thread over the shared git working tree.
ACTION_VALID_FROM = {
    # A rejected finding can be re-tested: «отклонена» means the admin
    # looked and did not find it promising, but may return to it later.
    "t": (STATUS_FOUND, STATUS_REJECTED, STATUS_TEST_FAILED,
          STATUS_TEST_NO_GAIN),
    "s": (STATUS_TEST_IMPROVED, STATUS_TEST_NO_GAIN, STATUS_STAGE_FAILED),
    "p": (STATUS_STAGE_PASSED, STATUS_DEPLOY_FAILED),
    "x": tuple(s for s in STATUSES if s not in TERMINAL_STATUSES),
}

PHASE_TEST = "test"
PHASE_STAGE = "stage"
PHASES = (PHASE_TEST, PHASE_STAGE)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS improvements (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL,
    summary          TEXT NOT NULL,
    source_url       TEXT,
    integration_plan TEXT,
    expected_gain    TEXT,
    status           TEXT NOT NULL CHECK (status IN
                     ({", ".join(f"'{s}'" for s in STATUSES)})),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    branch           TEXT,
    test_metrics     TEXT,
    stage_metrics    TEXT,
    decided_by       TEXT,
    error            TEXT,
    dedup_key        TEXT NOT NULL UNIQUE
);
"""


class ImprovementNotFoundError(users.UsersError):
    """The referenced improvement finding does not exist."""


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    # Lazy migration: the analytical deploy review (LLM recommendation +
    # rationale) is stored as JSON in a column added to existing DBs. A
    # concurrent add loses the race harmlessly (duplicate column -> ignore).
    cols = [r[1] for r in conn.execute("PRAGMA table_info(improvements)")]
    if "review" not in cols:
        try:
            conn.execute("ALTER TABLE improvements ADD COLUMN review TEXT")
        except sqlite3.OperationalError:
            pass
    # Оценка эффективности публикации советником по решателям (кэш):
    # JSON {score, confidence, rationale, cache_key, ...}. Та же ленивая
    # миграция, что и review.
    if "impact" not in cols:
        try:
            conn.execute("ALTER TABLE improvements ADD COLUMN impact TEXT")
        except sqlite3.OperationalError:
            pass
    # Дата публикации ПЕРВОИСТОЧНИКА (ГГГГ[-ММ[-ДД]]), не дата находки
    # агентом: по ней сортируется вкладка «Алгоритмы». Источники: arXiv-id
    # из URL или ответ модели при оценке эффективности.
    if "published_at" not in cols:
        try:
            conn.execute(
                "ALTER TABLE improvements ADD COLUMN published_at TEXT")
        except sqlite3.OperationalError:
            pass


def _parse_metrics(raw: str | None, column: str, imp_id: Any) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # A corrupt store must be loud, never a silent None.
        raise RuntimeError(
            f"corrupt {column} JSON for improvement id={imp_id}: {exc}"
        ) from exc


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "summary": row["summary"],
        "source_url": row["source_url"],
        "integration_plan": row["integration_plan"],
        "expected_gain": row["expected_gain"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "branch": row["branch"],
        "test_metrics": _parse_metrics(row["test_metrics"],
                                       "test_metrics", row["id"]),
        "stage_metrics": _parse_metrics(row["stage_metrics"],
                                        "stage_metrics", row["id"]),
        "decided_by": row["decided_by"],
        "error": row["error"],
        "dedup_key": row["dedup_key"],
        "review": _parse_metrics(
            row["review"] if "review" in row.keys() else None,
            "review", row["id"]),
        "impact": _parse_metrics(
            row["impact"] if "impact" in row.keys() else None,
            "impact", row["id"]),
        "published_at": (row["published_at"]
                         if "published_at" in row.keys() else None),
    }


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string, got {value!r}")
    return value


def _get_row(conn: sqlite3.Connection, imp_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM improvements WHERE id = ?",
                       (int(imp_id),)).fetchone()
    if row is None:
        raise ImprovementNotFoundError(f"no such improvement: id={imp_id}")
    return row


def _dedup_key(title: str, source_url: str | None) -> str:
    if source_url is not None and not isinstance(source_url, str):
        raise ValueError(
            f"source_url must be a string or None, got {source_url!r}")
    if source_url and source_url.strip():
        return source_url.strip().lower()
    return "title:" + title.strip().lower()


def add_finding(title: str, summary: str, source_url: str | None = None,
                integration_plan: str | None = None,
                expected_gain: str | None = None,
                ) -> tuple[bool, dict[str, Any]]:
    """Idempotent registration of a candidate improvement (status: found).

    Returns (created, row). created is True only when a brand-new row was
    inserted — the caller's cue to notify the admin about a NEW finding.
    A duplicate (same dedup_key: same normalized source URL, or same
    normalized title for URL-less findings) returns the existing row
    as-is, whatever its status — a rejected finding stays rejected no
    matter how many times the nightly agent rediscovers it.
    """
    title = _require_text(title, "title")
    summary = _require_text(summary, "summary")
    key = _dedup_key(title, source_url)
    now = users._now_iso()
    with users._write_txn() as conn:
        _ensure_table(conn)
        row = conn.execute("SELECT * FROM improvements WHERE dedup_key = ?",
                           (key,)).fetchone()
        if row is not None:
            return False, _row_to_dict(row)
        cur = conn.execute(
            "INSERT INTO improvements"
            " (title, summary, source_url, integration_plan, expected_gain,"
            "  status, created_at, updated_at, dedup_key)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, summary, source_url, integration_plan, expected_gain,
             STATUS_FOUND, now, now, key))
        return True, _row_to_dict(_get_row(conn, cur.lastrowid))


def get(imp_id: int) -> dict[str, Any]:
    with users._write_txn() as conn:
        _ensure_table(conn)
        return _row_to_dict(_get_row(conn, imp_id))


def set_status(imp_id: int, status: str, decided_by: str | None = None,
               error: str | None = None,
               branch: str | None = None) -> dict[str, Any]:
    """Advance a finding through the pipeline (admin decision or
    orchestrator progress). Fields passed as None keep their stored
    values (COALESCE), so decided_by/branch survive later transitions."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    with users._write_txn() as conn:
        _ensure_table(conn)
        _get_row(conn, imp_id)
        conn.execute(
            "UPDATE improvements SET status = ?, updated_at = ?,"
            " decided_by = COALESCE(?, decided_by),"
            " error = COALESCE(?, error),"
            " branch = COALESCE(?, branch)"
            " WHERE id = ?",
            (status, users._now_iso(), decided_by, error, branch,
             int(imp_id)))
        return _row_to_dict(_get_row(conn, imp_id))


def attach_metrics(imp_id: int, phase: str, metrics: Any) -> dict[str, Any]:
    """Store the orchestrator's measurements for one phase.

    phase is "test" or "stage"; metrics is any JSON-serializable object
    (stored as JSON text in test_metrics/stage_metrics, returned parsed).
    """
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}, got {phase!r}")
    payload = json.dumps(metrics, ensure_ascii=False)
    column = f"{phase}_metrics"
    with users._write_txn() as conn:
        _ensure_table(conn)
        _get_row(conn, imp_id)
        conn.execute(
            f"UPDATE improvements SET {column} = ?, updated_at = ?"
            " WHERE id = ?",
            (payload, users._now_iso(), int(imp_id)))
        return _row_to_dict(_get_row(conn, imp_id))


def attach_review(imp_id: int, review: dict[str, Any]) -> dict[str, Any]:
    """Store the analytical deploy review (LLM recommendation + rationale)
    for an improvement, returned parsed on the row. Advisory only — it does
    not change the status or gate any action."""
    payload = json.dumps(review, ensure_ascii=False)
    with users._write_txn() as conn:
        _ensure_table(conn)
        _get_row(conn, imp_id)
        conn.execute(
            "UPDATE improvements SET review = ?, updated_at = ? WHERE id = ?",
            (payload, users._now_iso(), int(imp_id)))
        return _row_to_dict(_get_row(conn, imp_id))


def attach_impact(imp_id: int, impact: dict[str, Any]) -> dict[str, Any]:
    """Кэш оценки эффективности публикации (советник по решателям).

    Совещательная величина: статус находки не меняет и действий не
    гейтит. `cache_key` внутри оценки — ключ инвалидации (хэш содержимого
    находки + версия скилла советника): пересчёт нужен только при смене
    того или другого."""
    payload = json.dumps(impact, ensure_ascii=False)
    with users._write_txn() as conn:
        _ensure_table(conn)
        _get_row(conn, imp_id)
        conn.execute(
            "UPDATE improvements SET impact = ?, updated_at = ? WHERE id = ?",
            (payload, users._now_iso(), int(imp_id)))
        return _row_to_dict(_get_row(conn, imp_id))


# Каталожная часть находки — ОБЩЕСИСТЕМНЫЙ факт о публикации: одинаков на
# всех контурах и синхронизируется реестром (tools/lab_registry.py). Всё
# остальное — status/branch/*_metrics/decided_by/error — состояние КОНВЕЙЕРА
# на конкретном контуре: своё у каждого и никогда не переносится, иначе
# импорт затрёт решение оператора (отклонённую находку, ветку с метриками).
CATALOG_FIELDS = ("title", "summary", "source_url",
                  "integration_plan", "expected_gain")


def update_catalog(imp_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    """Правка каталожных полей находки; конвейерные поля неприкосновенны.

    Единственная точка, где реестр меняет уже существующую запись. Список
    разрешённых колонок — белый, а не чёрный: попытка передать `status`,
    `branch`, метрики или что-либо неизвестное — ValueError, а не тихое
    игнорирование. `title` и `summary` обязаны остаться непустыми;
    `dedup_key` не меняется никогда — иначе запись потеряет тождество и
    следующий импорт создаст дубль."""
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty dict")
    unknown = [k for k in fields if k not in CATALOG_FIELDS]
    if unknown:
        raise ValueError(
            f"only catalog fields may be updated {CATALOG_FIELDS}, "
            f"refused: {sorted(unknown)}")
    for key in ("title", "summary"):
        if key in fields:
            _require_text(fields[key], key)
    sets = ", ".join(f"{k} = ?" for k in fields)
    args = [fields[k] for k in fields] + [users._now_iso(), int(imp_id)]
    with users._write_txn() as conn:
        _ensure_table(conn)
        _get_row(conn, imp_id)
        conn.execute(
            f"UPDATE improvements SET {sets}, updated_at = ? WHERE id = ?",
            args)
        return _row_to_dict(_get_row(conn, imp_id))


def set_published(imp_id: int, published: str) -> dict[str, Any]:
    """Дата публикации первоисточника (ГГГГ / ГГГГ-ММ / ГГГГ-ММ-ДД).

    Совещательное поле для сортировки «по дате публикации»; статус не
    меняет. Формат проверяет писатель (solver_advisor.normalize_published
    / published_from_url) — здесь только непустая строка."""
    published = _require_text(published, "published")
    with users._write_txn() as conn:
        _ensure_table(conn)
        _get_row(conn, imp_id)
        conn.execute(
            "UPDATE improvements SET published_at = ?, updated_at = ?"
            " WHERE id = ?",
            (published.strip(), users._now_iso(), int(imp_id)))
        return _row_to_dict(_get_row(conn, imp_id))


def known_findings() -> list[dict[str, Any]]:
    """(title, source_url) of EVERY recorded finding, any status.

    The nightly scan prompt includes this list so the research agent
    skips items COSMOS already knows about — including rejected ones."""
    with users._write_txn() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT title, source_url FROM improvements ORDER BY id"
        ).fetchall()
    return [{"title": r["title"], "source_url": r["source_url"]}
            for r in rows]


def counts_by_status() -> dict[str, int]:
    """status -> number of findings, for summary screens.

    Deliberately NOT len(list_all()): the loops overview needs two numbers
    and would otherwise drag every row's test_metrics/stage_metrics/review
    blob across just to count them."""
    with users._write_txn() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM improvements"
            " GROUP BY status").fetchall()
    return {r["status"]: int(r["n"]) for r in rows}


def newest_created_at() -> str | None:
    """Время самой свежей находки, или None на пустой таблице.

    Тоже НЕ через list_all(): та материализует каждую строку целиком и по
    построению ГРОМКО падает на испорченном JSON в метриках или ревью
    («A corrupt store must be loud»). Для одной даты это и лишнее чтение,
    и лишний способ уронить сводку контуров: одна битая строка обрушила бы
    страницу целиком, все девять контуров."""
    with users._write_txn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT MAX(created_at) AS newest FROM improvements").fetchone()
    return (row["newest"] or None) if row else None


def open_count() -> int:
    """Findings still in flight — everything outside TERMINAL_STATUSES.

    Mirrors ACTION_VALID_FROM["x"]: a finding is «в работе» until it is
    either deployed or explicitly rejected, failed phases included."""
    counts = counts_by_status()
    return sum(n for s, n in counts.items() if s not in TERMINAL_STATUSES)


def list_all(status: str | None = None) -> list[dict[str, Any]]:
    """All findings newest-first, optionally filtered by exact status."""
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    with users._write_txn() as conn:
        _ensure_table(conn)
        if status is None:
            rows = conn.execute(
                "SELECT * FROM improvements"
                " ORDER BY created_at DESC, id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM improvements WHERE status = ?"
                " ORDER BY created_at DESC, id DESC",
                (status,)).fetchall()
    return [_row_to_dict(r) for r in rows]
