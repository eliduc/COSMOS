# -*- coding: utf-8 -*-
r"""«Полярис Лайт» (PLL) — малый смок-комплект (предложение владельца
2026-08-21): тот же мир и генератор, что у «Поляриса», но объём в разы
меньше — полный конвейер (заморозка → извлечение → сквозной цикл →
анкета → режим 2) проходит за минуты, критические ошибки протокола
ловятся быстро. Пирамида: Лайт (минуты) → Полярис (часы) → UA (сутки).

Состав: 16 правил M0, ~35 клауз (docx + журнал + памятка), 8 дефектов
(по одному на класс), маленькая книга данных (40 рейсов), чужая
формализация с 2 подвохами, книга ответов.

Выход: crew-demo\open-bench\polyaris-light\
Запуск: ..\..\.venv\Scripts\python.exe build_light.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
CREW = HERE.parent.parent
LIGHT = HERE.parent / "polyaris-light"
sys.path.insert(0, str(CREW))
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import formulations as forms                      # noqa: E402
from m0 import RULES, BASES                       # noqa: E402

# Ядро Лайта: 16 правил — все зоны, оба вида, определения и границы.
LIGHT_IDS = ["M-001", "M-003", "M-010", "M-013", "M-014", "M-020",
             "M-023", "M-024", "M-025", "M-040", "M-043", "M-045",
             "M-047", "M-050", "M-060", "M-081"]

# Дефекты Лайта: по одному на класс, все бьют по правилам ядра.
# (id, класс, документ, цель, вид, payload, ожидание, правила)
LIGHT_DEFECTS = [
    ("LT-1", "опечатка", "docx", "M-024", "replace",
     ("внебазовом", "внебазововом"), "нормализация", ["M-024"]),
    ("LS-1", "склейка", "jpg", "M-020", "replace",
     ("Стыковка в базе", "Стыковка вбазе"), "нормализация",
     ["M-020"]),
    ("LK-1", "противоречие", "jpg", "M-010", "replace",
     ("не более 12:00 (с согласия — до 13:00)",
      "не более 11:30 (с согласия — до 13:00)"),
     "вопрос анкеты", ["M-010"]),
    ("LA-1", "двусмысленность", "docx", "M-013", "replace",
     ("не более шести", "как правило, не более шести"),
     "вопрос анкеты", ["M-013"]),
    ("LG-1", "пропуск", "docx", None, "extra",
     "Нормы предоставления питания экипажу в полёте устанавливаются "
     "приложением 2 к настоящим Правилам.",
     "вопрос анкеты", []),
    ("LD-1", "дубль", "docx", "M-023", "extra",
     "Напоминание: отдых между сменами в базе приписки — не менее 12 "
     "часов 30 минут.",
     "вопрос анкеты", ["M-023"]),
    ("LL-1", "битая ссылка", "docx", "M-025", "replace",
     ("предоставляется отдых не менее 14 часов.",
      "предоставляется отдых не менее 14 часов согласно п. 9.4 "
      "настоящих Правил."),
     "замечание отчёта", ["M-025"]),
    ("LX-1", "смешение языков", "docx", "M-014", "asis",
     "check-in", "нормализация", ["M-014"]),
]

THIRD_PARTY_LIGHT = """# Формализация правил «Полярис» (сторонний интегратор, краткая)

1. Продолжительность полётной смены не должна превышать 12 часов
   30 минут.
2. Минимальное время стыковки в аэропорту базы — 45 минут.
3. Отдых между сменами в базе приписки — не менее 12 часов.
4. Месячный налёт члена экипажа не превышает 90 часов.
5. В состав экипажа полётной смены входят два пилота.
6. За работу в праздничные дни начисляется доплата с коэффициентом
   2,0 к кредитным часам.
"""
THIRD_PARTY_LIGHT_EXPECT = [
    {"claim": 1, "verdict": "расходится", "rule": "M-010",
     "note": "12:30 вместо 12:00"},
    {"claim": 6, "verdict": "в источниках отсутствует", "rule": None,
     "note": "праздничная доплата отсутствует в документах"},
]

ANSWER_BOOK_LIGHT = """# Книга правильных ответов «Полярис Лайт»

| Тема (дефект) | Правильный ответ |
|---|---|
| LK-1 предел смены: 12:00 или 11:30? | 12:00 (памятка устарела) |
| LA-1 «как правило, не более шести» | Жёсткое: ровно ≤ 6 |
| LG-1 нормы питания (приложение 2) | Вне контура: не формализуется |
| LD-1 отдых дома: 12:00 или 12:30? | 12:00 (напоминание ошибочно) |
| Веса целевой функции (3а/3б) | Справедливость 3; остальное — по документам |
"""


def main() -> int:
    import defects as defects_mod
    import generate_docs as gen
    import m0 as m0_mod

    # Подмена состава на Лайт: правила, дефекты, шум журнала.
    m0_mod.RULES[:] = [r for r in RULES if r[0] in LIGHT_IDS]
    forms.DOCX = {k: v for k, v in forms.DOCX.items()
                  if k in LIGHT_IDS}
    forms.XLSX = {}
    forms.PAMYATKA = [(rid, line) for rid, line in forms.PAMYATKA
                      if rid in LIGHT_IDS]
    defects_mod.DEFECTS = LIGHT_DEFECTS
    defects_mod.DOSE = {"опечатка": 1, "склейка": 1,
                        "противоречие": 1, "двусмысленность": 1,
                        "пропуск": 1, "дубль": 1, "битая ссылка": 1,
                        "смешение языков": 1}
    gen.XLSX_NOISE = gen.XLSX_NOISE[:6]
    gen.DOCS = LIGHT / "docs"
    gen.ETALON = LIGHT / "etalon"

    rc = gen.main()
    if rc:
        return rc

    (LIGHT / "etalon" / "ANSWER-BOOK.md").write_text(
        ANSWER_BOOK_LIGHT, encoding="utf-8")
    (LIGHT / "docs" / "POLYARIS-LIGHT-THIRD-PARTY.md").write_text(
        THIRD_PARTY_LIGHT, encoding="utf-8")
    (LIGHT / "etalon" / "THIRD-PARTY-EXPECT.json").write_text(
        json.dumps(THIRD_PARTY_LIGHT_EXPECT, ensure_ascii=False,
                   indent=1), encoding="utf-8")

    # Маленькая книга данных: 40 рейсов + экипажи двух баз + 2 дефекта.
    import build_extras as bx
    bx.DATA = LIGHT / "data"
    legs = bx.read_legs()[:40]
    crew_list = [c for c in bx.crews() if c["base"] in ("PLY", "SVG")][:8]
    duty_list = bx.duties(legs, crew_list)[:8]
    legs[5]["_df1"] = True
    from datetime import datetime
    duty_list.append({"tab": duty_list[0]["tab"],
                      "start": datetime(2000, 1, 20, 6, 0),
                      "end": datetime(2000, 1, 20, 19, 30)})
    bx.build_xlsx(legs, crew_list, duty_list)
    (LIGHT / "etalon" / "DATA-DEFECTS.json").write_text(json.dumps(
        [{"id": "LDF-1", "sheet": "Рейсы", "what": "дубль рейса",
          "template": "уникальность", "rule": None},
         {"id": "LDF-2", "sheet": "Смены", "what": "смена 13.5 часа",
          "template": "максимальная-длительность-часов",
          "rule": "M-010"}],
        ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"«Полярис Лайт» готов: {LIGHT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
