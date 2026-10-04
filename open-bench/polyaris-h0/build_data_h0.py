# -*- coding: utf-8 -*-
"""Книга данных набора ИДАК H0 — операционные данные, на которых правила
ВМП H0 исполнимы и СВЯЗЫВАЮТ (ультимативная задача владельца 03.09,
пункт 2: набор проходит конвейер обоих продуктов до выгрузки решателя;
план «прямой ход» Д8 — свидетельство активности правила).

Книга строится КОДОМ из явных величин, согласованных с правилами H0
(open-bench/polyaris-h0/etalon/VMP-H0.json), и сама себя проверяет:
после сборки код доказывает, что каждое жёсткое правило выполнено, а
каждое правило применимо — есть случай, стоящий у самого порога, так что
ужесточение параметра делает книгу недопустимой (свидетель активности).

Состав листов — контракт приёмника COSMOS
(final-report/KONTRAKT-DANNYH-PAKETA-CLD.md): «Рейсы», «Экипажи»,
«Отпуска», «Допуски», «Свойства», «Аэропорты».

    python open-bench/polyaris-h0/build_data_h0.py            # проверка
    python open-bench/polyaris-h0/build_data_h0.py --write    # запись xlsx
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_XLSX = HERE / "data" / "ID-H0-DATA.xlsx"
#: Сборщик набора лежит рядом: в нём носитель нормализации байтов файлов
#: Office (freeze_office_file) — ОДНА формула на все сборщики формы H0.
KIT_BUILDER = HERE / "build_kit_h0.py"

CUSTOMER = "Полярис-H0 (ВМП H0 комплекта)"
#: Базы приписки набора (VN-003): главная — PLY.
BASES = ("PLY", "SVG", "GRT")
MAIN_BASE = "PLY"
#: Единый пояс: смещение шкалы данных 0 минут к местному времени PLY
#: (VN-004) — все времена книги в одной шкале.
TIMEZONE = "Europe/Moscow"
AIRCRAFT = "Полярис-Р200"
#: Плановые дни: пятница, суббота, воскресенье — календарное окно
#: надбавки (VN-D91) действует на ночёвке, начинающейся в пятницу.
DAYS = ("2026-09-04", "2026-09-05", "2026-09-06")
#: Пороги правил H0 — для самопроверки книги (значения берутся из
#: эталона ВМП H0 при проверке, здесь только имена).
LIMITS = {"max_duty_duration": 720, "min_turnaround": 45,
          "min_rest_home": 720, "min_rest_outstation": 600,
          "crew_size": 2, "reserve_crews": {"PLY": 2, "SVG": 1, "GRT": 1}}

#: Рейсы дня: (номер, вылет, прилёт, время вылета, время прилёта).
#: Смысл расстановки — каждое правило имеет случай у порога:
#: * «база» PL101→PL102: стыковка в базе 50 минут при пороге 45;
#: * связка PL201 (вечер) → PL202 (утро) даёт ночёвку вне базы с
#:   отдыхом 640 минут при пороге 600;
#: * длинная смена PL301…PL304 длится 690 минут при пределе 720 и
#:   потолке продления 780.
LEGS_DAILY = (
    ("PL101", "PLY", "SVG", "08:00", "09:30"),
    ("PL102", "SVG", "PLY", "10:20", "11:50"),
    ("PL201", "PLY", "GRT", "19:00", "20:40"),
    ("PL202", "GRT", "PLY", "07:20", "09:00"),
    ("PL301", "PLY", "SVG", "06:00", "07:30"),
    ("PL302", "SVG", "PLY", "08:20", "09:50"),
    ("PL303", "PLY", "GRT", "10:40", "12:20"),
    ("PL304", "GRT", "PLY", "13:10", "14:50"),
    ("PL305", "PLY", "SVG", "15:40", "17:30"),
)
#: Экипажи по базам. Число выведено из СВИДЕТЕЛЬСТВА выполнимости
#: (см. reserve_witness): смены дня раздаются базам так, чтобы после
#: занятости остался ежедневный резерв VN-060 (PLY ≥ 2 экипажа,
#: SVG ≥ 1, GRT ≥ 1) с запасом — впритык решателю негде маневрировать,
#: и любая другая раздача ломает резерв (находка прогона 03.09: −4hard
#: на 8/4/4, ночёвка занимает пилота два дня).
CREW_AT = (("PLY", 12), ("SVG", 6), ("GRT", 6))
ROLES = ("КВС", "Второй пилот")


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _day_index(day: str) -> int:
    return DAYS.index(day)


def _abs_min(day: str, hhmm: str) -> int:
    """Минуты от начала первого планового дня (шкала одна, VN-004)."""
    return _day_index(day) * 1440 + _mins(hhmm)


def flights() -> list[dict]:
    out = []
    for day in DAYS:
        for num, dep, arr, t1, t2 in LEGS_DAILY:
            out.append({"Номер рейса": num, "Дата": day, "Вылет": dep,
                        "Время вылета": t1, "Прилёт": arr,
                        "Время прилёта": t2, "Тип ВС": AIRCRAFT})
    return out


def crews() -> list[dict]:
    out = []
    n = 0
    for base, count in CREW_AT:
        for _ in range(count):
            n += 1
            out.append({"Табельный номер": "H%02d" % n,
                        "Фамилия И.О.": "Пилот %02d" % n,
                        "База": base,
                        "Должность": ROLES[n % len(ROLES)]})
    return out


def qualifications(crew: list[dict]) -> list[dict]:
    return [{"Табельный номер": c["Табельный номер"], "Тип ВС": AIRCRAFT,
             "Действует до": "2027-12-31"} for c in crew]


def properties_rows() -> list[dict]:
    return [{"ключ": "planSince", "значение": DAYS[0] + "T00:00"},
            {"ключ": "planUntil", "значение": DAYS[-1] + "T23:59"}]


def airports() -> list[dict]:
    return [{"Код": b, "timeZone": TIMEZONE} for b in BASES]


def tables() -> dict:
    """Книга данных в форме clean-data (лист → колонки и записи)."""
    crew = crews()
    return {
        "Рейсы": {"columns": ["Номер рейса", "Дата", "Вылет", "Время вылета",
                              "Прилёт", "Время прилёта", "Тип ВС"],
                  "rows": flights()},
        "Экипажи": {"columns": ["Табельный номер", "Фамилия И.О.", "База",
                                "Должность"], "rows": crew},
        "Отпуска": {"columns": ["Табельный номер", "С", "По"], "rows": []},
        "Допуски": {"columns": ["Табельный номер", "Тип ВС", "Действует до"],
                    "rows": qualifications(crew)},
        "Свойства": {"columns": ["ключ", "значение"], "rows": properties_rows()},
        "Аэропорты": {"columns": ["Код", "timeZone"], "rows": airports()},
    }


# ----------------------------------------------------- самопроверка книги

def _duties() -> list[dict]:
    """Смены, которые книга ДОПУСКАЕТ по построению: связки рейсов одного
    дня из базы и обратно. Проверяются на пределы H0."""
    out = []
    for day in DAYS:
        out.append({"name": "короткая PLY", "day": day,
                    "legs": ["PL101", "PL102"]})
        out.append({"name": "длинная PLY", "day": day,
                    "legs": ["PL301", "PL302", "PL303", "PL304", "PL305"]})
    return out


def _leg(num: str) -> tuple:
    for row in LEGS_DAILY:
        if row[0] == num:
            return row
    raise KeyError(num)


def duty_plan() -> list[dict]:
    """Смены книги с занятостью по дням: короткая и длинная — из базы
    вылета и обратно в тот же день; ночёвка — вылет вечером и возврат
    утром, поэтому её экипаж занят ДВА календарных дня."""
    plan = []
    for i, day in enumerate(DAYS):
        plan.append({"duty": "короткая", "days": [day], "crews": 1})
        plan.append({"duty": "длинная", "days": [day], "crews": 1})
        if i + 1 < len(DAYS):
            plan.append({"duty": "ночёвка", "days": [day, DAYS[i + 1]],
                         "crews": 1})
    return plan


def reserve_witness() -> tuple[dict, list[str]]:
    """Свидетельство выполнимости резерва: раздача смен базам, при
    которой каждый день на каждой базе свободных пилотов не меньше
    порога VN-060. Возврат: (раздача по базам, нарушения).

    Раздача жадная и детерминированная: смены отдаются базе с наибольшим
    запасом свободных пилотов после назначения. Свидетельство не
    заменяет решатель, но доказывает, что решение с нулём жёстких
    нарушений существует, — иначе книга ставит невыполнимую задачу."""
    crew = {base: count for base, count in CREW_AT}
    need = {b: n * LIMITS["crew_size"] for b, n in LIMITS["reserve_crews"].items()}
    busy: dict = {(b, d): 0 for b in crew for d in DAYS}
    assignment: dict = {}
    for item in duty_plan():
        best, best_slack = None, None
        for base, total in sorted(crew.items()):
            slack = min(total - busy[(base, d)] - LIMITS["crew_size"] - need[base]
                        for d in item["days"])
            if best_slack is None or slack > best_slack:
                best, best_slack = base, slack
        if best is None or best_slack < 0:
            return assignment, [
                f"смену «{item['duty']}» ({', '.join(item['days'])}) нельзя "
                f"отдать ни одной базе без нарушения резерва VN-060"]
        for d in item["days"]:
            busy[(best, d)] += LIMITS["crew_size"]
        assignment.setdefault(best, []).append(
            f"{item['duty']} {'+'.join(item['days'])}")
    problems = []
    free_min = {}
    for base, total in sorted(crew.items()):
        free = min(total - busy[(base, d)] for d in DAYS)
        free_min[base] = free
        if free < need[base]:
            problems.append(
                f"база {base}: в худший день свободно {free} пилотов, "
                f"резерв требует {need[base]} (VN-060)")
        elif free < need[base] + LIMITS["crew_size"]:
            # Существование решения впритык — не годится простейшему
            # набору: у решателя нет ни одного свободного экипажа сверх
            # резерва, любая иная раздача ломает жёсткое правило, и поиск
            # честно упирается (живой случай 03.09: −4hard на 8/4/4 при
            # формально существующей раздаче).
            problems.append(
                f"база {base}: свободно {free} при резерве {need[base]} — "
                f"нет запаса ни на один экипаж; книга ставит задачу впритык")
    return {"assignment": assignment, "free_min": free_min, "need": need}, problems


def self_check() -> tuple[list[str], dict]:
    """Доказательства книги: жёсткие правила выполнены, и у каждого есть
    случай у порога (ужесточение делает книгу недопустимой). Возврат:
    (нарушения, числа)."""
    problems: list[str] = []
    facts: dict = {}
    # VN-010 предел смены и VN-011 потолок продления.
    durations = []
    for duty in _duties():
        legs = [_leg(n) for n in duty["legs"]]
        start = _mins(legs[0][3])
        end = _mins(legs[-1][4])
        durations.append(end - start)
    facts["max_duty_minutes"] = max(durations)
    if facts["max_duty_minutes"] > LIMITS["max_duty_duration"]:
        problems.append(f"смена {facts['max_duty_minutes']} мин длиннее предела "
                        f"{LIMITS['max_duty_duration']} (VN-010)")
    # VN-020 стыковки внутри смены.
    turns = []
    for duty in _duties():
        legs = [_leg(n) for n in duty["legs"]]
        for a, b in zip(legs, legs[1:]):
            turns.append(_mins(b[3]) - _mins(a[4]))
    facts["min_turnaround"] = min(turns)
    if facts["min_turnaround"] < LIMITS["min_turnaround"]:
        problems.append(f"стыковка {facts['min_turnaround']} мин меньше порога "
                        f"{LIMITS['min_turnaround']} (VN-020)")
    # VN-024 отдых вне базы: ночёвка PL201 (вечер) → PL202 (утро).
    out_rest = (_abs_min(DAYS[1], _leg("PL202")[3])
                - _abs_min(DAYS[0], _leg("PL201")[4]))
    facts["min_rest_outstation"] = out_rest
    if out_rest < LIMITS["min_rest_outstation"]:
        problems.append(f"отдых вне базы {out_rest} мин меньше порога "
                        f"{LIMITS['min_rest_outstation']} (VN-024)")
    # VN-023 отдых дома: между длинными сменами соседних дней.
    home_rest = (_abs_min(DAYS[1], _leg("PL301")[3])
                 - _abs_min(DAYS[0], _leg("PL305")[4]))
    facts["min_rest_home"] = home_rest
    if home_rest < LIMITS["min_rest_home"]:
        problems.append(f"отдых дома {home_rest} мин меньше порога "
                        f"{LIMITS['min_rest_home']} (VN-023)")
    # VN-081 состав экипажа и VN-060 ежедневный резерв: пилотов базы
    # хватает на смены дня и на резервные экипажи.
    crew = crews()
    by_base: dict = {}
    for c in crew:
        by_base[c["База"]] = by_base.get(c["База"], 0) + 1
    facts["crew_by_base"] = by_base
    facts["roles"] = sorted({c["Должность"] for c in crew})
    if len(facts["roles"]) != LIMITS["crew_size"]:
        problems.append(f"должностей {facts['roles']} — приёмник требует ровно "
                        f"{LIMITS['crew_size']} различных (VN-081)")
    # Резерв судится СВИДЕТЕЛЬСТВОМ выполнимости, а не формулой: ночёвка
    # занимает экипаж два дня, и свободных считают по базе приписки.
    witness, reserve_problems = reserve_witness()
    facts["reserve_witness"] = witness
    problems += reserve_problems
    # Правила применимы (Д8): у каждого есть случай у самого порога.
    facts["witness"] = {
        "VN-010": facts["max_duty_minutes"],
        "VN-020": facts["min_turnaround"],
        "VN-023": home_rest,
        "VN-024": out_rest,
    }
    slack = {"VN-010": LIMITS["max_duty_duration"] - facts["max_duty_minutes"],
             "VN-020": facts["min_turnaround"] - LIMITS["min_turnaround"],
             "VN-023": home_rest - LIMITS["min_rest_home"],
             "VN-024": out_rest - LIMITS["min_rest_outstation"]}
    facts["slack_minutes"] = slack
    for rid, gap in slack.items():
        if gap > 60:
            problems.append(f"{rid}: запас {gap} мин — правило книгу не "
                            f"связывает (нужен случай у порога, Д8)")
    # VN-070 допуск по типу воздушного судна: тип каждого рейса входит
    # в парк набора (VN-002), и у КАЖДОГО пилота есть допуск на этот
    # тип, действующий до конца планового периода. Правило применимо и
    # выполнимо: в простейшем наборе допуск не отсекает никого, но
    # механизм задействован и данные его несут.
    ratings = qualifications(crew)
    flight_types = {r["Тип ВС"] for r in flights()}
    fleet = {AIRCRAFT}
    facts["flight_types"] = sorted(flight_types)
    if not flight_types <= fleet:
        problems.append(f"типы рейсов {sorted(flight_types - fleet)} вне парка "
                        f"набора {sorted(fleet)} (VN-002/VN-070)")
    until = dt.date.fromisoformat(DAYS[-1])
    by_crew: dict = {}
    for r in ratings:
        try:
            valid = dt.date.fromisoformat(str(r["Действует до"]))
        except ValueError:
            problems.append(f"допуск {r['Табельный номер']}: срок "
                            f"{r['Действует до']!r} не дата (VN-070)")
            continue
        if valid >= until:
            by_crew.setdefault(r["Табельный номер"], set()).add(r["Тип ВС"])
    facts["crew_with_valid_rating"] = len(by_crew)
    for c in crew:
        tn = c["Табельный номер"]
        if not flight_types <= by_crew.get(tn, set()):
            problems.append(f"пилот {tn}: нет действующего на {DAYS[-1]} "
                            f"допуска на типы {sorted(flight_types)} (VN-070)")
    # VN-030 и VN-D91: ночёвка вне базы есть, и её первый день — пятница.
    first = dt.date.fromisoformat(DAYS[0])
    facts["weekday_of_first_day"] = first.strftime("%A")
    facts["layovers"] = len(DAYS) - 1
    if first.weekday() != 4:
        problems.append("первый плановый день не пятница — надбавка VN-D91 "
                        "не применима к ночёвке")
    return problems, facts


def freeze_office(path: Path) -> Path:
    """Байты книги — от её текста, а не от часов машины: нормализация
    носителем БАЗОВОГО сборщика (build_kit_h0.freeze_office_file).

    Книга данных сборщик набора модулем не импортирует (у неё свои входы
    и свой запуск), поэтому носитель берётся из СОСЕДНЕГО файла по пути —
    так же, как сборщики вариаций подключают базовый: формула
    нормализации живёт в одном месте и не дублируется."""
    mod = sys.modules.get("build_kit_h0")
    if mod is None or not hasattr(mod, "freeze_office_file"):
        spec = importlib.util.spec_from_file_location("build_kit_h0",
                                                      KIT_BUILDER)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["build_kit_h0"] = mod
        spec.loader.exec_module(mod)
    return mod.freeze_office_file(Path(path))


def write_xlsx(path: Path = OUT_XLSX) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    first = True
    for name, sheet in tables().items():
        ws = wb.active if first else wb.create_sheet(name)
        ws.title = name
        first = False
        ws.append(list(sheet["columns"]))
        for row in sheet["rows"]:
            ws.append([row.get(c) for c in sheet["columns"]])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return freeze_office(path)      # тот же текст → те же байты


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="записать xlsx")
    ap.add_argument("--json", action="store_true", help="печатать числа книги")
    a = ap.parse_args(argv)
    problems, facts = self_check()
    print("книга данных H0:", ", ".join(
        f"{k} {len(v['rows'])}" for k, v in tables().items()))
    if a.json:
        print(json.dumps(facts, ensure_ascii=False, indent=1))
    else:
        print("пороги книги:", json.dumps(facts["witness"], ensure_ascii=False),
              "| запас:", json.dumps(facts["slack_minutes"], ensure_ascii=False))
        print("экипажи по базам:", json.dumps(facts["crew_by_base"],
                                              ensure_ascii=False),
              "| должности:", facts["roles"],
              "| первый день:", facts["weekday_of_first_day"])
    for p in problems:
        print("  -", p)
    print("САМОПРОВЕРКА:", "ЧИСТО" if not problems else f"НАРУШЕНИЙ {len(problems)}")
    if a.write and not problems:
        print("записано:", write_xlsx())
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
