# -*- coding: utf-8 -*-
"""K01-барьер волны 17а (контракт П0 §3.2 и §9, 12.09.2026): полнота
семьи считается ОДНИМ расчётом по контракту каталога, а не наличием
вопроса анкеты. Обязательный параметр использованной семьи, у которого
нет ни ячейки посадки со ЗНАЧЕНИЕМ, читаемым в единице записи каталога,
ни применённого БЕЗУСЛОВНОГО отказа, держит готовность затронутых задач
ниже «в», краснит гейт производителя и отказывает выпуску кодом
`family-incomplete` — с вопросом в анкете и без него одинаково.

Смена контракта семьи судится СОДЕРЖАНИЕМ записи (правки по
неавтор-ревью 12.09.2026, В-1): отпечаток посадки разрешается в
выпущенную версию каталога, и запись семьи той версии сравнивается с
действующей; разошлись — «перепосадка требуется» ДОКАЗАНА (её и только
её объявляет нарушением валидатор свода), совпали — «совместимо», нет
такого выпуска — «совместимость не доказана» (обычная недостача со
словами, путь ответов анкеты открыт).

Перепосадку доказывают ТОЛЬКО две клаузы §12.15 (решение ведущего
12.09.2026 по находке К-2 неавтор-ревью): (б) у параметра, объявленного
ОБЕИМИ записями, сменились единица, отношение или уточнитель, и (в)
несомое посадкой имя из записи убрано — ответом анкеты такой свод не
лечится, и вопрос по такой семье анкета больше не ставит. Клауза (а) —
запись стала требовать БОЛЬШЕ, чем несёт посадка (наборы переписаны
так, что посадка, полная по прежней записи, ни одного набора
действующей не покрывает), — перепосадкой НЕ является: смысл уже
посаженных значений тот же, недостаёт соседнего, и §12.12 п. 1 считает
это ОБЫЧНОЙ недостачей, которую держат готовность, гейт и рубеж
выпуска, а лечит ответ анкеты. Прежний закон объявлял (а) доказанной
перепосадкой — и ответ анкеты её всё равно лечил: посадка, объявленная
негодной, выпускалась как годная (дыра К-2). Чистое ослабление (новая
запись требует МЕНЬШЕ) не держит ничего.

Полный K01 (`family_contract` с отпечатком записи, ключ манифеста
`families`, приёмный пересчёт COSMOS, общий расчёт в
`template_catalog/completeness.py`) — волна 18: здесь не проверяется."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

import catalog_copies as cc
from cdpm import catalog_bridge as bridge
from cdpm import export_task_package as tp
from cdpm import family_params as fp
from cdpm import model as m
from cdpm.gates import gate_task_package as gate

REV = m.registry_fingerprint()
QID = "Q-3b-параметр-rest_floors-min_rest_outstation"
#: Вопрос о параметре, ДОБАВЛЕННОМ записью 0.6.0 (клауза (а) §12.15).
QNIGHT = "Q-3b-параметр-rest_floors-min_rest_night"
#: Вопрос о первом параметре записи — им проверяется клауза (в).
QHOME = "Q-3b-параметр-rest_floors-min_rest_home"
#: Независимый эталон (не из проверяемой функции): запись `rest_floors`
#: каталога 0.5.0 объявляет ДВА параметра и не объявляет `param_sets`,
#: значит обязательны оба (закон fail-closed).
REST_REQUIRED = ("min_rest_home", "min_rest_outstation")

ROOT = Path(__file__).resolve().parents[1]
#: Отпечатки ВЫПУЩЕННЫХ версий — прямо из журнала каталога (независимый
#: эталон, не из проверяемого кода).
RELEASES = {v["version"]: v["fingerprint"] for v in json.loads(
    (ROOT / "catalog" / "versions.json").read_text(encoding="utf-8"))["versions"]}
#: 0.4.0 — выпуск, которым посажены наборы H0: запись `rest_floors` там
#: ТА ЖЕ, что в 0.5.0 (сверено ниже отдельным стендом) — совместимо.
REV_040 = RELEASES["0.4.0"]
#: 0.2.0 — выпуск, где у `duty_limits` НЕ объявлены `param_sets` (нужны
#: все три параметра), а в 0.5.0 объявлены три набора по одному: набор
#: разошёлся ОСЛАБЛЕНИЕМ — действующая запись требует меньше, посадка её
#: покрывает, и перепосадкой это не считается (решение ведущего
#: 12.09.2026; ужесточение судится тем же законом и держит).
REV_020 = RELEASES["0.2.0"]
#: Отпечаток, которого нет ни у одного выпуска: прежней записи семьи
#: взять неоткуда — «совместимость не доказана» (В-1).
REV_UNKNOWN = "0" * 64


def _ready_doc():
    """Свод уровня «в»: чистая жёсткая, взвешенная мягкая, критерий
    пейринга, отказ от оптимизации ростеринга."""
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


def _landed(rid="R-F", zone="BOTH", kind="HARD", family="rest_floors",
            cells=None, rev=REV):
    """Правило-носитель с посадкой A на `rest_floors`; по умолчанию задан
    ОДИН обязательный параметр из двух (случай VN-023 набора Н0-B)."""
    return {"id": rid, "title": "Полы отдыха",
            "statement": "отдых дома не менее 12:00", "zone": zone,
            "kind": kind, "sources": [{"sid": "S1", "anchor": "п.1"}],
            "template_fit": {"category": "A", "registry_rev": rev,
                             "family": family,
                             "params": cells if cells is not None else
                             {"min_rest_home": {"value": 720,
                                                "quote": "12:00"}}}}


FULL_CELLS = {"min_rest_home": {"value": 720, "quote": "12:00"},
              "min_rest_outstation": {"value": 600, "quote": "10:00"}}


def _doc_with(*rules):
    doc = _ready_doc()
    doc["rules"].extend(rules)
    return doc


def _run_dir(tmp_path, doc, name="run-9101"):
    run = tmp_path / name
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (run / "extract" / "svod.md").write_text("# свод\n", encoding="utf-8")
    return run


def _repack(files: dict, doc: dict) -> dict:
    """Пакет с подменённым model.json и пересчитанными sha256: гейт судит
    БАРЬЕР, а не порчу байтов."""
    out = dict(files)
    out["model.json"] = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    manifest = json.loads(out["manifest.json"])
    for row in manifest.get("files", []):
        if row.get("name") in out and "sha256" in row:
            row["sha256"] = hashlib.sha256(out[row["name"]]).hexdigest()
    out["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False).encode("utf-8")
    return out


def _gate_on(tmp_path, good_doc, bad_doc, name="k01"):
    """Пакет выпущен по ЗАКОННОМУ своду, а в нём подменён model.json:
    манифест по-прежнему разрешает режимы — это и есть подделка
    производного «зелёного» (класс X-01 карты предпроверки)."""
    files = tp.build_package(_run_dir(tmp_path, good_doc, f"run-{name}"))
    pkg = tmp_path / f"{name}.zip"
    pkg.write_bytes(tp.write_zip(_repack(files, bad_doc)))
    return gate.check(pkg)


# --------------------------------------------------------------------
# 3.7 K01: incomplete_no_question / incomplete_question_open / complete
# --------------------------------------------------------------------

def test_incomplete_no_question_holds_readiness_and_names_family_and_param():
    """`incomplete_no_question`: недостача существует БЕЗ вопроса —
    вопрос не является ни условием барьера, ни предварительным вызовом."""
    doc = _doc_with(_landed())
    assert [q["id"] for q in doc["questions"]
            if fp.is_param_question(q["id"])] == []
    got = m.readiness(doc)
    assert got["state"] == "а"
    words = ("параметр min_rest_outstation семьи rest_floors не задан и "
             "не спрошен")
    assert words in got["pairing_open"] and words in got["rostering_open"]
    assert words in got["label"]
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE
    assert cell["given"] == ["min_rest_home"]
    assert cell["missing"] == ["min_rest_outstation"]
    assert cell["asked"] == {"min_rest_outstation": None}
    assert sorted(REST_REQUIRED) == sorted(cell["given"] + cell["missing"])


def test_incomplete_question_open_gives_the_same_verdict():
    """`incomplete_question_open`: анкета — СЛЕДСТВИЕ расчёта; вопрос
    открыт, значения нет — вердикт тот же, слова называют вопрос."""
    doc = _doc_with(_landed())
    assert fp.ensure_family_param_questions(doc) == [QID]
    got = m.readiness(doc)
    assert got["state"] == "а"
    words = (f"параметр min_rest_outstation семьи rest_floors спрошен "
             f"({QID}), ответа нет")
    assert words in got["rostering_open"]
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE
    assert cell["asked"] == {"min_rest_outstation": QID}


def test_complete_family_is_ready_and_exports(tmp_path):
    """`complete`: оба обязательных параметра заданы ячейками посадки —
    готовность «в», выпуск идёт, гейт зелёный."""
    doc = _doc_with(_landed(cells=copy.deepcopy(FULL_CELLS)))
    assert fp.family_completeness(doc)["rest_floors"]["verdict"] \
        == fp.VERDICT_COMPLETE
    assert m.validate_model(doc) == []
    assert m.readiness(doc)["state"] == "в"
    files = tp.build_package(_run_dir(tmp_path, doc))
    pkg = tmp_path / "ok.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(pkg)
    assert problems == [], problems


def test_applied_unconditional_decline_closes_the_param(tmp_path):
    """Второй законный исход: решение авиакомпании «не применяется»
    закрывает обязательный параметр — семья полна без значения."""
    doc = _doc_with(_landed())
    assert fp.ensure_family_param_questions(doc) == [QID]
    q = next(x for x in doc["questions"] if x["id"] == QID)
    got = fp.apply_param_answer(doc, q, "Не применяется", 2)
    assert got["applied"], q.get("answer")
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["declined"] == ["min_rest_outstation"]
    assert cell["missing"] == [] and cell["verdict"] == fp.VERDICT_COMPLETE
    assert m.readiness(doc)["state"] == "в"


# --------------------------------------------------------------------
# Гейт, рубеж выпуска и равенство вердиктов (3.7 K01, класс X-01)
# --------------------------------------------------------------------

def test_release_refuses_family_incomplete_with_items_and_words(tmp_path):
    """Рубеж выпуска: код `family-incomplete`, в `items` —
    «семья/параметр», в словах — та же недостача и что делать."""
    doc = _doc_with(_landed())
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc))
    assert e.value.code == tp.FAMILY_INCOMPLETE == "family-incomplete"
    assert e.value.items == ["rest_floors/min_rest_outstation"]
    assert "параметр min_rest_outstation семьи rest_floors" in str(e.value)
    assert "не применяется" in str(e.value)          # что делать


def test_producer_gate_is_red_when_the_manifest_still_allows_the_mode(
        tmp_path):
    """Гейт производителя судит model.json пакета ТЕМ ЖЕ расчётом:
    манифест разрешает режимы, а семья неполна — красный теми же
    словами и с тем же кодом."""
    good = _doc_with(_landed(cells=copy.deepcopy(FULL_CELLS)))
    bad = copy.deepcopy(good)
    bad["rules"][-1]["template_fit"]["params"].pop("min_rest_outstation")
    problems, counters = _gate_on(tmp_path, good, bad)
    words = m.readiness_barriers(bad)[0]["words"]
    assert any(words in p for p in problems), problems
    assert any("family-incomplete" in p for p in problems), problems
    assert "barriers_held" not in counters


def test_verdicts_of_readiness_validate_gate_and_release_agree(tmp_path):
    """Равенство вердиктов (3.7 K01): готовность, гейт и рубеж выпуска
    говорят об одной и той же семье и параметре одними словами.

    Валидатор к ним НЕ присоединяется у обычной недостачи (поправка
    контракта §12.12, регресс В-1 неавтор-ревью: непустой
    `validate_model` закрывает путь ответов анкеты — `webui`/`answers`
    судят пробный свод тем же валидатором, — то есть закрывает ровно
    тот путь, которым недостача и лечится)."""
    doc = _doc_with(_landed())
    words = m.readiness_barriers(doc)[0]["words"]
    assert words in m.readiness(doc)["rostering_open"]
    assert m.validate_model(doc) == []
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc))
    assert e.value.code == tp.FAMILY_INCOMPLETE and words in str(e.value)
    good = _doc_with(_landed(cells=copy.deepcopy(FULL_CELLS)))
    problems, _ = _gate_on(tmp_path, good, doc, name="k01-eq")
    assert any(words in p for p in problems), problems


# --------------------------------------------------------------------
# Смена контракта семьи (3.7 K01 catalog_changed / old_run_new_required
# param; классы K01b-04, K01b-05)
# --------------------------------------------------------------------

def test_pure_loosening_of_the_record_is_not_a_relanding(tmp_path):
    """`catalog_changed`, ОСЛАБЛЕНИЕ (закон уточнён решением ведущего
    12.09.2026, открытый вопрос 2): правило посажено выпуском 0.2.0, где
    у `duty_limits` наборов нет вовсе — нужны все три параметра; в 0.5.0
    объявлены три набора по одному, то есть действующая запись требует
    МЕНЬШЕ, и посадка с одним параметром её набор покрывает. Ломаться
    нечему: «совместимо», обычный выпуск.

    Прежний закон («любая разница наборов — перепосадка») останавливал
    такие прогоны требованием повторить их по текущему каталогу —
    ложный отказ: ужесточения не было. Ужесточение ловит соседний тест
    `test_content_change_of_the_record_is_a_proven_reland`."""
    doc = _doc_with(_landed(rid="R-D", family="duty_limits", rev=REV_020,
                            cells={"max_pairing_days": {"value": 3,
                                                        "quote": "3 суток"}}))
    cell = fp.family_completeness(doc)["duty_limits"]
    assert cell["missing"] == []                 # по действующей записи полна
    assert cell["contract_state"] == fp.CONTRACT_COMPATIBLE
    assert cell["verdict"] == fp.VERDICT_COMPLETE
    assert cell["contract"]["landed_revs"] == [REV_020]
    assert cell["contract"]["catalog_fingerprint"] == REV
    assert cell["contract"]["differences"] == [] and cell["problems"] == []
    assert m.validate_model(doc) == []
    assert m.readiness(doc)["state"] == "в"
    assert tp.build_package(_run_dir(tmp_path, doc))


def test_a_loosening_that_still_leaves_the_landing_short_stays_open(tmp_path):
    """Граница того же закона: ослабление не делает недостачу
    перепосадкой. Носитель `duty_limits` посажен выпуском 0.2.0 и не
    несёт НИ ОДНОГО параметра: наборы разошлись (0.2.0 требовал все три,
    0.5.0 — любой один), но посадка не была полной и по прежней записи —
    ломаться нечему. Состояние контракта «совместимо», вердикт
    «неполна»: путь ответов анкеты открыт, выпуск отказывает обычной
    недостачей, и совета «повторите прогон по текущему каталогу» в
    отказе нет."""
    doc = _doc_with(_landed(rid="R-D", family="duty_limits", rev=REV_020,
                            cells={}))
    cell = fp.family_completeness(doc)["duty_limits"]
    assert cell["contract_state"] == fp.CONTRACT_COMPATIBLE
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE
    assert cell["given"] == [] and cell["missing"] == ["max_duty_duration"]
    assert cell["contract"]["differences"] == []
    # Валидатор молчит О ПЕРЕПОСАДКЕ (пустую посадку он бракует своим
    # правилом формы — это другая претензия и другой путь).
    assert [p for p in m.validate_model(doc)
            if "перепосадка требуется" in p] == []
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-loose"))
    assert e.value.code == tp.FAMILY_INCOMPLETE
    assert "повторите прогон по текущему каталогу" not in str(e.value)


def test_question_does_not_heal_an_incompatible_contract(
        tmp_path, monkeypatch, _fresh_bridge):
    """K01b-04: ответ анкеты не превращает ДОКАЗАННУЮ перепосадку в
    обычную недостачу — «перепосадка требуется» остаётся и после ответа
    значением: ответ садится в ячейку, а разошедшийся контракт записи
    от этого не сходится. Доказанность берётся СМЕНОЙ ЕДИНИЦЫ уже
    посаженного параметра (клауза (б) §12.15: «600» в минутах и «600» в
    счёте — разные правды).

    Правка по К-2 (решение ведущего 12.09.2026, п. 6): раз лечения нет,
    анкета по такой семье вопрос БОЛЬШЕ НЕ СТАВИТ — обещать
    авиакомпании исход, которого не будет, нечестно. Вопрос в своде
    берётся тот, что был задан ЗАКОННО, пока запись только ужесточалась
    (клауза (а)): живой случай — каталог сменился между изданием анкеты
    и ответом.

    Минус (на прежнем коде): `ensure_family_param_questions` ставила
    вопрос и при доказанной перепосадке — краснеет утверждение о пустом
    перечне созданных вопросов."""
    # 1) Каталог 0.6.0 ужесточил наборы — вопрос задан ЗАКОННО.
    _variant(tmp_path, monkeypatch, {"rest_floors": _add_param},
             name="cat-a")
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    assert fp.ensure_family_param_questions(doc) == [QNIGHT]
    # 2) Следующий каталог сменил у ПОСАЖЕННОГО параметра единицу.
    _variant(tmp_path, monkeypatch, {"rest_floors": _harden}, name="cat-b")
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert cell["verdict"] == fp.VERDICT_RELAND
    # Новых вопросов анкета не ставит, а прежний не снимает кодом:
    # снять его словами «параметр задан сводом» было бы неправдой.
    assert fp.ensure_family_param_questions(doc) == []
    q = next(x for x in doc["questions"] if x["id"] == QNIGHT)
    assert m.is_open_question(q)
    # 3) Ответ на прежний вопрос садится в ячейку — и не лечит.
    assert fp.apply_param_answer(doc, q, "600 минут", 2)["applied"]
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["missing"] == []           # по действующей записи полна
    assert cell["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert cell["verdict"] == fp.VERDICT_RELAND
    assert any("изменилась единица" in d
               for d in cell["contract"]["differences"])
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-heal"))
    assert e.value.code == tp.FAMILY_INCOMPLETE
    assert "повторите прогон по текущему каталогу" in str(e.value)


def test_the_live_panel_path_refuses_an_answer_to_a_proven_reland(
        tmp_path, monkeypatch, _fresh_bridge):
    """ЖИВОЙ путь панели, клауза (б): стадия применения ответов
    заканчивается `webui._sync_canon` — переизданием канонической
    модели прогона ПОД КОНТРАКТОМ. Свод с доказанной перепосадкой её не
    проходит: ответ, применённый в ячейку, НА ДИСК не попадает
    (`model.json` остаётся байт в байт прежним), слова отказа называют
    различие и путь — повтор прогона по текущему каталогу, — а оба
    входа выпуска отвечают отказом `family-incomplete` (409 ручки).

    Минус (на прежнем коде): этим же путём проходила и клауза (а) —
    ответ её ЛЕЧИЛ, канон переписывался, выпуск отдавал 200, и посадка,
    объявленная негодной, уезжала заказчику годной (дыра К-2)."""
    from cdpm import webui
    _variant(tmp_path, monkeypatch, {"rest_floors": _add_param},
             name="cat-a")
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    assert fp.ensure_family_param_questions(doc) == [QNIGHT]
    run = _run_dir(tmp_path, doc, "run-live-b")
    before = (run / "extract" / "model.json").read_bytes()

    _variant(tmp_path, monkeypatch, {"rest_floors": _harden}, name="cat-b")
    q = next(x for x in doc["questions"] if x["id"] == QNIGHT)
    assert fp.apply_param_answer(doc, q, "600 минут", 3)["applied"]
    problems = webui._sync_canon(run, doc, "применение ответов")
    assert problems, "живой путь обязан отвергнуть перепосадку"
    assert any("изменилась единица" in p for p in problems), problems
    assert any("повторите прогон по текущему каталогу" in p
               for p in problems), problems
    assert (run / "extract" / "model.json").read_bytes() == before
    for call, mark in ((tp.build_package, "build"), (tp.export_zip, "zip")):
        with pytest.raises(tp.TaskPackageError) as e:
            call(run)
        assert e.value.code == tp.FAMILY_INCOMPLETE, mark


def test_the_live_panel_path_refuses_an_answer_when_a_param_was_removed(
        tmp_path, monkeypatch, _fresh_bridge):
    """Тот же живой путь для клаузы (в): посадка НЕСЁТ имя, которого
    действующая запись больше не объявляет. Вопрос о недостающем
    `min_rest_home` задан законно на прежнем каталоге; после смены
    каталога анкета молчит, а ответ на прежний вопрос канон не
    принимает — `model.json` не меняется, выпуск отказывает."""
    from cdpm import webui
    doc = _doc_with(_landed(rev=REV, cells={
        "min_rest_outstation": {"value": 600, "quote": "10:00"}}))
    assert fp.ensure_family_param_questions(doc) == [QHOME]
    run = _run_dir(tmp_path, doc, "run-live-rm")
    before = (run / "extract" / "model.json").read_bytes()

    _variant_without_outstation(tmp_path, monkeypatch, name="cat-rm-live")
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert fp.ensure_family_param_questions(doc) == []
    q = next(x for x in doc["questions"] if x["id"] == QHOME)
    assert fp.apply_param_answer(doc, q, "720 минут", 3)["applied"]
    problems = webui._sync_canon(run, doc, "применение ответов")
    assert any(REMOVED_WORDS in p for p in problems), problems
    assert any("повторите прогон по текущему каталогу" in p
               for p in problems), problems
    assert (run / "extract" / "model.json").read_bytes() == before
    with pytest.raises(tp.TaskPackageError) as e:
        tp.export_zip(run)
    assert e.value.code == tp.FAMILY_INCOMPLETE


def test_a_reland_never_speaks_the_words_of_a_healable_shortage(
        tmp_path, monkeypatch, _fresh_bridge):
    """Граница К-2: правка каталога УЖЕСТОЧИЛА наборы И сменила единицу
    посаженного параметра — доказанная перепосадка (б) старше. Слова
    семьи обязаны быть ОДНИ: «перепосадка требуется … повторите прогон
    по текущему каталогу». Слов ужесточения («перепосадки не требуется,
    недостачу лечит ответ анкеты») в перечне быть не должно — иначе об
    одной семье говорилось бы два противоположных вердикта сразу, а
    владелец, поверив недостаче, ответил бы на вопрос впустую.

    Минус: если слова ужесточения приклеиваются к недостаче независимо
    от состояния контракта, краснеет утверждение о «перепосадки не
    требуется»."""
    _variant(tmp_path, monkeypatch, {"rest_floors": _harden})
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert cell["missing"] == ["min_rest_night"]   # недостача тоже есть
    assert cell["contract"]["hardening"] == []
    words = [pr["words"] for pr in cell["problems"]]
    assert any("перепосадка требуется" in w for w in words), words
    assert not any("перепосадки не требуется" in w for w in words), words
    assert not any("недостачу лечит ответ анкеты" in w for w in words), words


# --------------------------------------------------------------------
# К-1 неавтор-ревью 12.09.2026: совет рубежа выпуска не зовёт к
# действию, которого система не допускает
# --------------------------------------------------------------------

def test_a_reland_with_a_shortage_never_advises_answering_the_questionnaire(
        tmp_path, monkeypatch, _fresh_bridge):
    """К-1: у ОДНОЙ семьи есть и ДОКАЗАННАЯ перепосадка (клауза (б): у
    посаженного `min_rest_outstation` сменилась единица), и недостача
    добавленного `min_rest_night`. Совет отказа обязан звать ТОЛЬКО к
    повтору прогона: вопрос по такой семье анкета НЕ ставит
    (`ensure_family_param_questions` — решение К-2, п. 6), значит
    «Ответьте на вопрос анкеты» называло бы действие, которого система
    не допускает. Слова самой недостачи обязаны назвать неизлечимость,
    а не звучать как обычная, излечимая недостача.

    Это тот же класс, что К-2, — ложное обещание лечения: одно и то же
    сообщение говорило «ответом анкеты это не лечится» и «ответьте на
    вопрос анкеты».

    Минус (на прежнем коде): счёт совета вёлся по признаку `proven`
    ОТДЕЛЬНОГО основания, недостача той же семьи идёт с
    `proven=False`, и ветка `len(proven) < len(fam)` выдавала «Ответьте
    на вопрос анкеты …, а при „перепосадка требуется“ — повторите
    прогон». Первым краснеет утверждение об отсутствии слов «Ответьте
    на вопрос анкеты», последним — хвост слов недостачи."""
    _variant(tmp_path, monkeypatch, {"rest_floors": _harden})
    _fresh_cdpm_bindings(tmp_path, monkeypatch)
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert cell["verdict"] == fp.VERDICT_RELAND
    assert cell["missing"] == ["min_rest_night"]
    # Основания ОДНОЙ семьи: доказанная перепосадка и недостача; у
    # второго `proven=False` — на нём и ломался прежний счёт.
    assert [pr["proven"] for pr in cell["problems"]] == [True, False]
    # Анкета вопроса по такой семье не ставит — звать к ответу нечем.
    assert fp.ensure_family_param_questions(doc) == []

    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-k1-reland"))
    msg = str(e.value)
    assert e.value.code == tp.FAMILY_INCOMPLETE
    assert "Ответьте на вопрос анкеты о параметре" not in msg, msg
    assert ("Ответом анкеты такой свод не лечится: повторите прогон по "
            "текущему каталогу") in msg, msg

    # Слова недостачи называют неизлечимость прямо, а не «не задан и не
    # спрошен» — иначе владелец ждал бы вопроса, которого не будет.
    words = next(pr["words"] for pr in cell["problems"]
                 if pr["param"] == "min_rest_night")
    assert "вопрос анкеты по семье rest_floors не ставится" in words
    assert "ответом эта недостача не лечится" in words
    assert "повторите прогон по текущему каталогу" in words


def test_a_family_without_a_catalog_record_advises_a_rerun_not_an_answer(
        tmp_path):
    """К-1 (вторая половина того же счёта): записи семьи в каталоге НЕТ
    — вопроса по ней анкета тоже не ставит (`_reland_families` считает
    и этот случай `RELAND_STATES`), и совет «Ответьте на вопрос
    анкеты» был бы тем же несбыточным обещанием. Основание такой семьи
    идёт с `proven=False` (доказывать нечем — сравнивать не с чем),
    поэтому прежний счёт по `proven` выдавал ЧИСТЫЙ совет к ответу, без
    единого слова о повторе прогона.

    Минус (на прежнем коде): краснеет и утверждение об отсутствии слов
    «Ответьте на вопрос анкеты», и утверждение о наличии «Ответом
    анкеты такой свод не лечится»."""
    doc = _doc_with(_landed(family="no_such_family"))
    cell = fp.family_completeness(doc)["no_such_family"]
    assert cell["contract_state"] == fp.CONTRACT_NONE
    assert cell["verdict"] == fp.VERDICT_RELAND
    assert [pr["proven"] for pr in cell["problems"]] == [False]
    assert fp.ensure_family_param_questions(doc) == []
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-k1-norec"))
    msg = str(e.value)
    assert e.value.code == tp.FAMILY_INCOMPLETE
    assert "Ответьте на вопрос анкеты о параметре" not in msg, msg
    assert "Ответом анкеты такой свод не лечится" in msg, msg


def test_the_words_say_an_answer_to_an_earlier_question_will_not_apply(
        tmp_path, monkeypatch, _fresh_bridge):
    """К-1: вопрос задан ЗАКОННО, пока запись только ужесточалась
    (клауза (а)); каталог сменился, и та же недостача стала
    неизлечимой. Слова обязаны сказать ОБА факта: новых вопросов по
    семье анкета не ставит, а ответ на заданный ранее применён не
    будет — живой путь панели отвергает его целиком (см.
    `test_the_live_panel_path_refuses_an_answer_to_a_proven_reland`).

    Минус (на прежнем коде): слова были только «параметр … спрошен (Q),
    ответа нет» — владелец видел открытый вопрос и совет ответить, а
    ответ ничего не менял."""
    _variant(tmp_path, monkeypatch, {"rest_floors": _add_param},
             name="cat-a")
    _fresh_cdpm_bindings(tmp_path, monkeypatch, name="cat-a")
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    assert fp.ensure_family_param_questions(doc) == [QNIGHT]

    _variant(tmp_path, monkeypatch, {"rest_floors": _harden}, name="cat-b")
    _fresh_cdpm_bindings(tmp_path, monkeypatch, name="cat-b")
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert cell["asked"] == {"min_rest_night": QNIGHT}
    words = next(pr["words"] for pr in cell["problems"]
                 if pr["param"] == "min_rest_night")
    assert f"спрошен ({QNIGHT}), ответа нет" in words
    assert f"ответ на заданный ранее {QNIGHT} применён не будет" in words
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-k1-asked"))
    assert "Ответьте на вопрос анкеты о параметре" not in str(e.value)


def test_a_healable_family_next_to_a_relanded_one_still_calls_for_an_answer(
        tmp_path, monkeypatch, _fresh_bridge):
    """Граница К-1 (положительный контроль): семей две — у `rest_floors`
    доказанная перепосадка, у `duty_limits` ОБЫЧНАЯ недостача, которую
    лечит ответ. Совет обязан назвать ОБА пути: сузить его до «повторите
    прогон» значило бы промолчать о вопросе, который анкета ставит и
    который вторую семью действительно вылечит.

    Минус: если лечимость считать по перечню целиком (нет ни одного
    `proven=False`? нет ни одной семьи без перепосадки?), а не по
    СЕМЬЯМ, краснеет утверждение о словах «Ответьте на вопрос
    анкеты»."""
    _variant(tmp_path, monkeypatch, {"rest_floors": _harden})
    _fresh_cdpm_bindings(tmp_path, monkeypatch)
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)),
                    _landed(rid="R-D", zone="PAIRING", family="duty_limits",
                            rev=REV, cells={}))
    got = fp.family_completeness(doc)
    assert got["rest_floors"]["verdict"] == fp.VERDICT_RELAND
    assert got["duty_limits"]["verdict"] == fp.VERDICT_INCOMPLETE
    assert got["duty_limits"]["missing"] == ["max_duty_duration"]
    # Вопрос ставится ТОЛЬКО по лечимой семье.
    assert fp.ensure_family_param_questions(doc) == \
        ["Q-3a-параметр-duty_limits-max_duty_duration"]

    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-k1-mixed"))
    msg = str(e.value)
    assert "Ответьте на вопрос анкеты о параметре" in msg, msg
    assert "а при «перепосадка требуется» — повторите прогон" in msg, msg


# --------------------------------------------------------------------
# Тот же класс ложного обещания: КРИВАЯ ЗАПИСЬ КАТАЛОГА (12.09.2026)
# --------------------------------------------------------------------

def _broken_records(monkeypatch, fam="rest_floors"):
    """Записи каталога, где у `fam` минимальный полный набор называет
    параметр, которого у записи НЕТ (Н-4): набор отбрасывается целиком,
    требования читаются по остатку — и ЗАПИСЬ КАТАЛОГА кривая.

    Подделка вносится только на ВХОДЕ (читатель записей каталога), сам
    проверяемый расчёт не подменяется. Через дверь копии каталога такую
    запись не внести: её отвергает собственная проверка каталога
    (`catalog_copies.copy_catalog` — `CatalogRequired`), и это верно;
    CDPM же обязан судить кривую запись fail-closed, откуда бы она ни
    пришла."""
    records = copy.deepcopy(bridge.family_records())
    records[fam] = dict(records[fam],
                        param_sets=[{"id": "s1", "note": "кривой набор",
                                     "params": ["min_rest_home", "zzz"]}])
    monkeypatch.setattr(bridge, "family_records", lambda: records)
    return records


def test_a_broken_catalog_record_is_not_a_reland_and_not_curable(
        monkeypatch):
    """Запись семьи в каталоге ЧИТАЕТСЯ НЕ ЦЕЛИКОМ — перепосадкой это НЕ
    является (посадка годна, смысл посаженных значений тот же, вердикт
    остаётся «неполна»), но и ОТВЕТОМ АНКЕТЫ не лечится: кривую запись
    каталога авиакомпания не чинит.

    Один закон лечимости (`family_curable`) считает обе причины; в
    `RELAND_STATES` этому состоянию не место — оно не перепосадка.

    Минус (на прежнем коде): лечимость считалась по одному вердикту,
    вердикт здесь «неполна», и семья попадала в лечимые — краснеет
    утверждение `cell["curable"] is False`."""
    _broken_records(monkeypatch)
    doc = _doc_with(_landed())
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_SAME     # не перепосадка
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE
    assert cell["verdict"] != fp.VERDICT_RELAND
    assert cell["catalog_problems"] and "zzz" in cell["catalog_problems"][0]
    assert cell["curable"] is False
    assert fp.family_curable(cell["verdict"],
                             cell["catalog_problems"]) is False
    # Сам СВОД законен — виноват каталог; валидатор о нём молчит.
    assert m.validate_model(doc) == []
    assert all(pr["curable"] is False and pr["catalog_broken"] is True
               for pr in cell["problems"]), cell["problems"]


def test_a_broken_catalog_record_makes_the_advice_call_for_a_catalog_fix(
        tmp_path, monkeypatch):
    """Совет рубежа выпуска обязан звать к ИСПРАВЛЕНИЮ КАТАЛОГА, а не к
    ответу анкеты: ответ этот свод не выпустит — основание «читается не
    целиком» держит барьер и с ответом.

    Минус (на прежнем коде): совет был ровно «Ответьте на вопрос анкеты
    о параметре (значение либо „не применяется“)» — краснеет первым
    утверждение об отсутствии этих слов."""
    _broken_records(monkeypatch)
    doc = _doc_with(_landed())
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-broken"))
    msg = str(e.value)
    assert e.value.code == tp.FAMILY_INCOMPLETE
    assert "Ответьте на вопрос анкеты о параметре" not in msg, msg
    assert "Ответом анкеты такой свод не лечится" in msg, msg
    assert ("исправьте запись семьи в каталоге и повторите прогон по "
            "исправленному каталогу") in msg, msg
    # Совет «повторите прогон по ТЕКУЩЕМУ каталогу» был бы неправдой не
    # меньшей: текущий каталог и есть кривой.
    assert "повторите прогон по текущему каталогу" not in msg, msg


def test_the_shortage_words_name_the_broken_catalog_record(monkeypatch):
    """Слова самой недостачи называют причину честно: требования
    прочтены по уцелевшему остатку записи, и ответом эта недостача не
    лечится.

    Минус (на прежнем коде): слова были «параметр … не задан и не
    спрошен» — обычная, излечимая недостача, и владелец ждал бы, что
    ответ её закроет."""
    _broken_records(monkeypatch)
    doc = _doc_with(_landed())
    cell = fp.family_completeness(doc)["rest_floors"]
    words = next(pr["words"] for pr in cell["problems"]
                 if pr["param"] == "min_rest_outstation")
    assert "параметр min_rest_outstation семьи rest_floors" in words
    assert "читается не целиком" in words
    assert "ответом анкеты эта недостача не лечится" in words
    assert "исправьте запись семьи в каталоге" in words
    # Текст самой беды записи не дублируется в хвосте недостачи: он
    # едет СВОЕЙ строкой основания рядом (симметрия с хвостом К-1).
    assert "zzz" not in words, words
    ground = next(pr["words"] for pr in cell["problems"]
                  if pr.get("catalog_record"))
    assert "zzz" in ground and "читается не целиком" in ground


def test_a_complete_family_with_a_broken_record_is_still_incurable(
        tmp_path, monkeypatch):
    """Граница: недостачи у семьи НЕТ вовсе (оба параметра заданы),
    вердикт «полна» — и всё равно кривая запись держит выпуск, а совет
    зовёт к каталогу. Лечимость считается по СЕМЬЕ, и «полна» лечимой
    её не делает.

    Минус: при счёте по вердикту «полна» попадала в лечимые, и совет
    звал ответить на вопрос анкеты, которого нет вовсе (недостачи нет —
    анкете нечего спрашивать)."""
    _broken_records(monkeypatch)
    doc = _doc_with(_landed(cells=copy.deepcopy(FULL_CELLS)))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["missing"] == [] and cell["verdict"] == fp.VERDICT_COMPLETE
    assert cell["curable"] is False
    assert fp.ensure_family_param_questions(doc) == []
    assert m.readiness(doc)["state"] != "в"
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-broken-full"))
    msg = str(e.value)
    assert "Ответьте на вопрос анкеты о параметре" not in msg, msg
    assert "исправьте запись семьи в каталоге" in msg, msg


def test_a_healable_family_next_to_a_broken_record_keeps_both_paths(
        tmp_path, monkeypatch):
    """Положительный контроль (та же граница, что у К-1): семей две — у
    `rest_floors` запись каталога кривая, у `duty_limits` ОБЫЧНАЯ
    недостача, которую лечит ответ. Совет обязан назвать ОБА пути.

    Минус: если считать лечимость по перечню целиком («есть хоть одна
    кривая запись — молчим про анкету»), краснеет утверждение о словах
    «Ответьте на вопрос анкеты»."""
    _broken_records(monkeypatch)
    doc = _doc_with(_landed(),
                    _landed(rid="R-D", zone="PAIRING", family="duty_limits",
                            cells={}))
    got = fp.family_completeness(doc)
    assert got["rest_floors"]["curable"] is False
    assert got["duty_limits"]["curable"] is True
    assert got["duty_limits"]["missing"] == ["max_duty_duration"]
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-broken-mixed"))
    msg = str(e.value)
    assert "Ответьте на вопрос анкеты о параметре" in msg, msg
    assert ("а при «читается не целиком» — исправьте запись семьи в "
            "каталоге и повторите прогон по исправленному каталогу") in msg


def test_the_old_advice_is_unchanged_when_no_record_is_broken(tmp_path):
    """Сторож прежних слов: у семьи с обычной недостачей и целой записью
    каталога совет остался БУКВАЛЬНО прежним — правка 12.09.2026 новых
    слов в старые случаи не приносит."""
    doc = _doc_with(_landed())
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-plain-advice"))
    msg = str(e.value)
    assert ("Ответьте на вопрос анкеты о параметре (значение либо «не "
            "применяется»), и повторите выпуск") in msg, msg
    assert "читается не целиком" not in msg, msg


def test_compatible_change_does_not_block(tmp_path):
    """K01b-05 (положительный контроль): посадка ВЫПУЩЕННЫМ каталогом
    0.4.0, чья запись `rest_floors` по контракту совпадает с
    действующей, — «совместимо», ничего не держится, выпуск идёт.
    Это ровно случай наборов H0/H0-B/H0-C."""
    doc = _doc_with(_landed(rev=REV_040, cells=copy.deepcopy(FULL_CELLS)))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_COMPATIBLE
    assert cell["contract"]["differences"] == []
    assert cell["verdict"] == fp.VERDICT_COMPLETE
    assert m.readiness(doc)["state"] == "в"
    assert m.validate_model(doc) == []
    assert tp.build_package(_run_dir(tmp_path, doc))


def test_an_unrelated_family_stays_green_next_to_a_broken_one():
    """K01b-05/K01b-07: поломана одна семья — вторая (полная) в перечень
    проблем не попадает, её правила не называются."""
    doc = _doc_with(_landed(),
                    _landed(rid="R-D", zone="PAIRING",
                            family="duty_limits",
                            cells={"max_pairing_days": {"value": 3,
                                                        "quote": "3 суток"}}))
    got = fp.family_completeness(doc)
    assert got["duty_limits"]["verdict"] == fp.VERDICT_COMPLETE
    assert got["rest_floors"]["verdict"] == fp.VERDICT_INCOMPLETE
    problems = fp.family_barrier_problems(doc)
    assert [p["family"] for p in problems] == ["rest_floors"]
    assert problems[0]["rules"] == ["R-F"]


# --------------------------------------------------------------------
# Что считается заданным и отклонённым (классы K01b-02, K01b-03)
# --------------------------------------------------------------------

@pytest.mark.parametrize("raw", [None, "", "   ", "__нет ключа__"])
def test_empty_cell_is_not_given(raw):
    """K01b-02: ключ без значения (нет поля, null, пустая строка) —
    НЕ «задан»: полнота судится по значению, а не по имени ключа."""
    cells = copy.deepcopy(FULL_CELLS)
    if raw == "__нет ключа__":
        cells["min_rest_outstation"] = {"quote": "10:00"}
    else:
        cells["min_rest_outstation"] = {"value": raw, "quote": "10:00"}
    cell = fp.family_completeness(_doc_with(_landed(cells=cells)))["rest_floors"]
    assert cell["given"] == ["min_rest_home"]
    assert cell["missing"] == ["min_rest_outstation"]


def test_legal_zero_is_given_not_lost_by_truthiness():
    """K01b-02: законный ноль — значение, а не пустота (домен параметра
    каталога `count` — целое ≥ 0)."""
    doc = _doc_with(_landed(rid="R-DO", zone="ROSTERING",
                            family="days_off_floor",
                            cells={"min_days_off": {"value": 0,
                                                    "quote": "0 дней"}}))
    cell = fp.family_completeness(doc)["days_off_floor"]
    assert cell["given"] == ["min_days_off"]
    assert cell["missing"] == [] and cell["verdict"] == fp.VERDICT_COMPLETE


def test_unapplied_or_foreign_decline_does_not_close_the_param():
    """K01b-03: отказ засчитывается только ПРИМЕНЁННЫЙ и адресный —
    черновик ответа, снятие кодом и отказ по другому параметру
    обязательный параметр не закрывают."""
    doc = _doc_with(_landed())
    assert fp.ensure_family_param_questions(doc) == [QID]
    q = next(x for x in doc["questions"] if x["id"] == QID)
    q["answer"] = {"raw": "Не применяется", "status": "ожидает обработчика",
                   "revision": 2}
    assert fp.family_completeness(doc)["rest_floors"]["declined"] == []
    q["answer"] = {"raw": fp.CODE_CLOSED_RAW, "status": "применён",
                   "revision": 2}
    assert fp.family_completeness(doc)["rest_floors"]["declined"] == []
    # Отказ по ДРУГОМУ параметру той же семьи чужого не закрывает.
    q["id"] = "Q-3b-параметр-rest_floors-min_rest_home"
    q["answer"] = {"raw": "Не применяется", "status": "применён",
                   "revision": 2}
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["declined"] == ["min_rest_home"]
    assert cell["missing"] == ["min_rest_outstation"]
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE


def test_pending_revision_of_a_cell_is_visible_in_the_calculation():
    """K01b-03: ячейка с открытым пересмотром (`pending_revision`, закон
    2.4 — единица K04) видна расчёту отдельным перечнем: старое значение
    остаётся `given`, но пересмотр не теряется."""
    cells = copy.deepcopy(FULL_CELLS)
    cells["min_rest_outstation"]["pending_revision"] = "Q-3b-…-R1"
    cell = fp.family_completeness(_doc_with(_landed(cells=cells)))["rest_floors"]
    assert cell["pending"] == ["min_rest_outstation"]
    assert cell["given"] == sorted(REST_REQUIRED)


# --------------------------------------------------------------------
# Откуда берутся требования (класс K01b-06)
# --------------------------------------------------------------------

def test_unknown_family_is_not_complete_from_an_empty_requirement_list():
    """K01b-06: у семьи, которой нет в каталоге, требований взять
    неоткуда — «полна» не выдаётся (fail-closed), готовность держится,
    выпуск отказывает."""
    doc = _doc_with(_landed(family="no_such_family"))
    cell = fp.family_completeness(doc)["no_such_family"]
    assert cell["contract_state"] == fp.CONTRACT_NONE
    assert cell["verdict"] == fp.VERDICT_RELAND
    assert "нет в каталоге" in cell["problems"][0]["words"]
    assert m.readiness(doc)["state"] == "а"


def test_requirements_come_from_the_catalog_sets_not_from_a_union(
        monkeypatch):
    """K01b-06: требования — по выбранному допустимому набору записи, а
    не объединением всех наборов; набор с несуществующим параметром
    отбрасывается, а когда законных наборов не осталось — обязательны
    ВСЕ параметры записи (fail-closed). Эталон выписан руками."""
    from cdpm import catalog_bridge as bridge
    record = {"id": "synthetic", "params": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
              "param_sets": [{"id": "s1", "params": ["a", "b"]},
                             {"id": "s2", "params": ["c"]}]}
    monkeypatch.setattr(bridge, "family_records", lambda: {"synthetic": record})
    assert fp.missing_params("synthetic", {"c"}) == []
    assert fp.missing_params("synthetic", {"a"}) == ["b"]
    assert fp.missing_params("synthetic", set()) == ["c"]
    bogus = {"id": "synthetic", "params": [{"name": "a"}, {"name": "b"}],
             "param_sets": [{"id": "s1", "params": ["zzz"]}]}
    monkeypatch.setattr(bridge, "family_records", lambda: {"synthetic": bogus})
    assert fp.missing_params("synthetic", {"a"}) == ["b"]
    assert fp.missing_params("synthetic", set()) == ["a", "b"]


# --------------------------------------------------------------------
# Затронутые режимы и частичная передача (класс K01b-07)
# --------------------------------------------------------------------

def test_only_the_affected_task_is_held_and_partial_transfer_stays(tmp_path):
    """K01b-07: поломана ростеринговая семья — держится ростеринг,
    пейринг остаётся готовым («б»), и законная частичная передача
    незатронутого пейринга сохраняется: пакет выпускается, а основание
    видно в матрице готовности манифеста."""
    doc = _doc_with(_landed(zone="ROSTERING"),
                    _landed(rid="R-D", zone="PAIRING", family="duty_limits",
                            cells={"max_pairing_days": {"value": 3,
                                                        "quote": "3 суток"}}))
    got = m.readiness(doc)
    assert got["state"] == "б"
    assert got["pairing_ready"] and not got["rostering_ready"]
    assert list(got["pairing_open"]) == []
    modes, _ = tp.allowed_modes(doc)
    assert modes["pairing"] and not modes["rostering"]
    words = m.readiness_barriers(doc)[0]["words"]
    matrix = m.readiness_matrix(doc)
    assert matrix["pairing"]["feasibility-hard"]["ready"]
    for mode in ("feasibility-hard", "feasibility-soft"):
        assert not matrix["rostering"][mode]["ready"]
        assert matrix["rostering"][mode]["reasons"] == {words: 1}
    files = tp.build_package(_run_dir(tmp_path, doc))
    manifest = json.loads(files["manifest.json"])
    assert manifest["allowed_modes"]["rostering"] == []
    assert manifest["allowed_modes"]["pairing"]
    pkg = tmp_path / "part.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, counters = gate.check(pkg)
    assert problems == [], problems
    assert counters["barriers_held"] == [words]


def test_soft_carrier_holds_only_the_penalty_modes():
    """Мягкий носитель держит режимы со штрафами, а выполнимость без
    штрафов — нет (та же применимость, что у пробела покрытия)."""
    doc = _doc_with(_landed(zone="ROSTERING", kind="SOFT"))
    doc["rules"][-1]["weight"] = 10
    modes, _ = tp.allowed_modes(doc)
    assert "feasibility-hard" in modes["rostering"]
    assert "feasibility-soft" not in modes["rostering"]
    assert "optimization" not in modes["rostering"]


def test_verdict_does_not_depend_on_order_or_on_the_question_generator():
    """K01b-07: вердикт не зависит от порядка правил и вопросов и от
    того, звали ли генератор анкеты; повторный вызов генератора ничего
    не меняет."""
    doc = _doc_with(_landed(),
                    _landed(rid="R-D", zone="PAIRING", family="duty_limits",
                            cells={"max_pairing_days": {"value": 3,
                                                        "quote": "3 суток"}}))
    before = [(p["family"], p["param"], p["words"])
              for p in fp.family_barrier_problems(doc)]
    doc["rules"].reverse()
    doc["questions"].reverse()
    assert [(p["family"], p["param"], p["words"])
            for p in fp.family_barrier_problems(doc)] == before
    assert fp.ensure_family_param_questions(doc) == [QID]
    assert fp.ensure_family_param_questions(doc) == []
    after = fp.family_barrier_problems(doc)
    assert [(p["family"], p["param"]) for p in after] \
        == [(f, pm) for f, pm, _ in before]
    assert after[0]["question"] == QID


@pytest.mark.parametrize("raw", [[], {}])
def test_empty_collection_value_is_not_given(raw):
    """K01b-02/X-02: пустой перечень и пустой объект — такая же
    пустота, как null и пустая строка (значение параметра бывает
    массивом или объектом обозначений, контракт §3.1): полнота из них
    не выводится."""
    cells = copy.deepcopy(FULL_CELLS)
    cells["min_rest_outstation"] = {"value": raw, "quote": "10:00"}
    cell = fp.family_completeness(_doc_with(_landed(cells=cells)))["rest_floors"]
    assert cell["given"] == ["min_rest_home"]
    assert cell["missing"] == ["min_rest_outstation"]
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE


def test_a_family_used_only_by_a_definition_rule_is_still_judged_complete():
    """K01b-07/X-03: семья «использована», если на неё посажено ЛЮБОЕ
    правило свода, а не только машинное. Строка-определение мира
    (DATA/DEF) несёт параметры семьи так же, как жёсткая: недостача
    обязательного параметра видна расчётом и держит готовность —
    иначе вся недостача пряталась бы сменой вида строки.

    Исполнитель у такой семьи при этом не требуется: строку DATA/DEF
    судит линия данных входной книгой, а не сертификат решения
    (K06-барьер её не касается — соседний файл)."""
    doc = _doc_with(_landed(rid="R-DEF", kind="DEF", zone="ROSTERING"))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["missing"] == ["min_rest_outstation"]
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE
    words = [pr["words"] for pr in m.readiness_barriers(doc)]
    assert any("min_rest_outstation" in w and "не задан и не спрошен" in w
               for w in words), words
    got = m.readiness(doc)
    assert got["state"] != "в" and not got["rostering_ready"]
    # Полный набор — та же строка-определение ничего не держит.
    ok = _doc_with(_landed(rid="R-DEF", kind="DEF", zone="ROSTERING",
                           cells=copy.deepcopy(FULL_CELLS)))
    assert m.readiness_barriers(ok) == [] and m.readiness(ok)["state"] == "в"


def test_landing_without_any_revision_is_not_proven_compatible(tmp_path):
    """K01b-06: посадка БЕЗ ревизии вовсе (старая форма) — прежней
    записи семьи взять неоткуда: состояние «совместимость не доказана».
    Недостача при таком штампе держится как ОБЫЧНАЯ недостача (В-1: ни
    «перепосадка», ни валидатор — иначе закрылся бы путь ответов), а
    слова называют недоказанность прямо. Полный набор не блокируется:
    требования действующей записи выполнены."""
    r = _landed()
    r["template_fit"].pop("registry_rev")
    doc = _doc_with(r)
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract"]["landed_revs"] == [""]
    assert cell["contract_state"] == fp.CONTRACT_UNPROVEN
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE
    words = cell["problems"][0]["words"]
    assert "совместимость посадки не доказана" in words
    assert "без ревизии" in words
    # Валидатор молчит о полноте: единственная его претензия к такому
    # своду — отсутствующий штамп посадки, и путь ответов открыт.
    assert m.validate_model(doc) == ["R-F: посадка без registry_rev"]
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-norev"))
    assert e.value.code == tp.FAMILY_INCOMPLETE
    full = _landed(cells=copy.deepcopy(FULL_CELLS))
    full["template_fit"].pop("registry_rev")
    ok = _doc_with(full)
    assert fp.family_completeness(ok)["rest_floors"]["verdict"] \
        == fp.VERDICT_COMPLETE
    assert m.readiness(ok)["state"] == "в"


# ====================================================================
# Правки по неавтор-ревью ранней части K01 (12.09.2026)
# ====================================================================

@pytest.fixture
def _fresh_bridge():
    """Мост CDPM смотрит на каталог теста, а после теста — снова на
    дерево (кэш моста живёт на процесс)."""
    bridge.forget()
    yield
    bridge.forget()


def _variant(tmp_path, monkeypatch, edit, name="cat"):
    """Копия действующего каталога с ПРАВКОЙ записи и НОВЫМ ярлыком
    версии: журнал версий и папки `releases/` копия несёт живые, значит
    отпечаток 0.5.0 дерева в ней по-прежнему разрешается в выпуск — и
    расчёт сравнивает ЗАПИСИ, а не штампы."""
    folder = cc.copy_catalog(tmp_path, families=edit, releases=True,
                             version="0.6.0", name=name)
    cc.use_catalog(monkeypatch, folder)
    bridge.forget()
    assert bridge.stamp()["fingerprint"] != REV
    return folder


def _fresh_cdpm_bindings(tmp_path, monkeypatch, name="cat"):
    """Свежие привязки продукта CDPM для КОПИИ каталога.

    К проверяемому закону не относится и относиться не должно: в дереве
    привязки CDPM заявлены о каталоге 0.5.0, для копии 0.6.0 их
    свидетельство устарело, и K06-барьер отказал бы РАНЬШЕ кодом
    `support-missing`, заслонив собой K01. Правится только шапка
    (версия и отпечаток) — строки исполнителей те же."""
    stamp = bridge.stamp()
    data = json.loads(
        (ROOT / "cdpm" / "bindings.json").read_text(encoding="utf-8"))
    data["catalog_version"] = stamp["version"]
    data["catalog_fingerprint"] = stamp["fingerprint"]
    path = tmp_path / f"cdpm-bindings-{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    monkeypatch.setenv(bridge.SUPPORT_ENV["CDPM"], str(path))
    bridge.forget()
    return path


def _add_param(rec):
    rec["params"] = list(rec["params"]) + [
        {"name": "min_rest_night", "note": "пол ночного отдыха",
         "qualifier": None, "relation": "min", "unit": "duration_minutes"}]


def _remove_param(rec):
    """Снятие параметра из записи семьи: выпуск 0.6.0 больше не
    объявляет `min_rest_outstation`, а посадка 0.5.0 его НЕСЁТ."""
    rec["params"] = [p for p in rec["params"]
                     if p["name"] != "min_rest_outstation"]


def _set_field(field, value):
    def edit(rec):
        params = [dict(p) for p in rec["params"]]
        for p in params:
            if p["name"] == "min_rest_outstation":
                p[field] = value
        rec["params"] = params
    return edit


def _harden(rec):
    """Ужесточение записи, которое ответом анкеты НЕ лечится: добавлен
    обязательный параметр (о нём анкета и спросит) И у уже посаженного
    параметра сменилась единица (С-3 — «600» в минутах и «600» в счёте
    разные правды)."""
    _add_param(rec)
    _set_field("unit", "count")(rec)


# --------------------------------------------------------------------
# В-1: несовместимость судится СОДЕРЖАНИЕМ записи, а не штампом
# --------------------------------------------------------------------

@pytest.mark.parametrize("edit,mark", [
    ({"rest_floors": _set_field("unit", "count")}, "изменилась единица"),
    ({"rest_floors": _set_field("relation", "max")}, "изменилось отношение"),
])
def test_content_change_of_the_record_is_a_proven_reland(
        tmp_path, monkeypatch, _fresh_bridge, edit, mark):
    """В-1 и С-3: запись семьи в действующем каталоге РАЗОШЛАСЬ С
    записью выпуска, которым посажены правила, ПО СМЫСЛУ — у УЖЕ
    ЗАДАННОГО параметра сменилась единица или отношение (клауза (б)
    §12.15): «перепосадка требуется» доказана содержанием. Валидатор
    объявляет её нарушением, выпуск отказывает — даже когда по
    действующей записи недостачи нет, и слова называют путь.

    Добавление обязательного параметра (клауза (а)) из перечня УБРАНО
    решением ведущего 12.09.2026 по находке К-2: смысл посаженных
    значений оно не меняет, это обычная недостача — см. соседний
    `test_a_hardened_record_is_an_ordinary_shortage_healed_by_answer`."""
    _variant(tmp_path, monkeypatch, edit)
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert cell["verdict"] == fp.VERDICT_RELAND
    words = cell["problems"][0]["words"]
    assert words.startswith("перепосадка требуется") and mark in words
    assert "повторите прогон по текущему каталогу" in words
    assert any(words in p for p in m.validate_model(doc))
    assert m.readiness(doc)["state"] == "а"
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-var"))
    assert e.value.code == tp.FAMILY_INCOMPLETE


def test_a_hardened_record_is_an_ordinary_shortage_healed_by_answer(
        tmp_path, monkeypatch, _fresh_bridge):
    """К-2 неавтор-ревью 12.09.2026 (решение ведущего): действующая
    запись семьи стала требовать БОЛЬШЕ, чем несёт посадка, — каталог
    0.6.0 добавил `rest_floors` третий обязательный параметр, а посадка
    0.5.0 полна по прежней записи (клауза (а) §12.15). Перепосадкой это
    НЕ является: единицы, отношения и уточнители те же, ничего не
    убрано — посаженные значения означают ровно то же, и недостаёт
    соседнего. Значит это ОБЫЧНАЯ недостача §12.12 п. 1: состояние
    «запись ужесточена», вердикт «неполна», валидатор молчит, анкета
    спрашивает, готовность ниже «в», выпуск отказывает
    `family-incomplete` БЕЗ совета «повторите прогон», а слова называют
    причину честно — и ответ анкеты её ЗАКОННО лечит.

    Минус (на прежнем коде): состояние было «несовместимо», вердикт —
    «перепосадка требуется», валидатор объявлял нарушение, а ответ
    анкеты всё равно лечил — и посадка, объявленная негодной,
    выпускалась как годная (дыра К-2: два разных вердикта об одном
    своде до и после ответа). Первым покраснело бы утверждение о
    состоянии контракта, последним — выпуск после ответа."""
    _variant(tmp_path, monkeypatch, {"rest_floors": _add_param})
    _fresh_cdpm_bindings(tmp_path, monkeypatch)
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_HARDENED
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE
    assert cell["missing"] == ["min_rest_night"]
    # Доказанных различий НЕТ — есть объяснение недостачи.
    assert cell["contract"]["differences"] == []
    assert any("минимальный полный набор параметров изменился" in h
               for h in cell["contract"]["hardening"])

    # Слова недостачи называют ПРИЧИНУ, а не выдают её за «параметра
    # никогда не было», и перепосадкой не пугают.
    words = cell["problems"][0]["words"]
    assert cell["problems"][0]["proven"] is False
    assert cell["problems"][0]["code"] == tp.FAMILY_INCOMPLETE
    assert words.startswith("параметр min_rest_night семьи rest_floors")
    assert "стала требовать больше, чем несёт посадка" in words
    assert "перепосадки не требуется" in words
    assert "повторите прогон по текущему каталогу" not in words

    # Валидатор молчит (иначе закрылся бы сам путь ответов), готовность
    # держится теми же словами, выпуск отказывает недостачей.
    assert m.validate_model(doc) == []
    assert m.readiness(doc)["state"] == "а"
    assert words in m.readiness(doc)["rostering_open"]
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-hard"))
    assert e.value.code == tp.FAMILY_INCOMPLETE
    assert e.value.items == ["rest_floors/min_rest_night"]
    assert "повторите прогон по текущему каталогу" not in str(e.value)
    assert "Ответьте на вопрос анкеты о параметре" in str(e.value)

    # Анкета спрашивает добавленный параметр, и ответ ЗАКОННО лечит:
    # это и есть ожидаемый исход, а не дыра.
    assert fp.ensure_family_param_questions(doc) == [QNIGHT]
    q = next(x for x in doc["questions"] if x["id"] == QNIGHT)
    assert fp.apply_param_answer(doc, q, "480 минут", 2)["applied"]
    after = fp.family_completeness(doc)["rest_floors"]
    assert after["contract_state"] == fp.CONTRACT_COMPATIBLE
    assert after["verdict"] == fp.VERDICT_COMPLETE
    assert m.validate_model(doc) == []
    assert m.readiness(doc)["state"] == "в"
    assert tp.build_package(_run_dir(tmp_path, doc, "run-hard-ok"))

    # ЖИВОЙ путь панели: канон прогона переиздаётся (стадия применения
    # ответов заканчивается именно этим), и пакет уходит обоими входами
    # выпуска — так выглядит ЗАКОННОЕ лечение недостачи.
    from cdpm import webui
    run = _run_dir(tmp_path, doc, "run-hard-live")
    assert webui._sync_canon(run, doc, "применение ответов") == []
    assert tp.export_zip(run)


def test_a_hardened_record_is_also_healed_by_an_unconditional_decline(
        tmp_path, monkeypatch, _fresh_bridge):
    """Второй законный исход той же клаузы (а): авиакомпания отвечает
    «не применяется» — требования у неё нет. Безусловный отказ закрывает
    добавленный параметр тем же счётом, каким считается `missing`
    (К-1 п. 2), семья полна, выпуск идёт."""
    _variant(tmp_path, monkeypatch, {"rest_floors": _add_param})
    _fresh_cdpm_bindings(tmp_path, monkeypatch)
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    assert fp.ensure_family_param_questions(doc) == [QNIGHT]
    q = next(x for x in doc["questions"] if x["id"] == QNIGHT)
    assert fp.apply_param_answer(doc, q, "Не применяется", 2)["applied"]
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["declined"] == ["min_rest_night"]
    assert cell["contract_state"] == fp.CONTRACT_COMPATIBLE
    assert cell["verdict"] == fp.VERDICT_COMPLETE
    assert m.readiness(doc)["state"] == "в"
    assert tp.build_package(_run_dir(tmp_path, doc, "run-hard-decl"))


def test_an_unrelated_change_of_the_catalog_stays_compatible(
        tmp_path, monkeypatch, _fresh_bridge):
    """В-1 (положительный контроль): каталог сменил отпечаток правкой
    ПОСТОРОННЕЙ семьи — контракт `rest_floors` тот же, посадка прежним
    отпечатком совместима, ничего не держится, валидатор молчит."""
    _variant(tmp_path, monkeypatch,
             {"layover_penalty": {"note": "примечание изменено"}})
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_COMPATIBLE
    assert cell["verdict"] == fp.VERDICT_COMPLETE
    assert [p for p in m.validate_model(doc) if "rest_floors" in p] == []
    assert [b for b in m.readiness_barriers(doc)
            if b.get("code") == fp.FAMILY_INCOMPLETE] == []


#: Слова третьей клаузы закона несовместимости (§12.12 п. 2(в)) —
#: независимый эталон, не из проверяемой функции.
REMOVED_WORDS = ("параметр min_rest_outstation, который несёт посадка, "
                 "из записи семьи убран")


def _variant_without_outstation(tmp_path, monkeypatch, name="cat-rm"):
    """Копия каталога, где запись `rest_floors` БОЛЬШЕ НЕ объявляет
    `min_rest_outstation`, — с привязками копии, которые этого имени
    тоже не называют.

    Правка привязок обязательна и не относится к проверяемому закону:
    строка исполнителя, ссылающаяся на снятый параметр, — беда СВЕЖЕСТИ
    привязок, и K06-барьер отказал бы раньше кодом `support-missing`,
    заслонив собой K01. Отпечаток шапки привязок копии не трогается —
    файл остаётся свежим для действующего каталога."""
    folder = cc.copy_catalog(tmp_path,
                             families={"rest_floors": _remove_param},
                             releases=True, version="0.6.0", name=name)
    cc.use_catalog(monkeypatch, folder)
    bridge.forget()
    stamp = bridge.stamp()
    assert stamp["fingerprint"] != REV
    _drop_param_from_bindings(folder / cc.BINDINGS_FILE)
    cdpm_b = json.loads(
        (ROOT / "cdpm" / "bindings.json").read_text(encoding="utf-8"))
    cdpm_b["catalog_version"] = stamp["version"]
    cdpm_b["catalog_fingerprint"] = stamp["fingerprint"]
    path = tmp_path / "cdpm-bindings-rm.json"
    path.write_text(json.dumps(cdpm_b, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    _drop_param_from_bindings(path)
    monkeypatch.setenv(bridge.SUPPORT_ENV["CDPM"], str(path))
    bridge.forget()
    return folder


def _drop_param_from_bindings(path) -> None:
    """Убрать снятый параметр из строк исполнителей файла привязок."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for row in data.get("bindings", []):
        keep = []
        for ex in row.get("executors") or []:
            if ex.get("param") == "min_rest_outstation":
                continue
            if isinstance(ex.get("params"), list):
                ex["params"] = [p for p in ex["params"]
                                if p != "min_rest_outstation"]
            keep.append(ex)
        row["executors"] = keep
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def test_a_param_removed_from_the_record_is_a_proven_reland(
        tmp_path, monkeypatch, _fresh_bridge):
    """К-1 неавтор-ревью 12.09.2026: у семьи `rest_floors` выпуска 0.6.0
    параметр `min_rest_outstation` УБРАН, а посадка 0.5.0 его несёт —
    третья клауза §12.12 п. 2(в). Прежде она была МЁРТВОЙ: разность
    считалась по именам, ВЗЯТЫМ ИЗ ДЕЙСТВУЮЩЕЙ записи (`given` и
    `unreadable` строятся только по объявленным именам), и убранное имя
    в неё не попадало никогда. Итог был два вердикта об одном пакете
    (готовность «в», рубеж выпускает — гейт и валидатор красные), то
    есть прямой отказ критерия K01 о РАВЕНСТВЕ вердиктов производителя,
    приёмника и формы.

    Минус (на прежнем коде): `contract_state == «совместимо»`,
    `verdict == «полна»`, `differences == []`, готовность «в», и
    `build_package`/`export_zip` ВЫПУСКАЮТ пакет с ячейкой
    `min_rest_outstation` — проверка краснеет на первом же
    утверждении."""
    _variant_without_outstation(tmp_path, monkeypatch)
    doc = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert cell["verdict"] == fp.VERDICT_RELAND
    assert cell["contract"]["differences"] == [REMOVED_WORDS]
    # По ДЕЙСТВУЮЩЕЙ записи недостачи нет — держит именно перепосадка.
    assert cell["missing"] == [] and cell["given"] == ["min_rest_home"]
    words = cell["problems"][0]["words"]
    assert words.startswith("перепосадка требуется") and REMOVED_WORDS in words
    assert cell["problems"][0]["proven"] is True

    # Один вердикт у всех читателей расчёта: свод, готовность, матрица.
    assert any(words in p for p in m.validate_model(doc))
    assert m.readiness(doc)["state"] == "а"
    mx = m.readiness_matrix(doc)
    for task in ("pairing", "rostering"):
        assert not any(c.get("ready") for c in mx[task].values()), task

    # НИ ОДИН путь выпуска пакета не отдаёт: и сборка (её зовут
    # «Передать в COSMOS» и кнопка COSMOS), и ручка СКАЧИВАНИЯ
    # `/export/task-package` (`export_zip`), которой пакет и уходил
    # заказчику мимо гейта.
    for call, mark in ((tp.build_package, "build"), (tp.export_zip, "zip")):
        with pytest.raises(tp.TaskPackageError) as e:
            call(_run_dir(tmp_path, doc, f"run-rm-{mark}"))
        assert e.value.code == tp.FAMILY_INCOMPLETE, mark
        assert e.value.items == ["rest_floors/—"], mark
        assert "повторите прогон по текущему каталогу" in str(e.value), mark

    # Равенство вердиктов производителя, приёмника и формы (K01):
    # пакет, выпущенный по ЗАКОННОМУ своду и подделанный подменой
    # model.json, краснит гейт ТЕМИ ЖЕ словами, что и рубеж.
    good = _doc_with(_landed(rev=bridge.stamp()["fingerprint"],
                             cells={"min_rest_home": {"value": 720,
                                                      "quote": "12:00"}}))
    assert fp.family_completeness(good)["rest_floors"]["verdict"] \
        == fp.VERDICT_COMPLETE
    problems, _c = _gate_on(tmp_path, good, doc, name="k01-removed")
    assert any(words in p for p in problems), problems


