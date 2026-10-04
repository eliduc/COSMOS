# -*- coding: utf-8 -*-
"""Оркестратор аудит-цикла CDPM (АЦ-2) на подменных моделях.

Три сценария протокола: сходимость (замечания → принятие с патчами →
два чистых аудита подряд → успех), тупик со стагнацией (отклонено и
повторено → решение владельца, цикл останавливается сам), патч,
ломающий контракт свода (отбраковывается с причиной, строка остаётся
прежней). Обе модели подменные — сеть и ключи не нужны."""
import json

import pytest

from cdpm import exchange as ex, extract, freeze, model as m
from test_cdpm_extract import _RULES_A, _RULES_B, fake_llm


@pytest.fixture()
def workspace(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES-A.md").write_text(_RULES_A, encoding="utf-8")
    (src / "RULES-B.md").write_text(_RULES_B, encoding="utf-8")
    kit = tmp_path / "kit"
    freeze.freeze([src / "RULES-A.md", src / "RULES-B.md"], kit)
    out = tmp_path / "extract"
    extract.run_extract(kit, out, fake_llm, "Полярис (тест)")
    return kit, out, tmp_path / "exchange"


_REMARK_DISTORT = {
    "severity": "КРИТИЧЕСКОЕ", "defect_class": "искажение",
    "target": {"rule": "R-S1-п-1-9"},
    "source_refs": [{"sid": "S1", "anchor": "п.1.9"}],
    "text": "Потеряно уточнение «на базе»",
    "evidence": "«Отдых на базе не менее 10 часов»",
    "proposal": "вернуть уточнение",
}
_REMARK_OMIT = {
    "severity": "СЕРЬЁЗНОЕ", "defect_class": "пропуск",
    "target": {},
    "source_refs": [{"sid": "S1", "anchor": "п.2.3"}],
    "text": "Не формализована норма о явке",
    "evidence": "«не планировать явку ранее 08:00»",
    "proposal": "добавить строку",
}

_PATCH_FIX = {
    "rule_id": "R-S1-п-1-9",
    "rule": {"id": "R-S1-п-1-9",
             "title": "Минимальный отдых на базе (ОМ-А)",
             "statement": "rest_home ≥ 10 ч (на базе)",
             "zone": "PAIRING", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1.9"}],
             "blockers": ["Q-1-п-1-9-п-2-2"]},
}
_PATCH_NEW = {
    "rule_id": "+",
    "rule": {"title": "Норма о ранней явке (дополнение)",
             "statement": "duty_start ≥ 08:00 после ночного рейса",
             "zone": "ROSTERING", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.2.3"}]},
}


def _make_auditor(rounds):
    """Аудитор по расписанию: список пакетов замечаний по раундам."""
    state = {"i": 0}

    def auditor(prompt):
        # Один раунд аудита = по чанку на источник; замечания выдаём в
        # чанке S1, в остальных — пусто.
        if "Клаузы документа S1" in prompt:
            i = min(state["i"], len(rounds) - 1)
            state["i"] += 1
            return json.dumps({"remarks": rounds[i]},
                              ensure_ascii=False)
        return json.dumps({"remarks": []}, ensure_ascii=False)
    return auditor


def _make_worker(answer_map):
    """Обработчик: вердикты по substring-ключам текста замечаний."""
    def worker(prompt):
        got = json.loads(prompt.split("Замечания аудитора:\n", 1)[1]
                         .split("\n\nЗатронутые строки", 1)[0])
        answers = []
        for rm in got:
            for needle, answer in answer_map.items():
                if needle in rm["text"]:
                    answers.append({"remark": rm["id"], **answer})
                    break
            else:
                answers.append({"remark": rm["id"],
                                "verdict": "ОТКЛОНЕНО",
                                "basis": "источник не подтверждает"})
        return json.dumps({"answers": answers}, ensure_ascii=False)
    return worker


def test_convergence_to_success(workspace):
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_DISTORT),
                              dict(_REMARK_OMIT)], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: «на базе» — принято",
                               "change": "строка уточнена",
                               "patch": _PATCH_FIX},
        "Не формализована": {"verdict": "ПРИНЯТО",
                             "basis": "цитата: «не планировать явку» — "
                                      "принято",
                             "change": "добавлена строка",
                             "patch": _PATCH_NEW},
    })
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5)
    assert summary["verdict"] == "успех"
    assert summary["rounds"] == 3
    assert [h["weighty"] for h in summary["history"]] == \
        [[0, 1, 1], [0, 0, 0], [0, 0, 0]]
    final = m.load_model(exdir / "versions" / "model-v03.json")
    fixed = next(r for r in final["rules"] if r["id"] == "R-S1-п-1-9")
    assert "(на базе)" in fixed["statement"]
    assert any("ранней явке" in r["title"] for r in final["rules"])
    reg = json.loads((exdir / "registry.json")
                     .read_text(encoding="utf-8"))
    states = {f["id"]: f["state"] for f in reg["findings"]}
    assert states["R1-A1-001"] == "закрыто"
    assert states["R1-A1-002"] == "закрыто"
    assert (exdir / "versions" / "svod-v03.md").is_file()
    assert summary["deadlocks"] == []


def test_fit_votes_dispute_and_journal(workspace):
    """П-2 пакета посадки: независимый голос аудитора. Совпадение —
    журнал; спор с цитатой — штатное замечание «посадка оспорена»;
    голос по чужой строке и спор без цитаты — брак голоса со следом,
    не падение."""
    kit, out, exdir = workspace
    doc0 = m.load_model(out / "model.json")
    s1_hard = [r["id"] for r in doc0["rules"]
               if r["kind"] == "HARD" and r["id"].startswith("R-S1")
               and (r.get("template_fit") or {}).get("category")]
    rid1, rid2 = s1_hard[0], s1_hard[1]

    state = {"i": 0}

    def auditor(prompt):
        if "Клаузы документа S1" in prompt:
            state["i"] += 1
            if state["i"] > 1:      # спор одного раунда, не вечный
                return json.dumps({"remarks": []},
                                  ensure_ascii=False)
            return json.dumps({"remarks": [], "fit_votes": [
                {"rule": rid1, "category": "A",
                 "quote": "Отдых на базе не менее 10 часов",
                 "confidence": 90},
                {"rule": rid2, "category": "C",
                 "quote": "цитата", "confidence": 70},
                {"rule": "R-НЕТ-ТАКОЙ", "category": "A",
                 "quote": "х"},
                {"rule": rid1, "category": "B"},
            ]}, ensure_ascii=False)
        return json.dumps({"remarks": []}, ensure_ascii=False)

    worker = _make_worker({
        "посадка оспорена": {"verdict": "ОТКЛОНЕНО",
                             "basis": "цитата: посадка обоснована "
                                      "категорией C — механики нет"},
    })
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5)
    assert summary["verdict"] == "успех"
    pack1 = json.loads(
        (exdir / "rounds" / "round-01" / "remarks.json")
        .read_text(encoding="utf-8"))
    disputes = [rm for rm in pack1["remarks"]
                if "посадка оспорена" in rm.get("text", "")]
    assert len(disputes) == 1
    assert disputes[0]["target"]["rule"] == rid1
    assert disputes[0]["evidence"]
    rows = [json.loads(ln) for ln in
            (exdir / "fit-journal.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    assert all(r["role"] == "auditor" for r in rows)
    by = {}
    for r in rows:
        by.setdefault(r["rule"], []).append(r)
    assert any(r.get("disputed") for r in by[rid1])
    assert any(r.get("invalid") == "спор без цитаты"
               for r in by[rid1])
    assert any("не показанной" in str(r.get("invalid"))
               for r in by["R-НЕТ-ТАКОЙ"])
    clean = [r for r in by[rid2]
             if not r.get("invalid") and not r.get("disputed")]
    assert clean and clean[0]["category"] == "C" \
        and clean[0]["confidence"] == 70


def test_deadlock_and_stall_stop_the_cycle(workspace):
    kit, out, exdir = workspace
    same = [dict(_REMARK_DISTORT)]
    auditor = _make_auditor([same, same, same, same])
    worker = _make_worker({})           # всё отклоняется
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=6)
    assert summary["verdict"] == "стагнация"
    assert summary["deadlocks"], "тупик обязан быть предъявлен владельцу"


def test_contract_breaking_patch_is_rejected(workspace):
    kit, out, exdir = workspace
    bad_patch = {"rule_id": "R-S1-п-1-9",
                 "rule": {"id": "R-S1-п-1-9", "title": "х",
                          "statement": "х", "zone": "PAIRING",
                          "kind": "HARD", "sources": []}}
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: принято",
                               "change": "правка",
                               "patch": bad_patch}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=4)
    rej = json.loads(
        (exdir / "rounds" / "round-01" / "patch-rejections.json")
        .read_text(encoding="utf-8"))
    assert rej and any("трассировки" in reason or "non-empty" in reason
                       for reason in rej[0]["reasons"])
    final = m.load_model(exdir / "versions" /
                         summary["final_version"])
    rule = next(r for r in final["rules"] if r["id"] == "R-S1-п-1-9")
    assert rule["statement"] == "rest_home ≥ 10 ч"   # строка не тронута


def test_gate_green_on_converged_cycle_and_red_on_tamper(workspace,
                                                         capsys):
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: «на базе» — принято",
                               "change": "строка уточнена",
                               "patch": _PATCH_FIX}})
    ex.run_exchange(kit, out, exdir, auditor, worker, max_rounds=5)
    rc = gate_exchange.run(str(exdir))
    outtxt = capsys.readouterr().out
    assert rc == 0, outtxt
    assert "обменов 3" in outtxt
    assert "ВЕРДИКТ: аудит-цикл соответствует контракту" in outtxt

    # Подделка метрики ловится пересчётом.
    mpath = exdir / "rounds" / "round-01" / "metrics.json"
    metrics = json.loads(mpath.read_text(encoding="utf-8"))
    metrics["weighty"] = [0, 0, 0]
    mpath.write_text(json.dumps(metrics, ensure_ascii=False),
                     encoding="utf-8")
    rc = gate_exchange.run(str(exdir))
    outtxt = capsys.readouterr().out
    assert rc == 1
    assert "не сходится с" in outtxt


def test_repatched_rule_retires_its_old_regress_token(workspace):
    """Законная переправка строки новым принятым патчем списывает её
    старый регресс-токен НАВСЕГДА: обмен 3 не должен ловить «откат»
    текста, который обмен 2 сам законно переписал (сбой первого живого
    цикла)."""
    kit, out, exdir = workspace
    remark2 = dict(_REMARK_DISTORT, text="Уточнение всё ещё неполно",
                   evidence="«Отдых на базе не менее 10 часов»")
    patch2 = {"rule_id": "R-S1-п-1-9",
              "rule": {**_PATCH_FIX["rule"],
                       "statement": "rest_home ≥ 10 ч (на базе, "
                                    "вторая редакция)"}}
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [remark2],
                             [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: «на базе» — принято",
                               "change": "строка уточнена",
                               "patch": _PATCH_FIX},
        "всё ещё неполно": {"verdict": "ПРИНЯТО",
                            "basis": "цитата: принято",
                            "change": "вторая редакция",
                            "patch": patch2},
    })
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5)
    assert summary["verdict"] == "успех"       # без ложного «отката»
    final = m.load_model(exdir / "versions" /
                         summary["final_version"])
    rule = next(r for r in final["rules"] if r["id"] == "R-S1-п-1-9")
    assert "вторая редакция" in rule["statement"]


def test_completed_rounds_replay_from_disk_without_llm(workspace,
                                                       tmp_path):
    """Возобновление: завершённые обмены воспроизводятся из записанных
    файлов — модели не вызываются, оплаченная работа не сгорает."""
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: «на базе» — принято",
                               "change": "строка уточнена",
                               "patch": _PATCH_FIX}})
    first = ex.run_exchange(kit, out, exdir, auditor, worker,
                            max_rounds=5)

    def must_not_be_called(prompt):
        raise AssertionError("модель вызвана при полном повторе с диска")
    replay = ex.run_exchange(kit, out, exdir, must_not_be_called,
                             must_not_be_called, max_rounds=5)
    assert replay["verdict"] == first["verdict"]
    assert replay["history"] == first["history"]


def test_audit_and_respond_chunks_cache_to_disk(workspace, tmp_path):
    """Кэш чанков ВНУТРИ раунда: отказ канала на позднем чанке не
    сжигает часы уже оплаченной работы раунда — повторный проход
    аудита и ответа читает готовые чанки с диска без вызовов модели
    (живой урок UniA 2026-08-20: три таймаута уронили раунд 2 после
    ~2.5 часов аудита)."""
    kit, out, exdir = workspace
    from cdpm import extract as extract_mod
    kit_entries = extract_mod._load_kit(kit)
    doc = m.load_model(out / "model.json")
    calls = {"audit": 0, "respond": 0}

    def auditor(prompt):
        calls["audit"] += 1
        if "Клаузы документа S1" in prompt:
            return json.dumps({"remarks": [dict(_REMARK_DISTORT)]},
                              ensure_ascii=False)
        return json.dumps({"remarks": []}, ensure_ascii=False)

    acache = tmp_path / "audit-cache"
    pack1, rej1 = ex._audit_round(auditor, doc, kit_entries, 1,
                                  cache_dir=acache)
    n_audit = calls["audit"]
    assert n_audit > 0 and list(acache.glob("*.json"))
    pack2, rej2 = ex._audit_round(auditor, doc, kit_entries, 1,
                                  cache_dir=acache)
    assert calls["audit"] == n_audit, \
        "повторный аудит раунда обязан читать чанки из кэша"
    assert pack2 == pack1 and rej2 == rej1

    plain_worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: «на базе» — принято",
                               "change": "строка уточнена",
                               "patch": _PATCH_FIX}})

    def worker(prompt):
        calls["respond"] += 1
        return plain_worker(prompt)

    clause_by_key = {(e["sid"], c["anchor"]): c["text"]
                     for e in kit_entries for c in e["clauses"]}
    rcache = tmp_path / "respond-cache"
    resp1 = ex._respond_round(worker, doc, pack1, clause_by_key,
                              cache_dir=rcache)
    n_resp = calls["respond"]
    assert n_resp > 0 and list(rcache.glob("*.json"))
    resp2 = ex._respond_round(worker, doc, pack1, clause_by_key,
                              cache_dir=rcache)
    assert calls["respond"] == n_resp, \
        "повторный ответ раунда обязан читать чанки из кэша"
    assert resp2 == resp1


import re as _re


def _deadlocked_exchange(kit, out, exdir):
    """Цикл с одним тупиком: аудитор настаивает, обработчик отклоняет."""
    same = [dict(_REMARK_DISTORT)]
    auditor = _make_auditor([same, same, same, same])
    worker = _make_worker({})
    return ex.run_exchange(kit, out, exdir, auditor, worker,
                           max_rounds=6)


def _make_arbiter(verdict):
    def arbiter(prompt):
        ids = _re.findall(r"Тупик \[([^\]]+)\]", prompt)
        return json.dumps({"verdicts": [
            {"deadlock": i, "verdict": verdict,
             "basis": "по клаузе источника"} for i in ids]},
            ensure_ascii=False)
    return arbiter


