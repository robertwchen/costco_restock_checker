"""End-to-end browser test.

Marked ``browser`` and excluded from CI. It launches a real Chromium instance
and drives the full checker against a local HTML file. Run locally with::

    pytest -m browser
"""

import pytest

from app.checker import Availability, run_check

pytestmark = pytest.mark.browser


def test_checker_classifies_local_file(tmp_path):
    page = tmp_path / "product.html"
    page.write_text("<html><body><h1>Item</h1><button>Add to Cart</button></body></html>")

    outcome = run_check(page.as_uri())
    assert outcome.availability is Availability.IN_STOCK
