# -*- coding: utf-8 -*-
"""K06-барьер волны 17а (контракт П0 §3.5, §9 и поправка 12.11 от
12.09.2026): CDPM переходит с закона 1 («запись каталога объявила виды
исполнителей») на закон 2 («поддержка точной записи подтверждена»).

Закон 2 в переходном носителе: семья ПОДДЕРЖАНА, если у её ТОЧНОЙ
записи (раздел `families`, id) каждый объявленный записью вид
исполнителя имеет состояние «есть» хотя бы у ОДНОГО продукта по
объединению ДВУХ файлов привязок — `vmp/bindings.json` (COSMOS) и
`cdpm/bindings.json` (CDPM) — через нейтральную функцию читателя
`template_catalog.bindings.best_states` (ранг «есть > заявлен > нет»).
Исключений по имени вида нет: шаблон проверки данных — такой же
объявленный вид, и исполняет его код CDPM своим файлом привязок.
Свежесть КАЖДОГО файла — числовая ТРОЙКА версии и полный отпечаток
действующего каталога ОДНОЙ функцией закона
`template_catalog.bindings.matches_catalog` (закон «A», поручение
GPT161/A от 13.09.2026: одна смена допустимого суффикса версии свежести
не лишает); несвежий, отсутствующий, чужой и нечитаемый файл клеток не даёт
(fail-closed), но исключением не становится. Всё прочее — не поддержка:
вид без клетки, «нет», «заявлен» (заявка и черновик исполнимости не
дают), слово вне словаря, вид, подтверждённый у ДРУГОЙ семьи, семья вне
записей каталога со статусом «есть». Семья без подтверждённой поддержки
держит готовность затронутых задач ниже «в», краснит гейт и отказывает
выпуску кодом `support-missing` с адресом записи в `items`.

Здесь же — три находки неавторского ревью 12.09.2026: F1 (две строки
одной идентичности сворачивались last-wins — вердикт зависел от порядка),
F2 (правило объявленной семьи без поддержки получало вопрос линии данных
авиакомпании, и вопрос не снимался после появления поддержки), F4 (кэш
свидетельства по (mtime, размер) отдавал прежний вердикт на новых
байтах).

Ручка службы `GET /api/records/{section}/{id}/support`, ключ манифеста
`support` и приёмный пересчёт COSMOS — волна 18: здесь не проверяются.
Подделка вносится ТОЛЬКО на стороне входного свидетельства (файлы
привязок), проверяемый расчёт не подменяется."""
import copy
import json
import os

import pytest

from catalog_copies import copy_catalog, use_catalog
from cdpm import catalog_bridge as cb
from cdpm import export_task_package as tp
from cdpm import model as m
from cdpm.gates import gate_task_package as gate
from template_catalog.bindings import matches_catalog
from template_catalog.manifest import parse_version

REV = m.registry_fingerprint()
#: Настоящие свидетельства площадки — читаются ДО подмены переменных.
REAL = {p: cb.support_path(p) for p in cb.SUPPORT_PRODUCTS}
#: Независимый эталон: виды, ОБЪЯВЛЕННЫЕ записью `rest_floors`. Все они
#: нужны переходно — исключений по имени вида нет (§12.11 п. 1).
REST_DECLARED = ("builder_method", "check_code", "check_template",
                 "export_form", "kotlin_constraint", "kotlin_knob")
#: Вид, который у семей каталога 0.5.0 исполняет КОД CDPM: в файле
#: COSMOS он «нет» с заметкой «исполнитель — линия данных CDPM».
CDPM_KIND = "check_template"


# --------------------------------------------------------------------
# Свидетельство: читаем настоящие файлы, подменяем переменными площадки
# --------------------------------------------------------------------

def _real(product):
    return json.loads(REAL[product].read_text(encoding="utf-8"))


def _put(tmp_path, monkeypatch, product, doc, name):
    path = tmp_path / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(cb.SUPPORT_ENV[product], str(path))
    return path


def _evidence(tmp_path, monkeypatch, *cosmos, cdpm=(), tag="0"):
    """Оба файла привязок — копии настоящих с правками; обе двери
    площадки переведены на копии. Позиционные правки — файлу COSMOS
    (его правит большинство проверок), `cdpm=` — файлу CDPM."""
    out = {}
    for product, muts in (("COSMOS", cosmos), ("CDPM", cdpm)):
        doc = _real(product)
        for mutate in muts:
            mutate(doc)
        out[product] = _put(tmp_path, monkeypatch, product, doc,
                            f"{product}-{tag}.json")
    return out


def _absent(tmp_path, monkeypatch, product, tag="0"):
    monkeypatch.setenv(cb.SUPPORT_ENV[product],
                       str(tmp_path / f"нет-{product}-{tag}.json"))


def _rows(doc, fam):
    return [r for r in doc["bindings"]
            if r.get("section") == "families" and r.get("id") == fam]


def _drop(fam):
    """Убрать собственные клетки записи целиком."""
    def go(doc):
        doc["bindings"] = [r for r in doc["bindings"]
                           if not (r.get("section") == "families"
                                   and r.get("id") == fam)]
    return go


def _drop_kind(fam, kind):
    def go(doc):
        for row in _rows(doc, fam):
            row["executors"] = [e for e in row["executors"]
                                if e.get("kind") != kind]
    return go


def _state(fam, kind, state, **extra):
    """Состояние клеток вида: «есть» → «нет»/«заявлен» по форме читателя
    привязок (у не-«есть» поля исполнителя пусты, заметка обязательна)."""
    def go(doc):
        for row in _rows(doc, fam):
            for ex in row["executors"]:
                if ex.get("kind") == kind:
                    ex.update({"state": state, "lang": None, "file": None,
                               "symbol": None,
                               "note": "снято проверкой K06-барьера"})
                    ex.update(extra)
    return go


def _header(**fields):
    def go(doc):
        doc.update(fields)
    return go


def _retarget(fam, section):
    def go(doc):
        for row in _rows(doc, fam):
            row["section"] = section
    return go


def _split_rows(fam, kind):
    """ДВЕ строки одной идентичности (раздел, id): клетки вида — в
    первую, остальные — во вторую. Живой класс F1: словарь «id → строка»
    оставлял только последнюю строку, и вердикт зависел от порядка."""
    def go(doc):
        rows = _rows(doc, fam)
        assert rows, fam
        head = rows[0]
        mine = [e for e in head["executors"] if e.get("kind") == kind]
        rest = [e for e in head["executors"] if e.get("kind") != kind]
        assert mine and rest, (fam, kind)
        head["executors"] = mine
        twin = {"section": "families", "id": fam, "executors": rest,
                "stand_verified": list(head.get("stand_verified") or [])}
        doc["bindings"].insert(doc["bindings"].index(head) + 1, twin)
    return go


def _reverse_rows(doc):
    doc["bindings"].reverse()


# --------------------------------------------------------------------
# Свод для готовности, выпуска и гейта
# --------------------------------------------------------------------

