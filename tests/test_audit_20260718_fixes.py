"""Regression tests for the 2026-07-18 audit-fix batch (the well-scoped
fixes shipped in response to AUDIT-legion-Test-Stage-Prod-20260718-RU.md)."""
import json

import pytest


# ------------------------------------------------- M01: Ярус-1 только целые
def test_rule_pack_rejects_fractional_tier1(monkeypatch):
    from webapp import rule_packs
    param = next(iter(rule_packs.PARAMETERS))
    # a fractional tier-1 value used to pass approval but 422 at apply-config
    with pytest.raises(rule_packs.RulePackError):
        rule_packs._validate_constraint(
            {"kind": "parameter", "parameter": param, "value": 30.5})
    ok = rule_packs._validate_constraint(
        {"kind": "parameter", "parameter": param, "value": 30})
    assert ok["value"] == 30


# ------------------------------------------- M02: env-floor маскирования
def test_mask_env_floor_cannot_be_weakened(monkeypatch):
    from webapp import data_import, settings
    monkeypatch.setenv("CREW_IMPORT_MASK_SAMPLES", "1")   # deployment forced it
    monkeypatch.setattr(settings, "get_mask_samples", lambda: "cloud")
    # "cloud" policy must NOT turn off a forced local-masking floor
    assert data_import._mask_samples_for("local") is True
    assert data_import._mask_samples_for("cloud") is True
    # without the env floor, "cloud" leaves local raw (as designed)
    monkeypatch.delenv("CREW_IMPORT_MASK_SAMPLES", raising=False)
    assert data_import._mask_samples_for("local") is False


# ---------------------------------------------- M06: строгий bool в фактах
def test_ops_strict_bool():
    from webapp import ops_log
    assert ops_log._strict_bool(True, "x") is True
    assert ops_log._strict_bool("false", "x") is False    # bool("false")==True
    assert ops_log._strict_bool("true", "x") is True
    assert ops_log._strict_bool(None, "x") is False
    with pytest.raises(ops_log.OpsLogError):
        ops_log._strict_bool("maybe", "x")
    # a JSON string "false" must NOT cancel a flight
    fact = ops_log._clean_fact({"cancelled": "false"})
    assert fact["cancelled"] is False


# ----------------------------------------- M14: CSV formula-injection guard
def test_csv_formula_injection_neutralised():
    from webapp import results
    assert results._csv_safe("=cmd|'/c calc'!A1") == "'=cmd|'/c calc'!A1"
    assert results._csv_safe("+1") == "'+1"
    assert results._csv_safe("-2") == "'-2"
    assert results._csv_safe("@x") == "'@x"
    assert results._csv_safe("SVO") == "SVO"              # normal cell untouched
    assert results._csv_safe(None) == ""


# ----------------------------------- M16: точный тег прогретой модели
def test_warm_check_requires_exact_tag(monkeypatch):
    from webapp import llm_backend
    monkeypatch.setenv("CREW_OLLAMA_MODEL", "qwen3.6:35b-a3b")

    class _Resp:
        status = 200

        def __init__(self, models):
            self._m = models

        def read(self):
            return json.dumps({"models": self._m}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # a DIFFERENT-size sibling must NOT count as warm
    monkeypatch.setattr(llm_backend.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp([{"name": "qwen3.6:8b"}]))
    assert llm_backend._local_model_loaded() is False
    # the exact configured tag does
    monkeypatch.setattr(
        llm_backend.urllib.request, "urlopen",
        lambda req, timeout=None: _Resp([{"name": "qwen3.6:35b-a3b"}]))
    assert llm_backend._local_model_loaded() is True

    # an UNTAGGED config matches Ollama's :latest resolution (what generation
    # actually loads) but still NOT a different explicit tag (review fix)
    monkeypatch.setenv("CREW_OLLAMA_MODEL", "qwen3.6")
    monkeypatch.setattr(
        llm_backend.urllib.request, "urlopen",
        lambda req, timeout=None: _Resp([{"name": "qwen3.6:latest"}]))
    assert llm_backend._local_model_loaded() is True
    monkeypatch.setattr(llm_backend.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp([{"name": "qwen3.6:8b"}]))
    assert llm_backend._local_model_loaded() is False


# ------ H14 (review fix): transient OSError on the read path is retried, not 500
def test_ops_load_retries_transient_oserror(monkeypatch, tmp_path):
    from webapp import ops_log
    real = tmp_path / "ops_log.json"
    real.write_text('{"i1": {}}', encoding="utf-8")

    class _FlakyPath:
        def __init__(self, p, fails):
            self._p, self._fails = p, fails

        def exists(self):
            return True

        def read_text(self, encoding="utf-8"):
            if self._fails[0] > 0:
                self._fails[0] -= 1
                raise OSError("sharing violation")
            return self._p.read_text(encoding=encoding)

    # two transient sharing-violations then success -> retried, no exception,
    # a perfectly valid store still reads (before the fix this 500'd)
    monkeypatch.setattr(ops_log, "OPS_LOG_PATH", _FlakyPath(real, [2]))
    assert ops_log._load() == {"i1": {}}
