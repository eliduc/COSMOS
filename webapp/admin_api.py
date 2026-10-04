"""Admin API for the user catalog (admin-only APIRouter under /api/admin).

Kept out of app.py on purpose: the tenancy work edits the instance/job/
result routes there, so the whole admin surface lives in this router and
app.py only does `app.include_router(admin_api.router)`.

Authorization: the auth middleware has already placed the verified
identity in request.state.user; every route here additionally requires
role=admin (403 for role=user, 401 if the middleware let an anonymous
request through — which it should not).

Every mutation is audit-logged to webapp/config/admin-audit.log
(timestamp, actor, action, target, outcome). AUDIT_LOG_PATH is a module
variable so tests point it at a tmp file.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request

from . import users
from .registry import REPO_ROOT

AUDIT_LOG_PATH: Path = REPO_ROOT / "webapp" / "config" / "admin-audit.log"

_audit_lock = threading.Lock()

router = APIRouter(prefix="/api/admin")


def _require_admin(request: Request) -> dict:
    """The acting identity, or 401/403 (middleware fills request.state.user)."""
    user = getattr(request.state, "user", None)
    if not isinstance(user, dict) or not user.get("email"):
        raise HTTPException(401, "authentication required")
    if user.get("role") != users.ROLE_ADMIN:
        raise HTTPException(403, "admin role required")
    return user


def _audit(actor: str, action: str, target: str, outcome: str,
           detail: str = "") -> None:
    """Append one audit line; an unwritable audit log is a loud 500."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (f"{ts}\tactor={actor}\taction={action}\ttarget={target}"
            f"\toutcome={outcome}" + (f"\t{detail}" if detail else "") + "\n")
    with _audit_lock:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)


def _catalog_call(actor: str, action: str, target: str, fn, detail: str = ""):
    """Run one catalog mutation, mapping typed errors to HTTP + audit."""
    try:
        result = fn()
    except ValueError as exc:
        _audit(actor, action, target, "rejected", f"error={exc}")
        raise HTTPException(422, str(exc)) from exc
    except users.UserExistsError as exc:
        _audit(actor, action, target, "rejected", f"error={exc}")
        raise HTTPException(409, str(exc)) from exc
    except users.LastAdminError as exc:
        _audit(actor, action, target, "rejected", f"error={exc}")
        raise HTTPException(409, str(exc)) from exc
    except users.UserNotFoundError as exc:
        _audit(actor, action, target, "rejected", f"error={exc}")
        raise HTTPException(404, str(exc)) from exc
    _audit(actor, action, target, "ok", detail)
    return result


@router.get("/users")
def admin_list_users(request: Request):
    _require_admin(request)
    return {"users": users.list_all(), "summary": users.stats_summary()}


@router.post("/users", status_code=201)
def admin_add_user(request: Request, body: dict = Body(...)):
    actor = _require_admin(request)
    email = body.get("email")
    role = body.get("role", users.ROLE_USER)
    if not isinstance(email, str):
        raise HTTPException(422, "'email' must be a string")
    if not isinstance(role, str):
        raise HTTPException(422, "'role' must be a string")
    user = _catalog_call(actor["email"], "add_user", email,
                         lambda: users.add(email, role, by=actor["email"]),
                         detail=f"role={role}")
    return {"user": user}


@router.patch("/users/{email}")
def admin_patch_user(email: str, request: Request, body: dict = Body(...)):
    actor = _require_admin(request)
    role = body.get("role")
    status = body.get("status")
    if role is None and status is None:
        raise HTTPException(422, "provide 'role' and/or 'status'")
    user = None
    if role is not None:
        if not isinstance(role, str):
            raise HTTPException(422, "'role' must be a string")
        user = _catalog_call(actor["email"], "set_role", email,
                             lambda: users.set_role(email, role),
                             detail=f"role={role}")
    if status is not None:
        if not isinstance(status, str):
            raise HTTPException(422, "'status' must be a string")
        user = _catalog_call(actor["email"], "set_status", email,
                             lambda: users.set_status(email, status),
                             detail=f"status={status}")
    return {"user": user}


@router.delete("/users/{email}")
def admin_delete_user(email: str, request: Request):
    actor = _require_admin(request)
    _catalog_call(actor["email"], "delete_user", email,
                  lambda: users.delete(email))
    return {"ok": True}


@router.get("/stats")
def admin_stats(request: Request):
    _require_admin(request)
    return users.stats_summary()


