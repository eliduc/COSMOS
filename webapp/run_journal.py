# -*- coding: utf-8 -*-
"""Журнал COSMOS в записи о прогоне и сшивка с хозяином записи.

Основание — проект `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, разделы
3.2 (единица журнала), 4 (хозяин и хранитель), 10 (половина COSMOS шага
2) и 11.2 (тело ручки `record/append` панели CDPM). Словарь полей —
общий носитель `vmp/run_record.py`: имена этапов, исходы и счётчики
здесь не выдумываются, а читаются оттуда.

Что делает модуль.

  * После КАЖДОЙ законченной попытки нажатия (пейринг, ростер) и после
    КАЖДОЙ сверки с ИДАК складывает запись этапа по словарю
    (`attempt_to_journal` и этап «сверка с ИДАК»): время, исход, код
    причины, слова, актор, артефакты с отпечатками, счётчики, расход
    моделей в токенах.
  * Кладёт записи строками в `run-record.entries.jsonl` папки нажатий
    (только добавление; повтор ключа заменяет строку) и — у ЧАСТНОГО
    прогона, где хозяин записи сам COSMOS, — дописывает журнал в
    `run-record.json` и закрывает запись, когда прошли обе задачи.
  * Отправляет записи хозяину `append`-ом по адресу `record_api`. Хозяин
    недоступен либо отказал — строка остаётся в очереди
    `record-outbox.jsonl`, ответ ложится в `record-append-receipts.jsonl`,
    повтор идёт при следующем нажатии и при открытии карточки. Исход
    отправки называется ТРЕМЯ словами (`STITCH_*`): «отправлено» —
    хозяин принял; «ждёт» — вердикта не было (сеть, таймаут занятой
    панели, поломка службы), и повтор по ключу идемпотентности запись у
    хозяина заменит, а не удвоит; «отказано» — хозяин назвал причину.

Правило, которому подчинён весь модуль: ЖУРНАЛ НЕ ЛОМАЕТ НАЖАТИЕ.
Летопись важна, но расчёт важнее: любая беда журнала и сшивки — слова
предупреждения в записи попытки, а не отказ кнопки и не исключение
наружу.

Идемпотентность — ключ `(system, stage, substage)` (договор с CDPM,
раздел 11.2): `substage` несёт номер попытки, повтор ключа ЗАМЕНЯЕТ
прежнюю запись, а не удваивает её. Так повтор отправки после сбоя сети
не превращает один этап в два.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib import error as _urlerror
from urllib import request as _urlrequest

from vmp import run_record as rr
from vmp.run.llm_exchanges import ROLE_CODE_SOURCE_READER, role_words

from .run_gate import read_json, write_json_atomic

#: Записи этапов COSMOS по этому пакету (только добавление).
ENTRIES_FILE = "run-record.entries.jsonl"
#: Очередь неотправленных записей.
OUTBOX_FILE = "record-outbox.jsonl"
#: Ответы хозяина записи на отправки.
RECEIPTS_FILE = "record-append-receipts.jsonl"
#: Последняя единая запись, полученная от хозяина.
REMOTE_FILE = "run-record.remote.json"

#: Схема тела ручки `record/append` (договор с CDPM 10.09.2026).
APPEND_SCHEMA = "cosmos-run-record-append/v1"
#: Имя системы в ключе идемпотентности.
SYSTEM = "cosmos"

#: Исходы отправки записи хозяину — ТРИ разных исхода, и путать их
#: нельзя (разбор 11.09.2026). «Отправлено» — хозяин принял запись;
#: «ждёт» — вердикта не было вовсе (сеть, таймаут занятой панели,
#: поломка службы): запись у хозяина могла УЖЕ лечь целиком, и повтор
#: по ключу идемпотентности (система, этап, подэтап) её ЗАМЕНИТ, а не
#: удвоит; «отказано» — хозяин сказал НЕТ и назвал причину. Прежде
#: любая неудача считалась отказом, и карточка обвиняла панель там, где
#: запись уже была полна.
STITCH_SENT = "sent"
STITCH_WAITING = "waiting"
STITCH_REFUSED = "refused"

#: Коды несостоявшейся отправки: вердикта хозяина не было. Квитанция с
#: таким кодом — «ждёт», а не «отказано».
CODE_UNREACHABLE = "owner-unreachable"
CODE_OWNER_BROKEN = "owner-broken"
#: Коды, по которым старая квитанция (без поля `outcome`) узнаётся как
#: ожидание: перечень закрытый, всё прочее — названный отказ хозяина.
WAITING_CODES = (CODE_UNREACHABLE, CODE_OWNER_BROKEN)
#: Код отказа хозяина по умолчанию, когда тело ответа его не назвало.
CODE_REFUSED = "append-refused"

#: Имя этапа сверки решения с исходными документами.
STAGE_SOURCE_CHECK = "сверка с ИДАК"
#: Имена этапов нажатий — из словаря записи (одно место на две системы).
STAGES_PRESS = tuple(rr.COSMOS_STAGES.values()) + ("ростер на пейринге",)

#: Ожидание хозяина записи — короткое: нажатие не ждёт панель.
TIMEOUT_S = 5.0
#: Явный повтор пользователем может дождаться медленного ответа хозяина.
#: Фиксированный бюджет, не параметр HTTP-запроса; автоматические попытки — 5 с.
EXPLICIT_RETRY_TIMEOUT_S = 30.0


# ------------------------------------------------------------- мелочи
def entries_path(wd) -> Path:
    return Path(wd) / ENTRIES_FILE


def outbox_path(wd) -> Path:
    return Path(wd) / OUTBOX_FILE


def receipts_path(wd) -> Path:
    return Path(wd) / RECEIPTS_FILE


def remote_path(wd) -> Path:
    return Path(wd) / REMOTE_FILE


def _read_rows(path: Path) -> list:
    """Строки файла-журнала; файла нет — пусто, кривая строка —
    пропускается (летопись читается и после обрыва записи)."""
    out = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _append_row(path: Path, row: dict) -> None:
    """Строка в конец файла (только добавление)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rewrite(path: Path, rows: list) -> None:
    """Файл целиком — атомарно (замена строки по ключу)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                           for r in rows), encoding="utf-8")
    os.replace(tmp, p)


def _key(entry: dict) -> dict:
    return {"system": SYSTEM, "stage": entry.get("stage"),
            "substage": entry.get("substage")}


def _same_key(a, b) -> bool:
    a = a if isinstance(a, dict) else {}
    b = b if isinstance(b, dict) else {}
    return [a.get(k) for k in ("system", "stage", "substage")] == \
           [b.get(k) for k in ("system", "stage", "substage")]


def _put(path: Path, row: dict) -> None:
    """Строка по ключу: новая — добавлением, известная — заменой
    (последнее слово свежее)."""
    rows = _read_rows(path)
    for old in rows:
        if _same_key(old.get("key"), row.get("key")):
            _rewrite(path, [row if _same_key(r.get("key"), row.get("key"))
                            else r for r in rows])
            return
    _append_row(path, row)


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def entries(wd) -> list:
    """Записи этапов COSMOS по этому пакету — как они лежат."""
    return _read_rows(entries_path(wd))


# ------------------------------------------------- попытка → запись этапа
def attempt_for_journal(rec: dict) -> dict:
    """Запись попытки в именах словаря записи — ОДНА точка правды.

    Сегодняшний носитель (`webapp/package_runs.py`, `attempts[]`) держит
    параметры под именем `params`, а стенное время счёта — внутри
    `resources`; словарь записи зовёт их `request` и `solver_seconds`.
    Расхождение имён снимается здесь, а не в каждом читателе."""
    a = dict(rec if isinstance(rec, dict) else {})
    if isinstance(a.get("params"), dict) and not isinstance(a.get("request"),
                                                            dict):
        a["request"] = a["params"]
    res = a.get("resources") if isinstance(a.get("resources"), dict) else {}
    if a.get("solver_seconds") is None:
        a["solver_seconds"] = res.get("solver_seconds")
    return a


#: Артефакты попытки, чьи отпечатки едут в запись: имя файла → вид.
_ARTIFACT_FILES = (("delivery.json", "выдача"),
                   ("certificate.json", "сертификат"),
                   ("certificate.md", "сертификат словами"))


def _artifacts_of(wd: Path, rec: dict) -> list:
    """Артефакты попытки ссылкой и отпечатком: выдача, сертификат.
    Файла нет — строки нет: запись не обещает того, чего не существует."""
    out = []
    catalog = str((rec or {}).get("catalog") or "")
    if not catalog or catalog.startswith("снимок не снят"):
        return out
    root = Path(wd) / catalog
    for name, kind in _ARTIFACT_FILES:
        sha = _sha256_file(root / name)
        if sha:
            out.append({"kind": kind, "path": "%s/%s" % (catalog, name),
                        "sha256": sha})
    return out


def _results_of_attempt(rec: dict, entry: dict, artifacts: list) -> dict:
    """Вклад попытки в итоги записи: вердикт задачи и выдачи ссылками."""
    verdict = rec.get("verdict")
    summary = rec.get("summary") if isinstance(rec.get("summary"), dict) else {}
    if verdict is None:
        verdict = summary.get("verdict")
    out: dict = {"verdicts": {}, "deliveries": []}
    if verdict:
        out["verdicts"][str(entry.get("stage"))] = str(verdict)
    out["deliveries"] = [a for a in artifacts if a.get("kind") == "выдача"]
    return out


def entry_of_attempt(wd, rec: dict) -> tuple[dict, dict]:
    """(запись этапа, вклад в итоги) по записи попытки нажатия."""
    a = attempt_for_journal(rec)
    entry = rr.attempt_to_journal(a)
    artifacts = _artifacts_of(Path(wd), a)
    if artifacts:
        entry["artifacts"] = list(entry.get("artifacts") or []) + artifacts
    summary = a.get("summary") if isinstance(a.get("summary"), dict) else {}
    if summary.get("certified") is not None:
        # Счётчики хранят числа: «удостоверено» — 1 либо 0, чтобы
        # признак не пропал при нормализации словаря.
        entry.setdefault("counters", {})["certified"] = \
            1 if summary.get("certified") else 0
    return rr.normalize_entry(entry), _results_of_attempt(a, entry, artifacts)


def _count_of(value) -> int:
    """Число из поля, которое у одних носителей перечень, а у других —
    счёт (снимок сверки кладёт `problems` числом, панель — списком)."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _source_check_models(snap: dict) -> list:
    """Строки расхода читателя ИДАК для этапа «сверка с ИДАК».

    Снимок сверки несёт расход сам (`resources.models[]` — его кладёт
    `vmp/source_check_run.reader_resources` из разобранного `usage`
    канала читателя): вызовов, входных и выходных токенов, сосчитано ли.
    Строки едут в запись как есть — второго счёта расхода здесь нет.

    Снимок расхода НЕ назвал (снимок прежней версии, сверка ещё идёт
    либо отказала) — строка всё равно стоит, но с честным
    `counted: false` и причиной словами: пропажа строки читалась бы
    «читателя не звали», а это другое, и неизвестное за ноль не
    выдаётся. Читателя не назвал и снимок — строки нет: звать было
    некого."""
    snap = snap if isinstance(snap, dict) else {}
    resources = snap.get("resources") if isinstance(snap.get("resources"),
                                                    dict) else {}
    rows = [m for m in (resources.get("models") or []) if isinstance(m, dict)]
    if rows:
        return rows
    reader = str(snap.get("reader") or snap.get("model") or "").strip()
    if not reader:
        return []
    # `reader` — «контур/модель» (cloud/claude-sonnet-5): имя модели
    # справа, контур в строку расхода не едет (у неё своё поле).
    model = reader.rsplit("/", 1)[-1] or reader
    channel = str(snap.get("reader_channel") or snap.get("channel")
                  or "").strip() or "не назван"
    return [{"role": role_words(ROLE_CODE_SOURCE_READER),
             "role_code": ROLE_CODE_SOURCE_READER,
             "model": model, "provider": None,
             "calls": None, "input_tokens": None, "output_tokens": None,
             "counted": False,
             "note": "снимок сверки расхода читателя не назвал (канал %s)"
                     % channel}]


