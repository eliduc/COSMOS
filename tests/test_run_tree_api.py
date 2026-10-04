# -*- coding: utf-8 -*-
"""Дерево прогонов на стороне COSMOS (webapp/run_tree_api.py и её ручки).

Основание — проект `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, раздел
7 «Дерево прогонов», решение владельца 6.3 и раздел 12 (шаг 3). Судится
ровно то, ради чего ручки заведены:

  * индекс собирается по ДВУМ корням (записи частных прогонов COSMOS в
    `package-runs` и записи прогонов панели CDPM по `CREW_CDPM_RUNS_DIR`)
    и ложится файлом `run-tree.json` в корень данных; чтение идёт из
    файла, а перестройка — по ручке и по событию;
  * карточка прогона показывает путь от корня, соседние ветви и
    сравнение с золотым прогоном по умолчанию;
  * сравнение двух прогонов зовётся номерами записей и отказывает
    словами, когда прогона на дереве нет;
  * золотой прогон: площадка не `prod`, прогон не завершён, пустые
    слова, чужая подпись и машинный канал — отказы словами с кодами;
    у частного прогона COSMOS признак ложится в СВОЮ запись и индекс
    перестраивается, у полного — подтверждение ПЕРЕСЫЛАЕТСЯ хозяину
    записи (судится тело и путь запроса, сети нет);
  * при закрытии записи частного прогона в итоги ложится сравнение с
    предыдущим прогоном и с золотым (раздел 12.4).

Литералов среды в файле нет (сторож tests/test_no_environment_literals):
каталоги — только `tmp_path`, адрес хозяина записи — вымышленная зона
`.invalid`, личность — `_stand_identity.ADMIN_EMAIL`, служб не
поднимается, сеть подставлена.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest
from _stand_identity import ADMIN_EMAIL

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from tests.test_run_gate import _receive, _upload, _wd, client  # noqa: E402,F401
from tests.test_run_tree import record, put, written            # noqa: E402
from vmp import run_record as rr                                # noqa: E402
from vmp import run_tree as rt                                  # noqa: E402

ADMIN = ADMIN_EMAIL
TOKEN = "test-run-tree-token-2026"
TREE = "/api/run-tree"
REBUILD = "/api/run-tree/rebuild"
COMPARE = "/api/run-tree/compare"
CURRENT_TREE = "/api/run/current/tree"
DECLARE = "/api/run/declare"
#: Адрес записи у хозяина: вымышленная зона, сеть не трогается.
PANEL_RECORD = "http://панель.invalid/api/runs/run-0007/record"


@pytest.fixture(autouse=True)
def _tree_env(tmp_path, monkeypatch):
    """Каталог данных, каталог прогонов панели и защита от частой
    перестройки — свои у каждого теста: значение с машины сделало бы
    прогон недетерминированным."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.delenv("CREW_TASK_INBOX", raising=False)
    monkeypatch.delenv("CREW_TASK_PACKAGE", raising=False)
    monkeypatch.delenv("CREW_VMP_REGISTRY", raising=False)
    monkeypatch.delenv("CREW_SOLVER_URL", raising=False)
    monkeypatch.delenv("CREW_CHANNEL_TOKEN", raising=False)
    monkeypatch.delenv("CREW_CONTOUR", raising=False)
    monkeypatch.delenv("CREW_CDPM_RECORD_API", raising=False)
    monkeypatch.delenv("CREW_CDPM_RUNS_DIR", raising=False)
    # Перестройка по событию — сразу: срок защиты в стенде не ждут.
    monkeypatch.setenv("CREW_RUN_TREE_REBUILD_S", "0")
    from webapp import flow, run_tree_api, task_channel
    run_tree_api.forget()
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)


# ----------------------------------------------------------- помощники
def _panel_runs(tmp_path, monkeypatch) -> Path:
    """Каталог прогонов панели CDPM на этой машине — второй корень
    обхода (`CREW_CDPM_RUNS_DIR`)."""
    folder = tmp_path / "прогоны-панели"
    folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CREW_CDPM_RUNS_DIR", str(folder))
    return folder


