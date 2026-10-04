# -*- coding: utf-8 -*-
"""РВ-143 (слово владельца 04.10.2026): сборщик выпуска exact-установщика
`tools/build_release.py` — на синтетическом срезе, без сети и служб.

Закон: архивы выпуска принимает тот же материализатор, что у установщика;
файлы установщика в роли не входят (SHA профиля вписывается после сборки);
связь тега и ролей проверяется поимённо; ключи, маркеры заказчика и копии-
откаты площадки в выпуск не проходят."""
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

import installer_release as ir
from tools import build_release as br

MARKERS = {"checks": [{"name": "заказчик", "kind": "token", "values": ["ПЕРЕВОЗЧИК-Х"]}]}


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _slice(tmp_path):
    repo = tmp_path / "slice"
    files = {
        "cdpm/webui.py": "print('cdpm')\n",
        "webapp/app.py": "print('webapp')\n",
        "catalog_service/__main__.py": "print('catalog')\n",
        "webapp/config/airline_default.json.example":
            json.dumps({"parameters": {"min_sit_min": 30}}) + "\n",
        "cdpm/agents-default.json.example": "{}\n",
        "install_cosmos.py": "RELEASE = {}\n",
        # приманка гейта в наборе тестов (как tests/test_settings.py среза)
        "tests/test_fake_key.py": "K = 'sk-' + 'ant-' + 'x' * 24\nV = '%s'\n" % ("sk-" + "ant-api03-" + "y" * 24),
        "installer_release.py": "# materializer\n",
        "README.md": "principia\n",
    }
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "slice")
    return repo


def _native(tmp_path):
    files = {"quarkus-app/quarkus-run.jar": b"jar", "quarkus-app/lib/a.jar": b"lib"}
    raw = br.pack(files)
    z = tmp_path / "native.zip"
    z.write_bytes(raw)
    art = tmp_path / "native-artifact.json"
    # тот же вид описи, что пишет tools/build_native.py (доля COSMOS)
    art.write_text(json.dumps({"schema": br.NATIVE_SCHEMA,
                               "artifact": {"sha256": hashlib.sha256(raw).hexdigest(),
                                            "files": br.pins(files)},
                               "origin_commit": "c" * 40, "java_version": "21.0.11",
                               "maven_distribution": "maven-3.9.16"}),
                   encoding="utf-8")
    return z, art


def _assets(tmp_path, extra=None):
    cat = tmp_path / "assets" / "catalog"
    (cat / "releases").mkdir(parents=True)
    (cat / "catalog.json").write_text('{"version": "0.9.0"}\n', encoding="utf-8")
    (cat / "releases" / "0.9.0.json").write_text("{}\n", encoding="utf-8")
    for name, text in (extra or {}).items():
        (cat / name).write_text(text, encoding="utf-8")
    b = tmp_path / "assets" / "bindings"
    b.mkdir()
    (b / "cosmos-bindings.json").write_text("{}\n", encoding="utf-8")
    (b / "cdpm-bindings.json").write_text("{}\n", encoding="utf-8")
    return cat, {br.ASSET_BINDINGS[0]: b / "cosmos-bindings.json",
                 br.ASSET_BINDINGS[1]: b / "cdpm-bindings.json"}


def _build(tmp_path, repo, extra_assets=None, runtime=None):
    z, art = _native(tmp_path)
    cat, bindings = _assets(tmp_path, extra_assets)
    return br.build(repo, "a" * 40, z, art, cat, bindings, tmp_path / "out",
                    release_id="cosmos-test", repository="eliduc/COSMOS",
                    runtime=runtime, markers=MARKERS)


