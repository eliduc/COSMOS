# -*- coding: utf-8 -*-
"""Сборщик набора «ИД H0-B» — вариант B (поручение владельца 09.09.2026):
шаблон с двумя параметрами, один задан документами, второго нет.

Комплект выводится из формы H0 (open-bench/polyaris-h0/etalon/
VMP-H0-form.json) тем же генератором build_kit_h0: единственное отличие
— в части 2 а) (лист «VMP-H0-2») НЕТ строки VN-024 «Отдых вне базы».
Семья каталога rest_floors несёт два параметра (min_rest_home,
min_rest_outstation); документы задают только первый.

Ожидаемое поведение конвейера — в EXPECTED.md и в ключе «expect»
манифеста (раннер run_local_pipeline судит по нему):
  - обработчик CDPM видит семью с двумя параметрами и одним заданным и
    задаёт вопрос анкеты авиакомпании о поле отдыха вне базы (id вопроса
    — по соглашению CDPM: PARAM_QUESTION; книга ответов ловит его и по
    префиксу);
  - авиакомпания отвечает «Минимальный отдых между сменами вне базы —
    10:00 (600 минут)»; после ответа ВМП по содержанию равна ВМП H0
    (семья rest_floors с обоими параметрами), пакет — готовность «в»,
    прогон COSMOS даёт числа H0;
  - вопрос линии данных об истории (1.17.2) — как у H0-К: «б».

Запуск: python open-bench/polyaris-h0b/build_kit_h0b.py
"""
from __future__ import annotations

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

CUSTOMER = "Полярис-H0-B (ВМП H0 с неполным шаблоном отдыха)"
DOCX_NAME = "ID-H0B-VMP-1.docx"
XLSX_NAME = "ID-H0B-VMP-2.xlsx"
JPG_NAME = "ID-H0B-VMP-3.jpg"
DATA_NAME = bk.DATA_NAME            # книга данных — та же, что у H0
RENAMES = {bk.DOCX_NAME: DOCX_NAME, bk.XLSX_NAME: XLSX_NAME,
           bk.JPG_NAME: JPG_NAME}
#: Правило, которого нет в документах, — второй параметр семьи полов отдыха.
REMOVED_RULE = "VN-024"
FAMILY = "rest_floors"
PRESENT_PARAM = "min_rest_home"
MISSING_PARAM = "min_rest_outstation"
#: Вопрос анкеты о недостающем параметре семьи — id по соглашению CDPM
#: (предложение COSMOS 09.09.2026); книга ответов ловит его и префиксом.
PARAM_QUESTION = f"Q-3b-параметр-{FAMILY}-{MISSING_PARAM}"
PARAM_QUESTION_PREFIX = f"Q-3b-параметр-{FAMILY}"
PARAM_ANSWER = ("Минимальный отдых между сменами вне базы — 10:00 (600 минут). "
                "Это второй параметр того же правила о полах отдыха: дома — "
                "12:00 (720 минут), вне базы — 10:00 (600 минут).")
