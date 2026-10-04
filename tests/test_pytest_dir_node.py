# -*- coding: utf-8 -*-
"""Свидетель обвязки «один узел папки на прогон» (tests/_pytest_dir_node.py).

Стенд — мини-дерево на штатном плагине pytester: подпапка с фикстурой,
файл в корне дерева, второй файл подпапки. Порядок «подпапка → папка →
подпапка» — ровно тот, что 09.09.2026 давал «fixture 'service' not
found» в tests/catalog_service.

Свидетель двусторонний:

  * с обвязкой оба порядка зелёные, и совместимость «файл, названный
    дважды» не тронута;
  * БЕЗ обвязки тот же стенд обязан показать дефект — иначе стенд
    ничего не ловит. Красный отрицательный контроль после обновления
    pytest значит одно: pytest починил сборку сам, и обвязку вместе с
    этим свидетелем можно снять.

Закон берётся копией НАСТОЯЩЕГО файла tests/_pytest_dir_node.py: второй
редакции обвязки в дереве нет. Имя копии в мини-дереве другое нарочно —
модуль настоящего дерева уже загружен внешним прогоном, и совпадение
имён подсунуло бы его вместо копии.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

#: Носитель обвязки; в мини-дерево кладётся его исходный текст.
CARRIER = Path(__file__).resolve().parent / "_pytest_dir_node.py"
#: Имя копии в мини-дереве — заведомо не занятое внешним прогоном.
COPY_NAME = "_dir_node_under_test"

TREE_CONFTEST = "from %s import pytest_collect_directory  # noqa: F401\n" \
                % COPY_NAME
SUB_CONFTEST = """\
import pytest


@pytest.fixture()
def probe():
    return "present"
"""
USES_PROBE = """\
def test_it(probe):
    assert probe == "present"
"""
NO_FIXTURES = """\
def test_it():
    assert True
"""
#: Порядок «подпапка → папка → подпапка» — тот, что был красным.
ORDER = ("sub/test_a.py", "test_b.py", "sub/test_c.py")


def _tree(pytester: pytest.Pytester, *, hook: bool) -> None:
    """Мини-дерево в рабочей папке pytester; hook=False — без обвязки."""
    pytester.makeini("[pytest]\n")
    pytester.makepyfile(**{
        "sub/conftest.py": SUB_CONFTEST,
        "sub/test_a.py": USES_PROBE,
        "test_b.py": NO_FIXTURES,
        "sub/test_c.py": USES_PROBE,
    })
    if hook:
        (pytester.path / (COPY_NAME + ".py")).write_text(
            CARRIER.read_text(encoding="utf-8"), encoding="utf-8")
        pytester.makeconftest(TREE_CONFTEST)


def test_с_обвязкой_фикстуры_подпапки_видны_в_любом_порядке(pytester):
    _tree(pytester, hook=True)
    for order in (ORDER, tuple(reversed(ORDER))):
        res = pytester.runpytest(*order)
        res.assert_outcomes(passed=3, errors=0, failed=0)
    print("порядков проверено: 2 (по 3 passed)")


def test_с_обвязкой_повтор_файла_в_строке_не_ломается(pytester):
    """Совместимость pytest «файл, названный дважды, не объединяется»
    обвязка не трогает: повтор pytest сводит сам, как и без неё."""
    _tree(pytester, hook=True)
    res = pytester.runpytest(ORDER[0], ORDER[0])
    res.assert_outcomes(passed=1, errors=0, failed=0)


def test_без_обвязки_стенд_воспроизводит_дефект(pytester):
    """Отрицательный контроль: стенд обязан ловить то, что судит."""
    _tree(pytester, hook=False)
    res = pytester.runpytest(*ORDER)
    res.stdout.fnmatch_lines(["*fixture 'probe' not found*"])
    outcomes = res.parseoutcomes()
    assert outcomes.get("errors", 0) == 1, (
        "pytest %s больше не воспроизводит дефект на стенде — обвязку "
        "tests/_pytest_dir_node.py и этот свидетель можно снять: %s"
        % (pytest.__version__, outcomes))
    print("без обвязки: %s" % outcomes)


def test_без_обвязки_пара_файлов_остаётся_зелёной(pytester):
    """Дефект только у тройки: пара «подпапка → подпапка» зелена и без
    обвязки — стенд ловит именно порядок, а не поломанное дерево."""
    _tree(pytester, hook=False)
    res = pytester.runpytest(ORDER[0], ORDER[2])
    res.assert_outcomes(passed=2, errors=0, failed=0)
