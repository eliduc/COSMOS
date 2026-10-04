# -*- coding: utf-8 -*-
"""Обёртка шага 2 (vmp/template_registry): проекции каталога ≡ снимку
констант шага 1 (tests/fixtures/registry_step1.json) с прибавками шага 4,
формы и порядок, формула registry_rev, отказ без каталога, SPEC как
проекция, инварианты чисел.

Шаг 7, часть 7-Б (08.09.2026): стенды режима среза базы шаблонов сняты
вместе с самим режимом — вместо них стенд 5 (карта «вид и зона» по всем
семьям, перенесён из снятого tests/vmp/test_template_base_slice.py) и
стенд 6 (без каталога обёртка не отдаёт ничего + гейт против воскрешения
переменной среза в коде).

Ребро гейта «снимок ≡ проекции обёртки»; ребро «каталог ≡ снимок» — в
общем файле tests/test_template_catalog_parity.py; «каталог ≡ проекции»
— по построению (L10 + тесты потребителей).

Шаг 4 (08.09.2026). Снимок шага 1 НЕ переписывается (его
`frozen_until` называет три поля и повод их движения), поэтому
ожидаемое здесь ВЫЧИСЛЯЕТСЯ: снимок ⊕ дельта
`catalog/transition/step4_additions.json` (раздача имён семьям, шесть
критериев-метрик) ⊕ исполнители из ФАЙЛА привязок `vmp/bindings.json`,
прочитанного тестом напрямую — вторым путём, независимым от двери и
обёртки. Дельты нет — ожидаемое равно снимку, и гейт годен обоим
каталогам; расхождение называется словами, а не пропускается.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import catalog_copies as cc                                       # noqa: E402
import template_catalog as tc                                     # noqa: E402
from vmp import catalog_access as ca                              # noqa: E402
from vmp import criteria as crit                                  # noqa: E402
from vmp import registry_check as rc                              # noqa: E402
from vmp import template_registry as tr                           # noqa: E402

CATALOG = ROOT / "catalog"
FREEZER = ROOT / "tests" / "fixtures" / "freeze_registry_step1.py"
STEP1 = json.loads((ROOT / "tests" / "fixtures" / "registry_step1.json")
                   .read_text(encoding="utf-8"))
RMT_STEP1 = dict(STEP1["rule_mechanic_types"])
P2F_STEP1 = dict(STEP1["param_to_family"])
NSH_H0 = ROOT / "open-bench" / "polyaris-h0" / "etalon" / "NSH-H0.json"
VMP_H0 = ROOT / "open-bench" / "polyaris-h0" / "etalon" / "VMP-H0.json"
CODE_REV = "754c272613f075c3978a0d242b35e3a9185e4eb2"
#: sha256 файла снимка (CRLF→LF): снимок не переписывается (новая
#: заморозка — новый файл), поэтому его отпечаток закреплён здесь — подмена
#: снимка видна и в клоне без истории ревизии, где генератор отвечает
#: кодом 3 и ребро «снимок воспроизводим» пропускается.
SNAPSHOT_SHA256 = ("c968a13dd06a272da00c591ed4a873369ba3de0c97d75f8dbee4a36f"
                   "900850c7")
#: sha256 среза НШ-H0 после ПЕРЕВЫПУСКА эталонов (08.09, каталог-выпуск
#: 0.2.0): срез собирается генератором из каталога, и его отпечаток
#: закреплён здесь — подмена файла видна, а перевыпуск требует осознанной
#: правки числа.
NSH_H0_SHA256 = ("4fce9a633e491c352ed1fff503d5a22fc0e376dd1a16e240740cec8d48"
                 "a43ca7")   # перевыпуск 11.09.2026 под каталог 0.5.0
META5 = {"params", "kind", "zone", "class", "violation_unit"}
META8 = META5 | {"nature", "subject_words", "complements"}
#: Каталог 0.5.0: три поля записи семьи — чтение истории, размер нарушения,
#: свидетель природы (носители снятых долгов).
META11 = META8 | {"reads_history", "violation_size", "nature_confirmed_by"}

#: Дельта шага 4 — переходный носитель прибавок (версия каталога, новая
#: legacy-ревизия, раздача имён семьям, добавленные виды и критерии).
#: Файла нет — ожидаемое равно снимку шага 1 (§7.3 проекта: гейт годен
#: обоим каталогам).
DELTA_FILE = CATALOG / "transition" / "step4_additions.json"
DELTA = (json.loads(DELTA_FILE.read_text(encoding="utf-8"))
         if DELTA_FILE.is_file() else None)
#: Основание дельты — снимок шага 1 и каталог, которым он снят; без
#: дельты те же значения литералами (другого источника у них нет).
BASE = (DELTA or {}).get("base") or {
    "snapshot": "tests/fixtures/registry_step1.json",
    "code": CODE_REV,
    "version": "0.1.0-draft.3",
    "fingerprint": ("b357cd33a70638907766d5bd9fef8ede57fd3b02d7e0ff2540"
                    "12767637188471"),
    "legacy_registry_rev": "bc78f37804ec",
}
CATALOG_VERSION = (DELTA or {}).get("version") or BASE["version"]
LEGACY_REGISTRY_REV = ((DELTA or {}).get("legacy_registry_rev")
                       or BASE["legacy_registry_rev"])
#: Файл привязок продукта — второй, независимый от двери путь к строкам
#: исполнителей (шаг 4; до него исполнители жили в переходном носителе
#: transition/executors_legacy.json и стояли в снимке).
BINDINGS_FILE = ROOT / "vmp" / "bindings.json"
#: Класс шести критериев-метрик решателя (умолчание §14 п.10 схемы,
#: утверждённое проектом шага 4); независимый свидетель их состава —
#: TaskModes.kt в гейте паритета (L08).
METRIC_CRITERION_CLASS = "extension"


def _delta_params() -> dict:
    """Имена параметров, получившие семью шагом 4: {имя: семья}."""
    return {n: spec["family"] for n, spec
            in ((DELTA or {}).get("params_to_families") or {}).items()}


def _delta_families() -> dict:
    """Семьи, прибавленные к базе заморозки: {семья: курируемые поля}.

    Пакет паритета (решение 0.15): проекции обёртки сворачивают этот ключ
    дельты, а не держат перечень базовых семей литералом. У прибавленной
    семьи ожидание строится по КАТАЛОГУ (дельта её и называет), у базовых
    заморозка остаётся жёсткой — как сегодня."""
    return dict((DELTA or {}).get("families_added") or {})


def _expected_families() -> dict:
    """families() = снимок ⊕ раздача имён ⊕ прибавленные семьи."""
    out = {f: sorted(ps) for f, ps in STEP1["families"].items()}
    for fid in _delta_families():
        out.setdefault(fid, [])
    for name, fam in _delta_params().items():
        assert fam in out, f"дельта раздаёт {name} несуществующей семье {fam}"
        out[fam] = sorted(set(out[fam]) | {name})
    return out


def _expected_p2f() -> dict:
    """PARAM_TO_FAMILY = снимок ∪ раздача (L04 §7.3)."""
    out = dict(P2F_STEP1)
    out.update(_delta_params())
    return out


def _expected_criterion_types() -> dict:
    """CRITERION_TYPES = снимок ∪ критерии-метрики дельты."""
    out = {k: dict(v) for k, v in STEP1["criterion_types"].items()}
    for cid in (DELTA or {}).get("criteria_added") or ():
        out[cid] = {"class": METRIC_CRITERION_CLASS}
    return out


def _bindings_executors() -> dict | None:
    """Строки исполнителей ПРЯМО из файла привязок: «вид · символ ·
    файл» строк «есть» в порядке файла. Файла нет — None (до шага 4);
    после шага 4 его отсутствие обёртка называет отказом сама."""
    if not BINDINGS_FILE.is_file():
        return None
    doc = json.loads(BINDINGS_FILE.read_text(encoding="utf-8"))
    out: dict = {}
    for row in doc.get("bindings") or ():
        if row.get("section") != "families":
            continue
        out[str(row.get("id"))] = [
            "%s · %s · %s" % (ex["kind"], ex["symbol"], ex["file"])
            for ex in row.get("executors") or ()
            if ex.get("state") == "есть"]
    return out


def _expected_rmt() -> dict:
    """RULE_MECHANIC_TYPES = снимок (класс и заголовок) с исполнителями
    из файла привязок. Снимок объявил executors замороженными до шага 4
    (frozen_until.executors) — расхождение по этому полю ожидаемое, и
    ожидание строится независимым чтением файла, а не дверью."""
    execs = _bindings_executors()
    out = {f: dict(spec) for f, spec in RMT_STEP1.items()}
    for fid, spec in _delta_families().items():
        # Прибавленной семьи в снимке нет: её класс и заголовок называет
        # дельта, а исполнителей — файл привязок (снимок «раньше их не
        # было» доказать нечем — это делает правило L19 закона).
        out[fid] = {"class": spec["class"], "title": spec["title"],
                    "executors": (execs or {}).get(fid, [])}
    if execs is None:
        return out
    return {f: dict(spec, executors=execs.get(f, [])) for f, spec in out.items()}


@pytest.fixture(autouse=True)
def _door(monkeypatch):
    monkeypatch.delenv("CREW_TEMPLATE_CATALOG", raising=False)
    yield
    ca.forget()


@pytest.fixture(scope="module")
def cat():
    return tc.load(CATALOG)


# ------------------------------------------------------------- 1
def test_снимок_воспроизводим_из_ревизии():
    # Отпечаток файла снимка — без git: подмена видна и в неполном клоне.
    raw = (ROOT / "tests" / "fixtures" / "registry_step1.json").read_bytes()
    assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() \
        == SNAPSHOT_SHA256, "снимок шага 1 подменён: новая заморозка — " \
                            "новый файл registry_step<N>.json по слову владельца"
    env = dict(os.environ, PYTHONUTF8="1")
    res = subprocess.run([sys.executable, str(FREEZER), "--check"],
                         cwd=str(ROOT), capture_output=True, text=True,
                         encoding="utf-8", timeout=300, env=env)
    if res.returncode == 3:
        # Клон без истории: ребро «снимок воспроизводим» пропускается
        # с явным предупреждением генератора (имя ревизии и sha256
        # снимка); остальные два ребра гейта действуют.
        assert "ПРЕДУПРЕЖДЕНИЕ" in res.stdout and CODE_REV[:12] in res.stdout
        assert SNAPSHOT_SHA256 in res.stdout
        pytest.skip(f"ревизия {CODE_REV[:7]} недоступна в неполном клоне: "
                    f"{res.stdout.strip()}")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "расхождений 0" in res.stdout
    assert STEP1["code"] == BASE["code"] == CODE_REV
    # Снимок несёт ревизию ОСНОВАНИЯ (шаг 1); сегодняшняя ревизия
    # каталога после раздачи имён другая, и дельта называет обе.
    assert STEP1["registry_rev"] == BASE["legacy_registry_rev"]
    if DELTA is not None:
        assert LEGACY_REGISTRY_REV != BASE["legacy_registry_rev"], \
            "карта семей изменилась раздачей имён — свидетель обязан " \
            "сказать это новым значением"
    assert STEP1["source_files"] == ["vmp/template_registry.py",
                                     "vmp/criteria.py"]


# ------------------------------------------------------------- 2
def test_шесть_проекций_равны_снимку():
    """Проекции == ОЖИДАЕМОЕ = снимок шага 1 ⊕ дельта шага 4 (раздача
    имён семьям, шесть критериев-метрик) ⊕ исполнители файла привязок.
    Всё, чего дельта не называет, обязано совпасть со снимком поле в
    поле — перечень расхождений вычисляется от дельты, а не пишется
    рукой."""
    added = _delta_families()
    assert dict(tr.RULE_MECHANIC_TYPES) == _expected_rmt()
    assert tr.PARAM_TO_FAMILY == _expected_p2f()
    kind_zone = dict(STEP1["family_kind_zone"])
    violation = dict(STEP1["family_violation_unit"])
    for fid, spec in added.items():
        kind_zone[fid] = [spec["kind"], spec["zone"]]
        # Карта единиц нарушения — только у мягких семей (у жёсткой
        # единицы нет, и снимок шага 1 такой семьи не называет): первая
        # прибавленная ЖЁСТКАЯ семья (duty_series_limit, выпуск 0.4.0,
        # 09.09.2026) в карту не входит.
        if spec.get("violation_unit") is not None:
            violation[fid] = spec["violation_unit"]
    assert {f: list(v) for f, v in tr.FAMILY_KIND_ZONE.items()} == kind_zone
    assert tr.FAMILY_VIOLATION_UNIT == violation
    # Порядок VIOLATION_UNITS — словарь каталога, не кода шага 1
    # (отступление п. 21 приложения схемы, часть 13.6): состав равен
    # снимку, кортеж равен словарю каталога (тест 3).
    assert sorted(tr.VIOLATION_UNITS) == sorted(STEP1["violation_units"])
    assert tr.CRITERION_TYPES == _expected_criterion_types()
    fams = _expected_families()
    assert tr.families() == fams
    meta = tr.families_meta()
    assert set(meta) == set(STEP1["families_meta"]) | set(added)
    for f, m in meta.items():
        if f in added:
            # У прибавленной семьи заморозки нет: ожидание — дельта, а
            # свидетель её полей — правила L01/L02 и заявка (L21).
            assert (m["kind"], m["zone"], m["class"], m["violation_unit"]) == \
                (added[f]["kind"], added[f]["zone"], added[f]["class"],
                 added[f]["violation_unit"]), f
            assert m["params"] == fams[f], f
            continue
        assert {k: m[k] for k in META5} \
            == dict(STEP1["families_meta"][f], params=fams[f]), f
    # SPEC шага 4 равен снимку БЕЗ прибавок: словарь видов блока
    # `criterion` пакета отбирается по форме экземпляра, а у шести
    # критериев-метрик решателя её нет — метрика живёт в целевой.
    assert crit.SPEC == STEP1["criteria_spec"]
    assert set(crit.SPEC) <= set(tr.CRITERION_TYPES)


# ------------------------------------------------------------- 3
def test_формы_и_порядок(cat):
    fkz = tr.FAMILY_KIND_ZONE
    assert all(isinstance(v, tuple) and len(v) == 2 for v in fkz.values())
    units = tr.VIOLATION_UNITS
    assert isinstance(units, tuple)
    # кортеж — в порядке словаря каталога (видим CDPM: перечень единиц в
    # находке формы ВМП и cdpm/vmp_form.violation_units())
    assert units == tuple(cat.dictionaries()["violation_units"])
    rmt = tr.RULE_MECHANIC_TYPES
    for f, spec in rmt.items():
        assert set(spec) == {"class", "title", "executors"}, f
        assert isinstance(spec["executors"], list)
    ct = tr.CRITERION_TYPES
    assert len(ct) == len(_expected_criterion_types())
    if DELTA is not None:
        # Число выводится из дельты (два критерия шага 1 и метрики,
        # которые дельта назвала поимённо), а не пишется литералом.
        assert len(ct) == 2 + len(DELTA["criteria_added"]), \
            "два критерия шага 1 и метрики решателя из дельты"
    order = json.loads((CATALOG / "transition" / "families_order.json")
                       .read_text(encoding="utf-8"))
    assert list(rmt) == order
    fams = tr.families()
    assert list(tr.PARAM_TO_FAMILY) == [n for f in rmt for n in sorted(fams[f])]
    assert all(set(v) == {"class"} for v in tr.CRITERION_TYPES.values())
    assert set(tr.FAMILY_VIOLATION_UNIT) == {f for f, (k, _z) in fkz.items()
                                             if k == "SOFT"}
    fleet = " ".join(rmt["fleet_exclusions"]["executors"])
    assert "typeRatingRequired" in fleet and "typeRatingCurrency" in fleet
    # Два порядка в одной обёртке (п. 21 приложения, часть 13.6):
    # families()/families_meta() без среза — порядок читателя (по id),
    # константы и families_map — порядок families_order.json; состав один.
    assert list(fams) == list(cat.families()) == sorted(fams)
    assert list(tr.families_meta()) == list(fams)
    assert list(tr.families_map()["rule_families"]) == order
    assert sorted(rmt) == list(fams)
    assert set(tr._NAMES) <= set(dir(tr))
    from vmp.template_registry import PARAM_TO_FAMILY as p2f
    assert p2f == _expected_p2f()


# ------------------------------------------------------------- 4
def test_registry_rev_шаг3_отпечаток_каталога(cat):
    """Шаг 3 (§10 схемы, строка «3»): registry_rev обёртки — ПОЛНЫЙ
    отпечаток каталога, тот же, которым пакет объявляет каталог
    (manifest.catalog.fingerprint) и которым CDPM штампует посадки.
    Прежняя 12-значная формула больше нигде не считается — она осталась
    свидетелем неизменности данных: тот же канон семей даёт ровно
    legacy_registry_rev шапки. Литерала прежней ревизии в коде обёртки
    нет ни до, ни после переезда."""
    rev = tr.registry_rev()
    assert rev == cat.fingerprint() == cat.declared_fingerprint()
    assert len(rev) == 64
    canon = json.dumps(tr.families(), ensure_ascii=False, sort_keys=True)
    legacy = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]
    assert legacy == cat.header["legacy_registry_rev"] == LEGACY_REGISTRY_REV
    # Дельта шага 4 называет обе стороны переезда: каталог основания и
    # его legacy-ревизию — против сегодняшних. Дельты нет — каталог
    # обязан стоять на основании.
    if DELTA is None:
        assert cat.version() == BASE["version"]
        assert rev == BASE["fingerprint"]
        assert legacy == BASE["legacy_registry_rev"]
    else:
        assert cat.version() == CATALOG_VERSION != BASE["version"]
        assert rev != BASE["fingerprint"]
    src = (ROOT / "vmp" / "template_registry.py").read_text(encoding="utf-8")
    assert LEGACY_REGISTRY_REV not in src
    assert BASE["legacy_registry_rev"] not in src
    assert "hexdigest()[:12]" not in src


# ------------------------------------------------------------- 5
def test_карта_вид_зона_покрывает_все_семьи(cat):
    """Детерминизация формализации (владелец 2026-09-02): вид и зона —
    атрибуты СЕМЬИ шаблона, а не ярлык модели, и карта покрывает все
    семьи базы; единица размера нарушения — атрибут мягкой семьи.

    Ребро перенесено сюда из снятого стенда режима среза
    (tests/vmp/test_template_base_slice.py): сам режим ушёл частью 7-Б,
    а эта правда к нему не относилась и потеряться не должна."""
    assert set(tr.FAMILY_KIND_ZONE) == set(tr.RULE_MECHANIC_TYPES)
    meta = tr.families_meta()
    assert set(meta) == set(tr.RULE_MECHANIC_TYPES)
    kinds = set(cat.dictionaries()["kinds"])
    zones = set(cat.dictionaries()["zones"])
    for fam, spec in meta.items():
        assert spec["kind"] in kinds and spec["zone"] in zones, fam
        assert spec["params"] == tr.families()[fam]
        assert spec["class"] in ("core", "extension")
    # канон H0: предел смены — жёсткое пейринга, отдых — ростеринга,
    # продление и ночёвки — мягкие пейринга, разброс — мягкое ростеринга
    assert meta["duty_limits"]["kind"] == "HARD"
    assert meta["duty_limits"]["zone"] == "PAIRING"
    assert meta["rest_floors"]["zone"] == "ROSTERING"
    assert meta["duty_extension_penalty"]["kind"] == "SOFT"
    assert meta["balanced_workload"]["zone"] == "ROSTERING"
    soft = {f for f, sp in meta.items() if sp["kind"] == "SOFT"}
    assert set(tr.FAMILY_VIOLATION_UNIT) == soft
    for fam, spec in meta.items():
        if spec["kind"] == "SOFT":
            assert spec["violation_unit"] in tr.VIOLATION_UNITS, fam
        else:
            assert spec["violation_unit"] is None, fam
    assert meta["layover_penalty"]["violation_unit"] == "night"
    assert meta["duty_extension_penalty"]["violation_unit"] == "duty"
    assert meta["balanced_workload"]["violation_unit"] == "hour"
    assert set(meta["duty_limits"]) == META11


#: Переменная режима СРЕЗА базы шаблонов, снятого частью 7-Б шага 7.
#: Названа здесь один раз — чтобы гейт ниже искал именно её.
SLICE_ENV = "CREW" + "_VMP_TEMPLATE_BASE"
#: Файлы COSMOS, которые режим среза читали. Гейт против воскрешения:
#: в их КОДЕ имени переменной быть не должно. Докстроки и комментарии
#: гейт не судит — объяснение «что снято и чем заменено» обязано жить
#: рядом со снятым, иначе снятие перестаёт быть заменой.
NO_SLICE_FILES = ("vmp/template_registry.py", "vmp/catalog_access.py",
                  "webapp/template_base.py")
#: Имена помощников режима среза, снятых частью 7-Б (приложение 7-Б,
#: п. 1). Гейт судит их ПО ДЕРЕВУ РАЗБОРА — тем же законом, что и имя
#: переменной: сырой поиск по тексту красил бы гейт от упоминания
#: снятого в объяснении «что снято и чем заменено».
NO_SLICE_NAMES = ("_template_base_slice", "_slice_needs_catalog",
                  "_slice_error")


def _code_strings(path) -> set:
    """Строковые константы модуля ВНЕ докстрок (комментарии в дерево
    разбора не попадают вовсе): код воскрешает режим, текст — объясняет
    его снятие."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) \
                    and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docs.add(id(first.value))
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs}


