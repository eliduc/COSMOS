# -*- coding: utf-8 -*-
"""Сборщик выпуска для exact-установщика (РВ-143, слово владельца 04.10.2026).

Из ОПУБЛИКОВАННОГО среза (чистый git-репозиторий среза на коммите, который
уйдёт на GitHub) собирает то, что принимает `installer_release.materialize_
release` и `install_cosmos.prepare_exact_install`:

  base.zip      — файлы трёх ролей (producer / consumer / catalog — одно
                  дерево выпуска), КРОМЕ файлов самого установщика: SHA
                  профиля вписывается в установщик после сборки и не должен
                  менять архивы (иначе — кольцо хешей);
  native.zip    — решатель, собранный `tools/build_native.py` (доля COSMOS);
  assets.zip    — данные каталога (`catalog/…`) и два файла привязок;
  manifest.json — `cosmos.exact-release.v1`;
  profile.json  — `cosmos.exact-install-profile.v2` (путь пользователя): манифест,
                  артефакты, примеры и платформы — CPython 3.14, JDK 21 и
                  замок колёс с хешами (`locks/…`, копируется рядом).
                  Прежний v1 (среда ОДНОЙ машины, `--java-home`) — лишь
                  путь разработчика.

Связь тега и ролей (ревью GPT 12:05) не предполагается, а проверяется:
`public_identity.revision` — коммит среза, из которого прочитаны файлы ролей;
`verify_tag_tree` сверяет карту ролей с деревом любого коммита и допускает
разницу лишь в файлах установщика.

Ничего не скачивает и не запускает; ключей не читает. Архивы детерминированы:
порядок имён, время записи и права постоянны — повторная сборка из того же
коммита даёт те же байты.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import installer_release as ir  # noqa: E402

# Файлы установщика и его загрузчиков: службам не нужны, а SHA профиля
# вписывается в них ПОСЛЕ сборки (см. шапку). Список поимённый — новое имя
# установщика без правки здесь в роли попадёт, и `verify_tag_tree` это увидит.
INSTALLER_FILES = frozenset({
    "install_cosmos.py", "installer_release.py", "uninstall_cosmos.py",
    "install_bootstrap.ps1", "uninstall_bootstrap.ps1",
    "INSTALL-COSMOS.cmd", "UNINSTALL-COSMOS.cmd",
    "УСТАНОВИТЬ-COSMOS.cmd", "УДАЛИТЬ-COSMOS.cmd",
    "docs/exact-installer-prepared.md",
    # инструменты самого выпуска — меняются вместе с установщиком до тега
    "tools/build_release.py", "tools/build_native.py", "tools/build_lock.py",
    # замки пакетов по платформам — вход установщика (профиль v2), не служб
    "locks/requirements-lock-Windows-AMD64.txt", "locks/requirements-lock-Linux-x86_64.txt",
})
LOCK_PLATFORMS = ("Windows-AMD64", "Linux-x86_64")
# Не исполняемая часть служб: набор тестов в роли не идёт (в нём — приманки
# гейтов вида поддельных ключей, а службам он не нужен) и, как файлы
# установщика, вправе меняться до тега.
NONRUNTIME_PREFIXES = ("tests/",)


def _outside_roles(name: str) -> bool:
    return name in INSTALLER_FILES or name.startswith(NONRUNTIME_PREFIXES)
# Примеры площадки идут в профиль отдельно (EXACT_EXAMPLES установщика).
EXAMPLE_SOURCES = {"airline_rules": "webapp/config/airline_default.json.example",
                   "agents": "cdpm/agents-default.json.example"}
ASSET_BINDINGS = ("bindings/cosmos-bindings.json", "bindings/cdpm-bindings.json")
NATIVE_SCHEMA = "cosmos.exact-native-build.v1"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)

# Признаки ключей и закрытых ключей в тексте. Гейт среза (`export_gate`)
# уже прошёл; это вторая, независимая проверка байтов, уходящих в архив.
_SECRET = re.compile(
    rb"sk-ant-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}"
    rb"|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}"
    rb"|-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----")


class BuildError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> bytes:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if p.returncode != 0:
        raise BuildError("git %s: %s" % (" ".join(args),
                         p.stderr.decode("utf-8", "replace").strip()[:300]))
    return p.stdout


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def slice_files(slice_dir: Path) -> tuple[str, dict[str, bytes]]:
    """Коммит среза и байты его отслеживаемых файлов — из ОБЪЕКТОВ git, не
    с диска; грязный срез — отказ (собранное должно равняться коммиту)."""
    if _git(slice_dir, "status", "--porcelain", "--untracked-files=no").strip():
        raise BuildError("срез грязный — собирать можно лишь закоммиченное")
    revision = _git(slice_dir, "rev-parse", "HEAD").decode().strip()
    names = [n for n in _git(slice_dir, "ls-files", "-z").decode("utf-8").split("\0") if n]
    files = {}
    for name in names:
        files[name] = _git(slice_dir, "show", "HEAD:" + name)
    return revision, files


def role_map(files: dict[str, bytes]) -> tuple[dict[str, bytes], dict[str, str]]:
    """Файлы ролей и поимённый перечень исключённых с причиной."""
    taken, dropped = {}, {}
    for name in sorted(files):
        if name in INSTALLER_FILES:
            dropped[name] = "файл установщика"
            continue
        if name.startswith(NONRUNTIME_PREFIXES):
            dropped[name] = "не исполняемая часть служб (тесты)"
            continue
        try:
            ir._relative(name)
        except ir.ReleaseError as exc:
            dropped[name] = "правило путей установщика: %s" % exc
            continue
        taken[name] = files[name]
    ir._path_set(list(taken))
    return taken, dropped


def secrets_gate(files: dict[str, bytes]) -> None:
    bad = sorted(n for n, body in files.items() if _SECRET.search(body))
    if bad:
        raise BuildError("гейт ключей: признаки ключа в %d файлах: %s"
                         % (len(bad), ", ".join(bad[:5])))


def markers_gate(files: dict[str, bytes], spec: dict) -> None:
    """Маркеры заказчика — тем же перечнем, что гейт среза и линт набора
    (`tools/customer_markers.py`). Строку находки не печатаем — лишь файл и
    имя проверки: печать маркера сама была бы утечкой."""
    from tools import customer_markers as cm
    checks = [(c.get("name") or c.get("kind"), cm._patterns(c), cm._allow_patterns(c))
              for c in spec.get("checks") or []]
    found = []
    for name, raw in sorted(files.items()):
        if b"\x00" in raw[:4096]:
            continue
        for line in raw.decode("utf-8", "replace").splitlines():
            hit = next((check for check, pats, allows in checks
                        if any(p.search(line) for p in pats)
                        and not any(a.search(line) for a in allows)), None)
            if hit:
                found.append("%s (%s)" % (name, hit))
                break
    if found:
        raise BuildError("гейт маркеров заказчика: %d файлов: %s"
                         % (len(found), ", ".join(found[:5])))


def pack(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            zf.writestr(info, files[name])
    return buf.getvalue()


def pins(files: dict[str, bytes]) -> dict[str, dict]:
    return {n: {"size": len(b), "sha256": _sha(b)} for n, b in sorted(files.items())}


def assets_files(catalog_dir: Path, bindings: dict[str, Path]) -> dict[str, bytes]:
    out = {}
    for p in sorted(catalog_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(catalog_dir).as_posix()
            # Копии-откаты площадки (`.z-…`) рядом с каталогом — не выпуск
            # (опись COSMOS 04.10 12:1x): такое имя внутри — отказ.
            if ".z-" in rel:
                raise BuildError("в активах копия-откат: catalog/" + rel)
            out["catalog/" + rel] = p.read_bytes()
    if "catalog/catalog.json" not in out:
        raise BuildError("в каталоге нет catalog.json")
    for name in ASSET_BINDINGS:
        out[name] = bindings[name].read_bytes()
    return out


def build(slice_dir: Path, origin_commit: str, native_zip: Path, native_artifact: Path,
          catalog_dir: Path, bindings: dict[str, Path], out: Path, *,
          release_id: str, repository: str, runtime: dict | None,
          markers: dict) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[0-9a-f]{40}", origin_commit):
        raise BuildError("коммит происхождения — 40 знаков hex")
    revision, files = slice_files(slice_dir)
    role_files, dropped = role_map(files)
    secrets_gate(role_files)
    for kind, entry in (("producer", "cdpm/webui.py"), ("consumer", "webapp/app.py"),
                        ("catalog", "catalog_service/__main__.py")):
        if entry not in role_files:
            raise BuildError("в роли %s нет %s" % (kind, entry))

    base = pack(role_files)
    native_raw = native_zip.read_bytes()
    # Опись решателя — формат `tools/build_native.py` (доля COSMOS):
    # {"schema": NATIVE_SCHEMA, "artifact": {"sha256", "files"}, "origin_commit", …}.
    native_doc = json.loads(native_artifact.read_text(encoding="utf-8"))
    if native_doc.get("schema") != NATIVE_SCHEMA or not isinstance(native_doc.get("artifact"), dict):
        raise BuildError("native-artifact.json не того вида (нужна %s)" % NATIVE_SCHEMA)
    native_spec = native_doc["artifact"]
    if native_spec.get("sha256") != _sha(native_raw):
        raise BuildError("native.zip не совпадает с native-artifact.json")
    native_origin = native_doc.get("origin_commit")
    if not isinstance(native_origin, str) or not re.fullmatch(r"[0-9a-f]{40}", native_origin):
        raise BuildError("native-artifact.json: нет коммита происхождения")
    asset_files = assets_files(catalog_dir, bindings)
    secrets_gate(asset_files)
    # Активы приходят с площадки, а не из среза: гейт среза их не видел.
    markers_gate(asset_files, markers)
    assets = pack(asset_files)

    role_pins = pins(role_files)
    manifest = {
        "schema": ir.SCHEMA, "release_id": release_id,
        "public_identity": {"repository": repository, "revision": revision},
        "artifacts": {
            "base": {"sha256": _sha(base), "files": role_pins},
            "native": {"sha256": native_spec["sha256"], "files": native_spec["files"]},
            "assets": {"sha256": _sha(assets), "files": pins(asset_files)},
        },
        "roles": {role: {"origin_commit": origin_commit, "base": "base", "overlay": None,
                         "remove": [], "files": role_pins} for role in ir.ROLES},
        "native": {"origin_commit": native_origin, "artifact": "native"},
        "assets": {"artifact": "assets"},
    }
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True)
                    + "\n").encode("utf-8")
    # Самопроверка теми же функциями, что у установщика.
    limits = ir.Limits()
    ir._validate_manifest(manifest_raw, _sha(manifest_raw), limits)
    for name, raw in (("base", base), ("native", native_raw), ("assets", assets)):
        with ir._open_archive(raw, manifest["artifacts"][name]):
            pass

    written = {"manifest.json": manifest_raw, "base.zip": base,
               "native.zip": native_raw, "assets.zip": assets}
    examples = {}
    for kind, src in EXAMPLE_SOURCES.items():
        if src not in files:
            raise BuildError("в срезе нет примера %s" % src)
        name = "examples/" + Path(src).name
        written[name] = files[src]
        examples[kind] = {"path": name, "sha256": _sha(files[src])}
    for name, body in written.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(body)
    result = {"release_id": release_id, "public_revision": revision,
              "origin_commit": origin_commit, "manifest_sha256": _sha(manifest_raw),
              "role_files": len(role_pins), "dropped": dropped,
              "assets": {n: {"size": len(b), "sha256": _sha(b)} for n, b in written.items()}}
    lock_names = ["locks/requirements-lock-%s.txt" % p for p in LOCK_PLATFORMS]
    if runtime is None and all(n in files for n in lock_names):
        # Профиль выпуска v2 (РВ-143 D): одинаков у всех пользователей —
        # платформы, CPython 3.14, JDK 21 и замок колёс с хешами на каждую.
        import install_cosmos
        platforms = {}
        for plat, name in zip(LOCK_PLATFORMS, lock_names):
            install_cosmos._parse_lock(files[name])  # каждая строка — с хешами
            written[name] = files[name]
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            (out / name).write_bytes(files[name])
            result["assets"][name] = {"size": len(files[name]), "sha256": _sha(files[name])}
            platforms[plat] = {"python": install_cosmos.RUNTIME_PYTHON,
                               "java": install_cosmos.RUNTIME_JAVA,
                               "lock": {"path": name, "sha256": _sha(files[name])}}
        profile = {"schema": install_cosmos.EXACT_PROFILE_SCHEMA_V2,
                   "qualification_id": release_id,
                   "manifest": {"path": "manifest.json", "sha256": _sha(manifest_raw)},
                   "artifacts": {"base": "base.zip", "native": "native.zip",
                                 "assets": "assets.zip"},
                   "platforms": platforms, "examples": examples}
        profile_raw = (json.dumps(profile, ensure_ascii=False, indent=1, sort_keys=True)
                       + "\n").encode("utf-8")
        install_cosmos._exact_profile_v2(json.loads(profile_raw))
        (out / "profile.json").write_bytes(profile_raw)
        result["profile_sha256"] = _sha(profile_raw)
        result["assets"]["profile.json"] = {"size": len(profile_raw),
                                            "sha256": result["profile_sha256"]}
    elif runtime is not None:
        profile = {"schema": "cosmos.exact-install-profile.v1", "qualification_id": release_id,
                   "manifest": {"path": "manifest.json", "sha256": _sha(manifest_raw)},
                   "artifacts": {"base": "base.zip", "native": "native.zip",
                                 "assets": "assets.zip"},
                   "runtime": runtime, "examples": examples}
        profile_raw = (json.dumps(profile, ensure_ascii=False, indent=1, sort_keys=True)
                       + "\n").encode("utf-8")
        (out / "profile.json").write_bytes(profile_raw)
        result["profile_sha256"] = _sha(profile_raw)
        result["assets"]["profile.json"] = {"size": len(profile_raw),
                                            "sha256": result["profile_sha256"]}
    return result


def _tree(repo: Path, commit: str) -> dict[str, tuple[str, str, str]]:
    """{путь: (режим, тип, объект)} дерева коммита: смена режима или типа
    (обычный файл → ссылка с тем же блобом) — тоже изменение (ревью GPT 12:42)."""
    out = {}
    for row in _git(repo, "ls-tree", "-r", "-z", commit).decode("utf-8").split("\0"):
        if row:
            meta, name = row.split("\t", 1)
            mode, kind, obj = meta.split()
            out[name] = (mode, kind, obj)
    return out


def verify_tag_tree(repo: Path, commit: str, manifest: dict) -> list[str]:
    """Расхождения дерева `commit` (тега) с выпуском (пусто = связь доказана):
    каждый файл карты ролей есть в дереве с тем же sha256; всякий иной файл
    дерева — либо файл установщика (лишь им тег вправе отличаться от коммита
    сборки), либо тот же блоб, что в коммите сборки `public_identity.revision`
    (так отброшенные правилом путей файлы не могут меняться молча)."""
    built = manifest["public_identity"]["revision"]
    tag, base = _tree(repo, commit), _tree(repo, built)
    role = manifest["roles"]["producer"]["files"]
    problems = []
    for name, pin in role.items():
        entry = tag.get(name)
        if entry is None:
            problems.append("нет в дереве: " + name)
        elif entry[1] != "blob" or base.get(name, (None,))[0] != entry[0]:
            problems.append("иной режим или тип: " + name)
        elif _sha(_git(repo, "cat-file", "blob", entry[2])) != pin["sha256"]:
            problems.append("иной sha256: " + name)
    # Обход ОБЪЕДИНЕНИЯ деревьев: удаление файла вне ролей тоже изменение
    # (ревью GPT 12:42). Исключение — лишь поимённые файлы установщика и
    # набор тестов (NONRUNTIME_PREFIXES): в роли они не входят.
    for name in sorted(set(tag) | set(base)):
        if name in role or _outside_roles(name):
            continue
        if tag.get(name) != base.get(name):
            what = ("удалён" if name not in tag else "добавлен" if name not in base
                    else "изменён")
            problems.append("%s вне ролей: %s" % (what, name))
    return problems


def main_verify(argv) -> int:
    """`build_release.py verify --slice <срез> --commit <тег> --manifest <файл>`:
    шаг процедуры выпуска ПЕРЕД тегом и после него — связь тега и ролей."""
    ap = argparse.ArgumentParser(prog="build_release.py verify")
    ap.add_argument("--slice", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--manifest", required=True)
    a = ap.parse_args(argv)
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    problems = verify_tag_tree(Path(a.slice), a.commit, manifest)
    for p in problems:
        print("  " + p)
    print("СВЯЗЬ ТЕГА И РОЛЕЙ: %s" % ("доказана" if not problems
                                     else "РАЗОРВАНА (%d)" % len(problems)))
    return 0 if not problems else 2


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["verify"]:
        return main_verify(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--slice", required=True, help="чистый git-репозиторий среза")
    ap.add_argument("--origin-commit", required=True, help="коммит выпуска в рабочем репо")
    ap.add_argument("--native-zip", required=True)
    ap.add_argument("--native-artifact", required=True)
    ap.add_argument("--catalog-dir", required=True)
    ap.add_argument("--cosmos-bindings", required=True)
    ap.add_argument("--cdpm-bindings", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--release-id", default="cosmos-1.4.10")
    ap.add_argument("--repository", default="eliduc/COSMOS")
    ap.add_argument("--java-home", help="JDK 21 эталонной среды: профиль с её отпечатком")
    a = ap.parse_args(argv)
    runtime = None
    if a.java_home:
        import install_cosmos
        runtime = install_cosmos._exact_runtime(Path(a.java_home))
    from tools import customer_markers
    markers = customer_markers.load_checks()
    if not markers:
        # Машина, собирающая выпуск, обязана нести перечень (fail-closed).
        print("СБОРКА ОТКАЗАНА: нет перечня маркеров заказчика")
        return 2
    try:
        result = build(Path(a.slice), a.origin_commit, Path(a.native_zip),
                       Path(a.native_artifact), Path(a.catalog_dir),
                       {ASSET_BINDINGS[0]: Path(a.cosmos_bindings),
                        ASSET_BINDINGS[1]: Path(a.cdpm_bindings)},
                       Path(a.out), release_id=a.release_id, repository=a.repository,
                       runtime=runtime, markers=markers)
    except (BuildError, ir.ReleaseError, OSError, ValueError) as exc:
        print("СБОРКА ОТКАЗАНА: %s" % exc)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