def test_build_is_accepted_by_the_installer_materializer(tmp_path):
    repo = _slice(tmp_path)
    got = _build(tmp_path, repo)
    out = tmp_path / "out"
    manifest_raw = (out / "manifest.json").read_bytes()
    assert got["manifest_sha256"] == hashlib.sha256(manifest_raw).hexdigest()
    manifest = ir._validate_manifest(manifest_raw, got["manifest_sha256"], ir.Limits())
    # файлы установщика — не роли; правило путей отбросило webapp/config/
    role = manifest["roles"]["producer"]["files"]
    assert "install_cosmos.py" not in role and "installer_release.py" not in role
    assert "webapp/config/airline_default.json.example" not in role
    assert got["dropped"]["install_cosmos.py"] == "файл установщика"
    # набор тестов (с приманками гейта ключей) — не роль, гейт не спотыкается
    assert "tests/test_fake_key.py" not in role
    assert got["dropped"]["tests/test_fake_key.py"].startswith("не исполняемая")
    assert manifest["public_identity"]["revision"] == got["public_revision"]
    rel = ir.materialize_release(out / "manifest.json", got["manifest_sha256"],
                                 {"base": out / "base.zip", "native": out / "native.zip",
                                  "assets": out / "assets.zip"}, tmp_path / "site")
    assert (rel.producer / "cdpm" / "webui.py").is_file()
    assert (rel.catalog / "catalog_service" / "__main__.py").is_file()
    assert (rel.assets / "catalog" / "catalog.json").is_file()


def test_build_is_deterministic(tmp_path):
    repo = _slice(tmp_path)
    first = _build(tmp_path, repo)["assets"]
    second_dir = tmp_path / "again"
    second_dir.mkdir()
    second = _build(second_dir, repo)["assets"]
    assert first == second


def test_profile_pins_manifest_artifacts_and_examples(tmp_path):
    repo = _slice(tmp_path)
    runtime = {"platform": {"system": "Windows", "machine": "AMD64"},
               "python": {"implementation": "CPython", "version": "3.14.4",
                          "executable_sha256": "1" * 64, "distributions": {"pip": "25.0"}},
               "java": {"version": "21.0.11", "release_sha256": "2" * 64,
                        "executable_sha256": "3" * 64}}
    got = _build(tmp_path, repo, runtime=runtime)
    raw = (tmp_path / "out" / "profile.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == got["profile_sha256"]
    import install_cosmos
    profile = install_cosmos._exact_profile(json.loads(raw))
    assert profile["manifest"]["sha256"] == got["manifest_sha256"]
    for kind, pin in profile["examples"].items():
        body = (tmp_path / "out" / pin["path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == pin["sha256"]


def test_dirty_slice_is_refused(tmp_path):
    repo = _slice(tmp_path)
    (repo / "webapp" / "app.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(br.BuildError, match="грязный"):
        _build(tmp_path, repo)


def test_secret_in_role_file_is_refused(tmp_path):
    repo = _slice(tmp_path)
    key = "sk-" + "ant-" + "x" * 24              # не литерал ключа в репозитории
    (repo / "webapp" / "leak.py").write_text("K = '%s'\n" % key, encoding="utf-8")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "leak")
    with pytest.raises(br.BuildError, match="гейт ключей"):
        _build(tmp_path, repo)


def test_customer_marker_in_assets_is_refused(tmp_path):
    repo = _slice(tmp_path)
    with pytest.raises(br.BuildError, match="маркеров заказчика"):
        _build(tmp_path, repo, extra_assets={"note.md": "ПЕРЕВОЗЧИК-Х\n"})


def test_rollback_copy_in_assets_is_refused(tmp_path):
    repo = _slice(tmp_path)
    with pytest.raises(br.BuildError, match="копия-откат"):
        _build(tmp_path, repo, extra_assets={"catalog.json.z-090": "{}\n"})


def test_tag_tree_link_allows_only_installer_difference(tmp_path):
    repo = _slice(tmp_path)
    got = _build(tmp_path, repo)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    # коммит «SHA профиля в установщике» — меняет лишь файл установщика
    (repo / "install_cosmos.py").write_text("RELEASE = {'sha': 1}\n", encoding="utf-8")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "pin")
    assert br.verify_tag_tree(repo, "HEAD", manifest) == []
    # правка файла роли после сборки — связь разорвана, называется поимённо
    (repo / "webapp" / "app.py").write_text("print('other')\n", encoding="utf-8")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "drift")
    assert br.verify_tag_tree(repo, "HEAD", manifest) == ["иной sha256: webapp/app.py"]
    assert br.main(["verify", "--slice", str(repo), "--commit", "HEAD",
                    "--manifest", str(tmp_path / "out" / "manifest.json")]) == 2
    assert got["public_revision"] != subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
        text=True).stdout.strip()