def _package_runs() -> Path:
    from webapp import cosmos_runs
    folder = cosmos_runs.package_runs_root()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class _Resp:
    """Ответ хозяина записи для подставленного `urlopen`."""

    def __init__(self, body, status: int = 200):
        self._raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.status = status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _fake_urlopen(monkeypatch, answer, seen: list | None = None):
    """Подстановка сети у ручек дерева: `answer` — ответ либо исключение."""
    from webapp import run_tree_api

    def _open(req, timeout=None):
        if seen is not None:
            seen.append({"url": req.full_url, "method": req.get_method(),
                         "body": json.loads(req.data.decode("utf-8"))
                         if req.data else None})
        if isinstance(answer, Exception):
            raise answer
        return answer() if callable(answer) else answer
    monkeypatch.setattr(run_tree_api._urlrequest, "urlopen", _open)


def _http_error(status: int, body: dict) -> urllib.error.HTTPError:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return urllib.error.HTTPError(PANEL_RECORD, status, "отказ", {},
                                  io.BytesIO(raw))


def _prod_record(run_id: str, **over) -> dict:
    """Запись завершённого прогона площадки `prod` — такой прогон и
    бывает золотым (решение владельца 6.3)."""
    doc = record(run_id, contour={"name": "prod", "services": {}}, **over)
    return doc


# ------------------------------------------------------------- индекс
def test_индекс_собирается_по_двум_корням(tmp_path, monkeypatch):
    """Корни обхода COSMOS — записи частных прогонов в `package-runs` и
    записи прогонов панели CDPM на той же машине (раздел 12.1). У
    полного прогона своей записи в папке нажатий нет: дерево видит его
    только через запись панели."""
    from webapp import run_tree_api
    panel = _panel_runs(tmp_path, monkeypatch)
    written(panel, "run-0001", at="2026-09-10T09:00:00+03:00")
    written(panel, "run-0002", at="2026-09-10T10:00:00+03:00",
            model="модель-другая")
    written(panel, "run-0003", at="2026-09-10T11:00:00+03:00",
            engine="cplex", spent_limit_s=180)
    # Частный прогон COSMOS: он объявлен от готового ВМП и корня входов
    # не считал — на дерево он встаёт продолжением прогона-источника.
    private = record("частный-4", at="2026-09-10T12:00:00+03:00",
                     lineage={"parent_run": None, "branch_of": "решатели",
                              "package_sha256": rr.sha256_text("пакет"),
                              "source_run": "run-0001",
                              "root_sha256": None,
                              "root_answers_sha256": None})
    put(_package_runs() / ("a" * 64), private)

    kinds = {r["kind"]: r for r in run_tree_api.roots()}
    assert set(kinds) == {"package-runs", "cdpm-runs"}
    assert kinds["cdpm-runs"]["path"] == str(panel)
    assert all(r["present"] for r in kinds.values())

    tree = run_tree_api.rebuild("стенд")
    assert rt.validate_index(tree) == []
    assert len(tree["roots"]) == 1, "ветви разошлись по разным корням"
    runs = [n["run_id"] for n in tree["roots"][0]["runs"]]
    assert runs == ["run-0001", "run-0002", "run-0003", "частный-4"]
    node = rt.find_node(tree, "частный-4")
    assert node["edge"]["kind"] == rt.EDGE_CONTINUATION
    assert node["edge"]["to"] == "run-0001"
    assert rt.find_node(tree, "run-0003")["edge"]["differs"] == [
        "решатели", "параметры решателей"]
    assert run_tree_api.counts(tree) == {"roots": 1, "runs": 4, "orphans": 0,
                                         "refusals": 0}


