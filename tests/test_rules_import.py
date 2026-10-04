"""Тесты рамки импорта правил (_rules_import.py): реальные приёмники,
нейтральный обменный формат, честные отказы закрытых форматов; с
2026-07-24 — текстовые приёмники (подмножество Rave, таблицы норм,
XML-контейнеры) и шаблоны Яруса 2б."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from _rules_import import (FORMATS, TEXT_HANDLERS,           # noqa: E402
                           RulesImportError, detect_format,
                           detect_format_text, import_rules,
                           import_rules_text)
import _rule_templates as templates                          # noqa: E402
from tests.test_solver_adapter import make_solver_only       # noqa: E402
from webapp.rule_packs import _validate_rule_fields          # noqa: E402


def test_detect_format():
    solver = make_solver_only()
    solver["constraintWeightOverrides"] = {"WEEKLY_REST": "HIGH_HIGH"}
    assert detect_format(solver) == "solver-format5"
    assert detect_format({"format": 1, "employees": []}) \
        == "meridian-limits"
    assert detect_format({"format": "cosmos-rules-json"}) \
        == "cosmos-rules-json"
    assert detect_format({"что-то": "другое"}) is None


def test_meridian_limits_importer():
    mer = {"format": 1,
           "employees": [
               {"employeeId": "1", "workTimeLimit": 8640,
                "flightTimeLimit": 6000},
               {"employeeId": "2", "workTimeLimit": 8640,
                "flightTimeLimit": 6000}],
           "duties": [{"dutyId": "D1", "restOverride": 1320},
                      {"dutyId": "D2", "restOverride": 0}]}
    # QQ — синтетическая страна вымышленного перевозчика: файл format:1
    # юрисдикцию не несёт, её обязан подать оператор.
    pack = import_rules(mer, "meridian-limits", "m.json", country="QQ")
    assert pack["country"] == "QQ"
    assert len(pack["rules"]) == 3
    for r in pack["rules"]:
        _validate_rule_fields(r)
    # Единый у всех лимит налёта -> исполняемое правило Яруса 1.
    ftl = next(r for r in pack["rules"] if "налёта" in r["title"])
    assert ftl["constraint"] == {"kind": "parameter",
                                 "parameter": "max_monthly_block_min",
                                 "value": 6000}
    rest = next(r for r in pack["rules"]
                if "переопределений" in r["statement"])
    assert '"count": 1' in rest["constraint"]["note"]


def test_country_required_for_formats_without_it_in_file():
    """Форматы, чьи файлы не несут страну юрисдикции, без явного
    country отказывают словами: пустая страна означала бы «пакет
    действует на все наборы» — тихое расширение области действия."""
    mer = {"format": 1,
           "employees": [{"employeeId": "1", "flightTimeLimit": 6000}],
           "duties": []}
    with pytest.raises(RulesImportError) as e:
        import_rules(mer, "meridian-limits", "m.json")
    assert "country" in str(e.value)
    assert "meridian-limits" in str(e.value)

    solver = make_solver_only()
    solver["constraintWeightOverrides"] = {"WEEKLY_REST": "HIGH_HIGH"}
    with pytest.raises(RulesImportError) as e:
        import_rules(solver, "solver-format5", "s.json")
    assert "country" in str(e.value)
    assert "solver-format5" in str(e.value)
    pack = import_rules(solver, "solver-format5", "s.json", country="QQ")
    assert pack["country"] == "QQ"
    # кривое значение — отказ, а не тихая нормализация
    with pytest.raises(RulesImportError) as e:
        import_rules(mer, "meridian-limits", "m.json", country="Q1")
    assert "INTL" in str(e.value)


def test_country_re_matches_registry_pattern():
    """Страж дрейфа: локальная копия шаблона страны обязана посимвольно
    совпадать с эталоном webapp/registry.COUNTRY_RE."""
    from _rules_import import _COUNTRY_RE
    from webapp.registry import COUNTRY_RE
    assert _COUNTRY_RE.pattern == COUNTRY_RE.pattern


def test_cosmos_rules_json_importer():
    body = {"format": "cosmos-rules-json", "version": 1,
            "name": "Тестовый обмен", "country": "ZZ",
            "source": {"document": "Приказ N1"},
            "rules": [
                {"code": "R1", "title": "Отдых",
                 "statement": "Между нарядами не менее 12 часов.",
                 "template": "gap-threshold",
                 "parameters": {"minutes": 720}, "weight": "HIGH"},
                {"code": "R2", "title": "Стыковка",
                 "statement": "Минимальная стыковка 30 минут.",
                 "tier1": {"parameter": "min_sit_min", "value": 30}},
            ]}
    pack = import_rules(body, "cosmos-rules-json", "x.json")
    assert len(pack["rules"]) == 2
    for r in pack["rules"]:
        _validate_rule_fields(r)
    assert pack["rules"][1]["constraint"]["kind"] == "parameter"
    assert '"template": "gap-threshold"' in \
        pack["rules"][0]["constraint"]["note"]
    # Правило без источника отвергается.
    bad = dict(body, source=None,
               rules=[{"title": "x", "statement": "y"}])
    with pytest.raises(RulesImportError):
        import_rules(bad, "cosmos-rules-json", "x.json")
    # Страна в файле — данные: противоречащее ей явное country — отказ
    # с обоими значениями, совпадение — норма.
    with pytest.raises(RulesImportError) as e:
        import_rules(body, "cosmos-rules-json", "x.json", country="QQ")
    assert "ZZ" in str(e.value) and "QQ" in str(e.value)
    pack = import_rules(body, "cosmos-rules-json", "x.json", country="ZZ")
    assert pack["country"] == "ZZ"


def test_closed_formats_fail_honestly():
    for fmt in ("sabre-altitude", "netline-crew", "aims"):
        assert fmt in FORMATS
        with pytest.raises(RulesImportError) as e:
            import_rules({}, fmt, "sample")
        assert "образцов" in str(e.value)
        assert "догадкам" in str(e.value)
    # Текстовый формат через JSON-вход отклоняется с объяснением.
    with pytest.raises(RulesImportError) as e:
        import_rules({}, "jeppesen-rave", "sample")
    assert "текстовый формат" in str(e.value)


# ------------------------------------------------ подмножество Rave

RAVE_SAMPLE = """
/* демонстрационный модуль вымышленного перевозчика */
module demo_rules
%max_duty% = parameter 13:00 minvalue 10:00 maxvalue 14:00
             remark "Максимальная длительность наряда";
