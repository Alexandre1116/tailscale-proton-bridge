#!/bin/bash
# shellcheck disable=SC2004

set -u
umask 077

PROFILE_ROOT="${PROFILE_ROOT:-/etc/bridge-profiles}"
STATUS_DIR="/var/run/bridge-status"
STATUS_FILE="$STATUS_DIR/status.json"
TRAFFIC_FILE="$STATUS_DIR/traffic.json"
VPN_CHECK_INTERVAL="${VPN_CHECK_INTERVAL:-30}"
VPN_FAILURE_THRESHOLD="${VPN_FAILURE_THRESHOLD:-3}"
VPN_HEALTHCHECK_URL="${VPN_HEALTHCHECK_URL:-https://api.ipify.org}"
STATUS_UPDATE_INTERVAL="${STATUS_UPDATE_INTERVAL:-2}"

mkdir -p "$STATUS_DIR" /var/lib/tailscale /var/run/tailscale /tmp

declare -a PROFILE_IDS PROFILE_NAMES PROFILE_TYPES PROFILE_HOSTNAMES PROFILE_EXTRAS
declare -a VPN_INTERFACES TS_INTERFACES TS_SOCKETS TS_PIDS VPN_PIDS
declare -a PROFILE_STATES PROFILE_AUTH_URLS PROFILE_FAILURES PROFILE_TABLES
PROFILE_COUNT=0

json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

metadata_value() {
    local file="$1" key="$2"
    sed -n "s/^${key}=//p" "$file" | head -n1
}

interface_stat() {
    local interface="$1" stat="$2"
    local path="/sys/class/net/${interface}/statistics/${stat}"
    if [ -n "$interface" ] && [ -r "$path" ]; then
        cat "$path"
    else
        echo 0
    fi
}

sum_stat() {
    local stat="$1" total=0 value i
    local -n interfaces="$2"
    for ((i = 0; i < PROFILE_COUNT; i++)); do
        value=$(interface_stat "${interfaces[$i]}" "$stat")
        total=$((total + value))
    done
    echo "$total"
}

active_exit_node_clients() {
    # Count unique Tailscale source addresses in forwarded conntrack entries.
    # The count covers all configured exit-node profiles in multi-profile mode.
    if ! command -v conntrack >/dev/null 2>&1; then
        echo 0
        return
    fi

    conntrack -L -f ipv4 2>/dev/null | awk '
        function is_tailscale_ip(ip, parts, second_octet) {
            if (split(ip, parts, ".") != 4 || parts[1] != "100") return 0
            second_octet = parts[2] + 0
            return second_octet >= 64 && second_octet <= 127
        }
        {
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^src=/) {
                    ip = substr($i, 5)
                    if (is_tailscale_ip(ip)) clients[ip] = 1
                    break
                }
            }
        }
        END {
            count = 0
            for (ip in clients) count++
            print count + 0
        }
    '
}

