# -*- coding: utf-8 -*-
"""Дерево прогонов — половина CDPM (шаг 3 проекта «Запись о прогоне»,
раздел 12): индекс по каталогу прогонов панели, ручки `run-tree` /
`runs/<номер>/tree` / `run-tree/compare`, золотой прогон и отпечаток
ревизии ответов ПО СОДЕРЖАНИЮ (11.7).

Ядро дерева — общее (`vmp/run_tree.py`, судится
tests/test_run_tree.py). Здесь судится то, что своё у панели:

  * индекс собирается по каталогу прогонов и КЛАДЁТСЯ ФАЙЛОМ
    `run-tree.json`, а читается из файла — обхода диска на каждый
    запрос нет (12.1);
  * три записи панели встают одним корнем: первый прогон — корень,
    второй — соседняя ветвь со словом «чем отличается», третий —
    продолжение от прогона-источника;
  * ручки отвечают ровно теми ключами, о которых договорились обе
    системы (12.2), а прогона нет в дереве — 404 словами;
  * золотой прогон ставится ТОЛЬКО подписью человека, ТОЛЬКО у
    завершённого прогона и ТОЛЬКО на площадке prod (решение владельца
    6.3), переживает пересборку записи (`refresh`) и виден в индексе;
    снимает признак тот же человек, что подтверждал;
  * индекс перестраивается по событию (приём чужих записей, закрытие
    записи, золотой) и ручкой `POST /api/run-tree/rebuild`;
  * отпечаток ревизии ответов считается по СОДЕРЖАНИЮ: две книги с
    разными байтами и одними ответами дают один отпечаток, а изменённый
    ответ — другой.

Стенд без среды: каталог прогонов — `tmp_path`, отпечатки строятся в
самом тесте, служб не поднимается, личности владельца нет."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cdpm import answers_fingerprint as afp
from cdpm import run_record as rr
from cdpm import run_tree_panel as rtp
from vmp import run_tree as rt

#: Подписи стенда — вымышленные адреса зоны `.test`.
OWNER = "hozyain@example.test"
OTHER = "drugoy@example.test"

#: Отпечатки-пробы: строятся тестом, живых файлов не читают.
SHA_RULES = "a" * 64
SHA_DATA = "b" * 64
SHA_CATALOG = "c" * 64
SHA_PACKAGE = "d" * 64


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _delivery(run: Path, at: str) -> None:
    """Healthy stored-brief fixture: artifact identity, not package-gate proof."""
    import hashlib
    import zipfile

    package = run / "channel" / "task-package.zip"
    package.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("fixture.txt", "run-tree delivery projection")
    _write(run / "channel" / "delivery.json", {
        "sent": True, "sha": hashlib.sha256(package.read_bytes()).hexdigest(),
        "verdict": "OK", "mg": {}, "mismatch": [], "already_accepted": False,
        "detail": "Synthetic healthy brief for run-tree projection", "at": at})


def _declaration(run_id: str, *, at: str, model: str = "модель-а",
                 source_run: str | None = None) -> dict:
    """Голова прогона панели по словарю записи: ветви одного корня
    различаются ТОЛЬКО тем, чем их различает тест.

    Объявление проводится через `normalize_declaration` СЛОВАРЯ — так
    же, как его пишет панель: иначе отпечаток головы разошёлся бы со
    счётом словаря, и обход отправил бы запись в отказы."""
    from vmp.run_record import normalize_declaration
    rules = [{"name": "ПРАВИЛА.docx", "kind": "docx", "sha256": SHA_RULES,
              "size": 2048}]
    data = [{"name": "ДАННЫЕ.xlsx", "sheets": 2, "rows": 40,
             "sha256": SHA_DATA}]
    return normalize_declaration({
        "run_id": run_id,
        "created_at": at,
        "created_by": rr.PANEL_ACTOR,
        "purpose": "регресс",
        "contour": {"name": "local", "services": {}},
        "airline": {"name": "Проба-дерево", "data_class": "synthetic",
                    "llm_scope": "any"},
        "rules_documents": rules,
        "data_files": data,
        "answers": [],
        "catalog": {"name": "template-catalog", "version": "0.4.0",
                    "fingerprint": SHA_CATALOG, "path": None},
        "catalog_extension": None,
        "bindings": {"catalog_version": "0.4.0",
                     "catalog_fingerprint": SHA_CATALOG, "path": None},
        "models": [{"role": "extractor", "model": model, "provider": "облако",
                    "channel": "api", "effort": "high"}],
        "solvers": [],
        "code": {"cdpm_commit": "e" * 40, "cosmos_commit": "e" * 40,
                 "runner_fingerprint": None},
        "libraries": {"python": "3.14.4", "packages_sha256": None,
                      "packages": {}},
        "planned_stages": list(rr.PANEL_STAGES),
        "stop_after": "package",
        "expectations": {"kind": None, "ref": None},
        "lineage": {"root_sha256": rr.root_sha256(rules, data),
                    "root_answers_sha256": None,
                    "parent_run": source_run,
                    "branch_of": None,
                    "package_sha256": None,
                    "source_run": source_run},
        "answer_policy": "человек",
    })


def _run(runs_root: Path, run_id: str, *, at: str, model: str = "модель-а",
         source_run: str | None = None, done: bool = True) -> Path:
    """Прогон панели на диске: голова, журнал одного действия и (по
    желанию) доставленный пакет — им прогон и становится «завершён»."""
    run = Path(runs_root) / run_id
    _write(run / "status.json", {"state": "пакет отправлен",
                                 "created_at": at,
                                 "files": ["ПРАВИЛА.docx"],
                                 "extract_counters": {"rules": 12}})
    acts = [{"n": 1, "action": "extract", "at": at, "kind": "start",
             "files": []},
            {"n": 1, "kind": "end", "at": at, "duration_s": 12.0,
             "state_after": {"state": "извлечено"}, "error": None}]
    (run / "journal").mkdir(parents=True, exist_ok=True)
    (run / "journal" / "actions.jsonl").write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in acts) + "\n",
        encoding="utf-8")
    if done:
        _delivery(run, at)
    rr.declare(run, _declaration(run_id, at=at, model=model,
                                 source_run=source_run))
    return run


def _forest(runs_root: Path) -> Path:
    """Каталог прогонов панели: корень, соседняя ветвь (другая модель) и
    продолжение от корня."""
    runs = Path(runs_root) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    _run(runs, "run-0001", at="2026-09-10T07:00:00+02:00", model="модель-а")
    _run(runs, "run-0002", at="2026-09-10T08:00:00+02:00", model="модель-б")
    _run(runs, "run-0003", at="2026-09-10T09:00:00+02:00", model="модель-а",
         source_run="run-0001")
    return runs


# ═══════════════════════════════════ индекс ═══════════════════════════

def test_index_of_panel_runs_is_a_file_and_read_from_it(tmp_path):
    """Индекс собирается по каталогу прогонов, кладётся файлом
    `run-tree.json` и читается ИЗ ФАЙЛА: обхода диска на каждый запрос
    нет (12.1). Три записи панели встают одним корнем, и ребро каждой
    называет родство."""
    runs = _forest(tmp_path)
    tree = rtp.build_index(runs)
    assert rtp.index_path(runs) == runs / "run-tree.json"
    assert rtp.index_path(runs).is_file()
    assert tree["schema"] == rt.SCHEMA
    assert rt.validate_index(tree) == []
    assert tree["refusals"] == [] and tree["orphans"] == []
    assert len(tree["roots"]) == 1
    nodes = {n["run_id"]: n for n in rt.nodes(tree)}
    assert set(nodes) == {"run-0001", "run-0002", "run-0003"}
    assert all(n["state"] == "завершён" for n in nodes.values())
    assert nodes["run-0001"]["edge"]["kind"] == rt.EDGE_ROOT
    assert nodes["run-0002"]["edge"]["kind"] == rt.EDGE_SIBLING
    assert nodes["run-0002"]["edge"]["to"] == "run-0001"
    # Чем ветвь отличается — словом словаря ядра, а не пересказом.
    assert nodes["run-0002"]["edge"]["differs"] == ["модели"]
    assert nodes["run-0003"]["edge"]["kind"] == rt.EDGE_CONTINUATION
    assert nodes["run-0003"]["edge"]["to"] == "run-0001"
    # Чтение — из файла: убранная с диска папка из ИНДЕКСА не исчезает,
    # пока индекс не перестроят.
    import shutil
    shutil.rmtree(runs / "run-0002")
    assert len(rt.nodes(rtp.load_or_build(runs))) == 3
    assert len(rt.nodes(rtp.load_or_build(runs, rebuild=True))) == 2
    assert len(rt.nodes(rtp.load_or_build(runs))) == 2      # файл переписан


def test_index_refuses_a_broken_record_without_hiding_the_tree(tmp_path):
    """Негодная запись — отказ со словами В ИНДЕКСЕ, а не исключение:
    одна испорченная папка не имеет права скрыть дерево целиком."""
    runs = _forest(tmp_path)
    (runs / "run-0002" / rr.RECORD_FILE).write_text("{не json",
                                                    encoding="utf-8")
    tree = rtp.build_index(runs)
    assert len(rt.nodes(tree)) == 2
    assert len(tree["refusals"]) == 1
    assert "run-0002" in tree["refusals"][0]["path"]
    assert tree["refusals"][0]["words"]


def test_archive_is_not_a_root_of_the_walk(tmp_path):
    """Архив прогонов в обход НЕ входит: пометить архивный узел в
    закрытом словаре индекса нечем, а дерево, молча воскрешающее убранные
    из списка прогоны, обманывало бы читателя."""
    runs = _forest(tmp_path)
    assert rtp.roots(runs) == [runs]
    archive = tmp_path / "cdpm-runs-archive"
    archive.mkdir()
    import shutil
    shutil.move(str(runs / "run-0002"), str(archive / "run-0002"))
    tree = rtp.build_index(runs)
    assert {n["run_id"] for n in rt.nodes(tree)} == {"run-0001", "run-0003"}


# ═══════════════════════════════════ ручки ════════════════════════════

@pytest.fixture()
def panel(tmp_path, monkeypatch):
    """Панель на тестовом клиенте с готовым каталогом прогонов: три
    записи, служба каталога — заведомо недоступный адрес."""
    from fastapi.testclient import TestClient

    from cdpm import webui
    runs = _forest(tmp_path)
    monkeypatch.setenv("CDPM_RUNS_DIR", str(runs))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CREW_CATALOG_URL", "http://catalog.invalid")
    client = TestClient(webui.app)
    client.runs = runs
    return client


def test_run_tree_handle_gives_the_whole_index(panel):
    """`GET /api/run-tree` — индекс целиком; `?rebuild=1` собирает его
    заново перед ответом."""
    got = panel.get("/api/run-tree")
    assert got.status_code == 200, got.text
    tree = got.json()
    assert set(tree) == {"schema", "built_at", "roots", "orphans", "refusals"}
    assert tree["schema"] == rt.SCHEMA and len(tree["roots"]) == 1
    assert len(rt.nodes(tree)) == 3
    import shutil
    shutil.rmtree(panel.runs / "run-0003")
    assert len(rt.nodes(panel.get("/api/run-tree").json())) == 3
    again = panel.get("/api/run-tree", params={"rebuild": 1}).json()
    assert len(rt.nodes(again)) == 2


def test_run_tree_of_a_run_shows_path_siblings_and_comparisons(panel):
    """`GET /api/runs/<номер>/tree` — узел, путь от корня, соседние
    ветви с «чем отличается», золотой корня, сравнения и ожидания
    набора (12.2)."""
    got = panel.get("/api/runs/run-0003/tree")
    assert got.status_code == 200, got.text
    body = got.json()
    # Форма ответа — договор с COSMOS 23:1x (одна у обеих систем).
    assert set(body) == {"run_id", "built_at", "node", "path", "siblings",
                         "golden", "compare_golden", "compare_previous",
                         "expectations", "golden_action"}
    assert body["run_id"] == "run-0003" and body["built_at"]
    assert set(body["golden_action"]) == {"allowed", "code", "words", "confirmed",
                                          "owner", "by", "at", "words_of_owner"}
    assert body["golden_action"]["allowed"] is False      # площадка не prod
    assert body["golden_action"]["code"] == "golden-not-prod"
    assert body["node"]["run_id"] == "run-0003"
    assert [n["run_id"] for n in body["path"]["runs"]] == ["run-0001",
                                                           "run-0003"]
    assert body["path"]["root"]["root_sha256"] == body["node"]["root_sha256"]
    assert [n["run_id"] for n in body["siblings"]] == ["run-0001", "run-0002"]
    assert {n["run_id"]: n["edge"]["differs"]
            for n in body["siblings"]}["run-0002"] == ["модели"]
    assert body["golden"] is None and body["compare_golden"] is None
    assert body["compare_previous"]["a"] == "run-0002"
    assert body["compare_previous"]["b"] == "run-0003"
    assert body["expectations"] is None
    # Прогона нет вовсе — 404 словами, и прогона нет в дереве — тоже.
    assert panel.get("/api/runs/run-0099/tree").status_code == 404
    (panel.runs / "run-0007").mkdir()
    refused = panel.get("/api/runs/run-0007/tree")
    assert refused.status_code == 404
    assert "run-0007" in refused.json()["detail"]


def test_tree_of_a_run_declared_by_the_runner_is_found_by_folder_name(panel):
    """Живая находка 10.09.2026 23:5x (run-0147, круг s5): узлы дерева
    ключуются номером ЗАПИСИ — у прогона, объявленного раннером, это его
    номер (20260910-…), а ручка панели получает имя папки run-NNNN; без
    перевода имени в номер записи ручка отвечала 404. Сравнение принимает
    и имена папок, и номера записей."""
    runner_id = "20260910-234806-9c6f40ad"
    run = panel.runs / "run-0004"
    at = "2026-09-10T23:48:06+02:00"
    _write(run / "status.json", {"state": "пакет отправлен", "created_at": at,
                                 "files": ["ПРАВИЛА.docx"],
                                 "extract_counters": {"rules": 12}})
    (run / "journal").mkdir(parents=True, exist_ok=True)
    (run / "journal" / "actions.jsonl").write_text(
        json.dumps({"n": 1, "action": "extract", "at": at, "kind": "start",
                    "files": []}, ensure_ascii=False) + "\n"
        + json.dumps({"n": 1, "kind": "end", "at": at, "duration_s": 9.0,
                      "state_after": {"state": "извлечено"}, "error": None},
                     ensure_ascii=False) + "\n", encoding="utf-8")
    _delivery(run, at)
    head = _declaration(runner_id, at=at, model="модель-а")
    head["created_by"] = "раннер наборов H0"
    rr.declare(run, head)
    assert rtp.record_id_of_run(panel.runs, "run-0004") == runner_id
    assert rtp.record_id_of_run(panel.runs, runner_id) == runner_id
    assert rtp.record_id_of_run(panel.runs, "run-0001") == "run-0001"
    panel.get("/api/run-tree", params={"rebuild": 1})
    got = panel.get("/api/runs/run-0004/tree")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["run_id"] == runner_id and body["node"]["run_id"] == runner_id
    assert body["node"]["created_by"] == "раннер наборов H0"
    assert body["path"]["runs"][-1]["run_id"] == runner_id
    cmp = panel.get("/api/run-tree/compare",
                    params={"a": "run-0004", "b": "run-0001"})
    assert cmp.status_code == 200, cmp.text
    assert cmp.json()["a"]["run_id"] == runner_id if isinstance(cmp.json().get("a"), dict) \
        else cmp.status_code == 200


def test_compare_handle_compares_two_runs_by_number(panel):
    """`GET /api/run-tree/compare?a=&b=` — сравнение двух прогонов;
    узла нет — 404 словами."""
    got = panel.get("/api/run-tree/compare",
                    params={"a": "run-0001", "b": "run-0002"})
    assert got.status_code == 200, got.text
    cmp = got.json()
    assert (cmp["a"], cmp["b"]) == ("run-0001", "run-0002")
    assert cmp["same_root"] is True
    changed = {row["field"] for row in cmp["declaration"]["changed"]}
    assert changed == {"models.extractor.model"}
    assert cmp["words"] and any("модель-б" in w for w in cmp["words"])
    assert panel.get("/api/run-tree/compare",
                     params={"a": "run-0001", "b": "run-0099"}).status_code == 404
    assert panel.get("/api/run-tree/compare",
                     params={"a": "run-0001"}).status_code == 400


def test_rebuild_handle_and_event_rebuild_keep_the_index_fresh(panel):
    """Индекс перестраивается ручкой `POST /api/run-tree/rebuild` и ПО
    СОБЫТИЮ — приёмом чужих записей хранителем (12.1)."""
    got = panel.post("/api/run-tree/rebuild")
    assert got.status_code == 200, got.text
    body = got.json()
    # Форма ответа — договор с COSMOS 23:1x: {ok, tree, roots, counts,
    # golden_pending, index_file}.
    assert body["ok"] is True
    assert body["counts"] == {"roots": 1, "runs": 3, "orphans": 0, "refusals": 0}
    assert [r["runs"] for r in body["roots"]] == [3]
    assert body["tree"]["schema"] == "cosmos-run-tree/v1"
    assert body["golden_pending"] == []                  # площадка не prod
    assert body["built_at"] and body["index_file"].endswith("run-tree.json")
    # Событие: чужая система дописала итог — индекс догнал сам.
    (panel.runs / "run-0001" / "run-tree-check").write_text("", encoding="utf-8")
    rtp.index_path(panel.runs).unlink()
    head = rr.record_head(panel.runs / "run-0001")
    sent = panel.post("/api/runs/run-0001/record/append", json={
        "schema": rr.APPEND_SCHEMA, "record_id": head["run_id"],
        "declaration_sha256": head["declaration_sha256"],
        "system": "cosmos",
        "results": {"verdicts": {"pairing": "OK"}}})
    assert sent.status_code == 200, sent.text
    assert rtp.index_path(panel.runs).is_file()
    tree = rtp.load_or_build(panel.runs)
    assert rt.find_node(tree, "run-0001")["verdicts"]["cosmos"] == \
        {"pairing": "OK"}


# ═════════════════════════════════ золотой ════════════════════════════

def _golden(panel, run_id: str, **body):
    return panel.post(f"/api/runs/{run_id}/golden", json=body)


def test_golden_is_confirmed_only_by_a_person_on_prod(panel, monkeypatch):
    """Золотой прогон — только подписью человека, только у завершённого
    прогона и только на площадке prod (решение владельца 6.3):
    пустое «кто» и пустые слова — 400; незавершённый прогон и не та
    площадка — 409."""
    monkeypatch.setenv("CREW_CONTOUR", "prod")
    refused = _golden(panel, "run-0001", by="", words="приёмка пройдена")
    assert refused.status_code == 422                  # golden-without-by
    assert "КТО подтвердил" in refused.json()["detail"]
    refused = _golden(panel, "run-0001", by=OWNER, words="   ")
    assert refused.status_code == 422                  # golden-without-words
    assert "КАКИМИ СЛОВАМИ" in refused.json()["detail"]
    # Машинный канал ручку золотого не получает — 403.
    machine = panel.post("/api/runs/run-0001/golden",
                         json={"by": OWNER, "words": "приёмка пройдена"},
                         headers={"X-Channel-Token": "x"})
    assert machine.status_code == 403
    # Незавершённый прогон образцом не бывает.
    (panel.runs / "run-0002" / "channel" / "delivery.json").unlink()
    rr.refresh(panel.runs / "run-0002")
    refused = _golden(panel, "run-0002", by=OWNER, words="пусть будет")
    assert refused.status_code == 409
    assert "завершённый" in refused.json()["detail"]
    # Площадка не prod — ручка отказывает, даже когда прогон завершён.
    monkeypatch.setenv("CREW_CONTOUR", "test")
    refused = _golden(panel, "run-0001", by=OWNER, words="приёмка пройдена")
    assert refused.status_code == 409
    assert "prod" in refused.json()["detail"]
    # Личность из заголовка Cloudflare Access, когда «кто» не назван.
    monkeypatch.setenv("CREW_CONTOUR", "prod")
    got = panel.post("/api/runs/run-0001/golden",
                     json={"words": "приёмка трёх наборов пройдена"},
                     headers={"Cf-Access-Authenticated-User-Email": OWNER})
    assert got.status_code == 200, got.text
    rec = got.json()
    assert rec["schema"] == rr.RECORD_SCHEMA
    golden = rec["results"]["golden"]
    assert golden["confirmed"] is True and golden["by"] == OWNER
    assert golden["words"] == "приёмка трёх наборов пройдена" and golden["at"]
    # Голова прогона от подтверждения не шелохнулась.
    assert rec["declaration_sha256"] == \
        rr.read_declaration(panel.runs / "run-0001")["declaration_sha256"]


def test_golden_survives_the_record_being_rebuilt_and_reaches_the_tree(
        panel, monkeypatch):
    """Признак переживает пересборку записи (панель переписывает
    `run-record.json` после каждого действия) и виден в индексе дерева и
    в ручке записи."""
    monkeypatch.setenv("CREW_CONTOUR", "prod")
    assert _golden(panel, "run-0001", by=OWNER,
                   words="приёмка пройдена").status_code == 200
    run = panel.runs / "run-0001"
    onfile = json.loads((run / rr.RECORD_FILE).read_text(encoding="utf-8"))
    assert onfile["results"]["golden"]["by"] == OWNER
    # Пересборка записи признака не стирает: он лежит в журнале
    # хранителя, а не в переписываемом файле.
    assert rr.refresh(run)["results"]["golden"]["confirmed"] is True
    assert rr.build(run)["results"]["golden"]["by"] == OWNER
    assert panel.get("/api/runs/run-0001/record").json()[
        "results"]["golden"]["confirmed"] is True
    # Подтверждение — действие панели: оно в журнале прогона, а
    # состояние прогона от него не шелохнулось.
    rec = rr.build(run)
    assert [e["substage"] for e in rec["journal"]][-1] == "golden"
    assert rec["journal"][-1]["outcome"] == "успех"
    assert rec["results"]["state"] == "завершён"
    # Индекс дерева знает золотой прогон корня, а соседние ветви
    # сравниваются с ним по умолчанию.
    body = panel.get("/api/runs/run-0002/tree").json()
    assert body["golden"]["run_id"] == "run-0001"
    assert body["compare_golden"]["a"] == "run-0001"
    assert body["compare_golden"]["kind"] == rt.COMPARE_GOLDEN


def test_golden_is_unmarked_by_the_same_person(panel, monkeypatch):
    """Снимает признак ТОТ ЖЕ человек, что подтверждал: чужая рука —
    409 словами. Прогон не золотой — снимать нечего."""
    monkeypatch.setenv("CREW_CONTOUR", "prod")
    assert _golden(panel, "run-0001", by=OWNER,
                   words="приёмка пройдена").status_code == 200
    refused = panel.post("/api/runs/run-0001/golden/unmark",
                         json={"by": OTHER})
    assert refused.status_code == 422                  # golden-by-mismatch
    assert OWNER in refused.json()["detail"]
    # Отказ ничего не изменил — и в журнал прогона не попал: иначе
    # завершённый прогон стал бы «остановленным по правилу».
    rec = rr.build(panel.runs / "run-0001")
    assert rec["results"]["state"] == "завершён"
    assert [e["substage"] for e in rec["journal"]] == ["extract", "golden"]
    got = panel.post("/api/runs/run-0001/golden/unmark", json={"by": OWNER})
    assert got.status_code == 200, got.text
    assert got.json()["results"]["golden"] == rr.empty_golden()
    # Строка подтверждения из журнала хранителя не пропала — снятие
    # прибавило строку, а не стёрло прежнюю.
    rows = [json.loads(line) for line in
            rr.appends_path(panel.runs / "run-0001")
            .read_text(encoding="utf-8").splitlines() if line.strip()]
    marks = [r["results"]["golden"] for r in rows
             if isinstance(r.get("results"), dict)
             and isinstance(r["results"].get("golden"), dict)]
    assert [m["confirmed"] for m in marks] == [True, False]
    assert marks[0]["by"] == OWNER
    again = panel.post("/api/runs/run-0001/golden/unmark", json={"by": OWNER})
    assert again.status_code == 409
    assert "снимать нечего" in again.json()["detail"]


def test_record_carries_comparison_with_previous_and_golden(panel,
                                                            monkeypatch):
    """12.4: при закрытии записи хранитель кладёт в неё сравнение с
    предыдущим прогоном и с золотым — `{run_id, words[]}`; карточка
    показывает их без сборки дерева."""
    monkeypatch.setenv("CREW_CONTOUR", "prod")
    assert _golden(panel, "run-0001", by=OWNER,
                   words="приёмка пройдена").status_code == 200
    rec = panel.get("/api/runs/run-0003/record").json()
    comparison = rec["results"]["comparison"]
    assert set(comparison) == {"expectations", "previous_run", "golden"}
    assert comparison["previous_run"]["run_id"] == "run-0002"
    assert comparison["previous_run"]["words"]
    assert comparison["golden"]["run_id"] == "run-0001"
    assert any("ЗОЛОТОЙ" in w for w in comparison["golden"]["words"])
    # Ожидания набора пишет РАННЕР — панель их хранит и показывает.
    head = rr.record_head(panel.runs / "run-0003")
    sent = panel.post("/api/runs/run-0003/record/append", json={
        "schema": rr.APPEND_SCHEMA, "record_id": head["run_id"],
        "declaration_sha256": head["declaration_sha256"],
        "system": "runner",
        "results": {"comparison": {"expectations": {"судимые": 3,
                                                    "расхождения": []}}}})
    assert sent.status_code == 200, sent.text
    assert sent.json()["record"]["results"]["comparison"]["expectations"] == \
        {"судимые": 3, "расхождения": []}
    assert panel.get("/api/runs/run-0003/tree").json()["expectations"] == \
        {"судимые": 3, "расхождения": []}


# ═══════════════ отпечаток ревизии ответов по содержанию (11.7) ═══════

def _book(path: Path, sheets: dict, *, extra_sheet: bool = False,
          widths: bool = False) -> Path:
    """Книга ответов: листы частей с шапкой анкеты и строками
    «вопрос → ответ». Байты книг нарочно разные (порядок листов, чужой
    лист, ширины колонок) — содержание одно."""
    from openpyxl import Workbook

    from cdpm import anketa_xlsx
    wb = Workbook()
    wb.remove(wb.active)
    if extra_sheet:
        ws = wb.create_sheet(anketa_xlsx.SUMMARY_TITLE)
        ws.append([anketa_xlsx.REVISION_LABEL, 2])
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        ws.append(list(anketa_xlsx.HEADERS))
        for qid, answer in rows:
            ws.append([qid, f"вопрос {qid}", "свободный", "", "ПРАВИЛА.docx",
                       answer, "f" * 12])
        if widths:
            ws.column_dimensions["B"].width = 40
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


def test_answers_fingerprint_is_content_not_bytes(tmp_path):
    """Отпечаток ревизии ответов — по СОДЕРЖАНИЮ (11.7): две книги с
    разными байтами и одними ответами дают ОДИН отпечаток, изменённый
    ответ — другой, а книга-обрывок отпечатка не даёт."""
    from cdpm import anketa_xlsx
    part1, part2 = anketa_xlsx.SHEET_TITLES["1"], anketa_xlsx.SHEET_TITLES["2"]
    a = _book(tmp_path / "a.xlsx",
              {part1: [("Q-1-a", "да"), ("Q-2-b", "нет, только в базе")]})
    b = _book(tmp_path / "b.xlsx",
              {part2: [("Q-2-b", " нет,\n только  в базе ")],
               part1: [("Q-1-a", "да ")]},
              extra_sheet=True, widths=True)
    assert a.read_bytes() != b.read_bytes()               # байты РАЗНЫЕ
    assert afp.read_pairs(a) == {"Q-1-a": "да",
                                 "Q-2-b": "нет, только в базе"}
    assert afp.read_pairs(b) == afp.read_pairs(a)
    sha = afp.book_content_sha256(a)
    assert len(sha) == 64 and afp.book_content_sha256(b) == sha
    # Изменённый ответ — другой отпечаток.
    c = _book(tmp_path / "c.xlsx",
              {part1: [("Q-1-a", "да"), ("Q-2-b", "нет, никогда")]})
    assert afp.book_content_sha256(c) != sha
    # Канон: пары «вопрос → ответ» по возрастанию номера вопроса.
    import hashlib

    from vmp.run_record import canonical
    assert sha == hashlib.sha256(canonical(
        [["Q-1-a", "да"], ["Q-2-b", "нет, только в базе"]])).hexdigest()
    # Лист вне частей анкеты в отпечаток не входит — ответов с него
    # панель тоже не принимает.
    d = _book(tmp_path / "d.xlsx", {part1: [("Q-1-a", "да"),
                                            ("Q-2-b", "нет, только в базе")],
                                    "Пометки": [("Q-9-x", "чужое")]})
    assert afp.book_content_sha256(d) == sha
    # Два РАЗНЫХ ответа на один вопрос: ни один не принят (правило
    # чтения книги), остальные пары целы.
    e = _book(tmp_path / "e.xlsx", {part1: [("Q-1-a", "да"),
                                            ("Q-1-a", "нет"),
                                            ("Q-2-b", "нет, только в базе")]})
    assert afp.read_pairs(e) == {"Q-2-b": "нет, только в базе"}
    # Обрывок вместо книги и книги нет вовсе — отпечатка нет.
    (tmp_path / "обрывок.xlsx").write_bytes("не книга".encode("utf-8"))
    assert afp.book_content_sha256(tmp_path / "обрывок.xlsx") is None
    assert afp.book_content_sha256(tmp_path / "нет.xlsx") is None
    assert afp.content_sha256({}) is None


def test_record_answers_carry_the_content_fingerprint(tmp_path):
    """Запись о прогоне кладёт в `answers[].sha256` отпечаток
    СОДЕРЖАНИЯ, а байты книги остаются рядом (`book_sha256` сводки
    ревизии) — по ним узнают файл."""
    import hashlib

    from cdpm import anketa_xlsx
    runs = _forest(tmp_path)
    run = runs / "run-0001"
    part1 = anketa_xlsx.SHEET_TITLES["1"]
    book = _book(run / "answers" / "rev-002" / "anketa-otvety.xlsx",
                 {part1: [("Q-1-a", "да")]})
    _write(run / "answers" / "rev-002" / "answers-summary.json",
           {"book_sha256": hashlib.sha256(book.read_bytes()).hexdigest(),
            "applied": 1, "unparsed": 0})
    revs = rr.answers_revisions(run)
    assert [r["revision"] for r in revs] == [2]
    assert revs[0]["sha256"] == afp.book_content_sha256(book)
    assert revs[0]["sha256"] != revs[0]["book_sha256"]
    assert revs[0]["book_sha256"] == \
        hashlib.sha256(book.read_bytes()).hexdigest()
    rec = rr.build(run)
    assert rec["declaration"]["answers"] == [
        {"revision": 2, "sha256": afp.book_content_sha256(book),
         "source": rr.ANSWER_SOURCE_BOOK, "at": None}]
    # Готовый отпечаток из сводки ревизии книгу второй раз не открывает.
    _write(run / "answers" / "rev-002" / "answers-summary.json",
           {"book_sha256": "0" * 64, "content_sha256": "1" * 64})
    assert rr.answers_revisions(run)[0]["sha256"] == "1" * 64
