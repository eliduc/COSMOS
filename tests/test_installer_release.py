"""Tiny synthetic, offline adversarial tests; no live release or service imports."""

import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

import installer_release as ir


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pins(files):
    return {p: {"size": len(b), "sha256": sha(b)} for p, b in files.items()}


def zip_bytes(files, *, special=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            if special and name in special:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = special[name]
                archive.writestr(info, body)
            else:
                archive.writestr(name, body)
    return stream.getvalue()


@pytest.fixture
def release(tmp_path):
    blobs = {
        "base": {"app/main.py": b"BASE\n", "shared.txt": b"shared\n", "obsolete.py": b"old\n"},
        "producer": {"app/main.py": b"PRODUCER\n", "new.py": b"new\n"},
        "catalog": {"app/main.py": b"CATALOG\n"},
        "native": {"quarkus-run.jar": b"synthetic, not executable\n", "lib/test.jar": b"test\n"},
        "assets": {"catalog/catalog.json": b'{"version":"fixture"}\n',
                   "bindings/cdpm-bindings.json": b"{}\n", "bindings/cosmos-bindings.json": b"{}\n"},
    }
    artifacts = {}
    paths = {}
    for name, content in blobs.items():
        body = zip_bytes(content)
        path = tmp_path / (name + ".zip")
        path.write_bytes(body)
        paths[name] = path
        artifacts[name] = {"sha256": sha(body), "files": pins(content)}
    roles = {}
    for i, role in enumerate(ir.ROLES, 1):
        final = dict(blobs["base"])
        overlay = None if role == "consumer" else role
        removes = ["obsolete.py"] if role == "producer" else []
        for name in removes:
            del final[name]
        if overlay:
            final.update(blobs[overlay])
        roles[role] = {"origin_commit": str(i) * 40, "base": "base", "overlay": overlay,
                       "remove": removes, "files": pins(final)}
    doc = {"schema": ir.SCHEMA, "release_id": "test-release-01",
           "public_identity": {"repository": "owner/public", "revision": "a" * 40},
           "artifacts": artifacts, "roles": roles,
           "native": {"origin_commit": "4" * 40, "artifact": "native"},
           "assets": {"artifact": "assets"}}
    return {"root": tmp_path, "doc": doc, "paths": paths, "blobs": blobs,
            "manifest": tmp_path / "release.json", "destination": tmp_path / "new-release"}


def save(release, raw=None):
    raw = raw if raw is not None else json.dumps(release["doc"]).encode()
    release["manifest"].write_bytes(raw)
    return sha(raw)


def materialize(release, **kwargs):
    digest = save(release)
    return ir.materialize_release(release["manifest"], digest, release["paths"],
                                  release["destination"], **kwargs)


def assert_clean(release):
    assert not release["destination"].exists()
    assert not list(release["root"].glob(".exact-release-*"))


def repack(release, artifact, files, **kwargs):
    body = zip_bytes(files, **kwargs)
    release["paths"][artifact].write_bytes(body)
    release["doc"]["artifacts"][artifact]["sha256"] = sha(body)


def test_healthy_complete_maps_origins_public_identity_and_isolation(release):
    receipt = materialize(release)
    assert receipt.public_revision == "a" * 40
    assert dict(receipt.origins) == {"producer": "1" * 40, "consumer": "2" * 40,
                                     "catalog": "3" * 40, "native": "4" * 40}
    assert receipt.public_repository == "owner/public"
    assert receipt.release_id == "test-release-01"
    assert receipt.manifest_sha256 == sha(release["manifest"].read_bytes())
    for role in ir.ROLES:
        root = getattr(receipt, role)
        actual = {p.relative_to(root).as_posix(): {"size": p.stat().st_size,
                  "sha256": sha(p.read_bytes())} for p in root.rglob("*") if p.is_file()}
        assert actual == release["doc"]["roles"][role]["files"]
        assert all(p.stat().st_nlink == 1 for p in root.rglob("*") if p.is_file())
    assert (receipt.consumer / "app/main.py").read_bytes() == b"BASE\n"
    assert (receipt.producer / "app/main.py").read_bytes() == b"PRODUCER\n"
    assert not (receipt.producer / "obsolete.py").exists()
    assert (receipt.consumer / "obsolete.py").exists()
    (receipt.producer / "shared.txt").write_bytes(b"changed")
    assert (receipt.consumer / "shared.txt").read_bytes() == b"shared\n"
    assert (receipt.catalog / "shared.txt").read_bytes() == b"shared\n"
    assert set(p.name for p in receipt.destination.iterdir()) == {*ir.ROLES, "native", "assets"}
    assert not list(release["root"].glob(".exact-release-*"))


def test_manifest_digest_failure_before_zip_reads(release, monkeypatch):
    save(release)
    monkeypatch.setattr(ir, "_open_archive", lambda *a: pytest.fail("ZIP read before trust"))
    with pytest.raises(ir.ReleaseError, match="manifest-hash"):
        ir.materialize_release(release["manifest"], "0" * 64, release["paths"], release["destination"])
    assert_clean(release)


@pytest.mark.parametrize("raw", [b'{"schema":1,"schema":2}', b"not json", b"\xff", b"[]"])
def test_invalid_manifest(release, raw):
    digest = save(release, raw)
    with pytest.raises(ir.ReleaseError):
        ir.materialize_release(release["manifest"], digest, release["paths"], release["destination"])
    assert_clean(release)


def test_artifact_hash_failure(release):
    release["paths"]["native"].write_bytes(b"wrong artifact")
    with pytest.raises(ir.ReleaseError, match="artifact-hash"):
        materialize(release)
    assert_clean(release)


@pytest.mark.parametrize("kind", ["missing", "extra", "size", "content"])
def test_zip_member_exactness(release, kind):
    files = dict(release["blobs"]["native"])
    if kind == "missing":
        del files["lib/test.jar"]
    elif kind == "extra":
        files["extra.txt"] = b"extra"
    elif kind == "size":
        files["lib/test.jar"] += b"X"
    else:
        files["lib/test.jar"] = b"FAIL\n"
    repack(release, "native", files)
    with pytest.raises(ir.ReleaseError):
        materialize(release)
    assert_clean(release)


@pytest.mark.parametrize("bad", ["../escape", "/absolute", "C:/absolute", "C:drive", "a\\b",
                                     "a/../b", "a//b", "./b", "a\x00b", "trailing.", "space ",
                                     "CON.txt", "lpt1", "COM¹.txt", "a:b", "a?b", "a|b"])
def test_unsafe_manifest_paths(release, bad):
    release["doc"]["artifacts"]["native"]["files"] = {bad: {"size": 1, "sha256": sha(b"x")}}
    with pytest.raises(ir.ReleaseError):
        materialize(release)
    assert_clean(release)


@pytest.mark.parametrize("bad", [".env", ".env.prod", ".git/config", "webapp/config/users.db",
                                     "webapp/config/airline_default.json.example", "webapp/jobs/x",
                                     "data/uploads/file", "settings/agents-default.json",
                                     "settings/local-derived-executors-registry.json", "foo/private.pem",
                                     "credentials/token", "cdpm-runs/run.json", "id_ed25519", ".npmrc",
                                     ".pypirc", ".netrc", ".git-credentials", "credentials.json",
                                     "secrets.json", "channel-token-test.txt"])
def test_site_state_credentials_and_test_qualification_not_accepted(release, bad):
    release["doc"]["artifacts"]["native"]["files"] = {bad: {"size": 1, "sha256": sha(b"x")}}
    with pytest.raises(ir.ReleaseError):
        materialize(release)
    assert_clean(release)


@pytest.mark.parametrize("names", [("Name", "name"), ("Dir/a", "dir/b"), ("file", "file/child")])
def test_manifest_collisions(release, names):
    release["doc"]["artifacts"]["native"]["files"] = pins({p: b"x" for p in names})
    with pytest.raises(ir.ReleaseError):
        materialize(release)
    assert_clean(release)


def test_overlay_case_collision_is_rejected(release):
    overlay = release["doc"]["artifacts"]["producer"]["files"]
    overlay["Shared.txt"] = {"size": 1, "sha256": sha(b"x")}
    release["doc"]["roles"]["producer"]["files"]["Shared.txt"] = overlay["Shared.txt"]
    with pytest.raises(ir.ReleaseError, match="case-collision"):
        materialize(release)
    assert_clean(release)


@pytest.mark.parametrize("kind", ["missing_final", "wrong_final_hash", "unknown_remove", "duplicate_remove",
                                  "overlay_remove", "unused_artifact", "missing_artifact", "same_component"])
def test_composition_contract(release, kind):
    doc = release["doc"]
    spec = doc["roles"]["producer"]
    if kind == "missing_final":
        del spec["files"]["shared.txt"]
    elif kind == "wrong_final_hash":
        spec["files"]["shared.txt"]["sha256"] = "0" * 64
    elif kind == "unknown_remove":
        spec["remove"].append("missing.py")
    elif kind == "duplicate_remove":
        spec["remove"].append("obsolete.py")
    elif kind == "overlay_remove":
        spec["remove"].append("app/main.py")
    elif kind == "unused_artifact":
        doc["artifacts"]["unused"] = copy.deepcopy(doc["artifacts"]["base"])
    elif kind == "missing_artifact":
        spec["overlay"] = "unknown"
    else:
        doc["assets"]["artifact"] = "native"
    with pytest.raises(ir.ReleaseError):
        materialize(release)
    assert_clean(release)


@pytest.mark.parametrize("special", [(stat.S_IFLNK | 0o777) << 16, (stat.S_IFIFO | 0o600) << 16,
                                     (stat.S_IFREG | 0o600) << 16 | 0x400])
def test_zip_links_and_nonregular_members(release, special):
    repack(release, "native", release["blobs"]["native"], special={"lib/test.jar": special})
    with pytest.raises(ir.ReleaseError, match="zip-nonregular"):
        materialize(release)
    assert_clean(release)


def test_zip_traversal_even_if_not_in_manifest(release):
    repack(release, "native", {**release["blobs"]["native"], "../escape": b"x"})
    with pytest.raises(ir.ReleaseError):
        materialize(release)
    assert not (release["root"] / "escape").exists()
    assert_clean(release)


def test_duplicate_zip_member(release):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in release["blobs"]["native"].items():
            archive.writestr(name, content)
        with pytest.warns(UserWarning, match="Duplicate"):
            archive.writestr("lib/test.jar", b"test\n")
    body = stream.getvalue()
    release["paths"]["native"].write_bytes(body)
    release["doc"]["artifacts"]["native"]["sha256"] = sha(body)
    with pytest.raises(ir.ReleaseError, match="duplicate-path"):
        materialize(release)
    assert_clean(release)


@pytest.mark.parametrize("limit", [ir.Limits(manifest_bytes=1), ir.Limits(archive_bytes=1),
                                    ir.Limits(total_archive_bytes=1), ir.Limits(file_bytes=1),
                                    ir.Limits(total_uncompressed_bytes=1), ir.Limits(files=1)])
def test_size_limits(release, limit):
    with pytest.raises(ir.ReleaseError):
        materialize(release, limits=limit)
    assert_clean(release)


@pytest.mark.parametrize("kind", ["empty", "nonempty", "file"])
def test_existing_destination_never_changed(release, kind):
    dest = release["destination"]
    if kind == "file":
        dest.write_bytes(b"existing file")
    else:
        dest.mkdir()
        if kind == "nonempty":
            (dest / "keep").write_bytes(b"existing data")
    with pytest.raises(ir.ReleaseError, match="destination-exists"):
        materialize(release)
    if kind == "file":
        assert dest.read_bytes() == b"existing file"
    elif kind == "nonempty":
        assert (dest / "keep").read_bytes() == b"existing data"
    else:
        assert list(dest.iterdir()) == []
    assert not list(release["root"].glob(".exact-release-*"))


def test_write_failure_cleans_only_owned_staging(release, monkeypatch):
    previous = release["root"] / "previous-release"
    previous.mkdir()
    (previous / "keep").write_bytes(b"previous")
    original = ir._write_tree
    def fail(root, *args):
        if root.name == "catalog":
            raise OSError("synthetic failure")
        return original(root, *args)
    monkeypatch.setattr(ir, "_write_tree", fail)
    with pytest.raises(ir.ReleaseError, match="local-materialization-failed"):
        materialize(release)
    assert (previous / "keep").read_bytes() == b"previous"
    assert_clean(release)


def test_destination_race_does_not_replace_empty_directory(release, monkeypatch):
    original = ir._mkdir
    def mkdir(path, owned):
        if path == release["destination"]:
            path.mkdir()  # another caller reserves the name after validation
        return original(path, owned)
    monkeypatch.setattr(ir, "_mkdir", mkdir)
    with pytest.raises(ir.ReleaseError, match="local-materialization-failed"):
        materialize(release)
    assert release["destination"].is_dir()
    assert list(release["destination"].iterdir()) == []
    assert not list(release["root"].glob(".exact-release-*"))


def test_commit_transfer_failure_cleans_owned_output_not_previous(release, monkeypatch):
    previous = release["root"] / "previous"
    previous.mkdir()
    (previous / "keep").write_bytes(b"old")
    original = ir.os.rename
    count = 0
    def fail(src, dst):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic rename failure")
        original(src, dst)
    monkeypatch.setattr(ir.os, "rename", fail)
    with pytest.raises(ir.ReleaseError, match="local-materialization-failed"):
        materialize(release)
    assert (previous / "keep").read_bytes() == b"old"
    assert_clean(release)


def test_input_reparse_metadata_refused_without_privileged_link_creation(release, monkeypatch):
    target = release["paths"]["native"]
    original = ir.Path.lstat
    class Reparse:
        st_mode = stat.S_IFREG | 0o600
        st_file_attributes = 0x400
    def lstat(path):
        return Reparse() if path == target else original(path)
    monkeypatch.setattr(ir.Path, "lstat", lstat)
    with pytest.raises(ir.ReleaseError, match="local-link"):
        materialize(release)
    assert_clean(release)


def test_missing_or_extra_artifact_paths_refused(release):
    release["paths"]["unexpected"] = release["paths"]["native"]
    with pytest.raises(ir.ReleaseError, match="artifact-paths"):
        materialize(release)
    assert_clean(release)


def test_runtime_assets_only(release):
    release["doc"]["artifacts"]["assets"]["files"]["unrelated.txt"] = {"size": 1, "sha256": sha(b"x")}
    with pytest.raises(ir.ReleaseError, match="non-runtime-asset"):
        materialize(release)
    assert_clean(release)


@pytest.mark.parametrize("kind", ["missing", "changed"])
def test_final_output_is_reverified(release, monkeypatch, kind):
    original = ir._write_tree
    def corrupt(root, *args):
        original(root, *args)
        if root.name == "consumer":
            path = root / "shared.txt"
            path.unlink() if kind == "missing" else path.write_bytes(b"corrupt")
    monkeypatch.setattr(ir, "_write_tree", corrupt)
    with pytest.raises(ir.ReleaseError, match="output-final-map"):
        materialize(release)
    assert_clean(release)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_untracked_output_refused_without_deleting_another_writers_file(release, monkeypatch, kind):
    original = ir._write_tree
    inserted = []
    def contaminate(root, *args):
        original(root, *args)
        if root.name == "consumer":
            path = root / "foreign-file.txt"
            path.write_bytes(b"belongs to another writer") if kind == "file" else path.mkdir()
            inserted.append(path)
    monkeypatch.setattr(ir, "_write_tree", contaminate)
    with pytest.raises(ir.ReleaseError, match="cleanup-incomplete") as exc:
        materialize(release)
    assert isinstance(exc.value.__cause__, OSError)
    assert not release["destination"].exists()
    if kind == "file":
        assert inserted[0].read_bytes() == b"belongs to another writer"
    else:
        assert inserted[0].is_dir()


def test_manifest_metadata_unknown_keys_refused(release):
    release["doc"]["activation_command"] = "never executed"
    with pytest.raises(ir.ReleaseError, match="manifest-keys"):
        materialize(release)
    assert_clean(release)


def test_missing_artifact_key_refused(release):
    del release["paths"]["native"]
    with pytest.raises(ir.ReleaseError, match="artifact-paths"):
        materialize(release)
    assert_clean(release)


def test_native_and_asset_full_census_and_input_bytes_preserved(release):
    before = {name: path.read_bytes() for name, path in release["paths"].items()}
    result = materialize(release)
    for component in ("native", "assets"):
        root = getattr(result, component)
        contents = {p.relative_to(root).as_posix(): p.read_bytes()
                    for p in root.rglob("*") if p.is_file()}
        assert contents == release["blobs"][component]
    assert before == {name: path.read_bytes() for name, path in release["paths"].items()}


def test_invalid_pin_types_are_not_coerced(release):
    release["doc"]["artifacts"]["native"]["files"]["lib/test.jar"]["size"] = True
    with pytest.raises(ir.ReleaseError, match="invalid-file-size"):
        materialize(release)
    assert_clean(release)


def test_source_qualification_code_is_not_confused_with_test_qualification_state(release):
    content = {**release["blobs"]["catalog"], "vmp/qualification_run.py": b"# source, not receipt\n"}
    repack(release, "catalog", content)
    release["doc"]["artifacts"]["catalog"]["files"] = pins(content)
    release["doc"]["roles"]["catalog"]["files"].update(pins(content))
    result = materialize(release)
    assert (result.catalog / "vmp/qualification_run.py").read_bytes() == b"# source, not receipt\n"


def test_sync_client_links_do_not_refuse_a_valid_release(release, monkeypatch):
    """04.10.2026: Google Drive for desktop linked fresh output files into its
    .tmp.driveupload folder (st_nlink == 2) and a valid release was refused
    with output-nonregular. A link added by another process to a file this call
    created exclusively is not a content change; size/SHA still decide."""
    import os
    sync = release["root"] / "sync-client"
    sync.mkdir()
    real_verify = ir._verify_tree
    linked = []

    def verify_after_sync_client(root, pins_, limits):
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_nlink == 1:
                os.link(path, sync / str(len(linked)))
                linked.append(path)
        return real_verify(root, pins_, limits)

    monkeypatch.setattr(ir, "_verify_tree", verify_after_sync_client)
    receipt = materialize(release)
    assert linked and (receipt.producer / "app/main.py").stat().st_nlink == 2
    assert (receipt.producer / "app/main.py").read_bytes() == b"PRODUCER\n"


def test_sync_client_link_never_hides_a_content_change(release, monkeypatch):
    real_verify = ir._verify_tree

    def verify_after_tamper(root, pins_, limits):
        victim = next(p for p in sorted(root.rglob("*")) if p.is_file())
        victim.write_bytes(victim.read_bytes() + b"tampered")
        return real_verify(root, pins_, limits)

    monkeypatch.setattr(ir, "_verify_tree", verify_after_tamper)
    with pytest.raises(ir.ReleaseError):
        materialize(release)
    assert_clean(release)
