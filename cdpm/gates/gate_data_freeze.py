# -*- coding: utf-8 -*-
"""Гейт заморозки операционных данных (линия данных, ДЛ-3).

1. самотест: рассинхрон набора и манифеста ловится;
2. набор данных и манифест согласованы (SID, листы, счёт строк);
3. при --originals: SHA-256 каждого оригинала совпадает с манифестом —
   данные не менялись после заморозки.

    python -m cdpm.gates.gate_data_freeze КАТАЛОГ [--originals КАТ]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _selftest() -> None:
    dataset = {"sources": [{"sid": "D1", "file": "x", "sha256": "h",
                            "rows_seen": 1}],
               "tables": {"D1": {"Л": {"columns": ["a"],
                                       "rows": [{"a": "1"}]}}}}
    manifest = {"files": [{"sid": "D1", "file": "x", "sha256": "h",
                           "rows_seen": 2}],
                "sheets": {"D1": ["Л"]}}
    assert any("счёт строк" in p
               for p in _compare(dataset, manifest)), \
        "самотест: рассинхрон счёта строк не пойман"


def _compare(dataset: dict, manifest: dict) -> list[str]:
    problems = []
    ds = {s["sid"]: s for s in dataset.get("sources", [])}
    mf = {f["sid"]: f for f in manifest.get("files", [])}
    if sorted(ds) != sorted(mf):
        problems.append(f"SID набора {sorted(ds)} не совпадают с "
                        f"манифестом {sorted(mf)}")
        return problems
    for sid, s in ds.items():
        if s.get("sha256") != mf[sid].get("sha256"):
            problems.append(f"{sid}: SHA-256 набора и манифеста "
                            f"расходятся")
        rows = sum(len(t["rows"]) for t in
                   dataset.get("tables", {}).get(sid, {}).values())
        if rows != s.get("rows_seen") \
                or rows != mf[sid].get("rows_seen"):
            problems.append(f"{sid}: счёт строк не сходится (таблицы "
                            f"{rows}, набор {s.get('rows_seen')}, "
                            f"манифест {mf[sid].get('rows_seen')})")
        sheets = sorted(dataset.get("tables", {}).get(sid, {}))
        if sheets != sorted(manifest.get("sheets", {}).get(sid, [])):
            problems.append(f"{sid}: перечень листов расходится")
    return problems


def check(freeze_dir: Path,
          originals_dir: Path | None = None) -> tuple[list[str], dict]:
    problems: list[str] = []
    counters = {"files": 0, "sheets": 0, "rows": 0, "originals": 0}
    for name in ("dataset.json", "manifest.json"):
        if not (freeze_dir / name).is_file():
            return [f"{freeze_dir / name} не существует"], counters
    dataset = json.loads((freeze_dir / "dataset.json")
                         .read_text(encoding="utf-8"))
    manifest = json.loads((freeze_dir / "manifest.json")
                          .read_text(encoding="utf-8"))
    problems += _compare(dataset, manifest)
    counters.update(
        files=len(dataset.get("sources", [])),
        sheets=sum(len(t) for t in dataset.get("tables", {}).values()),
        rows=sum(s.get("rows_seen", 0)
                 for s in dataset.get("sources", [])))
    if originals_dir is not None:
        for f in manifest.get("files", []):
            path = originals_dir / f["file"]
            if not path.is_file():
                problems.append(f"оригинал {f['file']} не найден")
                continue
            counters["originals"] += 1
            got = hashlib.sha256(path.read_bytes()).hexdigest()
            if got != f.get("sha256"):
                problems.append(f"{f['file']}: SHA-256 оригинала не "
                                f"совпадает с манифестом — данные "
                                f"изменены после заморозки")
    return problems, counters


def run(freeze_dir: str, originals: str | None) -> int:
    _selftest()
    problems, c = check(Path(freeze_dir),
                        Path(originals) if originals else None)
    print(f"рассмотрено: файлов {c['files']}, листов {c['sheets']}, "
          f"строк {c['rows']}, оригиналов сверено {c['originals']} "
          f"(самотест пройден)")
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: заморозка данных соответствует контракту")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(
        description="Гейт заморозки данных CDPM")
    ap.add_argument("freeze_dir")
    ap.add_argument("--originals")
    args = ap.parse_args(argv)
    return run(args.freeze_dir, args.originals)


if __name__ == "__main__":
    raise SystemExit(main())
