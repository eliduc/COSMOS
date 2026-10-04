# -*- coding: utf-8 -*-
"""Аудит A3.5 (2026-09-04): состояние находки после арбитража — по
ФАКТУ исполнения вердикта, а не по вердикту.

Дефект: run_arbiter ставил «арбитр: аудитор прав» безусловно по
вердикту, хотя заплата обработчика могла быть отбракована (вне цели,
контракт П-1, не ПРИНЯТО), и «арбитр: в анкету» — хотя вопрос не был
создан (нет строки-носителя). Состояние терминально: аудитору
запрещалось поднимать находку снова, свод оставался с признанным
дефектом, счётчик enforced считал ответы обработчика ДО применения.

Здесь: отбракованная заплата не закрывает находку (остаётся «тупик»,
гейт красный, enforced = 0); «в анкету» без созданного вопроса не
закрывает; исполненный вердикт закрывает как прежде; повторный
арбитраж судит оставшийся тупик заново. Модели подменные — сеть и
ключи не нужны.

Ревью пакета (2026-09-04): заплата-пустышка (тождественная строке)
исполнением не считается; частичная переарбитрация после исполненного
вердикта не теряет принятые заплаты и созданные вопросы других
тупиков (база — model-arbiter.json), а возврат на переарбитрацию
самого исполненного тупика снимает его прежний след."""
import json
import re

import pytest

from cdpm import exchange as ex, model as m
from cdpm.gates import gate_arbiter
from test_cdpm_exchange_run import (   # noqa: F401 — фикстура workspace
    _PATCH_FIX, _REMARK_DISTORT, _REMARK_OMIT, _deadlocked_exchange,
    _enforcing_worker, _make_arbiter, _make_auditor, _make_worker,
    workspace)


def _registry(exdir) -> dict:
    return json.loads((exdir / "registry.json").read_text(encoding="utf-8"))


def _deadlock_ids(exdir) -> list[str]:
    vs = json.loads((exdir / "verdict.json").read_text(encoding="utf-8"))
    return [f["id"] for f in vs.get("deadlocks", [])]


def _state_of(exdir, fid: str) -> str:
    return next(f["state"] for f in _registry(exdir)["findings"]
                if f["id"] == fid)


def _breaking_worker(prompt):
    """Обработчик «исполняет» вердикт заплатой, ломающей контракт
    П-1 (жёсткая строка без формулировки) — _apply_patches её
    отбраковывает."""
    got = json.loads(prompt.split("Замечания к исполнению:\n", 1)[1]
                     .split("\n\nЗатронутые строки", 1)[0])
    broken = dict(_PATCH_FIX, rule=dict(_PATCH_FIX["rule"], statement=""))
    return json.dumps({"answers": [
        {"remark": rm["id"], "verdict": "ПРИНЯТО",
         "basis": "арбитраж", "change": "правка", "patch": broken}
        for rm in got]}, ensure_ascii=False)


def _refusing_worker(prompt):
    """Обработчик отвечает ОТКЛОНЕНО — арбитраж окончателен, ответ не
    засчитывается как исполнение."""
    got = json.loads(prompt.split("Замечания к исполнению:\n", 1)[1]
                     .split("\n\nЗатронутые строки", 1)[0])
    return json.dumps({"answers": [
        {"remark": rm["id"], "verdict": "ОТКЛОНЕНО",
         "basis": "не согласен и после арбитража"} for rm in got]},
        ensure_ascii=False)


