# -*- coding: utf-8 -*-
"""Рендеры CDPM: детерминированность и обязательные свойства артефактов.

Свод и анкета рендерятся из ОДНОЙ машинной модели — тесты держат обе
стороны этого решения: одинаковые байты при повторном рендере (на этом
стоит проверка «артефакт правлен мимо модели» в гейте) и присутствие в
артефактах всего, что обещает контракт: каждой строки, каждого вопроса,
маркеров блокировок, трассировок и заготовок ответов."""
import copy

from cdpm import model as m
from cdpm import render
from cdpm.sample import sample_model


def test_renders_are_deterministic():
    doc = sample_model()
    assert render.render_svod(doc) == render.render_svod(copy.deepcopy(doc))
    assert render.render_anketa(doc) == \
        render.render_anketa(copy.deepcopy(doc))


def test_svod_contains_every_rule_question_and_source():
    doc = sample_model()
    svod = render.render_svod(doc)
    for r in doc["rules"]:
        assert r["id"] in svod
    for q in doc["questions"]:
        assert q["id"] in svod
    for s in doc["sources"]:
        assert s["file"] in svod
        assert s["sha256"] in svod


def test_svod_marks_blockers_and_conditional_kinds():
    svod = render.render_svod(sample_model())
    assert "**[БЛОКИРОВАНО — см. Вопрос Q-1-REST]**" in svod
    # Условный тип: ветви в порядке ключей ответа + вопрос, решающий
    # развилку, — как в контракте компиляции свода пилотной линии.
    assert "SOFT/HARD — по Вопросу Q-2-MODAL" in svod


def test_svod_has_no_answer_slots_but_anketa_has_one_per_question():
    """Заготовка «Ответ:» — свойство анкеты (у отвечающего есть куда
    писать, уроки анкет v4–v7); в своде вопросы — блокеры, им заготовка
    не положена."""
    doc = sample_model()
    assert "Ответ:" not in render.render_svod(doc)
    anketa = render.render_anketa(doc)
    assert anketa.count("Ответ:") == len(doc["questions"])


def test_anketa_contains_parts_traces_and_formats():
    doc = sample_model()
    anketa = render.render_anketa(doc)
    for part in ("1", "2", "3a", "3b"):
        assert m.PART_TITLES[part] in anketa
    assert m.PART_TITLES["4"] not in anketa      # режим 1 — части 4 нет
    # Трассировка пункта — к строкам свода И к источникам (файл + место).
    assert "POLARIS-OM-A.docx, П.1.4" in anketa
    assert "R-S1-П-1-4, R-S2-П-5-1" in anketa
    # Замкнутый формат ответа: развилка перечисляет варианты.
    assert "а) 12 часов (колдоговор сильнее)" in anketa
    assert "реестр (приложить перечень)" in anketa


def test_table_cells_escape_pipes_and_newlines():
    """Вертикальная черта и перенос строки в тексте правила не должны
    ломать md-таблицу свода."""
    doc = sample_model()
    doc["rules"][0]["statement"] = "a | b\nc"
    svod = render.render_svod(doc)
    assert "a \\| b<br>c" in svod


def test_render_main_writes_both_files(tmp_path, capsys):
    doc = sample_model()
    model_path = tmp_path / "model.json"
    m.dump_model(doc, model_path)
    rc = render.main([str(model_path), "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr().out
    svod = tmp_path / "out" / "svod.md"
    anketa = tmp_path / "out" / "anketa.md"
    assert svod.is_file() and anketa.is_file()
    # Полные пути в выводе — рабочее соглашение владельца (У-10).
    assert str(svod.resolve()) in out
    assert str(anketa.resolve()) in out
    assert svod.read_text(encoding="utf-8") == render.render_svod(doc)
    assert anketa.read_text(encoding="utf-8") == render.render_anketa(doc)