def _source_check_entry(task: str, attempt_id: str | None, snap: dict,
                        actor: str | None = None) -> dict:
    """Запись этапа сверки решения с исходными документами.

    Подэтап — номер попытки и задача: одна попытка может быть сверена по
    пейрингу и по ростеру, и это разные строки журнала."""
    snap = snap if isinstance(snap, dict) else {}
    counts = snap.get("counts") if isinstance(snap.get("counts"), dict) \
        else (snap.get("summary") if isinstance(snap.get("summary"), dict)
              else {})
    n_problems = _count_of(snap.get("problems"))
    violated = _count_of(counts.get("violated"))
    status = str(snap.get("status") or "")
    reason, words = None, None
    # Счётчики сверки — имена ВЕРДИКТОВ читателя как есть (ok,
    # soft_info, not_checkable, violated): перечень видов вердикта живёт
    # у проверок, и второго его списка здесь заводить нельзя.
    said = ", ".join("%s %s" % (k, v) for k, v in sorted(counts.items()))
    if status == "error":
        outcome = rr.OUTCOME_FAILURE
        reason = "source-check-failed"
        words = "сверка с ИДАК не прошла: %s" % str(snap.get("error"))[:300]
    elif status and status != "done":
        outcome = rr.OUTCOME_NOT_STARTED
        reason = "source-check-unfinished"
        words = "сверка с ИДАК идёт: исход ещё не назван"
    else:
        outcome = rr.OUTCOME_WARN if (violated or n_problems) else rr.OUTCOME_OK
        if violated:
            reason = "source-check-violations"
        elif n_problems:
            reason = "source-check-problems"
        words = ("сверка с ИДАК (%s): %s; проблем %s"
                 % (task, said or "строк нет", n_problems))
    counters = {str(k): v for k, v in counts.items()}
    counters["problems"] = n_problems
    artifacts = []
    if snap.get("protocol"):
        artifacts.append({"kind": "протокол сверки",
                          "path": str(snap.get("protocol")), "sha256": None})
    return rr.normalize_entry({
        "stage": STAGE_SOURCE_CHECK,
        "substage": "%s/%s" % (attempt_id or "без попытки", task),
        "started_at": None,
        "finished_at": snap.get("at") or snap.get("finished_at"),
        "outcome": outcome, "reason_code": reason, "words": words,
        "actor": actor,
        "artifacts": artifacts, "counters": counters,
        "resources": {"models": _source_check_models(snap),
                      "solver_seconds": None}})


