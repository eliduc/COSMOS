# -*- coding: utf-8 -*-
"""T15: писатель каталога шаблонов (`template_catalog/writer.py`, §2.7
проекта шага 5).

Гейт файла: отпечаток шапки сходится после любой записи (H02), отказ
проверки не оставляет на диске ни байта, две записи одного тела дают
файлы байт в байт (LF, без BOM), `catalog.json` заменяется последним,
замена ждёт чужого читателя по времени и сдаётся словами, производные
инварианты (`refresh_lexicon_order`, `refresh_families_order`, формула
L10) — одна формула с помощником копий и гейтом паритета, снимок выпуска
открывается читателем.

Живой `catalog/` репозитория тесты НЕ трогают: всё на tmp-копиях.
"""
import copy
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

import catalog_copies as cc
import template_catalog as tc
from template_catalog import writer
from template_catalog.export import dump_json

REPO = Path(__file__).resolve().parent.parent
LIVE = REPO / "catalog"


def _parts(cat) -> dict:
    """Части каталога в форме аргументов писателя."""
    body = cat.body()
    return {"families": body["families"], "criteria": body["criteria"],
            "dictionaries": body["dictionaries"], "requests": cat.requests(),
            "versions": cat.versions()}


def _order(folder: Path) -> list:
    p = folder / "transition" / "families_order.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def _head(cat, **kw) -> dict:
    body = cat.body()
    args = {"version": cat.version(), "released_by": "владелец",
            "released_at": "2026-09-08", "note": "проба писателя",
            "body": body,
            "legacy_registry_rev": writer.legacy_registry_rev(cat.families())}
    args.update(kw)
    return writer.build_header(cat.header, **args)


