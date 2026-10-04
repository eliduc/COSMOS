# -*- coding: utf-8 -*-
r"""«Полярис-нано» (PLN) — набор для живого демо CDPM (директива
владельца 2026-08-21): каждый блок конвейера отрабатывает за секунды,
внимание аудитории не теряется.

Состав: 9 правил M0 (минимум клауз при согласованном мире), 2 дефекта
(опечатка — показывает нормализацию; противоречие docx против памятки —
рождает вопрос анкеты на глазах у зала), книга данных 6 рейсов с одним
дефектом, чужая формализация с 1 подвохом, книга ответов. Входные
файлы: PNI1 (docx), PNE1 (xlsx), PNC1 (jpg).

Выход: crew-demo\open-bench\polyaris-nano\ + копия входных файлов в
C:\work\Sandbox\Testing\TimeFold\Polaris\nano\
Запуск: ..\..\.venv\Scripts\python.exe build_nano.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
CREW = HERE.parent.parent
OUT = HERE.parent / "polyaris-nano"
DELIVER = Path(r"C:\work\Sandbox\Testing\TimeFold\Polaris\nano")
sys.path.insert(0, str(CREW))
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import formulations as forms                      # noqa: E402
from m0 import RULES                              # noqa: E402

# Ядро нано: 10 правил — базы и резерв держат согласованность мира
# (коды баз в docx и памятке), минимальный рабочий набор + мягкие
# ОБЕИХ зон (ЦФ-1: ни целевых, ни весов в документах — конвейер
# обязан спросить их вопросом Q-*-GOAL). M-004 (шкала времени) —
# ночь демо 29→30.08: без шкалы в ВМП построителю не по чему
# понимать окна местного времени (строка-7), а флаг из ниоткуда —
# число вне ВМП; смещение шкалы приходит ответом анкеты по теме
# носителя контура (ремень (а)).
NANO_IDS = ["M-003", "M-004", "M-010", "M-020", "M-023", "M-024",
            "M-030", "M-050", "M-060", "M-081"]

# Демо сквозного потока (решение владельца 2026-08-29): правило с
# ГАРАНТИРОВАННЫМ пробелом шаблона у расчётной стороны — календарного
# окна дней недели нет ни в одной механике ядра (сверено реестром
# механик COSMOS 29.08). Мягкое; вес по ЦФ-1 приходит ответом анкеты;
# календарный день ночёвки — по местному времени БАЗОВОЙ станции
# (названо в тексте явно, чтобы вопрос поясов не возник в кадре).
DEMO_GAP_RULE = ("M-D91", "Ночёвки в конце недели",
                 "штраф за ночёвку вне базы в пятницу или субботу "
                 "(календарный день по местному времени базы); вес "
                 "не установлен", "PAIRING", "SOFT", "")
DEMO_GAP_DOCX = (2, "Ночёвки в конце недели",
                 "Ночёвка вне базовой станции, календарный день "
                 "которой (по местному времени базовой станции) "
                 "приходится на пятницу или субботу, особенно "
                 "нежелательна: за каждую такую ночёвку начисляется "
                 "штраф; величина штрафа настоящими документами не "
                 "установлена.",
                 "мягкое", "—", "")

DOCX_NAME = "PNI1-Pravila-Polyaris.docx"
XLSX_NAME = "PNE1-Zhurnal-Polyaris.xlsx"
JPG_NAME = "PNC1-Pamyatka-Polyaris.jpg"

# Дефекты нано: 2 — по одному на «вау-момент» демо.
# (id, класс, документ, цель, вид, payload, ожидание, правила)
NANO_DEFECTS = [
    ("NT-1", "опечатка", "docx", "M-024", "replace",
     ("внебазовом", "внебазововом"), "нормализация", ["M-024"]),
    ("NK-1", "противоречие", "jpg", "M-010", "replace",
     ("не более 12:00 (с согласия — до 13:00)",
      "не более 11:30 (с согласия — до 13:00)"),
     "вопрос анкеты", ["M-010"]),
]

THIRD_PARTY_NANO = """# Формализация правил «Полярис» (сторонний интегратор, нано)

1. Продолжительность полётной смены не должна превышать 12 часов
   30 минут.
