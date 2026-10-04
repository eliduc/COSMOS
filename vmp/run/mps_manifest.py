# -*- coding: utf-8 -*-
"""Экспорт CPLEX (MPS/LP): половина CDPM — сопровождение по лучшим
практикам (отмашка владельца 2026-08-27).

Файлы модели пишет построитель решателя (--export-mps/--export-lp +
справочник <файл>.map.json, коммит fa3a350; паритет с живым счётом
проверен независимым решателем). Этот модуль кладёт ПОВЕРХ:

  * шапку-комментарий в сам файл (MPS: строки «*…», LP: «\\…») —
    происхождение, отпечатки, ЧТО включено и ЧЕГО В МОДЕЛИ НЕТ;
  * переименование с суффиксом -partial-set-cover — имя честно
    говорит, что это ЦЕЛОЧИСЛЕННАЯ ФАЗА ПОКРЫТИЯ, не задача целиком;
  * машинный манифест экспорта (export-manifest.json): суммы SHA-256,
    счётчики из справочника, перечни included/excluded, совместимость.

Предупреждения — НЕ украшение: файл уедет в чужой решатель, и всё,
чего в модели принципиально нет (правила легальности цепочек
применены при ГЕНЕРАЦИИ кандидатов; пороги и файл условий — снаружи;
пул колонок не полное пространство задачи), обязано ехать вместе с
ним, иначе чужой оптимум примут за оптимум задачи авиакомпании.
Судья результата — сертификация CDPM, и это тоже сказано в шапке."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: Чего в модели покрытия ПРИНЦИПИАЛЬНО нет — закрытый перечень
#: оговорок; каждая уезжает и в шапку файла, и в манифест.
EXCLUDED = (
    "правила легальности цепочек: применены при генерации кандидатов "
    "пула и в модели НЕ выражены (недопустимая цепочка просто "
    "отсутствует среди переменных)",
    "пороги пакета и файл условий расчёта: применены построителем "
    "снаружи модели",
    "пул кандидатов — НЕ полное пространство задачи: колонки "
    "порождаются поиском (column generation), у другого решателя "
    "нет способа добавить недостающую цепочку",
    "ростеринг (назначение людей на цепочки), резерв, допуски, "
    "отпуска: вторая фаза конвейера, в модели покрытия их нет",
)

#: Что модель НЕСЁТ (сверяется со справочником построителя).
INCLUDED = (
    "покрытие: каждый рейс-обязательство ровно одной цепочкой",
    "прочие рейсы (транспорт): не более одной цепочки",
    "лимиты перегонов и ёмкости баз — по профилю запуска "
    "(перечень ограничений в справочнике .map.json)",
    "целевая: критерий авиакомпании (min), см. objective справочника",
)

SOLVERS = ("CPLEX", "Gurobi", "HiGHS", "SCIP", "CBC")


class MpsManifestError(ValueError):
    """Сопровождение не собирается; причина названа (fail-closed)."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _header_lines(kind: str, map_doc: dict, package_sha: str,
                  source_label: str) -> list[str]:
    mark = "*" if kind == "mps" else "\\"
    obj = map_doc.get("objective") or {}
    lines = [
        "=" * 68,
        "ЧАСТИЧНАЯ ЗАДАЧА: целочисленная фаза покрытия пейринга "
        "(partial set cover)",
        "источник: %s" % source_label,
        "отпечаток пакета постановки (sha256): %s" % package_sha,
        "пул кандидатов: %s; обязательств покрытия: %s"
        % (map_doc.get("pool"), map_doc.get("obligations")),
        "целевая: %s (%s)" % (obj.get("kind"), obj.get("sense")),
        "справочник имён: <этот-файл>.map.json "
        "(переменная -> цепочка, ограничение -> рейс)",
        "-" * 68,
        "ЧТО В МОДЕЛИ ЕСТЬ:",
        *("  + " + s for s in INCLUDED),
        "ЧЕГО В МОДЕЛИ ПРИНЦИПИАЛЬНО НЕТ:",
        *("  - " + s for s in EXCLUDED),
        "-" * 68,
        "судья результата — сертификация CDPM против свода правил; "
        "оптимум этой модели не равен оптимуму задачи целиком",
        "совместимость: " + ", ".join(SOLVERS),
        "=" * 68,
    ]
    return [f"{mark} {s}".rstrip() for s in lines]


def build_export(out_dir, *, mps_path=None, lp_path=None,
                 map_path, package_sha256: str,
                 source_label: str) -> dict:
    """Сопровождение экспорта: аннотированные копии с суффиксом
    -partial-set-cover, справочник рядом, машинный манифест.

    Возвращает манифест (он же пишется export-manifest.json).
    Исходные файлы построителя не трогаются (его артефакты — его
    след); аннотированная копия — байты оригинала ПОСЛЕ шапки, сумма
    оригинала в манифесте — сверка, что шапка ничего не съела."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    map_p = Path(map_path)
    if not map_p.is_file():
        raise MpsManifestError(
            f"справочника нет: {map_p} — экспорт без карты имён "
            f"нечитаем, сопровождение не собирается")
    map_doc = json.loads(map_p.read_text(encoding="utf-8"))
    for key in ("variables", "constraints", "objective", "pool",
                "obligations"):
        if key not in map_doc:
            raise MpsManifestError(
                f"справочник {map_p.name} без поля {key!r} — форма "
                f"словаря решателя нарушена")
    files: list[dict] = []
    for kind, src in (("mps", mps_path), ("lp", lp_path)):
        if src is None:
            continue
        src = Path(src)
        if not src.is_file():
            raise MpsManifestError(f"файла модели нет: {src}")
        body = src.read_bytes()
        head = "\n".join(_header_lines(kind, map_doc, package_sha256,
                                       source_label)) + "\n"
        dst = out / (src.stem + "-partial-set-cover" + src.suffix)
        dst.write_bytes(head.encode("utf-8") + body)
        files.append({"name": dst.name, "kind": kind,
                      "sha256": _sha(dst),
                      "original": src.name,
                      "original_sha256": hashlib.sha256(body)
                      .hexdigest(),
                      "bytes": dst.stat().st_size})
    if not files:
        raise MpsManifestError(
            "ни одного файла модели не подано (mps_path/lp_path)")
    map_dst = out / (map_p.name if map_p.parent != out else map_p.name)
    if map_p.parent != out:
        map_dst.write_bytes(map_p.read_bytes())
    manifest = {
        "export": "partial-set-cover",
        "source": source_label,
        "package_sha256": package_sha256,
        "pool": map_doc.get("pool"),
        "obligations": map_doc.get("obligations"),
        "uncoverable_flights": map_doc.get("uncoverable_flights"),
        "objective": map_doc.get("objective"),
        "files": files,
        "map": {"name": map_dst.name, "sha256": _sha(map_dst)},
        "included": list(INCLUDED),
        "excluded": list(EXCLUDED),
        "solvers": list(SOLVERS),
        "judge": "сертификация CDPM против свода правил",
    }
    (out / "export-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    return manifest
