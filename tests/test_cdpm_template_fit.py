# -*- coding: utf-8 -*-
"""Пакет «Посадка правил на шаблоны»: контракт template_fit
(согласован CDPM+COSMOS 2026-09-01). Ядро: схема, словарная и
категорийная дисциплина, цитатный слой, отпечаток базы."""
from cdpm import model as m
from cdpm.sample import sample_model


REV = m.registry_fingerprint()


def _rule(doc):
    return next(r for r in doc["rules"] if r["kind"] == "HARD")


def _fit_a():
    return {"category": "A", "registry_rev": REV,
            "family": "duty_limits",
            "params": {"max_duty_duration": {
                "value": 720, "quote": "не более 12:00"}}}


def test_fit_a_valid_and_schema():
    doc = sample_model()
    _rule(doc)["template_fit"] = _fit_a()
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []


def test_fit_b_requires_assumption_and_allows_pending():
    doc = sample_model()
    r = _rule(doc)
    r["template_fit"] = dict(_fit_a(), category="B",
                             assumption="сутки по времени базы")
    assert m.validate_model(doc) == []
    r["template_fit"]["pending_question"] = "Q-нет-такого"
    assert any("pending_question" in p for p in m.validate_model(doc))
    r["template_fit"]["pending_question"] = \
        doc["questions"][0]["id"] if doc.get("questions") else None
    if r["template_fit"]["pending_question"] is None:
        r["template_fit"].pop("pending_question")
    assert m.validate_model(doc) == []


def test_fit_category_field_discipline():
    doc = sample_model()
    r = _rule(doc)
    # A с допущением — запрещено.
    r["template_fit"] = dict(_fit_a(), assumption="лишнее")
    assert any("запрещено при категории A" in p
               for p in m.validate_model(doc))
    # B без допущения — нарушение.
    r["template_fit"] = dict(_fit_a(), category="B")
    assert any("посадка B без поля 'assumption'" in p
               for p in m.validate_model(doc))
    # C: только gap; family запрещена.
    r["template_fit"] = {"category": "C", "registry_rev": REV,
                         "gap": "нет механики торгов",
                         "family": "duty_limits"}
    assert any("запрещено при категории C" in p
               for p in m.validate_model(doc))
    # D: candidates минимум две.
    r["template_fit"] = {"category": "D", "registry_rev": REV,
                         "candidates": ["duty_limits"]}
    assert any("минимум две" in p for p in m.validate_model(doc))


def test_fit_dictionary_discipline():
    doc = sample_model()
    r = _rule(doc)
    r["template_fit"] = dict(_fit_a(), family="нет-такой-семьи")
    assert any("вне базы шаблонов" in p for p in m.validate_model(doc))
    # Параметр чужой семьи: min_rest_home принадлежит rest_floors.
    r["template_fit"] = dict(
        _fit_a(), params={"min_rest_home": {
            "value": 640, "quote": "не менее 10 часов 40 минут"}})
    assert any("другой семье" in p for p in m.validate_model(doc))
    # Имя вне словаря PARAMS.
    r["template_fit"] = dict(
        _fit_a(), params={"vydumannyj": {"value": 1, "quote": "х"}})
    assert any("вне словаря PARAMS" in p
               for p in m.validate_model(doc))


def test_fit_only_for_machine_kinds():
    doc = sample_model()
    r = next(x for x in doc["rules"] if x["kind"] == "DEF")
    r["template_fit"] = _fit_a()
    assert any("только у машинных правил" in p
               for p in m.validate_model(doc))


def test_fit_contract_flag_demands_fit():
    doc = sample_model()
    doc["meta"]["fit_contract"] = True
    problems = m.validate_model(doc)
    assert any("без template_fit" in p for p in problems)
    for r in doc["rules"]:
        if r["kind"] in ("HARD", "SOFT"):
            r["template_fit"] = _fit_a() if r["kind"] == "HARD" else \
                dict(_fit_a(), category="B",
                     assumption="вес по ответу")
    # SOFT-посадка B без family-параметров недопустима? Нет: B несёт
    # family+params по контракту — фикстура выше валидна.
    assert not any("без template_fit" in p
                   for p in m.validate_model(doc))