2. Минимальное время стыковки в аэропорту базы — 45 минут.
3. В состав экипажа полётной смены входят два пилота.
"""
THIRD_PARTY_NANO_EXPECT = [
    {"claim": 1, "verdict": "расходится", "rule": "M-010",
     "note": "12:30 вместо 12:00"},
]

ANSWER_BOOK_NANO = """# Книга правильных ответов «Полярис-нано»

| Тема (дефект) | Правильный ответ |
|---|---|
| NK-1 предел смены: 12:00 или 11:30? | 12:00 (памятка устарела) |
| Целевая функция пейринга (Q-3a-GOAL) | Компонент: суммарная стоимость пейрингов; минимизировать; ярус 1; вес 1. Вес мягкого «ночь вне базы» — 10; вес мягкого «ночёвки в конце недели» — 25 |
| Целевая функция ростеринга (Q-3b-GOAL) | Компонент: равномерность кредитных часов; максимизировать; ярус 1; вес 1. Вес мягкого «разброс кредита» — 3 |
| Отдых вне базы (Q-3a-условия-отдых-вне-базы) | 10 часов |
| Шкала времени (Q-3a-условия-шкала-времени) | 0 минут (все времена данных уже в местном времени главной базы) |
| Машинный критерий пейринга (Q-3a-критерий-пейринга) | Минимум человеко-дней командировок. {"criterion": "MIN_PERSON_DAYS", "rate_per_duty": 100, "rule": "O-S1-табл-2-строка-2-кол-3", "version": "M-036/demo-1", "effective_from": "2026-08-01"} |
"""

README_NANO = """# «Полярис-нано» (PLN) — набор для живого демо CDPM

Назначение: демонстрация конвейера в реальном времени — каждый блок
отрабатывает за секунды. 10 правил (включая мягкие обеих зон без
весов и целевых — ЦФ-1, и демо-правило «ночёвки в конце недели» с
гарантированным пробелом шаблона расчётной стороны — для кадра
«трафарет на лету»), 2 дефекта: опечатка (зал видит нормализацию) и
противоречие docx против памятки (зал видит рождение вопроса анкеты).
Книга данных 6 рейсов с одним дефектом (дубль рейса).

Входные файлы (импортировать в CDPM):
- PNI1-Pravila-Polyaris.docx — правила (docx)
- PNE1-Zhurnal-Polyaris.xlsx — журнал замечаний (xlsx)
- PNC1-Pamyatka-Polyaris.jpg — памятка планировщику (jpg)

