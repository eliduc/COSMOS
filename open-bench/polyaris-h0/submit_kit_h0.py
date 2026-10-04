# -*- coding: utf-8 -*-
r"""Поблочная подача комплекта «Полярис-H0» на контур CDPM (правило
владельца 2026-09-01: прогон — только с его отмашки, по одному блоку,
с докладом после каждого; автопереходов между блоками НЕТ).

Каждая команда — один блок; следующий блок запускается отдельной
командой после доклада владельцу:

  python submit_kit_h0.py --panel http://127.0.0.1:8095 create
      --agents <раскладка.json> [--cloud-images]
  python submit_kit_h0.py ... data      <run>   # xlsx — файл ДАННЫХ
  python submit_kit_h0.py ... policy    <run>   # политика файлов правил
  python submit_kit_h0.py ... extract   <run>
  python submit_kit_h0.py ... cycle     <run>   # сквозной аудит-цикл
  python submit_kit_h0.py ... arbiter   <run>   # если есть тупики
  python submit_kit_h0.py ... answers   <run>   # анкета книгой комплекта
  python submit_kit_h0.py ... data-run  <run>
  python submit_kit_h0.py ... button    <run> --cosmos http://127.0.0.1:8123
                                        [--task pairing|rostering] [--engine …]
  python submit_kit_h0.py ... status    <run>

Раскладка моделей (--agents) — файл формы agents.json панели, данный
владельцем; без него команда create отказывает: умолчания площадки
согласием не считаются (llm-approval-before-runs).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import nano_regression as nr  # noqa: E402  (http-помощники, fill_book)

KIT = json.loads((HERE / "kit-manifest.json").read_text(encoding="utf-8"))
CUSTOMER = KIT.get("customer", "Полярис-H0")


def _status(panel: str, run: str) -> dict:
    return nr.http_get(f"{panel}/api/runs/{run}?scope=any",
                       timeout=120).json()


def _print_status(panel: str, run: str) -> None:
    st = _status(panel, run)
    rd = st.get("readiness") or {}
    print(json.dumps({
        "run": run, "state": st.get("state"),
        "vmp": rd.get("vmp_state"),
        "open_questions": rd.get("vmp_open_questions"),
        "data_state": st.get("data_state"),
        "error": st.get("error"),
    }, ensure_ascii=False, indent=1))


def _poll_state(panel: str, run: str, want: str | None,
                timeout_s: int) -> str:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = str(_status(panel, run).get("state") or "")
        low = last.lower()
        if want and want in low:
            return last
        if "отказ" in low or "красный" in low:
            raise SystemExit(f"блок отказал: {last}")
        if want is None and "идёт" not in low:
            return last
        time.sleep(10)
    raise SystemExit(f"тайм-аут блока: {last!r}")


# Роли раскладки — из реестра CDPM (полная независимость ролей 2.09:
# родители + все узлы, включая vision); скрипт не держит свой перечень.
try:
    from cdpm.providers import ROLES  # noqa: E402
except Exception:                                         # noqa: BLE001
    ROLES = ("worker", "auditor", "arbiter", "applier", "vision")


def _load_layout(path: str) -> dict:
    """Раскладка ролей из файла площадочной формы: служебные ключи
    (form_version, customer, _comment) пер-прогонная ручка /agents
    отвергает — подаются только роли."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    layout = {k: v for k, v in raw.items() if k in ROLES}
    if not layout:
        raise SystemExit(f"в {path} нет ни одной роли из {ROLES}")
    return layout


def _put_agents(panel: str, run: str, layout: dict) -> None:
    r = nr.http_post_json(f"{panel}/api/runs/{run}/agents", layout,
                          timeout=60, method="PUT")
    if r.status_code != 200:
        raise SystemExit(f"раскладка моделей: {r.status_code} "
                         f"{r.text[:300]}")
    got = r.json()
    print("раскладка моделей установлена:", json.dumps(
        {role: {c: (s.get("model")) for c, s in
                ((got.get(role) or {}).get("channels") or {}).items()}
         for role in ROLES if got.get(role)}, ensure_ascii=False))


def cmd_agents(a) -> None:
    _put_agents(a.panel, a.run, _load_layout(a.agents))
    _print_status(a.panel, a.run)


