"""A loaded package's projection status reaches the actual human run journal.

Synthetic package and solver health; stop BEFORE payload building/solver start.
The real producer gate, consumer load, button journal and CLI remain exercised.
"""
import json

import pytest

from cdpm.gates import gate_task_package as producer
from vmp import objective_projection as op
from vmp.run import button
from webapp import task_package as consumer
from test_objective_projection_boundaries import package, clear_catalogue_cache


class NoExecution:
    def health(self):
        return True

    def __getattr__(self, name):
        raise AssertionError("A display test must not call the solver: " + name)


@pytest.mark.parametrize("spec,hide_status", [
    ("1.18", False), ("1.20", False), ("1.20", True)])
def test_button_persists_projection_scope_before_any_execution(
        tmp_path, monkeypatch, spec, hide_status):
    path = package(tmp_path, monkeypatch, spec=spec)
    loaded = []
    callbacks = []

    def load(path):
        pkg = consumer.load(path, run_gate=False)
        assert pkg["objective_projection"]["judged"] is (spec == "1.20")
        loaded.append(pkg)
        if hide_status:
            del pkg["objective_projection"]
        return pkg

    def stop_before_payload(pkg):
        assert loaded and pkg is loaded[0]
        raise button.ButtonStop("display-only stop", "no solver execution")

    result = button.orchestrate(
        tmp_path, NoExecution(), package_path=path,
        coverage_fn=lambda *_: None, load_pkg=load, to_payload=stop_before_payload,
        log=lambda *entry: callbacks.append(entry))
    assert result["verdict"] == "display-only stop", result
    assert len(loaded) == 1  # A previous unrelated refusal is not a passing test.
    journal = json.loads((tmp_path / "button" / "log.json").read_text(encoding="utf-8"))
    projection = [entry for entry in journal if entry["step"] == "Ш-1ц"]
    assert len(projection) == 1, journal
    entry = projection[0]
    assert entry in result["journal"]
    assert (entry["step"], entry["status"], entry["detail"]) in callbacks
    delivery = json.loads((tmp_path / "button" / "delivery.json").read_text(encoding="utf-8"))
    assert entry in delivery["journal"]
    if hide_status:
        assert entry["status"] == "не установлено"
    elif spec == "1.18":
        assert entry["status"] == "не судится"
        assert entry["detail"] == op.archive_words("1.18")
        assert "не есть разрешение исполнения" in entry["detail"]
    else:
        assert entry["status"] == "пройден"
        assert "судимой части" in entry["detail"]


def test_producer_cli_says_when_projection_was_not_judged(
        tmp_path, monkeypatch, capsys):
    path = package(tmp_path, monkeypatch, spec="1.18")
    assert producer.run(str(path)) == 0
    assert op.archive_words("1.18") in capsys.readouterr().out
