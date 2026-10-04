"""Мутационное доказательство исполнителей строк 7 и 8 свода — живьём.

Ограничение, которое «выглядит применённым», от настоящего отличает
только одно: счёт меняется ровно на то, что оно считает. Здесь это
проверяется на ЖИВОМ решателе с пакетом, без /schedules/analyze
(в этой сборке он платный): все жёсткие штрафы неположительны, поэтому
равенство hard == −(сумма нехваток оракула) при нуле пустых мест и нуле
пересечений доказывает сразу три вещи — резерв посчитан, две реализации
прочтения (котлин и Python) совпали, прочих жёстких нет.

Исходы (каждый — с числами из машинного отчёта, не из лога):
  1. нано с пакетом: решение прогона; пустых мест 0; число пересечений
     печатается (ничья «конфликт 1 = экипаже-сутки 1» заявлена разбором);
  2. ИЗОЛЯЦИЯ: тот же ростер оценивается /schedules/score в JVM с
     пакетом и в JVM с копией пакета без правил резерва (вид OUT);
     разница обязана равняться −Σ нехватки оракула по этому ростеру —
     ровно вклад ограничения «Reserve crews», ничего больше;
  3. копия с нормой PLY 2 → 3: тот же ростер под ней строже ровно на
     разницу нехваток оракула (на нано — на единицу);
  4. копия с численностью 3 при наборе из 2 мест: решатель ОТКАЗЫВАЕТ
     до счёта (проверка фактов), текст называет рейс и числа;
  5. ростер с одним опустошённым местом: строже ровно на 30 + 1
     (Unassigned slot + Crew size) — единица принадлежит Crew size;
  6. мини: та же изоляция на решении мини (число печатается, не
     пришпиливается).

НАБОРЫ. Мутация идёт по НЕГАТИВНЫМ фикстурам эталонов
(`*-infeasible-reserve.zip`): в них резерв структурно невыполним при
полном укомплектовании, и вклад ограничения в счёт НЕ равен нулю —
только на таком входе доказательство содержательно. На расширенных
эталонах (экипаж 20 и 32) резерв выполним и вклад равен нулю; они
проверяются приёмкой А на ноль нарушений, а не здесь.

Запуск (из каталога crew-demo, jar собран):
    .venv\\Scripts\\python.exe -m tools.most_v_mutation
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                    # noqa: E402
from tools import most_v_certify_input as ci                   # noqa: E402
from tools import most_v_thresholds as mv                      # noqa: E402
from webapp import reserve_oracle as ro                        # noqa: E402
from webapp import task_package as tp                          # noqa: E402

force_utf8_console()

HARD_RE = re.compile(r"(-?\d+)hard")


def hard_of(score) -> int:
    m = HARD_RE.search(str(score or ""))
    if not m:
        raise RuntimeError("счёт без жёсткой части: %r" % (score,))
    return int(m.group(1))


def mutate_package(src: Path, dst: Path, edit) -> Path:
    """Копия пакета с изменённым model.json и пересчитанной суммой манифеста."""
    with zipfile.ZipFile(src) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    model = json.loads(entries["model.json"].decode("utf-8"))
    edit(model)
    raw = json.dumps(model, ensure_ascii=False, indent=2).encode("utf-8")
    entries["model.json"] = raw
    man = json.loads(entries["manifest.json"].decode("utf-8"))
    for f in man.get("files") or []:
        if f.get("name") == "model.json":
            f["sha256"] = hashlib.sha256(raw).hexdigest()
            f["bytes"] = len(raw)
    entries["manifest.json"] = json.dumps(man, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in entries.items():
            z.writestr(n, b)
    return dst


def run_package(path: Path, label: str):
    """Прогон пакета: решение, отчёт применённого, набор."""
    pkg = tp.load(path, run_gate=False)
    payload = {k: v for k, v in tp.to_payload(pkg).items() if not k.startswith("_")}
    ros = tp.objectives(pkg, "rostering")
    objective_id = str(ros[0]["id"]) if ros else None
    proc = mv.start_jvm(str(path))
    try:
        try:
            solved = ci._solve_full(payload, "optimization", objective_id)
            refusal = None
        except urllib.error.HTTPError as e:
            solved, refusal = None, (e.code, e.read().decode("utf-8", "replace"))
        rep = mv.applied() if solved is not None else None
        scored = None
        if solved is not None:
            # Исход 5: то же решение с одним опустошённым местом — /schedules/score.
            emptied = copy.deepcopy(solved)
            for a in emptied.get("flightAssignments") or []:
                if a.get("employee"):
                    a["employee"] = None
                    break
            scored = (score_of(solved), score_of(emptied))
    finally:
        mv.stop_jvm(proc)
    return pkg, payload, solved, rep, refusal, scored


def score_of(solution: dict) -> str:
    req = urllib.request.Request(
        mv.BASE + "/schedules/score",
        data=json.dumps(solution, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8").strip()


def oracle(label: str, payload: dict, solved: dict, norms: dict) -> int:
    rows = ro.reserve_table(payload, solved, norms)
    short = ro.total_shortfall(rows)
    print("%s: hard %s, нехватка оракула %d (нормы %s), пустых мест %d, пересечений %d"
          % (label, solved.get("score"), short, norms, ro.unassigned_seats(solved),
             ro.overlapping_pairs(payload, solved)))
    for r in rows:
        print("    %s %s: свободно %s → экипажей %d, норма %d, нехватка %d"
              % (r["base"], r["day"], r["free"], r["crews"], r["norm"], r["shortfall"]))
    return short


def score_under(package: Path, solution: dict) -> int:
    """Жёсткая часть счёта ДАННОГО ростера в JVM с ДАННЫМ пакетом."""
    proc = mv.start_jvm(str(package))
    try:
        return hard_of(score_of(solution))
    finally:
        mv.stop_jvm(proc)


def main() -> int:
    if not mv.JAR.exists():
        print("нет сборки решателя: %s" % mv.JAR)
        return 1
    # Негативные фикстуры: резерв невыполним, вклад ограничения не ноль.
    etalons = dict(mv.ETALONS)
    nano = Path(etalons["нано"]).with_name("run-0027-infeasible-reserve.zip")
    mini = Path(etalons["мини"]).with_name("mini-infeasible-reserve.zip")
    for f in (nano, mini):
        if not f.exists():
            print("нет негативной фикстуры: %s" % f)
            return 1
    print("мутация по негативным фикстурам: %s, %s" % (nano.name, mini.name))
    tmp = Path(tempfile.mkdtemp(prefix="most-v-mutation-"))
    checks = 0

    def no_reserve(model):
        for rule in model.get("rules") or []:
            if any(p.get("name") == "reserve_crews" for p in (rule.get("params") or [])):
                rule["kind"] = "OUT"

    def ply3(model):
        n = 0
        for rule in model.get("rules") or []:
            for p in rule.get("params") or []:
                if p.get("name") == "reserve_crews" and str(p.get("qualifier")).strip().upper() == "PLY":
                    p["value"] = 3
                    n += 1
        assert n, "в модели нет нормы PLY"

    def crew3(model):
        for rule in model.get("rules") or []:
            for p in rule.get("params") or []:
                if p.get("name") == "crew_size":
                    p["value"] = 3

    # 1. Нано с пакетом.
    _, payload, solved, rep, refusal, scored = run_package(nano, "нано-негатив")
    assert refusal is None, "негативная фикстура нано отвергнута: %s" % (refusal,)
    norms = ro.norms_from_applied(rep)
    assert norms, "нормы резерва не применены"
    short0 = oracle("1) нано-негатив, пакет как есть", payload, solved, norms)
    hard0 = hard_of(solved.get("score"))
    assert ro.unassigned_seats(solved) == 0, "пустые места: резерв «куплен» отменой рейсов"
    assert short0 > 0, ("на этом наборе нехватки резерва нет — доказательство вклада выродилось "
                        "бы в проверку нуля; мутация обязана идти по негативной фикстуре")
    checks += 1

    # 2. Изоляция: тот же ростер без правил резерва.
    p_off = mutate_package(nano, tmp / "nano-no-reserve.zip", no_reserve)
    hard_off = score_under(p_off, solved)
    print("2) тот же ростер без правил резерва: hard %d; с резервом %d; разница %d; нехватка оракула %d"
          % (hard_off, hard0, hard0 - hard_off, short0))
    assert hard0 - hard_off == -short0, "вклад «Reserve crews» обязан равняться −нехватке оракула"
    assert hard0 == hard_of(scored[0]), "score того же решения %s != hard прогона %d" % (scored[0], hard0)
    checks += 1

    # 3. Норма PLY 2 → 3 на том же ростере.
    p3 = mutate_package(nano, tmp / "nano-ply3.zip", ply3)
    norms3 = dict(norms, PLY=3)
    short3 = ro.total_shortfall(ro.reserve_table(payload, solved, norms3))
    hard3 = score_under(p3, solved)
    print("3) тот же ростер при норме PLY 3: hard %d (было %d), нехватка оракула %d (было %d)"
          % (hard3, hard0, short3, short0))
    assert hard3 - hard_off == -short3, "вклад при норме 3 обязан равняться −нехватке оракула"
    assert hard3 == hard0 - (short3 - short0), "счёт обязан сдвинуться ровно на разницу нехваток"
    # Решение решателя под нормой 3 — печатается для читателя (ничья).
    _, payload3, solved3, rep3, refusal3, _ = run_package(p3, "нано PLY=3")
    assert refusal3 is None, refusal3
    oracle("3а) решение решателя при норме PLY 3", payload3, solved3, ro.norms_from_applied(rep3))
    checks += 1

    # 4. Численность 3 при двух местах на рейс — отказ до счёта.
    pc = mutate_package(nano, tmp / "nano-crew3.zip", crew3)
    _, _, solvedC, _, refusalC, _ = run_package(pc, "нано crew_size=3")
    assert solvedC is None and refusalC is not None, "численность 3 при 2 местах обязана отвергаться"
    code, text = refusalC
    print("4) нано crew_size=3: код %d, причина: %s" % (code, text[:160].replace("\n", " ")))
    assert code == 400 and "мест" in text and "3" in text, text
    checks += 1

    # 5. Опустошённое место — 30 + 1.
    base_hard, emptied_hard = hard_of(scored[0]), hard_of(scored[1])
    print("5) ростер с одним пустым местом: %d hard против %d (разница %d)"
          % (emptied_hard, base_hard, base_hard - emptied_hard))
    assert base_hard - emptied_hard == 30 + 1, "пустое место обязано стоить 30 + 1 (Crew size)"
    checks += 1

    # 6. Мини: изоляция на решении мини.
    _, payloadM, solvedM, repM, refusalM, _ = run_package(mini, "мини")
    assert refusalM is None, refusalM
    normsM = ro.norms_from_applied(repM)
    shortM = oracle("6) мини, пакет как есть", payloadM, solvedM, normsM)
    hardM = hard_of(solvedM.get("score"))
    hardM_off = score_under(mutate_package(mini, tmp / "mini-no-reserve.zip", no_reserve), solvedM)
    print("   мини без правил резерва: hard %d; с резервом %d; разница %d; нехватка оракула %d"
          % (hardM_off, hardM, hardM - hardM_off, shortM))
    assert hardM - hardM_off == -shortM, "мини: вклад «Reserve crews» != −нехватка оракула"
    checks += 1

    # 7. Резерв нельзя «купить» раздвоением человека: при повышенной норме
    # решатель обязан оставить нехватку, а не посадить одного на два
    # одновременных рейса (унаследованная дыра «Flight conflict» закрыта
    # 2026-08-23: цена раздвоения выше любой выгоды от него).
    _, payload7, solved7, rep7, refusal7, _ = run_package(p3, "нано PLY=3 (покупка)")
    assert refusal7 is None, refusal7
    over7 = ro.overlapping_pairs(payload7, solved7)
    short7 = ro.total_shortfall(ro.reserve_table(payload7, solved7, ro.norms_from_applied(rep7)))
    print("7) норма PLY 3: пересечений %d, нехватка %d, счёт %s — раздвоение не куплено"
          % (over7, short7, solved7.get("score")))
    assert over7 == 0, "резерв «куплен» раздвоением: пересечений %d" % over7
    assert ro.unassigned_seats(solved7) == 0, "резерв «куплен» отменой рейсов"
    checks += 1

    print("ИТОГО: исходов проверено %d, все сошлись — исполнители строк 7 и 8 считают ровно то, что заявлено" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
