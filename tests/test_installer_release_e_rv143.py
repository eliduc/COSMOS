"""РВ-143 E: путь пользователя — загрузка ассетов Release со сверкой SHA,
выбор площадки, токен канала, запуск четырёх служб и пробы.

Без сети и без запуска: загрузка — подменённый opener, процессы — записывающий
popen, пробы — подменённый HTTP."""
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import install_cosmos as ic
import installer_release as ir
from test_installer_exact_release import case, encode, sha  # noqa: F401
from test_installer_release_v2_rv143 import PLAT, install, v2  # noqa: F401


def published(c):
    """Release page of the synthetic v2 bundle: flat asset names → bytes."""
    profile_raw = encode(c["profile"])
    pages = {"profile.json": profile_raw}
    for path in [c["profile"]["manifest"]["path"], *c["profile"]["artifacts"].values(),
                 *(p["path"] for p in c["profile"]["examples"].values()),
                 *(s["lock"]["path"] for s in c["profile"]["platforms"].values())]:
        pages[path.rsplit("/", 1)[-1]] = (c["bundle"] / path).read_bytes()
    return pages, sha(profile_raw)


class Opener:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.urls.append(url)
        prefix = "https://github.com/%s/releases/download/%s/" % (
            ic.RELEASE["repository"], ic.RELEASE["tag"])
        assert url.startswith(prefix), url
        name = url[len(prefix):]
        if name not in self.pages:
            raise OSError("404 " + name)
        return io.BytesIO(self.pages[name])


def test_download_checks_every_asset_and_lays_out_profile_paths(v2, tmp_path):
    pages, digest = published(v2)
    opener = Opener(pages)
    dest = tmp_path / "dl"
    profile_path = ic.download_release(dest, digest, opener)
    assert opener.urls[0].endswith("/profile.json")
    assert profile_path.read_bytes() == pages["profile.json"]
    for spec in v2["profile"]["platforms"].values():
        assert (dest / spec["lock"]["path"]).is_file()
    for name in v2["profile"]["artifacts"].values():
        assert (dest / name).read_bytes() == (v2["bundle"] / name).read_bytes()


@pytest.mark.parametrize("bad", ["profile", "lock", "archive"])
def test_download_refuses_any_tampered_asset(v2, tmp_path, bad):
    pages, digest = published(v2)
    if bad == "profile":
        digest = "0" * 64
    elif bad == "lock":
        pages["requirements-lock-%s.txt" % PLAT] += b"# changed\n"
    else:
        pages["base.zip"] += b"x"
    with pytest.raises(ir.ReleaseError):
        ic.download_release(tmp_path / "dl", digest, Opener(pages))


def test_release_site_choices_are_local_token_mode_with_admin_and_language():
    args = SimpleNamespace(port=None, catalog_port=None, admin_email="op@example.org", lang="ru")
    site = ic._exact_site(ic.release_site_choices(args))
    assert site["ports"] == {"webapp": ic.PORT_DEFAULT, "cdpm": ic.CDPM_PORT,
                             "catalog": ic.CATALOG_PORT, "solver": ic.SOLVER_PORT}
    assert site["auth"]["mode"] == "token" and site["auth"]["super_admin"] == "op@example.org"
    assert site["language"] == "ru" and site["data_policy"] == "internal"


def test_site_gets_its_own_channel_token_and_product_version(v2):
    install(v2)
    site = v2["install_root"] / "site"
    token = (site / "site-data/channel.token").read_text(encoding="ascii")
    assert re.fullmatch(r"[0-9a-f]{64}", token)
    launch = (site / "exact-launch.json").read_text(encoding="utf-8")
    assert token not in launch
    assert json.loads(launch)["env"]["CREW_PRODUCT_VERSION"] == ic.PRODUCT_VERSION


def test_channel_token_is_created_owner_only(v2, monkeypatch):
    """miniblack 04.10: umask 002 left the token 0664. The secret must be
    created with mode 0600 (os.open); other additions keep Path.open("xb")."""
    modes = {}
    real_open = ic.os.open

    def spy(path, flags, mode=0o777, *a, **kw):
        modes[Path(path).name] = mode
        return real_open(path, flags, mode, *a, **kw)

    monkeypatch.setattr(ic.os, "open", spy)
    install(v2)
    assert modes == {"channel.token": 0o600}
    if ic.os.name != "nt":
        site = v2["install_root"] / "site"
        assert (site / "site-data/channel.token").stat().st_mode & 0o077 == 0


