"""Seam test for the customer-contour title label (CREW_CONTOUR_LABEL).

The server stamps the label into the served index.html (initial <title>
suffix "/<label>" plus window.COSMOS_CONTOUR_LABEL for the client), and
i18n.js re-applies the suffix on every language switch. The seam is the
window variable NAME on both sides: a rename on one side fails silently
(the suffix just disappears after a language switch), so the name itself
is pinned here.

The app import is deferred into the tests so that collection always
succeeds; a broken backend is a red result, not a skip.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOW_VAR = "window.COSMOS_CONTOUR_LABEL"


@pytest.fixture()
def webapp_app():
    from webapp import app as webapp_app
    webapp_app._INDEX_CACHE.clear()
    yield webapp_app
    webapp_app._INDEX_CACHE.clear()


def test_label_stamped_into_index(monkeypatch, webapp_app):
    monkeypatch.setenv("CREW_CONTOUR_LABEL", "DEMO")
    html = webapp_app._versioned_index()
    assert "/DEMO</title>" in html, "initial <title> must carry the suffix"
    assert WINDOW_VAR + ' = "DEMO";' in html, "label must reach the client"


def test_no_label_leaves_index_untouched(monkeypatch, webapp_app):
    monkeypatch.delenv("CREW_CONTOUR_LABEL", raising=False)
    html = webapp_app._versioned_index()
    assert "COSMOS_CONTOUR_LABEL" not in html
    assert "</title>" in html


def test_blank_label_is_ignored(monkeypatch, webapp_app):
    monkeypatch.setenv("CREW_CONTOUR_LABEL", "   ")
    html = webapp_app._versioned_index()
    assert "COSMOS_CONTOUR_LABEL" not in html


def test_client_consumes_the_same_window_variable():
    js = (REPO_ROOT / "webapp" / "static" / "i18n.js").read_text(
        encoding="utf-8")
    occurrences = js.count(WINDOW_VAR)
    assert occurrences >= 2, (
        "i18n.js must both test and read %s (found %d occurrences)"
        % (WINDOW_VAR, occurrences))
