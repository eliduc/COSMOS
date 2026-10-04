# -*- coding: utf-8 -*-
"""Гейт извлечения CDPM (пакет работ П-3).

Проверяет результат `cdpm.extract` против комплекта заморозки:

1. самотест: проверка кандидатов обязана ловить контрольный дефект
   (якорь вне комплекта);
2. модель проходит оба слоя контракта П-1 (схема + инварианты);
3. КАЖДЫЙ якорь трассировки — и у правил, и у вопросов — существует в
   комплекте заморозки: модель не может ссылаться на место, которого в
   замороженных источниках нет;
4. свод и анкета побайтно совпадают с пересборкой из модели;
5. отчёт отбраковки существует — отбраковка не бывает молчаливой.

Печатает число рассмотренных объектов (У-05) и ВЕРДИКТ; красный гейт —
код возврата 1.

Запуск (полными путями, из любого каталога):
    python -m cdpm.gates.gate_extract КОМПЛЕКТ_ЗАМОРОЗКИ КАТАЛОГ_ВЫХОДА
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cdpm import extract, model as m, render


def _selftest() -> None:
    reason = extract._check_candidate(
        {"anchor": "п.НЕТ", "title": "х", "statement": "х",
         "zone": "PAIRING", "kind": "HARD", "question": None},
        known_anchors={"п.1"})
    assert reason and "отсутствует в комплекте" in reason, \
        "самотест: чужой якорь не пойман"
    assert extract._check_candidate(
        {"anchor": "п.1", "title": "х", "statement": "х",
         "zone": "PAIRING", "kind": "HARD", "question": None},
        known_anchors={"п.1"}) is None, \
        "самотест: чистый кандидат отбракован"


def check(kit_dir: Path, out_dir: Path) -> tuple[list[str], dict]:
    problems: list[str] = []
    counters = {"rules": 0, "questions": 0, "objectives": 0,
                "anchors_checked": 0, "renders": 0, "rejected": 0}

    model_path = out_dir / "model.json"
    if not model_path.is_file():
        return [f"модель {model_path} не существует"], counters
    doc = m.load_model(model_path)

    problems += m.validate_schema(doc)
    problems += m.validate_model(doc)
    counters["rules"] = len(doc.get("rules", []))
    counters["questions"] = len(doc.get("questions", []))
    counters["objectives"] = len(doc.get("objectives", []))

    kit_anchors: dict[str, set[str]] = {}
    for entry in extract._load_kit(kit_dir):
        kit_anchors[entry["sid"]] = {c["anchor"]
                                     for c in entry["clauses"]}

    def _check_refs(owner: str, refs: list[dict]):
        for ref in refs:
            counters["anchors_checked"] += 1
            known = kit_anchors.get(ref.get("sid"), set())
            if ref.get("anchor") not in known:
                problems.append(
                    f"{owner}: трассировка {ref.get('sid')}/"
                    f"{ref.get('anchor')!r} не существует в комплекте "
                    f"заморозки")

    for r in doc.get("rules", []):
        _check_refs(r.get("id", "<правило>"), r.get("sources", []))
    for q in doc.get("questions", []):
        _check_refs(q.get("id", "<вопрос>"), q.get("source_refs", []))
    for o in doc.get("objectives", []):
        _check_refs(o.get("id", "<целевая>"), o.get("sources", []))

    for name, renderer in (("svod.md", render.render_svod),
                           ("anketa.md", render.render_anketa)):
        path = out_dir / name
        if not path.is_file():
            problems.append(f"{path} не существует")
        elif path.read_text(encoding="utf-8") != renderer(doc):
            problems.append(f"{name}: не совпадает с пересборкой из "
                            f"модели — правлен мимо модели либо устарел")
        else:
            counters["renders"] += 1

    rej_path = out_dir / "rejections.json"
    if not rej_path.is_file():
        problems.append(f"отчёт отбраковки {rej_path} не существует — "
                        f"отбраковка не должна быть молчаливой")
    else:
        rej = json.loads(rej_path.read_text(encoding="utf-8"))
        counters["rejected"] = len(rej.get("rejected", []))
    return problems, counters


def run(kit_dir: str, out_dir: str) -> int:
    _selftest()
    problems, c = check(Path(kit_dir), Path(out_dir))
    print(f"рассмотрено: правил {c['rules']}, вопросов {c['questions']}, "
          f"целевых {c['objectives']}, якорей трассировки сверено "
          f"{c['anchors_checked']}, рендеров сверено {c['renders']}; "
          f"отбраковано при извлечении {c['rejected']} "
          f"(самотест пройден)")
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: результат извлечения соответствует контракту")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14: печать кириллицы
    ap = argparse.ArgumentParser(description="Гейт извлечения CDPM")
    ap.add_argument("kit", help="каталог комплекта заморозки")
    ap.add_argument("out", help="каталог результата извлечения")
    args = ap.parse_args(argv)
    return run(args.kit, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