# --------------------------------------------------------- запись этапа
def note(wd, entry: dict, results: dict | None = None,
         head: dict | None = None) -> dict:
    """Записать этап: в файл записей COSMOS, в свою запись (у частного
    прогона) и в очередь к хозяину; затем — попытка отправки.

    Возврат: `{"row": строка журнала, "words": слова бед сшивки,
    "stitch_code": код, когда шить некуда}`. Беда сшивки живёт ТОЛЬКО в
    возврате — исключением наружу она не выходит и нажатия не касается."""
    wd = Path(wd)
    row = {"key": _key(entry), "entry": entry, "at": rr.now_iso(),
           "results": results or {}}
    _put(entries_path(wd), row)
    words: list = []
    if head is None:
        head = _head_of_workdir(wd)
    if head is not None and head.get("owner") == "cosmos":
        words += _write_own_record(wd, entry, results)
    stitch_code = None
    if head is not None and head.get("record_api"):
        _put(outbox_path(wd), row)
        words += flush_outbox(wd, head).get("words") or []
    elif head is not None and head.get("owner") != "cosmos":
        # Хозяин записи есть, а адреса его ручек нет: сшивать некуда.
        # Это состояние пакета, а не беда нажатия — оно называется
        # кодом в состоянии сшивки, а не предупреждением у каждой
        # попытки.
        stitch_code = head.get("record_api_code") or "record-api-missing"
    # Записанный этап меняет узел дерева (состояние, вердикты, токены):
    # индекс перестраивается по событию, не чаще раза в срок (раздел
    # 12.1 проекта). Беда индекса нажатия не касается.
    from . import run_tree_api
    run_tree_api.note_change("записан этап журнала COSMOS")
    return {"row": row, "words": words, "stitch_code": stitch_code}


