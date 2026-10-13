from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_forwarding_is_configured_before_tailscaled_starts():
    entrypoint = (PROJECT_ROOT / "entrypoint.sh").read_text(encoding="utf-8")

    assert entrypoint.index("configure_forwarding") < entrypoint.index("tailscaled --")
    assert "net.ipv4.ip_forward=1" in entrypoint
    assert "net.ipv6.conf.all.forwarding=1" in entrypoint
    assert "net.ipv6.conf.all.forwarding=0" not in entrypoint


def test_tailscale_underlay_bypasses_vpn_only_for_its_udp_port():
    entrypoint = (PROJECT_ROOT / "entrypoint.sh").read_text(encoding="utf-8")

    # The original route must be captured before the VPN replaces it.
    assert entrypoint.index("UNDERLAY_ROUTE=$(ip route show default") < entrypoint.index("wg-quick up")
    assert '--port="$TS_PORT"' in entrypoint
    assert 'ipproto udp sport "$TS_PORT" lookup "$UNDERLAY_TABLE"' in entrypoint
    assert entrypoint.index("configure_tailscale_underlay\n") < entrypoint.index("tailscale up")
    # Under set -e a failing rule must not abort before 'tailscale up'.
    assert '! ip rule add priority "$UNDERLAY_RULE_PRIORITY"' in entrypoint


def test_compose_does_not_force_an_apparmor_profile_by_default():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "${BRIDGE_SECURITY_OPT:-no-new-privileges:true}" in compose
    assert "apparmor=${" not in compose


def test_read_only_updater_has_a_writable_docker_config():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    updater = compose[compose.index("\n  updater:"):compose.index("\nvolumes:")]

    assert "read_only: true" in updater
    assert "tmpfs:\n      - /tmp" in updater
    assert "DOCKER_CONFIG=/tmp/.docker" in updater


def test_compose_exposes_both_forwarding_sysctls():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "net.ipv4.ip_forward=1" in compose
    assert "net.ipv6.conf.all.forwarding=1" in compose
    assert "net.ipv6.conf.default.forwarding=1" in compose


def test_compose_enables_multi_profile_mode_by_default():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "MULTI_PROFILE_ENABLED=${MULTI_PROFILE_ENABLED:-1}" in compose


def test_multi_profile_dispatch_requires_a_usable_vpn_configuration():
    entrypoint = (PROJECT_ROOT / "entrypoint.sh").read_text(encoding="utf-8")

    assert "profile_has_vpn_config()" in entrypoint
    assert 'profile_has_vpn_config "$profile_metadata"' in entrypoint
    assert '"exit_node_clients": ${exit_node_clients}' in entrypoint


def test_multi_profile_authentication_stays_alive_until_the_user_finishes():
    supervisor = (PROJECT_ROOT / "multi-entrypoint.sh").read_text(encoding="utf-8")

    assert 'PROFILE_UP_PIDS[$i]="$up_pid"' in supervisor
    assert "process_pending_profiles" in supervisor
    assert 'kill -TERM "$up_pid"' not in supervisor


def test_multi_profile_kill_switch_precedes_exit_node_advertisement():
    supervisor = (PROJECT_ROOT / "multi-entrypoint.sh").read_text(encoding="utf-8")
    advertise_exit_node = 'tailscale --socket="${TS_SOCKETS[$i]}" up --authkey='

    assert supervisor.index("iptables -P FORWARD DROP") < supervisor.index(advertise_exit_node)
    assert supervisor.index("ip6tables -P FORWARD DROP") < supervisor.index(advertise_exit_node)
