# -*- coding: utf-8 -*-
"""Каталог шаблонов — ЕДИНСТВЕННЫЙ источник словарей CDPM (шаг 7
переезда, стадии 1а, 1б и 1в; проект final-report/KATALOG-SHAG-7-CDPM-
NOSITELI-PROEKT-CLD.md §2, §7).

Что охраняется:
  0) НОСИТЕЛИ СЛОВАРЕЙ В КОДЕ CDPM НЕ ВОСКРЕСАЮТ (стадия 1б): в
     `cdpm/**` нет имён снятых констант, нет переменной режима среза
     базы и нет литеральных перечней, равных по множеству словарям
     каталога, — вне JSON-схемы модели, где перечень есть закон формы;
     а перечни, оставшиеся кодом ПО ПРИРОДЕ (синонимы области, зоны,
     дни недели, ветви единиц условий, признак источника), сверяются с
     каталогом по множеству — иначе закон CDPM и данные каталога
     разошлись бы молча;
  1) внутри `cdpm/model.py` нет ни одного голого имени словаря каталога
     (AST-гейт: имя обслуживает только модульный `__getattr__` и
     функция-доступ `_cat`) — иначе модуль перестал бы импортироваться
     без каталога, а отказ возникал бы не там, где его ловят рубежи;
  2) импорт модулей CDPM без каталога проходит кодом 0 — отказ рождается
     при ОБРАЩЕНИИ к словарю, а не при сборе;
  3) рубежи без каталога отдают СЛОВА, а не трассировку: `validate_model`
     и `validate_form` — одну строку проблемы, гейт пакета — список
     проблем, продуктовая команда — код 2;
  4) подмена словарей в тестах — только каталогом (`catalog_copies`), не
     `setattr` по имени модуля: имя, положенное в `globals()`, заморозило
     бы проекцию на весь прогон pytest;
  5) с каталогом значения словарей равны ответам ЧИТАТЕЛЯ — и составом,
     и порядком;
  6) РУБЕЖИ ПАНЕЛИ И ВЫХОДОВ (стадия 1в): панель без каталога стартует и
     говорит словами (`GET /api/catalog`, строка журнала старта),
     синхронная ручка отдаёт 503 с кодом `catalog-unavailable`, нить
     стадии — «остановка: каталог шаблонов недоступен», выпуск пакета —
     отказ словами, повтор — отказ у обеих кнопок; журнал, записанный
     при прежнем порядке словаря, назван словами ДО нажатия.
"""
import ast
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from cdpm import catalog_bridge as bridge
from cdpm import export_task_package as tp
from cdpm import model as m
from cdpm import vmp_form as vf
from cdpm.gates import gate_task_package as gate
from cdpm.sample import sample_model

ROOT = Path(__file__).resolve().parents[1]
MODEL_PY = ROOT / "cdpm" / "model.py"
CATALOG = ROOT / "catalog"
#: Функции `cdpm/model.py`, которым голое имя словаря позволено:
#: модульный `__getattr__`/`__dir__` (PEP 562) и функция-доступ.
_ALLOWED_FUNCS = ("__getattr__", "__dir__", "_cat")
#: Модули, чей ИМПОРТ обязан проходить без каталога.
_IMPORTS_WITHOUT_CATALOG = ("cdpm.catalog_bridge", "cdpm.model",
                            "cdpm.family_lexicon", "cdpm.form_text",
                            "cdpm.vmp_form", "cdpm.bindings_build")


@pytest.fixture(autouse=True)
def _fresh_bridge(monkeypatch):
    monkeypatch.delenv(bridge.ENV_CATALOG, raising=False)
    monkeypatch.delenv("CREW_VMP_TEMPLATE_BASE", raising=False)
    bridge.forget()
    yield
    bridge.forget()


def _no_catalog(monkeypatch, tmp_path) -> Path:
    """Контур без каталога: переменная указывает на несуществующую
    папку, кэш моста сброшен."""
    absent = tmp_path / "нет-каталога"
    monkeypatch.setenv(bridge.ENV_CATALOG, str(absent))
    bridge.forget()
    return absent


# ------------------- (0) носители словарей в коде не воскресают (1б)

