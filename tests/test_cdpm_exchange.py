# -*- coding: utf-8 -*-
"""Контракты аудит-цикла CDPM (АЦ-1): замечания, ответы, реестр находок,
метрика, останов, diff-only, регресс-токены.

Проверяется по классам золотых правил пилотной линии: замечание без
адреса и цитаты не существует; ни одно замечание без ответа; принятие
с основанием (защита от уступчивости); находку закрывает аудитор;
отклонено и повторено — тупик владельцу; правка вне диффа и откат
старых исправлений ловятся машинно."""
import copy

import pytest

from cdpm import exchange as ex
from cdpm.sample import sample_model


def _remark(**over):
    rm = {"id": "A-001", "severity": "КРИТИЧЕСКОЕ",
          "defect_class": "искажение",
          "target": {"rule": "R-S1-П-1-1"},
          "source_refs": [{"sid": "S1", "anchor": "П.1.1"}],
          "text": "Единица «дня» подменена сутками",
          "evidence": "«не длиннее четырёх дней» — единица не задана",
          "proposal": "вернуть развилку"}
    rm.update(over)
    return rm


def _pack(*remarks):
    return {"round": 1, "remarks": list(remarks)}


def _answers(pack, verdict="ПРИНЯТО"):
    return {"round": 1, "answers": [
        {"remark": rm["id"], "verdict": verdict,
         "basis": "источник: «четырёх дней» — принято",
         "change": "строка переписана" if verdict != "ОТКЛОНЕНО"
         else None}
        for rm in pack["remarks"]]}


# --------------------------------------------------------- замечания

def test_clean_remark_pack_passes():
    assert ex.validate_remarks(_pack(_remark()), sample_model()) == []


@pytest.mark.parametrize("mutate,expected", [
    (lambda rm: rm.update(severity="УЖАСНОЕ"), "вне перечня"),
    (lambda rm: rm.update(defect_class="прочее"), "вне перечня"),
    (lambda rm: rm.update(target={"rule": "R-НЕТ"}), "несуществующую"),
    (lambda rm: rm.update(target={}), "только для класса «пропуск»"),
    (lambda rm: rm.update(source_refs=[]), "без трассировки"),
    (lambda rm: rm.update(source_refs=[{"sid": "S9", "anchor": "х"}]),
     "неизвестный источник"),
    (lambda rm: rm.update(text=" "), "пустая суть"),
    (lambda rm: rm.update(evidence=""), "без доказательства"),
], ids=["серьёзность", "класс", "чужая строка", "без адреса",
        "без трассировки", "чужой источник", "пустая суть",
        "без цитаты"])
def test_remark_violations_are_caught(mutate, expected):
    rm = _remark()
    mutate(rm)
    problems = ex.validate_remarks(_pack(rm), sample_model())
    assert any(expected in p for p in problems), problems


def test_omission_remark_may_have_no_target():
    rm = _remark(defect_class="пропуск", target={})
    assert ex.validate_remarks(_pack(rm), sample_model()) == []


def test_duplicate_remark_id_is_caught():
    problems = ex.validate_remarks(
        _pack(_remark(), _remark()), sample_model())
    assert any("повторный идентификатор" in p for p in problems)


def test_empty_remark_id_is_a_problem():
    """Ревью блока неизменности id (1.09): пустой id проходил
    валидатор, но выпадал из перечня допустимых номеров порции
    обработчика — validate_response требовал ответ на номер,
    которого «не существует». Пустой id режется у истока."""
    for empty in ("", "  "):
        problems = ex.validate_remarks(_pack(_remark(id=empty)),
                                       sample_model())
        assert any("пустой id" in p for p in problems), problems


def test_nonstring_ids_are_problems_not_crashes():
    """Живой урок UniA run-0051: аудитор вернул словарь вместо
    строкового id — валидация обязана отбраковать проблемой гейта,
    а не упасть на хэшировании (unhashable dict)."""
    rm = _remark(id={"rule": "R-001"})
    problems = ex.validate_remarks(_pack(rm), sample_model())
    assert any("id не строка" in p for p in problems), problems

    rm = _remark(target={"rule": {"id": "R-001"}})
    problems = ex.validate_remarks(_pack(rm), sample_model())
    assert any("адрес строки не строка" in p for p in problems), problems

    rm = _remark(target={"question": ["Q-1"]})
    problems = ex.validate_remarks(_pack(rm), sample_model())
    assert any("адрес вопроса не строка" in p for p in problems), \
        problems

    rm = _remark(source_refs=[{"sid": {"s": "S1"}, "anchor": "П.1.1"}])
    problems = ex.validate_remarks(_pack(rm), sample_model())
    assert any("нестроковым sid" in p for p in problems), problems

    pack = _pack(_remark(), _remark(id={"x": 1}))
    ans = {"round": 1, "answers": [
        {"remark": {"x": 1}, "verdict": "ПРИНЯТО",
         "basis": "б", "change": "в"}]}
    problems = ex.validate_response(ans, pack)
    assert any("не строка" in p for p in problems), problems


# ------------------------------------------------------------- ответы

def test_full_response_passes_and_gaps_are_caught():
    pack = _pack(_remark(), _remark(id="A-002"))
    assert ex.validate_response(_answers(pack), pack) == []
    short = {"round": 1, "answers": _answers(pack)["answers"][:1]}
    problems = ex.validate_response(short, pack)
    assert any("без ответа" in p for p in problems)


@pytest.mark.parametrize("mutate,expected", [
    (lambda a: a.update(verdict="МОЖЕТ БЫТЬ"), "вне перечня"),
    (lambda a: a.update(basis=""), "без основания"),
    (lambda a: a.update(change=None), "без описания изменения"),
], ids=["вердикт", "основание", "изменение"])
def test_response_violations_are_caught(mutate, expected):
    pack = _pack(_remark())
    resp = _answers(pack)
    mutate(resp["answers"][0])
    problems = ex.validate_response(resp, pack)
    assert any(expected in p for p in problems), problems


def test_rejection_needs_basis_but_not_change():
    pack = _pack(_remark())
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ОТКЛОНЕНО",
         "basis": "источник говорит «дней», замечание неверно"}]}
    assert ex.validate_response(resp, pack) == []


# ------------------------------------------------- метрика и останов

def test_metrics_count_by_severity():
    pack = _pack(_remark(severity="БЛОКИРУЮЩЕЕ"),
                 _remark(id="A-2", severity="СЕРЬЁЗНОЕ"),
                 _remark(id="A-3", severity="СЕРЬЁЗНОЕ"),
                 _remark(id="A-4", severity="РЕДАКЦИОННОЕ"))
    m = ex.metrics_of(pack)
    assert (m["B"], m["K"], m["S"], m["E"]) == (1, 0, 2, 1)
    assert m["weighty"] == (1, 0, 2)