def _enforcing_worker(prompt):
    got = json.loads(prompt.split("Замечания к исполнению:\n", 1)[1]
                     .split("\n\nЗатронутые строки", 1)[0])
    return json.dumps({"answers": [
        {"remark": rm["id"], "verdict": "ПРИНЯТО",
         "basis": "арбитраж: цитата источника",
         "change": "строка исправлена по решению арбитра",
         "patch": _PATCH_FIX} for rm in got]}, ensure_ascii=False)


def test_arbiter_worker_right_closes_deadlock(workspace):
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                         _enforcing_worker)
    assert rep["by_verdict"]["обработчик прав"] == rep["deadlocks"] > 0
    reg = json.loads((exdir / "registry.json")
                     .read_text(encoding="utf-8"))
    assert all(f["state"] != "тупик" for f in reg["findings"])
    assert any(f["state"] == "арбитр: обработчик прав"
               for f in reg["findings"])
    assert rep["enforced"] == 0 and rep["added_questions"] == []
    from cdpm.gates import gate_arbiter
    problems, counters = gate_arbiter.check(exdir)
    assert problems == []
    assert counters["deadlocks"] == rep["deadlocks"]


def test_arbiter_auditor_right_forces_the_fix(workspace):
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         _enforcing_worker)
    assert rep["by_verdict"]["аудитор прав"] > 0
    assert rep["enforced"] > 0
    final = m.load_model(exdir / "versions" / rep["final_version"])
    rule = next(r for r in final["rules"] if r["id"] == "R-S1-п-1-9")
    assert "(на базе)" in rule["statement"]     # патч исполнен
    reg = json.loads((exdir / "registry.json")
                     .read_text(encoding="utf-8"))
    assert any(f["state"] == "арбитр: аудитор прав"
               for f in reg["findings"])
    from cdpm.gates import gate_arbiter
    problems, _ = gate_arbiter.check(exdir)
    assert problems == []


def test_arbiter_to_questionnaire_adds_fork_and_blocks_rule(workspace):
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("в анкету"),
                         _enforcing_worker)
    assert rep["by_verdict"]["в анкету"] > 0
    assert rep["added_questions"]
    final = m.load_model(exdir / "versions" / rep["final_version"])
    qid = rep["added_questions"][0]
    q = next(x for x in final["questions"] if x["id"] == qid)
    assert q["part"] == "2"
    keys = [o["key"] for o in q["answer_format"]["options"]]
    assert keys == ["а", "б"]        # два чтения: аудитора и обработчика
    rule = next(r for r in final["rules"]
                if r["id"] in q["rule_refs"])
    assert qid in rule["blockers"]   # строка заблокирована до ответа
    from cdpm.gates import gate_arbiter
    problems, _ = gate_arbiter.check(exdir)
    assert problems == []


def test_arbiter_worker_right_suppresses_future_remarks(workspace):
    """Требование арбитра: после вердикта «обработчик прав» аудитор
    ту же находку больше не поднимает — замечание отбраковывается
    машинно с названной причиной, цикл остаётся чистым."""
    import shutil
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                   _enforcing_worker)

    exdir2 = exdir.parent / "exchange-2"
    exdir2.mkdir()
    shutil.copy2(exdir / "registry.json", exdir2 / "registry.json")
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], []])
    rep = ex.run_exchange(kit, out, exdir2, auditor, _make_worker({}),
                          max_rounds=5)
    assert rep["verdict"] == "успех"
    rejected = json.loads(
        (exdir2 / "rounds" / "round-01" / "audit-rejections.json")
        .read_text(encoding="utf-8"))
    assert any("требование арбитра" in r.get("reason", "")
               for r in rejected)


def test_arbiter_partial_rerun_merges_report(workspace):
    """Возврат ОДНОГО тупика на переарбитрацию сливается с прежним
    отчётом: вердикт на каждый тупик цикла всегда один и последний;
    гейт остаётся зелёным (живой случай 2026-08-18)."""
    kit, out, exdir = workspace
    r1, r2 = dict(_REMARK_DISTORT), dict(_REMARK_OMIT)
    auditor = _make_auditor([[r1, r2]] * 4)
    ex.run_exchange(kit, out, exdir, auditor, _make_worker({}),
                    max_rounds=6)
    rep1 = ex.run_arbiter(kit, exdir, _make_arbiter("обработчик прав"),
                          _enforcing_worker)
    assert rep1["deadlocks"] == 2

    target = rep1["verdicts"][0]["deadlock"]
    reg = json.loads((exdir / "registry.json")
                     .read_text(encoding="utf-8"))
    for f in reg["findings"]:
        if f["id"] == target:
            f["state"] = "тупик"
    (exdir / "registry.json").write_text(
        json.dumps(reg, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")

    rep2 = ex.run_arbiter(kit, exdir, _make_arbiter("в анкету"),
                          _enforcing_worker)
    assert rep2["deadlocks"] == 2            # отчёт слился, не усох
    by = {v["deadlock"]: v["verdict"] for v in rep2["verdicts"]}
    assert by[target] == "в анкету"
    assert list(by.values()).count("обработчик прав") == 1
    from cdpm.gates import gate_arbiter
    problems, _ = gate_arbiter.check(exdir)
    assert problems == []


def test_arbiter_prompt_carries_clause_for_bracketed_anchor(workspace):
    """Якорь замечания в квадратных скобках («[п.X]») не теряет клаузу
    в досье арбитра — живой арбитраж 2026-08-18 судил два тупика по
    «<нет>» именно из-за этого."""
    kit, out, exdir = workspace
    rm = dict(_REMARK_DISTORT)
    ref = dict(rm["source_refs"][0])
    ref["anchor"] = f"[{ref['anchor']}]"
    rm["source_refs"] = [ref]
    auditor = _make_auditor([[rm], [rm], [rm], [rm]])
    worker = _make_worker({})
    ex.run_exchange(kit, out, exdir, auditor, worker, max_rounds=6)

    seen = {}
    base = _make_arbiter("обработчик прав")

    def arbiter(prompt):
        seen["prompt"] = prompt
        return base(prompt)
    ex.run_arbiter(kit, exdir, arbiter, _enforcing_worker)
    assert "Клауза источника" in seen["prompt"]
    assert "<нет>" not in seen["prompt"]


def test_two_different_omissions_are_not_one_deadlock():
    reg = {}
    om1 = dict(_REMARK_OMIT, id="A-1")
    om2 = dict(_REMARK_OMIT, id="A-2",
               source_refs=[{"sid": "S2", "anchor": "п.7.1"}])
    pack1 = {"round": 1, "remarks": [om1]}
    resp1 = {"round": 1, "answers": [
        {"remark": "A-1", "verdict": "ОТКЛОНЕНО", "basis": "б"}]}
    ex.registry_update(reg, 1, pack1, resp1)
    pack2 = {"round": 2, "remarks": [om2]}
    resp2 = {"round": 2, "answers": [
        {"remark": "A-2", "verdict": "ОТКЛОНЕНО", "basis": "б"}]}
    ex.registry_update(reg, 2, pack2, resp2)
    states = [f["state"] for f in reg["findings"]]
    assert "тупик" not in states       # разные места — разные находки


def test_auditor_mandate_pair_svod_plus_anketa(workspace):
    """Мандат аудитора (решение владельца 2026-08-21): аудируется пара
    «свод + анкета». Промпт несёт открытые вопросы; замечание «выбор
    чтения» по строке, заблокированной открытым вопросом, машинно
    отбраковывается с причиной; замечание к вопросу анкеты проходит."""
    kit, out, exdir = workspace
    from cdpm import extract as extract_mod
    kit_entries = extract_mod._load_kit(kit)
    doc = m.load_model(out / "model.json")
    open_q = next(q["id"] for q in doc["questions"]
                  if ex is not None and q.get("answer") is None)
    blocked = next(r for r in doc["rules"]
                   if open_q in (r.get("blockers") or []))

    prompts: list[str] = []

    def auditor(prompt):
        prompts.append(prompt)
        if "Клаузы документа S1" in prompt:
            deferred = dict(_REMARK_DISTORT)
            deferred["defect_class"] = "выбор чтения"
            deferred["target"] = {"rule": blocked["id"]}
            to_question = dict(_REMARK_DISTORT)
            to_question["defect_class"] = "выбор чтения"
            to_question["target"] = {"question": open_q}
            to_question["text"] = "Развилка вопроса неполна"
            return json.dumps({"remarks": [deferred, to_question]},
                              ensure_ascii=False)
        return json.dumps({"remarks": []}, ensure_ascii=False)

    pack, rejected = ex._audit_round(auditor, doc, kit_entries, 1)
    assert "ОТКРЫТЫЕ ВОПРОСЫ АНКЕТЫ" in prompts[0]
    assert open_q in prompts[0]
    # Отложенное на авиакомпанию — отбраковано машинно с причиной.
    assert any("решение за авиакомпанией" in r2
               for r in rejected for r2 in r["reasons"])
    # Замечание к КОРРЕКТНОСТИ вопроса анкеты — живёт.
    kept = [rm for rm in pack["remarks"]
            if (rm.get("target") or {}).get("question") == open_q]
    assert kept, pack["remarks"]


def test_deferral_matched_by_source_anchors(workspace):
    """Решение владельца 2026-08-21: привязки вопроса к источникам —
    ключ сопоставления. Замечание «выбор чтения» БЕЗ target.rule, но с
    якорями, совпадающими с якорями открытого вопроса, — машинно
    отбраковывается с указанием вопроса; листинг вопросов для аудитора
    несёт и строки свода, и якоря источников."""
    kit, out, exdir = workspace
    doc = m.load_model(out / "model.json")
    open_q = next(q for q in doc["questions"]
                  if q.get("answer") is None)
    ref = dict(open_q["source_refs"][0])

    rm = dict(_REMARK_OMIT)
    rm["defect_class"] = "выбор чтения"
    rm["target"] = {}
    rm["source_refs"] = [ref]
    reason = ex.awaiting_decision_reason(doc, rm)
    assert reason and open_q["id"] in reason \
        and "совпадение якорей" in reason

    listing = ex._open_questions_listing(doc)
    assert f"{ref['sid']}:{ref['anchor']}" in listing
    assert "строки свода" in listing


def test_ac4_oscillation_becomes_deadlock(workspace):
    """АЦ-4/В1: строка, правленная три раунда подряд, нестабильна —
    её живая находка дозревает до тупика с поводом «осцилляция»."""
    kit, out, exdir = workspace
    same = [dict(_REMARK_DISTORT)]
    auditor = _make_auditor([same, same, same, same, same])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: «на базе» — принято",
                               "change": "строка уточнена",
                               "patch": _PATCH_FIX}})
    ex.run_exchange(kit, out, exdir, auditor, worker, max_rounds=6)
    reg = json.loads((exdir / "registry.json")
                     .read_text(encoding="utf-8"))
    kinds = {f.get("deadlock_kind") for f in reg["findings"]
             if f["state"] == "тупик"}
    assert "осцилляция" in kinds, reg["findings"]


def test_ac4_severity_cap_applies(workspace):
    """АЦ-4/Г1: серьёзность выше потолка класса понижается кодом с
    отметкой; «выбор чтения» не бывает блокирующим."""
    kit, out, exdir = workspace
    from cdpm import extract as extract_mod
    kit_entries = extract_mod._load_kit(kit)
    doc = m.load_model(out / "model.json")
    rm = dict(_REMARK_DISTORT)
    rm["defect_class"] = "потерянная трассировка"
    rm["severity"] = "БЛОКИРУЮЩЕЕ"

    def auditor(prompt):
        if "Клаузы документа S1" in prompt:
            return json.dumps({"remarks": [rm]}, ensure_ascii=False)
        return json.dumps({"remarks": []}, ensure_ascii=False)

    pack, _rej = ex._audit_round(auditor, doc, kit_entries, 1)
    got = pack["remarks"][0]
    assert got["severity"] == "КРИТИЧЕСКОЕ"
    assert got["severity_capped_from"] == "БЛОКИРУЮЩЕЕ"


def test_ac4_incremental_skips_untouched_chunks(workspace):
    """АЦ-4/Е1: инкрементальный аудит зовёт модель только по чанкам с
    затронутыми якорями."""
    kit, out, exdir = workspace
    from cdpm import extract as extract_mod
    kit_entries = extract_mod._load_kit(kit)
    doc = m.load_model(out / "model.json")
    calls = []

    def auditor(prompt):
        calls.append(prompt[:40])
        return json.dumps({"remarks": []}, ensure_ascii=False)

    full, _ = ex._audit_round(auditor, doc, kit_entries, 1)
    n_full = len(calls)
    calls.clear()
    only = {("S1", kit_entries[0]["clauses"][0]["anchor"])}
    inc, _ = ex._audit_round(auditor, doc, kit_entries, 2,
                             only_anchors=only)
    assert len(calls) < n_full and len(calls) >= 1


def test_ac4_orphan_finding_remaps_to_successor():
    """АЦ-4/А3: находка строки, заменённой новой с теми же
    источниками, переезжает на преемника."""
    old_doc = {"rules": [{"id": "R-A", "sources": [
        {"sid": "S1", "anchor": "п.1"}]}]}
    new_doc = {"rules": [{"id": "R-B", "sources": [
        {"sid": "S1", "anchor": "п.1"}]}]}
    reg = {"findings": [{
        "key": json.dumps(["искажение", "R-A", None, None],
                          ensure_ascii=False),
        "state": "отклонено"}]}
    moved = ex.remap_orphan_findings(reg, old_doc, new_doc)
    assert moved == 1
    assert json.loads(reg["findings"][0]["key"])[1] == "R-B"
    assert reg["findings"][0]["remapped"] is True


def test_ac4_tokens_survive_prior_file(workspace, tmp_path):
    """АЦ-4/Ж2: цикл читает tokens.json прежней ревизии — регресс
    исправления прошлого круга ловится в новом."""
    kit, out, exdir = workspace
    exdir.mkdir(parents=True, exist_ok=True)
    # Токен охраняет текст, которого в модели НЕТ, — цикл обязан
    # упасть «откатом», доказывая, что прежние токены действуют.
    (exdir / "tokens.json").write_text(json.dumps([{
        "rule": "R-S1-п-1-9", "must_contain": "(на базе)",
        "round": 1}], ensure_ascii=False), encoding="utf-8")
    auditor = _make_auditor([[], []])
    with pytest.raises(ex.ExchangeError):
        ex.run_exchange(kit, out, exdir, auditor, _make_worker({}),
                        max_rounds=2)


def test_all_arbiter_verdicts_suppress_repeats(workspace):
    """Решение владельца 2026-08-21: спор, решённый ЛЮБЫМ вердиктом
    арбитра, исчезает — повтор ключа отбраковывается машинно с
    причиной по вердикту (не только «обработчик прав»)."""
    kit, out, exdir = workspace
    key = ex.remark_key(_REMARK_DISTORT)
    exdir.mkdir(parents=True, exist_ok=True)
    # A07-R2 (12.09.2026): запись находки несёт серьёзность из перечня —
    # без неё реестр не читается ни одним потребителем (неизвестная
    # серьёзность неблокирующей не считается), и цикл её не написал бы.
    (exdir / "registry.json").write_text(json.dumps({"findings": [
        {"id": "X-1", "key": key, "state": "арбитр: аудитор прав",
         "severity": "КРИТИЧЕСКОЕ"}]},
        ensure_ascii=False), encoding="utf-8")
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], []])
    ex.run_exchange(kit, out, exdir, auditor, _make_worker({}),
                    max_rounds=3)
    rej = json.loads(
        (exdir / "rounds" / "round-01" / "audit-rejections.json")
        .read_text(encoding="utf-8"))
    assert any("аудитор прав» исполнен" in r.get("reason", "")
               for r in rej), rej


