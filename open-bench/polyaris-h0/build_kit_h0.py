# -*- coding: utf-8 -*-
r"""Генератор набора «ИД H0-ВМП» из ФОРМЫ ВМП (решения владельца
2026-09-02: форма — каноническая форма выдачи/приёмки ВМП и эталона).

Источник — etalon/VMP-H0-form.json (проекция эталона ВМП H0 в форму,
выпускается генератором эталона COSMOS вместе с VMP-H0.json). Набор =
форма, разнесённая БУКВА В БУКВУ на три файла — записи формы одной
строкой человеческого текста (cdpm.vmp_form.render_row_line), под
заголовками разделов формы:
  docs/ID-H0-VMP-1.docx  — заголовок ВМП, общий раздел (определения,
                           параметры мира), часть 1 «Пейринг» а) и б);
  docs/ID-H0-VMP-2.xlsx  — часть 2 «Ростеринг» а) и б) (строка листа =
                           заголовок раздела либо запись);
  docs/ID-H0-VMP-3.jpg   — последняя часть: разделы в) обеих частей
                           (критерии оптимизации), изображение текста.
Служебных полей (источники, machine) записи не несут — трассировку
ставит заморозка носителей. Книга ответов пуста. Вторая часть набора —
книга операционных данных data/ID-H0-DATA.xlsx (генератор
build_data_h0.py): источником ПРАВИЛ она не становится, но объявляется
манифестом как файл данных, иначе конвейер идёт без нижнего контура.

Самопроверка: штатная заморозка docx/xlsx и строки jpg → каждая запись
формы присутствует ровно один раз и совпадает с рендером буква в букву;
число записей = число строк формы. Расхождение = отказ сборки.

Отпечатки источников: после сборки документов блок `sources` ОБЕИХ
проекций эталона (формы VMP-H0-form.json и ВМП VMP-H0.json) ставится по
факту собранных файлов — формулой `stamp_sources` этого модуля, одной на
все сборщики формы H0. Без этого эталон называет sha прежней сборки, а
файла с таким отпечатком в комплекте уже нет (дефект 11.09.2026).

Тот же текст — те же байты: docx и xlsx после записи нормализуются
(`freeze_office_file`, волна 16, слово владельца 11.09.2026) — часов
машины ни в записях архива, ни в свойствах документа не остаётся,
поэтому отпечаток источника от пересборки к пересборке не «плывёт».
Носитель ОДИН на все сборщики наборов: вариации зовут его через
build_docx/build_xlsx, книги данных — через build_data_h0.freeze_office.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
#: Настройка продукта «какой каталог шаблонов» — дословно та же формула,
#: что у двери COSMOS (vmp/catalog_access.CATALOG_ENV) и моста CDPM
#: (cdpm/catalog_bridge.catalog_path): сборщики наборов обязаны читать
#: тот же каталог, что и продукт, иначе сухой прогон по временному
#: каталогу (стенд CR, кандидат записи) читает каталог дерева и
#: отказывает «семьи нет», хотя семья в проверяемом каталоге есть.
CATALOG_ENV = "CREW_TEMPLATE_CATALOG"
ETALON = HERE / "etalon"
FORM_H0 = ETALON / "VMP-H0-form.json"
DOCS = HERE / "docs"

#: Раскладка моделей по ролям — ОДИН файл на все наборы формы H0, и
#: манифест называет его путём ОТ КОРНЯ репозитория (ключ
#: `agents_layout_from_repo`, слово владельца 10.09.2026 о параметрах
#: прогона). Прежний путь от папки набора рвался у копии набора,
#: лежащей вне open-bench: соседа «../polyaris-h0» там нет.
LAYOUT_NAME = "agents-layout-h0.json"
LAYOUT_FROM_REPO = str((HERE / LAYOUT_NAME).relative_to(REPO)).replace("\\", "/")

DOCX_NAME = "ID-H0-VMP-1.docx"
XLSX_NAME = "ID-H0-VMP-2.xlsx"
JPG_NAME = "ID-H0-VMP-3.jpg"
DATA_NAME = "ID-H0-DATA.xlsx"      # вторая часть набора: книга данных
SID_DOCX, SID_XLSX, SID_JPG = "S1", "S2", "S3"
XLSX_SHEET = "VMP-H0-2"
#: Шрифты изображения ID-H0-VMP-3.jpg — Arial из шрифтов Windows; выложенный
#: комплект собран ими. Без них Pillow рисует запасным шрифтом, и байты jpg
#: (а с ними отпечаток источника S3) другие — так упало задание Linux в
#: CI № 81 (11.09.2026): содержание то же, файл другой.
JPG_FONTS = (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf")
#: Вопрос линии данных трёх исходов по истории (1.17.2, решение владельца
#: 05.09.2026): у правил ростеринга есть остатки истории, а истории в книге
#: H0 нет — авиакомпания отвечает «б» (продолжить с нулевыми остатками).
#: С каталога 0.5.0 вопросы задаются ПО ВИДУ чтения истории семей записи
#: (`Q-3b-история-<вид>`, cdpm.history_questions): книга ответов отвечает
#: по префиксу, а ожидание набора вычисляется кодом CDPM по эталонному
#: ВМП — тем же, что у панели (history_question_ids), не пишется рукой.
HISTORY_CONTINUE_ZERO = "б"


def history_question_prefix() -> str:
    from cdpm import history_section as hs
    return hs.HISTORY_QUESTION_PREFIX


def history_book_prefix() -> dict:
    """Строка книги ответов: любой вопрос об истории → «б»."""
    return {history_question_prefix(): HISTORY_CONTINUE_ZERO}


def history_question_ids(doc: dict, without_family: str | None = None) -> list:
    """id вопросов об истории, которые задаст панель по ВМП набора: по
    видам чтения истории семей (запись каталога, 0.5.0) — кодом CDPM.
    `without_family` — фаза 1 Н0-C: семьи в каталоге ещё нет, её строка
    зависит от истории по своему блоку логики (блок «логика»)."""
    from cdpm import history_section as hs
    from cdpm import history_questions as hq
    rules = hs.history_dependent_rules(doc)
    if not without_family:
        return [hs.history_question_id(b["read_kind"]) for b in hq.blocks_of(rules)]
    reads = {f: r for f, r in hs.history_reads().items() if f != without_family}
    order = list(hs.history_read_kinds()) + [hs.HISTORY_KIND_BY_LOGIC]
    kinds = set()
    for rule in rules:
        fams = [f for f in hs.rule_families(rule) if f in reads]
        kinds |= {reads[f]["kind"] for f in fams} or {hs.HISTORY_KIND_BY_LOGIC}
    return [hs.history_question_id(k) for k in order if k in kinds]

#: Ожидание «запись о прогоне сшита» (проект «Запись о прогоне», шаг 2,
#: раздел 10, пункт 5): у ХОЗЯИНА записи стоят этапы COSMOS по каждой
#: нажатой задаче и по сверке с ИДАК. Одно на все наборы формы H0 —
#: наборы-вариации берут его отсюда (`bk.RECORD_STITCHED`), а не
#: переписывают у себя. Судится только с машинным отчётом нажатий
#: (ключ --cosmos-press): без нажатий этапов COSMOS в записи и быть не
#: должно.
RECORD_STITCHED = {
    "cosmos_stages": ["pairing", "rostering", "source-check"],
    "_note": ("запись о прогоне сшита: у хозяина записи стоят этапы "
              "COSMOS по каждой нажатой задаче и по сверке с ИДАК; "
              "судится только с машинным отчётом нажатий "
              "(--cosmos-press)"),
}

#: Ожидание «золотым прогон называет только владелец» (проект «Запись о
#: прогоне», шаг 3; решение владельца 6.3 от 10.09.2026): после приёмки
#: на Prod система СПРАШИВАЕТ владельца, и признак ставится по его
#: подтверждению — машина его не ставит. Одно на все наборы формы H0
#: (наборы-вариации берут его отсюда, `bk.GOLDEN_BY_OWNER_ONLY`).
#: Судится, как и сшивка, только с машинным отчётом нажатий
#: (--cosmos-press): без нажатий приёмки не было и спрашивать было не о
#: чем.
GOLDEN_BY_OWNER_ONLY = {
    "runner_sets_golden": False,
    "_note": ("признак золотого прогона ставит ТОЛЬКО владелец словом "
              "после приёмки на Prod: раннер не ставит его никогда, а "
              "признак у хозяина записи без подписи (кто, когда, какими "
              "словами) — красная строка; судится только с машинным "
              "отчётом нажатий (--cosmos-press)"),
}

#: Ожидаемые вердикты СТАДИЙ (ключ `stage_verdicts` манифеста, 11.09.2026):
#: вердикт стадии — свойство НАБОРА, а не раннера. Стадии CDPM: извлечение
#: эквивалентно эталону там, где вопросов анкеты ПРИ ИЗВЛЕЧЕНИИ нет (Н0,
#: Н0-C: вопросы об истории рождаются линией данных на стадии cycle и
#: закрываются книгой ответов), и не эквивалентно там, где вопрос задаётся
#: уже при извлечении (Н0-B — второй параметр семьи; Н0-К/Н0-КИ — целевая):
#: такой набор называет своё ожидание переопределением. После ответов и в
#: пакете ВМП эквивалентна у всех. Вердикт сквозного цикла не объявляется:
#: он перекрашивается пометкой чистоты цикла и одним словом не
#: исчерпывается. Стадии COSMOS судятся только с машинным отчётом нажатий.
#: Живые лестницы кругов s8/s9 (11.09): Н0 и Н0-C — extract EQUIVALENT,
#: Н0-B — extract NOT_EQUIVALENT; answers и package — EQUIVALENT у всех.
STAGE_VERDICTS_CDPM = {"extract": "EQUIVALENT",
                       "answers": "EQUIVALENT",
                       "package": "EQUIVALENT"}
STAGE_VERDICTS_COSMOS = {"pairing": "пейринг построен",
                         "rostering": "допустимо",
                         "source-check": "без нарушений"}


def stage_verdicts(note: str, with_cosmos: bool = True, **overrides) -> dict:
    """Ожидание `stage_verdicts` набора: общие вердикты стадий плюс
    пояснение набора. `with_cosmos` — ждёт ли набор нажатий (у H0-КИ по
    замыслу кнопка ростеринга ОСТАНАВЛИВАЕТСЯ, и вердикта задачи там не
    ждут). `overrides` — вердикты, которыми набор отличается от общих
    (например, extract="NOT_EQUIVALENT" у набора с вопросом при
    извлечении)."""
    out = dict(STAGE_VERDICTS_CDPM)
    if with_cosmos:
        out.update(STAGE_VERDICTS_COSMOS)
    out.update(overrides)
    out["_note"] = note
    return out


#: Ожидаемый вердикт НЕПРОТИВОРЕЧИВОСТИ постановки (ключ `consistency`
#: манифеста, 11.09.2026): SMT-проверка стала обязательным гейтом
#: выпуска пакета (слово владельца), и вердикт кладётся в манифест
#: пакета. У всех наборов формы H0 жёсткие правила есть и противоречий
#: в них нет — ждём «sat» («none» ждал бы набор вовсе без жёстких
#: правил; «unsat» ожиданием не бывает: с ним пакет не выпускается).
#: Одно слово на все наборы — вариации берут его отсюда
#: (`bk.CONSISTENCY`), а не переписывают у себя.
CONSISTENCY = "sat"

sys.path.insert(0, str(REPO))
from cdpm import vmp_form as vf  # noqa: E402

# Раскладка разделов формы по файлам (часть → файл).
FILE_OF = {
    ("common", "definitions"): "docx", ("common", "world"): "docx",
    ("pairing", "hard"): "docx", ("pairing", "soft"): "docx",
    ("rostering", "hard"): "xlsx", ("rostering", "soft"): "xlsx",
    ("pairing", "criteria"): "jpg", ("rostering", "criteria"): "jpg",
}


def load_form() -> dict:
    return json.loads(FORM_H0.read_text(encoding="utf-8"))


def header_lines(form: dict) -> list[str]:
    return [
        f"ВМП — {form.get('customer')}",
        f"Валюта ставок и коэффициентов: {form.get('currency')}. База "
        f"шаблонов: {form.get('registry_rev')}.",
        vf.COMPOSITION_TEXT,
    ]


def lines_by_file(form: dict) -> dict:
    """{файл: [(kind, id|None, текст)]}: kind ∈ header|heading|row."""
    out = {"docx": [], "xlsx": [], "jpg": []}
    for text in header_lines(form):
        out["docx"].append(("header", None, text))
    for part, sec, rows in vf.form_sections(form):
        target = FILE_OF[(part, sec)]
        out[target].append(("heading", None, vf.SECTION_TITLES[(part, sec)]))
        for row in rows:
            out[target].append(("row", row["id"],
                                vf.render_row_line(row, sec)))
    return out


#: Постоянная метка времени нормализации файлов Office
#: (freeze_office_file): 1 января 2000 года, полночь. Меток раньше 1980
#: года zip не умеет; какой именно день — неважно, важно, что он один и
#: тот же в каждой сборке и на любой машине.
OFFICE_STAMP = (2000, 1, 1, 0, 0, 0)
#: Та же метка в записи W3CDTF — для свойств документа (docProps/core.xml).
OFFICE_STAMP_TEXT = "2000-01-01T00:00:00Z"
#: Свойства документа, которые библиотеки и редакторы заполняют часами
#: машины (а иногда и именем пользователя), → постоянные значения. Ключ —
#: МЕСТНОЕ имя элемента core.xml: приставка пространства имён у элемента
#: может быть любая (dcterms:created, cp:revision).
OFFICE_CORE_PROPS = {
    "created": OFFICE_STAMP_TEXT,
    "modified": OFFICE_STAMP_TEXT,
    "lastPrinted": OFFICE_STAMP_TEXT,
    "revision": "1",
    "lastModifiedBy": "",
}
#: Часть архива со свойствами документа.
OFFICE_CORE_PART = "docProps/core.xml"
#: Уровень сжатия называется ЯВНО: умолчание zipfile — дело версии
#: Python, а нам нужна одна и та же формула во всех сборках.
OFFICE_COMPRESS_LEVEL = 6
#: Внешние атрибуты записи — тоже ПОСТОЯННЫЕ: права файла на машине
#: сборки в наборе не нужны. Число названо здесь, а не оставлено нулём,
#: потому что ноль zipfile заменяет СВОИМ умолчанием (_open_to_write), а
#: оно — дело версии Python; берём привычное «?rw-------».
OFFICE_EXTERNAL_ATTR = 0o600 << 16


def freeze_office_props(xml: str) -> str:
    """Свойства документа (core.xml) — к постоянным значениям.

    Переписывается только ТЕКСТ известных элементов (OFFICE_CORE_PROPS);
    остальное в core.xml — заголовок, кодировка, автор-библиотека — не
    трогается, иначе нормализация меняла бы документ, а не его метки.
    Пустой элемент (<cp:lastModifiedBy/>) машинного времени не несёт и
    остаётся как есть."""
    for name, value in OFFICE_CORE_PROPS.items():
        tag = re.escape(name)
        xml = re.sub(rf"(<(?:[\w.\-]+:)?{tag}(?:\s[^>]*)?>)[^<]*"
                     rf"(</(?:[\w.\-]+:)?{tag}>)",
                     lambda m, v=value: m.group(1) + v + m.group(2), xml)
    return xml


def freeze_office_file(path: Path) -> Path:
    """Байты файла Office — от его ТЕКСТА, а не от часов машины.

    python-docx и openpyxl кладут в документ текущее время дважды: в
    записи zip-архива (date_time каждой части) и в свойства документа
    (dcterms:created/modified книги openpyxl). Из-за этого содержательно
    ТОТ ЖЕ документ получал при каждой пересборке новые байты, а с ними —
    новый отпечаток источника (sha256) в эталоне набора: отпечаток
    «плыл» без единой правки текста (волна 16, слово владельца
    11.09.2026).

    Носитель переписывает архив: те же части, в том же порядке, тем же
    методом сжатия, но у КАЖДОЙ записи постоянная метка (OFFICE_STAMP),
    постоянные внешние атрибуты (OFFICE_EXTERNAL_ATTR) и система 0 —
    иначе Windows и Linux пишут в заголовок записи разные числа (система
    3 против 0, права файла машины); свойства документа приводятся к
    постоянным (freeze_office_props). Содержимое частей, кроме core.xml,
    не трогается: текст документа нормализация не касается.

    ОДНА формула на все сборщики наборов формы H0: вариации зовут её
    через build_docx/build_xlsx этого модуля, книги данных —
    напрямую (build_data_h0, build_data_h0kh).

    Предел честности: сами байты сжатия даёт zlib той сборки Python, в
    которой собирают (в сборках встречаются и zlib, и zlib-ng — вывод
    разный). Поэтому «пересобрано = выложено» на ЧУЖОЙ машине не
    судится (урок CI № 81, 11.09.2026), а пересборка того же текста на
    одной машине даёт файл байт в байт."""
    path = Path(path)
    with zipfile.ZipFile(path) as zin:
        parts = [(info, zin.read(info)) for info in zin.infolist()]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, data in parts:
            if info.filename == OFFICE_CORE_PART:
                data = freeze_office_props(
                    data.decode("utf-8")).encode("utf-8")
            zi = zipfile.ZipInfo(info.filename, date_time=OFFICE_STAMP)
            zi.compress_type = info.compress_type
            zi.create_system = 0              # не «какая ОС собирала»
            # Внешние атрибуты — постоянные, от машины сборки не зависят;
            # от прежней записи остаётся только признак «папка» MS-DOS.
            zi.external_attr = OFFICE_EXTERNAL_ATTR | (info.external_attr
                                                       & 0x10)
            zout.writestr(zi, data, compresslevel=OFFICE_COMPRESS_LEVEL)
    path.write_bytes(buf.getvalue())
    return path


def build_docx(path: Path, items: list) -> None:
    import docx
    from docx.enum.section import WD_ORIENT
    from docx.shared import Mm, Pt

    d = docx.Document()
    sec = d.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Mm(297), Mm(210)
    sec.left_margin = sec.right_margin = Mm(15)
    d.styles["Normal"].font.size = Pt(10)
    for kind, _rid, text in items:
        # Без жирного: заморозка помечает жирные фрагменты маркерами
        # «**…**», а набор обязан совпадать с рендером буква в букву.
        d.add_paragraph(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(path))
    freeze_office_file(path)      # тот же текст → те же байты


def build_xlsx(path: Path, items: list) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = XLSX_SHEET
    ws.column_dimensions["A"].width = 220
    for kind, _rid, text in items:
        ws.append([text])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    freeze_office_file(path)      # тот же текст → те же байты


def jpg_fonts_present() -> bool:
    """Есть ли на машине те же шрифты, что у выложенной сборки jpg
    (JPG_FONTS) — только тогда пересборка воспроизводит выложенный файл
    байт в байт; иначе рисует запасной шрифт Pillow."""
    return all(Path(p).is_file() for p in JPG_FONTS)


def build_jpg(path: Path, items: list) -> list[str]:
    """Изображение текста: заголовок раздела и каждая запись — отдельный
    абзац (между абзацами пустая строка), перенос по словам."""
    from PIL import Image, ImageDraw, ImageFont

    lines = [text for _k, _r, text in items]
    try:
        font = ImageFont.truetype(JPG_FONTS[0], 26)
        font_b = ImageFont.truetype(JPG_FONTS[1], 26)
    except OSError:
        font = font_b = ImageFont.load_default()
    width = 1754
    img = Image.new("RGB", (width, 1240), "white")
    draw = ImageDraw.Draw(img)
    y = 70
    for kind, _rid, text in items:
        f = font_b if kind == "heading" else font
        words, cur, out = text.split(" "), "", []
        for w in words:
            probe = (cur + " " + w).strip() if cur else w
            if draw.textlength(probe, font=f) > width - 160 and cur:
                out.append(cur)
                cur = w
            else:
                cur = probe
        out.append(cur)
        for chunk in out:
            draw.text((80, y), chunk, fill="black", font=f)
            y += 36
        y += 36
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), quality=95)
    return lines


def freeze_texts(docx_path: Path, xlsx_path: Path) -> dict:
    from cdpm import freeze as fz
    return {
        "docx": [(c["anchor"], c["text"]) for c in fz._extract_docx(docx_path)],
        "xlsx": [(c["anchor"], c["text"]) for c in fz._extract_xlsx(xlsx_path)],
    }


def build_anchors_map(by_file: dict, frozen: dict, jpg_lines: list) -> tuple:
    """Якоря записей по факту заморозки (docx/xlsx — штатной, jpg — по
    порядку абзацев изображения); возвращает (карта, проблемы)."""
    amap: dict = {}
    problems: list[str] = []
    for fkey, sid in (("docx", SID_DOCX), ("xlsx", SID_XLSX)):
        texts = frozen[fkey]
        by_text: dict = {}
        for anchor, text in texts:
            by_text.setdefault(text, []).append(anchor)
        for kind, rid, text in by_file[fkey]:
            hits = by_text.get(text, [])
            if len(hits) != 1:
                problems.append(f"{fkey}: строка {rid or kind!r} в заморозке "
                                f"{len(hits)} раз (ожидалось 1)")
                continue
            if kind == "row":
                amap[rid] = [{"sid": sid, "anchor": hits[0]}]
        if len(texts) != len(by_file[fkey]):
            problems.append(f"{fkey}: клауз {len(texts)}, строк файла "
                            f"{len(by_file[fkey])}")
    for n, (kind, rid, text) in enumerate(by_file["jpg"], start=1):
        if text != jpg_lines[n - 1]:
            problems.append(f"jpg: абзац {n} не совпал с рендером")
        if kind == "row":
            amap[rid] = [{"sid": SID_JPG, "anchor": f"стр-1-абз-{n}"}]
    amap["_jpg_lines"] = [{"sid": SID_JPG, "line": t} for _k, _r, t in by_file["jpg"]]
    return amap, problems


def rel_to_repo(path: Path) -> str:
    """Путь для манифеста — от КОРНЯ репозитория (раннер читает
    REPO / etalon_vmp); сборка вне репозитория (стенд, временный
    каталог) — абсолютно. Та же формула, что у сборщиков вариаций."""
    p = Path(path)
    try:
        return str(p.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def sha256_file(path: Path) -> str:
    """Честный sha256 файла комплекта — ровно то число, которым источник
    ВМП называет генератор эталона (build_vmp_nano._kit_sources) и
    заморозка носителей (cdpm.freeze.sha256_of)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp_sources(form: dict, docs: Path) -> list:
    """Отпечатки источников формы — ПО ФАКТУ собранных файлов.

    Одна формула на все сборщики формы H0 (дефект 11.09.2026: базовый
    сборщик пересобирал docx/xlsx/jpg, но блок `sources` формы эталона
    оставлял с отпечатками ПРЕЖНЕЙ сборки — эталон называл sha, которого
    в комплекте уже нет; вариации H0-B/H0-C/H0-К отпечатки ставили).
    Файла из `sources` нет на диске — отказ словами, а не молчаливый
    пропуск.

    Воспроизводимость: отпечаток от сборки к сборке один и тот же —
    у docx и xlsx его держит нормализация носителя (freeze_office_file,
    волна 16): постоянная метка времени у записей архива и постоянные
    свойства документа, так что тот же текст даёт тот же файл байт в
    байт. У jpg меток времени нет вовсе, но нужны те же шрифты
    (JPG_FONTS): без них Pillow рисует запасным шрифтом и байты другие
    (задание Linux в CI № 81, 11.09.2026). Равенство с ВЫЛОЖЕННЫМ
    файлом остаётся правдой о машине сборки (сжатие даёт zlib той
    сборки Python) — отпечаток источника называет ФАЙЛ комплекта, а
    текст буква в букву судит сверка check_kit_h0 и карта якорей."""
    srcs = form.get("sources") or []
    if not srcs:
        raise SystemExit("в форме нет блока sources: отпечатки ставить "
                         "не на что")
    for s in srcs:
        p = Path(docs) / str(s.get("file") or "")
        if not p.is_file():
            raise SystemExit(f"источник формы не найден: {p}")
        s["sha256"] = sha256_file(p)
    return copy.deepcopy(srcs)