def test_a_param_carried_but_unknown_to_both_records_is_left_to_the_validator(
        tmp_path, monkeypatch, _fresh_bridge):
    """К-1 (граница): ячейка с именем, которого нет НИ В ОДНОЙ из двух
    записей, перепосадкой не объявляется — убирать было нечего. Такую
    посадку судит валидатор свода словами «вне словаря PARAMS», а
    расчёт полноты о ней молчит: закон §12.12 п. 2(в) — про параметр,
    который ДЕЙСТВУЮЩАЯ запись потеряла, а не про любое лишнее имя."""
    _variant(tmp_path, monkeypatch,
             {"layover_penalty": {"note": "примечание изменено"}})
    cells = copy.deepcopy(FULL_CELLS)
    cells["zzz_нет_такого"] = {"value": 1, "quote": "ц"}
    doc = _doc_with(_landed(rev=REV, cells=cells))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_COMPATIBLE
    assert cell["verdict"] == fp.VERDICT_COMPLETE
    assert cell["contract"]["differences"] == []
    assert any("zzz_нет_такого" in p and "вне словаря PARAMS" in p
               for p in m.validate_model(doc))


def test_an_unconditional_decline_counts_for_the_previous_record_too(
        tmp_path, monkeypatch, _fresh_bridge):
    """К-1 п. 2 (асимметрия ужесточения): клауза «слом наборов» судит
    полноту по ПРЕЖНЕЙ записи, и считать её надо тем же счётом, каким
    считается `missing`, — заданные ячейки ПЛЮС безусловные отказы.
    Прежде счёт был `given | unreadable`: посадка «A задана, B
    отклонена» при добавленном обязательном C давала один вердикт, а та
    же посадка с A и B ЗАДАННЫМИ — другой; об одной и той же правке
    записи говорились два разных слова.

    Исход обеих посадок с 12.09.2026 — УЖЕСТОЧЕНИЕ, то есть обычная
    недостача (К-2, решение ведущего): «запись ужесточена» / «неполна»,
    валидатор молчит, путь анкеты открыт. Предмет теста — РАВЕНСТВО
    вердиктов у двух посадок, и он тот же.

    Минус (на прежнем коде): после безусловного отказа по
    `min_rest_outstation` расчёт давал `совместимо`/`неполна`, а
    посадка с обоими заданными — `несовместимо`/«перепосадка
    требуется»; краснеет последняя тройка утверждений о равенстве."""
    _variant(tmp_path, monkeypatch, {"rest_floors": _add_param})
    doc = _doc_with(_landed(rev=REV))          # задан только min_rest_home
    # До ответа — ОБЫЧНАЯ недостача: путь анкеты открыт, валидатор чист.
    before = fp.family_completeness(doc)["rest_floors"]
    assert before["contract_state"] == fp.CONTRACT_COMPATIBLE
    assert before["verdict"] == fp.VERDICT_INCOMPLETE
    assert m.validate_model(doc) == []
    assert QID in fp.ensure_family_param_questions(doc)
    q = next(x for x in doc["questions"] if x["id"] == QID)
    assert fp.apply_param_answer(doc, q, "Не применяется", 2)["applied"]

    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["declined"] == ["min_rest_outstation"]
    assert cell["contract_state"] == fp.CONTRACT_HARDENED
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE
    assert cell["missing"] == ["min_rest_night"]
    assert m.validate_model(doc) == []       # недостача — не нарушение
    words = cell["problems"][0]["words"]
    assert "минимальный полный набор параметров изменился" in words
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-declined"))
    assert e.value.code == tp.FAMILY_INCOMPLETE
    assert "повторите прогон по текущему каталогу" not in str(e.value)

    # Та же правка записи при ОБОИХ заданных параметрах — тот же
    # вердикт теми же словами: асимметрии больше нет.
    both = _doc_with(_landed(rev=REV, cells=copy.deepcopy(FULL_CELLS)))
    twin = fp.family_completeness(both)["rest_floors"]
    assert twin["contract_state"] == cell["contract_state"]
    assert twin["verdict"] == cell["verdict"]
    assert twin["contract"]["differences"] == cell["contract"]["differences"]
    assert twin["contract"]["hardening"] == cell["contract"]["hardening"]


