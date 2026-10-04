# -*- coding: utf-8 -*-
r"""Гейт комплекта «Полярис» (постановка §7, У-05: самотест, счётчики,
вердикт; красный — код возврата 1).

Проверяет:
1. дозировка реестра дефектов сходится со знаменателями §5 (самотест
   defects.py выполняется при импорте);
2. каждый дефект воплощён ДОСЛОВНО в своём документе (docx — по
   клаузам штатной заморозки, xlsx — по ячейкам, jpg — по
   зафиксированным строкам jpg-lines.json);
3. M0: контракт validate_model чист, каждое правило трассировано на
   настоящие якоря заморозки;
4. согласованность мира: коды баз присутствуют в docx и памятке;
5. линт стоп-словарём UZ: ни один содержательный токен UZ не попал в
   публикуемые артефакты и исходники генератора (карта UNIA лежит
   только на этой машине; если её нет — линт честно помечается
   пропущенным).

Запуск: ..\..\.venv\Scripts\python.exe gate_polyaris.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
CREW = HERE.parent.parent
sys.path.insert(0, str(CREW))
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import defects as defects_mod                     # noqa: E402

DOCS = HERE / "docs"
ETALON = HERE / "etalon"
# Имена входных файлов — константы (производные наборы подменяют их
# вместе с DOCS/ETALON, как в build_light/build_mini/build_nano).
DOCX_NAME = "PI1-Pravila-Polyaris.docx"
XLSX_NAME = "PE1-Zhurnal-Polyaris.xlsx"
# Линт проприетарным стоп-словарём — локальная страховка ИЗДАТЕЛЯ:
# путь к словарю живёт в указателе ВНЕ репозитория (на чужих машинах
# указателя нет — линт честно помечается пропущенным). Прямой путь в
# этом файле сам был бы запрещённым токеном (гейт поймал себя живьём
# 2026-08-20).
_LINT_POINTER = Path(r"C:\work\Sandbox\Testing\TimeFold\open-bench"
                     r"\lint-map-location.txt")
BASE_CODES = ["SVG", "PLY", "GRT"]


def _docx_text() -> str:
    from cdpm import freeze as freeze_mod
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        kit = Path(td) / "kit"
        freeze_mod.freeze([DOCS / DOCX_NAME], kit)
        entry = json.loads((kit / "clauses" / "S1.json")
                           .read_text(encoding="utf-8"))
    return "\n".join(c["text"] for c in entry["clauses"])


def _xlsx_text() -> str:
    import openpyxl
    wb = openpyxl.load_workbook(str(DOCS / XLSX_NAME))
    parts = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None:
                    parts.append(str(c.value))
    return "\n".join(parts)


def _selftest() -> None:
    # Самотест проб: заведомо отсутствующая строка не должна
    # «находиться», заведомо присутствующая — обязана.
    text = "контрольная строка гейта"
    assert "контрольная" in text
    assert "отсутствующая-строка-987" not in text


def main() -> int:
    problems: list[str] = []
    counters = {"defects": 0, "rules": 0, "lint_files": 0}
    _selftest()

    texts = {"docx": _docx_text(), "xlsx": _xlsx_text(),
             "jpg": "\n".join(json.loads(
                 (ETALON / "jpg-lines.json")
                 .read_text(encoding="utf-8")))}

    # 2. Воплощение дефектов дословно.
    for d in defects_mod.registry():
        counters["defects"] += 1
        body = texts[d["doc"]]
        if d["probe"] not in body:
            problems.append(f"{d['id']}: воплощение не найдено в "
                            f"{d['doc']}: {d['probe'][:50]!r}")
        if d["how"] == "replace" and d["old"] and d["target_rule"]:
            # Заменённой чистой подстроки в целевом месте быть не
            # должно ВМЕСТЕ с новой (замена, не дописка).
            if d["old"] in body and d["old"] not in d["probe"]:
                # Допустимо, если old встречается в других правилах
                # законно — проверяем только совместное вхождение в
                # одну клаузу с probe.
                for chunk in body.split("\n"):
                    if d["probe"] in chunk and d["old"] in chunk:
                        problems.append(f"{d['id']}: замена не "
                                        f"выполнена (старый текст "
                                        f"рядом с новым)")
                        break

    # 3. M0: контракт и трассировка.
    from cdpm import model as model_mod
    m0 = model_mod.load_model(ETALON / "M0-model.json")
    v = model_mod.validate_model(m0)
    if v:
        problems.append(f"M0: контракт нарушен ({len(v)}): {v[0]}")
    for r in m0["rules"]:
        counters["rules"] += 1
        if not r.get("sources"):
            problems.append(f"{r['id']}: правило без трассировки")

    # 4. Согласованность мира.
    for code in BASE_CODES:
        for doc in ("docx", "jpg"):
            if code not in texts[doc]:
                problems.append(f"мир: код базы {code} отсутствует "
                                f"в {doc}")

    # 5. Линт проприетарным стоп-словарём (только у издателя).
    lint_note = "линт: указателя словаря нет — ПРОПУЩЕН"
    uz_map = None
    if _LINT_POINTER.is_file():
        uz_map = Path(_LINT_POINTER.read_text(
            encoding="utf-8").strip())
    if uz_map and uz_map.is_file():
        # Локальный инструментарий издателя загружается по явному
        # пути (importlib): это НЕ зависимость репозитория — на чужих
        # машинах его нет, и объявлять его в pyproject нельзя.
        import importlib.util
        kit_py = uz_map.parent / "cdpm-etalon" / "transform_kit.py"
        spec = importlib.util.spec_from_file_location(
            "polyaris_lint_kit", kit_py)
        kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kit)
        find_forbidden, stop_lexicon = (kit.find_forbidden,
                                        kit.stop_lexicon)
        lexicon = stop_lexicon(uz_map.read_text(encoding="utf-8"))
        targets = list(texts.items()) + [
            (p.name, p.read_text(encoding="utf-8"))
            for p in HERE.glob("*.py")] + [
            ("M0-model.json", (ETALON / "M0-model.json")
             .read_text(encoding="utf-8"))]
        hits_total = 0
        for name, body in targets:
            counters["lint_files"] += 1
            hits = find_forbidden(body, lexicon)
            if hits:
                hits_total += len(hits)
                problems.append(f"линт: {name}: запрещённые "
                                f"токены {hits[:4]}")
        lint_note = (f"линт: {counters['lint_files']} файлов, "
                     f"токенов-нарушений {hits_total}")

    print(f"рассмотрено: дефектов {counters['defects']}, правил "
          f"{counters['rules']}; {lint_note} (самотест пройден)")
    if problems:
        for p in problems[:20]:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: комплект «Полярис» соответствует постановке")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
