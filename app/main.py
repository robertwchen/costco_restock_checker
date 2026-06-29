"""FastAPI application: dashboard, product actions, and lifecycle wiring."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .config import get_settings
from .database import get_db, init_db, session_scope
from .models import AlertLog, CheckResult, Product
from .scheduler import create_scheduler
from .seed import seed_default_product
from .services import run_all_checks, run_check_for_product

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _datetimeformat(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M UTC")


templates.env.filters["datetimeformat"] = _datetimeformat


def parse_variant(raw: str) -> dict[str, str]:
    """Parse ``key=value, key=value`` text into a variant mapping."""
    variant: dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key, value = key.strip(), value.strip()
        if key:
            variant[key] = value
    return variant


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    with session_scope() as session:
        seed_default_product(session)

    scheduler = None
    if settings.enable_scheduler:
        scheduler = create_scheduler(settings)
        scheduler.start()
        logger.info(
            "Scheduler started; checking every %s minute(s)",
            settings.check_interval_minutes,
        )
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(title="Costco Restock Checker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _base_context(request: Request) -> dict[str, object]:
    settings = get_settings()
    return {
        "request": request,
        "app_name": settings.app_name,
        "default_zip": settings.delivery_zip,
        "interval": settings.check_interval_minutes,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    products = list(db.scalars(select(Product).order_by(Product.created_at.desc())))
    context = _base_context(request) | {"products": products}
    return templates.TemplateResponse(request, "index.html", context)


@app.post("/products")
def create_product(
    name: str = Form(...),
    url: str = Form(...),
    item_number: str = Form(""),
    zip_code: str = Form(""),
    variant: str = Form(""),
    db: Session = Depends(get_db),
):
    product = Product(
        name=name.strip(),
        url=url.strip(),
        item_number=item_number.strip() or None,
        zip_code=zip_code.strip() or None,
        variant=parse_variant(variant),
        active=True,
    )
    db.add(product)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.get("/products/{product_id}")
def product_detail(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        return RedirectResponse(url="/", status_code=303)

    checks = list(
        db.scalars(
            select(CheckResult)
            .where(CheckResult.product_id == product_id)
            .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
            .limit(25)
        )
    )
    alerts = list(
        db.scalars(
            select(AlertLog)
            .where(AlertLog.product_id == product_id)
            .order_by(AlertLog.created_at.desc(), AlertLog.id.desc())
            .limit(10)
        )
    )
    context = _base_context(request) | {
        "product": product,
        "checks": checks,
        "alerts": alerts,
    }
    return templates.TemplateResponse(request, "product.html", context)


def _check_product_now(product_id: int) -> None:
    settings = get_settings()
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product is not None:
            run_check_for_product(session, product, settings=settings)


@app.post("/products/{product_id}/check")
async def check_now(product_id: int):
    await run_in_threadpool(_check_product_now, product_id)
    return RedirectResponse(url=f"/products/{product_id}", status_code=303)


@app.post("/check-all")
async def check_all():
    await run_in_threadpool(run_all_checks)
    return RedirectResponse(url="/", status_code=303)


@app.post("/products/{product_id}/toggle")
def toggle_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is not None:
        product.active = not product.active
        db.commit()
    return RedirectResponse(url=f"/products/{product_id}", status_code=303)


@app.post("/products/{product_id}/delete")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is not None:
        db.delete(product)
        db.commit()
    return RedirectResponse(url="/", status_code=303)
