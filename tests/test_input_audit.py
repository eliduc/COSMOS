"""Тесты входного аудита (`_input_audit.py`).

Проверяется главное свойство инструмента: он обязан различать «данные
вне нормы» и «наша трактовка правила под подозрением» по доле
нарушений, и обязан называть правила, которые нельзя объявлять
жёсткими на этом наборе.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_ia = pytest.importorskip("_input_audit")


#: Ярусная норма отдыха — данные КОНКРЕТНОЙ авиакомпании; в продукте её
#: нет, она подаётся файлом. В тестах — синтетическая, вымышленная.
REST_TABLE = {"base": 720, "base_long": 2160, "off": 760, "off_long": 1480,
              "shift": 1440, "shift_long": 2880, "tz_shift_hours": 3.0}


def _args(**over) -> argparse.Namespace:
    base = dict(bases="BASE", pairing_span=6 * 1440, duty_span=760,
                min_rest_sit=30, min_rest_min=600,
                rest_table=dict(REST_TABLE),
                rest_model="ярусная таблица авиакомпании")
    base.update(over)
    return argparse.Namespace(**base)


def _leg(num, dep, arr, start, minutes):
    t0 = datetime.fromisoformat(start)
    return {"flightNumber": num, "departureAirport": dep,
            "arrivalAirport": arr, "departureUTCDateTime": t0.isoformat(),
            "arrivalUTCDateTime": (t0 + timedelta(minutes=minutes)).isoformat()}


def _payload(flights):
    return {"airports": [], "employees": [{"id": "E1", "homeAirport": "BASE"}],
            "flights": flights, "flightAssignments": []}


def _unit(duties, riders=(), **over):
    """Юнит каталога. `riders` — перелёты пассажиром: они входят в
    командировку (`fns`), но не в рабочую структуру (`active_fns`)."""
    flat = [x for d in duties for x in d]
    rec = {"fns": list(riders) + flat, "active_fns": flat,
           "duty_fns": duties, "kind": "chain", "sourceLegsMissing": 0}
    rec.update(over)
    return rec


def test_clean_structure_passes():
    """Оба плеча в ОДНОЙ смене: вылет из базы и возврат в тот же день,
    стыковка два часа. Нарушений быть не должно — а вот если те же
    плечи объявить двумя сменами подряд, аудит обязан отметить нехватку
    отдыха между ними (см. следующий тест)."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    rep = _ia.audit(payload, {"selected": [_unit([["F1", "F2"]])]}, _args())
    assert rep["hard_candidates_rejected"] == []
    assert all(r["violations"] == 0 for r in rep["rules"])


def test_unclosed_chain_is_caught():
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BBB", "2026-08-01T14:00:00", 120)])
    rep = _ia.audit(payload, {"selected": [_unit([["F1"], ["F2"]])]}, _args())
    row = next(r for r in rep["rules"] if "не замкнута" in r["rule"])
    assert row["violations"] == 1
    assert "Цепочка не замкнута на базу" in rep["hard_candidates_rejected"]


def test_short_rest_between_duties_is_caught():
    """Между сменами вне базы разрыв меньше требуемого отдыха."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T13:00:00", 120)])
    rep = _ia.audit(payload, {"selected": [_unit([["F1"], ["F2"]])]}, _args())
    row = next(r for r in rep["rules"] if "Отдых" in r["rule"])
    assert row["violations"] == 1


def _trip_with_positioning() -> tuple[dict, dict]:
    """Командировка, у которой края — перелёты пассажиром: экипаж уезжает
    из базы и возвращается в неё пассажирским местом, а работает вдали от
    базы. По рабочим участкам такая цепочка выглядит незамкнутой, хотя на
    самом деле замкнута."""
    payload = _payload([
        _leg("P0", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F1", "AAA", "BBB", "2026-08-01T10:00:00", 120),
        _leg("F2", "BBB", "AAA", "2026-08-01T14:00:00", 120),
        _leg("P1", "AAA", "BASE", "2026-08-01T18:00:00", 120)])
    return payload, _unit([["F1", "F2"]], riders=["P0", "P1"])


def test_positioning_legs_anchor_the_chain_to_base():
    payload, unit = _trip_with_positioning()
    rep = _ia.audit(payload, {"selected": [unit]}, _args())
    row = next(r for r in rep["rules"] if "не замкнута" in r["rule"])
    assert row["violations"] == 0, \
        "перелёт пассажиром — часть командировки, он и держит базу"
    assert row["checked"] == 1
    assert rep["hard_candidates_rejected"] == []


def test_fill_unit_is_not_judged_as_a_chain():
    """Достройка адаптера — одиночный участок, у которого нет ни начала,
    ни конца командировки. Судить её замкнутость — значит завышать число
    нарушений на ровном месте."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120)])
    fill = _unit([["F1"]], kind="fill")
    fill.pop("sourceLegsMissing")
    rep = _ia.audit(payload, {"selected": [fill]}, _args())
    row = next(r for r in rep["rules"] if "не замкнута" in r["rule"])
    assert row["checked"] == 0 and row["violations"] == 0
    assert rep["units_fill"] == 1 and rep["chains_judged"] == 0
    assert rep["hard_candidates_rejected"] == []