Дополнительно: POLYARIS-DATA.xlsx (книга данных),
POLYARIS-NANO-THIRD-PARTY.md (режим 2), ANSWER-BOOK.md (ответы
авиакомпании). Эталон и реестр дефектов:
crew-demo\\open-bench\\polyaris-nano\\etalon\\
Прогон — по инструкции прогона «Полярис» (каталог TimeFold\\Polaris).
"""


def main() -> int:
    import defects as defects_mod
    import generate_docs as gen
    import m0 as m0_mod

    # Подмена состава на нано: правила, дефекты, имена файлов, шум.
    m0_mod.RULES[:] = [r for r in RULES if r[0] in NANO_IDS]
    forms.DOCX = {k: v for k, v in forms.DOCX.items()
                  if k in NANO_IDS}
    import cf1_softening
    cf1_softening.soften(forms, m0_mod)
    # Демо-правило пробела — ПОСЛЕ фильтра и смягчения ЦФ-1 (вес не
    # задан документами; календарное окно — пробел шаблона).
    m0_mod.RULES.append(DEMO_GAP_RULE)
    forms.DOCX["M-D91"] = DEMO_GAP_DOCX
    forms.XLSX = {}
    forms.PAMYATKA = [(rid, line) for rid, line in forms.PAMYATKA
                      if rid in NANO_IDS]
    defects_mod.DEFECTS = NANO_DEFECTS
    defects_mod.DOSE = {"опечатка": 1, "противоречие": 1}
    gen.XLSX_NOISE = gen.XLSX_NOISE[:2]
    gen.DOCS = OUT / "docs"
    gen.ETALON = OUT / "etalon"
    gen.DOCX_NAME = DOCX_NAME
    gen.XLSX_NAME = XLSX_NAME
    gen.JPG_NAME = JPG_NAME

    rc = gen.main()
    if rc:
        return rc

    (OUT / "etalon" / "ANSWER-BOOK.md").write_text(
        ANSWER_BOOK_NANO, encoding="utf-8")
    (OUT / "docs" / "POLYARIS-NANO-THIRD-PARTY.md").write_text(
        THIRD_PARTY_NANO, encoding="utf-8")
    (OUT / "etalon" / "THIRD-PARTY-EXPECT.json").write_text(
        json.dumps(THIRD_PARTY_NANO_EXPECT, ensure_ascii=False,
                   indent=1), encoding="utf-8")

    # Книга данных: 6 рейсов + экипажи ТРЁХ баз + 1 дефект. Экипаж
    # посилен покрытию (ответ на вопрос вехи А 2026-08-22: 6 рейсов ×
    # 2 места при 2 людях делали задачу ростеринга нерешаемой — нано
    # теперь и набор конвейера, И решаемая задача).
    import build_extras as bx
    bx.DATA = OUT / "data"
    bx.VAC_START = 14   # отпуска ПОСЛЕ недели рейсов 07-12.09
    legs = bx.read_legs()[:6]
    # Многодневный мир (слово владельца, ночь 30.08, путь 1): шесть
    # рейсов ложатся на неделю сентября тремя парами «туда-обратно»
    # — дневная пара PLY, ночёвка среда→четверг вне базы (вес 10
    # содержательно) и ночёвка ПЯТНИЦА→суббота вне базы (вес 25 —
    # календарное окно строки-7 срабатывает не нулём). Времена
    # согласованы с порогами мира: смена ≤ 12:00, стыковка ≥ 45 мин,
    # отдых вне базы ≥ 10 ч. Прежняя однодневность шла из шкалы дат
    # GERAD-набора и делала покрытие доказанно невыполнимым.
    NANO_SCHEDULE = [
        ("PLY", "2026-09-07", "09:00", "P01", "2026-09-07", "10:30"),
        ("P01", "2026-09-07", "12:00", "PLY", "2026-09-07", "13:30"),
        ("SVG", "2026-09-09", "10:00", "P06", "2026-09-09", "11:20"),
        ("P06", "2026-09-10", "09:30", "SVG", "2026-09-10", "10:50"),
        ("GRT", "2026-09-11", "18:00", "P09", "2026-09-11", "19:30"),
        ("P09", "2026-09-12", "10:00", "GRT", "2026-09-12", "11:30"),
    ]
    for leg, (dep, d1, t1, arr, d2, t2) in zip(legs, NANO_SCHEDULE):
        leg.update({"dep": dep, "d1": d1, "t1": t1,
                    "arr": arr, "d2": d2, "t2": t2})
    all_crews = bx.crews()
    # Смены раздаются экипажам PLY — база обязана быть в выборке.
    # 12 человек = по одному на место (6 рейсов × 2): рейсы нано —
    # разрозненные плечи без обратных, второй рейс тому же человеку
    # ломает непрерывность маршрута (живой прогон вехи А: 8 человек
    # дали −22hard при полном покрытии).
    crew_list = ([c for c in all_crews if c["base"] == "PLY"][:6]
                 + [c for c in all_crews if c["base"] == "SVG"][:3]
                 + [c for c in all_crews if c["base"] == "GRT"][:3])
    assert crew_list, "в книге данных нет экипажей базы PLY"
    duty_list = bx.duties(legs, crew_list)[:2]
    legs[1]["_df1"] = True
    bx.build_xlsx(legs, crew_list, duty_list)
    (OUT / "etalon" / "DATA-DEFECTS.json").write_text(json.dumps(
        [{"id": "NDF-1", "sheet": "Рейсы", "what": "дубль рейса",
          "template": "уникальность", "rule": None}],
        ensure_ascii=False, indent=1), encoding="utf-8")

    # Гейт комплекта — тот же, что у «Поляриса», на каталогах нано.
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
            (OUT / "docs" / "POLYARIS-NANO-THIRD-PARTY.md",
             "POLYARIS-NANO-THIRD-PARTY.md"),
            (OUT / "etalon" / "ANSWER-BOOK.md", "ANSWER-BOOK.md")]:
        shutil.copy2(src, DELIVER / dst)
    (DELIVER / "README-PLN.md").write_text(README_NANO,
                                           encoding="utf-8")
    print(f"«Полярис-нано» готов: {OUT}")
    print(f"входные файлы владельцу: {DELIVER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
