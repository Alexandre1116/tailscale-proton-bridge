#!/bin/sh
set -eu

# Same probe list the entrypoints use: one overloaded endpoint must not make
# the container look unhealthy while the tunnel is fine. The total probe
# budget is split across every configured URL so Docker (timeout 20s in
# compose) never kills the script before the fallbacks are tried. Keep
# HEALTHCHECK_BUDGET_SECONDS below the Compose healthcheck timeout.
VPN_HEALTHCHECK_URLS="${VPN_HEALTHCHECK_URLS:-${VPN_HEALTHCHECK_URL:-https://api.ipify.org} https://checkip.amazonaws.com https://ifconfig.me/ip}"
HEALTHCHECK_BUDGET_SECONDS="${HEALTHCHECK_BUDGET_SECONDS:-18}"

url_count=0
for _probe_url in $VPN_HEALTHCHECK_URLS; do
    url_count=$((url_count + 1))
done
[ "$url_count" -gt 0 ] || url_count=1
probe_timeout=$((HEALTHCHECK_BUDGET_SECONDS / url_count))
[ "$probe_timeout" -ge 2 ] || probe_timeout=2

probe_via() {
    interface="$1"
    for url in $VPN_HEALTHCHECK_URLS; do
        if curl --interface "$interface" --fail --silent --show-error \
            --max-time "$probe_timeout" "$url" >/dev/null; then
            return 0
        fi
    done
    return 1
}

if grep -q '"mode":"multi"' /var/run/bridge-status/status.json 2>/dev/null; then
    grep -q '"connected":true' /var/run/bridge-status/status.json
    interface=$(grep -o '"vpn_interface":"[^"]*"' /var/run/bridge-status/status.json | head -n1 | cut -d'"' -f4)
    [ -n "$interface" ]
    ip addr show dev "$interface" | grep -q 'inet '
    probe_via "$interface"
    exit 0
fi

tailscale status >/dev/null 2>&1
grep -q '\"connected\": true' /var/run/bridge-status/status.json

interface=""
for candidate in protonvpn tun0; do
    if ip link show "$candidate" >/dev/null 2>&1; then
        interface="$candidate"
        break
    fi
done

[ -n "$interface" ]
ip addr show dev "$interface" | grep -q 'inet '
probe_via "$interface"