def test_decide_success_needs_two_clean_audits_in_a_row():
    clean = {"weighty": (0, 0, 0)}
    dirty = {"weighty": (0, 1, 0)}
    assert ex.decide([dirty, clean], 10, {}) == "продолжать"
    assert ex.decide([dirty, clean, clean], 10, {}) == "успех"
    # Редакционные не мешают успеху: weighty уже без них.
    assert ex.decide([clean, clean], 10, {}) == "успех"


def test_decide_limit_and_stall():
    dirty = {"weighty": (0, 2, 1)}
    worse = {"weighty": (1, 2, 1)}
    better = {"weighty": (0, 1, 0)}
    assert ex.decide([dirty, dirty, dirty], 3, {}) == "лимит"
    # Два обмена подряд без улучшения — стагнация.
    assert ex.decide([better, dirty, worse], 10, {}) == "стагнация"
    # Улучшение сбрасывает счёт стагнации.
    assert ex.decide([dirty, worse, better], 10, {}) == "продолжать"


# ------------------------------------------------------------ реестр

def test_registry_lifecycle_close_reopen_and_deadlock():
    reg = {}
    pack1 = _pack(_remark(), _remark(id="A-002",
                                     target={"rule": "R-S1-П-1-4"}))
    resp1 = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "change": "исправлено"},
        {"remark": "A-002", "verdict": "ОТКЛОНЕНО", "basis": "б"}]}
    ex.registry_update(reg, 1, pack1, resp1)
    states = {f["id"]: f["state"] for f in reg["findings"]}
    assert states == {"R1-A-001": "исправлено", "R1-A-002": "отклонено"}

    # Обмен 2: A-001 не повторено (→ закрыто аудитором), A-002
    # повторено (→ тупик владельцу).
    pack2 = _pack(_remark(id="B-001", target={"rule": "R-S1-П-1-4"}))
    resp2 = {"round": 2, "answers": [
        {"remark": "B-001", "verdict": "ОТКЛОНЕНО", "basis": "б"}]}
    ex.registry_update(reg, 2, pack2, resp2)
    states = {f["id"]: f["state"] for f in reg["findings"]}
    assert states["R1-A-001"] == "закрыто"
    assert states["R1-A-002"] == "тупик"


def test_registry_reopens_unconfirmed_fix():
    reg = {}
    pack1 = _pack(_remark())
    resp1 = _answers(pack1)
    ex.registry_update(reg, 1, pack1, resp1)
    # Аудитор повторил ту же находку — исправление не подтверждено; та же
    # запись переоткрывается и ведётся дальше (один ключ — одна живая
    # находка, проверка плана №18: прежде рождалась вторая запись, а
    # первая оставалась «открытой» навсегда); принятая повторно заплата —
    # снова «исправлено» до встречной проверки.
    pack2 = _pack(_remark(id="B-001"))
    ex.registry_update(reg, 2, pack2, _answers(pack2))
    assert len(reg["findings"]) == 1
    assert reg["findings"][0]["state"] == "исправлено"
    assert reg["findings"][0]["repeated_round"] == 2
    # Повтор без ответа обработчика — остаётся «открыто».
    pack3 = _pack(_remark(id="C-001"))
    ex.registry_update(reg, 3, pack3, {"answers": []})
    assert len(reg["findings"]) == 1 and reg["findings"][0]["state"] == "открыто"


# ------------------------------------------- diff-only и регресс

def test_diff_only_allows_accepted_and_catches_stray_edits():
    old = sample_model()
    new = sample_model()
    new["rules"][0]["statement"] = "исправленная запись"
    rid = new["rules"][0]["id"]
    assert ex.check_diff_only(old, new, {rid}) == []
    problems = ex.check_diff_only(old, new, set())
    assert any("вне перечня принятых" in p for p in problems)


def test_diff_only_governs_new_and_deleted_rules():
    old = sample_model()
    new = sample_model()
    new["rules"].append({**new["rules"][0], "id": "R-НОВАЯ"})
    assert any("без принятого замечания" in p
               for p in ex.check_diff_only(old, new, set()))
    assert ex.check_diff_only(old, new, {"+"}) == []
    new2 = sample_model()
    dropped = new2["rules"].pop(0)
    assert any("удалена вне перечня" in p
               for p in ex.check_diff_only(old, new2, set()))
    assert ex.check_diff_only(old, new2, {dropped["id"]}) == []


def test_regress_tokens_catch_rollback_and_lost_rule():
    doc = sample_model()
    rid = doc["rules"][0]["id"]
    ok = [{"id": "T1", "rule": rid,
           "must_contain": "4 «дней»"}]
    assert ex.check_regress_tokens(doc, ok) == []
    rolled = [{"id": "T2", "rule": rid, "must_contain": "нет такого"}]
    assert any("откатилось" in p
               for p in ex.check_regress_tokens(doc, rolled))
    lost = [{"id": "T3", "rule": "R-НЕТ", "must_contain": "х"}]
    assert any("исчезла" in p for p in ex.check_regress_tokens(doc, lost))


def test_заплата_не_объект_не_роняет_цикл():
    """Живой случай прод-показа 30.08 (run-0004): модель-обработчик
    вернула patch.rule СПИСКОМ строк — dict(rule) ронял ВЕСЬ
    аудит-цикл TypeError. Не-объект — отбраковка с причиной, цикл
    живёт (дисциплина призрачных источников)."""
    doc = sample_model()
    pack = _pack(_remark(), _remark(id="A-002"), _remark(id="A-003"))
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": "+",
                   "rule": ["кривая", "заплата", "списком"]}},
        {"remark": "A-002", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": ["не", "объект"]},
        {"remark": "A-003", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": doc["rules"][0]["id"],
                   "rule": dict(doc["rules"][0],
                                note="здоровая заплата")}},
    ]}
    new_doc, accepted, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1)
    reasons = "; ".join(r for rj in rejected
                        for r in rj.get("reasons", []))
    assert "не объект" in reasons
    assert len(rejected) == 2, rejected
    # Здоровая заплата применилась, модель жива.
    assert any(r.get("note") == "здоровая заплата"
               for r in new_doc["rules"])