def _head_of_workdir(wd: Path) -> dict | None:
    """Голова прогона по папке нажатий: своя запись либо ничего.

    Манифест пакета здесь не читается: крючки нажатия и сверки зовут
    журнал с уже разобранной головой, а этот путь — запасной для
    внутренних вызовов."""
    from . import run_gate
    return run_gate.head_for(wd, {})


def _write_own_record(wd: Path, entry: dict, results: dict | None) -> list:
    """Журнал в СВОЮ запись (частный прогон): дописать этап и закрыть
    запись, когда прошли обе задачи."""
    words: list = []
    try:
        record = rr.load(wd)
    except rr.RunRecordError as e:
        return ["запись о прогоне не прочитана: %s" % str(e)[:200]]
    try:
        # Тот же ключ (этап, подэтап) в журнале записи ЗАМЕНЯЕТСЯ — как
        # у хозяина (раздел 11.2): повтор нажатия того же номера попытки
        # не имеет права превратить один этап в два.
        record["journal"] = [
            e for e in (record.get("journal") or [])
            if not (isinstance(e, dict)
                    and e.get("stage") == entry.get("stage")
                    and e.get("substage") == entry.get("substage"))]
        rr.append_stage(record, entry)
    except rr.RunRecordError as e:
        return ["этап в запись не принят: %s" % str(e)[:300]]
    tasks = {e.get("stage") for e in record.get("journal") or []
             if isinstance(e, dict) and e.get("stage") in STAGES_PRESS
             and e.get("outcome") != rr.OUTCOME_NOT_STARTED}
    pairing = rr.COSMOS_STAGES["pairing"] in tasks
    rostering = bool({rr.COSMOS_STAGES["rostering"],
                      "ростер на пейринге"} & tasks)
    if pairing and rostering:
        try:
            rr.finish(record, results_of(entries(wd)))
            # Сравнение считается по ЗАКРЫТОЙ записи: у прогона уже есть
            # вердикты, время и расход, и сравнивать надо их, а не
            # половину.
            rr.finish(record, {"comparison": _comparison(record)})
        except rr.RunRecordError as e:
            words.append("итоги записи не приняты: %s" % str(e)[:300])
    try:
        rr.save(wd, record)
    except OSError as e:
        words.append("запись о прогоне не сохранена: %s" % str(e)[:200])
    return words


