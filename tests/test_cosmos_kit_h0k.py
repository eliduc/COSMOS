# -*- coding: utf-8 -*-
"""Сборщик набора «ИД H0-К» (H0 без критерия оптимизации) и перенос
свежей сборки в набор «ИД H0-КИ» (H0-К + листы истории).

Что судится:
  * отпечатки источников ставит НОСИТЕЛЬ базового сборщика
    (bk.stamp_sources) — своей формулы у вариации нет, и обе проекции
    эталона (форма и ВМП) получают отпечатки ПО ФАКТУ сборки; файла нет
    — отказ словами, проекции о разных источниках — отказ словами;
  * один запуск сборщика Н0-К перевязывает Н0-КИ: документы, обе
    проекции эталона с отпечатками по факту файлов НАБОРА, карта якорей,
    строки jpg и книга ответов (свои слова набора сохраняются), а также
    ожидания обоих вариантов (stop и full), у которых одно место правды
    — вердикты стадий и слово непротиворечивости;
  * набора рядом нет — перенос не делается и об этом сказано словами;
    набор неполон или о других файлах правил — отказ словами ДО записи.

Стенд собирает комплект во ВРЕМЕННУЮ папку и переносит в КОПИЮ набора
Н0-КИ: выложенные наборы репозитория тестом не трогаются. Равенство
пересобранных docx/xlsx/jpg с выложенными файлами не судится — в zip
docx/xlsx попадает время записи, а байты jpg зависят от шрифтов машины
(находка CI № 81, 11.09.2026); сравнивается то, что сборщик записал сам.
"""
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KIT = ROOT / "open-bench" / "polyaris-h0k"
H0KH = ROOT / "open-bench" / "polyaris-h0kh"


def _builder():
    if str(KIT) not in sys.path:
        sys.path.insert(0, str(KIT))
    spec = importlib.util.spec_from_file_location(
        "build_kit_h0k_stand", KIT / "build_kit_h0k.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def перенос(tmp_path_factory):
    """Одна сборка Н0-К на весь стенд: комплект — во временную папку,
    набор Н0-КИ — КОПИЯ выложенного (репозиторий сборкой не трогается).
    Возвращает (сборщик, папка комплекта, папка Н0-КИ, итог сборки)."""
    root = tmp_path_factory.mktemp("h0k")
    kit, kh = root / "kit", root / "polyaris-h0kh"
    shutil.copytree(H0KH, kh)
    b = _builder()
    res = b.build(kit, kh_dir=kh)
    return b, kit, kh, res


# ── Отпечатки источников: носитель один ───────────────────────────────

def test_сборщик_h0k_ставит_отпечатки_источников_по_факту_сборки(перенос):
    """Отпечатки обеих проекций эталона — sha ТОЛЬКО ЧТО собранных файлов
    комплекта, и ставит их носитель базового сборщика: своей формулы
    отпечатка у вариации больше нет (11.09.2026 — одна формула на все
    сборщики формы H0)."""
    b, kit, _kh, _res = перенос
    assert not hasattr(b, "_sha"), "у вариации своя формула отпечатка"
    form = _read(kit / "etalon" / "VMP-H0K-form.json")
    et = _read(kit / "etalon" / "VMP-H0K.json")
    assert [s["file"] for s in form["sources"]] == [b.DOCX_NAME, b.XLSX_NAME,
                                                    b.JPG_NAME]
    assert et["sources"] == form["sources"]
    for s in form["sources"]:
        assert s["sha256"] == _sha(kit / "docs" / s["file"]), s["file"]


def test_сборщик_h0k_отказывает_словами_когда_файла_источника_нет(tmp_path,
                                                                  monkeypatch):
    """Файла из `sources` на диске нет — отказ словами носителя, а не
    молчаливый пропуск и не голое падение разбора."""
    b = _builder()
    real = b.bk.build_jpg

    def _без_файла(path, items):
        lines = real(path, items)
        Path(path).unlink()
        return lines

    monkeypatch.setattr(b.bk, "build_jpg", _без_файла)
    with pytest.raises(SystemExit) as e:
        b.build(tmp_path)
    assert "источник формы не найден" in str(e.value)
    assert not (tmp_path / "kit-manifest.json").is_file()


def test_сборщик_h0k_отказывает_когда_проекции_эталона_о_разных_источниках(
        tmp_path, monkeypatch):
    """ВМП эталона о других файлах, чем форма, — отказ словами (закон
    базового сборщика): молча выдать эталон о чужих файлах нельзя."""
    b = _builder()
    srcs = [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64}]
    b.check_sources(srcs, {"sources": [dict(srcs[0])]})        # молчит
    for et in ({"sources": [{"sid": "S1", "file": "ЧУЖОЙ.docx"}]}, {}):
        with pytest.raises(SystemExit) as e:
            b.check_sources(srcs, et)
        assert "О РАЗНЫХ ИСТОЧНИКАХ" in str(e.value)
    real = b.derive_etalon
    monkeypatch.setattr(b, "derive_etalon", lambda *a, **kw: dict(
        real(*a, **kw), sources=[]))
    with pytest.raises(SystemExit) as e:
        b.build(tmp_path)
    assert "О РАЗНЫХ ИСТОЧНИКАХ" in str(e.value)
    assert not (tmp_path / "kit-manifest.json").is_file()


