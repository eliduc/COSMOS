# -*- coding: utf-8 -*-
"""Гейт продукта: стенд шаблонов проверок данных CDPM, форма мира
`tables` (шаг 4 каталога, часть CDPM; проект
final-report/KATALOG-SHAG4-CDPM-STEND-TABLES-PROEKT-CLD.md §4, T1–T11).

Судятся: посев задач (T1), миры на копии каталога читателем (T2),
равенство посева и каталога (T3), собственные правила формы (T4),
сходимость ожиданий и контракт отчёта (T5), детерминизм (T6), отказы
словами (T7), файл результатов и его свежесть (T8), слияние в привязки
(T9), живой файл результатов (T10) и коды возврата команды (T11).

`failed` вне разобранного перечня `stand_tables.KNOWN_FAILED` — красный
гейт: ожидание не подгоняется, перечень пополняется только по слову
владельца после разбора.
"""
import copy
import json
from pathlib import Path

import pytest

import catalog_copies as cc
import template_catalog as tc
from cdpm import bindings_build as bb
from cdpm import catalog_bridge as bridge
from cdpm import data_checks as dc
from cdpm import data_report as dr
from cdpm import stand_tables as st
from template_catalog.bindings import check_bindings
from template_catalog.export import dump_json

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "cdpm" / "stand_results.json"
#: Постоянные входы: файл результатов детерминирован при них, а тесту не
#: нужен git.
COMMIT = "0" * 40
AT = "2026-09-07T12:00:00+03:00"
#: Группы словаря `check_templates`, исполнитель которых — код проверок
#: данных CDPM (прочие группы стенду CDPM не принадлежат).
CDPM_GROUPS = ("data", "history_form")
#: Заметка формы `tables` для копии каталога, если живой словарь формы
#: ещё не несёт (объект `world_forms` схемой открыт для новых форм).
TABLES_FORM = {
    "fields": ["form", "tables"],
    "note": "таблицы линии данных: книга «идентификатор набора/лист» с "
            "колонками и строками; идентификаторы наборов — только "
            "синтетические; ожидание проверки по шаблону — имя шаблона, "
            "значения параметров и число находок",
}


@pytest.fixture(autouse=True)
def _fresh_bridge(monkeypatch):
    monkeypatch.delenv(bridge.ENV_CATALOG, raising=False)
    monkeypatch.delenv("CREW_VMP_TEMPLATE_BASE", raising=False)
    bridge.forget()
    yield
    bridge.forget()


def _catalog():
    cat = bridge.catalog()
    assert cat is not None, ("каталог шаблонов недоступен — стенд CDPM "
                             "судить не по чему")
    return cat


def _records(catalog) -> dict:
    """Записи каталога, за которые отвечает стенд, по адресу."""
    return {f"{section}/{rec.get('id')}": rec
            for section, rec in bb._records(catalog)}


def _task(seed: dict, addr: str, tid: str) -> dict:
    return copy.deepcopy(next(t for t in seed[addr] if t["id"] == tid))


def _refresh(folder: Path) -> None:
    """Отпечаток шапки копии — тем же способом, что у помощника копий
    (иначе любая правка копии доказывала бы H02, а не проверяемое
    правило); привязкам копии он тоже нужен (B01)."""
    docs = {n: json.loads((folder / n).read_text(encoding="utf-8"))
            for n in ("catalog.json", "families.json", "criteria.json",
                      "dictionaries.json")}
    body = {"families": docs["families.json"]["families"],
            "criteria": docs["criteria.json"]["criteria"],
            "dictionaries": {k: v for k, v
                             in docs["dictionaries.json"].items()
                             if k != "form_version"}}
    docs["catalog.json"]["fingerprint"] = tc.fingerprint_of(body)
    (folder / "catalog.json").write_bytes(
        dump_json(docs["catalog.json"]).encode("utf-8"))
    b = folder / cc.BINDINGS_FILE
    if b.is_file():
        doc = json.loads(b.read_text(encoding="utf-8"))
        doc["catalog_fingerprint"] = docs["catalog.json"]["fingerprint"]
        b.write_bytes(dump_json(doc).encode("utf-8"))


