# -*- coding: utf-8 -*-
"""A03-мин / K05-мин — точное происхождение значения ячейки посадки
(контракт П0 §2.3 п. 5, §12.3; аудит 4П, находки A03 и K05).

Охраняемое (инвариант общий, без единого имени набора и без опоры на
конкретную семью каталога):

1. Значение, внесённое в посадку ОТВЕТОМ, записывает ТОЧНУЮ версию
   ответа: издание отвеченного вопроса (идентификатор + ревизия +
   отпечаток) и отпечаток той версии ответа, из которой значение
   прочитано; корень родословной хранится отдельно. Ответ на вопрос-
   пересмотр пишет СВОЁ издание, а не корневое (K05).
2. Обратный ход один — `answers.resolve_source` по ТОЧНОЙ
   идентичности, не «первый применённый по порядку списка»; любое
   несовпадение отпечатка — отказ с кодом `answer-provenance` и
   словами, называющими вопрос и оба отпечатка.
3. Цитата — дословная подстрока `raw` ИМЕННО той версии, а значение
   читается из этой цитаты грамматикой K02: цитата подтверждает
   подлинность текста, но не правильность значения (контрпример
   аудита B1 — «600 → 660 при ответе без изменений»).
4. Полная проверка цитат требует ПОЛНЫЙ документ: срез строк без
   анкеты — ошибка контракта, а не молчаливое доверие признаку.
5. Один и тот же расчёт судит приём заплаты обработчика, рубеж
   выпуска пакета и гейт пакета.
6. Ячейки старой формы (одна строка `answer`) остаются читаемыми —
   миграция идёт волной 17б, минимальная часть их не ломает, но и НЕ
   ОСВОБОЖДАЕТ от закона: их «точная версия» — ДЕЙСТВУЮЩИЙ ответ
   цепочки пересмотров (последний по `revised_by`), а завести такую
   ячейку или изменить её заплатой нельзя вовсе.
7. Значение обосновывает только ответ ОБ ЭТОЙ ячейке: вопрос
   параметра семьи адресует её своим ключом (семья + параметр), прочий
   вопрос — своими строками. Адрес эффекта, корень родословной и
   прежняя строка `answer` сверяются с ячейкой и её вопросом.
8. Замок 4П «не переписать при проверке» судит чистоту ЦИТАТНОГО слоя
   — цитат из клауз; ячейки, внесённые ответом, в этот счёт не входят,
   иначе замок выключался бы состоянием анкеты.
"""
import copy
import hashlib
import io
import json
import zipfile

import pytest

from cdpm import answers as ans
from cdpm import exchange as ex
from cdpm import export_task_package as tp
from cdpm import family_params as fp
from cdpm import measure as ms
from cdpm import model as m
from cdpm import provenance as prov
from cdpm.gates import gate_task_package as gate

REV = m.registry_fingerprint()
FAMILY = "rest_floors"                  # семья с двумя параметрами-длительностями
PARAM = "min_rest_outstation"
QID = f"Q-3b-параметр-{FAMILY}-{PARAM}"
RAW_600 = "Минимальный отдых вне базы — 10 часов."
RAW_540 = "Теперь вне базы — 9 часов."
CLAUSES = {("S1", "п.2"): "Минимальный отдых дома — 12:00."}


# ── образец свода ────────────────────────────────────────────────────

def _doc():
    """Свод уровня «в» с правилом-носителем семьи, у которого один
    параметр задан клаузой, а второй спрашивается анкетой."""
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1",
                 "revision": 2},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [
            {"id": "R-H", "title": "Жёсткое", "statement": "x ≤ 1",
             "zone": "BOTH", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
            {"id": "R-S", "title": "Ночёвки",
             "statement": "штраф за ночь вне базы: вес 10",
             "zone": "PAIRING", "kind": "SOFT",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
            {"id": "R-R", "title": "Полы отдыха",
             "statement": "Минимальный отдых дома — 12:00.",
             "zone": "ROSTERING", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.2"}],
             "template_fit": {"category": "A", "registry_rev": REV,
                              "family": FAMILY,
                              "params": {"min_rest_home": {
                                  "value": 720, "quote": "дома — 12:00"}}}},
        ],
        "questions": [
            {"id": "Q-3a-GOAL", "part": "3a", "title": "Целевая",
             "text": "т", "answer_format": {"type": "registry"},
             "rule_refs": ["R-S"],
             "source_refs": [{"sid": "S1", "anchor": "п.1"}],
             "answer": {"raw": "таблица", "status": "применён",
                        "revision": 2, "goal_decline": False}},
            {"id": "Q-3b-GOAL", "part": "3b", "title": "Целевая",
             "text": "т2", "answer_format": {"type": "registry"},
             "rule_refs": ["R-H"],
             "source_refs": [{"sid": "S1", "anchor": "п.1"}],
             "answer": {"raw": "любое допустимое решение",
                        "status": "применён", "revision": 2,
                        "goal_decline": True}},
        ],
        "objectives": [
            {"id": "O-1", "title": "Критерий оптимизации пейринга",
             "statement": "Минимум человеко-дней командировок; цена "
                          "рабочего дня (смены) — 100 условных единиц",
             "zone": "PAIRING", "direction": "minimize",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
        ],
    }


def _answered(raw=RAW_600):
    """Свод с ПРИМЕНЁННЫМ ответом на вопрос параметра: ячейка внесена
    штатным путём (вопрос строит код, ответ применяет код)."""
    doc = _doc()
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    assert fp.ensure_family_param_questions(doc) == [QID]
    summary = ans.apply_answers(doc, {QID: raw})
    assert summary["applied"] == 1 and summary["unparsed"] == 0
    return doc, _cell(doc)


def _rule(doc, rid="R-R"):
    return next(r for r in doc["rules"] if r["id"] == rid)


def _cell(doc, rid="R-R", name=PARAM):
    return _rule(doc, rid)["template_fit"]["params"][name]


def _q(doc, qid):
    return next(x for x in doc["questions"] if x["id"] == qid)


# ── 1. запись точной версии ──────────────────────────────────────────

def test_write_records_exact_question_edition_and_answer_version():
    doc, cell = _answered()
    q = _q(doc, QID)
    assert cell["value"] == 600
    assert cell["source"] == {
        "kind": "answer",
        "question": {"id": QID, "revision": 1,
                     "fingerprint": prov.question_edition(q)["fingerprint"]},
        "version": {"fingerprint": prov.answer_version_fingerprint(RAW_600)},
        "effect": {"rule": PARAM, "field": "value"},
        "root": QID}
    # Прежняя строка остаётся: читатель старой формы не ломается.
    assert cell["answer"] == QID
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    assert m.validate_fit_quotes(doc, CLAUSES) == []
    assert prov.package_problems(doc) == []


def test_answer_version_fingerprint_is_deterministic_and_text_bound():
    """Отпечаток версии — от текста ответа: пробелы склеиваются,
    другой текст (и другой регистр) — другая версия."""
    assert prov.answer_version_fingerprint("10  часов\n") == \
        prov.answer_version_fingerprint("10 часов")
    assert prov.answer_version_fingerprint("10 часов") != \
        prov.answer_version_fingerprint("10 ЧАСОВ")
    assert prov.answer_version_fingerprint(RAW_600) != \
        prov.answer_version_fingerprint(RAW_540)
    assert len(prov.answer_version_fingerprint(RAW_600)) == 12


def test_revision_cell_carries_its_own_edition_not_the_root():
    """K05: ответ на вопрос-пересмотр пишет СВОЁ издание и СВОЮ версию;
    корень — отдельным полем (родословная, не источник)."""
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)
    rid_new = rep["created"]
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    assert ans.apply_answers(doc, {rid_new: RAW_540})["applied"] == 1
    cell = _cell(doc)
    assert cell["value"] == 540
    assert cell["source"]["question"]["id"] == rid_new
    assert cell["source"]["root"] == QID
    assert cell["source"]["version"]["fingerprint"] == \
        prov.answer_version_fingerprint(RAW_540)
    assert cell["source"]["question"]["fingerprint"] == \
        prov.question_edition(_q(doc, rid_new))["fingerprint"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    assert m.validate_fit_quotes(doc, CLAUSES) == []


def test_self_healing_takes_the_answer_in_force_not_the_first_in_list():
    """Самолечение возвращает значение ДЕЙСТВУЮЩЕГО ответа (последнего
    в цепочке пересмотров), а не «первого применённого по порядку
    списка», и пишет его точное происхождение."""
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)
    ans.apply_answers(doc, {rep["created"]: RAW_540})
    _rule(doc)["template_fit"]["params"].pop(PARAM)   # посадку переиздали
    assert fp.ensure_family_param_questions(doc) == []
    cell = _cell(doc)
    assert cell["value"] == 540
    assert cell["source"]["question"]["id"] == rep["created"]
    assert prov.package_problems(doc) == []


# ── 2. обратный ход по точной идентичности ───────────────────────────

def test_resolver_returns_the_exact_version_it_was_read_from():
    doc, cell = _answered()
    q, raw = ans.resolve_source(doc, cell["source"])
    assert q["id"] == QID and raw == RAW_600
    # После пересмотра ячейка ведёт к ТЕКСТУ ПЕРЕСМОТРА, а не корня.
    rep = ans.open_revision(doc, QID)
    ans.apply_answers(doc, {rep["created"]: RAW_540})
    q2, raw2 = ans.resolve_source(doc, _cell(doc)["source"])
    assert q2["id"] == rep["created"] and raw2 == RAW_540


def test_resolver_refuses_forged_answer_version():
    """Подложная ссылка на другую версию ответа — отказ с кодом и
    словами, называющими оба отпечатка."""
    doc, cell = _answered()
    forged = prov.answer_version_fingerprint(RAW_540)
    cell["source"]["version"]["fingerprint"] = forged
    with pytest.raises(prov.ProvenanceRefusal) as e:
        ans.resolve_source(doc, cell["source"])
    assert e.value.code == "answer-provenance"
    assert QID in str(e.value) and forged in str(e.value)
    assert prov.answer_version_fingerprint(RAW_600) in str(e.value)
    assert e.value.items == [QID]
    assert any("версия ответа" in p for p in m.validate_fit_quotes(doc, CLAUSES))


def test_resolver_refuses_changed_question_edition():
    """Вопрос переформулирован после ответа — издание другое, и
    прежнее значение к нему не относится."""
    doc, cell = _answered()
    was = cell["source"]["question"]["fingerprint"]
    _q(doc, QID)["text"] += " Уточнение: речь о рейсах из базы."
    with pytest.raises(prov.ProvenanceRefusal) as e:
        ans.resolve_source(doc, cell["source"])
    assert "издание вопроса" in str(e.value) and was in str(e.value)
    assert e.value.code == "answer-provenance"


def test_resolver_refuses_missing_question_and_unapplied_answer():
    doc, cell = _answered()
    src = copy.deepcopy(cell["source"])
    src["question"]["id"] = "Q-3b-параметр-нет-такого"
    with pytest.raises(prov.ProvenanceRefusal) as e:
        ans.resolve_source(doc, src)
    assert "нет в анкете" in str(e.value)
    _q(doc, QID)["answer"]["status"] = "ожидает обработчика"
    with pytest.raises(prov.ProvenanceRefusal) as e:
        ans.resolve_source(doc, cell["source"])
    assert "не применён" in str(e.value)
    # Вопрос, чей ответ применяется ПРЯМО СЕЙЧАС (приём заплаты,
    # поправка §12.3), резолвится — статус проставится по фиксации.
    q, raw = ans.resolve_source(doc, cell["source"], applying=(QID,))
    assert q["id"] == QID and raw == RAW_600


@pytest.mark.parametrize("broken,words", [
    ({"kind": "rule", "rule_id": "R-R"}, "не названо ответом"),
    ({"kind": "answer"}, "нет идентификатора издания"),
    ({"kind": "answer", "question": {"id": QID}}, "нет отпечатка издания"),
    (None, "не названо ответом"),
])
def test_resolver_refuses_malformed_source(broken, words):
    doc, _ = _answered()
    with pytest.raises(prov.ProvenanceRefusal) as e:
        ans.resolve_source(doc, broken)
    assert words in str(e.value)


def test_resolver_refuses_source_without_answer_version():
    doc, cell = _answered()
    src = copy.deepcopy(cell["source"])
    src.pop("version")
    with pytest.raises(prov.ProvenanceRefusal) as e:
        ans.resolve_source(doc, src)
    assert "отпечатка версии ответа" in str(e.value)


# ── 3. цитата и значение против точной версии ────────────────────────

def test_quote_must_be_verbatim_in_the_exact_version():
    doc, cell = _answered()
    cell["quote"] = "вне базы — 9 часов"          # текст ДРУГОЙ версии
    problems = m.validate_fit_quotes(doc, CLAUSES)
    assert any("подстрокой ответа" in p for p in problems)
    assert prov.package_problems(doc) == problems


def test_two_revisions_forged_reference_to_the_old_value_is_refused():
    """Контрпример K05: у параметра два пересмотра; исправные 540
    проходят полный quote-validator, подложная ссылка на старое 600
    отклоняется."""
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)
    ans.apply_answers(doc, {rep["created"]: RAW_540})
    rep2 = ans.open_revision(doc, rep["created"])
    ans.apply_answers(doc, {rep2["created"]: "Окончательно: 9 часов."})
    cell = _cell(doc)
    assert cell["value"] == 540
    assert m.validate_fit_quotes(doc, CLAUSES) == []      # исправное — проходит
    # Подлог: прежняя цифра с ссылкой на прежнюю версию ответа.
    forged = copy.deepcopy(doc)
    fcell = _cell(forged)
    fcell["value"] = 600
    fcell["quote"] = "10 часов"
    fcell["source"]["version"]["fingerprint"] = \
        prov.answer_version_fingerprint(RAW_600)
    problems = m.validate_fit_quotes(forged, CLAUSES)
    assert any("версия ответа" in p for p in problems)
    # Подлог второго рода: ссылка на корневой (пересмотренный) вопрос.
    forged2 = copy.deepcopy(doc)
    f2 = _cell(forged2)
    f2["value"] = 600
    f2["quote"] = "10 часов"
    f2["source"] = prov.answer_source(_q(forged2, QID), PARAM, RAW_600)
    assert any("не применён" in p for p in m.validate_fit_quotes(forged2,
                                                                CLAUSES))