#: Имена, снятые стадией 1б: константы-словари, обёртки запаса и
#: заглушка отпечатка. Возвращение любого из них в `cdpm/**` означало
#: бы второй источник словарей — ровно то, что шаг 7 снимает.
FORBIDDEN_NAMES = (
    "_PARAMS_CODE", "_PARAM_UNITS_CODE", "_CODE_UNITS_CODE",
    "_PARAM_RELATIONS_CODE", "_PARAM_TZ_CODE", "_AREA_QUALIFIERS_CODE",
    "_FIT_CATEGORIES_CODE", "SOFT_FAMILIES", "_SUBJECT_WORDS",
    "_COMPLEMENTARY", "_FALLBACK_UNITS", "_template_families_registry",
    "params_or", "vocab_or", "conditions_or", "families_or",
    "subject_words_or", "complementary_or", "unregistered",
)
#: Строки, которых в `cdpm/**` не должно быть вовсе (без исключения
#: «в тексте отказа»): переменная режима среза базы шаблонов, которой у
#: CDPM больше нет, и 12-значный отпечаток прежней базы.
FORBIDDEN_STRINGS = ("CREW_VMP_TEMPLATE_BASE", "bc78f37804ec")
#: Словари каталога, литеральную копию которых код CDPM держать не
#: вправе (§7 п. 1 проекта): именно они переехали в каталог.
GUARDED_VOCABULARIES = ("param_units", "code_units", "param_relations",
                        "param_tz", "area_qualifiers", "fit_categories")
#: Перечни, которые ОСТАЮТСЯ кодом по природе (закон формы модели и
#: закон порядка печати дней) — их согласованность с каталогом судит
#: тест ниже, а не запрет литералов.
LAW_ENUMS = ("ZONES", "KINDS", "PARTS", "_WEEK_ORDER")