def _bytes(folder: Path) -> dict:
    return {str(p.relative_to(folder)): p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _write(folder: Path, cat, **kw):
    parts = _parts(cat)
    parts.update(kw.pop("parts", {}))
    head = kw.pop("header", None) or _head(cat)
    return writer.write_catalog(folder, header=head,
                                families_order=_order(folder), **parts, **kw)


# ------------------------------------------------- отпечаток и форма
def test_отпечаток_шапки_сходится_после_любой_записи(tmp_path, capsys):
    """H02: писатель считает отпечаток из тела и не берёт его из шапки —
    подсунутая чужая шапка исправляется, а не переносится."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    head = _head(cat)
    head["fingerprint"] = "0" * 64                       # заведомо чужой
    out = writer.write_catalog(folder, header=head, families_order=_order(folder),
                               **_parts(cat))
    assert out.fingerprint() == out.declared_fingerprint() == cat.fingerprint()
    assert [p for p in out.validate() if p.severity == "error"] == []
    print(f"\nT15 H02: отпечаток {out.fingerprint()[:12]}… сходится, "
          f"записей {len(out.families(status=None))} семей и "
          f"{len(out.criteria(status=None))} критериев")


def test_форма_файлов_lf_без_bom_и_ключи_по_алфавиту(tmp_path):
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    _write(folder, cat)
    written = [p for p in folder.rglob("*.json") if p.is_file()]
    assert written
    for p in written:
        raw = p.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"BOM в {p.name}"
        assert b"\r\n" not in raw, f"CRLF в {p.name}"
    doc = json.loads((folder / "families.json").read_text(encoding="utf-8"))
    assert list(doc) == sorted(doc), "ключи файла не по алфавиту"


def test_две_записи_одного_тела_дают_байт_в_байт(tmp_path):
    """Детерминизм: перестановка записей и списков-множеств на входе не
    меняет ни одного байта на выходе (пишется тело канона)."""
    a = cc.copy_catalog(tmp_path, name="a")
    b = cc.copy_catalog(tmp_path, name="b")
    cat = tc.load(a)
    parts = _parts(cat)
    _write(a, cat)
    shuffled = dict(parts)
    shuffled["families"] = list(reversed(parts["families"]))
    shuffled["criteria"] = list(reversed(parts["criteria"]))
    writer.write_catalog(b, header=_head(cat), families_order=_order(b),
                         **shuffled)
    for name in ("catalog.json", "families.json", "criteria.json",
                 "dictionaries.json", "requests.json", "versions.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_ошибка_проверки_не_оставляет_на_диске_ни_байта(tmp_path, capsys):
    """Суд до диска: негодное тело — CatalogWriteError с перечнем
    проблем, байты папки до и после равны."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    before = _bytes(folder)
    parts = _parts(cat)
    broken = json.loads(json.dumps(parts["families"], ensure_ascii=False))
    broken[0]["zone"] = "НЕТ_ТАКОЙ_ЗОНЫ"
    parts["families"] = broken
    with pytest.raises(writer.CatalogWriteError) as ei:
        writer.write_catalog(folder, header=_head(cat),
                             families_order=_order(folder), **parts)
    assert ei.value.problems, "отказ без перечня проблем"
    codes = {p.code for p in ei.value.problems if p.severity == "error"}
    assert "D01" in codes, codes
    assert _bytes(folder) == before, "писатель тронул диск при отказе"
    print(f"\nT15 отказ: проблем {len(ei.value.problems)}, коды "
          f"{sorted(codes)}, файлов не тронуто {len(before)}")


def test_три_поля_0_5_0_пишутся_и_читаются_а_ложный_свидетель_не_ложится(
        tmp_path, capsys):
    """Поля выпуска 0.5.0 проходят запись и чтение как есть, а запись с
    ложным свидетелем природы до диска не доходит: суд раньше записи."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    новый = _write(tmp_path / "вывод", cat)
    семья = новый.family("night_series")
    assert семья["reads_history"] == cat.family("night_series")["reads_history"]
    assert семья["nature_confirmed_by"] == "solver_tables"
    assert (новый.dictionaries()["violation_units"]
            == cat.dictionaries()["violation_units"])
    мета = новый.families_meta()
    print("\nТ0.5.0 писатель: чтений истории "
          f"{sum(1 for m in мета.values() if m['reads_history'])}"
          f", размеров нарушения "
          f"{sum(1 for m in мета.values() if m['violation_size'])}")

    parts = _parts(cat)
    испорчено = [dict(r, nature_confirmed_by="change_request")
                 if r["id"] == "night_series" else r
                 for r in parts["families"]]
    пусто = tmp_path / "отказ"
    with pytest.raises(writer.CatalogWriteError) as e:
        writer.write_catalog(пусто, header=_head(cat),
                             families_order=_order(folder),
                             **{**parts, "families": испорчено})
    assert any(p.code == "X17" for p in e.value.problems), \
        [str(p) for p in e.value.problems[:3]]
    assert not пусто.exists(), "на диске не должно остаться ни байта"


def test_нецелое_число_отказ_словами_с_адресом(tmp_path):
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    parts = _parts(cat)
    fams = json.loads(json.dumps(parts["families"], ensure_ascii=False))
    fams[0]["note"] = fams[0].get("note") or ""
    fams[0]["complements"] = fams[0].get("complements") or []
    fams[0]["stand_tasks"] = []
    fams[0]["semantics"] = dict(fams[0]["semantics"])
    fams[0]["semantics"]["дробь"] = 1.5
    parts["families"] = fams
    before = _bytes(folder)
    with pytest.raises(writer.CatalogWriteError) as ei:
        writer.write_catalog(folder, header=_head(cat),
                             families_order=_order(folder), **parts)
    assert "канон не строится" in str(ei.value)
    assert "families[" in str(ei.value)
    assert _bytes(folder) == before


def test_маркер_заказчика_подаётся_писателю(tmp_path):
    """forbidden_terms доходит до validate(): служба судит обезличенность
    тем же вызовом, что и запись."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    parts = _parts(cat)
    fams = json.loads(json.dumps(parts["families"], ensure_ascii=False))
    fams[0]["note"] = "правило перевозчика Полярис-Авиа"
    parts["families"] = fams
    with pytest.raises(writer.CatalogWriteError) as ei:
        writer.write_catalog(folder, header=_head(cat),
                             families_order=_order(folder),
                             forbidden_terms=("Полярис-Авиа",), **parts)
    assert any(p.code.startswith("A") for p in ei.value.problems), \
        [str(p) for p in ei.value.problems]


# ------------------------------------------------- порядок и замена
def test_шапка_заменяется_последней(tmp_path, monkeypatch, capsys):
    """Окно несогласованности смотрит в одну сторону: пока шапка старая,
    читатель видит прежнюю версию; её замена и есть момент, когда
    каталог стал новым."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    seen: list = []
    orig = writer.replace_json

    def spy(path, text, **kw):
        seen.append(Path(path).name)
        return orig(path, text, **kw)

    monkeypatch.setattr(writer, "replace_json", spy)
    _write(folder, cat)
    assert seen[-1] == "catalog.json", seen
    assert seen.count("catalog.json") == 1
    for name in ("families.json", "criteria.json", "dictionaries.json",
                 "requests.json", "versions.json", "families_order.json",
                 "catalog.schema.json"):
        assert name in seen, name
    print(f"\nT15 порядок замен ({len(seen)}): {' → '.join(seen)}")


def test_временные_файлы_не_подходят_под_образец_каталога(tmp_path):
    """Отметка кэша читателя судит только `*.json` верхнего уровня —
    временный файл писателя не обязан её двигать и не остаётся после."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    names: list = []
    orig_replace = writer.replace_file

    def spy(src, dst):
        names.append(Path(src).name)
        return orig_replace(src, dst)

    writer.replace_file = spy
    try:
        _write(folder, cat)
    finally:
        writer.replace_file = orig_replace
    assert names and all(n.endswith(f".tmp-{__import__('os').getpid()}")
                         for n in names), names
    assert not [p for p in folder.rglob("*") if ".tmp-" in p.name]


def test_замена_ждёт_чужого_читателя_и_сдаётся_словами(tmp_path, capsys):
    """Повторы отмеряются ПО ВРЕМЕНИ: читатель, отпустивший файл внутри
    окна, писателю не мешает; читатель, держащий дольше окна, даёт
    PermissionError словами — какой файл, сколько ждали, сколько попыток."""
    folder = cc.copy_catalog(tmp_path)
    target = folder / "families.json"
    text = target.read_text(encoding="utf-8")

    def hold(seconds: float, started: threading.Event,
             release: threading.Event):
        with open(target, "rb") as fh:
            fh.read(1)
            started.set()
            release.wait(seconds)

    started, release = threading.Event(), threading.Event()
    t = threading.Thread(target=hold, args=(0.4, started, release))
    t.start()
    started.wait(5)
    writer.replace_json(target, text, window_s=5.0)       # окно шире хвата
    release.set()
    t.join()
    assert target.read_text(encoding="utf-8") == text

    started2, release2 = threading.Event(), threading.Event()
    t2 = threading.Thread(target=hold, args=(3.0, started2, release2))
    t2.start()
    started2.wait(5)
    began = time.monotonic()
    try:
        writer.replace_json(target, text, window_s=0.4)   # окно уже хвата
        words = None
    except PermissionError as e:
        words = str(e)
    spent = time.monotonic() - began
    release2.set()
    t2.join()
    if words is None:
        pytest.skip("файловая система пускает замену под открытым "
                    "читателем — правило окна проверять не на чем")
    assert "families.json" in words and "попыток" in words and "с (" in words
    assert spent < 3.0, "писатель ждал дольше окна"
    assert not [p for p in folder.rglob("*") if ".tmp-" in p.name]
    print(f"\nT15 окно замены: сдался за {spent:.2f} с словами — {words[:120]}")


# ----------------------------------------- производные инварианты
def test_refresh_lexicon_order_равен_прежнему_правилу_копий(tmp_path):
    """Правило D02 одно на помощника копий и на сборку службы: прежняя
    формула помощника (до шага 5) и функция писателя дают одно и то же."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    fams = sorted(cat.families_full(status=None), key=lambda r: str(r["id"]))
    order = list(cat.dictionaries().get("lexicon_order") or [])
    with_words = [r["id"] for r in fams if r.get("status") == "есть"
                  and isinstance(r.get("subject_words"), list)]
    old_rule = ([f for f in order if f in with_words]
                + [f for f in with_words if f not in order])
    assert writer.refresh_lexicon_order(order, fams) == old_rule
    # перестановка входа и лишние имена прежнего порядка
    assert writer.refresh_lexicon_order(["нет_такой"] + order, fams) == old_rule
    assert writer.refresh_lexicon_order([], fams) == sorted(with_words)
    assert writer.refresh_lexicon_order(order + order, fams) == old_rule


def test_refresh_families_order_держит_равенство_множеств(tmp_path):
    """Дверь COSMOS требует равенства множеств переходного порядка и
    семей «есть»: снятая уходит, новая дописывается в конец по id."""
    folder = cc.copy_catalog(tmp_path)
    order = _order(folder)
    assert order
    present = [f for f in order if f != order[0]] + ["новая_семья", "аaa_семья"]
    out = writer.refresh_families_order(order, present)
    assert set(out) == set(present)
    assert out[:len(order) - 1] == order[1:]
    assert out[len(order) - 1:] == sorted(["новая_семья", "аaa_семья"])


def test_legacy_registry_rev_формула_гейта_l10(tmp_path):
    import hashlib
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    fam_map = cat.families()
    expect = hashlib.sha256(
        json.dumps(fam_map, ensure_ascii=False,
                   sort_keys=True).encode("utf-8")).hexdigest()[:12]
    assert writer.legacy_registry_rev(fam_map) == expect
    assert len(expect) == 12


def test_refresh_body_даёт_те_же_инварианты_что_сборка_службы(tmp_path,
                                                              capsys):
    """Решение 0.8 пакета паритета: симметрия дополнений (X01), уход
    связанного имени из `unbound_params` (D03) и порядок лексикона (D02)
    переехали из `catalog_service/store.py` в писателя — и служба, и
    временный каталог кандидата зовут ОДНУ функцию.

    Свидетель — сама сборка службы: тело, собранное `store.
    assemble_in_memory` из черновика, обязано совпасть с телом, которое
    даст `writer.refresh_body` на тех же записях. Разошлись бы — у
    кандидата и у службы стало бы два закона на один переход."""
    from catalog_service import store as srv

    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    пара = next(r for r in cat.families_full() if r.get("complements"))
    имя = sorted(cat.dictionaries()["unbound_params"])[0]
    свободное = cat.dictionaries()["unbound_params"][имя]
    образец = copy.deepcopy(next(r for r in cat.families_full()
                                 if r.get("params")))
    новая = copy.deepcopy(образец)
    новая.pop("history", None)
    новая.update({
        "id": "writer_probe",
        "status": "есть", "subject_words": None, "rule_form": None,
        "param_sets": [], "conditions": [],
        "complements": [пара["id"]],
        "params": [{"name": имя, "unit": свободное["unit"],
                    "relation": свободное.get("relation") or "max",
                    "qualifier": None,
                    "note": "имя связано записью — уходит из словаря"}]})

    # левая сторона: писатель
    families = [copy.deepcopy(r) for r in cat.families_full(status=None)]
    families.append(copy.deepcopy(новая))
    dicts = copy.deepcopy(cat.body()["dictionaries"])
    было = {str(r.get("id")): list(r.get("complements") or [])
            for r in cat.families_full(status=None)}
    слова = writer.refresh_body(families, dicts, before=было)

    # правая сторона: сборка службы из того же черновика
    draft = srv.draft_doc("families", новая["id"], record=copy.deepcopy(новая),
                          note="проба сборки", by="владелец")
    cand = srv.assemble_in_memory(cat, [draft], actor="владелец")

    def тела(fams, d):
        return {"families": [{k: v for k, v in r.items() if k != "history"}
                             for r in sorted(fams,
                                             key=lambda r: str(r["id"]))],
                "dictionaries": d}

    assert тела(families, dicts) == тела(cand.families, cand.dictionaries)
    assert set(слова) == set(cand.derived) - {
        s for s in cand.derived if s.startswith("transition/")}
    # и по существу: имя ушло из словаря, дополнение симметрично
    assert имя not in dicts["unbound_params"]
    сосед = next(r for r in families if r["id"] == пара["id"])
    assert новая["id"] in сосед["complements"]
    assert any("unbound_params" in s for s in слова)
    assert any("дополнение симметрично" in s for s in слова)
    print(f"\nT: производных слов {len(слова)} — {'; '.join(слова)[:160]}")


def test_refresh_complements_снятие_не_симметризуется(tmp_path):
    """Пара «дополняют друг друга» — решение о ДВУХ записях: писатель
    дописывает добавленное, но одностороннего убора за вторую сторону не
    решает (честный X01 по её адресу), а запись «снята» уходит у всех."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    a = next(r for r in cat.families_full() if r.get("complements"))
    b_id = a["complements"][0]
    families = [copy.deepcopy(r) for r in cat.families_full(status=None)]
    было = {str(r.get("id")): list(r.get("complements") or [])
            for r in families}
    ушёл = next(r for r in families if r["id"] == a["id"])
    ушёл["complements"] = []
    слова = writer.refresh_complements(families, before=было)
    сосед = next(r for r in families if r["id"] == b_id)
    assert a["id"] in сосед["complements"], "снятие не симметризуется"
    assert слова == []
    # «снята» — уходит у всех: её больше нет в живом составе
    families = [copy.deepcopy(r) for r in cat.families_full(status=None)]
    снятая = next(r for r in families if r["id"] == b_id)
    снятая["status"] = "снята"
    слова = writer.refresh_complements(families, before=было)
    остался = next(r for r in families if r["id"] == a["id"])
    assert b_id not in остался["complements"]
    assert any(b_id in s for s in слова)


def test_build_header_копирует_базу_и_не_мутирует_её(tmp_path):
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    base = cat.header
    snapshot = json.dumps(base, ensure_ascii=False, sort_keys=True)
    head = writer.build_header(base, version="0.9.0", released_by="владелец",
                               released_at="2026-09-09", note="проба",
                               body=cat.body(), legacy_registry_rev=None)
    assert head["version"] == "0.9.0" and head["released_by"] == "владелец"
    assert head["legacy_registry_rev"] is None
    assert head["fingerprint"] == cat.fingerprint()
    assert head["sections"] == base["sections"] and head["name"] == base["name"]
    assert json.dumps(base, ensure_ascii=False, sort_keys=True) == snapshot


def test_write_requests_не_трогает_отпечаток(tmp_path):
    """Заявки в отпечаток не входят: файл заявок заменён, шапка и
    отпечаток каталога прежние."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    head_before = (folder / "catalog.json").read_bytes()
    writer.write_requests(folder, [])
    assert (folder / "catalog.json").read_bytes() == head_before
    again = tc.load(folder)
    assert again.fingerprint() == cat.fingerprint()
    assert again.requests() == []
    with pytest.raises(writer.CatalogWriteError):
        writer.write_requests(folder, {"не": "список"})


# ------------------------------------------------- снимок выпуска
def test_snapshot_release_открывается_читателем(tmp_path, capsys):
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    _write(folder, cat, header=_head(cat, version="0.9.0"))
    dst = writer.snapshot_release(folder, "0.9.0")
    assert dst == folder / "releases" / "0.9.0" and dst.is_dir()
    snap = tc.load(dst)
    assert snap.version() == "0.9.0"
    assert snap.fingerprint() == tc.load(folder).fingerprint()
    assert [p for p in snap.validate() if p.severity == "error"] == []
    assert (dst / "schema" / "catalog.schema.json").is_file()
    assert (dst / "transition" / "families_order.json").is_file()
    with pytest.raises(writer.CatalogWriteError) as ei:
        writer.snapshot_release(folder, "0.9.0")
    assert "неизменяемы" in str(ei.value)
    assert not [p for p in (folder / "releases").iterdir()
                if ".tmp-" in p.name]
    print(f"\nT15 снимок выпуска: файлов "
          f"{len([p for p in dst.rglob('*') if p.is_file()])}, версия "
          f"{snap.version()}, отпечаток {snap.fingerprint()[:12]}…")


def test_снимок_не_делается_при_нехватке_файла(tmp_path):
    folder = cc.copy_catalog(tmp_path)
    (folder / "versions.json").unlink()
    with pytest.raises(writer.CatalogWriteError) as ei:
        writer.snapshot_release(folder, "0.9.1")
    assert "versions.json" in str(ei.value)
    assert not (folder / "releases" / "0.9.1").exists()
    остатки = list((folder / "releases").iterdir()) \
        if (folder / "releases").is_dir() else []
    assert остатки == [], f"недоделанный снимок остался: {остатки}"


# ------------------------------------- гарантии пакета читателя
def test_импорт_писателя_и_диффа_ничего_не_читает(capsys):
    """Гарантия пакета (образец `test_template_catalog_reader.py`): при
    импорте ни файла, ни переменной среды, ни продуктов в sys.modules.
    Гейт независимости импорт-графа накрывает `template_catalog/*.py`
    сам — здесь проверяются побочные действия импорта."""
    import subprocess
    import sys
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
import template_catalog.writer, template_catalog.diff
for name in ("jsonschema", "cdpm", "vmp", "webapp", "tools"):
    assert name not in sys.modules, name
print("ok")
"""
    res = subprocess.run([sys.executable, "-c", script, str(REPO)],
                         capture_output=True, text=True, encoding="utf-8",
                         timeout=120,
                         env={"PYTHONUTF8": "1", "SYSTEMROOT": "C:\\Windows",
                              "PATH": ""})
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok" in res.stdout
    print("\nT15 импорт писателя и диффа: файлов не читано, среда не "
          "тронута, продуктов в sys.modules нет")


# ------------------------------------------------- живой каталог цел
def test_живой_каталог_репозитория_не_тронут(tmp_path):
    """Страж набора: помощник копий и писатель работают только в tmp."""
    before = {p.name: p.read_bytes() for p in LIVE.glob("*.json")}
    выпуски = sorted(p.name for p in (LIVE / "releases").iterdir()) \
        if (LIVE / "releases").is_dir() else None
    folder = cc.copy_catalog(tmp_path)
    _write(folder, tc.load(folder))
    assert {p.name: p.read_bytes() for p in LIVE.glob("*.json")} == before
    # Папки выпусков живого каталога тест не заводит и не трогает: с
    # 08.09 там лежит выпуск 0.2.0, и сравнивается перечень, а не «нет».
    после = sorted(p.name for p in (LIVE / "releases").iterdir()) \
        if (LIVE / "releases").is_dir() else None
    assert после == выпуски, "тест тронул папки выпусков живого каталога"
    cc.rmtree_writable(folder)


def test_форма_дампа_совпадает_с_формой_читателя(tmp_path):
    """Писатель и выгрузка читателя пишут одной функцией: отступ 1,
    ключи по алфавиту, LF в конце."""
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    _write(folder, cat)
    doc = json.loads((folder / "versions.json").read_text(encoding="utf-8"))
    assert (folder / "versions.json").read_bytes() == \
        dump_json(doc).encode("utf-8")