def _dataset_ids_allowed() -> bool:
    """Допускает ли закрытая схема читателя ключ `dataset_ids` в объекте
    `synthetic_codes` (каталог 0.2.0-draft.1). Схема не читается —
    считается, что нет: копия не по схеме хуже копии без необязательного
    ключа."""
    try:
        from template_catalog.validate import _load_schema
        props = ((_load_schema() or {}).get("$defs", {})
                 .get("dictionaries_file", {}).get("properties", {})
                 .get("synthetic_codes", {}).get("properties", {}))
        return "dataset_ids" in props
    except Exception:                                         # noqa: BLE001
        return False


def _copy_with_tasks(tmp_path, seed: dict, *, name: str = "catalog") -> Path:
    """Копия каталога с задачами посева в записях. Форма мира `tables` и
    перечень синтетических наборов добавляются в словарь копии, только
    если живой словарь их ещё не несёт (валидатор судит идентификатор
    книги по `dataset_ids` и без перечня отвечает «судить нечем»), а
    перечень — ещё и только когда схема читателя ключ допускает. Каждая
    правка словаря — с пересчётом отпечатка шапки."""
    families = {addr.split("/", 1)[1]: {"stand_tasks": copy.deepcopy(tasks)}
                for addr, tasks in seed.items()
                if addr.startswith("families/")}
    folder = cc.copy_catalog(tmp_path, families=families, name=name)
    path = folder / "dictionaries.json"
    dicts = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    forms = dicts.setdefault("world_forms", {})
    if st.WORLD_FORM not in forms:
        forms[st.WORLD_FORM] = copy.deepcopy(TABLES_FORM)
        changed = True
    codes = dicts.setdefault("synthetic_codes", {})
    if not codes.get("dataset_ids") and _dataset_ids_allowed():
        codes["dataset_ids"] = list(st.DATASET_IDS)
        changed = True
    if changed:
        path.write_bytes(dump_json(dicts).encode("utf-8"))
        _refresh(folder)
    return folder


def _use(monkeypatch, folder: Path):
    monkeypatch.setenv(bridge.ENV_CATALOG, str(folder))
    bridge.forget()
    return _catalog()


# ------------------------------------------------- T1: посев читается
def test_T1_seed_is_readable_and_agrees_with_catalog():
    seed = st.load_seed()
    cat = _catalog()
    records = _records(cat)
    dicts = cat.dictionaries() or {}
    templates = dicts.get("check_templates") or {}
    assert seed, "посев стендовых задач пуст"
    for addr, tasks in seed.items():
        rec = records.get(addr)
        assert rec is not None, (
            f"{addr}: записи со статусом «есть» и видом {st.KIND} в каталоге "
            f"нет — задачам посева не к чему относиться")
        ids = [t["id"] for t in tasks]
        assert ids == sorted(ids), f"{addr}: задачи не по id: {ids}"
        assert len(set(ids)) == len(ids), f"{addr}: повтор id задачи"
        for t in tasks:
            where = f"{addr}/{t['id']}"
            assert st.ID_RE.match(t["id"]), f"{where}: id не по образцу"
            assert set(t["expected"]) == {st.KIND}, (
                f"{where}: ключи ожидания {sorted(t['expected'])} — стенд "
                f"CDPM судит только {st.KIND}")
            exp = t["expected"][st.KIND]
            assert set(exp) == {"template", "params", "findings"}, exp
            assert isinstance(exp["findings"], int) and exp["findings"] >= 0
            name = exp["template"]
            assert name in (rec.get("check_templates") or []), (
                f"{where}: шаблон {name!r} записью не объявлен")
            spec = templates.get(name)
            assert isinstance(spec, dict), (
                f"{where}: шаблона {name!r} нет в словаре check_templates")
            assert spec.get("group") in CDPM_GROUPS, (
                f"{where}: шаблон {name!r} группы {spec.get('group')!r} — не "
                f"зона исполнителя CDPM {CDPM_GROUPS}")
            missing = [f for f in st.template_fields(dicts, name)
                       if f not in exp["params"]]
            assert not missing, (f"{where}: в ожидании нет обязательных "
                                 f"полей шаблона {missing}")
            assert t["world"].get("form") == st.WORLD_FORM, where