def test_фрагмент_и_копия_чужой_записи_в_дерево_не_идут(tmp_path):
    """Индексируются только настоящие записи: фрагмент COSMOS и
    полученная копия чужой записи записями не являются — иначе один
    прогон встал бы на дерево дважды."""
    from webapp import run_journal, run_gate, run_tree_api
    wd = _package_runs() / ("b" * 64)
    wd.mkdir(parents=True)
    doc = record("одинокий")
    (wd / rr.RECORD_FILE).write_text(json.dumps(doc, ensure_ascii=False),
                                     encoding="utf-8")
    (wd / run_gate.FRAGMENT_FILE).write_text(
        json.dumps({"schema": run_gate.FRAGMENT_SCHEMA}, ensure_ascii=False),
        encoding="utf-8")
    (wd / run_journal.REMOTE_FILE).write_text(
        json.dumps({"at": None, "record": doc}, ensure_ascii=False),
        encoding="utf-8")
    tree = run_tree_api.rebuild("стенд")
    assert [n["run_id"] for n in rt.nodes(tree)] == ["одинокий"]
    assert tree["refusals"] == []


def test_индекс_ложится_файлом_и_читается_из_файла(tmp_path, monkeypatch):
    """Чтение идёт из файла: обход диска на каждый запрос карточки не
    делается (раздел 12.1). Перестройка — по ручке и по событию."""
    from webapp import run_tree_api
    panel = _panel_runs(tmp_path, monkeypatch)
    written(panel, "run-0001")
    tree = run_tree_api.tree()
    assert run_tree_api.index_path().is_file()
    assert run_tree_api.index_path().name == rt.INDEX_FILE
    assert rt.load_index(run_tree_api.index_path()) == tree

    # Новая запись появилась, а файл индекса прежний — чтение отдаёт
    # прежний снимок…
    written(panel, "run-0002", at="2026-09-10T10:00:00+03:00")
    assert [n["run_id"] for n in rt.nodes(run_tree_api.tree())] == ["run-0001"]
    # …пока не попросили перестроить.
    again = run_tree_api.tree(rebuild_now=True)
    assert [n["run_id"] for n in rt.nodes(again)] == ["run-0001", "run-0002"]


def test_срок_защиты_от_частой_перестройки(tmp_path, monkeypatch):
    """Событие, пришедшее раньше срока, запоминается и перестроит индекс
    при первом чтении после срока: три записи подряд не имеют права
    трижды обойти диск."""
    from webapp import run_tree_api
    panel = _panel_runs(tmp_path, monkeypatch)
    written(panel, "run-0001")
    monkeypatch.setenv("CREW_RUN_TREE_REBUILD_S", "600")
    assert run_tree_api.rebuild_every_s() == 600
    run_tree_api.rebuild("первый раз")
    written(panel, "run-0002", at="2026-09-10T10:00:00+03:00")
    got = run_tree_api.note_change("этап записан")
    assert got["rebuilt"] is False and got["pending"] == "этап записан"
    assert [n["run_id"] for n in rt.nodes(run_tree_api.tree())] == ["run-0001"]
    # Срок вышел — отложенное событие перестраивает индекс само.
    monkeypatch.setenv("CREW_RUN_TREE_REBUILD_S", "0")
    assert [n["run_id"] for n in rt.nodes(run_tree_api.tree())] == ["run-0001",
                                                                   "run-0002"]