def _comparison(record: dict) -> dict:
    """Сравнения в итогах записи при её закрытии (раздел 12.4 проекта):
    с ожиданиями набора, с предыдущим прогоном того же корня и с
    золотым прогоном.

    Ожидания пишет раннер (их источник — манифест набора), и здесь они
    только сохраняются; предыдущий и золотой берутся из индекса дерева
    (`webapp/run_tree_api.py`). Сравнивать не с чем — пусто: карточке
    честная пустота понятнее выдуманного сравнения. Беда индекса
    закрытия записи не отменяет."""
    old = (record.get("results") or {}).get("comparison") or {}
    out = {"expectations": dict(old.get("expectations") or {}),
           "previous_run": dict(old.get("previous_run") or {}),
           "golden": dict(old.get("golden") or {})}
    try:
        from . import run_tree_api
        got = run_tree_api.comparison_for(record)
    except Exception:                                      # noqa: BLE001
        return out
    for key in ("previous_run", "golden"):
        if got.get(key):
            out[key] = got[key]
    return out


def results_of(rows) -> dict:
    """Итоги записи по строкам журнала COSMOS: вердикты по задачам и
    выдачи ссылками с отпечатками (повтор одной ссылки не удваивается)."""
    verdicts: dict = {}
    deliveries: list = []
    seen: set = set()
    for row in rows or []:
        res = row.get("results") if isinstance(row, dict) else None
        if not isinstance(res, dict):
            continue
        for k, v in (res.get("verdicts") or {}).items():
            verdicts[str(k)] = v
        for d in res.get("deliveries") or []:
            if not isinstance(d, dict):
                continue
            key = (d.get("kind"), d.get("sha256"), d.get("path"))
            if key in seen:
                continue
            seen.add(key)
            deliveries.append(d)
    return {"verdicts": verdicts, "deliveries": deliveries}