def test_rejected_patch_does_not_close_the_finding(workspace):
    """Вердикт «аудитор прав», заплата отбракована контрактом П-1:
    находка остаётся «тупик» с названной причиной, enforced = 0,
    гейт красный и называет тупик."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    assert ids
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         _breaking_worker)
    assert rep["by_verdict"]["аудитор прав"] == len(ids)
    assert rep["enforced"] == 0 and rep["enforced_deadlocks"] == []
    assert rep["patch_rejections"], "отбраковка обязана быть названа"
    assert [u["deadlock"] for u in rep["unenforced"]] == ids
    for fid in ids:
        assert _state_of(exdir, fid) == "тупик"
        entry = next(f for f in _registry(exdir)["findings"]
                     if f["id"] == fid)
        assert "не исполнен" in entry["arbiter_unenforced"]
    # Строка-цель не изменилась: свод не «исправлен».
    final = m.load_model(exdir / "versions" / rep["final_version"])
    rule = next(r for r in final["rules"] if r["id"] == "R-S1-п-1-9")
    assert "(на базе)" not in rule["statement"]
    problems, counters = gate_arbiter.check(exdir)
    assert counters["enforced"] == 0
    assert any(f"тупик {ids[0]}" in p and "не исполнен" in p
               for p in problems), problems


def test_refused_enforcement_does_not_close_the_finding(workspace):
    """Ответ «ОТКЛОНЕНО» на обязательное исполнение: находка остаётся
    открытой, гейт красный."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         _refusing_worker)
    assert rep["enforced"] == 0
    assert all(_state_of(exdir, fid) == "тупик" for fid in ids)
    problems, _ = gate_arbiter.check(exdir)
    assert any("не исполнен" in p for p in problems), problems


def test_silent_worker_does_not_close_the_finding(workspace):
    """Обработчик не ответил вовсе: молчание — не исполнение. Ответ
    без строки на замечание отказывается ещё на стадии ответа
    (ExchangeError с названным замечанием), реестр не тронут —
    находка остаётся «тупик»."""
    import pytest
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    with pytest.raises(ex.ExchangeError, match="без ответа"):
        ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                       lambda prompt: json.dumps({"answers": []}))
    assert all(_state_of(exdir, fid) == "тупик" for fid in ids)


def test_questionnaire_verdict_without_question_stays_open(workspace):
    """Вердикт «в анкету» для пропуска без строки-носителя (якорь вне
    свода): вопрос не создан — находка остаётся «тупик», гейт красный,
    объяснение в отбраковке не извиняет."""
    kit, out, exdir = workspace
    orphan = dict(_REMARK_OMIT,
                  source_refs=[{"sid": "S1",
                                "anchor": "[табл.9, строка 9, кол 9]"}])
    same = [orphan]
    ex.run_exchange(kit, out, exdir, _make_auditor([same] * 4),
                    _make_worker({}), max_rounds=6)
    ids = _deadlock_ids(exdir)
    assert ids
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("в анкету"),
                         _enforcing_worker)
    assert rep["by_verdict"]["в анкету"] == len(ids)
    assert rep["added_questions"] == [] and rep["question_reuse"] == {}
    assert any(any("вопрос не добавлен" in x for x in r["reasons"])
               for r in rep["patch_rejections"])
    assert [u["deadlock"] for u in rep["unenforced"]] == ids
    assert all(_state_of(exdir, fid) == "тупик" for fid in ids)
    problems, _ = gate_arbiter.check(exdir)
    assert any("«в анкету» не исполнен" in p for p in problems), problems


def test_questionnaire_verdict_on_question_target_reuses_it(workspace):
    """Цель замечания — уже существующий вопрос анкеты (кодовый вопрос о
    целевой, линии данных, истории либо вопрос части 2): развилка в анкете
    уже стоит этим вопросом, поэтому вердикт «в анкету» исполняется его
    переиспользованием (question_reuse), а не постановкой нового вопроса
    без строки-носителя. Живой прогон H0-КИ-окна №3 (05.09.2026): два
    тупика «выбор чтения» по Q-3a/3b-GOAL держали гейт арбитража красным
    «без строки-носителя: вопрос не добавлен»."""
    kit, out, exdir = workspace
    doc = m.load_model(out / "model.json")
    qid = doc["questions"][0]["id"]
    rm = dict(_REMARK_OMIT, defect_class="выбор чтения",
              target={"question": qid},
              text="Вопрос ссылается на нерелевантный абзац, а источники "
                   "конфликтуют о наличии критерия")
    same = [rm]
    ex.run_exchange(kit, out, exdir, _make_auditor([same] * 4),
                    _make_worker({}), max_rounds=6)
    ids = _deadlock_ids(exdir)
    assert ids
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("в анкету"),
                         _enforcing_worker)
    assert rep["by_verdict"]["в анкету"] == len(ids)
    assert rep["added_questions"] == []
    assert rep["question_reuse"] == {fid: qid for fid in ids}
    assert rep["unenforced"] == []
    assert all(_state_of(exdir, fid) == "арбитр: в анкету" for fid in ids)
    problems, _ = gate_arbiter.check(exdir)
    assert not any("не исполнен" in p or "вопрос не добавлен" in p
                   for p in problems), problems