write_status() {
    local now connected=false i ip state name host mode auth
    local primary_ip="" primary_host="" primary_vpn_interface="" primary_auth_url=""
    local status_tmp="${STATUS_FILE}.tmp.$$"
    for ((i = 0; i < PROFILE_COUNT; i++)); do
        if [ "${PROFILE_STATES[$i]}" = "connected" ]; then
            connected=true
            if [ -z "$primary_host" ]; then
                primary_ip=$(tailscale --socket="${TS_SOCKETS[$i]}" ip -4 2>/dev/null | head -n1 || true)
                primary_host="${PROFILE_HOSTNAMES[$i]}"
                primary_vpn_interface="${VPN_INTERFACES[$i]}"
            fi
        elif [ -z "$primary_auth_url" ] && [ -n "${PROFILE_AUTH_URLS[$i]}" ]; then
            primary_auth_url="${PROFILE_AUTH_URLS[$i]}"
        fi
    done

    {
        now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        printf '{"mode":"multi","vpn_mode":"multi","vpn_interface":"%s","connected":%s,"tailscale_ip":"%s","hostname":"%s","auth_url":"%s","profiles":[' \
            "$primary_vpn_interface" "$connected" "$primary_ip" "$(json_escape "$primary_host")" "$(json_escape "$primary_auth_url")"
        for ((i = 0; i < PROFILE_COUNT; i++)); do
            [ "$i" -gt 0 ] && printf ','
            state="${PROFILE_STATES[$i]}"
            ip=""
            if [ "$state" = "connected" ]; then
                ip=$(tailscale --socket="${TS_SOCKETS[$i]}" ip -4 2>/dev/null | head -n1 || true)
            fi
            name=$(json_escape "${PROFILE_NAMES[$i]}")
            host=$(json_escape "${PROFILE_HOSTNAMES[$i]}")
            auth=$(json_escape "${PROFILE_AUTH_URLS[$i]}")
            printf '{"id":"%s","name":"%s","vpn_mode":"%s","vpn_interface":"%s","tailscale_interface":"%s","connected":%s,"tailscale_ip":"%s","hostname":"%s","auth_url":"%s","state":"%s"}' \
                "${PROFILE_IDS[$i]}" "$name" "${PROFILE_TYPES[$i]}" "${VPN_INTERFACES[$i]}" "${TS_INTERFACES[$i]}" \
                "$([ "$state" = connected ] && echo true || echo false)" "$ip" "$host" "$auth" "$state"
        done
        printf '],"last_updated":"%s"}\n' "$now"
    } > "$status_tmp"
    chmod 0644 "$status_tmp"
    mv -f "$status_tmp" "$STATUS_FILE"
}

write_traffic() {
    local status_tmp="${TRAFFIC_FILE}.tmp.$$" now exit_node_clients
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    exit_node_clients=$(active_exit_node_clients)
    cat > "$status_tmp" <<EOF
{
  "tailscale_rx_bytes": "$(sum_stat rx_bytes TS_INTERFACES)",
  "tailscale_tx_bytes": "$(sum_stat tx_bytes TS_INTERFACES)",
  "tailscale_rx_packets": "$(sum_stat rx_packets TS_INTERFACES)",
  "tailscale_tx_packets": "$(sum_stat tx_packets TS_INTERFACES)",
  "vpn_rx_bytes": "$(sum_stat rx_bytes VPN_INTERFACES)",
  "vpn_tx_bytes": "$(sum_stat tx_bytes VPN_INTERFACES)",
  "vpn_rx_packets": "$(sum_stat rx_packets VPN_INTERFACES)",
  "vpn_tx_packets": "$(sum_stat tx_packets VPN_INTERFACES)",
  "exit_node_clients": ${exit_node_clients},
  "traffic_updated": "$now"
}
EOF
    chmod 0644 "$status_tmp"
    mv -f "$status_tmp" "$TRAFFIC_FILE"
}

read_secret() {
    local path="$1"
    [ -r "$path" ] && tr -d '\r\n' < "$path" || true
}

profile_mode() {
    local value="$1" directory="$2"
    value=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
    if [ -z "$value" ] || [ "$value" = auto ]; then
        if [ -f "$directory/wireguard.conf" ]; then
            echo wireguard
        elif [ -f "$directory/openvpn.ovpn" ]; then
            echo openvpn
        else
            echo ""
        fi
    elif [ "$value" = wireguard ] || [ "$value" = openvpn ]; then
        echo "$value"
    else
        echo ""
    fi
}

