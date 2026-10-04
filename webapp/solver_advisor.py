"""Solver-advisor knowledge base (2026-07-14).

A curated "skill" — a human-readable Markdown document that captures, for
which situations and which phase (pairing / rostering), which solver and
approach to use, how to split work between solvers, which parameters to set,
and what to watch out for. It is:

  * BUILT on the Test stand (external contour) from PUBLIC benchmark evidence
    by a cloud LLM — see evolution_runner.build_solver_skill (phase 2).
  * CONSULTED at run-configuration time (Stage/Prod) to produce an ADVISORY
    recommendation a human confirms before launching — see `consult()`.

Everything here is contour-aware but contour-agnostic in code: `consult()`
computes only NON-PII aggregates for an instance and picks the LLM backend
through llm_backend.resolve_backend(), which enforces the per-dataset
`llm_scope`. The skill text itself is public-benchmark-derived, so it may
travel from Test to Stage/Prod like any other config file.

The recommendation is a suggestion, never an auto-applied action; the
mapping of confirmed knobs onto the run command lives in the job dispatch
(phase 3), which re-validates every value.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from datetime import datetime
from pathlib import Path

from . import llm_backend
from .registry import REPO_ROOT, instance_payload_path, load_payload

SKILL_DIR = REPO_ROOT / "webapp" / "config" / "solver_advisor"
SKILL_PATH = SKILL_DIR / "skill.md"
META_PATH = SKILL_DIR / "meta.json"
HISTORY_DIR = SKILL_DIR / "history"

CONSULT_TIMEOUT_S = 90.0

# Tier-1 rule parameters that are part of the problem fingerprint (a cached
# solver choice from a different rule set must not be reused blindly).
RULE_KEYS = ("min_sit_min", "max_landings_per_duty", "max_duty_span_min",
             "min_rest_min", "max_monthly_block_min", "max_pairing_span_min")

# Knobs a confirmed recommendation may set on a run. The dispatch (phase 3)
# re-validates against these bounds before touching a command line — this is
# the single source of truth for what a KB is ALLOWED to influence, so an
# LLM cannot inject an arbitrary flag/value into a subprocess.
ENUM_PARAMS = {
    "pricer": ("legs", "duty"),
    "profile": ("gerad", "v2"),
    # построитель цепочек: способ порождения кандидатов
    "pairing_method": ("enumerate", "columns"),
    # движок фазы отбора покрытия (пакет «выбор движка»
    # 2026-08-28): внешние решают ту же модель, недоступный —
    # честный отказ построителя, не подмена
    "select_engine": ("cpsat", "cplex", "highs"),
    # движок ростеринга (ростеринговая часть пакета): timefold —
    # штатный решатель; cpsat — прямая модель v1 (объём судимых
    # правил декларируется отчётом движка поимённо)
    "roster_engine": ("timefold", "cpsat"),
}
INT_PARAM_BOUNDS = {
    "mp": (1, 32),
    "price_cap": (30, 7200),
    "final_tl": (30, 7200),
    "total_cap": (60, 57600),
    "max_landings": (1, 12),
    "budget_s": (30, 57600),
    "seed": (0, 2**31 - 1),
    # Д1 плана прямого хода: предел детерминированного времени CP-SAT
    # (счётная величина); построитель получает --deterministic-time и
    # один поток, стенные часы во всех фазах выключены.
    "deterministic_time": (1, 36000),
    # построитель цепочек: уровень свободы и веса стоимости
    "pairing_level": (1, 3),
    "w_night": (0, 100000),
    "w_dh": (0, 100000),
    # бюджет задачи покрытия отдельно от бюджета генерации кандидатов
    "solve_s": (0, 57600),
    # спасательный проход по плечам, которым не досталось кандидатов
    "rescue_dh": (0, 16),
    "rescue_per_leg": (1, 256),
    # Четвёртый аудит 2026-07-27 (§4.1): rescue_s был проведён в команду и
    # подпись, но санитайзер — единственная граница входа — его вырезал
    # ДО обеих точек: две разные постановки давали одинаковые argv и
    # одинаковую подпись задачи, а «исправление» было мёртвым кодом.
    # Санитайзер и есть перечень существующих параметров: новый ключ
    # обязан появляться ЗДЕСЬ, иначе его не существует нигде.
    "rescue_s": (1, 3600),
}


# ============================================================ skill storage

_SEED_SKILL = """\
# База знаний по выбору решателей (пейринг + ростеринг)

> Стартовый каркас (версия-семя). Наполняется на этапе Test облачной LLM из
> публичных бенчмарков; на Stage/Prod используется как советник (человек
> подтверждает выбор перед запуском).

## Конвейер и доступные движки

