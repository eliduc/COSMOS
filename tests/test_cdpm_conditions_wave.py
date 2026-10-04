# -*- coding: utf-8 -*-
"""Волна условий (этап 5б): миграции «норм прозой» подготовки,
разбора и стыковки. Каждая ветка — нарушающий случай И контрольный:
миграция, поднимающая всё подряд, так же вредна, как не поднимающая
ничего (условные формулировки и чужие контексты трогать нельзя)."""
from __future__ import annotations

from cdpm import model as m


def _doc(*rules):
    return {"rules": [dict(r) for r in rules]}


def _r(rid, kind, statement, params=None):
    return {"id": rid, "kind": kind, "statement": statement,
            "params": [dict(p) for p in (params or [])]}


# ---------------------------------------------- подготовка и разбор

def test_подготовка_и_разбор_поднимаются_из_прозы():
    doc = _doc(_r("R1", "HARD",
                  "Время предполетной подготовки не менее 1 часа, "
                  "послеполетной подготовки не менее 30 минут."))
    notes = m.restore_briefing_debrief_params(doc)
    got = {p["name"]: p["value"] for p in doc["rules"][0]["params"]}
    assert got == {"briefing_min": 60, "debriefing_min": 30}
    assert len(notes) == 2
    assert all(p["relation"] == "min"
               for p in doc["rules"][0]["params"])


def test_установленное_авиакомпанией_строже_нормы():
    doc = _doc(_r("R1", "HARD",
                  "Время предполетной подготовки не может быть менее "
                  "1 часа; в авиакомпании установлено 1,5 часа."))
    m.restore_briefing_debrief_params(doc)
    got = {p["name"]: p["value"] for p in doc["rules"][0]["params"]}
    assert got == {"briefing_min": 90}, "берётся установленное"


def test_зачёт_подготовки_в_служебное_время_не_поднимается():
    """«Увеличивается на время подготовки, но не более 60 мин» — про
    зачёт времени, не про норму длительности."""
    doc = _doc(_r("R1", "HARD",
                  "Служебное время кабинного экипажа увеличивается на "
                  "время предполётной подготовки, но не более 60 мин."))
    assert m.restore_briefing_debrief_params(doc) == []
    assert doc["rules"][0]["params"] == []


def test_снятое_правило_не_поднимается():
    doc = _doc(_r("R1", "OUT",
                  "Время предполетной подготовки не менее 60 минут."))
    assert m.restore_briefing_debrief_params(doc) == []


# ---------------------------------------------- чужая природа разбора

def test_разбор_с_параметром_стыковки_переименовывается():
    doc = _doc(_r("R1", "HARD",
                  "Продолжительность послеполетной подготовки "
                  "(де-брифинг) >= 30 мин",
                  [{"name": "min_turnaround", "value": 30,
                    "unit": "duration_minutes", "relation": "min"}]))
    notes = m.restore_debrief_param_nature(doc)
    assert len(notes) == 1
    assert doc["rules"][0]["params"][0]["name"] == "debriefing_min"


def test_настоящая_стыковка_не_переименовывается():
    doc = _doc(_r("R1", "HARD",
                  "Минимальная стыковка после послеполетного разбора "
                  "не менее 50 минут.",
                  [{"name": "min_turnaround", "value": 50,
                    "unit": "duration_minutes", "relation": "min"}]))
    assert m.restore_debrief_param_nature(doc) == []
    assert doc["rules"][0]["params"][0]["name"] == "min_turnaround"


def test_число_формулировки_обязано_подтверждать():
    doc = _doc(_r("R1", "HARD",
                  "Послеполетный разбор не менее 30 минут.",
                  [{"name": "min_turnaround", "value": 45,
                    "unit": "duration_minutes", "relation": "min"}]))
    assert m.restore_debrief_param_nature(doc) == [], (
        "число параметра расходится с формулировкой — не трогать")


# ---------------------------------------------- стыковка из прозы

def test_стыковка_из_прозы_с_областью_базы():
    doc = _doc(_r("R1", "HARD",
                  "Минимальное время стыковки в базовом аэропорту "
                  "составляет 1,5 часа."))
    notes = m.restore_sit_prose_params(doc)
    assert len(notes) == 1
    p = doc["rules"][0]["params"][0]
    assert p["name"] == "min_turnaround" and p["value"] == 90
    assert p["qualifier"] == "база"


def test_условная_стыковка_не_поднимается():
    doc = _doc(_r("R1", "HARD",
                  "Стыковка в базовом аэропорту без отдыха допустима "
                  "только при вылете <=04:00 UTC; при вылете >04:00 "
                  "UTC требуется либо стыковка >120 минут."))
    assert m.restore_sit_prose_params(doc) == []
    assert doc["rules"][0]["params"] == []


def test_повторный_вызов_ничего_не_меняет():
    doc = _doc(_r("R1", "HARD",
                  "Минимальное время стыковки в базовом аэропорту "
                  "составляет 1,5 часа."))
    m.restore_sit_prose_params(doc)
    assert m.restore_sit_prose_params(doc) == [], "детерминизм"
    assert len(doc["rules"][0]["params"]) == 1


# ---------------------------------------------- вопросы анкеты условий

def _rule_src(rid, kind, statement):
    r = _r(rid, kind, statement)
    r["sources"] = [{"sid": "S1", "anchor": "стр.1"}]
    return r


def test_вопросы_условий_создаются_по_темам_модели(
        conditions_topics_contour):
    doc = _doc(
        _rule_src("R1", "HARD",
                  "Пределы при неопределенной акклиматизации без FRMS "
                  "по числу секторов."),
        _rule_src("R2", "HARD",
                  "Минимальное время отдыха в полете зависит от "
                  "класса экипажа."))
    doc["questions"] = []
    created = __import__("cdpm.model", fromlist=["model"]) \
        .ensure_conditions_questions(doc)
    assert "Q-3a-условия-прил4" in created
    assert "Q-3a-условия-прил6" in created
    # Обе акклиматизационные темы представлены правилом R1.
    assert "Q-3a-условия-акклиматизация" in created
    q = next(x for x in doc["questions"]
             if x["id"] == "Q-3a-условия-прил4")
    assert q["answer_format"]["type"] == "value"
    assert q["rule_refs"] == ["R1"] and q["source_refs"]


