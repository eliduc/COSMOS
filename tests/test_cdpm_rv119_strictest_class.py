# -*- coding: utf-8 -*-
"""РВ-119/РВ-138 (04.10.2026): класс канала стадий, смешивающих все
файлы, без настройки файлов прогона берётся из признака комплекта по
классу данных (kit_scope), а не тихим «local» кода (стенд Stage 03.10
19:4x ушёл в локальный канал без модели)."""
import json

from cdpm import webui


def _run(tmp_path, status):
    d = tmp_path / "run-0001"
    d.mkdir()
    (d / "status.json").write_text(json.dumps(status, ensure_ascii=False),
                                   encoding="utf-8")
    return d


def test_no_files_synthetic_kit_goes_cloud(tmp_path):
    d = _run(tmp_path, {"state": "новый", "files": [],
                        "kit": {"data_class": "synthetic"}})
    assert webui._strictest_class(d) == "cloud"


def test_no_files_proprietary_or_no_kit_goes_local(tmp_path):
    d = _run(tmp_path, {"state": "новый", "files": [],
                        "kit": {"data_class": "proprietary"}})
    assert webui._strictest_class(d) == "local"
    d2 = tmp_path / "x"
    d2.mkdir()
    d2 = _run(d2, {"state": "новый", "files": []})
    assert webui._strictest_class(d2) == "local"


def test_explicit_file_policies_still_decide(tmp_path):
    d = _run(tmp_path, {"state": "новый", "files": ["a.docx", "b.docx"],
                        "kit": {"data_class": "synthetic"}})
    (d / "files-config.json").write_text(json.dumps({"files": {
        "a.docx": {"processing": "cloud", "note": ""},
        "b.docx": {"processing": "local", "note": ""}}}), encoding="utf-8")
    assert webui._strictest_class(d) == "local"
