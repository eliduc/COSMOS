# -*- coding: utf-8 -*-
"""T19 + Р3: защита инструмента извлечения и судьба заявок
(`catalog/tools/build_from_code.py`, §2.12 шага 5 и решение 0.11 шага 6).

Инструмент писал `requests.json` и `versions.json` ВСЕГДА пустыми: он
собирает каталог из кода, а журнала версий и заявок в коде нет. Пока
каталог вёл инструмент, это было безобидно; со шага 5 каталог ведёт
служба, и первая же пересборка поверх живой папки стёрла бы журнал
выпусков и очередь заявок, осиротив папки `releases/`.

Два рубежа, и оба нужны:

  * ГЕЙТ (T19): запись в папку, где каталог ведёт служба, отказывает
    СЛОВАМИ и кодом 3, ни одного байта не трогая; `--check` на той же
    папке работает; ключа принуждения нет; на чистой папке инструмент
    по-прежнему пишет (гейт L18 остаётся зелёным).
  * ЗАЯВКИ (Р3, дефект К9): инструмент их больше НЕ ПОРОЖДАЕТ и не
    стирает — читает с диска, судит форму тем же validate(), в сравнение
    `--check` не берёт, а пустую очередь заводит только на папке, где её
    нет. Без этого первая принятая заявка красила бы гейт L18, а
    следующая сборка её стирала бы — гейт остался бы зелёным ценой
    потери заявки.
"""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

import catalog_copies as cc

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "catalog"


