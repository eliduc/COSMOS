

def test_pairing_criterion_derived_and_gates_modes():
    """Спецификация 1.9: строка «критерий оптимизации пейринга …»
    даёт машинный блок criterion; без неё режим optimization
    пейринга честно снимается из allowed_modes."""
    from cdpm import export_solver as es
    from cdpm import export_task_package as tp
    row = {"id": "O-CRIT", "zone": "PAIRING",
           "title": "Критерий оптимизации пейринга",
           "statement": "минимум человеко-дней командировок; цена "
                        "рабочего дня (смены) — 100 условных единиц"}
    doc = {"objectives": [row], "rules": []}
    c = es.derive_pairing_criterion(doc)
    assert c == {"kind": "min_person_days", "rate_per_duty": 100,
                 "as_written": row["statement"],
                 "source_rule": "O-CRIT"}
    assert es.derive_pairing_criterion(
        {"objectives": [], "rules": []}) is None


def test_area_qualifier_restored_from_trace():
    """Живой урок 2026-08-26 (вскрытие фантомных −10 стыковок):
    параметр области «45 (база)» без машинного уточнителя ужесточал
    ОБЕ ручки потребителя (вне базы 40→45). Область, однозначно
    названная в следе, восстанавливается машинно с примечанием;
    двусмысленный след не трогается."""
    from cdpm import model as m
    # Живой класс run-0057: след параметра «45 мин» без области,
    # область — в формальной записи правила.
    doc = {"rules": [
        {"id": "R-A", "formal": "turnaround_time(база) ≥ 45 мин",
         "params": [
            {"name": "min_turnaround", "value": 45,
             "unit": "duration_minutes", "relation": "min",
             "as_written": "45 мин"}]},
        {"id": "R-B", "statement": "стыковка вне базы не менее 40",
         "params": [
            {"name": "min_turnaround", "value": 40,
             "unit": "duration_minutes", "relation": "min",
             "as_written": "40 мин"}]},
        {"id": "R-C",
         "formal": "sit_time(база | вне базы) ≥ 45 мин",
         "params": [
            {"name": "min_turnaround", "value": 45,
             "unit": "duration_minutes", "relation": "min",
             "as_written": "45 мин"}]},
    ]}
    notes = m.restore_area_qualifiers(doc)
    p_a, p_b, p_c = (doc["rules"][i]["params"][0] for i in range(3))
    assert p_a["qualifier"] == "база"
    assert p_b["qualifier"] == "вне базы"
    assert "qualifier" not in p_c            # двусмысленно — не трогаем
    assert len(notes) == 2 and "восстановлен из следа" in notes[0]
    assert "восстановлен" in doc["rules"][0]["note"]
    # Детерминизм: повторный вызов ничего не меняет.
    assert m.restore_area_qualifiers(doc) == []


def test_reinstate_question_full_cycle():
    """Живой класс: носитель условного пола снят конфликт-выбором,
    формулировка несёт условие (разница поясов) — генератор
    даёт вопрос-пересмотр со штатной условной ветвью; ответ «а»
    возвращает правило в действие, ответ «б» оставляет снятым; код
    правило не воскрешает."""
    from cdpm import answers as ans
    from cdpm import model as m
    rule = {"id": "R-TZ", "title": "Пол при разнице поясов",
            "kind": "OUT", "zone": "ROSTERING",
            "statement": "При нахождении вне базы и разнице в "
                         "часовых поясах >= 3 часов минимальное "
                         "время отдыха не менее 12 часов.",
            "note": "отменено: выбрано значение 600 минут",
            "sources": [{"sid": "S1", "anchor": "п.9"}],
            "params": [{"name": "min_rest_outstation", "value": 720,
                        "unit": "duration_minutes",
                        "as_written": "12 часов",
                        "relation": "min",
                        "qualifier": "вне базы"}]}
    p2 = {k: v for k, v in rule["params"][0].items()
          if k != "qualifier"}
    other = dict(rule, id="R-TZ2",
                 sources=[{"sid": "S1", "anchor": "п.21"}],
                 params=[p2])
    doc = {"meta": {"customer": "Т", "mode": 1,
                    "cdpm_version": "0.1", "revision": 2},
           "sources": [{"sid": "S1", "file": "a.md",
                        "sha256": "0" * 64}],
           "rules": [rule, other], "questions": [],
           "objectives": []}
    created = m.ensure_reinstate_questions(doc)
    assert len(created) == 1                 # семейство — один вопрос
    q = next(x for x in doc["questions"] if x["id"] == created[0])
    assert "условное правило" in q["text"]
    assert q["rule_refs"] == ["R-TZ"]        # кандидат с уточнителем
    # Контракт части 1: источники всего семейства, не менее двух.
    assert len(q["source_refs"]) == 2
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    assert rule["conditional"]["branches"] == {"а": "HARD",
                                               "б": "OUT"}
    # Повтор стадии — дубль не создаётся.
    assert m.ensure_reinstate_questions(doc) == []
    # Ответ «а» — правило вернулось штатной веткой.
    summary = ans.apply_answers(doc, {created[0]: "а"})
    assert summary["applied"] == 1
    assert rule["kind"] == "HARD"
    assert "conditional" not in rule and "blockers" not in rule
    assert other["kind"] == "OUT"            # прочие остаются снятыми


