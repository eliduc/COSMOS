# -*- coding: utf-8 -*-
"""Структурное решает код (решение владельца 2026-09-04).

1) Зона записи — по разделу документа канонической формы: общий раздел
   → BOTH, часть «Пейринг» → PAIRING, часть «Ростеринг» → ROSTERING; выбор
   модели перекрывается кодом (живой случай 04.09: определение из общего
   раздела прочитано как ROSTERING). Документы без распознанной
   структуры не трогаются.
2) Цитата параметра посадки, поставленного моделью без цитаты, — из
   клаузы-источника той же сверкой величин, что у баланса; результат
   проходит validate_fit_quotes; ненайденное остаётся без цитаты."""
import copy
import json
from pathlib import Path

from cdpm import freeze as fz
from cdpm import model as m
from cdpm import form_structure as st

REPO = Path(__file__).resolve().parents[1]
KIT = REPO / "open-bench" / "polyaris-h0"


def _kit_entries() -> list:
    manifest = json.loads((KIT / "kit-manifest.json").read_text(encoding="utf-8"))
    docx, xlsx, jpg = [KIT / p for p in manifest["rules_files"]]
    jpg_lines = json.loads((KIT / "etalon" / "jpg-lines.json").read_text(encoding="utf-8"))
    return [
        {"sid": "S1", "file": docx.name, "sha256": fz.sha256_of(docx),
         "clauses": fz._extract_docx(docx)},
        {"sid": "S2", "file": xlsx.name, "sha256": fz.sha256_of(xlsx),
         "clauses": fz._extract_xlsx(xlsx)},
        {"sid": "S3", "file": jpg.name, "sha256": fz.sha256_of(jpg),
         "clauses": [{"anchor": f"стр-1-абз-{n}", "kind": "p", "text": t}
                     for n, t in enumerate(jpg_lines, start=1)]},
    ]


def _etalon() -> dict:
    return json.loads((KIT / "etalon" / "VMP-H0.json").read_text(encoding="utf-8"))


def _clause_by_key(entries) -> dict:
    return {(str(e["sid"]), str(c["anchor"])): str(c.get("text") or "")
            for e in entries for c in e.get("clauses") or []}


def test_зоны_по_разделу_документа_перекрывают_модель():
    entries = _kit_entries()
    zmap = st.zones_by_source(entries)
    assert zmap, "структура набора H0 обязана распознаваться читателем формы"
    doc = _etalon()
    # Модель «прочла» определение общего раздела как ростеринг, жёсткое
    # правило пейринга как обе части, целевую ростеринга как пейринг.
    by_id = {r["id"]: r for r in doc["rules"]}
    by_id["VN-006"]["zone"] = "ROSTERING"
    by_id["VN-010"]["zone"] = "BOTH"
    obj = next(o for o in doc["objectives"] if o["id"] == "O-ROST")
    obj["zone"] = "PAIRING"
    changes = st.enforce_form_zones(doc, entries)
    assert {(c["id"], c["from"], c["to"]) for c in changes} == {
        ("VN-006", "ROSTERING", "BOTH"), ("VN-010", "BOTH", "PAIRING"),
        ("O-ROST", "PAIRING", "ROSTERING")}
    assert by_id["VN-006"]["zone"] == "BOTH" and by_id["VN-010"]["zone"] == "PAIRING"
    assert obj["zone"] == "ROSTERING"
    # Второй проход — без правок: эталонные зоны и есть зоны разделов.
    assert st.enforce_form_zones(doc, entries) == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_документы_без_структуры_не_трогаются():
    entries = [{"sid": "S1", "file": "rules.md", "sha256": "0" * 64,
                "clauses": [{"anchor": "абз.1", "kind": "p",
                             "text": "Смена не длиннее 11 часов."}]}]
    doc = {"rules": [{"id": "R-1", "kind": "HARD", "zone": "PAIRING", "title": "т",
                      "statement": "s", "sources": [{"sid": "S1", "anchor": "абз.1"}]}],
           "objectives": []}
    assert st.zones_by_source(entries) == {}
    assert st.enforce_form_zones(doc, entries) == []
    assert doc["rules"][0]["zone"] == "PAIRING"


def test_цитаты_параметров_восполняются_из_клаузы():
    entries = _kit_entries()
    cbk = _clause_by_key(entries)
    doc = _etalon()
    stripped = []
    for r in doc["rules"]:
        fit = r.get("template_fit") or {}
        for name, cell in (fit.get("params") or {}).items():
            if isinstance(cell, dict) and cell.get("quote"):
                cell["quote"] = ""
                stripped.append((r["id"], name))
    assert stripped, "в эталоне H0 есть параметры с цитатами"
    filled = st.backfill_param_quotes(doc, cbk)
    got = {(f["id"], f["param"]) for f in filled}
    missing = set(stripped) - got
    assert not missing, f"не восполнены: {sorted(missing)}"
    # Каждая цитата — дословный фрагмент клаузы-источника (тот же судья,
    # что у извлечения и цикла).
    assert m.validate_fit_quotes(doc, cbk) == []
    for r in doc["rules"]:
        for name, cell in ((r.get("template_fit") or {}).get("params") or {}).items():
            assert str(cell.get("quote") or "").strip(), (r["id"], name)


def test_цитата_не_найдена_остаётся_пустой_и_модель_цела():
    entries = _kit_entries()
    cbk = _clause_by_key(entries)
    doc = _etalon()
    r = next(x for x in doc["rules"] if x["id"] == "VN-050")
    cell = r["template_fit"]["params"]["workload_spread_threshold"]
    before = copy.deepcopy(r)
    cell["quote"] = ""
    cell["value"] = 4242            # величины нет в клаузе — цитаты не будет
    assert st.backfill_param_quotes(doc, cbk) == []
    assert cell["quote"] == ""
    cell["value"] = before["template_fit"]["params"]["workload_spread_threshold"]["value"]
    filled = st.backfill_param_quotes(doc, cbk)
    assert [(f["id"], f["param"]) for f in filled] == [("VN-050", "workload_spread_threshold")]
    assert "10" in cell["quote"]
