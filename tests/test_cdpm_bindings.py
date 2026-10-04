# -*- coding: utf-8 -*-
"""Гейт продукта: привязки CDPM к записям каталога шаблонов (шаг 4
каталога, часть CDPM; проект
final-report/KATALOG-SHAG4-CDPM-PRIVYAZKI-CLD.md §4).

Судятся: чтение файла читателем каталога, существование символов кода у
исполнителей «есть», знание имён шаблонов продуктом, структурные правила
B01–B05 читателя и совпадение файла с тем, что собирает генератор по
нынешнему коду и нынешнему каталогу. Сборка `bindings_build.build()` в
памяти судится ВСЕГДА; отсутствие файла `cdpm/bindings.json` — не
падение, а пропуск словами «привязки CDPM не объявлены» (семантика B04
«не объявлено», согласована с COSMOS): файл выпускается после выпуска
каталога 0.2.0-draft.1 сессией COSMOS.
"""
import re
from pathlib import Path

import pytest

from cdpm import bindings_build as bb
from cdpm import catalog_bridge as bridge
from cdpm import stand_tables as st
from cdpm.data_checks import TEMPLATES
from template_catalog.bindings import check_bindings, load_bindings

ROOT = Path(__file__).resolve().parents[1]
BINDINGS = ROOT / "cdpm" / "bindings.json"
COSMOS_BINDINGS = ROOT / "vmp" / "bindings.json"
#: Результаты стенда шаблонов проверок данных: пока файл не выпущен,
#: строки `stand_verified` пусты — сборка и файл судятся теми же мерками.
STAND_RESULTS = ROOT / "cdpm" / "stand_results.json"
#: Постоянные входы сборки: сверка `--check` их не смотрит, а тесту
#: нужна повторимость без обращения к git.
COMMIT = "0" * 40
AT = "2026-09-07T12:00:00+03:00"
ABSENT = "привязки CDPM не объявлены"


@pytest.fixture(autouse=True)
def _fresh_bridge(monkeypatch):
    monkeypatch.delenv(bridge.ENV_CATALOG, raising=False)
    monkeypatch.delenv("CREW_VMP_TEMPLATE_BASE", raising=False)
    bridge.forget()
    yield
    bridge.forget()


def _catalog():
    cat = bridge.catalog()
    assert cat is not None, ("каталог шаблонов недоступен — привязки CDPM "
                            "судить не по чему")
    return cat


def _stand() -> dict | None:
    """Результаты стенда, если они выпущены: сборка обязана видеть их
    так же, как их видит команда с ключом --stand-results."""
    return st.read_results(STAND_RESULTS) if STAND_RESULTS.is_file() else None


def _built() -> dict:
    return bb.build(_catalog(), commit=COMMIT, generated_at=AT,
                    stand_results=_stand())


def _docs() -> list:
    """Что судим: сборку в памяти — всегда, файл репозитория — если он
    выпущен. Без файла гейт не молчит: сборка судится теми же мерками."""
    out = [("сборка build()", _built())]
    if BINDINGS.is_file():
        out.append((str(BINDINGS), load_bindings(BINDINGS)))
    return out


def _executors(doc: dict):
    for row in doc.get("bindings") or []:
        for j, ex in enumerate(row.get("executors") or []):
            yield f"{row.get('section')}/{row.get('id')} исполнитель {j}", ex


def _groups() -> dict:
    dicts = _catalog().dictionaries() or {}
    return {str(n): (spec or {}).get("group")
            for n, spec in (dicts.get("check_templates") or {}).items()}


def _errors(problems, *, skip_b04: bool) -> list:
    """Ошибки читателя словами; B04 (полнота по всем видам записи) без
    привязок COSMOS не судится: одним продуктом она не закрывается."""
    return [str(p) for p in problems if p.severity == "error"
            and not (skip_b04 and p.code == "B04")]