def _cdpm_sources():
    for path in sorted((ROOT / "cdpm").rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def test_removed_carriers_do_not_come_back():
    """Гейт против воскрешения носителей: ни имени снятой константы, ни
    имени обёртки запаса, ни переменной среза базы в `cdpm/**`."""
    hits = []
    for path, text in _cdpm_sources():
        rel = path.relative_to(ROOT).as_posix()
        for name in FORBIDDEN_NAMES:
            for mt in re.finditer(r"\b" + re.escape(name) + r"\b", text):
                hits.append(f"{rel}:{text[:mt.start()].count(chr(10)) + 1}: "
                            f"{name}")
        for s in FORBIDDEN_STRINGS:
            for mt in re.finditer(re.escape(s), text):
                hits.append(f"{rel}:{text[:mt.start()].count(chr(10)) + 1}: "
                            f"{s}")
    assert not hits, ("носитель словарей вернулся в код CDPM — источник "
                      f"один, каталог: {hits}")


def _literal_string_sequences(tree):
    """Литеральные перечни строк (кортеж/список/множество из двух и
    более строковых констант) — кроме тех, что присвоены именам
    LAW_ENUMS: те остаются кодом по природе и судятся согласованностью."""
    exempt: set = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target,
                                                            ast.Name):
            targets = [node.target]
        if any(t.id in LAW_ENUMS for t in targets) and node.value is not None:
            exempt.update(id(n) for n in ast.walk(node.value))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Tuple, ast.List, ast.Set)) \
                or id(node) in exempt or len(node.elts) < 2:
            continue
        vals = [n.value for n in node.elts
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        if len(vals) == len(node.elts):
            yield node, vals


def test_no_literal_copies_of_catalog_vocabularies():
    """В `cdpm/**` нет литерального перечня, равного по множеству
    словарю каталога: такая копия — второй источник, который стареет
    молча (живой случай — перечень отношений в `cdpm/extract.py` до
    шага 7). Перечни JSON-схемы модели — исключение по построению:
    схема лежит в `cdpm/schemas/`, кодом не является и судится
    вложением в словари каталога отдельным тестом."""
    guarded = {key: set(bridge.vocab(key)) for key in GUARDED_VOCABULARIES}
    hits = []
    for path, text in _cdpm_sources():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(text, filename=rel)
        for node, vals in _literal_string_sequences(tree):
            for key, want in guarded.items():
                if set(vals) == want:
                    hits.append(f"{rel}:{node.lineno}: копия словаря "
                                f"{key!r} — {vals}")
    assert not hits, ("литеральная копия словаря каталога в коде CDPM: "
                      f"{hits}")


def test_code_laws_agree_with_the_catalog():
    """Перечни, оставшиеся кодом ПО ПРИРОДЕ, сверяются с каталогом по
    множеству — иначе закон CDPM и данные каталога разошлись бы молча
    (§1, §7 п. 4 проекта). Порядок у кода свой: `ZONES` — порядок формы
    модели, `_WEEK_ORDER` — порядок печати понедельник-первый, каталог
    отдаёт оба по алфавиту."""
    d = bridge.catalog().dictionaries()
    assert set(m.ZONES) == set(d["zones"]), "зоны кода и каталога"
    assert set(m._WEEK_ORDER) == set(d["weekdays"]), "дни недели"
    assert set(m.SOFT_FAMILY_DAYS.values()) <= set(d["weekdays"])
    assert set(m._AREA_SYNONYMS.values()) <= set(d["area_qualifiers"]), \
        "синонимы области ведут в канон каталога"
    assert bridge.SOLVER_ONLY_SOURCE in d["param_sources"], \
        "признак отбора имён — значение словаря источников каталога"
    # Единицы условий: у КАЖДОЙ единицы каталога код знает ветвь, и
    # ветвей сверх каталога у кода нет.
    for unit in d["condition_units"]:
        got = m.condition_value_problem("c", unit, object())
        assert got is not None, f"единица {unit}: ветвь кода не судит"
        assert "вне перечня единиц условий каталога" not in got, unit
    unknown = m.condition_value_problem("c", "нет-такой-единицы", "x")
    assert "вне перечня единиц условий каталога" in unknown
    # Метрики целевых и виды критериев — пока код (перевод отдельным
    # шагом, §11 п. 3); когда каталог их несёт, они его подмножество.
    ids = set(m.METRICS) | set(m.CRITERION_KINDS.values())
    criteria = set(bridge.catalog().criteria() or {})
    if not criteria:
        pytest.skip("каталог этой версии критериев не несёт — сверять не с чем")
    assert ids <= criteria, sorted(ids - criteria)


def test_model_schema_enums_are_inside_catalog_vocabularies():
    """Перечни JSON-схемы модели — закон ФОРМЫ документа, но значения в
    них обязаны быть значениями словарей каталога: схема, разошедшаяся
    со словарём, отбраковала бы законный пакет молча."""
    import json as _json
    schema = _json.loads((ROOT / "cdpm" / "schemas"
                          / "cdpm-model.schema.json").read_text(
        encoding="utf-8"))
    d = bridge.catalog().dictionaries()
    watched = {k: set(d[k]) for k in ("param_units", "param_relations",
                                      "param_tz", "fit_categories",
                                      "weekdays")}
    enums = []

    def walk(node):
        if isinstance(node, dict):
            got = node.get("enum")
            if isinstance(got, list) and got \
                    and all(isinstance(v, str) for v in got):
                enums.append(set(got))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    assert enums, "перечни схемы найдены"
    bad = [(key, sorted(got - want)) for got in enums
           for key, want in watched.items()
           if (got & want) and not (got <= want)]
    assert not bad, f"перечень схемы вышел за словарь каталога: {bad}"


# ------------------------------------------------ (а) AST-гейт модуля
def test_model_has_no_bare_catalog_names():
    """Ни одного узла Name с именем словаря каталога вне `__getattr__`,
    `__dir__` и функции-доступа `_cat` (образец — AST-скан двери COSMOS
    tests/vmp/test_catalog_access.py)."""
    tree = ast.parse(MODEL_PY.read_text(encoding="utf-8"))
    allowed_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in _ALLOWED_FUNCS:
            allowed_nodes.update(id(n) for n in ast.walk(node))
    bare = [f"{n.id} (строка {n.lineno})" for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id in m._CATALOG_NAMES
            and id(n) not in allowed_nodes]
    assert not bare, (
        "голое имя словаря каталога в cdpm/model.py — модуль перестанет "
        f"импортироваться без каталога: {bare}")
    # Сами имена в globals() модуля не лежат: их отдаёт __getattr__.
    import cdpm.model as mod
    assert not (set(vars(mod)) & set(m._CATALOG_NAMES))
    assert set(m._CATALOG_NAMES) <= set(dir(mod))


# --------------------------------- (б) импорт модулей без каталога
def test_modules_import_without_catalog(monkeypatch, tmp_path):
    """Импорт в ДОЧЕРНЕМ процессе с несуществующим каталогом — код 0:
    вычислений словарей на уровне модуля в продукте нет."""
    absent = _no_catalog(monkeypatch, tmp_path)
    code = "import " + ", ".join(_IMPORTS_WITHOUT_CATALOG)
    env = dict(os.environ)
    env[bridge.ENV_CATALOG] = str(absent)
    env["PYTHONUTF8"] = "1"
    env.pop("CREW_VMP_TEMPLATE_BASE", None)
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       cwd=str(ROOT), env=env, capture_output=True,
                       text=True, timeout=180)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")