# ------------------------- T2: миры проходят проверку каталога читателем
def test_T2_worlds_pass_catalog_validate_on_a_copy(tmp_path):
    seed = st.load_seed()
    folder = _copy_with_tasks(tmp_path, seed)
    catalog = tc.load(folder)
    assert st.WORLD_FORM in (catalog.dictionaries().get("world_forms") or {}), \
        "в копии каталога нет формы мира tables — судить нечем"
    for addr, tasks in seed.items():
        rec = catalog.family(addr.split("/", 1)[1])
        assert dump_json(rec.get("stand_tasks")) == dump_json(tasks), (
            f"{addr}: копия каталога несёт не те задачи")
    problems = catalog.validate()
    addresses = tuple(seed)
    mine = [str(p) for p in problems
            if any(str(p.where).startswith(a) for a in addresses)]
    assert not mine, ("читатель каталога нашёл проблемы у записей со "
                      "стендовыми задачами формы tables:\n  "
                      + "\n  ".join(mine))


# --------------------------------- T3: равенство посева и каталога
def test_T3_catalog_tasks_equal_the_seed(capsys):
    seed = st.load_seed()
    cat = _catalog()
    records = _records(cat)
    for addr, tasks in seed.items():
        rec = records.get(addr) or {}
        in_catalog = [t for t in (rec.get("stand_tasks") or [])
                      if (t.get("world") or {}).get("form") == st.WORLD_FORM]
        if not in_catalog:
            print(f"{addr}: каталог задач формы {st.WORLD_FORM} ещё не "
                  f"несёт — стенд по посеву")
            continue
        assert dump_json(sorted(in_catalog, key=lambda t: str(t.get("id")))) \
            == dump_json(sorted(tasks, key=lambda t: str(t.get("id")))), (
            f"{addr}: задачи каталога разошлись с посевом "
            f"cdpm/stand_tables_seed.json")
    capsys.readouterr()


# --------------------------------------- T4: собственные правила формы
def test_T4_translator_gives_dataset_shape():
    seed = st.load_seed()
    dicts = _catalog().dictionaries() or {}
    for addr, tasks in seed.items():
        for t in tasks:
            dataset = st.world_to_dataset(t["world"], dicts)
            assert dataset["sources"], f"{addr}/{t['id']}: нет источников"
            for src in dataset["sources"]:
                assert set(src) == {"sid", "file", "sha256", "rows_seen"}
                assert len(src["sha256"]) == 64 and src["rows_seen"] >= 0
            params = t["expected"][st.KIND]["params"]
            for field in ("sheet", "ref_sheet"):
                if field not in params:
                    continue
                _sid, _sheet, table = dc._table(dataset, params[field])
                assert table is not None, (
                    f"{addr}/{t['id']}: листа {params[field]!r} нет в "
                    f"переведённом наборе")
                for row in table["rows"]:
                    assert isinstance(row, dict)
                    assert set(row) == set(table["columns"])
                    assert all(isinstance(v, str) for v in row.values())


def _refuse(world, dicts) -> str:
    with pytest.raises(st.StandWorldError) as e:
        st.world_to_dataset(world, dicts)
    return str(e.value)


class _Twice(dict):
    """Объект таблиц, отдающий один и тот же ключ дважды: в объекте JSON
    повторного ключа не бывает, а страж повтора листа обязан говорить
    словами — иначе он необсуждаемая догадка."""

    def items(self):
        return list(super().items()) * 2


