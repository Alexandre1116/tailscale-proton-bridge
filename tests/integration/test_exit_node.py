import os
import shutil
import subprocess
import time

import pytest


CLIENT = os.environ.get("TS_CLIENT_CONTAINER")
BRIDGE = os.environ.get("TS_BRIDGE_CONTAINER", "protonvpn-tailscale-bridge")
EXIT_NODE = os.environ.get("TS_EXIT_NODE")
PUBLIC_IP_URL = os.environ.get("PUBLIC_IP_URL", "https://api.ipify.org")

pytestmark = pytest.mark.integration


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


def docker_exec(container, command, check=True):
    return run("docker", "exec", container, "sh", "-c", command, check=check)


@pytest.fixture(scope="module", autouse=True)
def integration_environment():
    if not shutil.which("docker") or not CLIENT or not EXIT_NODE:
        pytest.skip(
            "set TS_CLIENT_CONTAINER and TS_EXIT_NODE to run the Tailscale integration tests"
        )
    run("docker", "inspect", CLIENT)
    run("docker", "inspect", BRIDGE)
    docker_exec(CLIENT, "tailscale status")
    docker_exec(BRIDGE, "tailscale status")
    yield
    # Never leave the client using the test exit node after the test run.
    docker_exec(CLIENT, "tailscale set --exit-node=", check=False)


def public_ip(container):
    result = docker_exec(container, f"curl -4fsS --max-time 10 {PUBLIC_IP_URL}")
    return result.stdout.strip()


def select_exit_node():
    docker_exec(CLIENT, f"tailscale set --exit-node={EXIT_NODE} --exit-node-allow-lan-access=false")
    for _ in range(12):
        try:
            if public_ip(CLIENT) == public_ip(BRIDGE):
                return
        except subprocess.CalledProcessError:
            pass
        time.sleep(2)
    pytest.fail("Tailscale client did not use the bridge public IP")


def test_client_uses_proton_exit_node():
    select_exit_node()
    assert public_ip(CLIENT) == public_ip(BRIDGE)


def test_vpn_down_blocks_forwarded_traffic():
    select_exit_node()
    interface = os.environ.get("TS_BRIDGE_VPN_INTERFACE", "protonvpn")
    docker_exec(BRIDGE, f"ip link set dev {interface} down")
    try:
        result = docker_exec(
            CLIENT,
            f"curl -4fsS --connect-timeout 2 --max-time 5 {PUBLIC_IP_URL}",
            check=False,
        )
        assert result.returncode != 0, "traffic escaped while the VPN interface was down"
    finally:
        run("docker", "restart", BRIDGE, check=False)
