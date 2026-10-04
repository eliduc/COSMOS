"""Reported sole-cause carriers and direct resync neighbor: diagnostics only.

The five web UI cases execute exact, unchanged production Raise AST nodes
with prepared reasons and the real HTTPException class. This proves status
and detail formatting, NOT HTTP routing, reconsideration, a cycle gate, live
operation or semantic judging. A further composition uses the exact
_sync_canon Return before the resync/migration Raise, still with prepared reasons,
not real validators. The ASM cases call the real in-memory parser
and its real block validation, with no validator/formatter substitution.
Nothing imports cdpm.webui, starts an app, applies a delta or writes a journal.
These are noncritical diagnostics, not new full IDs or renewed acceptance.
"""
import ast
import copy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HTTP_CASES = (
    ("reconsider_question", 500, "problems",
     "вопрос-пересмотр не проходит контракт: "),
    ("resync_exchange_canon", 409, "problems",
     "гейт цикла красный — канон не трогаю: "),
    # The original b1d BASE had two clips: _sync_canon problems[:5], then resync
    # joins sync_problems[:4]. The composition below covers their connection.
    ("resync_exchange_canon", 409, "sync_problems", ""),
    ("apply_kind_migrations", 409, "reasons_all",
     "миграции не применены (всё-или-ничего): "),
    ("apply_kind_migrations", 409, "sync_problems", ""),
)


def _exact_http_raise(owner, status, variable, prefix):
    path = ROOT / "cdpm/webui.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == owner]
    assert len(functions) == 1, owner
    found = [node for node in ast.walk(functions[0])
             if isinstance(node, ast.Raise)
             and isinstance(node.exc, ast.Call)
             and isinstance(node.exc.func, ast.Name)
             and node.exc.func.id == "HTTPException"
             and len(node.exc.args) == 2
             and isinstance(node.exc.args[0], ast.Constant)
             and node.exc.args[0].value == status
             and any(isinstance(part, ast.Name) and part.id == variable
                     for part in ast.walk(node.exc.args[1]))
             and any(isinstance(part, ast.Constant)
                     and isinstance(part.value, str)
                     and part.value.startswith(prefix)
                     for part in ast.walk(node.exc.args[1]))]
    assert len(found) == 1, (owner, status, variable, prefix)
    # Compile the actual node, not a reconstructed/replaced raise or join.
    return compile(ast.Module(body=[found[0]], type_ignores=[]), str(path), "exec")


@pytest.mark.parametrize("owner,status,variable,prefix", HTTP_CASES,
                         ids=("question-revision-500", "cycle-resync-409",
                              "cycle-canon-sync-409", "kind-migrations-rejections-409",
                              "kind-migrations-sync-409"))
@pytest.mark.parametrize("count", [1, 15], ids=("short-control", "full-tail"))
def test_exact_http_exception_keeps_status_and_every_reason(owner, status, variable, prefix, count):
    from fastapi.exceptions import HTTPException

    reasons = [f"R-DIAG-{i:02}: причина {i:02} целиком" for i in range(count)]
    before = list(reasons)
    code = _exact_http_raise(owner, status, variable, prefix)
    with pytest.raises(HTTPException) as caught:
        exec(code, {"HTTPException": HTTPException, variable: reasons})
    assert type(caught.value) is HTTPException
    assert caught.value.status_code == status
    assert caught.value.detail == prefix + "; ".join(reasons)
    assert caught.value.headers is None
    assert reasons == before


