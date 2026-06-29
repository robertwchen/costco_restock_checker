"""Shared test configuration and fixtures.

Environment variables are set before any application module is imported so the
app binds to an isolated temporary database with the scheduler and alert
channels disabled.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="crc-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/test.db"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["DELIVERY_ZIP"] = "22903"
for _key in (
    "RESEND_API_KEY",
    "ALERT_EMAIL_FROM",
    "ALERT_EMAIL_TO",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "ALERT_SMS_TO",
):
    os.environ[_key] = ""

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_database() -> Iterator[None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
