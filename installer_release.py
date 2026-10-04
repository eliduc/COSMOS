"""Local, non-activating materializer for an exact COSMOS release.

No network, Git, subprocesses, imports from payloads, dependency installation or
service operations occur here. The caller supplies an independently trusted
SHA-256 of the UTF-8 manifest and local ZIP artifacts. Publication/content review
is a separate prerequisite: hashes prove identity, not absence of secrets in
arbitrary source text or nested JAR resources.

Manifest schema ``cosmos.exact-release.v1`` (all keys are required):

    {"schema": "cosmos.exact-release.v1", "release_id": "release-01",
     "public_identity": {"repository": "eliduc/COSMOS", "revision": "<40 hex>"},
     "artifacts": {"base": {"sha256": "<64 hex>",
                            "files": {"module.py": {"size": 12,
                                                    "sha256": "<64 hex>"}}}},
     "roles": {"producer": {"origin_commit": "<40 hex>", "base": "base",
                            "overlay": null, "remove": [], "files": {...}},
               "consumer": {...}, "catalog": {...}},
     "native": {"origin_commit": "<40 hex>", "artifact": "native"},
     "assets": {"artifact": "assets"}}

``files`` is the complete final map for each role. An overlay can add/replace
files; ``remove`` names existing base files and must not overlap its overlay.
Every artifact must be referenced. ZIPs contain regular file entries only,
without directory entries, links, extra members or case-insensitive collisions.
All hexadecimal digests and commit identifiers use canonical lowercase.
All relative names follow Windows-safe rules on every platform. Native/assets
use their artifact's complete map. Assets are limited to catalog data and the
two bindings files: site settings and TEST qualification are not distributed.

Origin commits describe private provenance, not public-fetchable Git commits.
The independent public_identity describes the sanitized published release.

``materialize_release`` creates only an absent, INACTIVE destination. It first
validates all bytes, builds in a unique sibling staging directory, then reserves
the destination using exclusive mkdir and moves the completed role directories.
This final transfer is intentionally not an activation/atomic active-pointer
switch. Directory existence is NOT success: use the returned receipt. Any
failure removes only entries created by this invocation; pre-existing targets
are never merged, replaced or removed. The caller owns activation and rollback.
The destination parent and input files must remain under trusted local control
during the call; this is not a hostile concurrent-filesystem sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Mapping
import zipfile


SCHEMA = "cosmos.exact-release.v1"
ROLES = ("producer", "consumer", "catalog")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_DEVICES = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"}
_DEVICES.update(f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10))
_DEVICES.update(f"{prefix}{i}" for prefix in ("com", "lpt") for i in "¹²³")
_FORBIDDEN_PARTS = {
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", "node_modules",
    "secrets", "credentials", "cdpm-runs", "cosmos-runs", "task-inbox",
}
_FORBIDDEN_PREFIXES = ("webapp/config/", "webapp/jobs/", "data/uploads/", "settings/")


class ReleaseError(ValueError):
    """Rejected release or unsafe destination; no activation was performed."""


@dataclass(frozen=True)
class Limits:
    manifest_bytes: int = 16 * 1024 * 1024
    archive_bytes: int = 512 * 1024 * 1024
    total_archive_bytes: int = 1024 * 1024 * 1024
    file_bytes: int = 256 * 1024 * 1024
    total_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024
    files: int = 30000


@dataclass(frozen=True)
class MaterializedRelease:
    release_id: str
    manifest_sha256: str
    public_repository: str
    public_revision: str
    destination: Path
    producer: Path
    consumer: Path
    catalog: Path
    native: Path
    assets: Path
    # tuples avoid returning mutable manifest dictionaries as a trust receipt.
    origins: tuple[tuple[str, str], ...]


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


def _keys(value: object, keys: set[str], code: str) -> dict:
    _need(type(value) is dict and set(value) == keys, code)
    return value


def _digest(value: object, size: int = 64) -> str:
    _need(type(value) is str and bool((_HEX64 if size == 64 else _HEX40).fullmatch(value)),
          "invalid-digest")
    return value


def _unique_json(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate-json-key")
        result[key] = value
    return result


def _relative(name: object) -> str:
    _need(type(name) is str and 0 < len(name) <= 240, "invalid-relative-path")
    _need(not any(ord(c) < 32 or ord(c) == 127 or c in '\\:<>"|?*' for c in name),
          "invalid-relative-path")
    parts = name.split("/")
    for part in parts:
        _need(part not in ("", ".", "..") and part == part.strip()
              and not part.endswith("."), "invalid-relative-path")
        _need(part.split(".", 1)[0].casefold() not in _DEVICES, "reserved-path")
        low = part.casefold()
        _need(low not in _FORBIDDEN_PARTS and not low.startswith(".env")
              and low not in ("id_rsa", "id_ed25519", "users.db", "users.db-wal", "users.db-shm")
              and low not in (".npmrc", ".pypirc", ".netrc", ".git-credentials", "credentials.json", "secrets.json")
              and not (low.startswith("channel-token-") and low.endswith(".txt"))
              and not low.endswith((".pem", ".key", ".pfx", ".p12")), "private-path")
    low = name.casefold()
    _need(not low.startswith(_FORBIDDEN_PREFIXES)
          and not (low.endswith(".json") and
                   ("executors-registry" in low or "qualification" in low.split("/")[-1])),
          "site-state-path")
    return name


def _path_set(names: list[str]) -> None:
    """Reject aliases and file/directory collisions, including parent casing."""
    observed: dict[str, str] = {}
    files = set()
    for name in names:
        _relative(name)
        parts = name.split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            key = prefix.casefold()
            _need(key not in observed or observed[key] == prefix, "case-collision")
            observed[key] = prefix
        key = name.casefold()
        _need(key not in files, "duplicate-path")
        files.add(key)
    for name in names:
        parts = name.split("/")
        _need(not any("/".join(parts[:i]).casefold() in files
                      for i in range(1, len(parts))), "file-directory-collision")


def _file_map(value: object, limits: Limits) -> dict[str, dict]:
    _need(type(value) is dict and 0 < len(value) <= limits.files, "invalid-file-map")
    _path_set(list(value))
    for pin in value.values():
        _keys(pin, {"size", "sha256"}, "invalid-file-pin")
        _need(type(pin["size"]) is int and 0 <= pin["size"] <= limits.file_bytes,
              "invalid-file-size")
        _digest(pin["sha256"])
    return value


def _plain(path: Path, *, directory: bool = False) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseError("missing-local-path") from exc
    _need(not stat.S_ISLNK(info.st_mode)
          and not getattr(info, "st_file_attributes", 0) & 0x400, "local-link")
    _need(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
          "local-path-type")
    return info


def _parents(path: Path) -> None:
    for parent in reversed(path.parents):
        _plain(parent, directory=True)


def _read_file(path: Path, limit: int) -> bytes:
    _parents(path)
    info = _plain(path)
    _need(info.st_size <= limit, "input-size-limit")
    with path.open("rb") as stream:
        # Read the stated size + 1 (growth is still caught below), never the
        # limit: read(n) preallocates n bytes — 256 MiB per small file cost
        # 65 ms each and made a 7465-file site take 31 minutes (04.10.2026).
        data = stream.read(info.st_size + 1)
    _need(len(data) <= limit and len(data) == info.st_size, "input-size-drift")
    return data


def _validate_manifest(raw: bytes, expected: str, limits: Limits) -> dict:
    _digest(expected)
    _need(hashlib.sha256(raw).hexdigest() == expected, "manifest-hash")
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReleaseError("invalid-json") from exc
    _keys(doc, {"schema", "release_id", "public_identity", "artifacts", "roles", "native", "assets"},
          "manifest-keys")
    _need(doc["schema"] == SCHEMA, "manifest-schema")
    _need(type(doc["release_id"]) is str and bool(_ID.fullmatch(doc["release_id"])), "release-id")
    pub = _keys(doc["public_identity"], {"repository", "revision"}, "public-identity")
    _need(type(pub["repository"]) is str and bool(_REPO.fullmatch(pub["repository"]))
          and all(p not in (".", "..") for p in pub["repository"].split("/")), "public-repository")
    _digest(pub["revision"], 40)
    artifacts = doc["artifacts"]
    _need(type(artifacts) is dict and 1 <= len(artifacts) <= 12, "artifacts-map")
    total_size = total_files = 0
    for name, artifact in artifacts.items():
        _need(type(name) is str and bool(_ID.fullmatch(name)), "artifact-id")
        _keys(artifact, {"sha256", "files"}, "artifact-keys")
        _digest(artifact["sha256"])
        pins = _file_map(artifact["files"], limits)
        total_files += len(pins)
        total_size += sum(p["size"] for p in pins.values())
    _need(total_files <= limits.files and total_size <= limits.total_uncompressed_bytes,
          "release-size-limit")
    _keys(doc["roles"], set(ROLES), "roles-map")
    used = set()
    for role in ROLES:
        spec = _keys(doc["roles"][role], {"origin_commit", "base", "overlay", "remove", "files"},
                     "role-keys")
        _digest(spec["origin_commit"], 40)
        _need(type(spec["base"]) is str and spec["base"] in artifacts, "role-base")
        _need(spec["overlay"] is None or (type(spec["overlay"]) is str
              and spec["overlay"] in artifacts and spec["overlay"] != spec["base"]), "role-overlay")
        _need(type(spec["remove"]) is list, "role-removals")
        _path_set(spec["remove"])
        expected_map = dict(artifacts[spec["base"]]["files"])
        used.add(spec["base"])
        overlay = {} if spec["overlay"] is None else artifacts[spec["overlay"]]["files"]
        for remove in spec["remove"]:
            _need(remove in expected_map and remove not in overlay, "invalid-removal")
            del expected_map[remove]
        expected_map.update(overlay)
        if spec["overlay"] is not None:
            used.add(spec["overlay"])
        final = _file_map(spec["files"], limits)
        _need(final == expected_map, "role-final-map")
    native = _keys(doc["native"], {"origin_commit", "artifact"}, "native-keys")
    _digest(native["origin_commit"], 40)
    assets = _keys(doc["assets"], {"artifact"}, "assets-keys")
    for component in (native, assets):
        name = component["artifact"]
        _need(type(name) is str and name in artifacts and name not in used, "component-artifact")
        used.add(name)
    for name in artifacts[assets["artifact"]]["files"]:
        _need(name.startswith("catalog/") or name in
              ("bindings/cdpm-bindings.json", "bindings/cosmos-bindings.json"), "non-runtime-asset")
    _need(used == set(artifacts), "unused-artifact")
    # Limit materialized size too: an artifact may be reused by all three roles.
    output_maps = [doc["roles"][r]["files"] for r in ROLES] + [
        artifacts[native["artifact"]]["files"], artifacts[assets["artifact"]]["files"]]
    _need(sum(len(m) for m in output_maps) <= limits.files
          and sum(p["size"] for m in output_maps for p in m.values())
          <= limits.total_uncompressed_bytes, "output-size-limit")
    return doc


def _member(archive: zipfile.ZipFile, name: str, pin: dict) -> bytes:
    try:
        with archive.open(name) as stream:
            data = stream.read(pin["size"] + 1)
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as exc:
        raise ReleaseError("zip-member-read") from exc
    _need(len(data) == pin["size"] and hashlib.sha256(data).hexdigest() == pin["sha256"],
          "file-hash-or-size")
    return data


def _open_archive(raw: bytes, spec: dict) -> zipfile.ZipFile:
    _need(hashlib.sha256(raw).hexdigest() == spec["sha256"], "artifact-hash")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseError("invalid-zip") from exc
    try:
        infos = archive.infolist()
        _path_set([i.filename for i in infos])
        _need({i.filename for i in infos} == set(spec["files"]), "zip-census")
        for info in infos:
            _need(info.orig_filename == info.filename, "zip-original-name")
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            _need(not info.is_dir() and file_type in (0, stat.S_IFREG)
                  and not info.external_attr & 0x410, "zip-nonregular")
            _need(not info.flag_bits & 1 and info.compress_type in
                  (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), "zip-method")
            _need(info.file_size == spec["files"][info.filename]["size"], "zip-member-size")
            _member(archive, info.filename, spec["files"][info.filename])
    except BaseException:
        archive.close()
        raise
    return archive


def _mkdir(path: Path, owned_dirs: list[Path]) -> None:
    path.mkdir()  # no exist_ok: ownership is never inferred from an old path.
    owned_dirs.append(path)


def _write_tree(root: Path, pins: dict, resolve_member, owned_dirs: list[Path],
                owned_files: list[Path]) -> None:
    _mkdir(root, owned_dirs)
    made = {root}
    for name, pin in sorted(pins.items()):
        target = root.joinpath(*name.split("/"))
        parent = root
        for part in name.split("/")[:-1]:
            parent /= part
            if parent not in made:
                _mkdir(parent, owned_dirs)
                made.add(parent)
        data = resolve_member(name, pin)
        with target.open("xb") as stream:
            owned_files.append(target)
            stream.write(data)
        info = _plain(target)
        # No st_nlink == 1 demand: the file was created exclusively ("xb") by
        # this call, and a sync client may add its own link at any moment
        # (Google Drive for desktop links new files into .tmp.driveupload;
        # on 04.10.2026 this refused a valid release in a synced folder).
        # Content identity: size and SHA-256 here and again in _verify_tree.
        _need(info.st_size == pin["size"]
              and hashlib.sha256(target.read_bytes()).hexdigest() == pin["sha256"], "output-verification")


def _cleanup(files: list[Path], dirs: list[Path]) -> None:
    # Exact owned paths only; never recurse through a replaced directory/link.
    for target in reversed(files):
        if target.exists() or target.is_symlink():
            _parents(target)
            _plain(target)
            target.unlink()
    for directory in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        if directory.exists() or directory.is_symlink():
            _parents(directory)
            _plain(directory, directory=True)
            directory.rmdir()


def _verify_tree(root: Path, pins: dict, limits: Limits) -> None:
    found = {}
    expected_dirs = {"/".join(name.split("/")[:i]) for name in pins
                     for i in range(1, len(name.split("/")))}
    pending = [root]
    while pending:
        directory = pending.pop()
        _plain(directory, directory=True)
        for child in directory.iterdir():
            info = child.lstat()
            _need(not stat.S_ISLNK(info.st_mode)
                  and not getattr(info, "st_file_attributes", 0) & 0x400, "output-link")
            if stat.S_ISDIR(info.st_mode):
                _need(child.relative_to(root).as_posix() in expected_dirs, "output-extra-directory")
                pending.append(child)
            else:
                # Regular file only; a link count above one is a sync client's
                # alias of a file this call created (see _write_tree).
                _need(stat.S_ISREG(info.st_mode), "output-nonregular")
                name = child.relative_to(root).as_posix()
                _need(name in pins, "output-extra-file")
                data = _read_file(child, limits.file_bytes)
                found[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    _need(found == pins, "output-final-map")


def materialize_release(manifest_path: str | Path, expected_manifest_sha256: str,
                        artifact_paths: Mapping[str, str | Path], destination: str | Path,
                        *, limits: Limits = Limits()) -> MaterializedRelease:
    """Validate and materialize a new inactive release, or fail without activation.

    The destination parent must already exist. Existing destinations are always
    refused, even empty directories. Input artifact keys must exactly match the
    manifest; nothing is downloaded or resolved by filename/URL heuristics.
    """
    _need(isinstance(limits, Limits) and all(type(n) is int and n > 0
          for n in vars(limits).values()), "invalid-limits")
    manifest_path = Path(os.path.abspath(manifest_path))
    destination = Path(os.path.abspath(destination))
    _parents(destination)
    _need(not os.path.lexists(destination), "destination-exists")
    raw = _read_file(manifest_path, limits.manifest_bytes)
    doc = _validate_manifest(raw, expected_manifest_sha256, limits)
    _need(isinstance(artifact_paths, Mapping) and set(artifact_paths) == set(doc["artifacts"]),
          "artifact-paths")
    archives: dict[str, zipfile.ZipFile] = {}
    owned_dirs: list[Path] = []
    owned_files: list[Path] = []
    succeeded = False
    try:
        total = 0
        for name, spec in doc["artifacts"].items():
            data = _read_file(Path(os.path.abspath(artifact_paths[name])), limits.archive_bytes)
            total += len(data)
            _need(total <= limits.total_archive_bytes, "archive-total-limit")
            archives[name] = _open_archive(data, spec)
        # No filesystem output until every manifest/member/hash has passed.
        staging = Path(tempfile.mkdtemp(prefix=".exact-release-", dir=destination.parent))
        owned_dirs.append(staging)
        for role in ROLES:
            spec = doc["roles"][role]
            overlay = {} if spec["overlay"] is None else doc["artifacts"][spec["overlay"]]["files"]
            def read_role(name, pin, spec=spec, overlay=overlay):
                source = spec["overlay"] if name in overlay else spec["base"]
                return _member(archives[source], name, pin)
            _write_tree(staging / role, spec["files"], read_role, owned_dirs, owned_files)
        for component in ("native", "assets"):
            artifact = doc[component]["artifact"]
            _write_tree(staging / component, doc["artifacts"][artifact]["files"],
                        lambda name, pin, a=artifact: _member(archives[a], name, pin),
                        owned_dirs, owned_files)
        final_maps = {r: doc["roles"][r]["files"] for r in ROLES}
        final_maps.update({c: doc["artifacts"][doc[c]["artifact"]]["files"] for c in ("native", "assets")})
        for component, pins in final_maps.items():
            _verify_tree(staging / component, pins, limits)
        # Exclusive reservation is portable: POSIX rename alone could replace an
        # empty directory created by someone else between check and rename.
        _parents(destination)
        _mkdir(destination, owned_dirs)
        for component in (*ROLES, "native", "assets"):
            old, new = staging / component, destination / component
            os.rename(old, new)
            owned_files[:] = [new / p.relative_to(old) if p.is_relative_to(old) else p
                              for p in owned_files]
            owned_dirs[:] = [new / p.relative_to(old) if p.is_relative_to(old) else p
                             for p in owned_dirs]
        staging.rmdir()
        owned_dirs.remove(staging)
        _need({p.name for p in destination.iterdir()} == {*ROLES, "native", "assets"}, "output-root-census")
        for component, pins in final_maps.items():
            _verify_tree(destination / component, pins, limits)
        pub = doc["public_identity"]
        receipt = MaterializedRelease(
            doc["release_id"], expected_manifest_sha256, pub["repository"], pub["revision"],
            destination, *(destination / c for c in (*ROLES, "native", "assets")),
            tuple((r, doc["roles"][r]["origin_commit"]) for r in ROLES)
            + (("native", doc["native"]["origin_commit"]),))
        succeeded = True
        return receipt
    except (OSError, EOFError) as exc:
        raise ReleaseError("local-materialization-failed") from exc
    finally:
        for archive in archives.values():
            archive.close()
        if not succeeded:
            # Preserve cleanup failures as errors too; never claim a clean failure
            # if another writer replaced an owned path or an I/O failure remains.
            try:
                _cleanup(owned_files, owned_dirs)
            except (OSError, ReleaseError) as exc:
                raise ReleaseError("cleanup-incomplete; inactive output requires inspection") from exc
