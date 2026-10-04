# -*- coding: utf-8 -*-
"""A09 (аудит «четыре принципа ВМП», CONS-04, fcec7a2e): два
противоречивых решения арбитра по одному тупику не становятся «первым
из двух».

Ожидания — по контракту П0 §1.3 с поправкой §12.2 («любой дубль после
повтора не решение») и контрпримеру аудитора
(`consistency/probe_consistency.py::duplicate_arbiter_probe`) с
ИНВЕРСИЕЙ: раньше после повтора принималось первое решение.

Охраняемое:
- `judge.batch_verdicts(...)` возвращает `(out, unresolved)`: тупик с
  противоречивыми повторами после повтора попадает в `unresolved` с
  причиной, а не в `out`;
- одинаковые повторы — тоже `unresolved` (§12.2: дедупликации
  одинаковых нет);
- прежний `judge._batch_verdicts(...)` (устаревшие вызовы) при
  неразрешённых идентификаторах отказывает исключением, а не отдаёт
  первое решение;
- отчёт арбитра несёт `unresolved`, состояние реестра — «не разрешено
  арбитром», и гейт арбитража зелёный при явной записи.

Второй раздел файла (реализация A09, 12.09.2026) держит тот же закон
на живом `run_arbiter`: реестр, отчёт, гейт и связь с A07."""
import json
import re as _re

import pytest

from cdpm import exchange as ex
from cdpm import judge
from cdpm import model as m
from cdpm.gates import gate_arbiter
from test_cdpm_exchange_run import (   # noqa: F401 — фикстура workspace
    _REMARK_DISTORT, _REMARK_OMIT, _deadlocked_exchange,
    _enforcing_worker, _make_arbiter, _make_auditor, _make_worker,
    workspace)


def _dup_reply(calls, first="обработчик прав", second="аудитор прав"):
    def llm(_prompt):
        calls.append(1)
        return json.dumps({"verdicts": [
            {"deadlock": "D1", "verdict": first, "basis": "source first reading"},
            {"deadlock": "D1", "verdict": second, "basis": "source opposite reading"}]},
            ensure_ascii=False)
    return llm


def test_contradictory_duplicates_are_unresolved_not_first():
    calls = []
    out, unresolved = judge.batch_verdicts(
        _dup_reply(calls), "{blocks}", {"D1": "synthetic deadlock"},
        ex.ARBITER_VERDICTS, id_field="deadlock")
    assert len(calls) == 2                       # ровно один повтор
    assert "D1" not in out
    assert "D1" in unresolved and "противореч" in unresolved["D1"]


def test_identical_duplicates_are_unresolved_too():
    """Любой дубль ожидаемого идентификатора после повтора остаётся
    неразрешённым — и одинаковые решения тоже (REPLY-AUDITOR-01/A09,
    GPT-088 п. 2; поправка заготовки по GPT-089): D1 не входит в принятые
    решения и не исчезает из блокеров."""
    calls = []
    out, unresolved = judge.batch_verdicts(
        _dup_reply(calls, "обработчик прав", "обработчик прав"), "{blocks}",
        {"D1": "synthetic deadlock"}, ex.ARBITER_VERDICTS, id_field="deadlock")
    assert len(calls) == 2
    assert "D1" not in out
    assert "D1" in unresolved and "повтор" in unresolved["D1"]


def test_legacy_wrapper_refuses_instead_of_returning_first():
    calls = []
    with pytest.raises(judge.JudgeError, match="D1"):
        judge._batch_verdicts(_dup_reply(calls), "{blocks}",
                              {"D1": "synthetic deadlock"},
                              ex.ARBITER_VERDICTS, id_field="deadlock")


def test_missing_id_after_retry_is_unresolved_too():
    calls = []

    def llm(_prompt):
        calls.append(1)
        return json.dumps({"verdicts": [
            {"deadlock": "D1", "verdict": "обработчик прав", "basis": "b"}]},
            ensure_ascii=False)
    out, unresolved = judge.batch_verdicts(
        llm, "{blocks}", {"D1": "a", "D2": "b"}, ex.ARBITER_VERDICTS,
        id_field="deadlock")
    assert out["D1"]["verdict"] == "обработчик прав"
    assert "D2" in unresolved and len(calls) == 2


def test_unresolved_state_is_a_registry_state():
    assert "не разрешено арбитром" in ex.STATES
    assert "не разрешено арбитром" in ex.LIVE_STATES


# ---------------------------------------------------------------------------
# Живой арбитраж: реестр, отчёт, гейт и связь с A07 (реализация A09)
# ---------------------------------------------------------------------------

#: История метрик, по которой цикл был бы «успешен», если бы реестр
#: был чист: два полных чистых раунда подряд.
_CLEAN_HISTORY = [{"round": 1, "weighty": (0, 0, 0), "mode": "полный"},
                  {"round": 2, "weighty": (0, 0, 0), "mode": "полный"}]


def _registry_of(exdir) -> dict:
    return json.loads((exdir / "registry.json")
                      .read_text(encoding="utf-8"))


def _report_of(exdir) -> dict:
    return json.loads((exdir / "arbiter.json").read_text(encoding="utf-8"))


def _finding(exdir, fid: str) -> dict:
    return next(f for f in _registry_of(exdir)["findings"]
                if f["id"] == fid)


def _deadlock_ids(exdir) -> list[str]:
    vs = json.loads((exdir / "verdict.json").read_text(encoding="utf-8"))
    return [f["id"] for f in vs.get("deadlocks", [])]


def _id_by_text(exdir, needle: str) -> str:
    return next(f["id"] for f in _registry_of(exdir)["findings"]
                if needle in (f.get("text") or ""))


def _dup_arbiter(first="обработчик прав", second="аудитор прав",
                 only: str | None = None):
    """Арбитр, назвавший ОДИН тупик дважды (по умолчанию — первый в
    досье), а остальные — ровно по разу."""
    def arbiter(prompt):
        ids = _re.findall(r"Тупик \[([^\]]+)\]", prompt)
        target = only if only is not None else (ids[0] if ids else None)
        rows = []
        for i in ids:
            if i == target:
                rows.append({"deadlock": i, "verdict": first,
                             "basis": "клауза читается в пользу аудитора"})
                rows.append({"deadlock": i, "verdict": second,
                             "basis": "клауза читается в пользу "
                                      "обработчика"})
            else:
                rows.append({"deadlock": i, "verdict": "обработчик прав",
                             "basis": "клауза подтверждает строку свода"})
        return json.dumps({"verdicts": rows}, ensure_ascii=False)
    return arbiter


def _stranger_arbiter(prompt):
    """Арбитр промолчал о предъявленном споре и назвал посторонний
    идентификатор — ни пропуск, ни лишнее не скрываются."""
    return json.dumps({"verdicts": [
        {"deadlock": "D-ЧУЖОЙ", "verdict": "обработчик прав",
         "basis": "решение о споре, которого не предъявляли"}]},
        ensure_ascii=False)


def test_run_arbiter_leaves_contradictory_deadlock_unresolved(workspace):
    """CONS-04 на живом арбитраже: пара «обработчик прав» / «аудитор
    прав» по одному тупику не превращается в первое решение — спор
    остаётся неразрешённым, живым и блокирующим успех цикла."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    assert len(ids) == 1
    rep = ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)

    assert rep["verdicts"] == []                  # решения нет
    assert sum(rep["by_verdict"].values()) == 0
    assert rep["deadlocks"] == 1                  # спор из счёта не исчез
    assert [u["deadlock"] for u in rep["unresolved"]] == ids
    assert "противореч" in rep["unresolved"][0]["reason"]
    assert rep["enforced"] == 0 and rep["added_questions"] == []

    f = _finding(exdir, ids[0])
    assert f["state"] == "не разрешено арбитром"
    assert "противореч" in f["arbiter_unresolved"]

    problems, counters = gate_arbiter.check(exdir)
    assert problems == []                         # исход назван явно
    assert counters["verdicts"] == 0
    assert counters["unresolved"] == 1            # видно и в счётчиках

    # A07: неразрешённый спор — живая критическая находка, «успех»
    # цикла по метрикам невозможен.
    reg = _registry_of(exdir)
    blockers = {b["id"] for b in ex.registry_blockers(reg)}
    assert set(ids) <= blockers
    assert ex.registry_verdict_of(reg)["count"] == len(blockers)
    assert ex.decide(_CLEAN_HISTORY, 9, registry=reg) \
        == ex.VERDICT_NOT_CLOSED


def test_run_arbiter_leaves_identical_duplicates_unresolved(workspace):
    """§12.2: два ОДИНАКОВЫХ решения по одному тупику — тоже не
    решение; находка не закрывается «арбитр: обработчик прав»."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    rep = ex.run_arbiter(kit, exdir,
                         _dup_arbiter("обработчик прав",
                                      "обработчик прав"),
                         _enforcing_worker)
    assert rep["by_verdict"]["обработчик прав"] == 0
    assert [u["deadlock"] for u in rep["unresolved"]] == ids
    assert "повтор" in rep["unresolved"][0]["reason"]
    assert _finding(exdir, ids[0])["state"] == "не разрешено арбитром"
    problems, _ = gate_arbiter.check(exdir)
    assert problems == []
    assert set(ids) <= {b["id"] for b
                        in ex.registry_blockers(_registry_of(exdir))}


