# -*- coding: utf-8 -*-
"""Штрафная сверка (vmp/penalty_reconcile.py), пункт 3 определения Д4:
штраф по ВМП (нарушения сертификата × коэффициент формы) равен штрафу
решателя (совпадения × применённый вес), вклад целевой отдельно, сумма
вкладов равна мягкому счёту. Несравнимое не считается сошедшимся."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "open-bench" / "vmp-stand"))

import stand  # noqa: E402
from vmp import penalty_reconcile as pr  # noqa: E402

LAYOVER = "Layover nights away from home"
BALANCE = "Balanced workload"


def _form():
    pkg, _e = stand.step_C0()
    return pkg["form"]


def _last_run(**over):
    base = {"appliedWeights": {LAYOVER: 10, BALANCE: 5},
            "effectiveWeights": {LAYOVER: 110, BALANCE: 5},
            "weightRules": {"S-LAYOVER": LAYOVER, "S-BALANCE": BALANCE},
            "objective": {"id": "O-P1", "constraint": LAYOVER, "weight": 100,
                          "metric": "layover_nights_total",
                          "sharesConstraintWithPenalty": True}}
    base.update(over)
    return base


def _breakdown(layover_matches=3, balance_matches=2, **over):
    cons = {LAYOVER: {"hard": 0, "soft": -layover_matches * 110,
                      "matches": layover_matches, "weight": "0hard/-110soft"},
            BALANCE: {"hard": 0, "soft": -balance_matches * 5,
                      "matches": balance_matches, "weight": "0hard/-5soft"}}
    soft = sum(v["soft"] for v in cons.values())
    d = {"score": "0hard/%dsoft" % soft, "hard": 0, "soft": soft,
         "constraints": cons}
    d.update(over)
    return d


def _certificate(layover=3, balance=2, run=("S-LAYOVER", "S-BALANCE")):
    checks = [{"id": "C-%s" % rid, "basis": {"rule": rid}, "status": "прогнана"}
              for rid in run]
    findings = [{"id": "F-%03d" % i, "rule_refs": ["S-LAYOVER"]}
                for i in range(layover)]
    findings += [{"id": "G-%03d" % i, "rule_refs": ["S-BALANCE"]}
                 for i in range(balance)]
    return {"checks": checks, "findings": findings}


def test_всё_сходится():
    res = pr.reconcile(_form(), _last_run(), _breakdown(), _certificate())
    assert res["verdict"] == pr.OK, pr.report(res)
    assert pr.is_clean(res)
    by = {r["rule"]: r for r in res["rows"]}
    assert by["S-LAYOVER"]["penalty_vmp"] == 30
    assert by["S-LAYOVER"]["penalty_solver"] == 30
    assert by["S-BALANCE"]["penalty_vmp"] == 10 == by["S-BALANCE"]["penalty_solver"]
    assert res["objective"]["verdict"] == pr.OK
    assert res["total"] == {"soft": -340, "sum_of_constraints": -340,
                            "verdict": pr.OK}
    assert res["counts"] == {pr.OK: 4, pr.DIVERGE: 0, pr.INCOMPARABLE: 0}
    text = pr.report(res)
    assert text.startswith("ШТРАФНАЯ СВЕРКА: СОШЛОСЬ")


def test_вес_решателя_не_равен_коэффициенту_формы():
    lr = _last_run(appliedWeights={LAYOVER: 12, BALANCE: 5})
    res = pr.reconcile(_form(), lr, _breakdown(), _certificate())
    assert res["verdict"] == pr.DIVERGE
    row = next(r for r in res["rows"] if r["rule"] == "S-LAYOVER")
    assert "вес решателя 12 ≠ коэффициент формы 10" in row["note"]


def test_сертификат_насчитал_другое_число_нарушений():
    res = pr.reconcile(_form(), _last_run(), _breakdown(), _certificate(layover=4))
    assert res["verdict"] == pr.DIVERGE
    row = next(r for r in res["rows"] if r["rule"] == "S-LAYOVER")
    assert row["certificate_violations"] == 4 and row["solver_matches"] == 3
    assert "единиц у решателя 3 ≠ нарушений по сертификату 4" in row["note"]
    assert not pr.is_clean(res)


def test_разбор_счёта_не_сходится_с_весами():
    bd = _breakdown()
    bd["constraints"][BALANCE]["soft"] = -11          # 2 × 5 = 10, а не 11
    res = pr.reconcile(_form(), _last_run(), bd, _certificate())
    assert res["verdict"] == pr.DIVERGE
    row = next(r for r in res["rows"] if r["rule"] == "S-BALANCE")
    # −11 при весе 5 — это 2,2 единицы против 2 нарушений сертификата и
    # штраф решателя 11 против 10 по ВМП.
    assert "единиц у решателя 2.2" in row["note"]
    assert "штраф по ВМП 10 ≠ штраф решателя 11" in row["note"]
    assert res["total"]["verdict"] == pr.DIVERGE     # итог тоже перекошен


def test_несравнимо_без_объявления_и_без_проверки():
    lr = _last_run(weightRules={"S-LAYOVER": LAYOVER})   # S-BALANCE не объявлено
    res = pr.reconcile(_form(), lr, _breakdown(), _certificate(run=("S-LAYOVER",)))
    # Правило без объявления — несравнимо; его ограничение при этом несёт
    # счёт, не приписанный ни одному правилу, — «вне ВМП» (разошлось).
    assert res["verdict"] == pr.DIVERGE and not pr.is_clean(res)
    by = {r["rule"]: r for r in res["rows"] if r["rule"]}
    assert by["S-BALANCE"]["verdict"] == pr.INCOMPARABLE
    assert "не объявил ограничение" in by["S-BALANCE"]["note"]
    outside = [r for r in res["rows"] if r["rule"] is None]
    assert [r["constraint"] for r in outside] == [BALANCE]
    lr2 = _last_run()
    res2 = pr.reconcile(_form(), lr2, _breakdown(), _certificate(run=("S-LAYOVER",)))
    assert {r["rule"]: r["verdict"] for r in res2["rows"]} == {
        "S-LAYOVER": pr.OK, "S-BALANCE": pr.INCOMPARABLE}
    assert "не прогнал проверку" in next(
        r for r in res2["rows"] if r["rule"] == "S-BALANCE")["note"]


def test_целевая_отдельно_и_с_общим_ограничением():
    lr = _last_run(effectiveWeights={LAYOVER: 111, BALANCE: 5})
    res = pr.reconcile(_form(), lr, _breakdown(), _certificate())
    assert res["objective"]["verdict"] == pr.DIVERGE
    assert res["verdict"] == pr.DIVERGE
    lr3 = _last_run(objective=None)
    res3 = pr.reconcile(_form(), lr3, _breakdown(), _certificate())
    assert res3["objective"] is None
    # без целевой soft LAYOVER = −3×110 больше не объясняется одним штрафом
    assert next(r for r in res3["rows"] if r["rule"] == "S-LAYOVER")["verdict"] \
        == pr.DIVERGE


def test_исключённая_до_данных_строка_не_участвует_и_только_своя_часть():
    form = copy.deepcopy(_form())
    form["rostering"]["soft"][0]["excluded_until_data"] = {"question": "Q-1",
                                                           "revision": 1}
    res = pr.reconcile(form, _last_run(), _breakdown(), _certificate())
    assert [r["rule"] for r in res["rows"]] == ["S-LAYOVER"]
    res_p = pr.reconcile(_form(), _last_run(), _breakdown(), _certificate(),
                         part="pairing")
    assert [r["rule"] for r in res_p["rows"]] == ["S-LAYOVER"]


def test_ограничение_вне_вмп_разошлось():
    """Самотест Д1 на H0 (03.09): решатель судил по литералам «Positioning
    deadhead cost» и «Fair weekend distribution», которых в ВМП нет —
    сверка обязана назвать их, а не молчать."""
    bd = _breakdown()
    bd["constraints"]["Positioning deadhead cost"] = {"hard": 0, "soft": -2000,
                                                      "matches": 4, "weight": "0hard/-500soft"}
    bd["constraints"]["Reserve crews"] = {"hard": -4, "soft": 0, "matches": 3,
                                          "weight": "-1hard/0soft"}
    bd["constraints"]["Quiet one"] = {"hard": 0, "soft": 0, "matches": 0}
    bd["soft"] = sum(v["soft"] for v in bd["constraints"].values())
    res = pr.reconcile(_form(), _last_run(), bd, _certificate())
    assert res["verdict"] == pr.DIVERGE
    outside = [r for r in res["rows"] if r["rule"] is None]
    assert {r["constraint"] for r in outside} == {"Positioning deadhead cost",
                                                  "Reserve crews"}
    assert all("вне ВМП" in r["note"] for r in outside)
    assert res["total"]["verdict"] == pr.OK          # сумма вкладов при этом сходится
    assert "Positioning deadhead cost" in pr.report(res)


def test_единицы_по_soft_и_меры_сертификата():
    """Ревизия семантики ночёвок (03.09): одно совпадение решателя несёт
    несколько ночей — единицы = −soft / вес; семья-мера (равномерность)
    сверяется по измеренной величине сертификата (measures), а не по
    числу находок."""
    bd = _breakdown()
    # Ростер: одно совпадение «Balanced workload» = мера 2 (units = 10/5).
    bd["constraints"][BALANCE] = {"hard": 0, "soft": -10, "matches": 1,
                                  "weight": "0hard/-5soft"}
    bd["soft"] = sum(v["soft"] for v in bd["constraints"].values())
    cert = _certificate(run=("S-LAYOVER",))
    cert["measures"] = {"S-BALANCE": {"value": 2}}
    res = pr.reconcile(_form(), _last_run(), bd, cert)
    assert res["verdict"] == pr.OK, pr.report(res)
    row = next(r for r in res["rows"] if r["rule"] == "S-BALANCE")
    assert row["solver_units"] == 2 and row["certificate_violations"] == 2
    assert row["certificate_status"] == "прогнана"
    # Контракт CDPM: мера внутри записи проверки (checks[].measure).
    cert_c = _certificate(run=("S-LAYOVER",))
    cert_c["checks"].append({"id": "C-S-BALANCE", "basis": {"rule": "S-BALANCE"},
                             "template": "мера-мягкого-правила", "status": "прогнана",
                             "measure": {"kind": "unfairness_sqrt_sum_sq_minutes",
                                         "value": 2, "raw": 2.4, "n": 24,
                                         "mean": 217.5}})
    res_c = pr.reconcile(_form(), _last_run(), bd, cert_c)
    assert res_c["verdict"] == pr.OK, pr.report(res_c)
    assert next(r for r in res_c["rows"] if r["rule"] == "S-BALANCE")["solver_units"] == 2
    # Мера сертификата 3 против единиц решателя 2 — разошлось, словами «мере».
    cert["measures"] = {"S-BALANCE": 3}
    res2 = pr.reconcile(_form(), _last_run(), bd, cert)
    row2 = next(r for r in res2["rows"] if r["rule"] == "S-BALANCE")
    assert row2["verdict"] == pr.DIVERGE and "мере" in row2["note"]
    # Ночёвки: одно совпадение на сотрудника, три ночи — единицы 3 из soft.
    lr = _last_run(objective=None, effectiveWeights={LAYOVER: 10, BALANCE: 5})
    bd2 = _breakdown()
    bd2["constraints"][LAYOVER] = {"hard": 0, "soft": -30, "matches": 1,
                                   "weight": "0hard/-10soft"}
    bd2["soft"] = sum(v["soft"] for v in bd2["constraints"].values())
    res3 = pr.reconcile(_form(), lr, bd2, _certificate(layover=3))
    row3 = next(r for r in res3["rows"] if r["rule"] == "S-LAYOVER")
    assert row3["verdict"] == pr.OK and row3["solver_units"] == 3


def test_мера_при_общем_ограничении_целевой_и_правила():
    """Задача стенда с известным ответом (сертификация прохода 26,
    03.09): целевая O-ROST (ставка 1) и правило VN-050 (коэффициент 3)
    метят в одно ограничение «Balanced workload», действующий вес 4;
    одно совпадение решателя несёт меру 174, soft −696. Единицы =
    −soft / действующий вес = 174 = мере сертификата; штраф правила
    3 × 174 = 522, вклад целевой 1 × 174 = 174, сумма 696. Совпадениями
    (1) сверка врала: «единиц 1 ≠ мере 174»."""
    form = copy.deepcopy(_form())          # у стенда коэффициент 5, у H0 — 3
    next(r for r in form["rostering"]["soft"]
         if r["id"] == "S-BALANCE")["penalty"]["coefficient"] = 3
    lr = _last_run(appliedWeights={LAYOVER: 10, BALANCE: 3},
                   effectiveWeights={LAYOVER: 10, BALANCE: 4},
                   objective={"id": "O-ROST", "constraint": BALANCE,
                              "weight": 1, "metric": "credit_hours_spread",
                              "sharesConstraintWithPenalty": True})
    bd = _breakdown()
    bd["constraints"][LAYOVER] = {"hard": 0, "soft": -70, "matches": 7,
                                  "weight": "0hard/-10soft"}
    bd["constraints"][BALANCE] = {"hard": 0, "soft": -696, "matches": 1,
                                  "weight": "0hard/-4soft"}
    bd["soft"] = sum(v["soft"] for v in bd["constraints"].values())
    cert = _certificate(layover=7, run=("S-LAYOVER",))
    cert["checks"].append({"id": "C-S-BALANCE", "basis": {"rule": "S-BALANCE"},
                           "status": "прогнана",
                           "measure": {"kind": "unfairness_sqrt_sum_sq_minutes",
                                       "value": 174, "raw": 174.3, "n": 23}})
    res = pr.reconcile(form, lr, bd, cert)
    assert res["verdict"] == pr.OK, pr.report(res)
    row = next(r for r in res["rows"] if r["rule"] == "S-BALANCE")
    assert (row["solver_units"], row["penalty_vmp"], row["penalty_solver"],
            row["objective_share"]) == (174, 522, 522, 174)
    assert (res["objective"]["solver_units"],
            res["objective"]["objective_contribution"]) == (174, 174)
    # Действующий вес, не равный весу правила + ставке целевой, — расхождение
    # и у строки правила, и у целевой.
    lr_bad = _last_run(appliedWeights={LAYOVER: 10, BALANCE: 3},
                       effectiveWeights={LAYOVER: 10, BALANCE: 3},
                       objective=lr["objective"])
    res_bad = pr.reconcile(form, lr_bad, bd, cert)
    assert res_bad["verdict"] == pr.DIVERGE
    assert "действующий вес" in next(
        r for r in res_bad["rows"] if r["rule"] == "S-BALANCE")["note"]
    assert res_bad["objective"]["verdict"] == pr.DIVERGE


def test_сверка_целевой_пейринга_по_каталогу():
    """Контракт Б, P-424 (единая версия 06.09.2026): суммы без области
    учёта — не «сошлось»: каталог без подписи цены, без расшифровок и без
    независимого пересчёта — «несравнимо» (нет свидетельства), а не
    «разошлось»; расхождение сумм остаётся «разошлось»."""
    cat = {"selected": [{"fns": ["L1"], "cost": 120}, {"fns": ["L2"], "cost": 10.5}],
           "ip_objective": 130.5}
    res = pr.reconcile_pairing(cat, {"total_cost": 130.5})
    assert res["verdict"] == pr.INCOMPARABLE and res["sum_of_chains"] == 130.5
    assert res["known_subtotal_verdict"] == pr.INCOMPARABLE
    assert res["full_cost_confirmed"] is False and res["signature_verified"] is False
    assert "подпись цены" in res["note"] and "нет расшифровки цены" in res["note"]
    assert [r["verdict"] for r in res["measure_rows"]] == [pr.INCOMPARABLE] * 2
    assert not pr.is_clean(res)
    bad = pr.reconcile_pairing(dict(cat, ip_objective=131), {"total_cost": 130.5})
    assert bad["verdict"] == pr.DIVERGE and "целевая 131 ≠" in bad["note"]
    assert bad["known_subtotal_verdict"] == pr.DIVERGE
    bad2 = pr.reconcile_pairing(cat, {"total_cost": 999})
    assert bad2["verdict"] == pr.DIVERGE and "total_cost отчёта 999" in bad2["note"]
    assert pr.reconcile_pairing({"selected": []})["verdict"] == pr.INCOMPARABLE
    nocost = {"selected": [{"fns": ["L1"]}], "ip_objective": 5}
    assert pr.reconcile_pairing(nocost)["verdict"] == pr.INCOMPARABLE
    noobj = {"selected": [{"fns": ["L1"], "cost": 5}]}
    assert pr.reconcile_pairing(noobj)["verdict"] == pr.INCOMPARABLE
    assert "известная часть не подтверждена" in pr.reconcile_pairing(noobj)["note"]
    without_report = pr.reconcile_pairing(cat)                  # без отчёта
    assert without_report["verdict"] == pr.INCOMPARABLE
    assert without_report["full_cost_confirmed"] is False
    inf = {"selected": [{"fns": ["L1"], "cost": float("inf")}], "ip_objective": 5}
    assert pr.reconcile_pairing(inf)["verdict"] == pr.INCOMPARABLE


def test_полнота_стоимости_ростеринга_в_сверке():
    """P-415 + H11-7 (единая версия 06.09.2026, политика P-417, план
    §3.10.3): штрафная сверка ростеринга называет полноту оценки
    стоимости отдельными полями — по оценке области учёта
    (cost_assessment), а без неё по шапке cost_certainty сертификата
    (показание, не подтверждение); при неполной оценке сошедшаяся
    сверка остаётся «сошлось», полная стоимость не подтверждена.
    full_cost_confirmed — только при полной области, паритете событий с
    решателем «OK» и покрытии ВСЕХ членов цены событиями ночёвок
    (GPT-014): один complete=True без сверки событий и при члене цены
    вне ночёвок (равномерность) флага не даёт."""
    form, lr, bd = _form(), _last_run(), _breakdown()
    res = pr.reconcile(form, lr, bd, _certificate())
    assert res["verdict"] == pr.OK and res["known_subtotal_verdict"] == pr.OK
    assert res["arithmetic_subtotal_verdict"] == pr.OK
    assert res["price_complete"] is None and res["full_cost_confirmed"] is False
    assert res["cost_assessment"] is None
    partial = _certificate()
    partial["cost_certainty"] = "partial"
    res_p = pr.reconcile(form, lr, bd, partial)
    assert res_p["verdict"] == pr.OK and pr.is_clean(res_p)
    assert res_p["price_complete"] is False and res_p["full_cost_confirmed"] is False
    full = _certificate()
    full["cost_certainty"] = "full"
    full["cost_assessment"] = {"task": "rostering", "complete": True}
    res_f = pr.reconcile(form, lr, bd, full)
    assert res_f["verdict"] == pr.OK and res_f["price_complete"] is True
    assert res_f["cost_assessment"] == {"task": "rostering", "complete": True}
    # Сверка событий с записью решателя не проводилась — полнота не
    # подтверждена, вердикт не понижен.
    assert res_f["full_cost_confirmed"] is False and res_f["night_cost_confirmed"] is False
    assert "не проводилась" in res_f["cost_scope_note"]
    # Полная область и паритет событий «OK» подтверждают только член
    # «ночёвки» (price_scope nights); равномерность S-BALANCE — вне
    # области событий, потому полная стоимость всё ещё не подтверждена.
    full["cost_assessment"]["solver_comparison"] = {"verdict": "OK", "reasons": []}
    res_n = pr.reconcile(form, lr, bd, full)
    assert res_n["verdict"] == pr.OK and res_n["night_cost_confirmed"] is True
    assert res_n["price_scope"] == "nights"
    assert res_n["price_members_uncovered"] == ["S-BALANCE"]
    assert res_n["full_cost_confirmed"] is False
    # полная оценка при несошедшейся или несравнимой сверке — не подтверждение
    diverged = pr.reconcile(form, lr, bd, dict(full, findings=full["findings"][1:]))
    assert diverged["verdict"] == pr.DIVERGE and diverged["full_cost_confirmed"] is False
    nested = {"certificate": dict(full)}
    assert pr.reconcile(form, lr, bd, nested)["price_complete"] is True
    incomparable = pr.reconcile(form, _last_run(weightRules={"S-LAYOVER": LAYOVER}),
                                bd, dict(full, checks=full["checks"][:1]))
    assert incomparable["verdict"] != pr.OK
    assert incomparable["full_cost_confirmed"] is False


def test_счёт_сертификата():
    counts, status = pr.certificate_counts(_certificate(layover=2, balance=0))
    assert counts == {"S-LAYOVER": 2, "S-BALANCE": 0}
    assert status == {"S-LAYOVER": "прогнана", "S-BALANCE": "прогнана"}
    c2, s2 = pr.certificate_counts({"checks": [
        {"basis": {"rule": "R"}, "status": "не прогнана"},
        {"basis": {"rule": "R"}, "status": "прогнана"}], "findings": []})
    assert s2 == {"R": "прогнана"} and c2 == {"R": 0}
