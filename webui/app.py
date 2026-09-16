#!/usr/bin/env python3
"""Configuration Web UI for the Tailscale <-> Proton VPN Bridge.

Lets users edit the project's .env file and upload VPN configuration files
(WireGuard/OpenVPN) without SSH access to the host. It does not control the
bridge container because it has no access to the Docker socket. Restart the
container manually after saving changes.
"""
import hmac
import json
import os
import re
import secrets
import ipaddress
import tempfile
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB é mais do que suficiente para .conf/.ovpn

ENV_PATH = os.environ.get("ENV_PATH", "/data/.env")
VPN_DIR = os.environ.get("VPN_DIR", "/data/vpn")
WG_DIR = os.path.join(VPN_DIR, "wireguard")
OVPN_DIR = os.path.join(VPN_DIR, "openvpn")
STATUS_FILE = os.environ.get("STATUS_FILE", "/var/run/bridge-status/status.json")
TRAFFIC_FILE = os.environ.get("TRAFFIC_FILE", "/var/run/bridge-status/traffic.json")

WEBUI_USERNAME = os.environ.get("WEBUI_USERNAME", "admin")
WEBUI_PASSWORD = os.environ.get("WEBUI_PASSWORD", "")
WEBUI_PASSWORD_FILE = os.environ.get("WEBUI_PASSWORD_FILE", "")
WEBUI_PROTOCOL = os.environ.get("WEBUI_PROTOCOL", "http").lower()
WEBUI_ACCESS_MODE = os.environ.get("WEBUI_ACCESS_MODE", "all").lower()
WEBUI_BIND_ADDRESS = os.environ.get("WEBUI_BIND_ADDRESS", "0.0.0.0")
WEBUI_ALLOWED_CIDRS = os.environ.get("WEBUI_ALLOWED_CIDRS", "").strip()
try:
    STATUS_UPDATE_INTERVAL = int(os.environ.get("STATUS_UPDATE_INTERVAL", "2"))
    if STATUS_UPDATE_INTERVAL < 1:
        raise ValueError
except ValueError as exc:
    raise RuntimeError("STATUS_UPDATE_INTERVAL must be a positive integer") from exc
if WEBUI_ACCESS_MODE == "tailnet" and not WEBUI_ALLOWED_CIDRS:
    WEBUI_ALLOWED_CIDRS = "100.64.0.0/10,fd7a:115c:a1e0::/48"

try:
    ALLOWED_NETWORKS = tuple(
        ipaddress.ip_network(value.strip())
        for value in WEBUI_ALLOWED_CIDRS.split(",")
        if value.strip()
    )
except ValueError as exc:
    raise RuntimeError("WEBUI_ALLOWED_CIDRS contains an invalid network") from exc

if WEBUI_PROTOCOL not in ("http", "https"):
    raise RuntimeError("WEBUI_PROTOCOL must be http or https")
if WEBUI_ACCESS_MODE not in ("all", "tailnet"):
    raise RuntimeError("WEBUI_ACCESS_MODE must be all or tailnet")
if WEBUI_ACCESS_MODE == "tailnet" and WEBUI_BIND_ADDRESS in ("0.0.0.0", "::"):
    raise RuntimeError("WEBUI_BIND_ADDRESS must be the host Tailscale IP in tailnet mode")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=WEBUI_PROTOCOL == "https",
)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'")
    if WEBUI_PROTOCOL == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response

SECRET_ENV_KEYS = {"TS_AUTHKEY", "PROTONVPN_PASSWORD", "WEBUI_PASSWORD"}
ENV_FIELDS = [
    "VPN_TYPE",
    "TS_AUTHKEY",
    "TS_HOSTNAME",
    "TS_EXTRA_ARGS",
    "PROTONVPN_USER",
    "PROTONVPN_PASSWORD",
    "WEBUI_USERNAME",
    "WEBUI_PASSWORD",
    "WEBUI_PORT",
    "WEBUI_PROTOCOL",
    "WEBUI_ACCESS_MODE",
    "WEBUI_BIND_ADDRESS",
    "SETUP_COMPLETE",
]

HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not WEBUI_PASSWORD:
            return "Web UI disabled: set WEBUI_PASSWORD.", 503

        if ALLOWED_NETWORKS:
            try:
                remote_ip = ipaddress.ip_address(request.remote_addr or "")
            except ValueError:
                return "Access denied.", 403
            if not any(remote_ip in network for network in ALLOWED_NETWORKS):
                return "Access denied.", 403

        auth = request.authorization
        valid = (
            auth is not None
            and hmac.compare_digest(auth.username or "", WEBUI_USERNAME)
            and hmac.compare_digest(auth.password or "", WEBUI_PASSWORD)
        )
        if not valid:
            return (
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="Tailscale ProtonVPN Bridge"'},
            )
        return view(*args, **kwargs)

    return wrapped