# ── Перенос в набор Н0-КИ ─────────────────────────────────────────────

def test_перенос_несёт_в_h0kh_свежие_документы_и_отпечатки(перенос):
    """Один запуск сборщика Н0-К кладёт в Н0-КИ те же документы и
    перевязывает ОБЕ проекции эталона отпечатками по факту файлов
    НАБОРА. Дефект 11.09.2026: документы и форму переносили рукой, а ВМП
    эталона оставался с отпечатками прежней сборки."""
    b, kit, kh, res = перенос
    assert res["h0kh"] and res["h0kh"]["variants"] == ["stop", "full"]
    for name in (b.DOCX_NAME, b.XLSX_NAME, b.JPG_NAME):
        assert (kh / "docs" / name).read_bytes() == \
            (kit / "docs" / name).read_bytes(), name
    form = _read(kh / "etalon" / "VMP-H0K-form.json")
    et = _read(kh / "etalon" / "VMP-H0K.json")
    assert et["sources"] == form["sources"]
    for s in form["sources"]:
        assert s["sha256"] == _sha(kh / "docs" / s["file"]), s["file"]
    # содержание эталона — то же, что у Н0-К (Н0-КИ живёт его копиями)
    kit_et = _read(kit / "etalon" / "VMP-H0K.json")
    assert et["rules"] == kit_et["rules"]
    assert et["objectives"] == kit_et["objectives"]
    assert _read(kh / "etalon" / "anchors-map.json") == \
        _read(kit / "etalon" / "anchors-map.json")
    assert _read(kh / "etalon" / "jpg-lines.json") == \
        _read(kit / "etalon" / "jpg-lines.json")


def test_перенос_книги_ответов_сохраняет_свои_слова_набора(перенос):
    """Ответы у Н0-КИ те же, что у Н0-К (вопросы те же), а пояснение
    набора — своё: перенос его не переписывает."""
    b, kit, kh, _res = перенос
    book = _read(kh / "etalon" / "ANSWER-BOOK-MACHINE.json")
    kit_book = _read(kit / "etalon" / "ANSWER-BOOK-MACHINE.json")
    for key in ("exact", "prefix", "regex"):
        assert book[key] == kit_book[key], key
    assert book["prefix"][b.bk.history_question_prefix()] == \
        b.bk.HISTORY_CONTINUE_ZERO
    свой = _read(H0KH / "etalon" / "ANSWER-BOOK-MACHINE.json")["_comment"]
    assert book["_comment"] == свой and book["_comment"] != kit_book["_comment"]
    assert "H0-КИ" in book["_comment"]


def test_перенос_ставит_ожидания_обоих_вариантов_h0kh(перенос):
    """Вердикты стадий и слово непротиворечивости у обоих вариантов —
    из одного места правды (сборщик), а своё у варианта только
    пояснение: стоп-вариант стадий COSMOS не объявляет (кнопка
    ростеринга останавливается словами), полный объявляет ростеринг
    «допустимо» — этого ждёт его замысел (expect.history)."""
    b, kit, kh, _res = перенос
    catalog = _read(kit / "kit-manifest.json")["catalog"]
    for rel, variant in (("kit-manifest.json", "stop"),
                         ("full/kit-manifest.json", "full")):
        man = _read(kh / rel)
        exp = man["expect"]
        assert exp["stage_verdicts"] == b.h0kh_stage_verdicts(variant), variant
        assert exp["consistency"] == b.bk.CONSISTENCY, variant
        assert exp["stage_verdicts"]["extract"] == "NOT_EQUIVALENT", variant
        assert exp["stage_verdicts"]["answers"] == \
            b.bk.STAGE_VERDICTS_CDPM["answers"], variant
        # своё у варианта — ожидания истории и вопросы анкеты, они целы
        assert exp["history"]["present"] is True, variant
        assert exp["questions"][:2] == ["Q-3a-GOAL", "Q-3b-GOAL"], variant
    стоп = _read(kh / "kit-manifest.json")
    полный = _read(kh / "full" / "kit-manifest.json")
    assert "rostering" not in стоп["expect"]["stage_verdicts"]
    assert полный["expect"]["stage_verdicts"]["rostering"] == \
        b.bk.STAGE_VERDICTS_COSMOS["rostering"]
    assert "pairing" not in полный["expect"]["stage_verdicts"]
    # блок каталога стоп-варианта перевязан сборщиком, а не рукой
    assert стоп["catalog"] == catalog
    # чужого в манифестах перенос не тронул
    assert полный["expect"]["history"]["with_history"] == 24
    assert стоп["expect"]["history"]["no_history_row"] == "H14"


