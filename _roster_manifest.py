"""Active-roster manifest: неизменяемая запись публикации ростера + активный
указатель (аудит-2, P1-06/P1-13).

Двухуровневая схема:
  * per-run манифест — final-report/runs/roster-manifests/<label>/<run_id>.json,
    пишется публикующим процессом ПОСЛЕ артефактов, никогда не перезаписывается;
  * активный указатель — final-report/active_roster_<label>.json — атомарная
    самодостаточная копия промотированного per-run манифеста с блоком
    `promotion`. Замена указателя = промоция.

Политика: обычный stage-2 и repair авто-промотируются публикующим процессом
(поведение «последняя публикация активна» сохраняется байт-в-байт по UX);
multirole — только явным действием (API), и только при result_status == "OK".

Хэши считает писатель (у него входы в руках), проверяет читатель
(webapp/active_roster.resolve) — fail-closed при любом несовпадении.
Артефакты БЕЗ манифеста читаются легальным фолбэком со строгой пометкой
"legacy" — существующие метки работают без миграции.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
import _data_paths
FINAL_REPORT = _data_paths.results_dir(create=False)

SCHEMA = "active-roster/v1"


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def sha256_canon(obj) -> str:
    """Канонический SHA-256 JSON-объекта (sort_keys, компактные разделители)."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()


def run_manifest_dir(label: str) -> Path:
    return FINAL_REPORT / "runs" / "roster-manifests" / label


def active_path(label: str) -> Path:
    return FINAL_REPORT / f"active_roster_{label}.json"


def _rel(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(HERE).as_posix()
    except ValueError:
        return str(path)


def build_run_manifest(*, label: str, mode: str, engine: str = "timefold",
                       result_status: str, payload_path: Path,
                       rules_snapshot: dict,
                       assignment_path: Path, solution_path: Path | None,
                       catalog_path: Path | None = None,
                       catalogs: dict | None = None,
                       trajectory_report: dict | None = None,
                       composition_report: dict | None = None,
                       multirole: dict | None = None,
                       limitations: list | None = None,
                       inst: int | None = None,
                       seed=None, budget_s=None,
                       job_id: str | None = None) -> dict:
    run_id = os.environ.get("CREW_RUN_ID")
    return {
        "schema": SCHEMA,
        "label": label,
        "inst": inst,
        "mode": mode,
        "engine": engine,
        "run_id": run_id or uuid.uuid4().hex[:12],
        "run_id_source": "env" if run_id else "self",
        # Личность ПОСТАНОВКИ (аудит R10, P0-03): производный артефакт
        # связывается с единым манифестом прогона, а не только с run_id.
        "problem_manifest_id":
            os.environ.get("CREW_PROBLEM_MANIFEST_ID") or None,
        "job_id": job_id,
        "published_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed": seed, "budget_s": budget_s,
        "result_status": result_status,
        "inputs": {
            "payload_path": _rel(payload_path),
            "payload_sha256": sha256_file(Path(payload_path)),
            "rules_sha256": sha256_canon(rules_snapshot),
            "rules_snapshot": dict(rules_snapshot),
            "catalog_path": _rel(catalog_path) if catalog_path else None,
            "catalog_sha256":
                sha256_file(Path(catalog_path)) if catalog_path else None,
            "catalogs": catalogs,
        },
        "artifacts": {
            "assignment_path": _rel(assignment_path),
            "assignment_sha256": sha256_file(Path(assignment_path)),
            "solution_path": _rel(solution_path) if solution_path else None,
            "solution_sha256":
                sha256_file(Path(solution_path)) if solution_path else None,
        },
        "certificates": {
            "trajectory_sha256":
                sha256_canon(trajectory_report) if trajectory_report else None,
            "composition_sha256":
                sha256_canon(composition_report) if composition_report
                else None,
        },
        "multirole": multirole,
        "limitations": list(limitations or []),
    }


def _atomic_write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    for attempt in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.1)


def write_run_manifest(manifest: dict) -> Path:
    """Неизменяемая запись: существующий файл того же run_id не трогаем
    (повторная публикация одного прогона — законный no-op)."""
    path = run_manifest_dir(manifest["label"]) / f"{manifest['run_id']}.json"
    if not path.exists():
        _atomic_write(path, manifest)
    return path


def promote(manifest: dict, *, promoted_by: str, auto: bool,
            source_run_manifest: Path | None = None) -> Path:
    """Атомарная замена активного указателя копией per-run манифеста."""
    doc = dict(manifest)
    doc["promotion"] = {
        "promoted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "promoted_by": promoted_by,
        "auto": bool(auto),
        "source_run_manifest":
            _rel(source_run_manifest) if source_run_manifest else None,
    }
    path = active_path(manifest["label"])
    _atomic_write(path, doc)
    return path


def publish(manifest: dict, *, auto_promote: bool,
            promoted_by: str = "system(auto)") -> dict:
    """Полный порядок публикации: per-run манифест, затем (для stage2/repair)
    авто-промоция указателя ПОСЛЕДНЕЙ. Крах между шагами оставляет старый
    указатель, чьи хэши уже не сходятся с перезаписанными артефактами —
    читатель отвечает fail-closed «stale», смешанного состояния нет."""
    run_path = write_run_manifest(manifest)
    out = {"run_manifest": str(run_path), "promoted": False}
    if auto_promote:
        promote(manifest, promoted_by=promoted_by, auto=True,
                source_run_manifest=run_path)
        out["promoted"] = True
    return out
