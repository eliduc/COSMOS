"""Контур «Буфер стыковки» — перепись ТЕСНЫХ стыковок внутри смены
(волна 2, 2026-07-20).

Наблюдение: сколько в готовом ростере пересадок, чей ПЛАНОВЫЙ наземный
интервал лежит впритык к минимально допустимому. Такая стыковка легальна
по правилам и потому невидима: ни одна проверка её не покажет — она
именно что проходит. Но запас у неё нулевой, и первая же задержка
прилёта делает её невыполнимой.

Почему перепись именно ПЛАНОВАЯ. Соблазн был считать по факту дня
операции: «сколько стыковок реально сорвалось». Так честнее, но так
контур молчал бы до первого импорта фактов, а на большинстве установок
журнала фактов нет вовсе. Плановая теснота считается по одному ростеру и
даёт число сразу — а когда факты появятся, к ней добавится доля реально
сорванных, не ломая уже накопленную историю.

Что контур НЕ предлагает и почему:
  * менять время в пути — такого параметра НЕТ: продолжительность рейса
    выводится вычитанием времён вылета и прилёта, и «откалибровать» её
    значит переписать сам payload, сменив отпечаток задачи и обесценив
    все продвинутые каталоги;
  * менять время руления — поле `taxiTimeInMinutes` в модели объявлено,
    но его не читает НИЧТО (проверено по всему дереву). Предложение было
    бы пустышкой, выглядящей как исправление, — это хуже, чем молчать;
  * менять минимальное время стыковки — вот это настоящее правило, и
    поменять его можно. Но оно входит в отпечаток задачи, поэтому новое
    значение обесценивает продвинутые каталоги и делает КАЖДЫЙ ростер
    «посчитанным по другим правилам». Такую цену контур назначать не
    вправе; он сообщает, что план уплотнился, а решение — за человеком.

Отсюда единственное, о чём контур заводит предложение: план стал теснее,
чем был. Ровно как «Здоровье данных» — сигналом считается УХУДШЕНИЕ
между снимками, а не абсолютное число: у разных авиакомпаний своя норма
плотности, и объявлять её со стороны было бы самоуверенно.

Контур ДЕТЕРМИНИРОВАННЫЙ: ни одного вызова языковой модели. Стыковки
несут табельные номера и ФИО (внутренний контур), поэтому группировка —
по точным ключам, а тексты собираются из шаблонов и чисел.

Контур НИЧЕГО не чинит и НЕ пишет в рабочие настройки: его выход —
история снимков в webapp/config/connection_buffer.json плюс список
черновиков CR, который регистрирует вызывающий.

Дисциплина хранилища — как в data_health.py: модульный лок, атомарная
запись tmp->replace с ретраем, и ТРИ различных исхода чтения («файла
нет» и «файл битый» законно деградируют в пусто, а «не смогли
прочитать» запись отменяет, иначе одна ошибка ввода-вывода стёрла бы
снимки всех экземпляров).
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import rules_config

from . import active_roster, pay_credit, registry

SNAPSHOT_PATH = registry.REPO_ROOT / "webapp" / "config" / \
    "connection_buffer.json"

AGENT = "connbuf"

# «Тесной» считается стыковка, чей плановый интервал лежит в
# [min_sit_min, min_sit_min + TIGHT_MARGIN_MIN). Нижняя граница — само
# правило: короче быть не может, иначе ростер нелегален. Верхняя — запас,
# которого хватает на типовую задержку; 15 минут выбраны потому, что это
# же значение служит порогом пунктуальности в дне операции (плитки OTP),
# то есть «вовремя» по меркам самого приложения.
TIGHT_MARGIN_MIN = 15

# Шаг корзины планового интервала для группировки. Точные минуты
# группировать нельзя: 31 и 32 минуты — одна и та же ситуация, а ключей
# было бы два, и ни одна группа не набрала бы порога.
BUCKET_MIN = 15

MAX_HISTORY = 200
MAX_EXAMPLES = 5
MAX_STATIONS = 10
MAX_TITLE = 90
MAX_SUMMARY = 600
CR_EFFORT = 3
MAX_PRIORITY = 10
READ_RETRIES = 5
READ_RETRY_PAUSE = 0.05

BY_MANUAL = "manual"
BY_AGENT = "agent"

# Ростер, посчитанный по ДРУГИМ правилам, мерить сегодняшним минимальным
# интервалом нельзя: получится теснота, которой в момент решения не было.
# Статусы, при которых перепись отказывается считать (active_roster).
REFUSED_STATUSES = ("stale", "absent")

_lock = threading.Lock()


class SnapshotStoreUnreadable(RuntimeError):
    """Хранилище есть, но прочитать его не удалось.

    То же различение, что в data_health: пустая история — законное
    состояние, нечитаемый файл — неизвестное, и писать поверх
    неизвестного нельзя."""


_CORRUPT = object()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------- хранилище

def _read_raw():
    """Сырой текст хранилища: None — файла нет, _CORRUPT — не текст."""
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
                    f"снимки буфера стыковки недоступны: {SNAPSHOT_PATH}"
                ) from exc
            time.sleep(READ_RETRY_PAUSE)
    return None                                 # недостижимо, для линтера


def _load_strict() -> dict:
    """История всех экземпляров; нечитаемое хранилище — ИСКЛЮЧЕНИЕ.

    Путь ЗАПИСИ обязан идти только через неё."""
    raw = _read_raw()
    if raw is None or raw is _CORRUPT:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load() -> dict:
    """То же для ПОКАЗА: нечитаемое хранилище деградирует в пусто."""
    try:
        return _load_strict()
    except SnapshotStoreUnreadable:
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
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    _replace_with_retry(tmp, SNAPSHOT_PATH)


# ------------------------------------------------------------ счёт стыковок

def _minutes(a: str, b: str) -> float:
    # parse_utc, а не fromisoformat (аудит R10): смешение aware/naive
    # меток из исторических наборов роняло вычитание TypeError-ом.
    from _canonical_time import parse_utc
    return (parse_utc(b) - parse_utc(a)).total_seconds() / 60.0


def _duties(legs: list[dict], rest_min: int) -> list[list[dict]]:
    """Смены пейринга.

    Границу смены определяет ОДНА функция на всё приложение —
    pay_credit.split_duties: тот же порог отдыха, что у расчёта оплаты и
    у солвера. Здесь только переклейка имён: доска зовёт поля dep_utc/
    arr_utc, payload — departureUTCDateTime/arrivalUTCDateTime. Своей
    копии правила «где кончается смена» тут нет и быть не должно: две
    копии разъедутся, и контур начнёт считать ночной отдых стыковкой."""
    adapted = [dict(leg,
                    departureUTCDateTime=leg["dep_utc"],
                    arrivalUTCDateTime=leg["arr_utc"])
               for leg in legs]
    return pay_credit.split_duties(adapted, rest_min)


def _bucket(sit_min: float) -> int:
    """Нижняя граница корзины планового интервала (кратна BUCKET_MIN)."""
    return int(sit_min // BUCKET_MIN) * BUCKET_MIN


def connections(pairings: list[dict], rest_min: int) -> list[dict]:
    """Все пересадки внутри смен: по одной на каждую пару соседних плеч.

    Стык МЕЖДУ сменами — это отдых, а не стыковка, и в перепись он не
    попадает: иначе ночёвка выглядела бы стыковкой с гигантским запасом
    и разбавляла бы статистику до бессмыслицы."""
    out: list[dict] = []
    for p in pairings:
        legs = p.get("legs") or []
        if len(legs) < 2:
            continue
        for duty in _duties(legs, rest_min):
            for prev, nxt in zip(duty, duty[1:]):
                sit = _minutes(prev["arr_utc"], nxt["dep_utc"])
                out.append({
                    "pid": p.get("pid"),
                    "airport": prev.get("arr"),
                    "in_fn": prev.get("fn"),
                    "out_fn": nxt.get("fn"),
                    "at": prev.get("arr_utc"),
                    "sit_min": int(round(sit)),
                })
    return out


def _percentile(values: list[int], q: float):
    """q-й перцентиль по ближайшему рангу.

    Своя реализация вместо statistics.quantiles намеренно: та требует
    минимум двух точек и интерполирует, а здесь нужен ПРЕДСТАВИТЕЛЬ
    выборки — на одной стыковке ответ обязан быть, и это она сама."""
    if not values:
        return None
    ordered = sorted(values)
    idx = int(q * (len(ordered) - 1) + 0.5)
    return ordered[idx]


def census(inst: dict) -> dict:
    """Перепись стыковок одного экземпляра. Хранилище НЕ трогает.

    Возвращает {"status": ..., "counted": bool, ...}. counted=False со
    статусом «нет ростера» или «правила разошлись» — законный ответ, а не
    ошибка: мерить нечего или мерить нечем."""
    from . import roster_board

    # Правила берём ПО ЭКЗЕМПЛЯРУ, а не общие для процесса. Разница не
    # теоретическая: у сценария «что если» свой файл правил, который
    # перекрывает общий, и rules_config.effective() про него не знает —
    # он читает окружение самого веб-процесса. Хуже того, ворота ниже
    # (active_roster) сверяют ростер именно с ПОЭКЗЕМПЛЯРНЫМИ правилами:
    # экземпляр с иным минимумом проходил бы как «актуальный», а мерили
    # бы его чужим минимумом. Сценарий с минимумом 60 при общем 30 давал
    # уверенный ноль тесных стыковок — навсегда.
    rules = active_roster.effective_rules_for(inst)
    min_sit = int(rules.get("min_sit_min") or 0)
    rest_min = int(rules.get("min_rest_min") or 0)

    state = active_roster.resolve(inst)
    status = str(state.get("status") or "absent")
    base = {"status": status, "min_sit_min": min_sit,
            "margin_min": TIGHT_MARGIN_MIN}
    if status in REFUSED_STATUSES:
        # «Устаревший» — это ростер, решённый под другим набором правил.
        # Померить его сегодняшним минимумом технически можно, и цифра
        # получится: вот только теснота в ней будет наведённой сменой
        # правил, а не решением солвера. Молчим, пока не пересчитают.
        return dict(base, counted=False, connections=0, tight=0,
                    buckets={}, stations=[], examples=[],
                    slack={"median": None, "p10": None})

    pairings, _crew, _days = roster_board._resolve(inst)
    if not pairings:
        return dict(base, counted=False, connections=0, tight=0,
                    buckets={}, stations=[], examples=[],
                    slack={"median": None, "p10": None})

    rows = connections(pairings, rest_min)
    tight_rows = [c for c in rows
                  if min_sit <= c["sit_min"] < min_sit + TIGHT_MARGIN_MIN]

    buckets: dict[str, int] = {}
    stations: dict[tuple, int] = {}
    for c in tight_rows:
        key = str(_bucket(c["sit_min"]))
        buckets[key] = buckets.get(key, 0) + 1
        skey = (c.get("airport") or "—", _bucket(c["sit_min"]))
        stations[skey] = stations.get(skey, 0) + 1

    slack = [c["sit_min"] - min_sit for c in rows]
    top = sorted(stations.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    return dict(
        base,
        counted=True,
        connections=len(rows),
        tight=len(tight_rows),
        buckets=buckets,
        stations=[{"airport": a, "bucket": b, "n": n}
                  for (a, b), n in top[:MAX_STATIONS]],
        examples=[{"pid": c["pid"], "airport": c["airport"],
                   "in_fn": c["in_fn"], "out_fn": c["out_fn"],
                   "at": c["at"], "sit_min": c["sit_min"]}
                  for c in sorted(tight_rows,
                                  key=lambda c: (c["sit_min"],
                                                 str(c["at"]),
                                                 str(c["in_fn"])))
                  [:MAX_EXAMPLES]],
        slack={"median": _percentile(slack, 0.5),
               "p10": _percentile(slack, 0.1)},
    )


# --------------------------------------------------------------- снимки

def take_snapshot(inst: dict, by: str = BY_MANUAL) -> dict:
    """Снять и сохранить срез по экземпляру. Возвращает сам снимок.

    Экземпляр без ростера снимка НЕ получает: ряд нулей в истории
    неотличим от «стало идеально», и тренд соврал бы улучшением."""
    inst_id = str(inst.get("id") or "")
    if not inst_id:
        raise ValueError("экземпляр без идентификатора")
    snap = census(inst)
    if not snap.get("counted"):
        return snap
    row = {"at": _now(), "by": str(by or BY_MANUAL),
           "connections": snap["connections"], "tight": snap["tight"],
           "min_sit_min": snap["min_sit_min"],
           "margin_min": snap["margin_min"],
           "buckets": snap["buckets"], "slack": snap["slack"]}
    with _lock:
        data = _load_strict()
        rows = data.get(inst_id)
        if not isinstance(rows, list):
            rows = []
        rows.append(row)
        data[inst_id] = rows[-MAX_HISTORY:]
        _save_locked(data)
    return dict(snap, at=row["at"], by=row["by"])


def history(inst_id: str) -> list[dict]:
    rows = _load().get(str(inst_id))
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("at")]


def latest(inst_id: str) -> dict | None:
    rows = history(inst_id)
    return max(rows, key=lambda r: str(r.get("at"))) if rows else None


def _window(row: dict) -> tuple[int, int]:
    """Окно тесноты снимка — (минимум, запас)."""
    return (int(row.get("min_sit_min") or 0),
            int(row.get("margin_min") or TIGHT_MARGIN_MIN))


def _cr_baseline(rows: list[dict], window: tuple[int, int] | None = None
                 ) -> dict | None:
    """База сравнения ДЛЯ ПРЕДЛОЖЕНИЙ — последний АГЕНТСКИЙ снимок,
    снятый В ТОМ ЖЕ ОКНЕ тесноты.

    Два независимых требования, и оба обязательны.

    Первое — только агентские снимки. Иначе нажатие кнопки «Снять
    снимок» съедает ухудшение: второй снимок сравнивается с первым, оба
    одинаковы, и разница исчезает навсегда (урок волны 1).

    Второе — то же окно. `tight` осмысленно только вместе с окном
    [минимум, минимум + запас), по которому он посчитан. Подними человек
    минимальное время стыковки с 30 до 40 — счётчик подскочит на сотни,
    хотя в ростере не изменилось НИЧЕГО. Контур сообщил бы «стало хуже»
    про решение, которого солвер не принимал, и обвинил бы ростер в
    правке правила. Снимки с другим окном базой быть не могут: сравнение
    начинается заново с первого снимка в новом окне."""
    rows = [r for r in rows if window is None or _window(r) == window]
    agent_rows = [r for r in rows if r.get("by") == BY_AGENT]
    if agent_rows:
        return max(agent_rows, key=lambda r: str(r.get("at")))
    return rows[0] if rows else None


def trend(inst_id: str) -> dict:
    """Показ: два последних снимка и разница между ними."""
    rows = history(inst_id)
    if not rows:
        return {"hasBaseline": False, "prev": None, "now": None,
                "deltaTight": None}
    ordered = sorted(rows, key=lambda r: str(r.get("at")))
    now = ordered[-1]
    if len(ordered) < 2:
        return {"hasBaseline": False, "prev": None, "now": now,
                "deltaTight": None}
    prev = ordered[-2]
    return {"hasBaseline": True, "prev": prev, "now": now,
            "deltaTight": int(now.get("tight") or 0)
            - int(prev.get("tight") or 0)}


# ---------------------------------------------------------- предложения

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


def _rate(snap: dict) -> float | None:
    total = int(snap.get("connections") or 0)
    if total <= 0:
        return None
    return 100.0 * int(snap.get("tight") or 0) / total


def _build_one(inst_id: str, base: dict, snap: dict) -> dict:
    prev, now = int(base.get("tight") or 0), int(snap.get("tight") or 0)
    delta = now - prev
    total = int(snap.get("connections") or 0)
    rate_now, rate_prev = _rate(snap), _rate(base)
    verb = "появились" if prev == 0 else "выросли"
    title = (f"Буфер стыковки: тесные стыковки {verb} {prev} → {now} "
             f"(экземпляр {inst_id})")
    min_sit = int(snap.get("min_sit_min") or 0)
    margin = int(snap.get("margin_min") or TIGHT_MARGIN_MIN)
    lines = [
        f"В ростере экземпляра {inst_id} стыковок внутри смен: {total}; "
        f"из них тесных (плановый интервал от {min_sit} до "
        f"{min_sit + margin} минут) — было {prev}, стало {now} "
        f"(прирост {delta}).",
    ]
    if rate_now is not None and rate_prev is not None:
        lines.append(f"Доля тесных: {rate_prev:.1f}% → {rate_now:.1f}%.")
    stations = snap.get("stations") or []
    if stations:
        lines.append("Чаще всего: " + "; ".join(
            f"{s['airport']} (от {s['bucket']} мин) — {s['n']}"
            for s in stations[:3]) + ".")
    # Конкретные пересадки — в самом предложении, а не только на экране
    # контура: карточку CR читают и решают по ней, не переходя на другую
    # вкладку, а текст ниже прямо просит «разобрать перечисленные
    # пересадки». Без перечня эта просьба была невыполнима.
    examples = snap.get("examples") or []
    if examples:
        lines.append("Самые тесные: " + "; ".join(
            f"{e['airport']}: {e['in_fn']} → {e['out_fn']} "
            f"{str(e['at'])[:16].replace('T', ' ')} ({e['sit_min']} мин)"
            for e in examples[:3]) + ".")
    # Явная оговорка про цену правила: минимальное время стыковки входит
    # в отпечаток задачи, и его смена обесценивает продвинутые каталоги и
    # делает каждый ростер «посчитанным по другим правилам». Контур не
    # вправе назначать такую цену молча — он называет её вслух.
    lines.append(
        "Тесная стыковка легальна, поэтому ни одна проверка её не покажет: "
        "запас у неё нулевой, и первая же задержка прилёта делает её "
        "невыполнимой. Предлагается разобрать перечисленные пересадки. "
        f"Поднимать само правило (минимум {min_sit} мин) — решение "
        "человека: оно входит в отпечаток задачи, поэтому новое значение "
        "обесценит продвинутые каталоги и потребует пересчёта всех "
        "ростеров.")
    return _cr_row(title, " ".join(lines),
                   min(MAX_PRIORITY, max(1, delta)))


def build_crs(instances) -> list[dict]:
    """Снять агентские снимки и вернуть ЧЕРНОВИКИ по УХУДШЕНИЯМ.

    Предложение появляется, когда тесных стыковок стало больше, чем на
    базе предложений. Абсолютного порога здесь нет намеренно: у разных
    авиакомпаний своя норма плотности расписания, и объявлять её со
    стороны было бы самоуверенно — сигналом считается только ухудшение
    относительно собственной истории экземпляра.

    Экземпляр без прошлого снимка предложений не даёт: первый срез —
    база сравнения, а не повод. Экземпляр без ростера (или с ростером,
    посчитанным по другим правилам) пропускается вовсе.

    Функция НИЧЕГО не пишет в очередь CR — регистрация на стороне
    вызывающего."""
    crs: list[dict] = []
    for inst in instances:
        inst_id = str(inst.get("id") or "")
        if not inst_id:
            continue
        try:
            # База берётся ДО нового снимка (иначе он сам стал бы базой),
            # но окно у неё должно совпасть с окном нового — а его мы
            # узнаём только после переписи. Поэтому сначала снимаем,
            # потом отбираем базу по окну получившегося снимка.
            previous = history(inst_id)
            snap = take_snapshot(inst, by=BY_AGENT)
            if not snap.get("counted"):
                continue
            base = _cr_baseline(previous, _window(snap))
            if base is None:
                continue
            if int(snap.get("tight") or 0) - int(base.get("tight") or 0) <= 0:
                continue
            crs.append(_build_one(inst_id, base, snap))
        except SnapshotStoreUnreadable:
            raise
        except Exception:                                # noqa: BLE001
            # Один нечитаемый payload не должен обрывать обход: остальные
            # экземпляры уже сняты, и падение здесь оставило бы прогон
            # агента наполовину сделанным без единого предложения.
            continue
    return crs