cleanup() {
    local i
    trap - TERM INT EXIT
    for ((i = 0; i < PROFILE_COUNT; i++)); do
        [ -n "${TS_PIDS[$i]:-}" ] && kill -TERM "${TS_PIDS[$i]}" 2>/dev/null || true
        [ -n "${VPN_PIDS[$i]:-}" ] && kill -TERM "${VPN_PIDS[$i]}" 2>/dev/null || true
        if [ "${PROFILE_TYPES[$i]}" = wireguard ]; then
            wg-quick down "${VPN_INTERFACES[$i]}" 2>/dev/null || true
        fi
        ip rule del pref "$((1000 + i))" 2>/dev/null || true
        ip rule del fwmark "${PROFILE_TABLES[$i]}" pref "$((2000 + i))" 2>/dev/null || true
    done
    iptables -F FORWARD 2>/dev/null || true
    iptables -t nat -F POSTROUTING 2>/dev/null || true
    iptables -t mangle -F PREROUTING 2>/dev/null || true
    iptables -t mangle -F OUTPUT 2>/dev/null || true
    rm -f "$TRAFFIC_FILE" "$STATUS_FILE"
    exit 0
}
trap cleanup TERM INT EXIT

if [ ! -d "$PROFILE_ROOT" ]; then
    echo "No profile directory found at $PROFILE_ROOT" >&2
    exit 1
fi

