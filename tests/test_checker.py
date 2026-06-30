from app import checker
from app.checker import Availability, CheckOutcome, classify_html, run_check
from app.config import Settings


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


# A document shaped like a real Costco product page: a Product with per-variant
# offers, plus misleading "Add to Cart" and "Out of Stock" text in the body.
COSTCO_LIKE_JSONLD = """
<html><head><title>Novaform Mattress | Costco</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[]}
</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Novaform Mattress","sku":"4000326605",
 "hasVariant":[
  {"@type":"Product","sku":"1847132","name":"Novaform Mattress, Full, Firm",
   "additionalProperty":[{"@type":"PropertyValue","name":"Bed Size","value":"Full"},
                         {"@type":"PropertyValue","name":"Firmness","value":"Firm"}],
   "offers":{"@type":"Offer","availability":"https://schema.org/InStock","price":"899.99"}},
  {"@type":"Product","sku":"1847133","name":"Novaform Mattress, Queen, Firm",
   "additionalProperty":[{"@type":"PropertyValue","name":"Bed Size","value":"Queen"},
                         {"@type":"PropertyValue","name":"Firmness","value":"Firm"}],
   "offers":{"@type":"Offer","availability":"https://schema.org/OutOfStock"}}
 ]}
</script></head><body>Add to Cart Out of Stock</body></html>
"""


def test_jsonld_in_stock_by_sku():
    outcome = classify_html(COSTCO_LIKE_JSONLD, item_number="1847132")
    assert outcome.availability is Availability.IN_STOCK
    assert "InStock" in outcome.detail


def test_jsonld_out_of_stock_by_sku():
    outcome = classify_html(COSTCO_LIKE_JSONLD, item_number="1847133")
    assert outcome.availability is Availability.OUT_OF_STOCK


def test_jsonld_match_by_variant_attributes():
    outcome = classify_html(
        COSTCO_LIKE_JSONLD, variant={"Bed Size": "Full", "Firmness": "Firm"}
    )
    assert outcome.availability is Availability.IN_STOCK


def test_jsonld_variant_attributes_out_of_stock():
    outcome = classify_html(
        COSTCO_LIKE_JSONLD, variant={"Bed Size": "Queen", "Firmness": "Firm"}
    )
    assert outcome.availability is Availability.OUT_OF_STOCK


def test_jsonld_unknown_sku_reports_unknown():
    outcome = classify_html(COSTCO_LIKE_JSONLD, item_number="9999")
    assert outcome.availability is Availability.BLOCKED_OR_UNKNOWN
    assert "not found" in outcome.detail.lower()


def test_structured_data_overrides_misleading_text():
    # The body contains both "Add to Cart" and "Out of Stock"; the matched
    # variant's structured availability must win.
    outcome = classify_html(COSTCO_LIKE_JSONLD, item_number="1847132")
    assert outcome.availability is Availability.IN_STOCK


def test_block_takes_precedence_over_structured_data():
    outcome = classify_html("Access Denied " + COSTCO_LIKE_JSONLD, item_number="1847132")
    assert outcome.availability is Availability.BLOCKED_OR_UNKNOWN


def test_jsonld_blocks_from_dom_are_used():
    block = '{"@type":"Product","sku":"77","offers":{"availability":"https://schema.org/InStock"}}'
    outcome = classify_html(
        "<html><body></body></html>", item_number="77", jsonld_blocks=[block]
    )
    assert outcome.availability is Availability.IN_STOCK


def test_jsonld_graph_wrapper():
    html = (
        '<script type="application/ld+json">{"@graph":[{"@type":"Product","sku":"88",'
        '"offers":{"availability":"https://schema.org/OutOfStock"}}]}</script>'
    )
    outcome = classify_html(html, item_number="88")
    assert outcome.availability is Availability.OUT_OF_STOCK


def test_run_check_retries_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    async def fake(url, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 2:
            return CheckOutcome(Availability.BLOCKED_OR_UNKNOWN, "blocked")
        return CheckOutcome(Availability.IN_STOCK, "ok")

    monkeypatch.setattr(checker, "_check_async", fake)
    settings = Settings(_env_file=None, checker_max_attempts=3, checker_retry_delay_seconds=0)
    outcome = run_check("https://www.costco.com/p/x/1", settings=settings)
    assert outcome.availability is Availability.IN_STOCK
    assert attempts["n"] == 2


def test_run_check_gives_up_after_max_attempts(monkeypatch):
    attempts = {"n": 0}

    async def always_blocked(url, **kwargs):
        attempts["n"] += 1
        return CheckOutcome(Availability.BLOCKED_OR_UNKNOWN, "blocked")

    monkeypatch.setattr(checker, "_check_async", always_blocked)
    settings = Settings(_env_file=None, checker_max_attempts=2, checker_retry_delay_seconds=0)
    outcome = run_check("https://www.costco.com/p/x/1", settings=settings)
    assert outcome.availability is Availability.BLOCKED_OR_UNKNOWN
    assert attempts["n"] == 2