def test_fit_quotes_against_clauses():
    doc = sample_model()
    r = _rule(doc)
    r["template_fit"] = _fit_a()
    key = (r["sources"][0]["sid"], r["sources"][0]["anchor"])
    ok = {key: "Продолжительность смены — не более 12:00 подряд."}
    assert m.validate_fit_quotes(doc, ok) == []
    bad = {key: "текст клаузы про другое"}
    assert any("дословной подстрокой" in p
               for p in m.validate_fit_quotes(doc, bad))
    # Клауз нет вовсе — честный отказ судить, не молчание.
    assert any("нечем судить" in p
               for p in m.validate_fit_quotes(doc, {}))


def test_fit_listing_teaches_quote_carrier():
    """Блок 3 run-0005 (Тест, 2.09): беспараметрные семьи (ночёвки,
    окна, равномерность) уходили в C — задание генератора не говорило
    о цитате-носителе (К-1 был только в валидаторах)."""
    text = m.template_fit_listing()
    assert "цитата-носитель" in text
    assert "params не" in text and "quote" in text
    assert "layover_penalty" in text


def test_soft_logic_cannot_be_required():
    """Прогон H0 run-0005 (2.09): SOFT-правило с logic.enforce=required
    останавливало кнопку «без исполнителя» при живой посадке A —
    мягкое правило исполняется штрафом семьи, логика только
    post-check."""
    rule = {"id": "R-X", "kind": "SOFT", "zone": "PAIRING",
            "params": [{"name": "long_duty_threshold", "value": 720,
                        "unit": "duration_minutes",
                        "as_written": "12:00", "relation": "max"}]}
    logic = {"subject": "duty", "enforce": "required",
             "demand": {"kind": "duration_within", "relation": "max",
                        "param": "long_duty_threshold",
                        "measure": "span", "as_written": "12:00"},
             "source_params": ["long_duty_threshold"]}
    errs = m.validate_logic(logic, rule)
    assert any("enforce=required" in e for e in errs)
    logic["enforce"] = "post-check"
    assert not any("enforce=required" in e
                   for e in m.validate_logic(logic, rule))
    rule["kind"] = "HARD"
    logic["enforce"] = "required"
    assert not any("enforce=required" in e
                   for e in m.validate_logic(logic, rule))


def test_registry_fingerprint_stable():
    assert m.registry_fingerprint() == REV
    assert REV and REV != "unregistered"
    # Шаг 3 переезда (07.09.2026): при доступном каталоге отпечаток
    # посадки — отпечаток КАТАЛОГА (64 hex) из одной отметки моста: у
    # строк и у manifest.catalog пакета он один по построению.
    import re
    from cdpm import catalog_bridge as bridge
    stamp = bridge.stamp()
    if stamp:
        assert REV == stamp["fingerprint"]
        assert re.fullmatch(r"[0-9a-f]{64}", REV)