for metadata in "$PROFILE_ROOT"/*/profile.env; do
    [ -f "$metadata" ] || continue
    directory=$(dirname "$metadata")
    profile_id=$(basename "$directory")
    case "$profile_id" in
        ''|*[!a-z0-9-]*) echo "Skipping invalid profile id: $profile_id" >&2; continue ;;
    esac
    mode=$(profile_mode "$(metadata_value "$metadata" VPN_TYPE)" "$directory")
    if [ -z "$mode" ]; then
        echo "Skipping $profile_id: no valid VPN configuration" >&2
        continue
    fi
    PROFILE_IDS[$PROFILE_COUNT]="$profile_id"
    PROFILE_NAMES[$PROFILE_COUNT]="$(metadata_value "$metadata" NAME)"
    PROFILE_TYPES[$PROFILE_COUNT]="$mode"
    PROFILE_HOSTNAMES[$PROFILE_COUNT]="$(metadata_value "$metadata" TS_HOSTNAME)"
    [ -n "${PROFILE_HOSTNAMES[$PROFILE_COUNT]}" ] || PROFILE_HOSTNAMES[$PROFILE_COUNT]="protonvpn-$profile_id"
    PROFILE_EXTRAS[$PROFILE_COUNT]="$(metadata_value "$metadata" TS_EXTRA_ARGS)"
    VPN_INTERFACES[$PROFILE_COUNT]="vpn${PROFILE_COUNT}"
    TS_INTERFACES[$PROFILE_COUNT]="ts${PROFILE_COUNT}"
    TS_SOCKETS[$PROFILE_COUNT]="/var/run/tailscale/${profile_id}.sock"
    PROFILE_STATES[$PROFILE_COUNT]="starting"
    PROFILE_AUTH_URLS[$PROFILE_COUNT]=""
    PROFILE_FAILURES[$PROFILE_COUNT]=0
    PROFILE_TABLES[$PROFILE_COUNT]="$((100 + PROFILE_COUNT))"
    TS_PIDS[$PROFILE_COUNT]=""
    VPN_PIDS[$PROFILE_COUNT]=""
    PROFILE_COUNT=$((PROFILE_COUNT + 1))
done

if [ "$PROFILE_COUNT" -eq 0 ]; then
    echo "No usable profiles found. Add profiles in the Web UI." >&2
    exit 1
fi

rm -f "$STATUS_FILE" "$TRAFFIC_FILE"
write_status

for ((i = 0; i < PROFILE_COUNT; i++)); do
    profile_id="${PROFILE_IDS[$i]}"
    directory="$PROFILE_ROOT/$profile_id"
    vpn_if="${VPN_INTERFACES[$i]}"
    ts_if="${TS_INTERFACES[$i]}"
    mode="${PROFILE_TYPES[$i]}"
    table="${PROFILE_TABLES[$i]}"
    echo "Starting profile $profile_id ($mode)"

    if [ "$mode" = wireguard ]; then
        source="$directory/wireguard.conf"
        running="/tmp/${vpn_if}.conf"
        sed -E \
            -e 's/^([[:space:]]*Address[[:space:]]*=[^,]+),.*/\1/' \
            -e 's/^([[:space:]]*AllowedIPs[[:space:]]*=[^,]+),.*/\1/' \
            -e 's/^([[:space:]]*DNS[[:space:]]*=)/# \1/gI' \
            -e '/^[[:space:]]*Table[[:space:]]*=/d' "$source" | \
            sed '/^\[Interface\]$/a Table = off' > "$running"
        if ! wg-quick up "$running" >/tmp/bridge-"${profile_id}".log 2>&1; then
            PROFILE_STATES[$i]="error"
            echo "Profile $profile_id: WireGuard failed to start" >&2
            continue
        fi
    else
        source="$directory/openvpn.ovpn"
        running="/tmp/${profile_id}.ovpn"
        credentials="/tmp/${profile_id}.credentials"
        if [ -f "$directory/credentials.txt" ]; then
            cp "$directory/credentials.txt" "$credentials"
        else
            proton_user=$(read_secret "$directory/protonvpn_user")
            proton_password=$(read_secret "$directory/protonvpn_password")
            if [ -z "$proton_user" ] || [ -z "$proton_password" ]; then
                PROFILE_STATES[$i]="error"
                echo "Profile $profile_id: OpenVPN credentials are missing" >&2
                continue
            fi
            printf '%s\n' "$proton_user" > "$credentials"
            printf '%s\n' "$proton_password" >> "$credentials"
        fi
        chmod 600 "$credentials"
        sed -E \
            -e '/^[[:space:]]*dev([[:space:]]|$)/d' \
            -e '/^[[:space:]]*route(-ipv6)?([[:space:]]|$)/d' "$source" > "$running"
        printf '\nroute-nopull\ndev %s\n' "$vpn_if" >> "$running"
        openvpn --config "$running" --auth-user-pass "$credentials" --auth-nocache \
            >/tmp/bridge-"${profile_id}".log 2>&1 &
        VPN_PIDS[$i]=$!
    fi

    wait_count=0
    while [ "$wait_count" -lt 30 ]; do
        if ip addr show dev "$vpn_if" 2>/dev/null | grep -q 'inet '; then break; fi
        wait_count=$((wait_count + 1))
        sleep 1
    done
    if ! ip addr show dev "$vpn_if" 2>/dev/null | grep -q 'inet '; then
        PROFILE_STATES[$i]="error"
        echo "Profile $profile_id: VPN did not receive an IPv4 address" >&2
        continue
    fi

    state_dir="/var/lib/tailscale/profiles/$profile_id"
    mkdir -p "$state_dir"
    rm -f "${TS_SOCKETS[$i]}"
    tailscaled --state="$state_dir/tailscaled.state" --socket="${TS_SOCKETS[$i]}" \
        --statedir="$state_dir" --tun="$ts_if" --port="$((41641 + i))" \
        --netfilter-mode=off >/tmp/bridge-"${profile_id}"-tailscale.log 2>&1 &
    TS_PIDS[$i]=$!
    wait_count=0
    while [ "$wait_count" -lt 30 ]; do
        [ -S "${TS_SOCKETS[$i]}" ] && break
        wait_count=$((wait_count + 1))
        sleep 1
    done
    if [ ! -S "${TS_SOCKETS[$i]}" ]; then
        PROFILE_STATES[$i]="error"
        echo "Profile $profile_id: tailscaled did not start" >&2
        continue
    fi

    authkey=$(read_secret "$directory/ts_authkey")
    extra_args=()
    if [ -n "${PROFILE_EXTRAS[$i]}" ]; then read -r -a extra_args <<< "${PROFILE_EXTRAS[$i]}"; fi
    up_log="/tmp/bridge-${profile_id}-up.log"
    tailscale --socket="${TS_SOCKETS[$i]}" up --authkey="$authkey" \
        --hostname="${PROFILE_HOSTNAMES[$i]}" --advertise-exit-node \
        --accept-routes=false --accept-dns=false --netfilter-mode=off \
        "${extra_args[@]}" >"$up_log" 2>&1 &
    up_pid=$!
    wait_count=0
    while kill -0 "$up_pid" 2>/dev/null && [ "$wait_count" -lt 60 ]; do
        PROFILE_AUTH_URLS[$i]=$(grep -oE 'https://login\.tailscale\.com/a/[A-Za-z0-9]+' "$up_log" 2>/dev/null | head -n1 || true)
        write_status
        wait_count=$((wait_count + 1))
        sleep 1
    done
    if ! wait "$up_pid"; then
        PROFILE_STATES[$i]="pending"
        echo "Profile $profile_id: waiting for Tailscale authentication" >&2
        continue
    fi

    ip route replace "100.64.0.0/10" dev "$ts_if" table "$table"
    ip route replace default dev "$vpn_if" table "$table"
    ip rule add iif "$ts_if" table "$table" pref "$((1000 + i))"
    ip rule add fwmark "$table" table "$table" pref "$((2000 + i))"
    PROFILE_STATES[$i]="connected"
    write_status
