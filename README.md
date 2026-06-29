# Costco Restock Checker

A small service that monitors a Costco product page on a schedule and sends an
email or SMS alert when a tracked item and variant becomes available for
delivery to a configured ZIP code.

The project is built as a clean, self-contained example of a scheduled
scraping-and-alerting service: a FastAPI app, a Playwright-driven page checker,
a SQLite store, a background scheduler, and a minimal dashboard.

## Status

Under active development. Milestones:

1. Project skeleton
2. Database models and configuration
3. Playwright checker
4. Email and SMS alerts
5. Dashboard UI and scheduler
6. Docker, tests, CI, and documentation

## Stack

- Python 3.12
- FastAPI and Jinja2
- Playwright (Chromium)
- SQLite via SQLAlchemy
- APScheduler
- Resend (email) and Twilio (SMS)
- Docker, pytest, and GitHub Actions

## How it works

The checker loads a product page in a real browser, sets the delivery ZIP,
selects the configured variant, and reads the rendered page for availability
signals. Each result is stored, and when an item transitions from unavailable
to available the configured alert channels fire once.

## Scope and limitations

- Availability is detected on a best-effort basis and is not guaranteed to be
  accurate. Page structure and bot mitigation can change at any time.
- The checker does not bypass CAPTCHAs, use proxies, or attempt to evade bot
  detection. If a page is blocked it is reported as `blocked_or_unknown`.
- Costco Same-Day and Instacart are out of scope.

## License

MIT. See [LICENSE](LICENSE).
