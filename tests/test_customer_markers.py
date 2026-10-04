# -*- coding: utf-8 -*-
"""Линт маркеров заказчика (единый источник — customer-markers.json
оснастки публикации, вне репозитория).

Гейт судит ПУБЛИКУЕМОЕ, а не локальный main: локальный репозиторий
законно несёт отчёты заказчика (данные не покидают машину),
публикация идёт срезом. Строгий прогон включается только в потоке
подготовки публикации: CUSTOMER_MARKERS_ENFORCE_REPO=<worktree
среза>. Без неё — тесты механики на синтетическом перечне (границы
слова с подчёркиваниями, kind-семантика, исключения путей,
молчание без перечня). Путь к перечню строится относительно (урок
CI: тесты не читают машинные пути вне репозитория) — на чужой
машине перечня честно нет."""
import os
import subprocess
from pathlib import Path

import pytest

from tools import customer_markers as cm

# Значения СИНТЕТИЧЕСКИЕ (не реальные маркеры): срез несёт этот
# файл, и гейт публикации не должен находить маркеры в самом тесте.
SPEC = {
    "checks": [
        {"name": "подстрока", "kind": "substring",
         "values": ["synthmark"]},
        {"name": "токен", "kind": "token", "values": ["QZR"]},
        {"name": "код", "kind": "regex",
         "values": ["QZ[-\\s]?\\d{2,4}"], "case": "exact"},
    ],
    "exceptions": ["docs/allowed.md"],
}


def _pats(i):
    return cm._patterns(SPEC["checks"][i])


def test_kind_semantics():
    assert _pats(0)[0].search("Synthmarkiana Airways")  # подстрока
    assert _pats(1)[0].search("рейс из QZR в SVG")    # токен
    assert not _pats(1)[0].search("x_QZR_y = 1")      # _ — часть слова
    assert not _pats(1)[0].search("слово sqzrum")     # не подстрока
    assert _pats(2)[0].search("рейс QZ-123")          # regex
    assert not _pats(2)[0].search("рейс qz-123")      # case exact


def _mini_repo(tmp_path):
    repo = tmp_path / "r"
    (repo / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.txt").write_text("маркер synthmark тут\nчисто\n",
                                encoding="utf-8")
    (repo / "docs" / "allowed.md").write_text("synthmark — искл.",
                                              encoding="utf-8")
    (repo / "b.bin").write_bytes(b"\x00synthmark\x00")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return repo


def test_scan_respects_exceptions_and_binary(tmp_path):
    got = cm.scan(SPEC, _mini_repo(tmp_path))
    assert [(f, n, name) for f, n, name, _ in got] \
        == [("a.txt", 1, "подстрока")]


def test_context_allow_suppresses(tmp_path):
    """context_allow перечня: строка с маркером НЕ находка, если в
    ней же разрешающий контекст (языковые названия интерфейса —
    замечание решателя 2026-08-25)."""
    spec = {"checks": [{"name": "п", "kind": "substring",
                        "values": ["synthmark"],
                        "context_allow": ["язык", "language"]}]}
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.txt").write_text(
        "маркер synthmark тут\n"
        "опция synthmark language выбрана\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    got = cm.scan(spec, repo)
    assert [(f, n) for f, n, _, _ in got] == [("a.txt", 1)]


def test_publish_scope_from_marker_list(tmp_path):
    """Область публикации живёт в САМОМ перечне (унификация
    2026-08-29): прежде списки исключений жили в двух местах — в
    линте и в сборщике среза — и разошлись бы молча. Порядок:
    publish_keep сильнее publish_exclude_exact сильнее префиксов
    publish_exclude; вне области файл не судится вовсе."""
    spec = {
        "checks": [{"name": "п", "kind": "substring",
                    "values": ["synthmark"]}],
        "publish_exclude": ["private/"],
        "publish_exclude_exact": ["notes.md"],
        "publish_keep": ["private/manual.md"],
    }
    assert cm.in_publish_scope(spec, "a.txt")
    assert not cm.in_publish_scope(spec, "private/x.txt")
    assert not cm.in_publish_scope(spec, "notes.md")
    assert cm.in_publish_scope(spec, "private/manual.md")  # keep

    repo = tmp_path / "r"
    (repo / "private").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for rel in ("a.txt", "notes.md", "private/x.txt",
                "private/manual.md"):
        (repo / rel).write_text("маркер synthmark тут\n",
                                encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    full = {(f, n) for f, n, _, _ in cm.scan(spec, repo)}
    assert full == {("a.txt", 1), ("notes.md", 1),
                    ("private/x.txt", 1), ("private/manual.md", 1)}
    pub = {(f, n) for f, n, _, _
           in cm.scan(spec, repo, publish_scope=True)}
    assert pub == {("a.txt", 1), ("private/manual.md", 1)}


def test_publish_exclude_permanent_strongest(tmp_path):
    """Постоянные исключения (publish_exclude_permanent, решение
    владельца 2026-08-29 «навсегда вне публикации»): отдельное поле,
    которое обычная ревизия publish_exclude_exact не трогает; область
    считается объединением exact+permanent, и permanent сильнее
    ВСЕГО, включая publish_keep."""
    spec = {
        "checks": [{"name": "п", "kind": "substring",
                    "values": ["synthmark"]}],
        "publish_exclude": ["private/"],
        "publish_exclude_exact": ["notes.md"],
        "publish_exclude_permanent": ["forever.md"],
        "publish_keep": ["forever.md"],       # keep НЕ воскрешает
    }
    assert not cm.in_publish_scope(spec, "forever.md")
    assert not cm.in_publish_scope(spec, "notes.md")   # объединение
    assert cm.in_publish_scope(spec, "a.txt")
    # ревизия exact (опустошение списка) постоянных не вычищает
    spec["publish_exclude_exact"] = []
    assert not cm.in_publish_scope(spec, "forever.md")

    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for rel in ("a.txt", "forever.md"):
        (repo / rel).write_text("маркер synthmark тут\n",
                                encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    pub = {(f, n) for f, n, _, _
           in cm.scan(spec, repo, publish_scope=True)}
    assert pub == {("a.txt", 1)}


def test_silent_without_marker_list(tmp_path):
    assert cm.load_checks(tmp_path / "нет.json") is None


def test_unknown_kind_refused():
    with pytest.raises(ValueError, match="kind"):
        cm._patterns({"kind": "чудо", "values": ["x"]})


@pytest.mark.skipif(
    not os.environ.get("CUSTOMER_MARKERS_ENFORCE_REPO"),
    reason="строгий линт — только в потоке публикации "
           "(CUSTOMER_MARKERS_ENFORCE_REPO=<worktree среза>)")
def test_publication_slice_is_clean():
    """Строгий гейт публикации: в срезе — ноль маркеров заказчика."""
    repo = Path(os.environ["CUSTOMER_MARKERS_ENFORCE_REPO"])
    spec = cm.load_checks()
    assert spec, "перечень маркеров обязателен в потоке публикации"
    findings = cm.scan(spec, repo)
    listing = "\n".join(f"{f}:{n} [{m}] {c}"
                        for f, n, m, c in findings[:40])
    assert not findings, f"маркеры заказчика в срезе:\n{listing}"
