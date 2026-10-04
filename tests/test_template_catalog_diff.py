# -*- coding: utf-8 -*-
"""T16: «что изменилось» и подсказка уровня версии
(`template_catalog/diff.py`, §2.8 проекта шага 5, правило §5 схемы
записи).

Гейт файла: уровень каждого вида правки (старшая, младшая, патч),
следующий номер на 0.x и с 1.0.0, разница от пустого снимка, форма
записи журнала по закрытой схеме `$defs/versions_file`, симметрия
«добавлена/удалена» и детерминизм при перестановках.

Тела берутся из живой копии каталога только на ЧТЕНИЕ; ни один тест
файла ничего не пишет.
"""
import copy
import json
from pathlib import Path

import pytest

import template_catalog as tc
from template_catalog import diff
from template_catalog.validate import _import_jsonschema, _load_schema

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def body() -> dict:
    return tc.load(REPO / "catalog").body()


def _fam(b: dict, status: str = "есть") -> dict:
    return next(r for r in b["families"] if r.get("status") == status)


def _with(b: dict, patch) -> dict:
    out = copy.deepcopy(b)
    patch(out)
    return out


def _one(b: dict, patch) -> diff.Change:
    changes = diff.diff_bodies(b, _with(b, patch))
    assert len(changes) == 1, [(c.section, c.id, c.note) for c in changes]
    return changes[0]


# --------------------------------------------------- уровни правок
def _снять(b):
    _fam(b)["status"] = "снята"


def _новая_запись(b):
    r = copy.deepcopy(_fam(b))
    r["id"] = "zzz_proba"
    r["complements"] = []
    b["families"].append(r)


def _title(b):
    _fam(b)["title"] += " (иначе)"


def _note_записи(b):
    _fam(b)["note"] = "иная заметка записи"


def _zone(b):
    r = _fam(b)
    r["zone"] = "PAIRING" if r["zone"] != "PAIRING" else "ROSTERING"


def _unit(b):
    p = next(p for r in b["families"] for p in r["params"])
    p["unit"] = "count" if p["unit"] != "count" else "duration_minutes"


def _qualifier(b):
    p = next(p for r in b["families"] for p in r["params"])
    p["qualifier"] = "area" if p.get("qualifier") != "area" else None


def _note_параметра(b):
    p = next(p for r in b["families"] for p in r["params"])
    p["note"] = "иная заметка параметра"


def _новый_параметр(b):
    r = next(r for r in b["families"] if r["params"])
    r["params"].append({"name": "zzz_proba_param", "unit": "count",
                        "relation": "max", "qualifier": None, "note": None})


def _переезд_параметра(b):
    донор = next(r for r in b["families"] if len(r["params"]) > 1)
    приёмник = next(r for r in b["families"] if r["id"] != донор["id"])
    приёмник["params"].append(донор["params"].pop())


def _новый_вид_у_есть(b):
    r = next(r for r in b["families"] if r.get("status") == "есть"
             and "check_template" not in (r.get("executor_kinds") or []))
    r["executor_kinds"] = list(r["executor_kinds"]) + ["check_template"]


def _снятие_вида(b):
    r = next(r for r in b["families"] if len(r.get("executor_kinds") or []) > 1)
    r["executor_kinds"] = list(r["executor_kinds"])[:-1]


def _stand_tasks(b):
    r = next(r for r in b["families"] if r.get("stand_tasks"))
    r["stand_tasks"] = list(r["stand_tasks"])[:-1]


def _удаление_значения_словаря(b):
    b["dictionaries"]["weekdays"] = list(b["dictionaries"]["weekdays"])[:-1]


def _добавление_значения_словаря(b):
    b["dictionaries"]["weekdays"] = list(b["dictionaries"]["weekdays"]) \
        + ["zzz_проба"]


def _подключ_словаря(b):
    b["dictionaries"]["world_forms"] = dict(b["dictionaries"]["world_forms"])
    b["dictionaries"]["world_forms"].pop(
        sorted(b["dictionaries"]["world_forms"])[0])


def _lexicon_order(b):
    order = list(b["dictionaries"]["lexicon_order"])
    b["dictionaries"]["lexicon_order"] = [order[-1]] + order[:-1]


def _чтение_истории(b):
    _fam(b)["reads_history"] = {"kind": "window", "need": "иная потребность"}


def _размер_нарушения(b):
    r = _fam(b)
    r["violation_size"] = ("count" if r.get("violation_size") == "magnitude"
                           else "magnitude")


def _свидетель_природы(b):
    _fam(b)["nature_confirmed_by"] = "change_request"


def _единицы_перечнем(b):
    """Каталог до 0.5.0: единицы нарушения — перечень кодов."""
    b["dictionaries"]["violation_units"] = sorted(
        b["dictionaries"]["violation_units"])