# ------------------------------------------- п.1: файл читается читателем
def test_file_is_read_by_catalog_reader():
    if not BINDINGS.is_file():
        pytest.skip(ABSENT)
    doc = load_bindings(BINDINGS)
    assert doc.get("product") == bb.PRODUCT, \
        f"{BINDINGS}: продукт {doc.get('product')!r}, ожидался {bb.PRODUCT!r}"
    assert doc.get("bindings"), f"{BINDINGS}: список привязок пуст"


# ------------------------------- п.2: символы исполнителей «есть» есть
def test_symbols_of_present_executors_are_importable_and_callable():
    for label, doc in _docs():
        for where, ex in _executors(doc):
            if ex.get("state") != "есть":
                continue
            obj = bb.resolve_symbol(ex["file"], ex["symbol"])
            assert callable(obj), (
                f"{label}: {where}: {ex['file']}:{ex['symbol']} — "
                f"не вызываемый объект ({type(obj).__name__})")


# ---------------------------------- п.3: имена шаблонов известны CDPM
def test_templates_are_known_to_cdpm_or_belong_to_cosmos():
    groups = _groups()
    for label, doc in _docs():
        for where, ex in _executors(doc):
            name = ex.get("template")
            if ex.get("state") == "есть":
                assert name in TEMPLATES \
                    or str(name).startswith(bb.HISTORY_PREFIXES), (
                    f"{label}: {where}: шаблон {name!r} объявлен «есть», а "
                    f"кода CDPM для него нет (ни в data_checks.TEMPLATES, "
                    f"ни среди префиксов {bb.HISTORY_PREFIXES})")
                if name in TEMPLATES:
                    assert ex["file"] == bb.DATA_CHECKS_FILE, (
                        f"{label}: {where}: шаблон перечня исполняет "
                        f"{bb.DATA_CHECKS_FILE}, а объявлен {ex['file']}")
                else:
                    assert ex["file"] == bb.HISTORY_FILE, (
                        f"{label}: {where}: шаблон истории/горизонта "
                        f"исполняет {bb.HISTORY_FILE}, а объявлен "
                        f"{ex['file']}")
            else:
                assert groups.get(name) == bb.CERTIFICATE_GROUP, (
                    f"{label}: {where}: у шаблона {name!r} исполнителя нет, "
                    f"а к группе {bb.CERTIFICATE_GROUP!r} словаря он не "
                    f"относится (группа {groups.get(name)!r}) — молчаливого "
                    f"отказа быть не должно")
                assert ex.get("note"), (
                    f"{label}: {where}: у исполнителя «{ex.get('state')}» "
                    f"нет заметки о причине")


# ---------------------------------- п.4: структурные правила B01–B05
def test_check_bindings_of_reader_finds_nothing():
    cat = _catalog()
    cosmos = load_bindings(COSMOS_BINDINGS) if COSMOS_BINDINGS.is_file() \
        else None
    for label, doc in _docs():
        args = [cosmos, doc] if cosmos is not None else [doc]
        errors = _errors(check_bindings(cat, *args), skip_b04=cosmos is None)
        assert not errors, (f"{label}: читатель каталога нашёл ошибки "
                            f"привязок:\n  " + "\n  ".join(errors))


# ------------------- п.5: файл не отстал от кода и от каталога (B01)
def test_file_matches_code_and_catalog():
    if not BINDINGS.is_file():
        pytest.skip(ABSENT)
    stamp = bridge.stamp()
    doc = load_bindings(BINDINGS)
    assert doc.get("catalog_fingerprint") == stamp["fingerprint"], (
        f"привязки CDPM отстали от каталога: в {BINDINGS} отпечаток "
        f"{doc.get('catalog_fingerprint')}, у каталога "
        f"{stamp['version']} — {stamp['fingerprint']}; перегенерируйте "
        f"файл (python cdpm/bindings_build.py --out cdpm/bindings.json)")
    assert doc.get("catalog_version") == stamp["version"], (
        f"привязки CDPM к каталогу {doc.get('catalog_version')}, читается "
        f"{stamp['version']}")
    diff = bb.differences(_built(), doc)
    assert not diff, ("привязки CDPM отстали от кода или каталога:\n  "
                      + "\n  ".join(diff))
    argv = ["--check", str(BINDINGS), "--commit", COMMIT,
            "--generated-at", AT]
    if STAND_RESULTS.is_file():
        argv += ["--stand-results", str(STAND_RESULTS)]
    assert bb.main(argv) == 0


