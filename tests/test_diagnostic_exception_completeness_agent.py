"""Five bounded sole-exception diagnostic carriers must keep every reason.

The AST cases execute the exact production Raise nodes, unchanged, with 15
prepared reasons. They prove exception formatting only: not reachability,
validator correctness, extraction, a whole pipeline, a judge, or a full ID.
The source-check cases use the real compile_checks parser/dictionary checks;
only its transport supplies synthetic JSON. No reader/LLM, socket, SMT, JVM,
source document, demo, or independent semantic oracle is exercised here.
"""
import ast
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
# These are the five reported locations, not a census of every list slice.
# Message prefixes locate exact Raise nodes without binding to line numbers.
CASES = (
    ("cdpm/extract.py", "run_extract", "ExtractError",
     "собранная модель не прошла инварианты контракта — модель не записана: "),
    ("cdpm/extract.py", "run_extract", "ExtractError",
     "модель после ремонта по находкам кода не прошла инварианты — не записана: "),
    ("cdpm/extract.py", "run_extract", "ExtractError",
     "модель с вопросами по остатку баланса / слою 2 не прошла инварианты — не записана: "),
    ("cdpm/extract.py", "produce_logic", "ExtractError",
     "модель после целевого прохода logic не прошла инварианты — не записана: "),
    ("webapp/source_check_compile.py", "compile_checks", "ValueError",
     "читатель не дал ни одной проверки словаря; проблемы: "),
)


def _production_raise(path, owner, exception_name, prefix):
    """Read source, not import/run the surrounding extraction pipeline."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == owner]
    assert len(functions) == 1, (str(path), owner)
    candidates = [node for node in ast.walk(functions[0])
                  if isinstance(node, ast.Raise)
                  and isinstance(node.exc, ast.Call)
                  and isinstance(node.exc.func, ast.Name)
                  and node.exc.func.id == exception_name
                  and any(isinstance(part, ast.Constant)
                          and isinstance(part.value, str)
                          and part.value.startswith(prefix)
                          for part in ast.walk(node.exc))]
    assert len(candidates) == 1, (str(path), owner, prefix)
    # The actual ExtractError definition is also used unchanged. Its body is
    # only the production docstring; no extractor dependency is imported.
    classes = [node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == exception_name]
    if exception_name == "ExtractError":
        assert len(classes) == 1
        assert all(isinstance(node, ast.Expr)
                   and isinstance(node.value, ast.Constant)
                   and isinstance(node.value.value, str)
                   for node in classes[0].body)
    else:
        assert exception_name == "ValueError" and not classes
    return classes, candidates[0]


@pytest.mark.parametrize("relative,owner,exception_name,prefix", CASES,
                         ids=("assembled-model", "repaired-model",
                              "balance-and-second-reading", "logic-pass",
                              "source-check-empty-specs"))
def test_exact_production_exception_keeps_all_fifteen_reasons(
        relative, owner, exception_name, prefix):
    path = ROOT / relative
    classes, node = _production_raise(path, owner, exception_name, prefix)
    reasons = [f"R-DIAG-{i:02}: готовая причина {i:02} целиком"
               for i in range(15)]
    before = list(reasons)
    namespace = {"problems": reasons, "__name__": "isolated_diagnostic_raise"}
    if classes:
        exec(compile(ast.Module(body=classes, type_ignores=[]), str(path), "exec"),
             namespace)
    # No replacement lambda, AST rewrite, validator stub, or copied join:
    # this is precisely the Raise node read from the production function.
    program = compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec")
    with pytest.raises(ValueError) as caught:
        exec(program, namespace)
    assert type(caught.value).__name__ == exception_name
    assert str(caught.value) == prefix + "; ".join(reasons)
    assert reasons == before


@pytest.fixture
def offline_source_compiler(monkeypatch):
    # llm_backend imports registry, whose first import creates results_dir.
    # Resolve its real path without incidental mkdir or a cached temporary
    # path leaking into later tests. This is not a compiler/validator seam.
    monkeypatch.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    import _data_paths
    original_results_dir = _data_paths.results_dir
    with monkeypatch.context() as import_guard:
        import_guard.setattr(_data_paths, "results_dir",
                             lambda create=True: original_results_dir(create=False))
        from webapp import llm_backend, source_check_compile

    previous_usage = llm_backend.peek_last_usage()

    def forbidden(*args, **kwargs):
        raise AssertionError("No real source-check transport is allowed")

    for name in ("_cli_generate", "_api_generate", "_ollama_generate"):
        monkeypatch.setattr(llm_backend, name, forbidden)

    calls = []

    def use_reply(rows):
        raw = json.dumps(rows, ensure_ascii=False)

        def complete(prompt, *, backend, usage, **kwargs):
            assert backend == "local" and usage == "source_check"
            calls.append(prompt)
            return {"ok": True, "text": raw, "backend": backend,
                    "model": "offline-json-fixture", "usage": None}

        monkeypatch.setattr(llm_backend, "complete", complete)

    try:
        yield source_check_compile, use_reply, calls
    finally:
        llm_backend.set_last_usage(previous_usage)


def _invalid_specs():
    rows = [{"id": f"bad-{i:02}", "kind": f"unknown-kind-{i:02}",
             "anchor": "A1", "params": {}} for i in range(15)]
    expected = [f"[{i}] вид 'unknown-kind-{i:02}' вне словаря"
                for i in range(15)]
    return rows, expected


CLAUSES = [{"sid": "S-DIAG", "anchor": "A1",
            "text": "Каждое место рейса закрыто сотрудником с требуемой ролью."}]


def test_real_source_compiler_keeps_all_closed_dictionary_rejections(
        offline_source_compiler):
    compiler, use_reply, calls = offline_source_compiler
    rows, expected = _invalid_specs()
    assert all(row["kind"] not in compiler.CHECK_KINDS for row in rows)
    use_reply(rows)
    with pytest.raises(ValueError) as caught:
        compiler.compile_checks(CLAUSES, scope="local-only", requested="local",
                                channel="local")
    assert len(calls) == 1
    assert str(caught.value) == CASES[-1][3] + "; ".join(expected)


def test_real_source_compiler_preserves_healthy_spec_and_rejection_list(
        offline_source_compiler):
    compiler, use_reply, calls = offline_source_compiler
    rows, expected = _invalid_specs()
    healthy = {"id": "healthy", "kind": "skill_required", "anchor": "A1",
               "params": {}, "note": "Требуемая роль."}
    assert healthy["kind"] in compiler.CHECK_KINDS
    use_reply(rows + [healthy])
    got = compiler.compile_checks(CLAUSES, scope="local-only", requested="local",
                                  channel="local")
    assert len(calls) == 1
    assert got["specs"] == [healthy]
    assert got["problems"] == expected
    assert got["backend"] == got["channel"] == "local"
