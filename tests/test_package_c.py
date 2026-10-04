"""Пакет C, часть, реализуемая кодом (аудит R9, P0-04).

Две меры из пакета C не требуют инфраструктуры и делаются здесь:

1. **Материализация ревизии без кандидатского `.git`.** Перехватчики,
   драйверы фильтров, файловый монитор и биты индекса живут в `.git`.
   Пока проверка перечисляет запрещённые пути, аудитор каждый раунд
   находит новый обход; когда каталога нет физически, обходить нечего.
   Отдельно проверяется перенос НЕОТСЛЕЖИВАЕМЫХ настроек: без него
   материализованное дерево считает по встроенным умолчаниям вместо
   правил заказчика — прямая угроза переданным числам.

2. **Контрподпись человека на продвижение.** Автоматика не может выписать
   её себе: подпись считается по секрету, которого нет ни в репозитории,
   ни в дереве кандидата, и привязана к тройке «правка, срез, фаза».

Полная независимость гейта требует отдельного держателя ключа вне машины
— это остаток пакета C, и он назван в реестре инвариантов честно.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from webapp import evolution_runner as er


def _repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                   cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (path / "webapp").mkdir(exist_ok=True)
    (path / "webapp" / "config").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


# ------------------------------------------------- материализация ревизии

def test_materialized_tree_has_no_candidate_git(tmp_path):
    """В разложенном дереве нет каталога `.git` — обходить нечего."""
    repo = tmp_path / "cand"
    sha = _repo(repo)
    info = er.materialize(repo, sha, tmp_path / "work")
    assert not (tmp_path / "work" / ".git").exists()
    assert info["has_git"] is False
    assert (tmp_path / "work" / "app.py").read_text(encoding="utf-8")


def test_candidate_hooks_do_not_survive_materialization(tmp_path):
    """Перехватчик, положенный кандидатом, в разложенное дерево не
    попадает: он лежит в `.git`, которого там нет."""
    repo = tmp_path / "cand"
    sha = _repo(repo)
    hooks = repo / ".git" / "hooks-disabled"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "post-checkout").write_text("echo pwned", encoding="utf-8")
    er.materialize(repo, sha, tmp_path / "work")
    assert not (tmp_path / "work" / ".git").exists()


def test_untracked_runtime_config_is_carried_explicitly(tmp_path):
    """Неотслеживаемые настройки переносятся ЯВНО и пересчитываются.

    Без переноса дерево из ревизии не содержит файла настроек
    авиакомпании, переменная пути к правилам дочернему процессу не
    выставляется, и движки берут ВСТРОЕННЫЕ умолчания — то есть замер
    идёт по чужим правилам.
    """
    repo = tmp_path / "cand"
    sha = _repo(repo)
    cfg = repo / "webapp" / "config" / "airline_default.json"
    cfg.write_text('{"parameters": {"min_sit_min": 30}}', encoding="utf-8")
    info = er.materialize(repo, sha, tmp_path / "work")
    dst = tmp_path / "work" / "webapp" / "config" / "airline_default.json"
    assert dst.exists(), "настройки не перенесены — расчёт пойдёт по умолчаниям"
    assert dst.read_text(encoding="utf-8") == cfg.read_text(encoding="utf-8")
    assert "webapp/config/airline_default.json" in info["carried_runtime"]
    assert info["missing_runtime"] == []


def test_absent_runtime_config_is_reported_not_hidden(tmp_path):
    """Отсутствие настроек называется, а не замалчивается."""
    repo = tmp_path / "cand"
    sha = _repo(repo)
    info = er.materialize(repo, sha, tmp_path / "work")
    assert info["carried_runtime"] == []
    assert "webapp/config/airline_default.json" in info["missing_runtime"]


def test_materialize_refuses_unknown_revision(tmp_path):
    repo = tmp_path / "cand"
    _repo(repo)
    with pytest.raises(er.MaterializeError):
        er.materialize(repo, "0" * 40, tmp_path / "work")


# ------------------------------------------------------- контрподпись

def test_promotion_without_secret_is_refused(monkeypatch):
    """Нет секрета — нет продвижения. Автоматика не может его придумать."""
    monkeypatch.delenv(er.COUNTERSIGN_ENV, raising=False)
    with pytest.raises(er.CountersignatureRequired):
        er.countersign_expected(1, "abc", "stage")


def test_promotion_without_token_is_refused(monkeypatch):
    monkeypatch.setenv(er.COUNTERSIGN_ENV, "s3cret")
    with pytest.raises(er.CountersignatureRequired):
        er.verify_countersignature(1, "abc", "stage", None)


def test_valid_countersignature_passes(monkeypatch):
    monkeypatch.setenv(er.COUNTERSIGN_ENV, "s3cret")
    tok = er.countersign_expected(7, "deadbeef", "stage")
    er.verify_countersignature(7, "deadbeef", "stage", tok)


def test_countersignature_is_bound_to_the_revision(monkeypatch):
    """Подтверждение одного среза не подходит другому: «однажды
    разрешили» не превращается в постоянный доступ."""
    monkeypatch.setenv(er.COUNTERSIGN_ENV, "s3cret")
    tok = er.countersign_expected(7, "deadbeef", "stage")
    with pytest.raises(er.CountersignatureRequired):
        er.verify_countersignature(7, "cafebabe", "stage", tok)


def test_countersignature_is_bound_to_the_phase(monkeypatch):
    """Подтверждение внутреннего контура не открывает боевой."""
    monkeypatch.setenv(er.COUNTERSIGN_ENV, "s3cret")
    tok = er.countersign_expected(7, "deadbeef", "stage")
    with pytest.raises(er.CountersignatureRequired):
        er.verify_countersignature(7, "deadbeef", "prod", tok)


def test_countersignature_is_bound_to_the_improvement(monkeypatch):
    monkeypatch.setenv(er.COUNTERSIGN_ENV, "s3cret")
    tok = er.countersign_expected(7, "deadbeef", "prod")
    with pytest.raises(er.CountersignatureRequired):
        er.verify_countersignature(8, "deadbeef", "prod", tok)


def test_secret_is_not_stored_in_the_repository():
    """Секрет не должен попасть в репозиторий ни в каком виде."""
    repo = Path(__file__).resolve().parents[1]
    for rel in ("webapp/evolution_runner.py", "tools/countersign.py"):
        text = (repo / rel).read_text(encoding="utf-8")
        assert "COSMOS_PROMOTION_SECRET" in text        # имя переменной — да
        assert "secret =" not in text.replace(
            'secret = os.environ.get(COUNTERSIGN_ENV) or ""', "")