def test_truncated_chain_is_not_judged():
    """Цепочка, у которой часть участков досталась другому юниту или
    отсечена окном импорта, теряет начало или конец при стыковке, а не по
    вине заказчика."""
    payload = _payload([
        _leg("F1", "AAA", "BBB", "2026-08-01T06:00:00", 120),
        _leg("F2", "BBB", "AAA", "2026-08-01T14:00:00", 120)])
    unit = _unit([["F1"], ["F2"]], sourceLegsMissing=2)
    rep = _ia.audit(payload, {"selected": [unit]}, _args())
    row = next(r for r in rep["rules"] if "не замкнута" in r["rule"])
    assert row["checked"] == 0
    assert rep["chains_truncated"] == 1 and rep["chains_judged"] == 0
    # Смены у урезанной цепочки настоящие: отдых между ними судится.
    assert any("Отдых" in r["rule"] and r["checked"] == 1
               for r in rep["rules"])


def test_report_states_the_perimeter():
    """Число нарушений, прочитанное в отрыве от того, что судилось,
    вводит в заблуждение: периметр обязан быть в отчёте и в тексте."""
    payload, chain = _trip_with_positioning()
    fill = _unit([["P0"]], kind="fill")
    fill.pop("sourceLegsMissing")
    rep = _ia.audit(payload, {"selected": [chain, fill]}, _args())
    assert rep["units"] == 2
    assert rep["chains_judged"] == 1
    assert rep["units_fill"] == 1
    assert rep["chains_truncated"] == 0
    assert rep["kind_marked"] is True
    text = _ia.as_markdown(rep, "тест")
    assert "Периметр" in text and "целым" in text


def test_catalog_without_kind_keeps_old_behaviour_and_says_so():
    """Каталог прежнего импорта пометки вида не несёт. Менять его разбор
    молча нельзя: судим всё подряд, но говорим об этом вслух."""
    payload, unit = _trip_with_positioning()
    old = {k: v for k, v in unit.items()
           if k not in ("kind", "sourceLegsMissing")}
    rep = _ia.audit(payload, {"selected": [old]}, _args())
    assert rep["kind_marked"] is False
    assert rep["chains_judged"] == 1
    assert "перезапустите импорт" in _ia.as_markdown(rep, "тест").lower()


def test_empty_perimeter_is_not_the_same_as_no_violations():
    """Ноль проверок даёт ноль нарушений. Конвейер, читающий только код
    возврата, прочёл бы это как «всё чисто» (аудит 2026-07-26)."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120)])
    # каталог о ДРУГОМ наборе: ни один его участок не известен
    alien = {"selected": [_unit([["ЧУЖОЙ"]])]}
    rep = _ia.audit(payload, alien, _args())
    assert rep["nothing_checked"] is True
    assert all(r["checked"] == 0 for r in rep["rules"])
    assert rep["hard_candidates_rejected"] == []
    text = _ia.as_markdown(rep, "тест")
    assert "Проверять было нечего" in text
    assert "НЕ означает" in text


def test_partially_unchecked_rules_are_named():
    """Правило, которому не досталось ни одной проверки, обязано быть
    названо: его «ноль нарушений» ничего не подтверждает."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    rep = _ia.audit(payload, {"selected": [_unit([["F1", "F2"]])]}, _args())
    assert rep["nothing_checked"] is False
    # одна смена — переходов между сменами нет
    assert "Отдых между сменами ниже ярусной таблицы" \
        in rep["rules_unchecked"]
    assert "ничего не подтверждает" in _ia.as_markdown(rep, "тест")


