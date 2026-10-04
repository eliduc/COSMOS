"""Контур «Здоровье данных» — периодический СНИМОК дефектов экземпляра
(2026-07-20).

Дефекты уже считаются на лету при открытии экрана, но нигде не оседают:
владелец данных видит «сейчас плохо», но не видит, стало ли хуже. Здесь
тот же счёт складывается в историю снимков, а рост числа дефектов
превращается в ЧЕРНОВИК предложения (CR со статусом draft).

Контур ДЕТЕРМИНИРОВАННЫЙ: ни одного вызова языковой модели. Данные несут
ФИО и табельные номера (внутренний контур), поэтому кластеризация видов
дефектов — по точным ключам, а не по смыслу; тексты предложений
собираются из строк самих отчётов, ничего не додумывается.

Контур НИЧЕГО не чинит и НЕ пишет в рабочие настройки: его выход —
история в webapp/config/data_health.json плюс список черновиков CR,
который регистрирует вызывающий.

Дисциплина хранилища — как в ops_log.py (модульный лок, атомарная запись
tmp->replace с ретраем PermissionError под Windows). Чтение РАЗДЕЛЕНО на
три исхода, и путать их нельзя: «файла нет» и «файл битый» законно
деградируют в «истории нет» (её пересоберёт следующий снимок), а вот «не
смогли прочитать» — это НЕ пустая история, и запись поверх такого исхода
стёрла бы снимки всех экземпляров из-за одной временной ошибки ввода-
вывода. Путь показа деградирует, путь ЗАПИСИ отказывается работать.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import anomaly_report, registry

SNAPSHOT_PATH = registry.REPO_ROOT / "webapp" / "config" / "data_health.json"

AGENT = "datahealth"

# Сколько снимков храним на экземпляр: история нужна для динамики, а не
# для вечного архива — старые срезы вытесняются.
MAX_HISTORY = 200
# Примеров на вид дефекта в снимке (контракт: «до 5 строк»).
MAX_DETAILS = 5
MAX_DETAIL_LEN = 200
MAX_TITLE = 90
MAX_SUMMARY = 600
CR_EFFORT = 3
MAX_PRIORITY = 10
# Ретраи чтения хранилища: столько же и с тем же шагом, что у записи и у
# соседних контуров (planner_feedback, run_profile).
READ_RETRIES = 5
READ_RETRY_PAUSE = 0.05

# Источник снимка. «Ручной» — кнопка на экране контуров, «агентский» —
# прогон агента. Различие несёт смысл: база ПРЕДЛОЖЕНИЙ отсчитывается
# только от агентских снимков (см. _cr_baseline).
BY_MANUAL = "manual"
BY_AGENT = "agent"

# Наивный ISO с секундами — ровно тот формат, который движок понимает
# буквально. Строка со смещением зоны ('+03:00', 'Z') СЮДА НЕ ПОДХОДИТ:
# смещение отбрасывается без пересчёта (описано в мануале), и рейс
# уезжает на несколько часов.
_NAIVE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
# Ведущее число в строке отчёта аномалий («12 рейс(ов) вне …»): это
# ФОРМАТ САМОГО ОТЧЁТА, а не догадка о смысле — если числа нет, строка
# считается одним дефектом.
_LEADING_COUNT_RE = re.compile(r"^(\d+)\s")
# Ошибка валидации про место экипажа, ссылающееся на несуществующий рейс
# или несуществующее плечо — дословный формат registry.validate_payload.
# Ровно эти дефекты считает и _orphan_slot_defects, поэтому из вида
# "validation" они вычитаются ПО ИНДЕКСУ МЕСТА (другие ошибки того же
# места, например «плечо не принадлежит рейсу», остаются в валидации).
_VALIDATION_SLOT_REF_RE = re.compile(
    r"^flightAssignments\[(\d+)\][^\n]*not in flights")

# Виды дефектов из anomaly_report.report(): ключ вида -> маркер, по
# которому строка предупреждения к нему относится. Маркеры — дословные
# куски формулировок anomaly_report; новых видов здесь не изобретается,
# всё неопознанное падает в "anomaly_other".
_ANOMALY_KINDS: tuple[tuple[str, str], ...] = (
    ("stray_month", "вне основного месяца"),
    ("dup_employee_id", "дубликаты табельных номеров"),
    ("namesakes", "полные тёзки"),
    ("idle_employee", "без единого назначения"),
    ("block_hours_outlier", "выброс налёта"),
    ("low_block_hours", "аномально низкий налёт"),
    ("mostly_unavailable", "дней горизонта"),
)
_ANOMALY_OTHER = "anomaly_other"
# Хвост-заглушка отчёта («… и ещё N») — не дефект, а признак усечения.
_CAP_MARKER = "… и ещё"

# Человеческие названия видов для заголовков предложений.
KIND_LABELS: dict[str, str] = {
    "stray_month": "рейсы вне основного месяца",
    "dup_employee_id": "дубликаты табельных",
    "namesakes": "полные тёзки в экипаже",
    "idle_employee": "сотрудники без назначений",
    "block_hours_outlier": "выбросы налёта",
    "low_block_hours": "аномально низкий налёт",
    "mostly_unavailable": "почти полностью недоступные",
    _ANOMALY_OTHER: "прочие аномалии истории",
    "validation": "ошибки валидации",
    "time_format": "нарушения формата времени",
    "orphan_slot": "места экипажа без рейса",
}

_lock = threading.Lock()
# Отдельный лок для временного снятия потолка в anomaly_report (см.
# _report_full): он защищает чужую глобальную настройку, а не наш файл,
# поэтому вложенности с _lock нет и быть не должно.
_anomaly_cap_lock = threading.Lock()


class SnapshotStoreUnreadable(RuntimeError):
    """Хранилище есть, но прочитать его не удалось.

    Отличается от «истории нет» принципиально: пустая история — законное
    состояние, а нечитаемый файл — неизвестное. Записывать поверх
    неизвестного нельзя (иначе шаринг-нарушение под Windows, антивирус
    или второй воркер стирают снимки ВСЕХ экземпляров)."""


# Сырьё прочитано, но текстом не оказалось: ретрай не поможет, лечится
# только перезаписью — законно деградируем в пусто.
_CORRUPT = object()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_raw():
    """Сырой текст хранилища: None — файла нет, _CORRUPT — не текст.

    Единственный источник исключения SnapshotStoreUnreadable: временная
    ошибка ввода-вывода переживает короткие ретраи."""
    for attempt in range(READ_RETRIES):
        try:
            return SNAPSHOT_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError:
            return _CORRUPT
        except OSError as exc:
            if attempt == READ_RETRIES - 1:
                raise SnapshotStoreUnreadable(
                    f"снимки здоровья данных недоступны: {SNAPSHOT_PATH}"
                ) from exc
            time.sleep(READ_RETRY_PAUSE)
    return None                                 # недостижимо, для линтера


def _load_strict() -> dict:
    """История всех экземпляров; нечитаемое хранилище — ИСКЛЮЧЕНИЕ.

    Путь ЗАПИСИ обязан идти через эту функцию: пустой словарь отсюда
    означает «истории действительно нет», а не «не смогли посмотреть»."""
    raw = _read_raw()
    if raw is None or raw is _CORRUPT:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load() -> dict:
    """То же для ПОКАЗА: здесь нечитаемое хранилище деградирует в пусто.

    Экран истории — производные данные, ронять его пятисоткой из-за
    занятого файла незачем; терять запись из-за него — есть зачем."""
    try:
        return _load_strict()
    except SnapshotStoreUnreadable:
        return {}


def _replace_with_retry(tmp: Path, dst: Path) -> None:
    # Под Windows параллельное чтение держит цель replace открытой —
    # короткий ретрай вместо падения записи.
    for attempt in range(5):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def _save_locked(data: dict) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    _replace_with_retry(tmp, SNAPSHOT_PATH)


# ------------------------------------------------------------ счёт дефектов

def _classify(warning: str) -> str:
    for kind, marker in _ANOMALY_KINDS:
        if marker in warning:
            return kind
    return _ANOMALY_OTHER


def _warning_weight(warning: str) -> int:
    """Сколько дефектов стоит за одной строкой отчёта аномалий."""
    m = _LEADING_COUNT_RE.match(warning)
    return int(m.group(1)) if m else 1


def _report_full(payload: dict) -> dict:
    """Отчёт аномалий БЕЗ потолка в 30 строк.

    Потолок отчёта — про размер экрана, а не про число дефектов: под ним
    из снимка исчезают ЦЕЛЫЕ ВИДЫ (виды печатаются по порядку, и поздние
    вытесняются многословными ранними), после чего тренд честно
    показывает несуществующее «улучшилось». Потолок снимаем ровно тогда,
    когда усечение реально случилось: сначала обычный вызов, и только
    при маркере «… и ещё N» — повторный под отдельным локом с возвратом
    прежнего значения в finally."""
    rep = anomaly_report.report(payload)
    warnings = [str(w) for w in (rep.get("warnings") or [])]
    if not any(w.startswith(_CAP_MARKER) for w in warnings):
        return rep
    with _anomaly_cap_lock:
        previous = anomaly_report.MAX_ITEMS
        try:
            anomaly_report.MAX_ITEMS = len(warnings) + _hidden_count(warnings)
            return anomaly_report.report(payload)
        finally:
            anomaly_report.MAX_ITEMS = previous


def _hidden_count(warnings: list[str]) -> int:
    """Сколько строк спрятал потолок — число из хвоста «… и ещё N»."""
    for w in warnings:
        if w.startswith(_CAP_MARKER):
            m = re.search(r"(\d+)", w)
            if m:
                return int(m.group(1))
    return 0


def _anomaly_defects(payload: dict) -> dict[str, list[str]]:
    """kind -> строки предупреждений anomaly_report (виды — из его вывода)."""
    try:
        rep = _report_full(payload)
    except Exception:                       # noqa: BLE001
        # Отчёт аномалий — вспомогательный источник: его сбой на кривом
        # payload'е не должен обнулять весь снимок (валидация и формат
        # времени ниже считаются независимо).
        return {}
    out: dict[str, list[str]] = {}
    for w in (rep.get("warnings") or []):
        text = str(w)
        if text.startswith(_CAP_MARKER):
            continue
        out.setdefault(_classify(text), []).append(text)
    return out


def _time_format_defects(payload: dict) -> list[str]:
    """Рейсы, чьё время вылета/прилёта не наивный ISO с секундами."""
    bad: list[str] = []
    flights = payload.get("flights")
    if not isinstance(flights, list):
        return bad
    for f in flights:
        if not isinstance(f, dict):
            continue
        for field in ("departureUTCDateTime", "arrivalUTCDateTime"):
            value = f.get(field)
            if not isinstance(value, str) or not _NAIVE_ISO_RE.match(value):
                bad.append(f"{f.get('flightNumber')}: {field}={value!r} — "
                           "ожидается 'ГГГГ-ММ-ДДTЧЧ:ММ:СС' без смещения зоны")
                break                       # рейс считаем один раз
    return bad


def _orphan_slot_defects(payload: dict) -> tuple[list[str], set[int]]:
    """Места экипажа, чей рейс не найден: (строки, индексы мест).

    Быстрый независимый пересчёт (validate_payload считает то же самое, но
    отдаёт лишь общее число ошибок под общим потолком — вид дефекта из неё
    не вытащить). Индексы мест возвращаются, чтобы ровно эти же дефекты
    не были посчитаны ВТОРОЙ раз ещё и в виде "validation"."""
    orphans: list[str] = []
    orphan_idx: set[int] = set()
    flights = payload.get("flights")
    slots = payload.get("flightAssignments")
    if not isinstance(slots, list):
        return orphans, orphan_idx
    numbers: set[str] = set()
    leg_ids: set[str] = set()
    if isinstance(flights, list):
        for f in flights:
            if not isinstance(f, dict):
                continue
            if isinstance(f.get("flightNumber"), str):
                numbers.add(f["flightNumber"])
            if isinstance(f.get("flightLegId"), str) and f["flightLegId"]:
                leg_ids.add(f["flightLegId"])
    for i, s in enumerate(slots):
        if not isinstance(s, dict):
            continue
        leg = s.get("flightLegId")
        # первоклассная идентичность плеча сильнее номера (C01/C02)
        if isinstance(leg, str) and leg:
            if leg in leg_ids:
                continue
            orphans.append(f"место {s.get('crewSlotId') or s.get('id')}: "
                           f"flightLegId={leg!r} нет среди рейсов")
            orphan_idx.add(i)
            continue
        fn = s.get("flight")
        if isinstance(fn, str) and fn in numbers:
            continue
        orphans.append(f"место {s.get('crewSlotId') or s.get('id')}: "
                       f"flight={fn!r} нет среди рейсов")
        orphan_idx.add(i)
    return orphans, orphan_idx


def _validation_defects(payload: dict,
                        orphan_idx: set[int]) -> tuple[list[str], bool]:
    """(строки ошибок валидации, признак насыщения потолка).

    Из списка вычтены ошибки о местах, которые уже посчитаны отдельным
    видом "orphan_slot": один дефект — один вид, иначе рост и починка
    осиротевших мест удваиваются в обоих счётчиках сразу.

    Второе значение — насыщение: validate_payload обрывает список на
    MAX_VALIDATION_ERRORS, поэтому на насыщенном счётчике и рост, и
    улучшение на порядок невидимы, и выводы о тренде по нему делать
    нельзя (снимок несёт это признаком, а не молчит)."""
    try:
        result = registry.validate_payload(payload)
    except Exception:                       # noqa: BLE001
        return [], False
    if result.get("ok"):
        return [], False
    errors = [str(e) for e in (result.get("errors") or [])]
    saturated = len(errors) >= registry.MAX_VALIDATION_ERRORS
    kept: list[str] = []
    for text in errors:
        m = _VALIDATION_SLOT_REF_RE.match(text)
        if m and int(m.group(1)) in orphan_idx:
            continue                        # уже посчитано как orphan_slot
        kept.append(text)
    return kept, saturated


def _trim(items: list[str]) -> list[str]:
    return [s[:MAX_DETAIL_LEN] for s in items[:MAX_DETAILS]]


def measure(payload: dict) -> dict:
    """Посчитать дефекты БЕЗ сохранения: counts / details / saturated.

    Виды с нулём в снимок не попадают — «вида нет» и «вида ноль» это одно
    и то же состояние, а хранить пустые ключи значит раздувать историю.

    saturated — виды, чей счётчик упёрся в чужой потолок и потому является
    лишь нижней оценкой; по таким видам предложения не строятся."""
    counts: dict[str, int] = {}
    details: dict[str, list[str]] = {}
    saturated: list[str] = []

    for kind, warnings in _anomaly_defects(payload).items():
        counts[kind] = sum(_warning_weight(w) for w in warnings)
        details[kind] = _trim(warnings)

    orphans, orphan_idx = _orphan_slot_defects(payload)
    validation, validation_saturated = _validation_defects(payload, orphan_idx)
    if validation_saturated:
        saturated.append("validation")

    for kind, rows in (("validation", validation),
                       ("time_format", _time_format_defects(payload)),
                       ("orphan_slot", orphans)):
        if rows:
            counts[kind] = len(rows)
            details[kind] = _trim(rows)

    return {"counts": counts, "details": details, "saturated": saturated}


# ----------------------------------------------------------------- снимки

def take_snapshot(inst_id: str, payload: dict,
                  by: str = BY_MANUAL) -> dict:
    """Посчитать дефекты экземпляра и дописать снимок в историю.

    by помечает ИСТОЧНИК снимка (кнопка или агент) — от этого зависит
    только выбор базы предложений; сам счёт от источника не зависит."""
    snap = {"at": _now(), "by": str(by), **measure(payload)}
    with _lock:
        # ЗАПИСЬ идёт через строгое чтение: нечитаемое хранилище должно
        # отказать, а не превратиться в пустую основу для перезаписи.
        data = _load_strict()
        rows = data.get(str(inst_id))
        if not isinstance(rows, list):
            rows = []
        rows = [r for r in rows if isinstance(r, dict)]
        rows.append(snap)
        data[str(inst_id)] = rows[-MAX_HISTORY:]
        _save_locked(data)
    return snap


def history(inst_id: str) -> list[dict]:
    """Снимки экземпляра по возрастанию времени (защитно: мусор отброшен)."""
    rows = _load().get(str(inst_id))
    if not isinstance(rows, list):
        return []
    clean = [r for r in rows if isinstance(r, dict) and r.get("at")]
    return sorted(clean, key=lambda r: str(r.get("at")))


def latest(inst_id: str) -> dict | None:
    rows = history(inst_id)
    return rows[-1] if rows else None


def _saturated_of(snap: dict | None) -> set[str]:
    """Виды снимка, чей счётчик упёрся в потолок (защитно к старым строкам:
    у снимков до появления признака его просто нет)."""
    rows = (snap or {}).get("saturated")
    if not isinstance(rows, list):
        return set()
    return {str(k) for k in rows}


def _counts_of(snap: dict | None) -> dict[str, int]:
    counts = (snap or {}).get("counts")
    if not isinstance(counts, dict):
        return {}
    return {str(k): int(v) for k, v in counts.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def trend(inst_id: str) -> dict:
    """Последний снимок против предыдущего.

    Без предыдущего снимка «прошлое» — нули: первый срез это база, по
    которой ещё нельзя сказать, стало ли хуже."""
    rows = history(inst_id)
    now_counts = _counts_of(rows[-1] if rows else None)
    prev_counts = _counts_of(rows[-2] if len(rows) >= 2 else None)

    kinds: dict[str, dict] = {}
    for kind in sorted(set(now_counts) | set(prev_counts)):
        n = now_counts.get(kind, 0)
        m = prev_counts.get(kind, 0)
        kinds[kind] = {"now": n, "prev": m, "delta": n - m}
    return {
        "kinds": kinds,
        "total_now": sum(now_counts.values()),
        "total_prev": sum(prev_counts.values()),
        "worsened": [k for k, v in kinds.items() if v["delta"] > 0],
        "improved": [k for k, v in kinds.items() if v["delta"] < 0],
        "hasBaseline": len(rows) >= 2,
    }


# ------------------------------------------------------------- черновики CR

def _label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def _cr_row(title: str, summary: str, priority: int) -> dict:
    now = _now()
    return {
        "id": "cr" + secrets.token_hex(4),
        "agent": AGENT,
        "title": title[:MAX_TITLE],
        "summary": summary[:MAX_SUMMARY],
        "priority": priority,
        "effort": CR_EFFORT,
        "status": "draft",
        "source": None,
        "country": None,
        "created_at": now,
        "updated_at": now,
    }


def _pairs(inst_ids_with_payloads) -> list[tuple[str, dict]]:
    """Принять и словарь {inst_id: payload}, и последовательность пар."""
    if isinstance(inst_ids_with_payloads, dict):
        items = inst_ids_with_payloads.items()
    else:
        items = inst_ids_with_payloads or ()
    out: list[tuple[str, dict]] = []
    for item in items:
        try:
            inst_id, payload = item
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            out.append((str(inst_id), payload))
    return out


def _cr_baseline(rows: list[dict]) -> dict | None:
    """База ПРЕДЛОЖЕНИЙ — это не база показа.

    Экран сравнивает два последних снимка, и для экрана это правильно. Но
    если бы от них же отсчитывался агент, любое нажатие «Снять снимок»
    сдвигало бы базу, и накопленное с прошлого прогона ухудшение
    исчезало бы НАВСЕГДА (чистый снимок → порча данных → кнопка → агент
    молчит). Поэтому агент отсчитывает от СВОЕГО прошлого снимка, а пока
    такого нет — от самого старого снимка истории: ухудшение
    отчитывается ровно один раз, промежуточные ручные снимки его не
    гасят и не размножают."""
    for row in reversed(rows):
        if row.get("by") == BY_AGENT:
            return row
    return rows[0] if rows else None


def build_crs(inst_ids_with_payloads) -> list[dict]:
    """Снять снимки и вернуть ЧЕРНОВИКИ предложений по ухудшениям.

    Предложение появляется, когда вид дефекта возник впервые либо его
    счётчик вырос относительно базы предложений (_cr_baseline). Экземпляр
    без единого прошлого снимка предложений не даёт: первый срез — база,
    а не повод.

    Функция НИЧЕГО не пишет в очередь CR — регистрация строк на стороне
    вызывающего."""
    crs: list[dict] = []
    for inst_id, payload in _pairs(inst_ids_with_payloads):
        base = _cr_baseline(history(inst_id))
        snap = take_snapshot(inst_id, payload, by=BY_AGENT)
        if base is None:
            continue
        prev_counts = _counts_of(base)
        now_counts = _counts_of(snap)
        # Насыщенный счётчик — нижняя оценка с обеих сторон: разность по
        # нему ничего не значит, предложение по такому виду не строим.
        blind = _saturated_of(base) | _saturated_of(snap)
        details = snap.get("details") or {}
        for kind in sorted(set(now_counts) | set(prev_counts)):
            if kind in blind:
                continue
            now = now_counts.get(kind, 0)
            prev = prev_counts.get(kind, 0)
            if now - prev <= 0:
                continue
            row = {"now": now, "prev": prev, "delta": now - prev}
            crs.append(_build_one(inst_id, kind, row,
                                  details.get(kind) or []))
    return crs


def _build_one(inst_id: str, kind: str, row: dict,
               examples: list[str]) -> dict:
    label = _label(kind)
    prev, now, delta = row["prev"], row["now"], row["delta"]
    verb = "появились" if prev == 0 else "выросли"
    title = (f"Здоровье данных: {label} {verb} {prev} → {now} "
             f"(экземпляр {inst_id})")
    lines = [f"Снимок здоровья данных экземпляра {inst_id}: вид дефекта "
             f"«{label}» — было {prev}, стало {now} (прирост {delta})."]
    if examples:
        lines.append("Примеры из отчёта: " + "; ".join(examples))
    lines.append("Предлагается проверить источник выгрузки и решить судьбу "
                 "каждой записи на стороне владельца данных: контур ничего "
                 "не исправляет автоматически.")
    return _cr_row(title, " ".join(lines), min(MAX_PRIORITY, max(1, delta)))
