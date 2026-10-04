# -*- coding: utf-8 -*-
"""Сборка решателя из ветки и эфемерный решатель-судья (шаг 6, пакет
«Kotlin-исполнители агентом», проект
`final-report/KATALOG-SHAG-6K-PROEKT-CLD.md`).

Один модуль на двоих: им судит конвейер пополнения каталога, и им же
проверяет себя агент, дописавший исполнителя решателя, — слова у обоих
одни. Второго кода запуска решателя в дереве нет и заводить его нельзя:
JVM, поднятая «своими командами», остаётся сиротой и держит открытыми
архивы сборки.

Что делает модуль:

* `probe`   — проба тулчейна словами ДО того, как потрачена квота дня:
              Java, обёртка Maven и дистрибутив в кэше, свидетели кэша
              зависимостей, импорты библиотеки CDPM и `httpx` в венве
              клона, проба записи в `_repro`, снятие сирот по метке;
* `build`   — сборка из КОММИТА ветки (или из рабочего дерева при
              самопроверке) в отдельной папке попытки: `package` без
              тестов → отпечатки артефакта → `test` → повторные
              отпечатки (обязаны совпасть) → счёт surefire → гашение
              демона компилятора Kotlin → поиск `hs_err_pid*.log`;
* `test_hygiene`  — судья тестов Kotlin: новый файл `*Test.kt` с
              литералом строки посева, существующие тесты не правлены,
              `@Disabled`/`Assumptions` не заведены;
* `tables_growth` — механический разбор двух снимков
              `vmp.kotlin_tables.tables()`: что в таблицах решателя
              выросло (класс правки К-исп либо К-табл);
* `jvm_env` — окружение подпроцессов БЕЛЫМ СПИСКОМ (список запретов
              слабее: он не знает о переменной, заведённой завтра);
* `EphemeralSolver` — решатель из собранного артефакта на свободном
              порту, с меткой `-Dcosmos.ephemeral=<ключ>`, сверкой
              «слушающий порт процесс — наш», готовностью по рабочей
              ручке и ОБЯЗАТЕЛЬНОЙ остановкой в `finally`;
* `reap_orphans` — сборщик сирот по КОМАНДНОЙ СТРОКЕ (решатели контуров
              по имени `java` не задеваются никогда);
* `scrub`   — вычистка машинных путей из слов свидетельства.

Ловушки, из-за которых модуль написан именно так (факты проб 08.09.2026
на машине владельца и на legion):

1. компилятор Kotlin оставляет фоновую JVM, наследующую дескрипторы
   вывода: всё, что читает вывод Maven до конца файла
   (`capture_output`, `communicate`, перенаправление в PowerShell),
   висит после конца сборки — вывод сборки идёт только В ФАЙЛ, а
   ожидание — только прямого потомка;
2. флаг `-Dkotlin.compiler.daemon=false` снимает саму ловушку и по
   времени ничего не стоит; гашение сирот компилятора — пояс и
   подтяжки;
3. `cmd /c "mvnw.cmd … > файл"` в кавычках не запускается (cmd считает
   всю строку именем команды) — обёртка зовётся напрямую;
4. у `Start-Process -PassThru` в PowerShell 5.1 `ExitCode` пуст —
   код возврата читается подпроцессом Python;
5. сборка в самом дереве столкнулась бы с архивами работающего
   решателя (jar открыт службой) — собирается только scratch-копия.

Команда (то же вычисление, что у гейта):

    python -m vmp.solver_build probe
    python -m vmp.solver_build build --attempt <папка> [--with-tests]
    python -m vmp.solver_build check --records <запись>

Зависимости: стандартная библиотека и `vmp.run.button.SolverHttp` через
`vmp.stand_solver` (готовность решателя — тот же клиент, которым ходит
кнопка; второго HTTP-клиента решателя в дереве не заводится).

Контракт (имена, подписи, форма словарей) — §9.1 проекта; форма блоков
`build.*` и `run.*` — §4 проекта.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from xml.etree import ElementTree

#: Корень дерева (модуль лежит в `vmp/`).
REPO = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------- слова

#: Метка эфемерного решателя в командной строке JVM. По ней и только по
#: ней сборщик сирот отличает наш экземпляр от решателей контуров.
MARKER = "-Dcosmos.ephemeral="

#: Метки фоновых JVM компилятора Kotlin (гасятся после сборки).
COMPILER_MARKERS = ("kotlin-build-tools-impl", "KotlinCompileDaemon")

#: Стратегия компилятора «в процессе»: без неё Maven оставляет демона.
KOTLIN_DAEMON_FLAG = "-Dkotlin.compiler.daemon=false"

#: Память: Maven (компилятор Kotlin в его процессе), форк surefire,
#: эфемерный решатель. Форк без предела брал четверть памяти машины.
MAVEN_OPTS_DEFAULT = "-Xmx2g"
XMX_MAVEN_DEFAULT = "2g"
XMX_FORK_DEFAULT = "1g"
XMX_SOLVER_DEFAULT = "1g"

#: Бюджеты §5 проекта, секунды.
PROBE_TIMEOUT_S = 60
BUILD_TIMEOUT_S = 900
TEST_TIMEOUT_S = 900
START_TIMEOUT_S = 120
STOP_TIMEOUT_S = 30
STAND_TIMEOUT_S = 900

#: Шаг опроса «поднялся ли решатель», секунды (§5: 0,5 с).
START_POLL_S = 0.5
#: Ожидание после `terminate` и после `taskkill`, секунды (§5).
STOP_TERMINATE_S = 20
STOP_KILL_S = 10

#: Классы правки решателя (решение 0.9).
CLASS_ISP = "К-исп"
CLASS_TABL = "К-табл"

#: Ключи снимка `vmp.kotlin_tables.tables()`.
TABLE_KEYS = ("knobs", "sit_knobs", "sit_param", "constants",
              "history_families", "history_units", "family_to_constraint",
              "logic_forms", "metrics")

#: Коды возврата команды.
RC_OK = 0
RC_RED = 1
RC_USAGE = 2
RC_NO_TOOLCHAIN = 3

#: Виды исполнителей решателя, которые судит живой стенд.
LIVE_KINDS = ("kotlin_constraint", "kotlin_logic_form", "kotlin_metric")

#: Окно портов, которое эфемерный решатель не занимает никогда: резервы
#: установщика (8090–8107 — контуры и службы каталога) и окно Hyper-V
#: (8030–8129). Проект считал, что динамический диапазон Windows
#: (49152–65535) лежит вне этих резервов, — на машине владельца ОС
#: выдала 9905 и 11398, то есть диапазон настроен ниже; свободный порт
#: из резервного окна отняли бы у службы, которая в этот миг не поднята.
RESERVED_PORT_WINDOW = (8030, 8129)

#: Имена ресурсов тестов, которые агенту заводить нельзя: файл настроек
#: и описатель платформы JUnit гасят утверждения целого набора.
FORBIDDEN_TEST_RESOURCES = ("junit-platform.properties",)

#: Признаки «тест не исполняется» — в новых файлах их быть не должно.
#: Семья условных аннотаций JUnit 5 (`@EnabledIf…`, `@DisabledIf…`,
#: `@EnabledOn…`, `@DisabledOn…`) гасит тест ровно так же, как
#: `@Disabled`, и в отчёте surefire даёт `skipped`; «@Disabled» как
#: подстрока покрывает и `@DisabledIf…`/`@DisabledOn…`.
DISABLED_TOKENS = ("@Disabled", "@Ignore", "Assumptions.", "assumeTrue",
                   "assumeFalse", "@EnabledIf", "@EnabledOn")

#: Тот же перечень регулярным выражением — им считаются УЖЕ СТОЯЩИЕ в
#: базе выключенные и условные тесты (мера «пропущенных не стало
#: больше»). Считать одни `@Disabled`/`@Ignore` нельзя: в дереве их нет
#: ни одного, а surefire стабильно даёт `skipped=1` из-за
#: `@EnabledIfSystemProperty(named = "bench")` — судья «skipped ≤
#: skipped_main» краснел бы у КАЖДОГО CR с видами решателя по
#: построению, каким бы зелёным ни был код.
SKIP_ANNOTATION_RE = re.compile(
    r"@Disabled\w*|@Ignore\b|@EnabledIf\w*|@DisabledIf\w*"
    r"|@EnabledOn\w*|@DisabledOn\w*|@EnabledFor\w*|@DisabledFor\w*"
    r"|\bAssumptions\.|\bassumeTrue\b|\bassumeFalse\b")

#: Хвост журнала в свидетельстве, строк.
LOG_TAIL_LINES = 40


class SolverBuildError(RuntimeError):
    """Отказ модуля словами (молчаливого пропуска нет ни на одном шаге)."""


# ------------------------------------------------------------- скруббер

_DRIVE_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/][^\s\"'()]+")
_HOME_PATH = re.compile(r"/home/[^\s\"'()]+")

#: Слова-заменители корней по их порядку в вызове (§4).
_ROOT_WORDS = ("<клон>", "<попытка>", "<временная папка>")


def _root_pattern(root: str) -> str:
    """Образец пути корня, безразличный к виду разделителя."""
    parts = [re.escape(p) for p in re.split(r"[\\/]+", str(root)) if p]
    return r"[\\/]+".join(parts)


def scrub(text: str, roots: list | None = None) -> str:
    """Слова свидетельства без машинных путей.

    Корни (клон, папка попытки, временная папка) заменяются словами по
    порядку — сначала самые длинные, иначе путь попытки внутри клона
    превратился бы в «<клон>/_repro/…» и выдал бы устройство машины.
    Остаток машинных путей — по образцу с ГРАНИЦЕЙ `(?<![A-Za-z])`: без
    неё скруббер испортил бы `http://127.0.0.1:53127` (буква перед
    двоеточием — часть слова «http»).
    """
    out = str(text if text is not None else "")
    pairs: list[tuple[str, str]] = []
    for i, item in enumerate(roots or []):
        if isinstance(item, (tuple, list)) and len(item) == 2:
            root, word = str(item[0]), str(item[1])
        else:
            root = str(item)
            word = _ROOT_WORDS[i] if i < len(_ROOT_WORDS) else "<путь>"
        if root.strip():
            pairs.append((root, word))
    for root, word in sorted(pairs, key=lambda p: -len(p[0])):
        out = re.sub(_root_pattern(root), word, out, flags=re.IGNORECASE)
    out = _DRIVE_PATH.sub("<путь>", out)
    out = _HOME_PATH.sub("<путь>", out)
    return out


def _tail(path: Path, lines: int = LOG_TAIL_LINES,
          roots: list | None = None) -> str:
    """Хвост журнала через скруббер (журнала нет — так и сказано)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "журнала нет"
    rows = [r.rstrip() for r in text.splitlines() if r.strip()]
    return scrub("\n".join(rows[-lines:]), roots)


