"""Non-finite JSON cannot enter or overwrite a model snapshot."""
import json

import pytest

from cdpm import model as m


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
@pytest.mark.parametrize("reader", [m.loads_strict, m.loads_model])
def test_nonfinite_input_is_rejected_before_migration(literal, reader):
    raw = ('{"meta":{"revision":' + literal + '},"rules":[],"questions":[]}').encode()
    with pytest.raises(ValueError, match="JSON"):
        reader(raw)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("existing", [False, True])
def test_dump_refuses_nonfinite_without_creating_or_truncating_file(tmp_path, value, existing):
    path = tmp_path / "model.json"
    before = b'{"meta":{"revision":7}}\n'
    if existing:
        path.write_bytes(before)
    with pytest.raises(ValueError):
        m.dump_model({"meta": {"revision": value}}, path)
    assert path.read_bytes() == before if existing else not path.exists()


def test_finite_numbers_and_literal_strings_round_trip(tmp_path):
    doc = {"meta": {"revision": 7}, "examples": [0, -3, 1.5, 1e100, "NaN", "Infinity", None, True]}
    path = tmp_path / "model.json"
    m.dump_model(doc, path)
    assert m.loads_strict(path.read_bytes()) == json.loads(path.read_bytes()) == doc


def test_duplicate_key_diagnostic_is_preserved():
    with pytest.raises(m.DuplicateJsonKey, match="revision.*meta"):
        m.loads_strict('{"meta":{"revision":1,"revision":2}}')


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
@pytest.mark.parametrize("as_bytes", [False, True])
def test_nonfinite_error_points_to_actual_token_not_a_quoted_lookalike(literal, as_bytes):
    quoted = json.dumps('строка: "' + literal + '" \\ ' + literal, ensure_ascii=False)
    raw = ('{\n  "before": ' + quoted + ',\n  "nested": {\n'
           '    "n": ' + literal + '\n  },\n  "after": ' + quoted + '\n}')
    expected = raw.index('"n": ') + len('"n": ')
    with pytest.raises(json.JSONDecodeError) as exc:
        m.loads_strict(raw.encode("utf-8") if as_bytes else raw)
    assert exc.value.pos == expected
    assert exc.value.lineno == 4 and exc.value.colno == 10
    assert literal in str(exc.value)


@pytest.mark.parametrize("where", ["model", "manifest"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_actual_package_reader_refuses_nonfinite_rehashed_json(tmp_path, monkeypatch, where, value):
    from test_cdpm_landing_v2_reader import package, receive, reader
    doc, _row, declared = package(tmp_path, monkeypatch)
    if where == "model":
        doc["meta"]["revision"] = value
    else:
        declared["nonfinite_extension"] = value
    with pytest.raises(reader.TaskPackageRefused, match="JSON"):
        receive(tmp_path, doc, declared)
