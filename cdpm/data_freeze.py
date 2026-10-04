# -*- coding: utf-8 -*-
"""Заморозка операционных данных (линия данных, пакет ДЛ-3).

Таблицы xlsx / csv превращаются в набор данных единой формы (обмен с
генератором проверок ДЛ-2) с манифестом SHA-256 каждого оригинала —
та же дисциплина, что у заморозки документов правил: данные, по
которым строится отчёт, зафиксированы криптографически.

    python -m cdpm.data_freeze ФАЙЛ... --out-dir КАТАЛОГ
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

DATA_FORMATS = (".xlsx", ".csv", ".json")


class DataFreezeError(ValueError):
    """Честный отказ: причина всегда названа."""


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        if v.hour == 0 and v.minute == 0 and v.second == 0:
            return v.strftime("%d.%m.%Y")
        return v.strftime("%d.%m.%Y %H:%M")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _sheet_rows(header: list, raw_rows) -> tuple[list, list]:
    """Колонки из первой строки (пустые и повторные имена получают
    суффикс), строки — словари; полностью пустые строки отбрасываются."""
    columns: list[str] = []
    seen: dict[str, int] = {}
    for i, h in enumerate(header):
        name = _cell_str(h) or f"колонка-{i + 1}"
        n = seen.get(name, 0)
        seen[name] = n + 1
        columns.append(name if n == 0 else f"{name} ({n + 1})")
    rows = []
    for raw in raw_rows:
        row = {columns[i]: _cell_str(raw[i]) if i < len(raw) else ""
               for i in range(len(columns))}
        if any(v for v in row.values()):
            rows.append(row)
    return columns, rows


def _read_xlsx(path: Path) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheets = {}
    for ws in wb.worksheets:
        raw = [[c for c in row] for row in ws.iter_rows(
            values_only=True)]
        if not raw:
            continue
        columns, rows = _sheet_rows(list(raw[0]), raw[1:])
        sheets[ws.title] = {"columns": columns, "rows": rows}
    wb.close()
    if not sheets:
        raise DataFreezeError(f"{path.name}: в книге нет непустых "
                              f"листов")
    return sheets


def _read_csv(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    delimiter = ";" if text.count(";") > text.count(",") else ","
    raw = list(csv.reader(text.splitlines(), delimiter=delimiter))
    if not raw:
        raise DataFreezeError(f"{path.name}: пустой файл")
    columns, rows = _sheet_rows(list(raw[0]), raw[1:])
    return {"Данные": {"columns": columns, "rows": rows}}


def _json_scalar(v, where: str, path: Path) -> str:
    """Скаляр записи в строку ячейки; вложенный контейнер — честный
    отказ с путём (плоские записи — форма линии данных, как листы
    xlsx)."""
    if isinstance(v, (dict, list)):
        raise DataFreezeError(
            f"{path.name}: вложенное значение в записи ({where}) — "
            f"линия данных ждёт ПЛОСКИЕ записи со скалярами; "
            f"расплющите выгрузку либо согласуйте расширение формы")
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _read_json(path: Path) -> dict:
    """Выгрузка данных JSON (запрос одной из линий, 2026-08-26): объект
    «имя листа → массив плоских записей» либо массив записей верхним
    уровнем (один лист «Данные»). Колонки — объединение ключей в
    порядке первого появления; повторяющийся ключ — отказ (штатный
    разбор молча теряет первое значение)."""
    def no_dup(pairs):
        seen = {}
        for k, v in pairs:
            if k in seen:
                raise DataFreezeError(
                    f"{path.name}: повторяющийся ключ {k!r} в объекте "
                    f"JSON — тихая потеря данных запрещена")
            seen[k] = v
        return seen

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"),
                          object_pairs_hook=no_dup)
    except DataFreezeError:
        raise
    except json.JSONDecodeError as e:
        raise DataFreezeError(
            f"{path.name}: JSON не разбирается — строка {e.lineno}, "
            f"позиция {e.colno}: {e.msg}")
    if isinstance(data, list):
        sheet_map = {"Данные": data}
    elif isinstance(data, dict):
        sheet_map = data
    else:
        raise DataFreezeError(
            f"{path.name}: верхний уровень JSON — {type(data).__name__},"
            f" а линия данных ждёт объект «лист → записи» либо массив "
            f"записей")
    sheets: dict = {}
    for name, records in sheet_map.items():
        if not isinstance(records, list):
            raise DataFreezeError(
                f"{path.name}: лист {name!r} — не массив записей "
                f"({type(records).__name__})")
        columns: list[str] = []
        rows = []
        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                raise DataFreezeError(
                    f"{path.name}: лист {name!r}, запись {i} — не "
                    f"объект ({type(rec).__name__}); форма — массив "
                    f"плоских записей")
            row = {}
            for k, v in rec.items():
                if k not in columns:
                    columns.append(k)
                row[k] = _json_scalar(v, f"лист {name!r}, запись {i},"
                                         f" ключ {k!r}", path)
            if any(row.values()):
                rows.append(row)
        if rows:
            # Недостающие ключи записей — пустые ячейки (как в xlsx).
            rows = [{c: r.get(c, "") for c in columns} for r in rows]
            sheets[name] = {"columns": columns, "rows": rows}
    if not sheets:
        raise DataFreezeError(f"{path.name}: в JSON нет непустых "
                              f"листов с записями")
    return sheets


def freeze_data(paths: list[Path], out_dir: Path) -> dict:
    """Набор данных + манифест; возвращает счётчики (У-05)."""
    sources, tables = [], {}
    for i, path in enumerate(sorted(paths, key=lambda p: p.name),
                             start=1):
        if path.suffix.lower() not in DATA_FORMATS:
            raise DataFreezeError(
                f"{path.name}: формат {path.suffix!r} не "
                f"поддерживается (данные: {DATA_FORMATS})")
        sid = f"D{i}"
        ext = path.suffix.lower()
        sheets = (_read_xlsx(path) if ext == ".xlsx"
                  else _read_json(path) if ext == ".json"
                  else _read_csv(path))
        tables[sid] = sheets
        sources.append({
            "sid": sid, "file": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "rows_seen": sum(len(s["rows"]) for s in sheets.values()),
        })
    dataset = {"sources": sources, "tables": tables}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    manifest = {"files": sources,
                "sheets": {sid: sorted(sheets) for sid, sheets
                           in tables.items()}}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return {"files": len(sources),
            "sheets": sum(len(s) for s in tables.values()),
            "rows": sum(s["rows_seen"] for s in sources)}


def load_dataset(freeze_dir: Path) -> dict:
    path = freeze_dir / "dataset.json"
    if not path.is_file():
        raise DataFreezeError(f"набора данных {path} нет — сначала "
                              f"заморозка данных")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(
        description="Заморозка операционных данных CDPM")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)
    c = freeze_data([Path(f) for f in args.files], Path(args.out_dir))
    print(f"заморожено: файлов {c['files']}, листов {c['sheets']}, "
          f"строк {c['rows']}")
    print(f"набор: {Path(args.out_dir) / 'dataset.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
