# -*- coding: utf-8 -*-
"""Генератор и исполнитель проверок данных (ДЛ-2).

Охраняемое: закрытый перечень шаблонов; спецификация компилятора
валидируется кодом (fail-closed: невалидная — «не скомпилирована» с
причиной, блокированная строка свода не компилируется); исполнители
детерминированы и честно считают записи; полнота компилятора — каждая
строка свода получает ровно один исход; собранный отчёт проходит
контракт ДЛ-1 и гейт."""
import json
import re

import pytest

from cdpm import data_checks as dc
from cdpm import data_report as dr
from cdpm.gates import gate_data_report
from cdpm.sample import sample_model

_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _dataset():
    return {
        "sources": [
            {"sid": "D1", "file": "ROSTER.xlsx", "sha256": _SHA_A,
             "rows_seen": 3},
            {"sid": "D2", "file": "QUAL.xlsx", "sha256": _SHA_B,
             "rows_seen": 2},
        ],
        "tables": {
            "D1": {"Ростер": {
                "columns": ["Табельный", "Рейс", "Дата", "Начало",
                            "Конец"],
                "rows": [
                    {"Табельный": "EMP-1", "Рейс": "PX-1",
                     "Дата": "05.03.2026",
                     "Начало": "05.03.2026 08:00",
                     "Конец": "05.03.2026 12:00"},
                    {"Табельный": "EMP-2", "Рейс": "PX-2",
                     "Дата": "07.03.2026",
                     "Начало": "07.03.2026 09:00",
                     "Конец": "07.03.2026 20:30"},
                    {"Табельный": "EMP-2", "Рейс": "PX-3",
                     "Дата": "видимо март",
                     "Начало": "07.03.2026 22:00",
                     "Конец": "08.03.2026 06:00"},
                ]}},
            "D2": {"Допуски": {
                "columns": ["Табельный", "Действителен до"],
                "rows": [
                    {"Табельный": "EMP-1",
                     "Действителен до": "31.12.2026"},
                    {"Табельный": "EMP-2",
                     "Действителен до": "06.03.2026"},
                ]}},
        },
    }


def _spec(template, params, basis=None):
    return {"id": "C-x-0", "basis": basis or {"builtin": "аномалия"},
            "template": template, "params": params,
            "statement": "т", "unit": "строка",
            "severity": "КРИТИЧЕСКОЕ", "status": "скомпилирована"}


def test_executors_each_template():
    ds = _dataset()
    n, bad = dc.run_spec(_spec(
        "обязательное-поле",
        {"sheet": "D1/Ростер", "column": "Рейс"}), ds)
    assert (n, bad) == (3, [])

    n, bad = dc.run_spec(_spec(
        "перечень-значений",
        {"sheet": "D1/Ростер", "column": "Табельный",
         "allowed": ["EMP-1"]}), ds)
    assert n == 3 and [f["target"]["row"] for f in bad] == [3, 4]

    n, bad = dc.run_spec(_spec(
        "значение-в-диапазоне",
        {"sheet": "D2/Допуски", "column": "Табельный", "min": 0}), ds)
    assert n == 2 and len(bad) == 2       # не числа — честные находки

    n, bad = dc.run_spec(_spec(
        "уникальность",
        {"sheet": "D1/Ростер", "columns": ["Табельный"]}), ds)
    assert n == 3 and [f["target"]["row"] for f in bad] == [4]

    n, bad = dc.run_spec(_spec(
        "ссылка-существует",
        {"sheet": "D1/Ростер", "column": "Табельный",
         "ref_sheet": "D2/Допуски", "ref_column": "Табельный"}), ds)
    assert (n, bad) == (3, [])

    n, bad = dc.run_spec(_spec(
        "действительность-на-дату",
        {"sheet": "D1/Ростер", "key_column": "Табельный",
         "date_column": "Дата", "ref_sheet": "D2/Допуски",
         "ref_key_column": "Табельный",
         "ref_until_column": "Действителен до"}), ds)
    # EMP-2: допуск истёк 06.03, полёт 07.03; «видимо март» не дата.
    assert n == 3 and [f["target"]["row"] for f in bad] == [3, 4]

    n, bad = dc.run_spec(_spec(
        "максимальная-длительность-часов",
        {"sheet": "D1/Ростер", "start_column": "Начало",
         "end_column": "Конец", "max_hours": 10}), ds)
    assert n == 3 and [f["target"]["row"] for f in bad] == [3]

    n, bad = dc.run_spec(_spec(
        "минимальный-разрыв-часов",
        {"sheet": "D1/Ростер", "group_column": "Табельный",
         "start_column": "Начало", "end_column": "Конец",
         "min_hours": 10}), ds)
    assert n == 3 and [f["target"]["row"] for f in bad] == [4]
    # Доказательство интервала называет ОБА конца — конец предыдущей
    # записи и начало следующей (живая сверка 2026-08-19: по
    # одной строке сверщик судил «доказательство не о том интервале»).
    assert "конец предыдущей (строка" in bad[0]["evidence"]
    assert "начало следующей (строка 4)" in bad[0]["evidence"]


