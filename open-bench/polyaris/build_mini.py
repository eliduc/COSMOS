# -*- coding: utf-8 -*-
r"""«Полярис-мини» (PLM) — набор для быстрой проверки идей CDPM
(директива владельца 2026-08-21): полный конвейер за ~10–20 минут.

Состав: 10 правил M0 (~16 клауз на три файла), 4 дефекта проверенных
классов (опечатка, противоречие, двусмысленность, дубль), маленькая
книга данных (12 рейсов, 2 дефекта), чужая формализация с 2 подвохами,
книга ответов. Входные файлы: PMI1 (docx), PME1 (xlsx), PMC1 (jpg).

Выход: crew-demo\open-bench\polyaris-mini\ + копия входных файлов в
C:\work\Sandbox\Testing\TimeFold\Polaris\mini\
Запуск: ..\..\.venv\Scripts\python.exe build_mini.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
CREW = HERE.parent.parent
OUT = HERE.parent / "polyaris-mini"
DELIVER = Path(r"C:\work\Sandbox\Testing\TimeFold\Polaris\mini")
sys.path.insert(0, str(CREW))
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import formulations as forms                      # noqa: E402
from m0 import RULES                              # noqa: E402

# Ядро мини: 10 правил — определение баз, пределы смены и посадок,
# стыковка, отдых дома и вне базы, мягкие ОБЕИХ зон (ночёвки —
# пейринг, справедливость — ростеринг; ЦФ-1: веса и целевые в
# документах НЕ заданы — цикл обязан спросить их вопросом Q-*-GOAL),
# резерв (несёт коды баз в памятке), экипаж.
MINI_IDS = ["M-003", "M-010", "M-013", "M-020", "M-023", "M-024",
            "M-030", "M-050", "M-060", "M-081"]

DOCX_NAME = "PMI1-Pravila-Polyaris.docx"
XLSX_NAME = "PME1-Zhurnal-Polyaris.xlsx"
JPG_NAME = "PMC1-Pamyatka-Polyaris.jpg"

# Дефекты мини: 4 проверенных Лайтом класса, все бьют по правилам ядра.
# (id, класс, документ, цель, вид, payload, ожидание, правила)
MINI_DEFECTS = [
    ("MT-1", "опечатка", "docx", "M-024", "replace",
     ("внебазовом", "внебазововом"), "нормализация", ["M-024"]),
    ("MK-1", "противоречие", "jpg", "M-010", "replace",
     ("не более 12:00 (с согласия — до 13:00)",
      "не более 11:30 (с согласия — до 13:00)"),
     "вопрос анкеты", ["M-010"]),
    ("MA-1", "двусмысленность", "docx", "M-013", "replace",
     ("не более шести", "как правило, не более шести"),
     "вопрос анкеты", ["M-013"]),
    ("MD-1", "дубль", "docx", "M-023", "extra",
     "Напоминание: отдых между сменами в базе приписки — не менее 12 "
     "часов 30 минут.",
     "вопрос анкеты", ["M-023"]),
]

THIRD_PARTY_MINI = """# Формализация правил «Полярис» (сторонний интегратор, мини)

1. Продолжительность полётной смены не должна превышать 12 часов
   30 минут.
2. Минимальное время стыковки в аэропорту базы — 45 минут.
3. Отдых между сменами в базе приписки — не менее 12 часов.
4. За работу в праздничные дни начисляется доплата с коэффициентом
   2,0 к кредитным часам.
"""
THIRD_PARTY_MINI_EXPECT = [
    {"claim": 1, "verdict": "расходится", "rule": "M-010",
     "note": "12:30 вместо 12:00"},
    {"claim": 4, "verdict": "в источниках отсутствует", "rule": None,
     "note": "праздничная доплата отсутствует в документах"},
]

ANSWER_BOOK_MINI = """# Книга правильных ответов «Полярис-мини»

| Тема (дефект) | Правильный ответ |
|---|---|
| MK-1 предел смены: 12:00 или 11:30? | 12:00 (памятка устарела) |
| MA-1 «как правило, не более шести» | Жёсткое: ровно ≤ 6 |
| MD-1 отдых дома: 12:00 или 12:30? | 12:00 (напоминание ошибочно) |
| Целевая функция пейринга (Q-3a-GOAL) | Компонент: суммарная стоимость пейрингов; минимизировать; ярус 1; вес 1. Вес мягкого «ночь вне базы» — 10 |
| Целевая функция ростеринга (Q-3b-GOAL) | Компонент: равномерность кредитных часов; максимизировать; ярус 1; вес 1. Вес мягкого «разброс кредита» — 3 |
"""

README_MINI = """# «Полярис-мини» (PLM) — набор для быстрой проверки идей CDPM

Назначение: обкатка новых идей пайплайна за ~10–20 минут (вместо часов
у «Лайта»). 10 правил (включая мягкие обеих зон без весов и целевых
— ЦФ-1), 4 дефекта (опечатка, противоречие, двусмысленность, дубль),
книга данных 12 рейсов с 2 дефектами.

Входные файлы (импортировать в CDPM):
- PMI1-Pravila-Polyaris.docx — правила (docx)
- PME1-Zhurnal-Polyaris.xlsx — журнал замечаний (xlsx)
- PMC1-Pamyatka-Polyaris.jpg — памятка планировщику (jpg)