def test_zh3_registry_continues_across_revisions(workspace):
    """АЦ-4/Ж3 (живой Лайт): реестр продолжает перенесённый файл —
    «отклонено» прошлого круга, повторённое в новом, зреет в тупик,
    а не открывается заново с чистого листа."""
    kit, out, exdir = workspace
    exdir.mkdir(parents=True, exist_ok=True)
    key = ex.remark_key(_REMARK_DISTORT)
    # A07-R2: перенесённая запись несёт серьёзность из перечня (её
    # пишет проводка круга) — иначе реестр негоден на чтении.
    (exdir / "registry.json").write_text(json.dumps({"findings": [
        {"id": "R0-X", "key": key, "state": "отклонено",
         "severity": "КРИТИЧЕСКОЕ",
         "text": "прежний спор", "basis": "прежнее основание"}]},
        ensure_ascii=False), encoding="utf-8")
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], []])
    ex.run_exchange(kit, out, exdir, auditor, _make_worker({}),
                    max_rounds=3)
    reg = json.loads((exdir / "registry.json")
                     .read_text(encoding="utf-8"))
    states = {f["id"]: f["state"] for f in reg["findings"]}
    assert states.get("R0-X") == "тупик", states


# ---------------------------------------------------------------------------
# С-2 (анализ Лайта-v3): классы-синонимы не разрывают ключ замечания
# ---------------------------------------------------------------------------

def test_c2_class_synonyms_share_key_and_mature_deadlock():
    """Живой случай круга 3 Лайта: одна претензия к вопросу анкеты
    пришла в р1 как «пропуск», в р5 как «потерянная трассировка» —
    ключи расходились и спор не дозревал. Теперь ключ един и повтор
    отклонённого зреет в тупик."""
    rm1 = {"id": "A1-001", "severity": "СЕРЬЁЗНОЕ",
           "defect_class": "пропуск",
           "target": {"question": "Q-1"},
           "text": "вопрос не перечисляет третью строку свода"}
    rm2 = dict(rm1, id="A2-001",
               defect_class="потерянная трассировка")
    assert ex.remark_key(rm1) == ex.remark_key(rm2)

    reg = {}
    ex.registry_update(reg, 1, {"remarks": [rm1]},
                       {"answers": [{"remark": "A1-001",
                                     "verdict": "ОТКЛОНЕНО",
                                     "basis": "строка не относится"}]})
    ex.registry_update(reg, 2, {"remarks": [rm2]}, {"answers": []})
    states = [f["state"] for f in reg["findings"]]
    assert "тупик" in states, states


def test_c2_prior_registry_keys_migrate():
    """Реестр прежней ревизии со старым ключом («потерянная
    трассировка») мигрирует к текущей канонизации — непрерывность Ж3
    не рвётся на классах-синонимах; мусорный ключ не роняет миграцию."""
    old_key = json.dumps(["потерянная трассировка", None, "Q-1", None],
                         ensure_ascii=False)
    reg = {"findings": [
        {"key": old_key, "state": "отклонено"},
        {"key": "не json", "state": "открыто"},
    ]}
    assert ex.normalize_registry_keys(reg) == 1
    assert json.loads(reg["findings"][0]["key"])[0] == "пропуск"
    assert reg["findings"][1]["key"] == "не json"
    # повторная миграция — идемпотентна
    assert ex.normalize_registry_keys(reg) == 0


# ---------------------------------------------------------------------------
# В2 (состязательная проверка 2026-08-21): доработки исправлений С-1/С-2
# ---------------------------------------------------------------------------

def test_b2_resume_does_not_double_registry(workspace):
    """КРИТИЧЕСКОЕ В2-1: возобновление цикла проводит раунды по СНИМКУ
    старта, а не по рабочему реестру — иначе каждая находка проводилась
    дважды (дубли, ложные тупики «повтор»). Живая репродукция
    интеграционного рецензента."""
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ОТКЛОНЕНО",
                               "basis": "формулировка совпадает с "
                                        "источником"}})
    ex.run_exchange(kit, out, exdir, auditor, worker, max_rounds=3)
    reg1 = json.loads((exdir / "registry.json")
                      .read_text(encoding="utf-8"))
    states1 = sorted(f["state"] for f in reg1["findings"])

    def silent(prompt):
        raise AssertionError("модель вызвана при повторе с диска")
    ex.run_exchange(kit, out, exdir, silent, silent, max_rounds=3)
    reg2 = json.loads((exdir / "registry.json")
                      .read_text(encoding="utf-8"))
    states2 = sorted(f["state"] for f in reg2["findings"])
    assert len(reg2["findings"]) == len(reg1["findings"]), \
        "возобновление задвоило находки реестра"
    assert states2 == states1, \
        "возобновление исказило состояния находок (ложные тупики)"


def test_b2_normalize_key_migrates_flat_anchor_and_survives_junk():
    """В2-2: ключ прежнего кода с плоским якорем [sid, anchor]
    мигрирует к формату списка пар; нестроковый мусор не роняет
    миграцию."""
    old = json.dumps(["пропуск", None, None, ["S1", "п.2.3"]],
                     ensure_ascii=False)
    new = ex._normalize_key(old)
    assert json.loads(new)[3] == [["S1", "п.2.3"]]
    # Идемпотентность: повторная нормализация ничего не меняет.
    assert ex._normalize_key(new) == new
    # Совпадение с живым ключом того же одноякорного замечания.
    rm = {"defect_class": "пропуск", "target": {},
          "source_refs": [{"sid": "S1", "anchor": "п.2.3"}]}
    assert new == ex.remark_key(rm)
    # Мусор: нестроковый ключ и не-JSON не роняют.
    reg = {"findings": [{"key": 42, "state": "открыто"},
                        {"key": old, "state": "отклонено"}]}
    assert ex.normalize_registry_keys(reg) == 1


def test_b2_rule_addressed_classes_do_not_merge():
    """В2 (сужение С-2): для СТРОКИ свода «пропуск» и «потерянная
    трассировка» — разные дефекты; слияние рождало бы ложные тупики и
    ложное подавление вердиктов арбитра."""
    rm1 = {"defect_class": "пропуск", "target": {"rule": "R-X"},
           "source_refs": []}
    rm2 = {"defect_class": "потерянная трассировка",
           "target": {"rule": "R-X"}, "source_refs": []}
    assert ex.remark_key(rm1) != ex.remark_key(rm2)


def test_au1_audit_chunk_sees_only_its_rules(workspace, monkeypatch):
    """АУ-1 (живой run-0015): аудитор чанка получает только строки,
    трассированные в клаузы ЕГО чанка, — строки другого чанка не
    объявляются «изобретением» по неполному окну; строки с якорями
    вне комплекта дозируются в последний чанк документа листингом
    проверки трассировки."""
    kit, out, exdir = workspace
    monkeypatch.setattr(ex, "AUDIT_CHUNK", 2)   # дробим S1 на чанки
    from cdpm import model as m
    doc = m.load_model(out / "model.json")
    # Строка с якорем, которого нет в комплекте.
    doc["rules"].append({
        "id": "R-S1-призрак", "title": "Призрак",
        "statement": "правило с трассировкой в никуда",
        "zone": "PAIRING", "kind": "HARD",
        "sources": [{"sid": "S1", "anchor": "п.99.99"}]})
    prompts: list[str] = []

    def auditor(prompt):
        prompts.append(prompt)
        return json.dumps({"remarks": []})

    ex._audit_round(auditor, doc, extract._load_kit(kit), 1)
    s1_prompts = [p for p in prompts if "Клаузы документа S1" in p]
    assert len(s1_prompts) >= 2, "S1 не раздробился на чанки"
    for p in s1_prompts:
        # Каждая показанная строка трассирована в якорь ЭТОГО чанка.
        import re as _re
        for rid in _re.findall(r"^(R-S1-[^\s|]+) \|", p, flags=_re.M):
            if rid == "R-S1-призрак":
                continue
            anchor = rid.removeprefix("R-S1-").replace("-", ".")
            assert f"[{anchor}]" in p, \
                f"{rid} показан чанку без его клаузы"
    # Призрак дозирован ровно в один (последний) чанк S1 с пометкой.
    ghost = [p for p in s1_prompts if "R-S1-призрак" in p]
    assert len(ghost) == 1
    assert "ВНЕ ПРЕДОСТАВЛЕННЫХ КЛАУЗ" in ghost[0]
    # Мандат окна обзора — в каждом промпте аудита.
    assert all("ОКНО ОБЗОРА" in p for p in prompts)


def test_ost1_manual_cycle_stops_on_bk_clean_and_resumes(workspace):
    """ОСТ-1: ручной цикл останавливается на первом обмене с Б=0,
    К=0, С>0 (вердикт «остановлен по Б/К», решение за человеком);
    возобновление с bk_stop_after продолжает дальше; полностью чистый
    обмен (0,0,0) остановку не вызывает — работает штатный критерий."""
    kit, out, exdir = workspace
    serious = dict(_REMARK_OMIT)      # СЕРЬЁЗНОЕ, безадресный пропуск
    auditor = _make_auditor([[serious], [serious], [], []])
    worker = _make_worker({
        "Не формализована норма о явке": {
            "verdict": "ОТКЛОНЕНО",
            "basis": "норма отражена строкой о ранней явке"}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5, bk_stop=True)
    assert summary["verdict"] == "остановлен по Б/К"
    assert summary["rounds"] == 1
    assert summary["history"][0]["weighty"] == [0, 0, 1]
    from cdpm.gates import gate_exchange
    problems, _c = gate_exchange.check(exdir)
    assert problems == [], problems

    # Продолжение: остановка не действует на уже сыгранные обмены.
    summary2 = ex.run_exchange(kit, out, exdir, auditor, worker,
                               max_rounds=5, bk_stop=True,
                               bk_stop_after=1)
    assert summary2["rounds"] >= 2
    assert summary2["verdict"] in ("остановлен по Б/К", "успех",
                                   "лимит", "стагнация")


def test_ost1_clean_round_does_not_trigger_bk_stop(workspace):
    """Полностью чистые обмены не считаются «остановлен по Б/К» —
    решает штатный критерий успеха."""
    kit, out, exdir = workspace
    auditor = _make_auditor([[], [], []])
    summary = ex.run_exchange(kit, out, exdir, auditor,
                              _make_worker({}), max_rounds=5,
                              bk_stop=True)
    assert summary["verdict"] == "успех"


def test_pair_svod_and_anketa_written_per_round(workspace):
    """Выход каждого обмена — пара «свод + анкета» одной версии
    (замечание владельца 2026-08-21)."""
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: «на базе» — принято",
                               "change": "строка уточнена",
                               "patch": _PATCH_FIX}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5)
    for n in range(1, summary["rounds"] + 1):
        assert (exdir / "versions" / f"svod-v{n:02d}.md").is_file()
        assert (exdir / "versions" / f"anketa-v{n:02d}.md").is_file()


def test_usk1_interround_audit_cache_reuses_unchanged_input(workspace):
    """УСК-1: подтверждающий раунд с полностью неизменным входом
    (свод, анкета, реестр) берёт ответы аудитора из общего кэша цикла
    — повторных вызовов модели нет."""
    kit, out, exdir = workspace
    calls = {"n": 0}

    def auditor(prompt):
        calls["n"] += 1
        return json.dumps({"remarks": []})
    summary = ex.run_exchange(kit, out, exdir, auditor,
                              _make_worker({}), max_rounds=5)
    assert summary["verdict"] == "успех"
    assert summary["rounds"] == 2       # чистый + подтверждающий
    # Все вызовы — в первом раунде; второй пришёл из кэша целиком.
    n_chunks_round1 = calls["n"]
    assert n_chunks_round1 > 0
    per_round = n_chunks_round1 / summary["rounds"]
    assert calls["n"] == n_chunks_round1, \
        "подтверждающий раунд повторно звал модель"
    assert (exdir / "audit-cache").is_dir()
    assert per_round <= n_chunks_round1


