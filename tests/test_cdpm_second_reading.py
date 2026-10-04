# -*- coding: utf-8 -*-
"""Слой 2 «перекрёстное чтение» (план «прямой ход» 2.7.2) на подставных
моделях: совпавшие сборки вопросов не рождают; расхождение канонического
слоя по носителю — вопрос части 2 с двумя чтениями и блокировкой строки;
раскладка без независимого второго читателя — слой пропущен с причиной;
проверка независимости семейств кодом."""
import json
import re

from cdpm import extract, model as m, providers as prov
from tests.test_cdpm_extract import _CANNED, fake_llm, kit  # noqa: F401 — fixture


def _second_reader(differs_at: str | None):
    def second(prompt: str) -> str:
        if "Строки свода" in prompt:
            return json.dumps({"contradictions": []})
        anchors = re.findall(r"^\[([^\]]+)\]", prompt, flags=re.M)
        cands = []
        for a in anchors:
            if a in _CANNED:
                c = dict(_CANNED[a])
                if a == differs_at:
                    c["kind"] = "SOFT" if c["kind"] == "HARD" else "HARD"
                cands.append(c)
        return json.dumps({"candidates": cands}, ensure_ascii=False)
    return second


def test_second_reading_agreement_adds_no_questions(kit, tmp_path):
    base = extract.run_extract(kit, tmp_path / "base", fake_llm, "Полярис (тест)")
    s = extract.run_extract(kit, tmp_path / "out", fake_llm, "Полярис (тест)",
                            second_llm=_second_reader(None))
    assert s["second_reading"]["disagree"] == 0
    assert s["second_reading"]["agree"] == s["second_reading"]["anchors"] >= 6
    assert s["second_reading"]["questions"] == 0
    assert s["questions"] == base["questions"]
    assert (tmp_path / "out" / "second-reading.json").is_file()


def test_second_reading_disagreement_becomes_part2_question(kit, tmp_path):
    s = extract.run_extract(kit, tmp_path / "out", fake_llm, "Полярис (тест)",
                            second_llm=_second_reader("п.1.9"))
    assert s["second_reading"]["disagree"] == 1
    assert s["second_reading"]["questions"] == 1
    doc = m.load_model(tmp_path / "out" / "model.json")
    qs = [q for q in doc["questions"] if "second_reading" in q["id"]]
    assert len(qs) == 1
    q = qs[0]
    assert q["part"] == "2" and "п.1.9" in q["text"]
    assert "чтение извлечения: HARD" in q["text"] and "второе чтение: SOFT" in q["text"]
    assert [o["key"] for o in q["answer_format"]["options"]] == ["а", "б", "в"]
    rule = next(r for r in doc["rules"] if r["id"] in q["rule_refs"])
    assert q["id"] in rule["blockers"]
    rep = json.loads((tmp_path / "out" / "second-reading.json").read_text(encoding="utf-8"))
    assert rep["disagreements"][0]["anchor"] == ["S1", "п.1.9"]