# ------------------------------------------- (в) рубежи без каталога
def test_dictionaries_refuse_with_words_without_catalog(monkeypatch,
                                                        tmp_path):
    absent = _no_catalog(monkeypatch, tmp_path)
    for call in (lambda: m.PARAMS, lambda: m.template_families(),
                 lambda: m.registry_fingerprint()):
        with pytest.raises(bridge.CatalogRequired) as e:
            call()
        assert str(e.value).startswith("CDPM: каталог шаблонов обязателен: ")
        assert str(absent) in str(e.value)
        assert e.value.code == "catalog-unavailable"


def test_task_package_gate_returns_problem_not_traceback(monkeypatch,
                                                         tmp_path):
    """Гейт пакета зовёт `validate_model`; без каталога тот отдаёт ОДНУ
    строку проблемы, и гейт остаётся списком проблем (кнопка COSMOS
    называет остановку словами, а не падает трассировкой)."""
    # Минимальный ГОТОВЫЙ пакет — фикстурой моста А (одна правда о том,
    # что такое пакет, у обоих тестов).
    from test_cdpm_task_package import _ready_doc, _run_dir
    run = _run_dir(tmp_path, _ready_doc())
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(tp.export_zip(run))
    assert zipfile.is_zipfile(pkg)
    _no_catalog(monkeypatch, tmp_path)
    problems, _counters = gate.check(pkg)
    words = [p for p in problems if "каталог шаблонов недоступен" in p]
    assert len(words) == 1, problems
    assert "не судятся" in words[0]


def test_validate_form_boundary_is_one_line(monkeypatch, tmp_path):
    form = vf.to_form(sample_model())
    _no_catalog(monkeypatch, tmp_path)
    problems = vf.validate_form(form)
    assert len(problems) == 1, problems
    assert problems[0].startswith("каталог шаблонов недоступен: ")


def test_bindings_command_returns_two_with_words(monkeypatch, tmp_path,
                                                 capsys):
    from cdpm import bindings_build as bb
    _no_catalog(monkeypatch, tmp_path)
    out_file = tmp_path / "bindings.json"
    assert bb.main(["--out", str(out_file)]) == 2
    out = capsys.readouterr().out
    assert "каталог шаблонов недоступен" in out, out
    assert not out_file.exists(), "при отказе файл привязок не пишется"


# ------------------------------------- (г) гигиена подмен в тестах
def test_tests_do_not_setattr_catalog_names():
    """Подмена словаря — только каталогом (`tests/catalog_copies.py` +
    CREW_TEMPLATE_CATALOG). `setattr` по имени модуля положил бы имя в
    globals(), и проекция замёрзла бы на весь прогон pytest."""
    names = "|".join(m._CATALOG_NAMES)
    patterns = [
        re.compile(r"setattr\(\s*[A-Za-z_][A-Za-z_0-9]*\s*,\s*"
                   r"[\"'](?:" + names + r")[\"']"),
        re.compile(r"^\s*[A-Za-z_][A-Za-z_0-9]*\.(?:" + names
                   + r")\s*=[^=]", re.M),
    ]
    hits = []
    for path in sorted((ROOT / "tests").glob("test_cdpm_*.py")):
        text = path.read_text(encoding="utf-8")
        for rx in patterns:
            for mt in rx.finditer(text):
                line = text[:mt.start()].count("\n") + 1
                hits.append(f"{path.name}:{line}: {mt.group(0).strip()}")
    assert not hits, hits