def test_пропуск_с_будущим_id_читается_добавлением():
    """Живой круг полноты run-0002 (30.08): обработчик назвал НОВУЮ
    строку её будущим id вместо "+" — замечание полноты принято, а
    возврат строки падал по форме («патч адресует несуществующую
    строку»). Для класса «пропуск» несуществующий id — ДОБАВЛЕНИЕ
    (id из якорей замечания); для прочих классов — прежняя
    отбраковка. Мягкая строка без веса получает вопрос весов тем же
    сквозным строителем, а не отбраковку инвариантом."""
    doc = sample_model()
    pack = _pack(
        _remark(defect_class="пропуск", target={"rule": None},
                source_refs=[{"sid": "S1", "anchor": "П.1.1"}],
                text="Строка о ночёвках вне базы пропущена"),
        _remark(id="A-002"))
    new_rule = {"title": "Ночёвки вне базы",
                "statement": "штраф за каждую ночёвку вне базы",
                "zone": "PAIRING", "kind": "SOFT",
                "sources": [{"sid": "S1", "anchor": "П.1.1"}]}
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": "O-S1-будущий-id",
                   "rule": dict(new_rule, id="O-S1-будущий-id")}},
        {"remark": "A-002", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": "R-НЕСУЩЕСТВУЮЩАЯ",
                   "rule": dict(doc["rules"][0])}},
    ]}
    new_doc, _acc, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1)
    added = [r for r in new_doc["rules"]
             if r.get("statement") == new_rule["statement"]]
    assert len(added) == 1, [r["id"] for r in new_doc["rules"]]
    assert "будущий" not in added[0]["id"]   # id — из якоря замечания
    assert added[0].get("blockers"), \
        "мягкая строка без веса обязана получить вопрос весов"
    reasons = "; ".join(r for rj in rejected
                        for r in rj.get("reasons", []))
    assert "несуществующую строку" in reasons, \
        "не-«пропуск» с кривым id — прежняя дисциплина"


def test_наследование_трассировки_от_применённого_вопроса():
    """Живой круг полноты run-0002 (A1-002): правило, рождённое
    ПРИМЕНЁННЫМ ОТВЕТОМ анкеты, обработчик трассировал на выдуманные
    координаты листов — «пропуск от ответа» был невозвратим. При
    якорях-призраках трассировка наследуется от применённого
    вопроса, названного замечанием; без такого вопроса — прежняя
    отбраковка гейтом якорей."""
    doc = sample_model()
    doc["questions"].append(
        {"id": "Q-1-приписка", "part": "1", "title": "Двойная база",
         "text": "Пары табельных работают из двух баз?",
         "answer_format": {"type": "value", "note": "да/нет"},
         "rule_refs": [doc["rules"][0]["id"]],
         "source_refs": [{"sid": "S1", "anchor": "П.1.1"},
                         {"sid": "S1", "anchor": "П.1.2"}],
         "answer": {"raw": "да, пары совместные",
                    "status": "применён", "revision": 1}})
    valid = {("S1", "П.1.1"), ("S1", "П.1.2")}
    ghost_rule = {"title": "Совместное базирование",
                  "statement": "пары табельных планируются совместно",
                  "zone": "ROSTERING", "kind": "HARD",
                  "sources": [{"sid": "S2",
                               "anchor": "лист «Отпуска», строка 2"}]}
    rm_ok = _remark(defect_class="пропуск", target={"rule": None},
                    source_refs=[{"sid": "S2",
                                  "anchor": "лист «Экипажи», строка 11"}],
                    text="Применённый ответ анкеты (Q-1-приписка) "
                         "требует правила совместного базирования — "
                         "правила нет")
    rm_ghost = _remark(id="A-002", defect_class="пропуск",
                       target={"rule": None},
                       source_refs=[{"sid": "S2",
                                     "anchor": "лист «Смены», строка 2"}],
                       text="Пропуск без внятного происхождения")
    pack = _pack(rm_ok, rm_ghost)
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": "+", "rule": dict(ghost_rule)}},
        {"remark": "A-002", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": "+", "rule": dict(ghost_rule)}},
    ]}
    new_doc, _acc, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1, valid_anchors=valid)
    added = [r for r in new_doc["rules"]
             if r.get("title") == "Совместное базирование"]
    assert len(added) == 1, (added, rejected)
    assert added[0]["sources"] == [{"sid": "S1", "anchor": "П.1.1"},
                                   {"sid": "S1", "anchor": "П.1.2"}]
    assert "унаследована" in added[0].get("note", "")
    reasons = "; ".join(r for rj in rejected
                        for r in rj.get("reasons", []))
    assert "вне заморозки" in reasons, \
        "пропуск без происхождения — прежняя отбраковка"


def test_форма_удаления_переводит_строку_в_OUT():
    """Живой круг полноты run-0002 (пятёрка п. 5): принятая лишняя
    строка («изобретение») прежде была невычищаемой — гейт требовал
    полную строку. Патч {"rule_id", "rule": {"remove": true}} (или
    пустой rule) переводит строку в OUT с сохранением следа; вес и
    семья снимаются."""
    doc = sample_model()
    rid = doc["rules"][0]["id"]
    doc["rules"][0]["kind"] = "SOFT"
    doc["rules"][0]["weight"] = 3
    pack = _pack(_remark(defect_class="изобретение",
                         target={"rule": rid},
                         text="Лишняя строка-дубль разброса"))
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": rid, "rule": {"remove": True}}}]}
    new_doc, accepted, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1)
    assert rejected == []
    out = next(r for r in new_doc["rules"] if r["id"] == rid)
    assert out["kind"] == "OUT"
    assert "weight" not in out and "export_solver" not in out
    assert "снята обменом" in out.get("note", "")
    assert rid in accepted


def test_реестр_патч_отбракован_не_закрывается_молчанием():
    """Живой круг полноты run-0002: вердикт ПРИНЯТО при отбракованном
    гейтом патче ставил находку «исправлено», и молчание следующего
    круга закрывало её БЕЗ правки. Теперь: состояние «патч
    отбракован», молчанием не закрывается, листинг незавершённого
    несёт её аудитору каждый круг."""
    reg = {"findings": []}
    rm = _remark(defect_class="пропуск", target={"rule": None},
                 text="Правило совместного базирования отсутствует")
    pack = _pack(rm)
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": "+", "rule": {}}}]}
    ex.registry_update(reg, 1, pack, resp, patch_rejected=[
        {"answer": "A-001",
         "reasons": ["трассировка вне заморозки: 'лист «Отпуска»'"]}])
    f = reg["findings"][0]
    assert f["state"] == "патч отбракован"
    assert "вне заморозки" in f.get("patch_reason", "")
    # Молчание следующего круга НЕ закрывает.
    ex.registry_update(reg, 2, _pack(), {"round": 2, "answers": []})
    assert reg["findings"][0]["state"] == "патч отбракован"
    # Повтор аудитором — снова в работу.
    ex.registry_update(reg, 3, _pack(rm), {"round": 3, "answers": []})
    assert reg["findings"][0]["state"] == "открыто"
    # Листинг незавершённого — в тройке листингов.
    _rej, _clo, pending = ex._registry_listings(
        {"findings": [{"key": "k", "state": "патч отбракован",
                       "text": "т", "patch_reason": "причина X"}]})
    assert "правка не встала" in pending and "причина X" in pending


