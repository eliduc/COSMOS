# -*- coding: utf-8 -*-
"""Автопрогон регрессии «Полярис-нано» полным циклом (пирамида, п.3).

Решение владельца 2026-08-31: ежедневная регрессия конвейера — нано
от документов до ФПР всеми LLM-точками, без человека. Оркестратор
гоняет ШТАТНЫЕ ручки панели CDPM (до пакета) и витрины COSMOS
(расчёт по пакету, решение владельца 04.09) на локальной
машине площадки (никаких обходов конвейера), отвечает на анкету по
машинной книге эталона (open-bench/polyaris-nano/etalon/
ANSWER-BOOK-MACHINE.json), сравнивает свод с золотым эталоном М0
(vmp/golden_diff) и пишет отчёт с хронометражем этапов.

Fail-closed: несопоставленный вопрос анкеты, красный гейт, отказ
стадии или РЕД-дифф — честный красный отчёт, автопрогон ничего не
выдумывает и не чинит.

Запуск (на площадке):
    python tools/nano_regression.py [--panel http://127.0.0.1:8097]
        [--webapp http://127.0.0.1:8090] [--out-dir DIR]
        [--max-answer-rounds 6]
Код возврата: 0 = GREEN/AMBER, 1 = RED/отказ.
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

NANO = HERE / "open-bench" / "polyaris-nano"
DOC_NAMES = ("PNI1-Pravila-Polyaris.docx", "POLYARIS-DATA.xlsx",
             "PNC1-Pamyatka-Polyaris.jpg")
DATA_NAME = "POLYARIS-DATA.xlsx"

TERMINAL_BAD = ("отказ", "ошибк")


class Chrono:
    def __init__(self):
        self.rows: list[dict] = []

    def stage(self, name: str):
        return _Stage(self, name)


class _Stage:
    def __init__(self, chrono: Chrono, name: str):
        self.chrono, self.name = chrono, name

    def __enter__(self):
        self.t0 = time.time()
        print(f"[{_now()}] -> {self.name}")
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = time.time() - self.t0
        status = "ОК" if exc_type is None else f"ОТКАЗ: {exc}"
        self.chrono.rows.append(
            {"stage": self.name, "seconds": round(dt, 1),
             "status": status})
        print(f"[{_now()}] <- {self.name}: {status} ({dt:.0f} с)")
        return False


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


class RegressError(RuntimeError):
    pass


class HttpReply:
    """Плоский ответ HTTP-хелперов (без сторонних зависимостей —
    канон дерева: stdlib urllib; страж объявленных зависимостей)."""

    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self.text = body.decode("utf-8", "replace")

    def json(self) -> dict:
        return json.loads(self.text)


def _http(req: urllib.request.Request, timeout: float) -> HttpReply:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpReply(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        return HttpReply(e.code, e.read())


def http_get(url: str, timeout: float = 60) -> HttpReply:
    return _http(urllib.request.Request(url), timeout)


def http_post_json(url: str, payload: dict | None = None,
                   timeout: float = 60,
                   method: str = "POST") -> HttpReply:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json"})
    return _http(req, timeout)


def http_post_files(url: str, files: list[tuple[str, tuple[str, bytes]]],
                    timeout: float = 300) -> HttpReply:
    boundary = f"----cosmos-{uuid.uuid4().hex}"
    out = io.BytesIO()
    for field, (name, blob) in files:
        out.write(f"--{boundary}\r\n".encode())
        out.write(
            f'Content-Disposition: form-data; name="{field}"; '
            f'filename="{name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
            .encode("utf-8"))
        out.write(blob)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        url, data=out.getvalue(), method="POST",
        headers={"Content-Type":
                 f"multipart/form-data; boundary={boundary}"})
    return _http(req, timeout)


def _data_root() -> Path:
    import _data_paths
    return Path(_data_paths.data_root())


def _poll(read_state, want=None, bad_substr=TERMINAL_BAD,
          timeout_s=3600, step_s=10, still=("идёт", "ожидан")):
    """Ждать терминального состояния: want (подстрока) — успех;
    состояние с bad_substr — отказ; «идёт/ожидание» — ждать."""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout_s:
        last = str(read_state() or "")
        low = last.lower()
        if want and want in low:
            return last
        if any(b in low for b in bad_substr):
            raise RegressError(f"стадия отказала: {last}")
        if want is None and not any(s in low for s in still):
            return last
        time.sleep(step_s)
    raise RegressError(f"тайм-аут стадии: {last!r}")


def load_machine_book() -> dict:
    return json.loads((NANO / "etalon" / "ANSWER-BOOK-MACHINE.json")
                      .read_text(encoding="utf-8"))


def match_answer(book: dict, qid: str, text: str) -> str | None:
    if qid in book.get("exact", {}):
        return book["exact"][qid]
    for pref, ans in book.get("prefix", {}).items():
        if qid.startswith(pref):
            return ans
    for row in book.get("regex", ()):
        if re.search(row["pattern"], text or "",
                     re.IGNORECASE | re.DOTALL):
            return row["answer"]
    return None


def fill_book(anketa_path: Path, book: dict,
              out_path: Path) -> tuple[int, list[str]]:
    """Заполнить открытые вопросы анкеты по машинной книге.
    Возврат: (заполнено, несопоставленные-вопросы)."""
    import openpyxl
    wb = openpyxl.load_workbook(anketa_path)
    filled, unmatched = 0, []
    for ws in wb.worksheets:
        if ws.title in ("Сводка", "Принятые ответы"):
            continue
        col = 5
        for j, cell in enumerate(ws[1]):
            if str(cell.value or "").strip().upper() \
                    == "ОТВЕТ АВИАКОМПАНИИ":
                col = j
                break
        for row in ws.iter_rows(min_row=2):
            qid = str(row[0].value or "").strip()
            if not qid:
                continue
            text = str(row[1].value or "")
            ans = match_answer(book, qid, text)
            if ans is None:
                unmatched.append(f"{qid}: {text[:120]}")
            else:
                row[col].value = ans
                filled += 1
    wb.save(out_path)
    return filled, unmatched


def run(panel: str, webapp: str, out_dir: Path,
        max_rounds: int, report: dict | None = None) -> dict:
    ch = Chrono()
    book = load_machine_book()
    if report is None:
        report = {}
    # chrono привязан ссылкой СРАЗУ: красный отчёт не теряет
    # пройденные этапы (урок первого запуска 2026-08-31).
    report["chrono"] = ch.rows
    report["started"] = datetime.now().isoformat(timespec="seconds")

    with ch.stage("создание прогона + заморозка"):
        files = []
        for n in DOC_NAMES:
            # книга данных лежит в data/ и участвует в линии
            # документов как источник правил S2 (канон прогонов)
            p = (NANO / "data" / n) if n == DATA_NAME \
                else (NANO / "docs" / n)
            if not p.is_file():
                raise RegressError(f"нет входного файла: {p}")
            files.append(("files", (n, p.read_bytes())))
        from urllib.parse import quote
        r = http_post_files(
            f"{panel}/api/runs?cloud_images=true"
            f"&customer={quote('Полярис-нано (регрессия)')}",
            files, timeout=300)
        if r.status_code != 200:
            raise RegressError(f"создание прогона: {r.status_code} "
                               f"{r.text[:200]}")
        run_id = r.json()["run"]
        report["run"] = run_id
        print(f"    прогон {run_id}")

    run_dir = _data_root() / "cdpm-runs" / run_id

    def state() -> str:
        return json.loads((run_dir / "status.json")
                          .read_text(encoding="utf-8")).get("state", "")

    with ch.stage("политика файлов -> облако"):
        cfg = {n: {"processing": "cloud"} for n in DOC_NAMES}
        r = http_post_json(f"{panel}/api/runs/{run_id}/files-config"
                           f"?scope=any", {"files": cfg},
                           timeout=60, method="PUT")
        if r.status_code != 200:
            raise RegressError(f"политика файлов: {r.status_code}")

    with ch.stage("извлечение"):
        r = http_post_json(f"{panel}/api/runs/{run_id}/extract"
                           f"?scope=any", timeout=60)
        if r.status_code != 200:
            raise RegressError(f"извлечение: {r.status_code} "
                               f"{r.text[:200]}")
        _poll(state, want="извлечено", timeout_s=1800)

    with ch.stage("сквозной аудит-цикл"):
        r = http_post_json(f"{panel}/api/runs/{run_id}/full-cycle"
                           f"?scope=any", timeout=60)
        if r.status_code != 200:
            raise RegressError(f"цикл: {r.status_code} {r.text[:200]}")
        _poll(state, timeout_s=5400, still=("идёт",))

    with ch.stage("анкета по машинной книге"):
        for rnd in range(1, max_rounds + 1):
            # Готовность считается ТОЛЬКО веб-ручкой панели —
            # status.json на диске её не несёт (урок первого
            # запуска: чтение файла дало «пусто» и анкета
            # проскочила незаполненной).
            st = http_get(f"{panel}/api/runs/{run_id}?scope=any",
                          timeout=120).json()
            open_q = ((st.get("readiness") or {})
                      .get("vmp_open_questions"))
            if not open_q:
                break
            filled, unmatched = fill_book(
                run_dir / "extract" / "anketa.xlsx", book,
                out_dir / f"book-round-{rnd}.xlsx")
            if unmatched:
                raise RegressError(
                    "вопросы без ответа в машинной книге: "
                    + "; ".join(unmatched[:5]))
            if not filled:
                raise RegressError(
                    f"открытых {open_q}, но заполнять нечего")
            blob = (out_dir / f"book-round-{rnd}.xlsx").read_bytes()
            r = http_post_files(
                f"{panel}/api/runs/{run_id}/answers?scope=any",
                [("file", (f"book-{rnd}.xlsx", blob))],
                timeout=300)
            if r.status_code != 200:
                raise RegressError(f"подача книги: {r.status_code} "
                                   f"{r.text[:200]}")
            _poll(state, want="применены", timeout_s=1800)
            print(f"    круг {rnd}: подано {filled}")
        else:
            raise RegressError("анкета не закрылась за "
                               f"{max_rounds} кругов")

    with ch.stage("линия данных"):
        p = NANO / "data" / DATA_NAME
        if not p.is_file():
            raise RegressError(f"нет книги данных: {p}")
        r = http_post_files(f"{panel}/api/runs/{run_id}/data",
                            [("files", (DATA_NAME,
                                        p.read_bytes()))],
                            timeout=300)
        if r.status_code != 200:
            raise RegressError(f"данные: {r.status_code}")
        # Раскладка моделей владельца обязательна для ВСЕХ точек:
        # политика «облако» ставится всем файлам прогона, ответ PUT
        # проверяется, фактический конфиг перечитывается. Урок
        # run-0024: молчаливый PUT + гонка регистрации файла данных
        # роняли класс канала данных в local (модель вне раскладки).
        want_cloud = dict.fromkeys(DOC_NAMES + (DATA_NAME,))
        cfg_body = {"files": {n: {"processing": "cloud"}
                              for n in want_cloud}}
        for attempt in range(1, 4):
            r = http_post_json(f"{panel}/api/runs/{run_id}"
                               f"/files-config?scope=any", cfg_body,
                               timeout=60, method="PUT")
            got = {}
            if r.status_code == 200:
                got = (http_get(f"{panel}/api/runs/{run_id}"
                                f"/files-config", timeout=60)
                       .json().get("files") or {})
            bad = [n for n in want_cloud
                   if (got.get(n) or {}).get("processing") != "cloud"]
            if r.status_code == 200 and not bad:
                break
            time.sleep(5)
        else:
            raise RegressError(
                "политика файлов данных не встала в «облако»: "
                f"PUT {r.status_code}, вне облака: {bad}")
        r = http_post_json(f"{panel}/api/runs/{run_id}/data-run"
                           f"?scope=any", timeout=60)
        if r.status_code != 200:
            raise RegressError(f"проверки данных: {r.status_code}")

        def dstate() -> str:
            return json.loads((run_dir / "status.json").read_text(
                encoding="utf-8")).get("data_state", "")

        def dclass() -> str:
            return json.loads((run_dir / "status.json").read_text(
                encoding="utf-8")).get("data_class", "")
        kl = dclass()
        if kl and kl != "cloud":
            raise RegressError(
                f"класс канала данных {kl!r} вместо «cloud» — "
                "раскладка моделей владельца нарушена")
        _poll(dstate, want="готов", timeout_s=1800)
        kl = dclass()
        if kl != "cloud":
            raise RegressError(
                f"класс канала данных {kl!r} вместо «cloud» — "
                "раскладка моделей владельца нарушена")

    with ch.stage("золотой дифф свода"):
        from vmp import golden_diff as gd
        model = json.loads((run_dir / "extract" / "model.json")
                           .read_text(encoding="utf-8"))
        res = gd.diff(model, gd.load_etalon(NANO / "etalon"))
        (out_dir / "golden-diff.md").write_text(
            gd.render_report(res, header=f"Прогон {run_id}"),
            encoding="utf-8")
        report["golden"] = {"verdict": res["verdict"],
                            "counts": res["counts"]}
        print(f"    дифф: {res['verdict']}")

    with ch.stage("пакет постановки"):
        r = http_get(f"{panel}/api/runs/{run_id}/export/"
                     f"task-package-send?scope=any", timeout=300)
        if r.status_code != 200:
            raise RegressError(f"пакет: {r.status_code} "
                               f"{r.text[:300]}")
        sent = r.json()
        report["package"] = {"sha": sent.get("sha"),
                             "mg": sent.get("mg")}
        mg = sent.get("mg") or {}
        blocked = [t for t, c in mg.items()
                   if not (c or {}).get("launch_allowed")]
        if blocked:
            raise RegressError(
                "гейт запуска закрыт для: " + ", ".join(blocked))

    # Расчёт — у COSMOS (решение владельца 04.09): пакет, принятый
    # каналом, стал текущим на витрине; кнопка нажимается её ручками
    # «Пакет постановки», артефакты читаются алиасами нажатия.
    import httpx
    from tools.cosmos_press_client import CosmosPress, CosmosPressError
    press = CosmosPress(httpx.Client(base_url=webapp,
                                     timeout=httpx.Timeout(600.0, connect=30.0)))
    pkg_sha = report["package"]["sha"]

    with ch.stage("пейринг кнопкой"):
        try:
            press.press("pairing", mode="optimization", unimproved_spent=5,
                        poll_s=3)
            st = press.wait("pairing", timeout_s=1800, poll_s=5,
                            package_sha=pkg_sha)
        except CosmosPressError as e:
            raise RegressError(f"пейринг: {e}")
        if st.get("error"):
            raise RegressError(f"пейринг: {st['error']}")
        cat = press.file_json("каталог-цепочек")
        cov = cat.get("coverage") or {}
        report["pairing"] = {
            "ip_status": cat.get("ip_status"),
            "chains": len(cat.get("selected") or []),
            "uncovered": cov.get("uncovered"),
            "verdict": cov.get("verdict"),
            "engine": st.get("engine")}
        if cov.get("verdict") != "OK" or cov.get("uncovered"):
            raise RegressError(f"пейринг не полон: {report['pairing']}")

    with ch.stage("ростеринг кнопкой"):
        pairing_run = None
        log = press.file_json("журнал-кнопки-пейринга")
        for row in log:
            m = re.search(r"runId ([0-9a-f-]{36})",
                          str(row.get("detail") or ""))
            if m:
                pairing_run = m.group(1)
        if not pairing_run:
            raise RegressError("runId каталога пейринга не найден")
        # objective_id — из карточки пакета витрины
        c = http_get(f"{webapp}/api/task-package/current",
                     timeout=60).json()
        obj = (((c.get("mg") or {}).get("columns") or {})
               .get("rostering") or {}).get("criterion")
        if not obj:
            raise RegressError("целевая ростеринга не найдена")
        try:
            press.press("rostering", mode="optimization",
                        pairing_run_id=pairing_run, objective_id=str(obj),
                        unimproved_spent=5, poll_s=3)
            st = press.wait("rostering", timeout_s=1800, poll_s=5,
                            package_sha=pkg_sha)
        except CosmosPressError as e:
            raise RegressError(f"ростеринг: {e}")
        if st.get("error"):
            raise RegressError(f"ростеринг: {st['error']}")
        delivery = press.file_json("выдача-кнопки-на-пейринге")
        report["rostering"] = {
            "verdict": delivery.get("verdict"),
            "score": (delivery.get("schedule") or {}).get("score"),
            "solver_run_id": (delivery.get("schedule") or {})
            .get("solver_run_id"),
            "certified": delivery.get("certified")}
        if delivery.get("verdict") != "допустимо":
            raise RegressError(f"ростеринг: {report['rostering']}")

    with ch.stage("ФПР обоих решений"):
        solver_run = report["rostering"]["solver_run_id"]
        r = http_post_json(f"{webapp}/api/source-check/run",
                           {"solverRunId": solver_run},
                           timeout=60)
        if r.status_code != 202:
            raise RegressError(f"запуск ФПР: {r.status_code} "
                               f"{r.text[:200]}")
        last_path = _data_root() / "source-check" / "last.json"

        def fstate() -> str:
            try:
                return json.loads(last_path.read_text(
                    encoding="utf-8")).get("status", "")
            except (OSError, json.JSONDecodeError):
                return "running"
        _poll(fstate, want="done", timeout_s=1800,
              bad_substr=("error",), still=("running",))
        last = json.loads(last_path.read_text(encoding="utf-8"))
        report["source_check"] = {
            "counts": last.get("counts"),
            "reader": last.get("reader")}
        counts = last.get("counts") or {}
        if counts.get("violated"):
            raise RegressError(f"ФПР: нарушения {counts}")

    report["verdict"] = ("AMBER"
                         if report.get("golden", {}).get("verdict")
                         in ("AMBER", "RED") else "GREEN")
    return report


def render(report: dict) -> str:
    lines = ["# Регрессия «Полярис-нано» (полный цикл)", "",
             f"Вердикт: **{report.get('verdict', 'RED')}**",
             f"Прогон: {report.get('run')} — {report.get('started')}",
             ""]
    if "error" in report:
        lines += [f"ОТКАЗ: {report['error']}", ""]
    lines.append("| Этап | Секунды | Статус |")
    lines.append("|---|---|---|")
    for r in report.get("chrono", ()):
        lines.append(f"| {r['stage']} | {r['seconds']} "
                     f"| {r['status']} |")
    for key in ("golden", "package", "pairing", "rostering",
                "source_check"):
        if key in report:
            lines += ["", f"## {key}",
                      "```json",
                      json.dumps(report[key], ensure_ascii=False,
                                 indent=1),
                      "```"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Регрессия нано")
    ap.add_argument("--panel", default="http://127.0.0.1:8097")
    ap.add_argument("--webapp", default="http://127.0.0.1:8090")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--max-answer-rounds", type=int, default=6)
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir) if args.out_dir else (
        _data_root() / "regression"
        / datetime.now().strftime("%Y-%m-%d_%H%M"))
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    try:
        run(args.panel, args.webapp, out_dir,
            args.max_answer_rounds, report=report)
        code = 0
    except (RegressError, urllib.error.URLError, OSError,
            json.JSONDecodeError, KeyError) as e:
        report["verdict"] = "RED"
        report["error"] = str(e)
        code = 1
    text = render(report)
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(text)
    print(f"отчёт: {out_dir}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