def test_previous_release_with_two_shortages_keeps_the_answer_path_open(
        tmp_path):
    """В-1 (регресс, ради которого правка): посадка ВЫПУЩЕННЫМ каталогом
    0.4.0 (запись семьи та же) и ДВЕ недостачи в разных семьях —
    валидатор свода чист, значит панель (`webui`) и ручка (`answers`)
    сохраняют свод и применяют ответ; готовность держится, выпуск
    отказывает обычной недостачей, совета «повторите прогон по текущему
    каталогу» в отказе нет."""
    doc = _doc_with(
        _landed(rev=REV_040),
        _landed(rid="R-N", family="night_series", rev=REV_040,
                cells={"max_consecutive_night_duties": {"value": 3,
                                                        "quote": "3"}}))
    got = fp.family_completeness(doc)
    assert got["rest_floors"]["contract_state"] == fp.CONTRACT_COMPATIBLE
    assert got["rest_floors"]["verdict"] == fp.VERDICT_INCOMPLETE
    assert got["night_series"]["verdict"] == fp.VERDICT_INCOMPLETE
    assert m.validate_model(doc) == []
    assert m.readiness(doc)["state"] == "а"
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-040"))
    assert e.value.code == tp.FAMILY_INCOMPLETE
    assert "повторите прогон по текущему каталогу" not in str(e.value)
    # Ответ применяется и свод остаётся годным — путь не закрыт.
    fp.ensure_family_param_questions(doc)
    q = next(x for x in doc["questions"] if x["id"] == QID)
    assert fp.apply_param_answer(doc, q, "600 минут", 2)["applied"]
    assert m.validate_model(doc) == []
    assert fp.family_completeness(doc)["rest_floors"]["verdict"] \
        == fp.VERDICT_COMPLETE