def test_carried_registry_id_collision_does_not_confuse_gate(
        workspace):
    """Живой Лайт run-0039 (2026-08-23): перенесённый сквозь
    мегакруги реестр несёт находки ПРЕЖНИХ циклов, нумерация
    R{круг}-… начинается заново — одноимённая чужая находка со
    state «исправлено» затеняла тупик в словаре по id, и гейт судил
    вердикт по чужому состоянию. Сопоставление — по паре (id, key)."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         _enforcing_worker)
    reg = json.loads((exdir / "registry.json")
                     .read_text(encoding="utf-8"))
    deadlock_ids = [f["id"] for f in reg["findings"]
                    if f["state"].startswith("арбитр:")]
    assert deadlock_ids
    # Чужая находка прежнего цикла: тот же id, другой ключ.
    reg["findings"].append({
        "id": deadlock_ids[0], "opened_round": 1,
        "severity": "СЕРЬЁЗНОЕ", "key": "чужой-ключ-прежнего-цикла",
        "state": "исправлено"})
    (exdir / "registry.json").write_text(
        json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    from cdpm.gates import gate_arbiter
    problems, _ = gate_arbiter.check(exdir)
    assert problems == [], problems


def test_registry_ids_namespaced_by_cycle():
    """Id находок мегакруга n>1 получают префикс C{n}- — id разных
    циклов не совпадают в перенесённом реестре."""
    reg = {"findings": []}
    pack = {"remarks": [{"id": "A1-001", "severity": "СЕРЬЁЗНОЕ",
                         "text": "т", "quote": "ц",
                         "source_refs": [{"sid": "S1",
                                          "anchor": "п.1"}]}]}
    resp = {"answers": [{"remark": "A1-001", "verdict": "ПРИНЯТО",
                         "basis": "б"}]}
    ex.registry_update(reg, 1, pack, resp, cycle=2)
    assert reg["findings"][0]["id"] == "C2-R1-A1-001"
    reg2 = {"findings": []}
    ex.registry_update(reg2, 1, pack, resp)
    assert reg2["findings"][0]["id"] == "R1-A1-001"


def test_enforcement_patch_outside_target_is_rejected(workspace):
    """Живой Лайт run-0040: обработчик, исполняя «аудитор прав»,
    патчил заодно ЧУЖУЮ строку — дифф-гейт краснел после применения.
    Патч вне цели своего тупика отбраковывается ДО применения с
    причиной; целевой патч применяется."""
    kit, out, exdir = workspace
    _deadlocked_exchange(kit, out, exdir)

    def overreaching_worker(prompt):
        got = json.loads(
            prompt.split("Замечания к исполнению:\n", 1)[1]
            .split("\n\nЗатронутые строки", 1)[0])
        answers = []
        for i, rm in enumerate(got):
            patch = dict(_PATCH_FIX)
            if i == 0:
                # Перелёт: исполнение «съехало» в чужую строку.
                patch["rule_id"] = "R-S1-п-1-1"
            answers.append({"remark": rm["id"], "verdict": "ПРИНЯТО",
                            "basis": "цитата", "change": "правка",
                            "patch": patch})
        return json.dumps({"answers": answers}, ensure_ascii=False)

    rep = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"),
                         overreaching_worker)
    assert any("вне цели арбитража" in r
               for pr in rep["patch_rejections"]
               for r in pr["reasons"]), rep["patch_rejections"]
    # Аудит A3.5 (2026-09-04): отбракованное исполнение НЕ закрывает
    # находку — тупик остаётся открытым, гейт называет его красным.
    reg = json.loads((exdir / "registry.json")
                     .read_text(encoding="utf-8"))
    rejected = {pr["answer"] for pr in rep["patch_rejections"]}
    assert rejected
    assert all(f["state"] == "тупик" for f in reg["findings"]
               if f["id"] in rejected)
    assert rep["enforced"] == len(rep["enforced_deadlocks"]) \
        == rep["deadlocks"] - len(rejected)
    from cdpm.gates import gate_arbiter
    problems, _ = gate_arbiter.check(exdir)
    assert any("не исполнен" in p for p in problems), problems


def test_arbiter_dedupes_identical_fork_questions(workspace):
    """Живой урок UniA run-0051: смежные тупики с одинаковой
    формулировкой рождали вопросы-дубли — инвариант дублей ронял
    арбитраж. Вопрос один, строки-носители подцепляются к нему."""
    kit, out, exdir = workspace
    twin = dict(_REMARK_DISTORT,
                target={"rule": "R-S1-п-2-3"},
                source_refs=[{"sid": "S1", "anchor": "п.2.3"}])
    same = [dict(_REMARK_DISTORT), twin]
    auditor = _make_auditor([same, same, same, same])
    ex.run_exchange(kit, out, exdir, auditor, _make_worker({}),
                    max_rounds=6)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("в анкету"),
                         _enforcing_worker)
    assert rep["by_verdict"]["в анкету"] == 2
    assert len(rep["added_questions"]) == 1, rep["added_questions"]
    final = m.load_model(exdir / "versions" / rep["final_version"])
    qid = rep["added_questions"][0]
    forks = [q for q in final["questions"]
             if q["id"].startswith("Q-2-арб-")]
    assert len(forks) == 1
    q = forks[0]
    assert set(q["rule_refs"]) == {"R-S1-п-1-9", "R-S1-п-2-3"}
    for rid in q["rule_refs"]:
        rule = next(r for r in final["rules"] if r["id"] == rid)
        assert qid in rule["blockers"]
    from cdpm.gates import gate_arbiter
    problems, _ = gate_arbiter.check(exdir)
    assert problems == []


def test_arbiter_fork_uses_rule_anchor(workspace):
    """Якорь вопроса арбитража — от строки-носителя, не из текста
    аудитора (живой урок UniA run-0051: вымышленные якоря в скобках
    краснили гейт извлечения трассировкой вопросов)."""
    kit, out, exdir = workspace
    fake = dict(_REMARK_DISTORT,
                source_refs=[{"sid": "S1",
                              "anchor": "[табл.9, строка 9, кол 9]"}])
    same = [fake]
    auditor = _make_auditor([same, same, same, same])
    ex.run_exchange(kit, out, exdir, auditor, _make_worker({}),
                    max_rounds=6)
    rep = ex.run_arbiter(kit, exdir, _make_arbiter("в анкету"),
                         _enforcing_worker)
    assert rep["added_questions"]
    final = m.load_model(exdir / "versions" / rep["final_version"])
    q = next(x for x in final["questions"]
             if x["id"] == rep["added_questions"][0])
    rule = next(r for r in final["rules"]
                if r["id"] == q["rule_refs"][0])
    assert q["source_refs"][0]["anchor"] \
        == rule["sources"][0]["anchor"]
    assert "[табл.9" not in q["source_refs"][0]["anchor"]


def test_patch_with_ghost_anchor_is_rejected(workspace):
    """Патч с трассировкой вне заморозки отбраковывается с причиной
    (живой урок UniA run-0051: вымышленные якоря аудитора доезжали
    до гейта извлечения)."""
    kit, out, exdir = workspace
    ghost_patch = {"rule_id": "R-S1-п-1-9",
                   "rule": {"id": "R-S1-п-1-9",
                            "title": "Отдых на базе",
                            "statement": "отдых на базе не менее "
                                         "10 часов",
                            "zone": "BOTH", "kind": "HARD",
                            "sources": [{"sid": "S1",
                                         "anchor": "[табл.9, "
                                                   "строка 1]"}]}}
    rm = dict(_REMARK_DISTORT)

    def worker(prompt):
        got = json.loads(prompt.split("ЗАМЕЧАНИЯ (JSON):\n", 1)[1]
                         .split("\n\nСВОД", 1)[0]) \
            if "ЗАМЕЧАНИЯ (JSON):" in prompt else None
        ids = _re.findall(r'"id":\s*"(A\d+-\d+)"', prompt)
        return json.dumps({"answers": [
            {"remark": i, "verdict": "ПРИНЯТО",
             "basis": "источник: цитата",
             "change": "строка переписана",
             "patch": ghost_patch} for i in ids]},
            ensure_ascii=False)

    auditor = _make_auditor([[rm], [rm], [rm]])
    ex.run_exchange(kit, out, exdir, auditor, worker, max_rounds=3)
    rej = []
    for rd in sorted((exdir / "rounds").glob("round-*")):
        p = rd / "patch-rejections.json"
        if p.is_file():
            rej += json.loads(p.read_text(encoding="utf-8"))
    assert any("вне заморозки" in r0
               for r in rej for r0 in r.get("reasons", [])), rej
    final_files = sorted((exdir / "versions").glob("model-v*.json"))
    final = m.load_model(final_files[-1])
    rule = next(r for r in final["rules"] if r["id"] == "R-S1-п-1-9")
    assert all("[табл.9" not in s.get("anchor", "")
               for s in rule["sources"])


def test_patch_with_nondict_source_is_rejected(workspace):
    """Источник патча строкой вместо объекта — призрак, отбраковка
    без падения (живой урок UniA run-0056)."""
    kit, out, exdir = workspace
    bad = {"rule_id": "R-S1-п-1-9",
           "rule": {"id": "R-S1-п-1-9", "title": "Отдых",
                    "statement": "отдых не менее 10 часов",
                    "zone": "BOTH", "kind": "HARD",
                    "sources": ["п.1.9"]}}
    rm = dict(_REMARK_DISTORT)

    def worker(prompt):
        ids = _re.findall(r'"id":\s*"(A\d+-\d+)"', prompt)
        return json.dumps({"answers": [
            {"remark": i, "verdict": "ПРИНЯТО",
             "basis": "цитата", "change": "правка",
             "patch": bad} for i in ids]}, ensure_ascii=False)

    auditor = _make_auditor([[rm], [rm], [rm]])
    ex.run_exchange(kit, out, exdir, auditor, worker, max_rounds=3)
    rej = []
    for rd in sorted((exdir / "rounds").glob("round-*")):
        p = rd / "patch-rejections.json"
        if p.is_file():
            rej += json.loads(p.read_text(encoding="utf-8"))
    assert any("вне заморозки" in r0
               for r in rej for r0 in r.get("reasons", [])), rej


def test_задание_полноты_в_каждом_круге_аудитора():
    """Пятёрка проверок владельца 2026-08-30, п. 4 (полнота ИД→ВМП;
    живой стоп прод-демо run-0002: пропуск строки п. 2.5 был молчалив
    пять кругов — аудитор судил качество имеющихся строк, полноту не
    судил никто): перечень клауз без единой трассировки строится
    МАШИННО и входит заданием полноты в промпт КАЖДОГО круга; клаузы,
    покрытые строкой, вопросом или целевой, в перечень не попадают."""
    clauses = [
        {"anchor": "п.1", "text": "Отдых не менее 10 часов"},
        {"anchor": "п.2", "text": "Ночи вне базы — штраф за каждую"},
        {"anchor": "п.3", "text": "Порог разброса уточняется"},
        {"anchor": "п.4", "text": "Минимизировать человеко-дни"}]
    doc = {
        "rules": [{"id": "R-1", "zone": "PAIRING", "kind": "HARD",
                   "title": "Отдых", "statement": "rest >= 600",
                   "sources": [{"sid": "S1", "anchor": "п.1"}]}],
        "questions": [{"id": "Q-1", "part": "1", "title": "Порог",
                       "text": "Назовите порог.",
                       "answer_format": {"type": "value"},
                       "rule_refs": ["R-1"],
                       "source_refs": [{"sid": "S1",
                                        "anchor": "п.3"}]}],
        "objectives": [{"id": "O-1", "zone": "PAIRING",
                        "direction": "minimize",
                        "statement": "минимум человеко-дней",
                        "sources": [{"sid": "S1", "anchor": "п.4"}]}]}
    unc = ex.uncovered_clause_anchors(doc, "S1", clauses)
    assert unc == ["п.2"], unc

    prompts: list[str] = []

    def auditor(prompt):
        prompts.append(prompt)
        return json.dumps({"remarks": []}, ensure_ascii=False)

    pack, _rej = ex._audit_round(auditor, doc,
                                 [{"sid": "S1", "clauses": clauses}],
                                 1)
    assert len(prompts) == 1
    assert "ЗАДАНИЕ ПОЛНОТЫ" in prompts[0]
    assert "[п.2]" in prompts[0].split("ЗАДАНИЕ ПОЛНОТЫ")[1] \
        .split("ОТКРЫТЫЕ ВОПРОСЫ")[0]
    # Покрытые якоря в перечень не попали.
    body = prompts[0].split("ЗАДАНИЕ ПОЛНОТЫ")[1] \
        .split("ОТКРЫТЫЕ ВОПРОСЫ")[0]
    for a in ("[п.1]", "[п.3]", "[п.4]"):
        assert a not in body, a


def test_происхождение_значений_из_применённых_ответов():
    """Живой круг полноты run-0002 (30.08): аудитор объявил
    «изобретением» вес и порог, пришедшие ПРИНЯТЫМИ ответами анкеты
    (в клаузах их нет по построению — их назвала авиакомпания).
    Классовое лечение двумя ремнями: (1) машинная страховка мандата
    отбраковывает «изобретение» по строке из rule_refs применённого
    вопроса; (2) листинг строк аудитора несёт пометку происхождения."""
    doc = {
        "rules": [{"id": "R-W", "zone": "PAIRING", "kind": "SOFT",
                   "title": "Окно", "statement": "штраф 25 за Пт/Сб",
                   "weight": 25,
                   "sources": [{"sid": "S1", "anchor": "п.7"}]}],
        "questions": [{"id": "Q-3a-GOAL", "part": "3a",
                       "title": "Целевая", "text": "Веса мягких.",
                       "answer_format": {"type": "registry"},
                       "rule_refs": ["R-W"],
                       "source_refs": [{"sid": "S1", "anchor": "п.7"}],
                       "answer": {"raw": "Пт/Сб: вес 25",
                                  "status": "применён",
                                  "revision": 2,
                                  "goal_decline": False}}],
        "objectives": []}
    # (1) Машинная страховка мандата.
    rm = {"id": "A-X", "severity": "КРИТИЧЕСКОЕ",
          "defect_class": "изобретение",
          "target": {"rule": "R-W"},
          "source_refs": [{"sid": "S1", "anchor": "п.7"}],
          "text": "Вес 25 не подтверждён клаузами",
          "evidence": "клауза веса не несёт"}
    reason = ex.awaiting_decision_reason(doc, rm)
    assert reason and "применённым ответом" in reason \
        and "Q-3a-GOAL" in reason
    # «Изобретение» по строке БЕЗ применённого вопроса — мандат
    # аудитора, страховка молчит.
    rm2 = dict(rm, target={"rule": "R-НЕТ"})
    assert ex.awaiting_decision_reason(doc, rm2) is None
    # (2) Пометка происхождения в листинге строк аудитора.
    clauses = [{"anchor": "п.7", "text": "Ночёвка в Пт/Сб хуже."}]
    prompts: list[str] = []

    def auditor(prompt):
        prompts.append(prompt)
        return json.dumps({"remarks": []}, ensure_ascii=False)

    ex._audit_round(auditor, doc,
                    [{"sid": "S1", "clauses": clauses}], 1)
    assert "[значения установлены применённым ответом анкеты: " \
           "Q-3a-GOAL]" in prompts[0]


def test_a07_rejected_critical_patch_leaves_cycle_not_closed(workspace):
    """A07 (контрпример аудитора CONS-02 через живой оркестратор):
    критическое замечание принято, заплата отбракована контрактом
    (форменный отказ — не терминальный замок), дальше аудитор молчит.
    Прежде два «чистых» раунда давали «успех»; теперь — «не закрыто»
    с перечнем блокирующих, реестр registry/2, гейт зелёный на честном
    вердикте и красный на подменённом."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    bad_patch = {"rule_id": "R-S1-п-1-9",
                 "rule": {"id": "R-S1-п-1-9", "title": "х",
                          "statement": "х", "zone": "PAIRING",
                          "kind": "HARD", "sources": []}}
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: принято",
                               "change": "правка",
                               "patch": bad_patch}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=4)
    assert summary["verdict"] == "не закрыто"
    assert summary["rounds"] == 3
    assert [h["weighty"] for h in summary["history"]] == \
        [[0, 1, 0], [0, 0, 0], [0, 0, 0]]
    rv = summary["registry_verdict"]
    assert rv["count"] == 1
    assert rv["blockers"][0]["severity"] == "КРИТИЧЕСКОЕ"
    assert rv["blockers"][0]["state"] == "патч отбракован"
    assert summary["deadlocks"] == []     # не тупик — правка не встала
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    assert reg["schema_version"] == "registry/2"
    assert [f["state"] for f in reg["findings"]] == ["патч отбракован"]
    assert rv["blockers"][0]["id"] == reg["findings"][0]["id"]
    problems, _c = gate_exchange.check(exdir)
    assert problems == [], problems
    # Подмена вердикта на «успех» — красный гейт с именем находки.
    vpath = exdir / "verdict.json"
    v = json.loads(vpath.read_text(encoding="utf-8"))
    v["verdict"] = "успех"
    vpath.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    problems, _c = gate_exchange.check(exdir)
    assert any(reg["findings"][0]["id"] in p for p in problems), problems


