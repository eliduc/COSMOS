# -*- coding: utf-8 -*-
"""Семьи мягких правил и путь к весу (контракт P-N-SHD 2026-08-30).

Живой корень прод-демо: три мягких правила остались без привязки к
шаблонам решателя (соотнесение картой по координатным id дрейфовало
между извлечениями), а правило разброса кредита ушло в пакет с
weight=null без блокера («взвешенность» судилась членством в
rule_refs применённого вопроса части 3, назвавшего порог, но не
вес). Классовое лечение: закрытый словарь семей export_solver.family
+ путь к весу = фактический вес либо отказ от оптимизации."""
import json

from cdpm import model as m
from cdpm import threshold_forms as tf


def _soft(rid, statement, **kw):
    r = {"id": rid, "zone": "PAIRING", "kind": "SOFT",
         "title": rid, "statement": statement,
         "sources": [{"sid": "S1", "anchor": "т1"}]}
    r.update(kw)
    return r


def _base_doc(*rules):
    return {"meta": {"customer": "тест", "mode": 1,
                     "cdpm_version": "т", "revision": 1},
            "sources": [{"sid": "S1", "file": "док.md",
                         "sha256": "0" * 64}],
            "rules": list(rules), "objectives": [], "questions": []}


def test_классификатор_трёх_семей_и_честный_пробел():
    """Три механики закрытого словаря узнаются по классовым признакам
    формулировки; правило без семьи (продление с согласия) остаётся
    БЕЗ export_solver — честный пробел покрытия, не карта-носитель."""
    doc = _base_doc(
        _soft("R-LAY", "Ночёвки экипажа вне базы приписки "
                       "нежелательны; за каждую такую ночёвку "
                       "начисляется штраф величиной 10.", weight=10),
        _soft("R-CAL", "Ночёвка вне базовой станции, календарный "
                       "день которой по местному времени базовой "
                       "станции приходится на пятницу или субботу, "
                       "особенно нежелательна.", weight=25),
        _soft("R-BAL", "Кредитные часы желательно распределять "
                       "между членами экипажа одной базы равномерно; "
                       "за разброс более 10 часов между членами "
                       "экипажа одной базы начисляется штраф.",
              weight=5),
        _soft("R-EXT", "Смена может быть продлена до 13:00 «с "
                       "согласия»; штраф за превышение 11:30 при "
                       "мягкой трактовке продления.", weight=10),
        _soft("R-GAP", "Слётанность пары пилотов желательна: "
                       "менять напарника нежелательно.", weight=2))
    notes = m.assign_soft_families(doc)
    by = {r["id"]: r.get("export_solver") for r in doc["rules"]}
    assert by["R-LAY"] == {"family": "layover_penalty"}
    assert by["R-CAL"] == {"family": "calendar_windows",
                           "days": ["friday", "saturday"],
                           "tz": "base-local"}
    assert by["R-BAL"] == {"family": "balanced_workload",
                           "group": "base"}
    # Первое живое расширение словаря каноническим процессом P-N-SHD
    # (ночь 30→31.08): стоп → заявка → шаблон → семья.
    assert by["R-EXT"] == {"family": "duty_extension_penalty"}
    assert by["R-GAP"] is None, "семья без шаблона не выдумывается"
    assert len(notes) == 5  # 4 семьи + порог разброса
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    assert m.assign_soft_families(doc) == [], "идемпотентность"


def test_порог_разброса_параметром_закрытого_словаря():
    doc = _base_doc(
        _soft("R-BAL", "Кредитные часы распределять равномерно; за "
                       "разброс более 10 часов между членами "
                       "экипажа одной базы начисляется штраф.",
              weight=5))
    m.assign_soft_families(doc)
    par = doc["rules"][0]["params"][0]
    assert par["name"] == "workload_spread_threshold"
    assert par["value"] == 10 and par["unit"] == "credit_hours"
    assert par["relation"] == "max"
    assert par["as_written"] == "разброс более 10 часов"
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_семья_снимается_с_потерей_мягкости():
    doc = _base_doc(
        _soft("R-LAY", "Ночёвки вне базы нежелательны, штраф.",
              weight=10))
    m.assign_soft_families(doc)
    assert doc["rules"][0]["export_solver"]["family"] \
        == "layover_penalty"
    doc["rules"][0]["kind"] = "OUT"
    doc["rules"][0].pop("weight")
    notes = m.assign_soft_families(doc)
    assert "export_solver" not in doc["rules"][0]
    assert notes and "семья снята" in notes[0]