def test_quote_does_not_prove_the_value_b1_counterexample():
    """B1 аудита: цитата подтверждает ПОДЛИННОСТЬ текста, но не
    значение — «без изменений» не обосновывает никакого числа, а
    «10 часов» не обосновывает 660."""
    empty = _doc()
    fp.ensure_family_param_questions(empty)
    assert ans.apply_answers(empty, {QID: "Без изменений."})["applied"] == 0
    assert _q(empty, QID)["answer"]["status"] == "не разобран"
    assert PARAM not in _rule(empty)["template_fit"]["params"]
    doc, cell = _answered()
    cell["value"] = 660                                # цитата прежняя, дословная
    problems = m.validate_fit_quotes(doc, CLAUSES)
    assert any("не равно прочитанному из цитаты" in p for p in problems)
    cell["quote"] = "Минимальный отдых вне базы"       # дословно, но без числа
    assert any("из цитаты" in p and "не читается" in p
               for p in m.validate_fit_quotes(doc, CLAUSES))


def test_cell_without_quote_is_refused():
    doc, cell = _answered()
    cell["quote"] = " "
    assert any("без цитаты" in p for p in prov.package_problems(doc))


# ── 4. полная проверка цитат требует анкету ──────────────────────────

def test_slice_without_questions_is_a_contract_error():
    doc, _ = _answered()
    with pytest.raises(ValueError) as e:
        m.validate_fit_quotes({"rules": [_rule(doc)]}, CLAUSES)
    assert "questions" in str(e.value)
    # Срез БЕЗ значений из ответа судится по-прежнему — закон касается
    # только того, что без анкеты не судится.
    plain = copy.deepcopy(_rule(doc))
    plain["template_fit"]["params"].pop(PARAM)
    assert m.validate_fit_quotes({"rules": [plain]},
                                 {("S1", "п.2"): CLAUSES[("S1", "п.2")]}) == []


def test_answer_marker_without_question_is_a_violation_not_trust():
    doc, cell = _answered()
    doc["questions"] = [q for q in doc["questions"] if q["id"] != QID]
    assert any("нет в анкете" in p for p in m.validate_fit_quotes(doc, CLAUSES))


# ── 5. приём заплаты, выпуск и гейт ──────────────────────────────────

def _worker_reply(qid, rules):
    return json.dumps({"patches": [
        {"question": qid, "change": "ответ внесён", "rules": rules}]},
        ensure_ascii=False)


def _doc_after_the_code_unlanded_the_cell():
    """Свод, у которого ячейку ответа снял КОД: пересмотр (`-R`) и
    решение авиакомпании «не применяется». У строки остаётся только
    клаузная ячейка — закон происхождения ей не указ, и снятие строки
    либо смена её вида заплатой дальше законны (положительный
    контроль: закон бьёт подлог, а не работу аудитора)."""
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)
    assert ans.apply_answers(doc, {rep["created"]: "не применяется"}
                             )["applied"] == 1
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    return doc


def _doc_with_open_question(base=None):
    """Свод с применённым значением параметра и ОТКРЫТЫМ вопросом по
    той же строке — вход для заплаты обработчика."""
    doc = base if base is not None else _answered()[0]
    doc["questions"].append(
        {"id": "Q-3b-уточнение", "part": "3b", "title": "Уточнение",
         "text": "Меняется ли пол отдыха вне базы?",
         "answer_format": {"type": "value",
                           "note": "минуты либо «без изменений»"},
         "rule_refs": ["R-R"],
         "source_refs": [{"sid": "S1", "anchor": "п.2"}]})
    _rule(doc)["blockers"] = ["Q-3b-уточнение"]
    assert ans.apply_answers(doc, {"Q-3b-уточнение": "Без изменений."}
                             )["awaiting"] == 1
    return doc


def test_patch_acceptance_refuses_invented_value_with_fictitious_basis():
    """B1 на приёме заплаты: обычная заплата меняет значение и
    ссылается на применённый ответ — отказ словами, статус вопроса
    остаётся «ожидает обработчика»."""
    doc = _doc_with_open_question()
    forged = copy.deepcopy(_rule(doc))
    forged["template_fit"]["params"][PARAM]["value"] = 660
    forged.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [forged]))
    assert summary["worker_applied"] == 0
    assert any("не равно прочитанному из цитаты" in r
               for r in summary["worker_rejected"][0]["reasons"])
    assert _q(doc, "Q-3b-уточнение")["answer"]["status"] == \
        "ожидает обработчика"
    assert _cell(doc)["value"] == 600
    # Та же подмена со ссылкой на отвечаемый ВОПРОС-ОСНОВАНИЕ
    # («без изменений» числа не несёт) — отказ по той же причине.
    forged2 = copy.deepcopy(_rule(doc))
    forged2["template_fit"]["params"][PARAM] = {
        "value": 660, "quote": "Без изменений",
        "source": prov.answer_source(_q(doc, "Q-3b-уточнение"), PARAM,
                                     "Без изменений.")}
    forged2.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [forged2]))
    assert summary["worker_applied"] == 0
    assert any("не читается" in r
               for r in summary["worker_rejected"][0]["reasons"])


def test_patch_acceptance_lets_the_honest_patch_through():
    """Положительный контроль: заплата, не трогающая значение, проходит
    — закон отвергает подлог, а не работу обработчика."""
    doc = _doc_with_open_question()
    ok = copy.deepcopy(_rule(doc))
    ok["note"] = "уточнено ответом Q-3b-уточнение: пол отдыха не меняется"
    ok.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [ok]))
    assert summary["worker_rejected"] == [] and summary["worker_applied"] == 1
    assert _q(doc, "Q-3b-уточнение")["answer"]["status"] == "применён"
    assert _cell(doc)["value"] == 600


def _run_dir(tmp_path, doc, name="run-9001"):
    run = tmp_path / name
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (run / "extract" / "svod.md").write_text("# свод\n", encoding="utf-8")
    return run


def test_release_refuses_answer_provenance_with_code_and_items(tmp_path):
    doc, cell = _answered()
    cell["value"] = 660                       # выдуманное значение
    run = _run_dir(tmp_path, doc)
    with pytest.raises(tp.TaskPackageError) as e:
        tp.export_zip(run)
    assert e.value.code == "answer-provenance"
    assert e.value.items == [f"R-R:{PARAM}"]
    assert "происхождение значения из ответа не подтверждено" in str(e.value)


def test_release_and_gate_green_on_confirmed_provenance(tmp_path):
    doc, _ = _answered()
    run = _run_dir(tmp_path, doc)
    body = tp.export_zip(run)
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(body)
    assert gate.check(pkg)[0] == []


def _forge_package(body: bytes, edit) -> bytes:
    """Подделка выпущенного пакета «по-настоящему»: модель правится, а
    суммы манифеста пересчитываются — гейт судит содержание, не суммы."""
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        files = {n: z.read(n) for n in z.namelist()}
    doc = json.loads(files["model.json"].decode("utf-8"))
    edit(doc)
    files["model.json"] = json.dumps(doc, ensure_ascii=False,
                                     indent=1).encode("utf-8")
    manifest = json.loads(files["manifest.json"].decode("utf-8"))
    for entry in manifest.get("files", []):
        if entry.get("name") in files:
            entry["sha256"] = hashlib.sha256(
                files[entry["name"]]).hexdigest()
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False,
                                        indent=1).encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()


def test_gate_reds_the_forged_package(tmp_path):
    """Приёмник считает по тому же закону, что производитель: подмена
    значения в выпущенном пакете (с пересчитанными суммами) краснит
    гейт словами и кодом."""
    doc, _ = _answered()
    body = tp.export_zip(_run_dir(tmp_path, doc))

    def bump(d):
        next(r for r in d["rules"] if r["id"] == "R-R")[
            "template_fit"]["params"][PARAM]["value"] = 660

    pkg = tmp_path / "forged.zip"
    pkg.write_bytes(_forge_package(body, bump))
    problems = gate.check(pkg)[0]
    assert any("answer-provenance" in p and "не равно прочитанному" in p
               for p in problems)


def test_gate_reds_the_forged_reference_to_another_version(tmp_path):
    doc, _ = _answered()
    body = tp.export_zip(_run_dir(tmp_path, doc))

    def repoint(d):
        cell = next(r for r in d["rules"] if r["id"] == "R-R")[
            "template_fit"]["params"][PARAM]
        cell["source"]["version"]["fingerprint"] = \
            prov.answer_version_fingerprint(RAW_540)

    pkg = tmp_path / "forged2.zip"
    pkg.write_bytes(_forge_package(body, repoint))
    assert any("версия ответа" in p for p in gate.check(pkg)[0])


# ── 6. старая форма ячейки остаётся читаемой ─────────────────────────