def test_a07_bk_stop_is_impossible_with_live_blocker(workspace):
    """Ревью A07, находка 1: остановка «по Б/К» (путь панели, bk_stop)
    судится и реестром. Критическое замечание принято, заплата
    отбракована («патч отбракован» живо), следующий обмен несёт одно
    серьёзное — метрика (0,0,1) прежде останавливала цикл словами
    «Б 0, К 0»; теперь цикл идёт дальше и заканчивается «не закрыто»;
    подменённый «остановлен по Б/К» гейт краснит с именем находки."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    bad_patch = {"rule_id": "R-S1-п-1-9",
                 "rule": {"id": "R-S1-п-1-9", "title": "х",
                          "statement": "х", "zone": "PAIRING",
                          "kind": "HARD", "sources": []}}
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [dict(_REMARK_OMIT)],
                             [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: принято",
                               "change": "правка",
                               "patch": bad_patch}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5, bk_stop=True, bk_stop_after=0)
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    assert [h["weighty"][:2] for h in summary["history"]][0] == [0, 1]
    assert summary["history"][1]["weighty"] == [0, 0, 1]
    assert summary["registry_verdict"]["count"] == 1
    blocker = summary["registry_verdict"]["blockers"][0]
    assert blocker["state"] == "патч отбракован"
    problems, _c = gate_exchange.check(exdir)
    assert problems == [], problems
    vpath = exdir / "verdict.json"
    v = json.loads(vpath.read_text(encoding="utf-8"))
    v["verdict"] = "остановлен по Б/К"
    vpath.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    problems, _c = gate_exchange.check(exdir)
    assert any("остановлен по Б/К" in p and blocker["id"] in p
               for p in problems), problems


def test_a07_bk_stop_positive_control_clean_registry(workspace):
    """Контроль: при пустом реестре блокирующих обмен с одним серьёзным
    (0,0,1) по-прежнему останавливает цикл «остановлен по Б/К», и гейт
    зелёный."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_OMIT)], [], []])
    worker = _make_worker({})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5, bk_stop=True, bk_stop_after=0)
    assert summary["verdict"] == "остановлен по Б/К", summary["verdict"]
    assert summary["registry_verdict"] == {"blockers": [], "count": 0}
    problems, _c = gate_exchange.check(exdir)
    assert problems == [], problems


def test_a07_code_rejection_is_live_and_gate_replays_it(workspace,
                                                        monkeypatch):
    """Ревью A07 (второй проход, находка 1): терминальная отбраковка
    замком кода оставляет находку живой («отклонено кодом», без
    closed_round), цикл заканчивается «не закрыто», а повторная
    проводка гейта применяет ту же отбраковку — гейт зелёный на честном
    вердикте и красный на подменённом «успех» с именем находки."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    # Любая причина отбраковки — терминальная (маркер-пустая строка
    # входит в любую строку): та же дорожка, что «смена семьи» и т. п.
    monkeypatch.setattr(ex, "_TERMINAL_REJECTION_MARKERS", ("",))
    bad_patch = {"rule_id": "R-S1-п-1-9",
                 "rule": {"id": "R-S1-п-1-9", "title": "х",
                          "statement": "х", "zone": "PAIRING",
                          "kind": "HARD", "sources": []}}
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: принято",
                               "change": "правка",
                               "patch": bad_patch}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=4)
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    f = reg["findings"][0]
    # Композиция A07 × A09 (решение аудитора 12.09.2026,
    # DECISION-A07-A09-INTEGRATION-LAW). Прежнее ожидание «состояние
    # остаётся „отклонено кодом“» описывало писателя A07 ДО §12.8; общий
    # закон — тупик вида «отклонено кодом», и вид назван отдельным полем
    # `deadlock_kind`, так что сведений не теряется. Предмет пробы
    # (находка живая, closed_round не ставится, цикл кончается «не
    # закрыто», повторная проводка гейта даёт ту же отбраковку) не
    # затронут.
    assert f["state"] == "тупик"
    assert f["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert f["closed_round"] is None
    assert f["code_rejected_round"] == 1
    assert f["code_rejection"]
    assert summary["registry_verdict"]["count"] == 1
    assert summary["registry_verdict"]["blockers"][0]["state"] == "тупик"
    problems, _c = gate_exchange.check(exdir)
    assert problems == [], problems
    vpath = exdir / "verdict.json"
    v = json.loads(vpath.read_text(encoding="utf-8"))
    v["verdict"] = "успех"
    vpath.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    problems, _c = gate_exchange.check(exdir)
    assert any(f["id"] in p for p in problems), problems


# ── Обязательные замечания независимого аудитора к A07 (ef0ac6f9,
# 12.09.2026) на ЖИВОМ оркестраторе: F1 «принятие без исполненного
# патча», F3 «старое закрытие замком кода переживает миграцию», F4
# «неизвестная/неполная форма реестра чинится до проверки версии».
# Ожидания — контракт П0 §0 п. 5 и §1.2 с инверсией дефекта.

def _dumpj(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _legacy_code_closure(closed_round=3):
    """Запись прежнего писателя (ed53ef3a): отвергнутая замком заплата
    закрывалась `closed_round` и `closed_by="замок кода"`."""
    return {"findings": [{"id": "OLD-1", "key": "k-перенесённый",
                          "severity": "КРИТИЧЕСКОЕ",
                          "state": "отклонено кодом", "opened_round": 1,
                          "closed_round": closed_round,
                          "closed_by": "замок кода"}]}


@pytest.mark.parametrize("patch_fields",
                         [{}, {"patch": None}, {"patch": {}}],
                         ids=["нет-поля", "null", "пустой-объект"])
@pytest.mark.parametrize("verdict", ["ПРИНЯТО", "ЧАСТИЧНО"])
def test_a07_empty_promise_does_not_close_critical_finding(
        workspace, patch_fields, verdict):
    """A07-F1: обработчик отвечает ПРИНЯТО/ЧАСТИЧНО с основанием и
    описанием правки, но заплаты нет (поля нет, null или {}). Ничего не
    исполнено — находка ЖИВАЯ («патч отбракован» с причиной), молчание
    аудитора её не закрывает, цикл — «не закрыто», гейт зелёный на
    честном вердикте и красный на подменённом «успех». Прежде такие два
    «чистых» раунда давали «успех» при неизменной модели."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], [], []])
    worker = _make_worker({"Потеряно уточнение": {
        "verdict": verdict, "basis": "цитата: «на базе»",
        "change": "обещано исправить", **patch_fields}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=4)
    # Фикстура ничего не правит: все версии свода совпадают.
    docs = [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((exdir / "versions").glob("model-v*.json"))]
    assert all(d == docs[0] for d in docs)
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    assert summary["registry_verdict"]["count"] == 1
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    f = reg["findings"][0]
    assert f["state"] == "патч отбракован"
    assert f["closed_round"] is None
    assert "без исполненной правки" in f["patch_reason"]
    # Причина записана в отбраковке раунда — её читает реплей гейта.
    rej = json.loads((exdir / "rounds" / "round-01"
                      / "patch-rejections.json").read_text(encoding="utf-8"))
    assert len(rej) == 1
    assert "без исполненной правки" in rej[0]["reasons"][0]
    problems, _c = gate_exchange.check(exdir)
    assert problems == [], problems
    vpath = exdir / "verdict.json"
    v = json.loads(vpath.read_text(encoding="utf-8"))
    v["verdict"] = "успех"
    vpath.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    problems, _c = gate_exchange.check(exdir)
    assert any(f["id"] in p for p in problems), problems


def test_a07_executed_patch_still_closes_the_finding(workspace):
    """Положительный контроль к F1: НАСТОЯЩАЯ заплата применяется,
    находка становится «исправлено» и законно закрывается молчанием
    следующего аудита; отбраковок нет, цикл — «успех», гейт зелёный."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], []])
    worker = _make_worker({"Потеряно уточнение": {
        "verdict": "ПРИНЯТО", "basis": "цитата: «на базе» — принято",
        "change": "строка уточнена", "patch": _PATCH_FIX}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5)
    assert summary["verdict"] == "успех", summary["verdict"]
    assert summary["registry_verdict"] == {"blockers": [], "count": 0}
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    assert [f["state"] for f in reg["findings"]] == ["закрыто"]
    assert reg["findings"][0]["closed_round"] == 2
    assert json.loads((exdir / "rounds" / "round-01"
                       / "patch-rejections.json")
                      .read_text(encoding="utf-8")) == []
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]


@pytest.mark.parametrize("closed_round", [1, 3])
def test_a07_legacy_code_closure_is_live_on_read_and_continuation(
        workspace, monkeypatch, closed_round):
    """A07-F3: закрытие «замком кода» прежнего писателя — не
    исполненный патч и не решение арбитра. При ЧТЕНИИ старого цикла
    панель показывает «не закрыто (по реестру)», файлы истории не
    переписываются; ПРОДОЛЖЕНИЕ настоящим циклом даёт «не закрыто» (не
    «успех»), номер прежнего закрытия сохраняется отдельным полем,
    гейт зелёный."""
    from cdpm import webui
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    run_dir = exdir.parent
    _dumpj(exdir / "registry.json", _legacy_code_closure(closed_round))
    _dumpj(exdir / "verdict.json", {"verdict": "успех", "rounds": 3,
                                    "max_rounds": 9})
    _dumpj(run_dir / "status.json", {"state": "аудит-цикл: успех"})
    frozen = {p: p.read_bytes() for p in (exdir / "registry.json",
                                          exdir / "verdict.json",
                                          run_dir / "status.json")}
    monkeypatch.setattr(webui, "_run_dir", lambda _rid: run_dir)
    shown = webui.exchange_status("run-0001")
    assert shown["verdict"]["verdict"] == "успех"        # как записано
    assert shown["verdict_by_registry"] == "не закрыто (по реестру)"
    assert shown["registry_verdict"]["count"] == 1
    assert webui._state_by_registry(run_dir, "аудит-цикл: успех") \
        == "аудит-цикл: не закрыто (по реестру): 1 блокирующих"
    assert [f["id"] for f in webui._live_findings(run_dir)] == ["OLD-1"]
    assert all(p.read_bytes() == b for p, b in frozen.items())
    summary = ex.run_exchange(kit, out, exdir, _make_auditor([[], []]),
                              _make_worker({}), max_rounds=2)
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    assert summary["registry_verdict"]["count"] == 1
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    f = reg["findings"][0]
    # Композиция A07 × A09: чтение возвращает унаследованную запись в
    # живые (F3) и тем же чтением доводит её до тупика вида «отклонено
    # кодом» (§12.8) — так у вчерашней записи появляется путь к арбитру,
    # которого прежде не было. Прежнее ожидание сохраняло СЛОВО
    # состояния A07; общий закон сохраняет ВИД отдельным полем, а
    # живость, номер прежнего закрытия и зелёный гейт — предмет этой
    # пробы — не меняются.
    assert f["state"] == "тупик"
    assert f["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert f["closed_round"] is None
    assert f["legacy_code_closure_round"] == closed_round
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]


def test_a07_real_closure_and_arbiter_survive_continuation(workspace):
    """Положительный контроль к F3: закрытие по исполненному патчу и
    решение арбитра миграция НЕ переоткрывает — перенесённый реестр
    даёт «успех» на двух чистых раундах, гейт зелёный."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    _dumpj(exdir / "registry.json", {"findings": [
        {"id": "OLD-1", "key": "k1", "severity": "КРИТИЧЕСКОЕ",
         "state": "закрыто", "opened_round": 1, "closed_round": 2},
        {"id": "OLD-2", "key": "k2", "severity": "БЛОКИРУЮЩЕЕ",
         "state": "арбитр: обработчик прав", "opened_round": 1,
         "closed_round": None}]})
    summary = ex.run_exchange(kit, out, exdir, _make_auditor([[], []]),
                              _make_worker({}), max_rounds=2)
    assert summary["verdict"] == "успех", summary["verdict"]
    assert summary["registry_verdict"] == {"blockers": [], "count": 0}
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    assert [f["closed_round"] for f in reg["findings"]] == [2, None]
    assert gate_exchange.check(exdir)[0] == []


def test_a07_full_cycle_does_not_finish_clean_on_legacy_code_closure(
        workspace, monkeypatch):
    """A07-F3 на теле сквозной ручки: перенесённая старая отбраковка
    замком кода — живой Б/К-спор, поэтому финала «чисто по Б/К» нет;
    круг доходит до лимита. Диспетчер нити, каналы моделей и действие
    каталога подменены; реестр, обмен, гейт и канон — настоящие."""
    from cdpm import webui
    kit, out, exdir = workspace
    run_dir = exdir.parent
    kit.rename(run_dir / "freeze")
    _dumpj(exdir / "registry.json", _legacy_code_closure())
    _dumpj(run_dir / "status.json", {"state": "извлечено"})
    auditor, worker = _make_auditor([[], []]), _make_worker({})
    monkeypatch.setattr(webui, "_run_dir", lambda _rid: run_dir)
    monkeypatch.setattr(webui, "_start_stage_thread",
                        lambda run, fn, **kw: fn())
    monkeypatch.setattr(webui, "_catalog_auto_after", lambda *a: None)
    monkeypatch.setattr(webui, "_channel_for",
                        lambda scope, run, role, *a, **kw:
                        auditor if role == "auditor" else worker)
    webui.start_full_cycle.__wrapped__("run-0001", max_rounds=2,
                                       max_megacycles=1)
    status = json.loads((run_dir / "status.json")
                        .read_text(encoding="utf-8"))
    assert status.get("error") is None, status
    assert status["state"] == "сквозной цикл: лимит кругов (1)", status
    assert status["exchange_summary"]["verdict"] == "не закрыто", status


@pytest.mark.parametrize("prior_file", ["registry.json",
                                        "registry-start.json"])
@pytest.mark.parametrize("body", [
    {"schema_version": "registry/9", "findings": []},
    {"schema_version": "registry/2"}],
    ids=["неизвестная-версия", "неполная-новая-форма"])
def test_a07_cycle_refuses_unknown_and_incomplete_registry_form(
        workspace, prior_file, body):
    """A07-F4: объявленная версия и полнота новой формы судятся ДО
    продолжения и перезаписи. Прежде писатель молча менял версию на
    registry/2, цикл выдавал «успех», а гейт видел уже известную
    версию; registry/2 без «findings» превращался в пустой реестр."""
    kit, out, exdir = workspace
    _dumpj(exdir / prior_file, body)
    before = (exdir / prior_file).read_bytes()
    with pytest.raises(ex.ExchangeError) as err:
        ex.run_exchange(kit, out, exdir, _make_auditor([[], []]),
                        _make_worker({}), max_rounds=2)
    assert "реестр находок" in str(err.value)
    assert (exdir / prior_file).read_bytes() == before


@pytest.mark.parametrize("body", [
    {"schema_version": "registry/2", "findings": []},
    {"findings": []},
    None],
    ids=["пустой-registry-2", "старая-форма-registry-1", "реестра-нет"])