# --------------------------- (д) значения словарей равны читателю
def test_dictionaries_equal_the_reader():
    """С каталогом значения равны ответам читателя — составом и
    порядком; имена с источником «словарь решателя» в PARAMS не входят
    (правило отбора шага 2 сохраняется)."""
    cat = bridge.catalog()
    table = cat.params()
    expected = {n: str(s["unit"]) for n, s in table.items()
                if s.get("unit") and s.get("source") != bridge.SOLVER_ONLY_SOURCE}
    assert m.PARAMS == expected
    assert list(m.PARAMS) == list(expected)          # порядок читателя
    solver_only = {n for n, s in table.items()
                   if s.get("source") == bridge.SOLVER_ONLY_SOURCE}
    # Правило отбора: имена «только словарь решателя» в PARAMS не входят.
    # Сколько их в живом каталоге — не утверждается (на выпуске 0.2.0 —
    # ни одного, но ручка решателя без записи — законное состояние
    # unbound, и каталогу позволено расти); само правило доказывает
    # tmp-копия ниже (test_solver_only_unbound_name_is_excluded_from_params).
    assert not (solver_only & set(m.PARAMS))
    d = cat.dictionaries()
    assert m.PARAM_UNITS == tuple(d["param_units"])
    assert m.CODE_UNITS == tuple(d["code_units"])
    assert m.PARAM_RELATIONS == tuple(d["param_relations"])
    assert m.PARAM_TZ == tuple(d["param_tz"])
    assert m.AREA_QUALIFIERS == tuple(d["area_qualifiers"])
    assert m.FIT_CATEGORIES == tuple(d["fit_categories"])
    assert m.AREA_PARAM_NAMES == tuple(d["area_param_names"])
    assert m.template_families() == {f: list(ps)
                                     for f, ps in cat.families().items()}


# ------- (д2) записи семей: ни одного чтения обёртки решателя (1г)
def test_cdpm_does_not_import_the_solver_wrapper():
    """CDPM не зависит от обёртки решателя `vmp.template_registry`:
    свойства и заголовки семей, карта «имя → семья», единицы нарушения
    и закон исполнителя читаются из каталога через мост. Перечень
    допустимых исключений ПУСТ — обёртка снимается частью COSMOS 7-Б, и
    ни одно чтение из `cdpm/**` не должно её пережить.

    Судится по узлам импорта (AST), а не по тексту: докстроки называют
    обёртку историей правки — это память, а не зависимость."""
    allowed: set = set()          # исключений нет и не планируется
    hits = []
    for path in sorted((ROOT / "cdpm").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [mod] + [f"{mod}.{a.name}" for a in node.names]
            for name in names:
                if name != "vmp.template_registry" \
                        and not name.startswith("vmp.template_registry."):
                    continue
                if rel in allowed:
                    continue
                hits.append(f"{rel}:{node.lineno}: {name}")
    assert not hits, ("чтение обёртки решателя в cdpm/** — она снимается "
                      f"частью COSMOS 7-Б: {hits}")


def test_family_records_of_the_listing_equal_the_catalog():
    """Задание модели, свойства и закон исполнителя — из ЗАПИСЕЙ
    каталога: заголовки семей в листинге посадки, вид и зона в traits,
    пометка семьи у имени в листинге словаря и `family_has_executor`
    равны данным читателя (ожидания живые, не литералы)."""
    cat = bridge.catalog()
    recs = {r["id"]: r for r in cat.families_full()}
    assert set(bridge.family_records()) == set(recs)

    # Заголовки: титул записи одной строкой — он идёт в задание.
    titles = {f: " ".join(str(r.get("title") or "").split())
              for f, r in recs.items()}
    assert bridge.family_titles() == titles
    listing = m.template_fit_listing()
    for fam, title in titles.items():
        assert f"- {fam} — {title};" in listing, fam

    # Вид и зона: запись каталога, сверенная с законом формы модели.
    meta = cat.families_meta()
    assert m.template_family_traits() == {
        f: {"kind": s["kind"] if s["kind"] in ("HARD", "SOFT") else None,
            "zone": s["zone"] if s["zone"] in m.ZONES else None}
        for f, s in meta.items()}
    assert bridge.family_traits("connection_windows")["violation_unit"] \
        == recs["connection_windows"].get("violation_unit")
    assert bridge.family_traits("нет-такой-семьи") == {}

    # Пометка семьи у имени параметра в листинге словаря.
    p2f = {n: s["family"] for n, s in cat.params().items() if s.get("family")}
    assert bridge.param_families() == p2f
    words = m.machine_dictionary_listing()
    for name, unit in m.PARAMS.items():
        fam = p2f.get(name)
        mark = f" [исполнитель: {fam}]" if fam else ""
        assert f"- {name} ({unit}){mark}" in words, name

    # Закон 1 «есть исполнитель»: виды записи вне check_template.
    for fam, rec in recs.items():
        kinds = [k for k in (rec.get("executor_kinds") or [])
                 if k != bridge.CHECK_TEMPLATE_KIND]
        assert bridge.family_has_executor(fam) is bool(kinds), fam
        assert m._executor_covers({"template_fit": {
            "category": "A", "family": fam}}) is bool(kinds), fam
    assert bridge.family_has_executor("нет-такой-семьи") is False


# ------------------ (е) рубежи панели и продуктовых выходов (стадия 1в)

@pytest.fixture()
def panel(tmp_path, monkeypatch):
    """Панель на тестовом клиенте: каталог прогонов и настройки — в
    tmp (рабочие данные тесты не трогают), языковая модель — та же
    заготовка, что в тестах извлечения (сети нет)."""
    from fastapi.testclient import TestClient

    from cdpm import webui
    from test_cdpm_extract import fake_llm
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(webui, "llm_factory", lambda scope: fake_llm)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: fake_llm)
    return TestClient(webui.app)