# ------------------------------------------------------- подпроцессы

def _kill_tree(pid: int) -> bool:
    """Снять процесс с потомками (`taskkill /T /F`; на POSIX — сигнал)."""
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/PID", str(int(pid)), "/T",
                                "/F"], capture_output=True, text=True,
                               timeout=30)
            return r.returncode == 0
        os.kill(int(pid), 9)                                  # noqa: S606
        return True
    except Exception:                                          # noqa: BLE001
        return False


def _run_to_file(argv: list, *, cwd, log_path: Path, timeout_s: float,
                 env: dict | None = None) -> tuple[int, float]:
    """Подпроцесс с выводом В ФАЙЛ; ожидается ТОЛЬКО прямой потомок.

    Ни `capture_output`, ни `communicate`: фоновая JVM компилятора
    наследует дескриптор вывода и живёт после конца Maven — читающий
    вывод до конца файла висит вместе с ней (проба legion: 624 с
    ожидания при 30 с сборки). Код возврата прямого потомка при этом
    надёжен.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with open(log_path, "ab") as fh:
        fh.write(("\n=== " + " ".join(str(a) for a in argv)
                  + "\n").encode("utf-8", "replace"))
        fh.flush()
        proc = subprocess.Popen([str(a) for a in argv], cwd=str(cwd),
                                stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env)
        try:
            rc = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            # Бюджет исчерпан: снимаем дерево процессов и говорим об
            # этом кодом 124 — «TIMEOUT» без диагноза недопустим.
            _kill_tree(proc.pid)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:       # pragma: no cover
                pass
            rc = 124
    return int(rc), round(time.time() - started, 1)


def _capture(argv: list, *, cwd=None, timeout_s: float = PROBE_TIMEOUT_S,
             env: dict | None = None) -> tuple[int, str]:
    """Короткая команда с чтением вывода (git, java -version, taskkill).

    Maven так НЕ зовётся никогда — только `_run_to_file`.
    """
    try:
        r = subprocess.run([str(a) for a in argv],
                           cwd=(str(cwd) if cwd else None),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout_s, env=env)
    except FileNotFoundError as e:
        return 127, str(e)
    except subprocess.TimeoutExpired:
        return 124, f"команда не уложилась в {timeout_s} с"
    return int(r.returncode), (r.stdout or "") + (r.stderr or "")


def _powershell(script: str, *, timeout_s: float = 60) -> tuple[int, str]:
    """PowerShell для опроса процессов и портов (Windows)."""
    if os.name != "nt":
        return 127, "не Windows"
    return _capture(["powershell.exe", "-NoProfile", "-NonInteractive",
                     "-Command", script], timeout_s=timeout_s)


# ------------------------------------------------------------ процессы

def _processes() -> list:
    """Живые JVM: PID, момент рождения (эпоха, секунды), КОМАНДНАЯ строка.

    Точка подмены для тестов: перечень процессов подделывается целиком,
    настоящая JVM тестам не нужна.
    """
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name like '%java%'\" | "
        "ForEach-Object { [pscustomobject]@{ pid = $_.ProcessId; "
        "created = [int64]((($_.CreationDate).ToUniversalTime() - "
        "(Get-Date '1970-01-01T00:00:00Z').ToUniversalTime())"
        ".TotalSeconds); cmd = $_.CommandLine } } | "
        "ConvertTo-Json -Compress")
    _rc, out = _powershell(script)
    # Код возврата PowerShell здесь не судья: при `-ErrorAction
    # SilentlyContinue` он бывает 1 и на пустом ответе, и на удачном
    # (ловушка «нет процесса с таким именем»). Судит разбор вывода.
    if not out.strip():
        return []
    try:
        data = json.loads(out.strip())
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    rows = []
    for item in data or []:
        try:
            rows.append({"pid": int(item.get("pid")),
                         "created": float(item.get("created") or 0),
                         "cmd": str(item.get("cmd") or "")})
        except (TypeError, ValueError):
            continue
    return rows


def contour_tag(repo) -> str:
    """Признак КОНТУРА (клона) в метке эфемерного решателя.

    На производственной машине контуры Test, Stage и Prod — зеркальные
    копии дерева под ОДНОЙ учётной записью, и метка без признака контура
    делала сборщика сирот межконтурным: перезапуск соседнего приложения
    или его предпосылка снимали бы JVM судьи, идущего минутами, а строка
    уходила бы в «нужен человек» со словами «умер сам». Признак — восемь
    шестнадцатеричных знаков от пути клона (регистр пути на Windows не
    значим, разделители приведены к одному виду).
    """
    text = str(Path(repo).resolve()).replace("\\", "/").rstrip("/").lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def contour_marker(repo) -> str:
    """Метка эфемерных решателей ЭТОГО клона: `-Dcosmos.ephemeral=<тег>:`.

    Её и передают сборщику сирот: чужой контур несёт свой тег, и его
    решатель не попадает под снятие.
    """
    return f"{MARKER}{contour_tag(repo)}:"


def find_orphans(marker=MARKER, since: float | None = None) -> list:
    """Живые JVM с меткой — БЕЗ снятия: строки перечня процессов.

    Отдельная от `reap_orphans` половина: проба тулчейна (и самопроверка
    агента через неё) обязана назвать сирот словами, но не снимать их —
    решатель соседнего контура или соседней попытки живым процессом не
    её дело.
    """
    marks = ([str(marker)] if isinstance(marker, str)
             else [str(m) for m in marker])
    out = []
    for row in _processes():
        cmd = row.get("cmd") or ""
        if not any(m and m in cmd for m in marks):
            continue
        if since is not None and float(row.get("created") or 0) < float(since):
            continue
        out.append(row)
    return out


def reap_orphans(marker=MARKER, since: float | None = None) -> list:
    """Снять сирот по КОМАНДНОЙ СТРОКЕ; вернуть снятые PID.

    Судится командная строка, а не имя образа: решатели контуров
    (8098/8099/8100 на legion, 9099 на машине владельца) зовутся тем же
    `java.exe` и метки не несут — их не трогает ничто. `since` (момент
    старта сборки) отсекает чужие фоновые JVM компилятора, жившие до
    нас.

    ВАЖНО: метку передавайте СВОЕГО контура — `contour_marker(<клон>)`.
    Умолчание `MARKER` совпадает с меткой ЛЮБОГО эфемерного решателя
    машины, и вызов с ним снял бы суд соседнего контура на полпути; ни
    один вызов в дереве умолчанием не пользуется.
    """
    killed = []
    for row in find_orphans(marker, since):
        if _kill_tree(row["pid"]):
            killed.append(int(row["pid"]))
    return killed


def _port_owner(port: int) -> int | None:
    """PID процесса, слушающего порт (сверка «сосед на порту — не наш»)."""
    _rc, out = _powershell(
        f"$c = Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if ($c) { $c.OwningProcess }")
    text = (out or "").strip()
    if text.isdigit():                    # код возврата здесь не судья
        return int(text)
    rc, out = _capture(["netstat", "-ano", "-p", "TCP"], timeout_s=60)
    if rc != 0:
        return None
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" \
                and parts[-2].upper() == "LISTENING" \
                and parts[1].endswith(f":{int(port)}"):
            try:
                return int(parts[-1])
            except ValueError:
                return None
    return None


def _free_port() -> int:
    """Свободный порт от ОС: `bind(127.0.0.1, 0)`.

    Реестра портов у эфемерного решателя нет и не нужно — но резервное
    окно `RESERVED_PORT_WINDOW` пропускается: ОС считает порт службы,
    которая сейчас не поднята, свободным, а мы заняли бы его на время
    запроса на изменение. Гонка «порт заняли между bind и стартом»
    закрыта одной повторной попыткой старта.
    """
    port = 0
    for _ in range(20):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            port = int(s.getsockname()[1])
        finally:
            s.close()
        if not (RESERVED_PORT_WINDOW[0] <= port <= RESERVED_PORT_WINDOW[1]):
            return port
    raise SolverBuildError(
        "свободного порта вне резервного окна "
        f"{RESERVED_PORT_WINDOW[0]}–{RESERVED_PORT_WINDOW[1]} ОС не дала "
        "за 20 попыток — проверьте настройку динамического диапазона "
        "портов машины")


def _port_free(port: int) -> bool:
    """Порт освободился (после остановки решателя)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ------------------------------------------------------------ тулчейн