def test_T4_form_rules_refuse_in_words():
    dicts = _catalog().dictionaries() or {}
    seed = st.load_seed()
    base = _task(seed, "families/rest_floors", "st-rest-away-1")["world"]

    def mutated(fn):
        w = copy.deepcopy(base)
        fn(w)
        return w

    def rows(w):
        return w["tables"]["S1/Смены"]["rows"]

    assert "Ф4" in _refuse(mutated(lambda w: rows(w)[0].update(
        {"Начало": 6})), dicts)
    assert "ключи строки" in _refuse(mutated(lambda w: rows(w)[0].pop(
        "Конец")), dicts)
    assert "не объект" in _refuse(mutated(lambda w: rows(w).__setitem__(
        0, ["P1", "2026-10-01 06:00", "2026-10-01 14:00", "ST1", "ST2"])),
        dicts)
    assert "не синтетический" in _refuse(mutated(
        lambda w: w["tables"].__setitem__(
            "S99/Смены", w["tables"].pop("S1/Смены"))), dicts)
    assert "раздела истории" in _refuse(mutated(
        lambda w: w["tables"].__setitem__(
            "S1/История", w["tables"].pop("S1/Смены"))), dicts)
    w = copy.deepcopy(base)
    w["tables"] = _Twice(w["tables"])
    assert "повтор листа" in _refuse(w, dicts)
    # Ф5, положительная часть: коды колонок «База приписки» / «Место
    # отдыха» / «Тип ВС» и табельные — по словарю каталога.
    assert "код станции" in _refuse(mutated(lambda w: rows(w)[0].update(
        {"Место отдыха": "ДМД"})), dicts)
    assert "код станции" in _refuse(mutated(lambda w: rows(w)[0].update(
        {"База приписки": "Домодедово"})), dicts)
    assert "табельный" in _refuse(mutated(lambda w: rows(w)[0].update(
        {"Табельный номер": "12345"})), dicts)
    fleet = copy.deepcopy(
        _task(seed, "families/fleet_exclusions", "st-fleet-valid-0")["world"])
    fleet["tables"]["S1/Допуски"]["rows"][0]["Тип ВС"] = "Суперджет"
    assert "тип судна" in _refuse(fleet, dicts)
    # Ф1 и Ф6
    assert "Ф1" in _refuse({"form": "schedule", "tables": {}}, dicts)
    assert "Ф6" in _refuse({"form": "tables", "tables": {}}, dicts)


# ------------------------------------------------ T5: ожидания сходятся
def test_T5_all_expectations_hold():
    cat = _catalog()
    seed = st.load_seed()
    results, sources = st.run_all(cat, seed=seed)
    st.print_results(results, sources)
    assert len(results) == sum(len(v) for v in seed.values()), (
        "стенд прогнал не все задачи посева")
    bad = st.unexpected_failures(results)
    assert not bad, ("стенд CDPM: расхождения с ожиданиями:\n  " + "\n  ".join(
        f"{r['record']}/{r['task']}: {r['note']}" for r in bad))
    for r in results:
        if r["result"] != "passed":
            assert r["note"], f"{r['record']}/{r['task']}: исход без слов"
        if r["record"] in st.RESERVATIONS and r["result"] == "passed":
            assert st.RESERVATIONS[r["record"]] in r["note"], (
                f"{r['record']}/{r['task']}: заметка без оговорки записи")


def test_T5_report_of_a_task_with_findings_passes_the_contract():
    """Судья стенда — та же дорога, что у отчёта данных: спецификация
    стоит на строке свода R-STAND, отчёт проходит контракт, находки
    класса «нарушение правила» со ссылкой на строку."""
    seed = st.load_seed()
    dicts = _catalog().dictionaries() or {}
    task = _task(seed, "families/duty_limits", "st-duty-hours-2")
    exp = task["expected"][st.KIND]
    dataset = st.world_to_dataset(task["world"], dicts)
    svod = st.svod_of(task)
    spec = st.spec_of(task, exp)
    assert dc.validate_spec(spec, dataset, st.svod_rules_of(svod)) is None
    report = dc.build_data_report("стенд", dataset, [spec],
                                  sanity_threshold=False, svod=svod)
    assert dr.validate_report(report, svod) == []
    assert [f["class"] for f in report["findings"]] == \
        ["нарушение правила"] * 2
    assert all(f.get("rule_refs") == [st.RULE_ID]
               for f in report["findings"])
    assert report["counters"]["by_class"] == {"нарушение правила": 2}
    assert report["counters"]["checks_run"] == 1


# ---------------------------------------------------- T6: детерминизм
def test_T6_run_is_deterministic():
    cat = _catalog()
    seed = st.load_seed()
    first, sources = st.run_all(cat, seed=seed)
    second, sources2 = st.run_all(cat, seed=seed)
    assert first == second and sources == sources2, \
        "два прогона стенда дали разные результаты"
    a = dump_json(st.results_doc(cat, first, tasks_source=sources,
                                 commit=COMMIT, at=AT))
    b = dump_json(st.results_doc(cat, second, tasks_source=sources2,
                                 commit=COMMIT, at=AT))
    assert a == b, "одинаковые входы дали разный текст файла результатов"
    assert a.endswith("\n") and "\r" not in a, \
        "форма файла: перевод строки LF и завершающий перевод строки"