# ------------------------------------------------------------- крючки
def note_attempt(wd, rec: dict, head: dict | None = None) -> dict:
    """Крючок нажатия: законченная попытка — этапом журнала."""
    entry, results = entry_of_attempt(wd, rec)
    return note(wd, entry, results, head)


def note_source_check(wd, task: str, attempt_id: str | None, snap: dict,
                      actor: str | None = None,
                      head: dict | None = None) -> dict:
    """Крючок сверки с ИДАК: снимок сверки — этапом журнала."""
    entry = _source_check_entry(task, attempt_id, snap, actor)
    return note(wd, entry, None, head)


# -------------------------------------------------------------- сшивка
def append_body(head: dict, rows: list, results: dict | None = None) -> dict:
    """Тело ручки `record/append` хозяина записи (раздел 11.2)."""
    body = {"schema": APPEND_SCHEMA,
            "record_id": (head or {}).get("record_id"),
            "declaration_sha256": (head or {}).get("declaration_sha256"),
            "system": SYSTEM,
            "entries": [r.get("entry") for r in rows
                        if isinstance(r, dict) and isinstance(r.get("entry"),
                                                              dict)]}
    if results and (results.get("verdicts") or results.get("deliveries")):
        body["results"] = results
    return body


def append_to_owner(record_api: str, body: dict,
                    timeout: float = TIMEOUT_S) -> dict:
    """Отправка записей хозяину: POST `<record_api>/append`.

    Возврат — квитанция: `{ok, outcome, status, code, words, record}`.
    Ни сеть, ни отказ хозяина исключением наружу не выходят: очередь и
    слова делают беду видимой, а нажатие продолжается.

    ИСХОД называется тремя словами, а не двумя (разбор 11.09.2026).
    «Отказано» — только НАЗВАННЫЙ вердикт хозяина: код отказа в теле
    ответа либо ответ 4xx. Таймаут занятой панели, обрыв сети и поломка
    службы — «ждёт»: вердикта не было, запись у хозяина к этому времени
    могла уже лечь полностью, и повтор по ключу идемпотентности заменит
    её, а не удвоит. Считать такую отправку отказом значило бы обвинять
    хозяина в том, чего он не говорил."""
    url = "%s/append" % str(record_api).rstrip("/")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = _urlrequest.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 0) or 0)
            raw = resp.read()
    except _urlerror.HTTPError as e:                       # noqa: PERF203
        status = int(getattr(e, "code", 0) or 0)
        try:
            raw = e.read()
        except Exception:                                  # noqa: BLE001
            raw = b""
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False, "outcome": STITCH_WAITING, "status": None,
                "code": CODE_UNREACHABLE,
                "words": "хозяин записи недоступен (%s): %s — запись ждёт "
                         "повтора" % (url, str(e)[:200]), "record": None}
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    if status == 200 and payload.get("ok"):
        return {"ok": True, "outcome": STITCH_SENT, "status": status,
                "code": None,
                "accepted": payload.get("accepted"),
                "replaced": payload.get("replaced"),
                "words": None, "record": payload.get("record")}
    words = payload.get("words") or payload.get("detail") \
        or ("хозяин записи ответил %s" % status)
    if isinstance(words, list):
        words = "; ".join(str(w) for w in words)
    code = str(payload.get("code") or "")
    # Вердикт хозяина назван, если он назвал код отказа либо ответил
    # 4xx («так нельзя»). Всё прочее — 5xx, ответ не по договору, пустое
    # тело — вердиктом не считается: отправка не состоялась.
    if not (code or 400 <= status < 500):
        return {"ok": False, "outcome": STITCH_WAITING, "status": status,
                "code": CODE_OWNER_BROKEN,
                "words": "%s — вердикта нет, запись ждёт повтора"
                         % str(words)[:400],
                "record": payload.get("record")}
    return {"ok": False, "outcome": STITCH_REFUSED, "status": status,
            "code": code or CODE_REFUSED,
            "words": str(words)[:500], "record": payload.get("record")}