def test_executed_verdicts_close_as_before(workspace):
    """Исполненный вердикт закрывает находку как прежде: «аудитор
    прав» с принятой заплатой — «арбитр: аудитор прав», enforced по
    факту, гейт зелёный."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         _enforcing_worker)
    assert rep["enforced"] == len(ids) == len(rep["enforced_deadlocks"])
    assert rep["unenforced"] == []
    for fid in ids:
        assert _state_of(exdir, fid) == "арбитр: аудитор прав"
        entry = next(f for f in _registry(exdir)["findings"]
                     if f["id"] == fid)
        assert "arbiter_unenforced" not in entry
    problems, counters = gate_arbiter.check(exdir)
    assert problems == [], problems
    assert counters["enforced"] == len(ids)


def test_gate_red_when_registry_closed_without_execution(workspace):
    """Реестр, закрывший находку без исполнения (подмена руками или
    прежний код), — красный гейт с названным тупиком."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                   _breaking_worker)
    reg = _registry(exdir)
    for f in reg["findings"]:
        if f["id"] in ids:
            f["state"] = "арбитр: аудитор прав"
    (exdir / "registry.json").write_text(
        json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    problems, _ = gate_arbiter.check(exdir)
    assert any("закрыто без исполнения" in p for p in problems), problems


def test_unenforced_deadlock_is_rearbitrated_and_closes_when_fixed(
        workspace):
    """Оставшийся открытым тупик судится повторным арбитражем заново
    и закрывается, когда заплата принята; аудитору находка между
    заходами не запрещена (нет ключа «требование арбитра»)."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    rep1 = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                          _breaking_worker)
    assert rep1["enforced"] == 0
    rep2 = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                          _enforcing_worker)
    assert rep2["enforced"] == len(ids)
    assert rep2["unenforced"] == []
    assert all(_state_of(exdir, fid) == "арбитр: аудитор прав"
               for fid in ids)
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


# ── Ревью пакета: заплата-пустышка и частичная переарбитрация ──

def _two_deadlocks(kit, out, exdir):
    """Цикл с двумя тупиками: искажение строки R-S1-п-1-9 и пропуск
    (цель «+»). Возвращает (id тупика-искажения, id другого)."""
    auditor = _make_auditor(
        [[dict(_REMARK_DISTORT), dict(_REMARK_OMIT)]] * 4)
    ex.run_exchange(kit, out, exdir, auditor, _make_worker({}),
                    max_rounds=6)
    vs = json.loads((exdir / "verdict.json").read_text(encoding="utf-8"))
    keys = {f["id"]: f.get("key") or "" for f in vs["deadlocks"]}
    assert len(keys) == 2, keys
    distort = next(i for i, k in keys.items() if "R-S1-п-1-9" in k)
    other = next(i for i in keys if i != distort)
    return distort, other


def _arbiter_by_id(mapping):
    """Арбитр с вердиктом на каждый тупик по его id."""
    def arbiter(prompt):
        ids = re.findall(r"Тупик \[([^\]]+)\]", prompt)
        return json.dumps({"verdicts": [
            {"deadlock": i, "verdict": mapping[i],
             "basis": "по клаузе источника"} for i in ids]},
            ensure_ascii=False)
    return arbiter


def _return_to_deadlock(exdir, fid):
    """Возврат находки на переарбитрацию — так же, как оператор руками
    (живой случай 2026-08-18)."""
    reg = _registry(exdir)
    for f in reg["findings"]:
        if f["id"] == fid:
            f["state"] = "тупик"
    (exdir / "registry.json").write_text(
        json.dumps(reg, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")


def _arbiter_model(exdir) -> dict:
    return m.load_model(exdir / "versions" / "model-arbiter.json")


def _statement(exdir, rid="R-S1-п-1-9") -> str:
    return next(r for r in _arbiter_model(exdir)["rules"]
                if r["id"] == rid)["statement"]


def test_noop_patch_does_not_close_the_finding(workspace):
    """Заплата-пустышка: ответ ПРИНЯТО с заплатой, тождественной
    текущей строке. Исполнением не считается — «тупик», enforced = 0,
    причина «заплата не меняет строку»; производитель и гейт согласны:
    гейт красный по «не исполнен», «числится исполненным» не звучит."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    vs = json.loads((exdir / "verdict.json").read_text(encoding="utf-8"))
    base = m.load_model(exdir / "versions" / vs["final_version"])
    rule = next(r for r in base["rules"] if r["id"] == "R-S1-п-1-9")

    def noop_worker(prompt):
        got = json.loads(prompt.split("Замечания к исполнению:\n", 1)[1]
                         .split("\n\nЗатронутые строки", 1)[0])
        return json.dumps({"answers": [
            {"remark": rm["id"], "verdict": "ПРИНЯТО",
             "basis": "арбитраж: цитата", "change": "правка",
             "patch": {"rule_id": rule["id"], "rule": dict(rule)}}
            for rm in got]}, ensure_ascii=False)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         noop_worker)
    assert rep["enforced"] == 0 and rep["enforced_deadlocks"] == []
    assert [u["deadlock"] for u in rep["unenforced"]] == ids
    assert all("не меняет строку" in u["reason"]
               for u in rep["unenforced"])
    assert any(any("не меняет строку" in x for x in r["reasons"])
               for r in rep["patch_rejections"])
    assert all(_state_of(exdir, fid) == "тупик" for fid in ids)
    problems, counters = gate_arbiter.check(exdir)
    assert counters["enforced"] == 0
    assert any("не исполнен" in p for p in problems), problems
    assert not any("числится исполненным" in p for p in problems), problems


