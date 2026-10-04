# -*- coding: utf-8 -*-
"""Сборщики наборов формы H0: тот же текст — те же байты.

python-docx и openpyxl кладут в файл текущее время дважды — в записи
zip-архива (date_time каждой части) и в свойства документа
(dcterms:created/modified книги openpyxl). Из-за этого содержательно тот
же документ получал при каждой пересборке новые байты, а с ними — новый
отпечаток источника (sha256) в эталоне набора: число «плыло» без единой
правки текста. Слово владельца 11.09.2026: нормализацию меток времени в
docx/xlsx у сборщиков наборов сделать отдельной волной.

Носитель один на все сборщики формы H0 —
`build_kit_h0.freeze_office_file`; книги данных зовут его через
`build_data_h0.freeze_office`. Стенд судит три вещи:
  * у КАЖДОЙ записи архива постоянная метка, постоянные внешние атрибуты
    и система 0 (иначе Windows и Linux пишут разные числа), а свойства
    документа — постоянная дата;
  * две сборки ПОД РАЗНЫМИ ЧАСАМИ дают файл байт в байт (иначе проверка
    была бы пустой: две сборки в одну секунду совпали бы и без носителя);
  * текст клауз по ШТАТНОЙ заморозке CDPM после нормализации тот же, что
    до неё: носитель правит метки, а не документ.

Чего стенд НЕ судит: равенства пересобранного файла с ВЫЛОЖЕННЫМ в
репозитории файлом набора. Сами байты сжатия даёт zlib той сборки
Python, в которой собирают (встречаются и zlib, и zlib-ng), а задания CI
идут на ubuntu и windows — урок CI № 81 (11.09.2026): байты, зависящие
от машины, тесты не судят. Судится «дважды на этой машине — одинаково».
"""
import importlib.util
import json
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KIT = ROOT / "open-bench" / "polyaris-h0"
KIT_KH = ROOT / "open-bench" / "polyaris-h0kh"
FORM = KIT / "etalon" / "VMP-H0-form.json"
VMP = KIT / "etalon" / "VMP-H0.json"
#: Две заведомо разные отметки машинных часов (секунды эпохи): под ними
#: собираются два комплекта. Без нормализации байты разошлись бы на
#: метках записей архива — проверка «дважды одинаково» не пуста.
CLOCKS = (1_000_000_000, 1_500_000_000)
#: Свойства документа с датой — их значения ищет проверка меток.
DATE_PROPS = re.compile(r"<(?:[\w.\-]+:)?(created|modified)[^>]*>([^<]*)<")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bk():
    """Базовый сборщик набора H0 — носитель нормализации."""
    return _load("build_kit_h0", KIT / "build_kit_h0.py")


@pytest.fixture(scope="module")
def bd():
    """Книга операционных данных H0."""
    return _load("build_data_h0", KIT / "build_data_h0.py")


@pytest.fixture(scope="module")
def bdkh():
    """Книга данных H0-КИ (история) — выводится из книги H0."""
    return _load("build_data_h0kh", KIT_KH / "build_data_h0kh.py")


def _items(builder) -> dict:
    """Строки формы H0 по файлам — вход сборщиков документов."""
    return builder.lines_by_file(builder.load_form())


