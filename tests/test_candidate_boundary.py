"""Граница доверия кандидатского кода (аудит R9, P0-04).

Класс дефекта: список запрещённых путей — не граница доверия. Каждый раунд
расширял список, а аудитор находил новый обход. R9 воспроизвёл сразу три:
перехватчик в каталоге, на который указывал наш же флаг; внешний файл
исключений, прячущий неотслеживаемый тест; биты индекса, скрывающие
изменение отслеживаемого теста.

Здесь проверяется ФОРМА: набор защитных флагов един и применяется на всех
вызовах (перепись по исходным текстам), путь перехватчиков лежит вне
дерева кандидата, а сам репозиторий кандидата проверяется как ИСТОЧНИК
опасности, а не только его рабочее дерево.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_hooks_path_never_resolves_inside_the_candidate_tree():
    """Путь перехватчиков — абсолютный и вне репозитория.

    Прежде он был относительным (`.git/hooks-disabled`), а такой путь
    разрешается от корня рабочего дерева — то есть внутри каталога, куда
    пишет кандидат. Каталога не существовало, и кандидату достаточно было
    создать его самому.
    """
    from webapp import evolution_runner as er

    flags = er._git_safe_flags()
    hooks = [f for f in flags if f.startswith("core.hooksPath=")]
    assert len(hooks) == 1
    path = Path(hooks[0].split("=", 1)[1])
    assert path.is_absolute(), "относительный путь разрешается внутри дерева"
    assert not (REPO in path.parents or path == REPO)
    assert path.is_dir() and not any(path.iterdir()), (
        "каталог перехватчиков обязан существовать и быть пустым: "
        "несуществующий кандидат создаёт сам")


def test_safe_flag_set_covers_every_known_execution_mechanism():
    """Набор гасит не только перехватчики.

    Соседи по классу — файловый монитор, драйверы фильтров, внешние файлы
    исключений и атрибутов — исполняют произвольную команду или скрывают
    изменение ровно так же.
    """
    from webapp import evolution_runner as er

    joined = " ".join(er._git_safe_flags())
    for key in ("core.hooksPath=", "core.fsmonitor=", "core.excludesFile=",
                "core.attributesFile=", "protocol.ext.allow=never"):
        assert key in joined, f"в наборе защитных флагов нет {key}"


def test_census_no_raw_git_invocation_outside_the_helper():
    """Перепись: прямых вызовов git мимо единой точки не существует.

    Это и есть доказательство закрытия класса. Прежде утверждение «хуки
    погашены на всех вызовах» было буквально неверным: два вызова —
    в модуле свидетельств и среди литеральных вызовов контура — шли мимо.
    """
    offenders = []
    pattern = re.compile(r'\[\s*"git"\s*,')
    for name in ("evolution_runner.py", "evidence.py", "change_requests.py",
                 "agent_broker.py", "build_identity.py"):
        p = REPO / "webapp" / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            tail = text[m.end():m.end() + 60]
            if "_git_safe_flags()" in tail:
                continue
            line = text[:m.start()].count("\n") + 1
            offenders.append(f"{name}:{line}")
    assert not offenders, (
        "прямые вызовы git без защитного набора флагов: "
        + ", ".join(offenders))


def test_census_detects_a_synthetic_raw_call():
    """Мета-проверка: перепись обязана ловить обход, а не просто быть
    зелёной."""
    pattern = re.compile(r'\[\s*"git"\s*,')
    assert pattern.search('_run(["git", "diff", "--raw"], cwd=repo)')


# ------------------------------------------- репозиторий как источник опасности

def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                   cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)


@pytest.mark.parametrize("key,value", [
    ("core.excludesFile", ".cache/hide"),
    ("core.fsmonitor", "evil.cmd"),
    ("core.attributesFile", ".cache/attrs"),
    ("filter.evil.clean", "evil.cmd"),
])
def test_repo_config_hazards_are_detected(tmp_path, key, value):
    """Опасная настройка в репозитории кандидата — сама по себе опасность.

    Гашение флагами закрывает НАШИ вызовы; настройка остаётся доступной
    любому другому инструменту, работающему с тем же репозиторием.
    """
    from webapp import evolution_runner as er

    repo = tmp_path / "cand"
    _init_repo(repo)
    subprocess.run(["git", "config", key, value], cwd=repo, check=True)
    hazards = er._repo_config_hazards(repo)
    assert any(key.lower() in h.lower() for h in hazards), hazards


def test_assume_unchanged_bit_is_detected(tmp_path):
    """Бит, скрывающий изменение отслеживаемого файла, обязан быть виден.

    Иначе изменённый тест исполняется, а состояние дерева выглядит
    чистым — обход, воспроизведённый аудитором.
    """
    from webapp import evolution_runner as er

    repo = tmp_path / "cand"
    _init_repo(repo)
    subprocess.run(["git", "update-index", "--assume-unchanged", "a.txt"],
                   cwd=repo, check=True)
    hazards = er._repo_config_hazards(repo)
    assert any("индекс скрывает" in h for h in hazards), hazards


def test_clean_repo_has_no_hazards(tmp_path):
    """Противовес: чистый репозиторий не должен давать ложных срабатываний,
    иначе проверку отключат первой же."""
    from webapp import evolution_runner as er

    repo = tmp_path / "cand"
    _init_repo(repo)
    assert er._repo_config_hazards(repo) == []