СЛУЧАИ = [
    ("снятие записи", _снять, "families", "удалена", "major"),
    ("новая запись", _новая_запись, "families", "добавлена", "minor"),
    ("title записи", _title, "families", "изменена", "patch"),
    ("note записи", _note_записи, "families", "изменена", "patch"),
    ("зона записи", _zone, "families", "изменена", "major"),
    ("единица параметра", _unit, "families", "изменена", "major"),
    ("уточнитель параметра", _qualifier, "families", "изменена", "minor"),
    ("заметка параметра", _note_параметра, "families", "изменена", "patch"),
    ("новый параметр", _новый_параметр, "families", "изменена", "minor"),
    ("новый вид у «есть»", _новый_вид_у_есть, "families", "изменена", "major"),
    ("снятие вида", _снятие_вида, "families", "изменена", "minor"),
    ("стендовые задачи", _stand_tasks, "families", "изменена", "minor"),
    ("удаление значения словаря", _удаление_значения_словаря,
     "dictionaries", "изменена", "major"),
    ("добавление значения словаря", _добавление_значения_словаря,
     "dictionaries", "изменена", "minor"),
    ("удаление подключа словаря", _подключ_словаря, "dictionaries",
     "изменена", "major"),
    ("перестановка lexicon_order", _lexicon_order, "dictionaries",
     "изменена", "minor"),
    # Поля выпуска 0.5.0: они ничего не отменяют, а называют то, что
    # прежде выводилось из кода, — младшие (вне MAJOR_FIELDS).
    ("чтение истории", _чтение_истории, "families", "изменена", "minor"),
    ("размер нарушения", _размер_нарушения, "families", "изменена", "minor"),
    ("свидетель природы", _свидетель_природы, "families", "изменена",
     "minor"),
]


@pytest.mark.parametrize("имя,правка,раздел,вид,уровень", СЛУЧАИ,
                         ids=[c[0] for c in СЛУЧАИ])
def test_уровень_правки(body, имя, правка, раздел, вид, уровень):
    ch = _one(body, правка)
    assert (ch.section, ch.kind, ch.level) == (раздел, вид, уровень), \
        f"{имя}: {ch}"
    assert ch.note and ch.note.strip() == ch.note
    assert diff.suggest_level([ch]) == уровень


def test_переезд_параметра_между_семьями_старшая(body, capsys):
    """Уход имени из записи — старшая: к имени привязан исполнитель, и
    переезд в другую семью виден здесь тем же уходом."""
    changes = diff.diff_bodies(body, _with(body, _переезд_параметра))
    assert len(changes) == 2, [(c.id, c.note) for c in changes]
    assert diff.suggest_level(changes) == "major"
    assert {c.level for c in changes} == {"major", "minor"}
    print(f"\nT16 переезд: {[(c.id, c.level, c.note) for c in changes]}")


def test_пустая_разница_и_уровень_same(body):
    assert diff.diff_bodies(body, copy.deepcopy(body)) == []
    assert diff.suggest_level([]) == "same"
    assert diff.next_version("0.2.0", "same") == "0.2.0"
    assert diff.next_version("0.2.0-draft.3", "same") == "0.2.0-draft.3"


def test_снята_обратно_в_есть_даёт_добавлена_младшую(body):
    снятая = _with(body, _снять)
    changes = diff.diff_bodies(снятая, body)
    assert [(c.kind, c.level) for c in changes] == [("добавлена", "minor")]
    assert "возвращена" in changes[0].note


# ------------------------------------------------- первый снимок
def test_разница_от_пустого_снимка_вся_добавлена(body, capsys):
    changes = diff.diff_bodies(None, body)
    assert changes and {c.kind for c in changes} == {"добавлена"}
    assert {c.level for c in changes} == {"minor"}
    assert diff.suggest_level(changes) == "minor"
    ожидание = (len(body["families"]) + len(body["criteria"])
                + len(body["dictionaries"]))
    assert len(changes) == ожидание
    assert all(c.note for c in changes), "заметка первого снимка пуста"
    print(f"\nT16 первый снимок: правок {len(changes)} "
          f"(семей {len(body['families'])}, критериев {len(body['criteria'])}, "
          f"ключей словарей {len(body['dictionaries'])})")


def test_симметрия_добавлена_удалена(body):
    """Разница в обратную сторону называет то же место другим видом."""
    прибавка = _with(body, _новая_запись)
    вперёд = diff.diff_bodies(body, прибавка)
    назад = diff.diff_bodies(прибавка, body)
    assert [(c.section, c.id, c.kind, c.level) for c in вперёд] == \
        [("families", "zzz_proba", "добавлена", "minor")]
    assert [(c.section, c.id, c.kind, c.level) for c in назад] == \
        [("families", "zzz_proba", "удалена", "major")]


# --------------------------------------------------- детерминизм
def test_перестановки_не_меняют_результата(body):
    """Тела приводятся к канону: порядок записей, ключей и списков-
    множеств на вход не влияет."""
    a = copy.deepcopy(body)
    b = _with(body, _title)
    b["families"] = list(reversed(b["families"]))
    b["criteria"] = list(reversed(b["criteria"]))
    b["dictionaries"] = dict(reversed(list(b["dictionaries"].items())))
    прямо = diff.diff_bodies(a, _with(body, _title))
    вперемешку = diff.diff_bodies(a, b)
    assert [(c.section, c.id, c.kind, c.note, c.level) for c in прямо] == \
        [(c.section, c.id, c.kind, c.note, c.level) for c in вперемешку]
    assert [c.id for c in diff.diff_bodies(None, body)] == \
        [c.id for c in diff.diff_bodies(None, b)][:len(prev)] \
        if (prev := [c.id for c in diff.diff_bodies(None, body)]) else True


