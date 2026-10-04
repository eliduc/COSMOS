# -*- coding: utf-8 -*-
"""Канал приёма пакета постановки (CDPM -> COSMOS) и резолвер
«текущего ВМП».

До канала единственным входом пакета была переменная окружения
CREW_TASK_PACKAGE, выставляемая руками (мануал 03). Канал добавляет
машинный путь: POST /api/task-package/receive принимает байты zip по
токену канала (env CREW_CHANNEL_TOKEN; без неё канал выключен,
fail-closed), НЕМЕДЛЕННО верифицирует пакет — гейт производителя
ин-процессно + матрица готовности — и кладёт его во входной каталог
данных task-inbox/<sha>/. Расчёт приёмом НЕ стартует: запуск остаётся
отдельным действием человека.

Резолвер current_package() делает оба способа получения неразличимыми
для потребителей (конвейер, карточка ВМП, личность прогона):
CREW_TASK_PACKAGE сохраняет ПРИОРИТЕТ (обратная совместимость; тесты
и личность прогона держат это поведение; залежавшаяся переменная
честно затеняет канал — карточка показывает источник «файл»), без неё
— пакет ТЕКУЩЕГО ПРОГОНА COSMOS (webapp.cosmos_runs, слово владельца
07.09.2026), а до заведения хранилища прогонов — указатель канала
task-inbox/current.json (зеркало; после заведения пишется последним и
только из прогона).

Один слот текущего пакета (живой случай 11.09.2026): пока по текущему
пакету идёт НЕЗАВЕРШЁННАЯ попытка нажатия, приём другого пакета
отказывает 409 словами (`slot-busy`) — и по машинному пути, и загрузкой
человеком. Иначе попытка досчитывает один пакет, а площадка показывает
другой. Тот же пакет подменой не считается; законченная попытка (итог
либо явный отказ) и осиротевшая слот освобождают.

Дисциплина каталога данных: всё пишется только в
_data_paths.data_root()/task-inbox (контракт «результаты вне git»);
журнал приёмов receipts.jsonl — только добавлением.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

import _data_paths

REPO_ROOT = Path(__file__).resolve().parent.parent
_logger = logging.getLogger(__name__)

CURRENT_NAME = "current.json"
RECEIPTS_NAME = "receipts.jsonl"
VERDICT_NAME = "verdict.json"
PACKAGE_NAME = "package.zip"

#: Журнал пишется из пула потоков — короткая блокировка на добавление
#: строки (образец: webapp/audit_inbox._lock).
_lock = threading.Lock()

# Карточка пакета тяжелее опроса конвейера (sha байтов, правила зон,
# критерий) — кэш учитывает также каталог, от которого зависит сверка.
_CARD_CACHE: dict = {"key": None, "value": None}


def inbox_dir() -> Path | None:
    """Входной каталог канала — в каталоге данных, не в дереве репо.

    CREW_TASK_INBOX переопределяет путь; ПУСТОЕ значение отключает
    инбокс вовсе (None). Урок живого e2e 2026-08-29: первый же обмен
    оставил НАСТОЯЩИЙ указатель в каталоге данных машины, и тесты
    без изоляции увидели канальный пакет там, где ждали «пакета
    нет», — набор изолируется пустым значением в conftest, как у
    соседних носителей контура."""
    env = os.environ.get("CREW_TASK_INBOX")
    if env is not None:
        return Path(env) if env.strip() else None
    return _data_paths.data_root() / "task-inbox"


def _now() -> str:
    """Метка времени — часы машины (правило статусов), секунды."""
    return datetime.now().isoformat(timespec="seconds")


def registry_path() -> Path:
    """Путь реестра исполнителей: CREW_VMP_REGISTRY площадки, без неё
    — файл в каталоге данных. Сюда пишет авто-вывод при приёме
    (P-N-SHD), отсюда читают верификация и гейт запуска."""
    env = os.environ.get("CREW_VMP_REGISTRY")
    if env:
        return Path(env)
    return _data_paths.data_root() / "vmp-executors-registry.json"


# --------------------------------------------------------------- токен

def token_state(presented: str | None) -> str:
    """Состояние аутентификации канала: «off» — CREW_CHANNEL_TOKEN не
    задан (канал выключен, fail-closed), «ok» — токен совпал,
    «denied» — не предъявлен или не совпал. Сравнение — постоянным
    временем (hmac.compare_digest, образец webapp/auth.py); причина
    отказа наружу не детализируется."""
    want = (os.environ.get("CREW_CHANNEL_TOKEN") or "").strip()
    if not want:
        return "off"
    got = (presented or "").strip()
    if got and hmac.compare_digest(got.encode("utf-8"),
                                   want.encode("utf-8")):
        return "ok"
    return "denied"


# ------------------------------------------------------------ резолвер

#: sha пакета «файлом руками» — кэш по (путь, mtime_ns, размер): файл
#: не хешируется при каждом опросе конвейера.
_ENV_SHA_CACHE: dict = {"key": None, "sha": None}


def _env_package_sha(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _ENV_SHA_CACHE["key"] != key:
        try:
            _ENV_SHA_CACHE["sha"] = hashlib.sha256(
                path.read_bytes()).hexdigest()
        except OSError:
            return None
        _ENV_SHA_CACHE["key"] = key
    return _ENV_SHA_CACHE["sha"]


def current_package() -> dict | None:
    """Текущий пакет постановки: {"path", "sha", "source": {kind, at,
    ...}, "run": {run, title, workdir, implicit} | None}.

    Приоритет — CREW_TASK_PACKAGE («файл руками»: обратная
    совместимость, личность прогона и тесты держат это поведение;
    неявный прогон — по маркеру держателя базового каталога нажатий, если
    хранилище прогонов заведено); без переменной — пакет ТЕКУЩЕГО
    ПРОГОНА COSMOS при заведённом хранилище, иначе указатель канала
    task-inbox/current.json (площадка до первого действия с прогонами
    работает ровно как прежде). Нет ни того, ни другого — честный None.
    Существование файла здесь не полицейское: политика отказа — у
    потребителя (конвейер честно пуст, личность прогона отказывает
    громко); утерянный пакет прогона отдаётся с path None."""
    from webapp import cosmos_runs
    env = (os.environ.get("CREW_TASK_PACKAGE") or "").strip()
    if env:
        at = None
        try:
            at = datetime.fromtimestamp(
                Path(env).stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            pass
        sha = _env_package_sha(Path(env))
        return {"path": env, "sha": sha,
                "source": {"kind": "file", "at": at},
                "run": cosmos_runs.implicit_run_of(sha) if sha else None}
    box = inbox_dir()
    if box is None:
        return None
    if cosmos_runs.storage_state() == "ready":
        doc, pkg = cosmos_runs.current_package_record()
        if doc is None or pkg is None:
            return None
        path = cosmos_runs.package_path_of(pkg)
        out = {"path": str(path) if path is not None
               # Пакет утерян: путь входного каталога (файла нет — потребители
               # честно видят «пакета нет»), причина — кодом.
               else str(box / str(pkg.get("sha")) / PACKAGE_NAME),
               "sha": pkg.get("sha"),
               "source": {"kind": pkg.get("source") or "channel",
                          "at": pkg.get("last_received_at")
                          or pkg.get("received_at"),
                          "sha": pkg.get("sha"),
                          "spec_version": pkg.get("spec_version"),
                          "source_run": pkg.get("source_run")},
               "run": {"run": doc.get("run"), "title": doc.get("title"),
                       "workdir": pkg.get("workdir"), "implicit": False}}
        if path is None:
            out["note_code"] = "package-bytes-missing"
        return out
    try:
        doc = json.loads((box / CURRENT_NAME)
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path = doc.get("path") if isinstance(doc, dict) else None
    if not isinstance(path, str) or not Path(path).is_file():
        return None
    return {"path": path, "sha": doc.get("sha"),
            "source": {"kind": doc.get("source") or "channel",
                       "at": doc.get("received_at"),
                       "sha": doc.get("sha"),
                       "spec_version": doc.get("spec_version"),
                       "source_run": doc.get("source_run")},
            "run": None}


# ------------------------------------------------------------- журнал

def _journal(row: dict) -> None:
    """Строка в журнал приёмов — ТОЛЬКО добавлением (append)."""
    d = inbox_dir()
    if d is None:
        raise RuntimeError("инбокс канала отключён (CREW_TASK_INBOX "
                           "пуст) — журналу приёмов некуда писать")
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False)
    with _lock:
        with open(d / RECEIPTS_NAME, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def receipts_tail(limit: int = 5) -> list[dict]:
    """Хвост журнала приёмов — карточке («источник + время»)."""
    box = inbox_dir()
    if box is None:
        return []
    p = box / RECEIPTS_NAME
    try:
        lines = p.read_text(encoding="utf-8",
                            errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for ln in lines[-limit:]:
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _cleanup_staging(path: Path) -> None:
    """Remove only our temporary; cleanup must preserve the primary outcome."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        _logger.warning("Could not remove publication staging file %s",
                        path, exc_info=True)


