"""Availability checker.

The checker loads a product page in a real Chromium browser, makes a
best-effort attempt to set the delivery ZIP, and classifies availability.

Classification strategy, in order:

1. Detect bot challenges or access blocks and report ``blocked_or_unknown``.
2. Read schema.org ``Product`` structured data (JSON-LD) and match the
   requested variant by SKU (item number) or by its attribute values, then use
   that offer's ``availability``. This is the reliable signal.
3. Fall back to a text heuristic only when no structured data is present.

Design notes and constraints:

- It does not solve CAPTCHAs, use proxies, or attempt to evade bot detection.
  Costco serves Akamai bot mitigation; headless browsers are typically blocked,
  so a real (headed) browser is usually required. When a page is blocked the
  result is ``blocked_or_unknown``.
- Costco Same-Day and Instacart sources are intentionally out of scope.
- Classification is best-effort and not guaranteed to be accurate.

:func:`classify_html` is pure and unit-tested. The browser driver wraps it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


class Availability(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    BLOCKED_OR_UNKNOWN = "blocked_or_unknown"

    @property
    def label(self) -> str:
        return {
            Availability.IN_STOCK: "In stock",
            Availability.OUT_OF_STOCK: "Out of stock",
            Availability.BLOCKED_OR_UNKNOWN: "Blocked or unknown",
        }[self]


@dataclass(frozen=True)
class CheckOutcome:
    availability: Availability
    detail: str

    @property
    def status(self) -> str:
        return self.availability.value


# Substrings that indicate a bot challenge or access block. These do not appear
# on a normal Costco product page, so matching the whole document is safe.
BLOCK_SIGNALS: tuple[str, ...] = (
    "access denied",
    "you don't have permission to access",
    "pardon our interruption",
    "are you a human",
    "verify you are a human",
    "unusual traffic",
    "px-captcha",
    "captcha",
)

# Heuristic text signals, used only when structured data is unavailable.
OUT_OF_STOCK_SIGNALS: tuple[str, ...] = (
    "out of stock",
    "out-of-stock",
    "sold out",
    "currently unavailable",
    "no longer available",
    "not available for delivery",
)

IN_STOCK_SIGNALS: tuple[str, ...] = (
    "add to cart",
    "add-to-cart",
)

# schema.org ItemAvailability tokens mapped to our states.
SCHEMA_IN_STOCK: frozenset[str] = frozenset(
    {"instock", "limitedavailability", "onlineonly"}
)
SCHEMA_OUT_OF_STOCK: frozenset[str] = frozenset(
    {
        "outofstock",
        "soldout",
        "discontinued",
        "instoreonly",
        "backorder",
        "preorder",
        "presale",
        "reserved",
    }
)

# Sources explicitly out of scope per the project rules.
DISALLOWED_HOST_FRAGMENTS: tuple[str, ...] = (
    "instacart.com",
    "sameday.costco.com",
)

_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _first_match(haystack: str, needles: tuple[str, ...]) -> str | None:
    for needle in needles:
        if needle in haystack:
            return needle
    return None


def _parse_jsonld_objects(raws):
    """Yield parsed JSON-LD objects from raw script strings, flattening @graph."""
    for raw in raws:
        if not raw:
            continue
        try:
            data = json.loads(raw.strip())
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                yield from (g for g in item["@graph"] if isinstance(g, dict))
            elif isinstance(item, dict):
                yield item


def _iter_jsonld(html: str):
    """Yield JSON-LD objects embedded in the document HTML."""
    yield from _parse_jsonld_objects(_LD_JSON_RE.findall(html))


def _is_product(obj: dict) -> bool:
    type_value = obj.get("@type")
    types = type_value if isinstance(type_value, list) else [type_value]
    return "Product" in types


def _offer_availability(offers: object) -> str | None:
    if isinstance(offers, list):
        for offer in offers:
            value = _offer_availability(offer)
            if value:
                return value
        return None
    if isinstance(offers, dict):
        availability = offers.get("availability")
        return availability if isinstance(availability, str) else None
    return None


def _candidate_haystack(candidate: dict) -> str:
    parts = [str(candidate.get("name") or "")]
    for prop in candidate.get("additionalProperty") or []:
        if isinstance(prop, dict):
            parts.append(str(prop.get("value", "")))
    return " ".join(parts).lower()


def _find_availability(
    products: list[dict],
    *,
    item_number: str | None,
    variant: dict[str, str] | None,
) -> str | None:
    """Find the availability string for the requested variant."""
    candidates: list[dict] = []
    for product in products:
        variants = product.get("hasVariant")
        if isinstance(variants, list) and variants:
            candidates.extend(v for v in variants if isinstance(v, dict))
        else:
            candidates.append(product)

    if item_number:
        for candidate in candidates:
            if str(candidate.get("sku") or "") == str(item_number):
                availability = _offer_availability(candidate.get("offers"))
                if availability:
                    return availability

    if variant:
        wanted = [str(value).lower() for value in variant.values() if str(value).strip()]
        if wanted:
            for candidate in candidates:
                haystack = _candidate_haystack(candidate)
                if all(value in haystack for value in wanted):
                    availability = _offer_availability(candidate.get("offers"))
                    if availability:
                        return availability

    if not item_number and not variant and len(candidates) == 1:
        return _offer_availability(candidates[0].get("offers"))

    return None


def _map_schema_availability(value: str) -> Availability | None:
    token = value.rsplit("/", 1)[-1].strip().lower()
    if token in SCHEMA_IN_STOCK:
        return Availability.IN_STOCK
    if token in SCHEMA_OUT_OF_STOCK:
        return Availability.OUT_OF_STOCK
    return None


def _structured_outcome(
    html: str,
    *,
    item_number: str | None,
    variant: dict[str, str] | None,
    jsonld_blocks: list[str] | None = None,
) -> CheckOutcome | None:
    """Classify from JSON-LD structured data, or None when not determinable.

    ``jsonld_blocks`` are raw script contents read from the live DOM, which can
    be richer than the JSON-LD embedded in the serialized HTML snapshot.
    """
    objects = list(_iter_jsonld(html))
    if jsonld_blocks:
        objects.extend(_parse_jsonld_objects(jsonld_blocks))
    products = [obj for obj in objects if _is_product(obj)]
    if not products:
        return None

    availability = _find_availability(products, item_number=item_number, variant=variant)
    if availability is not None:
        token = availability.rsplit("/", 1)[-1]
        mapped = _map_schema_availability(availability)
        if mapped is not None:
            return CheckOutcome(mapped, f"schema.org availability: {token}")
        return CheckOutcome(
            Availability.BLOCKED_OR_UNKNOWN, f"Unrecognized availability: {token}"
        )

    if item_number or variant:
        return CheckOutcome(
            Availability.BLOCKED_OR_UNKNOWN,
            "Requested variant not found in structured data",
        )
    return None


def _classify_by_text(html_lower: str) -> CheckOutcome:
    out_of_stock = _first_match(html_lower, OUT_OF_STOCK_SIGNALS)
    if out_of_stock:
        return CheckOutcome(
            Availability.OUT_OF_STOCK, f"Out-of-stock text (heuristic): {out_of_stock!r}"
        )
    in_stock = _first_match(html_lower, IN_STOCK_SIGNALS)
    if in_stock:
        return CheckOutcome(
            Availability.IN_STOCK, f"Add-to-cart text (heuristic): {in_stock!r}"
        )
    return CheckOutcome(Availability.BLOCKED_OR_UNKNOWN, "No availability signal found")


def classify_html(
    html: str,
    *,
    page_title: str = "",
    status_code: int = 200,
    item_number: str | None = None,
    variant: dict[str, str] | None = None,
    jsonld_blocks: list[str] | None = None,
) -> CheckOutcome:
    """Classify rendered page content into an availability state.

    Blocks are detected first, then schema.org structured data, then a text
    heuristic. Anything indeterminate is reported as unknown rather than
    assumed available.
    """
    if status_code in (403, 429):
        return CheckOutcome(
            Availability.BLOCKED_OR_UNKNOWN, f"Received HTTP {status_code}"
        )

    haystack = f"{page_title}\n{html}".lower()
    blocked = _first_match(haystack, BLOCK_SIGNALS)
    if blocked:
        return CheckOutcome(
            Availability.BLOCKED_OR_UNKNOWN, f"Block signal detected: {blocked!r}"
        )

    structured = _structured_outcome(
        html, item_number=item_number, variant=variant, jsonld_blocks=jsonld_blocks
    )
    if structured is not None:
        return structured

    return _classify_by_text(haystack)


def _is_disallowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(fragment in host for fragment in DISALLOWED_HOST_FRAGMENTS)


async def _set_delivery_zip(page, zip_code: str) -> None:
    """Best-effort attempt to set the delivery ZIP.

    Selectors are intentionally broad and may need updating as the site
    changes. Any failure is logged and ignored so the check can continue.
    """
    try:
        trigger = page.locator(
            "[data-testid*='zip' i], [aria-label*='delivery zip' i], #shipping-zipcode"
        ).first
        if await trigger.count() == 0:
            return
        await trigger.click(timeout=4000)
        zip_input = page.locator(
            "input[name*='zip' i], input[id*='zip' i], input[aria-label*='zip' i]"
        ).first
        if await zip_input.count() == 0:
            return
        await zip_input.fill(zip_code, timeout=4000)
        await zip_input.press("Enter")
        await page.wait_for_timeout(1500)
    except Exception:
        logger.debug("Could not set delivery ZIP; continuing", exc_info=True)


async def _await_hydrated_jsonld(page, timeout_ms: int) -> None:
    """Wait for client-side hydration to finish populating the product JSON-LD.

    Costco renders a small placeholder ``Product`` block first, then replaces it
    with the full per-variant data. Reading too early yields a stale state, so
    we wait for a richer block (one with ``hasVariant`` or a large Product) to
    appear before classifying.
    """
    script = """() => {
        const els = document.querySelectorAll('script[type="application/ld+json"]');
        for (const e of els) {
            const t = e.textContent || '';
            if (t.includes('"hasVariant"')) return true;
            if (t.includes('"@type":"Product"') && t.length > 1500) return true;
        }
        return false;
    }"""
    try:
        await page.wait_for_function(script, timeout=timeout_ms)
    except Exception:
        logger.debug("Hydrated JSON-LD not detected before timeout; using current DOM")


async def _check_async(
    url: str,
    *,
    item_number: str | None,
    variant: dict[str, str],
    zip_code: str,
    settings: Settings,
) -> CheckOutcome:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    timeout_ms = settings.request_timeout_seconds * 1000
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=settings.headless)
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 900}
                )
                page = await context.new_page()
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout_ms
                )
                status_code = response.status if response is not None else 0

                if zip_code:
                    await _set_delivery_zip(page, zip_code)

                await _await_hydrated_jsonld(page, min(timeout_ms, 12000))
                await page.wait_for_timeout(800)
                html = await page.content()
                title = await page.title()
                try:
                    jsonld_blocks = await page.eval_on_selector_all(
                        'script[type="application/ld+json"]',
                        "els => els.map(e => e.textContent)",
                    )
                except Exception:
                    jsonld_blocks = None
                return classify_html(
                    html,
                    page_title=title,
                    status_code=status_code,
                    item_number=item_number,
                    variant=variant,
                    jsonld_blocks=jsonld_blocks,
                )
            finally:
                await browser.close()
    except PlaywrightTimeoutError:
        return CheckOutcome(
            Availability.BLOCKED_OR_UNKNOWN, "Timed out loading the page"
        )
    except Exception as exc:
        logger.exception("Unexpected checker error for %s", url)
        return CheckOutcome(
            Availability.BLOCKED_OR_UNKNOWN, f"Checker error: {exc.__class__.__name__}"
        )


def run_check(
    url: str,
    *,
    item_number: str | None = None,
    variant: dict[str, str] | None = None,
    zip_code: str | None = None,
    settings: Settings | None = None,
    html_override: str | None = None,
) -> CheckOutcome:
    """Check availability for a product page.

    This call blocks. When invoked from inside a running event loop (such as a
    FastAPI route) run it in a worker thread so the browser gets its own loop.
    ``html_override`` bypasses the browser and classifies provided HTML, which
    is useful for testing.
    """
    if html_override is not None:
        return classify_html(html_override, item_number=item_number, variant=variant)

    if _is_disallowed(url):
        return CheckOutcome(
            Availability.BLOCKED_OR_UNKNOWN,
            "Source is out of scope (Same-Day/Instacart)",
        )

    settings = settings or get_settings()
    return asyncio.run(
        _check_async(
            url,
            item_number=item_number,
            variant=variant or {},
            zip_code=zip_code or settings.delivery_zip,
            settings=settings,
        )
    )


def _main() -> None:
    parser = argparse.ArgumentParser(description="Check availability for a product URL.")
    parser.add_argument("url", help="Product page URL")
    parser.add_argument("--item", dest="item_number", default=None, help="Item/SKU number")
    parser.add_argument("--zip", dest="zip_code", default=None, help="Delivery ZIP code")
    parser.add_argument(
        "--show", action="store_true", help="Run with a visible browser window"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if args.show:
        settings = settings.model_copy(update={"headless": False})

    outcome = run_check(
        args.url, item_number=args.item_number, zip_code=args.zip_code, settings=settings
    )
    print(f"{outcome.availability.value}: {outcome.detail}")


if __name__ == "__main__":
    _main()