class Proc:
    """Launcher process stand-in: alive until `dies_after` polls."""
    count = 0

    def __init__(self, dies_after=None, code=1):
        Proc.count += 1
        self.pid, self.polls, self.dies_after, self.code = 1000 + Proc.count, 0, dies_after, code

    def poll(self):
        self.polls += 1
        if self.dies_after is not None and self.polls > self.dies_after:
            return self.code
        return None


def free(port):
    return True


def test_start_site_launches_four_services_through_run_exact_and_probes(v2):
    install(v2)
    root = v2["install_root"]
    started, probed = [], []

    def popen(argv, **kw):
        started.append((argv, kw["cwd"]))
        return Proc()

    def probe(url, timeout=5):
        probed.append(url)
        return 200, "ok"

    status = ic.start_site(root, popen=popen, probe=probe, sleep=lambda s: None, port_free=free)
    launch = json.loads((root / "site/exact-launch.json").read_bytes())
    assert [a[-1] for a, _ in started] == ["solver", "catalog", "cdpm", "webapp"]
    assert all(a[0] == launch["python"] and a[-2].endswith("run-exact.py") for a, _ in started)
    ports = v2["site"]["ports"]
    assert probed == ["http://127.0.0.1:%d/schedules" % ports["solver"],
                      "http://127.0.0.1:%d/healthz" % ports["catalog"],
                      "http://127.0.0.1:%d/api/runs?brief=1" % ports["cdpm"],
                      "http://127.0.0.1:%d/healthz" % ports["webapp"]]
    assert all(s["ok"] for s in status.values())
    assert len({s["pid"] for s in status.values()}) == 4


def test_start_site_reports_a_silent_service_instead_of_claiming_success(v2):
    install(v2)
    clock = iter(range(0, 10_000, 100))
    status = ic.start_site(v2["install_root"], wait_s=250,
                           popen=lambda argv, **kw: Proc(),
                           probe=lambda url, timeout=5: (0, "refused") if "schedules" in url else (200, ""),
                           sleep=lambda s: None, clock=lambda: next(clock), port_free=free)
    assert status["solver"]["ok"] is False and status["webapp"]["ok"] is True
    assert "deadline" in status["solver"]["reason"]


def test_dead_launcher_is_a_failure_even_if_something_answers_200(v2):
    """GPT 16:45: a foreign service on the port must not pass for ours."""
    install(v2)
    procs = {}

    def popen(argv, **kw):
        procs[argv[-1]] = Proc(dies_after=0 if argv[-1] == "cdpm" else None, code=3)
        return procs[argv[-1]]

    status = ic.start_site(v2["install_root"], popen=popen,
                           probe=lambda url, timeout=5: (200, "someone else"),
                           sleep=lambda s: None, port_free=free)
    assert status["cdpm"]["ok"] is False and "exited with code 3" in status["cdpm"]["reason"]
    assert status["solver"]["ok"] and status["webapp"]["ok"]


def test_occupied_port_refuses_and_starts_nothing(v2):
    install(v2)
    started = []
    busy = v2["site"]["ports"]["catalog"]
    status = ic.start_site(v2["install_root"], popen=lambda argv, **kw: started.append(argv),
                           probe=lambda url, timeout=5: (200, ""), sleep=lambda s: None,
                           port_free=lambda port: port != busy)
    assert started == []
    assert not any(s["ok"] for s in status.values())
    assert "port %d already in use" % busy in status["catalog"]["reason"]


@pytest.mark.parametrize("argv,words", [
    (["--admin-email", "op@example.org"], "no pinned profile SHA-256"),
    (["--bundle", "x", "--admin-email", "op@example.org"], "--profile-sha256"),
    (["--bundle", "x", "--profile-sha256", "0" * 64], "--admin-email is required"),
])
def test_release_path_refuses_with_words_and_changes_nothing(argv, words, capsys, monkeypatch, tmp_path):
    monkeypatch.setitem(ic.RELEASE, "profile_sha256", None)
    assert ic.main(argv + ["--target", str(tmp_path / "cosmos")]) == 2
    assert words in capsys.readouterr().out
    assert not (tmp_path / "cosmos").exists()


def test_release_path_from_local_bundle_prepares_the_site(v2, capsys, monkeypatch):
    monkeypatch.setattr(ic.subprocess, "run", v2["machine"])
    digest = sha(encode(v2["profile"]))
    (v2["bundle"] / "profile.json").write_bytes(encode(v2["profile"]))
    code = ic.main(["--bundle", str(v2["bundle"]), "--profile-sha256", digest,
                    "--admin-email", "op@example.org", "--lang", "ru",
                    "--target", str(v2["install_root"])])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "prepared" in out and "not started" in out
    site = json.loads((v2["install_root"] / "site/exact-site.json").read_bytes())
    assert site["language"] == "ru" and site["auth"]["super_admin"] == "op@example.org"
