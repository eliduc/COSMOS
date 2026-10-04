"""Тесты пяти эндпоинтов страницы «Контуры» (/api/loops*) и идемпотентности
трёх ДЕТЕРМИНИРОВАННЫХ контуров (волна 1, ревью 2026-07-20).

До этого файла /api/loops* не были покрыты ничем. Здесь проверяются:
  * админ-гейт (не-админ получает 403 на всех пяти),
  * 404 на неизвестном экземпляре,
  * форма ответа и ПОРЯДОК контуров на пустых хранилищах,
  * коды видов дефектов и причин вместо русских подписей (пятиязычие),
  * нижняя граница min_size у /api/loops/overrides,
  * единственное чтение хранилища снимков за запрос (была квадратика),
  * дедупликация черновиков: два запуска подряд дают ОДИН CR, не два.

Все хранилища переведены в tmp: ни один тест не пишет в рабочие настройки
webapp/config (инвариант волны 1). Ни один вызов языковой модели здесь не
происходит — все три контура детерминированные.
"""
import json
import os
from pathlib import Path

import pytest
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
PLAIN = "plain-user@example.com"


@pytest.fixture()
def env(tmp_path):
    """TestClient в dev-режиме + ВСЕ хранилища контуров в tmp.

    Возвращает объект с клиентом и модулями, чтобы тест мог засеять
    наблюдения напрямую через API модулей, а не через файлы.
    """
    try:
        from webapp import (change_requests, connection_buffer, data_health,
                            evolution_runner, help_center, planner_feedback,
                            registry, run_profile, solver_advisor, users)
        from webapp.app import app
    except Exception as exc:                        # noqa: BLE001
        pytest.fail(f"webapp.app is not importable: {exc!r}")
    from fastapi.testclient import TestClient

    mp = pytest.MonkeyPatch()
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", ADMIN)
    mp.delenv("CREW_ACCESS_AUD", raising=False)
    mp.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    mp.setattr(registry, "OWNERSHIP_PATH", tmp_path / "ownership.json")
    # Хранилища наблюдений и очередь CR — в tmp: контур не пишет в рабочие
    # настройки, и тесты тем более.
    mp.setattr(change_requests, "CR_PATH", tmp_path / "change_requests.json")
    mp.setattr(data_health, "SNAPSHOT_PATH", tmp_path / "data_health.json")
    mp.setattr(planner_feedback, "OVERRIDE_PATH",
               tmp_path / "override_log.json")
    mp.setattr(run_profile, "PROFILE_PATH", tmp_path / "run_profile.json")
    mp.setattr(connection_buffer, "SNAPSHOT_PATH",
               tmp_path / "connection_buffer.json")
    mp.setattr(help_center, "SUGGESTIONS_PATH", tmp_path / "suggestions.json")
    # Наблюдения ДВУХ тяжёлых контуров тоже живут в файлах рабочего
    # webapp/config: без изоляции сводка контуров в тестах читала бы дату
    # скана и версию базы знаний РАБОЧЕЙ установки, и на стенде с историей
    # «пустые хранилища» перестали бы быть пустыми.
    mp.setattr(evolution_runner, "LAST_SCAN_AT_PATH",
               tmp_path / "evolution-last-scan-at.txt")
    # ВСЕ четыре пути модуля, а не только два читаемых: HISTORY_DIR
    # вычислен из SKILL_DIR на импорте, и подмена одного SKILL_PATH его не
    # двигает. save_skill() снимает копию уходящей версии в HISTORY_DIR —
    # при первом сохранении не снимает (файла ещё нет), а при втором уже
    # писала бы skill-v1.md в РАБОЧУЮ базу знаний установки.
    mp.setattr(solver_advisor, "SKILL_DIR", tmp_path / "solver_advisor")
    mp.setattr(solver_advisor, "SKILL_PATH", tmp_path / "skill.md")
    mp.setattr(solver_advisor, "META_PATH", tmp_path / "meta.json")
    mp.setattr(solver_advisor, "HISTORY_DIR", tmp_path / "kb-history")

    class Env:
        pass

    e = Env()
    e.change_requests = change_requests
    e.data_health = data_health
    e.planner_feedback = planner_feedback
    e.run_profile = run_profile
    e.evolution_runner = evolution_runner
    e.solver_advisor = solver_advisor
    e.connection_buffer = connection_buffer
    e.tmp_path = tmp_path
    e.mp = mp
    try:
        with TestClient(app) as c:
            users.ensure_db()
            users.add(PLAIN, users.ROLE_USER, by="pytest")
            e.client = c
            yield e
    finally:
        mp.undo()


