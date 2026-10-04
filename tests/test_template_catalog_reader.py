# -*- coding: utf-8 -*-
"""Читатель каталога шаблонов: load по пути/шапке/единому файлу/версии/None,
аксессоры, фильтр статусов, ошибки словами, детерминизм канона и
отпечатка, независимость пакета (import-граф, импорт без чтения файлов и
среды), сторож версий, структурный гейт привязок."""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import template_catalog as tc                                     # noqa: E402
import catalog_copies as cc                                       # noqa: E402
from template_catalog import bindings as tb                       # noqa: E402
from template_catalog import canon, export, manifest              # noqa: E402

CATALOG = ROOT / "catalog"
PY = sys.executable
#: Пакет паритета (08.09.2026, решение 0.15): счётчики этого файла —
#: ПРОИЗВОДНЫЕ от базы заморозки ⊕ вычисленной дельты, а не литералы
#: живого каталога. Иначе «паритет зелёный без правки чисел человеком»
#: было бы верно только для файла паритета: соседи держали бы числа.
STEP1 = json.loads((ROOT / "tests" / "fixtures" / "registry_step1.json")
                   .read_text(encoding="utf-8"))
DELTA = json.loads((CATALOG / "transition" / "step4_additions.json")
                   .read_text(encoding="utf-8"))
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.\-]+)?$")


@pytest.fixture(scope="module")
def cat():
    return tc.load(CATALOG)


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "cat"
    shutil.copytree(CATALOG, dst, ignore=shutil.ignore_patterns("tools", "schema", "__pycache__"))
    return dst


# ------------------------------------------------------------ load
def test_load_variants_give_same_catalog(cat):
    fp = cat.fingerprint()
    assert tc.load(None).fingerprint() == fp                   # DEFAULT_CATALOG_DIR
    assert tc.load(str(CATALOG)).fingerprint() == fp           # папка строкой
    assert tc.load(CATALOG / "catalog.json").fingerprint() == fp
    assert tc.load(cat.version()).fingerprint() == fp          # строка semver
    assert tc.DEFAULT_CATALOG_DIR == CATALOG
    assert tc.SECTION_FILES[0] == "catalog.json"
    assert tc.FINGERPRINT_SECTIONS == ("families", "criteria", "dictionaries")


def test_load_unknown_version_and_path_in_words(tmp_path):
    with pytest.raises(tc.CatalogNotFound) as ei:
        tc.load("9.9.9")
    assert "9.9.9" in str(ei.value) and "releases" in str(ei.value)
    with pytest.raises(tc.CatalogNotFound) as ei:
        tc.load(tmp_path / "нет-такой-папки")
    assert "нет-такой-папки" in str(ei.value)


def test_load_missing_section_and_broken_json(tmp_path):
    dst = _copy(tmp_path)
    (dst / "criteria.json").unlink()
    with pytest.raises(tc.CatalogNotFound) as ei:
        tc.load(dst)
    assert "criteria" in str(ei.value)
    dst2 = _copy(tmp_path / "b")
    (dst2 / "families.json").write_text('{"form_version": 1, "families": [}',
                                        encoding="utf-8")
    with pytest.raises(tc.CatalogFormatError) as ei:
        tc.load(dst2)
    assert "строка" in str(ei.value) and "позиция" in str(ei.value)
    assert "families.json" in str(ei.value)


def test_duplicate_key_is_format_error(tmp_path):
    dst = _copy(tmp_path)
    text = (dst / "catalog.json").read_text(encoding="utf-8")
    text = text.replace('"name": "template-catalog",',
                        '"name": "template-catalog",\n "name": "x",', 1)
    (dst / "catalog.json").write_text(text, encoding="utf-8")
    with pytest.raises(tc.CatalogFormatError) as ei:
        tc.load(dst)
    assert "дубль ключа" in str(ei.value) and "'name'" in str(ei.value)


