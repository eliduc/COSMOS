"""Единый манифест постановки (аудит R10, P0-03): контент-хэши всего,
что образует задачу, записанные один раз и неизменяемо; идентификатор
пересчитывается при чтении; подмена файла между построением команды и
манифестом — отказ."""
import hashlib
import json

import pytest

from webapp import problem_manifest as pm


def _payload(tmp_path, body='{"flights": []}'):
    p = tmp_path / "payload.json"
    p.write_text(body, encoding="utf-8")
    return p


def _frozen(tmp_path):
    d = tmp_path / "run-config"
    d.mkdir()
    return {"dir": d, "files": {"rules": str(d / "rules.json")},
            "fingerprints": {"rules": "aaaa", "cost": "bbbb", "pay": "cccc"}}


INST = {"id": "upload-x", "label": "x", "payload_path": "data/x.json"}


def test_build_hashes_all_files_and_is_deterministic(tmp_path):
    pay = _payload(tmp_path)
    rules = tmp_path / "source_rules_x.json"
    rules.write_text('{"parameters": {}}', encoding="utf-8")
    fp = hashlib.sha256(rules.read_bytes()).hexdigest()
    cmds = [["python", "_pairing_builder.py", "--payload", str(pay),
             "--source-rules", str(rules), "--source-rules-fp", fp]]
    frozen = _frozen(tmp_path)
    m1 = pm.build("stage1", INST, cmds, "run1", frozen, {"script": "s"})
    m2 = pm.build("stage1", INST, cmds, "run1", frozen, {"script": "s"})
    assert m1["manifest_id"] == m2["manifest_id"]
    body = m1["body"]
    assert body["files"]["payload"]["sha256"] == hashlib.sha256(
        pay.read_bytes()).hexdigest()
    assert body["files"]["source_rules"]["sha256"] == fp
    assert body["config_domains"]["rules"] == "aaaa"
    # содержимое меняет личность
    pay.write_text('{"flights": [1]}', encoding="utf-8")
    m3 = pm.build("stage1", INST, cmds, "run1", frozen, {"script": "s"})
    assert m3["manifest_id"] != m1["manifest_id"]


def test_build_refuses_command_fp_mismatch(tmp_path):
    """Файл подменён между построением команды (где посчитан fp) и
    манифестом — отказ постановки, а не тихая замена."""
    pay = _payload(tmp_path)
    rules = tmp_path / "source_rules_x.json"
    rules.write_text('{"parameters": {}}', encoding="utf-8")
    cmds = [["python", "_pairing_builder.py", "--payload", str(pay),
             "--source-rules", str(rules),
             "--source-rules-fp", "0" * 64]]
    with pytest.raises(pm.ManifestError, match="подменён"):
        pm.build("stage1", INST, cmds, "run1", _frozen(tmp_path), {})


def test_build_records_catalog_hash_and_upstream_chain(tmp_path):
    """Каталог этапа 1 в постановке этапа 2: контент-хэш + цепочка
    manifest_id из config_sig каталога."""
    pay = _payload(tmp_path)
    cat = tmp_path / "cat.json"
    cat.write_text(json.dumps(
        {"selected": [], "config_sig": {"problem_manifest_id": "f" * 64}}),
        encoding="utf-8")
    cat_fp = hashlib.sha256(cat.read_bytes()).hexdigest()
    cmds = [["python", "_gerad_stage2_roster.py", "--payload", str(pay),
             "--catalog", str(cat), "--catalog-fp", cat_fp]]
    m = pm.build("stage2", INST, cmds, "run2", _frozen(tmp_path), {})
    assert m["body"]["files"]["stage1_catalog"]["sha256"] == cat_fp
    assert m["body"]["upstream_manifest_id"] == "f" * 64


def test_write_is_immutable_and_load_verifies(tmp_path):
    pay = _payload(tmp_path)
    frozen = _frozen(tmp_path)
    m = pm.build("stage1", INST,
                 [["python", "x.py", "--payload", str(pay)]],
                 "run3", frozen, {})
    path = pm.write(frozen["dir"], m)
    # второй манифест того же прогона — отказ, не перезапись
    with pytest.raises(pm.ManifestError, match="уже существует"):
        pm.write(frozen["dir"], m)
    rec = pm.load(path)
    assert rec["manifest_id"] == m["manifest_id"]
    # правка тела после записи — идентификатор не сходится
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["body"]["label"] = "другой"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pm.ManifestError, match="не сходится"):
        pm.load(path)


def test_missing_file_is_loud(tmp_path):
    cmds = [["python", "x.py", "--payload", str(tmp_path / "нет.json")]]
    with pytest.raises(pm.ManifestError, match="нечитаем"):
        pm.build("stage1", INST, cmds, "run4", _frozen(tmp_path), {})
