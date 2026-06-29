"""Availability checker.

The checker loads a product page in a real Chromium browser, makes a
best-effort attempt to set the delivery ZIP and select the requested variant,
and then classifies the rendered HTML into one of three states.

Design notes and constraints:

- It does not solve CAPTCHAs, use proxies, or attempt to evade bot detection.
  When a challenge or block is detected the result is ``blocked_or_unknown``.
- Costco Same-Day and Instacart sources are intentionally out of scope.
- Classification is heuristic and best-effort. Page structure changes over
  time, so results are not guaranteed to be accurate.

:func:`classify_html` is pure and unit-tested. The browser driver wraps it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
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


# Substrings that indicate a bot challenge or access block. Kept specific to
# avoid matching ordinary CDN script references on a normal page.
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

# Sources explicitly out of scope per the project rules.
DISALLOWED_HOST_FRAGMENTS: tuple[str, ...] = (
    "instacart.com",
    "sameday.costco.com",
)


def _first_match(haystack: str, needles: tuple[str, ...]) -> str | None:
    for needle in needles:
        if needle in haystack:
            return needle
    return None


def classify_html(
    html: str,
    *,
    page_title: str = "",
    status_code: int = 200,
) -> CheckOutcome:
    """Classify rendered page content into an availability state.

    Order matters: blocks are detected first, then explicit out-of-stock
    indicators, then an add-to-cart control. Anything else is treated as
    unknown rather than assumed available.
    """
    haystack = f"{page_title}\n{html}".lower()

    if status_code in (403, 429):
        return CheckOutcome(
            Availability.BLOCKED_OR_UNKNOWN, f"Received HTTP {status_code}"
        )

    blocked = _first_match(haystack, BLOCK_SIGNALS)
    if blocked:
        return CheckOutcome(
            Availability.BLOCKED_OR_UNKNOWN, f"Block signal detected: {blocked!r}"
        )

    out_of_stock = _first_match(haystack, OUT_OF_STOCK_SIGNALS)
    if out_of_stock:
        return CheckOutcome(
            Availability.OUT_OF_STOCK, f"Out-of-stock signal: {out_of_stock!r}"
        )

    in_stock = _first_match(haystack, IN_STOCK_SIGNALS)
    if in_stock:
        return CheckOutcome(
            Availability.IN_STOCK, f"Add-to-cart signal: {in_stock!r}"
        )

    return CheckOutcome(
        Availability.BLOCKED_OR_UNKNOWN, "No availability signal found"
    )


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
        await page.wait_for_timeout(1000)
    except Exception:
        logger.debug("Could not set delivery ZIP; continuing", exc_info=True)


async def _select_variant(page, variant: dict[str, str]) -> None:
    """Best-effort attempt to select the requested variant options."""
    for name, value in variant.items():
        try:
            option = page.get_by_role("radio", name=value).first
            if await option.count() == 0:
                option = page.get_by_text(value, exact=True).first
            if await option.count() > 0:
                await option.click(timeout=4000)
                await page.wait_for_timeout(500)
        except Exception:
            logger.debug(
                "Could not select variant %s=%s; continuing", name, value, exc_info=True
            )


async def _check_async(
    url: str,
    *,
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
                context = await browser.new_context()
                page = await context.new_page()
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout_ms
                )
                status_code = response.status if response is not None else 0

                if zip_code:
                    await _set_delivery_zip(page, zip_code)
                if variant:
                    await _select_variant(page, variant)

                await page.wait_for_timeout(1500)
                html = await page.content()
                title = await page.title()
                return classify_html(html, page_title=title, status_code=status_code)
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
        return classify_html(html_override)

    if _is_disallowed(url):
        return CheckOutcome(
            Availability.BLOCKED_OR_UNKNOWN,
            "Source is out of scope (Same-Day/Instacart)",
        )

    settings = settings or get_settings()
    return asyncio.run(
        _check_async(
            url,
            variant=variant or {},
            zip_code=zip_code or settings.delivery_zip,
            settings=settings,
        )
    )


def _main() -> None:
    parser = argparse.ArgumentParser(description="Check availability for a product URL.")
    parser.add_argument("url", help="Product page URL")
    parser.add_argument("--zip", dest="zip_code", default=None, help="Delivery ZIP code")
    parser.add_argument(
        "--show", action="store_true", help="Run with a visible browser window"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if args.show:
        settings = settings.model_copy(update={"headless": False})

    outcome = run_check(args.url, zip_code=args.zip_code, settings=settings)
    print(f"{outcome.availability.value}: {outcome.detail}")


if __name__ == "__main__":
    _main()