def catalog_dir():
    """Папка каталога шаблонов для сборщиков наборов: настройка продукта
    CREW_TEMPLATE_CATALOG, не задана — каталог дерева (REPO/catalog).
    Одна формула на все сборщики (H0, H0-K, H0-C)."""
    return (os.environ.get(CATALOG_ENV) or "").strip() or (REPO / "catalog")


def catalog_block(form: dict) -> dict:
    """{version, fingerprint} живого каталога, которым выпущен набор.
    Отпечаток формы (`registry_rev`) обязан совпасть с живым: иначе
    эталон посажен на другой каталог — перевязать registry_rev, а не
    собирать набор с чужим числом (отказ словами)."""
    import template_catalog as tc
    cat = tc.load(catalog_dir())
    live = str(cat.fingerprint())
    mine = str(form.get("registry_rev") or "")
    if mine != live:
        raise SystemExit(
            f"форма ВМП привязана к каталогу {mine[:12]}…, живой каталог "
            f"{cat.version()} — {live[:12]}…: перевяжите registry_rev формы "
            f"и эталона (перевыпуск эталонов по слову владельца), затем "
            f"соберите набор")
    return {"version": str(cat.version()), "fingerprint": live}


def main() -> int:
    form = load_form()
    bad = vf.validate_form(form, strict=False)
    if bad:
        print("ФОРМА ЭТАЛОНА НЕ ЧИСТА:\n  " + "\n  ".join(bad))
        return 1
    DOCS.mkdir(parents=True, exist_ok=True)
    by_file = lines_by_file(form)
    build_docx(DOCS / DOCX_NAME, by_file["docx"])
    build_xlsx(DOCS / XLSX_NAME, by_file["xlsx"])
    jpg_lines = build_jpg(DOCS / JPG_NAME, by_file["jpg"])
    frozen = freeze_texts(DOCS / DOCX_NAME, DOCS / XLSX_NAME)
    amap, problems = build_anchors_map(by_file, frozen, jpg_lines)
    n_rows = sum(len(rows) for _p, _s, rows in vf.form_sections(form))
    n_map = sum(1 for k in amap if not k.startswith("_"))
    if n_map != n_rows:
        problems.append(f"в карте {n_map} записей, в форме {n_rows}")
    if problems:
        print("САМОПРОВЕРКА КРАСНАЯ:\n  " + "\n  ".join(problems))
        return 1
    # Отпечатки источников — по факту ТОЛЬКО ЧТО собранных файлов, и в
    # обе проекции эталона разом: форму (вход сборщика) и ВМП. Иначе
    # эталон называет sha прежней сборки (дефект 11.09.2026), а паспорт
    # прогона и сводка выдают это число за отпечаток поданного файла.
    sources = stamp_sources(form, DOCS)
    etalon_vmp = ETALON / "VMP-H0.json"
    et = json.loads(etalon_vmp.read_text(encoding="utf-8"))
    mine = [(str(s.get("sid")), str(s.get("file"))) for s in sources]
    theirs = [(str(s.get("sid")), str(s.get("file")))
              for s in (et.get("sources") or [])]
    if theirs != mine:
        print(f"ЭТАЛОН И ФОРМА О РАЗНЫХ ИСТОЧНИКАХ: ВМП {theirs}, форма "
              f"{mine} — перевыпустите эталон (build_vmp_nano --kit)")
        return 1
    et["sources"] = copy.deepcopy(sources)
    vf.dump_form(form, FORM_H0)
    etalon_vmp.write_text(json.dumps(et, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    (ETALON / "anchors-map.json").write_text(
        json.dumps(amap, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    (ETALON / "jpg-lines.json").write_text(
        json.dumps(jpg_lines, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    (ETALON / "ANSWER-BOOK-MACHINE.json").write_text(json.dumps({
        "_comment": ("Набор «ИД H0-ВМП» (форма): вопросов о правилах на таком "
                     "входе быть не должно — любой такой вопрос = дефект "
                     "конвейера. Единственный законный вопрос — линии данных "
                     "(1.17.2, решение владельца 05.09.2026): истории "
                     "сотрудников в книге нет, а правила ростеринга (пол "
                     "отдыха) судятся с остатками — авиакомпания отвечает "
                     "«продолжить с нулевыми остатками»."),
        "exact": {},
        "prefix": history_book_prefix(), "regex": []},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (HERE / "kit-manifest.json").write_text(json.dumps({
        "customer": form.get("customer"),
        "stage": "ИД H0-ВМП (форма)",
        # Каким каталогом выпущен набор (шаг 3: сторож дрейфа сверяет
        # ключ комплекта с каталогом продукта). Пишется сборщиком, а не
        # рукой: перевыпуск 09.09.2026 под 0.3.0 стёр добавленный вручную
        # блок, и наборы-вариации потеряли его по наследству.
        "catalog": catalog_block(form),
        # Признак допуска моделей комплекта (решение владельца 03.09.2026):
        # едет с набором, сборщик обязан его сохранять при пересборке
        # (живой случай 05.09: пересборка под новый registry_rev стёрла
        # добавленные вручную поля).
        "data_class": "synthetic",
        "llm_scope": "any",
        "llm_scope_basis": ("набор синтетический: вымышленная авиакомпания, "
                            "генератор в репозитории (build_vmp_nano.py, "
                            "build_data_h0.py); решение владельца 03.09.2026 — "
                            "для синтетических и открытых данных любые "
                            "модели, включая облачные"),
        "rules_files": [f"docs/{DOCX_NAME}", f"docs/{XLSX_NAME}",
                        f"docs/{JPG_NAME}"],
        # Вторая часть набора — книга операционных данных
        # (build_data_h0.py). Файлы данных источниками ПРАВИЛ не
        # становятся (решение Р1б: sid не получают), но конвейеру
        # обязаны быть объявлены: без них нижний контур не запускается
        # и пакет уходит без clean-data, а матрица готовности стоит.
        "data_files": [f"data/{DATA_NAME}"] if (HERE / "data" / DATA_NAME).is_file() else [],
        "answer_book": "etalon/ANSWER-BOOK-MACHINE.json",
        # Раскладка моделей по ролям — путь ОТ КОРНЯ репозитория:
        # параметр прогона обязан находиться откуда угодно, а не только
        # из папки набора (паспорт прогона, 10.09.2026).
        "agents_layout_from_repo": LAYOUT_FROM_REPO,
        # Строгий режим ИДАК-0 «вопросов быть не должно» снят: по закону
        # 1.17.2 (владелец 05.09.2026) книга данных без листа истории
        # рождает вопросы линии данных — с каталога 0.5.0 по одному на
        # вид чтения истории (Q-3b-история-<вид>), книга ответов отвечает
        # им по префиксу; ожидание набора называет их поимённо (вычислены
        # кодом CDPM по эталону), любой вопрос сверх — красный отчёт.
        # Живой случай 09.09.2026: свежий Н0 на Test (run-0018) был
        # NOT_EQUIVALENT строгим режимом из-за этого вопроса.
        "strict_questions": False,
        "expect": {
            "questions": history_question_ids(json.loads(
                (ETALON / "VMP-H0.json").read_text(encoding="utf-8"))),
            "objectives": 2,
            "readiness_ladder": {"pairing": "optimization",
                                 "rostering": "optimization"},
            # Эталон ТЕКУЩЕГО комплекта по чтению api (Test 09.09.2026,
            # run-0021): на одну мягкую строку больше прежних 9/12 —
            # правило продления смены VN-011 и критерии есть только в
            # текущем комплекте (старый пакет из run-0089 их не нёс).
            "cosmos_numbers": {"pairing": "10/0", "rostering": "13/0",
                               "_note": "по чтению ИДАК каналом api; строки "
                                        "ok+soft_info+not_checkable / "
                                        "нарушений"},
            "stage_verdicts": stage_verdicts(
                "ожидаемые вердикты стадий набора. Извлечение "
                "эквивалентно эталону: вопросов анкеты при извлечении у "
                "набора нет — вопросы об истории рождаются линией данных "
                "(стадия cycle) и закрываются книгой ответов; после "
                "ответов и в пакете ВМП эквивалентна. Вердикты стадий "
                "COSMOS (пейринг, ростер, "
                "сверка с ИДАК) судятся только с машинным отчётом нажатий "
                "(--cosmos-press); без него — предупреждение, а не "
                "краснота."),
            # Вердикт гейта непротиворечивости в манифесте пакета
            # (11.09.2026): жёсткие правила у набора есть, противоречий
            # в них нет — ждём «sat».
            "consistency": CONSISTENCY,
            "record_stitched": copy.deepcopy(RECORD_STITCHED),
            "golden_by_owner_only": copy.deepcopy(GOLDEN_BY_OWNER_ONLY),
        },
        "etalon_vmp": rel_to_repo(ETALON / "VMP-H0.json"),
        "etalon_form": rel_to_repo(FORM_H0),
        "note": ("Форма ВМП H0 буква в букву в трёх файлах правил: docx — "
                 "заголовок, общий раздел, часть 1 а/б; xlsx — часть 2 "
                 "а/б; jpg — разделы в) обеих частей. Записи — "
                 "render_row_line формы без служебных полей; книга "
                 "ответов пуста. Вторая часть набора — книга данных "
                 "ID-H0-DATA.xlsx (генератор build_data_h0.py)."),
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"ИД H0-ВМП (форма) собран: docx {len(by_file['docx'])} абзацев, "
          f"xlsx {len(by_file['xlsx'])} строк, jpg {len(by_file['jpg'])} "
          f"абзацев; записей формы {n_rows}, карта якорей {n_map}; "
          f"самопроверка чиста (заморозка = рендер буква в букву)")
    print("отпечатки источников обновлены по факту сборки (форма и ВМП): "
          + ", ".join(f"{s['sid']} {s['file']} {s['sha256'][:12]}…"
                      for s in sources))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
