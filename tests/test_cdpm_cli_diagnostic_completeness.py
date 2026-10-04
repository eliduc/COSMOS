"""CLI failures expose every formed reason, including reasons after eight."""
import json

import pytest

from cdpm import data_report as dr, data_review, data_to_anketa, mode2, model as m
from cdpm.sample import sample_model
from test_cdpm_data_to_anketa import _report


@pytest.mark.parametrize("command", ["review", "anketa", "mode2"])
@pytest.mark.parametrize("count", [0, 1, 8, 9, 12])
def test_cli_prints_all_validation_reasons_and_preserves_refusal(
        command, count, tmp_path, monkeypatch, capsys):
    model_path, report_path = tmp_path / "model.json", tmp_path / "report.json"
    model_path.write_text(json.dumps(sample_model(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    reasons = [f"Причина-{i:02d}: " + "длинное пояснение " * 30 + f"КОНЕЦ-{i:02d}"
               for i in range(count)]
    calls = []
    if command == "review":
        dr.dump_report(dr.sample_data_report(), report_path)
        monkeypatch.setattr(data_review.llm_mod, "cosmos_llm",
                            lambda **kw: lambda prompt: '{"reviews": []}')
        original = dr.validate_report
        def validate(report, svod):
            actual = original(report, svod)
            assert actual == [], actual
            calls.append(1)
            return reasons
        monkeypatch.setattr(dr, "validate_report", validate)
        rc = data_review.main([str(model_path), str(report_path), "--out-dir", str(out)])
    else:
        original = m.validate_model
        def validate(doc):
            actual = original(doc)
            assert actual == [], actual
            calls.append(1)
            return reasons
        monkeypatch.setattr(m, "validate_model", validate)
        if command == "anketa":
            report_path.write_text(json.dumps(_report(), ensure_ascii=False), encoding="utf-8")
            rc = data_to_anketa.main([str(model_path), str(report_path),
                "--checks", "C-A-0", "--title", "Как понимать правило?",
                "--text", "Поясните чтение.", "--options", "а=чтение 1;б=чтение 2",
                "--out-dir", str(out)])
        else:
            third = tmp_path / "third.md"
            third.write_text("1.1. Отдых на базе не менее 14 часов.", encoding="utf-8")
            monkeypatch.setattr(mode2.llm_mod, "cosmos_llm",
                                lambda **kw: lambda prompt: '{"checks": []}')
            rc = mode2.main([str(model_path), str(third), "--out-dir", str(out)])
    text = capsys.readouterr().out
    assert calls, "the actual CLI validation boundary must be reached"
    if count:
        assert rc == 1 and not out.exists()
        assert text == "".join(f"НАРУШЕНИЕ: {reason}\n" for reason in reasons)
    else:
        assert rc == 0 and out.is_dir()
        assert "НАРУШЕНИЕ:" not in text