def _ready_doc():
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1",
                 "revision": 2},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [
            {"id": "R-H", "title": "Жёсткое", "statement": "x ≤ 1",
             "zone": "BOTH", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
            {"id": "R-S", "title": "Ночёвки",
             "statement": "штраф за ночь вне базы: вес 10",
             "zone": "PAIRING", "kind": "SOFT",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
        ],
        "questions": [
            {"id": "Q-3a-GOAL", "part": "3a", "title": "Целевая",
             "text": "т", "answer_format": {"type": "registry"},
             "rule_refs": ["R-S"],
             "source_refs": [{"sid": "S1", "anchor": "п.1"}],
             "answer": {"raw": "таблица", "status": "применён",
                        "revision": 2, "goal_decline": False}},
            {"id": "Q-3b-GOAL", "part": "3b", "title": "Целевая",
             "text": "т2", "answer_format": {"type": "registry"},
             "rule_refs": ["R-H"],
             "source_refs": [{"sid": "S1", "anchor": "п.1"}],
             "answer": {"raw": "любое допустимое решение",
                        "status": "применён", "revision": 2,
                        "goal_decline": True}},
        ],
        "objectives": [
            {"id": "O-1", "title": "Критерий оптимизации пейринга",
             "statement": "Минимум человеко-дней командировок; цена "
                          "рабочего дня (смены) — 100 условных единиц",
             "zone": "PAIRING", "direction": "minimize",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
        ],
    }


REST_CELLS = {"min_rest_home": {"value": 720, "quote": "12:00"},
              "min_rest_outstation": {"value": 600, "quote": "10:00"}}
DUTY_CELLS = {"max_pairing_days": {"value": 3, "quote": "3 суток"}}


def _landed(rid="R-F", zone="BOTH", family="rest_floors", cells=None):
    """Носитель с ПОЛНЫМ набором параметров: неполная семья держала бы
    готовность соседним барьером (K01), и проверка K06 стала бы ложной."""
    return {"id": rid, "title": "Посаженное", "statement": "не менее 12:00",
            "zone": zone, "kind": "HARD",
            "sources": [{"sid": "S1", "anchor": "п.1"}],
            "template_fit": {"category": "A", "registry_rev": REV,
                             "family": family,
                             "params": copy.deepcopy(
                                 cells if cells is not None else REST_CELLS)}}


def _doc_with(*rules):
    doc = _ready_doc()
    doc["rules"].extend(rules)
    return doc


def _run_dir(tmp_path, doc, name="run-9201"):
    run = tmp_path / name
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (run / "extract" / "svod.md").write_text("# свод\n", encoding="utf-8")
    return run


# --------------------------------------------------------------------
# K06b-05: положительный контроль — подтверждённая поддержка записи
# --------------------------------------------------------------------

def test_own_confirmed_support_unblocks(tmp_path, monkeypatch):
    """Клетки записи «есть» по всем объявленным видам хотя бы у одного
    продукта при каталоге того же отпечатка — поддержка есть, готовность
    «в», пакет выпускается, гейт зелёный."""
    _evidence(tmp_path, monkeypatch)
    got = cb.family_support("rest_floors", product="COSMOS")
    assert got["supported"], got["evidence"]["words"]
    ev = got["evidence"]
    assert tuple(ev["declared"]) == REST_DECLARED
    assert ev["needed"] == ev["declared"]        # переходно — все виды
    assert ev["missing"] == [] and ev["words"] == ""
    assert ev["record"] == "families/rest_floors"
    for p in cb.SUPPORT_PRODUCTS:
        assert ev["files"][p]["ok"] and len(ev["files"][p]["sha256"]) == 64
        assert ev["files"][p]["catalog_fingerprint"] == REV
    doc = _doc_with(_landed())
    assert m.readiness(doc)["state"] == "в"
    files = tp.build_package(_run_dir(tmp_path, doc))
    pkg = tmp_path / "ok.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(pkg)
    assert problems == [], problems


def test_every_family_of_the_catalog_is_supported_by_the_two_files():
    """Ложных отказов по каталогу 0.5.0 быть не должно: по ДВУМ файлам
    привязок дерева у каждой семьи каждый объявленный вид имеет «есть»
    хотя бы у одного продукта (разведка COSMOS). Проверка живая — не
    список имён: она краснеет ровно тогда, когда выпуск каталога забыл
    перевыпустить один из файлов."""
    bad = {fam: cb.family_support(fam)["evidence"]["words"]
           for fam in cb.family_records()
           if not cb.family_support(fam)["supported"]}
    assert bad == {}, bad


# --------------------------------------------------------------------
# §12.11 п. 1: закон один на все виды, и читается по ОБОИМ файлам
# --------------------------------------------------------------------

def test_kind_executed_by_cdpm_counts_and_has_no_exemption_by_name(
        tmp_path, monkeypatch):
    """Главный закон поправки 12.11: вид `check_template` у семей
    каталога 0.5.0 стоит «нет» в файле COSMOS и «есть» в файле CDPM —
    исключения ПО ИМЕНИ вида нет, но и ложного отказа нет: клетка CDPM
    засчитывается. Убрали файл CDPM — тот же вид становится пробелом, и
    слова называют состояние у КАЖДОГО продукта."""
    _evidence(tmp_path, monkeypatch, tag="оба")
    ev = cb.family_support("rest_floors")["evidence"]
    assert ev["kinds"][CDPM_KIND] == {"COSMOS": "нет", "CDPM": "есть"}
    _absent(tmp_path, monkeypatch, "CDPM", tag="один")
    got = cb.family_support("rest_floors")
    assert not got["supported"]
    assert got["evidence"]["missing"] == [CDPM_KIND]
    assert f"{CDPM_KIND} (COSMOS: нет; CDPM: —)" \
        in got["evidence"]["words"]
    assert "файла привязок" in got["evidence"]["words"]   # беда названа


def test_three_kit_families_are_not_refused_by_one_file_law(tmp_path,
                                                            monkeypatch):
    """Семьи всех трёх наборов круга, у которых `check_template`
    исполняет CDPM, поддержаны по двум файлам и НЕ поддержаны по одному
    — ровно тот ложный отказ, ради которого закон стал общим."""
    _evidence(tmp_path, monkeypatch, tag="два")
    kits = ("crew_composition", "duty_limits", "fleet_exclusions",
            "rest_floors")
    assert [f for f in kits if not cb.family_support(f)["supported"]] == []
    _absent(tmp_path, monkeypatch, "CDPM", tag="два")
    assert [f for f in kits if cb.family_support(f)["supported"]] == []


# --------------------------------------------------------------------
# K06b-01: вид у ДРУГОЙ семьи этой не помогает (oracle предпроверки)
# --------------------------------------------------------------------

def test_kind_confirmed_at_another_family_does_not_support_this_one(
        tmp_path, monkeypatch):
    """K06b-01: те же виды подтверждены у `duty_limits`, а собственных
    клеток у `rest_floors` нет ни в одном файле — `rest_floors` не
    поддержана. Глобальный перечень видов продукта записи не
    засчитывается."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),))
    assert cb.family_support("duty_limits")["supported"]
    got = cb.family_support("rest_floors")
    assert not got["supported"]
    assert got["evidence"]["missing"] == list(REST_DECLARED)
    assert all(w.endswith("(COSMOS: —; CDPM: —)")
               for w in got["evidence"]["missing_words"])
    assert m.readiness(_doc_with(_landed(family="duty_limits",
                                         cells=DUTY_CELLS)))["state"] == "в"
    assert m.readiness(_doc_with(_landed()))["state"] == "а"


def test_precheck_oracle_a_needs_its_own_cell_in_any_file(tmp_path,
                                                          monkeypatch):
    """Oracle предпроверки K06b-01 на ДВУХ файлах: у записи A вид k2
    «нет», у записи B тот же вид «есть» — A не поддержана (клетка B ей
    не помогает ни из какого файла); появилась собственная клетка A.k2
    «есть» в файле CDPM — A поддержана. Закон судит идентичность
    записи, а не продукт и не файл."""
    a, b, k2 = "rest_floors", "duty_limits", "kotlin_knob"
    _evidence(tmp_path, monkeypatch, _state(a, k2, "нет"), tag="oracle-1")
    assert cb.family_support(b)["supported"]          # у B вид «есть»
    got = cb.family_support(a)
    assert not got["supported"] and got["evidence"]["missing"] == [k2]
    assert got["evidence"]["kinds"][k2] == {"COSMOS": "нет"}
    # Появилась СОБСТВЕННАЯ клетка A.k2 «есть» — и в файле ДРУГОГО
    # продукта (закон судит запись, а не файл и не продукт).
    _evidence(tmp_path, monkeypatch, _state(a, k2, "нет"),
              cdpm=(_copy_cell_from_cosmos(a, a, k2),), tag="oracle-2")
    got = cb.family_support(a)
    assert got["supported"], got["evidence"]["words"]
    assert got["evidence"]["kinds"][k2] == {"COSMOS": "нет", "CDPM": "есть"}


def _copy_cell_from_cosmos(src_fam, dst_fam, kind):
    """Клетка «есть» вида из НАСТОЯЩЕГО файла COSMOS — в документ, куда
    правку отдали (так проверка кладёт исполнителя другого продукта, не
    выдумывая полей читателя)."""
    donor = None
    for row in _rows(_real("COSMOS"), src_fam):
        donor = next((e for e in row["executors"]
                      if e.get("kind") == kind and e.get("state") == "есть"),
                     donor)
    assert donor is not None, (src_fam, kind)

    def go(doc):
        target = _rows(doc, dst_fam)
        if not target:
            doc["bindings"].append({"section": "families", "id": dst_fam,
                                    "executors": [], "stand_verified": []})
            target = _rows(doc, dst_fam)
        target[0]["executors"].append(copy.deepcopy(donor))
    return go


def test_adding_or_reordering_another_family_changes_nothing(
        tmp_path, monkeypatch):
    """K06b-01: перестановка и добавление строк ДРУГИХ записей вердикта
    этой не меняют — судятся только собственные клетки."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"), _reverse_rows,
              cdpm=(_drop("rest_floors"),))
    assert not cb.family_support("rest_floors")["supported"]
    assert cb.family_support("duty_limits")["supported"]


