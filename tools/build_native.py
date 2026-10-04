"""Архив «native» выпуска (РВ-143, РВ-143а): quarkus-app решателя для GitHub Release.

Собирает kotlin-solver обёрткой Maven (версия Maven закреплена в
kotlin-solver/.mvn/wrapper/maven-wrapper.properties) во временной папке.
Вход сборки — байты git (git archive HEAD), не файлы с диска: игнорируемые и
незакоммиченные файлы в сборку не попадают. Пакует quarkus-app/ в ZIP, который
принимает installer_release.materialize_release:
  - имена «quarkus-app/...», лишь обычные файлы, без записей каталогов;
  - порядок и время записей постоянны (1980-01-01), права 0644;
  - карта файлов (размер, sha256) — полная.

Гейт «нет ключей и данных площадки»:
  - имена архива и имена записей внутри jar — правилами installer_release
    (_path_set / _relative: приватные пути, данные площадки);
  - файлы вне lib/ и записи jar приложения — лишь из белого списка расширений,
    их текст — без признаков ключей;
  - происхождение доказывается, а не предполагается: каждый jar в lib/ —
    побайтно артефакт из quarkus-app-dependencies.txt в кэше Maven, и его sha1
    равна сумме, скачанной с Central (*.jar.sha1); ресурсы, переложенные
    Quarkus в «META-INF/<группа>_<артефакт>__jar/», — побайтно записи такого же
    проверенного артефакта; изменённые при сборке (страница и значки интерфейса
    OpenAPI) — судятся как свои и перечисляются поимённо. Иначе — отказ.

Итог — native.zip, native-artifact.json (sha256 архива, карта файлов, коммит
происхождения, версия Maven и JDK) и native-provenance.json (координаты Maven
каждого стороннего файла и, по умолчанию, адрес и sha1 его суммы на Central,
сверенной по сети сейчас). Сеть: Maven-обёртка (ключ -C — строгие суммы) и
чтение *.jar.sha1 с repo.maven.apache.org; --no-central-check — без сети, тогда
в описи — граница доверия к кэшу.

    python tools/build_native.py --java-home "C:\\Program Files\\Eclipse Adoptium\\jdk-21..." --out DIR [--work DIR] [--m2 DIR]
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import installer_release as ir  # noqa: E402

SCHEMA = "cosmos.exact-native-build.v1"
SOLVER = "kotlin-solver"
# Вход сборки: исходники и закреплённая обёртка, без target/.
SOLVER_INPUTS = ("pom.xml", "mvnw", "mvnw.cmd", ".mvn", "src")
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
# Вне lib/ и внутри jar приложения допустимы лишь эти виды файлов.
# Веб-файлы — интерфейс OpenAPI, который Quarkus вкладывает в generated-bytecode.jar.
WEB = (".html", ".js", ".css", ".map", ".ico", ".png", ".svg", ".woff", ".woff2")
ALLOWED = (".jar", ".dat", ".class", ".kotlin_module", ".xml", ".properties",
           ".mf", ".json", ".idx", ".list", ".txt", ".yaml", ".yml") + WEB
TEXT = (".xml", ".properties", ".mf", ".json", ".list", ".txt", ".yaml", ".yml", ".idx",
        ".html", ".js", ".css", ".svg")
SECRET = re.compile(
    rb"sk-ant-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{32,}|AKIA[0-9A-Z]{16}"
    rb"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    rb"|(?i:(?:api[_-]?key|secret|password|token)['\"]?\s*[=:]\s*['\"]?[A-Za-z0-9_\-./+]{12,})")
DEPS = "quarkus-app/quarkus-app-dependencies.txt"
DEP_LINE = re.compile(r"^([^:\s]+):([^:\s]+):([^:\s]*):jar:([^:\s]+)$")
RELOCATED = re.compile(r"^META-INF/([^/_]+)_([^/]+)__jar/(.+)$")


class BuildError(RuntimeError):
    """Сборка или гейт отказали; архив не выпущен."""


def _git(*args: str) -> str:
    p = subprocess.run(["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                        "-C", str(ROOT), *args], capture_output=True, text=True)
    if p.returncode:
        raise BuildError("git %s: %s" % (" ".join(args), p.stderr.strip()))
    return p.stdout.strip()


def origin_commit() -> str:
    """Коммит, чьи байты собираются; правки решателя вне коммита — отказ (сборка их не увидит)."""
    if _git("status", "--porcelain", "--", SOLVER):
        raise BuildError("в %s есть незакоммиченные правки" % SOLVER)
    return _git("rev-parse", "HEAD")


def maven_distribution() -> str:
    props = _git("show", "HEAD:%s/.mvn/wrapper/maven-wrapper.properties" % SOLVER)
    m = re.search(r"^distributionUrl=(\S+)$", props, re.M)
    if not m:
        raise BuildError("нет distributionUrl в обёртке Maven")
    return m.group(1)


def java_version(java_home: Path) -> str:
    release = (java_home / "release").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'^JAVA_VERSION="([^"]+)"', release, re.M)
    if not m or not m.group(1).startswith("21"):
        raise BuildError("нужен JDK 21: %s" % java_home)
    return m.group(1)


def export(work: Path, commit: str = "HEAD") -> Path:
    """Вход сборки из объектов git (git archive), не с диска."""
    p = subprocess.run(["git", "-c", "gc.auto=0", "-C", str(ROOT), "archive", "--format=tar", commit,
                        "--", *("%s/%s" % (SOLVER, n) for n in SOLVER_INPUTS)], capture_output=True)
    if p.returncode:
        raise BuildError("git archive: %s" % p.stderr.decode("utf-8", "replace").strip())
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
        for m in tar.getmembers():
            if not (m.isfile() or m.isdir()):
                raise BuildError("в решателе не файл и не каталог: %s" % m.name)
        tar.extractall(work, filter="data")
    return work / SOLVER


def build(java_home: Path, work: Path, commit: str = "HEAD") -> Path:
    """mvnw package на байтах git во временной папке; возвращает её quarkus-app/."""
    src = export(work, commit)
    wrapper = src / ("mvnw.cmd" if os.name == "nt" else "mvnw")
    if os.name != "nt":
        wrapper.chmod(0o755)
    env = dict(os.environ, JAVA_HOME=str(java_home))
    p = subprocess.run([str(wrapper), "-B", "-ntp", "-C", "package", "-DskipTests"],
                       cwd=src, env=env, capture_output=True, text=True,
                       shell=(os.name == "nt"))
    if p.returncode:
        raise BuildError("сборка решателя: код %d\n%s" % (p.returncode, p.stdout[-4000:]))
    app = src / "target" / "quarkus-app"
    if not (app / "quarkus-run.jar").is_file():
        raise BuildError("нет target/quarkus-app/quarkus-run.jar")
    return app


def collect(app: Path) -> dict[str, bytes]:
    files = {}
    for p in sorted(app.rglob("*")):
        if p.is_symlink():
            raise BuildError("ссылка в quarkus-app: %s" % p)
        if p.is_file():
            files["quarkus-app/" + p.relative_to(app).as_posix()] = p.read_bytes()
    return files


def _check_text(name: str, data: bytes) -> None:
    m = SECRET.search(data)
    if m:
        raise BuildError("признак ключа: %s (%s…)" % (name, m.group(0)[:10].decode("ascii", "replace")))


def _jar_name(a: str, v: str, cls: str) -> str:
    return "%s-%s%s.jar" % (a, v, "-" + cls if cls else "")


def central(m2: Path, g: str, a: str, v: str, cls: str = "") -> bytes:
    """Артефакт из кэша Maven, чья sha1 равна сумме *.jar.sha1 рядом с ним
    (её Maven скачал с Central). Сама по себе пара кэша — граница доверия к
    кэшу; связь с Central доказывает central_online()."""
    jar = m2.joinpath(*g.split("."), a, v, _jar_name(a, v, cls))
    sha1 = jar.with_name(jar.name + ".sha1")
    if not (jar.is_file() and sha1.is_file()):
        raise BuildError("нет в кэше Maven с суммой Central: %s:%s:%s" % (g, a, v))
    data = jar.read_bytes()
    want = (sha1.read_text(encoding="ascii", errors="replace").split() or [""])[0].lower()
    if hashlib.sha1(data).hexdigest() != want:
        raise BuildError("sha1 не равна сумме Central: %s:%s:%s" % (g, a, v))
    return data


CENTRAL = "https://repo.maven.apache.org/maven2"


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 — лишь https Central
        return r.read(256).decode("ascii", "replace")


def central_online(m2: Path, coords: set, base: str = CENTRAL, fetch=_fetch_text) -> dict:
    """Для каждого артефакта: sha1 его байтов в кэше = сумма, опубликованная
    на Central сейчас (тот же путь + .sha1). Возвращает {координаты: {url, sha1}}."""
    def one(c):
        g, a, v, cls = c
        name = _jar_name(a, v, cls)
        url = "%s/%s/%s/%s/%s.sha1" % (base, g.replace(".", "/"), a, v, name)
        got = hashlib.sha1(m2.joinpath(*g.split("."), a, v, name).read_bytes()).hexdigest()
        try:
            remote = (fetch(url).split() or [""])[0].lower()
        except OSError as exc:
            raise BuildError("Central недоступен для %s: %s" % (url, exc)) from None
        if remote != got:
            raise BuildError("sha1 в кэше не равна опубликованной на Central: %s" % url)
        return ":".join(x for x in c if x), {"url": url, "sha1": got}
    with ThreadPoolExecutor(8) as ex:
        return dict(sorted(ex.map(one, sorted(coords))))


def dependencies(files: dict[str, bytes]) -> dict[str, tuple]:
    """Имя файла в lib/ → (группа, артефакт, версия, классификатор) по quarkus-app-dependencies.txt."""
    if DEPS not in files:
        raise BuildError("нет %s" % DEPS)
    out = {}
    for line in files[DEPS].decode("utf-8").splitlines():
        if not line.strip():
            continue
        m = DEP_LINE.match(line.strip())
        if not m:
            raise BuildError("строка зависимостей не разобрана: %s" % line[:120])
        g, a, cls, v = m.groups()
        out["%s.%s" % (g, _jar_name(a, v, cls))] = (g, a, v, cls)
    return out


class _Relocated:
    """Ресурсы зависимости, переложенные Quarkus: побайтно — запись проверенного артефакта."""

    def __init__(self, m2: Path):
        self.m2, self.jars, self.found, self.changed, self.coords = m2, {}, {}, [], set()

    def _candidates(self, g: str, a: str) -> list:
        if (g, a) not in self.jars:
            base = self.m2.joinpath(*g.split("."), a)
            versions = sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
            self.jars[(g, a)] = []
            for v in versions:
                try:
                    self.jars[(g, a)].append((v, zipfile.ZipFile(io.BytesIO(central(self.m2, g, a, v)))))
                except BuildError:
                    continue
        return self.jars[(g, a)]

    def prove(self, where: str, g: str, a: str, rest: str, data: bytes) -> None:
        """Равна записи артефакта Central — доказано. Иначе (Quarkus подменил
        страницу или значок интерфейса OpenAPI) — артефакт обязан существовать,
        а запись судится как своя: текст — без признаков ключей, двоичное — лишь
        картинка; имя попадает в опись «изменено при сборке»."""
        candidates = self._candidates(g, a)
        if not candidates:
            raise BuildError("происхождение не доказано: %s (нет артефакта %s:%s с суммой Central)" % (where, g, a))
        for v, jar in candidates:
            try:
                same = jar.read(rest) == data
            except KeyError:
                continue
            if same:
                self.found.setdefault("%s:%s:%s" % (g, a, v), []).append(where)
                self.coords.add((g, a, v, ""))
                return
        low = rest.lower()
        if low.endswith(TEXT):
            _check_text(where, data)
        elif not low.endswith((".ico", ".png")):
            raise BuildError("происхождение не доказано: %s (двоичная запись не равна артефакту %s:%s)" % (where, g, a))
        self.changed.append(where)


def _check_app_jar(name: str, data: bytes, relocated: _Relocated) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as jar:
        for info in jar.infolist():
            if info.is_dir():
                continue
            where = name + "!" + info.filename
            try:
                ir._relative(info.filename)  # приватные пути и данные площадки — и внутри jar
            except ir.ReleaseError as exc:
                raise BuildError("путь внутри jar отклонён (%s): %s" % (exc, where)) from None
            low = info.filename.lower()
            service = low.startswith("meta-inf/services/")  # списки ServiceLoader, без расширения
            if not (low.endswith(ALLOWED) or service):
                raise BuildError("вид файла вне списка: %s" % where)
            m = RELOCATED.match(info.filename)
            if m:
                relocated.prove(where, m.group(1), m.group(2), m.group(3), jar.read(info))
            elif low.endswith(TEXT) or service:
                _check_text(where, jar.read(info))


def gate(files: dict[str, bytes], m2: Path, online=None) -> dict:
    """Гейт архива; возвращает доказанное происхождение сторонних файлов.
    online — central_online (или его заглушка в тестах); None — граница
    доверия к кэшу Maven, она пишется в опись словами."""
    ir._path_set(list(files))
    deps = dependencies(files)
    relocated = _Relocated(m2)
    lib, coords = {}, set()
    for name, data in files.items():
        low = name.lower()
        if not low.endswith(ALLOWED):
            raise BuildError("вид файла вне списка: %s" % name)
        if low.startswith("quarkus-app/lib/"):
            coord = deps.get(name.rsplit("/", 1)[-1])
            if coord is None or not low.endswith(".jar"):
                raise BuildError("в lib/ файл вне списка зависимостей: %s" % name)
            if central(m2, *coord) != data:
                raise BuildError("jar в lib/ не равен артефакту Maven: %s" % name)
            lib[name] = ":".join(c for c in coord if c)
            coords.add(coord)
        elif low.endswith(".jar"):
            _check_app_jar(name, data, relocated)
        elif low.endswith(TEXT):
            _check_text(name, data)
    coords |= relocated.coords
    if online is None:
        check = {"mode": "local-cache-only",
                 "boundary": "sha1 сверены лишь с *.jar.sha1 в кэше Maven; связь с Central не проверена"}
    else:
        check = {"mode": "central-online", "base": CENTRAL, "artifacts": online(m2, coords)}
    return {"central_check": check, "lib": dict(sorted(lib.items())),
            "relocated": {k: sorted(v) for k, v in sorted(relocated.found.items())},
            "relocated_changed_by_build": sorted(relocated.changed)}


def pack(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            z.writestr(info, files[name])
    return buf.getvalue()


def file_map(files: dict[str, bytes]) -> dict[str, dict]:
    return {n: {"size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in sorted(files.items())}


def self_check(raw: bytes, spec: dict) -> None:
    """Архив принимает тот же разбор, что и установщик."""
    ir._file_map(spec["files"], ir.Limits())
    ir._open_archive(raw, spec).close()


def _dump(doc: dict) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--java-home", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--work", type=Path, default=None,
                    help="где вести временную сборку (по умолчанию — системный TEMP)")
    ap.add_argument("--m2", type=Path, default=Path.home() / ".m2" / "repository",
                    help="кэш Maven, где сверяется происхождение сторонних jar")
    ap.add_argument("--no-central-check", action="store_true",
                    help="не сверять суммы с Central по сети; граница доверия к кэшу пишется в опись")
    a = ap.parse_args(argv)
    out = a.out
    if out.exists() and any(out.iterdir()):
        raise BuildError("каталог выхода не пуст: %s" % out)
    commit = origin_commit()
    java = java_version(a.java_home)
    with tempfile.TemporaryDirectory(prefix="cosmos-native-", dir=a.work) as tmp:
        files = collect(build(a.java_home, Path(tmp), commit))
    provenance = gate(files, a.m2, None if a.no_central_check else central_online)
    raw = pack(files)
    spec = {"sha256": hashlib.sha256(raw).hexdigest(), "files": file_map(files)}
    self_check(raw, spec)
    doc = {"schema": SCHEMA, "origin_commit": commit, "maven_distribution": maven_distribution(),
           "java_version": java, "artifact": spec}
    out.mkdir(parents=True, exist_ok=True)
    (out / "native.zip").write_bytes(raw)
    (out / "native-artifact.json").write_bytes(_dump(doc))
    (out / "native-provenance.json").write_bytes(_dump(
        {"schema": "cosmos.exact-native-provenance.v1", "origin_commit": commit,
         "native_sha256": spec["sha256"], **provenance}))
    print("native.zip %d Б sha256 %s; файлов %d; коммит %s; в lib/ доказано %d, переложенных записей %d"
          % (len(raw), spec["sha256"], len(files), commit, len(provenance["lib"]),
             sum(len(v) for v in provenance["relocated"].values())))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print("ОТКАЗ: %s" % exc, file=sys.stderr)
        raise SystemExit(2)
