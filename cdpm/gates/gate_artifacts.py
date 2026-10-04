# -*- coding: utf-8 -*-
"""Гейт выходных артефактов CDPM (пакет работ П-1).

Проверяет машинную модель и, если поданы, отрендеренные md-артефакты:

1. самотест: валидатор обязан поймать контрольный дефект — гейт, не
   способный найти ни одной ошибки, не проверка (урок методики §6);
2. структурный слой: модель против опубликованной JSON-схемы;
3. инвариантный слой: перекрёстные ссылки и правила методики
   (`cdpm.model.validate_model`) — трассировка у каждого правила,
   развилки вместо выборов, ветвь на каждый ответ, дубли вопросов;
4. рендеры: md-свод и md-анкета побайтно совпадают с пересборкой из
   модели — артефакт, правленный мимо модели, не проходит.

Печатает, сколько объектов рассмотрел (У-05), и ВЕРДИКТ; красный гейт —
код возврата 1.

Запуск (полными путями, из любого каталога):
    python -m cdpm.gates.gate_artifacts model.json \
        [--svod svod.md] [--anketa anketa.md]
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

from cdpm import model as m
from cdpm import render
from cdpm import sample


def _selftest() -> None:
    """Контрольные дефекты, которые валидатор ОБЯЗАН поймать. Ломается
    валидатор — гейт падает до проверки живой модели."""
    doc = sample.sample_model()
    assert m.validate_schema(doc) == [], "самотест: образец не по схеме"
    assert m.validate_model(doc) == [], "самотест: образец не проходит " \
                                        "инварианты"
    broken = copy.deepcopy(doc)
    broken["rules"][0]["sources"] = []          # правило без трассировки
    broken["questions"][1]["answer_format"]["type"] = "text"  # ч.2 без
    broken["questions"][1]["answer_format"].pop("options", None)  # развилки
    problems = m.validate_model(broken)
    assert any("без трассировки" in p for p in problems), \
        "самотест: потеря трассировки не поймана"
    assert any("требует развилки" in p for p in problems), \
        "самотест: выбор вместо развилки не пойман"


def run(model_path: str, svod_path: str | None = None,
        anketa_path: str | None = None) -> int:
    _selftest()
    doc = m.load_model(model_path)

    problems = m.validate_schema(doc) + m.validate_model(doc)

    rendered = {}
    if svod_path:
        rendered["свод"] = (Path(svod_path), render.render_svod(doc))
    if anketa_path:
        rendered["анкета"] = (Path(anketa_path), render.render_anketa(doc))
    for name, (path, expected) in rendered.items():
        if not path.is_file():
            problems.append(f"{name}: файл {path} не существует")
        elif path.read_text(encoding="utf-8") != expected:
            problems.append(
                f"{name}: {path} не совпадает с пересборкой из модели — "
                f"артефакт правлен мимо модели либо устарел")

    c = m.count_objects(doc)
    per_part = ", ".join(f"ч.{p}: {n}"
                         for p, n in c["questions_per_part"].items() if n)
    print(f"рассмотрено: источников {c['sources']}, правил {c['rules']}, "
          f"вопросов {c['questions']} ({per_part or 'нет'}), "
          f"ссылок {c['links']}; блокированных строк {c['blocked_rules']}, "
          f"условных {c['conditional_rules']}; "
          f"рендеров сверено {len(rendered)} (самотест пройден)")

    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: артефакты соответствуют контракту")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14: печать кириллицы
    ap = argparse.ArgumentParser(
        description="Гейт выходных артефактов CDPM")
    ap.add_argument("model", help="путь к model.json")
    ap.add_argument("--svod", help="путь к отрендеренному svod.md")
    ap.add_argument("--anketa", help="путь к отрендеренной anketa.md")
    args = ap.parse_args(argv)
    return run(args.model, args.svod, args.anketa)


if __name__ == "__main__":
    raise SystemExit(main())
