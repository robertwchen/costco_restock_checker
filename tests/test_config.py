from app.config import Settings


def test_defaults_and_disabled_channels():
    settings = Settings(_env_file=None)
    assert settings.check_interval_minutes == 30
    assert settings.email_enabled is False
    assert settings.sms_enabled is False


def test_email_enabled_requires_all_fields():
    settings = Settings(
        _env_file=None,
        resend_api_key="key",
        alert_email_from="from@example.com",
        alert_email_to="to@example.com",
    )
    assert settings.email_enabled is True


def test_email_disabled_when_partial():
    settings = Settings(_env_file=None, resend_api_key="key")
    assert settings.email_enabled is False


def test_sms_enabled_requires_all_fields():
    settings = Settings(
        _env_file=None,
        twilio_account_sid="sid",
        twilio_auth_token="token",
        twilio_from_number="+15550000000",
        alert_sms_to="+15551111111",
    )
    assert settings.sms_enabled is True