def test_ответ_обработчика_строкой_не_роняет_цикл():
    """Живой обрыв круга BA (31.08, после 229 ответов): элемент
    answers пришёл СТРОКОЙ — a.get ронял весь цикл TypeError.
    Дисциплина «не объект — отбраковка с причиной»: и заплаты, и
    реестр живут."""
    doc = sample_model()
    pack = _pack(_remark(), _remark(id="A-002"))
    resp = {"round": 1, "answers": [
        "просто строка вместо объекта ответа",
        {"remark": "A-002", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": doc["rules"][0]["id"],
                   "rule": dict(doc["rules"][0],
                                note="здоровая заплата")}},
    ]}
    new_doc, _acc, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1)
    reasons = "; ".join(r for rj in rejected
                        for r in rj.get("reasons", []))
    assert "не объект" in reasons
    assert any(r.get("note") == "здоровая заплата"
               for r in new_doc["rules"]), "цикл жив"
    reg = {"findings": []}
    probs = ex.registry_update(reg, 1, pack, resp)
    assert any("не объект" in p for p in probs)
    assert len(reg["findings"]) == 2, "реестр проведён"


def test_источники_заплаты_строками_не_роняют_добавление():
    """Живой обрыв круга BA (31.08, вторая точка): sources заплаты
    добавления пришли списком СТРОК — выбор якоря для id ронял цикл.
    Якорь берётся из первого ОБЪЕКТА (наследование от замечания);
    сама строковая трассировка бракуется гейтом якорей словами."""
    doc = sample_model()
    valid = {("S1", "П.1.1")}
    pack = _pack(_remark(defect_class="пропуск", target={"rule": None},
                         source_refs=[{"sid": "S1",
                                       "anchor": "П.1.1"}]))
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": "+",
                   "rule": {"title": "Новая", "statement": "s",
                            "zone": "PAIRING", "kind": "HARD",
                            "sources": ["п.2.5 табл.3",
                                        "ещё строка"]}}}]}
    new_doc, _acc, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1, valid_anchors=valid)
    added = [r for r in new_doc["rules"] if r.get("title") == "Новая"]
    assert len(added) == 1, (added, rejected)
    assert added[0]["sources"] == [{"sid": "S1", "anchor": "П.1.1"}], \
        "трассировка унаследована от замечания"


def test_реплей_реестра_судит_с_отбраковками_патчей():
    """Живой красный гейт круга BA (31.08): цикл проводит реестр с
    patch_rejected («патч отбракован»), а повторная проводка гейта
    шла без — жизненные циклы расходились на 129 находках. Реплей с
    теми же отбраковками сходится."""
    pack = _pack(_remark(defect_class="пропуск",
                         target={"rule": None}))
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": "+", "rule": {}}}]}
    rej = [{"answer": "A-001", "reasons": ["трассировка вне "
                                           "заморозки"]}]
    reg_cycle = {"findings": []}
    ex.registry_update(reg_cycle, 1, pack, resp, patch_rejected=rej)
    reg_replay = {"findings": []}
    ex.registry_update(reg_replay, 1, pack, resp, patch_rejected=rej)
    states = [f["state"] for f in reg_cycle["findings"]]
    assert states == ["патч отбракован"]
    assert [f["state"] for f in reg_replay["findings"]] == states
    reg_wrong = {"findings": []}
    ex.registry_update(reg_wrong, 1, pack, resp)
    assert [f["state"] for f in reg_wrong["findings"]] \
        == ["исправлено"], "без отбраковок реплей разошёлся бы"


def test_замечание_с_target_null_не_роняет_ответную_фазу():
    """Живой обрыв второго круга BA (31.08): замечание аудитора
    пришло с target: null — .get(ключ, {}) не спасает от значения
    null, и фаза ответов падала NoneType.get. Канон or {}."""
    doc = sample_model()
    rm_null = _remark(id="A-9", target=None, source_refs=None,
                      defect_class="пропуск")
    pack = {"round": 1, "remarks": [rm_null]}

    def llm(prompt):
        import json as _json
        return _json.dumps({"answers": [
            {"remark": "A-9", "verdict": "ОТКЛОНЕНО",
             "basis": "б", "change": None, "patch": None}]},
            ensure_ascii=False)

    resp = ex._respond_round(llm, doc, pack, {})
    assert len(resp["answers"]) == 1


def test_добор_ссылок_вопроса_третьей_формой():
    """Живой дефект линии BA (31.08): оспоренность «18:00» не
    дотягивалась до строки-носителя, а механики правки вопросов не
    было — принятое замечание к вопросу молча ничего не меняло.
    Третья форма: question_patch добавляет rule_refs/source_refs;
    текст и ответ неприкосновенны; призраки и чужие поля — отказ."""
    doc = sample_model()
    doc["questions"].append(
        {"id": "Q-3a-переспрос", "part": "3a", "title": "Переспрос",
         "text": "Какая лестница действует?",
         "answer_format": {"type": "value", "note": "часы"},
         "rule_refs": [doc["rules"][1]["id"]],
         "source_refs": [{"sid": "S1", "anchor": "П.1.1"}]})
    carrier = doc["rules"][0]["id"]
    valid = {("S1", "П.1.1"), ("S1", "П.2.5")}
    rm = _remark(defect_class="потерянная трассировка",
                 target={"question": "Q-3a-переспрос"},
                 text="Вопрос не ссылается на строку-носителя")
    pack = _pack(rm)
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": None,
         "question_patch": {
             "question_id": "Q-3a-переспрос",
             "add_rule_refs": [carrier],
             "add_source_refs": [{"sid": "S1", "anchor": "П.2.5"}]}},
    ]}
    new_doc, accepted, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1, valid_anchors=valid)
    assert rejected == []
    q = next(x for x in new_doc["questions"]
             if x["id"] == "Q-3a-переспрос")
    assert carrier in q["rule_refs"]
    assert {"sid": "S1", "anchor": "П.2.5"} in q["source_refs"]
    assert q["text"] == "Какая лестница действует?"
    assert "?Q-3a-переспрос" in accepted
    # Отказы: чужое поле; несуществующий вопрос; якорь-призрак.
    bad = [
        {"question_id": "Q-3a-переспрос", "set_text": "новый"},
        {"question_id": "Q-НЕТ", "add_rule_refs": [carrier]},
        {"question_id": "Q-3a-переспрос",
         "add_source_refs": [{"sid": "S9", "anchor": "выдумка"}]},
    ]
    for i, qp in enumerate(bad):
        resp_bad = {"round": 1, "answers": [
            {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
             "patch": None, "question_patch": qp}]}
        _nd, _a, rej, _t = ex._apply_patches(doc, pack, resp_bad, 1,
                                             valid_anchors=valid)
        assert rej, ("должен быть отказ", i)