def test_pinned_catalog_folder_switches_fingerprint_and_families(monkeypatch,
                                                                 tmp_path):
    """Шаг 7 переезда: закрепление базы у комплекта — ЗАКРЕПЛЁННАЯ ВЕРСИЯ
    КАТАЛОГА (CREW_TEMPLATE_CATALOG), а не рукописный срез. Копия
    каталога без одной семьи даёт другую карту семей и другой отпечаток
    посадок; режим среза базы (CREW_VMP_TEMPLATE_BASE) продуктом больше
    не читается — при заданной переменной ответы те же."""
    import catalog_copies as cc
    from cdpm import catalog_bridge as bridge
    folder = cc.copy_catalog(tmp_path, drop=("night_series",))
    monkeypatch.setenv(bridge.ENV_CATALOG, str(folder))
    bridge.forget()
    try:
        fams = m.template_families()
        assert fams and all(isinstance(v, list) for v in fams.values())
        assert "duty_limits" in fams and "night_series" not in fams
        pinned = m.registry_fingerprint()
        assert pinned != REV and len(pinned) == 64
        # Переменная среза базы мостом не читается: ни уважается, ни
        # отвергается (переходное правило §3 до части COSMOS 7-Б).
        monkeypatch.setenv("CREW_VMP_TEMPLATE_BASE",
                           str(tmp_path / "NSH-нет-такого.json"))
        assert m.template_families() == fams
        assert m.registry_fingerprint() == pinned
    finally:
        monkeypatch.delenv("CREW_VMP_TEMPLATE_BASE", raising=False)
        monkeypatch.delenv(bridge.ENV_CATALOG, raising=False)
        bridge.forget()
    assert m.registry_fingerprint() == REV
    assert "night_series" in m.template_families()


def test_ensure_fit_questions_structural_key():
    """П-3: B-посадка получает вопрос-подтверждение со структурным
    ключом fit-{семья}-{параметр}; идемпотентно; привязка
    pending_question; модель остаётся валидной."""
    doc = sample_model()
    r = _rule(doc)
    r["template_fit"] = dict(_fit_a(), category="B",
                             assumption="сутки по времени базы")
    created = m.ensure_fit_questions(doc)
    assert created == ["Q-2-fit-duty_limits-max_duty_duration"]
    assert r["template_fit"]["pending_question"] == created[0]
    q = next(x for x in doc["questions"] if x["id"] == created[0])
    assert q["part"] == "2"
    assert q["answer_format"]["type"] == "choice"
    assert "сутки по времени базы" in q["text"]
    assert q["rule_refs"] == [r["id"]]
    assert m.ensure_fit_questions(doc) == []
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []


CLAUSE = "Продолжительность смены — не более 12:00 подряд."


def test_accept_fit_candidate_clean_a():
    c = {"kind": "HARD", "template_fit": {
        "category": "A", "family": "duty_limits",
        "params": {"max_duty_duration": {
            "value": 720, "quote": "не более 12:00"}},
        "confidence": 88}}
    fit, conf, note = m.accept_fit_candidate(c, CLAUSE)
    assert fit["category"] == "A"
    assert fit["registry_rev"] == REV
    assert "confidence" not in fit
    assert conf == 88
    assert note is None


def test_accept_fit_candidate_failures_become_c():
    # Модель не дала посадку машинному правилу.
    fit, conf, note = m.accept_fit_candidate({"kind": "HARD"}, CLAUSE)
    assert fit["category"] == "C" and "не выполнила" in fit["gap"]
    # Цитата не из клаузы.
    c = {"kind": "HARD", "template_fit": {
        "category": "A", "family": "duty_limits",
        "params": {"max_duty_duration": {
            "value": 720, "quote": "какой-то пересказ"}}}}
    fit, conf, note = m.accept_fit_candidate(c, CLAUSE)
    assert fit["category"] == "C" and "отброшена" in fit["gap"]
    assert "дословной подстрокой" in note
    # value расходится с временем в цитате (12:00 = 720).
    c["template_fit"]["params"]["max_duty_duration"] = {
        "value": 700, "quote": "не более 12:00"}
    fit, conf, note = m.accept_fit_candidate(c, CLAUSE)
    assert fit["category"] == "C" and "не совпадает" in fit["gap"]
    # Чужая семья у параметра.
    c["template_fit"] = {"category": "A", "family": "rest_floors",
                         "params": {"max_duty_duration": {
                             "value": 720,
                             "quote": "не более 12:00"}}}
    fit, conf, note = m.accept_fit_candidate(c, CLAUSE)
    assert fit["category"] == "C" and "другой семье" in fit["gap"]


