"""SQLAlchemy ORM models.

A :class:`Product` is a tracked page and variant. Each automated or manual
check writes a :class:`CheckResult`, and every alert attempt is recorded as an
:class:`AlertLog` for auditing.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    item_number: Mapped[str | None] = mapped_column(String(64), default=None)
    variant: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    zip_code: Mapped[str | None] = mapped_column(String(16), default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    checks: Mapped[list[CheckResult]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="CheckResult.checked_at.desc(), CheckResult.id.desc()",
    )
    alerts: Mapped[list[AlertLog]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
    )

    @property
    def latest_check(self) -> CheckResult | None:
        return self.checks[0] if self.checks else None

    @property
    def variant_label(self) -> str:
        """Human-readable variant description, e.g. ``Bed Size: Full, Firmness: Firm``."""
        if not self.variant:
            return ""
        return ", ".join(f"{key}: {value}" for key, value in self.variant.items())


class CheckResult(Base):
    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    product: Mapped[Product] = relationship(back_populates="checks")


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(255), default="")
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="alerts")
