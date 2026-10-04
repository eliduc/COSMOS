# -*- coding: utf-8 -*-
"""Гейт эквивалентности ВМП (vmp/equivalence): эталон H0 эквивалентен
себе; любое каноническое расхождение — поимённое нарушение; строки
данных вне сравнения; легаси-форма свода CDPM (params с уточнителями,
«12:00») канонизируется; слой пакетов судит целевые и режимы."""
from __future__ import annotations

import copy
import json
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from vmp import equivalence as eq  # noqa: E402

ETALON_PATH = HERE / "open-bench" / "polyaris-nano" / "etalon" / \
    "VMP-NANO.json"


def _h0() -> dict:
    return json.loads(ETALON_PATH.read_text(encoding="utf-8"))


def _rule(model: dict, rid: str) -> dict:
    return next(r for r in model["rules"] if r["id"] == rid)


def test_эталон_эквивалентен_самому_себе():
    res = eq.equivalence(_h0(), _h0())
    assert res["verdict"] == "EQUIVALENT", res["problems"]
    assert res["counts"]["covered"] == res["counts"]["etalon_rules"] == 12
    assert res["counts"]["extra"] == 0


@pytest.mark.parametrize("mutate,needle", [
    (lambda m: _rule(m, "VN-010")["template_fit"]["params"]
     ["max_duty_duration"].__setitem__("value", 690), "параметры"),
    (lambda m: _rule(m, "VN-011").__setitem__("weight", 16), "вес"),
    (lambda m: _rule(m, "VN-023").__setitem__("zone", "BOTH"), "зона"),
    (lambda m: _rule(m, "VN-030").__setitem__("kind", "HARD"), "вид"),
    (lambda m: _rule(m, "VN-020")["template_fit"]
     .__setitem__("family", "rest_floors"), "семья"),
    (lambda m: m["rules"].remove(_rule(m, "VN-060")), "не покрыто"),
    # Категория посадки A→B (с допущением) — не «один в один».
    (lambda m: _rule(m, "VN-011")["template_fit"].update(
        {"category": "B", "assumption": "потолок принят по умолчанию"}),
     "категория посадки"),
])
def test_каждое_каноническое_расхождение_названо(mutate, needle):
    cand = _h0()
    mutate(cand)
    res = eq.equivalence(cand, _h0())
    assert res["verdict"] == "NOT_EQUIVALENT"
    assert any(needle in p for p in res["problems"]), res["problems"]


def test_сверхэталонное_правило_нарушение_а_данные_нет():
    cand = _h0()
    cand["rules"].append({"id": "R-X", "kind": "HARD", "zone": "BOTH",
                          "title": "Лишнее", "statement": "x",
                          "sources": [{"sid": "S1", "anchor": "нет"}]})
    res = eq.equivalence(cand, _h0())
    assert res["verdict"] == "NOT_EQUIVALENT"
    assert any("сверх эталона: R-X" in p for p in res["problems"])
    cand2 = _h0()
    cand2["rules"].append({"id": "D-1", "kind": "DATA", "zone": "BOTH",
                           "title": "Справка", "statement": "x",
                           "sources": [{"sid": "S2", "anchor": "лист"}]})
    res2 = eq.equivalence(cand2, _h0())
    assert res2["verdict"] == "EQUIVALENT"
    assert res2["counts"]["data_rows"] == 1


def test_открытый_вопрос_анкеты_нарушение():
    cand = _h0()
    cand["questions"][0]["answer"]["status"] = "открыт"
    res = eq.equivalence(cand, _h0())
    assert any("открытый вопрос" in p for p in res["problems"])


def test_легаси_форма_свода_cdpm_канонизируется():
    """Свод CDPM до посадок: params-массив с уточнителями per-base и
    «12:00» — эквивалентен посадке эталона."""
    cand = _h0()
    r60 = _rule(cand, "VN-060")
    del r60["template_fit"]
    r60["params"] = [
        {"name": "reserve_crews", "value": 2, "qualifier": "PLY",
         "unit": "count", "relation": "min"},
        {"name": "reserve_crews", "value": 1, "qualifier": "SVG",
         "unit": "count", "relation": "min"},
        {"name": "reserve_crews", "value": 1, "qualifier": "GRT",
         "unit": "count", "relation": "min"}]
    r10 = _rule(cand, "VN-010")
    del r10["template_fit"]
    r10["params"] = [{"name": "max_duty_duration", "value": "12:00",
                      "unit": "duration_minutes", "relation": "max"}]
    res = eq.equivalence(cand, _h0())
    assert res["verdict"] == "EQUIVALENT", res["problems"]
    fam, params = eq.family_params_of(r60)
    assert fam == "reserve_floors"
    assert params == {"reserve_crews": "GRT:1,PLY:2,SVG:1"}


