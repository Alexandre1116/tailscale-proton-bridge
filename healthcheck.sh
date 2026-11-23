#!/bin/sh
set -eu

# Same probe list the entrypoints use: one overloaded endpoint must not make
# the container look unhealthy while the tunnel is fine.
VPN_HEALTHCHECK_URLS="${VPN_HEALTHCHECK_URLS:-${VPN_HEALTHCHECK_URL:-https://api.ipify.org} https://checkip.amazonaws.com https://ifconfig.me/ip}"

probe_via() {
    interface="$1"
    for url in $VPN_HEALTHCHECK_URLS; do
        if curl --interface "$interface" --fail --silent --show-error --max-time 10 \
            "$url" >/dev/null; then
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
