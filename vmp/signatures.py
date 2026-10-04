# -*- coding: utf-8 -*-
"""Подписи прямого хода — части COSMOS плана
final-report/CDPM-PLAN-PRYAMOJ-HOD-CLD.md (раздел 7, пункты 1–3):

  Д1  профиль детерминированного режима решателей — поимённый состав
      настроек, при котором два запуска на одном пакете и одних данных
      дают тождественный канонический выход; штампуется в записи
      прогона и в сертификате;
  Д2  канонический выход решателей и его подпись: ростеринг —
      отсортированное множество назначений (сотрудник, участок,
      начало); пейринг — отсортированное множество цепочек
      (последовательности участков); плюс вектор счёта по ограничениям
      и статус завершения; SHA-256 канонической сериализации. Стенное
      время, имя каталога прогона, порядок обхода в подпись не входят;
  Д3  подпись задачи: SHA-256 канонического слоя контракта (вид, зона,
      семья, параметры в канонических единицах, веса, единицы
      нарушения, области, ярусы, целевые с направлением и ставкой)
      плюс профиль Д1 плюс отпечаток данных. Формулировки, цитаты,
      якоря, идентификаторы строк, source_run, время — не входят.

Канонизация параметров и целевых переиспользована из vmp.equivalence
(канон гейта эквивалентности), копий словарей здесь нет. Ни одного
обращения к решателям: модуль считает подписи по готовым объектам.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from vmp import equivalence as eq

PROFILE_VERSION = 1
SIGNATURE_VERSION = 1

_SCORE_PART = re.compile(r"(-?\d+)\s*(init|hard|medium|soft)")


# ------------------------------------------------------------ общее

def canon_json(obj: Any) -> str:
    """Каноническая сериализация: ключи по алфавиту, без пробелов,
    юникод как есть. Одна для всех подписей модуля."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canon_json(obj).encode("utf-8")).hexdigest()


# ------------------------------------------------------------ Д1

def deterministic_profile(task: str, *, seed: int = 0,
                          step_limit: int | None = None,
                          unimproved_step_limit: int | None = None,
                          deterministic_time: float | None = None,
                          extra: dict | None = None) -> dict:
    """Профиль детерминированного режима поимённо (Д1).

    rostering (Timefold): остановка по счётным событиям — число шагов
    либо неулучшающих шагов, не стенные часы; один поток ходов;
    фиксированное зерно; порядок сущностей по идентификаторам; режим
    среды NO_ASSERT (в Timefold 2.0 воспроизводимы все режимы, кроме
    NON_REPRODUCIBLE).
    pairing (CP-SAT + сборка задачи на Python): один поток поиска,
    фиксированное зерно, остановка по детерминированному времени
    CP-SAT (счётная величина, не стенные часы), дедлайны перечислителя
    выключены, PYTHONHASHSEED=0, сортировка коллекций."""
    if task == "rostering":
        if not step_limit and not unimproved_step_limit:
            raise ValueError("профиль ростеринга: нужен счётный лимит — "
                             "step_limit либо unimproved_step_limit")
        prof = {"version": PROFILE_VERSION, "task": task,
                "engine": "timefold", "seed": int(seed),
                # Timefold 2.0: воспроизводимы все режимы, кроме
                # NON_REPRODUCIBLE; решатель объявляет NO_ASSERT.
                "move_threads": 1, "environment_mode": "NO_ASSERT",
                "entity_order": "by_id",
                "termination": {"kind": "step_count",
                                "step_limit": step_limit,
                                "unimproved_step_limit": unimproved_step_limit}}
    elif task == "pairing":
        if not deterministic_time or deterministic_time <= 0:
            raise ValueError("профиль пейринга: нужен deterministic_time "
                             "CP-SAT (счётная величина) > 0")
        prof = {"version": PROFILE_VERSION, "task": task,
                "engine": "cp-sat", "seed": int(seed), "workers": 1,
                "python_hash_seed": 0, "collections_sorted": True,
                "enumeration_deadline": None,
                "termination": {"kind": "deterministic_time",
                                "value": float(deterministic_time)}}
    else:
        raise ValueError("неизвестная задача профиля: %r" % (task,))
    if extra:
        prof["extra"] = dict(extra)
    prof["stamp"] = sha256_of({k: v for k, v in prof.items() if k != "stamp"})
    return prof


def profile_stamp(profile: dict) -> str:
    return sha256_of({k: v for k, v in profile.items() if k != "stamp"})