def test_validate_spec_fail_closed():
    ds = _dataset()
    svod_rules = {r["id"]: r for r in sample_model()["rules"]}
    assert "вне перечня" in dc.validate_spec(
        _spec("телепатия", {"sheet": "D1/Ростер"}), ds, svod_rules)
    assert "нет параметра" in dc.validate_spec(
        _spec("обязательное-поле", {"sheet": "D1/Ростер"}), ds,
        svod_rules)
    assert "нет в наборе данных" in dc.validate_spec(
        _spec("обязательное-поле",
              {"sheet": "D9/Нет", "column": "x"}), ds, svod_rules)
    assert "колонки" in dc.validate_spec(
        _spec("обязательное-поле",
              {"sheet": "D1/Ростер", "column": "Нет такой"}), ds,
        svod_rules)
    assert "без min и без max" in dc.validate_spec(
        _spec("значение-в-диапазоне",
              {"sheet": "D1/Ростер", "column": "Рейс"}), ds,
        svod_rules)
    assert "блокирована вопросом" in dc.validate_spec(
        _spec("обязательное-поле",
              {"sheet": "D1/Ростер", "column": "Рейс"},
              basis={"rule": "R-S1-П-1-1"}), ds, svod_rules)
    assert "не основание" in dc.validate_spec(
        _spec("обязательное-поле",
              {"sheet": "D1/Ростер", "column": "Рейс"},
              basis={"rule": "R-S1-П-3-2"}), ds, svod_rules)


def _fake_compiler(good_rule="R-S2-DATA-QUAL", broken=False):
    def llm(prompt):
        ids = re.findall(r"^(R-\S+) \|", prompt, re.M)
        checks, skipped = [], []
        for rid in ids:
            if rid == good_rule:
                params = {"sheet": "D1/Ростер",
                          "key_column": "Табельный",
                          "date_column": "Дата",
                          "ref_sheet": "D2/Допуски",
                          "ref_key_column": "Табельный",
                          "ref_until_column": "Действителен до"}
                if broken:
                    params.pop("date_column")
                checks.append({"rule": rid,
                               "template": "действительность-на-дату",
                               "params": params,
                               "statement": "Допуск действителен на "
                                            "дату полёта",
                               "unit": "строка ростера",
                               "severity": "КРИТИЧЕСКОЕ"})
            else:
                skipped.append({"rule": rid,
                                "reason": "нет данных для проверки"})
        return json.dumps({"checks": checks, "skipped": skipped},
                          ensure_ascii=False)
    return llm


