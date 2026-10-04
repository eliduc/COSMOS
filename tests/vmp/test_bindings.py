# -*- coding: utf-8 -*-
"""Гейт продукта: привязки COSMOS к записям каталога шаблонов (шаг 4
каталога, §2.2 проекта final-report/KATALOG-SHAG-4-PROEKT-CLD.md,
пункты 1–14).

Судятся файл `vmp/bindings.json`, генератор `vmp/bindings_build.py` и
гейт `vmp/bindings_gate.py`: шапка и её зона, структурные правила
читателя (B01–B05), полнота видов записей, «параметр записи ⇒
исполнитель-читатель», существование символов кода, кросс-свидетели
Kotlin, детерминизм и сверка `--check`, стендовые подтверждения, счёт
клеток и строк формулой (не литералом), «ничего не потеряно» против
описи шага 1 и строки обёртки.

Файл привязок с шага 4 ОБЯЗАТЕЛЕН: без него запись каталога не называет
исполняющего её кода, и пропуск здесь был бы молчанием. Привязки CDPM
(`cdpm/bindings.json`) необязательны — их отсутствие говорится словами.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import template_catalog as tc                                     # noqa: E402
from template_catalog.bindings import check_bindings, load_bindings  # noqa: E402
from vmp import bindings_build as bb                              # noqa: E402
from vmp import bindings_gate as bg                               # noqa: E402
from vmp import catalog_access as ca                              # noqa: E402
from vmp import kotlin_tables as kt                               # noqa: E402
from vmp import version as ver                                    # noqa: E402

BINDINGS = ROOT / "vmp" / "bindings.json"
CDPM_BINDINGS = ROOT / "cdpm" / "bindings.json"
LEGACY = ROOT / "catalog" / "transition" / "executors_legacy.json"
#: Постоянные входы сборки: сверка их не смотрит, а тесту нужна
#: повторимость без обращения к git.
COMMIT = "0" * 40
AT = "2026-09-08T12:00:00+03:00"

#: Имена описи шага 1, которых в привязках нет ПО ПРИЧИНЕ (§2.2 п.13).
#: Перечень закрытый и с причиной у каждой строки: молчаливой пропажи
#: исполнителя быть не должно.
LOST_WITH_REASON = {
    "_conditions.worktime_exact":
        "символа нет; точную ярусную форму пишет "
        "conditions_export.build_conditions",
    "_conditions.rest_floor":
        "символа нет; лестницы отдыха пишет "
        "conditions_export.build_conditions",
    "_roster_cpsat.build_and_solve":
        "движок линии CP-SAT (webapp/jobs.py), в пакетный конвейер не "
        "входит",
    "_pairing_builder.chain_cost":
        "цена цепочки по ставкам источника, а не накопитель налёта",
    "month_to_date.days_worked":
        "путь данных в пояснении описи (перенос истории), не исполнитель",
    "history.calendar":
        "путь данных в пояснении описи (календарь истории), не исполнитель",
    "series.consecutive_night_duties":
        "путь данных в пояснении описи (перенос серии), не исполнитель",
    "last_duty.end":
        "путь данных в пояснении описи (перенос конца смены), не "
        "исполнитель",
}
#: Семьи, у которых опись называла исполнителя свободными словами без
#: единого точечного имени: им в привязках обязан отвечать символ.
FREE_TEXT_FAMILIES = ("crew_composition", "reserve_floors", "time_scale")
#: Точечное имя в строке описи: Class.method, module.func либо путь
#: модуля (vmp.conditions_export).
_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*"
                       r"(?:/[A-Za-z0-9_/]+)?\.[A-Za-z_][A-Za-z0-9_]*\b")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(ca.CATALOG_ENV, raising=False)
    monkeypatch.delenv(ca.BINDINGS_ENV, raising=False)
    ca.forget()
    bg.forget()
    yield
    ca.forget()
    bg.forget()


def _catalog():
    return ca.current()


def _doc() -> dict:
    return load_bindings(BINDINGS)


def _built(**kw) -> dict:
    kw.setdefault("commit", COMMIT)
    kw.setdefault("generated_at", AT)
    kw.setdefault("stand_results", bb.default_stand_results())
    return bb.build(_catalog(), **kw)


def _rows(doc: dict):
    for row in doc["bindings"]:
        for j, ex in enumerate(row["executors"]):
            yield f"{row['section']}/{row['id']}", j, ex


def _cells(catalog) -> dict:
    """{(раздел, id): [объявленные виды]} — по записям каталога."""
    out = {}
    for section, rec in bg.records_of(catalog):
        out[(section, str(rec["id"]))] = bg.declared_kinds(rec, section)
    return out


# --------------------------------- п.1: файл есть и читается читателем
def test_файл_привязок_есть_и_читается_читателем():
    assert BINDINGS.is_file(), (
        f"{BINDINGS}: с шага 4 файл привязок обязателен — соберите его "
        f"(python -m vmp.bindings_build --out vmp/bindings.json)")
    doc = _doc()
    assert doc.get("product") == ver.PRODUCT, \
        f"{BINDINGS}: продукт {doc.get('product')!r}, ожидался {ver.PRODUCT!r}"
    assert doc.get("bindings"), f"{BINDINGS}: список привязок пуст"


# ------------------------------------------ п.2: шапка и зона продукта
def test_шапка_называет_продукт_версию_каталог_и_зону():
    cat, doc = _catalog(), _doc()
    assert doc["product_version"] == ver.PRODUCT_VERSION, (
        f"{BINDINGS}: версия продукта {doc['product_version']!r}, в "
        f"vmp/version.py — {ver.PRODUCT_VERSION!r}")
    assert doc["catalog_version"] == cat.version(), (
        f"привязки COSMOS к каталогу {doc['catalog_version']}, читается "
        f"{cat.version()}: перегенерируйте "
        f"(python -m vmp.bindings_build --out vmp/bindings.json)")
    assert doc["catalog_fingerprint"] == cat.fingerprint(), (
        f"привязки COSMOS отстали от каталога: в файле отпечаток "
        f"{doc['catalog_fingerprint'][:12]}…, у каталога "
        f"{cat.fingerprint()[:12]}… — перегенерируйте "
        f"(python -m vmp.bindings_build --out vmp/bindings.json)")
    kinds = list(cat.dictionaries()["executor_kinds"])
    if bb._schema_allows("executor_kinds"):
        assert doc.get("executor_kinds") == kinds, (
            f"зона COSMOS — весь словарь видов ({len(kinds)}), в шапке "
            f"{doc.get('executor_kinds')}")
    else:                                                 # pragma: no cover
        assert "executor_kinds" not in doc, (
            "схема читателя поля executor_kinds не знает — шапка обязана "
            "быть без него, зону читатель выведет из строк файла")
    assert bg.zone_of(doc) == set(kinds), (
        "зона продукта по шапке (а без поля — по строкам всех состояний) "
        "обязана быть всем словарём видов")


# ------------------------------ п.3: структурные правила читателя B01-B05
def test_читатель_каталога_не_находит_ошибок():
    cat = _catalog()
    docs = [_doc()]
    if CDPM_BINDINGS.is_file():
        docs.append(load_bindings(CDPM_BINDINGS))
    else:
        print("привязки CDPM не объявлены (cdpm/bindings.json нет) — "
              "B04 по зоне COSMOS")
    errors = [str(p) for p in check_bindings(cat, *docs)
              if p.severity == "error"]
    assert not errors, ("читатель каталога нашёл ошибки привязок:\n  "
                        + "\n  ".join(errors))


# -------------------- п.4: предупреждения B03 — ровно три ручки без имени
def test_предупреждения_читателя_только_про_ручки_без_параметра():
    cat = _catalog()
    warnings = [p for p in check_bindings(cat, _doc())
                if p.severity != "error"]
    names = set()
    for p in warnings:
        m = re.search(r"ручка семьи без параметра в записи: '([^']+)'",
                      p.message)
        assert p.code == "B03" and m, (
            f"единственное законное предупреждение привязок — «ручка семьи "
            f"без параметра в записи», а читатель говорит: {p}")
        names.add(m.group(1))
    assert names == set(bg.UNBOUND_KNOBS), (
        f"ручки без параметра в записи: у читателя {sorted(names)}, гейт "
        f"ждёт {sorted(bg.UNBOUND_KNOBS)} — раздача имени семье обязана "
        f"уменьшить перечень UNBOUND_KNOBS")


# ------------------------- п.5: у каждого объявленного вида есть строка
def test_каждый_объявленный_вид_записи_имеет_строку():
    cat, doc = _catalog(), _doc()
    by_record = {(r["section"], r["id"]): {ex["kind"] for ex in r["executors"]}
                 for r in doc["bindings"]}
    for key, kinds in _cells(cat).items():
        have = by_record.get(key)
        assert have is not None, f"{key[0]}/{key[1]}: записи в привязках нет"
        missing = sorted(set(kinds) - have)
        assert not missing, (f"{key[0]}/{key[1]}: виды {missing} объявлены "
                             f"записью, а строк по ним в привязках нет")
    extra = sorted(set(by_record) - set(_cells(cat)))
    assert not extra, f"в привязках записи, которых нет в каталоге: {extra}"


# --------------------- п.6: «параметр записи ⇒ исполнитель-читатель»
def test_у_каждого_параметра_записи_есть_читатель_в_коде():
    rep = bg.gate(_catalog(), _doc())
    unread = [e for e in rep.errors if "параметр" in e]
    assert not unread, ("имена, записанные каталогом, но не читаемые ни "
                        "одним исполнителем «есть»:\n  " + "\n  ".join(unread))


# ------------------------------- п.7: символы исполнителей «есть» есть
def test_символы_исполнителей_есть_существуют():
    problems = []
    for addr, j, ex in _rows(_doc()):
        for word in bg.resolve(ex):
            problems.append(f"{addr} исполнитель {j}: {word}")
    assert not problems, ("привязки обещают код, которого нет:\n  "
                          + "\n  ".join(problems))


def test_гейт_целиком_чист_и_называет_отсутствие_привязок_cdpm():
    cdpm = load_bindings(CDPM_BINDINGS) if CDPM_BINDINGS.is_file() else None
    rep = bg.gate(_catalog(), _doc(), cdpm)
    assert rep.ok, "гейт привязок COSMOS:\n  " + "\n  ".join(rep.errors)
    if cdpm is None:
        assert any("привязки CDPM не объявлены" in n for n in rep.notes), \
            "отсутствие привязок CDPM обязано быть названо словами"
    assert bg.main([str(BINDINGS)]) == 0


# --------------------------------------- п.8: мёртвых символов в файле нет
def test_мёртвых_символов_в_привязках_нет():
    dead = ("worktime_exact", "rest_floor", "soft_penalty", "build_and_solve")
    found = []
    for addr, j, ex in _rows(_doc()):
        symbol = str(ex.get("symbol") or "")
        tail = symbol.rsplit(".", 1)[-1]
        if tail in dead:
            found.append(f"{addr} исполнитель {j}: {symbol}")
    assert not found, ("в привязках символы, снятые аудитом либо не "
                       "существующие:\n  " + "\n  ".join(found))


# ------------------------------------------- п.9: кросс-свидетели Kotlin
# Три правила «Kotlin ≡ привязки» — имена ограничений среди литералов
# провайдера, мягкие семьи `FAMILY_TO_CONSTRAINT` в строках семей и
# строки `kotlin_metric` ≡ `TaskModes.METRIC_EXECUTORS` — ПЕРЕЕХАЛИ в
# закон паритета правилом L22 (`catalog/tools/parity.py`, свидетель
# `tests/test_template_catalog_parity.py`).
#
# Причина переезда (пакет паритета 08.09.2026, решение 0.12): здесь они
# судили ТОЛЬКО живой каталог дерева и его файл привязок, и на ветке
# правки класса «рост таблиц решателя» краснели ПО ПОСТРОЕНИЮ — новая
# метрика или мягкая семья решателя ещё не имеет записи каталога, потому
# что запись несёт кандидат. L22 судит те же утверждения на СУДИМОМ
# каталоге («живой ⊕ кандидаты» на дереве, временный каталог попытки в
# гейте CR) и его привязках, поэтому зелёный получается по существу, а
# не по перечню ожидаемых красных. Утверждения не ослаблены: см. L22.


# ------------------------------- п.10: сверка, детерминизм, порядок строк
def test_сборка_детерминирована_и_форма_файла_lf():
    first, second = _built(), _built()
    assert bb.dump(first) == bb.dump(second), \
        "одинаковые входы дали разный текст файла привязок"
    text = bb.dump(first)
    assert text.endswith("\n") and "\r" not in text, \
        "форма файла: перевод строки LF и завершающий перевод строки"
    # Сравнение НЕЧУВСТВИТЕЛЬНО к переводу строки — тем же правилом, что
    # у отпечатков кода стенда (§5.5) и у гейта паритета L18: при
    # core.autocrlf=true clone, worktree и смена ветки материализуют файл
    # с CRLF, и сравнение сырых байтов краснело бы там, где ничего не
    # менялось. Форму файла (LF в том, что пишет генератор) доказывают
    # два утверждения выше.
    assert BINDINGS.read_bytes().replace(b"\r\n", b"\n") == bb.dump(
        _built(commit=_doc()["generated_from"],
               generated_at=_doc()["generated_at"])).encode("utf-8"), \
        "файл дерева отличается от того, что собирает генератор сегодня"


def test_check_возвращает_ноль_на_файле_дерева(capsys):
    assert bb.main(["--check", str(BINDINGS), "--commit", COMMIT,
                    "--generated-at", AT]) == 0
    assert "расхождений 0" in capsys.readouterr().out


def test_порядок_строк_не_зависит_от_порядка_посева():
    seed = bb.load_seed()
    shuffled = {"form_version": seed.get("form_version"),
                "pending_records": seed.get("pending_records"),
                "records": {addr: list(reversed(rows))
                            for addr, rows in seed["records"].items()}}
    assert bb.dump(_built(seed=shuffled)) == bb.dump(_built()), \
        "перестановка строк посева изменила файл — порядок задан ключом §1.1"


def test_ключ_порядка_строк_полный_и_состояние_есть_раньше_нет():
    kinds = list(_catalog().dictionaries()["executor_kinds"])
    for row in _doc()["bindings"]:
        keys = [bb._sort_key(ex, kinds) for ex in row["executors"]]
        assert keys == sorted(keys), \
            f"{row['section']}/{row['id']}: строки не в порядке ключа §1.1"
        assert len(set(keys)) == len(keys), \
            f"{row['section']}/{row['id']}: ключ порядка не различает строки"
    dep = next(r for r in _doc()["bindings"]
               if r["id"] == "duty_extension_penalty")
    knobs = [ex["state"] for ex in dep["executors"]
             if ex["kind"] == "kotlin_knob"]
    assert knobs == ["есть", "нет"], (
        f"у клетки с двумя строками «есть» идёт раньше «нет»: {knobs}")


def test_check_называет_расхождение_словами(tmp_path, capsys):
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
    assert f"{row['section']}/{row['id']}" in out and "поле note" in out, out


def test_канон_и_отпечаток_не_зависят_от_момента_сборки():
    a = _built(commit="0" * 40, generated_at="2026-09-08T12:00:00+03:00")
    b = _built(commit="f" * 40, generated_at="2026-09-09T23:59:59+03:00")
    assert bb.canon(a) == bb.canon(b) and bb.fingerprint(a) == bb.fingerprint(b)
    assert bb.dump(a) != bb.dump(b), "сам файл момент и коммит несёт"
    assert ca.bindings_summary()["fingerprint"] == bb.fingerprint(_doc()), \
        "сводка двери обязана нести тот же отпечаток канона"


# ------------------------------------ п.11: стендовые подтверждения (B05)
def _stand_results(cat, section, rec, task) -> dict:
    """Файл результатов формы §5.5 — с ЖИВОЙ картой отпечатков кода.

    Карта строится `stand_tasks.code_fingerprints()`, а не пишется
    рукой: пустая карта была бы фикстурой, закрепляющей лазейку —
    «файл, умолчавший об отпечатках, свеж»."""
    import vmp.stand_tasks as st
    return {
        "form_version": 1, "catalog_version": cat.version(),
        "catalog_fingerprint": cat.fingerprint(),
        "code": st.code_fingerprints(),
        "at": AT, "commit": COMMIT, "solver": None,
        "results": [{"record": f"{section}/{rec['id']}", "task": task["id"],
                     "executor_kind": task.get("executor_kind")
                     or bg.declared_kinds(rec, section)[0],
                     "result": "passed", "got": {"value": 1},
                     "note": "получено 1, ожидалось 1"}]}


def test_стендовые_подтверждения_из_файла_результатов(tmp_path):
    cat = _catalog()
    record = next((s, r) for s, r in bg.records_of(cat)
                  if r.get("stand_tasks"))
    section, rec = record
    task = rec["stand_tasks"][0]
    results = _stand_results(cat, section, rec, task)
    path = tmp_path / "stand_results.json"
    path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    doc = _built(stand_results=bb.load_stand_results(path))
    row = next(r for r in doc["bindings"] if r["id"] == rec["id"])
    assert len(row["stand_verified"]) == 1
    sv = row["stand_verified"][0]
    assert sv["task"] in {t["id"] for t in rec["stand_tasks"]}
    assert sv["executor_kind"] in bg.declared_kinds(rec, section)
    assert sv["result"] in cat.dictionaries()["stand_results"]
    assert sv["at"] == AT and sv["commit"] == COMMIT
    assert not [p for p in check_bindings(cat, doc) if p.severity == "error"]
    # устаревшие результаты — отказ, а не тихое подмешивание
    results["catalog_fingerprint"] = "0" * 64
    path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(stand_results=bb.load_stand_results(path))
    assert "перепрогоните стенд" in str(e.value)


@pytest.mark.parametrize("damage", ["пустая", "укороченная", "лишняя"])
def test_неполная_карта_кода_результатов_отказ(tmp_path, damage):
    """Файл результатов не может объявить себя свежим УМОЛЧАНИЕМ.

    Односторонний гейт сверял только те отпечатки, которые файл сам
    назвал: карта `code: {}` проходила за свежую, и чужой исход попадал
    бы в `stand_verified` без единого прогона исполнителя. Судится и
    обратное — «названы ровно те файлы, которыми стенд свеж»."""
    import vmp.stand_tasks as st
    cat = _catalog()
    section, rec = next((s, r) for s, r in bg.records_of(cat)
                        if r.get("stand_tasks"))
    results = _stand_results(cat, section, rec, rec["stand_tasks"][0])
    results["results"][0]["result"] = "failed"
    if damage == "пустая":
        results["code"] = {}
    elif damage == "укороченная":
        results["code"].pop(st.CODE_FILES[0])
    else:
        results["code"]["vmp/нет_такого_файла.py"] = "0" * 64
    path = tmp_path / "stand_results.json"
    path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(stand_results=bb.load_stand_results(path))
    assert "перепрогоните стенд" in str(e.value)
    assert "карта code" in str(e.value), str(e.value)


# ------------------------- п.12: счёт клеток и строк — формулой, не литералом
def test_счёт_клеток_и_строк_считается_от_каталога_и_файла():
    cat, doc = _catalog(), _doc()
    cells = sum(len(kinds) for kinds in _cells(cat).values())
    present = sum(1 for _, _, ex in _rows(doc) if ex["state"] == "есть")
    absent = sum(1 for _, _, ex in _rows(doc) if ex["state"] == "нет")
    summary = ca.bindings_summary()
    assert summary["cells"] == cells
    assert summary["present_rows"] == present
    assert summary["absent_rows"] == absent
    covered = {(r["section"], r["id"], ex["kind"])
               for r in doc["bindings"] for ex in r["executors"]}
    uncovered = [(s, i, k) for (s, i), kinds in _cells(cat).items()
                 for k in kinds if (s, i, k) not in covered]
    assert not uncovered, f"клетки без единой строки: {uncovered}"


# --------------------------------- п.13: «ничего не потеряно» против описи
def _matches_row(ident: str, ex: dict) -> bool:
    """Отвечает ли строка «есть» точечному имени описи: Class.method и
    module.func — по последнему компоненту символа и владельцу, путь
    модуля — по файлу строки."""
    symbol = str(ex["symbol"])
    module = str(ex["file"]).removesuffix(".py").replace("/", ".")
    head, tail = ident.rsplit(".", 1)
    head = head.replace("/", ".").split(".")[-1]
    if module == ident.replace("/", "."):
        return True                       # опись назвала модуль целиком
    return symbol.rsplit(".", 1)[-1] == tail and (
        symbol.split(".")[0] == head or module.split(".")[-1] == head)


def test_ничего_не_потеряно_против_описи_шага_1():
    legacy = json.loads(LEGACY.read_text(encoding="utf-8"))
    doc = _doc()
    present = {row["id"]: [ex for ex in row["executors"]
                           if ex["state"] == "есть"]
               for row in doc["bindings"]}
    lost, seen = [], set()
    for family, names in legacy.items():
        for line in names:
            for ident in _IDENT_RE.findall(str(line)):
                if ident in LOST_WITH_REASON:
                    seen.add(ident)
                    continue
                if not any(_matches_row(ident, ex)
                           for ex in present.get(family, [])):
                    lost.append(f"{family}: {ident}")
    assert not lost, ("исполнители описи шага 1, пропавшие из привязок без "
                      "причины:\n  " + "\n  ".join(sorted(set(lost)))
                      + f"\n(перечень с причинами: "
                        f"{sorted(LOST_WITH_REASON)})")
    stale = sorted(set(LOST_WITH_REASON) - seen)
    assert not stale, (f"перечень причин называет имена, которых в описи "
                       f"нет: {stale} — перечень обязан быть живым")
    for family in FREE_TEXT_FAMILIES:
        assert present.get(family), (
            f"{family}: опись называла исполнителя свободными словами — в "
            f"привязках обязан стоять символ")
    for name, why in LOST_WITH_REASON.items():
        assert why, f"{name}: причина отсутствия обязана быть словами"


# ------------------------------------------- п.14: строки обёртки из файла
def test_строки_обёртки_равны_строкам_есть_в_порядке_файла():
    from vmp import template_registry as tr
    doc = _doc()
    want = {}
    for row in doc["bindings"]:
        if row["section"] != "families":
            continue
        want[row["id"]] = [f"{ex['kind']} · {ex['symbol']} · {ex['file']}"
                           for ex in row["executors"]
                           if ex["state"] == "есть"]
    assert ca.bindings_executors(_catalog()) == want
    for family, lines in want.items():
        assert tr.RULE_MECHANIC_TYPES[family]["executors"] == lines, (
            f"{family}: строки обёртки разошлись с привязками")


# ---------------------------------------------- отказы генератора словами
def test_запись_каталога_без_посева_отказ():
    seed = bb.load_seed()
    seed["records"].pop("families/duty_limits")
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(seed=seed)
    assert "families/duty_limits" in str(e.value) and "посеве" in str(e.value)


def test_объявленный_вид_без_строки_отказ():
    seed = bb.load_seed()
    rows = seed["records"]["families/duty_limits"]
    seed["records"]["families/duty_limits"] = [
        r for r in rows if r["kind"] != "check_code"]
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(seed=seed)
    assert "check_code" in str(e.value)


def test_повтор_пары_вид_и_ключ_отказ():
    seed = bb.load_seed()
    rows = seed["records"]["families/duty_limits"]
    twin = next(dict(r) for r in rows if r["kind"] == "check_code")
    seed["records"]["families/duty_limits"] = rows + [twin]
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(seed=seed)
    assert "дважды" in str(e.value)


def test_поля_ручки_из_таблиц_решателя_в_посеве_запрещены():
    seed = bb.load_seed()
    rows = [dict(r) for r in seed["records"]["families/duty_limits"]]
    for r in rows:
        if r["kind"] == "kotlin_knob":
            r["unit"] = "count"
            break
    seed["records"]["families/duty_limits"] = rows
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(seed=seed)
    assert "из Kotlin" in str(e.value)


def test_неизвестная_ручка_решателя_отказ():
    seed = bb.load_seed()
    rows = [dict(r) for r in seed["records"]["families/duty_limits"]]
    for r in rows:
        if r["kind"] == "kotlin_knob":
            r["param"] = "max_duty_duration_ghost"
            break
    seed["records"]["families/duty_limits"] = rows
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(seed=seed)
    assert "RunThresholds.KNOBS" in str(e.value)


def test_перечень_ожидающих_записей_не_прячет_записи_каталога():
    seed = bb.load_seed()
    known = {f"{s}/{r['id']}" for s, r in bg.records_of(_catalog())}
    stale = sorted(set(seed.get("pending_records") or []) & known)
    assert not stale, (
        f"адреса {stale} объявлены ожидающими, а каталог их уже назвал: "
        f"такой перечень спрячет переименование записи вместо отказа — "
        f"уберите их из pending_records посева")


def test_запись_посева_вне_каталога_и_вне_ожидающих_отказ():
    seed = bb.load_seed()
    seed["records"]["families/ghost_family"] = [
        {"kind": "builder_method", "state": "есть", "file": "vmp/criteria.py",
         "symbol": "_exec_min_person_days", "params": []}]
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(seed=seed)
    assert "families/ghost_family" in str(e.value)
    seed["pending_records"] = list(seed.get("pending_records") or []) \
        + ["families/ghost_family"]
    doc = _built(seed=seed)
    assert "families/ghost_family" not in {r["id"] for r in doc["bindings"]}, \
        "запись, объявленная ожидающей, строк в файле не даёт"


def test_строка_нет_без_заметки_отказ():
    seed = bb.load_seed()
    rows = [dict(r) for r in seed["records"]["families/duty_limits"]]
    for r in rows:
        if r["state"] == "нет":
            r["note"] = None
            break
    seed["records"]["families/duty_limits"] = rows
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(seed=seed)
    assert "молчаливого отказа" in str(e.value)


def test_кривой_коммит_и_момент_отказ():
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(commit="не-хеш")
    assert "хеш git" in str(e.value)
    with pytest.raises(bb.BindingsBuildError) as e:
        _built(generated_at="вчера")
    assert "ISO 8601" in str(e.value)


def test_без_каталога_генератор_отказывает_словами(monkeypatch, tmp_path,
                                                   capsys):
    monkeypatch.setenv(ca.CATALOG_ENV, str(tmp_path / "нет-каталога"))
    ca.forget()
    out_path = tmp_path / "bindings.json"
    assert bb.main(["--out", str(out_path)]) == 2
    assert "каталог шаблонов недоступен" in capsys.readouterr().out
    assert not out_path.exists(), "при отказе файл не пишется"


def test_нет_символа_отказ_гейта():
    with pytest.raises(bg.GateError) as e:
        bg.resolve_python("vmp/criteria.py", "нет_такого_символа")
    assert "vmp/criteria.py" in str(e.value)
    words = bg.resolve({"kind": "kotlin_constraint", "state": "есть",
                        "lang": "kotlin", "file": bg.PROVIDER_FILE,
                        "symbol": "FlightCrewSchedulingConstraintProvider."
                                  "layoverNights",
                        "constraint": "Ограничение, которого нет"})
    assert words and "Ограничение, которого нет" in words[0]


def test_head_commit_полный_хеш():
    try:
        commit = bb.head_commit()
    except bb.BindingsBuildError as e:                      # pragma: no cover
        pytest.skip(f"git недоступен: {e}")
    assert re.fullmatch(r"[0-9a-f]{40}", commit), commit


# -------------------------------------- B04 по зонам: четыре ветви гейта
def _narrow(doc: dict, kinds: list) -> dict:
    """Копия файла с узкой зоной — для ветвей B04, недостижимых у живого
    файла COSMOS (его зона — все десять видов)."""
    out = json.loads(json.dumps(doc))
    out["executor_kinds"] = list(kinds)
    return out


def test_B04_по_зонам_четыре_ветви():
    cat, doc = _catalog(), _doc()
    # (а) «есть» у одного продукта закрывает вид
    assert bg.gate(cat, doc).ok
    # (б) вид вне зоны продукта и без «есть» — ошибка «ни в чьей зоне»
    stripped = json.loads(json.dumps(doc))
    for row in stripped["bindings"]:
        if row["id"] == "crew_composition":
            row["executors"] = [ex for ex in row["executors"]
                                if ex["kind"] != "check_template"]
    narrow = _narrow(stripped, [k for k in doc["executor_kinds"]
                                if k != "check_template"])
    words = bg.gate(cat, narrow).errors
    assert any("ни в чьей зоне" in w and "crew_composition" in w
               for w in words), words
    # (в) продукт в зоне, но строки не дал — ошибка с перечнем продуктов
    silent = json.loads(json.dumps(stripped))
    words = bg.gate(cat, silent).errors
    assert any("crew_composition" in w and "в зоне, но не объявил" in w
               for w in words), words
    # (г) зона без поля шапки — по строкам ВСЕХ состояний
    no_field = json.loads(json.dumps(doc))
    no_field.pop("executor_kinds")
    assert "check_template" in bg.zone_of(no_field), (
        "продукт с одними «нет» по виду обязан оставаться в зоне")
    assert bg.gate(cat, no_field).ok


def test_лучшее_состояние_клетки_не_зависит_от_порядка_строк():
    doc = _doc()
    states = bg.best_states(doc)
    cell = states[("families", "duty_extension_penalty")]["kotlin_knob"]
    assert cell == "есть", ("у клетки с «есть» и «нет» лучшее состояние — "
                            "«есть», в каком бы порядке строки ни лежали")
    flipped = json.loads(json.dumps(doc))
    for row in flipped["bindings"]:
        row["executors"].reverse()
    assert bg.best_states(flipped)[("families", "duty_extension_penalty")][
        "kotlin_knob"] == "есть"


def test_привязки_cdpm_читаются_вместе_с_нашими():
    if not CDPM_BINDINGS.is_file():
        pytest.skip("привязки CDPM не объявлены (cdpm/bindings.json нет)")
    cat = _catalog()
    rep = bg.gate(cat, _doc(), load_bindings(CDPM_BINDINGS))
    assert rep.ok, "\n  ".join(rep.errors)
    assert rep.cdpm_declared and set(rep.products) == {"COSMOS", "CDPM"}


# ------------------------------------------------- дверь: отказы словами
def test_дверь_отказывает_словами_без_файла_привязок(tmp_path, monkeypatch):
    monkeypatch.setenv(ca.BINDINGS_ENV, str(tmp_path / "нет-привязок.json"))
    ca.forget()
    with pytest.raises(tc.CatalogNotFound) as e:
        ca.bindings()
    assert "нет-привязок.json" in str(e.value) and \
        "vmp.bindings_build" in str(e.value)
    assert ca.bindings_summary()["state"] == ca.BIND_MISSING


# ── Ключ CR у строки «заявлен» (шаг 6: конвейер пополнения) ────────────

def test_строка_заявлен_без_ключа_cr_отказ_генератора():
    """«Заявлен» значит «исполнитель обещан строкой очереди изменений».
    Без ключа CR обещание не проверить: экран красил бы клетку словом
    «заявлен» без единого следа работы, а сборка версии не смогла бы
    сказать, чем эта клетка закрывается. До шага 6 это ловил только
    читатель (B03) на готовом файле — отказ перенесён к генератору, где
    строка рождается."""
    seed = bb.load_seed()
    rows = [dict(r) for r in seed["records"]["families/duty_limits"]]
    rows.append({"kind": "kotlin_knob", "state": "заявлен", "param": "max_duty_duration",
                 "note": "исполнитель заявлен по заявке каталога"})
    seed["records"]["families/duty_limits"] = rows

    def _build(seed_doc):
        # Результаты стенда здесь не при чём (их свежесть судят свои
        # тесты файла): предмет — строка «заявлен» и её ключ.
        return bb.build(_catalog(), commit=COMMIT, generated_at=AT,
                        stand_results=None, seed=seed_doc)

    with pytest.raises(bb.BindingsBuildError) as e:
        _build(seed)
    assert "ключа CR" in str(e.value) and "cr<8" in str(e.value)
    # Ключ не по образцу — тот же отказ (образец один на все машины).
    rows[-1]["cr_key"] = "CR575B6C"
    with pytest.raises(bb.BindingsBuildError):
        _build(dict(seed, records=dict(seed["records"],
                                       **{"families/duty_limits": rows})))
    # Ключ по закону заявки — строка строится.
    from template_catalog import requests as law
    rows[-1]["cr_key"] = law.cr_key_of(law.request_id_of(
        law.kind_request_key("families", "duty_limits", ["kotlin_knob"])))
    doc = _build(dict(seed, records=dict(seed["records"],
                                         **{"families/duty_limits": rows})))
    row = next(r for r in doc["bindings"] if r["id"] == "duty_limits")
    declared = [e for e in row["executors"] if e["state"] == "заявлен"]
    assert len(declared) == 1 and declared[0]["cr_key"] == rows[-1]["cr_key"]
    assert declared[0]["file"] is None and declared[0]["symbol"] is None
    # Читатель на этом файле молчит: генератор и B03 судят одно и то же.
    assert not [p for p in check_bindings(_catalog(), doc)
                if p.severity == "error" and p.code == "B03"]


def test_временный_каталог_кандидата_строит_строки_записи(tmp_path):
    """Гейт CR каталога собирает привязки на ВРЕМЕННОМ каталоге «живой
    каталог ⊕ запись кандидата»: строки записи строятся, и адреса
    записи в `pending_records` посева нет — иначе гейт был бы зелен на
    пустоте (запись, объявленная ожидающей, строк не даёт)."""
    from template_catalog import candidates as tcc
    import catalog_copies as cc
    cat = _catalog()
    record = {k: v for k, v in cat.family("duty_limits").items()
              if k != "history"}
    # Кандидаты ДЕРЕВА ложатся первыми: судимый каталог дерева — «живой ⊕
    # кандидаты» (0.3 проекта паритета), и посев дерева уже называет их
    # записи. Временный каталог, собранный только из своего кандидата, на
    # ветке роста таблиц решателя отвергается генератором словами «посев
    # называет записи, которых в каталоге нет» — по построению, а не по
    # делу. На чистом дереве кандидатов нет, и цепочка пуста.
    src = Path(cat.path)
    for i, doc_ in enumerate(cc.tree_candidate_docs()):
        src = tcc.materialize(src, doc_, tmp_path / f"дерево-{i}")
    folder = tcc.materialize(
        src,
        {"form_version": 1, "section": "families", "id": "duty_limits",
         "record": record, "note": "кандидат теста", "by": "тест",
         "saved_at": "2026-09-12T10:15:00+03:00",
         "request_id": "req-03707195193482806055", "cr_key": "cr575b6c6a"},
        tmp_path / "каталог-гейта")
    seed = bb.load_seed()
    assert "families/duty_limits" not in (seed.get("pending_records") or [])
    doc = bb.build(tc.load(folder), commit=COMMIT, generated_at=AT,
                   stand_results=None)
    row = next((r for r in doc["bindings"] if r["id"] == "duty_limits"), None)
    assert row is not None and row["executors"], doc["catalog_version"]
    assert doc["catalog_version"] == tc.load(folder).version()
    assert doc["catalog_fingerprint"] == tc.load(folder).fingerprint(), \
        "шапка привязок называет ТОТ каталог, на котором они собраны"


# ── Строки посева, опережающие живой каталог (пакет паритета, 0.10) ────

def _candidate_beside(folder: Path, section: str, rid: str, record: dict):
    """Файл-кандидат рядом с каталогом: `candidates/<раздел>/<id>.json`."""
    p = folder / "candidates" / section / f"{rid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"form_version": 1, "section": section, "id": rid, "record": record,
           "note": "кандидат стенда привязок", "by": "тест",
           "saved_at": "2026-09-12T10:15:00+03:00",
           "request_id": "req-03707195193482806055", "cr_key": "cr575b6c6a"}
    p.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True,
                              indent=1) + "\n").encode("utf-8"))
    return p


def _knob_row_for(param: str) -> dict:
    """Строка посева ручки решателя: поля порога — из Kotlin."""
    return {"kind": "kotlin_knob", "state": "есть",
            "file": "kotlin-solver/src/main/kotlin/org/acme/"
                    "flighcrewscheduling/solver/RunThresholds.kt",
            "symbol": "RunThresholds.KNOBS", "param": param}


def test_строка_нового_параметра_ждёт_кандидата_словами(tmp_path):
    """Решение 0.10: строка посева, чей параметр называет не каталог, а
    КАНДИДАТ рядом, законна — генератор пропускает её словами. После
    сборки (запись параметр называет) строка входит в файл сама, и
    править перечни рукой не нужно."""
    import catalog_copies as cc
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    # Имя ручки решателя, которое эта запись пока не называет.
    param = next(n for n in sorted(kt.knobs())
                 if n not in {p["name"] for p in
                              cat.family("duty_limits")["params"]})
    # Посев — с строками синтетических записей режима роста каталога:
    # без них генератор откажет «запись каталога в посеве не названа» по
    # построению режима, а не по проверяемому правилу.
    seed = cc.seed_with_probe()
    rows = [dict(r) for r in seed["records"]["families/duty_limits"]]
    rows.append(_knob_row_for(param))
    seed = dict(seed, records=dict(seed["records"],
                                   **{"families/duty_limits": rows}))

    # Без кандидата — отказ словами: строка обещает параметр, которого
    # запись не называет.
    with pytest.raises(bb.BindingsBuildError) as e:
        bb.build(cat, commit=COMMIT, generated_at=AT, stand_results=None,
                 seed=seed)
    assert param in str(e.value) and "кандидат" in str(e.value)

    # С кандидатом, объявившим параметр, — строка пропускается СЛОВАМИ.
    record = {k: v for k, v in cat.family("duty_limits").items()
              if k != "history"}
    record["params"] = sorted(
        list(record["params"]) + [{"name": param, "unit": kt.knobs()[param]["unit"],
                                   "relation": kt.knobs()[param]["relation"],
                                   "qualifier": None, "note": None}],
        key=lambda p: str(p["name"]))
    _candidate_beside(folder, "families", "duty_limits", record)
    cat = tc.load(folder)
    doc = bb.build(cat, commit=COMMIT, generated_at=AT, stand_results=None,
                   seed=seed)
    words = bb.awaiting_words(cat, seed)
    print("\n" + "; ".join(words))
    assert any(param in w and "duty_limits" in w for w in words), words
    row = next(r for r in doc["bindings"] if r["id"] == "duty_limits")
    assert param not in {ex.get("param") for ex in row["executors"]}
    assert not [p for p in check_bindings(cat, doc)
                if p.severity == "error" and p.code == "B03"]

    # «Сборка службой»: кандидата нет, запись параметр называет — строка
    # входит в файл сама.
    (folder / "candidates" / "families" / "duty_limits.json").unlink()
    grown = cc.copy_catalog(tmp_path, name="собранный",
                            families={"duty_limits": {
                                "params": record["params"]}})
    doc2 = bb.build(tc.load(grown), commit=COMMIT, generated_at=AT,
                    stand_results=None, seed=seed)
    row2 = next(r for r in doc2["bindings"] if r["id"] == "duty_limits")
    assert param in {ex.get("param") for ex in row2["executors"]}
    assert not bb.awaiting_words(tc.load(grown), seed)


def test_запись_кандидата_целиком_ждёт_словами(tmp_path):
    """Адрес записи, которой в каталоге ещё нет, законен по кандидату:
    отказ «посев называет записи, которых нет» не срабатывает, а слова о
    пропуске говорятся. Без кандидата — отказ, как и был."""
    import catalog_copies as cc
    folder = cc.copy_catalog(tmp_path)
    cat = tc.load(folder)
    # Посев — со строками синтетических записей режима роста (см. выше).
    seed = cc.seed_with_probe()
    seed = dict(seed, records=dict(
        seed["records"],
        **{"families/ghost_family": [
            {"kind": "builder_method", "state": "есть",
             "file": "vmp/criteria.py", "symbol": "_exec_min_person_days",
             "params": []}]}))
    with pytest.raises(bb.BindingsBuildError) as e:
        bb.build(cat, commit=COMMIT, generated_at=AT, stand_results=None,
                 seed=seed)
    assert "families/ghost_family" in str(e.value)

    record = {k: v for k, v in cat.family("duty_limits").items()
              if k not in ("history", "id")}
    record["id"] = "ghost_family"
    _candidate_beside(folder, "families", "ghost_family", record)
    cat = tc.load(folder)
    doc = bb.build(cat, commit=COMMIT, generated_at=AT, stand_results=None,
                   seed=seed)
    words = bb.awaiting_words(cat, seed)
    print("\n" + "; ".join(words))
    assert any("ghost_family" in w for w in words), words
    assert "ghost_family" not in {r["id"] for r in doc["bindings"]}


def test_prune_pending_снимает_отметку_ожидания_у_записи_каталога(tmp_path):
    """`--prune-pending`: отметка ожидания в посеве — состояние, выводимое
    из каталога. Запись, которую каталог уже назвал, перестаёт быть
    ожидающей шагом цепочки после сборки (выпуск 0.4.0, 09.09.2026:
    duty_series_limit осталась «ожидающей» и красила гейт волны); без
    ключа посев не трогается — гейты CR судят временный каталог."""
    seed = json.loads(json.dumps(bb.load_seed()))
    seed["pending_records"] = sorted(
        set(seed.get("pending_records") or []) | {"families/duty_limits"})
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seed, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    out = tmp_path / "bindings.json"
    stand = str(ROOT / "vmp" / "stand_results.json")
    assert bb.main(["--out", str(out), "--seed", str(seed_path),
                    "--stand-results", stand]) == 0
    kept = json.loads(seed_path.read_text(encoding="utf-8"))
    assert "families/duty_limits" in kept["pending_records"]
    assert bb.main(["--out", str(out), "--seed", str(seed_path),
                    "--stand-results", stand, "--prune-pending"]) == 0
    pruned = json.loads(seed_path.read_text(encoding="utf-8"))
    assert "families/duty_limits" not in pruned["pending_records"]
    assert pruned["records"] == seed["records"]