def _tools_dir(repo: Path) -> Path:
    """Папка инструментов клона (её кладёт установщик): `.tools`."""
    return Path(repo) / ".tools"


def java_home_order(repo: Path, java_home=None) -> tuple:
    """Порядок поиска Java (решение 0.12) — путь и слово об источнике."""
    if java_home:
        return Path(java_home), "аргумент команды"
    env = os.environ.get("COSMOS_SOLVER_JAVA_HOME", "").strip()
    if env:
        return Path(env), "COSMOS_SOLVER_JAVA_HOME"
    env = os.environ.get("JAVA_HOME", "").strip()
    if env:
        return Path(env), "JAVA_HOME"
    for cand in sorted(_tools_dir(repo).glob("jdk*")):
        if (cand / "bin").is_dir():
            return cand, "<клон>/.tools"
    exe = shutil.which("java")
    if exe:
        return Path(exe).resolve().parents[1], "java в PATH"
    return None, "нигде"


def java_exe(java_home) -> str:
    """Путь к `java` (из дома Java либо из PATH)."""
    if java_home:
        exe = Path(java_home) / "bin" / ("java.exe" if os.name == "nt"
                                         else "java")
        if exe.exists():
            return str(exe)
    return shutil.which("java") or "java"


def maven_order(repo: Path, solver_dir: Path, mvn=None, m2=None) -> dict:
    """Порядок выбора Maven (§5) со словами об источнике.

    Обёртка `mvnw.cmd` пина `only-script`: без дистрибутива в кэше она
    полезла бы в сеть, а гейт идёт оффлайн — поэтому наличие
    дистрибутива проверяется, а не предполагается.
    """
    if not mvn:
        mvn = os.environ.get("COSMOS_SOLVER_MVN", "").strip() or None
    if mvn:
        return {"ok": Path(mvn).exists(), "kind": "явный путь",
                "command": str(mvn), "dist": None,
                "words": ("Maven задан явно: " + str(mvn)
                          if Path(mvn).exists() else
                          "путь Maven из COSMOS_SOLVER_MVN не существует: "
                          + str(mvn))}
    wrapper = Path(solver_dir) / ("mvnw.cmd" if os.name == "nt" else "mvnw")
    props = Path(solver_dir) / ".mvn" / "wrapper" / "maven-wrapper.properties"
    dist_ver = ""
    if props.exists():
        m = re.search(r"apache-maven-([\d.]+)-bin",
                      props.read_text(encoding="utf-8", errors="replace"))
        dist_ver = m.group(1) if m else ""
    dists = _m2_root(m2) / "wrapper" / "dists"
    have_dist = bool(dist_ver) and bool(list(dists.glob(
        f"apache-maven-{dist_ver}*")))
    if wrapper.exists() and have_dist:
        return {"ok": True, "kind": "обёртка", "command": str(wrapper),
                "dist": f"apache-maven-{dist_ver}",
                "words": (f"Maven — обёртка {wrapper.name}, дистрибутив "
                          f"apache-maven-{dist_ver} в кэше (оффлайн)")}
    for cand in sorted(_tools_dir(repo).glob("apache-maven*")):
        exe = cand / "bin" / ("mvn.cmd" if os.name == "nt" else "mvn")
        if exe.exists():
            return {"ok": True, "kind": "установщик", "command": str(exe),
                    "dist": cand.name,
                    "words": f"Maven из .tools клона: {cand.name}"}
    exe = shutil.which("mvn")
    if exe:
        return {"ok": True, "kind": "PATH", "command": exe, "dist": None,
                "words": "Maven из PATH"}
    words = "Maven не найден: "
    if wrapper.exists() and not have_dist:
        words += (f"обёртка {wrapper.name} есть, но дистрибутива "
                  f"apache-maven-{dist_ver or '<версия из пина>'} нет в "
                  f"кэше обёртки — прогрейте с сетью "
                  f"«{wrapper.name} -N -B validate» либо задайте "
                  f"COSMOS_SOLVER_MVN")
    else:
        words += ("нет ни обёртки решателя, ни apache-maven в .tools "
                  "клона, ни mvn в PATH")
    return {"ok": False, "kind": None, "command": None, "dist": None,
            "words": words}


def _m2_root(m2=None) -> Path:
    """Корень локального Maven (`.m2`), а не хранилище внутри него."""
    if m2:
        p = Path(m2)
        return p.parent if p.name == "repository" else p
    env = os.environ.get("COSMOS_SOLVER_M2", "").strip()
    if env:
        p = Path(env)
        return p.parent if p.name == "repository" else p
    return Path.home() / ".m2"


def _m2_repository(m2=None) -> Path:
    """Хранилище зависимостей (`.m2/repository`)."""
    if m2:
        p = Path(m2)
        return p if p.name == "repository" else p / "repository"
    return _m2_root(m2) / "repository"


def pom_versions(solver_dir: Path) -> dict:
    """Версии из `pom.xml` решателя — свидетели кэша строятся по ним."""
    out = {"quarkus": None, "kotlin": None, "timefold": None,
           "surefire": None}
    pom = Path(solver_dir) / "pom.xml"
    try:
        text = pom.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    keys = {"quarkus": "version.io.quarkus", "kotlin": "version.kotlin",
            "timefold": "version.ai.timefold.solver",
            "surefire": "version.surefire.plugin"}
    for name, tag in keys.items():
        m = re.search(rf"<{re.escape(tag)}>([^<]+)</{re.escape(tag)}>", text)
        if m:
            out[name] = m.group(1).strip()
    return out


def cache_witnesses(versions: dict) -> list:
    """Свидетели кэша зависимостей (решение 0.12) — по версиям pom."""
    kotlin = versions.get("kotlin") or "2.3.20"
    surefire = versions.get("surefire") or "3.5.2"
    quarkus = versions.get("quarkus") or "3.34.5"
    return [f"org/jetbrains/kotlin/kotlin-maven-plugin/{kotlin}",
            f"org/apache/maven/surefire/surefire-junit-platform/{surefire}",
            f"io/quarkus/quarkus-maven-plugin/{quarkus}"]


def jvm_env(java_home, *, maven_opts: str = "", m2=None) -> dict:
    """Окружение подпроцесса БЕЛЫМ СПИСКОМ (решение 0.10).

    Список запретов слабее белого списка: он не знает о переменной,
    заведённой завтра, а решатель читает пакет постановки и настройки
    правил ИЗ ОКРУЖЕНИЯ (`CREW_TASK_PACKAGE`, `CREW_RULES_CONFIG`,
    `CREW_RESOURCE_RULES`) — наследование среды службы сделало бы суд
    невоспроизводимым. `JAVA_TOOL_OPTIONS` и `_JAVA_OPTIONS` не
    передаются никогда: они молча меняют флаги любой JVM.

    Местный Maven (`.m2`) передаётся не переменной, а аргументом
    `-Dmaven.repo.local` — переменной у него нет.
    """
    keep = ("SystemRoot", "windir", "SystemDrive", "TEMP", "TMP",
            "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "PATHEXT", "COMSPEC",
            "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
            "LANG", "LC_ALL")
    env = {k: os.environ[k] for k in keep if os.environ.get(k)}
    java_bin = ""
    if java_home:
        env["JAVA_HOME"] = str(java_home)
        java_bin = str(Path(java_home) / "bin")
    else:
        exe = shutil.which("java")
        if exe:
            java_bin = str(Path(exe).parent)
    root = env.get("SystemRoot") or os.environ.get("SystemRoot") or ""
    system_paths = [p for p in (
        (str(Path(root) / "system32") if root else ""),
        root,
        (str(Path(root) / "System32" / "Wbem") if root else ""),
        (str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0")
         if root else ""),
    ) if p]
    env["PATH"] = os.pathsep.join([p for p in ([java_bin] + system_paths)
                                   if p])
    if maven_opts:
        env["MAVEN_OPTS"] = str(maven_opts)
    if m2:
        # Не переменной среды: путь хранилища едет аргументом команды —
        # ключ виден в свидетельстве вместе с самой командой.
        env.setdefault("MAVEN_USER_HOME", str(_m2_root(m2)))
    return env


# ---------------------------------------------------------------- проба

