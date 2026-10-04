# -*- coding: utf-8 -*-
"""Самотест детерминированного режима (Д1 плана «прямой ход», порядок
COSMOS 03.09): два запуска решателей на одном пакете и одном профиле
обязаны дать ТОЖДЕСТВЕННЫЙ канонический выход.

Работа модуля — только сведение и сравнение: запуск задачи делает
вызывающая сторона (кнопка), подписи считает vmp.signatures. Здесь
собирается карточка самотеста: профиль, подпись задачи, подписи выхода
обоих запусков, вердикт. Расхождение — серия не стартует; порядок
разбора по плану: профиль → данные → потребители формулировок → канон
гейта.

    run_signature(task, result) -> подпись канонического выхода
    compare(first, second)      -> карточка самотеста
"""
from __future__ import annotations

from typing import Any

TASKS = ("pairing", "rostering")


def run_signature(task: str, result: dict) -> dict:
    """Подпись канонического выхода одного запуска.

    result: для ростеринга {"solved": решение, "constraints": разбор
    счёта (score-breakdown.constraints), "status": статус}; для пейринга
    {"catalog": каталог построителя, "status": статус}."""
    from vmp import signatures as sg
    if task == "rostering":
        canon = sg.canonical_roster(result.get("solved") or {},
                                    constraints=result.get("constraints"),
                                    status=result.get("status"))
    elif task == "pairing":
        canon = sg.canonical_pairing(result.get("catalog") or {},
                                     status=result.get("status"))
    else:
        raise ValueError(f"неизвестная задача самотеста: {task!r}")
    sig = dict(sg.output_signature(canon))
    # Канон едет рядом с подписью: при расхождении подсказка строится по
    # содержанию выхода, а не по хэшу (хэш о причине не говорит).
    sig["canon"] = canon
    return sig


def compare(first: dict, second: dict, *, task_signature: dict | None = None,
            profile: dict | None = None) -> dict:
    """Карточка самотеста Д1 по двум запускам одной задачи.

    first/second — выходы run_signature. Вердикт «сошлось» только при
    равенстве подписей; неполные данные — «несравнимо» (молчаливым
    успехом не считается)."""
    sha1 = (first or {}).get("sha256")
    sha2 = (second or {}).get("sha256")
    if not sha1 or not sha2:
        verdict = "несравнимо"
    elif sha1 == sha2:
        verdict = "сошлось"
    else:
        verdict = "разошлось"
    card: dict[str, Any] = {"verdict": verdict,
                            "run_1": first, "run_2": second,
                            "profile_stamp": (profile or {}).get("stamp"),
                            "task_signature": task_signature}
    if verdict == "разошлось":
        card["diff_hint"] = _diff_hint(first, second)
    return card


def _diff_hint(first: dict, second: dict) -> list:
    """Где именно разошлись выходы: по полям КАНОНА (счёт, статус, число
    назначений или цепочек) — подсказка для разбора, не вердикт."""
    out = []
    c1 = (first or {}).get("canon") or {}
    c2 = (second or {}).get("canon") or {}
    for key in sorted(set(c1) | set(c2)):
        a, b = c1.get(key), c2.get(key)
        if a == b:
            continue
        if isinstance(a, list) or isinstance(b, list):
            out.append({"field": key,
                        "run_1": f"записей {len(a or [])}",
                        "run_2": f"записей {len(b or [])}"})
        else:
            out.append({"field": key, "run_1": a, "run_2": b})
    if not out:
        for key in sorted(set(first or {}) | set(second or {})):
            if key in ("sha256", "canon"):
                continue
            a, b = (first or {}).get(key), (second or {}).get(key)
            if a != b:
                out.append({"field": key, "run_1": a, "run_2": b})
    return out


def selftest_card(results: dict, *, profiles: dict | None = None,
                  task_signatures: dict | None = None) -> dict:
    """Карточка самотеста по обеим задачам.

    results: {задача: [выход_1, выход_2]} в форме run_signature."""
    profiles = profiles or {}
    task_signatures = task_signatures or {}
    tasks: dict = {}
    for task in TASKS:
        pair = results.get(task) or []
        if len(pair) != 2:
            tasks[task] = {"verdict": "несравнимо",
                           "note": "нужны ровно два запуска задачи"}
            continue
        tasks[task] = compare(pair[0], pair[1],
                              task_signature=task_signatures.get(task),
                              profile=profiles.get(task))
    verdicts = {t: c.get("verdict") for t, c in tasks.items()}
    ok = bool(verdicts) and all(v == "сошлось" for v in verdicts.values())
    return {"tasks": tasks, "verdicts": verdicts, "passed": ok,
            "note": ("самотест Д1 пройден: два запуска дали тождественный "
                     "выход по обеим задачам"
                     if ok else
                     "самотест Д1 НЕ пройден — серия не стартует; порядок "
                     "разбора: профиль → данные → потребители формулировок "
                     "→ канон гейта")}


def render(card: dict) -> str:
    lines = ["САМОТЕСТ Д1: " + ("ПРОЙДЕН" if card.get("passed") else "НЕ ПРОЙДЕН")]
    for task, c in sorted((card.get("tasks") or {}).items()):
        lines.append("  %s: %s (подписи %s / %s)"
                     % (task, c.get("verdict"),
                        str((c.get("run_1") or {}).get("sha256"))[:12],
                        str((c.get("run_2") or {}).get("sha256"))[:12]))
        for d in c.get("diff_hint") or []:
            lines.append("     ≠ %s: %r против %r"
                         % (d["field"], d["run_1"], d["run_2"]))
        if c.get("note"):
            lines.append("     " + str(c["note"]))
    return "\n".join(lines)