def receipt_outcome(receipt) -> str:
    """Исход отправки по квитанции — одно из трёх слов `STITCH_*`.

    Читаются и СТАРЫЕ квитанции, писанные до разведения исходов: поля
    `outcome` у них нет, и исход судится по коду — коды несостоявшейся
    отправки (`WAITING_CODES`) значат «ждёт», любой другой отказ — это
    названный вердикт хозяина."""
    r = receipt if isinstance(receipt, dict) else {}
    got = r.get("outcome")
    if got in (STITCH_SENT, STITCH_WAITING, STITCH_REFUSED):
        return str(got)
    if r.get("ok"):
        return STITCH_SENT
    if str(r.get("code") or "") in WAITING_CODES:
        return STITCH_WAITING
    return STITCH_REFUSED


def flush_outbox(wd, head: dict | None, *, explicit_retry: bool = False) -> dict:
    """Отправить очередь хозяину: принятое уходит из очереди, отказ и
    несостоявшаяся отправка — остаются в ней, ответ ложится квитанцией.

    Возврат несёт ИСХОД словом (`outcome`): «ждёт» у неудачи транспорта
    и «отказано» у вердикта хозяина — счётчики карточки считают их
    порознь. Только явный пользовательский повтор получает 30 с ожидания;
    автоматические отправки и открытие карточки сохраняют бюджет 5 с."""
    wd = Path(wd)
    api = (head or {}).get("record_api")
    rows = _read_rows(outbox_path(wd))
    if not api or not rows:
        return {"sent": 0, "waiting": len(rows), "words": [],
                "outcome": STITCH_SENT if not rows else STITCH_WAITING}
    body = append_body(head, rows, results_of(entries(wd)))
    timeout = EXPLICIT_RETRY_TIMEOUT_S if explicit_retry else TIMEOUT_S
    got = append_to_owner(api, body, timeout=timeout)
    outcome = receipt_outcome(got)
    receipt = {"at": rr.now_iso(), "url": api, "ok": bool(got.get("ok")),
               "outcome": outcome,
               "status": got.get("status"), "code": got.get("code"),
               "words": got.get("words"),
               "keys": [r.get("key") for r in rows],
               "accepted": got.get("accepted"), "replaced": got.get("replaced")}
    _append_row(receipts_path(wd), receipt)
    if got.get("ok"):
        # Из очереди уходят ТОЛЬКО отправленные ключи: пока шла отправка,
        # соседняя нить (сверка с ИДАК) могла положить свою строку, и
        # чистка «всей очереди» её бы потеряла.
        sent_keys = [r.get("key") for r in rows]
        rest = [r for r in _read_rows(outbox_path(wd))
                if not any(_same_key(r.get("key"), k) for k in sent_keys)]
        _rewrite(outbox_path(wd), rest)
        if isinstance(got.get("record"), dict):
            try:
                write_json_atomic(remote_path(wd),
                                  {"at": rr.now_iso(),
                                   "record": got["record"]})
            except OSError:
                pass
        return {"sent": len(rows), "waiting": 0, "words": [],
                "outcome": STITCH_SENT}
    return {"sent": 0, "waiting": len(rows), "outcome": outcome,
            "words": [str(got.get("words") or got.get("code"))]}


def fetch_owner_record(record_api: str, timeout: float = TIMEOUT_S) -> dict:
    """Единая запись у хозяина: GET `record_api`."""
    try:
        req = _urlrequest.Request(str(record_api), method="GET")
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False, "words": "запись хозяина не прочитана (%s): %s"
                                      % (record_api, str(e)[:200])}
    if not isinstance(doc, dict):
        return {"ok": False, "words": "запись хозяина пришла не словарём"}
    record = doc.get("record") if isinstance(doc.get("record"), dict) else doc
    return {"ok": True, "record": record}



def _keeper_state(record: dict) -> str:
    """Состояние единой записи у хозяина: закрытую запись судит ХОЗЯИН
    (results.state — «завершён», «сбой», «остановлен …»), пересчёт по
    журналу — только пока хозяин её не закрыл. Иначе одна запись панели
    без отметки окончания (живая находка площадок 10.09: data_freeze/data
    «оборвано») перекрашивала «завершён» хозяина в «сбой»."""
    results = (record or {}).get("results") or {}
    closed = str(results.get("state") or "")
    if closed and closed not in (rr.STATE_DECLARED, rr.STATE_RUNNING):
        return closed
    return rr.state_from_journal(record)

