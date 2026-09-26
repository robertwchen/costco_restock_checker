"""Small public-safe checkpoint; fail closed on loss or identity changes."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .services import is_restock

STATUSES = {"in_stock", "out_of_stock", "blocked_or_unknown"}
OUTCOMES = {"pending", "accepted", "rejected", "ambiguous"}
SLOTS = {"restock", "summary", "secondary", "test"}


def opaque(key: str, value: str) -> str:
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


def initialize(identity: str, now: datetime) -> dict:
    return {"schema": 1, "identity": identity, "confirmed": None, "epoch": 0,
            "anchor": now.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
            "events": {}, "test_sends": {"email": 0, "sms": 0}}


def validate(state: dict, identity: str) -> dict:
    if not isinstance(state, dict) or set(state) != {"schema", "identity", "confirmed", "epoch", "anchor", "events", "test_sends"}:
        raise ValueError("State schema invalid; notification operation stopped")
    if state["schema"] != 1 or state["identity"] != identity:
        raise ValueError("State identity mismatch; notification operation stopped")
    if state["confirmed"] not in {None, "in_stock", "out_of_stock"} or type(state["epoch"]) is not int or not 0 <= state["epoch"] < 1000000:
        raise ValueError("Invalid confirmed state")
    date.fromisoformat(state["anchor"])
    events = state["events"]
    if not isinstance(events, dict) or not set(events) <= SLOTS:
        raise ValueError("Invalid notification events")
    for event in events.values():
        if not isinstance(event, dict) or set(event) != {"id", "deliveries"} or not isinstance(event["id"], str) or len(event["id"]) != 64:
            raise ValueError("Invalid event")
        if not isinstance(event["deliveries"], dict) or len(event["deliveries"]) > 10:
            raise ValueError("Invalid deliveries")
        for key, delivery in event["deliveries"].items():
            if len(key) != 64 or set(delivery) != {"status", "attempts"} or delivery["status"] not in OUTCOMES or type(delivery["attempts"]) is not int or not 1 <= delivery["attempts"] <= 3:
                raise ValueError("Invalid delivery outcome")
    if not isinstance(state["test_sends"], dict) or set(state["test_sends"]) != {"email", "sms"} or any(type(x) is not int or not 0 <= x <= 2 for x in state["test_sends"].values()):
        raise ValueError("Invalid test budget")
    return state


def load(path: Path, identity: str) -> dict:
    if not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("Checkpoint missing or oversized; explicit recovery required")
    return validate(json.loads(path.read_text()), identity)


def save(path: Path, state: dict) -> None:
    validate(state, state["identity"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, sort_keys=True))
    temporary.replace(path)


def observe(state: dict, status: str) -> bool:
    if status not in STATUSES:
        raise ValueError("Invalid reading")
    if status == "blocked_or_unknown":
        return False
    restock = is_restock(state["confirmed"], status)
    if restock:
        state["epoch"] += 1
    state["confirmed"] = status
    return restock


def summary_period(state: dict, now: datetime, days: int) -> str | None:
    local = now.astimezone(ZoneInfo('America/New_York'))
    elapsed = (local.date() - date.fromisoformat(state['anchor'])).days
    if elapsed < days or elapsed % days or local.time() < time(8, 30) or local.time() >= time(12):
        return None
    return local.date().isoformat()


def reserve(state: dict, *, slot: str, event_id: str, recipient_key: str, test_channel=None) -> bool:
    event = state['events'].get(slot)
    if event is None or event['id'] != event_id:
        event = state['events'][slot] = {'id': event_id, 'deliveries': {}}
    previous = event['deliveries'].get(recipient_key)
    if previous and (previous['status'] != 'rejected' or previous['attempts'] >= 3):
        return False
    if test_channel:
        if state['test_sends'][test_channel] >= 2:
            return False
        state['test_sends'][test_channel] += 1
    event['deliveries'][recipient_key] = {'status': 'pending', 'attempts': 1 + (previous['attempts'] if previous else 0)}
    return True


def utcnow():
    return datetime.now(UTC)