def test_reinstate_single_source_family_skipped_honestly():
    """Семейство с единственным источником: контракт части 1 требует
    двух ссылок — вопрос не задаётся, след на носителе."""
    from cdpm import model as m
    rule = {"id": "R-solo", "title": "Пол", "kind": "OUT",
            "zone": "ROSTERING",
            "statement": "При разнице в часовых поясах >= 3 часов "
                         "отдых не менее 12 часов.",
            "note": "отменено: выбрано значение 600",
            "sources": [{"sid": "S1", "anchor": "п.9"}],
            "params": [{"name": "min_rest_outstation", "value": 720,
                        "unit": "duration_minutes",
                        "as_written": "12 часов",
                        "relation": "min"}]}
    doc = {"rules": [rule], "questions": []}
    assert m.ensure_reinstate_questions(doc) == []
    assert "требует двух" in rule["note"]
    assert "conditional" not in rule
    # Повтор не зацикливается («пересмотр» в примечании).
    assert m.ensure_reinstate_questions(doc) == []


def test_param_relation_restored_from_name_nature():
    """Живой класс: формулировка требует минимум («не менее …»),
    а запись min_rest_outstation несла relation «max» — решатель
    честно отверг все пороги пакета. Отношение чинится по природе
    словарного имени при подтверждении формулировкой; двусмысленная
    формулировка не трогается."""
    from cdpm import model as m
    doc = {"rules": [
        {"id": "R-38", "kind": "HARD",
         "statement": "Минимальное время отдыха вне базы >= "
                      "предыдущего СПВ или 10 часов.",
         "params": [{"name": "min_rest_outstation", "value": 600,
                     "unit": "duration_minutes",
                     "as_written": "10 часов", "relation": "max"}]},
        {"id": "R-AMB", "kind": "HARD",
         "statement": "не менее 2 и не более 4",
         "params": [{"name": "min_days_off", "value": 2,
                     "unit": "count", "as_written": "2",
                     "relation": "max"}]},
    ]}
    notes = m.restore_param_relations(doc)
    assert doc["rules"][0]["params"][0]["relation"] == "min"
    assert "исправлено на 'min'" in doc["rules"][0]["note"]
    # Двусмысленно («не менее … не более») — не трогаем.
    assert doc["rules"][1]["params"][0]["relation"] == "max"
    assert len(notes) == 1
    assert m.restore_param_relations(doc) == []
    # Производящий путь чинит то же при валидной записи.
    kept, notes2 = m.sanitize_params(
        [{"name": "min_rest_outstation", "value": 600,
          "unit": "duration_minutes", "as_written": "10 часов",
          "relation": "max"}], "HARD",
        rule_text="Минимальный отдых вне базы не менее 10 часов")
    assert kept[0]["relation"] == "min"
    assert any("противоречило природе" in n for n in notes2)