# --------------------------------------------------- T7: отказы словами
def test_T7_refusals_are_words():
    cat = _catalog()
    seed = st.load_seed()
    dicts = cat.dictionaries() or {}
    task = _task(seed, "families/duty_limits", "st-duty-hours-2")

    # шаблон сертификата: исполнителя у CDPM нет — skipped со словами
    cert = copy.deepcopy(task)
    cert["expected"][st.KIND]["template"] = "мера-мягкого-правила"
    rec = {"id": "duty_limits", "check_templates": ["мера-мягкого-правила"]}
    out = st.run_task(rec, cert, dicts, address="families/duty_limits")
    assert out["result"] == "skipped" and out["note"] == st.NOTE_CERTIFICATE

    # имя вне перечня и префиксов — слова генератора привязок
    unknown = copy.deepcopy(task)
    unknown["expected"][st.KIND]["template"] = "проверка-которой-нет"
    rec = {"id": "duty_limits", "check_templates": ["проверка-которой-нет"]}
    out = st.run_task(rec, unknown, dicts, address="families/duty_limits")
    assert out["result"] == "skipped", out
    assert "заявка на исполнителя, а не молчание" in out["note"], out["note"]

    record = _records(cat)["families/duty_limits"]

    # листа нет в мире — спецификацию бракует validate_spec
    gone = copy.deepcopy(task)
    gone["expected"][st.KIND]["params"]["sheet"] = "S1/Дежурства"
    out = st.run_task(record, gone, dicts, address="families/duty_limits")
    assert out["result"] == "failed", out
    assert "спецификация отбракована" in out["note"] and \
        "S1/Дежурства" in out["note"], out["note"]

    # ожидание не сходится
    wrong = copy.deepcopy(task)
    wrong["expected"][st.KIND]["findings"] = 1
    out = st.run_task(record, wrong, dicts, address="families/duty_limits")
    assert out["result"] == "failed"
    assert out["note"].startswith("получено 2, ожидалось 1"), out["note"]

    # окна на границе среза без листа свойств и при срезе в день первой
    # смены — «частично прогнана», а не тихий ноль
    roll_rec = _records(cat)["families/rolling_rest"]
    roll = _task(seed, "families/rolling_rest", "st-rolling-rest-0")
    no_props = copy.deepcopy(roll)
    no_props["world"]["tables"].pop("S1/Свойства")
    out = st.run_task(roll_rec, no_props, dicts,
                      address="families/rolling_rest")
    assert out["result"] == "failed" and "частично прогнана" in out["note"], \
        out["note"]
    same_day = copy.deepcopy(roll)
    same_day["world"]["tables"]["S1/Свойства"]["rows"] = [
        {"ключ": "planSince", "значение": "2026-10-01T00:00"}]
    out = st.run_task(roll_rec, same_day, dicts,
                      address="families/rolling_rest")
    assert out["result"] == "failed" and "частично прогнана" in out["note"], \
        out["note"]


# ------------------------------------------------- T8: файл результатов
def test_T8_results_file_shape_and_staleness(tmp_path, capsys):
    cat = _catalog()
    out = tmp_path / "stand_results.json"
    assert st.main(["--out", str(out), "--commit", COMMIT, "--at", AT]) == 0
    capsys.readouterr()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["form_version"] == st.FORM_VERSION
    assert doc["catalog_version"] == cat.version()
    assert doc["catalog_fingerprint"] == cat.fingerprint()
    assert doc["commit"] == COMMIT and doc["at"] == AT
    assert doc["solver"] is None
    assert set(doc["code"]) == set(st.CODE_FILES)
    for name in ("cdpm/model.py", "cdpm/history_journal.py",
                 "cdpm/stand_tables_seed.json"):
        assert name in doc["code"], name
    assert doc["results"] and doc["tasks_source"]
    assert set(doc["tasks_source"].values()) <= {"seed", "catalog"}
    for r in doc["results"]:
        assert set(r) == {"record", "task", "executor_kind", "result", "got",
                          "expected", "note", "run_id"}, sorted(r)
        assert r["executor_kind"] == st.KIND
        assert r["result"] in ("passed", "failed", "skipped")
        if r["result"] == "passed":
            # Шага 6 достигает только отчёт, прошедший контракт данных
            # (шаг 5 run_task), — иначе исход был бы failed со словами.
            assert r["note"].startswith("получено "), r["note"]
    assert st.stale_reason(doc, cat) is None

    other = copy.deepcopy(doc)
    other["catalog_fingerprint"] = "0" * 64
    why = st.stale_reason(other, cat)
    assert why and "отпечаток каталога" in why, why
    for name in ("cdpm/model.py", "cdpm/history_journal.py",
                 "cdpm/data_checks.py"):
        other = copy.deepcopy(doc)
        other["code"][name] = "0" * 64
        why = st.stale_reason(other, cat)
        assert why and name in why, (name, why)
    other = copy.deepcopy(doc)
    other["form_version"] = 0
    assert "форма результатов" in (st.stale_reason(other, cat) or "")