def _new_run(client) -> str:
    """Прогон с двумя документами правил: заморозка каталога шаблонов
    не требует — её можно пройти и на контуре без каталога."""
    from test_cdpm_extract import _RULES_A, _RULES_B
    r = client.post("/api/runs", files=[
        ("files", ("RULES-A.md", _RULES_A.encode("utf-8"), "text/markdown")),
        ("files", ("RULES-B.md", _RULES_B.encode("utf-8"), "text/markdown")),
    ], params={"customer": "Полярис (тест)", "data_class": "synthetic"})
    assert r.status_code == 200, r.text
    return r.json()["run"]


def _wait_line(client, run_id: str, timeout_s: float = 60.0) -> dict:
    import time
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = client.get(f"/api/runs/{run_id}").json()
        state = str(st.get("state") or "")
        if not (state.endswith("идёт") or state.endswith("идут")):
            return st
        time.sleep(0.1)
    raise AssertionError("стадия не завершилась за отведённое время")


def test_panel_starts_without_catalog_and_says_it_in_words(panel, monkeypatch,
                                                           tmp_path,
                                                           declared_test_layout):
    """Панель СТАРТУЕТ и без каталога (иначе площадка ушла бы в цикл
    перезапусков), но не молчит: строка журнала старта, `GET
    /api/catalog` со словами и машинным кодом, а синхронная ручка,
    которой нужны словари, — 503 с тем же кодом, не трассировка."""
    from cdpm import webui
    run_id = _new_run(panel)
    absent = _no_catalog(monkeypatch, tmp_path)
    assert panel.get("/api/runs").status_code == 200, "панель жива"

    body = panel.get("/api/catalog").json()
    assert body["ok"] is False and body["available"] is False
    assert body["code"] == "catalog-unavailable"
    assert body["version"] is None and body["fingerprint"] is None
    assert str(absent) in body["error"]
    assert body["words"].startswith("каталог шаблонов недоступен: ")
    assert "каталог шаблонов недоступен" in webui.catalog_probe_line()
    # Настройки не показывают отметки каталога, которого нет.
    assert panel.get("/api/settings").json()["catalog"] is None

    r = panel.get(f"/api/runs/{run_id}/catalog-requests")
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "catalog-unavailable"
    assert detail["error"].startswith("каталог шаблонов недоступен: ")
    assert str(absent) in detail["error"]


def test_form_and_package_handles_refuse_in_words(monkeypatch, tmp_path):
    """Ручки, которым нужны словари, на прогоне С МОДЕЛЬЮ: форма ВМП —
    503 с машинным кодом, выпуск пакета — 409 словами (пакета без ключа
    `manifest.catalog` не бывает). Карточка прогона при этом отвечает:
    состояние и паспорт читаются и без каталога."""
    from fastapi.testclient import TestClient

    from cdpm import webui
    from test_cdpm_task_package import _ready_doc, _run_dir
    run = _run_dir(tmp_path, _ready_doc())
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    client = TestClient(webui.app)
    assert client.get(f"/api/runs/{run.name}/form").status_code == 200

    _no_catalog(monkeypatch, tmp_path)
    r = client.get(f"/api/runs/{run.name}/form")
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "catalog-unavailable"
    assert client.get(f"/api/runs/{run.name}/form.md").status_code == 503
    pkg = client.get(f"/api/runs/{run.name}/export/task-package")
    assert pkg.status_code == 409, pkg.text
    assert "каталог шаблонов недоступен" in pkg.json()["detail"]
    card = client.get(f"/api/runs/{run.name}")
    assert card.status_code == 200, card.text
    assert card.json()["replay"]["journal"]["reason_code"] \
        == "catalog-unavailable"


