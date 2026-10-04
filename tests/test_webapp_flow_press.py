# -*- coding: utf-8 -*-
"""Схема конвейера по нажатиям на пакете постановки (слово владельца
05.09.2026: каждый блок обязан работать в свой момент, блоки без момента
исключены): десять блоков (06.09.2026: у каждого решения пара проверок
— по ВМП и по ИДАК, сверка с документами — отдельный блок), состояния
по шагам кнопки Ш-0…Ш-4г."""
from __future__ import annotations

from webapp import flow


def _press(monkeypatch, st, src=None):
    from webapp import package_runs as pr
    monkeypatch.setattr(pr, "status", lambda attempt=None: st)
    src = src or {}
    monkeypatch.setattr(flow, "_source_check_state",
                        lambda task=None: dict(src.get(task)
                                               or {"status": "idle"}))


def _ids(blocks):
    return [b["id"] for b in blocks]


EIGHT = ["vmp", "conform", "pairing.pool", "pairing.select", "accept",
         "pair.src", "roster", "srccheck", "roster.src", "out"]


def test_no_package_keeps_legacy_scheme(monkeypatch):
    _press(monkeypatch, {"present": False})
    assert flow._press_blocks() is None


def test_pairing_running_at_solver_step_lights_selection(monkeypatch):
    _press(monkeypatch, {"present": True, "package_sha": "abc", "task": "pairing",
                         "done": False, "state": "кнопка: идёт · Ш-2 — построитель запущен",
                         "files": {}, "engine": "cpsat"})
    blocks = flow._press_blocks()
    by = {b["id"]: b for b in blocks}
    assert _ids(blocks) == EIGHT
    assert by["conform"]["status"] == "done"
    assert by["pairing.pool"]["status"] == "done"
    assert by["pairing.select"]["status"] == "working"
    assert by["accept"]["status"] == "waiting"
    assert by["roster"]["status"] == "idle"


