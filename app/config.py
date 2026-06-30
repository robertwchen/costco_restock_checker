"""Application configuration.

Settings are read from environment variables (and an optional ``.env`` file)
using pydantic-settings. Field names map to upper-case environment variables,
for example ``delivery_zip`` reads ``DELIVERY_ZIP``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Costco Restock Checker"

    # Checking behaviour
    delivery_zip: str = "98101"
    check_interval_minutes: int = 30
    headless: bool = True
    request_timeout_seconds: int = 45

    # Storage
    database_url: str = "sqlite:///./costco_restock.db"

    # Scheduler
    enable_scheduler: bool = True

    # Email alerts (Resend)
    resend_api_key: str | None = None
    alert_email_from: str | None = None
    alert_email_to: str | None = None

    # SMS alerts (Twilio)
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_api_key_sid: str | None = None
    twilio_api_key_secret: str | None = None
    twilio_from_number: str | None = None
    # One or more recipients, comma-separated. Shared by all SMS channels.
    alert_sms_to: str | None = None

    # SMS alerts (TextBelt) - pay-as-you-go, no number or registration required.
    textbelt_api_key: str | None = None

    @property
    def email_enabled(self) -> bool:
        """True when every value needed to send email is present."""
        return bool(self.resend_api_key and self.alert_email_from and self.alert_email_to)

    @property
    def sms_recipients(self) -> list[str]:
        """Recipient numbers parsed from the comma-separated list."""
        if not self.alert_sms_to:
            return []
        return [number.strip() for number in self.alert_sms_to.split(",") if number.strip()]

    @property
    def twilio_auth_ready(self) -> bool:
        """True when either an API key pair or the auth token is available."""
        has_api_key = bool(self.twilio_api_key_sid and self.twilio_api_key_secret)
        return has_api_key or bool(self.twilio_auth_token)

    @property
    def sms_enabled(self) -> bool:
        """True when every value needed to send SMS through Twilio is present."""
        return bool(
            self.twilio_account_sid
            and self.twilio_from_number
            and self.sms_recipients
            and self.twilio_auth_ready
        )

    @property
    def textbelt_enabled(self) -> bool:
        """True when an SMS can be sent through TextBelt."""
        return bool(self.textbelt_api_key and self.sms_recipients)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
