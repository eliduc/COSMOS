"""Миграция каталога прежнего выпуска: честный блок покрытия.

Каталоги, выпущенные до появления блока `coverage`, больше не публикуются
по одному факту существования файла (внешний аудит R6, P1-15). Этот
инструмент НЕ объявляет их годными — он ПЕРЕСЧИТЫВАЕТ покрытие по
payload'у набора и штампует только то, что доказал.

Счёт идёт в пространстве ИДЕНТИЧНОСТИ участков, а не множеством номеров
рейсов (внешний аудит R7, P0-01: множество номеров схлопывало два
вхождения одного номера, и одна запись каталога «покрывала» оба).
Правила отказа:

* повторный номер без материализованного flightLegId — отказ: адрес
  участка не строится;
* запись каталога ссылается голым номером на повторный номер — отказ:
  какое из вхождений она покрывает, неизвестно;
* запись ссылается на имя, которого в payload'е нет, — отказ: каталог
  от других данных;
* есть непокрытые участки — отказ: почему они не покрыты, знал только
  прогон, штамповать «годен» задним числом нельзя.

В штампуемый блок кладётся происхождение: sha256 payload'а и sha256
каталога ДО правки — свидетельство привязано к конкретной паре файлов,
а не к «какому-то каталогу».

Запуск: python tools/migrate_catalog_coverage.py --payload <p.json>
        --catalog <cat.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--catalog", type=Path, required=True)
    args = ap.parse_args(argv)

    payload_raw = args.payload.read_bytes()
    catalog_raw = args.catalog.read_bytes()
    payload = json.loads(payload_raw.decode("utf-8"))
    doc = json.loads(catalog_raw.decode("utf-8"))
    if isinstance(doc.get("coverage"), dict):
        print("каталог уже несёт блок coverage — миграция не нужна")
        return 0

    from _leg_identity import addr_of, fn_counts
    counts = fn_counts(payload)
    flights = payload.get("flights") or []
    addrs: set[str] = set()
    for f in flights:
        addr = addr_of(f, counts)
        if addr is None:
            print("ОТКАЗ: рейс %s встречается в payload'е %d раз без "
                  "flightLegId — адрес участка не строится, честный счёт "
                  "покрытия невозможен." % (f.get("flightNumber"),
                                            counts.get(str(f.get(
                                                "flightNumber")), 0)))
            return 1
        addrs.add(addr)
    if len(addrs) != len(flights):
        print("ОТКАЗ: %d рейсов дают лишь %d различимых адресов — участки "
              "с одинаковыми flightNumber и flightLegId неразличимы, "
              "честный счёт покрытия невозможен (внешний аудит R8, P1-01)."
              % (len(flights), len(addrs)))
        return 1
    dup_numbers = {fn for fn, n in counts.items() if n > 1}

    covered: set[str] = set()
    for u in doc.get("selected") or []:
        for x in u.get("active_fns") or []:
            x = str(x)
            if x in dup_numbers:
                print("ОТКАЗ: запись каталога ссылается голым номером %r "
                      "на повторный номер — какое из %d вхождений она "
                      "покрывает, неизвестно; при повторных номерах каталог "
                      "обязан нести flightLegId." % (x, counts[x]))
                return 1
            if x not in addrs:
                print("ОТКАЗ: запись каталога ссылается на участок %r, "
                      "которого в payload'е нет, — каталог от других "
                      "данных, штамповать нельзя." % x)
                return 1
            covered.add(x)

    uncovered = sorted(addrs - covered)
    if uncovered:
        print("ОТКАЗ: %d участков payload'а не покрыты каталогом (%s...) — "
              "причину знал только прогон; пересоберите каталог этапом 1, "
              "штамповать «годен» задним числом нельзя."
              % (len(uncovered), ", ".join(uncovered[:5])))
        return 1

    doc["coverage"] = {
        "legs_total": len(flights), "uncovered": 0,
        "no_pair_in_horizon": 0, "not_built": 0,
        "complete": True, "verdict": "OK",
        "source": "coverage-migration",
        # происхождение: свидетельство относится ровно к этой паре файлов
        "payload_sha256": hashlib.sha256(payload_raw).hexdigest(),
        "catalog_sha256_before": hashlib.sha256(catalog_raw).hexdigest(),
    }
    args.catalog.write_text(json.dumps(doc, ensure_ascii=False),
                            encoding="utf-8")
    print("каталог %s: покрытие пересчитано (%d участков, все покрыты), "
          "verdict OK проштампован; происхождение записано"
          % (args.catalog.name, len(flights)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