def test_вопросы_условий_идемпотентны_и_не_навязываются(
        conditions_topics_contour):
    doc = _doc(_r("R1", "HARD", "Стыковка не менее 50 минут."))
    doc["questions"] = []
    from cdpm import model as mm
    assert mm.ensure_conditions_questions(doc) == [], (
        "тем условий в модели нет — вопросы не навязываются")
    doc2 = _doc(_rule_src("R1", "HARD",
                          "Неопределенная акклиматизация: пределы."))
    doc2["questions"] = []
    first = mm.ensure_conditions_questions(doc2)
    assert first and mm.ensure_conditions_questions(doc2) == []


# ------------------- регрессии ревью 2026-08-27 (проверено исполнением)

def test_чужое_установлено_за_точкой_с_запятой_не_поднимается():
    doc = _doc(_r("R1", "HARD",
                  "Время предполетной подготовки не менее 1 часа; "
                  "для послеполетного разбора установлено 30 минут."))
    m.restore_briefing_debrief_params(doc)
    got = {p["name"]: p["value"] for p in doc["rules"][0]["params"]}
    assert got.get("briefing_min") == 60, (
        "чужая клауза «установлено» не должна побеждать свою норму")


def test_не_менее_чужой_величины_не_поднимается():
    doc = _doc(_r("R1", "HARD",
                  "После предполетной подготовки и полета экипажу "
                  "предоставляется отдых не менее 12 часов."))
    assert m.restore_briefing_debrief_params(doc) == []
    assert doc["rules"][0]["params"] == []


def test_условие_при_любое_блокирует_стыковку():
    doc = _doc(_r("R1", "HARD",
                  "Минимальное время стыковки при смене типа "
                  "воздушного судна составляет 1 час."))
    assert m.restore_sit_prose_params(doc) == []
    assert doc["rules"][0]["params"] == []


def test_неразрешённый_конфликт_природы_не_получает_второй_параметр():
    doc = _doc(_r("R1", "HARD",
                  "Послеполетный разбор не менее 30 минут.",
                  [{"name": "min_turnaround", "value": 45,
                    "unit": "duration_minutes", "relation": "min"}]))
    m.restore_debrief_param_nature(doc)
    notes = m.restore_briefing_debrief_params(doc)
    names = [p["name"] for p in doc["rules"][0]["params"]]
    assert names == ["min_turnaround"], (
        "debriefing_min рядом с неподтверждённым min_turnaround "
        "создал бы конфликтную пару")
    assert any("конфликт природы" in n for n in notes)


def test_буква_ё_не_ломает_подъём():
    doc = _doc(_r("R1", "HARD",
                  "Время предполётной подготовки не менее 1 часа."))
    m.restore_briefing_debrief_params(doc)
    got = {p["name"]: p["value"] for p in doc["rules"][0]["params"]}
    assert got == {"briefing_min": 60}


def test_стыковка_без_слова_время_поднимается():
    doc = _doc(_r("R1", "HARD",
                  "Стыковка между рейсами не менее 50 минут."))
    m.restore_sit_prose_params(doc)
    assert doc["rules"][0]["params"][0]["value"] == 50


def test_аэропорт_базы_обратным_порядком_слов_даёт_область():
    doc = _doc(_r("R1", "HARD",
                  "Минимальное время стыковки в аэропорту базы "
                  "составляет 45 минут."))
    m.restore_sit_prose_params(doc)
    p = doc["rules"][0]["params"][0]
    assert p["value"] == 45 and p.get("qualifier") == "база"


def test_отрицание_базы_не_ставит_область_база():
    doc = _doc(_r("R1", "HARD",
                  "Минимальное время стыковки не в базовом аэропорту "
                  "составляет 45 минут."))
    m.restore_sit_prose_params(doc)
    p = doc["rules"][0]["params"][0]
    assert "qualifier" not in p


def test_составная_длительность_час_и_минуты():
    doc = _doc(_r("R1", "HARD",
                  "Время предполетной подготовки не менее "
                  "1 часа 30 минут."))
    m.restore_briefing_debrief_params(doc)
    assert doc["rules"][0]["params"][0]["value"] == 90


def test_длительность_в_форме_часы_двоеточие_минуты():
    doc = _doc(_r("R1", "HARD",
                  "Стыковка между рейсами не менее 0:50."))
    m.restore_sit_prose_params(doc)
    assert doc["rules"][0]["params"][0]["value"] == 50


def test_двузначная_норма_не_выбирается_машиной():
    doc = _doc(_r("R1", "HARD",
                  "Время предполетной подготовки в базовом аэропорту "
                  "не менее 60 минут, во внебазовом не менее "
                  "30 минут."))
    notes = m.restore_briefing_debrief_params(doc)
    assert doc["rules"][0]["params"] == []
    assert any("двузначна" in n for n in notes)


def test_установлена_норма_согласованной_формой():
    doc = _doc(_r("R1", "HARD",
                  "Время предполетной подготовки не может быть менее "
                  "1 часа; в авиакомпании установлена норма "
                  "1,5 часа."))
    m.restore_briefing_debrief_params(doc)
    assert doc["rules"][0]["params"][0]["value"] == 90


def test_дебрифинг_без_слова_послеполетный_переименовывается():
    doc = _doc(_r("R1", "HARD",
                  "Де-брифинг не менее 30 минут.",
                  [{"name": "min_turnaround", "value": 30,
                    "unit": "duration_minutes", "relation": "min"}]))
    notes = m.restore_debrief_param_nature(doc)
    assert len(notes) == 1
    assert doc["rules"][0]["params"][0]["name"] == "debriefing_min"


