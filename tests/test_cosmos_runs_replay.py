# -*- coding: utf-8 -*-
"""Воспроизведение попытки нажатия (система прогонов COSMOS, проект
07.09.2026, раздел 17): журнал внешних вызовов попытки, «повтор из
журнала» с гейтом байт в байт, остановки, обрезка источника, «повтор
заново» со сравнением по итогам, отчёт, честные отказы кодами.

Все внешние стороны — подмены (FakeSolver, фальшивый построитель,
фальшивая модель): ни решателя, ни моделей; каталог данных каждого
теста — в tmp.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from tests.test_run_button import (  # noqa: E402
    FakeSolver, _fakes, _CATALOG_OK)
from tests.test_package_runs import _make_current, _pairing_kwargs  # noqa: E402

DRIVER = HERE / "tests" / "_replay_driver.py"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    for k in ("CREW_TASK_INBOX", "CREW_TASK_PACKAGE", "CREW_VMP_REGISTRY",
              "CREW_SOLVER_URL", "CREW_COSMOS_RUNS_DIR", "CREW_ATTEMPTS_KEEP",
              "CREW_CALL_JOURNAL", "CREW_LLM_EXCHANGES"):
        monkeypatch.delenv(k, raising=False)
    from webapp import flow, task_channel
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)
    try:
        from webapp import cosmos_runs as _cr
        _cr.forget()
    except Exception:                                      # noqa: BLE001
        pass
    from vmp import registry_check as _regc
    from vmp.run import button as btn
    root = Path(btn.__file__).resolve().parent.parent.parent
    counter = {"n": 0}

    def _fake_press_registry(run_dir, model, task):
        # created меняется каждым нажатием в той же форме, что у
        # настоящего вывода (ISO-время + слова): реестр обязан
        # сравниваться по содержанию без created, а строка Ш-1п2 журнала
        # («реестр от …») — нормализоваться по подстроке времени.
        counter["n"] += 1
        reg = {"created": "2026-09-07T10:00:%02d (авто-вывод из базы шаблонов)"
                          % (counter["n"] % 60),
               "scope": "кнопка: тест",
               "code_fingerprints": _regc.code_fingerprints(root),
               "rules": [{"rule_id": str(r["id"]), "verdict": "covered_full",
                          "title": r.get("title") or ""}
                         for r in (model or {}).get("rules") or []]}
        path = Path(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path
    monkeypatch.setattr(btn, "press_registry", _fake_press_registry)


# ------------------------------------------------------------ оснастка

def _catalog_builder(check: dict | None = None, catalog: dict | None = None,
                     payload_file: bool = True):
    """Фальшивый построитель, пишущий каталог, payload (как штатный build)
    и отчёт проверки — побочный файл."""
    def builder(payload, args, out):
        raw = json.dumps(catalog or _CATALOG_OK).encode("utf-8")
        if payload_file:
            (Path(out) / "builder-payload.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        (Path(out) / "builder-catalog.json").write_bytes(raw)
        (Path(out) / "pairing_builder_check_test.json").write_text(
            json.dumps(check if check is not None
                       else {"total_cost": 5, "person_nights": 1}),
            encoding="utf-8")
        return raw
    return builder


def _pairing_fakes():
    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)
    return fakes


def _press_pairing(solver=None, builder=None, fakes=None, **over):
    from webapp import package_runs as pr
    fakes = dict(fakes) if fakes is not None else _pairing_fakes()
    kw = dict(start_thread=False, sleep=lambda *_: None)
    kw.update(over)
    return pr.press("pairing", solver=solver or FakeSolver(),
                    pairing_builder=builder or _catalog_builder(), **fakes, **kw)


def _roster_fakes(with_llm: bool = False):
    fakes = _fakes(True)
    if with_llm:
        fakes.pop("certify_fn")
    return fakes


def fake_llm(prompt: str) -> str:
    """Детерминированная модель-компилятор: ответ с CRLF внутри."""
    return json.dumps({"checks": [], "skipped": [{"rule": "R-H", "reason": "стенд"}]},
                      ensure_ascii=False) + "\r\n"


def _press_rostering(solver=None, llm=None, fakes=None, **over):
    from webapp import package_runs as pr
    fakes = dict(fakes) if fakes is not None else _roster_fakes(with_llm=llm is not None)
    kw = dict(start_thread=False, sleep=lambda *_: None)
    kw.update(over)
    return pr.press("rostering", solver=solver or FakeSolver(), llm=llm,
                    **fakes, **kw)


def _replay(task: str, attempt: str, mode: str, fakes: dict, **kw):
    from webapp import package_runs as pr
    fakes = dict(fakes)
    fakes.pop("sleep", None)
    return pr.press(task, replay={"mode": mode, "run": None,
                                  "source_attempt": attempt},
                    start_thread=False, **fakes, **kw)


def _wd():
    """Рабочий каталог текущего пакета БЕЗ status() — тот зовёт
    certifier_identity, а тесты доступности считают её вызовы."""
    from webapp import package_runs as pr
    res = pr.resolve_current()
    if res is not None:
        return res.wd
    cur = pr.current()
    return pr.workdir_of(cur, cur["sha"])


def _last():
    from webapp import package_runs as pr
    return pr.status()["attempts"][0]


def _rec(aid: str) -> dict:
    from webapp import package_runs as pr
    return pr._attempt_record(pr.read_state(_wd()), aid)


def _report(aid: str) -> dict:
    from webapp import package_runs as pr
    p = _wd() / pr.ATTEMPTS_DIR / aid / "replay" / "report.json"
    assert p.is_file(), "отчёт повтора не записан: %s" % p
    return json.loads(p.read_text(encoding="utf-8"))


def _journal_dir(aid: str, out: str) -> Path:
    from webapp import package_runs as pr
    return _wd() / pr.ATTEMPTS_DIR / aid / out / "external-calls"


def _index(aid: str, out: str) -> list[dict]:
    from vmp.run import call_journal as cj
    return cj._load_index(_journal_dir(aid, out) / cj.INDEX_NAME)


def _avail(aid: str, deep: bool = False, **kw) -> dict:
    from webapp import package_runs as pr
    return pr.replay_availability(_wd(), _rec(aid), None, deep=deep, **kw)


class StopSolver(FakeSolver):
    """Решатель, отвергающий пакет на start — записанная остановка."""

    def start(self, *a, **k):
        from vmp.run import button as btn
        raise btn.ButtonStop("остановка: решатель отверг пакет", "стенд: 422 нет")


def _on_pairing_stub(monkeypatch, pairing_attempt: str, status_id: str = "r-каталог"):
    """Подмена проверки выбранного пейринга: сама проверка состава рейсов
    покрыта немодифицируемым стендом (test_h0_selected_pairing_gpt), здесь
    важен вызов решателя ДО занятия кнопки — он обязан попасть в журнал
    и обслуживаться из него при повторе."""
    from webapp import package_runs as pr

    def fake_validate(wd, package_path, package_sha, pairing_run_id, solver):
        remote = solver.status(status_id)
        assert isinstance(remote, dict)
        return {"pairing_run_id": pairing_run_id, "attempt_id": pairing_attempt,
                "catalog_sha256": "c" * 64, "chains": 1, "verified": True,
                "remote_state": remote.get("state")}
    monkeypatch.setattr(pr, "validate_pairing_selection", fake_validate)


# ------------------------------------------------------------ запись журнала

def test_record_journal_written_for_pairing_and_rostering_presses(tmp_path):
    """После двух обычных нажатий: index.jsonl в порядке вызовов, виды и
    методы, отпечатки и компоненты, тела как есть, sha от несжатого,
    manifest.complete, solver_methods без coverage у FakeSolver, снимок
    несёт external-calls/, запись попытки несёт journal.counts."""
    from vmp.run import call_journal as cj
    _make_current(tmp_path)
    st = _press_pairing()
    assert st["verdict"] == "пейринг построен", st
    p_rec = st["attempts"][0]
    rows = _index(p_rec["id"], "button-pairing")
    assert [r.get("kind") for r in rows] == ["solver", "pairing_builder", "solver", "end"]
    assert [r.get("method") for r in rows[:3]] == ["health", "build", "pairing_catalog"]
    assert [r["n"] for r in rows[:3]] == [1, 2, 3]
    assert rows[-1] == {**rows[-1], "kind": "end", "complete": True, "count": 3}
    jd = _journal_dir(p_rec["id"], "button-pairing")
    man = json.loads((jd / "manifest.json").read_text(encoding="utf-8"))
    assert man["schema"] == "cosmos-call-journal/v1" and man["complete"] is True
    assert man["mode"] == "record" and man["task"] == "pairing"
    assert "coverage" not in man["solver_methods"] and "start" in man["solver_methods"]
    assert man["counts"] == {"solver": 2, "pairing_builder": 1, "llm": 0,
                             "served_from_journal": 0, "unconsumed": 0}
    assert man["versions"]["solver"]["url"] == "http://fake:9098"
    assert man["versions"]["pairing_builder"]["select_engine"] == "cpsat"
    assert man["versions"]["panel"]["data_root"] == str(tmp_path / "data-root")
    # Тела как есть; sha256 — от несжатого тела.
    reg = rows[2]
    body = json.loads(cj.read_body(jd / reg["body"]).decode("utf-8"))
    assert body["response"] == {"runId": "r-каталог", "packageFingerprint": "fp-1"}
    assert cj.sha256_bytes(cj.read_body(jd / reg["body"])) == reg["body_sha256"]
    assert reg["input"]["fingerprint"] == cj.fingerprint_of(reg["input"]["components"])
    assert set(reg["input"]["components"]) == {"payload_sha256", "zip_sha256", "catalog_sha256"}
    assert (jd / reg["payload"]).is_file()
    build = rows[1]
    assert build["catalog_sha256"] == cj.sha256_bytes(json.dumps(_CATALOG_OK).encode("utf-8"))
    assert [f["name"] for f in build["files"]] == ["pairing_builder_check_test.json"]
    assert build["input"]["components"]["file_args_sha256"]["--source-rules"]
    assert any(a.startswith("<wd>/") for a in build["input"]["components"]["args_rel"])
    assert p_rec["journal"]["counts"]["solver"] == 2 and p_rec["journal"]["complete"] is True
    assert p_rec["journal"]["dir"] == "attempts/%s/button-pairing/external-calls" % p_rec["id"]
    # Ростеринг: health, start, status×k, result, applied, score_breakdown.
    st = _press_rostering(llm=fake_llm)
    assert st["error"] is None, st
    r_rec = st["attempts"][0]
    rows = _index(r_rec["id"], "button")
    methods = [r.get("method") or r.get("role") for r in rows if r.get("kind") != "end"]
    assert methods[:2] == ["health", "start"]
    assert methods.count("status") >= 1
    assert methods[-1] == "score_breakdown" and "certifier" in methods
    assert "result" in methods and "applied" in methods
    llm_rows = [r for r in rows if r.get("kind") == "llm"]
    assert llm_rows and llm_rows[0]["exchange_n"] == 1 and llm_rows[0]["reply_sha256"]
    assert r_rec["journal"]["counts"]["llm"] == len(llm_rows)
    assert r_rec["exchanges"]["count"] == len(llm_rows) and r_rec["exchanges"]["complete"]


def test_journal_wrapper_transparent_for_fakes(tmp_path):
    """Обёрнутый FakeSolver: нет coverage, base делегирован и очищен от
    userinfo, starts заполняются как прежде, ButtonStop из start
    записывается и пробрасывается; стенд без base оборачивается;
    неизвестный вызываемый атрибут журналируется под своим именем."""
    from vmp.run import button as btn
    from vmp.run import call_journal as cj
    journal = cj.CallJournal("a1", "rostering", out_name="button")
    fake = FakeSolver()
    fake.base = "http://user:secret@fake:9098"
    w = cj.wrap_solver(fake, journal)
    assert not hasattr(w, "coverage") and getattr(w, "coverage", None) is None
    assert w.base == "http://fake:9098"
    assert journal.solver_url == "http://fake:9098"
    w.start({"employees": []}, "", "feasibility-hard", None, 60, "rostering", None)
    assert fake.starts and fake.starts[0]["mode"] == "feasibility-hard"
    assert w.status("r-77")["state"] in ("считает", "готов")
    stop = StopSolver()
    ws = cj.wrap_solver(stop, journal)
    with pytest.raises(btn.ButtonStop):
        ws.start({}, "", None, None, 60, "rostering", None)
    journal.open(tmp_path / "ec")
    journal.close(complete=True)
    rows = cj._load_index(tmp_path / "ec" / "index.jsonl")
    errs = [r for r in rows if r.get("kind") == "solver" and not r.get("ok")]
    assert errs and errs[0]["error"]["verdict"] == "остановка: решатель отверг пакет"
    assert errs[0]["error"]["detail"] == "стенд: 422 нет"
    assert "start" in journal.solver_methods and "coverage" not in journal.solver_methods

    class Solver:                       # стенд без base (test_h0_selected_pairing_gpt)
        def status(self, run_id):
            return {"runId": run_id, "state": "готов"}

        def custom(self, x, y=1):
            return {"x": x, "y": y}
    j2 = cj.CallJournal("a2", "rostering", out_name="button")
    w2 = cj.wrap_solver(Solver(), j2)
    assert getattr(w2, "base", "<служба>") == "<служба>"
    assert w2.status("chosen")["state"] == "готов"
    assert w2.custom(3, y=4) == {"x": 3, "y": 4}
    assert j2.solver_url is None and j2.versions["solver"]["url"] is None
    j2.open(tmp_path / "ec2")
    j2.close(True)
    rows = cj._load_index(tmp_path / "ec2" / "index.jsonl")
    assert [r.get("method") for r in rows[:2]] == ["status", "custom"]
    assert rows[1]["input"]["components"] == {"args": [3], "kwargs": {"y": 4}}
    assert "custom" in j2.solver_methods


# ------------------------------------------------------------ повтор из журнала

def test_replay_journal_bytes_equal_gate_pairing(tmp_path, monkeypatch):
    """Гейт: повтор из журнала синтетической попытки пейринга без solver/
    pairing_builder/llm и без CREW_SOLVER_URL — same, файлы списка
    нормализации равны после нормализации, прочие байт в байт; журнал
    повтора — ссылки served_from без тел."""
    from webapp import package_runs as pr
    _make_current(tmp_path)
    src = _press_pairing()["attempts"][0]
    time.sleep(1.1)                   # другая секунда — метки времени разойдутся
    out = _replay("pairing", src["id"], "journal", _pairing_fakes())
    assert out["done"] is True and out["mode"] == "journal"
    assert out["source_attempt"] == src["id"] and out["attempt_id"] != src["id"]
    rec = _last()
    assert rec["id"] == out["attempt_id"] and rec["error"] is None
    assert rec["verdict"] == "пейринг построен"
    assert rec["params"] == src["params"] and rec["replay"]["of"]["mode"] == "journal"
    assert rec["replay"]["of"]["source_attempt"] == src["id"]
    summary = rec["replay"]["of"]["report"]
    assert summary["verdict"] == "same", summary
    report = _report(rec["id"])
    assert report["files"]["different"] == [], report["files"]
    assert report["files"]["compared"] >= 8
    normalized = set(report["files"]["normalized_same"])
    assert "registry/vmp-executors-registry.json" in normalized
    assert "button-pairing/log.json" in normalized and "button-pairing/delivery.json" in normalized
    assert report["journal"]["served"] == report["journal"]["recorded"] == 3
    assert report["journal"]["unconsumed"] == 0 and report["environment"]["differs"] == []
    assert "совпадает" in report["words"]
    rows = _index(rec["id"], "button-pairing")
    calls = [r for r in rows if r.get("kind") != "end"]
    assert all(r.get("served_from", {}).get("attempt") == src["id"] for r in calls)
    assert all(r.get("body") is None or not (_journal_dir(rec["id"], "button-pairing") / r["body"]).exists()
               for r in calls if r.get("kind") == "solver")
    man = json.loads((_journal_dir(rec["id"], "button-pairing") / "manifest.json")
                     .read_text(encoding="utf-8"))
    assert man["mode"] == "replay-journal" and man["served_from"]["attempt"] == src["id"]
    assert man["counts"]["served_from_journal"] == 3 and man["counts"]["unconsumed"] == 0
    # Побочное правило: повтор из журнала не кандидат пейринга.
    cands = pr._pairing_candidates(_wd(), pr.read_state(_wd()))
    assert [c["attempt_id"] for c in cands] == [src["id"]]
    # Нажатие «заново» у повтора доступно, «из журнала» — нет.
    av = _avail(rec["id"], solver_given=True)
    assert av["journal"]["reason_code"] == "replay-of-replay"
    assert src["id"] in av["journal"]["reason_words"] and av["fresh"]["available"]


def test_replay_journal_bytes_equal_gate_rostering_on_pairing_with_model_exchanges(
        tmp_path, monkeypatch):
    """Ростеринг на пейринге: solver.status до занятия — из журнала;
    обмены с моделью байт в байт, index.jsonl — после нормализации."""
    from webapp import package_runs as pr
    _make_current(tmp_path)
    pairing = _press_pairing()["attempts"][0]
    _on_pairing_stub(monkeypatch, pairing["id"])
    solver = FakeSolver(status_chains=1)
    st = _press_rostering(solver=solver, llm=fake_llm, pairing_run_id="r-каталог")
    assert st["error"] is None, st
    src = st["attempts"][0]
    assert src["catalog"].endswith("button-on-pairing")
    rows = _index(src["id"], "button-on-pairing")
    assert rows[0]["method"] == "health" or rows[0]["method"] == "status"
    assert any(r.get("method") == "status" and r["input"]["components"] == {"run_id": "r-каталог"}
               for r in rows), "вызов до занятия не попал в журнал"
    time.sleep(1.1)
    out = _replay("rostering", src["id"], "journal", _roster_fakes(with_llm=True))
    rec = _last()
    assert rec["error"] is None and rec["verdict"] == src["verdict"]
    report = _report(rec["id"])
    assert report["verdict"] == "same", report["files"]["different"]
    assert "button-on-pairing/llm-exchanges/index.jsonl" in report["files"]["normalized_same"]
    xa = _wd() / pr.ATTEMPTS_DIR / src["id"] / "button-on-pairing" / "llm-exchanges"
    xb = _wd() / pr.ATTEMPTS_DIR / rec["id"] / "button-on-pairing" / "llm-exchanges"
    for name in ("001-certifier.request.txt", "001-certifier.reply.txt"):
        assert (xa / name).read_bytes() == (xb / name).read_bytes()
    assert b"\r\n" in (xb / "001-certifier.reply.txt").read_bytes()
    assert report["journal"]["served"] == report["journal"]["recorded"]
    assert rec["exchanges"]["count"] == src["exchanges"]["count"]
    # После повтора пейринга кандидаты не дублируют прогон.
    _replay("pairing", pairing["id"], "journal", _pairing_fakes())
    cands = pr._pairing_candidates(_wd(), pr.read_state(_wd()))
    assert [c["pairing_run_id"] for c in cands] == ["r-каталог"]


def test_exchange_bodies_are_bytes_crlf_safe_and_verify_complete(tmp_path, monkeypatch):
    """Ответы модели с \\n и с \\r\\n — файлы несут ровно эти байты;
    verify_journal(deep=True) полон; JournalLLM возвращает строку байт в
    байт; тело журнала > 64 КБ уходит в gzip с sha от несжатого."""
    from vmp.run import call_journal as cj
    from vmp.run import llm_exchanges as lx
    replies = ['{"checks": [], "skipped": []}\n', '{"checks": [], "skipped": []}\r\n']
    seen = []

    def llm(prompt):
        seen.append(prompt)
        return replies[len(seen) - 1]
    journal = cj.CallJournal("a1", "rostering", out_name="button")
    journal.open(tmp_path / "ec")
    rec = lx.wrap(llm, role="certifier", out=tmp_path / "x", journal=journal)
    assert rec("первый") == replies[0] and rec("второй") == replies[1]
    rec.close(True)
    journal.close(True)
    assert (tmp_path / "x" / "001-certifier.reply.txt").read_bytes() == replies[0].encode("utf-8")
    assert (tmp_path / "x" / "002-certifier.reply.txt").read_bytes() == replies[1].encode("utf-8")
    assert (tmp_path / "x" / "001-certifier.request.txt").read_bytes() == "первый".encode("utf-8")
    record = {"journal": journal.record_summary("x")}
    v = cj.verify_journal(tmp_path / "ec", record, deep=True, exchanges_dir=tmp_path / "x")
    assert v["complete"] is True, v
    rp = cj.JournalReplayer(tmp_path / "ec", tmp_path / "x", source_attempt="a1")
    jl = cj.JournalLLM(rp)
    assert jl("первый") == replies[0] and jl("второй") == replies[1]
    with pytest.raises(cj.ReplayMismatch):
        jl("третий")
    # Большое тело — gzip; sha и чтение — от несжатого.
    j2 = cj.CallJournal("a2", "rostering", out_name="button")
    j2.open(tmp_path / "ec2")
    big = {"blob": "x" * (70 * 1024)}
    fake = FakeSolver()
    fake.result = lambda run_id: big
    w = cj.wrap_solver(fake, j2)
    assert w.result("r-1") == big
    j2.close(True)
    rows = cj._load_index(tmp_path / "ec2" / "index.jsonl")
    row = rows[0]
    assert row["body"].endswith(".json.gz") and row["body_encoding"] == "gzip"
    raw = cj.read_body(tmp_path / "ec2" / row["body"])
    assert cj.sha256_bytes(raw) == row["body_sha256"]
    assert json.loads(raw.decode("utf-8"))["response"] == big
    v2 = cj.verify_journal(tmp_path / "ec2", {"journal": j2.record_summary("x")}, deep=True)
    assert v2["complete"] is True, v2


def test_replay_journal_reproduces_recorded_solver_stop(tmp_path):
    _make_current(tmp_path)
    st = _press_rostering(solver=StopSolver())
    src = st["attempts"][0]
    assert src["verdict"] == "остановка: решатель отверг пакет", st
    out = _replay("rostering", src["id"], "journal", _roster_fakes())
    rec = _last()
    assert rec["verdict"] == "остановка: решатель отверг пакет" and rec["error"] is None
    report = _report(rec["id"])
    assert report["verdict"] == "same", report["files"]["different"]


def test_replay_journal_reproduces_clock_timeout_stop(tmp_path):
    """Источник остановлен циклом опроса по часам панели; повтор без
    часов поднимает ту же остановку при исчерпании очереди status."""
    from vmp.run import call_journal as cj
    _make_current(tmp_path)
    st = _press_rostering(solver=FakeSolver(states=("считает",)), timeout_s=-1)
    src = st["attempts"][0]
    assert src["verdict"] == cj.CLOCK_TIMEOUT_VERDICT, st
    man = json.loads((_journal_dir(src["id"], "button") / "manifest.json")
                     .read_text(encoding="utf-8"))
    assert man["terminal_stop"]["verdict"] == cj.CLOCK_TIMEOUT_VERDICT
    assert man["terminal_stop"]["extra"]["solver_run_id"] == "r-77"
    fakes = _roster_fakes()
    fakes["timeout_s"] = 3600           # перекрывается режимом journal
    _replay("rostering", src["id"], "journal", fakes)
    rec = _last()
    assert rec["verdict"] == cj.CLOCK_TIMEOUT_VERDICT and rec["error"] is None
    report = _report(rec["id"])
    assert report["verdict"] == "same", report["files"]["different"]
    assert report["journal"]["served"] == report["journal"]["recorded"]


def test_replay_journal_refuses_incomplete_journal_before_start(tmp_path):
    """complete: false в записи — карточка (deep=False) видит; удалённое
    тело — только POST (deep=True); новых попыток и файлов нет."""
    from vmp.run import call_journal as cj
    from webapp import package_runs as pr
    _make_current(tmp_path)
    a = _press_pairing()["attempts"][0]
    b = _press_pairing()["attempts"][0]

    def _edit(aid, fn):
        def apply(st):
            fn(pr._attempt_record(st, aid))
        pr._mutate_state(_wd(), apply)
    _edit(a["id"], lambda r: r["journal"].update(complete=False, degraded="стенд: диск"))
    av = _avail(a["id"])
    assert av["journal"]["reason_code"] == "journal-incomplete", av
    assert "стенд: диск" in av["journal"]["reason_words"]
    rows = _index(b["id"], "button-pairing")
    body = _journal_dir(b["id"], "button-pairing") / rows[2]["body"]
    body.unlink()
    assert _avail(b["id"])["journal"]["available"] is True     # легко — не видно
    deep = _avail(b["id"], deep=True)
    assert deep["journal"]["reason_code"] == "journal-incomplete"
    assert "№3" in deep["journal"]["reason_words"]
    before = sorted(p.name for p in (_wd() / pr.ATTEMPTS_DIR).iterdir())
    n_before = len(pr.read_state(_wd())["attempts"])
    for aid in (a["id"], b["id"]):
        with pytest.raises(pr.PackageRunError) as e:
            _replay("pairing", aid, "journal", _pairing_fakes())
        assert e.value.reason_code == "journal-incomplete"
    assert sorted(p.name for p in (_wd() / pr.ATTEMPTS_DIR).iterdir()) == before
    assert len(pr.read_state(_wd())["attempts"]) == n_before
    assert pr.status()["done"] is True


def test_replay_journal_unavailable_for_pre_journal_disabled_by_site_and_replay_of_replay(
        tmp_path, monkeypatch):
    from webapp import package_runs as pr
    _make_current(tmp_path)
    a = _press_pairing()["attempts"][0]

    def strip(st):
        pr._attempt_record(st, a["id"]).pop("journal")
    pr._mutate_state(_wd(), strip)
    av = _avail(a["id"], solver_given=True)
    assert av["journal"]["reason_code"] == "pre-journal" and av["fresh"]["available"]
    monkeypatch.setenv("CREW_CALL_JOURNAL", "0")
    b = _press_pairing()["attempts"][0]
    assert b["journal"] == {"present": False, "reason_code": "disabled-by-site"}
    assert not _journal_dir(b["id"], "button-pairing").exists()
    av = _avail(b["id"], solver_given=True)
    assert av["journal"]["reason_code"] == "disabled-by-site" and av["fresh"]["available"]
    monkeypatch.delenv("CREW_CALL_JOURNAL")
    c = _press_pairing()["attempts"][0]
    _replay("pairing", c["id"], "journal", _pairing_fakes())
    r = _last()
    av = _avail(r["id"], solver_given=True)
    assert av["journal"]["reason_code"] == "replay-of-replay" and av["fresh"]["available"]
    _replay("pairing", c["id"], "fresh", _pairing_fakes(), solver=FakeSolver(),
            pairing_builder=_catalog_builder(), sleep=lambda *_: None)
    f = _last()
    assert f["replay"]["of"]["mode"] == "fresh"
    av = _avail(f["id"], solver_given=True)
    assert av["journal"]["available"] and av["fresh"]["available"], av


def test_replay_refuses_when_source_would_be_trimmed_and_passes_with_keep_zero(
        tmp_path, monkeypatch):
    from webapp import package_runs as pr
    _make_current(tmp_path)
    monkeypatch.setenv("CREW_ATTEMPTS_KEEP", "2")
    a1 = _press_pairing()["attempts"][0]
    time.sleep(1.05)
    a2 = _press_pairing()["attempts"][0]
    av = _avail(a1["id"])
    assert av["journal"]["reason_code"] == "source-would-be-trimmed"
    assert "CREW_ATTEMPTS_KEEP=2" in av["journal"]["reason_words"]
    with pytest.raises(pr.PackageRunError) as e:
        _replay("pairing", a1["id"], "journal", _pairing_fakes())
    assert e.value.reason_code == "source-would-be-trimmed"
    ids = [r["id"] for r in pr.read_state(_wd())["attempts"]]
    assert ids == [a1["id"], a2["id"]]
    assert sorted(p.name for p in (_wd() / pr.ATTEMPTS_DIR).iterdir()) == sorted(ids)
    # Источник цел в записях, но самый старый среди каталогов.
    def reorder(st):
        st["attempts"] = [pr._attempt_record(st, a2["id"]), pr._attempt_record(st, a1["id"])]
    pr._mutate_state(_wd(), reorder)
    assert _avail(a2["id"])["journal"]["reason_code"] == "source-would-be-trimmed"
    pr._mutate_state(_wd(), lambda st: st.update(attempts=list(reversed(st["attempts"]))))
    # Повтор последней проходит и обрезает не источник.
    time.sleep(1.05)
    _replay("pairing", a2["id"], "journal", _pairing_fakes())
    r = _last()
    ids = [x["id"] for x in pr.read_state(_wd())["attempts"]]
    assert ids == [a2["id"], r["id"]] and pr.read_state(_wd())["attempts_trimmed"] == 1
    assert not (_wd() / pr.ATTEMPTS_DIR / a1["id"]).exists()
    assert (_wd() / pr.ATTEMPTS_DIR / a2["id"]).is_dir()
    assert r["replay"]["of"]["report"]["verdict"] == "same"
    monkeypatch.setenv("CREW_ATTEMPTS_KEEP", "0")
    assert _avail(a2["id"])["journal"]["available"]
    time.sleep(1.05)
    _replay("pairing", a2["id"], "journal", _pairing_fakes())
    assert len(pr.read_state(_wd())["attempts"]) == 3


def test_replay_journal_mismatch_names_component_and_first_difference(tmp_path, monkeypatch):
    """Подмена детерминированной части → ReplayMismatch в первом внешнем
    вызове с телом; выдача-остановка, differs, components_differ,
    первое различие — builder-payload.json с путём JSON; рассинхрон до
    занятия — 409 journal-mismatch без попытки."""
    from webapp import package_runs as pr
    _make_current(tmp_path)
    src = _press_pairing()["attempts"][0]
    fakes = _pairing_fakes()
    fakes["to_payload"] = lambda pkg: {
        "employees": [{"id": "E1", "base": "SVG"}],
        "flights": [{"departureUTCDateTime": "2026-09-01T09:00:00Z",
                     "arrivalUTCDateTime": "2026-09-02T10:00:00Z"}]}
    n_before = len(pr.read_state(_wd())["attempts"])
    _replay("pairing", src["id"], "journal", fakes)
    rec = _last()
    assert rec["done"] and rec["error"] is None
    assert rec["verdict"].startswith("остановка: повтор из журнала"), rec["verdict"]
    assert rec["journal"]["mismatch"]["components_differ"] == ["payload_sha256"]
    assert rec["journal"]["mismatch"]["method"] == "build"
    report = _report(rec["id"])
    assert report["verdict"] == "differs"
    fd = report["first_difference"]
    assert fd["file"] == "button-pairing/builder-payload.json", fd
    assert fd["path"] == "$.flights[0].departureUTCDateTime"
    assert report["journal"]["mismatch"]["components_differ"] == ["payload_sha256"]
    assert "$.flights[0].departureUTCDateTime" in report["journal"]["mismatch"]["words"]
    assert report["journal"]["unconsumed"] >= 1
    assert len(pr.read_state(_wd())["attempts"]) == n_before + 1
    # Рассинхрон до занятия: проверка выбранного пейринга спрашивает у
    # решателя другой прогон — отказ 409 с кодом, попытки нет.
    _on_pairing_stub(monkeypatch, src["id"], status_id="r-каталог")
    st = _press_rostering(solver=FakeSolver(status_chains=1), pairing_run_id="r-каталог")
    rsrc = st["attempts"][0]
    _on_pairing_stub(monkeypatch, src["id"], status_id="r-другой")
    n_before = len(pr.read_state(_wd())["attempts"])
    with pytest.raises(pr.PackageRunError) as e:
        _replay("rostering", rsrc["id"], "journal", _roster_fakes())
    assert e.value.reason_code == "journal-mismatch", str(e.value)
    assert "r-другой" in str(e.value) or "status" in str(e.value)
    assert len(pr.read_state(_wd())["attempts"]) == n_before
    assert pr.status()["done"] is True


def test_replay_journal_uses_snapshot_package_when_inbox_bytes_missing(tmp_path):
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    src = _press_pairing()["attempts"][0]
    wd = _wd()
    inbox_pkg = Path(pr.current()["path"])
    inbox_pkg.unlink()

    def avail():
        return pr.replay_availability(wd, pr._attempt_record(pr.read_state(wd), src["id"]), None)
    assert avail()["journal"]["available"], avail()
    out = _replay("pairing", src["id"], "journal", _pairing_fakes())
    rec = pr._attempt_record(pr.read_state(wd), out["attempt_id"])
    assert rec["error"] is None and rec["replay"]["of"]["report"]["verdict"] == "same"
    snap = wd / pr.ATTEMPTS_DIR / src["id"] / "button-pairing" / "package.zip"
    snap.write_bytes("PK-подмена".encode("utf-8"))
    # Лёгкая проверка карточки (deep=False) байты пакета не хеширует —
    # файл есть, кнопка доступна (17.2: запись, два stat, хвост индекса);
    # подменённые байты ловят полная проверка (deep=True) и сам запуск.
    assert avail()["journal"]["available"] is True
    deep = pr.replay_availability(wd, pr._attempt_record(pr.read_state(wd), src["id"]),
                                  None, deep=True)
    assert deep["journal"]["reason_code"] == "package-bytes-missing"
    assert sha[:12] in deep["journal"]["reason_words"]
    with pytest.raises(pr.PackageRunError) as e:
        _replay("pairing", src["id"], "journal", _pairing_fakes())
    assert e.value.reason_code == "package-bytes-missing"


def test_replay_journal_across_python_hash_seeds(tmp_path):
    """Гейт межпроцессного детерминизма: запись и повтор в разных
    процессах с разными PYTHONHASHSEED на одном каталоге данных."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("CREW_TASK_INBOX", "CREW_TASK_PACKAGE", "CREW_VMP_REGISTRY",
                        "CREW_SOLVER_URL", "CREW_COSMOS_RUNS_DIR", "CREW_ATTEMPTS_KEEP",
                        "CREW_CALL_JOURNAL", "CREW_LLM_EXCHANGES", "CREW_TEMPLATE_CATALOG")}
    env.update(PYTHONUTF8="1", CREW_DATA_DIR=str(tmp_path / "data-root"),
               REPLAY_DRIVER_TMP=str(tmp_path))

    def run(args, seed):
        p = subprocess.run([sys.executable, str(DRIVER), *args],
                           env=dict(env, PYTHONHASHSEED=seed), cwd=str(HERE),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        assert p.returncode == 0, (p.stdout[-2000:], p.stderr[-4000:])
        return json.loads(p.stdout.strip().splitlines()[-1])
    rec = run(["record"], "1")
    assert rec["verdict"] == "пейринг построен", rec
    rep = run(["replay", rec["attempt_id"]], "2")
    assert rep["error"] is None and rep["verdict"] == "пейринг построен", rep
    assert rep["report"]["verdict"] == "same", rep["report"]


def test_certificate_header_equals_attempt_record_certifier(tmp_path):
    """Шапка сертификата = attempts[].certifier буквально — у обычной
    попытки и у повтора (одна переменная сертификатора)."""
    from webapp import package_runs as pr
    _make_current(tmp_path)
    st = _press_rostering(llm=fake_llm)
    src = st["attempts"][0]
    cert = json.loads((_wd() / pr.ATTEMPTS_DIR / src["id"] / "button" / "certificate.json")
                      .read_text(encoding="utf-8"))
    assert cert["certifier"] == src["certifier"]
    assert src["certifier"]["provider"] == "ollama" and src["certifier"]["note_code"] == "ollama-default"
    _replay("rostering", src["id"], "journal", _roster_fakes(with_llm=True))
    rec = _last()
    cert2 = json.loads((_wd() / pr.ATTEMPTS_DIR / rec["id"] / "button" / "certificate.json")
                       .read_text(encoding="utf-8"))
    assert cert2["certifier"] == rec["certifier"] == src["certifier"]
    assert rec["replay"]["of"]["report"]["verdict"] == "same"


# ------------------------------------------------------------ повтор заново

class OtherRunSolver(FakeSolver):
    def start(self, *a, **k):
        got = super().start(*a, **k)
        got["runId"] = "r-88"
        return got

    def status(self, run_id):
        out = super().status(run_id)
        if out.get("state") == "готов":
            out["score"] = "0hard/-9soft"
        return out


def test_replay_fresh_creates_attempt_with_source_params_and_outcome_table(tmp_path):
    from vmp.run import replay_compare as rcmp
    _make_current(tmp_path)
    st = _press_rostering(spent_limit_s=45, moves="chain-seed")
    src = st["attempts"][0]
    assert src["params"]["spent_limit_s"] == 45 and src["params"]["moves"] == "chain-seed"
    out = _replay("rostering", src["id"], "fresh", _roster_fakes(),
                  solver=OtherRunSolver(), sleep=lambda *_: None)
    assert out["mode"] == "fresh"
    rec = _last()
    assert rec["params"] == src["params"] and rec["error"] is None
    assert rec["replay"]["of"]["mode"] == "fresh"
    report = _report(rec["id"])
    rows = {r["name"]: r for r in report["outcomes"]}
    assert rows["verdict"]["same"] is True
    assert rows["score.soft"]["same"] is False and rows["score.soft"]["replay"] == -9
    assert rows["solver_run_id"]["legit_difference"] is True
    assert rows["solver_run_id"]["source"] == "r-77" and rows["solver_run_id"]["replay"] == "r-88"
    assert rows["idak.counts"]["note_code"] == "not-run-for-replay"
    assert report["verdict"] == "differs" and "score.soft" in report["words"]
    assert rec["replay"]["of"]["report"]["outcomes_differ"] == ["score.soft"]
    assert report["reproducibility"]["explanation_code"] == "wall-clock-legit-difference"
    assert report["reproducibility"]["deterministic_gate"] is None
    assert rcmp.summary_of(report)["verdict"] == "differs"
    # Пейринг: строки названы файлами-источниками; отсутствие файла —
    # artifact-missing, не расхождение; при Д1 — гейт байт в байт.
    p = _press_pairing(builder=_catalog_builder(check=None),
                       deterministic={"seed": 3, "step_limit": 10})["attempts"][0]
    assert p["reproducibility"]["class"] == "deterministic"
    assert p["params"]["deterministic"] == {"seed": 3, "step_limit": 10}

    def no_check(payload, args, out):
        raw = json.dumps(_CATALOG_OK).encode("utf-8")
        (Path(out) / "builder-catalog.json").write_bytes(raw)
        return raw
    _replay("pairing", p["id"], "fresh", _pairing_fakes(), solver=FakeSolver(),
            pairing_builder=no_check, sleep=lambda *_: None)
    rec = _last()
    report = _report(rec["id"])
    rows = {r["name"]: r for r in report["outcomes"]}
    assert rows["total_cost"]["note_code"] == "artifact-missing" and rows["total_cost"]["same"] is None
    assert rows["total_cost"]["source_file"] == "pairing_builder_check_*.json"
    assert rows["selected"]["source_file"] == "builder-catalog.json" and rows["selected"]["same"]
    assert rows["chains"]["same"] is True
    assert report["reproducibility"]["explanation_code"] == "deterministic-expected-same"
    assert report["reproducibility"]["deterministic_gate"]["same"] is True
    assert report["verdict"] == "same-outcome"


def test_replay_fresh_roster_diff_by_employee_exact_numbers(tmp_path):
    from vmp.run import replay_compare as rcmp
    _make_current(tmp_path)
    st = _press_rostering()
    src = st["attempts"][0]
    fakes = _roster_fakes()

    def build_input(pkg, payload, solved, applied, name):
        sol = {"solution": "тестовый ростер",
               "assignments": [
                   {"employee": "E1", "flight": "PL101", "role": "CP",
                    "dep_time": "2026-01-15T06:00", "arr_time": "2026-01-15T08:00",
                    "dep_airport": "PLY", "arr_airport": "SVG"},
                   {"employee": "E2", "flight": "PL102", "role": "FO",
                    "dep_time": "2026-01-16T04:00", "arr_time": "2026-01-16T06:00",
                    "dep_airport": "SVG", "arr_airport": "PLY"}],
               "duties": [{"employee": "E1", "start": "2026-01-15T06:00",
                           "end": "2026-01-15T14:00"}],
               "window_employees": [{"employee": "E1"}, {"employee": "E2"},
                                    {"employee": "E3"}]}
        return sol, {"дома": "12:00"}
    fakes["build_input"] = build_input
    _replay("rostering", src["id"], "fresh", fakes, solver=FakeSolver(),
            sleep=lambda *_: None)
    rec = _last()
    report = _report(rec["id"])
    rd = report["roster_diff"]
    # Источник: E1 → {PL101, PL102} без роли; повтор: E1 → {(PL101, CP)},
    # E2 → {(PL102, FO)}, E3 — в окне без назначений.
    assert rd["employees_total"] == 3
    assert rd["unchanged"] == 1 and rd["changed"] == 2
    assert rd["lost"] == 2 and rd["gained"] == 2
    assert rd["only_in_replay"] == 1 and rd["only_in_source"] == 0
    by = {e["employee"]: e for e in rd["employees"]}
    assert by["E1"]["status"] == "changed" and by["E1"]["lost"] == 2 and by["E1"]["gained"] == 1
    assert by["E2"]["status"] == "only-in-replay" and by["E2"]["gained"] == 1
    assert by["E3"]["status"] == "unchanged"
    assert rd["words"].startswith("сотрудников 3: без изменений 1, изменилось 2 "
                                  "(потеряно 2 рейсо-мест, добавлено 2)")
    # pairing_diff: общие / только у источника / только у повтора.
    p = _press_pairing()["attempts"][0]
    other = {"selected": [{"fns": ["G-1"]}, {"fns": ["G-2", "G-3"]}],
             "coverage": {"verdict": "OK"}}
    _replay("pairing", p["id"], "fresh", _pairing_fakes(), solver=FakeSolver(),
            pairing_builder=_catalog_builder(catalog=other, check={"total_cost": 9}),
            sleep=lambda *_: None)
    report = _report(_last()["id"])
    pdf = report["pairing_diff"]
    assert pdf["common"] == 1 and pdf["only_in_source"] == 0 and pdf["only_in_replay"] == 1
    assert pdf["only_in_replay_list"] == [["G-2", "G-3"]]
    assert pdf["total_cost"] == {"source": 5, "replay": 9, "delta": 4}
    assert rcmp.summary_of(report)["outcomes_differ"]


def test_replay_environment_differs_named_and_registry_compared_without_created(
        tmp_path, monkeypatch):
    from vmp.run import call_journal as cj
    from webapp import package_runs as pr
    _make_current(tmp_path)
    a = _press_pairing()["attempts"][0]
    b = _press_pairing()["attempts"][0]
    ra = json.loads((_wd() / pr.ATTEMPTS_DIR / a["id"] / "registry" / "vmp-executors-registry.json")
                    .read_text(encoding="utf-8"))
    rb = json.loads((_wd() / pr.ATTEMPTS_DIR / b["id"] / "registry" / "vmp-executors-registry.json")
                    .read_text(encoding="utf-8"))
    assert ra["created"] != rb["created"] and a["registry"]["sha256"] == b["registry"]["sha256"]
    _replay("pairing", a["id"], "fresh", _pairing_fakes(), solver=FakeSolver(),
            pairing_builder=_catalog_builder(), sleep=lambda *_: None)
    report = _report(_last()["id"])
    assert report["environment"]["differs"] == [], report["environment"]
    monkeypatch.setattr(pr, "_code_identity",
                        lambda: {"commit": "d1e2f3a" * 5, "commit_short": "d1e2f3a",
                                 "tree_dirty": False, "dirty_paths": 0})
    monkeypatch.setattr(pr, "certifier_identity",
                        lambda scope: {"scope": scope, "backend": "local",
                                       "channel": "local", "model": "другая-модель"})
    monkeypatch.setattr(cj, "panel_versions",
                        lambda: {"data_root": "D:/другой-каталог", "platform": "x",
                                 "python": "0.0"})
    _replay("pairing", a["id"], "fresh", _pairing_fakes(), solver=FakeSolver(),
            pairing_builder=_catalog_builder(), sleep=lambda *_: None)
    rec = _last()
    report = _report(rec["id"])
    got = set(report["environment"]["differs"])
    assert {"code.commit", "certifier.model", "panel.data_root"} <= got, got
    assert set(rec["replay"]["of"]["report"]["environment_differs"]) == got


def test_replay_fresh_unavailable_when_params_not_recorded_names_missing_keys(
        tmp_path, monkeypatch):
    from webapp import package_runs as pr
    _make_current(tmp_path)
    a = _press_pairing()["attempts"][0]

    def strip(st):
        r = pr._attempt_record(st, a["id"])
        r["params"] = {"mode": None}
    pr._mutate_state(_wd(), strip)
    av = _avail(a["id"])
    assert av["fresh"]["reason_code"] == av["journal"]["reason_code"] == "params-not-recorded"
    assert "spent_limit_s" in av["fresh"]["reason_words"] and "moves" in av["fresh"]["reason_words"]
    b = _press_pairing()["attempts"][0]
    av = _avail(b["id"])
    assert av["fresh"]["reason_code"] == "solver-url-missing" and av["journal"]["available"]
    monkeypatch.setenv("CREW_SOLVER_URL", "http://127.0.0.1:1")
    assert _avail(b["id"])["fresh"]["available"]
    calls = []
    monkeypatch.setattr(pr, "certifier_identity",
                        lambda scope: calls.append(scope) or
                        {"scope": scope, "backend": "local", "channel": "local",
                         "model": None, "error": "локальная модель не найдена"})
    assert _avail(b["id"])["fresh"]["available"] and calls == []
    deep = _avail(b["id"], deep=True)
    assert deep["fresh"]["reason_code"] == "certifier-unavailable"
    assert "локальная модель не найдена" in deep["fresh"]["reason_words"]
    assert calls == ["local-only"]


def test_card_availability_reads_no_bodies_and_no_certifier_probe(tmp_path, monkeypatch):
    """Лёгкая проверка карточки (deep=False): ни тел журнала, ни зонда
    сертификатора, ни хеша байтов пакета (карточка опрашивается раз в 5 с
    на каждую попытку); отпечаток пакета сверяется под запуском
    (deep=True)."""
    from vmp.run import call_journal as cj
    from webapp import package_runs as pr
    _make_current(tmp_path)
    ids = [_press_pairing()["attempts"][0]["id"] for _ in range(3)]
    bodies, probes, hashes = [], [], []
    monkeypatch.setattr(cj, "read_body", lambda p: bodies.append(p) or b"")
    monkeypatch.setattr(pr, "certifier_identity", lambda scope: probes.append(scope) or {})
    monkeypatch.setattr(pr, "_sha256", lambda p: hashes.append(p) or "0" * 64)
    for aid in ids:
        av = pr.replay_availability(_wd(), _rec(aid), None, deep=False)
        assert set(av) == {"journal", "fresh"} and av["journal"]["available"]
    assert bodies == [] and probes == [] and hashes == []
    # под запуском байты пакета сверяются: подменённый хеш не совпал —
    # честный package-bytes-missing, а не запуск по чужим байтам
    deep = pr.replay_availability(_wd(), _rec(ids[0]), None, deep=True)
    assert hashes and deep["journal"]["reason_code"] == "package-bytes-missing"


def test_journal_replay_probes_no_certifier_and_checks_availability_once(
        tmp_path, monkeypatch):
    """«Из журнала» модель не вызывается — зонд сертификатора (поиск CLI,
    ключ API) не нужен и не идёт даже при собственной сертификации;
    полная проверка доступности — ровно одна, внутри press."""
    from webapp import package_runs as pr
    _make_current(tmp_path)
    pairing = _press_pairing()["attempts"][0]
    _on_pairing_stub(monkeypatch, pairing["id"])
    st = _press_rostering(solver=FakeSolver(status_chains=1), llm=fake_llm,
                          pairing_run_id="r-каталог")
    src = st["attempts"][0]
    probes, deep_calls = [], []
    monkeypatch.setattr(pr, "certifier_identity", lambda scope: probes.append(scope)
                        or {"error": "стенд: сертификатор недоступен"})
    real = pr.replay_availability

    def counting(*a, **k):
        if k.get("deep"):
            deep_calls.append(dict(k))
        return real(*a, **k)
    monkeypatch.setattr(pr, "replay_availability", counting)
    time.sleep(1.1)
    out = _replay("rostering", src["id"], "journal", _roster_fakes(with_llm=True))
    rec = _rec(out["attempt_id"])
    assert rec["error"] is None and rec["verdict"] == src["verdict"]
    assert probes == [], "зонд сертификатора у повтора из журнала"
    assert len(deep_calls) == 1 and deep_calls[0]["needs_certifier"] is False
    # у «заново» с собственной сертификацией зонд идёт — и его отказ честен
    with pytest.raises(pr.PackageRunError) as e:
        _replay("rostering", src["id"], "fresh", _roster_fakes(with_llm=True),
                solver=FakeSolver(status_chains=1), sleep=lambda *_: None)
    assert e.value.reason_code == "certifier-unavailable" and probes


def _forbid_live(monkeypatch):
    """Каждая точка живого вызова — исключение с меткой LIVE-CALL: решатель,
    построитель, канал модели, httpx, подпроцессы, сокеты."""
    import httpx
    from vmp.run import button as btn
    from webapp import package_runs as pr
    import cdpm.llm as llm_mod

    def boom(what):
        def _f(*a, **k):
            raise AssertionError("LIVE-CALL: %s" % what)
        return _f
    monkeypatch.setattr(btn, "SolverHttp", boom("SolverHttp()"))
    monkeypatch.setattr(btn, "subprocess_pairing_builder", boom("subprocess_pairing_builder()"))
    monkeypatch.setattr(pr, "solver_url", boom("solver_url()"))
    monkeypatch.setattr(pr, "cloud_certifier_channel", boom("cloud_certifier_channel()"))
    monkeypatch.setattr(pr, "certifier_identity", boom("certifier_identity()"))
    monkeypatch.setattr(llm_mod, "cosmos_llm", boom("cosmos_llm()"))
    monkeypatch.setattr(httpx.Client, "__init__", boom("httpx.Client()"))
    monkeypatch.setattr(httpx.Client, "request", boom("httpx.Client.request()"))
    monkeypatch.setattr(httpx, "request", boom("httpx.request()"))
    monkeypatch.setattr(subprocess, "run", boom("subprocess.run()"))
    monkeypatch.setattr(subprocess, "Popen", boom("subprocess.Popen()"))
    monkeypatch.setattr(socket.socket, "connect", boom("socket.connect()"))
    monkeypatch.setattr(socket, "create_connection", boom("socket.create_connection()"))


def test_journal_replay_pairing_makes_no_live_calls(tmp_path, monkeypatch):
    """Гейт «повтор из журнала без внешних сторон» — явный запрет всех
    точек живых вызовов, не отсутствие CREW_SOLVER_URL: регресс, тихо
    зовущий построитель, решатель или модель, падает меткой LIVE-CALL."""
    _make_current(tmp_path)
    src = _press_pairing()["attempts"][0]
    assert src["verdict"] == "пейринг построен"
    time.sleep(1.1)
    _forbid_live(monkeypatch)
    out = _replay("pairing", src["id"], "journal", _pairing_fakes())
    rec = _rec(out["attempt_id"])          # без status(): тот зовёт certifier_identity
    assert rec["error"] is None, rec["error"]
    assert rec["verdict"] == "пейринг построен"
    rep = _report(rec["id"])
    assert rep["verdict"] == "same", rep["files"]["different"]
    assert rep["journal"]["served"] == rep["journal"]["recorded"]


def test_journal_replay_rostering_on_pairing_with_model_makes_no_live_calls(
        tmp_path, monkeypatch):
    _make_current(tmp_path)
    pairing = _press_pairing()["attempts"][0]
    _on_pairing_stub(monkeypatch, pairing["id"])
    st = _press_rostering(solver=FakeSolver(status_chains=1), llm=fake_llm,
                          pairing_run_id="r-каталог")
    assert st["error"] is None, st
    src = st["attempts"][0]
    time.sleep(1.1)
    _forbid_live(monkeypatch)
    out = _replay("rostering", src["id"], "journal", _roster_fakes(with_llm=True))
    rec = _rec(out["attempt_id"])
    assert rec["error"] is None, rec["error"]
    assert rec["verdict"] == src["verdict"]
    rep = _report(rec["id"])
    assert rep["verdict"] == "same", rep["files"]["different"]
    assert rec["exchanges"]["count"] == src["exchanges"]["count"] >= 1


def test_normalization_keeps_writer_format_key_order_and_indent_are_differences(tmp_path):
    """Гейт «байт в байт» нормализует ровно поля времени и сериализует
    обратно в формате писателя: перестановка ключей или другой отступ
    (смена кода выдачи) — различие json-differs, а не «совпадает после
    нормализации»; нормализованные байты равны исходным везде, кроме
    полей времени."""
    from vmp.run import replay_compare as rcmp
    src, new = tmp_path / "src", tmp_path / "new"
    base = {"verdict": "допустимо", "certified": True,
            "journal": [{"step": "Ш-1п2", "status": "ok",
                         "detail": "реестр от 2026-09-07T10:00:00",
                         "at": "2026-09-07T10:00:00"}]}
    later = json.loads(json.dumps(base))
    later["journal"][0].update(at="2026-09-07T11:00:00",
                               detail="реестр от 2026-09-07T11:00:00")

    def write(root, doc, indent=1, keys=None):
        if root.exists():
            shutil.rmtree(root)
        out = root / "button"
        out.mkdir(parents=True)
        d = {k: doc[k] for k in (keys or list(doc))}
        (out / "delivery.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=indent), encoding="utf-8")
    write(src, base)
    write(new, later)
    got = rcmp.compare_snapshots(src, new)
    assert got["different"] == [] and got["normalized_same"] == ["button/delivery.json"]
    raw = (src / "button" / "delivery.json").read_bytes()
    norm = rcmp.normalize("button/delivery.json", raw)
    assert norm == raw.replace(b"2026-09-07T10:00:00", b"<t>")
    write(new, later, keys=["certified", "verdict", "journal"])
    got = rcmp.compare_snapshots(src, new)
    assert [d["file"] for d in got["different"]] == ["button/delivery.json"]
    assert got["different"][0]["reason_code"] == "json-differs"
    assert got["normalized_same"] == []
    write(new, later, indent=2)
    got = rcmp.compare_snapshots(src, new)
    assert got["different"] and got["different"][0]["reason_code"] == "json-differs"
    # реестр: created подменяется в сыром тексте, формат писателя не трогается
    reg_raw = json.dumps({"created": "2026-09-07T10:00:00 (авто-вывод)", "scope": "x",
                          "rules": [{"rule_id": "R1", "created": "не трогать"}]},
                         ensure_ascii=False, indent=1).encode("utf-8")
    reg_norm = rcmp.normalize("registry/vmp-executors-registry.json", reg_raw)
    assert reg_norm == reg_raw.replace("2026-09-07T10:00:00 (авто-вывод)".encode("utf-8"),
                                       b"<t>")
    # индекс обменов: формат писателя (sort_keys, перевод строки в конце)
    row = {"n": 1, "role": "certifier", "at": "2026-09-07T10:00:00", "elapsed_ms": 40}
    idx_raw = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    idx_norm = rcmp.normalize("button/llm-exchanges/index.jsonl", idx_raw)
    assert idx_norm == (json.dumps(dict(row, at="<t>", elapsed_ms=0), ensure_ascii=False,
                                   sort_keys=True) + "\n").encode("utf-8")


def test_replay_refuses_busy_workdir_and_running_source(tmp_path):
    from webapp import package_runs as pr
    _make_current(tmp_path)
    src = _press_pairing()["attempts"][0]
    go, started = threading.Event(), threading.Event()

    def hook_builder(payload, args, out):
        started.set()
        assert go.wait(10)
        return _catalog_builder()(payload, args, out)
    st = _press_pairing(builder=hook_builder, start_thread=True)
    running = st["attempt_id"]
    assert started.wait(10)
    try:
        av = _avail(src["id"])
        assert av["journal"]["reason_code"] == av["fresh"]["reason_code"] == "run-busy"
        assert _avail(running)["journal"]["reason_code"] == "attempt-running"
        with pytest.raises(pr.PackageRunError) as e:
            _replay("pairing", src["id"], "journal", _pairing_fakes())
        assert e.value.reason_code == "run-busy"
    finally:
        go.set()
    deadline = time.time() + 15
    while pr.status()["done"] is not True and time.time() < deadline:
        time.sleep(0.05)
    assert pr.status()["done"] is True


def test_press_replay_returns_new_attempt_and_reason_codes(tmp_path, monkeypatch):
    from webapp import package_runs as pr
    _make_current(tmp_path)
    src = _press_pairing()["attempts"][0]
    out = _replay("pairing", src["id"], "journal", _pairing_fakes())
    assert set(out) >= {"attempt_id", "run", "workdir", "state", "done", "mode",
                        "source_attempt", "task"}
    assert out["attempt_id"] != src["id"] and out["workdir"].startswith("package-runs/")
    assert "present" not in out
    with pytest.raises(pr.PackageRunError) as e:
        _replay("pairing", src["id"], "journal", _pairing_fakes(), solver=FakeSolver())
    assert e.value.reason_code == "press-refused" and "подставляются журналом" in str(e.value)
    with pytest.raises(pr.PackageRunError) as e:
        _replay("pairing", src["id"], "telepathy", _pairing_fakes())
    assert e.value.reason_code == "press-refused"
    with pytest.raises(pr.PackageRunError) as e:
        _replay("pairing", "нет-такой", "journal", _pairing_fakes())
    assert e.value.reason_code == "attempt-unknown"

    def boom(out):
        raise OSError("стенд: каталог заперт")
    monkeypatch.setattr(pr, "_clear_press_artifacts", boom)
    with pytest.raises(pr.PackageRunError) as e:
        _replay("pairing", src["id"], "journal", _pairing_fakes())
    assert e.value.reason_code == "workdir-not-prepared"
    assert pr.status()["done"] is True and "не подготовлен" in pr.status()["error"]
    assert pr.PackageRunError("x").reason_code is None


def test_replay_routes_session_and_token_report_and_aliases(tmp_path):
    """Алиасы артефактов повтора и наличие ручек области прогона
    (сами ручки — сессия/токен/400/404/409 — гоняет
    tests/test_cosmos_runs.py на клиенте с каталогом данных в tmp)."""
    from webapp import package_runs as pr
    for alias in ("журнал-внешних-вызовов", "журнал-внешних-вызовов-пейринга",
                  "журнал-внешних-вызовов-на-пейринге", "отчёт-повтора",
                  "обмены-с-моделью", "показания-решателя", "статус-прогона-решателя",
                  "реестр-попытки"):
        assert alias in pr.ALLOWED_FILES
    assert pr.ALLOWED_FILES["отчёт-повтора"] == ("replay", "report.json")
    assert pr.ALLOWED_FILES["журнал-внешних-вызовов-пейринга"] == (
        "button-pairing/external-calls", "index.jsonl")
    _make_current(tmp_path)
    src = _press_pairing()["attempts"][0]
    _replay("pairing", src["id"], "journal", _pairing_fakes())
    rec = _last()
    path, name = pr.artifact_path("отчёт-повтора", attempt=rec["id"])
    assert name == "report.json" and json.loads(path.read_text(encoding="utf-8"))["verdict"] == "same"
    path, name = pr.artifact_path("журнал-внешних-вызовов-пейринга", attempt=rec["id"])
    assert name == "index.jsonl" and path.is_file()
    path, _ = pr.artifact_path("реестр-попытки", attempt=rec["id"])
    assert path.is_file()
    with pytest.raises(pr.PackageRunError):
        pr.artifact_path("отчёт-повтора", attempt=src["id"])       # не повтор
    from webapp import app as app_module
    routes = {(getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", None) or ())))
              for r in app_module.app.routes}
    assert ("/api/cosmos-runs/{run_id}/attempts/{attempt}/replay", ("POST",)) in routes
    assert ("/api/cosmos-runs/{run_id}/attempts/{attempt}/replay-report",
            ("GET",)) in routes
    # токен канала читает отчёт (машинный образец GET), запуск — только сессия
    assert app_module._machine_route(
        "GET", "/api/cosmos-runs/run-0001/attempts/A1/replay-report")
    assert not app_module._machine_route(
        "POST", "/api/cosmos-runs/run-0001/attempts/A1/replay")


def test_normalization_list_sufficient_and_no_attempt_ids_in_artifacts(tmp_path, monkeypatch):
    from vmp.run import replay_compare as rcmp
    from webapp import package_runs as pr
    # Восьмая строка списка (10.09.2026) — расход модели в токенах у
    # индекса обменов: повтор из журнала модель не зовёт и токенов не
    # тратит, поэтому счёт израсходованного в гейт байт в байт не входит
    # (тот же класс, что `at` и `elapsed_ms`).
    assert len(rcmp.NORMALIZE) == 8
    _make_current(tmp_path)
    p = _press_pairing()["attempts"][0]
    _on_pairing_stub(monkeypatch, p["id"])
    r = _press_rostering(solver=FakeSolver(status_chains=1), llm=fake_llm,
                         pairing_run_id="r-каталог")["attempts"][0]
    time.sleep(1.1)
    _replay("pairing", p["id"], "journal", _pairing_fakes())
    p2 = _last()
    _replay("rostering", r["id"], "journal", _roster_fakes(with_llm=True))
    r2 = _last()
    for rec in (p2, r2):
        report = _report(rec["id"])
        assert report["verdict"] == "same", report["files"]["different"]
        assert report["files"]["normalized_same"], "список нормализации не задействован"
    # Идентификаторов попыток ростеринга в артефактах нет; единственный
    # законный — pairing_selection.attempt_id (попытка-источник пейринга).
    for aid in (r["id"], r2["id"]):
        for root_id in (r["id"], r2["id"]):
            root = _wd() / pr.ATTEMPTS_DIR / root_id
            for f in root.rglob("*"):
                if f.is_file() and "external-calls" not in f.parts and "replay" not in f.parts:
                    assert aid.encode("utf-8") not in f.read_bytes(), (aid, f)


def test_journal_has_no_secrets(tmp_path, monkeypatch):
    from webapp import package_runs as pr
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "tok-СЕКРЕТ-2026")
    monkeypatch.setenv("CREW_API_KEY", "key-СЕКРЕТ-9")
    monkeypatch.setenv("CREW_SOLVER_URL", "http://user:pass@fake:9098")
    _make_current(tmp_path)
    solver = FakeSolver(healthy=False)
    solver.base = "http://user:pass@fake:9098"
    st = _press_rostering(solver=solver)
    src = st["attempts"][0]
    assert src["verdict"] == "остановка: решатель недоступен"
    delivery = json.loads((_wd() / pr.ATTEMPTS_DIR / src["id"] / "button" / "delivery.json")
                          .read_text(encoding="utf-8"))
    assert delivery["detail"] == "http://fake:9098"
    assert src["solver"]["url"] == "http://fake:9098"
    man = json.loads((_journal_dir(src["id"], "button") / "manifest.json")
                     .read_text(encoding="utf-8"))
    assert man["versions"]["solver"]["url"] == "http://fake:9098"
    secrets = [v.encode("utf-8") for k, v in os.environ.items()
               if any(t in k.upper() for t in ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASS"))
               and v and len(v) >= 6]
    assert secrets
    for f in (_wd() / pr.ATTEMPTS_DIR / src["id"]).rglob("*"):
        if f.is_file():
            data = f.read_bytes()
            assert b"user:pass@" not in data, f
            for s in secrets:
                assert s not in data, (f, s)
    # Исключение httpx (SolverHttp.status → raise_for_status) несёт полный
    # URL запроса с userinfo: журнал (у отказа снимка нет — живой каталог),
    # запись попытки и состояние страницы обязаны быть без него.

    class HttpErrSolver(FakeSolver):
        base = "http://user:pass@fake:9098"

        def status(self, run_id):
            import httpx
            req = httpx.Request(
                "GET", "http://user:pass@fake:9098/schedules/run/%s/status" % run_id)
            httpx.Response(401, request=req).raise_for_status()
    st = _press_rostering(solver=HttpErrSolver())
    rec = st["attempts"][0]
    assert rec["error"] and "401" in rec["error"], rec["error"]
    assert "user:pass@" not in rec["error"] and "fake:9098" in rec["error"]
    jdir = _wd() / "button" / "external-calls"
    leaked = [str(p.relative_to(jdir)) for p in jdir.rglob("*")
              if p.is_file() and b"user:pass@" in p.read_bytes()]
    assert not leaked, "userinfo в журнале: %s" % leaked
    errs = [r for r in _index_live(jdir) if r.get("kind") == "solver" and not r.get("ok")]
    assert errs and errs[0]["error"]["type"] == "HTTPStatusError"
    assert "http://fake:9098/" in errs[0]["error"]["message"]
    assert b"user:pass@" not in (_wd() / "state.json").read_bytes()
    # паспорт прогона: сырой CREW_SOLVER_URL в окружение не попадает
    from webapp import cosmos_runs as cr
    cr.forget()
    env = cr.environment()
    assert env["solver"]["url"] == "http://fake:9098"
    assert "user:pass@" not in json.dumps(env)


def _index_live(jdir: Path) -> list[dict]:
    from vmp.run import call_journal as cj
    return cj._load_index(jdir / cj.INDEX_NAME)


def test_journal_degraded_write_does_not_break_press(tmp_path, monkeypatch):
    from vmp.run import call_journal as cj
    from webapp import package_runs as pr

    def broken(path, data):
        raise OSError("стенд: диск заперт")
    monkeypatch.setattr(cj, "_write_body", broken)
    _make_current(tmp_path)
    st = _press_pairing()
    assert st["done"] is True and st["error"] is None and st["verdict"] == "пейринг построен"
    rec = st["attempts"][0]
    assert rec["journal"]["complete"] is False and "диск заперт" in rec["journal"]["degraded"]
    assert (_wd() / pr.ATTEMPTS_DIR / rec["id"] / "button-pairing" / "delivery.json").is_file()
    av = _avail(rec["id"], solver_given=True)
    assert av["journal"]["reason_code"] == "journal-incomplete"
    assert "диск заперт" in av["journal"]["reason_words"] and av["fresh"]["available"]


# ------------------------------------------------------------ паспорт попытки (7.3, 7.8)

def test_solver_status_and_applied_rules_written_by_button(tmp_path):
    from webapp import package_runs as pr
    _make_current(tmp_path)
    st = _press_rostering()
    rec = st["attempts"][0]
    out = _wd() / pr.ATTEMPTS_DIR / rec["id"] / "button"
    status = json.loads((out / "solver-status.json").read_text(encoding="utf-8"))
    assert status["state"] == "готов" and status["score"] == "0hard/0soft"
    applied = json.loads((out / "applied-rules.json").read_text(encoding="utf-8"))
    assert applied == {"runId": "r-77"}
    path, name = pr.artifact_path("статус-прогона-решателя", attempt=rec["id"])
    assert name == "solver-status.json" and path.is_file()
    path, _ = pr.artifact_path("показания-решателя", attempt=rec["id"])
    assert path.is_file()


def test_attempt_record_carries_passport_fields(tmp_path):
    from webapp import package_runs as pr
    _make_current(tmp_path)
    st = _press_pairing(engine="cplex", spent_limit_s=30)
    rec = st["attempts"][0]
    assert rec["package_sha"] == st["package_sha"]
    assert rec["workdir"].startswith("package-runs/") and rec["workdir"] == st["workdir"]
    assert rec["params"]["spent_limit_s"] == 30 and rec["params"]["deterministic"] is None
    assert set(rec["params"]) == set(pr.PARAM_KEYS)
    assert rec["reproducibility"] == {"class": "wall-clock", "basis_code": "wall-clock-limit",
                                      "workers": 8}
    assert rec["solver"] == {"select_engine": "cplex"}
    assert rec["engine_versions"] and "ortools" in rec["engine_versions"]
    assert set(rec["certifier"]) >= {"scope", "backend", "model", "provider", "note_code"}
    assert set(rec["code"]) >= {"commit", "commit_short", "tree_dirty", "dirty_paths"}
    assert set(rec["template_catalog"]) >= {"version", "fingerprint"}
    assert rec["registry"]["sha256"] and rec["registry"]["source"] == "derived"
    assert rec["summary"]["verdict"] == "пейринг построен" and rec["summary"]["chains"] == 1
    assert rec["exchanges"] == {"present": False, "reason_code": "no-model-calls"}
    assert rec["replay"] == {"of": None}
    assert "run" in rec and st["run"] is None or st["run"]["run"] == rec["run"]
    assert st["attempts_trimmed"] == 0
    # Ростеринг: url решателя без userinfo, движок timefold, класс Д1
    # (стендовый решатель принимает параметры Д1 именованно).

    class D1Solver(FakeSolver):
        def start(self, payload, zip_b64, mode, objective_id, limit_s,
                  task="rostering", pairing_run_id=None, **kw):
            self.d1 = kw.get("deterministic")
            return super().start(payload, zip_b64, mode, objective_id, limit_s,
                                 task, pairing_run_id)
    solver = D1Solver()
    st = _press_rostering(solver=solver, deterministic={"seed": 1, "step_limit": 5})
    assert solver.d1 == {"seed": 1, "step_limit": 5}
    rec = st["attempts"][0]
    assert rec["error"] is None, rec
    assert rec["solver"] == {"url": "http://fake:9098", "engine": "timefold"}
    assert rec["reproducibility"]["class"] == "deterministic"
    assert rec["reproducibility"]["basis_code"] == "profile-d1"
    assert rec["engine_versions"] is None
    assert rec["summary"]["solver_run_id"] == "r-77" and rec["summary"]["score"] == "0hard/0soft"
    # Привязка сверки по ИДАК к попытке (7.7).
    got = pr.note_source_check(_wd(), "rostering", "r-77", None,
                               {"at": "2026-09-07T12:00:00", "reader": "cloud/claude-sonnet-5",
                                "reader_channel": "cli", "counts": {"violated": 0}})
    assert got == rec["id"]
    assert _rec(rec["id"])["source_check"]["counts"] == {"violated": 0}
    assert pr.note_source_check(_wd(), "rostering", "r-нет", None, {}) is None