def test_заплата_вопроса_линии_данных_отказ():
    """Живой прогон H0-К (05.09): три круга стагнации — аудитор добирал
    ссылку 'VN-023' вопросу Q-3b-история-нет, который строит код по
    книге данных. Вопросы линии данных (нет данных, значения вне
    перечня, история) заплатой не правятся: отказ словами, вопрос
    неприкосновенен; вопрос обработчика той же формой — применяется."""
    doc = sample_model()
    carrier = doc["rules"][0]["id"]
    for qid in ("Q-3b-история-нет", "Q-3a-нет-данных-X",
                "Q-3b-данные-Y", "Q-3a-данные-Z"):
        doc["questions"].append(
            {"id": qid, "part": "3b", "title": "линия данных",
             "text": "Как поступить?",
             "answer_format": {"type": "choice", "options": []},
             "rule_refs": [doc["rules"][1]["id"]],
             "source_refs": [{"sid": "S1", "anchor": "П.1.1"}]})
    rm = _remark(defect_class="потерянная трассировка",
                 target={"question": "Q-3b-история-нет"},
                 text="Вопрос не ссылается на строку-носителя")
    pack = _pack(rm)
    valid = {("S1", "П.1.1"), ("S1", "П.2.5")}
    before = copy.deepcopy(doc["questions"])
    for qid in ("Q-3b-история-нет", "Q-3a-нет-данных-X",
                "Q-3b-данные-Y", "Q-3a-данные-Z"):
        resp = {"round": 1, "answers": [
            {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
             "patch": None,
             "question_patch": {"question_id": qid,
                                "add_rule_refs": [carrier]}}]}
        new_doc, accepted, rejected, _t = ex._apply_patches(
            doc, pack, resp, 1, valid_anchors=valid)
        assert len(rejected) == 1, qid
        assert "строится кодом" in " ".join(rejected[0]["reasons"]), qid
        assert "?" + qid not in accepted
        assert new_doc["questions"] == before, qid
        assert doc["questions"] == before, ("исходник нетронут", qid)


def test_замечание_к_кодовому_вопросу_отбраковывается_на_приёме():
    """Живой прогон простого Н0 (05.09): аудитор три круга подряд адресовал
    вопрос Q-3b-история-нет, построенный кодом по книге данных, — цикл
    «не чист» при верном пакете. Замечание к кодовому вопросу (линия
    данных, история, целевая) не существует для обмена: отбраковка на
    приёме словами, реестр находок не пополняется."""
    doc = sample_model()
    for qid in ("Q-3b-история-нет", "Q-3a-нет-данных-X", "Q-3b-GOAL"):
        doc["questions"].append(
            {"id": qid, "part": "3b", "title": "кодовый вопрос",
             "text": "Как поступить?",
             "answer_format": {"type": "choice", "options": []},
             "rule_refs": [doc["rules"][0]["id"]],
             "source_refs": [{"sid": "S1", "anchor": "П.1.1"}]})
        rm = _remark(defect_class="выбор чтения", target={"question": qid},
                     text="Вопрос ссылается на нерелевантный абзац")
        problems = ex.validate_remarks(_pack(rm), doc)
        assert any("строится кодом" in p for p in problems), (qid, problems)
    # Обычный вопрос обработчика адресовать можно.
    doc["questions"].append(
        {"id": "Q-3a-переспрос", "part": "3a", "title": "Переспрос",
         "text": "Какая лестница действует?",
         "answer_format": {"type": "value", "note": "часы"},
         "rule_refs": [doc["rules"][1]["id"]],
         "source_refs": [{"sid": "S1", "anchor": "П.1.1"}]})
    rm = _remark(defect_class="выбор чтения", target={"question": "Q-3a-переспрос"},
                 text="Развилка сформулирована неверно")
    assert not any("строится кодом" in p for p in ex.validate_remarks(_pack(rm), doc))


def test_миграция_вида_четвёртой_формой():
    """Живой стоп нано (31.08): изменчивость извлечения дала
    справкам вид HARD, продлению — CASE; аудитор пытался лечить
    полной заменой строки — патчи отбраковывались инвариантами.
    kind_patch: закрытый перечень переходов; DATA снимает вес и
    семью; SOFT берёт вес полем; прочее — отказ словами."""
    doc = sample_model()
    spravka = dict(doc["rules"][0], id="R-SPRAVKA",
                   title="Базы приписки",
                   statement="Базы: SVG, PLY, GRT; главная — PLY.")
    case_r = dict(doc["rules"][0], id="R-CASE", kind="CASE",
                  title="Продление с согласия",
                  statement="Смена может быть продлена с согласия.")
    case_r.pop("conditional", None)
    doc["rules"] += [spravka, case_r]
    pack = _pack(
        _remark(defect_class="искажение", target={"rule": "R-SPRAVKA"},
                text="Справка записана жёстким правилом"),
        _remark(id="A-002", defect_class="искажение",
                target={"rule": "R-CASE"},
                text="Продление принято ответом как мягкое"),
        _remark(id="A-003"))
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": None,
         "kind_patch": {"rule_id": "R-SPRAVKA", "kind": "DATA"}},
        {"remark": "A-002", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": None,
         "kind_patch": {"rule_id": "R-CASE", "kind": "SOFT",
                        "weight": 15}},
        {"remark": "A-003", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": None,
         "kind_patch": {"rule_id": doc["rules"][0]["id"],
                        "kind": "OUT"}},
    ]}
    new_doc, accepted, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1)
    by = {r["id"]: r for r in new_doc["rules"]}
    assert by["R-SPRAVKA"]["kind"] == "DATA"
    assert "weight" not in by["R-SPRAVKA"]
    assert by["R-CASE"]["kind"] == "SOFT"
    assert by["R-CASE"]["weight"] == 15
    assert "R-SPRAVKA" in accepted and "R-CASE" in accepted
    reasons = "; ".join(r for rj in rejected
                        for r in rj.get("reasons", []))
    assert "вне перечня законных миграций" in reasons, \
        "переход в OUT четвёртой формой запрещён"


# ------------------------------------------- закон следа (A07-F1)
# Четвёртое неавтор-ревью A07 (проба R2): ИСПОЛНЕНИЕ судится следом в
# версии свода, а не наличием формы заплаты. Тот же закон — у цикла
# (`_apply_patches`) и у повторной проводки гейта.

def _ans(rid_remark, **over):
    a = {"remark": rid_remark, "verdict": "ПРИНЯТО", "basis": "б"}
    a.update(over)
    return a


def test_trace_row_patch_needs_changed_row():
    """Заплата строки: след — изменившаяся строка. Слово в слово
    прежняя заплата исполнением не является (и форма её не спасает)."""
    old = sample_model()
    rid = old["rules"][0]["id"]
    same = copy.deepcopy(old)
    changed = copy.deepcopy(old)
    changed["rules"][0]["statement"] = "исправленная запись"
    answers = [_ans("A-001", patch={"rule_id": rid, "rule": {"id": rid}})]
    assert ex.executed_by_trace(old, changed, answers) == {"A-001"}
    assert ex.executed_by_trace(old, same, answers) == set()
    # Форма не-объект следом не становится ни при каком исходе.
    assert ex.executed_by_trace(
        old, changed, [_ans("A-001", patch="строка")]) == set()
    # Отклонённый ответ вне закона (его судит не след, а вердикт).
    assert ex.executed_by_trace(
        old, changed, [_ans("A-001", verdict="ОТКЛОНЕНО",
                            patch={"rule_id": rid})]) == set()
    # Смешанный ответ, отбракованный по другой причине, исключён целиком.
    assert ex.executed_by_trace(old, changed, answers,
                                skip={"A-001"}) == set()


