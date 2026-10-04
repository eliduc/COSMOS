# -*- coding: utf-8 -*-
"""Состояние конвейера COSMOS для главного экрана-схемы.

Поручение владельца 2026-08-28: блок-схема процессов «от входа (ВМП)
до выхода (решения по пейрингу и ростерингу)» — ОСНОВНОЙ экран;
активный блок пульсирует и несёт живые параметры прогресса. Фаза
эскалации сирот — отдельный блок с рекуррентной связью в блок пула.

Модуль СОБИРАЕТ состояние из уже существующих машинных следов — новых
хранилищ нет (та же дисциплина, что у витрины «Прогоны»):
  * хранилище задач webapp/jobs/<id>.json + <id>.log — активная/
    последняя задача каждого рода, вехи и этапы построителя из лога;
  * след прогресса решателя runs/progress/<run_id>.jsonl —
    целевая, граница, % близости к оптимуму (поручение владельца:
    показатель выводится всегда);
  * реестр исполнителей (CREW_VMP_REGISTRY либо путь по умолчанию) —
    блок проверки соответствия ВМП.

Определение активной подфазы пейринга — по ПОСЛЕДНЕЙ строке «этап:»/
«веха [...]» лога задачи: журнал не парсится вглубь, читается хвост.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import jobs, runs

# Подфаза пейринга по последней строке-метке лога построителя.
_PHASE_MARKS = (
    ("извлечение выбранных", "arbiter"),
    ("выбрано цепочек", "arbiter"),
    ("точное покрытие", "select"),
    ("покрытия с клапаном", "select"),
    ("выбор из", "select"),
    ("сборка базовой модели", "select"),
    ("минимизация ядра", "select"),
    ("эскалация", "escalate"),
    ("спасательный проход", "pool"),
    ("генерация колонок", "pool"),
    ("перебор цепочек", "pool"),
    ("перебор готов", "pool"),
)
_MARK_RE = re.compile(r"^(?:этап:|веха \[\d\d:\d\d:\d\d\])")


def _log_tail(job_id: str, max_bytes: int = 65536) -> list[str]:
    p = jobs.JOBS_DIR / f"{job_id}.log"
    if not p.exists():
        return []
    try:
        with open(p, "rb") as f:
            f.seek(max(0, p.stat().st_size - max_bytes))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return data.splitlines()


def _pairing_phase(lines: list[str]) -> tuple[str, dict]:
    """(подфаза, счётчики) по последней строке-метке лога."""
    phase = "pool"
    params: dict = {}
    for ln in lines:
        if not _MARK_RE.match(ln):
            continue
        for mark, ph in _PHASE_MARKS:
            if mark in ln:
                phase = ph
                break
        m = re.search(r"кандидатов (\d+)", ln)
        if m:
            params["candidates"] = int(m.group(1))
        m = re.search(r"смен (\d+)", ln)
        if m:
            params["duties"] = int(m.group(1))
        m = re.search(r"осиротевших было (\d+), осталось (\d+)", ln)
        if m:
            params["orphans"] = int(m.group(2))
        m = re.search(r"эскалация: ступень (\d+) — сирот (\d+), "
                      r"покрыто (\d+)", ln)
        if m:
            params["esc_step"] = int(m.group(1))
            params["esc_orphans"] = int(m.group(2))
        m = re.search(r"эскалация сирот — было (\d+), покрыто (\d+), "
                      r"доказано (\d+)", ln)
        if m:
            params["esc_covered"] = int(m.group(2))
            params["esc_proved"] = int(m.group(3))
        m = re.search(r"движок отбора — (\w+)", ln)
        if m:
            params["engine"] = m.group(1)
    return phase, params


def _progress_last(run_id: str) -> dict:
    """Последняя точка следа прогресса + % близости к оптимуму."""
    try:
        import _data_paths
        p = (Path(_data_paths.results_dir()) / "runs" / "progress"
             / f"{run_id}.jsonl")
    except Exception:                                     # noqa: BLE001
        return {}
    if not p.exists():
        return {}
    last: dict = {}
    try:
        for ln in p.read_text(encoding="utf-8",
                              errors="replace").splitlines():
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            last.update({k: v for k, v in d.items() if v is not None})
    except OSError:
        return {}
    out: dict = {}
    if "incumbent" in last:
        out["objective"] = last["incumbent"]
    if "bound" in last:
        out["bound"] = last["bound"]
    if out.get("objective") and out.get("bound") \
            and out["bound"] > 0:
        # % близости к оптимуму — поручение владельца 2026-08-28:
        # выводить всегда, где есть целевая и граница.
        out["gap_pct"] = round(
            (out["objective"] - out["bound"]) / out["bound"] * 100, 2)
    if "t" in last:
        out["elapsed_s"] = int(last["t"])
    return out


def _job_block(types):
    """Последняя задача рода: (нормализованная запись, id задачи,
    run_id следа прогресса) либо (None, None, None)."""
    best = None
    for rec in runs.list_runs(None):
        if rec.get("type") not in types:
            continue
        if best is None or str(rec.get("started_at") or "") > \
                str(best.get("started_at") or ""):
            best = rec
    if best is None:
        return None, None, None
    jid = best.get("id")
    try:
        rid = jobs.run_id_of(jid) or jid
    except Exception:                                     # noqa: BLE001
        rid = jid
    return best, jid, rid


def _source_check_state(task: str | None = None) -> dict:
    """Последний протокол сверки решения с документами а/к (шестая
    проверка): data_root()/source-check/last.json; с задачей
    («pairing» | «rostering») — снимок этой задачи в подкаталоге
    (блоки «Проверка пейринга по ИДАК» / «Проверка ростера по ИДАК»
    живут каждый своим протоколом, слово владельца 06.09.2026). Нет
    файла — блок idle; кривой файл — честная пометка, не молчание."""
    import _data_paths
    root = Path(_data_paths.data_root()) / "source-check"
    p = (root / str(task) / "last.json") if task else (root / "last.json")
    if not p.is_file():
        return {"status": "idle"}
    try:
        # utf-8-sig: снимок на площадке начинается с BOM (проверено на
        # crew-demo-data/source-check/last.json, 07.09.2026).
        if task in ("pairing", "rostering"):
            from .source_check_status import selected_snapshot
            doc = selected_snapshot(task)
        else:
            doc = json.loads(p.read_text(encoding="utf-8-sig"))
        # Привязка снимка к пакету и прогону COSMOS (07.09.2026): по ней
        # схема отсекает протокол чужого пакета раньше, чем по прогону CDPM.
        binding = {"package_sha": doc.get("package_sha"),
                   "cosmos_run": doc.get("cosmos_run")}
        if doc.get("status") == "running":
            return {"status": "working", "at": doc.get("at"),
                    "run": doc.get("run"), **binding}
        if doc.get("status") == "error":
            return {"status": "idle",
                    "error": doc.get("error") or "сбой сверки"}
        if doc.get("status") != "done":
            return {"status": "idle"}
        counts = doc.get("counts") or {}
        return {"status": "done",
                "checks": sum(int(v) for v in counts.values()),
                "violated": int(counts.get("violated") or 0),
                "not_checkable": int(counts.get("not_checkable")
                                     or 0),
                "reader": doc.get("reader") or "",
                # наблюдённый канал читателя (cli|api|local) — показание
                # из снимка, только когда оно есть (S-04.2, 06.09.2026)
                **({"reader_channel": doc.get("reader_channel")}
                   if doc.get("reader_channel") else {}),
                "run": doc.get("run"),
                "at": doc.get("at"), **binding}
    except (OSError, ValueError, TypeError) as e:
        return {"status": "idle", "error": f"протокол не читается: "
                                           f"{e}"}


def _page_registry_path(cur: dict, pkg: dict | None = None) -> Path:
    """Реестр исполнителей, по которому судят страница и карточка
    конвейера: явный файл CREW_VMP_REGISTRY (стенды) либо реестр,
    выведенный под модель ТЕКУЩЕГО пакета той же процедурой, что у
    кнопки (registry_derive) — в каталог нажатий пакета. Находка
    04.09 (H0-К на Prod): после снятия статического файла площадки
    карточка «Проверка соответствия» писала «ожидает данных», хотя
    реестр под пакет есть — две правды о реестре."""
    reg = os.environ.get("CREW_VMP_REGISTRY")
    if reg:
        return Path(reg)
    from vmp import registry_derive as _rd
    from webapp import cosmos_runs, package_runs as _pr
    p = Path(cur["path"])
    sha = cur.get("sha") or cur.get("sha256") or _pr._sha256(p)
    # Рабочий каталог пары (прогон, пакет): реестр страницы второго
    # держателя того же sha ложится в его runs/<run>/registry/, а не в
    # базовый каталог чужого прогона (07.09.2026).
    path = (cosmos_runs.workdir_of(cur, sha) / "registry"
            / "vmp-executors-registry.page.json")
    if not path.is_file():
        if pkg is None:
            from webapp import task_package as wtp
            pkg = wtp.load(p, run_gate=False)
        _rd.derive_and_write(
            pkg.get("model") or {},
            Path(__file__).resolve().parent.parent, path,
            scope="страница: матрица готовности пакета %s" % sha[:12])
    return path


def _registry_state() -> dict:
    """Карточка конвейера «Проверка соответствия»: реестр текущего
    пакета — тот же, что у страницы и кнопки (_page_registry_path)."""
    from . import task_channel
    cur = task_channel.current_package()
    try:
        if cur is None or not cur.get("path") \
                or not Path(cur["path"]).is_file():
            path = os.environ.get("CREW_VMP_REGISTRY")
            p = Path(path) if path else None
        else:
            p = _page_registry_path(cur)
        if p is None or not p.exists():
            return {"present": False}
        reg = json.loads(p.read_text(encoding="utf-8"))
        return {"present": True,
                "rules": len(reg.get("rules") or []),
                "created": reg.get("created"),
                "derived": bool(reg.get("derived"))}
    except Exception as e:                                # noqa: BLE001
        return {"present": False, "error": str(e)[:200]}


_ACTIVE = ("running", "выполняется", "считает", "запущена")

# Матрица готовности ВМП (директива владельца 2026-08-29): читается
# из пакета постановки (единый резолвер task_channel.current_package:
# CREW_TASK_PACKAGE — приоритет, без неё — пакет, принятый каналом)
# и верифицируется vmp.readiness_verify; результат кэшируется по
# mtime пакета — опрос конвейера идёт раз в 2 с, а верификация
# читает весь пакет.
_READY_CACHE: dict = {"key": None, "value": None}


def readiness_state() -> dict:
    from . import task_channel
    cur = task_channel.current_package()
    if cur is None or not cur.get("path") or not Path(cur["path"]).is_file():
        return {"present": False}
    p = Path(cur["path"])
    key = (str(p), p.stat().st_mtime_ns,
           os.environ.get("CREW_VMP_REGISTRY"))
    if _READY_CACHE["key"] == key:
        return _READY_CACHE["value"]
    try:
        from vmp import readiness_verify as rv
        from webapp import task_package as wtp
        pkg = wtp.load(p, run_gate=False)
        # Реестр исполнителей — тот же, что у кнопки при нажатии:
        # выводится под модель ЭТОГО пакета (press_registry), а не
        # статический файл площадки (находка H0-К 04.09: «запуск
        # запрещён: реестра нет» при проходящем нажатии). Кэш по mtime
        # пакета выше; сам файл реестра пересоздаётся при смене пакета
        # (каталог нажатий — по sha пакета).
        reg_path = _page_registry_path(cur, pkg)
        if reg_path.is_file() and not os.environ.get("CREW_VMP_REGISTRY"):
            # Снимок кода мог смениться (раскат): вывести заново под
            # текущие отпечатки исполнителей, чтобы сторож дрейфа не
            # судил по старому снимку.
            from vmp import registry_derive as _rd
            _rd.derive_and_write(
                pkg.get("model") or {},
                Path(__file__).resolve().parent.parent, reg_path,
                scope="страница: матрица готовности пакета %s"
                      % (cur.get("sha") or "")[:12])
        ver = rv.verify(
            pkg.get("manifest") or {}, pkg.get("model") or {},
            pkg.get("clean_data"),
            Path(__file__).resolve().parent.parent,
            reg_path, form=pkg.get("form"))
        cols = {}
        man_cols = ((pkg.get("manifest") or {}).get(
            "readiness_matrix") or {}).get("columns") or {}
        for task, cell in (ver.get("columns") or {}).items():
            g = (man_cols.get(task) or {}).get("grounds") or {}
            q = g.get("questions") or {}
            cols[task] = {
                "state": cell.get("declared_state"),
                "match": cell.get("cell_match"),
                "launch_allowed": cell.get("launch_allowed"),
                "why_blocked": cell.get("why_blocked") or [],
                "zone_total": q.get("zone_total"),
                "open_blocking": q.get("open_blocking"),
                "strict_interim": q.get("strict_interim_answers"),
                "criterion": (g.get("criterion") or {}).get(
                    "source_rule"),
                "criterion_defined": (g.get("criterion") or {}).get(
                    "defined"),
                # Примечания клетки от CDPM (ИДАК H0-К: «критерия
                # оптимизации нет … возможен только feasibility-soft»)
                # — страница показывает их словами рядом с основаниями.
                "notes": [str(n) for n in
                          ((man_cols.get(task) or {}).get("notes") or [])],
                "registry_rules": (cell.get("registry") or {}).get(
                    "rules_in_zone"),
                "registry_gaps": (cell.get("registry") or {}).get(
                    "gaps"),
                # Пробелы реестра ПОИМЁННО (шаг 6 каталога): число
                # `registry_gaps` остаётся на месте — по нему судится
                # чистота реестра, — а перечень идёт рядом, чтобы
                # экран мог назвать правило, форму пробела и заявку
                # каталога с её состоянием. Перечня нет (реестр
                # старой формы) — пустой список, не отсутствие ключа.
                "registry_gap_rows": (cell.get("registry") or {}).get(
                    "gap_rows") or [],
                "data_missing": cell.get("data_actual_missing")
                or [],
            }
        # Постановка как ЗАДАЧА (половина COSMOS по разделению функций,
        # решение владельца 2026-09-03): вердикт vmp.formulation_check
        # рядом с матрицей — показание, не замок на запуск (перевод в
        # замок — решение владельца; пакеты до формы — «не проверено»).
        from vmp import formulation_check as fc
        out = {"present": True, "verdict": ver.get("verdict"),
               "columns": cols, "formulation": fc.summary(pkg)}
    except Exception as e:                            # noqa: BLE001
        out = {"present": False, "error": str(e)}
    _READY_CACHE["key"] = key
    _READY_CACHE["value"] = out
    return out


def build_state() -> dict:
    """Состояние всех блоков конвейера одной выдачей."""
    pairing, pair_jid, pair_rid = _job_block(("stage1", "pairing"))
    roster, rost_jid, rost_rid = _job_block(("stage2", "rostering"))
    blocks: list[dict] = []

    def add(bid, status, params=None, sub=None):
        blocks.append({"id": bid, "status": status,
                       "params": params or {}, "sub": sub or bid})

    # Этап 1: ВМП (постановка) — вход конвейера.
    add("vmp", "done" if pairing else "idle",
        {"instance": (pairing or {}).get("instance")})
    # Этап 2: единая проверка соответствия.
    reg = _registry_state()
    add("conform", "done" if reg.get("present") else "idle",
        {k: v for k, v in reg.items() if k != "present"})

    # Этап 3: пейринг — составной (пул -> эскалация -> отбор ->
    # арбитраж), эскалация с рекуррентной связью в пул.
    p_state = str((pairing or {}).get("status") or "")
    p_active = p_state.lower() in _ACTIVE
    phase, params = ("pool", {})
    if pair_jid:
        phase, params = _pairing_phase(_log_tail(pair_jid))
        params.update(_progress_last(pair_rid))
        for k in ("objective", "lp_bound", "gap_pct"):
            if params.get(k) is None \
                    and (pairing or {}).get(k) is not None:
                params[k] = pairing[k]
    for sub in ("pool", "escalate", "select", "arbiter"):
        if p_active:
            st = "working" if sub == phase else (
                "done" if _sub_before(sub, phase) else "waiting")
        else:
            st = "done" if pairing else "idle"
        add("pairing." + sub, st, params if sub == phase else {})
    # Этап 4: приёмка/авторизация.
    add("accept",
        "done" if pairing and not p_active else "idle",
        {"status": p_state} if pairing else {})
    # Этап 5: ростеринг.
    r_state = str((roster or {}).get("status") or "")
    r_active = r_state.lower() in _ACTIVE
    r_params = _progress_last(rost_rid) if rost_rid else {}
    r_params.update({"status": r_state} if roster else {})
    add("roster", "working" if r_active
        else ("done" if roster else "idle"), r_params)
    # Этап 5б: сверка решения с документами авиакомпании (шестая
    # проверка, поручение владельца 2026-08-31) — финальный рубеж
    # перед выходом. Статус — от последнего протокола (артефакт
    # data_root()/source-check/last.json); нарушения видны чипом
    # violated, сам протокол — файлом рядом.
    sc_p = _source_check_state("pairing")
    add("pair.src", sc_p.pop("status"), sc_p)
    sc = _source_check_state()
    add("srccheck", sc.pop("status"), sc)
    sc_r = _source_check_state("rostering")
    add("roster.src", sc_r.pop("status"), sc_r)
    # Этап 6: выход (решения) + опция «Сбой».
    add("out", "done" if roster and not r_active else "idle")
    add("disrupt", "idle")

    # Расчёт по пакету постановки (слово владельца 05.09.2026: каждый
    # блок схемы обязан работать в свой момент): когда на площадке есть
    # пакет и нажатия по нему, схема живёт шагами кнопки (Ш-0…Ш-4г), а
    # не легаси-заданиями по наборам.
    press = _press_blocks()
    if press is not None:
        blocks = press

    active = next((b["id"] for b in blocks
                   if b["status"] == "working"), None)
    # Текущий прогон COSMOS и подсказка (07.09.2026): чип шапки, переключатель
    # прогона и полоса-подсказка над схемой живут этими двумя ключами.
    try:
        from . import cosmos_runs
        run = cosmos_runs.current_run_summary()
        hint = cosmos_runs.hint_code()
    except Exception as e:                                # noqa: BLE001
        run, hint = None, "runs-unreadable: %s" % str(e)[:120]
    return {"blocks": blocks, "active": active,
            "readiness": readiness_state(),
            "run": run, "runs_hint_code": hint}


# Шаги кнопки (vmp/run/button.py) → блоки схемы. Порядок важен: шаг
# «Ш-1…» — проверка соответствия (гейт пакета, покрытие исполнителями,
# матрица готовности, измеритель); «Ш-2» — построитель/решатель; «Ш-2а»
# — решён; «Ш-3» — каталог/показания; «Ш-4…» — сертификация.
_STEP_RE = re.compile(r"Ш-(\d+)([а-яa-z0-9]*)")


def _press_step(state: str) -> tuple[int, str]:
    """(номер шага, суффикс) из текста состояния кнопки; (-1, '') — нет."""
    m = _STEP_RE.search(state or "")
    if not m:
        return -1, ""
    return int(m.group(1)), m.group(2)


#: Десять блоков схемы пакетного пути в порядке конвейера.
PRESS_BLOCK_IDS = ("vmp", "conform", "pairing.pool", "pairing.select",
                   "accept", "pair.src", "roster", "srccheck", "roster.src",
                   "out")


def _idle_run_blocks(run: dict) -> list[dict]:
    """Прогон есть, пакета нет: десять блоков в покое, «Постановка (ВМП)»
    несёт прогон COSMOS (демо: «новый прогон → передача из CDPM в него»)."""
    return [{"id": bid, "status": "idle",
             "params": {"run": run.get("run")} if bid == "vmp" else {},
             "sub": bid} for bid in PRESS_BLOCK_IDS]


def grade_delivery(d: dict | None) -> str | None:
    """Оценка блока проверки по ВМП цветом (слово владельца на демо
    06.09.2026: «зелёная, если нарушений нет; жёлтая, если некритичные;
    красная, если критичные»): из выдачи задачи — certified / гейт
    сертификата / недопустимость → «bad»; находки, непокрытые правила,
    незавершённый поиск → «warn»; иначе «ok»; нет выдачи — None."""
    if not isinstance(d, dict):
        return None
    res = d.get("result") if isinstance(d.get("result"), dict) else {}
    verdict_words = str(d.get("verdict") or "")
    if d.get("certified") is False or d.get("certificate_gate") \
            or res.get("feasibility") not in (None, "confirmed") \
            or "недопустим" in verdict_words:
        return "bad"
    findings = d.get("findings")
    n_find = len(findings) if isinstance(findings, list) else (
        int(findings) if isinstance(findings, (int, float)) else 0)
    if n_find > 0 or d.get("uncovered") or res.get("search_incomplete"):
        return "warn"
    return "ok"


def _press_blocks() -> list[dict] | None:
    """Блоки схемы по нажатиям на текущем пакете; None — ни прогона, ни
    пакета (схема живёт легаси-заданиями); прогон без пакета — десять
    блоков в покое с прогоном на «Постановке (ВМП)»."""
    try:
        from . import package_runs as pr
        st = pr.status()
    except Exception:                                      # noqa: BLE001
        return None
    if not st or not st.get("present"):
        try:
            from . import cosmos_runs
            run = cosmos_runs.current_run_summary()
        except Exception:                                  # noqa: BLE001
            run = None
        if run and not run.get("implicit"):
            return _idle_run_blocks(run)
        return None
    task = st.get("task")
    files = st.get("files") or {}
    have_pairing = bool(files.get("pairing"))
    # Ростер есть и от штатной кнопки, и «на утверждённом пейринге».
    have_roster = bool(files.get("rostering")) \
        or bool(files.get("rostering_on_pairing"))
    # Пакет принят, нажатий ещё нет — схема всё равно живёт пакетом
    # (приём и соответствие выполнены, остальное ждёт первого шага), а
    # не легаси-заданиями по наборам: иначе после доставки нового пакета
    # на схеме светились «выполнено» чужие прогоны (06.09.2026, демо).
    running = st.get("done") is False
    state = str(st.get("state") or "")
    step, suffix = _press_step(state)
    # Шаг нажатия («Ш-2 — принят решателем») живёт в поле progress, а
    # state — «кнопка: идёт»; без этого чтения схема во время ростеринга
    # помечала «работает» блок «Постановка (ВМП)», а «Ростеринг» оставляла
    # «в очереди» (замечание владельца на демо 06.09.2026: «блоки в работе
    # не мигают»).
    if step < 0:
        step, suffix = _press_step(str(st.get("progress") or ""))
    verdict = str(st.get("verdict") or "")
    ok_words = ("допустим", "построен", "сертифицирован")
    pairing_ok = have_pairing
    roster_ok = have_roster
    pr_run = st.get("pairing_run") or {}
    chains = pr_run.get("chains")
    engine = st.get("engine")

    def s(bid, status, params=None):
        return {"id": bid, "status": status, "params": params or {},
                "sub": bid}

    # Итоговые (не идущие) состояния по наличию выдачи.
    vmp = "done"
    conform = "done"
    pool = "done" if pairing_ok else "idle"
    select = "done" if pairing_ok else "idle"
    accept = "done" if pairing_ok else "idle"
    roster = "done" if roster_ok else "idle"
    srccheck = "done" if roster_ok else "idle"
    out = "done" if roster_ok and any(w in verdict for w in ok_words) \
        else ("idle" if not roster_ok else "waiting")
    if task == "rostering" and not running and roster_ok \
            and "допустим" not in verdict:
        out = "waiting"
    step_param = {"step": state.replace("кнопка: ", "")} if state else {}
    if running and task == "pairing":
        if step == 0 or (step < 0 and "идёт" in state and "Ш-" not in state):
            vmp, conform = "working", "waiting"
            pool = select = accept = "waiting"
        elif step == 1 or (step == 2 and suffix in ("м", "и", "с")):
            conform = "working"
            pool = select = accept = "waiting"
        elif step == 2:
            pool, select, accept = "done", "working", "waiting"
        elif step >= 3:
            pool, select, accept = "done", "done", "working"
        else:
            select = "working"
    elif running and task == "rostering":
        if step == 0 or (step < 0 and "Ш-" not in state):
            vmp, conform = "working", "waiting"
            roster = srccheck = "waiting"
            out = "waiting"
        elif step == 1 or (step == 2 and suffix in ("м", "и", "с")):
            conform = "working"
            roster = srccheck = out = "waiting"
        elif step == 2 and suffix == "":
            roster, srccheck, out = "working", "waiting", "waiting"
        elif step == 2 or step >= 3:
            roster, srccheck, out = "done", "working", "waiting"
        else:
            roster = "working"
    # Сверка с документами а/к (шестая проверка, читатель ФПР) — у
    # каждого решения свой блок и свой протокол (слово владельца
    # 06.09.2026): «Проверка пейринга по ИДАК» после сертификата
    # пейринга, «Проверка ростера по ИДАК» после сертификата ростера.
    # Состояние — только от протокола задачи: идёт/готов; протокола
    # нет — блок ждёт, пока есть решение, иначе не активен.
    source_run = st.get("source_run")
    package_sha = str(st.get("package_sha") or "")

    def _src(task_name: str, have: bool) -> tuple[str, dict]:
        sc = _source_check_state(task_name)
        sc_status = sc.pop("status", "idle")
        # Протокол чужого пакета к этому пакету не относится — сначала по
        # отпечатку пакета (когда оба известны), затем прежним правилом
        # по прогону CDPM; блок ждёт, пока есть решение.
        if sc_status in ("working", "done") and package_sha \
                and sc.get("package_sha") and sc.get("package_sha") != package_sha:
            sc_status = "idle"
        if sc_status in ("working", "done") and source_run \
                and sc.get("run") and sc.get("run") != source_run:
            sc_status = "idle"
        if sc_status in ("working", "done"):
            # На плашке блока — только счётчики (проверок / нарушений /
            # не машинных): читатель, канал, прогон и время — в панели
            # блока; семь плашек раздували блок (замечание владельца на
            # демо 06.09.2026).
            return sc_status, {k: sc[k] for k in
                               ("checks", "violated", "not_checkable")
                               if k in sc}
        # Historical counts alone do not bind the panel/protocol to this
        # solution. All three readers use selected_snapshot's identity gate.
        return ("waiting" if have else "idle"), (
            {"error": sc["error"]} if sc.get("error") else {})
    pair_src, pair_src_params = _src("pairing", pairing_ok)
    roster_src, roster_src_params = _src("rostering", roster_ok)
    # Оценка блоков проверок цветом: по ВМП — grade_delivery (выдача
    # задачи), по ИДАК — нарушения → «bad», иначе «ok» (мягкие и не
    # машинные — не нарушения).
    _grade_delivery = grade_delivery

    def _read_delivery(sub: Path | None) -> dict | None:
        if not sub:
            return None
        try:
            return json.loads((sub / "delivery.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    accept_grade = roster_grade = None
    try:
        # Рабочий каталог пары (прогон, пакет): относительный workdir из
        # состояния нажатия (07.09.2026), без него — каталог по sha.
        if st.get("workdir"):
            import _data_paths
            wd = Path(_data_paths.data_root()) / str(st["workdir"])
        else:
            wd = pr.workdir_for(package_sha)
        if pairing_ok:
            accept_grade = _grade_delivery(_read_delivery(wd / "button-pairing"))
        if roster_ok:
            roster_grade = _grade_delivery(
                _read_delivery(pr.roster_delivery_dir(wd)))
    except Exception:                                      # noqa: BLE001
        accept_grade = roster_grade = None

    def _grade_src(status: str, params: dict) -> str | None:
        if status != "done":
            return None
        try:
            return "bad" if int(params.get("violated") or 0) > 0 else "ok"
        except (TypeError, ValueError):
            return None
    if running and task == "pairing":
        pair_src = "waiting"
    if running and task == "rostering":
        roster_src = "waiting"
    # оценка — только у выполненного блока (после переводов в «ждёт»)
    if _grade_src(pair_src, pair_src_params):
        pair_src_params = dict(pair_src_params,
                               grade=_grade_src(pair_src, pair_src_params))
    if _grade_src(roster_src, roster_src_params):
        roster_src_params = dict(roster_src_params,
                                 grade=_grade_src(roster_src, roster_src_params))
    # На плашке блока «Постановка (ВМП)» — сначала прогон COSMOS
    # (07.09.2026: пакет лежит в текущем прогоне), затем номер прогона
    # CDPM (слово владельца 06.09.2026, демо: «сразу видно, что пришло»),
    # затем короткая сумма пакета.
    vmp_params = {}
    run_of_press = st.get("run")
    if isinstance(run_of_press, dict) and run_of_press.get("run"):
        vmp_params["run"] = str(run_of_press["run"])
    elif isinstance(run_of_press, str) and run_of_press:
        vmp_params["run"] = run_of_press
    else:
        try:
            from . import cosmos_runs
            cur_run = cosmos_runs.current_run_summary()
        except Exception:                                  # noqa: BLE001
            cur_run = None
        if cur_run and cur_run.get("run") and (
                not cur_run.get("package_sha12")
                or cur_run["package_sha12"] == package_sha[:12]):
            vmp_params["run"] = str(cur_run["run"])
    if source_run:
        vmp_params["source_run"] = str(source_run)
    vmp_params["instance"] = package_sha[:12]
    blocks = [
        s("vmp", vmp, vmp_params),
        s("conform", conform, {"engine": engine} if engine else {}),
        s("pairing.pool", pool, {"chains": chains} if chains else {}),
        s("pairing.select", select,
          step_param if (running and task == "pairing") else
          ({"engine": engine} if engine and pairing_ok else {})),
        s("accept", accept,
          dict(({"verdict": verdict} if pairing_ok and task == "pairing"
                and not running else {}),
               **({"grade": accept_grade} if accept_grade and accept == "done"
                  else {}))),
        s("pair.src", pair_src, pair_src_params),
        s("roster", roster,
          step_param if (running and task == "rostering") else {}),
        s("srccheck", srccheck,
          dict(({"verdict": verdict} if roster_ok and task == "rostering"
                and not running else {}),
               **({"grade": roster_grade} if roster_grade and srccheck == "done"
                  else {}))),
        s("roster.src", roster_src, roster_src_params),
        s("out", out,
          {"verdict": verdict} if roster_ok and task == "rostering"
          and not running else {}),
    ]
    return blocks


_SUB_ORDER = ("pool", "escalate", "select", "arbiter")


def _sub_before(a: str, b: str) -> bool:
    return _SUB_ORDER.index(a) < _SUB_ORDER.index(b)
