"""K05: a malformed edition number must not become an exact identity."""
import copy

import pytest

from cdpm import answers, provenance, render
from test_cdpm_a03m_provenance import FAMILY, PARAM, _answered


def _refused(doc, route):
    cell = next(r for r in doc["rules"] if r["id"] == "R-R")[
        "template_fit"]["params"][PARAM]
    if route == "resolver":
        with pytest.raises(provenance.ProvenanceRefusal) as exc:
            answers.resolve_source(doc, cell["source"])
        assert exc.value.code == "answer-provenance"
        assert "издани" in str(exc.value)
    elif route == "cell":
        assert provenance.cell_problems(doc, "R-R", FAMILY, PARAM, cell)
    elif route == "package":
        assert provenance.package_problems(doc)
    else:
        text = render.render_svod(doc)
        line = next(s for s in text.splitlines() if s.startswith("| R-R |"))
        assert "источник ответа не разрешён" in line
        assert "cdpm-answer-" not in line


@pytest.mark.parametrize("route", ["resolver", "cell", "package", "display"])
@pytest.mark.parametrize("revision", [None, True, False, "1", 1.0, [], {}])
def test_invalid_edition_number_is_not_accepted_as_exact(revision, route):
    doc, cell = _answered()
    cell["source"]["question"]["revision"] = revision
    before = copy.deepcopy(doc)
    _refused(doc, route)
    assert doc == before


@pytest.mark.parametrize("route", ["resolver", "cell", "package", "display"])
def test_missing_edition_number_is_not_inferred(route):
    doc, cell = _answered()
    cell["source"]["question"].pop("revision")
    before = copy.deepcopy(doc)
    _refused(doc, route)
    assert doc == before


def test_valid_integer_edition_keeps_exact_answer_and_human_route():
    doc, cell = _answered()
    before = copy.deepcopy(doc)
    q, raw = answers.resolve_source(doc, cell["source"])
    assert raw == q["answer"]["raw"]
    assert cell["source"]["question"]["revision"] == 1
    assert provenance.cell_problems(doc, "R-R", FAMILY, PARAM, cell) == []
    assert provenance.package_problems(doc) == []
    line = next(s for s in render.render_svod(doc).splitlines()
                if s.startswith("| R-R |"))
    assert "cdpm-answer-" in line
    assert doc == before