def test_a07_correct_registry_forms_are_accepted(workspace, body):
    """Положительный контроль к F4: корректный пустой registry/2,
    старая форма registry/1 и отсутствие реестра у нового цикла
    остаются законными — «успех» и зелёный гейт."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    if body is not None:
        _dumpj(exdir / "registry.json", body)
    summary = ex.run_exchange(kit, out, exdir, _make_auditor([[], []]),
                              _make_worker({}), max_rounds=2)
    assert summary["verdict"] == "успех", summary["verdict"]
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    assert reg["schema_version"] == ex.REGISTRY_SCHEMA
    assert reg["findings"] == []
    assert gate_exchange.check(exdir)[0] == []


# ── Третье неавтор-ревью дельты A07 (12.09.2026), проба P2: реплей
# гейта ВЫВОДИТ исполнение сам, а не верит файлу отбраковок раунда.

def test_a07_gate_replay_derives_execution_itself(workspace):
    """Проба P2 ревьюера, инверсия. Пустое обещание (ПРИНЯТО без
    заплаты) цикл записывает живым; подчистить кэш легко —
    patch-rejections.json опустошить, находку объявить «закрыто»,
    вердикт переписать на «успех». Прежде гейт зеленел: повторная
    проводка шла БЕЗ признака исполнения и получала ту же
    «исправлено». Теперь реплей выводит исполнение из ответа обмена
    (заплата строки/вида/вопроса либо законный перевод «выбора
    чтения» в вопрос анкеты) и краснит расхождение проекций."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], [], []])
    worker = _make_worker({"Потеряно уточнение": {
        "verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
        "change": "обещано исправить"}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=4)
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    # Положительный контроль: честный кэш зелёный.
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]
    # Подделка кэша: следов отбраковки нет, находка «закрыта»,
    # вердикт — «успех» со сводкой блокеров под него.
    _dumpj(exdir / "rounds" / "round-01" / "patch-rejections.json", [])
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    f = reg["findings"][0]
    f["state"], f["closed_round"] = "закрыто", 2
    f.pop("patch_reason", None)
    _dumpj(exdir / "registry.json", reg)
    v = json.loads((exdir / "verdict.json").read_text(encoding="utf-8"))
    v["verdict"] = "успех"
    v["registry_verdict"] = {"blockers": [], "count": 0}
    _dumpj(exdir / "verdict.json", v)
    problems, _c = gate_exchange.check(exdir)
    assert any("не совпадает с повторной проводкой" in p
               for p in problems), problems


def test_a07_gate_replay_counts_real_patch_as_executed(workspace):
    """Положительный контроль к P2: настоящая заплата — исполнение и
    для реплея; честное закрытие находки молчанием следующего аудита
    гейт принимает, «успех» зелёный."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], []])
    worker = _make_worker({"Потеряно уточнение": {
        "verdict": "ПРИНЯТО", "basis": "цитата: «на базе» — принято",
        "change": "строка уточнена", "patch": _PATCH_FIX}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5)
    assert summary["verdict"] == "успех", summary["verdict"]
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    assert [f["state"] for f in reg["findings"]] == ["закрыто"]
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]


# ---------------------------------------------------------------------------
# A07-F1, ЧЕТВЁРТОЕ неавтор-ревью (проба R2): исполнение судится СЛЕДОМ
# в версии свода, а не наличием формы заплаты. Прежде подчищенный
# patch-rejections.json возвращал «исполнение» ОТБРАКОВАННОЙ заплате —
# форма-то у неё была.

_Q_ANKETA = "Q-3a-S1-п-1-1"     # вопрос фикстуры, строимый не кодом
_PATCH_REMOVE = {"rule_id": "R-S1-п-1-9"}
_KIND_PATCH = {"rule_id": "R-S1-п-1-9", "kind": "SOFT", "weight": 5}
_QUESTION_PATCH = {"question_id": _Q_ANKETA,
                   "add_rule_refs": ["R-S1-п-1-9"]}


def _run_one_remark(workspace, remark, answer, max_rounds=4):
    """Цикл на одном замечании: аудитор даёт его в первом раунде, далее
    молчит. Возвращает (сводка, реестр, отбраковки раунда 1, версии)."""
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(remark)], [], [], []])
    worker = _make_worker({remark["text"][:12]: answer})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=max_rounds)
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    rej = json.loads((exdir / "rounds" / "round-01" /
                      "patch-rejections.json").read_text(encoding="utf-8"))
    versions = [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((exdir / "versions").glob("model-v*.json"))]
    return summary, reg, rej, versions


@pytest.mark.parametrize("remark,answer", [
    (_REMARK_DISTORT, {"verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
                       "change": "строка уточнена", "patch": _PATCH_FIX}),
    (_REMARK_DISTORT, {"verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
                       "change": "строка снята", "patch": _PATCH_REMOVE}),
    (_REMARK_OMIT, {"verdict": "ПРИНЯТО",
                    "basis": "цитата: «не планировать явку»",
                    "change": "строка добавлена", "patch": _PATCH_NEW}),
    (_REMARK_DISTORT, {"verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
                       "change": "вид мигрирован", "kind_patch": _KIND_PATCH}),
    (_REMARK_DISTORT, {"verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
                       "change": "ссылки вопроса добраны",
                       "question_patch": _QUESTION_PATCH}),
], ids=["строка", "снятие", "добавление", "вид", "вопрос"])
def test_a07_every_patch_form_leaves_a_trace_and_closes_the_finding(
        workspace, remark, answer):
    """Положительные контроли закона следа (R1a–R1e): КАЖДАЯ форма
    заплаты оставляет свой след в новой версии — правленая строка,
    снятая строка (OUT), новая строка, сменившийся вид, добранные цели
    вопроса. Находка закрывается молчанием следующего аудита,
    отбраковок нет, цикл — «успех», гейт зелёный и на цикле, и на
    повторной проводке."""
    from cdpm.gates import gate_exchange
    _kit, _out, exdir = workspace
    summary, reg, rej, versions = _run_one_remark(workspace, remark, answer)
    assert rej == [], rej
    assert versions[1] != versions[0], "форма без следа в версии"
    assert [f["state"] for f in reg["findings"]] == ["закрыто"]
    assert reg["findings"][0]["closed_round"] == 2
    assert summary["verdict"] == "успех", summary["verdict"]
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]


def test_a07_noop_patch_is_not_an_execution(workspace):
    """Закон следа в ЦИКЛЕ: заплата применилась, но ничего не изменила
    (добор ссылки, которая у вопроса уже стоит). Исполнением она не
    является — находка живая («патч отбракован» с причиной «заплата без
    следа»), цикл — «не закрыто», модель не менялась ни одной версией.
    Гейт на честном каталоге зелёный; подделка кэша (отбраковки стёрты,
    находка «закрыта», вердикт «успех») — красная."""
    from cdpm.gates import gate_exchange
    _kit, out, exdir = workspace
    doc0 = m.load_model(out / "model.json")
    q = next(q for q in doc0["questions"] if q["id"] == _Q_ANKETA)
    stays = q["rule_refs"][0]           # ссылка, которая уже стоит
    summary, reg, rej, versions = _run_one_remark(
        workspace, _REMARK_DISTORT,
        {"verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
         "change": "добор ссылки", "question_patch": {
             "question_id": _Q_ANKETA, "add_rule_refs": [stays]}})
    assert all(v == versions[0] for v in versions), "пустая заплата правит свод"
    assert [rj["answer"] for rj in rej] == ["A1-001"]
    assert rej[0]["reasons"] == [ex.NO_TRACE_REASON]
    f = reg["findings"][0]
    assert f["state"] == "патч отбракован"
    assert f["closed_round"] is None
    assert "заплата без следа" in f["patch_reason"]
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    assert summary["registry_verdict"]["count"] == 1
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]
    _forge_closed(exdir, f["id"])
    assert any("не совпадает с повторной проводкой" in p
               for p in gate_exchange.check(exdir)[0])


def _forge_closed(exdir, finding_id, round_no=1):
    """Подделка кэша завершённого цикла: отбраковки раунда стёрты,
    находка объявлена закрытой, вердикт — «успех» со сводкой под него."""
    _dumpj(exdir / "rounds" / f"round-{round_no:02d}"
           / "patch-rejections.json", [])
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    for f in reg["findings"]:
        if f["id"] == finding_id:
            f["state"], f["closed_round"] = "закрыто", round_no + 1
            f.pop("patch_reason", None)
    _dumpj(exdir / "registry.json", reg)
    v = json.loads((exdir / "verdict.json").read_text(encoding="utf-8"))
    v["verdict"] = "успех"
    v["registry_verdict"] = {"blockers": [], "count": 0}
    _dumpj(exdir / "verdict.json", v)


def test_a07_gate_replay_judges_execution_by_trace_not_by_form(workspace):
    """Проба R2 четвёртого неавтор-ревью (остаточный риск F1). Заплата
    ПРИШЛА формой ({"rule_id": …, "rule": "строка"}), но отбракована —
    правка не встала, находка живая. Кэш подделан: файл отбраковок
    пуст, находка «закрыто», вердикт «успех». Прежде реестр сходился с
    повторной проводкой (реплей верил форме) и гейт был ЗЕЛЁНЫМ; теперь
    реплей судит СЛЕДОМ в версиях: строка-цель не изменилась —
    исполнения нет, проводка даёт «патч отбракован», подделка красится
    словами «реестр не совпадает с повторной проводкой»."""
    from cdpm.gates import gate_exchange
    _kit, _out, exdir = workspace
    summary, reg, rej, versions = _run_one_remark(
        workspace, _REMARK_DISTORT,
        {"verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
         "change": "правка", "patch": {"rule_id": "R-S1-п-1-9",
                                       "rule": "строка"}})
    # Честный каталог: форма есть, следа нет, находка живая, гейт зелёный.
    assert all(v == versions[0] for v in versions)
    assert "не объект" in "; ".join(rej[0]["reasons"])
    f = reg["findings"][0]
    assert (f["state"], f["closed_round"]) == ("патч отбракован", None)
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]
    # Подделка кэша — красный гейт.
    _forge_closed(exdir, f["id"])
    problems, _c = gate_exchange.check(exdir)
    assert any("не совпадает с повторной проводкой" in p
               for p in problems), problems


def test_a07_reading_choice_conversion_is_traced_execution(workspace):
    """Положительный контроль к закону следа для пятой формы: принятый
    «выбор чтения» заплатой строку не правит — он рождает вопрос части
    2 и блокирует строку-носитель. Это СЛЕД: и цикл, и реплей считают
    такой ответ исполненным (вызов прямой: машинная страховка мандата
    не пускает такое замечание к строке, у которой уже открыт вопрос)."""
    _kit, out, _exdir = workspace
    doc = m.load_model(out / "model.json")
    rm = dict(_REMARK_DISTORT, id="A-001", defect_class="выбор чтения")
    pack = {"round": 1, "remarks": [rm]}
    resp = {"answers": [{"remark": "A-001", "verdict": "ПРИНЯТО",
                         "basis": "текущая строка верна",
                         "change": "правка", "patch": _PATCH_FIX}]}
    executed: set = set()
    new_doc, _accepted, rejected, _tok = ex._apply_patches(
        doc, pack, resp, 1, executed_out=executed)
    qid = resp["answers"][0].get("converted_to_question")
    assert qid and any(q["id"] == qid for q in new_doc["questions"])
    assert rejected == [], rejected
    assert executed == {"A-001"}
    assert ex.executed_by_trace(doc, new_doc, resp["answers"]) == {"A-001"}
    # Строка-носитель не правлена заплатой — она заблокирована вопросом.
    was = next(r for r in doc["rules"] if r["id"] == "R-S1-п-1-9")
    now = next(r for r in new_doc["rules"] if r["id"] == "R-S1-п-1-9")
    assert now["statement"] == was["statement"]
    assert qid in now["blockers"]


# ---------------------------------------------------------------------------
# A07-F1, ПЯТОЕ неавтор-ревью (12.09.2026). Находка 1 — ОДИН ЗАКОН
# ЦЕЛЕЙ: пустая (но применившаяся) заплата приносит касание строки и
# циклу, и повторной проводке гейта, иначе «патч отбракован» зреет в
# тупик только у одного из них и честный каталог краснеет. Находка 2 —
# СЛЕД СУДИТСЯ ПОКАЗНО: разница версий круга одна на строку, а принятых
# ответов на неё может быть несколько.

def _entry_row_patch(out, rid="R-S1-п-1-9"):
    """Заплата слово в слово равная строке НА ВХОДЕ цикла: свод берётся
    так же, как его берёт `run_exchange` (ярлыки наведены кодом), иначе
    «пустая» заплата на деле правила бы свод и проба стала бы иной."""
    doc = m.load_model(out / "model.json")
    m.derive_all_rule_traits(doc, "вход цикла обмена")
    row = next(r for r in doc["rules"] if r["id"] == rid)
    return {"rule_id": rid, "rule": json.loads(json.dumps(row))}


def test_a07_repeated_noop_patch_matures_alike_in_cycle_and_replay(
        workspace):
    """Проба P1 пятого неавтор-ревью (находка 1, ложный КРАСНЫЙ гейт на
    ЧЕСТНОМ цикле). Обработчик три круга подряд отвечает одной и той же
    копией строки слово в слово: заплата встаёт, но ничего не меняет.
    У цикла цель такой заплаты — КАСАНИЕ строки, и «патч отбракован»
    дозревает до тупика «осцилляция» (вердикт «стагнация»). Повторная
    проводка гейта прежде пропускала такой ответ целиком (он значится в
    отбраковках раунда) — касаний не считала, находка застревала в
    «патч отбракован», и гейт краснел словами «реестр не совпадает с
    повторной проводкой» без вины обработчика. Теперь закон целей ОДИН:
    ответ, ВСЕ причины отбраковки которого — «заплата без следа»,
    приносит цель и реплею. Исполнением он не становится ни там, ни
    там: судья исполнения — след в версии."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    patch = _entry_row_patch(out)
    same = [dict(_REMARK_DISTORT)]
    auditor = _make_auditor([same, same, same, [], []])
    worker = _make_worker({"Потеряно уточнение": {
        "verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
        "change": "правка", "patch": patch}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5)
    # Свод не сдвинулся ни одной версией — заплата пустая все три раза.
    versions = [json.loads(p.read_text(encoding="utf-8")) for p in
                sorted((exdir / "versions").glob("model-v*.json"))]
    assert all(v == versions[0] for v in versions), "заплата правит свод"
    for rnd in (1, 2, 3):
        rej = json.loads((exdir / "rounds" / f"round-{rnd:02d}"
                          / "patch-rejections.json")
                         .read_text(encoding="utf-8"))
        assert [r["reasons"] for r in rej] == [[ex.NO_TRACE_REASON]], rnd
    # ЦИКЛ: касания посчитаны, находка дозрела до тупика.
    assert summary["verdict"] == "стагнация", summary["verdict"]
    assert [(d["id"], d.get("deadlock_kind")) for d in
            summary["deadlocks"]] == [("R1-A1-001", "осцилляция")]
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    assert [(f["state"], f.get("deadlock_kind")) for f in
            reg["findings"]] == [("тупик", "осцилляция")]
    assert reg["touch"] == {"R-S1-п-1-9": 3}
    # РЕПЛЕЙ: проекция жизненного цикла та же — гейт зелёный. Именно
    # это расхождение (у цикла «тупик», у реплея «патч отбракован») и
    # краснило гейт до правки.
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]


