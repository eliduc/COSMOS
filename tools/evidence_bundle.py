#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Обезличенный пакет доказательств по набору данных (пятый аудит, E5).

Постановка. Результаты по набору заказчика показывать наружу
нужно — заказчику, аудитору, рецензенту, — а расписание и экипаж не
покидают машину. До сих пор это решалось ручной выборкой чисел из отчётов:
человек читал документ и переписывал сводки. Ручная выборка не
воспроизводима и не доказуема — нельзя проверить, что показанное число
относится к тому же прогону, что и остальные.

Пакет собирается кодом и содержит РОВНО четыре вида сведений:

1. отпечатки исходных файлов и артефактов (sha256, размер, число строк) —
   получатель убеждается, что речь об одних и тех же данных, не видя их;
2. сводные счётчики спроса и предложения — только числа и распределения,
   без единого опознавательного знака;
3. версии кода, решателя и правил — чем именно получен результат;
4. команды, которыми результат воспроизводится на машине владельца
   данных.

Чего в пакете НЕТ и быть не может: ни одной строки расписания, ни одного
сотрудника, ни номера рейса, ни номера борта, ни кода аэропорта, ни даты
конкретного вылета. Это не пожелание, а проверяемое свойство: сборщик
пропускает готовый пакет через страж `assert_anonymous`, и находка
опознавательного знака — отказ, а не предупреждение.

Использование:

    python tools/evidence_bundle.py --payload data/uploads/<файл>.json \\
        --artifact final-report/<результат>.json --out final-report/bundle

Пакет пишется в указанный каталог; наружу отдают именно его.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _cli_console import force_utf8_console                          # noqa
force_utf8_console()

SCHEMA = "cosmos-evidence-bundle/v1"


class AnonymityError(RuntimeError):
    """В пакете найден опознавательный знак. Пакет не выпускается."""


# --------------------------------------------------------------- отпечатки

def file_digest(path: Path) -> dict:
    """Отпечаток файла БЕЗ его содержимого."""
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return {"name": path.name, "sha256": h.hexdigest(), "bytes": size}


# --------------------------------------------------------------- агрегаты

