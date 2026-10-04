# -*- coding: utf-8 -*-
"""Дверь COSMOS к каталогу шаблонов (vmp/catalog_access) и границы
перевода отказов (шаг 2, 07.09.2026).

Охраняемое: импорт без чтения файлов и среды; без читателя / без каталога
/ кривой каталог — отказ словами с путём и переменной на первой операции
(запаса из кода нет); один экземпляр с мостом CDPM; переходные носители
fail-closed; живой экземпляр при правке на диске без forget(); потоки;
n обращений — одно чтение; сертификат без cdpm.family_lexicon; границы
(гейт соответствия, матрица готовности, вход построителя, покрытие
критерия, экран базы шаблонов) переводят CatalogError словами; реестр
исполнителей несёт блок catalog и сторож дрейфа его видит.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import catalog_copies as cc                                       # noqa: E402
import template_catalog as tc                                     # noqa: E402
from _stand_identity import ADMIN_EMAIL                           # noqa: E402
from template_catalog import export as tcx                        # noqa: E402
from vmp import catalog_access as ca                              # noqa: E402
from vmp import criteria as crit                                  # noqa: E402
from vmp import formulation_check as fc                           # noqa: E402
from vmp import registry_check as rc                              # noqa: E402
from vmp import registry_derive as rd                             # noqa: E402
from vmp import template_registry as tr                           # noqa: E402
from vmp.run import roster_certify as rcert                       # noqa: E402

CATALOG = ROOT / "catalog"
PY = sys.executable
SUB_ENV = {"PYTHONUTF8": "1",
           "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
           "PATH": ""}

RULE_HARD = {"id": "R-1", "kind": "HARD", "zone": "PAIRING", "title": "предел",
             "params": [{"name": "max_duty_duration", "value": 720}]}
RULE_FIT = {"id": "R-2", "kind": "HARD", "zone": "PAIRING", "title": "предел",
            "template_fit": {"category": "A", "family": "duty_limits",
                             "params": {"max_duty_duration":
                                        {"value": 720, "quote": "720"}},
                             "quote": "720"}}


@pytest.fixture(autouse=True)
def _door(monkeypatch):
    monkeypatch.delenv("CREW_TEMPLATE_CATALOG", raising=False)
    yield
    ca.forget()


def _empty(tmp_path, monkeypatch) -> Path:
    empty = tmp_path / "empty"
    empty.mkdir(exist_ok=True)
    cc.use_catalog(monkeypatch, empty)
    return empty


def _registry_doc(root: Path, rules=None, catalog=None) -> dict:
    reg = {"created": "2026-09-07", "source": "тест", "scope": "тест",
           "code_fingerprints": rc.code_fingerprints(root),
           "rules": rules if rules is not None else [
               {"rule_id": "R-1", "verdict": "covered_full",
                "title": "предел", "evidence": "тест"}]}
    if catalog is not None:
        reg["catalog"] = catalog
    return reg


def _write(tmp_path: Path, name: str, doc) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


# ------------------------------------------------------------- 1
def test_импорт_двери_обёртки_и_criteria_не_читает_файлы_и_среду():
    script = r"""
import builtins, io, os, sys
class NoEnv(dict):
    def __getitem__(self, k): raise AssertionError("environ read: " + str(k))
    def get(self, k, d=None): raise AssertionError("environ get: " + str(k))
    def __contains__(self, k): raise AssertionError("environ in: " + str(k))
def deny(*a, **kw): raise AssertionError("open at import: %r" % (a[:1],))
builtins.open = deny
io.open = deny
os.environ = NoEnv()
sys.path.insert(0, sys.argv[1])
import vmp.catalog_access, vmp.template_registry, vmp.criteria
assert vmp.template_registry.catalog is vmp.catalog_access.current
print("ok")
"""
    res = subprocess.run([PY, "-c", script, str(ROOT)], capture_output=True,
                         text=True, encoding="utf-8", timeout=120, env=SUB_ENV)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "ok"


# ------------------------------------------------------------- 2
def test_без_читателя_отказ_словами():
    script = r"""
import sys
sys.path.insert(0, sys.argv[1])
sys.modules["template_catalog"] = None
import vmp.catalog_access as ca
import vmp.template_registry as tr
assert ca.tc is None and ca.CATALOG_ERRORS == (ca.CatalogUnavailable,)
for fn in (tr.families, lambda: tr.RULE_MECHANIC_TYPES):
    try:
        fn()
    except ca.CatalogUnavailable as e:
        print(type(e).__name__ + ": " + str(e))
    else:
        raise SystemExit("нет отказа")
