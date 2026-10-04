# -*- coding: utf-8 -*-
"""РВ-145 (слово владельца 04.10): быстрый список прогонов для экрана
«Все прогоны обеих систем» (ORBITA) — `GET /api/runs?brief=1`.

Готовность линии правил (`_readiness_of`) грузит и пересчитывает модель
каждого прогона; в кратком списке её нет: `readiness` — null, прочие
поля строки те же, полный список прежний."""
import pytest

from cdpm import webui
from test_cdpm_webui import _extract_and_wait, _upload, client  # noqa: F401


@pytest.fixture(autouse=True)
def _s06r2_declared_layout(declared_test_layout):
    """S06-R2: извлечение требует объявленной раскладки ролей (conftest)."""


def _two_runs(client):  # noqa: F811
    extracted = _upload(client).json()["run"]
    st = _extract_and_wait(client, extracted)
    assert st["state"] == "извлечено", st
    bare = _upload(client).json()["run"]
    return extracted, bare


def test_brief_list_has_no_readiness_and_same_fields(client):  # noqa: F811
    extracted, bare = _two_runs(client)
    full = {r["run"]: r for r in client.get("/api/runs").json()}
    assert full[extracted]["readiness"] is not None, "полный — с готовностью"
    r = client.get("/api/runs", params={"brief": 1})
    assert r.status_code == 200, r.text
    brief = {x["run"]: x for x in r.json()}
    assert list(brief) == list(full) == [extracted, bare]
    for run_id, row in brief.items():
        assert row["readiness"] is None
        assert set(row) == set(full[run_id])
        assert {k: v for k, v in row.items() if k != "readiness"} \
            == {k: v for k, v in full[run_id].items() if k != "readiness"}


def test_brief_list_never_loads_models(client, monkeypatch):  # noqa: F811
    _two_runs(client)

    def _boom(run_dir):
        raise AssertionError(f"краткий список грузит модель {run_dir.name}")

    monkeypatch.setattr(webui, "_readiness_of", _boom)
    assert client.get("/api/runs", params={"brief": "1"}).status_code == 200
    assert client.get("/api/runs", params={"brief": "true"}).status_code == 200
    with pytest.raises(AssertionError, match="грузит модель"):
        client.get("/api/runs")


def test_full_list_unchanged_without_flag(client):  # noqa: F811
    extracted, _ = _two_runs(client)
    plain = client.get("/api/runs").json()
    explicit = client.get("/api/runs", params={"brief": 0}).json()
    assert plain == explicit
    row = {x["run"]: x for x in plain}[extracted]
    assert set(row["readiness"]) == {"state", "label"}