def test_unknown_fingerprint_is_an_ordinary_shortage_with_words(tmp_path):
    """В-1: отпечаток посадки не числится ни за одним выпуском —
    «совместимость не доказана»: готовность держится словами о
    недоказанности, но это НЕ перепосадка, валидатор молчит и ответ
    анкеты применяется."""
    doc = _doc_with(_landed(rev=REV_UNKNOWN))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["contract_state"] == fp.CONTRACT_UNPROVEN
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE
    words = cell["problems"][0]["words"]
    assert "совместимость посадки не доказана" in words
    assert not words.startswith("перепосадка требуется")
    assert m.validate_model(doc) == []
    assert words in m.readiness(doc)["rostering_open"]
    fp.ensure_family_param_questions(doc)
    q = next(x for x in doc["questions"] if x["id"] == QID)
    assert fp.apply_param_answer(doc, q, "600 минут", 2)["applied"]
    assert fp.family_completeness(doc)["rest_floors"]["verdict"] \
        == fp.VERDICT_COMPLETE


# --------------------------------------------------------------------
# В-2: заданное значение — то, что ЧИТАЕТСЯ в единице записи
# --------------------------------------------------------------------

@pytest.mark.parametrize("raw,label", [
    (None, "null"), ("", "пусто"), ("   ", "пробелы"), ([], "[]"), ({}, "{}"),
    (True, "true"), (False, "false"), ("abc", "строка-слово"),
    (float("nan"), "NaN"), (float("inf"), "inf"), ([1], "[1]"),
    ({"x": 1}, "{x:1}"),
])
def test_a_cell_that_does_not_read_in_the_unit_is_not_given(
        tmp_path, raw, label):
    """В-2: ячейка со значением, которое не читается в канонической
    единице параметра, заданной НЕ считается — недостача со словами
    «не читается в единице …», и выпуск отказывает."""
    doc = _doc_with(_landed(cells={"min_rest_home": {"value": 720,
                                                     "quote": "12:00"},
                                   "min_rest_outstation": {"value": raw,
                                                           "quote": "ц"}}))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["missing"] == ["min_rest_outstation"], label
    assert cell["given"] == ["min_rest_home"], label
    assert "min_rest_outstation" in cell["unreadable"], label
    words = cell["problems"][0]["words"]
    assert "ячейка посадки есть" in words and "не читается" in words
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-bad"))
    assert e.value.code == tp.FAMILY_INCOMPLETE


