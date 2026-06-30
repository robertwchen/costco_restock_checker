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


def test_build_restock_message_has_compact_sms():
    product = Product(
        name="A" * 80,
        url="https://www.costco.com/p/x/1?ADBUTLERID=zzz",
        item_number="123",
        variant={},
    )
    outcome = CheckOutcome(Availability.IN_STOCK, "ok")

    # Default: no URL (works on gateways that block links), ASCII single segment.
    message = build_restock_message(product, outcome, zip_code="20120")
    assert message.sms_text.startswith("In stock 20120:")
    assert "http" not in message.sms_text
    assert "item 123" in message.sms_text
    assert "..." in message.sms_text  # long name truncated
    assert message.sms_text.isascii()

    # Opt-in: include the product URL (query stripped).
    with_url = build_restock_message(product, outcome, zip_code="20120", include_url=True)
    assert "https://www.costco.com/p/x/1" in with_url.sms_text
    assert "?ADBUTLERID" not in with_url.sms_text


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


def test_send_sms_delivers_to_every_recipient(monkeypatch):
    sent: list[str] = []

    class _Messages:
        def create(self, *, body, from_, to):
            sent.append(to)

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(alerts, "_twilio_client", lambda settings: _Client())
    settings = Settings(
        _env_file=None,
        twilio_account_sid="AC123",
        twilio_auth_token="token",
        twilio_from_number="+15550000000",
        alert_sms_to="+15550001111,+15550002222",
    )
    assert alerts.send_sms(settings, AlertMessage("subject", "body")) is True
    assert sent == ["+15550001111", "+15550002222"]


def test_twilio_client_prefers_api_key():
    settings = Settings(
        _env_file=None,
        twilio_account_sid="AC123",
        twilio_api_key_sid="SK123",
        twilio_api_key_secret="secret",
    )
    client = alerts._twilio_client(settings)
    assert client.username == "SK123"
    assert client.account_sid == "AC123"


def test_send_textbelt_delivers_to_every_recipient(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        alerts,
        "_post_textbelt",
        lambda payload: calls.append(payload["phone"]) or {"success": True, "quotaRemaining": 9},
    )
    settings = Settings(
        _env_file=None, textbelt_api_key="key", alert_sms_to="+15550001111,+15550002222"
    )
    assert alerts.send_textbelt(settings, AlertMessage("subject", "body")) is True
    assert calls == ["+15550001111", "+15550002222"]


def test_send_textbelt_reports_failure(monkeypatch):
    monkeypatch.setattr(
        alerts, "_post_textbelt", lambda payload: {"success": False, "error": "out of quota"}
    )
    settings = Settings(_env_file=None, textbelt_api_key="key", alert_sms_to="+15550001111")
    assert alerts.send_textbelt(settings, AlertMessage("subject", "body")) is False


def test_send_textbelt_disabled_returns_false():
    assert alerts.send_textbelt(Settings(_env_file=None), AlertMessage("s", "b")) is False


def test_dispatch_uses_textbelt_when_enabled(session, monkeypatch):
    monkeypatch.setattr(alerts, "send_textbelt", lambda settings, message: True)
    settings = Settings(_env_file=None, textbelt_api_key="key", alert_sms_to="+15550001111")
    product = Product(name="X", url="https://u", variant={})
    session.add(product)
    session.flush()

    logs = dispatch_restock_alerts(
        session, product, CheckOutcome(Availability.IN_STOCK, "ok"), settings=settings
    )
    assert [log.channel for log in logs] == ["textbelt"]
    assert logs[0].success is True


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
