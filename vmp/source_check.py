# -*- coding: utf-8 -*-
"""Проверка решения от ПЕРВОИСТОЧНИКА (поручение владельца
2026-08-31): взять готовое решение (пейринг/ростеринг) и пройтись по
всем правилам ИСХОДНЫХ ДОКУМЕНТОВ авиакомпании — по каждому правилу
вердикт «выполнено / нарушено (поимённо, с числами) / машинной
проверке не поддаётся (причина словами)».

Независимость от конвейера формализации — по классу протокола
независимости решателей BA: словесные правила превращает в машинные
проверки ОТДЕЛЬНЫЙ читатель (свой промпт, своя модель), который не
видит ни ВМП, ни промптов формализации — только тексты клауз
замороженных документов. Числа по решению считает детерминированный
код этого модуля. Расхождение с прочтением конвейера (по якорям
клауз) — находка «правило прочитано двояко», а не молчание.

Словарь видов проверок ЗАКРЫТ: компилятор выбирает вид и параметры,
но не изобретает механик. Правило, не ложащееся в словарь, честно
получает вид not_machine_checkable с причиной — протокол не молчит.
Мягкие правила жёсткой проверке не подлежат по смыслу (штраф — не
запрет): протокол считает ФАКТЫ событий (сколько ночёвок и т.п.) и
помечает «мягкое, учтено счётом».
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

#: Закрытый словарь видов машинных проверок расписания.
CHECK_KINDS = (
    "duty_span_max",          # каждая смена <= limit_min
    "rest_min",               # разрыв между сменами >= пола места
    "turnaround_min",         # стыковка внутри смены >= min_min
    "landings_per_duty_max",  # посадок за смену <= limit
    "crew_size_exact",        # на рейсе ровно n РАЗЛИЧНЫХ людей
    "skill_required",         # место закрыто требуемой ролью
    "unavailability",         # назначений в недоступные дни нет
    "type_rating",            # допуск на тип судна
    "pairing_span_max_days",  # размах цепочки <= limit_days
    "reserve_daily_min",      # свободных людей базы в день >= нормы
    "soft_event_count",       # мягкое: только факт (счёт событий)
    "not_machine_checkable",  # честный отказ с причиной
)

#: Виды событий для soft_event_count.
SOFT_EVENTS = ("layover_nights", "layover_nights_in_window",
               "duty_over_threshold")


class SourceCheckError(ValueError):
    """Кривая спецификация проверки — отказ словами (fail-closed)."""


@dataclass
class CheckResult:
    check_id: str
    kind: str
    anchor: str
    verdict: str                  # ok | violated | soft_info | not_checkable
    detail: str
    objects_seen: int = 0
    violations: list = field(default_factory=list)
    notes: list = field(default_factory=list)   # показания, не нарушения


def _t(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", ""))


def _fmt_min(m: float) -> str:
    m = int(round(m))
    return "%d:%02d" % divmod(m, 60)


# ------------------------------------------------------------ timelines

def build_timelines(solution: dict,
                    rest_boundary: dict | None = None) -> dict:
    """Линии времени решения: по сотруднику — назначенные рейсы по
    порядку вылета (перегоны-пассажиром помечены); по цепочке
    (chainId) — то же. Основа всех проверок.

    rest_boundary — действующая граница «стыковка / отдых» T ЭТОГО
    прогона по правилам пакета (vmp.sit_boundary: ручка пакета
    max_sit_duration → показание day_break_threshold_min, без ручки —
    пол минимального отдыха; у построителя — --sit-max того же
    происхождения): {"rest_boundary_min": int | None,
    "rest_boundary_source": str}. Кладётся в линию времени как
    tl["rest_boundary_min"] / tl["rest_boundary_source"]; None —
    показаний прогона нет либо граница правилами пакета не определена
    (SOURCE-REST-R1 и решение владельца 06.09.2026)."""
    flights = {f.get("flightLegId") or f.get("flightNumber"): f
               for f in solution.get("flights") or ()}
    by_emp: dict[str, list] = {}
    by_chain: dict[str, list] = {}
    for fa in solution.get("flightAssignments") or ():
        fl = flights.get(fa.get("flight"))
        if fl is None:
            continue
        row = {
            "flight": fa.get("flight"),
            "dep": fl.get("departureAirport"),
            "arr": fl.get("arrivalAirport"),
            "t0": _t(fl.get("departureUTCDateTime")),
            "t1": _t(fl.get("arrivalUTCDateTime")),
            "skill": fa.get("requiredSkill"),
            "employee": fa.get("employee"),
            "deadhead": bool(fa.get("deadhead")),
            "aircraftType": fl.get("aircraftType"),
        }
        emp = fa.get("employee")
        if emp:
            by_emp.setdefault(emp, []).append(row)
        chain = fa.get("chainId")
        if chain:
            by_chain.setdefault(str(chain), []).append(row)
    for rows in by_emp.values():
        rows.sort(key=lambda r: (r["t0"], r["t1"]))
    for rows in by_chain.values():
        rows.sort(key=lambda r: (r["t0"], r["t1"]))
    rb = rest_boundary if isinstance(rest_boundary, dict) else {}
    bmin = rb.get("rest_boundary_min")
    by_place = rb.get("rest_boundary_by_place")
    return {"by_employee": by_emp, "by_chain": by_chain,
            "employees": {e.get("id"): e
                          for e in solution.get("employees") or ()},
            "flights": flights,
            "rest_boundary_min": (int(bmin) if isinstance(bmin, (int, float))
                                  and not isinstance(bmin, bool) else None),
            "rest_boundary_source": str(rb.get("rest_boundary_source")
                                        or "показаний прогона о границе "
                                           "«стыковка / отдых» не передано"),
            # критерий границы смены: "knob" — порог правила максимальной
            # стыковки; "rest_floor" — пол отдыха по месту (модельное
            # упрощение, слова — rest_boundary_assumption)
            "rest_boundary_basis": rb.get("rest_boundary_basis"),
            "rest_boundary_by_place": (dict(by_place)
                                       if isinstance(by_place, dict) else None),
            "rest_boundary_assumption": rb.get("rest_boundary_assumption")}


def split_duties(rows: list, rest_split_min: int) -> list[list]:
    """Нарезка линии времени на смены: разрыв не меньше порога
    отдыха = граница смены (независимая, простая семантика: порог
    один, задаётся компилятором из клаузы отдыха)."""
    duties: list[list] = []
    cur: list = []
    duty_end: datetime | None = None
    for r in rows:
        if cur and duty_end is not None:
            gap = (r["t0"] - duty_end).total_seconds() / 60
            if gap >= rest_split_min:
                duties.append(cur)
                cur = []
        cur.append(r)
        duty_end = r["t1"] if duty_end is None or r["t1"] > duty_end \
            else duty_end
    if cur:
        duties.append(cur)
    return duties


def split_duties_by_place(rows: list, home: str | None,
                          by_place: dict) -> list[list]:
    """Нарезка линии времени на смены по полу отдыха ПО МЕСТУ (критерий
    границы смены без правила максимальной стыковки — модельное
    упрощение, vmp.sit_boundary): разрыв не меньше пола отдыха на месте
    прилёта последнего участка смены (дома → home, иначе outstation) —
    граница смены. Зеркало NormativeDuties.slice решателя (порог даёт
    место) и sit_cap_at построителя."""
    duties: list[list] = []
    cur: list = []
    duty_end: datetime | None = None
    for r in rows:
        if cur and duty_end is not None:
            gap = (r["t0"] - duty_end).total_seconds() / 60
            at_home = home is not None and cur[-1]["arr"] == home
            need = by_place.get("home" if at_home else "outstation")
            if need is None:
                need = by_place.get("outstation" if at_home else "home")
            if need is not None and gap >= int(need):
                duties.append(cur)
                cur = []
                duty_end = None
        cur.append(r)
        duty_end = r["t1"] if duty_end is None or r["t1"] > duty_end \
            else duty_end
    if cur:
        duties.append(cur)
    return duties


def _duty_span_min(duty: list) -> float:
    return (max(r["t1"] for r in duty)
            - duty[0]["t0"]).total_seconds() / 60


# ------------------------------------------ критерий нарезки смен прогона

def duty_criterion(tl: dict) -> dict:
    """ОДИН критерий нарезки линии времени на смены у ЭТОГО прогона —
    для всех проверок, единицей которых является смена (длительность
    смены, стыковка внутри смены, посадки за смену, мягкий счёт смен
    сверх порога) и для отдыха между сменами (находка GPT-059 /
    P3-DUTY-BOUNDARY-WIRE, 06.09.2026: проверка длительности смены
    резала по числу читателя rest_split_min и давала ложное «ok» там,
    где по принятому критерию границы смена одна).

    По показаниям прогона из линии времени (build_timelines,
    vmp.sit_boundary):
    - basis "rest_floor" с полами по месту (tl["rest_boundary_by_place"])
      → mode "by_place": разрыв не короче пола отдыха на месте прилёта
      — граница смены (модельное упрощение, заявлено);
    - иначе T = tl["rest_boundary_min"] > 0 (basis "knob" либо явное
      показание T) → mode "threshold": разрыв не короче T — граница;
    - иначе (показаний нет, T=0 без полов) → mode None с причиной
      словами: нарезка на смены не выносится, число читателя
      rest_split_min не подставляется.

    Возврат: {"mode": "by_place" | "threshold" | None, "T": int | None,
              "by_place": dict | None, "words": показание для
              подробностей, "reason": причина при mode None}."""
    basis = tl.get("rest_boundary_basis")
    t = tl.get("rest_boundary_min")
    by_place = tl.get("rest_boundary_by_place") or {}
    source = str(tl.get("rest_boundary_source") or "источник не назван")
    if basis == "rest_floor" and (by_place.get("home")
                                  or by_place.get("outstation")):
        return {"mode": "by_place", "T": t, "by_place": dict(by_place),
                "reason": None,
                "words": ("смены нарезаны критерием прогона: пол отдыха "
                          f"по месту дома {by_place.get('home')} / вне "
                          f"базы {by_place.get('outstation')} (basis "
                          "rest_floor, модельное упрощение)")}
    if isinstance(t, int) and t > 0:
        tag = "knob" if basis != "rest_floor" else "rest_floor, единый пол"
        return {"mode": "threshold", "T": t, "by_place": None,
                "reason": None,
                "words": ("смены нарезаны критерием прогона: "
                          f"T={t} мин (basis {tag})")}
    why = ("T=0: в пакете нет ручки max_sit_duration, пол минимального "
           "отдыха показаниями не назван" if t == 0
           else "показаний решателя/построителя о ней нет")
    return {"mode": None, "T": t, "by_place": None, "words": "",
            "reason": ("граница стыковки/отдыха у прогона не объявлена и "
                       f"из пола отдыха не выведена ({why}; {source}) — "
                       "нарезка на смены не выносится")}


def duties_of_run(tl: dict, rows: list, home: str | None,
                  crit: dict | None = None) -> list[list]:
    """Смены одного сотрудника критерием прогона (duty_criterion):
    by_place → split_duties_by_place, threshold → split_duties;
    неопределённая граница — SourceCheckError с причиной (исполнители
    отвечают not_checkable раньше, сюда не доходят)."""
    crit = crit or duty_criterion(tl)
    if crit["mode"] == "by_place":
        return split_duties_by_place(rows, home, crit["by_place"])
    if crit["mode"] == "threshold":
        return split_duties(rows, int(crit["T"]))
    raise SourceCheckError(crit["reason"])


# ------------------------------------------------------------ executors

def _iter_emp_duties(tl: dict, crit: dict):
    emps = tl["employees"]
    for emp, rows in sorted(tl["by_employee"].items()):
        home = (emps.get(emp) or {}).get("homeAirport")
        for duty in duties_of_run(tl, rows, home, crit):
            yield emp, duty


def _reader_split_note(spec: dict) -> str | None:
    """Число читателя rest_split_min — только показание: никогда не
    критерий нарезки (ни 480 по умолчанию, ни явное значение)."""
    declared = spec["params"].get("rest_split_min")
    return (f"значение читателя rest_split_min={declared} не применяется"
            if declared is not None else None)


def _duty_gate(spec: dict, tl: dict, res: CheckResult) -> dict | None:
    """Гейт проверок с единицей «смена»: критерий прогона есть —
    показание в notes и критерий; нет — not_checkable с причиной теми же
    словами, что у rest_min, и None."""
    crit = duty_criterion(tl)
    note = _reader_split_note(spec)
    if crit["mode"] is None:
        res.verdict = "not_checkable"
        res.detail = crit["reason"] + (f"; {note}" if note else "")
        return None
    res.notes.append(crit["words"])
    if note:
        res.notes.append(note)
    return crit


def _run_duty_span_max(spec: dict, tl: dict) -> CheckResult:
    limit = int(spec["params"]["limit_min"])
    res = _mk(spec, "ok")
    crit = _duty_gate(spec, tl, res)
    if crit is None:
        return res
    for emp, duty in _iter_emp_duties(tl, crit):
        res.objects_seen += 1
        span = _duty_span_min(duty)
        if span > limit:
            res.violations.append(
                f"{emp}: смена {duty[0]['t0']:%Y-%m-%d %H:%M} длиной "
                f"{_fmt_min(span)} > предела {_fmt_min(limit)} "
                f"(превышение {_fmt_min(span - limit)})")
    return _seal(res, "смен проверено")


def _rest_floors_of_spec(spec: dict):
    """Полы отдыха клаузы rest_min ПО МЕСТУ из params спецификации
    читателя: (home_min | None, outstation_min | None, показание для
    notes) либо None — ни одного пола нет.

    - оба ключа home_min и outstation_min → оба места, показания нет;
    - только home_min (только outstation_min) → судится одно место,
      показание «проверка по месту: только дома (пол R); отдых вне базы
      этой проверкой не судится» (симметрично для вне базы);
    - единый min_min → пол каждого места, не заданного явно, с
      показанием «единый пол отдыха min_min=R — оба места …»;
    - ни одного ключа с полом → None (исполнитель отвечает
      not_checkable словами, не KeyError).

    Кривое значение (не число) — ValueError, как и прежде: обёртка
    run_checks отвечает «кривая спецификация» fail-closed."""
    params = spec.get("params") or {}

    def _floor(key):
        v = params.get(key)
        return None if v is None else int(v)

    home_min = _floor("home_min")
    out_min = _floor("outstation_min")
    single = _floor("min_min")
    note = None
    if home_min is None and out_min is None:
        if single is None:
            return None
        home_min = out_min = single
        note = (f"единый пол отдыха min_min={single} — оба места (дома и "
                "вне базы) судятся одним полом")
    elif single is not None and (home_min is None or out_min is None):
        # единый пол закрывает место, не названное явно
        home_min = single if home_min is None else home_min
        out_min = single if out_min is None else out_min
        note = (f"единый пол отдыха min_min={single} применён к месту, не "
                "названному явно (дома и вне базы судятся оба)")
    elif out_min is None:
        note = (f"проверка по месту: только дома (пол {home_min}); отдых "
                "вне базы этой проверкой не судится")
    elif home_min is None:
        note = (f"проверка по месту: только вне базы (пол {out_min}); "
                "отдых дома этой проверкой не судится")
    return home_min, out_min, note


def _run_rest_min(spec: dict, tl: dict) -> CheckResult:
    """Отдых между сменами — ТЕМ ЖЕ контрактом, что решатель, для ЭТОГО
    прогона (SOURCE-REST-R1, 06.09.2026). Граница «стыковка / отдых» T
    — не константа и не число читателя, а показание прогона из линии
    времени (tl["rest_boundary_min"]), выведенное по одному определению с
    кнопкой и решателем (vmp.sit_boundary, решение владельца 06.09.2026):
    ростеринг — ручка max_sit_duration показаний решателя этого runId
    (day_break_threshold_min), без ручки — пол минимального отдыха тех же
    показаний; пейринг — --sit-max построителя того же происхождения:

    - basis "knob" (правило максимальной стыковки, T > 0): разрыв
      gap ≥ T между сменами — интервал отдыха; нарушение при T ≤ gap < R,
      где R — пол отдыха по месту из клаузы (home_min в базе /
      outstation_min вне базы). Разрыв короче T — стыковка внутри смены,
      пол отдыха к ней не применяется (Kotlin «Rest between duty
      periods»: штраф при dayBreakThreshold > 0 и gap ∈ [T, minRestAt)).
    - basis "rest_floor" (ручки нет): критерий границы смены — пол
      минимального отдыха ПО МЕСТУ из показаний прогона
      (tl["rest_boundary_by_place"], split_duties_by_place) — явное
      модельное упрощение (tl["rest_boundary_assumption"] — в
      показаниях результата); достаточность отдыха судится полом клаузы
      по месту отдельно. Разрыв не короче пола — отдых не короче пола:
      нарушений по построению нет, проверка честно даёт ok с показанием
      «интервалов отдыха N» — ровно поведение решателя без ручки
      (нормативные смены режутся по полу отдыха по месту).
    - T = 0 (явное показание без вывода из пола): граница не объявлена
      и не выведена — честное not_checkable с причиной, не приговор по
      литералу.
    - T неизвестна (показаний прогона нет либо граница правилами пакета
      не определена) — not_checkable с причиной.

    Число читателя rest_split_min — только показание в detail: читатель
    ИДАК выводил порог нарезки меньше границы и считал стыковки отдыхом
    (живой урок раската Stage 06.09: 42 «нарушения» из 46 на законном
    каталоге).

    Полы клаузы ПО МЕСТУ (_rest_floors_of_spec): читатель ИДАК вправе
    скомпилировать правило отдыха как ДВЕ проверки по одному месту
    (живой контур Test 06.09: «отдых дома» с одним home_min и «отдых
    вне базы» с одним outstation_min) — проверка с одним полом судит
    только интервалы отдыха своего места, интервалы другого места ею не
    судятся и в objects_seen не считаются (показание в notes); единый
    min_min — оба места одним полом; ни одного пола — not_checkable с
    причиной словами, не KeyError. Критерий НАРЕЗКИ смен от полов клаузы
    не зависит — он по показаниям прогона (duty_criterion)."""
    res = _mk(spec, "ok")
    floors = _rest_floors_of_spec(spec)
    if floors is None:
        res.verdict = "not_checkable"
        res.detail = ("в спецификации нет пола отдыха (home_min / "
                      "outstation_min / min_min) — проверка не судится")
        return res
    home_min, out_min, place_note = floors
    declared = spec["params"].get("rest_split_min")
    split = tl.get("rest_boundary_min")
    source = str(tl.get("rest_boundary_source") or "источник не назван")
    reader_note = (f"; число читателя rest_split_min={declared} — "
                   f"показание, не граница" if declared is not None else "")
    if split is None:
        res.verdict = "not_checkable"
        res.detail = ("граница «стыковка / отдых» прогона неизвестна — "
                      "показаний решателя/построителя о ней нет "
                      f"({source}); отдых по разрывам тем же контрактом, "
                      "что решатель, не судится — литерал не подставляется"
                      + reader_note)
        return res
    split = int(split)
    if split <= 0:
        res.verdict = "not_checkable"
        res.detail = ("граница стыковки/отдыха у прогона не объявлена и "
                      "из пола отдыха не выведена (T=0: в пакете нет "
                      "ручки max_sit_duration, пол минимального отдыха "
                      f"показаниями не назван; {source}) — решатель отдых "
                      "по разрывам не судит, разрыв короче отдыха остаётся "
                      "внутри смены и судится её размахом; проверка "
                      "отдыха по разрывам здесь не выносится" + reader_note)
        return res
    res.notes.append(
        f"граница «стыковка / отдых» прогона T={split} мин ({source})")
    # нарезка на смены — тем же критерием прогона, что у всех проверок с
    # единицей «смена» (duty_criterion / duties_of_run, GPT-059)
    crit = duty_criterion(tl)
    by_place = crit["by_place"] or {}
    by_floor = crit["mode"] == "by_place"
    if by_floor:
        res.notes.append(
            "критерий границы смены: пол минимального отдыха по месту "
            f"(дома {by_place.get('home')} / вне базы "
            f"{by_place.get('outstation')}) — модельное упрощение; "
            f"достаточность отдыха — пол клаузы по месту "
            f"({'—' if home_min is None else home_min} / "
            f"{'—' if out_min is None else out_min}), обязательное правило")
    if place_note:
        res.notes.append(place_note)
    if tl.get("rest_boundary_assumption"):
        res.notes.append(str(tl["rest_boundary_assumption"]))
    if declared is not None and int(declared) != split:
        res.notes.append(
            f"значение читателя rest_split_min={declared} не применяется")
    emps = tl["employees"]
    for emp, rows in sorted(tl["by_employee"].items()):
        home = (emps.get(emp) or {}).get("homeAirport")
        duties = duties_of_run(tl, rows, home, crit)
        for a, b in zip(duties, duties[1:]):
            rest_at = a[-1]["arr"]
            need = home_min if rest_at == home else out_min
            if need is None:
                # интервал другого места — не предмет этой проверки
                continue
            res.objects_seen += 1
            gap = (b[0]["t0"]
                   - max(r["t1"] for r in a)).total_seconds() / 60
            # gap ≥ T по построению нарезки; нарушение — T ≤ gap < R
            if gap < need:
                res.violations.append(
                    f"{emp}: отдых {_fmt_min(gap)} в "
                    f"{rest_at} < пола {_fmt_min(need)} "
                    f"(разрыв ≥ T={split} мин — не стыковка)")
    return _seal(res, "интервалов отдыха проверено")


def _run_turnaround_min(spec: dict, tl: dict) -> CheckResult:
    need = int(spec["params"]["min_min"])
    res = _mk(spec, "ok")
    crit = _duty_gate(spec, tl, res)
    if crit is None:
        return res
    for emp, duty in _iter_emp_duties(tl, crit):
        for a, b in zip(duty, duty[1:]):
            res.objects_seen += 1
            gap = (b["t0"] - a["t1"]).total_seconds() / 60
            if gap < need:
                res.violations.append(
                    f"{emp}: стыковка {_fmt_min(gap)} в {a['arr']} "
                    f"({a['t1']:%H:%M}->{b['t0']:%H:%M}) < "
                    f"{_fmt_min(need)}")
    return _seal(res, "стыковок проверено")


def _run_landings_per_duty_max(spec: dict, tl: dict) -> CheckResult:
    limit = int(spec["params"]["limit"])
    res = _mk(spec, "ok")
    crit = _duty_gate(spec, tl, res)
    if crit is None:
        return res
    for emp, duty in _iter_emp_duties(tl, crit):
        res.objects_seen += 1
        landings = sum(1 for r in duty if not r["deadhead"])
        if landings > limit:
            res.violations.append(
                f"{emp}: посадок за смену {landings} > {limit} "
                f"({duty[0]['t0']:%Y-%m-%d})")
    return _seal(res, "смен проверено")


def _run_crew_size_exact(spec: dict, tl: dict) -> CheckResult:
    n = int(spec["params"]["n"])
    res = _mk(spec, "ok")
    per_flight: dict[str, set] = {}
    for emp, rows in tl["by_employee"].items():
        for r in rows:
            if not r["deadhead"]:
                per_flight.setdefault(r["flight"], set()).add(emp)
    for fid in sorted(tl["flights"]):
        res.objects_seen += 1
        got = len(per_flight.get(fid, ()))
        if got != n:
            res.violations.append(
                f"рейс {fid}: различных людей {got}, требуется {n}")
    return _seal(res, "рейсов проверено")


def _run_skill_required(spec: dict, tl: dict) -> CheckResult:
    res = _mk(spec, "ok")
    emps = tl["employees"]
    for emp, rows in sorted(tl["by_employee"].items()):
        skills = set((emps.get(emp) or {}).get("skills") or ())
        for r in rows:
            if r["deadhead"]:
                continue
            res.objects_seen += 1
            if r["skill"] and r["skill"] not in skills:
                res.violations.append(
                    f"{emp}: место {r['skill']} на {r['flight']} — "
                    f"роли нет у сотрудника (его роли: "
                    f"{sorted(skills)})")
    return _seal(res, "мест проверено")


def _run_unavailability(spec: dict, tl: dict) -> CheckResult:
    res = _mk(spec, "ok")
    emps = tl["employees"]
    for emp, rows in sorted(tl["by_employee"].items()):
        days = set((emps.get(emp) or {}).get("unavailableDays") or ())
        for r in rows:
            res.objects_seen += 1
            for d in {r["t0"].date().isoformat(),
                      r["t1"].date().isoformat()}:
                if d in days:
                    res.violations.append(
                        f"{emp}: назначение {r['flight']} в "
                        f"недоступный день {d}")
    return _seal(res, "назначений проверено")


def _run_type_rating(spec: dict, tl: dict) -> CheckResult:
    res = _mk(spec, "ok")
    emps = tl["employees"]
    for emp, rows in sorted(tl["by_employee"].items()):
        ratings = set((emps.get(emp) or {}).get("typeRatings") or ())
        for r in rows:
            if r["deadhead"] or not r["aircraftType"]:
                continue
            res.objects_seen += 1
            if ratings and r["aircraftType"] not in ratings:
                res.violations.append(
                    f"{emp}: тип {r['aircraftType']} на "
                    f"{r['flight']} вне допусков")
    return _seal(res, "назначений проверено")


def _run_pairing_span_max_days(spec: dict, tl: dict) -> CheckResult:
    limit = int(spec["params"]["limit_days"])
    res = _mk(spec, "ok")
    for chain, rows in sorted(tl["by_chain"].items()):
        res.objects_seen += 1
        days = (max(r["t1"] for r in rows).date()
                - rows[0]["t0"].date()).days + 1
        if days > limit:
            res.violations.append(
                f"цепочка {chain}: размах {days} дн > {limit}")
    return _seal(res, "цепочек проверено")


def _run_reserve_daily_min(spec: dict, tl: dict) -> CheckResult:
    norms: dict = dict(spec["params"]["per_base"])
    res = _mk(spec, "ok")
    emps = tl["employees"]
    all_days = sorted({r["t0"].date()
                      for rows in tl["by_employee"].values()
                      for r in rows})
    for day in all_days:
        d = day.isoformat()
        busy = {emp for emp, rows in tl["by_employee"].items()
                if any(r["t0"].date() <= day <= r["t1"].date()
                       for r in rows)}
        for base, need in sorted(norms.items()):
            res.objects_seen += 1
            free = [e for e, rec in emps.items()
                    if rec.get("homeAirport") == base
                    and e not in busy
                    and d not in set(rec.get("unavailableDays")
                                     or ())]
            if len(free) < int(need):
                res.violations.append(
                    f"{d} {base}: свободных {len(free)} < нормы "
                    f"{need}")
    return _seal(res, "пар день-база проверено")


def _run_soft_event_count(spec: dict, tl: dict) -> CheckResult:
    event = spec["params"]["event"]
    if event not in SOFT_EVENTS:
        raise SourceCheckError(
            f"вид события {event!r} вне словаря {SOFT_EVENTS}")
    res = _mk(spec, "soft_info")
    emps = tl["employees"]
    count = 0
    if event in ("layover_nights", "layover_nights_in_window"):
        days = set(str(d).lower() for d in
                   spec["params"].get("days") or ())
        for emp, rows in sorted(tl["by_employee"].items()):
            home = (emps.get(emp) or {}).get("homeAirport")
            for a, b in zip(rows, rows[1:]):
                if a["arr"] == home:
                    continue
                nights = (b["t0"].date() - a["t1"].date()).days
                for i in range(max(0, nights)):
                    nd = a["t1"].date() + timedelta(days=i)
                    if event == "layover_nights" or \
                            nd.strftime("%A").lower() in days:
                        count += 1
    elif event == "duty_over_threshold":
        thr = int(spec["params"]["threshold_min"])
        # единица факта — смена: нарезка критерием прогона, без границы
        # факт честно не считается (not_checkable с причиной)
        crit = _duty_gate(spec, tl, res)
        if crit is None:
            res.detail = ("мягкое правило: факт события duty_over_threshold "
                          "не считается — " + res.detail)
            return res
        for _emp, duty in _iter_emp_duties(tl, crit):
            if _duty_span_min(duty) > thr:
                count += 1
    res.objects_seen = count
    res.detail = (f"мягкое правило: жёсткая проверка не применима "
                  f"(штраф учтён счётом); фактов события {event}: "
                  f"{count}")
    if res.notes:
        res.detail += "; " + "; ".join(res.notes)
    return res


_EXECUTORS = {
    "duty_span_max": _run_duty_span_max,
    "rest_min": _run_rest_min,
    "turnaround_min": _run_turnaround_min,
    "landings_per_duty_max": _run_landings_per_duty_max,
    "crew_size_exact": _run_crew_size_exact,
    "skill_required": _run_skill_required,
    "unavailability": _run_unavailability,
    "type_rating": _run_type_rating,
    "pairing_span_max_days": _run_pairing_span_max_days,
    "reserve_daily_min": _run_reserve_daily_min,
    "soft_event_count": _run_soft_event_count,
}


def _mk(spec: dict, verdict: str) -> CheckResult:
    return CheckResult(check_id=str(spec.get("id") or spec["kind"]),
                       kind=spec["kind"],
                       anchor=str(spec.get("anchor") or ""),
                       verdict=verdict, detail="")


def _seal(res: CheckResult, unit: str) -> CheckResult:
    if res.violations:
        res.verdict = "violated"
        res.detail = (f"{unit}: {res.objects_seen}; нарушений: "
                      f"{len(res.violations)}")
    else:
        res.detail = f"{unit}: {res.objects_seen}; нарушений нет"
    if res.notes:
        res.detail += "; " + "; ".join(res.notes)
    return res


def base_limits_with_extension(specs: list) -> dict:
    """Базовые пределы смены, у которых есть продление: {id базовой
    duty_span_max: (потолок, id проверки потолка)}.

    Правило выводится из самих спецификаций, без слов читателя: жёсткая
    проверка A (duty_span_max, limit La) — базовый предел с продлением,
    если рядом есть жёсткая проверка B того же вида с потолком Lb > La И
    мягкая проверка C (soft_event_count, событие duty_over_threshold) с
    порогом threshold_min == La. Тогда превышение La законно (до Lb, со
    штрафом), и A судится не нарушением, а счётом превышений базы.
    Живой случай 09.09.2026 (пакет Н0-B, читатель cli): VN-010 «базовый
    предел 12:00; превышение — только продлением по VN-011» читатель
    закодировал жёсткой duty_span_max(720) рядом с потолком 780 и
    мягким счётом duty_over_threshold(720) — три смены 12:40 законны по
    ВМП, а протокол считал их нарушением. Без B или без C правило не
    срабатывает (fail-closed: одиночный предел остаётся жёстким)."""
    hard: list = []
    thresholds: set = set()
    for s in specs:
        if not isinstance(s, dict):
            continue
        p = s.get("params") or {}
        try:
            if s.get("kind") == "duty_span_max":
                hard.append((int(p["limit_min"]), str(s.get("id") or "")))
            elif s.get("kind") == "soft_event_count" \
                    and p.get("event") == "duty_over_threshold":
                thresholds.add(int(p["threshold_min"]))
        except (KeyError, TypeError, ValueError):
            continue
    out: dict = {}
    for la, aid in hard:
        if la not in thresholds:
            continue
        caps = [(lb, bid) for lb, bid in hard if lb > la]
        if caps:
            lb, bid = min(caps)
            out[aid] = (lb, bid)
    return out


def run_checks(specs: list[dict], solution: dict, *,
               rest_boundary: dict | None = None) -> list[CheckResult]:
    """Прогнать спецификации проверок по решению. Кривая
    спецификация — отказ словами, не молчаливый пропуск; отказ
    держится НА ПРОВЕРКЕ (вердикт not_checkable с причиной), а не
    роняет весь комплект: остальные проверки обязаны досудиться
    (урок прогона run-0003 2026-08-31 — читатель дважды подряд дал
    кривые параметры одной и той же мягкой проверки).

    rest_boundary — показание прогона о границе «стыковка / отдых»
    (см. build_timelines); без него исполнитель rest_min честно
    отвечает not_checkable, литерал не подставляется."""
    tl = build_timelines(solution, rest_boundary)
    out: list[CheckResult] = []
    extended = base_limits_with_extension(specs)
    for spec in specs:
        if isinstance(spec, dict) and str(spec.get("id") or "") in extended \
                and spec.get("kind") == "duty_span_max":
            # Базовый предел с продлением: превышения базы законны до
            # потолка — считаются счётом, потолок судит своя проверка.
            cap, cap_id = extended[str(spec.get("id") or "")]
            derived = dict(spec, kind="soft_event_count",
                           params={**(spec.get("params") or {}),
                                   "event": "duty_over_threshold",
                                   "threshold_min": spec["params"]["limit_min"]})
            try:
                res = _run_soft_event_count(derived, tl)
            except (SourceCheckError, KeyError, TypeError, ValueError) as e:
                res = _mk(spec, "not_checkable")
                res.detail = (f"кривая спецификация — проверка не "
                              f"судится (fail-closed): {e!r}")
                out.append(res)
                continue
            res.kind = "duty_span_max"
            res.detail = (f"базовый предел {_fmt_min(int(spec['params']['limit_min']))} "
                          f"с продлением до {_fmt_min(int(cap))} (потолок судит "
                          f"проверка {cap_id}): превышения базы законны и "
                          f"учтены счётом, не нарушением; " + res.detail)
            out.append(res)
            continue
        if not isinstance(spec, dict) or spec.get("kind") \
                not in CHECK_KINDS:
            broken = {"id": "spec-вне-словаря", "kind": "не задан",
                      "anchor": ""}
            if isinstance(spec, dict):
                broken["id"] = str(spec.get("id") or "spec-без-id")
                broken["anchor"] = str(spec.get("anchor") or "")
            res = CheckResult(check_id=broken["id"],
                              kind=broken["kind"],
                              anchor=broken["anchor"],
                              verdict="not_checkable", detail="")
            res.detail = (f"спецификация вне словаря видов — не "
                          f"судится (fail-closed): {spec!r:.120}")
            out.append(res)
            continue
        if spec["kind"] == "not_machine_checkable":
            res = _mk(spec, "not_checkable")
            res.detail = str(spec.get("reason")
                             or "причина не названа")
            out.append(res)
            continue
        fn = _EXECUTORS[spec["kind"]]
        try:
            out.append(fn(spec, tl))
        except (SourceCheckError, KeyError, TypeError, ValueError) \
                as e:
            res = _mk(spec, "not_checkable")
            res.detail = (f"кривая спецификация — проверка не "
                          f"судится (fail-closed): {e!r}")
            out.append(res)
    return out


def render_protocol(results: list[CheckResult],
                    header: str = "") -> str:
    """Протокол соответствия — человеку: по каждому правилу вердикт
    с числами; нарушения поимённо."""
    lines = ["# Протокол проверки решения от первоисточника", ""]
    if header:
        lines += [header, ""]
    marks = {"ok": "ВЫПОЛНЕНО", "violated": "НАРУШЕНО",
             "soft_info": "МЯГКОЕ (факты)",
             "not_checkable": "МАШИННО НЕ ПРОВЕРЯЕТСЯ"}
    counts: dict[str, int] = {}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
        lines.append(f"## [{marks[r.verdict]}] {r.check_id} "
                     f"({r.kind}; клауза: {r.anchor or '—'})")
        lines.append(r.detail)
        for v in r.violations:
            lines.append(f"- {v}")
        lines.append("")
    lines.insert(2, "Итог: " + "; ".join(
        f"{marks[k]}: {v}" for k, v in sorted(counts.items())) + ".")
    lines.insert(3, "")
    return "\n".join(lines)
