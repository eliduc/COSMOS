"""Offline G2 regressions: public schema vocabulary is not customer prose.

The source values/oracles are unchanged. A standard term used as an actual
sheet address, customer identity, source identifier or parameter code remains
a marker. No HTTP, LLM, service, solver or production run is involved.
"""
import json
import re
import socket
from pathlib import Path

import pytest

from cdpm import catalog_requests as cr
import template_catalog as tc


class EmptyPublicCatalog:
    def __init__(self, text=""):
        self.text = text

    def body(self):
        return {"note": self.text}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("This profile is offline")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(cr, "_marker_patterns", lambda: [re.compile("PrivateAir")])


def context(tmp_path, sheet="Отпуска", *, customer="", code=None, filename=None, public_text=""):
    root = tmp_path / "run"
    data = root / "data-freeze"
    data.mkdir(parents=True)
    (data / "dataset.json").write_text(json.dumps({
        "tables": {"D1": {sheet: {"columns": ["ОсобаяКолонка"]}}}
    }, ensure_ascii=False), encoding="utf-8")
    if filename:
        freeze = root / "freeze"
        freeze.mkdir()
        (freeze / "manifest.json").write_text(json.dumps({
            "files": [{"sid": "S1", "file": filename}]
        }, ensure_ascii=False), encoding="utf-8")
    doc = {"rules": [], "questions": []}
    if code:
        doc["rules"] = [{"id": "R-test", "kind": "HARD", "zone": "ROSTERING",
                         "params": [{"name": "base_codes", "unit": "code_list", "value": code}]}]
    return cr.run_context(root, doc, {"customer": customer}, EmptyPublicCatalog(public_text))


def hits(ctx, text):
    return [(kind, value) for kind, value, pattern in ctx["markers"] if pattern.search(text)]


@pytest.mark.parametrize("text", [
    "Недоступность члена экипажа в период отпуска",
    "Запрет назначения сотрудника на полётные задания в дни отпуска",
    "Календарь отпуска является операционными данными, а не порогом нормы",
])
def test_public_vacation_term_is_not_customer_sheet_address(tmp_path, text):
    assert hits(context(tmp_path), text) == []


@pytest.mark.parametrize("sheet,prose", [
    ("Рейсы", "Рейсы назначаются сотрудникам"),
    ("Экипажи", "Экипажи комплектуются по численности"),
    ("Допуски", "Допуски учитываются при назначении"),
    ("Отпуска", "Периоды отпуска блокируют назначение"),
    ("Смены", "Смены ограничены по длительности"),
])
def test_all_fixed_product_sheet_terms_have_same_semantic_law(tmp_path, sheet, prose):
    assert hits(context(tmp_path, sheet), prose) == []


@pytest.mark.parametrize("address", [
    "лист Отпуска", "на листе «Отпуска»", "из листа Отпуска",
    "вкладка Отпуска", "таблица: Отпуска", "колонка 'Отпуска'",
    "sheet Отпуска", "worksheet Отпуска", "«Отпуска»", '"Отпуска"',
    "Отпуска!A2", "'Отпуска'!A2", "D1/Отпуска", r"D1\Отпуска",
    "Отпуска/строка", "Отпуска[2]", "`Отпуска`", "Отпуска.xlsx",
    "лист с именем Отпуска", "лист под названием Отпуска",
    "на листе данных Отпуска", "таблица Excel Отпуска", "лист — Отпуска",
    "Отпуска — лист исходных данных", "[Отпуска]", "Отпуска.xlsm", "Отпуска.ods",
])
def test_explicit_standard_sheet_addresses_still_refused(tmp_path, address):
    assert hits(context(tmp_path), address)


@pytest.mark.parametrize("sheet", ["ЛичныеОтпуска", "Отпуска перевозчика", "PrivateAir"])
def test_arbitrary_customer_sheet_names_remain_strict(tmp_path, sheet):
    assert hits(context(tmp_path, sheet), "механизм " + sheet)