def _pkg(tmp_path: Path, name: str, weight: int, rate: int,
         modes=("optimization", "feasibility-soft")) -> Path:
    obj = {"pairing": {
        "direction": "minimize",
        "components": [{"rule_id": "VN-030", "weight": weight,
                        "sources": [{"sid": "S1",
                                     "anchor": "табл.3, строка 6, кол 3"}]}],
        "objectives": [{"id": "O-PAIR", "metric": "total_pairing_cost"}],
        "airline_answer": {"raw": "критерий {\"criterion\": "
                                  "\"MIN_PERSON_DAYS\", \"rate_per_duty\": "
                                  + str(rate) + "}"}},
        "rostering": {"direction": "minimize", "components": [],
                      "objectives": [{"id": "O-ROST",
                                      "metric": "credit_hours_spread"}]}}
    mani = {"allowed_modes": {"pairing": list(modes),
                              "rostering": list(modes)}}
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("objective.json", json.dumps(obj, ensure_ascii=False))
        z.writestr("manifest.json", json.dumps(mani, ensure_ascii=False))
    return p


def test_слой_пакетов_целевые_критерий_режимы(tmp_path):
    et = _pkg(tmp_path, "et.zip", 10, 100)
    same = _pkg(tmp_path, "same.zip", 10, 100)
    assert eq.diff_packages(same, et)["problems"] == []
    w = _pkg(tmp_path, "w.zip", 11, 100)
    assert any("компоненты" in p for p in eq.diff_packages(w, et)["problems"])
    r = _pkg(tmp_path, "r.zip", 10, 90)
    assert any("критерий" in p for p in eq.diff_packages(r, et)["problems"])
    m = _pkg(tmp_path, "m.zip", 10, 100, modes=("feasibility-hard",))
    assert any("allowed_modes" in p
               for p in eq.diff_packages(m, et)["problems"])


def test_кривые_входы_отказ_словами():
    with pytest.raises(eq.EquivalenceError):
        eq.equivalence([], _h0())


def test_отчёт_читаем():
    cand = _h0()
    _rule(cand, "VN-011")["weight"] = 99
    res = eq.equivalence(cand, _h0())
    txt = eq.render_report(res)
    assert "NOT_EQUIVALENT" in txt and "VN-011" in txt
    assert "Нарушения эквивалентности" in txt
    assert "вопросов анкеты всего" in txt and "целевых эталона" in txt


# Критика принципов 2026-09-02: целевые сверяются и на слое моделей;
# строгий режим вопросов — критерий ступени ИДАК-0.

def _objective(model: dict, oid: str) -> dict:
    return next(o for o in model["objectives"] if o["id"] == oid)


def test_целевые_на_слое_моделей_по_якорям():
    cand = _h0()
    _objective(cand, "O-ROST")["direction"] = "maximize"
    res = eq.equivalence(cand, _h0())
    assert res["verdict"] == "NOT_EQUIVALENT"
    assert any("целевая O-ROST" in p for p in res["problems"])
    # Идентификатор и формулировка целевой — вне сравнения.
    cand2 = _h0()
    o = _objective(cand2, "O-PAIR")
    o["id"], o["statement"] = "OBJ-77", "иная формулировка"
    assert eq.equivalence(cand2, _h0())["verdict"] == "EQUIVALENT"
    # Целевая кандидата без якоря эталона — «сверх эталона», ДАЖЕ если
    # её зона/направление/метрика дублируют эталонную (прогон №3 H0:
    # записи вопросов прочитаны целевыми — множество по зоне их
    # схлопывало); пропавшая — «не покрыта».
    cand3 = _h0()
    twin = copy.deepcopy(_objective(cand3, "O-PAIR"))
    twin["id"], twin["sources"] = "O-DUP", [{"sid": "S2", "anchor": "x5"}]
    cand3["objectives"].append(twin)
    res3 = eq.equivalence(cand3, _h0())
    assert res3["verdict"] == "NOT_EQUIVALENT"
    assert any("сверх эталона: целевая O-DUP" in p for p in res3["problems"])
    assert res3["counts"]["objectives_extra"] == 1
    cand4 = _h0()
    cand4["objectives"] = [x for x in cand4["objectives"]
                           if x["id"] != "O-PAIR"]
    assert any("целевая O-PAIR: не покрыта" in p
               for p in eq.equivalence(cand4, _h0())["problems"])
    # Целевая на якоре эталона, но с чужой зоной — расхождение.
    cand5 = _h0()
    _objective(cand5, "O-PAIR")["zone"] = "BOTH"
    assert any("целевая O-PAIR" in p
               for p in eq.equivalence(cand5, _h0())["problems"])


