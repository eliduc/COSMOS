# -*- coding: utf-8 -*-
"""Книга данных набора «Полярис-H0-КИ-окна» = данные H0 на семь суток,
плюс ночные рейсы каждые сутки и (в варианте hist) лист «История»
(спецификация 1.17) под правила о ночных сменах подряд и отдыхе в
скользящем окне семи суток (семьи базы шаблонов night_series, rolling_rest
— путь А развилки набора, 05.09.2026; правило о выходных в месяц VN-093
убрано вечером 05.09 по требованию простоты).

ОСНОВНОЕ ТРЕБОВАНИЕ ВЛАДЕЛЬЦА К НАБОРУ Н0 (05.09.2026, вечер, дословно):
«Требованием к этому набору только одно: он должен быть очень простой. Он
должен соответствовать здравому смыслу и передавать данные очень маленькой
авиакомпании. При этом он не должен быть изощренным. … он нужен для
демонстрации функциональности CDPM и Космоса, для того чтобы можно было
провести его по пайплайну обоих продуктов примерно за полчаса … Мы сами
вольны менять набор Н0 так, как мы хотим. Если что-то затрудняет расчет,
нужно поменять так, чтобы этот расчет можно было делать быстро, чтобы
проверки проводились быстро.» Прежний стоп «набор не облегчать» этим
словом отменён (память h0-simplicity-requirement).

Что взято из H0 без изменений (импорт build_data_h0): экипажи (24
пилота по базам PLY/SVG/GRT), допуски (действуют до 2027-12-31 — горизонт
покрыт), аэропорты (с ночи 05→06.09 — плюс широта/долгота вымышленной
полярной сети, AIRPORT_COORDS ниже), единая шкала времени (VN-004: времена книги = местное
время базы PLY), тип судна, дневные рейсы PL101…PL305.

Что изменено по требованию простоты (05.09.2026, вечер):
- программа рейсов СБАЛАНСИРОВАНА: добавлен ежедневный PL306 SVG→PLY
  18:20→19:50 (возврат экипажа PL305). Прежняя программа давала на один
  рейс PLY→SVG в сутки больше обратных: за 27 суток 54 пилото-перехода при
  24 пилотах — без позиционирования пассажирами набор был неразрешим по
  построению (это и был корень «недопустимо» H0-КИ-окна). Теперь потоки
  PLY↔SVG 4/4 и PLY↔GRT 2/2 в сутки, каждая ротация замыкается на базу
  экипажа, позиционирование не требуется;
- книга данных по умолчанию БЕЗ листа «История»: история сотрудников не
  задаётся; анкета CDPM спрашивает об этом (Q-3b-история-нет), ответ —
  «продолжить с нулевой историей», COSMOS называет допущение и считает
  (пример владельца). Вариант с историей (сценарии A/B/C) сохранён как
  книга ID-H0KW-HIST-DATA.xlsx для отдельной демонстрации позже.

Что добавлено:
- горизонт 04.09.2026–10.09.2026, семь суток (planSince/planUntil в
  «Свойствах» в той же записи, что у H0) — одно полное окно правила VN-092
  «отдых в скользящих 7 сутках». История горизонта: 7 суток → 27 суток до
  конца месяца (05.09 днём, ради правила «выходных в месяц» VN-093, которое
  на незакрытом месяце не судится по построению — «ПУСТО») → снова 7 суток
  (05.09 вечером, требование простоты: на 27 сутках ростеринг 24 пилотов ×
  322 рейса за 240 с не сходился; VN-093 из комплекта убрано);
- ночные рейсы каждые сутки между базами PLY и SVG: PL401 23:30→01:10
  (прилёт на следующие сутки — в книге у него ТА ЖЕ «Дата», приёмник
  webapp/task_package переносит прилёт на +1 сутки, когда прилёт раньше
  вылета) и PL402 02:10→03:50. Смена, начатая в 23:30, — ночная при окне
  ночи [22:00, 06:00) местного времени базы;
- лист «История» (срез 04.09.2026 00:00) с тремя сценариями:
  A — H03: серия ночных смен 2, последняя смена ночная, конец 03.09 03:50
      на базе (ночная смена 04.09 сделала бы серию 3 > 2);
  B — H07: конец последней смены 03.09 22:00 на базе (за 2 часа до
      горизонта), 6 дней с выходного, серия рабочих дней 6 (полу отдыха
      дома 720 минут и окну семи суток нужен отдых в начале горизонта);
  C — H11: 3 дня отработано в сентябре, 3 дня с выходного (перенос
      отработанных суток месяца в правило о выходных);
  остальные — лёгкая история: последняя смена 02.09, 2 дня отработано,
  ночных нет, 03.09 — выходной (серия 0).

Соглашение о полях серий: «Серия рабочих дней» — рабочие сутки подряд,
кончая суткам перед срезом (0, если сутки перед срезом свободны);
«Дней с выходного» — суток с последнего выходного до среза (равно серии,
когда все эти сутки рабочие); «Серия ночных смен» не длиннее серии
рабочих дней; «Дней отработано» — сутки месяца среза до среза (не больше
трёх при срезе 04.09).

    python open-bench/polyaris-h0kw/build_data_h0kw.py                     # проверка
    python open-bench/polyaris-h0kw/build_data_h0kw.py --write             # ID-H0KW-DATA.xlsx (без истории)
    python open-bench/polyaris-h0kw/build_data_h0kw.py --write --variant hist

Варианты:
- nohist (умолчание, data/ID-H0KW-DATA.xlsx) — книга без листа «История»
  (простой демонстрационный набор);
- hist (data/ID-H0KW-HIST-DATA.xlsx) — та же книга с листом «История»
  (сценарии A/B/C) — для отдельной демонстрации окон истории.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "open-bench" / "polyaris-h0"))
import build_data_h0 as h0  # noqa: E402

#: Вариант → имя книги. Умолчание — без истории (требование простоты).
VARIANTS = {
    "nohist": "ID-H0KW-DATA.xlsx",
    "hist": "ID-H0KW-HIST-DATA.xlsx",
}
DEFAULT_VARIANT = "nohist"
OUT_XLSX = HERE / "data" / VARIANTS[DEFAULT_VARIANT]
#: Дополнительный дневной рейс, замыкающий программу: возврат экипажа PL305
#: (сбалансированные потоки между базами — позиционирование не нужно).
EXTRA_DAY_LEGS = (
    ("PL306", "SVG", "PLY", "18:20", "19:50"),
)

#: Плановые сутки: подряд от пятницы 04.09.2026, семь суток (04–10.09) —
#: ровно одно полное окно правила «отдых в скользящих 7 сутках» (VN-092)
#: внутри горизонта. Требование простоты (05.09.2026, вечер): 27 суток до
#: конца месяца были нужны только правилу о выходных в месяц VN-093; на них
#: ростеринг 24 пилотов × 322 рейса за 240 с не сходился (холодный −391hard,
#: на цепочках −3290hard), правило убрано из комплекта, горизонт возвращён к
#: неделе. Три дня (как у базового H0) не годятся: окно VN-092 семи суток
#: не поместится, универсум формы пуст — решатель откажет «ПУСТО».
HORIZON_DAYS = 7
HORIZON_START = dt.date(2026, 9, 4)
HORIZON_END = HORIZON_START + dt.timedelta(days=HORIZON_DAYS - 1)
DAYS = tuple((HORIZON_START + dt.timedelta(days=i)).isoformat()
             for i in range((HORIZON_END - HORIZON_START).days + 1))
#: Срез истории = начало горизонта (форма дд.мм.гггг чч:мм по канону 1.15).
AS_OF = "04.09.2026 00:00"
#: Окно ночи [22:00, 06:00) местного времени базы, в минутах суток.
NIGHT_FROM, NIGHT_TO = 22 * 60, 6 * 60
#: Ночные рейсы суток: (номер, вылет, прилёт, время вылета, время прилёта).
#: PL401 прилетает после полуночи — «Дата» у него та же, что у вылета.
NIGHT_LEGS_DAILY = (
    ("PL401", "PLY", "SVG", "23:30", "01:10"),
    ("PL402", "SVG", "PLY", "02:10", "03:50"),
)
#: Канон колонок листа «История» (cdpm/history_section.py COLUMNS).
HISTORY_COLUMNS = [
    "Табельный номер", "Срез",
    "Налёт с начала месяца, мин", "Рабочее время с начала месяца, мин",
    "Дней отработано",
    "Последняя смена: конец", "Последняя смена: станция",
    "Последняя смена: работа, мин", "Последняя смена: ночная",
    "Серия рабочих дней", "Серия ночных смен", "Дней с выходного",
    "Местоположение",
]
#: Сценарии истории по табельным номерам (все — база PLY).
SCENARIO_A, SCENARIO_B, SCENARIO_C = "H03", "H07", "H11"
#: Ожидаемые значения сценариев — самопроверка сверяет с ними лист.
EXPECT = {
    SCENARIO_A: {"Серия ночных смен": 2, "Последняя смена: ночная": True,
                 "Последняя смена: конец": "2026-09-03T03:50"},
    SCENARIO_B: {"Последняя смена: конец": "2026-09-03T22:00",
                 "Дней с выходного": 6, "Серия рабочих дней": 6},
    SCENARIO_C: {"Дней отработано": 3, "Дней с выходного": 3},
}
#: Пилотов в резерве по базам суммарно (VN-060: PLY 2, SVG 1, GRT 1 экипажа
#: по 2 пилота) — для грубой оценки вместимости книги.
RESERVE_PILOTS = sum(n * h0.LIMITS["crew_size"]
                     for n in h0.LIMITS["reserve_crews"].values())


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_night_minute(minute_of_day: int) -> bool:
    """Минута суток в окне ночи [NIGHT_FROM, NIGHT_TO) с переходом через полночь."""
    if NIGHT_FROM <= NIGHT_TO:
        return NIGHT_FROM <= minute_of_day < NIGHT_TO
    return minute_of_day >= NIGHT_FROM or minute_of_day < NIGHT_TO


def flights() -> list[dict]:
    """Дневные рейсы H0 (полная программа) и ночные рейсы — на каждые сутки
    горизонта. Ночная программа замкнута внутри горизонта: у PL402 первых
    суток нет своего PL401 накануне (03.09 — до горизонта), у PL401
    последних суток нет PL402 назавтра (01.10 — за горизонтом) — такие
    сироты не выпускаются (простой набор: 26 замкнутых ночных пар, экипаж
    ночной пары всегда возвращается на базу внутри горизонта)."""
    out = []
    for day in DAYS:
        for num, dep, arr, t1, t2 in h0.LEGS_DAILY + EXTRA_DAY_LEGS + NIGHT_LEGS_DAILY:
            if (num == "PL402" and day == DAYS[0]) or (num == "PL401" and day == DAYS[-1]):
                continue
            out.append({"Номер рейса": num, "Дата": day, "Вылет": dep,
                        "Время вылета": t1, "Прилёт": arr,
                        "Время прилёта": t2, "Тип ВС": h0.AIRCRAFT})
    return out


def properties_rows() -> list[dict]:
    return [{"ключ": "planSince", "значение": DAYS[0] + "T00:00"},
            {"ключ": "planUntil", "значение": DAYS[-1] + "T23:59"}]


def _row(eid: str, base: str, **values) -> dict:
    row = {col: "" for col in HISTORY_COLUMNS}
    row["Табельный номер"] = eid
    row["Срез"] = AS_OF
    row["Последняя смена: станция"] = base
    row["Местоположение"] = base
    for k, v in values.items():
        row[k] = str(v)
    return row


def history_rows(crew: list[dict]) -> list[dict]:
    """Строки «Истории» по сценариям A/B/C и лёгкая история остальных."""
    rows = []
    for n, c in enumerate(crew, 1):
        eid, base = c["Табельный номер"], c["База"]
        if eid == SCENARIO_A:
            # Две ночные смены подряд PLY→SVG→PLY (23:30→03:50): 01/02.09 и
            # 02/03.09; заняты сутки 01, 02, 03; выходной был 31.08.
            rows.append(_row(eid, base, **{
                "Налёт с начала месяца, мин": 400,
                "Рабочее время с начала месяца, мин": 520,
                "Дней отработано": 3,
                "Последняя смена: конец": "03.09.2026 03:50",
                "Последняя смена: работа, мин": 260,
                "Последняя смена: ночная": "да",
                "Серия рабочих дней": 3, "Серия ночных смен": 2,
                "Дней с выходного": 3}))
        elif eid == SCENARIO_B:
            # Шесть рабочих суток подряд 29.08–03.09, последняя смена
            # дневная 12:00→22:00 на базе; выходной был 28.08.
            rows.append(_row(eid, base, **{
                "Налёт с начала месяца, мин": 1200,
                "Рабочее время с начала месяца, мин": 1800,
                "Дней отработано": 3,
                "Последняя смена: конец": "03.09.2026 22:00",
                "Последняя смена: работа, мин": 600,
                "Последняя смена: ночная": "нет",
                "Серия рабочих дней": 6, "Серия ночных смен": 0,
                "Дней с выходного": 6}))
        elif eid == SCENARIO_C:
            # Три рабочих суток 01–03.09 (длинные смены 06:00→17:30),
            # выходной был 31.08.
            rows.append(_row(eid, base, **{
                "Налёт с начала месяца, мин": 1230,
                "Рабочее время с начала месяца, мин": 1860,
                "Дней отработано": 3,
                "Последняя смена: конец": "03.09.2026 17:30",
                "Последняя смена: работа, мин": 690,
                "Последняя смена: ночная": "нет",
                "Серия рабочих дней": 3, "Серия ночных смен": 0,
                "Дней с выходного": 3}))
        else:
            # Лёгкая история: смены 01 и 02.09, 03.09 — выходной.
            rows.append(_row(eid, base, **{
                "Налёт с начала месяца, мин": 600 + 10 * n,
                "Рабочее время с начала месяца, мин": 900 + 15 * n,
                "Дней отработано": 2,
                "Последняя смена: конец": "02.09.2026 17:30",
                "Последняя смена: работа, мин": 450,
                "Последняя смена: ночная": "нет",
                "Серия рабочих дней": 0, "Серия ночных смен": 0,
                "Дней с выходного": 0}))
    return rows


# Координаты станций (ночь 05→06.09.2026, слово владельца: «визуализация
# карты и персонала — отдельной визуальной кнопкой после блока
# загрузки»): вымышленная полярная сеть «Полярис» на Севере — база PLY
# у Кольского залива, SVG в устье Северной Двины, GRT в низовьях
# Печоры. Расстояния согласованы с временами рейсов H0: PLY–SVG ≈ 590 км
# при 1 ч 30 в расписании, PLY–GRT ≈ 870 км при 1 ч 40. Нужны только
# «Карте мира» окна «Карта сети и персонал»: у синтетического класса
# данных справочник координат по коду НЕ применяется (PLY/SVG/GRT
# случайно совпадают с настоящими Плимутом, Ставангером и Гуджратом).
AIRPORT_COORDS = {"PLY": (68.90, 33.10), "SVG": (64.60, 40.70),
                  "GRT": (67.65, 53.10)}
AIRPORT_COLUMNS = ["Код", "timeZone", "Широта", "Долгота"]


def airports_rows() -> list[dict]:
    rows = []
    for r in h0.airports():
        if r["Код"] not in AIRPORT_COORDS:
            raise ValueError(f"нет координат станции {r['Код']!r}")
        lat, lon = AIRPORT_COORDS[r["Код"]]
        rows.append({**r, "Широта": lat, "Долгота": lon})
    return rows


def tables(variant: str = DEFAULT_VARIANT) -> dict:
    """Книга варианта: листы H0 с рейсами и свойствами суток горизонта до
    конца месяца, лист «Аэропорты» с координатами полярной сети; у
    варианта hist — ещё лист «История»."""
    if variant not in VARIANTS:
        raise ValueError(f"неизвестный вариант {variant!r}")
    t = h0.tables()
    t["Рейсы"] = {"columns": list(t["Рейсы"]["columns"]), "rows": flights()}
    t["Аэропорты"] = {"columns": list(AIRPORT_COLUMNS), "rows": airports_rows()}
    t["Свойства"] = {"columns": list(t["Свойства"]["columns"]),
                     "rows": properties_rows()}
    if variant == "hist":
        t["История"] = {"columns": HISTORY_COLUMNS,
                        "rows": history_rows(t["Экипажи"]["rows"])}
    return t


# ----------------------------------------------------- самопроверка книги

def _check_horizon(t: dict, problems: list[str], facts: dict) -> None:
    days = [dt.date.fromisoformat(d) for d in DAYS]
    facts["days"] = len(days)
    if any((b - a).days != 1 for a, b in zip(days, days[1:])):
        problems.append("сутки горизонта не подряд")
    if days[0] != HORIZON_START or len(days) != HORIZON_DAYS:
        problems.append(f"горизонт не {HORIZON_DAYS} суток от {HORIZON_START.isoformat()}")
    # Окно VN-092 (7 суток) помещается в горизонт целиком — иначе форме
    # нечего судить и решатель откажет «ПУСТО».
    if len(days) < 7:
        problems.append("горизонт короче окна семи суток правила VN-092")
    if days[0].month != days[-1].month:
        problems.append("горизонт пересекает границу месяца")
    props = {r["ключ"]: r["значение"] for r in t["Свойства"]["rows"]}
    if props.get("planSince") != HORIZON_START.isoformat() + "T00:00" \
            or props.get("planUntil") != HORIZON_END.isoformat() + "T23:59":
        problems.append(f"planSince/planUntil не {HORIZON_START}–{HORIZON_END}: {props}")
    # Допуски покрывают горизонт.
    until = days[-1]
    for r in t["Допуски"]["rows"]:
        if dt.date.fromisoformat(str(r["Действует до"])) < until:
            problems.append(f"допуск {r['Табельный номер']} кончается раньше "
                            f"{until.isoformat()}")


def _check_flights(t: dict, problems: list[str], facts: dict) -> None:
    cols = t["Рейсы"]["columns"]
    if cols != h0.tables()["Рейсы"]["columns"]:
        problems.append(f"колонки «Рейсов» отличаются от H0: {cols}")
    rows = t["Рейсы"]["rows"]
    facts["flights"] = len(rows)
    night_by_day = {d: 0 for d in DAYS}
    overnight = 0
    for r in rows:
        dep, arr = _mins(r["Время вылета"]), _mins(r["Время прилёта"])
        if arr == dep:
            problems.append(f"{r['Номер рейса']} {r['Дата']}: прилёт равен вылету")
        if arr < dep:
            overnight += 1          # приёмник перенесёт прилёт на +1 сутки
        if is_night_minute(dep):
            night_by_day[r["Дата"]] += 1
        if r["Вылет"] not in h0.BASES or r["Прилёт"] not in h0.BASES:
            problems.append(f"{r['Номер рейса']}: аэропорт вне баз H0 {h0.BASES}")
        if r["Тип ВС"] != h0.AIRCRAFT:
            problems.append(f"{r['Номер рейса']}: тип судна не {h0.AIRCRAFT}")
    facts["night_legs"] = sum(night_by_day.values())
    facts["night_legs_by_day_min"] = min(night_by_day.values())
    facts["overnight_arrivals"] = overnight
    if facts["night_legs"] == 0:
        problems.append("ночных рейсов нет")
    for d, n in night_by_day.items():
        if n < 1:
            problems.append(f"{d}: ни одного ночного рейса (вылет в окне "
                            f"[{NIGHT_FROM}, {NIGHT_TO}) минут суток)")
    if overnight == 0:
        problems.append("нет рейса через полночь — сценарий ночной смены 23:30 не задан")
    # Ночная связка PL401→PL402 как смена: стыковка и длительность в пределах H0.
    a, b = NIGHT_LEGS_DAILY
    turn = _mins(b[3]) + 1440 - _mins(a[4]) if _mins(b[3]) < _mins(a[4]) \
        else _mins(b[3]) - _mins(a[4])
    duty = (1440 + _mins(b[4])) - _mins(a[3])
    facts["night_duty"] = {"turnaround_min": turn, "duty_min": duty}
    if turn < h0.LIMITS["min_turnaround"]:
        problems.append(f"ночная стыковка {turn} мин меньше порога VN-020")
    if duty > h0.LIMITS["max_duty_duration"]:
        problems.append(f"ночная смена {duty} мин длиннее предела VN-010")
    # Полная дневная программа H0 — все её рейсы, ранний вылет до 10:00
    # (соблазн для сценария B) присутствует.
    facts["day_legs"] = [leg[0] for leg in h0.LEGS_DAILY + EXTRA_DAY_LEGS]
    if not any(_mins(leg[3]) < 10 * 60 for leg in h0.LEGS_DAILY):
        problems.append("нет дневного рейса с вылетом до 10:00 — сценарий B без соблазна")
    # Баланс потоков (требование простоты 05.09.2026): в каждую базу
    # прилетает столько же рейсов, сколько вылетает, — иначе без
    # позиционирования пассажирами набор неразрешим по построению. Дневная
    # программа судится за каждые сутки; ночная пара пересекает полночь
    # (PL401 суток d возвращается PL402 суток d+1), поэтому она судится
    # оборотами: у каждого PL401 есть PL402 назавтра и наоборот, а за весь
    # горизонт потоки всех рейсов сходятся в ноль.
    night_nums = {leg[0] for leg in NIGHT_LEGS_DAILY}
    net: dict[tuple[str, str], int] = {}
    total: dict[str, int] = {}
    for r in rows:
        total[r["Вылет"]] = total.get(r["Вылет"], 0) - 1
        total[r["Прилёт"]] = total.get(r["Прилёт"], 0) + 1
        if r["Номер рейса"] in night_nums:
            continue
        net[(r["Дата"], r["Вылет"])] = net.get((r["Дата"], r["Вылет"]), 0) - 1
        net[(r["Дата"], r["Прилёт"])] = net.get((r["Дата"], r["Прилёт"]), 0) + 1
    unbalanced = sorted((d, b, v) for (d, b), v in net.items() if v != 0)
    unbalanced += sorted(("горизонт", b, v) for b, v in total.items() if v != 0)
    facts["flow_balance"] = "сбалансирован" if not unbalanced else unbalanced[:5]
    if unbalanced:
        problems.append("потоки между базами не сбалансированы: "
                        + ", ".join(f"{d} {b} {v:+d}" for d, b, v in unbalanced[:5]))
    out_n = {(r["Дата"]) for r in rows if r["Номер рейса"] == NIGHT_LEGS_DAILY[0][0]}
    back_n = {(r["Дата"]) for r in rows if r["Номер рейса"] == NIGHT_LEGS_DAILY[1][0]}
    next_day = {d: (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat() for d in DAYS}
    orphans = [f"{NIGHT_LEGS_DAILY[0][0]} {d} без возврата" for d in sorted(out_n)
               if next_day[d] not in back_n]
    orphans += [f"{NIGHT_LEGS_DAILY[1][0]} {d} без вылета накануне" for d in sorted(back_n)
                if d not in {next_day[x] for x in out_n}]
    facts["night_pairs"] = len(out_n)
    if orphans:
        problems.append("ночная пара не замкнута внутри горизонта: " + ", ".join(orphans[:4]))
    # Грубая вместимость: смен суток (короткая, длинная, ночёвка и ночная
    # занимают по два календарных дня) хватает пилотов сверх резерва.
    per_day_crews = 1 + 1 + 2 + 2 + 1
    busy = per_day_crews * h0.LIMITS["crew_size"]
    total = sum(n for _, n in h0.CREW_AT)
    facts["capacity"] = {"pilots": total, "busy_max_per_day": busy,
                         "reserve": RESERVE_PILOTS}
    if total - busy < RESERVE_PILOTS:
        problems.append(f"пилотов {total}, занято до {busy} в сутки, резерв "
                        f"{RESERVE_PILOTS} не остаётся (VN-060)")
    # Справочно: средняя занятость пилота за горизонт (правило о выходных в
    # месяц VN-093 из комплекта убрано 05.09.2026 — месяц горизонтом не
    # закрыт; оценка остаётся фактом самопроверки, не условием).
    avg_busy = busy * len(DAYS) / total
    facts["avg_busy_days_per_pilot"] = round(avg_busy, 1)


def _check_history(t: dict, problems: list[str], facts: dict) -> None:
    from cdpm import history_section as hs
    crew = {r["Табельный номер"]: r["База"] for r in t["Экипажи"]["rows"]}
    hist = t["История"]
    facts["history_rows"] = len(hist["rows"])
    fields, rolling, unknown = hs.column_map(hist["columns"])
    if unknown:
        problems.append(f"неопознанные колонки «Истории»: {unknown}")
    seen: set[str] = set()
    as_of_iso = "2026-09-04T00:00"
    day_of_month_before = dt.date.fromisoformat(DAYS[0]).day - 1
    nights_carried = 0
    for row in hist["rows"]:
        rec, probs = hs.parse_row(row, fields, rolling)
        eid = rec.get("employee_id") or "?"
        problems += [f"{eid}: {p}" for p in probs]
        if probs:
            continue
        if eid in seen:
            problems.append(f"{eid}: строка истории дважды")
        seen.add(eid)
        if eid not in crew:
            problems.append(f"{eid}: нет в «Экипажах»")
            continue
        if rec["as_of"] != as_of_iso:
            problems.append(f"{eid}: срез {rec['as_of']} не начало горизонта")
        last, series, month = rec["last_duty"], rec["series"], rec["month_to_date"]
        if last["end"] is None or last["end"] > rec["as_of"]:
            problems.append(f"{eid}: конец последней смены {last['end']} позже среза")
        if last["end_station"] != crew[eid] or rec["location"] != crew[eid]:
            problems.append(f"{eid}: станция/местоположение не база {crew[eid]}")
        nd, nn, off = (series["consecutive_duty_days"],
                       series["consecutive_night_duties"],
                       series["days_since_day_off"])
        if None in (nd, nn, off) or month["days_worked"] is None:
            problems.append(f"{eid}: пустые поля серий или дней месяца")
            continue
        if not nn <= nd <= off:
            problems.append(f"{eid}: серии несогласованы: ночных {nn}, рабочих "
                            f"{nd}, с выходного {off}")
        if month["days_worked"] > day_of_month_before:
            problems.append(f"{eid}: дней отработано {month['days_worked']} больше "
                            f"суток месяца до среза ({day_of_month_before})")
        if bool(last["night"]) != (nn > 0):
            problems.append(f"{eid}: признак «ночная» {last['night']} не согласован "
                            f"с серией ночных {nn}")
        if nn > 0:
            nights_carried += 1
        # Сценарии — значения ровно те, что заявлены.
        got = {"Серия ночных смен": nn, "Последняя смена: ночная": last["night"],
               "Последняя смена: конец": last["end"], "Дней с выходного": off,
               "Серия рабочих дней": nd, "Дней отработано": month["days_worked"]}
        for key, want in EXPECT.get(eid, {}).items():
            if got[key] != want:
                problems.append(f"{eid}: «{key}» = {got[key]!r}, ожидалось {want!r}")
    if seen != set(crew):
        problems.append(f"состав строк истории не равен «Экипажам»: без строки "
                        f"{sorted(set(crew) - seen)}")
    facts["night_series_carried"] = nights_carried
    facts["scenarios"] = {"A": SCENARIO_A, "B": SCENARIO_B, "C": SCENARIO_C}


def self_check(variant: str = DEFAULT_VARIANT) -> tuple[list[str], dict]:
    """Доказательства книги: горизонт семь суток подряд (окно VN-092
    помещается, месяц не пересекается), ночные рейсы каждые сутки
    (вылет в окне ночи), колонки рейсов как у H0, строки «Истории»
    читаются каноном cdpm.history_section и согласованы (конец последней
    смены не позже среза, серии и дни месяца непротиворечивы, сценарии
    A/B/C ровно заявленные). Возврат: (нарушения, числа)."""
    problems: list[str] = []
    facts: dict = {"variant": variant}
    t = tables(variant)
    _check_horizon(t, problems, facts)
    _check_flights(t, problems, facts)
    if variant == "hist":
        _check_history(t, problems, facts)
    elif "История" in t:
        problems.append("вариант nohist несёт лист «История»")
    else:
        facts["history_rows"] = 0
    return problems, facts


def write_xlsx(variant: str = DEFAULT_VARIANT, path: Path | None = None) -> Path:
    import openpyxl
    path = path or HERE / "data" / VARIANTS[variant]
    wb = openpyxl.Workbook()
    first = True
    for name, sheet in tables(variant).items():
        ws = wb.active if first else wb.create_sheet(name)
        ws.title = name
        first = False
        ws.append(list(sheet["columns"]))
        for row in sheet["rows"]:
            ws.append([row.get(c) for c in sheet["columns"]])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="записать xlsx")
    ap.add_argument("--variant", choices=sorted(VARIANTS), default=DEFAULT_VARIANT,
                    help="nohist — без листа «История» (умолчание, простой набор); "
                         "hist — с листом «История» (сценарии A/B/C)")
    a = ap.parse_args(argv)
    problems, facts = self_check(a.variant)
    t = tables(a.variant)
    print(f"книга данных H0-КИ-окна [{a.variant}]:",
          ", ".join(f"{k} {len(v['rows'])}" for k, v in t.items()))
    print(f"горизонт {facts['days']} суток {DAYS[0]}…{DAYS[-1]}; рейсов "
          f"{facts['flights']}, ночных {facts['night_legs']} (в сутки не меньше "
          f"{facts['night_legs_by_day_min']}), через полночь "
          f"{facts['overnight_arrivals']}; ночная смена "
          f"{facts['night_duty']['duty_min']} мин, стыковка "
          f"{facts['night_duty']['turnaround_min']} мин; потоки между базами: "
          f"{facts['flow_balance']}")
    if a.variant == "hist":
        print(f"история: строк {facts['history_rows']}, срез {AS_OF}, серия ночных "
              f"переносится у {facts['night_series_carried']}; сценарии "
              f"A={SCENARIO_A} (ночные 2), B={SCENARIO_B} (конец 03.09 22:00, "
              f"6 дней с выходного), C={SCENARIO_C} (3 дня месяца)")
    if problems:
        print("САМОПРОВЕРКА КРАСНАЯ:\n  " + "\n  ".join(problems))
        return 1
    print("самопроверка чиста")
    if a.write:
        print("записано:", write_xlsx(a.variant))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