_IMPORT_PROBE = (
    "import json\n"
    "missing = []\n"
    "for name in ('cdpm.export_task_package', 'httpx'):\n"
    "    try:\n"
    "        __import__(name)\n"
    "    except Exception as e:\n"
    "        missing.append(name + ': ' + type(e).__name__)\n"
    "print(json.dumps(missing))\n")


def clone_python(repo: Path) -> str:
    """Python венва клона (стенд идёт им же)."""
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        cand = Path(repo) / ".venv" / rel[0] / rel[1]
        if cand.exists():
            return str(cand)
    return sys.executable


def probe(repo: Path, *, java_home=None, mvn=None,
          venv_python=None) -> dict:
    """Проба тулчейна словами ДО ветки и до траты квоты (решение 0.12).

    «Сборка недоступна» обязана звучать словами: чего нет и что сделать.
    Молчаливого пропуска нет ни на одном шаге — ни здесь, ни ниже.
    """
    repo = Path(repo)
    solver = repo / "kotlin-solver"
    roots = [str(repo)]
    out: dict = {}

    home, source = java_home_order(repo, java_home)
    exe = java_exe(home)
    rc, text = _capture([exe, "-version"], timeout_s=PROBE_TIMEOUT_S)
    version = (text or "").strip().splitlines()[0] if text.strip() else ""
    java_ok = rc == 0 and bool(version)
    out["java"] = {
        "ok": java_ok, "home": scrub(str(home or ""), roots),
        "exe": scrub(exe, roots), "version": version, "source": source,
        "words": (f"Java: {version} ({source})" if java_ok else
                  "Java не найдена: искали COSMOS_SOLVER_JAVA_HOME, "
                  "JAVA_HOME, <клон>/.tools/jdk*, java в PATH — "
                  "поставьте JDK 21 либо задайте COSMOS_SOLVER_JAVA_HOME")}

    maven = maven_order(repo, solver, mvn=mvn)
    out["maven"] = {"ok": bool(maven["ok"]), "kind": maven["kind"],
                    "command": scrub(str(maven["command"] or ""), roots),
                    "dist": maven["dist"], "words": maven["words"]}
    out["maven_kind"] = maven["kind"]

    versions = pom_versions(solver)
    repo_dir = _m2_repository()
    witnesses = cache_witnesses(versions)
    missing = [w for w in witnesses if not (repo_dir / w).is_dir()]
    cache_ok = repo_dir.is_dir() and not missing
    warm = ("прогрейте кэш С ТЕСТАМИ и с сетью: "
            "kotlin-solver/mvnw.cmd -B test — прогрев «-DskipTests "
            "package» поставщика surefire не тянет")
    out["cache"] = {
        "ok": cache_ok, "m2": scrub(str(repo_dir), roots),
        "witnesses": witnesses, "missing": missing,
        "words": ("кэш Maven на месте: " + ", ".join(witnesses)
                  if cache_ok else
                  "кэш Maven неполон: нет "
                  + ", ".join(missing or [scrub(str(repo_dir), roots)])
                  + " — " + warm)}

    py = str(venv_python or clone_python(repo))
    rc, text = _capture([py, "-c", _IMPORT_PROBE], cwd=repo,
                        timeout_s=PROBE_TIMEOUT_S)
    miss_imports: list = []
    if rc != 0:
        miss_imports = ["python венва клона не отвечает: " + text[-200:]]
    else:
        try:
            miss_imports = json.loads((text or "[]").strip().splitlines()[-1])
        except (ValueError, IndexError):
            miss_imports = ["ответ пробы импортов не разобран"]
    imports_ok = not miss_imports
    out["imports"] = {
        "ok": imports_ok, "python": scrub(py, roots),
        "checked": ["cdpm.export_task_package", "httpx"],
        "missing": miss_imports,
        "words": ("импорты венва клона на месте: библиотека CDPM и httpx"
                  if imports_ok else
                  "в венве клона нет " + ", ".join(miss_imports)
                  + " — стенд импортирует библиотеку CDPM и httpx: "
                  "поставьте зависимости среза шагов 4–7 каталога")}

    repro = repo / "_repro"
    try:
        repro.mkdir(parents=True, exist_ok=True)
        stamp = repro / f".probe-{os.getpid()}"
        stamp.write_text("проба записи", encoding="utf-8")
        stamp.unlink()
        repro_ok, repro_words = True, "запись в _repro возможна"
    except OSError as e:
        repro_ok = False
        repro_words = f"в _repro не пишется: {scrub(str(e), roots)}"
    out["repro_writable"] = {"ok": repro_ok, "path": "_repro",
                             "words": repro_words}

    # Модуль сборки в САМОМ клоне нужен только агенту (самопроверка
    # `python -m vmp.solver_build check`): судью конвейер берёт из
    # продукта и клоном не подменяет. Поэтому это НЕ условие пробы —
    # слова есть, красного нет.
    rc, _text = _capture([py, "-c", "import vmp.solver_build"], cwd=repo,
                         timeout_s=PROBE_TIMEOUT_S)
    out["agent_self_check"] = {
        "ok": rc == 0,
        "words": ("самопроверка агента доступна: python -m "
                  "vmp.solver_build check" if rc == 0 else
                  "в клоне нет vmp/solver_build.py: самопроверка агента "
                  "не поднимется (гейт конвейера берёт модуль из "
                  "продукта и от этого не зависит)")}

    # Сироты — НАЗЫВАЮТСЯ, но не снимаются: проба идёт и в самопроверке
    # агента, а снятие чужого живого решателя — не её дело (снимает
    # предпосылка конвейера меткой своего контура).
    out["orphans"] = [int(r["pid"]) for r in
                      find_orphans(contour_marker(repo))]
    out["ok"] = all(out[k]["ok"] for k in
                    ("java", "maven", "cache", "imports", "repro_writable"))
    parts = [out[k]["words"] for k in
             ("java", "maven", "cache", "imports", "repro_writable")]
    parts.append(out["agent_self_check"]["words"])
    if out["orphans"]:
        parts.append("эфемерные решатели этого контура из прошлых попыток "
                     "живы (проба их не снимает): "
                     + ", ".join(str(p) for p in out["orphans"]))
    out["words"] = ("проба тулчейна: " if out["ok"] else
                    "сборка решателя на этом контуре недоступна: ") \
        + "; ".join(parts)
    return out


# ------------------------------------------------------------ артефакт

