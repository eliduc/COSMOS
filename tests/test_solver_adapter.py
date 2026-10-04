"""Тесты конвертера «только из солвер-файла» (_solver_to_payload.py,
«отдельное решение» 2026-07-24): синтетическая фикстура вымышленного
перевозчика, без каких-либо данных заказчика."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / "_solver_to_payload.py"
SCHEMA = REPO / "webapp" / "schema" / "canonical_payload.schema.json"


def make_solver_only() -> dict:
    """Мини-format:5: замкнутый sequence-пейринг из двух нарядов, участок
    без структуры (мода), dh%-перегон, наземный наряд недоступности,
    неаллоцируемый сотрудник, заглушка ранга."""
    def z(t, plus_h=0):
        from datetime import datetime, timedelta
        dt = datetime.fromisoformat(t) + timedelta(hours=5 + plus_h)
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + "+05:00[Asia/Karachi]"

    return {
        "format": 5,
        "parameters": {"planSince": "2026-08-01T00:00:00Z[UTC]",
                       "planUntil": "2026-08-31T23:59:00Z[UTC]"},
        "legs": [
            {"id": "QQ100$05.08.2026$1", "flightCode": "QQ100",
             "start": z("2026-08-05T03:00:00"),
             "end": z("2026-08-05T04:00:00"), "acType": "A320neo",
             "departureStation": "BSE", "arrivalStation": "SEC"},
            {"id": "QQ101$05.08.2026$1", "flightCode": "QQ101",
             "start": z("2026-08-05T06:00:00"),
             "end": z("2026-08-05T07:00:00"), "acType": "A320neo",
             "departureStation": "SEC", "arrivalStation": "BSE"},
            # без структуры -> мода по типу судна
            {"id": "QQ200$06.08.2026$1", "flightCode": "QQ200",
             "start": z("2026-08-06T03:00:00"),
             "end": z("2026-08-06T04:00:00"), "acType": "A320neo",
             "departureStation": "BSE", "arrivalStation": "SEC"},
            # материализованный перегон — НЕ операционный спрос
            {"id": "dh%QQ100$05.08.2026$1", "flightCode": "QQ100",
             "start": z("2026-08-05T03:00:00"),
             "end": z("2026-08-05T04:00:00"), "acType": "A320neo",
             "departureStation": "BSE", "arrivalStation": "SEC"},
        ],
        "stations": [
            {"iata": "BSE", "name": "БАЗОВЫЙ", "latitude": 45.512,
             "longitude": 52.371},
            {"iata": "SEC", "name": "ВТОРОЙ", "latitude": 46.148,
             "longitude": 54.906},
        ],
        "duties": [
            {"id": "SD1", "ground": False},
            {"id": "SD2", "ground": False},
            {"id": "GR1", "ground": True, "sby": False,
             "group": "DAYOFF::Выходной",
             "start": z("2026-08-03T19:00:00"),
             "end": z("2026-08-04T18:59:00")},
        ],
        "sequences": [
            {"id": "SQ1_CA_1", "sequenceId": "SQ1", "crewPosition": "CA"}],
        "leg2Duties": [
            {"leg": "QQ100$05.08.2026$1", "duty": "SD1", "crewRole": "STD"},
            {"leg": "QQ101$05.08.2026$1", "duty": "SD2", "crewRole": "STD"},
            # перегон едет пассажиром в SD2 -> пассажирский член юнита SQ1
            {"leg": "dh%QQ100$05.08.2026$1", "duty": "SD2",
             "crewRole": "ONLY_DH"},
        ],
        "employeeAssignments": [
            {"id": "a1", "duty": "SD1", "preferredRank": "CA",
             "sequence": "SQ1_CA_1"},
            {"id": "a2", "duty": "SD1", "preferredRank": "PURS",
             "sequence": "SQ1_CA_1"},
            {"id": "a3", "duty": "SD2", "preferredRank": "CA",
             "sequence": "SQ1_CA_1"},
            {"id": "a4", "duty": "SD2", "preferredRank": "PURS",
             "sequence": "SQ1_CA_1"},
            {"id": "g1", "duty": "GR1", "employee": "E001"},
        ],
        "employees": [
            {"id": "E001", "base": "BSE", "allocable": True,
             "birthday": "1990-01-01",
             "ranks": {"A320neo": "CA", "B767-300": "<нет>"},
             "features": [
                 {"code": "Опасные грузы",
                  "activeSince": "2025-06-01T00:00:00.000",
                  "activeUntil": "2026-12-31T00:00:00.000"},
                 {"code": "Просроченный допуск",
                  "activeSince": "2024-01-01T00:00:00.000",
                  "activeUntil": "2026-01-01T00:00:00.000"},
             ]},
            {"id": "E002", "base": "BSE", "allocable": False,
             "ranks": {"A320neo": "CA"}, "features": []},
        ],
    }


def run_adapter(tmp_path, solver: dict):
    src = tmp_path / "solver.json"
    src.write_text(json.dumps(solver, ensure_ascii=False),
                   encoding="utf-8")
    out = tmp_path / "payload.json"
    check = tmp_path / "check.json"
    cat = tmp_path / "catalog.json"
    proc = subprocess.run(
        [sys.executable, str(ADAPTER), str(src), "--out", str(out),
         "--check", str(check), "--emit-catalog", str(cat),
         "--catalog-label", "pytest-solver"],
        capture_output=True, text=True, cwd=REPO,
        encoding="utf-8", errors="replace")
    return proc, out, check, cat


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("solver_only")
    proc, out, check, cat = run_adapter(tmp, make_solver_only())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (json.loads(out.read_text(encoding="utf-8")),
            json.loads(check.read_text(encoding="utf-8")),
            json.loads(cat.read_text(encoding="utf-8")))


def test_flights_and_deadhead_exclusion(converted):
    payload, check, _ = converted
    fns = sorted(f["flightNumber"] for f in payload["flights"])
    assert fns == ["QQ100-20260805-1", "QQ101-20260805-1",
                   "QQ200-20260806-1"]
    assert check["legs_excluded"] == {"deadhead": 1}
    # Времена приведены из зонированных к UTC.
    f0 = next(f for f in payload["flights"]
              if f["flightNumber"] == "QQ100-20260805-1")
    assert f0["departureUTCDateTime"] == "2026-08-05T03:00:00"
    assert f0["aircraftType"] == "A320neo"


def test_sequence_unit_closed_with_dh_rider(converted):
    _, check, cat = converted
    sq1 = next(u for u in cat["selected"]
               if u["sourceUnit"] == "sequence:SQ1")
    # Оба операционных участка + перегон-пассажир (dh% -> исходный рейс).
    assert sq1["active_fns"] == ["QQ100-20260805-1", "QQ101-20260805-1"]
    assert "QQ100-20260805-1" in sq1["fns"]
    assert sq1["seats"] == ["PURS", "CA"]
    assert check["catalog_units_by_kind"] == {"sequence": 1, "mode": 1}
    assert check["catalog_units_closed"] == 1
    assert check["catalog_riders_unmapped"] == 0


def test_catalog_marks_unit_kind_and_completeness(converted):
    """Контракт каталога один на оба адаптера: ротация решателя —
    целая командировка (`kind: chain` + счётчик потерянных участков),
    одиночный участок по моде типа судна — достройка (`kind: fill`), к
    которой замкнутость на базу неприменима по определению."""
    _, check, cat = converted
    sq1 = next(u for u in cat["selected"]
               if u["sourceUnit"] == "sequence:SQ1")
    mode = next(u for u in cat["selected"]
                if u["sourceUnit"].startswith("mode:"))
    assert sq1["kind"] == "chain"
    assert sq1["sourceLegsMissing"] == 0
    assert mode["kind"] == "fill"
    assert "sourceLegsMissing" not in mode
    assert check["catalog_chains_complete"] == 1
    assert check["catalog_chains_truncated"] == 0
    assert check["catalog_units_fill"] == 1


def test_employees_unavailability_and_pii(converted):
    payload, check, _ = converted
    assert [e["id"] for e in payload["employees"]] == ["E001"]
    assert check["employees_not_allocable_skipped"] == 1
    e = payload["employees"][0]
    assert e["skills"] == ["CA"]                 # заглушка '<нет>' пропущена
    assert e["typeRatings"] == ["A320neo"]
    assert e["unavailableDays"] == ["2026-08-03", "2026-08-04"]
    assert "birthday" not in e
    assert check["placeholder_ranks_skipped"] == {"<нет>": 1}
    assert check["ground_activity_types"] == {"DAYOFF": 1}


def test_qualifications_and_schema(converted):
    import jsonschema
    payload, check, _ = converted
    quals = payload["qualifications"]
    assert [q["code"] for q in quals] == ["Опасные грузы"]
    assert check["qualifications_skipped_expired"] == 1
    jsonschema.validate(payload, json.loads(
        SCHEMA.read_text(encoding="utf-8")))
    assert payload["provenance"]["adapter"] == "solver"


def test_model_free_adapter_source():
    src = ADAPTER.read_text(encoding="utf-8")
    import re
    assert not re.search(
        r"llm_backend|claude_cli|import httpx|import requests"
        r"|urllib\.request|http\.client", src), \
        "конвертер обязан быть свободен от модельного/сетевого выхода"


def test_station_timezone_reaches_the_payload():
    """Часовой пояс станции нужен для правил, заданных в МЕСТНОМ времени:
    ночная смена 22:00-06:00, «тёмная» 23:00-07:00, начало следующей смены
    после ночной не ранее 06:00, окончание смены перед отпуском до 20:00.
    Адаптер пояс отбрасывал, и эти правила были непроверяемы в принципе."""
    from _solver_to_payload import convert
    doc = {
        "format": "5", "id": "t",
        "legs": [{"id": "ZZ001$01.08.2026$1", "flightCode": "ZZ001",
                  "acType": "A320",
                  "departureStation": "BSE", "arrivalStation": "IST",
                  "start": "2026-08-01T06:00:00+05:00[Asia/BaseCity]",
                  "end": "2026-08-01T10:00:00+03:00[Europe/Istanbul]",
                  "deadhead": False, "allocable": True}],
        "duties": [], "sequences": [], "leg2Duties": [],
        "employeeAssignments": [], "employees": [],
        "stations": [{"iata": "BSE", "name": "BaseCity",
                      "timeZone": "Asia/BaseCity", "latitude": 45.5,
                      "longitude": 52.4},
                     {"iata": "IST", "name": "Istanbul",
                      "timeZone": "Europe/Istanbul", "latitude": 41.0,
                      "longitude": 28.8}],
        "parameters": {}, "metrics": [],
    }
    payload, check, _errors, _units = convert(doc, "t.json", "chk.json")
    tz = {a["code"]: a.get("timeZone") for a in payload["airports"]}
    assert tz.get("BSE") == "Asia/BaseCity", tz
    assert tz.get("IST") == "Europe/Istanbul", tz
    assert check["airports_with_timezone"] == 2


def test_briefing_and_debriefing_reach_the_payload():
    """Рабочее время смены = брифинг + рейсы + дебрифинг. По нему у
    системы-источника определяются и предел смены по составу, и признак
    ночной смены, поэтому без брифинга оба правила непроверяемы.

    Брифинг привязывается к ПЕРВОМУ рабочему рейсу смены, дебрифинг — к
    последнему; позиционный перелёт по краю в рабочее время не входит."""
    from _solver_to_payload import convert
    def leg(lid, code, dep, arr, h0, h1):
        return {"id": lid, "flightCode": code, "acType": "A320",
                "departureStation": dep, "arrivalStation": arr,
                "start": "2026-08-01T%02d:00:00+05:00[Asia/BaseCity]" % h0,
                "end": "2026-08-01T%02d:00:00+05:00[Asia/BaseCity]" % h1,
                "deadhead": False, "allocable": True}
    doc = {
        "format": "5", "id": "t",
        "legs": [leg("ZZ001$01.08.2026$1", "ZZ001", "BSE", "IST", 6, 10),
                 leg("ZZ002$01.08.2026$1", "ZZ002", "IST", "BSE", 12, 16),
                 leg("ZZ003$01.08.2026$1", "ZZ003", "BSE", "TRD", 4, 5)],
        "duties": [{"id": "D1", "flight": True, "ground": False,
                    "acType": "A320", "crewType": "STD",
                    "start": "2026-08-01T06:00:00+05:00[Asia/BaseCity]",
                    "end": "2026-08-01T16:00:00+05:00[Asia/BaseCity]",
                    "briefing": {"duration": "PT1H40M"},
                    "debriefing": {"duration": "PT1H"}}],
        "sequences": [],
        "leg2Duties": [
            # позиционный перелёт стоит ПЕРВЫМ по времени, но брифинг
            # обязан привязаться к первому РАБОЧЕМУ рейсу;
            # рабочие рейсы перечислены в ОБРАТНОМ порядке — иначе порядок
            # по файлу и порядок по времени совпадают, и сортировка по
            # моменту вылета остаётся непроверенной
            {"leg": "ZZ003$01.08.2026$1", "duty": "D1", "crewRole": "FIRST_DH"},
            {"leg": "ZZ002$01.08.2026$1", "duty": "D1", "crewRole": "STD"},
            {"leg": "ZZ001$01.08.2026$1", "duty": "D1", "crewRole": "STD"}],
        "employeeAssignments": [{"id": "A1", "duty": "D1", "employee": "E1",
                                 "preferredRank": "CA", "locked": True}],
        "employees": [], "stations": [
            {"iata": "BSE", "timeZone": "Asia/BaseCity"},
            {"iata": "IST", "timeZone": "Europe/Istanbul"},
            {"iata": "TRD", "timeZone": "Asia/Karachi"}],
        "parameters": {}, "metrics": [],
    }
    payload, check, _errors, _units = convert(doc, "t.json", "chk.json")
    by = {f["sourceLegId"]: f for f in payload["flights"]}
    assert by["ZZ001$01.08.2026$1"].get("briefingMin") == 100
    assert by["ZZ002$01.08.2026$1"].get("debriefingMin") == 60
    # позиционный перелёт брифинга не получает
    assert by["ZZ003$01.08.2026$1"].get("briefingMin") is None
    # и второй рабочий рейс не получает брифинга первого
    assert by["ZZ002$01.08.2026$1"].get("briefingMin") is None
    assert check["legs_with_briefing"] == 1
    assert check["legs_with_debriefing"] == 1


def test_emit_rules_requires_country(tmp_path):
    """Солвер-файл страну юрисдикции не несёт: --emit-rules без
    --rules-country — отказ словами ДО конвертации (код 2, файла нет);
    с --rules-country QQ страна оператора попадает в пакет."""
    solver = make_solver_only()
    solver["constraintWeightOverrides"] = {"WEEKLY_REST": "HIGH_HIGH"}
    src = tmp_path / "solver.json"
    src.write_text(json.dumps(solver, ensure_ascii=False),
                   encoding="utf-8")
    rules = tmp_path / "pack.json"
    base = [sys.executable, str(ADAPTER), str(src),
            "--out", str(tmp_path / "p.json"),
            "--check", str(tmp_path / "c.json"),
            "--emit-rules", str(rules)]
    proc = subprocess.run(base, capture_output=True, text=True, cwd=REPO,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "--rules-country" in proc.stdout
    assert not rules.exists()
    proc2 = subprocess.run(base + ["--rules-country", "qq"],
                           capture_output=True, text=True, cwd=REPO,
                           encoding="utf-8", errors="replace")
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    pack = json.loads(rules.read_text(encoding="utf-8"))
    assert pack["country"] == "QQ"
    # кривое значение — тот же отказ до конвертации
    proc3 = subprocess.run(base + ["--rules-country", "Q1Q"],
                           capture_output=True, text=True, cwd=REPO,
                           encoding="utf-8", errors="replace")
    assert proc3.returncode == 2, proc3.stdout + proc3.stderr