def test_weaker_second_reading_and_objective_per_are_not_disputes():
    from cdpm import second_reading as sr
    rule_a = {"id": "R-1", "kind": "SOFT", "zone": "PAIRING", "weight": 10,
              "sources": [{"sid": "S1", "anchor": "абз.14"}],
              "template_fit": {"category": "A", "family": "layover_penalty",
                               "quote": "штраф 10 условных единиц за каждую ночёвку"}}
    rule_c = dict(rule_a, template_fit={"category": "C", "gap": "нет цитаты"})
    obj1 = {"id": "O-1", "zone": "ROSTERING", "direction": "minimize",
            "metric": "credit_hours_spread", "sources": [{"sid": "S3", "anchor": "а4"}],
            "rate": {"value": 1, "per": "час разброса кредитных часов", "as_written": "x"}}
    obj2 = dict(obj1, rate={"value": 1, "per": "за каждый час разброса кредитных часов",
                            "as_written": "y"})
    rep = sr.compare({"rules": [rule_a], "objectives": [obj1]},
                     {"rules": [rule_c], "objectives": [obj2]})
    assert rep["disagree"] == 0 and rep["weaker"] == 1 and rep["agree"] == 1
    # Симметрично: чтение 1 без посадки, чтение 2 с посадкой — тоже «слабее» (проход №10).
    rep_sym = sr.compare({"rules": [rule_c]}, {"rules": [rule_a]})
    assert rep_sym["disagree"] == 0 and rep_sym["weaker"] == 1
    # Лишняя неформализованная строка (C без семьи) у одного из чтений при
    # совпавших остальных — не спор о задаче решателя (проход №11: второй
    # читатель добавил HARD C «согласие экипажа»).
    extra_c = {"id": "R-9", "kind": "HARD", "zone": "PAIRING",
               "sources": [{"sid": "S1", "anchor": "абз.14"}],
               "template_fit": {"category": "C", "gap": "не поддерживается"}}
    rep_extra = sr.compare({"rules": [rule_a]}, {"rules": [rule_a, extra_c]})
    assert rep_extra["disagree"] == 0 and rep_extra["weaker"] == 1
    # Слабее — не при ИНОМ ядре: A против C с ИНЫМ весом — спор.
    rule_c2 = dict(rule_c, weight=25)
    rep2 = sr.compare({"rules": [rule_a]}, {"rules": [rule_c2]})
    assert rep2["disagree"] == 1 and rep2["weaker"] == 0
    # Два разных A — спор о чтении.
    rule_b = dict(rule_a, template_fit={"category": "A", "family": "calendar_windows",
                                        "quote": "пятница"})
    assert sr.compare({"rules": [rule_a]}, {"rules": [rule_b]})["disagree"] == 1
    # Расхождение только у целевой — вопрос не ставится (нет строки свода), но учтено.
    obj3 = dict(obj1, rate={"value": 2, "per": "час", "as_written": "z"})
    rep3 = sr.compare({"rules": [], "objectives": [obj1]}, {"rules": [], "objectives": [obj3]})
    assert rep3["disagree"] == 1

    class _Asm:
        def __init__(self):
            self.doc = {"rules": [], "objectives": [obj1], "questions": []}

        def add_question(self, *a, **k):        # pragma: no cover — не должно звать
            raise AssertionError("вопрос без строки свода")
    asm = _Asm()
    assert sr.add_questions(asm, rep3, {}) == 0
    assert rep3["unasked"] == [{"anchor": ["S3", "а4"], "rows": ["O-1"]}]


def test_canon_ignores_trailing_punctuation_of_quote_and_zone_of_definitions():
    """Проход №13 H0: спор из-за точки в конце цитаты-носителя и из-за
    зоны определения (BOTH против ROSTERING) — ни то, ни другое не
    меняет задачу решателя."""
    from cdpm import second_reading as sr
    q = "штраф 3 условные единицы за каждый кредитный час разброса"
    r1 = {"id": "R-1", "kind": "SOFT", "zone": "ROSTERING", "weight": 3,
          "sources": [{"sid": "S2", "anchor": "строка 6"}],
          "template_fit": {"category": "A", "family": "balanced_workload", "quote": q + "."}}
    r2 = dict(r1, template_fit={"category": "A", "family": "balanced_workload", "quote": q})
    assert sr.compare({"rules": [r1]}, {"rules": [r2]})["agree"] == 1
    assert m.fit_signature(r1) == m.fit_signature(r2)
    d1 = {"id": "R-2", "kind": "DEF", "zone": "BOTH", "statement": "базы",
          "sources": [{"sid": "S1", "anchor": "абз.5"}]}
    d2 = dict(d1, zone="ROSTERING")
    assert sr.compare({"rules": [d1]}, {"rules": [d2]})["agree"] == 1
    # У правил с исполнением зона — часть задачи: расхождение остаётся спором.
    h1 = {"id": "R-3", "kind": "HARD", "zone": "PAIRING", "statement": "x",
          "sources": [{"sid": "S1", "anchor": "абз.9"}],
          "template_fit": {"category": "A", "family": "rest_floors",
                           "params": {"min_rest_home": {"value": 720, "quote": "12:00"}}}}
    assert sr.compare({"rules": [h1]}, {"rules": [dict(h1, zone="ROSTERING")]})["disagree"] == 1