def test_критерий_целевой_сравнивается_когда_поле_есть():
    et = _h0()
    _objective(et, "O-PAIR")["criterion"] = {"kind": "MIN_PERSON_DAYS",
                                              "rate_per_duty": 100}
    same = copy.deepcopy(et)
    assert eq.equivalence(same, et)["verdict"] == "EQUIVALENT"
    other = copy.deepcopy(et)
    _objective(other, "O-PAIR")["criterion"]["rate_per_duty"] = "90"
    res = eq.equivalence(other, et)
    assert any("целевая O-PAIR" in p for p in res["problems"])
    # Эталон БЕЗ носителя в модели критерий у кандидата не судит:
    # блок, выведенный из применённого ответа, — законен (ставку
    # сверяет слой пакетов).
    assert eq.equivalence(other, _h0())["verdict"] == "EQUIVALENT"
    # Служебные поля блока вне сравнения; словарь ставок — канон.
    rich = copy.deepcopy(et)
    _objective(rich, "O-PAIR")["criterion"].update(
        {"label": "минимум человеко-дней", "version": "M-036",
         "effective_from": "2026-01-01", "as_written": "100 у.е./день",
         "source_objective": "O-PAIR"})
    assert eq.equivalence(rich, et)["verdict"] == "EQUIVALENT"
    et2 = copy.deepcopy(et)
    _objective(et2, "O-PAIR")["criterion"] = {
        "kind": "MIN_PERSON_DAYS", "rates": {"flight": "100", "work": 60}}
    same2 = copy.deepcopy(et2)
    _objective(same2, "O-PAIR")["criterion"]["rates"] = {"work": 60.0,
                                                        "flight": 100}
    assert eq.equivalence(same2, et2)["verdict"] == "EQUIVALENT"
    diff2 = copy.deepcopy(et2)
    _objective(diff2, "O-PAIR")["criterion"]["rates"]["work"] = 61
    assert eq.equivalence(diff2, et2)["verdict"] == "NOT_EQUIVALENT"


def test_слой_пакетов_критерий_явным_блоком_и_словарём_ставок(tmp_path):
    et = _pkg(tmp_path, "et.zip", 10, 100)
    # Явный блок criterion пакета (r55-CDPM: из документа) — первичен и
    # эквивалентен JSON ответа при той же ставке.
    p = tmp_path / "blk.zip"
    obj = json.loads(zipfile.ZipFile(et).read("objective.json"))
    obj["pairing"]["criterion"] = {"kind": "MIN_PERSON_DAYS",
                                   "label": "человеко-дни",
                                   "rate_per_duty": "100",
                                   "rates_version": "M-036",
                                   "source_rule": "Q-3a",
                                   "as_written": "100 у.е."}
    obj["pairing"]["airline_answer"] = {"raw": "нет ответа"}
    mani = json.loads(zipfile.ZipFile(et).read("manifest.json"))
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("objective.json", json.dumps(obj, ensure_ascii=False))
        z.writestr("manifest.json", json.dumps(mani, ensure_ascii=False))
    assert eq.diff_packages(p, et)["problems"] == []
    obj["pairing"]["criterion"] = {"kind": "MIN_PERSON_DAYS",
                                   "rates": {"flight": 100, "work": 60}}
    p2 = tmp_path / "rates.zip"
    with zipfile.ZipFile(p2, "w") as z:
        z.writestr("objective.json", json.dumps(obj, ensure_ascii=False))
        z.writestr("manifest.json", json.dumps(mani, ensure_ascii=False))
    assert any("критерий" in x for x in eq.diff_packages(p2, et)["problems"])


