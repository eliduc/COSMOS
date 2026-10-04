"""Этап Г, Г-Д2 — замер затравки цепочками: прогон с затравкой против без.

Три плеча на одном экземпляре решателя (порт 9099, из уже собранного
архива, без пересборки; бегущие прогоны на штатных портах не трогаются):

  1) «затравка как есть» — прогон с затравкой бюджетом в несколько
     секунд: счёт показывает качество самой затравки до дошлифовки;
  2) «с затравкой» — прогон с затравкой рабочим бюджетом;
  3) «без затравки» — тот же бюджет, той же полезной нагрузкой, но с
     пустыми назначениями.

Пакет и правила не меняются: затравка — заполненный employee в
flightAssignments (начальное решение), см. webapp/chain_seed.py.

Запуск (из каталога crew-demo):
    .venv\\Scripts\\python.exe -m tools.most_gd2_seed_bench [бюджет_с]
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                    # noqa: E402
from tools import most_g_e2e as e2e                            # noqa: E402
from webapp import chain_seed as cs                            # noqa: E402
from webapp import task_package as tp                          # noqa: E402

force_utf8_console()

PKG = Path(r"C:\work\Sandbox\Testing\TimeFold\handoff\2026-08-24-REISSUE-LOGIC") \
    / "full-task-package.zip"


def run_arm(name: str, payload: dict, pkg_b64: str, budget: int) -> dict:
    started = time.monotonic()
    code, text = e2e._post(
        "/schedules/run?mode=feasibility-soft&spentLimitSeconds=%d" % budget,
        {"problem": payload, "packageZipBase64": pkg_b64}, timeout=60)
    if code != 200:
        raise SystemExit("%s: старт прогона HTTP %d %s" % (name, code, text[:300]))
    run_id = json.loads(text)["runId"]
    state = "считает"
    for _ in range(budget * 4 + 240):
        code, text = e2e._get("/schedules/run/%s/status" % run_id)
        state = json.loads(text).get("state") if code == 200 else "?"
        if state != "считает":
            break
        time.sleep(1)
    if state != "готов":
        raise SystemExit("%s: прогон %s в состоянии «%s»" % (name, run_id, state))
    _, text = e2e._get("/schedules/run/%s/result" % run_id, timeout=180)
    score = json.loads(text).get("score")
    took = time.monotonic() - started
    print("%-18s счёт %-28s (%d с бюджета, %.0f с всего)" % (name, score, budget, took))
    return {"arm": name, "runId": run_id, "score": score, "budget": budget}


def main() -> int:
    # Свой порт, чтобы не толкаться с приёмкой Г-S4 на 9098.
    e2e.PORT = 9099
    e2e.BASE = "http://127.0.0.1:%d" % e2e.PORT
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    pkg = tp.load(PKG, run_gate=False)
    payload = {k: v for k, v in tp.to_payload(pkg).items() if not k.startswith("_")}
    # Пороги затравки — из HARD-параметров пакета постановки: своих чисел
    # затравка не держит. Пакет без нужных параметров роняет бенч отказом
    # словами ЗДЕСЬ, до старта JVM, — механизм без данных спит.
    thr = cs.thresholds_from_package(pkg)
    seeded, summary = cs.seed(payload, **thr)
    lines = cs.build_lines(payload["flights"], **thr)
    print("затравка: линий %d, разрывов внутри линий %d, засеяно %d/%d кресел" % (
        summary["lines"], cs.continuity_defects(lines),
        summary["seats_seeded"], summary["seats_total"]))
    pkg_b64 = base64.b64encode(PKG.read_bytes()).decode("ascii")
    proc = e2e.start_jvm()
    results = []
    try:
        results.append(run_arm("затравка как есть", seeded, pkg_b64, 2))
        results.append(run_arm("с затравкой", seeded, pkg_b64, budget))
        results.append(run_arm("без затравки", payload, pkg_b64, budget))
    finally:
        proc.kill()
    out = Path(__file__).with_name("_most_gd2_bench.json")
    out.write_text(json.dumps({"summary": summary, "results": results},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("записано:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
