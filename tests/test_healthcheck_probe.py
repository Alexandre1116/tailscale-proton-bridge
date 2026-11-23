import os
import re
import subprocess
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_healthcheck_tries_fallback_url_when_first_fails(tmp_path):
    healthcheck = (PROJECT_ROOT / "healthcheck.sh").read_text(encoding="utf-8")

    calls = tmp_path / "calls.txt"
    # curl stub: fail the first URL, succeed on the fallback.
    curl = tmp_path / "curl"
    curl.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        echo "$@" >> {calls}
        for arg in "$@"; do
            if [ "$arg" = "https://api.ipify.org" ]; then exit 22; fi
        done
        exit 0
    """))
    ip = tmp_path / "ip"
    ip.write_text("#!/bin/sh\necho '5: protonvpn: <POINTOPOINT> mtu 1420 state UNKNOWN' 'inet 10.2.0.2/32 scope global protonvpn'\nexit 0\n")
    tailscale = tmp_path / "tailscale"
    tailscale.write_text("#!/bin/sh\nexit 0\n")
    for tool in (curl, ip, tailscale):
        tool.chmod(0o755)

    status = tmp_path / "status.json"
    script = tmp_path / "healthcheck.sh"
    script.write_text(healthcheck.replace("/var/run/bridge-status/status.json", str(status)))
    status.write_text('{"connected": true}\n')

    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    result = subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, text=True)

    text = calls.read_text()
    used_time = re.search(r"--max-time (\d+)", text)
    assert result.returncode == 0, result.stderr
    assert "https://api.ipify.org" in text
    assert "https://checkip.amazonaws.com" in text
    assert used_time and int(used_time.group(1)) <= 6


def test_compose_healthcheck_timeout_covers_the_probe_budget():
    import re as regex

    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    probe = (PROJECT_ROOT / "healthcheck.sh").read_text(encoding="utf-8")

    probe_seconds = int(regex.search(r"PROBE_TIMEOUT_SECONDS:-([0-9]+)", probe).group(1))
    default_urls = len(regex.findall(r"https://[^\s\"}]+", probe))
    bridge = compose[compose.index("\n  vpn-tailscale-bridge:"):compose.index("\n  webui:")]
    timeout_seconds = int(regex.search(r"healthcheck:.*?timeout: ([0-9]+)s", bridge, regex.S).group(1))

    assert default_urls >= 3
    assert timeout_seconds >= probe_seconds * default_urls