def test_слой_форм_по_разделам():
    """Структура ВМП (владелец 2026-09-02): обе модели проецируются в
    форму; строки сверяются по разделам и якорям; место строки — канон."""
    res = eq.equivalence(_h0(), _h0())
    assert res["verdict"] == "EQUIVALENT", res["problems"]
    assert res["forms"] is not None
    assert res["counts"]["form_rows"] == res["counts"]["form_rows_ok"] > 0
    assert res["counts"]["form_extra"] == 0
    # Правило пейринга в разделе ростеринга — нарушение «место» слоя
    # форм (и зоны — слоя моделей).
    cand = _h0()
    _rule(cand, "VN-010")["zone"] = "ROSTERING"
    res2 = eq.equivalence(cand, _h0())
    assert any("место" in p and "VN-010" in p for p in res2["problems"]), \
        res2["problems"]
    # Коэффициент штрафа — канон раздела б).
    cand3 = _h0()
    _rule(cand3, "VN-030")["weight"] = 11
    res3 = eq.equivalence(cand3, _h0())
    assert any("coefficient" in p and "VN-030" in p for p in res3["problems"])
    # Лишняя строка в разделе — «сверх эталона в разделе».
    cand4 = _h0()
    cand4["rules"].append({"id": "R-X", "kind": "HARD", "zone": "PAIRING",
                           "title": "Лишнее", "statement": "x",
                           "sources": [{"sid": "S1", "anchor": "нет"}]})
    res4 = eq.equivalence(cand4, _h0())
    assert any("сверх эталона в разделе" in p and "R-X" in p
               for p in res4["problems"])
    # Без слоя форм — прежний гейт; причина пропуска — ключ, не модуль.
    off = eq.equivalence(_h0(), _h0(), forms=False)
    assert off["forms"] is None
    assert "ключом" in eq.render_report(off)
    txt = eq.render_report(res)
    assert "Слой форм" in txt and "ч.1 Пейринг" in txt


def test_слой_форм_снятые_и_исключения_вне_сравнения_место_по_классу():
    """Аудит №1: приложение «снято» (OUT) и исключения (CASE) — вне
    сравнения, как в слое моделей; «место» судится только среди
    разделов того же класса (правило ≠ критерий на том же якоре)."""
    cand = _h0()
    cand["rules"].append({"id": "VN-X", "kind": "OUT", "zone": "PAIRING",
                          "title": "Снято", "statement": "x",
                          "sources": [{"sid": "S1", "anchor": "нет"}]})
    cand["rules"].append({"id": "VN-C", "kind": "CASE", "zone": "PAIRING",
                          "title": "Исключение", "statement": "y",
                          "sources": [{"sid": "S1", "anchor": "нет-2"}]})
    res = eq.equivalence(cand, _h0())
    assert res["verdict"] == "EQUIVALENT", res["problems"]
    # Кандидат без правила VN-050 (делит якорь с целевой O-ROST): слой
    # форм говорит «не покрыта», а не «место — стоит в разделе
    # критериев».
    cand2 = _h0()
    cand2["rules"] = [r for r in cand2["rules"] if r["id"] != "VN-050"]
    res2 = eq.equivalence(cand2, _h0())
    assert any("VN-050: не покрыта" in p for p in res2["problems"])
    assert not any("место" in p and "VN-050" in p for p in res2["problems"])
    # Допуск яруса — структурно, порядок ключей словаря не важен.
    a = {"id": "O", "direction": "minimize", "metric": "m", "tier": 2,
         "tolerance": {"value": 5, "unit": "percent"}}
    b = dict(a, tolerance={"unit": "percent", "value": 5})
    assert eq._form_row_canon(a, ("pairing", "criteria"), a) == \
        eq._form_row_canon(b, ("pairing", "criteria"), a)