def test_legacy_cell_with_string_answer_is_still_readable(tmp_path):
    """Миграция — волна 17б: ячейка старой формы (одна строка `answer`,
    без объекта происхождения) читается и выпускается, пока её цитата
    дословна, а значение из цитаты читается."""
    doc, cell = _answered()
    cell.pop("source")                       # ячейка прежних прогонов
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    assert m.validate_fit_quotes(doc, CLAUSES) == []
    assert prov.package_problems(doc) == []
    body = tp.export_zip(_run_dir(tmp_path, doc))
    pkg = tmp_path / "legacy.zip"
    pkg.write_bytes(body)
    assert gate.check(pkg)[0] == []


def test_legacy_cell_is_judged_by_the_same_value_law():
    """Старая форма не значит «без суда»: приписанное значение при
    дословной цитате отклоняется и у неё."""
    doc, cell = _answered()
    cell.pop("source")
    cell["value"] = 660
    assert any("не равно прочитанному из цитаты" in p
               for p in m.validate_fit_quotes(doc, CLAUSES))
    cell["value"] = 600
    cell["answer"] = "Q-3b-параметр-нет-такого"
    assert any("нет в анкете" in p for p in prov.package_problems(doc))


# ── 7. форма ячейки закон не отменяет (находка ревью 1) ──────────────

def test_legacy_cell_is_judged_by_the_answer_in_force_of_its_chain(tmp_path):
    """ФОРМА ячейки не освобождает от закона точной версии. У ячейки без
    `source` точная версия — ДЕЙСТВУЮЩИЙ ответ цепочки (последний по
    `revised_by`): прежняя цифра со ссылкой на пересмотренный корень не
    выпускается, хотя корень в анкете есть и «пересмотрен» — законное
    состояние."""
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)
    ans.apply_answers(doc, {rep["created"]: RAW_540})
    rep2 = ans.open_revision(doc, rep["created"])
    ans.apply_answers(doc, {rep2["created"]: "Окончательно: 9 часов."})
    forged = copy.deepcopy(doc)
    cell = _cell(forged)
    cell.clear()
    cell.update({"value": 600, "quote": "10 часов", "answer": QID})
    # Свод остаётся структурно исправным — ловит именно закон версии.
    assert m.validate_schema(forged) + m.validate_model(forged) == []
    # Прямо: цитата ПОДЛИННА для пересмотренного корня, и прежде её
    # оправдывал именно он; действующий же ответ цепочки — другой.
    assert "10 часов" in _q(forged, QID)["answer"]["raw"]
    assert _q(forged, QID)["answer"]["status"] == "пересмотрен"
    assert _cell(doc)["value"] == 540
    problems = m.validate_fit_quotes(forged, CLAUSES)
    assert any("подстрокой ответа" in p for p in problems)
    assert prov.package_problems(forged) == problems
    with pytest.raises(tp.TaskPackageError) as e:
        tp.export_zip(_run_dir(tmp_path, forged, "run-forged"))
    assert e.value.code == "answer-provenance"
    assert e.value.items == [f"R-R:{PARAM}"]
    # Положительный контроль: честная старая форма с ДЕЙСТВУЮЩИМ
    # значением цепочки проходит всё — закон бьёт подлог, не форму.
    honest = copy.deepcopy(doc)
    hcell = _cell(honest)
    hcell.clear()
    hcell.update({"value": 540, "quote": "9 часов", "answer": QID})
    assert m.validate_fit_quotes(honest, CLAUSES) == []
    assert prov.package_problems(honest) == []
    pkg = tmp_path / "honest.zip"
    pkg.write_bytes(tp.export_zip(_run_dir(tmp_path, honest, "run-honest")))
    assert gate.check(pkg)[0] == []


def test_legacy_cell_without_an_answer_in_force_is_refused():
    """Цепочка без действующего ответа (корень пересмотрен, ответа на
    пересмотр ещё нет) ячейку не обосновывает: «уцелела» не значит
    «подтверждена»."""
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)
    _rule(doc)["template_fit"]["params"][PARAM] = {
        "value": 600, "quote": "10 часов", "answer": QID}
    assert _q(doc, rep["created"]).get("answer") is None
    assert any("а его ещё нет" in p
               for p in m.validate_fit_quotes(doc, CLAUSES))


def test_patch_may_not_introduce_or_change_a_cell_without_exact_origin():
    """Новые ячейки — только с точным происхождением: заплата не вправе
    завести ячейку, помеченную ответом, без `source`, снять
    происхождение у имеющейся и изменить значение/цитату/метку ячейки
    старой формы. Иначе форма ячейки — обход закона точной версии
    (вектор B1: обработчик после пересмотра пишет прежние 600)."""
    # а) снятие происхождения у ячейки, внесённой ответом.
    doc = _doc_with_open_question()
    strip = copy.deepcopy(_rule(doc))
    strip["template_fit"]["params"][PARAM] = {
        "value": 600, "quote": "10 часов", "answer": QID}
    strip.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [strip]))
    assert summary["worker_applied"] == 0
    assert any("снято точное происхождение" in r
               for r in summary["worker_rejected"][0]["reasons"])
    assert _cell(doc).get("source") is not None and _cell(doc)["value"] == 600
    # б) новая ячейка, помеченная ответом, там, где её не было.
    doc = _doc_with_open_question()
    born = copy.deepcopy(_rule(doc))
    born["template_fit"]["params"]["min_rest_home"] = {
        "value": 720, "quote": "дома — 12:00", "answer": QID}
    born.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [born]))
    assert summary["worker_applied"] == 0
    assert any("заведён заплатой без точного происхождения" in r
               for r in summary["worker_rejected"][0]["reasons"])
    assert "answer" not in _cell(doc, name="min_rest_home")
    # в) изменение ячейки СТАРОЙ формы заплатой.
    doc = _doc_with_open_question()
    _cell(doc).pop("source")
    moved = copy.deepcopy(_rule(doc))
    moved["template_fit"]["params"][PARAM]["value"] = 660
    moved.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [moved]))
    assert summary["worker_applied"] == 0
    assert any("изменена заплатой (value)" in r
               for r in summary["worker_rejected"][0]["reasons"])
    assert _cell(doc)["value"] == 600
    # г) положительный контроль: ячейку старой формы заплата вправе
    # оставить как есть — закон бьёт подлог, а не работу обработчика.
    doc = _doc_with_open_question()
    _cell(doc).pop("source")
    keep = copy.deepcopy(_rule(doc))
    keep["note"] = "уточнено ответом: пол отдыха не меняется"
    keep.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [keep]))
    assert summary["worker_rejected"] == [] and summary["worker_applied"] == 1
    assert _cell(doc)["value"] == 600


# ── 8. «прямо сейчас применяется» — ровно отвечаемый вопрос ──────────

def _doc_with_plain_question_answered_by_worker():
    """Свод, где значение параметра внесено заплатой обработчика по
    ОБЫЧНОМУ (не параметрному) вопросу — вход для пересмотра."""
    doc = _doc()
    fp.ensure_family_param_questions(doc)
    ans.apply_answers(doc, {QID: "не применяется"})
    doc["questions"].append(
        {"id": "Q-3b-X", "part": "3b", "title": "Пол отдыха вне базы",
         "text": "Сколько минут минимального отдыха вне базы?",
         "answer_format": {"type": "value", "note": "минуты"},
         "rule_refs": ["R-R"],
         "source_refs": [{"sid": "S1", "anchor": "п.2"}]})
    _rule(doc)["blockers"] = ["Q-3b-X"]
    assert ans.apply_answers(doc, {"Q-3b-X": "10 часов"})["awaiting"] == 1
    ok = copy.deepcopy(_rule(doc))
    ok["template_fit"]["params"][PARAM] = {
        "value": 600, "quote": "10 часов", "answer": "Q-3b-X",
        "source": prov.answer_source(_q(doc, "Q-3b-X"), PARAM, "10 часов")}
    ok.pop("blockers", None)
    assert ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-X", [ok]))["worker_applied"] == 1
    return doc


def test_patch_on_a_revision_may_not_lean_on_the_revised_root():
    """«Прямо сейчас применяется» — РОВНО отвечаемый вопрос. Корень
    цепочки и соседний пересмотр применёнными не считаются: иначе
    заплата на `X-R` оправдывала бы значение пересмотренным ответом на
    `X` — приём проходил, а рубеж выпуска отказывал, то есть об одном
    своде было два вердикта (общий закон П0 № 4)."""
    doc = _doc_with_plain_question_answered_by_worker()
    XR = ans.open_revision(doc, "Q-3b-X")["created"]
    assert ans.apply_answers(doc, {XR: "9 часов"})["awaiting"] == 1
    forged = copy.deepcopy(_rule(doc))
    forged["template_fit"]["params"][PARAM] = {
        "value": 600, "quote": "10 часов", "answer": "Q-3b-X",
        "source": prov.answer_source(_q(doc, "Q-3b-X"), PARAM, "10 часов")}
    forged.pop("blockers", None)
    summary = ans.apply_worker_patches(doc,
                                       lambda p: _worker_reply(XR, [forged]))
    assert summary["worker_applied"] == 0
    assert any("пересмотрен" in r and "не применён" in r
               for r in summary["worker_rejected"][0]["reasons"])
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    # Прямо о законе, а не о статусе: тот же источник не разрешается и
    # при `applying` с отвечаемым пересмотром — «прямо сейчас
    # применяется» не распространяется на корень цепочки.
    with pytest.raises(prov.ProvenanceRefusal):
        ans.resolve_source(doc,
                           forged["template_fit"]["params"][PARAM]["source"],
                           applying=(XR,))
    # Положительный контроль: заплата, читающая ОТВЕЧАЕМЫЙ пересмотр,
    # проходит — статус «применён» проставится по фиксации.
    honest = copy.deepcopy(_rule(doc))
    honest["template_fit"]["params"][PARAM] = {
        "value": 540, "quote": "9 часов", "answer": "Q-3b-X",
        "source": prov.answer_source(_q(doc, XR), PARAM, "9 часов")}
    honest.pop("blockers", None)
    summary = ans.apply_worker_patches(doc,
                                       lambda p: _worker_reply(XR, [honest]))
    assert summary["worker_rejected"] == [] and summary["worker_applied"] == 1
    assert _cell(doc)["value"] == 540
    assert m.validate_fit_quotes(doc, CLAUSES) == []


# ── 9. адресат значения (находка ревью 3) ────────────────────────────

def test_value_is_justified_only_by_an_answer_about_this_very_cell():
    """Вопрос параметра семьи несёт адресата в своём ключе: применённый
    ответ о ДРУГОМ параметре той же семьи значение не обосновывает,
    сколь угодно дословной ни была бы цитата."""
    doc = _doc()
    _rule(doc)["template_fit"]["params"].pop("min_rest_home")
    assert len(fp.ensure_family_param_questions(doc)) == 2
    qh = f"Q-3b-параметр-{FAMILY}-min_rest_home"
    assert ans.apply_answers(doc, {qh: "Дома — 12 часов.",
                                   QID: RAW_600})["applied"] == 2
    assert m.validate_fit_quotes(doc, CLAUSES) == []      # честное — чисто
    forged = copy.deepcopy(doc)
    cell = _cell(forged)
    cell["value"], cell["quote"], cell["answer"] = 720, "12 часов", qh
    cell["source"] = prov.answer_source(_q(forged, qh), PARAM,
                                        "Дома — 12 часов.")
    # Цитата дословна, значение из неё читается, отпечатки сходятся —
    # ловит именно адресат.
    assert any("о параметре 'min_rest_home'" in p and "а не о" in p
               for p in m.validate_fit_quotes(forged, CLAUSES))


