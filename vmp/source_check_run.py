# -*- coding: utf-8 -*-
"""ФПР — финальная проверка решения против исходных документов
авиакомпании (ИДАК): клаузы заморозки прогона -> слепой читатель ->
детерминированный прогон по решениям -> протокол.

Протокол — ЧАСТЬ ВЫХОДНОГО КОМПЛЕКТА решения (решение владельца
2026-08-31): комплект выгрузки = решение + сертификат + протокол.

Проверяются ОБА решения: пейринг (каталог цепочек построителя) и
ростеринг (решение решателя). Цепочка пейринга проверяется как
собственная линия времени (её база — аэропорт вылета первого
плеча); правила О ЛЮДЯХ (численность, роли, недоступности, допуски,
резерв) на пейринге честно помечаются «судится на ростеринге», а не
пропускаются молча.

CLI:
  python -m vmp.source_check_run --run-dir <каталог прогона CDPM>
      [--solution <решение ростеринга JSON>]
      [--pairing-catalog <builder-catalog.json>]
      [--package <task-package.zip>]  (рейсы для каталога пейринга)
      [--model <модель>] [--scope any|local-only]
      [--requested cloud|local|auto] [--effort low|medium|high]
      [--channel cli|api|local]  (канал читателя; наблюдённый канал
                                  пишется в протокол и снимки)
      [--applied <applied-rules.json>]   (показания решателя прогона
                                  ростеринга — граница «стыковка /
                                  отдых» T для исполнителя rest_min)
      [--builder-input <builder-input.json>]  (ведомость входа
                                  построителя — T пейринга из --sit-max;
                                  без флага ищется рядом с каталогом)

Артефакты: <run-dir>/source-check/protocol.md + checks.json; копия
рядом с файлом решения (<решение>.source-check.md);
data_root()/source-check/{last.json, protocol-last.md} — снимок для
блока «Сверка с документами а/к» Конвейера и ручки выгрузки."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Роль модели в записи расхода — из ОДНОГО словаря ролей
#: продукта (раздел 3.1 `models[]` проекта «Запись о прогоне»):
#: двух написаний одной роли быть не должно. Импорт после
#: правки sys.path — корень репозитория виден только отсюда.
from vmp.run.llm_exchanges import (       # noqa: E402
    ROLE_CODE_SOURCE_READER as READER_ROLE_CODE, role_words)

#: Виды проверок О ЛЮДЯХ: на пейринг-каталоге (без назначений)
#: судятся только на ростеринге — протокол говорит это явно.
PEOPLE_KINDS = ("crew_size_exact", "skill_required",
                "unavailability", "type_rating", "reserve_daily_min")

#: Компиляция, взятая из кэша: вызова модели не было, и токены прежней
#: компиляции этому прогону не приписываются. Вызовов НОЛЬ и токенов
#: ноль — величина ИЗМЕРЕННАЯ (`counted: true`), а не неизвестная:
#: читателя не звали вовсе, и честнее сказать ноль, чем «не считается».
CACHED_COMPILE_NOTE = ("чтение из кэша: проверки взяты из кэша "
                       "компиляции — модель не вызывалась")


def no_usage_note(channel=None) -> str:
    """Причина словами там, где канал читателя расхода не назвал:
    неизвестное за ноль не выдаётся, строка остаётся с `counted: false`
    и этой причиной (канал CLI подписки токенов не отдаёт)."""
    return ("канал читателя %s расхода в токенах не назвал"
            % (channel or "не назван"))


def load_clauses(run_dir: Path) -> list[dict]:
    rows: list[dict] = []
    cl_dir = run_dir / "freeze" / "clauses"
    if not cl_dir.is_dir():
        raise SystemExit(f"нет каталога клауз заморозки: {cl_dir}")
    for p in sorted(cl_dir.glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        for c in doc.get("clauses") or []:
            rows.append({"sid": p.stem, "anchor": c.get("anchor"),
                         "text": c.get("text")})
    if not rows:
        raise SystemExit("клаузы заморозки пусты")
    from cdpm import source_answer_evidence
    rows.extend(source_answer_evidence.clauses_from_run(run_dir))
    return rows


# ------------------------------------------------- пейринг-каталог

def clean_flights_from_package(pkg_path: Path) -> dict:
    """Карта рейсов из clean-data принятого пакета:
    id вида «Номер|ГГГГ-ММ-ДДTЧЧ:ММ:СС» (ключ каталога пейринга) ->
    {dep, arr, t0, t1}. Кривые строки пропускаются с перечнем."""
    import zipfile
    with zipfile.ZipFile(pkg_path) as z:
        cd = json.loads(z.read("clean-data.json"))
    out: dict[str, dict] = {}
    for sheets in (cd.get("tables") or {}).values():
        for name, sheet in (sheets or {}).items():
            if str(name).strip().lower() not in (
                    "рейсы", "legs", "flights"):
                continue
            for row in (sheet or {}).get("rows") or ():
                num = str(row.get("Номер рейса")
                          or row.get("flightNumber") or "").strip()
                date = str(row.get("Дата") or "").strip()
                t_dep = str(row.get("Время вылета") or "").strip()
                t_arr = str(row.get("Время прилёта") or "").strip()
                dep = str(row.get("Вылет") or "").strip()
                arr = str(row.get("Прилёт") or "").strip()
                if not (num and date and t_dep and t_arr):
                    continue
                if len(t_dep) == 5:
                    t_dep += ":00"
                if len(t_arr) == 5:
                    t_arr += ":00"
                key = f"{num}|{date}T{t_dep}"
                out[key] = {"dep": dep, "arr": arr,
                            "t0": f"{date}T{t_dep}",
                            "t1": f"{date}T{t_arr}"}
    return out


def catalog_to_solution(catalog: dict, flights_map: dict) -> dict:
    """Каталог цепочек построителя -> форма решения для исполнителя
    ФПР: каждая цепочка — собственная линия времени («сотрудник» с
    базой = аэропорт вылета первого плеча). Плечо без записи в
    карте рейсов — честный отказ (молчаливый пропуск исказил бы
    вердикты)."""
    flights: dict[str, dict] = {}
    employees: list[dict] = []
    fas: list[dict] = []
    missing: list[str] = []
    for i, chain in enumerate(catalog.get("selected") or ()):
        cid = f"chain-{i}"
        fns = chain.get("fns") or ()
        first_dep = None
        for fn in fns:
            rec = flights_map.get(fn)
            if rec is None:
                missing.append(fn)
                continue
            if first_dep is None:
                first_dep = rec["dep"]
            flights.setdefault(fn, {
                "flightLegId": fn, "flightNumber": fn,
                "departureAirport": rec["dep"],
                "departureUTCDateTime": rec["t0"],
                "arrivalAirport": rec["arr"],
                "arrivalUTCDateTime": rec["t1"],
                "aircraftType": None})
            fas.append({"flight": fn, "employee": cid,
                        "requiredSkill": None, "chainId": cid,
                        "deadhead": False})
        employees.append({"id": cid, "homeAirport": first_dep,
                          "skills": [], "unavailableDays": [],
                          "typeRatings": []})
    if missing:
        raise SystemExit(
            "плечи каталога без записи в книге рейсов: "
            + ", ".join(sorted(set(missing))[:5]))
    return {"flights": list(flights.values()),
            "employees": employees, "flightAssignments": fas}


def _filter_specs_for_pairing(specs: list[dict]) -> tuple[list, list]:
    """(исполняемые на пейринге, пропущенные-людские)."""
    run, skip = [], []
    for s in specs:
        (skip if s.get("kind") in PEOPLE_KINDS else run).append(s)
    return run, skip


# ------------------------------------ граница «стыковка / отдых» прогона

#: Имя показания решателя о ручке максимальной стыковки (Kotlin
#: RunThresholds: ручка пакета max_sit_duration → day_break_threshold_min;
#: в пакетном прогоне без ручки провайдер читает 0 — тогда граница
#: «стыковка / отдых» выводится из пола минимального отдыха тех же
#: показаний: vmp.sit_boundary).
DAY_BREAK_READING = "day_break_threshold_min"


def builder_input_beside(catalog_path) -> dict | None:
    """Ведомость входа построителя (builder-input.json) рядом с
    каталогом цепочек нажатия «Пейринг» (кнопка кладёт их в один
    каталог button-pairing/); нет или не читается — None."""
    if catalog_path is None:
        return None
    p = Path(catalog_path).parent / "builder-input.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def rest_boundary(tl_scope: str, *, applied: dict | None = None,
                  builder_input: dict | None = None,
                  run_id: str | None = None,
                  note: str | None = None) -> dict:
    """Действующая граница «стыковка / отдых» T ЭТОГО прогона для
    исполнителя rest_min — из артефактов прогона, не из константы
    (SOURCE-REST-R1, единая версия 06.09.2026: читатель ИДАК судит тем
    же контрактом, что решатель, для этого прогона), по ОДНОМУ
    определению с кнопкой «Пейринг» и решателем (vmp.sit_boundary,
    решение владельца 06.09.2026): правило максимальной стыковки пакета,
    иначе пол минимального отдыха; ни того ни другого — не определено.

    roster — показания решателя этого runId (`GET /schedules/run/<id>/
    applied-rules`, lastRun.profileReadings): day_break_threshold_min > 0
    — ручка пакета max_sit_duration (максимальная стыковка), T из неё;
    0 (ручки в пакете нет) — критерий границы смены выводится из пола
    минимального отдыха ПО МЕСТУ тех же показаний (min_rest_home_min /
    min_rest_outstation_min; показанием T — меньший) с провенансом
    «выведено из пола отдыха» и явным модельным упрощением
    (sit_boundary.ASSUMPTION) — ровно поведение решателя по построению
    (нормативные смены режутся по полу отдыха по месту); нет и пола —
    None с причиной «граница смены не определена правилами пакета».
    Правила-источники — из lastRun.thresholds.applied[].rules. Показания
    чужого runId — не показания этого прогона (None с причиной).

    pairing — ведомость входа построителя нажатия «Пейринг»
    (builder-input.json): args[«--sit-max»] (кнопка подаёт его только
    при правиле максимальной стыковки) с происхождением
    provenance[«--sit-max»].from. Без --sit-max построитель режет по
    полу отдыха станции — граница по месту из --rest-base / --rest-off;
    нет и их — None.

    Нет артефакта или показания — None с причиной словами (note —
    причина вызывающей стороны, например HTTP-отказ решателя).
    Возврат: {"rest_boundary_min": int | None — T показанием,
              "rest_boundary_source": str,
              "rest_boundary_basis": "knob" | "rest_floor" | None,
              "rest_boundary_by_place": {"home": R, "outstation": R} | None,
              "rest_boundary_assumption": str | None}."""
    from vmp import sit_boundary as sb

    def _out(value, source: str, basis=None, by_place=None,
             assumption=None) -> dict:
        return {"rest_boundary_min": value, "rest_boundary_source": source,
                "rest_boundary_basis": basis,
                "rest_boundary_by_place": by_place,
                "rest_boundary_assumption": assumption}

    def _got(got: dict, source: str) -> dict:
        return _out(got["value"], source, got["basis"],
                    got["by_place"] if got["value"] is not None else None,
                    got["assumption"])

    def _int(v):
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            return int(v.strip())
        return None

    if tl_scope == "roster":
        if not isinstance(applied, dict):
            return _out(None, "показаний решателя (applied-rules) о прогоне "
                              "нет" + (f": {note}" if note else ""))
        got_id = str(applied.get("runId") or "")
        if run_id and got_id and got_id != str(run_id):
            return _out(None, f"показания несут runId {got_id!r}, ожидался "
                              f"{run_id!r} — показания чужого прогона")
        last_run = applied.get("lastRun") or {}
        readings = last_run.get("profileReadings")
        if not isinstance(readings, dict) or DAY_BREAK_READING not in readings:
            return _out(None, "в показаниях прогона нет "
                              f"lastRun.profileReadings.{DAY_BREAK_READING}"
                              + (f" (runId {got_id})" if got_id else ""))
        if _int(readings.get(DAY_BREAK_READING)) is None:
            return _out(None, f"показание {DAY_BREAK_READING} не число: "
                              f"{readings.get(DAY_BREAK_READING)!r}")
        who = f"показания решателя прогона {got_id or run_id or '?'}"
        got = sb.derive(readings, rules_of=sb.rules_by_threshold(
            (last_run.get("thresholds") or {}).get("applied")
            if isinstance(last_run.get("thresholds"), dict) else None))
        return _got(got, f"{who}: {got['source']}")

    if tl_scope == "pairing":
        if not isinstance(builder_input, dict):
            return _out(None, "ведомости входа построителя "
                              "(builder-input.json нажатия «Пейринг») нет"
                              + (f": {note}" if note else ""))
        args = builder_input.get("args")
        if not isinstance(args, list):
            return _out(None, "в builder-input.json нет списка args")
        value = None
        for i, a in enumerate(args):
            if str(a) == "--sit-max" and i + 1 < len(args):
                value = _int(args[i + 1])
        prov = (builder_input.get("provenance") or {}).get("--sit-max")
        origin = ""
        if isinstance(prov, dict) and prov.get("from"):
            origin = f"; происхождение: {prov['from']}"
        if "--sit-max" not in [str(a) for a in args]:
            got = sb.derive_from_builder_args(args)
            return _got(got, "builder-input.json нажатия «Пейринг»: "
                             + got["source"] + origin)
        if value is None:
            return _out(None, "builder-input.json: значение --sit-max не "
                              "число")
        if value <= 0:
            return _out(None, f"builder-input.json: --sit-max={value} — "
                              f"{sb.UNDEFINED}" + origin)
        # явный --sit-max — правило максимальной стыковки (или перекрытие
        # оператора): критерий границы смены = его порог; полы отдыха
        # построителя — для показания достаточности по месту
        floors = sb.derive_from_builder_args(args)
        return _out(value, f"builder-input.json нажатия «Пейринг»: "
                           f"--sit-max={value} — максимальная стыковка, "
                           "критерий границы смены" + origin,
                    "knob", floors.get("by_place")
                    if floors.get("value") is not None else None, None)

    return _out(None, f"неизвестная область линии времени {tl_scope!r}")


# --------------------------------------------------------- ядро ФПР

def _compile_key(clauses: list[dict], scope: str, requested,
                 model, effort: str, channel: str | None = None,
                 backend: str | None = None,
                 fingerprint: str | None = None) -> str:
    """Ключ кэша компиляции: клаузы (sid + якорь + текст) + допуск +
    разрешённый контур + запрос + КАНАЛ читателя + эффективная модель
    + усилие + ОТПЕЧАТОК КОМПИЛЯТОРА (версия, промпт, словарь видов).
    Стык S06 плана единой версии (06.09.2026, §3.9 п.2): старая
    компиляция через API/неизвестный канал не удостоверяет новый
    CLI-запрос; правка промпта/словаря обнуляет кэш."""
    import hashlib
    h = hashlib.sha256()
    for c in clauses:
        h.update(json.dumps(
            [c.get("sid"), c.get("anchor"), c.get("text")],
            ensure_ascii=False, sort_keys=True).encode("utf-8"))
        h.update(b"\n")
    h.update(json.dumps([scope, backend, requested, channel, model,
                         effort, fingerprint],
                        ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def _cached_compile(clauses: list[dict], *, scope: str, requested,
                    model, timeout_s: float, effort: str,
                    channel: str | None = None) -> dict:
    """Компиляция проверок с кэшем data_root()/source-check/compiled/
    <ключ>.json (ключ — отпечаток клауз, допуска, разрешённого
    контура, канала, модели, усилия и версии компилятора; см.
    _compile_key). Кэш — только удачные компиляции; файл кэша хранит
    свой канал/версию и сверяется при чтении: документ без
    compiler_version/channel, с чужим ключом или кривой — пересчёт.
    Канал из кэша — наблюдённый канал ТОЙ компиляции, поэтому
    показание reader_channel в снимке честно и на втором блоке."""
    from webapp import llm_backend, source_check_compile as scc
    import _data_paths
    # Допуск контура судится ДО кэша: запрещённый запрос (local-only +
    # cloud) не находит чужую компиляцию — тот же отказ, что у
    # компилятора, только раньше.
    backend = llm_backend.resolve_backend(scope, requested)
    fingerprint = scc.compiler_fingerprint()
    key = _compile_key(clauses, scope, requested, model, effort,
                       channel=channel, backend=backend,
                       fingerprint=fingerprint)
    cache_dir = Path(_data_paths.data_root()) / "source-check" / "compiled"
    cache = cache_dir / f"{key}.json"
    if cache.is_file():
        try:
            doc = json.loads(cache.read_text(encoding="utf-8"))
            if (isinstance(doc, dict) and doc.get("specs")
                    and doc.get("key") == key
                    and doc.get("compiler_version") == scc.COMPILER_VERSION
                    and doc.get("fingerprint") == fingerprint
                    and "channel" in doc
                    and doc.get("channel") == channel):
                doc["cached"] = True
                # Расход: взятая из кэша компиляция модель НЕ звала —
                # токены той, прежней компиляции этому прогону не
                # приписываются (иначе один и тот же расход считался бы
                # дважды). Этому прогону приписывается ноль, и он
                # СОСЧИТАН: вызовов не было — значит токенов не было.
                # Причина — словами (`reader_resources` ставит и
                # `calls: 0`: число вызовов знает он, не труба канала).
                doc["usage"] = {
                    "provider": None, "model": doc.get("model"),
                    "channel": doc.get("channel"), "counted": True,
                    "input_tokens": 0, "output_tokens": 0,
                    "note": CACHED_COMPILE_NOTE}
                return doc
        except (OSError, ValueError, TypeError):
            pass
    got = scc.compile_checks(clauses, scope=scope, requested=requested,
                             model=model, timeout_s=timeout_s,
                             effort=effort, channel=channel)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(
            {"specs": got["specs"], "problems": got.get("problems") or [],
             "backend": got.get("backend"), "model": got.get("model"),
             "channel": got.get("channel"),
             "scope": scope, "requested": requested, "effort": effort,
             "compiler_version": scc.COMPILER_VERSION,
             "fingerprint": fingerprint,
             "key": key, "clauses": len(clauses),
             "at": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    got.setdefault("problems", [])
    got.setdefault("channel", channel)
    got.setdefault("usage", None)
    got["cached"] = False
    return got


def reader_resources(got: dict) -> list[dict]:
    """Расход читателя ИДАК за прогон сверки для `resources.models[]`
    снимка (решение владельца № 1 от 10.09.2026): один вызов на
    компиляцию проверок — одна строка по роли «читатель ИДАК». Клауз
    два блока (пейринг и ростер), но читатель зовётся ОДИН раз: второй
    блок судится теми же проверками из кэша, и лишнего расхода нет.

    Расход берётся ОТТУДА ЖЕ, откуда у сертификатора: разобранный
    `usage` ответа канала (`webapp/llm_backend`, разборщики провайдеров
    и свод `merge_usage`) — второго носителя расхода у читателя нет.
    Три исхода, и путать их нельзя:

      * канал назвал токены — `calls: 1`, `input_tokens`,
        `output_tokens`, `counted: true`;
      * проверки взяты из кэша (модель не звалась) — `calls: 0`,
        токены `0`, `counted: true` и причина словами: ноль вызовов —
        величина измеренная;
      * канал расхода не отдаёт (CLI подписки) либо не назвал ни одного
        счётчика — `calls: 1`, токены отсутствуют, `counted: false` и
        причина словами: неизвестное за ноль не выдаётся."""
    from webapp import llm_backend as _lb
    got = got if isinstance(got, dict) else {}
    usage = got.get("usage") if isinstance(got.get("usage"), dict) \
        else _lb.uncounted_usage(None, got.get("model"),
                                 got.get("channel"),
                                 no_usage_note(got.get("channel")))
    if got.get("cached"):
        # Вызова не было: число вызовов знает раннер, а не труба канала
        # (`merge_usage` считает вызовы строками расхода, и строка тут
        # описывает НЕсостоявшийся вызов).
        merged = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                  "counted": True, "note": CACHED_COMPILE_NOTE}
    else:
        merged = _lb.merge_usage([usage])
    return [{"role": role_words(READER_ROLE_CODE),
             "role_code": READER_ROLE_CODE,
             "provider": usage.get("provider"),
             "model": usage.get("model") or got.get("model"),
             "channel": usage.get("channel") or got.get("channel"),
             **merged}]


def failed_reader_resources(model=None, channel=None) -> dict:
    """Расход читателя ИДАК у ОТКАЗАВШЕЙ сверки — для снимка отказа.

    Сверка падает и ПОСЛЕ ответа читателя: ответ пришёл, а компилятор его
    не разобрал (нет массива JSON, ни одной проверки словаря). Токены на
    такой вызов потрачены, и снимок обязан их назвать — иначе запись о
    прогоне говорит «снимок расхода не назвал» о вызове, который был.

    Расход берётся ОТТУДА ЖЕ, откуда у удачи: разобранный `usage`
    последнего вызова этого потока (`webapp/llm_backend`; компилятор его
    только подсматривает, не снимая, — `peek_last_usage`). Дальше —
    общая с удачей `reader_resources`: канал назвал токены — `calls: 1`
    и `counted: true`; расхода нет — прежнее честное «не считается» с
    причиной словами, никаких выдуманных нулей.

    model / channel — читатель, которого звали (объявление вызывающей
    стороны): у строки без расхода имя модели и канал берутся оттуда."""
    from webapp import llm_backend as _lb
    usage = _lb.take_last_usage()
    return {"models": reader_resources(
        {"usage": usage if isinstance(usage, dict) else None,
         "model": model, "channel": channel, "cached": False})}


def _unpack_solution(entry: tuple) -> tuple:
    """(label, solution, tl_scope, side[, rest_boundary]) →
    пятёрка; без пятого элемента граница None (run_full подставит
    честное «показаний не передано»)."""
    if len(entry) == 5:
        label, solution, tl_scope, side, boundary = entry
        return (label, solution, tl_scope, side,
                boundary if isinstance(boundary, dict) else None)
    label, solution, tl_scope, side = entry
    return label, solution, tl_scope, side, None


def run_full(run_dir: Path, solutions: list[tuple],
             scope: str = "any", requested: str | None = None,
             model: str | None = None, effort: str = "medium",
             timeout_s: float = 420.0,
             snap_sub: str | None = None, *,
             channel: str | None = None,
             binding: dict | None = None) -> dict:
    """Полная ФПР: компиляция клауз ОДИН раз + прогон по каждому
    решению. solutions: [(label, solution_dict, timeline_scope,
    side_copy_path|None[, rest_boundary])], timeline_scope:
    "roster"|"pairing"; rest_boundary — показание прогона о границе
    «стыковка / отдых» из rest_boundary(...) (SOURCE-REST-R1): без
    него исполнитель rest_min отвечает not_checkable с причиной —
    литерал не подставляется; провенанс (rest_boundary_source) едет
    в протокол, checks.json и снимки.
    snap_sub — задача («pairing» | «rostering»): снимок состояния и
    протокол кладутся ещё и в data_root()/source-check/<snap_sub>/
    — у блоков схемы «Проверка пейринга по ИДАК» и «Проверка ростера
    по ИДАК» свои состояния (слово владельца 06.09.2026: сверка с
    документами — отдельный блок у каждого решения). Корневой снимок
    (последний прогон любой задачи) пишется как прежде.
    channel — канал читателя, объявленный вызывающим (cli | api |
    local | None — прежний путь); всегда передаётся компилятору явно
    и входит в ключ кэша; НАБЛЮДЁННЫЙ канал (reader_channel) — часть
    выходного комплекта: заголовок протокола, checks.json, снимки,
    возврат (показание, не заявление; S06 плана единой версии).
    binding — привязка результата к решению (package_sha, revision,
    solver_run_id, catalog_sha256, roster_path…): пишется в снимок
    как есть, чтобы зелёный результат относился к конкретному
    пакету и выдаче, а не только к имени прогона (§3.9 п.2).
    Возврат {counts, per_solution, protocol_path, reader,
    reader_channel, resources}; `resources.models[]` — расход читателя
    ИДАК в токенах (решение владельца № 1 от 10.09.2026): он же лежит в
    снимке `last.json` и оттуда попадает в запись попытки."""
    from vmp import source_check as sc
    clauses = load_clauses(run_dir)
    # Одно прочтение клауз на оба решения (блоки «Проверка пейринга по
    # ИДАК» и «Проверка ростера по ИДАК», 06.09.2026): скомпилированные
    # проверки кэшируются по отпечатку клауз + допуск + контур + канал
    # + модель + версия компилятора; второй блок берёт их без нового
    # вызова модели — те же проверки судят оба решения, и читатель не
    # зовётся дважды.
    got = _cached_compile(clauses, scope=scope, requested=requested,
                          model=model, timeout_s=timeout_s,
                          effort=effort, channel=channel)
    reader = f"{got['backend']}/{got['model']}"
    reader_channel = got.get("channel")
    solutions = [_unpack_solution(s) for s in solutions]
    sections: list[str] = []
    total: dict[str, int] = {}
    per_solution: dict[str, dict] = {}
    boundaries: dict[str, dict] = {}
    all_results = {}
    for label, solution, tl_scope, _side, boundary in solutions:
        specs = got["specs"]
        skipped: list[dict] = []
        if tl_scope == "pairing":
            specs, skipped = _filter_specs_for_pairing(specs)
        if boundary is None:
            boundary = rest_boundary(               # None + причина
                tl_scope, note="показание прогона вызывающей стороной "
                               "не передано")
        boundaries[label] = boundary
        results = sc.run_checks(specs, solution, rest_boundary=boundary)
        counts: dict[str, int] = {}
        for r in results:
            counts[r.verdict] = counts.get(r.verdict, 0) + 1
            total[r.verdict] = total.get(r.verdict, 0) + 1
        per_solution[label] = counts
        all_results[label] = results
        score = solution.get("score")
        head = (f"## Решение: {label}"
                + (f" (счёт {score})" if score else ""))
        bmin = boundary.get("rest_boundary_min")
        head += ("\n\nГраница «стыковка / отдых» прогона (T): "
                 + (f"{bmin} мин" if bmin is not None else "неизвестна")
                 + f" — {boundary.get('rest_boundary_source')}")
        # GPT-053 / CDPM-038: запасной критерий границы смены — явное
        # модельное упрощение, словами в протоколе рядом с границей.
        if boundary.get("rest_boundary_assumption"):
            head += ("\n\nДопущение: "
                     + str(boundary["rest_boundary_assumption"]))
        body = sc.render_protocol(results, header="")
        body = body.split("\n", 2)[2]     # срез общего заголовка
        sections.append(head + "\n" + body)
        for s in skipped:
            sections.append(
                f"### [СУДИТСЯ НА РОСТЕРИНГЕ] {s.get('id')} "
                f"({s.get('kind')}; клауза: {s.get('anchor')})\n"
                f"правило о людях: у каталога цепочек нет "
                f"назначений — проверено решением ростеринга\n")
    header = (
        "# Протокол ФПР — проверка решений против исходных "
        "документов авиакомпании\n\n"
        "ВЫХОДНОЙ ДОКУМЕНТ РЕШЕНИЯ (решение владельца 2026-08-31: "
        "комплект выгрузки = решение + сертификат + этот "
        "протокол).\n\n"
        f"Прогон: {run_dir.name}; читатель: {reader}"
        + (f"/{reader_channel}" if reader_channel else "")
        + f"; клауз: {len(clauses)}; проверок скомпилировано: "
        f"{len(got['specs'])}; решений проверено: {len(solutions)}"
        + ("; проблемы компиляции: %d" % len(got["problems"])
           if got["problems"] else ""))
    proto = header + "\n\n" + "\n".join(sections)

    # Артефакты по задаче — свой подкаталог: протокол и checks.json
    # пейринга и ростера не затирают друг друга (находка ревью 06.09).
    out_dir = run_dir / "source-check"
    if snap_sub:
        out_dir = out_dir / str(snap_sub)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "protocol.md").write_text(proto, encoding="utf-8")
    resources = {"models": reader_resources(got)}
    (out_dir / "checks.json").write_text(json.dumps(
        {"specs": got["specs"], "problems": got["problems"],
         "reader": reader, "reader_channel": reader_channel,
         "resources": resources,
         "rest_boundary": boundaries,
         "results": {label: [r.__dict__ for r in rs]
                     for label, rs in all_results.items()}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    for _label, _sol, _scope2, side, _b in solutions:
        if side:
            Path(side).write_text(proto, encoding="utf-8")

    import _data_paths
    snap_dir = Path(_data_paths.data_root()) / "source-check"
    snap = {
        "status": "done",
        "at": datetime.now().isoformat(timespec="seconds"),
        "run": run_dir.name,
        "task": snap_sub,
        "solution": " + ".join(label for label, *_ in solutions),
        "reader": reader,
        "reader_channel": reader_channel,
        # Расход читателя ИДАК в токенах (решение владельца № 1 от
        # 10.09.2026): снимок несёт его сам, и запись попытки берёт его
        # отсюда — сверка идёт ОТДЕЛЬНЫМ вызовом, вне попытки нажатия.
        "resources": resources,
        "counts": total,
        "per_solution": per_solution,
        "protocol": str(out_dir / "protocol.md"),
        "checks": str(out_dir / "checks.json"),
        "problems": len(got["problems"]),
        "compiled_cached": bool(got.get("cached")),
        # SOURCE-REST-R1: граница «стыковка / отдых», по которой судился
        # отдых, и её происхождение — по каждому решению; у снимка
        # одной задачи — ещё и верхним уровнем (панель блока читает).
        "rest_boundary": boundaries,
        "skipped_people": [
            {"id": s.get("id"), "kind": s.get("kind"),
             "anchor": s.get("anchor")}
            for _l, _s, tl, _side, _b in solutions if tl == "pairing"
            for s in _filter_specs_for_pairing(got["specs"])[1]],
    }
    if len(boundaries) == 1:
        only = next(iter(boundaries.values()))
        snap["rest_boundary_min"] = only.get("rest_boundary_min")
        snap["rest_boundary_source"] = only.get("rest_boundary_source")
        snap["rest_boundary_basis"] = only.get("rest_boundary_basis")
        snap["rest_boundary_by_place"] = only.get("rest_boundary_by_place")
        snap["rest_boundary_assumption"] = only.get("rest_boundary_assumption")
    if isinstance(binding, dict):
        # Привязка к пакету и выдаче (package_sha, revision,
        # solver_run_id, catalog_sha256…) — поверх снимка, служебные
        # ключи снимка не перекрывает.
        for k, v in binding.items():
            snap.setdefault(str(k), v)
    targets = [snap_dir]
    if snap_sub:
        targets.append(snap_dir / str(snap_sub))
    for d in targets:
        d.mkdir(parents=True, exist_ok=True)
        (d / "protocol-last.md").write_text(proto, encoding="utf-8")
        (d / "last.json").write_text(json.dumps(
            snap, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"counts": total, "per_solution": per_solution,
            "protocol_path": str(out_dir / "protocol.md"),
            "reader": reader, "reader_channel": reader_channel,
            "resources": resources}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="ФПР: проверка решений против ИДАК")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--solution")
    ap.add_argument("--pairing-catalog")
    ap.add_argument("--package")
    ap.add_argument("--model", default=None)
    ap.add_argument("--scope", default="any",
                    choices=("any", "local-only"))
    ap.add_argument("--requested", default=None,
                    choices=("cloud", "local", "auto"))
    ap.add_argument("--effort", default="medium",
                    choices=("low", "medium", "high"))
    ap.add_argument("--timeout-s", type=float, default=420.0)
    ap.add_argument("--channel", default=None,
                    choices=("cli", "api", "local"),
                    help="канал читателя: cli — CLI подписки точной "
                         "моделью (ключ API канал не меняет), api — "
                         "Messages API, local — локальная модель; без "
                         "флага — прежний путь по контуру")
    ap.add_argument("--applied", default=None,
                    help="показания решателя прогона ростеринга (JSON "
                         "ответа GET /schedules/run/<id>/applied-rules): "
                         "граница «стыковка / отдых» T — из "
                         "lastRun.profileReadings: day_break_threshold_min "
                         "(ручка max_sit_duration), без ручки — пол "
                         "минимального отдыха (min_rest_*); без файла "
                         "отдых по разрывам не судится (not_checkable с "
                         "причиной)")
    ap.add_argument("--builder-input", default=None,
                    help="ведомость входа построителя (builder-input.json "
                         "нажатия «Пейринг») — T из --sit-max; без флага "
                         "ищется рядом с --pairing-catalog")
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir)
    solutions: list[tuple] = []
    if a.pairing_catalog:
        if not a.package:
            raise SystemExit("--pairing-catalog требует --package "
                             "(рейсы каталога берутся из книги "
                             "clean-data пакета)")
        catalog = json.loads(Path(a.pairing_catalog)
                             .read_text(encoding="utf-8"))
        fmap = clean_flights_from_package(Path(a.package))
        if a.builder_input:
            bi = json.loads(Path(a.builder_input)
                            .read_text(encoding="utf-8"))
        else:
            bi = builder_input_beside(Path(a.pairing_catalog))
        solutions.append(("пейринг (каталог цепочек)",
                          catalog_to_solution(catalog, fmap),
                          "pairing", None,
                          rest_boundary("pairing", builder_input=bi)))
    if a.solution:
        sol = json.loads(Path(a.solution)
                         .read_text(encoding="utf-8"))
        side = Path(a.solution).with_suffix(".source-check.md")
        applied = None
        if a.applied:
            applied = json.loads(Path(a.applied).read_text(encoding="utf-8"))
        solutions.append((f"ростеринг ({Path(a.solution).name})",
                          sol, "roster", str(side),
                          rest_boundary("roster", applied=applied,
                                        note="--applied не передан")))
    if not solutions:
        raise SystemExit("не задано ни одного решения "
                         "(--solution / --pairing-catalog)")
    got = run_full(run_dir, solutions, scope=a.scope,
                   requested=a.requested, model=a.model,
                   effort=a.effort, timeout_s=a.timeout_s,
                   channel=a.channel)
    print("reader:", got["reader"]
          + (f"/{got['reader_channel']}" if got.get("reader_channel")
             else ""))
    print("verdicts:", json.dumps(got["counts"], ensure_ascii=True))
    print("protocol:", got["protocol_path"])
    return 2 if got["counts"].get("violated") else 0


if __name__ == "__main__":
    raise SystemExit(main())