def test_слой_форм_легаси_без_посадки_и_открытые_вопросы_едино():
    """Легаси-свод без посадки (семья в export_solver, параметры
    массивом) шаблоном, категорией, параметрами и единицей нарушения
    слоем форм не судится; открытые вопросы оба слоя считают одним
    предикатом (статус ≠ «применён»)."""
    cand = _h0()
    r30 = _rule(cand, "VN-030")
    del r30["template_fit"]
    r30["export_solver"] = {"family": "layover_penalty"}
    r11 = _rule(cand, "VN-011")
    del r11["template_fit"]
    r11["params"] = [{"name": "long_duty_threshold", "value": 720,
                      "unit": "duration_minutes", "relation": "max"},
                     {"name": "duty_extension_cap", "value": 780,
                      "unit": "duration_minutes", "relation": "max"}]
    res = eq.equivalence(cand, _h0())
    assert res["verdict"] == "EQUIVALENT", res["problems"]
    cand2 = _h0()
    cand2["questions"][0]["answer"]["status"] = "ожидает обработчика"
    res2 = eq.equivalence(cand2, _h0())
    assert res2["counts"]["open_questions"] == 1
    assert len(res2["forms"]["open_questions"]) == 1


def test_форма_пакета_судится_и_сверяется_с_проекцией(tmp_path):
    """Пакет кандидата несёт vmp-form.json (выход конвейера): гейт
    судит его и сверяет тождество с проекцией модели той же стороны."""
    from cdpm import vmp_form as vf
    cand = _h0()
    et = _pkg(tmp_path, "et.zip", 10, 100)
    # Тождественная форма в пакете — чисто.
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(et) as src, zipfile.ZipFile(good, "w") as z:
        for name in src.namelist():
            z.writestr(name, src.read(name))
        z.writestr("vmp-form.json", json.dumps(vf.to_form(cand),
                                                ensure_ascii=False))
    res = eq.equivalence(cand, _h0(), good, et)
    assert res["forms"]["identity_problems"] == []
    assert res["verdict"] == "EQUIVALENT", res["problems"]
    # Форма пакета разошлась с моделью (коэффициент подменён) —
    # нарушение тождества и расхождение с эталоном.
    tampered = vf.to_form(cand)
    for row in tampered["pairing"]["soft"]:
        if row["id"] == "VN-030":
            row["penalty"]["coefficient"] = 99
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(et) as src, zipfile.ZipFile(bad, "w") as z:
        for name in src.namelist():
            z.writestr(name, src.read(name))
        z.writestr("vmp-form.json", json.dumps(tampered, ensure_ascii=False))
    res2 = eq.equivalence(cand, _h0(), bad, et)
    assert res2["verdict"] == "NOT_EQUIVALENT"
    assert any("форма пакета ≠ проекция модели" in p and "VN-030" in p
               for p in res2["problems"])
    assert any("coefficient" in p and "VN-030" in p and "99" in p
               for p in res2["forms"]["problems"])
    # Открытый вопрос кандидата — не нарушение тождества (его судит
    # сам слой форм один раз).
    cand3 = _h0()
    cand3["questions"][0]["answer"]["status"] = "открыт"
    good3 = tmp_path / "good3.zip"
    with zipfile.ZipFile(et) as src, zipfile.ZipFile(good3, "w") as z:
        for name in src.namelist():
            z.writestr(name, src.read(name))
        z.writestr("vmp-form.json", json.dumps(vf.to_form(cand3),
                                                ensure_ascii=False))
    res3 = eq.equivalence(cand3, _h0(), good3, et)
    assert res3["forms"]["identity_problems"] == []
    assert sum(1 for p in res3["forms"]["problems"]
               if "открытый вопрос" in p) == 1


