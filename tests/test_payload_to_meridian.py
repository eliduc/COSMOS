"""Тесты выгрузки решения в формат системы-источника
(`_payload_to_meridian.py`).

Фикстура синтетическая — вымышленный перевозчик ZZ, никаких данных
заказчика в репозитории. Проверяется то, на что опирается заказчик,
принимая нашу выгрузку: цепочка становится цепочкой, смена — нарядом,
рабочий участок получает роль «Р», перелёт пассажиром — «D/H» отдельным
нарядом, места перечислены по спросу участков, кредиты времени сходятся,
а круговой прогон через адаптер импорта возвращает ту же структуру.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
EXPORTER = REPO / "_payload_to_meridian.py"

_pm = pytest.importorskip("_payload_to_meridian")


def _leg(fn: str, dep: str, arr: str, start: str, minutes: int) -> dict:
    """Участок канона. `sourceLegId` — в формате источника
    (`<рейс>$dd.mm.yyyy$<номер>`): это мост обратно к исходному файлу."""
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(start)
    return {"flightNumber": fn, "flightLegId": f"{fn}|{start}",
            "sourceLegId": f"{fn}${t0.strftime('%d.%m.%Y')}$1",
            "departureAirport": dep, "arrivalAirport": arr,
            "departureUTCDateTime": t0.isoformat(),
            "arrivalUTCDateTime": (t0 + timedelta(minutes=minutes))
            .isoformat()}


def _payload(seats: dict | None = None) -> dict:
    flights = [
        _leg("P0", "BASE", "AAA", "2026-08-01T06:00:00", 120),   # пассажиром
        _leg("F1", "AAA", "BBB", "2026-08-01T10:00:00", 120),
        _leg("F2", "BBB", "AAA", "2026-08-01T14:00:00", 120),
        _leg("F3", "AAA", "BASE", "2026-08-02T10:00:00", 120),
    ]
    seats = seats or {"P0": ["CA"], "F1": ["PURS", "CA"], "F2": ["CA", "CA"],
                      "F3": ["CA"]}
    assigns = []
    for f in flights:
        for i, rank in enumerate(seats[f["flightNumber"]]):
            assigns.append({"id": f"{f['flightNumber']}#{i}",
                            "flight": f["flightNumber"], "indexInFlight": i,
                            "requiredSkill": rank, "employee": None})
    return {"airports": [{"code": c, "name": c}
                         for c in ("AAA", "BASE", "BBB")],
            "employees": [{"id": "E1", "homeAirport": "BASE"}],
            "flights": flights, "flightAssignments": assigns}


def _src(fn: str, day: str = "01.08.2026") -> str:
    return f"{fn}${day}$1"


def _catalog() -> dict:
    """Одна цепочка: две смены и перелёт пассажиром из базы."""
    return {"selected": [{"fns": ["P0", "F1", "F2", "F3"],
                          "active_fns": ["F1", "F2", "F3"],
                          "duty_fns": [["F1", "F2"], ["F3"]],
                          "kind": "chain", "sourceLegsMissing": 0}]}


def test_chain_becomes_pairing_with_duties_and_roles():
    doc, check, errors = _pm.export(_payload(), _catalog(), level=3)
    assert errors == []
    assert check["pairings"] == 1
    # ТОЛЬКО рабочие смены. Прежде перелёты пассажиром выделялись в
    # отдельный наряд командировки — наша конструкция, которой в формате
    # источника нет: там перегон принадлежит смене и обозначается ролью
    # «D/H» её участка. Отдельный наряд, кроме того, был единственной
    # причиной смен с ПУСТЫМ списком мест (замечание авиакомпании от
    # 31.07.2026, пункт 4 раздела о формате: 310 таких смен в поставке).
    assert check["duties"] == 2
    roles = {l["legId"]: l["dutyCrewTypes"] for l in doc["legs"]}
    assert list(roles[_src("F1")].values()) == ["Р"]
    assert list(roles[_src("P0")].values()) == ["D/H"]
    # рабочие участки одной смены — в одном наряде, разных смен — в разных
    assert list(roles[_src("F1")]) == list(roles[_src("F2")])
    assert list(roles[_src("F3", "02.08.2026")]) != list(roles[_src("F1")])
    assert check["rider_legs"] == 1
    assert check["legs_operated_more_than_once"] == 0


def test_complements_follow_leg_demand():
    """Места наряда — поролевой максимум спроса его участков, в
    каноническом порядке рангов. Табельных номеров нет: назначение людей
    это отдельный этап."""
    doc, _check, errors = _pm.export(_payload(), _catalog(), level=3)
    assert errors == []
    duty = next(d for d in doc["duties"]
                if d["firstDepartureStation"] == "AAA"
                and d["lastArrivalStation"] == "AAA")
    assert [c["complementRank"] for c in duty["complements"]] \
        == ["PURS", "CA", "CA"]
    assert all(c["employeeId"] is None for c in duty["complements"])
    assert not doc["employees"] and not doc["groundActivities"]


def test_time_credits_and_stations():
    doc, _check, errors = _pm.export(_payload(), _catalog(), level=3)
    assert errors == []
    duty = next(d for d in doc["duties"] if d["numberOfDuty"] == 1)
    # Границы наряда включают перегон в голове смены: по правилам
    # источника брифинг накладывается на перегон, и наряд начинается с
    # него. РАБОЧЕЕ же время перегон не включает — поэтому
    # `workTimeCredit` считается по окну «подготовка → разбор», а не по
    # границам наряда.
    assert duty["start"] == "2026-08-01T06:00:00"   # с перелёта пассажиром
    assert duty["end"] == "2026-08-01T16:00:00"
    assert duty["flightTimeCredit"] == "240"        # два участка по 120
    # Прежде здесь стоял размах полётов (360). Теперь это РАБОЧЕЕ время:
    # окно полётов плюс подготовка и разбор из данных участка. У фикстуры
    # буферов нет, поэтому значение совпадает с окном — тест закрепляет
    # ИМЯ величины, а её наполнение проверяет отдельный тест ниже.
    assert duty["workTimeCredit"] == "360"
    assert duty["stationsVisited"] == ["AAA", "BBB"]
    pairing = doc["pairings"][0]
    assert pairing["start"] == "2026-08-01T06:00:00"   # с перелёта пассажиром
    assert pairing["end"] == "2026-08-02T12:00:00"


def test_unknown_leg_fails_loudly():
    """Цепочка ссылается на участок, которого в наборе нет: молча
    выбросить его — значит потерять работу из выгрузки."""
    cat = _catalog()
    cat["selected"][0]["fns"].append("НЕТ-ТАКОГО")
    doc, check, errors = _pm.export(_payload(), cat, level=3)
    assert errors and "нет в наборе" in errors[0]
    assert check["pairings"] == 0


def test_duty_boundaries_outside_the_trip_fail_loudly():
    """Границы смен, ссылающиеся на участок вне командировки, — это
    рассогласованный каталог. Раньше такая ссылка давала KeyError в
    середине сборки: падение вместо вердикта и без отчёта проверки
    (аудит 2026-07-26, P2-01)."""
    cat = _catalog()
    cat["selected"][0]["duty_fns"].append(["ПРИЗРАК"])
    doc, check, errors = _pm.export(_payload(), cat, level=3)
    assert errors and "вне командировки" in errors[0]
    assert check["pairings"] == 0
    assert doc["duties"] == []


def test_working_leg_lost_by_the_duty_split_fails_loudly():
    """Рабочий участок, не попавший ни в одну смену, — тихая потеря
    работы: юнит выгрузился бы с меньшим спросом, чем несёт каталог."""
    cat = _catalog()
    cat["selected"][0]["duty_fns"] = [["F1", "F2"]]      # F3 потерян
    _doc, check, errors = _pm.export(_payload(), cat, level=3)
    assert errors and "вне границ смен" in errors[0]
    assert check["pairings"] == 0


def test_check_artifact_is_written_even_on_an_internal_error(tmp_path,
                                                             monkeypatch):
    """Дом. правило: отчёт проверки пишется всегда. Иначе вызывающий
    видит ненулевой код и не видит причины."""
    p = tmp_path / "p.json"
    p.write_text(json.dumps(_payload()), encoding="utf-8")
    c = tmp_path / "cat.json"
    c.write_text(json.dumps(_catalog()), encoding="utf-8")
    check = tmp_path / "check.json"
    boom = tmp_path / "boom.py"
    boom.write_text(
        "import sys, json\n"
        f"sys.path.insert(0, r'{REPO}')\n"
        "import _payload_to_meridian as m\n"
        "m.export = lambda *a, **k: (_ for _ in ()).throw("
        "RuntimeError('синтетический сбой'))\n"
        "sys.exit(m.main(sys.argv[1:]))\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(boom), "--payload", str(p), "--catalog", str(c),
         "--out", str(tmp_path / "out.json"), "--level", "3",
         "--check", str(check)],
        capture_output=True, text=True, cwd=REPO,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 1
    assert check.exists(), "отчёт проверки не написан при внутреннем сбое"
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["ok"] is False
    assert "внутренняя ошибка" in doc["errors"][0]


def test_double_covered_leg_is_counted_not_fatal():
    """Участок, обслуженный двумя цепочками, — лишние затраты решения, а
    не дефект выгрузки: формат источника это допускает, и в данных
    заказчика такие участки есть."""
    cat = _catalog()
    cat["selected"].append({"fns": ["F1"], "active_fns": ["F1"],
                            "duty_fns": [["F1"]]})
    _doc, check, errors = _pm.export(_payload(), cat, level=3)
    assert errors == []
    assert check["legs_operated_more_than_once"] == 1
    assert check["pairings"] == 2


def test_source_reference_keeps_all_legs_and_header():
    """Со справочником источника выгружаются ВСЕ его участки, в том числе
    не попавшие в решение (с пустой ролью), и заголовочные поля файла."""
    source = {"format": 1, "type": "pairing", "division": "ZZ",
              "planSince": "2026-08-01T00:00:00",
              "planUntil": "2026-08-31T23:59:00", "presetId": "p1",
              "airports": [{"IATA": "BASE", "name": "БАЗА"}],
              "legs": [{"legId": _src("F1"), "carrier": "ZZ",
                        "properties": "J_REGULAR"},
                       {"legId": _src("НЕ-НАШ"), "carrier": "ZZ"}]}
    doc, check, _errors = _pm.export(_payload(), _catalog(), 3, source)
    assert check["source_used"] is True
    assert check["legs_total"] == 2
    by_id = {l["legId"]: l for l in doc["legs"]}
    assert by_id[_src("F1")]["properties"] == "J_REGULAR"   # поле источника
    assert by_id[_src("НЕ-НАШ")]["dutyCrewTypes"] == {}
    assert doc["planSince"] == "2026-08-01T00:00:00"
    assert doc["division"] == "ZZ" and doc["presetId"] == "p1"
    assert doc["airports"] == source["airports"]


def test_without_source_legs_come_from_the_canonical_set():
    doc, check, _errors = _pm.export(_payload(), _catalog(), level=3)
    assert check["source_used"] is False
    assert check["legs_total"] == 4
    assert check["airports"] == 3
    assert doc["planSince"] == "2026-08-01T06:00:00"
    assert {l["legId"] for l in doc["legs"]} == {
        _src("P0"), _src("F1"), _src("F2"), _src("F3", "02.08.2026")}


def test_round_trip_through_the_import_adapter():
    """Круговой прогон: выгруженный документ разбирается штатным
    адаптером импорта и даёт ту же структуру — цепочку из двух смен с
    перелётом пассажиром. Это проверка того, что заказчик прочтёт нашу
    выгрузку своим форматом, а не только мы своим.

    Разбор вызывается напрямую: канон требует хотя бы одного сотрудника,
    а выгрузка людей намеренно не несёт — назначение это отдельный этап,
    и табельные номера в решение уровня цепочек не входят."""
    from _meridian_to_payload import convert
    cat = _catalog()
    # Позиционный перелёт кто-то выполняет: в расписании это обычный рейс,
    # и в круговом прогоне у него должна быть своя смена.
    cat["selected"].append({"fns": ["P0"], "active_fns": ["P0"],
                            "duty_fns": [["P0"]]})
    doc, _check, errors = _pm.export(_payload(), cat, level=3)
    assert errors == []
    from datetime import date
    _payload_back, check, errs, units = convert(
        doc, None, "export.json", "check.json",
        date(2026, 8, 1), date(2026, 8, 31), False)
    assert errs == [], errs
    chain = next(u for u in units if len(u["active_fns"]) == 3)
    assert len(chain["duty_fns"]) == 2, "границы смен не пережили круг"
    assert len(chain["active_fns"]) == 3
    assert len(chain["fns"]) == 4, "перелёт пассажиром не пережил круг"
    assert check["catalog_chains_complete"] == 2   # обе выгруженные цепочки
    assert check["catalog_chains_truncated"] == 0


def test_cli_writes_check_even_on_failure(tmp_path):
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps(_payload()), encoding="utf-8")
    cat = _catalog()
    cat["selected"][0]["fns"].append("НЕТ-ТАКОГО")
    catalog = tmp_path / "cat.json"
    catalog.write_text(json.dumps(cat), encoding="utf-8")
    check = tmp_path / "check.json"
    out = tmp_path / "out.json"
    proc = subprocess.run(
        [sys.executable, str(EXPORTER), "--payload", str(payload),
         "--catalog", str(catalog), "--out", str(out), "--level", "3",
         "--check", str(check)],
        capture_output=True, text=True, cwd=REPO,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 1
    assert not out.exists(), "неполная выгрузка не должна записываться"
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["ok"] is False and doc["errors_total"] == 1


def test_no_network_or_model_calls_in_source():
    src = EXPORTER.read_text(encoding="utf-8")
    for needle in ("requests", "httpx", "urllib", "socket", "openai",
                   "anthropic", "claude_cli", "ollama"):
        assert needle not in src, f"недопустимый вызов: {needle}"