Дополнительно: POLYARIS-DATA.xlsx (книга данных),
POLYARIS-MINI-THIRD-PARTY.md (режим 2), ANSWER-BOOK.md (ответы
авиакомпании). Эталон и реестр дефектов:
crew-demo\\open-bench\\polyaris-mini\\etalon\\
Прогон — по инструкции прогона «Полярис» (каталог TimeFold\\Polaris).
"""


def main() -> int:
    import defects as defects_mod
    import generate_docs as gen
    import m0 as m0_mod

    # Подмена состава на мини: правила, дефекты, имена файлов, шум.
    m0_mod.RULES[:] = [r for r in RULES if r[0] in MINI_IDS]
    forms.DOCX = {k: v for k, v in forms.DOCX.items()
                  if k in MINI_IDS}
    import cf1_softening
    cf1_softening.soften(forms, m0_mod)
    forms.XLSX = {}
    forms.PAMYATKA = [(rid, line) for rid, line in forms.PAMYATKA
                      if rid in MINI_IDS]
    defects_mod.DEFECTS = MINI_DEFECTS
    defects_mod.DOSE = {"опечатка": 1, "противоречие": 1,
                        "двусмысленность": 1, "дубль": 1}
    gen.XLSX_NOISE = gen.XLSX_NOISE[:4]
    gen.DOCS = OUT / "docs"
    gen.ETALON = OUT / "etalon"
    gen.DOCX_NAME = DOCX_NAME
    gen.XLSX_NAME = XLSX_NAME
    gen.JPG_NAME = JPG_NAME

    rc = gen.main()
    if rc:
        return rc

    (OUT / "etalon" / "ANSWER-BOOK.md").write_text(
        ANSWER_BOOK_MINI, encoding="utf-8")
    (OUT / "docs" / "POLYARIS-MINI-THIRD-PARTY.md").write_text(
        THIRD_PARTY_MINI, encoding="utf-8")
    (OUT / "etalon" / "THIRD-PARTY-EXPECT.json").write_text(
        json.dumps(THIRD_PARTY_MINI_EXPECT, ensure_ascii=False,
                   indent=1), encoding="utf-8")

    # Книга данных: 12 рейсов + экипажи трёх баз + 2 дефекта. Экипаж
    # посилен покрытию (ответ на вопрос вехи А 2026-08-22: 24 места
    # при 4 людях делали задачу ростеринга нерешаемой).
    import build_extras as bx
    bx.DATA = OUT / "data"
    legs = bx.read_legs()[:12]
    # Смены раздаются экипажам PLY — база обязана быть в выборке.
    all_crews = bx.crews()
    # По человеку на место (12 рейсов × 2 = 24): разрозненные плечи
    # без обратных — второй рейс тому же человеку ломает
    # непрерывность маршрута (урок вехи А на нано).
    crew_list = ([c for c in all_crews if c["base"] == "PLY"][:12]
                 + [c for c in all_crews if c["base"] == "SVG"][:6]
                 + [c for c in all_crews if c["base"] == "GRT"][:6])
    duty_list = bx.duties(legs, crew_list)[:4]
    legs[3]["_df1"] = True
    from datetime import datetime
    duty_list.append({"tab": duty_list[0]["tab"],
                      "start": datetime(2000, 1, 20, 6, 0),
                      "end": datetime(2000, 1, 20, 19, 30)})
    bx.build_xlsx(legs, crew_list, duty_list)
    (OUT / "etalon" / "DATA-DEFECTS.json").write_text(json.dumps(
        [{"id": "MDF-1", "sheet": "Рейсы", "what": "дубль рейса",
          "template": "уникальность", "rule": None},
         {"id": "MDF-2", "sheet": "Смены", "what": "смена 13.5 часа",
          "template": "максимальная-длительность-часов",
          "rule": "M-010"}],
        ensure_ascii=False, indent=1), encoding="utf-8")

    # Гейт комплекта — тот же, что у «Поляриса», на каталогах мини.
    import gate_polyaris as gate
    gate.DOCS = OUT / "docs"
    gate.ETALON = OUT / "etalon"
    gate.DOCX_NAME = DOCX_NAME
    gate.XLSX_NAME = XLSX_NAME
    rc = gate.main()
    if rc:
        return rc

    # Копия для владельца: тройка входных + данные + режим 2 + ответы.
    DELIVER.mkdir(parents=True, exist_ok=True)
    for src, dst in [
            (OUT / "docs" / DOCX_NAME, DOCX_NAME),
            (OUT / "docs" / XLSX_NAME, XLSX_NAME),
            (OUT / "docs" / JPG_NAME, JPG_NAME),
            (OUT / "data" / "POLYARIS-DATA.xlsx", "POLYARIS-DATA.xlsx"),
            (OUT / "docs" / "POLYARIS-MINI-THIRD-PARTY.md",
             "POLYARIS-MINI-THIRD-PARTY.md"),
            (OUT / "etalon" / "ANSWER-BOOK.md", "ANSWER-BOOK.md")]:
        shutil.copy2(src, DELIVER / dst)
    (DELIVER / "README-PLM.md").write_text(README_MINI,
                                           encoding="utf-8")
    print(f"«Полярис-мини» готов: {OUT}")
    print(f"входные файлы владельцу: {DELIVER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
