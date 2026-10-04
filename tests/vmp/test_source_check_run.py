# -*- coding: utf-8 -*-
"""ФПР-раннер: адаптер пейринг-каталога, людской скоуп «судится на
ростеринге», ядро run_full с двумя решениями (читатель мокается),
ручка запуска из интерфейса (202 / гейт running 409)."""
from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from vmp import source_check_run as scr  # noqa: E402

FMAP = {
    "PL100|2026-09-07T09:00:00": {
        "dep": "PLY", "arr": "P01",
        "t0": "2026-09-07T09:00:00", "t1": "2026-09-07T10:30:00"},
    "PL101|2026-09-07T12:00:00": {
        "dep": "P01", "arr": "PLY",
        "t0": "2026-09-07T12:00:00", "t1": "2026-09-07T13:30:00"},
}
CATALOG = {"selected": [
    {"fns": ["PL100|2026-09-07T09:00:00",
             "PL101|2026-09-07T12:00:00"]}]}


def test_адаптер_каталога_строит_линию_цепочки():
    sol = scr.catalog_to_solution(CATALOG, FMAP)
    assert len(sol["employees"]) == 1
    emp = sol["employees"][0]
    assert emp["id"] == "chain-0"
    assert emp["homeAirport"] == "PLY"   # база = вылет первого плеча
    assert len(sol["flights"]) == 2
    assert all(fa["employee"] == "chain-0"
               for fa in sol["flightAssignments"])


def test_адаптер_чужое_плечо_отказ_поимённо():
    with pytest.raises(SystemExit) as e:
        scr.catalog_to_solution(
            {"selected": [{"fns": ["НЕТ|2026-09-07T09:00:00"]}]},
            FMAP)
    assert "НЕТ|" in str(e.value)


def test_людские_виды_отделяются_для_пейринга():
    specs = [{"id": "a", "kind": "duty_span_max"},
             {"id": "b", "kind": "crew_size_exact"},
             {"id": "c", "kind": "unavailability"}]
    run, skip = scr._filter_specs_for_pairing(specs)
    assert [s["id"] for s in run] == ["a"]
    assert [s["id"] for s in skip] == ["b", "c"]


def test_книга_рейсов_из_пакета(tmp_path):
    cd = {"tables": {"D1": {"Рейсы": {
        "columns": ["Номер рейса", "Дата", "Вылет", "Время вылета",
                    "Прилёт", "Время прилёта"],
        "rows": [{"Номер рейса": "PL100", "Дата": "2026-09-07",
                  "Вылет": "PLY", "Время вылета": "09:00",
                  "Прилёт": "P01", "Время прилёта": "10:30"}],
    }}}}
    pkg = tmp_path / "p.zip"
    with zipfile.ZipFile(pkg, "w") as z:
        z.writestr("clean-data.json",
                   json.dumps(cd, ensure_ascii=False))
    fmap = scr.clean_flights_from_package(pkg)
    rec = fmap["PL100|2026-09-07T09:00:00"]
    assert rec["arr"] == "P01"
    assert rec["t1"] == "2026-09-07T10:30:00"


def _fake_compile(specs):
    def fn(clauses, scope, requested=None, model=None,
           timeout_s=0.0, effort="medium", channel=None):
        return {"specs": specs, "problems": [],
                "backend": "cloud", "model": "fake", "channel": channel}
    return fn


