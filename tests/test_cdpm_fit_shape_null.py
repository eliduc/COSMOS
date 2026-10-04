# -*- coding: utf-8 -*-
"""Пустой необязательный слот посадки (null) — отсутствие слота, а не
значение. Проход 19 финальной серии H0 (03.09) остановился на одной
записи «assumption: null» у посадки категории A: схема честно валила
модель («у A не бывает допущения»), хотя содержание посадки было
верным. Форма ответа модели нормализуется кодом; суд остаётся за
контрактом."""
from cdpm import model as m


def test_null_в_необязательных_слотах_снимается_как_отсутствие():
    fit = {"category": "A", "family": "layover_penalty", "assumption": None,
           "gap": None, "candidates": None, "pending_question": None,
           "quote": "Штраф 10 условных единиц за каждую ночёвку вне базы",
           "registry_rev": "x" * 12}
    out, notes = m.normalize_fit_shape(fit)
    assert set(out) == {"category", "family", "quote", "registry_rev"}
    assert len(notes) == 4 and all("null" in n for n in notes)


def test_заполненные_слоты_не_трогаются():
    fit = {"category": "B", "family": "rest_floors",
           "assumption": "отдых считается от последнего прилёта",
           "params": {"min_rest_home": {"value": 720, "quote": "720 минут"}},
           "registry_rev": "x" * 12}
    out, notes = m.normalize_fit_shape(fit)
    assert out == fit and notes == []


def test_null_params_и_quote_тоже_отсутствие():
    out, notes = m.normalize_fit_shape(
        {"category": "A", "family": "layover_penalty", "params": None,
         "quote": None, "registry_rev": "x" * 12})
    assert "params" not in out and "quote" not in out
    assert len(notes) == 2