def test_plain_question_justifies_only_the_rows_it_addresses():
    """Прочий вопрос адресует СТРОКИ: применённый ответ о другой строке
    ячейку не обосновывает; строка, названная вопросом либо названная
    ЖУРНАЛОМ СЛЕДСТВИЙ его ответа, — обосновывает."""
    raw = "Вне базы — 10 часов."
    base = _doc()
    fp.ensure_family_param_questions(base)
    ans.apply_answers(base, {QID: "не применяется"})
    base["questions"].append(
        {"id": "Q-3b-Z", "part": "3b", "title": "Про жёсткое",
         "text": "Сколько?", "answer_format": {"type": "value",
                                               "note": "минуты"},
         "rule_refs": ["R-H"],
         "source_refs": [{"sid": "S1", "anchor": "п.1"}],
         "answer": {"raw": raw, "status": "применён", "revision": 2}})

    def with_cell(doc):
        _rule(doc)["template_fit"]["params"][PARAM] = {
            "value": 600, "quote": "10 часов", "answer": "Q-3b-Z",
            "source": prov.answer_source(_q(doc, "Q-3b-Z"), PARAM, raw)}
        return doc

    alien = with_cell(copy.deepcopy(base))
    assert any("адресует строки R-H, а не R-R" in p
               for p in m.validate_fit_quotes(alien, CLAUSES))
    named = copy.deepcopy(base)
    _q(named, "Q-3b-Z")["rule_refs"] = ["R-R"]
    assert m.validate_fit_quotes(with_cell(named), CLAUSES) == []
    # Журнал следствий ответа — тоже адрес: строку, которую ответ УЖЕ
    # менял, он обосновывает, и запись эта во времени не убывает.
    journaled = copy.deepcopy(base)
    _q(journaled, "Q-3b-Z")["answer"]["effects"] = [
        {"rule": "R-R", "field": "template_fit"}]
    assert m.validate_fit_quotes(with_cell(journaled), CLAUSES) == []
    # Запись журнала о ЦЕЛЕВЫХ адресом строки не бывает.
    objectives = copy.deepcopy(base)
    _q(objectives, "Q-3b-Z")["answer"]["effects"] = [
        {"rule": "__objectives__", "field": "O-1"}]
    assert any("адресует строки R-H, а не R-R" in p
               for p in m.validate_fit_quotes(with_cell(objectives), CLAUSES))


def test_addressee_of_a_value_does_not_depend_on_the_moment_of_judging():
    """Набор адресатов НЕ ЗАВИСИТ ОТ ВРЕМЕНИ (находка второго ревью):
    прежде в него входили строки, ЗАБЛОКИРОВАННЫЕ вопросом, а блокировку
    снимает применение ответа — на приёме заплаты она стояла, на выпуске
    её уже не было, и об одном своде выходило ДВА вердикта (общий закон
    П0 № 4). Теперь приём и выпуск судят один набор: строки `rule_refs`
    цепочки и строки журнала следствий её ответов."""
    def _built(refs):
        doc = _doc()
        fp.ensure_family_param_questions(doc)
        ans.apply_answers(doc, {QID: "не применяется"})
        doc["questions"].append(
            {"id": "Q-3b-W", "part": "3b", "title": "Пол отдыха вне базы",
             "text": "Сколько минут отдыха вне базы?",
             "answer_format": {"type": "value", "note": "минуты"},
             "rule_refs": list(refs),
             "source_refs": [{"sid": "S1", "anchor": "п.2"}]})
        _rule(doc)["blockers"] = ["Q-3b-W"]    # R-R заблокирована вопросом
        assert m.validate_model(doc) == []
        assert ans.apply_answers(doc, {"Q-3b-W": "10 часов"})["awaiting"] == 1
        patch = copy.deepcopy(_rule(doc))
        patch.pop("blockers", None)
        patch["template_fit"]["params"][PARAM] = {
            "value": 600, "quote": "10 часов", "answer": "Q-3b-W",
            "source": prov.answer_source(_q(doc, "Q-3b-W"), PARAM,
                                         "10 часов")}
        summary = ans.apply_worker_patches(
            doc, lambda p: _worker_reply("Q-3b-W", [patch]))
        return doc, summary

    # Чужая строка: вопрос блокирует R-R, а называет R-H — отказ на
    # ОБОИХ рубежах, а не «принято, но не выпущено».
    doc, summary = _built(["R-H"])
    assert summary["worker_applied"] == 0
    assert any("адресует строки R-H, а не R-R" in r
               for r in summary["worker_rejected"][0]["reasons"])
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    assert prov.package_problems(doc) == []       # подлога в своде нет
    # Своя строка: названа вопросом — принято приёмом и выпущено
    # выпуском, хотя блокировка к выпуску уже снята.
    doc, summary = _built(["R-R"])
    assert summary["worker_rejected"] == [] and summary["worker_applied"] == 1
    assert _rule(doc).get("blockers") is None     # блокировка снята
    assert _cell(doc)["value"] == 600
    assert prov.package_problems(doc) == []
    assert m.validate_fit_quotes(doc, CLAUSES) == []


# ── 10. один факт — одно место: effect, root, прежняя строка ─────────

def test_effect_root_and_legacy_marker_are_cross_checked():
    """Адрес эффекта, корень родословной и прежняя строка `answer`
    сверяются с ячейкой и её вопросом: расхождение — отказ, а не выбор
    «кому верить» (общий закон П0 № 2)."""
    doc, cell = _answered()
    cell["source"]["effect"] = {"rule": "weight", "field": "quote"}
    cell["source"]["root"] = "Q-3a-GOAL"
    problems = m.validate_fit_quotes(doc, CLAUSES)
    assert any("адрес эффекта" in p and "а ячейка" in p for p in problems)
    assert any("не значение параметра (value)" in p for p in problems)
    assert any("корень родословной" in p for p in problems)
    # Прежняя строка `answer`, называющая другой вопрос, — тоже отказ.
    doc, cell = _answered()
    cell["answer"] = "Q-3a-GOAL"
    assert m.validate_model(doc) == []
    assert any("прежняя строка answer" in p
               for p in m.validate_fit_quotes(doc, CLAUSES))


# ── 11. замок 4П судит цитаты ИЗ КЛАУЗ (находка ревью 5) ─────────────

def test_fit_lock_counts_clause_cells_only_not_answer_cells():
    """Замок «не переписать при проверке» судит чистоту ЦИТАТНОГО слоя
    — цитат из клауз. Ячейки, внесённые ответом, в этот счёт не входят:
    иначе замок выключался бы СОСТОЯНИЕМ АНКЕТЫ (вопрос пересмотрен —
    замок снят), и заплата обмена понижала бы посадку A→C, теряя оба
    параметра. Происхождение таких ячеек судит свой закон."""
    doc, _ = _answered()
    prev = copy.deepcopy(_rule(doc))
    downgrade = copy.deepcopy(prev)
    downgrade["template_fit"] = {"category": "C", "family": FAMILY,
                                 "registry_rev": REV, "gap": "спорно"}
    for status in ("применён", "пересмотрен"):
        _q(doc, QID)["answer"]["status"] = status
        reasons = ex.fit_integrity_problems(prev, downgrade, CLAUSES, doc)
        assert any("понижение посадки A→C" in r for r in reasons), status
        assert any("потеря параметров посадки" in r for r in reasons), status
    # Прямо о механизме: при пересмотренном вопросе ячейка из ответа не
    # разрешается, и прежде ИМЕННО это выключало замок — «цитатный слой
    # не чист». Теперь этот счёт ведётся по клаузным ячейкам.
    assert _q(doc, QID)["answer"]["status"] == "пересмотрен"
    assert m.validate_fit_quotes({"questions": doc["questions"],
                                  "rules": [prev]}, CLAUSES) != []
    # Ячейка старой формы при пересмотренном вопросе — тот же вердикт.
    legacy = copy.deepcopy(prev)
    legacy["template_fit"]["params"][PARAM].pop("source")
    assert ex.fit_integrity_problems(legacy, downgrade, CLAUSES, doc)
    # Выдуманная цитата ИЗ КЛАУЗЫ замок по-прежнему выключает: чистота
    # цитатного слоя — условие замка, и оно судится по клаузным ячейкам.
    dirty = copy.deepcopy(prev)
    dirty["template_fit"]["params"]["min_rest_home"]["quote"] = "дома — 14:00"
    assert ex.fit_integrity_problems(dirty, downgrade, CLAUSES, doc) == []


# ── 12. полный ответ ограничивает допустимость вырезанной цитаты ──

@pytest.mark.parametrize("raw,quote,value,applied", [
    ("Минимальный отдых вне базы 600 минут, не 660 минут.", "660 минут", 660, 0),
    ("Минимальный отдых вне базы 600 минут.", "600 минут", 600, 1),
])
def test_full_answer_constrains_a_scalar_worker_quotation(raw, quote, value, applied):
    """K03/A03: подлинная цитата не обходит отказ полного ответа.

    Прежний тест минимальной части документировал приём неверных660.
    Теперь общий барьер требует согласованного значения из всего raw;
    здоровая заплата проходит тем же маршрутом. Это не полный разбор
    смысла/отрицаний: неоднозначный полный ответ остаётся открытым.
    """
    # Частичная защита: несколько разных значений — «не разобран».
    doc = _doc()
    fp.ensure_family_param_questions(doc)
    assert ans.apply_answers(
        doc, {QID: "Дома — 720 минут, вне базы — 600 минут."})["applied"] == 0
    assert _q(doc, QID)["answer"]["status"] == "не разобран"
    # Подлинность подстроки необходима, но не достаточна.
    doc = _doc()
    fp.ensure_family_param_questions(doc)
    ans.apply_answers(doc, {QID: "не применяется"})
    doc["questions"].append(
        {"id": "Q-3b-Y", "part": "3b", "title": "Пол", "text": "Сколько?",
         "answer_format": {"type": "value", "note": "минуты"},
         "rule_refs": ["R-R"],
         "source_refs": [{"sid": "S1", "anchor": "п.2"}]})
    _rule(doc)["blockers"] = ["Q-3b-Y"]
    assert ans.apply_answers(doc, {"Q-3b-Y": raw})["awaiting"] == 1
    cut = copy.deepcopy(_rule(doc))
    cut["template_fit"]["params"][PARAM] = {
        "value": value, "quote": quote, "answer": "Q-3b-Y",
        "source": prov.answer_source(_q(doc, "Q-3b-Y"), PARAM, raw)}
    cut.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-Y", [cut]))
    assert summary["worker_applied"] == applied
    if applied:
        assert _cell(doc)["value"] == value
    else:
        assert PARAM not in _rule(doc)["template_fit"]["params"]
        assert "Q-3b-Y" in _rule(doc)["blockers"]


# ── 13. тот же закон заплаты на приёме заплаты ОБМЕНА ────────────────

def _exchange_round(doc, rule):
    """Круг обмена с одной ПРИНЯТОЙ правкой строки-носителя."""
    pack = {"round": 1, "remarks": [
        {"id": "A-001", "severity": "КРИТИЧЕСКОЕ",
         "defect_class": "искажение", "target": {"rule": "R-R"},
         "source_refs": [{"sid": "S1", "anchor": "п.2"}],
         "text": "Пол отдыха вне базы прочитан неверно",
         "evidence": "«Минимальный отдых дома — 12:00»",
         "proposal": "вернуть прежнее значение"}]}
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО",
         "basis": "источник: «12:00» — принято",
         "change": "строка переписана",
         "patch": {"rule_id": "R-R", "rule": rule}}]}
    return ex._apply_patches(doc, pack, resp, 1, clause_texts=CLAUSES)


