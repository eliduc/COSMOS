# -*- coding: utf-8 -*-
"""Оценщик ступени лестницы: измеритель против таблицы ожиданий.

Судит НЕ свод, а сам измеритель (cdpm/measure.py): верно ли он делит
клаузы на нормативные и служебные и все ли ожидаемые величины
распознаёт. Таблица ожиданий составлена вручную вместе с текстами
ступени и коду измерителя не известна — иначе проверка была бы
самоподтверждением.

Метрики ступени:
  * нормативность: верных вердиктов, ложных норм (служебная клауза
    признана нормой — класс «заголовок стал правилом»), ложных
    отказов (норма признана служебной — класс «правило потеряно»);
  * величины: найдено из ожидаемых, не найдено (потеря величины),
    неразобранные числоподобные знаки (слепота разбора).

    python open-bench/ladder/evaluate_ladder.py <каталог ступени>
Код возврата 0 — ложных норм и ложных отказов нет и все ожидаемые
величины найдены; иначе 1 с поимённым перечнем.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from cdpm import measure as ms  # noqa: E402


def _found(value_text: str, values: list) -> bool:
    """Ожидаемая величина найдена, если её текст совпал с текстом
    распознанной величины либо вошёл в него (единица могла быть взята
    шире: «8 условных единиц» ⊇ «8 условных»)."""
    want = ms._norm(value_text)
    for v in values:
        got = ms._norm(v.text)
        if want == got or want in got or got in want:
            return True
    return False


def evaluate(step_dir: Path) -> dict:
    clauses = json.loads((step_dir / "clauses.json").read_text(
        encoding="utf-8"))["clauses"]
    expect = json.loads((step_dir / "expectations.json").read_text(
        encoding="utf-8"))
    # Судится НОСИТЕЛЬ: клетки таблицы собираются в строку с шапкой, а
    # абзац остаётся сам себе носителем (cdpm.measure.compose_carriers).
    by_key = {f"{c['sid']}|{c['anchor']}": c for c in clauses}
    _, carrier_text, _, roles = ms.compose_carriers(by_key)
    by_anchor = {k.split("|", 1)[1]: (t, roles.get(k))
                 for k, t in carrier_text.items()}
    rows = []
    false_norm, false_service, missed_values = [], [], []
    unclassified = 0
    for e in expect["expect"]:
        got = by_anchor.get(e["anchor"])
        if got is None:
            continue
        text, role = got
        c = {"text": text}
        verdict = ms.normativity(text, role)
        values = [v for v in ms.iter_values(c["text"])
                  if v.kind not in ("unclassified", "requisite")]
        unclassified += sum(1 for v in ms.iter_values(c["text"])
                            if v.kind == "unclassified")
        ok_norm = bool(verdict["normative"]) == bool(e["normative"])
        if not ok_norm and verdict["normative"]:
            false_norm.append({"anchor": e["anchor"], "why": e["why"],
                               "text": c["text"][:110]})
        if not ok_norm and not verdict["normative"]:
            false_service.append({"anchor": e["anchor"], "why": e["why"],
                                  "missing": verdict["missing"],
                                  "text": c["text"][:110]})
        miss = [w for w in e["values"] if not _found(w, values)]
        if miss:
            missed_values.append({"anchor": e["anchor"], "missing": miss,
                                  "found": [v.text for v in values]})
        rows.append({"anchor": e["anchor"], "role": e["role"],
                     "expected_normative": e["normative"],
                     "got_normative": verdict["normative"],
                     "values_expected": len(e["values"]),
                     "values_found": len(e["values"]) - len(miss)})
    total = len(rows)
    # Баланс на эталонном своде ступени: у безошибочного разбора
    # структурные классы обязаны быть нулевыми, а весь остаток —
    # закрытым вопросом. Это проверка меры целиком, а не только её
    # суждения о тексте.
    balance = None
    model_path = step_dir / "model.json"
    if model_path.exists():
        model = json.loads(model_path.read_text(encoding="utf-8"))
        rep = ms.measure(model, clauses)
        c = rep["counts"]
        balance = {
            "rows": c["rows"], "rows_orphan": c["rows_orphan"],
            "rows_on_nonnormative": c["rows_on_nonnormative"],
            "fields_without_carrier": c["fields_without_carrier"],
            "unconsumed": c["unconsumed_values"],
            "unconsumed_without_addressee":
                c["unconsumed_without_addressee"],
            "clean": ms.verdict(rep)[0],
            "problems": ms.verdict(rep)[1],
        }
    return {
        "step": expect.get("step"),
        "balance": balance,
        "counts": {
            "clauses": total,
            "normativity_ok": sum(1 for r in rows
                                  if r["expected_normative"]
                                  == r["got_normative"]),
            "false_normative": len(false_norm),
            "false_service": len(false_service),
            "values_expected": sum(r["values_expected"] for r in rows),
            "values_missed": sum(len(x["missing"]) for x in missed_values),
            "unclassified_tokens": unclassified,
        },
        "false_normative": false_norm,
        "false_service": false_service,
        "missed_values": missed_values,
        "rows": rows,
    }


def render(rep: dict) -> str:
    c = rep["counts"]
    b = rep.get("balance") or {}
    ok = (c["false_normative"] == 0 and c["false_service"] == 0
          and c["values_missed"] == 0 and b.get("clean", True))
    lines = [f"# Ступень {rep['step']}: измеритель против ожиданий", "",
             f"Вердикт: **{'ЧИСТО' if ok else 'ОТКЛОНЕНИЯ'}**", "",
             f"- клауз: {c['clauses']}; вердикт нормативности верен: "
             f"{c['normativity_ok']}",
             f"- ложных норм (служебное принято за норму): "
             f"{c['false_normative']}",
             f"- ложных отказов (норма принята за служебное): "
             f"{c['false_service']}",
             f"- ожидаемых величин: {c['values_expected']}; не найдено: "
             f"{c['values_missed']}",
             f"- неразобранных числоподобных знаков: "
             f"{c['unclassified_tokens']}", ""]
    if b:
        lines += [
            f"- эталонный свод ступени: строк {b['rows']}, без клаузы "
            f"{b['rows_orphan']}, на ненормативных {b['rows_on_nonnormative']}"
            f", полей без носителя {b['fields_without_carrier']}",
            f"- остаток на эталонном своде: {b['unconsumed']}, из него без "
            f"адресата: {b['unconsumed_without_addressee']}", ""]
        for p in b.get("problems") or ():
            lines.append(f"- БАЛАНС: {p}")
        if b.get("problems"):
            lines.append("")
    for x in rep["false_normative"]:
        lines.append(f"- ЛОЖНАЯ НОРМА {x['anchor']} ({x['why']}): "
                     f"«{x['text']}»")
    for x in rep["false_service"]:
        lines.append(f"- ЛОЖНЫЙ ОТКАЗ {x['anchor']} ({x['why']}): нет "
                     f"признаков {', '.join(x['missing'])} — «{x['text']}»")
    for x in rep["missed_values"]:
        lines.append(f"- НЕ НАЙДЕНЫ ВЕЛИЧИНЫ {x['anchor']}: "
                     f"{', '.join(x['missing'])} (найдено: "
                     f"{', '.join(x['found']) or '—'})")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step_dir", help="каталог ступени (clauses.json + "
                                     "expectations.json)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = evaluate(Path(a.step_dir))
    text = render(rep)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    c = rep["counts"]
    b = rep.get("balance") or {}
    return 0 if (c["false_normative"] == 0 and c["false_service"] == 0
                 and c["values_missed"] == 0
                 and b.get("clean", True)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
