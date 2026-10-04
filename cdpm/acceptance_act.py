# -*- coding: utf-8 -*-
"""Акт приёмки смысла — носитель П4 (определение Д5 плана «прямой ход»,
проверка С-1: у акта не было ни файла, ни приёмщика, ни места в кортеже).

Акт — файл `acceptance-act.json` в каталоге прогона: по каждой строке
свода и каждой целевой — вердикт («принято» / «отклонено» / «вопрос»),
носитель нормы (документ, якорь), приёмщик, дата, замечание. Хэш акта
входит в кортеж мишени, отчёт гейта и реестр золотых прогонов. Золотым
ВМП объявляет владелец по акту; расхождение выхода конвейера со
справочником записывается в акт одним из двух исходов — «дефект
конвейера» либо «решение владельца об изменении справочника».

Код изготавливает ОСТОВ акта из модели и проверяет заполненный акт;
вердикты ставит человек.

    python -m cdpm.acceptance_act <run_dir> --init      # остов из extract/model.json
    python -m cdpm.acceptance_act <run_dir> --check     # полнота, хэш, сводка
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

from cdpm.model import loads_strict

ACT_FILE = "acceptance-act.json"
VERDICTS = ("принято", "отклонено", "вопрос")
OUTCOMES = ("дефект конвейера", "решение владельца об изменении справочника")


def model_fingerprint(model: dict) -> str:
    """Отпечаток модели, к которой относится акт: канонический JSON без
    служебных полей meta (заметка, версия сборщика)."""
    body = {"rules": model.get("rules", []), "objectives": model.get("objectives", []),
            "questions": [q.get("id") for q in model.get("questions", []) or []]}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()[:16]


def _carrier(item: dict) -> dict:
    for s in item.get("sources") or []:
        if isinstance(s, dict):
            return {"sid": s.get("sid"), "anchor": s.get("anchor")}
    return {"sid": None, "anchor": None}


def build_skeleton(model: dict, run_id: str) -> dict:
    rows = []
    for r in model.get("rules", []) or []:
        if not isinstance(r, dict):
            continue
        fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else {}
        rows.append({"id": r.get("id"), "kind": r.get("kind"), "zone": r.get("zone"),
                     "statement": r.get("statement"), "carrier": _carrier(r),
                     "family": fit.get("family"), "category": fit.get("category"),
                     "verdict": None, "acceptor": None, "date": None, "remark": ""})
    for o in model.get("objectives", []) or []:
        if not isinstance(o, dict):
            continue
        c = o.get("criterion") if isinstance(o.get("criterion"), dict) else {}
        rows.append({"id": o.get("id"), "kind": "objective", "zone": o.get("zone"),
                     "statement": o.get("statement"), "carrier": _carrier(o),
                     "family": c.get("kind"), "category": None,
                     "verdict": None, "acceptor": None, "date": None, "remark": ""})
    return {"act_version": 1, "run": run_id, "model_fingerprint": model_fingerprint(model),
            "status": "черновик", "golden": False,
            "reference_discrepancies": [],   # {row, outcome ∈ OUTCOMES, note}
            "rows": rows}


def act_hash(act: dict) -> str:
    body = {"model_fingerprint": act.get("model_fingerprint"),
            "rows": [{k: r.get(k) for k in ("id", "verdict", "acceptor", "date", "remark")}
                     for r in act.get("rows", [])],
            "reference_discrepancies": act.get("reference_discrepancies", []),
            "golden": bool(act.get("golden"))}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()[:16]


def validate(act: dict, model: dict | None = None) -> list[str]:
    """Нарушения формы и полноты акта. Пустой список — акт полон:
    у каждой строки вердикт из перечня, приёмщик и дата; «отклонено» и
    «вопрос» — с замечанием; акт относится к этой модели."""
    problems: list[str] = []
    rows = act.get("rows") or []
    if model is not None:
        if act.get("model_fingerprint") != model_fingerprint(model):
            problems.append("акт относится к другой модели (отпечаток не совпал)")
        want = {x.get("id") for coll in ("rules", "objectives")
                for x in (model.get(coll) or []) if isinstance(x, dict)}
        have = {r.get("id") for r in rows}
        for i in sorted(want - have, key=str):
            problems.append(f"{i}: строки нет в акте")
        for i in sorted(have - want, key=str):
            problems.append(f"{i}: строки акта нет в модели")
    for r in rows:
        rid = r.get("id")
        v = r.get("verdict")
        if v is None:
            problems.append(f"{rid}: вердикт не поставлен")
            continue
        if v not in VERDICTS:
            problems.append(f"{rid}: вердикт {v!r} вне перечня {VERDICTS}")
        if not r.get("acceptor"):
            problems.append(f"{rid}: приёмщик не назван")
        if not r.get("date"):
            problems.append(f"{rid}: дата не указана")
        if v in ("отклонено", "вопрос") and not str(r.get("remark") or "").strip():
            problems.append(f"{rid}: вердикт {v!r} без замечания")
    for d in act.get("reference_discrepancies") or []:
        if not isinstance(d, dict) or d.get("outcome") not in OUTCOMES:
            problems.append(f"расхождение со справочником без исхода из {OUTCOMES}: {d!r}")
    if act.get("golden") and problems:
        problems.append("акт помечен «золотой», но не полон")
    return problems


def summary(act: dict) -> dict:
    rows = act.get("rows") or []
    counts = {v: 0 for v in VERDICTS}
    counts["без вердикта"] = 0
    for r in rows:
        v = r.get("verdict")
        counts[v if v in VERDICTS else "без вердикта"] += 1
    return {"hash": act_hash(act), "rows": len(rows), "counts": counts,
            "status": act.get("status"), "golden": bool(act.get("golden")),
            "discrepancies": len(act.get("reference_discrepancies") or [])}


def load(run_dir: Path) -> dict | None:
    p = Path(run_dir) / ACT_FILE
    if not p.is_file():
        return None
    return loads_strict(p.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="акт приёмки смысла (П4)")
    ap.add_argument("run_dir")
    ap.add_argument("--init", action="store_true", help="изготовить остов из extract/model.json")
    ap.add_argument("--check", action="store_true", help="проверить полноту и напечатать сводку")
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir)
    model_path = run_dir / "extract" / "model.json"
    # The act fingerprints the recorded model, not a migrated interpretation.
    # Reject duplicate keys in both evidence files without rewriting either.
    try:
        model = loads_strict(model_path.read_text(encoding="utf-8")) \
            if model_path.is_file() else None
    except (OSError, ValueError) as exc:
        print(f"не удалось прочитать модель {model_path}: {exc}")
        return 2
    if a.init:
        if model is None:
            print(f"нет модели {model_path}")
            return 2
        p = run_dir / ACT_FILE
        if p.is_file():
            print(f"акт уже есть: {p} — не перезаписан")
            return 2
        act = build_skeleton(model, run_dir.name)
        act["created"] = _dt.datetime.now().isoformat(timespec="seconds")
        p.write_text(json.dumps(act, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"остов акта: {p} ({len(act['rows'])} строк)")
        return 0
    try:
        act = load(run_dir)
    except (OSError, ValueError) as exc:
        print(f"не удалось прочитать акт {run_dir / ACT_FILE}: {exc}")
        return 2
    if act is None:
        print("акта нет")
        return 2
    probs = validate(act, model)
    print(json.dumps(summary(act), ensure_ascii=False))
    for pr in probs:
        print(" -", pr)
    print("АКТ ПОЛОН" if not probs else f"АКТ НЕ ПОЛОН: {len(probs)}")
    return 0 if not probs else 1


if __name__ == "__main__":
    raise SystemExit(main())
