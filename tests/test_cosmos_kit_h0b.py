# -*- coding: utf-8 -*-
"""ИДАК H0-B — набор H0 без второго параметра семьи полов отдыха
(поручение владельца 09.09.2026, вариант B). Проверяется: комплект собран
правильно — 15 записей документами (H0 минус VN-024), якоря H0 со сдвигом
строк листа, эталон после ответа = 16 правил H0 с ответом-источником у
VN-024, книга ответов закрывает вопрос о параметре и вопрос об истории,
ожидания манифеста называют вопрос о параметре; отпечатки источников
ставит НОСИТЕЛЬ базового сборщика (bk.stamp_sources), своей формулы у
вариации нет, и обе проекции эталона получают их по факту сборки.

Равенство пересобранных docx/xlsx/jpg с ВЫЛОЖЕННЫМИ файлами комплекта не
судится: в zip docx/xlsx попадает время записи, а байты jpg зависят от
шрифтов машины (находка CI № 81, 11.09.2026) — сравнивается то, что
сборщик записал сам."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KIT = ROOT / "open-bench" / "polyaris-h0b"
H0 = ROOT / "open-bench" / "polyaris-h0"


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_kit_h0b", KIT / "build_kit_h0b.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_h0i_kit_is_h0_without_second_rest_floor(tmp_path):
    b = _builder()
    res = b.build(tmp_path)
    form = json.loads((tmp_path / "etalon" / "VMP-H0B-form.json").read_text(encoding="utf-8"))
    ids = [r["id"] for r in form["rostering"]["hard"]]
    assert b.REMOVED_RULE not in ids and "VN-023" in ids
    h0_form = json.loads((H0 / "etalon" / "VMP-H0-form.json").read_text(encoding="utf-8"))
    assert res["rows"] == sum(len(rows) for _p, _s, rows in
                              __import__("cdpm.vmp_form", fromlist=["x"]).form_sections(h0_form)) - 1
    # якоря: H0 без VN-024, строки листа ниже снятой поднялись на одну
    amap = json.loads((tmp_path / "etalon" / "anchors-map.json").read_text(encoding="utf-8"))
    h0_map = json.loads((H0 / "etalon" / "anchors-map.json").read_text(encoding="utf-8"))
    assert b.REMOVED_RULE not in amap
    assert amap["VN-023"] == h0_map["VN-023"]
    assert amap["VN-060"][0]["anchor"].endswith("строка 3") \
        and h0_map["VN-060"][0]["anchor"].endswith("строка 4")
    assert amap["VN-010"] == h0_map["VN-010"]              # docx не тронут
    # в заморозке xlsx нет строки «вне базы»
    from openpyxl import load_workbook
    wb = load_workbook(tmp_path / "docs" / b.XLSX_NAME)
    texts = [str(c.value) for row in wb.active.iter_rows() for c in row if c.value]
    assert not any("вне базы" in t for t in texts), texts
    assert any("дома" in t for t in texts)


def test_h0i_etalon_after_answer_equals_h0_with_answer_source(tmp_path):
    b = _builder()
    b.build(tmp_path)
    et = json.loads((tmp_path / "etalon" / "VMP-H0B.json").read_text(encoding="utf-8"))
    h0 = json.loads((H0 / "etalon" / "VMP-H0.json").read_text(encoding="utf-8"))
    # закон CDPM «полнота параметров семьи»: отдельного VN-024 нет,
    # носитель VN-023 несёт оба параметра семьи, второй — по ответу
    assert [r["id"] for r in et["rules"]] == \
        [r["id"] for r in h0["rules"] if r["id"] != b.REMOVED_RULE]
    carrier = next(r for r in et["rules"] if r["id"] == b.CARRIER_RULE)
    fit = carrier["template_fit"]
    assert fit["family"] == b.FAMILY and fit["category"] == "A"
    assert set(fit["params"]) == {b.PRESENT_PARAM, b.MISSING_PARAM}
    assert fit["params"][b.PRESENT_PARAM]["value"] == 720
    second = fit["params"][b.MISSING_PARAM]
    assert second["value"] == 600 and second["answer"] == b.PARAM_QUESTION
    # цитата второго параметра — фраза, которую несёт и ответ авиакомпании
    assert second["quote"] in b.PARAM_ANSWER
    assert carrier["sources"] == [r for r in h0["rules"] if r["id"] == b.CARRIER_RULE][0]["sources"]
    # якоря эталона — по карте комплекта: строки листа ниже снятой поднялись
    vn060 = next(r for r in et["rules"] if r["id"] == "VN-060")
    assert vn060["sources"][0]["anchor"].endswith("строка 3")
    vn050 = next(r for r in et["rules"] if r["id"] == "VN-050")
    assert vn050["sources"][0]["anchor"].endswith("строка 5")
    assert et["questions"] == [] and len(et["objectives"]) == 2
    m = json.loads((tmp_path / "kit-manifest.json").read_text(encoding="utf-8"))
    assert m["expect"]["readiness_ladder"] == {"pairing": "optimization",
                                               "rostering": "optimization"}


def test_h0i_answer_book_and_expect(tmp_path):
    b = _builder()
    b.build(tmp_path)
    book = json.loads((tmp_path / "etalon" / "ANSWER-BOOK-MACHINE.json").read_text(encoding="utf-8"))
    assert book["exact"][b.PARAM_QUESTION] == b.PARAM_ANSWER
    assert "600" in b.PARAM_ANSWER and "вне базы" in b.PARAM_ANSWER
    assert b.PARAM_QUESTION_PREFIX in book["prefix"]
    # Каталог 0.5.0: вопросы об истории — по виду чтения семей эталона;
    # книга отвечает «б» по префиксу, ожидание вычислено кодом CDPM.
    assert book["prefix"][b.bk.history_question_prefix()] == b.bk.HISTORY_CONTINUE_ZERO
    assert not [q for q in book["exact"] if q.startswith(b.bk.history_question_prefix())]
    m = json.loads((tmp_path / "kit-manifest.json").read_text(encoding="utf-8"))
    et = json.loads((tmp_path / "etalon" / "VMP-H0B.json").read_text(encoding="utf-8"))
    history_q = b.bk.history_question_ids(et)
    assert history_q and all(q.startswith(b.bk.history_question_prefix()) for q in history_q)
    assert m["expect"]["questions"] == [b.PARAM_QUESTION] + history_q
    assert m["expect"]["objectives"] == 2
    assert m["expect"]["family_complete"] == {b.FAMILY: [b.PRESENT_PARAM, b.MISSING_PARAM]}
    assert m["catalog"] == json.loads((H0 / "kit-manifest.json").read_text(encoding="utf-8"))["catalog"]
    assert m["rules_files"] == [f"docs/{b.DOCX_NAME}", f"docs/{b.XLSX_NAME}", f"docs/{b.JPG_NAME}"]


def test_сборщик_h0b_ставит_отпечатки_источников_по_факту_сборки(tmp_path):
    """Отпечатки источников обеих проекций эталона (форма и ВМП) — sha
    ТОЛЬКО ЧТО собранных файлов комплекта, и ставит их носитель базового
    сборщика: своей формулы отпечатка у вариации больше нет (11.09.2026 —
    одна формула на все сборщики формы H0)."""
    b = _builder()
    assert not hasattr(b, "_sha"), "у вариации своя формула отпечатка"
    b.build(tmp_path)
    form = json.loads((tmp_path / "etalon" / "VMP-H0B-form.json")
                      .read_text(encoding="utf-8"))
    et = json.loads((tmp_path / "etalon" / "VMP-H0B.json")
                    .read_text(encoding="utf-8"))
    assert [s["file"] for s in form["sources"]] == [b.DOCX_NAME, b.XLSX_NAME,
                                                    b.JPG_NAME]
    assert et["sources"] == form["sources"]
    for s in form["sources"]:
        assert s["sha256"] == _sha(tmp_path / "docs" / s["file"]), s["file"]


def test_сборщик_h0b_отказывает_словами_когда_файла_источника_нет(tmp_path,
                                                                  monkeypatch):
    """Файла из `sources` на диске нет — отказ словами носителя, а не
    молчаливый пропуск и не голое падение разбора."""
    b = _builder()
    real = b.bk.build_jpg

    def _без_файла(path, items):
        lines = real(path, items)
        Path(path).unlink()
        return lines

    monkeypatch.setattr(b.bk, "build_jpg", _без_файла)
    with pytest.raises(SystemExit) as e:
        b.build(tmp_path)
    assert "источник формы не найден" in str(e.value)
    assert not (tmp_path / "kit-manifest.json").is_file()


def test_сборщик_h0b_отказывает_когда_проекции_эталона_о_разных_источниках(
        tmp_path, monkeypatch):
    """ВМП эталона о других файлах, чем форма, — отказ словами (закон
    базового сборщика): молча выдать эталон о чужих файлах нельзя."""
    b = _builder()
    srcs = [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64}]
    b.check_sources(srcs, {"sources": [dict(srcs[0])]})        # молчит
    for et in ({"sources": [{"sid": "S1", "file": "ЧУЖОЙ.docx"}]}, {}):
        with pytest.raises(SystemExit) as e:
            b.check_sources(srcs, et)
        assert "О РАЗНЫХ ИСТОЧНИКАХ" in str(e.value)
    # сквозь сборку: производная эталона потеряла источники — красный отказ
    real = b.derive_etalon
    monkeypatch.setattr(b, "derive_etalon", lambda *a, **kw: dict(
        real(*a, **kw), sources=[]))
    with pytest.raises(SystemExit) as e:
        b.build(tmp_path)
    assert "О РАЗНЫХ ИСТОЧНИКАХ" in str(e.value)
    assert not (tmp_path / "kit-manifest.json").is_file()


def test_h0i_builder_is_deterministic(tmp_path):
    b = _builder()
    b.build(tmp_path / "a")
    b.build(tmp_path / "b")
    # Отпечатки docx/xlsx в `sources` несут метки времени файлов и между
    # сборками отличаются — сравнивается содержание: записи, правила,
    # целевые, якоря, книга ответов.
    def _read(p):
        return json.loads(p.read_text(encoding="utf-8"))
    for name in ("etalon/anchors-map.json", "etalon/ANSWER-BOOK-MACHINE.json"):
        assert _read(tmp_path / "a" / name) == _read(tmp_path / "b" / name), name
    fa, fb = _read(tmp_path / "a/etalon/VMP-H0B-form.json"), _read(tmp_path / "b/etalon/VMP-H0B-form.json")
    fa.pop("sources"), fb.pop("sources")
    assert fa == fb
    ea, eb = _read(tmp_path / "a/etalon/VMP-H0B.json"), _read(tmp_path / "b/etalon/VMP-H0B.json")
    assert ea["rules"] == eb["rules"] and ea["objectives"] == eb["objectives"]
