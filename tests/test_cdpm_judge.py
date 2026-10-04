# -*- coding: utf-8 -*-
"""Судья меры успеха CDPM (П-4) на подменной модели.

Проверяемый контракт — протокол постановки §8: единица счёта — строка
эталона; три вердикта; спорная пара НЕ решается судьёй, а предъявляется
владельцу; метрика переоткрытых вопросов; «изобретённое» — счёт к нулю;
порогов «годен/не годен» судья не выносит. Строка эталона без
кандидатов получает «не найдено» детерминированно, без обращения к
модели."""
import json
import re

import pytest

from cdpm import extract, freeze, judge
from cdpm.gates import gate_judge
from test_cdpm_extract import _RULES_A, _RULES_B, fake_llm

_ETALON = """# Эталонный свод (изготовленный образец)

## ЧАСТЬ 1

| ID | Описание правила | Этап | Тип | Источник | Примечание |
|---|---|---|---|---|---|
| ET-1 | **Минимальный отдых на базе:** HARD: rest_home ≥ 10 ч | PAIRING | HARD | И1 П.1.9 | — |
| ET-2 | **Лимит длительности пейринга:** HARD: duration(pairing) ≤ 4 дней | PAIRING | HARD | И1 П.1.1 | — |
| ET-3 | **Ночёвки вне базы:** SOFT: минимизировать ночёвки вне базы | PAIRING | SOFT | И1 П.3.2 | — |
| ET-4 | **Кормление голубей на перроне:** запрещено распоряжением | PAIRING | OUT | И2 П.9.1 | — |

## ЧАСТЬ 2

#### ⚠️ ВОПРОС 2.1.6: Единица «дня» лимита длительности пейринга

Варианты: (а) календарные сутки; (б) 96 часов.

#### ⚠️ ВОПРОС 2.9.9: Политика кормления голубей

Варианты: (а) действует; (б) отменена.
"""


_RULE_VERDICTS = {
    "ET-1": {"verdict": "найдено", "candidate": "R-S1-п-1-9",
             "basis": "та же норма и та же граница"},
    "ET-2": {"verdict": "найдено частично", "candidate": "R-S1-п-1-1",
             "basis": "правило узнаваемо, единица «дня» не совпала"},
    "ET-3": {"verdict": "спорно", "candidate": None,
             "basis": "уверенного соответствия нет"},
}


def judge_llm(prompt: str) -> str:
    """Подменный судья пакетных запросов: отвечает по КАЖДОМУ
    идентификатору, найденному в пакете (контракт «ровно одна запись на
    строку»)."""
    ids = re.findall(r"Строка эталона \[([^\]]+)\]", prompt)
    if ids:
        return json.dumps({"verdicts": [
            {"etalon": i, **_RULE_VERDICTS[i]} for i in ids]},
            ensure_ascii=False)
    qids = re.findall(r"Вопрос эталона \[([^\]]+)\]", prompt)
    if qids:
        out = []
        for q in qids:
            if q == "2.1.6":
                out.append({"etalon": q, "verdict": "переоткрыт",
                            "candidate": "Q-3a-S1-п-1-1",
                            "basis": "тот же предмет — единица «дня»"})
            else:
                out.append({"etalon": q, "verdict": "не переоткрыт",
                            "candidate": None, "basis": "похожих нет"})
        return json.dumps({"verdicts": out}, ensure_ascii=False)
    rids = re.findall(r"Правило \[([^\]]+)\]", prompt)
    if rids:
        return json.dumps({"verdicts": [
            {"etalon": r, "verdict": "подтверждено",
             "basis": "клауза содержит норму"} for r in rids]},
            ensure_ascii=False)
    raise AssertionError(f"неожиданный запрос судьи: {prompt[:120]}")