def test_nested_quote_spans_of_parameterless_family_agree():
    """Проход №16 H0: у беспараметрной семьи два чтения взяли разные
    отрезки одной клаузы (один вложен в другой) — норма та же."""
    from cdpm import second_reading as sr
    short = "ночёвку вне базы, календарный день начала которой — пятница или суббота"
    long_ = "Надбавка 25 условных единиц к штрафу за " + short + "."
    r1 = {"id": "R-1", "kind": "SOFT", "zone": "PAIRING", "weight": 25,
          "sources": [{"sid": "S1", "anchor": "абз.15"}],
          "template_fit": {"category": "A", "family": "calendar_windows", "quote": short}}
    r2 = dict(r1, template_fit={"category": "A", "family": "calendar_windows", "quote": long_})
    rep = sr.compare({"rules": [r1]}, {"rules": [r2]})
    assert rep["agree"] == 1 and rep["disagree"] == 0
    # Разные отрезки без вложения — спор.
    r3 = dict(r1, template_fit={"category": "A", "family": "calendar_windows",
                                "quote": "ночёвку в праздничный день"})
    assert sr.compare({"rules": [r1]}, {"rules": [r3]})["disagree"] == 1


def test_omission_of_second_reader_is_incomplete_not_dispute():
    """Проход №21 H0: второй читатель не выдал по клаузе ни строки, ни
    вердикта — это неполнота второго чтения (считается и печатается),
    а не спор о существовании нормы; спор — когда второй читатель ЯВНО
    сказал «не правило» против строки первого."""
    from cdpm import second_reading as sr
    r1 = {"id": "R-1", "kind": "HARD", "zone": "BOTH", "statement": "x",
          "sources": [{"sid": "S1", "anchor": "абз.7"}],
          "template_fit": {"category": "A", "family": "time_scale",
                           "params": {"base_tz_offset_min": {"value": 0, "quote": "0 минут"}}}}
    rep = sr.compare({"rules": [r1]}, {"rules": []}, {}, {})
    assert rep["disagree"] == 0 and rep["incomplete_2"] == 1
    rep2 = sr.compare({"rules": [r1]}, {"rules": []}, {},
                      {("S1", "абз.7"): {"verdict": "не правило", "reason": "справка", "by": "model"}})
    assert rep2["disagree"] == 1 and rep2["incomplete_2"] == 0


def test_second_reading_skipped_with_reason_when_not_independent(kit, tmp_path):
    s = extract.run_extract(kit, tmp_path / "out", fake_llm, "Полярис (тест)",
                            second_note="второй читатель и извлечение — одно семейство")
    assert s["second_reading"] == {"skipped": "второй читатель и извлечение — одно семейство"}


def test_independence_problems_on_layouts():
    same = {"extractor": {"channels": {"cloud": {"provider": "cloud", "model": "claude-sonnet-5"}}},
            "auditor": {"channels": {"cloud": {"provider": "cloud", "model": "claude-opus-5"}}}}
    assert prov.independence_problems(same) == [
        "второй читатель и извлечение — одно семейство моделей 'claude': слой 2 не независим"]
    other = {"extractor": {"channels": {"cloud": {"provider": "cloud", "model": "claude-sonnet-5"}}},
             "auditor": {"channels": {"cloud": {"provider": "openai", "model": "gpt-5.6-terra"}}}}
    assert prov.independence_problems(other) == []
    assert prov.role_family(other, "second_reader") == ("openai", "gpt")
    assert prov.independence_problems({}) == [
        "второй читатель не настроен (ни своей роли, ни аудитора-родителя)"]
