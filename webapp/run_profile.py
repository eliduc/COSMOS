"""Контур «Надёжность стенда» (2026-07-20): профиль поведения самой
расчётной программы.

Наблюдаем не качество ростера, а стенд: укладывается ли задача в
отведённый бюджет времени, как часто она падает. Из этого растут
черновики предложений («поднять бюджет по умолчанию», «разобрать
отказы») — вместо угадывания настроек на глаз.

ДЕТЕРМИНИРОВАННО И БЕЗ ЗАВИСИМОСТЕЙ. Ни одного вызова языковой модели:
данные внутреннего контура (в метках задач встречаются имена экземпляров
и пользователей), облако запрещено. Кластеризация — по ТОЧНОМУ ключу
типа задачи, не по смыслу. Стенд принципиально stdlib-only, поэтому
собираем только то, что достаётся даром из уже существующего запуска:
тип, метка, размер, бюджет, факт, исход, код выхода. Никакого psutil —
ни памяти, ни загрузки процессора здесь принципиально нет.

Выход контура — ТОЛЬКО черновик (status="draft"); в рабочие настройки
(airline_default.json, cost_config, pay_config) модуль не пишет никогда.
Сам в change_requests.py тоже не пишет: build_crs ВОЗВРАЩАЕТ строки,
регистрирует их вызывающий.

Дисциплина хранения — как в ops_log.py: модульный замок, атомарная
запись tmp->replace с ретраем PermissionError под Windows. Чтение
ЗАЩИТНОЕ и в противоположную сторону: это телеметрия, а не данные
заказчика, поэтому битый файл деградирует в «нет наблюдений», а не роняет
вызывающий эндпоинт 500-й. Хранилище — runtime-состояние
webapp/config/run_profile.json.
"""
from __future__ import annotations

import json
import secrets
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import registry

PROFILE_PATH = registry.REPO_ROOT / "webapp" / "config" / "run_profile.json"

# Исходы прогона. "stopped" — остановка человеком: это НЕ отказ стенда и
# в долю отказов не входит, но и об укладываемости в бюджет ничего не
# говорит (прогон оборвали раньше времени). "failed" считается в долю
# отказов, но во времена — тоже нет: упавший прогон не дошёл до конца.
OUTCOMES = ("succeeded", "failed", "stopped")

# Держим только хвост наблюдений: профиль отвечает на вопрос «как стенд
# ведёт себя СЕЙЧАС», а файл не должен расти без предела.
MAX_ROWS = 2000

MAX_LABEL = 120
MAX_JTYPE = 60
# Потолки описания конфигурации: строка наблюдения обязана оставаться
# маленькой. Флаги известны и их единицы, но источник — argv, а argv в
# принципе может принести длинный путь.
MAX_CONFIG_SIG = 32
MAX_CFG_KEYS = 24
MAX_CFG_KEY = 40
MAX_CFG_VAL = 120

_lock = threading.Lock()


