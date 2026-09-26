# Cloud Deployment

## Active monitor

[Plush stock monitor](https://github.com/robertwchen/costco_restock_checker/actions/workflows/monitor.yml)
runs on standard GitHub-hosted Linux, with headed Chromium under Xvfb. No laptop,
editor, local terminal, or self-hosted runner is required. The workflow on `main`
uses `7,17,27,37,47,57 * * * *`, shared manual/scheduled concurrency without
cancellation, an eight-minute timeout, and two bounded inventory attempts with
backoff. The internal scheduler is disabled. The original mattress watch and
database are not deleted or scheduled by this workflow.

GitHub scheduling is best effort: jobs can be delayed or dropped. Public-repo
schedules disable after 60 days without repository activity. No keepalive
commits are generated. Inspect actual **schedule** events on the workflow page.
Re-enable using `gh workflow enable monitor.yml`; expired state still requires
recovery. Dispatch new runs instead of rerunning old jobs, which is rejected.
An additional `30 8 * * *` trigger uses `timezone: America/New_York` for the
morning summary. Both triggers share the same state and concurrency group;
calendar eligibility prevents daily or duplicate summary sends.

## Exact variant and location

Product: [Jumbo Baby Animal Plush](https://www.costco.com/p/-/jumbo-baby-animal-plush/4201016777).
The live `Design` selector and product data verified these mappings on September
26, 2026. Every check revalidates the relationship to parent 4201016777.

| Design | Inventory item |
|---|---|
| Capybara | 2005333 |
| Dog | 2005332 |
| Red Panda | 2005334 |
| Raccoon | 2005335 |

The parent URL ID is not a sellable SKU. JSON-LD supplies matched price only,
never plush availability. The existing inventory endpoint receives the exact
SKU, configured private ZIP, and country US. It does not normally echo ZIP:
location is bound to the actual request and unchanged final response URL. Any
echoed location must match. This is US standard delivery, not warehouse stock
or an express-shipping promise. SKU must match, Date/Age must be fresh within
120 seconds, and `availableForSale` must agree with `INSTOCK`/`NOSTOCK`.
Conflicts, unknown codes, malformed/missing data, absent identity, blocks, and
timeouts produce `blocked_or_unknown`. An absent option alone never means sold out.

First hosted result, 2026-09-26 15:17:14 UTC: Capybara explicitly NOSTOCK for the
configured ZIP. This is a historical observation; consult newer run summaries.

## Recipients, summaries, and secrets

TextBelt is the configured provider. The selected primary recipient is held in
`ALERT_SMS_TO`; the other existing recipient is in `SUMMARY_SMS_TO` and receives
only a summary every 14 days. Primary channels get all four animals every two
mornings, on the first run at/after 08:30 America/New_York and before noon. The
calendar anchor is explicit initialization; daylight saving changes are handled.
An entirely missed morning is skipped, not delivered as stale information later.

Required configuration: `DELIVERY_ZIP`, stable random `MONITOR_STATE_KEY` of at
least 32 characters, and one complete provider. Resend requires `RESEND_API_KEY`,
`ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`; these are currently absent. Twilio requires
`TWILIO_ACCOUNT_SID`, `TWILIO_FROM_NUMBER`, `ALERT_SMS_TO`, and `TWILIO_AUTH_TOKEN`
or the `TWILIO_API_KEY_SID`/`TWILIO_API_KEY_SECRET` pair. The current Twilio sender
is absent. TextBelt requires `TEXTBELT_API_KEY` and `ALERT_SMS_TO`. Complete Twilio
takes precedence; two SMS providers never send the same notification.
`SMS_INCLUDE_URL=false` is preserved; emails always include the product URL.

Set secrets through repository Settings > Secrets and variables > Actions, or
an interactive `gh secret set NAME --repo robertwchen/costco_restock_checker`
prompt. Never put values in command arguments, chat, documentation, screenshots,
or commits. GitHub secrets cannot be downloaded in plaintext. `.env` is ignored;
examples contain placeholders only. Recipient/ZIP changes require deliberate
checkpoint identity migration, not silent deduplication reset.

## State and notifications

`python -m app.cloud prepare --mode monitor` restores and validates state,
checks inventory, and reserves individual notifications. The workflow uploads
`monitor-intent` before `python -m app.cloud send`. It then uploads
`monitor-final`, including usable outcomes after partial failure. `initialize`
is allowed only on the first workflow run and enables one initial verified
in-stock alert. It cannot reset missing/corrupt state.

Checkpoints are schema-versioned and capped at 16 KiB. They contain keyed opaque
identities, confirmed availability, an event counter, calendar anchor, bounded
per-recipient outcomes, and test-send counters. No ZIP, recipient, secret,
session, full private database, or request data is uploaded. The private send
plan stays on the ephemeral runner. Two small artifacts per run have 30-day
retention and count toward GitHub's shared artifact storage allowance.

Restore uses the immediately preceding run, including failed runs: final state
first, otherwise intent. It never silently falls back to an older run. Missing,
expired, oversized, corrupt, or mismatched state stops notifications. Failure
before saving a checkpoint therefore requires operator recovery. This trades
availability for protection against duplicate alerts.

Unknown readings preserve confirmed state. Repeated in-stock does not alert;
a later confirmed out-of-stock then in-stock creates a new event. Each recipient
and channel has independent outcomes. Explicit rejections retry on a later
eligible run, at most three attempts. Accepted sends are not retried. Resend
receives an idempotency key. TextBelt/Twilio have no equivalent guarantee here:
timeouts and unfinished intents are ambiguous and suppressed pending review.
Acceptance is not confirmed device delivery.

Exactly-once delivery is not promised. A crash between intent upload and sending
can miss an alert. A crash after sending but before final upload leaves an
ambiguous intent and suppresses automatic retry. If both artifacts are lost,
subsequent runs stop. Never delete checkpoints to fix an alert failure.

## Recovery and authoritative results

1. Disable the workflow while investigating state loss or ambiguous sends.
2. Inspect the latest run's intent/final checkpoint and provider records.
   Reconcile all later send attempts before considering any older checkpoint.
3. Preserve confirmed state, accepted/ambiguous outcomes, and test counters when
   migrating ZIP, recipients, or hosts. Validate schema and watch identity.
4. Recovery requires publishing a reconciled checkpoint associated with the
   latest run, or migrating it to the persistent worker under review. There is
   intentionally no automatic reset switch.
5. Re-enable, dispatch a new run, then inspect a subsequent scheduled event.

The local dashboard reads local SQLite, not cloud artifacts. Cloud summaries
contain timestamp, exact variant/SKU, availability/reason, and notification
outcomes. Workflow controls provide manual checks and pause/resume. A green job
with an unknown result proves execution, not stock availability. Do not publish
the existing unauthenticated local dashboard.

## Verification

```sh
.venv/bin/ruff check .
.venv/bin/pytest -m "not browser"
gh workflow run monitor.yml -f mode=verify
gh workflow run monitor.yml -f mode=test
```

CI is deterministic, offline, and uses mocked sends. Cloud `test` checks
Capybara and up to two controls (Dog, Red Panda). It sends only for a verified
available control, labeled `TEST — [animal] availability check — not a Capybara
restock.` Production baseline remains untouched. The isolated fixed test event
suppresses a second invocation even if another control becomes available.
Persisted budgets cap all test attempts across recipients/providers at two
emails and two SMS; rejections consume budget too. Never reset these counters
when changing hosts. If no control is available, live positive-path testing is
blocked. No configured email means email testing remains unavailable.

## Hosting comparison

Official pricing checked September 26, 2026. Provider charges are separate;
TextBelt uses existing credits, and long summaries can use multiple SMS segments.
No new credits or paid resources are purchased automatically.

| Approach | Cost assumption | Practical tradeoff |
|---|---|---|
| Actions (selected) | $0 standard public-repo runner charge; small artifacts subject to shared storage allowance | Headed browser works; best-effort cron and state recovery limits |
| Render worker | 1 CPU, 2 GB RAM + 1 GB disk: $25.25/month on Hobby | Managed restarts and persistent SQLite |
| DigitalOcean VPS | Basic 1 vCPU, 2 GB RAM, 50 GB disk: $12/month before optional backups/tax | Persistent disk; OS/security maintenance |

Sources: [GitHub billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[Render pricing](https://render.com/pricing),
[Render plans](https://render.com/docs/compute-plans),
[DigitalOcean](https://www.digitalocean.com/pricing/droplets).
Changing providers cannot guarantee Costco access. No CAPTCHA bypass, proxy
rotation, cart operations, or orders are used.

## Inactive persistent fallback

`compose.worker.yml` provides one worker under Xvfb, persistent SQLite, automatic
restart, and a heartbeat health check. It checks on startup, then sleeps ten
minutes after completion. A disk lock excludes another worker on the same disk.
No dashboard port is published. Its new checkpoint table and Capybara seed do
not delete the mattress or history; the watch's active flag is respected.

Before switching: authorize the concrete paid resource, verify Costco there,
disable Actions, migrate reconciled state and private configuration, and verify
only one scheduler. Do not initialize to replace lost production state. For a
genuinely new installation only:

```sh
docker compose -f compose.worker.yml run --rm monitor python -m app.worker --initialize
docker compose -f compose.worker.yml up --build -d
```

Use protected `.env` and stable `MONITOR_STATE_KEY`; privately back up SQLite.
Missing/corrupt checkpoints stop the worker. Health failures flag stale checks;
Docker restarts exited processes, not unhealthy ones. Keep dashboard checks
disabled for the cloud-owned watch to avoid a second notification path.
