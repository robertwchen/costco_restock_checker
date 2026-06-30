"""Email and SMS alerting.

Both channels are optional. When the relevant credentials are not configured
the sender is skipped and reported as not sent. Every attempt is recorded as an
:class:`AlertLog` row so the history is auditable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .checker import CheckOutcome
from .config import Settings, get_settings
from .models import AlertLog, Product

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertMessage:
    subject: str
    body: str


def build_restock_message(
    product: Product, outcome: CheckOutcome, *, zip_code: str
) -> AlertMessage:
    """Compose the alert subject and body for a restocked product."""
    subject = f"Restock alert: {product.name}"
    lines = [f"{product.name} is now available."]
    if product.item_number:
        lines.append(f"Item number: {product.item_number}")
    if product.variant_label:
        lines.append(f"Variant: {product.variant_label}")
    lines.append(f"Delivery ZIP: {zip_code}")
    lines.append(f"Detail: {outcome.detail}")
    lines.append(product.url)
    return AlertMessage(subject=subject, body="\n".join(lines))


def send_email(settings: Settings, message: AlertMessage) -> bool:
    """Send an email through Resend. Returns True on success."""
    if not settings.email_enabled:
        logger.debug("Email alerts disabled; skipping")
        return False
    try:
        import resend

        resend.api_key = settings.resend_api_key
        resend.Emails.send(
            {
                "from": settings.alert_email_from,
                "to": [settings.alert_email_to],
                "subject": message.subject,
                "text": message.body,
            }
        )
        return True
    except Exception:
        logger.exception("Failed to send email alert")
        return False


def _twilio_client(settings: Settings):
    """Build a Twilio client, preferring API key authentication."""
    from twilio.rest import Client

    if settings.twilio_api_key_sid and settings.twilio_api_key_secret:
        return Client(
            settings.twilio_api_key_sid,
            settings.twilio_api_key_secret,
            settings.twilio_account_sid,
        )
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def send_sms(settings: Settings, message: AlertMessage) -> bool:
    """Send an SMS through Twilio to every recipient. Returns True on success."""
    if not settings.sms_enabled:
        logger.debug("SMS alerts disabled; skipping")
        return False
    try:
        client = _twilio_client(settings)
        body = f"{message.subject}\n{message.body}"
        all_sent = True
        for recipient in settings.sms_recipients:
            try:
                client.messages.create(
                    body=body, from_=settings.twilio_from_number, to=recipient
                )
            except Exception:
                logger.exception("Failed to send SMS to %s", recipient)
                all_sent = False
        return all_sent
    except Exception:
        logger.exception("Failed to initialize Twilio client")
        return False


def dispatch_restock_alerts(
    session: Session,
    product: Product,
    outcome: CheckOutcome,
    *,
    settings: Settings | None = None,
    zip_code: str | None = None,
) -> list[AlertLog]:
    """Send alerts on enabled channels and record each attempt."""
    settings = settings or get_settings()
    zip_code = zip_code or product.zip_code or settings.delivery_zip
    message = build_restock_message(product, outcome, zip_code=zip_code)

    logs: list[AlertLog] = []

    if settings.email_enabled:
        success = send_email(settings, message)
        logs.append(
            AlertLog(
                product_id=product.id,
                channel="email",
                target=settings.alert_email_to or "",
                success=success,
                message=message.subject,
            )
        )

    if settings.sms_enabled:
        success = send_sms(settings, message)
        logs.append(
            AlertLog(
                product_id=product.id,
                channel="sms",
                target=settings.alert_sms_to or "",
                success=success,
                message=message.subject,
            )
        )

    if not logs:
        logger.info("Restock detected but no alert channels are configured")

    for log in logs:
        session.add(log)
    session.flush()
    return logs
