# -*- coding: utf-8 -*-
"""Читатель каталога шаблонов: один экземпляр на процесс с
перечитыванием по изменению файлов и обёртки лексикона для CDPM
(просьбы CDPM к шагу 2, 07.09.2026)."""
import json
import os
import shutil
from pathlib import Path

import template_catalog as tc

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog"


def _copy_catalog(tmp_path: Path) -> Path:
    dst = tmp_path / "catalog"
    shutil.copytree(CATALOG, dst,
                    ignore=shutil.ignore_patterns("__pycache__", "tools",
                                                  "transition"))
    return dst


def test_current_отдаёт_тот_же_экземпляр_пока_файлы_не_менялись(tmp_path):
    tc.forget_current()
    folder = _copy_catalog(tmp_path)
    a = tc.current(folder)
    b = tc.current(folder)
    assert a is b
    assert a.version() == tc.load(folder).version()


def test_current_перечитывает_после_правки_файла(tmp_path):
    tc.forget_current()
    folder = _copy_catalog(tmp_path)
    a = tc.current(folder)
    head = folder / "catalog.json"
    doc = json.loads(head.read_text(encoding="utf-8"))
    doc["note"] = "правка на диске без перезапуска процесса"
    head.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    # mtime с запасом — файловые системы с грубой отметкой времени
    st = head.stat()
    os.utime(head, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    b = tc.current(folder)
    assert b is not a
    assert tc.current(folder) is b


def test_forget_current_сбрасывает_кэш(tmp_path):
    tc.forget_current()
    folder = _copy_catalog(tmp_path)
    a = tc.current(folder)
    tc.forget_current()
    assert tc.current(folder) is not a


def test_subject_words_кортеж_и_пусто_у_семьи_без_слов():
    tc.forget_current()
    lex = tc.load(CATALOG).lexicon()
    with_words = [f for f, w in lex["subject_words"].items() if w]
    assert with_words, "в каталоге должны быть семьи со словами-носителями"
    fam = with_words[0]
    words = tc.subject_words(fam, CATALOG)
    assert isinstance(words, tuple) and words
    assert list(words) == list(lex["subject_words"][fam])
    # семья без слов (null в записи) и неизвестная семья — пустой кортеж,
    # лексический контракт семью не судит
    assert tc.subject_words("нет-такой-семьи", CATALOG) == ()
    without = [f for f in tc.load(CATALOG).families()
               if f not in lex["subject_words"]]
    for f in without:
        assert tc.subject_words(f, CATALOG) == ()


def test_complements_словарь_кортежей():
    tc.forget_current()
    comps = tc.complements(CATALOG)
    assert isinstance(comps, dict)
    assert all(isinstance(v, tuple) for v in comps.values())
    lex = tc.load(CATALOG).lexicon()["complements"]
    assert {k: list(v) for k, v in comps.items()} == lex


def test_current_по_умолчанию_читает_папку_каталога_репозитория():
    tc.forget_current()
    cat = tc.current()
    assert cat.path == tc.DEFAULT_CATALOG_DIR
    assert tc.current() is cat
