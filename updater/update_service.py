#!/usr/bin/env python3
"""GitHub release watcher and controlled compose updater.

The service is deliberately separate from the Web UI. The UI can only place a
signed-in request in the shared state directory; this process owns the Docker
socket and performs updates only after validating the release tag and checking
for a clean Git worktree.
"""
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


WORKSPACE = os.environ.get("UPDATE_WORKSPACE", "/workspace")
STATE_PATH = os.environ.get("UPDATE_STATE_PATH", "/state/state.json")
REQUEST_PATH = os.environ.get("UPDATE_REQUEST_PATH", "/state/request.json")
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "Alexandre1116/tailscale-proton-bridge")
POLL_INTERVAL = max(int(os.environ.get("UPDATE_POLL_INTERVAL", "30")), 5)
TAG_RE = re.compile(r"^v?[0-9]+(?:\.[0-9]+){2}(?:[-.][0-9A-Za-z.-]+)?$")
VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?$")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_dotenv():
    values = {}
    path = os.path.join(WORKSPACE, ".env")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def version_key(value):
    match = VERSION_RE.fullmatch(value or "")
    if not match:
        return None
    major, minor, patch, prerelease = match.groups()
    # A stable release is newer than any prerelease of the same version.
    pre_key = (1, "") if not prerelease else (0, prerelease)
    return (int(major), int(minor or 0), int(patch or 0), pre_key)


def fetch_latest_release():
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", REPOSITORY):
        raise RuntimeError("GITHUB_REPOSITORY must have the owner/name format")
    request = Request(
        f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "tailscale-proton-bridge-updater",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            release = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"GitHub check failed: {exc}") from exc
    tag = release.get("tag_name") if isinstance(release, dict) else None
    if not tag or not TAG_RE.fullmatch(tag):
        raise RuntimeError("GitHub returned an invalid release tag")
    return {
        "latest_version": tag,
        "latest_name": release.get("name") or tag,
        "release_url": release.get("html_url"),
        "published_at": release.get("published_at"),
    }


def default_state():
    env = read_dotenv()
    return {
        "current_version": env.get("APP_VERSION", os.environ.get("APP_VERSION", "v0.1.0")),
        "latest_version": None,
        "latest_name": None,
        "release_url": None,
        "published_at": None,
        "last_checked": None,
        "last_auto_check": None,
        "update_available": False,
        "status": "starting",
        "error": None,
        "last_update": None,
        "request_id": None,
    }


def read_state():
    state = default_state()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as stream:
            saved = json.load(stream)
        if isinstance(saved, dict):
            state.update(saved)
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return state


def write_state(state):
    state_dir = os.path.dirname(STATE_PATH) or "."
    os.makedirs(state_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".state.", dir=state_dir, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, STATE_PATH)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_release(state, auto_check=False):
    state["status"] = "checking"
    state["error"] = None
    write_state(state)
    try:
        release = fetch_latest_release()
        current = version_key(state.get("current_version"))
        latest = version_key(release["latest_version"])
        if current is None or latest is None:
            raise RuntimeError("Cannot compare the installed and released versions")
        state.update(release)
        state["last_checked"] = now_iso()
        state["update_available"] = latest > current
        state["status"] = "update_available" if state["update_available"] else "up_to_date"
        if auto_check:
            state["last_auto_check"] = datetime.now().astimezone().strftime("%Y-%m-%d")
        state["error"] = None
        write_state(state)
    except RuntimeError as exc:
        state["status"] = "error"
        state["error"] = str(exc)[:500]
        state["last_checked"] = now_iso()
        write_state(state)


def run(command):
    return subprocess.run(command, cwd=WORKSPACE, text=True, capture_output=True, timeout=1800)


def update_env_version(tag):
    path = os.path.join(WORKSPACE, ".env")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            lines = stream.readlines()
    except OSError:
        lines = []
    replaced = False
    output = []
    for line in lines:
        if line.strip().startswith("APP_VERSION="):
            output.append(f"APP_VERSION={tag}\n")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"APP_VERSION={tag}\n")
    with open(path, "w", encoding="utf-8") as stream:
        stream.writelines(output)


def install_release(state, requested_tag=None):
    tag = requested_tag or state.get("latest_version")
    if not tag or not TAG_RE.fullmatch(tag):
        raise RuntimeError("No valid release is available to install")
    latest = state.get("latest_version")
    if requested_tag and latest and requested_tag != latest:
        raise RuntimeError("The requested release is no longer the latest release")

    state["status"] = "installing"
    state["error"] = None
    write_state(state)
    status = run(["git", "status", "--porcelain"])
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or "Could not inspect the Git worktree")
    if status.stdout.strip():
        raise RuntimeError("Update blocked: the Git worktree contains local changes")

    for command in (
        ["git", "fetch", "--tags", "origin"],
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
        ["git", "checkout", "--force", f"tags/{tag}"],
    ):
        result = run(command)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Command failed: {' '.join(command)}")

    update_env_version(tag)
    compose = [
        "docker", "compose", "--project-directory", WORKSPACE,
        "--env-file", os.path.join(WORKSPACE, ".env"),
        "-f", os.path.join(WORKSPACE, "docker-compose.yml"),
        "up", "-d", "--build", "vpn-tailscale-bridge", "webui",
    ]
    result = run(compose)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Docker Compose update failed")
    state["current_version"] = tag
    state["update_available"] = False
    state["status"] = "updated"
    state["last_update"] = now_iso()
    state["error"] = None
    write_state(state)


def handle_request(state, request_data):
    request_id = request_data.get("request_id")
    if not request_id or request_id == state.get("request_id"):
        return
    action = request_data.get("action")
    state["request_id"] = request_id
    try:
        if action == "check":
            check_release(state)
        elif action == "install":
            if not state.get("latest_version"):
                check_release(state)
            install_release(state, request_data.get("tag"))
        else:
            raise RuntimeError("Unsupported updater request")
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        state["status"] = "error"
        state["error"] = str(exc)[:500]
        write_state(state)


def scheduled_check_due(state, env):
    if env.get("AUTO_UPDATE_ENABLED") != "1":
        return False
    hour = env.get("AUTO_UPDATE_HOUR", "03:00")
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", hour):
        return False
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    return datetime.now().astimezone().strftime("%H:%M") == hour and state.get("last_auto_check") != today


def main():
    state = read_state()
    write_state(state)
    # Populate the banner soon after startup; subsequent automatic checks are daily.
    check_release(state)
    while True:
        state = read_state()
        try:
            with open(REQUEST_PATH, "r", encoding="utf-8") as stream:
                request_data = json.load(stream)
            if isinstance(request_data, dict):
                handle_request(state, request_data)
        except (OSError, json.JSONDecodeError, TypeError):
            pass

        state = read_state()
        if scheduled_check_due(state, read_dotenv()):
            check_release(state, auto_check=True)
            state = read_state()
            if state.get("update_available"):
                try:
                    install_release(state)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    state["status"] = "error"
                    state["error"] = str(exc)[:500]
                    write_state(state)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