# --------------------------------------------------------------------
# K06b-02: объявление, «нет», «заявлен» и черновик — не поддержка
# --------------------------------------------------------------------

def test_declaration_without_confirmation_is_not_support(
        tmp_path, monkeypatch):
    """Закон 1 остаётся словом каталога о намерении: запись объявляет
    виды (`family_has_executor` — истина), а исполнимости не даёт —
    её судит закон 2 (`family_support`)."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),))
    assert cb.family_has_executor("rest_floors") is True
    assert cb.family_support("rest_floors")["supported"] is False
    assert m.support_problems(_doc_with(_landed()))[0]["family"] \
        == "rest_floors"


@pytest.mark.parametrize("state,extra", [("нет", {}),
                                         ("заявлен", {"cr_key": "CR-77"})])
def test_state_no_or_filed_request_is_not_support(tmp_path, monkeypatch,
                                                  state, extra):
    """K06b-02: клетка «нет» и клетка «заявлен» (поданная заявка,
    одобренный черновик исполнителя) исполнимости не дают; слова
    называют вид и состояние у каждого продукта."""
    _evidence(tmp_path, monkeypatch,
              _state("rest_floors", "kotlin_constraint", state, **extra),
              tag=f"state-{state}")
    got = cb.family_support("rest_floors")
    assert not got["supported"]
    assert got["evidence"]["kinds"]["kotlin_constraint"] == {"COSMOS": state}
    assert got["evidence"]["missing"] == ["kotlin_constraint"]
    assert f"kotlin_constraint (COSMOS: {state}; CDPM: —)" \
        in got["evidence"]["words"]


def test_family_without_a_catalog_record_gives_no_executability(
        tmp_path, monkeypatch):
    """K06b-02: семьи нет среди записей каталога со статусом «есть»
    (черновик записи, заявка) — исполнимости нет, и это видно словами."""
    _evidence(tmp_path, monkeypatch)
    got = cb.family_support("no_such_family")
    assert not got["supported"]
    assert "черновик или заявка исполнимости не дают" \
        in got["evidence"]["words"]


# --------------------------------------------------------------------
# K06b-03: нужны ВСЕ объявленные виды, и только собственные клетки
# --------------------------------------------------------------------

def test_one_missing_kind_blocks_the_whole_record(tmp_path, monkeypatch):
    """K06b-03: записи не хватает ОДНОГО объявленного вида — поддержки
    нет (все объявленные клетки, а не любая случайная)."""
    _evidence(tmp_path, monkeypatch, _drop_kind("rest_floors", "kotlin_knob"),
              cdpm=(_drop_kind("rest_floors", "kotlin_knob"),))
    got = cb.family_support("rest_floors")
    assert not got["supported"]
    assert got["evidence"]["kinds"]["kotlin_knob"] == {}
    assert got["evidence"]["missing"] == ["kotlin_knob"]
    assert "kotlin_knob (COSMOS: —; CDPM: —)" in got["evidence"]["words"]
    assert got["evidence"]["kinds"]["kotlin_constraint"]["COSMOS"] == "есть"


def test_cells_of_another_section_do_not_transfer(tmp_path, monkeypatch):
    """K06b-03: те же клетки, но объявленные не тем разделом, записи не
    засчитываются — свидетельство отвергается по правилам каталога, а
    не читается «как получится»."""
    _evidence(tmp_path, monkeypatch, _retarget("rest_floors", "criteria"))
    got = cb.family_support("rest_floors")
    assert not got["supported"]
    assert "не по правилам каталога" in got["evidence"]["words"]


def test_mode_is_recorded_and_needs_the_same_kinds_early(tmp_path,
                                                         monkeypatch):
    """Ранняя граница: каталог 0.5.0 не делит виды исполнителей по
    режимам, поэтому режим записывается в свидетельство, а требуются ВСЕ
    объявленные виды записи. Деление по режимам — вместе с ручкой
    `support/1` (волна 18); выдумывать его здесь нельзя."""
    _evidence(tmp_path, monkeypatch, _drop_kind("rest_floors", "kotlin_knob"),
              cdpm=(_drop_kind("rest_floors", "kotlin_knob"),))
    for mode in ("optimization", "feasibility-soft", "feasibility-hard"):
        got = cb.family_support("rest_floors", mode=mode)
        assert not got["supported"]
        assert got["evidence"]["mode"] == mode
        assert got["evidence"]["missing"] == ["kotlin_knob"]


# --------------------------------------------------------------------
# K06b-04: отсутствие, порча и подделка свидетельства
# --------------------------------------------------------------------

@pytest.mark.parametrize("gone", list(cb.SUPPORT_PRODUCTS))
def test_missing_file_is_not_support(tmp_path, monkeypatch, gone):
    """K06b-04: файла свидетельства ЛЮБОГО из продуктов нет — его клеток
    нет (fail-closed) и беда названа словами, но второй продукт судится
    дальше: чужое молчание не отменяет своих клеток и не даёт поддержки
    из пустоты."""
    _evidence(tmp_path, monkeypatch, tag=f"gone-{gone}")
    _absent(tmp_path, monkeypatch, gone, tag=f"gone-{gone}")
    got = cb.family_support("rest_floors")
    assert not got["supported"]
    assert "файла привязок" in got["evidence"]["words"]
    assert got["evidence"]["files"][gone]["ok"] is False
    other = [p for p in cb.SUPPORT_PRODUCTS if p != gone][0]
    assert got["evidence"]["files"][other]["ok"] is True
    assert any(other in (st or {}) for st in got["evidence"]["kinds"].values())


def test_unreadable_file_is_not_support(tmp_path, monkeypatch):
    """K06b-04: повреждённый JSON — явная беда словами, не молчаливое
    «поддержано», и файл объявлен НЕГОДНЫМ (`ok: False`, клеток нет).

    Признак `ok` судится наравне со словами (обе половины, 13.09.2026):
    сам по себе нечитаемый файл клеток не даёт и без признака — брать
    их неоткуда, — поэтому fail-open в этой ветви не краснил НИ ОДНОЙ
    пробы, пока признак не судили. А читают его люди и свидетельство
    прогона: «ok у файла, который не прочитан» — неправда в носителе."""
    bad = tmp_path / "broken.json"
    bad.write_text("{не json", encoding="utf-8")
    monkeypatch.setenv(cb.SUPPORT_ENV["COSMOS"], str(bad))
    got = cb.family_support("rest_floors")
    assert not got["supported"]
    assert "не читается" in got["evidence"]["words"]
    side = got["evidence"]["files"]["COSMOS"]
    assert side["ok"] is False
    assert "не читается" in side["words"]
    assert not any("COSMOS" in (st or {})
                   for st in got["evidence"]["kinds"].values())
    # §12.11 п. 1: второй продукт судится дальше.
    assert got["evidence"]["files"]["CDPM"]["ok"] is True


def test_forged_summary_does_not_create_support(tmp_path, monkeypatch):
    """K06b-04 (класс X-01): готового вердикта «supported» свидетельство
    не несёт вовсе — дописанная в шапку производная надпись отвергается
    схемой привязок, и поддержки из неё не возникает."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              _header(supported=True, verdict="есть"),
              cdpm=(_drop("rest_floors"),), tag="forged")
    got = cb.family_support("rest_floors")
    assert not got["supported"]
    assert "не по правилам каталога" in got["evidence"]["words"]
    assert "supported" in got["evidence"]["words"]