def cmd_create(a) -> None:
    if not a.agents:
        raise SystemExit("нужна раскладка моделей владельца: --agents "
                         "<agents.json> (умолчания площадки — не "
                         "согласие)")
    layout = _load_layout(a.agents)
    files = []
    for rel in KIT["rules_files"]:          # порядок = S1, S2…
        p = HERE / rel
        files.append(("files", (p.name, p.read_bytes())))
    url = (f"{a.panel}/api/runs?customer={quote(CUSTOMER)}"
           f"&cloud_images={'true' if a.cloud_images else 'false'}")
    r = nr.http_post_files(url, files, timeout=600)
    if r.status_code != 200:
        raise SystemExit(f"создание прогона: {r.status_code} "
                         f"{r.text[:300]}")
    run = r.json()["run"]
    print(f"прогон {run}: {r.json().get('state')}")
    _put_agents(a.panel, run, layout)
    _print_status(a.panel, run)


def cmd_data(a) -> None:
    files = []
    for rel in KIT["data_files"]:
        p = HERE / rel
        files.append(("files", (p.name, p.read_bytes())))
    r = nr.http_post_files(f"{a.panel}/api/runs/{a.run}/data", files,
                           timeout=300)
    if r.status_code != 200:
        raise SystemExit(f"данные: {r.status_code} {r.text[:300]}")
    _print_status(a.panel, a.run)


def cmd_policy(a) -> None:
    # Политика — ВСЕМ файлам прогона (правила + данные): ручка
    # перетирает конфиг целиком, а незаписанный файл падает в
    # строжайшее «local» — линия данных ушла бы мимо раскладки.
    names = [Path(rel).name
             for rel in KIT["rules_files"] + KIT["data_files"]]
    cfg = {n: {"processing": a.processing} for n in names}
    r = nr.http_post_json(f"{a.panel}/api/runs/{a.run}/files-config"
                          f"?scope=any", {"files": cfg},
                          timeout=60, method="PUT")
    if r.status_code != 200:
        raise SystemExit(f"политика файлов: {r.status_code} "
                         f"{r.text[:300]}")
    print(json.dumps(r.json().get("files"), ensure_ascii=False))


def cmd_extract(a) -> None:
    r = nr.http_post_json(f"{a.panel}/api/runs/{a.run}/extract"
                          f"?scope=any", timeout=60)
    if r.status_code != 200:
        raise SystemExit(f"извлечение: {r.status_code} {r.text[:300]}")
    t0 = time.time()
    print("извлечение:", _poll_state(a.panel, a.run, "извлечено", 3600),
          f"({time.time() - t0:.0f} с)")
    _print_status(a.panel, a.run)


def cmd_cycle(a) -> None:
    r = nr.http_post_json(f"{a.panel}/api/runs/{a.run}/full-cycle"
                          f"?scope=any", timeout=60)
    if r.status_code != 200:
        raise SystemExit(f"цикл: {r.status_code} {r.text[:300]}")
    t0 = time.time()
    print("цикл:", _poll_state(a.panel, a.run, None, 7200),
          f"({time.time() - t0:.0f} с)")
    _print_status(a.panel, a.run)


def cmd_arbiter(a) -> None:
    r = nr.http_post_json(f"{a.panel}/api/runs/{a.run}/arbiter"
                          f"?scope=any", timeout=60)
    if r.status_code != 200:
        raise SystemExit(f"арбитраж: {r.status_code} {r.text[:300]}")
    t0 = time.time()
    print("арбитраж:", _poll_state(a.panel, a.run, None, 3600),
          f"({time.time() - t0:.0f} с)")
    _print_status(a.panel, a.run)


def cmd_answers(a) -> None:
    """Один круг анкеты: открытые вопросы → книга комплекта → подача.
    Несопоставленный вопрос — честный стоп (не заплатка книги)."""
    book = json.loads((HERE / KIT["answer_book"])
                      .read_text(encoding="utf-8"))
    run_dir = nr._data_root() / "cdpm-runs" / a.run
    st = _status(a.panel, a.run)
    open_q = (st.get("readiness") or {}).get("vmp_open_questions")
    if not open_q:
        print("открытых вопросов нет — подавать нечего")
        return
    out = HERE / "runs" / a.run
    out.mkdir(parents=True, exist_ok=True)
    n = len(list(out.glob("book-round-*.xlsx"))) + 1
    filled, unmatched = nr.fill_book(
        run_dir / "extract" / "anketa.xlsx", book,
        out / f"book-round-{n}.xlsx")
    if unmatched:
        raise SystemExit("вопросы без ответа в книге комплекта "
                         "(доложить владельцу, книгу не латать молча): "
                         + "; ".join(unmatched[:8]))
    blob = (out / f"book-round-{n}.xlsx").read_bytes()
    r = nr.http_post_files(f"{a.panel}/api/runs/{a.run}/answers?scope=any",
                           [("file", (f"book-{n}.xlsx", blob))],
                           timeout=300)
    if r.status_code != 200:
        raise SystemExit(f"подача книги: {r.status_code} {r.text[:300]}")
    print(f"круг {n}: подано ответов {filled};",
          _poll_state(a.panel, a.run, "применены", 1800))
    _print_status(a.panel, a.run)


