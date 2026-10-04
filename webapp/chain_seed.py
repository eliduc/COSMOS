"""Г-Д2 — конструктивная затравка непрерывными цепочками (этап Г).

Длинный прогон полного набора выходит на плато жёсткого счёта, 97%
которого — непрерывность маршрута сотрудника («прилетел туда, откуда
следующий вылет»). Дисбаланс кресло-потоков по аэропортам ничтожен —
непрерывные цепочки существуют, но локальный поиск сам их не собирает.
Указание владельца: строить цепочки конструктивно, до локального поиска.

Модуль строит покрытие рейсов ЛИНИЯМИ — непрерывными по аэропортам и
растущими по времени последовательностями рейсов (зазор между посадкой
и следующим вылетом не меньше порога стыковки), затем раздаёт линии
сотрудникам по навыку и базе. Затравка подаётся НАЧАЛЬНЫМ РЕШЕНИЕМ:
поле employee в flightAssignments заполняется идентификатором строкой —
конструктивная эвристика решателя назначенные переменные не сбрасывает,
локальный поиск дошлифовывает под полные правила (пороги, формы
ступени 3, резерв). Пакет и правила не меняются: затравка — стратегия
поиска, не постановка.

Алгоритм линий — жадные обороты по аэропортам во времени с учётом
СМЕН И ОТДЫХА (урок первого замера: линии только по стыковке летают
круглосуточно весь месяц — смена не кончается никогда, и затравка
тонет в нарушениях длительности смены). Рейсы в порядке вылета; линия
может взять рейс тремя способами, в порядке предпочтения:
  1) продолжить текущую смену: зазор >= стыковки И размах смены от её
     начала до посадки рейса <= порога длительности смены;
  2) начать новую смену после отдыха: зазор >= порога отдыха;
  3) не может — открывается новая линия.
Всюду действует страж месячного налёта: линия не берёт рейс, если её
налёт с ним превысит месячный порог (запас у капитанов 0.7% — линии
обязаны быть сбалансированы по налёту, перегруз одной хоронит другую).
Кандидаты в каждом классе берутся «раньше всех освободившиеся» (FIFO).
Всё детерминировано (устойчивые сортировки, без случайности).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from webapp import threshold_gate

# Единственная константа модуля: мёртвая зона решателя ВНЕ пакета — зазор
# от 6 часов до полного отдыха ни стыковка, ни отдых (правило «Rest
# between duty periods» карает поминутно), паузы внутри смены обязаны быть
# КОРОЧЕ. Это НЕ данные заказчика, а контракт Kotlin по умолчанию профиля:
# зеркало DefaultProviderRules.DAY_BREAK_THRESHOLD_MIN
# (FlightCrewSchedulingConstraintProvider.kt), в отчёте применённого —
# показание day_break_threshold_min. ДЛЯ ПАКЕТНЫХ ПРОГОНОВ литерал не
# читается ни решателем (без ручки max_sit_duration порог 0), ни кнопкой
# «Пейринг», ни проверкой по ИДАК: граница «стыковка / отдых» там одна и
# выводится из правил пакета (vmp.sit_boundary; решение владельца
# 06.09.2026). Остальные пороги затравка своими числами не держит: только
# пакет постановки (thresholds_from_package) либо явные аргументы.
SOLVER_DAY_BREAK_MIN = 360

#: Порог затравки -> (имя HARD-параметра пакета, свод при нескольких
#: записях). Имена — те же, что применяет решатель (RunThresholds.KNOBS,
#: SIT_KNOBS); свод — «строже правил»: max для «не меньше» (стыковка,
#: отдых), min для «не больше» (смена, налёт).
_PKG_THRESHOLDS = (
    ("sit_min", "min_turnaround", max),
    ("max_duty_min", "max_duty_duration", min),
    ("rest_base_min", "min_rest_home", max),
    ("rest_out_min", "min_rest_outstation", max),
    ("max_block_min", "max_monthly_block", min),
)


def _collect_thresholds(pkg: dict) -> dict:
    """HARD-параметры пакета постановки -> пороги затравки.

    Пакет вправе нести несколько записей одного имени (области, базы) —
    свод берёт строжайшее. Отсутствие имени, чужая единица или кривое
    значение — отказ словами с перечислением, не подстановка своих чисел.
    """
    by_name: dict[str, list[dict]] = {}
    for p in threshold_gate.collect_params(pkg, kinds=("HARD",)):
        by_name.setdefault(str(p.get("name")), []).append(p)
    out: dict = {}
    problems: list[str] = []
    for key, name, fold in _PKG_THRESHOLDS:
        entries = by_name.get(name, [])
        if not entries:
            problems.append("нет параметра %s" % name)
            continue
        vals: list[float] = []
        for p in entries:
            unit = str(p.get("unit"))
            if unit != "duration_minutes":
                problems.append("%s: единица «%s», ожидается duration_minutes"
                                % (name, unit))
                continue
            v = p.get("value")
            if (not isinstance(v, (int, float)) or isinstance(v, bool)
                    or not math.isfinite(v) or v <= 0):
                problems.append("%s: значение %r не годится порогом (нужно "
                                "конечное положительное число минут)" % (name, v))
                continue
            vals.append(v)
        if len(vals) != len(entries):
            continue                     # причины уже в problems
        agg = fold(vals)
        out[key] = int(agg) if float(agg).is_integer() else agg
    if problems:
        raise RuntimeError(
            "пороги затравки из пакета постановки не собрались: "
            + "; ".join(problems)
            + ". Затравке нужны HARD-параметры пакета (min_turnaround, "
            "max_duty_duration, min_rest_home, min_rest_outstation, "
            "max_monthly_block) либо явные аргументы — своих чисел она "
            "не держит")
    return out


def thresholds_from_package(pkg: dict) -> dict:
    """Пороги chain_seed из пакета постановки.

    У построителя ОДНА ручка отдыха против двух параметров пакета —
    берётся строжайшая (max): затравка строже правил нарушений не
    создаёт, мягче — создаёт. Оба параметра отдыха обязаны присутствовать:
    подстановка соседней области вместо отсутствующей — запрещённое
    угадывание (отказ уже в _collect_thresholds).
    """
    t = _collect_thresholds(pkg)
    return {"sit_min": t["sit_min"], "max_duty_min": t["max_duty_min"],
            "rest_min": max(t["rest_base_min"], t["rest_out_min"]),
            "max_block_min": t["max_block_min"]}


def colgen_thresholds_from_package(pkg: dict) -> dict:
    """Пороги line_colgen из пакета постановки: отдых раздельно по месту."""
    return dict(_collect_thresholds(pkg))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class _Line:
    __slots__ = ("flights", "last_arr_t", "last_arr_a", "duty_start", "block_min")

    def __init__(self) -> None:
        self.flights: list[dict] = []
        self.last_arr_t: datetime | None = None
        self.last_arr_a: str | None = None
        self.duty_start: datetime | None = None
        self.block_min = 0.0

    def take(self, f: dict, dep_t: datetime, arr_t: datetime, new_duty: bool) -> None:
        self.flights.append(f)
        if new_duty or self.duty_start is None:
            self.duty_start = dep_t
        self.last_arr_t = arr_t
        self.last_arr_a = f["arrivalAirport"]
        self.block_min += (arr_t - dep_t).total_seconds() / 60.0


def build_lines(flights: list[dict], *, sit_min: int, max_duty_min: int,
                rest_min: int, max_block_min: int,
                day_break_min: int = SOLVER_DAY_BREAK_MIN) -> list[list[dict]]:
    """Покрытие рейсов линиями: непрерывность по аэропортам, смены и отдых."""
    fs = sorted(flights, key=lambda f: (f["departureUTCDateTime"], f["flightNumber"]))
    sit = timedelta(minutes=sit_min)
    rest = timedelta(minutes=rest_min)
    duty = timedelta(minutes=max_duty_min)
    day_break = timedelta(minutes=day_break_min)
    lines: list[_Line] = []
    for f in fs:
        dep_t = _dt(f["departureUTCDateTime"])
        arr_t = _dt(f["arrivalUTCDateTime"])
        dur = (arr_t - dep_t).total_seconds() / 60.0
        same, rested = [], []
        for i, ln in enumerate(lines):
            if ln.last_arr_a != f["departureAirport"]:
                continue
            if ln.block_min + dur > max_block_min:
                continue
            gap_ok = ln.last_arr_t + sit <= dep_t < ln.last_arr_t + day_break
            if gap_ok and arr_t - ln.duty_start <= duty:
                same.append((ln.last_arr_t, i))
            elif ln.last_arr_t + rest <= dep_t:
                rested.append((ln.last_arr_t, i))
        if same:
            i = min(same)[1]
            lines[i].take(f, dep_t, arr_t, new_duty=False)
        elif rested:
            i = min(rested)[1]
            lines[i].take(f, dep_t, arr_t, new_duty=True)
        else:
            ln = _Line()
            ln.take(f, dep_t, arr_t, new_duty=True)
            lines.append(ln)
    return [ln.flights for ln in lines]


def merge_lines(lines: list[list[dict]], *, rest_min: int,
                max_block_min: int) -> list[list[dict]]:
    """Слияние линий: конец одной — старт другой в том же аэропорту через
    отдых, суммарный налёт в пределах месячного порога. Линий выходит из
    построителя больше, чем сотрудников (пики одновременности, окна
    отдыха); слияние поднимает покрытие затравкой без потери законности
    швов — стык линий и есть отдых. Жадно, хронологически, детерминировано."""
    rest = timedelta(minutes=rest_min)

    def block(line: list[dict]) -> float:
        return sum((_dt(f["arrivalUTCDateTime"]) - _dt(f["departureUTCDateTime"]))
                   .total_seconds() / 60.0 for f in line)

    pool = sorted(lines, key=lambda l: (l[0]["departureUTCDateTime"],
                                        l[0]["flightNumber"]))
    merged: list[list[dict]] = []
    while pool:
        cur = pool.pop(0)
        grew = True
        while grew:
            grew = False
            cur_end_t = _dt(cur[-1]["arrivalUTCDateTime"])
            cur_end_a = cur[-1]["arrivalAirport"]
            cur_block = block(cur)
            for j, cand in enumerate(pool):
                if (cand[0]["departureAirport"] == cur_end_a
                        and _dt(cand[0]["departureUTCDateTime"]) >= cur_end_t + rest
                        and cur_block + block(cand) <= max_block_min):
                    cur = cur + pool.pop(j)
                    grew = True
                    break
        merged.append(cur)
    return merged


def assign_lines(lines: list[list[dict]], employees: list[dict]) -> dict[int, str]:
    """Раздача линий сотрудникам одного навыка: длинные первыми, база — по
    аэропорту старта линии, при нехватке своей базы — любой свободный.
    Линии без людей остаются нерозданными (доскажет решатель), лишние
    люди остаются свободными (естественный резерв)."""
    by_base: dict[str, list[str]] = {}
    for e in sorted(employees, key=lambda e: e["id"]):
        by_base.setdefault(e.get("homeAirport") or "", []).append(e["id"])
    used: set[str] = set()
    out: dict[int, str] = {}
    for i in sorted(range(len(lines)), key=lambda i: (-len(lines[i]), i)):
        start = lines[i][0]["departureAirport"]
        pool = ([x for x in by_base.get(start, []) if x not in used]
                or [x for b in sorted(by_base) for x in by_base[b] if x not in used])
        if pool:
            out[i] = pool[0]
            used.add(pool[0])
    return out


def seed(payload: dict, *, sit_min: int, max_duty_min: int, rest_min: int,
         max_block_min: int,
         day_break_min: int = SOLVER_DAY_BREAK_MIN) -> tuple[dict, dict]:
    """Затравка: копия полезной нагрузки с заполненным employee + сводка.

    Пороги обязательны и прокидываются ВСЕ (раньше уходил только sit_min,
    остальные тихо брались умолчаниями — скрытый дефект): значения — из
    пакета постановки (thresholds_from_package) либо явные."""
    lines = merge_lines(
        build_lines(payload["flights"], sit_min=sit_min,
                    max_duty_min=max_duty_min, rest_min=rest_min,
                    max_block_min=max_block_min, day_break_min=day_break_min),
        rest_min=rest_min, max_block_min=max_block_min)
    seat_by_flight: dict[tuple[str, str], dict] = {}
    assignments = [dict(a) for a in payload["flightAssignments"]]
    for a in assignments:
        fl = a["flight"]
        key = (fl if isinstance(fl, str) else fl["flightNumber"], a["requiredSkill"])
        seat_by_flight[key] = a
    skills = sorted({a["requiredSkill"] for a in assignments})
    summary: dict = {"sit_min": sit_min, "lines": len(lines),
                     "line_lengths": sorted((len(l) for l in lines), reverse=True),
                     "by_skill": {}}
    for skill in skills:
        pool = [e for e in payload["employees"] if skill in (e.get("skills") or [])]
        picked = assign_lines(lines, pool)
        seeded = 0
        for i, emp_id in picked.items():
            for f in lines[i]:
                seat = seat_by_flight.get((f["flightNumber"], skill))
                if seat is not None:
                    seat["employee"] = emp_id
                    seeded += 1
        summary["by_skill"][skill] = {
            "employees": len(pool), "lines_assigned": len(picked),
            "seats_seeded": seeded, "employees_free": len(pool) - len(picked)}
    out = dict(payload)
    out["flightAssignments"] = assignments
    summary["seats_total"] = len(assignments)
    summary["seats_seeded"] = sum(1 for a in assignments if a.get("employee"))
    return out, summary


def seed_with_lines(payload: dict, lines: list[list[dict]],
                    dh_per_flight: int = 0) -> tuple[dict, dict]:
    """Затравка ГОТОВЫМИ линиями (например, colgen): раздача по навыку и
    базе как у seed(); рейс линии с меткой __deadhead__ — пассажирское
    плечо: при dh_per_flight > 0 полезная нагрузка получает ПОЛНЫЙ набор
    перегонных кресел (те же имена, что порождает решатель, — его
    порождение идемпотентно и вторых не добавит), и плечо садится в
    свободное кресло своего рейса."""
    seat_by_flight: dict[tuple[str, str], dict] = {}
    assignments = [dict(a) for a in payload["flightAssignments"]]
    for a in assignments:
        fl = a["flight"]
        key = (fl if isinstance(fl, str) else fl["flightNumber"], a["requiredSkill"])
        seat_by_flight[key] = a
    dh_seats: dict[str, list[dict]] = {}
    if dh_per_flight > 0:
        for f in payload["flights"]:
            leg = f.get("flightLegId") or f["flightNumber"]
            seats = []
            for i in range(1, dh_per_flight + 1):
                seat = {"id": "DH-%s-%d" % (leg, i), "crewSlotId": "DH-%s-%d" % (leg, i),
                        "flight": f["flightNumber"], "indexInFlight": 100 + i,
                        "requiredSkill": "", "deadhead": True}
                seats.append(seat)
                assignments.append(seat)
            dh_seats[f["flightNumber"]] = seats
    skills = sorted({a["requiredSkill"] for a in assignments if a["requiredSkill"]})
    work_lines = [[f for f in ln if not f.get("__deadhead__")] for ln in lines]
    summary: dict = {"lines": len(lines), "by_skill": {}}
    for skill in skills:
        pool = [e for e in payload["employees"] if skill in (e.get("skills") or [])]
        picked = assign_lines(work_lines, pool)
        seeded = dh_used = 0
        for i, emp_id in picked.items():
            for f in lines[i]:
                if f.get("__deadhead__"):
                    for seat in dh_seats.get(f["flightNumber"], ()):
                        if "employee" not in seat:
                            seat["employee"] = emp_id
                            dh_used += 1
                            break
                    continue
                seat = seat_by_flight.get((f["flightNumber"], skill))
                if seat is not None:
                    seat["employee"] = emp_id
                    seeded += 1
        summary["by_skill"][skill] = {
            "employees": len(pool), "lines_assigned": len(picked),
            "seats_seeded": seeded, "dh_seeded": dh_used,
            "employees_free": len(pool) - len(picked)}
    out = dict(payload)
    out["flightAssignments"] = assignments
    summary["seats_total"] = sum(1 for a in assignments if not a.get("deadhead"))
    summary["seats_seeded"] = sum(1 for a in assignments
                                  if a.get("employee") and not a.get("deadhead"))
    summary["dh_seeded"] = sum(1 for a in assignments
                               if a.get("employee") and a.get("deadhead"))
    return out, summary


def continuity_defects(lines: list[list[dict]]) -> int:
    """Самопроверка построителя: разрывов внутри линий обязано быть 0."""
    bad = 0
    for line in lines:
        for a, b in zip(line, line[1:]):
            if a["arrivalAirport"] != b["departureAirport"]:
                bad += 1
            if a["arrivalUTCDateTime"] > b["departureUTCDateTime"]:
                bad += 1
    return bad
