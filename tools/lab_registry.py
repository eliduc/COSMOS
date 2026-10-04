"""Реестр находок «Автоулучшение → Алгоритмы»: экспорт и импорт.

Находка состоит из двух разных вещей. Каталожная часть — факт о публикации
(заголовок, изложение, ссылка, план внедрения, ожидаемый выигрыш, дата
первоисточника, оценка эффективности): она общесистемная и обязана быть
одинаковой на всех трёх контурах. Конвейерная часть — статус, ветка,
метрики фаз, принявший решение: она СВОЯ у каждого контура и не переносится
никогда, иначе импорт затрёт решение оператора.

Носитель каталога — `data/lab-registry.jsonl` в репозитории: одна строка на
находку, сортировка по dedup_key, фиксированный порядок ключей. Файл едет на
площадки тем же бандлом, что и код, — отдельного канала между контурами не
появляется, а история правок остаётся в git.

    python tools/lab_registry.py export --site <каталог площадки>
    python tools/lab_registry.py import --site <каталог площадки> [--apply]

Импорт без `--apply` — холостой: печатает, что сделал бы, и ничего не пишет.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                  # noqa: E402

# Отчёт печатается по-русски: без этого инструмент падает
# UnicodeEncodeError в обычной консоли Windows (cp1251) — причём ПОСЛЕ
# записи в базу, то есть работа удалась, а код возврата врёт.
force_utf8_console()

# Поля каталога, которые несёт файл реестра. Порядок фиксирован: два
# экспорта одной базы обязаны давать побайтно одинаковый файл, иначе diff
# пакета зарастает шумом и правку в нём не разглядеть.
FIELD_ORDER = ("dedup_key", "title", "summary", "source_url",
               "integration_plan", "expected_gain", "published_at", "impact")
REQUIRED = ("dedup_key", "title", "summary")
DEFAULT_REGISTRY = Path("data") / "lab-registry.jsonl"


class RegistryError(RuntimeError):
    """Реестр или площадка непригодны — громкий отказ вместо тихой правки."""


def _load_site(site: Path):
    """Импорт модуля лаборатории ИМЕННО той площадки, с которой работаем.

    Путь к базе строится от корня репозитория площадки, поэтому подмена
    sys.path — единственное, что определяет, куда пойдёт запись. Проверяем
    результат явно: молча писать не в тот контур недопустимо."""
    site = site.resolve()
    if not (site / "webapp" / "evolution.py").exists():
        raise RegistryError(f"не похоже на площадку COSMOS: {site}")
    sys.path.insert(0, str(site))
    from webapp import evolution, users            # noqa: PLC0415
    db = Path(users.USERS_DB_PATH).resolve()
    if site not in db.parents:
        raise RegistryError(
            f"база {db} лежит вне площадки {site}: отказ, чтобы не записать "
            f"в чужой контур")
    return evolution, db


def _catalog_of(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row.get(k) for k in FIELD_ORDER}


def export(site: Path, out: Path, evolution=None) -> int:
    evolution, db = ((evolution, Path("<injected>"))
                     if evolution is not None else _load_site(site))
    rows = evolution.list_all()
    records = sorted((_catalog_of(r) for r in rows),
                     key=lambda r: r["dedup_key"])
    out.parent.mkdir(parents=True, exist_ok=True)
    # Явный "\n" и newline="": на Windows иначе получится CRLF, и файл,
    # выгруженный на разных машинах, будет отличаться каждым байтом строки.
    with out.open("w", encoding="utf-8", newline="") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False,
                                sort_keys=False) + "\n")
    print(f"экспорт: {len(records)} находок из {db}")
    print(f"реестр:  {out}")
    return 0


def _read_registry(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RegistryError(f"реестра нет: {path}")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(),
                               start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RegistryError(f"строка {num}: не разобрана как JSON: {exc}")
        if not isinstance(rec, dict):
            raise RegistryError(f"строка {num}: ожидался объект")
        missing = [k for k in REQUIRED
                   if not isinstance(rec.get(k), str) or not rec[k].strip()]
        if missing:
            raise RegistryError(
                f"строка {num}: пустые обязательные поля {missing}")
        extra = [k for k in rec if k not in FIELD_ORDER]
        if extra:
            raise RegistryError(
                f"строка {num}: посторонние поля {sorted(extra)}; "
                f"реестр несёт только каталог {list(FIELD_ORDER)}")
        if rec["dedup_key"] in seen:
            raise RegistryError(
                f"строка {num}: повтор ключа {rec['dedup_key']!r}")
        seen.add(rec["dedup_key"])
        records.append(rec)
    return records


def import_(site: Path, src: Path, apply: bool, evolution=None) -> int:
    evolution, db = ((evolution, Path("<injected>"))
                     if evolution is not None else _load_site(site))
    records = _read_registry(src)
    have = {r["dedup_key"]: r for r in evolution.list_all()}
    created = updated = unchanged = refused = 0
    can_update = hasattr(evolution, "update_catalog")

    for rec in records:
        key = rec["dedup_key"]
        cur = have.get(key)
        if cur is None:
            created += 1
            print(f"  + {rec['title'][:66]}")
            if apply:
                ok, row = evolution.add_finding(
                    title=rec["title"], summary=rec["summary"],
                    source_url=rec.get("source_url"),
                    integration_plan=rec.get("integration_plan"),
                    expected_gain=rec.get("expected_gain"))
                if rec.get("published_at"):
                    evolution.set_published(int(row["id"]),
                                            rec["published_at"])
                if rec.get("impact"):
                    evolution.attach_impact(int(row["id"]), rec["impact"])
            continue

        text_diff = {k: rec.get(k) for k in
                     ("title", "summary", "source_url", "integration_plan",
                      "expected_gain")
                     if rec.get(k) != cur.get(k)}
        pub_diff = (rec.get("published_at")
                    and rec["published_at"] != cur.get("published_at"))
        imp_diff = rec.get("impact") and rec["impact"] != cur.get("impact")
        if not (text_diff or pub_diff or imp_diff):
            unchanged += 1
            continue

        if text_diff and not can_update:
            # Площадка на старом коде: молча оставить расхождение нельзя,
            # переписать сырым SQL — тем более. Говорим вслух и не трогаем.
            refused += 1
            print(f"  ! текст расходится, площадка без update_catalog: "
                  f"{cur['title'][:52]} -> поля {sorted(text_diff)}")
            text_diff = {}

        updated += 1
        print(f"  ~ {cur['title'][:60]} "
              f"[{'текст ' if text_diff else ''}"
              f"{'дата ' if pub_diff else ''}{'оценка' if imp_diff else ''}]")
        if apply:
            imp_id = int(cur["id"])
            if text_diff:
                evolution.update_catalog(imp_id, text_diff)
            if pub_diff:
                evolution.set_published(imp_id, rec["published_at"])
            if imp_diff:
                evolution.attach_impact(imp_id, rec["impact"])

    only_local = sorted(set(have) - {r["dedup_key"] for r in records})
    print()
    print(f"площадка: {db}")
    print(f"реестр:   {src} ({len(records)} записей)")
    print(f"создано {created}, обновлено {updated}, без изменений {unchanged}, "
          f"отказано {refused}")
    if only_local:
        print(f"есть локально, но нет в реестре: {len(only_local)} "
              f"(не удаляем — реестр не владеет удалением)")
    if not apply:
        print("ХОЛОСТОЙ ПРОГОН: ничего не записано, повторите с --apply")
    return 1 if refused else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="выгрузить каталог площадки в реестр")
    e.add_argument("--site", required=True, type=Path)
    e.add_argument("--out", type=Path, default=None)
    i = sub.add_parser("import", help="внести реестр в каталог площадки")
    i.add_argument("--site", required=True, type=Path)
    i.add_argument("--in", dest="src", type=Path, default=None)
    i.add_argument("--apply", action="store_true",
                   help="писать в базу (без него — холостой прогон)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "export":
            return export(a.site, a.out or (a.site / DEFAULT_REGISTRY))
        return import_(a.site, a.src or (a.site / DEFAULT_REGISTRY), a.apply)
    except RegistryError as exc:
        print(f"ОТКАЗ: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