@pytest.mark.parametrize("raw,label", [
    (600, "целое"), (0, "законный ноль"), (0.0, "ноль дробью"),
    ("600", "числовая строка"), ("0", "ноль строкой"),
    ("10:00", "запись чч:мм"), ("10 ч", "с единицей словом"),
])
def test_a_readable_cell_is_given_including_a_lawful_zero(raw, label):
    """В-2 (положительный контроль): всё, что читается в единице
    параметра, — значение; законный ноль и числовая строка тоже."""
    doc = _doc_with(_landed(cells={"min_rest_home": {"value": 720,
                                                     "quote": "12:00"},
                                   "min_rest_outstation": {"value": raw,
                                                           "quote": "ц"}}))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["missing"] == [], label
    assert cell["verdict"] == fp.VERDICT_COMPLETE, label


def test_a_norm_by_qualifier_reads_structurally_not_as_one_number():
    """В-2: параметр с уточнителем («резерв по базам») несёт норму по
    клеткам — и строкой «GRT:1,PLY:2,SVG:1» (форма наборов H0), и
    объектом; кривая клетка нормы значением не считается."""
    def _reserve(cells):
        return _landed(rid="R-R", family="reserve_floors", cells=cells)
    for value in ("GRT:1,PLY:2,SVG:1", {"GRT": 1, "PLY": 2}):
        got = fp.family_completeness(_doc_with(_reserve(
            {"reserve_crews": {"value": value, "quote": "ц"}})))
        cell = got["reserve_floors"]
        assert cell["missing"] == [] and cell["given"] == ["reserve_crews"]
    bad = fp.family_completeness(_doc_with(_reserve(
        {"reserve_crews": {"value": "GRT:нет,PLY:2", "quote": "ц"}})))
    assert bad["reserve_floors"]["missing"] == ["reserve_crews"]
    assert "GRT" in bad["reserve_floors"]["unreadable"]["reserve_crews"]


