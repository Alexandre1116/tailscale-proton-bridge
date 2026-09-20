from datetime import datetime

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


def test_install_rebuilds_the_updater_service(monkeypatch, tmp_path):
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
