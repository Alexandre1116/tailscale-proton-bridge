#!/bin/sh
set -eu

if grep -q '"mode":"multi"' /var/run/bridge-status/status.json 2>/dev/null; then
    grep -q '"connected":true' /var/run/bridge-status/status.json
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
curl --interface "$interface" --fail --silent --show-error --max-time 10 \
    "${VPN_HEALTHCHECK_URL:-https://api.ipify.org}" >/dev/null