done

if ! sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1; then
    [ "$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo 0)" = 1 ] || exit 1
fi
sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true
iptables -F FORWARD || true
iptables -P FORWARD DROP
iptables -t nat -F POSTROUTING || true
iptables -t mangle -F PREROUTING || true
iptables -t mangle -F OUTPUT || true

for ((i = 0; i < PROFILE_COUNT; i++)); do
    [ "${PROFILE_STATES[$i]}" = connected ] || continue
    ts_if="${TS_INTERFACES[$i]}"
    vpn_if="${VPN_INTERFACES[$i]}"
    mark="${PROFILE_TABLES[$i]}"
    iptables -t mangle -A PREROUTING -i "$ts_if" -m conntrack --ctstate NEW -j CONNMARK --set-mark "$mark"
    iptables -t mangle -A PREROUTING -j CONNMARK --restore-mark
    iptables -t mangle -A OUTPUT -j CONNMARK --restore-mark
    iptables -A FORWARD -i "$ts_if" -o "$vpn_if" -j ACCEPT
    iptables -A FORWARD -i "$vpn_if" -o "$ts_if" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
    iptables -t nat -A POSTROUTING -o "$vpn_if" -j MASQUERADE
done

if ! grep -q '"connected":true' "$STATUS_FILE"; then
    echo "No multi-exit-node profile connected" >&2
    exit 1
fi

write_status
write_traffic
while true; do
    for ((i = 0; i < PROFILE_COUNT; i++)); do
        [ "${PROFILE_STATES[$i]}" = connected ] || continue
        if ! kill -0 "${TS_PIDS[$i]}" 2>/dev/null || \
           { [ "${PROFILE_TYPES[$i]}" = openvpn ] && ! kill -0 "${VPN_PIDS[$i]}" 2>/dev/null; } || \
           ! ip addr show dev "${VPN_INTERFACES[$i]}" 2>/dev/null | grep -q 'inet ' || \
           ! curl --interface "${VPN_INTERFACES[$i]}" --fail --silent --show-error --max-time 10 "$VPN_HEALTHCHECK_URL" >/dev/null 2>&1; then
            PROFILE_FAILURES[$i]=$((PROFILE_FAILURES[$i] + 1))
            PROFILE_STATES[$i]="degraded"
            if [ "${PROFILE_FAILURES[$i]}" -ge "$VPN_FAILURE_THRESHOLD" ]; then
                echo "Profile ${PROFILE_IDS[$i]} failed its health checks" >&2
            fi
        else
            PROFILE_FAILURES[$i]=0
            PROFILE_STATES[$i]="connected"
        fi
    done
    write_status
    write_traffic
    sleep "$VPN_CHECK_INTERVAL"
done