def test_нормы_перегона_не_становятся_общими():
    """Живой класс: нормы подготовки и разбора ДЛЯ ПЕРЕГОНА (DH,
    условные) — чужой контекст, общими носителями не становятся."""
    doc = _doc(_r("R1", "HARD",
                  "Послеполетный разбор после перегона DH составляет "
                  "15 мин. Предполетная подготовка перед перегоном "
                  "DH при наличии дальнейшего рабочего рейса "
                  "составляет 2 часа; при отсутствии дальнейших "
                  "работ — 30 мин."))
    assert m.restore_briefing_debrief_params(doc) == []
    assert doc["rules"][0]["params"] == []


def test_вопрос_конфликта_стыковки_ставится_и_идемпотентен():
    doc = _doc(
        _r("R-M", "HARD", "Стыковка не менее 30 минут.",
           [{"name": "min_turnaround", "value": 30,
             "unit": "duration_minutes", "relation": "min"}]),
        _rule_src("R-P", "HARD",
                  "Минимальное время стыковки в базовом аэропорту "
                  "составляет 1,5 часа."))
    doc["rules"][0]["sources"] = [{"sid": "S1", "anchor": "стр.2"}]
    doc["questions"] = []
    created = m.ensure_sit_conflict_question(doc)
    assert created == ["Q-3a-условия-стыковка"]
    q = doc["questions"][0]
    assert "R-M" in q["rule_refs"] and "R-P" in q["rule_refs"]
    assert "30 мин" in q["text"]
    assert m.ensure_sit_conflict_question(doc) == []


def test_вопрос_конфликта_стыковки_без_конфликта_не_ставится():
    doc = _doc(_r("R-M", "HARD", "Стыковка не менее 50 минут.",
                  [{"name": "min_turnaround", "value": 50,
                    "unit": "duration_minutes", "relation": "min"}]))
    doc["questions"] = []
    assert m.ensure_sit_conflict_question(doc) == [], (
        "один носитель без расходящейся прозы — вопроса нет")


def _sit_answer_doc(rule_kind="HARD", answer_status="применён"):
    q = {"id": "Q-3a-условия-стыковка", "part": "3a",
         "title": "Конфликт значений минимальной стыковки",
         "text": "…", "answer_format": {"type": "value"},
         "rule_refs": ["R-DH"],
         "answer": {"raw": "Минимальная стыковка экипажа между "
                           "рейсами = 50 минут. Правило 1,5 часа — "
                           "брифинг, снять.",
                    "status": answer_status, "revision": 11}}
    doc = _doc(_r("R-DH", rule_kind,
                  "briefing_duration >= min_value; для рейсов с DH "
                  "применяется стыковка 0:50, а не сокращение "
                  "брифинга до 60 минут."))
    doc["questions"] = [q]
    return doc


def test_ответ_о_стыковке_вносит_параметр_с_подтверждением():
    doc = _sit_answer_doc()
    notes = m.restore_sit_from_answer(doc)
    assert len(notes) == 1
    p = doc["rules"][0]["params"][0]
    assert p["name"] == "min_turnaround" and p["value"] == 50
    assert m.restore_sit_from_answer(doc) == [], "идемпотентность"


def test_ответ_без_подтверждающего_правила_ничего_не_вносит():
    doc = _sit_answer_doc()
    doc["rules"][0]["statement"] = "Стыковка описана в РПП."
    assert m.restore_sit_from_answer(doc) == []
    assert doc["rules"][0]["params"] == []


def test_непринятый_ответ_о_стыковке_не_применяется():
    doc = _sit_answer_doc(answer_status="ожидает обработчика")
    assert m.restore_sit_from_answer(doc) == []


# ------------------- критерий 0096 из ответа анкеты (2026-08-28)

_ANS_0096 = '''Целевая — нормированная стоимость по рангам (0096).
{"criterion": "NORMALIZED_COST_0096", "rule": "R-CRIT-01",
 "rates_param": "PARAM-T-01",
 "rates": {"КВС": {"flight": 1.0, "work": 0.7},
           "ВП": {"flight": 1.0, "work": 0.7}},
 "label": "NORMALIZED_COST",
 "version": "PARAM-T-01/base-1",
 "effective_from": "2026-08-24", "effective_to": null}'''


def _doc_with_goal(answer_raw=None):
    doc = {"rules": [], "objectives": [], "questions": [{
        "id": "Q-3a-GOAL", "part": "3a", "title": "цель", "text": "т",
        "answer_format": {"type": "value"},
        "rule_refs": ["R-G"],
        "source_refs": [{"sid": "S1", "anchor": "стр.1"}],
        "answer": {"raw": "минимум", "status": "применён",
                   "revision": 2}}]}
    if answer_raw is not None:
        doc["questions"].append({
            "id": "Q-3a-критерий-пейринга", "part": "3a",
            "title": "т", "text": "т",
            "answer_format": {"type": "value"},
            "rule_refs": ["R-G"], "source_refs": [],
            "answer": {"raw": answer_raw, "status": "применён",
                       "revision": 14}})
    return doc


def test_вопрос_критерия_ставится_от_якоря_целевой():
    doc = _doc_with_goal()
    created = m.ensure_criterion_question(doc)
    assert created == ["Q-3a-критерий-пейринга"]
    q = doc["questions"][-1]
    assert q["rule_refs"] == ["R-G"] and q["source_refs"]
    assert m.ensure_criterion_question(doc) == [], "идемпотентность"


def test_критерий_0096_деривируется_из_ответа():
    from cdpm import export_solver as es
    crit = es.derive_pairing_criterion(_doc_with_goal(_ANS_0096))
    assert crit["kind"] == "rank_normalized_cost"
    assert crit["label"] == "NORMALIZED_COST"
    assert crit["source_rule"] == "R-CRIT-01"
    assert crit["rates"] == {"flight": 1.0, "work": 0.7}
    assert crit["rates_version"]["version"] \
        == "PARAM-T-01/base-1"
    assert crit["rates_version"]["effective_from"] == "2026-08-24"


def test_расходящиеся_ставки_рангов_не_сплющиваются():
    from cdpm import export_solver as es
    bad = _ANS_0096.replace('"ВП": {"flight": 1.0', 
                            '"ВП": {"flight": 2.0')
    assert es.derive_pairing_criterion(_doc_with_goal(bad)) is None