def test_ужесточения_слоя_форм_аудита_2():
    """Аудит №2: ставка немашинного критерия сравнивается и без вида;
    CASE зоны BOTH вне сравнения; источник-ответ не якорь; пометка
    исключения у определения — канон; строка на двух якорях не двоится;
    недоступный модуль формы при forms=True — отказ, а не пропуск."""
    # (M10) ставка НЕмашинного критерия (носитель objectives[].rate,
    # как у O-ROST эталона комплекта H0): подмена значения или единицы
    # — нарушение, хотя вида (kind) у строки нет.
    rate = {"value": 1, "per": "час разброса кредитных часов",
            "as_written": "ставка 1 условная единица за час разброса"}
    et = _h0()
    _objective(et, "O-ROST")["rate"] = copy.deepcopy(rate)
    assert "criterion" not in _objective(et, "O-ROST")
    same = copy.deepcopy(et)
    assert eq.equivalence(same, et)["verdict"] == "EQUIVALENT"
    cand = copy.deepcopy(et)
    _objective(cand, "O-ROST")["rate"]["value"] = 7
    res = eq.equivalence(cand, et)
    assert res["verdict"] == "NOT_EQUIVALENT"
    assert any("O-ROST" in p and "rate" in p for p in res["problems"])
    cand_per = copy.deepcopy(et)
    _objective(cand_per, "O-ROST")["rate"]["per"] = "смена"
    assert eq.equivalence(cand_per, et)["verdict"] == "NOT_EQUIVALENT"
    # Ставки нет вовсе — тоже расхождение.
    cand_none = copy.deepcopy(et)
    del _objective(cand_none, "O-ROST")["rate"]
    assert eq.equivalence(cand_none, et)["verdict"] == "NOT_EQUIVALENT"
    # (M11) исключение (CASE) зоны BOTH — вне сравнения обоих слоёв.
    cand2 = copy.deepcopy(et)
    cand2["rules"].append({"id": "VN-CB", "kind": "CASE", "zone": "BOTH",
                           "title": "Исключение мира", "statement": "x",
                           "sources": [{"sid": "S1", "anchor": "нет-3"}]})
    assert eq.equivalence(cand2, et)["verdict"] == "EQUIVALENT"
    # (m-13) источник-ответ якорем не считается.
    assert eq._anchors({"sources": [{"question": "Q-1", "revision": 1}]}) == []
    assert eq._anchors({"sources": [{"sid": "S1", "anchor": "абз.1"},
                                    {"question": "Q-1"}]}) == ["S1|абз.1"]
    # (m-14) пометка исключения у определения — канон слоя форм.
    cand3 = copy.deepcopy(et)
    _rule(cand3, "VN-003")["excluded_until_data"] = {
        "question": "Q-3b-нет-данных-003", "revision": 1}
    res3 = eq.equivalence(cand3, et)
    assert res3["verdict"] == "NOT_EQUIVALENT"
    assert any("VN-003" in p and "excluded_until_data" in p
               for p in res3["problems"])
    # (m-17) строка эталона с двумя якорями покрыта одной строкой —
    # в отчёте она названа один раз.
    cand4, et4 = _h0(), _h0()
    for m in (cand4, et4):
        _rule(m, "VN-020")["sources"].append({"sid": "S1",
                                              "anchor": "абз.4-дубль"})
    res4 = eq.equivalence(cand4, et4)
    assert res4["verdict"] == "EQUIVALENT", res4["problems"]
    row = next(r for r in res4["forms"]["rows"] if r["etalon_id"] == "VN-020")
    assert row["candidate_ids"] == ["VN-020"]
    # (M12) слой форм запрошен, модуль недоступен — отказ словами.
    import cdpm
    real = eq._forms_of
    try:
        eq._forms_of = lambda *a, **k: None
        with pytest.raises(eq.EquivalenceError, match="слой форм"):
            eq.equivalence(_h0(), _h0())
    finally:
        eq._forms_of = real
    assert cdpm is not None
    # (m-15) кривой вход слоя форм — отказ словами (CLI код 2).
    broken = copy.deepcopy(et)
    broken["rules"][0].pop("id")
    with pytest.raises(eq.EquivalenceError, match="не проецируется"):
        eq.equivalence(broken, _h0())


def test_строгий_режим_вопросов_идак_0():
    et = _h0()
    res = eq.equivalence(et, et)
    assert res["verdict"] == "EQUIVALENT"
    assert res["counts"]["questions_total"] == len(et["questions"]) > 0
    strict = eq.equivalence(et, et, strict_questions=True)
    assert strict["verdict"] == "NOT_EQUIVALENT"
    assert any("строгий режим" in p for p in strict["problems"])
    assert "строгий режим" in eq.render_report(strict)
    cand = _h0()
    cand["questions"] = []
    ok = eq.equivalence(cand, et, strict_questions=True)
    assert ok["verdict"] == "EQUIVALENT", ok["problems"]
    assert ok["counts"]["questions_total"] == 0
