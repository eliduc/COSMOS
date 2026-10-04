"""Synthetic offline integration; never install packages, execute payloads or bind ports."""
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

import install_cosmos as ic
import installer_release as ir

REAL_RUNTIME_READER = ic._exact_runtime


def sha(body):
    return hashlib.sha256(body).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def pins(files):
    return {name: {"size": len(body), "sha256": sha(body)} for name, body in files.items()}


@pytest.fixture
def case(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source = {"cdpm/webui.py": b"# synthetic producer\n", "webapp/app.py": b"# synthetic consumer\n",
              "catalog_service/__main__.py": b"# synthetic catalog\n", "retired.py": b"# old\n"}
    blobs = {"base": source, "producer": {"cdpm/webui.py": b"# producer overlay\n"},
             "catalog": {"catalog_service/__main__.py": b"# catalog overlay\n"},
             "native": {"quarkus-app/quarkus-run.jar": b"NOT EXECUTABLE\n"},
             "assets": {"catalog/catalog.json": b"{}", "bindings/cdpm-bindings.json": b"{}",
                        "bindings/cosmos-bindings.json": b"{}"}}
    artifacts = {}
    for name, files in blobs.items():
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            for path, data in files.items():
                z.writestr(path, data)
        raw = stream.getvalue()
        (bundle / (name + ".zip")).write_bytes(raw)
        artifacts[name] = {"sha256": sha(raw), "files": pins(files)}
    roles = {}
    for n, role in enumerate(ir.ROLES, 1):
        final = dict(source)
        overlay = None if role == "consumer" else role
        if overlay:
            final.update(blobs[overlay])
        removes = ["retired.py"] if role == "producer" else []
        for path in removes:
            del final[path]
        roles[role] = {"origin_commit": str(n) * 40, "base": "base", "overlay": overlay,
                       "remove": removes, "files": pins(final)}
    manifest = {"schema": ir.SCHEMA, "release_id": "synthetic-01",
                "public_identity": {"repository": "example/public", "revision": "a" * 40},
                "artifacts": artifacts, "roles": roles,
                "native": {"origin_commit": "4" * 40, "artifact": "native"},
                "assets": {"artifact": "assets"}}
    (bundle / "release.json").write_bytes(encode(manifest))
    rules = {"parameters": dict.fromkeys(
        ["min_sit_min", "max_landings_per_duty", "max_duty_span_min",
         "min_rest_min", "max_monthly_block_min", "max_pairing_span_min"], 30)}
    agents = {"form_version": 1, "customer": "PUBLIC SYNTHETIC EXAMPLE ONLY", "_comment": "No keys"}
    for role in ("worker", "extractor", "replier", "auditor", "arbiter", "applier", "vision"):
        agents[role] = {"channels": {"cloud": {"provider": "cloud", "model": "fixture-only"}}}
    examples = {"airline_rules": rules, "agents": agents}
    for kind, value in examples.items():
        (bundle / (kind + ".json")).write_bytes(encode(value))
    runtime = {"platform": {"system": "Windows", "machine": "AMD64"},
               "python": {"implementation": "CPython", "version": "3.14.6",
                          "executable_sha256": sha(Path(ic.sys.executable).read_bytes()),
                          "distributions": {"fixture-only": "1.0"}},
               "java": {"version": "21.0.8", "release_sha256": sha(b'JAVA_VERSION="21.0.8"\n'),
                        "executable_sha256": sha(b"NOT EXECUTABLE")}}
    java = tmp_path / "jdk"
    (java / "bin").mkdir(parents=True)
    (java / "bin/java.exe").write_bytes(b"NOT EXECUTABLE")
    (java / "release").write_bytes(b'JAVA_VERSION="21.0.8"\n')
    profile = {"schema": ic.EXACT_PROFILE_SCHEMA, "qualification_id": "synthetic-not-qualified",
               "manifest": {"path": "release.json", "sha256": sha(encode(manifest))},
               "artifacts": {name: name + ".zip" for name in artifacts}, "runtime": runtime,
               "examples": {kind: {"path": kind + ".json", "sha256": sha(encode(value))}
                            for kind, value in examples.items()}}
    site = {"schema": ic.EXACT_SITE_SCHEMA, "ports": {"webapp": 18090, "cdpm": 18095,
            "catalog": 18105, "solver": 19096}, "public_catalog_url": "https://catalog.example.org/",
            "auth": {"mode": "token", "super_admin": "operator@example.org",
                     "access_team": None, "access_aud": None},
            "language": "en", "contour": "local", "llm_backend": "local", "data_policy": "internal"}
    result = {"root": tmp_path, "bundle": bundle, "manifest": manifest, "profile": profile,
              "examples": examples, "site": site, "java": java, "target": tmp_path / "fresh",
              "profile_path": tmp_path / "profile.json", "site_path": tmp_path / "site.json"}
    monkeypatch.setattr(ic, "_exact_runtime", lambda path: copy.deepcopy(runtime))
    # Any accidental legacy/live route must fail the test, never reach a machine.
    def forbidden(*a, **kw):
        raise AssertionError("legacy/live call in exact mode")
    for name in ("_run", "_http", "verify", "offer_start_now", "_offer_access_key"):
        monkeypatch.setattr(ic, name, forbidden)
    monkeypatch.setattr(ic.Survey, "run", forbidden)
    monkeypatch.setattr(ic.Installer, "run", forbidden)
    return result


def save(case):
    case["profile_path"].write_bytes(encode(case["profile"]))
    case["site_path"].write_bytes(encode(case["site"]))
    return sha(encode(case["profile"]))


def prepare(case, **kw):
    return ic.prepare_exact_install(case["profile_path"], save(case), case["bundle"],
                                    case["site_path"], case["java"], case["target"], **kw)


def cli(case, *extra):
    return ["--exact-profile", str(case["profile_path"]), "--exact-profile-sha256", save(case),
            "--exact-bundle", str(case["bundle"]), "--exact-site", str(case["site_path"]),
            "--java-home", str(case["java"]), "--target", str(case["target"]), *extra]


def no_output(case):
    assert not case["target"].exists()
    assert not list(case["root"].glob(".exact-release-*"))


def test_full_fresh_setup_uses_three_exact_roles_and_never_activates(case):
    receipt = prepare(case)
    root = case["target"]
    assert receipt["state"] == "prepared-not-started"
    assert receipt["activated"] is False and receipt["live_acceptance"] is False
    assert receipt["public_identity"]["revision"] == "a" * 40
    assert receipt["private_origin_commits"] == {r: str(i) * 40 for i, r in enumerate(ir.ROLES, 1)}
    assert receipt["native_origin_commit"] == "4" * 40
    assert "not wheel-content attestation" in receipt["runtime_check"]
    assert (root / "producer/cdpm/webui.py").read_bytes() == b"# producer overlay\n"
    assert (root / "consumer/cdpm/webui.py").read_bytes() == b"# synthetic producer\n"
    assert not (root / "producer/retired.py").exists()
    assert (root / "consumer/retired.py").exists()
    assert not (root / "site-data/qualification/own-executors-registry.json").exists()
    assert not list(root.rglob(".git"))
    assert not list(root.rglob(".venv"))
    assert not list(root.rglob("auth.json"))
    for kind, rel in ic.EXACT_EXAMPLES.items():
        assert (root / rel).read_bytes() == encode(case["examples"][kind])
    for file in root.rglob("*"):
        if file.is_file():
            assert file.stat().st_nlink == 1
    (root / "producer/webapp/app.py").write_text("# isolated")
    assert (root / "consumer/webapp/app.py").read_bytes() == b"# synthetic consumer\n"
    config = json.loads((root / "exact-launch.json").read_bytes())
    assert {Path(config["services"][s]["cwd"]).name for s in ("webapp", "cdpm", "catalog")} == set(ir.ROLES)
    assert config["env"]["CREW_CATALOG_URL"] == "https://catalog.example.org/"
    assert config["env"]["CDPM_PUBLIC_URL"] == "http://127.0.0.1:18095"
    assert config["env"]["CREW_CHANNEL_URL"] == "http://127.0.0.1:18090"
    assert config["env"]["CDPM_EXPORT_SPEC_VERSION"] == "1.21"
    assert config["env"]["CREW_CONTOUR"] == "local"
    assert config["env"]["CREW_LLM_DEFAULT_BACKEND"] == "local"
    assert "CREW_LLM_BACKEND" not in config["env"]
    assert config["services"]["solver"]["argv"][-1] == str(root / "native/quarkus-app/quarkus-run.jar")
    assert "own-site executor qualification" in receipt["pending"]
    assert "legacy uninstall" in (root / "EXACT-INSTALL.txt").read_text()


def test_exact_cli_reaches_offline_mode_and_probe_writes_nothing(case, capsys):
    assert ic.main(cli(case, "--probe")) == 0
    no_output(case)
    assert "probe-only" in capsys.readouterr().out
    assert ic.main(cli(case)) == 0
    assert "not started and not live-accepted" in capsys.readouterr().out


@pytest.mark.parametrize("option", ["--exact-profile", "--exact-profile-sha256",
                                   "--exact-bundle", "--exact-site", "--java-home", "--target"])
def test_incomplete_exact_options_never_fall_back(case, option):
    args = cli(case)
    i = args.index(option)
    del args[i:i + 2]
    assert ic.main(args) == 2
    no_output(case)


@pytest.mark.parametrize("extra", [("--repo", "x"), ("--port", "18000"),
                                  ("--catalog-port", "18001"), ("--answers", "no-file")])
def test_mixed_legacy_flags_refused(case, extra):
    assert ic.main(cli(case, *extra)) == 2
    no_output(case)


@pytest.mark.parametrize("component", ["platform", "python", "java"])
def test_runtime_mismatch_no_output_no_fallback(case, monkeypatch, component):
    value = copy.deepcopy(case["profile"]["runtime"])
    value[component]["extra"] = "different"
    monkeypatch.setattr(ic, "_exact_runtime", lambda p: value)
    assert ic.main(cli(case)) == 2
    no_output(case)


@pytest.mark.parametrize("change", ["schema", "platform", "python", "java", "extra", "distributions"])
def test_unsupported_profile_rejected_before_any_output(case, change):
    p = case["profile"]
    if change == "schema":
        p["schema"] = "v2"
    elif change == "extra":
        p["secret"] = "forbidden"
    elif change == "platform":
        p["runtime"]["platform"]["system"] = "Linux"
    elif change == "python":
        p["runtime"]["python"]["version"] = "3.15.0"
    elif change == "java":
        p["runtime"]["java"]["version"] = "17.0.1"
    else:
        p["runtime"]["python"]["distributions"] = {}
    with pytest.raises(ir.ReleaseError):
        prepare(case)
    no_output(case)


@pytest.mark.parametrize("path", ["../base.zip", "/base.zip", "C:/base.zip", "x\\base.zip",
                                ".env", "con.zip"])
def test_profile_local_input_paths_cannot_escape_or_name_secrets(case, path):
    case["profile"]["artifacts"]["base"] = path
    with pytest.raises(ir.ReleaseError):
        prepare(case)
    no_output(case)


def test_duplicate_case_alias_inputs_refused(case):
    case["profile"]["artifacts"]["producer"] = "BASE.zip"
    with pytest.raises(ir.ReleaseError):
        prepare(case)
    no_output(case)


@pytest.mark.parametrize("kind", ["airline_rules", "agents"])
def test_tampered_example_and_embedded_secret_refused(case, kind):
    path = case["bundle"] / (kind + ".json")
    path.write_bytes(b"{}")
    with pytest.raises(ir.ReleaseError, match="example-hash"):
        prepare(case)
    case["profile"]["examples"][kind]["sha256"] = sha(b"{}")
    with pytest.raises(ir.ReleaseError):
        prepare(case)
    doc = copy.deepcopy(case["examples"][kind])
    doc["api_key"] = "not-a-real-key"
    path.write_bytes(encode(doc))
    case["profile"]["examples"][kind]["sha256"] = sha(encode(doc))
    with pytest.raises(ir.ReleaseError):
        prepare(case)
    no_output(case)


@pytest.mark.parametrize("change", ["ports", "boolport", "dev_auth", "secret", "qualification",
                                  "mixed_policy", "url", "credentials_url", "contour", "backend", "none_backend"])
def test_unsafe_or_unknown_site_choices_refused(case, change):
    s = case["site"]
    if change == "ports":
        s["ports"]["cdpm"] = s["ports"]["webapp"]
    elif change == "boolport":
        s["ports"]["webapp"] = True
    elif change == "dev_auth":
        s["auth"]["mode"] = "dev"
    elif change == "secret":
        s["auth"]["api_key"] = "not-real"
    elif change == "qualification":
        s["registry"] = "TEST-qualified.json"
    elif change == "mixed_policy":
        s["llm_backend"] = "cloud"
    elif change == "url":
        s["public_catalog_url"] = "javascript:alert(1)"
    elif change == "contour":
        s["contour"] = "internal"
    elif change == "backend":
        s["llm_backend"] = "ollama"
    elif change == "none_backend":
        s["llm_backend"] = "none"
    else:
        s["public_catalog_url"] = "https://user:pass@example.org"
    with pytest.raises(ir.ReleaseError):
        prepare(case)
    no_output(case)


def test_access_public_navigation_is_preserved_without_changing_machine_callback(case):
    case["site"]["contour"] = "prod"
    case["site"]["auth"].update(mode="access", access_team="own-team", access_aud="public-aud-metadata")
    prepare(case)
    c = json.loads((case["target"] / "exact-launch.json").read_bytes())
    assert c["env"]["CREW_WEBAPP_COOKIE_SECURE"] == "1"
    assert c["env"]["CREW_ACCESS_TEAM"] == "own-team"
    assert c["env"]["CREW_CATALOG_URL"] == "https://catalog.example.org/"
    assert c["env"]["CREW_CDPM_RECORD_API"] == "http://127.0.0.1:18095"
    assert c["env"]["CREW_CONTOUR"] == "prod"


def test_profile_pin_and_duplicate_json_are_not_ignored(case):
    digest = save(case)
    case["profile_path"].write_bytes(b"{}")
    with pytest.raises(ir.ReleaseError, match="install-profile-hash"):
        ic.prepare_exact_install(case["profile_path"], digest, case["bundle"],
                                 case["site_path"], case["java"], case["target"])
    case["profile_path"].write_bytes(b'{"schema":1,"schema":2}')
    with pytest.raises(ir.ReleaseError, match="duplicate-json"):
        ic.prepare_exact_install(case["profile_path"], sha(case["profile_path"].read_bytes()),
                                 case["bundle"], case["site_path"], case["java"], case["target"])
    no_output(case)


@pytest.mark.parametrize("probe", [True, False])
def test_zip_tamper_stays_closed_even_for_probe(case, probe):
    (case["bundle"] / "base.zip").write_bytes(b"wrong")
    with pytest.raises(ir.ReleaseError):
        prepare(case, probe=probe)
    no_output(case)


def test_existing_target_is_never_modified_even_when_empty(case):
    case["target"].mkdir()
    with pytest.raises(ir.ReleaseError, match="destination-exists"):
        prepare(case)
    assert list(case["target"].iterdir()) == []
    sentinel = case["target"] / "existing.txt"
    sentinel.write_bytes(b"must survive")
    with pytest.raises(ir.ReleaseError):
        prepare(case)
    assert sentinel.read_bytes() == b"must survive"


def test_late_configuration_failure_removes_only_own_paths(case, monkeypatch):
    old = case["root"] / "old"
    old.mkdir()
    (old / "keep.txt").write_bytes(b"keep")
    original = Path.open
    def failure(path, *args, **kwargs):
        if path.name == "exact-launch.json" and args and args[0] == "xb":
            raise OSError("synthetic write failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", failure)
    with pytest.raises(OSError, match="synthetic write failure"):
        prepare(case)
    no_output(case)
    assert (old / "keep.txt").read_bytes() == b"keep"


def test_foreign_file_on_failure_is_preserved_not_recursively_deleted(case, monkeypatch):
    original = Path.open
    def failure(path, *args, **kwargs):
        if path.name == "exact-launch.json" and args and args[0] == "xb":
            (case["target"] / "foreign.txt").write_bytes(b"do not erase")
            raise OSError("failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", failure)
    with pytest.raises(ir.ReleaseError, match="exact-install-cleanup-incomplete"):
        prepare(case)
    assert (case["target"] / "foreign.txt").read_bytes() == b"do not erase"


def launcher_namespace(case):
    prepare(case)
    source = (case["target"] / "run-exact.py").read_text(encoding="utf-8")
    ns = {"__name__": "synthetic_import_not_main", "__file__": str(case["target"] / "run-exact.py")}
    exec(compile(source, ns["__file__"], "exec"), ns)
    return ns


def mock_launch_runtime(case, monkeypatch):
    monkeypatch.setattr(ic.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ic.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(ic.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(ic.platform, "python_version", lambda: "3.14.6")
    monkeypatch.setattr(ic.importlib.metadata, "distributions",
                        lambda: [SimpleNamespace(metadata={"Name": "fixture-only"}, version="1.0")])


@pytest.mark.parametrize("service", ["webapp", "cdpm", "catalog", "solver"])
def test_generated_launcher_argv_env_and_cwd_without_launching(case, monkeypatch, service):
    ns = launcher_namespace(case)
    mock_launch_runtime(case, monkeypatch)
    monkeypatch.setenv("COSMOS_EXACT_CHANNEL_TOKEN", "b" * 64)
    monkeypatch.setenv("CREW_CATALOG_URL", "http://wrong-site.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-key")
    monkeypatch.setenv("PYTHONPATH", "wrong-tree")
    got = []
    monkeypatch.setattr(ic.subprocess, "call", lambda *a, **kw: got.append((a, kw)) or 0)
    assert ns["main"]([service]) == 0
    assert len(got) == 1
    argv, kwargs = got[0][0][0], got[0][1]
    assert kwargs["shell"] is False
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "COSMOS_EXACT_CHANNEL_TOKEN" not in kwargs["env"]
    assert "PYTHONPATH" not in kwargs["env"]
    assert kwargs["env"]["CREW_CATALOG_URL"] == "https://catalog.example.org/"
    assert kwargs["env"]["CDPM_EXPORT_SPEC_VERSION"] == "1.21"
    if service in ("webapp", "cdpm"):
        assert kwargs["env"]["CREW_CHANNEL_TOKEN"] == "b" * 64
    assert Path(kwargs["cwd"]).name == {"webapp": "consumer", "cdpm": "producer",
                                       "catalog": "catalog", "solver": "native"}[service]
    assert all(isinstance(s, str) for s in argv)


@pytest.mark.parametrize("failure", ["token", "metadata", "config", "java", "receipt", "service"])
def test_generated_launcher_refuses_before_subprocess_on_drift(case, monkeypatch, failure):
    ns = launcher_namespace(case)
    mock_launch_runtime(case, monkeypatch)
    monkeypatch.setenv("COSMOS_EXACT_CHANNEL_TOKEN", "b" * 64)
    called = []
    monkeypatch.setattr(ic.subprocess, "call", lambda *a, **kw: called.append(a))
    args = ["webapp"]
    if failure == "token":
        monkeypatch.delenv("COSMOS_EXACT_CHANNEL_TOKEN")
    elif failure == "metadata":
        monkeypatch.setattr(ic.platform, "python_version", lambda: "3.14.99")
    elif failure == "config":
        (case["target"] / "exact-launch.json").write_bytes(b"{}")
    elif failure == "java":
        (case["java"] / "bin/java.exe").write_bytes(b"changed")
    elif failure == "receipt":
        (case["target"] / "exact-install-receipt.json").write_bytes(b"{}")
    else:
        args = ["unknown"]
    with pytest.raises(SystemExit, match="EXACT LAUNCH REFUSED"):
        ns["main"](args)
    assert not called


def test_legacy_branch_remains_distinct_without_network_or_install(case, monkeypatch):
    calls = []
    class Survey:
        def __init__(self, *args):
            calls.append("legacy-survey")
        def run(self):
            calls.append("legacy-run")
        def report(self):
            pass
        def get(self, key):
            return None
        def blockers(self):
            return [SimpleNamespace(name="python", detail="synthetic stop")]
    monkeypatch.setattr(ic, "Survey", Survey)
    # РВ-143 (owner's word 04.10.2026): the legacy rolling installer is reached
    # only by the explicit development switch, never by default.
    assert ic.main(["--dev-legacy", "--probe", "--target", str(case["target"])]) == 2
    assert calls == ["legacy-survey", "legacy-run"]
    no_output(case)


def test_default_is_the_pinned_release_path_not_legacy(case, monkeypatch, capsys):
    """РВ-143: with no options the user gets the pinned release path. Without a
    pinned profile digest it refuses with words, never a rolling checkout."""
    calls = []
    monkeypatch.setattr(ic, "Survey", lambda *a: calls.append("legacy-survey"))
    monkeypatch.setitem(ic.RELEASE, "profile_sha256", None)
    assert ic.main(["--target", str(case["target"])]) == 2
    assert calls == []
    out = capsys.readouterr().out
    assert "RELEASE REFUSED" in out and ic.RELEASE["tag"] in out and "--dev-legacy" in out
    no_output(case)


def test_dev_legacy_cannot_be_mixed_with_exact_inputs(case, capsys):
    args = ["--dev-legacy", "--exact-profile", str(case["target"]),
            "--exact-profile-sha256", "0" * 64, "--exact-bundle", str(case["target"]),
            "--exact-site", str(case["target"]), "--java-home", str(case["target"]),
            "--target", str(case["target"])]
    assert ic.main(args) == 2
    assert "EXACT REFUSED" in capsys.readouterr().out
    no_output(case)


def test_runtime_reader_checks_real_local_executable_and_java_bytes_without_execution(case, monkeypatch):
    mock_launch_runtime(case, monkeypatch)
    assert REAL_RUNTIME_READER(case["java"]) == case["profile"]["runtime"]
    (case["java"] / "release").write_bytes(b'JAVA_VERSION="21.0.9"\n')
    actual = REAL_RUNTIME_READER(case["java"])
    assert actual["java"]["version"] == "21.0.9"
    assert actual["java"] != case["profile"]["runtime"]["java"]
    (case["java"] / "release").write_bytes(b"invalid metadata")
    with pytest.raises(ir.ReleaseError, match="java-release-version"):
        REAL_RUNTIME_READER(case["java"])
    no_output(case)


def test_distribution_names_are_canonical_and_duplicates_are_refused(monkeypatch):
    monkeypatch.setattr(ic.importlib.metadata, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": "Synthetic_Test.Name"}, version="1.2")])
    assert ic._exact_distributions() == {"synthetic-test-name": "1.2"}
    monkeypatch.setattr(ic.importlib.metadata, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": "Synthetic_Test.Name"}, version="1.2"),
        SimpleNamespace(metadata={"Name": "synthetic-test-name"}, version="1.2")])
    with pytest.raises(ir.ReleaseError, match="runtime-distribution-metadata"):
        ic._exact_distributions()