def _as_user(env, email):
    os.environ["CREW_DEV_USER"] = email


LOOPS_ENDPOINTS = (
    ("GET", "/api/loops"),
    ("GET", "/api/loops/data-health/gerad-1"),
    ("GET", "/api/loops/connection-buffer/gerad-1"),
    ("GET", "/api/loops/overrides"),
    ("GET", "/api/loops/runtime"),
    ("POST", "/api/loops/data-health/snapshot"),
    ("POST", "/api/loops/connection-buffer/snapshot"),
)


# --- админ-гейт --------------------------------------------------------------

def test_loops_endpoints_are_admin_only(env):
    """Не-админ не должен видеть ни одного контура: страница показывает
    наблюдения по ВСЕМ экземплярам, включая чужие."""
    _as_user(env, PLAIN)
    for method, url in LOOPS_ENDPOINTS:
        resp = env.client.request(method, url)
        assert resp.status_code == 403, f"{method} {url} -> {resp.status_code}"


def test_loops_endpoints_open_for_admin(env):
    for method, url in LOOPS_ENDPOINTS:
        resp = env.client.request(method, url)
        assert resp.status_code == 200, f"{method} {url} -> {resp.status_code}"


# --- 404 и пустые хранилища --------------------------------------------------

def test_snapshot_unknown_instance_404(env):
    resp = env.client.post("/api/loops/data-health/snapshot?instance=нет-та")
    assert resp.status_code == 404