def test_ответ_без_версии_отвергается():
    from cdpm import export_solver as es
    bad = _ANS_0096.replace('"version": "PARAM-T-01/base-1",',
                            '')
    assert es.derive_pairing_criterion(_doc_with_goal(bad)) is None


def test_критерий_включает_оптимизацию_пейринга():
    # Живой брак кнопки №23 (2026-08-28): критерий лежал в пакете,
    # но матрица держала optimization снятым — режим не выбрался и
    # блок не дошёл до построителя. Критерий обязан включать режим.
    doc = _doc_with_goal(_ANS_0096)
    cell = m.readiness_matrix(doc)["pairing"]["optimization"]
    assert cell["ready"] is True
    assert cell["state"] == "готово"
    assert "критерий авиакомпании" in cell["note"]
    from cdpm.export_task_package import allowed_modes
    modes, _ = allowed_modes(doc)
    assert "optimization" in modes["pairing"]


def test_критерий_поверх_отказа_голосится():
    # Симметрия дыры из ревью ЦФ-1: более поздний ответ о критерии
    # перекрывает прежний отказ от оптимизации ГРОМКО, не молча.
    doc = _doc_with_goal(_ANS_0096)
    doc["questions"][0]["answer"] = {
        "raw": "любое допустимое решение", "status": "применён",
        "goal_decline": True, "revision": 1}
    cell = m.readiness_matrix(doc)["pairing"]["optimization"]
    assert cell["ready"] is True
    assert "ПОВЕРХ прежнего отказа" in cell["note"]


def test_без_критерия_оптимизация_не_включается():
    cell = m.readiness_matrix(
        _doc_with_goal())["pairing"]["optimization"]
    assert cell["ready"] is False


def test_текстовая_деривация_матрицу_не_включает():
    # Ревью пакета 1.11 (critical): текстовый фолбэк деривации не
    # проверяет блокеры и открытые вопросы — матрице разрешён только
    # строгий разбор ответа.
    doc = _doc_with_goal()
    doc["rules"].append({
        "id": "R-TXT", "zone": "PAIRING", "kind": "SOFT",
        "title": "Критерий пейринга",
        "statement": "минимизировать человеко-дни — 1 условная "
                     "единица"})
    from cdpm import export_solver as es
    assert es.derive_pairing_criterion(doc) is not None, \
        "текстовая деривация жива на экспортном пути"
    cell = m.readiness_matrix(doc)["pairing"]["optimization"]
    assert cell["ready"] is False, \
        "матрица не берёт текстовую деривацию"


def test_кривой_ответ_критерия_не_валит_матрицу():
    # Ревью пакета 1.11 (major): кривой JSON (пишет а/к или LLM) —
    # критерия нет, матрица живёт; ни TypeError, ни AttributeError.
    for bad in (
            _ANS_0096.replace('{"flight": 1.0, "work": 0.7}',
                              '{"work": 0.7}'),        # нет flight
            _ANS_0096.replace('"rates": {"КВС": {"flight": 1.0, '
                              '"work": 0.7},',
                              '"rates": [1,').replace(
                '"ВП": {"flight": 1.0, "work": 0.7}},', '2],'),
            _ANS_0096.replace('1.0', '"1,0"')):        # не число
        cell = m.readiness_matrix(
            _doc_with_goal(bad))["pairing"]["optimization"]
        assert cell["ready"] is False


def test_отказ_позже_критерия_не_перекрывается_молча():
    # Ревью пакета 1.11: доказанно более поздний по ревизии отказ
    # главнее критерия; матрица зовёт подтвердить актуальность.
    doc = _doc_with_goal(_ANS_0096)
    doc["questions"][0]["answer"] = {
        "raw": "любое допустимое решение", "status": "применён",
        "goal_decline": True, "revision": 20}
    cell = m.readiness_matrix(doc)["pairing"]["optimization"]
    assert cell["ready"] is False
    assert cell["state"] == "отказ подтверждён"
    assert "отказ НОВЕЕ" in cell["note"]


def test_закон_версии_камень_старых_пакетов():
    # Ревью пакета 1.11 (critical): пакет младше 1.11 судится своим
    # законом — optimization при пустых целевых снимается.
    from cdpm.export_task_package import allowed_modes
    doc = _doc_with_goal(_ANS_0096)
    assert "optimization" in allowed_modes(doc)[0]["pairing"]
    assert "optimization" in \
        allowed_modes(doc, spec_version="1.11")[0]["pairing"]
    assert "optimization" not in \
        allowed_modes(doc, spec_version="1.10")[0]["pairing"]


def test_открытый_вопрос_критерия_не_блокирует_выполнимость():
    doc = _doc_with_goal()
    m.ensure_criterion_question(doc)     # вопрос открыт, без ответа
    mx = m.readiness_matrix(doc)
    assert mx["pairing"]["feasibility-hard"]["ready"] is True
    assert mx["pairing"]["optimization"]["ready"] is False


def _doc_ladder_rules():
    return {"rules": [
        {"id": "R-A", "zone": "BOTH", "kind": "HARD",
         "title": "лестница +1",
         "statement": "Максимальное служебное время с отдыхом в "
                      "полете (доп. член экипажа, состав 2 чел.): "
                      "до 840 мин (класс 3), до 900 мин (класс 2), "
                      "до 960 мин (класс 1).",
         "sources": [{"sid": "S2", "anchor": "п.23.1"}]},
        {"id": "R-B", "zone": "PAIRING", "kind": "HARD",
         "title": "лестница обе ступени",
         "statement": "Может быть увеличено до 840/900/960 минут "
                      "(или 900/960/1020 для расширенного экипажа) "
                      "в зависимости от класса места отдыха.",
         "sources": [{"sid": "S5", "anchor": "табл.2"}]},
        {"id": "R-C", "zone": "PAIRING", "kind": "HARD",
         "title": "бонус",
         "statement": "Лимиты приложения 6 увеличиваются на 1 ч, "
                      "если рейс включает один сектор > 9 ч и не "
                      "превышает 2 сектора.",
         "sources": [{"sid": "S3", "anchor": "стр.7, абз.9"}]},
        {"id": "R-D", "zone": "PAIRING", "kind": "HARD",
         "title": "DH", "statement": "Допустим не более одного DH "
                                     "подряд для регулярного.",
         "sources": [{"sid": "S8", "anchor": "п.7"}]},
        {"id": "R-E", "zone": "BOTH", "kind": "CASE",
         "title": "Исключение типов", "statement": "Планирование исключённых типов — "
                                      "исключительно вручную.",
         "sources": [{"sid": "S8", "anchor": "абз.6"}]},
    ], "objectives": [], "questions": []}