%min_sit_ground% = parameter 30;
%block_time% = sum(leg(duty), leg.%block%);
rule (on) duty_span_check =
  valid duty.%check_rules%;
  duty.%span% <= %max_duty%;
  remark "Длительность наряда не выше предела";
end
rule night_rest =
  valid trip.%has_night%;
  next(duty, duty.%rest%) >= 10:00;
end
foreach weird construct not in subset;
"""


def test_rave_subset_parses_core():
    pack = import_rules_text(RAVE_SAMPLE, "jeppesen-rave", "demo.rave")
    for r in pack["rules"]:
        _validate_rule_fields(r)
    titles = [r["title"] for r in pack["rules"]]
    assert "Параметр Rave %max_duty%" in titles
    assert "Правило Rave duty_span_check" in titles
    assert "Правило Rave night_rest" in titles
    head = pack["rules"][0]
    assert "правил 2" in head["statement"]
    assert "параметров 2" in head["statement"]
    assert "переменных вне правил 1" in head["statement"]
    # неразобранная конструкция честно попала в образцы
    leftovers = next(r for r in pack["rules"]
                     if "Неразобранные" in r["title"])
    assert "foreach weird construct" in leftovers["statement"]
    # время в параметре переведено в минуты внутри структурной записи
    p = next(r for r in pack["rules"]
             if r["title"] == "Параметр Rave %max_duty%")
    assert '"minutes": 780' in p["constraint"]["note"]
    assert p["statement"].startswith("Максимальная длительность наряда")
    # сравнение из тела правила распознано
    rl = next(r for r in pack["rules"]
              if r["title"] == "Правило Rave duty_span_check")
    assert '"op": "<="' in rl["constraint"]["note"]


def test_rave_refuses_non_rave_text():
    with pytest.raises(RulesImportError) as e:
        import_rules_text("просто текст без правил", "jeppesen-rave", "x")
    assert "не файл Rave" in str(e.value)


# ------------------------------------------------ таблица норм

NORM_CSV = (
    "имя;значение;единица;окно;параметр;описание\n"
    "min_rest;10;ч;;;Минимальный отдых между нарядами\n"
    "max_monthly_block;100:00;;месяц;;Месячный предел налёта\n"
    "предел за 28 дней;100;ч;28 дней;max_block_rolling28;Скользящее окно\n"
    "loadfactor;5;слонов;;;Единица не существует\n")


def test_norm_table_csv():
    pack = import_rules_text(NORM_CSV, "norm-table", "нормы.csv")
    for r in pack["rules"]:
        _validate_rule_fields(r)
    by_title = {r["title"]: r for r in pack["rules"]}
    # каноническое имя + совместимое окно -> исполняемый Ярус 1
    rest = by_title["Минимальный отдых между нарядами"]
    assert rest["constraint"] == {"kind": "parameter",
                                  "parameter": "min_rest_min",
                                  "value": 600}
    block = by_title["Месячный предел полётного времени"]
    assert block["constraint"]["value"] == 6000
    # явный шаблон вне Яруса 1 -> запись «ресурса» (Ярус 2б)
    roll = by_title["Предел полётного времени за 28 подряд идущих дней"]
    assert roll["constraint"]["kind"] == "resource"
    assert roll["constraint"]["window"]["max"] == 6000
    assert roll["constraint"]["level"] == "rolling28"
    # неканоническое имя -> структурная запись, без догадок
    other = by_title["loadfactor"]
    assert other["constraint"]["kind"] == "note"


def test_norm_table_json_and_errors():
    rows = [{"name": "max_duty_span", "value": "13:00"},
            {"name": "max_landings_per_duty", "value": 5,
             "unit": "count"}]
    pack = import_rules_text(json.dumps(rows), "norm-table", "n.json")
    kinds = [r["constraint"] for r in pack["rules"]]
    assert {"kind": "parameter", "parameter": "max_duty_span_min",
            "value": 780} in kinds
    assert {"kind": "parameter", "parameter": "max_landings_per_duty",
            "value": 5} in kinds
    with pytest.raises(RulesImportError):
        import_rules_text("не;таблица\n1;2", "norm-table", "x.csv")


# ------------------------------------------------ XML-контейнеры

INRC_XML = """<SchedulingPeriod ID="demo">
 <Contracts>
  <Contract ID="FullTime">
    <MaxConsecutiveWorkingDays>6</MaxConsecutiveWorkingDays>
    <MinRestTime>720</MinRestTime>
    <MaxWorkingWeekends>2</MaxWorkingWeekends>
    <Label>ignore me</Label>
  </Contract>
 </Contracts>