def test_accept_fit_candidate_review_fixes():
    """Фиксы ревью пакета: диапазон времён в цитате законен (value
    совпадает с ЛЮБЫМ временем), строковое value-число законно,
    посторонний ключ и pending_question кандидата — честная C."""
    clause = "Ночная смена с 22:00 до 06:00 не длиннее 6:00."
    c = {"kind": "HARD", "template_fit": {
        "category": "A", "family": "duty_limits",
        "params": {"max_duty_duration": {
            "value": 360, "quote": "с 22:00 до 06:00 не длиннее "
                                   "6:00"}}}}
    fit, _, note = m.accept_fit_candidate(c, clause)
    assert fit["category"] == "A" and note is None
    c["template_fit"]["params"]["max_duty_duration"]["value"] = "360"
    fit, _, note = m.accept_fit_candidate(c, clause)
    assert fit["category"] == "A" and note is None
    c["template_fit"]["extra_key"] = 1
    fit, _, note = m.accept_fit_candidate(c, clause)
    assert fit["category"] == "C" and "посторонние" in fit["gap"]
    del c["template_fit"]["extra_key"]
    c["template_fit"]["pending_question"] = "Q-чужой"
    fit, _, note = m.accept_fit_candidate(c, clause)
    assert fit["category"] == "C" \
        and "pending_question" in fit["gap"]


def test_paramless_family_quote_carrier():
    """К-1 (эталон нано): семья без словарных параметров сажается
    цитатой-носителем quote; семья со словарными параметрами обязана
    дать params; quote при C/D запрещён."""
    clause = "Ночёвки вне базы нежелательны, штраф за каждую."
    c = {"kind": "SOFT", "template_fit": {
        "category": "A", "family": "layover_penalty",
        "quote": "штраф за каждую"}}
    fit, _, note = m.accept_fit_candidate(c, clause)
    assert fit["category"] == "A" and note is None
    # Без quote и без params — C.
    c["template_fit"] = {"category": "A",
                         "family": "layover_penalty"}
    fit, _, note = m.accept_fit_candidate(c, clause)
    assert fit["category"] == "C" and "params либо quote" in fit["gap"]
    # Семья со словарными параметрами требует params, quote не спасает.
    c["template_fit"] = {"category": "A", "family": "duty_limits",
                         "quote": "штраф за каждую"}
    fit, _, note = m.accept_fit_candidate(c, clause)
    assert fit["category"] == "C" and "params обязателен" in fit["gap"]
    # Валидатор модели: беспараметрная посадка с quote валидна.
    doc = sample_model()
    r = next(x for x in doc["rules"] if x["kind"] == "SOFT")
    r["template_fit"] = {"category": "A",
                         "family": "layover_penalty",
                         "quote": "штраф за каждую",
                         "registry_rev": REV}
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []
    # Цитатный слой: носитель судится против клауз.
    key = (r["sources"][0]["sid"], r["sources"][0]["anchor"])
    assert m.validate_fit_quotes(doc, {key: clause}) == []
    bad = m.validate_fit_quotes(doc, {key: "другая клауза"})
    assert any("цитата-носитель" in p for p in bad)
    # quote при C — запрещён.
    r["template_fit"] = {"category": "C", "registry_rev": REV,
                         "gap": "нет механики",
                         "quote": "штраф за каждую"}
    assert any("'quote' запрещено" in p for p in m.validate_model(doc))


def test_accept_fit_candidate_non_machine_dropped():
    c = {"kind": "DATA", "template_fit": {"category": "A"}}
    fit, conf, note = m.accept_fit_candidate(c, CLAUSE)
    assert fit is None and "немашинного вида" in note
    assert m.accept_fit_candidate({"kind": "DEF"}, CLAUSE) \
        == (None, None, None)


def test_align_fit_with_kind():
    doc = {"meta": {"fit_contract": True}}
    r = {"kind": "HARD"}
    m.align_fit_with_kind(doc, r, "тест")
    assert r["template_fit"]["category"] == "C"
    r["kind"] = "DATA"
    m.align_fit_with_kind(doc, r, "тест")
    assert "template_fit" not in r
    # Без контракта — машинный вид без посадки не дополняется.
    r2 = {"kind": "SOFT"}
    m.align_fit_with_kind({"meta": {}}, r2, "тест")
    assert "template_fit" not in r2