def test_форма_лестницы_собирается_из_правил():
    # Запрос владельца 2026-08-28: лестница ДОКУМЕНТОВ двумя
    # ступенями, без 18 ч; бонус за длинный сектор — условием.
    from cdpm import threshold_forms as tf
    ladder = tf.assemble_extended_ladder(_doc_ladder_rules())
    assert ladder["unit"] == "minutes"
    steps = {s["extra_crew"]: s for s in ladder["steps"]}
    assert steps[1]["by_class_min"] == {"1": 960, "2": 900, "3": 840}
    assert steps[2]["by_class_min"] == {"1": 1020, "2": 960, "3": 900}
    assert steps[1]["rule"] == "R-A", "явные классы главнее скобок"
    assert ladder["long_sector_bonus"]["add_min"] == 60
    assert ladder["long_sector_bonus"]["condition"] == {
        "kind": "one_sector_over_h", "value": 9, "max_sectors": 2}
    assert "1080" not in str(ladder["steps"])
    forms = tf.build_forms(_doc_ladder_rules())
    assert forms["extended_ladder"]["steps"] == ladder["steps"]


def _synthetic_topics_file(tmp_path, monkeypatch):
    """Синтетическая таблица тем контура — значения НАРОЧНО чужие
    (тест обезличенности механизма: принцип владельца 2026-08-29
    «шаблон принимает любые правила того же типа»)."""
    import json as _j
    p = tmp_path / "ensure-questions.json"
    p.write_text(_j.dumps({
        "form_version": 1, "customer": "синтетика",
        "_comment": "тестовая таблица",
        "topics": [
            {"key": "условия-синт-отдых",
             "trigger_regex":
                 r"отдых\w*\s+в\s+пол[её]те|мест\w*\s+(?:для\s+)?отдыха",
             "title": "Синтетическая лестница",
             "text": "Подтвердите лестницу 7/8/9 часов.",
             "note": "лестница в часах"},
            {"key": "условия-синт-типы",
             "trigger_regex": r"вручную",
             "title": "Синтетическая граница типов",
             "text": "Подтвердите границу перечня типов Q7/Q9.",
             "note": "выбор границы"},
        ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_ENSURE_QUESTIONS", str(p))
    return p


def test_вопросы_уточнений_ставятся_и_идемпотентны(tmp_path,
                                                   monkeypatch):
    _synthetic_topics_file(tmp_path, monkeypatch)
    doc = _doc_ladder_rules()
    created = m.ensure_pairing_clarification_questions(doc)
    assert sorted(created) == [
        "Q-3a-условия-синт-отдых",
        "Q-3a-условия-синт-типы"]
    assert m.ensure_pairing_clarification_questions(doc) == []
    by_id = {q["id"]: q for q in doc["questions"]}
    q1 = by_id["Q-3a-условия-синт-отдых"]
    assert "R-A" in q1["rule_refs"] and "R-B" in q1["rule_refs"]
    assert "7/8/9" in q1["text"]
    q2 = by_id["Q-3a-условия-синт-типы"]
    assert q2["rule_refs"] == ["R-E"]


def test_вопросы_уточнений_без_носителей_не_ставятся(tmp_path,
                                                     monkeypatch):
    _synthetic_topics_file(tmp_path, monkeypatch)
    doc = {"rules": [], "objectives": [], "questions": []}
    assert m.ensure_pairing_clarification_questions(doc) == []


def test_вопросы_уточнений_без_файла_контура_механизм_спит():
    # autouse-изоляция задаёт пустой CDPM_ENSURE_QUESTIONS — файла
    # нет, механизм честно спит даже при живых носителях.
    doc = _doc_ladder_rules()
    assert m.ensure_pairing_clarification_questions(doc) == []


def test_вопросы_уточнений_кривой_файл_честный_отказ(tmp_path,
                                                     monkeypatch):
    p = tmp_path / "ensure-questions.json"
    p.write_text('{"topics": [{"key": "без-регекса"}]}',
                 encoding="utf-8")
    monkeypatch.setenv("CDPM_ENSURE_QUESTIONS", str(p))
    doc = _doc_ladder_rules()
    import pytest as _pytest
    with _pytest.raises(ValueError, match="trigger_regex|поле"):
        m.ensure_pairing_clarification_questions(doc)


def _doc_consensus():
    return {"rules": [
        # Синтетический префикс фантомов (решение владельца 2026-08-29:
        # координата свода живёт параметром контура, не литералом).
        {"id": "R-T4-табл-2-строка-27-кол-2-вл-табл-1-строка-1-кол-4",
         "zone": "PAIRING", "kind": "HARD", "title": "фантом",
         "statement": "Максимальное количество секторов в смене не "
                      "должно превышать 5.",
         "params": [{"name": "max_landings_per_duty", "value": 5,
                     "unit": "count", "as_written": "5 секторов",
                     "relation": "max"}],
         "sources": [{"sid": "S4", "anchor": "т2с27"}]},
        {"id": "R-W1", "zone": "PAIRING", "kind": "HARD",
         "title": "МЦА нет попадания",
         "statement": "Максимум 5 секторов при отсутствии попадания "
                      "в окно минимальной циркадной активности.",
         "sources": [{"sid": "S2", "anchor": "стр.6, абз.18"}]},
        {"id": "R-W2", "zone": "PAIRING", "kind": "HARD",
         "title": "МЦА ≤ 2ч",
         "statement": "Максимум 4 сектора при попадании в окно "
                      "минимальной циркадной активности ≤ 120 мин.",
         "sources": [{"sid": "S2", "anchor": "стр.6, абз.19"}]},
        {"id": "R-W3", "zone": "PAIRING", "kind": "HARD",
         "title": "МЦА > 2ч",
         "statement": "Максимум 2 сектора при попадании в окно "
                      "минимальной циркадной активности более чем "
                      "на 2 часа.",
         "sources": [{"sid": "S2", "anchor": "стр.6, абз.20"}]},
        {"id": "R-N1", "zone": "BOTH", "kind": "DEF",
         "title": "ночь",
         "statement": "Служебное время ночью — окно [22:00, 06:00) "
                      "в часовом поясе акклиматизации члена экипажа.",
         "params": [{"name": "night_duty_start", "value": 1320,
                     "unit": "time_of_day_minutes",
                     "as_written": "22:00", "relation": "eq",
                     "tz": "base-local"}],
         "sources": [{"sid": "S2", "anchor": "стр.3, абз.14"}]},
        {"id": "R-F1", "zone": "PAIRING", "kind": "DATA",
         "title": "перечень",
         # Типы — синтетические (зачистка 2026-08-29: значения
         # заказчика в фикстурах заменены чужими; механизм обязан
         # принять любой перечень того же типа).
         "statement": "Все леги на воздушных судах типов "
                      "'Q7|Q9|QW2|QT3|QJ1' маркируются как "
                      "deadhead (перегон/заброска).",
         "sources": [{"sid": "S6", "anchor": "т4с18"}]},
        {"id": "R-E1", "zone": "PAIRING", "kind": "HARD",
         "title": "ранний вылет",
         "statement": "Пейринги с вылетом из HUB до 04:00 UTC не "
                      "могут содержать разворот в базовом аэропорту",
         "sources": [{"sid": "S6", "anchor": "т1с17"}]},
    ], "objectives": [], "questions": []}


def _migration_params_contour(tmp_path, monkeypatch):
    """Синтетический носитель параметров миграций (префикс фантомов
    под синтетический id фикстуры)."""
    import json as _j
    p = tmp_path / "migration-params.json"
    p.write_text(_j.dumps({
        "form_version": 1, "customer": "синтетика", "_comment": "т",
        "frms_phantom_prefix":
            "R-T4-табл-2-строка-27-кол-2-вл-табл-1-строка-1-кол-"},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_MIGRATION_PARAMS", str(p))


def test_фантомы_без_носителя_контура_миграция_спит():
    # autouse-изоляция: параметров миграций нет — фантомная чистка
    # честно спит даже при совпадающем id (fail-closed).
    doc = _doc_consensus()
    assert m.retire_frms_phantom_rules(doc) == []
    assert doc["rules"][0]["kind"] == "HARD"


def test_консенсус_миграции_и_формы(tmp_path, monkeypatch):
    # Консенсус CDPM/COSMOS 2026-08-28: фантомы в OUT, params
    # секторов МЦА подняты, пояс ночи — правда документа; формы
    # wocl/fleet/early собираются.
    from cdpm import threshold_forms as tf
    _migration_params_contour(tmp_path, monkeypatch)
    doc = _doc_consensus()
    assert len(m.retire_frms_phantom_rules(doc)) == 1
    phantom = doc["rules"][0]
    assert phantom["kind"] == "OUT"
    assert "неприменима по условию первоисточника" in phantom["note"]
    assert m.retire_frms_phantom_rules(doc) == [], "идемпотентность"
    assert len(m.restore_wocl_sector_params(doc)) == 3
    by = {r["id"]: r for r in doc["rules"]}
    assert by["R-W1"]["params"][0]["value"] == 5
    assert by["R-W3"]["params"][0]["value"] == 2
    assert len(m.restore_night_window_tz(doc)) == 1
    assert by["R-N1"]["params"][0]["tz"] == "acclimatized"
    assert m.restore_night_window_tz(doc) == [], "идемпотентность"
    wocl = tf.assemble_wocl_sector_limits(doc)
    assert wocl["limits"] == {"no_touch": 5,
                              "touch_le_threshold": 4,
                              "touch_gt_threshold": 2}
    assert wocl["window"] == {"from_min": 120, "to_min": 360,
                              "tz": "acclimatized"}
    fleet = tf.assemble_fleet_dh_marked(doc)
    assert fleet["types"] == ["Q7", "Q9", "QW2", "QT3", "QJ1"]
    assert fleet["semantics"] == "master_data_marking_description"
    assert "ЗАПРЕЩЕНО" in fleet["warning"]
    early = tf.assemble_early_base_departure(doc)
    assert early["base"] == "HUB" and early["cutoff_min"] == 240
    assert "отклонено" in early["note"]
    forms = tf.build_forms(doc)
    for k in ("wocl_sector_limits", "fleet_dh_marked",
              "early_base_departure"):
        assert k in forms, k
    # Фантом в OUT не кормит формы и валидацию не рвёт.
    assert m.validate_param(by["R-N1"]["params"][0], "DEF") == []


def test_вопросы_консенсуса_ставятся(tmp_path, monkeypatch):
    import json as _j
    p = tmp_path / "ensure-questions.json"
    p.write_text(_j.dumps({
        "form_version": 1, "customer": "синтетика",
        "_comment": "т",
        "topics": [
            {"key": "условия-синт-мца",
             "trigger_regex": r"циркадн",
             "title": "Границы окна", "text": "Подтвердите границы.",
             "note": "границы"},
            {"key": "условия-синт-ранний",
             "trigger_regex": r"разворот\s+в\s+базовом",
             "title": "Статус правила", "text": "Действует ли?",
             "note": "действует/нет"},
        ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_ENSURE_QUESTIONS", str(p))
    doc = _doc_consensus()
    created = m.ensure_pairing_clarification_questions(doc)
    assert "Q-3a-условия-синт-мца" in created
    assert "Q-3a-условия-синт-ранний" in created


def test_критерий_generic_метка_канон_1_13():
    """Ревизия 1.13: канон метки — generic NORMALIZED_COST (без
    номера правила заказчика); легаси-метка старых ответов законна
    (существующие тесты выше), обе дают одинаковый разбор."""
    generic = _ANS_0096.replace('"criterion": "NORMALIZED_COST_0096"',
                                '"criterion": "NORMALIZED_COST"')
    doc_a = _doc_with_goal(answer_raw=_ANS_0096)
    doc_b = _doc_with_goal(answer_raw=generic)
    ca = m._criterion_from_answer(doc_a)
    cb = m._criterion_from_answer(doc_b)
    assert ca is not None and cb is not None
    assert cb["source_rule"] == "R-CRIT-01"
    ca2 = {k: v for k, v in ca.items() if k != "as_written"}
    cb2 = {k: v for k, v in cb.items() if k != "as_written"}
    assert ca2 == cb2


# ---------------------------------------------------------------------------
# Ремни ночи демо 2026-08-29→30: детерминированное применение ответов
# ---------------------------------------------------------------------------

_ANS_MPD = ('Минимум человеко-дней. {"criterion": "MIN_PERSON_DAYS", '
            '"rate_per_duty": 100, "rule": "O-CRIT-02", '
            '"version": "M-036/demo-1", '
            '"effective_from": "2026-08-01"}')


def test_парсер_длительности_детерминирован():
    assert m._parse_duration_minutes("10 часов") == 600
    assert m._parse_duration_minutes("40 минут") == 40
    assert m._parse_duration_minutes("10 ч 30 мин") == 630
    assert m._parse_duration_minutes("10:30") == 630
    assert m._parse_duration_minutes("десять часов") is None
    assert m._parse_duration_minutes("") is None


def test_критерий_min_person_days_разбирается():
    crit = m._criterion_from_answer(_doc_with_goal(_ANS_MPD))
    assert crit["kind"] == "min_person_days"
    assert crit["rate_per_duty"] == 100
    assert crit["source_rule"] == "O-CRIT-02"
    assert crit["rates_version"]["version"] == "M-036/demo-1"
    cell = m.readiness_matrix(
        _doc_with_goal(_ANS_MPD))["pairing"]["optimization"]
    assert cell["ready"] is True


def test_критерий_min_person_days_кривые_формы():
    # Контракт COSMOS: ставка — целое > 0 (не bool), версия и дата
    # обязательны; кривизна = None, не падение.
    for bad in (
            _ANS_MPD.replace('"rate_per_duty": 100',
                             '"rate_per_duty": 100.5'),
            _ANS_MPD.replace('"rate_per_duty": 100',
                             '"rate_per_duty": true'),
            _ANS_MPD.replace('"rate_per_duty": 100',
                             '"rate_per_duty": 0'),
            _ANS_MPD.replace('"version": "M-036/demo-1", ', ''),
            _ANS_MPD.replace('"effective_from": "2026-08-01"',
                             '"effective_from": ""')):
        assert m._criterion_from_answer(_doc_with_goal(bad)) is None


def test_добор_критерия_после_текстового_ответа():
    # Ремень (б): применённый ответ без машинного блока порождает
    # вопрос-ДОБОР (прецедент повторного запроса весов); ответ добора
    # с блоком закрывает семейство.
    doc = _doc_with_goal("минимум человеко-дней, без цифр")
    created = m.ensure_criterion_question(doc)
    assert created == ["Q-3a-критерий-пейринга-2"]
    assert m.ensure_criterion_question(doc) == [], \
        "открытый добор не плодится"
    q2 = doc["questions"][-1]
    assert "JSON" in q2["text"] and q2["rule_refs"] == ["R-G"]
    q2["answer"] = {"raw": _ANS_MPD, "status": "применён",
                    "revision": 3}
    assert m._criterion_from_answer(doc)["kind"] == "min_person_days"
    assert m.ensure_criterion_question(doc) == [], \
        "носитель найден — добор закрыт"


def test_добор_не_ставится_пока_вопрос_открыт():
    doc = _doc_with_goal()
    assert m.ensure_criterion_question(doc) \
        == ["Q-3a-критерий-пейринга"]
    assert m.ensure_criterion_question(doc) == []


def test_внос_значения_темы_детерминирован(tmp_path, monkeypatch):
    # Ремень (а): value-ответ вопроса темы носителя с param_name
    # вносится параметром в правила rule_refs кодом; идемпотентно;
    # неразобранное значение честно пропускается.
    import json as _j
    p = tmp_path / "topics.json"
    p.write_text(_j.dumps({
        "form_version": 1, "customer": "синтетика", "_comment": "т",
        "topics": [{"key": "условия-синт-отдых",
                    "trigger_regex": r"отдых",
                    "title": "т", "text": "т", "note": "значение",
                    "param_name": "min_rest_synth",
                    "param_unit": "duration_minutes",
                    "param_relation": "min"}]},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_CONDITIONS_TOPICS", str(p))
    doc = {"meta": {"revision": 2}, "rules": [
        {"id": "R-T1", "zone": "PAIRING", "kind": "HARD",
         "title": "Отдых", "statement": "отдых вне базы"}],
        "questions": [{
            "id": "Q-3a-условия-синт-отдых", "part": "3a",
            "title": "т", "text": "т",
            "answer_format": {"type": "value"},
            "rule_refs": ["R-T1"], "source_refs": [],
            "answer": {"raw": "10 часов", "status": "применён",
                       "revision": 2}}]}
    notes = m.apply_topic_value_answers(doc)
    assert len(notes) == 1 and "min_rest_synth=600" in notes[0]
    par = doc["rules"][0]["params"][0]
    assert par["name"] == "min_rest_synth" and par["value"] == 600
    assert par["unit"] == "duration_minutes"
    assert par["relation"] == "min"
    assert m.apply_topic_value_answers(doc) == [], "идемпотентность"
    # Неразобранное значение — пропуск без выдумки.
    doc["rules"][0].pop("params")
    doc["questions"][0]["answer"]["raw"] = "десять часов"
    assert m.apply_topic_value_answers(doc) == []
    assert "params" not in doc["rules"][0]


def test_внос_значения_спит_без_носителя(monkeypatch):
    monkeypatch.setenv("CDPM_CONDITIONS_TOPICS", "")
    doc = {"meta": {"revision": 2}, "rules": [], "questions": []}
    assert m.apply_topic_value_answers(doc) == []


def test_след_вноса_короткий_и_канонизируется(tmp_path, monkeypatch):
    # Живой отказ круга 2 run-0064 (ночь демо): ответ с
    # хвостом-пояснением «0 минут (все времена…)» ложился следом
    # целиком и не канонизировался контрактом. След — только
    # совпавший фрагмент; параметр проходит validate_param.
    import json as _j
    p = tmp_path / "topics.json"
    p.write_text(_j.dumps({
        "form_version": 1, "customer": "синтетика", "_comment": "т",
        "topics": [{"key": "условия-синт-шкала",
                    "trigger_regex": r"шкал",
                    "title": "т", "text": "т", "note": "минуты",
                    "param_name": "base_tz_offset_min",
                    "param_unit": "duration_minutes",
                    "param_relation": "eq"}]},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_CONDITIONS_TOPICS", str(p))
    doc = {"meta": {"revision": 2}, "rules": [
        {"id": "R-T2", "zone": "PAIRING", "kind": "HARD",
         "title": "Шкала", "statement": "шкала времени"}],
        "questions": [{
            "id": "Q-3a-условия-синт-шкала", "part": "3a",
            "title": "т", "text": "т",
            "answer_format": {"type": "value"},
            "rule_refs": ["R-T2"], "source_refs": [],
            "answer": {"raw": "0 минут (все времена данных уже в "
                              "местном времени главной базы)",
                       "status": "применён", "revision": 2}}]}
    notes = m.apply_topic_value_answers(doc)
    assert len(notes) == 1
    par = doc["rules"][0]["params"][0]
    assert par["value"] == 0 and par["as_written"] == "0 минут"
    assert m.canon_minutes(par["as_written"]) == 0
    assert m.validate_param(par, "HARD") == []
    # Составной след тоже канонизируем.
    assert m._parse_duration_span("не менее 10 ч 30 мин отдыха") \
        == (630, "10 ч 30 мин")


def test_тема_определение_повышает_вид_до_def(tmp_path, monkeypatch):
    """Прод-демо run-0001 (30.08): извлечение классифицировало
    правило шкалы видом DATA — потребитель применяет параметры
    только HARD и DEF, и параметр-определение был мёртв. Тема с
    param_rule_kind=DEF повышает вид носителя; не-DATA не трогается;
    идемпотентно."""
    import json as _j
    p = tmp_path / "topics.json"
    p.write_text(_j.dumps({
        "form_version": 1, "customer": "синтетика", "_comment": "т",
        "topics": [{"key": "условия-синт-шкала2",
                    "trigger_regex": r"шкал",
                    "title": "т", "text": "т", "note": "минуты",
                    "param_name": "base_tz_offset_min",
                    "param_unit": "duration_minutes",
                    "param_relation": "eq",
                    "param_rule_kind": "DEF"}]},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_CONDITIONS_TOPICS", str(p))
    doc = {"meta": {"revision": 2}, "rules": [
        {"id": "R-TZ", "zone": "BOTH", "kind": "DATA",
         "title": "Шкала", "statement": "шкала времени планирования"},
        {"id": "R-TZ2", "zone": "BOTH", "kind": "HARD",
         "title": "Шкала2", "statement": "шкала запасная"}],
        "questions": [{
            "id": "Q-3a-условия-синт-шкала2", "part": "3a",
            "title": "т", "text": "т",
            "answer_format": {"type": "value"},
            "rule_refs": ["R-TZ", "R-TZ2"], "source_refs": [],
            "answer": {"raw": "0 минут", "status": "применён",
                       "revision": 2}}]}
    notes = m.apply_topic_value_answers(doc)
    assert any("DATA→DEF" in n for n in notes), notes
    assert doc["rules"][0]["kind"] == "DEF"
    assert "повышен DATA→DEF" in doc["rules"][0]["note"]
    assert doc["rules"][1]["kind"] == "HARD", "не-DATA не трогается"
    assert m.apply_topic_value_answers(doc) == [], "идемпотентность"


def test_тема_молчит_при_полноте_носителей(monkeypatch, tmp_path):
    """Мини-пакет 31.08 (живой прогон нано): универсальная тема с
    машинным параметром (стыковка, состав экипажа) живёт в носителе
    НАВСЕГДА — вопрос ставится только пока хоть один носитель без
    параметра; при полном покрытии тема молчит."""
    import json as _json
    carrier = tmp_path / "topics.json"
    carrier.write_text(_json.dumps({
        "form_version": 1, "customer": "т",
        "topics": [{
            "key": "условия-состав-экипажа",
            "trigger_regex": r"состав\w*\s+экипаж",
            "title": "Состав экипажа", "text": "Сколько пилотов?",
            "note": "число", "param_name": "crew_size",
            "param_unit": "count", "param_relation": "eq"}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_CONDITIONS_TOPICS", str(carrier))
    doc = {"meta": {"revision": 1}, "rules": [
        {"id": "R-1", "kind": "HARD", "zone": "PAIRING",
         "title": "Состав", "statement": "Состав экипажа — два "
                                         "пилота.",
         "sources": [{"sid": "S1", "anchor": "п.1"}],
         "params": [{"name": "crew_size", "value": 2,
                     "unit": "count", "as_written": "два пилота",
                     "relation": "eq"}]}], "questions": []}
    assert m.ensure_conditions_questions(doc) == [], \
        "носитель полон — тема молчит"
    # Второй носитель БЕЗ параметра — вопрос встаёт.
    doc["rules"].append(
        {"id": "R-2", "kind": "HARD", "zone": "PAIRING",
         "title": "Состав диспетчеров",
         "statement": "Состав экипажа кабинного — по типу ВС.",
         "sources": [{"sid": "S1", "anchor": "п.2"}]})
    created = m.ensure_conditions_questions(doc)
    assert created == ["Q-3a-условия-состав-экипажа"]