def cmd_button(a) -> None:
    """Блок «кнопка» (расчёт — у COSMOS, решение владельца 04.09): пакет
    постановки экспортируется из CDPM, загружается в COSMOS (--cosmos) и
    задача (pairing | rostering) нажимается ручками «Пакет постановки»;
    движок отбора и решатель — только сторона COSMOS."""
    import httpx
    from tools.cosmos_press_client import (CosmosPress, CosmosPressError,
                                           launch_gate)
    if not a.cosmos:
        raise SystemExit("нужен --cosmos: расчёт по пакету — у COSMOS")
    r = httpx.get(f"{a.panel}/api/runs/{a.run}/export/task-package",
                  params={"scope": "any"}, timeout=300)
    if r.status_code != 200:
        raise SystemExit(f"пакет из CDPM: {r.status_code} {r.text[:400]}")
    press = CosmosPress(httpx.Client(base_url=a.cosmos,
                                     timeout=httpx.Timeout(900.0, connect=30.0)))
    t0 = time.time()
    try:
        up = press.upload(r.content, name=f"{a.run}-task-package.zip")
        print("пакет принят COSMOS:", json.dumps(
            {k: up.get(k) for k in ("verdict", "sha", "mismatch", "detail")},
            ensure_ascii=False)[:400])
        gate = launch_gate(up, a.task)
        if gate:
            raise SystemExit(f"кнопка {a.task}: {gate}")
        press.press(a.task, engine=(a.engine if a.task == "pairing" else None),
                    pairing_run_id=a.pairing_run)
        st = press.wait(a.task, timeout_s=3600, poll_s=10, package_sha=up["sha"],
                        on_state=lambda s: print("   " + s[:140]))
    except CosmosPressError as e:
        raise SystemExit(f"кнопка {a.task}: {e}")
    print(f"кнопка {a.task}: {st.get('state')} ({time.time() - t0:.0f} с)")
    print(json.dumps({k: st.get(k) for k in
                      ("package_sha", "task", "engine", "state", "verdict",
                       "error", "llm_scope", "tasks_done")},
                     ensure_ascii=False)[:1200])


def cmd_data_run(a) -> None:
    r = nr.http_post_json(f"{a.panel}/api/runs/{a.run}/data-run"
                          f"?scope=any", timeout=60)
    if r.status_code != 200:
        raise SystemExit(f"линия данных: {r.status_code} {r.text[:300]}")
    deadline = time.time() + 3600
    while time.time() < deadline:
        ds = str(_status(a.panel, a.run).get("data_state") or "")
        if "идут" not in ds:
            print("линия данных:", ds)
            break
        time.sleep(10)
    _print_status(a.panel, a.run)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="http://127.0.0.1:8095")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--agents", help="раскладка моделей владельца")
    c.add_argument("--cloud-images", action="store_true")
    for name in ("agents", "data", "policy", "extract", "cycle",
                 "arbiter", "answers", "data-run", "button", "status"):
        p = sub.add_parser(name)
        p.add_argument("run")
        if name == "policy":
            p.add_argument("--processing", default="cloud",
                           choices=("local", "cloud-country", "cloud"))
        if name == "agents":
            p.add_argument("--agents", required=True)
        if name == "button":
            p.add_argument("--task", default="pairing",
                           choices=("pairing", "rostering"))
            p.add_argument("--cosmos", default=None,
                           help="адрес COSMOS (ручки «Пакет постановки»)")
            p.add_argument("--engine", default="cpsat",
                           choices=("cpsat", "cplex", "highs"))
            p.add_argument("--pairing-run", dest="pairing_run",
                           default=None)
    a = ap.parse_args()
    {"create": cmd_create, "agents": cmd_agents, "data": cmd_data,
     "policy": cmd_policy, "extract": cmd_extract, "cycle": cmd_cycle,
     "arbiter": cmd_arbiter, "answers": cmd_answers,
     "data-run": cmd_data_run, "button": cmd_button,
     "status": lambda x: _print_status(x.panel, x.run)}[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