def test_trace_removal_and_addition_forms():
    """Снятие строки (уход в OUT либо исчезновение) — след; «+» —
    появившаяся строка, по одной на обещание."""
    old = sample_model()
    rid = old["rules"][0]["id"]
    out_form = copy.deepcopy(old)
    out_form["rules"][0]["kind"] = "OUT"
    assert ex.executed_by_trace(
        old, out_form, [_ans("A-001", patch={"rule_id": rid})]) == {"A-001"}
    gone = copy.deepcopy(old)
    gone["rules"].pop(0)
    assert ex.executed_by_trace(
        old, gone, [_ans("A-001", patch={"rule_id": rid})]) == {"A-001"}
    added = copy.deepcopy(old)
    added["rules"].append({**old["rules"][0], "id": "R-НОВАЯ"})
    plus = [_ans("A-001", patch={"rule_id": "+", "rule": {}})]
    assert ex.executed_by_trace(old, added, plus) == {"A-001"}
    assert ex.executed_by_trace(old, copy.deepcopy(old), plus) == set()
    # Два обещания «добавлю» на ОДНУ добавленную строку: исполнено одно.
    two = [_ans("A-001", patch={"rule_id": "+", "rule": {}}),
           _ans("A-002", patch={"rule_id": "+", "rule": {}})]
    assert ex.executed_by_trace(old, added, two) == {"A-001"}
    # Класс «пропуск»: обработчик назвал новую строку будущим id —
    # строки с таким id в прежней версии нет, след ищется тем же пулом.
    named = [_ans("A-001", patch={"rule_id": "R-НОВАЯ", "rule": {}})]
    assert ex.executed_by_trace(old, added, named) == {"A-001"}


def test_trace_kind_and_question_patch_forms():
    """Заплата вида — сменившийся вид; заплата вопроса — сменившиеся
    цели вопроса (добор ссылки, которая уже стоит, следа не даёт)."""
    old = sample_model()
    rid = old["rules"][0]["id"]
    kp = [_ans("A-001", kind_patch={"rule_id": rid, "kind": "SOFT"})]
    soft = copy.deepcopy(old)
    soft["rules"][0]["kind"] = "SOFT"
    assert ex.executed_by_trace(old, soft, kp) == {"A-001"}
    noted = copy.deepcopy(old)
    noted["rules"][0]["note"] = "примечание есть, вид прежний"
    assert ex.executed_by_trace(old, noted, kp) == set()

    qid = old["questions"][0]["id"]
    qp = [_ans("A-001", question_patch={"question_id": qid,
                                        "add_rule_refs": [rid]})]
    grown = copy.deepcopy(old)
    grown["questions"][0].setdefault("rule_refs", []).append(rid)
    assert ex.executed_by_trace(old, grown, qp) == {"A-001"}
    assert ex.executed_by_trace(old, copy.deepcopy(old), qp) == set()
    srcs = copy.deepcopy(old)
    srcs["questions"][0].setdefault("source_refs", []).append(
        {"sid": "S9", "anchor": "П.9.9"})
    # A source change is not execution of the requested rule-reference change.
    assert ex.executed_by_trace(old, srcs, qp) == set()
    source_qp = [_ans("A-001", question_patch={"question_id": qid,
        "add_source_refs": [{"sid": "S9", "anchor": "П.9.9"}]})]
    assert ex.executed_by_trace(old, srcs, source_qp) == {"A-001"}


def test_trace_reading_choice_conversion():
    """«Выбор чтения», ставший вопросом анкеты: след — новый вопрос,
    добор его целей либо блокировка строки-носителя. Заплата такого
    ответа не применяется — по ней след и не ищется."""
    old = sample_model()
    rid = old["rules"][0]["id"]
    a = [_ans("A-001", patch={"rule_id": rid, "rule": {"id": rid}},
              converted_to_question="Q-2-ЧТЕНИЕ")]
    born = copy.deepcopy(old)
    born["questions"].append({"id": "Q-2-ЧТЕНИЕ", "part": "2",
                              "rule_refs": [rid],
                              "source_refs": [{"sid": "S1",
                                               "anchor": "П.1.1"}]})
    assert ex.executed_by_trace(old, born, a) == {"A-001"}
    # Вопрос-дубль переиспользован: цели прежние, но носитель
    # заблокирован — это тоже след.
    with_q = copy.deepcopy(born)
    reused = copy.deepcopy(with_q)
    reused["rules"][0].setdefault("blockers", []).append("Q-2-ЧТЕНИЕ")
    assert ex.executed_by_trace(with_q, reused, a) == {"A-001"}
    # Ни вопроса, ни блокировки — обещание без следа; строка при этом
    # НЕ правится (заплата перевода не применяется).
    assert ex.executed_by_trace(with_q, copy.deepcopy(with_q), a) == set()


def test_trace_law_is_one_for_cycle_and_gate():
    """Симметрия: то, что `_apply_patches` считает исполненным, и есть
    след по `executed_by_trace` — пустая заплата отбраковывается
    причиной «заплата без следа», а не числится исправлением."""
    doc = sample_model()
    rid, rid2 = doc["rules"][0]["id"], doc["rules"][1]["id"]
    pack = _pack(_remark(target={"rule": rid}),
                 _remark(id="A-002", target={"rule": rid2}))
    resp = {"round": 1, "answers": [
        {"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": rid,
                   "rule": {**doc["rules"][0],
                            "statement": "исправленная запись"}}},
        {"remark": "A-002", "verdict": "ПРИНЯТО", "basis": "б",
         "patch": {"rule_id": rid2,
                   "rule": copy.deepcopy(doc["rules"][1])}},
    ]}
    executed: set = set()
    new_doc, accepted, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1, executed_out=executed)
    assert executed == {"A-001"}
    assert ex.executed_by_trace(doc, new_doc, resp["answers"],
                                skip={"A-002"}) == {"A-001"}
    by_answer = {r["answer"]: "; ".join(r["reasons"]) for r in rejected}
    assert "A-001" not in by_answer
    assert "заплата без следа" in by_answer["A-002"], by_answer
    assert ex.NO_TRACE_REASON in by_answer["A-002"]
    # Цель diff-only у пустой заплаты остаётся (строка адресована) —
    # тем же перечнем живёт повторная проводка гейта.
    assert rid in accepted


# ------------- след судится ПОКАЗНО (A07-F1, пятое неавтор-ревью, №2)
# Разница версий круга ОДНА на строку, а принятых ответов на неё может
# быть несколько: судить исполнение только ею — значит закрывать
# пустое обещание чужим исправлением. Судья заплаты — редакция строки
# ПЕРЕД НЕЙ; порядок обработки равен порядку ответов.

