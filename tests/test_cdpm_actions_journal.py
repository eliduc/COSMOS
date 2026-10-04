# -*- coding: utf-8 -*-
"""Журнал действий над прогоном (проект повтора, этап Р1): строки start /
sync / end / refused, слияние, текущее действие контекста, запись файла."""
import hashlib
import json

import pytest

from cdpm import actions_journal as aj


@pytest.fixture(autouse=True)
def _fresh():
    aj.forget()
    aj.set_current(None)
    yield
    aj.forget()
    aj.set_current(None)


def test_note_end_refused_and_pairing(tmp_path):
    run = tmp_path / "run-0001"
    run.mkdir()
    assert aj.actions(run) == [] and aj.count(run) == 0
    n1 = aj.note(run, "create", params={"customer": "Полярис"},
                 files=[aj.file_entry("a.md", b"abc", "src/a.md")])
    n2 = aj.note(run, "extract", params={"scope": None},
                 state_before={"state": "заморожено"})
    n3 = aj.note(run, "kit-scope", params={"body": {"data_class": "x"}}, sync=True)
    assert (n1, n2, n3) == (1, 2, 3)
    aj.end(run, n1, state_after={"state": "заморожено"}, duration_s=0.5,
           calls=[0, 0])
    aj.refused(run, n3, 400, "неизвестные поля признака")
    acts = aj.actions(run)
    assert [a["n"] for a in acts] == [1, 2, 3]
    assert acts[0]["action"] == "create" and acts[0]["done"] is True
    assert acts[0]["files"][0]["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert acts[0]["files"][0] == {"name": "a.md", "sha256": acts[0]["files"][0]["sha256"],
                                   "bytes": 3, "stored": "src/a.md"}
    assert acts[0]["state_after"] == {"state": "заморожено"} and acts[0]["calls"] == [0, 0]
    assert acts[1]["done"] is False and acts[1]["refused"] is False
    assert acts[1]["state_before"] == {"state": "заморожено"}
    assert acts[2]["kind"] == "sync" and acts[2]["refused"] is True
    assert acts[2]["status"] == 400 and "неизвестные" in acts[2]["detail"]
    assert aj.count(run) == 3
    # Файл — только дописывание, строки читаются как есть.
    raw = aj.read(run)
    assert [r["kind"] for r in raw] == ["start", "start", "sync", "end", "refused"]
    for r in raw:
        assert r["at"]
    # Номера продолжаются после перезапуска процесса (счётчик с диска).
    aj.forget(run)
    assert aj.note(run, "arbiter", sync=True) == 4


def test_current_action_follows_the_context(tmp_path):
    run = tmp_path / "run-0001"
    run.mkdir()
    assert aj.current() is None
    n = aj.note(run, "extract")
    assert aj.current() == n
    token = aj.set_current(None)
    assert aj.current() is None
    aj.reset_current(token)
    assert aj.current() == n
    aj.set_current(7)
    assert aj.current() == 7
    aj.set_current(None)


def test_journal_file_is_plain_jsonl(tmp_path):
    run = tmp_path / "run-0001"
    run.mkdir()
    aj.note(run, "data", sync=True, files=[aj.file_entry("d.xlsx", b"\x00\x01", "data-src/d.xlsx")])
    p = aj.path_of(run)
    assert p == run / "journal" / "actions.jsonl"
    line = json.loads(p.read_bytes().decode("utf-8").splitlines()[0])
    assert line["action"] == "data" and line["files"][0]["bytes"] == 2
