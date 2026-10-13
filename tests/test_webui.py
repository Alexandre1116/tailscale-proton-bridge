import base64
import io
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


def test_assets_are_not_served_from_a_stale_browser_cache(client, monkeypatch):
    monkeypatch.setattr(webapp, "APP_VERSION", "v9.9.9")

    page = client.get("/", headers=auth_header(), follow_redirects=True)
    script = client.get("/static/app.js")

    assert "app.js?v=v9.9.9" in page.get_data(as_text=True)
    assert "style.css?v=v9.9.9" in page.get_data(as_text=True)
    assert script.headers["Cache-Control"] == "no-cache"
    assert client.get("/api/updates", headers=auth_header()).headers["Cache-Control"] == "no-cache"


def test_hidden_attribute_beats_class_display_rules(client):
    # .update-banner sets display: flex, which would keep a hidden banner visible.
    css = client.get("/static/style.css").get_data(as_text=True)

    assert "[hidden] { display: none !important; }" in css


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
        json.dumps({"vpn_tx_bytes": 1234, "exit_node_clients": 3, "traffic_updated": "traffic-time"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "STATUS_FILE", str(status_path))
    monkeypatch.setattr(webapp, "TRAFFIC_FILE", str(traffic_path))

    response = client.get("/api/status", headers=auth_header())

    assert response.status_code == 200
    data = response.get_json()
    assert data["connected"] is True
    assert data["vpn_tx_bytes"] == "1234"
    assert data["exit_node_clients"] == 3
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


def test_update_status_reads_shared_state(client, tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({
            "current_version": "v0.1.0",
            "latest_version": "v0.2.0",
            "update_available": True,
            "status": "update_available",
            "release_url": "https://github.com/Alexandre1116/tailscale-proton-bridge/releases/tag/v0.2.0",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "UPDATE_STATE_PATH", str(state_path))
    monkeypatch.setattr(webapp, "APP_VERSION", "v0.1.0")

    response = client.get("/api/updates", headers=auth_header())

    assert response.status_code == 200
    assert response.get_json()["update_available"] is True
    assert response.get_json()["latest_version"] == "v0.2.0"


def test_update_status_ignores_stale_installed_version(client, tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({
            "current_version": "v0.1.3",
            "latest_version": "v0.1.4",
            "update_available": True,
            "status": "update_available",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "UPDATE_STATE_PATH", str(state_path))
    monkeypatch.setattr(webapp, "APP_VERSION", "v0.1.4")

    data = client.get("/api/updates", headers=auth_header()).get_json()

    assert data["current_version"] == "v0.1.4"
    assert data["update_available"] is False
    assert data["status"] == "up_to_date"


@pytest.mark.parametrize("latest", ["v0.1.4", "0.1.4", "v0.1.3"])
def test_update_status_compares_versions_not_strings(client, tmp_path, monkeypatch, latest):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"latest_version": latest, "update_available": True, "status": "update_available"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "UPDATE_STATE_PATH", str(state_path))
    monkeypatch.setattr(webapp, "APP_VERSION", "v0.1.5")

    data = client.get("/api/updates", headers=auth_header()).get_json()

    assert data["update_available"] is False
    assert data["status"] == "up_to_date"


def test_update_check_requires_csrf_and_queues_request(client, tmp_path, monkeypatch):
    request_path = tmp_path / "request.json"
    monkeypatch.setattr(webapp, "UPDATE_REQUEST_PATH", str(request_path))
    with client.session_transaction() as session:
        session["csrf"] = "known-token"

    rejected = client.post("/api/updates/check", headers=auth_header())
    assert rejected.status_code == 400

    response = client.post(
        "/api/updates/check",
        headers=auth_header(),
        data={"csrf_token": "known-token"},
    )

    assert response.status_code == 202
    assert json.loads(request_path.read_text(encoding="utf-8"))["action"] == "check"


def test_update_install_rejects_invalid_tag(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "UPDATE_REQUEST_PATH", str(tmp_path / "request.json"))
    with client.session_transaction() as session:
        session["csrf"] = "known-token"

    response = client.post(
        "/api/updates/install",
        headers=auth_header(),
        data={"csrf_token": "known-token", "tag": "main; rm -rf /"},
    )

    assert response.status_code == 400


def test_save_profile_writes_metadata_configs_and_private_secrets(client, tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles"
    monkeypatch.setattr(webapp, "PROFILES_DIR", str(profiles_dir))
    with client.session_transaction() as session:
        session["csrf"] = "known-token"

    response = client.post(
        "/profile/save",
        headers=auth_header(),
        data={
            "csrf_token": "known-token",
            "profile_id": "pt-lisbon",
            "profile_name": "Portugal",
            "profile_vpn_type": "wireguard",
            "profile_hostname": "protonvpn-portugal",
            "profile_ts_extra_args": "",
            "profile_ts_authkey": "tskey-auth-example",
            "profile_protonvpn_user": "",
            "profile_protonvpn_password": "",
            "profile_wg_conf": (io.BytesIO(b"[Interface]\nPrivateKey = secret\n"), "portugal.conf"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    profile_dir = profiles_dir / "pt-lisbon"
    assert (profile_dir / "profile.env").read_text(encoding="utf-8") == (
        "ID=pt-lisbon\nNAME=Portugal\nVPN_TYPE=wireguard\n"
        "TS_HOSTNAME=protonvpn-portugal\nTS_EXTRA_ARGS=\n"
    )
    assert (profile_dir / "wireguard.conf").read_bytes() == b"[Interface]\nPrivateKey = secret\n"
    assert (profile_dir / "ts_authkey").read_text(encoding="utf-8") == "tskey-auth-example\n"


def test_list_profiles_hides_secret_contents(client, tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles" / "pt"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "profile.env").write_text(
        "ID=pt\nNAME=Portugal\nVPN_TYPE=wireguard\nTS_HOSTNAME=protonvpn-pt\n",
        encoding="utf-8",
    )
    (profiles_dir / "ts_authkey").write_text("secret-key", encoding="utf-8")
    monkeypatch.setattr(webapp, "PROFILES_DIR", str(profiles_dir.parent))
    monkeypatch.setattr(webapp, "STATUS_FILE", str(tmp_path / "missing-status.json"))

    profiles = webapp.list_profiles()

    assert profiles[0]["name"] == "Portugal"
    assert profiles[0]["ts_authkey_set"] is True
    assert "secret-key" not in json.dumps(profiles)


def test_index_renders_multi_profile_status(client, tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles" / "pt"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "profile.env").write_text(
        "ID=pt\nNAME=Portugal\nVPN_TYPE=wireguard\nTS_HOSTNAME=protonvpn-pt\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "PROFILES_DIR", str(profiles_dir.parent))
    monkeypatch.setattr(webapp, "STATUS_FILE", str(tmp_path / "missing-status.json"))

    response = client.get("/?skip_wizard=1", headers=auth_header())

    assert response.status_code == 200
    assert b"Portugal" in response.data
    assert b"Exit node status" in response.data
    assert b"Devices using this exit node" in response.data