def _two_same_row(order):
    doc = sample_model()
    rid = doc["rules"][0]["id"]
    pack = _pack(_remark(id="A-001", target={"rule": rid}),
                 _remark(id="A-002", target={"rule": rid}))
    noop = {"rule_id": rid, "rule": copy.deepcopy(doc["rules"][0])}
    fix = {"rule_id": rid, "rule": {**copy.deepcopy(doc["rules"][0]),
                                    "statement": "исправленная запись"}}
    pair = [("A-001", noop), ("A-002", fix)] if order == "пустая первой" \
        else [("A-001", fix), ("A-002", noop)]
    resp = {"round": 1,
            "answers": [_ans(aid, patch=p) for aid, p in pair]}
    executed: set = set()
    new_doc, accepted, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1, executed_out=executed)
    row = next(r for r in new_doc["rules"] if r["id"] == rid)
    return {"rid": rid, "doc": doc, "new_doc": new_doc,
            "answers": resp["answers"], "executed": executed,
            "accepted": accepted, "rejected": rejected,
            "statement": row.get("statement")}


def test_noop_patch_does_not_ride_on_another_answers_fix():
    """Проба P2a пятого неавтор-ревью (ложный ЗЕЛЁНЫЙ): в одном круге
    на ОДНУ строку принято два ответа — пустая копия прежней строки и
    настоящее исправление. Прежде исполненными числились ОБА (строка-то
    в версии изменилась), и пустое обещание закрывалось молчанием
    следующего аудита. Теперь копия судится редакцией, стоящей ПЕРЕД
    ней: «заплата без следа», исполнение — только у исправления."""
    got = _two_same_row("пустая первой")
    assert got["executed"] == {"A-002"}
    assert [(r["answer"], r["reasons"]) for r in got["rejected"]] == \
        [("A-001", [ex.NO_TRACE_REASON])]
    assert got["statement"] == "исправленная запись"
    # Цель строки у пустой заплаты остаётся (один закон целей с
    # реплеем гейта): касание строки цикл ей засчитал.
    assert got["accepted"] == {got["rid"]}
    assert ex.no_trace_only(got["rejected"]) == {"A-001"}
    # Второй судья (закон финальной версии) согласен с первым.
    assert ex.executed_by_trace(got["doc"], got["new_doc"],
                                got["answers"],
                                skip={"A-001"}) == {"A-002"}


def test_rollback_patch_does_not_erase_the_real_fix():
    """Обратный порядок той же пробы: сначала настоящее исправление,
    следом копия ПРЕЖНЕЙ строки. Применить её — значит стереть чужой
    след и отдать «заплату без следа» исправлению (так и было). Теперь
    откат отбраковывается формой, строка остаётся исправленной, а
    исполнение — у исправления."""
    got = _two_same_row("исправление первым")
    assert got["executed"] == {"A-001"}
    assert [(r["answer"], r["reasons"]) for r in got["rejected"]] == \
        [("A-002", [ex.ROLLBACK_REASON])]
    assert got["statement"] == "исправленная запись"
    # Откат — отбраковка по ИНОЙ причине: целью строки он не становится
    # ни у цикла, ни у реплея (её принёс исполненный ответ).
    assert ex.no_trace_only(got["rejected"]) == set()
    assert got["accepted"] == {got["rid"]}
    assert ex.executed_by_trace(got["doc"], got["new_doc"],
                                got["answers"],
                                skip={"A-002"}) == {"A-001"}


def test_empty_question_patch_is_judged_at_application():
    """Та же показная проверка у третьей формы: два принятых ответа
    добирают ссылки ОДНОМУ вопросу — один пустой (ссылка уже стоит),
    другой настоящий. Пустой добор отбраковывается «заплатой без
    следа» в момент применения, а не едет на чужом следе."""
    doc = sample_model()
    qid = doc["questions"][0]["id"]
    stays = doc["questions"][0]["rule_refs"][0]
    adds = next(r["id"] for r in doc["rules"]
                if r["id"] not in doc["questions"][0]["rule_refs"])
    pack = _pack(_remark(id="A-001", target={"question": qid},
                         defect_class="потерянная трассировка"),
                 _remark(id="A-002", target={"question": qid},
                         defect_class="потерянная трассировка"))
    resp = {"round": 1, "answers": [
        _ans("A-001", question_patch={"question_id": qid,
                                      "add_rule_refs": [stays]}),
        _ans("A-002", question_patch={"question_id": qid,
                                      "add_rule_refs": [adds]})]}
    executed: set = set()
    new_doc, _accepted, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1, executed_out=executed)
    assert executed == {"A-002"}
    assert [(r["answer"], r["reasons"]) for r in rejected] == \
        [("A-001", [ex.NO_TRACE_REASON])]
    q = next(x for x in new_doc["questions"] if x["id"] == qid)
    assert adds in q["rule_refs"]
    assert q["rule_refs"].count(stays) == 1


def test_no_trace_only_names_the_touch_bearing_rejections():
    """ОДИН ЗАКОН ЦЕЛЕЙ (находка 1 пятого неавтор-ревью): касание
    строки приносит ответ, ВСЕ причины отбраковки которого — «заплата
    без следа» (заплата применилась, только ничего не изменила).
    Любая другая причина — заплата не встала: ни цели, ни касания."""
    assert ex.no_trace_only(
        [{"answer": "A-001", "reasons": [ex.NO_TRACE_REASON]}]) == {"A-001"}
    # Смешанный ответ (пустая заплата строки + отбракованная формой
    # заплата вида) касания не приносит.
    assert ex.no_trace_only(
        [{"answer": "A-001", "reasons": [ex.NO_TRACE_REASON]},
         {"answer": "A-001", "reasons": ["заплата вида не объект: 1"]}]) == set()
    assert ex.no_trace_only(
        [{"answer": "A-002", "reasons": [ex.ROLLBACK_REASON]},
         {"answer": "A-003", "reasons": [ex.EMPTY_PROMISE_REASON]},
         {"answer": "A-004", "reasons": []},
         "мусор"]) == set()
    assert ex.no_trace_only(None) == set()


# ---------------------------------------------------------------------------
# A07-R2 / A07-R3 (вердикт VERDICT-A07-d9343ead, 12.09.2026): форма
# носителя реестра и его НЕДОСТУПНОСТЬ. Один загрузчик, три исхода:
# пути нет — пустой реестр; путь есть, а прочитать нечем — отказ;
# прочитан — судятся версия и домены полей.
# ---------------------------------------------------------------------------

def _reg_file(tmp_path, body, name="registry.json"):
    import json as _json
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


def _finding(**over):
    f = {"id": "F1", "key": "k", "state": "открыто",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 1, "closed_round": None}
    f.update(over)
    return f