def _write_json_atomic(path: Path, doc: dict) -> None:
    """Атомарная запись: tmp с uuid-суффиксом -> os.replace (образцы
    app.py / problem_manifest; uuid, не pid — sync-обработчики одного
    процесса крутятся в пуле потоков)."""
    tmp = path.with_name("%s.%s.tmp" % (path.name,
                                        uuid.uuid4().hex[:12]))
    try:
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, path)
    finally:
        _cleanup_staging(tmp)


# -------------------------------------------------------------- приём

def _brief(verdict: str, *, detail: str, sha: str,
           mg: dict | None = None, mismatch: list | None = None,
           spec_version=None, source_run=None,
           formulation: dict | None = None) -> dict:
    """Краткий вердикт приёма — согласованная форма ответа канала.
    formulation — вердикт постановки как задачи (vmp.formulation_check):
    показание рядом с матрицей готовности, приём и запуск им пока не
    решаются (решение владельца о замке отдельно)."""
    return {"verdict": verdict, "mg": mg or {},
            "mismatch": mismatch or [], "detail": detail,
            "sha": sha, "spec_version": spec_version,
            "source_run": source_run, "already_accepted": False,
            "formulation": formulation or {}}


def slot_hold(sha: str) -> dict | None:
    """Занят ли ОДИН слот текущего пакета — словами отказа либо None.

    Слот занят, пока по ТЕКУЩЕМУ пакету идёт незаконченная попытка
    нажатия: подменять пакет под идущим расчётом нельзя (живой случай
    11.09.2026 — чужой сценарий подал пакет между нажатиями, и разобрать,
    к какому пакету относится результат, стало нельзя). ТОТ ЖЕ пакет
    подменой не считается: повтор отправки слот не трогает и отказа не
    получает.

    Беда чтения состояния приёма НЕ держит: сторож молчит там, где не
    смог рассудить, — отказ по неизвестной причине хуже принятого
    пакета."""
    try:
        from webapp import package_runs as pr
        hold = pr.current_press_hold()
    except Exception:                                      # noqa: BLE001
        return None
    if not hold or str(hold.get("package_sha") or "") == str(sha):
        return None
    return {"code": pr.CODE_SLOT_BUSY,
            "detail": "слот занят: идёт попытка %s по пакету %s (актор %s, "
                      "начата %s) — дождитесь её итога либо остановите её; "
                      "новый пакет не принят"
                      % (hold.get("attempt_id"),
                         str(hold.get("package_sha"))[:12],
                         hold.get("actor"), hold.get("started_at"))}


