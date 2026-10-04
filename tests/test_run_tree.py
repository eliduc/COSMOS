# -*- coding: utf-8 -*-
"""Дерево прогонов (vmp/run_tree.py + vmp/run_tree.schema.json): индекс
записей, путь от корня, сравнение ветвей, золотой прогон.

Основание — проект `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, раздел
7 «Дерево прогонов», решение владельца 6.3 и шаг 3 раскладки. Судится
то, ради чего дерево заведено:

  * записи находятся обходом ПЕРЕЧНЯ КОРНЕЙ, а не по зашитой раскладке
    хранения; негодная запись уходит в отказы со словами и дерева не
    рушит;
  * три ветви одного корня встают под один корень, и ребро каждой
    называет, чем она отличается (модели, решатели, параметры);
  * частичный прогон от пакета — ребро «продолжение» к прогону-
    источнику, даже когда корня он сам не назвал;
  * смена одного ответа анкеты — ветвь с пометкой «изменены ответы»
    (ответы тоже исходные документы);
  * сравнение называет ИЗМЕНЁННЫЕ поля объявления и сдвиги итогов, а
    счётчики метит «справочно»;
  * золотой прогон ставится только подписью (решение владельца 6.3):
    пустое «кто» — отказ; новые ветви сравниваются с золотым по
    умолчанию;
  * индекс сходится со своей схемой, а схема описывает ровно поля
    модуля — перебором в обе стороны.

Литералов среды в файле нет (сторож tests/test_no_environment_literals):
пути — только `tmp_path`, отпечатки строятся в самом тесте, служб не
поднимается, личности владельца нет.
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
from vmp import run_tree as rt                                    # noqa: E402

#: Отпечатки-пробы: строятся тестом, живых файлов не читают.
SHA_RULES = rr.sha256_text("документ правил дерева")
SHA_DATA = rr.sha256_text("книга данных дерева")
SHA_ANSWERS = rr.sha256_text("книга ответов, ревизия 1")
SHA_ANSWERS_2 = rr.sha256_text("книга ответов, ревизия 2")
SHA_CATALOG = rr.sha256_text("каталог шаблонов пробы")
SHA_PACKAGES = rr.sha256_text("перечень пакетов пробы")
SHA_PACKAGE = rr.sha256_text("пакет постановки пробы")
SHA_JAR = rr.sha256_text("сборка решателя пробы")


# ------------------------------------------------------------ помощники
def declaration(run_id: str, *, at: str = "2026-09-10T09:00:00+03:00",
                answers=(SHA_ANSWERS,), model: str = "модель-извлечения",
                engine: str = "timefold", spent_limit_s: float = 60,
                **over) -> dict:
    """Объявление прогона: те же имена, что у словаря записи.

    Строится словарём, а не сборкой паспорта: здесь судится ДЕРЕВО, и
    стенд остаётся без набора, каталога и служб (стык записи с паспортом
    судит tests/test_run_record.py)."""
    d = {
        "run_id": run_id,
        "created_at": at,
        "created_by": "стенд",
        "purpose": "регресс",
        "contour": {"name": "local", "services": {}},
        "airline": {"name": "Проба-дерево", "data_class": "synthetic",
                    "llm_scope": "any"},
        "rules_documents": [{"name": "ПРАВИЛА.docx", "kind": "docx",
                             "sha256": SHA_RULES, "size": 1024}],
        "data_files": [{"name": "ДАННЫЕ.xlsx", "sheets": 2, "rows": 40,
                        "sha256": SHA_DATA}],
        "answers": [{"revision": f"rev-{n:03d}", "sha256": sha,
                     "source": "книга набора", "at": None}
                    for n, sha in enumerate(answers, start=1)],
        "catalog": {"name": "каталог-пробы", "version": "0.4.0",
                    "fingerprint": SHA_CATALOG, "path": None},
        "catalog_extension": None,
        "bindings": {"catalog_version": "0.4.0",
                     "catalog_fingerprint": SHA_CATALOG, "path": None},
        "models": [{"role": "extractor", "model": model, "provider": "облако",
                    "channel": "api", "effort": "high"}],
        "solvers": [{"task": "rostering", "engine": engine,
                     "version": "2.0.0", "build_sha256": SHA_JAR,
                     "spent_limit_s": spent_limit_s,
                     "unimproved_spent_limit_s": 30, "moves": "полный",
                     "seed": 7, "step_limit": 1000,
                     "unimproved_step_limit": 200, "deterministic_time": None,
                     "accepted_uncovered": []}],
        "code": {"cdpm_commit": "0123456789ab", "cosmos_commit": "0123456789ab",
                 "runner_fingerprint": None},
        "libraries": {"python": "3.14.4", "packages_sha256": SHA_PACKAGES,
                      "packages": []},
        "planned_stages": ["create", "package", "ростер"],
        "stop_after": None,
        "expectations": {"kind": "манифест набора", "ref": "kit-manifest.json"},
        "answer_policy": "книга",
    }
    d.update(over)
    return d


def record(run_id: str, *, verdict: str = "допустимо", machine_s: float = 120,
           tokens_in: int = 1200, findings: int = 0, **over) -> dict:
    """Запись прогона целиком: объявление, два этапа журнала, итоги."""
    doc = rr.new_record(declaration(run_id, **over))
    rr.append_stage(doc, {
        "stage": "create", "outcome": rr.OUTCOME_OK, "actor": "стенд",
        "duration_machine_s": machine_s / 2,
        "counters": {"files": 1, "clauses": 12},
        "resources": {"models": [
            {"role": "extractor", "model": doc["declaration"]["models"][0]["model"],
             "provider": "облако", "calls": 2, "input_tokens": tokens_in,
             "output_tokens": 300, "counted": True, "note": None}],
            "solver_seconds": None}})
    rr.append_stage(doc, {
        "stage": "ростер", "outcome": rr.OUTCOME_OK, "actor": "стенд",
        "duration_machine_s": machine_s / 2,
        "counters": {"chains": 12, "findings": findings},
        "resources": {"models": [], "solver_seconds": machine_s / 2}})
    rr.finish(doc, {"verdicts": {"rostering": verdict},
                    "deliveries": [{"kind": "выгрузка", "path": "roster.csv",
                                    "sha256": SHA_DATA}]})
    return doc


def put(folder: Path, doc: dict) -> Path:
    """Запись в папку прогона. Папка заводится заранее: `run_record.save`
    по несуществующему пути пишет ФАЙЛ с таким именем, а у прогона файл
    записи лежит В папке."""
    folder.mkdir(parents=True, exist_ok=True)
    return rr.save(folder, doc)


def written(folder: Path, run_id: str, **over) -> Path:
    """Запись, положенная в свою папку прогона (как у панели и COSMOS)."""
    return put(folder / run_id, record(run_id, **over))


def three_branches(folder: Path) -> dict:
    """Три ветви одного корня: другая модель, другой движок, другой
    лимит времени решателя — ровно те ветви, о которых раздел 7."""
    written(folder, "ветвь-1", at="2026-09-10T09:00:00+03:00")
    written(folder, "ветвь-2", at="2026-09-10T10:00:00+03:00",
            model="модель-другая", verdict="недопустимо", machine_s=200,
            tokens_in=1500, findings=3)
    written(folder, "ветвь-3", at="2026-09-10T11:00:00+03:00",
            engine="cplex", spent_limit_s=180)
    return rt.build([folder])


# --------------------------------------------------------------- обход
def test_обход_находит_записи_в_перечне_корней(tmp_path):
    """Раскладок хранения индексатор не знает: ему называют корни, а он
    ищет записи рекурсивно. Три раскладки сегодняшних систем — папка
    прогона панели, папка нажатий COSMOS, папка выдачи раннера —
    находятся одним обходом."""
    panel = tmp_path / "прогоны" / "run-0001"
    cosmos = tmp_path / "данные" / "package-runs" / "пакет-а"
    runner = tmp_path / "выдача"
    for n, folder in enumerate((panel, cosmos, runner), start=1):
        put(folder, record(f"прогон-{n}"))
    found = rt.scan([tmp_path / "прогоны", tmp_path / "данные", runner])
    assert [x["path"] for x in found["records"]] == sorted(
        x["path"] for x in found["records"])
    assert {rr.load(x["path"])["declaration"]["run_id"]
            for x in found["records"]} == {"прогон-1", "прогон-2", "прогон-3"}
    assert found["refusals"] == []


def test_обход_не_идёт_глубже_названной_глубины(tmp_path):
    """Глубина ограничена: обход не имеет права уйти гулять по диску."""
    deep = tmp_path / "а" / "б" / "в" / "г" / "д"
    put(deep, record("глубокий"))
    assert rt.scan([tmp_path], max_depth=2)["records"] == []
    assert len(rt.scan([tmp_path], max_depth=5)["records"]) == 1


def test_негодная_запись_уходит_в_отказы_а_дерево_собирается(tmp_path):
    """Одна испорченная папка не имеет права скрыть дерево целиком:
    негодная запись — отказ со словами, а не падение обхода."""
    put(tmp_path / "добрая", record("добрый"))
    (tmp_path / "мусор").mkdir()
    (tmp_path / "мусор" / rr.RECORD_FILE).write_text("не json",
                                                     encoding="utf-8")
    broken = record("подменённый")
    broken["declaration"]["catalog"]["version"] = "9.9.9"   # голова подменена
    (tmp_path / "подмена").mkdir()
    (tmp_path / "подмена" / rr.RECORD_FILE).write_text(
        json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "пусто").mkdir()

    found = rt.scan([tmp_path, tmp_path / "нет-такого-корня"])
    assert len(found["records"]) == 1
    paths = {Path(r["path"]).parent.name: r["words"] for r in found["refusals"]}
    assert "не разобрана как JSON" in " ".join(paths["мусор"])
    assert "объявление изменено после старта" in " ".join(paths["подмена"])
    assert any("корня обхода нет" in " ".join(r["words"])
               for r in found["refusals"])

    tree = rt.index(found["records"], found["refusals"])
    assert len(rt.nodes(tree)) == 1
    assert len(tree["refusals"]) == 3
    assert "ОТКАЗЫ ОБХОДА" in rt.render(tree)


# --------------------------------------------------------------- дерево
def test_три_ветви_одного_корня_под_одним_корнем(tmp_path):
    """Корень — отпечатки входов, а не имя авиакомпании; ветви — те же
    входы с другим объявлением. Ребро каждой ветви называет, ЧЕМ она
    отличается: модели, решатели, параметры решателей."""
    tree = three_branches(tmp_path)
    assert tree["schema"] == rt.SCHEMA
    assert len(tree["roots"]) == 1
    root = tree["roots"][0]
    assert root["airline"] == "Проба-дерево"
    assert root["root_sha256"] == rr.root_sha256(
        [{"sha256": SHA_RULES}], [{"sha256": SHA_DATA}])
    assert [d["name"] for d in root["documents"]["rules"]] == ["ПРАВИЛА.docx"]
    assert root["documents"]["data"][0]["sha256"] == SHA_DATA
    assert [n["run_id"] for n in root["runs"]] == ["ветвь-1", "ветвь-2",
                                                   "ветвь-3"]
    assert tree["orphans"] == [] and tree["refusals"] == []

    first, second, third = root["runs"]
    assert first["edge"]["kind"] == rt.EDGE_ROOT
    assert first["edge"]["to"] is None
    assert second["edge"]["kind"] == rt.EDGE_SIBLING
    assert second["edge"]["to"] == "ветвь-1"
    assert second["edge"]["differs"] == ["модели"]
    assert third["edge"]["differs"] == ["решатели", "параметры решателей"]
    assert "те же входы, другое объявление" in third["edge"]["words"]

    # узел несёт объявление крупными частями и итоги
    assert second["models"]["extractor"]["model"] == "модель-другая"
    assert third["solvers"]["rostering"]["engine"] == "cplex"
    assert third["solvers"]["rostering"]["spent_limit_s"] == 180
    assert first["catalog"]["version"] == "0.4.0"
    assert first["bindings"]["catalog_version"] == "0.4.0"
    assert first["code"]["cdpm_commit"] == "0123456789ab"
    assert first["state"] == rr.STATE_DONE
    assert first["verdicts"] == {"rostering": "допустимо"}
    assert first["timing"]["machine_s"] == 120
    assert first["tokens"]["extractor/модель-извлечения"]["input_tokens"] == 1200
    assert first["tokens"]["extractor/модель-извлечения"]["counted"] is True
    assert first["counters"]["chains"] == 12
    assert first["path"].endswith(rr.RECORD_FILE)
    # адреса служб площадки в индекс не переносятся — остаётся имя
    assert first["contour"] == "local"


def test_частичный_прогон_от_пакета_ребро_продолжения(tmp_path):
    """Частичный прогон начинается со входа в COSMOS и обязан назвать
    происхождение: прогон-источник и отпечаток пакета. На дереве это
    ребро «продолжение от артефакта родителя» (разделы 1 и 7)."""
    written(tmp_path, "полный-1")
    written(tmp_path, "частичный-2", at="2026-09-10T12:00:00+03:00",
            lineage={"parent_run": None, "branch_of": "решатели",
                     "package_sha256": SHA_PACKAGE, "source_run": "полный-1",
                     "root_sha256": None, "root_answers_sha256": None},
            engine="ortools")
    tree = rt.build([tmp_path])
    node = rt.find_node(tree, "частичный-2")
    assert node["edge"]["kind"] == rt.EDGE_CONTINUATION
    assert node["edge"]["to"] == "полный-1"
    assert node["package_sha256"] == SHA_PACKAGE
    assert "продолжение от артефакта прогона полный-1" in node["edge"]["words"]
    assert "ветвь по: решатели" in node["edge"]["words"]
    # обе ветви — на одном корне: входы у них одни
    assert len(tree["roots"]) == 1
    assert len(tree["roots"][0]["runs"]) == 2


def test_прогон_без_корня_встаёт_на_дерево_родителя(tmp_path):
    """Хозяин записи мог не назвать корень входов (COSMOS считает
    частичный прогон от пакета). Родитель назван — прогон живёт на
    дереве родителя, а не в стороне; родителя нет — прогон честно
    показывается «без корня», а не прячется."""
    put(tmp_path / "родитель", record("родитель-1"))
    for run_id, source in (("сирота-2", "родитель-1"), ("сирота-3", "нет-его")):
        doc = record(run_id, at="2026-09-10T13:00:00+03:00")
        doc["declaration"]["lineage"]["root_sha256"] = None
        doc["declaration"]["lineage"]["root_answers_sha256"] = None
        doc["declaration"]["lineage"]["source_run"] = source
        doc["declaration_sha256"] = rr.declaration_sha256(doc["declaration"])
        assert rr.validate(doc) == []
        put(tmp_path / run_id, doc)

    tree = rt.build([tmp_path])
    assert [n["run_id"] for n in tree["roots"][0]["runs"]] == ["родитель-1",
                                                               "сирота-2"]
    assert [n["run_id"] for n in tree["orphans"]] == ["сирота-3"]
    assert "корень дерева не назван" in tree["orphans"][0]["edge"]["words"]
    assert "БЕЗ КОРНЯ" in rt.render(tree)


def test_смена_ответа_анкеты_даёт_ветвь_с_пометкой(tmp_path):
    """Ответы на анкеты — тоже исходные документы (раздел 7): смена
    одного ответа при тех же документах и данных — ветвь от прежнего
    корня с пометкой «изменены ответы»."""
    written(tmp_path, "ответы-1")
    written(tmp_path, "ответы-2", at="2026-09-10T14:00:00+03:00",
            answers=(SHA_ANSWERS_2,))
    tree = rt.build([tmp_path])
    root = tree["roots"][0]
    assert len(root["root_answers_sha256"]) == 2
    node = rt.find_node(tree, "ответы-2")
    assert node["edge"]["kind"] == rt.EDGE_ANSWERS
    assert node["edge"]["to"] == "ответы-1"
    assert "изменены ответы на анкету" in node["edge"]["words"]
    assert "ветвей по ответам 2" in rt.render(tree)


def test_путь_от_корня_и_соседние_ветви(tmp_path):
    """Карточка показывает путь от корня (входы → родитель → прогон) и
    соседние ветви."""
    tree = three_branches(tmp_path)
    path = rt.path_from_root(tree, "ветвь-3")
    assert path["root"]["root_sha256"] == tree["roots"][0]["root_sha256"]
    assert path["root"]["airline"] == "Проба-дерево"
    assert [n["run_id"] for n in path["runs"]] == ["ветвь-1", "ветвь-3"]
    assert [n["run_id"] for n in rt.siblings(tree, "ветвь-3")] == ["ветвь-1",
                                                                   "ветвь-2"]
    assert rt.path_from_root(tree, "ветвь-1")["runs"][0]["run_id"] == "ветвь-1"
    assert "ПУТЬ ОТ КОРНЯ" in rt.render_path(path)
    with pytest.raises(rt.RunTreeError) as e:
        rt.path_from_root(tree, "такого-нет")
    assert "в дереве нет" in str(e.value)


def test_номер_прогона_дважды_попадает_в_отказы(tmp_path):
    """Номер прогона один на обе системы: та же запись, найденная в двух
    местах, — повод сказать словами, а не тихо показать прогон дважды."""
    doc = record("двойник")
    put(tmp_path / "здесь", doc)
    put(tmp_path / "и-здесь", doc)
    tree = rt.build([tmp_path])
    assert len(rt.nodes(tree)) == 2
    assert any("встретился дважды" in " ".join(r["words"])
               for r in tree["refusals"])


# ------------------------------------------------------------ сравнение
def test_сравнение_называет_изменённые_поля_объявления(tmp_path):
    """У прогонов одного корня сравнение показывается рядом: что
    менялось в объявлении (модели, решатель, параметры)."""
    a, b = record("сравнение-1"), record(
        "сравнение-2", model="модель-другая", engine="cplex",
        spent_limit_s=180, at="2026-09-10T10:00:00+03:00")
    cmp = rt.compare(a, b)
    changed = {row["field"]: (row["a"], row["b"])
               for row in cmp["declaration"]["changed"]}
    assert changed["models.extractor.model"] == ("модель-извлечения",
                                                 "модель-другая")
    assert changed["solvers.rostering.engine"] == ("timefold", "cplex")
    assert changed["solvers.rostering.spent_limit_s"] == (60, 180)
    assert "catalog.version" in cmp["declaration"]["same"]
    assert "libraries.python" in cmp["declaration"]["same"]
    assert cmp["same_root"] is True
    words = rt.render_compare(cmp)
    assert "СРАВНЕНИЕ ПРОГОНОВ сравнение-1 → сравнение-2" in words
    assert "models.extractor.model: «модель-извлечения» → «модель-другая»" \
        in words
    assert "корень один" in words
    # роль шла разными моделями: расход второй не с чем сравнивать, и это
    # сказано словами, а не выдано за «не считается»
    only = cmp["results"]["tokens"]["extractor/модель-другая"]
    assert only["only_in"] == "b"
    assert "этой модели у прогона а нет" in words


def test_сравнение_итогов_вердикты_время_токены_счётчики():
    """И что изменилось в итогах: вердикты, время, расход токенов,
    строки сверки. Счётчики метятся «справочно» — число гуляет по
    чтению модели (раздел 3.3 проекта)."""
    a = record("итоги-1")
    b = record("итоги-2", verdict="недопустимо", machine_s=200,
               tokens_in=1500, findings=3)
    cmp = rt.compare(a, b)
    res = cmp["results"]
    assert res["verdicts"]["rostering"] == {"a": "допустимо",
                                            "b": "недопустимо", "same": False}
    assert res["timing"]["machine_s"] == {"a": 120, "b": 200, "delta": 80}
    tokens = res["tokens"]["extractor/модель-извлечения"]
    assert tokens["a"]["input_tokens"] == 1200
    assert tokens["delta"]["input_tokens"] == 300
    assert tokens["counted"] is True
    assert res["counters"]["findings"] == {"a": 0, "b": 3, "delta": 3,
                                           "note": "справочно"}
    words = rt.render_compare(cmp)
    assert "вердикт rostering: «допустимо» → «недопустимо»  — РАСХОЖДЕНИЕ" \
        in words
    assert "время машины: 120 → 200 (+80)" in words
    assert "справочно — число гуляет по чтению модели" in words


def test_сравнение_разных_корней_говорит_об_этом():
    """Сравнение прогонов на РАЗНЫХ входах законно, но человек обязан
    видеть, что входы разные."""
    a = record("вход-1")
    b = record("вход-2", rules_documents=[{"name": "ИНОЕ.docx", "kind": "docx",
                                           "sha256": SHA_ANSWERS_2,
                                           "size": 10}])
    cmp = rt.compare(a, b)
    assert cmp["same_root"] is False
    assert "КОРНИ РАЗНЫЕ" in rt.render_compare(cmp)


def test_расход_модели_без_учёта_помечен_не_считается():
    """Канал без учёта токенов (командная строка) честно помечается «не
    считается»: половина расхода, выданная за целое, хуже молчания."""
    doc = rr.new_record(declaration("без-учёта"))
    rr.append_stage(doc, {"stage": "create", "outcome": rr.OUTCOME_OK,
                          "resources": {"models": [
                              {"role": "certifier", "model": "модель-цли",
                               "provider": "облако", "calls": 1,
                               "input_tokens": None, "output_tokens": None,
                               "counted": False,
                               "note": "канал cli: учёта нет"}],
                              "solver_seconds": None}})
    rr.finish(doc)
    node = rt.node(doc)
    assert node["tokens"]["certifier/модель-цли"]["counted"] is False
    cmp = rt.compare(doc, doc)
    assert "не считается" in rt.render_compare(cmp)


def test_узел_называет_системы_отметившиеся_в_записи():
    """Узел несёт перечень систем-летописцев журнала (`journal[].system`):
    по нему общий экран прогонов обеих систем говорит, кто из систем в
    этой записи уже писал. Повторов в перечне нет, порядок — словаря
    записи, а единица без имени системы (записи шага 1) в перечень
    ничего не добавляет: выдумывать ей систему нельзя."""
    doc = rr.new_record(declaration("кто-писал"))
    for system in (rr.SYSTEM_COSMOS, rr.SYSTEM_CDPM, rr.SYSTEM_COSMOS, None):
        rr.append_stage(doc, {"stage": "create", "substage": str(system),
                              "outcome": rr.OUTCOME_OK, "system": system})
    assert rt.node(doc)["systems"] == [rr.SYSTEM_CDPM, rr.SYSTEM_COSMOS]
    # Запись без единого имени системы — пустой перечень, а не догадка.
    assert rt.node(rr.new_record(declaration("без-систем")))["systems"] == []


# -------------------------------------------------------------- золотой
def test_золотой_ставится_только_подписью():
    """Решение владельца 6.3: после приёмки на Prod система запрашивает
    владельца, и признак ставится только по его подтверждению. Пустое
    «кто» и пустые слова — отказ."""
    doc = record("золотой-1")
    with pytest.raises(rr.RunRecordError) as e:
        rt.mark_golden(doc, by="", words="прогон принят на Prod")
    assert "не сказано, КТО подтвердил" in str(e.value)
    with pytest.raises(rr.RunRecordError) as e:
        rt.mark_golden(doc, by="владелец", words="   ")
    assert "не сказано, КАКИМИ СЛОВАМИ" in str(e.value)
    assert doc["results"]["golden"]["confirmed"] is False

    rt.mark_golden(doc, by="владелец", words="прогон принят на Prod",
                   at="2026-09-11T08:00:00+03:00")
    assert doc["results"]["golden"] == {"confirmed": True, "by": "владелец",
                                        "at": "2026-09-11T08:00:00+03:00",
                                        "words": "прогон принят на Prod"}
    assert doc["results"]["state"] == rr.STATE_DONE
    assert rr.validate(doc) == []

    rt.unmark_golden(doc)
    assert doc["results"]["golden"]["confirmed"] is False
    assert doc["results"]["golden"]["by"] is None
    assert rr.validate(doc) == []


def test_незавершённый_прогон_золотым_не_бывает():
    """Образцом, с которым сравнивают новые ветви, незаконченный прогон
    быть не может."""
    doc = rr.new_record(declaration("на-ходу"))
    rr.append_stage(doc, {"stage": "create", "outcome": rr.OUTCOME_OK})
    with pytest.raises(rr.RunRecordError) as e:
        rt.mark_golden(doc, by="владелец", words="пусть будет")
    assert "только завершённый прогон" in str(e.value)


def test_новая_ветвь_сравнивается_с_золотым_по_умолчанию(tmp_path):
    """Золотой прогон — узел дерева с признаком; новые ветви того же
    корня сравниваются с ним по умолчанию (раздел 7 проекта)."""
    tree = three_branches(tmp_path)
    assert rt.compare_with_golden(tree, "ветвь-3") is None

    gold = rr.load(tmp_path / "ветвь-1")
    rt.mark_golden(gold, by="владелец", words="принят на Prod",
                   at="2026-09-11T08:00:00+03:00")
    rr.save(tmp_path / "ветвь-1", gold)
    tree = rt.build([tmp_path])
    assert rt.golden_node(tree, tree["roots"][0]["root_sha256"])["run_id"] \
        == "ветвь-1"

    cmp = rt.compare_with_golden(tree, "ветвь-2")
    assert cmp["kind"] == rt.COMPARE_GOLDEN
    assert (cmp["a"], cmp["b"]) == ("ветвь-1", "ветвь-2")
    assert cmp["results"]["verdicts"]["rostering"]["same"] is False
    assert rt.compare_with_golden(tree, "ветвь-1") is None
    words = rt.render(tree)
    assert "ЗОЛОТОЙ ПРОГОН: владелец" in words
    assert "золотых 1" in words


def test_сравнение_с_предыдущим_прогоном_того_же_корня(tmp_path):
    """Сдвиги чисел против предыдущего прогона (раздел 3.3): предыдущий
    — соседняя ветвь того же корня, объявленная раньше."""
    tree = three_branches(tmp_path)
    cmp = rt.compare_with_previous(tree, "ветвь-3")
    assert (cmp["a"], cmp["b"]) == ("ветвь-2", "ветвь-3")
    assert cmp["kind"] == rt.COMPARE_PREVIOUS
    assert rt.compare_with_previous(tree, "ветвь-1") is None
    assert rt.render_compare(None).startswith("сравнивать не с кем")


# -------------------------------------------------------------- хранение
def test_индекс_ложится_файлом_и_читается(tmp_path):
    """Индекс — снимок дерева: он сохраняется атомарно и читается
    обратно тем же."""
    tree = three_branches(tmp_path / "прогоны")
    (tmp_path / "снимок").mkdir()
    path = rt.save_index(tmp_path / "снимок", tree)
    assert path.name == rt.INDEX_FILE
    assert not list(path.parent.glob("*.tmp"))
    assert rt.load_index(tmp_path / "снимок") == tree
    assert rt.load_index(path) == tree
    # путь файлом — тоже путь: индекс кладут и рядом с прогонами
    assert rt.load_index(rt.save_index(tmp_path / "своё-имя.json", tree)) == tree
    with pytest.raises(rt.RunTreeError) as e:
        rt.load_index(tmp_path / "нет-такого")
    assert "индекса дерева нет" in str(e.value)
    chuzhoy = tmp_path / "чужое" / rt.INDEX_FILE
    chuzhoy.parent.mkdir(parents=True)
    chuzhoy.write_text(json.dumps({"schema": "иное"}), encoding="utf-8")
    with pytest.raises(rt.RunTreeError) as e:
        rt.load_index(chuzhoy)
    assert "это не индекс дерева прогонов" in str(e.value)


# --------------------------------------------------- схема и модуль
def test_индекс_сходится_со_своей_схемой(tmp_path):
    """Собранное дерево судится схемой индекса: расхождений нет."""
    tree = three_branches(tmp_path)
    doc = rr.load(tmp_path / "ветвь-1")
    rt.mark_golden(doc, by="владелец", words="принят на Prod")
    rr.save(tmp_path / "ветвь-1", doc)
    (tmp_path / "мусор").mkdir()
    (tmp_path / "мусор" / rr.RECORD_FILE).write_text("не json",
                                                     encoding="utf-8")
    tree = rt.build([tmp_path])
    assert rt.validate_index(tree) == []
    assert rt.validate_index({"schema": rt.SCHEMA})   # неполный — слова есть


def test_схема_описывает_ровно_поля_модуля():
    """Перебором в обе стороны: каждое поле, которое пишет модуль,
    описано схемой дерева, и схема не описывает полей, которых модуль не
    пишет."""
    parts = {"": rt.TREE_FIELDS}
    parts.update(rt.NESTED_FIELDS)
    for path, fields in parts.items():
        described = set(rt.schema_properties(path))
        assert described == set(fields), (path, sorted(described ^ set(fields)))
    with pytest.raises(rt.RunTreeError) as e:
        rt.schema_properties("roots[].ветви")
    assert "схема дерева не описывает поле" in str(e.value)


def test_схема_и_модуль_называют_одни_слова():
    """Слова дерева (вид ребра, чем ветвь отличается, поля токенов)
    живут в схеме, и модуль повторяет их без расхождений."""
    edge = rt.schema_properties("roots[].runs[].edge")
    assert tuple(edge["kind"]["enum"]) == rt.EDGES
    assert tuple(edge["differs"]["items"]["enum"]) == rt.DIFFERS
    assert rt.schema_properties("")["schema"]["const"] == rt.SCHEMA
    defs = rt.schema_document()["$defs"]
    assert tuple(defs["tokens_row"]["required"]) == rt.TOKENS_FIELDS
    # решатель узла — те же параметры, что в словаре записи, без задачи
    assert set(defs["solver_row"]["required"]) == set(
        rr.NESTED_FIELDS["declaration.solvers[]"]) - {"task"}


def test_узел_не_несёт_полей_мимо_схемы(tmp_path):
    """Обход готового индекса: у каждого словаря ключи описаны схемой по
    его пути. Свободные словари (модели, решатели, токены, счётчики,
    вердикты) полей не объявляют — их обход не трогает."""
    tree = three_branches(tmp_path)

    def walk(value, path):
        if isinstance(value, dict):
            described = rt.schema_properties(path)
            if not described:
                return
            assert set(value) <= set(described), (path, sorted(value))
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for item in value:
                walk(item, path + "[]")

    walk(tree, "")


def test_узел_собирается_только_из_записи():
    with pytest.raises(rt.RunTreeError) as e:
        rt.node({"это": "не запись"})
    assert "это не запись о прогоне" in str(e.value)


# ------------------------------------------------------ командная строка
def test_командная_строка_собирает_индекс(tmp_path, capsys):
    """`index <корень>… --out` — собрать дерево, показать словами,
    положить файлом."""
    three_branches(tmp_path / "прогоны")
    out = tmp_path / "снимок" / rt.INDEX_FILE
    assert rt.main(["index", str(tmp_path / "прогоны"), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "ДЕРЕВО ПРОГОНОВ" in text
    assert "ветвь-2" in text and "ИНДЕКС ЗАПИСАН" in text
    assert rt.load_index(out)["roots"][0]["runs"][0]["run_id"] == "ветвь-1"

    # отказ обхода виден и в коде возврата, и словами
    (tmp_path / "прогоны" / "мусор").mkdir()
    (tmp_path / "прогоны" / "мусор" / rr.RECORD_FILE).write_text(
        "не json", encoding="utf-8")
    assert rt.main(["index", str(tmp_path / "прогоны")]) == 1
    assert "ОТКАЗЫ ОБХОДА" in capsys.readouterr().out
    # записей нет вовсе — отказ
    assert rt.main(["index", str(tmp_path / "пусто")]) == 2
    assert "записей о прогонах в названных корнях нет" in capsys.readouterr().out


def test_командная_строка_показывает_путь_и_сравнения(tmp_path, capsys):
    """`path <номер> --index`, `compare <запись> <запись>`."""
    tree = three_branches(tmp_path)
    gold = rr.load(tmp_path / "ветвь-1")
    rt.mark_golden(gold, by="владелец", words="принят на Prod")
    rr.save(tmp_path / "ветвь-1", gold)
    index_path = rt.save_index(tmp_path / "снимок", rt.build([tmp_path]))

    assert rt.main(["path", "ветвь-2", "--index", str(index_path)]) == 0
    text = capsys.readouterr().out
    assert "ПУТЬ ОТ КОРНЯ" in text
    assert "соседние ветви (2): ветвь-1, ветвь-3" in text
    assert "СРАВНЕНИЕ ПРОГОНОВ ветвь-1 → ветвь-2" in text

    assert rt.main(["compare", str(tmp_path / "ветвь-1"),
                    str(tmp_path / "ветвь-3")]) == 0
    assert "solvers.rostering.engine" in capsys.readouterr().out
    assert rt.main(["path", "нет-такого", "--index", str(index_path)]) == 2
    assert "ОТКАЗ:" in capsys.readouterr().out
    assert tree["roots"][0]["runs"][0]["run_id"] == "ветвь-1"


def test_командная_строка_подтверждает_золотой(tmp_path, capsys):
    """`golden <запись> --by --words` — подпись владельца; без «кто» —
    отказ и запись не меняется."""
    path = written(tmp_path, "золотой-cli")
    assert rt.main(["golden", str(path), "--words", "принят на Prod"]) == 2
    assert "не сказано, КТО подтвердил" in capsys.readouterr().out
    assert rr.load(path)["results"]["golden"]["confirmed"] is False

    assert rt.main(["golden", str(path), "--by", "владелец",
                    "--words", "принят на Prod"]) == 0
    text = capsys.readouterr().out
    assert "прогон назван золотым" in text
    saved = rr.load(path)
    assert saved["results"]["golden"]["by"] == "владелец"
    assert rr.validate(saved) == []

    assert rt.main(["golden", str(path), "--unmark"]) == 0
    assert "признак золотого прогона снят" in capsys.readouterr().out
    assert rr.load(path)["results"]["golden"]["confirmed"] is False