# --------------------------------------------- детерминизм и сверка
def test_build_is_deterministic():
    first, second = _built(), _built()
    assert bb.dump(first) == bb.dump(second), \
        "одинаковые входы дали разный текст файла привязок"
    text = bb.dump(first)
    assert text.endswith("\n") and "\r" not in text, \
        "форма файла: перевод строки LF и завершающий перевод строки"


def _stand_args() -> list:
    """Те же результаты стенда, что читает `_built()`: сборка командой и
    сборка в памяти обязаны судиться одними входами (задачи стенда живут
    в каталоге — строки stand_verified появляются у обеих сборок)."""
    return ["--stand-results", str(STAND_RESULTS)] if STAND_RESULTS.is_file() else []


def test_check_returns_zero_for_freshly_written_file(tmp_path, capsys):
    path = tmp_path / "bindings.json"
    assert bb.main(["--out", str(path), "--commit", COMMIT,
                    "--generated-at", AT] + _stand_args()) == 0
    assert path.read_bytes() == bb.dump(_built()).encode("utf-8")
    capsys.readouterr()
    assert bb.main(["--check", str(path), "--commit", COMMIT,
                    "--generated-at", AT] + _stand_args()) == 0
    out = capsys.readouterr().out
    assert "расхождений 0" in out, out


def test_check_names_differences_in_words(tmp_path, capsys):
    """Расхождение — код 1 и слова: какая запись, какой исполнитель,
    какое поле."""
    doc = _built()
    row = doc["bindings"][0]
    row["executors"][0]["note"] = "чужая заметка"
    doc["catalog_fingerprint"] = "0" * 64
    path = tmp_path / "bindings.json"
    path.write_bytes(bb.dump(doc).encode("utf-8"))
    capsys.readouterr()
    assert bb.main(["--check", str(path), "--commit", COMMIT,
                    "--generated-at", AT]) == 1
    out = capsys.readouterr().out
    assert "catalog_fingerprint" in out and "чужая заметка" in out, out
    assert f"{row['section']}/{row['id']}" in out, out
    assert "поле note" in out, out


# ------------------- стендовые подтверждения (шаг 4, форма мира tables)
def test_stand_verified_rows_stand_on_catalog_tasks():
    """Строка `stand_verified` появляется только по задаче, СТОЯЩЕЙ в
    записи каталога (иначе читатель скажет B05), несёт вид, исход из
    словаря каталога, отметку прогона стенда и слова заметки."""
    cat = _catalog()
    stand = _stand()
    dicts = cat.dictionaries() or {}
    outcomes = set(dicts.get("stand_results") or ())
    records = {f"{section}/{rec.get('id')}": rec
               for section, rec in bb._records(cat)}
    for label, doc in _docs():
        for row in doc.get("bindings") or []:
            addr = f"{row.get('section')}/{row.get('id')}"
            known = {str(t.get("id")) for t
                     in (records.get(addr, {}).get("stand_tasks") or [])}
            for sv in row.get("stand_verified") or []:
                where = f"{label}: {addr} стендовая строка {sv.get('task')!r}"
                assert sv.get("task") in known, (
                    f"{where}: такой задачи у записи каталога нет (B05)")
                assert sv.get("executor_kind") == bb.KIND, where
                assert sv.get("result") in outcomes, (
                    f"{where}: исход {sv.get('result')!r} вне словаря "
                    f"stand_results {sorted(outcomes)}")
                assert (sv.get("note") or "").strip(), (
                    f"{where}: исход без слов")
                if stand is not None:
                    assert sv.get("commit") == stand.get("commit"), where
                    assert sv.get("at") == stand.get("at"), where