# ------------------------------------------------------------ Д2

def parse_score(score: Any) -> dict:
    """«0hard/-5soft», «-3init/0hard/0soft», словарь {hard, soft} или
    None → {"hard": …, "soft": …[, "init": …]}."""
    if isinstance(score, dict):
        out = {}
        for k in ("init", "hard", "medium", "soft"):
            if k in score and score[k] is not None:
                out[k] = int(score[k])
        return out
    if score is None:
        return {}
    out = {}
    for num, name in _SCORE_PART.findall(str(score)):
        out[name] = int(num)
    return out


def _id_of(x: Any, key: str) -> str:
    if isinstance(x, dict):
        return str(x.get(key) or x.get("id") or "")
    return "" if x is None else str(x)


def canonical_roster(solved: dict, *, constraints: dict | None = None,
                     status: str | None = None) -> dict:
    """Канонический выход ростеринга (Д2): назначения как отсортированное
    множество (сотрудник, участок, начало), перегон — признаком; счёт;
    вектор счёта по ограничениям (из отчёта применённого решателя —
    показания, а не пересчёт); статус завершения."""
    times = {}
    for f in solved.get("flights") or []:
        if isinstance(f, dict):
            fn = str(f.get("flightNumber") or f.get("flightLegId") or "")
            if fn:
                times[fn] = str(f.get("departureUTCDateTime") or "")
    rows = []
    unassigned = 0
    for a in solved.get("flightAssignments") or []:
        if not isinstance(a, dict):
            continue
        emp = _id_of(a.get("employee"), "id")
        if not emp:
            unassigned += 1
            continue
        fn = _id_of(a.get("flight"), "flightNumber")
        start = times.get(fn) or (fn.split("|", 1)[1] if "|" in fn else "")
        rows.append([emp, fn, start, bool(a.get("deadhead"))])
    rows.sort()
    return {"task": "rostering", "version": SIGNATURE_VERSION,
            "assignments": rows, "unassigned": unassigned,
            "score": parse_score(solved.get("score")),
            "constraints": _constraints_vector(constraints),
            "status": status or str(solved.get("solverStatus")
                                    or solved.get("status") or "")}


def _constraints_vector(constraints: dict | None) -> list:
    """Вектор счёта по ограничениям — отсортированный список
    [имя, hard, soft, совпадений]; веса и текстовый score не нужны."""
    out = []
    for name, rec in (constraints or {}).items():
        if not isinstance(rec, dict):
            continue
        out.append([str(name), int(rec.get("hard") or 0),
                    int(rec.get("soft") or 0), int(rec.get("matches") or 0)])
    out.sort()
    return out


def canonical_pairing(catalog: dict, *, status: str | None = None) -> dict:
    """Канонический выход пейринга (Д2): цепочки как отсортированное
    множество последовательностей участков; у каждой — смены (участки
    по сменам), перегоны (участки цепочки, не отработанные ею), места;
    целевая построителя и вердикт покрытия."""
    chains = []
    for c in catalog.get("selected") or []:
        if not isinstance(c, dict):
            continue
        fns = [str(x) for x in (c.get("fns") or [])]
        active = {str(x) for x in (c.get("active_fns") or fns)}
        chains.append({
            "legs": fns,
            "duties": [[str(x) for x in d] for d in (c.get("duty_fns") or [])],
            "deadhead": [x for x in fns if x not in active],
            "seats": c.get("seats") if isinstance(c.get("seats"), int)
            else c.get("crew"),
            "cost": eq.canon_value(c.get("cost")) if c.get("cost") is not None
            else None})
    chains.sort(key=canon_json)
    cov = catalog.get("coverage") if isinstance(catalog.get("coverage"), dict) \
        else {}
    return {"task": "pairing", "version": SIGNATURE_VERSION,
            "chains": chains,
            "objective": eq.canon_value(catalog.get("ip_objective"))
            if catalog.get("ip_objective") is not None else None,
            "uncovered": cov.get("uncovered"),
            "status": status or str(catalog.get("ip_status") or ""),
            "coverage_verdict": cov.get("verdict")}


def output_signature(canon: dict) -> dict:
    """Подпись выхода: SHA-256 канонической сериализации канона Д2."""
    return {"task": canon.get("task"), "version": SIGNATURE_VERSION,
            "sha256": sha256_of(canon)}


# ------------------------------------------------------------ Д3