def test_индекс_перестраивается_по_событиям(client, tmp_path, monkeypatch):
    """Событий два (раздел 12.1): объявлен прогон от пакета (крючок в
    гейте) и записан этап журнала (крючок в журнале). Обход диска ради
    них делает сама система — человек ручку перестройки не ищет."""
    from webapp import cosmos_runs, run_journal, run_tree_api
    _upload(client, tmp_path, declared=False)
    assert not run_tree_api.index_path().exists()
    r = client.post(DECLARE, json={"purpose": "регресс"})
    assert r.status_code == 200, r.text
    run_id = r.json()["record"]["declaration"]["run_id"]
    tree = rt.load_index(run_tree_api.index_path())
    assert rt.find_node(tree, run_id) is not None, "объявление не в индексе"
    assert rt.find_node(tree, run_id)["state"] == rr.STATE_DECLARED

    doc = cosmos_runs.read_run(cosmos_runs.current_run_id())
    wd = cosmos_runs.workdir_of_package(cosmos_runs._current_pkg(doc))
    run_journal.note_attempt(wd, {
        "id": "A1", "task": "pairing", "engine": None,
        "started_at": "2026-09-10T10:00:00", "state": "завершено",
        "finished_at": "2026-09-10T10:05:00", "done": True,
        "verdict": "допустимо", "error": None, "catalog": None,
        "package_sha": "a" * 64, "actor": "стенд", "params": {},
        "summary": {"verdict": "допустимо"},
        "resources": {"models": [], "solver_seconds": 5}})
    again = rt.load_index(run_tree_api.index_path())
    assert rt.find_node(again, run_id)["state"] == rr.STATE_RUNNING


def test_ручка_индекса_и_перестройки(client, tmp_path, monkeypatch):
    """`GET /api/run-tree` отдаёт САМ документ индекса (он обязан
    сходиться со своей схемой), `POST /api/run-tree/rebuild` — обход с
    корнями и перечнем прогонов, ждущих слова владельца."""
    panel = _panel_runs(tmp_path, monkeypatch)
    written(panel, "run-0001")
    r = client.get(TREE)
    assert r.status_code == 200, r.text
    assert r.json()["schema"] == rt.SCHEMA
    assert rt.validate_index(r.json()) == []

    put(panel / "run-0002", _prod_record("run-0002",
                                         at="2026-09-10T10:00:00+03:00"))
    assert len(rt.nodes(client.get(TREE).json())) == 1          # из файла
    assert len(rt.nodes(client.get(TREE + "?rebuild=1").json())) == 2

    r2 = client.post(REBUILD)
    assert r2.status_code == 200, r2.text
    got = r2.json()
    assert got["ok"] is True
    assert got["counts"] == {"roots": 1, "runs": 2, "orphans": 0,
                             "refusals": 0}
    assert {row["kind"] for row in got["roots"]} == {"package-runs",
                                                     "cdpm-runs"}
    assert got["index_file"].endswith(rt.INDEX_FILE)
    # Прогон площадки prod, завершённый и без признака, ждёт слова
    # владельца (раздел 12.2 — запрос владельцу после приёмки).
    assert [x["run_id"] for x in got["golden_pending"]] == ["run-0002"]