def test_two_rows_of_one_record_are_merged_by_rank_in_any_order(
        tmp_path, monkeypatch):
    """F1: у записи ДВЕ строки одной идентичности (раздел, id) — клетки
    обеих складываются рангом каталога, и порядок строк вердикта не
    решает. Прежний свод «id → строка» оставлял последнюю строку, и
    ответ зависел от порядка файла."""
    _evidence(tmp_path, monkeypatch, _split_rows("rest_floors",
                                                 "kotlin_constraint"),
              tag="split")
    assert cb.family_support("rest_floors")["supported"]
    _evidence(tmp_path, monkeypatch,
              _split_rows("rest_floors", "kotlin_constraint"), _reverse_rows,
              tag="split-rev")
    assert cb.family_support("rest_floors")["supported"]
    # Ранг решает и внутри клетки: строка-синоним «нет» «есть» не прячет.
    def twin_no(doc):
        for row in _rows(doc, "rest_floors"):
            same = [e for e in row["executors"]
                    if e.get("kind") == "kotlin_constraint"]
            if not same:
                continue
            twin = copy.deepcopy(same[0])
            twin.update({"state": "нет", "lang": None, "file": None,
                         "symbol": None, "note": "синоним без исполнителя"})
            row["executors"].append(twin)

    _evidence(tmp_path, monkeypatch,
              _split_rows("rest_floors", "kotlin_constraint"), twin_no,
              tag="split-twin")
    assert cb.family_support("rest_floors")["supported"]
    _evidence(tmp_path, monkeypatch,
              _split_rows("rest_floors", "kotlin_constraint"), twin_no,
              _reverse_rows, tag="split-twin-rev")
    assert cb.family_support("rest_floors")["supported"]
    # Когда «есть» не осталось ни в одной клетке — поддержки нет.
    _evidence(tmp_path, monkeypatch,
              _state("rest_floors", "kotlin_constraint", "нет"), tag="none")
    assert not cb.family_support("rest_floors")["supported"]


@pytest.mark.parametrize("state", ["не проверено", None, "", "ЕСТЬ", 1])
def test_unknown_or_empty_state_word_is_not_support(tmp_path, monkeypatch,
                                                    state):
    """K06b-02 (класс X-02): слово состояния клетки — из словаря
    каталога (`есть` / `заявлен` / `нет`). Неизвестное слово, пустое,
    `null`, другой регистр и не-строка поддержки не дают: свидетельство
    отвергается правилами читателя, а не читается «как получится».
    Состояния «не проверено» ранний носитель не знает вовсе — оно
    приходит со схемой `support/1` волны 18."""
    _evidence(tmp_path, monkeypatch,
              _state("rest_floors", "kotlin_constraint", state),
              tag=f"state-{str(state)[:12]}")
    got = cb.family_support("rest_floors")
    assert not got["supported"]
    assert "не по правилам каталога" in got["evidence"]["words"]
    assert m.readiness(_doc_with(_landed()))["state"] != "в"


# --------------------------------------------------------------------
# K06b-06: продукт, версия, отпечаток каталога и ключ кэша
# --------------------------------------------------------------------

@pytest.mark.parametrize("product", list(cb.SUPPORT_PRODUCTS))
@pytest.mark.parametrize("fields,expect", [
    ({"product": "ЧУЖОЙ"}, "привязки продукта"),
    ({"catalog_fingerprint": "0" * 64}, "о другом каталоге"),
    ({"catalog_version": "0.4.0"}, "о другом каталоге"),
])
def test_wrong_product_or_catalog_blocks(tmp_path, monkeypatch, product,
                                         fields, expect):
    """K06b-06: свидетельство другого продукта, другой версии каталога
    или другого отпечатка поддержки не даёт — «устарело/не совпадает» =
    не готовность; свежесть судится у КАЖДОГО файла отдельно."""
    tag = f"{product}-{list(fields)[0]}"
    kw = {"cdpm": (_header(**fields),)} if product == "CDPM" else {}
    args = (_header(**fields),) if product == "COSMOS" else ()
    _evidence(tmp_path, monkeypatch, *args, tag=tag, **kw)
    got = cb.family_support("rest_floors")
    assert not got["supported"]
    assert expect in got["evidence"]["words"]
    assert got["evidence"]["files"][product]["ok"] is False


def test_support_is_not_a_global_cached_true(tmp_path, monkeypatch):
    """K06b-06: вердикт привязан ко входам, а не к первому успеху:
    свидетельство сменилось — сменился и ответ, и обратно."""
    _evidence(tmp_path, monkeypatch, tag="good")
    assert cb.family_support("rest_floors")["supported"]
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),), tag="bad")
    assert not cb.family_support("rest_floors")["supported"]
    _evidence(tmp_path, monkeypatch, tag="good2")
    assert cb.family_support("rest_floors")["supported"]