def test_partial_rerun_keeps_executed_patch(workspace):
    """Частичная переарбитрация ПОСЛЕ исполненного вердикта: заход 1 —
    «аудитор прав» с принятой заплатой для одного тупика; заход 2 —
    возврат ДРУГОГО тупика на повторный суд. Заплата остаётся в
    model-arbiter.json, enforced_deadlocks сохранён, реестр закрыт,
    гейт зелёный (прежде база бралась доарбитражной — заплата исчезала,
    гейт краснел «числится исполненным, но строка не изменена»)."""
    kit, out, exdir = workspace
    distort, other = _two_deadlocks(kit, out, exdir)
    rep1 = ex.run_arbiter(kit, exdir, _arbiter_by_id(
        {distort: "аудитор прав", other: "обработчик прав"}),
        _enforcing_worker)
    assert rep1["enforced_deadlocks"] == [distort]
    assert "(на базе)" in _statement(exdir)
    assert gate_arbiter.check(exdir)[0] == []

    _return_to_deadlock(exdir, other)
    rep2 = ex.run_arbiter(kit, exdir, _arbiter_by_id(
        {other: "обработчик прав"}), _enforcing_worker)
    assert rep2["deadlocks"] == 2
    assert rep2["enforced_deadlocks"] == [distort]
    assert rep2["enforced"] == 1 and rep2["unenforced"] == []
    assert "(на базе)" in _statement(exdir)      # заплата не потеряна
    assert _state_of(exdir, distort) == "арбитр: аудитор прав"
    assert _state_of(exdir, other) == "арбитр: обработчик прав"
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_partial_rerun_keeps_questionnaire_question(workspace):
    """Зеркально для «в анкету»: вопрос арбитража первого захода живёт
    в модели после переарбитрации другого тупика, строка-носитель
    заблокирована, added_questions слит, гейт зелёный."""
    kit, out, exdir = workspace
    distort, other = _two_deadlocks(kit, out, exdir)
    rep1 = ex.run_arbiter(kit, exdir, _arbiter_by_id(
        {distort: "в анкету", other: "обработчик прав"}),
        _enforcing_worker)
    qid = m.stable_question_id("2", f"арб-{distort}")
    assert rep1["added_questions"] == [qid]
    assert gate_arbiter.check(exdir)[0] == []

    _return_to_deadlock(exdir, other)
    rep2 = ex.run_arbiter(kit, exdir, _arbiter_by_id(
        {other: "обработчик прав"}), _enforcing_worker)
    assert rep2["added_questions"] == [qid]
    doc = _arbiter_model(exdir)
    assert any(q["id"] == qid for q in doc["questions"])
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-п-1-9")
    assert qid in rule["blockers"]
    assert _state_of(exdir, distort) == "арбитр: в анкету"
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_rerun_of_executed_deadlock_drops_its_trace(workspace):
    """Возврат на переарбитрацию САМОГО исполненного тупика: прежняя
    заплата снимается (строка — доарбитражная), новый вердикт
    «обработчик прав» — след исполнения уходит из отчёта, гейт
    зелёный. Вердикт на тупик один и последний — и след его
    исполнения тоже."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    rep1 = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                          _enforcing_worker)
    assert rep1["enforced_deadlocks"] == ids
    assert "(на базе)" in _statement(exdir)
    for fid in ids:
        _return_to_deadlock(exdir, fid)
    rep2 = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                          _enforcing_worker)
    assert rep2["enforced"] == 0 and rep2["enforced_deadlocks"] == []
    assert "(на базе)" not in _statement(exdir)
    assert all(_state_of(exdir, fid) == "арбитр: обработчик прав"
               for fid in ids)
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_rerun_of_questioned_deadlock_drops_its_question(workspace):
    """То же для «в анкету»: возвращённый тупик теряет свой вопрос и
    блокировку строки-носителя; новый вердикт «обработчик прав» —
    модель равна доарбитражной, гейт зелёный."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    rep1 = ex.run_arbiter(kit, exdir, _make_arbiter("в анкету"),
                          _enforcing_worker)
    qids = [m.stable_question_id("2", f"арб-{i}") for i in ids]
    assert rep1["added_questions"] == qids
    for fid in ids:
        _return_to_deadlock(exdir, fid)
    rep2 = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                          _enforcing_worker)
    assert rep2["added_questions"] == []
    doc = _arbiter_model(exdir)
    assert not any(q["id"] in qids for q in doc["questions"])
    rule = next(r for r in doc["rules"] if r["id"] == "R-S1-п-1-9")
    assert not set(qids) & set(rule.get("blockers") or [])
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