def sha256_file(path: Path) -> str:
    """SHA-256 файла."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_identity(quarkus_app: Path) -> dict | None:
    """Личность артефакта: четыре отпечатка (§4, решение 0.11).

    Хеш одного `app/*.jar` не описывает того, что запускалось: вместе с
    кодом едут `quarkus/generated-bytecode.jar` и `lib/main/*`. Поэтому
    сводный `tree_sha256` — отпечаток отсортированного перечня «путь →
    SHA-256» ВСЕХ файлов `quarkus-app/`. Пусковой `quarkus-run.jar`
    (сотни байт) одинаков у сборок и стоит для полноты.
    """
    qa = Path(quarkus_app)
    run_jar = qa / "quarkus-run.jar"
    app_jars = sorted((qa / "app").glob("*.jar")) if (qa / "app").is_dir() \
        else []
    if not qa.is_dir() or not run_jar.exists() or not app_jars:
        return None
    deps = qa / "quarkus-app-dependencies.txt"
    lines = []
    for p in sorted(qa.rglob("*")):
        if p.is_file():
            lines.append(f"{p.relative_to(qa).as_posix()} "
                         f"{sha256_file(p)}")
    tree = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return {"app_jar": f"quarkus-app/app/{app_jars[0].name}",
            "app_jar_sha256": sha256_file(app_jars[0]),
            "run_jar_sha256": sha256_file(run_jar),
            "dependencies_txt_sha256": (sha256_file(deps) if deps.exists()
                                        else ""),
            "tree_sha256": tree,
            "files": len(lines)}


def _same_identity(a: dict | None, b: dict | None) -> bool:
    """Артефакт до и после тестов — тот же (четыре отпечатка)."""
    if not a or not b:
        return False
    keys = ("app_jar_sha256", "run_jar_sha256", "dependencies_txt_sha256",
            "tree_sha256")
    return all(a.get(k) == b.get(k) for k in keys)


def surefire_counts(reports_dir: Path) -> dict:
    """Счёт surefire из машинных отчётов, а не из слов журнала."""
    reports = sorted(Path(reports_dir).glob("TEST-*.xml"))
    total = failures = errors = skipped = 0
    for p in reports:
        try:
            root = ElementTree.parse(p).getroot()
        except (ElementTree.ParseError, OSError):
            continue
        total += int(root.get("tests") or 0)
        failures += int(root.get("failures") or 0)
        errors += int(root.get("errors") or 0)
        skipped += int(root.get("skipped") or 0)
    return {"tests": total, "failures": failures, "errors": errors,
            "skipped": skipped, "classes": len(reports)}


def count_test_annotations(test_root: Path) -> tuple:
    """Число `@Test` в исходниках тестов и число файлов."""
    n = 0
    files = 0
    root = Path(test_root)
    if not root.is_dir():
        return 0, 0
    for p in sorted(root.rglob("*.kt")):
        files += 1
        n += len(re.findall(r"@Test\b",
                            p.read_text(encoding="utf-8", errors="replace")))
    return n, files


# ------------------------------------------------------------- сборка

def _rmtree_writable(path) -> None:
    """Удаление папки попытки, терпимое к атрибуту «только чтение».

    copytree переносит на копию исходников режим папок дерева, а клиенты
    синхронизации рабочего стола выставляют папкам дерева «только чтение»
    (09.09.2026: rmdir такой папки на Windows — «Access is denied», и
    прежнее ignore_errors молча оставляло попытку в scratch). Помощник
    снимает режим с помехи и с её папки-родителя и повторяет шаг; вторая
    неудача — молча, как прежде: суд попытки уже состоялся, а мусор в
    scratch — не отказ."""
    def _onexc(func, p, exc):
        if not isinstance(exc, PermissionError):
            return
        for q in (p, os.path.dirname(p)):
            try:
                os.chmod(q, stat.S_IRWXU)
            except OSError:
                pass
        try:
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onexc=_onexc)


def _materialize(repo: Path, attempt: Path, source: str,
                 commit: str | None) -> tuple:
    """Исходники решателя в папке попытки: из коммита или из дерева.

    Из КОММИТА — потому что судится код коммита, а не рабочего дерева;
    в scratch-копии — потому что сборка в самом дереве столкнулась бы с
    архивами работающего решателя (jar открыт службой) и подменила бы
    артефакт службы.
    """
    attempt = Path(attempt)
    attempt.mkdir(parents=True, exist_ok=True)
    dest = attempt / "kotlin-solver"
    if dest.exists():
        _rmtree_writable(dest)
    if source == "commit":
        if not commit:
            return None, "коммит сборки не назван (--commit)"
        tar_path = attempt / "kotlin-solver.tar"
        rc, text = _capture(["git", "-C", str(repo), "archive",
                             "--format=tar", "-o", str(tar_path), commit,
                             "kotlin-solver"], timeout_s=300)
        if rc != 0:
            return None, ("архив коммита не собран (git archive "
                          f"{commit[:12]}): {text[-300:]}")
        with tarfile.open(tar_path) as tf:
            tf.extractall(attempt, filter="data")
        tar_path.unlink(missing_ok=True)
    elif source == "tree":
        src = Path(repo) / "kotlin-solver"
        if not src.is_dir():
            return None, "в дереве нет папки kotlin-solver"
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(
            "target", ".git", "hs_err_pid*.log", "*.log"))
    else:
        return None, f"неизвестный источник сборки: {source}"
    if not (dest / "pom.xml").exists():
        return None, "в копии попытки нет kotlin-solver/pom.xml"
    return dest, ""


def _hs_errors(where: Path, roots: list) -> list:
    """Головы отчётов JVM о падении; файлы убираются за собой."""
    found = []
    for p in sorted(Path(where).rglob("hs_err_pid*.log")):
        try:
            head = p.read_text(encoding="utf-8", errors="replace")
            found.append(scrub("\n".join(head.splitlines()[:12]), roots))
        except OSError:                             # pragma: no cover
            found.append(f"{p.name}: не читается")
        p.unlink(missing_ok=True)
    return found


def build(repo: Path, *, attempt: Path, source: str = "commit",
          commit: str | None = None, with_tests: bool = False,
          timeout_package_s: float = BUILD_TIMEOUT_S,
          timeout_test_s: float = TEST_TIMEOUT_S,
          java_home=None, mvn=None, xmx_maven=None, xmx_fork=None,
          m2=None) -> dict:
    """Сборка решателя в папке попытки; словарь — блок `build` §4.

    Порядок обязателен: `package -DskipTests` → отпечатки артефакта →
    `test` → повторные отпечатки. Тесты исполняются ПОСЛЕ упаковки и не
    могут подменить измеряемый архив; расхождение отпечатков — красное
    словами, а не «наверное, так и надо».
    """
    repo = Path(repo)
    attempt = Path(attempt)
    if not attempt.is_absolute():
        attempt = (repo / attempt).resolve()
    roots = [str(repo), str(attempt)]
    xmx_maven = str(xmx_maven or os.environ.get("COSMOS_SOLVER_XMX_MAVEN")
                    or XMX_MAVEN_DEFAULT)
    xmx_fork = str(xmx_fork or os.environ.get("COSMOS_SOLVER_XMX")
                   or XMX_FORK_DEFAULT)
    maven_opts = f"-Xmx{xmx_maven}"
    out: dict = {
        "available": False, "ok": False, "source": source,
        "commit": (commit or None), "attempt": attempt.name,
        "command_package": None, "command_test": None,
        "rc_package": None, "rc_test": None,
        "duration_package_s": None, "duration_test_s": None,
        "toolchain": {}, "versions": {}, "surefire": None,
        "tests_match_sources": None,
        "test_annotations": {"branch": 0, "files": 0},
        "artifact": None, "hs_err": [], "compiler_daemons_killed": 0,
        "log_tail": "", "words": ""}

    src, words = _materialize(repo, attempt, source, commit)
    if src is None:
        out["words"] = "сборка не начата: " + scrub(words, roots)
        return out

    versions = pom_versions(src)
    out["versions"] = versions
    home, java_source = java_home_order(repo, java_home)
    exe = java_exe(home)
    rc, java_text = _capture([exe, "-version"], timeout_s=PROBE_TIMEOUT_S)
    java_line = ((java_text or "").strip().splitlines() or [""])[0]
    maven = maven_order(repo, src, mvn=mvn, m2=m2)
    if rc != 0 or not maven["ok"]:
        out["words"] = ("сборка решателя недоступна: "
                        + ("Java не запускается; " if rc != 0 else "")
                        + maven["words"])
        out["toolchain"] = {"java": java_line, "maven": maven["words"],
                            "maven_opts": maven_opts,
                            "kotlin_daemon": False}
        return out

    out["available"] = True
    out["toolchain"] = {
        # Источник Java — часть рецепта: «та же сборка» повторяется
        # только тем же порядком поиска инструментов.
        "java": f"{java_line} ({java_source})",
        "maven": f"{maven['kind']}"
                 + (f" {maven['dist']}" if maven["dist"] else "")
                 + (" (оффлайн)" if _offline() else " (с сетью)"),
        "maven_opts": maven_opts, "kotlin_daemon": False}

    env = jvm_env(home, maven_opts=maven_opts, m2=m2)
    common = (["-o"] if _offline() else []) + ["-B",
                                                KOTLIN_DAEMON_FLAG]
    if m2:
        common.append(f"-Dmaven.repo.local={_m2_repository(m2)}")
    mvn_cmd = maven["command"]
    started = time.time()

    pkg_argv = [mvn_cmd, *common, "-DskipTests", "package"]
    out["command_package"] = scrub(" ".join(str(a) for a in pkg_argv),
                                   [(str(src.parent), "<попытка>"),
                                    (str(repo), "<клон>")])
    pkg_log = attempt / "build-package.log"
    rc_pkg, sec_pkg = _run_to_file(pkg_argv, cwd=src, log_path=pkg_log,
                                   timeout_s=timeout_package_s, env=env)
    out["rc_package"], out["duration_package_s"] = rc_pkg, sec_pkg

    qa = src / "target" / "quarkus-app"
    ident_before = artifact_identity(qa)
    out["artifact"] = (dict(ident_before, unchanged_after_tests=None)
                       if ident_before else None)

    if with_tests and rc_pkg == 0:
        test_argv = [mvn_cmd, *common, f"-DargLine=-Xmx{xmx_fork}", "test"]
        out["command_test"] = scrub(" ".join(str(a) for a in test_argv),
                                    [(str(src.parent), "<попытка>"),
                                     (str(repo), "<клон>")])
        test_log = attempt / "build-test.log"
        rc_test, sec_test = _run_to_file(test_argv, cwd=src,
                                         log_path=test_log,
                                         timeout_s=timeout_test_s, env=env)
        out["rc_test"], out["duration_test_s"] = rc_test, sec_test
        ident_after = artifact_identity(qa)
        if out["artifact"] is not None:
            out["artifact"]["unchanged_after_tests"] = _same_identity(
                ident_before, ident_after)
        out["surefire"] = surefire_counts(src / "target" / "surefire-reports")

    branch_tests, branch_files = count_test_annotations(src / "src" / "test")
    out["test_annotations"] = {"branch": branch_tests, "files": branch_files}
    if out["surefire"] is not None:
        out["tests_match_sources"] = (out["surefire"]["tests"]
                                      == branch_tests)

    # Пояс и подтяжки: флаг стратегии демона снят, но фоновую JVM
    # компилятора, рождённую ЭТОЙ сборкой, гасим по командной строке.
    out["compiler_daemons_killed"] = len(
        reap_orphans(COMPILER_MARKERS, since=started))
    out["hs_err"] = _hs_errors(attempt, roots)
    out["log_tail"] = _tail(
        (attempt / "build-test.log") if with_tests and rc_pkg == 0
        else pkg_log, roots=roots)

    problems = []
    if rc_pkg != 0:
        problems.append(f"упаковка вернула код {rc_pkg}"
                        + (" (не уложилась в бюджет)" if rc_pkg == 124
                           else ""))
    if out["artifact"] is None:
        problems.append("артефакта нет: target/quarkus-app не собран")
    if with_tests:
        if rc_pkg != 0:
            problems.append("тесты решателя не прогонялись: упаковка "
                            "красная")
        elif out["rc_test"] != 0:
            problems.append(f"тесты решателя вернули код {out['rc_test']}"
                            + (" (не уложились в бюджет)"
                               if out["rc_test"] == 124 else ""))
        sf = out["surefire"] or {}
        if sf.get("failures") or sf.get("errors"):
            problems.append(f"surefire: падений {sf.get('failures')}, "
                            f"ошибок {sf.get('errors')}")
        if out["tests_match_sources"] is False:
            problems.append(
                f"счёт surefire {sf.get('tests')} не сошёлся с числом "
                f"@Test в исходниках ветки {branch_tests}")
        if out["artifact"] and out["artifact"]["unchanged_after_tests"] \
                is False:
            problems.append("артефакт изменился между упаковкой и "
                            "тестами: измерялось не то, что собрано")
    if out["hs_err"]:
        problems.append(f"JVM падала: отчётов hs_err {len(out['hs_err'])}")
    out["ok"] = not problems
    sf = out["surefire"] or {}
    green = (f"сборка зелёная: упаковка {sec_pkg} с"
             + (f", тесты {out['duration_test_s']} с, surefire "
                f"{sf.get('tests')} тестов, пропущено "
                f"{sf.get('skipped')}" if with_tests else
                ", тесты не прогонялись")
             + (f", артефакт {out['artifact']['app_jar_sha256'][:12]}"
                if out["artifact"] else ""))
    out["words"] = green if out["ok"] else "сборка красная: " + \
        "; ".join(problems)
    return out


def _offline() -> bool:
    """Оффлайн — всегда, кроме явного прогрева `COSMOS_SOLVER_BUILD_ONLINE`."""
    return os.environ.get("COSMOS_SOLVER_BUILD_ONLINE", "").strip() != "1"


# ------------------------------------------------------ гигиена тестов

_TEST_DIR = "kotlin-solver/src/test"
_TEST_SRC = "kotlin-solver/src/test/kotlin"
_TEST_RES = "kotlin-solver/src/test/resources"


def _git_lines(repo: Path, args: list, timeout_s: float = 120) -> list:
    """Строки вывода git (короткая команда, вывод читается).

    `core.quotePath=false` — не украшение: без него git печатает путь с
    неASCII именем в кавычках и восьмеричных последовательностях, и
    русское имя нового файла теста (имена тестов решателя уже русские)
    переставало оканчиваться на «.kt» — гигиена говорила «нового файла
    теста Kotlin нет» при наличии файла.
    """
    rc, text = _capture(["git", "-C", str(repo), "-c",
                         "core.quotePath=false", *args],
                        timeout_s=timeout_s)
    if rc != 0:
        raise SolverBuildError(
            "git не ответил на «" + " ".join(str(a) for a in args)
            + "»: " + (text or "")[-300:])
    return [ln for ln in (text or "").splitlines() if ln.strip()]


def _base_test_sources(repo: Path, base: str) -> dict:
    """Тексты тестов решателя на базе (одним архивом, без 35 вызовов)."""
    argv = ["git", "-C", str(repo), "archive", "--format=tar", base,
            _TEST_DIR]
    try:
        r = subprocess.run(argv, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SolverBuildError(f"архив тестов базы {base} не собран: {e}")
    if r.returncode != 0:
        raise SolverBuildError(
            f"архив тестов базы {base} не собран: "
            + r.stderr.decode("utf-8", "replace")[-300:])
    out: dict = {}
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
        for member in tf.getmembers():
            if member.isfile() and member.name.endswith(".kt"):
                fh = tf.extractfile(member)
                if fh is not None:
                    out[member.name] = fh.read().decode("utf-8", "replace")
    return out


def test_hygiene(repo: Path, base: str, seed_literals: list) -> dict:
    """Судья тестов Kotlin (решение 0.4).

    Тест, написанный тем же агентом, доказательством не является — но
    его отсутствие и его исполнение судятся машинно. Правка
    существующего теста или общих фикстур — путь самосертификации:
    добавленная аннотация расширения JUnit гасит утверждения целого
    класса, поэтому судится не «только добавления», а «только НОВЫЕ
    файлы».
    """
    repo = Path(repo)
    literals = [str(s) for s in (seed_literals or []) if str(s).strip()]
    problems: list = []
    new_files: list = []
    changed: list = []
    resources: list = []
    literals_found: dict = {}

    rows = _git_lines(repo, ["diff", "--name-status", base, "--", _TEST_DIR])
    for row in rows:
        parts = row.split("\t")
        status = parts[0].strip()[:1]
        path = parts[-1].strip().replace("\\", "/")
        if status == "A":
            (resources if path.startswith(_TEST_RES) else new_files).append(
                path)
        else:
            changed.append(f"{status} {path}")

    base_sources = _base_test_sources(repo, base)
    tests_main = sum(len(re.findall(r"@Test\b", t))
                     for t in base_sources.values())
    # Мера «пропущенных не стало больше» считается по ВСЕЙ семье
    # механизмов пропуска JUnit 5, а не по двум аннотациям: условная
    # аннотация даёт в отчёте surefire такой же `skipped`, и узкий счёт
    # делал судью красным по построению (в дереве ни одного
    # `@Disabled`, а surefire стабильно даёт `skipped=1`).
    skipped_main = sum(len(SKIP_ANNOTATION_RE.findall(t))
                       for t in base_sources.values())
    tests_branch, _ = count_test_annotations(repo / _TEST_DIR)

    disabled_added = 0
    kt_new = [p for p in new_files if p.endswith(".kt")]
    for path in kt_new:
        text = ""
        try:
            text = (repo / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            problems.append(f"новый файл теста не читается: {path}")
        if not path.endswith("Test.kt"):
            problems.append(
                f"имя нового файла теста не оканчивается на Test.kt: {path} "
                "— surefire такой класс не исполнит")
        found = [s for s in literals if s and s in text]
        literals_found[path] = found
        if literals and not found:
            problems.append(
                f"в тексте нового теста нет ни одного литерала строки "
                f"посева ({', '.join(literals[:4])}): {path}")
        for token in DISABLED_TOKENS:
            if token in text:
                disabled_added += text.count(token)
                problems.append(
                    f"новый тест не исполняется: в {path} есть «{token}»")

    if not kt_new:
        problems.append(
            "нового файла теста Kotlin нет: на каждый исполнитель решателя "
            "нужен новый файл kotlin-solver/src/test/kotlin/…/<Имя>Test.kt")
    if changed:
        problems.append(
            "существующие файлы тестов решателя правлены (можно только "
            "добавлять новые): " + ", ".join(changed[:6]))
    if tests_branch <= tests_main:
        problems.append(
            f"число @Test не выросло: на базе {tests_main}, на ветке "
            f"{tests_branch}")
    for path in resources:
        name = path.rsplit("/", 1)[-1]
        if name in FORBIDDEN_TEST_RESOURCES \
                or re.match(r"application.*\.properties$", name) \
                or "/META-INF/" in path:
            problems.append(
                f"ресурс тестов гасит утверждения набора: {path}")

    ok = not problems
    words = ("гигиена тестов решателя: новых файлов "
             f"{len(kt_new)}, @Test на базе {tests_main} → на ветке "
             f"{tests_branch}") if ok else \
        "гигиена тестов решателя красная: " + "; ".join(problems)
    return {"ok": ok, "tests_main": tests_main, "tests_branch": tests_branch,
            "new_files": kt_new, "changed_existing": changed,
            "resources": resources, "literals": literals_found,
            "skipped_main": skipped_main, "disabled_added": disabled_added,
            "problems": problems, "words": words}


# --------------------------------------------------------- рост таблиц

def _normalize(value):
    """Кортежи → списки: снимок таблиц ходит через JSON, и `("h","мин")`
    на ветке обязан равняться `["h","мин"]` из файла."""
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def tables_growth(before: dict, after: dict) -> dict:
    """Механический разбор двух снимков `kotlin_tables.tables()` (§4).

    Классификацию делает не человек и не слова резюме агента: имена,
    в которых снимки разошлись, перечисляются поимённо — новое имя,
    пропавшее имя, изменившееся содержимое (смена единицы, отношения,
    порога, текста отказа).
    """
    growth: dict = {}
    a = _normalize(before or {})
    b = _normalize(after or {})
    for key in TABLE_KEYS:
        old, new = a.get(key), b.get(key)
        names: list = []
        if isinstance(old, dict) or isinstance(new, dict):
            old = old if isinstance(old, dict) else {}
            new = new if isinstance(new, dict) else {}
            for name in sorted(set(old) | set(new)):
                if old.get(name) != new.get(name):
                    names.append(str(name))
        elif isinstance(old, list) or isinstance(new, list):
            old = old if isinstance(old, list) else []
            new = new if isinstance(new, list) else []
            names = sorted({str(x) for x in old} ^ {str(x) for x in new})
        elif old != new:
            names = [f"{old!r} → {new!r}"]
        growth[key] = names
    return growth


def growth_class(growth: dict) -> dict:
    """Класс правки по росту таблиц (решение 0.9) — словами.

    Рост таблиц агенту НЕ запрещён: класс К-табл проходит всю цепочку и
    завершается честным красным гейтом паритета живого каталога, а не
    маршрутом «закрывает человек».
    """
    names = [f"{key}: {n}" for key in TABLE_KEYS
             for n in (growth or {}).get(key, [])]
    unknown = [k for k in (growth or {}) if k not in TABLE_KEYS]
    if not names:
        words = ("таблицы решателя не выросли — правка класса "
                 f"{CLASS_ISP} (новое ограничение, форма логики либо "
                 "потребитель известной ручки)")
        return {"class": CLASS_ISP, "names": [], "words": words}
    words = (f"таблицы решателя выросли — правка класса {CLASS_TABL}: "
             + ", ".join(names))
    if unknown:
        words += ("; в снимке есть ключи, которых читатель не знает: "
                  + ", ".join(sorted(unknown)))
    return {"class": CLASS_TABL, "names": names, "words": words}


# --------------------------------------------------- эфемерный решатель

def _limits() -> dict:
    """Пределы живого прогона — импортом стенда, а не литералами."""
    out = {"spent_limit_s": None, "wait_ready_s": None, "poll_s": None,
           "http_timeout_s": None}
    try:
        import inspect

        from vmp import stand_solver as ss
        out["spent_limit_s"] = ss.LIMIT_S
        out["wait_ready_s"] = ss.WAIT_S
        out["poll_s"] = ss.POLL_S
        out["http_timeout_s"] = inspect.signature(
            ss.solver_client).parameters["timeout_s"].default
    except Exception:                                          # noqa: BLE001
        pass
    return out


def _health_client(url: str):
    """Клиент готовности — ТОТ ЖЕ, которым ходит кнопка (SolverHttp).

    Второго HTTP-клиента решателя в дереве нет: ручка живости, коды
    отказа и форма ответа должны быть одни на все пути.
    """
    from vmp.run.button import SolverHttp
    return SolverHttp(url, timeout_s=10.0)


def _spawn(argv: list, *, cwd, log_path: Path, env: dict | None):
    """Долгоживущая JVM с выводом в ФАЙЛ (точка подмены в тестах)."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_path, "ab")
    try:
        proc = subprocess.Popen([str(a) for a in argv], cwd=str(cwd),
                                stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env)
    except Exception:
        fh.close()
        raise
    return proc, fh


