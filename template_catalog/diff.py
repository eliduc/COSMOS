# -*- coding: utf-8 -*-
"""«Что изменилось» между двумя телами каталога и подсказка уровня
версии (§2.8 проекта шага 5, правило §5 схемы записи).

Одно вычисление на все стороны: журнал версий (`versions[].changes`),
подсказка следующего номера при сборке и выпуске, экран «Версии» и
сравнение черновиков берут разницу отсюда. Сравниваются ТЕЛА КАНОНА
(`canon.normalize` внутри), поэтому перестановка ключей и списков-
множеств результата не меняет.

Уровень правки (`Change.level`) — вклад одной записи; общий уровень
набора даёт `suggest_level`:

    старшая (major)  снятие записи (есть → снята), удаление записи или
                     ключа словаря, переименование id (видно как
                     удаление + добавление), смена kind / zone / nature /
                     violation_unit записи, смена unit или relation
                     параметра, уход параметра из записи (в том числе
                     переезд в другую семью), удаление значения перечня
                     или подключа объекта словаря, СМЕНА ВИДА значения
                     словаря (перечень стал картой — `violation_units` в
                     0.5.0: ключ читают все, и читатель перечня на карте
                     ломается), новый вид в executor_kinds /
                     computable_by записи «есть»;
    младшая (minor)  новая запись (в том числе снята → есть), новый
                     параметр, набор, условие, новое значение или подключ
                     словаря, перестановка lexicon_order, stand_tasks,
                     history, semantics, subject, subject_words,
                     complements, origin, check_templates, rule_form,
                     acceptance, qualifier параметра, снятие вида,
                     reads_history, violation_size, nature_confirmed_by
                     записи (поля 0.5.0: они ничего не отменяют, а
                     называют то, что прежде выводилось из кода);
    патч (patch)     title записи, note записи, note параметра.

Поле, не названное в таблице, при изменении даёт младшую: молчаливо
понижать до патча незнакомую правку — значит обещать совместимость,
которой никто не проверял.

`Change.level` в журнал не пишется: форма записи журнала закрыта
(`$defs/versions_file`), и наружу идут ровно четыре поля `journal()`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import canon
from .manifest import parse_version

#: Разделы тела канона в порядке показа.
SECTIONS = ("families", "criteria", "dictionaries")
#: Виды правки — ровно перечень схемы `$defs/versions_file.changes[].kind`.
KINDS = ("добавлена", "изменена", "удалена")
_LEVELS = ("same", "patch", "minor", "major")
_RANK = {name: i for i, name in enumerate(_LEVELS)}

#: Поля записи, чья смена — старшая правка.
MAJOR_FIELDS = ("kind", "zone", "nature", "violation_unit")
#: Поля записи, чья смена — патч.
PATCH_FIELDS = ("title", "note")
#: Поля параметра: старшая / патч; прочее (qualifier и др.) — младшая.
PARAM_MAJOR_FIELDS = ("unit", "relation")
PARAM_PATCH_FIELDS = ("note",)
#: Списки записи, сравниваемые по имени элемента: (ключ, поле имени,
#: уровень УХОДА элемента). Уход параметра, набора или условия —
#: старшая: к их именам привязаны исполнители (ручка, форма, условие), и
#: переезд имени в другую семью выглядит здесь тем же уходом. Уход
#: стендовой задачи — младшая: задача судит исполнителя, а не наоборот.
_KEYED_LISTS = (("params", "name", "major"), ("param_sets", "id", "major"),
                ("conditions", "name", "major"),
                ("stand_tasks", "id", "minor"))
#: Перечни видов исполнителей: новый вид у записи «есть» — старшая.
_KIND_LISTS = ("executor_kinds", "computable_by")
#: Длина заметки: журнал читают глазами, простыня в нём не нужна.
NOTE_LIMIT = 300


@dataclass(frozen=True)
class Change:
    section: str      # families | criteria | dictionaries
    id: str           # id записи либо ключ словаря
    kind: str         # добавлена | изменена | удалена
    note: str         # слова о правке
    level: str        # major | minor | patch — вклад этой правки

    def journal(self) -> dict:
        """Запись журнала версий: ровно четыре поля закрытой формы."""
        return {"section": self.section, "id": self.id, "kind": self.kind,
                "note": self.note}


def _worst(*levels) -> str:
    out = "same"
    for lv in levels:
        if _RANK.get(str(lv), 0) > _RANK[out]:
            out = str(lv)
    return out


def _cut(note: str) -> str:
    note = " ".join(str(note).split())
    if not note:
        note = "правка без описания"
    return note if len(note) <= NOTE_LIMIT else note[:NOTE_LIMIT - 1] + "…"


def _short(v) -> str:
    if v is None or isinstance(v, (str, int, bool)):
        return "null" if v is None else str(v)
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _same(a, b) -> bool:
    return json.dumps(a, ensure_ascii=False, sort_keys=True) == \
        json.dumps(b, ensure_ascii=False, sort_keys=True)


def _by_name(items, field: str) -> dict:
    out: dict = {}
    for x in items or []:
        if isinstance(x, dict):
            out[str(x.get(field))] = x
    return out


# ----------------------------------------------------------- записи
def _record_changes(old: dict, new: dict) -> tuple:
    """(уровень, [слова]) по одной паре записей одного id."""
    level = "same"
    words: list = []
    special = {"id", "status", "request_id", "executor_kinds",
               "computable_by"} | {k for k, _f, _l in _KEYED_LISTS}
    plain: list = []
    detail: list = []
    for key in sorted((set(old) | set(new)) - special):
        if _same(old.get(key), new.get(key)):
            continue
        if key in MAJOR_FIELDS:
            level = _worst(level, "major")
            detail.append(f"{key}: {_short(old.get(key))} → "
                          f"{_short(new.get(key))}")
        elif key in PATCH_FIELDS:
            level = _worst(level, "patch")
            plain.append(key)
        else:
            level = _worst(level, "minor")
            plain.append(key)
    if plain:
        words.append("поля: " + ", ".join(plain))
    words.extend(detail)
    # списки с именованными элементами
    for key, field, gone_level in _KEYED_LISTS:
        a, b = _by_name(old.get(key), field), _by_name(new.get(key), field)
        added, removed = sorted(set(b) - set(a)), sorted(set(a) - set(b))
        if added:
            level = _worst(level, "minor")
        if removed:
            level = _worst(level, gone_level)
        if added or removed:
            words.append(f"{key}: "
                         + ", ".join([f"+{n}" for n in added]
                                     + [f"−{n}" for n in removed]))
        for name in sorted(set(a) & set(b)):
            for f in sorted(set(a[name]) | set(b[name])):
                if _same(a[name].get(f), b[name].get(f)):
                    continue
                if key == "params" and f in PARAM_MAJOR_FIELDS:
                    level = _worst(level, "major")
                elif key == "params" and f in PARAM_PATCH_FIELDS:
                    level = _worst(level, "patch")
                    continue
                else:
                    level = _worst(level, "minor")
                words.append(f"{key}.{name}.{f}: {_short(a[name].get(f))} → "
                             f"{_short(b[name].get(f))}")
    # виды исполнителей: новый вид у записи «есть» — старшая
    present = new.get("status") == "есть"
    for key in _KIND_LISTS:
        a = {str(x) for x in (old.get(key) or [])}
        b = {str(x) for x in (new.get(key) or [])}
        added, removed = sorted(b - a), sorted(a - b)
        if added:
            level = _worst(level, "major" if present else "minor")
        if removed:
            level = _worst(level, "minor")
        if added or removed:
            words.append(f"{key}: "
                         + ", ".join([f"+{n}" for n in added]
                                     + [f"−{n}" for n in removed]))
    return level, words


def diff_record(section: str, old: dict | None, new: dict | None):
    """Разница одной записи; равные записи — None."""
    if old is None and new is None:
        return None
    if old is None:
        rid = str((new or {}).get("id"))
        return Change(section, rid, "добавлена",
                      _cut(f"новая запись: {new.get('title') or rid}"),
                      "minor")
    if new is None:
        rid = str(old.get("id"))
        return Change(section, rid, "удалена",
                      _cut(f"запись убрана из каталога: "
                           f"{old.get('title') or rid}"), "major")
    rid = str(new.get("id") or old.get("id"))
    was, now = old.get("status"), new.get("status")
    if was != "снята" and now == "снята":
        _level, words = _record_changes(old, new)
        note = "запись снята (status: есть → снята)"
        extra = [w for w in words if not w.startswith("поля: ")]
        return Change(section, rid, "удалена",
                      _cut("; ".join([note] + extra)), "major")
    if was == "снята" and now != "снята":
        level, words = _record_changes(old, new)
        note = "запись возвращена (status: снята → есть)"
        return Change(section, rid, "добавлена",
                      _cut("; ".join([note] + words)),
                      _worst("minor", level))
    level, words = _record_changes(old, new)
    if level == "same":
        return None
    return Change(section, rid, "изменена", _cut("; ".join(words)), level)


# ---------------------------------------------------------- словари
def _dict_change(key: str, old, new):
    if _same(old, new):
        return None
    if old is None:
        return Change("dictionaries", key, "добавлена",
                      _cut(f"новый ключ словарей: {_size_words(new)}"),
                      "minor")
    if new is None:
        return Change("dictionaries", key, "удалена",
                      _cut(f"ключ словарей убран: {_size_words(old)}"),
                      "major")
    if isinstance(old, list) and isinstance(new, list):
        a, b = [_short(x) for x in old], [_short(x) for x in new]
        added, removed = sorted(set(b) - set(a)), sorted(set(a) - set(b))
        level = "same"
        words: list = []
        if added:
            level = _worst(level, "minor")
        if removed:
            level = _worst(level, "major")
        if added or removed:
            words.append(", ".join([f"+{x}" for x in added]
                                   + [f"−{x}" for x in removed]))
        elif a != b:
            level = _worst(level, "minor")
            words.append("перестановка перечня")
        return Change("dictionaries", key, "изменена", _cut("; ".join(words)),
                      level)
    if isinstance(old, dict) and isinstance(new, dict):
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        changed = sorted(k for k in set(old) & set(new)
                         if not _same(old[k], new[k]))
        level = "same"
        if added or changed:
            level = _worst(level, "minor")
        if removed:
            level = _worst(level, "major")
        words = ", ".join([f"+{k}" for k in added] + [f"−{k}" for k in removed]
                          + [f"~{k}" for k in changed])
        return Change("dictionaries", key, "изменена", _cut(words), level)
    # Смена самого вида значения (перечень стал объектом, текст —
    # перечнем) ломает всех, кто ключ читает: это старшая, а не правка
    # текста.
    вид_сменился = type(old) is not type(new)
    return Change("dictionaries", key, "изменена",
                  _cut(f"{_short(old)} → {_short(new)}"),
                  "major" if вид_сменился else "minor")


def _size_words(value) -> str:
    if isinstance(value, list):
        return f"перечень из {len(value)} значений"
    if isinstance(value, dict):
        return f"объект из {len(value)} подключей"
    return _short(value)


# ------------------------------------------------------------- тела
def diff_bodies(a: dict | None, b: dict) -> list:
    """Разница двух тел канона: `a is None` — всё «добавлена» (первый
    снимок). Порядок возврата детерминирован: разделы по SECTIONS, внутри
    — по id либо ключу словаря."""
    body_b = canon.normalize(b)
    body_a = canon.normalize(a) if a is not None else None
    out: list = []
    for section in ("families", "criteria"):
        new = {str(r.get("id")): r for r in body_b.get(section) or []}
        было = (body_a or {}).get(section) or []
        old = {str(r.get("id")): r for r in было} \
            if body_a is not None else {}
        for rid in sorted(set(old) | set(new)):
            ch = diff_record(section, old.get(rid), new.get(rid))
            if ch is not None:
                out.append(ch)
    new_d = body_b.get("dictionaries") or {}
    old_d = (body_a or {}).get("dictionaries") or {} if body_a is not None \
        else {}
    for key in sorted(set(old_d) | set(new_d)):
        ch = _dict_change(key, old_d.get(key) if key in old_d else None,
                          new_d.get(key) if key in new_d else None)
        if ch is not None:
            out.append(ch)
    return out


def diff_catalogs(cat_a, cat_b) -> list:
    """Разница двух каталогов читателя; `cat_a is None` — первый снимок."""
    return diff_bodies(cat_a.body() if cat_a is not None else None,
                       cat_b.body())


def suggest_level(changes) -> str:
    """Общий уровень набора правок: «major» | «minor» | «patch» | «same»."""
    return _worst(*[c.level for c in changes or []])


def next_version(current: str, level: str) -> str:
    """Следующий номер по уровню. На 0.x старшая и младшая правки дают
    0.(MINOR+1).0, патч — 0.MINOR.(PATCH+1) (соглашение semver для 0.x:
    совместимости до 1.0.0 никто не обещал); с 1.0.0 — обычный semver.
    Уровень «same» возвращает текущий номер как есть."""
    if level == "same":
        return current
    if level not in ("major", "minor", "patch"):
        raise ValueError(f"уровень {level!r} — не major, minor, patch "
                         f"или same")
    triple = _triple(current)
    major, minor, patch = triple
    if major == 0:
        if level in ("major", "minor"):
            return f"0.{minor + 1}.0"
        return f"0.{minor}.{patch + 1}"
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _triple(version: str) -> tuple:
    return parse_version(version)[0]


def as_journal(changes) -> list:
    """Перечень правок в форме журнала версий (четыре поля на запись)."""
    return [c.journal() for c in changes or []]
