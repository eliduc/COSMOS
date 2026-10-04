"""Read-only replay recovery from exact historical input bytes; no services."""
import ast
import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


def _module():
    tree = ast.parse((Path(__file__).parents[1] / "cdpm/replay.py").read_text(encoding="utf-8"))
    names = {"ReplayRefused", "_sha", "_input_bytes", "_missing_inputs", "_apply_action"}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    scope = {"Path": Path, "hashlib": hashlib, "re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "replay-selected", "exec"), scope)
    return SimpleNamespace(**scope)


def _entry(data, stored="answers/rev-002/anketa-otvety.xlsx"):
    return {"name": "answer.xlsx", "stored": stored, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _put(root, rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_two_historical_answers_with_same_path_remain_distinct(tmp_path):
    m = _module()
    old, new = b"historical exact book", b"new exact book"
    a, b = _entry(old), _entry(new)
    p = _put(tmp_path, a["stored"], new)
    recovered = _put(tmp_path, "journal/input-recovery/sha256/" + a["sha256"], old)
    journal = _put(tmp_path, "journal/actions.jsonl", b"original journal unchanged\n")
    before = {x: x.read_bytes() for x in (p, recovered, journal)}
    acts = [{"n": 2, "files": [a]}, {"n": 17, "files": [b]}]
    assert m._missing_inputs(tmp_path, acts) == []
    assert m._input_bytes(tmp_path, a) == old
    assert m._input_bytes(tmp_path, b) == new
    assert before == {x: x.read_bytes() for x in before}


@pytest.mark.parametrize("canonical", [None, b"wrong file"])
def test_missing_or_changed_input_without_exact_recovery_stays_refused(tmp_path, canonical):
    m = _module()
    f = _entry(b"expected")
    if canonical is not None:
        _put(tmp_path, f["stored"], canonical)
    _put(tmp_path, "journal/input-recovery/sha256/" + f["sha256"], b"wrong archive")
    assert m._missing_inputs(tmp_path, [{"n": 3, "files": [f]}])
    with pytest.raises(m.ReplayRefused):
        m._input_bytes(tmp_path, f)


def test_exact_archive_can_recover_missing_input(tmp_path):
    m = _module()
    f = _entry(b"expected")
    _put(tmp_path, "journal/input-recovery/sha256/" + f["sha256"], b"expected")
    assert m._input_bytes(tmp_path, f) == b"expected"


def test_healthy_input_wins_and_legacy_without_hash_still_reads(tmp_path):
    m = _module()
    f = _entry(b"current")
    _put(tmp_path, f["stored"], b"current")
    _put(tmp_path, "journal/input-recovery/sha256/" + f["sha256"], b"wrong unused archive")
    assert m._input_bytes(tmp_path, f) == b"current"
    f.pop("sha256")
    assert m._input_bytes(tmp_path, f) == b"current"


@pytest.mark.parametrize("digest", ["../outside", "x" * 64, "a" * 63])
def test_invalid_digest_cannot_select_archive(tmp_path, digest):
    m = _module()
    f = {**_entry(b"old"), "sha256": digest}
    _put(tmp_path, f["stored"], b"new")
    with pytest.raises(m.ReplayRefused):
        m._input_bytes(tmp_path, f)


@pytest.mark.parametrize("action", ["answers", "third-party", "data"])
def test_driver_uses_verified_historical_bytes(tmp_path, action):
    m = _module()
    f = _entry(b"old")
    _put(tmp_path, f["stored"], b"new")
    _put(tmp_path, "journal/input-recovery/sha256/" + f["sha256"], b"old")
    seen = []
    def capture(*args):
        seen.append(args)
    w = SimpleNamespace(_upload_answers_bytes=capture, _upload_third_party_bytes=capture, _upload_data_bytes=capture)
    m._apply_action(w, "child", tmp_path / "child", tmp_path, {"action": action, "files": [f], "params": {"scope": "local"}})
    assert seen == ([("child", [("answer.xlsx", b"old")])] if action == "data" else
                    [("child", "answer.xlsx", b"old", "local")] if action == "answers" else
                    [("child", "answer.xlsx", b"old")])


def test_birth_and_all_driver_file_reads_share_checked_resolver():
    tree = ast.parse((Path(__file__).parents[1] / "cdpm/replay.py").read_text(encoding="utf-8"))
    for name, expected in (("start", 1), ("_apply_action", 3)):
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_input_bytes"]
        assert len(calls) == expected
        assert 'source_dir / f["stored"]' not in ast.unparse(fn)