def test_a07_form_rejected_patch_never_touches_the_row(workspace):
    """Контроль границы к находке 1 (контрпример аудитора CONS-02 на
    трёх кругах): заплата, отбракованная ФОРМОЙ (контракт свода), в
    строку не встала — касания она не приносит НИ циклу, НИ реплею, и
    «патч отбракован» в тупик не зреет, сколько бы кругов ни повторялся
    спор. Так «один закон целей» не превращается в «любая отбраковка —
    касание»."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    bad_patch = {"rule_id": "R-S1-п-1-9",
                 "rule": {"id": "R-S1-п-1-9", "title": "х",
                          "statement": "х", "zone": "PAIRING",
                          "kind": "HARD", "sources": []}}
    same = [dict(_REMARK_DISTORT)]
    auditor = _make_auditor([same, same, same, [], []])
    worker = _make_worker({"Потеряно уточнение": {
        "verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
        "change": "правка", "patch": bad_patch}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5)
    assert summary["deadlocks"] == [], summary["deadlocks"]
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    assert [f["state"] for f in reg["findings"]] == ["патч отбракован"]
    assert reg.get("touch", {}) == {}
    assert ex.no_trace_only(json.loads(
        (exdir / "rounds" / "round-01" / "patch-rejections.json")
        .read_text(encoding="utf-8"))) == set()
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]


def test_a07_noop_answer_does_not_close_on_another_answers_fix(
        workspace):
    """Проба P2c пятого неавтор-ревью (находка 2, ложный ЗЕЛЁНЫЙ). В
    ОДНОМ круге на ОДНУ строку принято два ответа: пустая копия прежней
    строки и настоящее исправление. Разница версий у круга ОДНА на
    строку — прежде исполненными числились ОБА, пустое обещание
    закрывалось молчанием следующего аудита, реестр показывал две
    «закрыто», а вердикт цикла был «успех» при зелёном гейте. Теперь
    каждая заплата судится ТОЙ редакцией строки, что стоит перед ней:
    копия получает «заплату без следа», исполнение — только у
    исправления, находка пустого обещания остаётся живой и блокирующей,
    вердикт — «не закрыто». Гейт на честном каталоге зелёный."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    noop = _entry_row_patch(out)
    rm_fix = dict(_REMARK_DISTORT, defect_class="изобретение",
                  text="Лишнее уточнение выдумано")
    auditor = _make_auditor([[dict(_REMARK_DISTORT), rm_fix], [], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: «на базе»",
                               "change": "правка", "patch": noop},
        "Лишнее уточнение": {"verdict": "ПРИНЯТО",
                             "basis": "цитата: «на базе»",
                             "change": "правка", "patch": _PATCH_FIX}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=4)
    rej = json.loads((exdir / "rounds" / "round-01"
                      / "patch-rejections.json").read_text(encoding="utf-8"))
    assert [(r["answer"], r["reasons"]) for r in rej] == \
        [("A1-001", [ex.NO_TRACE_REASON])], rej
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    assert [(f["id"], f["state"], f.get("closed_round"))
            for f in reg["findings"]] == \
        [("R1-A1-001", "патч отбракован", None),
         ("R1-A1-002", "закрыто", 2)]
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    assert summary["registry_verdict"]["count"] == 1
    assert summary["registry_verdict"]["blockers"][0]["id"] == "R1-A1-001"
    # Настоящее исправление в своде стоит — пустая заплата его не сняла.
    v1 = json.loads((exdir / "versions" / "model-v01.json")
                    .read_text(encoding="utf-8"))
    row = next(r for r in v1["rules"] if r["id"] == "R-S1-п-1-9")
    assert row["statement"] == _PATCH_FIX["rule"]["statement"]
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]


def test_a07_forged_cache_of_shared_row_is_red(workspace):
    """Шестое неавтор-ревью A07, остаток (а): два принятых ответа на одну
    строку в одном круге — пустая копия строки и настоящая правка.
    Честный цикл: копия отбракована («заплата без следа»), находка
    живая, «не закрыто», гейт зелёный. Подделка кэша (стёрта отбраковка
    копии, находка «закрыто», вердикт «успех») — гейт красный словами:
    заявленная заплата равна строке прежней версии, а отбраковки нет."""
    import copy as _copy
    from cdpm import model as m
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    entry = m.load_model(out / "model.json")
    m.derive_all_rule_traits(entry, "тест")
    rid = "R-S1-п-1-9"
    row = next(r for r in entry["rules"] if r["id"] == rid)
    noop = {"rule_id": rid, "rule": _copy.deepcopy(row)}
    rm_fix = dict(_REMARK_DISTORT, defect_class="изобретение",
                  text="Лишнее уточнение выдумано")
    auditor = _make_auditor([[dict(_REMARK_DISTORT), rm_fix], [], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО", "basis": "б",
                               "change": "правка", "patch": noop},
        "Лишнее уточнение": {"verdict": "ПРИНЯТО", "basis": "б",
                             "change": "правка",
                             "patch": _copy.deepcopy(_PATCH_FIX)}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=4)
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    problems, _c = gate_exchange.check(exdir)
    assert problems == [], problems
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    noop_finding = next(f for f in reg["findings"]
                        if f["state"] == "патч отбракован")
    # Подделка: стереть отбраковку копии, закрыть находку, объявить успех.
    prej_p = exdir / "rounds" / "round-01" / "patch-rejections.json"
    prej = json.loads(prej_p.read_text(encoding="utf-8"))
    forged_answer = next(r["answer"] for r in prej
                         if ex.NO_TRACE_REASON in "".join(
                             str(x) for x in r.get("reasons", [])))
    prej_p.write_text(json.dumps([r for r in prej
                                  if r["answer"] != forged_answer],
                                 ensure_ascii=False), encoding="utf-8")
    noop_finding["state"] = "закрыто"
    noop_finding["closed_round"] = 2
    noop_finding.pop("patch_reason", None)
    (exdir / "registry.json").write_text(json.dumps(reg, ensure_ascii=False),
                                         encoding="utf-8")
    vpath = exdir / "verdict.json"
    v = json.loads(vpath.read_text(encoding="utf-8"))
    v["verdict"] = "успех"
    v["registry_verdict"] = {"blockers": [], "count": 0}
    vpath.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    problems, _c = gate_exchange.check(exdir)
    assert any(forged_answer in p and "подчищенный след" in p
               for p in problems), problems


import copy as _copy                                        # noqa: E402

# ---------------------------------------------------------------------------
# A07-R1 (вердикт VERDICT-A07-d9343ead, 12.09.2026): ИСХОД ПРИНАДЛЕЖИТ
# ОТВЕТУ. Гейт проводит круг заново тем же кодом, что и цикл, и сверяет
# три предъявленные вещи: версию свода, исходы ответов и реестр.
# Прежде исполнение выводилось разницей версий — и стёртая отбраковка
# плохого ответа давала ему исполнение ЗА СЧЁТ чужой правки той же
# строки (подделанный кэш проезжал зелёным).
# ---------------------------------------------------------------------------

def _shared_row_cycle(workspace, bad_rule, bad_first):
    """Два замечания на ОДНУ строку: у одного заплата негодна формой, у
    другого — настоящее исправление. Возвращает (сводка, exdir)."""
    kit, out, exdir = workspace
    bad_rm = dict(_REMARK_DISTORT, text="Отбракованный ответ на строку")
    good_rm = dict(_REMARK_DISTORT, defect_class="изобретение",
                   text="Исполненный ответ на строку")
    remarks = [bad_rm, good_rm] if bad_first else [good_rm, bad_rm]
    worker = _make_worker({
        "Отбракованный ответ": {
            "verdict": "ПРИНЯТО", "basis": "цитата: на базе",
            "change": "обещано", "patch": {"rule_id": "R-S1-п-1-9",
                                           "rule": _copy.deepcopy(bad_rule)}},
        "Исполненный ответ": {
            "verdict": "ПРИНЯТО", "basis": "цитата: на базе",
            "change": "строка уточнена",
            "patch": _copy.deepcopy(_PATCH_FIX)}})
    summary = ex.run_exchange(kit, out, exdir,
                              _make_auditor([remarks, [], [], []]), worker,
                              max_rounds=4)
    return summary, exdir


@pytest.mark.parametrize("bad_rule",
                         ["строка-вместо-объекта", ["список"],
                          {"title": "неполный объект"}],
                         ids=["строка", "список", "неполный-объект"])
@pytest.mark.parametrize("bad_first", [True, False],
                         ids=["плохой-первым", "плохой-последним"])
def test_a07_r1_rejected_answer_cannot_borrow_another_answer_trace(
        workspace, bad_rule, bad_first):
    """Честный круг: плохая заплата отбракована, хорошая исполнена,
    критическая находка живая, гейт зелёный. Подделка кэша (стереть
    запись отбраковки, объявить находку закрытой, вердикт «успех») —
    красный С ИМЕНЕМ ОТВЕТА: прежде отбракованный ответ получал
    исполнение по чужому изменению той же строки."""
    from cdpm.gates import gate_exchange
    summary, exdir = _shared_row_cycle(workspace, bad_rule, bad_first)
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]
    reg = json.loads((exdir / "registry.json").read_text(encoding="utf-8"))
    bad_f = next(f for f in reg["findings"]
                 if f["text"] == "Отбракованный ответ на строку")
    good_f = next(f for f in reg["findings"]
                  if f["text"] == "Исполненный ответ на строку")
    assert bad_f["state"] == "патч отбракован" and bad_f["closed_round"] is None
    assert good_f["state"] == "закрыто"
    prej_p = exdir / "rounds" / "round-01" / "patch-rejections.json"
    prej = json.loads(prej_p.read_text(encoding="utf-8"))
    pack = json.loads((exdir / "rounds" / "round-01" / "remarks.json")
                      .read_text(encoding="utf-8"))
    bad_id = next(r["id"] for r in pack["remarks"]
                  if r["text"] == "Отбракованный ответ на строку")
    assert [r["answer"] for r in prej] == [bad_id]
    # Подделка только выдачи: код и ответы не трогаются.
    _dumpj(prej_p, [r for r in prej if r["answer"] != bad_id])
    bad_f["state"], bad_f["closed_round"] = "закрыто", 2
    bad_f.pop("patch_reason", None)
    _dumpj(exdir / "registry.json", reg)
    v = json.loads((exdir / "verdict.json").read_text(encoding="utf-8"))
    v["verdict"] = "успех"
    v["registry_verdict"] = {"blockers": [], "count": 0}
    _dumpj(exdir / "verdict.json", v)
    problems, _c = gate_exchange.check(exdir)
    assert problems, "ЛОЖНЫЙ ЗЕЛЁНЫЙ: отбраковка стёрта, гейт молчит"
    assert any(bad_id in p for p in problems), problems


def _two_fixes_cycle(workspace, field="statement"):
    """Две НЕЗАВИСИМЫЕ законные правки одной строки в одном круге —
    по разным полям записи."""
    kit, out, exdir = workspace
    first = _copy.deepcopy(_PATCH_FIX)
    second = _copy.deepcopy(_PATCH_FIX)
    second["rule"][field] = {
        "statement": "rest_home ≥ 10 ч (на базе, уточнено)",
        "title": "Минимальный отдых на базе (ОМ-А, уточнено)"}[field]
    rm1 = dict(_REMARK_DISTORT, text="Первая правка строки")
    rm2 = dict(_REMARK_DISTORT, defect_class="изобретение",
               text="Вторая правка строки")
    worker = _make_worker({
        "Первая правка": {"verdict": "ПРИНЯТО", "basis": "цитата: на базе",
                          "change": "правка 1", "patch": first},
        "Вторая правка": {"verdict": "ПРИНЯТО", "basis": "цитата: на базе",
                          "change": "правка 2", "patch": second}})
    summary = ex.run_exchange(kit, out, exdir,
                              _make_auditor([[rm1, rm2], [], [], []]),
                              worker, max_rounds=4)
    return summary, exdir, second


@pytest.mark.parametrize("field", ["statement", "title"])
def test_a07_r1_order_of_answers_is_part_of_the_law(workspace, field):
    """Порядок ответов — часть закона: заплата судится редакцией
    строки ПЕРЕД НЕЙ. Честный круг зелёный; переставленный в
    response.json порядок даёт другую итоговую строку, чем
    предъявленная версия, — красный. Поле правки значения не имеет:
    судится ЗНАЧЕНИЕ записи, а не выбранное поле."""
    from cdpm.gates import gate_exchange
    summary, exdir, second = _two_fixes_cycle(workspace, field)
    problems, _c = gate_exchange.check(exdir)
    assert problems == [], problems
    final = json.loads((exdir / "versions" / "model-v01.json")
                       .read_text(encoding="utf-8"))
    row = next(r for r in final["rules"] if r["id"] == "R-S1-п-1-9")
    assert row[field] == second["rule"][field]
    rpath = exdir / "rounds" / "round-01" / "response.json"
    resp = json.loads(rpath.read_text(encoding="utf-8"))
    _dumpj(rpath, dict(resp, answers=list(reversed(resp["answers"]))))
    problems, _c = gate_exchange.check(exdir)
    assert any("не совпадает с детерминированной проводкой" in p
               for p in problems), problems


def test_a07_r1_each_addition_belongs_to_its_own_answer(workspace):
    """Добавления с ОБЩИМ заявленным id: строку-новинку именует КОД,
    и каждое обещание забирает из пула ровно одну. Честный круг
    зелёный; спрятать одну добавленную строку (или одну заплату) в
    выдаче нельзя — проводка предъявляет ровно то, что выводится из
    ответов."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    rm1 = dict(_REMARK_OMIT, text="Первое добавление нормы")
    rm2 = dict(_REMARK_OMIT, text="Второе добавление нормы")
    worker = _make_worker({
        "Первое добавление": {"verdict": "ПРИНЯТО", "basis": "цитата: явка",
                              "change": "строка добавлена",
                              "patch": _copy.deepcopy(_PATCH_NEW)},
        "Второе добавление": {"verdict": "ПРИНЯТО", "basis": "цитата: явка",
                              "change": "строка добавлена",
                              "patch": _copy.deepcopy(_PATCH_NEW)}})
    ex.run_exchange(kit, out, exdir, _make_auditor([[rm1, rm2], [], [], []]),
                    worker, max_rounds=4)
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]
    v0 = json.loads((exdir / "versions" / "model-v00.json")
                    .read_text(encoding="utf-8"))
    vpath = exdir / "versions" / "model-v01.json"
    v1 = json.loads(vpath.read_text(encoding="utf-8"))
    added = [r for r in v1["rules"]
             if r["id"] not in {x["id"] for x in v0["rules"]}]
    assert len(added) == 2 and added[0]["id"] != added[1]["id"], added
    # Подделка 1: одна добавленная строка стёрта из предъявленной версии.
    _dumpj(vpath, dict(v1, rules=[r for r in v1["rules"]
                                  if r["id"] != added[1]["id"]]))
    assert any("не совпадает с детерминированной проводкой" in p
               for p in gate_exchange.check(exdir)[0])
    _dumpj(vpath, v1)
    # Подделка 2: одна заплата стёрта из предъявленного ответа —
    # добавленная строка второму обещанию не достаётся.
    rpath = exdir / "rounds" / "round-01" / "response.json"
    resp = json.loads(rpath.read_text(encoding="utf-8"))
    victim = resp["answers"][1]["remark"]
    resp["answers"][1].pop("patch")
    _dumpj(rpath, resp)
    problems, _c = gate_exchange.check(exdir)
    assert any(victim in p for p in problems), problems


def test_a07_r1_rollback_of_another_answer_fix_stays_named(workspace):
    """Эффект, отменённый поздней правкой: вторая заплата возвращает
    строку к редакции НАЧАЛА круга, уже переправленной первой. Цикл
    её не применяет (откат чужого следа); стёртая отбраковка —
    красный гейт с именем ответа."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    doc0 = m.load_model(out / "model.json")
    entry = _copy.deepcopy(next(r for r in doc0["rules"]
                                if r["id"] == "R-S1-п-1-9"))
    rm1 = dict(_REMARK_DISTORT, text="Правка строки на базе")
    rm2 = dict(_REMARK_DISTORT, defect_class="изобретение",
               text="Возврат строки к прежней редакции")
    worker = _make_worker({
        "Правка строки": {"verdict": "ПРИНЯТО", "basis": "цитата: на базе",
                          "change": "правка",
                          "patch": _copy.deepcopy(_PATCH_FIX)},
        "Возврат строки": {"verdict": "ПРИНЯТО", "basis": "цитата: на базе",
                           "change": "возврат",
                           "patch": {"rule_id": "R-S1-п-1-9", "rule": entry}}})
    ex.run_exchange(kit, out, exdir, _make_auditor([[rm1, rm2], [], [], []]),
                    worker, max_rounds=4)
    prej_p = exdir / "rounds" / "round-01" / "patch-rejections.json"
    prej = json.loads(prej_p.read_text(encoding="utf-8"))
    assert any(ex.ROLLBACK_REASON in (r.get("reasons") or []) for r in prej), \
        prej
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]
    victim = next(r["answer"] for r in prej
                  if ex.ROLLBACK_REASON in (r.get("reasons") or []))
    _dumpj(prej_p, [r for r in prej if r["answer"] != victim])
    problems, _c = gate_exchange.check(exdir)
    assert any(victim in p for p in problems), problems