def load_env():
    values = {}
    if os.path.isdir(ENV_PATH):
        print(
            f"WARNING: {ENV_PATH} is a directory. Create the .env file on the host before mounting it.",
            flush=True,
        )
        return values
    if os.path.isfile(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def save_env(updates):
    """Update or add the keys in updates, preserving comments and other lines."""
    if os.path.isdir(ENV_PATH):
        raise RuntimeError(
            f"{ENV_PATH} is a directory in the container. Make sure the .env file exists on the host."
        )

    lines = []
    if os.path.isfile(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

    seen = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                seen.add(key)
                continue
        new_lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}\n")

    env_dir = os.path.dirname(ENV_PATH) or "."
    os.makedirs(env_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".env.", dir=env_dir, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, ENV_PATH)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def read_status():
    default = {
        "vpn_mode": None,
        "vpn_interface": None,
        "connected": False,
        "tailscale_ip": "",
        "hostname": "",
        "tailscale_rx_bytes": 0,
        "tailscale_tx_bytes": 0,
        "tailscale_rx_packets": 0,
        "tailscale_tx_packets": 0,
        "vpn_rx_bytes": 0,
        "vpn_tx_bytes": 0,
        "vpn_rx_packets": 0,
        "vpn_tx_packets": 0,
        "traffic_updated": None,
        "auth_url": "",
        "last_updated": None,
    }
    if os.path.isfile(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                default.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            default["error"] = "could not read status"

    traffic_keys = {
        "tailscale_rx_bytes",
        "tailscale_tx_bytes",
        "tailscale_rx_packets",
        "tailscale_tx_packets",
        "vpn_rx_bytes",
        "vpn_tx_bytes",
        "vpn_rx_packets",
        "vpn_tx_packets",
        "traffic_updated",
    }
    if default["connected"] and os.path.isfile(TRAFFIC_FILE):
        try:
            with open(TRAFFIC_FILE, "r", encoding="utf-8") as f:
                traffic = json.load(f)
            default.update({key: traffic[key] for key in traffic_keys if key in traffic})
        except (json.JSONDecodeError, OSError):
            default["error"] = "could not read traffic status"

    return default


def save_upload(file_storage, dest_dir, dest_name):
    os.makedirs(dest_dir, exist_ok=True)
    # Ignoramos por completo o nome original do ficheiro (evita path traversal);
    # usamos sempre o nome fixo esperado pelo entrypoint.sh.
    dest_path = os.path.join(dest_dir, dest_name)
    file_storage.save(dest_path)
    try:
        os.chmod(dest_path, 0o600)
    except OSError:
        pass


def files_present_state():
    return {
        "wireguard": os.path.isfile(os.path.join(WG_DIR, "protonvpn.conf")),
        "openvpn_conf": os.path.isfile(os.path.join(OVPN_DIR, "protonvpn.ovpn")),
        "openvpn_creds": os.path.isfile(os.path.join(OVPN_DIR, "credentials.txt")),
    }


def build_context(env_values, files_present):
    csrf_token = secrets.token_hex(16)
    session["csrf"] = csrf_token

    display_values = {}
    for key in ENV_FIELDS:
        if key in SECRET_ENV_KEYS:
            display_values[key] = ""  # nunca reenviar segredos para o browser
        else:
            display_values[key] = env_values.get(key, "")

    secrets_set = {key: bool(env_values.get(key)) for key in SECRET_ENV_KEYS}

    return {
        "env": display_values,
        "secrets_set": secrets_set,
        "files_present": files_present,
        "csrf_token": csrf_token,
        "vpn_type": env_values.get("VPN_TYPE", "auto"),
        "ts_hostname": env_values.get("TS_HOSTNAME", ""),
        "status_update_interval": STATUS_UPDATE_INTERVAL,
    }


def is_first_run(env_values, files_present):
    if env_values.get("SETUP_COMPLETE") == "1":
        return False
    has_vpn_config = files_present["wireguard"] or files_present["openvpn_conf"]
    has_tailscale_setup = bool(env_values.get("TS_AUTHKEY"))
    return not (has_vpn_config or has_tailscale_setup)


@app.route("/healthz", methods=["GET"])
def healthz():
    """Unauthenticated health check endpoint."""
    return jsonify({"status": "healthy"}), 200


@app.route("/", methods=["GET"])
@require_auth
def index():
    env_values = load_env()
    files_present = files_present_state()

    if is_first_run(env_values, files_present) and not request.args.get("skip_wizard"):
        return redirect(url_for("wizard"))

    return render_template("index.html", **build_context(env_values, files_present))


@app.route("/wizard", methods=["GET"])
@require_auth
def wizard():
    env_values = load_env()
    files_present = files_present_state()
    return render_template("wizard.html", **build_context(env_values, files_present))


@app.route("/save", methods=["POST"])
@require_auth
def save():
    is_wizard = request.form.get("setup_complete") == "1"
    error_target = "wizard" if is_wizard else "index"

    token = request.form.get("csrf_token", "")
    if not token or not hmac.compare_digest(token, session.get("csrf", "")):
        abort(400, "Invalid CSRF token. Reload the page and try again.")

    vpn_type = request.form.get("vpn_type", "auto").strip().lower()
    if vpn_type not in ("auto", "wireguard", "openvpn"):
        vpn_type = "auto"

    ts_hostname = request.form.get("ts_hostname", "").strip() or "protonvpn-bridge"
    if not HOSTNAME_RE.match(ts_hostname):
        flash("Invalid device name (TS_HOSTNAME). Use only letters, numbers, and hyphens.", "error")
        return redirect(url_for(error_target))

    updates = {
        "VPN_TYPE": vpn_type,
        "TS_HOSTNAME": ts_hostname,
        "TS_EXTRA_ARGS": request.form.get("ts_extra_args", "").strip(),
        "PROTONVPN_USER": request.form.get("protonvpn_user", "").strip(),
        "WEBUI_USERNAME": request.form.get("webui_username", "").strip() or "admin",
        "WEBUI_PROTOCOL": request.form.get("webui_protocol", "http").strip().lower(),
        "WEBUI_ACCESS_MODE": request.form.get("webui_access_mode", "all").strip().lower(),
        "WEBUI_BIND_ADDRESS": request.form.get("webui_bind_address", "0.0.0.0").strip(),
    }

    if updates["WEBUI_PROTOCOL"] not in ("http", "https"):
        flash("WEBUI_PROTOCOL must be http or https.", "error")
        return redirect(url_for(error_target))
    if updates["WEBUI_ACCESS_MODE"] not in ("all", "tailnet"):
        flash("WEBUI_ACCESS_MODE must be all or tailnet.", "error")
        return redirect(url_for(error_target))
    if updates["WEBUI_ACCESS_MODE"] == "tailnet" and updates["WEBUI_BIND_ADDRESS"] == "0.0.0.0":
        flash("In tailnet mode, set WEBUI_BIND_ADDRESS to the host's Tailscale IP.", "error")
        return redirect(url_for(error_target))

    for key, value in updates.items():
        if "\n" in value or "\r" in value:
            flash(f"Invalid value for {key}.", "error")
            return redirect(url_for(error_target))

    if is_wizard:
        updates["SETUP_COMPLETE"] = "1"

    # Campos sensíveis: só substituímos o valor gravado se o utilizador escreveu algo novo.
    ts_authkey = request.form.get("ts_authkey", "").strip()
    if ts_authkey:
        updates["TS_AUTHKEY"] = ts_authkey

    protonvpn_password = request.form.get("protonvpn_password", "").strip()
    if protonvpn_password:
        updates["PROTONVPN_PASSWORD"] = protonvpn_password

    webui_password = request.form.get("webui_password", "").strip()
    if webui_password:
        if WEBUI_PASSWORD_FILE:
            flash("The Web UI password comes from WEBUI_PASSWORD_FILE. Change that file on the host.", "error")
            return redirect(url_for(error_target))
        updates["WEBUI_PASSWORD"] = webui_password

    for key, value in updates.items():
        if "\n" in value or "\r" in value:
            flash(f"Invalid value for {key}.", "error")
            return redirect(url_for(error_target))

    # Uploads de ficheiros de configuração
    wg_conf = request.files.get("wg_conf")
    if wg_conf and wg_conf.filename:
        save_upload(wg_conf, WG_DIR, "protonvpn.conf")
        flash("WireGuard file (protonvpn.conf) uploaded.", "success")

    ovpn_conf = request.files.get("ovpn_conf")
    if ovpn_conf and ovpn_conf.filename:
        save_upload(ovpn_conf, OVPN_DIR, "protonvpn.ovpn")
        flash("OpenVPN file (protonvpn.ovpn) uploaded.", "success")

    ovpn_creds = request.files.get("ovpn_creds")
    if ovpn_creds and ovpn_creds.filename:
        save_upload(ovpn_creds, OVPN_DIR, "credentials.txt")
        flash("OpenVPN credentials file uploaded.", "success")

    save_env(updates)
    if is_wizard:
        flash("Initial setup is complete. Run 'docker compose up -d --build' to apply it.", "success")
    else:
        flash("Configuration saved. Restart both containers to apply the changes "
              "(docker compose up -d --build vpn-tailscale-bridge webui).", "success")
    return redirect(url_for("index", skip_wizard=1))


@app.route("/api/status", methods=["GET"])
@require_auth
def api_status():
    return jsonify(read_status())


if __name__ == "__main__":
    port = int(os.environ.get("WEBUI_INTERNAL_PORT", "8080"))
    from waitress import serve

    serve(app, host="0.0.0.0", port=port)
