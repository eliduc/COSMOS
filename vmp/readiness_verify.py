# -*- coding: utf-8 -*-
"""Верификация матрицы готовности ВМП стороной COSMOS.

Директива владельца 2026-08-29: каждый ВМП характеризуется матрицей
готовности (столбцы pairing / rostering, лестница состояний stop →
feasible_hard → feasible_soft → optimization); матрица ПРИХОДИТ ИЗ
CDPM (манифест пакета постановки, ревизия 1.12) и ВЕРИФИЦИРУЕТСЯ
COSMOS; расхождение любой клетки = остановка запуска.

Верификация — ТРЕМЯ независимыми сторонами на клетку:
1. Пересчёт заявленной матрицы из содержимого пакета (model.json +
   clean-data.json) — ловит подделку манифеста и дрейф пакета.
2. Реестр исполнителей COSMOS по зоне задачи (правило владельца о
   100% сверке «правило - исполнитель» ДО расчёта): непустой перечень
   пробелов зоны = остановка задачи независимо от манифеста.
3. Фактическая полнота данных набора для задачи — СОБСТВЕННАЯ
   детекция разделов по тем же закрытым конвенциям (не заявление
   CDPM, а пересчёт по фактическим листам).

Любое расхождение / пробел / нехватка → launch_allowed=False для
задачи. Молчаливых обходов нет.
"""
from __future__ import annotations

import json
from pathlib import Path

# Конвенции — ЗЕРКАЛО закрытых перечней CDPM (согласованы 2026-08-29,
# final-report/MATRICA-GOTOVNOSTI-VMP-DIZAJN-CLD.md). Дублирование
# намеренное: независимая детекция и есть верификация; разъезд
# перечней проявится расхождением клеток, а не молча.
LADDER = ("stop", "feasible_hard", "feasible_soft", "optimization")
ZONES_OF_TASK = {"pairing": ("PAIRING", "BOTH"),
                 "rostering": ("ROSTERING", "BOTH")}
DATA_SECTIONS = {"pairing": ("рейсы", "экипажи"),
                 "rostering": ("рейсы", "экипажи", "недоступности")}
SECTION_SHEETS = {
    "рейсы": ("рейсы", "legs", "flights"),
    "экипажи": ("экипажи", "employees", "crew"),
    "недоступности": ("отпуска", "недоступности", "unavailability",
                      "unavailabledays", "vacations"),
}
UNAVAIL_COLUMNS = ("unavailabledays", "unavailable_days")


def actual_sections(clean_data: dict | None) -> set[str]:
    """Фактически присутствующие разделы данных — независимый
    пересчёт по листам clean-data (пустой лист = заявленное
    отсутствие записей, раздел присутствует)."""
    out: set[str] = set()
    if not isinstance(clean_data, dict):
        return out
    tables = clean_data.get("tables")
    if not isinstance(tables, dict):
        return out
    for sheets in tables.values():
        if not isinstance(sheets, dict):
            continue
        for name, sheet in sheets.items():
            low = str(name).strip().lower()
            for section, aliases in SECTION_SHEETS.items():
                if low in aliases:
                    out.add(section)
            cols = (sheet or {}).get("columns") \
                if isinstance(sheet, dict) else None
            for c in cols or ():
                if str(c).strip().lower() in UNAVAIL_COLUMNS:
                    out.add("недоступности")
    return out


def gap_rows(gaps: list, model: dict, *, run_name: str | None = None,
             task: str | None = None) -> list:
    """Пробелы зоны СТРОКАМИ, с номером и состоянием заявки каталога.

    Число пробелов (`gaps`) остаётся числом — по нему судит клетка
    матрицы; строки нужны панели и остановке, чтобы назвать заявку
    (0.16 шага 6: «остановка называет номер и состояние»), а не
    отправить человека искать её самому.

    Классификатор читает каталог, и ЛЮБАЯ его беда гасится здесь:
    колонка реестра обязана вернуть строки, а не исключение (контракт
    verify «никаких исключений» — иначе новая грань роняла бы вердикт
    матрицы готовности целиком). Строка тогда честно говорит «заявки
    нет» полем `request: null`, а причину называют своими словами
    колонка реестра (`error`, `drift`) и журнал нажатия."""
    rows = [{"rule_id": str(g.get("rule_id") or ""),
             "kind": str(g.get("kind") or ""),
             "note": str(g.get("note") or ""),
             "form": None, "request": None}
            for g in gaps or () if isinstance(g, dict)]
    if not rows:
        return rows
    from vmp import catalog_requests as _cr
    try:
        found = {str(r.get("rule_id") or ""): r for r in _cr.classify_gaps(
            gaps, model=model, run_name=run_name, task=task)}
    except Exception:                                        # noqa: BLE001
        return rows
    for row in rows:
        got = found.get(row["rule_id"])
        if not got:
            continue
        # Форма пробела и ссылка — то же, что несёт строка
        # классификатора (§1.2): панель показывает их столбцами, и
        # брать их иначе значило бы завести второй источник правды.
        row["form"] = got.get("form")
        if got.get("request_id"):
            row["request"] = {"request_id": got["request_id"],
                              "state": got.get("state"),
                              "link": got.get("link")}
    return rows


