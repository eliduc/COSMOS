# -*- coding: utf-8 -*-
"""Офлайн-калибровка измерителя по замороженным комплектам.

Ступени лестницы (`open-bench/ladder/`) проверяют меру на выдуманных
документах с известным ответом; этот прогон проверяет её на документах
РЕАЛЬНОГО вида — тех, что уже заморожены прошлыми прогонами. Ни одного
обращения к языковой модели и ни одного пересчёта: читаются готовые
клаузы заморозки и готовый свод, считаются структурные классы.

Комплектом считается каталог, в котором есть `freeze/clauses/*.json` и
свод — `extract/model.json` либо `exchange/versions/model-*.json`.
Каталог данных задаётся ключом и в репозиторий не попадает: наружу
выходят только числа, тексты документов авиакомпаний не печатаются и
не сохраняются.

    python open-bench/measure_kits.py --data <каталог> [--out отчёт.md]
                                      [--pattern "cdpm-runs/run-*"]

Код возврата всегда ноль: это измерение, а не замок. Замки строятся
поверх него отдельным решением.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from cdpm import measure as ms  # noqa: E402

DEFAULT_PATTERNS = ("cdpm-runs/run-*", "cdpm-*/2*")


def load_clauses(root: Path) -> list[dict]:
    out: list[dict] = []
    for f in sorted((root / "freeze" / "clauses").glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            continue
        for c in doc.get("clauses", []):
            out.append({"sid": doc.get("sid") or f.stem,
                        "anchor": c.get("anchor"),
                        "text": c.get("text") or ""})
    return out


def find_model(root: Path) -> Path | None:
    direct = root / "extract" / "model.json"
    if direct.exists():
        return direct
    vers = root / "exchange" / "versions"
    if vers.exists():
        arb = vers / "model-arbiter.json"
        if arb.exists():
            return arb
        got = sorted(vers.glob("model-v*.json"))
        if got:
            return got[-1]
    return None


def kits(data: Path, patterns) -> list[Path]:
    seen, out = set(), []
    for pat in patterns:
        for d in sorted(data.glob(pat)):
            if d.is_dir() and (d / "freeze" / "clauses").exists() \
                    and d not in seen:
                seen.add(d)
                out.append(d)
    return out


def measure_kit(root: Path) -> dict | None:
    mp = find_model(root)
    clauses = load_clauses(root)
    if mp is None or not clauses:
        return None
    model = json.loads(mp.read_text(encoding="utf-8"))
    if not (model.get("rules") or model.get("objectives")):
        return None
    rep = ms.measure(model, clauses)
    c = rep["counts"]
    norm = [r for r in rep["clauses"] if r["normative"]]
    return {
        "kit": f"{root.parent.name}/{root.name}",
        "clauses": c["clauses"], "carriers": c["carriers"],
        "composed": c["carriers_composed"], "rows": c["rows"],
        "orphan": c["rows_orphan"], "nonnorm": c["rows_on_nonnormative"],
        "nocarrier": c["fields_without_carrier"],
        "noslot": c["fields_without_carrier_slot"],
        "normative": len(norm),
        "normative_without_rows": c["normative_without_rows"],
        "unconsumed": c["unconsumed_values"],
        "unaddressed": c["unconsumed_without_addressee"],
        "unnamed": c["unconsumed_without_named_addressee"],
        "questions": c["questions"],
        "unclassified": c["unclassified_tokens"],
        "clean": ms.verdict(rep)[0],
    }


def render(rows: list[dict]) -> str:
    head = ("| комплект | клауз | носителей | строк | на ненорм. | без "
            "носителя | норм. без строк | остаток | без адресата | "
            "неразобр. |")
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines = ["# Калибровка измерителя на замороженных комплектах", "",
             "Только числа: содержание документов авиакомпаний не "
             "печатается.", "", head, sep]
    for r in rows:
        lines.append(
            f"| {r['kit']} | {r['clauses']} | {r['carriers']} | {r['rows']} "
            f"| {r['nonnorm']} | {r['nocarrier']} | "
            f"{r['normative_without_rows']} | {r['unconsumed']} | "
            f"{r['unaddressed']} | {r['unclassified']} |")
    tot = {k: sum(r[k] for r in rows) for k in
           ("clauses", "carriers", "rows", "nonnorm", "nocarrier",
            "normative_without_rows", "unconsumed", "unaddressed",
            "unclassified")} if rows else {}
    if tot:
        lines += ["", f"Итого по {len(rows)} комплектам: клауз "
                      f"{tot['clauses']}, носителей {tot['carriers']}, строк "
                      f"{tot['rows']}; на ненормативных носителях "
                      f"{tot['nonnorm']}; полей без носителя "
                      f"{tot['nocarrier']}; нормативных носителей без строк "
                      f"{tot['normative_without_rows']}; остаток "
                      f"{tot['unconsumed']}, из него без адресата "
                      f"{tot['unaddressed']}; неразобранных знаков "
                      f"{tot['unclassified']}."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True,
                    help="каталог данных с комплектами (вне репозитория)")
    ap.add_argument("--pattern", action="append", default=None,
                    help="образец пути комплекта; можно повторять")
    ap.add_argument("--out", default=None, help="файл отчёта")
    a = ap.parse_args(argv)
    rows = []
    for root in kits(Path(a.data), a.pattern or DEFAULT_PATTERNS):
        got = measure_kit(root)
        if got:
            rows.append(got)
    text = render(rows)
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"отчёт: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