def test_stage_thread_stops_with_catalog_words(panel, monkeypatch, tmp_path,
                                               declared_test_layout):
    """Нить стадии отказ каталога НЕ роняет и не прячет: линия прогона
    получает «остановка: каталог шаблонов недоступен» и слова причины —
    беда контура названа отдельно от отказов работы над документами."""
    from cdpm import webui
    run_id = _new_run(panel)
    _no_catalog(monkeypatch, tmp_path)
    assert panel.post(f"/api/runs/{run_id}/extract").status_code == 200
    st = _wait_line(panel, run_id)
    assert st["state"] == webui.CATALOG_STOP_STATE, st
    assert st["error"].startswith(webui.CATALOG_STOP_STATE + ": ")
    assert "отказ извлечения" in st["error"], "стадия названа"


def test_replay_says_catalog_and_dictionary_order_before_the_press(
        panel, monkeypatch, tmp_path, declared_test_layout):
    """Повтор — чтение: без каталога обе кнопки отказывают СЛОВАМИ (код
    `catalog-unavailable`), а журнал, записанный при прежнем порядке
    словаря (паспорт без признака `dictionary_order`), назван словами до
    нажатия — из журнала он повторится с расхождением на первом вызове
    извлечения, доступен повтор заново."""
    import json as _json

    from cdpm import replay as replay_mod
    run_id = _new_run(panel)
    assert panel.post(f"/api/runs/{run_id}/extract").status_code == 200
    st = _wait_line(panel, run_id)
    assert st["state"] == "извлечено", st
    run_dir = Path(st["dir"]) if st.get("dir") else \
        Path(os.environ["CDPM_RUNS_DIR"]) / run_id

    # Прогон шага 7 несёт признак порядка — предупреждения нет.
    assert st["passport"]["created"]["catalog"]["dictionary_order"] \
        == bridge.DICTIONARY_ORDER
    assert st["replay"]["journal"]["reason_code"] is None, st["replay"]

    # Тот же прогон, записанный ПРЕЖНИМ порядком (признака нет).
    status_path = run_dir / "status.json"
    status = _json.loads(status_path.read_text(encoding="utf-8"))
    status["passport"]["environment"]["catalog"].pop("dictionary_order")
    status_path.write_text(_json.dumps(status, ensure_ascii=False),
                           encoding="utf-8")
    got = replay_mod.availability(run_dir)
    assert got["journal"]["available"] is True, "предупреждение, не запрет"
    assert got["journal"]["reason_code"] == "dictionary-order"
    assert got["journal"]["reason_words"] \
        == replay_mod.DICTIONARY_ORDER_WORDS
    assert "повтор заново" in got["journal"]["reason_words"]
    assert got["fresh"]["available"] is True

    _no_catalog(monkeypatch, tmp_path)
    both = replay_mod.availability(run_dir)
    for key in ("journal", "fresh"):
        assert both[key]["available"] is False
        assert both[key]["reason_code"] == "catalog-unavailable"
        assert both[key]["reason_words"].startswith(
            "каталог шаблонов недоступен: ")


def test_package_release_refuses_with_words_without_catalog(monkeypatch,
                                                            tmp_path):
    """Выпуск пакета без каталога — `TaskPackageError` СЛОВАМИ (ручка
    отдаёт 409), а не пакет без ключа `manifest.catalog` и не
    трассировка из внутренностей сборки. Сводка посадок
    (`fits_catalog_of`) моста не зовёт — она судит строки пакета."""
    from test_cdpm_task_package import _ready_doc, _run_dir
    run = _run_dir(tmp_path, _ready_doc())
    assert "manifest.json" in tp.build_package(run)
    _no_catalog(monkeypatch, tmp_path)
    with pytest.raises(tp.TaskPackageError) as e:
        tp.build_package(run)
    assert str(e.value).startswith(
        "выпуск пакета невозможен: каталог шаблонов недоступен: ")
    doc = {"rules": [{"template_fit": {"registry_rev": "bc78f37804ec"}}]}
    got = tp.fits_catalog_of(doc, {"version": "0.1", "fingerprint": "abc"})
    assert got["matches_catalog"] is False, got