def test_a07_r1_gate_without_frozen_kit_refuses_rounds_with_patches(
        workspace):
    """СУДИТЬ НЕЧЕМ: заплаты судились клаузами и якорями заморозки —
    без комплекта детерминированной проводки нет, и круг с заплатами
    зелёным не считается (fail-closed). Круг БЕЗ заплат судится и без
    комплекта: проводка его тождественна."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    summary, _e, _s = _two_fixes_cycle(workspace)
    assert gate_exchange.check(exdir)[0] == []
    moved = kit.parent / "kit-moved"
    kit.rename(moved)
    problems, _c = gate_exchange.check(exdir)
    assert any("судить нечем" in p for p in problems), problems
    moved.rename(kit)
    assert gate_exchange.check(exdir)[0] == []
    # Подменённый комплект — названная причина, а не другой результат.
    receipt = exdir / ex.REPLAY_CONTEXT_FILE
    body = json.loads(receipt.read_text(encoding="utf-8"))
    _dumpj(receipt, dict(body, digest="0" * 64))
    problems, _c = gate_exchange.check(exdir)
    assert any("судить нечем" in p and "не тот" in p
               for p in problems), problems


@pytest.mark.parametrize("forged", [{"a": 1}, "мусор", ["не объект"], 7],
                         ids=["объект", "строка", "не-записи", "число"])
def test_a07_r1_rejections_file_of_wrong_form_is_named_not_a_crash(
        workspace, forged):
    """Перечень отбраковок — предъявленная сторона сверки: подделать
    его ФОРМОЙ нельзя. Негодная форма называется словами (гейт
    красный), а не роняет проверку трассировкой — аналитик читает
    причину."""
    from cdpm.gates import gate_exchange
    summary, exdir, _second = _two_fixes_cycle(workspace)
    assert gate_exchange.check(exdir)[0] == []
    _dumpj(exdir / "rounds" / "round-01" / "patch-rejections.json", forged)
    problems, _c = gate_exchange.check(exdir)
    assert any("patch-rejections.json негоден формой" in p
               for p in problems), problems


def test_a07_r1_cycle_without_patches_is_green_without_the_kit(workspace):
    """Положительный контроль: круг, где обработчик всё отклонил,
    заплат не несёт — проводка тождественна, и старый каталог без
    комплекта остаётся зелёным."""
    from cdpm.gates import gate_exchange
    kit, out, exdir = workspace
    ex.run_exchange(kit, out, exdir,
                    _make_auditor([[dict(_REMARK_DISTORT)], [], []]),
                    _make_worker({}), max_rounds=3)
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]
    kit.rename(kit.parent / "kit-moved")
    (exdir / ex.REPLAY_CONTEXT_FILE).unlink()
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]


# ---------------------------------------------------------------------------
# A07-R4 (вердикт VERDICT-A07-d9343ead): ОТВЕТ — АТОМАРНАЯ ЕДИНИЦА.
# Негодна любая часть — свод по этому ответу не меняется вовсе, а в
# отбраковку ложится ОДНА запись со всеми причинами. Прежде допустимая
# часть вставала в свод, а негодная давала отбраковку: цикл менял
# строку, реплей исключал ответ целиком — честный результат краснел
# словами «строка изменена вне перечня принятых замечаний».
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("extra_field", ["kind_patch", "question_patch"])
@pytest.mark.parametrize("bad_value", ["негодная-операция",
                                       {"rule_id": "R-нет", "kind": "SOFT"}],
                         ids=["не-объект", "негодное-содержание"])
def test_a07_r4_mixed_answer_is_all_or_nothing(workspace, extra_field,
                                               bad_value):
    """Смешанный ответ: законная заплата строки + негодная вторая
    часть. Свод не меняется ни одной версией, запись отбраковки одна,
    находка живая, вердикт «не закрыто», гейт ЗЕЛЁНЫЙ (честный
    результат не обязан краснеть)."""
    from cdpm.gates import gate_exchange
    if extra_field == "question_patch" and isinstance(bad_value, dict):
        bad_value = {"question_id": "Q-нет", "add_rule_refs": ["R-нет"]}
    _kit, _out, exdir = workspace
    summary, reg, rej, versions = _run_one_remark(
        workspace, _REMARK_DISTORT,
        {"verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
         "change": "правка строки и вторая операция",
         "patch": _copy.deepcopy(_PATCH_FIX),
         extra_field: bad_value})
    assert all(v == versions[0] for v in versions), \
        "часть отбракованного ответа встала в свод"
    assert [r["answer"] for r in rej] == ["A1-001"], rej
    assert len(rej) == 1 and len(rej[0]["reasons"]) >= 1
    f = reg["findings"][0]
    assert f["state"] == "патч отбракован" and f["closed_round"] is None
    assert summary["verdict"] == "не закрыто", summary["verdict"]
    problems, _c = gate_exchange.check(exdir)
    assert problems == [], problems


def test_a07_r4_two_bad_parts_give_one_record_with_all_reasons(workspace):
    """Две негодные части — одна запись отбраковки со всеми причинами
    (а не две записи и не первая попавшаяся)."""
    from cdpm.gates import gate_exchange
    _kit, _out, exdir = workspace
    summary, reg, rej, versions = _run_one_remark(
        workspace, _REMARK_DISTORT,
        {"verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
         "change": "две негодные операции",
         "kind_patch": "не-объект", "question_patch": 42})
    assert all(v == versions[0] for v in versions)
    assert len(rej) == 1 and rej[0]["answer"] == "A1-001"
    reasons = "; ".join(rej[0]["reasons"])
    assert "заплата вида не объект" in reasons, reasons
    assert "заплата вопроса не объект" in reasons, reasons
    assert reg["findings"][0]["state"] == "патч отбракован"
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]


def test_a07_r4_two_valid_parts_of_one_answer_both_stand(workspace):
    """Положительный контроль: две ДОПУСТИМЫЕ части одного ответа
    встают обе, находка исправлена и закрывается встречной проверкой,
    гейт зелёный."""
    from cdpm.gates import gate_exchange
    _kit, out, exdir = workspace
    doc0 = m.load_model(out / "model.json")
    q = next(q for q in doc0["questions"] if q["id"] == _Q_ANKETA)
    missing = next(r["id"] for r in doc0["rules"]
                   if r["id"] not in (q.get("rule_refs") or []))
    summary, reg, rej, versions = _run_one_remark(
        workspace, _REMARK_DISTORT,
        {"verdict": "ПРИНЯТО", "basis": "цитата: «на базе»",
         "change": "строка уточнена и вопрос дособран",
         "patch": _copy.deepcopy(_PATCH_FIX),
         "question_patch": {"question_id": _Q_ANKETA,
                            "add_rule_refs": [missing]}})
    assert rej == [], rej
    assert versions[1] != versions[0], "заплата ответа не встала"
    final_q = next(x for x in versions[-1]["questions"]
                   if x["id"] == _Q_ANKETA)
    # A published question retains its old scope; the extension is a new edition.
    from cdpm import question_editions
    assert final_q["rule_refs"] == q["rule_refs"]
    current_q = question_editions.latest(versions[-1], final_q)
    assert current_q["id"] != final_q["id"]
    assert missing in current_q["rule_refs"]
    assert reg["findings"][0]["state"] == "закрыто"
    assert summary["verdict"] == "успех", summary["verdict"]
    assert gate_exchange.check(exdir)[0] == [], gate_exchange.check(exdir)[0]


# ── A07-X02 (12.09.2026, остаток шестого неавтор-ревью «след вместо
# слов») и A07-R5 (продолжение цикла). ЗАКОН: гейт читает
# НЕДОВЕРЕННЫЕ файлы каталога, и ни один из них не вправе уронить
# проверку трассировкой — артефакт негодной формы получает ОДИН
# НАЗВАННЫЙ отказ, круг с ним не судится, гейт краснеет СЛОВАМИ.

from cdpm.gates import gate_exchange                      # noqa: E402


def _converged_cycle(workspace):
    """Честный сошедшийся цикл — тот самый каталог, на котором гейт
    зелёный: положительный контроль всех проб формы."""
    kit, out, exdir = workspace
    auditor = _make_auditor([[dict(_REMARK_DISTORT)], [], []])
    worker = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: «на базе» — принято",
                               "change": "строка уточнена",
                               "patch": _PATCH_FIX}})
    summary = ex.run_exchange(kit, out, exdir, auditor, worker,
                              max_rounds=5)
    return summary, exdir


#: Три падения, снятые пробой a07fix5/probe_gate_fuzz.py на этом же
#: каталоге ДО правки: «'str' object has no attribute 'get'» дважды и
#: «string indices must be integers» — стек вместо причины.
_X02_CASES = [
    ("rounds/round-01/remarks.json", {"round": 1, "remarks": "abc"},
     "поле «remarks» не список: str"),
    ("versions/model-v01.json", "abc",
     "артефакт versions/model-v01.json: не объект: str"),
    ("versions/model-v01.json",
     {"rules": "abc", "questions": [], "sources": []},
     "поле «rules» не список: str"),
    # Тем же законом (проба дала те же падения): корень пакета не
    # объект, записи замечаний не объекты, версия свода списком,
    # строки свода не объекты, метрика и ответ не объекты.
    ("rounds/round-01/remarks.json", [1, 2],
     "артефакт rounds/round-01/remarks.json: не объект: list"),
    ("rounds/round-01/remarks.json", {"round": 1, "remarks": ["x", 3]},
     "записи поля «remarks» не объекты"),
    ("rounds/round-01/response.json", "abc",
     "артефакт rounds/round-01/response.json: не объект: str"),
    ("rounds/round-01/metrics.json", [1],
     "артефакт rounds/round-01/metrics.json: не объект: list"),
    ("versions/model-v00.json", {"rules": ["x"], "questions": [],
                                 "sources": []},
     "записи поля «rules» не объекты"),
    ("verdict.json", "abc", "артефакт verdict.json: не объект: str"),
    ("tokens.json", {"a": 1}, "артефакт tokens.json: не перечень записей"),
]


@pytest.mark.parametrize("rel,value,words", _X02_CASES,
                         ids=[f"{i}-{c[0].split('/')[-1]}"
                              for i, c in enumerate(_X02_CASES)])
def test_a07_x02_gate_names_malformed_artifact_instead_of_crashing(
        workspace, capsys, rel, value, words):
    """Артефакт негодной формы: гейт НЕ падает, а называет его и
    краснеет. Прежде отказ формы пролезал внутрь контрактов
    (`validate_remarks`, `check_diff_only`) и ронял проверку
    AttributeError/TypeError — аналитик получал стек вместо причины, а
    гейт неопределённый исход вместо красного."""
    _summary, exdir = _converged_cycle(workspace)
    # Положительный контроль: честный каталог зелёный.
    assert gate_exchange.check(exdir)[0] == []
    path = exdir / rel
    assert path.is_file(), rel
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    problems, _c = gate_exchange.check(exdir)     # не падает
    assert any(words in p for p in problems), problems
    # Один артефакт — один отказ формы (финальная версия свода судится
    # и обменом, и хвостом проверки).
    assert len([p for p in problems if words in p]) == 1, problems
    # И красный СЛОВАМИ через CLI: код возврата 1, причина напечатана.
    capsys.readouterr()
    assert gate_exchange.run(str(exdir)) == 1
    printed = capsys.readouterr().out
    assert "НАРУШЕНИЕ" in printed and "ВЕРДИКТ: КРАСНЫЙ" in printed


def test_a07_x02_broken_json_artifact_is_named_not_traced(workspace):
    """Рваный JSON — тот же закон: названный отказ, а не трассировка
    JSONDecodeError из середины гейта."""
    _summary, exdir = _converged_cycle(workspace)
    (exdir / "rounds" / "round-01" / "remarks.json").write_text(
        "{not json", encoding="utf-8")
    problems, _c = gate_exchange.check(exdir)
    assert any("не читается как JSON" in p for p in problems), problems


def test_a07_x02_honest_catalog_stays_green(workspace):
    """Ложных красных закон формы не приносит: честный сошедшийся цикл
    (и он же после перечитывания) зелёный, счётчики те же."""
    summary, exdir = _converged_cycle(workspace)
    assert summary["verdict"] == "успех", summary["verdict"]
    first, c1 = gate_exchange.check(exdir)
    second, c2 = gate_exchange.check(exdir)
    assert first == [] and second == [], (first, second)
    assert c1 == c2 and c1["rounds"] == 3 and c1["findings"] == 1


def test_a07_r5_continuation_refuses_a_vanished_registry(workspace):
    """A07-R5 у ПРОДОЛЖЕНИЯ ЦИКЛА: вердикт цикла лежит в том же
    каталоге — исход заявлен, реестр обязан быть. Прежде исчезнувший
    registry.json читался пустым, и продолжение тихо теряло
    перенесённые находки и требования арбитра («обработчик прав»
    поднимался заново)."""
    kit, out, exdir = workspace
    _summary, _exdir = _converged_cycle(workspace)
    assert (exdir / "verdict.json").is_file()
    assert (exdir / "registry.json").is_file()
    (exdir / "registry.json").unlink()

    def must_not_be_called(prompt):
        raise AssertionError("модель вызвана при отказе продолжения")
    with pytest.raises(ex.RegistryMissing) as got:
        ex.run_exchange(kit, out, exdir, must_not_be_called,
                        must_not_be_called, max_rounds=5)
    assert "реестр отсутствует после исполненного цикла" in str(got.value)
    # Положительный контроль: реестр на месте — продолжение идёт
    # прежним путём (обмены воспроизводятся с диска, модели молчат).
    ex._dump_registry(exdir / "registry.json",
                      ex.load_registry(exdir / "registry-start.json"))
    replay = ex.run_exchange(kit, out, exdir, must_not_be_called,
                             must_not_be_called, max_rounds=5)
    assert replay["verdict"] == "успех", replay["verdict"]
