"""COSMOS 12f48240: explain both readings without deleting scope."""
import pytest

from cdpm import answer_parsing, catalog_bridge as cb, provenance
from test_k03_subject_vocabulary import selected_catalogue


@pytest.fixture(autouse=True)
def clean():
    yield
    cb.forget()


@pytest.mark.parametrize("phrase", ["в базе", "на базе"])
@pytest.mark.parametrize("tail", ["", " SVO только для пилотов"])
def test_subject_scope_collision_names_both_readings_and_keeps_full_condition(
        tmp_path, monkeypatch, phrase, tail):
    selected_catalogue(tmp_path, monkeypatch)
    raw = phrase + tail + " — не менее 10 часов"
    parsed = answer_parsing.parameter({"id": "Q-3b-параметр-rest_floors-min_rest_home"}, raw)
    if not tail:
        # An exact catalogue subject immediately followed by its bound has no
        # extra scope; the qualified twin below must still refuse and explain.
        assert parsed["value"] == 600 and not parsed["ambiguous"]
        assert "condition" not in parsed and parsed["quote"] == raw
        return
    assert parsed["ambiguous"] and parsed["value"] is None
    assert parsed["condition"] == {"raw": raw, "resolved": False}
    note = " ".join(parsed["problems"])
    assert "предмет" in note and "оговорк" in note and phrase in note
    assert "не удаляйте" in note
    problems = provenance.value_problems("rest_floors", "min_rest_home",
                                         {"value": 600, "quote": "10 часов"}, raw)
    assert problems and any("две трактовки" in p for p in problems)


@pytest.mark.parametrize("raw", ["дома — не менее 10 часов", "домашнего отдыха — 10 часов"])
def test_unambiguous_home_subject_stays_healthy(tmp_path, monkeypatch, raw):
    selected_catalogue(tmp_path, monkeypatch)
    parsed = answer_parsing.parameter({"id": "Q-3b-параметр-rest_floors-min_rest_home"}, raw)
    assert parsed["value"] == 600 and not parsed["ambiguous"]
