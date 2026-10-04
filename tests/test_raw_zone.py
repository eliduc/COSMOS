"""Неизменяемая Raw-зона (Ф3): запись исходников + манифест SHA-256,
write-once, санитизация имён, обработка traversal-подобных id."""
import hashlib
import json

import pytest

from webapp import raw_zone


@pytest.fixture()
def zone(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_zone, "RAW_DIR", tmp_path / "raw")
    return tmp_path / "raw"


def test_store_writes_files_and_manifest(zone):
    data = "Рейс;Откуда\nSU100;SVO\n".encode("utf-8")
    m = raw_zone.store("upload-u--m1",
                       [{"name": "рейсы марта.csv", "data": data},
                        {"name": "crew.xlsx", "data": b"\x00\x01"}],
                       "user@example.com")
    assert m["instance"] == "upload-u--m1" and m["by"] == "user@example.com"
    assert len(m["files"]) == 2
    rec = m["files"][0]
    # cyrillic stem transliterated, extension kept
    assert rec["stored_as"].endswith(".csv")
    assert rec["name"] == "рейсы марта.csv"
    assert rec["sha256"] == hashlib.sha256(data).hexdigest()
    assert rec["bytes"] == len(data)
    stored = zone / "upload-u--m1" / rec["stored_as"]
    assert stored.read_bytes() == data                  # byte-exact
    on_disk = json.loads((zone / "upload-u--m1" / "manifest.json")
                         .read_text(encoding="utf-8"))
    assert on_disk == m
    assert raw_zone.manifest_for("upload-u--m1") == m


def test_zone_is_write_once(zone):
    raw_zone.store("i1", [{"name": "a.csv", "data": b"x"}], "u@e")
    with pytest.raises(raw_zone.RawZoneError, match="write-once"):
        raw_zone.store("i1", [{"name": "b.csv", "data": b"y"}], "u@e")
    # the original survived untouched
    assert raw_zone.manifest_for("i1")["files"][0]["name"] == "a.csv"


def test_filename_sanitized_against_traversal(zone):
    m = raw_zone.store("i2",
                       [{"name": "..\\..\\evil.txt", "data": b"x"},
                        {"name": "../../../etc/passwd", "data": b"y"}],
                       "u@e")
    for rec in m["files"]:
        assert "/" not in rec["stored_as"] and "\\" not in rec["stored_as"]
        assert ".." not in rec["stored_as"]
        assert (zone / "i2" / rec["stored_as"]).is_file()
    # nothing escaped the zone dir
    assert not (zone.parent / "evil.txt").exists()


def test_name_collisions_get_suffixes(zone):
    m = raw_zone.store("i3",
                       [{"name": "data.csv", "data": b"1"},
                        {"name": "DATA.CSV", "data": b"2"},
                        {"name": "manifest.json", "data": b"3"}],
                       "u@e")
    stored = [r["stored_as"] for r in m["files"]]
    assert len(set(s.lower() for s in stored)) == 3
    assert "manifest.json" not in [s.lower() for s in stored]


def test_empty_store_creates_nothing(zone):
    assert raw_zone.store("i4", [], "u@e") is None
    assert not (zone / "i4").exists()
    assert raw_zone.manifest_for("i4") is None


def test_traversal_id_reads_as_no_zone(zone):
    assert raw_zone.manifest_for("../evil") is None
    with pytest.raises(raw_zone.RawZoneError):
        raw_zone.store("../evil", [{"name": "a", "data": b"x"}], "u@e")


def test_discard_removes_zone(zone):
    raw_zone.store("i5", [{"name": "a.csv", "data": b"x"}], "u@e")
    raw_zone.discard("i5")
    assert raw_zone.manifest_for("i5") is None
