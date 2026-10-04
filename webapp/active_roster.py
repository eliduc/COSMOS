"""Резолвер активного ростера (аудит-2, P1-06/P1-13).

Единая воронка чтения ростерного артефакта для доски, CSV, кабинета,
fair-share и результатов:

  * есть активный указатель (final-report/active_roster_<label>.json) —
    проверяем ВСЕ отпечатки (payload, правила, каталог(и), байты
    артефактов) и отдаём документ по artifacts.assignment_path манифеста;
    любой мисматч = fail-closed «stale» с перечнем расхождений;
  * указателя нет — легальный фолбэк на исторический
    gerad_stage2_assignment_<label>.json с пометкой "legacy" (существующие
    метки работают без миграции);
  * при multirole-OK новее активного указателя — пометка
    "unpromoted_multirole_ok" (риск «предъявить не тот наряд» из P1-06
    становится видимым, а не молчаливым).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import registry

import rules_config
from _roster_manifest import active_path, sha256_canon, sha256_file

REPO_ROOT = registry.REPO_ROOT


def _load_json(path: Path):
    # mtime-кэшированное чтение (ревью пакета: resolve() на горячем пути
    # доски перепарсивал артефакты на каждый запрос)
    doc = registry._load_json_cached(Path(path))
    return doc if isinstance(doc, (dict, list)) else None


_digest_cache: dict = {}


def _sha_cached(path: Path) -> str | None:
    """SHA-256 файла с кэшем по (путь, mtime_ns, размер) — горячие
    эндпоинты не должны хэшировать мегабайтные payload'ы на каждый GET.
    Писатели заменяют файлы атомарно (tmp+replace), меняя mtime_ns."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_mtime_ns, st.st_size)
    v = _digest_cache.get(key)
    if v is None:
        v = sha256_file(p)
        if len(_digest_cache) > 4096:
            _digest_cache.clear()
        _digest_cache[key] = v
    return v


def effective_rules_for(inst: dict) -> dict:
    """Правила, с которыми реально гоняются задачи этого экземпляра —
    та же логика выбора файла, что в jobs._base_env (сценарные правила
    приоритетнее общего конфига), и ТА ЖЕ нормализация значений, что у
    писателя (rules_config.load_from — ревью пакета: сырые значения из
    JSON давали асимметрию отпечатка и вечный stale)."""
    from . import jobs
    env = jobs._base_env(inst)
    try:
        return rules_config.load_from(env.get("CREW_RULES_CONFIG"))
    except ValueError:
        # битый конфиг: писатель с ним тоже не смог бы — отпечаток
        # гарантированно разойдётся, что честно даст stale
        return dict(rules_config.DEFAULTS)


def _mismatches(manifest: dict, inst: dict) -> list[str]:
    out: list[str] = []
    ins = manifest.get("inputs") or {}
    arts = manifest.get("artifacts") or {}
    # payload: против фактического файла экземпляра
    ppath = registry.instance_payload_path(inst)
    if ppath is not None:
        actual = _sha_cached(Path(ppath))
        if actual != ins.get("payload_sha256"):
            out.append("payload")
    # правила: против эффективного набора для ЭТОГО экземпляра
    if sha256_canon(effective_rules_for(inst)) != ins.get("rules_sha256"):
        out.append("rules")
    # каталог(и): логическая запись разрешается резолвером — данные
    # живут вне репозитория (внешний аудит R6, P2-01), и порядок проб
    # (корень данных, затем репозиторий) обязан совпадать с писателем
    if ins.get("catalog_path"):
        if _sha_cached(registry.resolve_rel(ins["catalog_path"])) != \
                ins.get("catalog_sha256"):
            out.append("catalog")
    for role, rec in (ins.get("catalogs") or {}).items():
        if rec.get("path"):
            if _sha_cached(registry.resolve_rel(rec["path"])) != \
                    rec.get("sha256"):
                out.append(f"catalog:{role}")
    # байты опубликованных артефактов
    if arts.get("assignment_path"):
        if _sha_cached(REPO_ROOT / arts["assignment_path"]) != \
                arts.get("assignment_sha256"):
            out.append("assignment")
    if arts.get("solution_path"):
        if _sha_cached(REPO_ROOT / arts["solution_path"]) != \
                arts.get("solution_sha256"):
            out.append("solution")
    return out


def _multirole_pending(label: str, manifest: dict | None) -> bool:
    """Есть multirole-OK сводка, которую активный указатель не покрывает.
    Ревью пакета: сравниваем свежесть — старый multirole-OK, УЖЕ
    перекрытый более поздней публикацией/промоцией, не должен вечно
    показывать предупреждение и кнопку."""
    summary = _load_json(registry.FINAL_REPORT /
                         f"gerad_multirole_{label}.json")
    if not isinstance(summary, dict) or \
            summary.get("result_status") != "OK":
        return False
    if manifest is None:
        return True
    if manifest.get("mode") == "multirole":
        return False
    stamp = str(summary.get("checked_at") or "")
    anchor = str((manifest.get("promotion") or {}).get("promoted_at")
                 or manifest.get("published_at") or "")
    # неизвестная свежесть (нет штампа) — консервативно предупреждаем
    return not anchor or not stamp or stamp > anchor