def test_run_full_два_решения_и_протокол(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    run_dir = tmp_path / "run-x"
    cl = run_dir / "freeze" / "clauses"
    cl.mkdir(parents=True)
    (cl / "S1.json").write_text(json.dumps({"clauses": [
        {"anchor": "п.1", "text": "Смена не длиннее 12:00."}]},
        ensure_ascii=False), encoding="utf-8")
    from webapp import source_check_compile as scc
    monkeypatch.setattr(scc, "compile_checks", _fake_compile([
        {"id": "span", "kind": "duty_span_max", "anchor": "п.1",
         "params": {"limit_min": 720, "rest_split_min": 480}},
        {"id": "crew", "kind": "crew_size_exact", "anchor": "п.2",
         "params": {"n": 1}},
    ]))
    pairing = scr.catalog_to_solution(CATALOG, FMAP)
    roster = {"flights": [], "employees": [],
              "flightAssignments": [], "score": "0hard/0soft"}
    side = tmp_path / "roster.json.source-check.md"
    got = scr.run_full(run_dir, [
        ("пейринг (каталог цепочек)", pairing, "pairing", None),
        ("ростеринг (r1)", roster, "roster", str(side)),
    ], requested="cloud", model="fake", channel="cli")
    proto = (run_dir / "source-check" / "protocol.md").read_text(
        encoding="utf-8")
    assert "ВЫХОДНОЙ ДОКУМЕНТ РЕШЕНИЯ" in proto
    assert "пейринг (каталог цепочек)" in proto
    assert "СУДИТСЯ НА РОСТЕРИНГЕ" in proto     # crew на пейринге
    assert side.is_file()
    last = json.loads((tmp_path / "data" / "source-check"
                       / "last.json").read_text(encoding="utf-8"))
    assert last["status"] == "done"
    assert last["per_solution"]["ростеринг (r1)"]
    assert got["reader"] == "cloud/fake"
    # S-05.16: наблюдённый канал читателя — в возврате, заголовке
    # протокола, снимке и checks.json (показание, не заявление)
    assert got["reader_channel"] == "cli"
    assert "читатель: cloud/fake/cli" in proto
    assert last["reader_channel"] == "cli"
    checks = json.loads((run_dir / "source-check" / "checks.json")
                        .read_text(encoding="utf-8"))
    assert checks["reader_channel"] == "cli" and checks["reader"] == "cloud/fake"
    # Расход читателя ИДАК — в снимке, в checks.json и в возврате
    # (решение владельца № 1 от 10.09.2026): сверка идёт отдельным
    # вызовом, и её токены видны отдельной строкой роли. Подставной
    # компилятор расхода не отдаёт — строка честно «не считается».
    row = last["resources"]["models"][0]
    assert row["role"] == "читатель ИДАК"
    assert row["role_code"] == "source_check_reader"
    assert row["calls"] == 1 and row["counted"] is False and row["note"]
    # Причина «не считается» — словами и с именем канала; нулей вместо
    # неизвестного нет.
    assert "cli" in row["note"] and "input_tokens" not in row
    assert checks["resources"] == last["resources"] == got["resources"]


def test_расход_читателя_в_снимке_и_кэш_не_удваивает_токены(
        tmp_path, monkeypatch):
    """Токены читателя ИДАК считаются один раз за вызов. Второй прогон
    берёт проверки из кэша — модель не зовётся, и расход той, прежней
    компиляции этому прогону НЕ приписывается (иначе один и тот же
    расход считался бы дважды): вызовов ноль, токенов ноль, и этот ноль
    СОСЧИТАН — причина словами «чтение из кэша»."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    run_dir = tmp_path / "run-к"
    cl = run_dir / "freeze" / "clauses"
    cl.mkdir(parents=True)
    (cl / "S1.json").write_text(json.dumps({"clauses": [
        {"anchor": "п.1", "text": "Смена не длиннее 12:00."}]},
        ensure_ascii=False), encoding="utf-8")
    specs = [{"id": "span", "kind": "duty_span_max", "anchor": "п.1",
              "params": {"limit_min": 720, "rest_split_min": 480}}]
    calls: list = []

    def _compile(clauses, scope, requested=None, model=None,
                 timeout_s=0.0, effort="medium", channel=None):
        calls.append(model)
        return {"specs": specs, "problems": [], "backend": "cloud",
                "model": model, "channel": channel,
                "usage": {"provider": "anthropic", "model": model,
                          "channel": channel, "counted": True,
                          "input_tokens": 3300, "output_tokens": 90}}
    from webapp import source_check_compile as scc
    monkeypatch.setattr(scc, "compile_checks", _compile)
    roster = {"flights": [], "employees": [],
              "flightAssignments": [], "score": "0hard/0soft"}
    kw = dict(requested="cloud", model="читатель-стенда", channel="api")
    first = scr.run_full(run_dir, [("ростеринг (r1)", roster, "roster", None)],
                         **kw)
    row = first["resources"]["models"][0]
    assert row["counted"] is True and row["calls"] == 1
    assert (row["input_tokens"], row["output_tokens"]) == (3300, 90)
    assert row["provider"] == "anthropic" and row["channel"] == "api"

    second = scr.run_full(run_dir, [("ростеринг (r2)", roster, "roster", None)],
                          **kw)
    assert calls == ["читатель-стенда"], "второй прогон звал модель заново"
    row2 = second["resources"]["models"][0]
    assert row2["calls"] == 0, "вызова не было — считать нечего"
    assert row2["counted"] is True, "ноль вызовов — величина измеренная"
    assert (row2["input_tokens"], row2["output_tokens"]) == (0, 0)
    assert row2["note"] == scr.CACHED_COMPILE_NOTE
    assert row2["note"].startswith("чтение из кэша")


# ------------------------- SOURCE-REST-R1: граница «стыковка / отдых»

def test_граница_отдыха_из_артефактов_прогона(tmp_path):
    """Одна функция rest_boundary для обоих решений — из артефактов
    ЭТОГО прогона, по одному определению с кнопкой и решателем
    (vmp.sit_boundary, решение владельца 06.09.2026), с провенансом
    словами: ростер — показание провайдера day_break_threshold_min по
    runId (ручка max_sit_duration), без ручки (0) — пол минимального
    отдыха тех же показаний с провенансом «выведено из пола» и правилом
    из lastRun.thresholds.applied; ни ручки, ни пола — None с причиной
    «граница смены не определена правилами пакета»; пейринг — --sit-max
    ведомости входа построителя (без него — пол --rest-base/--rest-off);
    чужой runId, нет показаний, нет ведомости — None с причиной, литерал
    не подставляется."""
    from vmp import sit_boundary as sb
    rb = scr.rest_boundary
    ap = {"runId": "r1", "lastRun": {"profileReadings": {
        "min_rest_home_min": 720, "day_break_threshold_min": 480},
        "thresholds": {"applied": [
            {"threshold": "day_break_threshold_min", "value": 480,
             "rules": ["VN-030"]}]}}}
    got = rb("roster", applied=ap, run_id="r1")
    assert got["rest_boundary_min"] == 480
    assert "r1" in got["rest_boundary_source"] \
        and "day_break_threshold_min=480" in got["rest_boundary_source"] \
        and "max_sit_duration" in got["rest_boundary_source"] \
        and "правило VN-030" in got["rest_boundary_source"]
    # ручки нет (0) → пол минимального отдыха тех же показаний: min(720, 600)
    ap0 = {"runId": "r1", "lastRun": {"profileReadings": {
        "day_break_threshold_min": 0, "min_rest_home_min": 720,
        "min_rest_outstation_min": 600},
        "thresholds": {"applied": [
            {"threshold": "min_rest_home_min", "value": 720, "rules": ["VN-023"]},
            {"threshold": "min_rest_outstation_min", "value": 600,
             "rules": ["VN-024"]}]}}}
    got0 = rb("roster", applied=ap0, run_id="r1")
    assert got0["rest_boundary_min"] == 600
    assert "выведено из пола минимального отдыха 600 мин" \
        in got0["rest_boundary_source"] \
        and "VN-024" in got0["rest_boundary_source"] \
        and "VN-023" in got0["rest_boundary_source"] \
        and "day_break_threshold_min=0" in got0["rest_boundary_source"] \
        and "модельное упрощение" in got0["rest_boundary_source"] \
        and "r1" in got0["rest_boundary_source"]
    # критерий границы — пол по месту, упрощение заявлено словами (GPT-053)
    assert got0["rest_boundary_basis"] == "rest_floor"
    assert got0["rest_boundary_by_place"] == {"home": 720, "outstation": 600}
    assert got0["rest_boundary_assumption"] == sb.ASSUMPTION
    assert got["rest_boundary_basis"] == "knob" \
        and got["rest_boundary_assumption"] is None \
        and got["rest_boundary_by_place"] == {"home": 720, "outstation": 720}
    # ни ручки, ни пола → не определена, литерал не подставляется
    ap00 = {"runId": "r1", "lastRun": {"profileReadings": {
        "day_break_threshold_min": 0}}}
    got00 = rb("roster", applied=ap00, run_id="r1")
    assert got00["rest_boundary_min"] is None \
        and sb.UNDEFINED in got00["rest_boundary_source"] \
        and "360" not in got00["rest_boundary_source"]
    bad = rb("roster", applied=ap, run_id="r2")
    assert bad["rest_boundary_min"] is None \
        and "чужого прогона" in bad["rest_boundary_source"]
    none = rb("roster", applied=None, note="HTTP 503")
    assert none["rest_boundary_min"] is None \
        and "HTTP 503" in none["rest_boundary_source"]
    nop = rb("roster", applied={"runId": "r1", "lastRun": {}}, run_id="r1")
    assert nop["rest_boundary_min"] is None \
        and "profileReadings" in nop["rest_boundary_source"]
    # пейринг: --sit-max кнопки — порог правила максимальной стыковки
    bi = {"args": ["--duty-span", "720", "--sit-max", "240",
                   "--rest-base", "720", "--rest-off", "600"],
          "provenance": {"--sit-max": {
              "value": 240, "passed": True, "basis": "knob",
              "from": "правило пакета (max_sit_duration): правило VN-030; "
                      "day_break_threshold_min=240"}}}
    gp = rb("pairing", builder_input=bi)
    assert gp["rest_boundary_min"] == 240 \
        and "--sit-max=240" in gp["rest_boundary_source"] \
        and "максимальная стыковка" in gp["rest_boundary_source"] \
        and "VN-030" in gp["rest_boundary_source"]
    assert gp["rest_boundary_basis"] == "knob" \
        and gp["rest_boundary_by_place"] == {"home": 720, "outstation": 600} \
        and gp["rest_boundary_assumption"] is None
    # без --sit-max (кнопка без ручки) построитель режет по полу отдыха
    # станции — граница по месту из --rest-base / --rest-off, упрощение
    gf = rb("pairing", builder_input={
        "args": ["--duty-span", "720", "--rest-base", "720", "--rest-off", "600"],
        "provenance": {"--sit-max": {
            "value": None, "passed": False, "basis": "rest_floor",
            "from": "выведено из пола минимального отдыха 600 мин "
                    "(правила VN-023, VN-024; …)"}}})
    assert gf["rest_boundary_min"] == 600 \
        and "выведено из пола отдыха построителя" in gf["rest_boundary_source"] \
        and "VN-024" in gf["rest_boundary_source"]
    assert gf["rest_boundary_basis"] == "rest_floor" \
        and gf["rest_boundary_by_place"] == {"home": 720, "outstation": 600} \
        and gf["rest_boundary_assumption"] == sb.ASSUMPTION
    g0 = rb("pairing", builder_input={"args": ["--duty-span", "720"]})
    assert g0["rest_boundary_min"] is None \
        and sb.UNDEFINED in g0["rest_boundary_source"]
    gn = rb("pairing", builder_input=None, note="каталог старого прогона")
    assert gn["rest_boundary_min"] is None \
        and "builder-input.json" in gn["rest_boundary_source"] \
        and "каталог старого прогона" in gn["rest_boundary_source"]
    # ведомость — рядом с каталогом нажатия «Пейринг»
    cat = tmp_path / "button-pairing" / "builder-catalog.json"
    cat.parent.mkdir(parents=True)
    cat.write_text("{}", encoding="utf-8")
    assert scr.builder_input_beside(cat) is None
    (cat.parent / "builder-input.json").write_text(
        json.dumps(bi, ensure_ascii=False), encoding="utf-8")
    assert scr.builder_input_beside(cat) == bi
    assert scr.builder_input_beside(None) is None


def _roster_gap(gap_min: int) -> dict:
    """E1 (база AAA): F1 AAA→BBB, стыковка 120, F2 BBB→CCC, разрыв
    gap_min вне базы, F3 CCC→AAA."""
    from datetime import datetime, timedelta
    t3 = datetime.fromisoformat("2026-03-02T12:00") + timedelta(minutes=gap_min)
    return {
        "employees": [{"id": "E1", "homeAirport": "AAA"}],
        "flights": [
            {"flightNumber": "F1", "departureAirport": "AAA", "arrivalAirport": "BBB",
             "departureUTCDateTime": "2026-03-02T06:00", "arrivalUTCDateTime": "2026-03-02T08:00"},
            {"flightNumber": "F2", "departureAirport": "BBB", "arrivalAirport": "CCC",
             "departureUTCDateTime": "2026-03-02T10:00", "arrivalUTCDateTime": "2026-03-02T12:00"},
            {"flightNumber": "F3", "departureAirport": "CCC", "arrivalAirport": "AAA",
             "departureUTCDateTime": t3.isoformat(timespec="minutes"),
             "arrivalUTCDateTime": (t3 + timedelta(hours=1)).isoformat(timespec="minutes")},
        ],
        "flightAssignments": [{"flight": f, "employee": "E1"}
                              for f in ("F1", "F2", "F3")],
        "score": "0hard/0soft",
    }


def test_run_full_судит_отдых_границей_прогона_и_пишет_провенанс(
        tmp_path, monkeypatch):
    """run_full берёт границу T из пятого элемента решения (показание
    прогона), исполнитель rest_min судит ею; T и её источник — в
    протоколе, checks.json и снимках; без показания — not_checkable,
    константа не подставляется."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    run_dir = tmp_path / "run-r"
    cl = run_dir / "freeze" / "clauses"
    cl.mkdir(parents=True)
    (cl / "S2.json").write_text(json.dumps({"clauses": [
        {"anchor": "п.2", "text": "Отдых вне базы не менее 10:00."}]},
        ensure_ascii=False), encoding="utf-8")
    from webapp import source_check_compile as scc
    monkeypatch.setattr(scc, "compile_checks", _fake_compile([
        {"id": "rest", "kind": "rest_min", "anchor": "п.2",
         "params": {"home_min": 720, "outstation_min": 600,
                    "rest_split_min": 480}}]))
    label = "ростеринг (r1)"
    boundary = scr.rest_boundary("roster", run_id="r1", applied={
        "runId": "r1", "lastRun": {"profileReadings": {
            "day_break_threshold_min": 240}}})
    got = scr.run_full(run_dir, [
        (label, _roster_gap(300), "roster", None, boundary)],
        requested="cloud", model="fake", channel="cli",
        snap_sub="rostering")
    # T=240 ≤ 300 < R=600 вне базы — нарушение (литерал 360 дал бы «ok»)
    assert got["counts"] == {"violated": 1}
    snap = json.loads((tmp_path / "data" / "source-check" / "rostering"
                       / "last.json").read_text(encoding="utf-8"))
    assert snap["rest_boundary_min"] == 240
    assert "day_break_threshold_min=240" in snap["rest_boundary_source"]
    assert snap["rest_boundary"][label]["rest_boundary_min"] == 240
    proto = Path(got["protocol_path"]).read_text(encoding="utf-8")
    assert "Граница «стыковка / отдых» прогона (T): 240 мин" in proto
    assert "rest_split_min=480 не применяется" in proto
    checks = json.loads((run_dir / "source-check" / "rostering"
                         / "checks.json").read_text(encoding="utf-8"))
    assert checks["rest_boundary"][label]["rest_boundary_min"] == 240
    # без показания прогона (четвёрка, как прежний вызов) — честный отказ
    got2 = scr.run_full(run_dir, [(label, _roster_gap(300), "roster", None)],
                        requested="cloud", model="fake", channel="cli",
                        snap_sub="rostering")
    assert got2["counts"] == {"not_checkable": 1}
    snap2 = json.loads((tmp_path / "data" / "source-check" / "rostering"
                        / "last.json").read_text(encoding="utf-8"))
    assert snap2["rest_boundary_min"] is None \
        and "не передано" in snap2["rest_boundary_source"]
    proto2 = Path(got2["protocol_path"]).read_text(encoding="utf-8")
    assert "(T): неизвестна" in proto2 and "литерал не подставляется" in proto2
    # ручки нет — граница по полу отдыха по месту из показаний прогона
    # (GPT-053): разрыв 300 вне базы короче пола 600 — стыковка, интервалов
    # отдыха 0, «ok»; допущение — в протоколе и снимке блока словами
    from vmp import sit_boundary as sb
    boundary3 = scr.rest_boundary("roster", run_id="r1", applied={
        "runId": "r1", "lastRun": {"profileReadings": {
            "day_break_threshold_min": 0, "min_rest_home_min": 720,
            "min_rest_outstation_min": 600}}})
    got3 = scr.run_full(run_dir, [
        (label, _roster_gap(300), "roster", None, boundary3)],
        requested="cloud", model="fake", channel="cli",
        snap_sub="rostering")
    assert got3["counts"] == {"ok": 1}
    snap3 = json.loads((tmp_path / "data" / "source-check" / "rostering"
                        / "last.json").read_text(encoding="utf-8"))
    assert snap3["rest_boundary_min"] == 600 \
        and snap3["rest_boundary_basis"] == "rest_floor" \
        and snap3["rest_boundary_by_place"] == {"home": 720, "outstation": 600} \
        and snap3["rest_boundary_assumption"] == sb.ASSUMPTION
    proto3 = Path(got3["protocol_path"]).read_text(encoding="utf-8")
    assert "(T): 600 мин" in proto3 and "Допущение: МОДЕЛЬНОЕ УПРОЩЕНИЕ" in proto3
    assert "критерий границы смены: пол минимального отдыха по месту" in proto3
    assert "интервалов отдыха проверено: 0" in proto3 or "0" in proto3


def test_ручка_запуска_фон_и_гейт_running(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", "user@example.com")
    monkeypatch.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    from fastapi.testclient import TestClient

    from webapp import users
    from webapp.app import app
    monkeypatch.setattr(users, "USERS_DB_PATH",
                        tmp_path / "users.db")
    users.ensure_db()
    users.add("user@example.com", "user", by="pytest-fpr")
    with TestClient(app) as c:
        # нет пакета — честный 409
        r = c.post("/api/source-check/run", json={})
        assert r.status_code == 409
        # running-гейт: свежий блокирует...
        from datetime import datetime, timedelta
        snap = tmp_path / "source-check"
        snap.mkdir(parents=True)
        (snap / "last.json").write_text(json.dumps(
            {"status": "running", "server_pid": os.getpid(),  # свой процесс — гейт
             "at": datetime.now().isoformat(timespec="seconds")}),
            encoding="utf-8")
        r2 = c.post("/api/source-check/run", json={})
        assert r2.status_code == 409
        assert "уже идёт" in r2.json()["detail"]
        # ...а брошенный (старше 30 мин) — нет: гейт пропускает до
        # следующей проверки (отсутствие пакета -> 409 с ДРУГИМ
        # текстом — значит stale не задушил запуск)
        (snap / "last.json").write_text(json.dumps(
            {"status": "running",
             "at": (datetime.now() - timedelta(hours=2))
             .isoformat(timespec="seconds")}), encoding="utf-8")
        r3 = c.post("/api/source-check/run", json={})
        assert r3.status_code == 409
        assert "уже идёт" not in r3.json()["detail"]
