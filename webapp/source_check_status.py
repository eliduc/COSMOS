"""Selected-solution identity for source-check status, protocol and UI waiting.

Global last files remain historical storage, never an unqualified answer about
the selected solution. No model, solver, network or filesystem writes here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ContextChanged(ValueError):
    pass


def context_key(context: dict | None) -> str | None:
    if not context:
        return None
    return hashlib.sha256(json.dumps(context, sort_keys=True,
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def current_context(task: str, *, cur=None) -> dict | None:
    from . import package_runs as pr
    import _data_paths
    try:
        cur = pr.current() if cur is None else cur
        if not isinstance(cur, dict) or task not in ("pairing", "rostering"):
            return None
        sha = cur.get("sha") or cur.get("sha256") or pr._sha256(Path(cur["path"]))
        wd = pr.workdir_of(cur, sha).resolve()
        run = (cur.get("run") or {}).get("run") or pr._holder_run(wd)
        state = pr.read_state(wd)
        if not run or state.get("package_sha") != sha:
            return None
        out = wd / "button-pairing" if task == "pairing" else pr.roster_delivery_dir(wd)
        if out is None:
            return None
        delivery = json.loads((out / "delivery.json").read_text(encoding="utf-8"))
        rid = (delivery.get("schedule") or {}).get("solver_run_id")
        attempts = [r for r in state.get("attempts", []) if isinstance(r, dict)
                    and r.get("task") == task and r.get("done") is True
                    and r.get("package_sha") == sha and r.get("run") == run
                    and rid and (r.get("summary") or {}).get("solver_run_id") == rid]
        if len(attempts) != 1 or not attempts[0].get("id"):
            return None
        return {"task": task, "package_sha": sha, "cosmos_run": run,
                "workdir": wd.relative_to(Path(_data_paths.data_root()).resolve()).as_posix(),
                "attempt_id": attempts[0]["id"],
                "solver_run_id": rid if task == "rostering" else None,
                "catalog_sha256": pr._sha256(out / "builder-catalog.json")
                    if task == "pairing" else None}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def matches(snapshot: dict, context: dict | None) -> bool:
    """One fail-closed predicate shared by every selected-solution reader.

    A legacy snapshot's exact UUID (or catalog SHA) may bind a unique attempt;
    an explicit attempt_id, when present, must also agree. Missing run/workdir
    identities are not inferred from global current state.
    """
    if not isinstance(snapshot, dict) or not context:
        return False
    fields = ("task", "package_sha", "cosmos_run", "workdir")
    identity = "solver_run_id" if context.get("task") == "rostering" else "catalog_sha256"
    if any(not context.get(k) or snapshot.get(k) != context[k] for k in (*fields, identity)):
        return False
    return not snapshot.get("attempt_id") or snapshot["attempt_id"] == context.get("attempt_id")


def selected_snapshot(task: str, expected_key: str | None = None) -> dict:
    import _data_paths
    context = current_context(task)
    key = context_key(context)
    if expected_key is not None and expected_key != key:
        raise ContextChanged("выбранное решение изменилось; откройте проверку заново")
    envelope = {"task": task, "context": context, "context_key": key}
    path = Path(_data_paths.data_root()) / "source-check" / task / "last.json"
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        snapshot = {}
    if not matches(snapshot, context):
        return {"status": "idle", "reason_code": "source-check-context-mismatch", **envelope}
    return {**snapshot, **envelope}