def test_без_соседнего_набора_перенос_не_делается_и_это_сказано(tmp_path,
                                                                capsys):
    """Сборка во временную папку (стенд, проба) соседа Н0-КИ не находит:
    перенос не делается, и об этом говорится словами — выложенный набор
    репозитория такой сборкой не трогается."""
    b = _builder()
    res = b.build(tmp_path / "kit")
    assert res["h0kh"] is None
    assert "перенос не делался" in capsys.readouterr().out


def test_перенос_отказывает_словами_на_неполном_и_чужом_наборе(перенос,
                                                               tmp_path):
    """Набор Н0-КИ неполон (нет папки эталона) или о ДРУГИХ файлах правил
    — отказ словами, и отказ случается ДО записи: перевязанного
    наполовину набора после него не остаётся."""
    b, kit, kh, _res = перенос
    form = _read(kit / "etalon" / "VMP-H0K-form.json")
    et = _read(kit / "etalon" / "VMP-H0K.json")
    docs, etalon = kit / "docs", kit / "etalon"
    неполный = tmp_path / "неполный"
    shutil.copytree(kh, неполный)
    shutil.rmtree(неполный / "etalon")
    with pytest.raises(SystemExit) as e:
        b.sync_h0kh(form, et, docs, etalon, неполный)
    assert "неполон" in str(e.value)
    чужой = tmp_path / "чужой"
    shutil.copytree(kh, чужой)
    man = _read(чужой / "kit-manifest.json")
    man["rules_files"] = ["docs/ЧУЖОЙ.docx"]
    b.dump_json(чужой / "kit-manifest.json", man)
    было = (чужой / "docs" / b.DOCX_NAME).read_bytes()
    with pytest.raises(SystemExit) as e:
        b.sync_h0kh(form, et, docs, etalon, чужой)
    assert "не о тех документах" in str(e.value)
    assert (чужой / "docs" / b.DOCX_NAME).read_bytes() == было
    # Набора рядом нет вовсе — это не отказ, а слова.
    assert b.sync_h0kh(form, et, docs, etalon, tmp_path / "нет-такого") is None


def test_выложенный_h0kh_обе_проекции_эталона_согласованы_с_документами():
    """Выложенный набор Н0-КИ: обе проекции эталона называют отпечатки
    файлов docs/ самого набора. Находка 11.09.2026 (волна 16): ВМП эталона
    Н0-КИ нёс отпечатки прежней сборки, форма — свежие; закрыто пересборкой
    сборщиком Н0-К, который перевязывает Н0-КИ сам (sync_h0kh)."""
    form = _read(H0KH / "etalon" / "VMP-H0K-form.json")
    et = _read(H0KH / "etalon" / "VMP-H0K.json")
    assert et["sources"] == form["sources"]
    for s in form["sources"]:
        p = H0KH / "docs" / s["file"]
        assert p.is_file(), f"источник набора Н0-КИ не выложен: {p}"
        assert s["sha256"] == _sha(p), (
            f"{s['sid']} {s['file']}: эталон Н0-КИ называет {s['sha256'][:12]}…, "
            f"файл набора — {_sha(p)[:12]}… (пересоберите Н0-К: "
            f"python open-bench/polyaris-h0k/build_kit_h0k.py)")


def test_отступ_записи_манифеста_берётся_у_файла():
    """Перезапись чужого манифеста не переформатирует его целиком: у
    стоп-варианта Н0-КИ отступ 1, у полного — 2."""
    b = _builder()
    assert b.json_indent('{\n "a": 1\n}\n') == 1
    assert b.json_indent('{\n  "a": 1\n}\n') == 2
    assert b.json_indent("{}") == 1
    assert b.json_indent(
        (H0KH / "kit-manifest.json").read_text(encoding="utf-8")) == 1
    assert b.json_indent(
        (H0KH / "full" / "kit-manifest.json").read_text(encoding="utf-8")) == 2
