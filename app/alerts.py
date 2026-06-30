"""Email and SMS alerting.

Both channels are optional. When the relevant credentials are not configured
the sender is skipped and reported as not sent. Every attempt is recorded as an
:class:`AlertLog` row so the history is auditable.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
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
    sms_text: str = ""


def build_restock_message(
    product: Product,
    outcome: CheckOutcome,
    *,
    zip_code: str,
    include_url: bool = False,
) -> AlertMessage:
    """Compose the alert for a restocked product.

    ``body`` is the long form used for email. ``sms_text`` is a compact,
    single-segment form used for SMS to keep per-message cost down. The URL is
    omitted from SMS unless ``include_url`` is set, since some gateways block
    links from unverified senders.
    """
    subject = f"Restock alert: {product.name}"
    lines = [f"{product.name} is now available."]
    if product.item_number:
        lines.append(f"Item number: {product.item_number}")
    if product.variant_label:
        lines.append(f"Variant: {product.variant_label}")
    lines.append(f"Delivery ZIP: {zip_code}")
    lines.append(f"Detail: {outcome.detail}")
    lines.append(product.url)

    # Keep SMS to ASCII (GSM-7) and ~one segment so each message costs one credit.
    short_name = product.name if len(product.name) <= 38 else product.name[:35] + "..."
    if include_url:
        sms_text = f"In stock {zip_code}: {short_name} {product.url.split('?')[0]}"
    else:
        item = f" item {product.item_number}" if product.item_number else ""
        sms_text = f"In stock {zip_code}: {short_name}{item}. Check Costco."
    return AlertMessage(subject=subject, body="\n".join(lines), sms_text=sms_text)


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
        body = message.sms_text or f"{message.subject}\n{message.body}"
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


TEXTBELT_URL = "https://textbelt.com/text"


def _post_textbelt(payload: dict[str, str]) -> dict:
    data = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(TEXTBELT_URL, data=data)
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        return json.load(response)


def send_textbelt(settings: Settings, message: AlertMessage) -> bool:
    """Send an SMS through TextBelt to every recipient. Returns True on success."""
    if not settings.textbelt_enabled:
        logger.debug("TextBelt alerts disabled; skipping")
        return False
    body = message.sms_text or f"{message.subject}\n{message.body}"
    all_sent = True
    for recipient in settings.sms_recipients:
        try:
            result = _post_textbelt(
                {"phone": recipient, "message": body, "key": settings.textbelt_api_key or ""}
            )
            if not result.get("success"):
                logger.error(
                    "TextBelt failed for %s: %s", recipient, result.get("error")
                )
                all_sent = False
        except Exception:
            logger.exception("TextBelt request failed for %s", recipient)
            all_sent = False
    return all_sent


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
    message = build_restock_message(
        product, outcome, zip_code=zip_code, include_url=settings.sms_include_url
    )

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

    if settings.textbelt_enabled:
        success = send_textbelt(settings, message)
        logs.append(
            AlertLog(
                product_id=product.id,
                channel="textbelt",
                target=", ".join(settings.sms_recipients),
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