def receive(raw: bytes, sha_header: str | None,
            spec_header: str | None = None,
            source_run_header: str | None = None,
            source: str = "channel") -> tuple[int, dict]:
    """Принять байты пакета. Возврат: (HTTP-код, краткий вердикт).

    Порядок: сверка sha по байтам -> ОДИН СЛОТ текущего пакета (идёт
    попытка нажатия по другому пакету — отказ 409 словами) ->
    идемпотентность по каталогу task-inbox/<sha>/ (повтор — тот же
    вердикт, гейт заново не гоняется) -> гейт производителя
    ин-процессно -> разбор пакета -> верификация матрицы готовности.
    Отказ (REFUSED/GATE_RED) ничего не оставляет в каталоге приёма;
    принятие кладёт package.zip, обновляет current.json и пишет
    verdict.json (его наличие — маркер завершённого приёма). Расчёт НЕ
    стартует.

    Сюда же приходит загрузка пакета человеком
    (`POST /api/task-package/upload`, source="upload"): вход в слот
    ОДИН на оба пути, и правило слота у них общее."""
    if inbox_dir() is None:
        return 503, _brief(
            "REFUSED", sha=str(sha_header or ""),
            detail="входной каталог канала отключён "
                   "(CREW_TASK_INBOX пуст) — приём невозможен")
    got = hashlib.sha256(raw).hexdigest()
    want = (sha_header or "").strip().lower()
    if want != got:
        # Ничего не сохраняется: байты не подтверждены отправителем.
        return 422, _brief(
            "REFUSED", sha=got,
            detail="sha не совпал: заголовок %s, факт %s"
                   % (want or "<нет>", got))

    # Слот текущего пакета один: пока по нему идёт попытка, подмены нет.
    busy = slot_hold(got)
    if busy is not None:
        return 409, dict(_brief("REFUSED", sha=got, detail=busy["detail"]),
                         code=busy["code"])

    pkg_dir = inbox_dir() / got
    verdict_path = pkg_dir / VERDICT_NAME
    if verdict_path.is_file():
        # Идемпотентный повтор: тот же краткий вердикт, второй записи
        # пакета нет, в журнале — пометка повтора.
        try:
            brief = json.loads(verdict_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            brief = None
        if isinstance(brief, dict):
            brief["already_accepted"] = True
            # Указатель «текущий» — про намерение отправителя, а
            # идемпотентность — про приём (проход H0-КИ, 05.09.2026: повтор
            # ранее принятого пакета не становился текущим, и нажать его
            # было нельзя). Повтор кладёт пакет в ТЕКУЩИЙ ПРОГОН COSMOS
            # (зеркало current.json пишется прогоном, последним); при
            # выключенном хранилище прогонов — переставляет current.json
            # на этот sha, как прежде; verdict.json не трогается.
            pkg_path = pkg_dir / PACKAGE_NAME
            current_switched = None
            row_extra: dict = {}
            if pkg_path.is_file():
                # Повтор — не первичный приём: если хранилище прогонов
                # заводится этим действием, повторяемый sha оформляется
                # своим прогоном по времени первого принятого приёма и
                # лишь потом присоединяется к текущему (ревью 07.09).
                joined = _attach_to_run(got, pkg_path, source, brief, None,
                                        primary=False)
                if joined is None:
                    brief["cosmos_run"] = None
                    brief["run_note_code"] = "storage-off"
                    try:
                        _write_json_atomic(inbox_dir() / CURRENT_NAME, {
                            "sha": got, "path": str(pkg_path),
                            "received_at": _now(),
                            "spec_version": brief.get("spec_version"),
                            "source_run": brief.get("source_run"),
                            "source": source, "repeat": True})
                        current_switched = True
                    except OSError as e:
                        # Параллельное чтение указателя на Windows может
                        # помешать замене файла: приём повтора остаётся
                        # принятым, а несостоявшаяся перестановка названа
                        # словами в кратком вердикте и журнале (ревью CDPM
                        # 05.09), не HTTP 500.
                        current_switched = False
                        brief["current_switched"] = False
                        brief["detail"] = ((brief.get("detail") or "")
                                           + "; указатель «текущий» не переставлен: "
                                           + str(e)[:200]).strip("; ")
                else:
                    current_switched = _note_join(brief, joined, row_extra)
            else:
                # Байтов пакета нет — прогон не трогается, повтор назван
                # словами (лечится повторной отправкой после восстановления).
                brief["cosmos_run"] = None
                brief["run_note_code"] = "package-bytes-missing"
            # Повтор перевыводит реестр исполнителей: код мог
            # раскатиться после первого приёма (отпечатки устарели),
            # и переотправка того же sha — штатный путь пересверки
            # без переизвлечения. Сбой вывода повтор не рушит, но и не
            # глотается: с шага 2 каталога шаблонов вывод зависит от
            # каталога, и несостоявшийся перевывод назван словами в
            # кратком вердикте и журнале (как в первичном приёме) —
            # иначе оператор увидел бы «реестр устарел» без причины.
            registry_note = None
            try:
                from vmp import registry_derive as rd
                from webapp import task_package as wtp
                pkg = wtp.load(pkg_dir / PACKAGE_NAME, run_gate=False)
                rd.derive_and_write(
                    pkg.get("model") or {}, REPO_ROOT,
                    registry_path(),
                    scope="канал (повтор): sha %s" % got[:12])
            except Exception as e:                    # noqa: BLE001
                registry_note = ("вывод реестра исполнителей не удался: "
                                 "%s" % e)
                brief["detail"] = "; ".join(
                    x for x in ((brief.get("detail") or ""), registry_note)
                    if x)
            row = {"at": _now(), "sha": got,
                   "spec_version": brief.get("spec_version"),
                   "source_run": brief.get("source_run"),
                   "size": len(raw),
                   "verdict": brief.get("verdict"),
                   "mg": {t: (c or {}).get("state") for t, c in
                          (brief.get("mg") or {}).items()},
                   "repeat": True, "current_switched": current_switched,
                   "cosmos_run": brief.get("cosmos_run"),
                   # вид источника — оформлению прежних данных (раздел 12)
                   "source": source}
            if registry_note:
                row["registry_note"] = registry_note
            row.update(row_extra)
            _journal(row)
            return 200, brief
        # кривой verdict.json — честный переприём обычным путём ниже

    inbox = inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    # Верификация идёт на временном файле: отказанный пакет не должен
    # оставлять и пустого каталога <sha>/ (приёма не было).
    tmp = inbox / ("incoming-%s.zip" % uuid.uuid4().hex[:12])
    try:
        tmp.write_bytes(raw)
        return _verify_and_store(tmp, raw, got, spec_header,
                                 source_run_header, pkg_dir, source)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            # Staging cleanup must not replace a completed receipt/refusal or
            # hide the primary publication error. This path is never current.
            _logger.warning("Could not remove incoming package staging file %s",
                            tmp, exc_info=True)


def _verify_and_store(tmp: Path, raw: bytes, sha: str,
                      spec_header: str | None,
                      source_run_header: str | None,
                      pkg_dir: Path,
                      source: str = "channel") -> tuple[int, dict]:
    # Гейт производителя ИН-ПРОЦЕССНО (прецедент cdpm/button.py):
    # быстрее сабпроцесса и отдаёт problems перечнем.
    try:
        from cdpm.gates import gate_task_package as gate
        problems, _counters = gate.check(tmp)
    except Exception as e:                            # noqa: BLE001
        problems = ["пакет не читается гейтом: %s" % e]

    # Разбор пакета: manifest/model/clean_data — всё, что нужно
    # верификации матрицы (гейт уже отработал строкой выше).
    from webapp import task_package as wtp
    try:
        pkg = wtp.load(tmp, run_gate=False)
    except Exception as e:                            # noqa: BLE001
        brief = _brief("REFUSED", sha=sha, detail=str(e))
        _journal({"at": _now(), "sha": sha, "spec_version": None,
                  "source_run": None, "size": len(raw),
                  "verdict": "REFUSED", "mg": {}, "repeat": False})
        return 422, brief
    man = pkg.get("manifest") or {}
    spec = man.get("spec_version")
    src_run = man.get("source_run")
    notes: list[str] = []
    if spec_header and str(spec_header).strip() != str(spec or ""):
        notes.append("заголовок X-Spec-Version (%s) расходится с "
                     "манифестом (%s)" % (spec_header, spec))
    if source_run_header and str(source_run_header).strip() \
            != str(src_run or ""):
        notes.append("заголовок X-Source-Run (%s) расходится с "
                     "манифестом (%s)" % (source_run_header, src_run))

    # Реестр исполнителей ВЫВОДИТСЯ из базы шаблонов на каждом приёме
    # (P-N-SHD 2026-08-30): ручное издание реестра уходит. Пробелы
    # вывода остаются вердиктами реестра и держат ЗАПУСК поимённо
    # (deep_check), приём пакета они не рушат. Несостоявшийся вывод —
    # заметка в вердикте; верификация ниже честно увидит прежний или
    # отсутствующий реестр.
    reg_path = registry_path()
    if not problems:
        # Красный гейт производителя реестра не трогает: отвергнутый
        # пакет не оставляет следа и здесь.
        try:
            from vmp import registry_derive as rd
            rd.derive_and_write(
                pkg.get("model") or {}, REPO_ROOT, reg_path,
                scope="канал: sha %s" % sha[:12])
        except Exception as e:                        # noqa: BLE001
            notes.append("вывод реестра исполнителей не удался: %s"
                         % e)

    # Верификация матрицы готовности — зеркало конвейера (flow).
    from vmp import readiness_verify as rv
    ver = rv.verify(man, pkg.get("model") or {}, pkg.get("clean_data"),
                    REPO_ROOT, reg_path, form=pkg.get("form"))
    # Постановка как задача (разделение функций, владелец 2026-09-03):
    # показание в вердикте канала; приём им не решается.
    from vmp import formulation_check as fc
    formulation = fc.summary(pkg)
    mg: dict = {}
    mismatch: list[str] = []
    for task, cell in (ver.get("columns") or {}).items():
        mg[task] = {"state": cell.get("declared_state"),
                    "launch_allowed": bool(cell.get("launch_allowed"))}
        # Ярусы критериев части (структура ВМП 2026-09-02): оптимизация
        # запрещена, режимы выполнимости — нет; вердикт канала обязан
        # это показывать, а не терять (аудит №2).
        if cell.get("criteria_tiers"):
            mg[task]["optimization_allowed"] = bool(
                cell.get("optimization_allowed"))
            mg[task]["criteria_tiers"] = list(cell["criteria_tiers"])
            mismatch.append("%s: ярусы критериев — оптимизация "
                            "запрещена: %s"
                            % (task, "; ".join(cell["criteria_tiers"])))
        if not cell.get("cell_match"):
            mismatch.append("%s: клетка манифеста не совпала с "
                            "пересчётом" % task)

    if problems:
        # Красный гейт производителя — отказ приёма: пакет не
        # сохраняется, каталог <sha>/ не появляется.
        # Оба носителя ответа сохраняют все причины, как detail при REFUSED;
        # здесь нельзя подменять полный отказ обрезанным предпросмотром.
        brief = _brief(
            "GATE_RED", sha=sha, mg=mg,
            mismatch=problems + mismatch,
            spec_version=spec, source_run=src_run,
            formulation=formulation,
            detail="; ".join(["красный гейт пакета: "
                              + "; ".join(problems)] + notes))
        _journal({"at": _now(), "sha": sha, "spec_version": spec,
                  "source_run": src_run, "size": len(raw),
                  "verdict": "GATE_RED",
                  "reasons_count": len(problems), "first_reason": problems[0],
                  "mg": {t: c["state"] for t, c in mg.items()},
                  "repeat": False})
        return 422, brief

    # Гейт зелёный: пакет принимается. Вердикт МГ может быть и
    # блокирующим — это СОСТОЯНИЕ постановки, не порча пакета.
    verdict = "OK" if ver.get("verdict") == "OK" else "MISMATCH"
    if verdict == "OK":
        detail_bits = ["пакет принят: гейт зелёный, матрица "
                       "готовности верифицирована"]
    else:
        detail_bits = ["пакет принят, но матрица готовности: %s — "
                       "запуск запрещён" % ver.get("verdict")]
    brief = _brief(verdict, sha=sha, mg=mg, mismatch=mismatch,
                   spec_version=spec, source_run=src_run,
                   formulation=formulation,
                   detail="; ".join(detail_bits + notes))

    # Публикация: package.zip -> verdict.json -> прогон -> зеркало
    # current.json -> журнал. verdict.json — маркер «приём завершён»
    # (идемпотентность); обрыв между ним и присоединением к прогону
    # лечится повторной отправкой того же sha (ветка повтора тоже
    # присоединяет). Прогон — свойство ПРИЁМА, вердикт — свойство пакета:
    # cosmos_run в verdict.json не пишется.
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = pkg_dir / PACKAGE_NAME
    stage = pkg_dir / ("%s.%s.tmp" % (PACKAGE_NAME,
                                      uuid.uuid4().hex[:12]))
    try:
        stage.write_bytes(raw)
        os.replace(stage, pkg_path)
    finally:
        _cleanup_staging(stage)
    _write_json_atomic(pkg_dir / VERDICT_NAME, brief)
    joined = _attach_to_run(sha, pkg_path, source, brief, man)
    row_extra: dict = {}
    if joined is None or not joined.get("run"):
        # Хранилище прогонов выключено либо присоединение не удалось:
        # зеркало — как прежде, площадка без хранилища видит пакет.
        _write_json_atomic(inbox_dir() / CURRENT_NAME, {
            "sha": sha, "path": str(pkg_path), "received_at": _now(),
            "spec_version": spec, "source_run": src_run,
            # Источник: «channel» — доставка панелью CDPM по каналу,
            # «upload» — загрузка пользователем кнопкой (репетиция
            # 05.09.2026: ручная загрузка показывалась как «канал приёма»).
            "source": source})
    if joined is not None:
        _note_join(brief, joined, row_extra)
    else:
        # квитанция несёт cosmos_run в любом случае: хранилище прогонов
        # выключено (CREW_COSMOS_RUNS_DIR пуст) — честный null с кодом
        brief["cosmos_run"] = None
        brief["run_note_code"] = "storage-off"
    _journal({"at": _now(), "sha": sha, "spec_version": spec,
              "source_run": src_run, "size": len(raw),
              "verdict": verdict,
              "mg": {t: c["state"] for t, c in mg.items()},
              "repeat": False, "cosmos_run": brief.get("cosmos_run"),
              # вид источника (channel | upload) — оформлению прежних данных
              # прогонами: без него загрузка рукой оформлялась бы «каналом»
              "source": source,
              **row_extra})
    return 200, brief


def _attach_to_run(sha: str, pkg_path: Path, source: str,
                   brief: dict | None, manifest: dict | None,
                   primary: bool = True) -> dict | None:
    """Пакет — в текущий прогон COSMOS (webapp.cosmos_runs.attach_package);
    None — хранилище прогонов выключено (шаг пропущен, стенды без
    изменений). Любой сбой — словами в возврате, приём не рушится.
    primary — первичный приём: его sha исключается из оформления прежних
    данных, если хранилище заводится этим же действием (каталог пакета уже
    лежит с verdict.json); у повтора исключения нет — иначе повторно
    присланный пакет на незаведённой площадке терял бы свой прогон и
    дату первого приёма."""
    from webapp import cosmos_runs
    if cosmos_runs.runs_root() is None:
        return None
    try:
        return cosmos_runs.attach_package(sha, pkg_path, source=source,
                                          brief=brief, manifest=manifest,
                                          by=source, adopt_exclude=primary)
    except Exception as e:                                # noqa: BLE001
        return {"run": None, "error_code": "run-not-written",
                "error": str(e)[:300]}


def _note_join(brief: dict, joined: dict, row_extra: dict) -> bool:
    """Итог присоединения — в квитанцию (cosmos_run; при сбое
    run_note_code и слова в detail) и в строку журнала (row_extra).
    Возвращает, стал ли пакет текущим через прогон."""
    if joined.get("run"):
        brief["cosmos_run"] = joined["run"]
        if joined.get("mirror_note"):
            brief["mirror_note"] = joined["mirror_note"]
            row_extra["mirror_note"] = joined["mirror_note"]
        return True
    brief["cosmos_run"] = None
    brief["run_note_code"] = joined.get("error_code") or "run-not-written"
    brief["current_switched"] = False
    brief["detail"] = ((brief.get("detail") or "")
                       + "; прогон не записан: "
                       + str(joined.get("error") or brief["run_note_code"])[:200]
                       ).strip("; ")
    row_extra["run_note"] = joined.get("error") or brief["run_note_code"]
    return False


# ------------------------------------------------------------ карточка

def card_state() -> dict:
    """Данные карточки «текущий ВМП» (GET /api/task-package/current):
    манифест, правила по зонам, критерий, верифицированная матрица
    готовности конвейера, источник получения и хвост журнала."""
    cur = current_package()
    if cur is None:
        return _absent_card()
    run = cur.get("run") if isinstance(cur.get("run"), dict) else None
    run_out = ({"run": run.get("run"), "title": run.get("title"),
                "implicit": bool(run.get("implicit"))} if run else None)
    if not cur.get("path") or not Path(cur["path"]).is_file():
        # Пакет прогона утерян на диске — причина кодом, не молчание.
        return ({"present": False, "note_code": "package-bytes-missing",
                 "run": run_out} if run_out else {"present": False})
    p = Path(cur["path"])
    from vmp import catalog_access as ca
    try:
        catalog_key = ca.current().fingerprint()
    except ca.CATALOG_ERRORS as exc:
        # Even unavailable catalogue state invalidates the old success. The
        # package reader supplies the addressed refusal (or archive boundary).
        catalog_key = (type(exc).__name__, str(exc))
    key = (str(p), p.stat().st_mtime_ns, catalog_key)
    if _CARD_CACHE["key"] != key:
        _CARD_CACHE["value"] = _card_body(p)
        _CARD_CACHE["key"] = key
    out = dict(_CARD_CACHE["value"])
    src = cur["source"]
    out["source"] = {"kind": src.get("kind"), "at": src.get("at"),
                     "name": p.name}
    # Прогон COSMOS, которому принадлежит пакет (07.09.2026); у пакета
    # «файлом руками» — неявный прогон по маркеру держателя либо null.
    out["run"] = run_out
    # МГ — уже верифицированная матрица конвейера (его кэш): то же
    # состояние, что на чипах блоков, а не второй пересчёт.
    from webapp import flow
    out["mg"] = flow.readiness_state()
    out["receipts"] = receipts_tail()
    return out


def _absent_card() -> dict:
    """Карточка без пакета: `{present: false}`. Указатель текущего прогона
    есть, а его запись не читается — причина кодом и номер прогона по
    образцу package-bytes-missing (ревью 07.09: оператор видел «пакета
    нет» без единого слова о причине). Пакет «файлом» и площадка без
    хранилища сюда не попадают: у них резолвер честно пуст."""
    if (os.environ.get("CREW_TASK_PACKAGE") or "").strip() or inbox_dir() is None:
        return {"present": False}
    from webapp import cosmos_runs
    if cosmos_runs.storage_state() != "ready":
        return {"present": False}
    run, doc, err = cosmos_runs.current_run_state()
    if run is not None and doc is None:
        return {"present": False, "note_code": "run-unreadable",
                "run": {"run": run, "title": None, "implicit": False},
                "error": err}
    return {"present": False}


def _card_body(p: Path) -> dict:
    """Тяжёлая часть карточки (под кэшем): sha байтов zip, манифест,
    счёт правил по зонам, критерий с версией ставок, карты правил."""
    raw = p.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    from webapp import task_package as wtp
    try:
        pkg = wtp.load(p, run_gate=False)
    except Exception as e:                            # noqa: BLE001
        # Пакет есть, но не читается — карточка честно называет
        # причину, а не прячет файл.
        return {"present": True, "sha256": sha, "error": str(e)}
    man = pkg.get("manifest") or {}
    model = pkg.get("model") or {}

    # Счёт правил по зонам задачи (перечень зон — зеркало
    # vmp.readiness_verify.ZONES_OF_TASK; всё вне перечня — OUT).
    zones = {"PAIRING": 0, "ROSTERING": 0, "BOTH": 0, "OUT": 0}
    for r in model.get("rules") or []:
        if not isinstance(r, dict):
            continue
        z = r.get("zone")
        zones[z if z in zones else "OUT"] += 1

    # Same three-carrier reader as the model/producer: document, answer, prose.
    # An answer-only reader falsely showed no criterion for document carriers.
    criterion = None
    try:
        from cdpm.model import derive_pairing_criterion
        crit = derive_pairing_criterion(model)
    except Exception:                                 # noqa: BLE001
        crit = None
    if isinstance(crit, dict):
        criterion = {"label": crit.get("label"),
                     "source_rule": crit.get("source_rule"),
                     "rates": crit.get("rates"),
                     "rates_version": crit.get("rates_version")}

    # Карты правил (манифест 1.14+): имена подсекций и счёт записей;
    # нет секции — честный null.
    maps = man.get("rule_maps")
    rule_maps = None
    if isinstance(maps, dict) and maps:
        rule_maps = {k: (len(v) if isinstance(v, (dict, list)) else 1)
                     for k, v in maps.items()}

    # Покрытие клауз (замки 4П, манифест 2026-09-02): сколько клауз
    # заморозки покрыто строками ВМП, сколько признано «не правило»,
    # сколько осталось без вердикта. Пакет, собранный без комплекта
    # заморозки (лабораторный), поля не несёт — честный null.
    cov = man.get("coverage")
    coverage = None
    if isinstance(cov, dict):
        coverage = {k: cov.get(k) for k in
                    ("clauses", "covered_by_rows", "not_rule", "missing")}
    # Правила, исключённые до получения данных по решению авиакомпании
    # (структура ВМП): карточка называет их, а не молчит.
    excluded = man.get("excluded_until_data")
    return {
        "present": True,
        "sha256": sha,
        "manifest": {
            "spec_version": man.get("spec_version"),
            "customer": man.get("customer"),
            "revision": man.get("revision"),
            "source_run": man.get("source_run"),
            "has_data": bool(man.get("has_data")),
            "allowed_modes": man.get("allowed_modes"),
            "crew_size": man.get("crew_size"),
            "coverage": coverage,
            "excluded_until_data": (list(excluded)
                                    if isinstance(excluded, list) else None),
            "files": [{"name": f.get("name"), "bytes": f.get("bytes")}
                      for f in man.get("files") or []
                      if isinstance(f, dict)],
        },
        "rules_by_zone": zones,
        "criterion": criterion,
        "objective_projection": pkg.get("objective_projection"),
        "rule_maps": rule_maps,
    }
