# -*- coding: utf-8 -*-
"""Калибровка уверенности посадок правил на шаблоны (пакет A/B/C/D,
решение владельца 2026-09-01): проценты confidence — ТОЛЬКО телеметрия;
этот отчёт сверяет заявленные категории и уверенности прогона стенда с
ИСТИНОЙ эталона М0 (поле family правил эталона) и строит таблицу
«заявленная уверенность -> фактическая точность» по каждой модели
раскладки. Числовой порог допускается к вводу лишь после того, как эта
кривая его оправдает, — отдельным решением владельца.

Вход: каталог прогона (extract/model.json + extract/fit-journal.jsonl)
и каталог эталона. Сопоставление правил свода с эталоном — по якорям
источников (та же механика, что у золотого диффа). Правила эталона без
поля family из калибровки исключаются поимённо (честно, не молча).

CLI:
  python -m tools.fit_calibration <run_dir> <etalon_dir> [out.md]
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp import golden_diff as gd  # noqa: E402

#: Бины заявленной уверенности для кривой калибровки.
BINS = ((0, 50), (50, 70), (70, 85), (85, 101))


class FitCalibrationError(ValueError):
    """Кривые входы калибровки — отказ словами."""


def load_journal(path: Path) -> list[dict]:
    """Журнал посадок: JSONL, каждая строка — голос одной роли
    {at, rule, role, category, confidence, model, registry_rev}."""
    if not path.is_file():
        raise FitCalibrationError(
            f"журнала посадок нет: {path} — прогон шёл до пакета "
            "посадки либо стадия извлечения не писала телеметрию")
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError as e:
                raise FitCalibrationError(
                    f"{path}: строка {i} не JSON: {e}") from e
            if not isinstance(row, dict) or not row.get("rule") \
                    or not row.get("role"):
                raise FitCalibrationError(
                    f"{path}: строка {i} без rule/role")
            rows.append(row)
    return rows


def truth_by_anchor(etalon: dict) -> tuple[dict, list[str]]:
    """Истина эталона: (sid|anchor) -> family. Правила эталона без
    family возвращаются вторым списком (исключены из калибровки)."""
    truth: dict[str, str] = {}
    unmarked: list[str] = []
    for r in etalon.get("rules") or []:
        fam = r.get("family")
        if not fam:
            unmarked.append(str(r.get("id")))
            continue
        for s in r.get("sources") or []:
            # тот же ключ, что у золотого диффа: sid|anchor заморозки
            truth["%s|%s" % (s.get("sid"), s.get("anchor"))] = fam
    return truth, unmarked


def rule_truth(rule: dict, truth: dict) -> str | None:
    """Истинная семья правила свода по якорям его источников;
    None — правило вне эталона (не судится)."""
    for s in rule.get("sources") or []:
        fam = truth.get("%s|%s" % (s.get("sid"), s.get("anchor")))
        if fam:
            return fam
    return None


def calibrate(model: dict, journal: list[dict],
              etalon: dict) -> dict:
    """Свод калибровки: по (роль, модель) — попадания/промахи посадок
    против истины эталона и кривая точности по бинам уверенности."""
    truth, unmarked = truth_by_anchor(etalon)
    rules_by_id = {str(r.get("id")): r
                   for r in model.get("rules") or []}
    out: dict = {"per_channel": {}, "skipped": [],
                 "etalon_unmarked": unmarked}
    for row in journal:
        rid = str(row.get("rule"))
        rule = rules_by_id.get(rid)
        if rule is None:
            out["skipped"].append(
                {"rule": rid, "why": "нет в model.json"})
            continue
        want_fam = rule_truth(rule, truth)
        if want_fam is None:
            out["skipped"].append(
                {"rule": rid, "why": "вне эталона (extra-правило)"})
            continue
        cat = str(row.get("category") or "")
        got_fam = ((rule.get("template_fit") or {}).get("family")
                   if cat in ("A", "B") else None)
        # Истина: правило эталона ИМЕЕТ семью, значит верная посадка —
        # категория A/B с той же семьёй; C/D или чужая семья — промах.
        hit = cat in ("A", "B") and got_fam == want_fam
        chan = "%s/%s" % (row.get("role"), row.get("model"))
        conf = row.get("confidence")
        ch = out["per_channel"].setdefault(
            chan, {"hits": 0, "misses": 0, "bins": {
                "%d-%d" % b: {"n": 0, "hits": 0} for b in BINS}})
        ch["hits" if hit else "misses"] += 1
        if isinstance(conf, (int, float)):
            for lo, hi in BINS:
                if lo <= conf < hi:
                    cell = ch["bins"]["%d-%d" % (lo, hi)]
                    cell["n"] += 1
                    cell["hits"] += 1 if hit else 0
                    break
    return out


def render(report: dict, header: str = "") -> str:
    lines = ["# Калибровка посадок против эталона М0", ""]
    if header:
        lines += [header, ""]
    for chan, ch in sorted(report["per_channel"].items()):
        total = ch["hits"] + ch["misses"]
        acc = (100.0 * ch["hits"] / total) if total else 0.0
        lines.append("## %s — точность %d/%d (%.0f%%)"
                     % (chan, ch["hits"], total, acc))
        lines.append("")
        lines.append("| Заявленная уверенность | Голосов | Точность |")
        lines.append("|---|---|---|")
        for b, cell in ch["bins"].items():
            pct = ("%.0f%%" % (100.0 * cell["hits"] / cell["n"])
                   if cell["n"] else "—")
            lines.append("| %s | %d | %s |" % (b, cell["n"], pct))
        lines.append("")
    if report["etalon_unmarked"]:
        lines.append("Правила эталона без разметки family (вне "
                     "калибровки): " +
                     ", ".join(report["etalon_unmarked"]))
    if report["skipped"]:
        lines.append("")
        lines.append("Пропущено голосов: %d (extra-правила вне "
                     "эталона и неизвестные id)"
                     % len(report["skipped"]))
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    # Обычная консоль Windows (cp1251/cp866) роняет кириллицу отчёта —
    # страж test_cli_console требует настройки у каждого печатающего CLI.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) < 3:
        print("usage: fit_calibration <run_dir> <etalon_dir> [out.md]")
        return 2
    run_dir, etalon_dir = Path(argv[1]), Path(argv[2])
    model = json.loads((run_dir / "extract" / "model.json")
                       .read_text(encoding="utf-8"))
    journal = load_journal(run_dir / "extract" / "fit-journal.jsonl")
    etalon = gd.load_etalon(etalon_dir)
    rep = calibrate(model, journal, etalon)
    text = render(rep, header="Прогон: %s" % run_dir)
    if len(argv) > 3:
        Path(argv[3]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
