# -*- coding: utf-8 -*-
"""Ступень ИДАК-0 набора «ИД H0-ВМП»: три файла → ВМП H0 без языковой
модели (кроме зрения).

Шаги: штатная заморозка docx/xlsx/jpg (cdpm.freeze) → чтение
канонической текстовой формы кодом (cdpm.form_text) → форма →
проверки формы (строго) → машинная модель → контракт модели → гейт
эквивалентности против эталона (vmp.equivalence, строгий режим).

Зрение: по умолчанию --vision offline — абзацы jpg берутся из
etalon/jpg-lines.json (строки, отрисованные генератором набора; живое
зрение opus-5 в прогоне №3 прочло их без расхождений); --vision cloud —
живой облачный канал зрения (запуск — по слову владельца).

    python open-bench/polyaris-h0/step0_form_text.py --out <каталог>
Код возврата 0 только при: 0 нераспознанных клауз, форма строго чиста,
контракт модели 0, вердикт EQUIVALENT.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
from cdpm import form_text as ft  # noqa: E402
from cdpm import freeze as fz  # noqa: E402
from cdpm import model as m  # noqa: E402
from cdpm import vmp_form as vf  # noqa: E402
from vmp import equivalence as eq  # noqa: E402


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="каталог прогона (вне git)")
    ap.add_argument("--vision", choices=("offline", "cloud"), default="offline")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((HERE / "kit-manifest.json").read_text(encoding="utf-8"))
    paths = [HERE / p for p in manifest["rules_files"]]
    etalon = json.loads((REPO / manifest["etalon_vmp"]).read_text(encoding="utf-8"))
    etalon_form = json.loads((REPO / manifest["etalon_form"]).read_text(encoding="utf-8"))

    if a.vision == "offline":
        lines = json.loads((HERE / "etalon" / "jpg-lines.json").read_text(encoding="utf-8"))

        def vision(_ask, _path):
            return json.dumps({"paragraphs": lines}, ensure_ascii=False)
    else:
        from cdpm import llm as llm_mod
        vision = llm_mod.vision_channel("any", requested="cloud")

    # 1. Заморозка — штатная.
    fman = fz.freeze(paths, out / "freeze", vision_llm=vision)
    entries = [json.loads((out / "freeze" / "clauses" / f"{f['sid']}.json")
                          .read_text(encoding="utf-8")) for f in fman["files"]]
    n_clauses = sum(len(e["clauses"]) for e in entries)

    # 2. Чтение канонической формы кодом.
    form, rep = ft.read_form_text(entries)
    (out / "form.json").write_text(json.dumps(form, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    (out / "read-report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    form_problems = vf.validate_form(form, strict=True)

    # 3. Модель и контракт.
    model = ft.model_from_form_text(form)
    (out / "model.json").write_text(json.dumps(model, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    contract = (m.validate_schema(model) + m.validate_model(model)
                + m.validate_template_fit(model))

    # 4. Гейт эквивалентности (строгий режим) + прямая сверка форм.
    res = eq.equivalence(model, etalon, strict_questions=True)
    report_md = eq.render_report(res, header=f"Кандидат: ступень ИДАК-0 без LLM "
                                              f"(зрение: {a.vision}); набор "
                                              f"{[p.name for p in paths]}")
    (out / "equivalence.md").write_text(report_md, encoding="utf-8")
    forms_direct = eq.diff_forms(form, etalon_form, strict_questions=True)["problems"]

    ok = (not rep["unrecognized"] and not form_problems and not contract
          and res["verdict"] == "EQUIVALENT" and not forms_direct)
    c = res["counts"]
    print(f"Заморозка: файлов {len(entries)}, клауз {n_clauses} "
          f"({', '.join(str(len(e['clauses'])) for e in entries)})")
    print(f"Чтение формы: заголовков {rep['headers']}, разделов {rep['headings']}, "
          f"записей {rep['rows']}, НЕ РАСПОЗНАНО {len(rep['unrecognized'])}")
    for u in rep["unrecognized"]:
        print(f"   ? {u['sid']} {u['anchor']}: {u['text'][:120]}")
    print(f"Форма (строго): нарушений {len(form_problems)}")
    for p in form_problems:
        print("   -", p)
    print(f"Контракт модели: нарушений {len(contract)}")
    for p in contract:
        print("   -", p)
    print(f"Гейт: {res['verdict']} — правил {c['covered']}/{c['etalon_rules']}, "
          f"сверх {c['extra']}, целевых {c['objectives_ok']}/{c['objectives_etalon']}, "
          f"вопросов {c['questions_total']}, форма {c['form_rows_ok']}/{c['form_rows']}, "
          f"нарушений {c['problems']}")
    for p in res["problems"]:
        print("   -", p)
    print(f"Форма ↔ форма эталона напрямую: нарушений {len(forms_direct)}")
    for p in forms_direct:
        print("   -", p)
    print("ИТОГ:", "СТУПЕНЬ ИДАК-0 ПРОЙДЕНА БЕЗ LLM" if ok else "НЕ ПРОЙДЕНА")
    print(f"Артефакты: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
