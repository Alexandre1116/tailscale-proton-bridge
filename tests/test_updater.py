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


def test_install_requires_matching_host_project_path(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("UPDATE_HOST_PROJECT_PATH=\n", encoding="utf-8")
    monkeypatch.setattr(update_service, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(update_service, "STATE_PATH", str(tmp_path / "state.json"))
    commands = []
    monkeypatch.setattr(update_service, "run", lambda command: commands.append(command))

    with pytest.raises(RuntimeError, match="UPDATE_HOST_PROJECT_PATH"):
        update_service.install_release({"latest_version": "v0.2.0"})

    assert commands == []


def test_install_rebuilds_the_updater_service(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(f"UPDATE_HOST_PROJECT_PATH={tmp_path}\n", encoding="utf-8")
    monkeypatch.setattr(update_service, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(update_service, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(update_service, "update_env_version", lambda tag: None)
    commands = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command):
        commands.append(command)
        return Result()

    monkeypatch.setattr(update_service, "run", fake_run)
    state = {"latest_version": "v0.2.0"}

    update_service.install_release(state)

    compose_command = commands[-1]
    assert compose_command[-3:] == ["vpn-tailscale-bridge", "webui", "updater"]