# ------------------------------------------------------------ карточка
def test_ручка_дерева_по_прогону_путь_соседи_и_золотой(client, tmp_path,
                                                       monkeypatch):
    """`GET /api/cosmos-runs/<run>/tree`: узел, путь от корня, соседние
    ветви и сравнение с золотым прогоном ПО УМОЛЧАНИЮ (раздел 7)."""
    from webapp import cosmos_runs
    panel = _panel_runs(tmp_path, monkeypatch)
    _upload(client, tmp_path, declared=False)
    r = client.post(DECLARE, json={"purpose": "регресс"})
    assert r.status_code == 200, r.text
    run_id = r.json()["record"]["declaration"]["run_id"]
    run = cosmos_runs.current_run_id()

    # Соседи по корню: два прогона панели на ТЕХ ЖЕ входах, один из них
    # золотой. Корень — отпечатки документов и данных, поэтому соседям
    # отдаются документы частного прогона: подменить корень в записи
    # нельзя (словарь сверяет его с отпечатками входов).
    declared = r.json()["record"]["declaration"]
    root = declared["lineage"]["root_sha256"]
    assert root, "объявление частного прогона не назвало корень входов"
    for n, when in ((1, "2026-09-09T09:00:00+03:00"),
                    (2, "2026-09-09T10:00:00+03:00")):
        put(panel / ("run-000%d" % n),
            record("сосед-%d" % n, at=when, answers=(),
                   rules_documents=declared["rules_documents"],
                   data_files=declared["data_files"]))
    gold = rr.load(panel / "run-0001")
    rt.mark_golden(gold, by=ADMIN, words="прогон принят на Prod",
                   at="2026-09-09T12:00:00+03:00")
    rr.save(panel / "run-0001", gold)
    # Прогоны панели легли МИМО COSMOS (их хозяин — панель), и её
    # событий COSMOS не видит: индекс перестраивается ручкой.
    assert client.post(REBUILD).status_code == 200

    got = client.get("/api/cosmos-runs/%s/tree" % run)
    assert got.status_code == 200, got.text
    doc = got.json()
    assert doc["run"] == run and doc["run_id"] == run_id
    assert doc["node"]["run_id"] == run_id
    assert doc["path"]["root"]["root_sha256"] == root
    assert doc["path"]["runs"][-1]["run_id"] == run_id
    assert {n["run_id"] for n in doc["siblings"]} == {"сосед-1", "сосед-2"}
    assert doc["golden"]["run_id"] == "сосед-1"
    assert doc["compare_golden"]["kind"] == rt.COMPARE_GOLDEN
    assert (doc["compare_golden"]["a"], doc["compare_golden"]["b"]) \
        == ("сосед-1", run_id)
    assert doc["compare_previous"]["a"] == "сосед-2"
    assert doc["expectations"] is None
    # Кнопка «Золотой» у прогона площадки не prod не показывается.
    assert doc["golden_action"] == {
        "allowed": False, "code": "golden-not-prod",
        "words": doc["golden_action"]["words"], "confirmed": False,
        "owner": "cosmos", "by": None, "at": None, "words_of_owner": None}
    assert "Prod" in " ".join(doc["golden_action"]["words"])

    # Та же карточка по ТЕКУЩЕМУ пакету.
    same = client.get(CURRENT_TREE)
    assert same.status_code == 200, same.text
    assert same.json()["node"]["run_id"] == run_id


def test_дерево_по_прогону_без_объявления_отказывает_словами(client, tmp_path):
    from webapp import cosmos_runs, run_gate
    _upload(client, tmp_path, declared=False)
    run = cosmos_runs.current_run_id()
    got = client.get("/api/cosmos-runs/%s/tree" % run)
    assert got.status_code == 409, got.text
    assert got.json()["code"] == run_gate.CODE_NOT_DECLARED
    assert client.get("/api/cosmos-runs/run-9999/tree").status_code == 404
    assert client.get(CURRENT_TREE).status_code == 409


def test_ручка_сравнения_двух_прогонов(client, tmp_path, monkeypatch):
    """`GET /api/run-tree/compare?a=&b=`: что менялось в объявлении и
    что изменилось в итогах."""
    from webapp import run_tree_api
    panel = _panel_runs(tmp_path, monkeypatch)
    written(panel, "run-0001")
    written(panel, "run-0002", at="2026-09-10T10:00:00+03:00",
            model="модель-другая", verdict="недопустимо", machine_s=200)
    got = client.get(COMPARE + "?a=run-0001&b=run-0002")
    assert got.status_code == 200, got.text
    cmp = got.json()
    assert (cmp["a"], cmp["b"]) == ("run-0001", "run-0002")
    assert cmp["same_root"] is True
    changed = {row["field"] for row in cmp["declaration"]["changed"]}
    assert "models.extractor.model" in changed
    assert cmp["results"]["verdicts"]["rostering"]["same"] is False
    assert cmp["results"]["timing"]["machine_s"]["delta"] == 80
    assert any("СРАВНЕНИЕ ПРОГОНОВ" in w for w in cmp["words"])

    nothing = client.get(COMPARE + "?a=run-0001&b=такого-нет")
    assert nothing.status_code == 404
    assert nothing.json()["code"] == run_tree_api.CODE_NOT_IN_TREE
    assert client.get(COMPARE).status_code == 400


