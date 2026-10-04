# -*- coding: utf-8 -*-
"""Оценка проверок постановки на ступенях стенда.

Ступень судит проверку, а не наоборот: приговор ступени (expectation:
вердикт и перечень находок «класс, уровень, часть, строка, поле»)
написан вместе с материалом. Проверка сошлась, когда её находки
совпадают с приговором как мультимножество: ни одной лишней (ложная
тревога), ни одной недостающей (молчаливый пропуск), и вердикт тот же.

Ступень с requires_solver судится решателем, не проверками; здесь
для неё проверяется только, что проверки постановки её ПРИНИМАЮТ
(класс не их), а прогон решателя — отдельно, по отмашке владельца.

    python open-bench/vmp-stand/evaluate_stand.py            # из памяти
    python open-bench/vmp-stand/evaluate_stand.py <каталог>  # из файлов

Код возврата 0 — все ступени сошлись.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from vmp import formulation_check as fc  # noqa: E402

KEY = ("class", "level", "part", "id", "field")


def _key(f: dict) -> tuple:
    # Пустая часть и пустая строка — «» (ключи сравнимы при сортировке).
    return tuple("" if f.get(k) is None else str(f.get(k)) for k in KEY)


def evaluate(pkg: dict, expectation: dict) -> dict:
    """Сравнение находок проверки с приговором ступени."""
    result = fc.check(pkg)
    got = sorted(_key(f) for f in result["findings"])
    want = sorted(_key(f) for f in expectation.get("findings") or [])
    missing = list(want)
    extra = []
    for k in got:
        if k in missing:
            missing.remove(k)
        else:
            extra.append(k)
    ok = (result["verdict"] == expectation["verdict"]
          and not missing and not extra)
    return {"ok": ok, "verdict": result["verdict"],
            "expected_verdict": expectation["verdict"],
            "missing": missing, "extra": extra,
            "requires_solver": bool(expectation.get("requires_solver")),
            "solver_verdict": expectation.get("solver_verdict"),
            "result": result}


def _fmt_key(k: tuple) -> str:
    cls, level, part, rid, field = k
    where = "%s/%s" % (part, rid) if part else str(rid)
    return "%s[%s] %s.%s" % (cls, level, where, field)


def run_all(source: Path | None = None) -> int:
    if source is None:
        import stand
        steps = stand.build_all()
    else:
        steps = []
        for d in sorted(p for p in source.iterdir() if p.is_dir()):
            exp = d / "expectation.json"
            if not exp.is_file():
                continue
            steps.append((d.name, fc.load_package_dir(d),
                          json.loads(exp.read_text(encoding="utf-8"))))
    failed = 0
    for name, pkg, expectation in steps:
        ev = evaluate(pkg, expectation)
        mark = "сошлась" if ev["ok"] else "РАЗОШЛАСЬ"
        note = ""
        if ev["requires_solver"]:
            note = " (приговор решателя %s — отдельный прогон по отмашке)" \
                % ev["solver_verdict"]
        print("%-4s %-52s %s: вердикт %s / ожидался %s%s"
              % (name, expectation.get("title", ""), mark, ev["verdict"],
                 ev["expected_verdict"], note))
        for k in ev["missing"]:
            print("       пропущено: " + _fmt_key(k))
        for k in ev["extra"]:
            print("       лишнее:    " + _fmt_key(k))
        if not ev["ok"]:
            failed += 1
    print("итог: ступеней %d, разошлось %d" % (len(steps), failed))
    return 1 if failed else 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return run_all(Path(argv[0]) if argv else None)


if __name__ == "__main__":
    sys.exit(main())
