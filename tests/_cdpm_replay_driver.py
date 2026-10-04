# -*- coding: utf-8 -*-
"""Вспомогательный сценарий теста детерминизма повтора CDPM между
процессами (проект повтора CDPM, 2.13): `record <runs_root>` — запись
прогона на поддельных каналах (рождение → извлечение) в этом процессе;
`replay <runs_root> <run>` — повтор из журнала в другом процессе (иное семя
хеша). Последняя строка вывода — JSON с итогом. (Файл `_replay_driver.py`
рядом — драйвер системы прогонов COSMOS, другой стенд.)"""
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

_RULES_NAMES = ("RULES-A.md", "RULES-B.md")


def _setup(runs_root: Path):
    runs_root.parent.mkdir(parents=True, exist_ok=True)
    # Рубеж непротиворечивости при выпуске пакета (11.09.2026): в
    # подпроцессе автофикстура conftest не действует — переводчика SMT
    # подменяет тот же стендовый модуль (Z3 настоящий, модели нет).
    import _smt_gate_stub
    _smt_gate_stub.install()
    os.environ["CDPM_RUNS_DIR"] = str(runs_root)
    os.environ["CDPM_SETTINGS_FILE"] = str(runs_root.parent / "cdpm-settings.json")
    os.environ["CREW_TASK_INBOX"] = ""
    os.environ["AIRLINE_INGEST_NORMS"] = ""
    from cdpm import llm as llm_mod
    from cdpm import providers as prov
    # Объявленная тестовая раскладка — как фикстура conftest.declared_test_layout.
    layout = {"form_version": 1, "customer": "тестовая площадка"}
    for role in prov.ROLES:
        if role == "vision":
            continue
        layout[role] = {"channels": {
            "cloud": {"model": "test-stub"},
            "local": {"provider": "local", "model": "test-local"}}}
    path = runs_root.parent / "agents-default-test.json"
    path.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
    os.environ["CDPM_AGENTS_DEFAULT"] = str(path)
    from cdpm import webui
    from test_cdpm_extract import fake_llm
    webui.llm_factory = lambda scope: fake_llm
    webui.exchange_llm_factory = lambda scope, requested: fake_llm

    def _channel_via_test_seam(scope="any", provider=None, model=None, **kw):
        return webui.exchange_llm_factory(scope, provider or "cloud")
    llm_mod.channel = _channel_via_test_seam
    return webui


def _client(webui):
    from fastapi.testclient import TestClient
    return TestClient(webui.app)


def _wait(client, run_id, timeout_s=120.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = client.get(f"/api/runs/{run_id}").json()
        busy = any(str(st.get(k) or "").endswith(("идёт", "идут"))
                   for k in ("state", "data_state", "smt_state"))
        if not busy:
            return st
        time.sleep(0.2)
    raise SystemExit("стадия не завершилась")


def record(runs_root: Path) -> dict:
    webui = _setup(runs_root)
    from test_cdpm_extract import _RULES_A, _RULES_B
    client = _client(webui)
    r = client.post("/api/runs", files=[
        ("files", (_RULES_NAMES[0], _RULES_A.encode("utf-8"), "text/markdown")),
        ("files", (_RULES_NAMES[1], _RULES_B.encode("utf-8"), "text/markdown")),
    ], params={"customer": "Полярис (тест)", "data_class": "synthetic"})
    assert r.status_code == 200, r.text
    run_id = r.json()["run"]
    r = client.post(f"/api/runs/{run_id}/extract")
    assert r.status_code == 200, r.text
    st = _wait(client, run_id)
    return {"run": run_id, "state": st.get("state")}


def replay(runs_root: Path, source: str) -> dict:
    webui = _setup(runs_root)
    client = _client(webui)
    r = client.post(f"/api/runs/{source}/replay", json={"mode": "journal"})
    assert r.status_code == 202, r.text
    child = r.json()["run"]
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        st = client.get(f"/api/runs/{child}").json()
        rep = (st.get("passport") or {}).get("replay") or {}
        if rep.get("verdict"):
            report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
            return {"run": child, "verdict": rep["verdict"],
                    "first_divergence": report.get("first_divergence"),
                    "calls": report.get("calls")}
        time.sleep(0.2)
    raise SystemExit("повтор не завершился")


if __name__ == "__main__":
    cmd = sys.argv[1]
    root = Path(sys.argv[2])
    if cmd == "record":
        out = record(root)
    elif cmd == "replay":
        out = replay(root, sys.argv[3])
    else:
        raise SystemExit(f"команда вне словаря: {cmd}")
    print(json.dumps(out, ensure_ascii=False))