def test_catalog_bridge_command_answers_with_a_line_and_a_code(tmp_path):
    """Команда-проба площадки и установщика: код 0 и строка с версией,
    отпечатком, числом предупреждений и путём; без каталога — код 2 и
    слова. Первое слово строки машинное и ASCII — чек-лист раската
    сверяет площадки и машиной, и глазами."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.pop("CREW_VMP_TEMPLATE_BASE", None)
    env.pop(bridge.ENV_CATALOG, None)
    cmd = [sys.executable, "-X", "utf8", "-m", "cdpm.catalog_bridge"]
    ok = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True,
                        timeout=180)
    out = ok.stdout.decode("utf-8", "replace")
    assert ok.returncode == 0, out + ok.stderr.decode("utf-8", "replace")
    head = out.strip().splitlines()[-1].split()
    assert head[:2] == ["CATALOG", "OK"]
    assert re.fullmatch(r"[0-9a-f]{64}", head[3])
    assert head[4].startswith("warnings=")
    assert Path(" ".join(head[5:])).is_dir(), out

    env[bridge.ENV_CATALOG] = str(tmp_path / "нет-каталога")
    bad = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True,
                         timeout=180)
    words = bad.stdout.decode("utf-8", "replace")
    assert bad.returncode == 2, words + bad.stderr.decode("utf-8", "replace")
    assert "CATALOG UNAVAILABLE: " in words
    assert "нет-каталога" in words, words


def test_solver_only_unbound_name_is_excluded_from_params(monkeypatch, tmp_path):
    """Правило отбора доказывается на tmp-копии каталога, а не на живом
    (просьба 1 извещения о паритете, 08.09): в копию подставлено имя
    unbound с источником «словарь решателя» — читатель его отдаёт, PARAMS
    его не берёт, а множество имён PARAMS остаётся равным «параметры с
    единицей и источником ≠ словарь решателя». Живому каталогу при этом
    позволено расти без правки теста."""
    import json
    import catalog_copies as cc
    from template_catalog import canon as tcanon
    from template_catalog.export import dump_json
    folder = cc.copy_catalog(tmp_path)
    probe = "probe_solver_knob"
    dic_p = folder / "dictionaries.json"
    dic = json.loads(dic_p.read_text(encoding="utf-8"))
    assert probe not in dic["unbound_params"]
    dic["unbound_params"][probe] = {
        "note": "проба: ручка решателя без записи каталога",
        "relation": None, "source": bridge.SOLVER_ONLY_SOURCE,
        "unit": "duration_minutes"}
    dic_p.write_bytes(dump_json(dic).encode("utf-8"))
    # Отпечаток шапки — той же формулой, что у помощника копий и писателя.
    fams = json.loads((folder / "families.json").read_text(encoding="utf-8"))["families"]
    crits = json.loads((folder / "criteria.json").read_text(encoding="utf-8"))["criteria"]
    body = {"families": fams, "criteria": crits,
            "dictionaries": {k: v for k, v in dic.items() if k != "form_version"}}
    hdr_p = folder / "catalog.json"
    hdr = json.loads(hdr_p.read_text(encoding="utf-8"))
    hdr["fingerprint"] = tcanon.fingerprint_of(body)
    hdr_p.write_bytes(dump_json(hdr).encode("utf-8"))
    bnd_p = folder / cc.BINDINGS_FILE
    if bnd_p.is_file():
        bnd = json.loads(bnd_p.read_text(encoding="utf-8"))
        bnd["catalog_fingerprint"] = hdr["fingerprint"]
        bnd_p.write_bytes(dump_json(bnd).encode("utf-8"))
    cc.use_catalog(monkeypatch, folder)
    bridge.forget()
    table = bridge.catalog().params()
    assert table[probe]["source"] == bridge.SOLVER_ONLY_SOURCE
    assert probe not in m.PARAMS
    assert set(m.PARAMS) == {n for n, s in table.items()
                             if s.get("unit")
                             and s.get("source") != bridge.SOLVER_ONLY_SOURCE}
    assert probe in {n for n, s in table.items()
                     if s.get("source") == bridge.SOLVER_ONLY_SOURCE}
