"""Read a roster for source checking after the solver has lost its in-memory run.

Only a native result 404 enables recovery. An immutable, completed attempt in
the explicitly selected workdir must prove its package, COSMOS run, solver UUID,
certificate input and recorded solver responses. Mutable current deliveries and
global last reports are never recovery candidates. This does not recertify,
replay or change the original (possibly partial-cost) result.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import zipfile

from vmp.run import call_journal as journal


def _require(ok, reason):
    if not ok:
        raise ValueError("сохранённая выдача не доказана: " + reason)


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    _require(path.is_relative_to(root.resolve()), "путь вне снимка")
    return path


def _doc(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), path.name + " не объект")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _journal_response(directory: Path, rows: list, method: str, run_id: str):
    entries = [r for r in rows if r.get("kind") == "solver"
               and r.get("method") == method]
    _require(len(entries) == 1, "неоднозначный ответ журнала " + method)
    entry = entries[0]
    components = {"run_id": run_id}
    _require(entry.get("ok") is True
             and entry.get("input", {}).get("components") == components
             and entry.get("input", {}).get("fingerprint")
             == journal.fingerprint_of(components), "UUID/вход журнала " + method)
    name = entry.get("body")
    _require(isinstance(name, str) and Path(name).name == name,
             "путь тела журнала " + method)
    raw = journal.read_body(_inside(directory, name))
    _require(hashlib.sha256(raw).hexdigest() == entry.get("body_sha256"),
             "хеш тела журнала " + method)
    body = json.loads(raw)
    _require(isinstance(body, dict) and not body.get("error")
             and isinstance(body.get("response"), dict), "тело журнала " + method)
    return body["response"]


def persisted_roster(*, wd: Path, package_sha: str, cosmos_run: str,
                     solver_run_id: str) -> tuple[dict, dict, dict]:
    """Return (native-shaped result, applied readings, provenance), read-only.

    No search outside wd, no mtime/latest selection, no arbitrary JSON fallback.
    Missing legacy provenance is an explicit refusal, not a weaker certificate.
    """
    try:
        _require(wd is not None and bool(cosmos_run) and bool(solver_run_id)
                 and re.fullmatch(r"[0-9a-f]{64}", package_sha or ""),
                 "нет точного контекста пакета/прогона")
        wd = Path(wd).resolve(strict=True)
        state = _doc(_inside(wd, "state.json"))
        _require(state.get("package_sha") == package_sha, "пакет рабочего каталога")
        candidates = [r for r in state.get("attempts", []) if isinstance(r, dict)
                      and r.get("task") == "rostering"
                      and (r.get("summary") or {}).get("solver_run_id") == solver_run_id]
        _require(len(candidates) == 1, "точная попытка отсутствует или неоднозначна")
        rec = candidates[0]
        _require(rec.get("done") is True and rec.get("state") == "завершено"
                 and rec.get("package_sha") == package_sha and rec.get("run") == cosmos_run,
                 "пакет/прогон/завершение попытки")
        aid, relative = rec.get("id"), rec.get("catalog")
        _require(isinstance(aid, str) and re.fullmatch(r"[A-Za-z0-9_-]+", aid),
                 "идентификатор попытки")
        _require(relative in (f"attempts/{aid}/button", f"attempts/{aid}/button-on-pairing"),
                 "путь точной попытки")
        out = _inside(wd, relative)
        paths = {n: _inside(out, n) for n in (
            "package.zip", "delivery.json", "certificate.json", "solution.json",
            "solved.json", "applied-rules.json", "solver-status.json")}
        _require(_sha(paths["package.zip"]) == package_sha, "SHA пакета попытки")
        with zipfile.ZipFile(paths["package.zip"]) as archive:
            _require(archive.namelist().count("manifest.json") == 1, "manifest пакета")
            manifest_sha = hashlib.sha256(archive.read("manifest.json")).hexdigest()
        delivery = _doc(paths["delivery.json"])
        cert = _doc(paths["certificate.json"])
        solution = _doc(paths["solution.json"])
        solved = _doc(paths["solved.json"])
        applied = _doc(paths["applied-rules.json"])
        status = _doc(paths["solver-status.json"])
        _require((delivery.get("schedule") or {}).get("solver_run_id") == solver_run_id,
                 "UUID выдачи")
        summary, header = rec.get("summary") or {}, cert.get("certificate") or {}
        _require(delivery.get("certified") is True and delivery.get("certificate_gate") == []
                 and (delivery.get("result") or {}).get("feasibility") == "confirmed"
                 and summary.get("certified") is True and summary.get("certificate_gate") == 0
                 and header.get("certified") is True
                 and (header.get("result") or {}).get("feasibility") == "confirmed",
                 "сертификат/гейт допустимости")
        # Same canonical source digest as roster_certify.solution_dataset; this
        # reads the existing certificate, never invokes a certifier or model.
        source_name = f"кнопка {wd.name}/{solver_run_id}"
        source_sha = hashlib.sha256(json.dumps(
            solution, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        sources = [s for s in cert.get("sources", []) if s.get("sid") == "SOL"]
        _require(solution.get("solution") == source_name and len(sources) == 1
                 and sources[0].get("file") == source_name
                 and sources[0].get("sha256") == source_sha, "вход/хеш сертификата SOL")
        for trace in (solution.get("runInfo") or {}, cert.get("run_trace") or {}):
            _require(trace.get("package_fingerprint") == manifest_sha
                     and trace.get("source_run") == wd.name, "пакет сертификата")
        _require(applied.get("runId") == solver_run_id and status.get("runId") == solver_run_id
                 and status.get("state") == "готов", "UUID/готовность показаний")
        _require((applied.get("lastRun") or {}).get("packageFingerprint") == manifest_sha
                 and status.get("packageFingerprint") == manifest_sha, "пакет показаний")
        # solved.json has no UUID of its own. Match it and applied-rules to the
        # hash-checked responses recorded under this UUID in the SAME attempt.
        rec_journal = rec.get("journal") or {}
        _require(rec_journal.get("complete") is True and not rec_journal.get("degraded")
                 and rec_journal.get("dir") == relative + "/external-calls", "журнал попытки")
        directory = _inside(out, "external-calls")
        manifest = _doc(_inside(directory, "manifest.json"))
        _require(manifest.get("schema") == "cosmos-call-journal/v1"
                 and manifest.get("task") == "rostering"
                 and manifest.get("complete") is True and not manifest.get("degraded"),
                 "неполный журнал")
        index_path = _inside(directory, "index.jsonl")
        rows = [json.loads(s) for s in index_path.read_text(encoding="utf-8").splitlines() if s.strip()]
        _require(rows and all(isinstance(r, dict) for r in rows)
                 and rows[-1].get("kind") == "end" and rows[-1].get("complete") is True
                 and rows[-1].get("count") == len(rows) - 1, "неполный индекс журнала")
        _require(_journal_response(directory, rows, "result", solver_run_id) == solved,
                 "решение разошлось с журналом")
        _require(_journal_response(directory, rows, "applied", solver_run_id) == applied,
                 "показания разошлись с журналом")
        origin = {"origin": "persisted-certified-attempt", "attempt_id": aid,
                  "package_sha": package_sha, "cosmos_run": cosmos_run,
                  "solver_run_id": solver_run_id, "roster_path": str(paths["solved.json"]),
                  "certificate_sha256": _sha(paths["certificate.json"]),
                  "solution_sha256": _sha(paths["solution.json"]),
                  "solved_sha256": _sha(paths["solved.json"]),
                  "applied_rules_sha256": _sha(paths["applied-rules.json"]),
                  "journal_index_sha256": _sha(index_path)}
        return solved, applied, origin
    except (OSError, ValueError, TypeError, KeyError, AttributeError, zipfile.BadZipFile) as e:
        raise ValueError("не удалось подтвердить сохранённую выдачу: " + str(e)) from e


def roster_result(*, get, base: str, wd: Path, package_sha: str,
                  cosmos_run: str, solver_run_id: str) -> tuple[dict, dict | None, str | None, dict]:
    """Keep the live read contract; recover only an absent in-memory result."""
    response = get(f"{base}/schedules/run/{solver_run_id}/result", timeout=120.0)
    if response.status_code == 404:
        solved, applied, origin = persisted_roster(
            wd=wd, package_sha=package_sha, cosmos_run=cosmos_run, solver_run_id=solver_run_id)
        return solved, applied, None, origin
    response.raise_for_status()
    applied, why = None, None
    try:
        readings = get(f"{base}/schedules/run/{solver_run_id}/applied-rules", timeout=120.0)
        readings.raise_for_status()
        applied = readings.json()
    except Exception as e:  # unchanged: missing rest readings stay not_checkable
        why = f"показания прогона не получены: {e!s:.200}"
    return response.json(), applied, why, {"origin": "native-run", "solver_run_id": solver_run_id}
