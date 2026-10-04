# -*- coding: utf-8 -*-
"""Заготовка волны 17б: направление критерия в каталоге 0.6.0 (поле
записи `direction`, словарь `directions`, правило валидатора X20) и
закон версий 0.5.0 → 0.6.0.

ID аудита: A06 (направление критерия — из каталога, а не из документа).

Контракт (oracle):
* `P0-CONTRACTS-v1.2-20260911.md` §3.3: «каталог 0.6.0 … добавляет: (а)
  поле записи критерия `criteria[].direction ∈ {minimize, maximize}` по
  словарю `directions` (8 записей каталога → `minimize`…) и правило
  валидатора X20 (обязательно у статуса «есть»)… Добавление словаря —
  младшая правка каталога ⇒ версия 0.6.0»;
* `P0-COSMOS-SIDE-v1-20260911.md` раздел «0-бис» (действующая редакция,
  старше разделов 1–8), строка «Направление критерия (В-4)»: «поле
  `criteria[].direction ∈ {minimize, maximize}` у каждой записи критерия
  (8 записей → minimize) + словарь `directions` + правило X20 (у записи
  со статусом «есть» направление обязательно и по словарю)»; карта
  `criterion_directions` из §3.3 и приложения В — ЗАМЕНЕНА полем записи;
* `P0-COSMOS-SIDE-v1-20260911.md` §3.4 (закон версий: на 0.x разница
  среднего числа — стоп сторожа; выпуск 0.6.0 перевязывает пять наборов)
  и §3.2 (все восемь сегодняшних видов — `minimize`).

Ожидаемое поведение:
1. у каждой записи `criteria[]` со статусом «есть» есть `direction` из
   словаря `directions`; у восьми записей 0.5.0 — `minimize`;
2. словарь `directions` = закрытый перечень {maximize, minimize};
3. X20: запись критерия «есть» без направления либо с направлением вне
   словаря — ОШИБКА валидатора с кодом X20, адресованная записи;
4. закон версий: добавление словаря и необязательного поля — младшая
   правка; на 0.x младшая даёт 0.(MINOR+1).0, значит после 0.5.0 —
   0.6.0, и каталог такой формы годен (validate без ошибок).

Заготовка КРАСНАЯ на d5af1f21: живой каталог — 0.5.0 без поля и без
словаря; схема записи критерия закрыта (`unevaluatedProperties: false`),
схема словарей закрыта (`additionalProperties: false`), правила X20 в
`template_catalog/validate.py` нет.

K9: исторические цитаты выше сохранены. Синтетическая новая форма теперь
0.7.0: выпущенная владельцем 0.6.0 остаётся legacy. Имена модуля/фикстур
сохранены для импортёров; ниже закон minor проверяется как 0.6.0 → 0.7.0.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import catalog_copies as cc                                       # noqa: E402
import template_catalog as tc                                     # noqa: E402
from template_catalog import diff                                 # noqa: E402
from template_catalog.canon import fingerprint_of                 # noqa: E402
from test_catalog_060_versioned_schema import modern_docs          # noqa: E402

CATALOG = ROOT / "catalog"
FILES = ("catalog.json", "families.json", "criteria.json",
         "dictionaries.json", "requests.json", "versions.json")
#: Словарь направлений — закрытый перечень (контракт §3.3, приложение В).
DIRECTIONS = ["maximize", "minimize"]
#: Синтетическая legacy-сторона minor-перехода, не правка истории выпуска.
VERSION_BEFORE = "0.6.0"
#: Новая синтетическая форма с направлением, не акт выпуска.
VERSION_AFTER = "0.7.0"


def _catalog_060(tmp_path: Path, mutate=None, *, name: str = "cat060") -> Path:
    """Копия каталога в форме 0.7.0 во временной папке: у записей
    критериев — `direction`, в словарях — `directions`.

    Поле и словарь ставятся `setdefault`: когда новая форма появится,
    копия равна живому каталогу, и тест продолжает судить правило, а не
    затирать данные. Журнал версий копии пуст (копия — черновик, как в
    tests/test_template_catalog_validate.py), отпечаток шапки
    пересчитывается — иначе любая правка доказывала бы H02."""
    dst = Path(tmp_path) / name
    if dst.exists():
        cc.rmtree_writable(dst)
    shutil.copytree(CATALOG, dst, ignore=shutil.ignore_patterns(
        "tools", "schema", "candidates", "__pycache__", "releases"))
    docs = {n: json.loads((dst / n).read_text(encoding="utf-8")) for n in FILES}
    docs["versions.json"] = {"form_version": 1, "versions": []}
    if docs["catalog.json"]["version"] in {"0.5.0", VERSION_BEFORE}:
        # Direction validation belongs to the 0.7 schema. Supply its complete
        # shape (including unit_words), not new fields labelled as legacy.
        # This temporary vocabulary proves schema plumbing, not a release.
        modern_docs(docs, VERSION_AFTER)
    for rec in docs["criteria.json"].get("criteria") or []:
        rec.setdefault("direction", "minimize")
    docs["dictionaries.json"].setdefault("directions", list(DIRECTIONS))
    if mutate is not None:
        mutate(docs)
    body = _body(docs)
    try:
        docs["catalog.json"]["fingerprint"] = fingerprint_of(body)
    except ValueError:                                   # pragma: no cover
        pass
    for name_, doc in docs.items():
        (dst / name_).write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return dst


def _body(docs: dict) -> dict:
    """Тело канона из документов копии (три раздела §9)."""
    return {"families": docs["families.json"].get("families") or [],
            "criteria": docs["criteria.json"].get("criteria") or [],
            "dictionaries": {k: v for k, v
                             in docs["dictionaries.json"].items()
                             if k != "form_version"}}


def _without_060(body: dict) -> dict:
    """Тело каталога БЕЗ новой формы — левая сторона закона версий."""
    out = copy.deepcopy(body)
    for rec in out.get("criteria") or []:
        rec.pop("direction", None)
    for key in ("directions", "criterion_directions", "unit_words"):
        out.get("dictionaries", {}).pop(key, None)
    return out


def _errors(cat, code: str | None = None) -> list:
    problems = [p for p in cat.validate() if p.severity == "error"]
    return [p for p in problems if code is None or p.code == code]


def test_every_criterion_record_declares_a_direction():
    """A06, контракт §3.3 и «0-бис»: направление — поле ЗАПИСИ каталога;
    у восьми видов 0.5.0 оно `minimize`."""
    cat = tc.load(CATALOG)
    dicts = cat.dictionaries()
    records = cat.criteria(status=("есть",))
    assert records, "в каталоге нет записей критериев со статусом «есть»"
    беда = {rid: rec.get("direction") for rid, rec in sorted(records.items())
            if rec.get("direction") not in set(dicts.get("directions") or
                                               DIRECTIONS)}
    assert not беда, (
        f"у записей критериев нет направления по словарю: {беда} "
        f"(контракт §3.3: поле criteria[].direction обязательно)")
    восемь = {"credit_hours_spread", "layover_nights_total",
              "min_person_days", "positioning_cost",
              "preferred_days_off_violations", "rank_normalized_cost",
              "total_pairing_cost", "uncovered_seats"}
    for rid in sorted(восемь & set(records)):
        assert records[rid]["direction"] == "minimize", (
            f"{rid}: контракт §3.2 называет направление minimize, "
            f"в записи {records[rid].get('direction')!r}")


def test_directions_dictionary_is_the_closed_list_of_two_words():
    """A06, контракт §3.3: словарь `directions` — закрытый перечень из
    двух слов; он же основание правила X20."""
    dicts = tc.load(CATALOG).dictionaries()
    assert "directions" in dicts, (
        "в словарях каталога нет ключа directions (контракт §3.3: "
        "новый словарь выпуска 0.6.0)")
    assert sorted(dicts["directions"]) == DIRECTIONS, dicts["directions"]
    from template_catalog import validate as v
    assert "directions" in v.TOKEN_KEYS, (
        "ключ directions не объявлен перечнем-словарём читателя "
        "(TOKEN_KEYS) — членство значений судиться не будет")


def test_x20_refuses_a_criterion_record_without_a_direction(tmp_path):
    """A06/X20, контракт §3.3 и сторона COSMOS §3.3: у записи критерия
    статуса «есть» направление обязательно; положительный контроль —
    нетронутая синтетическая копия 0.7.0 кода X20 не даёт."""
    целая = tc.load(_catalog_060(tmp_path, name="целая"))
    assert not _errors(целая, "X20"), (
        "правило X20 сработало на годной копии 0.7.0: "
        f"{[str(p) for p in _errors(целая, 'X20')]}")

    тронутая: dict = {}

    def убрать(docs):
        rec = docs["criteria.json"]["criteria"][0]
        rec.pop("direction", None)
        тронутая["id"] = str(rec.get("id"))

    порченая = tc.load(_catalog_060(tmp_path, убрать, name="без-направления"))
    rid = тронутая["id"]
    плохие = _errors(порченая, "X20")
    assert плохие, (
        "запись критерия «есть» без направления прошла валидатор: "
        "кода X20 в перечне проблем нет")
    assert any(rid in p.where for p in плохие), (
        f"проблема X20 не адресована записи {rid}: "
        f"{[p.where for p in плохие]}")


def test_x20_refuses_a_direction_outside_the_dictionary(tmp_path):
    """A06/X20, контракт §3.3: значение направления — только из словаря
    `directions`; слово вне словаря — ошибка, а не предупреждение."""

    def подменить(docs):
        docs["criteria.json"]["criteria"][0]["direction"] = "вниз"

    cat = tc.load(_catalog_060(tmp_path, подменить, name="чужое-слово"))
    плохие = _errors(cat, "X20")
    assert плохие, (
        "направление «вниз» вне словаря directions прошло валидатор "
        "(ожидался код X20)")


def test_law_of_versions_makes_060_a_minor_bump_and_the_shape_is_valid(
        tmp_path):
    """Добавление словаря остаётся minor; на синтетической legacy 0.6.0
    закон номеров даёт новую форму 0.7.0 без изменения старого выпуска."""
    cat = tc.load(_catalog_060(tmp_path, name="закон-версий"))
    ошибки = _errors(cat)
    assert not ошибки, (
        "каталог формы 0.7.0 (поле direction + словарь directions) не "
        f"признан годным: {[str(p) for p in ошибки][:8]}")
    было, стало = _without_060(cat.body()), cat.body()
    изменения = diff.diff_bodies(было, стало)
    assert изменения, "диффер не увидел ни поля, ни словаря новой формы 0.7.0"
    уровень = diff.suggest_level(изменения)
    assert уровень == "minor", (
        f"уровень правки {уровень!r}, контракт §3.3 называет младшую")
    assert diff.next_version(VERSION_BEFORE, уровень) == VERSION_AFTER, (
        f"закон номеров на 0.x: после {VERSION_BEFORE} младшая правка "
        f"обязана дать {VERSION_AFTER}")
