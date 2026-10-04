# -*- coding: utf-8 -*-
"""Измеритель постановки без эталона (cdpm/measure.py).

Правильность считается из самих клауз: величина клаузы обязана быть
потреблена машинным полем, поле обязано иметь носителя в тексте своей
клаузы, строка не рождается на ненормативной клаузе. Фикстуры —
выдуманный мир (не наш набор): инварианты обязаны работать на любых
документах. Отдельно — дымовая проверка на живом эталоне комплекта.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from cdpm import measure as ms  # noqa: E402


def _clause(anchor: str, text: str, sid: str = "S1") -> dict:
    return {"sid": sid, "anchor": anchor, "text": text}


def _rule(rid, anchor, statement, params=None, kind="HARD", **extra):
    r = {"id": rid, "kind": kind, "zone": "PAIRING", "title": rid,
         "statement": statement,
         "sources": [{"sid": "S1", "anchor": anchor}]}
    if params:
        r["template_fit"] = {
            "category": "A", "family": "duty_limits", "registry_rev": "t",
            "params": {n: {"value": v, "quote": q}
                       for n, (v, q) in params.items()}}
    r.update(extra)
    return r


def test_величина_клаузы_потреблена_полем_и_остаток_виден():
    text = ("Продолжительность рабочей смены пилота не должна превышать "
            "11:30; перерыв между сменами — не менее 9 часов.")
    model = {"rules": [_rule(
        "R-1", "п.4.2", text,
        {"max_duty_duration": (690, "не должна превышать 11:30")})]}
    rep = ms.measure(model, [_clause("п.4.2", text)])
    c = rep["counts"]
    assert c["rows_on_nonnormative"] == 0
    assert c["fields_without_carrier"] == 0
    # Взята одна величина из двух: вторая («9 часов») осталась в остатке.
    left = [v["text"] for r in rep["clauses"] for v in r["unconsumed"]]
    assert any("9 час" in x for x in left), left
    assert c["unconsumed_values"] == len(left) >= 1
    ok, problems = ms.verdict(rep)
    assert ok, problems          # остаток — измерение, не структурный стоп


def test_заголовок_не_норма_и_строка_на_нём_дефект():
    heading = "Раздел 3. Режим труда и отдыха"
    model = {"rules": [_rule("R-H", "заголовок-3", heading, kind="DEF")]}
    rep = ms.measure(model, [_clause("заголовок-3", heading)])
    assert ms.normativity(heading)["normative"] is False
    assert ms.normativity(heading)["heading"] is True
    assert rep["counts"]["rows_on_nonnormative"] == 1
    ok, problems = ms.verdict(rep)
    assert not ok and any("ненормативных" in p for p in problems)


def test_цитата_не_из_клаузы_это_дефект():
    text = "Минимальная стыковка между рейсами — не менее 40 минут."
    model = {"rules": [_rule(
        "R-2", "п.7", text,
        {"min_turnaround": (40, "стыковка не менее 40 минут")})]}
    rep = ms.measure(model, [_clause("п.7", text)])
    bad = rep["fields_without_carrier"]
    assert [x["kind"] for x in bad] == ["цитата не из клаузы"]
    assert not ms.verdict(rep)[0]


def test_направление_целевой_требует_слова_носителя():
    good = "Минимизировать суммарную стоимость командировок экипажей."
    bad = "Целевая функция задачи = критерий яруса + сумма штрафов."
    model_ok = {"objectives": [{"id": "O-1", "zone": "PAIRING",
                                "direction": "minimize",
                                "metric": "total_pairing_cost",
                                "statement": good,
                                "sources": [{"sid": "S1", "anchor": "ц.1"}]}]}
    rep_ok = ms.measure(model_ok, [_clause("ц.1", good)])
    assert rep_ok["counts"]["fields_without_carrier"] == 0
    model_bad = json.loads(json.dumps(model_ok))
    model_bad["objectives"][0]["sources"] = [{"sid": "S1", "anchor": "ц.2"}]
    rep_bad = ms.measure(model_bad, [_clause("ц.2", bad)])
    assert [x["field"] for x in rep_bad["fields_without_carrier"]] \
        == ["direction"]
    assert not ms.verdict(rep_bad)[0]


def test_определение_несёт_величины_как_содержание():
    text = ("Базы приписки экипажей: ALA (Алматы), TSE (Астана), "
            "CIT (Шымкент); главная база — ALA.")
    model = {"rules": [_rule("D-1", "п.1.1", text, kind="DEF")]}
    rep = ms.measure(model, [_clause("п.1.1", text)])
    assert ms.normativity(text)["signs"]["перечень кодов"] is True
    assert rep["counts"]["unconsumed_values"] == 0
    assert rep["counts"]["fields_without_carrier"] == 0
    assert ms.verdict(rep)[0]


def test_значение_по_базам_потребляет_коды_и_числа():
    text = ("Ежедневный резерв: ALA — 3 экипажа, TSE — 2 экипажа, "
            "CIT — 1 экипаж.")
    model = {"rules": [_rule(
        "R-3", "п.9", text, {"reserve_crews": (
            "ALA:3,CIT:1,TSE:2", "Ежедневный резерв: ALA — 3 экипажа")},
        zone="ROSTERING")]}
    rep = ms.measure(model, [_clause("п.9", text)])
    left = [v["text"] for r in rep["clauses"] for v in r["unconsumed"]]
    assert left == [], left


def test_реквизиты_и_знаменатель_разбора():
    text = ("П-12.4 (ред. 2026-08-01, версия М-3/демо): смена не более "
            "12:00 (720 минут).")
    kinds = {v.kind for v in ms.iter_values(text)}
    assert "requisite" in kinds
    # Скобочный повтор величины не требует второго поля.
    vals = [v.text for v in ms.iter_values(text)
            if v.kind not in ("requisite", "unclassified")]
    assert any("12:00" in v for v in vals)
    assert not any("720" in v for v in vals)
    # Знаменатель: любой числоподобный знак либо величина, либо
    # реквизит, либо неразобранный — молча пропасть не может.
    assert all(v.kind != "" for v in ms.iter_values(text))


def test_остаток_с_адресатом_и_без_него():
    """Отсутствие величины в постановке законно ровно тогда, когда о ней
    спрошено. Вопрос, назвавший величину, — строгий адресат; вопрос,
    стоящий на клаузе, — щедрый: о какой её величине спрошено, машине
    неизвестно, и разницу прячут только неверные учёты."""
    text = ("Минимальный отдых в базе — не менее 11 часов, вне базы — не "
            "менее 10 часов; при пересечении поясов — не менее 16 часов.")
    model = {
        "rules": [_rule("R-9", "п.5", text,
                        {"min_rest_home": (660, "не менее 11 часов")})],
        "questions": [{
            "id": "Q-1", "part": "3b", "title": "Отдых вне базы",
            "text": "Подтвердите норму «не менее 10 часов» вне базы.",
            "answer_format": {"type": "value"},
            "rule_refs": ["R-9"],
            "sources": [], "source_refs": [{"sid": "S1", "anchor": "п.5"}],
        }],
    }
    rep = ms.measure(model, [_clause("п.5", text)])
    c = rep["counts"]
    assert c["questions"] == 1
    # Три величины: одна взята полем, одна названа вопросом, одна лишь
    # прикрыта вопросом на клаузе.
    assert c["unconsumed_values"] == 2
    assert c["unconsumed_without_addressee"] == 0
    assert c["unconsumed_without_named_addressee"] == 1
    loose = rep["unconsumed_without_named_addressee"]
    assert "16 час" in loose[0]["value"]
    # Без вопросов вовсе весь остаток остаётся без адресата.
    bare = ms.measure({"rules": model["rules"]}, [_clause("п.5", text)])
    assert bare["counts"]["unconsumed_without_addressee"] == 2


def test_живой_эталон_комплекта_структурно_чист():
    """Дымовая: эталон H0 против клауз своего набора. Клаузы строятся
    штатной заморозкой без языковой модели; при отсутствии зависимостей
    заморозки проверка пропускается по названной причине."""
    import pytest
    kit = HERE / "open-bench" / "polyaris-h0"
    etalon = json.loads((kit / "etalon" / "VMP-H0.json")
                        .read_text(encoding="utf-8"))
    try:
        from cdpm import freeze as fz
    except Exception as e:                                # noqa: BLE001
        pytest.skip(f"заморозка недоступна: {e}")
    import tempfile
    manifest = json.loads((kit / "kit-manifest.json").read_text(encoding="utf-8"))
    lines = json.loads((kit / "etalon" / "jpg-lines.json")
                       .read_text(encoding="utf-8"))

    def vision(_ask, _path):
        return json.dumps({"paragraphs": lines}, ensure_ascii=False)

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        fman = fz.freeze([kit / p for p in manifest["rules_files"]],
                         out / "freeze", vision_llm=vision)
        clauses = []
        for f in fman["files"]:
            doc = json.loads((out / "freeze" / "clauses" / f"{f['sid']}.json")
                             .read_text(encoding="utf-8"))
            for c in doc["clauses"]:
                clauses.append({"sid": f["sid"], "anchor": c.get("anchor"),
                                "text": c.get("text") or ""})
    rep = ms.measure(etalon, clauses)
    ok, problems = ms.verdict(rep)
    assert ok, problems
    # Строк ровно столько, сколько записей в эталоне набора: число
    # растёт вместе с набором (парк, должности, допуск по типу —
    # 03.09), поэтому оно СЧИТАЕТСЯ, а не вписывается литералом.
    assert rep["counts"]["rows"] == len(etalon["rules"]) +         len(etalon.get("objectives") or [])
    assert rep["counts"]["rows_orphan"] == 0
    assert rep["counts"]["unclassified_tokens"] == 0


def test_coverage_report_content_layer():
    """Покрытие по содержанию (RAZDELENIE раздел 3 пункт 1): клауза,
    покрытая по якорю, но с невзятой величиной без адресата, считается
    покрытой частично; нормативный носитель без строк перечисляется."""
    from cdpm import coverage as cov
    entries = [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64, "clauses": [
        {"anchor": "абз.1", "text": "Минимальный отдых дома — 12:00 (720 минут); "
                                    "вне базы — 10:00 (600 минут)."},
        {"anchor": "абз.2", "text": "Экипаж смены — ровно 2 пилота."}]}]
    doc = {"meta": {"customer": "т"}, "sources": [], "questions": [],
           "objectives": [],
           "rules": [{"id": "R-1", "kind": "HARD", "zone": "ROSTERING",
                      "statement": "отдых дома 12:00",
                      "sources": [{"sid": "S1", "anchor": "абз.1"}],
                      "template_fit": {"category": "A", "family": "rest_floors",
                                       "params": {"min_rest_home": {
                                           "value": 720,
                                           "quote": "12:00 (720 минут)"}}}}]}
    clauses = [{"sid": e["sid"], "anchor": c["anchor"], "text": c["text"]}
               for e in entries for c in e["clauses"]]
    rep = ms.measure(doc, clauses)
    plain = cov.report(entries, doc, {})
    assert "covered_partially" not in plain          # прежний слой не изменился
    rich = cov.report(entries, doc, {}, measure_report=rep)
    assert rich["clauses"] == 2 and rich["covered_by_rows"] == 1
    # Вторая величина клаузы (отдых вне базы) не взята и не спрошена.
    assert rich["covered_partially"] == 1
    assert any("600" in v or "10:00" in v
               for v in rich["partial"][0]["values_left"])
    # Нормативная клауза без строк перечислена отдельно.
    assert any("абз.2" in c for c in rich["normative_without_rows"])
    assert rich["values_without_addressee"] >= 1


def test_параметр_из_ответа_анкеты_не_сирота():
    """Полнота параметров семьи (09.09.2026): запись параметра с
    признаком answer внесена ответом авиакомпании — носитель величины
    ответ, не клауза; измеритель сиротой её не считает (живой отказ
    гейта отправки Н0-B run-0100: params.min_rest_outstation)."""
    text = "Отдых между сменами дома — не менее 720 минут."
    r = _rule("R-REST", "п.2", text,
              {"min_rest_home": (720, "не менее 720 минут")})
    r["template_fit"]["family"] = "rest_floors"
    r["template_fit"]["params"]["min_rest_outstation"] = {
        "value": 600, "quote": "Отдых вне базы — 10:00 (600 минут)",
        "answer": "Q-3b-параметр-rest_floors-min_rest_outstation"}
    rep = ms.measure({"rules": [r]}, [_clause("п.2", text)])
    assert rep["fields_without_carrier"] == []
    assert ms.verdict(rep)[0]
    # Без признака та же запись — сирота («цитата не из клаузы»).
    r["template_fit"]["params"]["min_rest_outstation"].pop("answer")
    rep2 = ms.measure({"rules": [r]}, [_clause("п.2", text)])
    assert [x["field"] for x in rep2["fields_without_carrier"]] \
        == ["params.min_rest_outstation"]


def test_цитата_из_фрагментов_через_многоточие_в_порядке_текста():
    """Цитата-носитель из фрагментов через многоточие судится по
    фрагментам в порядке текста — та же дисциплина, что у цитат
    посадки (живой случай 09.09.2026: as_written критерия из JPG
    «…(версия M-036/demo-1)... действует с 2026-08-01»)."""
    text = ("Минимум суммарной стоимости; машинный критерий MIN_PERSON_DAYS, "
            "ставка 100 условных единиц за календарный день (версия "
            "M-036/demo-1). Направление: minimize. Действует с 2026-08-01.")
    good = ("машинный критерий MIN_PERSON_DAYS, ставка 100 условных единиц "
            "за календарный день (версия M-036/demo-1)... действует с "
            "2026-08-01")
    model = {"rules": [_rule(
        "R-C", "ц.9", text, {"max_duty_duration": (100, good)})]}
    rep = ms.measure(model, [_clause("ц.9", text)])
    assert rep["fields_without_carrier"] == []
    # Фрагменты не в порядке текста — сирота.
    bad = "действует с 2026-08-01... машинный критерий MIN_PERSON_DAYS"
    model2 = {"rules": [_rule(
        "R-C", "ц.9", text, {"max_duty_duration": (100, bad)})]}
    rep2 = ms.measure(model2, [_clause("ц.9", text)])
    assert [x["kind"] for x in rep2["fields_without_carrier"]] \
        == ["цитата не из клаузы"]


def test_цитата_из_фрагментов_через_точку_с_запятой():
    """Запасной шов фрагментов — «;» (живой случай на Test 09.09.2026:
    модель склеила два фрагмента клаузы JPG через «; », а не «...»);
    фрагменты судятся в порядке текста, обратный порядок — сирота."""
    text = ("O-PAIR. Минимум суммарной стоимости; машинный критерий "
            "MIN_PERSON_DAYS, ставка 100 условных единиц за календарный день "
            "командировки (версия M-036/demo-1). Направление: minimize; "
            "метрика: total_pairing_cost. Действует с 2026-08-01.")
    good = ("машинный критерий MIN_PERSON_DAYS, ставка 100 условных единиц "
            "за календарный день командировки (версия M-036/demo-1); "
            "действует с 2026-08-01")
    model = {"rules": [_rule("R-C2", "ц.10", text,
                             {"max_duty_duration": (100, good)})]}
    assert ms.measure(model, [_clause("ц.10", text)])["fields_without_carrier"] == []
    bad = "действует с 2026-08-01; машинный критерий MIN_PERSON_DAYS"
    model2 = {"rules": [_rule("R-C2", "ц.10", text,
                              {"max_duty_duration": (100, bad)})]}
    assert [x["kind"] for x in ms.measure(model2, [_clause("ц.10", text)])["fields_without_carrier"]] \
        == ["цитата не из клаузы"]
