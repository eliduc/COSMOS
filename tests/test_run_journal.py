# -*- coding: utf-8 -*-
"""Журнал COSMOS в записи о прогоне и сшивка с хозяином записи
(webapp/run_journal.py).

Основание — проект `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, разделы
3.2, 10 (половина COSMOS шага 2) и 11.2 (тело ручки `record/append`).
Судится ровно то, ради чего журнал заведён:

  * законченная попытка нажатия ложится этапом ПО СЛОВАРЮ
    (`vmp/run_record.py` судит запись сам — стенд не повторяет схему);
  * сверка с ИДАК ложится своим этапом со счётчиками сверки и расходом
    читателя;
  * повтор того же ключа (система, этап, подэтап) ЗАМЕНЯЕТ строку, а не
    удваивает её: иначе один этап становился бы двумя;
  * у частного прогона (хозяин — COSMOS) журнал дописывается в свою
    `run-record.json`, а после обеих задач запись закрывается итогами —
    и остаётся годной по словарю;
  * отправка хозяину: 200 — квитанция и полученная запись рядом;
    отказ хозяина и недоступная сеть — строка остаётся в очереди,
    НАЖАТИЕ ПРИ ЭТОМ НЕ ПАДАЕТ;
  * карточка читает запись у хозяина, а без связи — местную копию либо
    свою запись, честно называя источник.

Литералов среды в файле нет: каталог данных — `tmp_path`, адрес панели —
вымышленная зона `.invalid`, сеть не трогается (подставлен `urlopen`).
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from tests.test_package_runs import _env, _make_current   # noqa: E402,F401
from tests.test_run_button import FakeSolver              # noqa: E402
from tests.test_package_runs import _pairing_kwargs       # noqa: E402
from tests.test_run_gate import _receive, _wd             # noqa: E402
from vmp import run_record as rr                          # noqa: E402

#: Адрес панели-хозяина: вымышленная зона, сеть не трогается.
PANEL = "http://панель.invalid/api/runs/run-9001/record"


@pytest.fixture(autouse=True)
def _record_env(monkeypatch):
    """Носители площадки записи — чистые: значение с машины сделало бы
    прогон недетерминированным."""
    monkeypatch.delenv("CREW_CONTOUR", raising=False)
    monkeypatch.delenv("CREW_CDPM_RECORD_API", raising=False)


# ----------------------------------------------------------- помощники
def _judge(entry: dict) -> dict:
    """Запись этапа судит СЛОВАРЬ (`run_record.append_stage`): она либо
    ложится в журнал, либо отказывает словами. Второго перечня полей у
    стенда нет."""
    record: dict = {}
    rr.append_stage(record, entry)
    return record["journal"][0]


def _attempt(**over) -> dict:
    """Запись попытки нажатия в именах `webapp/package_runs.py`."""
    rec = {"id": "A1", "task": "rostering", "engine": None,
           "started_at": "2026-09-10T10:00:00",
           "finished_at": "2026-09-10T10:05:00",
           "state": "завершено", "done": True, "verdict": "допустимо",
           "error": None, "catalog": "attempts/A1/button",
           "package_sha": "a" * 64, "actor": "user:стенд",
           "params": {"spent_limit_s": 60, "mode": "feasibility-hard"},
           "summary": {"verdict": "допустимо", "certified": True,
                       "score": 0, "chains": 5, "findings": 0,
                       "uncovered": 0},
           "resources": {"models": [{"role": "certifier",
                                     "model": "модель-стенда",
                                     "provider": "ollama", "calls": 1,
                                     "input_tokens": 10, "output_tokens": 20,
                                     "counted": True, "note": None}],
                         "solver_seconds": 12.5}}
    rec.update(over)
    return rec


def _snap(**over) -> dict:
    """Снимок сверки с ИДАК (`source-check/<задача>/last.json`)."""
    snap = {"status": "done", "at": "2026-09-10T11:00:00",
            "run": "run-9001", "task": "rostering",
            "counts": {"ok": 7, "soft_info": 2, "not_checkable": 1},
            "problems": [], "protocol": "source-check/rostering/protocol.md",
            "reader": "модель-читателя", "reader_channel": "local",
            "resources": {"models": [{"role": "source_reader",
                                      "model": "модель-читателя",
                                      "provider": "ollama", "calls": 3,
                                      "input_tokens": 30,
                                      "output_tokens": 40,
                                      "counted": True, "note": None}]}}
    snap.update(over)
    return snap


class _Resp:
    """Ответ службы для подставленного `urlopen`."""

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
    """Подстановка сети: `answer` — ответ либо исключение."""
    from webapp import run_journal

    def _open(req, timeout=None):
        if seen is not None:
            seen.append({"url": req.full_url, "method": req.get_method(),
                         "body": json.loads(req.data.decode("utf-8"))
                         if req.data else None})
        if isinstance(answer, Exception):
            raise answer
        return answer() if callable(answer) else answer
    monkeypatch.setattr(run_journal._urlrequest, "urlopen", _open)


def _http_error(status: int, body: dict) -> urllib.error.HTTPError:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return urllib.error.HTTPError(PANEL, status, "отказ", {},
                                  io.BytesIO(raw))


# ------------------------------------------------------- записи этапов

def test_попытка_ложится_этапом_по_словарю(tmp_path):
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("a" * 64)
    out = wd / "attempts" / "A1" / "button"
    out.mkdir(parents=True)
    (out / "delivery.json").write_text(json.dumps({"verdict": "допустимо"}),
                                       encoding="utf-8")
    got = run_journal.note_attempt(wd, _attempt())
    entry = got["row"]["entry"]
    assert _judge(entry) == entry, "этап не сошёлся со словарём записи"
    assert entry["stage"] == rr.COSMOS_STAGES["rostering"]
    assert entry["substage"] == "A1"
    assert entry["outcome"] == rr.OUTCOME_OK
    assert entry["actor"] == "user:стенд"
    assert entry["duration_machine_s"] == 300.0
    assert entry["counters"]["chains"] == 5
    assert entry["counters"]["certified"] == 1
    assert entry["resources"]["solver_seconds"] == 12.5
    assert entry["resources"]["models"][0]["input_tokens"] == 10
    # Выдача — ссылкой и отпечатком, а не копией.
    delivery = [a for a in entry["artifacts"] if a["kind"] == "выдача"]
    assert delivery and len(delivery[0]["sha256"]) == 64
    assert delivery[0]["path"] == "attempts/A1/button/delivery.json"
    rows = run_journal.entries(wd)
    assert len(rows) == 1
    assert rows[0]["key"] == {"system": "cosmos",
                              "stage": entry["stage"], "substage": "A1"}
    assert rows[0]["results"]["verdicts"] == {entry["stage"]: "допустимо"}


def test_отказавшая_попытка_тоже_этап(tmp_path):
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("b" * 64)
    got = run_journal.note_attempt(wd, _attempt(
        done=True, verdict=None, error="решатель недоступен",
        state="отказ", catalog=None))
    entry = got["row"]["entry"]
    assert _judge(entry) == entry
    assert entry["outcome"] == rr.OUTCOME_FAILURE
    assert "решатель недоступен" in entry["words"]


def test_сверка_с_идак_ложится_этапом(tmp_path):
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("c" * 64)
    got = run_journal.note_source_check(wd, "rostering", "A1", _snap(),
                                        actor="user:стенд")
    entry = got["row"]["entry"]
    assert _judge(entry) == entry
    assert entry["stage"] == run_journal.STAGE_SOURCE_CHECK
    assert entry["substage"] == "A1/rostering"
    assert entry["outcome"] == rr.OUTCOME_OK
    assert entry["counters"] == {"ok": 7, "soft_info": 2, "not_checkable": 1,
                                 "problems": 0}
    assert entry["resources"]["models"][0]["model"] == "модель-читателя"
    assert entry["actor"] == "user:стенд"
    # Проблемы сверки — предупреждение, а не успех. Снимок службы
    # кладёт `problems` ЧИСЛОМ (vmp/source_check_run.py), панель —
    # перечнем: читаются обе формы.
    got2 = run_journal.note_source_check(wd, "pairing", "A2",
                                         _snap(task="pairing", problems=2))
    assert got2["row"]["entry"]["outcome"] == rr.OUTCOME_WARN
    assert got2["row"]["entry"]["counters"]["problems"] == 2
    assert len(run_journal.entries(wd)) == 2
    # Нарушенная строка сверки — тоже предупреждение с кодом причины.
    got3 = run_journal.note_source_check(
        wd, "rostering", "A3",
        _snap(counts={"ok": 3, "violated": 1}, problems=0))
    assert got3["row"]["entry"]["outcome"] == rr.OUTCOME_WARN
    assert got3["row"]["entry"]["reason_code"] == "source-check-violations"
    assert got3["row"]["entry"]["counters"]["violated"] == 1


def test_повтор_ключа_заменяет_а_не_удваивает(tmp_path):
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("d" * 64)
    run_journal.note_attempt(wd, _attempt())
    run_journal.note_attempt(wd, _attempt(verdict="недопустимо"))
    rows = run_journal.entries(wd)
    assert len(rows) == 1, "повтор ключа удвоил этап"
    assert "недопустимо" in rows[0]["entry"]["words"]
    # Другая попытка — другая строка (ключ различается подэтапом).
    run_journal.note_attempt(wd, _attempt(id="A2"))
    assert len(run_journal.entries(wd)) == 2


# ------------------------------------------------------------- сшивка

def _head(api: str | None = PANEL, owner: str = "панель/раннер по манифесту"):
    return {"record_id": "run-9001", "declaration_sha256": "f" * 64,
            "record_api": api, "owner": owner, "source_run": "run-9001",
            "customer": "Т"}


def test_отправка_хозяину_принята(tmp_path, monkeypatch):
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("e" * 64)
    record = {"schema": rr.SCHEMA, "declaration": {"run_id": "run-9001"},
              "journal": [], "results": {"state": rr.STATE_RUNNING}}
    seen: list = []
    _fake_urlopen(monkeypatch, _Resp({"ok": True, "accepted": 1,
                                      "replaced": 0, "record": record}), seen)
    got = run_journal.note_attempt(wd, _attempt(), _head())
    assert got["words"] == [], got
    assert seen[0]["url"] == PANEL + "/append"
    body = seen[0]["body"]
    assert body["schema"] == run_journal.APPEND_SCHEMA
    assert body["record_id"] == "run-9001" and body["system"] == "cosmos"
    assert body["declaration_sha256"] == "f" * 64
    assert len(body["entries"]) == 1
    assert body["results"]["verdicts"]
    # Очередь пуста, квитанция и полученная запись — рядом.
    assert run_journal._read_rows(run_journal.outbox_path(wd)) == []
    receipts = run_journal._read_rows(run_journal.receipts_path(wd))
    assert receipts and receipts[-1]["ok"] is True
    remote = json.loads(run_journal.remote_path(wd).read_text(encoding="utf-8"))
    assert remote["record"]["declaration"]["run_id"] == "run-9001"
    state = run_journal.stitch_state(wd, _head())
    assert state == {"mode": "remote", "record_api": PANEL, "sent": 1,
                     "waiting": 0, "refused": 0, "last_error": None,
                     "last_wait": None}


def test_отказ_хозяина_оставляет_запись_в_очереди(tmp_path, monkeypatch):
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("f" * 64)
    _fake_urlopen(monkeypatch, _http_error(409, {
        "ok": False, "code": "declaration-mismatch",
        "words": "отпечаток объявления не совпал"}))
    got = run_journal.note_attempt(wd, _attempt(), _head())
    assert got["words"] and "не совпал" in got["words"][0]
    assert len(run_journal._read_rows(run_journal.outbox_path(wd))) == 1
    receipts = run_journal._read_rows(run_journal.receipts_path(wd))
    assert receipts[-1]["ok"] is False
    assert receipts[-1]["code"] == "declaration-mismatch"
    state = run_journal.stitch_state(wd, _head())
    assert state["waiting"] == 1 and state["refused"] == 1
    assert "не совпал" in state["last_error"]
    # Панель поднялась — повтор очереди уходит целиком и не дублирует:
    # ключ (система, этап, подэтап) у хозяина заменяет запись.
    seen: list = []
    _fake_urlopen(monkeypatch, _Resp({"ok": True, "accepted": 0,
                                      "replaced": 1, "record": {}}), seen)
    again = run_journal.flush_outbox(wd, _head())
    assert again["sent"] == 1 and again["waiting"] == 0
    assert len(seen[0]["body"]["entries"]) == 1


def test_недоступная_сеть_не_роняет_запись(tmp_path, monkeypatch):
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("1" * 64)
    _fake_urlopen(monkeypatch, OSError("сети нет"))
    got = run_journal.note_attempt(wd, _attempt(), _head())
    assert got["words"] and "недоступен" in got["words"][0]
    assert len(run_journal.entries(wd)) == 1, "этап COSMOS записан у себя"
    assert len(run_journal._read_rows(run_journal.outbox_path(wd))) == 1


# ----------------------------------------- исходы сшивки: три, не два

def test_таймаут_занятой_панели_это_ждёт_а_не_отказ(tmp_path, monkeypatch):
    """Живой разбор 11.09.2026: панель занята, отправка обрывается по
    сроку — ВЕРДИКТА не было, и запись у хозяина к этому времени могла
    лечь целиком. Такой исход — «ждёт» (повтор заменит строку по ключу
    идемпотентности), а не «отказано»: счётчик отказов остаётся нулём, и
    слова живут в отдельном поле карточки."""
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("3" * 64)
    _fake_urlopen(monkeypatch, TimeoutError("срок вышел"))
    run_journal.note_attempt(wd, _attempt(), _head())
    receipts = run_journal._read_rows(run_journal.receipts_path(wd))
    assert receipts[-1]["outcome"] == run_journal.STITCH_WAITING
    assert receipts[-1]["code"] == run_journal.CODE_UNREACHABLE
    state = run_journal.stitch_state(wd, _head())
    assert state["refused"] == 0, "таймаут отказом не считается"
    assert state["waiting"] == 1 and state["sent"] == 0
    assert state["last_error"] is None
    assert state["last_wait"] and "ждёт повтора" in state["last_wait"]
    # Панель ответила — очередь уходит, и «ждёт» больше нет.
    _fake_urlopen(monkeypatch, _Resp({"ok": True, "accepted": 1,
                                      "record": {}}))
    again = run_journal.flush_outbox(wd, _head())
    assert again["outcome"] == run_journal.STITCH_SENT
    after = run_journal.stitch_state(wd, _head())
    assert after == dict(state, sent=1, waiting=0)


def test_поломка_службы_хозяина_это_тоже_ждёт(tmp_path, monkeypatch):
    """Ответ 5xx — служба сломалась, а не хозяин отказал: вердикта нет,
    запись ждёт повтора."""
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("4" * 64)
    _fake_urlopen(monkeypatch, _http_error(503, {"detail": "панель занята"}))
    run_journal.note_attempt(wd, _attempt(), _head())
    receipts = run_journal._read_rows(run_journal.receipts_path(wd))
    assert receipts[-1]["outcome"] == run_journal.STITCH_WAITING
    assert receipts[-1]["code"] == run_journal.CODE_OWNER_BROKEN
    state = run_journal.stitch_state(wd, _head())
    assert state["refused"] == 0 and state["waiting"] == 1
    assert "панель занята" in state["last_wait"]


def test_отказ_хозяина_считается_отказом_и_с_кодом_и_по_4xx(tmp_path,
                                                            monkeypatch):
    """«Отказано» — только НАЗВАННЫЙ вердикт: код отказа в теле ответа
    либо ответ 4xx («так нельзя»). Тело без кода при 4xx получает код
    по умолчанию, чтобы отказ не остался безымянным."""
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("5" * 64)
    _fake_urlopen(monkeypatch, _http_error(422, {"detail": "тело не по схеме"}))
    run_journal.note_attempt(wd, _attempt(), _head())
    receipts = run_journal._read_rows(run_journal.receipts_path(wd))
    assert receipts[-1]["outcome"] == run_journal.STITCH_REFUSED
    assert receipts[-1]["code"] == run_journal.CODE_REFUSED
    state = run_journal.stitch_state(wd, _head())
    assert state["refused"] == 1 and state["last_wait"] is None
    assert "тело не по схеме" in state["last_error"]


def test_старые_квитанции_без_исхода_читаются_по_коду(tmp_path):
    """Квитанции, писанные до разведения исходов, поля `outcome` не
    несут: исход судится по коду — иначе прежние прогоны разом стали бы
    «отказанными»."""
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("6" * 64)
    for row in ({"ok": True, "code": None, "words": None},
                {"ok": False, "code": "owner-unreachable",
                 "words": "хозяин записи недоступен"},
                {"ok": False, "code": "declaration-mismatch",
                 "words": "отпечаток объявления не совпал"}):
        run_journal._append_row(run_journal.receipts_path(wd), row)
    state = run_journal.stitch_state(wd, _head())
    assert state["sent"] == 1 and state["refused"] == 1
    assert "не совпал" in state["last_error"]
    assert "недоступен" in state["last_wait"]


def test_пакет_без_адреса_записи_шить_некуда(tmp_path):
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("2" * 64)
    head = dict(_head(api=None), record_api_code="record-api-missing")
    got = run_journal.note_attempt(wd, _attempt(), head)
    assert got["words"] == [] and got["stitch_code"] == "record-api-missing"
    assert run_journal.stitch_state(wd, head)["mode"] == "none"


# ------------------------------------------------------- частный прогон

def test_частный_прогон_запись_растёт_и_закрывается(tmp_path):
    """Хозяин записи — COSMOS: журнал дописывается в свою
    `run-record.json`, обе задачи закрывают запись итогами, и запись
    остаётся годной по словарю."""
    from webapp import run_gate, run_journal
    sha = _receive(tmp_path, declared=False)
    wd = _wd(sha)
    run_gate.declare({"purpose": "исследование"}, "стенд")
    head = run_gate.head_for(wd, {})
    assert head["owner"] == run_gate.OWNER_COSMOS
    run_journal.note_attempt(wd, _attempt(id="A1", task="pairing",
                                          verdict="пейринг построен",
                                          catalog=None), head)
    record = rr.load(wd)
    assert rr.validate(record) == []
    assert record["results"]["state"] == rr.STATE_RUNNING
    assert [e["stage"] for e in record["journal"]] == [
        rr.COSMOS_STAGES["pairing"]]
    run_journal.note_source_check(wd, "pairing", "A1", _snap(task="pairing"),
                                  actor="стенд", head=head)
    run_journal.note_attempt(wd, _attempt(id="A2", catalog=None), head)
    record = rr.load(wd)
    assert rr.validate(record) == []
    assert record["results"]["state"] == rr.STATE_DONE
    assert record["results"]["verdicts"] == {
        rr.COSMOS_STAGES["pairing"]: "пейринг построен",
        rr.COSMOS_STAGES["rostering"]: "допустимо"}
    assert record["results"]["timing"]["machine_s"] >= 600
    stages = [e["stage"] for e in record["journal"]]
    assert run_journal.STAGE_SOURCE_CHECK in stages
    view = run_journal.record_view(wd, head)
    assert view["source"] == "local" and view["state"] == rr.STATE_DONE


# --------------------------------------------------------- вид записи

def test_карточка_читает_запись_у_хозяина_и_без_него(tmp_path, monkeypatch):
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("3" * 64)
    wd.mkdir(parents=True)
    record = {"schema": rr.SCHEMA, "declaration": {"run_id": "run-9001"},
              "journal": [{"outcome": rr.OUTCOME_OK}],
              "results": {"state": rr.STATE_RUNNING}}
    _fake_urlopen(monkeypatch, _Resp({"record": record}))
    view = run_journal.record_view(wd, _head())
    assert view["source"] == "remote"
    assert view["record"]["declaration"]["run_id"] == "run-9001"
    assert view["state"] == rr.STATE_DONE      # по исходам журнала
    # Панель упала — показывается последняя полученная копия, и об этом
    # говорится словами.
    _fake_urlopen(monkeypatch, OSError("сети нет"))
    view2 = run_journal.record_view(wd, _head())
    assert view2["source"] == "remote-cache"
    assert view2["record"]["declaration"]["run_id"] == "run-9001"
    assert any("копия записи" in w for w in view2["words"])
    # Копии нет вовсе — честное «запись хозяина недоступна».
    run_journal.remote_path(wd).unlink()
    view3 = run_journal.record_view(wd, _head())
    assert view3["source"] == "none" and view3["record"] is None
    assert any("недоступна" in w for w in view3["words"])


# ------------------------------------------------------- крючок нажатия

def test_нажатие_кладёт_этап_в_летопись(tmp_path, monkeypatch):
    """Крючок в `package_runs.press`: после законченной попытки этап
    ложится в летопись сам, и нажатие от этого не зависит."""
    from webapp import package_runs as pr, run_journal
    sha = _make_current(tmp_path)
    fakes = _pairing_kwargs()
    st = pr.press("pairing", engine="cpsat", solver=FakeSolver(),
                  start_thread=False, sleep=lambda *_: None, **fakes)
    assert st["done"] is True and st["error"] is None, st
    wd = pr.workdir_for(sha)
    rows = run_journal.entries(wd)
    assert len(rows) == 1, rows
    entry = rows[0]["entry"]
    assert entry["stage"] == rr.COSMOS_STAGES["pairing"]
    assert entry["substage"] == st["attempt_id"]
    assert entry["outcome"] == rr.OUTCOME_OK
    assert _judge(entry) == entry
    assert any(a["kind"] == "выдача" for a in entry["artifacts"])
    # Пакет несёт номер записи, а адреса ручек хозяина у него нет:
    # предупреждений в записи попытки нет, состояние сшивки — «none».
    rec = pr._attempt_record(pr.read_state(wd), st["attempt_id"])
    assert rec.get("record_note") is None, rec.get("record_note")
    assert run_journal.stitch_state(wd, {"record_id": "run-9001"})["mode"] \
        == "none"


def test_беда_летописи_не_роняет_нажатие(tmp_path, monkeypatch):
    """Правило раздела 10: летопись не ломает нажатие. Сломанный
    журнал — предупреждение словами в записи попытки, расчёт цел."""
    from webapp import package_runs as pr, run_journal

    def _broken(*_a, **_k):
        raise RuntimeError("летопись сломалась")
    monkeypatch.setattr(run_journal, "note_attempt", _broken)
    sha = _make_current(tmp_path)
    st = pr.press("pairing", engine="cpsat", solver=FakeSolver(),
                  start_thread=False, sleep=lambda *_: None,
                  **_pairing_kwargs())
    assert st["done"] is True and st["error"] is None, st
    assert st["verdict"] == "пейринг построен"
    rec = pr._attempt_record(pr.read_state(pr.workdir_for(sha)),
                             st["attempt_id"])
    assert rec["record_note"] and "летопись сломалась" in rec["record_note"][0]


def test_нажатие_шьёт_этап_к_хозяину_записи(tmp_path, monkeypatch):
    """Адрес ручек записи задан площадкой — этап уходит хозяину прямо из
    нажатия: тело по договору, квитанция и полученная запись рядом."""
    from webapp import package_runs as pr, run_gate, run_journal
    monkeypatch.setenv("CREW_CDPM_RECORD_API", "http://панель.invalid")
    seen: list = []
    _fake_urlopen(monkeypatch, lambda: _Resp(
        {"ok": True, "accepted": 1, "replaced": 0,
         "record": {"schema": rr.SCHEMA,
                    "declaration": {"run_id": "run-9001"},
                    "journal": [], "results": {"state": rr.STATE_RUNNING}}}),
        seen)
    sha = _make_current(tmp_path)
    st = pr.press("pairing", engine="cpsat", solver=FakeSolver(),
                  start_thread=False, sleep=lambda *_: None,
                  **_pairing_kwargs())
    assert st["done"] is True and st["error"] is None, st
    wd = pr.workdir_for(sha)
    assert seen and seen[0]["url"].endswith("/api/runs/run-9001/record/append")
    body = seen[0]["body"]
    assert body["record_id"] == "run-9001" and body["system"] == "cosmos"
    assert body["entries"][0]["substage"] == st["attempt_id"]
    head = run_gate.head_for(wd, pr._package_manifest(Path(
        pr.read_state(wd)["package_path"])))
    state = run_journal.stitch_state(wd, head)
    assert state["mode"] == "remote" and state["sent"] == 1
    assert state["waiting"] == 0 and state["refused"] == 0
    assert run_journal.remote_path(wd).is_file()
    # Нажатие не оставило предупреждений: сшивка прошла.
    rec = pr._attempt_record(pr.read_state(wd), st["attempt_id"])
    assert rec.get("record_note") is None
