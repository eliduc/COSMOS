"""Repro farm for the AbstractIfExistsNode.doRetractCounter AIOOBE.

Mirrors the original crash environment exactly:
  - same quarkus-run.jar, same JVM flags as _compr_isolated.IsolatedJvmFleet
  - same HTTP POST /schedules/single?acceptor=LA&randomSeed=7&rules=GERAD3
  - fresh JVM per attempt (the original crash was on a fresh JVM ~313s in,
    right at the Construction Heuristic -> Local Search boundary)

On a 500 response: saves the JVM log, then re-POSTs twice on the SAME (warm)
JVM to distinguish a sticky JIT miscompilation from a one-off corruption.

Usage: python farm.py <port> <n_attempts> <spent_limit_s> <seed>
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

HERE = Path(__file__).parent
JAR = Path(r"C:\work\Sandbox\Testing\TimeFold\crew-demo\kotlin-solver\target\quarkus-app\quarkus-run.jar")
JAVA = r"C:\Program Files\Eclipse Adoptium\jdk-21.0.11.10-hotspot\bin\java.exe"
PAYLOAD = (HERE / "inst4_p2.json").read_bytes()

port = int(sys.argv[1])
n_attempts = int(sys.argv[2])
spent_limit = int(sys.argv[3])
seed = int(sys.argv[4])

LOGS = HERE / "logs"
LOGS.mkdir(exist_ok=True)


def ts():
    return time.strftime("%H:%M:%S")


def wait_ready(port, timeout_s=90):
    deadline = time.time() + timeout_s
    url = f"http://127.0.0.1:{port}/q/openapi"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def solve(port, spent, seed, timeout):
    url = (f"http://127.0.0.1:{port}/schedules/single"
           f"?acceptor=LA&spentLimitSeconds={spent}&randomSeed={seed}&rules=GERAD3")
    req = urllib.request.Request(url, data=PAYLOAD,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            score = json.loads(body).get("score")
            return ("OK", score, time.time() - t0)
    except urllib.error.HTTPError as e:
        return ("HTTP_%d" % e.code, None, time.time() - t0)
    except Exception as e:
        return ("EXC:" + type(e).__name__, None, time.time() - t0)


for attempt in range(1, n_attempts + 1):
    log_path = LOGS / f"jvm_p{port}_a{attempt:03d}.log"
    log_f = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [JAVA, "-Xms512m", "-Xmx8g", "-XX:+UseG1GC",
         f"-Dquarkus.http.port={port}",
         '-Dquarkus.log.category."org.jboss.resteasy.reactive".level=DEBUG',
         "-Dquarkus.http.limits.max-body-size=64M",
         "-Dquarkus.http.idle-timeout=12H",
         "-Dquarkus.timefold.solver.environment-mode=no-assert",
         "-jar", str(JAR)],
        stdin=subprocess.DEVNULL, stdout=log_f, stderr=subprocess.STDOUT,
        cwd=str(JAR.parent.parent),
    )
    try:
        if not wait_ready(port):
            print(f"[{ts()}] p{port} a{attempt}: JVM not ready, skipping", flush=True)
            continue
        status, score, dur = solve(port, spent_limit, seed, timeout=spent_limit + 600)
        print(f"[{ts()}] p{port} a{attempt}: {status} score={score} dur={dur:.0f}s",
              flush=True)
        if status.startswith("HTTP_5"):
            print(f"[{ts()}] p{port} a{attempt}: *** SERVER ERROR — retrying twice "
                  f"on the same warm JVM ***", flush=True)
            for retry in (1, 2):
                r_status, r_score, r_dur = solve(port, spent_limit, seed,
                                                 timeout=spent_limit + 600)
                print(f"[{ts()}] p{port} a{attempt} warm-retry {retry}: {r_status} "
                      f"score={r_score} dur={r_dur:.0f}s", flush=True)
            log_f.flush()
            shutil.copy(log_path, LOGS / f"CRASH_p{port}_a{attempt:03d}.log")
            print(f"[{ts()}] p{port} a{attempt}: log saved as CRASH_*.log — farm lane "
                  f"stops here for analysis", flush=True)
            break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_f.close()
        # keep logs small: clean runs' logs are not interesting beyond the tail
        if not (LOGS / f"CRASH_p{port}_a{attempt:03d}.log").exists():
            try:
                txt = log_path.read_text(encoding="utf-8", errors="replace")
                if len(txt) > 20000:
                    log_path.write_text(txt[:4000] + "\n...[truncated]...\n" + txt[-8000:],
                                        encoding="utf-8")
            except OSError:
                pass
    time.sleep(3)

print(f"[{ts()}] p{port}: lane finished", flush=True)
