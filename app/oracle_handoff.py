"""Manual deployment only: transfer configuration and checkpoint over pinned SSH."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from .cloud import context, gh, restore
from .config import Settings

CONFIG_NAMES = (
    "DELIVERY_ZIP", "MONITOR_STATE_KEY", "RESEND_API_KEY", "ALERT_EMAIL_FROM",
    "ALERT_EMAIL_TO", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_API_KEY_SID",
    "TWILIO_API_KEY_SECRET", "TWILIO_FROM_NUMBER", "TEXTBELT_API_KEY", "ALERT_SMS_TO",
    "SUMMARY_SMS_TO",
)
ROOT = "/opt/costco-restock-checker"
COMPOSE = "sudo -n docker compose -f compose.worker.yml"
STOPPED = (
    "test -z \"$(sudo -n docker ps -q "
    "--filter label=com.docker.compose.project=costco-restock-checker "
    "--filter label=com.docker.compose.service=monitor)\""
)
INSTALL_CONFIG = """
import os, pathlib, sys
p = pathlib.Path('.env')
data = sys.stdin.buffer.read()
if p.exists():
    assert not p.is_symlink() and p.read_bytes() == data
    assert p.stat().st_mode & 0o077 == 0
else:
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
"""


def configuration(environ):
    values = {name: environ.get(name, "") for name in CONFIG_NAMES}
    values.update(
        CHECK_INTERVAL_MINUTES="10", SMS_INCLUDE_URL="false", ENABLE_SCHEDULER="false",
        HEADLESS="false", CHECKER_MAX_ATTEMPTS="2", CHECKER_RETRY_DELAY_SECONDS="10",
        PYTHONUNBUFFERED="1",
    )
    lines = []
    for name, value in values.items():
        if any(c in value for c in "\r\n\0"):
            raise ValueError("Multiline configuration is not supported")
        quoted = value.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"{name}='{quoted}'")
    return ("\n".join(lines) + "\n").encode()


def require_actions_stopped():
    repo = os.environ["GITHUB_REPOSITORY"]
    workflow = gh(f"repos/{repo}/actions/workflows/monitor.yml")
    if workflow["state"] != "disabled_manually":
        raise ValueError("Disable monitor.yml before importing state")
    runs = gh(f"repos/{repo}/actions/workflows/monitor.yml/runs?per_page=100")
    if any(run["status"] != "completed" for run in runs["workflow_runs"]):
        raise ValueError("Wait for all monitor runs to finish; do not cancel sends")


def await_network(host):
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=15) as response:
        source = str(ipaddress.IPv4Address(response.read(64).decode().strip()))
    print(f"Temporary deployment SSH source: {source}/32", flush=True)
    print("Waiting up to ten minutes for narrowly scoped TCP/22 access.", flush=True)
    for _ in range(60):
        try:
            with socket.create_connection((host, 22), timeout=3):
                return
        except OSError:
            time.sleep(7)
    raise TimeoutError("Deployment network access not available")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["verify", "import"])
    parser.add_argument("--await-network", action="store_true")
    args = parser.parse_args()
    host = str(ipaddress.IPv4Address(os.environ["ORACLE_SSH_HOST"]))
    commit = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected an immutable deployment commit")
    _, identity = context(Settings(enable_scheduler=False))
    data = configuration(os.environ)
    if args.await_network:
        await_network(host)
    with tempfile.TemporaryDirectory() as directory:
        key = Path(directory) / "key"
        hosts = Path(directory) / "known_hosts"
        for path, name in ((key, "ORACLE_SSH_PRIVATE_KEY"), (hosts, "ORACLE_SSH_KNOWN_HOSTS")):
            value = os.environ[name]
            if not value.strip():
                raise ValueError("Missing SSH deployment configuration")
            path.touch(mode=0o600)
            path.write_text(value.rstrip() + "\n")

        def remote(command, payload=None, timeout=120):
            result = subprocess.run(
                ["ssh", "-i", str(key), "-o", f"UserKnownHostsFile={hosts}",
                 "-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes",
                 "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", f"ubuntu@{host}",
                 f"set -eu; cd {ROOT}; {STOPPED}; {command}"],
                input=payload, capture_output=True, timeout=timeout,
            )
            if result.returncode:
                # SSH/Compose diagnostics can contain private configuration; never echo them.
                raise RuntimeError("Remote operation failed; inspect the host privately")
            return result.stdout.decode()

        if args.phase == "verify":
            remote(
                "test \"$(git remote get-url origin)\" = "
                "https://github.com/robertwchen/costco_restock_checker.git; "
                "git diff --quiet; git diff --cached --quiet; "
                f"git fetch origin main; git checkout --detach {commit}"
            )
            remote("python3 -c " + shlex.quote(INSTALL_CONFIG), data)
            remote(f"{COMPOSE} build", timeout=900)
            output = remote(
                f"{COMPOSE} run --rm -T monitor xvfb-run -a python -m app.worker --verify",
                timeout=360,
            )
            print(output)
            print("Access verified without sends or production state changes. Worker remains stopped.")
        else:
            require_actions_stopped()
            state = restore(identity, "monitor")
            remote(f"test \"$(git rev-parse HEAD)\" = {commit}")
            remote("python3 -c " + shlex.quote(INSTALL_CONFIG), data)
            require_actions_stopped()
            remote(
                f"{COMPOSE} run --rm -T monitor python -m app.worker --import-state",
                json.dumps(state).encode(),
            )
            print("Exact checkpoint imported. Both production schedulers remain stopped.")
            print("Run bounded control verification before explicitly starting the worker.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Handoff stopped safely ({type(exc).__name__}); no automatic worker start.")
        raise SystemExit(1) from None