# ------------------------------------------------- условия семьи из каталога

def _fit_ext(params=None):
    return {"category": "A", "registry_rev": REV,
            "family": "duty_extension_penalty",
            "params": params or {
                "duty_extension_cap": {"value": 840,
                                       "quote": "не более 14:00"},
                "long_duty_threshold": {"value": 720,
                                        "quote": "свыше 12:00"},
                "extension_bases": {"value": "ABC, XYZ",
                                    "quote": "для экипажей баз ABC и XYZ"},
                "extension_consent": {"value": True,
                                      "quote": "с согласия экипажа"}}}


def test_fit_conditions_of_owner_family_from_catalog():
    """Остаток шага 2 каталога (ответ CDPM по схеме каталога, п.1,
    07.09.2026): условия исключения семьи (extension_bases — перечень
    кодов, extension_consent — да/нет) допускаются в params посадки
    ТОЛЬКО у семьи-владельца; единица — из записи условия каталога;
    в PARAMS условий нет; коды условия — среди обозначений ВМП."""
    conds = m.family_conditions("duty_extension_penalty")
    assert set(conds) == {"extension_bases", "extension_consent"}
    assert conds["extension_bases"]["unit"] == "code_list"
    assert conds["extension_bases"]["relation"] == "in"
    assert conds["extension_consent"]["unit"] == "flag"
    assert conds["extension_consent"]["relation"] == "yes"
    assert m.family_conditions("duty_limits") == {}
    assert "extension_bases" not in m.PARAMS
    assert "extension_consent" not in m.PARAMS
    assert m.fit_param_unit("duty_extension_penalty",
                            "extension_bases") == "code_list"
    assert m.fit_param_unit("duty_limits", "extension_bases") is None
    assert m.fit_param_unit("duty_extension_penalty",
                            "duty_extension_cap") == "duration_minutes"
    doc = sample_model()
    r = _rule(doc)
    r["template_fit"] = _fit_ext()
    assert m.validate_template_fit(doc) == []
    codes = {(c["param"], c["code"]) for c in m.parameter_codes(doc)
             if c["rule"] == r["id"]}
    assert {("extension_bases", "ABC"), ("extension_bases", "XYZ")} <= codes


def test_fit_conditions_discipline():
    """Условие чужой семьи, значение не по единице условия и условие
    без цитаты — нарушения посадки."""
    doc = sample_model()
    r = _rule(doc)
    r["template_fit"] = dict(_fit_a(), params={
        "max_duty_duration": {"value": 720, "quote": "не более 12:00"},
        "extension_consent": {"value": True, "quote": "с согласия"}})
    assert any("условие 'extension_consent' принадлежит семье "
               "'duty_extension_penalty', не 'duty_limits'" in p
               for p in m.validate_template_fit(doc))
    bad = _fit_ext()
    bad["params"]["extension_bases"]["value"] = ["ABC"]
    r["template_fit"] = bad
    assert any("value перечня кодов" in p
               for p in m.validate_template_fit(doc))
    bad = _fit_ext()
    bad["params"]["extension_bases"]["value"] = "XYZ, ABC"
    r["template_fit"] = bad
    assert any("не канонизирован" in p for p in m.validate_template_fit(doc))
    bad = _fit_ext()
    bad["params"]["extension_consent"]["value"] = "да"
    r["template_fit"] = bad
    assert any("value флага — true/false" in p
               for p in m.validate_template_fit(doc))
    bad = _fit_ext()
    bad["params"]["extension_consent"]["quote"] = ""
    r["template_fit"] = bad
    assert any("без value/quote" in p for p in m.validate_template_fit(doc))
    # Имя вне словаря — прежняя формулировка с уточнением про условия.
    r["template_fit"] = dict(
        _fit_a(), params={"vydumannyj": {"value": 1, "quote": "х"}})
    assert any("вне словаря PARAMS и условий каталога" in p
               for p in m.validate_template_fit(doc))


