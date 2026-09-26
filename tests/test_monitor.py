import json
from datetime import UTC, datetime, timedelta

import pytest

from app import cloud
from app.checker import Availability, CheckOutcome
from app.config import Settings
from app.monitor_state import initialize, load, observe, opaque, reserve, save, summary_period

KEY = "test-key-only-not-a-credential-00000000"
IDENTITY = opaque(KEY, "watch")
NOW = datetime(2026, 9, 26, 12, 30, tzinfo=UTC)


def state():
    return initialize(IDENTITY, NOW)


def settings():
    return Settings(
        _env_file=None,
        delivery_zip="00000",
        textbelt_api_key="mock",
        alert_sms_to="+15550000001",
        summary_sms_to="+15550000002",
        resend_api_key="mock",
        alert_email_from="a@example.com",
        alert_email_to="b@example.com",
    )


def checker(status):
    return lambda settings, animals: {
        a: CheckOutcome(Availability(status), "mock") for a in animals
    }


@pytest.mark.parametrize(
    "readings,count",
    [
        (["in_stock", "in_stock"], 1),
        (["out_of_stock", "blocked_or_unknown", "in_stock"], 1),
        (["in_stock", "blocked_or_unknown", "in_stock"], 1),
        (["in_stock", "out_of_stock", "in_stock"], 2),
    ],
)
def test_transitions(readings, count):
    s = state()
    assert sum(observe(s, r) for r in readings) == count


def test_restart_and_partial_failure(tmp_path):
    s = state()
    _, jobs = cloud.prepare(settings(), s, KEY, "monitor", checker("in_stock"))
    calls = []

    def sender(settings, channel, *args):
        calls.append(channel)
        return "accepted" if channel == "email" else "rejected"

    cloud.send_jobs(settings(), s, jobs, sender=sender)
    path = tmp_path / "state.json"
    save(path, s)
    restored = load(path, IDENTITY)
    _, jobs = cloud.prepare(settings(), restored, KEY, "monitor", checker("in_stock"))
    assert [j["channel"] for j in jobs] == ["textbelt"]
    cloud.send_jobs(settings(), restored, jobs, sender=lambda *a: "accepted")
    assert cloud.prepare(settings(), restored, KEY, "monitor", checker("in_stock"))[1] == []
    assert "example.com" not in path.read_text() and "+1555" not in path.read_text()


def test_pending_intent_never_automatically_retries(tmp_path):
    s = state()
    _, jobs = cloud.prepare(settings(), s, KEY, "monitor", checker("in_stock"))
    assert len(jobs) == 2
    path = tmp_path / "state.json"
    save(path, s)
    assert (
        cloud.prepare(settings(), load(path, IDENTITY), KEY, "monitor", checker("in_stock"))[1]
        == []
    )


@pytest.mark.parametrize("contents", [None, "{}", "broken", "[]", '{"schema": 999}'])
def test_missing_or_corrupt_never_initializes(tmp_path, contents):
    path = tmp_path / "state.json"
    if contents is not None:
        path.write_text(contents)
    with pytest.raises((ValueError, TypeError)):
        load(path, IDENTITY)


def test_wrong_identity(tmp_path):
    path = tmp_path / "state.json"
    save(path, state())
    with pytest.raises(ValueError):
        load(path, "different")


def test_summaries_every_two_and_fourteen_mornings_with_dst():
    s = state()
    assert summary_period(s, NOW + timedelta(days=1), 2) is None
    assert summary_period(s, NOW + timedelta(days=2, minutes=-1), 2) is None
    assert summary_period(s, NOW + timedelta(days=2), 2) == "2026-09-28"
    assert summary_period(s, NOW + timedelta(days=14), 14) == "2026-10-10"
    s["anchor"] = "2026-10-31"
    assert summary_period(s, datetime(2026, 11, 2, 13, 29, tzinfo=UTC), 2) is None
    assert summary_period(s, datetime(2026, 11, 2, 13, 30, tzinfo=UTC), 2) == "2026-11-02"


def test_test_control_does_not_change_production_and_suppresses_second_run():
    s = state()

    def mixed(settings, animals):
        return {
            a: CheckOutcome(
                Availability.OUT_OF_STOCK if a == "Capybara" else Availability.IN_STOCK, "mock"
            )
            for a in animals
        }

    _, jobs = cloud.prepare(settings(), s, KEY, "test", mixed)
    assert s["confirmed"] is None and s["epoch"] == 0
    assert len(jobs) == 2
    assert all(
        "TEST — Dog availability check — not a Capybara restock." in j["message"]["body"]
        for j in jobs
    )
    cloud.send_jobs(settings(), s, jobs, sender=lambda *a: "accepted")
    assert cloud.prepare(settings(), s, KEY, "test", mixed)[1] == []
    assert s["test_sends"] == {"email": 1, "sms": 1}


