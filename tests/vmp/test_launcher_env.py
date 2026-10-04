# -*- coding: utf-8 -*-
"""РВ-138, исправление 3 — класс «вне окружения пусковика».

Стенд с решателем или с записью результатов требует полного окружения
пусковика площадки; без него — отказ словами до старта. Явный ключ
--outside-launcher пропускает, но шапка результатов называет недостающие
переменные, и дверь привязок такой файл не принимает."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

le = pytest.importorskip("vmp.launcher_env")
st = pytest.importorskip("vmp.stand_tasks")


def _full_env(tmp_path) -> dict:
    cosmos = tmp_path / "cosmos-bindings.json"
    cdpm = tmp_path / "cdpm-bindings.json"
    cosmos.write_bytes(b'{"catalog_version": "0.9.0"}')
    cdpm.write_bytes(b'{"catalog_version": "0.9.0"}')
    return {"CREW_CONTOUR": "prod", "CDPM_EXPORT_SPEC_VERSION": "1.21",
            "CREW_TEMPLATE_CATALOG": str(tmp_path / "catalog"),
            "CREW_COSMOS_BINDINGS": str(cosmos), "CREW_CDPM_BINDINGS": str(cdpm),
            "CREW_CHANNEL_TOKEN": "секрет-не-для-паспорта"}


def test_неполное_окружение_называет_все_недостающие():
    assert le.missing({}) == list(le.REQUIRED)
    env = {"CREW_CONTOUR": "prod", "CDPM_EXPORT_SPEC_VERSION": "  "}
    assert le.missing(env) == [k for k in le.REQUIRED if k != "CREW_CONTOUR"]


def test_паспорт_полного_окружения_без_секретов(tmp_path):
    env = _full_env(tmp_path)
    p = le.passport(env)
    assert p["launcher"] is True and p["missing"] == []
    assert p["contour"] == "prod" and p["spec_version"] == "1.21"
    assert p["cosmos_bindings_sha256"] == hashlib.sha256(
        Path(env["CREW_COSMOS_BINDINGS"]).read_bytes()).hexdigest()
    assert "секрет" not in json.dumps(p, ensure_ascii=False)
    assert le.refusal(env, purpose="стенд") is None


def test_стенд_с_решателем_вне_пусковика_отказ_до_старта(tmp_path, monkeypatch, capsys):
    for k in le.REQUIRED:
        monkeypatch.delenv(k, raising=False)
    target = tmp_path / "stand-results.json"
    code = st.main(["--with-solver", "http://127.0.0.1:9", "--write-results",
                    str(target), "--tmp-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 2
    assert le.OUTSIDE_WORDS in out and "CDPM_EXPORT_SPEC_VERSION" in out
    assert not target.exists()


def test_запись_результатов_вне_пусковика_отказ_до_старта(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CDPM_EXPORT_SPEC_VERSION", raising=False)
    target = tmp_path / "stand-results.json"
    code = st.main(["--write-results", str(target), "--tmp-dir", str(tmp_path)])
    assert code == 2
    assert "CDPM_EXPORT_SPEC_VERSION" in capsys.readouterr().out
    assert not target.exists()


def test_живой_прогон_run_tasks_вне_пусковика_отказ_до_сборки_пакета(monkeypatch):
    """Единственный путь стенда к пакету — живой прогон; программный вызов
    мимо команды отказывает так же, до каталога и до первой сборки."""
    for k in le.REQUIRED:
        monkeypatch.delenv(k, raising=False)
    built = []
    from vmp import stand_solver
    monkeypatch.setattr(stand_solver, "package_bytes", lambda *a, **k: built.append(1))
    with pytest.raises(st.StandRunError) as e:
        st.run_tasks(solver_url="http://127.0.0.1:9")
    assert "CDPM_EXPORT_SPEC_VERSION" in str(e.value) and not built


class _Cat:
    def version(self):
        return "0.9.0"

    def fingerprint(self):
        return "f" * 64


def _doc(environment):
    doc = {"form_version": 1, "catalog_version": "0.9.0",
           "catalog_fingerprint": "f" * 64, "code": st.code_fingerprints(),
           "at": "2026-10-04T00:00:00+00:00", "commit": "a" * 40,
           "solver": None, "results": []}
    if environment is not None:
        doc["environment"] = environment
    return doc


def test_файл_снятый_вне_пусковика_не_свеж_для_привязок():
    reasons = st.stale_reasons(_doc(le.passport({})), _Cat())
    assert any(le.OUTSIDE_WORDS in r for r in reasons), reasons


def test_файл_из_пусковика_и_прежний_без_шапки_судятся_как_раньше(tmp_path):
    assert st.stale_reasons(_doc(le.passport(_full_env(tmp_path))), _Cat()) == []
    assert st.stale_reasons(_doc(None), _Cat()) == []
