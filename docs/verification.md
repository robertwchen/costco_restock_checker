# Deployment Verification

## Oracle preparation - September 27, 2026

Oracle Always Free is the selected replacement, but account sign-in remains
pending. No Oracle instance or paid resource has been created. Actions remains
the sole production monitor; its observed cadence is still inadequate.

On commit `54d849b`, [hosted lint and 128 offline tests](https://github.com/robertwchen/costco_restock_checker/actions/runs/36327455227)
passed. [Native ARM64 container verification](https://github.com/robertwchen/costco_restock_checker/actions/runs/36327455077)
also passed: the Docker image built, headed Chromium rendered local HTML under
Xvfb with networking disabled, and the worker CLI loaded. The first container
test timed out; the passing version mounts its test file instead of using stdin
and enforces a two-minute timeout. This verifies image compatibility, not Costco
access from Oracle. The local image build was blocked by a disk I/O error on the
nearly full laptop; no user data was deleted.

New regression tests cover checkpoint import without resetting accepted sends
or test budgets, refusal to overwrite existing state, corrupt/wrong-identity
imports, no-send verification, isolated control tests across invocations, and
exclusive worker locking. No real test notifications were sent.

The latest inspected scheduled run,
[36323813687](https://github.com/robertwchen/costco_restock_checker/actions/runs/36323813687),
checked Capybara 2005333 at **2026-09-27 13:51:45.892760 UTC** for the configured
private ZIP and US standard delivery. It returned explicit **NOSTOCK** and
reserved zero notifications. Oracle access, state transfer, restart persistence,
two automatic worker intervals, and live positive-path sends remain unverified.

See [Oracle deployment](oracle.md) for the prepared bootstrap and handoff.

## Live hosted results - September 26, 2026

All observations used the existing configured delivery ZIP (kept private), US
standard-delivery context, fresh inventory responses, and live product mapping.
No cart or purchase operations were performed.

| Run | Trigger | Actual check time UTC | Result |
|---|---|---|---|
| [36251353121](https://github.com/robertwchen/costco_restock_checker/actions/runs/36251353121) | Manual initialization | 15:17:14 | Capybara 2005333: NOSTOCK |
| [36251423679](https://github.com/robertwchen/costco_restock_checker/actions/runs/36251423679) | Manual control test | 15:18:25 | Capybara 2005333, Dog 2005332, Red Panda 2005334: all NOSTOCK |
| [36251922629](https://github.com/robertwchen/costco_restock_checker/actions/runs/36251922629) | Repeated control test | 15:27:02-03 | Same three variants: all NOSTOCK |
| [36263234882](https://github.com/robertwchen/costco_restock_checker/actions/runs/36263234882) | Actual schedule | 18:39:05 | Capybara 2005333: NOSTOCK |
| [36273466851](https://github.com/robertwchen/costco_restock_checker/actions/runs/36273466851) | Actual schedule | 21:36:28 | Capybara 2005333: NOSTOCK |

Raccoon's mapping to 2005335 was verified on the live page. Its inventory was
not used as a control, respecting the limit of two non-Capybara controls.
No verified available control was found, so live positive-path notification
testing is blocked. Actual sends: **0 emails, 0 SMS**. There is no provider
acceptance or delivery claim. Email settings are absent; TextBelt is configured.

## Scheduling limitation observed

The scheduled runs were created at 18:38:05 and 21:35:33 UTC. Their hosted jobs
started four and three seconds later, respectively. Inventory checks occurred
177 minutes 23 seconds apart, despite the requested ten-minute cron. GitHub
does not expose the original intended tick in these run records, so an exact
per-tick delay cannot be assigned. Many expected execution windows were absent.

Actions is enabled and operates independently of a laptop, but the observed
cadence is insufficient for reliable ten-minute monitoring or punctual morning
summaries. A persistent worker is prepared; creating a paid host requires the
owner's explicit authorization. Until then, Actions remains the sole active
production monitor. No paid resources were provisioned.

## State and tests

The final artifacts from the repeated manual run and both scheduled runs were
downloaded in memory and schema-validated. Each archive was 294 bytes; all three
preserved the same confirmed out-of-stock baseline and zero test-send counters.
No ZIP, recipients, session data, or secrets appear in the artifacts. No local
process or self-hosted runner is required.

[CI on 8b6356a](https://github.com/robertwchen/costco_restock_checker/actions/runs/36252165260)
passed lint and 114 offline tests. The separate local browser test passed.
Regression coverage includes exact SKU/location/freshness, conflicting data,
blocks/timeouts, first availability and restock transitions, unknown gaps,
independent channel outcomes, ambiguous sends, bounded test sends, restart
deduplication, missing/corrupt state, and summary dates across daylight saving.
The persistent worker also has a mocked startup/restart test against SQLite;
real access from a paid fallback host remains unverified.

Primary summaries are configured for every two mornings at 08:30 Eastern,
starting September 28. The existing secondary recipient receives only a summary
every 14 days, starting October 10. These dates remain subject to the current
Actions scheduling limitation.