@pytest.mark.parametrize("version", [None, False, True, 0, 7, 1.5, "",
                                     "registry/1", "registry/9", "REGISTRY/2",
                                     [], {}],
                         ids=["null", "false", "true", "zero", "number",
                              "float", "empty", "r1", "r9", "case", "list",
                              "object"])
def test_a07_r2_declared_version_is_judged_not_guessed(tmp_path, version):
    """ЗАЯВЛЕННАЯ версия судится, чем бы она ни была. Прежде
    `raw.get("schema_version") is None` читал явный null как СТАРУЮ
    форму: негодный носитель проезжал молча, писатель ставил ему
    registry/2 и цикл заканчивался «успехом». Отсутствие ключа и
    значение — разные вещи."""
    p = _reg_file(tmp_path, {"schema_version": version, "findings": []})
    before = p.read_bytes()
    with pytest.raises(ex.ExchangeError,
                       match="неизвестная или негодная версия реестра"):
        ex.load_registry(p)
    assert p.read_bytes() == before, "байты входа переписаны при отказе"


def test_a07_r2_absent_version_key_is_the_legacy_form(tmp_path):
    """Положительный контроль: ключа НЕТ — старая форма registry/1
    читается как прежде, включая реестр вовсе без поля «findings»."""
    p = _reg_file(tmp_path, {"findings": [_finding()]})
    assert [f["id"] for f in ex.load_registry(p)["findings"]] == ["F1"]
    p = _reg_file(tmp_path, {"cycle": 2})
    assert ex.load_registry(p) == {"cycle": 2}
    p = _reg_file(tmp_path, {"schema_version": ex.REGISTRY_SCHEMA,
                             "findings": [_finding()]})
    assert ex.load_registry(p)["schema_version"] == ex.REGISTRY_SCHEMA
    # Неполная новая форма — отказ (закон прежний, не ослаблен).
    p = _reg_file(tmp_path, {"schema_version": ex.REGISTRY_SCHEMA})
    with pytest.raises(ex.ExchangeError, match="неполная форма"):
        ex.load_registry(p)


@pytest.mark.parametrize("field,value", [
    ("state", "foreign-state"), ("state", None), ("state", 1),
    ("state", ["открыто"]), ("state", "ЗАКРЫТО"),
    ("severity", "foreign-severity"), ("severity", None), ("severity", 3),
    ("severity", ["КРИТИЧЕСКОЕ"]), ("severity", "критическое")])
def test_a07_r2_state_and_severity_domains_are_closed(tmp_path, field, value):
    """Домены полей, ВЛИЯЮЩИХ НА РЕШЕНИЕ, судятся до счёта живых и
    блокирующих находок: находка с чужим состоянием исчезала из живых,
    находка с чужой серьёзностью не блокировала успех — неизвестность
    работала доказательством отсутствия спора. Отказ называет находку.
    Отсутствие поля судится так же: цикл его всегда пишет."""
    body = {"schema_version": ex.REGISTRY_SCHEMA,
            "findings": [_finding(**{field: value})]}
    if value is None:
        body["findings"][0].pop(field)
    p = _reg_file(tmp_path, body)
    before = p.read_bytes()
    with pytest.raises(ex.ExchangeError, match="вне перечня"):
        ex.load_registry(p)
    with pytest.raises(ex.ExchangeError, match="'F1'"):
        ex.load_registry(p)
    assert p.read_bytes() == before


@pytest.mark.parametrize("state", ex.STATES)
@pytest.mark.parametrize("severity", ex.SEVERITIES)
def test_a07_r2_every_contract_state_and_severity_is_accepted(
        tmp_path, state, severity):
    """Положительный контроль по ВСЕМУ контракту: 12 состояний × 4
    серьёзности (включая решения арбитра) читаются без отказа."""
    p = _reg_file(tmp_path, {"schema_version": ex.REGISTRY_SCHEMA,
                             "findings": [_finding(state=state,
                                                   severity=severity)]})
    got = ex.load_registry(p)
    if state == ex.CODE_DEADLOCK_KIND:
        # Композиция A07 × A09 (решение аудитора 12.09.2026,
        # DECISION-A07-A09-INTEGRATION-LAW). Прежнее ожидание «состояние
        # читается тем же словом» соответствовало общему закону для всех
        # двенадцати состояний, пока читатель не дозревал унаследованную
        # техническую отбраковку. На общей голове единый загрузчик
        # доводит её до тупика (§12.8) — вид сохраняется полем
        # `deadlock_kind`, сведений не теряется, а предмет самой пробы
        # (ни одно состояние контракта не вызывает отказа чтения)
        # остаётся прежним. Для остальных одиннадцати ожидание не
        # менялось.
        assert got["findings"][0]["state"] == "тупик"
        assert got["findings"][0]["deadlock_kind"] == state
    else:
        assert got["findings"][0]["state"] == state
    assert got["findings"][0]["severity"] == severity


def test_a07_r3_loader_tells_absence_from_unreadable(tmp_path, monkeypatch):
    """ТРИ ИСХОДА загрузчика. Прежде судьёй было `not is_file()`, и
    каталог с именем registry.json, отказ доступа и сбой чтения
    читались как «реестра нет» → «живых находок нет» → зелёный показ.
    «Не удалось узнать о спорах» — не «споров нет»."""
    from pathlib import Path as _Path
    missing = tmp_path / "нет" / "registry.json"
    assert ex.load_registry(missing) == {}          # законная новизна
    good = _reg_file(tmp_path, {"schema_version": ex.REGISTRY_SCHEMA,
                                "findings": [_finding()]})
    assert len(ex.load_registry(good)["findings"]) == 1
    # Каталог вместо файла — отказ на любой площадке (подкласс OSError
    # у Windows и POSIX разный, закон один).
    as_dir = tmp_path / "dir" / "registry.json"
    as_dir.mkdir(parents=True)
    with pytest.raises(ex.RegistryUnavailable, match="недоступен"):
        ex.load_registry(as_dir)
    # Точечная ошибка чтения существующего файла.
    original = _Path.read_text
    for error_cls in (PermissionError, OSError, TimeoutError):
        def fail_one(self, *a, _e=error_cls, **kw):
            if self == good:
                raise _e("synthetic registry read unavailable")
            return original(self, *a, **kw)
        monkeypatch.setattr(_Path, "read_text", fail_one)
        with pytest.raises(ex.RegistryUnavailable, match="недоступен"):
            ex.load_registry(good)
        monkeypatch.undo()
    # Восстановление после отказа: тот же путь читается снова.
    assert len(ex.load_registry(good)["findings"]) == 1
    # Битый JSON — отказ ФОРМЫ, не недоступность (слова различают).
    good.write_text("{не json", encoding="utf-8")
    with pytest.raises(ex.ExchangeError, match="не читается как JSON"):
        ex.load_registry(good)
    assert not isinstance(
        pytest.raises(ex.ExchangeError, ex.load_registry, good).value,
        ex.RegistryUnavailable)