def test_exchange_patch_obeys_the_same_origin_law():
    """Приём заплаты обмена судит происхождение тем же расчётом, что
    приём заплаты обработчика: ячейку, помеченную ответом, нельзя
    завести без `source`, снять у неё происхождение и изменить её
    старую форму. Иначе один и тот же подлог отбивался бы на одном
    пути и проходил на другом."""
    doc, _ = _answered()
    strip = copy.deepcopy(_rule(doc))
    strip["template_fit"]["params"][PARAM] = {
        "value": 600, "quote": "10 часов", "answer": QID}
    new_doc, _acc, rejected, _tok = _exchange_round(doc, strip)
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "снято точное происхождение" in reasons
    assert _cell(new_doc).get("source") is not None
    # Положительный контроль: та же заплата, не трогающая ячейку,
    # принимается — закон бьёт подлог, а не работу аудитора.
    doc, _ = _answered()
    ok = copy.deepcopy(_rule(doc))
    ok["note"] = "уточнено обменом: пол отдыха не меняется"
    new_doc, _acc, rejected, _tok = _exchange_round(doc, ok)
    assert rejected == []
    assert _rule(new_doc).get("note") == \
        "уточнено обменом: пол отдыха не меняется"
    assert _cell(new_doc)["value"] == 600


def test_origin_patch_law_is_one_computation():
    """Закон заплаты живёт ОДНОЙ функцией (`provenance.patch_problems`)
    — её и зовут оба приёмника; три её случая называются словами."""
    prev = {"id": "R-R", "template_fit": {"params": {
        "p": {"value": 1, "quote": "1", "answer": "Q",
              "source": {"kind": "answer", "question": {"id": "Q"},
                         "version": {"fingerprint": "0" * 12},
                         "effect": {"rule": "p", "field": "value"},
                         "root": "Q"}}}}}
    assert prov.patch_problems(prev, prev) == []
    born = {"id": "R-R", "template_fit": {"params": {
        "p": {"value": 1, "quote": "1", "answer": "Q"}}}}
    assert any("заведён заплатой без точного происхождения" in p
               for p in prov.patch_problems({"id": "R-R"}, born))
    assert any("снято точное происхождение" in p
               for p in prov.patch_problems(prev, born))
    moved = copy.deepcopy(born)
    moved["template_fit"]["params"]["p"]["value"] = 2
    assert any("изменена заплатой (value)" in p
               for p in prov.patch_problems(born, moved))
    # Ячейка БЕЗ метки ответа (значение из клаузы) закону не подчинена:
    # её судит цитатный слой клауз, а не происхождение ответа.
    clause = {"id": "R-R", "template_fit": {"params": {
        "p": {"value": 2, "quote": "2"}}}}
    assert prov.patch_problems({"id": "R-R"}, clause) == []


# ── 14. вид ячейки — не дверь (находки второго ревью) ────────────────

def test_patch_may_not_strip_the_answer_marker_from_a_cell():
    """Дверь «снятие метки»: заплата переписывает ячейку, внесённую
    ответом, КЛАУЗНОЙ ячейкой `{value, quote}` — цитата взята из клаузы
    и подлинна, значение выдумано, а закон происхождения молчал, потому
    что ячейка «уже не ответная». Так значение из ответа выходило бы
    из-под закона сменой ВИДА ячейки, а не основанием: приём заплаты,
    выпуск и гейт пропускали 660. Закон один на оба приёмника."""
    doc = _doc_with_open_question()
    strip = copy.deepcopy(_rule(doc))
    strip["template_fit"]["params"][PARAM] = {"value": 660,
                                              "quote": "дома — 12:00"}
    strip.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [strip]))
    assert summary["worker_applied"] == 0
    assert any("снята метка ответа" in r
               for r in summary["worker_rejected"][0]["reasons"])
    assert _cell(doc)["value"] == 600 and _cell(doc).get("source") is not None
    # Тот же расчёт на приёме заплаты ОБМЕНА.
    doc, _ = _answered()
    strip2 = copy.deepcopy(_rule(doc))
    strip2["template_fit"]["params"][PARAM] = {"value": 660,
                                               "quote": "дома — 12:00"}
    new_doc, _acc, rejected, _tok = _exchange_round(doc, strip2)
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "снята метка ответа" in reasons
    assert _cell(new_doc)["value"] == 600
    assert prov.package_problems(new_doc) == []


def test_patch_may_not_change_the_kind_of_the_cells_origin():
    """Дверь «смена вида источника»: метка `answer` на месте, а
    `source.kind` объявлен иным («rule», «code») — схема такую ячейку
    принимает, писателя у неё нет. Прежде провенанс её пропускал («не
    ячейка ответа»), а измеритель считал несомой ответом: два читателя
    видели одну ячейку по-разному, и 660 выпускалось."""
    for kind in ("rule", "code"):
        doc = _doc_with_open_question()
        switched = copy.deepcopy(_rule(doc))
        switched["template_fit"]["params"][PARAM] = {
            "value": 660, "quote": "10 часов", "answer": QID,
            "source": {"kind": kind, "note": "миграция"}}
        switched.pop("blockers", None)
        summary = ans.apply_worker_patches(
            doc, lambda p: _worker_reply("Q-3b-уточнение", [switched]))
        assert summary["worker_applied"] == 0, kind
        assert any("изменён вид источника" in r
                   for r in summary["worker_rejected"][0]["reasons"]), kind
        assert _cell(doc)["value"] == 600, kind
    # Обмен закрывает ту же дверь: цитата взята ИЗ КЛАУЗЫ, и прежде
    # заплата проходила. Первым говорит тот из двух судей, чья очередь
    # раньше, — закон ячейки («не названо ответом») либо закон заплаты
    # («изменён вид источника»); оба называют одну и ту же подмену.
    doc, _ = _answered()
    switched = copy.deepcopy(_rule(doc))
    switched["template_fit"]["params"][PARAM] = {
        "value": 660, "quote": "12:00", "answer": QID,
        "source": {"kind": "rule", "rule_id": "R-R"}}
    assert any("изменён вид источника" in p
               for p in prov.patch_problems(_rule(doc), switched))
    new_doc, _acc, rejected, _tok = _exchange_round(doc, switched)
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "изменён вид источника" in reasons \
        or "не названо ответом" in reasons
    assert _cell(new_doc)["value"] == 600


def test_switched_origin_kind_is_a_violation_at_release_too(tmp_path):
    """Тот же подлог, попавший в свод помимо заплаты, краснит полную
    проверку цитат, рубеж выпуска и гейт словами «не названо ответом»:
    ячейку судит ОДИН предикат, поэтому молчания больше нет."""
    doc, cell = _answered()
    cell["value"] = 660
    cell["source"] = {"kind": "code", "note": "миграция"}
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    assert any("не названо ответом" in p
               for p in m.validate_fit_quotes(doc, CLAUSES))
    assert prov.package_problems(doc) != []
    with pytest.raises(tp.TaskPackageError) as e:
        tp.export_zip(_run_dir(tmp_path, doc, "run-9031"))
    assert e.value.code == "answer-provenance"
    assert e.value.items == [f"R-R:{PARAM}"]


def test_measurer_and_origin_law_read_one_cell_alike():
    """Предикат «ячейка названа ответом» один на двух читателей
    (`provenance.is_answer_cell`): у измерителя постановки такая ячейка
    несома ОТВЕТОМ (клаузой не судится), у закона происхождения — она
    же ячейка ответа. Клаузная ячейка — наоборот, у обоих."""
    switched = {"value": 660, "quote": "10 часов", "answer": QID,
                "source": {"kind": "rule", "rule_id": "R-R"}}
    assert prov.is_answer_cell(switched) is True
    rows = ms.field_values({"template_fit": {"params": {PARAM: switched}}})
    assert [r.get("carrier_answer") for r in rows] == [QID]
    clause = {"value": 720, "quote": "дома — 12:00"}
    assert prov.is_answer_cell(clause) is False
    rows = ms.field_values(
        {"template_fit": {"params": {"min_rest_home": clause}}})
    assert rows[0].get("carrier_answer") is None
    assert ms.carriers(CLAUSES[("S1", "п.2")],
                       {"id": "R-R", "template_fit": {"params": {
                           PARAM: switched}}}) == []
    # Ячейка новой формы БЕЗ прежней строки `answer` — тоже ответная, и
    # имя ответа читается из точного происхождения.
    doc, cell = _answered()
    cell.pop("answer")
    assert prov.is_answer_cell(cell) is True
    assert prov.answer_marker(cell) == QID
    assert m.validate_fit_quotes(doc, CLAUSES) == []


def test_honest_clause_cell_patch_still_passes():
    """Положительный контроль: закон бьёт подмену ВИДА ячейки, а не
    работу обработчика — клаузную ячейку той же строки заплата правит
    свободно, ячейка из ответа при этом не тронута."""
    doc = _doc_with_open_question()
    ok = copy.deepcopy(_rule(doc))
    ok["template_fit"]["params"]["min_rest_home"] = {
        "value": 720, "quote": "Минимальный отдых дома — 12:00"}
    ok.pop("blockers", None)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [ok]))
    assert summary["worker_rejected"] == [] and summary["worker_applied"] == 1
    assert _cell(doc, name="min_rest_home")["quote"] == \
        "Минимальный отдых дома — 12:00"
    assert _cell(doc)["value"] == 600
    assert m.validate_fit_quotes(doc, CLAUSES) == []


# ── 15. самолечение идёт по ЦЕПОЧКЕ, а не по порядку списка ──────────

def test_self_healing_is_deterministic_on_a_damaged_document():
    """Действующий ответ выбирается по ЦЕПОЧКЕ пересмотров
    (`provenance.active_answer`), а не по порядку записей анкеты: у
    повреждённого документа с двумя «применён» в одной цепочке
    перестановка вопросов не меняет ни значения, ни происхождения
    ячейки. Порядок списка — не закон."""
    def healed(reverse):
        doc, _ = _answered()
        rep = ans.open_revision(doc, QID)
        ans.apply_answers(doc, {rep["created"]: RAW_540})
        _q(doc, QID)["answer"]["status"] = "применён"      # повреждение
        _rule(doc)["template_fit"]["params"].pop(PARAM)
        if reverse:
            doc["questions"].reverse()
        fp.ensure_family_param_questions(doc)
        return copy.deepcopy(_cell(doc))

    straight, reversed_ = healed(False), healed(True)
    assert straight == reversed_
    assert straight["source"]["question"]["id"] == QID   # корень в силе
    assert straight["value"] == 600


def test_self_healing_names_an_unreadable_answer_in_words():
    """ЧЕТВЁРТЫЙ выход петли самолечения — действующий ответ цепочки НЕ
    ЧИТАЕТСЯ грамматикой — молчал, хотя три соседних говорят словами:
    нет корня цепочки, нет действующего ответа, открыт пересмотр. Закон
    A03-мин требует сказать, ПОЧЕМУ значение не возвращено.

    Исход и прежде был верным (fail-closed: параметр недостающий,
    вопрос открыт, носитель заблокирован) — недоставало СЛОВА. Цена
    молчания: ответ дан, значение не село, причина не названа.

    Формулировка задана аудитором (GPT156 §5, подтверждена GPT170 п. 3)
    и здесь не придумана. Путь станет ЧАЩЕ: `got is None` рождает
    грамматика K02, а её круги сделали отказов больше.

    Проба доходит до САМОГО этого выхода: заметка ровно одна и ровно
    эта — значит её не подменил ни один соседний отказ."""
    doc, _ = _answered()
    _rule(doc)["template_fit"]["params"].pop(PARAM)
    _q(doc, QID)["answer"]["raw"] = "уточним после совещания"
    # Legacy grammar diagnostic; a forged modern parse has its own refusal.
    _q(doc, QID)["answer"].pop("parsed", None)
    _q(doc, QID)["answer"].pop("answer_version", None)

    notes: list[str] = []
    assert fp._reapply_values(doc, fp.family_coverage(doc), notes) == []

    assert len(notes) == 1, notes
    assert PARAM in notes[0] and QID in notes[0], notes[0]
    assert "не удалось разобрать" in notes[0], notes[0]
    assert "ячейка не записана" in notes[0], notes[0]
    # исход не изменился: ячейки нет, лечение ничего не подставило
    assert PARAM not in _rule(doc)["template_fit"]["params"]