class EphemeralSolver:
    """Решатель из артефакта попытки: поднят, опрошен, ОСТАНОВЛЕН.

    Экземпляр живёт время одного запроса на изменение и обязательно
    гаснет: сирота держит открытыми архивы сборки, ломает чистку папки
    попытки и занимает порт. Поэтому остановка — в `finally`, а не «в
    конце удачного пути», и после неё проверяется, что порт свободен.

    Окружение — белый список: решатель читает пакет постановки и
    настройки правил из переменных среды, и наследование среды службы
    сделало бы суд невоспроизводимым.
    """

    def __init__(self, artifact_dir: Path, *, cr_key: str, log_path: Path,
                 java_home=None, xmx: str = XMX_SOLVER_DEFAULT,
                 start_timeout_s: float = START_TIMEOUT_S,
                 tag: str | None = None):
        self.artifact_dir = Path(artifact_dir)
        self.cr_key = str(cr_key)
        #: Признак контура в метке (`contour_tag`): сборщик сирот
        #: соседнего контура нашу JVM по метке не найдёт.
        self.tag = str(tag or "")
        self.marker = MARKER + (self.tag + ":" if self.tag else "") \
            + self.cr_key
        self.log_path = Path(log_path)
        self.java_home = java_home
        self.xmx = str(xmx or XMX_SOLVER_DEFAULT)
        self.start_timeout_s = float(start_timeout_s)
        self.port: int | None = None
        self.url: str | None = None
        self.pid: int | None = None
        self.pid_matches_port: bool | None = None
        self.attempts = 0
        self.started_in_s: float | None = None
        self.stopped: str = "не запускался"
        self.port_free_after: bool | None = None
        self.words: str = ""
        #: Отметка живого экземпляра рядом с журналом: если процесс
        #: приложения снят на полпути, следующий старт видит по ней, кто
        #: остался, а не гадает по перечню процессов.
        self.pid_path = self.log_path.with_name(
            self.log_path.stem + ".pid")
        self._proc = None
        self._fh = None
        self._jvm: list = []

    # ------------------------------------------------------- служебное
    def _run_jar(self) -> Path:
        """Пусковой архив: `quarkus-run.jar` собранной scratch-копии."""
        cand = [self.artifact_dir / "quarkus-run.jar",
                self.artifact_dir / "quarkus-app" / "quarkus-run.jar",
                self.artifact_dir / "target" / "quarkus-app"
                / "quarkus-run.jar"]
        for p in cand:
            if p.exists():
                return p
        raise SolverBuildError(
            "пускового архива решателя нет: quarkus-run.jar не найден в "
            + scrub(str(self.artifact_dir)))

    def _jvm_flags(self, port: int) -> list:
        return ["-Xms128m", f"-Xmx{self.xmx}", "-XX:+UseG1GC",
                "-Dquarkus.http.host=127.0.0.1",
                f"-Dquarkus.http.port={port}",
                self.marker]

    def alive(self) -> bool:
        """JVM ещё жива."""
        return bool(self._proc is not None and self._proc.poll() is None)

    def _write_pid_file(self) -> None:
        """Отметка «экземпляр жив»: PID, порт, адрес, ключ, момент."""
        try:
            self.pid_path.parent.mkdir(parents=True, exist_ok=True)
            self.pid_path.write_text(json.dumps(
                {"pid": self.pid, "port": self.port, "url": self.url,
                 "cr_key": self.cr_key, "tag": self.tag,
                 "marker": self.marker,
                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                ensure_ascii=False), encoding="utf-8")
        except OSError:                             # pragma: no cover
            pass

    def _drop_pid_file(self) -> None:
        """Отметка снимается вместе с решателем — иначе она врала бы."""
        try:
            self.pid_path.unlink(missing_ok=True)
        except OSError:                             # pragma: no cover
            pass

    # ------------------------------------------------------------ старт
    def _try_start(self, port: int) -> tuple:
        run_jar = self._run_jar()
        env = jvm_env(self.java_home, maven_opts="")
        self._jvm = self._jvm_flags(port)
        argv = [java_exe(self.java_home), *self._jvm, "-jar", str(run_jar)]
        started = time.time()
        self._proc, self._fh = _spawn(argv, cwd=run_jar.parent,
                                      log_path=self.log_path, env=env)
        self.pid = int(getattr(self._proc, "pid", 0) or 0)
        url = f"http://127.0.0.1:{port}"
        client = None
        while time.time() - started < self.start_timeout_s:
            if self._proc.poll() is not None:
                return None, ("JVM решателя умерла до готовности (код "
                              f"{self._proc.returncode})")
            if client is None:
                try:
                    client = _health_client(url)
                except Exception as e:                         # noqa: BLE001
                    return None, f"клиент решателя не создан: {e}"
            try:
                if client.health():
                    self.started_in_s = round(time.time() - started, 2)
                    return url, ""
            except Exception:                                  # noqa: BLE001
                pass
            time.sleep(START_POLL_S)
        return None, (f"решатель не поднялся за {self.start_timeout_s} с "
                      "(готовность — ручка /schedules/applied-rules)")

    def start(self) -> str:
        """Поднять решатель; вернуть адрес. Одна повторная попытка.

        Повтор — и при «Address already in use» (порт заняли между
        выбором и стартом: гонка, а не диагноз), и при смерти JVM до
        готовности; второй неудачи ждать нечего — слова и отказ.
        """
        last = ""
        while self.attempts < 2:
            self.attempts += 1
            port = _free_port()
            self.port = port
            try:
                url, words = self._try_start(port)
            except SolverBuildError:
                self.stop()
                raise
            if not url:
                last = words
                self._close(kill=True)
                continue
            owner = _port_owner(port)
            if owner is None:
                # Опрос слушающих сокетов не ответил: догадку за факт не
                # выдаём — в свидетельстве стоит «не проверено».
                self.pid_matches_port = None
                self.words = (f"сверка PID↔порт {port} не состоялась: "
                              "опрос слушающих сокетов не ответил")
            elif owner != self.pid:
                self.pid_matches_port = False
                self.words = (
                    f"порт {port} слушает чужой процесс {owner}, а не "
                    f"наш {self.pid}: суд по такому адресу шёл бы мимо "
                    "собранного артефакта")
                self.stop()
                raise SolverBuildError(self.words)
            else:
                self.pid_matches_port = True
                self.words = (f"эфемерный решатель поднят за "
                              f"{self.started_in_s} с, попыток "
                              f"{self.attempts}")
            self.url = url
            self._write_pid_file()
            return url
        self.stopped = "умер сам"
        self.words = ("эфемерный решатель не поднялся: " + last
                      + "; хвост журнала: " + _tail(self.log_path, 12))
        raise SolverBuildError(self.words)

    # -------------------------------------------------------- остановка
    def _close(self, kill: bool = False) -> None:
        proc, self._proc = self._proc, None
        fh, self._fh = self._fh, None
        if proc is not None and kill and proc.poll() is None:
            _kill_tree(proc.pid)
        if fh is not None:
            try:
                fh.close()
            except OSError:                         # pragma: no cover
                pass

    def stop(self) -> None:
        """Остановить решатель и убедиться, что порт свободен.

        Мягко (`terminate`), затем `taskkill /T /F` — и только после
        этого запись «порт свободен». Идемпотентна: зовётся из
        `__exit__` при любом исходе.
        """
        proc = self._proc
        if proc is None:
            if self.stopped == "не запускался" and self.pid:
                self.stopped = "умер сам"
        else:
            if proc.poll() is not None:
                self.stopped = "умер сам"
            else:
                try:
                    proc.terminate()
                except Exception:                              # noqa: BLE001
                    pass
                try:
                    proc.wait(timeout=STOP_TERMINATE_S)
                    self.stopped = "terminated"
                except Exception:                              # noqa: BLE001
                    _kill_tree(proc.pid)
                    try:
                        proc.wait(timeout=STOP_KILL_S)
                    except Exception:                          # noqa: BLE001
                        pass
                    self.stopped = "killed"
            self._close()
        if self.port:
            # Порт отпускается не мгновенно (соединения стенда уходят в
            # TIME_WAIT): ждём до предела остановки, а не судим по
            # первой же попытке — иначе гейт краснел бы на пустом месте.
            предел = time.time() + STOP_KILL_S
            while True:
                self.port_free_after = _port_free(self.port)
                if self.port_free_after or time.time() >= предел:
                    break
                time.sleep(0.5)
            if self.port_free_after is False:
                self.words = (f"порт {self.port} не освободился за "
                              f"{STOP_KILL_S} с после остановки решателя")
        self._drop_pid_file()

    # ---------------------------------------------------------- рапорт
    def _roots(self) -> list:
        """Корни для скруббера: папка попытки (журнал лежит в ней)."""
        return [(str(self.log_path.parent), "<попытка>")]

    def _banner(self) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        for line in text.splitlines():
            if "started in" in line:
                return scrub(line.strip()[:300], self._roots())
        return ""

    def report(self) -> dict:
        """Блок `run` свидетельства (§4)."""
        limits = _limits()
        limits["start_timeout_s"] = self.start_timeout_s
        return {"url": self.url, "port": self.port, "pid": self.pid,
                "pid_matches_port": self.pid_matches_port,
                "jvm": list(self._jvm), "env": "белый список",
                "banner": self._banner(), "limits": limits,
                "started_in_s": self.started_in_s,
                "attempts": self.attempts,
                "pid_file": self.pid_path.name,
                "artifact_unchanged_after": None,
                "stopped": self.stopped,
                "port_free_after": self.port_free_after,
                "log_tail": _tail(self.log_path, roots=self._roots()),
                "words": self.words}

    # -------------------------------------------------------- контекст
    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Остановка — при ЛЮБОМ исходе тела `with`; исключение наружу не
        # глотается (иначе красный гейт стал бы зелёным).
        self.stop()
        return False


# ------------------------------------------------------------- команда

def _print(text: str) -> None:
    print(text, flush=True)


def _cmd_probe(args) -> int:
    repo = Path(args.repo or REPO)
    data = probe(repo, java_home=args.java_home, mvn=args.mvn,
                 venv_python=args.venv_python)
    _print(data["words"])
    if args.json:
        Path(args.json).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return RC_OK if data["ok"] else RC_NO_TOOLCHAIN


def _cmd_build(args) -> int:
    repo = Path(args.repo or REPO)
    data = build(repo, attempt=Path(args.attempt), source=args.source,
                 commit=args.commit, with_tests=bool(args.with_tests),
                 java_home=args.java_home, mvn=args.mvn,
                 xmx_maven=args.xmx_maven, xmx_fork=args.xmx_fork,
                 m2=args.m2)
    _print(data["words"])
    if args.json:
        Path(args.json).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if not data["available"]:
        return RC_NO_TOOLCHAIN
    return RC_OK if data["ok"] else RC_RED


def _cmd_check(args) -> int:
    """Самопроверка агента: то же вычисление, что у гейта конвейера."""
    repo = Path(args.repo or REPO)
    pr = probe(repo, java_home=args.java_home, mvn=args.mvn)
    _print(pr["words"])
    if not pr["ok"]:
        return RC_NO_TOOLCHAIN
    attempt = Path(args.attempt) if args.attempt else \
        repo / "_repro" / ("check-" + time.strftime("%Y%m%d-%H%M%S"))
    _print(f"папка попытки: {attempt.name}")
    data = build(repo, attempt=attempt, source="tree", commit=None,
                 with_tests=not bool(args.no_tests),
                 java_home=args.java_home, mvn=args.mvn)
    _print(data["words"])
    if not data["available"]:
        return RC_NO_TOOLCHAIN
    if not data["ok"]:
        return RC_RED
    qa = attempt / "kotlin-solver" / "target" / "quarkus-app"
    solver = EphemeralSolver(qa, cr_key="check",
                             log_path=attempt / "solver.log",
                             java_home=java_home_order(
                                 repo, args.java_home)[0],
                             tag=contour_tag(repo))
    rc = RC_RED
    try:
        with solver as url:
            _print(f"эфемерный решатель: {url}")
            stand_log = attempt / "stand.log"
            argv = [clone_python(repo), "-m", "vmp.stand_tasks",
                    "--with-solver", url, "--records", str(args.records),
                    "--kinds", str(args.kinds or ",".join(LIVE_KINDS))]
            if args.catalog:
                argv += ["--catalog", str(args.catalog)]
            # Ключ --write-results НЕ передаётся: стенд отказывает кодом 2
            # на паре --records + --write-results (файл результатов —
            # свидетельство ВСЕГО каталога, и суженный прогон стёр бы
            # исходы прочих записей).
            rc, sec = _run_to_file(argv, cwd=repo, log_path=stand_log,
                                   timeout_s=STAND_TIMEOUT_S)
            _print(_tail(stand_log, 200, roots=[str(repo), str(attempt)]))
            _print(f"стенд записи: код {rc}, {sec} с")
    finally:
        rep = solver.report()
        _print(f"решатель остановлен: {rep['stopped']}, порт свободен: "
               f"{rep['port_free_after']}")
        target = attempt / "kotlin-solver" / "target"
        _rmtree_writable(target)
    return RC_OK if rc == 0 else RC_RED


def main(argv=None) -> int:
    """Команда модуля: `probe | build | check`."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                          # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="python -m vmp.solver_build",
        description="Сборка решателя из ветки и эфемерный решатель-судья")
    sub = ap.add_subparsers(dest="cmd")

    def common(parser):
        # Общие ключи объявляются У КАЖДОЙ команды, а не над ними:
        # значение ключа, поставленного ПЕРЕД именем команды, argparse
        # затирает умолчанием подразбора — тихая потеря --java-home.
        parser.add_argument("--repo", default=None, help="корень дерева")
        parser.add_argument("--java-home", default=None,
                            help="дом Java (иначе порядок поиска §5)")
        parser.add_argument("--mvn", default=None,
                            help="путь Maven (иначе обёртка решателя)")
        return parser

    p = common(sub.add_parser("probe", help="проба тулчейна словами"))
    p.add_argument("--venv-python", default=None,
                   help="python венва клона (импорты стенда)")
    p.add_argument("--json", default=None, help="файл словаря пробы")

    b = common(sub.add_parser("build", help="сборка в папке попытки"))
    b.add_argument("--attempt", required=True, help="папка попытки")
    b.add_argument("--source", choices=("commit", "tree"), default="commit")
    b.add_argument("--commit", default=None, help="коммит ветки (40 hex)")
    b.add_argument("--with-tests", action="store_true",
                   help="прогнать тесты решателя после упаковки")
    b.add_argument("--xmx-maven", default=None,
                   help="память Maven (умолчание 2g)")
    b.add_argument("--xmx-fork", "--xmx", dest="xmx_fork", default=None,
                   help="память форка surefire (умолчание 1g)")
    b.add_argument("--m2", default=None, help="хранилище зависимостей")
    b.add_argument("--json", default=None, help="файл словаря сборки")

    c = common(sub.add_parser("check",
                              help="самопроверка: сборка, решатель, стенд"))
    c.add_argument("--records", required=True, help="запись каталога")
    c.add_argument("--catalog", default=None, help="папка каталога")
    c.add_argument("--kinds", default=None,
                   help="виды через запятую (умолчание — виды решателя)")
    c.add_argument("--attempt", default=None, help="папка попытки")
    c.add_argument("--no-tests", action="store_true",
                   help="без тестов решателя (только упаковка)")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return RC_USAGE
    try:
        if args.cmd == "probe":
            return _cmd_probe(args)
        if args.cmd == "build":
            return _cmd_build(args)
        return _cmd_check(args)
    except SolverBuildError as e:
        _print(str(e))
        return RC_RED


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
