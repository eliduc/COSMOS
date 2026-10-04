"""Выгрузка ЦЕПОЧЕК COSMOS в рабочий формат оптимизатора вендора
(format:5), 2026-07-28.

Чем отличается от `_payload_to_solver.py`. Тот выгружает НАЗНАЧЕНИЯ
ЛЮДЕЙ: берёт исходный файл вендора и заполняет поле `employee` в его же
строках, оставляя структуру цепочек нетронутой. Для результата первого
этапа это не годится — у нас структура СВОЯ: другие смены, другие
командировки, другие перелёты пассажиром. Заполнить её в чужие строки
нельзя, надо написать свои.

Что пишется:
  * `legs`, `stations`, `parameters` — из исходного файла БЕЗ изменений:
    это данные, а не наш результат, и трогать их незачем;
  * `duties` — наши смены: границы, тип судна, ярус состава экипажа,
    признак ночной смены;
  * `leg2Duties` — участок в смене с ролью: `STD`/`ENF`/`DBL` для
    рабочего рейса, `ONLY_DH`/`FIRST_DH`/`LAST_DH` для перелёта
    пассажиром;
  * `sequences` — командировка на каждое место экипажа, как у вендора:
    у него это записи вида (командировка x должность);
  * `employeeAssignments` — место в смене без человека (`employee: null`):
    назначение людей это отдельный этап, и выдавать пустое за сделанное
    нельзя.

Чего НЕ пишется и почему:
  * `score` и `metrics` — оценка и метрики описывают ЕГО функцию
    качества, которую COSMOS не воспроизводит. Оставить их значило бы
    выдать чужие числа за свои, поэтому они пустые;
  * `employees` — справочник сотрудников: персональные данные, для
    структуры цепочек не нужны.

Проверка выгрузки — ОБРАТНЫМ ИМПОРТОМ (`--verify`): полученный файл
читается тем же адаптером, что и оригинал вендора, и восстановленные из
него командировки сверяются с исходным каталогом. Совпадение множеств
участков по каждой командировке — единственное доказательство, что файл
читается его средствами так же, как задумано нашими.

Детерминированность: ни одного обращения к языковым моделям и к сети.

Использование:
    python _pairing_to_solver.py --payload payload.json --catalog cat.json
        --source dataset.json --out solution_format5.json
        [--check check.json] [--verify]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()

DEADHEAD_PREFIX = "dh%"
TIER_ROLE = ("STD", "ENF", "DBL")


def _dt(value: str) -> datetime:
    from _canonical_time import parse_utc
    return parse_utc(value)


def _zoned(dt_utc: datetime, tz_name: str | None) -> str:
    """Отметка в том же виде, в каком её пишет вендор: местное время со
    смещением и названием пояса. Без пояса — всемирное время."""
    if not tz_name:
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z[UTC]"
    try:
        from zoneinfo import ZoneInfo
        local = dt_utc.replace(tzinfo=timezone.utc).astimezone(
            ZoneInfo(str(tz_name)))
    except Exception:                                     # noqa: BLE001
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z[UTC]"
    off = local.utcoffset() or timedelta(0)
    total = int(off.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    return "%s%s%02d:%02d[%s]" % (local.strftime("%Y-%m-%dT%H:%M:%S"),
                                  sign, abs(total) // 60, abs(total) % 60,
                                  tz_name)


def convert(payload: dict, catalog: dict, source: dict,
            tiers: tuple = (720, 840, 960),
            base_tz_offset_min: int = 300) -> tuple[dict, dict]:
    """Каталог цепочек -> документ формата вендора. Возвращает (документ,
    отчёт проверки)."""
    check: dict = {"schema": "pairing-to-solver/v1"}
    errors: list[str] = []

    # Идентичность участка (внешний аудит R6, P0-01): выгрузка обязана
    # работать в том же wire-пространстве, что и каталог. Повтор
    # flightNumber без переименования схлопнул бы legs_by_fn молча;
    # каталог от другого payload'а — громкий отказ, как на этапе 2.
    from _leg_identity import assert_representable
    from _leg_rekey import rekey_or_passthrough
    assert_representable(payload, "pairing_to_solver.convert")
    payload, wire_map = rekey_or_passthrough(payload)
    cat_wm = catalog.get("wire_map") or {}
    if (wire_map or cat_wm) and cat_wm != wire_map:
        raise ValueError(
            "каталог собран не от этого payload'а: карты wire-имён "
            "не совпадают — выгрузка отменена")

    legs_by_fn: dict[str, dict] = {}
    tz_of: dict[str, str] = {a.get("code"): a.get("timeZone")
                             for a in (payload.get("airports") or [])}
    brief: dict[str, int] = {}
    debrief: dict[str, int] = {}
    for f in payload["flights"]:
        legs_by_fn[f["flightNumber"]] = f
        if f.get("briefingMin") is not None:
            brief[f["flightNumber"]] = int(f["briefingMin"])
        if f.get("debriefingMin") is not None:
            debrief[f["flightNumber"]] = int(f["debriefingMin"])
    src_id_of_fn = {f["flightNumber"]: f.get("sourceLegId")
                    for f in payload["flights"] if f.get("sourceLegId")}
    src_leg_ids = {str(x.get("id")) for x in (source.get("legs") or [])}

    duties: list[dict] = []
    leg2duties: list[dict] = []
    sequences: list[dict] = []
    assignments: list[dict] = []
    stats = Counter()

    for idx, unit in enumerate(catalog.get("selected") or []):
        # Неизвестная ссылка — жёсткая ошибка, а не тихий фильтр (внешний
        # аудит R6, P1-03): участок, которого нет в payload'е, означает
        # каталог от других данных, и «выгрузить что получится» — это
        # выгрузить другой план.
        unknown = sorted({x for key in ("active_fns", "fns")
                          for x in (unit.get(key) or [])
                          if x not in legs_by_fn}
                         | {x for g in (unit.get("duty_fns") or [])
                            for x in g if x not in legs_by_fn})
        if unknown:
            errors.append("цепочка %d ссылается на участки вне payload'а: %s"
                          % (idx, ", ".join(unknown[:5])))
            continue
        active = list(unit.get("active_fns") or [])
        allfns = list(unit.get("fns") or [])
        if not active:
            stats["цепочек без рабочих участков"] += 1
            continue
        groups = [list(g) for g in (unit.get("duty_fns") or []) if g]
        if not groups:
            errors.append("цепочка %d без границ смен: выгрузка в формат "
                          "вендора требует их" % idx)
            continue
        seats = list(unit.get("seats") or [])
        if not seats:
            errors.append("цепочка %d без мест экипажа" % idx)
            continue
        seq_id = "COSMOS-%06d" % idx
        rider = sorted(set(allfns) - set(active),
                       key=lambda x: legs_by_fn[x]["departureUTCDateTime"])

        duty_ids: list[str] = []
        for gi, g in enumerate(sorted(
                groups, key=lambda gg: legs_by_fn[gg[0]][
                    "departureUTCDateTime"])):
            g = sorted(g, key=lambda x: legs_by_fn[x]["departureUTCDateTime"])
            first, last = legs_by_fn[g[0]], legs_by_fn[g[-1]]
            t0 = _dt(first["departureUTCDateTime"])
            t1 = _dt(last["arrivalUTCDateTime"])
            w0 = t0 - timedelta(minutes=brief.get(g[0], 0))
            w1 = t1 + timedelta(minutes=debrief.get(g[-1], 0))
            work = (w1 - w0).total_seconds() / 60
            tier = 0
            for i, lim in enumerate(tiers):
                if work <= lim:
                    tier = i
                    break
            else:
                # Смена длиннее наибольшего предела — жёсткая ошибка
                # выгрузки (внешний аудит R6, P1-03): у вендора такого
                # яруса нет, и «записать наибольший» означает молча
                # объявить недопустимую смену допустимой.
                tier = len(tiers) - 1
                stats["смен свыше наибольшего предела"] += 1
                errors.append(
                    "цепочка %d: рабочее время смены %d мин превышает "
                    "наибольший предел %d мин — такой ярус в формате "
                    "вендора не существует" % (idx, int(work), tiers[-1]))
            did = "%s-D%02d" % (seq_id, gi)
            duty_ids.append(did)
            dep_tz = tz_of.get(first["departureAirport"])
            arr_tz = tz_of.get(last["arrivalAirport"])
            duties.append({
                "id": did,
                "start": _zoned(t0, dep_tz), "end": _zoned(t1, arr_tz),
                "workTimeStart": _zoned(w0, dep_tz),
                "workTimeEnd": _zoned(w1, arr_tz),
                "acType": first.get("aircraftType") or "ANY",
                "crewType": TIER_ROLE[tier],
                "firstDepartureStation": first["departureAirport"],
                "lastArrivalStation": last["arrivalAirport"],
                "flight": True, "ground": False, "workDuty": True,
                "rest": False, "sby": False, "sick": False, "split": False,
                "extended": False, "optional": False, "locked": False,
                "firstInSequence": gi == 0,
                "group": "FLIGHT", "feature": "STD",
                "dutyBeforePlanSince": False,
                "stationsVisited": sorted({legs_by_fn[x]["departureAirport"]
                                           for x in g}
                                          | {legs_by_fn[x]["arrivalAirport"]
                                             for x in g}),
                "limitations": [], "requirements": [],
            })
            for fn in g:
                leg2duties.append({"leg": src_id_of_fn.get(fn, fn),
                                   "duty": did,
                                   "crewRole": TIER_ROLE[tier]})
                stats["строк рабочих участков"] += 1

        # Перелёты пассажиром — отдельным нарядом цепочки: рабочей смены
        # они не образуют, но именно они привязывают её к базе.
        if rider:
            did = "%s-DH" % seq_id
            duty_ids.append(did)
            f0, f1 = legs_by_fn[rider[0]], legs_by_fn[rider[-1]]
            t0 = _dt(f0["departureUTCDateTime"])
            t1 = _dt(f1["arrivalUTCDateTime"])
            duties.append({
                "id": did,
                "start": _zoned(t0, tz_of.get(f0["departureAirport"])),
                "end": _zoned(t1, tz_of.get(f1["arrivalAirport"])),
                "acType": "ANY", "crewType": "NONE",
                "firstDepartureStation": f0["departureAirport"],
                "lastArrivalStation": f1["arrivalAirport"],
                "flight": False, "ground": False, "workDuty": False,
                "rest": False, "sby": False, "sick": False, "split": False,
                "extended": False, "optional": False, "locked": False,
                "firstInSequence": False, "group": "FLIGHT",
                "feature": "STD", "dutyBeforePlanSince": False,
                "stationsVisited": [], "limitations": [], "requirements": [],
            })
            for n, fn in enumerate(rider):
                role = "ONLY_DH"
                if len(rider) > 1:
                    role = ("FIRST_DH" if n == 0
                            else ("LAST_DH" if n == len(rider) - 1
                                  else "ONLY_DH"))
                src = src_id_of_fn.get(fn, fn)
                dh_id = DEADHEAD_PREFIX + str(src)
                leg2duties.append({
                    "leg": dh_id if dh_id in src_leg_ids else src,
                    "duty": did, "crewRole": role})
                stats["строк перелётов пассажиром"] += 1

        # Командировка на КАЖДОЕ место экипажа — так устроен его формат.
        t_start = min(_dt(legs_by_fn[x]["departureUTCDateTime"])
                      for x in allfns)
        t_end = max(_dt(legs_by_fn[x]["arrivalUTCDateTime"]) for x in allfns)
        for si, rank in enumerate(seats):
            full = "%s_%s_%d" % (seq_id, rank, si)
            sequences.append({
                "id": full, "sequenceId": seq_id, "crewPosition": rank,
                "departureStation": legs_by_fn[allfns[0]]["departureAirport"],
                "arrivalStation": legs_by_fn[allfns[-1]]["arrivalAirport"],
                "sequenceStart": _zoned(t_start, None),
                "sequenceEnd": _zoned(t_end, None),
                "locked": False,
            })
            for did in duty_ids:
                assignments.append({
                    "id": "%s_%s" % (did, si), "duty": did,
                    "sequence": full, "employee": None,
                    "preferredRank": rank, "locked": False,
                })
        stats["командировок"] += 1
        stats["мест экипажа"] += len(seats)

    doc = {
        "format": "5",
        "id": "cosmos-pairing",
        "description": "COSMOS: построение цепочек экипажа",
        "legs": source.get("legs") or [],
        "stations": source.get("stations") or [],
        "parameters": source.get("parameters") or {},
        "duties": duties,
        "leg2Duties": leg2duties,
        "sequences": sequences,
        "employeeAssignments": assignments,
        "employees": [],
        "metrics": [],
        "score": None,
    }
    check.update({k: v for k, v in stats.items()})
    check["duties"] = len(duties)
    check["sequences"] = len(sequences)
    check["assignments"] = len(assignments)
    check["errors"] = errors
    check["errors_total"] = len(errors)
    check["ok"] = not errors
    return doc, check


def verify_round_trip(out_path: Path, catalog: dict) -> dict:
    """Обратный импорт: файл читается ТЕМ ЖЕ адаптером, что и оригинал
    вендора, и восстановленные командировки сверяются с каталогом.

    Это единственное доказательство, что выгрузка читается его
    средствами так же, как задумано нашими: совпадение чисел в отчёте
    доказывало бы лишь то, что мы умеем считать свои же строки.
    """
    from _solver_to_payload import convert as read_back
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    _pay, _chk, errs, units = read_back(doc, out_path.name, "verify.json")

    def key(u):
        """Структурный ключ командировки: рабочий состав И перегоны, с
        кратностью. Прежнее множество множеств (внешний аудит R6, P1-03)
        теряло кратность одинаковых командировок и перегоны — совпадение
        «по составу участков» ничего не говорило о совпадении структуры.
        Границы смен и места экипажа юнит обратного импорта не несёт
        (адаптер вендора отдаёт их отдельными структурами), поэтому в ключ
        круговой сверки они не входят — это ограничение названо в отчёте."""
        rider = tuple(sorted(set(u.get("fns") or ())
                             - set(u.get("active_fns") or ())))
        return (tuple(sorted(u.get("active_fns") or ())), rider)

    ours = Counter(key(u) for u in (catalog.get("selected") or [])
                   if u.get("active_fns"))
    # Достроечные юниты импортёра (kind="fill": одиночные участки по моде)
    # не выгружались нами и командировками не являются — сверке подлежат
    # только цепочки.
    back = Counter(key(u) for u in units
                   if u.get("active_fns")
                   and (u.get("kind") or "chain") == "chain")
    lost = ours - back
    extra = back - ours
    return {"область сверки": ("рабочий состав и перегоны, с кратностью; "
                               "границы смен и места экипажа формат назад "
                               "не переносит — вне сверки"),
            "командировок у нас": sum(ours.values()),
            "командировок после обратного импорта": sum(back.values()),
            "совпало": sum((ours & back).values()),
            "потеряно": sum(lost.values()),
            "лишних": sum(extra.values()),
            "ошибок чтения": len(errs)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--catalog", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True,
                    help="исходный файл вендора: участки, станции, настройки")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--check", type=Path, default=None)
    ap.add_argument("--verify", action="store_true",
                    help="обратный импорт и сверка командировок с каталогом")
    args = ap.parse_args(argv)

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    source = json.loads(args.source.read_text(encoding="utf-8"))
    doc, check = convert(payload, catalog, source)

    if check["errors"]:
        # Файл при ошибках НЕ пишется (внешний аудит R6, P1-03): выгрузка
        # с ошибками — это другой план, и лежать под рабочим именем ему
        # нельзя. Отчёт проверки пишется всегда — по нему видно, что и
        # почему отклонено.
        print("ОТКАЗ выгрузки — файл не записан:", flush=True)
        for e in check["errors"][:10]:
            print("  " + e, flush=True)
        if args.check:
            args.check.write_text(
                json.dumps(check, ensure_ascii=False, indent=1),
                encoding="utf-8")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    if args.verify:
        rt = check["round_trip"] = verify_round_trip(args.out, catalog)
        # Отрицательная круговая сверка — ГЕЙТ, а не приложение к отчёту
        # (внешний аудит R7, P1-03): потеря, лишняя командировка или
        # ошибка обратного чтения означают, что выгрузка не переносит
        # план, — файл снимается, код возврата ненулевой.
        if rt["потеряно"] or rt["лишних"] or rt["ошибок чтения"]:
            check["ok"] = False
            check["errors"].append(
                "круговая сверка: потеряно %s, лишних %s, ошибок чтения %s"
                % (rt["потеряно"], rt["лишних"], rt["ошибок чтения"]))
            check["errors_total"] = len(check["errors"])
            print("ОТКАЗ круговой сверки — файл снят: потеряно %s, "
                  "лишних %s, ошибок чтения %s"
                  % (rt["потеряно"], rt["лишних"], rt["ошибок чтения"]),
                  flush=True)
            try:
                args.out.unlink()
            except OSError:
                pass
            if args.check:
                args.check.write_text(
                    json.dumps(check, ensure_ascii=False, indent=1),
                    encoding="utf-8")
            return 1

    if args.check:
        args.check.write_text(json.dumps(check, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print("командировок %s, смен %s, мест %s -> %s (%d КБ)"
          % (check.get("командировок"), check["duties"],
             check.get("мест экипажа"), args.out.name,
             args.out.stat().st_size // 1024), flush=True)
    if "round_trip" in check:
        rt = check["round_trip"]
        print("обратный импорт: совпало %s из %s, потеряно %s, лишних %s, "
              "ошибок чтения %s"
              % (rt["совпало"], rt["командировок у нас"], rt["потеряно"],
                 rt["лишних"], rt["ошибок чтения"]), flush=True)
    return 0 if check["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
