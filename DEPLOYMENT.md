# Deployment

The checker needs a real (headed) browser because Costco blocks headless ones,
and it must keep running for the scheduler to fire alerts. This guide covers how
to run it continuously.

## The most important consideration: which IP it runs from

Costco uses Akamai bot mitigation that escalates with request volume and is much
more aggressive toward **data-center IP ranges** (most cloud providers). In
practice:

- A **residential connection** (a computer at home) is the most reliable place
  to run this. Blocks are occasional and transient, and the built-in retries
  usually get through.
- A **cloud VM / container host** will work, but expect a higher rate of
  `blocked_or_unknown` results because the IP is more likely to be challenged.

Keep the check interval conservative (the default 30 minutes is sensible).
Frequent checks increase the block rate.

## Option A: another computer at home (recommended)

Any always-on computer on a home connection works.

```bash
git clone https://github.com/robertwchen/costco_restock_checker.git
cd costco_restock_checker
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
cp .env.example .env        # then edit: HEADLESS=false, DELIVERY_ZIP, alert keys
```

Run it so it survives the machine going idle:

- macOS (prevents sleep while running):
  ```bash
  caffeinate -is .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  ```
- Linux (systemd user service): create `~/.config/systemd/user/costco.service`:
  ```ini
  [Unit]
  Description=Costco Restock Checker
  After=network-online.target

  [Service]
  WorkingDirectory=%h/costco_restock_checker
  ExecStart=%h/costco_restock_checker/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  Restart=always

  [Install]
  WantedBy=default.target
  ```
  Then `systemctl --user enable --now costco` (and `loginctl enable-linger $USER`
  so it runs without you logged in).

The dashboard is at `http://localhost:8000`.

## Option B: Docker (server without a display)

The image runs headed Chromium under Xvfb, so it works on a headless server.

```bash
cp .env.example .env        # set DELIVERY_ZIP and alert keys (HEADLESS is forced false)
docker compose up --build -d
```

The SQLite database persists on the `costco-data` volume and the container
restarts automatically. This is the unit to deploy to a VPS or any container
host — subject to the data-center IP caveat above.

## Option C: cloud VM

Provision a small Linux VM (a home-IP-like provider or a residential-proxy-free
setup is preferable), install Docker, copy `.env`, and run Option B. A consumer
mini PC or a Raspberry Pi at home is often more reliable than a cloud VM for this
workload.

## Secrets

`.env` holds the TextBelt key and recipient numbers and is gitignored. Never
commit it. On a host, create it directly or pass the values as environment
variables. Rotate the TextBelt key if it is ever exposed.

## Health and logs

- `GET /health` returns `{"status": "ok"}` for uptime checks.
- The app logs each cycle, including restock detections and alert results, to
  stdout.
