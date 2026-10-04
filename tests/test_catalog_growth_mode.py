# -*- coding: utf-8 -*-
"""Режим роста каталога шаблонов: сам механизм.

Каталог РАСТЁТ, а судьи и тесты пишутся на неподвижный каталог — и
ломаются на самом росте, а не на проверяемом правиле (09.09.2026:
четыре попытки агента из пяти сорвались так). Режим роста
(`COSMOS_TEST_CATALOG_GROWN`) подмешивает в КАЖДУЮ копию каталога
синтетическую семью и синтетический критерий, которых в живом каталоге
нет и не будет, — и весь набор тестов прогоняется на выросшем каталоге.

Здесь судится сам механизм: без переменной копия прежняя; с переменной
синтетические записи видны читателю, привязкам, посеву, стенду и
паритету; вердикты судей — ЗАЯВЛЕННЫЕ («исполнителя нет»), а не поломка
на числе записей. Три значения переменной:

  extension — семья-расширение без исполнителя (кандидат до реализации);
  clone     — клон существующей семьи с её исполнителями (покрытие есть);
  <путь>    — заготовка настоящей заявки (предполётная проверка задания).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import catalog_copies as cc                                       # noqa: E402
import template_catalog as tc                                     # noqa: E402
from template_catalog import growth                               # noqa: E402

ENV = growth.MODE_ENV


@pytest.fixture(autouse=True)
def _forget():
    """Дверь каталога — своя у каждого теста файла."""
    from vmp import catalog_access as ca
    yield
    ca.forget()


def _mode(monkeypatch, value: str | None):
    if value is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, value)


# --------------------------------------------------------- переменная
def test_без_переменной_режима_нет_и_копия_прежняя(tmp_path, monkeypatch):
    _mode(monkeypatch, None)
    assert growth.mode() is None
    folder = cc.copy_catalog(tmp_path)
    ids = set(tc.load(folder).families())
    assert not (ids & growth.probe_ids()), \
        "без переменной синтетических записей в копии быть не должно"
    assert not (folder / cc.SEED_FILE).is_file(), \
        "без режима роста копия посева не пишется"


def test_значения_переменной_разобраны_а_опечатка_отказ(monkeypatch):
    for value, want in (("1", growth.MODE_EXTENSION),
                        ("extension", growth.MODE_EXTENSION),
                        ("clone", growth.MODE_CLONE),
                        ("да", growth.MODE_EXTENSION)):
        _mode(monkeypatch, value)
        assert growth.mode() == want, value
    for value in ("0", "нет", ""):
        _mode(monkeypatch, value)
        assert growth.mode() is None, value
    # опечатка не превращается молча в «режим выключен»: прогон «в режиме
    # роста» обязан либо расти, либо отказать словами
    _mode(monkeypatch, "extention")
    with pytest.raises(growth.GrowthError) as e:
        growth.mode()
    assert ENV in str(e.value) and growth.MODE_CLONE in str(e.value)


# ------------------------------------------------- синтетические записи
@pytest.mark.parametrize("режим", [growth.MODE_EXTENSION, growth.MODE_CLONE])
def test_копия_в_режиме_роста_несёт_синтетические_записи(
        tmp_path, monkeypatch, режим):
    _mode(monkeypatch, режим)
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    fam = cat.family(growth.PROBE_FAMILY_ID)
    crit = cat.criterion(growth.PROBE_CRITERION_ID)
    assert fam and crit
    # имя говорящее и на живую запись не похоже
    assert growth.PROBE_FAMILY_ID.startswith("zz_probe")
    assert growth.PROBE_MARK in str(fam.get("note"))
    assert growth.PROBE_MARK in str(crit.get("note"))
    # запись видна ЧИТАТЕЛЮ как обычная семья «есть»
    assert growth.PROBE_FAMILY_ID in cat.families()
    assert fam["status"] == "есть" and fam["history"]


@pytest.mark.parametrize("режим", [growth.MODE_EXTENSION, growth.MODE_CLONE])
def test_выросший_каталог_годен_и_предупреждения_названы(
        tmp_path, monkeypatch, режим):
    """Валидатор на выросшем каталоге не даёт НИ ОДНОЙ ошибки, а его
    предупреждения названы поимённо: у синтетической записи нет
    стендовых задач — их пишет агент вместе с исполнителем."""
    _mode(monkeypatch, режим)
    cat = tc.load(cc.copy_catalog(tmp_path))
    problems = cat.validate()
    errors = [p for p in problems if p.severity == "error"]
    assert errors == [], [str(p) for p in errors]
    warns = [p for p in problems if p.severity != "error"]
    assert {p.code for p in warns} == {"X07"}, [str(p) for p in warns]
    assert all(any(i in p.where for i in growth.probe_ids()) for p in warns)


def test_расширение_без_исполнителя_а_клон_с_исполнителем(
        tmp_path, monkeypatch):
    """Два режима отвечают на разные вопросы: расширение — «исполнителя
    нет» (так выглядит кандидат до реализации), клон — «исполнитель
    есть» (запись, покрытие которой уже написано)."""
    _mode(monkeypatch, growth.MODE_EXTENSION)
    ext = _executors(cc.copy_catalog(tmp_path, name="ext"))
    assert ext and all(e["state"] == "нет" for e in ext), ext
    assert all(cc.PROBE_NOTE in str(e.get("note")) or e.get("symbol") is None
               for e in ext)

    _mode(monkeypatch, growth.MODE_CLONE)
    clone = _executors(cc.copy_catalog(tmp_path, name="clone"))
    assert clone and all(e["state"] == "есть" for e in clone), clone
    assert all(e.get("symbol") for e in clone)


def test_три_поля_0_5_0_у_пробной_записи_обеих_ветвей(tmp_path, monkeypatch):
    """Поля выпуска 0.5.0 у синтетической записи — по её СОБСТВЕННОЙ
    природе и виду, а не по образцу: природа пробы «расписание», значит
    истории она не читает (X19) и свидетеля природы у неё нет (X17), а
    размер нарушения задан ровно у мягкой записи (X16). Иначе клон
    мягкой семьи истории приносил бы в копию чужие ответы."""
    for режим in (growth.MODE_EXTENSION, growth.MODE_CLONE):
        _mode(monkeypatch, режим)
        cat = tc.load(cc.copy_catalog(tmp_path, name=f"поля-{режим}"))
        rec = cat.family(growth.PROBE_FAMILY_ID)
        assert rec["nature"] == "schedule"
        assert rec["reads_history"] is None, режим
        assert rec["nature_confirmed_by"] == "none", режим
        assert ((rec["violation_size"] is not None)
                == (rec["kind"] == "SOFT")), режим
        assert (rec["nature_confirmed_by"]
                in cat.dictionaries()["nature_witnesses"])
        # запись пробы заведена «синтетикой», а не заявкой — X18 её не
        # касается, и свидетеля у неё честно нет
        assert rec["origin"]["kind"] != "заявка", режим


def _executors(folder: Path) -> list:
    doc = json.loads((folder / cc.BINDINGS_FILE).read_text(encoding="utf-8"))
    row = next(r for r in doc["bindings"]
               if r["id"] == growth.PROBE_FAMILY_ID)
    return row["executors"]


def test_имя_параметра_в_двух_записях_каталог_не_пускает(
        tmp_path, monkeypatch):
    """Проектное решение режима клона названо правилом, а не мнением:
    повторить имена параметров образца клон не вправе — X02 читателя
    запрещает одно имя в двух записях, а карта `params()` плоская и
    молча отдала бы имя последней записи."""
    _mode(monkeypatch, None)
    образец = growth.clone_source(tc.load(ROOT / "catalog").families_full())
    folder = cc.copy_catalog(tmp_path, families={
        "zz_probe_same_names": lambda r: r.update(
            {**{k: v for k, v in образец.items()
                if k not in ("id", "subject_words", "complements",
                             "stand_tasks", "history")},
             "id": "zz_probe_same_names", "subject_words": None,
             "complements": [], "stand_tasks": []})})
    коды = {p.code for p in tc.load(folder).validate()
            if p.severity == "error" and "zz_probe_same_names" in p.where}
    assert "X02" in коды, коды
    # и клон режима роста имён образца НЕ повторяет
    assert growth.probe_family(mode_name=growth.MODE_CLONE,
                               source=образец)["params"] == []


# ----------------------------------------------------- носители копии
@pytest.mark.parametrize("режим", [growth.MODE_EXTENSION, growth.MODE_CLONE])
def test_посев_привязок_копии_знает_синтетические_записи(
        tmp_path, monkeypatch, режим):
    """Посев привязок — третий носитель знания об исполнителях, и у
    копии он свой: без него генератор отказал бы «запись каталога в
    посеве не названа» на любой записи, которой в дереве ещё нет."""
    _mode(monkeypatch, режим)
    folder = cc.copy_catalog(tmp_path)
    seed = json.loads((folder / cc.SEED_FILE).read_text(encoding="utf-8"))
    assert f"families/{growth.PROBE_FAMILY_ID}" in seed["records"]
    assert f"criteria/{growth.PROBE_CRITERION_ID}" in seed["records"]
    # дверь посева переключается вместе с дверью каталога
    from vmp import bindings_build as bb
    assert bb.SEED_ENV == "CREW_BINDINGS_SEED"


def test_дверь_посева_переключается_use_catalog(tmp_path, monkeypatch):
    _mode(monkeypatch, growth.MODE_EXTENSION)
    from vmp import bindings_build as bb
    folder = cc.copy_catalog(tmp_path)
    cc.use_catalog(monkeypatch, folder)
    assert bb.seed_path() == folder / cc.SEED_FILE
    assert f"families/{growth.PROBE_FAMILY_ID}" in bb.load_seed()["records"]


@pytest.mark.parametrize("режим", [growth.MODE_EXTENSION, growth.MODE_CLONE])
def test_генератор_и_гейт_привязок_на_выросшем_каталоге(
        tmp_path, monkeypatch, режим):
    """Генератор привязок собирается, а гейт называет пробел покрытия
    СЛОВАМИ в клетке записи — и не ломается на числе записей."""
    _mode(monkeypatch, режим)
    from vmp import bindings_build as bb
    from vmp import bindings_gate as bg
    folder = cc.copy_catalog(tmp_path)
    cc.use_catalog(monkeypatch, folder)
    cat = tc.load(folder)
    doc = bb.build(cat, commit="0" * 40,
                   generated_at="2026-01-01T00:00:00+00:00",
                   stand_results=None, seed=bb.load_seed())
    rep = bg.gate(cat, doc, None)
    клетка = rep.matrix[f"families/{growth.PROBE_FAMILY_ID}"]
    состояния = {s for cells in клетка.values() for s in cells.values()}
    assert состояния == ({"есть"} if режим == growth.MODE_CLONE
                         else {"нет"}), клетка
    # ошибки гейта — только про синтетические записи и только словами
    assert all(any(i in w for i in growth.probe_ids()) for w in rep.errors), \
        rep.errors


def test_стенд_знает_судей_из_кода_а_не_из_перечня(tmp_path, monkeypatch):
    """Перечень судей стенда ВЫВОДИТСЯ из исполнителей планов, а не
    пишется руками: иначе каждая новая форма красила бы задачи записи
    «судьи символа у стенда нет» по построению."""
    _mode(monkeypatch, growth.MODE_EXTENSION)
    from vmp import stand_tasks as st
    from vmp.run import logic_checks as lc
    assert st._logic_symbols() == frozenset(
        fn.__name__ for fn in lc._EXECUTORS.values())
    folder = cc.copy_catalog(tmp_path)
    cc.use_catalog(monkeypatch, folder)
    # у синтетической записи задач стенда нет — и это ЗАЯВЛЕННЫЙ пробел
    assert tc.load(folder).family(growth.PROBE_FAMILY_ID)["stand_tasks"] == []


def test_копия_равная_выпуску_синтетических_записей_не_несёт(
        tmp_path, monkeypatch):
    """`candidates=False` — копия обязана РАВНЯТЬСЯ выпуску: её
    отпечаток предмет суда, и рост каталога красил бы H03 «отпечаток
    записи журнала не равен шапке» по построению режима."""
    _mode(monkeypatch, growth.MODE_EXTENSION)
    folder = cc.copy_catalog(tmp_path, releases=True, candidates=False)
    cat = tc.load(folder)
    assert not (set(cat.families()) & growth.probe_ids())
    assert [p for p in cat.validate() if p.code == "H03"] == []


# ------------------------------------------- заявленные пробелы словами
def test_заявленные_пробелы_названы_а_чужое_красное_остаётся_красным(
        monkeypatch):
    """`probe_only` — договор режима: красное объясняется ТОЛЬКО
    синтетическими записями. Без режима роста красное остаётся красным
    всегда."""
    свои = (f"L19: families/{growth.PROBE_FAMILY_ID}: вид прибавлен, "
            f"исполнителя нет; L11: criteria/"
            f"{growth.PROBE_CRITERION_ID}.stand_tasks: без задач")
    чужое = свои + "; L05: duty_limits: единицу не подтверждает никто"
    _mode(monkeypatch, None)
    assert cc.probe_only(свои) is False
    _mode(monkeypatch, growth.MODE_EXTENSION)
    assert cc.probe_only(свои) is True
    assert cc.probe_only(чужое) is False
    assert cc.probe_only("") is False


def test_перечень_ожидаемых_пробелов_заготовки():
    """Пробелы заготовки названы одними и теми же словами у режима роста
    и у предполётной проверки: перечень один."""
    запись = growth.probe_family(mode_name=growth.MODE_EXTENSION)
    пробелы = growth.expected_gaps(запись, "families")
    assert any("исполнителя нет" in w and "check_code" in w for w in пробелы)
    assert any("задач стенда нет" in w for w in пробелы)
    assert any("слов-носителей" in w for w in пробелы)


# --------------------------------------------- режим «заготовка заявки»
def test_режим_заготовки_растит_каталог_записью_настоящей_заявки(
        tmp_path, monkeypatch):
    """Значение переменной — путь к файлу заготовки: набор тестов копий
    каталога прогоняется на записи, которую предстоит реализовать, а не
    на выдуманной. Так предполётная проверка задания агенту судит ту же
    запись, что достанется агенту."""
    заготовка = growth.draft_candidate(
        section="families", record_id="zz_probe_from_draft",
        draft=_черновик(),
        request_id=growth.probe_request_id(),
        cr_key=growth.cr_key_of(growth.probe_request_id()))
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(заготовка, ensure_ascii=False),
                    encoding="utf-8")
    _mode(monkeypatch, str(path))
    assert growth.mode() == growth.MODE_DRAFT
    assert growth.probe_ids() == {"zz_probe_from_draft"}
    folder = cc.copy_catalog(tmp_path / "копия")
    cat = tc.load(folder)
    запись = cat.family("zz_probe_from_draft")
    assert запись and запись["status"] == "есть"
    assert not (set(cat.families()) & {growth.PROBE_FAMILY_ID}), \
        "в режиме заготовки выдуманной семьи в копии быть не должно"
    assert [p for p in cat.validate() if p.severity == "error"] == []
    seed = json.loads((folder / cc.SEED_FILE).read_text(encoding="utf-8"))
    строки = seed["records"]["families/zz_probe_from_draft"]
    assert [r["state"] for r in строки] == ["нет"]


def _черновик() -> dict:
    """Черновик заявки: запись без состояния, истории и задач стенда."""
    return growth.draft_of(growth.probe_family(
        mode_name=growth.MODE_EXTENSION))