#: Правило-носитель семьи в документах: после ответа несёт оба параметра.
CARRIER_RULE = "VN-023"
#: Цитата второго параметра в эталоне — фраза ответа авиакомпании.
ANSWER_QUOTE = "Минимальный отдых между сменами вне базы — 10:00 (600 минут)"
EXPECT = {
    "_comment": ("Ожидание прогона H0-B (владелец 09.09.2026, вариант B): у "
                 "семьи rest_floors два параметра, документы задают один "
                 "(min_rest_home); обработчик обязан спросить второй "
                 "(min_rest_outstation) анкетой; после ответа всё садится "
                 "на шаблон — ВМП по содержанию равна H0 (15 правил: ответ "
                 "дополняет VN-023 второй ячейкой, отдельного VN-024 не "
                 "возникает — 16.09.2026, доказано ходом применения "
                 "ответа; обе целевые), готовность «в», числа COSMOS = "
                 "H0. Раннер "
                 "сверяет ключи ниже с пакетом."),
    "questions": [PARAM_QUESTION],   # + вопросы истории по виду — при сборке
    "_history_question": ("вопрос линии данных 1.17.2 (решение владельца "
                          "05.09.2026): у правил ростеринга есть остатки "
                          "истории, а истории в книге нет — «б»"),
    # Лестница готовности как у H0: обе части несут критерии оптимизации
    # (базовый прогон 09.09.2026: состояние столбцов «optimization»).
    "readiness_ladder": {"pairing": "optimization",
                         "rostering": "optimization"},
    "objectives": 2,
    "family_complete": {FAMILY: [PRESENT_PARAM, MISSING_PARAM]},
    "cosmos_numbers": {"pairing": "9/0", "rostering": "12/0",
                       "_note": "как у H0 по чтению ИДАК каналом api"},
    # Сшивка записи о прогоне — одна на все наборы формы H0 (шаг 2
    # проекта «Запись о прогоне»): ожидание берётся у базового набора,
    # а не переписывается здесь.
    # Ожидаемые вердикты стадий (11.09.2026): извлечение НЕ эквивалентно
    # ДО ОТВЕТОВ — в этом весь замысел варианта B, и краснеть тут нечему.
    "stage_verdicts": bk.stage_verdicts(
        "ожидаемые вердикты стадий варианта B. Извлечение НЕ "
        "ЭКВИВАЛЕНТНО ДО ОТВЕТОВ — в этом весь замысел набора: документы "
        "задают один параметр семьи rest_floors, второй приходит ответом "
        "анкеты, и до ответа ВМП эталону не равна; красная строка тут "
        "была бы неправдой. После ответов и в пакете — эквивалентно. "
        "Вердикты стадий COSMOS судятся только с машинным отчётом "
        "нажатий (--cosmos-press).",
        extract="NOT_EQUIVALENT"),
    # Вердикт гейта непротиворечивости в манифесте пакета (11.09.2026):
    # слово одно на все наборы формы H0 — берётся у базового набора.
    # Второй параметр семьи приходит ответом анкеты, но противоречия в
    # этом нет: значения разные у РАЗНЫХ параметров.
    "consistency": bk.CONSISTENCY,
    "record_stitched": copy.deepcopy(bk.RECORD_STITCHED),
    # Золотым прогон называет только владелец (шаг 3 проекта, решение
    # владельца 6.3): ожидание тоже одно на все наборы формы H0.
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


def derive_form(h0_form: dict) -> dict:
    """Форма H0-B: та же форма без правила VN-024 (второй параметр семьи
    полов отдыха документами не задан)."""
    form = copy.deepcopy(h0_form)
    form["customer"] = CUSTOMER
    hard = form.setdefault("rostering", {}).get("hard") or []
    kept = [r for r in hard if r.get("id") != REMOVED_RULE]
    if len(kept) != len(hard) - 1:
        raise SystemExit(f"в форме H0 нет правила {REMOVED_RULE}")
    form["rostering"]["hard"] = kept
    meta = form.setdefault("meta", {})
    meta["customer"] = CUSTOMER
    meta["note"] = (f"ИДАК H0-B (2026-09-09, вариант B): форма H0 без правила "
                    f"{REMOVED_RULE} «Отдых вне базы» — у семьи {FAMILY} "
                    f"документами задан один параметр из двух; второй "
                    f"({MISSING_PARAM}) ожидается вопросом анкеты и ответом "
                    f"авиакомпании. " + str(meta.get("note") or ""))
    for s in form.get("sources") or []:
        s["file"] = RENAMES.get(s.get("file"), s.get("file"))
    return form


def reanchor(rules: list, amap: dict) -> None:
    """Источники правил эталона — по карте якорей ЭТОГО комплекта: строки
    листа ниже снятой поднялись на одну, и якоря H0 у них не годятся
    (базовый прогон 09.09.2026: гейт эквивалентности сопоставляет правила
    по якорям — VN-060 и VN-050 считались непокрытыми и «сверх эталона»)."""
    for r in rules:
        if isinstance(r, dict) and r.get("id") in amap:
            r["sources"] = copy.deepcopy(amap[r["id"]])


def derive_etalon(h0_etalon: dict, form: dict, amap: dict | None = None) -> dict:
    """Эталон ВМП H0-B после ответа — по закону CDPM «полнота параметров
    семьи» (09.09.2026): отдельного правила VN-024 нет, правило-носитель
    VN-023 несёт оба параметра семьи; второй — по ответу анкеты (цитата =
    ответ, признак ответа — вопрос). «Буква в букву» с H0 — по машинному
    содержанию семьи, не по числу правил-носителей."""
    et = copy.deepcopy(h0_etalon)
    rules = [r for r in (et.get("rules") or [])
             if not (isinstance(r, dict) and r.get("id") == REMOVED_RULE)]
    if len(rules) != len(et.get("rules") or []) - 1:
        raise SystemExit(f"в эталоне H0 нет правила {REMOVED_RULE}")
    carrier = next((r for r in rules if r.get("id") == CARRIER_RULE), None)
    if carrier is None:
        raise SystemExit(f"в эталоне H0 нет правила-носителя {CARRIER_RULE}")
    fit = carrier.setdefault("template_fit", {})
    if fit.get("family") != FAMILY or PRESENT_PARAM not in (fit.get("params") or {}):
        raise SystemExit(f"{CARRIER_RULE}: ожидалась семья {FAMILY} с {PRESENT_PARAM}")
    fit["params"][MISSING_PARAM] = {"value": 600, "quote": ANSWER_QUOTE,
                                    "answer": PARAM_QUESTION}
    if amap:
        reanchor(rules, amap)
    et["rules"] = rules
    et["questions"] = []
    meta = et.setdefault("meta", {})
    meta["customer"] = CUSTOMER
    meta["note"] = (form.get("meta") or {}).get("note")
    et["sources"] = copy.deepcopy(form.get("sources") or [])
    return et


def expected_anchors(h0_map: dict) -> dict:
    """Карта якорей H0-B из карты H0: снятая строка исчезает, строки листа
    «VMP-H0-2» ниже неё поднимаются на одну; docx и jpg — без изменений."""
    removed_row = _row_of(h0_map[REMOVED_RULE][0]["anchor"])
    out: dict = {}
    for rid, anchors in h0_map.items():
        if rid.startswith("_") or rid == REMOVED_RULE:
            continue
        new = []
        for a in anchors:
            a = dict(a)
            if a.get("sid") == bk.SID_XLSX:
                row = _row_of(a["anchor"])
                if row > removed_row:
                    a["anchor"] = _ROW_RE.sub(f"строка {row - 1}", a["anchor"])
            new.append(a)
        out[rid] = new
    return out


def build(kit_dir: Path = HERE) -> dict:
    form = derive_form(bk.load_form())
    bad = vf.validate_form(form, strict=False)
    if bad:
        raise SystemExit("ФОРМА H0-B НЕ ЧИСТА:\n  " + "\n  ".join(bad))
    docs = kit_dir / "docs"
    etalon = kit_dir / "etalon"
    data = kit_dir / "data"
    for d in (docs, etalon, data):
        d.mkdir(parents=True, exist_ok=True)
    by_file = bk.lines_by_file(form)
    bk.build_docx(docs / DOCX_NAME, by_file["docx"])
    bk.build_xlsx(docs / XLSX_NAME, by_file["xlsx"])
    jpg_lines = bk.build_jpg(docs / JPG_NAME, by_file["jpg"])
    frozen = bk.freeze_texts(docs / DOCX_NAME, docs / XLSX_NAME)
    amap, problems = bk.build_anchors_map(by_file, frozen, jpg_lines)
    n_rows = sum(len(rows) for _p, _s, rows in vf.form_sections(form))
    n_map = sum(1 for k in amap if not k.startswith("_"))
    if n_map != n_rows:
        problems.append(f"в карте {n_map} записей, в форме {n_rows}")
    # Якоря H0-B обязаны быть якорями H0 без снятой строки, со сдвигом
    # строк листа под ней на одну вверх (docx и jpg не изменились).
    h0_map = json.loads((H0 / "etalon" / "anchors-map.json").read_text(encoding="utf-8"))
    want = expected_anchors(h0_map)
    for rid, anchors in amap.items():
        if rid.startswith("_"):
            continue
        if want.get(rid) != anchors:
            problems.append(f"якорь {rid}: H0-B {anchors} ≠ ожидание {want.get(rid)}")
    if set(want) != {k for k in amap if not k.startswith("_")}:
        problems.append(f"состав записей: {sorted(set(want) ^ {k for k in amap if not k.startswith('_')})}")
    if problems:
        raise SystemExit("САМОПРОВЕРКА КРАСНАЯ:\n  " + "\n  ".join(problems))
    src = H0 / "data" / DATA_NAME
    if not src.is_file():
        raise SystemExit(f"нет книги данных H0: {src}")
    shutil.copyfile(src, data / DATA_NAME)
    # Отпечатки источников — носителем базового сборщика (одна формула на
    # все сборщики формы H0): по факту ТОЛЬКО ЧТО собранных файлов, файла
    # нет — отказ словами.
    sources = bk.stamp_sources(form, docs)
    et = derive_etalon(json.loads((H0 / "etalon" / "VMP-H0.json").read_text(encoding="utf-8")),
                       form, amap)
    # Обе проекции эталона — об одних и тех же файлах (как в базовом
    # сборщике): состав источников ВМП обязан совпасть с формой, иначе
    # эталон называет файлы, которых в комплекте нет.
    check_sources(sources, et)

    def _dump(path: Path, obj) -> None:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")

    _dump(etalon / "VMP-H0B-form.json", form)
    _dump(etalon / "VMP-H0B.json", et)
    # Вопросы истории — по виду чтения семей эталона (каталог 0.5.0)
    EXPECT["questions"] = [PARAM_QUESTION] + bk.history_question_ids(et)
    _dump(etalon / "anchors-map.json", amap)
    _dump(etalon / "jpg-lines.json", jpg_lines)
    _dump(etalon / "ANSWER-BOOK-MACHINE.json", {
        "form_version": 1, "customer": CUSTOMER,
        "_comment": ("Набор «ИД H0-B»: законные вопросы — о недостающем "
                     f"параметре семьи {FAMILY} ({MISSING_PARAM}; id по "
                     "соглашению CDPM, ловится и префиксом) и вопрос линии "
                     "данных об истории (1.17.2): истории в книге нет — "
                     "«продолжить с нулевыми остатками». Любой другой вопрос "
                     "= дефект конвейера (несопоставленный вопрос — красный "
                     "отчёт). Сопоставление: exact по id, затем prefix."),
        "exact": {PARAM_QUESTION: PARAM_ANSWER},
        # Префикс только по семье: общий «Q-3b-параметр-» ловил бы вопросы
        # о параметрах ЧУЖИХ семей и отвечал бы им текстом про отдых
        # (замечание CDPM 09.09.2026, run-0098).
        "prefix": {PARAM_QUESTION_PREFIX: PARAM_ANSWER, **bk.history_book_prefix()},
        "regex": []})

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(REPO)).replace("\\", "/")
        except ValueError:
            return str(p).replace("\\", "/")

    h0_manifest = json.loads((H0 / "kit-manifest.json").read_text(encoding="utf-8"))
    manifest = {
        "customer": CUSTOMER,
        "catalog": copy.deepcopy(h0_manifest.get("catalog")),
        "stage": "ИД H0-B (форма H0 без второго параметра семьи полов отдыха)",
        "data_class": "synthetic",
        "llm_scope": "any",
        "llm_scope_basis": ("набор синтетический: вымышленная авиакомпания, "
                            "генератор в репозитории (build_kit_h0b.py поверх "
                            "build_kit_h0.py); решение владельца 03.09.2026 — "
                            "для синтетических и открытых данных любые модели, "
                            "включая облачные"),
        "rules_files": [f"docs/{DOCX_NAME}", f"docs/{XLSX_NAME}", f"docs/{JPG_NAME}"],
        "data_files": [f"data/{DATA_NAME}"],
        "answer_book": "etalon/ANSWER-BOOK-MACHINE.json",
        # Раскладка моделей — путь ОТ КОРНЯ репозитория (паспорт прогона,
        # 10.09.2026): у копии набора, лежащей вне open-bench, соседа
        # «../polyaris-h0» нет, и прежний путь ронял прогон на первой
        # стадии. Прежний ключ остаётся для читателей старых наборов.
        "agents_layout_from_repo": bk.LAYOUT_FROM_REPO,
        "agents_layout": "../polyaris-h0/agents-layout-h0.json",
        "strict_questions": False,
        "etalon_vmp": _rel(etalon / "VMP-H0B.json"),
        "etalon_form": _rel(etalon / "VMP-H0B-form.json"),
        "expect": EXPECT,
        "note": (f"ИДАК H0-B = H0 без правила {REMOVED_RULE} «Отдых вне базы» "
                 f"(владелец 09.09.2026, вариант B): семья {FAMILY} с двумя "
                 f"параметрами, документами задан один — второй обязан "
                 f"прийти вопросом анкеты и ответом авиакомпании; после "
                 f"ответа ВМП по содержанию равна H0. Три файла правил как у "
                 f"H0; книга ответов закрывает вопрос о параметре и вопрос "
                 f"об истории; ожидания — ключ expect и EXPECTED.md. Книга "
                 f"данных — {DATA_NAME} набора H0."),
    }
    _dump(kit_dir / "kit-manifest.json", manifest)
    res = {"docx": len(by_file["docx"]), "xlsx": len(by_file["xlsx"]),
           "jpg": len(by_file["jpg"]), "rows": n_rows, "map": n_map,
           "rules": len(et["rules"]), "objectives": len(et["objectives"])}
    print(f"ИД H0-B собран: docx {res['docx']} абзацев, xlsx {res['xlsx']} строк, "
          f"jpg {res['jpg']} абзацев ({jpg_lines}); записей формы {res['rows']} "
          f"(H0 минус {REMOVED_RULE}), карта якорей {res['map']}; эталон после "
          f"ответа: правил {res['rules']}, целевых {res['objectives']}; "
          f"самопроверка чиста")
    print("отпечатки источников обновлены по факту сборки (форма и ВМП): "
          + ", ".join(f"{s['sid']} {s['file']} {s['sha256'][:12]}…"
                      for s in sources))
    return res


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    build()
