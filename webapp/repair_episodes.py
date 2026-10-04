"""Журнал ЭПИЗОДОВ ремонта — что предлагалось и что приняли (волна 2).

Это ЗАПИСЬ НАБЛЮДЕНИЯ, а не контур: агента, метрики и строки на странице
«Контуры» у него пока нет. Причина прямая — на всех трёх контурах
установки ноль ремонтов за всё время, и любой порог кластера («пять
повторов одной ситуации») не набрался бы и за месяцы. Заводить строку,
которая гарантированно показывает ноль, значит обещать наблюдение,
которого нет.

Почему тогда пишем сейчас. Планировщик ремонта считает ПОЛНЫЙ
ранжированный список законных замен на каждое сломанное место, берёт
первого и молча выбрасывает остальных. Это единственное место, где
видно, ЧТО человеку предлагали помимо принятого, — и восполнить это
задним числом невозможно: список пересчитывается по состоянию ростера в
момент ремонта, которого больше нет. Каждый выполненный ремонт без
записи — безвозвратно потерянное наблюдение.

Что записывается: экземпляр, ключ ситуации, место, с кого сняли и кого
поставили, отвергнутые варианты (ограниченно) и сколько их было всего,
способ (прямая замена или обмен), кто и когда применил.

Ключ ситуации приходит из планировщика и выведен из СОДЕРЖИМОГО пейринга
плюс экземпляра — не из `pid`. Тот перенумеровывается при каждой
пересборке каталога, и группировка по нему склеила бы неродственные
пейринги разных прогонов.

Контур ДЕТЕРМИНИРОВАННЫЙ по построению: записи несут табельные номера, а
причина ремонта — болезнь сотрудника, то есть данные, к которым близко
не подходит ни одна облачная модель.

Дисциплина хранилища — как в data_health/connection_buffer: модульный
лок, атомарная запись, и три различных исхода чтения (нет файла / битый
файл законно деградируют в пусто; «не смогли прочитать» ЗАПИСЬ отменяет).
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import registry

EPISODES_PATH = registry.REPO_ROOT / "webapp" / "config" / \
    "repair_episodes.json"

# Потолки те же по смыслу, что у журнала откликов планировщика: история
# нужна для повторов, а не как вечный архив.
MAX_EPISODES = 5000
MAX_EPISODES_TOTAL = 20000
READ_RETRIES = 5
READ_RETRY_PAUSE = 0.05

_lock = threading.Lock()


class EpisodeStoreUnreadable(RuntimeError):
    """Хранилище есть, но прочитать его не удалось — писать нельзя."""


_CORRUPT = object()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_raw():
    for attempt in range(READ_RETRIES):
        try:
            return EPISODES_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError:
            return _CORRUPT
        except OSError as exc:
            if attempt == READ_RETRIES - 1:
                raise EpisodeStoreUnreadable(
                    f"журнал эпизодов ремонта недоступен: {EPISODES_PATH}"
                ) from exc
            time.sleep(READ_RETRY_PAUSE)
    return None                                 # недостижимо, для линтера


def _load_strict() -> dict:
    raw = _read_raw()
    if raw is None or raw is _CORRUPT:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load() -> dict:
    try:
        return _load_strict()
    except EpisodeStoreUnreadable:
        return {}


def _replace_with_retry(tmp: Path, dst: Path) -> None:
    for attempt in range(5):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def _save_locked(data: dict) -> None:
    EPISODES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EPISODES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    _replace_with_retry(tmp, EPISODES_PATH)


def _enforce_global_cap(data: dict) -> None:
    """Общий потолок срезает САМЫЙ БОЛЬШОЙ экземпляр, а не самый старый
    эпизод: иначе один шумный экземпляр вытеснил бы историю всех
    остальных (урок волны 1)."""
    total = sum(len(v) for v in data.values() if isinstance(v, list))
    while total > MAX_EPISODES_TOTAL:
        biggest = max((k for k, v in data.items() if isinstance(v, list)),
                      key=lambda k: len(data[k]), default=None)
        if biggest is None or not data[biggest]:
            return
        drop = min(len(data[biggest]), total - MAX_EPISODES_TOTAL)
        data[biggest] = data[biggest][drop:]
        total -= drop


def record_plan(inst_id: str, plan: dict, by: str | None = None,
                applied: int | None = None) -> list[dict]:
    """Записать эпизоды ПРИМЕНЁННОГО плана ремонта. Возвращает записи.

    Пишутся только строки, которые действительно применились: `applied`
    — длина применённого префикса из apply_repair. План, отвергнутый
    целиком, наблюдением не является: человек его не выбирал."""
    inst_id = str(inst_id or "")
    if not inst_id or not plan.get("available"):
        return []
    rows = plan.get("repairs") or []
    seq_len = len(plan.get("apply_sequence") or [])
    limit = seq_len if applied is None else max(0, int(applied))
    if limit <= 0:
        return []
    # repairs и apply_sequence идут в одном порядке и одной длины: обмен
    # добавляет по строке в оба списка. Берём столько же строк, сколько
    # реально применилось.
    rows = rows[:limit]
    at, batch = _now(), "rp" + secrets.token_hex(4)
    made = []
    for r in rows:
        made.append({
            "id": "re" + secrets.token_hex(4),
            "at": at, "batch": batch, "inst": inst_id,
            "skey": r.get("skey"), "pid": r.get("pid"),
            "slot": r.get("slot"), "from": r.get("from"), "to": r.get("to"),
            "via": r.get("via"),
            "alts": [str(a) for a in (r.get("alts") or [])],
            "alts_total": int(r.get("alts_total") or 0),
            "by": str(by or ""),
        })
    if not made:
        return []
    with _lock:
        data = _load_strict()
        bucket = data.get(inst_id)
        if not isinstance(bucket, list):
            bucket = []
        bucket.extend(made)
        data[inst_id] = bucket[-MAX_EPISODES:]
        _enforce_global_cap(data)
        _save_locked(data)
    return made


def episodes(inst_id: str | None = None) -> list[dict]:
    data = _load()
    keys = [str(inst_id)] if inst_id else sorted(data)
    out: list[dict] = []
    for k in keys:
        rows = data.get(k)
        if isinstance(rows, list):
            out.extend(r for r in rows if isinstance(r, dict))
    return out


def counts() -> dict:
    """Сводка для будущего контура и для проверки, что запись идёт."""
    rows = episodes()
    return {
        "episodes": len(rows),
        "instances": len({r.get("inst") for r in rows if r.get("inst")}),
        "situations": len({(r.get("inst"), r.get("skey")) for r in rows
                           if r.get("skey")}),
        "with_alternatives": sum(1 for r in rows if r.get("alts")),
        "last_at": max((str(r.get("at") or "") for r in rows), default="")
        or None,
    }