@pytest.mark.parametrize("product", list(cb.SUPPORT_PRODUCTS))
def test_rewrite_with_the_same_stamp_changes_the_verdict(tmp_path,
                                                         monkeypatch,
                                                         product):
    """F4: файл переписан ПО ТОМУ ЖЕ ПУТИ с тем же размером и
    восстановленной отметкой времени (`robocopy /COPY:DAT`, `cp -p`,
    двухсекундная точность FAT, откат файла) — вердикт обязан
    смениться. Кэш по (mtime, размер) отдавал прежний ответ на новых
    байтах: пакет судился бы не теми клетками, которыми выпущен."""
    paths = _evidence(tmp_path, monkeypatch, tag="f4")
    path = paths[product]
    assert cb.family_support("rest_floors")["supported"]
    st = path.stat()
    doc = _real(product)
    _drop("rest_floors")(doc)
    body = json.dumps(doc, ensure_ascii=False)
    body += " " * (st.st_size - len(body.encode("utf-8")))
    path.write_text(body, encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (st.st_size, st.st_mtime_ns)
    got = cb.family_support("rest_floors")
    assert not got["supported"], "кэш отдал вердикт по старым байтам"
    assert got["evidence"]["files"][product]["sha256"] \
        != cb.support_digest(REAL[product])


# --------------------------------------------------------------------
# Закон свежести каталога «A» (адресное поручение GPT161/A, подтверждено
# GPT163 и GPT168 §5): свежесть файла привязок — числовая ТРОЙКА версии
# И полный вычисленный канонический отпечаток. Одна смена ДОПУСТИМОГО
# суффикса (черновик → выпуск и обратно) при совпавших тройке и
# отпечатке поддержки не лишает. Закон ОДИН на дерево, и живёт он в
# `template_catalog.bindings.matches_catalog`: свидетельство поддержки
# (K06) и экран «Исполнители по продуктам» не вправе разойтись в том,
# для какого каталога написан файл продукта.
#
# До 13.09.2026 `_support_file` нёс ТРЕТЬЮ копию закона — точное
# сравнение СТРОКИ версии, и копия была СТРОЖЕ действующей: файл,
# выпущенный из того же тела каталога под снятым черновым суффиксом,
# объявлялся «о другом каталоге», и семья теряла исполнителя из-за
# одной метки выпуска.
# --------------------------------------------------------------------

def _drafted(version: str, n: int = 7) -> str:
    """Та же ТРОЙКА версии с допустимым суффиксом черновика."""
    (major, minor, patch), _ = parse_version(str(version))
    return f"{major}.{minor}.{patch}-draft.{n}"


@pytest.mark.parametrize("product", list(cb.SUPPORT_PRODUCTS))
def test_draft_suffix_alone_does_not_take_away_support(tmp_path, monkeypatch,
                                                       product):
    """Закон A, положительная сторона: файл привязок объявляет ТУ ЖЕ
    тройку версии и ТОТ ЖЕ полный отпечаток, а отличается ТОЛЬКО
    допустимым суффиксом (`0.5.0` → `0.5.0-draft.7`) — поддержка
    ОСТАЁТСЯ, и беды об этом файле нет.

    Обратимость: на точном сравнении строки версии здесь был отказ
    словами «свидетельство поддержки … о другом каталоге»."""
    own = cb.stamp()
    drafted = _drafted(own["version"])
    assert drafted != own["version"]
    kw = {"cdpm": (_header(catalog_version=drafted),)} \
        if product == "CDPM" else {}
    args = (_header(catalog_version=drafted),) if product == "COSMOS" else ()
    _evidence(tmp_path, monkeypatch, *args, tag=f"draft-{product}", **kw)
    got = cb.family_support("rest_floors")
    assert got["supported"], got["evidence"]["words"]
    assert got["evidence"]["words"] == ""
    side = got["evidence"]["files"][product]
    assert side["ok"] is True and side["words"] is None
    assert side["catalog_version"] == drafted
    assert side["catalog_fingerprint"] == own["fingerprint"]


def test_release_bindings_on_draft_catalog_keep_support(tmp_path, monkeypatch):
    """Та же сторона закона A с ДРУГОГО берега шва (половина «каталог» —
    половина «файл»): каталог — ЧЕРНОВИК той же тройки, а оба файла
    привязок выпущены к `0.5.0`. Отпечаток у них один: версия в канон
    каталога не входит (`Catalog.body()` — families, criteria,
    dictionaries), поэтому выпуск меняет только метку.

    Несимметричный закон («суффикс прощаем только файлу») прошёл бы
    первую пробу и покраснел бы здесь."""
    own = cb.stamp()
    drafted = _drafted(own["version"])
    try:
        folder = copy_catalog(tmp_path, version=drafted)
        use_catalog(monkeypatch, folder)
        cb.forget()
        assert cb.stamp() == {"version": drafted,
                              "fingerprint": own["fingerprint"]}
        _evidence(tmp_path, monkeypatch, tag="выпуск-на-черновике")
        got = cb.family_support("rest_floors")
        assert got["supported"], got["evidence"]["words"]
        for p in cb.SUPPORT_PRODUCTS:
            side = got["evidence"]["files"][p]
            assert side["ok"] is True, side["words"]
            assert side["catalog_version"] == own["version"]
    finally:
        cb.forget()


@pytest.mark.parametrize("product", list(cb.SUPPORT_PRODUCTS))
@pytest.mark.parametrize("fields,why", [
    ({"catalog_version": "0.4.0"}, "другая тройка — младшая разница"),
    ({"catalog_version": "1.5.0"}, "другая тройка — старшая разница"),
    ({"catalog_version": "0.5.1"}, "другая тройка — разница заплаты"),
    ({"catalog_version": "0.4.0-draft.1"},
     "суффикс есть, но тройка другая — послабления нет"),
    ({"catalog_version": "черновик"}, "негодная форма версии"),
    ({"catalog_version": None}, "версия не названа вовсе"),
    ({"catalog_fingerprint": "0" * 64}, "та же тройка, другой отпечаток"),
    ({"catalog_version": "0.5.0-draft.7", "catalog_fingerprint": "0" * 64},
     "суффикс прощён, но отпечаток другой"),
    ({"catalog_fingerprint": None}, "отпечаток не назван вовсе"),
])
def test_law_a_negative_controls_survive(tmp_path, monkeypatch, product,
                                         fields, why):
    """Отрицательные контроли закона A: послабление касается ТОЛЬКО
    допустимого суффикса при совпавших тройке И полном отпечатке.
    Другая тройка (в любую сторону), негодная форма версии, неназванная
    версия и любой другой отпечаток — не поддержка, отказ словами.

    Строка «суффикс прощён, но отпечаток другой» держит ВТОРУЮ половину
    закона отдельно: у черновика читатель привязок (B01) говорит об
    отставшем отпечатке лишь ПРЕДУПРЕЖДЕНИЕМ, и свидетельство, которое
    переложило бы отпечаток на `check_bindings`, тут бы позеленело."""
    tag = f"{product}-{why[:18]}-{list(fields)[0]}"
    kw = {"cdpm": (_header(**fields),)} if product == "CDPM" else {}
    args = (_header(**fields),) if product == "COSMOS" else ()
    _evidence(tmp_path, monkeypatch, *args, tag=tag, **kw)
    got = cb.family_support("rest_floors")
    assert not got["supported"], why
    слова = got["evidence"]["words"]
    assert "о другом каталоге" in слова, why
    assert got["evidence"]["files"][product]["ok"] is False, why
    # Находка ревью 13.09: подстроки «о другом каталоге» мало — слова не
    # называли НИ объявленного каталога файла, НИ действующего, и
    # мутация «выбросить обе стороны» проходила зелёной на 92 пробах.
    # После закона A это важнее прежнего: свежий файл вправе нести
    # ДРУГУЮ строку версии, и человек обязан видеть обе стороны, чтобы
    # понять — разошлась тройка или отпечаток.
    свой = cb.catalog()
    assert свой.version() in слова, (why, слова)
    assert свой.fingerprint()[:12] in слова, (why, слова)


#: Шапки файла привязок для суда «один закон, а не два». Каждая строка —
#: (правка шапки, расходилось ли ПРЕЖНЕЕ точное сравнение строки версии
#: с законом A). Строки с True — предмет поручения GPT161/A.
_FRESHNESS_CASES = [
    ({}, False),
    ({"catalog_version": "0.5.0-draft.7"}, True),
    ({"catalog_version": "0.5.0-draft.1"}, True),
    ({"catalog_version": "0.4.0"}, False),
    ({"catalog_version": "0.5.1"}, False),
    ({"catalog_version": "1.5.0"}, False),
    ({"catalog_version": "черновик"}, False),
    ({"catalog_version": None}, False),
    ({"catalog_fingerprint": "0" * 64}, False),
    ({"catalog_version": "0.5.0-draft.7", "catalog_fingerprint": "0" * 64},
     False),
]


def test_support_file_and_reader_judge_freshness_by_one_law(tmp_path,
                                                            monkeypatch):
    """Один закон, а не два: на КАЖДОМ входе вердикт `_support_file` о
    свежести совпадает с вердиктом ЕДИНСТВЕННОЙ функции закона
    `template_catalog.bindings.matches_catalog`.

    Набор входов подобран так, что ПРЕЖНЕЕ точное сравнение строки
    версии расходилось с законом A (строки со снятым и надетым
    суффиксом черновика) — и проба ТРЕБУЕТ, чтобы такие входы в наборе
    были: вычеркнув их, её делают беззубой, и она краснеет сама."""
    cat, own = cb.catalog(), cb.stamp()
    diverged = []
    for i, (fields, expect_divergence) in enumerate(_FRESHNESS_CASES):
        doc = _real("COSMOS")
        doc.update(fields)
        path = _put(tmp_path, monkeypatch, "COSMOS", doc,
                    f"один-закон-{i}.json")
        law_a = matches_catalog(cat, doc)
        exact = (str(doc.get("catalog_version") or "") == own["version"]
                 and str(doc.get("catalog_fingerprint") or "")
                 == own["fingerprint"])           # прежняя, третья копия
        got = cb._support_file("COSMOS", cat, own, path,
                               cb.support_digest(path))
        assert got["ok"] is law_a, (fields, got["words"])
        assert (exact != law_a) is expect_divergence, fields
        if exact != law_a:
            diverged.append(fields)
    assert diverged, "набор входов беззуб: расхождения законов в нём нет"


def test_support_file_keeps_product_form_and_bindings_checks(tmp_path,
                                                             monkeypatch):
    """Перевод на закон A не съел соседних рубежей `_support_file`:
    принадлежность продукту судится РАНЬШЕ свежести (и своими словами),
    а форма и правила привязок — ПОЗЖЕ (и тоже своими). Три отказа
    остаются различимыми по словам, и все три — на файле, который по
    закону A свеж."""
    cat, own = cb.catalog(), cb.stamp()
    drafted = _drafted(own["version"])

    alien = _real("COSMOS")
    alien.update({"product": "ЧУЖОЙ", "catalog_version": drafted})
    p1 = _put(tmp_path, monkeypatch, "COSMOS", alien, "чужой.json")
    got = cb._support_file("COSMOS", cat, own, p1, cb.support_digest(p1))
    assert got["ok"] is False and "привязки продукта" in got["words"]

    broken = _real("COSMOS")
    broken["catalog_version"] = drafted
    _retarget("rest_floors", "criteria")(broken)
    p2 = _put(tmp_path, monkeypatch, "COSMOS", broken, "форма.json")
    got = cb._support_file("COSMOS", cat, own, p2, cb.support_digest(p2))
    assert got["ok"] is False and "не по правилам каталога" in got["words"]

    fresh = _real("COSMOS")
    fresh["catalog_version"] = drafted
    p3 = _put(tmp_path, monkeypatch, "COSMOS", fresh, "свежий.json")
    got = cb._support_file("COSMOS", cat, own, p3, cb.support_digest(p3))
    assert got["ok"] is True and got["words"] is None


def test_one_files_trouble_does_not_exempt_the_second_product(tmp_path,
                                                              monkeypatch):
    """§12.11 п. 1 под законом A: беда ОДНОГО файла исключением не
    становится. Файл COSMOS — о другой тройке (клеток не даёт), файл
    CDPM — со снятым суффиксом черновика (клетки даёт): второй продукт
    судится дальше, его клетки на месте, а беда первого названа
    словами."""
    own = cb.stamp()
    _evidence(tmp_path, monkeypatch, _header(catalog_version="0.4.0"),
              cdpm=(_header(catalog_version=_drafted(own["version"])),),
              tag="один-плох-второй-судится")
    ev = cb.family_support("rest_floors")["evidence"]
    assert ev["files"]["COSMOS"]["ok"] is False
    assert "о другом каталоге" in ev["files"]["COSMOS"]["words"]
    assert ev["files"]["CDPM"]["ok"] is True
    assert ev["kinds"][CDPM_KIND] == {"CDPM": "есть"}


# --------------------------------------------------------------------
# K06b-07: готовность, выпуск и гейт мимо формы
# --------------------------------------------------------------------

def test_readiness_holds_only_the_affected_task_with_words(tmp_path,
                                                           monkeypatch):
    """Семья без подтверждённой поддержки держит задачи ЗОН своих
    носителей ниже «в» словами (адрес записи, виды с состоянием у
    каждого продукта, режим прогона, переходная строгость); незатронутая
    задача остаётся готовой."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),))
    doc = _doc_with(_landed(zone="ROSTERING"),
                    _landed(rid="R-D", zone="PAIRING", family="duty_limits",
                            cells=DUTY_CELLS))
    got = m.readiness(doc)
    assert got["state"] == "б"
    assert got["pairing_ready"] and not got["rostering_ready"]
    words = m.support_problems(doc)[0]["words"]
    assert words.startswith("семья rest_floors (families/rest_floors) без "
                            "подтверждённой поддержки — виды без "
                            "исполнителя «есть» ни у одного продукта: ")
    assert "builder_method (COSMOS: —; CDPM: —)" in words
    assert "режим прогона: ростеринг" in words
    assert "переходно требуются все объявленные виды записи" in words
    assert got["rostering_open"] == {words: 1}
    assert list(got["pairing_open"]) == []
    matrix = m.readiness_matrix(doc)
    assert matrix["rostering"]["feasibility-hard"]["reasons"] == {words: 1}
    assert matrix["pairing"]["feasibility-hard"]["ready"]


def test_release_refuses_support_missing_with_the_record_address(
        tmp_path, monkeypatch):
    """Рубеж выпуска (прямой путь, мимо формы): код `support-missing`, в
    словах — семья с адресом записи, виды, режим и что делать; барьер
    отдаёт рубежу адрес точной записи `families/<id>` для `items`."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),))
    doc = _doc_with(_landed())
    problem = m.support_problems(doc)[0]
    assert problem["item"] == problem["record"] == "families/rest_floors"
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc))
    assert e.value.code == tp.SUPPORT_MISSING == "support-missing"
    assert "families/rest_floors" in str(e.value)
    assert "без подтверждённой поддержки" in str(e.value)
    # §12.11 п. 3: в `items` — адрес ТОЧНОЙ записи, не имя семьи.
    assert e.value.items == ["families/rest_floors"]
    # Хвост отказа называет закон поправки 12.11 (оба файла привязок),
    # а не «продуктом COSMOS».
    assert "по привязкам обоих продуктов" in str(e.value)
    assert "хотя бы у одного" in str(e.value)


