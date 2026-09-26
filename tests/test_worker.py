import json
import sys

import pytest

from app import cloud, worker
from app.checker import Availability, CheckOutcome
from app.models import AlertLog, CheckResult, MonitorCheckpoint, Product


class StopWorker(Exception):
    pass


def test_worker_startup_check_restart_and_mattress_preservation(session, monkeypatch):
    key, identity = "mock-key-for-unit-test-only", "a" * 64
    monkeypatch.setattr(worker, "context", lambda settings: (key, identity))
    monkeypatch.setenv("TEXTBELT_API_KEY", "mock")
    monkeypatch.setenv("ALERT_SMS_TO", "+15550000001")
    monkeypatch.setenv("CHECK_INTERVAL_MINUTES", "10")
    monkeypatch.setattr(sys, "argv", ["worker", "--initialize"])
    session.add(Product(name="Mattress", url="https://example.com", item_number="1847132"))
    session.commit()
    worker.main()
    monkeypatch.setattr(sys, "argv", ["worker"])

    def checker(settings, animals):
        return {animal: CheckOutcome(Availability.IN_STOCK, "mock evidence") for animal in animals}

    monkeypatch.setattr(
        worker,
        "prepare",
        lambda settings, state, key, mode: cloud.prepare(settings, state, key, mode, checker),
    )
    sends = []

    def sender(*args):
        sends.append(True)
        return "accepted"

    monkeypatch.setattr(
        worker,
        "send_jobs",
        lambda settings, state, jobs, checkpoint: cloud.send_jobs(
            settings, state, jobs, sender, checkpoint
        ),
    )

    def stop(delay):
        assert 590 <= delay <= 600
        raise StopWorker

    monkeypatch.setattr(worker.time, "sleep", stop)
    for _ in range(2):
        with pytest.raises(StopWorker):
            worker.main()
    session.expire_all()
    assert sends == [True]
    assert session.query(Product).count() == 2
    assert session.query(CheckResult).count() == 2
    assert session.query(AlertLog).count() == 1
    checkpoint = session.get(MonitorCheckpoint, identity)
    assert checkpoint.state["confirmed"] == "in_stock"
    assert "accepted" in json.dumps(checkpoint.state)


def test_worker_refuses_uninitialized_missing_state(monkeypatch):
    monkeypatch.setattr(worker, "context", lambda settings: ("mock-key", "a" * 64))
    monkeypatch.setattr(sys, "argv", ["worker"])
    with pytest.raises(ValueError, match="Missing checkpoint"):
        worker.main()
