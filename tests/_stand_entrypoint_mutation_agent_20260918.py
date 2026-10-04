"""In-process, single-call SPEC forwarding mutations for bounded stand tests.

No files are changed. Only the selected original function's code object is
replaced, using its exact source AST minus ONE **spec_version keyword. Original
globals/defaults/annotations remain in place; pytest teardown restores the code.
This is sensitivity evidence, not a solver run or independent acceptance.
Pins normalize CRLF to LF only, allowing a normal Windows frozen checkout;
the observed raw source SHA is printed separately for exact-byte provenance.
"""
import ast
import copy
import hashlib
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "tests" / "test_stand_spec_entrypoint_agent.py"
PINS = {
    "vmp/stand_tasks.py": "538dc4e9657ec325dbcd08b92423d750900854026491a4afca4a6f3dbdf5399e",
    "vmp/stand_solver.py": "d334f5d4971aea171448f944a31408206f442f295d763db102ad4c34bb15de95",
    "tests/test_stand_spec_entrypoint_agent.py": "9b14be8f8e39a9bda37f2cb822edb761e0ceccda4cdbb2216d0c2132af1f93a2",
}
CLI_TEST = "test_cli_selected_spec_reaches_real_zip_before_solver"
METRIC_TEST = "test_metric_spec_forwarding_seam_without_execution"
MODES = {
    "judge-measure": ("vmp/stand_tasks.py", "_judge_with_solver", "ss.measure", CLI_TEST),
    "measure-metric": ("vmp/stand_solver.py", "measure", "_measure_metric", METRIC_TEST),
    "world-package": ("vmp/stand_solver.py", "run_world", "package_bytes", CLI_TEST),
}
_PATCHED = []


def _require(condition, words):
    if not condition:
        raise pytest.UsageError("stand entrypoint mutation: " + words)


def _dump(node):
    return ast.dump(node, include_attributes=False)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pytest_collection_modifyitems(items):
    mode = os.environ.get("STAND_ENTRYPOINT_MUTATION")
    _require(mode in {"control", *MODES}, "select control/judge-measure/measure-metric/world-package")
    _require(not _PATCHED, "only one mutation per pytest process")
    sources, raw_hashes = {}, {}
    for relative, wanted in PINS.items():
        raw = (ROOT / relative).read_bytes()
        _require(_sha(raw.replace(b"\r\n", b"\n")) == wanted,
                 "LF-normalized source SHA changed: " + relative)
        raw_hashes[relative] = _sha(raw)
        sources[relative] = raw.decode("utf-8")
    _require(items and all(Path(item.module.__file__).resolve() == TEST for item in items),
             "select only the pinned entrypoint test file")
    _require(all(getattr(item, "originalname", "") in (CLI_TEST, METRIC_TEST) for item in items),
             "select only CLI composition or metric forwarding cases")
    if mode == "control":
        print("stand entrypoint sensitivity control: exact pinned sources; no mutation")
        return

    relative, function_name, callee, test_name = MODES[mode]
    _require(len(items) == 3 and all(item.originalname == test_name for item in items),
             "select all three modern/default cases of " + test_name)
    _require({item.callspec.params["revision"] for item in items} == {None, "1.20", "1.21"},
             "both modern revisions and the healthy default must be selected")
    parsed = ast.parse(sources[relative], filename=str(ROOT / relative))
    functions = [node for node in parsed.body
                 if isinstance(node, ast.FunctionDef) and node.name == function_name]
    _require(len(functions) == 1, "expected exactly one top-level target function")
    original_ast = functions[0]
    _require(not original_ast.decorator_list, "decorated target is outside this experiment")
    mutant = copy.deepcopy(original_ast)
    target_callee = _dump(ast.parse(callee, mode="eval").body)
    calls = [node for node in ast.walk(mutant)
             if isinstance(node, ast.Call) and _dump(node.func) == target_callee]
    _require(len(calls) == 1, "expected exactly one target call in selected function")
    expression = ('{"spec_version": ctx["spec_version"]} '
                  'if ctx.get("spec_version") is not None else {}') if mode == "judge-measure" else (
                  '{"spec_version": spec_version} if spec_version is not None else {}')
    expected = _dump(ast.parse(expression, mode="eval").body)
    keywords = [kw for kw in calls[0].keywords
                if kw.arg is None and _dump(kw.value) == expected]
    _require(len(keywords) == 1, "exact conditional SPEC forwarding keyword not found")
    calls[0].keywords.remove(keywords[0])
    _require(_dump(original_ast.args) == _dump(mutant.args), "function signature changed")
    _require(_dump(original_ast) != _dump(mutant), "mutation must change the target AST")

    from vmp import stand_solver, stand_tasks
    module = stand_tasks if relative.endswith("stand_tasks.py") else stand_solver
    _require(Path(module.__file__).resolve() == ROOT / relative, "wrong imported product path")
    function = getattr(module, function_name)
    _require(function.__globals__ is module.__dict__, "target is not the original module function")
    _require(not function.__code__.co_freevars, "closures are outside this experiment")
    # Exec defines only this function in a private local namespace. Its globals
    # are the actual module so existing monkeypatch boundaries keep working.
    tree = ast.Module(body=[mutant], type_ignores=[])
    ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, "<stand-entrypoint-mutation:" + mode + ">", "exec"),
         module.__dict__, namespace)
    replacement = namespace[function_name]
    _require(not replacement.__code__.co_freevars, "unexpected replacement closure")
    _require(function.__code__.co_argcount == replacement.__code__.co_argcount
             and function.__code__.co_kwonlyargcount == replacement.__code__.co_kwonlyargcount,
             "replacement argument shape changed")
    _PATCHED.append((function, function.__code__))
    function.__code__ = replacement.__code__
    print("stand entrypoint sensitivity mutation=" + mode
          + "; function=" + relative + ":" + function_name
          + "; removed_keyword_at_line=" + str(keywords[0].lineno)
          + "; source_raw_sha=" + raw_hashes[relative]
          + "; source_lf_sha=" + PINS[relative]
          + "; ast_before_sha=" + _sha(_dump(original_ast).encode("utf-8"))
          + "; ast_after_sha=" + _sha(_dump(mutant).encode("utf-8")))


def pytest_unconfigure(config):
    while _PATCHED:
        function, original_code = _PATCHED.pop()
        function.__code__ = original_code