# ── Третье неавтор-ревью дельты A07 (12.09.2026): реестр у арбитража и
# его гейта читается ТЕМ ЖЕ загрузчиком продукта, что у цикла и панели.

def test_arbiter_and_its_gate_read_registry_through_one_loader(workspace):
    """Арбитраж оставался последним читателем реестра «через
    json.loads»: негодная форма (посторонняя версия схемы) проезжала
    молча — арбитр судил по ней, гейт сверял по ней же. Теперь и
    `run_arbiter`, и `gate_arbiter` отказывают СЛОВАМИ; законная форма
    — как прежде, гейт зелёный.

    A07 (четвёртое неавтор-ревью, находка 2): гейт отказывает КРАСНОЙ
    СТРОКОЙ в перечне проблем, как gate_exchange, а не трассировкой
    ExchangeError из CLI — аналитик читает причину, а не стек.
    Производитель (`run_arbiter`) по-прежнему падает исключением:
    писать по негодной форме он не вправе."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         _enforcing_worker)
    assert rep["enforced"] == len(_deadlock_ids(exdir))
    assert gate_arbiter.check(exdir)[0] == [], gate_arbiter.check(exdir)[0]
    good = _registry(exdir)
    (exdir / "registry.json").write_text(
        json.dumps(dict(good, schema_version="registry/9"),
                   ensure_ascii=False), encoding="utf-8")
    problems, _c = gate_arbiter.check(exdir)
    assert any("версия схемы" in p for p in problems), problems
    with pytest.raises(ex.ExchangeError, match="версия схемы"):
        ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                       _enforcing_worker)
    # Положительный контроль: форму вернули — гейт снова зелёный.
    (exdir / "registry.json").write_text(
        json.dumps(good, ensure_ascii=False), encoding="utf-8")
    assert gate_arbiter.check(exdir)[0] == []
