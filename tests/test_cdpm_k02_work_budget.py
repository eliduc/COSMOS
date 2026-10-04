"""The complexity guard must distinguish actual historical algorithms."""
import inspect

import pytest

from cdpm import family_params as fp
from _k02_work_budget import WorkBudgetExceeded, k02_work_budget


def _mutant(source, name):
    # Compile only the explicitly named local function; no files are changed.
    namespace = dict(vars(fp))
    exec(compile(source, fp.__file__, "exec"), namespace)
    return namespace[name]


def test_budget_detects_digit_times_span_scan(k02_work_budget, monkeypatch):
    raw = "3 " + "1 " * 2000
    with k02_work_budget(raw) as healthy:
        assert fp.parse_value(raw, "count", fp.default_domain("count")) is None
    assert healthy.counts["python_lines"] > 0
    assert healthy.counts["regex_scan"] > 0
    source = '''def _digits_covered(s, spans):
    return all(any(a <= d.start() < b for a, b in spans)
               for d in _DIGIT_RE.finditer(s))
'''
    with pytest.raises(WorkBudgetExceeded, match="linear budget"):
        with k02_work_budget(raw):
            with monkeypatch.context() as scoped:
                scoped.setattr(fp, "_digits_covered", _mutant(source, "_digits_covered"))
                fp.parse_value(raw, "count", fp.default_domain("count"))


def test_budget_detects_list_instead_of_set(k02_work_budget, monkeypatch):
    raw = " ".join(str(i) for i in range(4000))
    with k02_work_budget(raw) as healthy:
        got = fp._stray_digit_records(raw, [])
    assert got == [str(i) for i in range(4000)]
    assert healthy.work <= healthy.limit
    source = inspect.getsource(fp._stray_digit_records)
    assert source.count("seen: set[str] = set()") == 1
    assert source.count("seen.add(rec)") == 1
    source = source.replace("seen: set[str] = set()", "seen = []")
    source = source.replace("seen.add(rec)", "seen.append(rec)")
    with pytest.raises(WorkBudgetExceeded, match="record_equal"):
        with k02_work_budget(raw):
            with monkeypatch.context() as scoped:
                scoped.setattr(fp, "_stray_digit_records", _mutant(source, "_stray_digit_records"))
                fp._stray_digit_records(raw, [])


def test_budget_keeps_repeated_records_and_restores_instrumentation(k02_work_budget):
    raw = "7 8 7 9 8"
    original = fp._RECORD_RE
    with k02_work_budget(raw) as work:
        assert fp._stray_digit_records(raw, []) == ["7", "8", "9"]
    assert work.counts["record_equal"] > 0
    assert fp._RECORD_RE is original


def test_budget_restores_instrumentation_on_failure(k02_work_budget):
    original = fp._RECORD_RE
    with pytest.raises(WorkBudgetExceeded):
        with k02_work_budget("7") as work:
            work.charge("deliberate_probe", work.limit + 1)
    assert fp._RECORD_RE is original


def test_budget_preserves_regex_metadata_and_record_values(k02_work_budget):
    original = fp._RECORD_RE
    raw = "7 8 7 9"
    with k02_work_budget(raw):
        assert fp._RECORD_RE.pattern == original.pattern
        assert fp._RECORD_RE.flags == original.flags
        got = [(mm.group(), mm.span()) for mm in fp._RECORD_RE.finditer(raw)]
    assert got == [(mm.group(), mm.span()) for mm in original.finditer(raw)]