def test_missing_and_unknown_deadlock_ids_are_not_hidden(workspace):
    """Пропущенный идентификатор — «нет решения» с живой находкой;
    лишний (неизвестный) доходит до отчёта и краснит гейт."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    rep = ex.run_arbiter(kit, exdir, _stranger_arbiter,
                         _enforcing_worker)
    by_id = {u["deadlock"]: u["reason"] for u in rep["unresolved"]}
    assert "нет решения" in by_id[ids[0]]
    assert "неизвестный идентификатор" in by_id["D-ЧУЖОЙ"]
    assert _finding(exdir, ids[0])["state"] == "не разрешено арбитром"
    problems, _ = gate_arbiter.check(exdir)
    assert any("неизвестному тупику" in p and "D-ЧУЖОЙ" in p
               for p in problems), problems


def test_unambiguous_decision_of_another_deadlock_survives(workspace):
    """Однозначное решение ДРУГОГО спора сохраняется, а оставшийся
    неразрешённым судится заново следующим заходом (положительный
    контроль: после чистого арбитража успех цикла снова возможен)."""
    kit, out, exdir = workspace
    ex.run_exchange(kit, out, exdir,
                    _make_auditor([[dict(_REMARK_DISTORT),
                                    dict(_REMARK_OMIT)]] * 4),
                    _make_worker({}), max_rounds=6)
    ids = _deadlock_ids(exdir)
    assert len(ids) == 2
    disputed = _id_by_text(exdir, "Потеряно уточнение")
    other = next(i for i in ids if i != disputed)

    rep = ex.run_arbiter(kit, exdir, _dup_arbiter(only=disputed),
                         _enforcing_worker)
    assert [v["deadlock"] for v in rep["verdicts"]] == [other]
    assert rep["by_verdict"]["обработчик прав"] == 1
    assert [u["deadlock"] for u in rep["unresolved"]] == [disputed]
    assert _finding(exdir, other)["state"] == "арбитр: обработчик прав"
    assert _finding(exdir, disputed)["state"] == "не разрешено арбитром"
    problems, _ = gate_arbiter.check(exdir)
    assert problems == []

    rep2 = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                          _enforcing_worker)
    assert rep2["unresolved"] == []
    assert {v["deadlock"] for v in rep2["verdicts"]} == set(ids)
    assert _finding(exdir, disputed)["state"] == "арбитр: обработчик прав"
    assert "arbiter_unresolved" not in _finding(exdir, disputed)
    problems2, _ = gate_arbiter.check(exdir)
    assert problems2 == []
    reg = _registry_of(exdir)
    assert ex.registry_blockers(reg) == []
    assert ex.decide(_CLEAN_HISTORY, 9, registry=reg) == "успех"


def test_gate_is_red_when_the_registry_closes_an_unresolved_deadlock(
        workspace):
    """Отрицательный контроль: неразрешённый спор, закрытый в реестре
    состоянием арбитра, — красный гейт."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    reg = _registry_of(exdir)
    for f in reg["findings"]:
        if f["id"] == fid:
            f["state"] = "арбитр: обработчик прав"
            f.pop("arbiter_unresolved", None)
    (exdir / "registry.json").write_text(
        json.dumps(reg, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    problems, _ = gate_arbiter.check(exdir)
    assert any("не соответствует записи «не разрешено»" in p
               for p in problems), problems


def test_gate_is_red_when_the_report_calls_the_first_of_two_a_decision(
        workspace):
    """Отрицательный контроль на подмену: отчёт объявил решение там,
    где реестр честно держит «не разрешено арбитром»."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    rep = _report_of(exdir)
    rep["unresolved"] = []
    rep["verdicts"] = [{"deadlock": fid, "verdict": "обработчик прав",
                        "basis": "первое из двух решений"}]
    rep["by_verdict"] = {**rep["by_verdict"], "обработчик прав": 1}
    (exdir / "arbiter.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    problems, _ = gate_arbiter.check(exdir)
    assert any("без записи «не разрешено» в отчёте" in p
               for p in problems), problems


def test_gate_refuses_double_and_duplicate_outcomes():
    """Один тупик — один исход: дубликаты решений, дубликаты записей
    «не разрешено» и решение вместе с записью — красный гейт (чистая
    функция, без каталога цикла)."""
    zero = {v: 0 for v in ex.ARBITER_VERDICTS}
    one = {**zero, "обработчик прав": 2}
    dup = {"verdicts": [
        {"deadlock": "X", "verdict": "обработчик прав", "basis": "б"},
        {"deadlock": "X", "verdict": "обработчик прав", "basis": "б"}],
        "by_verdict": one, "unresolved": []}
    assert any("повторные вердикты" in p
               for p in gate_arbiter._verdict_problems(dup, [{"id": "X"}]))

    twice = {"verdicts": [], "by_verdict": zero, "unresolved": [
        {"deadlock": "X", "reason": "противоречивые решения"},
        {"deadlock": "X", "reason": "противоречивые решения"}]}
    assert any("повторные записи «не разрешено»" in p
               for p in gate_arbiter._verdict_problems(twice,
                                                       [{"id": "X"}]))

    both = {"verdicts": [{"deadlock": "X", "verdict": "обработчик прав",
                          "basis": "б"}],
            "by_verdict": {**zero, "обработчик прав": 1},
            "unresolved": [{"deadlock": "X", "reason": "дубль"}]}
    assert any("исход обязан быть один" in p
               for p in gate_arbiter._verdict_problems(both,
                                                       [{"id": "X"}]))

    nothing = {"verdicts": [], "by_verdict": zero, "unresolved": []}
    assert any("ни записи «не разрешено»" in p
               for p in gate_arbiter._verdict_problems(nothing,
                                                       [{"id": "X"}]))

    blank = {"verdicts": [], "by_verdict": zero,
             "unresolved": [{"deadlock": "X", "reason": "  "}]}
    assert any("без причины словами" in p
               for p in gate_arbiter._verdict_problems(blank,
                                                       [{"id": "X"}]))


def test_a_re_judged_decision_does_not_survive_as_unresolved(workspace):
    """X-05 (жизненный цикл): исполненное решение прежнего захода не
    переживает пересуд, кончившийся неоднозначностью. След заплаты
    снимается со свода, тупик снова открыт («не разрешено арбитром»),
    исполнение из отчёта уходит — иначе в модели остался бы патч,
    оправданный решением, которого больше нет."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    before = json.loads((exdir / "versions" / "model-v03.json")
                        .read_text(encoding="utf-8"))
    first = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                           _enforcing_worker)
    assert first["enforced_deadlocks"] == [fid]
    changed = json.loads((exdir / "versions" / "model-arbiter.json")
                         .read_text(encoding="utf-8"))
    target = next(r for r in changed["rules"] if r["id"] == "R-S1-п-1-9")
    was = next(r for r in before["rules"] if r["id"] == "R-S1-п-1-9")
    assert target != was                          # заплата встала

    # Возврат на переарбитрацию — штатным путём: состояние «тупик» в
    # реестре (руководство 10.7).
    reg = _registry_of(exdir)
    for g in reg["findings"]:
        if g["id"] == fid:
            g["state"] = "тупик"
    (exdir / "registry.json").write_text(
        json.dumps(reg, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    second = ex.run_arbiter(kit, exdir, _dup_arbiter(),
                            _enforcing_worker)
    assert [u["deadlock"] for u in second["unresolved"]] == [fid]
    assert second["verdicts"] == []
    assert second["enforced"] == 0
    assert second["enforced_deadlocks"] == []
    after = json.loads((exdir / "versions" / "model-arbiter.json")
                       .read_text(encoding="utf-8"))
    assert next(r for r in after["rules"]
                if r["id"] == "R-S1-п-1-9") == was
    f = _finding(exdir, fid)
    assert f["state"] == "не разрешено арбитром"
    # Решения больше нет — значит, не решён и КЛЮЧ: вместе с тупиком
    # снова живо его эхо, повторы того же замечания прежних раундов
    # (закон live_findings, A07).
    reg_now = _registry_of(exdir)
    blockers = ex.registry_blockers(reg_now)
    assert fid in {b["id"] for b in blockers}
    assert {b["key"] for b in blockers} == {f["key"]}
    assert ex.decide(_CLEAN_HISTORY, 9, registry=reg_now) \
        == ex.VERDICT_NOT_CLOSED
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


# ---------------------------------------------------------------------------
# §12.8: находка, чью заплату отверг замок кода, дозревает в тупик
# (слово владельца «Вариант 1», 12.09.2026) — путь к арбитру и обратно
# ---------------------------------------------------------------------------

#: Заплата, которую замок кода отвергает ТЕРМИНАЛЬНО: смена вида
#: строки HARD→DEF вне закрытого перечня миграций и без посадки A/B на
#: семью вида DEF. Форма заплаты законна — запрещено её содержание,
#: поэтому исправить её обработчик не вправе, а повторить замечание не
#: вправе аудитор: спор без арбитра остался бы вечным.
_PATCH_KIND_LOCKED = {
    "rule_id": "R-S1-п-1-9",
    "rule": {"id": "R-S1-п-1-9",
             "title": "Минимальный отдых на базе (ОМ-А)",
             "statement": "rest_home ≥ 10 ч (на базе)",
             "zone": "PAIRING", "kind": "DEF",
             "sources": [{"sid": "S1", "anchor": "п.1.9"}],
             "blockers": ["Q-1-п-1-9-п-2-2"]},
}


def _refusing_worker(prompt):
    """Обработчик, не исполнивший вердикт арбитража: ответ не
    «ПРИНЯТО» — заплаты по тупику нет вовсе. Разбор промпта общий
    с _enforcing_worker: своего парсера тест не заводит."""
    resp = json.loads(_enforcing_worker(prompt))
    for a in resp["answers"]:
        a.update(verdict="ОТКЛОНЕНО", basis="исполнить нечем")
        a.pop("patch", None)
        a.pop("change", None)
    return json.dumps(resp, ensure_ascii=False)


def _code_rejected_cycle(kit, out, exdir, severity="КРИТИЧЕСКОЕ"):
    """Цикл, в котором замок кода терминально отверг заплату
    обработчика по замечанию заданной серьёзности."""
    rm = dict(_REMARK_DISTORT, severity=severity)
    auditor = _make_auditor([[rm], [], [], []])
    worker = _make_worker({"Потеряно уточнение": {
        "verdict": "ПРИНЯТО", "basis": "цитата: «на базе» — принято",
        "change": "вид строки изменён", "patch": _PATCH_KIND_LOCKED}})
    return ex.run_exchange(kit, out, exdir, auditor, worker,
                           max_rounds=6)


def test_code_rejected_finding_matures_into_a_deadlock(workspace):
    """A09-06: терминальная отбраковка замком кода — сразу «тупик»
    вида «отклонено кодом» (ни одна сторона действовать не вправе),
    круг отбраковки и слова замка сохранены, closed_round не ставится;
    Б/К держит цикл в «не закрыто», а повторная проводка гейта
    воспроизводит тот же тупик тем же писателем."""
    kit, out, exdir = workspace
    summary = _code_rejected_cycle(kit, out, exdir)
    assert summary["verdict"] == ex.VERDICT_NOT_CLOSED
    fid = _deadlock_ids(exdir)
    assert len(fid) == 1                     # тупик виден в вердикте
    f = _finding(exdir, fid[0])
    assert f["state"] == "тупик"
    assert f["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert f["closed_round"] is None
    assert f["code_rejected_round"] == 1
    assert "вне перечня миграций" in f["code_rejection"]
    # Новых записей прежнего состояния писатель не создаёт.
    assert all(g["state"] != "отклонено кодом"
               for g in _registry_of(exdir)["findings"])
    assert [b["id"] for b in
            ex.registry_blockers(_registry_of(exdir))] == fid
    assert ex.registry_verdict_of(_registry_of(exdir))["count"] == 1

    from cdpm.gates import gate_exchange
    problems, _ = gate_exchange.check(exdir)
    assert problems == [], problems


def test_serious_code_rejection_is_visible_but_does_not_block(workspace):
    """A09-06, вторая половина: дорожка одна для всех серьёзностей —
    СЕРЬЁЗНАЯ находка тоже становится тупиком и видна в `deadlocks`,
    но «не закрыто» держат только Б/К."""
    kit, out, exdir = workspace
    summary = _code_rejected_cycle(kit, out, exdir, severity="СЕРЬЁЗНОЕ")
    assert [d["deadlock_kind"] for d in summary["deadlocks"]] \
        == [ex.CODE_DEADLOCK_KIND]
    assert summary["verdict"] != ex.VERDICT_NOT_CLOSED
    assert ex.registry_blockers(_registry_of(exdir)) == []
    assert summary["registry_verdict"]["count"] == 0


def test_code_rejection_reaches_the_arbiter_dossier(workspace):
    """A09-09: досье несёт ПРЕДМЕТ спора целиком — замечание, саму
    отвергнутую заплату и слова запрета; иначе арбитр судил бы о
    заплате, которой не видел (обработчик ответил «ПРИНЯТО», и
    оснований отклонения у спора нет вовсе)."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    d = ex._deadlock_dossier(exdir, _finding(exdir, fid))
    assert d["remark"]["text"].startswith("Потеряно уточнение")
    assert d["rejections"] == []                 # отклонений не было
    assert "вне перечня миграций" in d["code_rejection"]
    assert [cr["terminal"] for cr in d["code_rejections"]] == [True]
    assert d["code_rejections"][0]["patch"] == _PATCH_KIND_LOCKED

    seen = {}
    base = _make_arbiter("обработчик прав")

    def arbiter(prompt):
        seen["prompt"] = prompt
        return base(prompt)
    ex.run_arbiter(kit, exdir, arbiter, _enforcing_worker)
    assert "вид тупика: отклонено кодом" in seen["prompt"]
    assert "Причина запрета кодом: требование замка кода" \
        in seen["prompt"]
    assert '"kind": "DEF"' in seen["prompt"]     # сама заплата
    # Слова замка не показываются дважды.
    assert seen["prompt"].count("вне перечня миграций") == 1


def test_arbiter_worker_right_resolves_the_code_rejected_deadlock(
        workspace):
    """A09-07: «обработчик прав» решает спор — ключ закрыт решением, и
    на чистом хвосте «успех» снова возможен."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                         _enforcing_worker)
    assert rep["by_verdict"]["обработчик прав"] == 1
    assert rep["enforced"] == 0 and rep["unenforced"] == []
    assert _finding(exdir, fid)["state"] == "арбитр: обработчик прав"
    reg = _registry_of(exdir)
    assert ex.registry_blockers(reg) == []
    assert ex.decide(_CLEAN_HISTORY, 9, registry=reg) == "успех"
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_auditor_right_asks_the_owner_instead_of_writing_a_banned_patch(
        workspace):
    """A09-07, главный контроль: «аудитор прав» НЕ обходит замок кода.
    Заплаты нет (свод не изменён), находка живая с названной причиной,
    спор передан владельцу вопросом анкеты — и «успехом» не
    становится."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    before = json.loads((exdir / "versions" / "model-v03.json")
                        .read_text(encoding="utf-8"))
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         _enforcing_worker)

    assert rep["enforced"] == 0 and rep["enforced_deadlocks"] == []
    assert [u["deadlock"] for u in rep["unenforced"]] == [fid]
    assert "замка кода" in rep["unenforced"][0]["reason"]
    qid = m.stable_question_id("2", f"арб-{fid}")
    assert rep["added_questions"] == [qid]       # вопрос владельцу

    f = _finding(exdir, fid)
    assert f["state"] == "тупик"                 # спор не разрешён
    assert "исполнить заплатой нельзя" in f["arbiter_unenforced"]
    assert qid in f["arbiter_unenforced"]

    after = json.loads((exdir / "versions" / "model-arbiter.json")
                       .read_text(encoding="utf-8"))
    kinds_before = {r["id"]: r["kind"] for r in before["rules"]}
    kinds_after = {r["id"]: r["kind"] for r in after["rules"]}
    assert kinds_after == kinds_before           # запрещённой правки нет
    q = next(x for x in after["questions"] if x["id"] == qid)
    assert q["part"] == "2"
    assert [o["key"] for o in q["answer_format"]["options"]] == ["а", "б"]
    assert "замок кода" in q["text"]
    assert q["rule_refs"] == ["R-S1-п-1-9"]
    assert qid in next(r for r in after["rules"]
                       if r["id"] == "R-S1-п-1-9")["blockers"]

    reg = _registry_of(exdir)
    assert [b["id"] for b in ex.registry_blockers(reg)] == [fid]
    assert ex.decide(_CLEAN_HISTORY, 9, registry=reg) \
        == ex.VERDICT_NOT_CLOSED
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_gate_is_red_when_the_code_lock_is_declared_enforced(workspace):
    """Отрицательный контроль к §12.8: отчёт объявил запрещённую
    заплату исполненной, а реестр закрыл находку — гейт краснеет и
    называет тупик."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                   _enforcing_worker)
    rep = _report_of(exdir)
    rep["enforced"], rep["enforced_deadlocks"] = 1, [fid]
    rep["unenforced"] = []
    (exdir / "arbiter.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    reg = _registry_of(exdir)
    for f in reg["findings"]:
        if f["id"] == fid:
            f["state"] = "арбитр: аудитор прав"
            f.pop("arbiter_unenforced", None)
    (exdir / "registry.json").write_text(
        json.dumps(reg, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    problems, _ = gate_arbiter.check(exdir)
    assert any("запрещает замок кода" in p and fid in p
               for p in problems), problems


def test_gate_is_red_when_the_owner_question_is_dropped(workspace):
    """Отрицательный контроль: причина названа, а вопроса владельцу
    нет — спор потерян, гейт красный."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                   _enforcing_worker)
    rep = _report_of(exdir)
    rep["added_questions"] = []
    (exdir / "arbiter.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    problems, _ = gate_arbiter.check(exdir)
    assert any("вопрос владельцу не поставлен" in p for p in problems), \
        problems


def test_code_lock_label_without_the_reason_gets_no_exception(workspace):
    """A09-09: подделанное производное поле ничего не разрешает — ярлык
    вида тупика без сохранённых слов замка отправляет находку ОБЫЧНОЙ
    дорогой, где неисполненный «аудитор прав» честно краснит гейт."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    reg = _registry_of(exdir)
    for f in reg["findings"]:
        if f["id"] == fid:
            f["deadlock_kind"] = ex.CODE_DEADLOCK_KIND
    (exdir / "registry.json").write_text(
        json.dumps(reg, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    assert not ex.code_locked_deadlock(_finding(exdir, fid))
    ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                   _refusing_worker)
    problems, _ = gate_arbiter.check(exdir)
    assert any("не исполнен" in p for p in problems), problems


def test_legacy_code_rejection_matures_by_the_same_law():
    """A09-08: унаследованная ЖИВАЯ запись «отклонено кодом» дозревает
    в тот же тупик тем же законом (иначе у вчерашних прогонов пути к
    арбитру нет); закрытая запись старой формы не воскресает; вход
    функции не меняется."""
    reg = {"findings": [
        # Нормализованная чтением A07 запись: closed_round снят,
        # прежний номер сохранён отдельно.
        {"id": "L1", "key": "k1", "state": "отклонено кодом",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
         "closed_round": None, "legacy_code_closure_round": 2},
        # Закрытая запись старой формы — читается как есть.
        {"id": "L2", "key": "k2", "state": "отклонено кодом",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
         "closed_round": 2},
        {"id": "L3", "key": "k3", "state": "открыто",
         "severity": "БЛОКИРУЮЩЕЕ", "opened_round": 1,
         "closed_round": None}]}
    frozen = json.dumps(reg, ensure_ascii=False, sort_keys=True)
    out = ex.mature_legacy_code_rejections(reg, 7)
    assert json.dumps(reg, ensure_ascii=False, sort_keys=True) == frozen

    l1, l2, l3 = out["findings"]
    assert l1["state"] == "тупик"
    assert l1["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert l1["code_rejected_round"] == 2        # из legacy-номера
    assert l1["code_rejection"] == ex.LEGACY_CODE_REJECTION
    assert ex.code_locked_deadlock(l1)           # путь к арбитру открыт
    assert l2["state"] == "отклонено кодом" and "deadlock_kind" not in l2
    assert l3 == reg["findings"][2]
    # Живость (A07) дозреванием не меняется, а повтор идемпотентен.
    # Композиция A07 × A09 (решение аудитора 12.09.2026): L2 — запись
    # «отклонено кодом» с проставленным closed_round и БЕЗ closed_by, то
    # есть ровно та унаследованная техническая отбраковка, которую F3
    # возвращает в живые (закрытием её объявлял прежний писатель, а не
    # исполненный патч и не арбитр). Прежнее ожидание {L1, L3} стояло на
    # посылке «закрытая запись старой формы читается как есть» — посылке
    # той базы A09, где нормализации A07 на общем пути ещё не было.
    # Ослаблением замена не является: воскрешение строго ДОБАВЛЯЕТ живых
    # блокеров (fail-closed), а положительный контроль настоящего
    # закрытия и решения арбитра стоит отдельной пробой
    # `test_a07_real_closure_and_arbiter_survive_continuation`.
    assert {b["id"] for b in ex.registry_blockers(out)} == {"L1", "L2",
                                                            "L3"}
    assert ex.mature_legacy_code_rejections(out, 9) == out


def test_legacy_maturation_keeps_the_words_the_writer_saved():
    """Сохранённые слова замка и номер круга не затираются
    придуманными: восстанавливается только недостающее."""
    reg = {"findings": [
        {"id": "L1", "key": "k1", "state": "отклонено кодом",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
         "closed_round": None, "code_rejected_round": 3,
         "code_rejection": "требование замка кода: смена семьи"}]}
    f = ex.mature_legacy_code_rejections(reg, 9)["findings"][0]
    assert f["code_rejected_round"] == 3
    assert f["code_rejection"] == "требование замка кода: смена семьи"


def test_in_anketa_on_a_code_locked_deadlock_follows_the_normal_path(
        workspace):
    """A09-07, третий контроль: «в анкету» по тупику замка кода идёт
    ЗАКОННЫМ механизмом передачи вопроса — развилка чтения источника
    (запрет кода тут ни при чём: спорно само чтение), а не скрытая
    заплата. Ключ решён, свод не переписан."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    before = json.loads((exdir / "versions" / "model-v03.json")
                        .read_text(encoding="utf-8"))
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("в анкету"),
                         _enforcing_worker)
    qid = m.stable_question_id("2", f"арб-{fid}")
    assert rep["added_questions"] == [qid]
    assert rep["unresolved"] == [] and rep["unenforced"] == []
    assert rep["enforced"] == 0

    f = _finding(exdir, fid)
    assert f["state"] == "арбитр: в анкету"       # спор решён
    assert "arbiter_unenforced" not in f
    after = json.loads((exdir / "versions" / "model-arbiter.json")
                       .read_text(encoding="utf-8"))
    q = next(x for x in after["questions"] if x["id"] == qid)
    assert "Какое чтение верно?" in q["text"]     # обычная развилка
    assert "замок кода" not in q["text"]
    assert {r["id"]: r["kind"] for r in after["rules"]} \
        == {r["id"]: r["kind"] for r in before["rules"]}

    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems
    assert ex.registry_blockers(_registry_of(exdir)) == []


def test_repeated_arbitration_of_a_code_locked_deadlock_is_idempotent(
        workspace):
    """A09-08, вторая половина: повторный заход по тому же тупику не
    плодит ни второй находки, ни второго вопроса и не самозакрывает
    спор; вид тупика и слова замка переживают заход."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    qid = m.stable_question_id("2", f"арб-{fid}")
    first = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                           _enforcing_worker)
    assert first["added_questions"] == [qid]
    second = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                            _enforcing_worker)

    # Вопрос владельцу один — поставлен заново или переиспользован.
    after = json.loads((exdir / "versions" / "model-arbiter.json")
                       .read_text(encoding="utf-8"))
    assert [q["id"] for q in after["questions"]].count(qid) == 1
    assert second["added_questions"].count(qid) \
        + (1 if fid in second["question_reuse"] else 0) == 1
    assert [u["deadlock"] for u in second["unenforced"]] == [fid]
    assert second["enforced"] == 0 and second["unresolved"] == []

    reg = _registry_of(exdir)
    assert sum(1 for g in reg["findings"] if g["id"] == fid) == 1
    f = _finding(exdir, fid)
    assert f["state"] == "тупик"                  # не самозакрылся
    assert f["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert "вне перечня миграций" in f["code_rejection"]
    assert [b["id"] for b in ex.registry_blockers(reg)] == [fid]
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_unresolved_answer_keeps_the_code_locked_path_open(workspace):
    """Два закона вместе: неоднозначный ответ (A09/§12.2) по тупику
    замка кода (§12.8) не стирает ни вида тупика, ни слов замка —
    следующий заход судит его тем же законом, а не обычной дорогой."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    rep = ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    assert [u["deadlock"] for u in rep["unresolved"]] == [fid]
    f = _finding(exdir, fid)
    assert f["state"] == "не разрешено арбитром"
    assert f["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert ex.code_locked_deadlock(f)             # путь к арбитру цел
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems

    rep2 = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                          _enforcing_worker)
    assert rep2["unresolved"] == [] and rep2["enforced"] == 0
    assert [u["deadlock"] for u in rep2["unenforced"]] == [fid]
    f2 = _finding(exdir, fid)
    assert f2["state"] == "тупик"
    assert "arbiter_unresolved" not in f2
    assert "исполнить заплатой нельзя" in f2["arbiter_unenforced"]
    problems2, _ = gate_arbiter.check(exdir)
    assert problems2 == [], problems2


@pytest.mark.parametrize("severity", ex.SEVERITIES)
def test_every_severity_takes_the_same_code_rejection_path(severity):
    """§12.8 п. 1: дорожка ОДНА для всех серьёзностей — состояние, вид
    тупика и сохранённые поля от серьёзности не зависят; разнится лишь
    блокировка успеха (только Б/К, закон A07)."""
    reg = {"findings": [
        {"id": "F1", "key": "k1", "severity": severity,
         "state": "открыто", "opened_round": 1, "closed_round": None}]}
    assert ex.mark_code_rejected(reg, {"k1": "замок: смена вида"}, 3) \
        == ["k1"]
    f = reg["findings"][0]
    assert f["state"] == "тупик"
    assert f["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert f["closed_round"] is None
    assert f["code_rejected_round"] == 3
    assert f["code_rejection"] == "замок: смена вида"
    assert [g["id"] for g in ex.live_findings(reg)] == ["F1"]
    assert bool(ex.registry_blockers(reg)) \
        is (severity in ex.BLOCKING_SEVERITIES)


# ---------------------------------------------------------------------------
# Форма ответа и ограниченный повтор (классы аудитора A09-03, A09-04)
# ---------------------------------------------------------------------------

def _replies(*payloads):
    """Арбитр по расписанию: своя выдача на каждое обращение, счётчик
    обращений — в замыкании."""
    state = {"n": 0}

    def llm(_prompt):
        i = min(state["n"], len(payloads) - 1)
        state["n"] += 1
        return payloads[i] if isinstance(payloads[i], str) \
            else json.dumps(payloads[i], ensure_ascii=False)
    return llm, state


def test_malformed_entries_do_not_become_decisions():
    """A09-03: запись-не-объект, запись без идентификатора и пустой
    идентификатор решением не становятся и не роняют прогон — каждая
    названа местом в ответе; законное решение соседнего спора живо."""
    llm, state = _replies({"verdicts": [
        "просто строка",
        {"deadlock": None, "verdict": "обработчик прав", "basis": "б"},
        {"deadlock": "   ", "verdict": "обработчик прав", "basis": "б"},
        {"deadlock": "D2", "verdict": "обработчик прав",
         "basis": "клауза подтверждает строку"}]})
    out, unresolved = judge.batch_verdicts(
        llm, "{blocks}", {"D1": "a", "D2": "b"}, ex.ARBITER_VERDICTS,
        id_field="deadlock")
    assert state["n"] == 2                        # ровно один повтор
    assert set(out) == {"D2"}                     # законное сохранено
    assert "D1" in unresolved and "нет решения" in unresolved["D1"]
    assert any("не объект" in r for r in unresolved.values())
    assert sum("без идентификатора" in k for k in unresolved) == 2
    assert all("не назван" in unresolved[k] for k in unresolved
               if "без идентификатора" in k)


def test_decision_without_a_basis_is_not_a_decision():
    """A09-03: идентичность решения — (спор, вердикт, основание).
    Вердикт без основания словами не закрывает спор: он приходит
    запасным «спорно» владельцу, а «спорно» не входит в три
    допустимых решения и ловится гейтом."""
    llm, state = _replies({"verdicts": [
        {"deadlock": "D1", "verdict": "обработчик прав", "basis": "  "}]})
    out, unresolved = judge.batch_verdicts(
        llm, "{blocks}", {"D1": "a"}, ex.ARBITER_VERDICTS,
        id_field="deadlock")
    assert state["n"] == 2
    assert unresolved == {}
    assert out["D1"]["verdict"] == "спорно"
    assert "спорно" not in ex.ARBITER_VERDICTS


def test_disputed_fallback_is_keyed_by_the_requested_id_field():
    """A09-03: запасное «спорно» кладётся под ЗАПРОШЕННЫМ полем
    (`deadlock` у арбитража) — прежде всегда под `etalon`, и гейт
    арбитража видел запись без идентификатора тупика."""
    llm, _ = _replies({"verdicts": [
        {"deadlock": "D1", "verdict": "никто не прав", "basis": "б"}]})
    out, _unresolved = judge.batch_verdicts(
        llm, "{blocks}", {"D1": "a"}, ex.ARBITER_VERDICTS,
        id_field="deadlock")
    assert out["D1"]["deadlock"] == "D1"
    assert "etalon" not in out["D1"]

    llm2, _ = _replies({"verdicts": [
        {"etalon": "ET-1", "verdict": "никто не прав", "basis": "б"}]})
    out2, _ = judge.batch_verdicts(
        llm2, "{blocks}", {"ET-1": "a"}, ("найдено", "не найдено"))
    assert out2["ET-1"]["etalon"] == "ET-1"       # поле по умолчанию


def test_a_corrected_second_reply_is_accepted():
    """A09-04: повтор существует ради исправления — второй ответ,
    давший ровно по одному корректному решению, принимается целиком, и
    неразрешённого не остаётся."""
    llm, state = _replies(
        {"verdicts": [{"deadlock": "D1", "verdict": "обработчик прав",
                       "basis": "первое чтение"},
                      {"deadlock": "D1", "verdict": "аудитор прав",
                       "basis": "второе чтение"}]},
        {"verdicts": [{"deadlock": "D1", "verdict": "в анкету",
                       "basis": "клауза допускает оба чтения"},
                      {"deadlock": "D2", "verdict": "обработчик прав",
                       "basis": "клауза подтверждает строку"}]})
    out, unresolved = judge.batch_verdicts(
        llm, "{blocks}", {"D1": "a", "D2": "b"}, ex.ARBITER_VERDICTS,
        id_field="deadlock")
    assert state["n"] == 2
    assert unresolved == {}
    assert out["D1"]["verdict"] == "в анкету"
    assert out["D2"]["verdict"] == "обработчик прав"


def test_the_retry_is_bounded_and_does_not_heal_by_dropping_duplicates():
    """A09-04, вторая половина: обращений не больше двух, и
    исчерпанный повтор не «лечится» удалением дублей — порядок записей
    на исход не влияет."""
    rows = [{"deadlock": "D1", "verdict": "обработчик прав",
             "basis": "первое чтение"},
            {"deadlock": "D1", "verdict": "аудитор прав",
             "basis": "второе чтение"},
            {"deadlock": "D2", "verdict": "обработчик прав",
             "basis": "клауза подтверждает строку"}]
    for order in (rows, list(reversed(rows))):
        llm, state = _replies({"verdicts": order}, {"verdicts": order})
        out, unresolved = judge.batch_verdicts(
            llm, "{blocks}", {"D1": "a", "D2": "b"},
            ex.ARBITER_VERDICTS, id_field="deadlock")
        assert state["n"] == 2                    # без бесконечного повтора
        assert set(out) == {"D2"}
        assert set(unresolved) == {"D1"}
        assert "противореч" in unresolved["D1"]


def test_broken_json_then_a_good_reply_is_accepted():
    """A09-04: битый JSON — повод для повтора, не смерть прогона;
    исправленный ответ принимается."""
    llm, state = _replies(
        '{"verdicts": [битый json',
        {"verdicts": [{"deadlock": "D1", "verdict": "обработчик прав",
                       "basis": "клауза подтверждает строку"}]})
    out, unresolved = judge.batch_verdicts(
        llm, "{blocks}", {"D1": "a"}, ex.ARBITER_VERDICTS,
        id_field="deadlock")
    assert state["n"] == 2 and unresolved == {}
    assert out["D1"]["verdict"] == "обработчик прав"


def test_broken_json_twice_leaves_every_deadlock_unresolved():
    """Оба обращения битые — спор не разрешён и не потерян; «первого
    из двух» здесь нет вовсе."""
    llm, state = _replies('{"verdicts": [битый json',
                          '{"verdicts": [опять битый')
    out, unresolved = judge.batch_verdicts(
        llm, "{blocks}", {"D1": "a", "D2": "b"}, ex.ARBITER_VERDICTS,
        id_field="deadlock")
    assert state["n"] == 2 and out == {}
    assert set(unresolved) == {"D1", "D2"}
    assert all("нет решения" in r for r in unresolved.values())


def test_run_arbiter_reds_the_gate_on_a_disputed_fallback(workspace):
    """A09-03 на живом арбитраже: «спорно» — не одно из трёх решений,
    находка остаётся в состоянии «тупик», и гейт краснеет дважды
    (вердикт вне перечня и незакрытый тупик), ничего не закрыв."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]

    def arbiter(prompt):
        ids = _re.findall(r"Тупик \[([^\]]+)\]", prompt)
        return json.dumps({"verdicts": [
            {"deadlock": i, "verdict": "никто не прав",
             "basis": "уклонение"} for i in ids]}, ensure_ascii=False)
    rep = ex.run_arbiter(kit, exdir, arbiter, _enforcing_worker)
    assert [v["verdict"] for v in rep["verdicts"]] == ["спорно"]
    assert rep["unresolved"] == []                # форма ответа цела
    assert _finding(exdir, fid)["state"] == "тупик"
    problems, _ = gate_arbiter.check(exdir)
    assert any("вне перечня" in p for p in problems), problems
    assert any("остался в состоянии «тупик»" in p
               for p in problems), problems
    assert set(_deadlock_ids(exdir)) <= {
        b["id"] for b in ex.registry_blockers(_registry_of(exdir))}


def test_run_arbiter_shows_a_malformed_reply_in_the_report(workspace):
    """A09-05: беда формы ответа доходит до отчёта записью «не
    разрешено» и краснит гейт — её не глотают молча.

    Счётчик «рассмотрено» при этом считает СПОРЫ ЦИКЛА (ревью A09,
    находка 6): запись без формы спором не является и в счёт не
    входит — её считает `unresolved_foreign`."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    rep = ex.run_arbiter(
        kit, exdir,
        lambda _p: json.dumps({"verdicts": ["просто строка"]}),
        _enforcing_worker)
    reasons = {u["deadlock"]: u["reason"] for u in rep["unresolved"]}
    assert any("не объект" in r for r in reasons.values())
    assert len(rep["unresolved"]) == 2            # спор цикла + форма
    assert rep["deadlocks"] == 1                  # спор цикла один
    assert rep["unresolved_foreign"] == 1
    problems, _ = gate_arbiter.check(exdir)
    assert any("неизвестному тупику" in p for p in problems), problems

# ---------------------------------------------------------------------------
# Подделанная форма перечней отчёта (класс A09-05, общий класс X-02)
# ---------------------------------------------------------------------------

def test_gate_names_a_forged_report_form_instead_of_falling():
    """Запись-не-объект, null и объект вместо списка в перечнях отчёта
    — нарушение СЛОВАМИ с местом записи, а не падение гейта трассой
    (упавший гейт читается как беда инструмента, и подделка уходит из
    вида). Спор при этом решённым не считается."""
    zero = {v: 0 for v in ex.ARBITER_VERDICTS}
    for field in ("verdicts", "unresolved"):
        rep = {"verdicts": [], "by_verdict": zero, "unresolved": []}
        rep[field] = ["просто строка", None]
        probs = gate_arbiter._verdict_problems(rep, [{"id": "D1"}])
        assert sum(f"перечня {field}" in p and "не объект" in p
                   for p in probs) == 2, probs
        assert any("ни записи «не разрешено»" in p for p in probs)

        rep[field] = {"D1": "решение"}            # объект вместо списка
        probs = gate_arbiter._verdict_problems(rep, [{"id": "D1"}])
        assert any(f"перечень {field}" in p and "не список" in p
                   for p in probs), probs
        assert any("ни записи «не разрешено»" in p for p in probs)

    # Отсутствие перечня — законная старая форма отчёта, не нарушение.
    old = {"verdicts": [{"deadlock": "D1", "verdict": "обработчик прав",
                         "basis": "по клаузе"}],
           "by_verdict": {**zero, "обработчик прав": 1}}
    assert gate_arbiter._verdict_problems(old, [{"id": "D1"}]) == []


def test_forged_report_form_reds_the_gate_on_the_real_directory(
        workspace):
    """Тот же закон у файлового потребителя: подделка формы в
    arbiter.json каталога цикла краснит гейт целиком и не роняет
    его."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    rep = _report_of(exdir)
    rep["unresolved"] = ["первое из двух"]
    (exdir / "arbiter.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    problems, counters = gate_arbiter.check(exdir)
    assert any("перечня unresolved" in p and "не объект" in p
               for p in problems), problems
    assert counters["unresolved"] == 0            # записей нет вовсе


# ---------------------------------------------------------------------------
# Неавторское ревью A09 (12.09.2026): подделка носителей, граница
# мегакруга, счёт рассмотренного и чужая история
# ---------------------------------------------------------------------------

def _write_json(path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def _next_cycle(exdir):
    """Продолжение цикла (следующий мегакруг) тем же путём, что и
    панель (`webui._archive_finished_exchange`): завершённый цикл
    уходит в exchange-rev-N (номер — первый свободный), реестр
    переносится в новый каталог.

    Номер берётся СВОБОДНЫЙ, как у панели: прежде помощник всегда
    писал «-rev-1», и второй мегакруг клал каталог ВНУТРЬ первого
    архива (shutil.move в существующий каталог), а реестр в новый
    цикл переносил из ПЕРВОГО архива — помощник изображал не то, что
    делает панель."""
    import shutil
    n = 1
    while (exdir.parent / f"{exdir.name}-rev-{n}").exists():
        n += 1
    rev = exdir.parent / f"{exdir.name}-rev-{n}"
    shutil.move(str(exdir), str(rev))
    exdir.mkdir()
    shutil.copy(rev / "registry.json", exdir / "registry.json")
    return rev


def _block_of(prompt: str, fid: str) -> str:
    """Блок ОДНОГО спора из задания арбитру — то, по чему он судит."""
    head = f"=== Тупик [{fid}]"
    assert head in prompt, prompt
    tail = prompt.split(head, 1)[1]
    return head + tail.split("=== Тупик", 1)[0].split("ЖЁСТКОЕ", 1)[0]


def test_gate_sees_the_code_lock_through_a_forged_registry(workspace):
    """Ревью A09, находка 1: подделаны ОБА носителя, которые пишет сам
    арбитраж, — реестр (находка закрыта решением, поля вида тупика и
    слов замка сняты) и отчёт (исполнение объявлено, unenforced пуст).
    Проверку «строка-цель изменена» подделка проходит: строку законно
    изменил вопрос владельцу, заблокировав её. Гейт всё равно краснеет:
    терминальная отбраковка живёт в файлах раундов — носителе, которым
    арбитраж не владеет."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                   _enforcing_worker)
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems              # честный заход зелен

    rep = _report_of(exdir)
    rep["enforced"], rep["enforced_deadlocks"] = 1, [fid]
    rep["unenforced"] = []
    _write_json(exdir / "arbiter.json", rep)
    reg = _registry_of(exdir)
    for f in reg["findings"]:
        if f["id"] == fid:
            f["state"] = "арбитр: аудитор прав"
            for k in ("deadlock_kind", "code_rejection",
                      "arbiter_unenforced"):
                f.pop(k, None)
    _write_json(exdir / "registry.json", reg)

    # Производные поля реестра стёрты — и сами по себе запрета больше
    # не свидетельствуют; третий носитель свидетельствует.
    entry = _finding(exdir, fid)
    assert not ex.code_locked_deadlock(entry)
    dossier = ex._deadlock_dossier(exdir, entry)
    assert [cr["terminal"] for cr in dossier["code_rejections"]] == [True]
    assert ex.code_locked_dispute(entry, dossier)

    problems, _ = gate_arbiter.check(exdir)
    assert any("запрещает замок кода" in p and fid in p
               for p in problems), problems


def test_a_code_locked_repeat_is_rejected_in_the_next_cycle(workspace):
    """Ревью A09, находка 2: в мегакруге 2 аудитор повторяет то же
    замечание. Запрет повтора держится на самом замке кода, а не на
    слове состояния: повтор отбракован машинно с причиной, ВТОРОЙ
    находки по ключу нет — и, значит, нет двух тупиков одного спора, по
    которым арбитр дал бы два разных решения (ровно CONS-04)."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    key = _finding(exdir, fid)["key"]
    _next_cycle(exdir)

    rm = dict(_REMARK_DISTORT)
    auditor = _make_auditor([[rm], [rm], [], []])
    worker = _make_worker({"Потеряно уточнение": {
        "verdict": "ПРИНЯТО", "basis": "цитата: «на базе» — принято",
        "change": "вид строки изменён", "patch": _PATCH_KIND_LOCKED}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=6)
    reg = _registry_of(exdir)
    assert reg["cycle"] == 2
    same_key = [f for f in reg["findings"] if f["key"] == key]
    assert [f["id"] for f in same_key] == [fid]
    assert same_key[0]["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert [d["id"] for d in summary["deadlocks"]] == [fid]

    # Отбраковка названа словами замка, а не молчанием.
    rej = json.loads((exdir / "rounds" / "round-01"
                      / "audit-rejections.json").read_text(
                          encoding="utf-8"))
    assert [r["reason"] for r in rej] \
        == ["требование замка кода: заплата отвергнута конвенцией или "
            "замком формализации, спор передан арбитру — повторное "
            "указание запрещено (состояние терминально)"]
    assert json.loads((exdir / "rounds" / "round-01" / "remarks.json")
                      .read_text(encoding="utf-8"))["remarks"] == []
    # Повторная проводка гейта идёт от снимка старта и воспроизводит
    # ту же картину: закон живёт в одном писателе.
    from cdpm.gates import gate_exchange
    assert gate_exchange.check(exdir)[0] == []


def test_the_same_ban_holds_after_an_unresolved_arbitration(workspace):
    """Тот же запрет держится и когда арбитраж по спору замка кода
    кончился неоднозначностью: состояние другое («не разрешено
    арбитром»), замок — тот же."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    assert _finding(exdir, fid)["state"] == "не разрешено арбитром"
    key = _finding(exdir, fid)["key"]
    _next_cycle(exdir)

    rm = dict(_REMARK_DISTORT)
    ex.run_exchange(kit, out, exdir, _make_auditor([[rm], [], []]),
                    _make_worker({}), max_rounds=4)
    reg = _registry_of(exdir)
    assert [f["id"] for f in reg["findings"] if f["key"] == key] == [fid]
    rej = json.loads((exdir / "rounds" / "round-01"
                      / "audit-rejections.json").read_text(
                          encoding="utf-8"))
    assert any("замка кода" in r["reason"] for r in rej), rej


def test_an_unresolved_dispute_reaches_the_arbiter_in_the_next_cycle(
        workspace):
    """Ревью A09, находка 3: спор «не разрешено арбитром», перенесённый
    в мегакруг 2, входит в него ТУПИКОМ — попадает в новый
    verdict.json, арбитраж его берёт и решает, гейт зелёный. Прежде он
    оставался спором без пути: тупиком не считался, в verdict.json не
    попадал, арбитраж его не брал, а ручной возврат в «тупик» краснил
    гейт словами «вердикт по неизвестному тупику» — и живая находка
    держала цикл в «не закрыто» вечно.

    Четвёртое неавтор-ревью A09: «доходит» — значит доходит СО СВОИМ
    СОДЕРЖАНИЕМ. Прежде тест закреплял лишь исход («решён, гейт
    зелёный») и молча благословлял СЛЕПОЙ суд: круги прежнего цикла
    уехали в архив, досье было пусто, и арбитру уходил блок
    «Замечание аудитора: None / Доказательство: None / Предложение:
    None / Клауза источника [None]: <нет> / Строка свода: <нет>» —
    решение по пустому досье закрывало живой блокирующий спор
    (контрпример §1.3 и §1.5 контракта П0). Теперь проверяется и
    предмет суда: замечание, доказательство, предложение, клауза
    источника и строка свода найдены в архиве прежнего цикла."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    f = _finding(exdir, fid)
    assert f["state"] == "не разрешено арбитром"
    why = f["arbiter_unresolved"]
    _next_cycle(exdir)

    summary = ex.run_exchange(kit, out, exdir,
                              _make_auditor([[], [], []]),
                              _make_worker({}), max_rounds=4)
    assert fid in [d["id"] for d in summary["deadlocks"]]
    back = _finding(exdir, fid)
    assert back["state"] == "тупик"
    assert back["arbiter_unresolved"] == why     # история не потеряна
    # Снимок старта (база реплея гейта) несёт то же состояние, и
    # повторная проводка цикла зелёная: закон один у цикла и у гейта.
    start = json.loads((exdir / "registry-start.json")
                       .read_text(encoding="utf-8"))
    assert next(g["state"] for g in start["findings"]
                if g["id"] == fid) == "тупик"
    from cdpm.gates import gate_exchange
    assert gate_exchange.check(exdir)[0] == []

    # Досье собрано из архива прежнего цикла — спор судится ПО
    # СОДЕРЖАНИЮ, а не по пустому бланку.
    dossier = ex._deadlock_dossier(exdir, _finding(exdir, fid))
    assert dossier["remark"]["text"] == _REMARK_DISTORT["text"]
    assert dossier["rejections"], "основания отклонений обработчика"
    assert dossier["remark_from_ban"] is False

    seen = {}

    def arbiter(prompt):
        seen["prompt"] = prompt
        return _make_arbiter("обработчик прав")(prompt)

    rep = ex.run_arbiter(kit, exdir, arbiter, _enforcing_worker)
    block = _block_of(seen["prompt"], fid)
    assert "Замечание аудитора: None" not in block
    assert _REMARK_DISTORT["text"] in block
    assert _REMARK_DISTORT["evidence"] in block
    assert _REMARK_DISTORT["proposal"] in block
    assert "Клауза источника [п.1.9]:" in block
    assert "Строка свода: <нет>" not in block
    assert fid in [v["deadlock"] for v in rep["verdicts"]]
    assert rep["unresolved"] == []
    solved = _finding(exdir, fid)
    assert solved["state"] == "арбитр: обработчик прав"
    assert "arbiter_unresolved" not in solved
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_the_arbiter_judges_only_the_deadlocks_of_its_own_verdict(
        workspace):
    """Ревью A09, находка 3, вторая половина: закон у суда и у его
    проверки ОДИН — разбираются только тупики из `verdict.json` цикла.
    Тупик, живущий в реестре мимо перечня (перенос чужого цикла, ручная
    правка), арбитражем не судится, и гейт не краснеет «вердиктом по
    неизвестному тупику»."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    reg = _registry_of(exdir)
    reg["findings"].append({
        "id": "C1-R9-A9-777",
        "key": '["искажение", "R-ЧУЖАЯ", null, null]',
        "state": "тупик", "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
        "closed_round": None, "text": "находка чужого цикла"})
    _write_json(exdir / "registry.json", reg)

    seen = {}
    base = _make_arbiter("обработчик прав")

    def arbiter(prompt):
        seen["prompt"] = prompt
        return base(prompt)
    rep = ex.run_arbiter(kit, exdir, arbiter, _enforcing_worker)
    assert "C1-R9-A9-777" not in seen["prompt"]
    assert [v["deadlock"] for v in rep["verdicts"]] == [fid]
    assert rep["unresolved"] == []
    assert _finding(exdir, "C1-R9-A9-777")["state"] == "тупик"
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_stray_answer_ids_do_not_inflate_the_reviewed_count(workspace):
    """Ревью A09, находка 6: «Тупиков рассмотрено» — споры ЭТОГО
    цикла. Посторонний идентификатор ответа арбитра спором не является:
    он не исчезает (перечень `unresolved`, свой счётчик, красный гейт),
    но счёт рассмотренного не раздувает. Следующий заход снимает его —
    беда одного ответа."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]

    def arbiter(prompt):
        ids = _re.findall(r"Тупик \[([^\]]+)\]", prompt)
        rows = [{"deadlock": i, "verdict": "обработчик прав",
                 "basis": "клауза подтверждает строку свода"}
                for i in ids]
        rows.append({"deadlock": "D-ЧУЖОЙ",
                     "verdict": "обработчик прав",
                     "basis": "решение о споре, которого не "
                              "предъявляли"})
        return json.dumps({"verdicts": rows}, ensure_ascii=False)

    rep = ex.run_arbiter(kit, exdir, arbiter, _enforcing_worker)
    assert rep["deadlocks"] == 1                 # спор цикла один
    assert [u["deadlock"] for u in rep["unresolved"]] == ["D-ЧУЖОЙ"]
    assert rep["unresolved_foreign"] == 1
    assert _finding(exdir, fid)["state"] == "арбитр: обработчик прав"
    problems, counters = gate_arbiter.check(exdir)
    assert counters["deadlocks"] == 1
    assert any("неизвестному тупику" in p and "D-ЧУЖОЙ" in p
               for p in problems), problems

    rep2 = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                          _enforcing_worker)
    assert rep2["unresolved"] == [] and rep2["unresolved_foreign"] == 0
    assert rep2["deadlocks"] == 1
    assert gate_arbiter.check(exdir)[0] == []


def test_code_rejection_does_not_orphan_a_dispute_already_disputed():
    """Ревью A09, находка 7: спор, уже дошедший до арбитра, замок кода
    состояния не меняет. «Не разрешено арбитром» сохраняет и состояние,
    и причину неразрешённости; у тупика другого вида прежний вид
    сохраняется словами, а не исчезает. Слова замка при этом
    записываются — иначе арбитр не увидит запрета."""
    reg = {"findings": [
        {"id": "A", "key": "k1", "severity": "КРИТИЧЕСКОЕ",
         "state": "не разрешено арбитром",
         "arbiter_unresolved": "противоречивые решения по A",
         "opened_round": 1, "closed_round": None},
        {"id": "B", "key": "k2", "severity": "КРИТИЧЕСКОЕ",
         "state": "тупик", "deadlock_kind": "повтор",
         "opened_round": 1, "closed_round": None},
        {"id": "C", "key": "k3", "severity": "КРИТИЧЕСКОЕ",
         "state": "открыто", "opened_round": 1, "closed_round": None}]}
    assert ex.mark_code_rejected(
        reg, {"k1": "замок: смена вида", "k2": "замок: смена вида",
              "k3": "замок: смена вида"}, 3) == ["k1", "k2", "k3"]
    a, b, c = reg["findings"]

    assert a["state"] == "не разрешено арбитром"
    assert a["arbiter_unresolved"] == "противоречивые решения по A"
    assert ex.code_locked_deadlock(a)            # запрет виден арбитру

    assert b["state"] == "тупик"
    assert b["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert b["deadlock_kind_before_code"] == "повтор"

    assert c["state"] == "тупик"                 # обычная дорожка цела
    assert c["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert "deadlock_kind_before_code" not in c
    # Решённый спор и закрытая находка не трогаются по-прежнему.
    done = {"findings": [
        {"id": "D", "key": "k1", "state": "арбитр: обработчик прав"},
        {"id": "E", "key": "k1", "state": "закрыто",
         "closed_round": 2}]}
    assert ex.mark_code_rejected(done, {"k1": "замок"}, 4) == []


def test_a_basis_that_is_not_words_is_not_a_basis():
    """Ревью A09, находка 8: основание — СЛОВА, и только они. Прежде
    `str(...)` принимал что угодно, и «основанием» решения становился
    список: потребитель показал бы владельцу `[1, 2]` как объяснение.
    Судья даёт запасное «спорно» (вердикт вне трёх — гейт красный),
    гейт называет тип."""
    for value in ([1, 2], {"why": "б"}, 7, None):
        llm, _ = _replies({"verdicts": [
            {"deadlock": "D1", "verdict": "обработчик прав",
             "basis": value}]})
        out, unresolved = judge.batch_verdicts(
            llm, "{blocks}", {"D1": "a"}, ex.ARBITER_VERDICTS,
            id_field="deadlock")
        assert unresolved == {}
        assert out["D1"]["verdict"] == "спорно", value
        assert "без основания словами" in out["D1"]["basis"]

    zero = {v: 0 for v in ex.ARBITER_VERDICTS}
    rep = {"verdicts": [{"deadlock": "D1", "verdict": "обработчик прав",
                         "basis": ["по", "клаузе"]}],
           "by_verdict": {**zero, "обработчик прав": 1},
           "unresolved": []}
    assert any("без основания словами" in p and "list" in p
               for p in gate_arbiter._verdict_problems(rep,
                                                       [{"id": "D1"}]))
    # Причина записи «не разрешено» — тем же законом.
    rep2 = {"verdicts": [], "by_verdict": zero,
            "unresolved": [{"deadlock": "D1", "reason": ["дубль"]}]}
    assert any("без причины словами" in p and "list" in p
               for p in gate_arbiter._verdict_problems(rep2,
                                                       [{"id": "D1"}]))


def test_legacy_maturation_after_normalisation_takes_closed_records():
    """Ревью A09, находка 4: закрытость унаследованной записи судит
    ОДНА нормализация чтения (A07), и помощник читает её следы, а не
    заводит свою проверку. Позванный сразу после нормализации
    (`normalized=True`) или увидев её след
    (`legacy_code_closure_round`), он дозревает и запись с
    проставленным `closed_round` — на w17a это живая техническая
    отбраковка. Без обоих следов запись старой формы не воскресает."""
    def _reg():
        return {"findings": [
            {"id": "L1", "key": "k1", "state": "отклонено кодом",
             "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
             "closed_round": 2, "closed_by": "замок кода"}]}

    # Вызов после нормализации: запись дозревает, номер круга уходит в
    # историю, тупик закрытым не остаётся.
    f = ex.mature_legacy_code_rejections(
        _reg(), 9, normalized=True)["findings"][0]
    assert f["state"] == "тупик"
    assert f["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert f["closed_round"] is None and f["closed_by"] is None
    assert f["legacy_code_closure_round"] == 2
    assert f["code_rejected_round"] == 2
    assert f["code_rejection"] == ex.LEGACY_CODE_REJECTION
    assert ex.code_locked_deadlock(f)            # путь к арбитру открыт
    assert [g["id"] for g in ex.live_findings({"findings": [f]})] \
        == ["L1"]

    # След нормализации, переживший запись на диск, действует и без
    # флага.
    marked = _reg()
    marked["findings"][0]["legacy_code_closure_round"] = 2
    assert ex.mature_legacy_code_rejections(marked, 9)[
        "findings"][0]["state"] == "тупик"

    # Без обоих следов — закрытая запись старой формы, как и прежде.
    kept = ex.mature_legacy_code_rejections(_reg(), 9)["findings"][0]
    assert kept["state"] == "отклонено кодом"
    assert kept["closed_round"] == 2
    # Чистота и идемпотентность не меняются.
    src = _reg()
    frozen = json.dumps(src, ensure_ascii=False, sort_keys=True)
    once = ex.mature_legacy_code_rejections(src, 9, normalized=True)
    assert json.dumps(src, ensure_ascii=False, sort_keys=True) == frozen
    assert ex.mature_legacy_code_rejections(once, 9,
                                            normalized=True) == once


# ---------------------------------------------------------------------------
# ВТОРОЕ неавторское ревью A09 (12.09.2026): спор = ключ, щит вопроса
# владельцу, лишнее упоминание решённого спора и форма отчёта
# ---------------------------------------------------------------------------

def _return_to_deadlock(exdir, fid: str) -> None:
    """Штатный возврат тупика на переарбитрацию (руководство 10.7)."""
    reg = _registry_of(exdir)
    for g in reg["findings"]:
        if g["id"] == fid:
            g["state"] = "тупик"
    _write_json(exdir / "registry.json", reg)


def _shown_ids(prompt: str) -> list[str]:
    return _re.findall(r"Тупик \[([^\]]+)\]", prompt)


def _echoing_cycle(kit, out, exdir):
    """Цикл, в котором по ОДНОМУ ключу рождаются ДВА тупика: аудитор
    трижды повторяет замечание, а обработчик на третьем повторе меняет
    ответ на ПРИНЯТО с заплатой, которую терминально отвергает замок
    кода (проба второго ревью, п. 2в)."""
    rm = dict(_REMARK_DISTORT)
    accept = _make_worker({"Потеряно уточнение": {
        "verdict": "ПРИНЯТО", "basis": "цитата: «на базе» — принято",
        "change": "вид строки изменён", "patch": _PATCH_KIND_LOCKED}})
    reject = _make_worker({})
    calls = {"n": 0}

    def worker(prompt):
        calls["n"] += 1
        return (reject if calls["n"] <= 2 else accept)(prompt)
    return ex.run_exchange(kit, out, exdir,
                           _make_auditor([[rm], [rm], [rm], [rm]]),
                           worker, max_rounds=6)


def test_one_dispute_is_one_block_and_one_decision(workspace):
    """X-03/X-04, главный контроль второго ревью: спор — это КЛЮЧ.
    Две находки одного ключа предъявляются арбитру ОДНИМ блоком (обе
    названы в досье), решение применяется к обеим разом. Прежде они
    предъявлялись порознь и получали РАЗНЫЕ решения — ключ считался
    решённым, реестр очищался и цикл приходил к «успеху»: контрпример
    §1.5 контракта П0 на уровне спора."""
    kit, out, exdir = workspace
    _echoing_cycle(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    assert len(ids) == 2
    key = ex.remark_key(dict(_REMARK_DISTORT))
    assert {_finding(exdir, i)["key"] for i in ids} == {key}

    seen = {}

    def arbiter(prompt):
        seen["shown"] = _shown_ids(prompt)
        seen["prompt"] = prompt
        return json.dumps({"verdicts": [
            {"deadlock": i, "verdict": "аудитор прав",
             "basis": "клауза читается в пользу аудитора"}
            for i in seen["shown"]]}, ensure_ascii=False)

    rep = ex.run_arbiter(kit, exdir, arbiter, _enforcing_worker)
    assert seen["shown"] == [ids[0]]              # один блок на спор
    assert "Спор ОДИН" in seen["prompt"]
    assert ids[1] in seen["prompt"]               # но назван в досье

    # Решение одно — и несут его ОБЕ находки ключа.
    assert {v["deadlock"]: v["verdict"] for v in rep["verdicts"]} \
        == {i: "аудитор прав" for i in ids}
    assert rep["deadlocks"] == 2
    # Замок кода запрещает заплату — вопрос владельцу ОДИН на спор.
    qid = m.stable_question_id("2", f"арб-{ids[0]}")
    assert rep["added_questions"] == [qid]
    assert rep["question_reuse"][ids[1]] == qid
    assert {u["deadlock"] for u in rep["unenforced"]} == set(ids)
    assert {_finding(exdir, i)["state"] for i in ids} == {"тупик"}

    # Контрпример закрыт: ключ не решён, обе находки живые, «успех»
    # цикла невозможен.
    reg = _registry_of(exdir)
    assert {b["id"] for b in ex.registry_blockers(reg)} == set(ids)
    assert ex.decide(_CLEAN_HISTORY, 9, registry=reg) \
        == ex.VERDICT_NOT_CLOSED
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_gate_is_red_when_one_key_carries_different_verdicts(workspace):
    """Отрицательный контроль к «спор = ключ»: подделанный отчёт, где
    находки ОДНОГО ключа несут разные решения (и где решение есть у
    одной, а у другой нет), — красный гейт с обоими идентификаторами."""
    kit, out, exdir = workspace
    _echoing_cycle(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                   _enforcing_worker)
    assert gate_arbiter.check(exdir)[0] == []     # честный заход зелен

    honest = _report_of(exdir)
    forged = json.loads(json.dumps(honest, ensure_ascii=False))
    for v in forged["verdicts"]:
        if v["deadlock"] == ids[1]:
            v["verdict"] = "в анкету"
    _write_json(exdir / "arbiter.json", forged)
    reg = _registry_of(exdir)
    for g in reg["findings"]:
        if g["id"] == ids[1]:
            g["state"] = "арбитр: в анкету"
    _write_json(exdir / "registry.json", reg)
    problems, _ = gate_arbiter.check(exdir)
    assert any("рассужен по-разному" in p and ids[0] in p and ids[1] in p
               for p in problems), problems

    # Решение на одной находке и молчание на другой — то же нарушение.
    dropped = json.loads(json.dumps(honest, ensure_ascii=False))
    dropped["verdicts"] = [v for v in dropped["verdicts"]
                           if v["deadlock"] != ids[1]]
    dropped["by_verdict"]["обработчик прав"] -= 1
    _write_json(exdir / "arbiter.json", dropped)
    problems2, _ = gate_arbiter.check(exdir)
    assert any("рассужен по-разному" in p for p in problems2), problems2


def test_disputes_of_different_keys_stay_apart(workspace):
    """Положительный контроль: разные ключи — разные споры. Оба блока
    предъявляются, решения у них могут быть РАЗНЫЕ, гейт зелёный."""
    kit, out, exdir = workspace
    ex.run_exchange(kit, out, exdir,
                    _make_auditor([[dict(_REMARK_DISTORT),
                                    dict(_REMARK_OMIT)]] * 4),
                    _make_worker({}), max_rounds=6)
    ids = _deadlock_ids(exdir)
    assert len({_finding(exdir, i)["key"] for i in ids}) == 2
    distort = _id_by_text(exdir, "Потеряно уточнение")
    other = next(i for i in ids if i != distort)
    seen = {}

    def arbiter(prompt):
        seen["shown"] = _shown_ids(prompt)
        seen["prompt"] = prompt
        return json.dumps({"verdicts": [
            {"deadlock": i,
             "verdict": "в анкету" if i == distort else "обработчик прав",
             "basis": "по клаузе источника"}
            for i in seen["shown"]]}, ensure_ascii=False)

    rep = ex.run_arbiter(kit, exdir, arbiter, _enforcing_worker)
    assert set(seen["shown"]) == set(ids)         # два спора — два блока
    assert "Спор ОДИН" not in seen["prompt"]
    assert {v["deadlock"]: v["verdict"] for v in rep["verdicts"]} \
        == {distort: "в анкету", other: "обработчик прав"}
    assert _finding(exdir, distort)["state"] == "арбитр: в анкету"
    assert _finding(exdir, other)["state"] == "арбитр: обработчик прав"
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_a_stray_mention_of_a_decided_deadlock_is_cleared(workspace):
    """X-05: арбитр упомянул тупик ЦИКЛА, уже решённый прежним заходом
    и не предъявленный. Счёт рассмотренного не раздувается (считаются
    РАЗНЫЕ тупики, а не сумма длин перечней), гейт честно краснеет о
    двух исходах одного тупика — и ЧЕСТНЫЙ заход это снимает. Прежде
    запись переносилась вечно: тупик больше не «тупик», в
    разбирательства не попадает — красный гейт навсегда."""
    kit, out, exdir = workspace
    ex.run_exchange(kit, out, exdir,
                    _make_auditor([[dict(_REMARK_DISTORT),
                                    dict(_REMARK_OMIT)]] * 4),
                    _make_worker({}), max_rounds=6)
    ids = _deadlock_ids(exdir)
    assert len(ids) == 2
    d1 = _id_by_text(exdir, "Потеряно уточнение")
    d2 = next(i for i in ids if i != d1)
    ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                   _enforcing_worker)
    assert gate_arbiter.check(exdir)[0] == []
    _return_to_deadlock(exdir, d1)

    def stray(prompt):
        rows = [{"deadlock": i, "verdict": "обработчик прав",
                 "basis": "клауза подтверждает строку свода"}
                for i in _shown_ids(prompt)]
        rows.append({"deadlock": d2, "verdict": "аудитор прав",
                     "basis": "решение о споре, которого не предъявляли"})
        return json.dumps({"verdicts": rows}, ensure_ascii=False)

    rep2 = ex.run_arbiter(kit, exdir, stray, _enforcing_worker)
    assert rep2["deadlocks"] == 2                 # два спора, не три
    assert rep2["unresolved_foreign"] == 0        # d2 — тупик ЦИКЛА
    assert [u["deadlock"] for u in rep2["unresolved"]] == [d2]
    assert "неизвестный идентификатор" in rep2["unresolved"][0]["reason"]
    problems, counters = gate_arbiter.check(exdir)
    assert counters["deadlocks"] == 2
    assert any("исход обязан быть один" in p and d2 in p
               for p in problems), problems

    # Честный заход: у d2 решение есть, неразрешённым в реестре он не
    # числится — запись снимается, гейт зелёный.
    rep3 = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                          _enforcing_worker)
    assert rep3["unresolved"] == []
    assert rep3["deadlocks"] == 2
    assert {v["deadlock"] for v in rep3["verdicts"]} == set(ids)
    assert gate_arbiter.check(exdir)[0] == []


def test_an_open_owner_question_shields_the_dispute(workspace):
    """§12.8 + щит fail-closed: спор замка кода, переданный владельцу
    ОТКРЫТЫМ вопросом анкеты, вторым заходом арбитру НЕ предъявляется.
    Прежде следующий заход переворачивал «аудитор прав» в «обработчик
    прав» БЕЗ слова владельца: находка закрывалась, а вопрос и
    блокировка строки повисали сиротами."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    qid = m.stable_question_id("2", f"арб-{fid}")
    first = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                           _enforcing_worker)
    assert first["added_questions"] == [qid]
    assert _finding(exdir, fid)["arbiter_question"] == qid

    seen = {}

    def arbiter(prompt):
        seen["prompt"] = prompt
        return _make_arbiter("обработчик прав")(prompt)

    second = ex.run_arbiter(kit, exdir, arbiter, _enforcing_worker)
    assert "prompt" not in seen                   # спор не предъявлен
    assert second["verdicts"] == []               # решения в отчёте нет
    assert second["deadlocks"] == 1               # но спор рассмотрен
    assert [a["deadlock"] for a in second["awaiting_owner"]] == [fid]
    assert second["awaiting_owner"][0]["question"] == qid
    assert "ждёт ответа владельца" \
        in second["awaiting_owner"][0]["reason"]

    f = _finding(exdir, fid)
    assert f["state"] == "тупик"                  # не перевернулся
    after = json.loads((exdir / "versions" / "model-arbiter.json")
                       .read_text(encoding="utf-8"))
    q = next(x for x in after["questions"] if x["id"] == qid)
    assert m.is_open_question(q)
    assert qid in next(r for r in after["rules"]
                       if r["id"] == "R-S1-п-1-9")["blockers"]
    reg = _registry_of(exdir)
    assert [b["id"] for b in ex.registry_blockers(reg)] == [fid]
    assert ex.decide(_CLEAN_HISTORY, 9, registry=reg) \
        == ex.VERDICT_NOT_CLOSED
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_the_shield_holds_across_the_megacycle_and_lifts_on_the_answer(
        workspace):
    """Тот же щит через ГРАНИЦУ мегакруга (отчёта прежнего цикла в
    новом каталоге нет вовсе — носитель id вопроса при находке) и его
    снятие: как только ответ владельца применён в каноническом своде,
    спор снова идёт к арбитру обычным заходом."""
    import shutil
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    qid = m.stable_question_id("2", f"арб-{fid}")
    ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                   _enforcing_worker)
    # Канон синхронизируется как в панели: model-arbiter.json → свод.
    shutil.copy(exdir / "versions" / "model-arbiter.json",
                out / "model.json")
    _next_cycle(exdir)
    summary = ex.run_exchange(kit, out, exdir,
                              _make_auditor([[], [], []]),
                              _make_worker({}), max_rounds=4)
    assert [d["id"] for d in summary["deadlocks"]] == [fid]

    rep = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                         _enforcing_worker)
    assert [a["deadlock"] for a in rep["awaiting_owner"]] == [fid]
    assert rep["verdicts"] == []
    assert _finding(exdir, fid)["state"] == "тупик"
    reg = _registry_of(exdir)
    assert [b["id"] for b in ex.registry_blockers(reg)] == [fid]
    assert ex.decide(_CLEAN_HISTORY, 9, registry=reg) \
        == ex.VERDICT_NOT_CLOSED
    assert gate_arbiter.check(exdir)[0] == []

    # Владелец ответил: ответ применён к каноническому своду —
    # блокировка строки-носителя снята обычным путём применения.
    canon = json.loads((out / "model.json").read_text(encoding="utf-8"))
    for q in canon["questions"]:
        if q["id"] == qid:
            q["answer"] = {"raw": "б", "key": "б", "status": "применён",
                           "revision": 1}
    for r in canon["rules"]:
        if qid in (r.get("blockers") or []):
            r["blockers"] = [b for b in r["blockers"] if b != qid]
    _write_json(out / "model.json", canon)
    _next_cycle(exdir)
    third = ex.run_exchange(kit, out, exdir, _make_auditor([[], [], []]),
                            _make_worker({}), max_rounds=4)
    assert [d["id"] for d in third["deadlocks"]] == [fid]
    seen3 = {}

    def arbiter3(prompt):
        seen3["prompt"] = prompt
        return _make_arbiter("обработчик прав")(prompt)

    rep3 = ex.run_arbiter(kit, exdir, arbiter3, _enforcing_worker)
    assert rep3["awaiting_owner"] == []           # щит снят
    # Четвёртое неавтор-ревью A09: щит снят — значит спор судится, и
    # судится ПО СОДЕРЖАНИЮ. Круги обоих прежних мегакругов лежат в
    # архивах (exchange-rev-1, exchange-rev-2), и прежде этот, третий,
    # заход получал пустой бланк: «Замечание аудитора: None». Тест
    # закреплял лишь исход и благословлял слепое закрытие спора,
    # дошедшего до арбитра через ДВА мегакруга.
    block3 = _block_of(seen3["prompt"], fid)
    assert "Замечание аудитора: None" not in block3
    assert _REMARK_DISTORT["text"] in block3
    assert _REMARK_DISTORT["evidence"] in block3
    assert "Причина запрета кодом:" in block3      # слова замка целы
    assert [v["deadlock"] for v in rep3["verdicts"]] == [fid]
    assert _finding(exdir, fid)["state"] == "арбитр: обработчик прав"
    assert gate_arbiter.check(exdir)[0] == []


def test_forged_deadlock_ids_are_named_instead_of_falling():
    """Ревью A09 №2, находка 4 (класс X-02): нехэшируемый, пустой или
    числовой идентификатор в перечнях отчёта — нарушение СЛОВАМИ с
    местом записи, а не падение гейта трассой «unhashable type».
    Упавший гейт читается как беда инструмента, и подделка уходит из
    вида."""
    zero = {v: 0 for v in ex.ARBITER_VERDICTS}
    extra = {"verdicts": {"verdict": "обработчик прав", "basis": "б"},
             "unresolved": {"reason": "противоречивые решения"},
             "awaiting_owner": {"question": "Q-2-арб-D1",
                                "reason": "ждёт ответа владельца"}}
    for field, tail in extra.items():
        for did in (["D1"], None, {"x": 1}, 5):
            rep = {"verdicts": [], "by_verdict": zero,
                   "unresolved": [], "awaiting_owner": []}
            rep[field] = [{"deadlock": did, **tail}]
            if field == "verdicts":
                rep["by_verdict"] = {**zero, "обработчик прав": 1}
            probs = gate_arbiter._verdict_problems(rep, [{"id": "D1"}])
            assert any(f"перечня {field}" in p
                       and "идентификатор тупика не строка" in p
                       for p in probs), (field, did, probs)
            assert any("ни записи «не разрешено»" in p
                       for p in probs), (field, did, probs)


def test_forged_report_fields_of_wrong_type_do_not_fall_the_gate(
        workspace):
    """Тот же класс на РЕАЛЬНОМ каталоге: null вместо перечня, список
    вместо соответствия, строка вместо перечня. Гейт называет форму
    словами и продолжает судить, а не падает `len(None)`."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                   _enforcing_worker)
    assert gate_arbiter.check(exdir)[0] == []
    for field, value, needle in (
            ("added_questions", None, None),
            ("question_reuse", [], "question_reuse"),
            ("enforced_deadlocks", "abc", "enforced_deadlocks"),
            ("by_verdict", None, "by_verdict")):
        rep = _report_of(exdir)                   # каждый раз с честного
        rep[field] = value
        _write_json(exdir / "arbiter.json", rep)
        problems, counters = gate_arbiter.check(exdir)
        assert isinstance(counters["questions"], int)
        if needle:
            assert any(needle in p for p in problems), (field, problems)


def test_the_arbiter_and_the_gate_match_the_registry_by_one_law(
        workspace):
    """Ревью A09 №2, находка 6: перечень тупиков БЕЗ ключей (вердикт
    прогона до АЦ-4) сопоставляется с реестром ОДНОЙ функцией у суда и
    у его проверки. Прежде арбитр по такому перечню не судил ничего
    (он знал только пару «id + ключ»), а гейт судил находку запасным
    проходом по id: предмет у них расходился."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    vs = json.loads((exdir / "verdict.json").read_text(encoding="utf-8"))
    for d in vs["deadlocks"]:
        d.pop("key", None)                        # форма до АЦ-4
    _write_json(exdir / "verdict.json", vs)
    matched = ex.match_listed_deadlocks(_registry_of(exdir),
                                        vs["deadlocks"])
    assert all(matched[i] is not None for i in ids)

    rep = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                         _enforcing_worker)
    assert {v["deadlock"] for v in rep["verdicts"]} == set(ids)
    assert all(_finding(exdir, i)["state"] == "арбитр: обработчик прав"
               for i in ids)
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_a_second_code_lock_keeps_the_first_words():
    """Ревью A09 №2, находка 7: повторный запрет замка по уже
    запертому спору не затирает ПЕРВЫХ слов и первого круга — они
    остаются в `code_rejection`, а новые дописываются историей. Прежде
    досье арбитра теряло слова, которыми спор дозрел до тупика."""
    reg = {"findings": [
        {"id": "A", "key": "k", "severity": "КРИТИЧЕСКОЕ",
         "state": "тупик", "deadlock_kind": ex.CODE_DEADLOCK_KIND,
         "code_rejected_round": 2, "code_rejection": "первые слова замка",
         "opened_round": 1, "closed_round": None}]}
    ex.mark_code_rejected(reg, {"k": "вторые слова замка"}, 5)
    f = reg["findings"][0]
    assert f["code_rejection"] == "первые слова замка"
    assert f["code_rejected_round"] == 2
    assert f["code_rejection_history"] == [
        {"round": 2, "reason": "первые слова замка"},
        {"round": 5, "reason": "вторые слова замка"}]
    ex.mark_code_rejected(reg, {"k": "вторые слова замка"}, 5)
    assert len(f["code_rejection_history"]) == 2   # дубля истории нет

    # Первый запрет по чистой находке истории не заводит.
    fresh = {"findings": [
        {"id": "B", "key": "k", "severity": "КРИТИЧЕСКОЕ",
         "state": "открыто", "opened_round": 1, "closed_round": None}]}
    ex.mark_code_rejected(fresh, {"k": "слова замка"}, 3)
    g = fresh["findings"][0]
    assert g["code_rejection"] == "слова замка"
    assert g["code_rejected_round"] == 3
    assert "code_rejection_history" not in g


def test_the_panel_summary_names_stray_and_awaiting_disputes(workspace):
    """Ревью A09 №2, находка 7 (панель): счётчик лишних упоминаний и
    споры, ждущие ответа владельца, доходят до сводки прогона — иначе
    оператор видел бы только красный гейт без объяснения. Старая форма
    отчёта сводку не роняет."""
    from cdpm import webui
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                   _enforcing_worker)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                         _enforcing_worker)
    s = webui._arbiter_summary(rep)
    assert s["awaiting_owner"] == 1
    assert s["awaiting_owner_deadlocks"][0]["deadlock"] == fid
    assert s["awaiting_owner_deadlocks"][0]["question"] \
        == m.stable_question_id("2", f"арб-{fid}")
    assert s["unresolved_foreign"] == 0
    old = webui._arbiter_summary({"deadlocks": 1, "by_verdict": {},
                                  "enforced": 0, "added_questions": [],
                                  "patch_rejections": []})
    assert old["awaiting_owner"] == 0 and old["unresolved_foreign"] == 0
    assert old["awaiting_owner_deadlocks"] == []

def _counters_add_up(exdir, rep) -> None:
    """Руководство 10.7, «Счёт»: при ЗЕЛЁНОМ гейте сумма трёх
    счётчиков `by_verdict` меньше числа рассмотренных тупиков ровно на
    число тупиков БЕЗ решения — неразрешённых плюс ждущих ответа
    владельца."""
    problems, counters = gate_arbiter.check(exdir)
    assert problems == [], problems
    without = len(rep.get("unresolved") or []) \
        - (rep.get("unresolved_foreign") or 0) \
        + len(rep.get("awaiting_owner") or [])
    assert sum(rep["by_verdict"].values()) == rep["deadlocks"] - without
    assert counters["deadlocks"] == len(
        json.loads((exdir / "verdict.json")
                   .read_text(encoding="utf-8"))["deadlocks"])


def test_the_three_counters_add_up_on_a_green_gate(workspace):
    """Та самая фраза руководства — проверяется числом, на всех трёх
    исходах: решение, «не разрешено» и «ждёт ответа владельца». Прежде
    счёт шёл суммой длин перечней, и фраза была неверна."""
    kit, out, exdir = workspace
    # Два спора разных ключей: один решён, второй оставлен без решения.
    ex.run_exchange(kit, out, exdir,
                    _make_auditor([[dict(_REMARK_DISTORT),
                                    dict(_REMARK_OMIT)]] * 4),
                    _make_worker({}), max_rounds=6)
    disputed = _id_by_text(exdir, "Потеряно уточнение")
    rep = ex.run_arbiter(kit, exdir, _dup_arbiter(only=disputed),
                         _enforcing_worker)
    assert rep["deadlocks"] == 2 and len(rep["unresolved"]) == 1
    _counters_add_up(exdir, rep)

    # Чистый заход: неразрешённых нет — сумма равна числу тупиков.
    rep2 = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                          _enforcing_worker)
    assert rep2["unresolved"] == []
    assert sum(rep2["by_verdict"].values()) == rep2["deadlocks"] == 2
    _counters_add_up(exdir, rep2)


def test_the_three_counters_add_up_with_an_awaiting_dispute(workspace):
    """Тот же счёт с третьим исходом: спор ждёт ответа владельца —
    решения у него нет, но из числа рассмотренных он не исчезает."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                   _enforcing_worker)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                         _enforcing_worker)
    assert rep["deadlocks"] == 1 and len(rep["awaiting_owner"]) == 1
    assert sum(rep["by_verdict"].values()) == 0
    _counters_add_up(exdir, rep)


# ---------------------------------------------------------------------------
# ТРЕТЬЕ неавторское ревью A09 (12.09.2026), К-1: решение, названное по
# РОДИЧУ спора, — неоднозначность ВСЕГО спора
# ---------------------------------------------------------------------------

def _kin_arbiter(lead_verdict, kin_verdict, ids, seen=None):
    """Арбитр, назвавший РОДИЧА спора: ведущую находку — одним
    решением, родича (он назван в досье блока) — другим, либо только
    родича, либо только ведущую."""
    def arbiter(prompt):
        if seen is not None:
            seen["shown"] = _shown_ids(prompt)
            seen["prompt"] = prompt
            seen["calls"] = seen.get("calls", 0) + 1
        rows = []
        if lead_verdict is not None:
            rows.append({"deadlock": ids[0], "verdict": lead_verdict,
                         "basis": "клауза подтверждает строку свода"})
        if kin_verdict is not None:
            rows.append({"deadlock": ids[1], "verdict": kin_verdict,
                         "basis": "клауза читается в пользу аудитора"})
        return json.dumps({"verdicts": rows}, ensure_ascii=False)
    return arbiter


def test_decision_named_by_a_kin_contradicting_the_lead_is_unresolved(
        workspace):
    """К-1 третьего ревью: арбитр назвал ВЕДУЩУЮ находку спора
    «обработчик прав», а её РОДИЧА (того же ключа, названного в досье
    блока) — «аудитор прав». Родич в `blocks` не лежит (блок один на
    ключ), и судья честно вернул его «неизвестным идентификатором» —
    а арбитраж отбрасывал такую запись как постороннюю: противоречащее
    решение исчезало молча, принятым оставалось первое, обе находки
    ключа закрывались «арбитр: обработчик прав», гейт зеленел и цикл
    приходил к «успеху». Прямой контрпример §1.5 контракта П0.

    Закон: упоминание родича — неоднозначность ВСЕГО спора. Решения
    нет ни у одной находки ключа, причина названа словами и называет
    родича, обе находки живы, «успех» цикла невозможен."""
    kit, out, exdir = workspace
    _echoing_cycle(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    assert len(ids) == 2
    seen = {}
    rep = ex.run_arbiter(kit, exdir,
                         _kin_arbiter("обработчик прав", "аудитор прав",
                                      ids, seen),
                         _enforcing_worker)
    # Блок по-прежнему ОДИН на спор, родич в нём назван, и арбитра
    # прямо предупредили, каким идентификатором отвечать.
    assert seen["shown"] == [ids[0]] and ids[1] in seen["prompt"]
    assert "В ответе назови ОДИН идентификатор" in seen["prompt"]
    assert seen["calls"] == 2                     # ровно один повтор

    # Решения нет НИ У ОДНОЙ находки ключа.
    assert rep["verdicts"] == []
    assert sum(rep["by_verdict"].values()) == 0
    assert rep["enforced"] == 0 and rep["added_questions"] == []
    # Исход — «не разрешено» у обеих, причина называет родича.
    assert [u["deadlock"] for u in rep["unresolved"]] == ids
    for u in rep["unresolved"]:
        assert f"родичу [{ids[1]}]" in u["reason"]
        assert f"ведущую находку {ids[0]}" in u["reason"]
    # Родич — не посторонний: он находка ЭТОГО спора, отдельным счётом
    # не считается и второй записи о себе не рождает.
    assert rep["unresolved_foreign"] == 0
    assert rep["deadlocks"] == 2

    assert {_finding(exdir, i)["state"] for i in ids} \
        == {"не разрешено арбитром"}
    for i in ids:
        assert f"родичу [{ids[1]}]" \
            in _finding(exdir, i)["arbiter_unresolved"]

    # Контрпример закрыт: обе находки живые, «успех» невозможен, а
    # гейт зелен — исход у спора назван явно и ОДИН на обе находки.
    reg = _registry_of(exdir)
    assert {b["id"] for b in ex.registry_blockers(reg)} == set(ids)
    assert ex.decide(_CLEAN_HISTORY, 9, registry=reg) \
        == ex.VERDICT_NOT_CLOSED
    problems, counters = gate_arbiter.check(exdir)
    assert problems == [], problems
    assert counters["verdicts"] == 0 and counters["unresolved"] == 2
    _counters_add_up(exdir, rep)


def test_answer_only_by_the_kin_names_the_true_reason(workspace):
    """Тот же закон при ответе ТОЛЬКО по родичу: спор и прежде
    оставался неразрешённым (fail-closed), но причина врала —
    «идентификатор не назван», хотя решение было названо, просто не
    тем идентификатором. Теперь причина несёт оба слова, и родич в ней
    назван."""
    kit, out, exdir = workspace
    _echoing_cycle(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    rep = ex.run_arbiter(kit, exdir,
                         _kin_arbiter(None, "обработчик прав", ids),
                         _enforcing_worker)
    assert rep["verdicts"] == []
    assert [u["deadlock"] for u in rep["unresolved"]] == ids
    for u in rep["unresolved"]:
        assert "нет решения" in u["reason"]           # ведущую не назвали
        assert f"родичу [{ids[1]}]" in u["reason"]    # а назвали родича
    assert rep["unresolved_foreign"] == 0
    assert {_finding(exdir, i)["state"] for i in ids} \
        == {"не разрешено арбитром"}
    assert ex.decide(_CLEAN_HISTORY, 9, registry=_registry_of(exdir)) \
        == ex.VERDICT_NOT_CLOSED
    assert gate_arbiter.check(exdir)[0] == []


def test_answer_by_the_lead_alone_resolves_the_whole_key(workspace):
    """Положительный контроль к К-1: назван ОДИН ведущий
    идентификатор — спор решён, и решение несут ОБЕ находки ключа.
    Закон ловит именно упоминание родича, а не всякий ответ."""
    kit, out, exdir = workspace
    _echoing_cycle(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    seen = {}
    rep = ex.run_arbiter(kit, exdir,
                         _kin_arbiter("обработчик прав", None, ids, seen),
                         _enforcing_worker)
    assert seen["calls"] == 1                     # повтора не потребовалось
    assert {v["deadlock"]: v["verdict"] for v in rep["verdicts"]} \
        == {i: "обработчик прав" for i in ids}
    assert rep["unresolved"] == [] and rep["unresolved_foreign"] == 0
    assert {_finding(exdir, i)["state"] for i in ids} \
        == {"арбитр: обработчик прав"}
    reg = _registry_of(exdir)
    assert ex.live_findings(reg) == []
    assert gate_arbiter.check(exdir)[0] == []


def test_empty_dossier_is_refused_in_words_and_never_judged(workspace):
    """Остаток третьего ревью (fail-closed): у находки потерян ключ —
    замечание по ней не находится ни в одном круге обмена, и досье
    пусто. Прежде блок уходил арбитру с «Замечание аудитора: None», и
    решение по ПУСТОМУ досье закрывало живой спор. Теперь такой спор
    арбитру не предъявляется вовсе (модель не зовут), отказ назван
    словами в отчёте и реестре, находка остаётся живой."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    reg = _registry_of(exdir)
    for f in reg["findings"]:
        if f["id"] == fid:
            f.pop("key", None)
    _write_json(exdir / "registry.json", reg)
    calls = []

    def arbiter(prompt):
        calls.append(prompt)
        return _make_arbiter("обработчик прав")(prompt)

    rep = ex.run_arbiter(kit, exdir, arbiter, _enforcing_worker)
    assert calls == []                       # пустое досье не судится
    assert rep["verdicts"] == [] and rep["enforced"] == 0
    assert [u["deadlock"] for u in rep["unresolved"]] == [fid]
    assert "нет ключа" in rep["unresolved"][0]["reason"]
    f = _finding(exdir, fid)
    assert f["state"] == "не разрешено арбитром"
    assert "нет ключа" in f["arbiter_unresolved"]
    assert ex.decide(_CLEAN_HISTORY, 9, registry=_registry_of(exdir)) \
        == ex.VERDICT_NOT_CLOSED
    assert gate_arbiter.check(exdir)[0] == []


# ---------------------------------------------------------------------------
# ЧЕТВЁРТОЕ неавторское ревью A09 (12.09.2026), критическая находка:
# СЛЕПОЙ СУД перенесённого спора на границе мегакруга.
#
# Мегакруг уносит весь каталог обмена в архив «exchange-rev-N», перенося
# в новый цикл лишь реестр и токены. Живой спор границу переживает
# (`reopen_unresolved_disputes` возвращает его тупиком нового
# verdict.json), а его СОДЕРЖАНИЕ — нет: досье собиралось только по
# `exchange/rounds`. Арбитру уходил блок «Замечание аудитора: None /
# Доказательство: None / Предложение: None / Клауза источника [None]:
# <нет> / Строка свода: <нет>», он отвечал «обработчик прав», живой
# блокирующий спор закрывался БЕЗ СОДЕРЖАНИЯ, реестр пустел, `decide`
# давал «успех», гейт зеленел. Контрпример §1.3 контракта П0 (решение
# обязано соответствовать исходным данным) и §1.5 (живой спор не
# закрывается решением, которого не было).
#
# Закон (вариант А + восстановление содержания):
# 1) досье ищет содержание ВЕЗДЕ, где оно штатно лежит — круги текущего
#    цикла, круги архивов прежних циклов того же прогона (по
#    возрастанию ревизии, последнее по времени выигрывает) и
#    `audit-rejections.json` (носитель запрещённого повтора);
# 2) пусто и после поиска — ОТКАЗ ЗАПИСЬЮ при ЛЮБОЙ находке: модель не
#    зовётся, причина названа словами, спор остаётся живым;
# 3) слова причины различают «нет ключа» и «содержания не нашлось».
# ---------------------------------------------------------------------------

def _carried_dispute(kit, out, exdir):
    """Спор, неразрешённый арбитром в цикле 1 и перенесённый в
    мегакруг 2 при МОЛЧАЩЕМ аудиторе (воспроизведение P6 рецензента):
    круги с содержанием уехали в архив, реестр перешёл в новый цикл,
    `reopen_unresolved_disputes` вернул спор тупиком."""
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    assert _finding(exdir, fid)["state"] == "не разрешено арбитром"
    rev = _next_cycle(exdir)
    summary = ex.run_exchange(kit, out, exdir, _make_auditor([[], [], []]),
                              _make_worker({}), max_rounds=4)
    assert [d["id"] for d in summary["deadlocks"]] == [fid]
    assert _finding(exdir, fid)["state"] == "тупик"
    return fid, rev


def _watching_arbiter(verdict="обработчик прав", seen=None):
    def arbiter(prompt):
        if seen is not None:
            seen["prompt"] = prompt
            seen["calls"] = seen.get("calls", 0) + 1
        return _make_arbiter(verdict)(prompt)
    return arbiter


def test_p6_carried_dispute_is_judged_by_its_content_from_the_archive(
        workspace):
    """P6: спор, перенесённый в мегакруг 2, судится ПО СОДЕРЖАНИЮ,
    найденному в архиве прежнего цикла, — а не по пустому бланку.

    «Минус»: на прежнем поведении досье пусто (`remark is None`), и
    блок уходит арбитру со словом None во всех строках предмета."""
    kit, out, exdir = workspace
    fid, rev = _carried_dispute(kit, out, exdir)
    assert rev.name == "exchange-rev-1"
    # Круги НОВОГО цикла содержания спора не несут: аудитор молчал.
    assert all(json.loads(p.read_text(encoding="utf-8"))["remarks"] == []
               for p in (exdir / "rounds").glob("round-*/remarks.json"))

    d = ex._deadlock_dossier(exdir, _finding(exdir, fid))
    assert d["remark"]["text"] == _REMARK_DISTORT["text"]
    assert d["remark"]["evidence"] == _REMARK_DISTORT["evidence"]
    assert d["rejections"] and all(r == "источник не подтверждает"
                                   for r in d["rejections"])
    assert d["remark_from_ban"] is False

    seen = {}
    rep = ex.run_arbiter(kit, exdir, _watching_arbiter(seen=seen),
                         _enforcing_worker)
    block = _block_of(seen["prompt"], fid)
    assert "Замечание аудитора: None" not in block
    assert "Доказательство аудитора: None" not in block
    assert "Предложение аудитора: None" not in block
    assert "Клауза источника [None]" not in block
    assert _REMARK_DISTORT["text"] in block
    assert _REMARK_DISTORT["evidence"] in block
    assert _REMARK_DISTORT["proposal"] in block
    assert "Отдых на базе" in block               # текст клаузы найден
    assert "Строка свода: <нет>" not in block
    assert "Основания отклонений обработчика: <нет>" not in block
    assert [v["deadlock"] for v in rep["verdicts"]] == [fid]
    assert _finding(exdir, fid)["state"] == "арбитр: обработчик прав"
    assert gate_arbiter.check(exdir)[0] == []


def test_p6_without_the_archive_the_dispute_is_refused_in_words(
        workspace):
    """Тот же перенос, но содержания НЕТ НИГДЕ (архив прежнего цикла
    утрачен). Fail-closed: модель не зовётся вовсе, причина названа
    словами «содержания спора не нашлось: судить нечем», спор остаётся
    живым и блокирующим, `decide` — «не закрыто», гейт зелёный.

    «Минус»: на прежнем поведении арбитр звался, получал пустой бланк
    и закрывал живой спор «обработчик прав» — цикл приходил к
    «успеху»."""
    import shutil
    kit, out, exdir = workspace
    fid, rev = _carried_dispute(kit, out, exdir)
    shutil.rmtree(rev)
    assert ex.archived_exchange_dirs(exdir) == []
    assert ex._deadlock_dossier(exdir, _finding(exdir, fid))["remark"] \
        is None

    seen = {}
    rep = ex.run_arbiter(kit, exdir, _watching_arbiter(seen=seen),
                         _enforcing_worker)
    assert "calls" not in seen                   # модель НЕ звалась
    assert rep["verdicts"] == [] and rep["enforced"] == 0
    assert [u["deadlock"] for u in rep["unresolved"]] == [fid]
    reason = rep["unresolved"][0]["reason"]
    assert "содержания спора не нашлось: судить нечем" in reason
    assert "архивах прежних циклов" in reason
    assert "нет ключа" not in reason             # причина ДРУГАЯ
    f = _finding(exdir, fid)
    assert f["state"] == "не разрешено арбитром"
    assert "содержания спора не нашлось" in f["arbiter_unresolved"]
    reg = _registry_of(exdir)
    assert fid in [b["id"] for b in ex.registry_blockers(reg)]
    assert ex.decide(_CLEAN_HISTORY, 9, registry=reg) \
        == ex.VERDICT_NOT_CLOSED
    assert gate_arbiter.check(exdir)[0] == []


def test_p6d_code_locked_repeat_keeps_the_content_of_the_dispute(
        workspace):
    """P6d (системно, от аудитора не зависит): спор замка кода в
    мегакруге 2. Аудитор ПОВТОРЯЕТ замечание, но повтор машинно
    запрещён замком (§12.8), и в `remarks.json` нового цикла его нет
    вовсе — прежде досье было пусто ВСЕГДА, и слепой суд наступал без
    чьей-либо ошибки.

    Теперь содержание живёт в двух носителях: архив прежнего цикла и
    сам файл отбраковки повтора, где отклонённое замечание СОХРАНЕНО.

    «Минус»: на прежнем поведении `audit-rejections.json` хранил лишь
    идентификатор и причину, а досье оставалось пустым."""
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    assert _finding(exdir, fid)["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    assert _finding(exdir, fid)["state"] == "не разрешено арбитром"
    _next_cycle(exdir)
    same = [dict(_REMARK_DISTORT)]
    ex.run_exchange(kit, out, exdir, _make_auditor([same, same, same]),
                    _make_worker({}), max_rounds=4)

    rej = json.loads((exdir / "rounds" / "round-01"
                      / "audit-rejections.json").read_text(
                          encoding="utf-8"))
    assert "повторное указание запрещено" in rej[0]["reason"]
    # Носитель несёт СОДЕРЖАНИЕ, а не только идентификатор и причину.
    assert rej[0]["remark"]["text"] == _REMARK_DISTORT["text"]
    assert rej[0]["remark"]["evidence"] == _REMARK_DISTORT["evidence"]
    assert json.loads((exdir / "rounds" / "round-01" / "remarks.json")
                      .read_text(encoding="utf-8"))["remarks"] == []

    d = ex._deadlock_dossier(exdir, _finding(exdir, fid))
    assert d["remark"]["text"] == _REMARK_DISTORT["text"]
    seen = {}
    rep = ex.run_arbiter(kit, exdir, _watching_arbiter(seen=seen),
                         _enforcing_worker)
    block = _block_of(seen["prompt"], fid)
    assert "Замечание аудитора: None" not in block
    assert _REMARK_DISTORT["text"] in block
    assert "Причина запрета кодом:" in block     # слова замка на месте
    assert [v["deadlock"] for v in rep["verdicts"]] == [fid]
    assert _finding(exdir, fid)["state"] == "арбитр: обработчик прав"
    assert gate_arbiter.check(exdir)[0] == []


def test_p6d_the_banned_repeat_alone_carries_the_dispute(workspace):
    """Тот же P6d, но архив прежнего цикла утрачен: содержание спора
    восстанавливается ТОЛЬКО из файла отбраковки запрещённого повтора
    — носителя, куда оно переносится штатно. Досье честно говорит,
    откуда взято замечание (`remark_from_ban`).

    «Минус»: на прежнем поведении носитель содержания не хранил, и
    спор закрывался вслепую."""
    import shutil
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    rev = _next_cycle(exdir)
    same = [dict(_REMARK_DISTORT)]
    ex.run_exchange(kit, out, exdir, _make_auditor([same, same, same]),
                    _make_worker({}), max_rounds=4)
    shutil.rmtree(rev)
    assert ex.archived_exchange_dirs(exdir) == []

    d = ex._deadlock_dossier(exdir, _finding(exdir, fid))
    assert d["remark"]["text"] == _REMARK_DISTORT["text"]
    assert d["remark_from_ban"] is True
    # Оснований отклонения обработчика по этому кругу нет — и они не
    # выдумываются: арбитр видит «<нет>».
    assert d["rejections"] == []
    seen = {}
    rep = ex.run_arbiter(kit, exdir, _watching_arbiter(seen=seen),
                         _enforcing_worker)
    block = _block_of(seen["prompt"], fid)
    assert _REMARK_DISTORT["text"] in block
    assert "Основания отклонений обработчика: <нет>" in block
    assert [v["deadlock"] for v in rep["verdicts"]] == [fid]
    assert gate_arbiter.check(exdir)[0] == []


def test_p6e_kin_dispute_across_the_megacycle_is_judged_by_content(
        workspace):
    """P6e (сквозная цепочка рецензента): спор из ДВУХ находок одного
    ключа, только что признанный «не разрешено» по закону родича
    (К-1), переносится в мегакруг 2 при молчащем аудиторе. Прежде он
    закрывался слепо — «обработчик прав» по пустому бланку, обе
    находки разом, `decide` = «успех».

    Теперь блок спора несёт и содержание из архива, и перечень родичей
    с прежней неразрешённостью.

    «Минус»: assert-ы на содержание блока краснеют на прежнем
    поведении."""
    kit, out, exdir = workspace
    _echoing_cycle(kit, out, exdir)
    ids = _deadlock_ids(exdir)
    assert len(ids) == 2
    ex.run_arbiter(kit, exdir,
                   _kin_arbiter("обработчик прав", "аудитор прав", ids),
                   _enforcing_worker)
    assert {_finding(exdir, i)["state"] for i in ids} \
        == {"не разрешено арбитром"}
    _next_cycle(exdir)
    summary = ex.run_exchange(kit, out, exdir, _make_auditor([[], [], []]),
                              _make_worker({}), max_rounds=4)
    assert sorted(d["id"] for d in summary["deadlocks"]) == sorted(ids)

    seen = {}
    rep = ex.run_arbiter(kit, exdir, _watching_arbiter(seen=seen),
                         _enforcing_worker)
    assert _shown_ids(seen["prompt"]) == [ids[0]]   # спор один, блок один
    block = _block_of(seen["prompt"], ids[0])
    assert "Замечание аудитора: None" not in block
    assert _REMARK_DISTORT["text"] in block
    assert _REMARK_DISTORT["evidence"] in block
    assert "Спор ОДИН" in block and ids[1] in block
    assert "прежде не разрешено:" in block
    assert {v["deadlock"] for v in rep["verdicts"]} == set(ids)
    assert {_finding(exdir, i)["state"] for i in ids} \
        == {"арбитр: обработчик прав"}
    assert gate_arbiter.check(exdir)[0] == []


def test_content_in_the_current_rounds_is_judged_as_before(workspace):
    """Положительный контроль: спор, чьё содержание лежит в кругах
    ТЕКУЩЕГО цикла, судится ровно как прежде — архивов нет, запасной
    носитель не участвует, блок и решение прежние."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    assert ex.archived_exchange_dirs(exdir) == []
    d = ex._deadlock_dossier(exdir, _finding(exdir, fid))
    assert d["remark"]["text"] == _REMARK_DISTORT["text"]
    assert d["remark_from_ban"] is False
    assert d["rejections"]

    seen = {}
    rep = ex.run_arbiter(kit, exdir, _watching_arbiter(seen=seen),
                         _enforcing_worker)
    block = _block_of(seen["prompt"], fid)
    assert _REMARK_DISTORT["text"] in block
    assert [v["deadlock"] for v in rep["verdicts"]] == [fid]
    assert _finding(exdir, fid)["state"] == "арбитр: обработчик прав"
    assert gate_arbiter.check(exdir)[0] == []


def _fake_archive(exdir, name: str, text: str, finding: dict) -> None:
    """Архив прежнего цикла ТОЙ ЖЕ истории: один круг с тем же
    замечанием (тот же КЛЮЧ), но со своим текстом — чтобы видеть, чьё
    содержание выиграло, — и реестр, узнающий находку по паре
    «идентификатор + ключ».

    Реестр обязателен (пятое неавтор-ревью A09): каталог
    «<обмен>-rev-NNN» БЕЗ реестра историей спора не считается вовсе —
    под тем же именем переизвлечение уносит обмены ЧУЖОГО свода.
    Прежде помощник изображал архив, какого панель не создаёт."""
    rdir = exdir.parent / name / "rounds" / "round-01"
    rdir.mkdir(parents=True)
    _write_json(exdir.parent / name / "registry.json", {"findings": [
        {"id": finding["id"], "key": finding["key"], "state": "тупик"}]})
    rm = dict(_REMARK_DISTORT, id="A1-001", text=text)
    _write_json(rdir / "remarks.json", {"round": 1, "remarks": [rm]})
    _write_json(rdir / "response.json", {"answers": [
        {"remark": "A1-001", "verdict": "ОТКЛОНЕНО",
         "basis": f"основание из {name}"}]})


def test_archives_are_walked_in_revision_order_not_in_file_order(
        workspace):
    """Детерминизм обхода: архивы берутся по ЧИСЛУ ревизии, а не по
    имени и не по порядку файловой системы, и последнее по времени
    содержание выигрывает. Однофамильцы (файл, каталог без номера,
    чужой прогон) не участвуют.

    «Минус»: сортировка по имени ставит «-rev-10» между 1 и 2, и
    выигрывает содержание ВТОРОЙ ревизии, а не десятой."""
    kit, out, exdir = workspace
    fid, _rev = _carried_dispute(kit, out, exdir)
    f = _finding(exdir, fid)
    _fake_archive(exdir, "exchange-rev-2", "версия ревизии 2", f)
    _fake_archive(exdir, "exchange-rev-10", "версия ревизии 10", f)
    (exdir.parent / "exchange-rev-7").write_text("не каталог",
                                                 encoding="utf-8")
    (exdir.parent / "exchange-rev-хвост").mkdir()
    (exdir.parent / "иной-exchange-rev-3").mkdir()
    assert [p.name for p in ex.archived_exchange_dirs(exdir)] \
        == ["exchange-rev-1", "exchange-rev-2", "exchange-rev-10"]

    d = ex._deadlock_dossier(exdir, _finding(exdir, fid))
    assert d["remark"]["text"] == "версия ревизии 10"
    assert d["rejections"][-1] == "основание из exchange-rev-10"
    assert "основание из exchange-rev-2" in d["rejections"]
    # Дважды подряд — тот же ответ.
    assert json.dumps(d, ensure_ascii=False, sort_keys=True) \
        == json.dumps(ex._deadlock_dossier(exdir, _finding(exdir, fid)),
                      ensure_ascii=False, sort_keys=True)


def _fresh_workspace(root):
    """Отдельная площадка, как фикстура `workspace`, — для сверки двух
    независимых прогонов байт в байт."""
    from cdpm import extract, freeze
    from test_cdpm_extract import _RULES_A, _RULES_B, fake_llm
    src = root / "src"
    src.mkdir(parents=True)
    (src / "RULES-A.md").write_text(_RULES_A, encoding="utf-8")
    (src / "RULES-B.md").write_text(_RULES_B, encoding="utf-8")
    kit = root / "kit"
    freeze.freeze([src / "RULES-A.md", src / "RULES-B.md"], kit)
    out = root / "extract"
    extract.run_extract(kit, out, fake_llm, "Полярис (тест)")
    return kit, out, root / "exchange"


def test_two_runs_of_the_carried_dispute_are_byte_identical(tmp_path):
    """Детерминизм: два независимых прогона одного сценария (спор
    через границу мегакруга, досье из архива) дают байт в байт те же
    артефакты обмена и то же задание арбитру."""
    got = []
    for name in ("run-a", "run-b"):
        kit, out, exdir = _fresh_workspace(tmp_path / name)
        fid, _rev = _carried_dispute(kit, out, exdir)
        seen = {}
        ex.run_arbiter(kit, exdir, _watching_arbiter(seen=seen),
                       _enforcing_worker)
        got.append((fid, seen["prompt"],
                    {n: (exdir / n).read_text(encoding="utf-8")
                     for n in ("arbiter.json", "registry.json",
                               "verdict.json")}))
    assert got[0][0] == got[1][0]
    assert got[0][1] == got[1][1]
    for name in got[0][2]:
        assert got[0][2][name] == got[1][2][name], name
# ======================================================================
# Пятое неавтор-ревью A09 (12.09.2026), критическая находка К-1:
# «досье судит спор по содержанию ЧУЖОГО свода». Закон — спор судится
# только по содержанию СВОЕЙ истории; переизвлечение историю обрывает.
# ======================================================================


def _reextract_boundary(kit, out, exdir):
    """Граница ПЕРЕИЗВЛЕЧЕНИЯ штатным путём панели: свод A с тупиком
    замка кода уезжает в «exchange-rev-001» ЦЕЛИКОМ (реестр в новый
    цикл не переносится — новый свод начинает историю с нуля), после
    чего в том же каталоге прогона живёт свод B с ОБЫЧНЫМ тупиком по
    тому же ключу (ключ строится от якоря источника и у нового свода
    тот же). Подделок нет: ровно то, что делает кнопка панели."""
    from cdpm import webui
    _code_rejected_cycle(kit, out, exdir)
    old_fid = _deadlock_ids(exdir)[0]
    assert _finding(exdir, old_fid)["deadlock_kind"] \
        == ex.CODE_DEADLOCK_KIND
    name = webui._archive_exchange_for_reextract(exdir.parent)
    assert name == "exchange-rev-001"
    assert not exdir.exists()
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    # Реестр не перенесён — у спора нового свода СВОЙ идентификатор.
    assert fid != old_fid
    assert _finding(exdir, fid)["key"] \
        == json.loads((exdir.parent / name / "registry.json")
                      .read_text(encoding="utf-8"))["findings"][0]["key"]
    return fid, exdir.parent / name


def test_reextract_archive_is_not_the_history_of_this_dispute(workspace):
    """К-1: архив ПЕРЕИЗВЛЕЧЕНИЯ — не история этого спора.

    Досье тянуло из чужого свода терминальную отбраковку замком кода:
    спор нового свода объявлялся «замком кода», вердикт «аудитор
    прав» не исполнялся заплатой, а владельцу ставился вопрос с
    цитатой запрета, которого в этом своде не было (§1.3 — суд не по
    тому, что видел цикл; §1.1 — реестр говорит «повтор без замка»,
    а досье и исполнение говорят «замок кода»).

    «Минус»: на коде до правки `code_locked_dispute` истинно, блок
    несёт «Причина запрета кодом» и заплату чужого свода,
    `enforced_deadlocks` пуст, владельцу ставится вопрос, состояние
    остаётся «тупик»."""
    kit, out, exdir = workspace
    fid, arch = _reextract_boundary(kit, out, exdir)
    f = _finding(exdir, fid)
    assert f.get("deadlock_kind") != ex.CODE_DEADLOCK_KIND
    assert not f.get("code_rejection")
    # Пояс: метка обрыва истории лежит в архиве.
    mark = json.loads((arch / ex.HISTORY_BREAK_FILE)
                      .read_text(encoding="utf-8"))
    assert mark["kind"] == "переизвлечение"
    assert "история спора здесь оборвана" in mark["note"]
    # Подтяжки: реестр ПРЕЖНЕГО свода цел, но этой находки не знает.
    assert (arch / "registry.json").is_file()
    assert all(g["id"] != fid for g in
               json.loads((arch / "registry.json")
                          .read_text(encoding="utf-8"))["findings"])
    # Каталог виден как архив прогона, но историей спора не является.
    assert [p.name for p in ex.archived_exchange_dirs(exdir)] \
        == [arch.name]
    assert ex.archived_exchange_dirs(exdir, f) == []

    d = ex._deadlock_dossier(exdir, f)
    assert d["code_rejections"] == []
    assert ex.code_locked_dispute(f, d) is False
    seen = {}
    rep = ex.run_arbiter(kit, exdir,
                         _watching_arbiter("аудитор прав", seen=seen),
                         _enforcing_worker)
    block = _block_of(seen["prompt"], fid)
    assert "Причина запрета кодом" not in block
    assert "вне перечня миграций" not in block
    assert rep["enforced_deadlocks"] == [fid]
    assert rep["unenforced"] == [] and rep["added_questions"] == []
    assert _finding(exdir, fid)["state"] == "арбитр: аудитор прав"
    assert gate_arbiter.check(exdir)[0] == []
    assert ex.decide(_CLEAN_HISTORY, 9, registry=_registry_of(exdir)) \
        == "успех"


def test_megacycle_archive_is_read_and_the_reextract_one_is_not(
        workspace):
    """Два каталога одного вида имени рядом: «exchange-rev-001» —
    переизвлечение (свод A, чужое содержание со словами замка кода),
    «exchange-rev-002» — мегакруг ТОГО ЖЕ свода B (содержание спора).
    Обе архивации — штатным путём панели. Досье берёт только второй,
    и именно в нём находит замечание: в кругах текущего цикла его нет
    (аудитор молчит).

    «Минус»: на коде до правки в обход попадают ОБА, и терминальная
    отбраковка чужого свода доезжает до арбитра."""
    from cdpm import webui
    kit, out, exdir = workspace
    fid, arch_foreign = _reextract_boundary(kit, out, exdir)
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    assert _finding(exdir, fid)["state"] == "не разрешено арбитром"
    own = webui._archive_finished_exchange(exdir.parent)
    assert own == "exchange-rev-002"
    # Архив мегакруга метки обрыва НЕ несёт: это тот же свод.
    assert not (exdir.parent / own / ex.HISTORY_BREAK_FILE).is_file()
    summary = ex.run_exchange(kit, out, exdir,
                              _make_auditor([[], [], []]),
                              _make_worker({}), max_rounds=4)
    assert [d["id"] for d in summary["deadlocks"]] == [fid]
    f = _finding(exdir, fid)
    assert [p.name for p in ex.archived_exchange_dirs(exdir)] \
        == ["exchange-rev-001", "exchange-rev-002"]
    assert [p.name for p in ex.archived_exchange_dirs(exdir, f)] \
        == ["exchange-rev-002"]

    d = ex._deadlock_dossier(exdir, f)
    # Содержание СВОЕЙ истории на месте (положительная половина).
    assert d["remark"]["text"] == _REMARK_DISTORT["text"]
    assert d["rejections"] and all(r == "источник не подтверждает"
                                   for r in d["rejections"])
    # Чужого свода в досье нет ни следа.
    assert d["code_rejections"] == []
    assert "вне перечня миграций" not in json.dumps(d, ensure_ascii=False)
    assert ex.code_locked_dispute(f, d) is False
    seen = {}
    rep = ex.run_arbiter(kit, exdir,
                         _watching_arbiter("аудитор прав", seen=seen),
                         _enforcing_worker)
    block = _block_of(seen["prompt"], fid)
    assert _REMARK_DISTORT["text"] in block
    assert "Причина запрета кодом" not in block
    assert rep["enforced_deadlocks"] == [fid]
    assert _finding(exdir, fid)["state"] == "арбитр: аудитор прав"
    assert gate_arbiter.check(exdir)[0] == []


def test_archive_without_a_registry_is_not_the_history(workspace):
    """Fail-closed: у архива нет `registry.json` — сказать «это та же
    история» нечем, и содержание не берётся. Такой каталог оставляет
    прерванная архивация или чужие руки; панель его не создаёт.

    «Минус»: на коде до правки содержание безреестрового каталога
    доезжает до арбитра и закрывает спор."""
    kit, out, exdir = workspace
    fid, rev = _carried_dispute(kit, out, exdir)
    (rev / "registry.json").unlink()
    f = _finding(exdir, fid)
    assert [p.name for p in ex.archived_exchange_dirs(exdir)] == [rev.name]
    assert ex.archived_exchange_dirs(exdir, f) == []
    d = ex._deadlock_dossier(exdir, f)
    assert d["remark"] is None and d["rejections"] == []

    seen = {}
    rep = ex.run_arbiter(kit, exdir, _watching_arbiter(seen=seen),
                         _enforcing_worker)
    assert "calls" not in seen                   # модель НЕ звалась
    assert [u["deadlock"] for u in rep["unresolved"]] == [fid]
    assert "содержания спора не нашлось" in rep["unresolved"][0]["reason"]
    assert _finding(exdir, fid)["state"] == "не разрешено арбитром"
    assert ex.decide(_CLEAN_HISTORY, 9, registry=_registry_of(exdir)) \
        == ex.VERDICT_NOT_CLOSED
    assert gate_arbiter.check(exdir)[0] == []


def test_archive_with_a_foreign_registry_is_not_the_history(workspace):
    """Реестр в архиве есть, но ЭТОЙ находки не знает. Судится
    ИДЕНТИЧНОСТЬ — идентификатор И ключ, а не один ключ: чужой
    идентификатор при том же ключе (ровно так выглядит
    переизвлечение) и свой идентификатор при чужом ключе — ни то, ни
    другое историей этого спора не является.

    «Минус»: на коде до правки оба архива читаются."""
    kit, out, exdir = workspace
    fid, rev = _carried_dispute(kit, out, exdir)
    f = _finding(exdir, fid)
    reg = json.loads((rev / "registry.json").read_text(encoding="utf-8"))
    for g in reg["findings"]:                    # тот же ключ, чужой id
        g["id"] = "C9-" + g["id"]
    _write_json(rev / "registry.json", reg)
    assert ex.archived_exchange_dirs(exdir, f) == []
    assert ex._deadlock_dossier(exdir, f)["remark"] is None
    for g in reg["findings"]:                    # свой id, чужой ключ
        g["id"] = g["id"][3:]
        g["key"] = json.dumps(["искажение", "R-ЧУЖАЯ-СТРОКА", None, None],
                              ensure_ascii=False)
    _write_json(rev / "registry.json", reg)
    assert ex.archived_exchange_dirs(exdir, f) == []
    assert ex._deadlock_dossier(exdir, f)["remark"] is None
    # Вернули реестр как был — история снова непрерывна.
    for g in reg["findings"]:
        g["key"] = f["key"]
    _write_json(rev / "registry.json", reg)
    assert [p.name for p in ex.archived_exchange_dirs(exdir, f)] \
        == [rev.name]
    assert ex._deadlock_dossier(exdir, f)["remark"]["text"] \
        == _REMARK_DISTORT["text"]


def test_an_old_reextract_archive_without_the_mark_is_cut_by_registry(
        workspace):
    """Архивы, созданные ДО этой правки, метки не несут — и закон
    держит ИМЕННО реестр (метка лишь делает намерение явным и
    читаемым человеком). Тот же сценарий К-1, но файл-метка снят с
    диска — отсечение прежнее.

    «Минус»: на коде до правки чужой свод судит спор."""
    kit, out, exdir = workspace
    fid, arch = _reextract_boundary(kit, out, exdir)
    (arch / ex.HISTORY_BREAK_FILE).unlink()      # «старый» архив
    f = _finding(exdir, fid)
    assert ex.archived_exchange_dirs(exdir, f) == []
    d = ex._deadlock_dossier(exdir, f)
    assert d["code_rejections"] == []
    assert ex.code_locked_dispute(f, d) is False
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         _enforcing_worker)
    assert rep["enforced_deadlocks"] == [fid]
    assert rep["added_questions"] == []
    assert _finding(exdir, fid)["state"] == "арбитр: аудитор прав"
    assert gate_arbiter.check(exdir)[0] == []


def test_chain_megacycle_reextract_megacycle_takes_only_its_own(
        workspace):
    """Цепочка «мегакруг → переизвлечение → мегакруг»: три архива
    подряд, все штатным путём панели. Первые два — история свода A
    (в том числе с его замком кода), третий — история свода B. Спор
    свода B берёт ТОЛЬКО третий.

    «Минус»: на коде до правки в обход входят все три, и слова замка
    свода A судят спор свода B."""
    from cdpm import webui
    kit, out, exdir = workspace
    # Свод A: тупик замка кода, мегакруг, перенос тупика.
    _code_rejected_cycle(kit, out, exdir)
    fid_a = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    assert webui._archive_finished_exchange(exdir.parent) \
        == "exchange-rev-001"
    assert [d["id"] for d in ex.run_exchange(
        kit, out, exdir, _make_auditor([[], [], []]), _make_worker({}),
        max_rounds=4)["deadlocks"]] == [fid_a]
    # ПЕРЕИЗВЛЕЧЕНИЕ: история свода A обрывается.
    assert webui._archive_exchange_for_reextract(exdir.parent) \
        == "exchange-rev-002"
    # Свод B: свой тупик, свой мегакруг, свой перенос.
    _deadlocked_exchange(kit, out, exdir)
    fid_b = _deadlock_ids(exdir)[0]
    assert fid_b != fid_a
    ex.run_arbiter(kit, exdir, _dup_arbiter(), _enforcing_worker)
    assert webui._archive_finished_exchange(exdir.parent) \
        == "exchange-rev-003"
    assert [d["id"] for d in ex.run_exchange(
        kit, out, exdir, _make_auditor([[], [], []]), _make_worker({}),
        max_rounds=4)["deadlocks"]] == [fid_b]

    f = _finding(exdir, fid_b)
    assert [p.name for p in ex.archived_exchange_dirs(exdir)] == [
        "exchange-rev-001", "exchange-rev-002", "exchange-rev-003"]
    assert [p.name for p in ex.archived_exchange_dirs(exdir, f)] \
        == ["exchange-rev-003"]
    d = ex._deadlock_dossier(exdir, f)
    assert d["remark"]["text"] == _REMARK_DISTORT["text"]
    assert d["code_rejections"] == []
    assert "вне перечня миграций" not in json.dumps(d, ensure_ascii=False)
    assert ex.code_locked_dispute(f, d) is False
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         _enforcing_worker)
    assert rep["enforced_deadlocks"] == [fid_b]
    assert _finding(exdir, fid_b)["state"] == "арбитр: аудитор прав"
    assert gate_arbiter.check(exdir)[0] == []


def test_an_interrupted_round_of_the_current_cycle_does_not_trace(
        workspace):
    """Дешёвый остаток пятого ревью: цикл, убитый посреди аудита,
    оставляет каталог `rounds/round-NN` с одним `audit-progress.json`
    — без `remarks.json`. Прежде сборка досье (а с нею и гейт
    арбитража) падала трассой `FileNotFoundError`: человек видел не
    «цикл прерван», а аварию кода. Такой круг и для самого цикла не
    начат — `run_exchange` при возобновлении аудирует его заново.

    «Минус»: на коде до правки оба вызова роняют
    `FileNotFoundError`."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                   _enforcing_worker)
    stub = exdir / "rounds" / "round-09"
    stub.mkdir()
    _write_json(stub / "audit-progress.json", {"done": 0, "total": 2})
    d = ex._deadlock_dossier(exdir, _finding(exdir, fid))
    assert d["remark"]["text"] == _REMARK_DISTORT["text"]
    assert d["rejections"]
    assert gate_arbiter.check(exdir)[0] == []


def test_a_corrupt_remarks_of_the_current_cycle_stays_strict(workspace):
    """Обратная сторона того же закона: мягкость — ровно на «файла
    ещё НЕТ». БИТЫЙ `remarks.json` текущего цикла по-прежнему роняет
    сборку досье и гейт: испорченное содержание — не то же самое, что
    ненаписанное, и молча судить спор по нему нельзя.

    «Минус» обратный: напиши пропуск мягким чтением (`_soft_json`) —
    битый круг станет «пустым», спор будет судиться по неполному
    досье, и этот тест краснеет."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    # Гейту нужен отчёт арбитража — иначе он судит до сборки досье.
    ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                   _enforcing_worker)
    assert gate_arbiter.check(exdir)[0] == []
    (exdir / "rounds" / "round-01" / "remarks.json").write_text(
        "{битый json", encoding="utf-8")
    with pytest.raises(ValueError):
        ex._deadlock_dossier(exdir, _finding(exdir, fid))
    with pytest.raises(ValueError):
        gate_arbiter.check(exdir)


# ======================================================================
# ШЕСТАЯ правка A09 (12.09.2026): три названных остатка состязательной
# проверки.
#
# П1. Пропуск незавершённого круга ТЕКУЩЕГО цикла смотрел только
#     `remarks.json`, а строгая ветвь читает ЕЩЁ и `response.json`
#     (писатель кладёт их по очереди). Круг, убитый на стадии
#     ОБРАБОТЧИКА, по-прежнему ронял досье и гейт трассой
#     `FileNotFoundError`. Завершённость судит НАБОР носителей; битый
#     (существующий, но нечитаемый) носитель остаётся СТРОГИМ — обе
#     стороны закреплены тестом.
# П2. `patch-rejections.json` читался строго в ОБЕИХ ветвях вопреки
#     обещанному мягкому чтению архивов: обрезанный файл в АРХИВЕ ронял
#     досье `JSONDecodeError`. Теперь архив — мягко, текущий цикл —
#     строго; обе стороны закреплены тестом.
# П3. Четыре клаузы закона «своей истории» не держал НИ ОДИН тест:
#     чтение метки обрыва, нормализация ключа, охрана «нет id или
#     ключа», мягкое чтение архивных кругов. Заглавные тесты держали
#     ДИЗЪЮНКЦИЮ (архив отсекался и без спорной клаузы), а не замки.
#     Ниже — по тесту на КАЖДУЮ клаузу: снятие ровно этой клаузы красит
#     ровно этот тест.
# ======================================================================


def test_a_round_killed_at_the_worker_stage_does_not_trace(workspace):
    """П1: цикл, убитый на стадии ОБРАБОТЧИКА, оставляет круг с
    `remarks.json`, но БЕЗ `response.json` (порядок записи: замечания
    — до опроса обработчика, ответ — после). Такой круг незавершён
    ровно так же, как круг, убитый посреди аудита, и пропускается
    целиком: `run_exchange` при возобновлении проходит его заново.

    «Минус»: на коде до правки пропуск смотрел один `remarks.json`,
    круг проходил в строгую ветвь, и `_deadlock_dossier` вместе с
    `gate_arbiter.check` роняли `FileNotFoundError` на
    `response.json` — человек видел не «цикл прерван», а аварию кода.

    Второй «минус» (содержание): недописанный круг не вправе
    ПЕРЕБИТЬ содержание завершённых кругов — его замечание в досье не
    попадает."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                   _enforcing_worker)
    stub = exdir / "rounds" / "round-09"
    stub.mkdir()
    _write_json(stub / "audit-progress.json", {"done": 2, "total": 2})
    _write_json(stub / "remarks.json", {"round": 9, "remarks": [
        dict(_REMARK_DISTORT, id="A9-001",
             text="содержание недописанного круга")]})
    assert not (stub / "response.json").exists()

    d = ex._deadlock_dossier(exdir, _finding(exdir, fid))
    assert d["remark"]["text"] == _REMARK_DISTORT["text"]
    assert d["remark"]["text"] != "содержание недописанного круга"
    assert d["rejections"]
    assert gate_arbiter.check(exdir)[0] == []


def test_a_corrupt_response_of_the_current_cycle_stays_strict(workspace):
    """Обратная сторона П1 на ВТОРОМ носителе: мягкость — ровно на
    «файла ещё НЕТ». БИТЫЙ `response.json` текущего цикла по-прежнему
    останавливает сборку досье и гейт: испорченный ответ обработчика
    — не то же самое, что ненаписанный, и молча судить спор без
    оснований отклонений нельзя.

    «Минус» обратный: почини П1 мягким чтением ответа
    (`resp = _soft_json(...)` в строгой ветви) — битый круг станет
    «кругом без ответа», основания отклонений пропадут, и этот тест
    покраснеет."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                   _enforcing_worker)
    assert gate_arbiter.check(exdir)[0] == []
    (exdir / "rounds" / "round-01" / "response.json").write_text(
        "{битый json", encoding="utf-8")
    with pytest.raises(ValueError):
        ex._deadlock_dossier(exdir, _finding(exdir, fid))
    with pytest.raises(ValueError):
        gate_arbiter.check(exdir)


def test_a_corrupt_patch_rejections_in_the_archive_is_read_softly(
        workspace):
    """П2: файл отбраковок АРХИВНОГО круга читается мягко, как и
    прочие носители архива. Обрезанный `patch-rejections.json`
    прежнего цикла — это «содержания здесь нет», а не исключение:
    остальное содержание спора из того же архива в досье приходит,
    арбитраж идёт своим ходом, гейт зелен.

    «Минус»: на коде до правки обе ветви читали файл строгим
    `json.loads`, и обрезанный файл в архиве ронял `_deadlock_dossier`
    (а с ним `run_arbiter` и гейт) `JSONDecodeError` — вопреки закону,
    обещавшему мягкое чтение архивных кругов."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    f = _finding(exdir, fid)
    _fake_archive(exdir, "exchange-rev-1", "версия архива", f)
    (exdir.parent / "exchange-rev-1" / "rounds" / "round-01"
     / "patch-rejections.json").write_text(
        '[{"answer": "A1-001", "reasons": ["обрез',
        encoding="utf-8")

    d = ex._deadlock_dossier(exdir, f)
    assert "основание из exchange-rev-1" in d["rejections"]
    assert d["code_rejections"] == []             # содержания нет
    assert ex.code_locked_dispute(f, d) is False
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                         _enforcing_worker)
    assert [v["deadlock"] for v in rep["verdicts"]] == [fid]
    assert gate_arbiter.check(exdir)[0] == []


def test_a_corrupt_patch_rejections_of_the_current_cycle_stays_strict(
        workspace):
    """Обратная сторона П2: в ТЕКУЩЕМ цикле файл отбраковок остаётся
    строгим, как и прочие его носители. Битый файл — не «отбраковок
    нет»: по нему судится терминальность замка кода, и молча считать
    её отсутствующей значит открыть запрещённому повтору дорогу.

    «Минус» обратный: напиши мягкое чтение в ОБЕИХ ветвях — этот тест
    покраснеет."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                   _enforcing_worker)
    assert gate_arbiter.check(exdir)[0] == []
    (exdir / "rounds" / "round-01" / "patch-rejections.json").write_text(
        '[{"answer": "A1-001", "reasons": ["обрез', encoding="utf-8")
    with pytest.raises(ValueError):
        ex._deadlock_dossier(exdir, _finding(exdir, fid))
    with pytest.raises(ValueError):
        gate_arbiter.check(exdir)


# --- П3: по тесту на каждую клаузу «своей истории» -------------------

def test_the_history_break_mark_alone_cuts_the_archive(workspace):
    """Клауза 1 закона `_same_dispute_history`: МЕТКА обрыва истории.

    Метка — самостоятельный замок, а не украшение при реестре. Здесь
    реестр архива знает находку по ИДЕНТИЧНОСТИ (идентификатор И
    ключ), то есть по реестру архив прошёл бы как своя история, — и
    отсечь его может только метка. Так и должно быть: метку кладёт
    панель, объявляя историю оборванной, и никакое совпадение
    реестров не вправе её перебить.

    «Минус»: сними чтение метки — архив снова станет историей спора, и
    его содержание (основание отклонения прежнего свода) вернётся в
    досье; тест краснеет на обоих утверждениях."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    f = _finding(exdir, fid)
    _fake_archive(exdir, "exchange-rev-1", "версия архива", f)
    arch = exdir.parent / "exchange-rev-1"
    # До метки: реестр знает находку — это своя история.
    assert ex._same_dispute_history(arch, f) is True
    assert "основание из exchange-rev-1" \
        in ex._deadlock_dossier(exdir, f)["rejections"]

    _write_json(arch / ex.HISTORY_BREAK_FILE,
                {"kind": "переизвлечение", "note": ex.HISTORY_BREAK_NOTE})
    assert ex._same_dispute_history(arch, f) is False
    assert ex.archived_exchange_dirs(exdir, f) == []
    # Каталог по-прежнему виден как архив прогона — но не как история.
    assert [p.name for p in ex.archived_exchange_dirs(exdir)] \
        == ["exchange-rev-1"]
    assert "основание из exchange-rev-1" \
        not in ex._deadlock_dossier(exdir, f)["rejections"]


def test_the_archive_registry_is_matched_by_the_normalized_key(workspace):
    """Клауза 2 закона `_same_dispute_history`: НОРМАЛИЗАЦИЯ ключа с
    обеих сторон сравнения.

    Реестр ЖИВОГО цикла миграция ключей переписывает
    (`normalize_registry_keys`), реестр АРХИВА — никогда: он уехал
    вместе с каталогом. Архив, созданный до АЦ-4/А2, знает ту же
    находку по СТАРОМУ ключу (плоская пара [sid, anchor] вместо списка
    пар), и без нормализации история спора рвалась бы на ровном месте
    — именно там, где закон обещает действовать «и на архивах,
    созданных ДО этой правки».

    «Минус»: сравни ключи как есть — архив перестаёт быть историей,
    содержание спора из него в досье не приходит."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    # Ключ безадресного «пропуска» — тот, чей формат менялся АЦ-4/А2.
    new_key = ex.remark_key(_REMARK_OMIT)
    parts = json.loads(new_key)
    assert isinstance(parts[3], list) and len(parts[3]) == 1
    old_key = json.dumps(parts[:3] + [parts[3][0]], ensure_ascii=False)
    assert old_key != new_key
    assert ex._normalize_key(old_key) == new_key

    gid = "R1-A1-777"
    arch = exdir.parent / "exchange-rev-2"
    rdir = arch / "rounds" / "round-01"
    rdir.mkdir(parents=True)
    _write_json(arch / "registry.json", {"findings": [
        {"id": gid, "key": old_key, "state": "тупик"}]})
    _write_json(rdir / "remarks.json", {"round": 1, "remarks": [
        dict(_REMARK_OMIT, id="A1-009")]})
    _write_json(rdir / "response.json", {"answers": [
        {"remark": "A1-009", "verdict": "ОТКЛОНЕНО",
         "basis": "основание из архива прежней ревизии"}]})

    g = {"id": gid, "key": new_key, "state": "тупик"}
    assert ex._same_dispute_history(arch, g) is True
    assert [p.name for p in ex.archived_exchange_dirs(exdir, g)] \
        == ["exchange-rev-2"]
    d = ex._deadlock_dossier(exdir, g)
    assert d["remark"]["text"] == _REMARK_OMIT["text"]
    assert d["rejections"] == ["основание из архива прежней ревизии"]


def test_a_finding_without_an_id_or_a_key_has_no_history(workspace):
    """Клауза 3 закона `_same_dispute_history`: ОХРАНА «нет
    идентификатора или нет ключа».

    Находка без одного из двух истории не имеет вовсе: сопоставлять
    нечем. Без охраны увечная находка совпадала бы с ОДИНАКОВО
    увечной строкой реестра (отсутствующее равно отсутствующему), и
    спор получал бы чужую историю даром — вместе с её основаниями
    отклонений и словами замка кода.

    «Минус»: сними охрану — `_same_dispute_history` станет истиной на
    обоих увечьях, архив попадёт в обход, и его основание придёт в
    досье находки, у которой нет даже идентификатора."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    f = _finding(exdir, fid)
    _fake_archive(exdir, "exchange-rev-1", "версия архива", f)
    arch = exdir.parent / "exchange-rev-1"
    assert [p.name for p in ex.archived_exchange_dirs(exdir, f)] \
        == ["exchange-rev-1"]
    # Реестр архива повреждён РОВНО ТАК ЖЕ, как находка: строка без
    # идентификатора и строка без ключа.
    _write_json(arch / "registry.json", {"findings": [
        {"key": f["key"], "state": "тупик"},
        {"id": fid, "state": "тупик"}]})

    assert ex._same_dispute_history(arch, {"key": f["key"]}) is False
    assert ex._same_dispute_history(arch, {"id": fid}) is False
    assert ex._same_dispute_history(arch, {}) is False
    assert ex.archived_exchange_dirs(exdir, {"key": f["key"]}) == []
    assert ex.archived_exchange_dirs(exdir, {"id": fid}) == []
    # И содержание архива такой находке не достаётся.
    assert "основание из exchange-rev-1" not in ex._deadlock_dossier(
        exdir, {"key": f["key"]})["rejections"]


def test_a_broken_round_of_the_archive_is_read_softly(workspace):
    """Клауза 4 закона: круги АРХИВА читаются МЯГКО.

    Прежний цикл мог быть убит на любой стадии, а его каталог — уехать
    в архив как есть: круг без носителей вовсе (`round-08`) и круг с
    нечитаемыми носителями (`round-09`). Для суда это «содержания
    здесь нет» — остальные круги того же архива работают, и спор
    судится по тому, что нашлось.

    «Минус»: читай архив строго — `round-08` роняет
    `FileNotFoundError`, `round-09` роняет `JSONDecodeError`, и
    сборка досье вместе с гейтом падает трассой на архиве, который
    никто уже не допишет."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    fid = _deadlock_ids(exdir)[0]
    f = _finding(exdir, fid)
    _fake_archive(exdir, "exchange-rev-1", "версия архива", f)
    ard = exdir.parent / "exchange-rev-1" / "rounds"
    (ard / "round-08").mkdir()
    (ard / "round-09").mkdir()
    (ard / "round-09" / "remarks.json").write_text("{обрез",
                                                   encoding="utf-8")
    (ard / "round-09" / "response.json").write_text("[обрез",
                                                    encoding="utf-8")

    d = ex._deadlock_dossier(exdir, f)
    assert "основание из exchange-rev-1" in d["rejections"]
    assert d["remark"]["text"] == _REMARK_DISTORT["text"]
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                         _enforcing_worker)
    assert [v["deadlock"] for v in rep["verdicts"]] == [fid]
    assert gate_arbiter.check(exdir)[0] == []