# --------------------------------------------------------------------
# В-3: отказ закрывает параметр только БЕЗУСЛОВНЫЙ
# --------------------------------------------------------------------

def test_a_conditional_decline_does_not_close_the_param(tmp_path):
    """В-3: ответ «Не применяется для сотрудников базы SVO; в остальных
    базах 10:00» — условие, а не решение «требования нет»: параметр
    остаётся открытым на РЕАЛЬНОМ пути применения ответа, готовность
    держится, выпуск отказывает, а слова называют причину."""
    doc = _doc_with(_landed())
    assert fp.ensure_family_param_questions(doc) == [QID]
    q = next(x for x in doc["questions"] if x["id"] == QID)
    raw = "Не применяется для сотрудников базы SVO; в остальных базах 10:00"
    fp.apply_param_answer(doc, q, raw, 2)
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["declined"] == []
    assert cell["missing"] == ["min_rest_outstation"]
    assert cell["conditional"] == {"min_rest_outstation": QID}
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE
    assert "отказ С УСЛОВИЕМ" in cell["problems"][0]["words"]
    assert m.readiness(doc)["state"] == "а"
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-cond"))
    assert e.value.code == tp.FAMILY_INCOMPLETE


@pytest.mark.parametrize("raw", [
    "Не применяется",
    "Не применяется.",
    "Не применяется: такого требования у нас нет.",
    "Такого требования у авиакомпании нет.",
    "Не применяется. Такого требования у авиакомпании нет.",
])
def test_an_unconditional_decline_still_closes_the_param(raw):
    """В-3 (положительный контроль): отказ без условия — решение
    авиакомпании; повтор фразы отказа условием не является. Произвольное
    пояснение проверяется отдельно как незакрытый вопрос по закону K02."""
    doc = _doc_with(_landed())
    fp.ensure_family_param_questions(doc)
    q = next(x for x in doc["questions"] if x["id"] == QID)
    assert fp.apply_param_answer(doc, q, raw, 2)["applied"]
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["declined"] == ["min_rest_outstation"]
    assert cell["verdict"] == fp.VERDICT_COMPLETE


