"""Deterministic work guard for the existing large-answer K02 regressions.

This is an algorithmic regression bound, NOT a latency SLA. It charges Python
line execution in family_params (including comprehensions), complete regex
scan input and record equality comparisons. The latter exposes C-level list
membership, which line tracing alone cannot see. Anchored regex methods are
charged per call, not as a scan of the whole suffix. Backtracking inside a
single regex call is outside this guard; the test process hang guard remains.
"""
from contextlib import contextmanager
import json
import re
import sys

import pytest

from cdpm import family_params as fp


class WorkBudgetExceeded(AssertionError):
    pass


class WorkBudget:
    # A fixed linear allowance for all existing stage passes plus setup.
    # These constants do not depend on the running machine or elapsed time.
    def __init__(self, size):
        self.limit = 128 * size + 10000
        self.work = 0
        self.counts = {}
        self.active = True

    def charge(self, kind, amount=1):
        if not self.active:
            return
        self.work += amount
        self.counts[kind] = self.counts.get(kind, 0) + amount
        if self.work > self.limit:
            raise WorkBudgetExceeded(
                f"K02 work {self.work} exceeds linear budget {self.limit}: "
                f"{self.counts}")


def _record(value, budget):
    class CountedRecord(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            budget.charge("record_equal")
            return str.__eq__(self, other)

    return CountedRecord(value)


class _Match:
    def __init__(self, match, budget):
        self.match, self.budget = match, budget

    def group(self, *args):
        value = self.match.group(*args)
        return _record(value, self.budget) if isinstance(value, str) else value

    def __getattr__(self, name):
        return getattr(self.match, name)


class _Pattern:
    def __init__(self, pattern, budget, records=False):
        self._pattern, self.budget, self.records = pattern, budget, records

    def __getattr__(self, name):
        attr = getattr(self._pattern, name)
        if not callable(attr):
            return attr

        def call(text, *args, **kwargs):
            self.budget.charge("regex_calls")
            if name in ("finditer", "findall", "search", "split", "sub", "subn"):
                # sub/subn receive the replacement before the searched text.
                scanned = args[0] if name in ("sub", "subn") else text
                self.budget.charge("regex_scan", len(scanned))
            got = attr(text, *args, **kwargs)
            if self.records and name == "finditer":
                return (_Match(mm, self.budget) for mm in got)
            return got

        return call


@pytest.fixture
def k02_work_budget(monkeypatch, request):
    @contextmanager
    def measure(raw):
        budget = WorkBudget(len(raw))
        previous_trace = sys.gettrace()
        filename = fp._digits_covered.__code__.co_filename

        def trace(frame, event, arg):
            if frame.f_code.co_filename != filename:
                return None
            if event == "line":
                budget.charge("python_lines")
            return trace

        with monkeypatch.context() as scoped:
            # Snapshot the names before wrapping. Only this product module is
            # instrumented; no clocks, global re functions or services change.
            for name, value in list(vars(fp).items()):
                if isinstance(value, re.Pattern):
                    scoped.setattr(fp, name, _Pattern(
                        value, budget, records=name == "_RECORD_RE"))
            try:
                sys.settrace(trace)
                yield budget
            finally:
                sys.settrace(previous_trace)
                budget.active = False
                request.node.user_properties.append(("k02_work", json.dumps({
                    "input_chars": len(raw), "work": budget.work,
                    "limit": budget.limit, "counts": budget.counts,
                }, sort_keys=True)))

    return measure
