"""Orchestration that ties checking, storage, and alerting together."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .alerts import dispatch_restock_alerts
from .checker import Availability, run_check
from .config import Settings, get_settings
from .database import session_scope
from .models import CheckResult, Product

logger = logging.getLogger(__name__)


def is_restock(previous: str | None, current: str) -> bool:
    """Return True on a rising edge into stock.

    A restock is any transition into ``in_stock`` from a different state,
    including the first ever check (``previous`` is ``None``).
    """
    return (
        current == Availability.IN_STOCK.value
        and previous != Availability.IN_STOCK.value
    )


def _latest_status(session: Session, product_id: int) -> str | None:
    return session.scalar(
        select(CheckResult.status)
        .where(CheckResult.product_id == product_id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(1)
    )


def run_check_for_product(
    session: Session, product: Product, *, settings: Settings | None = None
) -> CheckResult:
    """Check one product, store the result, and alert on a restock."""
    settings = settings or get_settings()
    zip_code = product.zip_code or settings.delivery_zip
    previous = _latest_status(session, product.id)

    outcome = run_check(
        product.url,
        variant=product.variant or {},
        zip_code=zip_code,
        settings=settings,
    )

    result = CheckResult(
        product_id=product.id, status=outcome.status, detail=outcome.detail
    )
    session.add(result)
    session.flush()

    if is_restock(previous, outcome.status):
        logger.info("Restock detected for product %s (%s)", product.id, product.name)
        dispatch_restock_alerts(
            session, product, outcome, settings=settings, zip_code=zip_code
        )

    return result


def run_all_checks(settings: Settings | None = None) -> None:
    """Check every active product. Used by the scheduler."""
    settings = settings or get_settings()
    with session_scope() as session:
        products = list(
            session.scalars(select(Product).where(Product.active.is_(True)))
        )
        if not products:
            logger.info("No active products to check")
            return
        logger.info("Checking %d active product(s)", len(products))
        for product in products:
            try:
                run_check_for_product(session, product, settings=settings)
            except Exception:
                logger.exception("Check failed for product %s", product.id)
