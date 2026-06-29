from app import alerts
from app.alerts import (
    AlertMessage,
    build_restock_message,
    dispatch_restock_alerts,
    send_email,
    send_sms,
)
from app.checker import Availability, CheckOutcome
from app.config import Settings
from app.models import Product


def _enabled_settings() -> Settings:
    return Settings(
        _env_file=None,
        resend_api_key="key",
        alert_email_from="from@example.com",
        alert_email_to="to@example.com",
        twilio_account_sid="sid",
        twilio_auth_token="token",
        twilio_from_number="+15550000000",
        alert_sms_to="+15551111111",
    )


def test_send_email_disabled_returns_false():
    assert send_email(Settings(_env_file=None), AlertMessage("s", "b")) is False


def test_send_sms_disabled_returns_false():
    assert send_sms(Settings(_env_file=None), AlertMessage("s", "b")) is False


def test_build_restock_message_includes_details():
    product = Product(
        name="Test Mattress",
        url="https://www.costco.com/p/x/1",
        item_number="1847132",
        variant={"Bed Size": "Full", "Firmness": "Firm"},
    )
    message = build_restock_message(
        product, CheckOutcome(Availability.IN_STOCK, "ok"), zip_code="22903"
    )
    assert "Test Mattress is now available" in message.body
    assert "1847132" in message.body
    assert "Bed Size: Full" in message.body
    assert "22903" in message.body
    assert "https://www.costco.com/p/x/1" in message.body


def test_dispatch_records_logs_for_each_channel(session, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(alerts, "send_email", lambda settings, message: calls.append("email") or True)
    monkeypatch.setattr(alerts, "send_sms", lambda settings, message: calls.append("sms") or True)

    product = Product(name="X", url="https://u", variant={})
    session.add(product)
    session.flush()

    logs = dispatch_restock_alerts(
        session,
        product,
        CheckOutcome(Availability.IN_STOCK, "ok"),
        settings=_enabled_settings(),
    )
    assert {log.channel for log in logs} == {"email", "sms"}
    assert all(log.success for log in logs)
    assert calls == ["email", "sms"]


def test_dispatch_without_channels_records_nothing(session):
    product = Product(name="X", url="https://u", variant={})
    session.add(product)
    session.flush()

    logs = dispatch_restock_alerts(
        session,
        product,
        CheckOutcome(Availability.IN_STOCK, "ok"),
        settings=Settings(_env_file=None),
    )
    assert logs == []
