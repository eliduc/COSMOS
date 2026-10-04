# -*- coding: utf-8 -*-
"""Слот цитаты веса (weight_quote): цитатный слой на все машинные поля
(RAZDELENIE-CDPM-COSMOS раздел 3 п.2; проверка плана №11/№16). Вес едет с
цитатой из клаузы на извлечении, переживает форму без потерь, судится
цитатным слоем и виден измерителю как поле с носителем."""
import json

from cdpm import extract as ex
from cdpm import measure as ms
from cdpm import model as m
from cdpm import vmp_form as vf

CLAUSE = ("VN-030. Ночёвки вне базы. Штраф 10 условных единиц за каждую "
          "ночёвку вне базы приписки.")


def _asm_with_soft():
    asm = ex._Assembler("т", [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64}])
    asm.add_candidate("S1", {"anchor": "абз.14", "title": "Ночёвки вне базы",
                             "statement": "penalty 10 за ночёвку вне базы",
                             "zone": "PAIRING", "kind": "SOFT",
                             "weight": {"value": 10,
                                        "as_written": "Штраф 10 условных единиц за каждую ночёвку"},
                             "template_fit": {"category": "A", "family": "layover_penalty",
                                              "quote": "Штраф 10 условных единиц за каждую "
                                                       "ночёвку вне базы приписки"}},
                      clause_text=CLAUSE)
    return asm


def test_extraction_stores_weight_with_quote_and_form_keeps_it():
    asm = _asm_with_soft()
    rule = asm.doc["rules"][0]
    assert rule["weight"] == 10
    assert rule["weight_quote"] == "Штраф 10 условных единиц за каждую ночёвку"
    assert m.validate_schema(asm.doc) == []
    form = vf.to_form(asm.doc)
    row = form["pairing"]["soft"][0]
    assert row["penalty"]["as_written"] == rule["weight_quote"]
    back = vf.from_form(form)
    assert back["rules"][0]["weight_quote"] == rule["weight_quote"]
    assert vf.canonical_diff(asm.doc, back) == []


def test_fit_quotes_judge_weight_quote():
    asm = _asm_with_soft()
    texts = {("S1", "абз.14"): CLAUSE}
    assert m.validate_fit_quotes(asm.doc, texts) == []
    asm.doc["rules"][0]["weight_quote"] = "штраф 10 монет за ночёвку"
    probs = m.validate_fit_quotes(asm.doc, texts)
    assert probs and "цитата веса" in probs[0]
    # Падеж вне числа не роняет (дословность в окрестности числа).
    asm.doc["rules"][0]["weight_quote"] = "Штрафом 10 условных единиц"
    assert m.validate_fit_quotes(asm.doc, texts) == []


def test_measurer_sees_weight_carrier():
    asm = _asm_with_soft()
    rule = asm.doc["rules"][0]
    fields = {f["field"]: f for f in ms.field_values(rule)}
    assert fields["weight"]["quote"] == rule["weight_quote"]
    assert [c for c in ms.carriers(CLAUSE, rule) if c["field"] == "weight"] == []
    rule.pop("weight_quote")
    assert any(c["field"] == "weight" and c["kind"] == "нет слота носителя в схеме"
               for c in ms.carriers(CLAUSE, rule))


def test_weight_from_answer_requires_quote_from_raw_answer():
    from cdpm import answers as ans_mod
    # Модельная сборка: вопрос весов с ответом «штраф 20 за ночёвку».
    doc = json.loads(json.dumps(_asm_with_soft().doc))
    r = doc["rules"][0]
    r.pop("weight"); r.pop("weight_quote")
    q = {"id": "Q-3a-GOAL", "part": "3a", "title": "Веса", "text": "Веса мягких строк?",
         "answer_format": {"type": "text"}, "rule_refs": [r["id"]],
         "source_refs": [{"sid": "S1", "anchor": "абз.14"}],
         "answer": {"raw": "штраф 20 условных единиц за ночёвку", "status": "получен",
                    "revision": 1}}
    r["blockers"] = [q["id"]]
    doc["questions"] = [q]
    calls = []

    def llm(prompt):
        calls.append(prompt)
        aw = "штраф 20 условных единиц" if len(calls) == 1 else "штраф 20 монет"
        return json.dumps({"decline": False, "objectives": [],
                           "weights": [{"rule": r["id"], "weight": 20,
                                        "statement": "penalty 20", "as_written": aw}]},
                          ensure_ascii=False)
    fn = getattr(ans_mod, "_apply_goal_answer", None)
    if fn is None:
        return                       # имя внутреннего применителя иное — путь покрыт сквозными тестами ответов
    problems = fn(doc, q, llm)
    assert not problems, problems
    assert doc["rules"][0]["weight"] == 20
    assert doc["rules"][0]["weight_quote"] == "штраф 20 условных единиц"
    doc2 = json.loads(json.dumps(_asm_with_soft().doc))
    doc2["rules"][0].pop("weight"); doc2["rules"][0].pop("weight_quote")
    doc2["rules"][0]["blockers"] = [q["id"]]
    doc2["questions"] = [json.loads(json.dumps(q))]
    problems2 = fn(doc2, doc2["questions"][0], llm)
    assert problems2 and "цитата" in problems2[0]
