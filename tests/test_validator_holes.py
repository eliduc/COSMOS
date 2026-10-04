"""Audit 2026-07-12: the two validator holes must stay closed —
(1) duplicate assignment must fail fully_covered;
(2) inter-pairing transitions with rest < 10 h must be violations."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _coverage_validator import check  # noqa: E402


def test_duplicate_assignment_fails_coverage():
    payload = {"flights": [{"flightNumber": "L0"},
                           {"flightNumber": "L1"}]}
    solution = {"flightAssignments": [
        {"flight": "L0", "employee": "E1"},
        {"flight": "L0", "employee": "E2"},
        {"flight": "L1", "employee": "E1"}]}
    r = check(payload, solution)
    assert r["duplicates"] == 1
    assert not r["fully_covered"]


def test_inter_pairing_short_rest_detected():
    # Two closed pairings of one employee separated by a 2h gap: legal as
    # sits inside a duty, but ILLEGAL as an inter-pairing transition.
    # Calls the PRODUCTION validator unit (audit 2026-07-12, round 5).
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence
    v = defaultdict(int)
    seq = [
        # (dep_min, arr_min, dep, arr, is_tdh, pid)
        (0, 60, "BASE1", "AIR1", False, "P1"),
        (120, 180, "AIR1", "BASE1", False, "P1"),
        (300, 360, "BASE1", "AIR1", False, "P2"),   # gap 2h, new pairing
        (420, 480, "AIR1", "BASE1", False, "P2"),
    ]
    check_employee_sequence(seq, v)
    assert v["inter-pairing rest<10h"] == 1


def test_clean_sequence_has_no_violations():
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence
    v = defaultdict(int)
    seq = [
        (0, 60, "BASE1", "AIR1", False, "P1"),
        (120, 180, "AIR1", "BASE1", False, "P1"),
        (900, 960, "BASE1", "AIR1", False, "P2"),   # 12h rest between
        (1020, 1080, "AIR1", "BASE1", False, "P2"),
    ]
    tdh = check_employee_sequence(seq, v)
    assert sum(v.values()) == 0 and tdh == 0


# --- месячный налёт: календарный месяц и личный предел (2026-07-27) -------

def _month_seq(minutes_per_month: dict):
    """Последовательность участков, дающая заданный налёт по календарным
    месяцам. Отсчёт минут — от EPOCH сертификата (2000-01-01)."""
    from datetime import datetime
    from _gerad_trajectory_check import EPOCH
    seq = []
    for ym, total in minutes_per_month.items():
        base = int((datetime.strptime(ym + "-02", "%Y-%m-%d")
                    - EPOCH).total_seconds() // 60)
        left, k = total, 0
        while left > 0:
            chunk = min(left, 300)
            start = base + k * 1440          # по одному участку в сутки
            seq.append((start, start + chunk, "BASE1", "BASE1", False, "P1"))
            left -= chunk
            k += 1
    return sorted(seq)


def test_block_counted_per_calendar_month_not_whole_horizon():
    """Горизонт прогона захватывает несколько календарных месяцев, а
    предел — месячный. Суммирование по размаху объявляло нарушителем
    того, кто в каждом месяце в норме."""
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence, MAX_BLOCK
    half = MAX_BLOCK * 3 // 4                 # в каждом месяце — 75 % нормы
    v = defaultdict(int)
    check_employee_sequence(_month_seq({"2026-07": half, "2026-08": half}), v)
    assert v["block>limit"] == 0, dict(v)


def test_block_over_the_cap_within_one_month_is_caught():
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence, MAX_BLOCK
    v = defaultdict(int)
    check_employee_sequence(_month_seq({"2026-08": MAX_BLOCK + 600}), v)
    assert v["block>limit"] == 1


def test_personal_limit_wins_over_the_global_one():
    """У набора заказчика большинство людей несут личный предел налёта
    выше общего. Сертификат мерил всех общим пределом и объявлял
    нарушителями людей, которых решатель законно считал в норме —
    ошибался сертификат."""
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence, MAX_BLOCK
    seq = _month_seq({"2026-08": MAX_BLOCK + 600})
    v = defaultdict(int)
    check_employee_sequence(seq, v, block_limit=MAX_BLOCK + 900)
    assert v["block>limit"] == 0, "личный предел выше — нарушения нет"
    v2 = defaultdict(int)
    check_employee_sequence(seq, v2, block_limit=MAX_BLOCK - 600)
    assert v2["block>limit"] == 1, "личный предел ниже — нарушение есть"


def test_zero_personal_limit_falls_back_to_the_global_one():
    """Ноль в данных означает «личного предела нет», а не «предела нет»."""
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence, MAX_BLOCK
    v = defaultdict(int)
    check_employee_sequence(_month_seq({"2026-08": MAX_BLOCK + 600}), v,
                            block_limit=0)
    assert v["block>limit"] == 1


# --- размах смены: перегон не работа (2026-07-27) --------------------------

def test_duty_span_excludes_positioning_flights():
    """Заказчик считает размах смены по РАБОЧИМ участкам. Проверено на его
    собственных данных: среди его цепочек смен длиннее 14 часов есть и
    цепочки без перегонов, и цепочки с ними, а входной аудит, работающий
    по его прочтению, называет только первые. Прежняя версия включала
    перегоны и в одиночку расходилась и с заказчиком, и с нашим
    построителем: на боевых ростерах она давала сотни превышений, из
    которых после исключения перегонов не оставалось ни одного."""
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence
    v = defaultdict(int)
    # рабочая часть 10 часов, плюс перегон домой, растягивающий смену до 16
    seq = [
        (0, 120, "BASE1", "AIR1", False, "P1"),
        (480, 600, "AIR1", "AIR2", False, "P1"),
        (840, 960, "AIR2", "BASE1", True, "P1"),     # перегон пассажиром
    ]
    check_employee_sequence(seq, v)
    assert v["duty span>limit"] == 0, "рабочая часть укладывается в предел"
    assert v["info:duty span>limit with positioning"] == 1, \
        "строгая трактовка обязана остаться видимой"


def test_long_working_duty_is_still_a_violation():
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence
    v = defaultdict(int)
    seq = [
        (0, 120, "BASE1", "AIR1", False, "P1"),
        # разрыв 8 часов — меньше отдыха, значит та же смена; рабочий
        # размах 17 часов
        (600, 1020, "AIR1", "BASE1", False, "P1"),
    ]
    check_employee_sequence(seq, v)
    assert v["duty span>limit"] == 1


def test_info_counters_do_not_enter_the_verdict():
    """Спорная трактовка публикуется, но гейт судит по прочтению
    заказчика: иначе строжайшее из возможных прочтений блокировало бы
    любой ростер."""
    total = sum(val for key, val in
                {"discontinuity": 2,
                 "info:duty span>limit with positioning": 500}.items()
                if not key.startswith("info:"))
    assert total == 2


# --- границы смен объявляются, а не выводятся (2026-07-27) ----------------

def test_declared_duty_boundaries_win_over_the_rest_threshold():
    """Границы смен нельзя выводить из порога отдыха. Проверено на данных
    ЗАКАЗЧИКА: у него заметная доля переходов между сменами короче десяти
    часов, а часть и вовсе отрицательные — его наряды несут брифинг с
    дебрифингом и перекрываются по времени. Восстановление по плоскому
    порогу склеивало смены и давало в разы больше превышений размаха,
    чем есть на самом деле."""
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence
    # два участка с разрывом ВСЕГО ЧАС, но объявленные РАЗНЫМИ сменами;
    # суммарный размах 17 часов
    seq = [
        (0, 120, "BASE1", "AIR1", False, "P1", ("P1", 0)),
        (180, 1020, "AIR1", "BASE1", False, "P1", ("P1", 1)),
    ]
    v = defaultdict(int)
    check_employee_sequence(seq, v)
    assert v["duty span>limit"] == 0, "две объявленные смены, каждая короткая"


def test_without_declared_boundaries_the_fallback_still_works():
    """Артефакты прежнего выпуска границ не несут — запасной путь обязан
    остаться и судить по-старому."""
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence
    seq = [
        (0, 120, "BASE1", "AIR1", False, "P1"),
        (180, 1020, "AIR1", "BASE1", False, "P1"),
    ]
    v = defaultdict(int)
    check_employee_sequence(seq, v)
    assert v["duty span>limit"] == 1, "без объявленных границ склейка законна"


def test_declared_long_duty_is_still_a_violation():
    """Объявленная смена длиннее предела — нарушение, и его не спрятать
    объявлением."""
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence
    seq = [
        (0, 120, "BASE1", "AIR1", False, "P1", ("P1", 0)),
        (180, 1020, "AIR1", "BASE1", False, "P1", ("P1", 0)),
    ]
    v = defaultdict(int)
    check_employee_sequence(seq, v)
    assert v["duty span>limit"] == 1


# --- равнозначные станции в сертификате маршрута ----------------------------

def test_equivalent_stations_are_not_a_broken_trajectory():
    """Экипаж законно продолжает работу из ДРУГОГО аэропорта той же
    агломерации, если разрыв не меньше заданного правилами источника
    (`stationEquivalenceRules`).

    Прежде сертификат об этом правиле не знал и объявлял такой переезд
    телепортацией. На наборе заказчика это давало десятки ложных
    нарушений — ВСЕ по объявленным парам, при разрывах заметно больше
    требуемого. Сам источник делает те же переезды."""
    from collections import Counter

    import _gerad_trajectory_check as tc

    # (вылет, прилёт, станция вылета, станция прилёта, перегон, цепочка)
    seq = [(0, 120, "BSE", "LHR", False, "P1"),
           (120 + 2880, 120 + 3000, "LGW", "BSE", False, "P2")]
    equiv = {("LHR", "LGW"): 2160, ("LGW", "LHR"): 2160}

    # без правила — разрыв маршрута
    v = Counter()
    tc.check_employee_sequence(seq, v)
    assert v["discontinuity"] == 1

    # с правилом и достаточным разрывом — не нарушение, но НАЗВАНО
    v2 = Counter()
    tc.check_employee_sequence(seq, v2, equivalent_stations=equiv)
    assert v2["discontinuity"] == 0
    assert v2["info:equivalent station move"] == 1

    # разрыв меньше требуемого — нарушение своего вида
    short = [(0, 120, "BSE", "LHR", False, "P1"),
             (120 + 600, 120 + 720, "LGW", "BSE", False, "P2")]
    v3 = Counter()
    tc.check_employee_sequence(short, v3, equivalent_stations=equiv)
    assert v3["equivalent station too soon"] == 1
    assert v3["discontinuity"] == 0

    # станция вне списка равнозначных — по-прежнему разрыв
    other = [(0, 120, "BSE", "LHR", False, "P1"),
             (120 + 2880, 120 + 3000, "CDG", "BSE", False, "P2")]
    v4 = Counter()
    tc.check_employee_sequence(other, v4, equivalent_stations=equiv)
    assert v4["discontinuity"] == 1


def test_equivalent_station_move_is_not_counted_as_a_violation():
    """Послабление обязано быть ВИДНО в отчёте и НЕ идти в счёт: приставка
    info: означает вторую трактовку спорного правила, а не молчание."""
    import _gerad_trajectory_check as tc
    v = {"discontinuity": 2, "info:equivalent station move": 5}
    total = sum(val for key, val in v.items()
                if not str(key).startswith("info:"))
    assert total == 2
    assert hasattr(tc, "load_equivalent_stations")


def test_equivalent_stations_are_not_assumed_without_the_source_file():
    """Правило заказчика не подставляется по умолчанию: умолчание выдало
    бы себя за его данные."""
    import _gerad_trajectory_check as tc
    assert tc.load_equivalent_stations(None) == {}


# ---------- запасной путь сертификата ЖИВ для каталогов без duty_fns ----
# Противниковая проверка 2026-08-06: безусловный синглтон-ключ для
# участка без объявленной смены делал «объявленной» ЛЮБУЮ модель — для
# каталогов без duty_fns (линия колонной генерации) проверки
# «landings>5» и «duty span>limit» структурно не срабатывали никогда.

def _six_leg_case(tmp_path, duty_fns=None):
    import json as _json
    legs = []
    fns = []
    t = 0
    for i in range(6):
        dep, arr = ("BASE", "DEST") if i % 2 == 0 else ("DEST", "BASE")
        fns.append("L%d" % (i + 1))
        legs.append({
            "flightNumber": fns[-1], "departureAirport": dep,
            "departureUTCDateTime":
                "2026-03-01T%02d:%02d:00" % (8 + t // 60, t % 60),
            "arrivalAirport": arr,
            "arrivalUTCDateTime":
                "2026-03-01T%02d:%02d:00" % (8 + (t + 60) // 60,
                                             (t + 60) % 60),
            "aircraftType": "A320"})
        t += 90                       # 60 мин полёт + 30 мин пауза (< REST)
    payload = {
        "airports": [{"code": "BASE"}, {"code": "DEST"}],
        "employees": [{"id": "E1", "homeAirport": "BASE",
                       "skills": ["Captain"]}],
        "flights": legs,
        "flightAssignments": [
            {"id": str(i), "flight": fn, "indexInFlight": 0,
             "requiredSkill": "Captain"} for i, fn in enumerate(fns)]}
    rec = {"fns": fns, "active_fns": fns, "cost": 1}
    if duty_fns is not None:
        rec["duty_fns"] = duty_fns
    catalog = {"selected": [rec]}
    traj = {"pairings": [{
        "pid": "C_000000", "employee": "E1",
        "crew": [{"indexInFlight": 0, "requiredSkill": "Captain",
                  "employee": "E1"}],
        "fns": fns, "active_fns": fns}]}
    expanded = {"airports": payload["airports"],
                "employees": payload["employees"], "flights": legs,
                "flightAssignments": [
                    dict(a, employee="E1")
                    for a in payload["flightAssignments"]]}
    tp, sp, cp, pp = (tmp_path / n for n in
                      ("traj.json", "sol.json", "cat.json", "pay.json"))
    tp.write_text(_json.dumps(traj))
    sp.write_text(_json.dumps(expanded))
    cp.write_text(_json.dumps(catalog))
    pp.write_text(_json.dumps(payload))
    return tp, sp, cp, pp


def test_catalog_without_duty_fns_falls_back_to_rest_split(tmp_path):
    """Каталог БЕЗ объявленных смен: шесть рабочих участков с паузами
    меньше отдыха обязаны склеиться запасным путём в одну смену и
    попасть в «landings>5» — прежде такой каталог не проверялся вовсе."""
    from _gerad_trajectory_check import check_inst
    tp, sp, cp, pp = _six_leg_case(tmp_path)
    r = check_inst("nofns", traj_path=tp, sol_path=sp,
                   catalog_path=cp, payload_path=pp)
    assert r["violations"].get("landings>5/duty", 0) == 1, r["violations"]


def test_declared_duty_fns_win_over_rest_split(tmp_path):
    """Контроль симметрии: те же участки с ОБЪЯВЛЕННЫМИ границами двух
    смен судятся по объявленной модели — нарушения нет."""
    from _gerad_trajectory_check import check_inst
    tp, sp, cp, pp = _six_leg_case(
        tmp_path, duty_fns=[["L1", "L2", "L3"], ["L4", "L5", "L6"]])
    r = check_inst("withfns", traj_path=tp, sol_path=sp,
                   catalog_path=cp, payload_path=pp)
    assert r["violations"].get("landings>5/duty", 0) == 0, r["violations"]


def test_dh_leg_keeps_declared_model_alive():
    """Перегон без объявленной смены получает собственную группу и не
    рушит объявленную модель остальных участков (прежний контракт):
    шесть рабочих участков в двух ОБЪЯВЛЕННЫХ сменах плюс перегон с
    собственным ключом судятся по объявленной модели — нарушения нет."""
    from collections import defaultdict
    from _gerad_trajectory_check import check_employee_sequence
    v = defaultdict(int)
    seq = []
    t = 0
    for i in range(6):
        key = ("P1", 0) if i < 3 else ("P1", 1)
        seq.append((t, t + 60, "A", "B", False, "P1", key))
        t += 90
    seq.append((t, t + 60, "B", "A", True, "P1", ("P1", "dh", "L7")))
    check_employee_sequence(seq, v)
    assert v.get("landings>5/duty", 0) == 0, dict(v)