def test_producer_gate_and_restart_keep_refusing(tmp_path, monkeypatch):
    """K06b-07: пакет, выпущенный при живом свидетельстве, на машине без
    подтверждённой поддержки краснит гейт теми же словами — повторное
    чтение и перезапуск отказ не снимают."""
    _evidence(tmp_path, monkeypatch, tag="live")
    doc = _doc_with(_landed())
    files = tp.build_package(_run_dir(tmp_path, doc))
    pkg = tmp_path / "pkg.zip"
    pkg.write_bytes(tp.write_zip(files))
    assert gate.check(pkg)[0] == []
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),), tag="gone")
    problems, _ = gate.check(pkg)
    words = m.readiness_barriers(doc)[0]["words"]
    assert any(words in p for p in problems), problems
    assert any("support-missing" in p for p in problems), problems
    assert gate.check(pkg)[0] == problems          # повтор — тот же ответ


def test_a_definition_rule_without_a_landing_needs_no_confirmed_executor(
        tmp_path, monkeypatch):
    """Граница закона 2 (переписана 12.09.2026 вместе с правкой
    асимметрии видов): подтверждённой поддержки требует ПОСАДКА на
    запись каталога, а не вид строки. Строка определения мира БЕЗ
    посадки носителем не является вовсе — её судит линия данных входной
    книгой, и снятие клеток семьи готовность не роняет; та же семья у
    ПОСАЖЕННОЙ жёсткой строки — роняет.

    Прежнее ожидание (до 12.09.2026) строило строку вида DEF С ПОСАДКОЙ
    A/B и требовало, чтобы барьер её пропустил. Оно отменено: посадка у
    немашинной строки — НАРУШЕНИЕ свода (`validate_model`: «посадка на
    шаблон допустима только у машинных правил»), и пропускать её мимо
    барьера значило бы держать асимметрию с законом 1
    (`family_has_executor` вида не спрашивает). Законную половину той
    границы — DEF без посадки — проверяет этот тест, незаконную —
    соседний."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),))
    definition = {"id": "R-DEF", "title": "Определение мира",
                  "statement": "сутки считаются от 00:00 UTC",
                  "zone": "ROSTERING", "kind": "DEF",
                  "sources": [{"sid": "S1", "anchor": "п.1"}]}
    doc = _doc_with(definition)
    assert m.validate_model(doc) == []          # свод ЗАКОНЕН
    assert m.support_problems(doc) == []
    assert m.readiness(doc)["state"] == "в"
    assert tp.build_package(_run_dir(tmp_path, doc, "run-def"))
    hard = _doc_with(_landed(rid="R-HARD", zone="ROSTERING"))
    assert [pr["family"] for pr in m.support_problems(hard)] == ["rest_floors"]
    assert m.readiness(hard)["state"] != "в"


@pytest.mark.parametrize("kind", ["DATA", "DEF", "CASE", "OUT"])
def test_a_landing_on_a_non_machine_rule_is_held_by_the_support_barrier(
        tmp_path, monkeypatch, kind):
    """Асимметрия видов между ДВУМЯ барьерами одного свода (правка
    12.09.2026, проверка указания соседней сессии).

    Закон 1 — исполнимость семьи (`catalog_bridge.family_has_executor`)
    — вида строки НЕ спрашивает. Закон 2 (`support_problems`) прежде
    судил только носителей видов HARD/SOFT, и правило вида DATA с
    посадкой A/B проскакивало мимо барьера поддержки: закон 1 истина,
    `support_problems` пуст, барьеров нет, готовность «в», и рубеж
    выпуска — который `validate_model` не зовёт — такой пакет отдавал.
    Теперь носитель у обоих барьеров один: ЛЮБАЯ посадка A/B.

    Сам свод при этом незаконен (валидатор говорит о посадке у
    немашинной строки), и гейт производителя его краснит — но стык
    двух барьеров обязан держать и БЕЗ валидатора: fail-closed.

    Минус (на прежнем коде): `support_problems` пуст, барьеров нет,
    готовность «в» и `build_package` возвращает файлы — краснеет первым
    утверждение о семье в `support_problems`."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),))
    rule = _landed(rid="R-NM", zone="BOTH")
    rule["kind"] = kind
    doc = _doc_with(rule)
    # Валидатор о такой посадке знает — но рубеж выпуска его не зовёт.
    assert any("посадка на шаблон допустима только у машинных правил"
               in p for p in m.validate_model(doc)), m.validate_model(doc)
    got = m.support_problems(doc)
    assert [pr["family"] for pr in got] == ["rest_floors"], got
    assert got[0]["rules"] == ["R-NM"]
    # Fail-closed: у любого вида, кроме SOFT, признак «жёсткий» истина —
    # держатся ВСЕ режимы задачи, а не только режимы со штрафами.
    assert got[0]["hard"] is True
    assert m.barrier_affects(got[0]) == {(t, md) for t in
                                        ("pairing", "rostering")
                                        for md in m.TASK_MODES}
    assert [b["code"] for b in m.readiness_barriers(doc)] \
        == [m.SUPPORT_MISSING]
    assert m.readiness(doc)["state"] != "в"
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, f"run-nm-{kind}"))
    assert e.value.code == tp.SUPPORT_MISSING
    assert e.value.items == ["families/rest_floors"]


