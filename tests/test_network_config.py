from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_forwarding_is_configured_before_tailscaled_starts():
    entrypoint = (PROJECT_ROOT / "entrypoint.sh").read_text(encoding="utf-8")

    assert entrypoint.index("configure_forwarding") < entrypoint.index("tailscaled --")
    assert "net.ipv4.ip_forward=1" in entrypoint
    assert "net.ipv6.conf.all.forwarding=1" in entrypoint
    assert "net.ipv6.conf.all.forwarding=0" not in entrypoint


def test_compose_exposes_both_forwarding_sysctls():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "net.ipv4.ip_forward=1" in compose
    assert "net.ipv6.conf.all.forwarding=1" in compose
    assert "net.ipv6.conf.default.forwarding=1" in compose
