# -*- coding: utf-8 -*-
"""Акт приёмки смысла (П4, Д5): остов из модели, полнота, хэш."""
import json

from cdpm import acceptance_act as act_mod


def _model():
    return {"meta": {"customer": "т"}, "sources": [],
            "rules": [{"id": "VN-001", "kind": "HARD", "zone": "PAIRING", "statement": "a",
                       "sources": [{"sid": "S1", "anchor": "абз.7"}],
                       "template_fit": {"category": "A", "family": "rest_floors", "params": {}}},
                      {"id": "VN-002", "kind": "DEF", "zone": "BOTH", "statement": "b",
                       "sources": [{"sid": "S1", "anchor": "абз.5"}]}],
            "objectives": [{"id": "O-PAIR", "zone": "PAIRING", "direction": "minimize",
                            "statement": "мин", "sources": [{"sid": "S3", "anchor": "стр-1-абз-2"}],
                            "criterion": {"kind": "min_person_days", "rate_per_duty": 100}}],
            "questions": []}


def test_skeleton_validate_and_hash(tmp_path):
    model = _model()
    act = act_mod.build_skeleton(model, "run-0001")
    assert [r["id"] for r in act["rows"]] == ["VN-001", "VN-002", "O-PAIR"]
    assert act["rows"][2]["kind"] == "objective" and act["rows"][2]["family"] == "min_person_days"
    probs = act_mod.validate(act, model)
    assert len(probs) == 3 and all("вердикт не поставлен" in p for p in probs)
    h0 = act_mod.act_hash(act)
    for r in act["rows"]:
        r.update(verdict="принято", acceptor="владелец", date="2026-09-03")
    act["rows"][1].update(verdict="вопрос")
    probs = act_mod.validate(act, model)
    assert probs == ["VN-002: вердикт 'вопрос' без замечания"]
    act["rows"][1]["remark"] = "перечень баз: уточнить главную"
    assert act_mod.validate(act, model) == []
    assert act_mod.act_hash(act) != h0
    s = act_mod.summary(act)
    assert s["counts"] == {"принято": 2, "отклонено": 0, "вопрос": 1, "без вердикта": 0}
    # Акт другой модели — отпечаток не совпал; строка акта вне модели.
    model2 = json.loads(json.dumps(model))
    model2["rules"].pop()
    probs2 = act_mod.validate(act, model2)
    assert any("другой модели" in p for p in probs2) and any("VN-002: строки акта нет" in p for p in probs2)
    # Расхождение со справочником — только с исходом из перечня.
    act["reference_discrepancies"] = [{"row": "VN-003", "outcome": "как-нибудь"}]
    assert any("без исхода" in p for p in act_mod.validate(act, model))
    act["reference_discrepancies"] = [{"row": "VN-003", "outcome": "дефект конвейера", "note": "x"}]
    assert act_mod.validate(act, model) == []
    # CLI: остов и проверка.
    (tmp_path / "extract").mkdir()
    (tmp_path / "extract" / "model.json").write_text(json.dumps(model, ensure_ascii=False),
                                                     encoding="utf-8")
    assert act_mod.main([str(tmp_path), "--init"]) == 0
    assert act_mod.main([str(tmp_path), "--init"]) == 2          # не перезаписывает
    assert act_mod.main([str(tmp_path), "--check"]) == 1         # не полон
