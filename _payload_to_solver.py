"""Выгрузка решения COSMOS в формате исходного файла оптимизатора
вендора (format:5), 2026-07-24.

Зачем: чтобы результат COSMOS можно было отдать обратно в систему
источника данных и сравнить/загрузить их же средствами, а не только
нашими. Обратная операция к `_solver_to_payload.py`.

Принцип — НЕ выдумывать структуру: берётся ИСХОДНЫЙ файл вендора и в
нём заполняется поле `employee` существующих строк назначений
(employeeAssignments). Идентификаторы строк, нарядов, цепочек, участков
и все прочие поля остаются нетронутыми, поэтому файл читается тем же
разбором, что и оригинал.

Соответствие устанавливается по цепочкам: юнит каталога
(`sourceUnit` = `sequence:<id>` или `solver:<наряд>`) -> строки
назначений тех же нарядов; место экипажа (позиция в `seats`) -> строка
нужного ранга. Один человек получает ВСЕ наряды своей цепочки — как и
в решении.

Честность выгрузки:
  * строки, закреплённые планировщиками (`locked=true` с сотрудником),
    не переписываются; расхождение с нашим решением считается и
    показывается (в норме ноль — закрепления мы обязаны сохранять);
  * наряды отдыха (`rest`) и наземные наряды остаются как в источнике —
    это данные планировщиков, а не наши назначения;
  * оценка (`score`) и метрики источника ОБНУЛЯЮТСЯ: они описывают
    исходный черновик, а функцию оценки вендора COSMOS не
    воспроизводит — оставить их значило бы выдать чужие числа за свои;
  * юниты без структуры нарядов у вендора (одиночные участки) в этом
    формате невыразимы и честно считаются отдельным счётчиком.

Отчёт конвертации пишется ВСЕГДА, даже при отказе.

Usage:
    python _payload_to_solver.py --solver dataset.json --catalog cat.json
        --roster solution.json --out solution_format5.json
        [--check check.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass


class ExportError(ValueError):
    """Отказ выгрузки — всегда с объяснением."""


def _seq_of_duty(solver: dict) -> Dict[str, str]:
    """Наряд -> идентификатор цепочки (как в _solver_to_payload)."""
    seq_id_of_full = {s["id"]: s["sequenceId"]
                      for s in solver.get("sequences") or []
                      if isinstance(s, dict)
                      and isinstance(s.get("id"), str)
                      and isinstance(s.get("sequenceId"), str)}
    votes: Dict[str, Counter] = defaultdict(Counter)
    for ea in solver.get("employeeAssignments") or []:
        if not isinstance(ea, dict):
            continue
        did, full = ea.get("duty"), ea.get("sequence")
        if isinstance(did, str) and isinstance(full, str) \
                and full in seq_id_of_full:
            votes[did][seq_id_of_full[full]] += 1
    return {did: min(s for s, n in cnt.items() if n == max(cnt.values()))
            for did, cnt in votes.items()}


def unit_key_of_duty(duty_id: str, seq_of_duty: Dict[str, str]) -> str:
    return (f"sequence:{seq_of_duty[duty_id]}" if duty_id in seq_of_duty
            else f"solver:{duty_id}")


def seats_from_roster(roster: dict, units: List[dict]) -> Dict[str, dict]:
    """Юнит -> {позиция места: (роль, сотрудник)} по нашему решению.

    Позиция места берётся из `indexInFlight`; со-локация решения
    гарантирует, что на всех участках юнита одна позиция занята одним
    человеком (проверяется, расхождение — громкий отказ)."""
    unit_of_fn: Dict[str, int] = {}
    for i, u in enumerate(units):
        for fn in u.get("active_fns") or []:
            unit_of_fn[fn] = i
    per_unit: Dict[int, Dict[int, tuple]] = defaultdict(dict)
    conflicts = 0
    for a in roster.get("flightAssignments") or []:
        emp = a.get("employee")
        if not emp:
            continue
        i = unit_of_fn.get(a.get("flight"))
        if i is None:
            continue
        idx = int(a.get("indexInFlight") or 0)
        role = str(a.get("requiredSkill") or "")
        prev = per_unit[i].get(idx)
        if prev is not None and prev != (role, emp):
            conflicts += 1
        per_unit[i][idx] = (role, emp)
    if conflicts:
        raise ExportError(
            f"решение непоследовательно: одна позиция места в цепочке "
            f"занята разными людьми на разных участках ({conflicts} "
            f"случаев) — выгрузка в формат нарядов невозможна")
    out: Dict[str, dict] = {}
    for i, seats in per_unit.items():
        out[units[i]["sourceUnit"]] = seats
    return out


def export(solver: dict, catalog: dict, roster: dict) -> tuple:
    """(файл в формате источника, отчёт конвертации)."""
    units = catalog.get("selected") or []
    if not units:
        raise ExportError("каталог пуст: нечего сопоставлять")
    rows = solver.get("employeeAssignments")
    if not isinstance(rows, list) or not rows:
        raise ExportError("во входном файле нет employeeAssignments")
    duties = {d["id"]: d for d in solver.get("duties") or []
              if isinstance(d, dict) and isinstance(d.get("id"), str)}
    seq_of_duty = _seq_of_duty(solver)
    by_unit_seats = seats_from_roster(roster, units)

    # строки источника по (юнит, наряд, ранг) в устойчивом порядке
    grouped: Dict[tuple, List[dict]] = defaultdict(list)
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("duty"), str):
            continue
        key = (unit_key_of_duty(r["duty"], seq_of_duty), r["duty"],
               str(r.get("preferredRank") or ""))
        grouped[key].append(r)
    for lst in grouped.values():
        lst.sort(key=lambda r: str(r.get("id")))

    out_rows = [dict(r) if isinstance(r, dict) else r for r in rows]
    row_by_id = {r.get("id"): r for r in out_rows if isinstance(r, dict)}

    check = {
        "units_in_catalog": len(units),
        "units_with_crew": len(by_unit_seats),
        "units_without_vendor_duties": 0,
        "rows_total": len(rows),
        "rows_filled": 0,
        "rows_kept_locked": 0,
        "locked_conflicts": 0,
        "seats_unplaced": 0,
        "rest_duty_rows_untouched": 0,
    }
    unplaced_samples: List[str] = []

    for src_key, seats in sorted(by_unit_seats.items()):
        # наряды этого юнита, кроме отдыха
        unit_duties = sorted(
            {d_id for (u, d_id, _rank) in grouped if u == src_key
             and not duties.get(d_id, {}).get("rest")})
        if not unit_duties:
            check["units_without_vendor_duties"] += 1
            check["seats_unplaced"] += len(seats)
            continue
        by_role: Dict[str, List[str]] = defaultdict(list)
        for idx in sorted(seats):
            role, emp = seats[idx]
            by_role[role].append(emp)
        for d_id in unit_duties:
            for role, emps in by_role.items():
                targets = [row_by_id.get(t.get("id"))
                           for t in (grouped.get((src_key, d_id, role))
                                     or [])]
                targets = [t for t in targets if t is not None]
                # Места ОДНОГО ранга взаимозаменяемы: сопоставляем
                # множествами, а не позиционно. Сначала закреплённые
                # строки (их людей не трогаем и вычитаем из очереди),
                # затем свободные строки получают остальных.
                locked_here = [t for t in targets
                               if t.get("locked") and t.get("employee")]
                free_here = [t for t in targets if t not in locked_here]
                queue = list(emps)
                for t in locked_here:
                    check["rows_kept_locked"] += 1
                    if t["employee"] in queue:
                        queue.remove(t["employee"])
                    else:
                        # закреплённый человек, которого наше решение на
                        # этот юнит не поставило — честное расхождение
                        check["locked_conflicts"] += 1
                for t in free_here:
                    if not queue:
                        break
                    t["employee"] = queue.pop(0)
                    check["rows_filled"] += 1
                if queue:
                    check["seats_unplaced"] += len(queue)
                    if len(unplaced_samples) < 10:
                        unplaced_samples.append(
                            f"{src_key}|{role}|людей {len(emps)} > "
                            f"строк {len(targets)}")

    check["rest_duty_rows_untouched"] = sum(
        1 for r in out_rows if isinstance(r, dict)
        and duties.get(r.get("duty"), {}).get("rest"))
    check["rows_with_employee_after"] = sum(
        1 for r in out_rows if isinstance(r, dict) and r.get("employee"))
    if unplaced_samples:
        check["seats_unplaced_samples"] = unplaced_samples

    out = dict(solver)
    out["employeeAssignments"] = out_rows
    # оценка и метрики источника описывают ЕГО черновик — не наши числа
    out["score"] = None
    out["metrics"] = []
    out["cosmosExport"] = {
        "generator": "COSMOS _payload_to_solver.py",
        "generatedAt": datetime.now(timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": ("employee заполнены решением COSMOS; идентификаторы, "
                 "наряды, цепочки и участки — из исходного файла; score "
                 "и metrics обнулены намеренно (функция оценки вендора "
                 "не воспроизводится)"),
        "counters": {k: v for k, v in check.items()
                     if isinstance(v, int)},
    }
    return out, check


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="выгрузка решения COSMOS в формате файла вендора")
    ap.add_argument("--solver", type=Path, required=True,
                    help="исходный файл вендора (format:5) — источник "
                         "структуры")
    ap.add_argument("--catalog", type=Path, required=True)
    ap.add_argument("--roster", type=Path, required=True,
                    help="решение этапа 2 (канонический ростер)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--check", type=Path, default=None)
    args = ap.parse_args(argv)

    check_path = args.check or args.out.with_name(
        args.out.stem + "_export_check.json")

    def _write_check(body: dict, ok: bool):
        check_path.parent.mkdir(parents=True, exist_ok=True)
        check_path.write_text(json.dumps(
            {**body, "ok": ok,
             "generatedAt": datetime.now(timezone.utc)
                                    .strftime("%Y-%m-%dT%H:%M:%SZ")},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  отчёт: {check_path}", flush=True)

    for p in (args.solver, args.catalog, args.roster):
        if not p.is_file():
            _write_check({"errors": [f"файл не найден: {p}"]}, False)
            print(f"ERROR: файл не найден: {p}", flush=True)
            return 2
    try:
        solver = json.loads(args.solver.read_text(encoding="utf-8"))
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        roster = json.loads(args.roster.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _write_check({"errors": [f"вход не разобран как JSON: {exc}"]},
                     False)
        print(f"ERROR: вход не разобран: {exc}", flush=True)
        return 1
    print("=== Решение COSMOS -> формат источника (format:5) ===",
          flush=True)
    try:
        out, check = export(solver, catalog, roster)
    except ExportError as exc:
        _write_check({"errors": [str(exc)]}, False)
        print(f"ERROR: {exc}", flush=True)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"  файл: {args.out}", flush=True)
    print(f"  заполнено строк: {check['rows_filled']}; закреплённых "
          f"сохранено: {check['rows_kept_locked']} (расхождений "
          f"{check['locked_conflicts']}); всего с сотрудником: "
          f"{check['rows_with_employee_after']}", flush=True)
    if check["units_without_vendor_duties"] or check["seats_unplaced"]:
        print(f"  невыразимо в формате нарядов: юнитов "
              f"{check['units_without_vendor_duties']}, мест "
              f"{check['seats_unplaced']}", flush=True)
    _write_check(check, check["locked_conflicts"] == 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