def test_a_soft_landing_on_a_non_machine_rule_is_not_softened(tmp_path,
                                                              monkeypatch):
    """Продолжение fail-closed: «мягким» носитель считается ТОЛЬКО при
    виде SOFT. У смешанного носителя (SOFT рядом с DATA) признак
    `hard` — истина, и барьер держит все режимы задачи, а не только
    режимы со штрафами.

    Минус: если `hard` считать «хотя бы один HARD», смешанный носитель
    стал бы мягким и оптимизация с жёсткой выполнимостью уехала бы."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),))
    soft = _landed(rid="R-SOFT", zone="ROSTERING")
    soft["kind"] = "SOFT"
    data = _landed(rid="R-DATA", zone="ROSTERING")
    data["kind"] = "DATA"
    only_soft = m.support_problems(_doc_with(soft))
    assert only_soft[0]["hard"] is False
    mixed = m.support_problems(_doc_with(soft, data))
    assert mixed[0]["rules"] == ["R-SOFT", "R-DATA"]     # порядок свода
    assert mixed[0]["hard"] is True


# --------------------------------------------------------------------
# F2: линия данных судит по ОБЪЯВЛЕНИЮ, а исполнимость — барьером
# --------------------------------------------------------------------

def _data_doc(*rules):
    return {"meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1",
                     "revision": 2},
            "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
            "rules": list(rules), "questions": [], "objectives": []}


def _no_data_report(*rule_ids):
    return {"checks": [{"id": f"C-{rid}", "status": "не скомпилирована",
                        "reason": "нет листа «Смены»", "basis": {"rule": rid}}
                       for rid in rule_ids],
            "findings": []}


def test_declared_family_without_support_is_not_asked_by_the_data_line(
        tmp_path, monkeypatch):
    """F2: у семьи правила каталог ОБЪЯВИЛ исполнителя, но поддержка не
    подтверждена — авиакомпанию не спрашивают «считать ли до получения
    данных»: правило судится решением, а исполнимость держит K06-барьер
    со своим следом. Прежде вопрос задавался, и готовность оставалась
    «б» из-за него, а не из-за барьера."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),))
    rule = _landed(rid="R-F", zone="ROSTERING")
    doc = _data_doc(rule)
    report = _no_data_report("R-F")
    assert m.ensure_missing_data_questions(doc, report) == []
    assert doc["questions"] == [] and not rule.get("blockers")
    assert report["judged_on_solution"][0]["rule"] == "R-F"
    # Держит готовность именно барьер, и он называет причину.
    assert [pr["code"] for pr in m.readiness_barriers(doc)] \
        == [m.SUPPORT_MISSING]


def test_after_support_appears_readiness_is_v_without_a_stale_question(
        tmp_path, monkeypatch):
    """F2, жизненный цикл: поддержка появилась — готовность «в», и ни
    одного открытого вопроса «нет данных» не осталось (устаревший вопрос
    не должен держать готовность вместо барьера)."""
    _evidence(tmp_path, monkeypatch, _drop("rest_floors"),
              cdpm=(_drop("rest_floors"),), tag="нет")
    rule = _landed(rid="R-F", zone="ROSTERING")
    doc = _data_doc(rule)
    m.ensure_missing_data_questions(doc, _no_data_report("R-F"))
    assert m.readiness(doc)["state"] != "в"
    _evidence(tmp_path, monkeypatch, tag="есть")
    assert m.ensure_missing_data_questions(doc, _no_data_report("R-F")) == []
    assert [q for q in doc["questions"] if m.is_open_question(q)] == []
    assert m.readiness(doc)["state"] == "в"


def test_legacy_open_no_data_question_is_withdrawn_with_a_mark(
        tmp_path, monkeypatch):
    """F2: открытый вопрос «нет данных» прежней стадии, ВСЕ носители
    которого теперь судятся решением, снимается кодом с пометкой —
    блокировка со строк уходит, след остаётся в отчёте."""
    _evidence(tmp_path, monkeypatch)
    rule = _landed(rid="R-F", zone="ROSTERING")
    qid = m.stable_question_id("3b", "нет-данных-R-F")
    rule["blockers"] = [qid]
    doc = _data_doc(rule)
    doc["questions"] = [{
        "id": qid, "part": "3b", "title": "Данных для правила R-F нет в книге",
        "text": "Проверка правила не прогоняется. Считать ли до получения "
                "данных?",
        "answer_format": {"type": "choice",
                          "options": [{"key": "а", "text": "да"},
                                      {"key": "б", "text": "нет"}]},
        "rule_refs": ["R-F"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}]}]
    report = _no_data_report("R-F")
    assert m.ensure_missing_data_questions(doc, report) == []
    assert report["withdrawn_judged_on_solution"] == [qid]
    q = doc["questions"][0]
    assert not m.is_open_question(q)
    # A04: retain code evidence separately from any airline answer.
    from cdpm import question_resolutions
    assert "answer" not in q and question_resolutions.complete(q)
    assert q["code_resolutions"][0]["data"]["outcome"] == "executor"
    assert "K06-барьер" in q["code_resolutions"][0]["reason"]
    assert not rule.get("blockers")
    assert m.readiness(doc)["state"] == "в"


def test_rule_of_an_undeclared_family_still_gets_the_data_question(
        tmp_path, monkeypatch):
    """F2, граница: правило, чью семью каталог НЕ объявил (записи нет —
    заявка или черновик) и правило без посадки вовсе, по-прежнему
    получают вопрос линии данных: их не судит никто другой."""
    _evidence(tmp_path, monkeypatch)
    stranger = _landed(rid="R-X", zone="ROSTERING", family="нет-такой-семьи",
                       cells={})
    plain = {"id": "R-N", "title": "Без посадки", "statement": "и так ясно",
             "zone": "ROSTERING", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}]}
    doc = _data_doc(stranger, plain)
    report = _no_data_report("R-X", "R-N")
    created = m.ensure_missing_data_questions(doc, report)
    assert len(created) == 2
    assert stranger["blockers"] and plain["blockers"]
    assert "judged_on_solution" not in report