def _tool():
    spec = importlib.util.spec_from_file_location(
        "build_from_code_guard", CATALOG / "tools" / "build_from_code.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args() -> list:
    """Аргументы — из шапки живого каталога, не из литералов теста."""
    h = json.loads((CATALOG / "catalog.json").read_text(encoding="utf-8"))
    return ["--version", h["version"], "--released-at", h["released_at"],
            "--released-by", h["released_by"], "--note", h["note"]]


def _bytes(folder: Path) -> dict:
    return {str(p.relative_to(folder)): p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _рост_под_кандидатом(mod) -> None:
    """Пропуск СЛОВАМИ, когда инструмент не собирает каталог из-за
    кандидата ветки.

    Инструмент строит каталог из ЗАМОРОЖЕННОГО посева
    (`catalog/tools/catalog_seed.json`) ⊕ код. Пока запись живёт
    кандидатом `catalog/candidates/**`, посев её не называет — владелец
    её ещё не принял, — а таблицы решателя ветки уже знают, и инструмент
    отказывает ПО ДЕЛУ: «критерии-метрики посева ≠ словарю метрик
    решателя». Предмет этих стендов другой — защита папки, которую ведёт
    служба, и судьба заявок; рост таблиц под кандидатом судит закон
    паритета на временном каталоге «живой ⊕ кандидаты» (решение 0.3
    проекта паритета). Условие двойное: кандидат рядом ЕСТЬ и инструмент
    ОТКАЗАЛ. На чистом дереве не пропускается ничего."""
    if not cc.tree_candidate_docs():
        return
    try:
        # `BuildError` наследует SystemExit (инструмент — программа
        # командной строки), поэтому ловится он, а не только Exception.
        mod.build("0.0.1-draft.1", "2026-09-08", "сессия COSMOS", "проба")
    except (Exception, SystemExit) as e:                 # noqa: BLE001
        pytest.skip(f"рост таблиц под кандидатом — судит закон паритета "
                    f"на временном каталоге: {e}")


@pytest.fixture()
def копия(tmp_path) -> Path:
    return cc.copy_catalog(tmp_path)


def _завести_выпуск(folder: Path) -> None:
    """Папка, которую ведёт служба: непустой журнал версий и папка
    выпуска рядом."""
    doc = json.loads((folder / "versions.json").read_text(encoding="utf-8"))
    head = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
    doc["versions"] = [{"version": "0.3.0", "fingerprint": head["fingerprint"],
                        "released_at": "2026-09-08",
                        "released_by": "владелец", "code": "a" * 40,
                        "changes": []}]
    (folder / "versions.json").write_bytes(
        json.dumps(doc, ensure_ascii=False, sort_keys=True,
                   indent=1).encode("utf-8") + b"\n")


def test_запись_поверх_журнала_версий_отказ_словами_и_кодом_3(копия, capsys):
    mod = _tool()
    _завести_выпуск(копия)
    before = _bytes(копия)
    rc = mod.main(["--out", str(копия), *_args()])
    out = capsys.readouterr().out
    assert rc == mod.GUARD_RC == 3, out
    assert "versions.json" in out and "не сделана" in out
    assert "служба каталога" in out and "--check" in out
    assert _bytes(копия) == before, "инструмент тронул папку при отказе"


def test_запись_поверх_очереди_заявок_тоже_отказ(копия, capsys):
    """Проект называет причиной защиты и журнал, И ЗАЯВКИ: инструмент
    пишет `requests.json` пустым так же, как `versions.json`."""
    mod = _tool()
    doc = json.loads((копия / "requests.json").read_text(encoding="utf-8"))
    doc["requests"] = [{"request_id": "req-0001", "state": "подана",
                        "section": "families", "record_id": "duty_limits",
                        "submitted_at": "2026-09-08T10:00:00+03:00",
                        "submitted_by": "аналитик",
                        "source": {"kind": "разбор", "document_class": "свод",
                                   "run_class": "нано", "ref": None},
                        "draft": None, "executor_kinds_requested": None,
                        "decision": {}, "cr_key": None}]
    (копия / "requests.json").write_bytes(
        json.dumps(doc, ensure_ascii=False, sort_keys=True,
                   indent=1).encode("utf-8") + b"\n")
    before = _bytes(копия)
    rc = mod.main(["--out", str(копия), *_args()])
    out = capsys.readouterr().out
    assert rc == 3 and "requests.json" in out, out
    assert _bytes(копия) == before


def test_папка_выпусков_рядом_тоже_отказ(копия, capsys):
    mod = _tool()
    (копия / "releases" / "0.3.0").mkdir(parents=True)
    before = _bytes(копия)
    rc = mod.main(["--out", str(копия), *_args()])
    out = capsys.readouterr().out
    assert rc == 3 and "releases/" in out, out
    assert _bytes(копия) == before


def test_нечитаемый_журнал_считается_непустым(копия, capsys):
    """Fail-closed: доказать, что в испорченном файле пусто, нечем."""
    mod = _tool()
    (копия / "versions.json").write_bytes("{ это не JSON".encode("utf-8"))
    before = _bytes(копия)
    rc = mod.main(["--out", str(копия), *_args()])
    out = capsys.readouterr().out
    assert rc == 3 and "не читается" in out, out
    assert _bytes(копия) == before


def test_check_на_той_же_папке_работает(копия, capsys):
    """Сверка — не запись: защита её не касается."""
    mod = _tool()
    _рост_под_кандидатом(mod)
    _завести_выпуск(копия)
    before = _bytes(копия)
    rc = mod.main(["--out", str(копия), "--check", *_args()])
    out = capsys.readouterr().out
    assert rc in (0, 1), out
    assert "проверка" in out and "расхождений" in out
    assert _bytes(копия) == before, "--check тронул папку"


def test_на_чистой_папке_инструмент_пишет(tmp_path, capsys):
    """Защита не запирает законный путь: `--out <временная папка>`
    работает, и гейт L18 остаётся зелёным."""
    mod = _tool()
    _рост_под_кандидатом(mod)
    out_dir = tmp_path / "свежая"
    rc = mod.main(["--out", str(out_dir), *_args()])
    печать = capsys.readouterr().out
    assert rc == 0, печать
    assert (out_dir / "catalog.json").is_file()
    assert json.loads((out_dir / "versions.json")
                      .read_text(encoding="utf-8"))["versions"] == []
    cc.rmtree_writable(out_dir)


def test_ключа_принуждения_нет(capsys):
    """Спасённый журнал важнее удобства пересборки: обойти защиту
    ключом нельзя."""
    mod = _tool()
    src = (CATALOG / "tools" / "build_from_code.py").read_text(
        encoding="utf-8")
    for слово in ("--force", "--allow-overwrite", "CATALOG_FORCE"):
        assert слово not in src, f"в инструменте завёлся обход: {слово}"
    assert "GUARD_RC = 3" in src and "def refuse_write" in src
    # `--refresh` пакета паритета обходом не является: он не пишет ни
    # одного раздела отпечатка, журнала и заявок — только посев и дельту.
    assert "def refuse_refresh" in src


def test_живой_каталог_защиту_включает_с_выпуска_0_2_0(capsys):
    """С выпуска 0.2.0 (08.09, слово владельца) живой каталог ведёт
    служба: журнал версий непуст и есть папка releases/ — инструмент
    отказывается писать поверх словами и кодом 3, а сверка `--check`
    остаётся зелёной (L18). Сам вызов записи здесь не делается: живой
    каталог тесты не трогают — судится только слово отказа."""
    mod = _tool()
    журнал = json.loads((CATALOG / "versions.json")
                        .read_text(encoding="utf-8"))["versions"]
    заявки = json.loads((CATALOG / "requests.json")
                        .read_text(encoding="utf-8"))["requests"]
    words = mod.refuse_write(CATALOG)
    assert журнал, "журнал версий живого каталога пуст — выпуска нет"
    assert (CATALOG / "releases").is_dir()
    assert words and "releases/" in words, words
    assert "--check" in words and "--out" in words, words
    print(f"\nT19 живой каталог: записей журнала {len(журнал)}, заявок "
          f"{len(заявки)}, папка releases/ есть — защита отказывает "
          f"словами и кодом {mod.GUARD_RC}")


# ═══════════════════════════════════ Р3: заявки инструмент не трогает

def _заявка(папка: Path) -> dict:
    """Заявка на вид исполнителя по живой записи копии — по закону
    ключа читателя (номер и ключ CR считаются, а не выдумываются)."""
    import template_catalog as tc
    from template_catalog import requests as rq
    cat = tc.load(папка)
    вид = sorted(cat.family("duty_limits")["executor_kinds"])[0]
    key = rq.kind_request_key("families", "duty_limits", [вид])
    rid = rq.request_id_of(key)
    класс = (cat.dictionaries().get("document_classes")
             or list(rq.DOCUMENT_CLASSES))[0]
    return {"request_id": rid, "state": "подана", "section": "families",
            "record_id": "duty_limits",
            "submitted_at": "2026-09-12T10:15:00+03:00",
            "submitted_by": "COSMOS",
            "source": {"kind": "пробел при нажатии", "document_class": класс,
                       "run_class": "прогон COSMOS",
                       "ref": rq.ref_of("run-0031", key)},
            "draft": None, "executor_kinds_requested": [вид],
            "decision": {"by": None, "at": None, "words": None},
            "cr_key": None}


def _записать_заявки(папка: Path, заявки: list) -> list:
    """Очередь копии: заявки стенда ⊕ заявки-свидетели кандидатов дерева
    (помощник копий). На ветке правки с кандидатом новой записи очередь
    из одной своей заявки стирала свидетеля единиц его имён, и сборка
    отказывала «единицу параметра … не подтверждает … ни заявка» по
    построению (09.09.2026, CR duty_series_limit). Возвращает
    записанный список — по нему считается ожидаемое «заявок N»."""
    return cc.write_requests(папка, заявки)


def test_check_на_копии_с_заявкой_расхождений_ноль(tmp_path, capsys):
    """Дефект К9: заявка в папке — не расхождение. `--check` её не
    сравнивает, но СЧИТАЕТ и судит форму.

    Сверка идёт `--from-catalog`: она судит папку из ЕЁ ЖЕ курируемых
    полей ⊕ код. Сверка от посева здесь не годится — посев описывает
    живой каталог дерева, и на ветке класса «рост таблиц решателя» она
    красна не из-за заявки, а из-за выросшего решателя (новая ручка
    ложится в несвязанные имена)."""
    mod = _tool()
    копия = cc.copy_catalog(tmp_path)
    заявка = _заявка(копия)
    очередь = _записать_заявки(копия, [заявка])
    before = _bytes(копия)
    rc = mod.main(["--out", str(копия), "--check", "--from-catalog", *_args()])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "расхождений 0" in out and f"заявок {len(очередь)}" in out
    assert _bytes(копия) == before, "--check тронул папку"
    print(f"\nР3: заявка {заявка['request_id']} в копии — «расхождений 0»")


def test_форма_заявки_судится_тем_же_validate(копия, capsys):
    """«requests.json — только на форму»: негодная заявка останавливает
    сборку кодом 2, а не проходит молча."""
    mod = _tool()
    _рост_под_кандидатом(mod)
    заявка = _заявка(копия)
    заявка["state"] = "выдуманное состояние"
    _записать_заявки(копия, [заявка])
    rc = mod.main(["--out", str(копия), "--check", *_args()])
    out = capsys.readouterr().out
    assert rc == 2, out
    assert "каталог не собран" in out and "request_states" in out


def test_запись_в_свежую_папку_заводит_пустую_очередь(tmp_path, capsys):
    """Чистая папка обязана стать ПОЛНЫМ каталогом: читателю нужны шесть
    файлов, и пустая очередь заводится ровно один раз."""
    import template_catalog as tc
    mod = _tool()
    _рост_под_кандидатом(mod)
    out_dir = tmp_path / "свежая"
    assert mod.main(["--out", str(out_dir), *_args()]) == 0
    печать = capsys.readouterr().out
    assert "заведена пустая очередь заявок" in печать
    assert json.loads((out_dir / "requests.json")
                      .read_text(encoding="utf-8"))["requests"] == []
    assert tc.load(out_dir).version()
    # Заявка легла в свежую папку — повторная запись её НЕ стирает
    # (первым отказывает гейт §2.12: очередь непуста).
    _записать_заявки(out_dir, [_заявка(out_dir)])
    было = (out_dir / "requests.json").read_bytes()
    rc = mod.main(["--out", str(out_dir), *_args()])
    печать = capsys.readouterr().out
    assert rc == mod.GUARD_RC, печать
    assert (out_dir / "requests.json").read_bytes() == было
    cc.rmtree_writable(out_dir)


def test_нечитаемая_очередь_названа_расхождением(копия, capsys):
    """`--check` не молчит о негодном файле заявок: он его не
    сравнивает, но и годным не объявляет."""
    mod = _tool()
    _рост_под_кандидатом(mod)
    (копия / "requests.json").write_bytes("{ это не JSON".encode("utf-8"))
    before = _bytes(копия)
    rc = mod.main(["--out", str(копия), "--check", *_args()])
    out = capsys.readouterr().out
    assert rc == 1 and "не читается" in out, out
    assert _bytes(копия) == before


def test_инструмент_не_порождает_requests_json():
    """Сторож замысла: в наборе файлов сборки заявок нет вовсе."""
    mod = _tool()
    _рост_под_кандидатом(mod)
    files = mod.build("0.0.1-draft.1", "2026-09-08", "сессия COSMOS", "проба")
    assert "requests.json" not in files
    assert set(files) >= {"catalog.json", "families.json", "criteria.json",
                          "dictionaries.json", "versions.json"}


def test_копия_схемы_равна_схеме_читателя(capsys):
    """Схема рядом с данными — копия схемы читателя байт в байт (перевод
    строки в сравнение не входит: git с autocrlf вправе её
    перематериализовать); словарь классов документа в ней —
    НЕОБЯЗАТЕЛЬНОЕ свойство (решение 0.14)."""
    def _lf(p: Path) -> bytes:
        return p.read_bytes().replace(b"\r\n", b"\n")
    копия_схемы = CATALOG / "schema" / "catalog.schema.json"
    источник = REPO / "template_catalog" / "schemas" / "catalog.schema.json"
    assert _lf(копия_схемы) == _lf(источник)
    doc = json.loads(источник.read_text(encoding="utf-8"))
    свойства = doc["$defs"]["dictionaries_file"]["properties"]
    обязательные = doc["$defs"]["dictionaries_file"]["required"]
    assert "document_classes" in свойства
    assert "document_classes" not in обязательные, \
        "словарь классов документа обязательным быть не может: он " \
        "появляется первой сборкой службой"
    print(f"\nР3: схема читателя и копия рядом с данными совпали; "
          f"необязательных свойств словарей "
          f"{len(set(свойства) - set(обязательные))}")


# ─────────── Паритет (08.09.2026): посев как проекция каталога ────────

def test_refresh_пишет_только_посев_и_дельту(tmp_path, capsys):
    """`--refresh` освежает ДВА носителя и не трогает ничего больше:
    шесть файлов, отпечаток, журнал и заявки остаются байт в байт."""
    mod = _tool()
    folder = cc.copy_catalog(
        tmp_path, releases=True,
        families={"duty_limits": {"note": "заметка стенда посева"}})
    shutil.copytree(CATALOG / "tools", folder / "tools")
    до = _bytes(folder)
    assert mod.main(["--out", str(folder), "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "освежены носители" in out, out
    после = _bytes(folder)
    менялись = sorted(Path(k).as_posix() for k in после
                      if после[k] != до.get(k))
    print("правились файлы: " + ", ".join(менялись))
    assert менялись == sorted([mod.SEED_REL, mod.DELTA_REL])
    assert sorted(после) == sorted(до), "инструмент завёл или снёс файл"


def test_refresh_под_releases_отказ_словами(tmp_path, capsys):
    """Папка выпуска неизменяема: посев ей не пишется, код 3 словами."""
    mod = _tool()
    folder = cc.copy_catalog(tmp_path, name="releases")
    (folder / "tools").mkdir(exist_ok=True)
    rc = mod.main(["--out", str(folder), "--refresh"])
    out = capsys.readouterr().out
    print(out)
    assert rc == mod.GUARD_RC
    assert "папка выпуска неизменяема" in out


def test_refresh_на_папке_без_tools_отказ_словами(копия, capsys):
    """Копия без `tools/` посева не ведёт — её судит `--from-catalog`."""
    mod = _tool()
    rc = mod.main(["--out", str(копия), "--refresh"])
    out = capsys.readouterr().out
    print(out)
    assert rc == mod.GUARD_RC and "нет tools/" in out


def _судимая_папка_дерева(tmp_path) -> Path:
    """Судимая папка дерева: живой каталог, а если рядом лежат кандидаты
    конвейера (`catalog/candidates/**`) — «живой ⊕ кандидаты» с копией
    `tools/`.

    Так же судит дерево свидетель закона паритета (0.3 проекта). На
    ветке класса «рост таблиц решателя» новая ручка объявлена записью
    КАНДИДАТА: живой каталог в это время законно не воспроизводится из
    своих курируемых полей ⊕ выросший код, и `--check --from-catalog` на
    нём краснел бы по построению — а с ним `pytest -q` клона и каждый
    такой CR."""
    from template_catalog import candidates as tcc
    root = CATALOG / "candidates"
    файлы = (sorted(p for p in root.rglob("*.json") if p.is_file())
             if root.is_dir() else [])
    if not файлы:
        return CATALOG
    cur = CATALOG
    for i, p in enumerate(файлы):
        cur = tcc.materialize(cur, tcc.load_candidate(p),
                              Path(tmp_path) / f"cat{i}")
    shutil.copytree(CATALOG / "tools", cur / "tools")
    print(f"судимая папка: живой ⊕ кандидаты ({len(файлы)})")
    return cur


def test_from_catalog_воспроизводит_живой_каталог(tmp_path, capsys):
    """Закон L18: судимый каталог дерева воспроизводится из СВОИХ
    курируемых полей ⊕ код, а жёсткие блоки посева проекция не трогает."""
    mod = _tool()
    папка = _судимая_папка_дерева(tmp_path)
    rc = mod.main(["--out", str(папка), "--check", "--from-catalog"])
    out = capsys.readouterr().out
    print(out)
    assert rc == 0 and "расхождений 0" in out
    было = mod.seed_of(папка)
    стало = json.loads(mod.refreshed(папка)[0])

    def канон(doc):
        return json.dumps(doc, sort_keys=True, ensure_ascii=False)

    for ключ in mod.HARD_SEED_KEYS:
        assert канон(было.get(ключ)) == канон(стало.get(ключ)), \
            f"жёсткий блок {ключ} переписан проекцией"
    for ключ in mod.HARD_CDPM_KEYS:
        assert канон(было["cdpm_dictionaries"][ключ]) == \
            канон(стало["cdpm_dictionaries"][ключ]), \
            f"жёсткий словарь CDPM {ключ} переписан проекцией"
    for ключ in mod.PROJECTED_CDPM_KEYS:
        assert ключ in стало["cdpm_dictionaries"], ключ


def test_блок_base_равен_каталогу_кода_заморозки(capsys):
    """Постоянный свидетель жёсткого блока `base`: перечни семей,
    критериев, видов исполнителей, форм миров и синтетических кодов равны
    КАТАЛОГУ КОДА ЗАМОРОЗКИ (`git show <base.code>:catalog/*.json`).

    Сравнение посева с самим собой (прежний сторож `--refresh`) ничего не
    стерегло: `curated_of` копирует жёсткие блоки из того же файла, с
    которым шло сравнение. Свидетелем блока `base` может быть только
    другой носитель — коммит заморозки."""
    import subprocess
    mod = _tool()
    base = mod.load_seed()["base"]
    code = str(base["code"])

    def показать(имя: str):
        p = subprocess.run(["git", "show", f"{code}:catalog/{имя}"],
                           cwd=str(REPO), capture_output=True,
                           encoding="utf-8", errors="replace")
        return json.loads(p.stdout) if p.returncode == 0 else None

    families = показать("families.json")
    dicts = показать("dictionaries.json")
    criteria = показать("criteria.json")
    if families is None or dicts is None or criteria is None:
        pytest.skip(f"каталога коммита заморозки {code} рядом нет — "
                    f"свидетель блока base недоступен")
    записи = {r["id"]: r for r in families["families"]}
    assert sorted(base["families"]) == sorted(записи), \
        "перечень семей блока base разошёлся с каталогом кода заморозки"
    assert sorted(base["criteria"]) == \
        sorted(r["id"] for r in criteria["criteria"]), \
        "перечень критериев блока base разошёлся с кодом заморозки"
    виды = {fid: sorted(r.get("executor_kinds") or [])
            for fid, r in записи.items()}
    было = {fid: sorted(v) for fid, v in (base["executor_kinds"] or {}).items()}
    assert было == {fid: v for fid, v in виды.items() if v}, \
        "виды исполнителей блока base разошлись с кодом заморозки"
    assert sorted(base["world_forms"]) == sorted(dicts.get("world_forms") or {})
    assert sorted(base["synthetic_codes"]) == \
        sorted(dicts.get("synthetic_codes") or {})
    print(f"\nблок base ≡ каталогу коммита {code[:7]}: семей "
          f"{len(base['families'])} | критериев {len(base['criteria'])} | "
          f"видов {len(было)} | форм миров {len(base['world_forms'])}")


def test_свой_словарь_службы_переживает_refresh(tmp_path, capsys):
    """Контракт §4: служба вправе завести свой словарь, и пересборка его
    не потеряет. Проекция переносит незнакомые инструменту ключи
    насквозь (`dictionaries_extra`), иначе первая же сборка словаря
    службой краснила бы `--check --from-catalog` и отказывала `--refresh`
    — красным по построению."""
    mod = _tool()
    # очередь заявок копии пуста: живая заявка (с 09.09.2026) ссылается
    # на класс документа, которого в «своём словаре службы» ниже нет
    folder = cc.copy_catalog(tmp_path, requests=False)
    shutil.copytree(CATALOG / "tools", folder / "tools")
    import template_catalog as tc
    from template_catalog.export import dump_json
    d = json.loads((folder / "dictionaries.json").read_text(encoding="utf-8"))
    # канон читателя ⊕ классы документов заявок-свидетелей кандидатов
    # дерева (на чистом дереве прибавка пуста): свой словарь службы не
    # вправе отвергать заявку, которая уже лежит в очереди копии, —
    # иначе validate() красен «значение вне словаря document_classes»
    # по построению стенда (ветка CR duty_series_limit, 09.09.2026);
    # список словаря сборка пишет отсортированным — канон тоже
    канон = sorted({"инструкция", "приказ"} | {
        str((rq.get("source") or {}).get("document_class"))
        for rq in cc.witness_requests(folder)})
    d["document_classes"] = канон
    (folder / "dictionaries.json").write_bytes(dump_json(d).encode("utf-8"))
    head = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
    head["fingerprint"] = tc.fingerprint_of(tc.load(folder).body())
    (folder / "catalog.json").write_bytes(dump_json(head).encode("utf-8"))

    посев = json.loads(mod.refreshed(folder)[0])
    assert посев["dictionaries_extra"].get("document_classes") == \
        канон, "чужой словарь потерян проекцией"
    assert mod.main(["--out", str(folder), "--refresh"]) == 0
    rc = mod.main(["--out", str(folder), "--check", "--from-catalog"])
    out = capsys.readouterr().out
    print(out)
    assert rc == 0 and "расхождений 0" in out


def test_refresh_check_свеж_на_дереве(capsys):
    """Носители дерева свежи: посев ≡ проекции, дельта ≡ пересчёту, и её
    отпечаток равен отпечатку каталога (правило L23)."""
    mod = _tool()
    rc = mod.main(["--out", str(CATALOG), "--refresh", "--check"])
    out = capsys.readouterr().out
    print(out)
    assert rc == 0 and "расхождений 0" in out
    delta = json.loads((CATALOG / "transition" / "step4_additions.json")
                       .read_text(encoding="utf-8"))
    head = json.loads((CATALOG / "catalog.json").read_text(encoding="utf-8"))
    assert delta["fingerprint"] == head["fingerprint"]
    assert delta["version"] == head["version"]


def test_from_catalog_называет_поле_расхождения(tmp_path, capsys):
    """Курируемое поле, которое разошлось с кодом, называется файлом:
    копия с чужой природой семьи — расхождение, а не тихий зелёный."""
    mod = _tool()
    folder = cc.copy_catalog(tmp_path,
                             families={"duty_limits": {"nature": "people"}})
    rc = mod.main(["--out", str(folder), "--check", "--from-catalog"])
    out = capsys.readouterr().out
    print(out)
    assert rc != 0
    assert "duty_limits" in out or "families.json" in out


def test_from_catalog_без_ключа_check_отказ(capsys):
    """`--from-catalog` судит папку и ничего не пишет: без `--check` он
    незаконен — иначе он выглядел бы обходом защиты записи."""
    mod = _tool()
    rc = mod.main(["--out", str(CATALOG), "--from-catalog"])
    out = capsys.readouterr().out
    print(out)
    assert rc == 2 and "только с --check" in out


def test_корень_дерева_ищется_и_у_каталога_не_под_корнем(tmp_path):
    """Раскладка `<репозиторий>/data/catalog`, которую поддерживает
    служба каталога (`repo_of` через `git rev-parse --show-toplevel`), —
    законная: корень дерева ищет `repo_root`, а не «родитель папки
    каталога». Прежде инструмент и закон паритета клали в `sys.path`
    папку `data/`, и импорт `template_catalog` падал ещё до разбора
    аргументов — судья отвечал «не судился» ценой трассировки."""
    mod = _tool()
    корень = tmp_path / "repo"
    (корень / "template_catalog").mkdir(parents=True)
    (корень / "template_catalog" / "__init__.py").write_text(
        "", encoding="utf-8")
    папка = корень / "data" / "catalog"
    папка.mkdir(parents=True)
    assert mod.repo_root(папка) == корень, \
        "корень дерева не найден: каталог вправе лежать не прямо под ним"
    # Обычная раскладка дерева — прежним путём, без единого подпроцесса.
    assert mod.repo_root(CATALOG) == REPO
    print(f"\nкорень для {папка.name}: {корень.name}; для дерева: {REPO.name}")