def test_the_note_names_the_answer_in_force_not_the_root():
    """ВТОРАЯ ПОЛОВИНА заметки: в ссылке стоит ДЕЙСТВУЮЩИЙ ответ
    цепочки, а не её корень.

    Соседняя проба этого не различала (находка неавторского ревью
    13.09.2026): в её своде корень И действующий ответ — ОДИН И ТОТ ЖЕ
    вопрос, и мутация «поставить корень» проходила зелёной. Слово
    «действующий» взято у аудитора, но пробой закреплено не было.

    Здесь цепочка настоящая: корень отвечен и ПЕРЕСМОТРЕН, действующий
    ответ — у вопроса-пересмотра, и он не разбирается. Заметка обязана
    назвать ПЕРЕСМОТР, а не корень: авиакомпания правила последний
    ответ, и в причине должен стоять он."""
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)
    пересмотр = rep["created"]
    # Ответ на пересмотр ПРИМЕНЯЕТСЯ штатно — иначе проба не дойдёт до
    # своего выхода: неприменённый ответ отсекает более ранний рубеж
    # («ответ цепочки не применён»), и заметка была бы ЧУЖОЙ.
    ans.apply_answers(doc, {пересмотр: RAW_540})
    assert _q(doc, пересмотр)["answer"]["status"] == "применён"
    # …а его сырой текст портится, и разбор его больше не читает.
    _q(doc, пересмотр)["answer"]["raw"] = "уточним после совещания"
    _q(doc, пересмотр)["answer"].pop("parsed", None)
    _q(doc, пересмотр)["answer"].pop("answer_version", None)
    _rule(doc)["template_fit"]["params"].pop(PARAM, None)

    notes: list[str] = []
    assert fp._reapply_values(doc, fp.family_coverage(doc), notes) == []

    assert len(notes) == 1, notes
    assert "не удалось разобрать" in notes[0], notes[0]
    # Назван ПЕРЕСМОТР. Его идентификатор содержит корень как ПРЕФИКС,
    # поэтому «корня нет в тексте» утверждать нельзя — различает именно
    # присутствие ПОЛНОГО идентификатора пересмотра: мутация «поставить
    # корень» оставит в заметке только `QID` и эту проверку провалит.
    assert пересмотр in notes[0], (пересмотр, notes[0])
    assert пересмотр != QID and пересмотр.startswith(QID)


def test_a_readable_answer_still_lands_without_a_note():
    """ПОЛОЖИТЕЛЬНЫЙ контроль к нему: разбираемый ответ по-прежнему
    садится в ячейку и НЕ рождает заметки. Без него заметку можно было
    бы ставить всем подряд, и проба выше этого не заметила бы."""
    doc, _ = _answered()
    _rule(doc)["template_fit"]["params"].pop(PARAM)
    notes: list[str] = []
    assert fp._reapply_values(doc, fp.family_coverage(doc), notes) != []
    assert notes == [], notes
    assert _rule(doc)["template_fit"]["params"][PARAM]["value"] == 600


def test_self_healing_does_not_guess_without_an_answer_in_force():
    """Действующего ответа у цепочки нет (пересмотр открыт и не
    отвечен) — самолечение НЕ пишет ячейку и НЕ молчит: причина
    словами, параметр остаётся недостающим, вопрос открыт, носители
    заблокированы. Прежде брался «последний применённый по списку», и
    случайный применённый ответ цепочки обосновывал бы значение."""
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)          # корень «пересмотрен»
    stray = copy.deepcopy(_q(doc, rep["created"]))
    stray["id"] = rep["created"] + "-2"        # звено вне цепочки ссылок
    stray["answer"] = {"raw": RAW_540, "status": "применён", "revision": 3}
    doc["questions"].append(stray)
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    notes: list[str] = []
    assert fp._reapply_values(doc, fp.family_coverage(doc), notes) == []
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    assert notes and PARAM in notes[0] and "а его ещё нет" in notes[0]
    # Видимый исход: вопрос-пересмотр открыт, носитель заблокирован.
    fp.ensure_family_param_questions(doc)
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    assert rep["created"] in (_rule(doc).get("blockers") or [])


# ── 16. ячейка ответа не ИСЧЕЗАЕТ заплатой (находки третьего ревью) ──

def _worker_reply_many(patches_by_qid):
    """Ответ обработчика с заплатами по НЕСКОЛЬКИМ вопросам в ОДНОМ
    вызове — тот самый вход, на котором метку снимали в два шага."""
    return json.dumps({"patches": [
        {"question": qid, "change": "ответ внесён", "rules": rules}
        for qid, rules in patches_by_qid.items()]}, ensure_ascii=False)


def _doc_with_two_open_questions():
    """Свод с применённым значением параметра и ДВУМЯ открытыми
    вопросами по той же строке: обе заплаты приходят одним вызовом."""
    doc = _doc_with_open_question()
    doc["questions"].append(
        {"id": "Q-3b-уточнение-2", "part": "3b", "title": "Уточнение 2",
         "text": "И ещё что-нибудь меняется?",
         "answer_format": {"type": "value",
                           "note": "минуты либо «без изменений»"},
         "rule_refs": ["R-R"],
         "source_refs": [{"sid": "S1", "anchor": "п.2"}]})
    _rule(doc)["blockers"] = ["Q-3b-уточнение", "Q-3b-уточнение-2"]
    assert ans.apply_answers(doc, {"Q-3b-уточнение-2": "Без изменений."}
                             )["awaiting"] == 1
    return doc


def _exchange_round_two(doc, rule1, rule2, klass="искажение"):
    """Круг обмена с ДВУМЯ принятыми правками одной строки в одном
    ответе обработчика — вход «два шага в одном вызове» на пути
    обмена."""
    pack = {"round": 1, "remarks": [
        {"id": f"A-00{i}", "severity": "КРИТИЧЕСКОЕ",
         "defect_class": klass, "target": {"rule": "R-R"},
         "source_refs": [{"sid": "S1", "anchor": "п.2"}],
         "text": "Пол отдыха прочитан неверно",
         "evidence": "«Минимальный отдых дома — 12:00»",
         "proposal": "исправить"} for i in (1, 2)]}
    resp = {"round": 1, "answers": [
        {"remark": f"A-00{i}", "verdict": "ПРИНЯТО",
         "basis": "источник: «12:00»", "change": "строка переписана",
         "patch": {"rule_id": "R-R", "rule": r}}
        for i, r in ((1, rule1), (2, rule2))]}
    return ex._apply_patches(doc, pack, resp, 1, clause_texts=CLAUSES)


def test_patch_may_not_drop_an_answer_cell_at_all():
    """Дверь «снятие ячейки» (находка третьего ревью): заплата не
    переписывает ячейку, внесённую ответом, а ВЫБРАСЫВАЕТ её из посадки
    (`params` без неё). Прежний обход этого не видел вовсе — он шёл по
    ячейкам НОВОЙ строки, а пропавшей ячейки там нет, — и заплата-выброс
    принималась МОЛЧА: значение авиакомпании исчезало из свода без
    единого слова. Снять такую ячейку может только КОД."""
    doc = _doc_with_open_question()
    drop = copy.deepcopy(_rule(doc))
    drop.pop("blockers", None)
    drop["template_fit"]["params"].pop(PARAM)
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [drop]))
    assert summary["worker_applied"] == 0
    assert any("снята заплатой" in r
               for r in summary["worker_rejected"][0]["reasons"])
    assert _cell(doc)["value"] == 600 and _cell(doc).get("source") is not None
    assert _q(doc, "Q-3b-уточнение")["answer"]["status"] == \
        "ожидает обработчика"
    # Тот же расчёт на приёме заплаты ОБМЕНА (общий закон П0 № 4).
    doc, _ = _answered()
    drop2 = copy.deepcopy(_rule(doc))
    drop2["template_fit"]["params"].pop(PARAM)
    new_doc, _acc, rejected, _tok = _exchange_round(copy.deepcopy(doc), drop2)
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "снята заплатой" in reasons or "потеря параметров" in reasons
    assert _cell(new_doc)["value"] == 600
    # Закон живёт одной функцией и называет случай словами.
    assert any("снять её может только код" in p
               for p in prov.patch_problems(_rule(doc), drop2))


def test_answer_marker_may_not_be_removed_in_two_patches_of_one_call():
    """Два шага, один вызов: заплата по первому вопросу ВЫБРАСЫВАЕТ
    ячейку ответа, заплата по второму заводит её заново клаузной парой
    `{value, quote}` с подлинной цитатой клаузы и выдуманным числом. По
    отдельности ни один шаг прежде не бракевался: первый — потому что
    пропажу никто не судил, второй — потому что в ПРЕДЫДУЩЕЙ (уже
    исправленной первым шагом) строке ячейки ответа нет, и «снятия
    метки» не видно. Итог — 2 применено, 0 отброшено, выпуск зелёный,
    в выпущенном файле 660."""
    doc = _doc_with_two_open_questions()
    drop = copy.deepcopy(_rule(doc))
    drop.pop("blockers", None)
    drop["template_fit"]["params"].pop(PARAM)
    readd = copy.deepcopy(drop)
    readd["template_fit"]["params"][PARAM] = {"value": 660,
                                              "quote": "дома — 12:00"}
    summary = ans.apply_worker_patches(doc, lambda p: _worker_reply_many(
        {"Q-3b-уточнение": [drop], "Q-3b-уточнение-2": [readd]}))
    assert summary["worker_applied"] == 0
    reasons = "; ".join(r for rj in summary["worker_rejected"]
                        for r in rj.get("reasons", []))
    assert "снята заплатой" in reasons          # первый шаг
    assert "снята метка ответа" in reasons      # второй шаг
    assert _cell(doc)["value"] == 600 and _cell(doc).get("source") is not None
    assert prov.package_problems(doc) == []
    assert m.validate_fit_quotes(doc, CLAUSES) == []


def test_two_step_removal_is_refused_on_a_dirty_quote_layer_too():
    """Тот же обход на приёме заплаты ОБМЕНА при ГРЯЗНОМ цитатном слое:
    у соседней КЛАУЗНОЙ ячейки цитата не дословна, поэтому замок 4П
    («потеря параметров посадки») выключен — он судит только строку с
    чистым слоем. Закон происхождения от чистоты чужих цитат не зависит:
    выброс ячейки ответа бракуется и здесь, иначе подлог проходил бы
    ровно у строки, где проверять труднее всего."""
    doc, _ = _answered()
    _rule(doc)["template_fit"]["params"]["min_rest_home"]["quote"] = \
        "дома — 13:00"                          # грязь в цитатном слое
    drop = copy.deepcopy(_rule(doc))
    drop["template_fit"]["params"].pop(PARAM)
    drop["template_fit"]["params"]["min_rest_home"]["quote"] = "дома — 12:00"
    readd = copy.deepcopy(drop)
    readd["template_fit"]["params"][PARAM] = {"value": 660,
                                              "quote": "дома — 12:00"}
    # Замок 4П на этой строке молчит — судит один лишь закон ячейки.
    assert ex.fit_integrity_problems(_rule(doc), drop, CLAUSES, doc) == []
    new_doc, _acc, rejected, _tok = _exchange_round_two(
        copy.deepcopy(doc), drop, readd)
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "снята заплатой" in reasons
    assert "снята метка ответа" in reasons
    assert _cell(new_doc)["value"] == 600
    assert _cell(new_doc).get("source") is not None


