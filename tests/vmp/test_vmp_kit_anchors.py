# -*- coding: utf-8 -*-
"""Режим «комплект» генератора ВМП нано (обратная задача H0): карта
якорей комплекта переопределяет якоря правил, целевых и вопросов;
правило без записи в карте — отказ; источники — ФАЙЛЫ ПРАВИЛ комплекта
по манифесту в порядке подачи (у набора «ИД H0-ВМП» это docx, xlsx и
jpg — все три несут форму ВМП; файлы ДАННЫХ, если они есть,
источниками не становятся); живой комплект polyaris-h0 даёт ВМП,
эквивалентный себе по гейту."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

GEN = HERE / "open-bench" / "polyaris" / "build_vmp_nano.py"
KIT = HERE / "open-bench" / "polyaris-h0"


def _gen():
    spec = importlib.util.spec_from_file_location("build_vmp_nano", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_карта_переопределяет_якоря_правил_целевых_и_вопросов():
    g = _gen()
    doc = g.build()
    amap = {r["id"]: [{"sid": "S9", "anchor": f"якорь-{r['id']}"}]
            for r in doc["rules"]}
    amap.update({o["id"]: [{"sid": "S9", "anchor": f"якорь-{o['id']}"}]
                 for o in doc["objectives"]})
    out = g.apply_anchor_map(doc, amap, [{"sid": "S9", "file": "x",
                                          "sha256": "0" * 64}])
    for r in out["rules"]:
        assert r["sources"] == [{"sid": "S9", "anchor": f"якорь-{r['id']}"}]
    for o in out["objectives"]:
        assert o["sources"][0]["anchor"] == f"якорь-{o['id']}"
    # вопрос без своей записи — якорь первого rule_refs
    q = next(x for x in out["questions"] if x["id"] == "Q-3b-GOAL")
    assert q["source_refs"][0]["anchor"] == "якорь-VN-050"
    assert out["sources"][0]["sid"] == "S9"


def test_правило_без_записи_в_карте_отказ():
    g = _gen()
    doc = g.build()
    amap = {r["id"]: [{"sid": "S1", "anchor": "a"}]
            for r in doc["rules"][:-1]}
    with pytest.raises(ValueError):
        g.apply_anchor_map(doc, amap)


def test_источники_комплекта_по_манифесту(tmp_path):
    """sid — по порядку rules_files манифеста (как cdpm.freeze);
    data_files sid не получают; без манифеста — отказ."""
    g = _gen()
    (tmp_path / "docs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "docs" / "P.docx").write_bytes(b"docx")
    (tmp_path / "docs" / "P.jpg").write_bytes(b"jpg")
    (tmp_path / "data" / "D.xlsx").write_bytes(b"xlsx")
    with pytest.raises(ValueError):
        g._kit_sources(tmp_path)
    (tmp_path / "kit-manifest.json").write_text(json.dumps({
        "rules_files": ["docs/P.docx", "docs/P.jpg"],
        "data_files": ["data/D.xlsx"]}), encoding="utf-8")
    src = g._kit_sources(tmp_path)
    assert [(s["sid"], s["file"]) for s in src] == [("S1", "P.docx"),
                                                    ("S2", "P.jpg")]
    assert all(len(s["sha256"]) == 64 for s in src)


@pytest.mark.skipif(not (KIT / "etalon" / "anchors-map.json").is_file(),
                    reason="комплект polyaris-h0 не выложен")
def test_живой_комплект_h0_эквивалентен_себе():
    from vmp import equivalence as eq
    g = _gen()
    amap = json.loads((KIT / "etalon" / "anchors-map.json")
                      .read_text(encoding="utf-8"))
    amap = {k: v for k, v in amap.items() if not k.startswith("_")}
    doc = g.apply_anchor_map(g.build(), amap, g._kit_sources(KIT))
    res = eq.equivalence(doc, doc)
    assert res["verdict"] == "EQUIVALENT", res["problems"]
    # ИД H0-ВМП из ФОРМЫ (решение владельца 2026-09-02): docx = S1
    # (общий раздел + часть 1 а/б), xlsx = S2 (часть 2 а/б), jpg = S3
    # (разделы в) обеих частей — критерии).
    sids = {s["sid"] for s in doc["sources"]}
    assert sids == {"S1", "S2", "S3"}
    s2 = {r["id"] for r in doc["rules"]
          if any(s["sid"] == "S2" for s in r["sources"])}
    assert s2 == {"VN-023", "VN-024", "VN-060", "VN-050"}
    s3 = {o["id"] for o in doc["objectives"]
          if any(s["sid"] == "S3" for s in o["sources"])}
    assert s3 == {"O-PAIR", "O-ROST"}
