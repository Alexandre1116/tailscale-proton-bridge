import os
import stat
from datetime import datetime
from pathlib import Path

import pytest

from updater import update_service


def test_version_key_orders_stable_after_prerelease():
    assert update_service.version_key("v0.2.0") > update_service.version_key("v0.1.9")
    assert update_service.version_key("v1.0.0") > update_service.version_key("v1.0.0-beta.9")
    assert update_service.version_key("invalid") is None


def test_read_dotenv_reads_update_settings(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "AUTO_UPDATE_ENABLED=1\nAUTO_UPDATE_HOUR=04:30\nAPP_VERSION=v0.1.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(update_service, "WORKSPACE", str(tmp_path))

    values = update_service.read_dotenv()

    assert values["AUTO_UPDATE_ENABLED"] == "1"
    assert values["AUTO_UPDATE_HOUR"] == "04:30"


def test_scheduled_check_requires_enabled_setting(monkeypatch):
    class FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 17, 4, 30, tzinfo=tz)

    monkeypatch.setattr(update_service, "datetime", FakeDateTime)
    assert update_service.scheduled_check_due({}, {"AUTO_UPDATE_ENABLED": "0", "AUTO_UPDATE_HOUR": "04:30"}) is False
    assert update_service.scheduled_check_due({}, {"AUTO_UPDATE_ENABLED": "1", "AUTO_UPDATE_HOUR": "04:30"}) is True
    assert update_service.scheduled_check_due({"last_auto_check": "2026-09-17"}, {"AUTO_UPDATE_ENABLED": "1", "AUTO_UPDATE_HOUR": "04:30"}) is False


@pytest.mark.skipif(not hasattr(os, "chown"), reason="POSIX permissions only")
def test_state_is_readable_by_the_web_ui(monkeypatch, tmp_path):
    state_path = tmp_path / "state" / "state.json"
    monkeypatch.setattr(update_service, "STATE_PATH", str(state_path))
    monkeypatch.setattr(update_service, "STATE_UID", os.getuid())
    monkeypatch.setattr(update_service, "STATE_GID", os.getgid())

    update_service.prepare_state_dir()
    update_service.write_state({"status": "up_to_date"})

    assert stat.S_IMODE(os.stat(state_path.parent).st_mode) == 0o770
    assert stat.S_IMODE(os.stat(state_path).st_mode) == 0o644


def test_prepare_state_dir_survives_chown_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(update_service, "STATE_PATH", str(tmp_path / "state" / "state.json"))

    def deny(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(update_service.os, "chown", deny, raising=False)

    update_service.prepare_state_dir()

    assert (tmp_path / "state").is_dir()


def test_web_ui_image_uses_the_updater_state_ids():
    dockerfile = (Path(__file__).resolve().parents[1] / "webui" / "Dockerfile").read_text(encoding="utf-8")

    assert f"addgroup -S -g {update_service.STATE_GID} webui" in dockerfile
    assert f"adduser -S -u {update_service.STATE_UID} -G webui webui" in dockerfile


def test_installed_version_comes_from_env_not_saved_state(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("APP_VERSION=v0.1.4\n", encoding="utf-8")
    (tmp_path / "state.json").write_text(
        '{"current_version": "v0.1.3", "latest_version": "v0.1.4", '
        '"update_available": true, "status": "update_available"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(update_service, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(update_service, "STATE_PATH", str(tmp_path / "state.json"))

    state = update_service.read_state()

    assert state["current_version"] == "v0.1.4"
    assert state["update_available"] is False
    assert state["status"] == "up_to_date"


def test_install_requires_matching_host_project_path(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("UPDATE_HOST_PROJECT_PATH=\n", encoding="utf-8")
    monkeypatch.setattr(update_service, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(update_service, "STATE_PATH", str(tmp_path / "state.json"))
    commands = []
    monkeypatch.setattr(update_service, "run", lambda command: commands.append(command))

    with pytest.raises(RuntimeError, match="UPDATE_HOST_PROJECT_PATH"):
        update_service.install_release({"latest_version": "v0.2.0"})

    assert commands == []


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def fake_docker(commands, helper=None, logs=""):
    """Fake run(): helper is None (absent) or the "running exit_code" inspect output."""
    def fake_run(command):
        commands.append(command)
        if command[:2] == ["docker", "inspect"] and command[-1] == update_service.UPDATE_HELPER:
            return Result(1) if helper is None else Result(stdout=helper)
        if command[:2] == ["docker", "inspect"]:
            return Result(stdout="sha256:updater")
        if command[:2] == ["docker", "logs"]:
            return Result(stderr=logs)
        return Result()

    return fake_run


def prepare_install(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(f"UPDATE_HOST_PROJECT_PATH={tmp_path}\n", encoding="utf-8")
    monkeypatch.setattr(update_service, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(update_service, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(update_service, "update_env_version", lambda tag: None)


def test_install_runs_compose_in_a_detached_helper(monkeypatch, tmp_path):
    prepare_install(monkeypatch, tmp_path)
    commands = []
    monkeypatch.setattr(update_service, "run", fake_docker(commands))
    state = {"latest_version": "v0.2.0"}

    update_service.install_release(state)

    helper_command = commands[-1]
    # Compose must not run inside the updater, which it recreates.
    assert not any(command[:2] == ["docker", "compose"] for command in commands)
    assert helper_command[:4] == ["docker", "run", "--detach", "--name"]
    assert "sha256:updater" in helper_command
    assert "DOCKER_CONFIG=/tmp/.docker" in helper_command
    assert helper_command[-3:] == ["vpn-tailscale-bridge", "webui", "updater"]
    assert state["status"] == "installing"


def test_install_refuses_while_a_helper_is_running(monkeypatch, tmp_path):
    prepare_install(monkeypatch, tmp_path)
    commands = []
    monkeypatch.setattr(update_service, "run", fake_docker(commands, helper="true 0"))

    with pytest.raises(RuntimeError, match="already running"):
        update_service.install_release({"latest_version": "v0.2.0"})

    assert not any(command[:2] == ["docker", "run"] for command in commands)


def test_collect_update_records_success_and_removes_helper(monkeypatch, tmp_path):
    monkeypatch.setattr(update_service, "STATE_PATH", str(tmp_path / "state.json"))
    commands = []
    monkeypatch.setattr(update_service, "run", fake_docker(commands, helper="false 0"))
    state = {"status": "installing", "update_available": True}

    assert update_service.collect_update(state) is False

    assert state["status"] == "updated"
    assert state["update_available"] is False
    assert commands[-1] == ["docker", "rm", "--force", update_service.UPDATE_HELPER]


def test_collect_update_reports_compose_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(update_service, "STATE_PATH", str(tmp_path / "state.json"))
    commands = []
    monkeypatch.setattr(update_service, "run", fake_docker(commands, helper="false 1", logs="build failed"))
    state = {"status": "installing"}

    assert update_service.collect_update(state) is False

    assert state["status"] == "error"
    assert state["error"] == "build failed"


def test_collect_update_waits_for_a_running_helper(monkeypatch, tmp_path):
    monkeypatch.setattr(update_service, "STATE_PATH", str(tmp_path / "state.json"))
    commands = []
    monkeypatch.setattr(update_service, "run", fake_docker(commands, helper="true 0"))
    state = {"status": "installing"}

    assert update_service.collect_update(state) is True

    assert state["status"] == "installing"
    assert not any(command[:2] == ["docker", "rm"] for command in commands)
