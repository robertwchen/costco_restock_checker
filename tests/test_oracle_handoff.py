import io
import json
import subprocess
import sys

import pytest
from dotenv import dotenv_values

from app import oracle_handoff as handoff


def test_configuration_preserves_secret_characters_and_empty_channels():
    values = {name: "" for name in handoff.CONFIG_NAMES}
    values.update(TEXTBELT_API_KEY="example'\\$quoted # value", ALERT_SMS_TO="+15550000001")
    result = dotenv_values(stream=io.StringIO(handoff.configuration(values).decode()), interpolate=False)
    assert all(result[name] == value for name, value in values.items())
    assert result["CHECK_INTERVAL_MINUTES"] == "10"
    assert result["ENABLE_SCHEDULER"] == "false"
    assert result["SMS_INCLUDE_URL"] == "false"


@pytest.mark.parametrize("value", ["a\nb", "a\rb", "a\0b"])
def test_configuration_rejects_multiline_values(value):
    with pytest.raises(ValueError):
        handoff.configuration({"TEXTBELT_API_KEY": value})


@pytest.mark.parametrize("state,status", [("active", "completed"), ("disabled_manually", "queued")])
def test_import_requires_disabled_and_finished_monitor(monkeypatch, state, status):
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setattr(handoff, "gh", lambda path: (
        {"workflow_runs": [{"status": status}]} if "runs?" in path else {"state": state}
    ))
    with pytest.raises(ValueError):
        handoff.require_actions_stopped()


def test_remote_config_refuses_overwrite_and_keeps_private_mode(tmp_path):
    command = [sys.executable, "-c", handoff.INSTALL_CONFIG]
    for _ in range(2):
        subprocess.run(command, cwd=tmp_path, input=b"NAME='example'\n", check=True)
    assert (tmp_path / ".env").stat().st_mode & 0o077 == 0
    result = subprocess.run(command, cwd=tmp_path, input=b"NAME='different'\n", capture_output=True)
    assert result.returncode != 0
    assert (tmp_path / ".env").read_bytes() == b"NAME='example'\n"


def test_network_wait_is_bounded_and_logs_only_public_source(monkeypatch, capsys):
    monkeypatch.setattr(handoff.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"192.0.2.2\n"))
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append(True)
        raise TimeoutError

    monkeypatch.setattr(handoff.socket, "create_connection", blocked)
    monkeypatch.setattr(handoff.time, "sleep", lambda seconds: None)
    with pytest.raises(TimeoutError):
        handoff.await_network("192.0.2.1")
    assert len(attempts) == 60
    assert "192.0.2.2/32" in capsys.readouterr().out


@pytest.mark.parametrize("phase", ["verify", "import"])
def test_handoff_uses_pinned_ssh_and_never_starts_worker(monkeypatch, phase):
    for name, value in {
        "ORACLE_SSH_HOST": "192.0.2.1", "GITHUB_SHA": "a" * 40,
        "ORACLE_SSH_PRIVATE_KEY": "mock private key", "ORACLE_SSH_KNOWN_HOSTS": "mock host key",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["handoff", phase])
    monkeypatch.setattr(handoff, "context", lambda settings: ("key", "identity"))
    monkeypatch.setattr(handoff, "require_actions_stopped", lambda: None)
    original = {"confirmed": "out_of_stock", "test_sends": {"sms": 2}}
    monkeypatch.setattr(handoff, "restore", lambda identity, mode: original)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(handoff.subprocess, "run", run)
    handoff.main()
    for command, kwargs in calls:
        assert "StrictHostKeyChecking=yes" in command
        assert "BatchMode=yes" in command
        assert handoff.STOPPED in command[-1]
        assert "up -d" not in command[-1]
        assert "--initialize" not in command[-1]
        assert kwargs["capture_output"]
        assert "mock private key" not in str(command)
    if phase == "import":
        assert json.loads(calls[-1][1]["input"]) == original
    else:
        assert "--verify" in calls[-1][0][-1]