def test_dropping_an_answer_cell_is_refused_even_on_a_re_landing():
    """Закон БЕЗУСЛОВЕН: посадка строки на другую семью не оправдывает
    пропажу ячейки прежней семьи. Заплата вправе пересадить строку, но
    не вправе этим стереть ответ авиакомпании: сначала ответ снимает
    код (пересмотр либо решение «не применяется»), потом строка
    пересаживается. Иначе «пересадка» была бы третьей дверью к тому же:
    значение исчезает, вопрос остаётся применённым, спросить некого."""
    traits = m.template_family_traits()
    other = next(f for f in sorted(traits)
                 if f != FAMILY and (traits[f] or {}).get("kind") == "HARD")
    doc = _doc_with_open_question()
    resit = copy.deepcopy(_rule(doc))
    resit.pop("blockers", None)
    resit["template_fit"] = {"category": "A", "registry_rev": REV,
                             "family": other, "params": {}}
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [resit]))
    assert summary["worker_applied"] == 0
    assert any("снята заплатой" in r
               for r in summary["worker_rejected"][0]["reasons"])
    assert _rule(doc)["template_fit"]["family"] == FAMILY
    assert _cell(doc)["value"] == 600
    # Положительный контроль: путь КОДА ячейку снимает — закон не тупик.
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    assert ans.apply_answers(doc, {rep["created"]: "не применяется"}
                             )["applied"] == 1
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    assert prov.package_problems(doc) == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_a_clause_cell_may_still_be_dropped_by_a_patch():
    """Положительный контроль закона: бьёт он ячейки ОТВЕТА, а не всякую
    правку посадки. Клаузную ячейку той же строки заплата убирает
    свободно (её судит цитатный слой клауз и замок 4П, а не
    происхождение), а ячейка из ответа при этом цела."""
    doc = _doc_with_open_question()
    ok = copy.deepcopy(_rule(doc))
    ok.pop("blockers", None)
    ok["template_fit"]["params"].pop("min_rest_home")
    assert prov.patch_problems(_rule(doc), ok) == []
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [ok]))
    assert summary["worker_rejected"] == [] and summary["worker_applied"] == 1
    assert "min_rest_home" not in _rule(doc)["template_fit"]["params"]
    assert _cell(doc)["value"] == 600 and _cell(doc).get("source") is not None
    assert prov.package_problems(doc) == []


def _exchange_round_kind(doc, patch_obj, klass="изобретение"):
    """Круг обмена с ПРИНЯТЫМ замечанием, чья правка снимает строку
    (`remove`) либо меняет её вид: у такой строки посадку снимает КОД."""
    pack = {"round": 1, "remarks": [
        {"id": "A-001", "severity": "КРИТИЧЕСКОЕ", "defect_class": klass,
         "target": {"rule": "R-R"},
         "source_refs": [{"sid": "S1", "anchor": "п.2"}],
         "text": "Строка лишняя", "evidence": "«Минимальный отдых дома»",
         "proposal": "снять"}]}
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО",
         "basis": "источник: «12:00» — принято",
         "change": "строка снята", "patch": patch_obj}]}
    return ex._apply_patches(doc, pack, resp, 1, clause_texts=CLAUSES)


def _kind_patch_round(doc, kp):
    """Круг обмена ЧЕТВЁРТОЙ формой заплат — заплатой ВИДА
    (`kind_patch`): миграция вида существующей строки. Прежде эта форма
    закон происхождения не звала вовсе, и «снятие в два хода» шло
    через неё (находка четвёртого ревью)."""
    pack = {"round": 1, "remarks": [
        {"id": "A-001", "severity": "КРИТИЧЕСКОЕ", "defect_class": "вид",
         "target": {"rule": kp.get("rule_id")},
         "source_refs": [{"sid": "S1", "anchor": "п.2"}],
         "text": "Вид строки прочитан неверно",
         "evidence": "«Минимальный отдых дома — 12:00»",
         "proposal": "сменить вид"}]}
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО",
         "basis": "источник: «12:00» — принято",
         "change": "вид строки мигрирован", "kind_patch": kp}]}
    return ex._apply_patches(doc, pack, resp, 1, clause_texts=CLAUSES)


def test_code_may_still_unland_a_row_that_stops_being_machine():
    """Закон судит заплату КАК ПОДАНА, а не после работы кода. Посадку
    у строки, переставшей быть машинной (снятие лишней строки в OUT,
    смена вида по перечню миграций), снимает `align_fit_with_kind` —
    это ход КОДА, и приписывать его заплате нельзя. Иначе один и тот же
    законный ход выходит разным у двух приёмников: суд, стоявший на
    пути обмена ПОСЛЕ ярлыков кода, бракевал снятие строки и смену вида
    полной заплатой, а путь обработчика те же ходы принимал (общий
    закон П0 № 4: расчёт один).

    ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ идёт по строке БЕЗ ячеек ответа: выравнивать
    приёмников надо в сторону ОТКАЗА, а не разрешения (находка
    четвёртого ревью) — строку, чья посадка НЕСЁТ ответ авиакомпании,
    заплата не снимает и не переводит в немашинный вид (см. соседние
    тесты). Здесь ячейку уже снял КОД (пересмотр + «не применяется»),
    и дальше и снятие строки, и смена вида идут свободно: закон бьёт
    подлог, а не работу аудитора."""
    base = _doc_after_the_code_unlanded_the_cell()
    new_doc, _acc, rejected, _tok = _exchange_round_kind(
        copy.deepcopy(base), {"rule_id": "R-R", "rule": {"remove": True}})
    assert rejected == []
    assert _rule(new_doc)["kind"] == "OUT"
    assert "template_fit" not in _rule(new_doc)
    # Смена вида полной заплатой — тот же ход кода.
    as_data = copy.deepcopy(_rule(base))
    as_data["kind"] = "DATA"
    new_doc, _acc, rejected, _tok = _exchange_round_kind(
        copy.deepcopy(base), {"rule_id": "R-R", "rule": as_data}, klass="вид")
    assert rejected == []
    assert _rule(new_doc)["kind"] == "DATA"
    assert "template_fit" not in _rule(new_doc)
    # Заплата ВИДА (четвёртая форма) — тем же исходом.
    new_doc, _acc, rejected, _tok = _kind_patch_round(
        copy.deepcopy(base), {"rule_id": "R-R", "kind": "DATA"})
    assert rejected == []
    assert _rule(new_doc)["kind"] == "DATA"
    # И тот же ход на пути обработчика.
    doc = _doc_with_open_question(_doc_after_the_code_unlanded_the_cell())
    as_data = copy.deepcopy(_rule(doc))
    as_data.pop("blockers", None)
    as_data["kind"] = "DATA"
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-уточнение", [as_data]))
    assert summary["worker_rejected"] == [] and summary["worker_applied"] == 1
    assert _rule(doc)["kind"] == "DATA"
    assert "template_fit" not in _rule(doc)
    assert prov.package_problems(doc) == []


# ── 16б. дверь «снятие в два хода»: OUT и смена вида (ревью 4) ───────

def test_a_row_with_answer_cells_may_not_be_removed_by_a_patch():
    """Дверь «снятие в два хода» через OUT (находка четвёртого ревью).
    Ход 1 — заплата `{"remove": true}`: строка уходит в OUT, а посадку
    у немашинного вида тут же снимает КОД, вместе с ячейкой ответа.
    Ход 2 — полная заплата OUT→HARD с той же посадкой и клаузной парой
    `{660, «дома — 12:00»}` на месте ответа: у ПРЕЖНЕЙ строки посадки
    уже нет, судить пропажу не на чем, и «снятие метки» невидимо. Итог
    прежде — применено, выпуск зелёный, в файле 660, а вопрос стоит
    «применён»: значение авиакомпании исчезло МОЛЧА.

    Строку, чья посадка несёт ячейки ответа, снимает только КОД —
    пересмотр (-R) либо решение «не применяется»."""
    doc, _ = _answered()
    new_doc, _acc, rejected, _tok = _exchange_round_kind(
        copy.deepcopy(doc), {"rule_id": "R-R", "rule": {"remove": True}})
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "снимается или меняет вид только после пересмотра" in reasons
    assert _rule(new_doc)["kind"] == "HARD"
    assert _cell(new_doc)["value"] == 600
    assert _cell(new_doc).get("source") is not None
    # Оба хода ОДНИМ вызовом — ни один не проходит.
    revive = copy.deepcopy(_rule(doc))
    revive["template_fit"]["params"][PARAM] = {"value": 660,
                                               "quote": "дома — 12:00"}
    new_doc, _acc, rejected, _tok = _exchange_round_two(
        copy.deepcopy(doc), {"remove": True}, revive, klass="изобретение")
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "снимается или меняет вид только после пересмотра" in reasons
    assert "снята метка ответа" in reasons          # второй ход
    assert _cell(new_doc)["value"] == 600
    assert prov.package_problems(new_doc) == []
    assert m.validate_fit_quotes(new_doc, CLAUSES) == []
    # Закон живёт ОДНОЙ функцией и называет случай словами.
    out_row = copy.deepcopy(_rule(doc))
    out_row["kind"] = "OUT"
    assert any("снимается или меняет вид только после пересмотра" in p
               for p in prov.patch_problems(_rule(doc), out_row))


def test_the_row_may_not_go_to_out_on_the_worker_path_either():
    """Та же дверь на пути ОБРАБОТЧИКА (общий закон П0 № 4: расчёт
    один): заплата по первому вопросу уводит строку в вид OUT, заплата
    по второму возвращает её машинной с клаузной парой `{660, «дома —
    12:00»}`. Проверяются оба входа — два вопроса в ОДНОМ вызове и два
    вызова подряд."""
    doc = _doc_with_two_open_questions()
    as_out = copy.deepcopy(_rule(doc))
    as_out.pop("blockers", None)
    as_out["kind"] = "OUT"
    revive = copy.deepcopy(_rule(doc))
    revive.pop("blockers", None)
    revive["template_fit"]["params"][PARAM] = {"value": 660,
                                               "quote": "дома — 12:00"}
    one = copy.deepcopy(doc)
    summary = ans.apply_worker_patches(one, lambda p: _worker_reply_many(
        {"Q-3b-уточнение": [as_out], "Q-3b-уточнение-2": [revive]}))
    assert summary["worker_applied"] == 0
    reasons = "; ".join(r for rj in summary["worker_rejected"]
                        for r in rj.get("reasons", []))
    assert "снимается или меняет вид только после пересмотра" in reasons
    assert "снята метка ответа" in reasons
    assert _rule(one)["kind"] == "HARD" and _cell(one)["value"] == 600
    # Два вызова подряд — тем же исходом (первый ход не проходит, и
    # второму не на чем стоять).
    two = copy.deepcopy(doc)
    s1 = ans.apply_worker_patches(
        two, lambda p: _worker_reply("Q-3b-уточнение", [as_out]))
    s2 = ans.apply_worker_patches(
        two, lambda p: _worker_reply("Q-3b-уточнение-2", [revive]))
    assert s1["worker_applied"] == 0 and s2["worker_applied"] == 0
    assert _rule(two)["kind"] == "HARD"
    assert _cell(two)["value"] == 600 and _cell(two).get("source") is not None
    assert prov.package_problems(two) == []
    assert m.validate_fit_quotes(two, CLAUSES) == []


