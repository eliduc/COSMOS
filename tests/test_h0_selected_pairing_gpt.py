"""H0 step 3: explicit run selection, byte provenance and flight partition.

Источник — тест GPT (Test legion, шаг 3, 05.09.2026), адаптирован к
реализации main единой версии 06.09.2026 (строки W-3…W-6, решение
GPT-011): попытки пейринга и снимки — attempts/<id>/button-pairing
(аудит A10), утверждённость — вердикт «пейринг построен» (approved_pairing
main), выдача ростеринга на пейринге — files.rostering_on_pairing,
холодный (сквозной) ростеринг остаётся штатным путём и НЕ подставляет
последний пейринг.

These unit checks need no live services, solver or model.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from webapp import package_runs as pr


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def selected(tmp_path, monkeypatch):
    from webapp import task_package
    package = tmp_path / "package.zip"
    manifest = b'{"spec_version":"1.17"}'
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", manifest)
    sha = hashlib.sha256(package.read_bytes()).hexdigest()
    fp = hashlib.sha256(manifest).hexdigest()
    flights = [{"flightNumber": n, "departureAirport": "PLY", "arrivalAirport": "SVG",
                "departureUTCDateTime": "2026-09-05T07:00:00Z",
                "arrivalUTCDateTime": "2026-09-05T08:00:00Z"} for n in ("F1", "F2")]
    monkeypatch.setattr(task_package, "load", lambda *_a, **_kw: {})
    monkeypatch.setattr(task_package, "to_payload", lambda *_a: {"flights": flights})
    wd = tmp_path / "runs"
    recs = []
    remotes = {}
    for attempt, run, parts in (("older", "chosen", [["F1"], ["F2"]]),
                                 ("newer", "not-chosen", [["F1", "F2"]])):
        root = wd / pr.ATTEMPTS_DIR / attempt / "button-pairing"
        _write(root / "builder-catalog.json", {"selected": [{"fns": x} for x in parts]})
        digest = hashlib.sha256((root / "builder-catalog.json").read_bytes()).hexdigest()
        (root / "package.zip").write_bytes(package.read_bytes())
        _write(root / "builder-payload.json", {"flights": flights})
        # Утверждённость — вердикт выдачи, как у approved_pairing main.
        _write(root / "delivery.json", {"verdict": "пейринг построен",
                                        "schedule": {"solver_run_id": run},
                                        "catalog_sha256": digest, "chains": len(parts),
                                        "trace": {"package_fingerprint": fp}})
        recs.append({"id": attempt, "task": "pairing", "done": True,
                     "engine": "cpsat", "finished_at": "2026-09-05T12:00:00"})
        remotes[run] = {"runId": run, "task": "pairing", "state": "готов",
                        "packageFingerprint": fp, "catalogSha256": digest}
    _write(wd / pr.STATE_NAME, {"attempts": recs})

    class Solver:
        def __init__(self):
            self.seen = []

        def status(self, run_id):
            self.seen.append(run_id)
            return remotes[run_id]

    solver = Solver()
    return {"wd": wd, "pkg": package, "sha": sha, "solver": solver,
            "remotes": remotes, "flights": flights,
            "root": wd / pr.ATTEMPTS_DIR / "older" / "button-pairing"}


def _validate(s, run="chosen"):
    return pr.validate_pairing_selection(s["wd"], s["pkg"], s["sha"], run, s["solver"])


def test_older_explicit_run_is_selected_not_latest(selected):
    got = _validate(selected)
    assert got["pairing_run_id"] == "chosen" and got["attempt_id"] == "older"
    assert got["verified"] and got["flight_count"] == 2
    assert got["solver_catalog"]["confirmed"] is True
    assert selected["solver"].seen == ["chosen"]
    # Кандидаты состояния: обе попытки, новые первыми, порядок ≠ выбор.
    cands = pr._pairing_candidates(selected["wd"], pr.read_state(selected["wd"]))
    assert [c["pairing_run_id"] for c in cands] == ["not-chosen", "chosen"]
    assert all(c["approved"] for c in cands)


@pytest.mark.parametrize("field,value", [("runId", "different"), ("task", "rostering"),
                                         ("state", "считает"), ("packageFingerprint", "bad"),
                                         ("catalogSha256", "bad")])
def test_solver_record_mismatch_fails_closed(selected, field, value):
    selected["remotes"]["chosen"][field] = value
    with pytest.raises(pr.PackageRunError):
        _validate(selected)


def test_solver_without_catalog_sha_is_unconfirmed_not_green(selected):
    """Статус прогона решателя main не несёт catalogSha256: показание
    честно «решателем не подтверждён» — не отказ и не зелёный."""
    del selected["remotes"]["chosen"]["catalogSha256"]
    got = _validate(selected)
    assert got["verified"] and got["solver_catalog"]["confirmed"] is None
    assert "не подтверждён" in got["solver_catalog"]["note"]


def test_unknown_run_never_falls_back(selected):
    with pytest.raises(pr.PackageRunError, match="не найден"):
        _validate(selected, "unknown")
    assert selected["solver"].seen == []


def test_changed_package_rejected(selected):
    (selected["root"] / "package.zip").write_bytes(b"changed")
    with pytest.raises(pr.PackageRunError, match="пакет"):
        _validate(selected)


def test_changed_catalog_rejected(selected):
    path = selected["root"] / "builder-catalog.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(pr.PackageRunError, match="SHA"):
        _validate(selected)


def test_changed_flight_details_rejected(selected):
    path = selected["root"] / "builder-payload.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["flights"][0]["arrivalAirport"] = "NOT-CURRENT"
    _write(path, payload)
    with pytest.raises(pr.PackageRunError, match="состав или данные рейсов"):
        _validate(selected)


def test_transport_is_in_composition_but_not_required_active_coverage(selected):
    flights = selected["flights"]
    flights.append(dict(flights[0], flightNumber="TRANSPORT", transportOnly=True))
    root = selected["root"]
    _write(root / "builder-payload.json", {"flights": flights})
    _write(root / "builder-catalog.json", {"selected": [
        {"fns": ["F1", "TRANSPORT", "F2"], "active_fns": ["F1", "F2"]}]})
    digest = hashlib.sha256((root / "builder-catalog.json").read_bytes()).hexdigest()
    delivery = json.loads((root / "delivery.json").read_text(encoding="utf-8"))
    delivery["catalog_sha256"] = digest
    _write(root / "delivery.json", delivery)
    selected["remotes"]["chosen"]["catalogSha256"] = digest
    got = _validate(selected)
    assert got["flight_count"] == 3 and got["obligation_flight_count"] == 2


@pytest.mark.parametrize("parts", [[["F1"]], [["F1"], ["F1", "F2"]],
                                  [["F1", "F2", "FOREIGN"]]])
def test_catalog_must_partition_current_flights(selected, parts):
    root = selected["root"]
    path = root / "builder-catalog.json"
    _write(path, {"selected": [{"active_fns": x} for x in parts]})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    delivery = json.loads((root / "delivery.json").read_text(encoding="utf-8"))
    delivery["catalog_sha256"] = digest
    _write(root / "delivery.json", delivery)
    selected["remotes"]["chosen"]["catalogSha256"] = digest
    with pytest.raises(pr.PackageRunError, match="полным разбиением|чужие рейсы") as err:
        _validate(selected)
    if "полным разбиением" in str(err.value):
        assert "не доказывает невозможность" in str(err.value)


def _public(selected, monkeypatch):
    monkeypatch.setattr(pr, "current", lambda: {"path": str(selected["pkg"]),
                                                "sha": selected["sha"]})
    monkeypatch.setattr(pr, "workdir_for", lambda _sha: selected["wd"])
    monkeypatch.setattr(pr, "package_scope", lambda _path: "local-only")
    monkeypatch.setattr(pr, "certifier_identity", lambda _scope: {"scope": "local-only"})


def _fake_orchestrate(calls, result):
    """Оркестратор стенда: пишет выдачу теми же полями выбора, что
    боевой _deliver (selected_pairing_fields), — чтобы страница и файл
    судились по одному источнику."""
    from vmp.run import button

    def orchestrate(wd, solver, **kwargs):
        calls.append(kwargs)
        out = Path(wd) / ("button-on-pairing" if kwargs.get("pairing_run_id") else "button")
        out.mkdir(parents=True, exist_ok=True)
        delivery = {"verdict": "недопустимо" if result else "остановка", "result": result}
        if kwargs.get("pairing_run_id"):
            delivery.update(button.selected_pairing_fields(
                kwargs["pairing_run_id"], kwargs.get("pairing_selection"), result,
                kwargs.get("selection_note")))
        (out / "delivery.json").write_text(json.dumps(delivery, ensure_ascii=False),
                                           encoding="utf-8")
        return delivery
    return orchestrate


@pytest.mark.parametrize("run_id", [None, "", "   "])
def test_public_roster_press_blank_run_id_is_cold_path_not_last_pairing(
        selected, monkeypatch, run_id):
    """GPT-011: холодный (сквозной) ростеринг не запрещается; пустой или
    пробельный pairingRunId — это НЕ выбор и НЕ подстановка последнего
    пейринга: оркестратор идёт без прогона, в каталог button/."""
    from vmp.run import button
    _public(selected, monkeypatch)
    calls = []
    monkeypatch.setattr(button, "orchestrate",
                        _fake_orchestrate(calls, {"feasibility": "confirmed"}))
    st = pr.press("rostering", pairing_run_id=run_id, solver=selected["solver"],
                  start_thread=False, certify_fn=None, pairing_certify_fn=None)
    assert st["done"] and st["error"] is None
    assert calls[0]["pairing_run_id"] is None and "package_from" not in calls[0]
    assert "pairing_selection" not in calls[0]
    assert st["pairing_selection"] is None and st["next_actions"] == []
    assert st["roster_path"] == "cold"
    assert selected["solver"].seen == []          # выбор не подставлялся


def test_public_unknown_run_is_refused_not_replaced(selected, monkeypatch):
    """Недопустимый ID не подменяется ни последним, ни холодным путём."""
    from vmp.run import button
    _public(selected, monkeypatch)
    calls = []
    monkeypatch.setattr(button, "orchestrate", _fake_orchestrate(calls, None))
    with pytest.raises(pr.PackageRunError, match="не соответствует прогону пейринга"):
        pr.press("rostering", pairing_run_id="unknown", solver=selected["solver"],
                 start_thread=False, certify_fn=None, pairing_certify_fn=None)
    assert calls == [] and selected["solver"].seen == []
    assert pr.status()["task"] is None            # кнопка не занята


def test_public_press_records_selected_scope_and_retry_actions(selected, monkeypatch):
    from vmp.run import button
    _public(selected, monkeypatch)
    calls = []
    monkeypatch.setattr(button, "orchestrate",
                        _fake_orchestrate(calls, {"feasibility": "violated"}))
    st = pr.press("rostering", pairing_run_id="chosen", solver=selected["solver"],
                  start_thread=False, certify_fn=None, pairing_certify_fn=None)
    assert st["done"] and st["error"] is None
    assert calls[0]["pairing_run_id"] == "chosen"
    # Прежняя (не последняя) попытка: байты и выдача пейринга — из её снимка.
    assert calls[0]["package_from"] == "attempts/older/button-pairing"
    assert calls[0]["pairing_selection"]["attempt_id"] == "older"
    assert st["pairing_selection"]["attempt_id"] == "older"
    assert st["pairing_selection"]["verified"] is True
    assert st["next_actions"] == ["reselect_pairing", "expand_catalog"]
    assert "не доказывает невозможность" in st["selection_note"]
    assert [c["pairing_run_id"] for c in st["pairing_candidates"]] == ["not-chosen", "chosen"]
    path = selected["wd"] / "button-on-pairing/delivery.json"
    delivery = json.loads(path.read_text(encoding="utf-8"))
    assert delivery["feasibility_scope"] == "selected_pairing_catalog"
    assert delivery["original_problem_infeasibility_proven"] is False
    assert delivery["pairing_selection"]["attempt_id"] == "older"
    assert "выдача-кнопки-на-пейринге" in st["files"]["rostering_on_pairing"]
    assert st["roster_path"] == "on-pairing"
    # Запись попытки несёт выбор.
    assert st["attempts"][0]["pairing_selection"]["pairing_run_id"] == "chosen"


def test_orchestrate_delivery_carries_selected_scope_on_stop(tmp_path):
    """Боевой оркестратор: любой исход на выбранном пейринге (здесь —
    остановка Ш-0) несёт область допустимости и следующие шаги в самой
    выдаче, а при подтверждённой допустимости подсказки нет."""
    from vmp.run import button as btn

    class Dead:
        base = "http://none"

        def health(self):
            return False

    d = btn.orchestrate(tmp_path, Dead(), task="rostering", pairing_run_id="chosen",
                        pairing_selection={"attempt_id": "older"},
                        selection_note=pr.PAIRING_RETRY_NOTE)
    assert d["verdict"].startswith("остановка")
    assert d["feasibility_scope"] == "selected_pairing_catalog"
    assert d["next_actions"] == ["reselect_pairing", "expand_catalog"]
    assert d["original_problem_infeasibility_proven"] is False
    assert d["pairing_selection"] == {"attempt_id": "older"}
    on_disk = json.loads((tmp_path / "button-on-pairing" / "delivery.json")
                         .read_text(encoding="utf-8"))
    assert on_disk["selection_note"] == pr.PAIRING_RETRY_NOTE
    ok = btn.selected_pairing_fields("chosen", None, {"feasibility": "confirmed"}, "x")
    assert ok == {"feasibility_scope": "selected_pairing_catalog"}
    cold = btn.orchestrate(tmp_path, Dead(), task="rostering")
    assert "feasibility_scope" not in cold and "next_actions" not in cold


def test_ui_selects_pairing_run_visibly():
    """UI CLD (GPT-011): видимый выбор утверждённого прогона из
    st.pairing_candidates; кнопка «на пейринге» шлёт выбранный прогон,
    холодная кнопка «Ростеринг» остаётся без pairingRunId."""
    source = (Path(__file__).resolve().parents[1] / "webapp/static/app.js").read_text(encoding="utf-8")
    block = source[source.index("function vmpRunBlock("):source.index("function vmpChainsGantt(")]
    assert "st.pairing_candidates" in block
    assert 't("flow.vmp.run.choosePairing")' in block
    assert 't("flow.vmp.run.selectedPairing")' in block
    assert 'q += "&pairingRunId=" + encodeURIComponent(pairingRunId)' in block
    assert 'btnR.onclick = function () { press("rostering"); };' in block
    assert "st.selection_note" in block and "st.next_actions" in block