class RunProfileError(ValueError):
    """Некорректный ввод наблюдения; текст безопасно показать пользователю."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> list[dict]:
    # Телеметрия: битое/нечитаемое хранилище деградирует в пусто. Потерять
    # наблюдения не страшно (в отличие от журнала фактов), а вот уронить
    # вызывающий эндпоинт из-за одного битого байта — страшно.
    if not PROFILE_PATH.exists():
        return []
    raw = None
    for attempt in range(5):
        try:
            raw = PROFILE_PATH.read_text(encoding="utf-8")
            break
        except OSError:
            # разделяемый доступ под Windows: параллельный replace
            if attempt == 4:
                return []
            time.sleep(0.05)
    try:
        data = json.loads(raw or "")
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def _replace_with_retry(tmp: Path, dst: Path) -> None:
    for attempt in range(5):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def _save_locked(rows: list[dict]) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    _replace_with_retry(tmp, PROFILE_PATH)


def _opt_int(value, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RunProfileError(f"{field} must be an integer") from exc


def _opt_float(value, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise RunProfileError(f"{field} must be a number of seconds") from exc
    # отрицательная длительность/бюджет — ошибка измерения, не наблюдение
    if f < 0:
        raise RunProfileError(f"{field} must not be negative")
    return f


def _clip(value, limit: int) -> str:
    return str(value or "")[:limit]


def record_run(*, jtype, label, legs=None, budget_s=None, wall_s=None,
               outcome, exit_code=None, gate_nonzero=False,
               instance_id=None, config_sig=None,
               resolved_config=None) -> dict:
    """Append one observation about a stand run; returns the stored row.

    instance_id / config_sig / resolved_config добавлены волной 2: без
    них наблюдение не отвечало на вопрос «что именно запускали» — а
    сравнивать два прогона, не зная их конфигураций, нельзя вовсе. Поля
    необязательные: старые строки остаются валидными, у них просто нет
    метки, и любое будущее сравнение обязано их пропускать, а не считать
    одинаковыми."""
    if outcome not in OUTCOMES:
        raise RunProfileError(
            f"unknown outcome {outcome!r}; expected one of {OUTCOMES}")
    jt = _clip(jtype, MAX_JTYPE)
    if not jt:
        raise RunProfileError("jtype is required")
    cfg = resolved_config if isinstance(resolved_config, dict) else None
    row = {
        "at": _now(),
        "jtype": jt,
        "label": _clip(label, MAX_LABEL),
        "instance_id": _clip(instance_id, MAX_LABEL) or None,
        "config_sig": _clip(config_sig, MAX_CONFIG_SIG) or None,
        "config": {str(k)[:MAX_CFG_KEY]: str(v)[:MAX_CFG_VAL]
                   for k, v in list(cfg.items())[:MAX_CFG_KEYS]}
        if cfg else None,
        "legs": _opt_int(legs, "legs"),
        "budget_s": _opt_float(budget_s, "budget_s"),
        "wall_s": _opt_float(wall_s, "wall_s"),
        "outcome": outcome,
        "exit_code": _opt_int(exit_code, "exit_code"),
        "gate_nonzero": bool(gate_nonzero),
    }
    with _lock:
        rows = _load()
        rows.append(row)
        if len(rows) > MAX_ROWS:
            rows = rows[-MAX_ROWS:]
        _save_locked(rows)
    return row


def runs(jtype: str | None = None) -> list[dict]:
    """Observations, oldest first; optionally one job type only."""
    rows = _load()
    if jtype is None:
        return rows
    return [r for r in rows if r.get("jtype") == jtype]


def _num(value) -> float | None:
    """Число из ЗАЩИТНОГО чтения: чужая/старая строка может нести строку
    или null там, где мы ждём секунды."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _p90(values: list[float]) -> float:
    """90-й перцентиль методом ближайшего ранга — на чистом Python, без
    интерполяции: выборки здесь маленькие и рваные, а ближайший ранг
    всегда указывает на РЕАЛЬНО наблюдавшийся прогон."""
    ordered = sorted(values)
    rank = max(1, -(-90 * len(ordered) // 100))     # ceil(0.9*n)
    return round(ordered[min(rank, len(ordered)) - 1], 1)


def profile() -> dict:
    """Aggregates per job type over the stored observations.

    Счётчики исходов (runs/succeeded/failed/stopped/failure_pct) — по ВСЕМ
    наблюдениям; времена и укладываемость (median/p90/within_budget_pct) —
    только по успешно завершённым прогонам."""
    by_type: dict[str, dict] = {}
    buckets: dict[str, list[dict]] = {}
    for r in _load():
        jt = str(r.get("jtype") or "")
        if jt:
            buckets.setdefault(jt, []).append(r)
    total = 0
    for jt, rows in buckets.items():
        n = len(rows)
        total += n
        outcomes = {o: 0 for o in OUTCOMES}
        for r in rows:
            o = r.get("outcome")
            if o in outcomes:
                outcomes[o] += 1
        # Ревью волны 1: о БЮДЖЕТЕ говорит только прогон, дошедший до
        # конца. Оборванный человеком (stopped) остановлен раньше времени,
        # упавший (failed) умер на полпути — их время меньше настоящего, и
        # в укладываемость/медиану/перцентиль они подмешивают оптимизм.
        # Отказы считаются отдельной метрикой failure_pct.
        done = [r for r in rows if r.get("outcome") == "succeeded"]
        walls = [w for w in (_num(r.get("wall_s")) for r in done)
                 if w is not None]
        # укладываемость — только там, где известны ОБА числа: бюджет без
        # факта (или наоборот) ничего не сравнивает
        paired = [(b, w) for b, w in
                  ((_num(r.get("budget_s")), _num(r.get("wall_s")))
                   for r in done)
                  if b is not None and w is not None]
        within = (_pct(sum(1 for b, w in paired if w <= b), len(paired))
                  if paired else None)
        by_type[jt] = {
            "runs": n,
            "succeeded": outcomes["succeeded"],
            "failed": outcomes["failed"],
            "stopped": outcomes["stopped"],
            "within_budget_pct": within,
            "median_wall_s": (round(statistics.median(walls), 1)
                              if walls else None),
            "p90_wall_s": _p90(walls) if walls else None,
            "failure_pct": _pct(outcomes["failed"], n),
        }
    return {"by_type": by_type, "total_runs": total}


def _priority(deviation: float) -> int:
    """Приоритет от ВЕЛИЧИНЫ отклонения от порога (в процентных пунктах),
    один пункт приоритета на каждые 10 п.п., не выше 10."""
    return max(1, min(10, -(-int(round(deviation)) // 10)))


def _cr(title: str, summary: str, priority: int) -> dict:
    return {
        "id": "cr" + secrets.token_hex(4),
        "agent": "runtime",
        "title": _clip(title, 90),
        "summary": _clip(summary, 600),
        "priority": priority,
        "effort": 2,
        "status": "draft",
        "source": None,
        "country": None,
        "created_at": _now(), "updated_at": _now(),
    }


def _sample_note(agg: dict) -> str:
    """Хвост описания: фактические числа выборки + честная оговорка. Без
    них предложение неотличимо от догадки. Число успешных названо явно:
    времена считаются только по ним, и читатель должен видеть, на скольких
    прогонах стоит медиана."""
    med = agg["median_wall_s"]
    p90 = agg["p90_wall_s"]
    return (f"Выборка: {agg['runs']} прогонов "
            f"(успешно завершились {agg['succeeded']}), медиана "
            f"{med if med is not None else 'н/д'} с, 90-й перцентиль "
            f"{p90 if p90 is not None else 'н/д'} с. "
            f"Один прогон ничего не доказывает: это статистика хвоста "
            f"наблюдений, а не воспроизведённый эксперимент.")


def build_crs(min_runs: int = 10, within_budget_floor: float = 80.0,
              failure_ceiling: float = 10.0) -> list[dict]:
    """Draft CRs from the run profile (the caller registers them)."""
    out: list[dict] = []
    prof = profile()
    # порядок типов фиксирован по имени: два одинаковых состояния
    # хранилища обязаны дать одинаковый список черновиков
    for jt in sorted(prof["by_type"]):
        agg = prof["by_type"][jt]
        if agg["runs"] < min_runs:
            continue
        within = agg["within_budget_pct"]
        if within is not None and within < within_budget_floor:
            dev = within_budget_floor - within
            out.append(_cr(
                f"Поднять бюджет по умолчанию для задач «{jt}»",
                f"В бюджет укладывается {within}% прогонов типа «{jt}» "
                f"при пороге {within_budget_floor}%. Предложение: поднять "
                f"бюджет времени по умолчанию до уровня 90-го перцентиля "
                f"фактического времени, чтобы обычный прогон доходил до "
                f"конца, а не обрывался по времени. " + _sample_note(agg),
                _priority(dev)))
        failure = agg["failure_pct"]
        if failure > failure_ceiling:
            dev = failure - failure_ceiling
            out.append(_cr(
                f"Разобрать отказы прогонов типа «{jt}»",
                f"Отказом завершились {failure}% прогонов типа «{jt}» "
                f"({agg['failed']} из {agg['runs']}) при пороге "
                f"{failure_ceiling}%. Предложение: разобрать коды выхода и "
                f"журналы отказавших прогонов и устранить общую причину. "
                + _sample_note(agg),
                _priority(dev)))
    return out