@pytest.fixture()
def workspace(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES-A.md").write_text(_RULES_A, encoding="utf-8")
    (src / "RULES-B.md").write_text(_RULES_B, encoding="utf-8")
    kit = tmp_path / "kit"
    freeze.freeze([src / "RULES-A.md", src / "RULES-B.md"], kit)
    out = tmp_path / "extract"
    extract.run_extract(kit, out, fake_llm, "Полярис (тест)")
    etalon = tmp_path / "ETALON.md"
    etalon.write_text(_ETALON, encoding="utf-8")
    return etalon, out, kit, tmp_path / "judge"


def test_e1_record_keys_match_journal_rows_across_addressing():
    """Итерация 2 меры успеха: эталон адресует запись журнала номером
    записи («Е1 (П.22/Л1)»), заморозка xlsx — номером строки листа.
    Судья достаёт номер записи из первой ячейки текста клаузы и лист из
    имени вкладки — якорная ступень для Е1 начинает работать."""
    assert judge.etalon_e1_keys("Е1 (П.22/Л1) правило") == \
        {"е1:22", "е1:22/1"}
    assert judge.etalon_e1_keys("Е1 (П.29-H/Л2)") == \
        {"е1:29", "е1:29/2"}
    assert judge.etalon_e1_keys("Е1 (П.13-D)") == {"е1:13"}

    rule = {"sources": [{"sid": "S2",
                         "anchor": "лист «Журнал испытаний 1 этапа», "
                                   "строка 37"}]}
    clauses = {("S2", "лист «Журнал испытаний 1 этапа», строка 37"):
               "**П.22** | Уточнение по построению колец | ЗАКРЫТО"}
    assert judge.rule_e1_keys(rule, clauses) == {"е1:22", "е1:22/1"}

    et = {"anchors": [], "e1": {"е1:22/1"},
          "text": "совсем другие слова без пересечения лексики"}
    index = judge.build_rule_index(
        [{"id": "R-X", "title": "Кольца", "statement": "парность",
          "sources": rule["sources"]}], clauses)
    assert [r["id"] for r in judge.shortlist(et, index)] == ["R-X"]


def test_rare_number_or_code_is_a_candidate_signal():
    """Итерация 3: «не менее 10 часов» и «rest_home ≥ 10 ч» почти не
    пересекаются словами, но числом — точно; номер рейса — тем более.
    Редкость считается по своду: токен, живущий в каждом правиле,
    сигналом не является."""
    clauses = {("S1", "п.5.5"): "минимальный отдых на базе"}
    index = judge.build_rule_index(
        [{"id": "R-N", "title": "Отдых", "statement": "rest_home ≥ 10 ч",
          "sources": [{"sid": "S1", "anchor": "п.5.5"}]},
         {"id": "R-C", "title": "Разворот", "statement": "рейс ZZ252 "
                                                        "разворотом",
          "sources": [{"sid": "S1", "anchor": "п.5.5"}]}], clauses)
    et_num = {"anchors": [], "e1": set(),
              "text": "продолжительность не менее 10 часов"}
    assert [r["id"] for r in judge.shortlist(et_num, index)] == ["R-N"]
    et_code = {"anchors": [], "e1": set(),
               "text": "смена с рейсом ZZ252 собирается разворотом"}
    assert [r["id"] for r in judge.shortlist(et_code, index)] == ["R-C"]


def test_ubiquitous_tokens_are_not_a_signal():
    """Число, живущее в каждом правиле свода, не роднит всё со всем."""
    clauses = {}
    rules = [{"id": f"R-{i}", "title": "х",
              "statement": f"вариант 1 категории {i}",
              "sources": [{"sid": "S1", "anchor": f"п.{i}"}]}
             for i in range(1, 8)]
    index = judge.build_rule_index(rules, clauses)
    et = {"anchors": [], "e1": set(), "text": "совсем о другом, но с 1"}
    assert judge.shortlist(et, index) == []


def test_clause_text_feeds_the_lexical_shortlist():
    """Эталон цитирует источник дословно — слова клаузы-источника
    участвуют в лексическом коротком списке, даже когда формальная
    запись правила их потеряла."""
    clauses = {("S1", "п.7.7"): "экипаж уведомляется о госпитале "
                                "дежурным по смене немедленно"}
    index = judge.build_rule_index(
        [{"id": "R-Y", "title": "Уведомление", "statement": "notify()",
          "sources": [{"sid": "S1", "anchor": "п.7.7"}]}], clauses)
    et = {"anchors": ["9.9"], "e1": set(),
          "text": "уведомление экипажа о госпитале дежурным по смене"}
    assert [r["id"] for r in judge.shortlist(et, index)] == ["R-Y"]


def test_parse_etalon_cleans_glued_ids_and_disambiguates():
    """Ячейка ID эталона бывает склеена с хвостом разметки — модель
    отвечает чистым идентификатором (живой прогон); дубль после
    очистки не теряется, а получает суффикс."""
    text = ("## ЧАСТЬ 1\n\n"
            "| ID | Описание правила | Этап | Тип | Источник | Прим |\n"
            "|---|---|---|---|---|---|\n"
            "| OP-X-1`<br>source=[`Каталог`]` | **А:** х | PAIRING | "
            "HARD | И1 П.1.1 | — |\n"
            "| OP-X-1 | **Б:** у | PAIRING | HARD | И1 П.1.2 | — |\n")
    got = judge.parse_etalon(text)
    assert [r["id"] for r in got["rules"]] == ["OP-X-1", "OP-X-1 (2)"]


def test_parse_etalon_rules_questions_and_subs():
    got = judge.parse_etalon(_ETALON)
    assert [r["id"] for r in got["rules"]] == ["ET-1", "ET-2", "ET-3",
                                              "ET-4"]
    assert got["rules"][0]["anchors"] == ["1.9"]
    assert [q["qid"] for q in got["questions"]] == ["2.1.6", "2.9.9"]
    assert got["questions"][0]["subs"] == ["а", "б"]


def test_end_to_end_metrics(workspace):
    etalon, out, kit, jdir = workspace
    rep = judge.run_judge(etalon, out, kit, jdir, judge_llm)
    assert rep["by_verdict"] == {"найдено": 1, "найдено частично": 1,
                                 "не найдено": 1}
    # Спорная пара не решена судьёй, а предъявлена владельцу.
    assert [d["etalon"] for d in rep["disputed"]] == ["ET-3"]
    row3 = next(r for r in rep["rows"] if r["etalon"] == "ET-3")
    assert row3["verdict"] == "спорно" and row3["candidate"] is None
    # Строка без кандидатов — «не найдено» без обращения к модели.
    row4 = next(r for r in rep["rows"] if r["etalon"] == "ET-4")
    assert row4["verdict"] == "не найдено"
    assert "кандидатов не нашлось" in row4["basis"]
    # Метрика 2: переоткрыт один вопрос из двух.
    assert rep["reopened"] == 1
    assert rep["etalon"]["sub_items"] == 4
    # Метрика 3: изобретённого нет; порога «годен/не годен» нет.
    assert rep["invented"] == []
    assert "порог" in rep["note"]
    assert (jdir / "judge-report.json").is_file()
    assert (jdir / "judge-report.md").is_file()


def test_invented_rule_is_flagged(workspace):
    etalon, out, kit, jdir = workspace

    def llm(prompt):
        reply = judge_llm(prompt)
        if "Правило [R-S2-п-7-1]" in prompt:
            d = json.loads(reply)
            for e in d["verdicts"]:
                if e["etalon"] == "R-S2-п-7-1":
                    e.update(verdict="изобретено",
                             basis="клауза о другом")
            return json.dumps(d, ensure_ascii=False)
        return reply
    rep = judge.run_judge(etalon, out, kit, jdir, llm)
    assert [i["rule"] for i in rep["invented"]] == ["R-S2-п-7-1"]


def test_malformed_json_batch_is_retried(workspace, tmp_path):
    """Битый JSON пакета (неэкранированная кавычка и подобное) — повод
    для повторной попытки, не для смерти прогона (живой прогон)."""
    calls = {"n": 0}

    def flaky(prompt):
        if "Строка эталона [" in prompt:
            calls["n"] += 1
            if calls["n"] == 1:
                return '{"verdicts": [битый json'
        return judge_llm(prompt)
    etalon, out, kit, _ = workspace
    rep = judge.run_judge(etalon, out, kit, tmp_path / "j-json", flaky)
    assert rep["by_verdict"]["найдено"] == 1


def test_incomplete_batch_is_retried_then_refused(workspace, tmp_path):
    """Неполный пакет вердиктов получает одну повторную попытку с
    перечнем недостач; постоянная недостача — отказ, не тихая дыра."""
    calls = {"n": 0}

    def flaky(prompt):
        reply = judge_llm(prompt)
        if "Строка эталона [" in prompt:
            calls["n"] += 1
            if calls["n"] == 1:
                d = json.loads(reply)
                d["verdicts"] = d["verdicts"][:-1]     # потерян один id
                return json.dumps(d, ensure_ascii=False)
        return reply
    etalon, out, kit, _ = workspace
    rep = judge.run_judge(etalon, out, kit, tmp_path / "j-retry", flaky)
    assert rep["by_verdict"]["найдено"] == 1       # повтор закрыл дыру

    def broken(prompt):
        reply = judge_llm(prompt)
        if "Строка эталона [" in prompt:
            d = json.loads(reply)
            d["verdicts"] = d["verdicts"][:-1]
            return json.dumps(d, ensure_ascii=False)
        return reply
    with pytest.raises(judge.JudgeError) as e:
        judge.run_judge(etalon, out, kit, tmp_path / "j-broken", broken)
    assert "не сошёлся после повтора" in str(e.value)


def test_judge_is_deterministic(workspace, tmp_path):
    etalon, out, kit, _ = workspace
    judge.run_judge(etalon, out, kit, tmp_path / "j1", judge_llm)
    judge.run_judge(etalon, out, kit, tmp_path / "j2", judge_llm)
    assert (tmp_path / "j1" / "judge-report.json").read_bytes() == \
        (tmp_path / "j2" / "judge-report.json").read_bytes()


def test_unlisted_candidate_becomes_disputed_not_a_crash(workspace):
    """«Найдено», но кандидат вне короткого списка (или не назван), —
    это неуверенность модели: после повтора строка приводится как
    «спорно» владельцу, а не убивает весь прогон (живой прогон:
    одна такая строка стоила 40 минут работы судьи)."""
    etalon, out, kit, jdir = workspace

    def llm(prompt):
        reply = judge_llm(prompt)
        if "Строка эталона [ET-1]" in prompt:
            d = json.loads(reply)
            for e in d["verdicts"]:
                if e["etalon"] == "ET-1":
                    e["candidate"] = "R-ЧУЖОЙ"
            return json.dumps(d, ensure_ascii=False)
        return reply
    rep = judge.run_judge(etalon, out, kit, jdir, llm)
    row = next(r for r in rep["rows"] if r["etalon"] == "ET-1")
    assert row["verdict"] == "спорно"
    assert "кандидата" in row["basis"]
    assert "ET-1" in [d["etalon"] for d in rep["disputed"]]


def test_gate_green_on_clean_report(workspace, capsys):
    etalon, out, kit, jdir = workspace
    judge.run_judge(etalon, out, kit, jdir, judge_llm)
    rc = gate_judge.run(str(etalon), str(out), str(jdir))
    outtxt = capsys.readouterr().out
    assert rc == 0, outtxt
    assert "строк эталона 4, вопросов 2, спорных 1" in outtxt
    assert "ВЕРДИКТ: отчёт судьи соответствует контракту" in outtxt


def test_gate_red_on_incomplete_count(workspace, capsys):
    etalon, out, kit, jdir = workspace
    judge.run_judge(etalon, out, kit, jdir, judge_llm)
    rep_path = jdir / "judge-report.json"
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    rep["rows"].pop()
    rep_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2)
                        + "\n", encoding="utf-8")
    rc = gate_judge.run(str(etalon), str(out), str(jdir))
    assert rc == 1
    assert "счёт неполон" in capsys.readouterr().out


