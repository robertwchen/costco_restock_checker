# Costco Restock Checker

[![tests](https://github.com/robertwchen/costco_restock_checker/actions/workflows/tests.yml/badge.svg)](https://github.com/robertwchen/costco_restock_checker/actions/workflows/tests.yml)

A scheduled service that monitors a Costco product page and sends an email or
SMS alert when a tracked item and variant becomes available for delivery to a
configured ZIP code.

It is built as a small, self-contained example of a scrape-and-alert service:
a FastAPI app, a Playwright-driven page checker, a SQLite store, a background
scheduler, and a minimal dashboard. The default tracked product is a Novaform
mattress (item 1847132, Full / Firm), and any other product URL can be added
from the dashboard.

![Dashboard](docs/dashboard.png)

## Features

- Scheduled availability checks at a configurable interval (default 30 minutes).
- Real-browser checking with best-effort delivery ZIP and variant selection.
- Three honest states: in stock, out of stock, and blocked or unknown.
- Restock alerts over email (Resend) and SMS (Twilio), each optional.
- Minimal dashboard to add, view, check, pause, and remove products.
- Stored check and alert history per product.
- Docker image, unit tests, and a GitHub Actions workflow.

## How it works

1. The scheduler runs every `CHECK_INTERVAL_MINUTES` and checks each active product.
2. For each product, Chromium loads the page, sets the delivery ZIP, and selects
   the configured variant on a best-effort basis.
3. The rendered HTML is classified into `in_stock`, `out_of_stock`, or
   `blocked_or_unknown`. Blocks and challenges are reported, never circumvented.
4. The result is stored. When an item transitions into stock, the configured
   alert channels fire once.

The classification function is pure and unit-tested; the browser driver wraps it.

## Tech stack

Python 3.12, FastAPI, Jinja2, Playwright (Chromium), SQLAlchemy with SQLite,
APScheduler, Resend, Twilio, Docker, pytest, and GitHub Actions.

## Getting started

Requires Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env   # then edit as needed
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. The scheduler runs its first check one interval
after start; use Check now or Check all now to run immediately.

You can also check a single URL from the command line:

```bash
python -m app.checker "https://www.costco.com/p/..." --zip 22903
```

## Configuration

All settings are read from the environment (or a `.env` file).

| Variable | Default | Description |
| --- | --- | --- |
| `DELIVERY_ZIP` | `98101` | ZIP used when checking availability. |
| `CHECK_INTERVAL_MINUTES` | `30` | Minutes between scheduled checks. |
| `HEADLESS` | `true` | Run the browser headless. |
| `REQUEST_TIMEOUT_SECONDS` | `45` | Per-check navigation timeout. |
| `DATABASE_URL` | `sqlite:///./costco_restock.db` | SQLAlchemy database URL. |
| `ENABLE_SCHEDULER` | `true` | Start the scheduler on boot. |
| `RESEND_API_KEY` | empty | Resend API key (email). |
| `ALERT_EMAIL_FROM` | empty | Verified sender address. |
| `ALERT_EMAIL_TO` | empty | Recipient address. |
| `TWILIO_ACCOUNT_SID` | empty | Twilio account SID (SMS). |
| `TWILIO_AUTH_TOKEN` | empty | Twilio auth token. |
| `TWILIO_FROM_NUMBER` | empty | Twilio sender number. |
| `ALERT_SMS_TO` | empty | Recipient phone number. |

Email is enabled only when all three Resend values are set; SMS is enabled only
when all four Twilio values are set. Otherwise the channel is skipped.

## Running with Docker

```bash
cp .env.example .env
docker compose up --build
```

The image installs Chromium and its system dependencies, and the SQLite
database is persisted on a named volume. The app is served on
http://localhost:8000.

## Testing

```bash
pip install -r requirements-dev.txt
ruff check .
pytest -m "not browser"   # fast unit tests, no browser
pytest -m browser         # end-to-end browser test (requires chromium)
```

CI runs the linter and the non-browser tests on Python 3.12.

## Project structure

```
app/
  main.py        FastAPI app, routes, and lifespan wiring
  config.py      Environment-based settings
  database.py    Engine, session factory, helpers
  models.py      Product, CheckResult, AlertLog
  seed.py        Default tracked product
  checker.py     Playwright driver and HTML classifier
  services.py    Check, store, restock detection
  alerts.py      Resend email and Twilio SMS
  scheduler.py   APScheduler interval job
  templates/     Jinja2 dashboard
  static/        Stylesheet
tests/           pytest suite
```

## Scope, limitations, and responsible use

- Availability detection is heuristic and best-effort. Page structure and bot
  mitigation change over time, so results are not guaranteed to be accurate and
  should not be relied on for purchasing decisions.
- The checker does not solve CAPTCHAs, use proxies, or attempt to evade bot
  detection. When a page is blocked or challenged it is reported as
  `blocked_or_unknown`.
- Costco Same-Day and Instacart sources are out of scope.
- Use a conservative check interval and respect the target site's terms of use.

## License

MIT. See [LICENSE](LICENSE).