def test_without_stand_results_rows_are_empty():
    """Без файла результатов стенда строки пусты — как до стенда:
    подтверждать нечем, а выдумывать подтверждения нельзя."""
    doc = bb.build(_catalog(), commit=COMMIT, generated_at=AT)
    assert all(row["stand_verified"] == [] for row in doc["bindings"])


# ------------------------- зона ответственности продукта в шапке (B04)
def test_executor_kinds_header_follows_reader_schema(monkeypatch):
    """Поле executor_kinds пишется только когда закрытая схема читателя
    его допускает (каталог 0.2.0-draft.1); иначе шапка без поля, и зона
    выводится читателем из строк — для CDPM это тот же один вид."""
    monkeypatch.setattr(bb, "_schema_allows", lambda field: True)
    with_field = _built()
    assert with_field.get("executor_kinds") == [bb.KIND], with_field.get(
        "executor_kinds")
    monkeypatch.setattr(bb, "_schema_allows", lambda field: False)
    without = _built()
    assert "executor_kinds" not in without
    kinds_in_rows = {ex.get("kind") for _, ex in _executors(without)}
    assert kinds_in_rows == {bb.KIND}, (
        f"строки CDPM должны нести один вид {bb.KIND!r}, а несут "
        f"{kinds_in_rows}")
    assert "executor_kinds" in bb.HEADER, \
        "сверка --check обязана видеть расхождение зоны в шапке"


def test_current_schema_decides_header_field():
    """Сборка по живой схеме согласуется с ней же: поле есть ровно тогда,
    когда схема его знает (после 0.2.0-draft.1 — есть)."""
    built = _built()
    assert ("executor_kinds" in built) == bb._schema_allows("executor_kinds")


# ---------------------------------------------- отказы всегда словами
class _StubCatalog:
    """Каталог с одним неизвестным CDPM шаблоном — для отказа сборки."""

    def __init__(self, template: str, group: str = "data"):
        self._t, self._g = template, group

    def dictionaries(self):
        return {"check_templates": {self._t: {"group": self._g}}}

    def families_full(self, *a, **k):
        return [{"id": "sample_family", "status": "есть",
                 "executor_kinds": ["check_template"],
                 "check_templates": [self._t]}]

    def criteria(self, *a, **k):
        return {}

    def version(self):
        return "0.1.0-draft.3"

    def fingerprint(self):
        return "0" * 64


def test_unknown_template_is_a_refusal_in_words():
    with pytest.raises(bb.BindingsBuildError) as e:
        bb.build(_StubCatalog("проверка-которой-нет"), commit=COMMIT,
                 generated_at=AT)
    words = str(e.value)
    assert "sample_family" in words and "проверка-которой-нет" in words, words


def test_catalog_without_check_templates_is_a_refusal():
    cat = _StubCatalog("обязательное-поле")
    cat.dictionaries = lambda: {}
    with pytest.raises(bb.BindingsBuildError) as e:
        bb.build(cat, commit=COMMIT, generated_at=AT)
    assert "check_templates" in str(e.value)


def test_missing_symbol_is_a_refusal():
    with pytest.raises(bb.BindingsBuildError) as e:
        bb.resolve_symbol(bb.DATA_CHECKS_FILE, "нет_такого_символа")
    assert bb.DATA_CHECKS_FILE in str(e.value)


def test_without_catalog_generator_refuses_with_words(monkeypatch, tmp_path,
                                                      capsys):
    monkeypatch.setenv(bridge.ENV_CATALOG, str(tmp_path / "нет-каталога"))
    bridge.forget()
    assert bb.main(["--out", str(tmp_path / "bindings.json")]) == 2
    out = capsys.readouterr().out
    assert "каталог шаблонов недоступен" in out, out
    assert not (tmp_path / "bindings.json").exists(), \
        "при отказе файл не пишется"


def test_head_commit_is_full_hash():
    try:
        commit = bb.head_commit()
    except bb.BindingsBuildError as e:                      # pragma: no cover
        pytest.skip(f"git недоступен: {e}")
    assert re.fullmatch(r"[0-9a-f]{40}", commit), commit
