# -*- coding: utf-8 -*-
"""Лестница наборов с управляемым шумом: ступени Л0–Л3.

Задача (план 2026-09-02, шаг «измеритель»): классы дефектов обязаны
находиться ПЛАНОВО, а не по факту живого прогона. Ступень — это один и
тот же смысл (правила выдуманной авиакомпании), записанный документами
разного вида, плюс независимая ТАБЛИЦА ОЖИДАНИЙ: какая клауза несёт
норму, какие величины в ней обязаны быть распознаны и какая роль у
записи. Таблица составляется вручную вместе с текстом клаузы и служит
судьёй для измерителя и конвейера — она не выводится из кода, который
проверяется.

Ступени (шум включается по одному измерению):
  Л0 — канонические строки формы ВМП (машинный хвост в строке);
  Л1 — тот же смысл прозой, без машинного хвоста;
  Л2 — проза + структурный шум: заголовки, оглавление, колонтитул,
       сноски, перекрёстные ссылки, продолжение таблицы;
  Л3 — Л2 + неполнота и противоречия: правило без данных, две клаузы с
       разными числами об одном пределе, отсылка к внешнему документу.

Мир ступеней ВЫДУМАН (авиакомпания «Каспий-Тест», базы ALA/TSE/CIT) —
ни один инвариант не должен опираться на наш набор H0.

    python open-bench/ladder/build_ladder.py --out <каталог вне git>
Выход: <каталог>/<ступень>/clauses.json + expectations.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

#: Записи мира: одна норма — одна запись. По каждой заданы: смысл,
#: каноническая строка формы (Л0), прозаическая формулировка (Л1+),
#: ожидаемые величины (что обязан распознать разбор) и роль.
#: Эталонный свод ступени: что обязан был бы выдать безошибочный
#: разбор. Пишется вместе с текстами и коду конвейера не известен —
#: это задачник с известным ответом в выдуманном мире. Ключ «fit» —
#: посадка на семью шаблона с параметрами и цитатами-носителями; ключ
#: «ask» — величина, которую брать нечем и о которой обязан быть
#: задан вопрос (законный остаток).
ENTRIES = [
    {
        "id": "KT-001", "role": "definition",
        "form": ("KT-001. Базы приписки. Базы приписки экипажей: ALA "
                 "(Алматы), TSE (Астана), CIT (Шымкент); главная база — "
                 "ALA. Вид записи: определение или перечень документа."),
        "prose": ("Экипажи приписаны к базам ALA (Алматы), TSE (Астана) и "
                  "CIT (Шымкент). Главной базой считается ALA."),
        "values": ["ALA", "TSE", "CIT", "ALA"],
        "normative": True,
        "kind": "DEF",
    },
    {
        "id": "KT-010", "role": "hard",
        "form": ("KT-010. Предел полётной смены. Полётная смена не должна "
                 "превышать 11:30. Шаблон: duty_limits (категория A). "
                 "Параметры: max_duty_duration = 690 duration_minutes "
                 "(«не должна превышать 11:30»)."),
        "prose": "Полётная смена не должна превышать 11:30.",
        "values": ["11:30"],
        "normative": True,
        "kind": "HARD",
        "fit": ("duty_limits", {"max_duty_duration":
                                (690, "не должна превышать 11:30")}),
    },
    {
        "id": "KT-011", "role": "hard",
        "form": ("KT-011. Стыковка. Минимальная стыковка между рейсами — "
                 "не менее 35 минут. Шаблон: connection_windows "
                 "(категория A). Параметры: min_turnaround = 35 "
                 "duration_minutes («не менее 35 минут»)."),
        "prose": "Минимальная стыковка между рейсами — не менее 35 минут.",
        "values": ["35 минут"],
        "normative": True,
        "kind": "HARD",
        "fit": ("connection_windows", {"min_turnaround":
                                       (35, "не менее 35 минут")}),
    },
    {
        "id": "KT-020", "role": "hard",
        "form": ("KT-020. Отдых дома. Минимальный отдых в базе приписки — "
                 "не менее 11 часов. Шаблон: rest_floors (категория A). "
                 "Параметры: min_rest_home = 660 duration_minutes («не "
                 "менее 11 часов»)."),
        "prose": "Минимальный отдых в базе приписки — не менее 11 часов.",
        "values": ["11 часов"],
        "normative": True,
        "kind": "HARD",
        "fit": ("rest_floors", {"min_rest_home":
                                (660, "не менее 11 часов")}),
    },
    {
        "id": "KT-030", "role": "soft",
        "form": ("KT-030. Ночёвки вне базы. Штраф 8 условных единиц за "
                 "каждую ночёвку вне базы приписки. Шаблон: "
                 "layover_penalty (категория A); носитель нормы: «Штраф 8 "
                 "условных единиц за каждую ночёвку вне базы приписки». "
                 "Штраф: 8 за ночь."),
        "prose": ("Штраф 8 условных единиц за каждую ночёвку вне базы "
                  "приписки."),
        "values": ["8 условных единиц"],
        "normative": True,
        "kind": "SOFT",
        "weight": (8, "Штраф 8 условных единиц за каждую ночёвку вне базы "
                      "приписки"),
        "fit": ("layover_penalty", {}),
    },
    {
        "id": "KT-040", "role": "soft",
        "form": ("KT-040. Разброс нагрузки. Штраф 2 условные единицы за "
                 "каждый час разброса налёта сверх порога 12 часов за "
                 "месяц. Шаблон: balanced_workload (категория A); "
                 "носитель нормы: «Штраф 2 условные единицы за каждый час "
                 "разброса налёта». Штраф: 2 за час."),
        "prose": ("Штраф 2 условные единицы за каждый час разброса налёта "
                  "сверх порога 12 часов за месяц."),
        "values": ["2 условные единицы", "12 часов"],
        "normative": True,
        "kind": "SOFT",
        "weight": (2, "Штраф 2 условные единицы за каждый час разброса "
                      "налёта"),
        "fit": ("balanced_workload", {"spread_threshold":
                                      (720, "сверх порога 12 часов")}),
    },
    {
        "id": "KT-050", "role": "hard",
        "form": ("KT-050. Резерв. Ежедневный резерв: ALA — 3 экипажа, TSE "
                 "— 2 экипажа, CIT — 1 экипаж. Шаблон: reserve_floors "
                 "(категория A). Параметры: reserve_crews = "
                 "ALA:3,CIT:1,TSE:2 count («Ежедневный резерв: ALA — 3 "
                 "экипажа, TSE — 2 экипажа, CIT — 1 экипаж»)."),
        "prose": ("Ежедневный резерв: ALA — 3 экипажа, TSE — 2 экипажа, "
                  "CIT — 1 экипаж."),
        "values": ["ALA", "3 экипажа", "TSE", "2 экипажа", "CIT",
                   "1 экипаж"],
        "normative": True,
        "kind": "HARD",
        "fit": ("reserve_floors", {"reserve_crews": (
            "ALA:3,CIT:1,TSE:2",
            "Ежедневный резерв: ALA — 3 экипажа, TSE — 2 экипажа, "
            "CIT — 1 экипаж")}),
    },
    {
        "id": "KT-O-1", "role": "criterion",
        "form": ("KT-O-1. Стоимость пейрингов. Минимизировать суммарную "
                 "стоимость пейрингов; ставка 90 условных единиц за "
                 "календарный день командировки. Направление: minimize; "
                 "метрика: total_pairing_cost; ярус 1. Машинный критерий: "
                 "min_person_days; ставка 90 за календарный день "
                 "командировки; версия ставок K-12; действует с "
                 "2026-01-01."),
        "prose": ("Минимизировать суммарную стоимость пейрингов; ставка 90 "
                  "условных единиц за календарный день командировки "
                  "(версия ставок K-12, действует с 2026-01-01)."),
        "values": ["90 условных единиц"],
        "normative": True,
        "kind": "OBJECTIVE",
        "objective": {"direction": "minimize",
                      "metric": "total_pairing_cost",
                      "rate": (90, "ставка 90 условных единиц за "
                                   "календарный день командировки")},
    },
]

#: Служебные клаузы: нормы не несут ни при каком виде документа.
SERVICE = [
    {"id": "s-title", "text": "Регламент планирования экипажей «Каспий-Тест»",
     "why": "титул"},
    {"id": "s-toc", "text": "Содержание", "why": "оглавление"},
    {"id": "s-toc-1", "text": "Раздел 1. Общие положения ...... 3",
     "why": "строка оглавления"},
    {"id": "s-head-1", "text": "Раздел 2. Режим труда и отдыха",
     "why": "заголовок раздела"},
    {"id": "s-head-2", "text": "2.3. Ограничения полётной смены",
     "why": "заголовок подраздела"},
    {"id": "s-footer", "text": "Стр. 4 из 12", "why": "колонтитул"},
    {"id": "s-rev", "text": "Редакция 3 от 2026-02-15", "why": "реквизит"},
    {"id": "s-sign", "text": "Директор по производству полётов",
     "why": "подпись"},
    {"id": "s-ref", "text": "См. также Приложение 2 к настоящему "
                            "Регламенту.", "why": "перекрёстная ссылка"},
    {"id": "s-cont", "text": "Продолжение таблицы 4", "why": "продолжение "
                                                             "таблицы"},
    {"id": "s-note", "text": "Примечание: значения приведены в местном "
                             "времени базы.", "why": "сноска-пояснение"},
]

#: Клаузы неполноты и противоречий (ступень Л3): конвейер обязан не
#: молчать, а спросить либо остановиться.
TRICKY = [
    {"id": "x-conflict", "text": "Полётная смена не должна превышать 12:00.",
     "why": "противоречие с KT-010 (11:30)", "normative": True,
     "values": ["12:00"]},
    {"id": "x-nodata", "text": ("Экипаж с истёкшим допуском к полётам не "
                                "назначается на рейс."),
     "why": "правило без данных (справочника допусков нет)",
     "normative": True, "values": []},
    # Отсылка нормы не несёт (норма в другом документе), но обязана
    # породить вопрос авиакомпании — молчаливый пропуск запрещён.
    {"id": "x-external", "text": ("Нормы отдыха при пересечении часовых "
                                  "поясов установлены Приложением 5."),
     "why": "отсылка к внешнему документу", "normative": False,
     "values": [], "question": True},
    # Предписание настоящим временем: модального слова нет, норма есть.
    # Самый частый залог русской регламентной прозы (найден на
    # замороженных комплектах 2026-09-03).
    {"id": "x-passive", "text": ("Время отдыха в полёте засчитывается в "
                                 "служебное полётное время."),
     "why": "предписание настоящим временем", "normative": True,
     "values": []},
    # Норма со ссылкой в хвосте: отсылка не отменяет собственной нормы
    # клаузы, но вопрос авиакомпании порождает.
    {"id": "x-norm-ref", "text": ("Полётная смена не должна превышать "
                                  "значений, приведённых в Приложении 3."),
     "why": "норма с отсылкой в хвосте", "normative": True, "values": [],
     "question": True},
]


#: Ступень Л4: тот же смысл, разложенный по КЛЕТКАМ таблицы. Ни одна
#: клетка нормы не несёт: модальность живёт в шапке колонки, величина —
#: в клетке, предмет — в соседней. Носителем служит строка вместе с
#: шапкой; читатель, выпускающий одни клетки, лишает конвейер носителя.
TABLE_HEAD = ["№", "Показатель", "Предельное значение (не более)",
              "Примечание"]
TABLE_ROWS = [
    (["1", "Полётная смена", "11:30", "для узкофюзеляжных судов"],
     ["11:30"], "предел смены строкой таблицы"),
    (["2", "Стыковка между рейсами", "35 минут", ""],
     ["35 минут"], "минимальная стыковка строкой таблицы"),
    (["3", "Отдых в базе приписки", "11 часов", ""],
     ["11 часов"], "минимальный отдых строкой таблицы"),
]
#: Справочник данных: строка таблицы, которая нормы не несёт ни в каком
#: составе. Правило, выросшее на такой строке, — дефект конвейера.
DATA_HEAD = ["Маршрут", "Расстояние, км"]
DATA_ROWS = [["ALA-TSE", "970"], ["ALA-CIT", "790"], ["TSE-CIT", "1180"]]
#: Паспорт документа: таблица «имя поля | значение», где первая строка
#: не шапка, а запись. Название документа содержит слово «Правила», и
#: раньше это делало таблицу таблицей правил, а её строки — нормами
#: (ложное срабатывание найдено на наборе H0 2026-09-03). Имя колонки
#: коротко; предложение именем колонки не бывает.
PASSPORT_HEAD = ["Документ", "Правила оптимизации планирования экипажей"]
PASSPORT_ROWS = [
    (["Авиакомпания", "«Каспий-Тест» (KTS)"], [], "паспорт документа"),
    (["Версия", "1.0"], [], "паспорт документа"),
    (["Дата", "01.08.2026"], [], "паспорт документа"),
    (["Подразделение", "Отдел планирования экипажей"], [],
     "паспорт документа"),
]
#: Журнал замечаний опытной эксплуатации: строки-записи о дефектах
#: выгрузки и отчётов. Нормой они не являются ни при каком составе, но
#: раньше становились ею из-за двух ловушек: слово «Средний» в колонке
#: приоритета принималось за среду, а отметка времени записи — за
#: величину нормы (найдено на наборе H0 2026-09-03).
JOURNAL_HEAD = ["ID", "Замечание", "Описание", "Приоритет", "Статус",
                "Дата добавления в журнал"]
JOURNAL_ROWS = [
    (["KT-001", "Замечание", "Дубли рейсов в выгрузке расписания за 3-е "
      "число.", "Средний", "В работе", "2026-07-02 00:00:00"], [],
     "запись журнала о дефекте выгрузки"),
    (["KT-002", "Замечание", "Не подтянулись фамилии в отчёте по "
      "цепочкам.", "Высокий", "Закрыто", "2026-07-03 00:00:00"], [],
     "запись журнала о дефекте отчёта"),
]
#: Таблица правил: шапка ОБЪЯВЛЯЕТ назначение колонки. Норма записана
#: утвердительным настоящим временем — ни модального слова, ни величины
#: в ней нет, и признать её нормой позволяет только положение.
RULES_HEAD = ["№", "Правило", "Описание"]
RULES_ROWS = [
    (["П.1", "Однотипность пейринга",
      "В пейринг входят рейсы одного типа воздушного судна"], [],
     "норма-утверждение в таблице правил"),
    (["П.2", "Деление по подразделениям",
      "Бортпроводники планируются все вместе, без деления по отрядам"], [],
     "норма-утверждение в таблице правил"),
]
#: Реестр правил колонками «Наименование / Формулировка / Характер»:
#: форма, встреченная на замороженных комплектах (2026-09-03). Норму
#: объявляет колонка «Формулировка», а не слова строки.
REGISTRY_HEAD = ["№", "Наименование", "Формулировка", "Характер",
                 "Значение", "Примечание"]
REGISTRY_ROWS = [
    (["2.16", "Замыкание цепочки",
      "Цепочка начинается и заканчивается в базе приписки", "жёсткое",
      "", ""], [], "норма в реестре правил"),
    (["3.11", "Баланс баз",
      "Нагрузка распределяется между базами приписки равномерно",
      "мягкое", "", ""], [], "норма в реестре правил"),
]
#: Таблица определений: связка набрана ДЕФИСОМ, как её набирают в
#: документах, а не типографским тире.
TERMS_HEAD = ["Код", "Определение"]
TERMS_ROWS = [
    (["КВС", "КВС - командир воздушного судна, занимает левое кресло"], [],
     "определение через дефис"),
    (["ВП", "ВП - второй пилот, занимает правое кресло в кабине"], [],
     "определение через дефис"),
]


#: Ступень Л5: тот же мир по-английски. Руководства по производству
#: полётов у многих перевозчиков ведутся на английском, и мера обязана
#: читать их так же, как русские: язык — свойство записи, а не смысла.
EN_ENTRIES = [
    ("The flight duty period shall not exceed 11:30.", ["11:30"],
     "предел смены по-английски", "EN-010", "HARD",
     ("duty_limits", {"max_duty_duration": (690, "shall not exceed 11:30")})),
    ("The minimum connection time between flights is 35 minutes.",
     ["35 minutes"], "стыковка по-английски", "EN-011", "HARD",
     ("connection_windows", {"min_turnaround": (35, "is 35 minutes")})),
    ("Crew members must be given at least 11 hours of rest at the home "
     "base.", ["11 hours"], "отдых по-английски", "EN-020", "HARD",
     ("rest_floors", {"min_rest_home": (660, "at least 11 hours")})),
    ("A penalty of 8 points applies for each night away from the home "
     "base.", ["8 points"], "штраф по-английски", "EN-030", "SOFT",
     ("layover_penalty", {})),
    ("Daily reserve: ALA — 3 crews, TSE — 2 crews, CIT — 1 crew.",
     ["3 crews", "2 crews", "1 crew"], "резерв по базам по-английски",
     "EN-050", "HARD",
     ("reserve_floors", {"reserve_crews": (
         "ALA:3,CIT:1,TSE:2",
         "ALA — 3 crews, TSE — 2 crews, CIT — 1 crew")})),
    ("In-flight rest time is counted towards the flight duty period.",
     [], "предписание настоящим временем по-английски", "EN-060", "DEF",
     None),
]
EN_SERVICE = [
    ("Crew Planning Manual", "титул"),
    ("Table of Contents", "оглавление"),
    ("Chapter 3. Flight Time Limitations", "заголовок раздела"),
    ("Page 4 of 12", "колонтитул"),
    ("Director of Flight Operations", "подпись"),
    ("Continued on next page", "продолжение таблицы"),
]
EN_RULES_HEAD = ["No", "Rule", "Description"]
EN_RULES_ROWS = [
    (["R.1", "Pairing homogeneity",
      "A pairing consists of flights operated by one aircraft type"], [],
     "норма-утверждение в английской таблице правил"),
]


def _clause(sid, anchor, text):
    return {"sid": sid, "anchor": anchor, "text": text}


def build_step(step: str) -> tuple[list[dict], list[dict]]:
    """Клаузы ступени и таблица ожиданий."""
    clauses: list[dict] = []
    expect: list[dict] = []
    n = 0

    def add(text, *, normative, role, values, why="", question=False):
        nonlocal n
        n += 1
        anchor = f"п.{n}"
        clauses.append(_clause("S1", anchor, text))
        expect.append({"anchor": anchor, "normative": normative,
                       "role": role, "values": list(values), "why": why,
                       "question": bool(question)})

    if step == "Л5":
        for text, why in EN_SERVICE:
            add(text, normative=False, role="service", values=[], why=why)
        for text, vals, why, *_rest in EN_ENTRIES:
            add(text, normative=True, role="rule", values=vals, why=why)
        for col, cell in enumerate(EN_RULES_HEAD, start=1):
            clauses.append(_clause("S1", f"табл.1, строка 1, кол {col}",
                                   cell))
        expect.append({"anchor": "табл.1, строка 1", "normative": False,
                       "role": "service", "values": [],
                       "why": "шапка английской таблицы", "question": False})
        for r, (cells, vals, why) in enumerate(EN_RULES_ROWS, start=2):
            for col, cell in enumerate(cells, start=1):
                clauses.append(_clause(
                    "S1", f"табл.1, строка {r}, кол {col}", cell))
            expect.append({"anchor": f"табл.1, строка {r}", "normative": True,
                           "role": "hard", "values": list(vals), "why": why,
                           "question": False})
        return clauses, expect

    if step in ("Л2", "Л3"):
        for s in SERVICE[:6]:
            add(s["text"], normative=False, role="service", values=[],
                why=s["why"])
    for e in ENTRIES:
        text = e["form"] if step == "Л0" else e["prose"]
        add(text, normative=e["normative"], role=e["role"],
            values=e["values"], why=f"запись {e['id']}")
        if step in ("Л2", "Л3") and e["id"] == "KT-020":
            for s in SERVICE[6:]:
                add(s["text"], normative=False, role="service", values=[],
                    why=s["why"])
    if step == "Л3":
        for t in TRICKY:
            add(t["text"], normative=t["normative"], role="tricky",
                values=t["values"], why=t["why"],
                question=bool(t.get("question")))

    if step == "Л4":
        # Клетки идут в клаузы, ожидания пишутся на НОСИТЕЛЕ — строке.
        def table(num, head, rows, *, normative, role, why):
            for col, cell in enumerate(head, start=1):
                clauses.append(_clause(
                    "S1", f"табл.{num}, строка 1, кол {col}", cell))
            expect.append({"anchor": f"табл.{num}, строка 1",
                           "normative": False, "role": "service",
                           "values": [], "why": "шапка таблицы",
                           "question": False})
            for r, item in enumerate(rows, start=2):
                cells = item[0] if isinstance(item, tuple) else item
                vals = item[1] if isinstance(item, tuple) else []
                note = item[2] if isinstance(item, tuple) else why
                for col, cell in enumerate(cells, start=1):
                    if not cell:
                        continue
                    clauses.append(_clause(
                        "S1", f"табл.{num}, строка {r}, кол {col}", cell))
                expect.append({"anchor": f"табл.{num}, строка {r}",
                               "normative": normative, "role": role,
                               "values": list(vals), "why": note,
                               "question": False})

        table(1, TABLE_HEAD, TABLE_ROWS, normative=True, role="hard",
              why="норма строкой таблицы")
        table(2, DATA_HEAD, DATA_ROWS, normative=False, role="data",
              why="строка справочника данных: нормы не несёт")
        table(3, RULES_HEAD, RULES_ROWS, normative=True, role="hard",
              why="норма-утверждение в таблице правил")
        table(4, TERMS_HEAD, TERMS_ROWS, normative=True, role="definition",
              why="определение через дефис")
        table(5, REGISTRY_HEAD, REGISTRY_ROWS, normative=True, role="hard",
              why="норма в реестре правил (колонка «Формулировка»)")
        table(6, PASSPORT_HEAD, PASSPORT_ROWS, normative=False,
              role="service", why="строка паспорта документа")
        table(7, JOURNAL_HEAD, JOURNAL_ROWS, normative=False, role="service",
              why="запись журнала замечаний")
        n += 1
        clauses.append(_clause("S1", f"п.{n}", SERVICE[9]["text"]))
        expect.append({"anchor": f"п.{n}", "normative": False,
                       "role": "service", "values": [],
                       "why": SERVICE[9]["why"], "question": False})
    return clauses, expect


def reference_model(step: str, expect: list[dict]) -> dict:
    """Эталонный свод ступени: что выдал бы безошибочный разбор.

    Строится из тех же записей мира и привязывается к якорям ступени,
    поэтому годится и как задачник конвейеру (кассета с известным
    ответом в выдуманном мире), и как проверка самой меры: на нём
    структурные классы обязаны быть нулевыми, а весь остаток —
    закрытым вопросом.
    """
    by_why = {e["why"]: e["anchor"] for e in expect}
    rules, objectives, questions = [], [], []
    if step == "Л5":
        for text, _vals, why, rid, kind, fit in EN_ENTRIES:
            anchor = by_why.get(why)
            if anchor is None:
                continue
            rule = {"id": f"R-{rid}", "kind": kind, "zone": "PAIRING",
                    "title": rid, "statement": text,
                    "sources": [{"sid": "S1", "anchor": anchor}]}
            if fit:
                fam, params = fit
                rule["template_fit"] = {
                    "category": "A", "family": fam,
                    "registry_rev": "ladder",
                    "params": {n: {"value": v, "quote": q}
                               for n, (v, q) in params.items()}}
            if kind == "SOFT":
                rule["weight"] = 8
            rules.append(rule)
        anchor = by_why.get("норма-утверждение в английской таблице правил")
        if anchor:
            rules.append({
                "id": "R-EN-070", "kind": "HARD", "zone": "PAIRING",
                "title": "EN-070",
                "statement": "A pairing consists of flights operated by one "
                             "aircraft type",
                "sources": [{"sid": "S1", "anchor": anchor}]})
        return {"meta": {"customer": "Каспий-Тест", "mode": 1,
                         "cdpm_version": "ladder"},
                "sources": [{"sid": "S1", "file": f"ступень {step}"}],
                "rules": rules, "objectives": [], "questions": []}
    for e in ENTRIES:
        anchor = by_why.get(f"запись {e['id']}")
        if anchor is None:
            continue
        src = [{"sid": "S1", "anchor": anchor}]
        text = e["form"] if step == "Л0" else e["prose"]
        if e.get("kind") == "OBJECTIVE":
            o = e["objective"]
            objectives.append({
                "id": f"O-{e['id']}", "zone": "PAIRING",
                "direction": o["direction"], "metric": o["metric"],
                "statement": text, "sources": src,
                "title": e["id"],
                # Имена полей — канон схемы модели: ступень служит и
                # кассетой конвейеру, поэтому говорит его языком.
                "rate": {"value": o["rate"][0], "as_written": o["rate"][1],
                         "per": "календарный день командировки"},
                "tier": 1,
            })
            continue
        rule = {"id": f"R-{e['id']}", "kind": e.get("kind", "HARD"),
                "zone": "PAIRING", "title": e["id"], "statement": text,
                "sources": src}
        fam, params = e.get("fit", (None, {}))
        if fam:
            rule["template_fit"] = {
                "category": "A", "family": fam, "registry_rev": "ladder",
                "params": {n: {"value": v, "quote": q}
                           for n, (v, q) in params.items()}}
        if e.get("weight"):
            # У веса мягкого правила слота цитаты в схеме нет — это
            # известный пробел контракта, и мера считает его отдельным
            # классом «нет слота носителя», а не ложью свода.
            rule["weight"] = e["weight"][0]
        rules.append(rule)
    # Величины, которые брать нечем, закрываются вопросом — иначе они
    # были бы молчаливой потерей (И-5).
    for e in expect:
        if e.get("question"):
            questions.append({
                "id": f"Q-{e['anchor']}", "part": "3b",
                "title": e["why"],
                "text": f"Уточните норму, названную записью {e['anchor']}.",
                "answer_format": {"type": "value"},
                "rule_refs": ["R-KT-010"],
                "source_refs": [{"sid": "S1", "anchor": e["anchor"]}],
            })
    return {"meta": {"customer": "Каспий-Тест", "mode": 1,
                     "cdpm_version": "ladder"},
            "sources": [{"sid": "S1", "file": f"ступень {step}"}],
            "rules": rules, "objectives": objectives,
            "questions": questions}


def main(argv=None) -> int:
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="каталог ступеней (вне git)")
    ap.add_argument("--steps", default="Л0,Л1,Л2,Л3,Л4,Л5")
    a = ap.parse_args(argv)
    root = Path(a.out)
    for step in [s.strip() for s in a.steps.split(",") if s.strip()]:
        clauses, expect = build_step(step)
        d = root / step
        d.mkdir(parents=True, exist_ok=True)
        (d / "clauses.json").write_text(
            json.dumps({"clauses": clauses}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        (d / "expectations.json").write_text(
            json.dumps({"step": step, "expect": expect}, ensure_ascii=False,
                       indent=1), encoding="utf-8")
        model = reference_model(step, expect)
        (d / "model.json").write_text(
            json.dumps(model, ensure_ascii=False, indent=1),
            encoding="utf-8")
        norm = sum(1 for e in expect if e["normative"])
        print(f"{step}: клауз {len(clauses)} (нормативных {norm}, "
              f"служебных {len(expect) - norm}) -> {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
