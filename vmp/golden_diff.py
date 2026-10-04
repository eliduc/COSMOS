# -*- coding: utf-8 -*-
"""Дифф свода прогона против «золотого» эталона набора (М0).

Системное лечение изменчивости формализации (решение владельца
2026-08-31, пункт 2): каждый набор стенда «Полярис» рождён из
эталонной модели M0 (open-bench/<набор>/etalon/M0-model.json) —
свод любого прогона обязан покрывать её правила. Дифф — машинное
сравнение по ЯКОРЯМ ЗАМОРОЗКИ (sid+anchor), а не по id: id свода
изменчивы, якоря — нет.

Сверяется, по каждому правилу эталона:
* ПОКРЫТИЕ — в своде есть хотя бы одно правило с его якорем;
* ВИД — жёсткое правило эталона не смягчено без следа ответа
  (допустимые пары видов — «совместимость», не равенство:
  HARD может стать CASE/DEF, DATA-справка — остаться данными);
* ЧИСЛА — числовые токены формулы эталона находятся в машинных
  параметрах или тексте правила свода (эвристика, вердикт
  «полное/частичное/нет»).

Правила свода с якорями ВНЕ эталона перечисляются отдельно
(«сверх эталона») — они законны (строки данных, уточнения), но
обязаны быть видны.

Итог: вердикт GREEN (всё покрыто, виды совместимы, числа полные) /
AMBER (есть частичные числа или сверхэталонные жёсткие правила) /
RED (эталонное правило не покрыто или вид несовместим).

CLI:
    python -m vmp.golden_diff MODEL.json ETALON_DIR [--out report.md]
Код возврата: 0 = GREEN/AMBER, 1 = RED, 2 = кривые входы.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

#: Совместимость видов: эталонный вид -> допустимые виды свода.
KIND_COMPAT = {
    "HARD": {"HARD", "CASE", "DEF"},
    "SOFT": {"SOFT"},
    "DEF": {"DEF", "HARD", "DATA"},
    "DATA": {"DATA"},
    "CASE": {"CASE", "HARD", "SOFT"},
    "OUT": {"OUT", "DATA"},
}

_NUM_RE = re.compile(r"\d+(?::\d+)?")


class GoldenDiffError(ValueError):
    """Кривые входы диффа — отказ словами."""


def _anchor_key(src: dict) -> str:
    return f"{src.get('sid')}|{src.get('anchor')}"


def _num_groups(text: str) -> list[set[str]]:
    """Числовые токены формулы ГРУППАМИ-альтернативами: «12:00» и
    «720» — одно значение в двух написаниях, группа удовлетворена
    любым из них."""
    out: list[set[str]] = []
    for tok in _NUM_RE.findall(text or ""):
        grp = {tok}
        if ":" in tok:
            h, m = tok.split(":", 1)
            try:
                grp.add(str(int(h) * 60 + int(m)))
            except ValueError:
                pass
        if grp not in out:
            out.append(grp)
    return out


def _nums_of(text: str) -> set[str]:
    out: set[str] = set()
    for grp in _num_groups(text):
        out |= grp
    return out


def _rule_numbers(rule: dict) -> set[str]:
    out: set[str] = set()
    for p in rule.get("params") or ():
        if isinstance(p, dict) and p.get("value") is not None:
            out.add(str(p["value"]))
    out |= _nums_of(str(rule.get("statement") or ""))
    out |= _nums_of(str(rule.get("note") or ""))
    return out


def load_etalon(etalon_dir: Path) -> dict:
    path = Path(etalon_dir) / "M0-model.json"
    if not path.is_file():
        raise GoldenDiffError(f"нет эталона: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def diff(model: dict, etalon: dict) -> dict:
    """Машинный дифф. Возврат: {verdict, rows, extra, counts}."""
    if not isinstance(model, dict) or not isinstance(etalon, dict):
        raise GoldenDiffError("модель и эталон должны быть объектами")
    run_rules = [r for r in model.get("rules") or []
                 if isinstance(r, dict)]
    by_anchor: dict[str, list[dict]] = {}
    for r in run_rules:
        for s in r.get("sources") or ():
            by_anchor.setdefault(_anchor_key(s), []).append(r)

    rows: list[dict] = []
    used_run_ids: set[str] = set()
    for e in etalon.get("rules") or []:
        anchors = [_anchor_key(s) for s in e.get("sources") or ()]
        hits = []
        for a in anchors:
            hits.extend(by_anchor.get(a, ()))
        # одно правило свода может нести несколько якорей — уникально
        seen: set[str] = set()
        hits = [h for h in hits
                if not (str(h.get("id")) in seen
                        or seen.add(str(h.get("id"))))]
        row = {"etalon_id": e.get("id"),
               "title": e.get("title"),
               "anchors": anchors,
               "covered": bool(hits),
               "run_ids": [str(h.get("id")) for h in hits],
               "kind_ok": None, "numbers": None, "fit": None}
        if hits:
            used_run_ids.update(row["run_ids"])
            allowed = KIND_COMPAT.get(str(e.get("kind")),
                                      {str(e.get("kind"))})
            row["kind_ok"] = any(str(h.get("kind")) in allowed
                                 for h in hits)
            # Посадочный слой (пакет A/B/C/D): эталон с разметкой
            # family судит канонические посадки точно — совпадение
            # семьи у A/B-посадки сильнее эвристики видов; посадка в
            # ЧУЖУЮ семью — красный, даже когда вид совместим.
            want_fam = e.get("family")
            if want_fam:
                fams = [((h.get("template_fit") or {}).get("family"))
                        for h in hits
                        if (h.get("template_fit") or {})
                        .get("category") in ("A", "B")]
                if any(f == want_fam for f in fams):
                    row["fit"] = "точная"
                    row["kind_ok"] = True
                elif fams:
                    row["fit"] = ("чужая семья: %s вместо %s"
                                  % (", ".join(sorted(
                                      {str(f) for f in fams})),
                                     want_fam))
                    row["kind_ok"] = False
                # без посадок — прежний путь по видам, fit=None
            groups = _num_groups(str(e.get("statement") or ""))
            if groups:
                got: set[str] = set()
                for h in hits:
                    got |= _rule_numbers(h)
                missing = [g for g in groups if not (g & got)]
                row["numbers"] = ("полное" if not missing else
                                  ("частичное"
                                   if len(missing) < len(groups)
                                   else "нет"))
                row["numbers_missing"] = sorted(
                    min(g) for g in missing)
            else:
                row["numbers"] = "нет чисел в эталоне"
        rows.append(row)

    extra = [{"id": str(r.get("id")), "kind": r.get("kind"),
              "title": r.get("title")}
             for r in run_rules
             if str(r.get("id")) not in used_run_ids]

    red = [r for r in rows
           if not r["covered"] or r["kind_ok"] is False]
    amber = [r for r in rows
             if r["covered"]
             and r.get("numbers") in ("частичное", "нет")]
    extra_hard = [x for x in extra if x.get("kind") == "HARD"]
    verdict = ("RED" if red else
               ("AMBER" if amber or extra_hard else "GREEN"))
    return {"verdict": verdict, "rows": rows, "extra": extra,
            "counts": {"etalon_rules": len(rows),
                       "covered": sum(1 for r in rows if r["covered"]),
                       "red": len(red), "amber": len(amber),
                       "extra": len(extra),
                       "extra_hard": len(extra_hard)}}


def render_report(res: dict, header: str = "") -> str:
    c = res["counts"]
    lines = ["# Дифф свода против золотого эталона (М0)", ""]
    if header:
        lines += [header, ""]
    lines += [f"Вердикт: **{res['verdict']}** — правил эталона "
              f"{c['etalon_rules']}, покрыто {c['covered']}, "
              f"красных {c['red']}, янтарных {c['amber']}, "
              f"сверх эталона {c['extra']} "
              f"(из них жёстких {c['extra_hard']})", ""]
    for r in res["rows"]:
        fit = r.get("fit")
        mark = ("НЕ ПОКРЫТО" if not r["covered"] else
                ("ПОСАДКА В ЧУЖУЮ СЕМЬЮ" if fit and fit != "точная"
                 else ("ВИД НЕСОВМЕСТИМ" if r["kind_ok"] is False else
                       ("ЧИСЛА ЧАСТИЧНО"
                        if r.get("numbers") == "частичное"
                        else ("ОК, ПОСАДКА" if fit == "точная"
                              else "ОК")))))
        lines.append(f"- [{mark}] {r['etalon_id']} {r['title']} -> "
                     f"{', '.join(r['run_ids']) or '—'}"
                     + (f"; {fit}" if fit and fit != "точная" else "")
                     + (f"; не найдены числа: "
                        f"{', '.join(r['numbers_missing'])}"
                        if r.get("numbers_missing") else ""))
    if res["extra"]:
        lines += ["", "Сверх эталона (законно, но видимо):"]
        lines += [f"- {x['id']} [{x['kind']}] {x['title']}"
                  for x in res["extra"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Дифф свода против М0")
    ap.add_argument("model")
    ap.add_argument("etalon_dir")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    try:
        model = json.loads(Path(args.model).read_text(encoding="utf-8"))
        res = diff(model, load_etalon(Path(args.etalon_dir)))
    except (GoldenDiffError, OSError, json.JSONDecodeError) as e:
        print(f"ОТКАЗ: {e}")
        return 2
    text = render_report(res, header=f"Свод: {args.model}")
    if args.out:
        io.open(args.out, "w", encoding="utf-8").write(text)
    print(text)
    return 1 if res["verdict"] == "RED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
