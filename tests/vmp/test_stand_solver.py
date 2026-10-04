# -*- coding: utf-8 -*-
"""Живой прогон Kotlin-видов стенда (§5.6 проекта шага 4, путь 1).

Свидетель здесь — СТЕНДОВЫЙ РЕШАТЕЛЬ-ЗАГЛУШКА: настоящий HTTP-сервер в
потоке, отвечающий теми же тремя ручками, что и служба
(`POST /schedules/run`, `GET /schedules/run/{id}/status`,
`POST /schedules/run/{id}/score-breakdown`). Так проверяется ВЕСЬ путь —
включая боевой клиент `vmp.run.button.SolverHttp`, которым стенд обязан
ходить (второго клиента решателя в дереве быть не должно), — и ни один
тест не обращается к живому решателю: сеть здесь только петлевая, а
данных авиакомпании нет и быть не может (миры синтетические, коды
ST/T/P).

Что судится:

  * СОСТАВ ПАКЕТА по правилу §5.6 — посадки категории A у всех семей, чьи
    параметры названы миром; вес 1 у мягких; метрика у задач
    `kotlin_metric`;
  * ПАРАМЕТРЫ ЗАПУСКА — крошечный предел, режим по виду задачи и то, что
    профиль Д1 прогоном НЕ задаётся (находка `stand_solver.D1_FINDING`:
    на этой сборке решателя счётный предел останавливает прогон не на
    всех мирах стенда);
  * РАЗБОР СЧЁТА в исходы `passed` / `failed` — по именам ограничений, по
    порождённому имени формы логики, по вкладу целевой;
  * ФОРМА логики измеряется ПОКАЗАНИЯМИ ПРОГОНА (`applied-rules` →
    `lastRun.logic.plans[]`), а не копируется из ожидания: две задачи
    одной семьи судятся под одним именем ограничения, и копия давала бы
    зелёную строку по построению;
  * ПУЛ ПРОГОНОВ СЛУЖБЫ — живость до серии, занятый пул опознаётся первым
    прогоном, и очередь чужого экземпляра стенд не набивает;
  * ОТКАЗЫ СЛОВАМИ — решатель молчит, пакет отвергнут гейтом решателя,
    ограничения нет в разборе, прогон упал, ожидание не дождалось; отказ
    после старта несёт `run_id`, и красная строка прослеживается до
    прогона службы.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

HERE = Path(__file__).resolve().parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

sa = pytest.importorskip("vmp.stand_adapters")
ss = pytest.importorskip("vmp.stand_solver")
st = pytest.importorskip("vmp.stand_tasks")


@pytest.fixture(autouse=True)
def _launcher_env(monkeypatch):
    """Живые прогоны этого модуля идут «как из пусковика»; закон окружения
    (РВ-138, исправление 3) судит `test_launcher_env.py`."""
    from vmp import launcher_env
    monkeypatch.setattr(launcher_env, "missing", lambda env=None: [])


# --------------------------------------------------------------- миры

#: Мир-1 §5.2: один сотрудник базы ST1, два рейса туда-обратно с ночёвкой
#: вне базы. Коды синтетические (A03), данных авиакомпании нет.
WORLD_1 = {
    "form": "schedule",
    "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
    "flights": [
        {"id": "F1", "from": "ST1", "to": "ST2", "dep": "2026-10-01T06:00",
         "arr": "2026-10-01T08:00", "fleet_type": "T1"},
        {"id": "F2", "from": "ST2", "to": "ST1", "dep": "2026-10-02T10:00",
         "arr": "2026-10-02T12:00", "fleet_type": "T1"}],
    "params": {"max_duty_duration": 780, "min_rest_home": 660,
               "min_rest_outstation": 700, "min_turnaround": 45,
               "max_pairing_days": 7, "base_tz_offset_min": 0},
    "stand": {"w_night": 1, "nights_per_layover": 2},
    "history": None,
    "horizon": {"from": "2026-10-01", "to": "2026-10-31"},
}

#: Мир семьи истории: логика формы приходит ИЗ ПОСАДКИ, а не блоком
#: `logic` — посадка обязана нести параметры.
WORLD_NIGHTS = {
    "form": "schedule",
    "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
    "flights": [
        {"id": "N1", "from": "ST1", "to": "ST2", "dep": "2026-10-02T23:00",
         "arr": "2026-10-03T01:00", "fleet_type": "T1"},
        {"id": "N2", "from": "ST2", "to": "ST1", "dep": "2026-10-03T23:00",
         "arr": "2026-10-04T01:00", "fleet_type": "T1"},
        {"id": "N3", "from": "ST1", "to": "ST2", "dep": "2026-10-04T23:00",
         "arr": "2026-10-05T01:00", "fleet_type": "T1"}],
    "params": {"max_consecutive_night_duties": 2, "night_duty_start": 1320,
               "night_duty_end": 360},
    "history": [{"crew": "P1", "consecutive_night_duties": 0}],
    "horizon": {"from": "2026-10-02", "to": "2026-11-01"},
}


# --------------------------------------------- решатель-заглушка (сервер)

class StubState:
    """Что заглушка ответит и что она увидела."""

    def __init__(self):
        self.calls: list = []          # (путь, запрос, тело)
        self.start_status = 200
        self.start_body = {"runId": "run-1", "state": "считает",
                           "packageFingerprint": "f" * 64,
                           "mode": "feasibility-soft"}
        self.start_refusal = "решатель отверг пакет: проба отказа"
        self.states = ["готов"]
        self.failure = None
        # Признак остановки прогона (`termination` правки Д1 решателя,
        # 08.09.2026). `None` — служба СТАРОЙ сборки: поля в ответе нет
        # вовсе, и стенд обязан сказать об этом словами.
        self.termination = None
        self.breakdown_status = 200
        self.breakdown = {
            "score": "0hard/-1soft", "hard": 0, "soft": -1,
            "packageFingerprint": "f" * 64, "generated": [],
            "constraints": {
                "Layover nights away from home": {
                    "score": "0hard/-1soft", "hard": 0, "soft": -1,
                    "matches": 1, "weight": "0hard/-1soft"}}}
        # Показания прогона (`applied-rules`) — ими служба отдаёт ФОРМУ
        # порождённого ограничения (`lastRun.logic.plans[]`), и вид
        # kotlin_logic_form судится по ним, а не по копии ожидания.
        self.applied_status = 200
        self.applied = {"lastRun": {"logic": {"plans": []}}}

    def set_logic(self, rid: str, form: str, *, matches: int = 1,
                  plan_form: str | None = None) -> None:
        """Заглушка порождает форму правила `rid`: разбор счёта и
        показания прогона отвечают согласованно. `plan_form` задаёт
        форму ПЛАНА отдельно — так проверяется случай «служба породила
        не ту форму, а совпадений всё равно одно»."""
        name = ss.logic_constraint_id(rid)
        self.breakdown = dict(
            self.breakdown, generated=[name],
            constraints={name: {"score": "-1hard/0soft", "hard": -1,
                                "soft": 0, "matches": matches,
                                "weight": "-1hard/0soft"}})
        self.applied = {"lastRun": {"logic": {"plans": [
            {"rules": [rid], "constraint": name,
             "form": plan_form or form}]}}}

    def package(self, index: int = 0) -> dict:
        """Файлы пакета, который заглушка получила прогоном index."""
        body = [c for c in self.calls if c[0] == "/schedules/run"][index][2]
        raw = base64.b64decode(body["packageZipBase64"])
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            return {n: zf.read(n) for n in zf.namelist()}

    def query(self, index: int = 0) -> dict:
        return [c for c in self.calls if c[0] == "/schedules/run"][index][1]

    def problem(self, path_part: str = "score-breakdown") -> dict:
        for path, _q, body in self.calls:
            if path.endswith(path_part):
                return body
        raise AssertionError("заглушку не спрашивали про " + path_part)


def _handler(state: StubState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):                            # noqa: A003
            pass

        def _read(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def _send(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):                                    # noqa: N802
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            body = self._read()
            state.calls.append((url.path, query, body))
            if url.path == "/schedules/run":
                if state.start_status != 200:
                    return self._send(state.start_status,
                                      {"detail": state.start_refusal})
                return self._send(200, state.start_body)
            if url.path.endswith("/score-breakdown"):
                if state.breakdown_status != 200:
                    return self._send(state.breakdown_status,
                                      {"detail": "разбор не отдан"})
                return self._send(200, state.breakdown)
            return self._send(404, {"detail": "нет ручки"})

        def do_GET(self):                                     # noqa: N802
            url = urlparse(self.path)
            state.calls.append((url.path, {}, None))
            if url.path.endswith("/status"):
                nxt = state.states.pop(0) if len(state.states) > 1 \
                    else state.states[0]
                body = {"state": nxt, "failure": state.failure}
                if state.termination is not None:
                    body["termination"] = state.termination
                return self._send(200, body)
            if url.path.endswith("/applied-rules"):
                if state.applied_status != 200:
                    return self._send(state.applied_status,
                                      {"detail": "показания не отданы"})
                return self._send(200, state.applied)
            return self._send(200, {})
    return Handler


@pytest.fixture()
def stub():
    """Решатель-заглушка на петлевом адресе: (состояние, адрес)."""
    state = StubState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def catalog():
    ca = pytest.importorskip("vmp.catalog_access")
    try:
        return ca.current()
    except Exception as e:                                    # noqa: BLE001
        pytest.skip("каталог шаблонов недоступен: %s" % e)


@pytest.fixture(scope="module")
def meta(catalog):
    return st._family_meta(catalog)


def _measure(kind, world, expected, *, meta, catalog, url, base,
             record=None, family=None, left_s=None):
    return ss.measure(kind, world=world, expected=dict(expected),
                      families=st.families_for(world, family, meta),
                      meta=meta, stamp=ss.stamp_of(catalog),
                      base=base, client=ss.solver_client(url),
                      record=record, family=family, left_s=left_s)


# ------------------------------------------------------------ имена

def test_имя_порождённого_ограничения_считается_как_у_решателя():
    """`logic <8 hex>` от sha256 идентификатора правила в UTF-8 —
    формула `LogicCompiler.constraintIdFor`, а не догадка по форме."""
    rid = "R-STAND-night_series"
    want = hashlib.sha256(rid.encode("utf-8")).hexdigest()[:8]
    assert ss.logic_constraint_id(rid) == "logic " + want


@pytest.mark.parametrize("row,want", [
    ({"hard": -600, "soft": 0, "weight": "-5hard/0soft"}, 120),
    ({"hard": 0, "soft": -40, "weight": "0hard/-40soft"}, 1),
    ({"hard": 0, "soft": 0, "weight": "-1hard/0soft"}, 0),
])
def test_мера_ограничения_делит_вклад_на_вес(row, want):
    """Мера = |вклад| / вес той половины счёта, у которой вес не ноль."""
    assert ss.amount_of(row) == want


def test_нулевой_вес_при_ненулевом_вкладе_отказ_словами():
    """Делить не на что — это отказ, а не «мера ноль»."""
    with pytest.raises(ss.StandSolverError) as e:
        ss.amount_of({"hard": -5, "soft": 0, "weight": "0hard/0soft",
                      "score": "-5hard/0soft"})
    assert "вес ограничения нулевой" in str(e.value)


def test_ограничения_нет_в_разборе_отказ_а_не_ноль():
    """Имя ожидания, которого нет у решателя, — расхождение словами."""
    with pytest.raises(ss.StandSolverError) as e:
        ss.constraint_row({"constraints": {"Crew size": {}}}, "Нет такого")
    assert "нет в разборе счёта" in str(e.value)
    assert "Crew size" in str(e.value)


# ------------------------------------------------------------- пакет

def test_состав_пакета_по_правилу_семей_мира(stub, meta, catalog, tmp_path):
    """Правило §5.6: посадки A получают ВСЕ семьи, чьи параметры названы
    миром; мягкая семья идёт с весом 1, жёсткая — без веса."""
    state, url = stub
    _measure("kotlin_constraint", WORLD_1,
             {"constraint": "Layover nights away from home", "matches": 1,
              "amount": 1}, meta=meta, catalog=catalog, url=url,
             base=tmp_path, family="layover_penalty")
    model = json.loads(state.package()["model.json"])
    fits = {r["template_fit"]["family"]: r for r in model["rules"]}
    # Семьи параметров мира-1 плюс семья записи.
    assert set(fits) == {"layover_penalty", "connection_windows",
                         "duty_limits", "rest_floors", "time_scale"}
    for rule in model["rules"]:
        assert rule["template_fit"]["category"] == "A"
        assert rule["template_fit"]["registry_rev"] == catalog.fingerprint()
        if rule["kind"] == "SOFT":
            assert rule["weight"] == 1
        else:
            assert "weight" not in rule
    # Пороги мира доехали посадкой той семьи, которой принадлежат.
    assert fits["duty_limits"]["template_fit"]["params"][
        "max_duty_duration"]["value"] == 780
    assert fits["rest_floors"]["template_fit"]["params"][
        "min_rest_outstation"]["value"] == 700


def test_семья_истории_едет_посадкой_с_параметрами(stub, meta, catalog,
                                                   tmp_path):
    """Логика формы решателя выводится ИЗ ПОСАДКИ (путь А решателя):
    посадка семьи истории обязана нести параметры, иначе формы нет."""
    state, url = stub
    rid = "R-STAND-night_series"
    state.set_logic(rid, "CONSECUTIVE_NIGHT_DUTIES_MAX")
    out = _measure("kotlin_logic_form", WORLD_NIGHTS,
                   {"form": "CONSECUTIVE_NIGHT_DUTIES_MAX", "matches": 1,
                    "amount": None}, meta=meta, catalog=catalog, url=url,
                   base=tmp_path, family="night_series")
    assert out["got"] == {"form": "CONSECUTIVE_NIGHT_DUTIES_MAX",
                          "matches": 1, "amount": None}
    model = json.loads(state.package()["model.json"])
    night = [r for r in model["rules"]
             if r["template_fit"]["family"] == "night_series"][0]
    assert night["id"] == rid
    assert set(night["template_fit"]["params"]) == {
        "max_consecutive_night_duties", "night_duty_start", "night_duty_end"}


def test_метка_каталога_пакета_переписана_каталогом_стенда(stub, meta,
                                                           catalog, tmp_path):
    """`manifest.catalog` — отметка ТОГО каталога, которым судит стенд, и
    сводка `fits_catalog` пересчитана вместе с ней (гейт производителя
    судит их согласованность)."""
    state, url = stub
    _measure("kotlin_constraint", WORLD_1,
             {"constraint": "Layover nights away from home", "matches": 1,
              "amount": 1}, meta=meta, catalog=catalog, url=url,
             base=tmp_path, family="layover_penalty")
    manifest = json.loads(state.package()["manifest.json"])
    assert manifest["catalog"] == {"version": catalog.version(),
                                   "fingerprint": catalog.fingerprint()}
    assert manifest["fits_catalog"]["matches_catalog"] is True


def test_метрика_едет_целевой_пакета(stub, meta, catalog, tmp_path):
    """Задача вида `kotlin_metric`: метрика записи — в `objectives[].metric`,
    прогон идёт режимом optimization с этой целевой."""
    state, url = stub
    state.breakdown = dict(state.breakdown, constraints={
        "Layover nights away from home": {
            "score": "0hard/-1soft", "hard": 0, "soft": -1, "matches": 1,
            "weight": "0hard/-1soft"}})
    record = {"id": "layover_nights_total", "zone": "BOTH",
              "title": "суммарное число ночей вне базы",
              "semantics": {"text": "сумма календарных ночей вне базы"}}
    out = _measure("kotlin_metric", WORLD_1,
                   {"value": 1, "matches": 1, "refusal": False}, meta=meta,
                   catalog=catalog, url=url, base=tmp_path, record=record)
    assert out["got"] == {"value": 1, "matches": 1, "refusal": False}
    objective = json.loads(state.package()["objective.json"])
    rostering = objective["rostering"]["objectives"]
    assert [o["metric"] for o in rostering] == ["layover_nights_total"]
    assert state.query()["mode"] == "optimization"
    assert state.query()["objective_id"] == "O-STAND-layover_nights_total"


# ------------------------------------------------------- параметры запуска

def test_прогон_крошечный_и_без_профиля_д1(stub, meta, catalog, tmp_path):
    """Предел прогона — одна секунда; профиль Д1 НЕ задаётся.

    Находка живого прогона (`stand_solver.D1_FINDING`): счётный предел
    режима Д1 прогон стенда не останавливает — поток пула занимается
    навсегда. Числа стенда от хода поиска не зависят вовсе (разбор
    считает назначения стенда), поэтому прогон идёт стенными часами, и
    это отступление названо здесь тестом, а не спрятано."""
    state, url = stub
    _measure("kotlin_constraint", WORLD_1,
             {"constraint": "Layover nights away from home", "matches": 1,
              "amount": 1}, meta=meta, catalog=catalog, url=url,
             base=tmp_path, family="layover_penalty")
    query = state.query()
    assert query["spentLimitSeconds"] == "1"
    assert query["task"] == "pairing", "задача — по зоне записи семьи"
    assert "deterministic" not in query and "stepLimit" not in query
    assert "mode" not in query, "чистые Kotlin-виды идут режимом умолчания"
    assert ss.D1_FINDING and ss.DETERMINISTIC == {"seed": 0, "step_limit": 1}


def test_разбор_счёта_идёт_по_назначениям_стенда(stub, meta, catalog,
                                                 tmp_path):
    """Телом запроса разбора уезжает ЗАДАЧА СТЕНДА с назначениями: счёт
    считается по ним, а не по решению, которое нашёл поиск."""
    state, url = stub
    _measure("kotlin_constraint", WORLD_1,
             {"constraint": "Layover nights away from home", "matches": 1,
              "amount": 1}, meta=meta, catalog=catalog, url=url,
             base=tmp_path, family="layover_penalty")
    problem = state.problem("score-breakdown")
    assert [a["employee"] for a in problem["flightAssignments"]] == ["P1", "P1"]
    assert problem["employees"][0]["homeAirport"] == "ST1"


def test_прогон_ждёт_состояния_готов(stub, meta, catalog, tmp_path):
    """Разбор спрашивается ПОСЛЕ «готов», а не по первому ответу."""
    state, url = stub
    state.states = ["считает", "считает", "готов"]
    _measure("kotlin_constraint", WORLD_1,
             {"constraint": "Layover nights away from home", "matches": 1,
              "amount": 1}, meta=meta, catalog=catalog, url=url,
             base=tmp_path, family="layover_penalty")
    order = [p for p, _q, _b in state.calls]
    assert order.count("/schedules/run/run-1/status") == 3
    assert order[-1].endswith("/score-breakdown")


# --------------------------------------------------------------- исходы

def test_расхождение_с_ожиданием_даёт_failed_словами(stub, meta, catalog,
                                                     tmp_path):
    """Число решателя не сошлось — красная строка с обоими числами."""
    state, url = stub
    state.breakdown = dict(state.breakdown, constraints={
        "Layover nights away from home": {
            "score": "0hard/0soft", "hard": 0, "soft": 0, "matches": 0,
            "weight": "0hard/-1soft"}})
    ctx = {"world": WORLD_1, "family": "layover_penalty", "meta": meta,
           "catalog": catalog, "tmp_dir": tmp_path, "task_id": "st-layover-1",
           "record": {"id": "layover_penalty", "zone": "PAIRING"},
           "families": st.families_for(WORLD_1, "layover_penalty", meta),
           "expected": {"constraint": "Layover nights away from home",
                        "matches": 1, "amount": 1}}
    out = st._judge_with_solver("kotlin_constraint", ctx, st.SolverGate(url))
    assert out.result == "failed"
    assert "получено 0, ожидалось 1" in out.note
    # Прогон и адрес — ПОЛЯМИ строки, а не словами `note`: `note` уезжает
    # в привязки и обязан оставаться каноном каталога, а не прогона.
    assert ctx["run_id"] == "run-1"
    assert "run-1" not in out.note and url not in out.note


def test_совпадение_даёт_passed_с_прогоном_и_пакетом_полями(stub, meta,
                                                            catalog, tmp_path):
    """Зелёная строка прослеживается до прогона службы ПОЛЯМИ строки
    (`run_id`, `package_fingerprint`), а печатная таблица дописывает их
    словами. В `note` их нет: он уезжает в `stand_verified` привязок, и
    идентификатор прогона сделал бы файл привязок каноном ПРОГОНА."""
    state, url = stub
    ctx = {"world": WORLD_1, "family": "layover_penalty", "meta": meta,
           "catalog": catalog, "tmp_dir": tmp_path, "task_id": "st-layover-1",
           "record": {"id": "layover_penalty", "zone": "PAIRING"},
           "families": st.families_for(WORLD_1, "layover_penalty", meta),
           "expected": {"constraint": "Layover nights away from home",
                        "matches": 1, "amount": 1}}
    out = st._judge_with_solver("kotlin_constraint", ctx, st.SolverGate(url))
    assert out.result == "passed", out.note
    assert ctx["run_id"] == "run-1"
    assert ctx["package_fingerprint"] == "f" * 64
    assert "run-1" not in out.note and url not in out.note
    assert state.query()["task"] == "pairing", "зона записи решает задачу"
    row = {"record": "families/layover_penalty", "task": "st-layover-1",
           "executor_kind": "kotlin_constraint", "result": out.result,
           "note": out.note, "run_id": ctx["run_id"],
           "package_fingerprint": ctx["package_fingerprint"]}
    printed = st._table([row])
    assert "прогон run-1" in printed and "пакет ffffffffffff…" in printed


# --------------------------------------------------------- отказы словами

def test_решатель_не_отвечает_отказ_словами_и_без_прогонов(meta, catalog,
                                                           tmp_path):
    """Служба молчит — красная строка с причиной, не пропуск. Живость
    спрашивается ДО прогона: молчащая служба не порождает ни одного."""
    gate = st.SolverGate("http://127.0.0.1:1")
    ctx = {"world": WORLD_1, "family": "layover_penalty", "meta": meta,
           "catalog": catalog, "tmp_dir": tmp_path, "task_id": "st-layover-1",
           "record": {"id": "layover_penalty", "zone": "PAIRING"},
           "families": st.families_for(WORLD_1, "layover_penalty", meta),
           "expected": {"constraint": "Layover nights away from home",
                        "matches": 1, "amount": 1}}
    out = st._judge_with_solver("kotlin_constraint", ctx, gate)
    assert out.result == "failed"
    assert "решатель не отвечает" in out.note
    assert gate.state == "dead"
    assert ctx.get("run_id") is None


def test_пакет_отвергнут_гейтом_решателя_словами_решателя(stub, meta,
                                                          catalog, tmp_path):
    """Отказ гейта решателя поднимается ЕГО словами: своих формулировок
    поверх чужого отказа стенд не сочиняет."""
    state, url = stub
    state.start_status = 422
    state.start_refusal = "режим optimization не разрешён пакетом"
    with pytest.raises(ss.StandSolverError) as e:
        _measure("kotlin_constraint", WORLD_1,
                 {"constraint": "Layover nights away from home",
                  "matches": 1, "amount": 1}, meta=meta, catalog=catalog,
                 url=url, base=tmp_path, family="layover_penalty")
    assert "режим optimization не разрешён пакетом" in str(e.value)


def test_прогон_упал_отказ_с_причиной(stub, meta, catalog, tmp_path):
    """Упавший прогон — отказ словами службы и признак `run_failed`.

    Признак не `unanswered`: служба ОТВЕТИЛА о неудаче ЭТОГО пакета, и
    ранний обрыв серии на такой ответ не ставится (он ничего не
    экономит — падение приходит первым же опросом, — а свидетельство
    прочих строк терял бы).
    """
    state, url = stub
    state.states = ["упал"]
    state.failure = "провайдер не собрался"
    with pytest.raises(ss.StandSolverError) as e:
        _measure("kotlin_constraint", WORLD_1,
                 {"constraint": "Layover nights away from home",
                  "matches": 1, "amount": 1}, meta=meta, catalog=catalog,
                 url=url, base=tmp_path, family="layover_penalty")
    assert "провайдер не собрался" in str(e.value)
    assert e.value.run_failed is True and e.value.unanswered is False


def test_ожидание_не_дождалось_отказ_а_не_вечное_ожидание(
        stub, meta, catalog, tmp_path, monkeypatch):
    """Прогон, не дошедший до «готов», — отказ словами: стенд не висит.

    Потолок ДОКАЗЫВАЕТСЯ, а не описывается: `WAIT_S` подменён на 0,3 с,
    и отказ обязан прийти за секунды, а число опросов `/status` — не
    превысить потолок, делённый на шаг. Прежняя редакция брала потолок
    умолчанием аргумента, подмена не действовала, и тест честно ждал
    минуту — он прошёл бы и при потолке в час.
    """
    state, url = stub
    state.states = ["считает"]
    monkeypatch.setattr(ss, "WAIT_S", 0.3)
    monkeypatch.setattr(ss, "POLL_S", 0.05)
    started = time.monotonic()
    with pytest.raises(ss.StandSolverError) as e:
        _measure("kotlin_constraint", WORLD_1,
                 {"constraint": "Layover nights away from home",
                  "matches": 1, "amount": 1}, meta=meta, catalog=catalog,
                 url=url, base=tmp_path, family="layover_penalty")
    spent = time.monotonic() - started
    assert "не дошёл до состояния «готов»" in str(e.value)
    assert "остался в очереди решателя" in str(e.value)
    assert e.value.run_id == "run-1" and e.value.queued is True
    assert spent < 5, "потолок не сработал: ждали %.1f с" % spent
    polls = [p for p, _q, _b in state.calls if p.endswith("/status")]
    assert len(polls) <= math.ceil(ss.WAIT_S / ss.POLL_S) + 1, len(polls)


def test_потолок_ожидания_читается_в_момент_вызова():
    """`WAIT_S`/`POLL_S` — ручки модуля, а не значения, застывшие при
    определении функции: иначе укоротить ожидание нельзя ни тесту, ни
    стенду (проба занятого пула)."""
    class Stuck:
        def status(self, run_id):
            return {"state": "считает"}

    slept = []
    with pytest.raises(ss.StandSolverError):
        ss.wait_ready(Stuck(), "run-9", wait_s=0.0, poll_s=0.0,
                      sleep=slept.append)
    assert slept == [] or slept == [0.0]


def test_форма_логики_не_порождена_отказ_словами(stub, meta, catalog,
                                                 tmp_path):
    """Правило семьи истории не стало формой у решателя — расхождение
    словами, а не ноль совпадений."""
    state, url = stub
    state.breakdown = dict(state.breakdown, generated=[], constraints={})
    with pytest.raises(ss.StandSolverError) as e:
        _measure("kotlin_logic_form", WORLD_NIGHTS,
                 {"form": "CONSECUTIVE_NIGHT_DUTIES_MAX", "matches": 1,
                  "amount": None}, meta=meta, catalog=catalog, url=url,
                 base=tmp_path, family="night_series")
    assert "решателем не порождена" in str(e.value)
    assert e.value.run_id == "run-1", "отказ после старта несёт прогон"


# ------------------------------------------- форма логики измеряется

def test_форма_логики_берётся_из_показаний_прогона(stub, meta, catalog,
                                                   tmp_path):
    """ФОРМА приходит планом компилятора логики службы
    (`lastRun.logic.plans[]`), а не копией ожидания: у планов её и
    спрашивают."""
    state, url = stub
    state.set_logic("R-STAND-night_series", "CONSECUTIVE_NIGHT_DUTIES_MAX")
    out = _measure("kotlin_logic_form", WORLD_NIGHTS,
                   {"form": "CONSECUTIVE_NIGHT_DUTIES_MAX", "matches": 1,
                    "amount": None}, meta=meta, catalog=catalog, url=url,
                   base=tmp_path, family="night_series")
    assert out["got"]["form"] == "CONSECUTIVE_NIGHT_DUTIES_MAX"
    assert [p for p, _q, _b in state.calls
            if p.endswith("/run/run-1/applied-rules")], (
        "форму обязана дать служба, а не ожидание")


def test_чужая_форма_плана_при_совпадениях_один_даёт_failed(stub, meta,
                                                            catalog,
                                                            tmp_path):
    """Служба породила НЕ ТУ форму, а совпадений всё равно одно.

    Имя порождённого ограничения выводится из идентификатора ПРАВИЛА,
    поэтому две задачи одной семьи (`st-off-1`, `st-off-2`) судятся под
    одним именем: без измерения формы такая строка была бы зелёной по
    построению, и «форма подтверждена» уехало бы в привязки ложью.
    """
    state, url = stub
    state.set_logic("R-STAND-night_series", "CONSECUTIVE_NIGHT_DUTIES_MAX",
                    plan_form="NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX")
    ctx = {"world": WORLD_NIGHTS, "family": "night_series", "meta": meta,
           "catalog": catalog, "tmp_dir": tmp_path, "task_id": "st-night-1",
           "record": {"id": "night_series", "zone": "ROSTERING"},
           "families": st.families_for(WORLD_NIGHTS, "night_series", meta),
           "expected": {"form": "CONSECUTIVE_NIGHT_DUTIES_MAX",
                        "matches": 1, "amount": None}}
    out = st._judge_with_solver("kotlin_logic_form", ctx, st.SolverGate(url))
    assert out.result == "failed", out.note
    assert "NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX" in out.note
    assert ctx["run_id"] == "run-1"


def test_показаний_нет_форма_не_кладётся_в_полученное(stub, meta, catalog,
                                                      tmp_path):
    """Служба формы не отдала — стенд НЕ подставляет ожидание: ключа
    `form` в полученном нет, и судья честно говорит «исполнитель
    значения не дал»."""
    state, url = stub
    state.set_logic("R-STAND-night_series", "CONSECUTIVE_NIGHT_DUTIES_MAX")
    state.applied_status = 404
    ctx = {"world": WORLD_NIGHTS, "family": "night_series", "meta": meta,
           "catalog": catalog, "tmp_dir": tmp_path, "task_id": "st-night-1",
           "record": {"id": "night_series", "zone": "ROSTERING"},
           "families": st.families_for(WORLD_NIGHTS, "night_series", meta),
           "expected": {"form": "CONSECUTIVE_NIGHT_DUTIES_MAX",
                        "matches": 1, "amount": None}}
    out = st._judge_with_solver("kotlin_logic_form", ctx, st.SolverGate(url))
    assert out.result == "failed", out.note
    assert "form" not in (out.got or {})
    assert "исполнитель значения не дал" in out.note


def test_плана_нужного_имени_нет_в_показаниях_отказ_словами(stub, meta,
                                                            catalog,
                                                            tmp_path):
    """Планы есть, а плана порождённого ограничения среди них нет —
    расхождение словами (молча такие показания не пропускаются)."""
    state, url = stub
    state.set_logic("R-STAND-night_series", "CONSECUTIVE_NIGHT_DUTIES_MAX")
    state.applied = {"lastRun": {"logic": {"plans": [
        {"constraint": "logic deadbeef", "form": "REST_IN_ROLLING_WINDOW"}]}}}
    with pytest.raises(ss.StandSolverError) as e:
        _measure("kotlin_logic_form", WORLD_NIGHTS,
                 {"form": "CONSECUTIVE_NIGHT_DUTIES_MAX", "matches": 1,
                  "amount": None}, meta=meta, catalog=catalog, url=url,
                 base=tmp_path, family="night_series")
    assert "нет в показаниях" in str(e.value)
    assert "logic deadbeef" in str(e.value)
    assert e.value.run_id == "run-1"


def test_адрес_решателя_обязателен():
    """Умолчания у адреса нет: без ключа обращаться некуда."""
    with pytest.raises(ss.StandSolverError) as e:
        ss.solver_client("")
    assert "--with-solver" in str(e.value)


def test_клиент_решателя_один_на_дерево():
    """Стенд ходит БОЕВЫМ клиентом кнопки, а не своим вторым."""
    from vmp.run.button import SolverHttp
    assert isinstance(ss.solver_client("http://127.0.0.1:1"), SolverHttp)


def test_папка_прогона_обязательна_а_не_рабочий_каталог(tmp_path):
    """Без папки прогона пакеты писались бы в рабочий каталог процесса —
    отказ словами, а не молчаливая подстановка «здесь»."""
    with pytest.raises(ss.StandSolverError) as e:
        ss.run_base(None, "st-layover-1")
    assert "--tmp-dir" in str(e.value)
    assert ss.run_base(tmp_path, "st-layover-1").is_dir()


# ------------------------------------------------- пул прогонов службы

def test_занятый_пул_опознаётся_первым_прогоном_и_очередь_не_набивается(
        stub, meta, catalog, tmp_path, monkeypatch):
    """Пул службы занят чужой работой — стенд ставит ОДИН прогон, а не
    двадцать четыре.

    Живая находка 08.09.2026: на занятом экземпляре прогон не начинается
    вовсе, и прежний стенд ждал бы потолок каждой строкой, оставляя в
    чужой очереди по прогону на строку.
    """
    state, url = stub
    state.states = ["считает"]
    monkeypatch.setattr(ss, "PROBE_WAIT_S", 0.3)
    monkeypatch.setattr(ss, "POLL_S", 0.05)
    gate = st.SolverGate(url)
    ctx = {"world": WORLD_1, "family": "layover_penalty", "meta": meta,
           "catalog": catalog, "tmp_dir": tmp_path, "task_id": "st-layover-1",
           "record": {"id": "layover_penalty", "zone": "PAIRING"},
           "families": st.families_for(WORLD_1, "layover_penalty", meta),
           "expected": {"constraint": "Layover nights away from home",
                        "matches": 1, "amount": 1}}
    first = st._judge_with_solver("kotlin_constraint", ctx, gate)
    assert first.result == "failed"
    assert "остался в очереди" in first.note
    assert ctx["run_id"] == "run-1", "красная строка называет свой прогон"
    assert gate.state == "busy"
    starts = len([c for c in state.calls if c[0] == "/schedules/run"])
    ctx["run_id"] = None            # как делает run_tasks на каждой строке
    second = st._judge_with_solver("kotlin_constraint", ctx, gate)
    assert second.result == "failed"
    assert "решатель занят" in second.note
    assert len([c for c in state.calls if c[0] == "/schedules/run"]) == starts
    assert ctx["run_id"] is None, "прогона у этой строки не было"


def test_клиент_один_на_серию_и_закрывается(stub, meta, catalog, tmp_path,
                                            monkeypatch):
    """Клиент решателя заводится ОДИН на прогон стенда и закрывается по
    его окончании: пул соединений на каждую строку — не «тот же клиент,
    которым ходит кнопка»."""
    _state, url = stub
    made = []
    real = ss.solver_client

    def spy(address, **kw):
        client = real(address, **kw)
        made.append(client)
        return client

    monkeypatch.setattr(ss, "solver_client", spy)
    rows = st.run_tasks(catalog=catalog, kinds=["kotlin_constraint"],
                        solver_url=url, tmp_dir=tmp_path,
                        records=["layover_penalty"])
    assert rows, "строк вида kotlin_constraint не вышло"
    assert len(made) == 1, "клиент на строку — не замысел кнопки"
    assert made[0]._c.is_closed, "пул соединений остался открытым"


# ------------------------- остаток бюджета внутри строки (правка ревью)

def test_остаток_бюджета_ограничивает_ожидание_готов(stub, meta, catalog,
                                                     tmp_path, monkeypatch):
    """Правка по ревью 08.09.2026. Остаток бюджета команды ограничивает
    строку В ПОЛЁТЕ: ожидание «готов» не длиннее остатка.

    Прежде звенья строки шли своими потолками (ожидание 60 с, клиент
    120 с на обращение), и строка, начатая у самой границы предела,
    переезжала его на минуты — обещание «стенд укладывается в предел
    команды сам» держалось на удаче.
    """
    state, url = stub
    state.states = ["считает"]
    monkeypatch.setattr(ss, "WAIT_S", 60.0)
    monkeypatch.setattr(ss, "POLL_S", 0.05)
    started = time.monotonic()
    with pytest.raises(ss.StandSolverError) as e:
        ss.measure("kotlin_constraint", world=WORLD_1,
                   expected={"constraint": "Layover nights away from home",
                             "matches": 1, "amount": 1},
                   families=st.families_for(WORLD_1, "layover_penalty", meta),
                   meta=meta, stamp=ss.stamp_of(catalog), base=tmp_path,
                   client=ss.solver_client(url), family="layover_penalty",
                   left_s=0.4)
    spent = time.monotonic() - started
    assert spent < 10, "ожидание не сузилось остатком: ждали %.1f с" % spent
    assert e.value.run_id == "run-1"
    assert (e.value.unanswered or e.value.out_of_time), str(e.value)


def test_потолок_обращения_уезжает_клиенту_от_остатка(stub):
    """Потолок ОДНОГО обращения к службе ставится от остатка бюджета: без
    этого начатая у границы строка ждала бы полный потолок клиента (120 с)
    на каждом из трёх обращений. Двойник без ручки — не отказ."""
    client = ss.solver_client(stub[1])
    try:
        ss.set_call_timeout(client, 0.5)
        assert client._c.timeout.read == 0.5
    finally:
        ss.close_client(client)
    ss.set_call_timeout(object(), 0.5)      # клиента без ручки терпим


def test_кончившийся_остаток_посреди_строки_отказ_словами(stub, meta,
                                                          catalog, tmp_path):
    """Остаток, кончившийся ПОСРЕДИ строки, — отказ словами с признаком
    `out_of_time`: судья строки выдаст ему бюджетные слова, и строка
    попадёт в частичные, а не в «решатель молчит»."""
    url = stub[1]
    with pytest.raises(ss.StandSolverError) as e:
        _measure("kotlin_constraint", WORLD_1,
                 {"constraint": "Layover nights away from home",
                  "matches": 1, "amount": 1}, meta=meta, catalog=catalog,
                 url=url, base=tmp_path, family="layover_penalty",
                 left_s=0.0)
    assert e.value.out_of_time is True
    assert "остаток бюджета команды кончился" in str(e.value)
    assert e.value.unanswered is False, "служба тут ни при чём"


# ------------------------------------------------------- отказ метрики

def _metric_record(refusal: str) -> dict:
    return {"id": "total_pairing_cost", "zone": "PAIRING",
            "title": "суммарная стоимость пейрингов",
            "semantics": {"text": "стоимость отобранных цепочек"},
            "refusal": refusal}


def test_ожидаемый_отказ_метрики_сверяется_словами_записи(stub, meta,
                                                          catalog, tmp_path):
    """Запись ждёт отказа — и он обязан прозвучать ТЕМИ ЖЕ словами."""
    state, url = stub
    words = ("метрика total_pairing_cost вычислима только раздельным "
             "прогоном пейринга")
    state.start_status = 422
    state.start_refusal = words + "; похожую величину подставлять нельзя"
    world = copy.deepcopy(WORLD_1)
    world["criterion"] = {"kind": "min_person_days", "rate_per_duty": 7}
    out = _measure("kotlin_metric", world,
                   {"value": None, "matches": None, "refusal": True},
                   meta=meta, catalog=catalog, url=url, base=tmp_path,
                   record=_metric_record(words))
    assert out["got"] == {"value": None, "matches": None, "refusal": True}
    assert state.query()["mode"] == "optimization"


def test_отказ_чужими_словами_это_расхождение(stub, meta, catalog, tmp_path):
    """Отказ прозвучал, но не тот: подтверждать таким ожидание нельзя."""
    state, url = stub
    state.start_status = 422
    state.start_refusal = "режим optimization не разрешён пакетом"
    world = copy.deepcopy(WORLD_1)
    world["criterion"] = {"kind": "min_person_days", "rate_per_duty": 7}
    with pytest.raises(ss.StandSolverError) as e:
        _measure("kotlin_metric", world,
                 {"value": None, "matches": None, "refusal": True},
                 meta=meta, catalog=catalog, url=url, base=tmp_path,
                 record=_metric_record("метрика вычислима только раздельным "
                                       "прогоном пейринга"))
    assert "ДРУГИМИ словами" in str(e.value)


def test_ожидался_отказ_а_прогон_состоялся(stub, meta, catalog, tmp_path):
    """Отказ не прозвучал — ожидание не подтверждено, и это сказано."""
    _state, url = stub
    world = copy.deepcopy(WORLD_1)
    world["criterion"] = {"kind": "min_person_days", "rate_per_duty": 7}
    with pytest.raises(ss.StandSolverError) as e:
        _measure("kotlin_metric", world,
                 {"value": None, "matches": None, "refusal": True},
                 meta=meta, catalog=catalog, url=url, base=tmp_path,
                 record=_metric_record("метрика вычислима только раздельным "
                                       "прогоном пейринга"))
    assert "отказ не прозвучал" in str(e.value)


# --------------------------------------------- форма задачи решателя

def test_аэропорт_в_задаче_назван_кодом_кроме_списка():
    """`Airport` объявлен `@JsonIdentityInfo` по коду: второй полный
    объект того же кода разбор задачи отвергает целиком (живой ответ
    решателя — 400 без тела)."""
    problem = sa.world_to_problem(WORLD_1)
    assert problem["airports"] == [{"code": "ST1"}, {"code": "ST2"}]
    assert problem["employees"][0]["homeAirport"] == "ST1"
    assert problem["flights"][0]["departureAirport"] == "ST1"
    assert problem["window_horizon"] == {
        "start_inclusive": "2026-10-01T00:00:00",
        "end_exclusive": "2026-10-31T00:00:00"}


def test_история_переводится_в_имена_набора_решателя():
    """Имена мира и имена записи истории набора — разные словари, и
    перевод между ними закрыт картой."""
    problem = sa.world_to_problem(WORLD_NIGHTS)
    emp = problem["employees"][0]
    assert emp["history"] == {"historyConsecutiveNightDuties": 0}
    assert emp["historyAsOf"] == "2026-10-02T00:00:00"


def test_незнакомое_поле_истории_мира_отказ_словами():
    """Поле, которого нет в записи набора, молча не пропускается: строгий
    разбор решателя отверг бы задачу целиком без объяснения."""
    world = copy.deepcopy(WORLD_NIGHTS)
    world["history"] = [{"crew": "P1", "выдуманное_поле": 1}]
    with pytest.raises(sa.StandWorldError) as e:
        sa.world_to_problem(world)
    assert "выдуманное_поле" in str(e.value)


def test_имя_ограничения_метрики_читается_из_kotlin():
    """Своей карты «метрика -> ограничение» у стенда нет: имя берётся
    ТЕМ ЖЕ читателем, которым живёт генератор привязок
    (`TaskModes.METRIC_EXECUTORS`). Рукописная копия разошлась бы с
    решателем молча, а стенд отказал бы по ложной причине."""
    kt = pytest.importorskip("vmp.kotlin_tables")
    known = kt.metrics()
    assert not hasattr(ss, "METRIC_CONSTRAINT"), (
        "рукописной карты метрик у стенда быть не должно")
    for metric, row in known.items():
        if row["constraint"]:
            assert ss.metric_constraint(metric) == row["constraint"]
        else:
            # Метрика с объявленным отказом ограничения не несёт — и это
            # отказ словами, а не пустое имя.
            with pytest.raises(ss.StandSolverError):
                ss.metric_constraint(metric)
    with pytest.raises(ss.StandSolverError) as e:
        ss.metric_constraint("выдуманная_метрика")
    assert "METRIC_EXECUTORS" in str(e.value)


def test_имена_истории_совпадают_с_носителем_продукта():
    """Карта имён истории стенда — та же, что у пакета продукта
    (`webapp.task_package.HISTORY_FIELDS`, класс `EmployeeHistory`).

    Сторож нужен потому, что карта переписана: новое поле истории в
    наборе решателя стенд отверг бы словами «перевести не во что», а
    ПЕРЕИМЕНОВАННОЕ уехало бы старым именем молча, и строгий разбор
    набора отверг бы задачу целиком.
    """
    tp = pytest.importorskip("webapp.task_package")
    assert set(sa.HISTORY_FIELD.values()) == {name for _s, _f, name
                                              in tp.HISTORY_FIELDS}


def test_норма_по_базам_едет_каноном_строки(meta):
    """Норма резерва мира — карта {база: норма}, канон носителя — строка
    «ST1:1»: без неё решатель честно отказывается исполнять норму без
    уточнителя-кода базы."""
    world = copy.deepcopy(WORLD_1)
    world["params"] = {"reserve_crews": {"ST1": 1, "ST2": 2}}
    svod, _ = sa.world_to_certificate(world, ["reserve_floors"], meta=meta)
    params = svod["rules"][0]["template_fit"]["params"]
    assert params["reserve_crews"]["value"] == "ST1:1,ST2:2"


# ------------------------------ формы полученного (просьба Ж-2)

def _catalog_expected_keys(cat) -> dict:
    """Ключи ожиданий Kotlin-видов ЖИВОГО каталога: {вид: множество}."""
    out: dict = {}
    records = list(cat.families_full()) + list(cat.criteria().values())
    for record in records:
        for task in record.get("stand_tasks") or ():
            for kind, expected in (task.get("expected") or {}).items():
                if kind.startswith("kotlin_") and isinstance(expected, dict):
                    out.setdefault(kind, set()).update(expected)
    return out


def _fake_metric_breakdown(metric: str) -> dict:
    """Разбор счёта с ограничением метрики: имя читается из Kotlin тем
    же путём, каким его читает стенд."""
    name = ss.metric_constraint(metric)
    return {"score": "0hard/-1soft", "hard": 0, "soft": -1, "generated": [],
            "constraints": {name: {"score": "0hard/-1soft", "hard": 0,
                                   "soft": -1, "matches": 1,
                                   "weight": "0hard/-1soft"}}}


def test_формы_got_равны_ключам_ожиданий_живого_каталога(catalog):
    """Просьба Ж-2. `GOT_FORMS` — ОДИН источник закона о форме ожиданий
    трёх видов решателя, и он сверен с живым каталогом.

    Литерал в коде без такого свидетеля молчалив: конвейер пополнения
    держал те же формы вторым литералом, и разойдись он с каталогом —
    агент писал бы ожидания не той формы, а стенд честно краснел бы по
    ЛОЖНОЙ причине. Ключи формы обязаны совпасть с ключами ожиданий тех
    же видов в `catalog/families.json` и `catalog/criteria.json`.
    """
    seen = _catalog_expected_keys(catalog)
    assert set(seen) == set(ss.GOT_FORMS), sorted(seen)
    for kind, keys in sorted(seen.items()):
        assert set(ss.GOT_FORMS[kind]) == keys, kind


def test_формы_got_равны_тому_что_кладёт_стенд(stub, meta, catalog, tmp_path):
    """Просьба Ж-2. Форма — не только обещание: `measure` кладёт в `got`
    РОВНО объявленные ключи.

    У вида `kotlin_logic_form` ключ `form` необязателен по замыслу
    (служба не отдала показаний — судья говорит «исполнитель значения не
    дал»), поэтому здесь форма измерена, и множество ключей полное.
    """
    state, url = stub
    got = _measure("kotlin_constraint", WORLD_1,
                   {"constraint": "Layover nights away from home",
                    "matches": 1, "amount": 1}, meta=meta, catalog=catalog,
                   url=url, base=tmp_path / "c",
                   family="layover_penalty")["got"]
    assert set(got) == set(ss.GOT_FORMS["kotlin_constraint"])

    rid = "R-STAND-night_series"
    state.set_logic(rid, "CONSECUTIVE_NIGHT_DUTIES_MAX")
    got = _measure("kotlin_logic_form", WORLD_NIGHTS,
                   {"form": "CONSECUTIVE_NIGHT_DUTIES_MAX", "matches": 1,
                    "amount": None}, meta=meta, catalog=catalog, url=url,
                   base=tmp_path / "l", family="night_series")["got"]
    assert set(got) == set(ss.GOT_FORMS["kotlin_logic_form"])

    metric = "layover_nights_total"
    state.breakdown = _fake_metric_breakdown(metric)
    got = _measure("kotlin_metric", WORLD_1,
                   {"value": 1, "matches": 1, "refusal": False}, meta=meta,
                   catalog=catalog, url=url, base=tmp_path / "m",
                   record={"id": metric, "zone": "PAIRING", "title": metric,
                           "semantics": {}})["got"]
    assert set(got) == set(ss.GOT_FORMS["kotlin_metric"])


# --------------------- поле мира -> секция пакета (просьба Ж-5)

#: Мир задачи семьи `calendar_windows`: форма правила объявлена МИРОМ.
WORLD_WINDOW = dict(copy.deepcopy(WORLD_1), rule_form={"days": ["thursday"]})


def test_секция_пакета_едет_по_таблице_поля_мира(stub, meta, catalog,
                                                 tmp_path):
    """Просьба Ж-5. Дни окна доезжают до пакета НОСИТЕЛЕМ ТАБЛИЦЫ.

    Таблица `stand_adapters.WORLD_TO_PACKAGE_SECTION` называет оба конца
    — поле мира и секцию `threshold-forms.json`, — и носителя, который
    их связывает. Раньше это знание жило условием по имени семьи внутри
    сборщика пакета, и новая секция требовала правки стенда.
    """
    state, url = stub
    row = sa.WORLD_TO_PACKAGE_SECTION["calendar_windows"]
    assert row["world_field"] == "rule_form.days"
    assert row["section"] == "calendar_window_penalties"
    name = "Calendar window layover penalty"
    state.breakdown = dict(state.breakdown, constraints={
        name: {"score": "0hard/-1soft", "hard": 0, "soft": -1,
               "matches": 1, "weight": "0hard/-1soft"}})
    _measure("kotlin_constraint", WORLD_WINDOW,
             {"constraint": name, "matches": 1, "amount": 1}, meta=meta,
             catalog=catalog, url=url, base=tmp_path,
             family="calendar_windows")
    files = state.package()
    model = json.loads(files["model.json"])
    window = [r for r in model["rules"]
              if r["template_fit"]["family"] == "calendar_windows"][0]
    assert "четверг" in window["template_fit"]["quote"]
    # Секция собралась В ПАКЕТЕ, а не осталась обещанием таблицы.
    forms = json.loads(files["threshold-forms.json"])
    assert [f["days"] for f in forms[row["section"]]] == [["thursday"]]


def test_секция_вне_таблицы_отказ_словами(meta, catalog, tmp_path):
    """Просьба Ж-5. Мир объявил форму правила, а таблица семью задачи не
    называет — отказ СЛОВАМИ, а не пакет без формы.

    Молчаливый пропуск здесь дал бы близнецам вида `kotlin_form`
    одинаковые числа: ограничение решателя осталось бы без фактов, и обе
    строки зеленели бы по построению.
    """
    world = dict(copy.deepcopy(WORLD_1), rule_form={"days": ["thursday"]})
    rules = ss._rules_for(world, ["layover_penalty"], meta)
    with pytest.raises(ss.StandSolverError) as e:
        ss.model_doc(rules, world=world, family="layover_penalty")
    words = str(e.value)
    assert "из мира стенда в пакет не собирается" in words
    assert "WORLD_TO_PACKAGE_SECTION" in words and "days" in words
    # Мир БЕЗ формы правила тот же пакет собирает молча и законно.
    assert ss.model_doc(ss._rules_for(WORLD_1, ["layover_penalty"], meta),
                        world=WORLD_1, family="layover_penalty")["rules"]


def test_носитель_таблицы_без_функции_отказ_словами(meta, monkeypatch):
    """Просьба Ж-5. Строка таблицы, назвавшая носителя, которого в стенде
    нет, — тоже отказ словами: таблица не должна обещать больше, чем
    стенд умеет."""
    monkeypatch.setitem(sa.WORLD_TO_PACKAGE_SECTION, "layover_penalty",
                        {"world_field": "rule_form.days",
                         "section": "выдуманная_секция",
                         "carrier": "носителя-нет", "note": "проба"})
    world = dict(copy.deepcopy(WORLD_1), rule_form={"days": ["thursday"]})
    rules = ss._rules_for(world, ["layover_penalty"], meta)
    with pytest.raises(ss.StandSolverError) as e:
        ss.model_doc(rules, world=world, family="layover_penalty")
    assert "выдуманная_секция" in str(e.value)
    assert "из мира стенда в пакет не собирается" in str(e.value)


def test_поле_мира_вне_строки_таблицы_отказ_словами(meta, catalog, tmp_path):
    """Правка по ревью 08.09.2026. Семья В ТАБЛИЦЕ, а мир объявил ПОЛЕ,
    которого строка не называет, — отказ СЛОВАМИ.

    Прежде поле `world_field` не судилось ни с чем: носитель звался по
    одному факту существования строки, «повёз» возвращалось безусловно,
    и мир с чужим полем уходил молча — пакет без секции, близнецы вида
    `kotlin_form` с одинаковыми числами, красный близнец по ЛОЖНОЙ
    причине (числа не разошлись, а секции не было).
    """
    world = dict(copy.deepcopy(WORLD_1), rule_form={"weeks": [1]})
    rules = ss._rules_for(world, ["calendar_windows"], meta)
    with pytest.raises(ss.StandSolverError) as e:
        ss.model_doc(rules, world=world, family="calendar_windows")
    words = str(e.value)
    assert "поле мира weeks секцией пакета не несётся" in words
    assert "rule_form.days" in words and "calendar_window_penalties" in words
    # Пустое поле рядом с чужим — тот же отказ: считается объявленное.
    world_both = dict(copy.deepcopy(WORLD_1),
                      rule_form={"days": [], "weeks": [1]})
    with pytest.raises(ss.StandSolverError):
        ss.model_doc(ss._rules_for(world_both, ["calendar_windows"], meta),
                     world=world_both, family="calendar_windows")


def test_семья_в_таблице_но_правила_в_своде_нет_свои_слова(meta):
    """Правка по ревью 08.09.2026. Семья названа таблицей, а правила её
    в своде пакета нет — слова говорят про СОСТАВ СВОДА, а не советуют
    «назовите там семью задачи»: она названа, и совет вёл бы не туда."""
    world = dict(copy.deepcopy(WORLD_1), rule_form={"days": ["thursday"]})
    rules = ss._rules_for(world, ["duty_limits"], meta)
    with pytest.raises(ss.StandSolverError) as e:
        ss.model_doc(rules, world=world, family="calendar_windows")
    words = str(e.value)
    assert "правила этой семьи в своде пакета нет" in words
    assert "назовите там семью задачи" not in words


def test_каждая_строка_таблицы_называет_живого_носителя():
    """Обещания таблицы и умения стенда сведены: носитель каждой строки
    существует, а секция названа непустым именем."""
    for family, row in sorted(sa.WORLD_TO_PACKAGE_SECTION.items()):
        assert row["carrier"] in ss.SECTION_CARRIERS, family
        assert row["section"] and row["world_field"], family
    assert set(sa.PACKAGE_SECTION_FAMILY) == {
        row["section"] for row in sa.WORLD_TO_PACKAGE_SECTION.values()}


# ------------------------------ переключатель профиля Д1

def _measure_d1(url, meta, catalog, tmp_path):
    return ss.measure(
        "kotlin_constraint", world=WORLD_1,
        expected={"constraint": "Layover nights away from home",
                  "matches": 1, "amount": 1},
        families=st.families_for(WORLD_1, "layover_penalty", meta),
        meta=meta, stamp=ss.stamp_of(catalog), base=tmp_path,
        client=ss.solver_client(url), family="layover_penalty",
        deterministic=dict(ss.DETERMINISTIC))


def test_профиль_д1_включается_ключом_и_уезжает_прогоном(stub, meta, catalog,
                                                         tmp_path):
    """Переключатель Д1: по умолчанию профиля нет (тест «прогон крошечный
    и без профиля Д1» выше), с ним — зерно и счётный предел уезжают
    запросом прогона, а слова строки называют профиль."""
    state, url = stub
    out = _measure_d1(url, meta, catalog, tmp_path)
    query = state.query()
    assert query["deterministic"] == "true"
    assert query["seed"] == "0" and query["stepLimit"] == "1"
    assert "профиль Д1" in out["note"]


def test_признак_остановки_прогона_читается_из_ответа_службы(
        stub, meta, catalog, tmp_path):
    """Признак остановки Д1 приходит ОТВЕТОМ ПРОГОНА (`termination`
    правки решателя 08.09.2026), а не константой стенда, и живёт ПОЛЕМ
    строки: вид, признак воспроизводимости, слова.

    Изменчивых чисел прогона (шаги, миллисекунды) в словах нет по
    замыслу, а сам вид остановки не уезжает в `note`: `note` идёт в
    `stand_verified` привязок и сделал бы файл каноном ПРОГОНА, а не
    каталога (правка по ревью 08.09.2026 — вид остановки зависит от
    секундомера, см. тест ниже).
    """
    state, url = stub
    state.states = ["готов"]
    state.termination = {"version": 1, "kind": "wall_clock_safety",
                         "mode": "deterministic", "reproducible": False,
                         "search_steps": 1, "elapsed_ms": 1017,
                         "words": "остановлен СТРАХОВОЧНЫМ пределом"}
    out = _measure_d1(url, meta, catalog, tmp_path)
    stop = out["termination"]
    assert stop["kind"] == "wall_clock_safety"
    assert stop["reproducible"] is False
    assert ss.TERMINATION_KINDS["wall_clock_safety"] in stop["words"]
    assert "воспроизводимо: нет" in stop["words"]
    assert stop["solver_words"] == "остановлен СТРАХОВОЧНЫМ пределом"
    assert "1017" not in json.dumps(stop, ensure_ascii=False)
    # В `note` — только устойчивая голова профиля.
    assert "профиль Д1" in out["note"]
    assert "остановка" not in out["note"] and "воспроизводимо" \
        not in out["note"]


def test_вид_остановки_не_дрожит_в_словах_строки(stub, meta, catalog,
                                                 tmp_path):
    """Два прогона одного мира с РАЗНЫМ признаком остановки дают
    ОДИНАКОВЫЙ `note`.

    Признак зависит от секундомера службы: страховка стенных часов
    внутри Д1 судится раньше счётного предела (зазор 250 мс правки
    решателя), и живой замер 08.09.2026 дал на одном профиле и мире то
    `step_count` (104 мс), то `wall_clock_safety` (1017 мс). Уезжай он в
    `note`, канон привязок дрожал бы от прогона к прогону — ровно то,
    чего правило стенда «изменчивых значений в словах строки НЕТ» не
    допускает.
    """
    state, url = stub
    state.termination = {"kind": "step_count", "reproducible": True}
    first = _measure_d1(url, meta, catalog, tmp_path)
    state.termination = {"kind": "wall_clock_safety", "reproducible": False}
    second = _measure_d1(url, meta, catalog, tmp_path)
    assert first["note"] == second["note"]
    assert first["got"] == second["got"]
    # Разница видна ПОЛЕМ строки — паспортом прогона, а не каноном.
    assert first["termination"]["kind"] != second["termination"]["kind"]


def test_признак_остановки_вне_перечня_назван_словами(stub, meta, catalog,
                                                      tmp_path):
    """Вид остановки вне закрытого перечня контракта стенд НАЗЫВАЕТ, а не
    проглатывает: разошедшийся контракт решателя должен быть виден."""
    state, url = stub
    state.termination = {"kind": "выдуманный_вид", "reproducible": True}
    stop = _measure_d1(url, meta, catalog, tmp_path)["termination"]
    assert "вид вне закрытого перечня стенда" in stop["words"]
    assert "воспроизводимо: да" in stop["words"]


def test_служба_без_правки_д1_признака_не_даёт_и_это_сказано(
        stub, meta, catalog, tmp_path):
    """Служба старой сборки отвечает без поля `termination` — поля у
    строки нет, а слова о старой сборке у стенда есть: он говорит это, а
    не молчит и не выдумывает вид остановки."""
    url = stub[1]
    out = _measure_d1(url, meta, catalog, tmp_path)
    assert out["termination"] is None
    words = ss.termination_words({"state": "готов"})
    assert "признак остановки службой не назван" in words
    assert "без правки Д1" in words


def test_место_признака_остановки_названо_одной_строкой():
    """`D1_STOP_SIGN` называет, ГДЕ живёт признак (поле ответа), а не
    что-то, что стенд решает сам: константа рассказывала бы о правке, а
    поле — об этом прогоне."""
    assert "termination" in ss.D1_STOP_SIGN
    assert set(ss.TERMINATION_KINDS) == {
        "step_count", "unimproved_step_count", "wall_clock_safety",
        "wall_clock", "search_exhausted", "cancelled"}


def test_отказ_готов_под_профилем_д1_называет_профиль_словами(
        stub, meta, catalog, tmp_path, monkeypatch):
    """Отказ по ожиданию «готов» ПОД профилем Д1 называет профиль и
    состояние правки решателя: иначе читатель ищет мёртвую службу там,
    где виновата известная находка остановки (`D1_FINDING`)."""
    state, url = stub
    state.states = ["считает"]
    monkeypatch.setattr(ss, "WAIT_S", 0.01)
    monkeypatch.setattr(ss, "POLL_S", 0.0)
    with pytest.raises(ss.StandSolverError) as e:
        ss.measure(
            "kotlin_constraint", world=WORLD_1,
            expected={"constraint": "Layover nights away from home",
                      "matches": 1, "amount": 1},
            families=st.families_for(WORLD_1, "layover_penalty", meta),
            meta=meta, stamp=ss.stamp_of(catalog), base=tmp_path,
            client=ss.solver_client(url), family="layover_penalty",
            deterministic=dict(ss.DETERMINISTIC))
    words = str(e.value)
    assert "не дошёл до состояния «готов»" in words
    assert "профиль Д1" in words and "D1_FINDING" in words
    assert "termination" in words, (
        "отказ называет, ГДЕ признак остановки: службы без правки Д1 его "
        "не дают, и это первое, что стоит проверить")
    assert e.value.unanswered is True and e.value.queued is True


def test_отказ_ожидания_готов_помечен_а_отказ_гейта_нет(stub, meta, catalog,
                                                        tmp_path):
    """Признак `unanswered` отличает отказ БЕЗ ЖИВОГО ОТВЕТА службы от
    отказа ГЕЙТА решателя: на первом стоит ранний обрыв серии (Ж-3), а
    отвергнутый пакет — законный ответ живой службы и серию обрывать не
    должен."""
    state, url = stub
    state.start_status = 400
    with pytest.raises(ss.StandSolverError) as e:
        _measure("kotlin_constraint", WORLD_1,
                 {"constraint": "Layover nights away from home",
                  "matches": 1, "amount": 1}, meta=meta, catalog=catalog,
                 url=url, base=tmp_path, family="layover_penalty")
    assert e.value.unanswered is False


def test_служба_не_ответила_на_постановке_прогона_это_помечено(
        meta, catalog, tmp_path):
    """Транспортный отказ НА ПОСТАНОВКЕ прогона (служба не отвечает по
    адресу) несёт `unanswered`: ранний обрыв серии обязан срабатывать в
    самом буквальном случае «решатель не отвечает».

    Прежде такой отказ приходил без признака, и `note_failure` считал
    его ответом живой службы — счётчик обнулялся на каждой строке, а
    каждая ждала полный потолок клиента.
    """
    # Порт закрыт: соединение не встанет, и это не ответ гейта.
    url = "http://127.0.0.1:9"
    with pytest.raises(ss.StandSolverError) as e:
        _measure("kotlin_constraint", WORLD_1,
                 {"constraint": "Layover nights away from home",
                  "matches": 1, "amount": 1}, meta=meta, catalog=catalog,
                 url=url, base=tmp_path, family="layover_penalty")
    assert e.value.unanswered is True, str(e.value)
    assert e.value.run_failed is False