def test_compile_full_coverage_and_fail_closed():
    svod = sample_model()
    specs = dc.compile_checks(svod, _dataset(), _fake_compiler())
    by_rule = {s["basis"]["rule"]: s for s in specs}
    checkable = [r["id"] for r in svod["rules"]
                 if r["kind"] in dr.CHECKABLE_KINDS]
    assert sorted(by_rule) == sorted(checkable)      # полнота
    assert by_rule["R-S2-DATA-QUAL"]["status"] == "скомпилирована"
    assert "блокирована вопросом" in by_rule["R-S1-П-1-1"]["reason"]
    assert "компилятор: нет данных" in by_rule["R-S1-CASE-11"]["reason"]

    # Невалидная спецификация модели отбраковывается с причиной.
    specs = dc.compile_checks(svod, _dataset(),
                              _fake_compiler(broken=True))
    by_rule = {s["basis"]["rule"]: s for s in specs}
    assert by_rule["R-S2-DATA-QUAL"]["status"] == "не скомпилирована"
    assert "отбракована" in by_rule["R-S2-DATA-QUAL"]["reason"]


def test_правило_с_исполнителем_не_компилируется_по_входной_книге():
    """Ложная проверка хуже отсутствующей. Правило о полётной смене
    компилировалось по листу рейсов и объявляло нарушением каждый
    рейс подряд («длительность вне лимита 12 ч» на полуторачасовом
    рейсе) — 27 критических находок на ровном месте, прогон 03.09.
    Правило, которое исполняет решатель, по ВХОДНОЙ книге не судится:
    плодов планирования в ней нет, а соблюдение проверяет сертификат
    решения. Появился в книге лист смен (план-факт) — проверка снова
    законна."""
    # Семья с исполнителем — по КАТАЛОГУ шаблонов через мост (шаг 7:
    # обёртки решателя `vmp.template_registry` CDPM не читает; закон —
    # виды исполнителей записи вне шаблона проверки данных).
    from cdpm import catalog_bridge as bridge
    from cdpm import model as m
    fam = next(f for f in sorted(m.template_families())
               if bridge.family_has_executor(f))
    rule = {"id": "R-ИСП", "kind": "HARD", "zone": "PAIRING",
            "title": "Т", "statement": "предел смены 720 минут",
            "sources": [{"sid": "S1", "anchor": "п.1"}],
            "template_fit": {"category": "A", "family": fam,
                             "registry_rev": "x" * 12,
                             "quote": "предел смены 720 минут"}}
    ds = _dataset()
    assert dc._judged_on_solution(rule, ds) is True
    specs = dc.compile_checks({"rules": [rule]}, ds, _fake_compiler())
    assert [s["status"] for s in specs] == ["не скомпилирована"]
    assert "судится решением" in specs[0]["reason"]
    # Книга с плодами планирования — проверка снова компилируется.
    ds2 = {"tables": {"D1": dict(next(iter(ds["tables"].values())))}}
    ds2["tables"]["D1"]["Смены"] = {"columns": ["Табельный номер"],
                                    "rows": []}
    assert dc._judged_on_solution(rule, ds2) is False


def test_builtin_checks_detect_duplicates_and_dates():
    ds = {"sources": [{"sid": "D3", "file": "x.xlsx",
                       "sha256": _SHA_A, "rows_seen": 5}],
          "tables": {"D3": {"Журнал": {
              "columns": ["Номер", "Дата"],
              "rows": [
                  {"Номер": "1", "Дата": "01.03.2026"},
                  {"Номер": "2", "Дата": "02.03.2026"},
                  {"Номер": "3", "Дата": "03.03.2026"},
                  {"Номер": "4", "Дата": "четвёртое марта"},
                  {"Номер": "2", "Дата": "02.03.2026"},
              ]}}}}
    specs = dc.builtin_checks(ds)
    kinds = {(s["basis"]["builtin"], s["params"].get("column"))
             for s in specs}
    assert ("дубль", None) in kinds
    assert ("формат", "Дата") in kinds
    doc = dc.build_data_report("Тест", ds, specs)
    classes = {(f["class"], f["target"]["row"])
               for f in doc["findings"]}
    assert ("дубль", 6) in classes
    assert ("формат", 5) in classes
    assert dr.validate_report(doc) == []


