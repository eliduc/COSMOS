"""Подписанное свидетельство проверки кандидата и запретные пути.

Пятый аудит, пакет C1. Проверяется не «механизм есть», а «механизм
непреодолим»: подделанная запись, чужой ключ, переписанная ветка и
перенос запретного файла в другой каталог обязаны ОСТАНАВЛИВАТЬ конвейер,
а не проходить мимо.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from webapp import evidence, evolution_runner as er


# ------------------------------------------------------- запретные пути

def test_forbidden_by_basename_anywhere():
    """Корневой запрет обходился переносом: `webapp/conftest.py` pytest
    исполняет так же, как корневой, а `sitecustomize.py` подхватывается
    из любого места на пути импорта."""
    assert er._is_forbidden_path("conftest.py")
    assert er._is_forbidden_path("webapp/conftest.py")
    assert er._is_forbidden_path("tests/unit/conftest.py")
    assert er._is_forbidden_path("webapp/sitecustomize.py")
    assert er._is_forbidden_path("pytest.ini")
    assert er._is_forbidden_path("tools/verify_audit_20260726.py")
    assert er._is_forbidden_path("webapp/users.py")


def test_ordinary_candidate_paths_stay_allowed():
    """Кандидат обязан уметь править продукт и ДОБАВЛЯТЬ тесты на свою
    же правку — иначе проверка зелена ровно там, где ничего не
    проверено."""
    for path in ("webapp/jobs.py", "tests/test_jobs_run_profile.py",
                 "docs/manual/02-podgotovka-dannyh-RU.md",
                 "webapp/static/app.js", "docs/tools/README.md"):
        assert not er._is_forbidden_path(path), path


# ------------------------------------------------------- подпись записи

@pytest.fixture()
def key(tmp_path, monkeypatch):
    p = tmp_path / "evidence.key"
    monkeypatch.setenv("COSMOS_EVIDENCE_KEY", str(p))
    monkeypatch.setattr(evidence, "CONFIG_DIR", tmp_path / "cfg")
    return p


def test_issue_and_verify_roundtrip(key):
    evidence.issue(7, "test", base_sha="a" * 40, candidate_sha="b" * 40,
                   diff_hash="c" * 64, checks={"unit_tests": "green"})
    body = evidence.verify(7, "test", candidate_sha="b" * 40,
                           diff_hash="c" * 64)
    assert body["base_sha"] == "a" * 40
    assert key.is_file(), "ключ создаётся при первом обращении"


def test_tampered_body_is_refused(key):
    evidence.issue(7, "test", base_sha="a" * 40, candidate_sha="b" * 40,
                   diff_hash="c" * 64, checks={"unit_tests": "red"})
    path = evidence.evidence_path(7, "test")
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["body"]["checks"]["unit_tests"] = "green"   # «мы всё починили»
    path.write_text(json.dumps(rec), encoding="utf-8")
    with pytest.raises(evidence.EvidenceError, match="подпись"):
        evidence.verify(7, "test")


def test_foreign_key_does_not_validate(key, tmp_path, monkeypatch):
    evidence.issue(7, "test", base_sha="a" * 40, candidate_sha="b" * 40,
                   diff_hash="c" * 64, checks={})
    monkeypatch.setenv("COSMOS_EVIDENCE_KEY", str(tmp_path / "other.key"))
    with pytest.raises(evidence.EvidenceError, match="подпись"):
        evidence.verify(7, "test")


def test_rewritten_branch_is_caught(key):
    """Ветку переписали после проверки — раскатывать нечего."""
    evidence.issue(7, "stage", base_sha="a" * 40, candidate_sha="b" * 40,
                   diff_hash="c" * 64, checks={})
    with pytest.raises(evidence.EvidenceError, match="ветка переписана"):
        evidence.verify(7, "stage", candidate_sha="d" * 40)


def test_changed_diff_is_caught(key):
    evidence.issue(7, "test", base_sha="a" * 40, candidate_sha="b" * 40,
                   diff_hash="c" * 64, checks={})
    with pytest.raises(evidence.EvidenceError, match="состав изменений"):
        evidence.verify(7, "test", diff_hash="e" * 64)


def test_missing_evidence_is_an_error_not_a_pass(key):
    with pytest.raises(evidence.EvidenceError, match="нет свидетельства"):
        evidence.verify(99, "stage")


def test_key_inside_candidate_tree_is_refused(tmp_path, monkeypatch):
    """Подписывающий и подписываемый не могут быть одним лицом."""
    cand = tmp_path / "cosmos-test"
    cand.mkdir()
    monkeypatch.setenv("COSMOS_TEST_DIR", str(cand))
    monkeypatch.setenv("COSMOS_EVIDENCE_KEY", str(cand / "evidence.key"))
    with pytest.raises(evidence.EvidenceError, match="внутри дерева Test"):
        evidence.sign({"x": 1})


# --------------------------------------------- отпечаток состава правок

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


def test_diff_fingerprint_tracks_content(repo):
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                          capture_output=True, text=True).stdout.strip()
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "one")
    head1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                           capture_output=True, text=True).stdout.strip()
    fp1 = evidence.diff_fingerprint(repo, base, head1)
    assert fp1 == evidence.diff_fingerprint(repo, base, head1), "устойчив"

    # Та же ревизия, но содержимое подменено — отпечаток обязан уехать.
    (repo / "a.py").write_text("x = 3\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "two")
    head2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                           capture_output=True, text=True).stdout.strip()
    assert evidence.diff_fingerprint(repo, base, head2) != fp1


def test_diff_fingerprint_refuses_unknown_revision(repo):
    with pytest.raises(evidence.EvidenceError):
        evidence.diff_fingerprint(repo, "0" * 40, "1" * 40)