def test_diff_catalogs_и_diff_record(body):
    cat = tc.load(REPO / "catalog")
    assert diff.diff_catalogs(cat, cat) == []
    assert len(diff.diff_catalogs(None, cat)) == len(diff.diff_bodies(None,
                                                                     body))
    rec = _fam(body)
    assert diff.diff_record("families", rec, copy.deepcopy(rec)) is None
    assert diff.diff_record("families", None, None) is None
    ch = diff.diff_record("families", None, rec)
    assert (ch.kind, ch.level) == ("добавлена", "minor")
    ch = diff.diff_record("families", rec, None)
    assert (ch.kind, ch.level) == ("удалена", "major")


# ------------------------------------------------ следующий номер
def test_next_version_на_нулевой_старшей_и_с_единицы():
    assert diff.next_version("0.2.0", "major") == "0.3.0"
    assert diff.next_version("0.2.0", "minor") == "0.3.0"
    assert diff.next_version("0.2.3", "patch") == "0.2.4"
    assert diff.next_version("0.2.0-draft.7", "minor") == "0.3.0"
    assert diff.next_version("1.4.2", "major") == "2.0.0"
    assert diff.next_version("1.4.2", "minor") == "1.5.0"
    assert diff.next_version("1.4.2", "patch") == "1.4.3"
    with pytest.raises(ValueError):
        diff.next_version("0.2.0", "средняя")
    with pytest.raises(ValueError):
        diff.next_version("не версия", "minor")


# ------------------------------------------------ форма журнала
def test_форма_записи_журнала_по_закрытой_схеме(body, capsys):
    """`journal()` отдаёт ровно четыре поля: форма
    `$defs/versions_file.changes[]` закрыта (additionalProperties: false),
    и `level` в неё не попадает."""
    changes = diff.diff_bodies(None, body)
    rows = diff.as_journal(changes)
    assert rows and all(set(r) == {"section", "id", "kind", "note"}
                        for r in rows)
    assert all(r["kind"] in diff.KINDS for r in rows)
    schema = _load_schema()
    jsonschema = _import_jsonschema()
    if schema is None or jsonschema is None:
        pytest.skip("схема или jsonschema недоступны")
    sub = {"$schema": schema.get("$schema"), "$defs": schema["$defs"],
           "$ref": "#/$defs/versions_file"}
    doc = {"form_version": 1,
           "versions": [{"version": "0.2.0", "fingerprint": "a" * 64,
                         "released_at": "2026-09-08",
                         "released_by": "владелец", "code": "b" * 40,
                         "changes": rows}]}
    errors = list(jsonschema.Draft202012Validator(sub).iter_errors(doc))
    assert errors == [], [e.message for e in errors[:3]]
    print(f"\nT16 журнал: записей {len(rows)}, схема закрытой формы "
          f"{len(errors)} ошибок")


def test_смена_вида_значения_словаря_старшая(body):
    """Перечень, ставший объектом, ломает всех, кто ключ читает."""
    ch = _one(body, lambda b: b["dictionaries"].update(
        {"weekdays": {"было": "перечнем"}}))
    assert (ch.section, ch.kind, ch.level) == ("dictionaries", "изменена",
                                               "major")
    ch2 = _one(body, lambda b: b["dictionaries"].update(
        {"acceptance_default": b["dictionaries"]["acceptance_default"] + "."}))
    assert ch2.level == "minor"


def test_карта_единиц_старшая_правка_а_номер_следующий_на_ноль_икс(body):
    """Выпуск 0.5.0: единицы нарушения стали картой «код → слово».

    Смена ВИДА значения словаря — старшая правка по закону самого
    каталога: ключ читают все, и читатель перечня на карте ломается.
    Номер при этом всё равно 0.MINOR+1.0 — на 0.x старшая и младшая
    правки дают один и тот же следующий номер, и это честно названо и
    в журнале, и в заметке выпуска."""
    прежнее = _with(body, _единицы_перечнем)
    changes = diff.diff_bodies(прежнее, body)
    assert len(changes) == 1, [(c.section, c.id) for c in changes]
    ch = changes[0]
    assert ((ch.section, ch.id, ch.kind, ch.level)
            == ("dictionaries", "violation_units", "изменена", "major"))
    assert diff.suggest_level(changes) == "major"
    assert diff.next_version("0.4.0", "major") == "0.5.0"
    assert diff.next_version("0.4.0", "minor") == "0.5.0"


def test_заметка_не_длиннее_предела(body):
    длинная = _with(body, lambda b: b["families"][0].update(
        {"title": "х" * 5000}))
    ch = diff.diff_bodies(body, длинная)[0]
    assert len(ch.note) <= diff.NOTE_LIMIT
    assert json.dumps(ch.journal(), ensure_ascii=False)
