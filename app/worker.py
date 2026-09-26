"""Optional single persistent worker; do not run alongside Actions."""

from __future__ import annotations

import argparse
import fcntl
import json
import time
from pathlib import Path

from sqlalchemy import select

from .cloud import context, prepare, report, send_jobs
from .config import Settings
from .database import init_db, session_scope
from .models import AlertLog, CheckResult, MonitorCheckpoint
from .monitor_state import initialize, utcnow, validate
from .seed import seed_capybara


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--initialize", action="store_true", help="Explicitly initialize a new persistent watch"
    )
    args = parser.parse_args()
    settings = Settings(enable_scheduler=False)
    key, identity = context(settings)
    if not settings.database_url.startswith("sqlite:///"):
        raise ValueError("Worker requires persistent SQLite")
    path = Path(settings.database_url.removeprefix("sqlite:///"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        init_db()
        with session_scope() as session:
            checkpoint = session.scalar(select(MonitorCheckpoint))
            if checkpoint is None:
                if not args.initialize:
                    raise ValueError(
                        "Missing checkpoint: explicitly initialize a new watch or restore backup"
                    )
                checkpoint = MonitorCheckpoint(id=identity, state=initialize(identity, utcnow()))
                session.add(checkpoint)
            validate(checkpoint.state, identity)
            seed_capybara(session)
        if args.initialize:
            print("Persistent watch initialized; start worker without --initialize")
            return
        while True:
            with session_scope() as session:
                checkpoint = session.get(MonitorCheckpoint, identity)
                state = validate(json.loads(json.dumps(checkpoint.state)), identity)
                product = seed_capybara(session)
                if product.active:
                    results, jobs = prepare(settings, state, key, "monitor")

                    def persist(value):
                        checkpoint.state = json.loads(json.dumps(value))
                        session.add(checkpoint)
                        session.commit()

                    persist(state)  # Durable intent before any network send.
                    result = results["Capybara"]
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
                path.with_suffix(".heartbeat").touch()
            time.sleep(settings.check_interval_minutes * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"Worker stopped safely ({type(exc).__name__}); inspect configuration and persistent state"
        )
        raise SystemExit(1) from None
