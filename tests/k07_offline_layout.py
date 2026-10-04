"""Load only the reviewed declarative layout fixture, not global conftest.

Used with --noconftest through the network/process-blocking offline runner.
No conftest imports, top-level actions or other autouse fixtures are executed.
The original fixture supplies temporary test-only roles and the normal LLM
test seam; network/process guards remain active.
"""
import ast
import os
from pathlib import Path
import re

import pytest


_source = Path(__file__).with_name("conftest.py")
_tree = ast.parse(_source.read_text(encoding="utf-8"))
_fixture = next(n for n in _tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "declared_test_layout")
exec(compile(ast.Module(body=[_fixture], type_ignores=[]),
             str(_source), "exec"), globals())


@pytest.fixture(autouse=True)
def code_identity_without_child_process(monkeypatch):
    """The caller reads exact git HEAD before the guarded Python process.

    Only passport metadata transport is replaced. No receipt or product
    verdict is mocked, and all subprocess/network attempts stay prohibited.
    """
    from cdpm import run_passport
    head = os.environ.get("K07_TEST_COMMIT", "")
    assert re.fullmatch(r"[0-9a-f]{40}", head), "provide verified git HEAD"
    run_passport._stable_environment.cache_clear()
    monkeypatch.setattr(run_passport, "_git_head",
                        lambda repo: {"commit": head, "short": head[:7]})
    yield
    run_passport._stable_environment.cache_clear()
