# -*- coding: utf-8 -*-
"""Единая проверка соответствия ВМП и шаблонов COSMOS (registry_check).

Код приложения, независимый от конкретного ВМП: фикстуры здесь
синтетические (правила R-1..R-4, выдуманные файлы-исполнители).
Дисциплина негативных тестов: каждый тест закрывает ОБХОД инварианта
fail-closed, а не счастливый путь.

Инварианты:
  1. Реестр — единственный источник истины; отсутствующий, пустой,
     бесформенный или двоящийся реестр = стоп, не пропуск.
  2. Рубеж 1 (глубокая сверка): каждое правило расчётных зон и критерий
     против реестра; неизвестное правило, неполный вердикт, неизвестный
     вид критерия — поимённый пробел.
  3. Рубеж 2 (сторож дрейфа): изменение любого файла-исполнителя после
     сверки делает реестр устаревшим ДО глубокой сверки.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from vmp import registry_check as rc  # noqa: E402


def _fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in rc.EXECUTOR_FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# исполнитель %s\n" % rel, encoding="utf-8")
    return root


def _registry(root: Path, rules=None) -> dict:
    return {
        "created": "2026-08-28",
        "code_fingerprints": rc.code_fingerprints(root),
        "rules": rules if rules is not None else [
            {"rule_id": "R-1", "verdict": "covered_full",
             "title": "правило один", "evidence": "file.py:1"},
            {"rule_id": "R-2", "verdict": "data_or_out_of_scope",
             "title": "правило два", "gap_note": "данные набора"},
            {"rule_id": "R-3", "verdict": "covered_partial",
             "title": "правило три", "gap_note": "исполнитель неполон"},
        ],
    }


def _write_registry(tmp_path: Path, reg: dict) -> Path:
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    return p


MODEL = {"rules": [
    {"id": "R-1", "zone": "PAIRING", "title": "правило один"},
    {"id": "R-2", "zone": "BOTH", "title": "правило два"},
    {"id": "R-9", "zone": "ROSTERING", "title": "чужая зона"},
]}


def test_исключено_до_получения_данных_не_пробел_и_ярусы_стоп(tmp_path):
    """Структура ВМП (владелец 2026-09-02): правило с пометкой
    excluded_until_data к исполнению не требуется и пробелом не
    считается; несколько критериев / ярус > 1 в форме — стоп."""
    root = _fake_repo(tmp_path)
    reg = _registry(root, rules=[
        {"rule_id": "R-1", "verdict": "covered_full",
         "title": "правило один", "evidence": "file.py:1"}])
    excluded = {"id": "R-7", "zone": "PAIRING", "kind": "HARD",
                "title": "нет данных",
                "excluded_until_data": {"question": "Q-3b-нет-данных-7",
                                        "revision": 1}}
    plain = {"id": "R-8", "zone": "PAIRING", "kind": "HARD",
             "title": "нет в реестре"}
    assert rc.is_excluded_until_data(excluded)
    assert not rc.is_excluded_until_data(plain)
    assert rc.excluded_until_data_ids([excluded, plain]) == ["R-7"]
    # Исключённое — не пробел; обычное без записи — пробел (как прежде).
    gaps = rc.deep_check([excluded, plain], None, reg)
    assert [g["rule_id"] for g in gaps] == ["R-8"]
    # Пустая/кривая пометка не исключает.
    assert not rc.is_excluded_until_data(
        {"id": "R-9", "excluded_until_data": {}})
    # Ярусы критериев: один критерий яруса 1 — чисто; два критерия
    # либо ярус 2 — честный стоп (решатель исполняет один критерий).
    ok = {"pairing": {"criteria": [{"id": "O-1", "tier": 1}]}}
    assert rc.criteria_tiers_gaps(ok) == []
    assert rc.criteria_tiers_gaps(None) == []
    two = {"pairing": {"criteria": [{"id": "O-1", "tier": 1},
                                    {"id": "O-2", "tier": 2,
                                     "tolerance": 5}]}}
    g = rc.criteria_tiers_gaps(two)
    assert [x["kind"] for x in g] == ["criteria_tiers_unsupported"]
    assert "O-1, O-2" in g[0]["rule_id"] and "один критерий" in g[0]["note"]
    # Ярусы судятся по ЧАСТИ задачи: та же форма чиста для ростеринга.
    assert rc.criteria_tiers_gaps(two, parts=("rostering",)) == []
    two_r = {"rostering": {"criteria": [{"id": "O-1", "tier": 1},
                                        {"id": "O-2", "tier": 1}]}}
    assert rc.criteria_tiers_gaps(two_r, parts=("rostering",))
    # Гейт в сборе: форма с ярусами останавливает расчёт поимённо —
    # только при исполняемом критерии (оптимизация); в режимах
    # выполнимости критерий не исполняется — ярусы не судятся.
    p = _write_registry(tmp_path, reg)
    model = {"rules": [excluded, {"id": "R-1", "zone": "PAIRING",
                                  "title": "правило один"}]}
    crit = {"kind": "min_person_days"}
    rep = rc.conformance_gate(model, crit, root, p, form=ok)
    assert rep["verdict"] == "OK"
    assert rep["rules_checked"] == 1
    assert rep["rules_excluded_until_data"] == ["R-7"]
    assert rc.conformance_gate(model, None, root, p, form=two)["verdict"] \
        == "OK"
    with pytest.raises(rc.ConformanceError) as e:
        rc.conformance_gate(model, crit, root, p, form=two)
    assert e.value.gaps[0]["kind"] == "criteria_tiers_unsupported"
    # Ярусы части «Ростеринг» гейт пейринга не судит, гейт ростеринга —
    # судит.
    assert rc.conformance_gate(model, crit, root, p, form=two_r,
                               task="pairing")["verdict"] == "OK"
    with pytest.raises(rc.ConformanceError):
        rc.conformance_gate(model, crit, root, p, form=two_r,
                            task="rostering")
    # Реестр помечает правило исключённым, а пометка снята — пересверка,
    # не тихое «покрыто».
    stale = _registry(root, rules=[
        {"rule_id": "R-7", "verdict": "excluded_until_data",
         "title": "нет данных", "evidence": "исключено"}])
    back = {"id": "R-7", "zone": "PAIRING", "kind": "HARD",
            "title": "нет данных"}
    gaps = rc.deep_check([back], None, stale)
    assert [g["kind"] for g in gaps] == ["rule_excluded_stale"]
    assert rc.deep_check([excluded], None, stale) == []


def test_чистая_посадка_не_отменяет_вердикт_реестра(tmp_path):
    """Критика принципов 2026-09-02: посадка судится первой, но запись
    реестра с пробелом («две правды о семье») предъявляется и при
    чистой посадке; отсутствие записи при посадке — не пробел."""
    root = _fake_repo(tmp_path)
    fitted = {"id": "R-5", "zone": "PAIRING", "kind": "SOFT",
              "title": "ночёвки", "weight": 2,
              "template_fit": {"category": "A", "family": "layover_penalty",
                               "registry_rev": "t",
                               "quote": "дословный носитель"}}
    reg = _registry(root, rules=[
        {"rule_id": "R-5", "verdict": "family_conflict",
         "title": "ночёвки", "gap_note": "две правды о семье"}])
    gaps = rc.deep_check([fitted], None, reg)
    assert [g["kind"] for g in gaps] == ["rule_family_conflict"]
    assert "две правды" in gaps[0]["note"]
    # Та же посадка при покрытом вердикте — чисто.
    reg_ok = _registry(root, rules=[
        {"rule_id": "R-5", "verdict": "covered_full",
         "title": "ночёвки", "evidence": "семья layover_penalty"}])
    assert rc.deep_check([fitted], None, reg_ok) == []
    # Записи о правиле нет — посадка одна судит (обратная совместимость).
    reg_none = _registry(root, rules=[
        {"rule_id": "R-1", "verdict": "covered_full",
         "title": "правило один", "evidence": "file.py:1"}])
    assert rc.deep_check([fitted], None, reg_none) == []
    # Кривая посадка предъявляется посадкой, а не реестром.
    broken = dict(fitted, template_fit={"category": "A",
                                        "family": "layover_penalty",
                                        "registry_rev": "t"})
    gaps2 = rc.deep_check([broken], None, reg)
    assert [g["kind"] for g in gaps2] == ["fit_invalid"]


def test_отсутствующий_и_бесформенный_реестр_это_стоп(tmp_path):
    with pytest.raises(rc.ConformanceError) as e:
        rc.load_registry(tmp_path / "нет-такого.json")
    assert e.value.gaps[0]["kind"] == "no_registry"
    # пустые правила
    with pytest.raises(rc.ConformanceError) as e:
        rc.load_registry(_write_registry(
            tmp_path, {"code_fingerprints": {"x": "y"}, "rules": []}))
    assert e.value.gaps[0]["kind"] == "registry_malformed"
    # без отпечатков кода сторож дрейфа слеп — тоже не форма
    with pytest.raises(rc.ConformanceError):
        rc.load_registry(_write_registry(
            tmp_path, {"rules": [{"rule_id": "R-1",
                                  "verdict": "covered_full"}]}))
    # двоящийся rule_id — источник истины двоится
    with pytest.raises(rc.ConformanceError) as e:
        rc.load_registry(_write_registry(tmp_path, {
            "code_fingerprints": {"x": "y"},
            "rules": [{"rule_id": "R-1", "verdict": "covered_full"},
                      {"rule_id": "R-1", "verdict": "covered_partial"}]}))
    assert any("дважды" in g["note"] for g in e.value.gaps)


def test_сторож_дрейфа_ловит_изменение_исполнителя(tmp_path):
    root = _fake_repo(tmp_path)
    reg = _registry(root)
    assert rc.drift_check(reg, root) == []
    changed = root / rc.EXECUTOR_FILES[0]
    changed.write_text("# изменён после сверки\n", encoding="utf-8")
    assert rc.drift_check(reg, root) == [rc.EXECUTOR_FILES[0]]
    # исчезнувший файл — тоже дрейф, не пропуск
    (root / rc.EXECUTOR_FILES[1]).unlink()
    assert rc.EXECUTOR_FILES[1] in rc.drift_check(reg, root)


def test_глубокая_сверка_судит_правила_расчётных_зон(tmp_path):
    root = _fake_repo(tmp_path)
    reg = _registry(root)
    # R-1 закрыт, R-2 вне контура, зона ROSTERING не судится
    assert rc.deep_check(MODEL["rules"], None, reg) == []
    # неизвестное правило расчётной зоны — поимённый пробел
    rules = MODEL["rules"] + [{"id": "R-4", "zone": "PAIRING",
                               "title": "новое правило"}]
    gaps = rc.deep_check(rules, None, reg)
    assert [g["kind"] for g in gaps] == ["rule_unknown"]
    assert gaps[0]["rule_id"] == "R-4"
    # неполный вердикт — пробел с заметкой реестра
    rules = MODEL["rules"] + [{"id": "R-3", "zone": "BOTH",
                               "title": "правило три"}]
    gaps = rc.deep_check(rules, None, reg)
    assert [g["kind"] for g in gaps] == ["rule_covered_partial"]
    assert gaps[0]["note"] == "исполнитель неполон"


def test_критерий_судится_тем_же_рубежом(tmp_path):
    root = _fake_repo(tmp_path)
    reg = _registry(root)
    # вид из закрытого словаря с исполнителем — пробелов нет
    assert rc.deep_check([], {"kind": "min_person_days"}, reg) == []
    # неизвестный вид — пробел criterion_unknown
    gaps = rc.deep_check([], {"kind": "чудо-критерий"}, reg)
    assert [g["kind"] for g in gaps] == ["criterion_unknown"]
    # вид в словаре, но без исполнителя — пробел (monkeypatch словаря)
    from vmp import criteria as _c
    _c.SPEC["_тест_вид"] = {"title": "т", "needs": "шаблона нет"}
    try:
        gaps = rc.deep_check([], {"kind": "_тест_вид"}, reg)
        assert [g["kind"] for g in gaps] == ["criterion_no_executor"]
        assert gaps[0]["note"] == "шаблона нет"
    finally:
        del _c.SPEC["_тест_вид"]


def test_единый_гейт_оба_рубежа_fail_closed(tmp_path):
    root = _fake_repo(tmp_path)
    reg_path = _write_registry(tmp_path, _registry(root))
    rep = rc.conformance_gate(MODEL, {"kind": "min_person_days"},
                              root, reg_path)
    assert rep["verdict"] == "OK"
    assert rep["rules_checked"] == 2 and rep["criterion_checked"]
    # дрейф кода закрывает гейт ДО глубокой сверки
    (root / rc.EXECUTOR_FILES[0]).write_text("# дрейф",
                                             encoding="utf-8")
    with pytest.raises(rc.ConformanceError) as e:
        rc.conformance_gate(MODEL, None, root, reg_path)
    assert all(g["kind"] == "drift" for g in e.value.gaps)
    # свежая пересверка (новые отпечатки) открывает правила, но пробел
    # правила по-прежнему стоп
    reg2 = _registry(root, rules=[{"rule_id": "R-1",
                                   "verdict": "covered_full"}])
    reg2_path = _write_registry(tmp_path, reg2)
    with pytest.raises(rc.ConformanceError) as e:
        rc.conformance_gate(MODEL, None, root, reg2_path)
    assert [g["rule_id"] for g in e.value.gaps] == ["R-2"]


def test_ручной_запуск_коды_возврата(tmp_path, capsys):
    root = _fake_repo(tmp_path)
    reg_path = _write_registry(tmp_path, _registry(root))
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(MODEL, ensure_ascii=False),
                          encoding="utf-8")
    assert rc.main([str(model_path), "--registry", str(reg_path),
                    "--repo-root", str(root)]) == 0
    assert "OK" in capsys.readouterr().out
    # пробел — код 2 и поимённый перечень
    bad = {"rules": MODEL["rules"] + [{"id": "R-4", "zone": "PAIRING",
                                       "title": "новое"}]}
    model_path.write_text(json.dumps(bad, ensure_ascii=False),
                          encoding="utf-8")
    assert rc.main([str(model_path), "--registry", str(reg_path),
                    "--repo-root", str(root)]) == 2
    out = capsys.readouterr().out
    assert "R-4" in out and "СТОП" in out


def test_строки_сверки_не_несут_заявку_каталога(tmp_path):
    """Шаг 6 каталога: обогащение пробела номером и состоянием заявки —
    дело КНОПКИ (она читает каталог), а не глубокой сверки. Сверка
    остаётся чистой функцией от модели и реестра: поля `request` в её
    строках нет, и каталог она для этого не открывает.

    Без этого рубежа сверку было бы невозможно звать там, где каталога
    нет (гейт ручного запуска, стенды), — а её вердикт стал бы зависеть
    от состояния чужой очереди заявок."""
    root = _fake_repo(tmp_path)
    reg = _registry(root)
    rules = MODEL["rules"] + [{"id": "R-4", "zone": "PAIRING",
                               "title": "новое правило"}]
    gaps = rc.deep_check(rules, {"kind": "чудо-критерий"}, reg)
    assert len(gaps) == 2, gaps
    for gap in gaps:
        assert "request" not in gap
        assert set(gap) == {"kind", "rule_id", "title", "note"}
    # Классификатор кнопки судит ЭТИ ЖЕ виды пробелов: ни один из них не
    # уходит в «классификатору неизвестен».
    from vmp import catalog_requests as vcr
    for gap in gaps:
        assert gap["kind"].startswith(vcr.RULE_PREFIX) \
            or gap["kind"] in vcr.CRITERION_GAPS \
            or gap["kind"] in vcr.RULE_GAPS \
            or gap["kind"] in vcr.NOT_CATALOG_GAPS, gap["kind"]
    print("строк сверки рассмотрено: %d" % len(gaps))
