# Oracle Always Free Worker

Deployment status: **prepared, not deployed**. Account sign-in and the Ashburn
home region are verified; instance creation and SSH access approval are pending.
The existing Actions monitor remains the only enabled production monitor until
the replacement passes its live access check. Do not claim a ten-minute service
is active based on this setup document alone.

## Resources and cost boundary

Use one **Always Free eligible** Ubuntu 24.04 ARM64 instance in the account's
home region: `VM.Standard.A1.Flex`, 1 OCPU, 4 GB RAM, 50 GB boot disk. Verify the
console's eligibility and the account's existing usage before creation. Do not
upgrade to paid billing, choose a paid shape, provision a NAT gateway, load
balancer, managed database, or buy messaging credits as part of this deployment.

Oracle currently documents a shared free allowance equivalent to 2 OCPUs and
12 GB RAM, plus 200 GB boot/block storage. Capacity may be unavailable; wait or
try another availability domain in the same home region, not a paid substitute.
Idle instances can be reclaimed after meeting seven-day utilization criteria.
This low-traffic worker may qualify. Never generate artificial load to avoid
reclamation. Free hosting is not a promise of uninterrupted operation.

Sources, checked September 26, 2026:
[Always Free limits and reclamation](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
[account and card verification](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm).
TextBelt credits and any other notification-provider costs remain separate.

## Bootstrap

Account creation, password entry, card verification, MFA, and acceptance of
Oracle's terms must be completed by the account owner in Oracle's console.
No credentials should be pasted into chat or committed.

Supply an operator-controlled SSH public key and
[`deploy/oracle-cloud-init.yml`](../deploy/oracle-cloud-init.yml) as the instance's
cloud-init configuration. Use a public subnet with an internet gateway for
outbound HTTPS. Restrict SSH ingress to the operator's current IP; add a narrowly
scoped deployment connection only when performing the handoff. Do not open
port 8000 or publish the dashboard. Confirm all selected network resources are
free before creating them.

The bootstrap installs Docker from its official signed Ubuntu repository,
disables password/root SSH login, and clones this existing public repository to
`/opt/costco-restock-checker`. It does not include secrets, initialize state,
start a worker, or configure a second scheduler. Inspect `cloud-init status
--wait` and the bootstrap result before continuing. Verify the SSH host key
against Oracle's console; do not disable host-key checking.

Sources: [Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/),
[cloud-init configuration](https://docs.cloud-init.io/en/latest/reference/examples.html).

## State-preserving handoff

1. Check out the reviewed deployment commit on the server. Build the image.
2. Transfer the existing configured ZIP/providers/selected recipients into a
   mode-0600 `.env` over a verified encrypted connection. Preserve the primary
   and secondary recipient split; do not copy the old two-recipient local SMS
   setting wholesale. Keep `CHECK_INTERVAL_MINUTES=10`, `SMS_INCLUDE_URL=false`,
   and the internal dashboard scheduler disabled.
3. Run the no-send access check below on Oracle. It requires no checkpoint or
   state key and never writes a production baseline. Unknown Capybara evidence
   exits unsuccessfully. If blocked, leave Oracle stopped and report the result.
4. Disable `monitor.yml` and wait for all running/queued checks to finish. Do
   not cancel an active send. Retrieve the immediately latest run's final
   checkpoint, or its intent if no final exists, even if the run failed. Never
   fall back to an older run or initialize a new baseline.
5. Transfer the **same** `MONITOR_STATE_KEY` and checkpoint to Oracle. If the key
   exists only in GitHub secrets, use a reviewed one-time deployment workflow
   that references the existing secret and transfers it directly over verified
   SSH. GitHub's secrets API cannot return plaintext. Do not print, upload as an
   artifact, or download the key to the laptop; do not substitute a new key.
   This one-time deployment operation is separate from scheduled stock checks.
6. Import the checkpoint from stdin. Identity includes ZIP and recipients;
   mismatches, malformed state, and an existing checkpoint are rejected.
   Confirm baseline, summary anchor, channel outcomes, and global test-send
   counters are unchanged. Keep any existing SQLite data and mattress history.
7. With the daemon stopped, run `--once --test` twice in separate invocations.
   Only a verified available control can send. The second must suppress the
   accepted send. Production baseline/history is unchanged; isolated test
   outcomes and the lifetime two-email/two-SMS budget stay in SQLite. A blocked
   positive-path test is not proof of notification delivery.
8. Start the worker. Observe startup and two automatic interval executions,
   recording actual UTC timestamps. Restart once and verify state survives
   without duplicate alerts. Confirm Actions remains disabled and no local
   scheduler or other cloud monitor owns this watch.

### Manual deployment workflow

`oracle-handoff.yml` is dispatch-only, shares the monitor's concurrency group,
and never starts the worker or sends notifications. Configure these additional
repository secrets only after the VM and its administrative access are approved:
`ORACLE_SSH_HOST` (IPv4), `ORACLE_SSH_PRIVATE_KEY` (dedicated deployment key), and
`ORACLE_SSH_KNOWN_HOSTS` (host key verified against Oracle's console). Transfer
secret files with `gh secret set NAME < protected-file`; never put values in
shell arguments, chat, or workflow inputs. The job reports its public source
address using Amazon's check-IP service and waits up to ten minutes for TCP/22.
Temporarily permit that runner's SSH source only, then remove that rule after
each job (a separate dispatch may use a different address). Do not open
SSH globally to accommodate changing runner addresses.

Dispatch `verify` first. It checks out the workflow's immutable commit on the
server, installs a mode-0600 environment file, builds the image, and runs the
no-send Oracle access check. Existing differing configuration is rejected, not
overwritten. Review the result before disabling `monitor.yml`. Then dispatch
`import` at the same commit. It requires the monitor to be manually disabled
with no unfinished runs, restores its latest checkpoint, and imports that exact
state over strict host-key-checked SSH. It does not initialize a new baseline.
An existing checkpoint makes a repeat import fail closed. Configuration and
keys are never uploaded as artifacts; remote errors are intentionally redacted.

After successful import, remove the temporary deployment key secret from GitHub
and retain operator access privately. Start the worker only after the bounded
test and deduplication checks below. A failure between disabling Actions and
starting Oracle is an explicit monitoring gap, not an automatic fallback.

Commands on the server, after protected configuration is in place:

```sh
cd /opt/costco-restock-checker
sudo docker compose -f compose.worker.yml build
sudo docker compose -f compose.worker.yml run --rm -T monitor xvfb-run -a python -m app.worker --verify
sudo docker compose -f compose.worker.yml run --rm -T monitor python -m app.worker --import-state < monitor-state/state.json
sudo docker compose -f compose.worker.yml run --rm -T monitor xvfb-run -a python -m app.worker --once --test
sudo docker compose -f compose.worker.yml run --rm -T monitor xvfb-run -a python -m app.worker --once --test
sudo docker compose -f compose.worker.yml up -d
sudo docker compose -f compose.worker.yml ps
sudo docker compose -f compose.worker.yml logs --since 30m monitor
```

Never use `--initialize` for this migration. No cron job, systemd timer,
self-hosted GitHub runner, or website is needed; the worker is the sole scheduler.
Checks run on startup and every ten minutes start-to-start. A long check does
not overlap another; outages, maintenance, and Costco blocking can create gaps.
Morning summaries run on the first check at/after 08:30 Eastern, before noon,
every two mornings; the secondary recipient receives only a 14-day summary.
The original anchor survives migration, including daylight-saving behavior.

## Operations and recovery

The worker's private SQLite volume and redacted container logs become
authoritative after migration. The local dashboard and old Actions artifacts
do not mirror new checks. Do not publish SQLite, recipients, or `.env` publicly.
Docker restarts an exited worker; a failing health check alone does not restart
it. Check health and actual successful inventory evidence, not just process
uptime. Missing/corrupt state stops safely and requires recovery, not reset.

Back up the stopped SQLite volume privately before maintenance, along with its
matching configuration/key. Arrange a separate private off-instance backup
within verified free limits before considering the service unattended. A copy
on the same VM does not protect against reclamation or disk loss. Lost state
must never be replaced with `--initialize` to make alerts resume.

Pause with `sudo docker compose -f compose.worker.yml stop`. Do not run `down
-v`, which removes state. Do not re-enable Actions after Oracle has checked or
sent without reverse-migrating the newest state; its old checkpoint is stale.
If interrupted before import/start, keep the new worker stopped and reconcile
the handoff before retrying. No automatic fallback starts a second monitor.

Offline verification:

```sh
.venv/bin/ruff check .
.venv/bin/pytest -m 'not browser'
```

The `ARM64 worker container` CI workflow additionally builds the actual image on
a native GitHub-hosted ARM64 runner and launches headed Chromium under Xvfb with
networking disabled. It uses no secrets, contacts no Costco/provider endpoints,
and does not run a monitor. This is image compatibility evidence, not evidence
of live Costco access from Oracle.
