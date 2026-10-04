# -*- coding: utf-8 -*-
"""Запись о прогоне (vmp/run_record.py + vmp/run_record.schema.json):
единый словарь полей CDPM, COSMOS и раннера наборов.

Основание — проект `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md` и слово
владельца 10.09.2026 «Начинай» (шаг 1 раскладки). Судится ровно то, ради
чего словарь заведён одним носителем:

  * круг «паспорт → объявление → запись → журнал → итоги → файл →
    чтение» проходит целиком и без расхождений;
  * голова записи неизменна после старта: подменили объявление —
    дописывать журнал нельзя, и отказ говорит словами;
  * корень дерева прогонов детерминирован и меняется от СМЕНЫ ОДНОГО
    ОТВЕТА на анкету (раздел 7 проекта: ответы — тоже исходные
    документы);
  * «золотой» по умолчанию не подтверждён и без подписи владельца не
    ставится (решение владельца № 3);
  * запись попытки COSMOS ложится этапом журнала под именами раздела
    3.2.3 проекта;
  * объявление без каталога, привязок или документов правил не
    принимается: умолчаний «из дерева» у прогона нет;
  * схема и модуль называют ОДНИ И ТЕ ЖЕ поля — перебором в обе
    стороны, иначе носителя словаря снова станет два.

Литералов среды в файле нет (сторож tests/test_no_environment_literals):
пути — только `tmp_path`, отпечатки строятся в самом тесте, служб не
поднимается.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vmp import run_record as rr                                  # noqa: E402

#: Отпечатки-пробы: строятся тестом, живых файлов не читают.
SHA_RULES = rr.sha256_text("документ правил")
SHA_DATA = rr.sha256_text("книга данных")
SHA_ANSWERS = rr.sha256_text("книга ответов, ревизия 1")
SHA_ANSWERS_2 = rr.sha256_text("книга ответов, ревизия 2")
SHA_CATALOG = rr.sha256_text("каталог шаблонов пробы")
SHA_PACKAGE = rr.sha256_text("пакет постановки пробы")


# ------------------------------------------------------------ помощники
def passport(**over) -> dict:
    """Синтетический паспорт прогона в именах `cosmos-run-passport/v1`.

    Настоящий паспорт собирает `vmp/run_passport.py` от файлов набора;
    здесь судится ПЕРЕВОД имён, а не сборка, поэтому паспорт строится
    словарём — стенд остаётся без набора, каталога и служб. Стык с
    настоящей сборкой судит tests/test_run_passport.py."""
    doc = {
        "schema": "cosmos-run-passport/v1",
        "at": "2026-09-10T09:00:00",
        "kit": {"dir": "наборы/проба", "manifest": "наборы/проба/kit-manifest.json",
                "manifest_sha256": rr.sha256_text("манифест"),
                "customer": "Проба-запись", "stage": "проба записи",
                "data_class": "synthetic", "llm_scope": "any",
                "references": {"answer_book": {"file": "наборы/проба/ответы.json",
                                               "sha256": SHA_ANSWERS,
                                               "present": True}}},
        "rules_documents": [{"file": "наборы/проба/ПРАВИЛА.docx",
                             "name": "ПРАВИЛА.docx", "sha256": SHA_RULES,
                             "bytes": 1024}],
        "data_documents": [{"file": "наборы/проба/ДАННЫЕ.xlsx",
                            "name": "ДАННЫЕ.xlsx", "sha256": SHA_DATA,
                            "bytes": 2048}],
        "catalog": {"source": "дерево", "path": "каталоги/проба",
                    "version": "0.4.0", "fingerprint": SHA_CATALOG,
                    "words": "каталог дерева"},
        "bindings": {"source": "дерево", "file": "vmp/bindings.json",
                     "present": True, "catalog_version": "0.4.0",
                     "catalog_fingerprint": SHA_CATALOG, "rows": 12},
        "agents_layout": {"source": "манифест набора", "file": "раскладка.json",
                          "sha256": rr.sha256_text("раскладка"),
                          "roles": {"extractor": {"cloud": {
                              "provider": "cloud", "model": "модель-извлечения",
                              "effort": "high", "temperature": 0}}}},
        "solver": {"source": None, "url": None, "engine": "timefold",
                   "required": False, "reachable": None,
                   "build": {"app_jar_sha256": rr.sha256_text("jar"),
                             "timefold_version": "2.0.0"}},
        "python": {"version": "3.14.4", "implementation": "CPython",
                   "packages": [{"name": "jsonschema", "version": "4.26.0"}],
                   "packages_count": 1,
                   "packages_sha256": rr.sha256_text("перечень пакетов")},
        "code": {"commit": "0123456789ab", "fingerprint": "fedcba9876543210",
                 "tree_dirty": False, "files": 149},
        "panel": {"base_url": None, "in_process": True},
        "stages": {"until": "package"},
        "problems": [],
    }
    doc.update(over)
    return doc


def declaration(**over) -> dict:
    """Объявление прогона из синтетического паспорта."""
    d = rr.passport_to_declaration(
        passport(), run_id="проба-1", purpose="регресс", contour="local",
        created_by="стенд", planned_stages=["create", "extract", "package"])
    d.update(over)
    return d


def attempt(**over) -> dict:
    """Запись попытки COSMOS в именах `webapp/package_runs.py`
    (`attempts[]` состояния линии): параметры под ключом `params`,
    сводка выдачи — `summary`, кто нажал — `actor`."""
    rec = {
        "id": "a1b2c3d4", "task": "rostering", "engine": None,
        "pairing_selection": None,
        "started_at": "2026-09-10T10:00:00",
        "finished_at": "2026-09-10T10:04:00",
        "state": "кнопка: готово", "done": True, "verdict": "допустимо",
        "error": None, "package_sha": SHA_PACKAGE,
        "catalog": "package-runs/проба/button", "llm_scope": "any",
        "certifier": {"model": "модель-сертификатора", "channel": "api",
                      "provider": "cloud"},
        "actor": "user:стенд", "run": "прогон-1",
        "params": {"mode": "штатный", "objective_id": None,
                   "spent_limit_s": 60, "unimproved_spent_limit_s": 30,
                   "pairing_run_id": None, "moves": "полный",
                   "accepted_uncovered": [], "poll_s": 2.0,
                   "deterministic": {"seed": 7, "step_limit": 1000,
                                     "unimproved_step_limit": 200,
                                     "deterministic_time": 30}},
        "code": {"commit": "0123456789ab", "commit_short": "0123456"},
        "summary": {"verdict": "допустимо", "certified": True,
                    "solver_run_id": "решатель-1", "score": "0hard/-5soft",
                    "chains": 12, "findings": 0, "uncovered": 0,
                    "certificate_gate": 0},
    }
    rec.update(over)
    return rec


# ------------------------------------------------------- круг целиком
def test_круг_паспорт_объявление_запись_журнал_итоги_файл(tmp_path):
    """Паспорт → объявление → запись → журнал → итоги → сохранить →
    загрузить → расхождений нет. Круг целиком: словарь полей один, и
    ничего по дороге не теряется."""
    record = rr.new_record(declaration())
    assert record["schema"] == rr.SCHEMA
    assert record["results"]["state"] == rr.STATE_DECLARED
    assert record["journal"] == []
    # голова приняла параметры паспорта своими именами
    d = record["declaration"]
    assert d["catalog"]["version"] == "0.4.0"
    assert d["bindings"]["catalog_fingerprint"] == SHA_CATALOG
    assert d["rules_documents"][0]["kind"] == "docx"
    assert d["models"][0]["role"] == "extractor"
    assert d["models"][0]["channel"] == "cloud"
    assert d["libraries"]["python"] == "3.14.4"
    assert d["answer_policy"] == "книга"
    # ответы к объявлению неизвестны — ревизии закрываются по ходу
    # прогона (10.09.2026, run-0141); книга набора — ссылка паспорта
    assert d["answers"] == [] and d["lineage"]["root_answers_sha256"] is None

    rr.append_stage(record, {"stage": "create", "outcome": rr.OUTCOME_OK,
                             "started_at": "2026-09-10T09:00:00+03:00",
                             "finished_at": "2026-09-10T09:02:00+03:00",
                             "counters": {"files": 3, "clauses": 41},
                             "artifacts": [{"kind": "прогон CDPM",
                                            "path": "run-0001"}]})
    assert record["results"]["state"] == rr.STATE_RUNNING
    rr.append_stage(record, {"stage": "package", "outcome": rr.OUTCOME_RULE,
                             "reason_code": "coverage-gap",
                             "words": "пакет не выдан: пробел покрытия",
                             "started_at": "2026-09-10T09:02:00+03:00",
                             "finished_at": "2026-09-10T09:03:00+03:00",
                             "duration_waiting_human_s": 30})
    rr.finish(record, {"verdicts": {"package": "стоп"}})
    assert record["results"]["state"] == rr.STATE_RULE_STOP
    assert record["results"]["timing"]["machine_s"] == 180
    assert record["results"]["timing"]["waiting_human_s"] == 30
    assert record["results"]["timing"]["total_s"] == 210

    path = rr.save(tmp_path, record)
    assert path.name == rr.RECORD_FILE
    assert rr.load(tmp_path) == record
    assert rr.validate(rr.load(path)) == []


def test_длительность_считается_по_отметкам_а_без_них_молчит():
    """Время машины берётся из отметок этапа; отметок нет — поле пусто,
    а не ноль: ноль значил бы «мгновенно», а не «не измеряли»."""
    e = rr.normalize_entry({"stage": "extract", "outcome": rr.OUTCOME_OK,
                            "started_at": "2026-09-10T09:00:00+03:00",
                            "finished_at": "2026-09-10T09:00:45+03:00"})
    assert e["duration_machine_s"] == 45
    assert rr.normalize_entry({"stage": "extract",
                               "outcome": rr.OUTCOME_OK})["duration_machine_s"] is None


def test_непройденный_этап_плана_помечается_не_начат():
    """План этапов объявлен: до чего прогон не дошёл — «не начат», а не
    молчание (раздел 1 проекта)."""
    record = rr.new_record(declaration())
    rr.append_stage(record, {"stage": "create", "outcome": rr.OUTCOME_OK})
    rr.append_stage(record, {"stage": "package",
                             "outcome": rr.OUTCOME_NOT_STARTED,
                             "reason_code": "stage-not-reached"})
    rr.finish(record, None, rr.STATE_HUMAN_STOP)
    assert [e["outcome"] for e in record["journal"]] == [rr.OUTCOME_OK,
                                                         rr.OUTCOME_NOT_STARTED]
    assert record["results"]["state"] == rr.STATE_HUMAN_STOP


# ------------------------------------------------ неизменность головы
def test_объявление_после_старта_неизменно(tmp_path):
    """Голова записи — рецепт повторения: подменённая на ходу, она
    рецептом быть перестаёт. Дописывание журнала к подменённому
    объявлению — отказ словами."""
    record = rr.new_record(declaration())
    rr.append_stage(record, {"stage": "create", "outcome": rr.OUTCOME_OK})
    record["declaration"]["catalog"]["version"] = "0.5.0"
    with pytest.raises(rr.RunRecordError) as e:
        rr.append_stage(record, {"stage": "extract", "outcome": rr.OUTCOME_OK})
    assert "объявление изменено после старта" in str(e.value)
    assert "объявлено" in str(e.value) and "на деле" in str(e.value)
    # та же подмена названа и проверкой записи целиком
    words = rr.validate(record)
    assert any("объявление изменено после старта" in w for w in words), words
    assert len(record["journal"]) == 1


def test_отпечаток_объявления_не_зависит_от_порядка_ключей():
    """Отпечаток головы канонический: порядок ключей на него не влияет,
    иначе неизменность ловила бы перестановку вместо подмены."""
    d = declaration()
    reordered = {k: d[k] for k in reversed(list(d))}
    assert rr.declaration_sha256(d) == rr.declaration_sha256(reordered)


# ------------------------------------------------------ корень дерева
def test_корень_дерева_детерминирован_и_меняется_от_одного_ответа():
    """Корень по документам и данным известен до старта и не зависит от
    порядка и имён файлов; корень с ответами (раздел 7 проекта) меняется
    от смены одного ответа — новая ветвь дерева. Счёт — по договору с
    CDPM 10.09.2026: sha256(canonical({"rules": [sha…], "data": [sha…]
    [, "answers": [sha…]}))."""
    import hashlib
    rules = [{"name": "А.docx", "sha256": SHA_RULES},
             {"name": "Б.docx", "sha256": SHA_DATA}]
    data = [{"name": "Д.xlsx", "sha256": SHA_DATA}]
    answers = [{"revision": "rev-001", "sha256": SHA_ANSWERS}]
    root = rr.root_sha256(rules, data)
    assert root == rr.root_sha256(list(reversed(rules)), data)
    assert root == rr.root_sha256([{"name": "иначе.docx", "sha256": SHA_RULES},
                                   {"name": "Б.docx", "sha256": SHA_DATA}], data)
    assert root == hashlib.sha256(rr.canonical(
        {"rules": sorted([SHA_RULES, SHA_DATA]), "data": [SHA_DATA]})).hexdigest()
    assert rr.root_sha256([], []) != root
    with_answers = rr.root_answers_sha256(rules, data, answers)
    assert with_answers and with_answers != root
    other = rr.root_answers_sha256(rules, data,
                                   [{"revision": "rev-001", "sha256": SHA_ANSWERS_2}])
    assert other != with_answers
    assert rr.root_answers_sha256(rules, data, []) is None


def test_корень_записывается_в_объявление_и_сверяется():
    """Корень едет в объявление сам; подменённый — расхождение словами."""
    record = rr.new_record(declaration())
    d = record["declaration"]
    assert d["lineage"]["root_sha256"] == rr.root_sha256(
        d["rules_documents"], d["data_files"])
    assert d["lineage"]["root_answers_sha256"] == rr.root_answers_sha256(
        d["rules_documents"], d["data_files"], d["answers"])
    record["declaration"]["lineage"]["root_sha256"] = SHA_CATALOG
    words = rr.validate(record)
    assert any("корень дерева прогонов не сходится" in w for w in words), words


def test_отпечаток_объявления_не_зависит_от_ответов():
    """Ревизии ответов растут по ходу прогона и в отпечаток головы не
    входят (договор с CDPM): дописанный ответ не ломает неизменность
    объявления, а подмена каталога — ломает."""
    d = declaration()
    before = rr.declaration_sha256(d)
    grown = dict(d, answers=list(d.get("answers") or [])
                 + [{"revision": "rev-002", "sha256": SHA_ANSWERS_2,
                     "source": "человек", "at": None}])
    assert rr.declaration_sha256(grown) == before
    changed = dict(d, catalog=dict(d["catalog"], version="9.9.9"))
    assert rr.declaration_sha256(changed) != before


# --------------------------------------------------------- золотой
def test_золотой_прогон_по_умолчанию_не_подтверждён():
    record = rr.new_record(declaration())
    assert record["results"]["golden"]["confirmed"] is False
    assert record["results"]["golden"]["by"] is None


def test_золотой_прогон_без_подписи_владельца_отказ():
    """Признак образца ставится ТОЛЬКО словом владельца после приёмки на
    Prod (решение владельца № 3): без «кто, когда и какими словами» —
    расхождение."""
    record = rr.new_record(declaration())
    rr.append_stage(record, {"stage": "create", "outcome": rr.OUTCOME_OK})
    with pytest.raises(rr.RunRecordError) as e:
        rr.finish(record, {"golden": {"confirmed": True}})
    assert "золотым прогон назван без подписи" in str(e.value)
    rr.finish(record, {"golden": {"confirmed": True, "by": "владелец",
                                  "at": "2026-09-11T08:00:00+03:00",
                                  "words": "прогон принят на Prod"}})
    assert record["results"]["golden"]["confirmed"] is True
    assert rr.validate(record) == []


# ------------------------------------------------- попытка COSMOS
def test_попытка_cosmos_становится_этапом_журнала():
    """Запись попытки COSMOS ложится этапом под именами раздела 3.2.3
    проекта: задача — этап, номер попытки — подэтап, вердикт — исход,
    счётчики сводки — счётчиками этапа."""
    entry = rr.attempt_to_journal(attempt())
    assert entry["stage"] == "ростер"
    assert entry["substage"] == "a1b2c3d4"
    assert entry["outcome"] == rr.OUTCOME_OK
    assert entry["actor"] == "user:стенд"
    assert entry["duration_machine_s"] == 240
    assert entry["counters"]["chains"] == 12
    assert entry["counters"]["uncovered"] == 0
    # словесная оценка решателя числом не притворяется
    assert "score" not in entry["counters"]
    kinds = {a["kind"]: a for a in entry["artifacts"]}
    assert kinds["пакет постановки"]["sha256"] == SHA_PACKAGE
    # сертификатор назван; расхода попытка не несёт вовсе — число
    # вызовов неизвестно, и токены честно «не считаются»
    model = entry["resources"]["models"][0]
    assert model["role"] == rr.CERTIFIER_ROLE_WORDS
    assert model["counted"] is False and model["input_tokens"] is None
    assert model["calls"] is None
    assert "не прочитан" in model["note"]
    assert rr.validate(_record_with(entry)) == []


def test_попытка_на_пейринге_и_пейринг_называются_разными_этапами():
    assert rr.attempt_to_journal(attempt(task="pairing"))["stage"] == "пейринг"
    on_pairing = attempt(pairing_selection={"run": "пейринг-1"})
    assert rr.attempt_to_journal(on_pairing)["stage"] == "ростер на пейринге"


def test_попытка_с_ошибкой_и_попытка_на_ходу():
    """Ошибка без кода причины — сбой; ошибка с кодом — отказ по правилу;
    незаконченная попытка вердикта не получает."""
    broken = rr.attempt_to_journal(attempt(error="решатель не ответил",
                                           done=True, verdict=None))
    assert broken["outcome"] == rr.OUTCOME_FAILURE
    assert broken["words"] == "решатель не ответил"
    refused = rr.attempt_to_journal(attempt(error="каталог не тот",
                                            reason_code="catalog-mismatch"))
    assert refused["outcome"] == rr.OUTCOME_RULE
    running = rr.attempt_to_journal(attempt(done=False, verdict=None,
                                            finished_at=None))
    assert running["outcome"] == rr.OUTCOME_NOT_STARTED
    assert running["reason_code"] == "attempt-unfinished"
    assert "идёт" in running["words"]


def test_попытка_несёт_свой_расход_моделей():
    """Попытка уже считает токены — запись берёт её счёт, а не
    придумывает свой; второй строки сертификатора при этом не заводится
    (роль зовут и кодом, и словами — обе формы узнаются)."""
    carried = attempt(resources={"models": [
        {"role": "certifier", "model": "модель", "provider": "cloud",
         "calls": 2, "input_tokens": 1200, "output_tokens": 300,
         "counted": True, "note": None}]})
    models = rr.attempt_to_journal(carried)["resources"]["models"]
    assert len(models) == 1
    assert models[0]["input_tokens"] == 1200 and models[0]["counted"] is True
    words = attempt(resources={"models": [
        {"role": rr.CERTIFIER_ROLE_WORDS, "role_code": "certifier",
         "model": "модель", "provider": "cloud", "calls": 1,
         "input_tokens": 10, "output_tokens": 20, "counted": True}]})
    assert len(rr.attempt_to_journal(words)["resources"]["models"]) == 1


def test_объявленный_сертификатор_при_нуле_вызовов_виден_с_нулём():
    """Объявленная модель сертификатора обязана быть ВИДНА в итогах.

    Свод расхода строит строки по состоявшимся вызовам, и при нуле
    вызовов объявленная модель из итогов пропадала — а пропажа читается
    «сертификатор не объявлен». Ноль вызовов знается двумя путями: свод
    прямо сказал «вызовов не было» кодом либо вернул строки, среди
    которых сертификатора нет (значит, он не звался). В обоих случаях
    строка стоит с явным «вызовов 0» и нулевыми токенами, и ноль
    СОСЧИТАН."""
    no_calls = attempt(resources={"models": [],
                                  "models_code": rr.NO_MODEL_CALLS_CODE})
    models = rr.attempt_to_journal(no_calls)["resources"]["models"]
    assert len(models) == 1
    row = models[0]
    assert row["role"] == rr.CERTIFIER_ROLE_WORDS
    assert row["model"] == "модель-сертификатора"
    assert row["calls"] == 0
    assert row["input_tokens"] == 0 and row["output_tokens"] == 0
    assert row["counted"] is True, "ноль вызовов — сосчитанный ноль"
    assert "вызовов не было" in row["note"] and "api" in row["note"]
    # Читателя ИДАК звали, сертификатора — нет: строка всё равно стоит.
    only_reader = attempt(resources={"models": [
        {"role": "читатель ИДАК", "role_code": "source_check_reader",
         "model": "модель-читателя", "provider": "ollama", "calls": 3,
         "input_tokens": 30, "output_tokens": 40, "counted": True}]})
    models = rr.attempt_to_journal(only_reader)["resources"]["models"]
    assert len(models) == 2
    cert = [m for m in models if m["role"] == rr.CERTIFIER_ROLE_WORDS]
    assert cert and cert[0]["calls"] == 0 and cert[0]["counted"] is True


def test_нулевые_вызовы_видны_в_итогах_дерева():
    """Итоги прогона (свод `vmp/run_tree.py`) обязаны показать
    объявленную модель строкой «вызовов 0», а не промолчать: молчание
    читается «модель не объявлена»."""
    from vmp import run_tree

    entry = rr.attempt_to_journal(attempt(resources={
        "models": [], "models_code": rr.NO_MODEL_CALLS_CODE}))
    tokens = run_tree._tokens({"journal": [entry]})
    key = "%s/модель-сертификатора" % rr.CERTIFIER_ROLE_WORDS
    assert key in tokens, tokens
    assert tokens[key] == {"input_tokens": 0, "output_tokens": 0,
                           "calls": 0, "counted": True}


# ------------------------------------ объявление без обязательного
def test_объявление_без_каталога_привязок_и_документов_отказ():
    """Ничего «из дерева по умолчанию» (требование 2 раздела 8 проекта):
    каталог, привязки и документы правил называются до старта, иначе
    запись не создаётся и отказ говорит про каждое."""
    with pytest.raises(rr.RunRecordError) as e:
        rr.new_record({"run_id": "проба-2", "purpose": "регресс",
                       "contour": {"name": "local", "services": {}}})
    words = str(e.value)
    assert "без каталога правил" in words
    assert "без привязок исполнителей COSMOS" in words
    assert "без документов правил авиакомпании" in words


def test_объявление_без_номера_прогона_отказ():
    with pytest.raises(rr.RunRecordError) as e:
        rr.new_record(declaration(run_id=None))
    assert "без номера прогона" in str(e.value)


def test_слова_целей_площадок_и_исходов_судятся():
    """Слова значений — из перечня: чужое слово не проходит ни в цель,
    ни в площадку, ни в исход этапа."""
    with pytest.raises(rr.RunRecordError) as e:
        rr.new_record(declaration(purpose="просто так"))
    assert "не из перечня слов" in str(e.value)
    record = rr.new_record(declaration())
    with pytest.raises(rr.RunRecordError) as e:
        rr.append_stage(record, {"stage": "create", "outcome": "нормально"})
    assert "не из перечня слов" in str(e.value)
    with pytest.raises(rr.RunRecordError):
        rr.append_stage(record, {"outcome": rr.OUTCOME_OK})   # этап без имени


def test_поля_мимо_словаря_не_принимаются():
    """Словарь полей один: дописать в запись поле мимо него нельзя."""
    record = rr.new_record(declaration())
    record["declaration"]["своё_поле"] = 1
    words = rr.validate(record)
    assert any("схема не описывает" in w for w in words), words


# ---------------------------------------------------------- хранение
def test_запись_ложится_атомарно_и_читается(tmp_path):
    """Сохранение — через соседний временный файл и замену: оборванная
    запись не оставляет половины файла. Временных следов после
    сохранения нет."""
    record = rr.new_record(declaration())
    path = rr.save(tmp_path / "папка" / rr.RECORD_FILE, record)
    assert path.is_file()
    assert not list(path.parent.glob("*.tmp"))
    assert json.loads(path.read_text(encoding="utf-8")) == record
    rr.save(path, record)                       # повторная запись поверх
    assert not list(path.parent.glob("*.tmp"))


def test_записи_нет_отказ_словами(tmp_path):
    with pytest.raises(rr.RunRecordError) as e:
        rr.load(tmp_path)
    assert "записи о прогоне нет" in str(e.value)
    bad = tmp_path / rr.RECORD_FILE
    bad.write_text("не json", encoding="utf-8")
    with pytest.raises(rr.RunRecordError) as e:
        rr.load(tmp_path)
    assert "не разобрана как JSON" in str(e.value)


def test_слова_записи_для_человека():
    """Карточка и журнал читают запись словами, а не JSON-ом."""
    record = rr.new_record(declaration())
    rr.append_stage(record, {"stage": "create", "outcome": rr.OUTCOME_OK,
                             "started_at": "2026-09-10T09:00:00+03:00",
                             "finished_at": "2026-09-10T09:01:00+03:00"})
    rr.finish(record, {"verdicts": {"package": "собран"}})
    text = rr.summary(record)
    assert "ЗАПИСЬ О ПРОГОНЕ проба-1" in text
    assert "цель: регресс" in text
    assert "create: успех" in text
    assert "золотой прогон: не подтверждён" in text
    assert "Проба-запись" in text


# ------------------------------------------- схема и модуль об одном
def _record_with(entry: dict) -> dict:
    record = rr.new_record(declaration())
    rr.append_stage(record, entry)
    return record


def test_схема_описывает_ровно_поля_модуля():
    """Перебором в обе стороны: каждое поле, которое пишет модуль,
    описано схемой, и схема не описывает полей, которых модуль не
    пишет. Иначе носителей словаря снова станет два."""
    parts = {"": rr.RECORD_FIELDS, "declaration": rr.DECLARATION_FIELDS,
             "journal[]": rr.JOURNAL_FIELDS, "results": rr.RESULTS_FIELDS}
    parts.update(rr.NESTED_FIELDS)
    for path, fields in parts.items():
        described = set(rr.schema_properties(path))
        assert described == set(fields), (path, sorted(described ^ set(fields)))


def test_схема_и_модуль_называют_одни_слова():
    """Перечни слов (цель, площадка, исход, состояние, источник ответов,
    политика ответов) живут в схеме, и модуль повторяет их без
    расхождений."""
    for path, field, words in (
            ("declaration", "purpose", rr.PURPOSES),
            ("declaration.contour", "name", rr.CONTOURS),
            ("journal[]", "outcome", rr.OUTCOMES),
            ("results", "state", rr.STATES)):
        assert tuple(rr.schema_properties(path)[field]["enum"]) == words, path
    policies = rr.schema_properties("declaration")["answer_policy"]["enum"]
    assert tuple(p for p in policies if p is not None) == rr.ANSWER_POLICIES
    sources = rr.schema_properties("declaration.answers[]")["source"]["enum"]
    assert tuple(s for s in sources if s is not None) == rr.ANSWER_SOURCES
    assert rr.schema_properties("")["schema"]["const"] == rr.SCHEMA


def test_записи_модуля_не_несут_полей_мимо_схемы(tmp_path):
    """Обход готовой записи: у каждого словаря записи ключи описаны
    схемой по его пути. Свободные словари (счётчики, вердикты, адреса
    служб) полей не объявляют — их обход не трогает."""
    record = rr.new_record(declaration())
    rr.append_stage(record, rr.attempt_to_journal(attempt()))
    rr.append_stage(record, {"stage": "create", "outcome": rr.OUTCOME_OK,
                             "counters": {"files": 3}})
    rr.finish(record, {"deliveries": [{"kind": "выгрузка", "path": "f.csv",
                                       "sha256": SHA_DATA}],
                       "comparison": {"expectations": {"судимых": 5}}})

    def walk(value, path):
        if isinstance(value, dict):
            described = rr.schema_properties(path)
            if not described:
                return
            assert set(value) <= set(described), (path, sorted(value))
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for item in value:
                walk(item, path + "[]")

    walk(record, "")
    assert rr.validate(record) == []


def test_несуществующее_поле_схемы_отказ_словами():
    with pytest.raises(rr.RunRecordError) as e:
        rr.schema_properties("declaration.каталог")
    assert "схема записи не описывает поле" in str(e.value)


def test_запись_журнала_называет_систему_летописца():
    """Шаг 2 (10.09.2026): необязательное поле `system` единицы журнала —
    кто написал запись: панель, раннер, COSMOS; без поля — запись хозяина
    (совместимость с записями шага 1). Слово вне перечня — отказ словами."""
    record = rr.new_record(declaration())
    rr.append_stage(record, {"stage": "create", "outcome": rr.OUTCOME_OK})
    assert record["journal"][0]["system"] is None
    rr.append_stage(record, {"stage": "package", "outcome": rr.OUTCOME_OK,
                             "system": rr.SYSTEM_RUNNER})
    assert record["journal"][1]["system"] == "runner"
    assert rr.validate(record) == []
    # запись без поля вовсе — по схеме годна (панель шага 1 поля не пишет)
    bare = {k: v for k, v in record["journal"][0].items() if k != "system"}
    assert rr.validate({**record, "journal": [bare]}) == []
    with pytest.raises(rr.RunRecordError) as e:
        rr.append_stage(record, {"stage": "answers", "outcome": rr.OUTCOME_OK,
                                 "system": "panel"})
    assert "system" in str(e.value)
    # попытка COSMOS помечает себя сама
    assert rr.attempt_to_journal(attempt())["system"] == rr.SYSTEM_COSMOS


def test_модель_головы_называет_свою_систему():
    """Договор с CDPM 11.09.2026: строка модели головы несёт
    НЕОБЯЗАТЕЛЬНОЕ поле `system` того же словаря `SYSTEMS`, что у
    журнала. Поля нет — модель CDPM (так голова читалась прежде);
    слово вне словаря — отказ словами."""
    cosmos = {"role": "сертификатор", "model": "модель-сертификатора",
              "provider": "ollama", "channel": "local", "effort": None,
              "system": rr.SYSTEM_COSMOS}
    d = declaration()
    d["models"] = list(d["models"]) + [cosmos]
    record = rr.new_record(d)
    rows = record["declaration"]["models"]
    # модель CDPM из паспорта поля не получает вовсе — не «cdpm» пустотой
    assert "system" not in rows[0]
    assert rows[-1]["system"] == rr.SYSTEM_COSMOS
    assert rr.validate(record) == []
    assert "[cosmos]" in rr.summary(record)
    bad = declaration()
    bad["models"] = [{**cosmos, "system": "панель"}]
    with pytest.raises(rr.RunRecordError) as e:
        rr.new_record(bad)
    assert "system" in str(e.value)


def test_слова_о_неполноте_раскладки_в_голове():
    """`models_note` — слова о том, чьих моделей в голове нет и почему
    (COSMOS не ответил при объявлении). Поле необязательное: раскладка
    полна — его нет вовсе."""
    assert "models_note" not in rr.new_record(declaration())["declaration"]
    note = "COSMOS не опрошен при объявлении"
    record = rr.new_record(declaration(models_note=note))
    assert record["declaration"]["models_note"] == note
    assert rr.validate(record) == []
    assert note in rr.summary(record)


def test_новые_поля_не_сдвигают_отпечаток_прежних_записей():
    """Совместимость (договор с CDPM 11.09.2026): запись, объявленная ДО
    появления `system` и `models_note`, читается, проходит схему и даёт
    ТОТ ЖЕ отпечаток объявления — иначе все прежние записи разом стали
    бы «изменёнными после старта»."""
    old = rr.new_record(declaration())
    kept = old["declaration_sha256"]
    # голова прежней записи: ни поля системы у модели, ни слов о полноте
    head = json.loads(json.dumps(old["declaration"], ensure_ascii=False))
    assert all("system" not in m for m in head["models"])
    assert "models_note" not in head
    assert rr.declaration_sha256(head) == kept
    assert rr.validate(old) == []
    rr.append_stage(old, {"stage": "create", "outcome": rr.OUTCOME_OK})
    # названная система у модели голову МЕНЯЕТ — это другой рецепт
    other = dict(head)
    other["models"] = [{**m, "system": rr.SYSTEM_CDPM} for m in head["models"]]
    assert rr.declaration_sha256(other) != kept


def test_отпечаток_ответов_по_содержанию_один_на_обе_системы():
    """Пункт 12.6: отпечаток ревизии ответов считается по содержанию
    (номер вопроса → нормализованный ответ), не по байтам файла книги:
    порядок пар, лишние пробелы и пустые ответы не меняют его, другой
    ответ — меняет; вид пары — список либо словарь."""
    a = rr.answers_fingerprint([["Q-2", "да"], ["Q-1", "нет,  так"]])
    b = rr.answers_fingerprint([{"question_id": "Q-1", "answer": " нет, так "},
                                {"question_id": "Q-2", "answer": "да"},
                                {"question_id": "Q-3", "answer": ""}])
    assert a and a == b and len(a) == 64
    assert rr.answers_fingerprint([["Q-1", "нет, так"], ["Q-2", "нет"]]) != a
    assert rr.answers_fingerprint([]) is None
    assert rr.answers_fingerprint([["Q-9", "   "]]) is None