# COSMOS Evolution lab: the read-only findings list for the admin UI (the
# «Лаборатория» tab). Contract path is GET /api/evolution — outside the
# /api/admin prefix — so it lives on its own router; same admin gate.
evolution_router = APIRouter(prefix="/api")


@evolution_router.get("/evolution")
def admin_list_evolution(request: Request):
    _require_admin(request)
    from . import evolution  # lazy: keep the admin router import-light
    from . import solver_advisor
    # scoring_allowed сообщает интерфейсу, показывать ли кнопку «Оценить»:
    # балл считает ровно один контур, остальные получают его реестром.
    allowed, reason = solver_advisor.scoring_allowed()
    return {"improvements": evolution.list_all(),
            "scoring_allowed": allowed,
            "scoring_refused_reason": reason}


@evolution_router.post("/evolution/scan")
def admin_trigger_scan(request: Request):
    """Manual «Начать R&D» trigger: launch a research scan off-schedule.
    Admin-only; a no-op 409 when evolution is disabled, a scan is already
    running, or a pipeline phase is active (a scan must not reset the
    phase's live feed)."""
    actor = _require_admin(request)
    from . import evolution_runner
    if not evolution_runner.enabled():
        raise HTTPException(
            409, "COSMOS Evolution is disabled on this server "
                 "(CREW_EVOLUTION_ENABLED is not set)")
    started = evolution_runner.trigger_scan()
    if not started:
        raise HTTPException(
            409, "a research scan is already running, or a pipeline phase "
                 "is active")
    _audit(actor["email"], "evolution_scan", "-", "ok")
    return {"ok": True, "started": True}


# Web-side pipeline buttons (mirror of the Telegram ones); the wire names
# map onto the same one-letter actions the Telegram callbacks use.
_EVO_ACTIONS = {"test": "t", "stage": "s", "prod": "p", "reject": "x"}


@evolution_router.post("/evolution/{imp_id}/action")
def admin_evolution_action(imp_id: int, request: Request,
                           body: dict = Body(...)):
    """One pipeline decision from the «Лаборатория» table: launch the
    Test/Stage/Prod phase for a finding or reject it. Same transition
    gate as the Telegram buttons (evolution.ACTION_VALID_FROM), so a
    stale press can never start a second concurrent phase."""
    actor = _require_admin(request)
    wire = str((body or {}).get("action") or "")
    action = _EVO_ACTIONS.get(wire)
    if action is None:
        raise HTTPException(400, f"unknown action {wire!r}")
    from . import evolution_runner
    # Запуск фазы Test = запуск агента внедрения. Если среда не
    # изолирована, решение не принимается за администратора: возвращаем
    # перечень препятствий, интерфейс показывает предупреждение и
    # присылает повтор с подтверждением.
    confirmed = bool((body or {}).get("confirm_unisolated"))
    # Контрподпись человека для фаз Stage/Prod (аудит R10, P0-04): токен
    # выписывает оператор tools/countersign.py по секрету, которого нет у
    # автоматики; здесь он только передаётся дальше — сервер сверит.
    token = str((body or {}).get("countersign") or "").strip() or None
    res = evolution_runner.approve_action(imp_id, action, actor["email"],
                                          acknowledged_unisolated=confirmed,
                                          countersign=token)
    if not res["ok"]:
        if res["error"] == "not_found":
            raise HTTPException(404, "improvement not found")
        if res["error"] == "isolation_unconfirmed":
            _audit(actor["email"], f"evolution_{wire}", str(imp_id),
                   "isolation_unconfirmed")
            raise HTTPException(409, {"error": "isolation_unconfirmed",
                                      "problems": res.get("problems") or []})
        if res["error"] == "countersign_required":
            _audit(actor["email"], f"evolution_{wire}", str(imp_id),
                   "countersign_required")
            raise HTTPException(409, {
                "error": "countersign_required",
                "message": res.get("message") or ""})
        raise HTTPException(
            409, f"action unavailable from status «{res.get('current')}»")
    _audit(actor["email"], f"evolution_{wire}", str(imp_id),
           "ok_unisolated_confirmed" if confirmed else "ok")
    return {"ok": True, "status": res["status"],
            "unisolated_confirmed": confirmed}


@evolution_router.post("/evolution/{imp_id}/stop")
def admin_evolution_stop(imp_id: int, request: Request):
    """Stop button (item 1): interrupt a running Test phase. The phase
    salvages whatever instances already finished, compares them to the
    baseline, runs the analytical review and reports partial results.
    A no-op 409 when the improvement is not currently testing."""
    actor = _require_admin(request)
    from . import evolution_runner
    if not evolution_runner.request_stop(imp_id):
        raise HTTPException(409, "improvement is not currently testing")
    _audit(actor["email"], "evolution_stop", str(imp_id), "ok")
    return {"ok": True}


