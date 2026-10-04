# -*- coding: utf-8 -*-
"""Экспорт CPLEX: сопровождение CDPM (шапка, суффикс, манифест).

Главные свойства: байты модели ПОСЛЕ шапки нетронуты (сумма оригинала
в манифесте), каждая строка шапки — комментарий своего формата (MPS
«*», LP «\\»), обязательные оговорки «чего в модели нет» на месте,
неполный справочник — честный отказ."""
from __future__ import annotations

import json

import pytest

from vmp.run import mps_manifest as mm


def _map_doc():
    return {"variables": {"x0": {"fns": ["QZ100|2026-01-05T10:00:00"],
                                 "cost": 300}},
            "constraints": {"cov_QZ100": {"kind": "cover",
                                          "flight": "QZ100"}},
            "objective": {"kind": "min_person_days", "sense": "min"},
            "pool": 204, "obligations": 39,
            "uncoverable_flights": ["QZ919"],
            "fixed_zero_vars": []}


def _setup(tmp_path):
    mps = tmp_path / "model.mps"
    mps.write_bytes(b"NAME COVER\nROWS\nENDATA\n")
    lp = tmp_path / "model.lp"
    lp.write_bytes(b"Minimize\n obj: x0\nEnd\n")
    mp = tmp_path / "model.mps.map.json"
    mp.write_text(json.dumps(_map_doc(), ensure_ascii=False),
                  encoding="utf-8")
    return mps, lp, mp


def test_шапка_комментарием_и_байты_модели_нетронуты(tmp_path):
    mps, lp, mp = _setup(tmp_path)
    out = tmp_path / "export"
    man = mm.build_export(out, mps_path=mps, lp_path=lp, map_path=mp,
                          package_sha256="ab" * 32,
                          source_label="кнопка №1 run-0001")
    assert len(man["files"]) == 2
    for f in man["files"]:
        data = (out / f["name"]).read_bytes()
        mark = b"*" if f["kind"] == "mps" else b"\\"
        head, _, _ = data.partition(b"NAME" if f["kind"] == "mps"
                                    else b"Minimize")
        for line in head.splitlines():
            if line.strip():
                assert line.startswith(mark), line
        # Байты модели после шапки — оригинал (сумма совпадает).
        import hashlib
        tail = data[len(head):]
        assert hashlib.sha256(tail).hexdigest() \
            == f["original_sha256"]
        assert "-partial-set-cover" in f["name"]


def test_манифест_несёт_оговорки_и_счётчики(tmp_path):
    mps, lp, mp = _setup(tmp_path)
    out = tmp_path / "export"
    man = mm.build_export(out, mps_path=mps, map_path=mp,
                          package_sha256="cd" * 32,
                          source_label="тест")
    assert man["pool"] == 204 and man["obligations"] == 39
    assert man["uncoverable_flights"] == ["QZ919"]
    text = json.dumps(man, ensure_ascii=False)
    assert "легальности" in text and "полное пространство" in text
    assert "сертификация CDPM" in man["judge"]
    saved = json.loads((out / "export-manifest.json")
                       .read_text(encoding="utf-8"))
    assert saved["package_sha256"] == "cd" * 32
    # Шапка файла тоже несёт оговорки.
    body = next(out.glob("*-partial-set-cover.mps")) \
        .read_text(encoding="utf-8")
    assert "ЧЕГО В МОДЕЛИ ПРИНЦИПИАЛЬНО НЕТ" in body
    assert "partial set cover" in body


def test_неполный_справочник_честный_отказ(tmp_path):
    mps, lp, mp = _setup(tmp_path)
    doc = _map_doc()
    del doc["pool"]
    mp.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(mm.MpsManifestError) as e:
        mm.build_export(tmp_path / "o", mps_path=mps, map_path=mp,
                        package_sha256="ee" * 32, source_label="т")
    assert "pool" in str(e.value)


def test_без_единого_файла_модели_отказ(tmp_path):
    _mps, _lp, mp = _setup(tmp_path)
    with pytest.raises(mm.MpsManifestError):
        mm.build_export(tmp_path / "o", map_path=mp,
                        package_sha256="ff" * 32, source_label="т")