def test_crew_variant_qualifier_restored_and_size_derived():
    """Живой случай одной из линий: базовый состав 2 (действует) и
    расширенный 3 (только продлённый FDP) — оба записи crew_size eq
    без уточнителя, производная манифеста честно видела конфликт и
    молчала. Миграция ставит расширенной записи уточнитель из
    формулировки — численность места выводится: 2."""
    from cdpm import export_task_package as tp
    from cdpm import model as m
    doc = {"rules": [
        {"id": "R-BASE", "kind": "DATA",
         "statement": "Базовый минимальный состав летного экипажа "
                      "составляет 2 человека (1 КВС + 1 ВП).",
         "params": [{"name": "crew_size", "value": 2,
                     "unit": "count", "relation": "eq",
                     "as_written": "2 ЧЛЭ"}]},
        {"id": "R-EXT", "kind": "DATA",
         "statement": "Расширенный состав летного экипажа составляет "
                      "3 человека (только для продлённого FDP).",
         "params": [{"name": "crew_size", "value": 3,
                     "unit": "count", "relation": "eq",
                     "as_written": "3 ЧЛЭ"}]},
    ]}
    assert tp.crew_size_of(doc) is None        # честный конфликт
    notes = m.restore_crew_variant_qualifiers(doc)
    assert len(notes) == 1 and "R-EXT" in notes[0]
    ext = doc["rules"][1]["params"][0]
    assert ext["qualifier"] == "расширенный"
    assert "вариант состава условный" in doc["rules"][1]["note"]
    base = doc["rules"][0]["params"][0]
    assert "qualifier" not in base             # базовая — численность
    got = tp.crew_size_of(doc)
    assert got == {"value": 2, "rule": "R-BASE"}
    # Детерминизм: повторный вызов ничего не меняет.
    assert m.restore_crew_variant_qualifiers(doc) == []


def test_unweighted_soft_strips_penalty_modes():
    """Живой случай одной из линий: подтверждённый отказ от весов
    оставил SOFT-строки без веса и без блокировок — режимы со
    штрафами снимаются честно (остаётся «любое допустимое»);
    заблокированная строка (вес ещё придёт) режим не снимает; для
    пакетов младше 1.9 закон не действует."""
    from cdpm import export_task_package as tp
    import tests.test_cdpm_task_package as tpk
    doc = tpk._ready_doc()
    doc["rules"].append(
        {"id": "R-SOFT-1", "kind": "SOFT", "zone": "ROSTERING",
         "title": "Мягкое без веса",
         "statement": "желательно поровну",
         "sources": [{"sid": "S1", "anchor": "п.9"}]})
    modes, _ = tp.allowed_modes(doc)
    assert modes["rostering"] == ["feasibility-hard"]
    assert "feasibility-soft" in modes["pairing"]   # чужая зона цела
    # Строка под открытым вопросом весов: режим со штрафами тоже
    # недоступен — но это решает сама матрица готовности (блокировка),
    # ещё до снятия; итог одинаково честный.
    doc["rules"][-1]["blockers"] = ["Q-3b-GOAL-W"]
    modes2, _ = tp.allowed_modes(doc)
    assert modes2["rostering"] == ["feasibility-hard"]
    # Замороженные пакеты младше 1.9 судятся своим законом.
    doc["rules"][-1].pop("blockers")
    modes3, _ = tp.allowed_modes(doc, spec_version="1.3")
    assert "feasibility-soft" in modes3["rostering"]


def test_sit_param_restored_from_formal_statement():
    """Живой класс строки-11: параметр «40» без единицы был честно
    отброшен канонизацией, а формулировка несёт «≥ 40 мин» —
    миграция при сборке пакета возвращает параметр детерминированно,
    с областью и примечанием. Чужие правила не трогаются."""
    from cdpm import model as m
    doc = {"rules": [
        {"id": "R-11", "kind": "HARD",
         "statement": "turnaround_time(вне базы) ≥ 40 мин",
         "note": "параметр отброшен: min_turnaround: след '40' не "
                 "канонизируется (нет явной единицы времени) — "
                 "сверка канонизации невозможна"},
        {"id": "R-X", "kind": "HARD",
         "statement": "какое-то правило без отброса"},
    ]}
    notes = m.restore_sit_param_from_formal(doc)
    p = doc["rules"][0]["params"][0]
    assert p == {"name": "min_turnaround", "value": 40,
                 "unit": "duration_minutes", "relation": "min",
                 "qualifier": "вне базы", "as_written": "40 мин"}
    assert "восстановлен из формальной записи" in doc["rules"][0]["note"]
    assert len(notes) == 1
    assert "params" not in doc["rules"][1]
    # Детерминизм: повторный вызов ничего не меняет.
    assert m.restore_sit_param_from_formal(doc) == []