def test_tag_may_not_change_files_outside_roles_except_installer(tmp_path):
    """Ревью GPT 12:27: файл, отброшенный правилом путей (здесь — пример
    площадки в webapp/config/), тег не вправе менять молча."""
    repo = _slice(tmp_path)
    _build(tmp_path, repo)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    ex = repo / "webapp" / "config" / "airline_default.json.example"
    ex.write_text(json.dumps({"parameters": {"min_sit_min": 45}}) + "\n", encoding="utf-8")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "example")
    assert br.verify_tag_tree(repo, "HEAD", manifest) == [
        "изменён вне ролей: webapp/config/airline_default.json.example"]


def test_tag_deletion_outside_roles_and_mode_change_are_named(tmp_path):
    """Ревью GPT 12:42: обход объединения деревьев (удаление вне ролей) и
    режим / тип записи (тот же блоб, иной режим) — тоже изменения."""
    repo = _slice(tmp_path)
    _build(tmp_path, repo)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    _git(repo, "rm", "-q", "webapp/config/airline_default.json.example")
    _git(repo, "update-index", "--chmod=+x", "webapp/app.py")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "del+mode")
    assert br.verify_tag_tree(repo, "HEAD", manifest) == [
        "иной режим или тип: webapp/app.py",
        "удалён вне ролей: webapp/config/airline_default.json.example"]


def test_native_artifact_of_another_shape_is_refused(tmp_path):
    repo = _slice(tmp_path)
    z, art = _native(tmp_path)
    doc = json.loads(art.read_text(encoding="utf-8"))
    flat = {"sha256": doc["artifact"]["sha256"], "files": doc["artifact"]["files"],
            "origin_commit": doc["origin_commit"]}
    art.write_text(json.dumps(flat), encoding="utf-8")
    cat, bindings = _assets(tmp_path)
    with pytest.raises(br.BuildError, match="не того вида"):
        br.build(repo, "a" * 40, z, art, cat, bindings, tmp_path / "out",
                 release_id="cosmos-test", repository="eliduc/COSMOS",
                 runtime=None, markers=MARKERS)


def _with_locks(repo, hashed=True):
    for plat in br.LOCK_PLATFORMS:
        line = "fastapi==0.136.1" + (" --hash=sha256:" + "f" * 64 if hashed else "")
        p = repo / "locks" / ("requirements-lock-%s.txt" % plat)
        p.parent.mkdir(exist_ok=True)
        p.write_text("# lock\n" + line + "\n", encoding="ascii", newline="\n")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "locks")
    return repo


def test_release_profile_v2_pins_platform_locks_not_one_machine(tmp_path):
    """РВ-143 D: профиль пользователя — платформы и замки, без хешей машины."""
    repo = _with_locks(_slice(tmp_path))
    got = _build(tmp_path, repo)
    out = tmp_path / "out"
    raw = (out / "profile.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == got["profile_sha256"]
    import install_cosmos
    profile = install_cosmos._exact_profile_v2(json.loads(raw))
    assert set(profile["platforms"]) == set(br.LOCK_PLATFORMS)
    assert "runtime" not in profile
    for plat, spec in profile["platforms"].items():
        assert spec["python"] == "3.14" and spec["java"] == "21"
        body = (out / spec["lock"]["path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == spec["lock"]["sha256"]
        assert body == (repo / spec["lock"]["path"]).read_bytes()
    manifest = json.loads((out / "manifest.json").read_bytes())
    role = manifest["roles"]["producer"]["files"]
    assert not any(n.startswith("locks/") for n in role)
    assert got["dropped"]["locks/requirements-lock-Linux-x86_64.txt"] == "файл установщика"


def test_lock_without_hashes_is_refused_by_the_builder(tmp_path):
    repo = _with_locks(_slice(tmp_path), hashed=False)
    with pytest.raises(ir.ReleaseError, match="lock-line"):
        _build(tmp_path, repo)