def test_cli_exit_codes(tmp_path):
    """0 — чисто, 2 — есть нарушенные правила, 3 — периметр пуст."""
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]

    def run(payload, catalog):
        p = tmp_path / "p.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        c = tmp_path / "c.json"
        c.write_text(json.dumps(catalog), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(repo / "_input_audit.py"),
             "--payload", str(p), "--catalog", str(c), "--bases", "BASE",
             "--out", str(tmp_path / "rep.json")],
            capture_output=True, text=True, cwd=repo,
            encoding="utf-8", errors="replace")
        return proc.returncode

    clean = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    assert run(clean, {"selected": [_unit([["F1", "F2"]])]}) == 0
    broken = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BBB", "2026-08-01T14:00:00", 120)])
    assert run(broken, {"selected": [_unit([["F1"], ["F2"]])]}) == 2
    assert run(clean, {"selected": [_unit([["ЧУЖОЙ"]])]}) == 3


def test_verdict_separates_data_fault_from_our_reading():
    """Ключевое свойство: по доле нарушений вердикт меняется. Одна
    нарушенная цепочка из тридцати — исключение в данных; двадцать из
    тридцати — повод пересмотреть свою трактовку, а не данные."""
    assert "исключения" in _ia.verdict(1, 30)
    assert "наша трактовка" in _ia.verdict(20, 30)
    assert _ia.verdict(0, 30) == "данные соответствуют"
    assert _ia.verdict(0, 0) == "нечего проверять"


def test_markdown_names_the_rules_that_cannot_be_hard():
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BBB", "2026-08-01T14:00:00", 120)])
    rep = _ia.audit(payload, {"selected": [_unit([["F1"], ["F2"]])]}, _args())
    text = _ia.as_markdown(rep, "тест")
    assert "нельзя объявлять жёсткими" in text


def test_no_network_or_model_calls_in_source():
    src = (HERE / "_input_audit.py").read_text(encoding="utf-8")
    for needle in ("requests", "httpx", "urllib", "socket", "openai",
                   "anthropic", "claude_cli", "ollama"):
        assert needle not in src, f"недопустимый вызов: {needle}"


def test_rest_norm_is_data_not_product():
    """Ярусная норма отдыха — правило конкретной авиакомпании. Без файла
    аудит судит единым порогом продукта и ГОВОРИТ об этом, а не
    подставляет чужие числа (правка 2026-07-26)."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T20:00:00", 120)])
    unit = _unit([["F1"], ["F2"]])
    # разрыв между сменами 12 часов: по норме заказчика вне базы нужно
    # 760 минут -> нарушения нет; по единому порогу 600 -> тоже нет
    with_table = _ia.audit(payload, {"selected": [unit]}, _args())
    assert with_table["rest_model"] == "ярусная таблица авиакомпании"

    flat = _ia.audit(payload, {"selected": [unit]},
                     _args(rest_table=None, min_rest_min=900,
                           rest_model="единый порог правил уровня 1: 900 мин"))
    row = next(r for r in flat["rules"] if "Отдых" in r["rule"])
    assert row["violations"] == 1, "единый порог 900 минут обязан сработать"
    text = _ia.as_markdown(flat, "тест")
    assert "Модель отдыха" in text and "не подана" in text


def test_tiered_table_distinguishes_base_from_outstation():
    """Проверяем саму ярусную логику на синтетической норме."""
    a = _args()
    audit = _ia.Audit(_payload([_leg("F1", "BASE", "AAA",
                                     "2026-08-01T06:00:00", 60)]), a)
    from datetime import datetime
    when = datetime(2026, 8, 1, 12)
    assert audit.rest_needed("BASE", 100, when) == REST_TABLE["base"]
    assert audit.rest_needed("AAA", 100, when) == REST_TABLE["off"]
    assert audit.rest_needed("BASE", 10_000, when) == REST_TABLE["base_long"]
    # без таблицы — единый порог, без ярусов
    a2 = _args(rest_table=None, min_rest_min=600)
    audit2 = _ia.Audit(_payload([_leg("F1", "BASE", "AAA",
                                      "2026-08-01T06:00:00", 60)]), a2)
    assert audit2.rest_needed("AAA", 10_000, when) == 600