def test_sanitize_enriches_bare_number_trace_from_rule_text():
    """Производящий путь: след-голое-число дополняется дословным
    фрагментом формулировки с единицей — параметр не отбрасывается."""
    from cdpm import model as m
    kept, notes = m.sanitize_params(
        [{"name": "min_turnaround", "value": 40,
          "unit": "duration_minutes", "relation": "min",
          "as_written": "40"}], "HARD",
        rule_text="turnaround_time(вне базы) ≥ 40 мин")
    assert kept and kept[0]["as_written"] == "40 мин"
    assert kept[0]["qualifier"] == "вне базы"
    assert any("дополнен единицей" in n for n in notes)


def test_sanitize_params_restores_area_from_trace():
    """Производящий путь (извлечение/патч): параметр области без
    qualifier получает уточнитель из следа либо переданного текста
    правила (формальная запись)."""
    from cdpm import model as m
    kept, notes = m.sanitize_params(
        [{"name": "min_turnaround", "value": 45,
          "unit": "duration_minutes", "relation": "min",
          "as_written": "45 мин"}], "HARD",
        rule_text="turnaround_time(база) ≥ 45 мин")
    assert kept[0]["qualifier"] == "база"
    assert any("восстановлен из следа" in n for n in notes)


def test_pairing_criterion_recognized_after_reformulation():
    """Живой урок run-0057: извлечение переформулировало строку
    критерия без слова «критерий» — распознавание идёт по содержанию
    (человеко-дни + минимизация) и числу ставки."""
    from cdpm import export_solver as es
    row = {"id": "O-R", "zone": "PAIRING",
           "title": "Минимум человеко-дней командировок",
           "statement": "minimize Σ(человеко-дни командировок) при "
                        "стоимости одного рабочего дня (смены) = "
                        "100 условных единиц"}
    c = es.derive_pairing_criterion({"objectives": [row], "rules": []})
    assert c and c["kind"] == "min_person_days"
    assert c["rate_per_duty"] == 100
    assert c["source_rule"] == "O-R"


def test_штрафная_целевая_возвращается_мягким_правилом():
    """Дефект 1 репетиции демо (2026-08-29): извлечение изменчиво
    кладёт штрафную строку без веса то мягким правилом, то целевой.
    Миграция детерминированно возвращает её SOFT-правилом зоны;
    настоящая целевая (без маркера «вес не установлен») не трогается;
    идемпотентно."""
    from cdpm import model as m
    doc = {"rules": [], "objectives": [
        {"id": "O-S9-табл-1-строка-2-кол-3", "zone": "PAIRING",
         "title": "Штраф за ночёвки",
         "statement": "Минимизировать число ночёвок; вес штрафа "
                      "документами не установлен",
         "direction": "minimize",
         "sources": [{"sid": "S9", "anchor": "т1с2"}]},
        # P-N-SHD 2026-08-30: «минимизировать разброс кредитных
        # часов» классифицируется семьёй balanced_workload и сам
        # мигрирует правилом — настоящей целевой (не трогается)
        # служит стоимость, вне закрытого словаря семей.
        {"id": "O-REAL", "zone": "ROSTERING",
         "title": "Настоящая целевая",
         "statement": "Минимизировать суммарную стоимость решения",
         "direction": "minimize", "metric": "total_cost",
         "sources": [{"sid": "S9", "anchor": "т1с3"}]},
    ], "questions": []}
    notes = m.restore_soft_penalty_rules(doc)
    assert len(notes) == 1
    assert [o["id"] for o in doc["objectives"]] == ["O-REAL"]
    rule = doc["rules"][0]
    assert rule["id"] == "R-S9-табл-1-строка-2-кол-3"
    assert rule["kind"] == "SOFT" and rule["zone"] == "PAIRING"
    assert rule["sources"] == [{"sid": "S9", "anchor": "т1с2"}]
    # Идемпотентность: повтор ничего не меняет.
    assert m.restore_soft_penalty_rules(doc) == []
    # Вопрос весов зоны блокирует новую мягкую строку штатно.
    qid = m.ensure_goal_question(doc, "3a")
    assert qid == "Q-3a-GOAL"
    assert "Q-3a-GOAL" in (rule.get("blockers") or [])
