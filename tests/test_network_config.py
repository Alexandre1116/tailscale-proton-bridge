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
    assert "has_active_profile" in supervisor
    assert 'if [ "${PROFILE_STATES[$i]}" = pending ] && [ -n "${PROFILE_UP_PIDS[$i]:-}" ]; then' in supervisor
    assert 'kill -TERM "$up_pid"' not in supervisor


def test_multi_profile_kill_switch_precedes_exit_node_advertisement():
    supervisor = (PROJECT_ROOT / "multi-entrypoint.sh").read_text(encoding="utf-8")
    advertise_exit_node = 'tailscale --socket="${TS_SOCKETS[$i]}" up --authkey='

    assert supervisor.index("iptables -P FORWARD DROP") < supervisor.index(advertise_exit_node)
    assert supervisor.index("ip6tables -P FORWARD DROP") < supervisor.index(advertise_exit_node)


def test_manual_wireguard_routing_only_when_src_valid_mark_unavailable():
    entrypoint = (PROJECT_ROOT / "entrypoint.sh").read_text(encoding="utf-8")

    # Compose's sysctls can set the value even when /proc/sys is read-only, so
    # the entrypoint must test the current value instead of only the write.
    assert 'SRC_VALID_MARK=$(sysctl -n net.ipv4.conf.all.src_valid_mark' in entrypoint
    assert '[ "$SRC_VALID_MARK" != "1" ] && ! sysctl -q net.ipv4.conf.all.src_valid_mark=1' in entrypoint
    assert entrypoint.index("SRC_VALID_MARK=$(sysctl -n") < entrypoint.index("wg-quick up")


def test_container_dns_has_fallback_resolvers_and_short_timeouts():
    entrypoint = (PROJECT_ROOT / "entrypoint.sh").read_text(encoding="utf-8")

    assert 'DNS_FALLBACK_SERVERS="${DNS_FALLBACK_SERVERS:-1.1.1.1 9.9.9.9}"' in entrypoint
    assert '"options timeout:2 attempts:2"' in entrypoint
    assert "for dns in $DNS_FALLBACK_SERVERS" in entrypoint
    # IPv6 tunnel resolvers would hang when IPv6 is disabled in the namespace.
    assert entrypoint.index('if [ "$IPV6_DISABLED" != "1" ] || [ "${dns#*:}" = "$dns" ]') < entrypoint.index("for dns in $DNS_FALLBACK_SERVERS")


def test_vpn_health_probes_try_several_urls_in_both_entrypoints():
    entrypoint = (PROJECT_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    supervisor = (PROJECT_ROOT / "multi-entrypoint.sh").read_text(encoding="utf-8")
    healthcheck = (PROJECT_ROOT / "healthcheck.sh").read_text(encoding="utf-8")

    for script in (entrypoint, supervisor, healthcheck):
        assert "VPN_HEALTHCHECK_URLS" in script
        assert "checkip.amazonaws.com" in script
    for script in (entrypoint, supervisor):
        assert "for healthcheck_url in $VPN_HEALTHCHECK_URLS" in script or "for url in $VPN_HEALTHCHECK_URLS" in script


def test_multi_profile_local_probes_are_routed_through_the_tunnel():
    supervisor = (PROJECT_ROOT / "multi-entrypoint.sh").read_text(encoding="utf-8")

    # curl --interface binds the tunnel address; without a source-based rule the
    # probe would leave through eth0 and never validate the tunnel.
    assert 'ip rule add from "$tunnel_ip" table "$table" pref "$((3000 + i))"' in supervisor
    assert 'ip rule del pref "$((3000 + i))"' in supervisor
    assert supervisor.index('ip rule add from "$tunnel_ip"') < supervisor.index("PROFILE_STATES[$i]=\"connected\"\n    PROFILE_AUTH_URLS")