def test_accept_fit_candidate_canonizes_conditions():
    """Приём посадки кандидата: условия семьи проходят, перечень кодов
    канонизируется кодом (порядок в ответе модели — не повод для C),
    условие чужой семьи — честная C со следом."""
    clause = ("Продление смены свыше 12:00 не более 14:00 допускается "
              "для экипажей баз XYZ и ABC только с согласия экипажа")
    fit = {"category": "A", "family": "duty_extension_penalty",
           "params": {
               "duty_extension_cap": {"value": 840,
                                      "quote": "не более 14:00"},
               "long_duty_threshold": {"value": 720,
                                       "quote": "свыше 12:00"},
               "extension_bases": {"value": "XYZ, ABC",
                                   "quote": "для экипажей баз XYZ и ABC"},
               "extension_consent": {"value": True,
                                     "quote": "с согласия экипажа"}}}
    got, _conf, note = m.accept_fit_candidate(
        {"kind": "SOFT", "template_fit": fit}, clause)
    assert got["category"] == "A", note
    assert got["params"]["extension_bases"]["value"] == "ABC, XYZ"
    assert got["params"]["extension_consent"]["value"] is True
    alien = {"category": "A", "family": "duty_limits",
             "params": {"max_duty_duration": {"value": 720,
                                              "quote": "свыше 12:00"},
                        "extension_consent": {"value": True,
                                              "quote": "с согласия экипажа"}}}
    got, _conf, note = m.accept_fit_candidate(
        {"kind": "HARD", "template_fit": alien}, clause)
    assert got["category"] == "C" and "принадлежит семье" in got["gap"]
    wrong = {"category": "A", "family": "duty_extension_penalty",
             "params": dict(fit["params"], extension_consent={
                 "value": "да", "quote": "с согласия экипажа"})}
    got, _conf, note = m.accept_fit_candidate(
        {"kind": "SOFT", "template_fit": wrong}, clause)
    assert got["category"] == "C" and "true/false" in got["gap"]


def test_fit_listing_teaches_conditions():
    text = m.template_fit_listing()
    assert ("условия: extension_bases (перечень кодов, in), "
            "extension_consent (да/нет, yes)") in text
    assert "Условия исключения семьи" in text


def test_without_catalog_validator_refuses_with_one_line(monkeypatch,
                                                         tmp_path):
    """Шаг 7: каталог обязателен. Без него условия и словари брать негде
    — `family_conditions()` отказывает словами, а РУБЕЖ `validate_model`
    отдаёт ОДНУ строку проблемы (не трассировку и не молчание), чтобы
    гейт пакета оставался списком проблем. После возврата каталога —
    условия снова есть."""
    import pytest
    from cdpm import catalog_bridge as bridge
    monkeypatch.setenv(bridge.ENV_CATALOG, str(tmp_path / "no-such-catalog"))
    bridge.forget()
    try:
        with pytest.raises(bridge.CatalogRequired) as e:
            m.family_conditions()
        assert "каталог шаблонов обязателен" in str(e.value)
        assert e.value.code == "catalog-unavailable"
        doc = sample_model()
        _rule(doc)["template_fit"] = _fit_ext()
        problems = m.validate_model(doc)
        assert len(problems) == 1, problems
        assert problems[0].startswith("каталог шаблонов недоступен: ")
        assert "не судятся" in problems[0]
        # Тот же ответ и без контракта посадки: без словаря не судится
        # ни один параметр.
        doc2 = sample_model()
        doc2["meta"].pop("fit_contract", None)
        assert m.validate_model(doc2) == problems
    finally:
        monkeypatch.delenv(bridge.ENV_CATALOG, raising=False)
        bridge.forget()
    assert m.family_conditions("duty_extension_penalty")
