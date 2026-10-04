"""K02 measurement inputs: real answer cells or an explicit failed measurement."""
import copy
import json
from pathlib import Path

import pytest

from test_cdpm_k02_price_tool import K, _dump, _run, fp, price


def _book(tmp_path, text, name="ANSWER-BOOK.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_markdown_header_is_not_an_answer_and_answer_column_is_named(tmp_path):
    _book(tmp_path, "# Demo\n\n| Правильный ответ | Тема |\n"
          "|:---|---:|\n| 10 часов | отдых |\n| 600 минут | отдых |\n")
    assert price.book_lines(tmp_path) == ["10 часов", "600 минут"]


def test_markdown_escaped_pipe_and_repeated_table_headers(tmp_path):
    _book(tmp_path, "| Тема | Правильный ответ авиакомпании |\n|---|---|\n"
          "| тип | A\\|B |\n\n| Тема | Ответ |\n|---|---|\n| отдых | 10 часов |\n")
    assert price.book_lines(tmp_path) == ["A|B", "10 часов"]


@pytest.mark.parametrize("text", [
    "| Тема | Непонятная колонка |\n|---|---|\n| отдых | 10 часов |\n",
    "| отдых | 10 часов |\n",
    "| Тема | Ответ |\n|---|---|\n| отдых | 10 часов | лишнее |\n",
    "# Empty book\n",
    "| Тема | Ответ |\n|---|---|\n| отдых |   |\n",
])
def test_bad_markdown_is_named_not_counted_as_a_book(tmp_path, text):
    path = _book(tmp_path, text)
    with pytest.raises(ValueError, match="ANSWER-BOOK.md"):
        price.book_lines(tmp_path)
    assert path.exists()


@pytest.mark.parametrize("text", [
    "{broken", "[]", "{}", '{"exact": []}', '{"prefix": null}',
    '{"exact": {"q": null}}', '{"exact": {"q": 10}}',
    '{"exact": {"q": true}}', '{"exact": {"q": {"answer": "10"}}}',
    '{"exact": {"q": "10", "q": "20"}}',
    '{"regex": {"answer": "10"}}', '{"regex": ["10"]}',
    '{"regex": [{"pattern": "q"}]}',
    '{"exact": {}, "prefix": {}, "regex": []}',
])
def test_bad_machine_book_is_never_silently_skipped_or_stringified(tmp_path, text):
    _book(tmp_path, text, "ANSWER-BOOK-MACHINE.json")
    with pytest.raises(ValueError, match="ANSWER-BOOK-MACHINE.json"):
        price.book_lines(tmp_path)


def test_valid_machine_book_keeps_duplicates_and_verbatim_answers(tmp_path):
    doc = {"form_version": 1, "_comment": "metadata, not an answer",
           "exact": {"q1": " 10 часов ", "q2": "10 часов", "blank": "  "},
           "prefix": {"q": "10 часов"},
           "regex": [{"pattern": "q", "answer": "600 минут", "note": "ignore"}]}
    _book(tmp_path, json.dumps(doc), "ANSWER-BOOK-MACHINE.json")
    assert price.book_lines(tmp_path) == [" 10 часов ", "10 часов", "10 часов", "600 минут"]


@pytest.mark.parametrize("case", ["absent-root", "empty-root", "unsupported", "bad-utf8"])
def test_unreadable_or_missing_corpus_cannot_be_a_zero_price(tmp_path, case):
    root = tmp_path
    if case == "absent-root":
        root = tmp_path / "absent"
    elif case == "unsupported":
        _book(tmp_path, "10 часов", "ANSWER-BOOK.txt")
    elif case == "bad-utf8":
        (tmp_path / "ANSWER-BOOK.md").write_bytes(b"\xff\xfe\x80")
    with pytest.raises(ValueError):
        price.book_lines(root)


def test_io_failure_has_the_source_filename(tmp_path, monkeypatch):
    path = _book(tmp_path, '{"exact":{"q":"10 часов"}}', "ANSWER-BOOK.json")
    original = Path.read_bytes

    def denied(self):
        if self == path:
            raise PermissionError("fixture")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(ValueError, match="ANSWER-BOOK.json"):
        price.book_lines(tmp_path)


def test_snapshot_hash_binds_files_not_only_extracted_answers(tmp_path):
    path = _book(tmp_path, '{"exact":{"q":"10 часов"}}', "ANSWER-BOOK.json")
    before = price.book_snapshot(tmp_path)
    path.write_text('{"exact":{"other-question":"10 часов"}}', encoding="utf-8")
    after = price.book_snapshot(tmp_path)
    assert before["lines"] == after["lines"] == ["10 часов"]
    assert before["manifest"] != after["manifest"]
    assert before["manifest_sha"] != after["manifest_sha"]
    assert before["manifest"][0]["path"] == "ANSWER-BOOK.json"


def test_equal_empty_book_measurements_are_not_a_zero_price(tmp_path):
    a = _dump({K: "2"}, books=())
    assert _run(tmp_path, a, copy.deepcopy(a)) == 2


def test_errors_in_the_explanation_are_measurement_errors():
    assert price._errors({"causes": {K: "!ValueError"}}) == 1


def test_dump_refuses_bad_input_before_publishing_an_artifact(tmp_path, capsys):
    _book(tmp_path, "{broken", "ANSWER-BOOK.json")
    out = tmp_path / "dump.json"
    assert price.main(["dump", "--package-root", str(Path(fp.__file__).parents[1]),
                       "--books-root", str(tmp_path), "--out", str(out)]) == 2
    assert not out.exists()
    assert "ANSWER-BOOK.json" in capsys.readouterr().err


def test_dump_with_parser_error_is_not_success(tmp_path, monkeypatch):
    _book(tmp_path, '{"exact":{"q":"10 часов"}}', "ANSWER-BOOK.json")
    monkeypatch.setattr(price, "corpus", lambda: ["10 часов"])

    def broken(*args):
        raise ValueError("fixture")

    monkeypatch.setattr(fp, "parse_value", broken)
    out = tmp_path / "dump.json"
    assert price.main(["dump", "--package-root", str(Path(fp.__file__).parents[1]),
                       "--books-root", str(tmp_path), "--out", str(out)]) == 3
    body = json.loads(out.read_text(encoding="utf-8"))
    assert price._errors(body) > 0


@pytest.mark.parametrize("field", ["corpus_pairs", "corpus_status", "book_pairs", "book_status", "causes"])
def test_equal_truncated_matrices_do_not_certify_completeness(tmp_path, field):
    a = _dump({K: "2"})
    a[field].clear()
    assert _run(tmp_path, a, copy.deepcopy(a)) == 2


@pytest.mark.parametrize("field", ["corpus_rows", "book_rows", "books_manifest", "books_manifest_sha", "schema"])
def test_equal_dumps_missing_provenance_are_refused(tmp_path, field):
    a = _dump({K: "2"})
    a.pop(field)
    assert _run(tmp_path, a, copy.deepcopy(a)) == 2


def test_changed_book_bytes_with_unchanged_answers_are_not_comparable(tmp_path):
    a = _dump({K: "2"})
    b = copy.deepcopy(a)
    b["books_manifest"][0]["sha256"] = "a" * 64
    b["books_manifest_sha"] = price._json_sha(b["books_manifest"])
    assert _run(tmp_path, a, b) == 2


def test_dump_restores_import_path_and_catalog_environment(tmp_path, monkeypatch):
    import os
    import sys

    _book(tmp_path, '{"exact":{"q":"10 часов"}}', "ANSWER-BOOK.json")
    monkeypatch.setattr(price, "corpus", lambda: ["10 часов"])
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", "this-inherited-value-must-not-select-the-catalog")
    before = sys.path[:]
    out = tmp_path / "dump.json"
    assert price.main(["dump", "--package-root", str(Path(fp.__file__).parents[1]),
                       "--books-root", str(tmp_path), "--out", str(out)]) == 0
    assert sys.path == before
    assert os.environ["CREW_TEMPLATE_CATALOG"] == "this-inherited-value-must-not-select-the-catalog"
    body = json.loads(out.read_text(encoding="utf-8"))
    price._validate_dump(body)
    assert body["books"] == 1 and body["book_lines"] == 1
    assert body["book_pairs"][json.dumps(["10 часов", "duration_minutes"], ensure_ascii=False)] == "600"


def test_existing_measurement_artifact_is_not_overwritten(tmp_path, monkeypatch):
    _book(tmp_path, '{"exact":{"q":"10 часов"}}', "ANSWER-BOOK.json")
    monkeypatch.setattr(price, "corpus", lambda: ["10 часов"])
    out = tmp_path / "dump.json"
    out.write_text("prior evidence", encoding="utf-8")
    assert price.main(["dump", "--package-root", str(Path(fp.__file__).parents[1]),
                       "--books-root", str(tmp_path), "--out", str(out)]) == 2
    assert out.read_text(encoding="utf-8") == "prior evidence"


@pytest.mark.parametrize("path", ["cdpm/model.py", "cdpm/catalog_bridge.py",
                                  "template_catalog/reader.py", "catalog/families.json"])
def test_lattice_cannot_present_a_mixed_module_as_a_historical_build(path):
    from test_cdpm_k02_price_tool import lattice

    with pytest.raises(SystemExit, match="несопоставимая среда"):
        lattice._check_dependency_changes(["cdpm/family_params.py", path], [])
    with pytest.raises(SystemExit, match="несопоставимая среда"):
        lattice._check_dependency_changes([], [path])
    lattice._check_dependency_changes(["cdpm/family_params.py"], [])


def test_measurement_rejects_a_loaded_dependency_from_another_tree(monkeypatch):
    import sys
    import types

    root = Path(fp.__file__).parents[1]
    monkeypatch.setitem(sys.modules, "cdpm.fake_dependency", types.SimpleNamespace(__file__="X:/other/model.py"))
    with pytest.raises(ValueError, match="смешанное окружение"):
        price._measurement_environment(root, root / "catalog")


def test_compound_period_price_axis_covers_all_positions_and_healthy_controls():
    rows = price.corpus()
    for phrase in ("в течение каждого полного месяца", "на протяжении недели", "ежечасно"):
        assert "10 часов " + phrase in rows
        assert phrase + ": 10 часов" in rows
        assert "10 часов. " + phrase + "." in rows
    assert "10 часов" in rows
    assert price._status(fp, "10 часов", "duration_minutes") == "ЗНАЧЕНИЕ"


@pytest.mark.parametrize("field", ["environment", "module_sha256"])
def test_equal_dumps_without_module_or_environment_proof_are_refused(tmp_path, field):
    a = _dump({K: "2"})
    a.pop(field)
    assert _run(tmp_path, a, copy.deepcopy(a)) == 2


@pytest.mark.parametrize("field", ["sources", "catalog_sha", "tool_sha256", "python"])
def test_equal_dumps_with_incomplete_environment_are_refused(tmp_path, field):
    a = _dump({K: "2"})
    a["environment"].pop(field)
    assert _run(tmp_path, a, copy.deepcopy(a)) == 2


def test_module_hash_must_match_the_recorded_source(tmp_path):
    a = _dump({K: "2"})
    a["module_sha256"] = "9" * 64
    assert _run(tmp_path, a, copy.deepcopy(a)) == 2


def test_catalog_hash_must_bind_the_recorded_catalog_files(tmp_path):
    a = _dump({K: "2"})
    a["environment"]["catalog_sha"] = "9" * 64
    assert _run(tmp_path, a, copy.deepcopy(a)) == 2