def _last_run_not_ok(label: str, manifest: dict | None) -> dict | None:
    """P0-1: последний per-run манифест метки с вердиктом не-OK, который
    НОВЕЕ активного якоря, — прогон завершился, но канонические файлы не
    обновлялись и промоции не было; активным остался прежний ростер.
    Возврат: {run_id, result_status, mode, published_at, assignment_path}
    для meta.last_run либо None."""
    latest = _latest_run_manifest(label, mode=None)
    if latest is None:
        return None
    m = latest["manifest"]
    rs = m.get("result_status")
    if rs == "OK" or rs is None:
        return None
    stamp = str(m.get("published_at") or "")
    anchor = ""
    if manifest is not None:
        anchor = str((manifest.get("promotion") or {}).get("promoted_at")
                     or manifest.get("published_at") or "")
    if anchor and stamp and stamp < anchor:
        return None            # не-OK прогон уже перекрыт свежей промоцией
    return {"run_id": m.get("run_id"), "result_status": rs,
            "mode": m.get("mode"), "published_at": m.get("published_at"),
            "assignment_path":
                (m.get("artifacts") or {}).get("assignment_path")}


def resolve(inst: dict) -> dict:
    """{'status': 'active'|'legacy'|'stale'|'absent', 'assignment': dict|None,
    'meta': {...}, 'warnings': [...], 'mismatches': [...]}."""
    label = inst.get("label") or ""
    manifest = _load_json(active_path(label))
    warnings: list[str] = []
    if manifest is None:
        legacy_path = registry.FINAL_REPORT / \
            f"gerad_stage2_assignment_{label}.json"
        doc = _load_json(legacy_path)
        if doc is None:
            # Ревью P0 (2026-07-20): первый прогон свежей метки с не-OK
            # вердиктом не создаёт канонического файла — предупреждение
            # обязано быть видно и в absent-ветке, иначе прогон невидим.
            meta_a: dict = {}
            warn_a: list[str] = []
            last_bad = _last_run_not_ok(label, None)
            if last_bad:
                warn_a.append("last_run_not_ok")
                meta_a["last_run"] = last_bad
            return {"status": "absent", "assignment": None,
                    "meta": meta_a, "warnings": warn_a, "mismatches": []}
        warnings.append("legacy_no_manifest")
        if _multirole_pending(label, None):
            warnings.append("unpromoted_multirole_ok")
        meta = {"mode": "stage2", "run_id": doc.get("run_id"),
                "result_status": doc.get("result_status")}
        last_bad = _last_run_not_ok(label, None)
        if last_bad:
            warnings.append("last_run_not_ok")
            meta["last_run"] = last_bad
        return {"status": "legacy", "assignment": doc,
                "meta": meta,
                "warnings": warnings, "mismatches": []}
    mismatches = _mismatches(manifest, inst)
    if mismatches:
        return {"status": "stale", "assignment": None,
                "meta": {"mode": manifest.get("mode"),
                         "run_id": manifest.get("run_id"),
                         "result_status": manifest.get("result_status"),
                         "promoted_at":
                             (manifest.get("promotion") or {}).get(
                                 "promoted_at")},
                "warnings": ["stale_fingerprint"], "mismatches": mismatches}
    doc = _load_json(REPO_ROOT /
                     (manifest.get("artifacts") or {})["assignment_path"])
    if doc is None:
        return {"status": "stale", "assignment": None,
                "meta": {"mode": manifest.get("mode")},
                "warnings": ["assignment_unreadable"],
                "mismatches": ["assignment"]}
    if _multirole_pending(label, manifest):
        warnings.append("unpromoted_multirole_ok")
    meta = {"mode": manifest.get("mode"),
            "run_id": manifest.get("run_id"),
            "result_status": manifest.get("result_status"),
            "promoted_at": (manifest.get("promotion") or {}).get(
                "promoted_at"),
            "promoted_by": (manifest.get("promotion") or {}).get(
                "promoted_by"),
            "auto": (manifest.get("promotion") or {}).get("auto"),
            "limitations": manifest.get("limitations") or []}
    last_bad = _last_run_not_ok(label, manifest)
    if last_bad:
        warnings.append("last_run_not_ok")
        meta["last_run"] = last_bad
    return {"status": "active", "assignment": doc,
            "meta": meta,
            "warnings": warnings, "mismatches": []}


class PromoteError(ValueError):
    """Промоция невозможна; сообщение безопасно показать пользователю."""


