# -*- coding: utf-8 -*-
"""Самотест Д1 плана «прямой ход» ПУТЁМ КНОПКИ: два прогона обеих задач
(пейринг построителем ortools, ростеринг решателем) на ОДНИХ байтах
пакета и одном профиле обязаны дать тождественный канонический выход.

Отличие от tools/d1_selftest_roster.py (сторона COSMOS): там решатель
проверяется напрямую, здесь — весь путь производителя: экспорт пакета,
гейт пакета, матрица готовности, предпрогонная сверка покрытия,
построитель пейринга, регистрация каталога, счёт ростеринга. Профиль Д1
идёт вглубь: пейрингу — счётный предел и PYTHONHASHSEED=0, ростерингу —
deterministic/seed/stepLimit/unimprovedStepLimit.

Равенство байтов пакета между прогонами обеспечивается package_from:
свежий экспорт несёт created в манифесте и дал бы другой отпечаток при
той же постановке. Второй прогон каждой задачи едет на пакете первого.

Сертификация в самотесте НЕ проводится: она обращается к языковой
модели, а модели в прогонах назначает владелец. Кнопка получает
сертификатор-заглушку, которая так и пишет в сертификате; вердикт
кнопки при этом «недопустимо» — это ожидаемо и о детерминированности
выхода решателей ничего не говорит.

Запуск (из каталога crew-demo, ПО ОТМАШКЕ ВЛАДЕЛЬЦА — это прогон):
    .venv\\Scripts\\python.exe -m tools.d1_selftest_button <каталог прогона>
        [--port 9099] [--pairing-time 60] [--steps 20000]
        [--unimproved 2000] [--seed 0] [--out карточка.json]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from _cli_console import force_utf8_console                    # noqa: E402
from vmp.run import button as btn                                 # noqa: E402
from vmp.run import selftest_d1 as st1                            # noqa: E402

force_utf8_console()
JAR = REPO / "kotlin-solver" / "target" / "quarkus-app" / "quarkus-run.jar"

#: Сертификат самотеста: канал компилятора не подключён — так и
#: записано. Подделкой сертификата это не является: поле certified
#: ложно, причина названа словами.
CERT_NOTE = ("самотест Д1: канал компилятора не подключён по условиям "
             "самотеста (модели в прогонах назначает владелец) — "
             "сертификация против свода НЕ проводилась")


def _stub_certificate(svod, *_a, **_kw) -> dict:
    """Отчёт-заглушка формы сертификата: ни одной прогнанной проверки,
    признак certified ложен, причина названа словами. Кнопка его
    рендерит и кладёт рядом с прогоном — прочитавший видит, что
    сертификации не было."""
    customer = str((svod.get("meta") or {}).get("customer") or "?") \
        if isinstance(svod, dict) else "?"
    return {"customer": customer, "sources": [], "checks": [],
            "findings": [], "generated_by": "tools/d1_selftest_button.py",
            "certificate": {"certified": False, "note": CERT_NOTE,
                            "findings": [], "selftest_stub": True}}


class Jvm:
    """Решатель поднимается на отдельном порту: бегущие прогоны на
    штатных портах самотест не трогает."""

    def __init__(self, port: int, log: Path, copy_to: Path | None = None):
        self.base = "http://127.0.0.1:%d" % port
        self.port, self.log, self.proc = port, log, None
        # Решатель поднимается из КОПИИ каталога quarkus-app: сборка
        # соседней сессии и бегущий прогон не должны делить один
        # target — сборка 03.09 удалила lib-jar под работающей JVM
        # (NoSuchFileException на quarkus-vertx-http), и ростеринг
        # отказал «Internal Server Error» посреди сертификации.
        self.copy_to = copy_to

    def start(self):
        if not JAR.is_file():
            raise SystemExit("jar решателя не собран: %s" % JAR)
        jar = JAR
        if self.copy_to is not None:
            import shutil
            import time as _t
            root = Path(self.copy_to)
            # Каждый подъём — в СВЕЖИЙ каталог: прежнюю копию может ещё
            # держать умирающая JVM (Windows: «Access is denied» на
            # lib/boot при rmtree — живой отказ 03.09), и удаление
            # старых копий — только по возможности, не условие старта.
            for old in sorted(root.glob("quarkus-app*")) if root.exists() else []:
                try:
                    shutil.rmtree(old)
                except OSError:
                    pass
            dst = root / ("quarkus-app-%d" % int(_t.time()))
            shutil.copytree(JAR.parent, dst)
            jar = dst / JAR.name
        env = dict(os.environ)
        env.pop("CREW_TASK_PACKAGE", None)
        self.proc = subprocess.Popen(
            ["java", "-Xms512m", "-Xmx4g",
             "-Dquarkus.http.port=%d" % self.port, "-jar", str(jar)],
            stdin=subprocess.DEVNULL,
            stdout=self.log.open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT, env=env, cwd=str(jar.parent.parent))
        for _ in range(120):
            try:
                urllib.request.urlopen(
                    self.base + "/schedules/applied-rules", timeout=2).read()
                return
            except Exception:                                  # noqa: BLE001
                if self.proc.poll() is not None:
                    raise SystemExit("JVM умерла при старте; журнал: %s"
                                     % self.log)
                time.sleep(1)
        self.proc.kill()
        raise SystemExit("JVM не поднялась за 120 с")

    def stop(self):
        if self.proc is not None:
            self.proc.kill()


def _press(run_dir: Path, client, task: str, out_dir: str,
           package_from: str | None, profile: dict, label: str) -> dict:
    """Одно нажатие кнопки в профиле Д1. Возврат — доставка кнопки."""
    kw: dict = {"task": task, "out_dir": out_dir,
                "package_from": package_from,
                "deterministic": profile["deterministic"]}
    if task == "pairing":
        kw["pairing_builder"] = btn.subprocess_pairing_builder(
            label, deterministic=True)
    else:
        kw["certify_fn"] = _stub_certificate
        kw["spent_limit_s"] = profile["spent_limit_s"]
    def _log(step, status, detail):
        print("   %-6s %-28s %s" % (step, status, str(detail)[:110]),
              flush=True)
    return btn.orchestrate(run_dir, client, log=_log, **kw)


def _outcome(run_dir: Path, client, task: str, out_dir: str,
             delivery: dict) -> dict:
    """Канонический выход нажатия для подписи Д2.

    Пейринг — каталог построителя (байты, по которым считался sha и
    которые зарегистрированы у решателя); ростеринг — решение прогона
    с разбором счёта под пакетом."""
    if task == "pairing":
        cat = run_dir / out_dir / "builder-catalog.json"
        if not cat.is_file():
            return {}
        catalog = json.loads(cat.read_text(encoding="utf-8"))
        return {"catalog": catalog,
                "status": delivery.get("coverage_verdict"),
                # Происхождение границы «стыковка или отдых»: карточка
                # обязана показывать, из какого пола отдыха ВМП взят
                # верх окна, иначе прочитавший её этого не узнает.
                # В подпись выхода поле не входит (канон Д2 берёт
                # цепочки, цены и покрытие).
                "sit_window": catalog.get("sit_window")}
    run_id = ((delivery.get("schedule") or {}).get("solver_run_id"))
    if not run_id:
        return {}
    solved = client.result(run_id)
    breakdown = client.score_breakdown(run_id, solved)
    status = client.status(run_id)
    return {"solved": solved, "constraints": breakdown.get("constraints"),
            "status": status.get("state"),
            "score": (status.get("score")
                      or (solved.get("runInfo") or {}).get("score")),
            "breakdown": breakdown}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", help="каталог прогона CDPM (с extract/model.json)")
    ap.add_argument("--port", type=int, default=9099)
    ap.add_argument("--pairing-time", type=int, default=60,
                    help="счётный предел построителя пейринга, секунд")
    ap.add_argument("--steps", type=int, default=20000)
    ap.add_argument("--unimproved", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--spent-limit", type=int, default=60)
    ap.add_argument("--registry", default=None,
                    help="готовый реестр исполнителей; без него реестр "
                         "выводится из модели прогона и базы шаблонов")
    ap.add_argument("--out", default=None, help="куда записать карточку (json)")
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir).resolve()
    model_path = run_dir / "extract" / "model.json"
    if not model_path.is_file():
        raise SystemExit("в каталоге прогона нет extract/model.json: %s" % run_dir)
    # Реестр исполнителей — рубеж «100% соответствия ВМП шаблонам»
    # (поручение владельца 2026-08-28). Он ВЫВОДИТСЯ машинно из модели
    # прогона и базы шаблонов (P-N-SHD), а не издаётся рукой: правило
    # без посадки и без машинной механики даст вердикт-пробел, и кнопка
    # честно встанет на Ш-2м. Готовый реестр можно подать ключом.
    if a.registry:
        os.environ["CREW_VMP_REGISTRY"] = str(Path(a.registry).resolve())
    else:
        from vmp import registry_derive as rd
        from vmp import registry_check as rc
        model = json.loads(model_path.read_text(encoding="utf-8"))
        reg_path = run_dir / "registry" / "vmp-executors-registry.json"
        reg = rd.derive_and_write(model, REPO, reg_path,
                                  scope="самотест Д1 путём кнопки: %s"
                                        % run_dir.name)
        gaps = [r for r in reg["rules"]
                if r.get("verdict") not in rc.FINAL_VERDICTS]
        print("реестр исполнителей выведен: правил %d, пробелов %d — %s"
              % (len(reg["rules"]), len(gaps), reg_path), flush=True)
        for g in gaps:
            print("   пробел: %s — %s" % (g["rule_id"],
                                          str(g.get("gap_note"))[:100]))
        os.environ["CREW_VMP_REGISTRY"] = str(reg_path)
    profiles = {
        "pairing": {"deterministic": {"deterministic_time": a.pairing_time},
                    "spent_limit_s": a.spent_limit,
                    "stamp": "пейринг: счётный предел %d с, PYTHONHASHSEED=0, "
                             "один поток, без presolve" % a.pairing_time},
        "rostering": {"deterministic": {"seed": a.seed,
                                        "step_limit": a.steps,
                                        "unimproved_step_limit": a.unimproved},
                      "spent_limit_s": a.spent_limit,
                      "stamp": "ростеринг: deterministic, seed %d, шагов %d, "
                               "без улучшения %d" % (a.seed, a.steps,
                                                     a.unimproved)},
    }
    jvm = Jvm(a.port, REPO / "tools" / "_d1_selftest_button_jvm.log")
    print("решатель: поднимаю jar на порту %d" % a.port, flush=True)
    jvm.start()
    client = btn.SolverHttp(jvm.base)
    results: dict = {"pairing": [], "rostering": []}
    deliveries: dict = {"pairing": [], "rostering": []}
    task_signatures: dict = {}
    try:
        for task in ("pairing", "rostering"):
            for i, name in enumerate(("a", "b")):
                out_dir = "button-d1-%s-%s" % (task, name)
                src = ("button-d1-%s-a" % task) if i else None
                print("\n%s, прогон %s (каталог %s)"
                      % (task, name, out_dir), flush=True)
                t0 = time.time()
                delivery = _press(run_dir, client, task, out_dir, src,
                                  profiles[task], "d1-%s-%s" % (task, name))
                wall = round(time.time() - t0, 1)
                deliveries[task].append(delivery)
                print("   вердикт: %s (%.1f с)"
                      % (delivery.get("verdict"), wall), flush=True)
                out = _outcome(run_dir, client, task, out_dir, delivery)
                if not out:
                    results[task].append({"note": "выхода нет: %s"
                                                  % delivery.get("verdict")})
                    continue
                sig = st1.run_signature(task, out)
                sig["wall_s"] = wall
                sig["verdict"] = delivery.get("verdict")
                sig["score"] = out.get("score")
                if out.get("sit_window") is not None:
                    sig["sit_window"] = out["sit_window"]
                if delivery.get("chains") is not None:
                    sig["chains"] = delivery["chains"]
                results[task].append(sig)
                fp = (delivery.get("trace") or {}).get("package_fingerprint")
                if fp:
                    task_signatures.setdefault(task, {})["package_fingerprint"] = fp
    finally:
        jvm.stop()
    card = st1.selftest_card(results, profiles=profiles,
                             task_signatures=task_signatures)
    card["run_dir"] = str(run_dir)
    card["deliveries"] = {t: [{"verdict": d.get("verdict"),
                               "chains": d.get("chains"),
                               "solver_run_id": (d.get("schedule") or {})
                               .get("solver_run_id"),
                               "score": (d.get("schedule") or {}).get("score")}
                              for d in ds] for t, ds in deliveries.items()}
    card["certificate_note"] = CERT_NOTE
    print("\n" + st1.render(card))
    for task, ds in deliveries.items():
        for d in ds:
            sch = d.get("schedule") or {}
            if sch.get("score"):
                print("  %s: счёт %s" % (task, sch["score"]))
    for sig in results.get("pairing") or ():
        if sig.get("sit_window"):
            print("  пейринг, окно стыковки: %s"
                  % json.dumps(sig["sit_window"], ensure_ascii=False))
            break
    if a.out:
        Path(a.out).write_text(json.dumps(card, ensure_ascii=False, indent=1)
                               + "\n", encoding="utf-8")
        print("карточка: %s" % a.out)
    return 0 if card.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
