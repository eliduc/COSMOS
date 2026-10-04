# -*- coding: utf-8 -*-
"""Драйвер межпроцессного гейта детерминизма повтора (система прогонов
COSMOS, раздел 17.6): запись попытки и её повтор из журнала идут в
РАЗНЫХ процессах с разными PYTHONHASHSEED на одном каталоге данных
(CREW_DATA_DIR из среды). Те же подмены, что у стендов кнопки
(FakeSolver, фальшивый построитель, подмена вывода реестра) — ни
решателя, ни модели. Итог печатается одной строкой JSON.

    python tests/_replay_driver.py record            → {"attempt_id", "verdict", "sha"}
    python tests/_replay_driver.py replay <attempt>  → {"attempt_id", "verdict", "report": {...}}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _stand_identity import ADMIN_EMAIL                          # noqa: E402

os.environ.setdefault("CREW_SUPER_ADMIN", ADMIN_EMAIL)
os.environ["CREW_ALLOWLIST_EMAILS"] = ""
for _k in ("CREW_TASK_INBOX", "CREW_TASK_PACKAGE", "CREW_VMP_REGISTRY",
           "CREW_SOLVER_URL", "CREW_TEMPLATE_CATALOG", "CREW_COSMOS_RUNS_DIR",
           "CREW_ATTEMPTS_KEEP", "CREW_CALL_JOURNAL", "CREW_LLM_EXCHANGES"):
    os.environ.pop(_k, None)
os.environ["AIRLINE_INGEST_NORMS"] = ""


def _install_fakes():
    # Рубеж непротиворечивости при выпуске пакета (11.09.2026): в
    # подпроцессе автофикстура conftest не действует — переводчика SMT
    # подменяет тот же стендовый модуль (Z3 настоящий, модели нет).
    import _smt_gate_stub
    _smt_gate_stub.install()
    from vmp import registry_check as _regc
    from vmp.run import button as btn

    def _fake_press_registry(run_dir, model, task):
        reg = {"created": "драйвер", "scope": "кнопка: драйвер",
               "code_fingerprints": _regc.code_fingerprints(ROOT),
               "rules": [{"rule_id": str(r["id"]), "verdict": "covered_full",
                          "title": r.get("title") or ""}
                         for r in (model or {}).get("rules") or []]}
        path = Path(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path
    btn.press_registry = _fake_press_registry


def _fakes():
    from tests.test_package_runs import _pairing_kwargs
    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)
    fakes["sleep"] = lambda *_: None
    return fakes


def _builder(payload, args, out):
    from tests.test_run_button import _CATALOG_OK
    raw = json.dumps(_CATALOG_OK).encode("utf-8")
    (Path(out) / "builder-payload.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (Path(out) / "builder-catalog.json").write_bytes(raw)
    (Path(out) / "pairing_builder_check_driver.json").write_text(
        json.dumps({"total_cost": 7, "person_nights": 1}), encoding="utf-8")
    return raw


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    _install_fakes()
    from tests.test_run_button import FakeSolver
    from webapp import package_runs as pr
    if mode == "record":
        from tests.test_package_runs import _make_current
        tmp = Path(os.environ["REPLAY_DRIVER_TMP"])
        sha = _make_current(tmp)
        st = pr.press("pairing", solver=FakeSolver(), pairing_builder=_builder,
                      start_thread=False, **_fakes())
        print(json.dumps({"attempt_id": st["attempt_id"], "verdict": st["verdict"],
                          "error": st["error"], "sha": sha}, ensure_ascii=False))
        return 0
    if mode == "replay":
        attempt = argv[2]
        fakes = _fakes()
        fakes.pop("sleep", None)
        out = pr.press("pairing", replay={"mode": "journal", "run": None,
                                          "source_attempt": attempt},
                       start_thread=False, **fakes)
        st = pr.status()
        rec = st["attempts"][0]
        wd = pr.workdir_of(pr.current(), st["package_sha"])
        report_p = wd / pr.ATTEMPTS_DIR / rec["id"] / "replay" / "report.json"
        report = json.loads(report_p.read_text(encoding="utf-8")) \
            if report_p.is_file() else None
        print(json.dumps({"attempt_id": rec["id"], "verdict": rec["verdict"],
                          "error": rec["error"], "state": out.get("state"),
                          "report": {"verdict": (report or {}).get("verdict"),
                                     "words": (report or {}).get("words"),
                                     "first_difference": (report or {}).get("first_difference"),
                                     "different": ((report or {}).get("files") or {}).get("different")}},
                         ensure_ascii=False))
        return 0
    print(json.dumps({"error": "режим %r вне словаря: record, replay" % mode}))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