def test_end_to_end_report_passes_contract_and_gate(tmp_path, capsys):
    from cdpm import model as m
    svod = sample_model()
    ds = _dataset()
    specs = dc.compile_checks(svod, ds, _fake_compiler())
    specs += dc.builtin_checks(ds)
    doc = dc.build_data_report("Полярис (тест)", ds, specs)
    assert dr.validate_report(doc, svod) == []
    rule_findings = [f for f in doc["findings"]
                     if f["class"] == "нарушение правила"]
    assert {f["target"]["row"] for f in rule_findings} == {3, 4}
    assert all(f["rule_refs"] == ["R-S2-DATA-QUAL"]
               for f in rule_findings)

    dr.dump_report(doc, tmp_path / "data-report.json")
    (tmp_path / "data-report.md").write_text(
        dr.render_data_report(doc), encoding="utf-8")
    m.dump_model(svod, tmp_path / "model.json")
    rc = gate_data_report.run(str(tmp_path / "data-report.json"),
                              str(tmp_path / "model.json"),
                              str(tmp_path / "data-report.md"))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "ВЕРДИКТ: отчёт о качестве данных соответствует контракту" \
        in out


def test_parsers_are_strict():
    assert dc.parse_date("07.03.2026") is not None
    assert dc.parse_date("2026-03-07") is not None
    assert dc.parse_date("32.13.2026") is None
    assert dc.parse_date("видимо март") is None
    assert dc.parse_moment("07.03.2026 09:30").hour == 9
    assert dc.parse_moment("07.03.2026 около девяти") is None
    assert dc.parse_num("10,5") == pytest.approx(10.5)
    assert dc.parse_num("десять") is None


def test_value_exists_template_and_sanity_threshold():
    """«Значение-существует»: одна находка при полном отсутствии кода
    (не лавина «все строки не равны X»); санитарный порог сворачивает
    лавину неверно нацеленной проверки в одну мета-находку — уроки
    живого прогона 3.3 №2."""
    rows = [{"Код": "CA"} if i % 2 else {"Код": "PURS"}
            for i in range(60)]
    ds = {"sources": [{"sid": "D9", "file": "t.xlsx",
                       "sha256": "c" * 64, "rows_seen": 60}],
          "tables": {"D9": {"Реестр": {"columns": ["Код"],
                                       "rows": rows}}}}
    n, bad = dc.run_spec(_spec(
        "значение-существует",
        {"sheet": "D9/Реестр", "column": "Код", "value": "CA"}), ds)
    assert (n, bad) == (60, [])
    n, bad = dc.run_spec(_spec(
        "значение-существует",
        {"sheet": "D9/Реестр", "column": "Код", "value": "МОК"}), ds)
    assert n == 60 and len(bad) == 1
    assert "не встречается" in bad[0]["text"]

    lavina = _spec("перечень-значений",
                   {"sheet": "D9/Реестр", "column": "Код",
                    "allowed": ["МОК"]})
    doc = dc.build_data_report("Тест", ds, [lavina])
    assert len(doc["findings"]) == 1        # лавина свёрнута
    meta = doc["findings"][0]
    assert "неверная нацеленность" in meta["text"]
    assert "60 из 60" in meta["text"]
    assert dr.validate_report(doc) == []


def test_uniqueness_distinguishes_exact_and_divergent_duplicates():
    """Мост А (вопрос сессии COSMOS 2026-08-22): находка повтора ключа
    называет, точный это повтор строки или повтор ключа при разном
    содержимом — потребитель знает, можно ли сворачивать молча."""
    dataset = {"sources": [{"sid": "D", "file": "d.xlsx",
                            "sha256": "0" * 64, "rows_seen": 3}],
               "tables": {"D": {"Рейсы": {
                   "columns": ["Номер", "Вылет"],
                   "rows": [{"Номер": "PL1", "Вылет": "06:00"},
                            {"Номер": "PL1", "Вылет": "06:00"},
                            {"Номер": "PL1", "Вылет": "09:30"}]}}}}
    spec = {"id": "C-X-0", "basis": {"rule": "R-X"},
            "template": "уникальность",
            "params": {"sheet": "D/Рейсы", "columns": ["Номер"]},
            "statement": "s", "unit": "строка",
            "severity": "СЕРЬЁЗНОЕ", "status": "скомпилирована"}
    n, findings = dc.run_spec(spec, dataset)
    assert n == 3 and len(findings) == 2
    assert "точный повтор строки" in findings[0]["text"]
    assert "РАЗНОМ содержимом" in findings[1]["text"]