def test_pairing_gate_step_lights_conformance(monkeypatch):
    _press(monkeypatch, {"present": True, "package_sha": "abc", "task": "pairing",
                         "done": False, "state": "кнопка: идёт · Ш-2м — матрица",
                         "files": {}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["conform"]["status"] == "working"
    assert by["pairing.select"]["status"] == "waiting"


def test_pairing_certification_lights_vmp_check(monkeypatch):
    _press(monkeypatch, {"present": True, "package_sha": "abc", "task": "pairing",
                         "done": False, "state": "кнопка: идёт · Ш-4 — сертификация",
                         "files": {}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["pairing.select"]["status"] == "done"
    assert by["accept"]["status"] == "working"


def test_rostering_running_and_done(monkeypatch):
    _press(monkeypatch, {"present": True, "package_sha": "abc", "task": "rostering",
                         "done": False, "state": "кнопка: идёт · Ш-2 — принят решателем",
                         "files": {"pairing": ["каталог-цепочек"]}, "verdict": None,
                         "pairing_run": {"chains": 42}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["pairing.pool"]["status"] == "done"
    assert by["pairing.pool"]["params"] == {"chains": 42}
    assert by["roster"]["status"] == "working"
    assert by["srccheck"]["status"] == "waiting"
    assert by["out"]["status"] == "waiting"
    _press(monkeypatch, {"present": True, "package_sha": "abc", "task": "rostering",
                         "done": True, "state": "кнопка: допустимо",
                         "files": {"pairing": ["каталог-цепочек"],
                                   "rostering": ["выдача-кнопки"]},
                         "verdict": "допустимо"})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["roster"]["status"] == "done"
    assert by["srccheck"]["status"] == "done"
    assert by["out"]["status"] == "done"
    assert by["out"]["params"] == {"verdict": "допустимо"}


def test_rostering_not_certified_keeps_out_waiting(monkeypatch):
    _press(monkeypatch, {"present": True, "package_sha": "abc", "task": "rostering",
                         "done": True, "state": "кнопка: не удостоверено: непроверено 1 правил",
                         "files": {"rostering": ["выдача-кнопки"]},
                         "verdict": "не удостоверено: непроверено 1 правил"})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["out"]["status"] == "waiting"


def test_source_check_protocol_lights_roster_src_not_vmp_block(monkeypatch):
    # Сверка ростера с документами — свой блок roster.src; блок
    # «Проверка ростера по ВМП» (srccheck) живёт сертификатом кнопки.
    _press(monkeypatch, {
        "present": True, "package_sha": "abc", "task": "rostering", "done": True,
        "state": "кнопка: допустимо", "files": {"rostering": ["выдача-кнопки"]},
        "verdict": "допустимо"},
        src={"rostering": {"status": "working", "run": "r1"}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["roster.src"]["status"] == "working"
    # на плашке блока — только счётчики; прогон/время/читатель — в панели
    assert by["roster.src"]["params"] == {}
    assert by["srccheck"]["status"] == "done"
    assert by["srccheck"]["params"] == {"verdict": "допустимо"}
    # пейринга нет — его блок ИДАК не активен
    assert by["pair.src"]["status"] == "idle"


def test_pairing_source_check_done_lights_pair_src(monkeypatch):
    _press(monkeypatch, {
        "present": True, "package_sha": "abc", "task": "pairing", "done": True,
        "state": "кнопка: пейринг построен",
        "files": {"pairing": ["выдача-кнопки-пейринга"]},
        "verdict": "пейринг построен"},
        src={"pairing": {"status": "done", "checks": 9, "violated": 0,
                         "reader": "cli/claude-sonnet-5", "run": "run-0081"}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["pair.src"]["status"] == "done"
    assert by["pair.src"]["params"]["violated"] == 0
    # читатель на плашку не выносится (06.09.2026: семь плашек раздували блок)
    assert "reader" not in by["pair.src"]["params"]
    assert by["pair.src"]["params"]["checks"] >= 1
    # ростера ещё нет — его блоки ВМП и ИДАК не активны
    assert by["srccheck"]["status"] == "idle"
    assert by["roster.src"]["status"] == "idle"


def test_solution_without_source_check_keeps_src_block_waiting(monkeypatch):
    # Решение есть, сверка с документами не запускалась — блок ждёт
    # (человек нажимает кнопку сам), а не «выполнен» и не «не активен».
    _press(monkeypatch, {
        "present": True, "package_sha": "abc", "task": "rostering", "done": True,
        "state": "кнопка: допустимо",
        "files": {"pairing": ["выдача-кнопки-пейринга"],
                  "rostering": ["выдача-кнопки"]},
        "verdict": "допустимо"})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["pair.src"]["status"] == "waiting"
    assert by["roster.src"]["status"] == "waiting"
    assert _ids(flow._press_blocks()) == EIGHT


def test_package_without_presses_is_package_scheme_not_legacy(monkeypatch):
    # Пакет принят, нажатий нет: схема пакета (приём и соответствие
    # выполнены, остальное не активно / ждёт), а не легаси-задания.
    _press(monkeypatch, {"present": True, "package_sha": "abc",
                         "task": None, "files": {}})
    blocks = flow._press_blocks()
    assert blocks is not None and _ids(blocks) == EIGHT
    by = {b["id"]: b for b in blocks}
    assert by["vmp"]["status"] == "done" and by["conform"]["status"] == "done"
    for bid in ("pairing.pool", "pairing.select", "accept", "pair.src",
                "roster", "srccheck", "roster.src", "out"):
        assert by[bid]["status"] == "idle", bid


def test_source_check_of_other_package_does_not_light_block(monkeypatch):
    # Снимок сверки от прежнего пакета (run-0081) не светится
    # «выполнено» у нового пакета (run-0082) — блок ждёт.
    _press(monkeypatch, {
        "present": True, "package_sha": "abc", "task": "pairing", "done": True,
        "state": "кнопка: пейринг построен", "source_run": "run-0082",
        "files": {"pairing": ["выдача-кнопки-пейринга"]},
        "verdict": "пейринг построен"},
        src={"pairing": {"status": "done", "checks": 11, "run": "run-0081"}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["pair.src"]["status"] == "waiting"
    # тот же прогон — выполнено
    _press(monkeypatch, {
        "present": True, "package_sha": "abc", "task": "pairing", "done": True,
        "state": "кнопка: пейринг построен", "source_run": "run-0082",
        "files": {"pairing": ["выдача-кнопки-пейринга"]},
        "verdict": "пейринг построен"},
        src={"pairing": {"status": "done", "checks": 11, "run": "run-0082"}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["pair.src"]["status"] == "done"


def test_rostering_on_pairing_delivery_counts_as_roster(monkeypatch):
    # Выдача «на утверждённом пейринге» (button-on-pairing) — тоже ростер:
    # блоки ростера, сертификата и решений выполнены.
    _press(monkeypatch, {
        "present": True, "package_sha": "abc", "task": "rostering", "done": True,
        "state": "кнопка: допустимо", "verdict": "допустимо",
        "roster_path": "on-pairing",
        "files": {"pairing": ["выдача-кнопки-пейринга"],
                  "rostering_on_pairing": ["выдача-кнопки-на-пейринге",
                                           "сертификат-кнопки-на-пейринге"]}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["roster"]["status"] == "done"
    assert by["srccheck"]["status"] == "done"
    assert by["out"]["status"] == "done"
    assert by["roster.src"]["status"] == "waiting"


def test_running_rostering_keeps_roster_src_waiting(monkeypatch):
    _press(monkeypatch, {
        "present": True, "package_sha": "abc", "task": "rostering", "done": False,
        "state": "кнопка: Ш-2 — принят решателем",
        "files": {"rostering": ["выдача-кнопки"]}},
        src={"rostering": {"status": "done", "checks": 3}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["roster.src"]["status"] == "waiting", (
        "идущий расчёт — прежний протокол сверки к новому решению не относится")


# --- Прогоны COSMOS на схеме (07.09.2026) --------------------------------

def test_vmp_params_carry_run_before_source_run(monkeypatch):
    # Плашка «Постановка (ВМП)»: сначала прогон COSMOS, затем прогон CDPM,
    # затем сумма пакета — ключи в этом порядке.
    _press(monkeypatch, {"present": True, "package_sha": "abcdef1234567890",
                         "task": None, "files": {}, "source_run": "run-0082",
                         "run": {"run": "run-0003", "title": "Демо",
                                 "workdir": "package-runs/abc", "implicit": False}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert list(by["vmp"]["params"]) == ["run", "source_run", "instance"]
    assert by["vmp"]["params"]["run"] == "run-0003"
    assert by["vmp"]["params"]["instance"] == "abcdef123456"
    # прогон строкой — тоже понят
    _press(monkeypatch, {"present": True, "package_sha": "abc", "task": None,
                         "files": {}, "run": "run-0004"})
    assert {b["id"]: b for b in flow._press_blocks()}["vmp"]["params"]["run"] == "run-0004"


def test_press_blocks_idle_for_run_without_package(monkeypatch):
    from webapp import cosmos_runs
    _press(monkeypatch, {"present": False})
    monkeypatch.setattr(cosmos_runs, "current_run_summary",
                        lambda: {"run": "run-0007", "title": None,
                                 "state_kind": "new", "package_sha12": None,
                                 "packages_count": 0, "implicit": False,
                                 "busy_previous": None})
    blocks = flow._press_blocks()
    assert _ids(blocks) == EIGHT
    assert all(b["status"] == "idle" for b in blocks)
    assert blocks[0]["params"] == {"run": "run-0007"}
    assert all(b["params"] == {} for b in blocks[1:])
    # неявный прогон пакета «файлом» без пакета схему не заводит
    monkeypatch.setattr(cosmos_runs, "current_run_summary",
                        lambda: {"run": "run-0001", "implicit": True})
    assert flow._press_blocks() is None


def test_press_blocks_legacy_none_without_run_and_package(monkeypatch):
    from webapp import cosmos_runs
    _press(monkeypatch, {"present": False})
    monkeypatch.setattr(cosmos_runs, "current_run_summary", lambda: None)
    assert flow._press_blocks() is None


def test_build_state_carries_run_and_hint(monkeypatch):
    from webapp import cosmos_runs
    _press(monkeypatch, {"present": False})
    monkeypatch.setattr(cosmos_runs, "current_run_summary", lambda: None)
    monkeypatch.setattr(cosmos_runs, "hint_code", lambda: "storage-off")
    st = flow.build_state()
    assert st["run"] is None and st["runs_hint_code"] == "storage-off"
    run = {"run": "run-0002", "title": "Демо", "state_kind": "accepted",
           "package_sha12": "abcdef123456", "packages_count": 1,
           "implicit": False, "busy_previous": None}
    monkeypatch.setattr(cosmos_runs, "current_run_summary", lambda: run)
    monkeypatch.setattr(cosmos_runs, "hint_code", lambda: None)
    st = flow.build_state()
    assert st["run"] == run and st["runs_hint_code"] is None


def test_source_check_filtered_by_package_sha_before_run(monkeypatch):
    # Снимок с отпечатком чужого пакета отсекается, даже если прогон CDPM
    # совпал; снимок того же пакета — выполнено.
    base = {"present": True, "package_sha": "abc", "task": "pairing", "done": True,
            "state": "кнопка: пейринг построен", "source_run": "run-0082",
            "files": {"pairing": ["выдача-кнопки-пейринга"]},
            "verdict": "пейринг построен"}
    _press(monkeypatch, base,
           src={"pairing": {"status": "done", "checks": 11, "run": "run-0082",
                            "package_sha": "zzz", "cosmos_run": "run-0001"}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["pair.src"]["status"] == "waiting"
    _press(monkeypatch, base,
           src={"pairing": {"status": "done", "checks": 11, "violated": 0,
                            "run": "run-0082", "package_sha": "abc",
                            "cosmos_run": "run-0001"}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["pair.src"]["status"] == "done"
    assert by["pair.src"]["params"] == {"checks": 11, "violated": 0, "grade": "ok"}
    assert "cosmos_run" not in by["pair.src"]["params"]


def test_attempt_record_counts_do_not_replace_scoped_source_check(monkeypatch):
    # Historical counts remain history; without a bound snapshot they cannot
    # prove the identity of the panel and its downloadable protocol.
    _press(monkeypatch, {
        "present": True, "package_sha": "abc", "task": "rostering", "done": True,
        "state": "кнопка: допустимо", "verdict": "допустимо",
        "files": {"rostering": ["выдача-кнопки"]},
        "attempts": [{"id": "R2", "task": "rostering", "done": True,
                      "source_check": {"task": "rostering",
                                       "counts": {"violated": 1, "satisfied": 4,
                                                  "not_checkable": 2}}},
                     {"id": "R1", "task": "rostering", "done": True}]},
        src={"rostering": {"status": "done", "checks": 3, "package_sha": "zzz"}})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["roster.src"]["status"] == "waiting"
    assert "checks" not in by["roster.src"]["params"]
    # последняя попытка без сверки — блок ждёт, старая запись не подменяет
    _press(monkeypatch, {
        "present": True, "package_sha": "abc", "task": "rostering", "done": True,
        "state": "кнопка: допустимо", "verdict": "допустимо",
        "files": {"rostering": ["выдача-кнопки"]},
        "attempts": [{"id": "R3", "task": "rostering", "done": True},
                     {"id": "R2", "task": "rostering", "done": True,
                      "source_check": {"counts": {"violated": 1}}}]})
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["roster.src"]["status"] == "waiting"


def test_grade_delivery_is_module_level_and_unchanged():
    assert flow.grade_delivery(None) is None
    assert flow.grade_delivery({"certified": True, "result": {"feasibility": "confirmed"}}) == "ok"
    assert flow.grade_delivery({"certified": True, "findings": [1]}) == "warn"
    assert flow.grade_delivery({"certified": False}) == "bad"
    assert flow.grade_delivery({"verdict": "недопустимо"}) == "bad"