def _exact_sync_return():
    path = ROOT / "cdpm/webui.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    owners = [node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name == "_sync_canon"]
    assert len(owners) == 1
    returns = [node for node in ast.walk(owners[0])
               if isinstance(node, ast.Return)
               and isinstance(node.value, ast.ListComp)
               and any(isinstance(part, ast.Name) and part.id == "problems"
                       for part in ast.walk(node.value))]
    assert len(returns) == 1
    actual = returns[0]
    before = ast.dump(actual, include_attributes=True)
    # A Return requires a function frame. Only that frame is synthetic;
    # its sole body node is the unmodified production Return.
    frame = ast.FunctionDef(
        name="return_carrier", args=ast.arguments(posonlyargs=[], args=[],
            vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
        body=[actual], decorator_list=[], returns=None, type_comment=None)
    if "type_params" in ast.FunctionDef._fields:
        frame.type_params = []
    ast.copy_location(frame, actual)
    program = ast.fix_missing_locations(ast.Module(body=[frame], type_ignores=[]))
    assert ast.dump(actual, include_attributes=True) == before
    return compile(program, str(path), "exec")


@pytest.mark.parametrize("consumer,origin", (
    ("resync_exchange_canon", "пересинхронизация из exchange"),
    ("apply_kind_migrations", "операторские миграции вида"),
), ids=("resync", "kind-migrations"))
@pytest.mark.parametrize("count", [1, 15], ids=("short-control", "full-tail"))
def test_exact_sync_return_then_consumer_raise_preserves_every_reason(count, consumer, origin):
    from fastapi.exceptions import HTTPException

    reasons = [f"R-SYNC-{i:02}: готовая причина {i:02} целиком" for i in range(count)]
    before = list(reasons)
    namespace = {"problems": reasons, "origin": origin}
    exec(_exact_sync_return(), namespace)
    sync_problems = namespace["return_carrier"]()
    # The actual Return output feeds the actual Raise directly; it is not
    # replaced by the expected full list, so either clip breaks composition.
    code = _exact_http_raise(consumer, 409, "sync_problems", "")
    with pytest.raises(HTTPException) as caught:
        exec(code, {"HTTPException": HTTPException, "sync_problems": sync_problems})
    expected = [f"{origin}: финальная модель не проходит контракт: " + reason
                for reason in reasons]
    assert caught.value.status_code == 409 and caught.value.headers is None
    assert (sync_problems, caught.value.detail) == (expected, "; ".join(expected))
    assert reasons == before


@pytest.fixture
def schedule_parser(monkeypatch):
    # registry's first import resolves FINAL_REPORT and otherwise mkdirs it.
    # Resolve the actual path without writing or caching a temporary path.
    # The parser and exception formatting themselves remain untouched.
    import _data_paths
    original_results_dir = _data_paths.results_dir
    with monkeypatch.context() as import_guard:
        import_guard.setattr(_data_paths, "results_dir",
                             lambda create=True: original_results_dir(create=False))
        from webapp import schedule_deltas
    return schedule_deltas


def _parser_payload():
    # parse_asm only reads schedule years. This is not a claim that this
    # minimal parser input is a complete validated canonical payload.
    return {"flights": [{"flightNumber": "SU100",
                         "departureUTCDateTime": "2026-03-01T08:00:00"}]}


@pytest.mark.parametrize("count", [1, 15], ids=("short-control", "full-tail"))
def test_real_asm_parser_reports_all_invalid_blocks_without_partial_result(schedule_parser, count):
    sd = schedule_parser
    payload = _parser_payload()
    before = copy.deepcopy(payload)
    # A real healthy first action must not turn the following malformed
    # blocks into a partial successful result. Every invalid block has an
    # explicit year and a known action, so the failure is the missing time.
    blocks = ["CNL\nSU100/01MAR26"]
    blocks += [f"TIM\nSU{200 + i}/01MAR26" for i in range(count)]
    expected = [f"блок {i + 2}: TIM ожидает строку 'HHMM HHMM'"
                for i in range(count)]
    text = "\n\n".join(blocks)
    with pytest.raises(sd.DeltaError) as caught:
        sd.parse_asm(text, payload)
    assert type(caught.value) is sd.DeltaError
    assert str(caught.value) == "; ".join(expected)
    assert payload == before


@pytest.mark.parametrize("count", [1, 15], ids=("one-action", "many-actions"))
def test_real_asm_parser_preserves_complete_healthy_actions(schedule_parser, count):
    sd = schedule_parser
    payload = _parser_payload()
    before = copy.deepcopy(payload)
    text = "\n\n".join(f"ASM\nTIM\nSU{100 + i}/01MAR26\n0830 1030"
                         for i in range(count))
    expected = [{"action": "TIM", "flightNumber": f"SU{100 + i}",
                 "date": "2026-03-01", "dep_hhmm": "0830", "arr_hhmm": "1030"}
                for i in range(count)]
    assert sd.parse_asm(text, payload) == expected
    assert payload == before
