"""K02/T02: browser API behavior and explicit parity with both Python doors.

No live app, network, LLM, or process stages: Node replaces fetch before calls;
Python reads webui syntax without importing or starting it. Node >=22.18 needed.
"""
import ast
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "cdpm-ui"


def test_real_browser_api_refuses_before_network_and_preserves_valid_bounds():
    node = shutil.which("node")
    assert node, "Node >=22.18 is required for the CDPM UI regression profile"
    result = subprocess.run([node, "--test", "src/api.test.mjs"], cwd=UI,
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("door,param,prefix", [
    ("start_exchange", "max_rounds", "EXCHANGE_ROUNDS"),
    ("start_full_cycle", "max_rounds", "EXCHANGE_ROUNDS"),
    ("start_full_cycle", "max_megacycles", "FULL_CYCLES"),
])
def test_browser_bounds_equal_each_server_door(door, param, prefix):
    api = (UI / "src/api.ts").read_text(encoding="utf-8")
    values = [int(re.search(rf"export const {prefix}_{suffix} = ([0-9]+)", api)[1])
              for suffix in ("MIN", "MAX")]
    tree = ast.parse((ROOT / "cdpm/webui.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == door)
    checks = [n for n in ast.walk(fn) if isinstance(n, ast.Compare)
              and len(n.comparators) == 2 and isinstance(n.comparators[0], ast.Name)
              and n.comparators[0].id == param]
    assert len(checks) == 1
    check = checks[0]
    assert all(isinstance(op, ast.LtE) for op in check.ops)
    assert [ast.literal_eval(check.left), ast.literal_eval(check.comparators[1])] == values


@pytest.mark.parametrize("field,prefix", [
    ("maxRounds", "EXCHANGE_ROUNDS"), ("maxMegacycles", "FULL_CYCLES"),
])
def test_fields_reference_the_validated_api_bounds(field, prefix):
    page = (UI / "src/pages/RunPage.tsx").read_text(encoding="utf-8")
    inputs = re.findall(r"<input\b[\s\S]*?/>", page)
    matching = [item for item in inputs if "value={" + field + "}" in item]
    assert len(matching) == 1
    assert "min={" + prefix + "_MIN}" in matching[0]
    assert "max={" + prefix + "_MAX}" in matching[0]
    assert "step={1}" in matching[0]


def test_whole_pipeline_checks_limits_before_extracting():
    page = (UI / "src/pages/RunPage.tsx").read_text(encoding="utf-8")
    body = page.split("const launchWhole = async () => {", 1)[1].split("useEffect(", 1)[0]
    assert body.index("validateCycleLimits(maxRounds, maxMegacycles)") < body.index("await launch()")
    assert re.search(r"catch \(e\)\s*\{\s*notify.error\(String\(e\)\)\s*return", body)
