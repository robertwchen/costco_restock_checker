"""Exact plush identity and fresh, location-bound inventory checks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from .checker import INVENTORY_API, Availability, CheckOutcome, _inventory_headers
from .config import Settings

PRODUCT_ID = "4201016777"
URL = f"https://www.costco.com/p/-/jumbo-baby-animal-plush/{PRODUCT_ID}"
ANIMALS = {"Capybara": "2005333", "Dog": "2005332", "Red Panda": "2005334", "Raccoon": "2005335"}


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def variant_map(chunks: list[str]) -> dict[str, str]:
    """Read JSON records from Next's flight stream, without executing scripts."""
    found: dict[str, set[str]] = {}
    for line in "".join(chunks).splitlines():
        _, _, payload = line.partition(":")
        try:
            value = json.loads(payload)
        except (ValueError, TypeError):
            continue
        for obj in walk(value):
            if obj.get("parentId") == PRODUCT_ID and obj.get("key") == "Design":
                name, sku = obj.get("value"), obj.get("itemNumber")
                if name in ANIMALS and isinstance(sku, str):
                    found.setdefault(name, set()).add(sku)
    return {name: next(iter(skus)) for name, skus in found.items() if len(skus) == 1}


def flight_chunks(scripts: list[str]) -> list[str]:
    chunks = []
    for script in scripts:
        for part in script.split("self.__next_f.push(")[1:]:
            try:
                entry, _ = json.JSONDecoder().raw_decode(part)
                if isinstance(entry, list) and len(entry) == 2 and entry[0] == 1 and isinstance(entry[1], str):
                    chunks.append(entry[1])
            except ValueError:
                continue
    return chunks


def inventory_outcome(data, *, animal, mapped_sku, requested_url, response_url,
                      zip_code, date_header, age_header="0", now=None) -> CheckOutcome:
    def unknown(reason):
        return CheckOutcome(Availability.BLOCKED_OR_UNKNOWN, reason)
    sku = ANIMALS.get(animal)
    if not sku or mapped_sku != sku:
        return unknown("Variant identity missing or conflicting")
    expected = f"{INVENTORY_API}/{sku}?" + urlencode({
        "destinationPostalCode": zip_code, "destinationCountryCode": "US"})
    if not zip_code or requested_url != expected or response_url != expected:
        return unknown("Delivery request context missing or mismatched")
    try:
        age = ((now or datetime.now(UTC)) - parsedate_to_datetime(date_header)).total_seconds()
        if not -30 <= age <= 120 or int(age_header) > 120 or int(age_header) < 0:
            return unknown("Stale inventory response")
    except (ValueError, TypeError, OverflowError):
        return unknown("Inventory freshness missing")
    if isinstance(data, list):
        if len(data) != 1:
            return unknown("Ambiguous inventory records")
        data = data[0]
    if not isinstance(data, dict) or data.get("itemNumber") != sku:
        return unknown("Inventory SKU missing or mismatched")
    for key, expected_value in (("destinationPostalCode", zip_code), ("destinationCountryCode", "US")):
        if key in data and data[key] != expected_value:
            return unknown("Inventory location mismatch")
    flag, state = data.get("availableForSale"), data.get("availability")
    if flag is True and state == "INSTOCK":
        status = Availability.IN_STOCK
    elif flag is False and state == "NOSTOCK":
        status = Availability.OUT_OF_STOCK
    else:
        return unknown("Missing, malformed or conflicting inventory signals")
    return CheckOutcome(status, f"{animal}; SKU {sku}; US standard delivery; configured ZIP matched; {state}")


async def check_animals_async(settings: Settings, animals: list[str]) -> dict[str, CheckOutcome]:
    from playwright.async_api import async_playwright

    results = {a: CheckOutcome(Availability.BLOCKED_OR_UNKNOWN, "Page/session unavailable") for a in animals}
    if not settings.delivery_zip:
        return {a: CheckOutcome(Availability.BLOCKED_OR_UNKNOWN, "Delivery ZIP not configured") for a in animals}
    captured = {}
    def capture(request):
        if request.url.startswith(INVENTORY_API + "/"):
            captured["client"] = request.headers.get("client-identifier")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=settings.headless)
            try:
                page = await browser.new_page()
                page.on("request", capture)
                response = await page.goto(URL, wait_until="domcontentloaded", timeout=settings.request_timeout_seconds * 1000)
                if not response or response.status != 200 or "access denied" in (await page.title()).lower():
                    return results
                mapping = {}
                for _ in range(30):
                    chunks = flight_chunks(await page.locator('script').all_text_contents())
                    mapping = variant_map(chunks)
                    if captured.get("client") and all(a in mapping for a in animals):
                        break
                    await page.wait_for_timeout(500)
                if not captured.get("client"):
                    return results
                prices = {}
                for block in await page.locator('script[type="application/ld+json"]').all_text_contents():
                    try:
                        for obj in walk(json.loads(block)):
                            offer = obj.get("offers")
                            if isinstance(offer, dict) and offer.get("priceCurrency") == "USD":
                                value = offer.get("price")
                                if isinstance(value, (str, int, float)) and str(value).replace('.', '', 1).isdigit():
                                    prices[obj.get("sku")] = f"USD {value}"
                    except ValueError:
                        pass
                for animal in animals:
                    sku = ANIMALS[animal]
                    if mapping.get(animal) != sku:
                        results[animal] = CheckOutcome(Availability.BLOCKED_OR_UNKNOWN, "Variant mapping not verified on current product page")
                        continue
                    url = f"{INVENTORY_API}/{sku}?" + urlencode({"destinationPostalCode": settings.delivery_zip, "destinationCountryCode": "US"})
                    try:
                        r = await page.request.get(url, headers={**_inventory_headers(captured["client"]), "Cache-Control": "no-cache"}, timeout=15000)
                        if r.status != 200:
                            results[animal] = CheckOutcome(Availability.BLOCKED_OR_UNKNOWN, f"Inventory HTTP {r.status}")
                            continue
                        results[animal] = inventory_outcome(await r.json(), animal=animal, mapped_sku=mapping[animal], requested_url=url, response_url=r.url, zip_code=settings.delivery_zip, date_header=r.headers.get("date"), age_header=r.headers.get("age", "0"))
                        results[animal] = replace(results[animal], price=prices.get(sku))
                    except Exception:
                        results[animal] = CheckOutcome(Availability.BLOCKED_OR_UNKNOWN, "Inventory request failed or timed out")
            finally:
                await browser.close()
    except Exception as exc:
        results = {a: CheckOutcome(Availability.BLOCKED_OR_UNKNOWN, f"Browser check failed: {type(exc).__name__}") for a in animals}
    return results


def check_animals(settings: Settings, animals=None):
    animals = animals or ["Capybara"]
    results = {}
    pending = list(animals)
    for attempt in range(min(3, max(1, settings.checker_max_attempts))):
        results.update(asyncio.run(check_animals_async(settings, pending)))
        pending = [a for a in pending if results[a].availability == Availability.BLOCKED_OR_UNKNOWN]
        if not pending:
            break
        if attempt + 1 < min(3, settings.checker_max_attempts):
            import time
            time.sleep(min(30, settings.checker_retry_delay_seconds * (attempt + 1)))
    return results