def _area_ds():
    def row(emp, dep, arr, place, base):
        return {"Табельный": emp, "Начало": dep, "Конец": arr,
                "Прилёт": place, "База": base}
    return {
        "sources": [{"sid": "S", "file": "s.json", "sha256": _SHA_A,
                     "rows_seen": 4}],
        "tables": {"S": {"Назначения": {
            "columns": ["Табельный", "Начало", "Конец", "Прилёт",
                        "База"],
            "rows": [
                # Пара 1: разрыв 1 ч В БАЗЕ (место=PLY=базе).
                row("E1", "05.03.2026 06:00", "05.03.2026 08:00",
                    "PLY", "PLY"),
                row("E1", "05.03.2026 09:00", "05.03.2026 11:00",
                    "SVG", "PLY"),
                # Пара 2: разрыв 1 ч ВНЕ базы (место=SVG ≠ базе PLY).
                row("E1", "05.03.2026 12:00", "05.03.2026 14:00",
                    "PLY", "PLY"),
            ]}}},
    }


def test_gap_area_trio_filters_pairs():
    """4б: тройка области сужает счёт разрывов до «база»/«вне базы»;
    синоним области канонизируется; частичная тройка — отказ."""
    base_params = {"sheet": "S/Назначения", "group_column": "Табельный",
                   "start_column": "Начало", "end_column": "Конец",
                   "min_hours": 2}
    rules = {}
    # Без области: коротких разрывов два (оба).
    spec = _spec("минимальный-разрыв-часов", dict(base_params))
    assert dc.validate_spec(spec, _area_ds(), rules) is None
    seen, findings = dc.run_spec(spec, _area_ds())
    assert (seen, len(findings)) == (3, 2)
    # Область «база»: судится только разрыв с местом=базе.
    spec_b = _spec("минимальный-разрыв-часов", dict(
        base_params, place_column="Прилёт", base_column="База",
        area="база"))
    assert dc.validate_spec(spec_b, _area_ds(), rules) is None
    _, f_b = dc.run_spec(spec_b, _area_ds())
    assert len(f_b) == 1
    # Область «вне базы» синонимом: другой разрыв.
    spec_o = _spec("минимальный-разрыв-часов", dict(
        base_params, place_column="Прилёт", base_column="База",
        area="внебазовый"))
    _, f_o = dc.run_spec(spec_o, _area_ds())
    assert len(f_o) == 1
    # Частичная тройка и чужая область — отказы с причиной.
    bad = _spec("минимальный-разрыв-часов", dict(
        base_params, area="база"))
    assert "нужны все три" in dc.validate_spec(bad, _area_ds(), rules)
    bad2 = _spec("минимальный-разрыв-часов", dict(
        base_params, place_column="Прилёт", base_column="База",
        area="ТАС-base"))
    assert "вне закрытого словаря" in dc.validate_spec(
        bad2, _area_ds(), rules)


