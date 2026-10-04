# -*- coding: utf-8 -*-
"""Самотест Д1 плана прямого хода (ростеринг): два детерминированных
прогона одного пакета с книгой данных на собранном решателе дают одну
подпись выхода Д2.

Поднимает собранный jar решателя на отдельном порту (9098, как у
tools/most_g_e2e.py — бегущие прогоны на штатных портах не трогает),
дважды вызывает POST /schedules/run с профилем Д1 (deterministic=true,
seed, stepLimit, unimprovedStepLimit), по каждому прогону берёт
решение, отчёт применённого (профиль, weightRules) и разбор счёта под
пакетом прогона, считает подпись выхода (vmp.signatures) и сравнивает.
Печатает карточку: профиль, подписи, подпись задачи, разбор по
ограничениям с ненулевым счётом. Код возврата 0 — подписи совпали.

Запуск (из каталога crew-demo, ПО ОТМАШКЕ ВЛАДЕЛЬЦА — это прогон):
    .venv\\Scripts\\python.exe -m tools.d1_selftest_roster <пакет.zip>
        [--steps 20000] [--unimproved 2000] [--mode optimization]
        [--seed 0] [--port 9098]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from _cli_console import force_utf8_console                    # noqa: E402
from webapp import task_package as tp                          # noqa: E402
from vmp import signatures as sg                               # noqa: E402

force_utf8_console()
JAR = REPO / "kotlin-solver" / "target" / "quarkus-app" / "quarkus-run.jar"


class Solver:
    def __init__(self, port: int, log: Path):
        self.base = "http://127.0.0.1:%d" % port
        self.port, self.log, self.proc = port, log, None

    def req(self, method, path, payload=None, timeout=120):
        data = None if payload is None else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    def start(self):
        if not JAR.is_file():
            raise SystemExit("jar решателя не собран: %s" % JAR)
        env = dict(os.environ)
        env.pop("CREW_TASK_PACKAGE", None)
        self.proc = subprocess.Popen(
            ["java", "-Xms512m", "-Xmx4g", "-Dquarkus.http.port=%d" % self.port,
             "-jar", str(JAR)],
            stdin=subprocess.DEVNULL, stdout=self.log.open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT, env=env, cwd=str(JAR.parent.parent))
        for _ in range(120):
            try:
                urllib.request.urlopen(self.base + "/schedules/applied-rules",
                                       timeout=2).read()
                return
            except Exception:                                   # noqa: BLE001
                if self.proc.poll() is not None:
                    raise SystemExit("JVM умерла при старте; журнал: %s" % self.log)
                time.sleep(1)
        self.proc.kill()
        raise SystemExit("JVM не поднялась за 120 с")

    def stop(self):
        if self.proc is not None:
            self.proc.kill()


def one_run(sv: Solver, payload: dict, zip_b64: str, mode: str,
            objective_id: str | None, seed: int, steps: int, unimproved: int) -> dict:
    q = ("/schedules/run?task=rostering&mode=%s&deterministic=true&seed=%d"
         "&stepLimit=%d&unimprovedStepLimit=%d" % (mode, seed, steps, unimproved))
    if objective_id:
        q += "&objective_id=" + urllib.parse.quote(objective_id)
    code, text = sv.req("POST", q, {"problem": payload, "packageZipBase64": zip_b64},
                        timeout=60)
    if code != 200:
        raise SystemExit("старт прогона: HTTP %d %s" % (code, text[:600]))
    run_id = json.loads(text)["runId"]
    t0 = time.time()
    while True:
        code, text = sv.req("GET", "/schedules/run/%s/status" % run_id)
        st = json.loads(text)
        if st.get("state") != "считает":
            break
        if time.time() - t0 > 3600:
            raise SystemExit("прогон %s не завершился за час" % run_id)
        time.sleep(1)
    if st.get("state") != "готов":
        raise SystemExit("прогон %s: %s" % (run_id, json.dumps(st, ensure_ascii=False)[:600]))
    _, text = sv.req("GET", "/schedules/run/%s/result" % run_id)
    solved = json.loads(text)
    _, text = sv.req("GET", "/schedules/run/%s/applied-rules" % run_id)
    applied = json.loads(text)
    code, text = sv.req("POST", "/schedules/run/%s/score-breakdown" % run_id, solved)
    breakdown = json.loads(text) if code == 200 else {"error": text[:300]}
    canon = sg.canonical_roster(solved, constraints=breakdown.get("constraints"),
                                status=st.get("state"))
    return {"run_id": run_id, "wall_s": round(time.time() - t0, 1), "status": st,
            "applied": applied, "breakdown": breakdown,
            "signature": sg.output_signature(canon), "canon": canon}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--steps", type=int, default=20000)
    ap.add_argument("--unimproved", type=int, default=2000)
    ap.add_argument("--mode", default="optimization")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--port", type=int, default=9098)
    ap.add_argument("--out", default=None, help="куда записать карточку (json)")
    a = ap.parse_args(argv)
    pkg_path = Path(a.package)
    pkg = tp.load(pkg_path, run_gate=False)
    payload = {k: v for k, v in tp.to_payload(pkg).items() if not k.startswith("_")}
    zip_b64 = base64.b64encode(pkg_path.read_bytes()).decode("ascii")
    objs = (pkg["manifest"].get("objectives") or {}).get("rostering") or []
    objective_id = objs[0] if objs and a.mode == "optimization" else None
    print("пакет: %s | режим: %s | целевая: %s | мест: %d | сотрудников: %d"
          % (pkg_path.name, a.mode, objective_id, len(payload["flightAssignments"]),
             len(payload["employees"])))
    sv = Solver(a.port, REPO / "tools" / "_d1_selftest_jvm.log")
    sv.start()
    try:
        runs = [one_run(sv, payload, zip_b64, a.mode, objective_id, a.seed,
                        a.steps, a.unimproved) for _ in range(2)]
    finally:
        sv.stop()
    for i, r in enumerate(runs, 1):
        print("прогон %d: %s | %s с | счёт %s | разбор %s" % (
            i, r["run_id"][:8], r["wall_s"], r["status"].get("score"),
            r["breakdown"].get("score")))
        print("   подпись выхода:", r["signature"]["sha256"],
              "| назначений:", len(r["canon"]["assignments"]),
              "| незанятых:", r["canon"]["unassigned"])
    prof = runs[0]["status"].get("profile") or {}
    print("профиль:", json.dumps(prof, ensure_ascii=False))
    lr = runs[0]["applied"].get("lastRun") or {}
    print("weightRules:", json.dumps(lr.get("weightRules"), ensure_ascii=False))
    print("ограничения с ненулевым счётом (прогон 1):")
    for name, rec in sorted((runs[0]["breakdown"].get("constraints") or {}).items()):
        if rec.get("hard") or rec.get("soft"):
            print("   %-40s hard %5s soft %7s matches %3s"
                  % (name, rec.get("hard"), rec.get("soft"), rec.get("matches")))
    same = runs[0]["signature"] == runs[1]["signature"]
    print("САМОТЕСТ Д1 (ростеринг):", "ПОДПИСИ СОВПАЛИ" if same else "ПОДПИСИ РАЗОШЛИСЬ")
    if not same:
        x, y = runs[0]["canon"], runs[1]["canon"]
        for key in ("score", "status", "unassigned", "constraints"):
            if x[key] != y[key]:
                print("   разница в", key)
        da = set(map(tuple, x["assignments"])) ^ set(map(tuple, y["assignments"]))
        print("   назначений, отличающихся:", len(da))
    ts = sg.task_signature(pkg, prof or None)
    print("подпись задачи:", ts["sha256"])
    card = {"same": same, "task_signature": ts, "profile": prof,
            "runs": [{k: v for k, v in r.items() if k != "canon"} for r in runs]}
    out = Path(a.out) if a.out else REPO / "tools" / "_d1_selftest_roster_result.json"
    out.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    print("карточка:", out)
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
