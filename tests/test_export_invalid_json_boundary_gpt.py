"""Real byte reader/export boundary; no external service or verdict stubs.

The healthy control stops at the next carrier stage to observe its exact input.
This is a parser-boundary check, not a claim of full package acceptance.
"""
import json

import pytest

from cdpm import export_task_package as producer


def put_model(tmp_path, raw):
    path = tmp_path / "extract" / "model.json"
    path.parent.mkdir()
    path.write_bytes(raw)
    return path


@pytest.mark.parametrize("raw", [
    b'{"rules":[],"value":NaN}',
    b'{"rules":[],"value":Infinity}',
    b'{"rules":[],"value":-Infinity}',
    b'{"rules":[],"value":1e999}',
    b'{"rules":[],"value":-1e999}',
    b'{"rules":[],"nested":{"value":NaN}}',
    b'{"rules":[],"value":',
    b'{"rules":[],"value":"\xff"}',
    b'{"rules":[],"value":3,"value":30}',
])
def test_unreadable_bytes_are_a_domain_refusal_before_carriers(tmp_path, monkeypatch, raw):
    path = put_model(tmp_path, raw)

    def forbidden(_doc):
        pytest.fail("unreadable model reached carrier inspection")

    monkeypatch.setattr(producer, "refuse_diverging_carriers", forbidden)
    with pytest.raises(producer.TaskPackageError) as error:
        producer.build_package(tmp_path)
    assert "модель прогона нечитаема" in str(error.value)
    assert error.value.code is None  # Established form-error contract, not a new code.
    assert path.read_bytes() == raw


@pytest.mark.parametrize("raw", [b'[]', b'null', b'true', b'1', b'1.5', b'"model"'])
def test_non_object_json_is_a_domain_refusal(tmp_path, raw):
    path = put_model(tmp_path, raw)
    with pytest.raises(producer.TaskPackageError) as error:
        producer.build_package(tmp_path)
    assert "модель прогона" in str(error.value) and "объект" in str(error.value)
    assert error.value.code is None
    assert path.read_bytes() == raw


@pytest.mark.parametrize("fmt", ["task-package", "task-package-send"])
@pytest.mark.parametrize("raw", [b'{"value":NaN}', b'{"value":"\xff"}', b'[]'])
def test_public_export_handlers_return_409_without_publishing(tmp_path, monkeypatch, fmt, raw):
    from cdpm import webui

    path = put_model(tmp_path, raw)
    monkeypatch.setattr(webui, "_run_dir", lambda _run_id: tmp_path)
    response = webui.export_for_solver("invalid-model-boundary", fmt)
    assert response.status_code == 409
    body = json.loads(response.body)
    assert "модель прогона" in body["detail"]
    assert body["code"] is None and body["items"] == []
    assert path.read_bytes() == raw
    assert not (tmp_path / "channel").exists()


@pytest.mark.parametrize("raw,expected", [
    (b'{"rules":[],"value":0}', 0),
    (b'{"rules":[],"value":1e308}', 1e308),
    (b'{"rules":[],"value":-1e308}', -1e308),
    (b'{"rules":[],"value":"NaN Infinity 1e999"}', "NaN Infinity 1e999"),
])
def test_readable_bytes_reach_real_next_stage_unchanged(tmp_path, monkeypatch, raw, expected):
    path = put_model(tmp_path, raw)
    seen = []

    class ReachedCarriers(Exception):
        pass

    def observe(doc):
        seen.append(doc)
        raise ReachedCarriers

    monkeypatch.setattr(producer, "refuse_diverging_carriers", observe)
    with pytest.raises(ReachedCarriers):
        producer.build_package(tmp_path)
    assert seen == [{"rules": [], "value": expected}]
    assert path.read_bytes() == raw