def test_gate_red_on_foreign_candidate_and_tampered_md(workspace,
                                                      capsys):
    etalon, out, kit, jdir = workspace
    judge.run_judge(etalon, out, kit, jdir, judge_llm)
    rep_path = jdir / "judge-report.json"
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    rep["rows"][0]["candidate"] = "R-НЕТ"
    rep_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2)
                        + "\n", encoding="utf-8")
    rc = gate_judge.run(str(etalon), str(out), str(jdir))
    outtxt = capsys.readouterr().out
    assert rc == 1
    assert "не существует в модели CDPM" in outtxt

    judge.run_judge(etalon, out, kit, jdir, judge_llm)
    md = jdir / "judge-report.md"
    md.write_text(md.read_text(encoding="utf-8").replace(
        "переоткрыто: 1", "переоткрыто: 2"), encoding="utf-8")
    rc = gate_judge.run(str(etalon), str(out), str(jdir))
    assert rc == 1
    assert "мимо отчёта" in capsys.readouterr().out


def test_cli_main_prints_counters_and_paths(workspace, capsys):
    etalon, out, kit, jdir = workspace
    rc = judge.main([str(etalon), str(out), "--kit", str(kit),
                     "--out-dir", str(jdir)], llm_override=judge_llm)
    outtxt = capsys.readouterr().out
    assert rc == 0
    assert "строк эталона 4: найдено 1, частично 1, не найдено 1, " \
           "спорных 1" in outtxt
    assert str((jdir / "judge-report.json").resolve()) in outtxt