def _hours(a: str, b: str) -> float:
    try:
        t0 = datetime.fromisoformat(a.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return max(0.0, (t1 - t0).total_seconds() / 3600.0)
    except (ValueError, AttributeError):
        return 0.0


def demand_aggregates(payload: dict) -> dict:
    """Сводки спроса и предложения. Только числа.

    Ни один ключ здесь не возвращает значение из данных: коды аэропортов,
    типы судов и идентификаторы превращаются в КОЛИЧЕСТВА различных
    значений и в распределения по размеру, но не по имени."""
    flights = payload.get("flights") or []
    employees = payload.get("employees") or []
    assignments = payload.get("flightAssignments") or []
    contracts = payload.get("contracts") or []

    block_h = [_hours(f.get("departureUTCDateTime", ""),
                      f.get("arrivalUTCDateTime", "")) for f in flights]
    total_block = round(sum(block_h), 2)
    days = {str(f.get("departureUTCDateTime", ""))[:10] for f in flights}
    days.discard("")

    per_fleet = Counter(str(f.get("aircraftType") or "") for f in flights)
    per_base = Counter(str(e.get("homeAirport") or "") for e in employees)
    slots_per_flight = Counter(
        str(a.get("flightLegId") or a.get("flight") or "")
        for a in assignments)

    def spread(counter: Counter) -> dict:
        """Распределение по РАЗМЕРУ групп: сколько групп какого объёма.
        Имена групп не выходят — они и есть опознавательный знак."""
        sizes = sorted(counter.values(), reverse=True)
        return {"groups": len(sizes), "largest": sizes[0] if sizes else 0,
                "smallest": sizes[-1] if sizes else 0,
                "sizes": sizes[:20]}

    airports = {str(f.get("departureAirport") or "") for f in flights}
    airports |= {str(f.get("arrivalAirport") or "") for f in flights}
    airports.discard("")

    quals = payload.get("qualifications") or []
    unavailable = sum(len(e.get("unavailableDays") or []) for e in employees)
    reserve = sum(1 for e in employees if e.get("isReserve"))

    return {
        "flights": len(flights),
        "flight_days": len(days),
        "block_hours_total": total_block,
        "block_hours_mean": round(total_block / len(flights), 3)
        if flights else 0.0,
        "distinct_airports": len(airports),
        "distinct_fleet_types": len([k for k in per_fleet if k]),
        "flights_per_fleet_type": spread(
            Counter({k: v for k, v in per_fleet.items() if k})),
        "crew_slots": len(assignments),
        "slots_per_flight": spread(
            Counter({k: v for k, v in slots_per_flight.items() if k})),
        "employees": len(employees),
        "employees_reserve": reserve,
        "employees_per_base": spread(
            Counter({k: v for k, v in per_base.items() if k})),
        "distinct_bases": len([k for k in per_base if k]),
        "unavailable_day_records": unavailable,
        "qualification_records": len(quals),
        "contracts": len(contracts),
        "contract_types": len({str(c.get("type") or "") for c in contracts
                               if c.get("type")}),
    }


def artifact_aggregates(artifact: dict) -> dict:
    """Числовые итоги результата. Берутся ТОЛЬКО числовые поля верхнего
    уровня и известные сводки: списки цепочек и назначений не читаются."""
    out: dict = {}
    for key, val in artifact.items():
        if isinstance(val, bool):
            out[key] = val
        elif isinstance(val, (int, float)):
            out[key] = val
        elif isinstance(val, list):
            out[f"{key}_count"] = len(val)
    return out


# --------------------------------------------------------------- версии

def versions() -> dict:
    from webapp import build_identity
    ident = build_identity.build_identity(refresh=True)
    return {
        "source_commit": (ident.get("source") or {}).get("commit"),
        "source_tree_dirty": (ident.get("source") or {}).get("tree_dirty"),
        "solver_jar_sha256": (ident.get("solver") or {}).get("jar_sha256"),
        "solver_matches_manifest": (ident.get("solver") or {}).get(
            "matches_manifest"),
        "rules_contract_fingerprint": ident.get("rules_contract_fingerprint"),
    }


def rules_fingerprint(instance_id: str | None) -> dict:
    """Отпечаток ДЕЙСТВУЮЩИХ правил, но не сами значения: значения — это
    постановка заказчика, наружу уходит только их тождество."""
    from webapp import config_scope
    out = {}
    for domain in (config_scope.DOMAIN_RULES, config_scope.DOMAIN_COST,
                   config_scope.DOMAIN_PAY):
        try:
            eff = config_scope.effective(domain, instance_id)
            body = json.dumps(eff, sort_keys=True, ensure_ascii=False)
            out[domain] = hashlib.sha256(body.encode()).hexdigest()[:16]
        except Exception as exc:                          # noqa: BLE001
            out[domain] = f"n/a ({type(exc).__name__})"
    return out


# --------------------------------------------------------------- страж

# Опознавательные знаки, которых в пакете быть не должно. Шаблоны
# намеренно широкие: ложное срабатывание стоит одной правки сборщика, а
# пропуск — утечки данных заказчика.
# Границы слова заданы просмотром вперёд/назад, а не `\b`: подчёркивание
# для `\b` — обычный знак слова, и `ZZ601_uncovered` прошёл бы мимо
# правила о номере рейса. Ровно так опознавательный знак и попадает в
# ИМЯ поля сводки.
_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])", "трёхбуквенный код (аэропорт)"),
    (r"(?<![A-Za-z0-9])[A-Z]{2}\d{1,4}(?!\d)", "номер рейса"),
    (r"(?<![A-Za-z0-9])[A-Z]{2}-?[A-Z0-9]{3,5}(?![A-Za-z0-9])",
     "номер борта"),
    (r"\d{4}-\d{2}-\d{2}", "календарная дата"),
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "адрес почты"),
    (r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+", "имя кириллицей"),
    (r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", "имя латиницей"),
)

NOTE = ("сводные показатели и отпечатки; строк расписания и "
        "сведений о сотрудниках пакет не содержит")
REPRODUCE = (
    "python _meridian_to_payload.py --emit-catalog",
    "python _input_audit.py --payload <файл> --json",
    "python _pairing_builder.py --level 2 --payload <файл>",
    "python _gerad_stage2_roster.py --payload <файл> --run-id <id>",
)

# Значения, которые сборщик пишет САМ фиксированным текстом: схема,
# пояснение и команды воспроизведения. Пропуск идёт по ТОЧНОМУ СОВПАДЕНИЮ
# со значением, а не по имени поля. Разница существенна: пропуск по имени
# означал бы, что достаточно положить данные заказчика в поле `note` —
# и страж их не увидит. Проверка списка (`tools/verify_audit_20260726.py`)
# наткнулась ровно на это.
_ALLOWED_EXACT = frozenset({SCHEMA, NOTE, *REPRODUCE})
# Метка времени сборки — единственное место, где календарная дата
# законна; допускается только строгая форма отметки, а не любая дата.
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _scan_value(value: str, where: str, findings: list[str]) -> None:
    if value in _ALLOWED_EXACT:
        return
    if where == "$.generated_at" and _TIMESTAMP.fullmatch(value):
        return                       # отметка времени сборки пакета
    if re.fullmatch(r"[0-9a-f]{7,64}", value):
        return                       # отпечаток или номер ревизии
    for pattern, what in _FORBIDDEN_PATTERNS:
        m = re.search(pattern, value)
        if m:
            findings.append(f"{where}: {what} — «{m.group(0)}»")
            return


def assert_anonymous(bundle: dict) -> None:
    """Пройти по всему пакету и убедиться, что опознавательных знаков нет.

    Проверяются и КЛЮЧИ, и значения: имя поля вида `flights_ZZ601` выдало
    бы данные не хуже значения."""
    findings: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _scan_value(str(k), f"{path}.{k} (имя поля)", findings)
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            _scan_value(node, path, findings)

    walk(bundle, "$")
    if findings:
        raise AnonymityError(
            "пакет не обезличен, выпуск отменён:\n  " + "\n  ".join(findings))


# --------------------------------------------------------------- сборка

def build(payload_path: Path, artifact_paths: list[Path],
          instance_id: str | None, generated_at: str) -> dict:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    sources = [file_digest(payload_path)]
    artifacts = []
    for ap in artifact_paths:
        digest = file_digest(ap)
        try:
            body = json.loads(ap.read_text(encoding="utf-8"))
            digest["totals"] = artifact_aggregates(body) \
                if isinstance(body, dict) else {"items": len(body)}
        except (OSError, json.JSONDecodeError):
            digest["totals"] = {}
        artifacts.append(digest)

    bundle = {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "note": NOTE,
        "sources": sources,
        "artifacts": artifacts,
        "demand": demand_aggregates(payload),
        "versions": versions(),
        "rules_fingerprints": rules_fingerprint(instance_id),
        "reproduce": list(REPRODUCE),
    }
    # Имена файлов — тоже данные заказчика: «QZ-июль-экипаж.json» само по
    # себе сообщает и авиакомпанию, и период. Наружу идёт отпечаток.
    for entry in sources + artifacts:
        entry["name"] = "sha256:" + entry["sha256"][:12]
    assert_anonymous(bundle)
    return bundle


def _markdown(bundle: dict) -> str:
    d = bundle["demand"]
    v = bundle["versions"]
    lines = [
        "# Пакет доказательств (обезличенный)",
        "",
        f"Собран: {bundle['generated_at']}. {bundle['note']}.",
        "",
        "## Объём задачи",
        "",
        "| Показатель | Значение |",
        "|---|---|",
        f"| Участков (рейсовых плеч) | {d['flights']} |",
        f"| Суток в горизонте | {d['flight_days']} |",
        f"| Блок-часов всего | {d['block_hours_total']} |",
        f"| Типов судов | {d['distinct_fleet_types']} |",
        f"| Аэропортов | {d['distinct_airports']} |",
        f"| Мест экипажа к покрытию | {d['crew_slots']} |",
        f"| Сотрудников | {d['employees']} |",
        f"| Из них резерв | {d['employees_reserve']} |",
        f"| Баз | {d['distinct_bases']} |",
        "",
        "## Чем получено",
        "",
        f"* срез кода: `{v['source_commit']}`"
        f"{' (рабочее дерево изменено)' if v['source_tree_dirty'] else ''}",
        f"* решатель: `{v['solver_jar_sha256']}`, совпадение с манифестом "
        f"сборки: {'да' if v['solver_matches_manifest'] else 'нет'}",
        f"* договор правил: `{v['rules_contract_fingerprint']}`",
        "",
        "## Отпечатки",
        "",
        "| Файл | sha256 | Байт |",
        "|---|---|---|",
    ]
    for e in bundle["sources"] + bundle["artifacts"]:
        lines.append(f"| {e['name']} | `{e['sha256']}` | {e['bytes']} |")
    lines += [
        "",
        "## Воспроизведение на машине владельца данных",
        "",
        "```",
        *bundle["reproduce"],
        "```",
        "",
        "Пакет намеренно не содержит расписания и сведений о сотрудниках:",
        "получатель сверяет отпечатки со своими файлами и повторяет расчёт",
        "у себя.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--payload", required=True, type=Path)
    ap.add_argument("--artifact", action="append", default=[], type=Path)
    ap.add_argument("--instance", default=None)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        bundle = build(args.payload, list(args.artifact), args.instance,
                       generated_at)
    except AnonymityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "README-RU.md").write_text(_markdown(bundle),
                                           encoding="utf-8")
    print(f"пакет собран: {args.out}")
    print(f"участков {bundle['demand']['flights']}, "
          f"мест {bundle['demand']['crew_slots']}, "
          f"сотрудников {bundle['demand']['employees']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