@evolution_router.get("/evolution/{imp_id}/report")
def admin_evolution_report(imp_id: int, request: Request):
    """Full Test comparison report + analytical review for the detail panel
    (item 3), so the admin can decide on deployment consciously."""
    _require_admin(request)
    from . import evolution, evolution_runner
    try:
        return evolution_runner.get_test_report(imp_id)
    except evolution.ImprovementNotFoundError:
        raise HTTPException(404, "improvement not found")


@evolution_router.get("/evolution/scan-activity")
def admin_scan_activity(request: Request, since: int = 0):
    """Live feed of what the research agent is doing (search queries,
    fetched URLs, reasoning, findings). The «Лаборатория» tab polls this
    with the last seq it has so only new events come back."""
    _require_admin(request)
    from . import evolution_runner
    return evolution_runner.get_scan_activity(since)


@evolution_router.post("/evolution/score")
def admin_evolution_score(request: Request, body: dict = Body(default={})):
    """Батч-оценка эффективности находок вкладки «Алгоритмы» советником
    по решателям (кэш по содержимому + версии скилла). Один прогон за
    раз; интерфейс поллит score-status. Admin-only, audited."""
    actor = _require_admin(request)
    from . import llm_backend, solver_advisor
    backend = str((body or {}).get("backend") or "cloud")
    if backend not in llm_backend.BACKENDS:
        raise HTTPException(400, f"unknown backend {backend!r}")
    # Контур-владелец оценки один (внешний, установка Test): батч пишет
    # результат безусловно, поэтому запуск на внутреннем контуре стёр бы
    # синхронизированные баллы отказом модели. Гейт держим и на сервере —
    # скрытая кнопка не защищает от прямого запроса к API.
    allowed, reason = solver_advisor.scoring_allowed()
    if not allowed:
        _audit(actor["email"], "evolution_impact_score", "-", "refused",
               reason[:200])
        raise HTTPException(409, reason)
    res = solver_advisor.start_impact_scoring(backend)
    if res.get("already_running"):
        _audit(actor["email"], "evolution_impact_score", "-", "busy",
               f"backend={backend}")
        raise HTTPException(409, "an impact scoring run is already running")
    _audit(actor["email"], "evolution_impact_score", "-", "started",
           f"backend={backend}")
    return res


@evolution_router.get("/evolution/score-status")
def admin_evolution_score_status(request: Request):
    """Poll target for a running impact-scoring batch."""
    _require_admin(request)
    from . import solver_advisor
    return solver_advisor.impact_status()


# --------------------------- solver-advisor knowledge base (admin surface)

@evolution_router.get("/solver-advisor/skill")
def admin_solver_skill(request: Request):
    """Current skill document (Markdown) + provenance meta for the KB tab."""
    _require_admin(request)
    from . import solver_advisor
    return solver_advisor.load_skill()


@evolution_router.get("/solver-advisor/backends")
def admin_solver_backends(request: Request):
    """Reachability of the LLM backends for the KB/consult UI."""
    _require_admin(request)
    from . import llm_backend
    return llm_backend.available()


@evolution_router.get("/solver-advisor/build-status")
def admin_solver_build_status(request: Request):
    """Poll target for a running skill rebuild."""
    _require_admin(request)
    from . import solver_advisor
    return solver_advisor.skill_build_status()


@evolution_router.post("/solver-advisor/build")
def admin_solver_build(request: Request, body: dict = Body(default={})):
    """Rebuild the skill from PUBLIC benchmark evidence (external contour;
    cloud backend runs with cwd inside the Test checkout). One build at a
    time; the UI polls build-status. Admin-only, audited."""
    actor = _require_admin(request)
    from . import llm_backend, solver_advisor
    backend = str((body or {}).get("backend") or "cloud")
    if backend not in llm_backend.BACKENDS:
        raise HTTPException(400, f"unknown backend {backend!r}")
    res = solver_advisor.start_skill_build(backend)
    if res.get("already_running"):
        _audit(actor["email"], "solver_skill_build", "-", "busy",
               f"backend={backend}")
        raise HTTPException(409, "a skill build is already running")
    _audit(actor["email"], "solver_skill_build", "-", "started",
           f"backend={backend}")
    return res