def _code_names(path) -> set:
    """Имена, ЖИВУЩИЕ В КОДЕ модуля: объявления (функция, класс,
    довод), обращения по имени и по точке, псевдонимы импорта. Текст
    (докстроки, комментарии) в дерево разбора не попадает — и не
    судится: объяснение снятия обязано жить рядом со снятым."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.keyword) and n.arg:
            out.add(n.arg)
        elif isinstance(n, ast.alias):
            out.add(n.asname or n.name.split(".")[0])
    return out


# ------------------------------------------------------------- 6
def test_без_каталога_отказ_словами_и_режима_среза_нет(tmp_path, monkeypatch):
    """Шаг 7, часть 7-Б: режима СРЕЗА базы больше нет — второго
    источника рядом с каталогом у обёртки не осталось, и без каталога
    она не отдаёт НИЧЕГО: ни функции (families, registry_rev,
    families_meta), ни константы-проекции — только отказ словами.

    Прежде здесь стояли два стенда среза («срез первичен для функций и
    невидим константам», «полный срез не трогает каталог»); их правду —
    «запаса из кода у обёртки нет» — держит этот стенд, а правду «срез
    базы выводится ИЗ каталога» — стенд 9 этого файла (сборка НШ-H0
    генератором сверяется с эталоном поле в поле).

    Гейт против воскрешения судит ТОЛЬКО КОД: имя переменной — среди
    строковых констант вне докстрок, имена снятых помощников — среди
    имён дерева разбора. Текста гейт не судит намеренно (приложение
    7-Б, п. 9): объяснение «что снято и чем заменено» обязано жить
    рядом со снятым."""
    empty = tmp_path / "empty"
    empty.mkdir()
    cc.use_catalog(monkeypatch, empty)
    for name, call in (("families", tr.families),
                       ("registry_rev", tr.registry_rev),
                       ("families_meta", tr.families_meta),
                       ("RULE_MECHANIC_TYPES", lambda: tr.RULE_MECHANIC_TYPES),
                       ("PARAM_TO_FAMILY", lambda: tr.PARAM_TO_FAMILY),
                       ("VIOLATION_UNITS", lambda: tr.VIOLATION_UNITS)):
        with pytest.raises(tc.CatalogNotFound) as e:
            call()
        assert str(empty) in str(e.value), name
    # Ни имени переменной в коде, ни разбора среза в снятых местах.
    assert not hasattr(tr, "TEMPLATE_BASE_ENV")
    assert not hasattr(ca, "TEMPLATE_BASE_ENV")
    for rel in NO_SLICE_FILES:
        path = ROOT / rel
        assert SLICE_ENV not in _code_strings(path), \
            f"{rel}: режим среза воскрес в коде"
        names = _code_names(path)
        for gone in NO_SLICE_NAMES:
            assert gone not in names, \
                f"{rel}: помощник среза {gone} воскрес в коде"


# ------------------------------------------------------------- 7
def test_spec_один_объект_проекция_и_мутация(tmp_path, monkeypatch, cat):
    spec = crit.SPEC
    assert spec is crit.SPEC
    assert "semantics" not in spec["min_person_days"]
    rf = spec["rank_normalized_cost"]["semantics"]["rates_form"]
    assert rf["policy"] == "MUST_BE_EXTERNAL"
    assert rf["fields"] == ["flight", "work"]
    assert "text" not in spec["rank_normalized_cost"]["semantics"]
    # правка на месте видна судьям
    spec["_probe_kind"] = {"title": "проба", "needs": "исполнитель не написан"}
    try:
        gap = crit.apply_criterion({"kind": "_probe_kind"}, [], {})
        assert isinstance(gap, crit.CriterionGap)
        assert gap.needs == "исполнитель не написан"
        gaps = rc.deep_check([], {"kind": "_probe_kind"}, {"rules": []})
        assert [g["kind"] for g in gaps] == ["criterion_no_executor"]
    finally:
        del spec["_probe_kind"]
    assert "_probe_kind" not in crit.SPEC
    # третий критерий каталога с исполнителем-построителем, но без кода;
    # четвёртый — с построителем, но БЕЗ формы экземпляра (образец
    # критерия-метрики решателя шага 4: в SPEC ему места нет)
    other_kind = next(k for k in cat.dictionaries()["executor_kinds"]
                      if k != "builder_method")
    probe_form = {"fields": [{"name": "rate_per_duty",
                              "note": "целое больше нуля",
                              "required": True}],
                  "note": "форма экземпляра пробного критерия"}
    folder = cc.copy_catalog(tmp_path, criteria={
        "probe_declared": {"title": "заявленный вид", "needs": "исполнитель заявлен",
                           "computable_by": ["builder_method"],
                           "instance_form": probe_form},
        "probe_metric": {"title": "метрика без экземпляра", "needs": None,
                         "computable_by": ["builder_method"],
                         "instance_form": None},
        "probe_foreign": {"title": "чужой исполнитель", "needs": None,
                          "computable_by": [other_kind]}})
    cc.use_catalog(monkeypatch, folder)
    assert not [p for p in tc.load(folder).validate() if p.severity == "error"]
    spec2 = crit.SPEC
    assert spec2 is not spec and spec2 is crit.SPEC
    assert "probe_declared" in spec2 and "probe_foreign" not in spec2
    # Шаг 4: вид без формы экземпляра в блоке `criterion` пакета
    # невыразим — в SPEC он не входит, но записью каталога остаётся.
    assert "probe_metric" not in spec2
    assert "probe_metric" in tr.CRITERION_TYPES
    gaps = rc.deep_check([], {"kind": "probe_declared"}, {"rules": []})
    assert [g["kind"] for g in gaps] == ["criterion_no_executor"]
    assert gaps[0]["note"] == "исполнитель заявлен"
    with pytest.raises(crit.CriteriaError) as e:
        crit.apply_criterion({"kind": "probe_foreign"}, [], {})
    assert "вне перечня" in str(e.value)
    with pytest.raises(crit.CriteriaError):
        crit.apply_criterion({"kind": "probe_metric"}, [], {})


# ------------------------------------------------------------- 8
def test_families_map_и_lookup_формы(cat):
    """Формы карты и поиска. Шаг 4: заголовок критерия — из ЗАПИСИ
    каталога (в SPEC шести метрик решателя нет, и заголовок оттуда был
    бы пустым), признак исполнителя — «код COSMOS либо «есть» любого
    вида в привязках»: он истинен у всех записей критериев каталога, а
    `known_to_spec` у метрики ложен — два признака различны и это
    видно."""
    doc = tr.families_map()
    assert set(doc) == {"rule_families", "criteria"}
    for f, spec in doc["rule_families"].items():
        assert set(spec) == {"class", "title", "executors", "mapped_by"}, f
        assert set(spec["mapped_by"]) == {"family_field", "params"}
        assert spec["mapped_by"]["family_field"] is True
    assert doc["rule_families"]["duty_limits"]["mapped_by"]["params"] == \
        ["max_duty_duration", "max_landings_per_duty", "max_pairing_days"]
    records = cat.criteria()
    assert set(doc["criteria"]) == set(records) == set(tr.CRITERION_TYPES)
    for k, c in doc["criteria"].items():
        assert set(c) == {"class", "title", "has_executor"}, k
        assert c["has_executor"] is True, k
        assert c["title"] == records[k]["title"], k
    doc["rule_families"]["duty_limits"]["executors"].append("мусор")
    assert "мусор" not in tr.families_map()["rule_families"]["duty_limits"]["executors"]
    lk = tr.lookup("min_person_days")
    assert lk["section"] == "criterion" and lk["known_to_spec"] and lk["has_executor"]
    assert lk["class"] == "core"
    for cid in (DELTA or {}).get("criteria_added") or ():
        m = tr.lookup(cid)
        assert m["section"] == "criterion", cid
        assert m["has_executor"] is True, cid
        assert m["known_to_spec"] is False, cid
        assert m["class"] == METRIC_CRITERION_CLASS, cid
    lk2 = tr.lookup("duty_limits")
    assert lk2["section"] == "rule_mechanic" and lk2["executors"]
    assert tr.lookup("нет") is None


# ------------------------------------------------------------- 9
def test_инварианты_шага_2_числа(cat):
    assert cat.version() == CATALOG_VERSION
    assert cat.fingerprint() == cat.declared_fingerprint()
    assert tr.registry_rev() == cat.fingerprint()            # шаг 3
    spec = importlib.util.spec_from_file_location(
        "build_nsh_h0", ROOT / "open-bench" / "polyaris" / "build_nsh_h0.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    vmp = json.loads(VMP_H0.read_text(encoding="utf-8"))
    nsh = mod.build(vmp)
    payload = json.dumps(nsh, ensure_ascii=False, indent=1) + "\n"
    got = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    etalon_bytes = NSH_H0.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(etalon_bytes).hexdigest() == NSH_H0_SHA256
    # ЭТАЛОНЫ ПЕРЕВЫПУЩЕНЫ (слово владельца 08.09, §10 схемы строка «3»
    # и часть 7-Б): срез на диске собран сегодняшним каталогом-ВЫПУСКОМ
    # 0.2.0, поэтому сборка совпадает с ним БАЙТ В БАЙТ — прежнего
    # «расходится ровно двумя местами» больше нет. Срез закрепляет базу
    # версией и отпечатком каталога, а не одной ревизией.
    etalon_doc = json.loads(etalon_bytes.decode("utf-8"))
    assert got == NSH_H0_SHA256, \
        "срез, собранный сегодня, разошёлся с эталоном — перевыпустите " \
        "эталон: open-bench/polyaris/build_nsh_h0.py --kit ../polyaris-h0"
    assert nsh == etalon_doc
    assert nsh["registry_rev"] == cat.fingerprint()
    assert nsh["catalog_version"] == cat.version()
    assert nsh["catalog_fingerprint"] == cat.fingerprint()
    assert not cat.is_draft(), "срез закрепляет ВЫПУСК, а каталог — черновик"
    assert etalon_doc["registry_rev"] != BASE["legacy_registry_rev"], \
        "эталон остался на ревизии снимка шага 1 — перевыпуска не было"
    # Строки исполнителей среза — из файла привязок («вид · символ ·
    # файл»), а не из снимка шага 1: срез читает ту же проекцию, что и
    # обёртка.
    execs = _bindings_executors()
    for fam, spec in nsh["families"].items():
        if execs is not None:
            assert spec["executors"] == execs[fam], fam
    if execs is not None:
        assert any(spec["executors"] for spec in nsh["families"].values()), \
            "исполнители среза пусты — привязки не дошли до обёртки"
        assert any(spec["executors"] != RMT_STEP1[fam]["executors"]
                   for fam, spec in nsh["families"].items()), \
            "исполнители среза равны строкам снимка шага 1 — привязки не " \
            "дошли до среза"
    # Раздача 0.2.0-draft.2 видна в срезе: два имени словаря получили
    # семьи, и обе семьи есть в H0.
    assert "max_sit_duration" in nsh["families"]["connection_windows"]["params"]
    assert "fleet_type" in nsh["families"]["fleet_exclusions"]["params"]
    # Покрытие H0 после перевыпуска ЧИСТОЕ: посадки ВМП, срез и каталог
    # продукта стоят на одном отпечатке.
    assert mod.check_coverage(vmp, etalon_doc) == []
    assert mod.check_coverage(vmp, nsh) == []
    res = subprocess.run(["git", "ls-files", "catalog", "template_catalog"],
                         cwd=str(ROOT), capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr
    tracked = [ln for ln in res.stdout.splitlines() if ln.strip()]
    assert len(tracked) >= 20, tracked
    assert any(ln.endswith("catalog/catalog.json") for ln in tracked)
    assert any(ln.endswith("template_catalog/reader.py") for ln in tracked)
