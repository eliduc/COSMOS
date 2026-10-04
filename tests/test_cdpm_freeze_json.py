# -*- coding: utf-8 -*-
"""Заморозка JSON-документов (отмашка владельца 2026-08-26): каждое
скалярное значение — клауза с якорем-путём по ключам (индексы
массивов с нуля, как в инструментах JSON).

Охраняемые контракты: строки дословно (кириллица, опечатки — не
«улучшаются»); числа/истина/ложь/null — литералами JSON;
повторяющийся ключ объекта — честный отказ (штатный разбор молча
теряет первое значение — тихая потеря нормы запрещена); битый JSON —
отказ со строкой и позицией; пустой документ — отказ; гейт заморозки
перечитывает JSON детерминированно."""
import json

import pytest

from cdpm import freeze


def _write(tmp_path, data, name="PRAVILA.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p


def test_json_scalar_leaves_with_path_anchors(tmp_path):
    p = _write(tmp_path, {
        "rules": [{"id": "R-1",
                   "text": "Отдых не менне 10 часов."},
                  {"id": "R-2", "limit": 720, "hard": True,
                   "note": None}],
        "meta": {"источник": "ПИ-1, кириллический ключ"},
    })
    got = freeze.extract_file(p)
    by = {c["anchor"]: c for c in got["clauses"]}
    # Дословность: опечатка «менне» сохранена.
    assert by["rules[0].text"]["text"] == "Отдых не менне 10 часов."
    assert by["rules[1].limit"]["text"] == "720"
    assert by["rules[1].hard"]["text"] == "true"
    assert by["rules[1].note"]["text"] == "null"
    assert by["meta.источник"]["kind"] == "field"
    assert got["converted"] is None


def test_json_duplicate_key_is_an_honest_refusal(tmp_path):
    p = tmp_path / "DUP.json"
    p.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(freeze.FreezeError) as e:
        freeze.extract_file(p)
    assert "повторяющийся ключ" in str(e.value)
    assert "'a'" in str(e.value)


def test_json_broken_named_with_line_and_position(tmp_path):
    p = tmp_path / "BAD.json"
    p.write_text('{"a": 1,\n  "b": }', encoding="utf-8")
    with pytest.raises(freeze.FreezeError) as e:
        freeze.extract_file(p)
    msg = str(e.value)
    assert "строка 2" in msg and "не разбирается" in msg


def test_json_empty_document_refused(tmp_path):
    p = _write(tmp_path, {"пусто": {}, "тоже": []})
    with pytest.raises(freeze.FreezeError) as e:
        freeze.extract_file(p)
    assert "ни одной клаузы" in str(e.value)


def test_json_top_level_list_and_root_scalar(tmp_path):
    p = _write(tmp_path, ["первая норма", "вторая норма"])
    got = freeze.extract_file(p)
    assert [c["anchor"] for c in got["clauses"]] == ["[0]", "[1]"]
    p2 = _write(tmp_path, "одинокая строка", name="ROOT.json")
    got2 = freeze.extract_file(p2)
    assert got2["clauses"] == [{"anchor": "корень", "kind": "field",
                                "text": "одинокая строка"}]


def test_json_kit_passes_gate_redeterminism(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    p = _write(src, {"нормы": [{"текст": "Стыковка не менее 45 мин.",
                                "вес": 5}]}, name="AK-NORMY.json")
    out = tmp_path / "kit"
    manifest = freeze.freeze([p], out)
    assert manifest["files"][0]["sha256"] == freeze.sha256_of(p)
    from cdpm.gates import gate_freeze
    problems, counters = gate_freeze.check_kit(out, [p])
    assert problems == []
    assert counters["originals"] == 1
    proj = (out / "projections" /
            "S1-AK-NORMY.md").read_text(encoding="utf-8")
    assert "[нормы[0].текст] Стыковка не менее 45 мин." in proj
