# -*- coding: utf-8 -*-
"""Мост А: пакет постановки задачи COSMOS (спецификация v1.0).

Охраняемое: пакет самодостаточен (манифест с SHA-256 всех файлов),
разрешённые режимы и целевые манифеста равны пересчёту матрицы
корректности, веса обязательны при режимах со штрафами, порча файла
ловится гейтом, пустая постановка (ни одного режима) не выгружается."""
import hashlib
import json
import re
import zipfile
import io
from pathlib import Path

import pytest

from cdpm import export_task_package as tp, model as m
from cdpm.gates import gate_task_package as gate


def _ready_doc():
    """Модель уровня «в»: чистая жёсткая, взвешенная мягкая, целевая
    пейринга; ростеринг — отказ от оптимизации."""
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1",
                 "revision": 2},
        "sources": [{"sid": "S1", "file": "a.md",
                     "sha256": "0" * 64}],
        "rules": [
            {"id": "R-H", "title": "Жёсткое", "statement": "x ≤ 1",
             "zone": "BOTH", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
            {"id": "R-S", "title": "Ночёвки", "statement":
                "штраф за ночь вне базы: вес 10", "zone": "PAIRING",
             "kind": "SOFT",
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
            # Спецификация 1.9: без объявленного критерия режим
            # optimization пейринга снимается — фикстура несёт
            # каноническую строку критерия авиакомпании.
            {"id": "O-1", "title": "Критерий оптимизации пейринга",
             "statement": "Минимум человеко-дней командировок; "
                          "цена рабочего дня (смены) — 100 "
                          "условных единиц", "zone": "PAIRING",
             "direction": "minimize",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
        ],
    }


def _run_dir(tmp_path, doc):
    run = tmp_path / "run-9001"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (run / "extract" / "svod.md").write_text("# свод\n",
                                             encoding="utf-8")
    return run


def test_package_manifest_modes_and_gate_green(tmp_path):
    doc = _ready_doc()
    assert m.validate_model(doc) == []
    run = _run_dir(tmp_path, doc)
    body = tp.export_zip(run)
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(body)
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
    assert {"manifest.json", "model.json", "objective.json",
            "svod.md"} <= names
    assert manifest["spec_version"] == tp.SPEC_VERSION
    # Ф-1.7: метки времени в манифесте нет — два экспорта подряд
    # байт-идентичны по манифесту (сравнимость отпечатков пакета
    # между нажатиями кнопки по построению).
    assert "created" not in manifest
    assert manifest["source_run"] == "run-9001"
    assert manifest["has_data"] is False
    # Пейринг: целевая есть, мягкая взвешена → все три режима;
    # ростеринг: отказ подтверждён → только выполнимость.
    assert manifest["allowed_modes"]["pairing"] == [
        "optimization", "feasibility-soft", "feasibility-hard"]
    assert manifest["allowed_modes"]["rostering"] == [
        "feasibility-soft", "feasibility-hard"]
    assert manifest["objectives"]["pairing"] == ["O-1"]
    assert manifest["objectives"]["rostering"] == []
    assert gate.check(pkg)[0] == []


def test_modes_judged_by_package_spec_version():
    """Спецификация 1.9 задним числом не действует: пакет без
    критерия пейринга теряет optimization при текущей сборке и при
    версии ≥ 1.9, но замороженный пакет 1.3 (эталоны вехи А)
    сохраняет режим — судится законом своей версии."""
    doc = _ready_doc()
    doc["objectives"][0]["title"] = "Стоимость"
    doc["objectives"][0]["statement"] = "minimize cost"
    full = ["optimization", "feasibility-soft", "feasibility-hard"]
    assert tp.allowed_modes(doc)[0]["pairing"] == full[1:]
    assert tp.allowed_modes(doc, spec_version="1.9")[0][
        "pairing"] == full[1:]
    assert tp.allowed_modes(doc, spec_version="1.3")[0][
        "pairing"] == full
    # 1.10 ≥ 1.9 числом, не строкой.
    assert tp.allowed_modes(doc, spec_version="1.10")[0][
        "pairing"] == full[1:]


def test_reexport_manifest_byte_identical(tmp_path):
    """Ф-1.7: два одинаковых по смыслу экспорта несут байт-идентичный
    манифест — отпечаток пакета у потребителя (по содержимому
    манифеста) одинаков между нажатиями кнопки по построению."""
    run = _run_dir(tmp_path, _ready_doc())
    with zipfile.ZipFile(io.BytesIO(tp.export_zip(run))) as z1:
        m1 = z1.read("manifest.json")
    with zipfile.ZipFile(io.BytesIO(tp.export_zip(run))) as z2:
        m2 = z2.read("manifest.json")
    assert m1 == m2


#: Клаузы фикстуры заморозки — В ТОЙ ФОРМЕ, В КАКОЙ ИХ ПИШЕТ
#: cdpm.freeze: запись {sid, file, sha256, clauses: [{anchor, text}]} и
#: freeze/manifest.json рядом. До ревью A11-2 фикстура несла голый
#: список клауз без манифеста заморозки — формы, которой ни один
#: писатель не производит (0 из 96 боевых прогонов), — и гейт на ней
#: выходил молча, приняв «нет комплекта». Ожидание правлено вслед за
#: законом: комплектом объявляет ФАЙЛ, а не форма записи.
_FREEZE_TEXT_1 = ("Штраф за ночь вне базы — вес 10; x ≤ 1. Минимум "
                  "человеко-дней командировок; цена рабочего дня "
                  "(смены) — 100 условных единиц.")
_FREEZE_TEXT_2 = "Общие положения. Порядок утверждения определяется приказом."
_FREEZE_TEXT_S2 = "Пояснительная записка к документу."


def _freeze_kit(run, entries: list[dict]) -> None:
    """Комплект заморозки прогона так, как его пишет cdpm.freeze:
    freeze/manifest.json (перечень файлов без клауз) и запись
    freeze/clauses/<sid>.json на каждый источник."""
    clauses = run / "freeze" / "clauses"
    clauses.mkdir(parents=True)
    (run / "freeze" / "manifest.json").write_text(json.dumps(
        {"arbitration": "текст", "files": [
            {k: v for k, v in dict(e, clause_count=len(e["clauses"])).items()
             if k != "clauses"} for e in entries]},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_freeze_clauses_travel_with_package(tmp_path):
    """Дополнение к 1.17 (слово владельца 06.09.2026): записи
    freeze/clauses/S*.json едут с пакетом один в один (имена и байты),
    названы в манифесте с sha256, сводка — в manifest.freeze_clauses;
    гейт зелёный; без каталога заморозки ключа и записей нет.

    Комплект — В ФОРМЕ ПИСАТЕЛЯ (cdpm.freeze), и вердикты аудитора по
    клаузам, которых не берёт ни одна строка свода, лежат в
    exchange/coverage.json: так выглядит настоящий прогон, и только на
    нём проверка «гейт зелёный» что-то значит."""
    from cdpm import coverage as cov
    run = _run_dir(tmp_path, _ready_doc())
    entries = [
        {"sid": "S1", "file": "a.md", "sha256": "0" * 64,
         "clauses": [{"anchor": "п.1", "text": _FREEZE_TEXT_1},
                     {"anchor": "п.2", "text": _FREEZE_TEXT_2}]},
        {"sid": "S2", "file": "b.xlsx", "sha256": "1" * 64,
         "clauses": [{"anchor": "лист 1", "text": _FREEZE_TEXT_S2}]},
    ]
    _freeze_kit(run, entries)
    clauses = run / "freeze" / "clauses"
    s1 = json.dumps(entries[0], ensure_ascii=False, indent=2) + "\n"
    s2 = json.dumps(entries[1], ensure_ascii=False, indent=2) + "\n"
    (clauses / "S1.json").write_text(s1, encoding="utf-8")
    (clauses / "S2.json").write_text(s2, encoding="utf-8")
    (clauses / "notes.txt").write_text("не json — не едет", encoding="utf-8")
    # Клаузы, которых не берёт ни одна строка свода, закрыты вердиктом
    # аудитора «не правило» с причиной — иначе замок биекции выпуск не
    # пропустит (и правильно сделает).
    cov.save_verdicts(run / "exchange", {
        ("S1", "п.2"): {"verdict": cov.NOT_RULE, "reason": "общие положения",
                        "round": 1},
        ("S2", "лист 1"): {"verdict": cov.NOT_RULE, "reason": "пояснение",
                           "round": 1}})
    body = tp.export_zip(run)
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = set(z.namelist())
        assert {"freeze/clauses/S1.json", "freeze/clauses/S2.json"} <= names
        assert "freeze/clauses/notes.txt" not in names
        # Один в один — БАЙТАМИ файла прогона (не строкой: перевод
        # строки на диске машинный, и сравнение с текстом сравнивало бы
        # платформу, а не запись).
        assert z.read("freeze/clauses/S1.json") == (
            clauses / "S1.json").read_bytes()
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    listed = {f["name"]: f for f in manifest["files"]}
    assert "freeze/clauses/S1.json" in listed and "sha256" in listed[
        "freeze/clauses/S1.json"]
    assert manifest["freeze_clauses"] == {
        "files": 2, "clauses": 3, "sids": ["S1", "S2"],
        "path": "freeze/clauses/<sid>.json"}
    # Честная сборка при комплекте пишет покрытие и измерение ВМЕСТЕ с
    # ним — на этом и стоит закон гейта «клаузы есть, а покрытия нет».
    assert manifest["coverage"]["clauses"] == 3
    assert manifest["coverage"]["missing"] == 0
    assert manifest["coverage"]["not_rule"] == 2
    assert manifest["measure"]["available"] is True
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(body)
    problems, counters = gate.check(pkg)
    assert problems == [], problems
    assert counters["files"] >= 5
    # Прогон без каталога заморозки — ни записей, ни ключа.
    run2 = _run_dir(tmp_path / "b", _ready_doc())
    with zipfile.ZipFile(io.BytesIO(tp.export_zip(run2))) as z:
        assert not [n for n in z.namelist() if n.startswith("freeze/")]
        assert "freeze_clauses" not in json.loads(
            z.read("manifest.json").decode("utf-8"))


def test_gate_catches_tampered_file(tmp_path):
    run = _run_dir(tmp_path, _ready_doc())
    files = tp.build_package(run)
    files["objective.json"] += b" "        # порча после манифеста
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(pkg)
    assert any("sha256" in p for p in problems)


def test_gate_catches_mode_drift_and_extra_file(tmp_path):
    run = _run_dir(tmp_path, _ready_doc())
    files = tp.build_package(run)
    manifest = json.loads(files["manifest.json"])
    manifest["allowed_modes"]["rostering"] = list(m.TASK_MODES)
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False).encode("utf-8")
    files["lishnij.txt"] = b"x"
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(pkg)
    assert any("allowed_modes" in p for p in problems)
    assert any("lishnij.txt" in p for p in problems)


def test_missing_weight_strips_penalty_modes_honestly(tmp_path):
    """До 2026-08-27 пакет с невзвешенной мягкой при заявленном
    режиме со штрафами краснел на гейте; живой случай одной из линий
    (подтверждённый отказ от весов) показал честную семантику:
    производитель СНИМАЕТ режимы со штрафами при сборке — гейт
    зелёный, у задачи остаётся «любое допустимое»."""
    doc = _ready_doc()
    doc["rules"][1]["statement"] = "штраф за ночь вне базы"  # без веса
    # P-N-SHD 2026-08-30: «подтверждённый отказ от весов» из живого
    # случая моделируется честно — goal_decline применённого GOAL
    # (членство в rule_refs без веса и без отказа теперь красно).
    doc["questions"][0]["answer"]["goal_decline"] = True
    run = _run_dir(tmp_path, doc)
    pkg = tmp_path / "p.zip"
    body = tp.export_zip(run)
    pkg.write_bytes(body)
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        manifest = json.loads(z.read("manifest.json"))
    assert manifest["allowed_modes"]["pairing"] == ["feasibility-hard"]
    problems, _ = gate.check(pkg)
    assert problems == []


def test_empty_statement_package_refused(tmp_path):
    doc = _ready_doc()
    # Всё блокировано: жёсткая строка под открытым вопросом.
    doc["rules"][0]["blockers"] = ["Q-1-X"]
    doc["questions"].append(
        {"id": "Q-1-X", "part": "1", "title": "Х", "text": "т",
         "answer_format": {"type": "choice", "options": [
             {"key": "а", "text": "a"}, {"key": "б", "text": "b"}]},
         "rule_refs": ["R-H"],
         "source_refs": [{"sid": "S1", "anchor": "п.1"}]})
    doc["rules"][1]["blockers"] = ["Q-1-X"]
    doc["objectives"] = []
    run = _run_dir(tmp_path, doc)
    with pytest.raises(tp.TaskPackageError):
        tp.export_zip(run)


def test_manifest_carries_machine_crew_size(tmp_path):
    """Ревизия 1.2: численность экипажа — машинным полем манифеста,
    распознаёт производитель из СВОИХ формулировок («Число пилотов в
    экипаже полётной смены = 2» дважды ломало потребителя); конфликт
    чисел — поле честно отсутствует."""
    doc = _ready_doc()
    doc["rules"].append(
        {"id": "R-CS", "title": "Состав экипажа",
         "statement": "Число пилотов в экипаже полётной смены = 2 "
                      "(жёсткое требование к составу).",
         "zone": "BOTH", "kind": "HARD",
         "sources": [{"sid": "S1", "anchor": "п.1"}]})
    run = _run_dir(tmp_path, doc)
    files = tp.build_package(run)
    manifest = json.loads(files["manifest.json"])
    assert manifest["crew_size"] == {"value": 2, "rule": "R-CS"}
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(files))
    assert gate.check(pkg)[0] == []
    # Конфликт разных чисел → поля нет (не угадываем).
    doc["rules"].append(
        {"id": "R-CS2", "title": "Состав", "statement":
            "Экипаж состоит из 3 пилотов", "zone": "BOTH",
         "kind": "DATA",
         "sources": [{"sid": "S1", "anchor": "п.1"}]})
    run2 = tmp_path / "run-9002"
    (run2 / "extract").mkdir(parents=True)
    (run2 / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    manifest2 = json.loads(tp.build_package(run2)["manifest.json"])
    assert "crew_size" not in manifest2
    # Подделка поля ловится гейтом.
    bad = tp.build_package(run)
    mf = json.loads(bad["manifest.json"])
    mf["crew_size"] = {"value": 0, "rule": "R-НЕТ"}
    bad["manifest.json"] = json.dumps(
        mf, ensure_ascii=False).encode("utf-8")
    pkg2 = tmp_path / "p2.zip"
    pkg2.write_bytes(tp.write_zip(bad))
    problems, _ = gate.check(pkg2)
    assert any("crew_size" in p for p in problems)


def test_manifest_carries_kit_scope(tmp_path):
    """04.09 (расчёт переехал в COSMOS): признак допуска моделей
    комплекта едет с пакетом — потребитель выбирает модели
    сертификатора по признаку комплекта, не по площадке. Без признака
    ключа нет (потребитель судит «только локальные»); кривой статус —
    тоже без ключа, а не падение сборки."""
    doc = _ready_doc()
    run = _run_dir(tmp_path, doc)
    assert "kit" not in json.loads(tp.build_package(run)["manifest.json"])

    def _kit(status):
        (run / "status.json").write_text(json.dumps(status, ensure_ascii=False),
                                         encoding="utf-8")
        return json.loads(tp.build_package(run)["manifest.json"]).get("kit")

    # Проприетарный с согласием: круг «any» по признаку, основание едет.
    assert _kit({"state": "извлечено", "kit": {
        "data_class": "proprietary", "llm_scope": "any",
        "llm_scope_basis": "согласие заказчика №7", "extra": 1}}) == {
            "data_class": "proprietary", "llm_scope": "any",
            "llm_scope_source": "признак комплекта",
            "llm_scope_basis": "согласие заказчика №7"}
    # Находка COSMOS 04.09: синтетический БЕЗ явного признака — в пакете
    # ДЕЙСТВУЮЩИЙ круг «any» (сырое поле статуса пусто), не null.
    assert _kit({"state": "извлечено", "kit": {"data_class": "synthetic",
                                              "llm_scope": None}}) == {
        "data_class": "synthetic", "llm_scope": "any",
        "llm_scope_source": "умолчание по классу данных", "llm_scope_basis": None}
    # Проприетарный без согласия и вовсе без признака — только локальные.
    assert _kit({"state": "извлечено", "kit": {"data_class": "proprietary"}})[
        "llm_scope"] == "local-only"
    assert _kit({"state": "извлечено"}) == {
        "data_class": None, "llm_scope": "local-only",
        "llm_scope_source": "умолчание без признака", "llm_scope_basis": None}
    # Явный признак вне перечня (рука, старый код) — не умолчание
    # класса «any», а безопасный local-only с названным источником.
    assert _kit({"state": "извлечено", "kit": {"data_class": "open",
                                              "llm_scope": "cloud"}}) == {
        "data_class": "open", "llm_scope": "local-only",
        "llm_scope_source": "признак вне перечня", "llm_scope_basis": None}
    pkg = tmp_path / "kit.zip"
    pkg.write_bytes(tp.write_zip(tp.build_package(run)))
    assert gate.check(pkg)[0] == []
    # Подделка ключа ловится гейтом: круг вне перечня, проприетарный
    # any без основания, кривой источник.
    for bad_kit in ({"data_class": "open", "llm_scope": "cloud",
                     "llm_scope_source": "признак комплекта"},
                    {"data_class": "proprietary", "llm_scope": "any",
                     "llm_scope_source": "признак комплекта"},
                    {"data_class": "synthetic", "llm_scope": "any",
                     "llm_scope_source": "так захотелось"}):
        bad = tp.build_package(run)
        mf = json.loads(bad["manifest.json"])
        mf["kit"] = bad_kit
        bad["manifest.json"] = json.dumps(mf, ensure_ascii=False).encode("utf-8")
        pkg2 = tmp_path / "kit-bad.zip"
        pkg2.write_bytes(tp.write_zip(bad))
        problems, _ = gate.check(pkg2)
        assert any("kit" in p for p in problems), (bad_kit, problems)
    (run / "status.json").write_text("{кривой json", encoding="utf-8")
    assert "kit" not in json.loads(tp.build_package(run)["manifest.json"])


def _data_dirs(run, sheets):
    """data-freeze + data-report под clean-data: {лист: строки}."""
    (run / "data-freeze").mkdir()
    (run / "data-report").mkdir()
    dataset = {"tables": {"D1": {
        name: {"columns": cols, "rows": rows}
        for name, (cols, rows) in sheets.items()}}}
    (run / "data-freeze" / "dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    (run / "data-report" / "data-report.json").write_text(
        json.dumps({"findings": [], "checks": []},
                   ensure_ascii=False), encoding="utf-8")


_FULL_SHEETS = {
    "Рейсы": (["id"], [{"id": "F1"}]),
    "Экипажи": (["emp"], [{"emp": "E1"}]),
    "Отпуска": (["emp", "day"], []),   # пустой лист = раздел заявлен
}


def test_readiness_matrix_ladder_states(tmp_path):
    """Ревизия 1.12 (директива владельца 2026-08-29): матрица
    готовности в манифесте; состояние — детерминированная функция
    оснований. Полные данные: пейринг с критерием = optimization,
    ростеринг с подтверждённым отказом от оптимизации =
    feasible_soft. Без листа недоступностей столбец ростеринга падает
    в stop (fail-closed по данным), пейринг не задет. Совсем без
    данных оба столбца в stop."""
    doc = _ready_doc()
    run = _run_dir(tmp_path, doc)
    _data_dirs(run, _FULL_SHEETS)
    files = tp.build_package(run)
    ladder = json.loads(files["manifest.json"])["readiness_matrix"]
    cols = ladder["columns"]
    assert ladder["form_version"] == 1
    assert cols["pairing"]["state"] == "optimization"
    assert cols["pairing"]["grounds"]["criterion"] == {
        "defined": True, "source_rule": "O-1"}
    assert cols["rostering"]["state"] == "feasible_soft"
    assert cols["rostering"]["grounds"]["data_declared"][
        "complete"] is True
    # Гейт зелёный на честном пакете с данными.
    pkg = tmp_path / "p-full.zip"
    pkg.write_bytes(tp.write_zip(files))
    assert gate.check(pkg)[0] == []
    # Без недоступностей: ростеринг падает в stop, пейринг цел.
    run2 = _run_dir(tmp_path / "b", doc)
    _data_dirs(run2, {k: v for k, v in _FULL_SHEETS.items()
                      if k != "Отпуска"})
    cols2 = json.loads(tp.build_package(run2)["manifest.json"])[
        "readiness_matrix"]["columns"]
    assert cols2["pairing"]["state"] == "optimization"
    assert cols2["rostering"]["state"] == "stop"
    assert cols2["rostering"]["grounds"]["data_declared"][
        "missing_fields"] == ["недоступности"]
    # Совсем без данных: оба столбца stop (пакет без набора не
    # авторизует запуск).
    cols3 = json.loads(tp.build_package(
        _run_dir(tmp_path / "c", doc))["manifest.json"])[
        "readiness_matrix"]["columns"]
    assert cols3["pairing"]["state"] == "stop"
    assert cols3["rostering"]["state"] == "stop"


def test_readiness_ladder_hard_and_stop_and_strict(tmp_path):
    """Лестница вниз: мягкая строка без веса опускает пейринг до
    feasible_hard (вето 1.9 наследуется); открытый вопрос по жёсткой
    строке — stop с open_blocking. Временный строгий ответ (маркер
    «ВРЕМЕННЫЙ ОТВЕТ») состояние не понижает, но объявляется
    числом."""
    doc = _ready_doc()
    doc["rules"][1]["statement"] = "штраф за ночь вне базы"  # вес снят
    run = _run_dir(tmp_path, doc)
    _data_dirs(run, _FULL_SHEETS)
    cols = json.loads(tp.build_package(run)["manifest.json"])[
        "readiness_matrix"]["columns"]
    assert cols["pairing"]["state"] == "feasible_hard"
    # Открытый вопрос по жёсткой строке зоны — stop.
    doc2 = _ready_doc()
    doc2["questions"].append(
        {"id": "Q-1-x", "part": "1", "title": "Порог",
         "text": "какой предел?", "answer_format": {"type": "text"},
         "rule_refs": ["R-H"],
         "source_refs": [{"sid": "S1", "anchor": "п.1"}]})
    ladder2 = tp.readiness_ladder(doc2, {"tables": {"D1": {
        name: {"columns": c, "rows": r}
        for name, (c, r) in _FULL_SHEETS.items()}}})
    p2 = ladder2["columns"]["pairing"]
    assert p2["state"] == "stop"
    assert p2["grounds"]["questions"]["open_blocking"] >= 1
    # Временный строгий ответ: применён, состояние цело, число = 1.
    doc3 = _ready_doc()
    doc3["questions"].append(
        {"id": "Q-3a-условия-x", "part": "3a", "title": "Условие",
         "text": "т", "answer_format": {"type": "text"},
         "rule_refs": ["R-S"],
         "source_refs": [{"sid": "S1", "anchor": "п.1"}],
         "answer": {"raw": "ВРЕМЕННЫЙ ОТВЕТ ДО ОТВЕТА АВИАКОМПАНИИ: "
                           "строгая трактовка", "status": "применён",
                    "revision": 3}})
    ladder3 = tp.readiness_ladder(doc3, {"tables": {"D1": {
        name: {"columns": c, "rows": r}
        for name, (c, r) in _FULL_SHEETS.items()}}})
    p3 = ladder3["columns"]["pairing"]
    assert p3["state"] == "optimization"
    assert p3["grounds"]["questions"]["strict_interim_answers"] == 1


def test_gate_catches_readiness_matrix_counterfeit(tmp_path):
    """Противниковый случай директивы: состояние в манифесте
    подделано (stop ростеринга поднят до optimization рукой) — гейт
    красный, «состояние готовности не машинная правда»."""
    run = _run_dir(tmp_path, _ready_doc())
    _data_dirs(run, {k: v for k, v in _FULL_SHEETS.items()
                     if k != "Отпуска"})
    files = tp.build_package(run)
    mf = json.loads(files["manifest.json"])
    assert mf["readiness_matrix"]["columns"]["rostering"][
        "state"] == "stop"
    mf["readiness_matrix"]["columns"]["rostering"][
        "state"] = "optimization"
    files["manifest.json"] = json.dumps(
        mf, ensure_ascii=False).encode("utf-8")
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(pkg)
    assert any("не машинная правда" in p for p in problems)


def _rule_maps_carrier(tmp_path, monkeypatch, customers=("Т",)):
    import json as _j
    p = tmp_path / "rule-maps.json"
    p.write_text(_j.dumps({
        "form_version": 1, "customers": list(customers),
        "_comment": "синтетика",
        "crew_size_rules": ["R-X1"],
        "rule_to_constraint": {"R-X2": "Synthetic constraint"}},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_RULE_MAPS", str(p))
    return p


def test_rule_maps_section_copied_from_carrier(tmp_path, monkeypatch):
    """Ревизия 1.14 (пакет Б): секция rule_maps копируется из носителя
    контура при совпадении customer_substring; чужой носитель секцию
    не кладёт; без носителя секции нет (изоляция набора)."""
    _rule_maps_carrier(tmp_path, monkeypatch)
    run = _run_dir(tmp_path, _ready_doc())
    files = tp.build_package(run)
    mf = json.loads(files["manifest.json"])
    assert mf["rule_maps"] == {
        "crew_size_rules": ["R-X1"],
        "rule_to_constraint": {"R-X2": "Synthetic constraint"}}
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(files))
    assert gate.check(pkg)[0] == []
    # Чужой контур: точного совпадения customer нет — секции нет.
    _rule_maps_carrier(tmp_path, monkeypatch, customers=("ЧУЖОЙ",))
    run2 = _run_dir(tmp_path / "b", _ready_doc())
    mf2 = json.loads(tp.build_package(run2)["manifest.json"])
    assert "rule_maps" not in mf2


def test_rule_maps_counterfeit_and_unknown_section(tmp_path,
                                                   monkeypatch):
    """Подделка карты ловится сверкой с носителем; неизвестная
    подсекция — красная формой."""
    _rule_maps_carrier(tmp_path, monkeypatch)
    run = _run_dir(tmp_path, _ready_doc())
    files = tp.build_package(run)
    mf = json.loads(files["manifest.json"])
    mf["rule_maps"]["crew_size_rules"] = ["R-ПОДДЕЛКА"]
    files["manifest.json"] = json.dumps(
        mf, ensure_ascii=False).encode("utf-8")
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(pkg)
    assert any("не машинная правда" in p and "rule_maps" in p
               for p in problems)
    mf["rule_maps"] = {"neizvestnaya": {}}
    files["manifest.json"] = json.dumps(
        mf, ensure_ascii=False).encode("utf-8")
    pkg2 = tmp_path / "p2.zip"
    pkg2.write_bytes(tp.write_zip(files))
    problems2, _ = gate.check(pkg2)
    assert any("неизвестная подсекция" in p for p in problems2)


def test_rule_maps_семейство_носителей_и_двусмысленность(tmp_path,
                                                         monkeypatch):
    """Дефект 2 репетиции демо: по носителю на мир, выбор — точным
    совпадением meta.customer; заказчик в двух носителях —
    двусмысленность, честный отказ сборки."""
    import json as _j
    root = tmp_path / "data-root"
    root.mkdir()
    monkeypatch.setenv("CREW_DATA_DIR", str(root))
    monkeypatch.delenv("CDPM_RULE_MAPS", raising=False)
    (root / "cdpm-rule-maps.json").write_text(_j.dumps({
        "form_version": 1, "customers": ["Мир-А"],
        "rule_to_constraint": {"R-A": "CA"}}, ensure_ascii=False),
        encoding="utf-8")
    (root / "cdpm-rule-maps-b.json").write_text(_j.dumps({
        "form_version": 1, "customers": ["Т"],
        "rule_to_constraint": {"R-B": "CB"}}, ensure_ascii=False),
        encoding="utf-8")
    run = _run_dir(tmp_path, _ready_doc())
    mf = json.loads(tp.build_package(run)["manifest.json"])
    assert mf["rule_maps"] == {"rule_to_constraint": {"R-B": "CB"}}, (
        "выбран носитель своего мира из семейства")
    # Дубль заказчика в двух носителях — отказ.
    (root / "cdpm-rule-maps-b2.json").write_text(_j.dumps({
        "form_version": 1, "customers": ["Т"],
        "rule_to_constraint": {"R-C": "CC"}}, ensure_ascii=False),
        encoding="utf-8")
    import pytest as _pytest
    with _pytest.raises(tp.TaskPackageError, match="НЕСКОЛЬКИХ"):
        tp.build_package(_run_dir(tmp_path / "c", _ready_doc()))


def test_manifest_carries_made_by(tmp_path):
    """Дополнение к 1.18 (слова владельца 07.09.2026, форма согласована с
    COSMOS): необязательный ключ made_by — «чем сделан пакет»: коммит
    панели, версия спецификации, прогон и дата его рождения, модели
    ролей формализации по журналу каналов (extraction / processor /
    auditor / arbiter), версии инструментов проверок, входы по
    отпечаткам. Без статуса ключа нет; метки времени экспорта нет
    (Ф-1.7) — повторный экспорт байт-идентичен."""
    from cdpm import run_passport as rp
    run = _run_dir(tmp_path, _ready_doc())
    assert "made_by" not in json.loads(tp.build_package(run)["manifest.json"])
    status = {
        "state": "ответы применены", "created_at": "2026-09-07T09:00:00",
        "kit": {"data_class": "synthetic"},
        "passport": {"environment": rp.environment(),
                     "at": "2026-09-07T09:00:00"},
        "stage_channels": {
            "extractor/cloud": {"config_source": "site-default",
                                "effective": {"provider": "cloud",
                                              "model": "claude-sonnet-5",
                                              "channel": "api"},
                                "stage": "извлечение", "code": "abc1234"},
            "replier/cloud": {"config_source": "site-default",
                              "effective": {"provider": "cloud",
                                            "model": "claude-sonnet-5",
                                            "channel": "api"},
                              "effort": "high", "temperature": 0.2,
                              "stage": "аудит-цикл"},
            "auditor/cloud": {"config_source": "run-explicit",
                              "effective": {"provider": "openai",
                                            "model": "gpt-5.6-terra",
                                            "channel": "api"},
                              "stage": "аудит-цикл"},
            "smt_translator/cloud": {"effective": {"provider": "cloud",
                                                   "model": "x",
                                                   "channel": "api"}},
        }}
    (run / "status.json").write_text(json.dumps(status, ensure_ascii=False),
                                     encoding="utf-8")
    m1 = tp.build_package(run)["manifest.json"]
    made = json.loads(m1)["made_by"]
    env = rp.environment()
    assert made["product"] == "CDPM" and made["run"] == "run-9001"
    assert made["code"] == env["code"]["commit"]
    assert made["spec_version"] == tp.SPEC_VERSION
    assert made["run_created"] == "2026-09-07T09:00:00"
    assert "created" not in made                       # Ф-1.7
    assert made["tools"] == env["packages"]
    assert "catalog" not in made                       # место — manifest.catalog
    assert [(x["role"], x["model"]) for x in made["models"]] == [
        ("extraction", "claude-sonnet-5"), ("processor", "claude-sonnet-5"),
        ("auditor", "gpt-5.6-terra")]                  # переводчик SMT — не роль формализации
    proc = made["models"][1]
    assert proc["effort"] == "high" and proc["temperature"] == 0.2
    assert proc["channel"] == "api" and proc["source"] == "site-default"
    # Заморозки в этой фикстуре нет — входы честно пусты.
    assert made["inputs"] == {"rules": [], "data": [], "answers": None}
    # Повторный экспорт — байт в байт (метки времени экспорта нет).
    assert tp.build_package(run)["manifest.json"] == m1
    # Входы по отпечаткам — из манифеста заморозки документов и заморозки
    # данных (тот же код, что паспорт прогона в панели).
    run2 = tmp_path / "run-9002"
    (run2 / "freeze").mkdir(parents=True)
    (run2 / "freeze" / "manifest.json").write_text(json.dumps({"files": [
        {"sid": "S1", "file": "a.md", "sha256": "0" * 64, "clause_count": 1}]}),
        encoding="utf-8")
    (run2 / "data-freeze").mkdir()
    (run2 / "data-freeze" / "manifest.json").write_text(json.dumps({"files": [
        {"sid": "D1", "file": "d.xlsx", "sha256": "1" * 64, "rows_seen": 5}]}),
        encoding="utf-8")
    made2 = rp.made_by_of(run2, status)
    assert made2["run"] == "run-9002"
    assert made2["inputs"] == {"rules": [{"name": "a.md", "sha256": "0" * 64}],
                               "data": [{"name": "d.xlsx", "sha256": "1" * 64}],
                               "answers": None}
    # Кривой статус — без ключа, не падение сборки.
    (run / "status.json").write_text("{не json", encoding="utf-8")
    assert "made_by" not in json.loads(tp.build_package(run)["manifest.json"])


def _fit(rev):
    """Посадка A на семью пределов смены с названной ревизией базы."""
    return {"category": "A", "registry_rev": rev, "family": "duty_limits",
            "params": {"max_duty_duration": {"value": 720,
                                             "quote": "не более 12:00"}}}


#: Ревизия базы шаблонов до переезда на каталог (12 hex) — «прежний
#: каталог» в сводке посадок.
_OLD_REV = "bc78f37804ec"


def test_manifest_carries_catalog_and_fits_summary(tmp_path, monkeypatch):
    """Дополнение к 1.18 (второе, 07.09.2026; шаг 3 переезда на каталог
    шаблонов): манифест несёт необязательный ключ `catalog` — версию и
    отпечаток каталога, КОТОРЫМ пакет выпущен, — и сводку единства
    ревизий посадок `fits_catalog`. Сводка — предупреждение, не отказ:
    пакет с посадками прежнего каталога выпускается и проходит гейт.
    Посадок нет — сводки нет. Меток времени в ключах нет (Ф-1.7):
    повторный экспорт байт-идентичен."""
    from cdpm import catalog_bridge as bridge
    stamp = bridge.stamp()
    assert stamp, "каталог шаблонов обязан быть в дереве репозитория"

    # Пакет без посадок: каталог объявлен, судить единство нечего.
    run = _run_dir(tmp_path / "a", _ready_doc())
    man = json.loads(tp.build_package(run)["manifest.json"])
    assert man["catalog"] == stamp
    assert set(man["catalog"]) == {"version", "fingerprint"}
    assert re.fullmatch(r"[0-9a-f]{64}", man["catalog"]["fingerprint"])
    assert man["catalog"]["version"]
    assert "fits_catalog" not in man

    # Посадки тем же каталогом: одна ревизия, она же отпечаток пакета —
    # сводка без слов, гейт зелёный, повторный экспорт байт в байт.
    doc = _ready_doc()
    doc["rules"][0]["template_fit"] = _fit(stamp["fingerprint"])
    doc["rules"][1]["template_fit"] = _fit(stamp["fingerprint"])
    run2 = _run_dir(tmp_path / "b", doc)
    files = tp.build_package(run2)
    assert json.loads(files["manifest.json"])["fits_catalog"] == {
        "revs": [stamp["fingerprint"]], "uniform": True,
        "matches_catalog": True, "note": None}
    pkg = tmp_path / "fits.zip"
    pkg.write_bytes(tp.write_zip(files))
    assert gate.check(pkg)[0] == []
    assert tp.build_package(run2)["manifest.json"] == files["manifest.json"]

    # Посадки прежнего каталога (12 hex до переезда): единство есть,
    # совпадения с каталогом пакета нет — предупреждение словами.
    doc3 = _ready_doc()
    doc3["rules"][0]["template_fit"] = _fit(_OLD_REV)
    run3 = _run_dir(tmp_path / "c", doc3)
    files3 = tp.build_package(run3)
    fits3 = json.loads(files3["manifest.json"])["fits_catalog"]
    assert fits3["revs"] == [_OLD_REV] and fits3["uniform"] is True
    assert fits3["matches_catalog"] is False
    assert "прежнему каталогу" in fits3["note"] and _OLD_REV in fits3["note"]
    pkg3 = tmp_path / "fits-old.zip"
    pkg3.write_bytes(tp.write_zip(files3))
    assert gate.check(pkg3)[0] == []          # предупреждение, не отказ

    # Смешанные ревизии: единства нет — сводка называет все ревизии.
    doc4 = _ready_doc()
    doc4["rules"][0]["template_fit"] = _fit(stamp["fingerprint"])
    doc4["rules"][1]["template_fit"] = _fit(_OLD_REV)
    run4 = _run_dir(tmp_path / "d", doc4)
    fits4 = json.loads(tp.build_package(run4)["manifest.json"])["fits_catalog"]
    assert fits4["revs"] == sorted([stamp["fingerprint"], _OLD_REV])
    assert fits4["uniform"] is False and fits4["matches_catalog"] is False
    assert "разных ревизий" in fits4["note"]

    # Шаг 7 переезда (§0 «Режим среза»): переменная среза базы
    # CREW_VMP_TEMPLATE_BASE продуктом CDPM больше НЕ ЧИТАЕТСЯ — ни
    # уважается, ни отвергается (переходное правило до части COSMOS
    # 7-Б). Отметка манифеста всегда у каталога, которым выпущен пакет.
    slice_path = tmp_path / "NSH-срез.json"
    slice_path.write_text(json.dumps(
        {"registry_rev": "0ce7a9570d66", "catalog_version": "0.1.0-срез",
         "catalog_fingerprint": "a" * 64}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setenv("CREW_VMP_TEMPLATE_BASE", str(slice_path))
    assert tp.catalog_stamp_of() == stamp


def test_gate_judges_catalog_form_and_fits_truth(tmp_path):
    """Гейт пакета (Дополнение к 1.18, второе): присутствующий ключ
    `catalog` судится формой (ровно version и fingerprint, отпечаток —
    64 hex), сводка `fits_catalog` — ещё и правдой: ревизии посадок
    пересчитываются по строкам model.json пакета. Пакет без обоих
    ключей (выпущенный до каталога) законен — прежний закон."""
    from cdpm import catalog_bridge as bridge
    stamp = bridge.stamp()
    doc = _ready_doc()
    doc["rules"][0]["template_fit"] = _fit(stamp["fingerprint"])
    run = _run_dir(tmp_path, doc)

    def _judged(mutate):
        files = tp.build_package(run)
        mf = json.loads(files["manifest.json"])
        mutate(mf)
        files["manifest.json"] = json.dumps(
            mf, ensure_ascii=False).encode("utf-8")
        pkg = tmp_path / "cat.zip"
        pkg.write_bytes(tp.write_zip(files))
        return gate.check(pkg)[0]

    assert _judged(lambda mf: None) == []
    # Форма ключа каталога: отпечаток не 64 hex, пустая версия, лишнее
    # поле внутри (потребитель судит форму этого ключа буквально).
    assert any("catalog.fingerprint" in p for p in
               _judged(lambda mf: mf["catalog"].update(fingerprint=_OLD_REV)))
    assert any("catalog.version" in p for p in
               _judged(lambda mf: mf["catalog"].update(version="")))
    assert any("manifest.catalog не объект" in p for p in
               _judged(lambda mf: mf["catalog"].update(source="рука")))
    # Форма сводки: единство — да/нет, а не слова.
    assert any("fits_catalog.uniform" in p for p in
               _judged(lambda mf: mf["fits_catalog"].update(uniform="да")))
    # Правда сводки: подделка ревизий ловится пересчётом по строкам.
    assert any("fits_catalog не сходится" in p for p in _judged(
        lambda mf: mf["fits_catalog"].update(revs=["0" * 64],
                                             matches_catalog=True)))
    # Пакет без обоих ключей — законен (прежний закон «каталог не
    # объявлен»): гейт молчит, а не требует ключей задним числом.
    assert _judged(lambda mf: (mf.pop("catalog"),
                               mf.pop("fits_catalog"))) == []


def test_zip_is_content_addressed(tmp_path, monkeypatch):
    """Ф-1.7 доведено до архива (живая проверка 07.09.2026): sha256 zip —
    по содержанию, время записей архива постоянное; два нажатия «Передать
    в COSMOS» с одним содержанием дают один отпечаток независимо от часов."""
    import time as _time
    run = _run_dir(tmp_path, _ready_doc())
    z1 = tp.export_zip(run)
    real = _time.localtime
    monkeypatch.setattr(_time, "localtime",
                        lambda *a: real(real().__class__ and 1_800_000_000))
    z2 = tp.export_zip(run)
    assert z1 == z2
    with zipfile.ZipFile(io.BytesIO(z1)) as z:
        assert z.namelist() == sorted(z.namelist())
        assert {i.date_time for i in z.infolist()} == {tp.ZIP_DATE_TIME}
        assert {i.compress_type for i in z.infolist()} == {zipfile.ZIP_DEFLATED}


def test_child_manifest_carries_source_run_name(tmp_path):
    """Правило 2.15 (живой урок Р3): у потомка «повтора из журнала»
    манифест несёт имя прогона-источника (как и made_by) — пакет байт в
    байт; у потомка «заново» и у первичного прогона — своё имя."""
    run = _run_dir(tmp_path, _ready_doc())               # каталог run-9001
    assert json.loads(tp.build_package(run)["manifest.json"])["source_run"] == "run-9001"
    (run / "status.json").write_text(json.dumps({
        "state": "извлечено", "kit": {"data_class": "synthetic"},
        "passport": {"replay": {"mode": "replay-journal",
                                "source_run": "run-0088"}}}), encoding="utf-8")
    assert tp.package_run_name(run) == "run-0088"
    assert json.loads(tp.build_package(run)["manifest.json"])["source_run"] == "run-0088"
    (run / "status.json").write_text(json.dumps({
        "state": "извлечено", "kit": {"data_class": "synthetic"},
        "passport": {"replay": {"mode": "replay-fresh",
                                "source_run": "run-0088"}}}), encoding="utf-8")
    assert json.loads(tp.build_package(run)["manifest.json"])["source_run"] == "run-9001"


# ── Рубеж непротиворечивости выпуска (СМТ-1, слово владельца
# 11.09.2026: «давай реализуем оба предложения») ──────────────────────
# Пакет постановки не выпускается без свежего машинного вердикта о
# совместности жёстких правил: вердикт берётся из отчёта прогона, пока
# он о том же своде, иначе рубеж проверяет свод сам. Подменяется ровно
# одно — переводчик правил в SMT; решатель Z3 настоящий.

def _hard_pair_doc():
    """Свод с ДВУМЯ жёсткими правилами — на них видно и ядро
    конфликта, и счёт переведённых утверждений."""
    doc = _ready_doc()
    doc["rules"].append(
        {"id": "R-H2", "title": "Жёсткое второе", "statement": "x ≥ 2",
         "zone": "BOTH", "kind": "HARD",
         "sources": [{"sid": "S1", "anchor": "п.1"}]})
    return doc


def _translator(calls, asserts):
    """Подменный переводчик: считает обращения и отдаёт заданные
    утверждения (отпечатка канала у него нет — манифест честно пишет
    провайдера и модель пустыми)."""
    def fn(prompt):
        calls.append(prompt)
        return json.dumps({"decls": ["(declare-fun x () Int)"],
                           "asserts": asserts}, ensure_ascii=False)
    return fn


def _put_translator(monkeypatch, calls, asserts):
    monkeypatch.setattr(tp, "smt_channel_factory",
                        lambda run_dir: _translator(calls, asserts))


def test_package_declares_consistency_verdict(tmp_path, monkeypatch):
    """Манифест несёт вердикт о непротиворечивости свода, которым
    выпущен пакет; отчёт проверки остаётся в прогоне; гейт
    производителя судит форму ключа и зелен."""
    calls: list = []
    _put_translator(monkeypatch, calls, [
        {"rule": "R-H", "smt": "(<= x 1)"},
        {"rule": "R-H2", "smt": "(>= x 0)"}])
    run = _run_dir(tmp_path, _hard_pair_doc())
    canon = (run / "extract" / "model.json").read_bytes()
    body = tp.export_zip(run)
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(body)
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        manifest = json.loads(z.read("manifest.json"))
    from cdpm import smt_verify as sv
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        exported = json.loads(z.read("model.json"))
    assert manifest["consistency"] == {
        "verdict": "sat",
        "model_sha256": hashlib.sha256(canon).hexdigest(),
        "n_rules": 2, "n_translated": 2,
        "translator": {"role": "smt_translator", "provider": None,
                       "model": None},
        "report": "extract/smt-report.json",
        # 12.09.2026 (A01-F1): предмет проверки — отпечаток жёстких правил
        # ВЫПУСКАЕМОГО model.json и слово «canonical» (сборка предмет не
        # изменила — вердикт отчётом кнопки).
        "subject_sha256": sv.subject_fingerprint(exported),
        "subject": "canonical"}
    # Отчёт — в прогоне (проверяем глазами) И копией в пакете
    # (A01-F1, 12.09.2026): гейт производителя сверяет его строки с
    # жёсткими правилами model.json пакета, не доверяя счётчикам.
    report_bytes = (run / "extract" / "smt-report.json").read_bytes()
    report = json.loads(report_bytes.decode("utf-8"))
    assert report["verdict"] == "sat" and report["checked_at"]
    assert "smt-report.json" in [f["name"] for f in manifest["files"]]
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        assert z.read("smt-report.json") == report_bytes
    assert len(calls) == 1
    problems, counters = gate.check(pkg)
    assert problems == [] and counters["consistency"] == "sat"


def test_fresh_report_is_reused_and_manifest_stays_byte_identical(
        tmp_path, monkeypatch):
    """Свежий отчёт (тот же отпечаток свода) — вердикт берётся из него:
    второе нажатие кнопки модель не зовёт, и манифест байт в байт
    прежний (Ф-1.7)."""
    calls: list = []
    _put_translator(monkeypatch, calls, [{"rule": "R-H", "smt": "(<= x 1)"}])
    run = _run_dir(tmp_path, _ready_doc())
    first = tp.build_package(run)["manifest.json"]
    assert len(calls) == 1
    assert tp.build_package(run)["manifest.json"] == first
    assert len(calls) == 1


def test_stale_report_is_rechecked_before_release(tmp_path, monkeypatch):
    """Отчёт о ПРЕЖНЕМ своде вердиктом не считается: рубеж проверяет
    свод заново и переписывает отчёт отпечатком нового свода."""
    calls: list = []
    _put_translator(monkeypatch, calls, [{"rule": "R-H", "smt": "(<= x 1)"}])
    run = _run_dir(tmp_path, _ready_doc())
    (run / "extract" / "smt-report.json").write_text(json.dumps(
        {"verdict": "sat", "core": [], "n_rules": 1, "n_translated": 1,
         "model_sha256": "0" * 64}), encoding="utf-8")
    canon = (run / "extract" / "model.json").read_bytes()
    man = json.loads(tp.build_package(run)["manifest.json"])
    assert len(calls) == 1
    assert man["consistency"]["model_sha256"] == \
        hashlib.sha256(canon).hexdigest()
    assert json.loads((run / "extract" / "smt-report.json")
                      .read_text(encoding="utf-8"))["model_sha256"] \
        == man["consistency"]["model_sha256"]


def test_report_without_fingerprint_is_not_a_verdict(tmp_path, monkeypatch):
    """Отчёт, сделанный до 11.09.2026 (без отпечатка свода), свежим не
    считается — неизвестно, о каком своде его вердикт."""
    calls: list = []
    _put_translator(monkeypatch, calls, [{"rule": "R-H", "smt": "(<= x 1)"}])
    run = _run_dir(tmp_path, _ready_doc())
    (run / "extract" / "smt-report.json").write_text(
        json.dumps({"verdict": "sat", "core": []}), encoding="utf-8")
    tp.build_package(run)
    assert len(calls) == 1


# ── Рубеж у потомка повтора: вердикт источника вместо вызова модели ───
# У потомка ответы моделей берутся ИЗ ЖУРНАЛА источника, и запроса
# переводчика SMT там нет — вызов дал бы ReplayMismatch. Своды потомка и
# источника байт в байт равны (Ф-1.7), поэтому вердикт источника о том
# же отпечатке есть вердикт и о своде потомка.

def _replay_pair(tmp_path, doc, with_source_dir=False):
    """Источник и потомок повтора «из журнала» рядом в корне прогонов
    (корень — их общий родитель): свод один байт в байт, у потомка —
    паспорт повтора. `with_source_dir` — паспорт с путём источника (как
    у живого повтора) либо только с именем (поиск соседа по имени)."""
    source, child = tmp_path / "run-9001", tmp_path / "run-9002"
    body = json.dumps(doc, ensure_ascii=False)
    for run in (source, child):
        (run / "extract").mkdir(parents=True)
        (run / "extract" / "model.json").write_text(body, encoding="utf-8")
        (run / "extract" / "svod.md").write_text("# свод\n",
                                                 encoding="utf-8")
    rep = {"mode": "replay-journal", "source_run": source.name}
    if with_source_dir:
        rep["source_dir"] = str(source)
    (child / "status.json").write_text(
        json.dumps({"passport": {"replay": rep}}, ensure_ascii=False),
        encoding="utf-8")
    return source, child


def _source_report(source, sha):
    """Свежий отчёт источника о своде с отпечатком `sha`."""
    from cdpm import smt_verify as sv
    report = {"status": "непротиворечиво (все жёсткие правила переведены, "
                        "утверждения совместны)",
              # 12.09.2026 (A01): свежий отчёт — в действующей форме.
              "schema_version": "smt-report/2",
              # 12.09.2026 (A01-F1): отчёт называет СВОЙ предмет —
              # иначе он не свеж и рубеж проверяет свод сам.
              "subject_sha256": sv.subject_fingerprint(_ready_doc()),
              "verdict": "sat", "core": [], "n_rules": 1,
              "n_translated": 1, "n_untranslatable": 0, "untranslated": [],
              "translations": [{"rule": "R-H", "status": "translated",
                                "smt": "(<= x 1)"}],
              "model_sha256": sha, "checked_at": "2026-09-11T00:00:00+03:00",
              "translator": {"role": "smt_translator", "provider": "cloud",
                             "model": "claude-sonnet-5"}}
    (source / "extract" / "smt-report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return report


def test_replay_child_takes_verdict_from_source_report(tmp_path,
                                                       monkeypatch):
    """У потомка повтора своего отчёта нет, у источника — свежий о том
    же своде: пакет выпускается, модель НЕ зовётся, отчёт источника
    лежит у потомка, манифест несёт его вердикт."""
    calls: list = []
    _put_translator(monkeypatch, calls, [{"rule": "R-H", "smt": "(<= x 1)"}])
    source, child = _replay_pair(tmp_path, _ready_doc())
    canon = (child / "extract" / "model.json").read_bytes()
    sha = hashlib.sha256(canon).hexdigest()
    # Ф-1.7: свод потомка байт в байт исходный — отпечаток один.
    assert hashlib.sha256(
        (source / "extract" / "model.json").read_bytes()).hexdigest() == sha
    report = _source_report(source, sha)
    man = json.loads(tp.build_package(child)["manifest.json"])
    assert calls == []                       # переводчика не звали
    from cdpm import smt_verify as sv
    assert man["consistency"] == {
        "verdict": "sat", "model_sha256": sha,
        "n_rules": 1, "n_translated": 1,
        "translator": {"role": "smt_translator", "provider": "cloud",
                       "model": "claude-sonnet-5"},
        "report": "extract/smt-report.json",
        # 12.09.2026 (A01-F1): предмет проверки выпускаемого свода.
        "subject_sha256": sv.subject_fingerprint(_ready_doc()),
        "subject": "canonical"}
    # Отчёт скопирован в потомка: ключ `report` манифеста — путь в
    # СВОЁМ прогоне, и вердикт проверяем глазами там же.
    assert json.loads((child / "extract" / "smt-report.json")
                      .read_text(encoding="utf-8")) == report
    # Правило 2.15: имя прогона в манифесте — имя источника.
    assert man["source_run"] == "run-9001"
    # Второй выпуск читает уже свой отчёт — модель по-прежнему молчит.
    assert json.loads(tp.build_package(child)["manifest.json"]) == man
    assert calls == []


def test_stale_source_report_falls_back_to_translator(tmp_path,
                                                      monkeypatch):
    """Отчёт источника о ПРЕЖНЕМ своде вердиктом о своде потомка не
    считается: рубеж идёт прежним путём — зовёт переводчика и пишет
    свой отчёт с отпечатком своего свода."""
    calls: list = []
    _put_translator(monkeypatch, calls, [{"rule": "R-H", "smt": "(<= x 1)"}])
    source, child = _replay_pair(tmp_path, _ready_doc(),
                                 with_source_dir=True)
    _source_report(source, "0" * 64)
    canon = (child / "extract" / "model.json").read_bytes()
    man = json.loads(tp.build_package(child)["manifest.json"])
    assert len(calls) == 1                   # прежний путь: вызов модели
    assert man["consistency"]["model_sha256"] == \
        hashlib.sha256(canon).hexdigest()
    assert json.loads((child / "extract" / "smt-report.json")
                      .read_text(encoding="utf-8"))["model_sha256"] \
        == man["consistency"]["model_sha256"]
    # Отчёт источника рубеж не трогает.
    assert json.loads((source / "extract" / "smt-report.json")
                      .read_text(encoding="utf-8"))["model_sha256"] \
        == "0" * 64


def test_replay_refusal_words_name_the_replay(tmp_path, monkeypatch):
    """Вердикта нет ни у потомка, ни у источника, а вызвать переводчика
    нечем: отказ называет повтор и источник — иначе слова зовут править
    свод, которого у потомка править нельзя."""
    def refuse(run_dir):
        raise RuntimeError("запроса с таким отпечатком в журнале "
                           "источника нет")
    monkeypatch.setattr(tp, "smt_channel_factory", refuse)
    _, child = _replay_pair(tmp_path, _ready_doc())
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(child)
    assert e.value.code == "smt-unavailable"
    words = str(e.value)
    assert "повтор прогона run-9001" in words
    assert "В ИСТОЧНИКЕ" in words


def test_contradiction_stops_release_with_core_and_code(tmp_path,
                                                        monkeypatch):
    """Красный вердикт останавливает выпуск словами с ядром конфликта
    и кодом `smt-contradiction`; ядро называет правила теми же
    идентификаторами, что свод."""
    calls: list = []
    _put_translator(monkeypatch, calls, [
        {"rule": "R-H", "smt": "(<= x 1)"},
        {"rule": "R-H2", "smt": "(>= x 2)"}])
    run = _run_dir(tmp_path, _hard_pair_doc())
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(run)
    words = str(e.value)
    assert e.value.code == "smt-contradiction"
    assert "R-H" in words and "R-H2" in words
    assert "противоречие правил" in words
    # Слова называют, что делать, и куда смотреть.
    assert "анкеты" in words and "smt-report.json" in words


def test_missing_solver_stops_release_fail_closed(tmp_path, monkeypatch):
    """Решателя на площадке нет — вердикта нет, пакет не выпускается
    (fail-closed) с кодом `smt-unavailable` и словами, как поставить
    z3-solver."""
    import importlib.util as _ilu
    run = _run_dir(tmp_path, _ready_doc())
    real = _ilu.find_spec

    def no_z3(name, *a, **kw):
        return None if name == "z3" else real(name, *a, **kw)
    monkeypatch.setattr(_ilu, "find_spec", no_z3)
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(run)
    assert e.value.code == "smt-unavailable"
    assert "z3-solver" in str(e.value) and "pip install" in str(e.value)


def test_channel_refusal_stops_release(tmp_path, monkeypatch):
    """Канал переводчика не построился (роль не объявлена) — отказ
    словами канала с кодом `smt-unavailable`, а не молчаливый выпуск."""
    def refuse(run_dir):
        raise RuntimeError("раскладка моделей ролей не объявлена")
    monkeypatch.setattr(tp, "smt_channel_factory", refuse)
    run = _run_dir(tmp_path, _ready_doc())
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(run)
    assert e.value.code == "smt-unavailable"
    assert "раскладка моделей ролей не объявлена" in str(e.value)
    # Переводчика нет вовсе — тот же код и свои слова.
    monkeypatch.setattr(tp, "smt_channel_factory", None)
    with pytest.raises(tp.TaskPackageError) as e2:
        tp.build_package(run)
    assert e2.value.code == "smt-unavailable"


def test_broken_translation_stops_release(tmp_path, monkeypatch):
    """Перевод дважды не прошёл парсер Z3 — вердикта нет, выпуск
    остановлен с кодом `smt-unavailable`."""
    calls: list = []
    _put_translator(monkeypatch, calls, [{"rule": "R-H", "smt": "(кривой"}])
    run = _run_dir(tmp_path, _ready_doc())
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(run)
    assert e.value.code == "smt-unavailable"
    assert "не разобрался" in str(e.value)
    assert len(calls) == 2                      # один повтор, как у кнопки


def test_empty_translation_is_not_a_verdict(tmp_path, monkeypatch):
    """Переводчик вернул ноль утверждений при жёстких правилах: «sat»
    пустого скрипта вердиктом не считается. (12.09.2026, A01: пустой
    ответ — частный случай неполного перевода: после повтора отчёт
    честно неполон, отказ `smt-incomplete` называет все правила.)"""
    calls: list = []
    _put_translator(monkeypatch, calls, [])
    run = _run_dir(tmp_path, _ready_doc())
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(run)
    assert e.value.code == "smt-incomplete"
    assert e.value.items == ["R-H"] and "R-H" in str(e.value)
    assert len(calls) == 2                      # один повтор с перечнем
    assert "переведено 0 из 1" in str(e.value)


def test_svod_without_hard_rules_declares_verdict_none(tmp_path,
                                                       monkeypatch):
    """Жёстких правил нет — проверять нечего: вердикт «none», модель
    не зовётся, выпуск идёт."""
    calls: list = []
    _put_translator(monkeypatch, calls, [])
    doc = _ready_doc()
    doc["rules"] = [r for r in doc["rules"] if r["kind"] != "HARD"]
    run = _run_dir(tmp_path, doc)
    canon = (run / "extract" / "model.json").read_bytes()
    got = tp.consistency_of(run, canon)
    assert got["verdict"] == "none" and got["n_rules"] == 0
    assert calls == []


def test_gate_reds_forged_consistency(tmp_path, monkeypatch):
    """Гейт производителя судит справку о непротиворечивости: вердикт
    вне перечня (пакет с ним рубеж не выпускает), кривая форма ключа и
    подделанный счёт — красные; пакет без ключа судится прежним
    законом."""
    calls: list = []
    _put_translator(monkeypatch, calls, [{"rule": "R-H", "smt": "(<= x 1)"}])
    run = _run_dir(tmp_path, _ready_doc())
    files = tp.build_package(run)

    def _regate(mutate):
        man = json.loads(files["manifest.json"].decode("utf-8"))
        mutate(man)
        pkg = dict(files)
        pkg["manifest.json"] = (json.dumps(man, ensure_ascii=False,
                                           indent=2) + "\n").encode("utf-8")
        out = tmp_path / "forged.zip"
        out.write_bytes(tp.write_zip(pkg))
        return gate.check(out)[0]

    assert _regate(lambda man: None) == []
    bad = _regate(lambda man: man["consistency"].__setitem__("verdict",
                                                             "unsat"))
    assert any("verdict вне перечня" in p for p in bad), bad
    bad = _regate(lambda man: man["consistency"].pop("model_sha256"))
    assert any("не объект ровно с ключами" in p for p in bad), bad
    bad = _regate(lambda man: man["consistency"].__setitem__("n_translated",
                                                             5))
    assert any("больше жёстких правил" in p for p in bad), bad
    bad = _regate(lambda man: man["consistency"]["translator"].__setitem__(
        "role", "worker"))
    assert any("не роль переводчика SMT" in p for p in bad), bad
    assert _regate(lambda man: man.pop("consistency")) == []


# ====================================================================
# Ш-8 (реестр швов CDPM ↔ COSMOS): КОНТРПРОБА ВТОРОЙ ПОЛОВИНЫ ШВА
# «код и перечень отказа рубежа выпуска читаются потребителем».
#
# Половина ПРОИЗВОДИТЕЛЯ (наша зона, `refuse_by_barriers`): законный
# отказ поддержки несёт машинный КОД `support-missing` и НЕПУСТОЙ
# перечень АДРЕСОВ точных записей каталога `families/<id>` (§12.11
# п. 3).  Половина ПОТРЕБИТЕЛЯ (зона COSMOS,
# `vmp.run.button.package_refusal_stop`): читает `.code`/`.items` и
# строит по ним заявку на вид исполнителя.
#
# Зелёная проба потребителя у COSMOS есть
# (tests/test_run_button.py::test_пробел_относительно_записи_подаётся_
# заявкой_на_вид) — она доказывает, что при ЗДОРОВОМ производителе
# потребитель работает.  Она НЕ доказывает, что половина производителя
# потребителю нужна: сними её — и оракул останется зелёным.  Ровно это
# и меряет контрпроба: половина производителя ломается в СВОЕЙ КОПИИ
# модуля (дерево не трогается), и у потребителя требуется ответ.
# ====================================================================

_ПРОИЗВОДИТЕЛЬ = Path(tp.__file__).resolve()

#: Строка половины производителя, которой отказ поддержки отдаёт КОД и
#: ПЕРЕЧЕНЬ адресов (cdpm/export_task_package.py, конец
#: `refuse_by_barriers`).  Якорь один на файл — проверяется счётом.
_ЯКОРЬ = '            SUPPORT_MISSING, [pr["item"] for pr in sup])'

#: Поломки половины производителя: {имя: строка на месте якоря}.
_ПОЛОМКИ = {
    "перечень пуст":
        '            SUPPORT_MISSING, [])',
    "имя семьи вместо адреса записи":
        '            SUPPORT_MISSING, [pr["family"] for pr in sup])',
    "кода нет":
        '            None, [pr["item"] for pr in sup])',
    "чужой код отказа":
        '            FAMILY_INCOMPLETE, [pr["item"] for pr in sup])',
    "назван адрес чужой записи":
        '            SUPPORT_MISSING, ["families/night_series"])',
    "адрес назван дважды":
        '            SUPPORT_MISSING, [pr["item"] for pr in sup] * 2)',
    "перечень неполон":
        '            SUPPORT_MISSING, [pr["item"] for pr in sup][:1])',
}

#: Посадки правил свода на семьи каталога: (правило, семья). Вторая
#: нужна там, где перечень отказа обязан нести ДВА адреса.
_ПОСАДКА_ДЕЖУРСТВА = ("R-DUTY", "duty_limits")
_ПОСАДКА_НОЧЕЙ = ("R-REST", "night_series")


def _копия_производителя(monkeypatch, tmp_path, поломка: str | None):
    """Поставить потребителю КОПИЮ модуля производителя со сломанной
    половиной шва.  Возврат — загруженный модуль.

    Мутируется ТОЛЬКО копия в tmp: файл дерева читается, его отпечаток
    берётся до и после и сверяется — писать в дерево (а значит и
    восстанавливать его `git checkout`, который берёт из ИНДЕКСА и
    молча оставляет старый код) не требуется вовсе.

    Копия садится двумя путями сразу — в `sys.modules` и атрибутом
    пакета `cdpm`: потребитель берёт модуль формой
    `from cdpm import export_task_package as tp`, а она читает сперва
    атрибут пакета.  Подменный переводчик SMT ставится КОПИИ отдельно:
    autouse-фикстура conftest патчит модуль дерева, у копии свой
    `smt_channel_factory`, и без этого выпуск звал бы живую модель.
    """
    import importlib.util
    import sys

    import _smt_gate_stub

    # Отпечаток — с НОРМАЛИЗОВАННОГО текста, не с сырых байтов дерева:
    # git разворачивает переводы строк по своим настройкам, и в одном
    # рабочем дереве файл лежит с одним концом строки, в соседнем — с
    # другим. Сырые байты сделали бы пробу зависящей от машины и
    # checkout; доказать надо другое — что копия несёт ТО ЖЕ
    # СОДЕРЖАНИЕ, что половина производителя в дереве.
    # ДВА РАЗНЫХ основания, и путать их нельзя:
    #  `было` — СЫРЫЕ байты дерева: им и только им доказывается,
    #    что дерево после подмены не тронуто;
    #  `содержание` — отпечаток НОРМАЛИЗОВАННОГО текста: им
    #    доказывается, что здоровая копия несёт то же, что дерево.
    # Сырыми байтами второе доказывать нельзя: git разворачивает
    # переводы строк по своим настройкам, и в соседнем рабочем
    # дереве та же половина производителя лежит другими байтами —
    # проба стала бы зависеть от машины и checkout.
    было = hashlib.sha256(_ПРОИЗВОДИТЕЛЬ.read_bytes()).hexdigest()
    текст = _ПРОИЗВОДИТЕЛЬ.read_text(encoding="utf-8")
    assert текст.count(_ЯКОРЬ) == 1, "якорь половины производителя не один"
    if поломка is not None:
        текст = текст.replace(_ЯКОРЬ, _ПОЛОМКИ[поломка])
    путь = tmp_path / "копия-производителя" / "export_task_package.py"
    путь.parent.mkdir(parents=True, exist_ok=True)
    # БАЙТАМИ, а не write_text: тот на Windows переводит перевод строки
    # в пару, и копия перестала бы совпадать с нормализованным текстом
    # дерева. Что ДЕРЕВО НЕ ТРОНУТО, доказывает не это равенство, а
    # сверка сырых байтов `стало == было` ниже: основания разные.
    путь.write_bytes(текст.encode("utf-8"))
    отпечаток = hashlib.sha256(путь.read_bytes()).hexdigest()
    # ВТОРОЕ, НЕЗАВИСИМОЕ чтение дерева. Прежде `содержание` считалось
    # из ТОЙ ЖЕ переменной `текст`, из которой писалась копия, и
    # равенство сравнивало запись с её же источником: подмешай в
    # `текст` чужую строку — и утверждение осталось бы зелёным
    # (померено ревью мутацией теста T-1). Читаем дерево ЗАНОВО.
    содержание = hashlib.sha256(
        _ПРОИЗВОДИТЕЛЬ.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    print("копия модуля ЗАГРУЖАЕТСЯ: %s (поломка: %s, sha256 копии %s, "
          "дерево %s sha256 %s)"
          % (путь, поломка or "нет — здоровая половина", отпечаток[:12],
             _ПРОИЗВОДИТЕЛЬ, было[:12]))
    if поломка is None:
        assert отпечаток == содержание, "копия не то же, что дерево"
    else:
        assert отпечаток != содержание, "поломка не попала в копию"

    spec = importlib.util.spec_from_file_location(
        "cdpm.export_task_package", путь)
    копия = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(копия)
    копия.smt_channel_factory = _smt_gate_stub.channel_factory
    копия.__ФАЙЛ_КОПИИ__ = str(путь)

    import cdpm
    monkeypatch.setitem(sys.modules, "cdpm.export_task_package", копия)
    monkeypatch.setattr(cdpm, "export_task_package", копия)

    стало = hashlib.sha256(_ПРОИЗВОДИТЕЛЬ.read_bytes()).hexdigest()
    assert стало == было, "файл дерева изменён — так мутировать нельзя"
    return копия


def _полная_посадка(doc: dict, rid: str, семья: str) -> dict:
    """Посадка A правила на семью с ПОЛНЫМ набором параметров.

    Перечня параметров у пробы СВОЕГО НЕТ: недостающие называет закон
    полноты (`cdpm.family_params.missing_params`) по ТОМУ ЖЕ каталогу,
    что судит барьер. Иначе проба завела бы второе место истины о
    каталоге и молча краснела бы от его роста, а не от поломки шва.

    Полнота нужна затем, что барьер полноты семьи (`family-incomplete`)
    СТАРШЕ барьера поддержки: при неполной посадке рубеж отказал бы
    другим кодом, и перечень был бы не о том (проверено: посадка
    `night_series` одним параметром даёт `family-incomplete`)."""
    from cdpm import family_params as fp
    правило = next(r for r in doc["rules"] if r["id"] == rid)
    цитата = правило["statement"]
    параметры: dict = {}
    for _ in range(20):
        нет = fp.missing_params(семья, параметры)
        if not нет:
            break
        for имя in нет:
            параметры[имя] = {"value": 12, "quote": цитата}
    else:                                        # pragma: no cover
        raise AssertionError("полная посадка семьи %s не сошлась" % семья)
    правило["template_fit"] = {"category": "A", "family": семья,
                               "registry_rev": "x" * 12, "quote": цитата,
                               "params": параметры}
    return doc


def _свод_с_посадками(посадки):
    """Свод кнопочных стендов с ПОЛНЫМИ посадками A на семьи
    каталога. Стенд свода — `test_run_button._doc`, один на обе
    стороны шва."""
    import test_run_button as trb
    doc = trb._doc()
    for rid, семья in посадки:
        doc = _полная_посадка(doc, rid, семья)
    return doc


def _нажатие_без_исполнителей(tmp_path, monkeypatch, поломка: str | None,
                              *, семьи=("duty_limits",),
                              посадки=(("R-DUTY", "duty_limits"),)):
    """Один прогон кнопки COSMOS на стенде «семья без исполнителей».
    Возврат — (выдача кнопки, {семья: виды без исполнителя}).

    Стенд ВТОРОЙ РАЗ НЕ СТРОИТСЯ: каталог без исполнителей готовит
    ОБЩИЙ помощник `catalog_copies.without_executors` — тот самый, по
    которому судит зелёную пробу потребитель (шов Ш-8 объявлен
    договором обеих сторон). Второе место истины о том же каталоге
    сделало бы контрпробу неотносимой: она краснела бы от расхождения
    стендов, а не от поломки половины производителя. Из набора кнопки
    берётся ТОЛЬКО обвязка нажатия (свод, каталог прогона, подменные
    швы решателя и сертификации) — своей у пробы тоже быть не должно.

    Путь НАСТОЯЩИЙ: `export_zip` не подменён, отказ поднимает живой
    барьер поддержки K06, который видит копию каталога без
    исполнителей. Ломается только та строка половины производителя,
    которой отказ отдаёт код и перечень.
    """
    import catalog_copies as cc
    import test_run_button as trb
    from vmp import catalog_access as ca

    виды = cc.without_executors(tmp_path, monkeypatch, *семьи)
    копия = _копия_производителя(monkeypatch, tmp_path, поломка)
    try:
        run = trb._run_dir(tmp_path, _свод_с_посадками(посадки))
        d = trb.btn.orchestrate(run, trb.FakeSolver(),
                                sleep=lambda *_: None, **trb._fakes(True))
    finally:
        ca.forget()
    assert копия.__ФАЙЛ_КОПИИ__.endswith("export_task_package.py"), \
        "прогон недействителен: копия модуля не загружена"
    return d, виды


#: Исход потребителя при ЗДОРОВОЙ половине производителя: пробел
#: каталога назван и заявка на вид строится.
ЗДОРОВЫЙ_ВЕРДИКТ = "остановка: ВМП не закрыт шаблонами COSMOS"
#: Исход, которым потребитель говорит «отказ производителя прочитать
#: нечем»: заявки нет вовсе.
КРАСНЫЙ_ВЕРДИКТ = "остановка производителя"


def test_ш8_здоровая_половина_производителя_даёт_потребителю_заявку(
        tmp_path, monkeypatch):
    """ЯКОРЬ обратимости контрпробы: на НЕТРОНУТОЙ половине
    производителя (копия модуля байт в байт с деревом) потребитель
    строит заявку на вид исполнителя.

    Это положительная половина шва Ш-8: живой барьер K06 отдаёт код
    `support-missing` и НЕПУСТОЙ перечень адресов
    (`families/duty_limits`), и потребитель по НИМ — а не по словам
    отказа — называет раздел, запись и виды заявки. Тот же исход
    обязан меняться у каждой поломки ниже; где не меняется — шов
    измерен как непрочный, и это сказано словами, а не сглажено.

    Полный договор потребителя (подача в очередь, номер в словах
    остановки, квитанция) судит COSMOS своей пробой
    tests/test_run_button.py::test_пробел_относительно_записи_подаётся_
    заявкой_на_вид — здесь якорь держит РОВНО то, что определяет
    половина производителя: код и адреса перечня."""
    d, виды = _нажатие_без_исполнителей(tmp_path, monkeypatch, None)
    print("ЗДОРОВЫЙ ИСХОД: verdict=%r; заявка=%s"
          % (d["verdict"], d.get("catalog_requests")))
    assert d["verdict"] == ЗДОРОВЫЙ_ВЕРДИКТ, d
    строка = d["catalog_requests"][0]
    assert строка["form"] == "kind"
    assert (строка["section"], строка["record"]) == ("families",
                                                     "duty_limits")
    assert строка["kinds"] == виды["duty_limits"]
    assert строка["request_id"], "номер заявки считается по адресу записи"
    assert "families/duty_limits" in d["detail"]


@pytest.mark.parametrize("поломка", ["перечень пуст", "кода нет",
                                     "чужой код отказа"])
def test_ш8_снятый_код_или_перечень_краснят_потребителя(tmp_path,
                                                        monkeypatch,
                                                        поломка):
    """КОНТРПРОБА: сними у отказа код либо перечень — и потребитель
    заявку НЕ строит, а останавливается словами производителя.

    Три поломки половины производителя, каждая — свой параметр:
    перечень пуст (`items=[]`), кода нет (`None` на месте
    `SUPPORT_MISSING`), код чужой (`family-incomplete` — тот, которым
    отказывает старший барьер полноты семьи). Все три меняют ВЕРДИКТ
    кнопки: пробел каталога перестаёт называться, номер заявки не
    считается. Значит половина производителя потребителю нужна: без
    кода и перечня его половина не работает.

    Очередь заявок здесь НЕ улика: автоподача выключена изоляцией
    носителей (conftest, `CREW_CATALOG_REQUESTS_AUTO=0`), и очередь
    пуста при любом исходе — судится то, что перечень определяет:
    вердикт и строка заявки."""
    d, _ = _нажатие_без_исполнителей(tmp_path, monkeypatch, поломка)
    print("ПОЛОМКА %r → verdict=%r; заявка=%s"
          % (поломка, d["verdict"], d.get("catalog_requests")))
    assert d["verdict"] == КРАСНЫЙ_ВЕРДИКТ != ЗДОРОВЫЙ_ВЕРДИКТ, d
    assert not d.get("catalog_requests"), \
        "заявка не имеет права строиться без кода и перечня"
    if поломка == "перечень пуст":
        # Fail-closed назван словами, а не молчаливым пропуском.
        assert "семьи не назвал" in d["detail"], d["detail"][:300]


def test_ш8_перечень_не_того_вида_заявки_не_даёт_но_вердикт_тот_же(
        tmp_path, monkeypatch):
    """КОНТРПРОБА и ЧЕСТНАЯ ГРАНИЦА: перечень не того вида (имя семьи
    `duty_limits` вместо адреса записи `families/duty_limits` — ровно
    ошибка, которую чинила поправка §12.11 п. 3).

    Потребитель это РАЗЛИЧАЕТ, но НЕ ВЕРДИКТОМ: вердикт остаётся
    здоровым, и проба, которая судила бы один вердикт, поломку
    пропустила бы. Отличие — в строке заявки: `form` «none», номера
    нет, слова называют образец «<раздел>/<запись>». Отсюда мерило
    прочности этой половины шва: перечень судится по ВИДУ адреса, и
    судить его обязана строка заявки, а не вердикт."""
    d, _ = _нажатие_без_исполнителей(tmp_path, monkeypatch,
                                     "имя семьи вместо адреса записи")
    print("ПОЛОМКА 'имя семьи вместо адреса' → verdict=%r; заявка=%s"
          % (d["verdict"], d.get("catalog_requests")))
    assert d["verdict"] == ЗДОРОВЫЙ_ВЕРДИКТ, \
        "вердикт этой поломки не различает — так и записано"
    строка = d["catalog_requests"][0]
    assert строка["form"] == "none" and строка["request_id"] is None
    assert строка["section"] is None and строка["record"] is None
    assert "не по образцу" in строка["words"], строка["words"]


def test_ш8_чужой_адрес_в_перечне_потребителем_не_опознаётся(tmp_path,
                                                             monkeypatch):
    """ИЗМЕРЕНИЕ НЕПРОЧНОСТИ ШВА (следствия нет — и это сказано):
    производитель называет адрес ЧУЖОЙ записи того же вида, и
    потребитель принимает его за чистую монету.

    Стенд — обе семьи без исполнителей, посажена сводом ОДНА
    (`duty_limits`): живой барьер назвал бы только её. Сломанная
    половина называет `families/night_series` — адрес по образцу,
    запись в каталоге есть, исполнителей у неё нет. Потребитель
    строит ПОЛНОЦЕННУЮ заявку на чужую запись: вердикт здоровый,
    `form` «kind», номер считается.

    След у подмены ЕСТЬ и он машинный — но ДВУСМЫСЛЕННЫЙ, и потому
    различением не является. Правил свода на названной записи
    потребитель не находит и говорит это словами («правил свода на
    записях нет»), а слова отказа в `detail` называют СОВСЕМ ДРУГУЮ
    запись. Тот же самый след даёт и ЗАКОННЫЙ случай — расхождение
    двух половин счёта семей (`catalog_requests.family_of` у
    потребителя против `model.support_problems` у производителя),
    ради которого строка `bare` и заведена. Ошибка производителя и
    законное расхождение дают ОДИН вывод, поэтому следствия у
    потребителя нет и быть не может; лечится это полем причины у
    элемента перечня (§12.11) — на стороне CDPM.

    Проба закрепляет измерение, чтобы «потребитель читает перечень»
    не читалось как «потребитель перечень проверяет»."""
    d, виды = _нажатие_без_исполнителей(
        tmp_path, monkeypatch, "назван адрес чужой записи",
        семьи=("duty_limits", "night_series"))
    print("ПОЛОМКА 'чужой адрес' → verdict=%r; заявка=%s"
          % (d["verdict"], d.get("catalog_requests")))
    assert d["verdict"] == ЗДОРОВЫЙ_ВЕРДИКТ, d
    строка = d["catalog_requests"][0]
    assert строка["form"] == "kind", "подмена адреса выглядит здоровой"
    assert (строка["section"], строка["record"]) == ("families",
                                                     "night_series")
    assert строка["kinds"] == виды["night_series"]
    assert строка["request_id"], "номер заявки считается на чужую запись"
    # Расхождение перечня со словами отказа налицо — и не замечено.
    assert "families/duty_limits" in d["detail"]
    assert "duty_limits" not in str(строка["record"])
    # След двусмысленный: те же слова даёт законное расхождение половин.
    assert "правил свода на записях нет: families/night_series" \
        in d["detail"], d["detail"][-400:]


def test_ш8_повтор_адреса_в_перечне_различать_не_требуется(tmp_path,
                                                           monkeypatch):
    """ГРАНИЦА контрпробы: повтор адреса в перечне — НЕ поломка, и
    потребитель обязан не различать его по существу.

    Второй раз названная запись считается один раз
    (`dict.fromkeys` в `package_refusal_stop`): исход в точности
    здоровый. Проба держит это допущение явно — иначе следующая
    «строгая» правка потребителя (считать каждый элемент перечня
    отдельной строкой заявки) прошла бы незамеченной."""
    d, виды = _нажатие_без_исполнителей(tmp_path, monkeypatch,
                                        "адрес назван дважды")
    print("ДОПУСК 'адрес дважды' → verdict=%r; заявок=%d"
          % (d["verdict"], len(d.get("catalog_requests") or ())))
    assert d["verdict"] == ЗДОРОВЫЙ_ВЕРДИКТ, d
    assert len(d["catalog_requests"]) == 1, d["catalog_requests"]
    строка = d["catalog_requests"][0]
    assert (строка["form"], строка["section"], строка["record"]) == \
        ("kind", "families", "duty_limits")
    assert строка["kinds"] == виды["duty_limits"]


def test_ш8_перечень_производителя_называет_все_записи_без_поддержки(
        tmp_path, monkeypatch):
    """ПОЛНОТА перечня — закон ПОЛОВИНЫ ПРОИЗВОДИТЕЛЯ, и следствие у
    неё может быть только здесь (правило 8 реестра швов Ш-8).

    Потребитель неполный перечень не различает ПО ПОСТРОЕНИЮ: своего
    счёта записей без поддержки у него нет — он верит перечню
    (измерено пробой ниже). Значит охранять полноту обязан
    производитель, и проба на неё обязана существовать.

    Стенд — ДВЕ семьи без исполнителей, обе посажены ПОЛНЫМИ
    посадками (иначе раньше отказал бы старший барьер полноты семьи
    `family-incomplete`, и перечень был бы не о том). Путь
    настоящий: `build_package` без единой подмены. Ожидание — по
    одному адресу на КАЖДУЮ семью-носителя без подтверждённой
    поддержки, ни одного лишнего, все по образцу «families/<id>».

    Прежние пробы перечня (`tests/test_cdpm_k06_support_barrier.py`
    :692, :795) судят ОДНУ семью: усечение перечня до первой записи
    они не различают — обрезанный и полный перечень у них совпадают."""
    import test_cdpm_k06_support_barrier as k06
    k06._evidence(tmp_path, monkeypatch,
                  k06._drop("rest_floors"), k06._drop("duty_limits"),
                  cdpm=(k06._drop("rest_floors"),
                        k06._drop("duty_limits")))
    doc = k06._doc_with(k06._landed(),
                        k06._landed(rid="R-D", family="duty_limits",
                                    cells=k06.DUTY_CELLS))
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(k06._run_dir(tmp_path, doc))
    err = e.value
    print("ПОЛНОТА ПЕРЕЧНЯ: code=%r items=%s" % (err.code, err.items))
    assert err.code == tp.SUPPORT_MISSING
    assert sorted(err.items) == ["families/duty_limits",
                                 "families/rest_floors"], err.items
    # Каждая названная запись — и в словах отказа: перечень и слова об
    # одном и том же (расхождение потребителю не видно — см. пробу
    # «чужой адрес»).
    for адрес in err.items:
        assert адрес in str(err), (адрес, str(err)[:300])


def _ячейки_из_реестра(семья: str, цитата: str) -> dict:
    """Полный набор ячеек семьи — по ТОМУ ЖЕ закону полноты
    (`missing_params`), что судит барьер, а не своим перечнем.

    Своего перечня у пробы быть не должно (тот же довод, что у
    `_полная_посадка` выше): он завёл бы ВТОРОЕ МЕСТО ИСТИНЫ о
    каталоге, и проба молча краснела бы от роста реестра — причём
    СТАРШИМ кодом `family-incomplete`, то есть выглядела бы поломкой
    шва, которой нет. Находка неавторского ревью 13.09.2026."""
    from cdpm import family_params as fp
    ячейки: dict = {}
    for _ in range(20):
        нет = fp.missing_params(семья, ячейки)
        if not нет:
            return ячейки
        for имя in нет:
            ячейки[имя] = {"value": 12, "quote": цитата}
    raise AssertionError(f"полный набор семьи {семья} не сошёлся")


# ОЖИДАНИЕ ЗАДАНО УСЛОВИЯМИ ПРИМЕРА, а не зовом проверяемой функции
# (условие аудитора, GPT167 §3): шесть семей посажены и лишены клеток,
# седьмая посажена и поддержку сохраняет, восьмая клеток лишена, но НЕ
# посажена — значит в предмет этого свода не входит.
_БЕЗ_ПОДДЕРЖКИ_В_ПРЕДМЕТЕ = ["crew_composition", "days_off_floor",
                             "duty_limits", "night_series",
                             "reserve_floors", "rest_floors"]
_С_ПОДДЕРЖКОЙ_В_ПРЕДМЕТЕ = "span_accumulators"
_БЕЗ_ПОДДЕРЖКИ_ВНЕ_ПРЕДМЕТА = "deadhead_rules"


def _стенд_полноты(tmp_path, monkeypatch, k06, посаженные=None):
    """Стенд полноты перечня: снимает клетки у шести семей предмета и у
    одной вне предмета, оставляет поддержку седьмой. Возвращает свод."""
    снять = [k06._drop(f) for f in
             _БЕЗ_ПОДДЕРЖКИ_В_ПРЕДМЕТЕ + [_БЕЗ_ПОДДЕРЖКИ_ВНЕ_ПРЕДМЕТА]]
    k06._evidence(tmp_path, monkeypatch, *снять, cdpm=tuple(снять))
    семьи = посаженные if посаженные is not None else (
        _БЕЗ_ПОДДЕРЖКИ_В_ПРЕДМЕТЕ + [_С_ПОДДЕРЖКОЙ_В_ПРЕДМЕТЕ])
    правила = []
    for i, f in enumerate(семьи):
        r = k06._landed(rid=f"R-{i}", family=f, cells={})
        r["template_fit"]["params"] = _ячейки_из_реестра(f, r["statement"])
        правила.append(r)
    return k06._doc_with(*правила)


def test_ш8_полнота_перечня_шесть_записей_поддержанная_и_вне_предмета(
        tmp_path, monkeypatch):
    """ПОЛНОТА перечня по условиям аудитора (GPT167 §3): различающий
    стенд, а не одна семья.

    Проверяются РАЗОМ четыре вещи, которых прежняя проба на двух
    семьях не закрывала:

    1. **несколько независимо заданных** неподдержанных используемых
       семей — их ШЕСТЬ, и перечень обязан назвать все;
    2. **поддержанная используемая** семья (`span_accumulators`)
       в перечень НЕ входит — перечень не про «все посаженные»;
    3. **неподдержанная семья ВНЕ предмета** (`deadhead_rules`,
       клеток лишена, но не посажена) в перечень НЕ входит — перечень
       не про «все пробелы каталога»;
    4. шесть больше четырёх: предел четвёрки у ЧЕЛОВЕЧЕСКИХ СЛОВ
       (`cdpm/export_task_package.py`, `_require_barriers`) НЕ
       становится пределом машинного списка.

    Ожидаемый состав задан условиями примера — списком
    `_БЕЗ_ПОДДЕРЖКИ_В_ПРЕДМЕТЕ`, а НЕ зовом `support_problems` или
    `readiness_barriers`, которые здесь и проверяются.

    Отказ обязан быть именно `support-missing`: при смешанных
    основаниях `family-incomplete` звучит РАНЬШЕ, и зелень на нём была
    бы фиктивным прохождением — поэтому все посадки полные, и код
    отказа проверяется отдельным утверждением."""
    import test_cdpm_k06_support_barrier as k06
    doc = _стенд_полноты(tmp_path, monkeypatch, k06)

    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(k06._run_dir(tmp_path, doc))
    err = e.value
    print("ПОЛНОТА-6: code=%r items=%s" % (err.code, sorted(err.items)))

    # отказ тот самый, а не старший сосед
    assert err.code == tp.SUPPORT_MISSING, (err.code, str(err)[:300])

    ожидание = sorted(f"families/{f}" for f in _БЕЗ_ПОДДЕРЖКИ_В_ПРЕДМЕТЕ)
    assert sorted(err.items) == ожидание, err.items

    # (2) и (3) — отдельными словами, чтобы поломка была читаема
    assert f"families/{_С_ПОДДЕРЖКОЙ_В_ПРЕДМЕТЕ}" not in err.items
    assert f"families/{_БЕЗ_ПОДДЕРЖКИ_ВНЕ_ПРЕДМЕТА}" not in err.items

    # (4) список длиннее четвёрки слов
    assert len(err.items) == 6
    # Предел ЧЕТВЁРКИ — у СЛОВ, и только у слов (`sup[:4]` в
    # `_require_barriers`). Прежнее `<=` было тождественно истинно
    # (обе стороны по 6) и не краснело даже при снятии предела; а
    # `< len(err.items)` проверяет лишь сокращение — его прошли бы и
    # пять, и ноль (поправка аудитора GPT173). Закрепляем ТОЧНОЕ число.
    слова = str(err)
    названо_словами = [а for а in ожидание if а.split("/")[1] in слова]
    assert len(названо_словами) == 4, названо_словами
    assert len(названо_словами) < len(err.items), названо_словами


def test_ш8_поддержанная_семья_одна_в_предмете_отказа_не_даёт(
        tmp_path, monkeypatch):
    """ПОЛОЖИТЕЛЬНЫЙ контроль того же стенда: если в предмете ОДНА
    поддержанная семья, отказа поддержки нет вовсе. Без него
    предыдущая проба не отличала бы «перечень верен» от «барьер
    отказывает всегда»."""
    import test_cdpm_k06_support_barrier as k06
    doc = _стенд_полноты(tmp_path, monkeypatch, k06,
                         посаженные=[_С_ПОДДЕРЖКОЙ_В_ПРЕДМЕТЕ])
    tp.build_package(k06._run_dir(tmp_path, doc))   # исключения нет


def test_ш8_усечённый_перечень_потребителем_не_различается(tmp_path,
                                                           monkeypatch):
    """ИЗМЕРЕНИЕ НЕПРОЧНОСТИ ШВА (следствия у потребителя нет — и это
    сказано): производитель называет ОДНУ запись из двух, и
    потребитель принимает перечень за полный.

    Обе семьи посажены и обе без исполнителей — законный отказ нёс бы
    два адреса. Сломанная половина отдаёт первый. Потребитель строит
    ОДНУ заявку, вердикт здоровый, и второй пробел исчезает молча:
    своего счёта записей без поддержки у него нет и быть не может —
    он судит каталог не тем законом, что барьер (`catalog_requests.
    family_of` против `model.support_problems`).

    Следствие этой поломки — ПРОБА ВЫШЕ, на стороне производителя
    (`test_ш8_перечень_производителя_называет_все_записи_без_поддержки`):
    полноту перечня охраняет тот, кто его строит."""
    d, _ = _нажатие_без_исполнителей(
        tmp_path, monkeypatch, "перечень неполон",
        семьи=("duty_limits", "night_series"),
        посадки=(_ПОСАДКА_ДЕЖУРСТВА, _ПОСАДКА_НОЧЕЙ))
    строки = d["catalog_requests"]
    print("ПОЛОМКА 'перечень неполон' → verdict=%r; заявок=%d; записи=%s"
          % (d["verdict"], len(строки),
             [с.get("record") for с in строки]))
    # Проба не имеет права быть пустой: на ЭТОМ ЖЕ стенде законный
    # перечень несёт ДВЕ записи — значит он правда усечён, а не совпал
    # с полным. Состав задан УСЛОВИЯМИ СТЕНДА (две посадки выше), а не
    # зовом `support_problems`, которую этот же шов и проверяет:
    # обещанная чистка, условие аудитора GPT167 §3.
    assert len(строки) < 2, строки
    assert d["verdict"] == ЗДОРОВЫЙ_ВЕРДИКТ, d
    записи = {с.get("record") for с in строки}
    assert записи == {"duty_limits"}, записи
    # Вторая запись пропала молча: в словах остановки её нет как
    # ПРОБЕЛА — её называют только слова отказа самого производителя.
    assert not any("night_series" == с.get("record") for с in строки)