def test_header_without_version_and_bom_tolerated(tmp_path):
    dst = _copy(tmp_path)
    head = json.loads((dst / "catalog.json").read_text(encoding="utf-8"))
    (dst / "catalog.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(head, ensure_ascii=False).encode("utf-8"))
    assert tc.load(dst).version() == head["version"]           # BOM терпится
    del head["version"]
    (dst / "catalog.json").write_text(json.dumps(head, ensure_ascii=False),
                                      encoding="utf-8")
    with pytest.raises(tc.CatalogFormatError) as ei:
        tc.load(dst)
    assert "version" in str(ei.value)


def test_strict_load_raises_catalog_invalid(tmp_path):
    dst = _copy(tmp_path)
    head = json.loads((dst / "catalog.json").read_text(encoding="utf-8"))
    head["fingerprint"] = "0" * 64
    (dst / "catalog.json").write_text(json.dumps(head, ensure_ascii=False),
                                      encoding="utf-8")
    assert tc.load(dst).declared_fingerprint() == "0" * 64        # не strict — читается
    with pytest.raises(tc.CatalogInvalid) as ei:
        tc.load(dst, strict=True)
    assert any(p.code == "H02" for p in ei.value.problems)
    assert "отпечаток" in str(ei.value)


# -------------------------------------------------------- аксессоры
def test_accessors_shapes(cat):
    fams = cat.families()
    assert list(fams) == sorted(fams)
    assert len(fams) == len(STEP1["families"]) + len(DELTA["families_added"])
    meta = cat.families_meta()
    assert set(meta["layover_penalty"]) == {"params", "kind", "zone", "class",
                                            "violation_unit", "violation_size",
                                            "nature", "nature_confirmed_by",
                                            "reads_history",
                                            "subject_words", "complements"}
    # 0.5.0: три признака записи отдаются как есть, чтения истории —
    # копией (правка у читателя оригинала не трогает).
    ночная = meta["night_series"]
    assert ночная["nature"] == "history" and ночная["reads_history"], (
        "семья истории обязана нести чтение истории")
    assert set(ночная["reads_history"]) == {"kind", "need"}
    assert ночная["nature_confirmed_by"] == "solver_tables"
    assert (meta["layover_penalty"]["violation_size"]
            in cat.dictionaries()["violation_sizes"])
    ночная["reads_history"]["kind"] = "подменено"
    assert (cat.families_meta()["night_series"]["reads_history"]["kind"]
            != "подменено")
    rec = cat.family("reserve_floors")
    assert rec["params"][0]["qualifier"] == "base_code"
    rec["params"].clear()                                     # копия, не оригинал
    assert cat.family("reserve_floors")["params"]
    params = cat.params()
    assert len(params) == (len(STEP1["param_to_family"])
                           + len(DELTA["params_to_families"])
                           + len(cat.dictionaries()["unbound_params"]))
    # 0.2.0-draft.2 (слово владельца 08.09): max_sit_duration получил
    # семью, и «словаря решателя» в живом каталоге не осталось ни у
    # одного имени — источник «семья» выводит читатель сам, поля source
    # в записи нет. Источник «словарь CDPM» остаётся у имён без семьи.
    assert params["max_sit_duration"]["source"] == "семья"
    assert params["max_sit_duration"]["family"] == "connection_windows"
    assert params["max_block_time"]["source"] == "словарь CDPM"
    assert params["max_block_time"]["family"] is None
    assert params["min_turnaround"]["family"] == "connection_windows"
    assert set(params["crew_size"]) == {"unit", "relation", "qualifier", "family",
                                        "family_status", "source", "tz_required",
                                        "note"}
    assert set(cat.conditions()) == {"extension_bases", "extension_consent"}
    lx = cat.lexicon()
    assert set(lx) == {"subject_words", "complements", "order"}
    assert lx["complements"]["duty_limits"] == ["duty_extension_penalty"]
    crits = cat.criteria()
    # шаг 4: два критерия построителя и шесть критериев-метрик решателя
    assert {"min_person_days", "rank_normalized_cost"} <= set(crits)
    assert len(crits) == (len(STEP1["criterion_types"])
                          + len(DELTA["criteria_added"]))
    assert cat.criterion("min_person_days")["instance_form"]["fields"][0]["name"] == "rate_per_duty"
    assert cat.criterion("total_pairing_cost")["computable_by"] == \
        ["builder_method", "kotlin_metric"]
    d = cat.dictionaries()
    assert d["area_param_names"] == ["min_turnaround"]
    assert "form_version" not in d
    # Число шаблонов проверок держит закон паритета (L09: перечень ≡
    # cdpm.data_checks ∪ сертификатные ∪ префиксы); здесь — форма и то,
    # что аксессор и словарь называют ОДНО.
    assert set(cat.check_templates()) == set(d["check_templates"])
    assert cat.check_templates(), "шаблонов проверок нет вовсе"
    # Очередь заявок живого каталога — перечень заявок формы службы (с
    # 09.09.2026 в ней заявка duty_series_limit в работе): форма, а не
    # пустота — заявки живут в дереве вне отпечатка.
    reqs = cat.requests()
    assert isinstance(reqs, list)
    for rq in reqs:
        assert isinstance(rq, dict)
        assert {"request_id", "state", "section", "record_id"} <= set(rq)
    # Живой каталог — ВЫПУСК (слово владельца 08.09): черновиком он
    # больше не бывает, версия — из шапки по образцу semver, а журнал
    # версий кончается ЭТОЙ версией с отпечатком шапки.
    assert not cat.is_draft()
    assert cat.version() == cat.header["version"]
    assert SEMVER_RE.match(cat.version()), cat.version()
    журнал = cat.versions()
    assert [v["version"] for v in журнал][-1] == cat.version()
    assert журнал[-1]["fingerprint"] == cat.fingerprint()
    assert журнал[-1]["released_by"] == "владелец"
    assert isinstance(cat.canonical_json(), str) and cat.canonical_json().startswith('{"criteria"')


def test_unknown_record_lists_known_ids(cat):
    with pytest.raises(tc.UnknownRecord) as ei:
        cat.family("nope")
    msg = str(ei.value)
    assert "nope" in msg and "duty_limits" in msg and cat.version() in msg
    assert isinstance(ei.value, KeyError)
    with pytest.raises(tc.UnknownRecord) as ei:
        cat.criterion("nope")
    assert "min_person_days" in str(ei.value)


def test_status_filter(cat):
    assert cat.families(status=("снята",)) == {}
    assert cat.families(status=None) == cat.families()
    assert cat.params(status=("снята",)).keys() == set(cat.dictionaries()["unbound_params"])
    assert cat.criteria(status=("снята",)) == {}


def _класс_документа(cat):
    """Класс документа-источника — из ЖИВОЙ копии: словарь
    `document_classes` появится первой сборкой службой (шаг 6, 0.14), а
    до него перечень живёт в читателе. Литерал здесь развалил бы тест в
    день, когда словарь заведут."""
    from template_catalog import requests as _rq
    словарь = cat.dictionaries().get("document_classes")
    return (словарь or list(_rq.DOCUMENT_CLASSES))[0]


def test_families_full_include_requests(tmp_path, cat):
    dst = _copy(tmp_path)
    draft = cat.family("layover_penalty")
    for k in ("status", "history", "stand_tasks"):
        del draft[k]
    draft["id"] = "sit_overage_steps"
    draft["complements"] = []
    req = {"request_id": "req-0001", "state": "подана", "section": "families",
           "record_id": "sit_overage_steps",
           "submitted_at": "2026-09-10T15:40:00+03:00", "submitted_by": "CDPM",
           "source": {"kind": "посадка категории C",
                      "document_class": _класс_документа(cat),
                      "run_class": "прогон CDPM", "ref": "run-0090"},
           "draft": draft, "executor_kinds_requested": None,
           "decision": {"by": None, "at": None, "words": None}, "cr_key": None}
    (dst / "requests.json").write_text(
        json.dumps({"form_version": 1, "requests": [req]}, ensure_ascii=False),
        encoding="utf-8")
    c = tc.load(dst)
    assert c.fingerprint() == cat.fingerprint()              # заявки вне отпечатка
    assert not [p for p in c.validate() if p.severity == "error"]
    full = c.families_full(include_requests=True)
    added = [r for r in full if r["id"] == "sit_overage_steps"]
    assert added and added[0]["status"] == "заявлен" and added[0]["history"] == []
    assert added[0]["request_id"] == "req-0001"
    assert "sit_overage_steps" not in c.families()
    assert len(c.families_full()) == len(cat.families()), \
        "заявка прибавила запись «есть» — она обязана оставаться заявкой"


def test_module_level_wrappers(cat):
    assert tc.families(CATALOG) == cat.families()
    assert tc.family("duty_limits", CATALOG)["id"] == "duty_limits"
    assert tc.params(CATALOG).keys() == cat.params().keys()
    assert tc.lexicon(CATALOG)["order"] == cat.lexicon()["order"]
    assert tc.criteria(CATALOG).keys() == cat.criteria().keys()
    assert tc.dictionaries(CATALOG)["kinds"] == cat.dictionaries()["kinds"]
    assert tc.version(CATALOG) == cat.version()
    assert tc.fingerprint(CATALOG) == cat.fingerprint()
    # шаг 4: у каждой записи есть стендовые задачи — X07 больше нет
    assert [str(p) for p in tc.validate(CATALOG)] == []
    assert tc.__version__


# --------------------------------------------- канон и отпечаток
def test_fingerprint_cached_and_equals_canon(cat):
    assert cat.fingerprint() is cat.fingerprint()
    assert cat.fingerprint() == canon.fingerprint_of(cat.body())
    assert canon.canonical_bytes(cat.body()) == cat.canonical_json().encode("utf-8")
    assert "\n" not in cat.canonical_json() and " " not in cat.canonical_json()[:20]


def test_canon_is_insensitive_to_form_but_sensitive_to_data(cat):
    body = cat.body()
    fp = canon.fingerprint_of(body)
    # перестановка ключей, форма записи, form_version
    text = json.dumps(body, ensure_ascii=False, indent=3, sort_keys=False)
    reparsed = json.loads(text)
    reparsed["dictionaries"]["form_version"] = 1
    assert canon.fingerprint_of(reparsed) == fp
    # перестановка списков-множеств
    b2 = cat.body()
    b2["families"][0]["executor_kinds"].reverse()
    b2["families"].reverse()
    fam = next(f for f in b2["families"] if f["complements"])
    fam["params"].reverse()
    assert canon.fingerprint_of(b2) == fp
    # NFD ≡ NFC
    b3 = json.loads(unicodedata.normalize("NFD", json.dumps(cat.body(), ensure_ascii=False)))
    assert canon.fingerprint_of(b3) == fp
    # порядок данных значим: две записи истории одной даты
    b4 = cat.body()
    fam = next(f for f in b4["families"] if f["id"] == "night_series")
    same_day = [h for h in fam["history"] if h["date"] == "2026-09-05"]
    assert len(same_day) == 2
    i, j = fam["history"].index(same_day[0]), fam["history"].index(same_day[1])
    fam["history"][i], fam["history"][j] = fam["history"][j], fam["history"][i]
    assert canon.fingerprint_of(b4) != fp
    # subject_words — данные
    b5 = cat.body()
    next(f for f in b5["families"] if f["subject_words"])["subject_words"].reverse()
    assert canon.fingerprint_of(b5) != fp
    # 0.5.0: КАРТЫ устойчивы к перестановке ключей — ни чтение истории
    # записи, ни карта единиц нарушения словарей отпечатка не сдвинут.
    b6 = cat.body()
    fam = next(f for f in b6["families"] if f["reads_history"])
    fam["reads_history"] = dict(reversed(list(fam["reads_history"].items())))
    units = b6["dictionaries"]["violation_units"]
    b6["dictionaries"]["violation_units"] = dict(reversed(list(units.items())))
    assert list(b6["dictionaries"]["violation_units"]) != list(units)
    assert canon.fingerprint_of(b6) == fp
    # а СЛОВО единицы — данные: подмена перевода меняет отпечаток
    b7 = cat.body()
    код = sorted(b7["dictionaries"]["violation_units"])[0]
    b7["dictionaries"]["violation_units"][код] += " (иначе)"
    assert canon.fingerprint_of(b7) != fp


def test_canon_rejects_floats_and_extra_sections(cat):
    body = cat.body()
    body["families"][0]["rule_form"] = {"fields": [{"name": "days", "values": None,
                                                    "required": 1.5, "note": None}],
                                        "note": None}
    with pytest.raises(ValueError) as ei:
        canon.fingerprint_of(body)
    assert "families[0].rule_form.fields[0].required" in str(ei.value)
    with pytest.raises(ValueError):
        canon.normalize({**cat.body(), "requests": []})


def test_file_form_does_not_change_fingerprint(tmp_path, cat):
    dst = _copy(tmp_path)
    for name in ("families.json", "criteria.json", "dictionaries.json"):
        doc = json.loads((dst / name).read_text(encoding="utf-8"))
        text = json.dumps(doc, ensure_ascii=True, indent=4, sort_keys=False)
        (dst / name).write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    assert tc.load(dst).fingerprint() == cat.fingerprint()


# ------------------------------------------------------- выгрузка
def test_export_single_and_import_single_roundtrip(tmp_path, cat):
    text = export.export_single(cat)
    single = tmp_path / "catalog-single.json"
    single.write_text(text, encoding="utf-8")
    c1 = tc.load(single)
    assert c1.fingerprint() == cat.fingerprint() and c1.single_file
    assert c1.families() == cat.families()
    assert canon.fingerprint_of(json.loads(text)["data"]) == cat.fingerprint()
    out = export.import_single(text, tmp_path / "unpacked")
    c2 = tc.load(out)
    assert c2.fingerprint() == cat.fingerprint()
    assert not [p for p in c2.validate() if p.severity == "error"]
    assert manifest.catalog_stamp(c2) == {"version": cat.version(),
                                          "fingerprint": cat.fingerprint()}
    # форма файлов раскладки не зависит от ОС: LF, без BOM
    for name in tc.SECTION_FILES:
        raw = (out / name).read_bytes()
        assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf"), name
        assert raw.endswith(b"\n"), name
    with pytest.raises(tc.CatalogFormatError):
        export.import_single("{}", tmp_path / "bad")


def test_single_file_without_requests_or_versions_is_refused(tmp_path, cat):
    """§9 п.1: читатель ничего не подставляет — единый файл без заявок или
    журнала версий отказ словами, а не молчаливый []."""
    doc = json.loads(export.export_single(cat))
    for key in ("requests", "versions"):
        broken = {k: v for k, v in doc.items() if k != key}
        single = tmp_path / f"без-{key}.json"
        single.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(tc.CatalogFormatError) as ei:
            tc.load(single)
        assert key in str(ei.value) and single.name in str(ei.value)
        problems = tc.validate_path(single)
        assert [p.code for p in problems] == ["S01"] and key in problems[0].message


# ------------------------------------------------- независимость
def test_package_import_graph_is_standalone():
    banned = {"cdpm", "vmp", "webapp", "tools", "kotlin", "_data_paths"}
    for py in (ROOT / "template_catalog").glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for n in names:
                assert n.split(".")[0] not in banned, f"{py.name}: import {n}"
                assert n.split(".")[0] != "jsonschema" or py.name == "validate.py"


def test_package_import_reads_no_files_and_no_env():
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
import template_catalog
for name in ("jsonschema", "cdpm", "vmp", "webapp"):
    assert name not in sys.modules, name
print("ok")
"""
    res = subprocess.run([PY, "-c", script, str(ROOT)], capture_output=True,
                         text=True, encoding="utf-8", timeout=120,
                         env={"PYTHONUTF8": "1", "SYSTEMROOT": "C:\\Windows",
                              "PATH": ""})
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "ok"


def test_validate_import_of_jsonschema_is_lazy_and_optional(cat, monkeypatch):
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    problems = cat.validate()
    assert any(p.code == "S06" and p.severity == "warning" for p in problems)
    assert not [p for p in problems if p.severity == "error"]


# --------------------------------------------- сторож версий
def test_parse_and_compare_versions():
    assert manifest.parse_version("1.2.3") == ((1, 2, 3), None)
    assert manifest.parse_version("0.1.0-draft.4") == ((0, 1, 0), 4)
    with pytest.raises(ValueError):
        manifest.parse_version("1.2")
    d = manifest.compare_versions("0.1.0-draft.1", "0.1.0")
    assert (d.level, d.zero_major, d.draft_a, d.draft_b) == ("same", True, True, False)
    assert manifest.compare_versions("0.2.0", "0.1.9").level == "minor"
    assert manifest.compare_versions("1.2.0", "1.1.9").level == "minor"
    assert manifest.compare_versions("1.0.0", "2.0.0").level == "major"
    assert manifest.compare_versions("1.0.1", "1.0.0").level == "patch"


def test_check_manifest_catalog_levels(cat):
    fp = cat.fingerprint()
    b = {"catalog_version": "1.0.0", "catalog_fingerprint": fp}
    assert manifest.check_manifest_catalog(None, b).severity == "warning"
    assert manifest.check_manifest_catalog({"version": "1.0.0", "fingerprint": fp}, b) is None
    p = manifest.check_manifest_catalog({"version": "1.0.0", "fingerprint": "0" * 64}, b)
    assert p.severity == "error" and "разные данные" in p.message
    p = manifest.check_manifest_catalog({"version": "2.0.0", "fingerprint": fp}, b)
    assert p.severity == "error"
    p = manifest.check_manifest_catalog({"version": "1.1.0", "fingerprint": fp}, b)
    assert p.severity == "warning"
    b0 = {"catalog_version": "0.1.0", "catalog_fingerprint": fp}
    p = manifest.check_manifest_catalog({"version": "0.2.0", "fingerprint": fp}, b0)
    assert p.severity == "error", "младшая при 0.x — старшая"
    p = manifest.check_manifest_catalog({"version": "0.1.0-draft.1", "fingerprint": "0" * 64}, b0)
    assert p.severity == "warning" and "черновик" in p.message
    p = manifest.check_manifest_catalog({"version": "x"}, b0)
    assert p.severity == "error"


# ------------------------------------------------------ привязки
def _executor(kind, state="есть", **extra):
    row = {"kind": kind, "state": state, "lang": "python", "file": "vmp/x.py",
           "symbol": "Builder.chain_cost", "note": None, "cr_key": None}
    if state != "есть":
        row.update(lang=None, file=None, symbol=None, note="нет исполнителя")
        if state == "заявлен":
            row["cr_key"] = "CR-1"
    row.update(extra)
    return row


def _full_bindings(cat, product="COSMOS"):
    rows = []
    for rec in cat.families_full():
        rows.append({"section": "families", "id": rec["id"], "stand_verified": [],
                     "executors": [_executor(k, "нет") for k in rec["executor_kinds"]]})
    for rec in cat.criteria().values():
        rows.append({"section": "criteria", "id": rec["id"], "stand_verified": [],
                     "executors": [_executor(k, "нет") for k in rec["computable_by"]]})
    return {"product": product, "product_version": "1.18.5",
            "catalog_version": cat.version(), "catalog_fingerprint": cat.fingerprint(),
            "generated_at": "2026-09-08T12:00:00+03:00", "generated_from": "abc1234",
            "bindings": rows}


def test_bindings_green_and_states_table(cat, tmp_path):
    b = _full_bindings(cat)
    lay = next(r for r in b["bindings"] if r["id"] == "layover_penalty")
    lay["executors"] = [
        _executor("kotlin_constraint", lang="kotlin", constraint="Layover nights away from home",
                  file="kotlin-solver/X.kt", symbol="layoverNights"),
        _executor("builder_method"),
        _executor("check_template", template="мера-мягкого-правила",
                  symbol="measure_soft_rules"),
        _executor("kotlin_knob", "нет")]
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    loaded = tb.load_bindings(path)
    problems = tb.check_bindings(cat, loaded)
    errors = [p for p in problems if p.severity == "error"]
    assert not errors, "\n".join(map(str, errors))
    assert any(p.code == "B03" and p.severity == "warning" for p in problems), \
        "kotlin_knob у записи не объявлен — предупреждение"
    table = tb.executor_states(cat, loaded)
    assert table["families"]["layover_penalty"]["kotlin_constraint"] == {"COSMOS": "есть"}
    assert table["families"]["duty_limits"]["builder_method"] == {"COSMOS": "нет"}
    with pytest.raises(tc.CatalogNotFound):
        tb.load_bindings(tmp_path / "нет.json")
    (tmp_path / "кривой.json").write_text("{}", encoding="utf-8")
    with pytest.raises(tc.CatalogFormatError):
        tb.load_bindings(tmp_path / "кривой.json")


def _draft_catalog(tmp_path: Path, version: str = "0.3.0-draft.1", *,
                   name: str = "draft"):
    """Копия живого каталога с объявленной версией и пустым журналом.

    Живой каталог бывает то выпуском, то черновиком волны выпуска, и на
    нём встречается лишь одна из двух ветвей правила B01; закон обязан
    оставаться под гейтом обеими, поэтому и черновик, и выпуск строятся
    копиями."""
    dst = tmp_path / name
    if dst.exists():
        cc.rmtree_writable(dst)
    shutil.copytree(CATALOG, dst, ignore=shutil.ignore_patterns(
        "tools", "schema", "__pycache__", "releases"))
    head = json.loads((dst / "catalog.json").read_text(encoding="utf-8"))
    head["version"] = version
    (dst / "catalog.json").write_text(
        json.dumps(head, ensure_ascii=False, indent=1), encoding="utf-8")
    (dst / "versions.json").write_text(
        json.dumps({"form_version": 1, "versions": []}, ensure_ascii=False,
                   indent=1), encoding="utf-8")
    return tc.load(dst)


def _released_catalog(tmp_path, version: str = "9.9.9"):
    """Копия живого каталога с версией ВЫПУСКА (пара к `_draft_catalog`).

    Живой каталог бывает черновиком — так выглядит волна выпуска, — и
    ветвь правила B01 «стоп защищает выпуски» проверялась бы тогда на
    черновике, то есть не проверялась бы вовсе. Обе ветви закона обязаны
    оставаться под гейтом в любой день, поэтому и выпуск, и черновик
    строятся копиями."""
    return _draft_catalog(tmp_path, version=version, name="release")


def test_bindings_rules_b01_b05(cat, tmp_path):
    released = _released_catalog(tmp_path)
    b = _full_bindings(released)
    b["catalog_fingerprint"] = "0" * 64
    # Каталог-выпуск: одна версия с разными данными теперь ОСТАНОВКА
    # («стоп защищает выпуски»), а не предупреждение.
    assert any(p.code == "B01" and p.severity == "error"
               for p in tb.check_bindings(released, b))
    draft = _draft_catalog(tmp_path)
    bd = _full_bindings(draft)
    bd["catalog_fingerprint"] = "0" * 64
    codes = {p.code for p in tb.check_bindings(draft, bd)
             if p.severity == "warning"}
    assert "B01" in codes                                   # черновик — предупреждение
    b["catalog_version"] = "1.0.0"
    assert any(p.code == "B01" and p.severity == "error"
               for p in tb.check_bindings(released, b))
    b = _full_bindings(cat)
    b["bindings"].append({"section": "families", "id": "nope", "executors": [], "stand_verified": []})
    assert any(p.code == "B02" for p in tb.check_bindings(cat, b))
    b = _full_bindings(cat)
    row = next(r for r in b["bindings"] if r["id"] == "layover_penalty")
    row["executors"] = [_executor("kotlin_constraint", lang="kotlin", file="x.kt", symbol="a"),
                        _executor("builder_method"), _executor("check_template", "заявлен", cr_key=None)]
    probs = tb.check_bindings(cat, b)
    assert any(p.code == "B03" and "constraint" in p.message for p in probs)
    assert any(p.code == "B03" and "CR" in p.message for p in probs)
    b = _full_bindings(cat)
    row = next(r for r in b["bindings"] if r["id"] == "layover_penalty")
    row["executors"] = [_executor("builder_method")]
    assert any(p.code == "B04" and "kotlin_constraint" in p.where for p in tb.check_bindings(cat, b))
    b = _full_bindings(cat)
    row = next(r for r in b["bindings"] if r["id"] == "layover_penalty")
    row["stand_verified"] = [{"task": "st-1", "executor_kind": "kotlin_constraint",
                              "result": "passed", "at": "2026-09-08T12:00:00+03:00",
                              "commit": "abc1234", "note": None}]
    assert any(p.code == "B05" for p in tb.check_bindings(cat, b))


def test_bindings_schema_closed_and_typed(cat):
    """Схема $defs/bindings_file применяется: лишнее поле исполнителя,
    отсутствующее поле шапки, кривые образцы stand_verified — ошибки с
    адресами в форме check_bindings; зелёные привязки схемой не трогаются."""
    b = _full_bindings(cat)
    assert not [p for p in tb.check_bindings(cat, b) if p.severity == "error"]
    b = _full_bindings(cat)
    del b["generated_at"]
    b["extra"] = 1
    row = next(r for r in b["bindings"] if r["id"] == "layover_penalty")
    row["executors"][0]["symbo"] = "x"                      # опечатка в имени поля
    row["stand_verified"] = [{"task": "st-1", "executor_kind": "kotlin_constraint",
                              "result": "passed", "at": "вчера", "commit": "xyz",
                              "note": None}]
    probs = tb.check_bindings(cat, b)
    by_where = {p.where: p for p in probs if p.severity == "error"}
    assert by_where["bindings[COSMOS].generated_at"].code == "B01"           # нет поля шапки
    assert "generated_at" in by_where["bindings[COSMOS].generated_at"].message
    assert by_where["bindings[COSMOS].extra"].code == "B01"
    assert by_where["bindings[COSMOS].bindings/layover_penalty.executors[0].symbo"].code == "B03"
    assert by_where["bindings[COSMOS].bindings/layover_penalty.stand_verified[0].at"].code == "B05"
    assert by_where["bindings[COSMOS].bindings/layover_penalty.stand_verified[0].commit"].code == "B05"


def test_bindings_schema_without_jsonschema_warns(cat, monkeypatch):
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    b = _full_bindings(cat)
    b["extra"] = 1
    probs = tb.check_bindings(cat, b)
    assert any(p.code == "S06" and p.severity == "warning" for p in probs)
    assert not [p for p in probs if p.severity == "error"]


def test_problem_str_format():
    p = tc.Problem("families/duty_limits/params[2].unit", "D01",
                   'неизвестная единица "hours"', "error")
    assert str(p) == '[error] families/duty_limits/params[2].unit: неизвестная единица "hours"'
