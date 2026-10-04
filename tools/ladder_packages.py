# -*- coding: utf-8 -*-
"""Свежие пакеты постановки для лестницы «Полярис» (нано, мини, Лайт,
полный) текущим кодом CDPM — под стенд движков отбора (CPLEX по всем
наборам, отмашка владельца 04.09.2026 в сессии CDPM).

Путь один и тот же для каждого комплекта, ШТАТНЫМИ ручками панели CDPM
(служба по --panel; в процессе панель не поднимается):
  создание прогона (документы комплекта, класс данных «синтетический»,
  облако для изображений) → политика файлов «облако» → извлечение →
  сквозной аудит-цикл → анкета по МАШИННОЙ книге кругами → книга
  данных → проверки данных → экспорт пакета постановки → гейт
  производителя → копия в каталог стенда.

Машинная книга ответов: у нано — etalon/ANSWER-BOOK-MACHINE.json; у
остальных выводится (derive_book) из ответов прежних прогонов того же
комплекта (точные id вопросов) + общих правил нано (вопросы «нет
данных», спорное чтение, канон 12:00/13:00) и сохраняется рядом с
книгой для человека. Вопрос без ответа в книге — честная остановка с
перечнем (дописать книгу, продолжить --run <id> --from-stage answers).

    python -m tools.ladder_packages --kit nano --panel http://127.0.0.1:8124
        [--run run-0001 --from-stage answers] [--rebuild-book]
        [--out <каталог отчёта>] [--bench-dir <куда класть zip>]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
from _cli_console import force_utf8_console  # noqa: E402
import nano_regression as nr  # noqa: E402  (http-помощники, fill_book, _poll)

force_utf8_console()
BENCH = REPO.parent / "crew-demo-data" / "bench-cplex"
# Анкета отвечается ДВАЖДЫ: до данных (вопросы извлечения и аудита) и после
# отчёта данных (вопросы «данных для правила нет в книге» рождаются
# проверками данных и до них не существуют).
STAGES = ("create", "extract", "cycle", "answers", "data", "answers2", "package")

KITS = {
    # Состав документов — как у прежних прогонов комплектов (run-0068,
    # run-0045, run-0040, run-0047): правила docx + журнал xlsx + памятка
    # jpg; книга данных — на стадии данных.
    "nano": {"dir": REPO / "open-bench" / "polyaris-nano",
             "docs": ("PNI1-Pravila-Polyaris.docx", "PNE1-Zhurnal-Polyaris.xlsx",
                      "PNC1-Pamyatka-Polyaris.jpg"),
             "data": "POLYARIS-DATA.xlsx", "customer": "Полярис-нано (стенд движков)",
             "old_runs": ()},
    "mini": {"dir": REPO / "open-bench" / "polyaris-mini",
             "docs": ("PMI1-Pravila-Polyaris.docx", "PME1-Zhurnal-Polyaris.xlsx",
                      "PMC1-Pamyatka-Polyaris.jpg"),
             "data": "POLYARIS-DATA.xlsx", "customer": "Полярис-мини (стенд движков)",
             "old_runs": ("run-0045",)},
    "light": {"dir": REPO / "open-bench" / "polyaris-light",
              "docs": ("PI1-Pravila-Polyaris.docx", "PE1-Zhurnal-Polyaris.xlsx",
                       "PR1-Pamyatka-Polyaris.jpg"),
              "data": "POLYARIS-DATA.xlsx", "customer": "Полярис-Лайт (стенд движков)",
              "old_runs": ("run-0062", "run-0040", "run-0039")},
    "full": {"dir": REPO / "open-bench" / "polyaris",
             "docs": ("PI1-Pravila-Polyaris.docx", "PE1-Zhurnal-Polyaris.xlsx",
                      "PR1-Pamyatka-Polyaris.jpg"),
             "data": "POLYARIS-DATA.xlsx", "customer": "Полярис (полный, стенд движков)",
             "old_runs": ("run-0047",)},
}


def _log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ── машинная книга ответов ──
def derive_book(kit: str, old_runs_root: Path) -> dict:
    """Книга для комплекта: точные ответы прежних прогонов по id вопроса
    + регэкспы по заголовку вопроса + общие правила нано."""
    nano = json.loads((KITS["nano"]["dir"] / "etalon" / "ANSWER-BOOK-MACHINE.json")
                      .read_text(encoding="utf-8"))
    book = {"form_version": 1, "customer": KITS[kit]["customer"],
            "_comment": "Выведена tools/ladder_packages.derive_book (04.09.2026): exact — "
                        "ответы прежних прогонов комплекта по id вопроса; regex — по "
                        "заголовку вопроса прежних прогонов и общие правила нано "
                        "(«нет данных» → post-check, спорное чтение → аудитор, канон "
                        "12:00/13:00). Стенд движков, не эталон.",
            "exact": {}, "prefix": dict(nano.get("prefix") or {}), "regex": []}
    seen_titles = set()
    for run in KITS[kit]["old_runs"]:
        mp = old_runs_root / run / "extract" / "model.json"
        if not mp.is_file():
            continue
        m = json.loads(mp.read_text(encoding="utf-8"))
        for q in m.get("questions") or []:
            raw = str((q.get("answer") or {}).get("raw") or "").strip()
            if not raw or not q.get("id"):
                continue
            if str(q["id"]).startswith(("Q-3a-нет-данных-", "Q-3b-нет-данных-")):
                # Форма вопроса «нет данных» изменилась 03.09 (а — считать
                # без данных с исключением, б — ждать данных): старые
                # ответы «б» означали другое; такие вопросы закрывает
                # prefix-правило нано, не точные ответы прежних прогонов.
                continue
            book["exact"].setdefault(str(q["id"]), raw)
            title = str(q.get("title") or "").strip()
            if title and title not in seen_titles and len(title) >= 12:
                seen_titles.add(title)
                book["regex"].append({"pattern": re.escape(title), "answer": raw,
                                      "note": f"по заголовку вопроса {run}/{q['id']}"})
    # Общие правила нано — после частных (порядок regex важен).
    for r in nano.get("regex") or []:
        book["regex"].append(dict(r, note="общее правило нано: " + str(r.get("note") or "")))
    return book


def load_book(kit: str, rebuild: bool, old_runs_root: Path) -> tuple[dict, Path]:
    path = KITS[kit]["dir"] / "etalon" / "ANSWER-BOOK-MACHINE.json"
    if kit == "nano" or (path.is_file() and not rebuild):
        return json.loads(path.read_text(encoding="utf-8")), path
    book = derive_book(kit, old_runs_root)
    path.write_text(json.dumps(book, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    _log("машинная книга %s выведена: exact %d, regex %d → %s"
         % (kit, len(book["exact"]), len(book["regex"]), path))
    return book, path


# ── прогон ──
class Ladder:
    def __init__(self, kit: str, panel: str, out: Path, max_rounds: int):
        self.kit = kit
        self.cfg = KITS[kit]
        self.panel = panel.rstrip("/")
        self.out = out
        self.max_rounds = max_rounds
        self.run_id: str | None = None
        self.report: dict = {"kit": kit, "panel": panel, "stages": {},
                             "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.report_path = out / f"ladder-{kit}-report.json"

    def save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(self.report, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")

    def status(self) -> dict:
        return nr.http_get(f"{self.panel}/api/runs/{self.run_id}?scope=any", timeout=120).json()

    def state(self) -> str:
        return str(self.status().get("state") or "")

    def data_state(self) -> str:
        return str(self.status().get("data_state") or "")

    def _stage(self, name: str, fn) -> None:
        t0 = time.time()
        _log("%s: %s…" % (self.kit, name))
        try:
            res = fn()
        except Exception as e:                            # noqa: BLE001
            self.report["stages"][name] = {"status": "отказ", "error": str(e)[:600],
                                           "s": round(time.time() - t0)}
            self.save()
            raise
        self.report["stages"][name] = {"status": "ok", "s": round(time.time() - t0),
                                       **(res or {})}
        self.save()
        _log("%s: %s — ok, %d с" % (self.kit, name, time.time() - t0))

    def st_create(self) -> dict:
        files = []
        for n in self.cfg["docs"]:
            p = (self.cfg["dir"] / "data" / n) if n == self.cfg["data"] else self.cfg["dir"] / "docs" / n
            if not p.is_file():
                raise nr.RegressError(f"нет входного файла: {p}")
            files.append(("files", (n, p.read_bytes())))
        r = nr.http_post_files(
            f"{self.panel}/api/runs?cloud_images=true&data_class=synthetic"
            f"&customer={quote(self.cfg['customer'])}", files, timeout=300)
        if r.status_code != 200:
            raise nr.RegressError(f"создание прогона: {r.status_code} {r.text[:300]}")
        self.run_id = r.json()["run"]
        self.report["run"] = self.run_id
        cfg = {n: {"processing": "cloud"} for n in self.cfg["docs"]}
        r = nr.http_post_json(f"{self.panel}/api/runs/{self.run_id}/files-config?scope=any",
                              {"files": cfg}, timeout=60, method="PUT")
        if r.status_code != 200:
            raise nr.RegressError(f"политика файлов: {r.status_code} {r.text[:200]}")
        return {"run": self.run_id, "files": list(self.cfg["docs"])}

    def st_extract(self) -> dict:
        r = nr.http_post_json(f"{self.panel}/api/runs/{self.run_id}/extract?scope=any", timeout=60)
        if r.status_code != 200:
            raise nr.RegressError(f"извлечение: {r.status_code} {r.text[:300]}")
        nr._poll(self.state, want="извлечено", timeout_s=5400)
        st = self.status()
        return {"rules": (st.get("extract_summary") or {}).get("rules"),
                "questions": (st.get("readiness") or {}).get("vmp_open_questions")}

    def st_cycle(self) -> dict:
        r = nr.http_post_json(f"{self.panel}/api/runs/{self.run_id}/full-cycle?scope=any", timeout=60)
        if r.status_code != 200:
            raise nr.RegressError(f"цикл: {r.status_code} {r.text[:300]}")
        last = nr._poll(self.state, timeout_s=10800, still=("идёт",))
        return {"state": last}

    def st_answers(self, book: dict, run_dir: Path) -> dict:
        rounds = []
        for rnd in range(1, self.max_rounds + 1):
            st = self.status()
            open_q = (st.get("readiness") or {}).get("vmp_open_questions")
            if not open_q:
                break
            # Анкета рендерится из КАНОНИЧЕСКОЙ модели прогона тем же кодом,
            # что у панели (после аудит-цикла файла extract/anketa.xlsx нет —
            # панель отдаёт анкету версией обмена).
            model_path = run_dir / "extract" / "model.json"
            if not model_path.is_file():
                raise nr.RegressError(f"нет модели прогона {model_path}")
            from cdpm import anketa_xlsx as anketa_mod
            anketa = self.out / f"{self.kit}-anketa-round-{rnd}.xlsx"
            anketa_mod.render_anketa_xlsx(
                json.loads(model_path.read_text(encoding="utf-8")), anketa)
            filled, unmatched = nr.fill_book(anketa, book, self.out / f"{self.kit}-book-round-{rnd}.xlsx")
            if unmatched:
                (self.out / f"{self.kit}-unmatched-round-{rnd}.txt").write_text(
                    "\n".join(unmatched) + "\n", encoding="utf-8")
                raise nr.RegressError("вопросы без ответа в машинной книге (%d): %s"
                                      % (len(unmatched), "; ".join(u[:100] for u in unmatched[:6])))
            if not filled:
                raise nr.RegressError(f"открытых {open_q}, но заполнять нечего")
            blob = (self.out / f"{self.kit}-book-round-{rnd}.xlsx").read_bytes()
            r = nr.http_post_files(f"{self.panel}/api/runs/{self.run_id}/answers?scope=any",
                                   [("file", (f"book-{rnd}.xlsx", blob))], timeout=300)
            if r.status_code != 200:
                raise nr.RegressError(f"подача книги: {r.status_code} {r.text[:300]}")
            nr._poll(self.state, want="применены", timeout_s=3600)
            rounds.append({"round": rnd, "filled": filled, "open_before": open_q})
            _log("%s: круг %d — подано %d (открытых было %s)" % (self.kit, rnd, filled, open_q))
        else:
            raise nr.RegressError(f"анкета не закрылась за {self.max_rounds} кругов")
        return {"rounds": rounds, "open_after": (self.status().get("readiness") or {}).get("vmp_open_questions")}

    def st_data(self) -> dict:
        p = self.cfg["dir"] / "data" / self.cfg["data"]
        if not p.is_file():
            raise nr.RegressError(f"нет книги данных: {p}")
        r = nr.http_post_files(f"{self.panel}/api/runs/{self.run_id}/data",
                               [("files", (self.cfg["data"], p.read_bytes()))], timeout=300)
        if r.status_code != 200:
            raise nr.RegressError(f"данные: {r.status_code} {r.text[:300]}")
        names = list(self.cfg["docs"]) + [self.cfg["data"]]
        cfg_body = {"files": {n: {"processing": "cloud"} for n in names}}
        for attempt in range(1, 4):
            r = nr.http_post_json(f"{self.panel}/api/runs/{self.run_id}/files-config?scope=any",
                                  cfg_body, timeout=60, method="PUT")
            got = {}
            if r.status_code == 200:
                got = (nr.http_get(f"{self.panel}/api/runs/{self.run_id}/files-config", timeout=60)
                       .json().get("files") or {})
            bad = [n for n in names if (got.get(n) or {}).get("processing") != "cloud"]
            if r.status_code == 200 and not bad:
                break
            time.sleep(5)
        else:
            raise nr.RegressError(f"политика файлов данных не встала в «облако»: {bad}")
        r = nr.http_post_json(f"{self.panel}/api/runs/{self.run_id}/data-run?scope=any", timeout=60)
        if r.status_code != 200:
            raise nr.RegressError(f"проверки данных: {r.status_code} {r.text[:300]}")
        last = nr._poll(self.data_state, want="готов", timeout_s=3600)
        return {"data_state": last}

    def st_package(self, bench_dir: Path) -> dict:
        r = nr.http_get(f"{self.panel}/api/runs/{self.run_id}/export/task-package?scope=any",
                        timeout=300)
        if r.status_code != 200:
            raise nr.RegressError(f"пакет: {r.status_code} {r.text[:400]}")
        body = r.text.encode("utf-8", "surrogateescape") if False else None
        # HttpReply хранит текст; для zip нужен сырой ответ — читаем заново байтами.
        import urllib.request
        with urllib.request.urlopen(
                f"{self.panel}/api/runs/{self.run_id}/export/task-package?scope=any",
                timeout=300) as resp:
            body = resp.read()
        pkg = self.out / f"polaris-{self.kit}.zip"
        pkg.write_bytes(body)
        from cdpm.gates import gate_task_package as gate
        problems, counters = gate.check(pkg)
        import io
        import zipfile
        man = json.loads(zipfile.ZipFile(io.BytesIO(body)).read("manifest.json"))
        res = {"path": str(pkg), "bytes": len(body), "spec_version": man.get("spec_version"),
               "kit": man.get("kit"), "allowed_modes": man.get("allowed_modes"),
               "readiness": {k: (v or {}).get("state") for k, v in
                             ((man.get("readiness_matrix") or {}).get("columns") or {}).items()},
               "gate_problems": problems, "counters": counters}
        if problems:
            raise nr.RegressError("гейт пакета красный (%d): %s" % (len(problems), problems[0][:200]))
        bench_dir.mkdir(parents=True, exist_ok=True)
        dst = bench_dir / f"polaris-{self.kit}.zip"
        shutil.copyfile(pkg, dst)
        res["bench_path"] = str(dst)
        return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", required=True, choices=sorted(KITS))
    ap.add_argument("--panel", default="http://127.0.0.1:8124")
    ap.add_argument("--runs-dir", default=None,
                    help="каталог прогонов службы панели (умолчание — CDPM_RUNS_DIR службы: "
                         "<data_root>/bench-cplex/cdpm-runs)")
    ap.add_argument("--old-runs-root", default=str(REPO.parent / "crew-demo-data" / "cdpm-runs"))
    ap.add_argument("--run", default=None, help="продолжить существующий прогон")
    ap.add_argument("--from-stage", choices=STAGES, default=None)
    ap.add_argument("--max-rounds", type=int, default=8)
    ap.add_argument("--rebuild-book", action="store_true")
    ap.add_argument("--out", default=str(BENCH / "ladder-runs"))
    ap.add_argument("--bench-dir", default=str(BENCH / "packages"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    runs_dir = Path(a.runs_dir) if a.runs_dir else BENCH / "cdpm-runs"
    book, book_path = load_book(a.kit, a.rebuild_book, Path(a.old_runs_root))
    lad = Ladder(a.kit, a.panel, out, a.max_rounds)
    lad.report["book"] = str(book_path)
    start = STAGES.index(a.from_stage) if a.from_stage else 0
    if a.run:
        lad.run_id = a.run
        lad.report["run"] = a.run
        if start == 0:
            start = 1
    elif start > 0:
        raise SystemExit("--from-stage требует --run")
    try:
        if start <= 0:
            lad._stage("create", lad.st_create)
        run_dir = runs_dir / lad.run_id
        if start <= 1:
            lad._stage("extract", lad.st_extract)
        if start <= 2:
            lad._stage("cycle", lad.st_cycle)
        if start <= 3:
            lad._stage("answers", lambda: lad.st_answers(book, run_dir))
        if start <= 4:
            lad._stage("data", lad.st_data)
        if start <= 5:
            lad._stage("answers2", lambda: lad.st_answers(book, run_dir))
        if start <= 6:
            lad._stage("package", lambda: lad.st_package(Path(a.bench_dir)))
    except nr.RegressError as e:
        _log("СТОП %s: %s" % (a.kit, e))
        lad.report["stop"] = str(e)
        lad.save()
        return 1
    lad.report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    lad.report["passed"] = True
    lad.save()
    _log("%s: пакет готов — %s" % (a.kit, lad.report["stages"]["package"].get("bench_path")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
