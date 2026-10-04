# -*- coding: utf-8 -*-
"""«Выпустить версию» — только владелец, двенадцать предпроверок, снимок.

Выпуск отличается от сборки одним: он объявляет, что ЭТИ данные теперь
имеют имя. Поэтому у него нет суффикса черновика, он записан в журнал
версий, у него есть неизменяемая копия в `catalog/releases/<версия>/` и
срез кода, в котором лежит его тело.

Смысл поля `code`: дерево `catalog/` и `template_catalog/` обязано быть
чистым, значит HEAD уже несёт собранный черновик с тем же отпечатком —
`code` и есть тот срез. Коммит самого выпуска (шапка, журнал, папка
выпуска) идёт следующим; это записано в контракте и в руководстве 13.9.

Двенадцатая предпроверка (пакет «паритет на временном каталоге», решение
0.13) — «паритет с кодом COSMOS»: служба зовёт закон паритета
`catalog/tools/parity.py` ПОДПРОЦЕССОМ и по его вердикту отказывает
(`parity-red`) либо требует осознанного подтверждения (`confirm_parity`).
Подпроцессом, а не импортом: закон паритета читает таблицы решателя
(`vmp.kotlin_tables`) и код CDPM, а служба не импортирует продуктов —
это шов, а не удобство.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import template_catalog as tc
from template_catalog import diff as tdiff
from template_catalog import writer
from template_catalog.manifest import parse_version

from . import assemble as asm
from . import executors, identity, markers
from .errors import ServiceError, scrub

_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
#: Пути, чистота которых требуется перед выпуском: тело выпуска и
#: читатель, которым оно записано.
GIT_PATHS = ("template_catalog",)


def repo_of(catalog_dir) -> Path:
    """Корень дерева git, в котором лежит папка каталога.

    Спрашиваем сам git (`rev-parse --show-toplevel`), а не берём родителя
    папки: каталог вправе лежать не прямо под корнем (`<репозиторий>/data/
    catalog`), и тогда родитель — не корень. `git rev-parse HEAD` из
    подпапки всё равно дал бы 40 hex, а `git status -- template_catalog`
    из неё не нашёл бы читателя — и выпуск прошёл бы при грязном
    читателе. Не репозиторий вовсе — родитель, как раньше: тогда `HEAD`
    не даст 40 hex и сработает честный отказ `no-git`."""
    folder = Path(catalog_dir).resolve()
    cwd = folder if folder.is_dir() else folder.parent
    try:
        res = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=str(cwd), capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return folder.parent
    out = (res.stdout or "").strip()
    return Path(out) if res.returncode == 0 and out else folder.parent


def catalog_pathspec(catalog_dir, repo: Path | None = None) -> str:
    """Папка каталога относительно корня дерева — в таком виде её ждёт
    `git status -- <путь>`."""
    repo = repo if repo is not None else repo_of(catalog_dir)
    try:
        rel = Path(os.path.relpath(Path(catalog_dir).resolve(), repo)).as_posix()
    except (ValueError, OSError):
        return Path(catalog_dir).name
    return Path(catalog_dir).name if rel.startswith("..") else rel


def unchecked_paths(catalog_dir) -> list[str]:
    """Пути `GIT_PATHS`, которых в этом дереве нет: их чистота НЕ
    проверена, и молчать об этом нельзя — предпроверка 6 обещает чистое
    дерево каталога И читателя."""
    repo = repo_of(catalog_dir)
    return [p for p in GIT_PATHS if not (repo / p).is_dir()]


def _git(repo: Path, *args: str) -> tuple[int, str, str]:
    """Вывод отдаётся КАК ЕСТЬ: у `git status --porcelain` первые два
    столбца значащие, и « M файл» после общего strip() потерял бы первую
    букву имени. Обрезает пробелы тот, кому это нужно."""
    try:
        res = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", f"{type(e).__name__}: {e}"
    return res.returncode, res.stdout or "", (res.stderr or "").strip()


def head_commit(catalog_dir) -> str | None:
    """HEAD как 40 hex; не репозиторий или пустой — None."""
    code, out, _err = _git(repo_of(catalog_dir), "rev-parse", "HEAD")
    out = out.strip()
    return out if code == 0 and _SHA1_RE.match(out) else None


def dirty_files(catalog_dir) -> list[str]:
    """Незакоммиченные файлы каталога и читателя — именами.

    Строка `git status --porcelain` — это `XY имя`: два столбца
    состояния, пробел, имя с четвёртого знака. Ни одного знака имени не
    теряется — владелец по этому списку решает, что коммитить."""
    repo = repo_of(catalog_dir)
    paths = [catalog_pathspec(catalog_dir, repo)]
    paths += [p for p in GIT_PATHS if (repo / p).is_dir()]
    # core.quotepath=false: имя с кириллицей git иначе отдаёт восьмеричными
    # escape-последовательностями («"\321\201-git/…"»), и владелец увидел
    # бы вместо имени файла шифр.
    code, out, _err = _git(repo, "-c", "core.quotepath=false",
                           "status", "--porcelain", "--", *paths)
    if code != 0:
        return []
    return [line[3:].strip() for line in out.splitlines()
            if len(line) > 3 and line.strip()]


# ─────────────────────────────────────────── гейт папок выпусков

def releases_gate(cat) -> list[dict]:
    """«Каталог — сам себе источник правды»: у каждой записи журнала есть
    читаемый неизменный снимок с тем же номером, каноном, полной записью
    журнала и фактическими правками. Сегодняшняя семантика применяется
    только к совпадающей форме записи; форма прошлого не отменяет
    проверки целостности. Это не вызов release() и не разрешение выпуска.
    """
    from template_catalog.history import release_history
    return release_history(cat)


def releases_ok(cat) -> bool:
    return all(r["ok"] for r in releases_gate(cat))


# ────────────────────────────────────────────────────────── выпуск

def check_version(cat, version: str) -> None:
    """Пятая предпроверка: номер выпуска не ниже подсказки, не ниже
    тройки текущего черновика и строго больше последней записи журнала."""
    try:
        triple, n = parse_version(str(version))
    except ValueError as e:
        raise ServiceError("bad-form", str(e), 400) from None
    if n is not None:
        raise ServiceError(
            "version-too-low",
            f"выпуск не бывает черновиком: {version} несёт -draft.{n}", 422)
    cur = parse_version(cat.version())[0]
    if triple < cur:
        raise ServiceError(
            "version-too-low",
            f"черновик назван {cat.version()} — выпуск не может быть младше "
            f"{'%d.%d.%d' % cur}", 422)
    versions = cat.versions()
    if versions:
        last = str(versions[-1].get("version"))
        if triple <= parse_version(last)[0]:
            raise ServiceError(
                "version-too-low",
                f"{version} не больше последней записи журнала {last}", 422)
    hint = asm.suggest_release(cat)
    floor = parse_version(str(hint["version"]))[0]
    if triple < floor:
        raise ServiceError(
            "version-too-low",
            f"{version} ниже подсказки {hint['level']} → {hint['version']}",
            422)


#: Двенадцать предпроверок выпуска (восемь §2.6 проекта шага 5, три §4
#: проекта шага 6 и двенадцатая — паритет с кодом COSMOS, 0.13 проекта
#: паритета): имя, короткое название и место в чек-листе экрана. Порядок
#: закреплён — по нему выпуск и отказывает, и рассказывает.
CHECK_TITLES = (
    ("role", "режим правки и роль владельца"),
    ("drafts", "черновиков нет"),
    ("markers", "перечень маркеров заказчика прочитан"),
    ("validate", "проверка каталога без ошибок"),
    ("version", "номер выпуска не ниже подсказки"),
    ("git", "репозиторий и чистое дерево каталога"),
    ("bindings", "дверь продуктов не остановится (B01)"),
    ("folder", "папки выпуска ещё нет"),
    ("candidates", "кандидатов конвейера не осталось"),
    ("executors", "у записей из заявок исполнители названы"),
    ("in_work", "заявки в работе (предупреждение)"),
    ("parity", "паритет с кодом COSMOS"),
)
#: Строка «заявлен» файла привязок засчитывается предпроверке 10 только
#: с ключом CR по образцу: «заявлен» без ключа — обещание без адресата.
_CR_KEY_RE = re.compile(r"^cr[0-9a-f]{8}$")


def _check_role(settings) -> ServiceError | None:
    return None if identity.can(settings, "release") \
        else identity.refuse(settings, "release")


def _check_drafts(store) -> ServiceError | None:
    counts = store.draft_counts()
    if not sum(counts.values()):
        return None
    return ServiceError(
        "drafts-pending",
        f"сначала соберите или отмените черновики: записей "
        f"{counts['families'] + counts['criteria']}, словарей "
        f"{counts['dictionaries']}", 409)


def _check_markers(settings, terms) -> ServiceError | None:
    if terms:
        return None
    return ServiceError("markers-missing",
                        markers.release_refusal_words(settings), 412)


def _check_validate(cat, terms) -> ServiceError | None:
    problems = cat.validate(forbidden_terms=terms)
    errors = [p for p in problems if p.severity == "error"]
    if not errors:
        return None
    return ServiceError(
        "invalid-catalog",
        f"каталог не проходит проверку: ошибок {len(errors)} — "
        f"выпуск не делается", 422, problems=problems)


def _check_version(cat, version) -> ServiceError | None:
    try:
        check_version(cat, version)
    except ServiceError as e:
        return e
    return None


def _unchecked_words(settings) -> str:
    """Чего в этом дереве нет — словами, а не молчанием."""
    missing = unchecked_paths(settings.catalog_dir)
    if not missing:
        return ""
    return (f"; {', '.join(missing)} в этом дереве нет — чистота этих папок "
            f"не проверялась")


def _check_git(settings) -> ServiceError | None:
    if head_commit(settings.catalog_dir) is None:
        return ServiceError(
            "no-git",
            "выпуск требует репозитория git: поле code журнала — срез "
            "кода, в котором лежит тело выпуска", 412)
    dirty = dirty_files(settings.catalog_dir)
    if dirty:
        return ServiceError(
            "git-dirty",
            "выпуск требует чистого дерева каталога и читателя: "
            "незакоммичены " + ", ".join(dirty[:8])
            + (f" (и ещё {len(dirty) - 8})" if len(dirty) > 8 else "")
            + " — сначала закоммитьте собранный черновик"
            + _unchecked_words(settings), 412,
            extra={"dirty": dirty,
                   "unchecked": unchecked_paths(settings.catalog_dir)})
    return None


def release_folder(catalog_dir, version) -> Path | None:
    """Путь папки выпуска — только для номера по образцу semver: строка
    пользователя в путь как есть не подставляется (иначе `..` назвался бы
    «версией»). Номер не по образцу — None, и об этом говорит проверка
    номера, а не проверка папки."""
    try:
        parse_version(str(version))
    except ValueError:
        return None
    return Path(catalog_dir) / "releases" / str(version)


def _check_folder(settings, version) -> ServiceError | None:
    folder = release_folder(settings.catalog_dir, version)
    if folder is None:
        return None                     # номер не по образцу — скажет №5
    if folder.exists():
        return ServiceError(
            "release-exists",
            f"папка releases/{version} уже есть: выпуски неизменяемы", 409)
    return None


# ───────────────────────── предпроверки 9–11 (конвейер пополнения)

def _check_candidates(store, cat) -> ServiceError | None:
    """9: файл-кандидат конвейера не остался в `catalog/candidates/`.

    Кандидат — черновик ветки CR, а не тело каталога: у записи, которая
    уже в выпуске, его быть не может. Оставленный файл значит одно из
    двух — его не импортировали через `adopt` либо сборка его не убрала;
    выпуск с ним объявил бы версией то, что ещё не решено."""
    from . import request_queue as rq
    body = cat.body()
    known = {("families", str(r.get("id"))) for r in body["families"]}
    known |= {("criteria", str(r.get("id"))) for r in body["criteria"]}
    left = [row for row in rq.pending_candidates(store.settings, cat)
            if tuple(str(row["record"]).split("/", 1)) in known]
    if not left:
        return None
    first = left[0]
    return ServiceError(
        "candidate-pending",
        f"кандидат {first['record']} из CR {first['cr_key'] or '—'} не "
        f"импортирован или не удалён ({first['file']}): импортируйте через "
        f"adopt либо удалите файл; удаление — в коммит"
        + (f"; всего таких файлов {len(left)}" if len(left) > 1 else ""),
        409, extra={"candidates": left})


def _record_of(cat, section: str, record_id: str):
    from template_catalog.errors import UnknownRecord
    try:
        return cat.family(record_id) if section == "families" \
            else cat.criterion(record_id)
    except UnknownRecord:
        return None


def _kind_row_words(kind: str, by_product: dict) -> str | None:
    """Чего не хватает виду записи — словами; None, если всё сошлось.

    Засчитываются три честных исхода: «есть» (исполнитель написан),
    «заявлен» с ключом CR по образцу (работа названа и адресована) и
    «нет» с заметкой (сказано, почему исполнителя не будет). Молчание —
    четвёртый исход, и он не засчитывается."""
    states = []
    for row in (by_product or {}).values():
        state = str((row or {}).get("state") or "")
        states.append(state or "—")
        if state == "есть":
            return None
        if state == "заявлен" and _CR_KEY_RE.match(
                str((row or {}).get("cr_key") or "")):
            return None
        if state == "нет" and str((row or {}).get("note") or "").strip():
            return None
    if not states:
        return f"{kind}: строки нет ни у одного продукта"
    return f"{kind}: {', '.join(sorted(set(states)))}"


def _check_executors(store, cat) -> ServiceError | None:
    """10: у записи, рождённой заявкой «есть», исполнители названы.

    Сторож версий (`bindings-stop`, проверка 7) судит СВЕЖЕСТЬ файлов
    привязок; здесь судится ПОЛНОТА исполнителей у новых записей — это
    другой вопрос, и у него свой код (`executors-incomplete`), иначе
    один код значил бы две разные беды.

    Вопрос здесь ДРУГОЙ, чем у свидетельства поддержки, и потому закон
    другой — это проверено заново вместе с правкой K06. Свидетельство
    отвечает «исполнимо ли» и засчитывает одно: «есть» со стендом по
    виду. Эта предпроверка отвечает «названы ли исполнители» и
    засчитывает три ЧЕСТНЫХ исхода — «есть», «заявлен» с ключом CR и
    «нет» с заметкой: запись, у которой исполнителя не будет и это
    сказано словами, выпускать законно, а молчать о ней нельзя. Свести
    их в один закон значило бы либо запретить выпуск всякой записи без
    зелёного стенда (барьер, которого контракт не просит: П0 §3.5 —
    «поздний барьер сохраняется», не «усиливается»), либо ослабить
    свидетельство.

    Годность файлов привязок, из которых считается клетка, здесь не
    судится намеренно: её судит предпроверка 7 того же выпуска
    (`bindings_stop(..., check_fingerprint=True)`) — файл иной версии
    или иного отпечатка останавливает выпуск раньше и своим кодом. Два
    кода на одну беду читались бы как две разные беды.

    Акт выпуска пересчитывает `catalog/support.json` заново и заполняет
    в нём `approved` (сторона COSMOS §4.2) — стареть файлу между
    выпусками нечем."""
    settings = store.settings
    bad: list[dict] = []
    for rqst in cat.requests():
        if rqst.get("state") != "есть":
            continue
        section = str(rqst.get("section"))
        record_id = str(rqst.get("record_id"))
        record = _record_of(cat, section, record_id)
        if record is None:                              # R02 скажет своё
            continue
        kinds_field = "executor_kinds" if section == "families" \
            else "computable_by"
        cells = executors.record_cells(cat, settings, section, record_id)
        for kind in [str(k) for k in (record.get(kinds_field) or [])]:
            words = _kind_row_words(kind, cells.get(kind) or {})
            if words:
                bad.append({"record": f"{section}/{record_id}",
                            "words": words})
        stand = executors.record_stand(cat, settings, section, record_id)
        if stand.get("failed"):
            bad.append({"record": f"{section}/{record_id}",
                        "words": f"стенд: провалов {stand['failed']}"})
    if not bad:
        return None
    return ServiceError(
        "executors-incomplete",
        "у записей из принятых заявок исполнители названы не полностью: "
        + "; ".join(f"{r['record']} — {r['words']}" for r in bad[:5])
        + (f" (и ещё {len(bad) - 5})" if len(bad) > 5 else ""),
        412, extra={"rows": bad})


# ───────────────────────── предпроверка 12 (паритет с кодом COSMOS)

#: Закон паритета внутри папки каталога: `<каталог>/tools/parity.py`.
#: Инструмент лежит рядом с данными, которые судит, и потому едет вместе
#: с любой копией каталога — стенду службы не нужно знать дерево COSMOS.
PARITY_TOOL = ("tools", "parity.py")
#: Предел ожидания вердикта, секунд (0.13 проекта: типично 5–10 с в
#: клоне с импортами, 1,2 с в дереве). Замок записи держится всё это
#: время — дольше двух минут ждать выпуску нечего.
PARITY_TIMEOUT_S = 120.0


def parity_tool(catalog_dir) -> Path:
    """Путь закона паритета для этой папки каталога."""
    return Path(catalog_dir).joinpath(*PARITY_TOOL)


def parity_python(repo: Path) -> str:
    """Интерпретатор подпроцесса: `.venv` рядом с деревом каталога, если
    он есть, иначе тот, которым запущена служба.

    Риск 5 §8 проекта: `sys.executable` службы не обязан быть venv
    COSMOS (служба вправе стоять отдельно), а движок паритета зовёт
    `vmp.kotlin_tables` и код CDPM. Найденный рядом venv — та же среда,
    в которой живут продукты дерева."""
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        p = repo.joinpath(".venv", *rel)
        if p.is_file():
            return str(p)
    return sys.executable


def _unjudged(reason: str, tool: Path, rc=None) -> dict:
    return {"judged": False, "ok": None, "rc": rc, "words": reason,
            "warnings": [], "verdict": None, "tool": tool.name}


def parity_verdict(settings, *, timeout_s: float = PARITY_TIMEOUT_S) -> dict:
    """Вердикт закона паритета о ЖИВОМ каталоге службы — подпроцессом.

    Договор команды (`catalog/tools/PARITY-CONTRACT.md`): коды 0 — красных
    нет, 1 — есть красные, 2 — не судилось; `--json` пишет вердикт файлом
    (`ok`, `words`, `counts`, `rules`, `catalog`). Переменную
    `CREW_TEMPLATE_CATALOG` подпроцессу отдаёт ЗОВУЩИЙ: движок её не
    ставит, а правило L10 сверяет по ней двери продуктов с судимой
    папкой — без переменной оно ответило бы «двери читают другой
    каталог» не по делу.

    Возврат — запись из семи полей: `judged` (был ли вердикт),
    `ok` (`True` зелёный | `False` красный | `None` не судился),
    `rc`, `words` (для чек-листа и журнала), `warnings` (слова
    `skipped`, требующих подтверждения), `verdict` (тело `--json` как
    есть) и `tool`. Ни одного байта на диск каталога: `--json` пишется
    во временную папку и уходит вместе с ней."""
    catalog_dir = Path(settings.catalog_dir)
    tool = parity_tool(catalog_dir)
    if not tool.is_file():
        return _unjudged(
            f"закона паритета {'/'.join(PARITY_TOOL)} рядом с каталогом нет — "
            f"судить нечем", tool)
    repo = repo_of(catalog_dir)
    bindings = dict(executors.product_paths(settings)).get("COSMOS")
    env = dict(os.environ)
    env["CREW_TEMPLATE_CATALOG"] = str(catalog_dir)
    env["PYTHONUTF8"] = "1"
    # Settings wins over ambient env, exactly as in the service's A02 gate.
    # Resolve before changing cwd: a relative explicit path must name the same
    # policy in the child, and absence/invalidity must not become zero markers.
    selected_markers = markers.markers_path(settings)
    if selected_markers is not None:
        env["CATALOG_MARKERS_FILE"] = str(Path(selected_markers).resolve())
    else:
        env.pop("CATALOG_MARKERS_FILE", None)
    with tempfile.TemporaryDirectory(prefix="catalog-parity-") as tmp:
        out = Path(tmp) / "parity.json"
        cmd = [parity_python(repo), str(tool), "--catalog", str(catalog_dir)]
        if bindings is not None and Path(bindings).is_file():
            cmd += ["--bindings", str(bindings)]
        cmd += ["--json", str(out), "--quiet"]
        try:
            res = subprocess.run(cmd, cwd=str(repo), capture_output=True,
                                 text=True, encoding="utf-8",
                                 errors="replace", env=env, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return _unjudged(f"закон паритета не уложился в предел "
                             f"{timeout_s:g} с", tool)
        except (OSError, subprocess.SubprocessError) as e:
            return _unjudged(f"закон паритета не запустился: "
                             f"{type(e).__name__}: {e}", tool)
        doc = None
        if out.is_file():
            try:
                doc = json.loads(out.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = None
    return _read_verdict(res, doc if isinstance(doc, dict) else None, tool,
                         repo)


def _read_verdict(res, doc: dict | None, tool: Path, repo: Path) -> dict:
    """Вердикт по коду возврата и телу `--json`.

    Судит КОД возврата, а не только тело: вердикт, который не прочитался,
    не делает выпуск разрешённым — но и код 1 не делает его «не
    судившимся». Слова красного берутся из вердикта, а хвост вывода —
    только когда вердикта нет: пересказ конвейером был бы вторым
    законом (риск 6 §8 проекта)."""
    rc = res.returncode
    tail = _tail(res, tool, tool.parent.parent, repo)
    if rc not in (0, 1):
        return _unjudged(
            f"закон паритета вернул код {rc} — не судилось"
            + (f": {tail}" if tail else ""), tool, rc=rc)
    if rc == 1:
        words = str((doc or {}).get("words") or "") or tail \
            or "красные правила названы вердиктом"
        return {"judged": True, "ok": False, "rc": rc, "words": words,
                "warnings": _parity_warnings(doc), "verdict": doc,
                "tool": tool.name}
    if doc is None:
        return _unjudged("закон паритета вернул 0, но вердикта "
                         "(--json) не оставил", tool, rc=rc)
    return {"judged": True, "ok": True, "rc": rc,
            "words": _green_words(doc), "warnings": _parity_warnings(doc),
            "verdict": doc, "tool": tool.name}


#: Абсолютный путь машины в свободном тексте (диск, UNC, POSIX-корень).
_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?<![\w.])/)[^\s\"',;]{3,}")


def _tail(res, *paths, limit: int = 400) -> str:
    """Хвост вывода судьи — для случая «вердикта нет», БЕЗ путей машины.

    Раскладка машины владельца — не дело витрины (правило `errors.scrub`):
    известные пути заменяются именами, а всё, что похоже на абсолютный
    путь и уцелело (кадры чужих модулей в следе), — словом `<путь>`.
    Полный текст остаётся в выводе процесса службы."""
    text = " ".join((res.stderr or "").split()) or \
        " ".join((res.stdout or "").split())
    text = _ABS_PATH_RE.sub("<путь>", scrub(text, *paths))
    return text[-limit:]


def _parity_warnings(doc: dict | None) -> list[str]:
    """Слова правил `skipped` с признаком предупреждения (сегодня L08b
    «CDPM ещё не знает метрику …»). Молчаливо пропустить их нельзя:
    выпуск объявляет версией то, чего судья не проверил."""
    rules = (doc or {}).get("rules")
    if not isinstance(rules, dict):
        return []
    out = []
    for rid, rule in rules.items():
        if isinstance(rule, dict) and rule.get("warning"):
            out.append(f"{rid}: {rule.get('skipped') or rule.get('words') or ''}"
                       .strip().rstrip(":").strip())
    return out


def _green_words(doc: dict) -> str:
    """Слова зелёного вердикта — числами судьи, а не пересказом."""
    cat = doc.get("catalog") if isinstance(doc.get("catalog"), dict) else {}
    counts = doc.get("counts") if isinstance(doc.get("counts"), dict) else {}
    what = ", ".join(f"{k} {counts[k]}" for k in
                     ("rules", "green", "red", "skipped", "warning")
                     if k in counts)
    return (f"паритет с кодом COSMOS зелёный: {cat.get('version') or '—'} "
            f"{str(cat.get('fingerprint') or '')[:12]}…"
            + (f" | {what}" if what else ""))


def parity_not_called(settings) -> dict | None:
    """Запись «предпроверка 12 не зовётся» — либо None, если зовётся.

    Площадка (`--read-only`) и роль без права выпуска выпусков не
    делают, и проект §4 прямо говорит: там предпроверка 12 не зовётся.
    Прежде `checks()` считал вердикт БЕЗУСЛОВНО, и каждое чтение
    чек-листа (диалог «Выпустить версию» просит его до нажатия)
    поднимало подпроцесс `.venv` и ждало до 120 с — в том числе там, где
    рядом может не быть кода COSMOS. Строка чек-листа при этом не
    молчит: она говорит, почему судьи не было."""
    if identity.can(settings, "release"):
        return None
    why = ("режим только чтение — выпусков площадка не делает"
           if not settings.is_edit()
           else f"право выпуска у роли {settings.actor} отсутствует")
    return {"judged": False, "ok": None, "rc": None, "not_called": True,
            "words": f"паритет с кодом COSMOS не судится: {why}",
            "warnings": [], "verdict": None,
            "tool": parity_tool(settings.catalog_dir).name}


def parity_confirm_needed(verdict: dict) -> bool:
    """Нужно ли осознанное подтверждение владельца: судья не судил либо
    судил с предупреждением. Красный подтверждением НЕ снимается.

    «Не зовётся» подтверждения не требует: выпуска там не будет вовсе —
    его остановит первая же строка чек-листа (право роли)."""
    if verdict.get("not_called"):
        return False
    return bool(verdict.get("ok") is None or verdict.get("warnings"))


def _check_parity(verdict: dict, *, confirm: bool = False
                  ) -> ServiceError | None:
    """12: паритет каталога с кодом COSMOS.

    Красный — отказ без вариантов: «стоп защищает выпуски», а версия,
    противоречащая таблицам решателя, не имеет права называться версией.
    «Не судился» и предупреждение — не молчаливый проход: они снимаются
    полем `confirm_parity: true`, как стоп сторожа снимается
    `confirm_stop`."""
    extra = {"parity": {"ok": verdict.get("ok"), "rc": verdict.get("rc"),
                        "warnings": list(verdict.get("warnings") or ())}}
    if verdict.get("not_called"):
        return None
    if verdict.get("ok") is False:
        return ServiceError(
            "parity-red",
            f"паритет каталога с кодом COSMOS красен: {verdict['words']}; "
            f"версию, противоречащую таблицам решателя, выпускать нельзя — "
            f"подтверждением это не снимается", 412, extra=extra)
    if not parity_confirm_needed(verdict) or confirm:
        return None
    if verdict.get("ok") is None:
        why = f"паритет с кодом COSMOS не судился: {verdict['words']}"
    else:
        why = ("паритет с кодом COSMOS зелёный, но судья кое-чего не судил: "
               + "; ".join(verdict.get("warnings") or ()))
    return ServiceError(
        "parity-unconfirmed",
        f"{why}; продолжить осознанно — полем confirm_parity: true", 412,
        extra=extra)


def _requests_in_work(cat) -> list[str]:
    """11: заявки «исполнитель заявлен» — предупреждение, не отказ.

    Выпуск их не трогает: работа по CR идёт своим чередом, и версия
    выпускается по тому, что уже стало записями. Но владелец обязан
    видеть, сколько заявок останется в работе после выпуска."""
    return [str(rqst.get("request_id"))
            for rqst in cat.requests()
            if rqst.get("state") == "исполнитель заявлен"]


def _check_words(key: str, cat, settings, version, head, parity=None) -> str:
    """Слова зелёной проверки — то же, что показал бы отказ, только с
    другой стороны: владелец обязан видеть, ЧТО именно сошлось."""
    if key == "role":
        return f"рабочее место: {settings.actor}"
    if key == "drafts":
        return "черновиков нет"
    if key == "markers":
        return "перечень маркеров заказчика прочитан"
    if key == "validate":
        return "ошибок проверки нет"
    if key == "version":
        hint = asm.suggest_release(cat)
        return (f"{version}: подсказка {hint['level']} → {hint['version']}, "
                f"изменений {hint['changes']}")
    if key == "git":
        return (f"дерево каталога и читателя чисто, HEAD "
                f"{(head or '')[:7]}{_unchecked_words(settings)}")
    if key == "bindings":
        return "привязки продуктов дверь не остановят"
    if key == "candidates":
        return "кандидатов конвейера в catalog/candidates/ не осталось"
    if key == "executors":
        return "у записей из заявок каждый объявленный вид назван"
    if key == "in_work":
        left = _requests_in_work(cat)
        return (f"заявок в работе: {len(left)}"
                + (f" ({', '.join(left[:5])})" if left else
                   " — выпуск ничего не оставляет незакрытым"))
    if key == "parity":
        return str((parity or {}).get("words")
                   or "паритет с кодом COSMOS зелёный")
    return f"папки releases/{version} нет"


def checks(store, version: str | None = None) -> dict:
    """Все двенадцать предпроверок словами — БЕЗ единого байта на диск.

    Диалог «Выпустить версию» обязан показать владельцу чек-лист ДО
    нажатия (§4.5 проекта), а посчитать маркеры, чистоту дерева git,
    остановку сторожа и вердикт паритета экран не может — это знание
    службы. Закон один: и здесь, и в `release()` работают те же функции
    проверок, и паритет судится тем же подпроцессом."""
    settings = store.settings
    cat = store.catalog()
    hint = asm.suggest_release(cat)
    version = str(version or hint["version"]).strip() or str(hint["version"])
    terms = markers.terms(settings)
    head = head_commit(settings.catalog_dir)
    # Судья паритета — подпроцесс до 120 с: он поднимается только там,
    # где выпуск возможен. Площадка `--read-only` и роль без права
    # выпуска получают строку чек-листа СЛОВАМИ, а не ожидание.
    parity = parity_not_called(settings) or parity_verdict(settings)
    results = {
        "role": _check_role(settings),
        "drafts": _check_drafts(store),
        "markers": _check_markers(settings, terms),
        "validate": _check_validate(cat, terms),
        "version": _check_version(cat, version),
        "git": _check_git(settings),
        "bindings": executors.bindings_stop(cat, settings, version,
                                            check_fingerprint=True),
        "folder": _check_folder(settings, version),
        "candidates": _check_candidates(store, cat),
        "executors": _check_executors(store, cat),
        # Одиннадцатая строка — предупреждение по построению: она
        # никогда не красная, но всегда называет число заявок в работе.
        "in_work": None,
        "parity": _check_parity(parity),
    }
    rows = []
    for n, (key, title) in enumerate(CHECK_TITLES, start=1):
        bad = results[key]
        rows.append({
            "n": n, "key": key, "title": title, "ok": bad is None,
            "code": None if bad is None else bad.code,
            "words": _check_words(key, cat, settings, version, head, parity)
            if bad is None else bad.words,
            "extra": {} if bad is None else bad.extra,
        })
    in_work = _requests_in_work(cat)
    return {"version": version, "suggested": hint,
            "ok": all(r["ok"] for r in rows), "checks": rows,
            "requests_in_work": in_work,
            "warnings": ([f"заявок в работе: {len(in_work)} — выпуск их не "
                          f"трогает"] if in_work else [])
            + list(parity.get("warnings") or ()),
            "confirm_stop_needed": results["bindings"] is not None,
            # Красный паритет подтверждением не снимается — признак
            # говорит ровно о том, что снимается полем `confirm_parity`.
            "confirm_parity_needed": (parity.get("ok") is not False
                                      and parity_confirm_needed(parity)),
            "parity": {"ok": parity.get("ok"), "rc": parity.get("rc"),
                       "words": parity.get("words"),
                       "warnings": list(parity.get("warnings") or ())}}


def release(store, journal, *, version: str, note: str,
            confirm_stop: bool = False, confirm_parity: bool = False) -> dict:
    """Выпустить версию. Любая красная предпроверка — ничего не записано.

    `confirm_parity` — осознанное продолжение при вердикте «не судился»
    либо при предупреждении судьи паритета (0.13). КРАСНЫЙ паритет им не
    снимается: версия, противоречащая таблицам решателя, не выпускается."""
    settings = store.settings
    identity.require(settings, "release")               # 1
    note = str(note or "").strip()
    if not note:
        raise ServiceError("bad-form",
                           "заметка выпуска обязательна: чем эта версия "
                           "отличается от предыдущей", 400)
    with store.writing():
        cat = store.catalog()
        bad = _check_drafts(store)                      # 2
        if bad is not None:
            raise bad
        terms = markers.terms(settings)                 # 3
        bad = _check_markers(settings, terms)
        if bad is not None:
            raise bad
        bad = _check_validate(cat, terms)               # 4
        if bad is not None:
            raise bad
        check_version(cat, version)                     # 5
        bad = _check_git(settings)                      # 6
        if bad is not None:
            raise bad
        head = head_commit(settings.catalog_dir)
        stop = executors.bindings_stop(cat, settings, str(version),  # 7
                                       check_fingerprint=True)
        if stop is not None and not confirm_stop:
            raise stop
        bad = _check_folder(settings, version)          # 8
        if bad is not None:
            raise bad
        bad = _check_candidates(store, cat)             # 9
        if bad is not None:
            raise bad
        bad = _check_executors(store, cat)              # 10
        if bad is not None:
            raise bad
        in_work = _requests_in_work(cat)                # 11 — предупреждение
        # 12: паритет с кодом COSMOS — ПЕРЕД записью. Замок записи
        # держится на время подпроцесса (0.13: типично 5–10 с, предел
        # 120 с): судить каталог, который в эту секунду переписывают,
        # значило бы судить не то, что выпускается.
        parity = parity_verdict(settings)
        bad = _check_parity(parity, confirm=confirm_parity)
        if bad is not None:
            journal.append(actor=settings.actor, op="release",
                           target=f"catalog/{version}", outcome="refused",
                           words=bad.words)
            raise bad

        base_body, base = asm.base_of(cat)
        changes = tdiff.diff_bodies(base_body, cat.body())
        body = cat.body()
        today = date.today().isoformat()
        entry = {"version": str(version), "fingerprint": cat.fingerprint(),
                 "released_at": today,
                 "released_by": identity.ACTORS["owner"], "code": head,
                 "changes": _journal_changes(changes, first=base is None)}
        header = writer.build_header(
            cat.header, version=str(version),
            released_by=identity.ACTORS["owner"], released_at=today,
            note=note, body=body,
            legacy_registry_rev=writer.legacy_registry_rev(cat.families()))
        try:
            written = writer.write_catalog(
                settings.catalog_dir, header=header,
                families=body["families"], criteria=body["criteria"],
                dictionaries=body["dictionaries"], requests=cat.requests(),
                versions=cat.versions() + [entry],
                # Порядок семей выпуск не меняет, но переходный носитель
                # мог и не существовать (каталог без transition/): писать
                # ПУСТОЙ порядок нельзя — дверь COSMOS требует равенства
                # множеств, и пустота остановила бы её на ровном месте.
                families_order=writer.refresh_families_order(
                    store.families_order(cat),
                    [str(r.get("id")) for r in body["families"]
                     if r.get("status") == "есть"]),
                forbidden_terms=terms)
            snapshot = writer.snapshot_release(settings.catalog_dir,
                                               str(version))
            # Свидетельство поддержки — актом выпуска (сторона COSMOS
            # §4.2): таблица считается ЗАНОВО по телу, которое только что
            # записано, `approved` заполняется у подтверждённых ячеек, и
            # неизменяемая копия ложится рядом с телом версии. Порядок
            # намеренный — после записи каталога: свидетельство обязано
            # нести отпечаток ВЫПУЩЕННОГО каталога, а не черновика.
            support = executors.support_table(
                written, settings, approved_by=identity.ACTORS["owner"],
                approved_how=f"выпуск {version}, предпроверка 10")
            executors.write_support(settings, support)
            executors.write_support(settings, support, folder=snapshot)
        except writer.CatalogWriteError as e:
            journal.append(actor=settings.actor, op="release",
                           target=f"catalog/{version}", outcome="refused",
                           words=str(e))
            raise ServiceError("invalid-catalog", str(e), 422,
                               problems=getattr(e, "problems", ())) from None
        store.forget()
        # Вердикт паритета — в журнале выпуска дословно: чем эта версия
        # сверена с кодом решателя, обязано быть видно из записи, а не
        # из памяти того, кто нажимал.
        journal.append(actor=settings.actor, op="release",
                       target=f"catalog/{version}",
                       outcome="warning" if parity_confirm_needed(parity)
                       else "ok",
                       words=f"выпущена {version} ({len(changes)} изменений): "
                             f"{note}; {parity['words']}"
                             + (" — осознанное продолжение владельца"
                                if parity_confirm_needed(parity) else "")
                             + "; свидетельство поддержки: "
                             + ", ".join(
                                 f"{k} — {v}" for k, v in
                                 executors.support_counts(support).items()))
        return {"version": written.version(),
                "fingerprint": written.fingerprint(),
                "released_at": today, "code": head,
                "changes": len(changes),
                "release_dir": f"releases/{snapshot.name}",
                "support": {"file": executors.SUPPORT_FILE,
                            "cells": len(support["cells"]),
                            "totals": executors.support_counts(support),
                            "approved_by": identity.ACTORS["owner"]},
                "requests_in_work": in_work,
                "warnings": ([f"заявок в работе: {len(in_work)} "
                              f"({', '.join(in_work[:5])}) — выпуск их не "
                              f"трогает"] if in_work else [])
                + list(parity.get("warnings") or ()),
                "parity": {"ok": parity.get("ok"), "rc": parity.get("rc"),
                           "words": parity.get("words"),
                           "warnings": list(parity.get("warnings") or ())},
                "confirmed_parity": parity_confirm_needed(parity),
                "next": next_steps(str(version))}


#: Заметка каждой строки журнала ПЕРВОГО выпуска (§2.6 проекта): базы
#: нет, всё «добавлена», и заметка обязана говорить не «новая запись», а
#: откуда взялось тело.
FIRST_RELEASE_NOTE = ("первый выпуск службой; до него каталог собирался "
                      "инструментом извлечения")


def _journal_changes(changes, *, first: bool) -> list[dict]:
    """Строки `versions[].changes`: четыре поля схемы. У первого выпуска
    заметка одна на все строки — она о происхождении снимка, а не о
    правке, которой не было."""
    rows = tdiff.as_journal(changes)
    if first:
        for row in rows:
            row["note"] = FIRST_RELEASE_NOTE
    return rows


def next_steps(version: str) -> list[str]:
    """Послевыпускные проверки не меняют утверждённые привязки.

    `release` уже записал их побайтовые SHA в неизменяемый support.
    Цепочка сборки черновика здесь неприменима: новый generated_at/from
    изменит эти SHA, даже если исполнители остались прежними. После
    обновления производных проекций и коммита остаются только читающие
    гейт привязок и паритет. Сам стенд и обе привязки под окончательный
    номер должны быть готовы до акта выпуска, не после него.
    """
    return [
        asm.REFRESH_COMMAND
        + " (посев и дельта получают версию выпуска — вычислением, "
          "а не правкой рукой)",
        f'git add catalog && git commit -m "каталог: выпуск {version}"'
        " (вместе с посевом, дельтой и удалением использованных "
        "catalog/candidates/…)",
        r".venv\Scripts\python.exe -m vmp.bindings_gate "
        r"vmp\bindings.json cdpm\bindings.json",
        asm.PARITY_COMMAND + " → 0 красных",
        "стенд и обе привязки под окончательный номер каталога готовятся "
        "до акта выпуска; support.json уже утверждает точные байты этих "
        "привязок — не пересоздавайте их после выпуска",
        "если утверждённые привязки отсутствуют или изменились, "
        "восстановите точные утверждённые артефакты и повторите проверки; "
        "если нужны другие байты — требуется новый согласованный выпуск. "
        "Не исправляйте SHA вручную и не считайте обновление support новым "
        "утверждением; текущие проверки и неизменяемый снимок не обходить",
        "пакеты, собранные по черновику, остановятся сторожем при нажатии — "
        "перевыпуск производителем (CDPM)",
        "база заморозки (снимок шага 1) выпуском не меняется; пересмотр "
        "атрибутов базовых записей — новым файлом снимка по слову владельца",
    ]
