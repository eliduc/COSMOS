# -*- coding: utf-8 -*-
"""Оформление существующих данных прогонами без потерь
(webapp/cosmos_runs.ensure_storage, раздел 12 проекта прогонов).

Стенд в tmp: входной каталог с тремя принятыми пакетами (verdict.json +
package.zip), одним незавершённым приёмом (без verdict.json) и
посторонними файлами корня; каталоги нажатий — два со state.json и
попытками (один по sha не из инбокса — пакет «файлом»), три без
state.json (кэш реестра страницы); журнал приёмов с отказом GATE_RED до
принятия; снимок сверки по ИДАК с BOM; зеркало current.json.

Охраняемое: по прогону на принятый пакет с номерами по времени первого
ПРИНЯТОГО приёма; state.json не трогаются, попытки не теряются; текущий
указатель ведёт на тот же sha, что зеркало; повторный вызов — пусто;
обрыв посередине продолжается; created_at со смещением; CLI --dry-run
ничего не пишет; числа сводки считаются по стенду, не вшиты."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tests.test_cdpm_task_package import _ready_doc  # noqa: E402
from tests.test_task_channel import _machined_doc, _pkg_bytes  # noqa: E402

ISO_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
BOM = "﻿"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    for k in ("CREW_TASK_INBOX", "CREW_TASK_PACKAGE", "CREW_VMP_REGISTRY",
              "CREW_SOLVER_URL", "CREW_COSMOS_RUNS_DIR"):
        monkeypatch.delenv(k, raising=False)
    from webapp import cosmos_runs, flow, task_channel
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)
    cosmos_runs.forget()


def _packages(tmp_path: Path) -> list[bytes]:
    doc3 = _ready_doc()
    doc3.setdefault("meta", {})["revision"] = 7
    return [_pkg_bytes(tmp_path / "pkg-a"),
            _pkg_bytes(tmp_path / "pkg-b", _machined_doc()),
            _pkg_bytes(tmp_path / "pkg-c", doc3)]


def _wj(path: Path, doc, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((BOM if bom else "") + json.dumps(doc, ensure_ascii=False),
                    encoding="utf-8")


def _attempt(aid: str, task: str, started: str) -> dict:
    return {"id": aid, "task": task, "pairing_selection": None,
            "engine": "cpsat" if task == "pairing" else None,
            "started_at": started, "state": "завершено", "done": True,
            "verdict": "пейринг построен" if task == "pairing" else "допустимо",
            "error": None, "finished_at": started, "catalog": None}


def build_stand(tmp_path: Path) -> dict:
    """Стенд раздела 12; возвращает ожидания, ПОСЧИТАННЫЕ по раскладке."""
    root = tmp_path / "data-root"
    inbox = root / "task-inbox"
    pr_root = root / "package-runs"
    bodies = _packages(tmp_path)
    shas = [hashlib.sha256(b).hexdigest() for b in bodies]
    # принятые пакеты: приёмы в журнале в НЕалфавитном порядке времени
    times = {shas[0]: "2026-09-05T12:00:00", shas[1]: "2026-09-04T09:30:00",
             shas[2]: "2026-09-06T18:45:00"}
    for body, sha in zip(bodies, shas):
        d = inbox / sha
        d.mkdir(parents=True)
        (d / "package.zip").write_bytes(body)
        _wj(d / "verdict.json", {"verdict": "OK" if sha != shas[2] else "MISMATCH",
                                 "mg": {"pairing": {"state": "optimization",
                                                    "launch_allowed": sha != shas[2]},
                                        "rostering": {"state": "feasible_soft",
                                                      "launch_allowed": False}},
                                 "mismatch": [], "detail": "принят", "sha": sha,
                                 "spec_version": "1.18", "source_run": "run-9001",
                                 "already_accepted": False, "formulation": {}})
    # незавершённый приём: package.zip без verdict.json
    half = "0" * 63 + "1"
    (inbox / half).mkdir()
    (inbox / half / "package.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    # посторонние файлы корня инбокса
    _wj(inbox / "model-run-0062.json", {"model": "x"})
    rows = [{"at": "2026-09-04T09:00:00", "sha": shas[1], "verdict": "GATE_RED",
             "repeat": False},
            {"at": times[shas[1]], "sha": shas[1], "verdict": "OK", "repeat": False},
            {"at": times[shas[0]], "sha": shas[0], "verdict": "OK", "repeat": False},
            {"at": "2026-09-05T13:00:00", "sha": shas[0], "verdict": "OK",
             "repeat": True},
            {"at": times[shas[2]], "sha": shas[2], "verdict": "MISMATCH",
             "repeat": False},
            {"at": "2026-09-06T19:00:00", "sha": "f" * 64, "verdict": "REFUSED",
             "repeat": False}]
    (inbox / "receipts.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    _wj(inbox / "current.json", {"sha": shas[1],
                                 "path": str(inbox / shas[1] / "package.zip"),
                                 "received_at": times[shas[1]],
                                 "spec_version": "1.18", "source_run": "run-9001",
                                 "source": "channel"})
    # нажатия: по первому пакету инбокса (2 попытки) и по пакету «файлом»
    file_pkg = tmp_path / "file-pkg.zip"
    file_pkg.write_bytes(bodies[0] + b"\n")            # другой sha, есть файл
    file_sha = hashlib.sha256(file_pkg.read_bytes()).hexdigest()
    # у пакета «файлом» без строки журнала время — mtime файла (раздел 12)
    import os
    stamp = datetime.fromisoformat("2026-09-07T08:00:00").timestamp()
    os.utime(file_pkg, (stamp, stamp))
    _wj(pr_root / shas[0] / "state.json",
        {"task": "rostering", "done": True, "package_sha": shas[0],
         "package_path": str(inbox / shas[0] / "package.zip"),
         "attempts": [_attempt("A1", "pairing", "2026-09-05T12:10:00"),
                      _attempt("A2", "rostering", "2026-09-05T12:20:00")]})
    _wj(pr_root / file_sha / "state.json",
        {"task": "pairing", "done": True, "package_sha": file_sha,
         "package_path": str(file_pkg),
         "attempts": [_attempt("F1", "pairing", "2026-09-07T08:00:00")]})
    lost_sha = "e" * 64
    _wj(pr_root / lost_sha / "state.json",
        {"task": "pairing", "done": True, "package_sha": lost_sha,
         "package_path": str(tmp_path / "gone.zip"),
         "attempts": [_attempt("L1", "pairing", "2026-09-07T09:00:00")]})
    for i in range(3):
        (pr_root / (("%x" % i) * 64) / "registry").mkdir(parents=True)
        _wj(pr_root / (("%x" % i) * 64) / "registry" / "vmp-executors-registry.page.json",
            {"rules": []})
    _wj(root / "source-check" / "last.json",
        {"status": "done", "run": "run-9001", "counts": {"violated": 0}},
        bom=True)
    expected_order = sorted([shas[1], shas[0], shas[2]], key=lambda s: times[s])
    return {"shas": shas, "times": times, "file_sha": file_sha,
            "lost_sha": lost_sha, "half": half,
            "expected_order": expected_order + [file_sha, lost_sha],
            "inbox": inbox, "pr_root": pr_root, "root": root}


def _runs(root: Path) -> dict[str, dict]:
    out = {}
    for p in sorted((root / "cosmos-runs").glob("run-*")):
        if (p / "run.json").is_file():
            out[p.name] = json.loads((p / "run.json").read_text(encoding="utf-8"))
    return out


def test_runs_created_per_accepted_sha_with_numbers_by_first_accepted_receipt(
        tmp_path):
    from webapp import cosmos_runs as cr
    st = build_stand(tmp_path)
    got = cr.ensure_storage("init")
    assert got["initialized"] is True
    runs = _runs(st["root"])
    # 3 принятых + 1 «файлом» с файлом + 1 «файлом» без файла = 5
    assert [r["packages"][0]["sha"] for r in runs.values()] == st["expected_order"]
    assert list(runs) == ["run-0001", "run-0002", "run-0003", "run-0004", "run-0005"]
    assert all(r["origin"] == "adoption" and r["created_by"] == "adoption"
               for r in runs.values())
    first = runs["run-0001"]
    assert first["packages"][0]["sha"] == st["shas"][1]
    assert first["created_at"].startswith("2026-09-04T09:30:00")
    assert first["adoption"]["from"] == "task-inbox"
    assert first["packages"][0]["verdict"] == "OK"
    assert first["packages"][0]["source"] == "channel"
    assert first["packages"][0]["workdir"] == "package-runs/" + st["shas"][1]
    third = runs["run-0003"]
    assert third["packages"][0]["verdict"] == "MISMATCH"
    assert third["packages"][0]["mg"]["pairing"]["launch_allowed"] is False
    assert json.loads((st["root"] / "cosmos-runs" / "counter.json")
                      .read_text(encoding="utf-8"))["last"] == 5
    assert (st["root"] / "cosmos-runs" / "storage.json").is_file()
    assert not (st["root"] / "cosmos-runs" / "order.json").exists()
    log = [json.loads(x) for x in (st["root"] / "cosmos-runs" / "adoption.jsonl")
           .read_text(encoding="utf-8").splitlines()]
    assert [x["run"] for x in log] == list(runs)


def test_inbox_dir_without_verdict_and_foreign_files_skipped_named(tmp_path):
    from webapp import cosmos_runs as cr
    st = build_stand(tmp_path)
    got = cr.ensure_storage("init")
    skipped = got["adoption"]["skipped_inbox"]
    assert skipped == [{"sha12": st["half"][:12], "reason_code": "receipt-incomplete"}]
    shas_in_runs = {r["packages"][0]["sha"] for r in _runs(st["root"]).values()}
    assert st["half"] not in shas_in_runs
    assert not any("model-run" in s for s in shas_in_runs)


def test_cache_dirs_without_state_are_skipped_counted(tmp_path):
    from webapp import cosmos_runs as cr
    st = build_stand(tmp_path)
    got = cr.ensure_storage("init")
    assert got["adoption"]["skipped_dirs"] == 3
    for i in range(3):
        d = st["pr_root"] / (("%x" % i) * 64)
        assert not (d / "holder.json").exists()


def test_file_package_dir_becomes_run_with_state_package_path_or_missing_code(
        tmp_path):
    from webapp import cosmos_runs as cr
    st = build_stand(tmp_path)
    cr.ensure_storage("init")
    runs = _runs(st["root"])
    by_sha = {r["packages"][0]["sha"]: r for r in runs.values()}
    f = by_sha[st["file_sha"]]
    assert f["adoption"]["from"] == "package-runs"
    assert f["packages"][0]["source"] == "file"
    assert f["packages"][0]["path"].endswith("file-pkg.zip")
    assert f["packages"][0]["note_code"] is None
    assert f["created_at"].startswith("2026-09-07T08:00:00")  # mtime файла
    lost = by_sha[st["lost_sha"]]
    assert lost["created_at"].startswith("2026-09-07T09:00:00")  # первая попытка
    assert lost["packages"][0]["path"] is None
    assert lost["packages"][0]["note_code"] == "package-bytes-missing"
    lst = {x["run"]: x for x in cr.list_runs()["runs"]}
    assert lst[lost["run"]]["state_kind"] == "lost"
    assert lst[f["run"]]["state_kind"] == "done"


def test_attempt_counts_and_state_files_untouched(tmp_path):
    from webapp import cosmos_runs as cr, package_runs as pr
    st = build_stand(tmp_path)
    before = {p: p.read_bytes() for p in st["pr_root"].rglob("state.json")}
    cr.ensure_storage("init")
    after = {p: p.read_bytes() for p in st["pr_root"].rglob("state.json")}
    assert before == after
    total_before = sum(len(json.loads(b)["attempts"]) for b in before.values())
    runs = _runs(st["root"])
    total_after = sum(len(cr.attempts_of(r)) for r in runs.values())
    assert total_before == total_after == 4
    # маркер держателя — в базовых каталогах со state.json, не в новых
    for sha in (st["shas"][0], st["file_sha"], st["lost_sha"]):
        holder = json.loads((st["pr_root"] / sha / "holder.json").read_text(encoding="utf-8"))
        assert holder["kind"] == "run" and holder["sha"] == sha
        assert holder["run"] in runs
    for sha in (st["shas"][1], st["shas"][2]):
        assert not (st["pr_root"] / sha).exists(), "без нажатий каталог не создаётся"
    # сторож сирот видит все state.json (без runs/* здесь)
    assert pr.recover_orphans() == []
    # членство попыток — по каталогу: держатель базового каталога
    card = cr.run_card(runs["run-0002"]["run"])
    assert [a["id"] for a in card["attempts"]] == ["A2", "A1"]
    assert all(a["run"] == "run-0002" for a in card["attempts"])


def test_current_pointer_targets_same_sha_as_mirror(tmp_path):
    from webapp import cosmos_runs as cr, task_channel
    st = build_stand(tmp_path)
    mirror_before = (st["inbox"] / "current.json").read_bytes()
    cr.ensure_storage("init")
    cur = cr.current_run()
    assert cur is not None and cur["run"] == "run-0001"
    assert cur["current_package"] == st["shas"][1]
    assert (st["inbox"] / "current.json").read_bytes() == mirror_before, \
        "оформление зеркало не переписывает"
    got = task_channel.current_package()
    assert got["sha"] == st["shas"][1] and got["run"]["run"] == "run-0001"
    assert got["path"] == str(st["inbox"] / st["shas"][1] / "package.zip")


def test_second_call_is_noop_and_interrupted_first_call_resumes(tmp_path,
                                                                monkeypatch):
    from webapp import cosmos_runs as cr
    st = build_stand(tmp_path)
    real = cr._write_json_atomic
    written = {"runs": 0}

    def cut(path, doc):
        if Path(path).name == "run.json":
            written["runs"] += 1
            if written["runs"] == 3:
                raise OSError("обрыв посередине")
        return real(path, doc)
    monkeypatch.setattr(cr, "_write_json_atomic", cut)
    with pytest.raises(OSError):
        cr.ensure_storage("init")
    assert cr.storage_state() == "uninitialized"
    assert list(_runs(st["root"])) == ["run-0001", "run-0002"]
    assert sorted(p.name for p in (st["root"] / "cosmos-runs").glob("run-*")) \
        == ["run-0001", "run-0002"], "пустой каталог сорванной записи убран"
    monkeypatch.setattr(cr, "_write_json_atomic", real)
    got = cr.ensure_storage("init")
    assert got["initialized"] and got["adoption"]["runs_created"] == 3
    runs = _runs(st["root"])
    assert [r["packages"][0]["sha"] for r in runs.values()] == st["expected_order"]
    assert len({r["packages"][0]["sha"] for r in runs.values()}) == 5
    # Повторный вызов — ничего не оформляет: сводка честно «0».
    assert cr.ensure_storage("init") == {
        "initialized": False, "state": "ready",
        "adoption": {"runs_created": 0}}
    assert len(_runs(st["root"])) == 5


def test_created_at_normalized_with_offset(tmp_path):
    from webapp import cosmos_runs as cr
    st = build_stand(tmp_path)
    cr.ensure_storage("init")
    for r in _runs(st["root"]).values():
        assert ISO_OFFSET.match(r["created_at"]), r["created_at"]
        assert ISO_OFFSET.match(r["passport"]["at"])
    first = _runs(st["root"])["run-0001"]
    want = datetime.fromisoformat(st["times"][st["shas"][1]]).astimezone() \
        .isoformat(timespec="seconds")
    assert first["created_at"] == want
    assert first["packages"][0]["received_at"] == want


def test_bom_last_json_tolerated(tmp_path):
    from webapp import cosmos_runs as cr, flow
    st = build_stand(tmp_path)
    assert (st["root"] / "source-check" / "last.json").read_bytes().startswith(
        BOM.encode("utf-8"))
    got = flow._source_check_state()
    assert got["status"] == "done" and got["checks"] == 0
    snaps = cr._source_check_snapshots()
    assert [t for t, _d in snaps] == [None]
    cr.ensure_storage("init")
    assert cr.list_runs()["runs"]


def test_cli_init_dry_run_writes_nothing(tmp_path, capsys):
    from webapp import cosmos_runs as cr
    st = build_stand(tmp_path)
    rc = cr.main(["init", "--data-dir", str(st["root"]), "--dry-run"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["state"] == "uninitialized"
    assert [x["sha12"] for x in out["would_create"]] == \
        [s[:12] for s in st["expected_order"]]
    assert out["skipped_dirs"] == 3 and out["skipped_inbox"]
    assert not (st["root"] / "cosmos-runs").exists()
    assert not (st["pr_root"] / st["shas"][0] / "holder.json").exists()
    rc = cr.main(["init", "--data-dir", str(st["root"])])
    assert rc == 0
    got = json.loads(capsys.readouterr().out)
    assert got["initialized"] is True and got["adoption"]["runs_created"] == 5
    assert (st["root"] / "cosmos-runs" / "storage.json").is_file()
    assert cr.main(["init", "--data-dir", str(st["root"])]) == 0
    assert json.loads(capsys.readouterr().out)["initialized"] is False


def test_repeat_receive_as_first_write_action_adopts_all_one_run_per_package(tmp_path):
    """Самый вероятный сценарий демонстрации на незаведённой площадке:
    «передача из CDPM в текущий прогон» повторной отправкой уже принятого
    пакета без кнопки «Оформить прогоны». Повтор — не первичный приём:
    исключения sha из оформления у него нет, и (а) повтор ТЕКУЩЕГО пакета
    даёт по прогону на каждый принятый пакет, текущий — держатель зеркала с
    origin adoption, датой первого приёма и receipts 2; (б) повтор более
    СТАРОГО пакета с нажатиями оставляет ему свой прогон и его попытки, а в
    текущий он ложится вторым держателем (runs/<run>)."""
    from webapp import cosmos_runs as cr, task_channel
    st = build_stand(tmp_path)
    accepted = len(st["expected_order"])
    cur_sha = st["shas"][1]                                # держатель зеркала
    body = (st["inbox"] / cur_sha / "package.zip").read_bytes()
    code, brief = task_channel.receive(body, cur_sha)
    assert code == 200 and brief["already_accepted"] is True
    assert brief["cosmos_run"] == "run-0001"
    runs = _runs(st["root"])
    assert len(runs) == accepted, "по одному прогону на принятый пакет"
    assert [r["packages"][0]["sha"] for r in runs.values()] == st["expected_order"]
    cur = cr.current_run()
    assert cur["run"] == "run-0001"
    assert cur["origin"] == "adoption" and cur["created_by"] == "adoption"
    want = datetime.fromisoformat(st["times"][cur_sha]).astimezone() \
        .isoformat(timespec="seconds")
    assert cur["created_at"] == want, "дата — первый принятый приём, не сегодня"
    pkg = cur["packages"][0]
    assert pkg["sha"] == cur_sha and pkg["received_at"] == want
    assert pkg["receipts"] == 2 and pkg["last_received_at"] > pkg["received_at"]
    assert pkg["workdir"] == "package-runs/" + cur_sha
    mirror = json.loads((st["inbox"] / "current.json").read_text(encoding="utf-8"))
    assert mirror["sha"] == cur_sha and mirror["repeat"] is True
    # (б) повтор более старого пакета с нажатиями A1, A2
    old_sha = st["shas"][0]
    body = (st["inbox"] / old_sha / "package.zip").read_bytes()
    code, brief2 = task_channel.receive(body, old_sha)
    assert code == 200 and brief2["cosmos_run"] == "run-0001"
    runs = _runs(st["root"])
    assert len(runs) == accepted
    own = next(r for r in runs.values() if r["packages"][0]["sha"] == old_sha)
    assert own["run"] == "run-0002" and own["origin"] == "adoption"
    assert own["created_at"].startswith("2026-09-05T12:00:00")
    cur = cr.read_run("run-0001")
    assert [p["sha"] for p in cur["packages"]] == [cur_sha, old_sha]
    assert cur["current_package"] == old_sha
    second = cur["packages"][1]
    assert second["workdir"] == "package-runs/%s/runs/run-0001" % old_sha
    assert second["receipts"] == 1
    # попытки старого пакета — в его прогоне, не в карточке чужого
    assert [a["id"] for a in cr.run_card("run-0002")["attempts"]] == ["A2", "A1"]
    assert cr.run_card("run-0001")["attempts"] == []
    holder = json.loads((st["pr_root"] / old_sha / "holder.json").read_text(encoding="utf-8"))
    assert holder["run"] == "run-0002"
    # первичный приём нового пакета — как прежде: в текущий прогон, без
    # второго прогона на тот же sha
    assert cr.ensure_storage("init")["adoption"]["runs_created"] == 0


def test_adoption_source_kind_from_mirror_and_receipts_not_channel_by_default(tmp_path):
    """Вид источника при оформлении: у пакета зеркала — из зеркала, у
    пакетов с новыми строками журнала приёмов (ключ source) — из строки,
    иначе умолчание «channel»; карточка пакета показывает честный вид, и
    PUT current не затирает его в зеркале (регресс репетиции 05.09)."""
    from webapp import cosmos_runs as cr, task_channel
    st = build_stand(tmp_path)
    inbox = st["inbox"]
    mirror = json.loads((inbox / "current.json").read_text(encoding="utf-8"))
    mirror["source"] = "upload"
    _wj(inbox / "current.json", mirror)
    rows = [json.loads(x) for x in (inbox / "receipts.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    for r in rows:
        if r["sha"] == st["shas"][2] and not r["repeat"]:
            r["source"] = "upload"
    (inbox / "receipts.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    cr.ensure_storage("init")
    by_sha = {r["packages"][0]["sha"]: r for r in _runs(st["root"]).values()}
    assert by_sha[st["shas"][1]]["packages"][0]["source"] == "upload"      # зеркало
    assert by_sha[st["shas"][2]]["packages"][0]["source"] == "upload"      # журнал
    assert by_sha[st["shas"][0]]["packages"][0]["source"] == "channel"     # умолчание
    assert task_channel.current_package()["source"]["kind"] == "upload"
    assert task_channel.card_state()["source"]["kind"] == "upload"
    cr.set_current("run-0001", "user:test")
    mirror2 = json.loads((inbox / "current.json").read_text(encoding="utf-8"))
    assert mirror2["sha"] == st["shas"][1] and mirror2["source"] == "upload"
    run3 = by_sha[st["shas"][2]]["run"]
    cr.set_current(run3, "user:test")
    mirror3 = json.loads((inbox / "current.json").read_text(encoding="utf-8"))
    assert mirror3["sha"] == st["shas"][2] and mirror3["source"] == "upload"
    lst = {x["run"]: x for x in cr.list_runs()["runs"]}
    assert lst[run3]["package"]["source"] == "upload"


def test_numbers_computed_not_hardcoded(tmp_path):
    from webapp import cosmos_runs as cr
    st = build_stand(tmp_path)
    legacy = cr.legacy_summary()
    accepted = [d for d in st["inbox"].iterdir()
                if d.is_dir() and (d / "verdict.json").is_file()
                and (d / "package.zip").is_file()]
    caches = [d for d in st["pr_root"].iterdir()
              if d.is_dir() and not (d / "state.json").is_file()]
    assert legacy == {"packages": len(accepted),
                      "current_sha12": st["shas"][1][:12],
                      "dirs_without_state": len(caches)}
    lst = cr.list_runs()
    assert lst["storage"] == {"state": "uninitialized", "legacy": legacy}
    got = cr.ensure_storage("init")["adoption"]
    file_dirs = [d for d in st["pr_root"].iterdir()
                 if (d / "state.json").is_file()
                 and not (st["inbox"] / d.name).is_dir()]
    assert got["runs_created"] == len(accepted) + len(file_dirs)
    assert got["skipped_dirs"] == len(caches)
    assert got["current_run"] == "run-0001"
    assert got["current_sha12"] == st["shas"][1][:12]
    assert cr.list_runs()["storage"]["legacy"] is None
    assert cr.legacy_summary() is None
