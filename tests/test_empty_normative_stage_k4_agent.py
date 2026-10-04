"""K4: empty stage results need existing clause coverage, not an empty audit.

Synthetic frozen inputs and injected deterministic replies only. These tests
exercise the real assembler, coverage, measurement and exchange; no model,
HTTP service, solver or coverage/verdict replacement is involved.
"""
import hashlib
import json

import pytest

from cdpm import coverage, exchange, extract, model


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _kit(tmp_path, texts):
    """Exact synthetic frozen clauses; this is not a document-parser test."""
    kit = tmp_path / "freeze"
    entry = {
        "sid": "S1", "file": "synthetic-k4.md",
        "sha256": hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest(),
        "clauses": [{"anchor": f"п.{i}", "text": text}
                    for i, text in enumerate(texts, 1)],
    }
    _write_json(kit / "manifest.json", {"files": [{"sid": "S1"}]})
    _write_json(kit / "clauses" / "S1.json", entry)
    return kit, entry


def _reply(value):
    return lambda _prompt: json.dumps(value, ensure_ascii=False)


def _legacy_empty(extract_dir, entry):
    doc = {
        "meta": {"customer": "K4 synthetic", "mode": 1,
                 "cdpm_version": "0.1"},
        "sources": [{key: entry[key] for key in ("sid", "file", "sha256")}],
        "rules": [], "questions": [], "objectives": [],
    }
    assert model.validate_schema(doc) + model.validate_model(doc) == []
    extract_dir.mkdir(parents=True, exist_ok=True)
    model.dump_model(doc, extract_dir / "model.json")
    return doc


@pytest.mark.parametrize("reply", [
    {"candidates": []},
    {"paragraphs": [{"text": "Ответ иной формы без кандидатов"}]},
    {"candidates": [], "not_rule": [
        {"anchor": "п.2", "reason": "якобы справочная строка"}]},
], ids=["empty-candidates", "wrong-shape-json", "rejected-not-rule"])
def test_extract_refuses_empty_uncovered_result_after_saving_diagnostics(tmp_path, reply):
    kit, _ = _kit(tmp_path, ["Раздел 1. Введение",
                             "Отдых на базе не менее 10 часов."])
    out = tmp_path / "extract"
    with pytest.raises(extract.ExtractError, match="пустой результат") as error:
        extract.run_extract(kit, out, _reply(reply), "K4 synthetic")
    assert "S1/п.2" in str(error.value)
    assert "S1/п.1" not in str(error.value)
    report = json.loads((out / "coverage-report.json").read_text(encoding="utf-8"))
    assert report["not_rule"] == 1
    assert [(row["sid"], row["anchor"]) for row in report["missing"]] == [("S1", "п.2")]
    doc = model.load_model(out / "model.json")
    assert [doc[key] for key in ("rules", "objectives", "questions")] == [[], [], []]
    assert (out / "measure.json").is_file()
    assert (out / "rejections.json").is_file()


@pytest.mark.parametrize("texts", [[], ["Раздел 1. Введение"]], ids=["empty", "code-not-rule"])
def test_extract_and_exchange_allow_genuine_nonrule_only_inputs(tmp_path, texts):
    kit, _ = _kit(tmp_path, texts)
    out = tmp_path / "extract"
    result = extract.run_extract(kit, out, _reply({"candidates": []}), "K4 synthetic")
    assert (result["rules"], result["objectives"], result["questions"]) == (0, 0, 0)
    assert result["coverage"]["missing"] == 0
    assert result["coverage"]["not_rule"] == len(texts)
    summary = exchange.run_exchange(kit, out, tmp_path / "exchange",
                                    _reply({"remarks": []}), _reply({"answers": []}),
                                    max_rounds=2)
    assert summary["verdict"] == "успех"
    assert summary["coverage"]["missing"] == 0


def test_objective_only_result_is_not_an_empty_stage(tmp_path):
    text = "Минимизировать стоимость пейрингов."
    kit, _ = _kit(tmp_path, [text])
    out = tmp_path / "extract"
    reply = {"candidates": [], "objectives": [{
        "anchor": "п.1", "title": "Стоимость пейрингов", "statement": text,
        "zone": "PAIRING", "direction": "minimize",
    }]}
    result = extract.run_extract(kit, out, _reply(reply), "K4 synthetic")
    assert result["rules"] == 0 and result["objectives"] == 1
    assert result["coverage"]["missing"] == 0
    summary = exchange.run_exchange(kit, out, tmp_path / "exchange",
                                    _reply({"remarks": []}), _reply({"answers": []}),
                                    max_rounds=2)
    assert summary["verdict"] == "успех"
    assert summary["coverage"]["missing"] == 0


def test_second_reading_can_rescue_the_empty_first_result_before_guard(tmp_path):
    text = "Термин экипаж означает группу работников."
    kit, _ = _kit(tmp_path, [text])
    out = tmp_path / "extract"
    second = {"candidates": [{"anchor": "п.1", "title": "Термин экипаж",
                               "statement": text, "zone": "BOTH", "kind": "DEF"}]}
    result = extract.run_extract(kit, out, _reply({"candidates": []}), "K4 synthetic",
                                 second_llm=_reply(second))
    assert result["candidates"] == 0
    assert result["rules"] == 1
    assert result["coverage"]["missing"] == 0
    report = json.loads((out / "second-reading.json").read_text(encoding="utf-8"))
    assert report["adopted_missing"] == [{"anchor": ["S1", "п.1"], "row": "R-S1-п-1"}]


def test_exchange_rejects_legacy_empty_uncovered_before_any_auditor_call(tmp_path):
    kit, entry = _kit(tmp_path, ["Отдых на базе не менее 10 часов."])
    out = tmp_path / "extract"
    _legacy_empty(out, entry)
    before = (out / "model.json").read_bytes()
    calls = []

    def clean_but_irrelevant(prompt):
        calls.append(prompt)
        return '{"remarks": [], "answers": []}'

    exdir = tmp_path / "exchange"
    with pytest.raises(exchange.ExchangeError, match="пустой результат") as error:
        exchange.run_exchange(kit, out, exdir, clean_but_irrelevant, clean_but_irrelevant)
    assert "S1/п.1" in str(error.value)
    assert calls == []
    assert (out / "model.json").read_bytes() == before
    assert not (exdir / "verdict.json").exists()


def test_legacy_empty_with_saved_nonrule_verdict_remains_valid(tmp_path):
    kit, entry = _kit(tmp_path, ["Справочное пояснение к документу."])
    out = tmp_path / "extract"
    _legacy_empty(out, entry)
    coverage.save_verdicts(out, {("S1", "п.1"): {
        "verdict": "не правило", "reason": "Справочное пояснение, не требование",
        "by": "аудитор", "round": 1,
    }})
    before = (out / "coverage.json").read_bytes()
    summary = exchange.run_exchange(kit, out, tmp_path / "exchange",
                                    _reply({"remarks": []}), _reply({"answers": []}),
                                    max_rounds=2)
    assert summary["verdict"] == "успех"
    assert summary["coverage"]["missing"] == 0
    assert summary["coverage"]["not_rule"] == 1
    assert (out / "coverage.json").read_bytes() == before