def test_сквозная_проверка_весов_ловит_позднюю_мягкую_строку():
    """Живой корень: правило стало мягким ПОСЛЕ применения
    Q-3b-GOAL (ответ на вопрос порога назвал порог, но не вес) —
    ни GOAL, ни повторный запрос весов его не видели. Сквозной
    проход обеих частей обязан создать Q-3b-GOAL-W с блокером."""
    doc = _base_doc(
        {"id": "R-H1", "zone": "ROSTERING", "kind": "HARD",
         "title": "R-H1", "statement": "жёсткая норма",
         "sources": [{"sid": "S1", "anchor": "т0"}]},
        _soft("R-BAL", "Кредитные часы распределять равномерно; "
                       "разброс более 10 часов штрафуется.",
              zone="ROSTERING"))
    doc["questions"] = [
        {"id": "Q-3b-GOAL", "part": "3b", "title": "Целевая",
         "text": "Назовите целевую функцию ростеринга.",
         "answer_format": {"type": "registry"},
         "rule_refs": ["R-H1"], "source_refs": [{"sid": "S1", "anchor": "т1"}],
         "answer": {"raw": "мин. стоимость", "status": "применён",
                    "revision": 1, "goal_decline": False}},
        {"id": "Q-3b-порог", "part": "3b", "title": "Порог разброса",
         "text": "Назовите порог существенного разброса.",
         "answer_format": {"type": "value", "note": "часы"},
         "rule_refs": ["R-BAL"], "source_refs": [{"sid": "S1", "anchor": "т1"}],
         "answer": {"raw": "10 часов", "status": "применён",
                    "revision": 1}}]
    # До лечения: строка «взвешена» членством в rule_refs — дыра.
    created = m.ensure_goal_questions_sweep(doc)
    assert created == ["Q-3b-GOAL-W"]
    rule = doc["rules"][1]
    assert rule["blockers"] == ["Q-3b-GOAL-W"]
    q = next(x for x in doc["questions"] if x["id"] == "Q-3b-GOAL-W")
    assert q["rule_refs"] == ["R-BAL"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    # Второй проход законно ставит добор ЦЕЛЕВОЙ (целевых зоны нет,
    # отказа нет — механика ночи 30→31.08); третий — пусто.
    assert m.ensure_goal_questions_sweep(doc) == ["Q-3b-GOAL-T"]
    assert m.ensure_goal_questions_sweep(doc) == [], "идемпотентность"


def test_отказ_от_оптимизации_законен_без_веса():
    """goal_decline применённого GOAL — законный путь строки без
    веса: сквозная проверка вопрос не плодит, валидатор чист."""
    doc = _base_doc(
        _soft("R-SOFT", "Ночёвки вне базы нежелательны.",
              zone="ROSTERING"))
    doc["questions"] = [
        {"id": "Q-3b-GOAL", "part": "3b", "title": "Целевая",
         "text": "т", "answer_format": {"type": "registry"},
         "rule_refs": ["R-SOFT"], "source_refs": [{"sid": "S1", "anchor": "т1"}],
         "answer": {"raw": "любое допустимое решение",
                    "status": "применён", "revision": 1,
                    "goal_decline": True}}]
    assert m.ensure_goal_questions_sweep(doc) == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_мягкая_строка_без_веса_и_без_блокера_красна():
    """Инвариант валидатора: членство в rule_refs применённого
    вопроса части 3 БЕЗ веса и БЕЗ отказа — больше не путь к весу."""
    doc = _base_doc(_soft("R-SOFT", "Штрафная строка без веса."))
    doc["questions"] = [
        {"id": "Q-3a-любой", "part": "3a", "title": "т", "text": "т",
         "answer_format": {"type": "value"},
         "rule_refs": ["R-SOFT"], "source_refs": [{"sid": "S1", "anchor": "т1"}],
         "answer": {"raw": "порог 5", "status": "применён",
                    "revision": 1}}]
    probs = m.validate_model(doc)
    assert any("SOFT-правило без блокировки" in p for p in probs)


def test_форма_окон_проекция_семьи_без_регэкспов():
    """Сборщик календарной формы читает export_solver семьи, не
    формулировку: запись без семьи форму не даёт, дни — из записи."""
    doc = _base_doc(
        _soft("R-CAL", "Ночёвка вне базовой станции по местному "
                       "времени базовой станции в пятницу "
                       "нежелательна.", weight=7))
    assert tf.assemble_calendar_window_penalties(doc) == [], \
        "без классификатора формы нет — двойного разбора текста нет"
    m.assign_soft_families(doc)
    got = tf.assemble_calendar_window_penalties(doc)
    assert len(got) == 1
    assert got[0]["days"] == ["friday"]
    assert got[0]["tz"] == "base-local"
    assert got[0]["weight"] == 7 and got[0]["rule"] == "R-CAL"


def test_целевая_минимизации_событий_мигрирует_мягким_правилом():
    """Живой круг полноты run-0002 (30.08, согласовано с решателем —
    вариант «а»): целевая «минимизировать суммарное число ночей вне
    базы» — неполная форма мягкого штрафа за событие (family
    layover_penalty): контракт P-N-SHD исполняет её SOFT-правилом с
    весом. Миграция возвращает правило, классификатор даёт семью,
    сквозная проверка вешает вопрос веса. Настоящая целевая
    (стоимость) не трогается."""
    doc = _base_doc()
    doc["objectives"] = [
        {"id": "O-S1-табл-3-строка-6-кол-3", "zone": "BOTH",
         "direction": "minimize", "title": "Ночи вне базы",
         "statement": "Минимизировать суммарное число ночей вне "
                      "базы за расчётный период.",
         "sources": [{"sid": "S1", "anchor": "т3с6"}]},
        {"id": "O-COST", "zone": "PAIRING", "direction": "minimize",
         "title": "Стоимость",
         "statement": "Минимизировать суммарную стоимость решения.",
         "sources": [{"sid": "S1", "anchor": "т9с1"}]}]
    notes = m.restore_soft_penalty_rules(doc)
    assert notes and "мягким правилом" in notes[0]
    assert [o["id"] for o in doc["objectives"]] == ["O-COST"]
    rule = next(r for r in doc["rules"]
                if r["id"] == "R-S1-табл-3-строка-6-кол-3")
    assert rule["kind"] == "SOFT"
    m.assign_soft_families(doc)
    assert rule["export_solver"] == {"family": "layover_penalty"}
    created = m.ensure_goal_questions_sweep(doc)
    assert created, "мягкое правило без веса обязано получить вопрос"
    assert rule.get("blockers")
    assert m.restore_soft_penalty_rules(doc) == [], "идемпотентность"


def test_целевая_с_машинной_метрикой_не_мигрирует():
    """Лестница Ш-2 (сухая сборка ВМП нано, 1.09): настоящая целевая
    ростеринга с machine-метрикой закрытого словаря
    (credit_hours_spread) и «семейной» формулировкой («минимум
    разброса…») мигрировала в бесвесное SOFT — зона теряла
    optimization. Машинная метрика = настоящая целевая по
    определению: миграции не подлежит."""
    doc = _base_doc()
    doc["objectives"] = [
        {"id": "O-ROST", "zone": "ROSTERING",
         "direction": "minimize", "title": "Равномерность",
         "statement": "Минимум разброса кредитных часов по ростеру "
                      "между сотрудниками базы.",
         "metric": "credit_hours_spread",
         "sources": [{"sid": "S1", "anchor": "т3с7"}]}]
    assert m.restore_soft_penalty_rules(doc) == []
    assert [o["id"] for o in doc["objectives"]] == ["O-ROST"]
    assert not any(r["id"] == "R-ROST" for r in doc.get("rules", []))


def test_чистка_целевых_осколков_применения():
    """Живой круг полноты run-0002 (ночь 30→31.08): применение ответа
    целевой рождало целевые-осколки пояснений («вес штрафа = 15»,
    «разброс, вес 3 — уже взвешено») — вторая ветка миграции
    легализовала осколок правилом-ДУБЛЕМ настоящего правила разброса.
    Чистка: целевая с origin.question и БЕЗ machine-метрики снимается
    ДО миграции; целевая ответа С метрикой и легаси-целевая без
    origin не тронуты."""
    doc = _base_doc(
        _soft("R-BAL", "Кредитные часы распределять равномерно; за "
                       "разброс более 10 часов между членами "
                       "экипажа одной базы начисляется штраф.",
              weight=3, zone="ROSTERING"))
    doc["objectives"] = [
        {"id": "O-SHARD-1", "zone": "ROSTERING",
         "direction": "minimize", "title": "Осколок продления",
         "statement": "минимизировать суммарный штраф за превышение "
                      "порога длительности смены, вес штрафа = 15",
         "sources": [{"sid": "S1", "anchor": "т1"}],
         "origin": {"question": "Q-3b-GOAL", "revision": 4}},
        {"id": "O-SHARD-2", "zone": "ROSTERING",
         "direction": "minimize", "title": "Осколок разброса",
         "statement": "минимизировать разброс кредитных часов, "
                      "вес = 3 (уже взвешено ранее)",
         "sources": [{"sid": "S1", "anchor": "т1"}],
         "origin": {"question": "Q-3b-GOAL", "revision": 4}},
        {"id": "O-REAL-ANS", "zone": "PAIRING",
         "direction": "minimize", "title": "Настоящая из ответа",
         "statement": "minimize total_pairing_cost",
         "metric": "total_pairing_cost",
         "sources": [{"sid": "S1", "anchor": "т1"}],
         "origin": {"question": "Q-3a-GOAL", "revision": 4}},
        {"id": "O-LEGACY", "zone": "PAIRING",
         "direction": "minimize", "title": "Легаси без метрики",
         "statement": "минимизировать простой без метрики",
         "sources": [{"sid": "S1", "anchor": "т1"}]}]
    notes = m.restore_soft_penalty_rules(doc)
    left = [o["id"] for o in doc["objectives"]]
    assert "O-SHARD-1" not in left and "O-SHARD-2" not in left
    assert "O-REAL-ANS" in left and "O-LEGACY" in left
    assert sum("осколок" in n for n in notes) == 2
    # Правило-дубль разброса НЕ родилось: настоящая строка одна.
    bal = [r for r in doc["rules"]
           if (r.get("export_solver") or {}).get("family")
           == "balanced_workload" or "разброс" in
           str(r.get("statement", ""))]
    assert [r["id"] for r in bal] == ["R-BAL"]
    assert m.restore_soft_penalty_rules(doc) == [], "идемпотентность"


def test_добор_целевой_после_применённого_GOAL():
    """Живая дыра ночи 30→31.08 (run-0002): Q-3b-GOAL применён
    ответом только о весах — целевых зоны нет, отказа нет, матрица
    держит stop, а спросить некому (пере-ответ поверх применённого
    запрещён). Сквозная проверка ставит вопрос-ДОБОР Q-3b-GOAL-T
    (прецедент добора критерия); с целевыми или отказом — не ставит;
    идемпотентно; -T входит в семейство GOAL, -W — нет."""
    doc = _base_doc(
        _soft("R-BAL", "Кредитные часы распределять равномерно; "
                       "разброс более 10 часов штрафуется.",
              zone="ROSTERING", weight=3))
    doc["questions"] = [
        {"id": "Q-3b-GOAL", "part": "3b", "title": "Целевая",
         "text": "Назовите целевую функцию ростеринга.",
         "answer_format": {"type": "registry"},
         "rule_refs": ["R-BAL"],
         "source_refs": [{"sid": "S1", "anchor": "т1"}],
         "answer": {"raw": "разброс: вес 3", "status": "применён",
                    "revision": 1, "goal_decline": False}}]
    created = m.ensure_goal_questions_sweep(doc)
    assert created == ["Q-3b-GOAL-T"], created
    t = next(x for x in doc["questions"] if x["id"] == "Q-3b-GOAL-T")
    assert "добор" in t["title"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    assert m.ensure_goal_questions_sweep(doc) == [], "идемпотентность"
    # Появилась целевая — добор больше не ставится (после ответа).
    doc2 = _base_doc(
        _soft("R-B2", "Ночёвки вне базы нежелательны.", weight=2,
              zone="ROSTERING"))
    doc2["questions"] = [dict(doc["questions"][0],
                              rule_refs=["R-B2"])]
    doc2["objectives"] = [
        {"id": "O-R", "zone": "ROSTERING", "direction": "minimize",
         "statement": "минимизировать штрафы", "metric": "penalty",
         "sources": [{"sid": "S1", "anchor": "т1"}]}]
    assert m.ensure_goal_questions_sweep(doc2) == []
    # Отказ от оптимизации — добор не ставится.
    doc3 = _base_doc(
        _soft("R-B3", "Ночёвки вне базы нежелательны.", weight=2,
              zone="ROSTERING"))
    doc3["questions"] = [dict(doc["questions"][0],
                              rule_refs=["R-B3"])]
    doc3["questions"][0]["answer"] = dict(
        doc3["questions"][0]["answer"], goal_decline=True)
    assert m.ensure_goal_questions_sweep(doc3) == []
    # Семейство GOAL: базовый и доборы — да, вопросы весов — нет.
    assert m.is_goal_family("Q-3b-GOAL")
    assert m.is_goal_family("Q-3b-GOAL-T")
    assert m.is_goal_family("Q-3a-GOAL-T-2")
    assert not m.is_goal_family("Q-3a-GOAL-W")
    assert not m.is_goal_family("Q-3a-GOAL-W-2")
    assert not m.is_goal_family(None)