def test_a_row_with_answer_cells_may_not_change_kind_by_a_patch():
    """Та же дверь СМЕНОЙ ВИДА (HARD→DATA по перечню миграций): вид
    немашинный — посадку снимает код, ячейка ответа исчезает, и
    следующая заплата возвращает строку машинной с клаузной парой.
    Судится во всех трёх формах заплаты: полной на пути обмена,
    четвёртой формой (`kind_patch`, прежде закон происхождения не
    звавшей ВОВСЕ) и на пути обработчика — одним вызовом и двумя."""
    doc, _ = _answered()
    as_data = copy.deepcopy(_rule(doc))
    as_data["kind"] = "DATA"
    revive = copy.deepcopy(_rule(doc))
    revive["template_fit"]["params"][PARAM] = {"value": 660,
                                               "quote": "дома — 12:00"}
    new_doc, _acc, rejected, _tok = _exchange_round_kind(
        copy.deepcopy(doc), {"rule_id": "R-R", "rule": as_data}, klass="вид")
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "снимается или меняет вид только после пересмотра" in reasons
    assert _rule(new_doc)["kind"] == "HARD" and _cell(new_doc)["value"] == 600
    # Четвёртая форма заплат — заплата ВИДА.
    new_doc, _acc, rejected, _tok = _kind_patch_round(
        copy.deepcopy(doc), {"rule_id": "R-R", "kind": "DATA"})
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "снимается или меняет вид только после пересмотра" in reasons
    assert _rule(new_doc)["kind"] == "HARD"
    assert _cell(new_doc).get("source") is not None
    # Оба хода одним вызовом обмена.
    new_doc, _acc, rejected, _tok = _exchange_round_two(
        copy.deepcopy(doc), as_data, revive, klass="вид")
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "снимается или меняет вид только после пересмотра" in reasons
    assert "снята метка ответа" in reasons
    assert _cell(new_doc)["value"] == 600
    # Путь обработчика: Q1 → DATA, Q2 → HARD с клаузной парой.
    doc = _doc_with_two_open_questions()
    as_data = copy.deepcopy(_rule(doc))
    as_data.pop("blockers", None)
    as_data["kind"] = "DATA"
    revive = copy.deepcopy(_rule(doc))
    revive.pop("blockers", None)
    revive["template_fit"]["params"][PARAM] = {"value": 660,
                                               "quote": "дома — 12:00"}
    one = copy.deepcopy(doc)
    summary = ans.apply_worker_patches(one, lambda p: _worker_reply_many(
        {"Q-3b-уточнение": [as_data], "Q-3b-уточнение-2": [revive]}))
    assert summary["worker_applied"] == 0
    reasons = "; ".join(r for rj in summary["worker_rejected"]
                        for r in rj.get("reasons", []))
    assert "снимается или меняет вид только после пересмотра" in reasons
    assert "снята метка ответа" in reasons
    assert _rule(one)["kind"] == "HARD" and _cell(one)["value"] == 600
    two = copy.deepcopy(doc)
    s1 = ans.apply_worker_patches(
        two, lambda p: _worker_reply("Q-3b-уточнение", [as_data]))
    s2 = ans.apply_worker_patches(
        two, lambda p: _worker_reply("Q-3b-уточнение-2", [revive]))
    assert s1["worker_applied"] == 0 and s2["worker_applied"] == 0
    assert _cell(two)["value"] == 600 and _cell(two).get("source") is not None
    assert prov.package_problems(two) == []


def test_the_code_path_out_of_the_law_is_not_a_dead_end():
    """Закон не тупик, и это его условие: строку, чью посадку держит
    ответ авиакомпании, ОСВОБОЖДАЕТ код. Пересмотр `-R` заводится и на
    такой строке (блокируя её), ответ «не применяется» ячейку снимает,
    и дальше строка снимается заплатой как всякая другая. Один и тот же
    ход — после кода законен, вместо кода отказан."""
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)
    assert rep["created"] in (_rule(doc).get("blockers") or [])
    assert ans.apply_answers(doc, {rep["created"]: "не применяется"}
                             )["applied"] == 1
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    assert prov.package_problems(doc) == []
    # Пересмотр вопроса, чья строка УЖЕ снята кодом в OUT, — тоже не
    # тупик: `-R` заводится, «не применяется» применяется, свод цел.
    gone, _ = _answered()
    r = _rule(gone)
    r["kind"] = "OUT"
    r.pop("template_fit")
    rep = ans.open_revision(gone, QID)
    assert ans.apply_answers(gone, {rep["created"]: "не применяется"}
                             )["applied"] == 1
    assert m.validate_model(gone) == []
    # А после освобождения строка снимается обычной заплатой обмена.
    new_doc, _acc, rejected, _tok = _exchange_round_kind(
        copy.deepcopy(doc), {"rule_id": "R-R", "rule": {"remove": True}})
    assert rejected == []
    assert _rule(new_doc)["kind"] == "OUT"


# ── 17. служебный след кода адресата не создаёт (находка ревью 3) ────

def test_a_journal_entry_about_blockers_is_not_an_addressee():
    """Плечо ЖУРНАЛА СЛЕДСТВИЙ возвращало адресата, которого закон уже
    отверг как зависящего от момента: вопрос называет одну строку, а
    БЛОКИРУЕТ другую; применение ответа блокировку снимает, журнал
    получает запись `(другая строка, blockers)` — и вопрос «адресует»
    чужую строку. Блокировку ставит и снимает КОД: запись о поле
    `blockers` говорит лишь, что строка ЖДАЛА ответа, а не что ответ о
    ней. Из журнала берутся следствия СОДЕРЖАНИЯ ответа."""
    doc = _doc()
    fp.ensure_family_param_questions(doc)
    ans.apply_answers(doc, {QID: "не применяется"})
    doc["questions"].append(
        {"id": "Q-3b-W", "part": "3b", "title": "Пол отдыха вне базы",
         "text": "Сколько минут отдыха вне базы?",
         "answer_format": {"type": "value", "note": "минуты"},
         "rule_refs": ["R-H"],                   # называет ЖЁСТКУЮ строку
         "source_refs": [{"sid": "S1", "anchor": "п.1"}]})
    _rule(doc)["blockers"] = ["Q-3b-W"]          # а блокирует носителя
    assert m.validate_model(doc) == []
    assert ans.apply_answers(doc, {"Q-3b-W": "10 часов"})["awaiting"] == 1
    unblock = copy.deepcopy(_rule(doc))
    unblock.pop("blockers", None)                # заплата СНИМАЕТ блокировку
    summary = ans.apply_worker_patches(
        doc, lambda p: _worker_reply("Q-3b-W",
                                     [copy.deepcopy(_rule(doc, "R-H")),
                                      unblock]))
    assert summary["worker_rejected"] == [] and summary["worker_applied"] == 1
    journal = [(e.get("rule"), e.get("field"))
               for e in _q(doc, "Q-3b-W")["answer"].get("effects") or []]
    assert ("R-R", "blockers") in journal
    assert prov._target_problems(doc, _q(doc, "Q-3b-W"), "R-R",
                                 FAMILY, PARAM) != []
    # Ячейка от этого вопроса на этой строке не проходит приёмом обмена.
    cell = {"value": 600, "quote": "10 часов", "answer": "Q-3b-W",
            "source": prov.answer_source(_q(doc, "Q-3b-W"), PARAM,
                                         "10 часов")}
    add = copy.deepcopy(_rule(doc))
    add["template_fit"]["params"][PARAM] = cell
    new_doc, _acc, rejected, _tok = _exchange_round(copy.deepcopy(doc), add)
    reasons = "; ".join(r for rj in rejected for r in rj.get("reasons", []))
    assert "адресует строки R-H, а не R-R" in reasons
    assert PARAM not in _rule(new_doc)["template_fit"]["params"]
    # Контроль: НАСТОЯЩЕЕ следствие по содержанию ответа адресата даёт.
    live = copy.deepcopy(doc)
    _q(live, "Q-3b-W")["answer"]["effects"].append(
        {"rule": "R-R", "field": "statement",
         "before": "было", "after": "стало"})
    assert prov._target_problems(live, _q(live, "Q-3b-W"), "R-R",
                                 FAMILY, PARAM) == []
    _rule(live)["template_fit"]["params"][PARAM] = copy.deepcopy(cell)
    assert prov.package_problems(live) == []


# ── 18. заметка самолечения доходит до зовущего (находка ревью 3) ────

def test_self_healing_note_reaches_the_caller_of_the_production_call():
    """Слова самолечения не теряются на БОЕВОМ зове: постановка вопросов
    принимает список `notes` зовущего и кладёт туда причину, по которой
    значение в посадку не возвращено. Полей документа заметка не
    получает — схема их не знает, а выдумывать поле ради заметки нельзя
    (сток заметок кода в интерфейсе — волна 17б). Молчания в СВОДЕ нет
    и без стока: вопрос остаётся открытым, носитель заблокированным."""
    doc, _ = _answered()
    rep = ans.open_revision(doc, QID)            # корень «пересмотрен»
    stray = copy.deepcopy(_q(doc, rep["created"]))
    stray["id"] = rep["created"] + "-2"          # звено вне цепочки ссылок
    stray["answer"] = {"raw": RAW_540, "status": "применён", "revision": 3}
    doc["questions"].append(stray)
    notes: list[str] = []
    assert fp.ensure_family_param_questions(doc, notes) == []
    assert notes and PARAM in notes[0] and "не возвращено" in notes[0]
    assert "не возвращено" not in json.dumps(doc, ensure_ascii=False)
    assert PARAM not in _rule(doc)["template_fit"]["params"]
    assert rep["created"] in (_rule(doc).get("blockers") or [])
    # Зов без `notes` законен и ничего не роняет (боевые зовы — такие).
    assert fp.ensure_family_param_questions(copy.deepcopy(doc)) == []


# ── 19. передача не обещает того, чего в дельте нет ──────────────────

def test_handoff_prompts_do_not_ask_models_to_re_land_answer_cells():
    """Ячейки из ответов хранит и судит КОД, а не модель: обещать
    модели право переписывать их — обещать то, чего дельта не даёт
    (заплата такую ячейку ни завести, ни изменить, ни снять не вправе).
    Строка живёт в передачах обоих путей — обработчика и обмена."""
    line = ("посадку строки с ячейками из ответов не повторяй — "
            "такие ячейки хранит и судит код")
    for text in (ans._PROMPT_WORKER, ex._PROMPT_RESPOND, ex._PROMPT_ENFORCE):
        assert line in " ".join(text.split())


def test_refusal_names_the_marker_as_it_stands_in_the_cell():
    """Косметика отказа: слова называют ТО значение метки, которое в
    ячейке стоит. Прежде приведение `str(... or "")` превращало метку
    `0` в пустую строку, и отказ говорил «помечен ответом ''» — читатель
    искал в анкете не то, что написано в своде."""
    doc, cell = _answered()
    cell.pop("source")
    cell["answer"] = 0                           # схема такую ячейку краснит
    assert m.validate_schema(doc) != []
    assert any("помечен ответом '0'" in p for p in prov.package_problems(doc))
    assert prov.answer_marker(cell) == "0"
