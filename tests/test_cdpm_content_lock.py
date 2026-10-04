# -*- coding: utf-8 -*-
"""Замок содержания пакета постановки (аудит 2026-09-04, пункт 2, план
A2.4/A2.5): величина клаузы, не взятая ни одним машинным полем строк, не
спрошенная в анкете и без вердикта аудитора цикла, останавливает
сборку пакета словами; вердикт аудитора по величине («не норма /
повтор / справочно») засчитывается адресатом, как вердикт «не правило»
по клаузе; гейт пакета пересчитывает вердикт и счётчики измерителя из
протокола пакета и краснит measure.ok=false, расхождение с манифестом
и ненулевой остаток замка. Фикстуры — выдуманный мир (не наш набор);
отдельно — дымовая на золотых комплектах H0 / H0-К."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from cdpm import coverage as cov, exchange as ex, measure as ms  # noqa: E402
from cdpm import export_task_package as etp  # noqa: E402
from cdpm.gates import gate_task_package as gate  # noqa: E402
from test_cdpm_task_package import _ready_doc  # noqa: E402

CLAUSE_1 = ("Штраф за ночь вне базы — вес 10; x ≤ 1. Минимум человеко-дней "
            "командировок; цена рабочего дня (смены) — 100 условных единиц.")
CLAUSE_2 = ("Не более 6 рабочих дней подряд; перерыв между блоками — не "
            "менее 36 часов.")
CLAUSE_3 = "Экипаж смены — ровно 2 пилота."
VALUE_LEFT = "36 часов"


def _doc() -> dict:
    """Модель уровня «в» (test_cdpm_task_package._ready_doc) плюс правило
    на второй клаузе, берущее ОДНУ из двух её величин: «36 часов»
    остаётся в остатке без адресата."""
    doc = _ready_doc()
    doc["rules"].append(
        {"id": "R-D", "title": "Дни подряд", "zone": "ROSTERING", "kind": "HARD",
         "statement": "не более 6 рабочих дней подряд",
         "sources": [{"sid": "S1", "anchor": "п.2"}],
         "params": [{"name": "max_consecutive_days", "value": 6,
                     "unit": "count", "relation": "max",
                     "as_written": "6 рабочих дней подряд"}]})
    return doc


def _entries(with_third: bool = False) -> list[dict]:
    clauses = [{"anchor": "п.1", "text": CLAUSE_1},
               {"anchor": "п.2", "text": CLAUSE_2}]
    if with_third:
        clauses.append({"anchor": "п.3", "text": CLAUSE_3})
    return [{"sid": "S1", "file": "a.md", "sha256": "0" * 64,
             "clauses": clauses}]


def _clauses(entries: list[dict]) -> list[dict]:
    return [{"sid": e["sid"], "anchor": c["anchor"], "text": c["text"]}
            for e in entries for c in e["clauses"]]


def _run(tmp_path: Path, doc: dict, entries: list[dict]) -> Path:
    run = tmp_path / "run-lock"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    kit = run / "freeze"
    (kit / "clauses").mkdir(parents=True)
    (kit / "manifest.json").write_text(json.dumps({"files": [
        {"sid": e["sid"], "file": e["file"], "sha256": e["sha256"]}
        for e in entries]}), encoding="utf-8")
    for e in entries:
        (kit / "clauses" / f"{e['sid']}.json").write_text(
            json.dumps(e, ensure_ascii=False), encoding="utf-8")
    return run


# ---------------------------------------------------------- измеритель

def test_измеритель_чтит_вердикт_по_величине_и_только_из_перечня():
    doc, entries = _doc(), _entries()
    rep = ms.measure(doc, _clauses(entries))
    assert [x["value"] for x in rep["unconsumed_without_addressee"]] == [VALUE_LEFT]
    lock = ms.content_lock(rep)
    assert len(lock) == 1 and VALUE_LEFT in lock[0] and "п.2" in lock[0]
    # Вид величины — деловой подписью, не машинным именем.
    assert "число с единицей" in lock[0] and "number_with_unit" not in lock[0]
    # Вердикт «справочно» по величине — строгий адресат.
    vv = {("S1", "п.2", cov.norm_value("36  часов")):
          {"verdict": "справочно", "reason": "перерыв — справка"}}
    rep2 = ms.measure(doc, _clauses(entries), verdicts=vv)
    assert rep2["counts"]["unconsumed_without_addressee"] == 0
    assert rep2["counts"]["unconsumed_by_verdict"] == 1
    assert ms.content_lock(rep2) == []
    row = next(r for r in rep2["clauses"] if r["clause"] == "S1|п.2")
    assert row["unconsumed_addressed"] == ["вердикт аудитора: справочно"]
    # Слово вне закрытого перечня вердиктом не является (fail-closed).
    bad = {("S1", "п.2", cov.norm_value(VALUE_LEFT)):
           {"verdict": "ок", "reason": "как-нибудь"}}
    assert ms.measure(doc, _clauses(entries), verdicts=bad)["counts"][
        "unconsumed_without_addressee"] == 1


def test_нормативный_носитель_без_строк_закрывается_вердиктом_или_вопросом():
    doc, entries = _doc(), _entries(with_third=True)
    vv = {("S1", "п.2", cov.norm_value(VALUE_LEFT)):
          {"verdict": "справочно", "reason": "справка"}}
    rep = ms.measure(doc, _clauses(entries), verdicts=vv)
    assert rep["normative_without_rows"] == ["S1|п.3"]
    lock = ms.content_lock(rep)
    assert len(lock) == 1 and "S1|п.3" in lock[0] and "Экипаж смены" in lock[0]
    # Вердикт «не правило» по клаузе — слово аудитора старше эвристики.
    vv2 = dict(vv)
    vv2[("S1", "п.3")] = {"verdict": cov.NOT_RULE, "reason": "справка о составе"}
    rep2 = ms.measure(doc, _clauses(entries), verdicts=vv2)
    assert rep2["normative_without_rows"] == []
    assert rep2["counts"]["normative_not_rule"] == 1
    assert ms.content_lock(rep2) == []
    # Открытый вопрос на клаузе — адресат носителя без строк, ПОКА он
    # открыт: применённый ответ носитель не закрывает (вопрос отработан,
    # строки по клаузе так и нет — носитель предъявляется снова).
    doc_q = _doc()
    doc_q["questions"].append(
        {"id": "Q-3", "part": "1", "title": "Состав", "text": "т",
         "source_refs": [{"sid": "S1", "anchor": "п.3"}]})
    rep3 = ms.measure(doc_q, _clauses(entries), verdicts=vv)
    assert rep3["normative_without_rows"] == []
    row3 = next(r for r in rep3["clauses"] if r["clause"] == "S1|п.3")
    assert row3["questions"] == ["Q-3"] and row3["questions_open"] == ["Q-3"]
    doc_q["questions"][-1]["answer"] = {"raw": "два", "status": "применён",
                                        "key": "а", "revision": 2}
    rep4 = ms.measure(doc_q, _clauses(entries), verdicts=vv)
    assert rep4["normative_without_rows"] == ["S1|п.3"]
    row4 = next(r for r in rep4["clauses"] if r["clause"] == "S1|п.3")
    assert row4["questions"] == ["Q-3"] and row4["questions_open"] == []
    assert ms.content_lock(rep4)
    # Ответ «ожидает обработчика» — вопрос уже не открыт: тоже не закрывает.
    doc_q["questions"][-1]["answer"] = {"raw": "два",
                                        "status": "ожидает обработчика",
                                        "revision": 2}
    assert ms.measure(doc_q, _clauses(entries),
                      verdicts=vv)["normative_without_rows"] == ["S1|п.3"]
    # Неразобранный ответ — вопрос задаётся повторно, он открыт.
    doc_q["questions"][-1]["answer"] = {"raw": "??", "status": "не разобран",
                                        "revision": 2}
    assert ms.measure(doc_q, _clauses(entries),
                      verdicts=vv)["normative_without_rows"] == []


# ---------------------------------------------------------- сборка пакета

def test_экспорт_отказывает_по_величине_без_адресата_и_пускает_с_вердиктом(tmp_path):
    run = _run(tmp_path, _doc(), _entries())
    with pytest.raises(etp.TaskPackageError) as ei:
        etp.build_package(run)
    msg = str(ei.value)
    assert "замок содержания" in msg and VALUE_LEFT in msg and "п.2" in msg
    # Вердикт с чужим словом — не вердикт: отказ остаётся.
    cov.save_verdicts(run / "exchange", {}, {
        ("S1", "п.2", cov.norm_value(VALUE_LEFT)):
            {"verdict": "ок", "reason": "как-нибудь", "round": 1}})
    with pytest.raises(etp.TaskPackageError):
        etp.build_package(run)
    # Вердикт аудитора «справочно» по величине — пакет выпускается.
    cov.save_verdicts(run / "exchange", {}, {
        ("S1", "п.2", cov.norm_value(VALUE_LEFT)):
            {"verdict": "справочно", "reason": "перерыв — справка",
             "round": 1, "value": VALUE_LEFT}})
    files = etp.build_package(run)
    manifest = json.loads(files["manifest.json"].decode("utf-8"))
    assert manifest["coverage"]["values_without_addressee"] == 0
    assert manifest["coverage"]["values_by_verdict"] == 1
    assert manifest["coverage"]["normative_without_rows"] == 0
    assert manifest["measure"]["available"] and manifest["measure"]["ok"]
    assert manifest["measure"]["counts"]["unconsumed_by_verdict"] == 1
    proto = json.loads(files["measure-protocol.json"].decode("utf-8"))
    assert proto["unconsumed_by_verdict"][0]["value"] == VALUE_LEFT
    # Детерминизм: повторная сборка байт-идентична.
    assert etp.build_package(run) == files
    # Гейт: пересчёт по протоколу сходится с манифестом — зелёный.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for name, body in files.items():
        (pkg / name).parent.mkdir(parents=True, exist_ok=True)
        (pkg / name).write_bytes(body)
    problems, _ = gate.check(pkg)
    assert problems == [], problems


def test_экспорт_чтит_вердикт_не_правило_по_нормативной_клаузе(tmp_path):
    run = _run(tmp_path, _doc(), _entries(with_third=True))
    cov.save_verdicts(run / "exchange", {}, {
        ("S1", "п.2", cov.norm_value(VALUE_LEFT)):
            {"verdict": "справочно", "reason": "справка", "round": 1}})
    # Третья клауза без вердикта — прежний замок адресов (не содержания).
    with pytest.raises(etp.TaskPackageError) as ei:
        etp.build_package(run)
    assert "клауз без вердикта покрытия" in str(ei.value)
    # Вердикт «не правило» закрывает и адрес, и нормативный носитель
    # без строк; вердикты величин при записи клаузных не теряются.
    cov.save_verdicts(run / "exchange", {
        ("S1", "п.3"): {"verdict": cov.NOT_RULE, "reason": "справка", "round": 1}})
    assert cov.load_value_verdicts(run / "exchange")
    manifest = json.loads(etp.build_package(run)["manifest.json"].decode("utf-8"))
    assert manifest["coverage"]["normative_not_rule"] == 1
    assert manifest["coverage"]["normative_without_rows"] == 0


def test_сбой_измерителя_останавливает_сборку(tmp_path, monkeypatch):
    run = _run(tmp_path, _doc(), _entries())

    def boom(*_a, **_k):
        raise RuntimeError("сломан")

    monkeypatch.setattr(ms, "measure", boom)
    with pytest.raises(etp.TaskPackageError) as ei:
        etp.build_package(run)
    assert "измеритель постановки не отработал" in str(ei.value)


def test_испорченный_файл_вердиктов_называется_своим_именем(tmp_path):
    run = _run(tmp_path, _doc(), _entries())
    (run / "exchange").mkdir()
    (run / "exchange" / cov.COVERAGE_FILE).write_text("{не json", encoding="utf-8")
    with pytest.raises(etp.TaskPackageError) as ei:
        etp.build_package(run)
    msg = str(ei.value)
    assert "файл вердиктов coverage.json не читается" in msg
    assert "измеритель постановки не отработал" not in msg


# ---------------------------------------------------------- гейт пакета

def _package(tmp_path: Path) -> tuple[Path, dict]:
    run = _run(tmp_path, _doc(), _entries())
    cov.save_verdicts(run / "exchange", {}, {
        ("S1", "п.2", cov.norm_value(VALUE_LEFT)):
            {"verdict": "справочно", "reason": "справка", "round": 1}})
    files = etp.build_package(run)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for name, body in files.items():
        # Записи пакета бывают вложенными (freeze/clauses/<sid>.json с
        # 7b4455f) — каталог записи создаётся, как делает распаковка.
        (pkg / name).parent.mkdir(parents=True, exist_ok=True)
        (pkg / name).write_bytes(body)
    return pkg, files


def _rewrite(pkg: Path, name: str, obj: dict) -> None:
    """Переписать файл пакета и его сумму в манифесте (подделка
    честная по суммам — гейт обязан ловить содержание)."""
    body = (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    (pkg / name).write_bytes(body)
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        if f["name"] == name:
            f["sha256"] = hashlib.sha256(body).hexdigest()
            f["bytes"] = len(body)
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_гейт_краснит_measure_ok_false_и_подделку_счётчиков(tmp_path):
    pkg, _files = _package(tmp_path)
    assert gate.check(pkg)[0] == []
    proto = json.loads((pkg / "measure-protocol.json").read_text(encoding="utf-8"))
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    # (1) Манифест заявляет ok=false согласованно с протоколом (строка на
    # ненормативной клаузе) — гейт красный по measure.ok=false.
    bad_proto = json.loads(json.dumps(proto))
    bad_proto["counts"]["rows_on_nonnormative"] = 1
    bad_proto["rows_on_nonnormative"] = [
        {"rule_id": "R-D", "clause": "S1|п.2", "missing_signs": ["модальность"],
         "text": CLAUSE_2}]
    _rewrite(pkg, "measure-protocol.json", bad_proto)
    ok, probs = ms.verdict(bad_proto)
    assert not ok
    manifest["measure"] = dict(manifest["measure"], ok=False, problems=probs,
                               counts=bad_proto["counts"])
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    problems, _ = gate.check(pkg)
    assert any("measure.ok=false" in p for p in problems), problems
    # (2) Манифест говорит ok=true, протокол — нет: расхождение красное.
    manifest["measure"] = dict(manifest["measure"], ok=True, problems=[])
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    problems, _ = gate.check(pkg)
    assert any("≠ пересчёту по протоколу" in p for p in problems), problems
    # (3) Протокол несёт остаток без адресата при нулях манифеста —
    # замок содержания по протоколу и расхождение coverage.
    pkg2, _f2 = _package(tmp_path / "second")
    proto2 = json.loads((pkg2 / "measure-protocol.json").read_text(encoding="utf-8"))
    proto2["unconsumed_without_addressee"] = [
        {"clause": "S1|п.2", "value": VALUE_LEFT, "kind": "number_with_unit"}]
    proto2["counts"]["unconsumed_without_addressee"] = 1
    _rewrite(pkg2, "measure-protocol.json", proto2)
    problems, _ = gate.check(pkg2)
    assert any("замок содержания по протоколу" in p and VALUE_LEFT in p
               for p in problems), problems
    assert any("measure.counts манифеста ≠" in p for p in problems), problems
    assert any("coverage.values_without_addressee" in p for p in problems), problems
    # (4) Покрытие клауз заявлено, протокола нет — красный.
    pkg3, _f3 = _package(tmp_path / "third")
    (pkg3 / "measure-protocol.json").unlink()
    m3 = json.loads((pkg3 / "manifest.json").read_text(encoding="utf-8"))
    m3["files"] = [f for f in m3["files"] if f["name"] != "measure-protocol.json"]
    (pkg3 / "manifest.json").write_text(
        json.dumps(m3, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    problems, _ = gate.check(pkg3)
    assert any("measure-protocol.json в пакете нет" in p for p in problems), problems


# ---------------------------------------------------------- цикл аудита

def test_аудит_цикла_даёт_вердикты_по_величинам(tmp_path):
    doc, entries = _doc(), _entries()
    prompts: list[str] = []

    def auditor(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({"remarks": [], "fit_votes": [], "coverage": [],
                           "coverage_values": [
                               {"anchor": "п.2", "value": VALUE_LEFT,
                                "verdict": "Справочно", "reason": "перерыв — справка"},
                               {"anchor": "п.2", "value": "99 часов",
                                "verdict": "справочно", "reason": "вне задания"},
                               {"anchor": "п.2", "value": VALUE_LEFT,
                                "verdict": "ок", "reason": "чужое слово"}]},
                          ensure_ascii=False)

    pack, rejected = ex._audit_round(auditor, doc, entries, 1,
                                     cache_dir=tmp_path / "c")
    assert any(f"[п.2] число с единицей «{VALUE_LEFT}»" in p for p in prompts)
    assert not any("number_with_unit" in p for p in prompts)
    assert pack["coverage_values"] == [
        {"sid": "S1", "anchor": "п.2", "value": VALUE_LEFT,
         "verdict": "справочно", "reason": "перерыв — справка", "round": 1}]
    assert pack["coverage_values_missing"] == []
    reasons = [r for rj in rejected for r in rj["reasons"]]
    assert any("вне задания по величинам" in r for r in reasons)
    # Чужое слово вердиктом не является — отбраковка со словами; первый
    # принятый вердикт по величине держится.
    assert any("вне перечня" in r for r in reasons)

    # Молчание — остаток без адресата: метрика раунда, «успех» невозможен.
    def silent(_prompt: str) -> str:
        return json.dumps({"remarks": [], "fit_votes": [], "coverage": []})

    pack2, _ = ex._audit_round(silent, doc, entries, 1)
    assert pack2["coverage_values_missing"] == [
        {"sid": "S1", "anchor": "п.2", "value": VALUE_LEFT}]
    metrics = ex.metrics_of(pack2)
    assert metrics["coverage_values_missing"] == 1
    hist = [dict(metrics, mode="полный"), dict(metrics, mode="полный")]
    assert ex.decide(hist, 9, {}) == "продолжать"
    clean = dict(metrics, coverage_values_missing=0, mode="полный")
    assert ex.decide([clean, clean], 9, {}) == "успех"
    # Вердикт прежнего раунда закрывает величину: задание её не повторяет.
    vv = {("S1", "п.2", cov.norm_value(VALUE_LEFT)):
          {"verdict": "справочно", "reason": "справка", "round": 1}}
    pack3, _ = ex._audit_round(silent, doc, entries, 2, value_verdicts=vv)
    assert pack3["coverage_values_missing"] == []
    # Замечание по клаузе величины — адресат (правка — её судьба), но
    # только то, что величину возьмёт: класс «пропуск»/«искажение»
    # весомой серьёзности. Редакционное замечание, изобретение или выбор
    # чтения на той же клаузе величину не берут — она остаётся в
    # остатке, иначе цикл объявил бы «успех» при величине, которую
    # сборка пакета остановит.
    def remark_of(severity: str, klass: str, rule=None):
        def _fn(_prompt: str) -> str:
            return json.dumps({"remarks": [
                {"severity": severity, "defect_class": klass,
                 "target": {"rule": rule, "question": None},
                 "source_refs": [{"sid": "S1", "anchor": "п.2"}],
                 "text": "перерыв 36 часов не взят", "evidence": CLAUSE_2,
                 "proposal": "добавить правило"}],
                "fit_votes": [], "coverage": []}, ensure_ascii=False)
        return _fn

    left = [{"sid": "S1", "anchor": "п.2", "value": VALUE_LEFT}]
    pack4, _ = ex._audit_round(remark_of("СЕРЬЁЗНОЕ", "пропуск"), doc, entries, 1)
    assert pack4["coverage_values_missing"] == []
    pack5, rej5 = ex._audit_round(remark_of("КРИТИЧЕСКОЕ", "искажение", "R-D"),
                                  doc, entries, 1)
    assert pack5["coverage_values_missing"] == [], rej5
    # Отрицательные контроли: редакционный «пропуск», весомое
    # «изобретение», «выбор чтения» — величина без адресата.
    pack6, _ = ex._audit_round(remark_of("РЕДАКЦИОННОЕ", "пропуск"), doc, entries, 1)
    assert pack6["coverage_values_missing"] == left
    pack7, rej7 = ex._audit_round(remark_of("СЕРЬЁЗНОЕ", "изобретение", "R-D"),
                                  doc, entries, 1)
    assert pack7["coverage_values_missing"] == left, rej7
    pack8, rej8 = ex._audit_round(remark_of("КРИТИЧЕСКОЕ", "выбор чтения", "R-D"),
                                  doc, entries, 1)
    assert pack8["coverage_values_missing"] == left, rej8
    assert ex.metrics_of(pack6)["coverage_values_missing"] == 1


def test_файл_вердиктов_хранит_величины_рядом_с_клаузами(tmp_path):
    d = tmp_path / "exchange"
    cov.save_verdicts(d, {("S1", "п.1"): {"verdict": cov.NOT_RULE, "reason": "з", "round": 1}},
                      {("S1", "п.2", cov.norm_value("36  Часов")):
                       {"verdict": "справочно", "reason": "с", "round": 1,
                        "value": "36 Часов"}})
    # Перезапись клаузных вердиктов без величин — величины не теряются.
    cov.save_verdicts(d, {("S1", "п.1"): {"verdict": cov.NOT_RULE, "reason": "з2", "round": 2}})
    assert cov.load_verdicts(d) == {("S1", "п.1"): {"verdict": cov.NOT_RULE,
                                                    "reason": "з2", "round": 2}}
    # Величина в файле — дословно, как в документе; при загрузке текст
    # сохраняется (файл пересохраняется каждый раунд — ключ на месте
    # текста сделал бы его нечитаемым).
    assert cov.load_value_verdicts(d) == {
        ("S1", "п.2", "36 часов"): {"verdict": "справочно", "reason": "с",
                                    "round": 1, "value": "36 Часов"}}
    cov.save_verdicts(d, {("S1", "п.1"): {"verdict": cov.NOT_RULE, "reason": "з3", "round": 3}},
                      cov.load_value_verdicts(d))
    rows3 = json.loads((d / cov.COVERAGE_FILE).read_text(encoding="utf-8"))["verdicts"]
    assert [r.get("value") for r in rows3] == [None, "36 Часов"]
    rows = json.loads((d / cov.COVERAGE_FILE).read_text(encoding="utf-8"))["verdicts"]
    assert [r.get("value") for r in rows] == [None, "36 Часов"]
    both = cov.load_all_verdicts(d)
    assert len(both) == 2 and ("S1", "п.2", "36 часов") in both


# ---------------------------------------------------------- золотые комплекты

@pytest.mark.parametrize("kit_name,etalon_name", [
    ("polyaris-h0", "VMP-H0.json"), ("polyaris-h0k", "VMP-H0K.json")])
def test_золотые_комплекты_проходят_замок_содержания(kit_name, etalon_name):
    """Дымовая: эталон против клауз штатной заморозки (без языковой
    модели) с вердиктами кода стадии структуры — остатка без адресата
    нет, нормативные носители без строк закрыты вердиктом «не правило»
    (канон композиции целевой, заголовок раздела формы)."""
    import tempfile
    try:
        from cdpm import freeze as fz, structure as st
    except Exception as e:                                # noqa: BLE001
        pytest.skip(f"заморозка недоступна: {e}")
    kit = REPO / "open-bench" / kit_name
    etalon = json.loads((kit / "etalon" / etalon_name).read_text(encoding="utf-8"))
    manifest = json.loads((kit / "kit-manifest.json").read_text(encoding="utf-8"))
    lines = json.loads((kit / "etalon" / "jpg-lines.json").read_text(encoding="utf-8"))

    def vision(_ask, _path):
        return json.dumps({"paragraphs": lines}, ensure_ascii=False)

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        fman = fz.freeze([kit / p for p in manifest["rules_files"]],
                         out / "freeze", vision_llm=vision)
        entries = []
        for f in fman["files"]:
            d = json.loads((out / "freeze" / "clauses" / f"{f['sid']}.json")
                           .read_text(encoding="utf-8"))
            entries.append({"sid": f["sid"], "clauses": d["clauses"]})
    verdicts = st.code_verdicts(entries)
    rep = ms.measure(etalon, _clauses(entries), verdicts=verdicts)
    assert rep["counts"]["unconsumed_without_addressee"] == 0
    assert rep["normative_without_rows"] == []
    assert rep["counts"]["normative_not_rule"] == 2
    assert ms.content_lock(rep) == []
    # Без вердиктов кода те же два носителя честно предъявляются.
    bare = ms.measure(etalon, _clauses(entries))
    assert len(bare["normative_without_rows"]) == 2