**Этап 1 — пейринг (подбор смен/пар экипажа).** Генерация колонок:
мастер-LP на OR-Tools GLOP, прайсинг `--pricer legs|duty` (duty — по сети
дежурств, обычно устойчивее на крупных инстансах), интегеризация набора —
CP-SAT (set-covering при якорении на базах) либо set-partitioning-селектор.
Профили `--profile gerad|v2`. Исследовательские движки (HiGHS, SCIP,
branch-and-price с ценообразованием и сглаживанием двойственных) валидированы
на COR@L/OR-Library, но в боевой диспетчер пока не подключены.

**Этап 2 — ростеринг (назначение на сотрудников).** Timefold (Kotlin, REST на
изолированной JVM), поздний приём (Late Acceptance), правила GERAD2P→GERAD2.
Альтернативного движка этапа 2 в конвейере сейчас нет.

## Ориентиры выбора (наполняется по мере накопления прогонов)

- По умолчанию для GERAD-подобных инстансов: этап 1 — `--pricer duty
  --profile gerad`, распараллеливание `--mp 10`, кап ценообразования ~600 с,
  финальный лимит ~900 с; этап 2 — Timefold, бюджет ~600 с, seed 7.
- Для генерических загрузок: этап 1 — `--profile v2`.
- На что смотреть: устойчивость этапа 2 (на части инстансов ростеринг
  исторически завершался ненулевым кодом — проверяйте `last_gate_status`);
  разница во времени счёта в пределах ~1% — это шум, а не выигрыш.

## Правила яруса-1 в фингерпринте

