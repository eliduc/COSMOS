# -*- coding: utf-8 -*-
"""A11 (аудит 11.09.2026, находка «индикатор „Биекция“ зелёный при
доступном ему неполном покрытии»; контракты П0 v1.4 §9): факты биекции
считает ОДНА функция cdpm.coverage.bijection_facts, и её вердикт — тот
же, что у замка выпуска пакета и гейта пакета. Охраняемое:
  * контрпример аудитора (частичное покрытие + величина без адресата +
    висячий якорь существующего источника) — P4.ok=false с названными
    фактами, и та же модель отказана сборкой пакета по тем же причинам;
  * чистая модель — P4.ok=true И выпуск проходит;
  * эквивалентность: на наборе синтетических моделей вердикт P4 равен
    «замок выпуска пропустил бы», и слова отказа — слова индикатора;
  * без комплекта заморозки якоря не судятся, без измерения не судится
    содержание — ok не заявляется;
  * гейт пакета краснит висячий якорь по клаузам заморозки самого
    пакета и считает слой содержания тем же расчётом;
  * ранние классы аудитора (PRECHECK 12.09.2026, A11-01…A11-08): строка
    без ссылки, якорь чужого источника, непокрытая клауза, все три
    состояния ответа на нормативном носителе (открыт / ожидает
    обработчика / применён) и чужой адресат, подделка одного счётчика,
    отчёт не о том комплекте и не о том своде, испорченный отчёт,
    независимость от порядка и повторного вычисления, одинаковый текст
    при разных якорях, положительный контроль на золотых комплектах;
  * находки неавторского ревью (12.09.2026): снятие ключей манифеста
    зелёного не покупает (судья строк и якорей — клаузы заморозки
    самого пакета), испорченный СВОЙ протокол даёт слова, а не падение
    читателя, панель судит свод в виде сборки (один закон — один
    вход), гейт пересчитывает измерителя по самому пакету и краснит
    подделку производных артефактов, строка таблицы, выпущенная и
    строкой, и клетками, из состава носителя не теряется, факты не
    делят объектов со своими входами.
Фикстуры — выдуманный мир test_cdpm_content_lock (не наш набор); ждём
от функции то, что выписано руками ДО вызова, а не то, что она вернёт."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from cdpm import coverage as cov, measure as ms, principles as pr  # noqa: E402
from cdpm import export_task_package as etp  # noqa: E402
from cdpm.gates import gate_task_package as gate  # noqa: E402
from test_cdpm_content_lock import (CLAUSE_1, CLAUSE_3,  # noqa: E402
                                    VALUE_LEFT, _clauses, _doc,
                                    _entries, _run)

GHOST = "п.НЕТ"


def _ghost_doc() -> dict:
    """Контрпример аудитора (B5) на фикстуре замка содержания: правило
    R-D берёт одну из двух величин клаузы п.2 («36 часов» остаётся без
    адресата — частичное покрытие) и вдобавок ссылается на якорь,
    которого у источника S1 нет."""
    doc = _doc()
    doc["rules"][-1]["sources"].append({"sid": "S1", "anchor": GHOST})
    return doc


def _p4(run: Path) -> dict:
    """P4 так, как его считает карточка прогона (webui.run_status):
    комплект (run_kit) + измерение (run_measure) + сводка (run_report),
    и всё это — по своду В ВИДЕ СБОРКИ (package_doc), тому же, что
    судит замок выпуска."""
    kit = cov.run_kit(run)
    doc = json.loads((run / "extract" / "model.json").read_text(encoding="utf-8"))
    doc_pkg, note = cov.package_doc(doc)
    measured = cov.run_measure(run, kit, doc_pkg)
    coverage = cov.run_report(run, kit_entries=kit, doc=doc_pkg,
                              measured=measured)
    return pr.summary(doc, coverage, kit_entries=kit,
                      measure_report=measured[0], facts_doc=doc_pkg,
                      facts_notes=[note] if note else None)["P4"]


def _release(run: Path) -> str | None:
    """None — выпуск прошёл; иначе слова отказа."""
    try:
        etp.build_package(run)
    except etp.TaskPackageError as e:
        return str(e)
    return None


def _value_verdict(run: Path) -> None:
    cov.save_verdicts(run / "exchange", {}, {
        ("S1", "п.2", cov.norm_value(VALUE_LEFT)):
            {"verdict": "справочно", "reason": "перерыв — справка", "round": 1}})


# ------------------------------------------------ контрпример аудитора

def test_контрпример_аудитора_p4_красный_с_названными_фактами(tmp_path):
    run = _run(tmp_path, _ghost_doc(), _entries())
    p4 = _p4(run)
    assert p4["ok"] is False
    assert p4["anchors_judged"] is True and p4["content_judged"] is True
    assert p4["covered_partially"] == 1
    assert p4["values_without_addressee"] == 1
    assert p4["normative_without_rows"] == 0
    assert p4["rows_without_source"] == 0
    assert p4["dangling_anchors"] == 1
    d = p4["dangling"][0]
    assert (d["coll"], d["id"], d["sid"], d["anchor"]) == ("rules", "R-D", "S1", GHOST)
    # Прежний закон (SID известен, клауз без вердикта нет) дал бы
    # зелёный — ровно находка A11: адреса покрыты, содержание и якорь нет.
    assert p4["coverage"] == {"clauses": 2, "covered_by_rows": 2,
                              "not_rule": 0, "missing": 0}
    words = " || ".join(p4["problems"])
    assert VALUE_LEFT in words and "п.2" in words
    assert "частично покрытых клауз 1" in words
    assert "висячих якорей 1" in words and GHOST in words


def test_та_же_модель_отказана_выпуском_по_тем_же_причинам(tmp_path):
    run = _run(tmp_path, _ghost_doc(), _entries())
    msg = _release(run)
    assert msg is not None
    assert "замок содержания" in msg and VALUE_LEFT in msg and "п.2" in msg
    assert "частично покрытых клауз 1" in msg
    assert "замок биекции" in msg and "висячих якорей 1" in msg and GHOST in msg
    # Слова отказа — слова индикатора: каждый факт P4 назван в отказе.
    for w in _p4(run)["problems"]:
        assert w in msg, w
    # Вердикт «справочно» закрывает величину, но не якорь: выпуск
    # по-прежнему отказывает — только якорем; индикатор — тем же фактом.
    _value_verdict(run)
    msg2 = _release(run)
    assert msg2 is not None and "висячих якорей 1" in msg2
    assert "замок содержания" not in msg2
    p4 = _p4(run)
    assert p4["ok"] is False and p4["dangling_anchors"] == 1
    assert p4["values_without_addressee"] == 0 and p4["covered_partially"] == 0
    assert p4["problems"] == [w for w in p4["problems"] if "висячих якорей" in w]


def test_чистая_модель_p4_зелёный_и_выпуск_проходит(tmp_path):
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    assert _release(run) is None
    p4 = _p4(run)
    assert p4["ok"] is True and p4["problems"] == []
    assert (p4["dangling_anchors"], p4["rows_without_source"],
            p4["covered_partially"], p4["values_without_addressee"],
            p4["normative_without_rows"]) == (0, 0, 0, 0, 0)
    assert p4["anchors_judged"] and p4["content_judged"]
    assert p4["coverage"] == {"clauses": 2, "covered_by_rows": 2,
                              "not_rule": 0, "missing": 0}


# ------------------------------------------------ эквивалентность

def _variant_clean():
    return _doc(), _entries(), True, {}


def _variant_orphan_value():
    return _doc(), _entries(), False, {}


def _variant_ghost_anchor():
    return _ghost_doc(), _entries(), True, {}


def _variant_unknown_sid():
    doc = _doc()
    doc["rules"][-1]["sources"].append({"sid": "S9", "anchor": "п.2"})
    return doc, _entries(), True, {}


def _variant_clause_without_verdict():
    # Третья клауза нормативна и без строк: и без вердикта покрытия, и
    # нормативный носитель без строк — два факта одной клаузы.
    return _doc(), _entries(with_third=True), True, {}


def _variant_third_not_rule():
    return _doc(), _entries(with_third=True), True, {
        ("S1", "п.3"): {"verdict": cov.NOT_RULE, "reason": "справка", "round": 1}}


def _variant_third_open_question():
    # Открытый вопрос анкеты на носителе — адресат носителя без строк и
    # покрытие адреса (source_refs вопроса считаются трассировкой).
    doc = _doc()
    doc["questions"].append(
        {"id": "Q-3-CREW", "part": "3a", "title": "Состав", "text": "т",
         "answer_format": {"type": "value"}, "rule_refs": [],
         "source_refs": [{"sid": "S1", "anchor": "п.3"}]})
    return doc, _entries(with_third=True), True, {}


VARIANTS = {
    "чистая": _variant_clean,
    "величина без адресата": _variant_orphan_value,
    "висячий якорь известного источника": _variant_ghost_anchor,
    "источник не объявлен": _variant_unknown_sid,
    "клауза без вердикта и нормативный носитель без строк": _variant_clause_without_verdict,
    "носитель закрыт вердиктом «не правило»": _variant_third_not_rule,
    "носитель закрыт открытым вопросом": _variant_third_open_question,
}
EXPECT_OK = {"чистая", "носитель закрыт вердиктом «не правило»",
             "носитель закрыт открытым вопросом"}
#: Слова замка покрытия/содержания в отказе выпуска (refusal_words);
#: отказ без них — другой рубеж сборки (готовность, каталог, SMT), не
#: предмет биекции.
LOCK_MARKERS = ("клауз без вердикта покрытия", "замок содержания",
                "замок биекции")


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_вердикт_p4_равен_вердикту_замка_выпуска(tmp_path, name):
    doc, entries, value_ok, clause_verdicts = VARIANTS[name]()
    run = _run(tmp_path, doc, entries)
    if value_ok:
        _value_verdict(run)
    if clause_verdicts:
        cov.save_verdicts(run / "exchange", clause_verdicts)
    p4 = _p4(run)
    msg = _release(run)
    lock_refused = msg is not None and any(k in msg for k in LOCK_MARKERS)
    # Индикатор зелёный ⇔ замок покрытия/содержания выпуска пропустил.
    assert p4["ok"] == (not lock_refused), (name, p4["problems"], msg)
    assert p4["ok"] == (name in EXPECT_OK), name
    if lock_refused:
        # Один закон слов: каждый факт индикатора назван в отказе.
        for w in p4["problems"]:
            assert w in msg, (name, w, msg)
    elif msg is not None:
        # Отказ не по замку биекции: открытый вопрос анкеты опускает
        # готовность ниже «в» (матрица корректности) — зона принципа 3,
        # индикатор биекции его не судит и не обязан.
        assert name == "носитель закрыт открытым вопросом" \
            and "матрицей корректности" in msg, (name, msg)


# ------------------------------------------------ незнание — не успех

def test_без_комплекта_якоря_не_судятся_и_ok_не_заявляется():
    doc, entries = _doc(), _entries()
    vv = {("S1", "п.2", cov.norm_value(VALUE_LEFT)):
          {"verdict": "справочно", "reason": "справка"}}
    rep = ms.measure(doc, _clauses(entries), verdicts=vv)
    assert ms.content_lock(rep) == []
    coverage = cov.report(entries, doc, {}, rep)
    # Всё чисто, но комплект не дан: якоря не судились — не зелёный.
    p4 = pr.summary(doc, coverage, measure_report=rep)["P4"]
    assert p4["ok"] is False and p4["anchors_judged"] is False
    assert p4["dangling_anchors"] == 0
    assert any("якоря не судились" in w for w in p4["problems"])
    # С комплектом — зелёный (те же входы).
    p4k = pr.summary(doc, coverage, kit_entries=entries, measure_report=rep)["P4"]
    assert p4k["ok"] is True
    # Сводка без слоя содержания и без отчёта измерителя — содержание
    # не судилось: не зелёный, даже когда адреса и якоря чисты.
    bare = cov.report(entries, doc, {})
    p4b = pr.summary(doc, bare, kit_entries=entries)["P4"]
    assert p4b["ok"] is False and p4b["content_judged"] is False
    assert any("содержание клауз не судилось" in w for w in p4b["problems"])
    # Без сводки покрытия вовсе — прежний честный None и не зелёный.
    p4n = pr.summary(doc)["P4"]
    assert p4n["coverage"] is None and p4n["ok"] is False


def test_факты_по_протоколу_равны_фактам_по_сводке():
    """Гейт судит по протоколу измерителя (measure_report), панель — по
    сводке со слоем содержания: одна функция, одни числа."""
    doc, entries = _ghost_doc(), _entries()
    rep = ms.measure(doc, _clauses(entries))
    coverage = cov.report(entries, doc, {}, rep)
    by_cov = cov.bijection_facts(doc, coverage, kit_entries=entries)
    by_proto = cov.bijection_facts(doc, coverage, measure_report=rep,
                                   kit_entries=entries)
    keys = ("ok", "rows_without_source", "dangling_anchors", "missing",
            "covered_partially", "values_without_addressee",
            "normative_without_rows", "anchors_judged", "content_judged")
    assert {k: by_cov[k] for k in keys} == {k: by_proto[k] for k in keys}
    assert by_cov["ok"] is False
    # Слой содержания сводки — тот же расчёт, что у единой функции.
    assert cov.content_layer(rep)["covered_partially"] == coverage["covered_partially"] == 1
    # Слова отказа пусты ровно при ok.
    assert cov.refusal_words(by_proto) and not cov.refusal_words(
        cov.bijection_facts(_doc(), cov.report(entries, _doc(), {}, ms.measure(
            _doc(), _clauses(entries), verdicts={
                ("S1", "п.2", cov.norm_value(VALUE_LEFT)):
                    {"verdict": "справочно", "reason": "справка"}})),
            kit_entries=entries))


def test_covered_partially_влечёт_величину_без_адресата():
    """Сегодняшний измеритель: частично покрытая клауза = клауза со
    строками и остатком без единого адресата, значит у неё есть величина
    без адресата — замок содержания выпуска её ловит. Единая функция
    всё равно называет частичное покрытие отдельным фактом (контракт)."""
    rep = ms.measure(_doc(), _clauses(_entries()))
    layer = cov.content_layer(rep)
    assert layer["covered_partially"] == 1
    assert layer["values_without_addressee"] >= 1
    assert ms.content_lock(rep)


#: Клауза с ТРЕМЯ величинами: одну берёт строка, одну закрывает вердикт
#: аудитора, третья остаётся без адресата (граница счётчика ниже).
CLAUSE_4 = ("Не более 8 посадок в смене; перерыв не менее 12 часов; "
            "запас не менее 3 тонн.")


def _doc_and_entries_partly_addressed() -> tuple[dict, list[dict]]:
    doc = _doc()
    doc["rules"].append(
        {"id": "R-E", "title": "Посадки", "zone": "ROSTERING", "kind": "HARD",
         "statement": "не более 8 посадок в смене",
         "sources": [{"sid": "S1", "anchor": "п.4"}],
         "params": [{"name": "max_landings", "value": 8, "unit": "count",
                     "relation": "max", "as_written": "8 посадок в смене"}]})
    entries = _entries()
    entries[0]["clauses"].append({"anchor": "п.4", "text": CLAUSE_4})
    return doc, entries


def test_частичный_адресат_остатка_счётчик_молчит_вердикт_нет(tmp_path):
    """Граница счётчика «частично покрытых» (поведение измерителя, не
    A11): частичным считается носитель, у остатка которого НЕТ НИ ОДНОГО
    адресата, — стоит закрыть вердиктом одну величину из двух, и счётчик
    молчит, хотя вторая величина осталась без адресата. Счётчик
    занижает, но вердикт не врёт: величина без адресата красит биекцию
    и отказывает выпуску теми же словами. Тест держит эту границу
    названной: если счётчик станет считать по величинам, число вырастет
    — вердикт (ok и отказ) обязан остаться тем же."""
    doc, entries = _doc_and_entries_partly_addressed()
    run = _run(tmp_path, doc, entries)
    _value_verdict(run)
    cov.save_verdicts(run / "exchange", {}, {
        ("S1", "п.2", cov.norm_value(VALUE_LEFT)):
            {"verdict": "справочно", "reason": "перерыв — справка", "round": 1},
        ("S1", "п.4", cov.norm_value("12 часов")):
            {"verdict": "справочно", "reason": "перерыв — справка", "round": 1}})
    p4 = _p4(run)
    assert p4["covered_partially"] == 0        # граница счётчика
    assert p4["values_without_addressee"] == 1  # но факт назван
    assert p4["ok"] is False
    msg = _release(run)
    assert msg is not None and "замок содержания" in msg and "S1|п.4" in msg
    for w in p4["problems"]:
        assert w in msg, w


# ------------------------------------------------ карточка прогона

def _p4_of_api(tmp_path: Path, doc: dict, entries: list[dict],
               monkeypatch) -> dict:
    """P4 из САМОГО обработчика карточки (/api/runs/<id>): проверяется
    не только закон, но и то, что панель даёт ему комплект заморозки и
    измеритель, — иначе индикатор снова судил бы по одним адресам."""
    from fastapi.testclient import TestClient
    from cdpm import webui
    run = _run(tmp_path, doc, entries)
    _value_verdict(run)
    (run / "status.json").write_text('{"state": "извлечено"}',
                                     encoding="utf-8")
    monkeypatch.setattr(webui, "_run_dir", lambda rid: run)
    body = TestClient(webui.app).get("/api/runs/run-0001").json()
    assert body.get("principles_error") is None
    return body["principles"]["P4"]


def test_карточка_прогона_даёт_индикатору_комплект_и_измерителя(
        tmp_path, monkeypatch):
    p4 = _p4_of_api(tmp_path, _ghost_doc(), _entries(), monkeypatch)
    assert p4["anchors_judged"] is True and p4["content_judged"] is True
    assert p4["ok"] is False and p4["dangling_anchors"] == 1
    assert GHOST in " || ".join(p4["problems"])
    assert p4["basis"]                       # основание строки словами


def test_карточка_прогона_зелёная_на_чистом_своде(tmp_path, monkeypatch):
    p4 = _p4_of_api(tmp_path, _doc(), _entries(), monkeypatch)
    assert p4["ok"] is True and p4["problems"] == []
    assert (p4["covered_partially"], p4["values_without_addressee"],
            p4["normative_without_rows"], p4["dangling_anchors"]) == (0, 0, 0, 0)


# ------------------------------------------------ гейт пакета

def _package_dir(tmp_path: Path, files: dict) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    for name, body in files.items():
        (pkg / name).parent.mkdir(parents=True, exist_ok=True)
        (pkg / name).write_bytes(body)
    return pkg


def _rewrite(pkg: Path, name: str, obj) -> None:
    body = (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    (pkg / name).write_bytes(body)
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        if f["name"] == name:
            f["sha256"] = hashlib.sha256(body).hexdigest()
            f["bytes"] = len(body)
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_гейт_краснит_висячий_якорь_по_клаузам_заморозки_пакета(tmp_path):
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    files = etp.build_package(run)
    assert "freeze/clauses/S1.json" in files
    pkg = _package_dir(tmp_path, files)
    assert gate.check(pkg)[0] == []
    # Подделка честная по суммам: правило пакета ссылается на якорь,
    # которого нет в клаузах заморозки, приехавших с пакетом.
    doc = json.loads((pkg / "model.json").read_text(encoding="utf-8"))
    doc["rules"][-1]["sources"].append({"sid": "S1", "anchor": GHOST})
    _rewrite(pkg, "model.json", doc)
    problems, _ = gate.check(pkg)
    assert any("замок биекции по пакету" in p and "висячих якорей 1" in p
               and GHOST in p for p in problems), problems
    # Клаузы заморозки из пакета убраны — покрытие заявлено, судить
    # нечем: красное слово, не тишина.
    pkg2 = _package_dir(tmp_path / "second", files)
    (pkg2 / "freeze" / "clauses" / "S1.json").unlink()
    m2 = json.loads((pkg2 / "manifest.json").read_text(encoding="utf-8"))
    m2["files"] = [f for f in m2["files"] if f["name"] != "freeze/clauses/S1.json"]
    (pkg2 / "manifest.json").write_text(
        json.dumps(m2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    problems2, _ = gate.check(pkg2)
    assert any("висячие якоря судить нечем" in p for p in problems2), problems2


def test_гейт_слой_содержания_тем_же_расчётом(tmp_path):
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    pkg = _package_dir(tmp_path, etp.build_package(run))
    proto = json.loads((pkg / "measure-protocol.json").read_text(encoding="utf-8"))
    # Протокол несёт остаток без адресата и частичное покрытие — гейт
    # называет оба факта словами единой функции.
    row = next(r for r in proto["clauses"] if r["clause"] == "S1|п.2")
    row["unconsumed_addressed"] = []
    proto["unconsumed_without_addressee"] = [
        {"clause": "S1|п.2", "value": VALUE_LEFT, "kind": "number_with_unit"}]
    proto["counts"]["unconsumed_without_addressee"] = 1
    proto["counts"]["unconsumed_by_verdict"] = 0
    _rewrite(pkg, "measure-protocol.json", proto)
    problems, _ = gate.check(pkg)
    assert any("замок содержания по протоколу" in p and VALUE_LEFT in p
               and "без адресата" in p for p in problems), problems
    assert any("замок содержания по протоколу" in p
               and "частично покрытых клауз 1" in p for p in problems), problems



# ------------------------------- ранние классы аудитора (12.09.2026)
# Ожидаемое по каждому классу выписано руками из синтетического
# источника ДО вызова проверяемой функции: oracle независим от неё.

CLAUSE_5 = "Приложение 1. Формы документов."      # не норма: ни модальности,
CLAUSE_6 = "Не более 6 рабочих дней подряд."      # ни величины; и норма-двойник


def _with_clause(entries: list[dict], anchor: str, text: str) -> list[dict]:
    out = [dict(e, clauses=list(e["clauses"])) for e in entries]
    out[0]["clauses"].append({"anchor": anchor, "text": text})
    return out


def test_строка_без_ссылки_на_источник_красит_и_отказывает(tmp_path):
    """A11-03 (нет ссылки у строки свода). Ожидание: rows_without_source
    = 1 (R-NOSRC), прочие факты нулевые; выпуск отказан словом замка
    биекции, и слова индикатора — слова отказа."""
    doc = _doc()
    doc["rules"].append(
        {"id": "R-NOSRC", "title": "Ниоткуда", "zone": "PAIRING",
         "kind": "HARD", "statement": "экипаж отдыхает", "sources": []})
    run = _run(tmp_path, doc, _entries())
    _value_verdict(run)
    p4 = _p4(run)
    assert p4["ok"] is False
    assert p4["rows_without_source"] == 1
    assert p4["dangling_anchors"] == 0 and p4["covered_partially"] == 0
    assert p4["values_without_addressee"] == 0
    words = " || ".join(p4["problems"])
    assert "строк без источника 1" in words and "R-NOSRC" in words
    msg = _release(run)
    assert msg is not None and "замок биекции: строк без источника 1" in msg
    for w in p4["problems"]:
        assert w in msg, w


def test_якорь_чужого_источника_висячий(tmp_path):
    """A11-03 (ссылка на якорь ДРУГОГО источника). Судится пара
    sid+anchor: якорь «п.7» существует у S2, но не у S1, и ссылка
    правила на S1/п.7 висячая при нулевом остатке."""
    doc = _doc()
    doc["sources"].append({"sid": "S2", "file": "b.md", "sha256": "1" * 64})
    doc["rules"][-1]["sources"].append({"sid": "S1", "anchor": "п.7"})
    entries = _entries() + [{"sid": "S2", "file": "b.md", "sha256": "1" * 64,
                             "clauses": [{"anchor": "п.7", "text": CLAUSE_3}]}]
    run = _run(tmp_path, doc, entries)
    _value_verdict(run)
    cov.save_verdicts(run / "exchange", {
        ("S2", "п.7"): {"verdict": cov.NOT_RULE, "reason": "справка",
                        "round": 1}})
    p4 = _p4(run)
    assert p4["ok"] is False
    assert p4["coverage"]["missing"] == 0      # адреса покрыты, якорь — нет
    assert p4["covered_partially"] == 0
    assert p4["dangling_anchors"] == 1
    d = p4["dangling"][0]
    assert (d["id"], d["sid"], d["anchor"]) == ("R-D", "S1", "п.7")
    assert "комплекте" in d["why"]
    msg = _release(run)
    assert msg is not None and "висячих якорей 1" in msg and "S1" in msg


def test_непокрытая_клауза_не_прячется_за_покрытыми(tmp_path):
    """A11-02: клауза без строк и без вердикта — «клауза без вердикта
    покрытия», и две покрытые клаузы рядом её не скрывают."""
    entries = _with_clause(_entries(), "п.5", CLAUSE_5)
    run = _run(tmp_path, _doc(), entries)
    _value_verdict(run)
    p4 = _p4(run)
    assert p4["coverage"] == {"clauses": 3, "covered_by_rows": 2,
                              "not_rule": 0, "missing": 1}
    assert p4["ok"] is False
    # Клауза не нормативна — замок содержания о ней молчит, а покрытие нет.
    assert p4["normative_without_rows"] == 0
    assert any("клауз без вердикта покрытия 1 из 3" in w
               for w in p4["problems"])
    msg = _release(run)
    assert msg is not None and "клауз без вердикта покрытия 1 из 3" in msg


def _asked_on(anchor: str, answer: dict | None = None) -> dict:
    q = {"id": "Q-3-CREW", "part": "3a", "title": "Состав", "text": "т",
         "answer_format": {"type": "value"}, "rule_refs": [],
         "source_refs": [{"sid": "S1", "anchor": anchor}]}
    if answer is not None:
        q["answer"] = answer
    return q


@pytest.mark.parametrize("answer,left", [
    (None, 0),                                            # вопрос открыт
    ({"raw": "два", "status": "не разобран", "revision": 2}, 0),
    ({"raw": "два", "status": "ожидает обработчика", "revision": 2}, 1),
    ({"raw": "два", "status": "применён", "key": "а", "revision": 2}, 1),
])
def test_вопрос_на_носителе_закрывает_его_только_пока_открыт(
        tmp_path, answer, left):
    """A11-07, все три состояния ответа: вопрос на нормативном носителе
    закрывает его, ПОКА открыт (нет ответа либо ответ не разобрался и
    задаётся снова); ответ, ОЖИДАЮЩИЙ ОБРАБОТЧИКА, и ПРИМЕНЁННЫЙ ответ
    носитель не закрывают — вопрос отработан, а строки свода по клаузе
    так и нет: снова факт биекции, и выпуск отказан теми же словами
    (готовность при отработанном ответе уже не мешает). Ожидаемое
    выписано руками: закрывает только открытый вопрос."""
    doc = _doc()
    doc["questions"].append(_asked_on("п.3", answer))
    run = _run(tmp_path, doc, _entries(with_third=True))
    _value_verdict(run)
    p4 = _p4(run)
    assert p4["normative_without_rows"] == left
    if not left:
        # Носитель закрыт законным адресатом — покрытие чисто; выпуск
        # здесь судит готовность (открытый вопрос), не биекция.
        assert p4["ok"] is True and p4["problems"] == []
        return
    assert p4["ok"] is False
    msg = _release(run)
    assert msg is not None
    assert "нормативных носителей без строк свода 1" in msg
    assert "S1|п.3" in msg
    for w in p4["problems"]:
        assert w in msg, w


def test_чужой_адресат_носитель_не_берёт(tmp_path):
    """A11-07 (чужой адресат): открытый вопрос закрывает ТОТ носитель,
    на котором стоит; вопрос на другой клаузе нормативный носитель без
    строк не берёт."""
    doc = _doc()
    doc["questions"].append(_asked_on("п.2"))
    run = _run(tmp_path, doc, _entries(with_third=True))
    p4 = _p4(run)
    assert p4["normative_without_rows"] == 1 and p4["ok"] is False


def test_подделан_счётчик_остатка_факт_берётся_из_перечня():
    """A11-05: подделан ТОЛЬКО счётчик (нуль), перечень остатка говорит
    об обратном. Одному счётчику веры нет: величина без адресата
    названа по перечню, расхождение — отдельным словом, ok не заявлен."""
    doc, entries = _doc(), _entries()
    rep = ms.measure(doc, _clauses(entries))
    assert rep["counts"]["unconsumed_without_addressee"] == 1
    forged = json.loads(json.dumps(rep, ensure_ascii=False))
    forged["counts"]["unconsumed_without_addressee"] = 0
    for row in forged["clauses"]:
        if row["clause"] == "S1|п.2":
            row["unconsumed_addressed"] = ["вердикт аудитора: справочно"]
    facts = cov.bijection_facts(doc, cov.report(entries, doc, {}),
                                measure_report=forged, kit_entries=entries)
    assert facts["values_without_addressee"] == 1     # из перечня, не из счётчика
    assert facts["covered_partially"] == 0            # подделка скрыла частичность
    assert facts["ok"] is False
    assert any("одному счётчику веры нет" in w
               for w in facts["problems_by_kind"]["form"])
    # Дубликат записи вместо пропавшей: число сходится, предмет — нет.
    doubled = json.loads(json.dumps(rep, ensure_ascii=False))
    doubled["clauses"] = [r for r in doubled["clauses"] if r["clause"] != "S1|п.1"]
    doubled["clauses"].append(dict(doubled["clauses"][0]))
    f2 = cov.bijection_facts(doc, None, measure_report=doubled,
                             kit_entries=entries)
    assert f2["measure_of_kit"] is False and f2["content_judged"] is False
    assert any("не о клаузах этого комплекта" in w
               for w in f2["problems_by_kind"]["form"])


def test_чужой_отчёт_измерителя_содержания_не_судит():
    """A11-08/A11-04: свежий отчёт ДРУГОГО комплекта (и отчёт, снятый до
    того, как в комплект добавили клаузу) свидетельством этого прогона
    не является — содержание им не судится, ok не заявляется."""
    doc, entries = _doc(), _entries()
    alien = [{"sid": "S9", "file": "z.md", "sha256": "9" * 64,
              "clauses": [{"anchor": "п.1", "text": CLAUSE_1}]}]
    rep_alien = ms.measure(doc, _clauses(alien))
    facts = cov.bijection_facts(doc, None, measure_report=rep_alien,
                                kit_entries=entries)
    assert facts["measure_of_kit"] is False and facts["content_judged"] is False
    words = " || ".join(facts["problems_by_kind"]["form"])
    assert "не о клаузах этого комплекта" in words
    assert "S9|п.1" in words and "S1|п.1" in words
    assert facts["ok"] is False
    # Клаузу добавили в комплект ПОСЛЕ измерения: отчёт устарел.
    rep_old = ms.measure(doc, _clauses(entries))
    grown = _with_clause(entries, "п.5", CLAUSE_5)
    stale = cov.bijection_facts(doc, None, measure_report=rep_old,
                                kit_entries=grown)
    assert stale["measure_of_kit"] is False
    assert any("клауз комплекта не судилось 1: S1|п.5" in w
               for w in stale["problems_by_kind"]["form"])
    # Тот же отчёт со СВОИМ комплектом — свидетельство годное.
    same = cov.bijection_facts(doc, None, measure_report=rep_old,
                               kit_entries=entries)
    assert same["measure_of_kit"] is True and same["content_judged"] is True
    assert same["measure_of_doc"] is True


def test_чужой_свод_отчёт_измерителя_содержания_не_судит():
    """A11-04/A11-08 по ВТОРОМУ входу измерителя: отчёт обязан быть не
    только о клаузах этого комплекта, но и о строках ЭТОГО свода. Свод
    подменён (строка переименована) либо дополнен после измерения —
    отчёт устарел, содержание им не судится, ok не заявляется. Комплект
    при этом тот же: проверяются именно строки."""
    doc, entries = _doc(), _entries()
    rep = ms.measure(doc, _clauses(entries))
    # Та же строка под другим идентификатором: чисел не меняет, предмет
    # меняет — отчёт судил R-D, свод предъявляет R-D-ПОДМЕНА.
    forged = json.loads(json.dumps(doc, ensure_ascii=False))
    forged["rules"][-1]["id"] = "R-D-ПОДМЕНА"
    facts = cov.bijection_facts(forged, None, measure_report=rep,
                                kit_entries=entries)
    assert facts["measure_of_kit"] is True      # комплект тот же
    assert facts["measure_of_doc"] is False
    assert facts["content_judged"] is False and facts["ok"] is False
    words = " || ".join(facts["problems_by_kind"]["form"])
    assert "не о строках этого свода" in words
    assert "чужих строк 1: R-D" in words
    assert "строк свода не судилось 1: R-D-ПОДМЕНА" in words
    assert any("отчёт измерителя этому своду не принадлежит" in w
               for w in facts["problems_by_kind"]["unjudged"])
    # Строку добавили ПОСЛЕ измерения: отчёт о ней молчит — тот же класс.
    grown = json.loads(json.dumps(doc, ensure_ascii=False))
    grown["rules"].append(dict(doc["rules"][-1], id="R-НОВОЕ"))
    stale = cov.bijection_facts(grown, None, measure_report=rep,
                                kit_entries=entries)
    assert stale["measure_of_doc"] is False and stale["content_judged"] is False
    assert any("строк свода не судилось 1: R-НОВОЕ" in w
               for w in stale["problems_by_kind"]["form"])
    # Свой свод — свидетельство годное (положительный контроль).
    mine = cov.bijection_facts(doc, None, measure_report=rep,
                               kit_entries=entries)
    assert mine["measure_of_doc"] is True and mine["content_judged"] is True


def test_гейт_краснит_подменённый_свод_пакета(tmp_path):
    """A11-04 на пути пакета: у гейта нет текущих входов — он читает
    запечатанный пакет, где и свод, и манифест пересчитать можно. Если
    model.json подменён после измерения, протокол говорит о другом
    своде: содержание им не судится, и это красное слово, а не тишина."""
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    files = etp.build_package(run)
    pkg = _package_dir(tmp_path, files)
    assert gate.check(pkg)[0] == []
    doc = json.loads((pkg / "model.json").read_text(encoding="utf-8"))
    doc["rules"][-1]["id"] = "R-D-ПОДМЕНА"
    _rewrite(pkg, "model.json", doc)
    problems, _ = gate.check(pkg)
    assert any("замок биекции по пакету" in p and "не о строках этого свода" in p
               for p in problems), problems


@pytest.mark.parametrize("broken", [
    {"clauses": "нет", "counts": {"unconsumed_without_addressee": 1}},
    {"clauses": [{"clause": "S1|п.2", "rows": ["R-D"], "unconsumed": "нет"}],
     "counts": {"unconsumed_without_addressee": "два"}},
    {"clauses": [["S1|п.2"]], "counts": "нет"},
    {"counts": {"unconsumed_without_addressee": -1}},
    {"clauses": [], "counts": {}, "unconsumed_without_addressee": {"a": 1}},
])
def test_испорченный_отчёт_не_роняет_и_зелёного_не_даёт(broken):
    """A11-04 / общий класс X-02: повреждённая форма свидетельства не
    роняет читателя, не считается нулём и не даёт зелёного — она
    названа словом вида «form»."""
    doc, entries = _doc(), _entries()
    facts = cov.bijection_facts(doc, cov.report(entries, doc, {}),
                                measure_report=broken, kit_entries=entries)
    assert facts["ok"] is False
    assert facts["problems_by_kind"]["form"], broken
    assert cov.refusal_words(facts)


def test_порядок_источников_и_строк_фактов_не_меняет(tmp_path):
    """A11-08: перестановка источников, строк свода и клауз комплекта
    вердикта и фактов не меняет (сравниваются предметные тождества, не
    длины списков)."""
    doc, entries = _ghost_doc(), _entries(with_third=True)
    a = cov.bijection_facts(doc, cov.report(entries, doc, {}),
                            measure_report=ms.measure(doc, _clauses(entries)),
                            kit_entries=entries)
    # Повторное вычисление на тех же входах — тот же ответ целиком
    # (расчёт чистый: входов не правит, состояния не копит).
    assert cov.bijection_facts(
        doc, cov.report(entries, doc, {}),
        measure_report=ms.measure(doc, _clauses(entries)),
        kit_entries=entries) == a
    doc_r = json.loads(json.dumps(doc, ensure_ascii=False))
    doc_r["rules"].reverse()
    doc_r["sources"].reverse()
    doc_r["questions"].reverse()
    ent_r = [dict(e, clauses=list(reversed(e["clauses"]))) for e in entries]
    b = cov.bijection_facts(doc_r, cov.report(ent_r, doc_r, {}),
                            measure_report=ms.measure(doc_r, _clauses(ent_r)),
                            kit_entries=ent_r)
    for key in ("ok", "rows_without_source", "missing", "covered_partially",
                "values_without_addressee", "normative_without_rows",
                "anchors_judged", "content_judged", "measure_of_kit"):
        assert a[key] == b[key], key
    def ident(f):
        return sorted((d["coll"], d["id"], d["sid"], d["anchor"])
                      for d in f["dangling_anchors"])

    assert ident(a) == ident(b) == [("rules", "R-D", "S1", GHOST)]
    assert sorted(a["normative_without_rows_keys"]) == \
        sorted(b["normative_without_rows_keys"]) == ["S1|п.3"]


def test_одинаковый_текст_разные_якоря_судятся_порознь(tmp_path):
    """A11-08: две клаузы с одинаковым по смыслу текстом на разных
    якорях — разные носители: покрытие одной не покрывает другую
    (тождество по паре sid+anchor, не по тексту)."""
    doc = _doc()
    entries = _with_clause(_entries(), "п.6", CLAUSE_6)
    facts = cov.bijection_facts(
        doc, cov.report(entries, doc, {}),
        measure_report=ms.measure(doc, _clauses(entries)),
        kit_entries=entries)
    # Покрыта п.2, её текстовый двойник п.6 — нет: ни покрытием, ни
    # нормативным носителем правило на п.2 за неё не отвечает.
    assert facts["normative_without_rows_keys"] == ["S1|п.6"]
    assert facts["missing"] == 1 and facts["clauses"] == 3
    assert facts["ok"] is False
    run = _run(tmp_path, doc, entries)
    _value_verdict(run)
    p4 = _p4(run)
    assert p4["coverage"] == {"clauses": 3, "covered_by_rows": 2,
                              "not_rule": 0, "missing": 1}
    assert p4["normative_without_rows"] == 1 and p4["ok"] is False


def test_гейт_краснит_подделку_счётчика_и_чужой_протокол(tmp_path):
    """A11-05/A11-08 на пути пакета: подделан счётчик остатка (перечень
    говорит об остатке) и протокол не о клаузах заморозки самого
    пакета — красные слова, а не молчание; испорченная форма протокола
    гейт не роняет."""
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    files = etp.build_package(run)
    pkg = _package_dir(tmp_path, files)
    assert gate.check(pkg)[0] == []
    proto = json.loads((pkg / "measure-protocol.json").read_text(encoding="utf-8"))
    forged = json.loads(json.dumps(proto, ensure_ascii=False))
    forged["unconsumed_without_addressee"] = [
        {"clause": "S1|п.2", "value": VALUE_LEFT, "kind": "number_with_unit"}]
    _rewrite(pkg, "measure-protocol.json", forged)
    problems, _ = gate.check(pkg)
    assert any("замок биекции по пакету" in p and "одному счётчику веры нет" in p
               for p in problems), problems
    # Чужой протокол: клаузы не те, что в freeze/clauses пакета.
    pkg2 = _package_dir(tmp_path / "чужой", files)
    alien = json.loads(json.dumps(proto, ensure_ascii=False))
    for row in alien["clauses"]:
        row["clause"] = row["clause"].replace("S1|", "S9|")
        row["members"] = [m.replace("S1|", "S9|") for m in row["members"]]
    _rewrite(pkg2, "measure-protocol.json", alien)
    problems2, _ = gate.check(pkg2)
    assert any("не о клаузах этого комплекта" in p for p in problems2), problems2
    # Испорченная форма: гейт краснит словами и не падает.
    pkg3 = _package_dir(tmp_path / "порча", files)
    bad = json.loads(json.dumps(proto, ensure_ascii=False))
    bad["clauses"] = "нет"
    _rewrite(pkg3, "measure-protocol.json", bad)
    problems3, _ = gate.check(pkg3)
    assert any("перечень клауз отчёта измерителя не список" in p
               for p in problems3), problems3


@pytest.mark.parametrize("kit_name,etalon_name", [
    ("polyaris-h0", "VMP-H0.json"), ("polyaris-h0k", "VMP-H0K.json")])
def test_золотые_комплекты_отчёт_о_своём_комплекте(kit_name, etalon_name):
    """A11-06, положительный контроль на НЕВЫДУМАННЫХ комплектах: на
    штатной заморозке H0 и H0-К отчёт измерителя признаётся отчётом
    этого комплекта (клетки таблиц, собранные в носителя-строку, клауз
    не теряют), содержание судится, а факты биекции нулевые всюду, где
    сам комплект чист. Иначе проверка принадлежности краснила бы
    законные прогоны."""
    import tempfile
    try:
        from cdpm import freeze as fz, structure as st
    except Exception as e:                                # noqa: BLE001
        pytest.skip(f"заморозка недоступна: {e}")
    kit = HERE.parent / "open-bench" / kit_name
    etalon = json.loads((kit / "etalon" / etalon_name).read_text(encoding="utf-8"))
    manifest = json.loads((kit / "kit-manifest.json").read_text(encoding="utf-8"))
    lines = json.loads((kit / "etalon" / "jpg-lines.json").read_text(encoding="utf-8"))

    def vision(_ask, _path):
        return json.dumps({"paragraphs": lines}, ensure_ascii=False)

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        fman = fz.freeze([kit / p for p in manifest["rules_files"]],
                         out / "freeze", vision_llm=vision)
        entries = [dict(json.loads(
            (out / "freeze" / "clauses" / f"{f['sid']}.json")
            .read_text(encoding="utf-8")), sid=f["sid"])
            for f in fman["files"]]
    verdicts = st.code_verdicts(entries)
    rep = ms.measure(etalon, _clauses(entries), verdicts=verdicts)
    facts = cov.bijection_facts(etalon, None, measure_report=rep,
                                kit_entries=entries)
    assert facts["measure_of_kit"] is True, facts["problems_by_kind"]["form"]
    assert facts["content_judged"] is True
    assert facts["problems_by_kind"]["form"] == []
    assert (facts["rows_without_source"], facts["dangling_anchors"],
            facts["values_without_addressee"], facts["covered_partially"],
            facts["normative_without_rows"]) == (0, [], 0, 0, 0)


# ------------------------- по неавторскому ревью A11 (12.09.2026)
# Каждая находка ревью — законом, а не заплаткой: ждём выписанного
# руками ДО вызова, и у каждой есть «минус» (подделка краснит) и «плюс»
# (честный вход зелёный).

def _strip_manifest(pkg: Path, keys: tuple[str, ...],
                    drop: tuple[str, ...] = ()) -> None:
    """Снять ключи манифеста (и файлы) — так выглядит пакет, собранный
    в обход замка биекции либо обобранный после сборки."""
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    for k in keys:
        manifest.pop(k, None)
    for name in drop:
        (pkg / name).unlink()
        manifest["files"] = [f for f in manifest["files"]
                             if f["name"] != name]
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def test_снятие_ключей_манифеста_зелёного_не_покупает(tmp_path):
    """Ревью п. 1 (ложный зелёный рубежа): строки без источника и
    висячие якоря судились ТОЛЬКО при manifest.coverage — и пакет с
    клаузами заморозки, но без ключей coverage/measure и без протокола
    проходил зелёным с висячим якорем. Судья строк и якорей — КЛАУЗЫ
    ЗАМОРОЗКИ САМОГО ПАКЕТА; заявления манифеста при них обязаны быть
    полными: честная сборка пишет комплект, покрытие и измерение
    вместе."""
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    files = etp.build_package(run)

    def _with_ghost(where: str) -> Path:
        """Честный пакет, в своде которого после сборки появился якорь,
        которого нет среди клауз заморозки, приехавших с пакетом."""
        pkg_ = _package_dir(tmp_path / where, files)
        doc_ = json.loads((pkg_ / "model.json").read_text(encoding="utf-8"))
        doc_["rules"][-1]["sources"].append({"sid": "S1", "anchor": GHOST})
        _rewrite(pkg_, "model.json", doc_)
        return pkg_

    # (−) сняты coverage + measure + протокол: якорь-призрак назван.
    pkg = _with_ghost("a")
    _strip_manifest(pkg, ("coverage", "measure"), ("measure-protocol.json",))
    problems, _ = gate.check(pkg)
    assert any("висячих якорей 1" in p and GHOST in p
               for p in problems), problems
    assert any("клаузы заморозки в пакете есть" in p
               and "manifest.coverage" in p and "manifest.measure" in p
               for p in problems), problems
    # (−) снят только coverage: измерение заявлено, покрытие — нет.
    pkg2 = _with_ghost("b")
    _strip_manifest(pkg2, ("coverage",))
    problems2, _ = gate.check(pkg2)
    assert any("висячих якорей 1" in p for p in problems2), problems2
    assert any("measure.available заявлено, а coverage манифеста нет" in p
               for p in problems2), problems2
    # (−) сводка манифеста о клаузах не о ЭТОМ пакете (записи вынуты).
    pkg3 = _package_dir(tmp_path / "c", files)
    _strip_manifest(pkg3, (), ("freeze/clauses/S1.json",))
    problems3, _ = gate.check(pkg3)
    assert any("manifest.freeze_clauses объявляет записи" in p
               for p in problems3), problems3
    # (+) тот же честный пакет без правки свода — зелёный.
    assert gate.check(_package_dir(tmp_path / "d", files))[0] == []


@pytest.mark.parametrize("name,mutate", [
    ("нормативные без строк — число вместо перечня",
     lambda r: r.update(normative_without_rows=5)),
    ("остаток без адресата — строка вместо перечня",
     lambda r: r.update(unconsumed_without_addressee="нет")),
    ("остаток без адресата — перечень не объектов",
     lambda r: r.update(unconsumed_without_addressee=[1])),
    ("нормативные без строк — объект вместо перечня",
     lambda r: r.update(normative_without_rows={"a": 1})),
])
def test_испорченный_свой_протокол_даёт_слова_а_не_падение(name, mutate,
                                                           tmp_path):
    """Ревью п. 2 (падение вместо слов): слова замка содержания брались
    из СЫРОГО отчёта измерителя, и протокол, ПРИНАДЛЕЖАЩИЙ комплекту и
    своду, но испорченный в перечнях, ронял и единый расчёт, и гейт
    пакета (TypeError/AttributeError вместо красного слова). Слова
    строятся из ПРОВЕРЕННЫХ перечней: читатель обязан назвать порок, а
    не упасть на нём."""
    doc, entries = _doc(), _entries()
    rep = ms.measure(doc, _clauses(entries), verdicts={
        ("S1", "п.2", cov.norm_value(VALUE_LEFT)): {"verdict": "справочно",
                                                    "reason": "справка"}})
    broken = json.loads(json.dumps(rep, ensure_ascii=False))
    mutate(broken)
    facts = cov.bijection_facts(doc, None, measure_report=broken,
                                kit_entries=entries)
    # Протокол СВОЙ: порок именно в форме, а не в принадлежности.
    assert facts["measure_of_kit"] is True and facts["measure_of_doc"] is True
    assert facts["ok"] is False
    assert facts["problems_by_kind"]["form"], name
    assert cov.refusal_words(facts)
    # Тот же порок на пути гейта пакета — красные слова, не падение.
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    pkg = _package_dir(tmp_path, etp.build_package(run))
    proto = json.loads((pkg / "measure-protocol.json").read_text(encoding="utf-8"))
    mutate(proto)
    _rewrite(pkg, "measure-protocol.json", proto)
    problems, _ = gate.check(pkg)
    assert any("замок биекции по пакету" in p for p in problems), problems


@pytest.mark.parametrize("broken", [[], "x", 0, 3.5])
def test_отчёт_измерителя_не_объект_называется_словом(broken):
    """Ревью п. 2, крайний случай: отчёт измерителя вовсе не объект.
    Слов с именами тогда нет и быть не может — читатель называет порок
    формы, содержание объявляет НЕсудившимся и зелёного не даёт."""
    doc, entries = _doc(), _entries()
    facts = cov.bijection_facts(doc, cov.report(entries, doc, {}),
                                measure_report=broken, kit_entries=entries)
    assert facts["ok"] is False and facts["content_judged"] is False
    assert any("отчёт измерителя не объект" in w
               for w in facts["problems_by_kind"]["form"])
    assert any("отчёт измерителя испорчен" in w
               for w in facts["problems_by_kind"]["unjudged"])


def test_панель_судит_свод_в_виде_сборки(tmp_path):
    """Ревью п. 3 (панель строже сборки): замок выпуска судит свод ПОСЛЕ
    машинного лечения сборкой, а индикатор судил канонический — и на
    целевой-осколке ответа без машинной метрики и без трассировки
    расходились: выпуск и гейт зелёные, панель красная «строк без
    источника 1». Вход у закона один: панель судит ту же копию
    (coverage.package_doc). Канонический свод прогона не трогается."""
    doc = _doc()
    doc["objectives"].append(
        {"id": "O-ANS", "title": "Из ответа", "statement": "минимум ночёвок",
         "zone": "PAIRING", "direction": "minimize", "sources": [],
         "origin": {"question": "Q-3a-GOAL", "revision": 2}})
    canon = json.loads(json.dumps(doc, ensure_ascii=False))
    run = _run(tmp_path, doc, _entries())
    _value_verdict(run)
    # Лечение снимает целевую-осколок: выпуск проходит — и панель тоже.
    assert _p4(run)["ok"] is True
    assert _release(run) is None
    # Свод прогона панель не переписала (ни в памяти, ни на диске).
    assert doc == canon
    assert json.loads((run / "extract" / "model.json")
                      .read_text(encoding="utf-8")) == canon
    # (−) целевая, которую лечение НЕ снимает (машинная метрика есть):
    # красны оба — панель теми же словами, что отказ выпуска.
    doc2 = _doc()
    doc2["objectives"].append(
        {"id": "O-ANS2", "title": "Из ответа", "statement": "минимум ночёвок",
         "metric": "layover_nights_total", "zone": "PAIRING",
         "direction": "minimize", "sources": []})
    run2 = _run(tmp_path / "second", doc2, _entries())
    _value_verdict(run2)
    p4 = _p4(run2)
    assert p4["ok"] is False and p4["rows_without_source"] == 1
    words = _release(run2)
    assert words is not None and "O-ANS2" in words
    assert any("O-ANS2" in p for p in p4["problems"])


def test_лечение_сборки_детерминировано_и_идемпотентно():
    """Ревью п. 3: один вход у закона законен лишь потому, что цепочка
    лечения ДЕТЕРМИНИРОВАНА и ИДЕМПОТЕНТНА — повторный прогон не меняет
    свода, и копия панели равна своду сборки."""
    doc = _doc()
    doc["objectives"].append(
        {"id": "O-ANS", "title": "Из ответа", "statement": "минимум ночёвок",
         "zone": "PAIRING", "direction": "minimize", "sources": [],
         "origin": {"question": "Q-3a-GOAL", "revision": 2}})
    once, note = cov.package_doc(doc)
    assert note is None
    twice, _ = cov.package_doc(once)
    assert once == twice
    assert etp.heal_for_package(
        json.loads(json.dumps(once, ensure_ascii=False))) == []
    # Копия панели — та же, что свод сборки: лечение зовёт одна функция.
    same = json.loads(json.dumps(doc, ensure_ascii=False))
    etp.heal_for_package(same)
    assert same == once


def test_сбой_лечения_называется_словом_и_краснит(monkeypatch):
    """Ревью п. 3: цепочка лечения зовёт каталог шаблонов (ярлыки
    семей). Каталог недоступен — панель не роняется и зелёного не даёт:
    факты считаны по каноническому своду (он строже), а расхождение
    названо словом."""
    def boom(_doc):
        raise RuntimeError("каталог шаблонов недоступен")
    monkeypatch.setattr(etp, "heal_for_package", boom)
    doc = _doc()
    got, note = cov.package_doc(doc)
    assert got == doc and note and "каталог шаблонов недоступен" in note
    p4 = pr.summary(doc, None, facts_doc=got, facts_notes=[note])["P4"]
    assert p4["ok"] is False and note in p4["problems"]


def test_гейт_пересчитывает_измерителя_по_самому_пакету(tmp_path):
    """Ревью п. 4 (предел гейта): содержание бралось из протокола после
    одних тождеств, и свод пакета со снятыми params при прежнем
    протоколе проходил зелёным, хотя пересчёт по самому пакету даёт две
    величины без адресата. Протокол — производный артефакт: гейт
    пересчитывает измерителя по model.json и freeze/clauses ПАКЕТА и
    сверяет вердикт-НЕЗАВИСИМЫЕ части."""
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    files = etp.build_package(run)
    # (+) честный пакет: пересчёт сходится с протоколом.
    assert gate.check(_package_dir(tmp_path / "ok", files))[0] == []
    # (−) у правила сняты params: величина клаузы больше не потреблена.
    pkg = _package_dir(tmp_path / "a", files)
    doc = json.loads((pkg / "model.json").read_text(encoding="utf-8"))
    next(r for r in doc["rules"] if r["id"] == "R-D").pop("params")
    _rewrite(pkg, "model.json", doc)
    problems, _ = gate.check(pkg)
    assert any("пересчёту по самому пакету" in p for p in problems), problems
    # (−) подделаны ОБА производных: клауза заморозки под призрак и
    # строка протокола под неё же (тождества сходятся, содержание — нет).
    pkg2 = _package_dir(tmp_path / "b", files)
    doc2 = json.loads((pkg2 / "model.json").read_text(encoding="utf-8"))
    doc2["rules"][-1]["sources"].append({"sid": "S1", "anchor": GHOST})
    _rewrite(pkg2, "model.json", doc2)
    kit = json.loads((pkg2 / "freeze/clauses/S1.json").read_text(encoding="utf-8"))
    kit["clauses"].append({"anchor": GHOST, "text": CLAUSE_1})
    _rewrite(pkg2, "freeze/clauses/S1.json", kit)
    proto = json.loads((pkg2 / "measure-protocol.json").read_text(encoding="utf-8"))
    proto["clauses"].append(
        {"clause": f"S1|{GHOST}", "sid": "S1", "members": [f"S1|{GHOST}"],
         "normative": True, "missing_signs": [], "cells": 1, "rows": ["R-D"],
         "values": 0, "consumed": 0, "unconsumed": [],
         "unconsumed_addressed": [], "not_rule_verdict": False,
         "questions": [], "questions_open": [], "unclassified": 0, "text": "x"})
    proto["counts"]["clauses"] += 1
    proto["counts"]["carriers"] += 1
    proto["counts"]["clauses_with_rows"] += 1
    _rewrite(pkg2, "measure-protocol.json", proto)
    manifest = json.loads((pkg2 / "manifest.json").read_text(encoding="utf-8"))
    manifest["measure"]["counts"] = proto["counts"]
    (pkg2 / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    problems2, _ = gate.check(pkg2)
    assert any("пересчёту по самому пакету" in p for p in problems2), problems2


def test_гейт_краснит_выдуманный_вопрос_адресатом_остатка(tmp_path):
    """Ревью п. 4, разделение достижимого и остатка: адресат остатка
    ВОПРОСОМ проверяем (вопросы едут в model.json) — выдуманный вопрос
    краснится; адресат ВЕРДИКТОМ аудитора пересчёту недоступен
    (coverage.json в пакет не едет) и остаётся названным остатком."""
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    pkg = _package_dir(tmp_path, etp.build_package(run))
    proto = json.loads((pkg / "measure-protocol.json").read_text(encoding="utf-8"))
    row = next(r for r in proto["clauses"] if r["clause"] == "S1|п.2")
    row["unconsumed_addressed"] = ["Q-ВЫДУМАН"]
    _rewrite(pkg, "measure-protocol.json", proto)
    problems, _ = gate.check(pkg)
    assert any("которых пересчёт не подтверждает" in p and "Q-ВЫДУМАН" in p
               for p in problems), problems


def test_строка_таблицы_данная_и_строкой_и_клетками_из_состава_не_теряется():
    """Ревью п. 5: состав носителя переписывался клетками, и клауза
    «табл.N, строка M», выпущенная читателем И целой строкой, И
    клетками, из состава пропадала — отчёт объявлялся «не о клаузах
    этого комплекта» на законном комплекте. Состав ДОБИРАЕТСЯ: клауза
    комплекта не теряется, каким бы способом читатель её ни выпустил."""
    entries = [{"sid": "S1", "file": "a.md", "sha256": "0" * 64, "clauses": [
        {"anchor": "табл.1, строка 1, кол 1", "text": "Правило"},
        {"anchor": "табл.1, строка 1, кол 2", "text": "Значение"},
        {"anchor": "табл.1, строка 2, кол 1",
         "text": "Не более рабочих дней подряд"},
        {"anchor": "табл.1, строка 2, кол 2", "text": "6"},
        {"anchor": "табл.1, строка 2",
         "text": "Не более рабочих дней подряд | 6"},
        {"anchor": "п.1", "text": CLAUSE_1}]}]
    doc = _doc()
    doc["rules"][-1]["sources"] = [{"sid": "S1", "anchor": "табл.1, строка 2"}]
    rep = ms.measure(doc, _clauses(entries))
    row = next(r for r in rep["clauses"] if r["clause"] == "S1|табл.1, строка 2")
    assert set(row["members"]) == {"S1|табл.1, строка 2, кол 1",
                                   "S1|табл.1, строка 2, кол 2",
                                   "S1|табл.1, строка 2"}
    facts = cov.bijection_facts(doc, None, measure_report=rep,
                                kit_entries=entries)
    assert facts["measure_of_kit"] is True
    assert facts["problems_by_kind"]["form"] == []
    # Тот же комплект БЕЗ целой строки (обычный docx) — как прежде.
    only_cells = [{**entries[0],
                   "clauses": [c for c in entries[0]["clauses"]
                               if c["anchor"] != "табл.1, строка 2"]}]
    doc2 = _doc()
    doc2["rules"][-1]["sources"] = [{"sid": "S1",
                                     "anchor": "табл.1, строка 2, кол 2"}]
    rep2 = ms.measure(doc2, _clauses(only_cells))
    f2 = cov.bijection_facts(doc2, None, measure_report=rep2,
                             kit_entries=only_cells)
    assert f2["measure_of_kit"] is True and f2["problems_by_kind"]["form"] == []


def test_факты_не_делят_объектов_со_своими_входами():
    """Ревью п. 7: перечень частично покрытых клауз ехал в факты теми же
    объектами, что лежат в сводке и в отчёте измерителя, — правка
    результата была видна во входе. Результат — своя копия: вход
    неприкосновенен, повтор равен первому вызову."""
    doc, entries = _doc(), _entries()
    rep = ms.measure(doc, _clauses(entries))
    report = cov.report(entries, doc, {}, rep)
    snapshot = json.dumps([doc, entries, rep, report], ensure_ascii=False)
    facts = cov.bijection_facts(doc, report, measure_report=rep,
                                kit_entries=entries)
    assert facts["partial"], "проба требует непустого перечня"
    facts["partial"][0]["rows"] = ["ПОРЧА"]
    facts["partial"][0]["values_left"].append("ПОРЧА")
    assert json.dumps([doc, entries, rep, report],
                      ensure_ascii=False) == snapshot
    again = cov.bijection_facts(doc, report, measure_report=rep,
                                kit_entries=entries)
    assert again["partial"] and again["partial"][0]["rows"] != ["ПОРЧА"]


@pytest.mark.parametrize("kit_name,etalon_name", [
    ("polyaris-h0", "VMP-H0.json"), ("polyaris-h0k", "VMP-H0K.json")])
def test_вердикты_не_меняют_сверяемых_гейтом_частей(kit_name, etalon_name):
    """Ревью п. 4, ОСНОВАНИЕ пересчёта: гейт пересчитывает измерителя
    БЕЗ вердиктов аудитора (их в пакете нет) и сверяет с протоколом,
    снятым С вердиктами. Это законно ровно потому, что сверяемые части
    от вердиктов не зависят: положительный контроль на невыдуманных
    комплектах H0 и H0-К — ни одна из них не меняется, а
    вердикт-ЗАВИСИМЫЕ (нормативные носители без строк) меняются, иначе
    проба ничего не проверяла бы."""
    import tempfile
    try:
        from cdpm import freeze as fz, structure as st
    except Exception as e:                                # noqa: BLE001
        pytest.skip(f"заморозка недоступна: {e}")
    kit = HERE.parent / "open-bench" / kit_name
    etalon = json.loads((kit / "etalon" / etalon_name).read_text(encoding="utf-8"))
    manifest = json.loads((kit / "kit-manifest.json").read_text(encoding="utf-8"))
    lines = json.loads((kit / "etalon" / "jpg-lines.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        fman = fz.freeze([kit / p for p in manifest["rules_files"]],
                         out / "freeze",
                         vision_llm=lambda _a, _p: json.dumps(
                             {"paragraphs": lines}, ensure_ascii=False))
        entries = [dict(json.loads(
            (out / "freeze" / "clauses" / f"{f['sid']}.json")
            .read_text(encoding="utf-8")), sid=f["sid"])
            for f in fman["files"]]
    cl = _clauses(entries)
    verdicts = st.code_verdicts(entries)
    assert verdicts, "проба требует непустых вердиктов"
    with_v = ms.measure(etalon, cl, verdicts=verdicts)
    without = ms.measure(etalon, cl)

    def _texts(row, key):
        got = row.get(key)
        return sorted(str(v.get("text") if isinstance(v, dict) else v)
                      for v in got) if isinstance(got, list) else None

    a = {r["clause"]: r for r in with_v["clauses"]}
    b = {r["clause"]: r for r in without["clauses"]}
    assert set(a) == set(b)
    for key in a:
        for field in ("normative", "values", "consumed", "unclassified"):
            assert a[key].get(field) == b[key].get(field), (key, field)
        for field in ("rows", "members", "questions_open", "unconsumed"):
            assert _texts(a[key], field) == _texts(b[key], field), (key, field)
    for name in ("clauses", "carriers", "carriers_composed",
                 "clauses_with_rows", "rows", "rows_orphan",
                 "rows_on_nonnormative", "fields_without_carrier",
                 "fields_without_carrier_slot", "unconsumed_values",
                 "questions", "unclassified_tokens"):
        assert with_v["counts"][name] == without["counts"][name], name
    assert ({x["id"] for x in with_v["orphan_rows"]}
            == {x["id"] for x in without["orphan_rows"]})
    # Вердикт-зависимое меняется — проба не пуста.
    assert (with_v["counts"]["normative_without_rows"]
            != without["counts"]["normative_without_rows"])


# --------------------- второе неавторское ревью A11 (12.09.2026)
# Находки Н-1…Н-4. Закон один и тот же у всех читателей: свидетельство
# судится ПО ФОРМЕ, заявление судится ТЕМ ЖЕ УСЛОВИЕМ, каким сделано, а
# перечень отчёта выводится из его же записей. У каждой находки «минус»
# (подделка краснит) и «плюс» (честный пакет зелёный).

def _honest_pkg(tmp_path: Path, where: str = "h") -> Path:
    """Честный пакет фикстуры замка содержания: комплект заморозки,
    покрытие, измерение и протокол — вместе, как их пишет сборка."""
    run = _run(tmp_path / where, _doc(), _entries())
    _value_verdict(run)
    return _package_dir(tmp_path / where, etp.build_package(run))


def _set_manifest(pkg: Path, **keys) -> None:
    """Переписать ключи манифеста (файлы и их sha256 не трогаются)."""
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(keys)
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def test_честный_пакет_второго_ревью_зелёный(tmp_path):
    """Положительный контроль ко всем находкам Н-1…Н-4: пакет, собранный
    честно, после ужесточения гейта остаётся зелёным — законы ловят
    подделку, а не работу."""
    assert gate.check(_honest_pkg(tmp_path))[0] == []


@pytest.mark.parametrize("bad", [5, 3.5, True, "перечень"])
@pytest.mark.parametrize("field", ["clauses", "orphan_rows",
                                   "unconsumed_addressed"])
def test_пересчёт_судит_форму_каждого_читаемого_перечня(field, bad,
                                                        tmp_path):
    """Н-1: пересчёт измерителя по самому пакету читал перечни протокола
    без проверки формы — и ГЕЙТ ПАДАЛ (TypeError) на своде с
    `clauses: 5`, `orphan_rows: 5`, `unconsumed_addressed: 5`, а строка
    вместо перечня молча принималась пустой. Обещание спецификации и
    руководства — «гейт при этом не падает»: всякий перечень судится той
    же дисциплиной, что в content_layer, и порочная форма — красное
    слово, не след, не «пусто» и не падение. Одного try вокруг
    measure() мало: измеритель считает по ПАКЕТУ, роняли — перечни
    ПРОТОКОЛА."""
    pkg = _honest_pkg(tmp_path)
    proto = json.loads(
        (pkg / "measure-protocol.json").read_text(encoding="utf-8"))
    if field == "unconsumed_addressed":
        row = next(r for r in proto["clauses"] if r["clause"] == "S1|п.2")
        assert row["unconsumed_addressed"], "проба требует адресата"
        row["unconsumed_addressed"] = bad
    else:
        proto[field] = bad
    _rewrite(pkg, "measure-protocol.json", proto)
    problems, _ = gate.check(pkg)                  # не падает
    assert problems, (field, bad)
    assert any("не список" in p and type(bad).__name__ in p
               for p in problems), problems
    if field != "clauses":
        # Перечни, которых кроме пересчёта не читает никто, называет он.
        assert any(p.startswith("пересчёт измерителя по самому пакету:")
                   for p in problems), problems


@pytest.mark.parametrize("name,make", [
    ("записи голым списком клауз", lambda kit: kit["clauses"]),
    ("clauses не списком", lambda kit: dict(kit, clauses={})),
])
def test_запись_не_формы_комплекта_комплекта_не_отменяет(name, make,
                                                         tmp_path):
    """Н-2 (X-02, пробы П1-d/П1-g): «комплекта нет» определялось ФОРМОЙ
    ЗАПИСИ, и пакет, у которого freeze/clauses/<sid>.json переписан
    голым списком клауз либо с `clauses` не списком, выходил из гейта
    молча — зелёным с висячим якорем, стоило снять покрытие и
    измерение. Но сборщик считает ЛЮБОЙ такой файл в
    manifest.freeze_clauses, то есть сам объявляет его комплектом.
    Закон: комплект объявляет ФАЙЛ; запись не формы комплекта — красное
    слово о форме записи, а не «комплекта нет»."""
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    pkg = _package_dir(tmp_path, etp.build_package(run))
    doc = json.loads((pkg / "model.json").read_text(encoding="utf-8"))
    doc["rules"][-1]["sources"].append({"sid": "S1", "anchor": GHOST})
    _rewrite(pkg, "model.json", doc)
    kit = json.loads(
        (pkg / "freeze" / "clauses" / "S1.json").read_text(encoding="utf-8"))
    _rewrite(pkg, "freeze/clauses/S1.json", make(kit))
    _strip_manifest(pkg, ("coverage",), ("measure-protocol.json",))
    _set_manifest(pkg, measure={"available": False,
                                "reason": "нет заморозки — измерять не по чему"})
    problems, _ = gate.check(pkg)
    assert any("не формы комплекта" in p and "freeze/clauses/S1.json" in p
               for p in problems), problems
    assert any("клаузы заморозки в пакете есть" in p
               and "manifest.coverage" in p for p in problems), problems
    assert any("висячие якоря судить нечем" in p
               for p in problems), problems


def test_coverage_манифеста_не_объект_судится_как_заявленное(tmp_path):
    """Н-3 (X-02, проба П1-e): покрытие ОБЪЯВЛЯЛОСЬ условием
    `coverage is not None`, а СУДИЛОСЬ более узким `isinstance(dict)` —
    и в зазор проходил пакет с `coverage` вида [] / 0 / "x" / true:
    комплект вынут, а строки и якоря не судятся. Заявление и судейство
    — одно условие: не-пустой ключ любого вида объявляет покрытие
    (и получает слово о своей форме), а якоря при вынутом комплекте
    судить нечем — это красное слово, не тишина."""
    run = _run(tmp_path, _doc(), _entries())
    _value_verdict(run)
    files = etp.build_package(run)
    honest = json.loads(files["manifest.json"].decode("utf-8"))["coverage"]

    def _без_комплекта(where: str, coverage) -> list[str]:
        pkg = _package_dir(tmp_path / where, files)
        doc = json.loads((pkg / "model.json").read_text(encoding="utf-8"))
        doc["rules"][-1]["sources"].append({"sid": "S1", "anchor": GHOST})
        _rewrite(pkg, "model.json", doc)
        _strip_manifest(pkg, ("freeze_clauses",), ("freeze/clauses/S1.json",))
        _set_manifest(pkg, coverage=coverage)
        return gate.check(pkg)[0]

    for i, bad in enumerate(([], 0, "x", True)):
        problems = _без_комплекта(f"e{i}", bad)
        assert any("coverage манифеста не объект" in p
                   and type(bad).__name__ in p for p in problems), problems
        assert any("висячие якоря судить нечем" in p
                   for p in problems), (bad, problems)
    # Контроль: честный ОБЪЕКТ покрытия при вынутом комплекте даёт то же
    # слово о якорях — строгость от вида ключа больше не зависит.
    control = _без_комплекта("e-ok", honest)
    assert any("висячие якоря судить нечем" in p for p in control), control
    assert not any("coverage манифеста не объект" in p for p in control)


def test_перечень_нормативных_без_строк_выводится_из_записей(tmp_path):
    """Н-4 (X-01, класс A11-05 «удалить дефектную строку отчёта,
    сохранив количество»): гейт верил ПЕРЕЧНЮ `normative_without_rows`
    протокола, не выводя его из записей самого протокола, — и честный
    пакет, у которого в протоколе снят признак «не правило», а перечень
    и счётчик оставлены пустыми, проходил ЗЕЛЁНЫМ, хотя его же записи
    говорят: нормативен, строк нет, вердикта нет, открытых вопросов
    нет. Закон: перечень выводится из записей (нормативен ∧ без строк ∧
    без вердикта «не правило» ∧ без открытого вопроса) — первые три
    подтверждает пересчёт, — а расхождение краснится. Тогда остаток,
    принимаемый как заявлено, сужается ровно до признака «не правило»."""
    run = _run(tmp_path, _doc(), _entries(with_third=True))
    _value_verdict(run)
    cov.save_verdicts(run / "exchange",
                      {("S1", "п.3"): {"verdict": cov.NOT_RULE,
                                       "reason": "справка о составе",
                                       "round": 1}})
    files = etp.build_package(run)
    # (+) честный пакет с носителем, закрытым вердиктом «не правило».
    plus = _package_dir(tmp_path / "plus", files)
    assert gate.check(plus)[0] == []
    proto = json.loads(
        (plus / "measure-protocol.json").read_text(encoding="utf-8"))
    row = next(r for r in proto["clauses"] if r["clause"] == "S1|п.3")
    assert row["normative"] is True and row["rows"] == []
    assert row["not_rule_verdict"] is True and row["questions_open"] == []
    # (−) признак «не правило» снят, перечень и счётчик оставлены пустыми.
    minus = _package_dir(tmp_path / "minus", files)
    proto = json.loads(
        (minus / "measure-protocol.json").read_text(encoding="utf-8"))
    for r in proto["clauses"]:
        if r["clause"] == "S1|п.3":
            r["not_rule_verdict"] = False
    proto["normative_not_rule"] = []
    proto["counts"]["normative_not_rule"] = 0
    assert proto["normative_without_rows"] == []
    assert proto["counts"]["normative_without_rows"] == 0
    _rewrite(minus, "measure-protocol.json", proto)
    manifest = json.loads((minus / "manifest.json").read_text(encoding="utf-8"))
    manifest["measure"]["counts"] = proto["counts"]
    _set_manifest(minus, measure=manifest["measure"])
    problems, _ = gate.check(minus)
    assert any("нормативных носителей без строк свода 1" in p
               and "S1|п.3" in p for p in problems), problems
    assert any("не выведен из записей самого отчёта" in p
               for p in problems), problems


def test_перечень_против_записей_ловит_единая_функция():
    """Н-4, тот же закон у панели и сборки: подделка перечня при честных
    записях — не только беда гейта. Факты считает ОДНА функция, и она
    обязана назвать расхождение и взять факт объединением заявленного и
    выведенного, иначе индикатор «Биекция» зелёный там, где выпуск
    обязан отказать."""
    doc, entries = _doc(), _entries(with_third=True)
    rep = ms.measure(doc, _clauses(entries), verdicts={
        ("S1", "п.2", cov.norm_value(VALUE_LEFT)): {"verdict": "справочно",
                                                    "reason": "справка"}})
    assert rep["normative_without_rows"] == ["S1|п.3"]
    forged = json.loads(json.dumps(rep, ensure_ascii=False))
    forged["normative_without_rows"] = []
    forged["counts"]["normative_without_rows"] = 0
    facts = cov.bijection_facts(doc, cov.report(entries, doc, {}),
                                measure_report=forged, kit_entries=entries)
    assert facts["normative_without_rows"] == 1
    assert facts["normative_without_rows_keys"] == ["S1|п.3"]
    assert any("не выведен из записей самого отчёта" in w
               for w in facts["problems_by_kind"]["form"]), facts["problems"]
    assert facts["ok"] is False
    # Честный отчёт того же свода расхождения не даёт.
    ok_facts = cov.bijection_facts(doc, cov.report(entries, doc, {}),
                                   measure_report=rep, kit_entries=entries)
    assert not any("не выведен из записей" in w
                   for w in ok_facts["problems_by_kind"]["form"])
