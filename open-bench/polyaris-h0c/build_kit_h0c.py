# -*- coding: utf-8 -*-
"""Сборщик набора «ИД H0-C» — вариант C (поручение владельца 09.09.2026):
правило, под которое в каталоге шаблонов НЕТ семьи.

Комплект выводится из формы H0 (open-bench/polyaris-h0/etalon/
VMP-H0-form.json) тем же генератором build_kit_h0. Отличий от H0 два:
  * в части 2 а) (лист «VMP-H0-2») ДОБАВЛЕНА строка VN-027 «Отпуск»:
    «Член экипажа не может быть назначен на рейс в дни своего отпуска»
    (HARD, ROSTERING). Семьи под неё в каталоге 0.5.0 нет: понятие
    недоступности сотрудника не судит ни одна семья, критерий или
    словарь (строгий поиск по всему тексту записей, 16.09.2026);
  * в книге данных на листе «Отпуска» ОДНА строка (слово владельца
    16.09.2026): без неё правило сядет, но в решении не сработает ни разу.
Исполнители у правила уже есть: ограничение решателя «Employee
unavailable» (HARD_EMPLOYEE_UNAVAILABLE по Employee.unavailableDays) и
проверка сверки с ИДАК вида unavailability (vmp/source_check.py) —
новой разработки решателя сценарий не требует, только записи каталога
и привязки.

Две фазы (EXPECTED.md, ключ «expect» манифеста):
  фаза 1 (каталог без семьи): посадка C у CDPM → заявка в каталог
    (стоп → заявка → шаблон), пакет с готовностью «в» не выдаётся;
  фаза 2 (каталог с семьёй после слова владельца и выпуска): правило
    садится на новую семью, ВМП = H0 + VN-027, готовность «в», COSMOS:
    пейринг «построен», ростер «допустимо», сверки с ИДАК без нарушений
    (строк справочно — см. EXPECT_PHASE_2).

Запуск: python open-bench/polyaris-h0c/build_kit_h0c.py [--phase 2
--family <id семьи из каталога> [--same-run]] — фаза 2 перестраивает
эталон ВМП по живому каталогу (семья, параметры, версия и отпечаток).
Ключ --same-run — продолжение ТОГО ЖЕ прогона фазы 1 (слово владельца
16.09.2026: показывать Н0-С одним прогоном): документы и книга данных
остаются байт в байт документами фазы 1 (они уже заморожены прогоном,
и паспорт раннера сверяет их с заморозкой), а эталон и ожидания — фазы 2.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
H0 = REPO / "open-bench" / "polyaris-h0"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(H0))
import build_kit_h0 as bk  # noqa: E402
from cdpm import vmp_form as vf  # noqa: E402

CUSTOMER = "Полярис-H0-C (ВМП H0 с правилом без шаблона)"
DOCX_NAME = "ID-H0C-VMP-1.docx"
XLSX_NAME = "ID-H0C-VMP-2.xlsx"
JPG_NAME = "ID-H0C-VMP-3.jpg"
DATA_NAME = bk.DATA_NAME            # книга данных — H0 плюс строка отпуска
#: Одна строка отпуска в книге данных (слово владельца 16.09.2026):
#: без неё правило VN-027 сядет, но в решении не сработает ни разу — на
#: листе «Отпуска» набора H0 ноль строк. Табельный номер — КВС базы PLY
#: (у базы шесть КВС на пять вылетов в сутки, останется пять), даты — два
#: дня из трёх суток горизонта planSince…planUntil. Книга H0 не трогается:
#: строка дописывается в КОПИЮ набора H0-C, и копия замораживается тем же
#: законом, что документы (байты от текста, а не от часов машины).
VACATION_SHEET = "Отпуска"
VACATION_ROW = {"Табельный номер": "H02", "С": "2026-09-05", "По": "2026-09-06"}
RENAMES = {bk.DOCX_NAME: DOCX_NAME, bk.XLSX_NAME: XLSX_NAME,
           bk.JPG_NAME: JPG_NAME}
#: Новое правило без семьи в каталоге — ИСТОРИЯ ВЫБОРА (16.09.2026).
#: Прежнее VN-026 «Серия смен» пробелом быть перестало — его семья
#: duty_series_limit вышла в каталоге 0.4.0 и стоит на проде (0.5.0).
#: Первая замена (днём) — ДОПУСК НА ТИП; её доводы были такими:
#:   * семьи о допусках в каталоге 0.5.0 нет (проверено по subject_words
#:     всех 17 семей: ни «допуск», ни «квалификац»);
#:   * данные УЖЕ в наборе — лист «Допуски» (Табельный номер, Тип ВС,
#:     Действует до);
#:   * исполнитель УЖЕ в решателе — HARD_TYPE_RATING_CURRENCY
#:     (FlightCrewSchedulingConstraintProvider.kt), и колонка «Действует
#:     до» доезжает до него с 2026-08-23.
#: Значит сценарий проходит до решений и выгрузок без новой разработки
#: решателя: не хватает ровно записи каталога и привязки.
#: 16.09 вечер, ОТЗЫВ первой замены: «допуск на тип» каталог ЗНАЕТ —
#: семья fleet_exclusions (subject «допуски и исключения по типам судов»,
#: semantics.measure «назначение без действующего допуска на тип судна —
#: недопустимо», check_templates «действительность-на-дату»); в C оно
#: падало по привязке параметров, а не по отсутствию понятия. Пробел искал
#: по одному полю subject_words — этого мало. Строгий поиск по всему
#: тексту семей, критериев и словарей дал понятие БЕЗ семьи: недоступность
#: сотрудника (отпуска). Три оси измерены: семьи/критерия/словаря нет;
#: исполнитель есть — HARD_EMPLOYEE_UNAVAILABLE по Employee.unavailableDays;
#: путь данных есть — лист «Отпуска» → vmp/stand_solver.UNAVAIL_SHEET →
#: unavailableDays → vmp/source_check (сверка с ИДАК читает то же поле).
NEW_RULE = "VN-027"
NEW_TITLE = "Отпуск"
NEW_STATEMENT = ("Член экипажа не может быть назначен на рейс в дни "
                 "своего отпуска.")
#: Роли параметров семьи по формулировке VN-027. Правило признаковое:
#: числа у него нет вовсе — семья каталога вправе выйти БЕЗ параметров
#: (как calendar_windows и layover_penalty), и тогда посадка A несёт
#: пустой перечень параметров. Если же семья объявит параметр, фаза 2
#: связывает его с единственной ролью unavailable (ключ --param имя=роль
#: при одном параметре не нужен); значение — признак, цитата —
#: ДОСЛОВНЫЙ кусок формулировки.
PARAM_ROLES = {
    "unavailable": {"value": True,
                    "quote": "не может быть назначен на рейс в дни "
                             "своего отпуска"},
}
NEW_QUOTE = PARAM_ROLES["unavailable"]["quote"]
#: Роль, обязательная у одного из параметров, когда параметры у семьи есть.
MAIN_ROLE = "unavailable"
#: Слова пробела покрытия (посадка C): семьи нет и почему соседние не годятся.
GAP_WORDS = ("недоступность члена экипажа в дни отпуска — семьи каталога "
             "нет: days_off_floor судит норму выходных в окне, rolling_rest — "
             "недельный отдых, reserve_floors — резерв на базах; дни, когда "
             "человек отсутствует по отпуску, не судит ни одна семья, критерий "
             "или словарь")
#: После какого правила листа «VMP-H0-2» стоит новая строка (часть 2 а).
AFTER_RULE = "VN-060"
#: Обход дефекта линии данных CDPM (09.09.2026): вопрос «нет данных» у
#: правила посадки C; ответ «а» — считать до получения данных.
NO_DATA_QUESTION_PREFIX = "Q-3b-нет-данных-"
NO_DATA_ANSWER = "а"
EXPECT_PHASE_1 = {
    "_comment": ("Фаза 1 H0-C (каталог без семьи): машинное правило VN-027 "
                 "садится в категорию C — пробел покрытия; CDPM подаёт "
                 "заявку в каталог (черновик семьи), прогон останавливается "
                 "«стоп → заявка → шаблон»; пакет с готовностью «в» не "
                 "выдаётся. Вопрос анкеты по VN-027 не задаётся (иначе "
                 "строка не кандидат заявки)."),
    "questions": [],   # вопросы истории по виду — при сборке (каталог 0.5.0)
    "objectives": 2,
    "coverage_gap_rules": [NEW_RULE],
    # 16.09.2026: слова ожидания идут за правилом VN-027. Единица
    # параметра ЧЕРНОВИКА заявки — признак (флаг), а не счёт: у правила
    # числа нет вовсе; сама семья каталога вправе выйти без параметров.
    # Формулировка заявки — ПЕРЕСКАЗ модели-извлекателя (прогоны 16.09
    # 22:27 и 17.09 00:08 дали «в дни своего отпуска» и «в дни, отмеченные
    # как отпуск»): ожидание держится за предмет (основу «отпуск»), а
    # правило узнаётся по номеру строки листа, не по дословности.
    "catalog_requests": {"count": 1, "kind": "HARD", "zone": "ROSTERING",
                         "statement_contains": "отпуск",
                         "param_unit": "flag"},
    "package_ready": False,
}
EXPECT_PHASE_2 = {
    "_comment": ("Фаза 2 H0-C (каталог с семьёй после слова владельца, CR "
                 "конвейера и выпуска): VN-027 садится в категорию A, ВМП = "
                 "H0 + VN-027, готовность «в»; COSMOS: пейринг «построен», "
                 "ростер «допустимо», обе сверки с ИДАК без нарушений "
                 "(строк справочно 11 и 14 — H0 плюс VN-027)."),
    "questions": [],   # вопросы истории по виду — при сборке (каталог 0.5.0)
    "objectives": 2,
    # Лестница как у H0: обе части несут критерии оптимизации.
    "readiness_ladder": {"pairing": "optimization",
                         "rostering": "optimization"},
    "rule_landing": {NEW_RULE: "A"},
    # Числа сверки с ИДАК — «строк/нарушений». Правка 10.09.2026 по
    # фактическим прогонам фазы 2 на трёх ступенях 09.09.2026 (локально
    # 12/0 и 15/0, Test 11/0 и 14/0, Prod 11/0 и 14/0): прежние 9/0 и
    # 13/0 устарели ещё с волны 7. Критерий «без ошибок» — слово
    # владельца: вердикты, сертификаты и НОЛЬ нарушений; ЧИСЛО СТРОК
    # СПРАВОЧНОЕ — оно гуляет по чтению модели (not_checkable ±1), и
    # расхождение ровно в строках при нуле нарушений раннер показывает
    # предупреждением, а не провалом.
    "cosmos_numbers": {"pairing": "11/0", "rostering": "14/0",
                       "_note": "справочно, по чтению ИДАК каналом api: "
                                "строк ok+soft_info+not_checkable / "
                                "нарушений; число строк гуляет по чтению "
                                "модели, критерий — вердикты, сертификаты "
                                "и 0 нарушений"},
    # Ожидаемые вердикты стадий (11.09.2026): общие берутся у базового
    # набора, пояснение — своё. Фазе 1 ключа нет: там пакет не выдаётся.
    "stage_verdicts": bk.stage_verdicts(
        "ожидаемые вердикты стадий фазы 2. Извлечение эквивалентно "
        "эталону, если модель не задала необязательный вопрос по VN-027 "
        "(логика «свод против пересказа», величина «одна»); задала — "
        "NOT_EQUIVALENT до ответа, оба пути законны. Вопросы об истории "
        "рождаются линией данных (стадия cycle) и закрываются книгой "
        "ответов; после ответов и в пакете ВМП = H0 + VN-027. Вердикты "
        "стадий COSMOS судятся только "
        "с машинным отчётом нажатий (--cosmos-press). У фазы 1 ключа "
        "нет: прогон встаёт на пробеле покрытия, пакет не выдаётся, и "
        "вердикт пакета там другой.",
        extract=["EQUIVALENT", "NOT_EQUIVALENT"]),
    # Вердикт гейта непротиворечивости в манифесте пакета (11.09.2026):
    # слово одно на все наборы формы H0 — берётся у базового набора.
    # Фазе 1 ключа нет: там пакет не выдаётся, и вердикта в манифесте
    # взяться неоткуда.
    "consistency": bk.CONSISTENCY,
    # Сшивка записи о прогоне — одна на все наборы формы H0 (шаг 2
    # проекта «Запись о прогоне»): ожидание берётся у базового набора.
    "record_stitched": copy.deepcopy(bk.RECORD_STITCHED),
    # Золотым прогон называет только владелец (шаг 3 проекта, решение
    # владельца 6.3): ожидание тоже берётся у базового набора. Фазе 1
    # его нет — прогон встаёт до нажатий, приёмки не было.
    "golden_by_owner_only": copy.deepcopy(bk.GOLDEN_BY_OWNER_ONLY),
}

_ROW_RE = re.compile(r"строка (\d+)")


def check_sources(sources: list, et: dict) -> None:
    """Обе проекции эталона — об одних и тех же файлах (закон базового
    сборщика, 11.09.2026): состав источников ВМП обязан совпасть с
    формой по (sid, файл). Расхождение — отказ словами: эталон, который
    называет файлы, которых в комплекте нет, врёт паспорту прогона."""
    mine = [(str(s.get("sid")), str(s.get("file"))) for s in sources]
    theirs = [(str(s.get("sid")), str(s.get("file")))
              for s in (et.get("sources") or [])]
    if theirs != mine:
        raise SystemExit(f"ЭТАЛОН И ФОРМА О РАЗНЫХ ИСТОЧНИКАХ: ВМП {theirs}, "
                         f"форма {mine}")


def _row_of(anchor: str) -> int:
    m = _ROW_RE.search(str(anchor))
    if not m:
        raise SystemExit(f"якорь без номера строки: {anchor!r}")
    return int(m.group(1))


def add_vacation_row(book: Path) -> None:
    """Дописать строку отпуска в копию книги данных H0-C. Самопроверка —
    словами: лист есть и пуст, шапка та же, табельный номер есть на листе
    «Экипажи», обе даты внутри горизонта листа «Свойства», С ≤ По."""
    import openpyxl
    wb = openpyxl.load_workbook(str(book))
    if VACATION_SHEET not in wb.sheetnames:
        raise SystemExit(f"в книге данных нет листа «{VACATION_SHEET}»")
    ws = wb[VACATION_SHEET]
    head = [c.value for c in ws[1]][:3]
    if head != list(VACATION_ROW):
        raise SystemExit(f"шапка листа «{VACATION_SHEET}» {head} ≠ "
                         f"{list(VACATION_ROW)}")
    if ws.max_row != 1:
        raise SystemExit(f"лист «{VACATION_SHEET}» книги H0 не пуст "
                         f"({ws.max_row - 1} строк): строка отпуска задумана "
                         f"единственной — проверьте книгу H0")
    crews = {str(r[0].value) for r in wb["Экипажи"].iter_rows(min_row=2)
             if r[0].value is not None}
    tab = VACATION_ROW["Табельный номер"]
    if tab not in crews:
        raise SystemExit(f"табельного номера {tab} нет на листе «Экипажи»")
    props = {str(r[0].value): str(r[1].value)
             for r in wb["Свойства"].iter_rows(min_row=2) if r[0].value}
    since = props.get("planSince", "")[:10]
    until = props.get("planUntil", "")[:10]
    if not (since <= VACATION_ROW["С"] <= VACATION_ROW["По"] <= until):
        raise SystemExit(f"отпуск {VACATION_ROW['С']}…{VACATION_ROW['По']} "
                         f"вне горизонта {since}…{until} либо С > По")
    ws.append([tab, VACATION_ROW["С"], VACATION_ROW["По"]])
    wb.save(str(book))
    bk.freeze_office_file(book)


def new_rule_row(registry_rev: str, anchor_row: int) -> dict:
    """Запись формы для VN-027: шаблона нет (template null + gap); машинная
    посадка — категория C."""
    # Параметров у записи нет: у правила числа нет вовсе; имена параметров
    # (если семья их объявит) — словарь семьи каталога, а семьи ещё нет.
    return {
        "id": NEW_RULE, "title": NEW_TITLE, "statement": NEW_STATEMENT,
        "sources": [{"sid": bk.SID_XLSX,
                     "anchor": f"лист «{bk.XLSX_SHEET}», строка {anchor_row}"}],
        "template": None, "gap": GAP_WORDS,
        "machine": {"kind": "HARD",
                    "template_fit": {"category": "C", "gap": GAP_WORDS,
                                     "registry_rev": registry_rev}},
    }


def derive_form(h0_form: dict) -> dict:
    """Форма H0-C: та же форма плюс правило VN-027 после VN-060."""
    form = copy.deepcopy(h0_form)
    form["customer"] = CUSTOMER
    hard = form.setdefault("rostering", {}).get("hard") or []
    idx = next((i for i, r in enumerate(hard) if r.get("id") == AFTER_RULE), None)
    if idx is None:
        raise SystemExit(f"в форме H0 нет правила {AFTER_RULE}")
    after = hard[idx]
    row = _row_of(after["sources"][0]["anchor"]) + 1
    rev = str(form.get("registry_rev") or "")
    hard.insert(idx + 1, new_rule_row(rev, row))
    form["rostering"]["hard"] = hard
    meta = form.setdefault("meta", {})
    meta["customer"] = CUSTOMER
    meta["note"] = (f"ИДАК H0-C (2026-09-09, вариант C): форма H0 плюс правило "
                    f"{NEW_RULE} «{NEW_TITLE}» без семьи в каталоге — посадка C, "
                    f"заявка в каталог, новый шаблон по слову владельца. "
                    + str(meta.get("note") or ""))
    for s in form.get("sources") or []:
        s["file"] = RENAMES.get(s.get("file"), s.get("file"))
    return form


def etalon_rule(form: dict, amap: dict, *, phase: int,
                family: str | None = None, params: dict | None = None,
                registry_rev: str | None = None) -> dict:
    """Правило VN-027 эталона ВМП: фаза 1 — категория C с пробелом; фаза 2
    — категория A с семьёй каталога и её параметрами ({имя: роль}):
    значение и цитата — по роли из формулировки."""
    row = next(r for r in form["rostering"]["hard"] if r["id"] == NEW_RULE)
    rule = {"id": NEW_RULE, "title": NEW_TITLE, "statement": NEW_STATEMENT,
            "zone": "ROSTERING", "kind": "HARD",
            "sources": copy.deepcopy(amap[NEW_RULE])}
    rev = registry_rev or row["machine"]["template_fit"]["registry_rev"]
    if phase == 1:
        rule["template_fit"] = {"category": "C", "registry_rev": rev,
                                "gap": GAP_WORDS}
    else:
        if not family or params is None:
            raise SystemExit("фаза 2: нужны семья каталога и роли её параметров")
        rule["template_fit"] = {"category": "A", "registry_rev": rev,
                                "family": family,
                                "params": {name: dict(PARAM_ROLES[role])
                                           for name, role in params.items()}}
    return rule


def derive_etalon(h0_etalon: dict, form: dict, amap: dict, *, phase: int,
                  family: str | None = None, params: dict | None = None,
                  registry_rev: str | None = None) -> dict:
    """Эталон ВМП H0-C: правила H0 буква в букву плюс VN-027 после VN-060."""
    et = copy.deepcopy(h0_etalon)
    rules = et.get("rules") or []
    idx = next((i for i, r in enumerate(rules) if r.get("id") == AFTER_RULE), None)
    if idx is None:
        raise SystemExit(f"в эталоне H0 нет правила {AFTER_RULE}")
    rules.insert(idx + 1, etalon_rule(form, amap, phase=phase, family=family,
                                      params=params, registry_rev=registry_rev))
    # Источники правил — по карте якорей ЭТОГО комплекта: строки листа от
    # вставленной и ниже опустились на одну, якоря H0 у них не годятся
    # (гейт эквивалентности сопоставляет правила по якорям).
    for r in rules:
        if isinstance(r, dict) and r.get("id") in amap:
            r["sources"] = copy.deepcopy(amap[r["id"]])
    et["rules"] = rules
    et["questions"] = []
    meta = et.setdefault("meta", {})
    meta["customer"] = CUSTOMER
    meta["note"] = (form.get("meta") or {}).get("note")
    et["sources"] = copy.deepcopy(form.get("sources") or [])
    return et


def expected_anchors(h0_map: dict, inserted_row: int) -> dict:
    """Карта якорей H0-C из карты H0: строки листа «VMP-H0-2» от вставленной
    и ниже опускаются на одну; VN-027 — вставленная строка; docx и jpg —
    без изменений."""
    out: dict = {}
    for rid, anchors in h0_map.items():
        if rid.startswith("_"):
            continue
        new = []
        for a in anchors:
            a = dict(a)
            if a.get("sid") == bk.SID_XLSX:
                row = _row_of(a["anchor"])
                if row >= inserted_row:
                    a["anchor"] = _ROW_RE.sub(f"строка {row + 1}", a["anchor"])
            new.append(a)
        out[rid] = new
    out[NEW_RULE] = [{"sid": bk.SID_XLSX,
                      "anchor": f"лист «{bk.XLSX_SHEET}», строка {inserted_row}"}]
    return out


def bind_roles(family: str, names: list, mapping: dict | None) -> dict:
    """Связать параметры семьи каталога с ролями формулировки VN-027.

    У правила числа нет, роль одна — unavailable. Семья без параметров
    законна (как calendar_windows и layover_penalty): тогда посадка A несёт
    пустой перечень, и ключи --param запрещены. Один параметр без ключей —
    роль unavailable; иначе каждый параметр семьи назван ровно один раз
    ключом --param имя=роль (закон CDPM «полнота параметров семьи»:
    незаданный параметр — вопрос анкеты, а не посадка), роли не
    повторяются, роль unavailable обязательна. Отказ — словами."""
    names = [str(n) for n in names]
    mapping = {str(k): str(v) for k, v in (mapping or {}).items()}
    if not names:
        if mapping:
            raise SystemExit(f"у семьи {family!r} нет параметров, а ключи "
                             f"--param даны: {mapping}")
        return {}
    if not mapping and len(names) == 1:
        mapping = {names[0]: MAIN_ROLE}
    bad = [n for n in mapping if n not in names]
    if bad:
        raise SystemExit(f"параметров {bad} у семьи {family!r} нет; "
                         f"параметры семьи: {names}")
    unknown = sorted(set(mapping.values()) - set(PARAM_ROLES))
    if unknown:
        raise SystemExit(f"ролей {unknown} нет; роли: {sorted(PARAM_ROLES)}")
    missing = [n for n in names if n not in mapping]
    if missing:
        raise SystemExit(f"у семьи {family!r} параметры {names}: без роли "
                         f"{missing} — задайте --param имя=роль "
                         f"({'|'.join(sorted(PARAM_ROLES))})")
    roles = list(mapping.values())
    if len(set(roles)) != len(roles):
        raise SystemExit(f"роль назначена дважды: {mapping}")
    if MAIN_ROLE not in roles:
        raise SystemExit(f"роль {MAIN_ROLE} обязана быть у одного из параметров")
    return {n: mapping[n] for n in names}


def catalog_family(family: str, mapping: dict | None = None) -> tuple[dict, str, str]:
    """({параметр: роль}, версия каталога, отпечаток) по живому каталогу —
    для фазы 2: семья обязана быть в каталоге со статусом «есть», её
    параметры связываются с ролями формулировки (bind_roles)."""
    import template_catalog as tc
    # Каталог — тот же, что у продукта (bk.catalog_dir: настройка
    # CREW_TEMPLATE_CATALOG, иначе каталог дерева): сухой прогон фазы 2
    # по временному каталогу стенда CR читает проверяемый каталог, а не
    # дерево.
    cat = tc.load(bk.catalog_dir())
    try:
        rec = cat.family(family)
    except Exception:                                   # noqa: BLE001
        rec = None
    if not rec or rec.get("status") != "есть":
        raise SystemExit(f"семьи {family!r} со статусом «есть» в каталоге нет")
    names = [p.get("name") for p in (rec.get("params") or [])]
    return (bind_roles(family, names, mapping), str(cat.version()),
            str(cat.header.get("fingerprint") or ""))


def build(kit_dir: Path = HERE, *, phase: int = 1,
          family: str | None = None, params: dict | None = None,
          same_run: bool = False) -> dict:
    form = derive_form(bk.load_form())
    bad = vf.validate_form(form, strict=False)
    if bad:
        raise SystemExit("ФОРМА H0-C НЕ ЧИСТА:\n  " + "\n  ".join(bad))
    if same_run and phase != 2:
        raise SystemExit("--same-run имеет смысл только у фазы 2")
    # Снимок формы фазы 1 ДО правок фазы 2: при --same-run документы и
    # книга строятся из него (байт в байт как у заморозки прогона фазы 1),
    # а эталон и ожидания — из формы фазы 2.
    doc_form = copy.deepcopy(form)
    if same_run:
        # Шапка документа несёт «База шаблонов: <registry_rev>»; у
        # заморозки фазы 1 это отпечаток прежнего каталога — берём его
        # из формы прежнего набора на диске, иначе документы разойдутся
        # с заморозкой и паспорт раннера остановит продолжение.
        # Источник отпечатка — манифест ПРЕЖНЕГО набора на диске: у фазы 1
        # это отпечаток её каталога; у прежней сборки --same-run — поле
        # same_run.docs_registry_rev (иначе вторая сборка --same-run взяла
        # бы отпечаток фазы 2 и документы разошлись бы с заморозкой —
        # замер 17.09 01:53).
        prev_manifest = kit_dir / "kit-manifest.json"
        if not prev_manifest.is_file():
            raise SystemExit("--same-run: нет прежнего набора (kit-manifest.json)")
        pm = json.loads(prev_manifest.read_text(encoding="utf-8"))
        prev_rev = (pm.get("same_run") or {}).get("docs_registry_rev")
        if not prev_rev:
            if int(pm.get("phase") or 0) != 1:
                raise SystemExit("--same-run: прежний набор не фаза 1 и без "
                                 "same_run.docs_registry_rev — откуда брать "
                                 "отпечаток документов, неизвестно")
            prev_rev = (pm.get("catalog") or {}).get("fingerprint")
        if not prev_rev:
            raise SystemExit("--same-run: в прежнем манифесте нет отпечатка каталога")
        doc_form["registry_rev"] = prev_rev
        same_run_block = {"docs_registry_rev": prev_rev,
                          "note": "документы и книга — байт в байт фазы 1 "
                                  "(продолжение того же прогона)"}
    # Фаза 2: каталог судится ДО записи документов — иначе отказ (семьи
    # «есть» нет, роли не связались) оставлял бы комплект переписанным по
    # отметкам времени docx/xlsx при том же содержании (сухой прогон
    # 09.09.2026: неудачный запуск пачкал комплект).
    roles = None
    rev = None
    catalog_block = None
    if phase == 2:
        roles, version, fingerprint = catalog_family(family or "", params)
        catalog_block = {"version": version, "fingerprint": fingerprint}
        rev = fingerprint
        # Посадка правила — ОДНИМ носителем: строка формы (из неё строятся
        # документы и их встроенная каноническая форма) и эталон обязаны
        # говорить одно. Сухой и живой прогоны 09.09.2026: сборщик фазы
        # 2 обновлял эталон («A, семья, 3 и 1»), а текст формы в
        # документах оставался «C, семьи нет» — поклаузная сверка CDPM
        # давала «расхождений 1» на каждом прогоне.
        import template_catalog as tc
        units = {str(p.get("name")): p.get("unit")
                 for p in (tc.load(bk.catalog_dir()).family(family or "")
                           .get("params") or [])}
        row = next(r for r in form["rostering"]["hard"] if r["id"] == NEW_RULE)
        row["template"] = {"family": family, "category": "A"}
        row.pop("gap", None)
        row.pop("params", None)
        if roles:
            row["params"] = [{"name": name, "value": PARAM_ROLES[role]["value"],
                              "quote": PARAM_ROLES[role]["quote"],
                              "unit": units.get(name)}
                             for name, role in roles.items()]
        row["machine"] = {"kind": "HARD", "template_fit": {"registry_rev": rev}}
        meta = form.setdefault("meta", {})
        meta["note"] = (f"Фаза 2: семья {family} выпущена каталогом {version}, "
                        f"правило {NEW_RULE} посажено (категория A). "
                        + ("Продолжение того же прогона фазы 1: документы и "
                           "книга данных — фазы 1, байт в байт. " if same_run
                           else "")
                        + str(meta.get("note") or ""))
    docs = kit_dir / "docs"
    etalon = kit_dir / "etalon"
    data = kit_dir / "data"
    for d in (docs, etalon, data):
        d.mkdir(parents=True, exist_ok=True)
    by_file = bk.lines_by_file(doc_form if same_run else form)
    bk.build_docx(docs / DOCX_NAME, by_file["docx"])
    bk.build_xlsx(docs / XLSX_NAME, by_file["xlsx"])
    jpg_lines = bk.build_jpg(docs / JPG_NAME, by_file["jpg"])
    frozen = bk.freeze_texts(docs / DOCX_NAME, docs / XLSX_NAME)
    amap, problems = bk.build_anchors_map(by_file, frozen, jpg_lines)
    n_rows = sum(len(rows) for _p, _s, rows in vf.form_sections(form))
    n_map = sum(1 for k in amap if not k.startswith("_"))
    if n_map != n_rows:
        problems.append(f"в карте {n_map} записей, в форме {n_rows}")
    h0_map = json.loads((H0 / "etalon" / "anchors-map.json").read_text(encoding="utf-8"))
    inserted_row = _row_of(next(r for r in form["rostering"]["hard"]
                                if r["id"] == NEW_RULE)["sources"][0]["anchor"])
    want = expected_anchors(h0_map, inserted_row)
    for rid, anchors in amap.items():
        if rid.startswith("_"):
            continue
        if want.get(rid) != anchors:
            problems.append(f"якорь {rid}: H0-C {anchors} ≠ ожидание {want.get(rid)}")
    if set(want) != {k for k in amap if not k.startswith("_")}:
        problems.append(f"состав записей: {sorted(set(want) ^ {k for k in amap if not k.startswith('_')})}")
    if problems:
        raise SystemExit("САМОПРОВЕРКА КРАСНАЯ:\n  " + "\n  ".join(problems))
    src = H0 / "data" / DATA_NAME
    if not src.is_file():
        raise SystemExit(f"нет книги данных H0: {src}")
    shutil.copyfile(src, data / DATA_NAME)
    add_vacation_row(data / DATA_NAME)
    # Отпечатки источников — носителем базового сборщика (одна формула на
    # все сборщики формы H0): по факту ТОЛЬКО ЧТО собранных файлов, файла
    # нет — отказ словами.
    sources = bk.stamp_sources(form, docs)
    h0_manifest = json.loads((H0 / "kit-manifest.json").read_text(encoding="utf-8"))
    if catalog_block is None:
        catalog_block = copy.deepcopy(h0_manifest.get("catalog"))
    et = derive_etalon(json.loads((H0 / "etalon" / "VMP-H0.json").read_text(encoding="utf-8")),
                       form, amap, phase=phase, family=family, params=roles,
                       registry_rev=rev)
    # Обе проекции эталона — об одних и тех же файлах (как в базовом
    # сборщике): состав источников ВМП обязан совпасть с формой.
    check_sources(sources, et)

    def _dump(path: Path, obj) -> None:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")

    _dump(etalon / "VMP-H0C-form.json", form)
    _dump(etalon / "VMP-H0C.json", et)
    # Вопросы истории — по виду чтения семей эталона (каталог 0.5.0);
    # фаза 1: семьи в каталоге ещё нет, её строка идёт блоком «логика».
    EXPECT_PHASE_2["questions"] = bk.history_question_ids(et)
    EXPECT_PHASE_1["questions"] = bk.history_question_ids(
        et, without_family=family if phase == 2 else None)
    # Необязательные вопросы модели по строке VN-027 (17.09.2026): логика
    # «свод против пересказа» и «величина без машинного поля» (слово «одна»
    # в тексте пробела). Их вправе задать, а вправе и не задать; ответы —
    # в машинной книге ниже теми же идентификаторами.
    optional_questions = [
        f"Q-2-лог-R-S2-лист-«{bk.XLSX_SHEET}»-строка-{inserted_row}",
        f"Q-2-balance-S2-лист-«{bk.XLSX_SHEET}»-строка-{inserted_row}-number_word",
    ]
    EXPECT_PHASE_1["questions_optional"] = list(optional_questions)
    EXPECT_PHASE_2["questions_optional"] = list(optional_questions)
    _dump(etalon / "anchors-map.json", amap)
    _dump(etalon / "jpg-lines.json", jpg_lines)
    _dump(etalon / "ANSWER-BOOK-MACHINE.json", {
        "form_version": 1, "customer": CUSTOMER,
        "_comment": ("Набор «ИД H0-C»: законный вопрос — только линия данных "
                     "об истории (1.17.2): истории в книге нет — «продолжить с "
                     "нулевыми остатками». Вопросов по VN-027 быть не должно: "
                     "правило без семьи — пробел покрытия и заявка в каталог, "
                     "а не анкета. Дефект линии данных (09.09.2026, прогон "
                     "CDPM run-0096: вопрос «нет данных» у правила посадки C) "
                     "исправлен CDPM в тот же день (1316023b, слово владельца "
                     "«Пункт 2 - согласен»); обход «а» по префиксу «нет "
                     "данных» снят — любой вопрос сверх истории снова красный "
                     "отчёт раннера."),
        # 17.09.2026 01:39 (фаза 1 на А′): шаг «логика правил» извлечения
        # иногда (1 прогон из 3) спрашивает о VN-027 «свод против пересказа»
        # — пересказ модели добавляет «или недоступности», которого в
        # своде нет. Верно чтение свода — «а». Вопрос не о данных и не об
        # истории, поэтому книга отвечает точным идентификатором строки.
        # 17.09 01:43 (продолжение на Б′): шаг «баланс величин» спросил о
        # слове «одна» из текста пробела строки VN-027 («ни одна семья») —
        # «величина без машинного поля»; это не норма, не ставка и не
        # параметр — справочная величина, «г».
        "exact": {optional_questions[0]: "а", optional_questions[1]: "г"},
        "prefix": bk.history_book_prefix(), "regex": []})

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(REPO)).replace("\\", "/")
        except ValueError:
            return str(p).replace("\\", "/")

    expect = copy.deepcopy(EXPECT_PHASE_2 if phase == 2 else EXPECT_PHASE_1)
    if phase == 2:
        expect["rule_landing"] = {NEW_RULE: "A"}
        expect["family"] = {"id": family, "roles": roles,
                            "params": {n: PARAM_ROLES[r]["value"]
                                       for n, r in roles.items()}}
        # закон CDPM «полнота параметров семьи»: все параметры семьи заданы
        expect["family_complete"] = {family: list(roles)}
    manifest = {
        "customer": CUSTOMER,
        "catalog": catalog_block,
        "stage": (f"ИД H0-C, фаза {phase} ("
                  + ("каталог без семьи: заявка" if phase == 1
                     else f"каталог с семьёй {family}: полный прогон") + ")"),
        "phase": phase,
        "data_class": "synthetic",
        "llm_scope": "any",
        "llm_scope_basis": ("набор синтетический: вымышленная авиакомпания, "
                            "генератор в репозитории (build_kit_h0c.py поверх "
                            "build_kit_h0.py); решение владельца 03.09.2026 — "
                            "для синтетических и открытых данных любые модели, "
                            "включая облачные"),
        "rules_files": [f"docs/{DOCX_NAME}", f"docs/{XLSX_NAME}", f"docs/{JPG_NAME}"],
        "data_files": [f"data/{DATA_NAME}"],
        "answer_book": "etalon/ANSWER-BOOK-MACHINE.json",
        # Раскладка моделей — путь ОТ КОРНЯ репозитория (паспорт прогона,
        # 10.09.2026): копия набора вне open-bench соседа
        # «../polyaris-h0» не имеет — прежний путь ронял показ фазы 1
        # 09.09.2026. Прежний ключ остаётся для читателей старых наборов.
        "agents_layout_from_repo": bk.LAYOUT_FROM_REPO,
        "agents_layout": "../polyaris-h0/agents-layout-h0.json",
        "strict_questions": False,
        "etalon_vmp": _rel(etalon / "VMP-H0C.json"),
        "etalon_form": _rel(etalon / "VMP-H0C-form.json"),
        "expect": expect,
        "expect_phases": {"1": EXPECT_PHASE_1, "2": EXPECT_PHASE_2},
        **({"same_run": same_run_block} if same_run else {}),
        "note": (f"ИДАК H0-C = H0 плюс правило {NEW_RULE} «{NEW_TITLE}» без семьи "
                 f"в каталоге 0.2.0 (владелец 09.09.2026, вариант C). Фаза 1: "
                 f"посадка C, заявка в каталог, стоп; слово владельца → CR "
                 f"конвейера пишет семью и исполнитель → перевод записи → "
                 f"выпуск; фаза 2: полный прогон, ВМП = H0 + {NEW_RULE}, "
                 f"COSMOS без нарушений (строк справочно 11 и 14). Книга "
                 f"данных — {DATA_NAME} набора H0 плюс одна строка отпуска "
                 f"({VACATION_ROW['Табельный номер']}, {VACATION_ROW['С']}…"
                 f"{VACATION_ROW['По']}; слово владельца 16.09.2026)."
                 + (" Продолжение того же прогона фазы 1 (--same-run): "
                    "документы и книга — фазы 1." if same_run else "")),
    }
    _dump(kit_dir / "kit-manifest.json", manifest)
    res = {"docx": len(by_file["docx"]), "xlsx": len(by_file["xlsx"]),
           "jpg": len(by_file["jpg"]), "rows": n_rows, "map": n_map,
           "rules": len(et["rules"]), "objectives": len(et["objectives"]),
           "phase": phase, "family": family, "params": roles}
    res["same_run"] = same_run
    print(f"ИД H0-C собран (фаза {phase}): docx {res['docx']} абзацев, xlsx "
          f"{res['xlsx']} строк, jpg {res['jpg']} абзацев; записей формы "
          f"{res['rows']} (H0 плюс {NEW_RULE}), карта якорей {res['map']}; "
          f"эталон: правил {res['rules']}, целевых {res['objectives']}"
          + (f"; семья {family}, параметры {roles}" if phase == 2 else "")
          + f"; книга данных: H0 плюс отпуск {VACATION_ROW['Табельный номер']} "
            f"{VACATION_ROW['С']}…{VACATION_ROW['По']}"
          + ("; --same-run: документы и книга фазы 1" if same_run else "")
          + "; самопроверка чиста")
    print("отпечатки источников обновлены по факту сборки (форма и ВМП): "
          + ", ".join(f"{s['sid']} {s['file']} {s['sha256'][:12]}…"
                      for s in sources))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", type=int, choices=(1, 2), default=1)
    ap.add_argument("--family", default=None,
                    help="фаза 2: id семьи каталога, покрывшей VN-027")
    ap.add_argument("--param", action="append", default=[], metavar="ИМЯ=РОЛЬ",
                    help="фаза 2: параметр семьи каталога → роль по формулировке "
                         "VN-027 (единственная роль — unavailable); при семье "
                         "без параметров или с одним параметром не нужен")
    ap.add_argument("--same-run", action="store_true",
                    help="фаза 2 как продолжение ТОГО ЖЕ прогона фазы 1: документы "
                         "и книга данных — байт в байт фазы 1, эталон и ожидания "
                         "— фазы 2")
    a = ap.parse_args(argv)
    mapping = {}
    for item in a.param:
        name, sep, role = item.partition("=")
        if not sep or not name.strip() or not role.strip():
            ap.error(f"--param ждёт ИМЯ=РОЛЬ, получено {item!r}")
        mapping[name.strip()] = role.strip()
    build(phase=a.phase, family=a.family, params=mapping or None,
          same_run=a.same_run)
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