Оптимальный подход зависит от правил (мин. пересидка, макс. посадок за
дежурство и т.д.) — рекомендация действительна для того же набора правил,
на котором наблюдалась.
"""


def _read_meta() -> dict:
    try:
        raw = json.loads(META_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def load_skill() -> dict:
    """Current skill: {"text": str, "meta": {...}}. When nothing has been
    built yet, returns the seed scaffold with version 0 (source 'seed')."""
    try:
        text = SKILL_PATH.read_text(encoding="utf-8")
    except OSError:
        return {"text": _SEED_SKILL,
                "meta": {"version": 0, "source": "seed", "updated_at": None,
                         "backend": None, "model": None, "evidence": [],
                         "note": "seed scaffold (no build yet)"}}
    meta = _read_meta()
    meta.setdefault("version", 1)
    meta.setdefault("source", "unknown")
    return {"text": text, "meta": meta}


def skill_meta() -> dict:
    """Provenance of the current skill WITHOUT its text.

    Same defaults as load_skill(), but never reads skill.md — the loops
    overview needs only version/updated_at and must not pull the whole
    document on every refresh. Existence of skill.md still decides
    «built» vs «seed», so the two stay in agreement."""
    if not SKILL_PATH.exists():
        return {"version": 0, "source": "seed", "updated_at": None,
                "backend": None, "model": None, "evidence": [],
                "note": "seed scaffold (no build yet)"}
    meta = _read_meta()
    meta.setdefault("version", 1)
    meta.setdefault("source", "unknown")
    return meta


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save_skill(text: str, *, source: str, backend: str | None = None,
               model: str | None = None, evidence: list | None = None,
               note: str = "", now: str | None = None) -> dict:
    """Persist a new skill version. Snapshots the previous version under
    history/ first, then bumps the version counter. `now` is injectable for
    deterministic tests."""
    text = (text or "").strip()
    if not text:
        raise ValueError("refusing to save an empty skill document")
    cur = load_skill()
    prev_version = int(cur["meta"].get("version") or 0)
    # snapshot the outgoing version (only if a real file exists on disk)
    if SKILL_PATH.exists():
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(HISTORY_DIR / f"skill-v{prev_version}.md", cur["text"])
    version = prev_version + 1
    meta = {
        "version": version,
        "source": source,
        "backend": backend,
        "model": model,
        "evidence": list(evidence or []),
        "note": note[:2000],
        "updated_at": now or datetime.now().isoformat(timespec="seconds"),
    }
    _atomic_write(SKILL_PATH, text + "\n")
    _atomic_write(META_PATH, json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


# ======================================================= instance descriptor

def _payload_aggregates(payload: dict) -> dict:
    """Non-PII structural aggregates: airport set/count, distinct home-base
    count, and a best-effort planning horizon. No employee/flight identity
    ever leaves this function."""
    airports = sorted({a.get("code") for a in (payload.get("airports") or [])
                       if isinstance(a, dict) and a.get("code")})
    bases = {e.get("homeAirport") for e in (payload.get("employees") or [])
             if isinstance(e, dict) and e.get("homeAirport")}
    # Best-effort horizon from any ISO-date-looking field on flights.
    dates: list[str] = []
    for f in (payload.get("flights") or []):
        if not isinstance(f, dict):
            continue
        for k in ("departureDateTime", "departureTime", "scheduledDeparture",
                  "departure", "date"):
            v = f.get(k)
            if isinstance(v, str) and len(v) >= 10 and v[4] == "-":
                dates.append(v[:10])
                break
    horizon = None
    if dates:
        horizon = {"from": min(dates), "to": max(dates)}
    return {
        "airports": airports[:80],
        "n_airports": len(airports),
        "n_home_bases": len(bases),
        "horizon": horizon,
    }


def instance_descriptor(inst: dict, rules: dict | None = None) -> dict:
    """The non-PII descriptor sent to the LLM. Built from the instance's
    aggregate stats + tier-1 rules + structural aggregates — never raw rows
    (employee/flight identities, unavailable days)."""
    rules = rules or {}
    d = {
        "source": inst.get("source"),
        "legs": inst.get("legs"),
        "employees": inst.get("employees"),
        "slots": inst.get("slots"),
        "stage2_available": inst.get("stage2_available"),
        "has_best_catalog": inst.get("has_best_catalog"),
        "catalog_objective": inst.get("catalog_objective"),
        "last_gate_status": inst.get("last_gate_status"),
        "rules": {k: rules.get(k) for k in RULE_KEYS if k in rules},
    }
    m = re.fullmatch(r"gerad-([1-7])", str(inst.get("id") or ""))
    if m:
        d["gerad_number"] = int(m.group(1))
    try:
        payload = load_payload(instance_payload_path(inst))
        d.update(_payload_aggregates(payload))
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        pass                          # aggregates are best-effort, non-fatal
    return d


# =========================================================== recommendation

def _extract_json_objects(text: str) -> list[dict]:
    """Every top-level balanced {...} block that parses as a JSON object,
    in order. Tolerant of prose around/between them."""
    out: list[dict] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    blob = text[start:i + 1]
                    try:
                        d = json.loads(blob)
                        if isinstance(d, dict):
                            out.append(d)
                    except json.JSONDecodeError:
                        pass
                    start = -1
    return out


def _clip(v, n: int) -> str:
    return str(v).strip()[:n]


def sanitize_params(raw) -> dict:
    """Keep ONLY whitelisted knobs, coerced and range-checked. This is the
    security boundary: an LLM-authored params object can never introduce an
    unknown flag or an out-of-range value into a subprocess."""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, allowed in ENUM_PARAMS.items():
        v = raw.get(key)
        if isinstance(v, str) and v.strip().lower() in allowed:
            out[key] = v.strip().lower()
    for key, (lo, hi) in INT_PARAM_BOUNDS.items():
        v = raw.get(key)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and lo <= v <= hi:
            out[key] = int(v)
    return out


def _normalize_recommendation(d: dict) -> dict:
    """Coerce a raw LLM object into the recommendation contract, dropping
    anything unrecognised."""
    def _phase(p) -> dict:
        p = p if isinstance(p, dict) else {}
        return {
            "engine": _clip(p.get("engine", ""), 120),
            "pricer": (p.get("pricer") if p.get("pricer") in ENUM_PARAMS["pricer"] else None),
            "profile": (p.get("profile") if p.get("profile") in ENUM_PARAMS["profile"] else None),
            "notes": _clip(p.get("notes", ""), 600),
        }
    warnings = d.get("warnings")
    warn_list = [_clip(w, 300) for w in warnings][:12] if isinstance(warnings, list) else []
    return {
        "stage1": _phase(d.get("stage1")),
        "stage2": {
            "engine": _clip((d.get("stage2") or {}).get("engine", "") if isinstance(d.get("stage2"), dict) else "", 120),
            "notes": _clip((d.get("stage2") or {}).get("notes", "") if isinstance(d.get("stage2"), dict) else "", 600),
        },
        "split": _clip(d.get("split", ""), 600),
        "params": sanitize_params(d.get("params")),
        "warnings": warn_list,
        "rationale": _clip(d.get("rationale", ""), 1600),
    }


def parse_recommendation(text: str) -> dict | None:
    """Last JSON object that looks like a recommendation (has a rationale or
    a stage1/stage2 block), normalized to the contract."""
    for d in reversed(_extract_json_objects(text)):
        if any(k in d for k in ("rationale", "stage1", "stage2", "params")):
            rec = _normalize_recommendation(d)
            if rec["rationale"] or rec["stage1"]["engine"] or rec["params"]:
                return rec
    return None


def build_consult_prompt(skill_text: str, descriptor: dict,
                         job_type: str) -> str:
    """Prompt for the advisory consult. The LLM reads the skill and the
    non-PII descriptor and returns a strict-JSON recommendation."""
    return (
        "Ты — старший инженер по оптимизации расписаний экипажей. Ниже — база "
        "знаний по выбору решателей (skill) и обезличенный дескриптор задачи "
        "(только агрегаты, без персональных данных). Порекомендуй, как решать "
        f"этот прогон (тип: {job_type}): какой движок/подход на этапе 1 "
        "(пейринг) и этапе 2 (ростеринг), как распределять работу между "
        "решателями, какие параметры выставить и на что обратить внимание.\n\n"
        "=== БАЗА ЗНАНИЙ (skill.md) ===\n" + (skill_text or "")[:12000] + "\n\n"
        "=== ДЕСКРИПТОР ЗАДАЧИ (обезличенный) ===\n"
        + json.dumps(descriptor, ensure_ascii=False, indent=1) + "\n\n"
        "Ответь СТРОГО одним JSON-объектом, без текста вокруг, по схеме:\n"
        '{\n'
        '  "stage1": {"engine": str, "pricer": "legs"|"duty"|null, '
        '"profile": "gerad"|"v2"|null, "notes": str},\n'
        '  "stage2": {"engine": str, "notes": str},\n'
        '  "split": str,\n'
        '  "params": {"mp": int?, "price_cap": int?, "final_tl": int?, '
        '"total_cap": int?, "max_landings": int?, "budget_s": int?, '
        '"seed": int?, "pairing_method": "enumerate"|"columns"?, '
        '"pairing_level": int?, "w_night": int?, "w_dh": int?, '
        '"solve_s": int?, "rescue_dh": int?, "rescue_per_leg": int?, '
        '"rescue_s": int?},\n'
        '  "warnings": [str],\n'
        '  "rationale": "<2-5 предложений по-русски>"\n'
        '}\n'
        "Указывай в params только те ключи, которые реально стоит поменять "
        "относительно значений по умолчанию.")


def _fallback_recommendation(descriptor: dict, job_type: str) -> dict:
    """Deterministic recommendation when the LLM is unavailable — mirrors the
    pipeline's current hard-coded defaults so the human still sees a sane,
    confirmable pre-fill."""
    gerad_like = descriptor.get("source") == "gerad" or descriptor.get("gerad_number")
    warnings = []
    if descriptor.get("last_gate_status") and descriptor["last_gate_status"] != "OK":
        warnings.append(
            f"Прошлый вердикт этапа 2: {descriptor['last_gate_status']} — "
            "проверьте устойчивость ростеринга.")
    if gerad_like:
        rec = {
            "stage1": {"engine": "генерация колонок (GLOP + CP-SAT)",
                       "pricer": "duty", "profile": "gerad",
                       "notes": "Устойчивый профиль для GERAD-подобных."},
            "stage2": {"engine": "Timefold (Kotlin)",
                       "notes": "Поздний приём, правила GERAD2P→GERAD2."},
            "split": "Последовательно: этап 1 (пейринг) → этап 2 (ростеринг).",
            "params": {"mp": 10, "price_cap": 600, "final_tl": 900,
                       "budget_s": 600, "seed": 7},
        }
    else:
        rec = {
            "stage1": {"engine": "генерация колонок (профиль v2)",
                       "pricer": "duty", "profile": "v2",
                       "notes": "Профиль по умолчанию для загрузок."},
            "stage2": {"engine": "Timefold (Kotlin)", "notes": ""},
            "split": "Последовательно: этап 1 → этап 2.",
            "params": {"budget_s": 600, "seed": 7},
        }
    rec["warnings"] = warnings
    rec["rationale"] = ("Модель ИИ недоступна — показаны текущие параметры "
                        "по умолчанию из конвейера. Подтвердите или измените "
                        "вручную.")
    rec["params"] = sanitize_params(rec["params"])
    return rec


def consult(inst: dict, rules: dict | None, job_type: str, *,
            backend_request: str | None = None,
            timeout_s: float = CONSULT_TIMEOUT_S) -> dict:
    """Produce an ADVISORY solver recommendation for an instance.

    Resolves the LLM backend from the dataset's llm_scope (raising
    ScopeViolation on a cloud request for a local-only dataset), sends the
    skill + NON-PII descriptor, and returns a normalized recommendation. On
    any LLM failure, returns the deterministic fallback with source
    'fallback'. The returned `descriptor` is exactly what was sent to the LLM
    (so the UI can show what left the machine)."""
    scope = inst.get("llm_scope") or "local-only"
    backend = llm_backend.resolve_backend(scope, backend_request)
    descriptor = instance_descriptor(inst, rules)
    skill = load_skill()
    prompt = build_consult_prompt(skill["text"], descriptor, job_type)
    res = llm_backend.complete(prompt, backend=backend, timeout_s=timeout_s,
                               usage="advisor")
    base = {
        "instance_id": inst.get("id"),
        "job_type": job_type,
        "scope": scope,
        "backend": backend,
        "model": res.get("model"),
        "descriptor": descriptor,
        "skill_version": skill["meta"].get("version"),
    }
    if res.get("ok"):
        rec = parse_recommendation(res["text"])
        if rec:
            return {**base, "source": "llm", "recommendation": rec,
                    "llm_error": None}
        llm_error = "LLM-ответ не распарсился как рекомендация"
    else:
        llm_error = res.get("error")
    return {**base, "source": "fallback", "llm_error": llm_error,
            "recommendation": _fallback_recommendation(descriptor, job_type)}


# ============================================== skill build (Test / external)

BUILD_TIMEOUT_S = 300.0

# PUBLIC benchmark artifacts the builder distils into the skill. Every file
# here is public (GERAD/BTS/COR@L/OR-Library seed results) and present in
# every install — no customer data is ever read into the build prompt.
EVIDENCE_FILES = (
    "final-report/gerad_stage1_results.json",
    "final-report/gerad_phase2_two_stage.json",
    "final-report/coral_a320_engines.json",
    "final-report/coral727_engines.json",
    "final-report/orlib_spp_results.csv",
    "final-report/orlib_spp_timefold.csv",
    "benchmark_results.csv",
)
_EVIDENCE_PER_FILE_CAP = 2600
_EVIDENCE_TOTAL_CAP = 16000


def gather_evidence() -> tuple[str, list[str]]:
    """Compact digest of the PUBLIC solver-vs-instance evidence + a list of
    the files that contributed. Each file is truncated; JSON is re-emitted
    compactly. Improvement-store benchmark verdicts are appended when
    present (public GERAD/BTS metrics only)."""
    parts: list[str] = []
    used: list[str] = []
    total = 0
    for rel in EVIDENCE_FILES:
        p = REPO_ROOT / rel
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if rel.endswith(".json"):
            try:
                raw = json.dumps(json.loads(raw), ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        snippet = raw[:_EVIDENCE_PER_FILE_CAP]
        if len(raw) > _EVIDENCE_PER_FILE_CAP:
            snippet += " …[обрезано]"
        block = f"### {rel}\n{snippet}\n"
        if total + len(block) > _EVIDENCE_TOTAL_CAP:
            break
        parts.append(block)
        used.append(rel)
        total += len(block)
    # Improvement-store benchmark verdicts (public benchmark data).
    try:
        from . import evolution
        rows = evolution.list_all()
        digest = []
        for r in rows[:12]:
            tm = r.get("test_metrics")
            verdict = None
            if isinstance(tm, dict):
                verdict = tm.get("verdict") or (tm.get("summary") or {}).get("verdict")
            digest.append(f"- #{r.get('id')} {str(r.get('title'))[:80]} "
                          f"[{r.get('status')}] verdict={verdict}")
        if digest:
            block = "### improvements (бенчмарк-вердикты)\n" + "\n".join(digest) + "\n"
            if total + len(block) <= _EVIDENCE_TOTAL_CAP + 2000:
                parts.append(block)
                used.append("improvements-store")
    except Exception:                                   # noqa: BLE001
        pass
    return ("\n".join(parts) if parts else "(нет доступных артефактов)"), used


def build_skill_prompt(current_skill: str, digest: str) -> str:
    return (
        "Ты — старший инженер по оптимизации расписаний экипажей. Обнови "
        "базу знаний (skill) по выбору решателей для двухэтапной схемы "
        "(этап 1 — пейринг, этап 2 — ростеринг), опираясь на публичные "
        "результаты бенчмарков ниже. База знаний — это Markdown-документ, "
        "который потом читает советник при выборе решателя для конкретной "
        "задачи (человек подтверждает выбор).\n\n"
        "Требования к документу:\n"
        "- по-русски, аккуратный Markdown, разделы по этапам и по ситуациям;\n"
        "- конкретика: какой движок/подход и параметры для каких размеров/"
        "структур инстансов; как распределять работу между решателями;\n"
        "- явные предупреждения и на что смотреть (нестабильность, известные "
        "провалы, шум времени счёта в пределах ~1%);\n"
        "- не выдумывай данные, которых нет в свидетельствах; сохрани полезное "
        "из текущей версии и уточни/дополни его.\n\n"
        "=== ТЕКУЩАЯ ВЕРСИЯ (skill.md) ===\n" + (current_skill or "")[:12000]
        + "\n\n=== ПУБЛИЧНЫЕ СВИДЕТЕЛЬСТВА (инстанс → решатель → метрики) ===\n"
        + digest + "\n\n"
        "Верни ТОЛЬКО обновлённый Markdown-документ (без пояснений вокруг, "
        "без ``` -ограждения).")


def _clean_skill_markdown(text: str) -> str:
    """Unwrap an optional ```markdown fenced block; strip stray prose the
    model sometimes adds around the document."""
    text = (text or "").strip()
    m = re.search(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def build_solver_skill(*, backend: str = "cloud", cwd=None,
                       now: str | None = None) -> dict:
    """Distil the PUBLIC evidence into a new skill version via an LLM. The
    cloud backend runs with cwd inside the external-contour Test checkout
    (the documented invariant); only public data enters the prompt. Returns
    {ok, meta?, error?, evidence, backend, model}."""
    digest, evidence = gather_evidence()
    if backend == "cloud" and cwd is None:
        from .evolution_runner import test_dir
        cwd = str(test_dir())
    current = load_skill()
    prompt = build_skill_prompt(current["text"], digest)
    res = llm_backend.complete(prompt, backend=backend,
                               timeout_s=BUILD_TIMEOUT_S, cwd=cwd,
                               usage="advisor")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "evidence": evidence,
                "backend": backend, "model": res.get("model")}
    text = _clean_skill_markdown(res["text"])
    if len(text) < 200:
        return {"ok": False, "error": "LLM вернул слишком короткий документ",
                "evidence": evidence, "backend": backend,
                "model": res.get("model")}
    meta = save_skill(text, source="llm", backend=res.get("backend"),
                      model=res.get("model"), evidence=evidence,
                      note="сборка из публичных бенчмарков", now=now)
    return {"ok": True, "meta": meta, "evidence": evidence,
            "backend": res.get("backend"), "model": res.get("model")}


# ------------------------------------------------- background build runner

_build_lock = threading.Lock()
_build_state: dict = {"running": False, "started_at": None,
                      "finished_at": None, "ok": None, "error": None,
                      "version": None, "backend": None}


def skill_build_status() -> dict:
    with _build_lock:
        return copy.deepcopy(_build_state)


def _run_build(backend: str) -> None:
    try:
        res = build_solver_skill(backend=backend)
        with _build_lock:
            _build_state.update(
                running=False,
                finished_at=datetime.now().isoformat(timespec="seconds"),
                ok=bool(res.get("ok")), error=res.get("error"),
                version=(res.get("meta") or {}).get("version"),
                backend=res.get("backend") or backend)
    except Exception as exc:                            # noqa: BLE001
        with _build_lock:
            _build_state.update(
                running=False,
                finished_at=datetime.now().isoformat(timespec="seconds"),
                ok=False, error=f"{type(exc).__name__}: {exc}")


def start_skill_build(backend: str = "cloud") -> dict:
    """Kick off a skill rebuild in a daemon thread (one at a time). Returns
    the status snapshot; the UI polls skill_build_status()."""
    with _build_lock:
        if _build_state["running"]:
            return {**copy.deepcopy(_build_state), "already_running": True}
        _build_state.update(
            running=True,
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None, ok=None, error=None, version=None,
            backend=backend)
    threading.Thread(target=_run_build, args=(backend,), daemon=True).start()
    return {**skill_build_status(), "already_running": False}


# --------------------------------------- оценка эффективности публикаций

# Оценка «насколько находка агента-исследователя способна улучшить наш
# пейринг/ростеринг» — совещательная величина для сортировки вкладки
# «Алгоритмы». Оценивает СОВЕТНИК (его база знаний и его usage-модель):
# предмет — применимость знания к нашей задаче, это домен советника;
# агенты Автоулучшения остаются исполнителями отобранных находок.

IMPACT_TIMEOUT_S = 90.0

# Дата публикации первоисточника (не дата находки агентом!). Для arXiv она
# зашита в сам идентификатор; для остальных источников её называет модель в
# ответе оценки (published), а при незнании остаётся null.
_ARXIV_NEW_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/(\d{2})(\d{2})\.\d{4,5}", re.IGNORECASE)
_ARXIV_OLD_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/[a-z-]+(?:\.[A-Z]{2})?/(\d{2})(\d{2})\d{3}",
    re.IGNORECASE)
_PUBLISHED_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def normalize_published(value: object) -> str | None:
    """ГГГГ / ГГГГ-ММ / ГГГГ-ММ-ДД с проверкой диапазонов; иначе None."""
    if not isinstance(value, str):
        return None
    m = _PUBLISHED_RE.match(value.strip())
    if not m:
        return None
    year = int(m.group(1))
    if not 1980 <= year <= 2035:
        return None
    if m.group(2) is not None and not 1 <= int(m.group(2)) <= 12:
        return None
    if m.group(3) is not None and not 1 <= int(m.group(3)) <= 31:
        return None
    return "-".join(g for g in m.groups() if g is not None)


def published_from_url(url: str | None) -> str | None:
    """Дата публикации (ГГГГ-ММ) из URL, если источник её кодирует (arXiv)."""
    if not url:
        return None
    m = _ARXIV_NEW_RE.search(url)
    if m:
        yy, mm = m.groups()
        if 1 <= int(mm) <= 12:
            return f"20{yy}-{mm}"
        return None
    m = _ARXIV_OLD_RE.search(url)
    if m:
        yy, mm = m.groups()
        if 1 <= int(mm) <= 12:
            century = "19" if int(yy) >= 90 else "20"
            return f"{century}{yy}-{mm}"
    return None


def impact_cache_key(finding: dict, skill_version: str | None) -> str:
    """Ключ инвалидации кэша: содержимое находки + версия скилла."""
    basis = "|".join([
        str(finding.get("title") or ""),
        str(finding.get("summary") or ""),
        str(finding.get("expected_gain") or ""),
        str(finding.get("integration_plan") or ""),
        str(skill_version or "")])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def build_impact_prompt(skill_text: str, finding: dict) -> str:
    return (
        "Ты — старший инженер по оптимизации расписаний экипажей. Дана "
        "база знаний нашего стека решателей (skill) и находка "
        "агента-исследователя (публикация по crew pairing/rostering). "
        "Оцени, насколько применение этой публикации способно улучшить "
        "НАШ конвейер пейринга и ростеринга.\n\n"
        "=== БАЗА ЗНАНИЙ (skill.md) ===\n" + (skill_text or "")[:12000]
        + "\n\n=== НАХОДКА ===\n"
        + json.dumps({k: finding.get(k) for k in
                      ("title", "summary", "expected_gain",
                       "integration_plan", "source_url")},
                     ensure_ascii=False, indent=1) + "\n\n"
        "Ответь СТРОГО одним JSON-объектом, без текста вокруг:\n"
        '{\n'
        '  "score": <целое 0..100 — потенциал улучшения именно нашего '
        'стека>,\n'
        '  "confidence": "low"|"med"|"high",\n'
        '  "rationale": "<2-4 предложения по-русски: что именно и на '
        'каком этапе улучшит, чем рискует>",\n'
        '  "published": "<дата ПУБЛИКАЦИИ первоисточника в формате '
        'ГГГГ-ММ или ГГГГ-ММ-ДД, если она известна по идентификатору, '
        'URL или самой публикации; иначе null — НЕ выдумывай>"\n'
        '}')


def _normalize_impact(d: dict) -> dict | None:
    try:
        score = int(d.get("score"))
    except (TypeError, ValueError):
        return None
    score = max(0, min(100, score))
    conf = str(d.get("confidence") or "").strip().lower()
    if conf not in ("low", "med", "high"):
        conf = "low"
    return {"score": score, "confidence": conf,
            "rationale": str(d.get("rationale") or "").strip()[:2000],
            "published": normalize_published(d.get("published"))}


def parse_impact(text: str) -> dict | None:
    for d in reversed(_extract_json_objects(text)):
        if "score" in d:
            norm = _normalize_impact(d)
            if norm is not None:
                return norm
    return None


def score_finding(finding: dict, *, backend_request: str = "cloud",
                  timeout_s: float = IMPACT_TIMEOUT_S) -> dict:
    """Оценка одной находки. Не бросает: при недоступности модели —
    честный источник "unscored" без числа (сортировка ставит в конец)."""
    skill = load_skill()
    version = (skill.get("meta") or {}).get("version")
    key = impact_cache_key(finding, version)
    base = {"cache_key": key, "skill_version": version,
            "scored_at": datetime.now().isoformat(timespec="seconds")}
    try:
        # complete() возвращает СЛОВАРЬ {ok, text, backend, model, error} и
        # не бросает на ожидаемых отказах — сначала смотрим ok, парсим text.
        res = llm_backend.complete(
            build_impact_prompt(skill.get("text") or "", finding),
            backend=backend_request, timeout_s=timeout_s, usage="advisor")
        if not res.get("ok"):
            return {**base, "source": "unscored",
                    "error": str(res.get("error") or "модель недоступна"),
                    "model": res.get("model")}
        norm = parse_impact(res.get("text") or "")
        if norm is None:
            return {**base, "source": "unscored",
                    "error": "ответ модели не разобран",
                    "model": res.get("model")}
        return {**base, **norm, "source": "llm",
                "backend": backend_request, "model": res.get("model")}
    except Exception as exc:                            # noqa: BLE001
        return {**base, "source": "unscored",
                "error": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------- background impact batch run

_impact_lock = threading.Lock()
_impact_state: dict = {"running": False, "started_at": None,
                       "finished_at": None, "total": 0, "scored": 0,
                       "skipped": 0, "failed": 0, "error": None}


def impact_status() -> dict:
    with _impact_lock:
        return copy.deepcopy(_impact_state)


def _run_impact_batch(backend: str) -> None:
    from webapp import evolution
    try:
        skill_version = (load_skill().get("meta") or {}).get("version")
        rows = evolution.list_all()
        with _impact_lock:
            _impact_state["total"] = len(rows)
        for f in rows:
            # Дата публикации в первоисточнике: URL-эвристика надёжнее
            # ответа модели, поэтому она главная; заполняется и на
            # кэш-пропуске (колонка появилась позже первых оценок).
            url_pub = published_from_url(f.get("source_url"))
            cached = f.get("impact") or {}
            if (cached.get("source") == "llm" and cached.get("cache_key")
                    == impact_cache_key(f, skill_version)):
                pub = url_pub or cached.get("published")
                if pub and pub != f.get("published_at"):
                    evolution.set_published(int(f["id"]), pub)
                with _impact_lock:
                    _impact_state["skipped"] += 1
                continue
            impact = score_finding(f, backend_request=backend)
            evolution.attach_impact(int(f["id"]), impact)
            pub = url_pub or (impact.get("published")
                              if impact.get("source") == "llm" else None)
            if pub and pub != f.get("published_at"):
                evolution.set_published(int(f["id"]), pub)
            with _impact_lock:
                if impact.get("source") == "llm":
                    _impact_state["scored"] += 1
                else:
                    _impact_state["failed"] += 1
        with _impact_lock:
            _impact_state.update(
                running=False,
                finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as exc:                            # noqa: BLE001
        with _impact_lock:
            _impact_state.update(
                running=False,
                finished_at=datetime.now().isoformat(timespec="seconds"),
                error=f"{type(exc).__name__}: {exc}")


def scoring_allowed() -> tuple[bool, str]:
    """Разрешена ли оценка находок НА ЭТОМ контуре.

    Оценка — общесистемный факт о публикации: балл одинаков для всех
    контуров и синхронизируется реестром (tools/lab_registry.py). Считать
    его должен ровно один контур, иначе получаются три несравнимых числа
    для одной статьи — ровно та беда, которую разбирали 10.08.2026.

    Владелец оценки — ВНЕШНИЙ контур (установка Test): там разрешены
    облачные модели по контурному инварианту evolution_runner. Признак
    берём оттуда же, а не новой переменной окружения: контур определяется
    совпадением корня установки с COSMOS_TEST_DIR.

    Важно, почему это ЗАПРЕТ, а не предупреждение: батч пишет результат
    через attach_impact БЕЗУСЛОВНО — даже отказ модели («ответ модели не
    разобран», «модель недоступна») ложится в базу и стирает ранее
    синхронизированный балл. Одно нажатие на внутреннем контуре обнуляет
    колонку целиком.
    """
    try:
        from . import evolution_runner
        here = Path(REPO_ROOT).resolve()
        test = evolution_runner.test_dir().resolve()
    except Exception as exc:                            # noqa: BLE001
        # Неизвестность трактуем как запрет: молча испортить чужие баллы
        # хуже, чем не посчитать свои.
        return False, f"контур не определён: {type(exc).__name__}: {exc}"
    if here == test:
        return True, ""
    return False, (
        "оценка выполняется только на внешнем контуре (установка Test): "
        f"здесь {here.name}, оценочный контур {test.name}. Результат "
        "разносится на остальные контуры реестром находок.")


def start_impact_scoring(backend: str = "cloud") -> dict:
    """Батч-оценка всех находок вкладки «Алгоритмы» (демон-поток, один
    прогон за раз; уже оценённые с совпадающим ключом кэша пропускаются).

    На неоценочном контуре не запускается вовсе: см. scoring_allowed."""
    ok, reason = scoring_allowed()
    if not ok:
        return {**copy.deepcopy(_impact_state), "refused": True,
                "error": reason}
    with _impact_lock:
        if _impact_state["running"]:
            return {**copy.deepcopy(_impact_state), "already_running": True}
        _impact_state.update(
            running=True,
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None, total=0, scored=0, skipped=0, failed=0,
            error=None)
    threading.Thread(target=_run_impact_batch, args=(backend,),
                     daemon=True).start()
    return {**impact_status(), "already_running": False}
