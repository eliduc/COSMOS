#!/usr/bin/env bash
# CI gate: the FULL test battery (audit 2026-07-12, round 5 — an explicit
# file list silently skips new tests; conftest.py already excludes the
# legacy timefold-dependent suites).
cd "$(dirname "$0")"
.venv/Scripts/python.exe -m pytest tests -q
