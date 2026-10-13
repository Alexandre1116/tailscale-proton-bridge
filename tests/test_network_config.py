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


def test_compose_exposes_both_forwarding_sysctls():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "net.ipv4.ip_forward=1" in compose
    assert "net.ipv6.conf.all.forwarding=1" in compose
    assert "net.ipv6.conf.default.forwarding=1" in compose
