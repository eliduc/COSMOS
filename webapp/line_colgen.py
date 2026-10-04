"""Г-Д5 — колонная генерация линий для затравки полного набора (colgen).

Жадная затравка (webapp/chain_seed.py) упирается в 33–34 линии при 21
капитане: покрытие 84.6% и плато жёсткого счёта на непрерывности.
Точные границы: минимум 27 линий на жадной нарезке смен, нижняя граница
16 на уровне рейсов — зазор принадлежит нарезке. Здесь нарезка и сцепка
оптимизируются ВМЕСТЕ, паттерном колонной генерации (как линия GERAD
проекта: master LP → прайсер → целочисленный отбор):

  1) пространство смен перечисляется ЦЕЛИКОМ (DFS: зазоры внутри смены
     [стыковка, мёртвая зона), размах <= порога) — на полном наборе
     «Поляриса» это ~6 тысяч смен, перебор мгновенный;
  2) master — покрывающая LP-релаксация (GLOP): каждый рейс покрыт,
     цена линии 1, минимизируется число линий; двойственные цены рейсов
     идут прайсеру;
  3) прайсер — метки по ориентированному ациклическому графу смен
     (дуга = отдых: тот же аэропорт, зазор >= порога отдыха по месту)
     с ресурсом месячного налёта и парето-доминированием; отдаёт линии
     с отрицательной приведённой стоимостью;
  4) целочисленный финал — CP-SAT разбиение по итоговому пулу линий:
     каждый рейс ровно один раз, линий как можно меньше.

Выход — линии для затравки (webapp/chain_seed.seed_with_lines):
непрерывность, смены, отдых и налёт легальны ПО ПОСТРОЕНИЮ. Пороги —
ТОЛЬКО аргументами (значения из пакета постановки —
chain_seed.colgen_thresholds_from_package — либо явные); своих чисел
модуль не держит, отсутствие порогов — отказ словами. Единственная
константа — мёртвая зона cs.SOLVER_DAY_BREAK_MIN (контракт решателя,
не данные заказчика). Всё детерминировано: устойчивые сортировки, без
случайности.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timedelta

from webapp import chain_seed as cs


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class Duty:
    """Смена: непрерывная последовательность рейсов одного экипажа.

    Перегонные плечи (dh_prefix / dh_suffix) — ПАССАЖИРСКИЕ рейсы до
    первого либо после последнего рабочего: они двигают аэропорт и время
    начала/конца смены и входят в её размах (зачёт «в размах, не в
    налёт»), но НЕ входят в покрытие и налёт.
    """
    __slots__ = ("idx", "flights", "start", "end", "start_airport",
                 "end_airport", "block_min", "flight_ids",
                 "dh_prefix", "dh_suffix")

    def __init__(self, idx: int, flights: list[dict],
                 dh_prefix: dict | None = None, dh_suffix: dict | None = None):
        self.idx = idx
        self.flights = flights
        self.dh_prefix = dh_prefix
        self.dh_suffix = dh_suffix
        # «Пустая смена» из одного перегона: рабочих рейсов нет, экипаж
        # только перемещается — покрытие и налёт нулевые, сцепка живая.
        first = dh_prefix or flights[0]
        last = dh_suffix or (flights[-1] if flights else dh_prefix)
        self.start = _dt(first["departureUTCDateTime"])
        self.end = _dt(last["arrivalUTCDateTime"])
        self.start_airport = first["departureAirport"]
        self.end_airport = last["arrivalAirport"]
        self.block_min = sum(
            (_dt(f["arrivalUTCDateTime"]) - _dt(f["departureUTCDateTime"]))
            .total_seconds() / 60.0 for f in flights)
        self.flight_ids = tuple(f["flightNumber"] for f in flights)


def build_duty_pool(flights: list[dict], *, sit_min: int, max_duty_min: int,
                    day_break_min: int = cs.SOLVER_DAY_BREAK_MIN) -> list[Duty]:
    """Все допустимые смены: DFS по внутрисменным преемникам."""
    fs = sorted(flights, key=lambda f: (f["departureUTCDateTime"], f["flightNumber"]))
    n = len(fs)
    dep_t = [_dt(f["departureUTCDateTime"]) for f in fs]
    arr_t = [_dt(f["arrivalUTCDateTime"]) for f in fs]
    sit = timedelta(minutes=sit_min)
    dz = timedelta(minutes=day_break_min)
    duty = timedelta(minutes=max_duty_min)
    succ: list[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if (fs[j]["departureAirport"] == fs[i]["arrivalAirport"]
                    and arr_t[i] + sit <= dep_t[j] < arr_t[i] + dz):
                succ[i].append(j)
    out: list[Duty] = []
    chain: list[int] = []

    def dfs(start_dep: datetime) -> None:
        out.append(Duty(len(out), [fs[k] for k in chain]))
        for j in succ[chain[-1]]:
            if arr_t[j] - start_dep <= duty:
                chain.append(j)
                dfs(start_dep)
                chain.pop()

    for i in range(n):
        chain.append(i)
        dfs(dep_t[i])
        chain.pop()
    return out


def extend_with_deadheads(duties: list[Duty], flights: list[dict], *,
                          sit_min: int, max_duty_min: int,
                          day_break_min: int = cs.SOLVER_DAY_BREAK_MIN) -> list[Duty]:
    """Пул + варианты смен с перегонным плечом (префикс ЛИБО суффикс).

    Плечо соединяется со сменой по правилам внутрисменного зазора
    [стыковка, мёртвая зона), и размах смены С НИМ держит порог — сама
    затравка нарушений не создаёт. Одно плечо на смену: суточный предел
    перегонов (≤2) остаётся финальному отбору и решателю.
    """
    fs = sorted(flights, key=lambda f: (f["departureUTCDateTime"], f["flightNumber"]))
    sit = timedelta(minutes=sit_min)
    dz = timedelta(minutes=day_break_min)
    duty = timedelta(minutes=max_duty_min)
    by_arr: dict[str, list[dict]] = {}
    by_dep: dict[str, list[dict]] = {}
    for f in fs:
        by_arr.setdefault(f["arrivalAirport"], []).append(f)
        by_dep.setdefault(f["departureAirport"], []).append(f)
    out = list(duties)
    for d in duties:
        # префикс: пассажиром ПРИЕХАТЬ к началу смены
        for g in by_arr.get(d.start_airport, ()):
            ga = _dt(g["arrivalUTCDateTime"])
            if (ga + sit <= d.start < ga + dz
                    and d.end - _dt(g["departureUTCDateTime"]) <= duty):
                out.append(Duty(len(out), d.flights, dh_prefix=g))
        # суффикс: пассажиром УЕХАТЬ после конца смены
        for g in by_dep.get(d.end_airport, ()):
            gd = _dt(g["departureUTCDateTime"])
            if (d.end + sit <= gd < d.end + dz
                    and _dt(g["arrivalUTCDateTime"]) - d.start <= duty):
                out.append(Duty(len(out), d.flights, dh_suffix=g))
    # «Пустые смены»: день-перегон между рабочими сменами — единственный
    # способ сцепки, когда в аэропорту конца нет продолжения. Каждый рейс
    # сети — кандидат в чистое пассажирское плечо.
    for g in fs:
        out.append(Duty(len(out), [], dh_prefix=g))
    return out


class DutyGraph:
    """Дуги отдыха между сменами: тот же аэропорт, зазор >= отдыха по месту.

    Оба порога отдыха и множество баз обязательны: пороги — раздельно из
    min_rest_home / min_rest_outstation пакета постановки (данные различают
    720/600 — одна цифра на обе области лгала бы), базы — множество
    homeAirport сотрудников полезной нагрузки (ровно так строит множество
    баз решатель, RunThresholds.kt)."""

    def __init__(self, duties: list[Duty], *, rest_base_min: int,
                 rest_out_min: int, bases: frozenset[str]):
        self.duties = duties
        self.rest_base = timedelta(minutes=rest_base_min)
        self.rest_out = timedelta(minutes=rest_out_min)
        self.bases = bases
        by_airport: dict[str, list[Duty]] = {}
        for d in duties:
            by_airport.setdefault(d.start_airport, []).append(d)
        self._starts: dict[str, tuple[list[datetime], list[Duty]]] = {}
        for a, ds in by_airport.items():
            ds.sort(key=lambda d: (d.start, d.idx))
            self._starts[a] = ([d.start for d in ds], ds)

    def rest_at(self, airport: str) -> timedelta:
        return self.rest_base if airport in self.bases else self.rest_out

    def successors(self, d: Duty) -> list[Duty]:
        pair = self._starts.get(d.end_airport)
        if pair is None:
            return []
        times, ds = pair
        lo = bisect_left(times, d.end + self.rest_at(d.end_airport))
        return ds[lo:]


def price_lines(graph: DutyGraph, duals: dict[str, float], *,
                max_block_min: int,
                labels_per_duty: int = 8, top_k: int = 120,
                eps: float = 1e-6,
                rc_threshold: float = 1.0,
                max_work_days: int | None = None) -> list[tuple[float, tuple[int, ...]]]:
    """Прайсер: линии с приведённой стоимостью < -eps.

    Метка у смены — (ценность = сумма двойственных цен покрытых рейсов,
    налёт, РАБОЧИЕ ДНИ, путь). Рабочие дни — второй ресурс рядом с
    налётом (2026-08-26): линия в пределе налёта может касаться 24-25
    суток месяца и съесть выходные людей по построению; при заданном
    [max_work_days] прайсер не порождает таких линий вовсе. Дни метки
    ведутся скаляром (счёт + последняя дата): дни одной смены
    непрерывны, поэтому прирост считается точно. Парето-доминирование
    по (ценность, налёт, дни, последняя дата), не более
    [labels_per_duty] меток на смену (усечение честное: прайсер вправе
    вернуть не всё — master просто сойдётся за больше шагов; сертификат
    оптимальности LP из этого прайсера не выводится и не заявляется).
    Возвращает до [top_k] лучших колонок: (приведённая стоимость, смены).
    """
    duties = sorted(graph.duties, key=lambda d: (d.start, d.idx))
    value = {d.idx: sum(duals.get(f, 0.0) for f in d.flight_ids) for d in duties}
    dspan = {}
    for d in duties:
        if d.flights:
            f0 = _dt(d.flights[0]["departureUTCDateTime"]).date()
            f1 = _dt(d.flights[-1]["arrivalUTCDateTime"]).date()
            dspan[d.idx] = (f0, f1, (f1 - f0).days + 1)
        else:
            dspan[d.idx] = (None, None, 0)   # пустая смена-перегон
    labels: dict[int, list[tuple]] = {}

    def push(d: Duty, val: float, block: float, days: int, last,
             path: tuple[int, ...]) -> bool:
        # Страж налёта и у ПЕРВОЙ смены пути: без него одиночная смена
        # сверх предела получала метку и уходила колонкой в пул — линия
        # нарушала бы месячный порог по построению.
        if block > max_block_min:
            return False
        if max_work_days is not None and days > max_work_days:
            return False
        cur = labels.setdefault(d.idx, [])
        for v, b, dy, ld, _ in cur:
            if (v >= val - eps and b <= block + eps and dy <= days
                    and (ld is None or last is None or ld <= last)):
                return False
        cur[:] = [(v, b, dy, ld, p) for v, b, dy, ld, p in cur
                  if not (val >= v - eps and block <= b + eps and days <= dy
                          and (last is None or ld is None or last <= ld))]
        cur.append((val, block, days, last, path))
        cur.sort(key=lambda x: (-x[0], x[1], x[2]))
        del cur[labels_per_duty:]
        return True

    def grown(days: int, last, di: int) -> tuple[int, object]:
        f0, f1, nd = dspan[di]
        if nd == 0:
            return days, last
        add = nd - (1 if (last is not None and f0 == last) else 0)
        return days + add, f1

    for d in duties:
        d0, l0 = grown(0, None, d.idx)
        push(d, value[d.idx], d.block_min, d0, l0, (d.idx,))
        for val, block, days, last, path in list(labels.get(d.idx, [])):
            for nxt in graph.successors(d):
                nb = block + nxt.block_min
                if nb > max_block_min:
                    continue
                ndays, nlast = grown(days, last, nxt.idx)
                push(nxt, val + value[nxt.idx], nb, ndays, nlast,
                     path + (nxt.idx,))
    best: list[tuple[float, tuple[int, ...]]] = []
    for lset in labels.values():
        for val, _, _, _, path in lset:
            rc = rc_threshold - val
            if rc < -eps:
                best.append((rc, path))
    best.sort(key=lambda x: (x[0], x[1]))
    return best[:top_k]


def _column_key(duties: list[Duty], path: tuple[int, ...]) -> list[dict]:
    """Устойчивое представление колонки для банка: смены кортежами рейсов
    и перегонными плечами (индексы смен между запусками не переносимы)."""
    out = []
    for di in path:
        d = duties[di]
        out.append({"f": list(d.flight_ids),
                    "p": (d.dh_prefix or {}).get("flightNumber"),
                    "s": (d.dh_suffix or {}).get("flightNumber")})
    return out


def load_bank(bank_path, duties: list[Duty], log=None) -> list[tuple[int, ...]]:
    """Колонки банка → пути по ТЕКУЩЕМУ пулу смен; несопоставимые (пороги
    сменились, смены нет) отбрасываются с числом в лог — честно, не молча."""
    import json as _json
    from pathlib import Path as _P
    f = _P(bank_path)
    if not f.exists():
        return []
    by_key = {(tuple(d.flight_ids),
               (d.dh_prefix or {}).get("flightNumber"),
               (d.dh_suffix or {}).get("flightNumber")): d.idx for d in duties}
    cols, dropped = [], 0
    for col in _json.loads(f.read_text(encoding="utf-8")):
        path = []
        ok = True
        for duty in col:
            key = (tuple(duty["f"]), duty.get("p"), duty.get("s"))
            di = by_key.get(key)
            if di is None:
                ok = False
                break
            path.append(di)
        if ok and path:
            cols.append(tuple(path))
        else:
            dropped += 1
    if log:
        log("банк колонн: загружено %d, отброшено несопоставимых %d" % (len(cols), dropped))
    return cols


def save_bank(bank_path, duties: list[Duty], pool: list[tuple[int, ...]]) -> None:
    import json as _json
    from pathlib import Path as _P
    _P(bank_path).write_text(_json.dumps(
        [_column_key(duties, p) for p in pool], ensure_ascii=False),
        encoding="utf-8")


def dive_cover(flights: list[dict], duties: list[Duty], graph: DutyGraph,
               pool: list[tuple[int, ...]], max_lines: int, *,
               max_block_min: int,
               max_dh_per_flight: int = 0,
               rounds_per_step: int = 2,
               pool_admissible=None,
               max_work_days: int | None = None,
               log=None) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]]]:
    """Нырки (price-and-dive) против плато целочисленного отбора.

    Разрыв LP↔целое у покрытия структурный: дробное покрытие размазано
    по многим пересекающимся колонкам, и CP-SAT по готовому пулу стоит
    на плато. Нырок доводит дробное решение до целого ПОСЛЕДОВАТЕЛЬНО:
    LP по остатку → фиксация колонки с максимальным LP-значением →
    пере-прайсинг на графе, суженном до непокрытых рейсов, — новые
    колонки подгоняются под уже выбранные. Выбранные пути попарно
    дизъюнктны по рейсам и чтят кресла перегонов ПО ПОСТРОЕНИЮ.
    Детерминировано. Возвращает (выбранные пути, новые колонки)."""
    from ortools.linear_solver import pywraplp

    flight_ids = [f["flightNumber"] for f in flights]
    rest_base = int(graph.rest_base.total_seconds() // 60)
    rest_out = int(graph.rest_out.total_seconds() // 60)
    covered: set[str] = set()
    dh_used: dict[str, int] = {}
    fixed: list[tuple[int, ...]] = []
    seen = set(pool)
    fresh_cols: list[tuple[int, ...]] = []

    def flights_of(p: tuple[int, ...]) -> list[str]:
        out: list[str] = []
        for di in p:
            out.extend(duties[di].flight_ids)
        return out

    def dh_of(p: tuple[int, ...]) -> list[str]:
        out: list[str] = []
        for di in p:
            d = duties[di]
            for g in (d.dh_prefix, d.dh_suffix):
                if g is not None:
                    out.append(g["flightNumber"])
        return out

    def feasible(p: tuple[int, ...]) -> bool:
        fls = flights_of(p)
        if not fls:
            return False               # покрытия не добавит
        if pool_admissible is not None and not pool_admissible(p):
            return False               # пороги пула вызывающей стороны
        if any(f in covered for f in fls):
            return False               # дизъюнктность с выбранным
        dh = dh_of(p)
        if dh and max_dh_per_flight <= 0:
            return False
        cnt: dict[str, int] = {}
        for f in dh:
            cnt[f] = cnt.get(f, 0) + 1
        return all(dh_used.get(f, 0) + c <= max_dh_per_flight
                   for f, c in cnt.items())

    cand = [p for p in pool if feasible(p)]
    while len(fixed) < max_lines and len(covered) < len(flight_ids):
        cand = [p for p in cand if feasible(p)]
        if not cand:
            break
        remaining = [f for f in flight_ids if f not in covered]
        picked = None
        smoothed: dict[str, float] = {}
        for rnd in range(rounds_per_step):
            lp = pywraplp.Solver.CreateSolver("GLOP")
            xs = [lp.NumVar(0.0, lp.infinity(), "x%d" % i)
                  for i in range(len(cand))]
            ys = {fid: lp.NumVar(0.0, 1.0, "y_" + fid) for fid in remaining}
            cons = {}
            for fid in remaining:
                c = lp.Constraint(0.0, lp.infinity(), "c_" + fid)
                c.SetCoefficient(ys[fid], -1.0)
                cons[fid] = c
            for i, p in enumerate(cand):
                for fid in flights_of(p):
                    if fid in cons:
                        cons[fid].SetCoefficient(
                            xs[i], cons[fid].GetCoefficient(xs[i]) + 1.0)
            card = lp.Constraint(-lp.infinity(),
                                 float(max_lines - len(fixed)), "cardinality")
            for x in xs:
                card.SetCoefficient(x, 1.0)
            obj = lp.Objective()
            for y in ys.values():
                obj.SetCoefficient(y, 1.0)
            obj.SetMaximization()
            if lp.Solve() != pywraplp.Solver.OPTIMAL:
                break
            # снимок ДО дополнения пула: выбор идёт по решённому LP
            picked = (list(cand), [x.solution_value() for x in xs],
                      obj.Value())
            if rnd + 1 >= rounds_per_step:
                break
            raw = {fid: abs(cons[fid].dual_value()) for fid in remaining}
            sigma = abs(card.dual_value())
            alpha = 0.5 if smoothed else 1.0
            smoothed = {fid: alpha * raw[fid]
                        + (1 - alpha) * smoothed.get(fid, 0.0)
                        for fid in remaining}
            sub = DutyGraph(
                [d for d in duties
                 if all(f not in covered for f in d.flight_ids)],
                rest_base_min=rest_base, rest_out_min=rest_out,
                bases=graph.bases)
            cols = price_lines(sub, smoothed, max_block_min=max_block_min,
                               rc_threshold=sigma, max_work_days=max_work_days)
            add = [p for _, p in cols if p not in seen and feasible(p)]
            if not add:
                break
            for p in add:
                seen.add(p)
                fresh_cols.append(p)
                cand.append(p)
        if picked is None:
            break
        snap, vals, lp_obj = picked
        order = sorted(range(len(snap)),
                       key=lambda i: (-vals[i], -len(flights_of(snap[i])),
                                      snap[i]))
        best = order[0]
        if vals[best] <= 1e-9:
            break                      # LP не берёт ни одной колонки
        p = snap[best]
        fixed.append(p)
        covered.update(flights_of(p))
        for f in dh_of(p):
            dh_used[f] = dh_used.get(f, 0) + 1
        if log:
            log("нырок %d/%d: LP=%.2f, фиксация x=%.3f, покрыто %d/%d"
                % (len(fixed), max_lines, lp_obj, vals[best],
                   len(covered), len(flight_ids)))
    return fixed, fresh_cols


def solve_cover(flights: list[dict], duties: list[Duty], graph: DutyGraph, *,
                sit_min: int, max_duty_min: int, max_block_min: int,
                rounds: int = 60, final_seconds: float = 60.0,
                max_lines: int | None = None,
                max_dh_per_flight: int = 0,
                bank_path=None, dive: bool = False,
                max_work_days: int | None = None,
                log=None) -> list[list[dict]]:
    """Colgen: master LP → прайсер → CP-SAT разбиение. Возвращает линии."""
    from ortools.linear_solver import pywraplp
    from ortools.sat.python import cp_model

    flight_ids = [f["flightNumber"] for f in flights]

    def work_days(path: tuple[int, ...]) -> int:
        ds = set()
        for di in path:
            for f in duties[di].flights:
                d0 = _dt(f["departureUTCDateTime"]).date()
                d1 = _dt(f["arrivalUTCDateTime"]).date()
                d = d0
                while d <= d1:
                    ds.add(d)
                    d = d + __import__("datetime").timedelta(days=1)
        return len(ds)

    def admissible(path: tuple[int, ...]) -> bool:
        # Третья дверь пула (2026-08-26): рабочие ДНИ линии — тот же
        # контракт, что налёт. Линия в 5395 минут может касаться 24-25
        # суток месяца и съесть выходные человека по построению.
        if max_work_days is not None and work_days(path) > max_work_days:
            return False
        return True

    # стартовый пул — жадные линии затравки (законные по построению).
    # Пороги жадного старта — те же, что у пула смен и отбора: своих
    # чисел он не держит; единая ручка отдыха построителя — строжайшая
    # из двух порогов графа («строже правил» нарушений не создаёт).
    pool: list[tuple[int, ...]] = []
    seen: set[tuple[int, ...]] = set()
    greedy_rest_min = int(max(graph.rest_base, graph.rest_out)
                          .total_seconds() // 60)
    # только смены БЕЗ перегонных плеч: жадные линии перегонов не несут
    by_ids = {d.flight_ids: d.idx for d in duties
              if d.dh_prefix is None and d.dh_suffix is None}
    for line in cs.build_lines(flights, sit_min=sit_min,
                               max_duty_min=max_duty_min,
                               rest_min=greedy_rest_min,
                               max_block_min=max_block_min):
        path: list[int] = []
        duty_fls: list[dict] = []
        start = _dt(line[0]["departureUTCDateTime"])
        prev = None
        for f in line:
            if prev is not None:
                gap = _dt(f["departureUTCDateTime"]) - _dt(prev["arrivalUTCDateTime"])
                if gap >= graph.rest_at(prev["arrivalAirport"]):
                    key = tuple(x["flightNumber"] for x in duty_fls)
                    if key in by_ids:
                        path.append(by_ids[key])
                    duty_fls = []
            duty_fls.append(f)
            prev = f
        key = tuple(x["flightNumber"] for x in duty_fls)
        if key in by_ids:
            path.append(by_ids[key])
        if path:
            t = tuple(path)
            # Жадный старт чтит предел налёта ЭТОГО прогона — тот же
            # контракт, что у банка: любая дверь в пул проверяет пороги
            # (жадные линии «законны по построению» лишь для СВОИХ
            # умолчаний, не для порогов вызывающей стороны).
            if sum(duties[di].block_min for di in t) > max_block_min:
                continue
            if not admissible(t):
                continue
            if t not in seen:
                seen.add(t)
                pool.append(t)

    # Банк колонн с диска: пул прошлых запусков не придумывается заново.
    # Колонка банка обязана чтить ПОРОГИ ЭТОГО прогона: банк, собранный
    # при большем пределе налёта, нёс бы нелегальные здесь линии — они
    # отбрасываются с числом, как несопоставимые смены.
    if bank_path is not None:
        over = 0
        for pth in load_bank(bank_path, duties, log=log):
            if sum(duties[di].block_min for di in pth) > max_block_min                     or not admissible(pth):
                over += 1
                continue
            if pth not in seen:
                seen.add(pth)
                pool.append(pth)
        if over and log:
            log("банк колонн: отброшено сверх предела налёта %d" % over)

    def covered(path: tuple[int, ...]) -> list[str]:
        out: list[str] = []
        for di in path:
            out.extend(duties[di].flight_ids)
        return out

    # Мастер и финал обязаны решать ОДНУ задачу (урок v3: LP «покрыть всё
    # минимумом линий» не порождает колонн для «максимума покрытия в K»).
    # При max_lines задан — мастер «максимум покрытия ровно K линиями»:
    #   max Σ y_f;  y_f <= Σ x_l (l покрывает f);  y_f <= 1;  Σ x_l <= K.
    # Прайсеру идут цены покрытия π и цена кардинальности σ: колонка
    # улучшает LP, когда Σπ(линии) > σ.
    smoothed: dict[str, float] = {}
    for rnd in range(rounds):
        lp = pywraplp.Solver.CreateSolver("GLOP")
        xs = [lp.NumVar(0.0, lp.infinity(), "x%d" % i) for i in range(len(pool))]
        cons = {}
        sigma_con = None
        if max_lines is None:
            for fid in flight_ids:
                cons[fid] = lp.Constraint(1.0, lp.infinity(), "c_" + fid)
            for i, path in enumerate(pool):
                for fid in covered(path):
                    cons[fid].SetCoefficient(xs[i], cons[fid].GetCoefficient(xs[i]) + 1.0)
            obj = lp.Objective()
            for x in xs:
                obj.SetCoefficient(x, 1.0)
            obj.SetMinimization()
        else:
            ys = {fid: lp.NumVar(0.0, 1.0, "y_" + fid) for fid in flight_ids}
            for fid in flight_ids:
                c = lp.Constraint(0.0, lp.infinity(), "c_" + fid)
                c.SetCoefficient(ys[fid], -1.0)
                cons[fid] = c
            for i, path in enumerate(pool):
                for fid in covered(path):
                    cons[fid].SetCoefficient(xs[i], cons[fid].GetCoefficient(xs[i]) + 1.0)
            sigma_con = lp.Constraint(-lp.infinity(), float(max_lines), "cardinality")
            for x in xs:
                sigma_con.SetCoefficient(x, 1.0)
            obj = lp.Objective()
            for y in ys.values():
                obj.SetCoefficient(y, 1.0)
            obj.SetMaximization()
        if lp.Solve() != pywraplp.Solver.OPTIMAL:
            break
        raw = {fid: abs(cons[fid].dual_value()) for fid in flight_ids}
        sigma = abs(sigma_con.dual_value()) if sigma_con is not None else 0.0
        # Стабилизация против вырожденности: сглаженные двойственные
        # (Wentges smoothing) — прайсеру даётся смесь прошлого и текущего.
        alpha = 0.5 if smoothed else 1.0
        smoothed = {fid: alpha * raw[fid] + (1 - alpha) * smoothed.get(fid, 0.0)
                    for fid in flight_ids}
        threshold = sigma if max_lines is not None else 1.0
        cols = price_lines(graph, smoothed, max_block_min=max_block_min,
                           rc_threshold=threshold, max_work_days=max_work_days)
        if not [p for _, p in cols if p not in seen]:
            # Сглаженные цены исчерпаны — добираем на сырых.
            cols = price_lines(graph, raw, max_block_min=max_block_min,
                               rc_threshold=threshold,
                               max_work_days=max_work_days)
        fresh = [p for _, p in cols if p not in seen and admissible(p)]
        if log:
            log("colgen шаг %d: LP=%.2f, sigma=%.3f, пул=%d, новых колонок=%d"
                % (rnd + 1, obj.Value(), sigma, len(pool), len(fresh)))
        if not fresh:
            break
        for p in fresh:
            seen.add(p)
            pool.append(p)

    # Нырки: последовательная фиксация против плато целочисленного отбора;
    # их колонки входят в пул (и банк), их отбор — подсказка финалу.
    dive_paths: list[tuple[int, ...]] = []
    if dive and max_lines is not None:
        dive_paths, extra = dive_cover(
            flights, duties, graph, pool, max_lines=max_lines,
            max_dh_per_flight=max_dh_per_flight,
            max_block_min=max_block_min,
            pool_admissible=admissible, max_work_days=max_work_days, log=log)
        for p in extra:
            if p not in seen and admissible(p):
                seen.add(p)
                pool.append(p)
        if log:
            log("нырки: выбрано %d линий, новых колонок %d"
                % (len(dive_paths), len(extra)))

    # Банк на диск ДО финала: придуманное не пропадает при любом исходе.
    if bank_path is not None:
        save_bank(bank_path, duties, pool)
        if log:
            log("банк колонн сохранён: %d колонн" % len(pool))

    # Целочисленный финал. Два режима:
    #   max_lines=None — разбиение (каждый рейс ровно один раз), минимум линий;
    #   max_lines=K   — затравочный: не больше K линий, максимум покрытых
    #                   рейсов (каждый не больше одного раза) — под число
    #                   сотрудников слоя.
    m = cp_model.CpModel()
    use = [m.new_bool_var("l%d" % i) for i in range(len(pool))]
    by_flight: dict[str, list[int]] = {fid: [] for fid in flight_ids}
    dh_by_flight: dict[str, list[int]] = {}
    for i, path in enumerate(pool):
        for fid in covered(path):
            by_flight[fid].append(i)
        for di in path:
            d = duties[di]
            for g in (d.dh_prefix, d.dh_suffix):
                if g is not None:
                    dh_by_flight.setdefault(g["flightNumber"], []).append(i)
    if max_lines is None:
        for fid, idxs in by_flight.items():
            m.add(sum(use[i] for i in idxs) == 1)
        m.minimize(sum(use))
    else:
        covered_vars = []
        for fid, idxs in by_flight.items():
            c = m.new_bool_var("f_" + fid)
            m.add(sum(use[i] for i in idxs) == c)
            covered_vars.append(c)
        m.add(sum(use) <= max_lines)
        m.maximize(sum(covered_vars))
    # Перегонных пассажиров на рейс — не больше предела пакета (кресла).
    if max_dh_per_flight > 0:
        for fid, idxs in dh_by_flight.items():
            m.add(sum(use[i] for i in idxs) <= max_dh_per_flight)
    # Тёплая подсказка финалу: отбор нырков (если был) — допустимый и
    # обычно сильнее жадного; иначе жадные линии старта (первые колонки).
    if max_lines is not None:
        if dive_paths:
            pos = {p: i for i, p in enumerate(pool)}
            hint_idx = {pos[p] for p in dive_paths}
        else:
            hint_idx = set(range(min(max_lines, len(pool))))
        for i, u in enumerate(use):
            m.add_hint(u, 1 if i in hint_idx else 0)
    elif dh_by_flight:
        # Пул с перегонными плечами при выключенных креслах — противоречие
        # вызывающей стороны: честный отказ, не молчаливое использование.
        raise RuntimeError("пул несёт перегонные плечи, а предел перегонов "
                           "на рейс не задан (возможность выключена)")
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = final_seconds
    solver.parameters.num_workers = 8
    st = solver.solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError("CP-SAT не нашёл отбора по пулу (%d колонок)" % len(pool))
    lines: list[list[dict]] = []
    for i, path in enumerate(pool):
        if solver.value(use[i]):
            fls: list[dict] = []
            for di in path:
                d = duties[di]
                if d.dh_prefix is not None:
                    fls.append(dict(d.dh_prefix, __deadhead__=True))
                fls.extend(d.flights)
                if d.dh_suffix is not None:
                    fls.append(dict(d.dh_suffix, __deadhead__=True))
            lines.append(fls)
    return lines


def colgen_lines(payload: dict, log=None, **kw) -> list[list[dict]]:
    """Линии colgen для полезной нагрузки. Пороги — ТОЛЬКО из kw.

    Обязательные kw: sit_min, max_duty_min, rest_base_min, rest_out_min,
    max_block_min — значения порогов пакета постановки
    (chain_seed.colgen_thresholds_from_package) либо явные; своих чисел
    механизм не держит, отсутствие ключей — отказ словами. Базы отдыха —
    множество homeAirport сотрудников полезной нагрузки (как у решателя,
    RunThresholds.kt); пустое множество — отказ, не угадывание.
    kw: max_dh_per_flight > 0 включает перегонные плечи смен (значение —
    из порога пакета max_deadheads_per_flight); max_lines=K включает
    затравочный режим «максимум покрытия ≤K линиями».
    """
    required = ("sit_min", "max_duty_min", "rest_base_min",
                "rest_out_min", "max_block_min")
    missing = [k for k in required if kw.get(k) is None]
    if missing:
        raise RuntimeError(
            "colgen без порогов не считает: не заданы %s. Значения — "
            "пороги пакета постановки "
            "(chain_seed.colgen_thresholds_from_package) либо явные "
            "аргументы" % ", ".join(missing))
    flights = payload["flights"]
    bases = frozenset(e.get("homeAirport") for e in payload.get("employees") or []
                      if e.get("homeAirport"))
    if not bases:
        raise RuntimeError(
            "в полезной нагрузке нет ни одной базы (employees[*].homeAirport "
            "пусты): отдых «по месту» судить не по чему")
    day_break = kw.get("day_break_min", cs.SOLVER_DAY_BREAK_MIN)
    duties = build_duty_pool(flights, sit_min=kw["sit_min"],
                             day_break_min=day_break,
                             max_duty_min=kw["max_duty_min"])
    max_dh = kw.get("max_dh_per_flight", 0)
    if max_dh > 0:
        duties = extend_with_deadheads(
            duties, flights, sit_min=kw["sit_min"],
            day_break_min=day_break, max_duty_min=kw["max_duty_min"])
    graph = DutyGraph(duties, rest_base_min=kw["rest_base_min"],
                      rest_out_min=kw["rest_out_min"], bases=bases)
    if log:
        log("пространство смен: %d (перегонные плечи: %s)"
            % (len(duties), "да" if max_dh > 0 else "нет"))
    return solve_cover(flights, duties, graph,
                       sit_min=kw["sit_min"],
                       max_duty_min=kw["max_duty_min"],
                       max_block_min=kw["max_block_min"],
                       rounds=kw.get("rounds", 60),
                       final_seconds=kw.get("final_seconds", 60.0),
                       max_lines=kw.get("max_lines"),
                       max_dh_per_flight=max_dh,
                       bank_path=kw.get("bank_path"),
                       dive=kw.get("dive", False),
                       max_work_days=kw.get("max_work_days"), log=log)
