import base64
import ipaddress
import json
import os

import pytest

import webui.app as webapp


def auth_header(username="admin", password="test-password"):
    value = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(webapp, "ENV_PATH", str(env_path))
    monkeypatch.setattr(webapp, "WEBUI_USERNAME", "admin")
    monkeypatch.setattr(webapp, "WEBUI_PASSWORD", "test-password")
    monkeypatch.setattr(webapp, "ALLOWED_NETWORKS", ())
    return webapp.app.test_client()


def test_authentication_required(client):
    response = client.get("/api/status")
    assert response.status_code == 401

    response = client.get("/api/status", headers=auth_header())
    assert response.status_code == 200
    assert response.get_json()["connected"] is False


def test_invalid_credentials_are_rejected(client):
    response = client.get("/api/status", headers=auth_header(password="wrong"))
    assert response.status_code == 401


def test_status_ignores_stale_traffic_when_health_is_disconnected(client, tmp_path, monkeypatch):
    status_path = tmp_path / "status.json"
    traffic_path = tmp_path / "traffic.json"
    status_path.write_text(json.dumps({"connected": False, "last_updated": "health-time"}), encoding="utf-8")
    traffic_path.write_text(
        json.dumps({"vpn_tx_bytes": 1234, "traffic_updated": "traffic-time"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "STATUS_FILE", str(status_path))
    monkeypatch.setattr(webapp, "TRAFFIC_FILE", str(traffic_path))

    response = client.get("/api/status", headers=auth_header())

    assert response.status_code == 200
    data = response.get_json()
    assert data["connected"] is False
    assert data["vpn_tx_bytes"] == "0"
    assert data["traffic_updated"] is None


def test_status_keeps_health_state_when_live_traffic_updates_separately(client, tmp_path, monkeypatch):
    status_path = tmp_path / "status.json"
    traffic_path = tmp_path / "traffic.json"
    status_path.write_text(json.dumps({"connected": True, "last_updated": "health-time"}), encoding="utf-8")
    traffic_path.write_text(
        json.dumps({"vpn_tx_bytes": 1234, "traffic_updated": "traffic-time"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "STATUS_FILE", str(status_path))
    monkeypatch.setattr(webapp, "TRAFFIC_FILE", str(traffic_path))

    response = client.get("/api/status", headers=auth_header())

    assert response.status_code == 200
    data = response.get_json()
    assert data["connected"] is True
    assert data["vpn_tx_bytes"] == "1234"
    assert data["traffic_updated"] == "traffic-time"


def test_status_handles_non_object_traffic_json(client, tmp_path, monkeypatch):
    status_path = tmp_path / "status.json"
    traffic_path = tmp_path / "traffic.json"
    status_path.write_text(json.dumps({"connected": True}), encoding="utf-8")
    traffic_path.write_text("null", encoding="utf-8")
    monkeypatch.setattr(webapp, "STATUS_FILE", str(status_path))
    monkeypatch.setattr(webapp, "TRAFFIC_FILE", str(traffic_path))

    response = client.get("/api/status", headers=auth_header())

    assert response.status_code == 200
    assert response.get_json()["error"] == "could not read traffic status"


def test_tailnet_access_mode_rejects_non_tailnet_clients(client, monkeypatch):
    monkeypatch.setattr(webapp, "ALLOWED_NETWORKS", (ipaddress.ip_network("100.64.0.0/10"),))

    response = client.get("/api/status", headers=auth_header(), environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 403

    response = client.get("/api/status", headers=auth_header(), environ_base={"REMOTE_ADDR": "100.64.0.20"})
    assert response.status_code == 200


def test_save_env_preserves_comments_and_unrelated_values(client, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("# keep this comment\nKEEP_ME=yes\nTS_HOSTNAME=old-name\n", encoding="utf-8")

    with client.session_transaction() as session:
        session["csrf"] = "known-token"

    response = client.post(
        "/save",
        headers=auth_header(),
        data={
            "csrf_token": "known-token",
            "vpn_type": "wireguard",
            "ts_hostname": "new-name",
            "ts_extra_args": "",
            "protonvpn_user": "",
            "webui_username": "admin",
            "webui_protocol": "http",
            "webui_access_mode": "all",
            "webui_bind_address": "0.0.0.0",
        },
    )

    assert response.status_code == 302
    saved = env_path.read_text(encoding="utf-8")
    assert "# keep this comment\n" in saved
    assert "KEEP_ME=yes\n" in saved
    assert "TS_HOSTNAME=new-name\n" in saved
    if os.name != "nt":
        assert oct(env_path.stat().st_mode & 0o777) == "0o600"


def test_save_rejects_invalid_csrf(client):
    response = client.post(
        "/save",
        headers=auth_header(),
        data={"csrf_token": "wrong", "ts_hostname": "bridge"},
    )
    assert response.status_code == 400