def test_standard_term_as_customer_identity_stays_strict(tmp_path):
    assert any("авиакомпании" in k for k, _ in hits(context(tmp_path, customer="Отпуска"), "период отпуска"))


def test_standard_term_as_code_is_not_hidden_by_sheet_marker_dedup(tmp_path):
    assert any("код" in k for k, _ in hits(context(tmp_path, code="Отпуска"), "период отпуска"))


def test_filename_and_nonstandard_column_are_not_public_terms(tmp_path):
    ctx = context(tmp_path, filename="Отпуска.xlsx")
    assert hits(ctx, "проверьте Отпуска.xlsx")
    assert hits(ctx, "свойство ОсобаяКолонка")
    assert hits(ctx, "источник D1")


def test_existing_catalog_safe_word_does_not_erase_explicit_schema_reference(tmp_path):
    ctx = context(tmp_path, public_text="отпуска")
    assert hits(ctx, "на листе Отпуска")


def request_with_prose(text):
    statement = "Механизм индивидуальной недоступности"
    key = cr.request_key("HARD", "ROSTERING", statement)
    row = {"kind": "HARD", "zone": "ROSTERING", "statement": statement,
           "key": key, "request_id": cr.request_id_of(key), "gap": "новая механика"}
    fields = {"id": "new_public_term_probe", "title": "Ограничение назначений",
              "subject": text, "nature": "schedule", "params": [],
              "violation_unit": None, "violation_size": None, "reads_history": None,
              "semantics": {"scope": "сотрудник", "unit": "дни",
                            "measure": "недопустимое назначение", "text": text}}
    draft = cr.build_draft(row, fields, draft_id=fields["id"],
                          document_class=cr.DEFAULT_DOCUMENT_CLASS,
                          origin_date="2026-09-20")
    return cr.assemble_request(row, draft, document_class=cr.DEFAULT_DOCUMENT_CLASS,
                               run_name="run-0090", submitted_at="2026-09-20T13:00:00Z")


def live_gate(tmp_path, text):
    cat = tc.load(Path(__file__).resolve().parents[1] / "catalog-integration/0.8.0")
    request = request_with_prose(text)
    return cr.gate([request], cat, context(tmp_path), {})[request["request_id"]]


def test_real_gate_healthy_public_semantics(tmp_path):
    verdict = live_gate(tmp_path, "Календарные периоды отпуска исключают назначение сотрудника")
    assert verdict["hard"] == []
    assert verdict["soft"] == []


@pytest.mark.parametrize("text", ["на листе Отпуска", "Кадровое свойство ОсобаяКолонка", "из D1/Отпуска"])
def test_real_gate_retains_g2_source_reference_refusal(tmp_path, text):
    assert any("[Г2]" in p for p in live_gate(tmp_path, text)["hard"])


@pytest.mark.parametrize("text", ["HARD/ROSTERING", "PrivateAir", "C:/private/row"])
def test_real_gate_keeps_original_a05_and_private_marker_refusals(tmp_path, text):
    assert any("[Г1" in p for p in live_gate(tmp_path, text)["hard"])


def test_prompt_keeps_scope_and_operational_data_separate():
    cat = tc.load(Path(__file__).resolve().parents[1] / "catalog-integration/0.8.0")
    prompt = cr.compose_prompt({"kind": "HARD", "zone": "ROSTERING", "statement": "проба"}, cat)
    assert "это не заголовки" in prompt
    assert "не коды вида" in prompt
    assert "не расширять до дежурств" in prompt
    assert "params может быть пустым списком" in prompt
    assert "не становятся параметрами нормы" in prompt
    assert "для него reads_history не добавлять" in prompt


def test_public_data_contract_is_the_package_reader_contract():
    from vmp import data_contract
    from webapp import task_package
    fields = ("SHEET_FLIGHTS", "SHEET_CREW", "SHEET_QUALS", "SHEET_VACATIONS", "SHEET_DUTIES")
    assert {getattr(task_package, k) for k in fields} == data_contract.STANDARD_DATA_SHEETS
    assert all(getattr(task_package, k) == getattr(data_contract, k) for k in fields)