def test_group_count_template():
    """4б: «счёт-состава-группы» — ровно N записей (с фильтром роли
    и без); доказательство называет группу и оба числа."""
    ds = {
        "sources": [{"sid": "S", "file": "s.json", "sha256": _SHA_A,
                     "rows_seen": 5}],
        "tables": {"S": {"Назначения": {
            "columns": ["Рейс", "Роль"],
            "rows": [
                {"Рейс": "PL1", "Роль": "Пилот"},
                {"Рейс": "PL1", "Роль": "Пилот"},
                {"Рейс": "PL2", "Роль": "Пилот"},
                {"Рейс": "PL2", "Роль": "Проверяющий"},
                {"Рейс": "PL3", "Роль": "Пилот"},
            ]}}},
    }
    rules = {}
    spec = _spec("счёт-состава-группы", {
        "sheet": "S/Назначения", "group_column": "Рейс", "count": 2,
        "role_column": "Роль", "role_value": "Пилот"})
    assert dc.validate_spec(spec, ds, rules) is None
    seen, findings = dc.run_spec(spec, ds)
    assert seen == 5
    # PL1: 2 пилота — чисто; PL2: 1 пилот; PL3: 1 пилот.
    assert len(findings) == 2
    assert any("PL2" in f["evidence"] and "записей" in f["evidence"]
               for f in findings)
    # Без фильтра роли: PL2 набирает 2 записи и чист.
    spec2 = _spec("счёт-состава-группы", {
        "sheet": "S/Назначения", "group_column": "Рейс", "count": 2})
    _, f2 = dc.run_spec(spec2, ds)
    assert len(f2) == 1
    # Валидация fail-closed.
    assert "count не целое" in dc.validate_spec(
        _spec("счёт-состава-группы", {"sheet": "S/Назначения",
                                      "group_column": "Рейс",
                                      "count": 0}), ds, rules)
    assert "только вместе" in dc.validate_spec(
        _spec("счёт-состава-группы", {"sheet": "S/Назначения",
                                      "group_column": "Рейс",
                                      "count": 2,
                                      "role_column": "Роль"}),
        ds, rules)


def test_group_count_max_mode():
    """4г: relation=max — «не больше N в группе» (посадки за смену);
    равенство остаётся умолчанием; чужое relation — отказ."""
    ds = {
        "sources": [{"sid": "S", "file": "s.json", "sha256": _SHA_A,
                     "rows_seen": 5}],
        "tables": {"S": {"Назначения": {
            "columns": ["Смена"],
            "rows": [{"Смена": "D1"}] * 3 + [{"Смена": "D2"}] * 2,
        }}},
    }
    spec = _spec("счёт-состава-группы", {
        "sheet": "S/Назначения", "group_column": "Смена",
        "count": 2, "relation": "max"})
    assert dc.validate_spec(spec, ds, {}) is None
    seen, findings = dc.run_spec(spec, ds)
    assert (seen, len(findings)) == (5, 1)
    assert "не больше 2" in findings[0]["evidence"]
    assert "D1" in findings[0]["evidence"]
    assert "вне перечня" in dc.validate_spec(
        _spec("счёт-состава-группы", {
            "sheet": "S/Назначения", "group_column": "Смена",
            "count": 2, "relation": "между"}), ds, {})


def test_role_filter_must_exist_in_data_vocabulary():
    """4г: role_value, не встречающийся в колонке НИ РАЗУ, — отказ
    компиляции с причиной (живой класс: «Пилот» из текста правила
    против Captain/FirstOfficer в данных, 12 ложных); совпадение без
    учёта регистра законно; словарь значений колонок печатается в
    схему компилятора."""
    ds = {
        "sources": [{"sid": "S", "file": "s.json", "sha256": _SHA_A,
                     "rows_seen": 2}],
        "tables": {"S": {"Назначения": {
            "columns": ["Рейс", "Роль"],
            "rows": [{"Рейс": "PL1", "Роль": "Captain"},
                     {"Рейс": "PL1", "Роль": "FirstOfficer"}],
        }}},
    }
    bad = _spec("счёт-состава-группы", {
        "sheet": "S/Назначения", "group_column": "Рейс", "count": 2,
        "role_column": "Роль", "role_value": "Пилот"})
    assert "словарь значений данных иной" in dc.validate_spec(
        bad, ds, {})
    ok = _spec("счёт-состава-группы", {
        "sheet": "S/Назначения", "group_column": "Рейс", "count": 1,
        "role_column": "Роль", "role_value": "captain"})
    assert dc.validate_spec(ok, ds, {}) is None
    schema = dc._schema_text(ds)
    assert "Роль: значения Captain, FirstOfficer" in schema


