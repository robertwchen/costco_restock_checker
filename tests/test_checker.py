from app.checker import Availability, classify_html, run_check


def test_add_to_cart_is_in_stock():
    assert classify_html("<button>Add to Cart</button>").availability is Availability.IN_STOCK


def test_out_of_stock_text():
    assert classify_html("<div>Out of Stock</div>").availability is Availability.OUT_OF_STOCK


def test_sold_out_text():
    assert classify_html("<span>Sold Out</span>").availability is Availability.OUT_OF_STOCK


def test_access_denied_is_blocked():
    outcome = classify_html("Access Denied", page_title="Access Denied")
    assert outcome.availability is Availability.BLOCKED_OR_UNKNOWN


def test_captcha_is_blocked():
    assert classify_html('<div class="px-captcha"></div>').availability is Availability.BLOCKED_OR_UNKNOWN


def test_http_403_is_blocked():
    outcome = classify_html("<button>Add to Cart</button>", status_code=403)
    assert outcome.availability is Availability.BLOCKED_OR_UNKNOWN


def test_no_signal_is_unknown():
    assert classify_html("<html><body></body></html>").availability is Availability.BLOCKED_OR_UNKNOWN


def test_block_takes_precedence_over_add_to_cart():
    outcome = classify_html("<div>Access Denied</div><button>Add to Cart</button>")
    assert outcome.availability is Availability.BLOCKED_OR_UNKNOWN


def test_out_of_stock_takes_precedence_over_add_to_cart():
    outcome = classify_html("<button>Add to Cart</button><div>Out of Stock</div>")
    assert outcome.availability is Availability.OUT_OF_STOCK


def test_html_override_bypasses_browser():
    outcome = run_check("https://example.com", html_override="<button>Add to Cart</button>")
    assert outcome.availability is Availability.IN_STOCK


def test_instacart_source_is_out_of_scope():
    outcome = run_check("https://www.instacart.com/store/costco/products/1")
    assert outcome.availability is Availability.BLOCKED_OR_UNKNOWN
    assert "scope" in outcome.detail.lower()


def test_availability_labels():
    assert Availability.IN_STOCK.label == "In stock"
    assert Availability.OUT_OF_STOCK.label == "Out of stock"
    assert Availability.BLOCKED_OR_UNKNOWN.label == "Blocked or unknown"