"""
    res = subprocess.run([PY, "-c", script, str(ROOT)], capture_output=True,
                         text=True, encoding="utf-8", timeout=120, env=SUB_ENV)
    assert res.returncode == 0, res.stderr
    lines = res.stdout.strip().splitlines()
    assert len(lines) == 2
    for ln in lines:
        assert ln.startswith("CatalogUnavailable")
        assert "template_catalog" in ln and "установка" in ln


# ------------------------------------------------------------- 3
def test_нет_каталога_отказ_с_путём_и_переменной(tmp_path, monkeypatch):
    empty = _empty(tmp_path, monkeypatch)
    probes = [lambda: tr.RULE_MECHANIC_TYPES, tr.families, lambda: crit.SPEC,
              lambda: rc.fit_gap(RULE_FIT), lambda: rd.derive_rule(RULE_HARD)]
    for probe in probes:
        with pytest.raises(tc.CatalogNotFound) as e:
            probe()
        assert str(empty) in str(e.value)
        assert ca.CATALOG_ENV in str(e.value)
    assert hasattr(tr, "нет_такого") is False
    assert getattr(tr, "нет_такого", 1) == 1
    with pytest.raises(AttributeError):
        crit.нет_такого
    # Проекции обёртки — свойства с отказом: getattr с умолчанием и
    # hasattr от отказа каталога НЕ защищают (поднимается CatalogError,
    # не AttributeError) — читатель ловит catalog_access.CATALOG_ERRORS.
    with pytest.raises(tc.CatalogNotFound):
        hasattr(tr, "RULE_MECHANIC_TYPES")
    with pytest.raises(tc.CatalogNotFound):
        getattr(tr, "PARAM_TO_FAMILY", {})
    with pytest.raises(ca.CATALOG_ERRORS):
        getattr(crit, "SPEC", None)


# ------------------------------------------------------------- 4
def test_кривой_каталог_X03_при_первом_обращении_и_запомненный_отказ(
        tmp_path, monkeypatch):
    folder = cc.copy_catalog(tmp_path,
                             families={"duty_limits": {"violation_unit": "duty"}})
    cc.use_catalog(monkeypatch, folder)
    n = cc.count_validate(monkeypatch)
    with pytest.raises(tc.CatalogInvalid) as e:
        tr.FAMILY_VIOLATION_UNIT
    assert "X03" in {p.code for p in e.value.problems}
    assert ca.CATALOG_ENV in str(e.value)
    with pytest.raises(tc.CatalogInvalid) as e2:
        tr.families()
    assert e2.value is e.value
    with pytest.raises(tc.CatalogInvalid):
        crit.SPEC
    assert len(n) == 1


# ------------------------------------------------------------- 5
def test_правка_мимо_инструмента_H02(tmp_path, monkeypatch):
    folder = cc.copy_catalog(
        tmp_path, families={"duty_limits": {"title": "правка мимо инструмента"}},
        refresh=False)
    cc.use_catalog(monkeypatch, folder)
    seen = []
    for probe in (tr.families, lambda: rc.fit_gap(RULE_FIT),
                  lambda: fc.check_soft({}), lambda: crit.SPEC):
        with pytest.raises(tc.CatalogInvalid) as e:
            probe()
        seen.append(e.value)
    assert all(x is seen[0] for x in seen)
    assert "H02" in {p.code for p in seen[0].problems}


def test_имя_переменной_подмены_привязок_одно_у_двери_и_у_копий():
    # Шов дорожек: помощник копий (tests/catalog_copies.py) не импортирует
    # vmp — он служит и тестам читателя, — поэтому имя переменной подмены
    # стоит у него своим литералом. Разойдись эти два имени, use_catalog
    # ставил бы одну переменную, а дверь читала бы другую: копия каталога
    # молча судилась бы по ЖИВЫМ привязкам. Имя сводит в одно место этот
    # гейт, а не дублирующий импорт.
    assert cc.BINDINGS_ENV == ca.BINDINGS_ENV, (
        "имя переменной подмены привязок разошлось: помощник копий ставит "
        f"{cc.BINDINGS_ENV!r}, дверь читает {ca.BINDINGS_ENV!r}")


# ------------------------------------------------------------- 6
def test_порядок_семей_и_привязки_fail_closed(tmp_path, monkeypatch):
    # нет носителей и нет привязок: константы — отказ с путём файла
    # привязок и командой сборки; families/registry_rev живы
    a = cc.copy_catalog(tmp_path, transition=False, name="a")
    cc.use_catalog(monkeypatch, a)
    monkeypatch.setenv(ca.BINDINGS_ENV, str(tmp_path / "нет-привязок.json"))
    ca.forget()
    with pytest.raises(tc.CatalogNotFound) as e:
        ca.bindings()
    assert "нет-привязок.json" in str(e.value) and \
        "vmp.bindings_build" in str(e.value)
    with pytest.raises(tc.CatalogNotFound):
        tr.RULE_MECHANIC_TYPES
    with pytest.raises(tc.CatalogNotFound):
        rd.derive_rule(RULE_HARD)
    assert tr.families() == tc.load(a).families()
    # Шаг 3: registry_rev — отпечаток каталога (данные те же, что у
    # полной копии, — значит и отпечаток тот же).
    assert tr.registry_rev() == tc.load(a).fingerprint()
    # кривой порядок семей
    b = cc.copy_catalog(tmp_path, name="b")
    (b / "transition" / ca.ORDER_FILE).write_text('{"x": 1}', encoding="utf-8")
    cc.use_catalog(monkeypatch, b)
    with pytest.raises(tc.CatalogFormatError) as e:
        tr.RULE_MECHANIC_TYPES
    assert ca.ORDER_FILE in str(e.value)
    # лишний id в порядке — по имени
    c = cc.copy_catalog(tmp_path, name="c")
    op = c / "transition" / ca.ORDER_FILE
    order = json.loads(op.read_text(encoding="utf-8")) + ["ghost_family"]
    op.write_text(json.dumps(order), encoding="utf-8")
    cc.use_catalog(monkeypatch, c)
    with pytest.raises(tc.CatalogFormatError) as e:
        tr.PARAM_TO_FAMILY
    assert "ghost_family" in str(e.value)
    # семья без строк «есть» — законный пробел [], прочие семьи целы
    d = cc.copy_catalog(tmp_path, name="d")
    bp = d / cc.BINDINGS_FILE
    doc = json.loads(bp.read_text(encoding="utf-8"))
    for row in doc["bindings"]:
        if row["id"] == "duty_limits":
            for ex in row["executors"]:
                if ex["state"] == "есть":
                    ex.update({"state": "нет", "lang": None, "file": None,
                               "symbol": None,
                               "note": "пробел копии: исполнителя нет"})
    bp.write_bytes(tcx.dump_json(doc).encode("utf-8"))
    cc.use_catalog(monkeypatch, d)
    assert tr.RULE_MECHANIC_TYPES["duty_limits"]["executors"] == []
    assert tr.RULE_MECHANIC_TYPES["rest_floors"]["executors"]
    # привязки не по форме — отказ читателя с путём файла
    e_ = cc.copy_catalog(tmp_path, name="e")
    (e_ / cc.BINDINGS_FILE).write_text('{"product": "COSMOS"}',
                                       encoding="utf-8")
    cc.use_catalog(monkeypatch, e_)
    with pytest.raises(tc.CatalogFormatError) as e:
        tr.RULE_MECHANIC_TYPES
    assert cc.BINDINGS_FILE in str(e.value) and "bindings" in str(e.value)
    # привязка к записи, которой в каталоге нет — по имени (B02)
    f = cc.copy_catalog(tmp_path, name="f")
    bp = f / cc.BINDINGS_FILE
    doc = json.loads(bp.read_text(encoding="utf-8"))
    doc["bindings"].append({"section": "families", "id": "ghost_family",
                            "executors": [dict(doc["bindings"][0]
                                               ["executors"][0])],
                            "stand_verified": []})
    bp.write_bytes(tcx.dump_json(doc).encode("utf-8"))
    cc.use_catalog(monkeypatch, f)
    with pytest.raises(tc.CatalogFormatError) as e:
        tr.FAMILY_KIND_ZONE
    assert "ghost_family" in str(e.value) and cc.BINDINGS_FILE in str(e.value)
    # черновик с отставшим отпечатком — не отказ, а состояние stale-draft
    # (номер копии — черновой: у выпуска 0.2.0 живого каталога та же
    # разница данных при равной версии была бы отказом, а не состоянием)
    s = cc.copy_catalog(tmp_path, name="s", version="0.2.1-draft.1")
    bp = s / cc.BINDINGS_FILE
    doc = json.loads(bp.read_text(encoding="utf-8"))
    doc["catalog_fingerprint"] = "0" * 64
    bp.write_bytes(tcx.dump_json(doc).encode("utf-8"))
    cc.use_catalog(monkeypatch, s)
    assert tr.RULE_MECHANIC_TYPES["rest_floors"]["executors"]
    assert ca.bindings_state() == ca.BIND_STALE
    assert ca.bindings_summary()["state"] == ca.BIND_STALE
    # повтор id в порядке семей (множество равно, длина — нет)
    g = cc.copy_catalog(tmp_path, name="g")
    op = g / "transition" / ca.ORDER_FILE
    order = json.loads(op.read_text(encoding="utf-8"))
    op.write_text(json.dumps(order + [order[0]]), encoding="utf-8")
    cc.use_catalog(monkeypatch, g)
    with pytest.raises(tc.CatalogFormatError) as e:
        tr.VIOLATION_UNITS
    assert "повторы" in str(e.value) and order[0] in str(e.value)
    assert ca.ORDER_FILE in str(e.value)
    # кривой JSON шапки — отказ читателя с адресом двери впереди (_rewrap:
    # тот же класс, переменная и путь папки в тексте)
    h = cc.copy_catalog(tmp_path, name="h")
    (h / "catalog.json").write_text('{"version": "0.1.0-draft.3",',
                                    encoding="utf-8")
    cc.use_catalog(monkeypatch, h)
    with pytest.raises(tc.CatalogFormatError) as e:
        tr.families()
    assert ca.CATALOG_ENV in str(e.value) and str(h) in str(e.value)
    assert "кривой JSON" in str(e.value)
    with pytest.raises(tc.CatalogFormatError):
        tr.RULE_MECHANIC_TYPES


# ------------------------------------------------------------- 7
def test_единый_файл_выгрузки_отказ_до_validate(tmp_path, monkeypatch):
    single = tmp_path / "catalog-single.json"
    single.write_text(tcx.export_single(tc.load(CATALOG)), encoding="utf-8")
    cc.use_catalog(monkeypatch, single)
    n = cc.count_validate(monkeypatch)
    with pytest.raises(tc.CatalogFormatError) as e:
        tr.families()
    assert "папку каталога" in str(e.value) and "мост CDPM" in str(e.value)
    assert n == []


# ------------------------------------------------------------- 8
def test_один_экземпляр_с_мостом_cdpm():
    from cdpm import catalog_bridge as cb
    ca.forget()
    cb.forget()
    a = ca.current()
    assert a is tc.current(None)
    assert a is cb.catalog()
    assert a is ca.current()
    assert "strict=" not in (ROOT / "vmp" / "catalog_access.py") \
        .read_text(encoding="utf-8")


# ------------------------------------------------------------- 9
def test_живой_экземпляр_при_правке_на_диске(tmp_path, monkeypatch):
    folder = cc.copy_catalog(tmp_path)
    cc.use_catalog(monkeypatch, folder)
    a = tr.RULE_MECHANIC_TYPES
    assert a is tr.RULE_MECHANIC_TYPES
    s = crit.SPEC
    head = folder / "catalog.json"
    doc = json.loads(head.read_text(encoding="utf-8"))
    doc["note"] = "правка шапки на диске без перезапуска процесса"
    head.write_bytes(tcx.dump_json(doc).encode("utf-8"))
    cc.bump_mtime(head)
    b = tr.RULE_MECHANIC_TYPES
    assert b is not a and b == a
    assert crit.SPEC is not s and crit.SPEC == s
    bp = folder / cc.BINDINGS_FILE
    doc = json.loads(bp.read_text(encoding="utf-8"))
    for row in doc["bindings"]:
        if row["id"] == "duty_limits":
            row["executors"] = [ex for ex in row["executors"]
                                if ex["state"] != "есть"] + [
                {"kind": "builder_method", "state": "есть", "lang": "python",
                 "file": "_pairing_builder.py",
                 "symbol": "Builder.new_duty_limit_executor", "params": [],
                 "note": None, "cr_key": None}]
    bp.write_bytes(tcx.dump_json(doc).encode("utf-8"))
    cc.bump_mtime(bp)
    c = tr.RULE_MECHANIC_TYPES
    assert c is not b
    line = ("builder_method · Builder.new_duty_limit_executor · "
            "_pairing_builder.py")
    assert c["duty_limits"]["executors"] == [line], (
        "правка привязок на диске обязана дать новые строки проекции без "
        "перезапуска процесса")
    assert line in rd.derive_rule(RULE_HARD)["evidence"]


# ------------------------------------------------- 9б (шаг 4: файл CDPM)
def test_отметка_видит_и_привязки_cdpm(tmp_path, monkeypatch):
    # Состояния клеток для экрана дверь считает по ОБОИМ файлам привязок
    # (bindings_states), поэтому файл CDPM обязан входить в отметку кэша
    # проекций: иначе его выпуск или правка оставила бы проекцию обёртки
    # прежней МОЛЧА. Живой cdpm/bindings.json не заводится — он чужой:
    # подменяется только путь константы.
    folder = cc.copy_catalog(tmp_path)
    cc.use_catalog(monkeypatch, folder)
    other = tmp_path / "cdpm-bindings.json"
    monkeypatch.setattr(ca, "CDPM_BINDINGS_FILE", other)
    cat = ca.current()
    a = ca.transition_stamp(cat)
    assert len(a) == 3 and a[2][1] is None, \
        "нет файла CDPM — третьей отметкой обязана быть пустая"
    other.write_bytes(tcx.dump_json(
        {"product": "CDPM", "bindings": []}).encode("utf-8"))
    b = ca.transition_stamp(cat)
    assert b != a and b[:2] == a[:2], (
        "появление cdpm/bindings.json обязано менять отметку кэша "
        "проекций и не трогать отметки своих файлов")


def test_нечитаемые_привязки_cdpm_не_роняют_проекции(tmp_path, monkeypatch):
    """Чужой файл не останавливает COSMOS.

    Привязки CDPM дверь читает только ради слов сводки и показа чужих
    клеток. Кривой, полузаписанный или не по форме файл в чужой зоне не
    смеет ронять RULE_MECHANIC_TYPES и все нажатия: документ снимается с
    показа, сводка говорит третье слово «не читаются» и называет
    причину. Слово «объявлены» — только о ПРОЧИТАННОМ и прошедшем
    читателя документе, а не о существовании файла."""
    folder = cc.copy_catalog(tmp_path)
    cc.use_catalog(monkeypatch, folder)
    other = tmp_path / "cdpm-bindings.json"
    monkeypatch.setattr(ca, "CDPM_BINDINGS_FILE", other)
    ca.forget()
    assert ca.bindings_summary()["cdpm"] == ca.CDPM_NO

    for damage, mark in ((b"{\"product\": \"CDPM\", ", "кривой JSON"),
                         (b"[]", "не объект")):
        other.write_bytes(damage)
        ca.forget()
        # Проекции живы: чужая правка не роняет свои строки.
        assert tr.RULE_MECHANIC_TYPES["duty_limits"]["executors"], mark
        assert ca.bindings_states(), mark
        sm = ca.bindings_summary()
        assert sm["cdpm"] == ca.CDPM_BAD, (mark, sm["cdpm"])
        assert sm["cdpm_note"], mark
        assert ca.cdpm_bindings() is None, mark

    # Запись не из каталога — читатель B02, слово то же, причина своя.
    other.write_bytes(tcx.dump_json(
        {"product": "CDPM", "product_version": "1.18",
         "catalog_version": ca.current().version(),
         "catalog_fingerprint": ca.current().fingerprint(),
         "generated_at": "2026-09-08T12:00:00+03:00",
         "generated_from": "0" * 40,
         "bindings": [{"section": "families", "id": "нет-такой-семьи",
                       "executors": [{"kind": "check_template",
                                      "state": "нет", "lang": None,
                                      "file": None, "symbol": None,
                                      "template": "выходных-в-месяце",
                                      "note": "проба", "cr_key": None}],
                       "stand_verified": []}]}).encode("utf-8"))
    ca.forget()
    assert tr.RULE_MECHANIC_TYPES["duty_limits"]["executors"]
    sm = ca.bindings_summary()
    assert sm["cdpm"] == ca.CDPM_BAD and "нет-такой-семьи" in sm["cdpm_note"]

    # Чужой документ читается ПО ОТМЕТКЕ, а не на каждой операции:
    # дверь открывают при каждом нажатии, и чтение чужого файла каждый
    # раз было бы платой ни за что.
    import template_catalog.bindings as tcb
    reads = []
    real = tcb.load_bindings
    monkeypatch.setattr(tcb, "load_bindings",
                        lambda p: (reads.append(str(p)), real(p))[1])
    ca.forget()
    cat = ca.current()
    for _ in range(4):
        ca.cdpm_bindings_state(cat)
    assert [r for r in reads if r == str(other)] == [str(other)], reads
    other.write_bytes(other.read_bytes() + b"\n")
    ca.cdpm_bindings_state(cat)
    assert len([r for r in reads if r == str(other)]) == 2, \
        "правка чужого файла на диске обязана дать новое чтение"


# ------------------------------------------------------------- 10
def test_потоки_один_объект(monkeypatch):
    ca.forget()
    n = cc.count_validate(monkeypatch)
    barrier = threading.Barrier(8)
    ids_rmt: list = []
    ids_spec: list = []
    errors: list = []

    def worker():
        try:
            barrier.wait(timeout=30)
            ids_rmt.append(id(tr.RULE_MECHANIC_TYPES))
            ids_spec.append(id(crit.SPEC))
        except Exception as e:                            # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert len(ids_rmt) == 8 and len(set(ids_rmt)) == 1
    assert len(ids_spec) == 8 and len(set(ids_spec)) == 1
    assert len(n) == 1


# ------------------------------------------------------------- 11
def test_n_обращений_одно_чтение(monkeypatch):
    ca.forget()
    calls = cc.count_reads(monkeypatch)
    # Привязки читатель открывает своим load_bindings (он держит ссылку
    # на read_json_file с импорта, и общий счётчик его не видит) —
    # поэтому у файла привязок счётчик свой.
    from template_catalog import bindings as tcb
    binds: list = []
    orig = tcb.load_bindings

    def counting(path):
        binds.append(str(path))
        return orig(path)
    monkeypatch.setattr(tcb, "load_bindings", counting)
    tr.RULE_MECHANIC_TYPES
    names = [Path(p).name for p in calls]
    for fn in cc.SECTION_FILES + cc.TRANSITION_FILES:
        assert names.count(fn) <= 1, (fn, names)
    assert set(cc.TRANSITION_FILES) <= set(names)
    # Чужие привязки CDPM (cdpm/bindings.json) дверь тоже читает один раз,
    # если файл есть (выпущен CDPM 08.09 по каталогу 0.2.0-draft.1) —
    # ожидание не привязано к его наличию.
    expected = [str(ca.bindings_path())]
    if ca.CDPM_BINDINGS_FILE.is_file():
        expected.append(str(ca.CDPM_BINDINGS_FILE))
    assert sorted(binds) == sorted(expected), (
        f"каждый файл привязок обязан читаться дверью ровно один раз: {binds}")
    calls.clear()
    binds.clear()
    for _ in range(20):
        tr.families_meta()
        tr.families()
        tr.registry_rev()
        tr.RULE_MECHANIC_TYPES
        crit.SPEC
    assert calls == [] and binds == []


# ------------------------------------------------------------- 12
def _cap_rule(rid, fam, params, kind="HARD"):
    return {"id": rid, "kind": kind, "zone": "PAIRING", "title": rid,
            "statement": "s", "sources": [{"sid": "S1", "anchor": rid}],
            "template_fit": {"category": "A", "family": fam,
                             "registry_rev": "x" * 12,
                             "params": {k: {"value": v, "quote": str(v)}
                                        for k, v in params.items()}}}


def _cap_spec(rid, max_hours):
    return {"id": f"C-{rid}-0", "basis": {"rule": rid},
            "template": "максимальная-длительность-часов",
            "params": {"sheet": "SOL/Смены", "start_column": "Начало",
                       "end_column": "Конец", "max_hours": max_hours},
            "statement": "смена ≤ предела", "unit": "смена",
            "status": "скомпилирована"}


def _cap_dataset():
    rows = [{"Табельный номер": "E1", "База приписки": "AAA",
             "Начало": "2026-01-15T06:00", "Конец": "2026-01-15T18:40"}]
    return {"tables": {rcert.SOLUTION_SID: {"Смены": {
        "columns": ["Табельный номер", "База приписки", "Начало", "Конец"],
        "rows": rows}}}}


def test_complements_и_сертификат_без_cdpm_family_lexicon(tmp_path, monkeypatch):
    assert ca.complements()["duty_limits"] == ("duty_extension_penalty",)
    assert rcert._complementary_families("duty_limits", "duty_extension_penalty")
    assert rcert._complementary_families("duty_extension_penalty", "duty_limits")
    assert not rcert._complementary_families("duty_limits", "duty_limits")
    assert not rcert._complementary_families("duty_limits", "rest_floors")
    assert not rcert._complementary_families("", "duty_limits")
    tree = ast.parse((ROOT / "vmp" / "run" / "roster_certify.py")
                     .read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod != "cdpm.family_lexicon"
            if mod == "cdpm":
                assert "family_lexicon" not in [a.name for a in node.names]
        if isinstance(node, ast.Import):
            assert "cdpm.family_lexicon" not in [a.name for a in node.names]
    monkeypatch.setitem(sys.modules, "cdpm.family_lexicon", None)
    svod = {"rules": [
        _cap_rule("R-LIM", "duty_limits", {"max_duty_duration": 720}),
        _cap_rule("R-EXT", "duty_extension_penalty",
                  {"long_duty_threshold": 720, "duty_extension_cap": 780},
                  kind="SOFT")]}
    kept, notes, checks, findings = rcert.apply_extension_caps(
        svod, [_cap_spec("R-LIM", 12.0)], _cap_dataset())
    assert kept == [] and len(checks) == 1 and checks[0]["status"] == "прогнана"
    assert findings == []
    # потолок без каталога — отказ словами; без потолка каталог не трогается
    _empty(tmp_path, monkeypatch)
    with pytest.raises(tc.CatalogNotFound):
        rcert.apply_extension_caps(svod, [_cap_spec("R-LIM", 12.0)],
                                   _cap_dataset())
    calls = cc.count_reads(monkeypatch)
    svod2 = {"rules": [_cap_rule("R-LIM", "duty_limits",
                                 {"max_duty_duration": 720})]}
    specs = [_cap_spec("R-LIM", 12.0)]
    assert rcert.apply_extension_caps(svod2, specs, _cap_dataset()) \
        == (specs, [], [], [])
    assert calls == []


# ------------------------------------------------------------- 13
@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient
    from webapp import raw_zone, registry, users
    from webapp.app import app
    cfg_dir = tmp_path / "webapp-dev-auth"
    cfg_dir.mkdir()
    mp = pytest.MonkeyPatch()
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    mp.delenv("CREW_ACCESS_AUD", raising=False)
    mp.setattr(users, "USERS_DB_PATH", cfg_dir / "users.db")
    mp.setattr(registry, "OWNERSHIP_PATH", cfg_dir / "ownership.json")
    (cfg_dir / "ownership.json").write_text("{}", encoding="utf-8")
    mp.setattr(raw_zone, "RAW_DIR", cfg_dir / "raw")
    try:
        with TestClient(app) as c:
            yield c
    finally:
        mp.undo()


def test_границы_переводят_отказ_словами(tmp_path, monkeypatch, client):
    from cdpm import export_task_package as tp
    from vmp import pairing_input as pi
    from vmp import readiness_verify as rv
    from tests.test_cdpm_task_package import _ready_doc
    empty = _empty(tmp_path, monkeypatch)
    # гейт соответствия: реестр под каталогом, каталога нет
    reg_path = _write(tmp_path, "registry.json", _registry_doc(
        ROOT, catalog={"version": "0.1.0-draft.3", "fingerprint": "0" * 64,
                       "path": "x"}))
    model = {"rules": [RULE_HARD]}
    with pytest.raises(rc.ConformanceError) as e:
        rc.conformance_gate(model, None, ROOT, reg_path)
    assert e.value.gaps[0]["kind"] == "catalog_unavailable"
    assert "каталог шаблонов недоступен" in str(e.value)
    assert str(empty) in e.value.gaps[0]["note"]
    # реестр без блока, правило с посадкой — тот же перевод (fit_gap)
    reg2 = _write(tmp_path, "registry2.json", _registry_doc(
        ROOT, rules=[{"rule_id": "R-2", "verdict": "covered_full",
                      "title": "предел", "evidence": "тест"}]))
    with pytest.raises(rc.ConformanceError) as e2:
        rc.conformance_gate({"rules": [RULE_FIT]}, None, ROOT, reg2)
    assert e2.value.gaps[0]["kind"] == "catalog_unavailable"
    # матрица готовности: показание в колонке реестра, не исключение
    doc = _ready_doc()
    clean = {"customer": "Т", "tables": {"D1": {
        "Рейсы": {"columns": ["id"], "rows": [{"id": "F1"}]},
        "Экипажи": {"columns": ["emp"], "rows": [{"emp": "E1"}]},
        "Отпуска": {"columns": ["emp", "day"], "rows": []}}}}
    man = {"spec_version": tp.SPEC_VERSION,
           "readiness_matrix": tp.readiness_ladder(doc, clean)}
    ver = rv.verify(man, doc, clean, ROOT, reg_path)
    cell = ver["columns"]["pairing"]
    assert "каталог" in (cell["registry"]["error"] or "")
    assert cell["launch_allowed"] is False
    assert any("реестр исполнителей" in w for w in cell["why_blocked"])
    # вход построителя: отказ словами, не трассировка
    applied = {"min_sit_home_min": 45, "min_sit_outstation_min": 60,
               "max_duty_span_min": 720, "min_rest_home_min": 720,
               "min_rest_outstation_min": 600}
    payload = {"employees": [{"id": "E1", "base": "SVG", "skills": ["CP"]}],
               "flights": [{"departureUTCDateTime": "2026-09-01T08:00:00Z",
                            "arrivalUTCDateTime": "2026-09-01T10:00:00Z"}]}
    with pytest.raises(pi.PairingInputError) as e3:
        pi.builder_args(applied, payload, budget_s=60,
                        criterion={"kind": "min_person_days",
                                   "rate_per_duty": 100, "source_rule": "O-X"})
    assert "каталог" in str(e3.value) and str(empty) in str(e3.value)
    # покрытие критерия — «невычислима с причиной»
    cov = crit.coverage({"kind": "min_person_days"})
    assert cov["computable"] is False and "каталог" in cov["reason"]
    # экран базы шаблонов — 503 словами
    r = client.get("/api/template-base")
    assert r.status_code == 503, r.text
    assert str(empty) in r.json()["detail"]
    # Ручка прежней заявки на правку/снятие каталога БОЛЬШЕ НЕ ЧИТАЕТ
    # (шаг 6, решение 0.8): правка и снятие живут на экране службы, а
    # заявка каталога у существующей записи бывает только на вид
    # исполнителя. Поэтому отказ у неё один и тот же при любом
    # состоянии каталога — 409 словами, а не 503 «каталог недоступен»:
    # недоступность каталога к этому отказу отношения не имеет.
    r = client.post("/api/template-base/change",
                    json={"family": "duty_limits", "action": "edit",
                          "reason": "проба"})
    assert r.status_code == 409, r.text
    assert "службы каталога" in r.json()["detail"]["error"]


# ------------------------------------------------------------- 14
def test_реестр_несёт_блок_catalog_и_сторож_видит_смену(tmp_path):
    reg = rd.derive_registry({"rules": [RULE_HARD]}, ROOT)
    assert set(reg["catalog"]) == {"version", "fingerprint", "path",
                                   "manifest_check", "manifest_check_against"}
    # Версия и отпечаток — каталога, не литералы теста: их назначает
    # инструмент каталога, и повторять их здесь значило бы держать одно
    # число в двух местах.
    assert reg["catalog"]["version"] == tc.load(CATALOG).version()
    assert reg["catalog"]["fingerprint"] == tc.load(CATALOG).fingerprint()
    # Шаг 3: реестр, выведенный НЕ при нажатии (приём пакета, канал
    # задач), пакета с каталогом не сверял — это видно, а не выдумано;
    # реестр нажатия несёт исход сторожа словом из закрытого перечня.
    assert reg["catalog"]["manifest_check"] is None
    assert reg["catalog"]["manifest_check_against"] is None
    against = {"version": "0.1.0-draft.9", "fingerprint": "0" * 64,
               "source": "catalog"}
    pressed = rd.derive_registry({"rules": [RULE_HARD]}, ROOT,
                                 manifest_check=ca.CHECK_MATCH,
                                 manifest_check_against=against)
    assert pressed["catalog"]["manifest_check"] == "match"
    # Мерка исхода — рядом с исходом, и реестр переносит её ДОСЛОВНО:
    # запись прогона обязана называть ту самую сторону, по которой
    # вынесен исход, — иначе «сошлось» не проверить. Сам блок catalog
    # при этом называет каталог сегодняшнего дерева, и расхождение
    # между ними видно, а не сглажено.
    assert pressed["catalog"]["manifest_check_against"] == against
    assert pressed["catalog"]["fingerprint"] != against["fingerprint"]
    assert rc.drift_check(pressed, ROOT) == []
    assert rc.drift_check(reg, ROOT) == []
    stale = dict(reg, catalog=dict(reg["catalog"], fingerprint="0" * 64))
    assert rc.drift_check(stale, ROOT) == ["catalog"]
    without = {k: v for k, v in reg.items() if k != "catalog"}
    assert rc.drift_check(without, ROOT) == []
    p = _write(tmp_path, "stale.json", stale)
    with pytest.raises(rc.ConformanceError) as e:
        rc.conformance_gate({"rules": [RULE_HARD]}, None, ROOT, p)
    assert "код, каталог шаблонов или привязки изменены" in str(e.value)
    assert [(g["kind"], g["rule_id"]) for g in e.value.gaps] == [("drift", "catalog")]


# --------------------------------------------------- 14б (шаг 4: привязки)
def test_реестр_несёт_блок_bindings_и_сторож_видит_правку_привязок(
        tmp_path, monkeypatch):
    from vmp import bindings_build as bb
    reg = rd.derive_registry({"rules": [RULE_HARD]}, ROOT)
    assert set(reg["catalog"]) == {"version", "fingerprint", "path",
                                   "manifest_check", "manifest_check_against"}
    block = reg["bindings"]
    doc = ca.bindings()
    assert block["fingerprint"] == bb.fingerprint(doc)
    assert block["state"] == ca.BIND_MATCH
    assert block["catalog_fingerprint"] == tc.load(CATALOG).fingerprint()
    assert block["product_version"] == "1.18.5"
    assert rc.drift_check(reg, ROOT) == []
    # отставший отпечаток привязок — сторож это видит
    stale = dict(reg, bindings=dict(block, fingerprint="0" * 64))
    assert rc.drift_check(stale, ROOT) == ["bindings"]
    # реестр без блока привязок (до шага 4) по ним не судится
    without = {k: v for k, v in reg.items() if k != "bindings"}
    assert rc.drift_check(without, ROOT) == []
    # перегенерация с другим моментом и коммитом отпечатка не меняет:
    # канон изменчивых полей не несёт
    other = bb.build(ca.current(), commit="f" * 40,
                     generated_at="2026-09-09T23:59:59+03:00",
                     stand_results=bb.default_stand_results())
    fresh = tmp_path / "bindings.json"
    fresh.write_bytes(bb.dump(other).encode("utf-8"))
    monkeypatch.setenv(ca.BINDINGS_ENV, str(fresh))
    ca.forget()
    assert ca.bindings()["generated_from"] == "f" * 40
    assert rc.drift_check(reg, ROOT) == []


# ------------------------------------------------------------- 15
def test_ast_скан_from_импортов():
    tree = ast.parse((ROOT / "vmp" / "registry_derive.py")
                     .read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert node.module != "vmp.template_registry", \
                "from-импорт уровня модуля заморозил бы первый снимок"
    assert set(tr._NAMES) & set(vars(tr)) == set()
    assert "SPEC" not in vars(crit)
