# -*- coding: utf-8 -*-
"""Экспорт итоговой модели в форматы решателей (ЭКС-1).

Охраняемое: экспорт закрыт при непригодных данных (readiness «а»);
Rave-скелет несёт каждое правило контура с трассировкой и НЕ выдумывает
семантику (тело — заготовка); CSV и JSON полны; Meridian — честный
отказ до передачи документации; неизвестный формат — отказ с перечнем."""
import json

import pytest

from cdpm import export_solver as es


def _doc(blocked: bool):
    q = {"id": "Q-2-x", "part": "2", "title": "Развилка",
         "text": "Как читать?", "answer_format": {
             "type": "choice", "options": [
                 {"key": "а", "text": "так"},
                 {"key": "б", "text": "иначе"}]},
         "rule_refs": ["R-1"],
         "source_refs": [{"sid": "S1", "anchor": "п.1"}]}
    return {
        "meta": {"customer": "«Полярис» (тест)", "mode": 1,
                 "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64}],
        "rules": [
            {"id": "R-1", "title": "Предел смены",
             "statement": "duty ≤ 12:00", "zone": "BOTH",
             "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}],
             **({"blockers": ["Q-2-x"]} if blocked else {})},
            {"id": "R-2", "title": "Вне контура",
             "statement": "нормы питания", "zone": "BOTH",
             "kind": "OUT",
             "sources": [{"sid": "S1", "anchor": "п.2"}]},
        ],
        "questions": [q] if blocked else [],
    }


def test_export_closed_when_data_insufficient():
    doc = _doc(blocked=True)      # блокированная строка → readiness «а»
    with pytest.raises(es.ExportNotReady, match="недостаточно"):
        es.export(doc, "rave")


def test_rave_skeleton_traces_and_does_not_invent():
    body, ext, mime = es.export(_doc(blocked=False), "rave")
    assert ext == ".rave" and mime == "text/plain"
    assert "module cdpm_" in body
    assert "rule r_1" in body                  # транслит идентификатора
    assert "%TODO%" in body                    # семантика не выдумана
    assert "duty ≤ 12:00" in body              # формальная запись — в remark
    assert "S1:п.1" in body                    # трассировка
    assert "нормы питания" not in body         # OUT решателю не идёт


def test_csv_and_json_are_complete():
    body, ext, _ = es.export(_doc(blocked=False), "csv")
    assert ext == ".csv"
    assert "R-1;BOTH;HARD" in body
    assert "S1:п.1" in body
    body, ext, _ = es.export(_doc(blocked=False), "json")
    got = json.loads(body)
    assert got["rules"][0]["id"] == "R-1"


def test_meridian_honestly_unavailable_and_unknown_rejected():
    with pytest.raises(es.ExportUnavailable, match="документация"):
        es.export(_doc(blocked=False), "meridian")
    with pytest.raises(ValueError, match="неизвестный формат"):
        es.export(_doc(blocked=False), "штирлиц")


def test_minizinc_skeleton_compilable_shape():
    """MiniZinc-скелет: ограничение на каждое жёсткое правило (тело —
    заглушка true с TODO), мягкие — слагаемые целевой, OUT — вне
    контура, трассировка в комментариях; закрывается solve."""
    doc = _doc(blocked=False)
    doc["rules"].append({
        "id": "R-3", "title": "Справедливость",
        "statement": "minimize spread(credit)", "zone": "ROSTERING",
        "kind": "SOFT",
        "sources": [{"sid": "S1", "anchor": "п.3"}]})
    body, ext, _ = es.export(doc, "minizinc")
    assert ext == ".mzn"
    assert "constraint true; % TODO: r_1" in body
    assert "var int: pen_r_3 = 0;" in body
    assert "solve minimize pen_r_3;" in body
    assert "% формально: duty ≤ 12:00" in body
    assert "% источник: S1:п.1" in body
    assert "нормы питания" not in body        # OUT вне контура
    # Без мягких правил — solve satisfy.
    body2, _e, _m = es.export(_doc(blocked=False), "minizinc")
    assert "solve satisfy;" in body2


def test_objective_export_components_and_honest_weights():
    """ЭКС-2: критерий оптимизации — SOFT-компоненты по зонам; вес
    берётся ТОЛЬКО из формальной записи (не выдумывается), рядом —
    применённый ответ авиакомпании; непригодная зона помечена."""
    doc = _doc(blocked=False)
    doc["rules"] += [
        {"id": "R-P", "title": "Меньше ночёвок",
         "statement": "minimize nights; вес 5", "zone": "PAIRING",
         "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "п.4"}]},
        {"id": "R-R", "title": "Разброс кредита",
         "statement": "minimize spread(credit)", "zone": "ROSTERING",
         "kind": "SOFT",
         "sources": [{"sid": "S1", "anchor": "п.5"}]},
    ]
    doc["questions"].append({
        "id": "Q-3a-OBJ", "part": "3a", "title": "Целевая пейринга",
        "text": "Состав и веса", "answer_format": {"type": "registry"},
        "rule_refs": ["R-P"],
        "source_refs": [{"sid": "S1", "anchor": "п.4"}],
        "answer": {"status": "применён",
                   "raw": "ночёвки — вес 5, больше ничего"}})
    body, ext, _ = es.export(doc, "objective")
    got = json.loads(body)
    pairing = got["pairing"]
    assert pairing["components"][0]["rule_id"] == "R-P"
    assert pairing["components"][0]["weight"] == 5
    assert pairing["airline_answer"]["raw"].startswith("ночёвки")
    rost = got["rostering"]
    assert rost["components"][0]["weight"] is None   # веса в записи нет
    assert got["readiness"] in ("б", "в")


def test_weight_regex_natural_language_and_foreign_numbers():
    """Вес читается из естественных формулировок обработчика («штраф
    величиной 10», «вес: 3»), а чужие числа («за каждые 30 минут»)
    весом не считаются (живой урок вехи А 2026-08-22)."""
    import cdpm.export_solver as es
    ok = {"штраф величиной 10": 10,
          "штраф W_night = 10": 10,
          "penalty factor 7": 7,
          "вес: 3": 3,
          "штрафом в размере 7 баллов": 7,
          "penalty = 4": 4,
          "штраф 1 за каждые полные 0:30 стыковки": 1}
    for text, want in ok.items():
        mm = es._WEIGHT_RE.search(text)
        assert mm and int(mm.group(1)) == want, text
    for text in ("штраф начисляется за каждые 30 минут",
                 "ночёвки нежелательны"):
        assert es._WEIGHT_RE.search(text) is None, text