def _sheets(path: Path) -> dict:
    """Содержимое книги по листам — чем её видит читатель."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True)
    return {ws.title: [[c.value for c in row] for row in ws.iter_rows()]
            for ws in wb.worksheets}


def test_у_записей_архива_и_свойств_документа_постоянные_метки(bk, tmp_path):
    """Прямая проверка носителя: часов машины в файле не остаётся ни в
    заголовках записей архива, ни в свойствах документа."""
    items = _items(bk)
    docx, xlsx = tmp_path / "ИД-1.docx", tmp_path / "ИД-2.xlsx"
    bk.build_docx(docx, items["docx"])
    bk.build_xlsx(xlsx, items["xlsx"])
    for path in (docx, xlsx):
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            core = z.read(bk.OFFICE_CORE_PART).decode("utf-8")
        assert infos, f"{path.name}: в архиве нет записей"
        for i in infos:
            assert i.date_time == bk.OFFICE_STAMP, (
                f"{path.name}/{i.filename}: метка записи {i.date_time}, "
                f"ожидалась постоянная {bk.OFFICE_STAMP}")
            assert i.create_system == 0, (
                f"{path.name}/{i.filename}: система {i.create_system} — "
                f"в записи осталось, какая ОС собирала")
            assert i.external_attr in (bk.OFFICE_EXTERNAL_ATTR,
                                       bk.OFFICE_EXTERNAL_ATTR | 0x10), (
                f"{path.name}/{i.filename}: внешние атрибуты "
                f"{i.external_attr:#x} — в записи остались права машины")
        dates = DATE_PROPS.findall(core)
        assert dates, f"{path.name}: в свойствах документа нет дат"
        assert {v for _name, v in dates} == {bk.OFFICE_STAMP_TEXT}, (
            f"{path.name}: даты свойств {sorted({v for _n, v in dates})}, "
            f"ожидалась одна постоянная {bk.OFFICE_STAMP_TEXT}")


def test_документы_и_книга_данных_дважды_собираются_байт_в_байт(
        bk, bd, tmp_path, monkeypatch):
    """Две сборки ПОД РАЗНЫМИ ЧАСАМИ в двух временных папках: docx, xlsx
    и книга данных — байт в байт. Часы подменяются нарочно: иначе две
    сборки в одну секунду совпали бы и без носителя."""
    items = _items(bk)
    made = []
    for n, clock in enumerate(CLOCKS, start=1):
        folder = tmp_path / f"сборка-{n}"
        folder.mkdir()
        monkeypatch.setattr(time, "time", lambda c=clock: float(c))
        bk.build_docx(folder / "ИД-1.docx", items["docx"])
        bk.build_xlsx(folder / "ИД-2.xlsx", items["xlsx"])
        bd.write_xlsx(folder / "ИД-ДАННЫЕ.xlsx")
        made.append(folder)
    monkeypatch.undo()
    for name in ("ИД-1.docx", "ИД-2.xlsx", "ИД-ДАННЫЕ.xlsx"):
        first, second = (made[0] / name), (made[1] / name)
        assert first.read_bytes() == second.read_bytes(), (
            f"{name}: две сборки того же текста дали разные байты — "
            f"отпечаток источника поплывёт при каждой пересборке")


def test_нормализация_не_меняет_текст_клауз(bk, tmp_path, monkeypatch):
    """Штатная заморозка CDPM читает нормализованный файл и даёт те же
    клаузы (якорь и текст), что у ненормализованного: носитель правит
    метки, а не документ. Проверка не пуста: байты файлов различаются, а
    клаузы есть."""
    items = _items(bk)
    raw, norm = tmp_path / "без-носителя", tmp_path / "с-носителем"
    raw.mkdir()
    norm.mkdir()
    monkeypatch.setattr(bk, "freeze_office_file", lambda p: Path(p))
    bk.build_docx(raw / "ИД-1.docx", items["docx"])
    bk.build_xlsx(raw / "ИД-2.xlsx", items["xlsx"])
    monkeypatch.undo()
    bk.build_docx(norm / "ИД-1.docx", items["docx"])
    bk.build_xlsx(norm / "ИД-2.xlsx", items["xlsx"])
    for name in ("ИД-1.docx", "ИД-2.xlsx"):
        assert (raw / name).read_bytes() != (norm / name).read_bytes(), (
            f"{name}: носитель ничего не изменил — сравнивать нечего")
    was = bk.freeze_texts(raw / "ИД-1.docx", raw / "ИД-2.xlsx")
    now = bk.freeze_texts(norm / "ИД-1.docx", norm / "ИД-2.xlsx")
    assert was["docx"] and was["xlsx"], "заморозка не нашла клауз"
    assert now == was, "после нормализации заморозка читает другой текст"


def test_книга_данных_h0_после_нормализации_читается_та_же(bd, tmp_path,
                                                           monkeypatch):
    """Книга данных открывается openpyxl, и её листы, строки и значения
    после нормализации те же, что до неё."""
    monkeypatch.setattr(bd, "freeze_office", lambda p: Path(p))
    raw = bd.write_xlsx(tmp_path / "без-носителя.xlsx")
    monkeypatch.undo()
    norm = bd.write_xlsx(tmp_path / "с-носителем.xlsx")
    assert raw.read_bytes() != norm.read_bytes(), (
        "носитель книгу не тронул — сравнивать нечего")
    was = _sheets(raw)
    assert was, "в книге данных нет листов"
    assert _sheets(norm) == was, (
        "после нормализации книга читается иначе")


def test_книга_данных_h0ки_дважды_собирается_байт_в_байт(bdkh, tmp_path,
                                                         monkeypatch):
    """Книга H0-КИ берёт носитель через книгу H0: две сборки под разными
    часами — байт в байт, и метки записей постоянные."""
    made = []
    for n, clock in enumerate(CLOCKS, start=1):
        monkeypatch.setattr(time, "time", lambda c=clock: float(c))
        made.append(bdkh.write_xlsx("stop", tmp_path / f"сборка-{n}.xlsx"))
    monkeypatch.undo()
    assert made[0].read_bytes() == made[1].read_bytes(), (
        "две сборки книги H0-КИ дали разные байты")
    with zipfile.ZipFile(made[0]) as z:
        stamps = {i.date_time for i in z.infolist()}
    assert stamps == {(2000, 1, 1, 0, 0, 0)}, (
        f"метки записей книги H0-КИ: {sorted(stamps)}")
    assert _sheets(made[0]), "книга H0-КИ не читается openpyxl"


def _rebuild_kit(builder, folder: Path, monkeypatch) -> Path:
    """Полная пересборка набора H0 во временной папке по копии эталона
    (ни один файл комплекта в репозитории не трогается); возвращает папку
    документов."""
    docs, etalon = folder / "docs", folder / "etalon"
    etalon.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FORM, etalon / "VMP-H0-form.json")
    shutil.copyfile(VMP, etalon / "VMP-H0.json")
    monkeypatch.setattr(builder, "HERE", folder)
    monkeypatch.setattr(builder, "DOCS", docs)
    monkeypatch.setattr(builder, "ETALON", etalon)
    monkeypatch.setattr(builder, "FORM_H0", etalon / "VMP-H0-form.json")
    assert builder.main() == 0, "пересборка набора H0 вернула отказ"
    return docs


def _stamped_sources(docs: Path) -> dict:
    """{файл: отпечаток} из формы эталона, только что перевязанной
    сборщиком по факту сборки."""
    form = json.loads((docs.parent / "etalon" / "VMP-H0-form.json")
                      .read_text(encoding="utf-8"))
    return {s["file"]: s["sha256"] for s in form["sources"]}


def test_пересборка_набора_h0_дважды_даёт_те_же_документы_и_отпечатки(
        bk, tmp_path, monkeypatch):
    """Набор целиком (тот же путь, которым его собирает водитель сборки):
    две пересборки в двух временных папках дают одинаковые docx и xlsx, а
    значит и один и тот же отпечаток источника в эталоне."""
    first = _rebuild_kit(bk, tmp_path / "первая", monkeypatch)
    second = _rebuild_kit(bk, tmp_path / "вторая", monkeypatch)
    monkeypatch.undo()
    for name in (bk.DOCX_NAME, bk.XLSX_NAME):
        assert (first / name).read_bytes() == (second / name).read_bytes(), (
            f"{name}: две пересборки набора дали разные байты")
    was, now = _stamped_sources(first), _stamped_sources(second)
    for name in (bk.DOCX_NAME, bk.XLSX_NAME):
        assert was[name] == now[name], (
            f"{name}: отпечаток источника при пересборке изменился "
            f"({was[name][:12]}… → {now[name][:12]}…)")
