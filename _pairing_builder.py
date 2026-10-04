"""Построитель цепочек экипажа (этап 1) с уровнями свободы.

Альтернатива генератору колонок `_gerad_colgen.py` для наборов, где
структура работы приходит от авиакомпании: строит смены из участков,
цепочки из смен и выбирает набор цепочек решателем CP-SAT, минимизируя
стоимость при жёстком условии полного покрытия спроса.

Три уровня свободы (какая часть структуры заказчика неизменна):
  1 — цепочки заказчика сохраняются целиком, достраивается недостающее;
  2 — сохраняются смены заказчика, сцепка в цепочки строится заново;
  3 — смены и цепочки строятся заново из участков.
Уровни 1 и 2 требуют базовый каталог заказчика (`--base-catalog`),
уровень 3 работает на любом каноническом наборе.

Критерий: стоимость в человеко-масштабе — ночёвка вне базы и перегон
пассажиром умножаются на численность экипажа цепочки. Полное покрытие
спроса — жёсткое условие, избыточное покрытие штрафуется.

Выход — каталог в формате этапа 1 (`gerad_colgen_selected_<label>.json`),
который этап 2 принимает без изменений, плюс отчёт о построении.

Детерминированность: ни одного обращения к языковым моделям и к сети.
Построение смен, кандидатов и колонок воспроизводимо полностью. А вот
выбор набора цепочек воспроизводим только при `--workers 1`: CP-SAT с
несколькими рабочими потоками принимает решения по настенным часам, и
при одном и том же зерне два прогона дают разные решения. Более того,
больший бюджет не гарантирует лучший результат — на данных заказчика
час поиска дал план дороже, чем пятнадцать минут (357 180
против 338 070 на том же пуле). Поэтому отчёт помечает каждый прогон
полем `solver_deterministic`, а сравнивать прогоны между собой можно
только при одинаковых параметрах и с оглядкой на это поле.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _cli_console import force_utf8_console  # noqa: E402


def _now_s() -> float:
    import time as _time
    return _time.monotonic()


_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}


def _weekday_list(text: str) -> tuple:
    """«friday,saturday» либо «4,5» -> (4, 5); чужое имя дня — отказ."""
    out = []
    for tok in str(text or "").split(","):
        t = tok.strip().lower()
        if not t:
            continue
        if t.isdigit():
            d = int(t)
        elif t in _WEEKDAYS:
            d = _WEEKDAYS[t]
        else:
            raise argparse.ArgumentTypeError(
                "день недели не распознан: %r (ожидаются monday..sunday "
                "или 0..6)" % tok)
        if not 0 <= d <= 6:
            raise argparse.ArgumentTypeError("день недели вне 0..6: %r" % tok)
        if d not in out:
            out.append(d)
    return tuple(out)


def _deterministic(args) -> bool:
    """Детерминированный режим (Д1 плана прямого хода): задан предел
    детерминированного времени CP-SAT. В нём ни одна фаза не
    останавливается по стенным часам."""
    return bool(getattr(args, "deterministic_time", None))


def _deadline_of(args, seconds: float):
    """Дедлайн фазы по стенным часам либо None в детерминированном
    режиме: два запуска на одном входе обязаны пройти одинаковый путь,
    а стенные часы на двух машинах (и на одной дважды) разные."""
    if _deterministic(args):
        return None
    import time as _time
    return _time.monotonic() + seconds


def deterministic_profile(args) -> dict | None:
    """Профиль Д1 поимённо для отчёта и сертификата (штамп — SHA-256
    состава); None вне детерминированного режима."""
    if not _deterministic(args):
        return None
    import os as _os
    try:
        from vmp.signatures import deterministic_profile as _dp
        prof = _dp("pairing", seed=int(args.seed),
                   deterministic_time=float(args.deterministic_time))
    except Exception:                                     # noqa: BLE001
        prof = {"task": "pairing", "engine": "cp-sat",
                "seed": int(args.seed), "workers": 1,
                "termination": {"kind": "deterministic_time",
                                "value": float(args.deterministic_time)}}
    # Зерно хэша процесса — свидетельство среды: ставит ЗАПУСКАЮЩИЙ
    # (jobs / кнопка), построитель лишь записывает факт.
    prof["python_hash_seed_env"] = _os.environ.get("PYTHONHASHSEED")
    return prof


def _rss_mb() -> int:
    """Память процесса (working set), МБ — без внешних зависимостей."""
    import ctypes
    import ctypes.wintypes as wt

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    h = ctypes.windll.kernel32.GetCurrentProcess()
    # На новых Windows функция живёт в kernel32 под именем K32...;
    # psapi остаётся запасным для старых сборок.
    for fn in (getattr(ctypes.windll.kernel32, "K32GetProcessMemoryInfo",
                       None),
               getattr(ctypes.windll.psapi, "GetProcessMemoryInfo", None)):
        if fn is not None and fn(h, ctypes.byref(pmc), pmc.cb):
            return int(pmc.WorkingSetSize // (1024 * 1024))
    return -1


def _start_watchdog(out_path: str,
                    interval_s: float = 30.0) -> "threading.Event":
    """Сторож-диагност: каждые interval_s секунд пишет в файл рядом с
    каталогом время, память процесса и СТЕКИ ВСЕХ ПОТОКОВ. Возвращает
    событие остановки: build() гасит сторожа по окончании сборки, иначе
    поток-демон писал бы файл до конца процесса (встроенные вызовы и
    тесты, зовущие сборку в своём процессе, оставляли писателя на весь
    прогон — находка CDPM 08.09.2026).

    Причина появления: 2026-08-26 три прогона подряд застревали в
    разных местах каскада выбора (после клапанной стадии; в минимизации
    ядра; в сборке клапанной модели с ростом памяти 3,5 ГБ/мин) — при
    молчащем журнале место застоя приходилось УГАДЫВАТЬ по косвенным
    признакам. Сторож делает застой самодиагностирующимся: стек в файле
    называет строку кода, где процесс стоит, а динамика памяти между
    записями отличает «варит кашу» от «крутится на месте». Накладные
    расходы — миллисекунды раз в полминуты.
    """
    import threading
    import time as _time
    import traceback

    wd = Path(str(out_path)).with_suffix("").as_posix() + "-watchdog.log"

    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval_s):
            try:
                with open(wd, "a", encoding="utf-8") as fh:
                    fh.write("=== %s RSS=%d MB потоков=%d\n"
                             % (_time.strftime("%H:%M:%S"), _rss_mb(),
                                threading.active_count()))
                    frames = sys._current_frames()
                    names = {th.ident: th.name
                             for th in threading.enumerate()}
                    for tid, frame in frames.items():
                        nm = names.get(tid, "?")
                        if nm == "ba-watchdog":
                            continue
                        fh.write("-- поток %s (%s)\n" % (tid, nm))
                        fh.write("".join(traceback.format_stack(frame)))
                    fh.flush()
            except Exception:                        # noqa: BLE001
                pass                                 # сторож не роняет счёт

    threading.Thread(target=loop, daemon=True, name="ba-watchdog").start()
    return stop



# участок без записей спроса — одно безымянное место
_ONE_SEAT = Counter({"": 1})

force_utf8_console()

HERE = Path(__file__).resolve().parent
import _data_paths
import _conditions
import _pricing_certificate as _pc
from vmp import chain_price as _cp
FINAL_REPORT = _data_paths.results_dir(create=False)

# Пределы смены и цепочки в коде НЕ живут: они приходят из действующего
# набора правил (см. TIER1_TO_ARG ниже). Прежде здесь стояли 760 и 8640 —
# значения конкретной авиакомпании, попавшие в продукт из её файлов. Код
# продукта не должен зависеть от данных заказчика: они появляются на
# импорте и путешествуют вместе с набором.
DEF_SIT_MAX = 240              # прежний литерал верха окна стыковки; с ревизии
                               # литералов 2026-09-03 умолчанием НЕ является
                               # (граница — пол отдыха ВМП, sit_cap_at); оставлен
                               # как справочное число для тестов-фикстур
DEF_MIN_REST_SIT = 30          # нижняя граница разумной стыковки

# Единый контракт правил яруса 1 (аудит 2026-07-26, P0-03). Построитель
# — второй исполнитель тех же правил, что и решатель этапа 2, и обязан
# читать их ИЗ ОДНОГО ИСТОЧНИКА. Иначе оператор меняет правило в
# интерфейсе, видит подтверждение, а этап 1 продолжает считать по своим
# числам — и об этом никто не узнаёт.
#
# Соответствие ключей яруса 1 параметрам построителя. Флаг командной
# строки остаётся допустимым перекрытием, но фиксируется в отчёте и
# каталоге: молчаливое расхождение — это и есть подмена постановки.
TIER1_TO_ARG = {
    "min_sit_min": "sit_min",
    "max_landings_per_duty": "max_segs",
    "max_duty_span_min": "duty_span",
    "max_pairing_span_min": "pairing_span",
}
DEF_W_NIGHT, DEF_W_DH, DEF_W_EXCESS = 30, 90, 200
# Фикс-цена цепочки (за человека). 0 — выключено (историческое
# поведение байт-в-байт). Ненулевая цена заставляет покрытие сливать
# участки в полные смены: без неё дробление на худые однодневные
# цепочки бесплатно (ночей нет, перегонов нет) и точное покрытие
# растранжиривает календарные дни людей — Полярис-каталог занимал
# 834 цепочко-дня на роль при ёмкости нормы выходных 736 (2026-08-26).
DEF_W_CHAIN = 0
# Цена за СМЕНУ (за человека): смена = рабочий день, это прямой прокси
# человеко-дней. Урок 2026-08-26: цена за цепочку (--w-chain) сливала
# цепочки НОЧЁВКАМИ (3 худых дня = 1 цепочка, дни не экономятся),
# цена за смену ночёвками не обходится — выгодно только уплотнять
# смены участками до предела посадок.
DEF_W_DUTY = 0
# Цена КАЛЕНДАРНОГО дня командировки (за человека) — буквальное
# исполнение критерия min_person_days: человеко-день = день в отъезде,
# ВКЛЮЧАЯ дни отдыха внутри цепочки. Урок 2026-08-26: цена за смену
# делала простойные дни внутри командировки бесплатными — каталог
# 389 цепочек нёс 849 календарных дней на роль при 509 сменах.
DEF_W_SPAN_DAY = 0
DEF_TZ_OFFSET_MIN = 0


def _dt(value: str) -> datetime:
    from _canonical_time import parse_utc
    return parse_utc(value)


_FN_SUFFIX = re.compile(r"^(.*?)-\d{8}-\d+$")


def _true_fn_of(leg_id: str) -> str:
    """Подлинный номер рейса из внутреннего идентификатора участка.

    Идентификатор участка — `<номер>-<ГГГГММДД>-<н>`. Правила, судящие по
    номеру рейса (чартер против регулярного, лоукостер), обязаны видеть
    номер: счёт цифр по идентификатору целиком не срабатывает никогда.
    Идентификатор без суффикса возвращается как есть.
    """
    m = _FN_SUFFIX.match(str(leg_id))
    return m.group(1) if m else str(leg_id)


# RULE: none -- извлечение подлинного номера рейса из идентификатора участка; разбор данных, не ограничение
def _true_fn(leg: dict) -> str:
    return leg.get("fn") or _true_fn_of(leg["id"])


def resolve_time_scale(declared, data_off, explicit=None, fallback=None):
    """Сдвиг шкалы меток к местному времени базы (аудит A9): под одним
    ключом жили два смысла — сдвиг шкалы у пакета/решателя и смещение
    пояса к UTC-меткам у построителя, и пояс листа «Аэропорты» молча
    перекрывал объявленный пакетом сдвиг (H0: метки местные, сдвиг 0,
    лист Europe/Moscow +180 — построитель с контрактом брал +180).

    Порядок: явный ключ --base-tz-offset-min > объявление контракта
    (base_tz_offset_declared) > пояс базы по листу (метки тогда
    считаются UTC) > необъявленное число файла правил (fallback, как
    прежде — с предупреждением) > отказ словами.
    Возвращает (сдвиг, заметка, отказ): заметка — что действует и
    почему; отказ — текст, когда сдвига взять неоткуда."""
    if explicit is not None:
        return int(explicit), ("сдвиг шкалы меток задан явно: %+d мин"
                               % int(explicit)), None
    if declared is not None:
        note = ("сдвиг шкалы меток по контракту: %+d мин" % int(declared))
        if data_off is not None and int(data_off) != int(declared):
            note += ("; пояс базы по листу «Аэропорты» %+d мин не является "
                     "сдвигом шкалы: метки понимаются как объявил пакет, "
                     "пояс листа служит только разнице поясов станций"
                     % int(data_off))
        return int(declared), note, None
    if data_off is not None:
        return int(data_off), ("сдвиг шкалы контрактом не объявлен: метки "
                               "считаются UTC, сдвиг — пояс базы по листу "
                               "«Аэропорты» %+d мин" % int(data_off)), None
    if fallback is not None:
        return int(fallback), ("ВНИМАНИЕ: часовой пояс базы в данных набора "
                               "не найден — окна местного времени понимаются "
                               "по поясу %+d мин из файла правил; "
                               "--base-tz-offset-min остаётся перекрытием"
                               % int(fallback)), None
    return None, "", ("часовой пояс базы не найден ни в данных набора, ни "
                      "в файле правил — окна местного времени понимать не "
                      "по чему; задайте --base-tz-offset-min явно")


def _base_tz_offset(payload: dict, bases: str) -> int | None:
    """Смещение пояса базы — ИЗ ДАННЫХ, а не из зашитого числа.

    В этом поясе понимаются окна местного времени правил источника
    (ночная смена 22:00-06:00, «тёмная» 23:00-07:00, час начала смены
    после ночной). Зашитое число молча объявило бы ночными смены другой
    авиакомпании, а умолчание выдало бы себя за данные заказчика.

    Смещение берётся на дату первого рейса базы, чтобы летний перевод
    часов не давал зимнего значения. Возвращает None, если пояса в данных
    нет: тогда вызывающий обязан сказать об этом вслух.
    """
    names = [x for x in (bases or "").split(",") if x]
    tz_of = {a.get("code"): a.get("timeZone")
             for a in (payload.get("airports") or [])}
    if not names:
        names = sorted({e.get("homeAirport")
                        for e in payload.get("employees") or []
                        if e.get("homeAirport")})
    for code in names:
        name = tz_of.get(code)
        if not name:
            continue
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(str(name))
        except Exception:                       # noqa: BLE001
            continue
        when = None
        for f in payload.get("flights") or ():
            if f.get("departureAirport") == code:
                when = _dt(f["departureUTCDateTime"])
                break
        if when is None:
            continue
        off = when.replace(tzinfo=timezone.utc).astimezone(zone).utcoffset()
        if off is not None:
            return int(off.total_seconds() // 60)
    return None


def seat_codes_for(role: str, active, fleet_of, roles_of_leg) -> list:
    """Коды допуска «тип_роль» для МЕСТА данной роли.

    Код требуется только на типах тех активных участков, где эта роль
    ЕСТЬ В СОСТАВЕ УЧАСТКА. На участке, чей состав роли не содержит,
    человек этого места работает рядовым либо едет сверхштатно — и
    допуска «роль на этом типе» у авиакомпании не существует вовсе.

    Первый боевой ростеринг одной из линий: прежняя формула требовала
    роль на КАЖДОМ типе цепочки, и все места «с допуском в бизнес-класс»
    смешанных цепочек оказались невыполнимы в принципе — кода
    «бизнес-класс на узкофюзеляжном типе» нет ни у одного сотрудника,
    потому что узкофюзеляжный состав бизнес-класса не несёт. При этом
    носители широкого кода держат и узкофюзеляжные коды рядовой роли —
    места посадимы, требование было чрезмерным.

    Роль, которой нет в составе ни одного участка (усиление сверх
    спроса, добавка условий заказчика), сохраняет прежнее требование —
    код на каждом типе: выдумывать ей «домашний» тип нельзя."""
    typed = sorted({fleet_of(x) for x in active if role in roles_of_leg(x)})
    if not typed:
        typed = sorted({fleet_of(x) for x in active})
    return [f"{ac}_{role}" for ac in typed]


# ----------------------------------------------------------------------
# Потоковая запись точной модели покрытия (MPS / LP / справочник).
#
# Поручение владельца 04.09.2026: CPLEX обязан работать для разработки на
# ВСЕХ наборах. Прежний экспорт копил все строки файла списком и склеивал
# их одной строкой — на полном «Полярисе» (сотни тысяч колонок, миллионы
# ненулей) это сотни мегабайт строк Python. Здесь строки уходят в файл
# по мере генерации; единственная структура O(ненулей) — компактный
# индекс колонок (array), который колоночному формату MPS неизбежен.
# Байты файлов — те же, что у прежней записи (стенд
# tests/test_exact_export_stream.py сверяет с эталонной реализацией).
# ----------------------------------------------------------------------

def _column_index(rows, n_all):
    """CSR-индекс колонок: для переменной ci — пары (номер строки, коэф.).
    Массивы array('i'): 8 байт на ненуль вместо кортежа и списка."""
    from array import array
    counts = array("i", [0]) * (n_all + 1)
    for _s, _n, _r, terms in rows:
        for ci, _coef in terms:
            counts[ci + 1] += 1
    for i in range(1, n_all + 1):
        counts[i] += counts[i - 1]
    fill = array("i", counts)
    row_of = array("i", [0]) * counts[n_all]
    coef_of = array("i", [0]) * counts[n_all]
    for ri, (_s, _n, _r, terms) in enumerate(rows):
        for ci, coef in terms:
            k = fill[ci]
            row_of[k] = ri
            coef_of[k] = coef
            fill[ci] = k + 1
    return counts, row_of, coef_of


def write_exact_mps(path, rows, cost, fixed_zero, n_all) -> None:
    """Свободный MPS точной модели, строка за строкой в файл."""
    counts, row_of, coef_of = _column_index(rows, n_all)
    names = [name for _s, name, _r, _t in rows]
    with open(path, "w", encoding="ascii") as f:
        w = f.write
        w("NAME exact_cover\nROWS\n N  COST\n")
        for sense, name, _, _ in rows:
            w(" %s  %s\n" % (sense, name))
        w("COLUMNS\n")
        w("    MARKER                 'MARKER'                 'INTORG'\n")
        for ci in range(n_all):
            w("    x%d COST %d\n" % (ci, cost[ci]))
            for k in range(counts[ci], counts[ci + 1]):
                w("    x%d %s %d\n" % (ci, names[row_of[k]], coef_of[k]))
        w("    MARKER                 'MARKER'                 'INTEND'\n")
        w("RHS\n")
        for sense, name, rhs, _ in rows:
            w("    RHS %s %d\n" % (name, rhs))
        w("BOUNDS\n")
        for ci in range(n_all):
            w(" UP BND x%d %d\n" % (ci, 0 if ci in fixed_zero else 1))
        w("ENDATA\n")


def write_exact_lp(path, rows, cost, fixed_zero, nvar, n_all) -> None:
    """LP-формат точной модели, потоково; байты — как у прежней записи
    (в том числе двойной пробел при пустой сумме)."""
    with open(path, "w", encoding="ascii") as f:
        w = f.write
        w("Minimize\n obj: ")
        sep = ""
        for ci in range(nvar):
            w("%s%+d x%d" % (sep, cost[ci], ci))
            sep = " "
        w("\nSubject To\n")
        for sense, name, rhs, terms in rows:
            op = "=" if sense == "E" else "<="
            w(" %s: " % name)
            sep = ""
            for ci, coef in terms:
                w("%s%+d x%d" % (sep, coef, ci))
                sep = " "
            w(" %s %d\n" % (op, rhs))
        w("Bounds\n")
        for ci in sorted(fixed_zero):
            w(" x%d = 0\n" % ci)
        w("Binary\n ")
        sep = ""
        for ci in range(n_all):
            w("%sx%d" % (sep, ci))
            sep = " "
        w("\nEnd\n")


def write_exact_map(path, cands, cost, aux_map, cons_map, tail: dict) -> None:
    """Справочник переменных JSON, потоково: те же байты, что у
    json.dumps(..., ensure_ascii=False) целого словаря, но без словаря
    всех переменных в памяти. tail — поля после constraints (objective,
    pool, exact, …) в порядке записи."""
    import json as _json
    d = lambda o: _json.dumps(o, ensure_ascii=False)   # noqa: E731
    with open(path, "w", encoding="utf-8") as f:
        w = f.write
        w('{"variables": {')
        sep = ""
        for ci, c in enumerate(cands):
            w('%s"x%d": %s' % (sep, ci, d({
                "fns": sorted(c["legs"]),
                "dh": sorted(c.get("dh") or ()),
                "cost": cost[ci],
                "crew": c.get("crew")})))
            sep = ", "
        w("}, ")
        aux_vars = {"x%d" % yv: {"aux": "покрытие рейса исполнителем",
                                 "fn": lid}
                    for yv, lid in sorted(aux_map.items())}
        w('"aux_variables": %s, ' % d(aux_vars))
        w('"constraints": %s' % d(cons_map))
        for key, value in tail.items():
            w(", %s: %s" % (d(key), d(value)))
        w("}")


class BuilderConfigError(ValueError):
    """Единый типизированный отказ построителя (пакет «умолчания-в-данные»).

    Значение, обязанное прийти из данных (пакет постановки, файл правил,
    файл условий, явный флаг), не пришло. `main()` печатает такой отказ
    словами («ОТКАЗ: ...») и возвращает код 2; внутрипроцессные
    вызыватели получают исключение. Подкласс ValueError — по образцу
    `SourceRulesError` файла правил: молчаливое умолчание здесь опаснее
    отказа.
    """


class Builder:
    """Построение и выбор цепочек. Состояние вынесено в объект, чтобы
    функцию можно было вызывать из приложения, а не только из CLI."""

    def __init__(self, payload: dict, args: argparse.Namespace) -> None:
        self.args = args
        # Тестовые Namespace старше ручки не несут её поля.
        if not hasattr(args, "w_chain"):
            args.w_chain = DEF_W_CHAIN
        if not hasattr(args, "w_duty"):
            args.w_duty = DEF_W_DUTY
        if not hasattr(args, "w_span_day"):
            args.w_span_day = DEF_W_SPAN_DAY
        # Композиция целевой (канон ВМП 2026-09-02: критерий + Σ размер
        # нарушения × коэффициент мягких правил части; исправление
        # 2026-09-03 — построитель минимизировал только критерий):
        # штраф надбавки за ночёвку в окне дней недели и штраф продлённой
        # смены — слагаемые цены цепочки; веса ТОЛЬКО из пакета, умолчание 0.
        if not hasattr(args, "w_weekend_night"):
            args.w_weekend_night = 0
        if not hasattr(args, "weekend_days"):
            args.weekend_days = ()
        if not hasattr(args, "w_long_duty"):
            args.w_long_duty = 0
        if not hasattr(args, "long_duty_threshold"):
            args.long_duty_threshold = None
        # Мера 100 (НАДЗОР 11:2x): жёсткий потолок продления смены (duty_extension_cap ВМП). Смена длиннее
        # базы (--duty-span) и не длиннее потолка допустима — со штрафом продления от базы; без ключа
        # предел смены — база, как прежде.
        if not hasattr(args, "duty_cap"):
            args.duty_cap = None
        if (args.duty_cap is not None and getattr(args, "duty_span", None) is not None
                and args.duty_cap < args.duty_span):
            raise BuilderConfigError(
                f"--duty-cap {args.duty_cap} ниже базового предела смены --duty-span {args.duty_span}: "
                f"потолок продления не может быть строже базы")
        if not hasattr(args, "cost_0096"):
            args.cost_0096 = False           # False = механизм спит
        # Заглушки ПРИСУТСТВИЯ полей (подпись и тесты читают их через
        # getattr): значения — только из данных пакета, None = не поданы.
        if not hasattr(args, "cost_rank_deci_work"):
            args.cost_rank_deci_work = None
        if not hasattr(args, "cost_rank_deci_dh"):
            args.cost_rank_deci_dh = None
        # Гейт внутрипроцессного пути (Builder зовут в обход main тесты
        # и внешние тулы): деци-ставки критерия 0096 считает исполнитель
        # vmp/criteria из блока rates пакета — умолчаний в коде нет.
        if args.cost_0096 and (args.cost_rank_deci_work is None
                               or args.cost_rank_deci_dh is None):
            raise BuilderConfigError(
                "--cost-0096 требует явных --cost-rank-deci-work и "
                "--cost-rank-deci-dh: деци-ставки приходят из блока "
                "rates критерия пакета (исполнитель vmp/criteria), "
                "умолчаний в коде нет")
        if not hasattr(args, "warm_catalog"):
            args.warm_catalog = None
        # Тёплый старт точной фазы (отмашка владельца 2026-08-26):
        # каталог прежнего прогона подсказывает решателю выбор —
        # ключ цепочки = множество РАБОЧИХ плеч. Подсказка частична и
        # не обязывает: солвер вправе улучшать. Нужна ёмкостям баз:
        # без неё точная фаза на CP-SAT 9.15 не находила решения
        # (завис presolve / UNKNOWN без presolve) при доказанной
        # выполнимости.
        self.warm_keys: set[frozenset] = set()
        if args.warm_catalog:
            import json as _json
            _wc = _json.loads(Path(args.warm_catalog).read_text(
                encoding="utf-8"))
            for _ch in _wc.get("selected") or ():
                _fns = _ch.get("active_fns") or _ch.get("fns") or ()
                if _fns:
                    self.warm_keys.add(frozenset(_fns))
        if not hasattr(args, "base_day_cap"):
            args.base_day_cap = ""
        # Ёмкость баз в календарных днях НА РОЛЬ (балансировка
        # стартовых баз, виток «путь к нулю» 2026-08-26): производная
        # данных пакета (штат по (база, роль) × дни горизонта), едет
        # флагом от транслятора. Пусто — ограничение выключено.
        self.base_day_cap = {}
        for part in (args.base_day_cap or "").split(","):
            part = part.strip()
            if part:
                b, n = part.split("=")
                self.base_day_cap[b.strip()] = int(n)
        if not hasattr(args, "criterion_source"):
            args.criterion_source = "умолчания построителя"
        self.payload = payload
        self.tz = timedelta(minutes=args.tz_offset_min)
        self.bases = set(args.bases.split(",")) if args.bases else set()
        if not self.bases:
            self.bases = {e.get("homeAirport") for e in payload["employees"]
                          if e.get("homeAirport")}
        # Контракт Б (единая версия 06.09.2026, P-425): базы поиска и
        # замыкания цепочек (self.bases, ключ --bases) — не приписки
        # людей. Универсум приписок для ЦЕНЫ — homeAirport всех
        # сотрудников постановки; подмножество --bases не доказывает,
        # что станция вне всех приписок. Универсум полон, только когда у
        # каждого сотрудника приписка есть; иначе ночь вне баз не
        # доказана и цена цепочки — известная часть с причиной.
        _people = list(payload.get("employees") or ())
        _homes = [(e.get("homeAirport") if isinstance(e, dict) else None)
                  for e in _people]
        self.price_eligible_bases = {
            h.strip() for h in _homes if isinstance(h, str) and h.strip()}
        self.price_eligible_bases_complete = bool(_people) and all(
            isinstance(h, str) and bool(h.strip()) for h in _homes)
        self.price_home_coverage = {
            "source": "all_payload_employees.homeAirport",
            "employees": len(_people),
            "with_home": sum(1 for h in _homes
                             if isinstance(h, str) and h.strip()),
            "complete": self.price_eligible_bases_complete}
        self.duty_caps: set = set()
        self.legs: dict[str, dict] = {}
        for f in payload["flights"]:
            self.legs[f["flightNumber"]] = {
                "id": f["flightNumber"],
                # Подлинный номер рейса, без суффикса даты и порядкового
                # номера: правила, судящие по НОМЕРУ (чартер, лоукостер),
                # обязаны видеть номер, а не внутренний адрес участка.
                "fn": _true_fn_of(f["flightNumber"]),
                "dep": f["departureAirport"], "arr": f["arrivalAirport"],
                "t0": _dt(f["departureUTCDateTime"]),
                "t1": _dt(f["arrivalUTCDateTime"]),
                "fleet": f.get("aircraftType") or "?",
                # Рейс-транспорт: экипаж может лететь им пассажиром, но
                # обслуживать его наш план не обязан — это рейс другого
                # перевозчика (правило 0094 свода) либо иной перелёт,
                # предоставленный как средство доставки. Без такого
                # признака чужой рейс пришлось бы либо выбросить из
                # набора (и потерять разрешённый транспорт), либо
                # объявить обязательством покрытия (и требовать на него
                # свой экипаж) — обе крайности неверны.
                "transport_only": bool(f.get("transportOnly")),
                "req": sorted(f.get("requiredQualifications") or [])}
        # Смещение пояса КАЖДОЙ станции — им определяется «дальняя»
        # стоянка (см. `CosRules.is_trans_layover`). Берётся на время
        # первого касания станции, чтобы летний перевод часов не давал
        # зимнего значения. Станция без пояса в данных остаётся None и
        # дальней не считается — молчаливое ужесточение недопустимо.
        self.tz_off_of: dict[str, int | None] = {}
        self.tz_off_unknown: set[str] = set()
        # A9: зона станции хранится IANA-именем и смещение считается НА
        # МОМЕНТ (station_off): перевод часов внутри горизонта менял
        # пояс станции, а замороженное «на первое касание» значение —
        # нет. tz_off_of остаётся смещением первого касания (отчёт).
        self.tz_zone_of: dict = {}
        tz_name_of = {a.get("code"): a.get("timeZone")
                      for a in (payload.get("airports") or [])}
        first_seen: dict[str, datetime] = {}
        for leg in self.legs.values():
            first_seen.setdefault(leg["dep"], leg["t0"])
            first_seen.setdefault(leg["arr"], leg["t1"])
        for code, when in first_seen.items():
            name = tz_name_of.get(code)
            off = None
            if name:
                try:
                    from zoneinfo import ZoneInfo
                    zone = ZoneInfo(str(name))
                    d = when.replace(tzinfo=timezone.utc).astimezone(
                        zone).utcoffset()
                    off = None if d is None else int(d.total_seconds() // 60)
                    if off is not None:
                        self.tz_zone_of[code] = zone
                except Exception:                       # noqa: BLE001
                    off = None
            self.tz_off_of[code] = off
            if off is None:
                self.tz_off_unknown.add(code)
        self.demand: dict[str, Counter] = defaultdict(Counter)
        # места спроса в ПОЗИЦИОННОМ порядке: этап 2 сопоставляет места
        # каталога с местами спроса по позиции, а не как мультимножество,
        # поэтому алфавитный порядок ролей рассадил бы людей по чужим
        # местам — сертификат ловит это как «назначено иначе»
        slots: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for a in payload["flightAssignments"]:
            self.demand[a["flight"]][a["requiredSkill"]] += 1
            slots[a["flight"]].append((a.get("indexInFlight") or 0,
                                       a["requiredSkill"]))
        self.slots_of_leg: dict[str, list[str]] = {
            fn: [r for _i, r in sorted(v, key=lambda x: x[0])]
            for fn, v in slots.items()}
        # Помест­ный счёт ролей участка — заранее: `crew_size` вызывается
        # на каждой цепочке-кандидате, строить Counter там дорого.
        self.role_count_of_leg: dict[str, Counter] = {
            fn: Counter(v) for fn, v in self.slots_of_leg.items()}
        self.crew_of_leg = {fn: sum(c.values())
                            for fn, c in self.demand.items()}
        # Обычный состав типа судна — самый частый в спросе. Он служит
        # точкой отсчёта: усиленный и двойной состав добавляют к нему
        # места, а НЕ выводятся из числа мест обратно. Обратный вывод
        # проверен на наборе заказчика и не работает: сошлось
        # 201 случай из 383, мешают незакрытые места.
        by_fleet: dict[str, Counter] = defaultdict(Counter)
        for fn, n in self.crew_of_leg.items():
            leg = self.legs.get(fn)
            if leg:
                by_fleet[leg["fleet"]][n] += 1
        self.std_crew_of_fleet = {
            fleet: c.most_common(1)[0][0] for fleet, c in by_fleet.items()}
        # Брифинг и дебрифинг участка. У системы-источника это ДАННЫЕ, а
        # не выводимое правило: пять кандидатов на ключ (станция, тип
        # судна, место в цепочке и их сочетания) оставляют неоднозначность,
        # а по участку её нет — 2084 участка, 0 расхождений. Без них
        # рабочее время смены посчитать нельзя, а по нему определяются и
        # предел смены, и признак ночной смены.
        self.briefing_of: dict[str, int] = {}
        self.debriefing_of: dict[str, int] = {}
        by_dep_brief: dict[str, Counter] = defaultdict(Counter)
        by_arr_debrief: dict[str, Counter] = defaultdict(Counter)
        for f in payload["flights"]:
            fn = f["flightNumber"]
            if f.get("briefingMin") is not None:
                self.briefing_of[fn] = int(f["briefingMin"])
                by_dep_brief[f["departureAirport"]][int(f["briefingMin"])] += 1
            if f.get("debriefingMin") is not None:
                self.debriefing_of[fn] = int(f["debriefingMin"])
                by_arr_debrief[f["arrivalAirport"]][
                    int(f["debriefingMin"])] += 1
        # Участок, ни разу не бывший крайним в смене заказчика, своего
        # брифинга в данных не имеет. Заполняется большинством по станции
        # — из ТЕХ ЖЕ данных, а не выдуманным правилом; сколько участков
        # заполнено таким образом, попадает в отчёт, чтобы вывод нельзя
        # было прочитать в отрыве от того, на чём он стоит.
        self.brief_by_station = {st: c.most_common(1)[0][0]
                                 for st, c in by_dep_brief.items()}
        self.debrief_by_station = {st: c.most_common(1)[0][0]
                                   for st, c in by_arr_debrief.items()}
        # Станция, с которой заказчик ни разу не начинал смену, своего
        # значения не даёт вовсе — на наборе таких 27 из 73, и на них
        # приходится 10,4 % участков. Ноль здесь недопустим: он ЗАНИЗИЛ бы
        # рабочее время и пропустил смены, которые правило обязано
        # отвергнуть. Берётся преобладающее значение по всем данным, а
        # флаг командной строки остаётся перекрытием для перекрёстной
        # проверки крайними значениями.
        all_b: Counter = Counter()
        all_db: Counter = Counter()
        for c in by_dep_brief.values():
            all_b.update(c)
        for c in by_arr_debrief.values():
            all_db.update(c)
        self.brief_fallback = (all_b.most_common(1)[0][0] if all_b else 0)
        self.debrief_fallback = (all_db.most_common(1)[0][0] if all_db else 0)
        # Явно переданный НОЛЬ обязан применяться: это ровно тот ключ,
        # которым делается перекрёстная проверка чувствительности к
        # запасному значению. Проверка на истинность отбрасывала его, и
        # проверка показала бы «влияния нет» ложно.
        if getattr(args, "briefing_default_min", None) is not None:
            self.brief_fallback = int(args.briefing_default_min)
        if getattr(args, "debriefing_default_min", None) is not None:
            self.debrief_fallback = int(args.debriefing_default_min)
        # Учёт ведётся по РАЗЛИЧНЫМ участкам, а не по обращениям: во время
        # перебора одна и та же смена опрашивается многократно, и счётчик
        # обращений выдал бы тысячи там, где участков сотни.
        self._brief_src: dict[str, str] = {}
        self._debrief_src: dict[str, str] = {}
        # Ставки цели — из ДАННЫХ источника, если они там есть. Иначе
        # остаются наши коэффициенты, и это видно в отчёте: цель, взятая
        # с потолка, делает сравнение с чужим решением бессмысленным,
        # даже когда правила совпадают.
        self.layover_cost_of: dict[str, int] = {}
        for a in payload.get("airports") or []:
            if a.get("layoverCostKnown") and a.get("layoverCost") is not None:
                self.layover_cost_of[a["code"]] = int(a["layoverCost"])
        self.dh_cost_of: dict[str, int] = {
            f["flightNumber"]: int(f["deadheadCost"])
            for f in payload["flights"] if f.get("deadheadCost") is not None}
        self.source_cost = bool(self.layover_cost_of and self.dh_cost_of
                                and getattr(args, "source_cost", True))
        self.rules = getattr(args, "cos_rules", None)
        # Условия расчёта из спецификации заказчика. Когда они заданы, они
        # ЗАМЕЩАЮТ величины, выведенные из данных источника: состав,
        # предел рабочего времени, стыковку, отдых и предел связки. Так
        # расчёт опирается только на переданные документы, а не на
        # наблюдения за чужим решением.
        self.conditions = getattr(args, "conditions", None)
        # --- гейты «значения только из данных» (пакет умолчания-в-
        # данные). Место стражей — именно __init__: main печатает отказ
        # словами и возвращает 2, внутрипроцессные вызыватели получают
        # исключение, а контракт кодов замоканного build (rc=3,
        # test_audit_r7_fixes) не задевается.
        if self.source_cost and getattr(args, "nights_per_layover",
                                        None) is None:
            raise BuilderConfigError(
                "ставки источника в данных есть, а конвенция ночёвок не "
                "подана: --nights-per-layover N из пакета постановки, "
                "0 — по фактическим календарным ночам, либо "
                "--no-source-cost; молчаливый переход на 0 — смена цели")
        if self.rules is None and any(
                getattr(args, k, None) is None
                for k in ("rest_base", "rest_base_long",
                          "rest_off", "rest_off_long")):
            raise BuilderConfigError(
                "таблица отдыха не задана — подайте --source-rules "
                "(layover-таблица источника), --conditions (лестницы "
                "отдыха условий) либо все четыре флага --rest-base/"
                "--rest-base-long/--rest-off/--rest-off-long; голый "
                "запуск чужими числами не считается")
        self.sorted_legs = sorted(self.legs.values(), key=lambda x: x["t0"])
        # Транспортные рейсы участвуют ТОЛЬКО как перегоны: в множество
        # рабочих смен они не входят (`by_dep_leg` — индекс построения
        # смен), в обязательства покрытия тоже, но в маршрутном индексе
        # `by_route` остаются, потому что именно им ищется перегон.
        self.transport_only = {l["id"] for l in self.sorted_legs
                               if l.get("transport_only")}
        # RULE: R-S6-табл-4-строка-18-кол-3 R-S8-абз-6 -- типы ВС вне
        # автоматического планирования: леги перечисленных
        # условиями типов маркируются перегонами — участвуют только как
        # заброски, обязательствами покрытия не являются (крыло
        # планируется вручную). Категория в отчёте отдельная и
        # поимённая: молча растворять их в транспортных нельзя.
        excl_fleets = (self.conditions.auto_plan_excluded_fleets()
                       if self.conditions is not None else frozenset())
        self.auto_plan_excluded = {
            l["id"] for l in self.sorted_legs
            if l["id"] not in self.transport_only
            and str(l.get("fleet") or "").replace("(*)", "").strip()
            in excl_fleets}
        self.transport_only |= self.auto_plan_excluded
        self.by_dep_leg: dict[str, list] = defaultdict(list)
        self.by_route: dict[tuple, list] = defaultdict(list)
        for l in self.sorted_legs:
            if l["id"] not in self.transport_only:
                self.by_dep_leg[l["dep"]].append(l)
            self.by_route[(l["dep"], l["arr"])].append(l)
        self.duties: list[dict] = []
        self.by_dep: dict[str, list] = defaultdict(list)
        self.candidates: dict[tuple, dict] = {}
        self.fixed_chains: list[list[dict]] = []
        # Режим закрепления (locked=true — обязательное условие заказчика):
        # метаданные закреплённых цепочек, позиционно к fixed_chains.
        self.fixed_meta: list[dict] = []
        self._pin_active: set[str] = set()
        self._pin_keys: set[tuple] = set()
        self.report: dict = {"level": args.level, "bases": sorted(self.bases)}
        # Происхождение границы «стыковка / отдых» объявляется СРАЗУ и
        # едет в каталог (карточка серии): прочитавший каталог обязан
        # видеть, из какого пола отдыха взята граница (заявка CDPM
        # 2026-09-03). Без пола и без перекрытия поле остаётся пустым, а
        # первая же стыковка даст отказ sit_cap_at.
        if getattr(args, "sit_max", None) is not None:
            self.report["sit_window"] = {
                "mode": "задан --sit-max: граница «стыковка / отдых» — "
                        "порог правила максимальной стыковки пакета "
                        "(кнопка «Пейринг») или явное перекрытие оператора",
                "cap_min": args.sit_max}
        elif getattr(args, "rest_base", None) is not None \
                or getattr(args, "rest_off", None) is not None:
            self.report["sit_window"] = {
                "mode": "по полу отдыха ВМП: пауза короче пола отдыха "
                        "станции — стыковка, иначе отдых",
                "cap_base_min": args.rest_base,
                "cap_outstation_min": args.rest_off}

    # --- смены --------------------------------------------------------
    # RULE: 0019 0020 0021 0037 0085 -- сборка смен: служебное полётное время, неизменность состава в пределах FDP
    def build_duties(self, base_catalog: dict | None) -> None:
        a = self.args
        duty_set: dict[tuple, list] = {}
        seeded = 0
        if base_catalog is not None:
            units = base_catalog.get("selected") or []
            # Уровень 2 обещает сохранить СМЕНЫ заказчика. Если каталог
            # пришёл без их границ, запасной вариант считает пейринг одной
            # сменой — и уровень 2 молча вырождается в уровень 1 без
            # закреплений. Такую подмену постановки надо называть вслух,
            # а не переживать: отказываем громко.
            if a.level == 2 and not any(rec.get("duty_fns") for rec in units):
                raise ValueError(
                    "уровень 2 требует границ смен в базовом каталоге "
                    "(поле duty_fns), а их нет: каталог собран старым "
                    "импортом. Перезапустите импорт выгрузки — иначе "
                    "«сохранение смен» подменяется сохранением цепочек")
            for rec in units:
                for group in rec.get("duty_fns") or [rec.get("active_fns")
                                                     or rec.get("fns") or []]:
                    g = sorted((self.legs[x] for x in group if x in self.legs),
                               key=lambda x: x["t0"])
                    # Засев минует свободный перебор, но не правило:
                    # транспортные и исключённые из планирования по типам
                    # участки рабочей сменой не бывают НИ НА ОДНОМ пути —
                    # группа с таким участком отклоняется целиком (не
                    # молча: счётчик в отчёте; урезать группу значило бы
                    # подменить смену заказчика другой сменой).
                    if g and any(x["id"] in self.transport_only for x in g):
                        self.report["seeded_groups_dropped_transport"] = \
                            self.report.get(
                                "seeded_groups_dropped_transport", 0) + 1
                        continue
                    if g:
                        key = tuple(x["id"] for x in g)
                        if key not in duty_set:
                            duty_set[key] = g
                            seeded += 1
        if a.level == 3 or base_catalog is None:
            max_segs = (self.rules.max_landings_per_duty if self.rules
                        else a.max_segs)
            sit_min = self.rules.min_sit_min if self.rules else a.sit_min

            def grow(chain: list[dict]) -> bool:
                # Предел рабочего времени проверяется ЗДЕСЬ — в единственной
                # точке, где смена попадает в множество. Раньше проверка
                # стояла только на расширении (`chain + [nxt]`), поэтому
                # односегментная смена входила в множество не проверенной
                # вовсе: дальнемагистральный рейс на 15 часов становился
                # сменой при пределе в 12. Инвариант принадлежит входу, а
                # не каждому пути, которым до входа доходят.
                if not self.duty_limit_ok(chain):
                    return False
                duty_set.setdefault(tuple(x["id"] for x in chain), list(chain))
                if len(chain) >= max_segs:
                    return True
                last, taken = chain[-1], 0
                for nxt in self.by_dep_leg.get(last["arr"], ()):
                    sit = (nxt["t0"] - last["t1"]).total_seconds() / 60
                    # Порог стыковки зависит от СТАНЦИИ: в базе он строже
                    # (условия, группа B — для кабинного экипажа базовое
                    # значение в документе не задано, взято наибольшее из
                    # приведённых), вне базы — 45 минут по П.2.7.
                    need_sit = (self.conditions.sit_min(
                        last["arr"] in self.bases)
                        if self.conditions is not None else sit_min)
                    if sit < need_sit:
                        continue
                    # Пауза не короче пола отдыха станции — это отдых,
                    # смена кончилась (граница из ВМП, не литерал).
                    if sit >= self.sit_cap_at(last["arr"]):
                        break
                    if nxt["fleet"] != last["fleet"]:
                        continue
                    # Предел смены проверяет сам grow на входе — условие
                    # живёт в одном месте. В счёт ветвления идут только
                    # ПРИНЯТЫЕ продолжения: отвергнутое пределом не должно
                    # съедать бюджет ветвления и сужать поиск.
                    if taken >= a.branch_duty:
                        # Кап — только при НАБЛЮДЕНИИ (k+1)-го допустимого
                        # продолжения (аудит R10, P2-01): «набрано k» само
                        # по себе усечения не доказывает — при ровно k
                        # вариантах множество смен полное, и запись капа
                        # незаслуженно подавляла бы нижнюю границу.
                        # Свидетель проверяет ТО ЖЕ условие входа в
                        # множество (duty_limit_ok), но не принимает
                        # продолжение — бюджет ветвления уже исчерпан.
                        if self.duty_limit_ok(chain + [nxt]):
                            # Усечение пространства смен ДО ценообразования
                            # (внешний аудит R8, P0-03): «полный» pricing
                            # по усечённому множеству смен полноты не
                            # доказывает.
                            self.duty_caps.add("branch_duty")
                            break
                        continue
                    if not grow(chain + [nxt]):
                        continue
                    taken += 1
                # Смена зарегистрирована — для вызывающего это ПРИНЯТОЕ
                # продолжение, независимо от того, вышло ли из неё что-то
                # длиннее. False означает одно: смена в множество не вошла.
                return True

            for l in self.sorted_legs:
                # Транспортный рейс (чужой перевозчик) рабочим сектором
                # быть не может: экипаж летит им пассажиром. Без этого
                # отсева он становился односегментной сменой, и в плане
                # выходило, что рейс Egyptair выполняет наш экипаж
                # (аудит передаваемого файла 2026-08-25).
                if l["id"] in self.transport_only:
                    continue
                grow([l])
        # Режим закрепления: участки закреплённых цепочек существуют ТОЛЬКО
        # в закреплённых сменах. Любая другая смена с таким участком дала
        # бы свободному перебору цепочку, накрывающую закреплённый участок
        # вторым экипажем, — а точное покрытие держит «ровно один» лишь
        # для свободных участков.
        if self._pin_active:
            duty_set = {key: ls for key, ls in duty_set.items()
                        if key in self._pin_keys
                        or not any(x["id"] in self._pin_active for x in ls)}
        for i, (_key, ls) in enumerate(duty_set.items()):
            d = {"i": i, "legs": ls, "dep": ls[0]["dep"], "arr": ls[-1]["arr"],
                 "t0": ls[0]["t0"], "t1": ls[-1]["t1"],
                 "span": (ls[-1]["t1"] - ls[0]["t0"]).total_seconds() / 60}
            self.annotate_duty(d)
            self.duties.append(d)
        if self.rules is not None:
            # Смены заказчика могли прийти из каталога, не пройдя проверку
            # предела; такие считаются отдельно, а не отбрасываются молча.
            # Счётчик обязан мерить ФАКТИЧЕСКИЙ предел, а не побочный
            # признак. Раньше он считал смены без яруса состава; при
            # работе по условиям ярус проставляется всегда (усиление не
            # применяется), поэтому счётчик структурно давал ноль — и 47
            # превышений предела прошли как «нарушений нет». Признак,
            # который не может отличиться, — не проверка.
            over = [d for d in self.duties
                    if not self.duty_limit_ok(d["legs"])]
            self.report["duties_over_work_time_limit"] = len(over)
            self.report["duties_by_tier"] = dict(Counter(
                str(d.get("tier")) for d in self.duties))
            self.report["duties_night"] = sum(
                1 for d in self.duties if d.get("night"))
            self.report["duties_dark"] = sum(
                1 for d in self.duties if d.get("dark"))
            self.report["duties_trans"] = sum(
                1 for d in self.duties if d.get("trans"))
            self.report["work_time_provenance"] = self.work_time_provenance()
        for d in self.duties:
            self.by_dep[d["dep"]].append(d)
        for v in self.by_dep.values():
            v.sort(key=lambda d: d["t0"])
        self.report["duties"] = len(self.duties)
        self.report["duties_from_customer"] = seeded

    # RULE: 0092 -- заранее построенные пейринги: необязательный вход, оптимизатор их не изменяет
    def prepare_pins(self, pin_catalog: dict) -> None:
        """Объявить закреплённые участки и смены ДО построения смен.

        Вызывается перед `build_duties`: та обязана, во-первых, засеять
        закреплённые группы как смены-данность, во-вторых, не дать ни
        одной свободной смене содержать закреплённый участок.
        """
        for rec in pin_catalog.get("selected") or []:
            rec_legs: set[str] = set()
            for group in rec.get("duty_fns") or ():
                g = sorted((x for x in group if x in self.legs),
                           key=lambda x: self.legs[x]["t0"])
                if g:
                    self._pin_keys.add(tuple(g))
                    rec_legs.update(g)
            # Пересечение закреплённых структур между собой несовместимо
            # с разбиением: обе не могут существовать как отдельные
            # цепочки, и тихий демоушен разорвал бы одну из них. Отказ
            # громкий — выбор совместимого подмножества делается ДО
            # расчёта и вслух.
            clash = rec_legs & self._pin_active
            if clash:
                raise ValueError(
                    "закреплённые структуры пересекаются по участкам "
                    "(%d общих, пример %s) — постановка с перекрытиями "
                    "невозможна в точном разбиении; отберите совместимое "
                    "подмножество явно" % (len(clash), sorted(clash)[:3]))
            self._pin_active.update(rec_legs)

    def pin_chains(self, pin_catalog: dict) -> set[int]:
        """Закреплённые цепочки — данность с ЧЕСТНОЙ стоимостью.

        Отличия от уровня 1: закрепляется ПОДМНОЖЕСТВО цепочек при
        свободном поиске остального; перегоны пассажиром закреплённой
        цепочки сохраняются (без них цель недосчитывала бы её стоимость);
        происхождение цепочки остаётся в каталоге — судья аудита обязан
        уметь отличить унаследованное от построенного.
        """
        by_key = {tuple(x["id"] for x in d["legs"]): d for d in self.duties}
        used: set[int] = set()
        for rec in pin_catalog.get("selected") or []:
            groups = rec.get("duty_fns") or []
            chain = []
            for group in groups:
                g = sorted((x for x in group if x in self.legs),
                           key=lambda x: self.legs[x]["t0"])
                d = by_key.get(tuple(g))
                if d is not None:
                    chain.append(d)
            if not chain:
                continue
            chain.sort(key=lambda d: d["t0"])
            active = {l["id"] for d in chain for l in d["legs"]}
            dh_legs = [self.legs[x] for x in rec.get("fns") or ()
                       if x in self.legs and x not in active]
            self.fixed_chains.append(chain)
            self.fixed_meta.append({
                "sourceUnit": rec.get("sourceUnit") or "locked:?",
                "dh": sorted(l["id"] for l in dh_legs),
                "dh_legs": dh_legs, "origin": "pin"})
            used.update(d["i"] for d in chain)
        self.report["pinned_chains"] = sum(
            1 for m in self.fixed_meta if m.get("origin") == "pin")
        return used

    def fixed_resources(self) -> dict:
        """Ресурсы, занятые закреплёнными цепочками (внешний аудит
        04.09.2026, A5): закрепления — заранее выбранные члены решения
        (константа 1) в ТОЙ ЖЕ модели ресурсов, что свободные кандидаты.
        Возвращает: cov — рабочие участки закреплённых цепочек (рейс
        выполняется: перегон на нём законен всегда); dh — перегоны
        закреплённых цепочек с кратностью (занимают места на рейсе и
        перевозки дня); base_days — календарные дни командировок,
        стартующих с базы (занимают ёмкость базы)."""
        cov: set[str] = set()
        dh: list[str] = []
        base_days: Counter = Counter()
        for idx, ch in enumerate(self.fixed_chains):
            meta = (self.fixed_meta[idx] if idx < len(self.fixed_meta)
                    else {})
            cov.update(l["id"] for d in ch for l in d["legs"])
            dh_legs = list(meta.get("dh_legs") or [])
            dh.extend(l["id"] for l in dh_legs)
            acts = sorted([l for d in ch for l in d["legs"]] + dh_legs,
                          key=lambda l: l["t0"])
            if acts:
                base_days[acts[0]["dep"]] += self.span_days(ch, dh_legs)
        return {"cov": cov, "dh": dh, "base_days": base_days}

    def base_cap_eff(self, base: str) -> int:
        """Ёмкость базы для СВОБОДНЫХ цепочек = ёмкость минус дни
        закреплённых командировок с этой базы (A5). Закрепления сверх
        ёмкости — честная запись переполнения, предел свободным 0."""
        cap = self.base_day_cap[base]
        used = self.fixed_resources()["base_days"].get(base, 0)
        if used > cap:
            self._note_pin_overflow({"kind": "base_day_cap", "base": base,
                                     "cap": cap, "pinned": used})
        return max(0, cap - used)

    def _note_pin_overflow(self, rec: dict) -> None:
        lst = self.report.setdefault("pin_resource_overflow", [])
        if rec not in lst:
            lst.append(rec)
            print("ВНИМАНИЕ: закрепления сами превышают предел %s: "
                  "занято %s при пределе %s (%s)"
                  % (rec["kind"], rec["pinned"], rec["cap"],
                     rec.get("flight") or rec.get("day") or rec.get("base")),
                  flush=True)

    def judge_fixed_chains(self, strict: bool) -> None:
        """Единый суд для засеянных путей (реестр соответствия
        2026-08-28, группа Д): закреплённые и импортированные цепочки
        проходят ТОТ ЖЕ суд, что построенные и добираемые, — прежде
        они принимались данностью без единой проверки.

        Политика исходов: закрепление (origin=pin) с нарушением —
        громкий отказ построения (оно входит в НАШЕ решение); импорт
        заказчика (origin=customer, уровень 1) — поимённая пометка в
        отчёте: план оценивается, но публикация с нарушениями
        останавливается вердиктом (пользователь извещается)."""
        viols = []
        for idx, ch in enumerate(self.fixed_chains):
            meta = (self.fixed_meta[idx] if idx < len(self.fixed_meta)
                    else {})
            dh_legs = meta.get("dh_legs") or []
            reasons = []
            for d in ch:
                if not self.duty_limit_ok(d["legs"]):
                    reasons.append("предел рабочего времени смены: "
                                   + ",".join(l["id"] for l in d["legs"]))
            if not self.chain_ok(ch):
                reasons.append("цепочка: серия/состав/чартер")
            if not self.pairing_days_ok(ch, dh_legs):
                reasons.append("предел суток цепочки")
            if not self.charter_mix_ok(ch, extra_legs=dh_legs):
                reasons.append("смешение перевозчиков")
            if self.periods_tiers(ch, dh_legs) is None:
                reasons.append("периоды/отдых между периодами")
            if not self.adjacent_dh_ok(ch, dh_legs):
                reasons.append("примыкание перегонов (стыковка/"
                               "дебрифинг+посадка/пара)")
            if reasons:
                viols.append({
                    "sourceUnit": meta.get("sourceUnit") or "?",
                    "origin": meta.get("origin") or "?",
                    "reasons": reasons})
        self.report["fixed_chain_violations"] = viols
        if not viols:
            return
        print("ВНИМАНИЕ: %d засеянных цепочек с нарушениями правил — "
              "поимённо в отчёте (fixed_chain_violations)" % len(viols),
              flush=True)
        pin_bad = [v for v in viols if v["origin"] == "pin"]
        if strict and pin_bad:
            raise ValueError(
                "закреплённые цепочки нарушают правила (%d): %s — "
                "снимите закрепление либо исправьте каталог"
                % (len(pin_bad),
                   "; ".join(v["sourceUnit"] for v in pin_bad[:5])))

    def fix_customer_chains(self, base_catalog: dict) -> set[int]:
        """Уровень 1: цепочки заказчика принимаются как данность."""
        by_key = {tuple(x["id"] for x in d["legs"]): d for d in self.duties}
        used: set[int] = set()
        for rec in base_catalog.get("selected") or []:
            groups = rec.get("duty_fns") or [rec.get("active_fns")
                                             or rec.get("fns") or []]
            chain = []
            for group in groups:
                g = sorted((x for x in group if x in self.legs),
                           key=lambda x: self.legs[x]["t0"])
                d = by_key.get(tuple(g))
                if d is not None:
                    chain.append(d)
            if chain:
                chain.sort(key=lambda d: d["t0"])
                self.fixed_chains.append(chain)
                # Мета выравнивается с цепочкой ВСЕГДА (прежде её клали
                # только закрепления, и при сочетании уровня 1 с
                # закреплениями индексы меты смещались на чужие цепочки).
                self.fixed_meta.append({
                    "sourceUnit": rec.get("sourceUnit") or "customer:?",
                    "dh": [], "dh_legs": [], "origin": "customer"})
                used.update(d["i"] for d in chain)
        self.report["fixed_chains"] = len(self.fixed_chains)
        return used

    # --- рабочее время смены -------------------------------------------
    # RULE: 0019 0020 0021 -- состав служебного полётного времени: брифинг + секторы + дебрифинг
    def work_period(self, legs) -> tuple[datetime, datetime]:
        """Рабочее время смены: брифинг перед первым рабочим рейсом и
        дебрифинг после последнего.

        Позиционные перелёты по краям в рабочее время не входят — на этом
        этапе цепочка их ещё не содержит, поэтому берутся крайние участки
        смены. Когда брифинга у участка нет, применяется значение по
        умолчанию, и такие смены пересчитываются в отчёте: молчаливая
        подстановка выдала бы умолчание за данные заказчика.
        """
        first, last = legs[0], legs[-1]
        b = self.briefing_of.get(first["id"])
        if b is None:
            b = self.brief_by_station.get(first["dep"])
            src = "по станции"
            if b is None:
                b, src = self.brief_fallback, "по всем данным"
            self._brief_src[first["id"]] = src
        else:
            self._brief_src[first["id"]] = "из данных участка"
        db = self.debriefing_of.get(last["id"])
        if db is None:
            db = self.debrief_by_station.get(last["arr"])
            src = "по станции"
            if db is None:
                db, src = self.debrief_fallback, "по всем данным"
            self._debrief_src[last["id"]] = src
        else:
            self._debrief_src[last["id"]] = "из данных участка"
        if self.conditions is not None:
            # Пол документа поверх данных участка (правило и его смысл —
            # в `Conditions.briefing_floor`, единственной реализации на
            # построитель и выгрузку).
            b = self.conditions.briefing_floor(b, first["dep"] in self.bases)
            db = self.conditions.debriefing_floor(
                db, last["arr"] in self.bases)
        return (first["t0"] - timedelta(minutes=b),
                last["t1"] + timedelta(minutes=db))

    def work_time_provenance(self) -> dict:
        """Откуда взялся брифинг у каждого участка, побывавшего крайним.

        Число обращений ни о чём не говорит — перебор опрашивает одну и ту
        же смену многократно. Считаются различные участки, и вывод нельзя
        прочитать в отрыве от того, на скольких из них он стоит.
        """
        return {"briefing": dict(Counter(self._brief_src.values())),
                "debriefing": dict(Counter(self._debrief_src.values())),
                "briefing_fallback_min": self.brief_fallback,
                "debriefing_fallback_min": self.debrief_fallback}

    # RULE: 0022 0028 -- предел FDP смены и его проверка
    def hard_duty_span(self):
        """Жёсткий предел смены ветки без правил источника: потолок продления (--duty-cap), если он подан,
        иначе база (--duty-span). База остаётся порогом штрафа продления и границей «длинной» смены для
        яруса отдыха (мера 100, НАДЗОР 11:2x)."""
        cap = getattr(self.args, "duty_cap", None)
        return cap if cap is not None else self.args.duty_span

    def duty_limit_ok(self, legs) -> bool:
        """Смена укладывается в предел рабочего времени и счёта посадок.

        Счёт посадок (реестр соответствия 2026-08-28, группа А):
        правила R-S1-стр-11-абз-15/16/17 — условная тройка пределов
        5/4/2 по касанию окна минимальной циркадной активности;
        контракт несёт её строжайшую свёртку скаляром
        (max_landings_per_duty — наихудшая трактовка, законна всегда).
        Прежде скаляр держал только собственный перебор (кап
        генерации) — засеянные пути (уровни 1-2, закрепления, добор)
        миновали счёт вовсе; судья обязан не мягче генератора.
        Условная форма лестницы — расширение машинного носителя ВМП
        (не блокер: действующее строже)."""
        if self.rules is not None and \
                len(legs) > self.rules.max_landings_per_duty:
            return False
        # RULE: R-S9-табл-1-строка-19-кол-4 R-S6-табл-1-строка-17-кол-3
        # -- ранние вылеты из базы (пороги UTC из формулировок ВМП):
        # участок с вылетом из базы раньше порога не достигается
        # СТЫКОВКОЙ в базе (обязан начинать смену); смена с ранним
        # вылетом из базы не содержит промежуточной посадки в базе.
        # Ключей нет — правила не применяются (прежние наборы условий).
        if self.conditions is not None and legs:
            thr = getattr(self.conditions,
                          "base_sit_dep_utc_before_min", None)
            if thr is not None:
                for nx in legs[1:]:
                    if nx["dep"] in self.bases and \
                            nx["t0"].hour * 60 + nx["t0"].minute < thr:
                        return False
            thr2 = getattr(self.conditions,
                           "base_turn_dep_utc_before_min", None)
            f0 = legs[0]
            if thr2 is not None and f0["dep"] in self.bases \
                    and f0["t0"].hour * 60 + f0["t0"].minute < thr2 \
                    and any(l["arr"] in self.bases for l in legs[:-1]):
                return False
        # Внешний аудит 04.09.2026, A1: ветка без файла правил меряла
        # смену «вылет → прилёт» (без брифинга и дебрифинга), а с
        # правилами — рабочим временем через work_period: одна норма
        # ВМП судилась по-разному. Обе ветки меряют одно рабочее время.
        w0, w1 = self.work_period(legs)
        work = (w1 - w0).total_seconds() / 60
        if self.rules is None:
            return work <= self.hard_duty_span()
        if self.conditions is not None:
            if self.conditions.has_exact_worktime:
                # Генерационное сито точной формы: смена допустима, если
                # проходит хоть при каком состоянии — акклиматизированный
                # предел не мягче предела неопределённого состояния, а
                # ступени лестницы продления не мягче базового. Верх
                # сита — верхняя ступень; точная проверка по положению
                # смены в цепочке — в `chain_tiers`: там известно время
                # с начала ротации.
                base, steps = self._exact_limits(legs, w0, len(legs),
                                                 acclimatized=True)
                lim = steps[-1][1] if steps else base
                return lim is not None and work <= lim
            # Предел зависит от ЧИСЛА ПОСАДОК и от доли ночи (П.2.1), а не
            # от одного порога: 12 часов при одной-двух посадках днём, но
            # 10 при четырёх ночью, а пять и более посадок ночью запрещены
            # вовсе. Перегоны пассажиром в число посадок не входят —
            # здесь их ещё нет, смена состоит из рабочих участков.
            lim = self.conditions.work_limit(
                len(legs), self.rules.is_night_duty(w0, w1))
            return lim is not None and work <= lim
        if self.rules.duty_span_matrix:
            # Матрица предела смены (ревизия 1.10): предел по окну
            # местного старта и числу участков, строже яруса состава —
            # действует минимум из двух. Смена, не попавшая ни в одну
            # строку матрицы, недопустима: молчаливый возврат к плоскому
            # пределу превратил бы дыру покрытия матрицы в ослабление.
            lim = self.rules.duty_span_limit(w0, len(legs))
            if lim is None or work > lim:
                return False
        return self.rules.tier_for_work_time(work) is not None

    # RULE: 0022 0023 0024 0030 0085 -- точные пределы прил. 2/3/4/6 по акклиматизации, посадкам и классу места отдыха
    def _exact_limits(self, legs, w0, landings, acclimatized):
        """(базовый предел, ступени продления) точной формы для смены.

        Период старта считается в базовом поясе (акклиматизация «Б» —
        к местному времени базового пояса). Когда станция старта в
        поясе ближе порога таблицы к базе, дополнительно берётся предел
        по местному времени станции и применяется меньший из двух:
        расхождение до двух часов может сдвинуть период начала, и
        двусмысленность решается в жёсткую сторону.

        Ступени — лестница по числу дополнительных членов экипажа
        (наименьшую достаточную выбирает вызывающий судья). Надбавка
        длинного сектора (правило R-S3-стр-7-абз-9): при секторе сверх
        порога и числе секторов не более границы каждая ступень
        удлиняется на надбавку; к базовому пределу надбавка не
        применяется — носитель говорит о продлённой смене (наихудшая
        трактовка до ответа анкеты по лестнице, вердикт выпуска ВМП
        2026-08-28).
        """
        c = self.conditions
        base_off = self.rules.base_tz_offset_min
        start_base_local = int((w0 - datetime(1970, 1, 1)).total_seconds()
                               // 60 + base_off)
        fleet = legs[0]["fleet"]
        base, steps = c.exact_limits(start_base_local, landings,
                                     acclimatized, fleet)
        # A9: пояс станции на момент начала смены; местное время станции
        # = метка + сдвиг шкалы + (пояс станции − пояс базы) — разница
        # поясов от шкалы меток не зависит.
        st_off = self.station_off(legs[0]["dep"], w0)
        bz = self.base_zone_off(w0)
        if (acclimatized and st_off is not None and bz is not None
                and st_off != bz):
            b2, _s2 = c.exact_limits(
                int((w0 - datetime(1970, 1, 1)).total_seconds() // 60
                    + base_off + (st_off - bz)), landings, acclimatized,
                fleet)
            if b2 is not None and (base is None or b2 < base):
                base = b2
        bonus = c.long_sector_bonus
        # Условие секторов — по СУДИМОЙ ЕДИНИЦЕ (параметр landings: у
        # слитого периода это секторы всего периода, не последней
        # смены), длинный сектор ищется среди переданных участков —
        # слитый период с тремя секторами надбавки не получает
        # (состязательная выверка пересверки 2026-08-28).
        if bonus and steps and int(landings) <= bonus["max_sectors"] \
                and any((l["t1"] - l["t0"]).total_seconds() / 60
                        > bonus["over_min"] for l in legs):
            steps = [(k, lim + bonus["add_min"]) for k, lim in steps]
        return base, steps

    @staticmethod
    def _least_tier(span_min, base, steps):
        """Наименьшая достаточная ступень состава для длительности:
        0 — базовый предел; иначе первая ступень лестницы, чей предел
        не меньше длительности; None — сверх верхней ступени (смена
        недопустима). Зеркало tier_for_work_time контракта — то же
        правило наименьшего достаточного состава."""
        if base is not None and span_min <= base:
            return 0
        for crew, lim in steps:
            if span_min <= lim:
                return crew
        return None

    # RULE: 0047 0048 -- порог отделения перегона: перегон отдельным нарядом
    def _dh_rest_floor(self, leg) -> float:
        """Отдых, требуемый ПОСЛЕ перегонного периода из одного перелёта.

        Порогом отделения головной заброски служит отдых после НЕЁ, а не
        после рабочей смены: перегонная смена коротка, и мерить её чужой
        длительностью значило бы объявлять отделённым то, что отделено не
        было (и наоборот).
        """
        pseudo = {"legs": [leg], "t0": leg["t0"], "t1": leg["t1"],
                  "span": (leg["t1"] - leg["t0"]).total_seconds() / 60}
        self.annotate_duty(pseudo)
        return self.rest_after(pseudo, leg["arr"])

    # RULE: 0037 0046 0049 0050 0051 0086 0087 -- период обязанностей: рабочие секторы и перегоны, не отделённые нормативным отдыхом
    def periods_of(self, chain: list[dict], dh_legs=()):
        """Периоды обязанностей цепочки — ЕДИНОЙ моделью `_duty_periods`.

        Возвращает список записей (начало, конец, ярус, смена, станция
        окончания); None — цепочка недопустима. Той же моделью период
        строит выгрузка решения и судит аудит: три реализации одного
        понятия расходились, и расхождение проявлялось не ошибкой, а
        несогласием результата с проверкой.
        """
        import _duty_periods as dp

        if (self.conditions is None
                or not self.conditions.has_exact_worktime):
            ts = self.chain_tiers(chain)
            if ts is None:
                return None
            pers = [[self.busy_from(d), self.free_from(d), tier, d,
                     d["legs"][-1]["arr"]] for d, tier in zip(chain, ts)]
            # Перегоны участвуют в периодах И НА ФЛАГОВЫХ МИРАХ (без
            # условий источника): перегон, не отделённый нормативным
            # отдыхом, продолжает период, и предел меряется всем
            # периодом. Прежде запасная ветка перегоны игнорировала:
            # цепочка «смена 599 мин + пауза 599 мин на станции (порог
            # 600) + возвратный перегон» уходила в каталог законной, а
            # судья решателя видел период 1367 мин при пределе — минус
            # 8160 жёстких на затравке Полярис (2026-08-26).
            lim = self.hard_duty_span() or (
                self.rules.max_duty_span_min if self.rules else None)
            for l in sorted(dh_legs or (), key=lambda x: x["t0"]):
                attached = False
                dh_span = (l["t1"] - l["t0"]).total_seconds() / 60
                for per in pers:
                    if per[0] <= l["t0"] and l["t1"] <= per[1]:
                        attached = True
                        break
                    gap_to = (per[0] - l["t1"]).total_seconds() / 60
                    if 0 <= gap_to < self.rest_after(
                            {"span": dh_span}, l["arr"]):
                        per[0] = min(per[0], l["t0"])
                        attached = True
                        break
                    gap_from = (l["t0"] - per[1]).total_seconds() / 60
                    if 0 <= gap_from < self.rest_after(per[3], per[4]):
                        per[1] = max(per[1], l["t1"])
                        per[4] = l["arr"]
                        attached = True
                        break
                if not attached:
                    pers.append([l["t0"], l["t1"], 0, chain[0], l["arr"]])
                    pers.sort(key=lambda x: x[0])
            if lim:
                for per in pers:
                    if (per[1] - per[0]).total_seconds() / 60 > lim:
                        return None
            return [tuple(x) for x in sorted(pers, key=lambda x: x[0])]

        brief = self.conditions.briefing(chain[0]["legs"][0]["dep"]
                                         in self.bases)
        debrief = self.conditions.debriefing(True)
        acts = []
        for d in chain:
            for l in d["legs"]:
                acts.append(dp.Activity(l["id"], l["t0"], l["t1"], l["dep"],
                                        l["arr"], True, d))
        for l in dh_legs:
            acts.append(dp.Activity(l["id"], l["t0"], l["t1"], l["dep"],
                                    l["arr"], False, None))

        def floor(period, station):
            # Пол отдыха считается от СВОЕГО периода: у рабочего — по
            # его смене, у чисто перегонного — по нему самому (перегон
            # короток, и мерить его чужой рабочей сменой значило бы
            # требовать отдыха, которого правило не требует).
            return self.rest_after(self._duty_for_period(period), station)

        periods = dp.build_periods(acts, floor, brief, debrief)
        # RULE: 0038 0039 0047 -- «недоотдых» запрещён: стоянка внутри
        # периода обязанностей не длиннее внутрисменной стыковки.
        # Перегон после рабочей смены допустим ровно в двух видах: как
        # продолжение той же смены коротким примыканием (кресты дальних)
        # либо после ПОЛНОГО нормативного отдыха отдельным нарядом.
        # Середина — отдых есть, но меньше требуемого max(FDP, пол), —
        # нарушает правило отдыха, а в трактовке per_duty ещё и не
        # судится пределом периода: рабочая часть в норме, хвост
        # растягивает период безнаказанно (каталог Б 2026-08-25 — 4
        # цепочки, отдых 600..775 при требуемых 840..935, поймано
        # аудитом передаваемого файла).
        # Верх окна стыковки — граница «стыковка / отдых» по полу
        # отдыха ВМП на станции паузы (sit_cap_at); литерала перебора
        # больше нет (ревизия литералов 2026-09-03).
        # RULE: R-S9-табл-1-строка-19-кол-4 -- участок (рабочий ИЛИ
        # перегон) с вылетом из базы раньше порога UTC не достигается
        # стыковкой в базе: внутри периода обязанностей любой
        # непервый участок достигнут стыковкой по построению периода.
        # Точка едина для всех видов участков — состязательная
        # выверка 2026-08-28 показала, что суд только рабочих легов
        # (duty_limit_ok) пропускал перегон, достигнутый стыковкой.
        thr_sit = getattr(self.conditions,
                          "base_sit_dep_utc_before_min", None) \
            if self.conditions is not None else None
        for per in periods:
            for a, nxt in zip(per.activities, per.activities[1:]):
                if (nxt.t0 - a.t1).total_seconds() / 60 \
                        >= self.sit_cap_at(nxt.dep):
                    return None
                if thr_sit is not None and nxt.dep in self.bases \
                        and nxt.t0.hour * 60 + nxt.t0.minute < thr_sit:
                    return None
        t0 = periods[0].start
        out = []
        per_duty = self.conditions.limit_per_duty
        for per in periods:
            if not per.working:
                # Период из одних перегонов: табличного предела у него
                # нет (перегон не рабочий сектор), ярус нулевой.
                out.append((per.start, per.end, 0, chain[0], per.end_station))
                continue
            d = per.working[-1].payload
            span = (per.work_span_min(brief, debrief) if per_duty
                    else per.span_min)
            st = per.working[0].dep
            acclim = self.conditions.is_acclimatized(
                self.station_off(st, per.start),
                self.base_zone_off(per.start),
                (per.start - t0).total_seconds() / 60)
            # Слитый период может нести смены РАЗНЫХ типов ВС — судится
            # форма пределов КАЖДОЙ смены, ярус периода — наихудший из
            # наименьших достаточных. Прежний суд по классу последней
            # смены пропускал период, где строгий тип стоял не последним
            # (состязательная выверка пересверки 2026-08-28).
            tier = 0
            seen_ids = set()
            for w in per.working:
                if id(w.payload) in seen_ids:
                    continue
                seen_ids.add(id(w.payload))
                base, steps = self._exact_limits(
                    w.payload["legs"], per.start, per.sectors, acclim)
                t = self._least_tier(span, base, steps)
                if t is None:
                    return None
                tier = max(tier, t)
            out.append((per.start, per.end, tier, d, per.end_station))
        return out

    def _duty_for_period(self, period):
        """Смена, по которой считается отдых после периода.

        У периода с работой это его последняя рабочая смена; у периода
        из одних перегонов — псевдосмена из его же активностей: перегон
        тоже период обязанностей, но короткий, и пол отдыха у него свой.
        """
        w = period.working
        if w:
            return w[-1].payload
        legs = [self.legs[a.key] for a in period.activities
                if a.key in self.legs]
        pseudo = {"legs": legs, "t0": legs[0]["t0"], "t1": legs[-1]["t1"],
                  "span": (legs[-1]["t1"] - legs[0]["t0"]).total_seconds() / 60}
        self.annotate_duty(pseudo)
        return pseudo

    # RULE: 0038 0039 0040 0043 -- нормативный отдых после периода: база, внебаза, разница поясов
    def rest_floor_after(self, period) -> float:
        """Отдых, требуемый после периода обязанностей, минут.

        Три поправки против наивного счёта, каждая из правил:
          * станция — ОКОНЧАНИЯ периода: после перегона домой это база,
            а не станция последнего рабочего сектора;
          * длительность — ПЕРИОДА, а не рабочей части: формула правила
            0038 говорит max(предыдущий FDP, пол), и перегон в период
            входит (0047), значит удлиняет и требуемый отдых;
          * ярус — правило 0041: после продлённого предела не менее
            четырнадцати часов.
        """
        p_start, p_end, tier, d, end_st = period
        span = (p_end - p_start).total_seconds() / 60
        probe = dict(d)
        probe["work"] = max(d.get("work") or 0, span)
        need = self.rest_after(probe, end_st)
        if tier >= 1:
            need = max(need, 14 * 60)
        return need

    # RULE: 0055 0056 -- интервал между соседними перелётами пассажиром
    def adjacent_dh_ok(self, chain, dh_legs) -> bool:
        """Соседние перегоны внутри одного периода обязанностей разделены
        нормативным минимумом (правило 0055 свода).

        Минимум приходит из условий; без него правило не применяется —
        своего числа здесь нет.
        """
        gap_min = getattr(self.conditions, "adjacent_dh_min", None)             if self.conditions is not None else None
        if not dh_legs:
            return True
        # Правило R-S5-табл-2-строка-13-кол-2 (HARD): минимальная
        # стыковка для перегонов — перегон, примкнувший к работе,
        # обязан отстоять от вылета РАБОЧЕГО участка не меньше порога
        # стыковки (порог — параметр условий, своего числа здесь нет).
        # Судья прежде пары «перегон -> работа» не проверял вовсе, и
        # порция с зазором чуть меньше порога
        # прошла суд (перекрёстная сверка линий 2026-08-28). Генератор
        # это правило держит с рождения (pre_dh_latest) — судья обязан
        # не мягче; проверка живёт ДО условия пар 0055: правило
        # стыковки от файла условий не зависит.
        sit_thr = (self.args.sit_min if self.rules is not None
                   else self.args.min_rest_sit)
        if sit_thr:
            work = sorted((l for d in chain for l in d["legs"]),
                          key=lambda l: l["t0"])
            for l in sorted(dh_legs, key=lambda x: x["t0"]):
                nxt = next((w for w in work if w["t0"] >= l["t1"]),
                           None)
                if nxt is None:
                    continue
                gap = (nxt["t0"] - l["t1"]).total_seconds() / 60
                if 0 <= gap < sit_thr - 1e-6:
                    return False
        # Обратное примыкание «работа -> перегон» судится СВОИМ
        # составным правилом: дебрифинг рабочего участка (R-S4-15)
        # плюс посадка на перегон после дебрифинга
        # (правило 0047/0048) — зазор от прилёта работы
        # до вылета перегона не меньше их суммы. Аудитор одной из
        # линий нашёл нарушения обоих направлений в сданном финале
        # — судья обязан держать оба направления.
        if self.rules is not None or self.conditions is not None:
            work_sorted = sorted(
                (l for d in chain for l in d["legs"]),
                key=lambda l: l["t1"])
            board = self.dh_board_min()
            for l in sorted(dh_legs, key=lambda x: x["t0"]):
                prev = next((w for w in reversed(work_sorted)
                             if w["t1"] <= l["t0"]), None)
                if prev is None:
                    continue
                gap = (l["t0"] - prev["t1"]).total_seconds() / 60
                # Дебрифинг — из УСЛОВИЙ по станции прилёта работы
                # (в данных рейсов поля нет); поле данных —
                # лишь переопределение, когда есть.
                deb = self.debriefing_of.get(prev["id"])
                if deb is None and self.conditions is not None:
                    deb = self.conditions.debriefing(
                        prev["arr"] in self.bases)
                floor = (deb or 0) + board
                if 0 <= gap < floor - 1e-6:
                    return False
        # Правило R-S8-п-7 (HARD, вердикт выпуска ВМП): для регулярного
        # расписания запрещён второй перегон подряд.
        # Слово «подряд» двусмысленно в первоисточнике (протокол
        # вопросов-ответов заказчика) — вопрос анкеты поставлен; до ответа
        # действует НАИХУДШАЯ ШИРОКАЯ трактовка: два смежных перегона
        # цепочки без работы между ними запрещены, даже разделённые
        # полноценным отдыхом, — если хотя бы один регулярный.
        # Чартерная половина: перегон чартером из БАЗОВОГО аэропорта
        # запрещён (эстафеты внутри цепочки судятся своим правилом).
        if self.conditions is not None and dh_legs:
            # Нет признака чартера у приёмника условий — рейс
            # считается РЕГУЛЯРНЫМ (строже; стабы тестов сюда же).
            _chart = getattr(self.conditions, "is_charter", None) \
                or (lambda _fn: False)
            events = sorted(
                [(l, True) for l in dh_legs]
                + [(l, False) for d in chain for l in d["legs"]],
                key=lambda p: p[0]["t0"])
            prev_dh = None
            for leg, is_dh in events:
                if not is_dh:
                    prev_dh = None
                    continue
                if prev_dh is not None:
                    if not (_chart(_true_fn(prev_dh))
                            and _chart(_true_fn(leg))):
                        return False
                prev_dh = leg
            for l in dh_legs:
                if _chart(_true_fn(l)) and l["dep"] in self.bases:
                    return False
        if not gap_min:
            return True
        # Пара перегонов, разделённая МЕНЬШЕ чем нормативом, заведомо
        # лежит в одном периоде обязанностей — где бы она ни стояла:
        # головная пересадка ДО первой рабочей смены (позиционирование)
        # прежде ускользала от периодного цикла ниже, и пара с зазором
        # вдвое меньше поданного норматива проходила (кнопка №19).
        all_dh = sorted(dh_legs, key=lambda l: l["t0"])
        for a, b in zip(all_dh, all_dh[1:]):
            gap = (b["t0"] - a["t1"]).total_seconds() / 60
            if 0 <= gap < gap_min - 1e-6:
                return False
        pers = self.periods_of(chain, dh_legs)
        if pers is None:
            return False
        ids = {l["id"] for l in dh_legs}
        for p_start, p_end, _tier, _d, _st in pers:
            inside = sorted((l for l in dh_legs
                             if p_start <= l["t0"] and l["t1"] <= p_end),
                            key=lambda l: l["t0"])
            for a, b in zip(inside, inside[1:]):
                gap = (b["t0"] - a["t1"]).total_seconds() / 60
                if gap < gap_min - 1e-6:
                    return False
        return True

    # RULE: 0038 0041 -- периоды разделяются только полным нормативным отдыхом
    def periods_tiers(self, chain: list[dict], dh_legs=()) -> list[int] | None:
        """Ярусы периодов; None — цепочка недопустима.

        Здесь же судится отдых МЕЖДУ периодами: он отсчитывается от
        конца периода (включая хвостовой перегон и его завершающие
        работы) до начала следующего.
        """
        pers = self.periods_of(chain, dh_legs)
        if pers is None:
            return None
        for a, b in zip(pers, pers[1:]):
            gap = (b[0] - a[1]).total_seconds() / 60
            if gap < self.rest_floor_after(a) - 1e-6:
                return None
        return [p[2] for p in pers]

    # RULE: 0022 0085 -- предел применяется к каждому периоду цепочки
    def chain_tiers(self, chain: list[dict]) -> list[int] | None:
        """Ярус состава каждой смены цепочки точной формой; None —
        цепочка недопустима.

        Состояние акклиматизации смены зависит от её положения: время с
        начала обязанностей ротации в базовом поясе (прил. 2). Смена
        сверх базового предела допустима только ступенью лестницы
        продления своего класса места отдыха — ярус равен ЧИСЛУ
        дополнительных членов наименьшей достаточной ступени (отдых в
        полёте); сверх верхней ступени не строится вовсе.
        """
        if (self.conditions is None
                or not self.conditions.has_exact_worktime):
            return [d.get("tier") or 0 for d in chain]
        t0 = self.busy_from(chain[0])
        out = []
        for d in chain:
            elapsed = (self.busy_from(d) - t0).total_seconds() / 60
            st = d["legs"][0]["dep"]
            acclim = self.conditions.is_acclimatized(
                self.station_off(st, self.busy_from(d)),
                self.base_zone_off(self.busy_from(d)), elapsed)
            base, steps = self._exact_limits(
                d["legs"], self.busy_from(d), len(d["legs"]), acclim)
            tier = self._least_tier(d.get("work") or 0, base, steps)
            if tier is None:
                return None
            out.append(tier)
        return out

    # RULE: 0098 -- ночная обязанность: любая часть в окне 22:00-06:00
    def annotate_duty(self, d: dict) -> None:
        """Признаки смены, от которых зависят отдых и порядок смен."""
        w0, w1 = self.work_period(d["legs"])
        d["work0"], d["work1"] = w0, w1
        d["work"] = (w1 - w0).total_seconds() / 60
        if self.rules is None:
            # Аудит A1 (04.09.2026): рабочее время и без файла правил —
            # с брифингом/дебрифингом, как в duty_limit_ok; прежде
            # здесь стоял размах «вылет → прилёт».
            d["tier"] = 0
            d["night"] = d["dark"] = d["trans"] = False
            return
        if self.conditions is not None:
            # Ярус здесь предварительный: без точной формы усиление не
            # применяется вовсе (условия, группа B), а с точной формой
            # ярус смены зависит от её положения в ротации и считается
            # на цепочке (`chain_tiers`) — здесь контекста ещё нет.
            d["tier"] = 0
        else:
            d["tier"] = self.rules.tier_for_work_time(d["work"])
        # Метка ночи — от ОПРЕДЕЛЕНИЯ УСЛОВИЙ, когда оно есть (реестр
        # 2026-08-28, группа В: определение а/к разбиралось, но метку
        # ставил контракт, гашеный нейтрализатором); контракт —
        # запасной путь без файла условий.
        if self.conditions is not None:
            d["night"] = self.conditions.is_night_duty(
                w0, w1, self.rules.base_tz_offset_min)
        else:
            d["night"] = self.rules.is_night_duty(w0, w1)
        d["dark"] = self.rules.is_dark_duty(w0, w1)
        # Признак «дальней» стоянки принадлежит МЕСТУ отдыха, а не смене:
        # здесь он проставлен по станции прилёта только ради отчёта, а в
        # решении берётся по фактической станции стоянки (`rest_after`).
        d["trans"] = self.is_trans_station(d["legs"][-1]["arr"])

    # --- правила отдыха и перегоны -------------------------------------
    def dh_board_min(self) -> int:
        """Зазор от конца дебрифинга до посадки на перегон домой.

        С правилами источника — его границa (см. `_cos_rules`,
        откалибровано по его решению). Прежний свой параметр был чуть
        жёстче и рушил единственный способ обслужить еженедельный
        рейс на дальнюю станцию — а источник этот способ
        применяет во всех таких случаях."""
        if self.rules is None:
            return self.args.min_rest_sit
        # Поле файла может отсутствовать: это минимальный зазор, и
        # молчаливый откат на args был бы ослаблением — отказ словами.
        return self.rules._need("dh_board_after_debrief_min")

    def pre_dh_latest(self, duty: dict) -> datetime:
        """Крайний прилёт головного перегона: вылет работы минус
        минимальная стыковка.

        По решению источника (наряды с головным перегоном): минимум
        зазора — ровно его минимальная стыковка, а брифинг
        НАКЛАДЫВАЕТСЯ на перегон, поэтому граница «к началу брифинга»
        неверна — она осиротила бы зеркальные еженедельные рейсы, которые
        он сам возит этим способом. Прежний свой параметр был МЯГЧЕ его
        границы."""
        gap = (self.args.sit_min if self.rules is not None
               else self.args.min_rest_sit)
        return duty["t0"] - timedelta(minutes=gap)

    # RULE: 0043 -- трансмеридиональность по разнице поясов с базой
    def is_trans_station(self, station: str) -> bool:
        if self.rules is None:
            return False
        # A9: пояс станции первого касания (момента у вызова нет) —
        # через единый метод station_off.
        return self.rules.is_trans_layover(self.station_off(station))

    def sit_cap_at(self, station: str) -> float:
        """Верх окна стыковки на станции: пауза КОРОЧЕ пола отдыха ВМП
        на этой станции — стыковка (остаёмся в смене), пауза не короче
        пола — отдых (смена кончилась). Граница «стыковка / отдых»
        принадлежит ВМП, не перебору (ревизия литералов 2026-09-03:
        прежний литерал 240 мин рвал смены на паузах, которых
        авиакомпания не запрещала). Явный --sit-max — перекрытие
        оператора: действует, но объявляется в отчёте и подписи."""
        a = self.args
        explicit = getattr(a, "sit_max", None)
        if explicit is not None:
            # 06.09.2026 (решение владельца): кнопка передаёт сюда границу
            # из правил пакета (vmp.sit_boundary: правило максимальной
            # стыковки, иначе пол минимального отдыха) — одна линейка
            # «стыковка / отдых» у построителя, решателя и проверки по
            # ИДАК; происхождение — в ведомости входа кнопки
            # (builder-input.json, provenance[--sit-max]).
            self.report.setdefault("sit_window", {
                "mode": "задан --sit-max: граница «стыковка / отдых» — "
                        "порог правила максимальной стыковки пакета "
                        "(кнопка «Пейринг») или явное перекрытие оператора",
                "cap_min": explicit})
            return float(explicit)
        floor = a.rest_base if station in self.bases else a.rest_off
        if floor is None:
            raise BuilderConfigError(
                "верх окна стыковки не определён: нет пола отдыха ВМП "
                "(--rest-base/--rest-off) и нет явного --sit-max — "
                "граница «стыковка / отдых» ниоткуда не берётся")
        self.report.setdefault("sit_window", {
            "mode": "по полу отдыха ВМП: пауза короче пола отдыха станции "
                    "— стыковка, иначе отдых",
            "cap_base_min": a.rest_base, "cap_outstation_min": a.rest_off})
        return float(floor)

    # RULE: 0038 0039 0040 -- ярусная таблица отдыха
    def rest_after(self, duty: dict, station: str) -> int:
        a = self.args
        on_base = station in self.bases
        # A9: момент начала отдыха — окончание смены; у хвостовой смены
        # сборки каталога поля t1 нет, берётся прилёт последнего перелёта.
        _rest_from = duty.get("t1") or (
            duty["legs"][-1]["t1"] if duty.get("legs") else None)
        if self.rules is None:
            long_duty = duty["span"] > a.duty_span
            if on_base:
                return a.rest_base_long if long_duty else a.rest_base
            return a.rest_off_long if long_duty else a.rest_off
        if self.conditions is not None:
            # Условия заказчика (`usloviya-rascheta.json`) заменяют таблицу,
            # выведенную из настроек источника: отдых берётся по П.3.5/П.3.7,
            # признак дальнего перелёта — по порогу условий на фактическом
            # расхождении поясов, а без порога или пояса — по принятой
            # наихудшей трактовке; вне базы добавляется дорога до места
            # отдыха (П.3.1). Пояс базы — из документа правил: данные
            # станций несут пояс места стоянки, а сторону сравнения
            # задаёт постановка.
            need = self.conditions.required_layover(
                duty.get("work", duty["span"]), on_base,
                bool(duty.get("night")),
                self.conditions.is_trans(
                    self.station_off(station, _rest_from),
                    self.base_zone_off(_rest_from)))
        else:
            need = self.rules.required_layover(
                duty.get("work", duty["span"]), on_base,
                bool(duty.get("night")), self.is_trans_station(station))
        if duty.get("night"):
            # Ночная серия длиной один: отдых после неё отдельным правилом,
            # и он может быть строже табличного.
            need = max(need, self.rules.layover_after_night_sequence(on_base))
        return need

    # RULE: none -- правил разделения чартера и регулярного расписания в своде нет; признаки выключены недостижимыми значениями (b7_lowcost_vs_charter), проверка возвращает истину всегда
    def charter_mix_ok(self, chain: list[dict], extra_legs=()) -> bool:
        """Чартер и регулярное расписание не в одной связке (П.1.1.5).

        Чартерным считается рейс с четырёхзначным номером, не
        начинающимся с девятки: четырёхзначные с девяткой — лоукостер
        (условия, группа B: документ задаёт оба признака одним и тем же
        свойством номера, и это разведено трактовкой).

        Перегоны учитываются наравне с рабочими участками: правило
        запрещает и выполнять чартером перегоны.

        Признак считается по ПОДЛИННОМУ номеру рейса. Внутренний
        идентификатор участка несёт суффикс даты и порядкового номера
        (`ZZ3223-20260812-1`), и счёт цифр по всей строке никогда не
        давал «ровно четыре» — проверка молча пропускала всё. Признак,
        который не может сработать, — не проверка.
        """
        if self.conditions is None:
            return True
        kinds = set()
        for d in chain:
            for l in d["legs"]:
                kinds.add(self.conditions.is_charter(_true_fn(l)))
        for l in extra_legs:
            kinds.add(self.conditions.is_charter(_true_fn(l)))
        return len(kinds) <= 1

    # RULE: 0050 -- брифинг при перегоне перед рабочим сектором
    def head_dh_ok(self, duty: dict, dh_legs) -> bool:
        """Смена с перегоном ПЕРЕД ней укладывается в предел (П.2.10).

        Перелёт пассажиром к месту работы засчитывается в полётное рабочее
        время: сто процентов от времени перелёта, если перед рейсом не
        предоставлен межполётный отдых от двенадцати часов. Мы такой отдых
        не планируем, поэтому засчитывается полностью (условия, группа A;
        доля выбрана наихудшей из двух возможных).

        Из-за этого допустимость смены перестаёт зависеть только от неё
        самой: одна и та же смена законна без перегона и незаконна с ним.
        Проверка стоит там, где перегон выбирается, — иначе предел
        обходился бы простым добавлением дороги.
        """
        if self.conditions is None or not dh_legs:
            return True
        pct = self.conditions.deadhead_to_work_pct / 100.0
        extra = sum((l["t1"] - l["t0"]).total_seconds() / 60 for l in dh_legs)
        work = duty.get("work")
        if work is None:
            w0, w1 = self.work_period(duty["legs"])
            work = (w1 - w0).total_seconds() / 60
        if self.conditions.has_exact_worktime:
            if self.conditions.limit_per_duty:
                # Трактовка «per_duty»: перегоны предел не удлиняют.
                return True
            # Перегон и работа без нормативного отдыха между ними — ОДИН
            # период обязанностей (правила 0048/0050), и предел меряется
            # его ДЛИТЕЛЬНОСТЬЮ, а не суммой времён перелётов: стоянка
            # между перегоном и работой тоже проходит в обязанностях.
            # Прежняя форма (work + extra) не видела стоянок и выпускала
            # периоды до 23 часов — поймано аудитом передаваемого файла
            # 2026-08-25. Практика авиакомпании ту же норму подтверждает:
            # у неё перегон всегда отдельная смена, а между перегоном и
            # работой стоит отдых от двенадцати часов (39 случаев из 39).
            w0 = duty.get("work0") or duty["t0"]
            w1 = duty.get("work1") or duty["t1"]
            head = [l for l in dh_legs if l["t1"] <= w0]
            tail = [l for l in dh_legs if l["t0"] >= w1]
            p_start, p_end = w0, w1
            for l in head:
                if (w0 - l["t1"]).total_seconds() / 60 < self._dh_rest_floor(l):
                    p_start = min(p_start, l["t0"] - timedelta(
                        minutes=self.conditions.briefing(
                            l["dep"] in self.bases)))
            for l in tail:
                if (l["t0"] - w1).total_seconds() / 60 < self.rest_after(
                        duty, w1 and duty["legs"][-1]["arr"]):
                    p_end = max(p_end, l["t1"] + timedelta(
                        minutes=self.conditions.debriefing(
                            l["arr"] in self.bases)))
            span = (p_end - p_start).total_seconds() / 60
            base, steps = self._exact_limits(
                duty["legs"], p_start, len(duty["legs"]), acclimatized=True)
            lim = steps[-1][1] if steps else base
            return lim is not None and span <= lim
        lim = self.conditions.work_limit(
            len(duty["legs"]), bool(duty.get("night")))
        return lim is not None and work + extra * pct <= lim

    def _duty_ok_cached(self, d: dict) -> bool:
        """Суд смены с кэшем на объекте: цепочечные судьи зовут его на
        каждом кандидате, а смена одна и та же — без кэша построитель
        пересчитывал бы брифинги десятки тысяч раз."""
        ok = d.get("_limit_ok")
        if ok is None:
            ok = self.duty_limit_ok(d["legs"])
            d["_limit_ok"] = ok
        return ok

    # RULE: 0022 0025 0026 0027 0029 -- цепочка целиком; продления к пределу не применяются
    def chain_ok(self, chain: list[dict]) -> bool:
        """Цепочка целиком по правилам, которых нет в таблице отдыха.

        Сюда же — запрет смешивать чартер с регулярным расписанием
        (П.1.1.5): правило говорит о СВЯЗКЕ целиком, поэтому проверка
        стоит здесь, а не на переходе между сменами.

        Проверять переход «предыдущая-следующая» недостаточно: правила
        «не более N ночных смен подряд» и «не более K тёмных в окне из M»
        говорят о ПОСЛЕДОВАТЕЛЬНОСТИ. Прежде первое исполнялось вручную и
        только для частного случая N=1 (при N=2 отключалось целиком), а
        второе не исполнялось вовсе, хотя разбиралось и попадало в
        отпечаток — на наборе заказчика тёмными помечены 232
        смены, то есть правило могло связывать.
        """
        if not self.charter_mix_ok(chain):
            return False
        # Суд каждой смены цепочки (реестр 2026-08-28, группа А):
        # засеянные смены (уровни 1-2, закрепления, добор) попадают в
        # цепочки мимо генерационного сита — цепочечный судья обязан
        # прогнать каждую через суд смены (счёт посадок + предел
        # рабочего времени); кэш на объекте смены держит цену нулевой
        # для смен собственного перебора.
        if any(not self._duty_ok_cached(d) for d in chain):
            return False
        if (self.conditions is not None
                and self.conditions.has_exact_worktime
                and self.chain_tiers(chain) is None):
            # Точная форма: предел каждой смены зависит от её положения в
            # ротации (акклиматизация) и от класса места отдыха типа ВС;
            # смена сверх продлённого предела не строится ни при каком
            # составе. Проверка нужна и одиночной смене: генерационное
            # сито щедрее позиционного предела.
            return False
        # RULE: R-S6-табл-1-строка-17-кол-3 -- пейринг с вылетом из
        # базы раньше порога UTC завершается ПЕРВЫМ же возвращением в
        # базу: промежуточная посадка в базе (концом смены или внутри
        # смены) запрещена. Судится здесь — единая точка всех путей.
        if self.conditions is not None and chain:
            thr2 = getattr(self.conditions,
                           "base_turn_dep_utc_before_min", None)
            f0 = chain[0]["legs"][0]
            if thr2 is not None and f0["dep"] in self.bases \
                    and f0["t0"].hour * 60 + f0["t0"].minute < thr2:
                if any(d["legs"][-1]["arr"] in self.bases
                       for d in chain[:-1]):
                    return False
                if any(l["arr"] in self.bases
                       for d in chain for l in d["legs"][:-1]):
                    return False
        if self.rules is None or len(chain) < 2:
            return True
        r = self.rules
        if not r.night_sequence_ok(bool(d.get("night")) for d in chain):
            return False
        if not r.dark_sequence_ok(bool(d.get("dark")) for d in chain):
            return False
        for prev, nxt in zip(chain, chain[1:]):
            if prev.get("night") and not r.next_duty_start_allowed(
                    True, self.busy_from(nxt)):
                return False
        return True

    def _c_class_role(self) -> str | None:
        """Роль «с допуском в бизнес-класс» — ИЗ ДАННЫХ, не из кода.

        Зашитое в коде имя у другой авиакомпании не совпадает с её
        собственным написанием роли: на синтетическом наборе UniA
        (приёмка 2026-08-06) кириллическая С в написании роли не
        ловилась детектором «место уже есть», и построитель достраивал
        205 мест ролью, которой нет ни у одного сотрудника, — этап 2 не
        мог их закрыть в принципе. Детекция — по гомоглиф-устойчивому
        суффиксу «-cl» (дефис и cl латинские в обоих написаниях).

        Каналы по порядку: (а) мода ролей с суффиксом «-cl» из спроса
        набора; (б) ключ имени роли узла c_class_attendant_required
        файла условий (написание конкретной а/к живёт в её данных).
        Нет ни того, ни другого — None: выпуск при активном П.1.9
        обязан отказать словами, а не дописать выдуманную роль.
        """
        cached = getattr(self, "_c_class_role_name", None)
        if cached is None:
            roles: Counter = Counter()
            for d in self.demand.values():
                for r, n in d.items():
                    if "-cl" in str(r):
                        roles[str(r)] += n
            if roles:
                cached = roles.most_common(1)[0][0]
            else:
                cached = (getattr(self.conditions, "c_class_role", None)
                          if self.conditions is not None else None)
            self._c_class_role_name = cached
        return cached

    # RULE: 0052 -- ограничение раннего вылета из базы после стыковки
    def transition_ok(self, prev: dict, nxt: dict, station: str) -> bool:
        """Быстрый отсев по паре смен — то из правил последовательности,
        что видно уже на переходе. Полная проверка — в `chain_ok`."""
        if self.rules is None or prev is None:
            return True
        if prev.get("night"):
            if nxt.get("night") and self.rules.night_seq_max <= 1:
                return False
            if not self.rules.next_duty_start_allowed(
                    True, self.busy_from(nxt)):
                return False
        return True

    # RULE: 0095 -- настраиваемый предел длительности пейринга (с перегонами)
    def pairing_days_ok(self, chain, dh_legs) -> bool:
        """Длительность цепочки в сутках вместе с позиционными перелётами.

        Отличается от `pairing_span_ok` тем, что видит перегоны: они
        добавляются к цепочке только в момент её выпуска, а до тех пор
        обход о них не знает.
        """
        if self.rules is None:
            return True
        starts = [self.busy_from(chain[0])] + [l["t0"] for l in dh_legs]
        ends = [self.free_from(chain[-1])] + [l["t1"] for l in dh_legs]
        return self.rules.pairing_days(min(starts), max(ends)) \
            <= self.rules.max_pairing_days

    # RULE: 0095 -- настраиваемый предел длительности пейринга
    def pairing_span_ok(self, chain) -> bool:
        """Длительность цепочки в сутках по местному времени базы.

        Предел в сутках и предел в минутах — разные правила: цепочка из
        шести коротких смен укладывается в минуты, но может занять семь
        календарных суток.
        """
        if self.rules is None:
            return True
        start = chain[0].get("work0") or chain[0]["t0"]
        end = chain[-1].get("work1") or chain[-1]["t1"]
        return self.rules.pairing_days(start, end) <= self.rules.max_pairing_days

    # RULE: none -- перечень запретов эстафеты приходит из настроек вендора и в текущем контуре пуст; при пустом перечне проверка возвращает истину, ограничением не является
    def relay_ok(self, chain, dh_legs=()) -> bool:
        """Цепочка не устраивает эстафету рейсу, которому она запрещена.

        Эстафета — ночёвка вне базы, то есть цепочка более чем из одной
        смены, не возвращающаяся на базу между ними. Правило задаётся
        перечнем номеров рейсов и типов воздушных судов
        (`CosRules.no_relay_rules`) и относится к их СОЧЕТАНИЮ.

        Правило приходит из свода заказчика, а не из настроек вендора:
        ключа для него у вендора нет. Пока поля не существовало, запрет
        нельзя было ни передать, ни проверить машинно — в боевом
        прогоне одной из линий большинство цепочек нарушало его
        незамеченными.
        """
        if self.rules is None or not self.rules.no_relay_rules:
            return True
        # Правило судит о ВЫПОЛНЕНИИ рейса: экипаж, летящий на нём
        # пассажиром, его не выполняет. Поэтому номера берутся только из
        # рабочих участков, а перевозки — нет. Смена здесь словарь с
        # ключом `legs`, а не список участков.
        work = [x for duty in chain for x in duty["legs"]]
        if not self.rules.relay_forbidden([x["fn"] for x in work],
                                          [x["fleet"] for x in work]):
            return True
        # Ночёвка же ищется по ВСЕЙ цепочке, вместе с перевозками:
        # экипаж ночует вне базы и тогда, когда прилетел туда пассажиром
        # или улетает обратно наутро. Проверка «только между сменами»
        # пропускала ровно эти случаи — цепочки из одной смены и цепочки
        # с ночёвкой у края (боевой прогон на наборе заказчика).
        legs = sorted(work + list(dh_legs), key=lambda x: x["t0"])
        need = self.rules.min_off_base_rest()
        for a, b in zip(legs, legs[1:]):
            gap = (b["t0"] - a["t1"]).total_seconds() / 60
            if gap >= need and a["arr"] not in self.bases:
                return False
        return True

    # RULE: 0093 -- предел числа экипажей на одном рейсе перегоном
    def dh_limit_groups(self, cands, vars_, fixed_dh=()):
        """Группы переменных под лимитами перевозки экипажа пассажиром.

        Возвращает пары «список переменных, предел»: сумма выбранных в
        группе не должна превышать предел. Две группы правил:
          * по рейсу — сколько экипажей летит пассажиром одним рейсом
            (места в салоне);
          * по суткам времени базы — сколько перевозок за день.

        Единица счёта — ПЕРЕВОЗКА, то есть пара «цепочка и рейс, которым
        её экипаж летит пассажиром». Цепочка с двумя перевозками в один
        день входит в дневную группу дважды, и это не описка: она и
        занимает два места. Той же единицей считает приёмка.

        Оба правила ограничивают НАБОР выбранных цепочек, а не каждую
        по отдельности: любая цепочка сама по себе их не нарушает.
        Поэтому им место здесь, в модели покрытия, а не в проверке при
        выпуске цепочки.

        fixed_dh — перегоны закреплённых цепочек с кратностью (A5): они
        уже заняли места на рейсе и перевозки дня, предел для свободных
        уменьшается на их число; группа выпускается и при одном
        кандидате, если предел исчерпан. Закрепления сверх предела —
        честная запись переполнения (pin_resource_overflow).
        """
        out: list = []
        r = self.rules
        if r is None:
            return out
        fixed_dh = list(fixed_dh or ())

        def counted(lid):
            """Перевозка попадает под лимит. При счёте «заброски»
            возврат на базу исключён: правило первоисточника говорит о
            ЗАСЫЛКЕ бригад (пример — Москва), а не о возврате домой;
            лимит, применённый к возвратам, делал задачу неразрешимой
            на любом пуле (все пиковые ячейки августа — база BSE)."""
            if getattr(r, "dh_limit_counts", "placements") != "placements":
                return True
            leg = self.legs.get(lid)
            return leg is not None and leg["arr"] not in self.bases

        if getattr(r, "max_dh_per_flight", None) is not None:
            per_flight: dict[str, list] = defaultdict(list)
            for ci, c in enumerate(cands):
                for lid in c.get("dh") or ():
                    if counted(lid):
                        per_flight[lid].append(vars_[ci])
            pinned_flight: Counter = Counter(
                lid for lid in fixed_dh if counted(lid))
            for lid, n_pin in pinned_flight.items():
                if n_pin > r.max_dh_per_flight:
                    self._note_pin_overflow({
                        "kind": "max_dh_per_flight", "flight": lid,
                        "cap": r.max_dh_per_flight, "pinned": n_pin})
            for lid, vs in per_flight.items():
                cap = max(0, r.max_dh_per_flight - pinned_flight.get(lid, 0))
                if len(vs) > cap:
                    out.append((vs, cap))
        if getattr(r, "max_dh_per_day", None) is not None:
            # Гранула суток задаётся правилами, а не выбирается здесь:
            # «по всей сети» и «на направление» — разные задачи, и
            # источник между ними не выбрал (см. `dh_day_scope`).
            by_dest = getattr(r, "dh_day_scope", "network") == "destination"
            per_day: dict = defaultdict(list)
            off = timedelta(minutes=r.base_tz_offset_min)
            def day_key(lid):
                leg = self.legs.get(lid)
                if leg is None or not counted(lid):
                    return None
                day = (leg["t0"] + off).date()
                return (day, leg["arr"]) if by_dest else day

            for ci, c in enumerate(cands):
                for lid in c.get("dh") or ():
                    k = day_key(lid)
                    if k is not None:
                        per_day[k].append(vars_[ci])
            pinned_day: Counter = Counter(
                k for k in (day_key(lid) for lid in fixed_dh)
                if k is not None)
            for k, n_pin in pinned_day.items():
                if n_pin > r.max_dh_per_day:
                    self._note_pin_overflow({
                        "kind": "max_dh_per_day", "day": str(k),
                        "cap": r.max_dh_per_day, "pinned": n_pin})
            for k, vs in per_day.items():
                cap = max(0, r.max_dh_per_day - pinned_day.get(k, 0))
                if len(vs) > cap:
                    out.append((vs, cap))
        return out

    # RULE: 0094 0060 -- перегон рейсом другого перевозчика; наземных перемещений во входе нет
    def dh_options(self, frm: str, to: str, not_before: datetime,
                   not_after: datetime, k: int = 2,
                   latest: bool = False, *,
                   sink=None) -> list[list[dict]]:
        """Перегон пассажиром: прямой рейс либо с одной пересадкой.

        Приёмник усечений `sink` — параметр ОБЯЗАТЕЛЬНЫЙ по смыслу и
        именованный по форме: прежде каждый из шести вызывающих сам решал,
        записывать ли усечение по длине результата, и два из шести этого
        не делали (внешний аудит R9, P0-01 и его сосед — начальный перегон
        к первой смене). Теперь усечение записывает сам отбор, в момент
        наблюдения отброшенного варианта. `sink=None` допустим только для
        зонда существования, где смысл вызова — «есть ли хоть один», и
        усечение по определению безразлично. Доказательством
        невозможности отрицательный ответ зонда НЕ является (аудит A4,
        04.09.2026): `classify_uncovered` судит достижимость обходом
        графа рейсов (`station_reachability`), а не этим зондом.

        Пересадочные варианты рассматриваются ТОЛЬКО когда прямых не
        нашлось вовсе. Это осознанное усечение пространства (докстрока
        прежде утверждала обратное — «оба вида ранжируются вместе», — что
        коду не соответствовало). Поведение сохранено, но факт теперь
        записывается: подавление целого класса вариантов не должно быть
        невидимым для сертификата.
        """
        pool: list[list[dict]] = []
        src = self.by_route.get((frm, to), ())
        direct = _pc.take(
            (l for l in (reversed(src) if latest else src)
             if l["t0"] >= not_before and l["t1"] <= not_after),
            k, sink=sink, name="dh_options")
        pool.extend([l] for l in direct)
        # Полный режим (доказательная ступень эскалации): пересадочные
        # перебираются ДАЖЕ при живых прямых — иначе запись о
        # подавлении честно отменяет доказательство исчерпания.
        full = bool(getattr(self.args, "dh_full_transfers", False))
        if not pool or full:
            src1 = self.by_dep_leg.get(frm, ())
            hops: list[list[dict]] = []
            for l1 in (reversed(src1) if latest else src1):
                if not (not_before <= l1["t0"]) or l1["t1"] > not_after \
                        or l1["arr"] == to:
                    continue
                first2 = None
                for l2 in self.by_route.get((l1["arr"], to), ()):
                    gap = (l2["t0"] - l1["t1"]).total_seconds() / 60
                    if 60 <= gap <= 1440 and l2["t1"] <= not_after:
                        if first2 is None:
                            # первый подходящий стыковочный рейс, а не
                            # лучший: зашитое сужение
                            first2 = l2
                            continue
                        # ВТОРОЙ допустимый существует — сужение «первый,
                        # а не лучший» доказано наблюдением (аудит R10,
                        # опровержение INV-PRICE-01): прежний break прятал
                        # альтернативу, и порядок массива менял
                        # математический ответ без следа в сертификате.
                        if sink is not None:
                            sink.record("dh_transfer_first_only", 1, 2)
                        break
                if first2 is not None:
                    hops.append([l1, first2])
                if len(hops) > k:
                    break
            taken = _pc.take(hops, k, sink=sink, name="dh_options")
            # в полном режиме прямые уже в pool — пересадки добавляются
            pool = pool + taken if full else taken
        elif sink is not None and self.by_dep_leg.get(frm):
            # прямые нашлись — пересадочные не рассматривались вовсе
            sink.record("dh_transfers_suppressed", 0, 1)
        pool.sort(key=lambda o: o[-1]["t1"], reverse=latest)
        return pool

    # ---- пояса станций на момент (внешний аудит 04.09.2026, A9) ----
    def station_off(self, code: str, when: datetime | None = None):
        """Смещение пояса станции (минуты к UTC) НА МОМЕНТ when по
        IANA-зоне листа «Аэропорты»; None — пояса в данных нет (станция
        дальней не считается — молчаливое ужесточение недопустимо).
        when=None — смещение первого касания (как прежде)."""
        zone = self.tz_zone_of.get(code)
        if zone is None or when is None:
            return self.tz_off_of.get(code)
        d = when.replace(tzinfo=timezone.utc).astimezone(zone).utcoffset()
        return None if d is None else int(d.total_seconds() // 60)

    def base_zone_off(self, when: datetime | None = None):
        """Смещение ПОЯСА базы на момент — по листу «Аэропорты» (первая
        по алфавиту база с поясом). Это не сдвиг шкалы меток контракта
        (base_tz_offset_min): разница поясов станции и базы (дальняя
        стоянка, акклиматизация) не зависит от того, в какой шкале
        записаны метки. Листа нет — сдвиг шкалы контракта, как прежде."""
        for b in sorted(self.bases):
            off = self.station_off(b, when)
            if off is not None:
                return off
        return (self.rules.base_tz_offset_min
                if self.rules is not None else None)

    def nights_of(self, leg_lists) -> int:
        days = {(l["t0"] + self.tz).date() for ls in leg_lists for l in ls}
        return max(0, (max(days) - min(days)).days) if days else 0

    # ---- ОДНА цена цепочки (внешний аудит 04.09.2026, A6): критерий +
    # Σ мера × вес × состав по мягким правилам части «Пейринг». До A6
    # штрафы мягких правил жили только в ветке коэффициентов: при
    # критерии 0096 и при ставках источника они молча выпадали из цены,
    # а ночь считалась двумя определениями (построитель — max(1, …),
    # сертификатор — «тот же день = 0»). Меры и композиция — в
    # vmp/chain_price, тем же модулем сертификатор пересчитывает меры
    # независимо и сверяет их с расшифровкой цены в каталоге.
    def soft_rules(self) -> list[dict]:
        """Мягкие правила части из ключей запуска (семья, вес, параметры).
        При ставках источника ночёвка оплачена внутри критерия (таблица
        ставок стоянок), и семья layover_penalty второй раз в цену не
        входит — строка остаётся с весом 0, чтобы это было видно."""
        cached = getattr(self, "_soft_rules_cache", None)
        if cached is not None:
            return cached
        a = self.args
        w_night = 0 if (self.source_cost and not a.cost_0096) else a.w_night
        rules = _cp.rules_from_weights(
            w_night=w_night or 0,
            w_weekend_night=getattr(a, "w_weekend_night", 0) or 0,
            weekend_days=getattr(a, "weekend_days", ()) or (),
            w_long_duty=getattr(a, "w_long_duty", 0) or 0,
            long_duty_threshold=getattr(a, "long_duty_threshold", None),
            base_duty_threshold=getattr(a, "duty_span", None),
            rule_ids=_cp.parse_rule_ids(getattr(a, "soft_rule_ids", None)))
        self._soft_rules_cache = rules
        return rules

    def price_periods(self, chain, dh_legs=()) -> list:
        """Периоды для цены: каждая смена целиком, каждый перегон — своим
        периодом. Ночь между перегоном и сменой на разных датах —
        настоящая гостиничная ночь; тот же день — нет; непрерывная смена
        через полночь ночи не создаёт (единое определение ночи)."""
        groups = [list(d["legs"]) for d in chain]
        groups += [[l] for l in (dh_legs or ())]
        return _cp.periods_from_legs([g for g in groups if g])

    def price(self, chain, dh_legs=(), crew: int = 1, *,
              open_end: bool = False) -> dict:
        """ЕДИНАЯ цена цепочки для всех точек ценообразования (выпуск
        кандидата, оценка колонок, нижняя оценка, закреплённые цепочки):
        критерий ветки (0096 / ставки источника / коэффициенты) + Σ мера
        × вес × состав по мягким правилам части. Возвращает расшифровку
        vmp.chain_price.price: cost (известная часть, known_subtotal),
        criterion, nights, soft[], область учёта и определённость
        (certainty, unknown_events, full_cost). Ветки критерия и
        пространство поиска не меняются. У анонимной цепочки нет ни
        выведенного дома, ни наблюдаемого продолжения: open_end —
        аргумент совместимости, не доказательство нижней или полной
        оценки (хвост цепочки и без него не начисляется)."""
        dh_legs = list(dh_legs or ())
        a = self.args
        if a.cost_0096:
            crit = self.cost_0096(chain, dh_legs, crew)
            kind = "0096"
        elif self.source_cost:
            crit, _s, _u = self.chain_cost(chain, dh_legs, crew)
            kind = "source_rates"
        else:
            crit = (a.w_dh * len(dh_legs) + a.w_chain
                    + a.w_duty * len(chain)
                    + a.w_span_day * self.span_days(chain, dh_legs)) * crew
            kind = "weights"
        # Контракт Б (план 05.09.2026, согласован с аудитором): цена
        # цепочки учитывает только ДОКАЗАННЫЕ внутренние ночёвки по
        # определению ВМП; отдых на любой базе (приписка экипажа
        # неизвестна) и открытый конец — неопределённый вклад, он едет в
        # расшифровке отдельно (price["uncertain"] / unknown_events) и не
        # объявляется нулём. База старта цепочки базой приписки не
        # считается. Область учёта — универсум приписок людей
        # постановки и его полнота (P-425), не базы поиска.
        return _cp.price(crit, self.price_periods(chain, dh_legs),
                         self.soft_rules(), self.price_eligible_bases,
                         self.tz, crew, open_end=open_end, criterion_kind=kind,
                         eligible_bases_complete=self.price_eligible_bases_complete)

    # RULE: 0096 -- стоимость по рангу: полётное время на ставку полётного, рабочее на ставку рабочего
    def span_days(self, chain, dh_legs=()) -> int:
        """Календарные дни командировки: от первого вылета до последнего
        прилёта (перегоны включаются — человек в отъезде и на них)."""
        ts = [l["t0"] for d in chain for l in d["legs"]]
        te = [l["t1"] for d in chain for l in d["legs"]]
        for l in dh_legs or ():
            ts.append(l["t0"]); te.append(l["t1"])
        if not ts:
            return 0
        return ((max(te) + self.tz).date() - (min(ts) + self.tz).date()).days + 1

    # RULE: 0096 -- стоимость по рангу: ставки полёта и работы — параметрами из пакета (NORMALIZED_COST)
    def cost_0096(self, chain: list[dict], dh_legs, crew: int) -> int:
        """Нормированная стоимость по рангам в деци-норм-минутах.

        Обезличенный шаблон (принцип владельца 2026-08-29): времена —
        по перелётам (рабочий перелёт входит и в полётное, и в
        рабочее время; перегон — только в рабочее), ранги
        равноправны, итог умножается на численность состава. СТАВКИ —
        параметры запуска, не константы кода: деци-коэффициент
        рабочей минуты перелёта = 10*(ставка_полёта + ставка_работы),
        перегонной = 10*ставка_работы; их считает исполнитель
        критерия из блока rates пакета (vmp/criteria). Целые
        деци-единицы не убывают при расширении цепочки — нижняя
        оценка генерации колонок остаётся обоснованной.
        """
        fly = sum(int((l["t1"] - l["t0"]).total_seconds() // 60)
                  for d in chain for l in d["legs"])
        dh = sum(int((l["t1"] - l["t0"]).total_seconds() // 60)
                 for l in dh_legs or ())
        # Чтение прямое, без числовых умолчаний: метод вызывается только
        # в ветке cost_0096, где гейт __init__ гарантирует оба значения.
        cw = int(self.args.cost_rank_deci_work)
        cd = int(self.args.cost_rank_deci_dh)
        return (cw * fly + cd * dh) * crew

    def chain_cost(self, chain: list[dict], dh_legs: list[dict],
                   crew: int) -> tuple[float, int, int]:
        """Стоимость цепочки в цели системы-источника.

        Обе ставки — ДАННЫЕ, а не наши коэффициенты:
          * перелёт экипажа пассажиром — цена самого участка. Сумма по
            решению источника воспроизводит его метрику ТОЧНО, до
            единицы;
          * стоянка вне базы — цена станции, две ночи за стоянку. Так
            воспроизводится 99,3 % гостиничной части и 99,91 % цели
            целиком. Небольшой остаток не объяснён и вынесен в вопросы
            заказчику.

        Станция без ставки (у источника это сторожевое число «неизвестная
        станция») в стоимость не входит и считается отдельно: сторожевое
        число в цели выглядело бы запретом летать туда вовсе.

        Возвращает (стоимость, стоянок, стоянок без ставки).
        """
        cost = 0.0
        stops = unknown = 0
        for prev, nxt in zip(chain, chain[1:]):
            st = prev["arr"]
            if st in self.bases:
                continue
            stops += 1
            rate = self.layover_cost_of.get(st)
            if rate is None:
                unknown += 1
            elif self.args.nights_per_layover > 0:
                cost += rate * self.args.nights_per_layover
            else:
                # --nights-per-layover 0: по ФАКТИЧЕСКИМ календарным
                # ночам стоянки (худший для нас вариант вопроса № 8 —
                # наши стоянки длиннее, чем у источника). Ночь — переход
                # календарной даты в поясе базы между прилётом и вылетом.
                nights = max(1, ((nxt["t0"] + self.tz).date()
                                 - (prev["t1"] + self.tz).date()).days)
                cost += rate * nights
        for l in dh_legs:
            cost += self.dh_cost_of.get(l["id"], self.args.w_dh)
        return cost * crew, stops * crew, unknown * crew

    # RULE: 0031 0032 0033 -- состав лётного экипажа: минимальный, увеличенный, расширенный
    def crew_size(self, chain, tiers=None) -> int:
        """Численность экипажа цепочки.

        Спрос участка — нижняя граница: столько людей нужно, чтобы рейс
        вылетел. Но если НАША смена длиннее и по правилам источника
        требует усиленного или двойного состава, людей нужно больше — и
        платить за них тоже.

        Прежде бралcя только спрос участка, а он взят из файла заказчика
        и несёт ЕГО решения о составе: участок, который он вёз усиленным
        экипажем, приносит лишнее место даже нашей короткой смене, а наша
        длинная смена лишнего места не получала вовсе. На наборе
        заказчика второе перевешивало: 315 цепочек из 1352
        недосчитывали 495 человеко-мест, и цель оказалась занижена на
        8,9 %.
        """
        # Спрос цепочки — не максимум одного участка, а сумма помест­ных
        # максимумов РОЛЕЙ: старший бортпроводник одного участка и
        # бизнес-класс другого не взаимозаменяемы, экипаж везёт обоих всю
        # командировку. Считать по максимуму участка — оплатить 4 мест,
        # пообещав этапу 2 пять (22 такие цепочки в каталоге 2026-07-28).
        need: Counter = Counter()
        for d in chain:
            for l in d["legs"]:
                for role, cnt in self.role_count_of_leg.get(
                        l["id"], _ONE_SEAT).items():
                    if cnt > need[role]:
                        need[role] = cnt
        demand = sum(need.values()) or 1
        if self.rules is None:
            return demand
        if self.conditions is not None:
            # Стандартный состав по типу ВС из условий заказчика (П.1.6):
            # он БАЗА планирования, а не выведенная из данных величина.
            # Берётся наибольший по типам судов цепочки — экипаж везёт её
            # целиком, и урезать состав на части участков нельзя. Точная
            # ярусная форма добавляет к составу человека на цепочку, где
            # хоть одна смена идёт продлённым пределом (расширенный
            # состав с отдыхом в полёте, прил. 6).
            base = max((self.conditions.crew_std(l["fleet"])
                        for d in chain for l in d["legs"]), default=1)
            tier = 0
            if self.conditions.has_exact_worktime:
                # Ярусы приходят от вызывающего, когда перегоны уже
                # выбраны (periods_tiers): период с перегоном длиннее
                # рабочей части, и состав считается по нему.
                ts = tiers if tiers is not None else self.chain_tiers(chain)
                tier = max(ts) if ts else 0
            # Ярус — добавка к ФАКТИЧЕСКОМУ составу, а не к стандартному:
            # `max(demand, base + tier)` терял человека яруса всякий раз,
            # когда стандартный состав типа не задан условиями (base 0)
            # или равен спросу — поймано тестом на смене продлённого
            # предела без записи crew_std.
            return max(demand, base) + tier
        # Обычный состав типа судна — самый частый в спросе; надбавка по
        # ярусу нашей смены. Ярус смены уже посчитан по рабочему времени.
        base = max((self.std_crew_of_fleet.get(l["fleet"], 1)
                    for d in chain for l in d["legs"]), default=1)
        tier = max((d.get("tier") or 0 for d in chain), default=0)
        return max(demand, base + tier)

    # --- границы, от которых отсчитывается отдых -------------------------
    # Отдых по правилам источника отмеряется от конца РАБОЧЕГО времени до
    # начала следующего, а не от прилёта до вылета: между ними лежат
    # дебрифинг и брифинг, до 160 минут вместе. Считая по рейсам, расчёт
    # недосчитывал отдых — аудит построенного (2026-07-27) нашёл 172
    # таких перехода в 143 цепочках из 1430.
    # RULE: 0021 -- момент освобождения экипажа: окончание последнего сектора плюс дебрифинг
    def free_from(self, duty: dict) -> datetime:
        return duty.get("work1") or duty["t1"]

    # RULE: 0020 -- момент занятия экипажа: начало первого сектора минус брифинг
    def busy_from(self, duty: dict) -> datetime:
        return duty.get("work0") or duty["t0"]

    # --- перечисление кандидатов ---------------------------------------
    # RULE: 0084 0097 -- цепочка начинается и заканчивается в базе
    def emit(self, chain: list[dict], dh_legs: list[dict]) -> dict | None:
        key = (tuple(d["i"] for d in chain),
               tuple(sorted(x["id"] for x in dh_legs)))
        if key in self.candidates:
            return None
        # Правила ПОСЛЕДОВАТЕЛЬНОСТИ проверяются на готовой цепочке: их
        # нельзя решить по паре смен. Проверка стоит здесь, в единственной
        # точке выпуска кандидата, — тогда её нельзя обойти ни широким
        # проходом, ни генерацией колонок.
        if not self.chain_ok(chain):
            return None
        # Длительность цепочки в сутках считается ВМЕСТЕ с позиционными
        # перелётами: у источника они входят в смену, и мерить только по
        # рабочим сменам значило бы мерить другое. На наборе это ровно 11
        # цепочек, которые по рабочим сменам укладывались в шесть суток,
        # а вместе с перегонами занимали семь.
        if not self.pairing_days_ok(chain, dh_legs):
            return None
        if not self.relay_ok(chain, dh_legs):
            return None
        # П.1.1.5 обязан видеть и перегоны: правило запрещает выполнять
        # чартером перегоны, но единственная проверка стояла в chain_ok
        # БЕЗ dh-плечей — перегон, добавленный при выпуске, обходил её
        # (противниковая проверка 2026-08-06).
        if not self.charter_mix_ok(chain, extra_legs=dh_legs):
            return None
        # Перегон, не отделённый нормативным отдыхом, продолжает период
        # обязанностей: предел меряется всем периодом (0047/0048/0050/
        # 0051). Проверка стоит здесь, где цепочка выпускается вместе с
        # перегонами, — до неё периода с перегонами просто не существует.
        tiers = self.periods_tiers(chain, dh_legs)
        if tiers is None:
            return None
        # Между соседними перегонами одной смены — не менее двух часов
        # (правило 0055, параметр 0026). Правило значилось «проверяется
        # приёмкой», но приёмка судит уже готовый план: соблюдать его
        # обязан расчёт (аудит передаваемого файла 2026-08-25).
        if not self.adjacent_dh_ok(chain, dh_legs):
            return None
        crew = self.crew_size(chain, tiers)
        pr = self.price(chain, dh_legs, crew)     # A6: одна цена
        cost, n = pr["cost"], pr["nights"]
        rec = {"duties": [d["i"] for d in chain],
               "dh": sorted(x["id"] for x in dh_legs), "cost": cost,
               "nights": n, "crew": crew, "price": pr,
               "legs": [l["id"] for d in chain for l in d["legs"]]}
        self.candidates[key] = rec
        return rec

    # RULE: 0016 0059 0064 0066 0067 0068 0084 0097 -- обход: базовая станция сети, стыковки ЧЛЭ, единый тип ВС, эквивалентные аэропорты
    def _grow(self, duties, per_duty: int, dh_k: int,
              skip: set[int], deadline: float | None = None) -> None:
        """Обход цепочек от заданных смен. Вынесен отдельно, потому что
        нужен дважды: обычным широким проходом по всем сменам и узким
        спасательным — по сменам плеч, которым кандидатов не досталось.
        `dh_k` — сколько вариантов перегона пассажиром рассматривать:
        при единице одна неудачная дорога домой хоронит плечо целиком.
        `deadline` — предел времени обхода (monotonic): по истечении обход
        останавливается, а `report["enumeration_time_limited"]` встаёт в
        истину — усечение пула обязано быть видно в отчёте."""
        import time as _time
        a = self.args
        # Приёмник усечений РАЗОГРЕВА ПУЛА — намеренно отдельный от
        # приёмника прохода ценообразования. Пул почти всегда усечён (в
        # этом и смысл эвристики), но границу доказывает не он, а последний
        # проход ценообразования. Слить их в один приёмник значило бы не
        # публиковать границу никогда; потерять различие — публиковать её
        # незаслуженно. Оба исхода — дефект.
        pool_sink = _pc.Truncations(_pc.SCOPE_POOL)
        self._last_pool_truncations = pool_sink
        # Живые вехи перебора (пакет улучшений кнопки, пункт (в);
        # урок застоя: немая фаза по журналу неотличима от
        # зависшей). Раз в интервал печатается веха с размером
        # пула — экран Конвейера читает их из хвоста журнала.
        _pe = getattr(a, "pulse_every_s", None)
        pulse_every = float(_pe) if _pe else 60.0
        self._pulse_next = _time.monotonic() + pulse_every

        def stations_from(station):
            """Станции, с которых экипаж может начать следующую смену:
            та же и равнозначные ей. Равнозначность — правило источника
            (`stationEquivalenceRules`), а не вольность: в его решении ВСЕ
            83 смены станции между сменами пришлись на эти пары."""
            if self.rules is None:
                return ((station, 0),)
            out = [(station, 0)]
            # None = «пары не заявлены» — механизм спит (отсутствие пар
            # только ужесточает: станции не равнозначны).
            for (frm, to), gap in (self.rules.teleportation or {}).items():
                if frm == station:
                    out.append((to, gap))
            return out

        def successors(station, avail, need, t0, prev):
            lim = t0 + timedelta(minutes=a.pairing_span)
            out, ret = [], None
            for st, tel_gap in stations_from(station):
                for d in self.by_dep.get(st, ()):
                    if d["i"] in skip:
                        continue
                    if self.busy_from(d) < avail + timedelta(
                            minutes=max(need, tel_gap)):
                        continue
                    if d["t1"] > lim:
                        break
                    if not self.transition_ok(prev, d, station):
                        continue
                    out.append(d)
                    if ret is None and d["arr"] in self.bases:
                        ret = d
                if len(out) >= a.branch_chain and ret is not None:
                    break
            out.sort(key=lambda d: d["t0"])
            picked = out[:a.branch_chain]
            if ret is not None and ret not in picked:
                picked.append(ret)
            return picked

        def walk(chain, station, avail, t0, pre=(), per=None):
            now_m = _time.monotonic()
            if deadline is not None and now_m > deadline:
                self.report["enumeration_time_limited"] = True
                raise TimeoutError
            if now_m >= self._pulse_next:
                self._pulse_next = now_m + pulse_every
                left = ("" if deadline is None else
                        ", бюджета осталось %d с" % max(
                            0, int(deadline - now_m)))
                print("веха [%s] перебор жив: кандидатов %d%s"
                      % (_time.strftime("%H:%M:%S"),
                         len(self.candidates), left), flush=True)
            if per is not None and per["n"] >= per_duty:
                return
            if station in self.bases:
                if self.emit(chain, list(pre)) is not None and per:
                    per["n"] += 1
                return
            lim = t0 + timedelta(minutes=a.pairing_span)
            for home in self.bases:
                for dh in self.dh_options(
                        station, home,
                        avail + timedelta(minutes=self.dh_board_min()),
                        lim, dh_k, sink=pool_sink):
                    if self.emit(chain, list(pre) + dh) is not None and per:
                        per["n"] += 1
            if len(chain) >= a.depth:
                # Обрыв пула по глубине — с тем же свидетелем пространства,
                # что у ценообразования (аудит R10): пул границу не
                # подавляет, но молчаливых обрывов не имеет.
                if any(self.by_dep.get(s) for s, _ in
                       stations_from(station)):
                    pool_sink.record("depth", a.depth, a.depth + 1)
                return
            need = self.rest_after(chain[-1], station)
            for nxt in successors(station, avail, need, t0, chain[-1]):
                if not self.pairing_span_ok(chain + [nxt]):
                    continue
                walk(chain + [nxt], nxt["arr"], self.free_from(nxt), t0,
                     pre, per)

        try:
            for d in duties:
                if d["i"] in skip:
                    continue
                per = {"n": 0}
                if d["dep"] in self.bases:
                    walk([d], d["arr"], self.free_from(d), d["t0"], (), per)
                else:
                    for home in self.bases:
                        for dh_out in self.dh_options(
                                home, d["dep"],
                                d["t0"] - timedelta(minutes=a.pairing_span),
                                self.pre_dh_latest(d),
                                dh_k, latest=True, sink=pool_sink):
                            if not self.head_dh_ok(d, dh_out):
                                continue
                            walk([d], d["arr"], self.free_from(d),
                                 dh_out[0]["t0"], tuple(dh_out), per)
        except TimeoutError:
            print("ВНИМАНИЕ: перебор остановлен по --budget-s — пул "
                  "усечён, что записано в отчёт "
                  "(enumeration_time_limited)", flush=True)

    def _minimize_core(self, core_idx, resolve, extract,
                       infeasible_status):
        """Минимизация ядра неразрешимости: core-of-core до
        неподвижной точки, затем удаление по одному. Два предела:
        бюджет вызовов (--core-min-budget: 0 — сырое ядро) и стена
        --core-min-wall-s (пакет улучшений кнопки, пункт (б)): бюджет
        режет ЧИСЛО перерешиваний, но не время — на патологиях
        решателя каждый вызов длится до своего полного лимита, и
        диагностика съедает часы. Попадание в стену — в отчёт
        (core_min_wall_hit); частично минимизированное ядро законно.

        `resolve(assum, limit_s)` -> (status, solver) и
        `extract(solver)` -> list внедряются вызывающим: боевой путь
        подаёт перерешивание CP-SAT, тесты — модель отказов."""
        core_budget = getattr(self.args, "core_min_budget", 60)
        wall_s = getattr(self.args, "core_min_wall_s", 300)
        wall_dl = (_now_s() + wall_s) if wall_s else None
        self.report["core_min_wall_hit"] = False

        def _wall_left():
            return None if wall_dl is None else wall_dl - _now_s()

        def _limit():
            left = _wall_left()
            return 30.0 if left is None else max(1.0, min(30.0, left))

        def _wall_stop(where):
            left = _wall_left()
            if left is None or left > 0:
                return False
            if not self.report["core_min_wall_hit"]:
                self.report["core_min_wall_hit"] = True
                import time as _tm4
                print("веха [%s] минимизация ядра: настенный предел "
                      "%d с достигнут (%s) — ядро частично "
                      "минимизировано, %d предположений"
                      % (_tm4.strftime("%H:%M:%S"), wall_s, where,
                         len(core_idx)), flush=True)
            return True

        for _ in range(6 if core_budget > 0 else 0):  # core-of-core
            if _wall_stop("этап core-of-core"):
                return core_idx
            st2, s2 = resolve(core_idx, _limit())
            if st2 != infeasible_status:
                break
            new_idx = list(extract(s2))
            if len(new_idx) >= len(core_idx):
                break
            core_idx = new_idx
        budget = core_budget                         # deletion-минимизация
        for lit in list(core_idx):
            if budget <= 0 or len(core_idx) <= 2:
                break
            if _wall_stop("этап удаления по одному"):
                break
            trial = [x for x in core_idx if x != lit]
            st2, s2 = resolve(trial, _limit())
            budget -= 1
            if st2 == infeasible_status:
                core_idx = trial
        return core_idx

    def enumerate_candidates(self, skip: set[int]) -> None:
        # Веха этапа печатается сразу, а не в конце прогона: по журналу
        # должно быть видно, где расчёт находится и жив ли он.
        print("этап: перебор цепочек от %d смен, до %d с..."
              % (len(self.duties), self.args.budget_s), flush=True)
        t0 = _now_s()
        # `--budget-s` — настоящий предел перебора. Прежде ключ никак не
        # действовал (использовался лишь как запасное значение времени
        # выбора), но записывался в отпечаток постановки: прогон
        # «с удвоенным бюджетом» строил байт-в-байт тот же пул, а читатель
        # отпечатка думал, что поиск был вдвое шире (вскрыто 2026-07-28).
        self.report["enumeration_time_limited"] = False
        self._grow(self.duties, self.args.per_duty, self.args.dh_options,
                   skip, deadline=_deadline_of(self.args, self.args.budget_s))
        self.report["candidates"] = len(self.candidates)
        print("этап: перебор готов — кандидатов %d (%.0f с)"
              % (len(self.candidates), _now_s() - t0), flush=True)

    def orphan_legs(self) -> list[str]:
        """Плечи, которым не досталось ни одной цепочки-кандидата.

        Ради них и существует спасательный проход: пока у плеча нет
        кандидата, ограничение на его покрытие вообще не создаётся —
        решатель отчитается об оптимуме на облегчённой задаче, а плечо
        молча останется без экипажа."""
        covered = {lid for c in self.candidates.values() for lid in c["legs"]}
        # Закреплённые участки покрыты данностью — фиксированными
        # цепочками, которых нет среди кандидатов.
        covered |= self._pin_active
        return [lid for lid in self.legs
                if lid not in covered and lid not in self.transport_only]

    def rescue_orphans(self, skip: set[int]) -> None:
        """Второй проход только по сменам осиротевших плеч: шире выбор
        перегонов пассажиром, отдельный лимит на смену. Стоит дёшево —
        осиротевших плеч единицы, — а закрывает главную дыру в отчёте."""
        import time as _time
        a = self.args
        orphans = self.orphan_legs()
        self.report["orphans_before_rescue"] = len(orphans)
        if orphans and a.rescue_dh > 0:
            wanted, duties = set(orphans), []
            for d in self.duties:
                if d["i"] not in skip and any(l["id"] in wanted
                                              for l in d["legs"]):
                    duties.append(d)
            # первый заход — обычным обходом, шире по перегонам
            self._grow(duties, a.rescue_per_leg, a.rescue_dh, skip)
            if self.orphan_legs():
                # второй — ценообразующим обходом без отсева по стоимости:
                # он ходит иначе (ранжирует продолжения, а не обрывается на
                # первом возврате на базу) и достаёт то, что первый не видит
                rr = self.price_pass({}, max(a.cg_top_k, 1000),
                                     _deadline_of(a, a.rescue_s), skip,
                                     accept_all=True, only_duties=duties,
                                     per_duty=a.rescue_per_leg)
                # Спасательный проход — не сертифицирующий, но его
                # усечения не выбрасываются (аудит R9 D2/D3): перечень
                # уезжает в отчёт.
                self.report.setdefault("rescue_truncations", []).extend(
                    rr.truncation_rows())
            if self.orphan_legs():
                # третий — СО СТОРОНЫ ПРЕДШЕСТВЕННИКОВ. Смена сироты может
                # начинаться на станции, куда перегона нет вовсе, — туда
                # можно только прилететь РАБОТАЯ и отдохнуть. Оба захода
                # выше стартуют от смены сироты и этого пути не видят.
                # Обход от смен, прилетающих на станции сирот, дёшев:
                # таких смен десятки.
                rest_orphans = set(self.orphan_legs())
                deps = {d["dep"] for d in self.duties
                        if any(l["id"] in rest_orphans for l in d["legs"])}
                preds = [d for d in self.duties
                         if d["i"] not in skip and d["arr"] in deps
                         and not any(l["id"] in rest_orphans
                                     for l in d["legs"])]
                if preds:
                    rr = self.price_pass({}, max(a.cg_top_k, 1000),
                                         _deadline_of(a, a.rescue_s),
                                         skip, accept_all=True,
                                         only_duties=preds,
                                         per_duty=a.rescue_per_leg)
                    self.report.setdefault("rescue_truncations", []).extend(
                        rr.truncation_rows())
            self.report["candidates"] = len(self.candidates)
        rest = self.orphan_legs()
        self.report["orphans_after_rescue"] = len(rest)
        self.report["rescued_legs"] = len(orphans) - len(rest)
        if orphans:
            print("этап: спасательный проход — осиротевших было %d, "
                  "осталось %d" % (len(orphans), len(rest)), flush=True)

    def escalate_orphans(self, skip: set[int]) -> None:
        """Цикл эскалации по осиротевшим плечам ДО РАЗВЯЗКИ (поручение
        владельца 2026-08-28): спасательный проход — одна попытка
        фиксированной силы; эскалация наращивает силу ступенями, пока
        каждый сирота либо не ПОКРЫТ, либо невозможность не ДОКАЗАНА
        исчерпанием пространства, либо честно «бюджет исчерпан».

        Ступени: (1) целевой проход от смен сирот с учетверённой шириной
        перегонов и ветвления; (2) то же от предшественников (смен,
        прилетающих на станции сирот); (3) доказательная: полный обход
        окна размаха командировки вокруг сироты БЕЗ эвристических капов —
        завершился без усечений и без срыва по времени, а сирота не
        покрыт, значит законной цепочки с ним НЕ СУЩЕСТВУЕТ (носитель —
        счётчики обхода). Любое усечение или срыв по времени доказательство
        честно отменяют (перечень — в отчёт).

        Бюджет фазы — args.escalate_s (0 = фаза выключена, прежнее
        поведение); вехи каждой ступени — в журнал печатью."""
        import time as _time
        a = self.args
        budget = getattr(a, "escalate_s", 0) or 0
        orphans0 = self.orphan_legs()
        if not budget or not orphans0:
            return
        # Детерминированный режим (Д1): эскалация без стенных часов —
        # предел бесконечен, дедлайны волн отсутствуют.
        det = _deterministic(a)
        t_end = float("inf") if det else _time.monotonic() + budget
        esc: dict = {"budget_s": budget, "steps": [],
                     "proved": [], "covered": [], "budget_hit": [],
                     "deterministic": det}

        def _wave(tag, only, per_leg, dh_k, branch, depth_add,
                  deadline):
            saved = (a.dh_options, a.branch_chain, a.depth)
            a.dh_options, a.branch_chain = dh_k, branch
            a.depth = saved[2] + depth_add
            try:
                return self.price_pass({}, max(a.cg_top_k, 1000),
                                       deadline, skip, accept_all=True,
                                       only_duties=only,
                                       per_duty=per_leg)
            finally:
                a.dh_options, a.branch_chain, a.depth = saved

        def _duties_of(wanted):
            return [d for d in self.duties if d["i"] not in skip
                    and any(l["id"] in wanted for l in d["legs"])]

        # --- ступени 1-2: наращивание силы по всем сиротам разом -------
        for step, (per_mul, dh_k, br_mul, dep_add) in enumerate(
                ((4, max(a.dh_options * 4, 8), 2, 1),
                 (8, max(a.dh_options * 8, 16), 4, 2)), start=1):
            rest = set(self.orphan_legs())
            if not rest or _time.monotonic() >= t_end:
                break
            duties = _duties_of(rest)
            deps = {d["dep"] for d in duties}
            preds = [d for d in self.duties if d["i"] not in skip
                     and d["arr"] in deps and d not in duties]
            share = None if det else min(t_end, _time.monotonic()
                                         + max(30.0, budget / 4))
            _wave("сироты", duties, a.rescue_per_leg * per_mul, dh_k,
                  a.branch_chain * br_mul, dep_add, share)
            if self.orphan_legs():
                _wave("предшественники", preds,
                      a.rescue_per_leg * per_mul, dh_k,
                      a.branch_chain * br_mul, dep_add,
                      None if det else min(t_end, _time.monotonic()
                                           + max(30.0, budget / 4)))
            now = set(self.orphan_legs())
            covered = sorted(rest - now)
            esc["covered"].extend(covered)
            esc["steps"].append({"step": step, "orphans": len(rest),
                                 "covered": covered})
            print("веха [%s] эскалация: ступень %d — сирот %d, "
                  "покрыто %d"
                  % (_time.strftime("%H:%M:%S"), step, len(rest),
                     len(covered)), flush=True)

        # --- ступень 3: доказательная, по одному сироте ----------------
        for leg_id in sorted(set(self.orphan_legs())):
            left = t_end - _time.monotonic()
            if left <= 5:
                esc["budget_hit"].append(leg_id)
                continue
            leg = self.legs[leg_id]
            lo = leg["t0"] - timedelta(minutes=a.pairing_span)
            hi = leg["t1"] + timedelta(minutes=a.pairing_span)
            window = [d for d in self.duties if d["i"] not in skip
                      and d["t1"] >= lo and d["t0"] <= hi]
            saved = (a.dh_options, a.branch_chain, a.depth,
                     a.cg_top_k, getattr(a, "dh_full_transfers", False))
            a.dh_options = 10 ** 6
            a.branch_chain = 10 ** 6
            a.depth = 64
            a.cg_top_k = 10 ** 6
            a.dh_full_transfers = True
            try:
                rr = self.price_pass(
                    {}, 10 ** 6,
                    None if det else _time.monotonic() + min(left, max(
                        60.0, budget / max(1, len(orphans0)))),
                    skip, accept_all=True, only_duties=window,
                    per_duty=10 ** 9)
            finally:
                (a.dh_options, a.branch_chain, a.depth,
                 a.cg_top_k, a.dh_full_transfers) = saved
            trunc = rr.truncation_rows()
            if leg_id not in set(self.orphan_legs()):
                esc["covered"].append(leg_id)
                print("веха [%s] эскалация: %s покрыт доказательной "
                      "ступенью" % (_time.strftime("%H:%M:%S"),
                                    leg_id), flush=True)
            elif rr.deadline_hit:
                esc["budget_hit"].append(leg_id)
            elif trunc:
                esc.setdefault("not_certified", []).append(
                    {"leg": leg_id,
                     "truncations": sorted({t.get("name", "?")
                                            for t in trunc})})
            else:
                esc["proved"].append(
                    {"leg": leg_id, "window_duties": len(window),
                     "note": "полный обход окна размаха командировки "
                             "завершён без усечений — законной "
                             "цепочки с рейсом не существует"})
                print("веха [%s] эскалация: %s — невозможность "
                      "ДОКАЗАНА (окно %d смен, обход полный)"
                      % (_time.strftime("%H:%M:%S"), leg_id,
                         len(window)), flush=True)
        esc["orphans_final"] = len(self.orphan_legs())
        self.report["escalation"] = esc
        self.report["candidates"] = len(self.candidates)
        print("этап: эскалация сирот — было %d, покрыто %d, доказано "
              "%d, бюджет исчерпан у %d, не сертифицировано %d"
              % (len(orphans0), len(esc["covered"]),
                 len(esc["proved"]), len(esc["budget_hit"]),
                 len(esc.get("not_certified", []))), flush=True)

    # --- генерация колонок по двойственным оценкам ----------------------
    def _duty_dual(self, duty: dict, duals: dict) -> float:
        return sum(duals.get(l["id"], 0.0) for l in duty["legs"])

    def price_pass(self, duals: dict, top_k: int, deadline: float,
                   skip: set[int], accept_all: bool = False,
                   only_duties=None,
                   per_duty: int | None = None) -> "_pc.PricingResult":
        """Один проход ценообразования: цепочки с приведённой стоимостью
        ниже порога. Приёмы те же, что в генераторе колонок этапа 1:
        куча лучших колонок с адаптивным порогом, обоснованная нижняя
        оценка приведённой стоимости для отсечения ветви, ограничение
        эмиссии на стартовую смену (иначе проход утыкается в один
        «горячий» регион) и предел времени прохода.

        `accept_all` снимает отсев по приведённой стоимости: при нулевых
        двойственных ценах выгодных цепочек не бывает по определению, и
        без этого режима проход не породит ни одной. Он нужен, чтобы
        добыть хоть какую-то цепочку плечу, которому иначе не досталось
        ни одного кандидата. `only_duties` сужает обход до нужных смен."""
        import heapq
        import itertools
        import time as _time
        if top_k <= 0:
            # P1-07 (аудит R10): heap[0] на пустой куче падал бы IndexError
            # глубоко в проходе — граница параметров проверяется на входе.
            raise ValueError(f"top_k должен быть положительным: {top_k}")
        a = self.args
        cap = a.per_duty if per_duty is None else per_duty
        heap: list = []
        tiebreak = itertools.count()
        emit_count: Counter = Counter()
        exhausted = True
        # Капы обхода (преемники, ветвление, перегоны) — эвристические
        # усечения: пока хоть один сработал, «новых цепочек нет» доказывает
        # лишь исчерпание УСЕЧЁННОГО перебора, и значение вспомогательной
        # задачи границей оптимума не является (внешний аудит R6, P0-04).
        # Именованный приёмник усечений этого прохода (внешний аудит R9,
        # P0-01; аудит R10 — единственная форма). Он передаётся В отбор,
        # а не заполняется вызывающими: прежде каждый из шести вызывающих
        # сам решал, записать ли усечение по длине результата, и двое
        # этого не делали. Прежний набор имён `caps_hit` упразднён:
        # результат прохода — PricingResult, полнота вычисляется из
        # перечня усечений, отдельного «exhausted» не существует.
        price_sink = _pc.Truncations(_pc.SCOPE_PRICING)
        # максимальная сумма двойственных цен одной смены — из неё
        # строится верхняя оценка того, что даст любое продолжение
        max_duty_dual = max((self._duty_dual(d, duals) for d in self.duties),
                            default=0.0)

        def threshold() -> float:
            return -heap[0][0] if len(heap) >= top_k else -1e-6

        def emit_col(chain, dh_legs, dual_sum) -> None:
            key = (tuple(d["i"] for d in chain),
                   tuple(sorted(x["id"] for x in dh_legs)))
            if key in self.candidates:
                return
            # Те же правила последовательности, что и в широком проходе:
            # иначе генерация колонок предложит недопустимую цепочку и
            # оптимум будет получен на другой задаче.
            if not self.chain_ok(chain):
                return
            if not self.pairing_days_ok(chain, dh_legs):
                return
            if not self.relay_ok(chain, dh_legs):
                return
            # П.1.1.5 с перегонами — зеркально emit (см. комментарий там)
            if not self.charter_mix_ok(chain, extra_legs=dh_legs):
                return
            # Период обязанностей с перегонами — зеркально emit
            tiers = self.periods_tiers(chain, dh_legs)
            if tiers is None:
                return
            if not self.adjacent_dh_ok(chain, dh_legs):
                return
            crew = self.crew_size(chain, tiers)
            pr = self.price(chain, dh_legs, crew)     # A6: одна цена
            cost, n = pr["cost"], pr["nights"]
            rc = cost - dual_sum
            if not accept_all and rc >= threshold():
                return
            start = chain[0]["i"]
            if emit_count[start] >= cap:
                # Кап выпуска на смену — тоже усечение перебора (внешний
                # аудит R7, P0-04а: per_duty=0 давал «0 колонок,
                # исчерпано, капов нет»). Запись честная по построению:
                # мы пришли сюда с (cap+1)-й цепочкой этой смены.
                price_sink.record("per_duty", cap, emit_count[start] + 1)
                return
            emit_count[start] += 1
            rec = {"duties": [d["i"] for d in chain],
                   "dh": sorted(x["id"] for x in dh_legs), "cost": cost,
                   "nights": n, "crew": crew, "price": pr,
                   "legs": [l["id"] for d in chain for l in d["legs"]],
                   "rc": rc}
            item = (-rc, next(tiebreak), key, rec)
            if len(heap) < top_k:
                heapq.heappush(heap, item)
            else:
                heapq.heappushpop(heap, item)

        def walk(chain, station, avail, t0, dual_sum, pre=()) -> None:
            nonlocal exhausted
            if deadline is not None and _time.monotonic() > deadline:
                exhausted = False
                raise TimeoutError
            if station in self.bases:
                emit_col(chain, list(pre), dual_sum)
                return
            lim = t0 + timedelta(minutes=a.pairing_span)
            for home in self.bases:
                opts = self.dh_options(
                    station, home,
                    avail + timedelta(minutes=self.dh_board_min()), lim,
                    a.dh_options, sink=price_sink)
                for dh in opts:
                    emit_col(chain, list(pre) + dh, dual_sum)
            if len(chain) >= a.depth:
                # Предел глубины цепочки — ЭВРИСТИКА, а не объявленная
                # граница постановки: он не следует из правила о
                # длительности командировки. Прежде обрыв по нему не
                # записывался никуда, и «перебор исчерпан» означало
                # «исчерпан в пределах шести звеньев» (внешний аудит R9,
                # сосед P0-01).
                #
                # Свидетельство ПРИБЛИЖЁННОЕ и названо так честно: точная
                # проверка «существовало ли допустимое продолжение»
                # потребовала бы построить множество преемников ровно там,
                # где обход и прерывается ради скорости. Берётся дешёвое
                # необходимое условие — есть ли вылеты со станции вообще.
                # Направление ошибки безопасное: лишняя запись ПОДАВЛЯЕТ
                # границу, а не публикует её.
                #
                # Свидетель обязан смотреть В ТО ЖЕ пространство, которое
                # обрезается (аудит R10, опровержение №1): продолжения
                # ниже строятся от станции И равнозначных ей (правило
                # телепортации источника), и вылет с равнозначной станции
                # прежде оставался невидимым — усечение ПРОПУСКАЛОСЬ.
                wit_stations = ([station] if self.rules is None else
                                [station] + [to for (frm, to)
                                             in (self.rules.teleportation
                                                 or {})
                                             if frm == station])
                if any(self.by_dep.get(s) for s in wit_stations):
                    price_sink.record("depth~", a.depth, a.depth + 1)
                return
            # обоснованная нижняя оценка: стоимость цепочки не убывает при
            # расширении, а прирост двойственных не превышает
            # (осталось шагов) x (максимум по смене)
            n_left = a.depth - len(chain)
            _crew = self.crew_size(chain)
            # A6: та же цена, что у выпуска; open_end — последний период,
            # оконченный вне базы, ночи ещё не даёт: цена не убывает при
            # расширении, оценка снизу остаётся обоснованной.
            cur_cost = self.price(chain, list(pre), _crew,
                                  open_end=True)["cost"]
            if not accept_all and \
                    cur_cost - dual_sum - n_left * max_duty_dual >= threshold():
                return
            need = self.rest_after(chain[-1], station)
            # Те же правила, что и в широком проходе: равнозначные станции,
            # ночная серия, час начала после ночной смены. Иначе генерация
            # колонок предложит цепочки, недопустимые по постановке, и
            # оптимум будет получен на другой задаче.
            starts = ([(station, 0)] if self.rules is None else
                      [(station, 0)] + [(to, gap) for (frm, to), gap
                                        in (self.rules.teleportation
                                            or {}).items()
                                        if frm == station])
            succ = []
            for st, tel_gap in starts:
                for d in self.by_dep.get(st, ()):
                    if d["i"] in skip:
                        continue
                    if self.busy_from(d) < avail + timedelta(
                            minutes=max(need, tel_gap)):
                        continue
                    if d["t1"] > lim:
                        break
                    if not self.transition_ok(chain[-1], d, station):
                        continue
                    if not self.pairing_span_ok(chain + [d]):
                        continue
                    succ.append(d)
                    if len(succ) > 40:
                        # 41-й допустимый НАБЛЮДЁН (аудит R10): «набрано
                        # 40» само по себе усечения не доказывало — при
                        # ровно сорока вариантах перебор полный. Лишний
                        # элемент в работу не идёт.
                        price_sink.record("successors_40", 40, 41)
                        succ.pop()
                        break
            succ.sort(key=lambda d: self._duty_dual(d, duals), reverse=True)
            ret = next((d for d in succ if d["arr"] in self.bases), None)
            picked = succ[:a.branch_chain]
            if len(succ) > a.branch_chain:
                # лишние преемники реально существуют в succ — наблюдение
                price_sink.record("branch_chain", a.branch_chain, len(succ))
            if ret is not None and ret not in picked:
                picked.append(ret)
            for nxt in picked:
                walk(chain + [nxt], nxt["arr"], self.free_from(nxt), t0,
                     dual_sum + self._duty_dual(nxt, duals), pre)

        pool = self.duties if only_duties is None else only_duties
        order = sorted((d for d in pool if d["i"] not in skip),
                       key=lambda d: self._duty_dual(d, duals), reverse=True)
        try:
            for d in order:
                if d["dep"] in self.bases:
                    walk([d], d["arr"], self.free_from(d), d["t0"],
                         self._duty_dual(d, duals))
                else:
                    for home in self.bases:
                        # P0-01 (внешний аудит R9): здесь усечение НЕ
                        # записывалось вовсе, тогда как у обратного
                        # перегона — записывалось. Приёмник тот же самый.
                        for dh_out in self.dh_options(
                                home, d["dep"],
                                d["t0"] - timedelta(minutes=a.pairing_span),
                                self.pre_dh_latest(d),
                                a.dh_options, latest=True, sink=price_sink):
                            if not self.head_dh_ok(d, dh_out):
                                continue
                            walk([d], d["arr"], self.free_from(d),
                                 dh_out[0]["t0"],
                                 self._duty_dual(d, duals), tuple(dh_out))
        except TimeoutError:
            pass
        cols = [rec for _k, _t, _key, rec in heap]
        for _k, _t, key, rec in heap:
            self.candidates[key] = rec
        # Ни одно усечение не выводится по длине уже усечённого списка:
        # отбор подсматривает (k+1)-й вариант и записывает факт только
        # когда тот действительно существовал. Результат — единый тип
        # (аудит R10, пакет A): полнота ВЫЧИСЛЯЕТСЯ из перечня усечений,
        # записать «исчерпано» отдельно от доказательства невозможно по
        # построению; тройка (cols, exhausted, caps) упразднена.
        self._last_pricing_truncations = price_sink
        import hashlib
        import json as _json
        duals_fp = hashlib.sha256(_json.dumps(
            sorted((str(k), round(float(v), 6)) for k, v in duals.items())
        ).encode()).hexdigest()[:16]
        return _pc.PricingResult(
            columns=tuple(cols), sinks=(price_sink,),
            problem_fingerprint=str(getattr(a, "rules_fingerprint", "")
                                    or ""),
            duals_fingerprint=duals_fp,
            deadline_hit=not exhausted)

    def generate_columns(self, skip: set[int]) -> None:
        """Цикл генерации колонок: ослабленная задача покрытия даёт
        двойственные цены, ценообразование ищет улучшающие цепочки.
        Двойственные сглаживаются (приём Neame): смесь текущего решения
        и предыдущего, что гасит колебания цен между раундами."""
        import time as _time
        from ortools.linear_solver import pywraplp
        a = self.args
        # стартовый пул: обычное перечисление с нулевыми ценами
        self.enumerate_candidates(skip)
        prev: dict = {}
        rounds = []
        for rnd in range(1, a.cg_rounds + 1):
            cands = list(self.candidates.values())
            lp = pywraplp.Solver.CreateSolver("GLOP")
            xs = [lp.NumVar(0, 1, f"x{i}") for i in range(len(cands))]
            by_leg: dict[str, list] = defaultdict(list)
            for ci, c in enumerate(cands):
                for lid in c["legs"]:
                    by_leg[lid].append(xs[ci])
            cons = {}
            for lid in self.legs:
                if lid in self.transport_only:
                    continue          # транспорт покрытия не требует
                if lid in by_leg:
                    ct = lp.Constraint(1, lp.infinity())
                    for v in by_leg[lid]:
                        ct.SetCoefficient(v, 1)
                    cons[lid] = ct
            obj = lp.Objective()
            for c, x in zip(cands, xs):
                obj.SetCoefficient(x, float(c["cost"]))
            obj.SetMinimization()
            if lp.Solve() not in (pywraplp.Solver.OPTIMAL,
                                  pywraplp.Solver.FEASIBLE):
                break
            raw = {lid: ct.dual_value() for lid, ct in cons.items()}
            duals = {lid: (a.cg_alpha * prev.get(lid, v)
                           + (1 - a.cg_alpha) * v) for lid, v in raw.items()}
            prev = duals
            deadline = _deadline_of(a, a.cg_pass_s)
            res = self.price_pass(duals, a.cg_top_k, deadline, skip)
            cols = list(res.columns)
            exhausted = not res.deadline_hit
            caps = res.truncation_names()
            rounds.append({"round": rnd,
                           # Округление ВНИЗ (внешний аудит R9): величина
                           # публикуется как граница СНИЗУ, а округление к
                           # ближайшему способно поднять её на полединицы
                           # выше истинного оптимума — то есть «доказать»
                           # недостижимое.
                           "rmp_lp_value_on_pool": _pc.floor_bound(obj.Value()),
                           "columns": len(cols), "pool": len(self.candidates),
                           "exhaustive": exhausted, "caps": caps,
                           # Машинный перечень усечений с limit/observed
                           # (аудит R10): имена без чисел доказательством
                           # не являются.
                           "truncations": res.truncation_rows(),
                           "certifies_bound": res.certifies_bound()})
            print("этап: круг %d/%d — LP на пуле %d, новых цепочек %d, "
                  "пул %d%s" % (rnd, a.cg_rounds, round(obj.Value()),
                                len(cols), len(self.candidates),
                                "" if exhausted else " (круг оборван по "
                                "времени)"), flush=True)
            if not cols and exhausted:
                # Сходимость доказана пока лишь на СГЛАЖЕННЫХ дуалах:
                # отсутствие улучшающей цепочки относительно центра
                # стабилизации не есть отсутствие её относительно точных
                # дуалов текущего RMP (внешний аудит R7, P0-04б).
                # Контрольный проход по сырым дуалам: нашёл цепочки —
                # сходимости не было, продолжаем; чист и не усечён —
                # сходимость подтверждена.
                deadline = _deadline_of(a, a.cg_pass_s)
                res2 = self.price_pass(raw, a.cg_top_k, deadline, skip)
                cols2 = list(res2.columns)
                exh2 = not res2.deadline_hit
                caps2 = res2.truncation_names()
                rounds[-1]["raw_pass"] = {"columns": len(cols2),
                                          "exhaustive": exh2,
                                          "caps": caps2,
                                          "truncations":
                                              res2.truncation_rows(),
                                          "certifies_bound":
                                              res2.certifies_bound()}
                if cols2:
                    print("этап: контроль по сырым дуалам нашёл %d "
                          "цепочек — генерация продолжается" % len(cols2),
                          flush=True)
                    continue
                print("этап: генерация колонок сошлась (контроль по "
                      "сырым дуалам: %s)"
                      % ("чист" if exh2 and not caps2
                         else "усечён — полноты нет"), flush=True)
                break
        # Контракт Б (единая версия 06.09.2026, P-427): исчерпание
        # поиска по числовой метрике (известной части цены) ничего не
        # говорит о полной цене — у пула с неполной областью учёта
        # целевая поиска названа известной частью явно. Сертификаты
        # границы (certifies_bound) и оценка снизу НЕ обнуляются: они
        # относятся к целевой поиска (известной части), см.
        # lower_bound_scope / known_subtotal_search_complete ниже.
        pool_price_complete = bool(self.candidates) and all(
            ((c.get("price") or {}).get("certainty") or {}).get("complete") is True
            for c in self.candidates.values())
        self.report["price_complete_for_pool"] = pool_price_complete
        self.report["pricing_objective_scope"] = (
            "complete_declared_scope" if pool_price_complete else "known_subtotal")
        self.report["cg_rounds"] = rounds
        if rounds:
            last = rounds[-1]
            # Значение вспомогательной задачи на ТЕКУЩЕМ пуле становится
            # оценкой снизу только при сходимости: пока перебор находит
            # новые цепочки, добавление их может это значение уменьшить,
            # и настоящий оптимум способен оказаться ниже. Публиковать
            # несошедшееся значение как оценку — значит выдать за границу
            # то, что границей не является.
            #
            # Вторая, более тонкая подмена (внешний аудит R6, P0-04):
            # «новых цепочек нет» при сработавших капах обхода доказывает
            # исчерпание лишь УСЕЧЁННОГО перебора — улучшающая цепочка
            # могла остаться за капом. Поэтому сходимость и полнота
            # ценообразования разделены: математическая оценка снизу
            # публикуется только когда последний проход прошёл без капов;
            # иначе значение честно называется величиной на текущем пуле.
            converged = bool(last["exhaustive"]) and last["columns"] == 0
            caps = sorted(set(last.get("caps") or [])
                          | set((last.get("raw_pass") or {}).get("caps")
                                or [])
                          | self.duty_caps)
            # Полнота требует и чистого контрольного прохода по СЫРЫМ
            # дуалам (внешний аудит R7, P0-04б): сглаженный вектор — центр
            # стабилизации, а не оптимальные цены текущего RMP.
            rp = last.get("raw_pass") or {}
            raw_ok = (converged and rp.get("columns") == 0
                      and bool(rp.get("exhaustive")))
            self.report["cg_converged"] = converged
            self.report["pricing_caps"] = caps
            self.report["pricing_raw_dual_verified"] = raw_ok
            # Полнота ВЫЧИСЛЯЕТСЯ из сертификатов проходов (аудит R10),
            # а не из набора имён: последний проход и контроль по сырым
            # дуалам обязаны сертифицировать границу каждый сам, усечения
            # пространства смен (duty_caps) подавляют её так же.
            self.report["pricing_complete"] = (
                converged and raw_ok
                and bool(last.get("certifies_bound"))
                and bool(rp.get("certifies_bound"))
                and not self.duty_caps)
            # Машинный перечень усечений с limit/observed — требование
            # аудита R10: имена без чисел доказательством не являются.
            self.report["pricing_truncations"] = (
                list(last.get("truncations") or [])
                + list(rp.get("truncations") or [])
                + [{"name": n, "limit": a.branch_duty,
                    "observed_at_least": a.branch_duty + 1,
                    "scope": _pc.SCOPE_DUTY}
                   for n in sorted(self.duty_caps)])
            self.report["cg_lp_value_on_pool"] = last["rmp_lp_value_on_pool"]
            self.report["lower_bound"] = (
                last["rmp_lp_value_on_pool"]
                if self.report["pricing_complete"] else None)
            # P-427: граница и полнота ценообразования относятся к
            # целевой поиска — известной части цены при неполной области
            # учёта пула; полная цена этим не доказывается.
            self.report["known_subtotal_search_complete"] = self.report["pricing_complete"]
            self.report["lower_bound_scope"] = self.report["pricing_objective_scope"]
            if not converged:
                print("ВНИМАНИЕ: генерация колонок не сошлась за %d кругов "
                      "(последний добавил %d цепочек) — оценки снизу нет. "
                      "Значение %d относится только к текущему пулу и "
                      "границей оптимума НЕ является; увеличьте --cg-rounds."
                      % (len(rounds), last["columns"], last["rmp_lp_value_on_pool"]),
                      flush=True)
            elif caps:
                print("ВНИМАНИЕ: генерация колонок сошлась, но перебор был "
                      "усечён капами (%s) — математической оценки снизу "
                      "нет. Значение %d — величина вспомогательной задачи "
                      "на текущем пуле; статус решения: лучшее из "
                      "построенного." % (", ".join(caps),
                                         last["rmp_lp_value_on_pool"]), flush=True)
            elif not raw_ok:
                print("ВНИМАНИЕ: контроль по сырым дуалам не подтвердил "
                      "полноту ценообразования — математической оценки "
                      "снизу нет. Значение %d — величина вспомогательной "
                      "задачи на текущем пуле." % last["rmp_lp_value_on_pool"],
                      flush=True)

    # --- выбор ---------------------------------------------------------
    # RULE: 0070 0072 -- покрытие целевого множества, каждый рейс ровно один раз
    # RULE: none -- экспорт точной модели покрытия во внешние форматы; правил не меняет, зеркалит построение build_and_solve(exact=True)
    def _select_external(self, engine, cands, fixed, prep_per_leg,
                         prep_dh_groups, prep_base_cap, prep_dh_idx,
                         target, no_cand, hard_cover, ban_groups):
        """Фаза отбора внешним движком (highs | cplex): та же модель
        строк, лексикографика «покрытие первое», тот же цикл запретов
        пар 0055; групповые запреты петли арбитража — ограничениями.
        Недоступный движок — честный отказ каталога, не подмена."""
        import time as _tme
        import _select_engines as se
        a = self.args
        rows, cons_map, cost, fixed_zero, _aux = self._selection_rows(
            cands, prep_per_leg, prep_dh_groups, prep_base_cap,
            prep_dh_idx, target, no_cand)
        hard_names = {n for n, c in cons_map.items()
                      if c.get("kind") == "cover"
                      and c.get("flight") in hard_cover}
        ident2idx = {}
        for ci, c in enumerate(cands):
            ident2idx.setdefault(
                (frozenset(c["legs"]), frozenset(c["dh"])), ci)
        grp_idx = []
        for grp in (ban_groups or ()):
            idxs = [ident2idx[k] for k in grp if k in ident2idx]
            if len(idxs) >= 2:
                grp_idx.append(idxs)
        budget = a.solve_s or a.budget_s
        if getattr(a, "out", None):
            workdir = Path(str(a.out)).parent
        else:
            import tempfile
            workdir = Path(tempfile.mkdtemp(prefix="select-engine-"))
        try:
            se.check_available(engine)
        except se.EngineUnavailable as e:
            print("ОТКАЗ: %s — каталог не публикуется (молчаливой "
                  "подмены движка нет; выберите cpsat либо "
                  "установите движок)" % e, flush=True)
            self.report["select_engine"] = engine
            self.report["select_engine_error"] = str(e)
            self.report["exact_cover"] = False
            return []
        print("этап: движок отбора — %s (модель: строк %d, "
              "кандидатов %d)" % (engine, len(rows), len(cands)),
              flush=True)
        banned_pairs: list = []
        res = None
        for _round in range(3):
            t0 = _tme.monotonic()
            res = se.solve(engine, rows, cons_map, cost, fixed_zero,
                           hard_names, banned_pairs, grp_idx, budget,
                           workdir, a.label)
            # вспомогательные «рейс покрыт» (индексы >= числа
            # кандидатов) — не кандидаты, в выбор не входят
            picked = [ci for ci in res["picked"] if ci < len(cands)]
            print("веха [%s] движок %s: статус %s, выбрано %d, "
                  "непокрытых строк %d (%d с)"
                  % (_tme.strftime("%H:%M:%S"), engine,
                     res["status"], len(picked),
                     len(res["uncovered_rows"]),
                     int(_tme.monotonic() - t0)), flush=True)
            bad = self._bad_0055_pairs(picked, cands)
            if not bad:
                break
            print("этап: перерешивание клапана (движок %s) — "
                  "запрещено пар: %d" % (engine, len(bad)),
                  flush=True)
            banned_pairs.extend(bad)
        self.report["select_engine"] = engine
        # Словарь статусов один для любого движка (контракт
        # независимости от решателя, 04.09.2026); сырой статус движка —
        # рядом, для журнала.
        self.report["solver_status"] = se.normalize_status(
            res["status"], bool(picked))
        self.report["solver_status_raw"] = res["status"]
        self.report["dh_gap_pairs_banned"] = len(banned_pairs)
        self.report["select_engine_uncovered_rows"] = \
            len(res["uncovered_rows"])
        self.report["exact_cover"] = (
            not res["uncovered_rows"]
            and self.report["solver_status"] == "OPTIMAL")
        if res.get("bound") is not None:
            self.report["select_engine_bound"] = res["bound"]
        chosen = [cands[ci] for ci in picked]
        chosen.extend(fixed)
        return chosen

    def _selection_rows(self, cands, prep_per_leg, prep_dh_groups,
                        prep_base_cap, prep_dh_idx, target, no_cand):
        """Строки модели отбора — ЕДИНЫЙ носитель для экспорта и
        внешних движков (зеркало build_and_solve, менять синхронно).
        Возврат: (rows, cons_map, cost, fixed_zero, aux_map);
        aux_map: индекс вспомогательной переменной -> рейс. Перегоны
        связаны линейной формой «рейс покрыт» (y <= сумма
        исполнителей; перегон <= y) — как в build_and_solve: прежняя
        форма несла полную сумму в каждой строке и была квадратичной
        по слагаемым. Вспомогательные переменные идут ПОСЛЕ
        кандидатов (индексы >= len(cands)), их цена 0."""
        rows = []          # (sense 'E'|'L', name, rhs, [(var_i, coef)])
        cons_map = {}
        fixed_zero = set()
        for lid, idxs in sorted(prep_per_leg.items()):
            name = "c%d" % len(rows)
            if lid in target and lid not in no_cand:
                rows.append(("E", name, 1, [(ci, 1) for ci in idxs]))
                cons_map[name] = {"kind": "cover", "flight": lid}
            else:
                rows.append(("L", name, 1, [(ci, 1) for ci in idxs]))
                cons_map[name] = {"kind": "at_most_one",
                                  "flight": lid}
        for grp, cap in prep_dh_groups:
            name = "c%d" % len(rows)
            rows.append(("L", name, cap, [(ci, 1) for ci in grp]))
            cons_map[name] = {"kind": "dh_limit", "cap": cap}
        for b2, items2 in sorted(prep_base_cap.items()):
            name = "c%d" % len(rows)
            cap_eff = self.base_cap_eff(b2)          # A5
            rows.append(("L", name, cap_eff,
                         [(ci, s2) for s2, ci in items2]))
            cons_map[name] = {"kind": "base_day_cap", "base": b2,
                              "cap": cap_eff,
                              "cap_total": self.base_day_cap[b2]}
        aux_map = {}
        next_var = len(cands)
        fixed_cov = self.fixed_resources()["cov"]
        for lid, didx in sorted(prep_dh_idx.items()):
            if lid in fixed_cov:
                continue          # рейс выполняет закреплённая цепочка (A5)
            wvs = prep_per_leg.get(lid) or ()
            if not wvs:
                for ci in didx:
                    fixed_zero.add(ci)
                continue
            if not didx:
                continue
            yv = next_var
            next_var += 1
            aux_map[yv] = lid
            name = "c%d" % len(rows)
            rows.append(("L", name, 0,
                         [(yv, 1)] + [(wi, -1) for wi in wvs]))
            cons_map[name] = {"kind": "dh_requires_covered_link",
                              "flight": lid}
            for ci in didx:
                name = "c%d" % len(rows)
                rows.append(("L", name, 0, [(ci, 1), (yv, -1)]))
                cons_map[name] = {"kind": "dh_requires_covered",
                                  "flight": lid, "candidate": ci}
        cost = [int(c["cost"]) for c in cands] + [0] * len(aux_map)
        return rows, cons_map, cost, fixed_zero, aux_map

    def _export_exact_model(self, cands, prep_per_leg, prep_dh_groups,
                            prep_base_cap, prep_dh_idx, target, no_cand):
        """Точная модель покрытия свободным MPS и/или LP + справочник.

        ЗЕРКАЛО сборки `build_and_solve(exact=True)` (менять СИНХРОННО):
        переменная на кандидата; покрытие == 1 по обязательствам с
        кандидатами; не более одного — по прочим участкам; лимиты
        перевозок пассажиром; ёмкости баз (при профиле); перегон своим
        рейсом требует выбранного исполнителя (запрет — нулевой границей).
        Подсказки тёплого старта и ленивые запреты пар в модель не входят
        (они не меняют множество допустимых решений первого захода).
        Участки без кандидатов в строки не попадают и перечисляются в
        справочнике: их непокрываемость — свойство пула, внешний решатель
        о них знать обязан из map.json, не из неразрешимости.
        """
        nvar = len(cands)
        rows, cons_map, cost, fixed_zero, aux_map = \
            self._selection_rows(
                cands, prep_per_leg, prep_dh_groups, prep_base_cap,
                prep_dh_idx, target, no_cand)
        n_all = len(cost)      # кандидаты + вспомогательные «покрыт»
        mps_path = getattr(self.args, "export_mps", None)
        lp_path = getattr(self.args, "export_lp", None)
        # Потоковая запись (поручение владельца 04.09.2026: CPLEX на всех
        # наборах) — файлы пишутся строка за строкой, список строк и
        # словарь всех переменных в памяти не собираются.
        if mps_path:
            write_exact_mps(mps_path, rows, cost, fixed_zero, n_all)
        if lp_path:
            write_exact_lp(lp_path, rows, cost, fixed_zero, nvar, n_all)
        base = Path(mps_path or lp_path)
        map_path = base.with_suffix(base.suffix + ".map.json")
        write_exact_map(map_path, cands, cost, aux_map, cons_map, {
            "objective": {"sense": "min",
                          "kind": "стоимость кандидатов (критерий "
                                  "профиля запуска)"},
            "pool": nvar, "exact": True,
            "obligations": sum(1 for v2 in cons_map.values()
                               if v2["kind"] == "cover"),
            "uncoverable_flights": sorted(no_cand),
            "fixed_zero_vars": sorted("x%d" % i for i in fixed_zero),
        })
        print("экспорт точной модели: переменных %d (кандидатов %d, "
              "вспомогательных %d), строк %d "
              "(покрытие %d, прочие %d), файлы: %s%s + %s"
              % (n_all, nvar, len(aux_map), len(rows),
                 sum(1 for v2 in cons_map.values()
                     if v2["kind"] == "cover"),
                 len(rows) - sum(1 for v2 in cons_map.values()
                                 if v2["kind"] == "cover"),
                 mps_path or "", (" / %s" % lp_path) if lp_path else "",
                 map_path), flush=True)

    def select(self, ban_groups=()) -> list[dict]:
        from ortools.sat.python import cp_model
        fixed = []
        for idx, ch in enumerate(self.fixed_chains):
            meta = (self.fixed_meta[idx] if idx < len(self.fixed_meta)
                    else {})
            dh_legs = meta.get("dh_legs") or []
            crew = self.crew_size(ch)
            # Стоимость закреплённой цепочки — ЧЕСТНАЯ, вместе с её
            # перегонами и той же ценой, что у построенных (A6): нулевая
            # стоимость данности занижала бы цель ровно на цену
            # обязательного условия.
            pr = self.price(ch, dh_legs, crew)
            cost = pr["cost"]
            fixed.append({
                "duties": [d["i"] for d in ch],
                "dh": meta.get("dh") or [], "cost": cost,
                "nights": pr["nights"], "price": pr,
                "crew": crew,
                "legs": [l["id"] for d in ch for l in d["legs"]],
                "fixed": True,
                "sourceUnit": meta.get("sourceUnit")})
        chk_in = getattr(self.args, "use_cover_checkpoint", None)
        if chk_in:
            # Восстановление выбора из контрольной записи: решатель не
            # вызывается вовсе. Смены находятся по идентификаторам
            # участков — индексы пула между запусками не гарантированы.
            import json as _json
            doc = _json.loads(Path(chk_in).read_text(encoding="utf-8"))
            # Смена находится по ПОЗИЦИИ в пуле, не по полю i: сборщик
            # каталога разрешает индексы позиционно, а поле i у одной
            # смены пула разошлось с позицией (потерянный крест трёх
            # рейсов, приёмка одного из прогонов).
            by_legs = {tuple(l["id"] for l in d["legs"]): pos
                       for pos, d in enumerate(self.duties)}
            recovered = []
            for rec in doc["records"]:
                rec = dict(rec)
                keys = [tuple(dl) for dl in rec.pop("duty_legs")]
                missing = [k for k in keys if k not in by_legs]
                assert not missing, (
                    "контрольная запись не совпала с пулом смен: %r"
                    % missing[:2])
                rec["duties"] = [by_legs[k] for k in keys]
                recovered.append(rec)
            self.report["solver_status"] = "COVER_CHECKPOINT"
            self.report["exact_cover_status"] = "SKIPPED_CHECKPOINT"
            self.report["exact_cover"] = False
            self.report["solve_seconds"] = 0
            self.report["solver_deterministic"] = True
            self.report["cover_checkpoint"] = {
                "objective": doc.get("objective"),
                "bound": doc.get("bound"),
                "n_solution": doc.get("n_solution")}
            print("этап: выбор восстановлен из контрольной записи — "
                  "цепочек %d, целевая %.0f, граница %.0f (решатель "
                  "не вызывался)" % (len(recovered),
                                     doc.get("objective") or 0,
                                     doc.get("bound") or 0), flush=True)
            return fixed + recovered
        cands = list(self.candidates.values())
        # A5: закрепления — члены той же модели ресурсов (константа 1):
        # их рабочие участки выполняются, перегоны занимают места и
        # перевозки дня, командировки — ёмкость баз.
        fixed_res = self.fixed_resources()
        self.report["fixed_resources"] = {
            "working_legs": len(fixed_res["cov"]),
            "dh": len(fixed_res["dh"]),
            "base_days": dict(fixed_res["base_days"])}
        if not cands:
            # уровень 1 мог занять все смены закреплениями — тогда
            # выбирать нечего, но сами закрепления остаются решением
            self.report["solver_status"] = "FIXED_ONLY"
            return fixed
        # Перегон СВОИМ рейсом имеет смысл только если этот рейс кто-то
        # выполняет. Цепочка, чей перегон стоит на рейсе, которого не
        # покрывает НИ ОДИН кандидат, недостижима — и её надо убрать ДО
        # модели: оставленная в пуле, она делает задачу неразрешимой,
        # когда оказывается единственным кандидатом на какой-то рейс
        # (пустое решение на 125 тысячах кандидатов, 2026-08-25).
        # Отсев итеративный: изъятие цепочек может лишить покрытия
        # другие рейсы, и каскад продолжается до устойчивости.
        alive = list(range(len(cands)))
        dropped_ghost = 0
        while True:
            cov_by: dict[str, int] = Counter()
            # рейс закреплённой цепочки выполняется ею — перегон на нём
            # законен (A5: прежде такие цепочки отсеивались как
            # «перегон на невыполняемом рейсе», участок становился
            # ложной сиротой)
            cov_by.update(fixed_res["cov"])
            for i in alive:
                for lid in cands[i]["legs"]:
                    cov_by[lid] += 1
            bad = set()
            for i in alive:
                for lid in cands[i].get("dh") or ():
                    if lid in self.transport_only:
                        continue          # чужой рейс выполняет не мы
                    if not cov_by.get(lid):
                        bad.add(i)
                        break
            if not bad:
                break
            alive = [i for i in alive if i not in bad]
            dropped_ghost += len(bad)
        if dropped_ghost:
            cands = [cands[i] for i in alive]
            print("этап: отсев цепочек с перегоном на невыполняемом "
                  "рейсе — убрано %d, осталось %d"
                  % (dropped_ghost, len(cands)), flush=True)
        self.report["dropped_ghost_dh_chains"] = dropped_ghost
        if not cands:
            self.report["solver_status"] = "NO_CANDIDATES_AFTER_GHOST_FILTER"
            return fixed
        dump = getattr(self.args, "dump_pool", None)
        if dump:
            # Выгрузка пула для внешнего доказательства (CPLEX,
            # 2026-08-28): кандидаты ПОСЛЕ отсева фантомных перегонов —
            # ровно то пространство, что видит ослабленный режим;
            # счёт не запускается. ПРЯМАЯ запись, НЕ to_catalog: тот
            # ведёт арбитраж владения и пул из перекрывающихся
            # кандидатов схлопывает (72 из 2070 на срезе).
            recs = []
            for i, c in enumerate(cands):
                duty_fns = [[l["id"] for l in self.duties[di]["legs"]]
                            for di in c["duties"]]
                act = [lid for dl in duty_fns for lid in dl]
                dh = sorted(c.get("dh") or [])
                recs.append({
                    "sourceUnit": "pool-%06d" % i,
                    "fns": act + dh, "active_fns": act,
                    "duty_fns": duty_fns, "dh": dh,
                    "cost": c["cost"], "crew": c["crew"]})
            Path(dump).write_text(json.dumps(
                {"selected": recs, "label": "POOL-FULL",
                 "cost_units": ("деци-норм-минуты 0096"
                                if self.args.cost_0096 else
                                "коэффициенты расчёта")},
                ensure_ascii=False), encoding="utf-8")
            print("пул выгружен: %d кандидатов -> %s"
                  % (len(recs), dump), flush=True)
            raise SystemExit(0)
        top_k = getattr(self.args, "valve_top_per_leg", 0) or 0
        if top_k > 0:
            # Предфильтр модели: на каждый рейс — K самых дешёвых
            # кандидатов (по стоимости на покрытый рейс). Сборка модели
            # на полном пуле (162 тыс.) недетерминированно раздувала
            # память однопоточно (774 МБ / 7,8 ГБ / 16,6 ГБ на одном и
            # том же коде и данных, 2026-08-26) — сжатие пула на
            # порядок закрывает окно риска; дорогие кандидаты в оптимум
            # не входят, потеря ограничена K.
            best: dict[str, list] = {}
            for i, c in enumerate(cands):
                eff = c["cost"] / max(1, len(c["legs"]))
                for lid in c["legs"]:
                    best.setdefault(lid, []).append((eff, i))
            keep: set[int] = set()
            for lid, lst in best.items():
                lst.sort()
                keep.update(i for _, i in lst[:top_k])
            keep.update(i for i, c in enumerate(cands) if c.get("fixed"))
            dropped = len(cands) - len(keep)
            cands = [cands[i] for i in sorted(keep)]
            print("этап: предфильтр клапанной модели — по %d кандидатов "
                  "на рейс: осталось %d, отсечено %d"
                  % (top_k, len(cands), dropped), flush=True)
            self.report["valve_prefilter_kept"] = len(cands)
            self.report["valve_prefilter_dropped"] = dropped
        if getattr(self.args, "greedy_cover", False):
            # Жадное покрытие вместо решателя — режим быстрого цикла
            # «решение -> суд правил». Судьям нужен набор ПОСТРОЕННЫХ
            # цепочек, а не оптимум: точное покрытие на полном наборе
            # структурно неразрешимо (ядро 288 участков), и клапанный
            # каскад с коротким бюджетом уходит ни с чем (пустой выбор
            # за 72 минуты, 2026-08-26). Жадный отбор дешёвых цепочек
            # первыми — «почти оптимизатор»: он так же охотно берёт
            # нелегально-дешёвые конструкции, если перечислитель их
            # ошибочно допустил, — и делает это за секунды.
            t_g = _now_s()
            order = sorted(range(len(cands)),
                           key=lambda i: cands[i]["cost"]
                           / max(1, len(cands[i]["legs"])))
            covered: set[str] = set()
            chosen = list(fixed)
            for rec in fixed:
                covered.update(rec["legs"])
            picked = 0
            for i in order:
                legs = cands[i]["legs"]
                if any(l in covered for l in legs):
                    continue
                covered.update(legs)
                chosen.append(cands[i])
                picked += 1
            self.report["solver_status"] = "GREEDY_COVER"
            self.report["exact_cover_status"] = "SKIPPED_GREEDY"
            self.report["solve_seconds"] = round(_now_s() - t_g, 1)
            self.report["solver_deterministic"] = True
            print("этап: жадное покрытие — цепочек %d, покрыто %d "
                  "(%.0f с); решатель пропущен по --greedy-cover"
                  % (picked, len(covered), _now_s() - t_g), flush=True)
            return chosen
        import time as _tm
        print("веха [%s] сборка базовой модели: кандидатов %d"
              % (_tm.strftime("%H:%M:%S"), len(cands)), flush=True)
        model = cp_model.CpModel()
        xs = [model.new_bool_var(f"c{i}") for i in range(len(cands))]
        by_leg: dict[str, list] = defaultdict(list)
        for ci, c in enumerate(cands):
            for lid in c["legs"]:
                by_leg[lid].append(xs[ci])
        fixed_cov = {l["id"] for ch in self.fixed_chains for d in ch
                     for l in d["legs"]}
        # Транспортные рейсы (чужой перевозчик) покрытию не подлежат:
        # включать их в целевое множество значило бы требовать на них
        # свой экипаж — задача становится неразрешимой по построению.
        target = set(self.legs) - fixed_cov - self.transport_only
        no_cand = target - set(by_leg)
        # Штраф за двойное покрытие обязан быть соразмерен ЦЕЛИ. Когда
        # ставки взяты из данных источника, прежний свой штраф стал
        # пренебрежимо мал: решатель охотно покрывал
        # участок дважды, а `to_catalog` затем урезал проигравшую цепочку
        # — и в её смене оставалась дыра. Аудит построенного поймал так
        # десятки разрывов станции в урезанных цепочках.
        #
        # Участок, выполненный дважды двумя экипажами, — не компромисс, а
        # бессмыслица; штраф делается заведомо перевешивающим любую
        # экономию, но остаётся конечным, чтобы задача не стала
        # неразрешимой там, где иначе нельзя.
        w_excess = self.args.w_excess
        if self.source_cost:
            w_excess = max(w_excess, 10 * max(
                list(self.dh_cost_of.values())
                + [c * self.args.nights_per_layover
                   for c in self.layover_cost_of.values()] or [1]))
        self.report["excess_penalty"] = w_excess
        self.report["legs_without_candidate"] = len(no_cand)
        # Поимённо: признак «нет кандидата» считается ПО ПУЛУ, не по
        # решению — цикл точечных сдвигов данных (методика unsat-core
        # синтеза UniA) обязан работать по этому списку, а не по
        # непокрытым урезанного решения.
        self.report["legs_without_candidate_legs"] = sorted(no_cand)

        # Участок, выполненный дважды двумя экипажами, — не компромисс, а
        # бессмыслица: выпуская каталог, приходится отнимать участок у
        # одной из цепочек, и в её смене остаётся дыра. Поэтому сначала
        # решается ТОЧНАЯ задача — каждый участок ровно одной цепочкой.
        #
        # Точная задача бывает неразрешима (набор цепочек может не
        # разбиваться нацело), и тогда включается прежний клапан. Но
        # включается он ВСЛУХ: молчаливое ослабление постановки — это и
        # есть подмена задачи. Прежде клапан работал всегда, и при
        # недоказанном оптимуме решатель охотно им пользовался: 186
        # задвоенных участков и 50 цепочек с разрывом.
        class _Progress(cp_model.CpSolverSolutionCallback):
            """Ход отбора покрытия: показатель вместо тишины.

            Отбор идёт десятки минут и до сих пор не печатал ничего:
            «идёт расчёт» без единого числа неотличимо от зависшего
            процесса. Решатель знает своё состояние — лучшее решение,
            нижнюю границу и зазор между ними, — и это ровно тот
            количественный показатель хода, которого не хватало.

            Зазор — честная мера незавершённости: пока он не ноль,
            оптимальность не доказана, сколько бы решений ни нашлось.
            """

            def __init__(self, stage, writer=None, checkpoint=None):
                super().__init__()
                self._n = 0
                self._stage = stage
                self._t0 = _now_s()
                self._last = 0.0
                # Серия для графика схождения: КАЖДОЕ улучшение — в
                # машинный след (файл дешёвый), печать — дросселированной.
                self._writer = writer
                # Контрольная запись решения: (путь, переменные,
                # кандидаты, смены). Регрессия CP-SAT 9.15 виснет на
                # свёртке параллельного поиска (2026-08-26: зазор 0,7%
                # достигнут, возврата из solve нет 20+ минут) — каждое
                # улучшение сохраняется на диск ЦЕЛИКОМ в момент
                # находки, и зависшая свёртка не стоит результата.
                self._checkpoint = checkpoint
                self._best_saved = None

            def on_solution_callback(self):
                self._n += 1
                now = _now_s()
                obj = self.objective_value
                bound = self.best_objective_bound
                if self._writer is not None:
                    self._writer.emit(incumbent=obj, bound=bound, n=self._n)
                # Контрольная запись — ПРИ КАЖДОМ строгом улучшении, вне
                # дросселя печати (замечание владельца 2026-08-26: связка
                # с дросселем теряла финальное решение, если оно приходило
                # младше 15 секунд и останавливало поиск по цели зазора).
                # Улучшений за прогон десятки, запись — доли секунды.
                if self._checkpoint is not None and (
                        self._best_saved is None or obj < self._best_saved):
                    try:
                        self._save_checkpoint(obj, bound, now)
                        self._best_saved = obj
                    except Exception as exc:          # noqa: BLE001
                        print("  ВНИМАНИЕ: контрольная запись решения "
                              "не удалась: %s" % exc, flush=True)
                # Доказанный оптимум — немедленная остановка поиска
                # (предложение владельца 2026-08-27): при границе,
                # равной лучшему, решателю больше нечего искать, а его
                # свёртка на больших моделях висла до 80 минут при уже
                # записанном на диск оптимуме. Решение только что
                # сохранено контрольной записью — останавливаемся сами.
                if obj <= bound:
                    print("  ход %s: решение %d, целевая %.0f = граница "
                          "— оптимум доказан, поиск остановлен "
                          "(прошло %.0f с)"
                          % (self._stage, self._n, obj, now - self._t0),
                          flush=True)
                    self.stop_search()
                    return
                # Не чаще раза в 15 с: журнал должен показывать ход, а
                # не заливать его тысячами строк на больших задачах.
                if self._n > 1 and now - self._last < 15:
                    return
                self._last = now
                gap = (abs(obj - bound) / abs(obj) * 100.0) if obj else 0.0
                print("  ход %s: решение %d, целевая %.0f, граница %.0f, "
                      "зазор %.1f %%, прошло %.0f с"
                      % (self._stage, self._n, obj, bound, gap, now - self._t0),
                      flush=True)

            def _save_checkpoint(self, obj, bound, now):
                import json as _json
                import os as _os
                path, cvars, ccands, duties = self._checkpoint
                chosen = []
                for i, x in enumerate(cvars):
                    if not self.value(x):
                        continue
                    rec = dict(ccands[i])
                    # Индексы смен не переживают повторного перечисления
                    # (порядок пула при параллельном обходе не
                    # гарантирован) — рядом кладутся идентификаторы
                    # участков каждой смены, по ним восстановитель
                    # находит смены заново.
                    rec["duty_legs"] = [
                        [l["id"] for l in duties[di]["legs"]]
                        for di in rec["duties"]]
                    chosen.append(rec)
                doc = {"objective": obj, "bound": bound,
                       "elapsed_s": round(now - self._t0),
                       "n_solution": self._n, "records": chosen}
                tmp = str(path) + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    _json.dump(doc, fh, ensure_ascii=False)
                _os.replace(tmp, str(path))

        # Тяжёлый анализ модели покрытия выполняется ОДИН раз на обе
        # стадии (точную и клапанную): прежде каждая собирала его
        # заново — вторые ~13 минут однопоточной работы на 162 тыс.
        # кандидатов (разбор ёмкостей баз со span_days на каждого,
        # группы лимитов перевозок, фантомные связки перегонов).
        prep_per_leg: dict[str, list] = defaultdict(list)
        for ci, c in enumerate(cands):
            for lid in c["legs"]:
                prep_per_leg[lid].append(ci)
        prep_dh_groups = self.dh_limit_groups(
            cands, list(range(len(cands))), fixed_dh=fixed_res["dh"])
        prep_base_cap: dict[str, list] = defaultdict(list)
        if self.base_day_cap:
            for ci, c in enumerate(cands):
                ch2 = [self.duties[i] for i in c["duties"]]
                dh2 = [self.legs[x] for x in c.get("dh") or ()
                       if x in self.legs]
                acts2 = sorted([l for d in ch2 for l in d["legs"]] + dh2,
                               key=lambda l: l["t0"])
                span2 = self.span_days(ch2, dh2)
                if acts2 and span2 \
                        and acts2[0]["dep"] in self.base_day_cap:
                    prep_base_cap[acts2[0]["dep"]].append((span2, ci))
        prep_dh_idx: dict[str, list] = defaultdict(list)
        for ci, c in enumerate(cands):
            for lid in c.get("dh") or ():
                if lid not in self.transport_only:
                    prep_dh_idx[lid].append(ci)

        if getattr(self.args, "export_mps", None) \
                or getattr(self.args, "export_lp", None):
            self._export_exact_model(cands, prep_per_leg, prep_dh_groups,
                                     prep_base_cap, prep_dh_idx, target,
                                     no_cand)
            if getattr(self.args, "export_only", False):
                print("режим «только экспорт»: модель выгружена, расчёт "
                      "пропущен — каталог не строится", flush=True)
                return []

        hard_cover = set(getattr(self.args, "hard_cover_leg", []) or [])

        # Пакет «выбор движка» (поручение владельца 2026-08-28): фаза
        # отбора решается выбранным движком; пул, судья, арбитраж и
        # петля — общие. Внешний движок получает ТУ ЖЕ модель строк
        # (зеркало _selection_rows) в форме «покрытие лексикографически
        # первое» и тот же трёхраундовый цикл запретов пар 0055.
        engine = str(getattr(self.args, "select_engine", "cpsat")
                     or "cpsat")
        if engine != "cpsat":
            ext = self._select_external(
                engine, cands, fixed, prep_per_leg, prep_dh_groups,
                prep_base_cap, prep_dh_idx, target, no_cand,
                hard_cover, ban_groups)
            return ext

        def build_and_solve(exact: bool, ban_pairs=(), hint_idx=None,
                            ban_groups=()):
            valve_diag = (not exact
                          and os.environ.get("PAIRING_VALVE_CORE") == "1")
            m = cp_model.CpModel()
            v = [m.new_bool_var(f"c{i}") for i in range(len(cands))]
            # Запреты пар кандидатов (ленивое перерешивание 0055,
            # решение владельца 2026-08-27): совместный выбор пары
            # делает перегон проигравшего нелегальным при любом
            # владельце - таким парам вместе не быть.
            for _bi, _bj in (ban_pairs or ()):
                m.add(v[_bi] + v[_bj] <= 1)
            # Групповые запреты (петля «отбор - арбитраж», разбор №28):
            # многосторонний клапан публикационного арбитража, который
            # терял покрытие, возвращается сюда ограничением «всем
            # участникам вместе не быть» — отбор ищет иную комбинацию.
            for grp in (ban_groups or ()):
                idxs = [i for i, c in enumerate(cands)
                        if (frozenset(c["legs"]),
                            frozenset(c["dh"])) in grp]
                if len(idxs) >= 2:
                    m.add(sum(v[i] for i in idxs) <= len(idxs) - 1)
            if hint_idx:
                for _ci in hint_idx:
                    m.add_hint(v[_ci], 1)
            per_leg = {lid: [v[ci] for ci in idxs]
                       for lid, idxs in prep_per_leg.items()}
            pen = []
            # Точная фаза ставится ПРЕДПОЛОЖЕНИЯМИ (assumptions): при
            # доказанной неразрешимости CP-SAT называет ЯДРО — участки,
            # чьи «ровно один» несовместимы. Ядро идёт в машинный отчёт
            # (exact_cover_core) — по нему работает цикл точечных сдвигов
            # данных (методика unsat-core синтеза UniA, патчи v18/v21).
            assume_lid: dict[int, str] = {}
            for lid, vs2 in per_leg.items():
                if lid in target and lid not in no_cand:
                    if exact:
                        b = m.new_bool_var(f"x_{lid}")
                        m.add(sum(vs2) == 1).only_enforce_if(b)
                        assume_lid[b.index] = lid
                        m.add_assumption(b)
                    elif lid in hard_cover:
                        # двойное покрытие нелегально при любом
                        # владельце (0055) — жёсткое «ровно один»
                        if valve_diag:
                            bv = m.new_bool_var(f"x_{lid}")
                            m.add(sum(vs2) == 1).only_enforce_if(bv)
                            assume_lid[bv.index] = lid
                            m.add_assumption(bv)
                        else:
                            m.add(sum(vs2) == 1)
                    else:
                        e = m.new_int_var(0, len(vs2), f"e{lid}")
                        if valve_diag:
                            # Диагностика неразрешимости клапана
                            # (PAIRING_VALVE_CORE=1): покрытие каждого рейса
                            # — предположением; при INFEASIBLE CP-SAT
                            # называет ядро поимённо, как в точной фазе.
                            bv = m.new_bool_var(f"x_{lid}")
                            m.add(sum(vs2) == 1 + e).only_enforce_if(bv)
                            assume_lid[bv.index] = lid
                            m.add_assumption(bv)
                        else:
                            m.add(sum(vs2) == 1 + e)
                        pen.append(w_excess
                                   * self.crew_of_leg.get(lid, 1) * e)
                else:
                    m.add_at_most_one(vs2)
            # Лимиты перевозки экипажа пассажиром. Число наложенных
            # ограничений идёт в отчёт: ноль нарушений при нуле
            # наложенных ограничений — это проверка вхолостую, а не
            # соблюдённое правило.
            dh_groups = [([v[ci] for ci in grp], cap)
                         for grp, cap in prep_dh_groups]
            for vs3, cap in dh_groups:
                m.add(sum(vs3) <= cap)
            self.report["dh_limit_constraints"] = len(dh_groups)
            # Балансировка стартовых баз (--base-day-cap, виток «путь к
            # нулю» 2026-08-26): суммарные календарные дни цепочек,
            # СТАРТУЮЩИХ с базы, не превышают её ёмкости (дни на роль,
            # производная штата пакета). Невыполнимость — честный
            # INFEASIBLE/ядро, не молчаливое ослабление.
            if self.base_day_cap:
                ncap = 0
                for b2, items2 in prep_base_cap.items():
                    m.add(sum(s2 * v[ci] for s2, ci in items2)
                          <= self.base_cap_eff(b2))
                    ncap += 1
                self.report["base_day_cap_constraints"] = ncap
            if exact and self.warm_keys:
                matched = 0
                for ci, c in enumerate(cands):
                    if frozenset(c["legs"]) in self.warm_keys:
                        m.add_hint(v[ci], 1)
                        matched += 1
                self.report["warm_hints_matched"] = matched
                self.report["warm_hints_total"] = len(self.warm_keys)
                print("тёплый старт: подсказано %d кандидатов из %d "
                      "цепочек прежнего каталога"
                      % (matched, len(self.warm_keys)), flush=True)
            # Перегон СВОИМ рейсом допустим только если этот рейс в том
            # же решении кто-то выполняет: иначе экипаж летит пассажиром
            # рейсом, который некому вести. Транспортные рейсы (чужой
            # перевозчик) под это условие не подпадают — их выполняет не
            # наша авиакомпания. Ограничение связывает НАБОР цепочек,
            # поэтому живёт здесь, а не в проверке при выпуске.
            n_ghost = 0
            for lid, didx in prep_dh_idx.items():
                if lid in fixed_res["cov"]:
                    continue      # рейс выполняет закреплённая цепочка (A5)
                wvs = [v[ci] for ci in prep_per_leg.get(lid) or ()]
                # Классовый фикс №30 (слово владельца 2026-08-28):
                # прежняя форма несла полную сумму исполнителей в
                # КАЖДОМ ограничении перегона - модель квадратична по
                # слагаемым; на пуле, раздутом эскалацией, построение
                # уходило в часы до входа в решатель (№29).
                # Вспомогательная «рейс покрыт» делает размер
                # линейным; пространство решений по кандидатам и цель
                # тождественны: y=1 достижимо ровно тогда, когда рейс
                # кем-то выполняется.
                if not wvs:
                    for ci in didx:
                        m.add(v[ci] == 0)
                    n_ghost += 1
                    continue
                if didx:
                    y = m.new_bool_var(f"ycov_{lid}")
                    m.add(y <= sum(wvs))
                    for ci in didx:
                        m.add(v[ci] <= y)
                n_ghost += 1
            self.report["dh_requires_covered_leg"] = n_ghost
            m.minimize(sum(c["cost"] * x for c, x in zip(cands, v))
                       + sum(pen))
            import time as _tm2
            print("веха [%s] build_and_solve(exact=%s): модель готова, "
                  "вход в solve" % (_tm2.strftime("%H:%M:%S"), exact),
                  flush=True)
            s = cp_model.CpSolver()
            s.parameters.random_seed = self.args.seed
            if _deterministic(self.args):
                # Д1: счётный предел (детерминированное время CP-SAT)
                # вместо стенных часов и один поток поиска — два запуска
                # на одном входе обязаны дать один выход.
                s.parameters.max_deterministic_time = float(
                    self.args.deterministic_time)
                s.parameters.num_search_workers = 1
            else:
                s.parameters.max_time_in_seconds = solve_s
                s.parameters.num_search_workers = self.args.workers
            # Настроечный блок (авторизация владельца 2026-08-28),
            # PAIRING_CPSAT_TUNE=1; по умолчанию ВЫКЛЮЧЕН — байт-в-байт
            # прежнее поведение. Четыре рычага под слабые места линии:
            # бережная починка подсказки тёплого старта, усиленная
            # линеаризация (оценка снизу без предобработки нищая),
            # спуск через ядра (цель — большая сумма по колонкам),
            # ограничение полных потоков (клон модели ~1,8 ГБ на поток
            # без предобработки — освобождает место лёгким потокам).
            if os.environ.get("PAIRING_CPSAT_TUNE") == "1":
                s.parameters.repair_hint = True
                s.parameters.linearization_level = 2
                s.parameters.optimize_with_core = True
                _nfs = os.environ.get("PAIRING_FULL_SUBSOLVERS")
                if _nfs:
                    s.parameters.num_full_subsolvers = int(_nfs)
                self.report["cpsat_tune"] = {
                    "repair_hint": True, "linearization_level": 2,
                    "optimize_with_core": True,
                    "num_full_subsolvers": int(_nfs) if _nfs else None}
                print("настройка CP-SAT: repair_hint, linearization=2, "
                      "optimize_with_core, full_subsolvers=%s"
                      % (_nfs or "умолчание"), flush=True)
            # Управление предобработкой CP-SAT. ПРИЧИНА (доказана
            # стеками сторожа 2026-08-26): на морфологии этой модели
            # (сотни тысяч булевых переменных, тысячи «ровно один» по
            # сотням слагаемых, тысячи импликаций) однопоточный presolve
            # уходит в патологический режим — 13-16 ГБ памяти, часы без
            # выхода в параллельный поиск; лимит времени внутри его
            # проходов проверяется редко. Зондирование — главный
            # подозреваемый; итерации ограничиваются, чтобы предел был
            # у ЛЮБОГО прохода.
            if getattr(self.args, "no_presolve", False):
                s.parameters.cp_model_presolve = False
            if getattr(self.args, "solver_log", False):
                # Журнал самого CP-SAT: presolve печатает статистику по
                # каждому проходу — виновник тяжести называется
                # поимённо, без гипотез (совет CP-SAT Primer).
                s.parameters.log_search_progress = True
                s.log_callback = lambda m: print("cp-sat| " + m,
                                                 flush=True)
            gap = getattr(self.args, "gap_limit", 0.0) or 0.0
            if gap > 0:
                # Порог зазора: остановиться, когда доказано, что решение
                # не дальше gap от оптимума. Прошлые прогоны находили
                # решение за десять минут и час доказывали оптимальность
                # (зазор 1,3% на 531-й секунде) — допустимость правил
                # порог не трогает, торгуется только хвост стоимости.
                s.parameters.relative_gap_limit = gap
            from _opt_progress import ProgressWriter
            chk = None
            if not exact and getattr(self.args, "out", None):
                chk = (str(Path(str(self.args.out)).with_suffix(""))
                       + "-cover-checkpoint.json",
                       v, cands, self.duties)
            st = s.solve(m, _Progress(
                "точного покрытия" if exact else "покрытия с клапаном",
                writer=ProgressWriter(self.args.label, "cover"),
                checkpoint=chk))
            print("веха [%s] build_and_solve(exact=%s): выход из solve, "
                  "статус %s" % (_tm2.strftime("%H:%M:%S"), exact,
                                 s.status_name(st)), flush=True)
            return st, s, v, assume_lid, m

        solve_s = self.args.solve_s or self.args.budget_s
        self.report["solve_seconds"] = solve_s
        # Один поток со стенными часами воспроизводим лишь при равном
        # времени; настоящий детерминизм — профиль Д1 (счётный предел).
        self.report["solver_deterministic"] = (self.args.workers == 1)
        self.report["deterministic_profile"] = deterministic_profile(
            self.args)
        print("этап: выбор из %d кандидатов, до %d с..."
              % (len(cands), solve_s), flush=True)
        t0 = _now_s()
        status, solver, xs, assume_lid, m_exact = build_and_solve(
            exact=True, ban_groups=ban_groups)
        self.report["exact_cover_status"] = solver.status_name(status)
        print("этап: точное покрытие — %s (%.0f с)"
              % (solver.status_name(status), _now_s() - t0), flush=True)
        if status == cp_model.INFEASIBLE:
            # Ядро неразрешимости — ПОИМЁННО в машинный отчёт: участки,
            # чьи «ровно один» несовместимы (по нему работает цикл
            # точечных сдвигов данных синтеза UniA). «Достаточное» ядро
            # CP-SAT бывает тривиально огромным (все предположения) —
            # минимизируем: core-of-core до неподвижной точки, затем
            # удаление по одному с бюджетом вызовов. Каждый повторный
            # solve точной фазы — секунды (доказательство INFEASIBLE
            # дёшево), бюджет ограничивает худший случай.
            core_idx = list(solver.sufficient_assumptions_for_infeasibility())
            mdl = m_exact
            def _resolve(assum, limit_s=30.0):
                import time as _tm3
                print("веха [%s] минимизация ядра: перерешивание с "
                      "%d предположениями"
                      % (_tm3.strftime("%H:%M:%S"), len(assum)),
                      flush=True)
                mdl.clear_assumptions()
                mdl.proto.assumptions.extend(assum)
                s2 = cp_model.CpSolver()
                if _deterministic(self.args):
                    s2.parameters.max_deterministic_time = float(
                        self.args.deterministic_time)
                else:
                    s2.parameters.max_time_in_seconds = limit_s
                s2.parameters.num_search_workers = 1
                s2.parameters.random_seed = self.args.seed
                # Перерешивание обязано наследовать лечение причины:
                # без этого каждое из них заново запускает тот самый
                # патологический presolve (раздувание 13 ГБ на
                # минимизации ядра, 2026-08-26).
                if getattr(self.args, "no_presolve", False):
                    s2.parameters.cp_model_presolve = False
                st2 = s2.solve(mdl)
                return st2, s2
            # Минимизация ядра стоит до 66 повторных решений по 30 с —
            # для часового счёта это ценная диагностика, для чернового
            # смок-прогона бессмысленная трата: --core-min-budget 0
            # оставляет сырое ядро; стену времени держит метод.
            core_idx = self._minimize_core(
                core_idx, _resolve,
                lambda s2: s2.sufficient_assumptions_for_infeasibility(),
                cp_model.INFEASIBLE)
            core = sorted(assume_lid[i] for i in core_idx if i in assume_lid)
            self.report["exact_cover_core"] = core
            print("ядро неразрешимости точного покрытия: %d участков%s"
                  % (len(core), (": " + ", ".join(core[:8]) +
                     ("..." if len(core) > 8 else "")) if core else ""),
                  flush=True)
            # Клапан двойного покрытия открывается только по ДОКАЗАННОЙ
            # неразрешимости точной модели. UNKNOWN (таймаут) — не
            # доказательство: ослаблять постановку из-за нехватки времени
            # значит молча подменять задачу (внешний аудит R6, P0-05).
            print("ВНИМАНИЕ: точное покрытие доказуемо неразрешимо — "
                  "включён клапан двойного покрытия. Постановка ослаблена, "
                  "и это надо учитывать при сравнении.", flush=True)
            banned_pairs: list = []
            prev_pick: list = []
            # Тёплый старт и для ослабленного режима (2026-08-28):
            # прежний каталог — законный полный план, решателю он
            # даёт стартовую точку вместо блужданий сверху; критерий
            # 0096 делает нижнюю оценку слабой, и без старта решатель
            # час не достигал даже уровня прежнего плана.
            warm_pick: list = []
            if self.warm_keys:
                warm_pick = [i for i, c in enumerate(cands)
                             if frozenset(c["legs"]) in self.warm_keys]
                if warm_pick:
                    print("тёплый старт ослабленного режима: подсказано "
                          "%d кандидатов из %d цепочек прежнего каталога"
                          % (len(warm_pick), len(self.warm_keys)),
                          flush=True)
            for _round in range(3):
                status, solver, xs, assume_v, _ = build_and_solve(
                    exact=False, ban_pairs=banned_pairs,
                    hint_idx=prev_pick or warm_pick or None,
                    ban_groups=ban_groups)
                if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                    if status == cp_model.INFEASIBLE and assume_v:
                        core = sorted(
                            assume_v.get(i, "?") for i in
                            solver.sufficient_assumptions_for_infeasibility())
                        self.report["valve_infeasible_core"] = core
                        print("ядро неразрешимости клапана: %d участков: %s"
                              % (len(core), ", ".join(core[:40])),
                              flush=True)
                    break
                prev_pick = [i for i, x in enumerate(xs)
                             if solver.value(x)]
                bad = self._bad_0055_pairs(prev_pick, cands)
                if not bad:
                    break
                print("этап: перерешивание клапана — запрещено пар "
                      "с неразрешимым 0055-конфликтом: %d" % len(bad),
                      flush=True)
                banned_pairs.extend(bad)
            self.report["dh_gap_pairs_banned"] = len(banned_pairs)
            self.report["exact_cover"] = False
        elif status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print("ОТКАЗ: точное покрытие не решено (%s) за %d с — это "
                  "нехватка бюджета, а не доказанная неразрешимость. "
                  "Каталог не публикуется; увеличьте --solve-s."
                  % (solver.status_name(status), solve_s), flush=True)
            self.report["exact_cover"] = False
            self.report["exact_cover_unresolved"] = True
            return []
        else:
            self.report["exact_cover"] = True
        self.report["solver_status"] = solver.status_name(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return []
        import time as _tm4
        print("веха [%s] извлечение выбранных цепочек"
              % _tm4.strftime("%H:%M:%S"), flush=True)
        chosen = [c for c, x in zip(cands, xs) if solver.value(x)]
        print("веха [%s] выбрано цепочек: %d"
              % (_tm4.strftime("%H:%M:%S"), len(chosen)), flush=True)
        chosen.extend(fixed)
        return chosen


    def _bad_0055_pairs(self, picked, cands):
        """Пары выбранных кандидатов, чьё двойное покрытие нелегально
        при любом владельце: проигравший получает изъятый участок
        перегоном, и интервал соседних перегонов рушится в обе стороны
        (правило 0055). Ленивое перерешивание клапана запрещает такие
        пары совместно."""
        def loser_ok(c_loser, lid):
            act = set(c_loser["legs"]) - {lid}
            if not act:
                return True
            chain = []
            for di in c_loser["duties"]:
                g = [l for l in self.duties[di]["legs"]
                     if l["id"] in act]
                if g:
                    chain.append({**self.duties[di],
                                  "legs": sorted(
                                      g, key=lambda l: l["t0"])})
            chain.sort(key=lambda d: d["legs"][0]["t0"])
            act_sorted = sorted(act, key=lambda x: self.legs[x]["t0"])
            extras = sorted(set(c_loser["dh"]) | {lid},
                            key=lambda x: self.legs[x]["t0"])
            first, last = act_sorted[0], act_sorted[-1]
            kept = []
            for x in extras:
                if self.legs[first]["dep"] in self.bases                         and self.legs[x]["t1"] <= self.legs[first]["t0"]:
                    continue
                if self.legs[last]["arr"] in self.bases                         and self.legs[x]["t0"] >= self.legs[last]["t1"]:
                    continue
                kept.append(x)
            return self.adjacent_dh_ok(
                chain, [self.legs[x] for x in kept])

        by_leg: dict = {}
        for ci in picked:
            for lid in cands[ci]["legs"]:
                by_leg.setdefault(lid, []).append(ci)
        bad = set()
        for lid, cis in by_leg.items():
            if len(cis) < 2:
                continue
            for a in range(len(cis)):
                for b in range(a + 1, len(cis)):
                    i, j = cis[a], cis[b]
                    if not loser_ok(cands[j], lid)                             and not loser_ok(cands[i], lid):
                        bad.add((min(i, j), max(i, j)))
        return sorted(bad)

    # RULE: 0079 -- каждый непокрытый рейс объясняется поимённо
    def data_fingerprint(self) -> str:
        """Отпечаток физических фактов набора (общий контракт A4): им
        подписывается каждое доказательство невозможности, и потребитель
        (гейт этапа 2, проверка покрытия) принимает доказательство только
        при равенстве отпечатков — на других данных оно недействительно."""
        fp = getattr(self, "_data_fp", None)
        if fp is None:
            from _coverage_validator import payload_data_fingerprint
            fp = self._data_fp = payload_data_fingerprint(self.payload)
        return fp

    def proof_horizon(self) -> dict:
        """Горизонт, в пределах которого выведено доказательство: размах
        командировки, базы и границы данных — чтобы читатель видел, о
        каком пространстве утверждение."""
        a = self.args
        legs = self.sorted_legs
        return {"pairing_span_min": a.pairing_span,
                "bases": sorted(self.bases),
                "data_from": (legs[0]["t0"].isoformat(timespec="minutes")
                              if legs else None),
                "data_to": (max(l["t1"] for l in legs)
                            .isoformat(timespec="minutes") if legs else None)}

    def station_reachability(self, lid: str) -> dict:
        """Необходимое условие покрытия участка (аудит A4, 04.09.2026):
        станция вылета достижима из какой-либо базы, а после прилёта
        достижима база, — обходом графа РЕЙСОВ (все рейсы набора, включая
        транспортные, — ими экипаж едет пассажиром) БЕЗ предела числа
        перегонов, в пределах размаха командировки вокруг участка.

        Класс ошибки A4: прежний зонд `dh_options` (прямой перегон либо
        одна пересадка со стыковкой 60–1440 мин) объявлял «нет пары»
        участок, до станции которого можно долететь РАБОТАЯ по цепочке
        база→X→Y→Z, и это шло в каталог как доказательство.

        Ослабление честное в одну сторону: между соседними рейсами
        требуется лишь неотрицательный зазор, а прилёт последнего рейса
        перед участком и вылет первого после — те же границы, что у
        обхода построителя (стыковка / дебрифинг + посадка). Любой
        законный маршрут — путь в этом графе, поэтому ПУСТОТА графа —
        строгое доказательство невозможности; непустота ничего не
        доказывает (правила отдыха и пределы смены здесь не судятся).

        Возврат: {"reach": bool, "back": bool, "visited": n}."""
        a = self.args
        l = self.legs[lid]
        by_arr = getattr(self, "_by_arr_all", None)
        if by_arr is None:
            by_arr = self._by_arr_all = defaultdict(list)
            self._by_dep_all = defaultdict(list)
            for x in self.sorted_legs:
                by_arr[x["arr"]].append(x)
                self._by_dep_all[x["dep"]].append(x)
        by_dep = self._by_dep_all
        pre_gap = timedelta(minutes=a.sit_min if self.rules is not None
                            else a.min_rest_sit)
        debrief = timedelta(minutes=self.debriefing_of.get(lid) or 0) \
            if self.rules is not None else timedelta(0)
        lo = l["t0"] - timedelta(minutes=a.pairing_span)
        hi = l["t0"] + timedelta(minutes=a.pairing_span)
        visited = 0

        def _walk(first, nxt, is_home) -> bool:
            nonlocal visited
            seen: set = set()
            stack = list(first)
            while stack:
                x = stack.pop()
                if x["id"] in seen:
                    continue
                seen.add(x["id"])
                visited += 1
                if is_home(x):
                    return True
                stack.extend(y for y in nxt(x) if y["id"] not in seen)
            return False

        # назад: рейсы, прилетающие на станцию вылета участка не позже
        # крайней стыковки; их предшественники — рейсы, прилетающие на их
        # станцию вылета не позже их вылета; всё — не раньше начала размаха
        def back_from(station, limit):
            return [x for x in by_arr.get(station, ())
                    if x["t1"] <= limit and x["t0"] >= lo]

        # вперёд: рейсы, вылетающие со станции прилёта участка не раньше
        # конца дебрифинга плюс зазор посадки; их преемники — не раньше
        # их прилёта; всё — не позже конца размаха
        board = timedelta(minutes=self.dh_board_min())

        def fwd_from(station, limit):
            return [x for x in by_dep.get(station, ())
                    if x["t0"] >= limit and x["t1"] <= hi]

        reach = l["dep"] in self.bases or _walk(
            back_from(l["dep"], l["t0"] - pre_gap),
            lambda x: back_from(x["dep"], x["t0"]),
            lambda x: x["dep"] in self.bases)
        back = l["arr"] in self.bases or _walk(
            fwd_from(l["arr"], l["t1"] + debrief + board),
            lambda x: fwd_from(x["arr"], x["t1"]),
            lambda x: x["arr"] in self.bases)
        return {"reach": reach, "back": back, "visited": visited}

    def classify_uncovered(self, uncovered: set[str]) -> dict:
        """Разделить непокрытые участки по ТРЁМ исходам (аудит A4,
        04.09.2026): «доказано невозможно» — с приложенным доказательством
        (недостижимость по графу рейсов либо доказательная ступень
        эскалации); «поиск не завершён» — эскалация упёрлась в предел
        глубины (усечения) или времени; «не найдено» — вариант не найден в
        проверенном пространстве (эскалация выключена или участка не
        касалась). Второй и третий исходы — `not_built` с причиной
        словами: они НЕ прощаются потребителями и держат вердикт
        INCOMPLETE_SEARCH.

        Класс ошибки A4: отрицательный ответ эвристического зонда
        (`dh_options`, одна пересадка) выдавался за доказательство; строгое
        доказательство ступени 3 эскалации (esc["proved"]) в список не
        попадало, а срыв по бюджету (esc["budget_hit"]) всё равно
        объявлялся «невозможно»."""
        # Честное имя потери (разбор №24): участок, чьё единственное
        # покрытие исключено арбитражем 0055, — отдельная категория:
        # это не «нет пары» (конструкция была) и не «перебор не нашёл»
        # (нашёл и выбрал) — это осознанная жертва конфликта за
        # заброску, и лечится она не расширением генерации, а данными.
        # Доказательством невозможности она тоже НЕ является.
        arb = sorted(uncovered & getattr(self, "_arb_lost_legs", set()))
        esc = self.report.get("escalation") or {}
        esc_proved = {p.get("leg"): p for p in (esc.get("proved") or ())
                      if isinstance(p, dict)}
        esc_budget = set(esc.get("budget_hit") or ())
        esc_trunc = {p.get("leg"): p for p in (esc.get("not_certified")
                                                or ()) if isinstance(p, dict)}
        fp = self.data_fingerprint()
        horizon = self.proof_horizon()
        proofs: list[dict] = []
        missed: list[str] = []
        reasons: dict[str, dict] = {}
        for lid in sorted(uncovered - set(arb)):
            l = self.legs[lid]
            r = self.station_reachability(lid)
            if not r["reach"] or not r["back"]:
                side = []
                if not r["reach"]:
                    side.append(f"станция вылета {l['dep']} недостижима из "
                                f"баз в пределах размаха")
                if not r["back"]:
                    side.append(f"после прилёта в {l['arr']} база "
                                f"недостижима в пределах размаха")
                proofs.append({"leg": lid, "kind": "graph_unreachable",
                               "detail": "; ".join(side)
                               + " (обход графа рейсов без предела числа "
                                 "перегонов)",
                               "horizon": horizon, "data_fingerprint": fp})
            elif lid in esc_proved:
                p = esc_proved[lid]
                proofs.append({"leg": lid, "kind": "escalation_proved",
                               "detail": str(p.get("note") or "")
                               + f" (окно {p.get('window_duties')} смен)",
                               "horizon": horizon, "data_fingerprint": fp})
            else:
                if lid in esc_budget:
                    reason, detail = "предел времени", (
                        "доказательная ступень эскалации не уложилась в "
                        "бюджет — пространство обойдено не полностью")
                elif lid in esc_trunc:
                    reason, detail = "предел глубины", (
                        "обход окна с усечениями: "
                        + ", ".join(esc_trunc[lid].get("truncations") or ()))
                else:
                    reason, detail = (
                        "вариант не найден в проверенном пространстве",
                        "перебор с текущими пределами цепочки не построил; "
                        "доказательной ступени по участку не было")
                missed.append(lid)
                reasons[lid] = {"search_incomplete": True,
                                "reason": reason, "detail": detail}
        no_pair = [p["leg"] for p in proofs]
        return {"uncovered_no_pair_in_horizon": len(no_pair),
                "uncovered_not_built": len(missed),
                # ПОИМЁННЫЙ список доказуемо непокрываемых — контракт
                # прощения этапа 2 (proven_impossible_legs); с 04.09.2026
                # (аудит A4) каждому имени соответствует запись
                # доказательства в proven_impossible_proofs, и потребитель
                # прощает ТОЛЬКО по записи с совпавшим отпечатком данных.
                "proven_impossible_legs": sorted(no_pair),
                "proven_impossible_proofs": proofs,
                # Не доказанные — тоже ПОИМЁННО, с причиной словами: цикл
                # точечных сдвигов данных (методика unsat-core синтеза
                # UniA) работает по этому списку; десять примеров для
                # глаза остаются ниже.
                "uncovered_not_built_legs": sorted(missed),
                "uncovered_not_built_reasons": reasons,
                "search_incomplete": bool(missed),
                "uncovered_arbitration_0055": len(arb),
                "uncovered_arbitration_0055_legs": arb,
                "uncovered_examples": (no_pair + missed + arb)[:10]}

    def _arb_apply_keep(self, _idx, excl, participants, chosen,
                        claims, owner, unresolved, dh_ok) -> bool:
        """Применить исход клапана: исключённые отдают владение,
        спорные участки переходят живым претендентам (предпочтение
        длинной цепочке). Истина сделки — легальность ВСЕХ затронутых
        цепочек. Возврат False = сделка нелегальна; откат владения —
        у вызывающего по сохранённой копии."""
        dead = set(excl) | set(unresolved)
        touched = {j for j in participants if j not in dead}
        for j in excl:
            for x in chosen[j]["legs"]:
                if owner.get(x) == j:
                    rest = [k for k in claims[x] if k not in dead]
                    if rest:
                        owner[x] = max(rest, key=lambda k:
                                       len(chosen[k]["legs"]))
                        touched.add(owner[x])
        return all(dh_ok(j) for j in touched)

    def _arb_best_combo(self, participants, chosen, claims, owner,
                        unresolved, dh_ok, loss, repair):
        """Лучшая развязка клапана ПОЛНЫМ перебором подмножеств
        исключаемых участников (разбор №28: бинарное «я против всех»
        теряло покрытие на многосторонних клапанах). Возврат:
        (исключаемые, цепочки-замены, непокрытый остаток) либо None.
        Мерило лексикографическое: сперва потеря покрытия, затем
        цена (стоимость замен минус стоимость исключённых)."""
        import itertools
        best = None
        base_owner = dict(owner)
        for r in range(1, len(participants)):
            for excl in itertools.combinations(participants, r):
                excl_s = set(excl)
                owner.clear()
                owner.update(base_owner)
                if not self._arb_apply_keep(
                        None, excl_s, participants, chosen, claims,
                        owner, unresolved, dh_ok):
                    continue
                adds, rest = repair(excl_s)
                delta = (sum(a["cost"] for a in adds)
                         - sum(chosen[j]["cost"] for j in excl_s))
                score = (len(rest), delta)
                if best is None or score < best[0]:
                    best = (score, (excl_s, adds, rest))
        owner.clear()
        owner.update(base_owner)
        return best[1] if best else None

    # --- каталог --------------------------------------------------------
    # RULE: none -- выпуск каталога: форматирование результата, отбор уже произошёл; отказы внутри относятся к разбору станций, а не к правилам
    def to_catalog(self, chosen: list[dict], label: str) -> dict:
        owner: dict[str, int] = {}
        for idx, c in enumerate(chosen):
            for lid in c["legs"]:
                cur = owner.get(lid)
                if cur is None or len(chosen[idx]["legs"]) > \
                        len(chosen[cur]["legs"]):
                    owner[lid] = idx
        # Арбитраж с оглядкой на 0055 (суд 2026-08-27): изъятый участок
        # остаётся у проигравшего ПЕРЕГОНОМ, и тот перегон обязан
        # держать интервал соседних DH. Нарушение лечится сменой
        # владельца спорного участка, если у обеих цепочек после
        # обмена перегоны легальны; неразрешимое — поимённо в отчёт
        # и в счётчик (публикацию заворачивает вердикт).
        claims: dict[str, list[int]] = {}
        for idx, c in enumerate(chosen):
            for lid in c["legs"]:
                claims.setdefault(lid, []).append(idx)

        def _trim_extras(act_sorted, extras):
            """Перегоны до первого рабочего, вылетающего из базы, и
            после последнего, прилетающего в базу, — не нужны: экипаж
            стартует/заканчивает прямо в базе (типовой случай арбитража:
            проигравшему заброска не нужна вовсе)."""
            if not act_sorted:
                return []
            first, last = act_sorted[0], act_sorted[-1]
            out = []
            for x in extras:
                if self.legs[first]["dep"] in self.bases \
                        and self.legs[x]["t1"] <= self.legs[first]["t0"]:
                    continue
                if self.legs[last]["arr"] in self.bases \
                        and self.legs[x]["t0"] >= self.legs[last]["t1"]:
                    continue
                out.append(x)
            return out

        def _dh_ok(idx: int) -> bool:
            c = chosen[idx]
            act = {lid for lid in c["legs"] if owner[lid] == idx}
            if not act:
                return True
            lost_ = [lid for lid in c["legs"] if owner[lid] != idx]
            # контракт adjacent_dh_ok: цепочка — список СМЕН с legs
            chain = []
            for di in c["duties"]:
                g = [l for l in self.duties[di]["legs"]
                     if l["id"] in act]
                if g:
                    # псевдо-смена: поля исходной смены (span/work/night
                    # нужны формуле отдыха), участки — только владельца
                    chain.append({**self.duties[di],
                                  "legs": sorted(
                                      g, key=lambda l: l["t0"])})
            chain.sort(key=lambda d: d["legs"][0]["t0"])
            act_sorted = sorted(act, key=lambda x: self.legs[x]["t0"])
            extras = _trim_extras(act_sorted,
                                  sorted(set(c["dh"]) | set(lost_),
                                         key=lambda x: self.legs[x]["t0"]))
            dh_legs = [self.legs[x] for x in extras]
            return self.adjacent_dh_ok(chain, dh_legs)

        unresolved_0055 = []
        reresolved = []
        # Группы для петли «отбор - арбитраж» (разбор №28):
        # участники клапана, чья лучшая развязка всё же теряет
        # покрытие, — кандидаты на запрет совместного выбора.
        self._arb_conflict_groups = []

        def _ident(c):
            return (frozenset(c["legs"]), frozenset(c["dh"]))

        def _mark_group(parts, rest):
            if rest:
                self._arb_conflict_groups.append(
                    frozenset(_ident(chosen[j]) for j in parts))
        for idx, c in enumerate(chosen):
            if idx in unresolved_0055 or _dh_ok(idx):
                continue
            fixed = False
            for lid in [x for x in c["legs"] if owner[x] != idx]:
                prev = owner[lid]
                owner[lid] = idx
                # Опустевшая после обмена цепочка — законный исход: её
                # вклад в покрытие полностью замещён новым владельцем,
                # публикация её просто пропустит (случай из суда
                # 2026-08-27).
                others_ok = all(_dh_ok(j) for j in claims[lid] if j != idx)
                # Полное владение = первозданная форма кандидата,
                # легальная по построению пула — проверять нечего.
                whole = all(owner[x] == idx for x in c["legs"])
                if others_ok and (whole or _dh_ok(idx)):
                    fixed = True
                    break
                owner[lid] = prev
            if fixed:
                continue
            # Перерешение по минимуму потери покрытия (суд 2026-08-28,
            # разбор №24): исключение цепочки целиком — тоже ход
            # арбитража, и выбор, КОГО исключать, обязан считать цену
            # покрытием, а не порядком обхода. Сравниваются два исхода:
            # исключить ЭТУ цепочку (прежнее поведение) либо исключить
            # конкурентов за её потерянные участки, вернув ей полное
            # владение (первозданная форма легальна по построению).
            # Мерило — рейсы, теряющие ЕДИНСТВЕННОЕ покрытие: покрытие
            # лексикографически первое (в №24 исключение экспоната
            # стоило двух рейсов ради одного — конфликт за единственную
            # заброску).
            rivals = sorted({j for x in c["legs"] if owner[x] != idx
                             for j in claims[x] if j != idx})

            def _loss(excl: set) -> list:
                dead = set(excl) | set(unresolved_0055)
                return sorted(l2 for l2, js in claims.items()
                              if js and all(j in dead for j in js))

            def _repair(excl: set) -> tuple[list, list]:
                """Докрытие потерь исхода запасными цепочками пула.

                Разбор №25 (2026-08-28): простая ничья потерь 2=2 не
                решается обменом — эталон побеждает её тем, что кроет
                потерянный рейс ДРУГОЙ цепочкой. Замена берётся из
                пула кандидатов (легальны по построению) при жёстком
                условии: РАБОЧИЕ участки замены целиком в множестве
                потерь — никакого нового двойного покрытия. Жадный
                выбор: больше покрытых, при равенстве дешевле.
                Возврат: (цепочки-замены, непокрытый остаток)."""
                free = set(_loss(excl))
                if not free:
                    return [], []
                adds, still = [], set(free)
                pool = [r for r in self.candidates.values()
                        if set(r["legs"]) <= free]
                while still and pool:
                    best = None
                    for r in pool:
                        gain = len(set(r["legs"]) & still)
                        if gain and (best is None or
                                     (gain, -r["cost"]) > best[0]):
                            best = ((gain, -r["cost"]), r)
                    if best is None:
                        break
                    adds.append(best[1])
                    still -= set(best[1]["legs"])
                return adds, sorted(still)

            # МНОГОСТОРОННИЙ перебор (разбор №28, 2026-08-28): клапан
            # с тремя и более участниками решался бинарно «я против
            # всех конкурентов», и попарные решения теряли покрытие.
            # Теперь стороны перебираются ПОЛНОСТЬЮ: каждое
            # подмножество участников — кандидат в исключение; мерило
            # лексикографическое — потеря покрытия, затем цена
            # (стоимость замен минус стоимость исключённых). Перебор
            # ограничен 2^8 комбинаций; больший клапан — прежняя
            # бинарная развязка (честно, в отчёт).
            participants = [idx] + rivals
            combo = self._arb_best_combo(
                participants, chosen, claims, owner,
                unresolved_0055, _dh_ok, _loss, _repair) \
                if rivals and len(participants) <= 8 else None
            if combo is not None:
                excl, adds_c, rest_c = combo
                saved = dict(owner)
                ok = self._arb_apply_keep(
                    idx, excl, participants, chosen, claims, owner,
                    unresolved_0055, _dh_ok)
                if ok:
                    unresolved_0055.extend(sorted(excl))
                    for r in adds_c:
                        chosen.append(r)
                        j2 = len(chosen) - 1
                        for x in r["legs"]:
                            claims.setdefault(x, []).append(j2)
                            owner[x] = j2
                    reresolved.append(
                        {"kept": [j for j in participants
                                  if j not in excl],
                         "excluded": sorted(excl),
                         "combo_search": "полный перебор сторон",
                         "repair_added": len(adds_c),
                         "repair_added_legs": sorted(
                             x for r in adds_c for x in r["legs"]),
                         "cover_loss_kept": rest_c})
                    _mark_group(participants, rest_c)
                    continue
                owner.clear()
                owner.update(saved)
            loss_self = _loss({idx})
            loss_riv = _loss(set(rivals))
            adds_self, rest_self = _repair({idx})
            adds_riv, rest_riv = _repair(set(rivals))
            swap_ok = False
            if rivals and len(rest_riv) < len(rest_self):
                # Возврат полного владения и передел осиротевших
                # участков конкурентов оставшимся претендентам; сделка
                # честна, только если ВСЕ затронутые цепочки после
                # передела легальны — каскадов арбитраж не строит.
                saved = dict(owner)
                for x in c["legs"]:
                    owner[x] = idx
                touched = {idx}
                for j in rivals:
                    for x in chosen[j]["legs"]:
                        if owner[x] == j:
                            rest = [k for k in claims[x]
                                    if k != j and k not in rivals
                                    and k not in unresolved_0055]
                            if rest:
                                owner[x] = max(
                                    rest, key=lambda k:
                                    len(chosen[k]["legs"]))
                                touched.add(owner[x])
                if all(_dh_ok(j) for j in touched):
                    swap_ok = True
                    unresolved_0055.extend(rivals)
                    _mark_group([idx] + rivals, rest_riv)
                    # Замены встают в решение полноправными цепочками:
                    # владение целиком их, работа не пересекается ни с
                    # кем по построению (_repair: работа ⊆ потери).
                    for r in adds_riv:
                        chosen.append(r)
                        j2 = len(chosen) - 1
                        for x in r["legs"]:
                            claims.setdefault(x, []).append(j2)
                            owner[x] = j2
                    reresolved.append(
                        {"kept": idx, "excluded": rivals,
                         "repair_added": len(adds_riv),
                         "repair_added_legs": sorted(
                             x for r in adds_riv for x in r["legs"]),
                         "cover_loss_kept": rest_riv,
                         "cover_loss_alt": rest_self})
                else:
                    owner.clear()
                    owner.update(saved)
            if not swap_ok:
                unresolved_0055.append(idx)
                _mark_group([idx] + rivals, rest_self)
                # Потери прежнего исхода тоже докрываются из пула:
                # исключение цепочки не повод терять рейсы, для которых
                # есть непересекающаяся замена (разбор №25).
                for r in adds_self:
                    chosen.append(r)
                    j2 = len(chosen) - 1
                    for x in r["legs"]:
                        claims.setdefault(x, []).append(j2)
                        owner[x] = j2
                if adds_self:
                    reresolved.append(
                        {"kept": None, "excluded": [idx],
                         "repair_added": len(adds_self),
                         "repair_added_legs": sorted(
                             x for r in adds_self for x in r["legs"]),
                         "cover_loss_kept": rest_self,
                         "cover_loss_alt": rest_riv})
        self.report["dh_gap_arbitration_reresolved"] = reresolved
        # Рейсы, потерявшие ЕДИНСТВЕННОЕ покрытие из-за исключений
        # арбитража, — ПОИМЁННО: классификация непокрытых обязана
        # называть их своим именем, а не «нет пары в горизонте»
        # (ложное имя потери — разбор №24, 2026-08-28).
        self._arb_lost_legs = {
            l2 for l2, js in claims.items()
            if js and all(j in set(unresolved_0055) for j in js)}
        self.report["uncovered_by_arbitration_legs"] = sorted(
            self._arb_lost_legs)
        self.report["dh_gap_arbitration_unresolved"] = len(unresolved_0055)
        self.report["dh_gap_arbitration_unresolved_details"] = [
            {"chain_index": idx,
             "active": [x for x in chosen[idx]["legs"]
                        if owner[x] == idx],
             "lost_dh": [x for x in chosen[idx]["legs"]
                         if owner[x] != idx],
             "planned_dh": sorted(chosen[idx]["dh"])}
            for idx in unresolved_0055]
        if unresolved_0055:
            print("ВНИМАНИЕ: %d цепочек с неразрешимым 0055-конфликтом "
                  "арбитража двойного покрытия ИСКЛЮЧЕНЫ из каталога — "
                  "их участки честно в непокрытых; пары названы в "
                  "отчёте." % len(unresolved_0055), flush=True)

        selected, demoted = [], 0
        skip_0055 = set(unresolved_0055)
        for idx, c in enumerate(chosen):
            if idx in skip_0055:
                # Неразрешённый 0055-конфликт арбитража: публиковать
                # цепочку с нелегальной парой перегонов нельзя — она
                # исключается целиком, её участки честно уходят в
                # непокрытые (кнопки №19-№22: пары с зазором меньше
                # норматива четырежды доезжали до каталога этим путём).
                continue
            active = [lid for lid in c["legs"] if owner[lid] == idx]
            demoted += len(c["legs"]) - len(active)
            if not active:
                continue
            active.sort(key=lambda x: self.legs[x]["t0"])
            # Проигравший арбитраж двойного покрытия НЕ урезается — лег
            # остаётся в цепочке ПЕРЕГОНОМ: экипаж физически летит этим
            # рейсом (иначе станционный разрыв — потерянный крест трёх
            # рейсов из приёмки одного из прогонов). Рабочая роль —
            # только у владельца.
            lost = [lid for lid in c["legs"] if owner[lid] != idx]
            extras = _trim_extras(active,
                                  sorted(set(c["dh"]) | set(lost),
                                         key=lambda x: self.legs[x]["t0"]))
            fns = sorted(set(active) | set(extras),
                         key=lambda x: self.legs[x]["t0"])
            # Границы СМЕН цепочки. Без них потребитель видит командировку
            # как одну длинную смену: проверка предела рабочего времени,
            # числа посадок и стыковок теряет смысл, а уровень 2 на нашем
            # же каталоге отказывает громко — тогда как каталог импорта
            # эти границы несёт. Аудит построенного поймал на этом
            # сотни мнимых нарушений.
            keep = set(active)
            duty_fns = []
            for d in (self.duties[i] for i in c["duties"]):
                g = [l["id"] for l in d["legs"] if l["id"] in keep]
                if g:
                    duty_fns.append(sorted(
                        g, key=lambda x: self.legs[x]["t0"]))
            duty_fns.sort(key=lambda g: self.legs[g[0]]["t0"])
            need: Counter = Counter()
            for lid in active:
                for role, cnt in self.demand[lid].items():
                    need[role] = max(need[role], cnt)
            # за основу берём позиционный ряд мест самого ёмкого участка
            # цепочки и дополняем ролями, которых на нём меньше, чем нужно
            # другим участкам. Алфавитный порядок здесь недопустим: этап 2
            # сажает экипаж на места по позиции
            base = max((self.slots_of_leg.get(lid) or [] for lid in active),
                       key=len, default=[])
            seats: list[str] = list(base)
            have = Counter(seats)
            for role in sorted(need):
                if need[role] > have[role]:
                    seats.extend([role] * (need[role] - have[role]))
            # Численность по НАШЕМУ ярусу обязана дойти до состава мест, а
            # не только до стоимости: иначе каталог обещает этапу 2 меньше
            # людей, чем оплачено в цели, выгрузки несут состав источника,
            # а человеко-метрики считаются по чужому решению. Роль
            # дополнительного места выведена из данных: во всех флотах
            # набора усиление добавляет самую многочисленную роль состава
            # (проверено по модам составов на 3399 участках — всегда
            # линейный бортпроводник), уникальные роли не удваиваются.
            crew = int(c.get("crew") or 0)
            if crew > len(seats) and seats:
                roles = Counter(r for lid in active
                                for r in self.slots_of_leg.get(lid) or ())
                top = max(roles.values())
                # при равенстве счётчиков — роль МЛАДШЕЙ позиции состава:
                # позиционный ряд идёт от старших к линейным, и в данных
                # источника усиление добавляет именно хвостовую роль
                aug = max((r for r in seats if roles.get(r) == top),
                          key=seats.index, default=seats[-1])
                seats.extend([aug] * (crew - len(seats)))
            if self.conditions is not None:
                # Бортпроводник с допуском в бизнес-класс обязателен на
                # рейсах I–II группы, причём старший бортпроводник в счёт
                # не идёт (П.1.9). Группа определяется длительностью рейса
                # (условия, группа B: границы групп в документе
                # пересекаются). Если такого места в составе нет — оно
                # добавляется, а не подменяет линейное: подмена уменьшила
                # бы численность, уже оплаченную в цели.
                long_leg = any(
                    self.conditions.c_class_required(
                        (self.legs[x]["t1"] - self.legs[x]["t0"]
                         ).total_seconds() / 60) for x in active)
                if long_leg and not any("-cl" in str(s) for s in seats):
                    role = self._c_class_role()
                    if role is None:
                        # Молчаливый сон незаконен: условие заказчика
                        # активно, а имени роли нет ни в данных, ни в
                        # условиях — выпуск отказывает словами.
                        raise BuilderConfigError(
                            "роль бортпроводника бизнес-класса требуется "
                            "условием П.1.9, но ни в наборе (роль с "
                            "суффиксом «-cl» в спросе), ни в условиях "
                            "(ключ role узла c_class_attendant_required) "
                            "она не названа")
                    seats.append(role)
            acs = sorted({self.legs[x]["fleet"] for x in active})
            req_all = sorted({q for x in active for q in self.legs[x]["req"]})
            lo = min(int(self.legs[x]["t0"].timestamp() // 60) for x in fns)
            hi = max(int(self.legs[x]["t1"].timestamp() // 60) for x in fns)
            block: dict[str, int] = {}
            for x in active:
                l = self.legs[x]
                key = (l["t0"] + self.tz).strftime("%Y-%m")
                block[key] = block.get(key, 0) + int(
                    (l["t1"] - l["t0"]).total_seconds() // 60)
            windows: dict = {}
            for x in fns:
                l = self.legs[x]
                day = (l["t0"] + self.tz).date()
                w0, w1 = windows.get(day, (l["t0"], l["t1"]))
                windows[day] = (min(w0, l["t0"]), max(w1, l["t1"]))
            work: dict[str, int] = {}
            for day, (w0, w1) in windows.items():
                key = day.strftime("%Y-%m")
                work[key] = work.get(key, 0) + int(
                    (w1 - w0).total_seconds() // 60)
            last = self.legs[active[-1]]
            long_last = (last["t1"] - self.legs[active[0]]["t0"]
                         ).total_seconds() / 60 > self.args.duty_span
            # Отдых ПОСЛЕ цепочки уезжает на второй этап и там становится
            # правилом для ростера. Брать его по прежним плоским числам,
            # когда расчёт идёт по таблице источника, значит выпустить
            # цепочку, после которой ростер законно поставит следующую
            # через 12 часов вместо 24 — и этого не заметит ни один
            # контроль.
            if self.rules is None or not duty_fns:
                # Чтение args.rest_* живёт ТОЛЬКО в ветке без правил
                # источника: при правилах предзначение и так немедленно
                # перекрывалось таблицей, а с None-умолчаниями упало бы
                # на арифметике (пакет «умолчания-в-данные»).
                rest_after = (self.args.rest_base_long if long_last
                              else self.args.rest_base)
            if self.rules is not None and duty_fns:
                tail = {"legs": [self.legs[x] for x in duty_fns[-1]],
                        "span": 0.0}
                self.annotate_duty(tail)
                rest_after = self.rest_after(
                    tail, self.legs[duty_fns[-1][-1]]["arr"])
                # Правило 0041: после периода, прошедшего продлённым
                # пределом (усиленный состав с отдыхом в полёте), отдых
                # не менее 14 часов. Ярус берётся у ПОСЛЕДНЕГО периода
                # цепочки — именно он определяет отдых после неё.
                chain_d = [self.duties[i] for i in c.get("duties") or ()]
                dh_l = [self.legs[x] for x in (c.get("dh") or ())
                        if x in self.legs]
                if chain_d:
                    pers = self.periods_of(chain_d, dh_l)
                    if pers:
                        rest_after = max(rest_after,
                                         self.rest_floor_after(pers[-1]))
            selected.append({
                "fns": fns, "active_fns": active, "duty_fns": duty_fns,
                "cost": c["cost"], "price": c.get("price"), "seats": seats,
                "sourceUnit": (c.get("sourceUnit")
                               or f"pairing-builder-l{self.args.level}:{idx}"),
                "pinned": bool(c.get("fixed")),
                # Построитель выпускает только целые командировки, якорные
                # на базах, — входной аудит применим и к нашему каталогу,
                # а не только к каталогу импорта.
                "kind": "chain",
                # Участок, отданный другой выбранной цепочке, из этой
                # ИЗЪЯТ, и оставшаяся смена — уже не та, что строилась:
                # у неё может быть дыра на месте изъятого участка.
                # Жёсткий ноль объявлял такую цепочку целой, и входной
                # аудит судил её наравне с настоящими.
                "sourceLegsMissing": len(c["legs"]) - len(active),
                "requiredCodesAll": req_all,
                "seatCodes": [
                    seat_codes_for(
                        r, active,
                        lambda x: self.legs[x]["fleet"],
                        lambda x: self.role_count_of_leg.get(x, ()))
                    for r in seats],
                "minRestAfterMin": rest_after,
                "busyWindows": [[lo, hi]],
                "blockMinutesByMonth": block, "workMinutesByMonth": work})
        self.report["demoted_legs"] = demoted
        self.report["units"] = len(selected)
        # Урезанная цепочка — та, у которой участок забрала другая
        # выбранная цепочка. Как построена она была допустимой, а как
        # опубликована — уже нет: в смене остаётся дыра, и станция
        # прилёта одной смены перестаёт совпадать со станцией вылета
        # следующей. Такие цепочки считаются отдельно и НАЗЫВАЮТСЯ: молча
        # опубликованная дыра доедет до второго этапа как настоящая
        # командировка.
        trunc = [r for r in selected if r.get("sourceLegsMissing")]
        self.report["units_truncated"] = len(trunc)

        def _station_break(r) -> bool:
            # Непрерывность судится по ФАКТИЧЕСКИМ перелётам экипажа —
            # рабочие участки И перегоны вместе (fns): проигравший
            # арбитража двойного покрытия остаётся в цепочке пассажиром,
            # и дыра в одних рабочих группах, закрытая перегоном, разрывом
            # не является (счётчик аудита R6 P0-05в судил только рабочие
            # группы и кричал ложно после этого фикса).
            seq = sorted((f for f in (r.get("fns") or [])
                          if f in self.legs),
                         key=lambda f: self.legs[f]["t0"])
            for a, b in zip(seq, seq[1:]):
                if self.legs[a]["arr"] != self.legs[b]["dep"]:
                    return True
            return False

        broken = sum(1 for r in trunc if _station_break(r))
        self.report["units_truncated_with_station_break"] = broken
        if broken:
            print("ВНИМАНИЕ: %d цепочек опубликованы с разрывом станции "
                  "внутри — у них забрали участок. Это не допустимая "
                  "командировка; поднимите штраф за двойное покрытие "
                  "(--w-excess) или расширьте пул кандидатов." % broken,
                  flush=True)
        covered = {x for r in selected for x in r["active_fns"]}
        # Транспортный рейс покрытием не считается и в непокрытые не
        # попадает: обслуживать его наш план не обязан.
        uncovered = set(self.legs) - covered - self.transport_only
        self.report["uncovered_legs"] = len(uncovered)
        self.report.update(self.classify_uncovered(uncovered))
        self.report["person_nights"] = sum(c["nights"] * c["crew"]
                                           for c in chosen)
        self.report["person_rides"] = sum(len(c["dh"]) * c["crew"]
                                          for c in chosen)
        # Отчёт обязан называть ТУ ЖЕ величину, которую минимизировал
        # расчёт. Прежде здесь печатались прежние коэффициенты, тогда как
        # выбор шёл по ставкам источника, и число в отчёте не значило
        # ничего: 540 510 при одних только перегонах на 4,2 миллиона.
        if self.args.cost_0096:
            # RULE: 0096 -- отчёт называет ту же величину, что и цель;
            # ставки — параметры запуска (обезличенный шаблон), их
            # источник несёт исполнитель критерия флагом. Чтение прямое,
            # без числовых умолчаний: валидность гарантирует гейт
            # __init__ (в ветке 0096 None невозможен).
            cw = int(self.args.cost_rank_deci_work)
            cd = int(self.args.cost_rank_deci_dh)
            self.report["cost_model"] = (
                "нормированная стоимость по рангам: деци-коэффициенты "
                "%d (рабочая минута перелёта) / %d (минута перегона)"
                % (cw, cd))
            self.report["cost_rates_param"] = str(getattr(
                self.args, "cost_rates_param", None) or "не задан")
            self.report["total_cost"] = sum(c["cost"] for c in chosen)
        elif self.source_cost:
            hotel = rides = stops = unknown = 0
            for c in chosen:
                crew = c["crew"]
                ch = [self.duties[i] for i in c["duties"]]
                dh = [self.legs[x] for x in c["dh"] if x in self.legs]
                _cost, s, u = self.chain_cost(ch, [], crew)
                hotel += _cost
                stops += s
                unknown += u
                rides += sum(self.dh_cost_of.get(l["id"], 0)
                             for l in dh) * crew
            self.report["cost_model"] = "ставки системы-источника"
            self.report["cost_layovers"] = hotel
            self.report["cost_deadheads"] = rides
            self.report["person_stops_off_base"] = stops
            self.report["person_stops_without_rate"] = unknown
            self.report["total_cost"] = hotel + rides
        else:
            self.report["cost_model"] = "коэффициенты расчёта"
            # Происхождение критерия и ПОЛНЫЙ вектор весов — в
            # сертификате всегда: смесь объявленного критерия с
            # умолчаниями способа обязана быть видимой (п. 10-11
            # постановки Г-П4).
            self.report["cost_criterion_source"] = getattr(
                self.args, "criterion_source", None)
            self.report["cost_weights"] = {
                "w_night": self.args.w_night, "w_dh": self.args.w_dh,
                "w_chain": self.args.w_chain, "w_duty": self.args.w_duty,
                "w_span_day": self.args.w_span_day,
                # композиция целевой: штрафы мягких правил ВМП
                "w_weekend_night": getattr(self.args, "w_weekend_night", 0),
                "weekend_days": sorted(getattr(self.args, "weekend_days", ()) or ()),
                "w_long_duty": getattr(self.args, "w_long_duty", 0),
                "long_duty_threshold": getattr(self.args, "long_duty_threshold", None)}
        # A6: при ЛЮБОМ критерии общая стоимость = Σ цен выбранных
        # цепочек, а мягкие штрафы части — суммой и по семьям из
        # расшифровки цены; модель цены — в отчёте и в подписи.
        soft_rows = [r for c in chosen
                     for r in ((c.get("price") or {}).get("soft") or ())]
        self.report["soft_penalty_total"] = sum(
            r["contribution"] for r in soft_rows)
        by_family: dict = {}
        for r in soft_rows:
            by_family[r["family"]] = (by_family.get(r["family"], 0)
                                      + r["contribution"])
        self.report["soft_penalty_by_family"] = by_family
        self.report["price_model"] = _cp.VERSION
        self.report["total_cost"] = sum(c["cost"] for c in chosen)
        # Контракт Б (единая версия 06.09.2026, P-429): сводка
        # определённости цены — общая функция vmp.chain_price, одна у
        # отчёта, каталога и сертификатора; total_cost — известная часть
        # (known_subtotal), полная цена (full_cost) есть только при полной
        # области учёта всех цепочек. Оптимум полной цены при неполной
        # области не объявляется; оценка снизу (lower_bound) остаётся —
        # она относится к целевой поиска, то есть к известной части
        # (lower_bound_scope), и не обнуляется (P-427).
        price_certainty = _cp.aggregate_certainty(r.get("price") for r in selected)
        self.report["price_certainty"] = price_certainty
        self.report["known_subtotal"] = self.report["total_cost"]
        self.report["full_cost"] = price_certainty["full_cost"]
        self.report["cost_kind"] = price_certainty["cost_kind"]
        self.report["solver_status_scope"] = (
            "complete_declared_scope" if price_certainty["complete"]
            else "known_subtotal")
        self.report["full_price_optimality"] = (
            "by_solver_status" if price_certainty["complete"] else "not_proven")
        # Свидетельство покрытия едет ВНУТРИ каталога (аудит 2026-07-26,
        # P0-05). Раньше готовность этапа 2 определялась одним лишь фактом
        # существования файла, и частичный каталог молча становился
        # каноническим. Различие между «непокрываемо в принципе» и «перебор
        # не нашёл» здесь решающее: первое — свойство данных, второе —
        # недоработка расчёта, и предъявлять её как результат нельзя.
        coverage = {
            # Знаменатель покрытия — обязательства, то есть рейсы
            # без транспортных: иначе доля покрытия занижена чужими
            # рейсами, которые мы и не должны обслуживать.
            "legs_total": len(self.legs) - len(self.transport_only),
            # Исключение по типам: леги типов вне автоматического планирования —
            # отдельная поимённая категория (не обязательства, но и
            # не транспорт данных: их вывел исполнитель правила).
            "auto_plan_excluded": sorted(self.auto_plan_excluded),
            "uncovered": self.report["uncovered_legs"],
            "no_pair_in_horizon":
                self.report.get("uncovered_no_pair_in_horizon", 0),
            "not_built": self.report.get("uncovered_not_built", 0),
            "arbitration_0055":
                self.report.get("uncovered_arbitration_0055", 0),
        }
        coverage["complete"] = coverage["uncovered"] == 0
        # Три исхода по непокрытым (аудит A4, 04.09.2026): доказано
        # невозможно / поиск не завершён / не найдено. Доказанные видны
        # поимённо (excluded_proven) — частичное покрытие не выдаётся за
        # выполненный план даже при вердикте INFEASIBLE_INPUT.
        coverage["excluded_proven"] = sorted(
            self.report.get("proven_impossible_legs") or [])
        coverage["not_built_reasons"] = dict(
            self.report.get("uncovered_not_built_reasons") or {})
        coverage["search_incomplete"] = bool(
            self.report.get("search_incomplete"))
        coverage["verdict"] = coverage_verdict(
            coverage["uncovered"], len(coverage["excluded_proven"]))
        # Свидетельство ослабления — ВНУТРИ каталога, где его читают оба
        # гейта (внешний аудит R6, P0-05): ослабленный выбор или урезанные
        # цепочки понижают вердикт, и белые списки registry и этапа 2
        # такой каталог не пропустят.
        coverage["exact_cover"] = bool(self.report.get("exact_cover", True))
        coverage["units_truncated"] = self.report.get("units_truncated", 0)
        coverage["units_truncated_with_station_break"] = \
            self.report.get("units_truncated_with_station_break", 0)
        coverage["dh_gap_arbitration_unresolved"] = \
            self.report.get("dh_gap_arbitration_unresolved", 0)
        # Засеянные цепочки с нарушениями (группа Д реестра): план с
        # ними не публикуется молча — вердикт понижается, перечень
        # поимённый в fixed_chain_violations.
        coverage["fixed_chain_violations"] = len(
            self.report.get("fixed_chain_violations") or [])
        # A5: закрепления, сами превышающие предел ресурса (места на
        # рейсе, перевозки дня, ёмкость базы), — план не публикуется
        # молча: перечень словами в pin_resource_overflow отчёта.
        coverage["pin_resource_overflow"] = len(
            self.report.get("pin_resource_overflow") or [])
        if not coverage["exact_cover"] or coverage["units_truncated"] \
                or coverage["dh_gap_arbitration_unresolved"] \
                or coverage["fixed_chain_violations"] \
                or coverage["pin_resource_overflow"]:
            coverage["verdict"] = "RELAXED_COVER"
        # run_id — ЛИЧНОСТЬ ПРОГОНА (CREW_RUN_ID от запуска задачи), а не
        # метка набора: повторный аудит 2026-07-26 поймал здесь label, и
        # артефакт нельзя было связать со снимком конфигурации прогона.
        # Ручной запуск без CREW_RUN_ID честно даёт null, а не подмену.
        doc = {"inst": None, "label": label, "profile": "pairing-builder",
               "run_id": os.environ.get("CREW_RUN_ID") or None,
               "ip_status":
                   self.report.get("solver_status"),
               "ip_objective": self.report["total_cost"],
               # Контракт Б (P-429): целевая — известная часть цены на
               # объявленной области учёта; полная цена — только при
               # полной области всех цепочек.
               "ip_objective_scope": self.report["solver_status_scope"],
               "known_subtotal": self.report["total_cost"],
               "full_cost": price_certainty["full_cost"],
               "coverage": coverage,
               # Контракт прощения этапа 2 — тот же, что у линии
               # генерации колонок: поимённый список доказуемо
               # непокрываемых участков (иначе безупречный ростер
               # получает INFEASIBLE_INCUMBENT из-за свойств данных).
               "proven_impossible_legs": sorted(
                   self.report.get("proven_impossible_legs") or []),
               # Доказательства к каждому имени (аудит A4): вид, горизонт,
               # отпечаток данных — потребитель прощает недостачу только
               # по записи, совпавшей с его данными.
               "proven_impossible_proofs": list(
                   self.report.get("proven_impossible_proofs") or []),
               "config_sig": self.config_signature(),
               # Граница «стыковка / отдых» и её происхождение — в
               # каталоге, не только в отчёте проверки (карточка серии).
               "sit_window": self.report.get("sit_window"),
               # Контракт Б (05.09.2026): определённость цены каталога —
               # доказанные внутренние ночёвки против неопределённого
               # вклада (отдых на базе, открытый конец); цена с
               # неопределённым вкладом полной не является. Та же сводка,
               # что в отчёте (vmp.chain_price.aggregate_certainty) — её
               # сверяет сертификатор с агрегатом по цепочкам.
               "price_certainty": price_certainty,
               "source": "pairing_builder", "selected": selected}
        # Карта wire-имён — только когда переименование было: пустой ключ
        # сдвинул бы байты и отпечатки всех прежних каталогов. Этап 2
        # сверяет карту с payload'ом и чужой каталог отвергает.
        wm = getattr(self.args, "wire_map", None)
        if wm:
            doc["wire_map"] = wm
        return doc

    def config_signature(self) -> dict:
        """Отпечаток постановки: без него два прогона с разными уровнями,
        способами и бюджетами неразличимы (аудит 2026-07-26, P1-16).
        Сюда же — действующий набор правил и его отпечаток: расчёт по
        другим правилам обязан быть отличим от расчёта по этим."""
        a = self.args
        # Доступ к полям области учёта — мягкий (как к a.*): подпись
        # собирают и тесты с минимальным построителем; без построенного
        # универсума приписок область учёта честно None.
        _homes = getattr(self, "price_eligible_bases", None)
        _tz = getattr(self, "tz", None)
        price_scope = (_cp.scope_template(
            _homes, _tz, eligible_bases_complete=bool(
                getattr(self, "price_eligible_bases_complete", False)))
            if _homes is not None and isinstance(_tz, timedelta) else None)
        home_coverage = getattr(self, "price_home_coverage", None)
        sig = {"level": a.level, "method": a.method, "budget_s": a.budget_s,
               # A6: модель цены — часть личности прогона: смена
               # определения ночи или композиции меняет цену.
               "price_model": _cp.VERSION,
               # Контракт Б (единая версия 06.09.2026, P-430): объявленная
               # область учёта цены (универсум приписок людей постановки,
               # его полнота, пояс, определение ночи) и id мягких правил
               # ВМП — для независимого пересчёта сертификатором той же
               # области с теми же id строк. Смена состава подписи меняет
               # отпечатки каталогов намеренно (как и версия модели).
               "price_scope": price_scope,
               "price_home_coverage": (dict(home_coverage)
                                       if isinstance(home_coverage, dict) else None),
               "soft_rule_ids": _cp.parse_rule_ids(
                   getattr(a, "soft_rule_ids", None)),
               # Действующая ветка критерия (0096 / ставки источника /
               # коэффициенты) — по ней сертификатор собирает те же
               # мягкие правила для независимого пересчёта мер: флаг
               # source_cost ключа не равен действующей ветке (ставок
               # источника может не быть).
               "price_criterion": (
                   "0096" if getattr(a, "cost_0096", False)
                   else "source_rates" if getattr(self, "source_cost", False)
                   else "weights"),
               # Движок отбора — ЛИЧНОСТЬ прогона (пакет «выбор
               # движка» 2026-08-28): планы разных движков обязаны
               # быть различимы подписью.
               "select_engine": getattr(a, "select_engine", "cpsat"),
               "solve_s": a.solve_s, "seed": a.seed, "bases": a.bases,
               # Д1: детерминированный режим — часть личности прогона
               # (счётный предел вместо стенных часов, один поток).
               "deterministic_time": getattr(a, "deterministic_time", None),
               "w_night": a.w_night, "w_dh": a.w_dh,
               # композиция целевой: штрафы мягких правил ВМП — часть
               # личности прогона (меняют цену и выбор цепочек)
               "w_weekend_night": getattr(a, "w_weekend_night", 0),
               "weekend_days": sorted(getattr(a, "weekend_days", ()) or ()),
               "w_long_duty": getattr(a, "w_long_duty", 0),
               "long_duty_threshold": getattr(a, "long_duty_threshold", None),
               "w_chain": getattr(a, "w_chain", 0),
               "w_duty": getattr(a, "w_duty", 0),
               "w_span_day": getattr(a, "w_span_day", 0),
               "criterion_source": getattr(a, "criterion_source", None),
               "base_day_cap": getattr(a, "base_day_cap", "") or None,
               "warm_catalog": (str(a.warm_catalog)
                                if getattr(a, "warm_catalog", None)
                                else None),
               "duty_span": a.duty_span, "pairing_span": a.pairing_span,
               "max_segs": a.max_segs, "sit_min": a.sit_min,
               "rescue_dh": a.rescue_dh, "rescue_per_leg": a.rescue_per_leg,
               # rescue_s — в подписи каталога (R4 §4.1: разные бюджеты
               # спасательного прохода давали одинаковую личность каталога)
               "rescue_s": getattr(a, "rescue_s", None),
               # Внешний аудит R8 (P0-03): два прогона с разными капами
               # поиска имели ОДНУ подпись — любой параметр, способный
               # убрать смену или цепочку из пространства, часть личности.
               "branch_duty": getattr(a, "branch_duty", None),
               "branch_chain": getattr(a, "branch_chain", None),
               "depth": getattr(a, "depth", None),
               "per_duty": getattr(a, "per_duty", None),
               "dh_options": getattr(a, "dh_options", None),
               "sit_max": getattr(a, "sit_max", None),
               "cg_rounds": getattr(a, "cg_rounds", None),
               "cg_top_k": getattr(a, "cg_top_k", None),
               "cg_alpha": getattr(a, "cg_alpha", None),
               "cg_pass_s": getattr(a, "cg_pass_s", None),
               "nights_per_layover": getattr(a, "nights_per_layover", None),
               "w_excess": getattr(a, "w_excess", None),
               # Внешний аудит R9 (P1-01): подпись перечисляла ручки ПОИСКА,
               # но не параметры, меняющие ДОПУСТИМОСТЬ и ЦЕЛЬ. Девять
               # прогонов с разными значениями ниже имели одну личность —
               # то есть исторические пулы, лучшие решения и результаты
               # разных математических постановок можно было смешивать.
               #
               # Доступ только через getattr: подпись читают и тесты, и
               # инструмент сверки, собирающие Namespace вручную и не
               # обязанные знать полный перечень (прямое обращение уронило
               # бы их с AttributeError, а не выявило дефект).
               "tz_offset_min": getattr(a, "tz_offset_min", None),
               "min_rest_sit": getattr(a, "min_rest_sit", None),
               "rest_base": getattr(a, "rest_base", None),
               "rest_base_long": getattr(a, "rest_base_long", None),
               "rest_off": getattr(a, "rest_off", None),
               "rest_off_long": getattr(a, "rest_off_long", None),
               "source_cost": getattr(a, "source_cost", None),
               "cost_0096": getattr(a, "cost_0096", None),
               # Деци-коэффициенты критерия по рангам — часть личности
               # постановки (обезличенный шаблон: ставки из пакета).
               "cost_rank_deci_work": getattr(
                   a, "cost_rank_deci_work", None),
               "cost_rank_deci_dh": getattr(a, "cost_rank_deci_dh",
                                            None),
               "cost_rates_param": getattr(a, "cost_rates_param",
                                           None),
               "briefing_default_min": getattr(a, "briefing_default_min", None),
               "debriefing_default_min": getattr(
                   a, "debriefing_default_min", None),
               # Параллелизм влияет на ВЫБОР, а не только на скорость:
               # соседний код сам признаёт недетерминизм при workers != 1.
               "workers": getattr(a, "workers", None)}
        if getattr(a, "duty_cap", None) is not None:
            # Мера 100: потолок продления меняет допустимое множество смен — личность каталога; без потолка
            # подпись прежняя (отпечатки прежних каталогов не сдвигаются)
            sig["duty_cap"] = a.duty_cap
        sig["rules_fingerprint"] = getattr(a, "rules_fingerprint", None)
        sig["rules_effective"] = getattr(a, "rules_effective", None)
        sig["rules_overridden"] = getattr(a, "rules_overridden", [])
        # Личность прогона (P1-13 внешнего аудита: у каталога стояло
        # `run_id: null`). Без неё снимок конфигурации прогона существует,
        # а связи артефакта с ним нет — и «по чему это посчитано» опять
        # приходится угадывать.
        sig["run_id"] = os.environ.get("CREW_RUN_ID") or None
        # Правила источника — часть постановки: расчёт по ним и расчёт по
        # своим правилам обязан быть отличим по отпечатку, иначе два
        # несравнимых прогона получат одинаковую личность.
        rules = getattr(a, "cos_rules", None)
        sig["source_rules"] = rules.as_dict() if rules is not None else None
        # Происхождение файла правил — атомарно с телом (внешний аудит R6,
        # P0-02): путь и sha256 в отпечатке связывают постановку с
        # конкретным артефактом, а не с «каким-то файлом правил». Ключи
        # пишутся только при использовании: прежние отпечатки не меняются.
        if getattr(a, "source_rules", None) is not None:
            sig["source_rules_path"] = Path(a.source_rules).name
        if getattr(a, "source_rules_fp", None):
            sig["source_rules_fp"] = a.source_rules_fp
        # Осознанная публикация неполного каталога — часть постановки.
        # Ключ пишется только при использовании: отпечатки обычных
        # прогонов не меняются.
        if getattr(a, "accept_incomplete_search", False):
            sig["accept_incomplete_search"] = True
        # Закрепления — часть постановки: прогон с ними и без них обязан
        # быть различим по отпечатку.
        if getattr(self, "fixed_meta", None):
            sig["pinned_chains"] = len(self.fixed_meta)
        # КОНТЕНТ-хэши базового и пин-каталогов (аудит R10, P1-02): их
        # содержимое меняет пространство допустимых смен и цепочек, а в
        # подписи был лишь счётчик закреплений — два прогона с разными
        # каталогами получали одну личность. Ключи пишутся только при
        # использовании: прежние отпечатки не меняются.
        if getattr(a, "base_catalog_sha256", None):
            sig["base_catalog_sha256"] = a.base_catalog_sha256
        if getattr(a, "pin_catalog_sha256", None):
            sig["pin_catalog_sha256"] = a.pin_catalog_sha256
        if getattr(a, "inject_catalog_sha256", None):
            sig["inject_catalog_sha256"] = list(a.inject_catalog_sha256)
        # Личность ПОСТАНОВКИ (аудит R10, P0-03): каталог штампует
        # идентификатор единого манифеста прогона — этап 2 и производные
        # артефакты сверяют цепочку, а не собирают постановку заново.
        pm_id = os.environ.get("CREW_PROBLEM_MANIFEST_ID")
        if pm_id:
            sig["problem_manifest_id"] = pm_id
        return sig


_HOMOGLYPHS = str.maketrans("АВЕКМНОРСТУХаеорсух",
                            "ABEKMHOPCTYXaeopcyx")


def warn_fleet_homoglyphs(conditions, payload) -> list[str]:
    """Громкая диагностика гомоглифов в словарях по флоту.

    Кириллическая «А» в типе судна против латинской «A» в ключе условий
    делает класс места отдыха ненаходимым МОЛЧА — продлённый предел
    исчезает, и легальные смены бракуются (добор одной из линий).
    Невидимая буква — невидимая норма: называем вслух.
    """
    ex = getattr(conditions, "_exact", None) if conditions else None
    if not ex:
        return []
    keys = set(ex.get("class_by_fleet") or ())
    fleets = {f.get("aircraftType") or "?" for f in
              payload.get("flights", [])}
    out = []
    for fl in sorted(fleets - keys):
        norm = fl.translate(_HOMOGLYPHS)
        twins = [k for k in keys
                 if k.translate(_HOMOGLYPHS) == norm and k != fl]
        if twins:
            out.append("флот %r не найден в классах мест отдыха, но есть "
                       "ключ %r, отличающийся только кириллицей/латиницей "
                       "— гомоглиф в данных" % (fl, twins[0]))
    for w in out:
        print("ВНИМАНИЕ: " + w, flush=True)
    return out


def canon_names_to_payload(cat: dict, payload: dict, name: str) -> dict:
    """Канонические имена рейсов добора (NUM-YYYYMMDD-K) — в имена
    payload'а. Нераспознанное имя — громкий отказ: молчаливый отброс
    превращал бы конструкцию в пустую и хоронил добор без следа."""
    from collections import defaultdict as _dd
    by_nd = _dd(list)
    for f in payload.get("flights", []):
        num = f["flightNumber"].split("|")[0]
        date = f["departureUTCDateTime"][:10].replace("-", "")
        by_nd[(num, date)].append(f["flightNumber"])
    for v in by_nd.values():
        v.sort()
    known = {f["flightNumber"] for f in payload.get("flights", [])}

    def one(x: str) -> str:
        if x in known:
            return x
        parts = x.rsplit("-", 2)
        if len(parts) == 3 and parts[2].isdigit():
            lst = by_nd.get((parts[0], parts[1])) or []
            k = int(parts[2])
            if 1 <= k <= len(lst):
                return lst[k - 1]
        raise SystemExit(
            "ОТКАЗ: каталог %s несёт имя рейса %r, которого нет в "
            "наборе ни прямо, ни в канонической форме NUM-YYYYMMDD-K"
            % (name, x))

    out = {k: v for k, v in cat.items() if k != "selected"}
    out["selected"] = []
    for u in cat.get("selected") or []:
        nu = dict(u)
        for k in ("fns", "active_fns"):
            nu[k] = [one(x) for x in u.get(k) or []]
        nu["duty_fns"] = [[one(x) for x in g]
                          for g in u.get("duty_fns") or []]
        out["selected"].append(nu)
    return out


def coverage_verdict(uncovered: int, proven: int) -> str:
    """Вердикт покрытия по трём исходам (аудит A4, 04.09.2026):
    OK — непокрытых нет; INFEASIBLE_INPUT — ВСЕ непокрытые доказанно
    непокрываемы (свойство данных, с приложенными доказательствами);
    INCOMPLETE_SEARCH — есть непокрытые без доказательства (поиск не
    завершён, вариант не найден, жертва арбитража) — недоработка расчёта
    или неизвестность, а не свойство данных.

    Класс ошибки A4: прежде INFEASIBLE_INPUT ставился при «не построено
    перебором = 0», где «доказанность» была отрицательным ответом
    эвристического зонда — неполный перебор назывался свойством данных."""
    if uncovered == 0:
        return "OK"
    if proven >= uncovered:
        return "INFEASIBLE_INPUT"
    return "INCOMPLETE_SEARCH"


def build(payload: dict, args: argparse.Namespace,
          base_catalog: dict | None = None,
          pin_catalog: dict | None = None,
          inject_catalogs: list[dict] | None = None) -> tuple[dict, dict]:
    """Собрать каталог цепочек. Возвращает (каталог, отчёт).

    Сторож-диагност живёт ровно столько, сколько идёт сборка: событие
    остановки гасится в finally, чтобы поток не переживал вызов."""
    if args.level in (1, 2) and base_catalog is None:
        raise ValueError("уровни 1 и 2 требуют базовый каталог заказчика "
                         "(--base-catalog)")
    stop = _start_watchdog(getattr(args, "out", None) or "pairing-run")
    try:
        return _build_body(payload, args, base_catalog, pin_catalog,
                           inject_catalogs)
    finally:
        stop.set()


def _build_body(payload: dict, args: argparse.Namespace,
                base_catalog: dict | None,
                pin_catalog: dict | None,
                inject_catalogs: list[dict] | None) -> tuple[dict, dict]:
    """Тело сборки каталога цепочек (сторож запускает build())."""
    b = Builder(payload, args)
    hom = warn_fleet_homoglyphs(getattr(args, "conditions", None),
                               payload)
    if hom:
        b.report["fleet_homoglyph_warnings"] = hom
    if pin_catalog is not None:
        b.prepare_pins(pin_catalog)
    # засев смен-данностей: каталожные смены уровней 1-2, закреплённые
    # группы и КОНСТРУКЦИИ ТОЧЕЧНОГО ДОБОРА (--inject-catalog) идут одним
    # механизмом; добор при этом НЕ закрепляется — его цепочки станут
    # обычными кандидатами с честной ценой профиля, отбор волен их не
    # брать (жёсткое закрепление вытесняет соседей — смок 2026-08-27)
    seed = base_catalog if base_catalog is not None else pin_catalog
    inject_units = [u for c in (inject_catalogs or ())
                    for u in c.get("selected") or []]
    if inject_units:
        seed = {"selected": list((seed or {}).get("selected") or ())
                + inject_units}
    b.build_duties(seed)
    skip: set[int] = set()
    if args.level == 1:
        skip = b.fix_customer_chains(base_catalog)
    if pin_catalog is not None:
        skip |= b.pin_chains(pin_catalog)
    # Единый суд для засеянных путей (реестр соответствия 2026-08-28,
    # группа Д): закреплённые и импортированные цепочки прежде не
    # судились ВООБЩЕ («данность») — суд обязан быть один для всех
    # путей входа. Закрепление с нарушением — громкий отказ (оно
    # становится частью НАШЕГО решения); импорт заказчика (уровень 1)
    # с нарушением — поимённая пометка в отчёте, вердикт публикации
    # понижается (пользователь извещается, план с нарушениями молча
    # не выпускается).
    b.judge_fixed_chains(strict=pin_catalog is not None
                         and args.level != 1)
    if args.method == "columns":
        b.generate_columns(skip)
    else:
        b.enumerate_candidates(skip)
    b.rescue_orphans(skip)
    b.escalate_orphans(skip)
    if inject_units:
        by_key = {tuple(x["id"] for x in d["legs"]): d for d in b.duties}
        ok = bad = dup = 0
        bad_why: dict[str, list] = {}
        for u in inject_units:
            chain = []
            for group in u.get("duty_fns") or ():
                g = sorted((x for x in group if x in b.legs),
                           key=lambda x: b.legs[x]["t0"])
                d = by_key.get(tuple(g))
                if d is not None:
                    chain.append(d)
            if not chain or len(chain) != len(u.get("duty_fns") or ()):
                bad += 1
                bad_why.setdefault("смена не найдена в множестве смен",
                                   []).append(u.get("sourceUnit") or "?")
                continue
            chain.sort(key=lambda d: d["t0"])
            active = {l["id"] for d in chain for l in d["legs"]}
            dh_legs = [b.legs[x] for x in u.get("fns") or ()
                       if x in b.legs and x not in active]
            dupkey = (tuple(d["i"] for d in chain),
                      tuple(sorted(x["id"] for x in dh_legs)))
            if dupkey in b.candidates:
                # Дубликат пула — не брак: перебор построил эту цепочку
                # сам, покрытие конструкции уже обеспечено.
                dup += 1
                continue
            rec = b.emit(chain, dh_legs)
            if rec is None:
                # Конструкция добора не прошла НАШИ проверки легальности —
                # отказ вслух, а не молча: источник обязан узнать.
                bad += 1
                bad_why.setdefault("не прошла проверки выпуска (emit)",
                                   []).append(u.get("sourceUnit") or "?")
            else:
                ok += 1
        b.report["injected_candidates"] = ok
        b.report["inject_duplicates"] = dup
        b.report["inject_rejected"] = bad
        print("точечный добор: конструкций принято кандидатами %d, "
              "уже в пуле (дубликаты) %d, отвергнуто проверками %d (из %d)"
              % (ok, dup, bad, len(inject_units)), flush=True)
        for why, who in bad_why.items():
            print("  отказ добора [%s]: %d шт., например %s"
                  % (why, len(who), ", ".join(who[:4])), flush=True)
        b.report["inject_rejected_why"] = {k: v for k, v in bad_why.items()}
    # Петля «отбор - арбитраж» (разбор №28, 2026-08-28): неразрешимый
    # многосторонний клапан публикационного арбитража, стоивший
    # покрытия, возвращается в отбор ограничением несовместности
    # участников — отбор ищет другую комбинацию. До двух повторных
    # заходов; ухудшившийся заход откатывается к лучшему каталогу.
    import time as _tm5
    ban_groups: list = []
    chosen = b.select()
    catalog = b.to_catalog(chosen, args.label)
    best = (chosen, catalog,
            catalog.get("coverage", {}).get("uncovered", 0))
    for _lap in range(2):
        groups = [g for g in (getattr(b, "_arb_conflict_groups", None)
                              or []) if g not in ban_groups]
        if not groups \
                or not b.report.get("uncovered_by_arbitration_legs"):
            break
        ban_groups.extend(groups)
        print("веха [%s] петля отбор-арбитраж: заход %d, ограничений "
              "несовместности %d"
              % (_tm5.strftime("%H:%M:%S"), _lap + 2,
                 len(ban_groups)), flush=True)
        chosen2 = b.select(ban_groups=ban_groups)
        catalog2 = b.to_catalog(chosen2, args.label)
        unc2 = catalog2.get("coverage", {}).get("uncovered", 0)
        unc_best = best[1].get("coverage", {}).get("uncovered", 0)
        if isinstance(unc2, int) and isinstance(unc_best, int) \
                and unc2 <= unc_best:
            best = (chosen2, catalog2, unc2)
            chosen, catalog = chosen2, catalog2
        else:
            # Заход хуже — честный откат к лучшему каталогу (пересбор
            # восстанавливает и поля отчёта).
            print("веха [%s] петля отбор-арбитраж: заход хуже — "
                  "откат к лучшему" % _tm5.strftime("%H:%M:%S"),
                  flush=True)
            chosen, catalog = best[0], b.to_catalog(best[0],
                                                    args.label)
            break
    b.report["arb_ban_groups"] = len(ban_groups)
    return catalog, b.report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--level", type=int, choices=(1, 2, 3), default=3)
    ap.add_argument("--base-catalog", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--bases", default="")
    ap.add_argument("--budget-s", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--deterministic-time", type=float, default=None,
                    help="детерминированный режим (Д1 плана прямого хода): "
                         "предел детерминированного времени CP-SAT вместо "
                         "секунд, один поток поиска, дедлайны перечисления, "
                         "добора, эскалации и генерации колонок выключены; "
                         "два запуска на одном входе дают один выход")
    # Значения по умолчанию у правил яруса 1 — None: их даёт единый
    # контракт правил. Явный флаг остаётся перекрытием и фиксируется.
    ap.add_argument("--max-segs", type=int, default=None)
    ap.add_argument("--sit-min", type=int, default=None)
    ap.add_argument("--sit-max", type=int, default=None,
                    help="верх окна стыковки внутри смены, минуты. По "
                         "умолчанию НЕ задан: граница «стыковка / отдых» "
                         "берётся из пола отдыха ВМП по станции (пауза "
                         "короче пола отдыха — стыковка, иначе отдых; "
                         "ревизия литералов 2026-09-03). Явное число — "
                         "перекрытие оператора, попадает в отчёт и подпись")
    ap.add_argument("--duty-span", type=int, default=None)
    ap.add_argument("--duty-cap", type=int, default=None,
                    help="жёсткий потолок продления смены (duty_extension_cap ВМП, мера 100): смена длиннее "
                         "--duty-span и не длиннее потолка допустима, штраф продления — от базы; без ключа "
                         "предел смены — --duty-span")
    ap.add_argument("--pairing-span", type=int, default=None)
    # Правила в формулировке системы-источника. Без ключа построитель
    # считает по своим правилам ровно как раньше; с ключом — по правилам
    # источника, и тогда сравнение с его решением идёт на одних условиях.
    ap.add_argument("--source-rules", type=Path, default=None,
                    help="файл системы-источника (или его раздел "
                         "parameters): предел смены по составу, таблица "
                         "отдыха, ночная и «тёмная» смены, равнозначные "
                         "станции, предел цепочки в сутках")
    ap.add_argument("--source-rules-fp", default=None,
                    help="sha256 файла правил источника: продуктовый запуск "
                         "передаёт отпечаток атомарно с путём, и подменённый "
                         "между стартом и чтением файл даёт отказ (внешний "
                         "аудит R6, P0-02)")
    ap.add_argument("--source-rules-strict", action="store_true",
                    help="неполный файл источника — отказ, а не тихое "
                         "удержание умолчаний другой авиакомпании")
    # Профиль пакета ВМП (внешний аудит 04.09.2026, A1; класс ошибки:
    # умолчания площадки молча подменяли нормы ВМП). Кнопка COSMOS
    # подаёт этот флаг всегда: настройка площадки (rules_config) не
    # читается вовсе, пределы яруса 1 — только явными ключами и
    # контрактом --source-rules; незаданный порог — отказ с именем
    # правила, две правды (ключ ≠ контракт) — тоже отказ.
    ap.add_argument("--vmp-package", action="store_true",
                    help="профиль пакета ВМП: пределы смены, посадок, "
                         "стыковки и цепочки — только из ключей запуска "
                         "и контракта --source-rules; настройка площадки "
                         "не читается, незаданный порог — отказ словами")
    ap.add_argument("--dark-definition",
                    choices=("strict", "ratio", "touch"), default="strict",
                    help="определение «тёмной» смены: источником оно не "
                         "установлено, поэтому задаётся явно и проверяется "
                         "крайними вариантами")
    ap.add_argument("--base-tz-offset-min", type=int, default=None,
                    help="смещение пояса базы: в нём понимаются окна "
                         "22:00-06:00 и 23:00-07:00")
    ap.add_argument("--conditions", type=Path, default=None,
                    help=("спецификация условий расчёта (json). Когда "
                          "задана, состав экипажа, предел рабочего "
                          "времени, стыковка, отдых и предел связки "
                          "берутся ИЗ НЕЁ, а не выводятся из данных "
                          "источника"))
    ap.add_argument("--inject-catalog", type=Path, action="append",
                    default=None,
                    help="каталог конструкций точечного добора: его "
                         "цепочки добавляются КАНДИДАТАМИ в пул "
                         "(смены засеваются данностью, цена по "
                         "профилю, обязательности нет); флаг "
                         "повторяем — порции складываются")
    ap.add_argument("--pin-catalog", type=Path, default=None,
                    help="каталог закреплённых цепочек (locked=true — "
                         "обязательное условие): структуры входят в "
                         "решение неприкосновенными со своей честной "
                         "стоимостью, поиск идёт по остальному")
    ap.add_argument("--accept-incomplete-search", action="store_true",
                    help="опубликовать каталог, даже если перебор не "
                         "построил цепочку для достижимых участков. Факт "
                         "остаётся в вердикте покрытия и в отпечатке "
                         "постановки; молчаливого пути обойти гейт нет — "
                         "этап 2 проверяет вердикт внутри каталога")
    ap.add_argument("--trans-tz-hours", type=float, default=None,
                    help="с какого расхождения часов со своей базой "
                         "стоянка считается «дальней» (столбец Trans "
                         "таблицы отдыха). Значение несёт файл правил "
                         "источника; явный флаг — перекрытие оператора "
                         "и попадает в список перекрытий")
    ap.add_argument("--briefing-default-min", type=int, default=None,
                    help="брифинг, если его нет ни у участка, ни у его "
                         "станции; по умолчанию берётся преобладающее "
                         "значение по всем данным. Явный ноль применяется")
    ap.add_argument("--debriefing-default-min", type=int, default=None)
    ap.add_argument("--rules-config", type=Path, default=None,
                    help="файл правил яруса 1; по умолчанию — путь из "
                         "переменной окружения CREW_RULES_CONFIG, а без "
                         "неё встроенные значения")
    ap.add_argument("--min-rest-sit", type=int, default=DEF_MIN_REST_SIT)
    # Таблица отдыха — данные заказчика, в коде она не живёт. Каналы:
    # пороги пакета постановки (builder_args из applied), файл правил
    # источника (--source-rules, layover-таблица), файл условий
    # (--conditions, лестницы отдыха); явные флаги — допустимое
    # перекрытие. Голый запуск без носителя — отказ словами (страж в
    # Builder.__init__).
    ap.add_argument("--rest-base", type=int, default=None,
                    help="минуты отдыха на базе; без файла правил или "
                         "условий обязательны все четыре флага --rest-*")
    ap.add_argument("--rest-base-long", type=int, default=None,
                    help="минуты отдыха на базе после длинной смены")
    ap.add_argument("--rest-off", type=int, default=None,
                    help="минуты отдыха вне базы")
    ap.add_argument("--rest-off-long", type=int, default=None,
                    help="минуты отдыха вне базы после длинной смены")
    ap.add_argument("--branch-duty", type=int, default=3)
    ap.add_argument("--branch-chain", type=int, default=4)
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--per-duty", type=int, default=30)
    ap.add_argument("--dh-options", type=int, default=1,
                    help="сколько вариантов перегона пассажиром "
                         "рассматривать в основном проходе")
    ap.add_argument("--rescue-dh", type=int, default=4,
                    help="то же в спасательном проходе по плечам без "
                         "кандидатов; 0 — проход выключен")
    ap.add_argument("--rescue-per-leg", type=int, default=8,
                    help="сколько цепочек строить на смену в "
                         "спасательном проходе")
    ap.add_argument("--use-cover-checkpoint", type=str, default=None,
                    help="восстановить выбор покрытия из контрольной "
                         "записи (решатель не вызывается)")
    ap.add_argument("--solver-log", action="store_true",

                    help="печатать журнал CP-SAT (статистика presolve и "
                         "поиска) в журнал прогона")
    ap.add_argument("--no-presolve", action="store_true",
                    help="полностью выключить presolve CP-SAT")
    ap.add_argument("--valve-top-per-leg", type=int, default=0,
                    help="предфильтр модели выбора: по K самых дешёвых "
                         "кандидатов на рейс; 0 — без фильтра")
    ap.add_argument("--greedy-cover", action="store_true",
                    help="жадное покрытие вместо решателя: режим быстрого "
                         "цикла суда правил; оптимальность не заявляется")
    ap.add_argument("--dump-pool", default=None,
                    help="выгрузить пул кандидатов (после отсева фантомов) "
                         "каталогом порций в файл и выйти без счёта — для "
                         "внешнего доказательства оптимума")
    ap.add_argument("--gap-limit", type=float, default=0.0,
                    help="относительный зазор остановки выбора покрытия "
                         "(0.01 = принять решение в 1%% от оптимума); "
                         "0 — доказывать оптимальность до бюджета")
    ap.add_argument("--core-min-budget", type=int, default=60,
                    help="повторных решений на минимизацию ядра "
                         "неразрешимости; 0 — сырое ядро без минимизации "
                         "(черновые прогоны)")
    ap.add_argument("--core-min-wall-s", type=int, default=300,
                    help="настенный предел всей минимизации ядра, с "
                         "(второй предел к бюджету вызовов: на "
                         "патологиях решателя вызовы длятся до своего "
                         "полного лимита каждый); 0 — без стены")
    ap.add_argument("--rescue-s", type=int, default=120,
                    help="предел времени второго спасательного прохода, с")
    ap.add_argument("--select-engine",
                    choices=("cpsat", "cplex", "highs"),
                    default="cpsat",
                    help="движок фазы отбора покрытия: cpsat — "
                         "встроенный CP-SAT; cplex/highs — внешние "
                         "(та же модель, честный отказ при "
                         "недоступности)")
    ap.add_argument("--escalate-s", type=int, default=0,
                    help="бюджет фазы эскалации сирот до развязки "
                         "(покрыт / невозможность доказана / бюджет "
                         "исчерпан), с; 0 — фаза выключена")
    ap.add_argument("--solve-s", type=int, default=0,
                    help="бюджет финального решателя задачи покрытия, с; "
                         "0 — брать --budget-s")
    ap.add_argument("--method", choices=("enumerate", "columns"),
                    default="enumerate",
                    help="enumerate — перечисление кандидатов; "
                         "columns — генерация колонок по "
                         "двойственным оценкам")
    ap.add_argument("--cg-rounds", type=int, default=8)
    ap.add_argument("--cg-top-k", type=int, default=20000)
    ap.add_argument("--cg-alpha", type=float, default=0.5,
                    help="сглаживание двойственных (Neame)")
    ap.add_argument("--cg-pass-s", type=int, default=60)
    ap.add_argument("--w-night", type=int, default=DEF_W_NIGHT)
    # Композиция целевой (канон ВМП): штрафы мягких правил части —
    # слагаемые цены цепочки; веса и параметры ТОЛЬКО из пакета.
    ap.add_argument("--w-weekend-night", type=int, default=0,
                    help="надбавка за ночёвку вне базы, начатую в день из "
                         "--weekend-days (семья calendar_windows); 0 = нет")
    ap.add_argument("--weekend-days", type=_weekday_list, default=(),
                    help="дни недели окна через запятую: friday,saturday "
                         "либо числа 0..6 (понедельник = 0)")
    ap.add_argument("--w-long-duty", type=int, default=0,
                    help="штраф за смену длиннее --long-duty-threshold "
                         "(семья duty_extension_penalty); 0 = нет")
    ap.add_argument("--long-duty-threshold", type=int, default=None,
                    help="базовый порог продления смены, минуты (параметр "
                         "long_duty_threshold ВМП)")
    ap.add_argument("--w-dh", type=int, default=DEF_W_DH)
    ap.add_argument("--warm-catalog", type=Path, default=None,
                    help="каталог прежнего прогона как ПОДСКАЗКА "
                         "точной фазе (тёплый старт); частичная, "
                         "решателя не обязывает")
    ap.add_argument("--export-mps", type=Path, default=None,
                    help="выгрузить ТОЧНУЮ модель покрытия свободным "
                         "MPS в указанный файл (рядом — <файл>.map.json "
                         "со справочником переменных и ограничений); "
                         "расчёт продолжается как обычно")
    ap.add_argument("--export-lp", type=Path, default=None,
                    help="то же в формате LP (CPLEX LP file)")
    ap.add_argument("--export-only", action="store_true",
                    help="выйти сразу после выгрузки модели (--export-mps/"
                         "--export-lp), не запуская решатель: профиль и пул "
                         "боевые, счёта нет — режим панельной кнопки "
                         "экспорта")
    ap.add_argument("--base-day-cap", default="",
                    help="ёмкость баз в календарных днях на роль, "
                         "формат SVG=138,PLY=460: сумма дней цепочек "
                         "со стартом с базы не превышает ёмкости; "
                         "пусто — выключено")
    ap.add_argument("--criterion-source", default="умолчания построителя",
                    help="происхождение критерия стоимости (правило "
                         "пакета, например «пакет: O-…»); едет в отчёт "
                         "и подпись — происхождение числа доказуемо")
    ap.add_argument("--w-span-day", type=int, default=DEF_W_SPAN_DAY,
                    help="цена календарного дня командировки за "
                         "человека — буквальный min_person_days "
                         "(дни отдыха внутри цепочки платные)")
    ap.add_argument("--w-duty", type=int, default=DEF_W_DUTY,
                    help="цена смены за человека: прямой прокси "
                         "человеко-дней (ночёвками не обходится); "
                         "0 — историческое поведение")
    ap.add_argument("--w-chain", type=int, default=DEF_W_CHAIN,
                    help="фикс-цена цепочки за человека: >0 сливает "
                         "участки в полные смены (ручка классической "
                         "схемы); 0 — историческое поведение")
    ap.add_argument("--w-excess", type=int, default=DEF_W_EXCESS)
    ap.add_argument("--hard-cover-leg", action="append", default=[],
                    help="лег с жёстким «ровно один» даже в клапане: "
                         "двойное покрытие этого рейса нелегально при "
                         "любом владельце (доказано арбитражем 0055)")
    # Ставки цели — из данных источника. Конвенция «ночей за одну
    # стоянку вне базы» — число из пакета постановки (вопрос заказчику),
    # а не константа кода: при ставках источника в данных и без флага —
    # отказ словами (страж в Builder.__init__).
    ap.add_argument("--nights-per-layover", type=int, default=None,
                    help="ночей за стоянку вне базы в цели по ставкам "
                         "источника: N — из пакета постановки, 0 — по "
                         "фактическим календарным ночам; при ставках "
                         "источника в данных флаг обязателен "
                         "(либо --no-source-cost)")
    ap.add_argument("--no-source-cost", dest="source_cost",
                    action="store_false",
                    help="считать по нашим коэффициентам, даже если ставки "
                         "источника есть в данных (для сравнения целей)")
    ap.set_defaults(source_cost=True)
    ap.add_argument("--cost-0096", action="store_true",
                    help="целевая = нормированная стоимость по рангам "
                         "(деци-норм-минуты на человека, умноженные на "
                         "состав); главнее ставок источника и весов; "
                         "деци-коэффициенты — параметрами ниже")
    ap.add_argument("--cost-rank-deci-work", type=int, default=None,
                    help="деци-коэффициент рабочей минуты перелёта = "
                         "10*(ставка_полёта + ставка_работы); значение "
                         "считает исполнитель критерия vmp/criteria из "
                         "блока rates пакета — умолчания в коде нет, "
                         "при --cost-0096 флаг обязателен")
    ap.add_argument("--cost-rank-deci-dh", type=int, default=None,
                    help="деци-коэффициент минуты перегона = "
                         "10*ставка_работы; источник тот же, при "
                         "--cost-0096 флаг обязателен")
    ap.add_argument("--cost-rates-param", default=None,
                    help="источник/версия ставок критерия — след "
                         "происхождения для отчёта; подаёт "
                         "исполнитель критерия из версии ставок "
                         "пакета")
    ap.add_argument("--tz-offset-min", type=int, default=DEF_TZ_OFFSET_MIN)
    args = ap.parse_args(argv)

    # ---- деци-ставки критерия 0096 — только из данных пакета -----------
    # Значения считает исполнитель критерия (vmp/criteria) из блока
    # rates пакета постановки; умолчаний в коде нет. Гейт стоит ДО
    # контракта яруса 1: деци-флаги в TIER1_TO_ARG не входят, и None
    # оттуда не перезаписывается.
    if args.cost_0096 and (args.cost_rank_deci_work is None
                           or args.cost_rank_deci_dh is None):
        print("ОТКАЗ: --cost-0096 требует явных --cost-rank-deci-work и "
              "--cost-rank-deci-dh — деци-ставки приходят из блока rates "
              "критерия пакета (исполнитель vmp/criteria); умолчаний в "
              "коде нет", flush=True)
        return 2

    # ---- единый контракт правил яруса 1 (P0-03) ------------------------
    # Незаданные флаги берут значения из действующего набора правил — того
    # же, что исполняет решатель этапа 2 и показывает интерфейс. Заданные
    # флаги перекрывают его, но перекрытие называется вслух и попадает в
    # каталог: иначе оператор считает, что правило применено, а оно нет.
    overrides = []
    package_mode = bool(getattr(args, "vmp_package", False))
    if package_mode:
        # Профиль пакета ВМП (аудит A1, 04.09.2026): настройка площадки
        # НЕ читается — ни файл, ни встроенные значения; пределы яруса 1
        # — только явные ключи и контракт --source-rules (ниже). До
        # этого незаданный ключ молча получал 5 посадок / 4 суток
        # площадки и выдавал их за норму ВМП.
        effective = None
        if not args.source_rules:
            print("ОТКАЗ: профиль пакета ВМП (--vmp-package) требует "
                  "контракт правил --source-rules с отпечатком — без "
                  "носителя норм ВМП исполнять нечего; настройка "
                  "площадки в этом профиле не читается", flush=True)
            return 2
        print("этап: профиль пакета ВМП — настройка площадки не "
              "читается; пределы яруса 1 только из ключей запуска и "
              "контракта правил", flush=True)
    else:
        import rules_config
        effective = (rules_config.load_from(str(args.rules_config))
                     if args.rules_config else rules_config.load())
        for key, arg in TIER1_TO_ARG.items():
            if getattr(args, arg) is None:
                setattr(args, arg, int(effective[key]))
            elif int(getattr(args, arg)) != int(effective[key]):
                overrides.append(f"{key}: правила {effective[key]} -> "
                                 f"флаг {getattr(args, arg)}")
    args.rules_effective = effective

    # ---- условия расчёта из спецификации заказчика ----------------------
    if isinstance(getattr(args, "conditions", None), Path):
        spec_path = args.conditions
        args.conditions = _conditions.load(spec_path)
        prov = args.conditions.provenance()
        print("этап: условия расчёта из %s — правил однозначных %d, "
              "принятых в наихудшей трактовке %d"
              % (spec_path.name, len(prov["group_A"]),
                 len(prov["group_B_worst_case"])), flush=True)
        print("      состав, предел рабочего времени, стыковка, отдых и "
              "предел связки берутся ИЗ УСЛОВИЙ, а не из данных источника",
              flush=True)

    # ---- правила системы-источника -------------------------------------
    args.cos_rules = None
    if not args.source_rules:
        # Вслух, а не молча (внешний аудит R6, P0-02): без файла источника
        # действует постановка яруса 1, и читатель отчёта обязан узнать об
        # этом из вывода, а не из сравнения чисел.
        print("этап: правила системы-источника не переданы — расчёт по "
              "правилам яруса 1 (замороженная конфигурация набора)",
              flush=True)
    if args.source_rules and not args.source_rules_fp:
        # Отпечаток ОБЯЗАТЕЛЕН и в прямом CLI (аудит R10, отсрочка №2):
        # путь без sha256 допускал подмену файла между построением команды
        # и чтением; этап 2 и сертификат требуют отпечаток давно — прямой
        # запуск построителя был единственной незакрытой дверью.
        print("ОТКАЗ: --source-rules требует --source-rules-fp "
              "(sha256 файла правил). Посчитать: python -c \"import "
              "hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb')"
              ".read()).hexdigest())\" <файл>", flush=True)
        return 2
    if args.source_rules:
        import _cos_rules
        raw = args.source_rules.read_bytes()
        if args.source_rules_fp:
            got = hashlib.sha256(raw).hexdigest()
            if got != args.source_rules_fp:
                print("ОТКАЗ: файл правил источника %s не совпал с "
                      "отпечатком команды (%s != %s) — файл подменён "
                      "между стартом задачи и чтением."
                      % (args.source_rules.name, got[:16],
                         args.source_rules_fp[:16]), flush=True)
                return 2
        # Опознание вида и отказ при неопознанном — в единой точке
        # загрузки (`_cos_rules.load_bytes`). Принимается и служебный
        # JSON, и ДОКУМЕНТ ПОСТАНОВКИ целиком (`CMD-GPT-FINAL-FULL-*.md`):
        # заказчик возвращает один документ, и всякая ручная подготовка
        # между ним и расчётом — место, где они расходятся.
        #
        # Прежде здесь стоял прямой вызов from_parameters, и документ
        # вида `as_dict()` разбирался в НОЛЬ прочитанных ключей: расчёт
        # молча уходил на умолчания, а отпечаток каталога объявлял их
        # правилами заказчика (боевой прогон одной из линий).
        try:
            args.cos_rules = _cos_rules.load_bytes(
                raw, where=args.source_rules.name)
        except _cos_rules.SourceRulesError as exc:
            print("ОТКАЗ: %s" % exc, flush=True)
            return 2
        if args.source_rules_strict and args.cos_rules.source_keys_missing:
            print("ОТКАЗ: файл правил источника неполон — отсутствуют "
                  "ключи: %s. В строгом режиме недостающий ключ означает "
                  "отказ, а не тихое умолчание другой авиакомпании."
                  % ", ".join(args.cos_rules.source_keys_missing), flush=True)
            return 2
        args.cos_rules.dark_definition = args.dark_definition
        # Порог «дальней» стоянки: главное — значение ФАЙЛА правил; явный
        # флаг --trans-tz-hours — перекрытие оператора, и оно называется
        # вслух списком перекрытий. Прежде CLI-умолчание безусловно
        # затирало значение файла (в том числе нейтрализатор контракта
        # кнопки) и выдавало себя за правило заказчика. Файл без ключа —
        # отказ при первом обращении к правилу (`_cos_rules._need`).
        if args.trans_tz_hours is not None:
            _old_trans = args.cos_rules.trans_tz_hours
            if _old_trans is None \
                    or float(_old_trans) != float(args.trans_tz_hours):
                overrides.append(
                    "trans_tz_hours: источник %s -> флаг %s"
                    % (_old_trans, args.trans_tz_hours))
            args.cos_rules.trans_tz_hours = float(args.trans_tz_hours)
        if args.base_tz_offset_min is not None:
            args.cos_rules.base_tz_offset_min = int(args.base_tz_offset_min)
        # Правила источника ПЕРЕКРЫВАЮТ правила яруса 1 — и обязаны сказать
        # об этом вслух. Прежде расчёт шёл по посадкам и стыковке
        # источника, а в отпечаток каталога попадали значения из
        # конфигурации, и список перекрытий оставался пустым: аудитор,
        # читающий отпечаток, воспроизвёл бы постановку неверно.
        r = args.cos_rules
        # ГЕЙТ шага перекрытий: правила, читаемые сразу после загрузки,
        # обязаны быть в файле правил (или пакете постановки). Молчаливо
        # подставить сюда ярус 1 или умолчание класса значило бы назвать
        # чужие числа правилами источника.
        lacking = [name for name, val in (
            ("min_sit_min", r.min_sit_min),
            ("work_time_limit_by_tier", r.work_time_limit_by_tier),
            ("max_pairing_days", r.max_pairing_days))
            if val is None]
        if lacking:
            print("ОТКАЗ: файл правил источника не несёт %s — дополните "
                  "файл правил (снимок as_dict) или пакет постановки; "
                  "расчёт по умолчанию запрещён"
                  % ", ".join(lacking), flush=True)
            return 2
        # Предел цепочки В МИНУТАХ — отдельное правило от предела в сутках.
        # Оставленный своим (4 суток), он молча обрезал цепочки, тогда как
        # предел источника шире: расчёт шёл по пересечению двух
        # пределов, о чём нигде не было сказано.
        src_pairing_span = r.max_pairing_days * 1440
        if package_mode:
            # Профиль пакета (аудит A1): каждый предел яруса 1 — явный
            # ключ запуска либо ключ контракта, ПРОЧИТАННЫЙ из файла
            # (source_keys_read; умолчание класса CosRules — не норма
            # ВМП). Ни того ни другого — отказ с именем правила; ключ и
            # контракт разошлись — две правды, тоже отказ. Выбранное
            # значение кладётся и в правила: судья (duty_limit_ok) и
            # перебор (max_segs) читают одно число.
            read = set(getattr(r, "source_keys_read", ()) or ())
            resolved = {}
            for arg, key, value in (
                    ("max_segs", "max_landings_per_duty",
                     r.max_landings_per_duty),
                    ("sit_min", "min_sit_min", r.min_sit_min),
                    ("duty_span", "max_duty_span_min", r.max_work_time()),
                    ("pairing_span", "max_pairing_span_min",
                     src_pairing_span)):
                in_file = ((key in read) or
                           (key == "max_duty_span_min"
                            and "work_time_limit_by_tier" in read) or
                           (key == "max_pairing_span_min"
                            and "max_pairing_days" in read))
                flag = getattr(args, arg)
                base_kept = None
                if arg == "duty_span" and \
                        getattr(args, "duty_cap", None) is not None:
                    # Потолок продления подан (--duty-cap): жёсткий предел
                    # смены контракта — потолок; база --duty-span остаётся
                    # порогом штрафа продления (РВ-98, Stage 03.10).
                    base_kept, flag = flag, args.duty_cap
                if flag is not None and in_file \
                        and int(flag) != int(value):
                    print("ОТКАЗ: профиль пакета — две правды о правиле "
                          "%s: ключ запуска %s, контракт %s; какое "
                          "действует, решает постановка, тихий выбор "
                          "запрещён" % (key, flag, value), flush=True)
                    return 2
                if flag is None and not in_file:
                    print("ОТКАЗ: профиль пакета — правило %s не задано "
                          "ни ключом запуска (--%s), ни контрактом "
                          "правил (%s); настройка площадки и умолчание "
                          "класса в этом профиле не читаются — "
                          "дополните ВМП" % (key, arg.replace("_", "-"),
                                             args.source_rules.name),
                          flush=True)
                    return 2
                chosen = int(flag) if flag is not None else int(value)
                setattr(args, arg, chosen)
                resolved[key] = chosen
                if base_kept is not None:
                    # Предел смены правил — потолок (chosen); ключ
                    # --duty-span — по-прежнему база штрафа продления.
                    args.duty_span = int(base_kept)
            r.max_landings_per_duty = resolved["max_landings_per_duty"]
            if r.min_sit_min is None:
                r.min_sit_min = resolved["min_sit_min"]
            args.rules_effective = dict(
                resolved, profile="vmp-package",
                source="ключи запуска + контракт %s; настройка площадки "
                       "не читалась" % args.source_rules.name)
        else:
            for arg, value, key in (
                    ("max_segs", r.max_landings_per_duty,
                     "max_landings_per_duty"),
                    ("sit_min", r.min_sit_min, "min_sit_min"),
                    ("duty_span", r.max_work_time(),
                     "max_duty_span_min"),
                    ("pairing_span", src_pairing_span,
                     "max_pairing_span_min")):
                if getattr(args, arg) is not None \
                        and int(getattr(args, arg)) != int(effective[key]):
                    continue    # оператор перекрыл явным флагом — не трогаем
                if int(effective[key]) != int(value):
                    overrides.append(
                        f"{key}: правила {effective[key]} -> "
                        f"источник {value}")
                setattr(args, arg, int(value))
    # Предел связки из УСЛОВИЙ заказчика строже предела источника и
    # обязан победить: правила источника обрабатываются ПОЗЖЕ условий и
    # прежде молча возвращали шесть суток поверх пяти — расчёт шёл с
    # шестисуточными связками, а баннер условий обещал пятисуточные.
    # Пересечение пределов объявляется вслух тем же списком перекрытий.
    if getattr(args, "conditions", None) is not None:
        cond_span = int(args.conditions.chain_max_days) * 1440
        if args.pairing_span is None or args.pairing_span > cond_span:
            overrides.append(
                f"max_pairing_span_min: источник {args.pairing_span} -> "
                f"условия {cond_span} (П.2.14, {args.conditions.chain_max_days} сут)")
            args.pairing_span = cond_span
    args.rules_overridden = overrides
    args.rules_fingerprint = hashlib.sha256(
        json.dumps(args.rules_effective, sort_keys=True).encode()
    ).hexdigest()[:16]
    if overrides:
        print("ВНИМАНИЕ: параметры расходятся с действующими правилами — "
              "расчёт пойдёт по названным значениям:", flush=True)
        for o in overrides:
            print(f"  {o}", flush=True)

    payload = json.loads(args.payload.read_text(encoding="utf-8"))

    # Идентичность участка — контракт всей линии (внешний аудит R6, P0-01):
    # словарь self.legs ключуется временем провода, и повтор flightNumber
    # молча схлопывал два реальных участка в один, складывая их спрос.
    # Решение то же, что у линий GERAD: непредставимый payload — громкий
    # отказ; повторные номера переименовываются в wire-имена, карта
    # соответствия уезжает в каталог и сверяется этапом 2.
    from _leg_identity import assert_representable
    from _leg_rekey import rekey_or_passthrough
    # Неоднородный состав мест построителю РАЗРЕШЁН: численность цепочки
    # он считает по максимуму каждой роли (ревизия 2026-07-28), а каталог
    # несёт список мест каждой записи — как режим композиции этапа 2.
    # Страж здесь ловит только настоящие препятствия идентичности
    # (повторные номера без flightLegId и подобное).
    assert_representable(payload, "pairing_builder.main",
                         allow_heterogeneous_seats=True)
    payload, wire_map = rekey_or_passthrough(payload)
    args.wire_map = wire_map
    if wire_map:
        print("этап: повторные номера рейсов переименованы в wire-имена "
              "(%d участков) — каталог несёт карту соответствия"
              % len(wire_map), flush=True)

    if args.cos_rules is not None:
        # Пояс, в котором понимаются окна местного времени. Берётся из
        # ДАННЫХ — часового пояса аэропорта базы, — а не из зашитого
        # числа: зашитое молча объявило бы ночными смены другой
        # авиакомпании. Явный флаг остаётся перекрытием.
        if args.base_tz_offset_min is None:
            # A9: контракт побеждает данные. Прежде пояс листа
            # «Аэропорты» молча перекрывал объявленный пакетом сдвиг
            # шкалы (два смысла под одним ключом); нет ни того, ни
            # другого — отказ словами, не счёт по чужому поясу.
            _declared = (args.cos_rules.base_tz_offset_min
                         if getattr(args.cos_rules,
                                    "base_tz_offset_declared", False)
                         else None)
            tz, note, refuse = resolve_time_scale(
                _declared, _base_tz_offset(payload, args.bases),
                fallback=args.cos_rules.base_tz_offset_min)
            if refuse:
                print("ОТКАЗ: " + refuse, flush=True)
                return 2
            print("шкала времени: " + note, flush=True)
            args.cos_rules.base_tz_offset_min = tz
        # Пояс обязан быть ОДИН и для правил, и для счёта ночёвок в цели:
        # иначе оптимум ищется по цели, посчитанной в чужом времени.
        args.tz_offset_min = args.cos_rules.base_tz_offset_min
        if args.cos_rules.duty_span_matrix:
            # Вслух, как всякое действующее правило: читатель отчёта обязан
            # узнать о матрице из вывода, а не из сравнения чисел.
            print("правила системы-источника: матрица предела смены — "
                  "%d строк (окно местного старта x участки, действует "
                  "минимум с ярусом состава)"
                  % len(args.cos_rules.duty_span_matrix), flush=True)
        # Порог «дальней» стоянки может быть не задан ни файлом, ни
        # флагом: это законное состояние до первого ОБРАЩЕНИЯ к правилу
        # (там — отказ словами из `_cos_rules._need`), и печать обязана
        # его пережить, не выдумывая числа.
        trans_view = ("расхождение с базой от %.1f ч"
                      % args.cos_rules.trans_tz_hours
                      if args.cos_rules.trans_tz_hours is not None
                      else "порог не задан (обращение к правилу — отказ)")
        print("правила системы-источника: предел смены по составу %s, "
              "ночное окно %02d:%02d-%02d:%02d по поясу %+d мин, "
              "цепочка до %d суток, «тёмная» смена — определение «%s», "
              "«дальняя» стоянка — %s"
              % (list(args.cos_rules.work_time_limit_by_tier),
                 args.cos_rules.night_start_min // 60,
                 args.cos_rules.night_start_min % 60,
                 args.cos_rules.night_end_min // 60,
                 args.cos_rules.night_end_min % 60,
                 args.cos_rules.base_tz_offset_min,
                 args.cos_rules.max_pairing_days,
                 args.cos_rules.dark_definition,
                 trans_view), flush=True)
        if args.cos_rules.teleportation is None:
            # Сон механизма — законен (отсутствие пар только ужесточает),
            # но вслух: читатель отчёта обязан узнать об этом из вывода.
            print("правила системы-источника: равнозначные станции не "
                  "заданы — стыковка цепочек строго по одному аэропорту",
                  flush=True)

    def _to_wire(cat: dict, name: str) -> dict:
        """Каталог заказчика/закреплений — в wire-имена payload'а.

        После переименования повторных номеров фильтры `x in self.legs`
        отбрасывали бы участки каталога МОЛЧА, и уровень 2 вырождался бы
        без ошибки (внешний аудит R6, P0-01). Перевод либо полный, либо
        громкий отказ.
        """
        wm = getattr(args, "wire_map", None)
        if not wm:
            return cat
        from _leg_rekey import leg_ids_to_wire
        # Ссылка ПО НОМЕРУ на повторный номер неоднозначна: перевести её
        # нельзя, а фильтр `x in self.legs` отбросил бы её молча.
        ambiguous = {rec["flightNumber"] for rec in wm.values()}

        def tr(fns):
            out = leg_ids_to_wire(fns, wm)
            bad = [x for x in out if x in ambiguous]
            if bad:
                raise SystemExit(
                    "ОТКАЗ: каталог %s ссылается на повторные номера рейсов "
                    "по номеру (%s) — при повторных номерах каталог обязан "
                    "нести flightLegId." % (name, ", ".join(sorted(bad)[:5])))
            return out

        for unit in cat.get("selected") or []:
            for key in ("fns", "active_fns"):
                if unit.get(key):
                    unit[key] = tr(unit[key])
            if unit.get("duty_fns"):
                unit["duty_fns"] = [tr(g) for g in unit["duty_fns"]]
        print("этап: каталог %s переведён в wire-имена" % name, flush=True)
        return cat

    base_catalog = None
    if args.base_catalog:
        # Контент-хэш от ТЕХ ЖЕ байтов, что идут в разбор (аудит R10,
        # P1-02): содержимое базового каталога меняет пространство
        # допустимых смен, а подпись несла только путь — два прогона с
        # разными базовыми каталогами имели одну личность.
        raw = args.base_catalog.read_bytes()
        args.base_catalog_sha256 = hashlib.sha256(raw).hexdigest()
        base_catalog = _to_wire(json.loads(raw.decode("utf-8")),
                                args.base_catalog.name)
    inject_catalogs = []
    if getattr(args, "inject_catalog", None):
        shas = []
        for pth in args.inject_catalog:
            raw = pth.read_bytes()
            shas.append(hashlib.sha256(raw).hexdigest())
            inject_catalogs.append(_to_wire(canon_names_to_payload(
                json.loads(raw.decode("utf-8")), payload, pth.name),
                pth.name))
        args.inject_catalog_sha256 = shas
        print("точечный добор: порций %d, конструкций %d (%s)"
              % (len(inject_catalogs),
                 sum(len(c.get("selected") or []) for c in inject_catalogs),
                 ", ".join(p2.name for p2 in args.inject_catalog)),
              flush=True)
    pin_catalog = None
    if args.pin_catalog:
        raw = args.pin_catalog.read_bytes()
        args.pin_catalog_sha256 = hashlib.sha256(raw).hexdigest()
        pin_catalog = _to_wire(json.loads(raw.decode("utf-8")),
                               args.pin_catalog.name)
        print("режим закрепления: %d структур из %s — неприкосновенны, "
              "стоимость честная"
              % (len(pin_catalog.get("selected") or []),
                 args.pin_catalog.name), flush=True)
    try:
        catalog, report = build(payload, args, base_catalog, pin_catalog,
                                inject_catalogs=inject_catalogs or None)
    except BuilderConfigError as exc:
        # Единый типизированный отказ построителя (пакет «умолчания-в-
        # данные»): значение, обязанное прийти из данных, не пришло.
        # Сам страж живёт в Builder.__init__ — внутрипроцессные вызовы
        # (тесты, внешние тулы) получают тот же отказ, а контракт кодов
        # замоканного build (rc=3) не задевается.
        print("ОТКАЗ: %s" % exc, flush=True)
        return 2
    out = args.out or (FINAL_REPORT
                       / f"gerad_colgen_selected_{args.label}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Канонический каталог публикуется, только если покрытие подтверждено
    # (аудит 2026-07-26, P0-05). Прогон, где перебор не построил цепочку
    # для достижимого участка, — это недоработка расчёта: его результат
    # уезжает в отложенный файл, чтобы им можно было заняться, но этап 2
    # его не подхватит и не выдаст за готовый каталог. Участки, которые
    # непокрываемы В ПРИНЦИПЕ (нечем добраться или вернуться), публикацию
    # не блокируют: это свойство данных, а не расчёта, и оно записано в
    # свидетельстве покрытия внутри каталога.
    cov = catalog["coverage"]
    # БЕЛЫЙ СПИСОК публикации у самого писателя (внешний аудит R7, P0-05):
    # прежнее «всё, кроме INCOMPLETE_SEARCH» публиковало под каноническим
    # именем и RELAXED_COVER, и любой будущий вердикт — ослабленный каталог
    # с кодом 0 подменял последний годный, а отказ всплывал лишь на этапе 2.
    published = cov["verdict"] in ("OK", "INFEASIBLE_INPUT")
    if cov["verdict"] == "INCOMPLETE_SEARCH" and args.accept_incomplete_search:
        published = True
        print("ВНИМАНИЕ: каталог опубликован с вердиктом INCOMPLETE_SEARCH "
              "по явному ключу --accept-incomplete-search; факт записан в "
              "отпечаток постановки.", flush=True)
    if not published:
        out = out.with_name(out.stem + ".pending.json")
    out.write_text(json.dumps(catalog), encoding="utf-8")
    check = (args.out or FINAL_REPORT / "x").parent \
        / f"pairing_builder_check_{args.label}.json"
    check.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    print(f"уровень {args.level}: смен {report.get('duties')}, кандидатов "
          f"{report.get('candidates')}, цепочек {report.get('units')}")
    print(f"непокрытых участков: {report.get('uncovered_legs')} "
          f"(нет пары в горизонте {report.get('uncovered_no_pair_in_horizon')}, "
          f"не нашлось цепочки {report.get('uncovered_not_built')}, "
          f"жертва арбитража 0055 "
          f"{report.get('uncovered_arbitration_0055', 0)}); "
          f"человеко-ночей {report.get('person_nights')}, "
          f"человеко-перегонов {report.get('person_rides')}; "
          f"стоимость {report.get('total_cost')}")
    if report.get("lower_bound") is not None:
        print(f"оценка снизу: {report['lower_bound']}; раундов "
              f"{len(report.get('cg_rounds') or [])}")
    print(f"каталог -> {out.name}; отчёт -> {check.name}")
    _reasons = Counter(r.get("reason") for r in
                       (cov.get("not_built_reasons") or {}).values())
    print(f"покрытие: {cov['verdict']} "
          f"(непокрыто {cov['uncovered']} из {cov['legs_total']}: "
          f"доказано невозможно {cov['no_pair_in_horizon']}, "
          f"без доказательства {cov['not_built']}"
          + (" — " + ", ".join(f"{k}: {n}" for k, n in sorted(
              _reasons.items(), key=lambda kv: str(kv[0])))
             if _reasons else "") + ")")
    if not published:
        if cov["verdict"] == "RELAXED_COVER":
            print("  КАТАЛОГ НЕ ОПУБЛИКОВАН: покрытие ослаблено или "
                  "цепочки урезаны (RELAXED_COVER) — результат отложен в "
                  ".pending для разбора; пересоберите точным покрытием.")
        else:
            print("  КАТАЛОГ НЕ ОПУБЛИКОВАН: есть непокрытые участки без "
                  "доказательства невозможности (поиск не завершён или "
                  "вариант не найден) — это недоработка расчёта или "
                  "неизвестность, а не свойство данных. Расширьте поиск "
                  "или бюджет эскалации; этап 2 такой каталог не "
                  "подхватит.")
    # Коды возврата: 0 — покрытие полное и опубликовано; 2 — перебор не
    # достроил (каталог отложен); 3 — ослабленное покрытие/неизвестный
    # вердикт (каталог отложен); 4 — непокрытое есть, но всё оно
    # непокрываемо в принципе (каталог опубликован, признак внутри).
    if not published:
        return 3 if cov["verdict"] not in ("INCOMPLETE_SEARCH",) else 2
    return 0 if cov["complete"] else 4


if __name__ == "__main__":
    sys.exit(main())