# ------------------------------------------------------------- золотой
def _declare_prod(client, tmp_path, monkeypatch) -> tuple:
    """Частный прогон COSMOS, объявленный на площадке `prod`."""
    from webapp import cosmos_runs
    monkeypatch.setenv("CREW_CONTOUR", "prod")
    _upload(client, tmp_path, declared=False)
    r = client.post(DECLARE, json={"purpose": "регресс"})
    assert r.status_code == 200, r.text
    return cosmos_runs.current_run_id(), r.json()["record"]


def _finish(run: str) -> Path:
    """Закрыть запись частного прогона: золотым бывает только
    завершённый прогон."""
    from webapp import cosmos_runs as cr
    doc = cr.read_run(run)
    wd = cr.workdir_of_package(cr._current_pkg(doc))
    record_doc = rr.load(wd)
    rr.append_stage(record_doc, {"stage": rr.COSMOS_STAGES["rostering"],
                                 "outcome": rr.OUTCOME_OK, "actor": "стенд",
                                 "duration_machine_s": 12})
    rr.finish(record_doc, {"verdicts": {"rostering": "допустимо"}})
    rr.save(wd, record_doc)
    return wd


def test_золотой_у_частного_прогона_и_отказы(client, tmp_path, monkeypatch):
    """Признак золотого ставится у ХОЗЯИНА записи (частный прогон —
    COSMOS) и только после приёмки на Prod у завершённого прогона."""
    from webapp import run_tree_api
    run, _record = _declare_prod(client, tmp_path, monkeypatch)
    golden = "/api/cosmos-runs/%s/golden" % run

    # Пустое «кто» — отказ словами (ручка личность подставляет сама,
    # но правило судит сам модуль: подпись обязательна).
    with pytest.raises(run_tree_api.RunTreeApiError) as e:
        run_tree_api.golden(run, by="  ", words="принят на Prod")
    assert e.value.code == run_tree_api.CODE_GOLDEN_BY
    assert e.value.status == 422
    assert "КТО подтвердил" in e.value.text()
    # Пустые слова — отказ до всякой записи.
    r = client.post(golden, json={})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == run_tree_api.CODE_GOLDEN_WORDS
    # Чужая подпись в теле — отказ: подтверждает тот, кто вошёл.
    r = client.post(golden, json={"by": "кто-то@стенд.invalid",
                                  "words": "принят"})
    assert r.status_code == 422
    assert r.json()["code"] == run_tree_api.CODE_GOLDEN_BY_MISMATCH
    # Прогон не завершён — образцом не бывает.
    r = client.post(golden, json={"words": "принят на Prod"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == run_tree_api.CODE_GOLDEN_STATE

    wd = _finish(run)
    r = client.post(golden, json={"by": ADMIN, "words": "принят на Prod"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["ok"] is True and got["owner"] == run_tree_api.OWNER_LOCAL
    assert got["golden"]["confirmed"] is True
    assert got["golden"]["by"] == ADMIN
    assert got["golden"]["words"] == "принят на Prod"
    on_disk = rr.load(wd)
    assert on_disk["results"]["golden"]["confirmed"] is True
    assert rr.validate(on_disk) == []
    # Индекс перестроен сразу: с золотым сравниваются новые ветви.
    tree = rt.load_index(run_tree_api.index_path())
    node = rt.find_node(tree, on_disk["declaration"]["run_id"])
    assert node["golden"]["confirmed"] is True
    assert client.get("/api/cosmos-runs/%s/tree" % run) \
        .json()["golden_action"]["allowed"] is True

    # Снятие тем же лицом.
    r = client.post(golden + "/unmark", json={"by": ADMIN})
    assert r.status_code == 200, r.text
    assert rr.load(wd)["results"]["golden"]["confirmed"] is False


def test_золотой_не_ставится_на_чужой_площадке(client, tmp_path, monkeypatch):
    """Решение владельца 6.3: признак — только после приёмки на Prod.
    Площадка записи не `prod` — отказ словами, а не молчаливая пометка."""
    from webapp import run_tree_api
    monkeypatch.setenv("CREW_CONTOUR", "test")
    _upload(client, tmp_path, declared=False)
    assert client.post(DECLARE, json={"purpose": "регресс"}).status_code == 200
    from webapp import cosmos_runs
    run = cosmos_runs.current_run_id()
    _finish(run)
    r = client.post("/api/cosmos-runs/%s/golden" % run,
                    json={"words": "принят"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == run_tree_api.CODE_GOLDEN_CONTOUR
    assert "Prod" in r.json()["detail"]


def test_машинный_канал_ручку_золотого_не_получает(client, tmp_path,
                                                   monkeypatch):
    """Подтверждает ЧЕЛОВЕК: маршрут золотого в машинный перечень не
    входит, а предъявленный токен канала — отказ 403 (fail-closed)."""
    from webapp import app as app_module
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    run, _record = _declare_prod(client, tmp_path, monkeypatch)
    _finish(run)
    golden = "/api/cosmos-runs/%s/golden" % run
    assert not app_module._machine_route("POST", golden)
    assert not app_module._machine_route("POST", golden + "/unmark")
    r = client.post(golden, json={"words": "принят на Prod"},
                    headers={"X-Channel-Token": TOKEN})
    assert r.status_code == 403, r.text
    assert "человек" in r.json()["detail"]
    # Признак не поставлен.
    assert client.get("/api/cosmos-runs/%s/tree" % run) \
        .json()["node"]["golden"]["confirmed"] is False
    # А ЧТЕНИЕ дерева машинному читателю открыто.
    for method, path in (("GET", TREE), ("POST", REBUILD),
                         ("GET", COMPARE), ("GET", CURRENT_TREE),
                         ("GET", "/api/cosmos-runs/run-0001/tree")):
        assert app_module._machine_route(method, path), (method, path)
    ok = {"X-Channel-Token": TOKEN}
    assert client.get(TREE, headers=ok).status_code == 200
    assert client.get(TREE, headers={"X-Channel-Token": "wrong-token"}) \
        .status_code == 403


def test_золотой_полного_прогона_пересылается_хозяину(client, tmp_path,
                                                      monkeypatch):
    """У полного прогона хозяин записи — панель: подтверждение
    пересылается ей ручкой `POST …/golden`, а ответ хозяина отдаётся как
    есть (раздел 12.2)."""
    from webapp import cosmos_runs, run_tree_api
    monkeypatch.setenv("CREW_CONTOUR", "prod")
    monkeypatch.setenv("CREW_CDPM_RECORD_API", "http://панель.invalid")
    _upload(client, tmp_path)                       # пакет С номером записи
    run = cosmos_runs.current_run_id()
    seen: list = []
    _fake_urlopen(monkeypatch, _Resp({"ok": True, "record": {"schema": "…"}}),
                  seen)
    r = client.post("/api/cosmos-runs/%s/golden" % run,
                    json={"words": "принят на Prod по трём наборам"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "record": {"schema": "…"}}
    assert len(seen) == 1
    head = client.get("/api/cosmos-runs/%s/record" % run).json()["head"]
    assert seen[0]["method"] == "POST"
    assert seen[0]["url"] == "http://панель.invalid/api/runs/%s/golden" \
        % head["record_id"]
    assert seen[0]["body"] == {"by": ADMIN,
                               "words": "принят на Prod по трём наборам"}
    assert run_tree_api.golden_url(PANEL_RECORD, mark=False) \
        == "http://панель.invalid/api/runs/run-0007/golden/unmark"

    # Отказ хозяина — тоже его слова и его код ответа.
    _fake_urlopen(monkeypatch, _http_error(409, {"detail": "прогон не принят",
                                                 "code": "golden-not-prod"}))
    r2 = client.post("/api/cosmos-runs/%s/golden" % run,
                     json={"words": "принят"})
    assert r2.status_code == 409
    assert r2.json()["detail"] == "прогон не принят"

    # Хозяин недоступен — 502 своими словами, а не 500 без слов.
    _fake_urlopen(monkeypatch, OSError("сети нет"))
    r3 = client.post("/api/cosmos-runs/%s/golden" % run,
                     json={"words": "принят"})
    assert r3.status_code == 502
    assert r3.json()["code"] == run_tree_api.CODE_OWNER_UNREACHABLE


# ------------------------------------------------- сравнение в записи
def test_сравнение_с_предыдущим_и_золотым_при_закрытии(tmp_path, monkeypatch):
    """Раздел 12.4: при закрытии записи хозяин пишет в итоги сравнение с
    предыдущим прогоном и с золотым — `{run_id, words[]}` по индексу."""
    from webapp import cosmos_runs, run_journal, run_tree_api
    panel = _panel_runs(tmp_path, monkeypatch)
    root = rr.root_sha256([{"sha256": rr.sha256_text("документ правил дерева")}],
                          [{"sha256": rr.sha256_text("книга данных дерева")}])
    written(panel, "run-0001", at="2026-09-09T09:00:00+03:00")
    written(panel, "run-0002", at="2026-09-09T10:00:00+03:00",
            model="модель-другая")
    gold = rr.load(panel / "run-0001")
    rt.mark_golden(gold, by=ADMIN, words="принят на Prod",
                   at="2026-09-09T12:00:00+03:00")
    rr.save(panel / "run-0001", gold)

    # Частный прогон COSMOS того же корня: его запись закрывает журнал.
    wd = cosmos_runs.package_runs_root() / ("c" * 64)
    doc = record("частный-3", at="2026-09-09T13:00:00+03:00",
                 verdict="недопустимо")
    doc["journal"] = []
    doc["results"] = rr.empty_results()
    doc["results"]["comparison"]["expectations"] = {"kind": "манифест набора"}
    assert doc["declaration"]["lineage"]["root_sha256"] == root
    put(wd, doc)
    run_tree_api.rebuild("стенд")

    head = {"record_id": "частный-3", "declaration_sha256":
            doc["declaration_sha256"], "record_api": None, "owner": "cosmos",
            "source_run": None, "customer": None}
    for task, attempt in (("pairing", "A1"), ("rostering", "A2")):
        run_journal.note_attempt(wd, {
            "id": attempt, "task": task, "engine": None,
            "started_at": "2026-09-09T13:00:00", "state": "завершено",
            "finished_at": "2026-09-09T13:05:00", "done": True,
            "verdict": "допустимо", "error": None, "catalog": None,
            "package_sha": "c" * 64, "actor": "стенд", "params": {},
            "summary": {"verdict": "допустимо"},
            "resources": {"models": [], "solver_seconds": 5}}, head)

    closed = rr.load(wd)
    assert closed["results"]["state"] == rr.STATE_DONE
    comparison = closed["results"]["comparison"]
    assert comparison["expectations"] == {"kind": "манифест набора"}
    assert comparison["previous_run"]["run_id"] == "run-0002"
    assert comparison["golden"]["run_id"] == "run-0001"
    assert any("СРАВНЕНИЕ ПРОГОНОВ" in w
               for w in comparison["golden"]["words"])
    assert rr.validate(closed) == []


def test_сравнивать_не_с_чем_пусто(tmp_path, monkeypatch):
    """Прогон первый на корне и золотого нет — сравнения пусты: честная
    пустота понятнее выдуманного сравнения с самим собой."""
    from webapp import run_tree_api
    wd = _package_runs() / ("d" * 64)
    doc = record("один-единственный")
    put(wd, doc)
    run_tree_api.rebuild("стенд")
    assert run_tree_api.comparison_for(doc) == {"previous_run": {},
                                                "golden": {}}