def test_no_available_control_no_sends():
    s = state()
    assert cloud.prepare(settings(), s, KEY, "test", checker("out_of_stock"))[1] == []


def test_global_test_cap_includes_rejections():
    s = state()
    for _ in range(4):
        _, jobs = cloud.prepare(settings(), s, KEY, "test", checker("in_stock"))
        cloud.send_jobs(settings(), s, jobs, sender=lambda *a: "rejected")
    assert s["test_sends"] == {"email": 2, "sms": 2}


def test_reservation_prevents_overlap():
    s = state()
    args = dict(slot="restock", event_id="a" * 64, recipient_key="b" * 64)
    assert reserve(s, **args)
    assert not reserve(s, **args)


def test_unknown_preserves_confirmed_and_never_sends():
    s = state()
    cloud.prepare(settings(), s, KEY, "monitor", checker("in_stock"))
    assert cloud.prepare(settings(), s, KEY, "monitor", checker("blocked_or_unknown"))[1] == []
    assert s["confirmed"] == "in_stock"


def test_restore_uses_partial_failure_intent(monkeypatch, tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("state.json", json.dumps(state()))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "2")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(cloud, "STATE", tmp_path / "state.json")

    def fake(path, binary=False):
        if "workflows" in path:
            return {"workflow_runs": [{"id": 1, "status": "completed", "conclusion": "failure"}]}
        if path.endswith("/artifacts"):
            return {
                "artifacts": [
                    {"name": "monitor-intent", "id": 10, "expired": False, "size_in_bytes": 1000}
                ]
            }
        return buf.getvalue()

    monkeypatch.setattr(cloud, "gh", fake)
    assert cloud.restore(IDENTITY, "monitor") == state()


def test_ambiguous_send_is_not_retried():
    s = state()
    _, jobs = cloud.prepare(settings(), s, KEY, "monitor", checker("in_stock"))
    cloud.send_jobs(settings(), s, jobs, sender=lambda *a: "ambiguous")
    assert cloud.prepare(settings(), s, KEY, "monitor", checker("in_stock"))[1] == []


@pytest.mark.parametrize(
    "artifacts",
    [
        [],
        [{"name": "monitor-final", "id": 10, "expired": True, "size_in_bytes": 100}],
        [{"name": "monitor-final", "id": 10, "expired": False, "size_in_bytes": 40000}],
    ],
)
def test_restore_never_falls_back_to_older_run(monkeypatch, artifacts):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "3")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    calls = []

    def fake(path, binary=False):
        calls.append(path)
        if "workflows" in path:
            return {
                "workflow_runs": [
                    {"id": 2, "status": "completed"},
                    {"id": 1, "status": "completed"},
                ]
            }
        return {"artifacts": artifacts}

    monkeypatch.setattr(cloud, "gh", fake)
    with pytest.raises(ValueError):
        cloud.restore(IDENTITY, "initialize")
    assert not any("/runs/1/" in p for p in calls)


def test_out_of_order_run_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "2")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(
        cloud, "gh", lambda path: {"workflow_runs": [{"id": 3, "status": "completed"}]}
    )
    with pytest.raises(ValueError):
        cloud.restore(IDENTITY, "monitor")


def test_twilio_and_textbelt_not_both_selected():
    s = settings().model_copy(
        update={
            "twilio_account_sid": "mock",
            "twilio_auth_token": "mock",
            "twilio_from_number": "+15550000003",
        }
    )
    assert [channel for channel, _ in cloud.channels(s)] == ["email", "sms"]


def test_secondary_only_on_fourteen_day_summary(monkeypatch):
    monkeypatch.setattr(cloud, "utcnow", lambda: NOW + timedelta(days=14))
    s = state()
    _, jobs = cloud.prepare(settings(), s, KEY, "monitor", checker("out_of_stock"))
    secondary = [job for job in jobs if job["slot"] == "secondary"]
    assert len(secondary) == 1 and secondary[0]["recipient"] == "+15550000002"
    assert all(j["recipient"] != "+15550000002" for j in jobs if j["slot"] != "secondary")
    assert all(
        a in secondary[0]["message"]["body"] for a in ["Capybara", "Dog", "Red Panda", "Raccoon"]
    )


def test_worker_checkpoint_table_preserves_history(session):
    from app.models import CheckResult, MonitorCheckpoint, Product

    product = Product(name="Mattress", url="https://example.com")
    session.add(product)
    session.flush()
    session.add(CheckResult(product_id=product.id, status="out_of_stock"))
    session.add(MonitorCheckpoint(id=IDENTITY, state=state()))
    session.commit()
    assert session.get(MonitorCheckpoint, IDENTITY).state == state()
    assert session.query(CheckResult).count() == 1
