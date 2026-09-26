import io
import json
import sys

import pytest

from app import cloud, worker
from app.checker import Availability, CheckOutcome
from app.models import AlertLog, CheckResult, MonitorCheckpoint, Product
from app.monitor_state import initialize, utcnow


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


def test_import_preserves_baseline_outcomes_budgets_and_restart(session, monkeypatch):
    key, identity = "mock-migration-key", "a" * 64
    monkeypatch.setattr(worker, "context", lambda settings: (key, identity))
    monkeypatch.setenv("TEXTBELT_API_KEY", "mock")
    monkeypatch.setenv("ALERT_SMS_TO", "+15550000001")
    original = initialize(identity, utcnow())

    def checker(settings, animals):
        return {a: CheckOutcome(Availability.IN_STOCK, "mock") for a in animals}

    settings = worker.Settings()
    _, jobs = cloud.prepare(settings, original, key, "monitor", checker)
    cloud.send_jobs(settings, original, jobs, sender=lambda *args: "accepted")
    original["test_sends"] = {"email": 2, "sms": 2}
    session.add(Product(name="Mattress", url="https://example.com"))
    session.commit()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(original)))
    monkeypatch.setattr(sys, "argv", ["worker", "--import-state"])
    worker.main()
    assert session.get(MonitorCheckpoint, identity).state == original
    monkeypatch.setattr(sys, "argv", ["worker", "--once"])
    monkeypatch.setattr(
        worker, "prepare",
        lambda settings, state, key, mode: cloud.prepare(settings, state, key, mode, checker),
    )

    def no_sends(settings, state, jobs, checkpoint):
        assert jobs == []

    monkeypatch.setattr(worker, "send_jobs", no_sends)
    worker.main()
    worker.main()
    session.expire_all()
    assert session.get(MonitorCheckpoint, identity).state == original
    assert session.query(Product).count() == 2
    assert session.query(CheckResult).count() == 2


@pytest.mark.parametrize("action", ["--initialize", "--import-state"])
def test_setup_cannot_overwrite_existing_checkpoint(session, monkeypatch, action):
    identity = "a" * 64
    original = initialize(identity, utcnow())
    session.add(MonitorCheckpoint(id=identity, state=original))
    session.commit()
    monkeypatch.setattr(worker, "context", lambda settings: ("mock-key", identity))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(original)))
    monkeypatch.setattr(sys, "argv", ["worker", action])
    with pytest.raises(ValueError, match="must not be overwritten"):
        worker.main()
    session.expire_all()
    assert session.get(MonitorCheckpoint, identity).state == original


@pytest.mark.parametrize("raw", ["", "{}", "broken", "x" * 16385])
def test_import_rejects_missing_corrupt_oversized_state(session, monkeypatch, raw):
    monkeypatch.setattr(worker, "context", lambda settings: ("mock-key", "a" * 64))
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    monkeypatch.setattr(sys, "argv", ["worker", "--import-state"])
    with pytest.raises(ValueError):
        worker.main()
    assert session.query(MonitorCheckpoint).count() == 0
    assert session.query(Product).count() == 0


def test_import_rejects_wrong_identity(session, monkeypatch):
    monkeypatch.setattr(worker, "context", lambda settings: ("mock-key", "a" * 64))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(initialize("b" * 64, utcnow()))))
    monkeypatch.setattr(sys, "argv", ["worker", "--import-state"])
    with pytest.raises(ValueError, match="identity mismatch"):
        worker.main()
    assert session.query(MonitorCheckpoint).count() == 0


@pytest.mark.parametrize("status", [Availability.IN_STOCK, Availability.BLOCKED_OR_UNKNOWN])
def test_verify_never_initializes_or_sends(session, monkeypatch, status):
    def checker(settings, animals):
        assert animals == ["Capybara", "Dog", "Red Panda"]
        return {a: CheckOutcome(status, "mock") for a in animals}

    monkeypatch.setattr(worker, "check_animals", checker)
    monkeypatch.setattr(sys, "argv", ["worker", "--verify"])
    if status == Availability.BLOCKED_OR_UNKNOWN:
        with pytest.raises(ValueError, match="Cloud access"):
            worker.main()
    else:
        worker.main()
    assert session.query(MonitorCheckpoint).count() == 0
    assert session.query(CheckResult).count() == 0
    assert session.query(AlertLog).count() == 0


def test_control_mode_preserves_production_and_suppresses_second_invocation(session, monkeypatch):
    key, identity = "mock-key", "a" * 64
    monkeypatch.setattr(worker, "context", lambda settings: (key, identity))
    monkeypatch.setenv("TEXTBELT_API_KEY", "mock")
    monkeypatch.setenv("ALERT_SMS_TO", "+15550000001")
    original = initialize(identity, utcnow())
    original["confirmed"] = "out_of_stock"
    session.add(MonitorCheckpoint(id=identity, state=original))
    session.commit()

    def checker(settings, animals):
        return {a: CheckOutcome(Availability.IN_STOCK, "mock") for a in animals}

    monkeypatch.setattr(
        worker, "prepare",
        lambda settings, state, key, mode: cloud.prepare(settings, state, key, mode, checker),
    )
    sends = []

    def sender(settings, channel, recipient, message, event_id):
        sends.append(message.subject)
        return "accepted"

    monkeypatch.setattr(
        worker, "send_jobs",
        lambda settings, state, jobs, checkpoint: cloud.send_jobs(
            settings, state, jobs, sender, checkpoint
        ),
    )
    monkeypatch.setattr(sys, "argv", ["worker", "--once", "--test"])
    worker.main()
    worker.main()
    session.expire_all()
    saved = session.get(MonitorCheckpoint, identity).state
    assert saved["confirmed"] == "out_of_stock" and saved["epoch"] == 0
    assert saved["test_sends"] == {"email": 0, "sms": 1}
    assert len(sends) == 1 and "not a Capybara restock" in sends[0]
    assert session.query(CheckResult).count() == 0
    assert session.query(AlertLog).count() == 0


def test_concurrent_worker_fails_before_check(monkeypatch):
    monkeypatch.setattr(worker, "context", lambda settings: ("mock-key", "a" * 64))
    monkeypatch.setattr(sys, "argv", ["worker", "--once"])
    path = worker.Path(worker.Settings().database_url.removeprefix("sqlite:///"))
    with open(path.with_suffix(".lock"), "w") as lock:
        worker.fcntl.flock(lock, worker.fcntl.LOCK_EX | worker.fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            worker.main()