def _latest_run_manifest(label: str, mode: str | None) -> dict | None:
    """Свежайший per-run манифест метки; mode=None — любой режим (P0-1:
    предупреждение last_run_not_ok смотрит на все прогоны)."""
    from _roster_manifest import run_manifest_dir
    best = None
    try:
        paths = sorted(run_manifest_dir(label).glob("*.json"))
    except OSError:
        return None
    for p in paths:
        doc = _load_json(p)
        if isinstance(doc, dict) and (mode is None
                                      or doc.get("mode") == mode):
            if best is None or str(doc.get("published_at")) >= \
                    str(best[0].get("published_at")):
                best = (doc, p)
    return {"manifest": best[0], "path": best[1]} if best else None


def promote_multirole(inst: dict, email: str) -> dict:
    """Явная промоция multirole-OK (аудит-2 P1-06): материализует единый
    pairing-документ из lane-траекторий (merged-решение оркестратора —
    flight-level и ростерным читателям непригодно) и атомарно переключает
    активный указатель. Fail-closed: не-OK, расхождение отпечатков или
    не-OK lane — отказ без побочных эффектов."""
    from _gerad_role_views import role_slug
    from _roster_manifest import promote, sha256_file as _shaf
    label = inst.get("label") or ""
    found = _latest_run_manifest(label, "multirole")
    if not found:
        raise PromoteError("нет multirole-манифеста для этой метки — "
                           "сначала выполните multirole-прогон")
    manifest, run_path = found["manifest"], found["path"]
    if manifest.get("result_status") != "OK":
        raise PromoteError(
            f"multirole-вердикт {manifest.get('result_status')!r} — "
            f"промотировать можно только OK")
    bad = [m for m in _mismatches(manifest, inst)
           if not m.startswith("assignment")]
    # assignment-мисматч сводки допустим ТОЛЬКО из-за более新ого прогона —
    # но тогда и manifest новее; строгий отказ при любом расхождении входов.
    if bad:
        raise PromoteError("отпечатки прогона разошлись с текущим "
                           f"состоянием ({', '.join(bad)}) — перегоните "
                           f"multirole")
    roles = list((manifest.get("multirole") or {}).get("roles") or [])
    if not roles:
        raise PromoteError("манифест не содержит ролей multirole")
    pairings = []
    wm_union: dict = {}
    for ordinal, role in enumerate(roles):
        slug = role_slug(role)
        lane_doc = _load_json(
            registry.FINAL_REPORT /
            f"gerad_stage2_assignment_{label}--role--{slug}.json")
        if not isinstance(lane_doc, dict) or \
                lane_doc.get("result_status") != "OK":
            raise PromoteError(f"lane «{role}» не в статусе OK — "
                               f"промоция невозможна")
        # Ревью пакета: смешение прогонов — отказ (lane'ы наследуют один
        # CREW_RUN_ID от multirole-джоба).
        if manifest.get("run_id") and lane_doc.get("run_id") and \
                lane_doc["run_id"] != manifest["run_id"]:
            raise PromoteError(
                f"lane «{role}» от другого прогона "
                f"({lane_doc['run_id']} != {manifest['run_id']}) — "
                f"перегоните multirole")
        # Ревью пакета: без переноса wire_map промотированный ростер
        # re-key-payload'а молча терял участки на доске/в CSV.
        wm_union.update(lane_doc.get("wire_map") or {})
        for p in lane_doc.get("pairings") or []:
            if not isinstance(p, dict):
                continue
            q = dict(p)
            q["pid"] = f"{slug}--{p.get('pid', '')}"
            if not q.get("crew"):
                q["crew"] = [{"indexInFlight": ordinal,
                              "requiredSkill": role,
                              "employee": p.get("solver_employee")
                              or p.get("employee")}]
            pairings.append(q)
    doc = {"schema": "multirole-roster/v1", "label": label,
           "mode": "multirole", "run_id": manifest.get("run_id"),
           "result_status": "OK", "stage2_score": None,
           "score_non_comparable": True, "roles": roles,
           "pairings": pairings}
    if wm_union:
        doc["wire_map"] = wm_union
    out = registry.FINAL_REPORT / f"gerad_multirole_roster_{label}.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(out)
    promoted = dict(manifest)
    promoted["artifacts"] = dict(manifest.get("artifacts") or {})
    try:
        promoted["artifacts"]["assignment_path"] = \
            out.relative_to(REPO_ROOT).as_posix()
    except ValueError:                      # тестовая изоляция вне репо
        promoted["artifacts"]["assignment_path"] = str(out)
    promoted["artifacts"]["assignment_sha256"] = _shaf(out)
    promote(promoted, promoted_by=email, auto=False,
            source_run_manifest=run_path)
    return {"promoted": True, "pairings": len(pairings),
            "roles": roles, "artifact": out.name}
