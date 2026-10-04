"""РВ-143 (B): архив «native» выпуска — гейт, происхождение, упаковка и приём разбором установщика.

Без Maven: на вход подаётся готовый набор файлов quarkus-app и подложный кэш Maven
(jar + *.jar.sha1, как его кладёт Maven при скачивании с Central).
"""
import hashlib
import io
import tarfile
import zipfile

import pytest

import installer_release as ir
from tools import build_native as bn

THIRD = {"x.txt": b"password=AAAAAAAAAAAAAAAAAAAA"}  # сторонний jar: «password=» не судится, он доказан суммой
# Приманки гейта собираются при выполнении: в исходнике нет строки, похожей на
# ключ, — иначе гейт публикации (export_gate) справедливо примет тест за утечку.
KEY_NAME = "api" + "_key"
FAKE_VALUE = "0123456789" + "abcdef0123"
PEM_HEAD = "-----BEGIN " + "RSA PRIVATE " + "KEY-----"
SK_ANT = "sk-" + "ant-"

UI = {"META-INF/resources/openapi-ui/ui.js": b"var token = 'abcdefghijklmnopqrstuv';",
      "META-INF/resources/openapi-ui/index.html": b"<html>upstream</html>"}


def _jar(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(zipfile.ZipInfo(name, bn.ZIP_TIME), data)
    return buf.getvalue()


def _put(m2, g, a, v, data, sha1=None):
    d = m2.joinpath(*g.split("."), a, v)
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s-%s.jar" % (a, v))).write_bytes(data)
    (d / ("%s-%s.jar.sha1" % (a, v))).write_text(sha1 or hashlib.sha1(data).hexdigest())


@pytest.fixture
def m2(tmp_path):
    root = tmp_path / "m2"
    _put(root, "org.example", "third", "1.0", _jar(THIRD))
    _put(root, "io.smallrye", "smallrye-open-api-ui", "4.2.4", _jar(UI))
    return root


def _app(**extra) -> dict:
    files = {
        "quarkus-app/quarkus-run.jar": _jar({"META-INF/MANIFEST.MF": b"Main-Class: io.quarkus.Main\n"}),
        "quarkus-app/app/solver.jar": _jar({
            "org/acme/A.class": b"\xca\xfe\xba\xbe",
            "application.properties": b"quarkus.http.port=9091\n",
            "META-INF/services/io.quarkus.arc.ComponentsProvider": b"io.quarkus.arc.X\n"}),
        "quarkus-app/quarkus/generated-bytecode.jar": _jar({
            "io/quarkus/Gen.class": b"\xca\xfe\xba\xbe",
            "META-INF/io.smallrye_smallrye-open-api-ui__jar/META-INF/resources/openapi-ui/ui.js":
                UI["META-INF/resources/openapi-ui/ui.js"],
            # Quarkus подменяет страницу интерфейса: не равна артефакту, судится как своя
            "META-INF/io.smallrye_smallrye-open-api-ui__jar/META-INF/resources/openapi-ui/index.html":
                b"<html>quarkus</html>"}),
        "quarkus-app/lib/main/org.example.third-1.0.jar": _jar(THIRD),
        "quarkus-app/quarkus/quarkus-application.dat": b"\x00\x01",
        "quarkus-app/quarkus-app-dependencies.txt": b"org.example:third::jar:1.0\n",
    }
    files.update(extra)
    return files


def _spec(raw: bytes, files: dict) -> dict:
    return {"sha256": hashlib.sha256(raw).hexdigest(), "files": bn.file_map(files)}


def test_clean_app_passes_gate_with_proven_provenance_and_materializer_accepts(m2):
    files = _app()
    prov = bn.gate(files, m2)
    assert prov["lib"] == {"quarkus-app/lib/main/org.example.third-1.0.jar": "org.example:third:1.0"}
    assert list(prov["relocated"]) == ["io.smallrye:smallrye-open-api-ui:4.2.4"]
    assert [p.rsplit("/", 1)[-1] for p in prov["relocated_changed_by_build"]] == ["index.html"]
    raw = bn.pack(files)
    bn.self_check(raw, _spec(raw, files))


def test_pack_is_byte_stable():
    files = _app()
    assert bn.pack(files) == bn.pack(dict(reversed(list(files.items()))))


def test_archive_entries_are_regular_files_without_dirs():
    with zipfile.ZipFile(io.BytesIO(bn.pack(_app()))) as z:
        infos = z.infolist()
    assert infos and all(not i.is_dir() and i.date_time == bn.ZIP_TIME for i in infos)
    assert all((i.external_attr >> 16) & 0o777 == 0o644 for i in infos)


@pytest.mark.parametrize("secret", [
    ("anthropic=" + SK_ANT + "a" * 30).encode(),
    (KEY_NAME + " = " + FAKE_VALUE).encode(),
    PEM_HEAD.encode(),
])
def test_key_sign_in_app_jar_refuses(m2, secret):
    files = _app(**{"quarkus-app/app/solver.jar": _jar({"application.properties": secret})})
    with pytest.raises(bn.BuildError, match="признак ключа"):
        bn.gate(files, m2)


def test_unknown_file_kind_in_app_jar_refuses(m2):
    files = _app(**{"quarkus-app/app/solver.jar": _jar({"data/customer.xlsx": b"PK"})})
    with pytest.raises(bn.BuildError, match="вид файла вне списка"):
        bn.gate(files, m2)


def test_private_path_refuses_by_materializer_rules(m2):
    files = _app(**{"quarkus-app/secrets/x.txt": b"x"})
    with pytest.raises(ir.ReleaseError, match="private-path"):
        bn.gate(files, m2)


# GPT 12:25: приватные пути внутри jar не проверялись.
@pytest.mark.parametrize("inner", ["secrets/credentials.json", "META-INF/credentials.json", "webapp/config/site.json"])
def test_private_path_inside_app_jar_refuses(m2, inner):
    files = _app(**{"quarkus-app/app/solver.jar": _jar({inner: b"{}"})})
    with pytest.raises(bn.BuildError, match="путь внутри jar отклонён"):
        bn.gate(files, m2)


# GPT 12:25: «…__jar/» обходил проверку текста одним именем.
def test_fake_relocated_name_does_not_bypass_text_check(m2):
    files = _app(**{"quarkus-app/app/solver.jar": _jar({
        "META-INF/customer__jar/private.json": ('{"%s": "%s"}' % (KEY_NAME, FAKE_VALUE)).encode()})})
    with pytest.raises(bn.BuildError, match="признак ключа"):
        bn.gate(files, m2)


def test_relocated_from_unknown_artifact_refuses(m2):
    files = _app(**{"quarkus-app/app/solver.jar": _jar({
        "META-INF/com.acme_private-data__jar/x.json": b"{}"})})
    with pytest.raises(bn.BuildError, match="происхождение не доказано"):
        bn.gate(files, m2)


def test_relocated_changed_text_is_still_key_checked(m2):
    files = _app(**{"quarkus-app/quarkus/generated-bytecode.jar": _jar({
        "META-INF/io.smallrye_smallrye-open-api-ui__jar/META-INF/resources/openapi-ui/ui.js":
            ("var %s = '%s';" % (KEY_NAME, FAKE_VALUE)).encode()})})
    with pytest.raises(bn.BuildError, match="признак ключа"):
        bn.gate(files, m2)


def test_relocated_changed_binary_other_than_image_refuses(m2):
    files = _app(**{"quarkus-app/quarkus/generated-bytecode.jar": _jar({
        "META-INF/io.smallrye_smallrye-open-api-ui__jar/META-INF/resources/openapi-ui/font.woff2": b"\x00evil"})})
    with pytest.raises(bn.BuildError, match="двоичная запись не равна"):
        bn.gate(files, m2)


def test_lib_jar_not_in_dependencies_refuses(m2):
    files = _app(**{"quarkus-app/lib/main/org.example.extra-1.0.jar": _jar(THIRD)})
    with pytest.raises(bn.BuildError, match="вне списка зависимостей"):
        bn.gate(files, m2)


def test_lib_jar_differs_from_maven_artifact_refuses(m2):
    files = _app(**{"quarkus-app/lib/main/org.example.third-1.0.jar": _jar({"x.txt": b"other"})})
    with pytest.raises(bn.BuildError, match="не равен артефакту Maven"):
        bn.gate(files, m2)


def test_maven_cache_with_wrong_central_sum_refuses(tmp_path):
    root = tmp_path / "m2"
    _put(root, "org.example", "third", "1.0", _jar(THIRD), sha1="0" * 40)
    _put(root, "io.smallrye", "smallrye-open-api-ui", "4.2.4", _jar(UI))
    with pytest.raises(bn.BuildError, match="sha1 не равна сумме Central"):
        bn.gate(_app(), root)


# GPT 12:48: пара «jar + .sha1» в кэше сама Central не доказывает — сверка с опубликованной суммой.
def _online(m2, published):
    def fetch(url):
        if url not in published:
            raise OSError("нет ответа")
        return published[url]
    return lambda root, coords: bn.central_online(root, coords, fetch=fetch)


def _published(m2, override=None):
    out = {}
    for jar in m2.rglob("*.jar"):
        rel = jar.relative_to(m2).as_posix()
        out[bn.CENTRAL + "/" + rel + ".sha1"] = hashlib.sha1(jar.read_bytes()).hexdigest() + "  " + jar.name
    out.update(override or {})
    return out


def test_central_online_records_url_and_sum_for_every_artifact(m2):
    prov = bn.gate(_app(), m2, _online(m2, _published(m2)))
    check = prov["central_check"]
    assert check["mode"] == "central-online"
    assert set(check["artifacts"]) == {"org.example:third:1.0", "io.smallrye:smallrye-open-api-ui:4.2.4"}
    assert all(v["url"].startswith(bn.CENTRAL + "/") and len(v["sha1"]) == 40 for v in check["artifacts"].values())


def test_cache_pair_agreeing_but_differing_from_central_refuses(m2):
    url = bn.CENTRAL + "/org/example/third/1.0/third-1.0.jar.sha1"
    with pytest.raises(bn.BuildError, match="не равна опубликованной на Central"):
        bn.gate(_app(), m2, _online(m2, _published(m2, {url: "0" * 40})))


def test_central_unreachable_refuses_not_silently_trusts(m2):
    with pytest.raises(bn.BuildError, match="Central недоступен"):
        bn.gate(_app(), m2, _online(m2, {}))


def test_without_online_check_boundary_is_written(m2):
    check = bn.gate(_app(), m2)["central_check"]
    assert check["mode"] == "local-cache-only" and "граница" not in check["mode"] and check["boundary"]


# GPT 12:25: вход сборки — байты git, а не файлы с диска (игнорируемые не попадают).
def test_build_input_is_git_bytes_without_ignored_files(tmp_path):
    out = bn.export(tmp_path)
    tracked = set(bn._git("ls-files", "--", bn.SOLVER).splitlines())
    got = {p.relative_to(tmp_path).as_posix() for p in out.rglob("*") if p.is_file()}
    assert got and got <= tracked
    assert not any("/target/" in p for p in got)


def test_maven_pin_comes_from_wrapper():
    assert bn.maven_distribution().endswith("/apache-maven-3.9.16-bin.zip")


# Квитанция exact-установки называет CPLEX лишь находкой; движок по умолчанию — CP-SAT.
from test_installer_exact_release import case, prepare  # noqa: E402,F401


def test_exact_receipt_names_cplex_and_cpsat_default(case, monkeypatch):
    import install_cosmos
    monkeypatch.setattr(install_cosmos, "find_cplex", lambda: None)
    receipt = prepare(case)
    assert receipt["cplex"] == {"found": None, "default_engine": "cpsat"}
    assert receipt["activated"] is False
