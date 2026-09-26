"""Optional single persistent worker; do not run alongside Actions."""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
from pathlib import Path

from sqlalchemy import select

from .checker import Availability
from .cloud import context, prepare, report, send_jobs
from .config import Settings
from .database import init_db, session_scope
from .models import AlertLog, CheckResult, MonitorCheckpoint
from .monitor_state import initialize, utcnow, validate
from .plush import check_animals
from .seed import seed_capybara


def main():
    parser = argparse.ArgumentParser()
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument(
        "--initialize", action="store_true", help="Explicitly initialize a new persistent watch"
    )
    setup.add_argument(
        "--import-state", action="store_true", help="Import an existing checkpoint from stdin"
    )
    setup.add_argument(
        "--verify", action="store_true", help="Check cloud access without changing state or sending"
    )
    parser.add_argument("--once", action="store_true", help="Run one check, persist, and exit")
    parser.add_argument("--test", action="store_true", help="Use the isolated, bounded control test")
    args = parser.parse_args()
    if args.test and not args.once:
        parser.error("--test requires --once")
    if (args.initialize or args.import_state or args.verify) and (args.once or args.test):
        parser.error("Setup/verification cannot be combined with check modes")
    settings = Settings(enable_scheduler=False)
    if args.verify:
        results = check_animals(settings, ["Capybara", "Dog", "Red Panda"])
        report(results, [], {})
        if results["Capybara"].availability not in {
            Availability.IN_STOCK,
            Availability.OUT_OF_STOCK,
        }:
            raise ValueError("Cloud access could not establish Capybara availability")
        return
    key, identity = context(settings)
    imported = None
    if args.import_state:
        raw = sys.stdin.read(16385)
        if len(raw.encode()) > 16384:
            raise ValueError("Checkpoint oversized")
        imported = validate(json.loads(raw), identity)
    if not settings.database_url.startswith("sqlite:///"):
        raise ValueError("Worker requires persistent SQLite")
    path = Path(settings.database_url.removeprefix("sqlite:///"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        init_db()
        with session_scope() as session:
            checkpoint = session.scalar(select(MonitorCheckpoint))
            if checkpoint is not None and (args.initialize or args.import_state):
                raise ValueError("Existing checkpoint must not be overwritten or reinitialized")
            if checkpoint is None:
                if not args.initialize and not args.import_state:
                    raise ValueError(
                        "Missing checkpoint: explicitly initialize a new watch or restore backup"
                    )
                checkpoint = MonitorCheckpoint(
                    id=identity,
                    state=imported if args.import_state else initialize(identity, utcnow()),
                )
                session.add(checkpoint)
            validate(checkpoint.state, identity)
            seed_capybara(session)
        if args.initialize or args.import_state:
            print("Persistent checkpoint saved; no checks or notifications performed")
            return
        while True:
            started = time.monotonic()
            with session_scope() as session:
                checkpoint = session.get(MonitorCheckpoint, identity)
                state = validate(json.loads(json.dumps(checkpoint.state)), identity)
                product = seed_capybara(session)
                if product.active:
                    results, jobs = prepare(settings, state, key, "test" if args.test else "monitor")

                    def persist(value):
                        checkpoint.state = json.loads(json.dumps(value))
                        session.add(checkpoint)
                        session.commit()

                    persist(state)  # Durable intent before any network send.
                    result = results["Capybara"]
                    if not args.test:
                        session.add(
                            CheckResult(
                                product_id=product.id,
                                status=result.status,
                                detail=f"{result.detail}; {result.checked_at}"
                                + (f"; {result.price}" if result.price else ""),
                            )
                        )
                    session.commit()
                    send_jobs(settings, state, jobs, checkpoint=persist)
                    for job in jobs:
                        status = state["events"][job["slot"]]["deliveries"][job["recipient_key"]][
                            "status"
                        ]
                        if not args.test:
                            session.add(
                                AlertLog(
                                    product_id=product.id,
                                    channel=job["channel"],
                                    target=job["recipient"],
                                    success=status == "accepted",
                                    message=status,
                                )
                            )
                    report(results, jobs, state)
                    outcomes = [
                        state["events"][job["slot"]]["deliveries"][job["recipient_key"]]["status"]
                        for job in jobs
                    ]
                    print("Notification outcomes: " + (", ".join(outcomes) or "no attempts"))
                path.with_suffix(".heartbeat").touch()
            if args.once:
                return
            time.sleep(max(1, started + settings.check_interval_minutes * 60 - time.monotonic()))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"Worker stopped safely ({type(exc).__name__}); inspect configuration and persistent state"
        )
        raise SystemExit(1) from None
