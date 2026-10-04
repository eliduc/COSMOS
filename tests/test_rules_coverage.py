"""Тесты таблицы покрытия правил (`_rules_coverage.py`).

Фикстура синтетическая. Проверяется главное свойство инструмента: он
обязан отличать правило, дошедшее до решателя, от правила, которое лишь
попало в Пакет правил, и не имеет права молча пропускать код, статус
которого не объявлен, — иначе непокрытое читается как выполненное.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TOOL = REPO / "_rules_coverage.py"

_rc = pytest.importorskip("_rules_coverage")


def _rule(code: str, weight: str) -> dict:
    return {"title": code, "statement": code,
            "constraint": {"kind": "note",
                           "note": json.dumps({"vendor_constraint": code,
                                               "weight_level": weight})}}


def _pack(*rules) -> dict:
    return {"name": "тест", "rules": list(rules)}


def test_supported_rule_is_counted_as_full_coverage():
    pack = _pack(_rule("ASSIGNMENT_CONFLICT", "HIGH_HIGH"))
    table = _rc.rows(pack, {"constraints": {
        "ASSIGNMENT_CONFLICT": {"data": "+", "note": "пересечения"}}})
    assert table[0]["check"] == table[0]["solver"] == "+"
    t = _rc.totals(table)
    assert (t["full"], t["partial"], t["missing"], t["off"]) == (1, 0, 0, 0)


def test_rule_in_the_pack_but_not_in_the_solver_is_not_covered():
    """Правило, попавшее в Пакет правил, но не дошедшее до решателя,
    иначе выглядит выполненным — именно это и надо назвать вслух."""
    pack = _pack(_rule("GENDER_ACCOMMODATION", "STD"))
    table = _rc.rows(pack, {"constraints": {
        "GENDER_ACCOMMODATION": {"data": "нет данных"}}})
    assert table[0]["solver"] == "-"
    t = _rc.totals(table)
    assert (t["full"], t["missing"]) == (0, 1)


def test_disabled_rule_is_counted_separately():
    pack = _pack(_rule("ASSIGNMENT_CONFLICT", "OFF"))
    table = _rc.rows(pack)
    t = _rc.totals(table)
    assert table[0]["data"] == "n/a" and "выключено" in table[0]["note"]
    assert (t["off"], t["active"]) == (1, 0)


def test_unknown_code_is_named_loudly():
    """Код, статуса которого продукт не объявлял, обязан попасть в отчёт
    и в счётчик неклассифицированного, а не исчезнуть."""
    pack = _pack(_rule("СОВСЕМ_НОВОЕ_ПРАВИЛО", "HIGH"))
    table = _rc.rows(pack)
    assert table[0]["check"] == _rc.UNKNOWN
    assert "НЕ КЛАССИФИЦИРОВАНО" in table[0]["note"]
    t = _rc.totals(table)
    assert t["unclassified"] == 1
    text = _rc.as_markdown(table, t)
    assert "Не классифицировано: 1" in text


def test_overlay_supplies_dataset_facts_only():
    """Что умеет продукт — в коде; есть ли данные у конкретного
    заказчика — в наложении.

    Прежде наложению позволялось уточнять и столбцы продукта, и этот тест
    такое поведение закреплял. Аудит 2026-07-26 (P2-02) показал, чем оно
    оборачивается: наложение объявляло поддержку, которой в коде нет.
    Теперь способность приходит ТОЛЬКО из таблицы поддержки, а попытка
    задать её называется вслух."""
    pack = _pack(_rule("WEEKLY_REST", "HIGH_HIGH"))
    plain = _rc.rows(pack)
    assert plain[0]["data"] == _rc.UNKNOWN and plain[0]["check"] == "+"
    over = _rc.rows(pack, {"constraints": {
        "WEEKLY_REST": {"data": "+", "note": "калибровка по учёту"}}})
    assert over[0]["data"] == "+" and over[0]["note"] == "калибровка по учёту"
    forced = _rc.rows(pack, {"constraints": {
        "WEEKLY_REST": {"data": "+", "solver": "-"}}})
    assert forced[0]["solver"] == _rc.SUPPORT["WEEKLY_REST"][1], \
        "наложение не имеет права переписывать способность продукта"


def test_rows_are_ordered_by_weight_then_code():
    pack = _pack(_rule("WEEKLY_REST", "STD"),
                 _rule("ASSIGNMENT_CONFLICT", "HIGH_HIGH"),
                 _rule("UNASSIGNED", "OFF"))
    codes = [r["code"] for r in _rc.rows(pack)]
    assert codes == ["ASSIGNMENT_CONFLICT", "WEEKLY_REST", "UNASSIGNED"]


def test_rules_without_vendor_origin_are_ignored():
    """В Пакете правил лежат и наши собственные записи; таблица покрытия
    говорит о правилах заказчика и чужие строки не выдумывает."""
    pack = _pack(_rule("WEEKLY_REST", "STD"))
    pack["rules"].append({"title": "наше", "constraint": {"kind": "parameter",
                                                          "parameter": "x",
                                                          "value": 1}})
    assert [r["code"] for r in _rc.rows(pack)] == ["WEEKLY_REST"]


def test_parameters_table_names_the_unclassified():
    vendor = {"parameters": {"planSince": "...", "неведомыйПараметр": 1}}
    params = _rc.parameters_table(vendor)
    assert dict(params)["planSince"] == "периметр планирования"
    assert dict(params)["неведомыйПараметр"].startswith("??")
    text = _rc.as_markdown([], _rc.totals([]), params)
    assert "Неклассифицированных параметров: 1" in text


def test_cli_exits_nonzero_when_something_is_not_covered(tmp_path):
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps(_pack(_rule("GENDER_ACCOMMODATION", "STD"))),
                    encoding="utf-8")
    out = tmp_path / "coverage.md"
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--pack", str(pack), "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "НЕ покрыто 1" in out.read_text(encoding="utf-8")


def test_no_network_or_model_calls_in_source():
    src = TOOL.read_text(encoding="utf-8")
    for needle in ("requests", "httpx", "urllib", "socket", "openai",
                   "anthropic", "claude_cli", "ollama"):
        assert needle not in src, f"недопустимый вызов: {needle}"


def test_overlay_cannot_invent_product_support(capsys):
    """Аудит 2026-07-26 (P2-02): наложение набора данных могло объявить
    поддержку, которой в коде нет — неподдерживаемое правило синтетически
    превращалось в полностью покрытое. Наложение сообщает ФАКТЫ О ДАННЫХ;
    способность продукта приходит только из таблицы поддержки."""
    pack = _pack(_rule("GENDER_ACCOMMODATION", "HIGH"))
    overlay = {"constraints": {"GENDER_ACCOMMODATION": {
        "data": "+", "check": "+", "solver": "+", "applied": "+"}}}
    row = _rc.rows(pack, overlay)[0]
    assert row["data"] == "+", "факт о данных наложение задавать вправе"
    sup = _rc.SUPPORT.get("GENDER_ACCOMMODATION")
    assert (row["check"], row["solver"], row["applied"]) == (
        sup[0] if sup else _rc.UNKNOWN,
        sup[1] if sup else _rc.UNKNOWN,
        sup[2] if sup else _rc.UNKNOWN)
    assert _rc.totals(_rc.rows(pack, overlay))["full"] == 0, \
        "неподдерживаемое правило не имеет права стать полностью покрытым"
    assert "проигнорированы" in capsys.readouterr().out


def test_overlay_may_still_carry_data_facts_and_notes():
    pack = _pack(_rule("GENDER_ACCOMMODATION", "HIGH"))
    row = _rc.rows(pack, {"constraints": {"GENDER_ACCOMMODATION": {
        "data": "+", "note": "поле есть в выгрузке"}}})[0]
    assert row["data"] == "+" and row["note"] == "поле есть в выгрузке"