def record_view(wd, head: dict | None) -> dict:
    """Единая запись для карточки: у хозяина, если он доступен; иначе —
    последняя полученная копия либо своя запись частного прогона.

    Полный прогон без связи с панелью честно говорит «запись хозяина
    недоступна» и показывает голову с записями COSMOS: молчать о
    недоступности нельзя, выдумывать чужую запись — тем более."""
    wd = Path(wd)
    words: list = []
    if head and head.get("record_api"):
        got = fetch_owner_record(head["record_api"])
        if got.get("ok"):
            try:
                write_json_atomic(remote_path(wd),
                                  {"at": rr.now_iso(), "record": got["record"]})
            except OSError:
                pass
            return {"source": "remote", "record": got["record"],
                    "words": [],
                    "state": _keeper_state(got["record"])}
        words.append(str(got.get("words")))
        cached = read_json(remote_path(wd))
        if isinstance(cached, dict) and isinstance(cached.get("record"), dict):
            words.append("показана последняя полученная копия записи от %s"
                         % cached.get("at"))
            return {"source": "remote-cache", "record": cached["record"],
                    "words": words,
                    "state": _keeper_state(cached["record"])}
    if head and head.get("owner") == "cosmos":
        try:
            record = rr.load(wd)
        except rr.RunRecordError as e:
            words.append(str(e)[:200])
            return {"source": "none", "record": None, "words": words,
                    "state": "не объявлен"}
        return {"source": "local", "record": record, "words": words,
                "state": (record.get("results") or {}).get("state")
                or rr.state_from_journal(record)}
    if not head:
        return {"source": "none", "record": None, "words": words,
                "state": "не объявлен"}
    words.append("запись хозяина недоступна: показаны голова записи из "
                 "манифеста пакета и записи этапов COSMOS")
    rows = [r.get("entry") for r in entries(wd) if isinstance(r, dict)]
    # Пустой журнал — не «завершён»: прогон объявлен, а этапов COSMOS
    # ещё не было. Состояние по исходам судится только там, где исходы
    # есть.
    return {"source": "none", "record": None, "words": words,
            "state": (rr.state_from_journal({"journal": rows}) if rows
                      else rr.STATE_DECLARED)}


def stitch_state(wd, head: dict | None) -> dict:
    """Состояние сшивки: куда шьём, сколько отправок принято, сколько
    записей ждёт повтора, сколько ОТКАЗАНО хозяином и последними
    словами — почему.

    «Ждёт» и «отказано» — разные вещи (разбор 11.09.2026): отправка,
    оборвавшаяся таймаутом занятой панели или сетью, считается
    ОЖИДАНИЕМ — вердикта не было, запись у хозяина могла уже лечь
    целиком, и повтор по ключу идемпотентности её заменит. Отказано —
    только там, где хозяин назвал причину. Слова у них тоже разные:
    `last_error` — последний ОТКАЗ хозяина, `last_wait` — последняя
    несостоявшаяся отправка; пусто — такого исхода не было."""
    wd = Path(wd)
    waiting = len(_read_rows(outbox_path(wd)))
    receipts = _read_rows(receipts_path(wd))
    outcomes = [receipt_outcome(r) for r in receipts]
    sent = sum(1 for o in outcomes if o == STITCH_SENT)
    refused = sum(1 for o in outcomes if o == STITCH_REFUSED)
    last_error = None
    last_wait = None
    for r, outcome in zip(reversed(receipts), reversed(outcomes)):
        if outcome == STITCH_REFUSED and last_error is None:
            last_error = r.get("words")
        elif outcome == STITCH_WAITING and last_wait is None:
            last_wait = r.get("words")
    if head and head.get("record_api"):
        mode = "remote"
    elif head and head.get("owner") == "cosmos":
        mode = "local"
    else:
        mode = "none"
    return {"mode": mode, "record_api": (head or {}).get("record_api"),
            "sent": sent, "waiting": waiting, "refused": refused,
            "last_error": last_error, "last_wait": last_wait}
