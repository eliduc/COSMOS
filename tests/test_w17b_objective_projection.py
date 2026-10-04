# -*- coding: utf-8 -*-
"""Заготовка волны 17б: проекция `objective.json` одной функцией.

ID аудита: A08 (objective.json не сверяется с моделью пакета), A06
(направление — из каталога).

Контракт (oracle):
* `P0-CONTRACTS-v1.2-20260911.md` §3.4: «общий модуль
  `vmp/objective_projection.py` (зона COSMOS… модуль не импортирует
  `cdpm`, только `template_catalog`): `project(model_doc, catalog) ->
  dict` строит содержимое `objective.json` из документа модели
  ДЕТЕРМИНИРОВАННО (компоненты: `rule_id` существует и активен, `weight`
  = модели; целевые: `id`, `direction`; критерий: `kind`, `direction`,
  ставки, версия; источники существуют); `compare(objective_json,
  model_doc, catalog) -> list[problem]` сверяет предъявленный файл с
  проекцией»; §5 — код отказа выпуска `objective-mismatch`;
* `P0-COSMOS-SIDE-v1-20260911.md` раздел «0-бис», строка «Проекция
  целевой (В-7)» (действующая редакция подписей: `project(model_doc,
  catalog)`, `compare(objective_json, model_doc, catalog)`;
  `canonical_bytes`/`ObjectiveProjectionError` — дополнения) и §6.2–6.3
  (выходная форма, правило сравнения, отказ словами с перечнем
  компонентов);
* `P0-COSMOS-SIDE-v1-20260911.md` приложение Е — фрагмент выхода
  `project` (ключи `<задача>.direction`, `objectives[]`, `components[]`,
  `criterion`) и слова отказа сравнения;
* `P0-CONTRACTS-v1.2-20260911.md` §3.3 — направление берётся из записи
  каталога (`criteria[].direction`), а не из константы «minimize».

Фикстуры синтетические: документ модели собран по формам спецификации
пакета (meta/rules/objectives/questions), каталог — копия каталога
дерева во временной папке, которой дописано поле `direction` записей
критериев (форма 0.6.0). Сети и живых служб нет.

Заготовка КРАСНАЯ на d5af1f21: файла `vmp/objective_projection.py` нет.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import catalog_copies as cc                                       # noqa: E402
import template_catalog as tc                                     # noqa: E402
from template_catalog.canon import fingerprint_of                 # noqa: E402

CATALOG = ROOT / "catalog"
MODULE = ROOT / "vmp" / "objective_projection.py"
FILES = ("catalog.json", "families.json", "criteria.json",
         "dictionaries.json", "requests.json", "versions.json")
#: Ревизия спецификации пакета, вводящая проекцию (контракт §11.2 п. 1).
SPEC_VERSION = "1.20"
#: Вид критерия и метрика зоны пейринга — записи каталога 0.5.0.
KIND = "min_person_days"
METRIC = "total_pairing_cost"
#: Семья мягкого правила зоны пейринга (`catalog/families.json`).
FAMILY = "layover_penalty"


def _projection():
    """Модуль проекции. Импорт внутри теста: до реализации его нет, и
    сбор набора ломаться не должен — краснеет каждый тест сам."""
    from vmp import objective_projection                  # noqa: PLC0415
    return objective_projection


def _catalog(tmp_path: Path, *, directions: dict | None = None,
             name: str = "cat060"):
    """Каталог формы 0.6.0 во временной папке: записям критериев
    дописано `direction` (умолчание `minimize`, контракт §3.2), словарям
    — перечень `directions`."""
    dst = Path(tmp_path) / name
    if dst.exists():
        cc.rmtree_writable(dst)
    shutil.copytree(CATALOG, dst, ignore=shutil.ignore_patterns(
        "tools", "schema", "candidates", "__pycache__", "releases"))
    docs = {n: json.loads((dst / n).read_text(encoding="utf-8")) for n in FILES}
    docs["versions.json"] = {"form_version": 1, "versions": []}
    for rec in docs["criteria.json"].get("criteria") or []:
        rec["direction"] = (directions or {}).get(str(rec.get("id")),
                                                  "minimize")
    docs["dictionaries.json"].setdefault("directions",
                                         ["maximize", "minimize"])
    body = {"families": docs["families.json"].get("families") or [],
            "criteria": docs["criteria.json"].get("criteria") or [],
            "dictionaries": {k: v for k, v in docs["dictionaries.json"].items()
                             if k != "form_version"}}
    try:
        docs["catalog.json"]["fingerprint"] = fingerprint_of(body)
    except ValueError:                                   # pragma: no cover
        pass
    for имя, doc in docs.items():
        (dst / имя).write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    tc.forget_current()
    return tc.load(dst)


def _model(cat, *, weight: int = 10, direction: str = "minimize",
           rate: int = 100) -> dict:
    """Синтетический документ модели (канон ВМП пакета): одно мягкое
    правило зоны пейринга, одна целевая с машинным критерием, один
    источник."""
    contract = {"family_id": FAMILY, "catalog_version": cat.version(),
                "catalog_fingerprint": cat.fingerprint(),
                "record_fingerprint": None, "param_set": None}
    источник = {"sid": "S1", "anchor": "стр-1-абз-2"}
    return {
        "meta": {"customer": "Проба-В17б", "spec_version": SPEC_VERSION,
                 "catalog": {"version": cat.version(),
                             "fingerprint": cat.fingerprint()}},
        "sources": [{"sid": "S1", "title": "Пробный документ",
                     "kind": "документ"}],
        "rules": [
            {"id": "VN-030", "kind": "SOFT", "zone": "PAIRING",
             "family": FAMILY, "weight": weight,
             "statement": "ночёвка вне базы наказывается",
             "sources": [источник],
             "template_fit": {"category": "A", "schema_version": "landing/2",
                              "family": FAMILY, "family_contract": contract,
                              "params": {}}},
        ],
        "objectives": [
            {"id": "O-PAIR", "zone": "PAIRING", "direction": direction,
             "metric": METRIC, "title": "Стоимость пейрингов",
             "statement": "минимизировать стоимость пейрингов",
             "sources": [источник],
             "criterion": {"kind": KIND, "rate_per_duty": rate,
                           "per": "календарный день командировки",
                           "version": "M-036/demo-1",
                           "effective_from": "2026-08-01",
                           "as_written": "машинный критерий MIN_PERSON_DAYS, "
                                         "ставка 100"}},
        ],
        "questions": [],
    }


def _zone(projection: dict) -> dict:
    assert isinstance(projection, dict), f"проекция — объект: {projection!r}"
    assert "pairing" in projection, (
        f"в проекции нет зоны pairing (приложение Е): {sorted(projection)}")
    return projection["pairing"]


def _words(problems) -> str:
    """Слова расхождений одной строкой: форма проблемы контрактом не
    закреплена (слова либо объект), поэтому судится текст."""
    assert isinstance(problems, list), f"compare возвращает список: {problems!r}"
    return " | ".join(str(p) for p in problems)


def test_projection_is_deterministic_in_bytes(tmp_path):
    """A08, контракт §3.4 («строит… детерминированно») и §6.2: два
    вызова дают равные байты — иначе подпись манифеста плавает."""
    op = _projection()
    cat = _catalog(tmp_path)
    model = _model(cat)
    первый, второй = op.project(model, cat), op.project(model, cat)
    assert первый == второй, "две проекции одного документа разошлись"
    assert op.canonical_bytes(первый) == op.canonical_bytes(второй), (
        "канонические байты проекции недетерминированы")


def test_projection_carries_the_components_and_the_criterion_of_the_model(
        tmp_path):
    """A08, контракт §3.4 и приложение Е: компоненты — правила модели с
    их весами, критерий — вид, ставка и направление."""
    op = _projection()
    cat = _catalog(tmp_path)
    зона = _zone(op.project(_model(cat), cat))
    компоненты = {str(c.get("rule_id")): c for c in зона.get("components") or []}
    assert "VN-030" in компоненты, (
        f"компонента правила VN-030 в проекции нет: {sorted(компоненты)}")
    assert компоненты["VN-030"].get("weight") == 10, компоненты["VN-030"]
    assert компоненты["VN-030"].get("family") == FAMILY, компоненты["VN-030"]
    цели = {str(o.get("id")): o for o in зона.get("objectives") or []}
    assert "O-PAIR" in цели, f"целевой O-PAIR в проекции нет: {sorted(цели)}"
    assert цели["O-PAIR"].get("metric") == METRIC
    критерий = зона.get("criterion")
    assert isinstance(критерий, dict), f"критерий зоны: {критерий!r}"
    assert критерий.get("kind") == KIND
    assert критерий.get("rate_per_duty") == 100
    assert критерий.get("direction") == "minimize", (
        "направление критерия в проекции берётся из записи каталога "
        "(контракт §3.3)")


def test_direction_comes_from_the_catalog_and_is_not_a_constant(tmp_path):
    """A06/A08, контракт §3.3 и §6.2: направление зоны и целевых — из
    записи каталога, а не строка «minimize» в коде."""
    op = _projection()
    вниз = _catalog(tmp_path, name="вниз")
    assert _zone(op.project(_model(вниз), вниз)).get("direction") == "minimize"

    вверх = _catalog(tmp_path, name="вверх",
                     directions={KIND: "maximize", METRIC: "maximize"})
    зона = _zone(op.project(_model(вверх, direction="maximize"), вверх))
    assert зона.get("direction") == "maximize", (
        "направление зоны не последовало за каталогом: проекция держит "
        "константу вместо поля criteria[].direction")


def test_projection_refuses_when_the_document_argues_with_the_catalog(
        tmp_path):
    """A06/A08, контракт §6.2: «обязано совпасть с objectives[].direction
    документа — иначе `ObjectiveProjectionError`»."""
    op = _projection()
    cat = _catalog(tmp_path)
    with pytest.raises(ValueError) as поймано:
        op.project(_model(cat, direction="maximize"), cat)
    слова = str(поймано.value)
    assert "maximize" in слова and "minimize" in слова, слова


def test_compare_is_silent_on_the_projection_itself(tmp_path):
    """A08, контракт §6.3 (положительный контроль): производитель пишет
    `objective.json = canonical_bytes(project(...))`, и сверка молчит."""
    op = _projection()
    cat = _catalog(tmp_path)
    model = _model(cat)
    assert op.compare(op.project(model, cat), model, cat) == [], (
        "сверка нашла расхождение у собственной проекции")


def test_compare_names_a_substituted_component_weight(tmp_path):
    """A08, тест SC-3 аудита (контракт §3.7): подмена веса компонента в
    `objective.json` — расхождение словами с именем правила."""
    op = _projection()
    cat = _catalog(tmp_path)
    model = _model(cat)
    подделка = op.project(model, cat)
    _zone(подделка)["components"][0]["weight"] = 999
    беды = op.compare(подделка, model, cat)
    слова = _words(беды)
    assert беды, "подмена веса компонента сверкой не замечена"
    assert "VN-030" in слова and "999" in слова, слова


def test_compare_names_a_substituted_criterion(tmp_path):
    """A08, тест SC-3: подмена машинного критерия (ставка и вид) —
    расхождение словами."""
    op = _projection()
    cat = _catalog(tmp_path)
    model = _model(cat)
    подделка = op.project(model, cat)
    _zone(подделка)["criterion"]["rate_per_duty"] = 999
    беды = op.compare(подделка, model, cat)
    assert беды, "подмена ставки критерия сверкой не замечена"
    assert "999" in _words(беды), _words(беды)


def test_compare_names_a_substituted_direction(tmp_path):
    """A06/A08, контракт §6.3 (слова отказа: «pairing.direction maximize
    ≠ minimize (каталог…)»)."""
    op = _projection()
    cat = _catalog(tmp_path)
    model = _model(cat)
    подделка = op.project(model, cat)
    _zone(подделка)["direction"] = "maximize"
    беды = op.compare(подделка, model, cat)
    assert беды, "подмена направления зоны сверкой не замечена"
    assert "maximize" in _words(беды), _words(беды)


def test_compare_refuses_an_empty_objective_json(tmp_path):
    """A08, контракт §6.3: «пустой `{}` — расхождение (сегодня проходит
    гейт и останавливается позже)»."""
    op = _projection()
    cat = _catalog(tmp_path)
    model = _model(cat)
    assert op.compare({}, model, cat), "пустой objective.json признан равным"


def test_module_reads_only_the_catalog_and_never_imports_cdpm():
    """A08, контракт §3.4 и §6.2: модуль общий, зависимости — стандартная
    библиотека и `template_catalog`; импорта `cdpm` в нём нет."""
    assert MODULE.is_file(), (
        f"модуля проекции нет: {MODULE} (контракт §3.4 — общий модуль "
        f"зоны COSMOS)")
    текст = MODULE.read_text(encoding="utf-8")
    for строка in ("import cdpm", "from cdpm"):
        assert строка not in текст, (
            f"{MODULE} импортирует cdpm ({строка!r}) — контракт §6.2 это "
            f"запрещает")
    assert "template_catalog" in текст, (
        f"{MODULE} не читает каталог: направление и единицы обязаны "
        f"браться у template_catalog")