def data_fingerprint(clean_data: dict | None) -> str | None:
    """Отпечаток данных набора: SHA-256 канонической сериализации таблиц
    (листы, колонки, строки). Отчёт очистки и имя заказчика — вне
    отпечатка: они не меняют задачу."""
    if not isinstance(clean_data, dict):
        return None
    tables = clean_data.get("tables") or {}
    canon = {}
    for sid, sheets in tables.items():
        for sheet, block in (sheets or {}).items():
            if not isinstance(block, dict):
                continue
            rows = [canon_json(r) for r in (block.get("rows") or [])
                    if isinstance(r, dict)]
            canon.setdefault(str(sheet), []).append({
                "columns": [str(c) for c in (block.get("columns") or [])],
                "rows": sorted(rows)})
    for sheet in canon:
        canon[sheet].sort(key=canon_json)
    return sha256_of(canon)


def _penalty_units(form: dict | None) -> dict:
    units = {}
    for part in ("pairing", "rostering"):
        for row in (((form or {}).get(part) or {}).get("soft") or []):
            if isinstance(row, dict) and isinstance(row.get("penalty"), dict):
                units[str(row.get("id"))] = row["penalty"].get("violation_unit")
    return units


def _js(x: Any) -> str | None:
    return None if x is None else canon_json(x)


def canonical_contract(model: dict, form: dict | None = None) -> dict:
    """Канонический слой контракта (Д3): строки без идентификаторов,
    формулировок, цитат и якорей. Снятые строки (OUT) не входят;
    определения и записи данных (DEF/DATA) входят своими параметрами,
    если они есть (норма из файла данных — часть задачи)."""
    rules = []
    units = _penalty_units(form)
    for r in (model.get("rules") or []):
        if not isinstance(r, dict) or r.get("kind") == "OUT":
            continue
        fam, params = eq.family_params_of(r)
        excluded = r.get("excluded_until_data")
        row = {
            "kind": r.get("kind"), "zone": r.get("zone"),
            "family": fam, "params": params,
            "weight": eq.canon_value(r.get("weight"))
            if r.get("weight") is not None else None,
            "violation_unit": units.get(str(r.get("id"))),
            "scope": _js(r.get("scope")),
            "logic": _js(r.get("logic")),
            "conditional": _js(r.get("conditional")),
            "export_solver": _js(r.get("export_solver")),
            "excluded_until_data": bool(isinstance(excluded, dict)
                                        and excluded.get("question")),
            "case": r.get("kind") == "CASE",
        }
        rules.append(row)
    rules.sort(key=canon_json)
    objectives = []
    for o in (model.get("objectives") or []):
        if not isinstance(o, dict):
            continue
        key = eq._objective_canon(o, with_criterion=True)
        objectives.append({
            "zone": key[0], "direction": key[1], "metric": key[2],
            "criterion": _js(key[3]),
            "rate": _js(_rate_canon(o.get("rate"))),
            "tier": int(o.get("tier") or 1),
            "tolerance": _js(o.get("tolerance")),
        })
    objectives.sort(key=canon_json)
    return {"version": SIGNATURE_VERSION, "rules": rules,
            "objectives": objectives}


def _rate_canon(rate: Any) -> Any:
    if not isinstance(rate, dict):
        return None
    out = {}
    if rate.get("value") is not None:
        out["value"] = eq.canon_value(rate["value"])
    if rate.get("per"):
        out["per"] = str(rate["per"]).strip().casefold()
    if isinstance(rate.get("rates"), dict):
        out["rates"] = {str(k): eq.canon_value(v)
                        for k, v in sorted(rate["rates"].items())}
    return out or None


def task_signature(pkg: dict, profile: dict | None) -> dict:
    """Подпись задачи (Д3): контракт + профиль Д1 + отпечаток данных.
    Профиль без штампа или отсутствующий — подпись помечена
    profile=None: задача без профиля воспроизводимой не считается."""
    contract = canonical_contract(pkg.get("model") or {}, pkg.get("form"))
    contract_sha = sha256_of(contract)
    data_sha = data_fingerprint(pkg.get("clean_data"))
    prof_sha = profile_stamp(profile) if isinstance(profile, dict) else None
    return {"version": SIGNATURE_VERSION,
            "contract_sha256": contract_sha,
            "data_sha256": data_sha,
            "profile_sha256": prof_sha,
            "sha256": sha256_of({"contract": contract_sha, "data": data_sha,
                                 "profile": prof_sha}),
            "rules": len(contract["rules"]),
            "objectives": len(contract["objectives"])}
