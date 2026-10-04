# -*- coding: utf-8 -*-
"""Контракт машинной модели CDPM (пакет работ П-1).

Проверяется по классам нарушений: каждый инвариант, оплаченный уроком
живых линий (трассировка у каждого утверждения, развилка вместо
выбора, ветвь на каждый ответ, дубли вопросов), обязан ловиться
валидатором на испорченной копии образца. Валидатор, не находящий
ошибок, — не проверка, поэтому негативных случаев больше, чем
позитивных."""
import copy

import jsonschema
import pytest

from cdpm import model as m
from cdpm.sample import sample_model


def test_sample_is_clean():
    """Образец вымышленной авиакомпании чист по обоим слоям — это же
    гарантирует самотест гейта."""
    doc = sample_model()
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []


def test_schema_file_is_a_valid_jsonschema():
    import json
    schema = json.loads(m.SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


def test_schema_rejects_unknown_fields_and_bad_sha():
    """Схема закрыта (additionalProperties: false) — чужие поля и битый
    SHA-256 не проползают в контракт молча."""
    doc = sample_model()
    # «weight» стал законным полем (машинный вес мягких, 2026-08-22)
    # — пример чужого поля заменён.
    doc["rules"][0]["vydumka"] = 5
    assert any("vydumka" in p for p in m.validate_schema(doc))
    doc = sample_model()
    doc["sources"][0]["sha256"] = "не-хеш"
    assert any("sha256" in p or "не-хеш" in p
               for p in m.validate_schema(doc))


def _broken(mutate):
    doc = sample_model()
    mutate(doc)
    return m.validate_model(doc)


# Каждый случай: (имя класса нарушения, мутация образца, подстрока,
# которую валидатор обязан назвать).
_VIOLATIONS = [
    ("правило без трассировки",
     lambda d: d["rules"][0].update(sources=[]),
     "без трассировки к источнику"),
    ("трассировка на неизвестный источник",
     lambda d: d["rules"][0].update(sources=[{"sid": "S9", "anchor": "П.1"}]),
     "неизвестный источник"),
    ("дубликат идентификатора правила",
     lambda d: d["rules"][1].update(id=d["rules"][0]["id"]),
     "дубликат идентификатора правила"),
    ("блокировка на несуществующий вопрос",
     lambda d: d["rules"][0].update(blockers=["Q-НЕТ"]),
     "несуществующий вопрос"),
    ("ветви условного типа не покрывают ответы",
     lambda d: d["rules"][3]["conditional"].update(branches={"а": "SOFT"}),
     "ветвь нужна каждому ответу"),
    ("условный тип не заблокирован своим вопросом",
     lambda d: d["rules"][3].update(blockers=[]),
     "не заблокирован своим вопросом"),
    ("условный тип на вопрос без развилки",
     lambda d: d["questions"][1]["answer_format"].update(type="text",
                                                        options=[]),
     "требует вопроса-развилки"),
    ("SOFT без вопроса о весе",
     lambda d: d["rules"][4].update(blockers=[]),
     "SOFT-правило без блокировки"),
    ("противоречие с одной ссылкой на источники",
     lambda d: d["questions"][0].update(
         source_refs=[{"sid": "S1", "anchor": "П.1.4"}]),
     "не менее двух ссылок"),
    ("неоднозначность без развилки",
     lambda d: d["questions"][1]["answer_format"].update(type="text",
                                                        options=[]),
     "требует развилки"),
    ("дубль вопроса",
     lambda d: d["questions"][2].update(text=d["questions"][1]["text"]),
     "дубль вопроса"),
    ("часть 4 в режиме 1",
     lambda d: d["questions"].append({
         "id": "Q-4-X", "part": "4", "title": "х", "text": "х?",
         "answer_format": {"type": "text"},
         "rule_refs": [d["rules"][0]["id"]],
         "source_refs": [{"sid": "S1", "anchor": "П.1"}]}),
     "часть 4 существует только в режиме 2"),
    ("часть 4 без ссылки на файл третьей стороны",
     lambda d: (d["meta"].update(mode=2), d["questions"].append({
         "id": "Q-4-X", "part": "4", "title": "х", "text": "х?",
         "answer_format": {"type": "text"},
         "rule_refs": [d["rules"][0]["id"]],
         "source_refs": [{"sid": "S1", "anchor": "П.1"}]})),
     "без ссылки на файл третьей стороны"),
    ("развилка из одного варианта",
     lambda d: d["questions"][0]["answer_format"].update(
         options=d["questions"][0]["answer_format"]["options"][:1]),
     "не менее двух"),
    ("value без единицы",
     lambda d: d["questions"][3].update(
         answer_format={"type": "value"}),
     "без указания единицы или формы"),
    ("вопрос без ссылки на свод",
     lambda d: d["questions"][0].update(rule_refs=[]),
     "без ссылки на строки свода"),
    ("вопрос без трассировки к источникам",
     lambda d: d["questions"][0].update(source_refs=[]),
     "без трассировки к источникам"),
    ("варианты у неразвилки",
     lambda d: d["questions"][3]["answer_format"].update(
         options=[{"key": "а", "text": "х"}]),
     "только у развилки"),
    ("ссылка на несуществующее правило",
     lambda d: d["questions"][0].update(rule_refs=["R-НЕТ"]),
     "несуществующее правило"),
    ("зона вне перечня",
     lambda d: d["rules"][0].update(zone="EVERYWHERE"),
     "вне перечня"),
]


@pytest.mark.parametrize("name,mutate,expected",
                         _VIOLATIONS, ids=[v[0] for v in _VIOLATIONS])
def test_validator_catches(name, mutate, expected):
    problems = _broken(mutate)
    assert any(expected in p for p in problems), \
        f"{name}: валидатор не назвал нарушение; получено {problems}"


def test_validator_reports_all_problems_not_first():
    """Находки предъявляются пакетом, а не по одной (порядок аудит-цикла
    пилотной линии: ревизия закрывает пакет замечаний)."""
    doc = sample_model()
    doc["rules"][0]["sources"] = []
    doc["questions"][0]["rule_refs"] = []
    problems = m.validate_model(doc)
    assert len(problems) >= 2


def test_stable_ids_are_stable_and_disambiguated():
    assert m.stable_rule_id("S1", "П.1.9") == m.stable_rule_id("S1", "П.1.9")
    assert m.stable_rule_id("S1", "П.1.9") == "R-S1-П-1-9"
    assert m.stable_rule_id("S1", "П.1.9", 2) == "R-S1-П-1-9-2"
    assert m.stable_question_id("2", "MODAL") == "Q-2-MODAL"


def test_normalized_question_text_folds_case_space_and_punctuation():
    a = m.normalized_question_text("Какое требование ДЕЙСТВУЕТ?")
    b = m.normalized_question_text("какое   требование действует")
    assert a == b


def test_count_objects_matches_sample():
    c = m.count_objects(sample_model())
    assert c["rules"] == 10
    assert c["questions"] == 5
    assert c["questions_per_part"] == {"1": 1, "2": 1, "3a": 2, "3b": 1,
                                       "4": 0}
    assert c["blocked_rules"] == 6
    assert c["conditional_rules"] == 1
    assert c["links"] > 0


def test_mutations_do_not_leak_between_cases():
    """Мутации негативных случаев не портят общий образец: каждый случай
    строит свой документ."""
    before = sample_model()
    _broken(lambda d: d["rules"][0].update(sources=[]))
    assert sample_model() == before
    assert copy.deepcopy(before) == sample_model()


def test_validate_model_survives_junk_types():
    """Валидатор — линия обороны от предложений модели: любой JSON
    даёт нарушения, а не падение (живой цикл 2026-08-19: объект в
    blockers уронил проверку на нехешируемом ключе)."""
    from cdpm.sample import sample_model
    doc = sample_model()
    rule = doc["rules"][0]
    rule["blockers"] = [{"пришёл": "объект"}]
    rule["sources"].append({"sid": {"тоже": "объект"}, "anchor": "x"})
    doc["rules"].append({**doc["rules"][1], "id": {"нестроковый": 1}})
    doc["questions"][0]["rule_refs"] = [{"объект": True}]
    doc["questions"][1]["text"] = {"не": "строка"}
    doc["rules"][2]["conditional"] = "не объект"
    problems = m.validate_model(doc)          # не должно упасть
    assert any("несуществующий вопрос" in p for p in problems)
    assert any("идентификатор не строка" in p for p in problems)
    assert any("неизвестный источник" in p for p in problems)
    assert any("несуществующее правило" in p for p in problems)
    assert any("текст вопроса не строка" in p for p in problems)
    assert any("conditional не объект" in p for p in problems)