def test_data_health_unknown_instance_degrades_to_empty(env):
    """Детальный эндпоинт не знает реестра: у неизвестного экземпляра
    просто НЕТ снимков — это пустая история, а не ошибка."""
    resp = env.client.get("/api/loops/data-health/no-such-instance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["history"] == []
    assert body["trend"]["hasBaseline"] is False


def test_loops_empty_stores(env):
    """Пустые хранилища: все девять контуров есть, метрики нулевые,
    последнего наблюдения нет."""
    body = env.client.get("/api/loops").json()
    for item in body["items"]:
        # у «Базы знаний» ожидающих решения сущностей нет в принципе —
        # None, а не 0 (ноль читался бы как «всё разобрано»)
        assert item["open_crs"] == (None if item["key"] == "kb" else 0)
        assert item["metric"]["n"] in (0, len(
            env.change_requests.instance_countries()))
        if item["key"] in ("datahealth", "override", "runtime", "connbuf",
                           "lab", "kb"):
            assert item["last_at"] is None

    ov = env.client.get("/api/loops/overrides").json()
    assert ov["clusters"] == [] and ov["episodes"] == []
    assert ov["total_episodes"] == 0
    rt = env.client.get("/api/loops/runtime").json()
    assert rt["recent"] == [] and rt["profile"]["total_runs"] == 0


# --- форма ответа и порядок --------------------------------------------------

def test_loops_order_and_item_shape(env):
    from webapp import app as app_mod

    body = env.client.get("/api/loops").json()
    keys = [i["key"] for i in body["items"]]
    assert tuple(keys) == app_mod.ALL_LOOP_KEYS, "порядок контуров фиксирован"
    # тяжёлые контуры идут ПОСЛЕ питающих очередь CR и остаются в сводке:
    # обзор обещает все контуры группы, а не только тех, кто пишет в очередь
    assert keys[-2:] == list(app_mod.HEAVY_LOOP_KEYS)
    for item in body["items"]:
        assert set(item) == {"key", "title", "observes", "last_at",
                             "metric", "open_crs"}
        assert item["metric"]["i18n"].startswith("app.loops.m.")
        assert isinstance(item["metric"]["n"], int)
        assert item["open_crs"] is None or isinstance(item["open_crs"], int)


# --- тяжёлые контуры в сводке (2026-07-20) -----------------------------------

def _row(body, key):
    return next(i for i in body["items"] if i["key"] == key)


def test_heavy_loops_report_own_stores(env):
    """«Алгоритмы» и «База знаний» не пишут в очередь CR, но обязаны быть в
    сводке: их числа берутся из СОБСТВЕННЫХ хранилищ."""
    from webapp import evolution

    evolution.add_finding(title="A", summary="s")
    _, deployed = evolution.add_finding(title="B", summary="s")
    evolution.set_status(deployed["id"], evolution.STATUS_DEPLOYED)
    env.evolution_runner.LAST_SCAN_AT_PATH.write_text("2026-07-20T09:00:00Z",
                                                      encoding="utf-8")
    env.solver_advisor.save_skill("текст базы", source="seed",
                                  now="2026-07-19T12:00:00")

    body = env.client.get("/api/loops").json()
    lab, kb = _row(body, "lab"), _row(body, "kb")
    # метрика = ВСЕ находки, «открытых» = только нетерминальные
    assert lab["metric"]["n"] == 2 and lab["open_crs"] == 1
    assert lab["last_at"] == "2026-07-20T09:00:00Z"
    assert kb["metric"]["n"] == 1                     # первая версия базы
    assert kb["last_at"] == "2026-07-19T12:00:00"
    assert kb["open_crs"] is None


def test_lab_last_at_falls_back_to_newest_finding(env):
    """Отметка скана появилась 2026-07-20: на установке с прежними
    находками её нет, и сводка обязана показать свежесть по находкам, а не
    рисовать «—» над непустой базой."""
    from webapp import evolution

    evolution.add_finding(title="A", summary="s")
    assert not env.evolution_runner.LAST_SCAN_AT_PATH.exists()

    lab = _row(env.client.get("/api/loops").json(), "lab")
    assert lab["last_at"] == evolution.list_all()[0]["created_at"]


def test_lab_last_at_survives_a_corrupt_finding(env):
    """Одна испорченная строка находок не должна ронять ВСЮ страницу.

    Откат на «самую свежую находку» — постоянный путь там, где скан
    выключен. Через list_all() он материализовал каждую строку и падал на
    неразбираемом JSON метрик («A corrupt store must be loud»), унося с
    собой все девять контуров, а не только «Алгоритмы»."""
    from webapp import evolution, users

    evolution.add_finding(title="A", summary="s")
    with users._write_txn() as conn:
        conn.execute("UPDATE improvements SET review = ? WHERE id = 1",
                     ("{не json",))

    # прежний путь на этом же состоянии падал — тем и ценна замена
    with pytest.raises(RuntimeError):
        evolution.list_all()

    resp = env.client.get("/api/loops")
    assert resp.status_code == 200, "битая находка обрушила сводку контуров"
    assert _row(resp.json(), "lab")["last_at"] == evolution.newest_created_at()


def test_kb_history_snapshots_stay_out_of_the_working_install(env):
    """Второе сохранение базы знаний снимает копию предыдущей версии — она
    обязана лечь в tmp, а не в рабочий webapp/config."""
    from webapp import solver_advisor as sa

    real_history = Path(sa.__file__).resolve().parent / "config" / \
        "solver_advisor" / "history"
    sa.save_skill("версия один", source="seed")
    sa.save_skill("версия два", source="seed")

    assert not real_history.exists(), "снимок истории лёг в рабочую установку"
    assert (env.tmp_path / "kb-history" / "skill-v1.md").exists()
    assert _row(env.client.get("/api/loops").json(), "kb")["metric"]["n"] == 2


def test_lab_open_count_ignores_terminal_statuses(env):
    """Находка выходит из работы только «внедрена» или «отклонена»;
    провалившаяся фаза — всё ещё в работе и ждёт человека."""
    from webapp import evolution

    for status in (evolution.STATUS_TEST_FAILED, evolution.STATUS_STAGE_PASSED,
                   evolution.STATUS_REJECTED, evolution.STATUS_DEPLOYED):
        _, row = evolution.add_finding(title=f"f-{status}", summary="s")
        evolution.set_status(row["id"], status)

    assert evolution.open_count() == 2
    assert _row(env.client.get("/api/loops").json(), "lab")["open_crs"] == 2


def test_heavy_loops_are_not_cr_agents(env):
    """Ключи тяжёлых контуров — это ключи ВКЛАДОК, а не имена агентов:
    попытка «запустить агента» по ним обязана быть отвергнута, иначе
    кнопка действия в их строках вела бы к 500."""
    from webapp import app as app_mod

    for key in app_mod.HEAVY_LOOP_KEYS:
        assert key not in env.change_requests.AGENTS
        resp = env.client.post(f"/api/cr/agents/{key}/run")
        assert resp.status_code in (404, 422), f"{key} -> {resp.status_code}"


def test_loops_ships_codes_not_russian_labels(env):
    """M-сервер: страница пятиязычная — сервер отдаёт КОДЫ и ключи i18n,
    подпись рисует клиент. Русская подпись в ответе = дефект."""
    from webapp import data_health, planner_feedback

    body = env.client.get("/api/loops").json()
    assert isinstance(body["kinds"], list) and isinstance(body["reasons"], list)
    assert [k["key"] for k in body["kinds"]] == list(data_health.KIND_LABELS)
    # префиксы согласованы с клиентским loopCodeLabel() и i18n.js
    assert all(k["i18n"] == "app.loops.dh.k." + k["key"]
               for k in body["kinds"])
    assert ([r["key"] for r in body["reasons"]]
            == list(planner_feedback.REASON_LABELS))
    assert all(r["i18n"] == "app.loops.reason." + r["key"]
               for r in body["reasons"])
    # ни одной русской подписи ни в одном справочнике
    labels = set(data_health.KIND_LABELS.values()) | set(
        planner_feedback.REASON_LABELS.values())
    raw = json.dumps(body, ensure_ascii=False)
    assert not [lbl for lbl in labels if lbl in raw]

    # те же справочники в двух детальных эндпоинтах
    dh = env.client.get("/api/loops/data-health/gerad-1").json()
    assert [k["key"] for k in dh["kinds"]] == list(data_health.KIND_LABELS)
    ov = env.client.get("/api/loops/overrides").json()
    assert [r["key"] for r in ov["reasons"]] == list(
        planner_feedback.REASON_LABELS)


# --- O) нижняя граница min_size ---------------------------------------------

def _seed_episodes(env, n, inst="inst-a"):
    for _ in range(n):
        env.planner_feedback.record(inst, pid="P1", slot=0, frm="E1", to="E2",
                                    reason_code="missing_rule",
                                    reason_text="повтор", by=ADMIN)


@pytest.mark.parametrize("min_size", [0, -5, 1])
def test_overrides_min_size_has_floor(env, min_size):
    """0/отрицательное/1 отдавали ВСЕ группы — каждый эпизод сам себе
    «кластер». Ниже разумного минимума порог не опускается."""
    from webapp import app as app_mod

    _seed_episodes(env, 1)                       # одиночная правка
    body = env.client.get(f"/api/loops/overrides?min_size={min_size}").json()
    assert body["min_size"] == app_mod.LOOPS_MIN_CLUSTER_SIZE
    assert body["clusters"] == [], "одиночная правка — не повтор"


def test_overrides_min_size_above_floor_is_honoured(env):
    _seed_episodes(env, 3)
    body = env.client.get("/api/loops/overrides?min_size=3").json()
    assert body["min_size"] == 3
    assert body["clusters"], "три одинаковых правки — устойчивый кластер"


# --- I) квадратичное чтение data_health.json --------------------------------

def test_loops_reads_data_health_store_once(env):
    """Хранилище снимков перечитывалось и заново парсилось на КАЖДЫЙ
    экземпляр реестра. Читаем ровно один раз за запрос."""
    from webapp import data_health, registry

    calls = []
    real_load = data_health._load

    def counting_load():
        calls.append(1)
        return real_load()

    env.mp.setattr(data_health, "_load", counting_load)
    assert len(registry.list_instances()) > 1, "нужен многоэкземплярный реестр"
    assert env.client.get("/api/loops").status_code == 200
    assert len(calls) == 1, f"стор прочитан {len(calls)} раз(а) вместо одного"


# --- P) вкладка «Контуры» в журнале активности ------------------------------

def test_loops_tab_is_journalled(env):
    """Переход на вкладку не попадал в журнал: её не было в списке
    известных вкладок, и маячок отбивался 400."""
    resp = env.client.post("/api/activity/nav", json={"tab": "loops"})
    assert resp.status_code == 200
    assert env.client.post("/api/activity/nav",
                           json={"tab": "constructor"}).status_code == 400


# --- G) идемпотентность трёх детерминированных контуров ----------------------

def _open_crs(env, agent):
    return [c for c in env.change_requests.list_crs()
            if c.get("agent") == agent
            and c.get("status") in env.change_requests.OPEN_STATUSES]


def _run(env, agent):
    resp = env.client.post(f"/api/cr/agents/{agent}/run")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_override_loop_is_idempotent(env):
    """Два нажатия «Запустить агента» на неизменившемся журнале переделок
    давали два ПОБАЙТОВО одинаковых черновика."""
    _seed_episodes(env, 6)
    first = _run(env, "override")
    assert first["created"] >= 1
    second = _run(env, "override")
    assert second["created"] == 0
    assert second["duplicates"] >= 1
    assert len(_open_crs(env, "override")) == first["created"]


def test_runtime_loop_is_idempotent(env):
    for _ in range(12):
        env.run_profile.record_run(jtype="solve", label="L", budget_s=10.0,
                                   wall_s=99.0, outcome="failed",
                                   exit_code=1)
    first = _run(env, "runtime")
    assert first["created"] >= 1
    second = _run(env, "runtime")
    assert second["created"] == 0
    assert len(_open_crs(env, "runtime")) == first["created"]


def _payload(stray_flights):
    """Экземпляр с управляемым числом дефектов вида «рейсы вне основного
    месяца»: дефекты считаются отчётом аномалий по самим данным."""
    flights = [{"flightNumber": f"F{i}", "departureAirport": "AAA",
                "departureUTCDateTime": f"2026-07-0{1 + i % 9}T06:00:00",
                "arrivalAirport": "BBB",
                "arrivalUTCDateTime": f"2026-07-0{1 + i % 9}T08:00:00"}
               for i in range(20)]
    for j in range(stray_flights):
        flights.append({"flightNumber": f"S{j}", "departureAirport": "AAA",
                        "departureUTCDateTime": "2026-09-02T06:00:00",
                        "arrivalAirport": "BBB",
                        "arrivalUTCDateTime": "2026-09-02T08:00:00"})
    return {"formatVersion": "1",
            "airports": [{"code": "AAA"}, {"code": "BBB"}],
            "employees": [{"id": "E1", "homeAirport": "AAA",
                           "skills": ["Pilot"]}],
            "flights": flights,
            "flightAssignments": [{"id": "F0-0", "flight": "F0",
                                   "indexInFlight": 0,
                                   "requiredSkill": "Pilot"}]}


def test_datahealth_loop_is_idempotent(env):
    """Контур здоровья данных повторяет ЗАГОЛОВОК при повторе того же
    ухудшения («было 1, стало 3»): данные могут улучшиться и снова
    испортиться до тех же чисел, пока прежний черновик ещё открыт, — и
    очередь получала второй побайтово одинаковый черновик."""
    state = {"stray": 1}

    def payloads():
        return [("inst-dh", _payload(state["stray"]))]

    env.mp.setattr(env.change_requests, "_visible_instance_payloads", payloads)

    _run(env, "datahealth")                      # база, предложений нет
    state["stray"] = 3
    first = _run(env, "datahealth")              # ухудшение -> черновик
    assert first["created"] == 1
    state["stray"] = 1
    _run(env, "datahealth")                      # улучшение, предложений нет
    state["stray"] = 3
    second = _run(env, "datahealth")             # то же ухудшение снова
    assert second["created"] == 0
    assert second["duplicates"] == 1
    assert len(_open_crs(env, "datahealth")) == 1


def test_dedup_does_not_silence_a_closed_proposal(env):
    """Отклонённое предложение не должно затыкать контуру рот навсегда:
    дубль ищется только среди ОТКРЫТЫХ (draft/accepted)."""
    _seed_episodes(env, 6)
    _run(env, "override")
    cr = _open_crs(env, "override")[0]
    env.change_requests.reject_cr(cr["id"], ADMIN, "не сейчас")
    again = _run(env, "override")
    assert again["created"] >= 1


def test_dedup_untouched_for_other_agents(env):
    """Инвариант: дедупликация включена ТОЛЬКО для детерминированных
    контуров — общий путь регистрации ведёт себя как прежде."""
    rows = [{"id": "cr1", "agent": "uiux", "title": "T", "summary": "S",
             "priority": 1, "effort": 1, "status": "draft", "source": None,
             "country": None, "created_at": "2026-07-20T00:00:00+00:00",
             "updated_at": "2026-07-20T00:00:00+00:00"}]
    env.change_requests._add_crs(rows)
    env.change_requests._add_crs([dict(rows[0], id="cr2")])
    assert len([c for c in env.change_requests.list_crs()
                if c["agent"] == "uiux"]) == 2
