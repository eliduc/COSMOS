"""CDPM-403: evidence readers reject duplicate keys without migrating facts."""
import json

import pytest

from cdpm import acceptance_act as act_mod, model as m
from test_cdpm_acceptance_act import _model


def _run(tmp_path):
    doc = _model()
    doc["rules"][0]["template_fit"]["params"] = {"min_rest": 3}
    path = tmp_path / "extract" / "model.json"
    path.parent.mkdir()
    raw = json.dumps(doc, ensure_ascii=False)
    path.write_text(raw, encoding="utf-8")
    act = act_mod.build_skeleton(doc, tmp_path.name)
    for row in act["rows"]:
        row.update(verdict="принято", acceptor="тестовый приёмщик", date="2026-09-14")
    return doc, path, raw, act


def _save_act(tmp_path, act):
    path = tmp_path / act_mod.ACT_FILE
    path.write_text(json.dumps(act, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("before,after,key,where", [
    ('"rules":', '"rules": [], "rules":', "rules", "корень документа"),
    ('"statement": "a"', '"statement": "подмена", "statement": "a"',
     "statement", "rules[0]"),
    ('"min_rest": 3', '"min_rest": 30, "min_rest": 3',
     "min_rest", "rules[0].template_fit.params"),
    ('"min_rest": 3', '"min_rest": 3, "min_rest": 3',
     "min_rest", "rules[0].template_fit.params"),
])
@pytest.mark.parametrize("mode", ["--init", "--check"])
def test_duplicate_model_is_refused_before_act_work(
        tmp_path, capsys, before, after, key, where, mode):
    doc, path, raw, act = _run(tmp_path)
    act_path = tmp_path / act_mod.ACT_FILE
    if mode == "--check":
        _save_act(tmp_path, act)
        assert act_mod.main([str(tmp_path), mode]) == 0
    original_act = act_path.read_bytes() if act_path.exists() else None
    bad = raw.replace(before, after, 1)
    assert bad != raw and json.loads(bad) == doc  # last-wins hides the defect
    with pytest.raises(m.DuplicateJsonKey):
        m.loads_strict(bad)
    path.write_text(bad, encoding="utf-8")
    original_model = path.read_bytes()
    capsys.readouterr()
    assert act_mod.main([str(tmp_path), mode]) == 2
    out = capsys.readouterr().out
    assert key in out and where in out and "АКТ ПОЛОН" not in out
    assert path.read_bytes() == original_model
    assert (act_path.read_bytes() if act_path.exists() else None) == original_act


@pytest.mark.parametrize("before,after,key,where", [
    ('"golden": false', '"golden": true, "golden": false', "golden", "корень документа"),
    ('"verdict": "принято"', '"verdict": "отклонено", "verdict": "принято"',
     "verdict", "rows[0]"),
])
def test_duplicate_act_is_refused_by_loader_and_cli(tmp_path, capsys, before, after, key, where):
    _, model_path, _, act = _run(tmp_path)
    path = _save_act(tmp_path, act)
    assert act_mod.main([str(tmp_path), "--check"]) == 0
    raw = path.read_text(encoding="utf-8")
    bad = raw.replace(before, after, 1)
    assert bad != raw and json.loads(bad) == act
    path.write_text(bad, encoding="utf-8")
    model_bytes, act_bytes = model_path.read_bytes(), path.read_bytes()
    with pytest.raises(m.DuplicateJsonKey) as exc:
        act_mod.load(tmp_path)
    assert key in str(exc.value) and where in str(exc.value)
    capsys.readouterr()
    assert act_mod.main([str(tmp_path), "--check"]) == 2
    out = capsys.readouterr().out
    assert key in out and where in out and "АКТ ПОЛОН" not in out
    assert (model_path.read_bytes(), path.read_bytes()) == (model_bytes, act_bytes)


def test_healthy_evidence_keeps_fingerprint_and_human_verdicts(tmp_path, monkeypatch):
    doc, path, _, act = _run(tmp_path)
    original = path.read_bytes()

    def forbidden_migration(*args, **kwargs):
        raise AssertionError("Evidence must not be silently migrated")

    monkeypatch.setattr(m, "loads_model", forbidden_migration)
    assert act_mod.load(tmp_path) is None
    assert act_mod.main([str(tmp_path), "--init"]) == 0
    skeleton = act_mod.load(tmp_path)
    assert skeleton["model_fingerprint"] == act_mod.model_fingerprint(doc)
    assert len(skeleton["rows"]) == 3
    assert all(r["verdict"] is None for r in skeleton["rows"])
    act_path = _save_act(tmp_path, act)
    evidence = act_path.read_bytes()
    assert act_mod.main([str(tmp_path), "--check"]) == 0
    assert act_mod.main([str(tmp_path), "--init"]) == 2
    assert act_mod.load(tmp_path) == act
    assert (path.read_bytes(), act_path.read_bytes()) == (original, evidence)


@pytest.mark.parametrize("broken", ["model", "act"])
def test_invalid_json_is_reported_without_overwrite(tmp_path, capsys, broken):
    _, model_path, _, act = _run(tmp_path)
    act_path = _save_act(tmp_path, act)
    path = model_path if broken == "model" else act_path
    path.write_text("{", encoding="utf-8")
    original = (model_path.read_bytes(), act_path.read_bytes())
    assert act_mod.main([str(tmp_path), "--check"]) == 2
    assert "не удалось прочитать" in capsys.readouterr().out
    assert (model_path.read_bytes(), act_path.read_bytes()) == original
