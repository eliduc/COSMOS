"""Неизменяемая Raw-зона инстанса — этап Ф3 конвейера формализации
(2026-07-17, дорожная карта №7 документа COSMOS-DATA-FORMALIZATION-RU).

data/raw/<inst_id>/ хранит ТОЧНЫЕ байты, которые передал заказчик, плюс
manifest.json с отпечатками SHA-256, размерами и автором приёмки. Зона
write-once: исходники никогда не редактируются и не перезаписываются —
повторная запись для того же инстанса — громкий отказ (RawZoneError).
Конвертация всегда воспроизводима: канонический payload можно пересобрать
из Raw-зоны и сверить с отпечатком.

Пути приёмки, которые сюда пишут: универсальный импорт (commit) и
SSIM-импорт. Прямая загрузка канонического JSON (/api/instances/upload)
НЕ дублируется: сохранённый payload и есть источник этого пути.

The directory is git-ignored (data/raw/) and covered by the CR
implementer's diff-guard denylist (data/ prefix) — agents cannot touch it.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from datetime import datetime, timezone

from . import registry

RAW_DIR = registry.REPO_ROOT / "data" / "raw"
MANIFEST_NAME = "manifest.json"

_store_lock = threading.Lock()


class RawZoneError(ValueError):
    """Invalid raw-zone operation; message is safe to show the user."""


_EXT_RE = re.compile(r"[^a-z0-9]+")


def _safe_name(name: str, taken: set[str]) -> str:
    """Uploaded file names are untrusted: keep only the basename, pass the
    stem through the house transliterating slugify (Cyrillic survives as
    Latin, anything else hashes) and whitelist the extension chars."""
    base = str(name or "file").replace("\\", "/").rsplit("/", 1)[-1].strip()
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    safe_stem = registry.slugify_or_hash(stem or "file")
    safe_ext = _EXT_RE.sub("", ext.lower())[:10]
    cand = safe_stem + (("." + safe_ext) if safe_ext else "")
    i = 1
    while cand.lower() in taken or cand.lower() == MANIFEST_NAME:
        cand = f"{safe_stem}-{i}" + (("." + safe_ext) if safe_ext else "")
        i += 1
    taken.add(cand.lower())
    return cand


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _zone_dir(inst_id: str):
    """None for an id that could escape RAW_DIR — the caller resolved the
    id via the registry, so a traversal-looking id is simply 'no zone'."""
    if not inst_id or any(ch in inst_id for ch in ("/", "\\")) \
            or ".." in inst_id:
        return None
    return RAW_DIR / inst_id


def store(inst_id: str, files: list[dict], by: str) -> dict | None:
    """Write the source files + manifest for a NEW instance.
    `files`: [{"name": str, "data": bytes}]. Returns the manifest, or None
    when there is nothing to store (no zone is created)."""
    if not files:
        return None
    dest = _zone_dir(inst_id)
    if dest is None:
        raise RawZoneError(f"недопустимый идентификатор инстанса: {inst_id}")
    # Write-once is defined by a COMPLETE zone (a present manifest), not a
    # bare directory: a directory without a manifest is an aborted prior
    # attempt, safe to overwrite — otherwise a crash mid-store would brick
    # the (deterministic) instance id forever with a 409. The lock closes
    # the check-then-create race for the same id in this process.
    with _store_lock:
        if (dest / MANIFEST_NAME).exists():
            raise RawZoneError(
                f"raw-зона инстанса {inst_id} уже существует — исходники "
                "неизменяемы (write-once)")
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)   # incomplete attempt
        dest.mkdir(parents=True)
        try:
            taken: set[str] = set()
            recs = []
            for f in files:
                data = f.get("data")
                if not isinstance(data, (bytes, bytearray)):
                    raise RawZoneError(
                        f"файл {f.get('name')!r}: содержимое не байты")
                stored = _safe_name(str(f.get("name") or "file"), taken)
                (dest / stored).write_bytes(data)
                recs.append({
                    "name": str(f.get("name") or "file"),
                    "stored_as": stored,
                    "sha256": hashlib.sha256(bytes(data)).hexdigest(),
                    "bytes": len(data),
                })
            manifest = {"instance": inst_id, "created_at": _now(),
                        "by": by, "files": recs}
            tmp = dest / (MANIFEST_NAME + ".tmp")
            tmp.write_text(json.dumps(manifest, ensure_ascii=False,
                                      indent=1), encoding="utf-8")
            tmp.replace(dest / MANIFEST_NAME)
            return manifest
        except BaseException:
            # never leave a manifest-less orphan that would brick the id
            shutil.rmtree(dest, ignore_errors=True)
            raise


def manifest_for(inst_id: str) -> dict | None:
    """The stored manifest, or None when the instance has no raw zone
    (pre-feature instances, seed data, canonical-JSON uploads)."""
    dest = _zone_dir(inst_id)
    if dest is None:
        return None
    try:
        return json.loads((dest / MANIFEST_NAME).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"raw manifest unreadable for {inst_id}: {exc}") from exc


def discard(inst_id: str) -> None:
    """Rollback helper for a commit that failed AFTER store(): the only
    sanctioned way a raw zone disappears. Best-effort by design — the
    caller is already propagating the real error."""
    dest = _zone_dir(inst_id)
    if dest is not None:
        shutil.rmtree(dest, ignore_errors=True)
