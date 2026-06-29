"""Default tracked product used to seed an empty database.

The default is the product named in the project brief. Additional products can
be added through the dashboard, so the data here is only a convenience for a
fresh install.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Product

DEFAULT_PRODUCT: dict[str, object] = {
    "name": 'Novaform 14" Legacy Premier Support Hybrid Euro Top Mattress',
    "url": "https://www.costco.com/p/-/novaform-14-legacy-premier-support-hybrid-euro-top-mattress/4000326605?ADBUTLERID=category_hero_Novaform_Mattresses_062226",
    "item_number": "1847132",
    "variant": {"Bed Size": "Full", "Firmness": "Firm"},
}


def seed_default_product(session: Session) -> Product | None:
    """Insert the default product if no products exist yet."""
    if session.scalar(select(Product).limit(1)) is not None:
        return None

    settings = get_settings()
    product = Product(
        name=str(DEFAULT_PRODUCT["name"]),
        url=str(DEFAULT_PRODUCT["url"]),
        item_number=str(DEFAULT_PRODUCT["item_number"]),
        variant=dict(DEFAULT_PRODUCT["variant"]),  # type: ignore[arg-type]
        zip_code=settings.delivery_zip,
    )
    session.add(product)
    session.flush()
    return product