# --------------------------------------------------- T9: слияние в привязки
def test_T9_stand_rows_are_merged_only_for_catalog_tasks(tmp_path, capsys,
                                                         monkeypatch):
    seed = st.load_seed()
    live = _catalog()
    live_results, live_sources = st.run_all(live, seed=seed)
    live_doc = st.results_doc(live, live_results, tasks_source=live_sources,
                              commit=COMMIT, at=AT)
    built = bb.build(live, commit=COMMIT, generated_at=AT,
                     stand_results=live_doc)
    if not any(t for rec in _records(live).values()
               for t in (rec.get("stand_tasks") or [])
               if (t.get("world") or {}).get("form") == st.WORLD_FORM):
        assert all(not row["stand_verified"] for row in built["bindings"]), (
            "каталог задач формы tables не несёт — строк stand_verified быть "
            "не должно (иначе читатель скажет B05)")

    # Каталог с задачами: строки появляются, и читатель их принимает.
    folder = _copy_with_tasks(tmp_path, seed)
    cat = _use(monkeypatch, folder)
    results, sources = st.run_all(cat, seed=seed)
    assert set(sources.values()) == {"catalog"}, sources
    doc = st.results_doc(cat, results, tasks_source=sources, commit=COMMIT,
                         at=AT)
    built = bb.build(cat, commit=COMMIT, generated_at=AT, stand_results=doc)
    rows = {f"{r['section']}/{r['id']}": r["stand_verified"]
            for r in built["bindings"]}
    total = 0
    for addr, tasks in seed.items():
        got = rows.get(addr) or []
        assert [x["task"] for x in got] == sorted(t["id"] for t in tasks), (
            f"{addr}: строки стенда {got}")
        for x in got:
            assert set(x) == {"task", "executor_kind", "result", "at",
                              "commit", "note"}, sorted(x)
            assert x["executor_kind"] == st.KIND and x["at"] == AT
            assert x["commit"] == COMMIT and x["note"]
        total += len(got)
    assert total == sum(len(v) for v in seed.values())
    fleet = rows.get("families/fleet_exclusions") or []
    assert all(st.RESERVATIONS["families/fleet_exclusions"] in x["note"]
               for x in fleet), fleet
    errors = [str(p) for p in check_bindings(cat, built)
              if p.severity == "error" and p.code != "B04"]
    assert not errors, ("читатель каталога нашёл ошибки привязок со "
                        "стендовыми строками:\n  " + "\n  ".join(errors))

    # Устаревшие результаты — отказ словами, файл не выпускается.
    stale = copy.deepcopy(doc)
    stale["catalog_fingerprint"] = "0" * 64
    with pytest.raises(bb.BindingsBuildError) as e:
        bb.build(cat, commit=COMMIT, generated_at=AT, stand_results=stale)
    assert "результаты стенда устарели" in str(e.value), str(e.value)

    # Команда: файл со строками сверяется с теми же результатами и
    # расходится без них.
    res = tmp_path / "stand_results.json"
    st.write_results(res, doc)
    path = tmp_path / "bindings.json"
    capsys.readouterr()
    assert bb.main(["--out", str(path), "--commit", COMMIT,
                    "--generated-at", AT,
                    "--stand-results", str(res)]) == 0
    out = capsys.readouterr().out
    assert "стенд: passed 15" in out, out
    assert bb.main(["--check", str(path), "--commit", COMMIT,
                    "--generated-at", AT,
                    "--stand-results", str(res)]) == 0
    capsys.readouterr()
    assert bb.main(["--check", str(path), "--commit", COMMIT,
                    "--generated-at", AT]) == 1
    out = capsys.readouterr().out
    assert "стендовые подтверждения" in out, out