def registry_side(model: dict, task: str, repo_root: Path,
                  registry_path: Path | None) -> dict:
    """Сторона реестра исполнителей: пробелы зоны задачи.

    Для задачи, чья зона реестром ещё не сверялась, честный итог —
    полный перечень правил зоны пробелами (не исключение)."""
    from vmp import catalog_access as _ca
    from vmp import registry_check as rc
    out = {"rules_in_zone": 0, "gaps": None, "gap_rows": [], "drift": None,
           "error": None}
    zones = ZONES_OF_TASK[task]
    rules = [r for r in (model.get("rules") or [])
             if isinstance(r, dict) and r.get("zone") in zones]
    out["rules_in_zone"] = len(rules)
    try:
        reg = rc.load_registry(registry_path)
        # Сторож дрейфа и глубокая сверка читают каталог шаблонов
        # (шаг 2): недоступный каталог — показание в колонке реестра,
        # не исключение (контракт verify «никаких исключений»).
        out["drift"] = rc.drift_check(reg, repo_root)
        gaps = rc.deep_check(rules, None, reg, zones=zones)
        out["gaps"] = len(gaps)
        out["gap_rows"] = gap_rows(gaps, model, task=task)
    except (rc.ConformanceError, OSError, ValueError,
            *_ca.CATALOG_ERRORS) as e:
        out["error"] = str(e)
        return out
    # Исключённые до получения данных по решению авиакомпании — к
    # исполнению не требуются (deep_check их не судит), но видимы.
    out["excluded_until_data"] = rc.excluded_until_data_ids(rules)
    return out


def verify(manifest: dict, model: dict, clean_data: dict | None,
           repo_root: Path,
           registry_path: Path | None = None,
           form: dict | None = None,
           applied: dict | None = None) -> dict:
    """Полная верификация матрицы манифеста. Возврат: вердикты по
    клеткам + общий. Никаких исключений на расхождениях — расхождение
    это РЕЗУЛЬТАТ (жёсткая остановка — дело гейта-потребителя).
    form — форма ВМП пакета (vmp-form.json): ярусы критериев части
    задачи (несколько критериев / ярус > 1) — стоп клетки для режима
    оптимизации (структура ВМП 2026-09-02; решатель исполняет один
    критерий). applied — применённые пороги решателя (отчёт покрытия
    П-1): четвёртая сторона (внешний аудит 04.09.2026, A1) — исполнитель
    покрывает правило только при переданном ему пороге ВМП; жёсткое
    правило зоны, чей порог в applied отсутствует, держит запуск
    поимённо. None — служба без П-1, сторона не судится."""
    declared = manifest.get("readiness_matrix")
    out = {"has_matrix": isinstance(declared, dict), "columns": {},
           "verdict": "OK"}
    if not out["has_matrix"]:
        out["verdict"] = "NO_MATRIX"
        return out
    # пересчёт заявленного — функцией производителя на содержимом
    # пакета: ловит подделку/дрейф (тот же закон, другой вход)
    try:
        from cdpm.export_task_package import readiness_ladder
        expect = readiness_ladder(
            model, clean_data,
            spec_version=manifest.get("spec_version"))
    except Exception as e:                            # noqa: BLE001
        out["verdict"] = "RECOMPUTE_ERROR"
        out["error"] = str(e)
        return out
    facts = actual_sections(clean_data)
    for task in ("pairing", "rostering"):
        dcell = (declared.get("columns") or {}).get(task) or {}
        ecell = (expect.get("columns") or {}).get(task) or {}
        d_state = dcell.get("state")
        e_state = ecell.get("state")
        missing = [s for s in DATA_SECTIONS[task] if s not in facts]
        reg = registry_side(model, task, repo_root, registry_path)
        reg_clean = (reg["error"] is None and reg["gaps"] == 0
                     and not reg["drift"])
        cell_ok = (d_state == e_state and dcell == ecell)
        from vmp import registry_check as _rc
        tiers = _rc.criteria_tiers_gaps(form, parts=(task,))
        # Сторона порогов (аудит A1): реестр знает исполнителя, но
        # исполнитель судит только по переданному порогу — правило с
        # порогом вне applied решателя не покрыто.
        thr_missing: list[str] = []
        if isinstance(applied, dict):
            from vmp import execution_manifest as _xm
            thr_missing = [
                "%s: %s" % (g["rule_id"], g["note"])
                for g in _xm.thresholds_gaps(
                    model.get("rules") or [], applied, task=task)]
        launch = (cell_ok and d_state in LADDER and d_state != "stop"
                  and not missing and reg_clean and not thr_missing)
        out["columns"][task] = {
            "declared_state": d_state,
            "recomputed_state": e_state,
            "cell_match": cell_ok,
            "data_actual_missing": missing,
            "registry": reg,
            "thresholds_missing": thr_missing,
            # Ярусы критериев части: режимы выполнимости не задеты,
            # оптимизация — стоп (criteria_tiers_unsupported).
            "criteria_tiers": [g["note"] for g in tiers],
            "optimization_allowed": launch and not tiers,
            "launch_allowed": launch,
            "why_blocked": [] if launch else [w for w in (
                None if cell_ok else
                "клетка манифеста не совпала с пересчётом",
                None if d_state != "stop" else
                "состояние клетки — остановка",
                None if not missing else
                "фактически нет разделов данных: " + ", ".join(
                    missing),
                None if reg_clean else
                "реестр исполнителей: пробелы зоны %s, дрейф %s%s"
                % (reg["gaps"], len(reg["drift"] or []),
                   ("; " + reg["error"]) if reg["error"] else ""),
                None if not thr_missing else
                "пороги ВМП не переданы исполнителям: "
                + "; ".join(thr_missing),
            ) if w],
        }
        if not cell_ok:
            out["verdict"] = "MISMATCH"
    return out


def state_allows(state: str | None, mode: str) -> bool:
    """Достаточно ли состояние клетки для запрошенного режима."""
    need = {"feasibility-hard": "feasible_hard",
            "feasibility-soft": "feasible_soft",
            "optimization": "optimization"}.get(mode)
    if need is None or state not in LADDER:
        return False
    return LADDER.index(state) >= LADDER.index(need)
