# -*- coding: utf-8 -*-
"""Носители машинных условий — в модели (build_vmp_nano.carriers_in_model):
критерий пейринга блоком criterion у целевой, вопросов анкеты нет.
Принципы ИД→ВМП П6/П11; вариант (а) после прогона №3 H0 (2026-09-02).
Файлы эталонов не трогаются — всё в памяти."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "open-bench" / "polyaris"))

import build_vmp_nano as bv  # noqa: E402
from vmp import equivalence as eq  # noqa: E402


def _obj(doc: dict, oid: str) -> dict:
    return next(o for o in doc["objectives"] if o["id"] == oid)


def test_критерий_переезжает_в_целевую_а_вопросы_снимаются():
    base = bv.build()
    doc = bv.carriers_in_model(copy.deepcopy(base))
    assert doc["questions"] == []
    crit = _obj(doc, "O-PAIR")["criterion"]
    assert crit["kind"] == "min_person_days"      # значение словаря CDPM
    assert crit["rate_per_duty"] == 100
    assert crit["version"] == "M-036/demo-1"
    assert crit["effective_from"] == "2026-08-01"
    # as_written — фрагмент СОБСТВЕННОЙ формулировки целевой (аудит №1),
    # и самопроверка генератора это видит.
    assert bv._norm_text(crit["as_written"]) in bv._norm_text(
        _obj(doc, "O-PAIR")["statement"])
    assert "MIN_PERSON_DAYS" in crit["as_written"]
    assert bv.self_consistency_report(doc) == []
    broken = copy.deepcopy(doc)
    _obj(broken, "O-PAIR")["criterion"]["as_written"] = "чужая фраза"
    assert any("O-PAIR" in x for x in bv.self_consistency_report(broken))
    assert "criterion" not in _obj(doc, "O-ROST")
    # Правила и целевые не тронуты числом и якорями.
    assert [r["id"] for r in doc["rules"]] == [r["id"] for r in base["rules"]]
    assert [o["id"] for o in doc["objectives"]] == ["O-PAIR", "O-ROST"]
    assert "в модели" in doc["meta"]["note"]


def test_носители_не_добавляют_нарушений_контракта_cdpm():
    """Перенос носителей не порождает НОВЫХ нарушений контракта CDPM
    сверх тех, что несёт сам build() (эталон судится отдельно: его
    чистоту по контракту r55 печатает генератор при выпуске)."""
    from cdpm import model as m

    def probs(d):
        return set(m.validate_schema(d) + m.validate_model(d)
                   + m.validate_template_fit(d))
    base = bv.build()
    doc = bv.carriers_in_model(copy.deepcopy(base))
    extra = probs(doc) - probs(base)
    assert extra == set(), sorted(extra)


def test_гейт_эталон_с_носителями_и_строгий_режим():
    base = bv.build()
    doc = bv.carriers_in_model(copy.deepcopy(base))
    # Сам с собой — эквивалентен и в строгом режиме (вопросов 0).
    res = eq.equivalence(doc, doc, strict_questions=True)
    assert res["verdict"] == "EQUIVALENT", res["problems"]
    assert res["counts"]["questions_total"] == 0
    # Против прежнего эталона (критерий в ответе, не в модели) — тоже
    # эквивалентен: прежний носителя не объявляет, критерий не судит.
    assert eq.equivalence(doc, base)["verdict"] == "EQUIVALENT"
    # А эталон с носителем от кандидата критерий ТРЕБУЕТ: кандидат без
    # блока — расхождение, с иной ставкой — расхождение.
    cand = copy.deepcopy(doc)
    del _obj(cand, "O-PAIR")["criterion"]
    assert any("целевая O-PAIR" in p
               for p in eq.equivalence(cand, doc)["problems"])
    cand2 = copy.deepcopy(doc)
    _obj(cand2, "O-PAIR")["criterion"]["rate_per_duty"] = 90
    assert any("целевая O-PAIR" in p
               for p in eq.equivalence(cand2, doc)["problems"])
    # Регистр вида критерия — вне сравнения: кандидат пишет вид
    # ПРОПИСНЫМИ (метка словаря CDPM), эталон — значением словаря.
    cand3 = copy.deepcopy(doc)
    assert _obj(cand3, "O-PAIR")["criterion"]["kind"] == "min_person_days"
    _obj(cand3, "O-PAIR")["criterion"]["kind"] = "MIN_PERSON_DAYS"
    assert eq.equivalence(cand3, doc)["verdict"] == "EQUIVALENT"


def test_без_применённого_ответа_части_3a_отказ_словами():
    doc = bv.build()
    doc["questions"] = [q for q in doc["questions"] if q["part"] != "3a"]
    with pytest.raises(ValueError, match="3a"):
        bv.carriers_in_model(doc)


def test_cli_флаг_только_с_kit(capsys):
    assert bv.main(["--carriers-in-model"]) == 2
    assert "только с --kit" in capsys.readouterr().out


def test_cli_без_ключей_ничего_не_пишет(capsys, tmp_path):
    """Нано-эталон заморожен: без --kit/--nano генератор ничего не
    записывает (аудит №1: умолчание перезаписывало нано-эталон)."""
    nano = bv.OUT / "VMP-NANO.json"
    before = nano.read_bytes() if nano.is_file() else None
    assert bv.main([]) == 2
    out = capsys.readouterr().out
    assert "ничего не записано" in out
    assert (nano.read_bytes() if nano.is_file() else None) == before