def test_a_free_explanation_after_decline_is_not_silently_discarded():
    """Прежний вход K01 сохраняется, но судится полным законом K02:
    код не угадывает смысл свободного пояснения и не снимает вопрос."""
    doc = _doc_with(_landed())
    fp.ensure_family_param_questions(doc)
    q = next(x for x in doc["questions"] if x["id"] == QID)
    raw = "Не применяется. Ночных серий в расписании не бывает."
    assert not fp.apply_param_answer(doc, q, raw, 2)["applied"]
    assert q["answer"]["status"] == "не разобран"
    assert m.is_open_question(q)
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["declined"] == []
    assert cell["missing"] == ["min_rest_outstation"]
    assert cell["conditional"] == {"min_rest_outstation": QID}
    assert cell["verdict"] == fp.VERDICT_INCOMPLETE


# --------------------------------------------------------------------
# С-1: кривая форма ячеек посадки — не трассировка, а слова
# --------------------------------------------------------------------

@pytest.mark.parametrize("params,label", [
    (["x"], "перечень"), ("720", "строка"), (5, "число"),
])
def test_malformed_params_give_no_cells_and_never_a_traceback(
        tmp_path, params, label):
    """С-1: `template_fit.params` не объект — ячеек нет вовсе (все
    параметры недостают), и ни готовность, ни матрица, ни расчёт, ни
    лестница не падают; кривую форму называет валидатор."""
    doc = _doc_with(_landed(cells=params))
    cell = fp.family_completeness(doc)["rest_floors"]
    assert cell["given"] == []
    assert cell["missing"] == ["min_rest_home", "min_rest_outstation"]
    assert m.readiness(doc)["state"] == "а"
    assert m.readiness_matrix(doc)["rostering"]["feasibility-hard"]["reasons"]
    assert any("params посадки не непустой объект" in p
               for p in m.validate_model(doc)), label
    assert tp.readiness_ladder(doc)["columns"]["rostering"]["state"] == "stop"
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-shape"))
    assert e.value.code == tp.FAMILY_INCOMPLETE


