# -*- coding: utf-8 -*-
"""Каноническая текстовая форма ВМП читается кодом (cdpm/form_text):
биекция запись ↔ строка на эталоне и на полном составе полей; набор
«ИД H0-ВМП» (три файла) → форма → модель эквивалентна эталону H0 в
строгом режиме без языковой модели; чужая строка не распознаётся молча."""
import copy
import json
from pathlib import Path

from cdpm import form_text as ft, freeze as fz, model as m, vmp_form as vf
from vmp import equivalence as eq

REPO = Path(__file__).resolve().parent.parent
KIT = REPO / "open-bench" / "polyaris-h0"
ETALON = KIT / "etalon" / "VMP-H0.json"
ETALON_FORM = KIT / "etalon" / "VMP-H0-form.json"


def _public(row: dict) -> dict:
    out = {k: copy.deepcopy(v) for k, v in row.items()
           if k not in ("sources", "machine")}
    if isinstance(out.get("rate"), dict):
        out["rate"].pop("as_written", None)     # цитата — не сравнивается
    return out


def test_row_line_bijection_on_etalon_form():
    form = json.loads(ETALON_FORM.read_text(encoding="utf-8"))
    n = 0
    for _part, sec, rows in vf.form_sections(form):
        for row in rows:
            line = vf.render_row_line(row, sec)
            got = ft.parse_row_line(line, sec)
            assert got is not None, line
            assert _public(got) == _public(row), (sec, row["id"])
            n += 1
    # Число записей считается ПО ФОРМЕ: набор растёт (парк, должности,
    # допуск по типу — 03.09), и литерал сделал бы тест хранителем
    # состава, а не биекции «строка формы ↔ человеческая строка».
    assert n == sum(len(rows) for _p, _s, rows in vf.form_sections(form))
    assert n > 0


def test_row_line_bijection_on_full_field_set():
    rows = {
        "hard": {"id": "R-1", "title": "Исключение", "statement": "Особый случай.",
                 "template": None, "gap": "нужна семья особых случаев",
                 "case": True, "note": "из листа исключений",
                 "excluded_until_data": {"question": "Q-3b-нет-данных-R-1",
                                         "revision": 2}},
        "soft": {"id": "R-2", "title": "Ночёвки", "statement": "Штраф 10 за ночь.",
                 "template": {"family": "layover_penalty", "category": "B",
                              "assumption": "ночь = ночёвка вне базы",
                              "quote": "Штраф 10 за ночь"},
                 "penalty": {"coefficient": 10, "violation_unit": "night"}},
        "world": {"id": "R-3", "title": "Состав", "statement": "Экипаж — 2 пилота.",
                  "template": {"family": "crew_composition", "category": "A"},
                  "params": [{"name": "crew_size", "value": 2, "unit": "count",
                              "quote": "2 пилота"}],
                  "scope": {"pairing": "применяется", "rostering": "не применяется"}},
        "criteria": {"id": "O-1", "title": "Стоимость", "statement": "Минимум стоимости.",
                     "direction": "minimize", "metric": "total_pairing_cost",
                     "tier": 2, "tolerance": {"value": 5, "unit": "percent"},
                     "kind": "rank_normalized_cost",
                     "rate": {"rates": {"flight": 1.0, "work": 0.7}},
                     "version": "v2", "effective_from": "2026-09-01",
                     "note": "второй ярус"},
        "definitions": {"id": "D-1", "title": "Базы", "statement": "Базы: AAA, BBB.",
                        "role": "registry",
                        "excluded_until_data": {"question": "Q-x", "revision": 1}},
    }
    for sec, row in rows.items():
        line = vf.render_row_line(row, sec)
        got = ft.parse_row_line(line, sec)
        assert got is not None, line
        assert _public(got) == _public(row), (sec, line)


def test_foreign_lines_are_not_rows():
    assert ft.parse_row_line("Экипажи отдыхают не менее 12 часов.", "hard") is None
    assert ft.parse_row_line("VN-1. Отдых. Не менее 12 часов. Шаблон: rest_floors "
                             "(категория Z).", "hard") is None
    # Запись правила в разделе определений — не запись раздела.
    assert ft.parse_row_line("VN-1. Отдых. Не менее 12 часов. Шаблон: rest_floors "
                             "(категория A).", "definitions") is None
    entries = [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64, "clauses": [
        {"anchor": "абз.1", "text": vf.SECTION_TITLES[("pairing", "hard")]},
        {"anchor": "абз.2", "text": "Просто фраза документа."}]}]
    form, rep = ft.read_form_text(entries)
    assert rep["rows"] == 0 and len(rep["unrecognized"]) == 1
    assert rep["unrecognized"][0]["anchor"] == "абз.2"


def test_kit_three_files_read_by_code_are_equivalent_to_etalon_strict():
    """Набор ИД H0-ВМП: docx и xlsx — штатной заморозкой, jpg — строками
    генератора (зрение отдельно) → форма → модель → гейт строгий:
    EQUIVALENT, вопросов 0, без языковой модели."""
    manifest = json.loads((KIT / "kit-manifest.json").read_text(encoding="utf-8"))
    docx, xlsx, jpg = [KIT / p for p in manifest["rules_files"]]
    jpg_lines = json.loads((KIT / "etalon" / "jpg-lines.json").read_text(encoding="utf-8"))
    entries = [
        {"sid": "S1", "file": docx.name, "sha256": fz.sha256_of(docx),
         "clauses": fz._extract_docx(docx)},
        {"sid": "S2", "file": xlsx.name, "sha256": fz.sha256_of(xlsx),
         "clauses": fz._extract_xlsx(xlsx)},
        {"sid": "S3", "file": jpg.name, "sha256": fz.sha256_of(jpg),
         "clauses": [{"anchor": f"стр-1-абз-{n}", "kind": "p", "text": t}
                     for n, t in enumerate(jpg_lines, start=1)]},
    ]
    form, rep = ft.read_form_text(entries)
    assert rep["unrecognized"] == []
    etalon_form = json.loads(ETALON_FORM.read_text(encoding="utf-8"))
    want_rows = sum(len(rows) for _p, _s, rows in vf.form_sections(etalon_form))
    assert rep["rows"] == want_rows and rep["headings"] == 8         and rep["headers"] == 3
    assert vf.validate_form(form, strict=True) == []
    model = ft.model_from_form_text(form)
    assert m.validate_schema(model) == []
    assert m.validate_model(model) == []
    assert m.validate_template_fit(model) == []
    assert model["questions"] == []
    etalon = json.loads(ETALON.read_text(encoding="utf-8"))
    res = eq.equivalence(model, etalon, strict_questions=True)
    assert res["verdict"] == "EQUIVALENT", res["problems"]
    c = res["counts"]
    # Числа берутся из САМОГО эталона: охраняемое свойство — «покрыто
    # ровно столько, сколько в эталоне, и ни одного вопроса», а не
    # конкретный состав набора, который растёт.
    assert c["covered"] == c["etalon_rules"] == len(etalon["rules"])
    assert c["objectives_ok"] == c["objectives_etalon"]         == len(etalon["objectives"])
    assert c["questions_total"] == 0
    assert c["form_rows_ok"] == c["form_rows"] == want_rows
    assert eq.diff_forms(form, etalon_form,
                         strict_questions=True)["problems"] == []