def test_builtin_gap_in_dense_column():
    """Живой Лайт run-0040: рейс без времени вылета (единственная
    пустая ячейка колонки на 1014 строк) прошёл линию данных —
    компилятор не предложил обязательное-поле, встроенных проверок
    пустоты не было; дефект нашёл конвертер потребителя. Прореха
    плотной колонки — встроенная проверка без модели; редкая
    колонка (заполнена меньше 90%) прорехой не считается."""
    rows = [{"Рейс": f"PL{i}", "Время вылета": "08:00",
             "Примечание": ""} for i in range(2, 60)]
    rows[30]["Время вылета"] = ""          # прореха
    rows[0]["Примечание"] = "редкое"       # редкая колонка — не флаг
    ds = {
        "sources": [{"sid": "D", "file": "d.xlsx", "sha256": _SHA_A,
                     "rows_seen": len(rows)}],
        "tables": {"D": {"Рейсы": {
            "columns": ["Рейс", "Время вылета", "Примечание"],
            "rows": rows}}},
    }
    specs = dc.builtin_checks(ds)
    # Класс «пропуск данных» с 1.18 несёт и проверку «горизонт-объявлен»
    # (книга без «Свойств»); прореха колонки — шаблон обязательного поля.
    gap = [s for s in specs
           if s["basis"].get("builtin") == "пропуск данных"
           and s["template"] == "обязательное-поле"]
    assert len(gap) == 1
    assert gap[0]["params"]["column"] == "Время вылета"
    seen, findings = dc.run_spec(gap[0], ds)
    assert len(findings) == 1
    assert findings[0]["target"]["row"] == 32


def test_group_count_min_and_distinct():
    """Заявка моста 2026-08-23: relation=min (нормы резерва «не
    меньше N») и distinct_column (различные сотрудники, а не записи
    — дубль человека на рейсе больше не проходит счёт)."""
    ds = {
        "sources": [{"sid": "S", "file": "s.json", "sha256": _SHA_A,
                     "rows_seen": 4}],
        "tables": {"S": {"Назначения": {
            "columns": ["Рейс", "Табельный"],
            "rows": [
                {"Рейс": "PL1", "Табельный": "E1"},
                {"Рейс": "PL1", "Табельный": "E1"},   # дубль лица
                {"Рейс": "PL2", "Табельный": "E1"},
                {"Рейс": "PL2", "Табельный": "E2"},
            ]}}},
    }
    spec = _spec("счёт-состава-группы", {
        "sheet": "S/Назначения", "group_column": "Рейс", "count": 2,
        "distinct_column": "Табельный"})
    assert dc.validate_spec(spec, ds, {}) is None
    seen, findings = dc.run_spec(spec, ds)
    # PL1: записей 2, но РАЗЛИЧНЫХ 1 — нарушение; PL2 чист.
    assert len(findings) == 1
    assert "различных" in findings[0]["evidence"]
    assert "PL1" in findings[0]["evidence"]
    spec_min = _spec("счёт-состава-группы", {
        "sheet": "S/Назначения", "group_column": "Рейс", "count": 2,
        "relation": "min", "distinct_column": "Табельный"})
    _, f_min = dc.run_spec(spec_min, ds)
    assert len(f_min) == 1 and "не меньше" in f_min[0]["evidence"]


def test_нечисловой_параметр_шаблона_не_валит_отчёт():
    """Живой отказ run-0060 (2026-08-27): компилятор отдал
    max_hours='reg_limit' — стадия падала целиком. Кривая
    спецификация обязана стать «не прогнана» со следом причины."""
    from cdpm import data_checks as dc
    dataset = {"tables": {"D1": {"Смены": {
        "columns": ["Начало", "Конец"],
        "rows": [{"Начало": "2026-01-01 08:00",
                  "Конец": "2026-01-01 20:00"}]}}}}
    spec = {"id": "C-BAD", "status": "скомпилирована",
            "basis": {"rule": "R-X"}, "statement": "предел смены",
            "unit": "проверка",
            "template": "максимальная-длительность-часов",
            "params": {"sheet": "D1/Смены", "max_hours": "reg_limit",
                       "start_column": "Начало",
                       "end_column": "Конец"}}
    doc = dc.build_data_report("тест", dataset, [spec])
    chk = doc["checks"][0]
    assert chk["status"] == "не прогнана"
    assert "неисполнима" in chk["reason"] \
        and "reg_limit" in chk["reason"]