# --------------------------------------------------------------------
# С-2: основания барьера едут в манифесте ЧЕСТНО
# --------------------------------------------------------------------

def test_manifest_carries_barrier_grounds_as_notes_not_as_open_questions(
        tmp_path):
    """С-2 (и F3 ревью K06): слова барьера едут в `notes` столбца
    лестницы манифеста, а в счётчике открытых блокирующих вопросов их
    НЕТ — при нуле вопросов страница COSMOS показывала «открытых
    блокирующих вопросов: 1»."""
    doc = _doc_with(_landed(zone="ROSTERING"),
                    _landed(rid="R-D", zone="PAIRING", family="duty_limits",
                            cells={"max_pairing_days": {"value": 3,
                                                        "quote": "3 суток"}}))
    words = m.readiness_barriers(doc)[0]["words"]
    assert not [q for q in doc["questions"] if m.is_open_question(q)]
    files = tp.build_package(_run_dir(tmp_path, doc, "run-grounds"))
    ladder = json.loads(files["manifest.json"])["readiness_matrix"]
    col = ladder["columns"]["rostering"]
    assert col["state"] == "stop"
    assert f"барьер: {words}" in col["notes"]
    assert col["grounds"]["questions"]["open_blocking"] == 0
    assert col["grounds"]["questions"]["open_soft"] == 0
    # В незатронутом столбце слов барьера нет.
    assert all(not n.startswith("барьер: ")
               for n in ladder["columns"]["pairing"]["notes"])
    assert ladder["form_version"] == 1        # форма лестницы не тронута
    pkg = tmp_path / "grounds.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, counters = gate.check(pkg)
    assert problems == [] and counters.get("barriers_held") == [words]


def test_a_package_without_barriers_keeps_the_ladder_form(tmp_path):
    """С-2: у пакета БЕЗ барьеров лестница не меняется — замороженные
    пакеты (наборы H0/H0-B/H0-C, spec_version 1.18) остаются зелёными
    на гейте, который сверяет клетку побайтно."""
    doc = _doc_with(_landed(cells=copy.deepcopy(FULL_CELLS)))
    ladder = tp.readiness_ladder(doc)
    for task in ("pairing", "rostering"):
        col = ladder["columns"][task]
        assert all(not n.startswith("барьер: ") for n in col["notes"])
        assert set(col["grounds"]) == {"questions", "criterion",
                                       "data_declared"}
    assert ladder["form_version"] == 1


# --------------------------------------------------------------------
# Вопрос исполнителя: отказ называет ВСЕ основания остановленных задач
# --------------------------------------------------------------------

def test_refusal_names_the_grounds_of_every_task_without_modes(tmp_path):
    """Ответ ревьюера на вопрос исполнителя: код отказа один
    (`family-incomplete`), но названы и прочие основания — иначе за
    ответом на вопрос о параметре пришёл бы второй отказ о другом
    основании другой задачи."""
    doc = _doc_with(_landed(zone="ROSTERING"))
    doc["questions"].append(
        {"id": "Q-3a-ЖС", "part": "3a", "title": "Вопрос по жёсткой",
         "text": "?", "answer_format": {"type": "text"},
         "rule_refs": ["R-H"],
         "source_refs": [{"sid": "S1", "anchor": "п.1"}]})
    doc["rules"][0]["blockers"] = ["Q-3a-ЖС"]
    modes, _ = tp.allowed_modes(doc)
    assert not any(modes.values())
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-both"))
    assert e.value.code == tp.FAMILY_INCOMPLETE
    assert "Прочие основания остановки" in str(e.value)
    assert "блокированные жёсткие строки (1)" in str(e.value)
    assert "min_rest_outstation" in str(e.value)


# --------------------------------------------------------------------
# Мелкие находки ревью: Н-2 (порядок), Н-4 (кривой набор), Н-5 (код)
# --------------------------------------------------------------------

def test_words_do_not_depend_on_the_order_of_carriers_and_families(
        tmp_path, monkeypatch, _fresh_bridge):
    """Н-2: слова перепосадки и недостачи не зависят от порядка правил
    в своде — идентификаторы носителей отсортированы."""
    _variant(tmp_path, monkeypatch, {"rest_floors": _add_param})
    a = _doc_with(_landed(rid="R-A", rev=REV,
                          cells=copy.deepcopy(FULL_CELLS)),
                  _landed(rid="R-B", rev=REV,
                          cells=copy.deepcopy(FULL_CELLS)))
    b = copy.deepcopy(a)
    b["rules"].reverse()
    assert [p["words"] for p in fp.family_barrier_problems(a)] \
        == [p["words"] for p in fp.family_barrier_problems(b)]


def test_a_param_set_naming_an_unknown_param_is_dropped_with_words(
        monkeypatch):
    """Н-4: набор записи каталога, называющий параметр, которого у
    записи нет, прежде МОЛЧА сужался до известных имён — и кривая
    запись ослабляла требования. Теперь набор отбрасывается целиком,
    требования читаются по остальным, а беда названа словами."""
    record = {"id": "synthetic",
              "params": [{"name": "a", "unit": "count"},
                         {"name": "b", "unit": "count"}],
              "param_sets": [{"id": "s1", "params": ["a", "zzz"]}]}
    monkeypatch.setattr(bridge, "family_records",
                        lambda: {"synthetic": record})
    assert fp.missing_params("synthetic", {"a"}) == ["b"]
    assert fp.missing_params("synthetic", set()) == ["a", "b"]
    sets, problems = fp._sets_and_problems("synthetic")
    assert sets == [["a", "b"]]
    assert len(problems) == 1 and "zzz" in problems[0]
    assert "набор отброшен целиком" in problems[0]


def test_catalog_unavailable_refusal_carries_its_code(tmp_path, monkeypatch,
                                                      _fresh_bridge):
    """Н-5: отказ выпуска «каталог шаблонов недоступен» несёт КОД
    (`catalog-unavailable`) — тот же, что у двери COSMOS; прежде ручка
    выпуска клала в тело 409 `code: None`."""
    monkeypatch.setenv(bridge.ENV_CATALOG, str(tmp_path / "нет-каталога"))
    bridge.forget()
    doc = _doc_with(_landed())
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(_run_dir(tmp_path, doc, "run-nocat"))
    assert e.value.code == bridge.CatalogRequired.code == "catalog-unavailable"
    assert "каталог шаблонов недоступен" in str(e.value)


def test_a_lawful_list_of_codes_is_given(tmp_path):
    """Местный сторож структурных значений K01 бракова́л ЛЮБОЙ перечень
    и объявлял недостачей ЗАКОННЫЙ перечень обозначений у единицы
    `code_list` (`fleet_types` семьи `fleet_exclusions`). Закон один и
    живёт у A05 (`canonical_value`): значение судится по форме
    КАНОНИЧЕСКОЙ единицы — перечень строк тут значение, перечень чисел
    нет."""
    def _fleet(value):
        return _doc_with(_landed(rid="R-FL", family="fleet_exclusions",
                                 cells={"fleet_types": {"value": value,
                                                        "quote": "ц"}}))
    cell = fp.family_completeness(_fleet(["B737", "A320"]))["fleet_exclusions"]
    assert cell["given"] == ["fleet_types"] and cell["missing"] == []
    assert cell["verdict"] == fp.VERDICT_COMPLETE
    bad = fp.family_completeness(_fleet([3, 4]))["fleet_exclusions"]
    assert bad["missing"] == ["fleet_types"]
    assert "fleet_types" in bad["unreadable"]