def test_T9_stand_rows_ignore_the_run_stamp(tmp_path, monkeypatch):
    """Перезапуск стенда без изменений (иные `at`/`commit`) не делает
    файл привязок отставшим."""
    seed = st.load_seed()
    cat = _use(monkeypatch, _copy_with_tasks(tmp_path, seed))
    results, sources = st.run_all(cat, seed=seed)
    first = st.results_doc(cat, results, tasks_source=sources, commit=COMMIT,
                           at=AT)
    second = copy.deepcopy(first)
    second["at"] = "2026-09-08T09:00:00+03:00"
    second["commit"] = "1" * 40
    a = bb.build(cat, commit=COMMIT, generated_at=AT, stand_results=first)
    b = bb.build(cat, commit=COMMIT, generated_at=AT, stand_results=second)
    assert not bb.differences(a, b), bb.differences(a, b)


# ------------------------------------------- T10: живой файл результатов
def test_T10_released_results_file_matches_a_fresh_run():
    if not RESULTS.is_file():
        pytest.skip("результаты стенда CDPM не выпущены")
    cat = _catalog()
    doc = st.read_results(RESULTS)
    why = st.stale_reason(doc, cat)
    assert why is None, (
        f"{RESULTS} устарел: {why}; перегенерируйте — python -m "
        f"cdpm.stand_tables --out cdpm/stand_results.json")
    results, _sources = st.run_all(cat, seed=st.load_seed())
    diff = st.results_differences(results, doc.get("results") or [])
    assert not diff, ("живой файл результатов разошёлся с повторным "
                      "прогоном:\n  " + "\n  ".join(diff))


# --------------------------------------------------------- T11: команда
def test_T11_command_returns_zero_on_green_seed(capsys):
    assert st.main([]) == 0
    out = capsys.readouterr().out
    assert "стенд CDPM: задач" in out and "failed 0" in out, out


def test_T11_command_returns_one_when_an_expectation_fails(tmp_path, capsys):
    doc = json.loads((ROOT / "cdpm" / "stand_tables_seed.json")
                     .read_text(encoding="utf-8"))
    for t in doc["records"]["families/duty_limits"]:
        if t["id"] == "st-duty-hours-2":
            t["expected"]["check_template"]["findings"] = 1
    seed = tmp_path / "seed.json"
    seed.write_bytes(dump_json(doc).encode("utf-8"))
    capsys.readouterr()
    assert st.main(["--seed", str(seed), "--tasks", "seed"]) == 1
    out = capsys.readouterr().out
    assert "получено 2, ожидалось 1" in out, out
    assert "failed 1" in out, out


def test_T11_command_refuses_without_catalog(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(bridge.ENV_CATALOG, str(tmp_path / "нет-каталога"))
    bridge.forget()
    out_file = tmp_path / "stand_results.json"
    assert st.main(["--out", str(out_file)]) == 2
    out = capsys.readouterr().out
    assert "каталог шаблонов недоступен" in out, out
    assert not out_file.exists(), "при отказе файл результатов не пишется"


def test_T12_code_fingerprints_ignore_line_endings(tmp_path, monkeypatch):
    """Отпечатки кода стенда не зависят от перевода строки: чистый worktree
    на Windows отдаёт файлы с CRLF, рабочая копия — с LF; иначе результаты
    стенда «устаревают» при равном коде (красный полный pytest 08.09)."""
    lf = tmp_path / "a.py"
    crlf = tmp_path / "b.py"
    lf.write_bytes(b"x = 1\nprint(x)\n")
    crlf.write_bytes(b"x = 1\nprint(x)\n")
    monkeypatch.setattr(st, "CODE_FILES", ("a.py", "b.py"))
    monkeypatch.setattr(st, "REPO", tmp_path)
    fps = st.code_fingerprints()
    assert fps["a.py"] == fps["b.py"], fps

