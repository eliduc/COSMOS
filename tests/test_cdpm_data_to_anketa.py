# -*- coding: utf-8 -*-
"""Вынос находок отчёта 3.3 в анкету (ДЛ-6).

Охраняемое: вопрос части 2 со стабильным идентификатором и
трассировкой к строкам-основаниям группы проверок; строки-основания
блокируются до ответа (fail-closed); повторный вынос той же группы —
честный отказ; модель после выноса проходит контракт П-1."""
import json

import pytest

from cdpm import data_to_anketa as da
from cdpm import model as m
from cdpm.sample import sample_model


def _report():
    return {"customer": "Полярис", "sources": [], "findings": [],
            "checks": [
                {"id": "C-A-0", "basis": {"rule": "R-S2-DATA-QUAL"},
                 "statement": "т", "unit": "строка",
                 "status": "прогнана", "rows_checked": 1},
                {"id": "C-B-0", "basis": {"rule": "R-S1-CASE-11"},
                 "statement": "т", "unit": "строка",
                 "status": "не прогнана", "rows_checked": 0,
                 "reason": "п"},
                {"id": "C-встр-0", "basis": {"builtin": "дубль"},
                 "statement": "т", "unit": "строка",
                 "status": "прогнана", "rows_checked": 1},
            ]}


def test_promote_adds_question_blocks_rules_and_passes_contract():
    svod = sample_model()
    qid = da.promote_to_question(
        svod, _report(), ["C-A-0", "C-B-0"],
        "Как понимать правило?",
        "Проверки данных вскрыли спорное чтение — поясните.",
        [{"key": "а", "text": "чтение 1"},
         {"key": "б", "text": "чтение 2"}])
    q = next(x for x in svod["questions"] if x["id"] == qid)
    assert q["part"] == "2"
    assert q["answer_format"]["type"] == "choice"
    assert [o["key"] for o in q["answer_format"]["options"]]         == ["а", "б"]
    assert set(q["rule_refs"]) == {"R-S2-DATA-QUAL", "R-S1-CASE-11"}
    for rid in q["rule_refs"]:
        rule = next(r for r in svod["rules"] if r["id"] == rid)
        assert qid in rule["blockers"]      # fail-closed до ответа
    assert q["source_refs"]                 # трассировка к источникам
    assert m.validate_schema(svod) + m.validate_model(svod) == []

    # Повторный вынос той же группы — честный отказ.
    with pytest.raises(da.PromoteError, match="уже есть"):
        da.promote_to_question(svod, _report(), ["C-A-0", "C-B-0"],
                               "т", "т",
                               [{"key": "а", "text": "т"}])


def test_promote_fails_closed_on_bad_input():
    svod = sample_model()
    with pytest.raises(da.PromoteError, match="нет в отчёте"):
        da.promote_to_question(svod, _report(), ["C-НЕТ"], "т", "т",
                               [{"key": "а", "text": "т"}])
    with pytest.raises(da.PromoteError, match="без строки-основания"):
        da.promote_to_question(svod, _report(), ["C-встр-0"], "т",
                               "т", [{"key": "а", "text": "т"}])


def test_cli_renders_and_validates(tmp_path, capsys):
    m.dump_model(sample_model(), tmp_path / "model.json")
    (tmp_path / "report.json").write_text(
        json.dumps(_report(), ensure_ascii=False), encoding="utf-8")
    rc = da.main([str(tmp_path / "model.json"),
                  str(tmp_path / "report.json"),
                  "--checks", "C-A-0",
                  "--title", "Как понимать правило?",
                  "--text", "Поясните чтение.",
                  "--options", "а=чтение 1;б=чтение 2",
                  "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "вопрос добавлен" in out
    got = json.loads((tmp_path / "out" / "model.json")
                     .read_text(encoding="utf-8"))
    assert any("Как понимать правило?" == q["title"]
               for q in got["questions"])
    anketa = (tmp_path / "out" / "anketa.md").read_text(
        encoding="utf-8")
    assert "Как понимать правило?" in anketa
