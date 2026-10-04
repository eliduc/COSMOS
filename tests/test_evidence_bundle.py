"""Обезличенный пакет доказательств (пятый аудит, E5).

Главная проверка — не «пакет собирается», а «данные заказчика в него не
попадают». Поэтому синтетический набор нарочно наполнен опознавательными
знаками: фамилиями, номерами рейсов и бортов, кодами аэропортов, датами
и адресом почты. Ни один из них не имеет права оказаться в выпуске.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "evidence_bundle", REPO / "tools" / "evidence_bundle.py")
eb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eb)


MARKERS = ["БАЗОВЫЙ", "Иванов Пётр", "ZZ601", "XX32011", "BSE", "IST",
           "2026-08-14", "petr.ivanov@example.com", "John Smith"]


def _payload() -> dict:
    """Набор, набитый опознавательными знаками нарочно."""
    flights = []
    for i in range(6):
        flights.append({
            "flightNumber": f"ZZ{601 + i}",
            "flightLegId": f"ZZ{601 + i}-2026-08-14",
            "departureAirport": "BSE", "arrivalAirport": "IST",
            "departureUTCDateTime": f"2026-08-14T0{i}:00:00Z",
            "arrivalUTCDateTime": f"2026-08-14T0{i + 3}:00:00Z",
            "aircraftType": "B788", "tailNumber": "XX32011",
        })
    employees = [
        {"id": "EMP-1", "name": "Иванов Пётр", "homeAirport": "BSE",
         "isReserve": False, "unavailableDays": ["2026-08-20"],
         "seniority": 3},
        {"id": "EMP-2", "name": "John Smith", "homeAirport": "IST",
         "isReserve": True, "unavailableDays": [], "seniority": 1},
    ]
    assignments = [
        {"id": f"A{i}", "flightLegId": f"ZZ{601 + i}-2026-08-14",
         "requiredSkill": "Captain", "employee": "EMP-1"}
        for i in range(6)
    ]
    return {
        "formatVersion": "2",
        "provenance": {"adapter": "meridian", "source": "БАЗОВЫЙ-выгрузка",
                       "convertedAt": "2026-08-14T00:00:00Z"},
        "flights": flights, "employees": employees,
        "flightAssignments": assignments,
        "contracts": [{"employee": "EMP-1", "type": "FULL_TIME",
                       "monthlyGuaranteeHours": 75}],
        "qualifications": [{"employee": "EMP-1", "kind": "rating",
                            "code": "B788", "validTo": "2027-01-01"}],
        "airports": [{"code": "BSE", "name": "BaseCity",
                      "latitude": 12.3, "longitude": -45.6}],
    }


@pytest.fixture()
def payload_file(tmp_path):
    p = tmp_path / "set.json"
    p.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    return p


def _build(payload_file, artifacts=()):
    return eb.build(payload_file, list(artifacts), None,
                    "2026-07-27T10:00:00Z")


def test_bundle_carries_no_customer_identifier(payload_file):
    """Ни одного подсаженного знака в выпуске."""
    bundle = _build(payload_file)
    text = json.dumps(bundle, ensure_ascii=False)
    for marker in MARKERS:
        assert marker not in text, f"в пакет утёк опознавательный знак: {marker}"
    # и в человекочитаемой части тоже
    md = eb._markdown(bundle)
    for marker in MARKERS:
        assert marker not in md, marker


def test_aggregates_are_correct(payload_file):
    d = _build(payload_file)["demand"]
    assert d["flights"] == 6
    assert d["crew_slots"] == 6
    assert d["employees"] == 2
    assert d["employees_reserve"] == 1
    assert d["distinct_airports"] == 2
    assert d["distinct_fleet_types"] == 1
    assert d["distinct_bases"] == 2
    assert d["flight_days"] == 1
    assert d["block_hours_total"] == 18.0     # шесть плеч по три часа


def test_file_name_never_leaves(tmp_path):
    """Имя файла — тоже данные: «QZ-2031-01-crew.json» сообщает и
    авиакомпанию, и период."""
    p = tmp_path / "QZ-2031-01-crew.json"
    p.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    bundle = _build(p)
    assert bundle["sources"][0]["name"].startswith("sha256:")
    assert "QZ-2031-01-crew" not in json.dumps(bundle, ensure_ascii=False)


def test_artifact_totals_take_numbers_not_rows(payload_file, tmp_path):
    """Из артефакта берутся числа и длины списков, но не содержимое."""
    art = tmp_path / "result.json"
    art.write_text(json.dumps({
        "status": "OK", "cost": 12345.6, "pairings": [
            {"id": "ZZ601-chain", "legs": ["ZZ601"]}],
    }, ensure_ascii=False), encoding="utf-8")
    bundle = _build(payload_file, [art])
    totals = bundle["artifacts"][0]["totals"]
    assert totals["cost"] == 12345.6
    assert totals["pairings_count"] == 1
    assert "ZZ601" not in json.dumps(bundle, ensure_ascii=False)


def test_guard_refuses_a_leaking_bundle():
    """Страж — не украшение: подложенная строка обязана ронять выпуск."""
    with pytest.raises(eb.AnonymityError, match="номер рейса"):
        eb.assert_anonymous({"demand": {"note": "рейс ZZ601 не покрыт"}})
    with pytest.raises(eb.AnonymityError, match="имя"):
        eb.assert_anonymous({"crew": ["Иванов Пётр"]})
    with pytest.raises(eb.AnonymityError, match="почты"):
        eb.assert_anonymous({"x": "petr.ivanov@example.com"})


def test_guard_checks_field_names_too():
    """Имя поля выдало бы данные не хуже значения."""
    with pytest.raises(eb.AnonymityError, match="имя поля"):
        eb.assert_anonymous({"ZZ601_uncovered": 3})


def test_guard_allows_digests_and_counts():
    eb.assert_anonymous({"sha256": "a" * 64, "flights": 4401,
                         "commit": "7fe7d08", "ratio": 0.918})


def test_cli_writes_two_files(payload_file, tmp_path, monkeypatch):
    # Часы CLI обязаны быть заморожены: маркер «2026-08-14» — дата рейсов
    # набора, и в день календарного совпадения живая метка «Собран: <сейчас>»
    # роняла бы сканер маркеров ложно (поймано прогоном 2026-08-14).
    class _FrozenDatetime(eb.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 27, 10, 0, 0, tzinfo=tz)

    monkeypatch.setattr(eb, "datetime", _FrozenDatetime)
    out = tmp_path / "bundle"
    rc = eb.main(["--payload", str(payload_file), "--out", str(out)])
    assert rc == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == eb.SCHEMA
    assert (out / "README-RU.md").is_file()
    text = (out / "README-RU.md").read_text(encoding="utf-8")
    for marker in MARKERS:
        assert marker not in text, marker


def test_cli_refuses_and_writes_nothing_when_guard_trips(payload_file,
                                                         tmp_path,
                                                         monkeypatch):
    """Отказ обязан быть до записи: полупакет на диске хуже отсутствия."""
    monkeypatch.setattr(eb, "demand_aggregates",
                        lambda p: {"leak": "рейс ZZ601"})
    out = tmp_path / "bundle"
    assert eb.main(["--payload", str(payload_file), "--out", str(out)]) == 2
    assert not out.exists()


def test_note_field_is_not_a_hole_in_the_guard():
    """Пропуск по ИМЕНИ поля означал бы: положи данные в `note` — и страж
    их не увидит. Проверка списка (`tools/verify_audit_20260726.py`)
    наткнулась ровно на это, пропуск переведён на точное совпадение со
    значением."""
    eb.assert_anonymous({"note": eb.NOTE})          # свой текст — можно
    with pytest.raises(eb.AnonymityError):
        eb.assert_anonymous({"note": "рейс ZZ601 не покрыт"})
    with pytest.raises(eb.AnonymityError):
        eb.assert_anonymous({"generated_at": "2026-08-14"})   # не отметка


def test_reproduce_commands_are_fixed_text():
    """Команды воспроизведения пропускаются как фиксированный текст —
    значит, подменённая команда обязана быть замечена."""
    eb.assert_anonymous({"reproduce": list(eb.REPRODUCE)})
    with pytest.raises(eb.AnonymityError):
        eb.assert_anonymous({"reproduce": ["scp qz-2031-01-14.json host:/"]})