</SchedulingPeriod>"""


def test_roster_xml_inventory():
    pack = import_rules_text(INRC_XML, "roster-xml", "inrc.xml")
    for r in pack["rules"]:
        _validate_rule_fields(r)
    head = pack["rules"][0]
    assert "SchedulingPeriod" in head["title"]
    by_title = {r["title"]: r for r in pack["rules"]}
    # тег со словом Time БЕЗ явной единицы — масштаб не угадывается:
    # структурная запись с честной причиной (ревью 2026-07-24)
    rest = by_title["MinRestTime"]["constraint"]
    assert rest["kind"] == "note"
    assert "не объявлена" in rest["note"]
    days = by_title["MaxConsecutiveWorkingDays"]["constraint"]
    assert days["kind"] == "resource"
    assert days["unit"] == "days"
    assert days["window"]["max"] == 6
    # путь до элемента сохранён в функции продления
    assert "Contract[FullTime]" in days["extension"]
    # «weekend» — счётный предел на весь горизонт, не «неделя»
    wk = by_title["MaxWorkingWeekends"]["constraint"]
    assert wk["unit"] == "count"
    assert wk["level"] == "roster"


def test_roster_xml_honest_units_and_skips():
    """Ревью 2026-07-24: часы конвертируются явно, мусорные значения
    считаются, глубокая вложенность не роняет импорт."""
    xml_text = ("<SchedulingPeriod><Contract ID=\"F\">"
                "<MaxWorkingHours>40</MaxWorkingHours>"
                "<MinRestTime>-5</MinRestTime>"
                "<MaxShifts>6.5</MaxShifts>"
                "<MaxLoad>1e999</MaxLoad>"
                "</Contract></SchedulingPeriod>")
    pack = import_rules_text(xml_text, "roster-xml", "x.xml")
    by_title = {r["title"]: r for r in pack["rules"]}
    hours = by_title["MaxWorkingHours"]["constraint"]
    assert hours == {"kind": "resource", "resource": "MaxWorkingHours",
                     "window": {"min": None, "max": 2400},
                     "unit": "minutes", "level": "roster",
                     "extension": hours["extension"]}
    # -5, 6.5 и 1e999 не приняты — и это видно в сводке и образцах
    assert '"skipped_values": 3' in pack["rules"][0]["constraint"]["note"]
    skipped = by_title["Не принятые значения (образцы)"]
    assert "MinRestTime=-5" in skipped["statement"]
    # глубокая вложенность — итеративный обход, не рекурсия
    deep = ("<SchedulingPeriod>" + "<a>" * 5000
            + "<MaxShifts>3</MaxShifts>" + "</a>" * 5000
            + "</SchedulingPeriod>")
    pack2 = import_rules_text(deep, "roster-xml", "deep.xml")
    assert any(r["title"] == "MaxShifts" for r in pack2["rules"])


def test_roster_xml_doctype_beyond_4kb_rejected():
    """Страж действует по всему тексту: длинный комментарий перед
    DOCTYPE больше не обходит проверку (ревью 2026-07-24)."""
    evil = ("<!-- " + "x" * 5000 + " -->"
            "<!DOCTYPE a [<!ENTITY b \"c\">]><a>&b;</a>")
    with pytest.raises(RulesImportError) as e:
        import_rules_text(evil, "roster-xml", "evil.xml")
    assert "DOCTYPE" in str(e.value)


def test_roster_xml_rejects_doctype():
    with pytest.raises(RulesImportError) as e:
        import_rules_text("<!DOCTYPE x [<!ENTITY a 'b'>]><a>&a;</a>",
                          "roster-xml", "x.xml")
    assert "DOCTYPE" in str(e.value)


# ------------------------------------------------ автоопределение текста

def test_detect_format_text():
    assert detect_format_text(RAVE_SAMPLE) == "jeppesen-rave"
    assert detect_format_text(NORM_CSV) == "norm-table"
    assert detect_format_text(INRC_XML) == "roster-xml"
    solver = make_solver_only()
    solver["constraintWeightOverrides"] = {"WEEKLY_REST": "HIGH_HIGH"}
    assert detect_format_text(json.dumps(solver)) == "solver-format5"
    assert detect_format_text(json.dumps(
        [{"name": "min_rest", "value": 600}])) == "norm-table"
    assert detect_format_text("просто текст") is None
    assert set(TEXT_HANDLERS) == {"jeppesen-rave", "norm-table",
                                  "roster-xml"}


# ------------------------------ сценарии адверсарного ревью 2026-07-24

def test_rave_end_attribute_and_if_block_do_not_truncate():
    """HIGH-находка: `.end` и `if…end` внутри выражений не обрывают
    тело правила; терминатор — только отдельный оператор end."""
    text = ("rule c = valid trip.start < trip.end;"
            " trip.block <= %max_block%; end\n"
            "rule d = valid if leg.dh then true else false end;"
            " leg.block <= 10:00; end\n")
    pack = import_rules_text(text, "jeppesen-rave", "t.rave")
    by_title = {r["title"]: r for r in pack["rules"]}
    c = json.loads(by_title["Правило Rave c"]["constraint"]["note"])
    assert c["valid"] == "trip.start < trip.end"
    assert {"lhs": "trip.block", "op": "<=",
            "rhs": "%max_block%"} in c["comparisons"]
    d = json.loads(by_title["Правило Rave d"]["constraint"]["note"])
    assert d["valid"] == "if leg.dh then true else false end"
    assert any(x["lhs"] == "leg.block" for x in d["comparisons"])
    head = by_title["Разбор файла Rave: состав и границы"]
    assert "правил 2" in head["statement"]
    assert "Неразобранных конструкций нет" in head["statement"]


def test_rave_strings_survive_comments_and_semicolons():
    """Комментарии и `;` внутри строк remark не рвут разбор."""
    text = ('%min_rest% = parameter 11:00 '
            'remark "см. https://док.пример/ома; раздел 7.2";\n'
            '%x% = parameter 1 remark "доля a/*b*/c";\n')
    pack = import_rules_text(text, "jeppesen-rave", "t.rave")
    by_title = {r["title"]: r for r in pack["rules"]}
    p = by_title["Параметр Rave %min_rest%"]
    assert p["statement"] == "см. https://док.пример/ома; раздел 7.2"
    assert by_title["Параметр Rave %x%"]["statement"] == "доля a/*b*/c"
    head = by_title["Разбор файла Rave: состав и границы"]
    assert "параметров 2" in head["statement"]
    assert "Неразобранных конструкций нет" in head["statement"]


def test_rave_valid_prefix_identifier_and_empty_remark():
    """`valid_days` — не условие valid; пустой remark не рождает
    запись с пустым утверждением."""
    text = ('rule r = valid_days >= 2; valid trip.ok; remark ""; end\n')
    pack = import_rules_text(text, "jeppesen-rave", "t.rave")
    rec = next(r for r in pack["rules"]
               if r["title"] == "Правило Rave r")
    body = json.loads(rec["constraint"]["note"])
    assert body["valid"] == "trip.ok"
    assert any(x["lhs"] == "valid_days" for x in body["comparisons"])
    assert rec["statement"].strip()          # не пустое
    from webapp.rule_packs import _validate_rule_fields as v
    v(rec)


def test_rave_unterminated_rule_counts_as_unparsed():
    text = "rule broken = valid trip.ok; trip.a <= 5"
    with pytest.raises(RulesImportError):
        # единственная конструкция не завершена -> правил нет вовсе
        import_rules_text(text, "jeppesen-rave", "t.rave")
    text2 = "%p% = parameter 5;\nrule broken = valid trip.ok; trip.a <= 5"
    pack = import_rules_text(text2, "jeppesen-rave", "t.rave")
    head = pack["rules"][0]
    assert "правил 0" in head["statement"]
    assert "Неразобранных конструкций: 3" in head["statement"]
    samples = next(r for r in pack["rules"]
                   if "Неразобранные" in r["title"])
    assert "нет завершающего end" in samples["statement"]


def test_rave_capacity_cap_is_honest():
    text = "\n".join(f"%p{i}% = parameter {i + 1};" for i in range(300))
    pack = import_rules_text(text, "jeppesen-rave", "big.rave")
    assert len(pack["rules"]) == 196
    tail = pack["rules"][-1]
    assert tail["title"] == "Урезание по вместимости пакета"
    assert '"dropped"' in tail["constraint"]["note"]


def test_norm_table_zero_negative_and_int32_borders():
    csv_text = ("name;value;unit\n"
                "min_rest;0;min\n"
                "max_monthly_block;2147483647;min\n"
                "max_duty_span;2147483648;min\n")
    pack = import_rules_text(csv_text, "norm-table", "b.csv")
    problems = pack["rules"][0]
    assert problems["title"] == "Строки, не принятые приёмником"
    assert "положительным" in problems["statement"]
    assert "не преобразуется" in problems["statement"]
    ok = [r for r in pack["rules"][1:]]
    assert len(ok) == 1
    assert ok[0]["constraint"]["value"] == 2147483647


def test_norm_table_count_unit_conflict_and_missing_unit():
    csv_text = ("name;value;unit\n"
                "max_landings_per_duty;5;часов\n"
                "min_rest;600;\n")
    # обе строки отклонены -> импорт честно отказывает с ОБЕИМИ причинами
    with pytest.raises(RulesImportError) as e:
        import_rules_text(csv_text, "norm-table", "b.csv")
    assert "противоречит счётному шаблону" in str(e.value)
    assert "единица не указана" in str(e.value)
    # при наличии хотя бы одной годной строки причины остаются в записи
    pack = import_rules_text(csv_text + "min_sit;30;мин\n",
                             "norm-table", "b.csv")
    problems = pack["rules"][0]
    assert problems["title"] == "Строки, не принятые приёмником"
    assert "противоречит счётному шаблону" in problems["statement"]


def test_norm_table_json_preserves_zero():
    rows = [{"name": "какая-то норма", "value": 0, "unit": "min"}]
    pack = import_rules_text(json.dumps(rows, ensure_ascii=False),
                             "norm-table", "z.json")
    note = pack["rules"][0]["constraint"]["note"]
    assert '"value": "0"' in note


def test_norm_table_inf_nan_rejected_cleanly():
    for bad in ("inf", "nan", "1e999"):
        csv_text = f"name;value;unit\nmax_landings_per_duty;{bad};count\n"
        with pytest.raises(RulesImportError):
            # единственная строка отклонена -> «ни одна строка не дала»
            import_rules_text(csv_text, "norm-table", "b.csv")


def test_norm_table_oversized_csv_field_clean_error():
    """csv.Error (поле сверх лимита) оборачивается в честный отказ, а не
    в необработанное исключение (= код 500 на сервере). Лимит csv —
    глобальное состояние процесса, поэтому фиксируем его явно."""
    import csv as _csv
    big = "name;value\n" + "a" * 2_000_000 + ";5\n"
    old = _csv.field_size_limit(131072)
    try:
        with pytest.raises(RulesImportError) as e:
            import_rules_text(big, "norm-table", "big.csv")
        assert "CSV не разобран" in str(e.value)
    finally:
        _csv.field_size_limit(old)


def test_meridian_limits_fractional_and_overflow_stay_structural():
    mer = {"format": 1,
           "employees": [{"employeeId": "1", "flightTimeLimit": 6000.7}],
           "duties": []}
    pack = import_rules(mer, "meridian-limits", "m.json", country="QQ")
    ftl = next(r for r in pack["rules"] if "налёта" in r["title"])
    assert ftl["constraint"]["kind"] == "note"
    assert "6000.7" in ftl["statement"]
    mer["employees"][0]["flightTimeLimit"] = 3_000_000_000
    pack = import_rules(mer, "meridian-limits", "m.json", country="QQ")
    ftl = next(r for r in pack["rules"] if "налёта" in r["title"])
    assert ftl["constraint"]["kind"] == "note"


def test_resource_validator_negative_branches():
    from webapp.rule_packs import RulePackError, _validate_rule_fields
    base = {"title": "t", "statement": "s", "source": {"document": "d"}}

    def c(**kw):
        return dict({"kind": "resource", "resource": "r",
                     "window": {"min": 1, "max": 2}, "unit": "minutes",
                     "level": "duty", "extension": ""}, **kw)

    bad = [c(window={"min": 100, "max": 50}),
           c(window={"min": None, "max": None}),
           c(window={"min": -5, "max": None}),
           c(window={"min": 2.5, "max": None}),
           c(window={"min": None, "max": 2_147_483_648}),
           c(unit="слоны"), c(level="галактика"), c(resource="")]
    for constraint in bad:
        with pytest.raises(RulePackError):
            _validate_rule_fields(dict(base, constraint=constraint))


def test_cli_main_paths(tmp_path):
    from _rules_import import main
    # файл не найден -> код 2
    assert main([str(tmp_path / "нет.csv")]) == 2
    # cp1251-таблица с кириллическим заголовком -> код 0 и файл пакета
    src = tmp_path / "нормы.csv"
    src.write_bytes("имя;значение;единица\nmin_rest;10;ч\n"
                    .encode("cp1251"))
    out = tmp_path / "пакет.json"
    assert main([str(src), "--format", "norm-table",
                 "--out", str(out)]) == 0
    pack = json.loads(out.read_text(encoding="utf-8"))
    assert any(r["constraint"].get("parameter") == "min_rest_min"
               and r["constraint"]["value"] == 600
               for r in pack["rules"])
    # мусор -> код 1
    junk = tmp_path / "мусор.bin"
    junk.write_text("просто текст", encoding="utf-8")
    assert main([str(junk)]) == 1
    # meridian-файл без --country -> отказ словами, код 1;
    # с --country QQ -> страна в пакете
    mer = tmp_path / "мер.json"
    mer.write_text(json.dumps(
        {"format": 1,
         "employees": [{"employeeId": "1", "flightTimeLimit": 6000}],
         "duties": []}, ensure_ascii=False), encoding="utf-8")
    assert main([str(mer), "--format", "meridian-limits"]) == 1
    mer_out = tmp_path / "мер-пакет.json"
    assert main([str(mer), "--format", "meridian-limits",
                 "--country", "QQ", "--out", str(mer_out)]) == 0
    assert json.loads(mer_out.read_text(
        encoding="utf-8"))["country"] == "QQ"


# ------------------------------------------------ шаблоны (Ярус 2б)

def test_templates_conversions():
    assert templates.value_to_minutes("100:30", "") == 6030
    assert templates.value_to_minutes(10, "ч") == 600
    assert templates.value_to_minutes("1", "days") == 1440
    assert templates.value_to_minutes("5", "слонов") is None
    assert templates.value_to_minutes("-1", "min") is None
    assert templates.value_to_count("5") == 5
    assert templates.value_to_count("5.5") is None
    assert templates.normalize_window("месяц") == "month"
    assert templates.normalize_window("") == ""
    assert templates.normalize_window("чужое окно") is None
    assert templates.canonical_template("min_rest_min") == "min_rest"
    assert templates.canonical_template("нечто") is None
    # каждая запись resource_constraint проходит валидатор пакета
    for tid in templates.TEMPLATES:
        c = templates.resource_constraint(tid, 600)
        _validate_rule_fields({
            "title": "t", "statement": "s",
            "source": {"document": "d"}, "constraint": c})


def test_solver_format5_via_framework():
    solver = make_solver_only()
    solver["parameters"] = dict(solver["parameters"],
                                maximumPairingDays=6)
    solver["constraintWeightOverrides"] = {"WEEKLY_REST": "HIGH_HIGH"}
    # format=auto: отказ без страны называет РАЗРЕШЁННЫЙ формат
    with pytest.raises(RulesImportError) as e:
        import_rules(solver, "auto", "s.json")
    assert "solver-format5" in str(e.value)
    pack = import_rules(solver, "auto", "s.json", country="QQ")
    assert pack["country"] == "QQ"
    assert any(r["constraint"]["kind"] == "parameter"
               for r in pack["rules"])
    for r in pack["rules"]:
        _validate_rule_fields(r)