# --------------------------------------------------------------------
# §12.11 п. 5: строка паспорта прогона о привязках ОБОИХ продуктов
# --------------------------------------------------------------------

def test_passport_names_the_bytes_of_both_bindings(tmp_path, monkeypatch):
    """Паспорт = рецепт: запись о прогоне называет по каждому файлу
    привязок путь, SHA-256 СОДЕРЖИМОГО, версию с отпечатком каталога и
    версию продукта — по ним видно, ЧЕМ судили поддержку."""
    from cdpm import run_record as rr
    paths = _evidence(tmp_path, monkeypatch, tag="паспорт")
    facts = rr.bindings_evidence()
    assert set(facts) == set(cb.SUPPORT_PRODUCTS)
    for product, path in paths.items():
        got = facts[product]
        assert got["path"] == str(path)
        assert got["sha256"] == cb.support_digest(path)
        assert got["catalog_fingerprint"] == REV
        assert got["product_version"] and got["generated_from"]


def test_declaration_bindings_follow_the_shared_dictionary(tmp_path,
                                                           monkeypatch):
    """ШОВ §12.11 п. 5: словарь объявления один на обе системы и закрыт;
    необязательные имена в него добавляет симметричная правка COSMOS.
    Строка объявления следует словарю: с прежним словарём — три поля, с
    расширенным — байты обоих носителей, и запись остаётся годной по
    схеме в обоих случаях."""
    jsonschema = pytest.importorskip("jsonschema")
    from cdpm import run_record as rr
    _evidence(tmp_path, monkeypatch, tag="словарь")
    assert set(rr._bindings()) == {"catalog_version", "catalog_fingerprint",
                                   "path"}
    schema = json.loads(rr.SCHEMA_PATH.read_text(encoding="utf-8"))
    props = schema["$defs"]["bindings"]["properties"]
    for key in rr.BINDINGS_EXTRA:
        props[key] = {"type": ["string", "null"]}
    props[rr.BINDINGS_CDPM_KEY] = {"type": ["object", "null"]}
    grown = tmp_path / "run_record.schema.json"
    grown.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rr, "SCHEMA_PATH", grown)
    got = rr._bindings()
    assert set(got) == {"catalog_version", "catalog_fingerprint", "path",
                        *rr.BINDINGS_EXTRA, rr.BINDINGS_CDPM_KEY}
    assert set(got) <= set(rr.BINDINGS_KEYS)
    assert got["sha256"] == rr.bindings_evidence()["COSMOS"]["sha256"]
    assert got[rr.BINDINGS_CDPM_KEY]["sha256"] \
        == rr.bindings_evidence()["CDPM"]["sha256"]
    validator = jsonschema.Draft202012Validator(
        {"$defs": schema["$defs"], "$ref": "#/$defs/bindings"})
    assert list(validator.iter_errors(got)) == []


def test_environment_mismatch_names_which_bindings_file_differs(tmp_path,
                                                                monkeypatch):
    """Сверка «объявлено / на деле» называет КАКОЙ файл разошёлся (прежде
    факты файла COSMOS шли под подписью «привязки CDPM» — находка F8), и
    судит содержание обоих носителей, а не только версию каталога."""
    from cdpm import run_record as rr
    mine = {"catalog_version": "0.5.0", "catalog_fingerprint": "aa",
            "path": "/своё/vmp/bindings.json", "sha256": "1" * 64,
            "product_version": "1.18.5", "generated_from": "c0",
            rr.BINDINGS_CDPM_KEY: {"catalog_version": "0.5.0",
                                   "catalog_fingerprint": "aa",
                                   "path": "/своё/cdpm/bindings.json",
                                   "sha256": "2" * 64,
                                   "product_version": "1.18",
                                   "generated_from": "c1"}}
    same = copy.deepcopy(mine)
    same["path"] = "/чужое/vmp/bindings.json"          # путь не сверяется
    same[rr.BINDINGS_CDPM_KEY]["path"] = "/чужое/cdpm/bindings.json"
    assert rr.environment_mismatch({"bindings": same}, {}, mine, []) == []
    other = copy.deepcopy(same)
    other["sha256"] = "9" * 64
    other[rr.BINDINGS_CDPM_KEY]["product_version"] = "1.17"
    words = rr.environment_mismatch({"bindings": other}, {}, mine, [])
    assert any(w.startswith("привязки COSMOS (vmp/bindings.json): sha256")
               for w in words), words
    assert any(w.startswith("привязки CDPM (cdpm/bindings.json): "
                            "product_version") for w in words), words
    # Голова прежнего словаря о новых именах молчит — это не расхождение.
    old = {"catalog_version": "0.5.0", "catalog_fingerprint": "aa",
           "path": "/чужое/vmp/bindings.json"}
    assert rr.environment_mismatch({"bindings": old}, {}, mine, []) == []


def test_withdrawal_on_this_branch_keeps_a_foreign_revision_barrier(
        tmp_path, monkeypatch):
    """Б-1 ревью 13.09.2026, ВТОРАЯ ветвь снятия (правило судится
    решением): единый писатель снимал признак `pending_revision` со
    ВСЕХ ячеек, указывающих на снимаемый вопрос, — безусловно, не
    спрашивая, пересмотр ли это. Вопрос линии данных пересмотром не
    является, значит `code_close_refusal` на нём всегда `None` и
    ничего не держит: чужой барьер K04 исчезал молча.

    Дефект предсуществующий (ветвь «посадка C» ходила через того же
    писателя в принятой единице K04), но композиция 12.09.2026 перевела
    на писателя и ЭТУ ветвь — поверхность удвоилась, поэтому проба
    стоит на обеих. Первая половина шва — в
    `test_cdpm_k04_revision_barrier`."""
    _evidence(tmp_path, monkeypatch)
    rule = _landed(rid="R-F", zone="ROSTERING")
    qid = m.stable_question_id("3b", "нет-данных-R-F")
    rule["blockers"] = [qid]
    # Ячейка СОСЕДНЕГО параметра несёт признак открытого пересмотра,
    # который указывает на этот же вопрос линии данных (признак
    # подделан либо испорчен переносом) — барьер K04 стоит и называет
    # беду словами, снятию чужого вопроса он не подчиняется.
    rule["template_fit"]["params"]["min_rest_home"]["pending_revision"] = qid
    doc = _data_doc(rule)
    doc["questions"] = [{
        "id": qid, "part": "3b", "title": "Данных для правила R-F нет в книге",
        "text": "Проверка правила не прогоняется. Считать ли до "
                "получения данных?",
        "answer_format": {"type": "choice",
                          "options": [{"key": "а", "text": "да"},
                                      {"key": "б", "text": "нет"}]},
        "rule_refs": ["R-F"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}]}]
    before = m.pending_revision_facts(doc)
    assert [f["cell"] for f in before] == ["R-F.min_rest_home"]
    assert "не является пересмотром" in before[0]["problem"]

    report = _no_data_report("R-F")
    assert m.ensure_missing_data_questions(doc, report) == []
    assert report["withdrawn_judged_on_solution"] == [qid]   # вопрос снят
    assert not rule.get("blockers")

    cell = rule["template_fit"]["params"]["min_rest_home"]
    assert cell["pending_revision"] == qid                   # барьер стоит
    after = m.pending_revision_facts(doc)
    assert [f["cell"] for f in after] == [f["cell"] for f in before]
    assert [f["problem"] for f in after] == [f["problem"] for f in before]
    assert len(m.pending_revision_problems(doc)) == 1
    assert tp.pending_revision_problem(doc) is not None
    assert m.readiness(doc)["state"] != "в"
