#!/bin/bash

# Terminar imediatamente em caso de erro não tratado
set -e
umask 077

echo "============================================="
echo "   Tailscale - Proton VPN Bridge Exit Node   "
echo "============================================="

WG_RUNNING_CONF="/tmp/protonvpn.conf"
STATUS_WRITER_PID=""

# Função de limpeza para encerramento gracioso
cleanup() {
    echo "Sinal de paragem recebido. A encerrar serviços..."
    if [ -n "$STATUS_WRITER_PID" ]; then
        kill -TERM "$STATUS_WRITER_PID" 2>/dev/null || true
    fi
    write_status "false" 2>/dev/null || true
    if [ "$VPN_MODE" = "wireguard" ]; then
        echo "A desligar interface WireGuard..."
        wg-quick down "$WG_RUNNING_CONF" 2>/dev/null || wg-quick down protonvpn 2>/dev/null || true
    elif [ "$VPN_MODE" = "openvpn" ]; then
        echo "A parar o OpenVPN..."
        if [ -n "$OPENVPN_PID" ]; then
            kill -TERM "$OPENVPN_PID" 2>/dev/null || true
        fi
    fi
    echo "A parar o Tailscale daemon..."
    if [ -n "$TAILSCALED_PID" ]; then
        kill -TERM "$TAILSCALED_PID" 2>/dev/null || true
    fi
    exit 0
}

# Capturar sinais de encerramento do Docker (SIGTERM e SIGINT)
trap cleanup SIGTERM SIGINT

# 0. Estado partilhado (lido pela Web UI através de um volume Docker)
STATUS_DIR="/var/run/bridge-status"
STATUS_FILE="$STATUS_DIR/status.json"
mkdir -p "$STATUS_DIR"
read_secret_file() {
    if [ -z "${1:-}" ] || [ ! -r "$1" ]; then
        echo "Erro: secret file ausente ou ilegível: ${1:-}" >&2
        exit 1
    fi
    tr -d '\r\n' < "$1"
}

if [ -n "${TS_AUTHKEY_FILE:-}" ]; then
    TS_AUTHKEY="$(read_secret_file "$TS_AUTHKEY_FILE")"
fi
if [ -n "${PROTONVPN_USER_FILE:-}" ]; then
    PROTONVPN_USER="$(read_secret_file "$PROTONVPN_USER_FILE")"
fi
if [ -n "${PROTONVPN_PASSWORD_FILE:-}" ]; then
    PROTONVPN_PASSWORD="$(read_secret_file "$PROTONVPN_PASSWORD_FILE")"
fi

write_status() {
    local connected="$1"
    local auth_url="${2:-}"
    local ts_ip=""
    if [ "$connected" = "true" ]; then
        ts_ip=$(tailscale ip -4 2>/dev/null | head -n1)
    fi
    local ts_rx_bytes
    local ts_tx_bytes
    local ts_rx_packets
    local ts_tx_packets
    local vpn_rx_bytes
    local vpn_tx_bytes
    local vpn_rx_packets
    local vpn_tx_packets
    ts_rx_bytes=$(interface_stat "tailscale0" "rx_bytes")
    ts_tx_bytes=$(interface_stat "tailscale0" "tx_bytes")
    ts_rx_packets=$(interface_stat "tailscale0" "rx_packets")
    ts_tx_packets=$(interface_stat "tailscale0" "tx_packets")
    vpn_rx_bytes=$(interface_stat "${VPN_INTERFACE:-}" "rx_bytes")
    vpn_tx_bytes=$(interface_stat "${VPN_INTERFACE:-}" "tx_bytes")
    vpn_rx_packets=$(interface_stat "${VPN_INTERFACE:-}" "rx_packets")
    vpn_tx_packets=$(interface_stat "${VPN_INTERFACE:-}" "tx_packets")
    local status_tmp="${STATUS_FILE}.tmp.${BASHPID:-$$}"
    cat > "$status_tmp" <<EOF
{
  "vpn_mode": "${VPN_MODE:-}",
  "vpn_interface": "${VPN_INTERFACE:-}",
  "connected": ${connected},
  "tailscale_ip": "${ts_ip}",
  "hostname": "${HOSTNAME:-}",
  "tailscale_rx_bytes": ${ts_rx_bytes},
  "tailscale_tx_bytes": ${ts_tx_bytes},
  "tailscale_rx_packets": ${ts_rx_packets},
  "tailscale_tx_packets": ${ts_tx_packets},
  "vpn_rx_bytes": ${vpn_rx_bytes},
  "vpn_tx_bytes": ${vpn_tx_bytes},
  "vpn_rx_packets": ${vpn_rx_packets},
  "vpn_tx_packets": ${vpn_tx_packets},
  "auth_url": "${auth_url}",
  "last_updated": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
    # A Web UI runs as an unprivileged user in another container and only needs
    # to read this status file. It never contains VPN credentials.
    chmod 0644 "$status_tmp"
    mv -f "$status_tmp" "$STATUS_FILE"
}

interface_stat() {
    local interface="$1"
    local stat="$2"
    local path="/sys/class/net/${interface}/statistics/${stat}"
    if [ -n "$interface" ] && [ -r "$path" ]; then
        cat "$path"
    else
        echo 0
    fi
}

write_status "false"

# 1. Definir e Validar o Modo de VPN
WG_CONF="/etc/wireguard/protonvpn.conf"
OVPN_CONF="/etc/openvpn/protonvpn.ovpn"
OVPN_CREDS="/tmp/vpn-credentials.txt"
VPN_MODE=""

# Converter VPN_TYPE para minúsculas se existir
if [ -n "$VPN_TYPE" ]; then
    VPN_TYPE=$(echo "$VPN_TYPE" | tr '[:upper:]' '[:lower:]')
fi

if [ -z "$VPN_TYPE" ] || [ "$VPN_TYPE" = "auto" ]; then
    echo "Detecção automática de protocolo ativada..."
    if [ -f "$WG_CONF" ]; then
        VPN_MODE="wireguard"
    elif [ -f "$OVPN_CONF" ]; then
        VPN_MODE="openvpn"
    else
        echo "Erro: Não foi encontrado nenhum ficheiro de configuração VPN!"
        echo "Coloque 'protonvpn.conf' em /etc/wireguard/ ou 'protonvpn.ovpn' em /etc/openvpn/."
        exit 1
    fi
elif [ "$VPN_TYPE" = "wireguard" ]; then
    VPN_MODE="wireguard"
elif [ "$VPN_TYPE" = "openvpn" ]; then
    VPN_MODE="openvpn"
else
    echo "Erro: VPN_TYPE inválido ($VPN_TYPE). Escolha 'wireguard' ou 'openvpn'."
    exit 1
fi

echo "Modo de VPN selecionado: $VPN_MODE"

# Validar ficheiros para o modo selecionado
if [ "$VPN_MODE" = "wireguard" ]; then
    if [ ! -f "$WG_CONF" ]; then
        echo "Erro: O ficheiro de configuração WireGuard não existe em: $WG_CONF"
        exit 1
    fi
elif [ "$VPN_MODE" = "openvpn" ]; then
    if [ ! -f "$OVPN_CONF" ]; then
        echo "Erro: O ficheiro de configuração OpenVPN não existe em: $OVPN_CONF"
        exit 1
    fi
    
    # Gerir credenciais do OpenVPN
    if [ -f "/etc/openvpn/credentials.txt" ]; then
        echo "A utilizar o ficheiro de credenciais montado em /etc/openvpn/credentials.txt..."
        cp /etc/openvpn/credentials.txt "$OVPN_CREDS"
        chmod 600 "$OVPN_CREDS"
    elif [ ! -f "$OVPN_CREDS" ]; then
        if [ -n "$PROTONVPN_USER" ] && [ -n "$PROTONVPN_PASSWORD" ]; then
            echo "A criar o ficheiro de credenciais OpenVPN..."
            echo "$PROTONVPN_USER" > "$OVPN_CREDS"
            echo "$PROTONVPN_PASSWORD" >> "$OVPN_CREDS"
            chmod 600 "$OVPN_CREDS"
        else
            echo "Erro: Configuração OpenVPN encontrada, mas falta o ficheiro de credenciais"
            echo "ou as variáveis PROTONVPN_USER e PROTONVPN_PASSWORD no ficheiro .env."
            exit 1
        fi
    fi
fi

# 2. Iniciar o Daemon do Tailscale (tailscaled)
echo "A iniciar o daemon do Tailscale (tailscaled)..."
mkdir -p /var/lib/tailscale /var/run/tailscale

# Se o socket antigo ainda existir devido a um crash anterior, limpá-lo
rm -f /var/run/tailscale/tailscaled.sock

tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock &
TAILSCALED_PID=$!

# Aguardar que o socket do tailscaled fique ativo
echo "A aguardar pelo socket do Tailscale..."
socket_wait=0
while [ "$socket_wait" -lt 30 ]; do
    socket_wait=$((socket_wait + 1))
    if [ -S /var/run/tailscale/tailscaled.sock ]; then
        break
    fi
    sleep 0.5
done

if [ ! -S /var/run/tailscale/tailscaled.sock ]; then
    echo "Erro: O daemon tailscaled não iniciou corretamente."
    exit 1
fi
echo "Tailscale daemon está ativo!"

# 3. Estabelecer a Conexão VPN com o Proton VPN
VPN_INTERFACE=""

if [ "$VPN_MODE" = "wireguard" ]; then
    echo "A ligar ao Proton VPN via WireGuard..."
    
    # Prevenir conflitos do resolvconf com Docker bind-mounts em /etc/resolv.conf
    # Cria uma cópia funcional do ficheiro sem a diretiva DNS do wg-quick
    IPV6_DISABLED=$(sysctl -n net.ipv6.conf.all.disable_ipv6 2>/dev/null || echo "0")
    if [ "$IPV6_DISABLED" = "1" ]; then
        sed -E \
            -e 's/^([[:space:]]*Address[[:space:]]*=[^,]+),.*/\1/' \
            -e 's/^([[:space:]]*AllowedIPs[[:space:]]*=[^,]+),.*/\1/' \
            -e 's/^([[:space:]]*DNS[[:space:]]*=)/# \1/gI' \
            "$WG_CONF" > "$WG_RUNNING_CONF"
    else
        sed 's/^\([[:space:]]*DNS[[:space:]]*=\)/# \1/gI' "$WG_CONF" > "$WG_RUNNING_CONF"
    fi
    DNS_SERVERS=$(grep -i "^[[:space:]]*DNS" "$WG_CONF" | head -n1 | cut -d'=' -f2 | tr ',' ' ')

    # Verificar se o módulo de kernel WireGuard está disponível na máquina hospedeira.
    # Caso contrário, forçar o wg-quick a usar a implementação em userspace (wireguard-go)
    if ! ip link add dev wg-test-link type wireguard >/dev/null 2>&1; then
        echo "Módulo de kernel do WireGuard não detectado. A usar wireguard-go (userspace)..."
        export WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go
    else
        ip link delete dev wg-test-link
    fi
    
    # Em Docker-in-LXC, o namespace pode impedir a escrita de
    # net.ipv4.conf.all.src_valid_mark. Nesse caso, o routing automÃ¡tico do
    # wg-quick falha; usamos Table=off e instalamos as rotas equivalentes.
    WG_MANUAL_ROUTING="0"
    if ! sysctl -q net.ipv4.conf.all.src_valid_mark=1 2>/dev/null; then
        WG_ENDPOINT=$(awk -F= '/^[[:space:]]*Endpoint[[:space:]]*=/{gsub(/[[:space:]]/, "", $2); print $2; exit}' "$WG_RUNNING_CONF")
        WG_ENDPOINT_IP="${WG_ENDPOINT%:*}"
        WG_DEFAULT_ROUTE=$(ip route show default | awk '$1 == "default" {print $3, $5; exit}')
        WG_DEFAULT_GATEWAY=${WG_DEFAULT_ROUTE%% *}
        WG_DEFAULT_DEVICE=${WG_DEFAULT_ROUTE#* }
        if [ -z "$WG_ENDPOINT_IP" ] || [ -z "$WG_DEFAULT_GATEWAY" ] || [ -z "$WG_DEFAULT_DEVICE" ]; then
            echo "Erro: nÃ£o foi possÃ­vel determinar a rota original para o endpoint WireGuard."
            exit 1
        fi
        sed '/^\[Interface\]$/a Table = off' "$WG_RUNNING_CONF" > /tmp/protonvpn.tmp.conf
        mv -f /tmp/protonvpn.tmp.conf /tmp/protonvpn.conf
        WG_RUNNING_CONF="/tmp/protonvpn.conf"
        WG_MANUAL_ROUTING="1"
        echo "Aviso: a usar routing WireGuard manual (ambiente Docker-in-LXC)."
    fi

    # Iniciar WireGuard
    wg-quick up "$WG_RUNNING_CONF"
    VPN_INTERFACE="protonvpn"

    if [ "$WG_MANUAL_ROUTING" = "1" ]; then
        ip route replace "${WG_ENDPOINT_IP}/32" via "$WG_DEFAULT_GATEWAY" dev "$WG_DEFAULT_DEVICE"
        ip route replace default dev "$VPN_INTERFACE"
    fi

    # Aplicar DNS do túnel VPN diretamente a /etc/resolv.conf sem quebrar o bind mount
    if [ -n "$DNS_SERVERS" ]; then
        echo "A configurar DNS do túnel VPN..."
        {
            for dns in $DNS_SERVERS; do
                dns=$(echo "$dns" | tr -d ' ')
                [ -n "$dns" ] && echo "nameserver $dns"
            done
        } > /etc/resolv.conf 2>/dev/null || true
    fi

elif [ "$VPN_MODE" = "openvpn" ]; then
    echo "A ligar ao Proton VPN via OpenVPN..."
    
    # Garantir que o dispositivo de túnel TUN/TAP existe no container
    mkdir -p /dev/net
    if [ ! -c /dev/net/tun ]; then
        mknod /dev/net/tun c 10 200
    fi
    
    # Iniciar OpenVPN em segundo plano
    openvpn --config "$OVPN_CONF" --auth-user-pass "$OVPN_CREDS" --dev tun0 --auth-nocache &
    OPENVPN_PID=$!
    unset PROTONVPN_PASSWORD
    VPN_INTERFACE="tun0"
    
    # Aguardar até que a interface tun0 esteja criada e tenha um IP
    echo "A aguardar pela interface tun0 do OpenVPN..."
    vpn_wait=0
    while [ "$vpn_wait" -lt 30 ]; do
        vpn_wait=$((vpn_wait + 1))
        if ip addr show dev tun0 2>/dev/null | grep -q "inet "; then
            break
        fi
        sleep 1
    done
fi

# Validar se a interface VPN obteve IP com sucesso
if ! ip addr show dev "$VPN_INTERFACE" 2>/dev/null | grep -q "inet "; then
    echo "Erro: A interface VPN ($VPN_INTERFACE) não obteve um endereço IP."
    exit 1
fi
echo "Conexão VPN estabelecida com sucesso na interface $VPN_INTERFACE!"

# 4. Configurar Encaminhamento e Regras do Firewall (iptables NAT)
echo "A ativar o IP forwarding (IPv4)..."
if ! sysctl -w net.ipv4.ip_forward=1 2>/dev/null; then
    if [ "$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo 0)" != "1" ]; then
        echo "Erro: não foi possível ativar net.ipv4.ip_forward."
        exit 1
    fi
    echo "Aviso: net.ipv4.ip_forward já estava ativo; a escrita foi recusada pelo ambiente."
fi

# Desativar IPv6 por completo para prevenir fugas (leaks) de tráfego fora do túnel:
# a Proton VPN (Free) e a generalidade dos servidores WireGuard/OpenVPN gratuitos não
# encaminham IPv6, pelo que deixar o forwarding de IPv6 ativo permitiria que os clientes
# da Tailnet saíssem para a internet em IPv6 sem passar pela VPN.
echo "A desativar IPv6 (prevenção de fugas de tráfego)..."
sysctl -w net.ipv6.conf.all.disable_ipv6=1 2>/dev/null || true
sysctl -w net.ipv6.conf.default.disable_ipv6=1 2>/dev/null || true
sysctl -w net.ipv6.conf.all.forwarding=0 2>/dev/null || true

echo "A aplicar regras de iptables para o encaminhamento..."
# Resetar regras de encaminhamento
iptables -F FORWARD || true
iptables -P FORWARD DROP

# Permitir tráfego vindo do Tailscale com destino ao Proton VPN
iptables -A FORWARD -i tailscale0 -o "$VPN_INTERFACE" -j ACCEPT
# Permitir tráfego de resposta voltando do Proton VPN para o Tailscale
iptables -A FORWARD -i "$VPN_INTERFACE" -o tailscale0 -m state --state RELATED,ESTABLISHED -j ACCEPT

# Aplicar NAT/Masquerade na saída da VPN para mascarar o IP das máquinas da Tailnet
iptables -t nat -A POSTROUTING -o "$VPN_INTERFACE" -j MASQUERADE

# Defesa adicional: bloquear por completo o encaminhamento IPv6
if command -v ip6tables >/dev/null 2>&1; then
    ip6tables -P FORWARD DROP 2>/dev/null || true
fi

echo "Regras de rede configuradas com sucesso."

# 5. Ligar à Tailnet e Anunciar Exit Node
HOSTNAME="${TS_HOSTNAME:-protonvpn-bridge}"
echo "A registar dispositivo no Tailscale com o hostname '$HOSTNAME'..."

# Configurar argumentos adicionais
EXTRA_ARGS_ARRAY=()
if [ -n "$TS_EXTRA_ARGS" ]; then
    read -r -a EXTRA_ARGS_ARRAY <<< "$TS_EXTRA_ARGS"
fi

TS_UP_LOG="/tmp/tailscale-up.log"
rm -f "$TS_UP_LOG"

# Correr 'tailscale up' em segundo plano para podermos capturar o link de
# autenticação (quando não há TS_AUTHKEY) e publicá-lo no ficheiro de estado
# lido pela Web UI, sem bloquear a escrita de estado enquanto se aguarda login.
tailscale up \
    --authkey="${TS_AUTHKEY}" \
    --hostname="${HOSTNAME}" \
    --advertise-exit-node \
    --accept-routes=false \
    --accept-dns=false \
    "${EXTRA_ARGS_ARRAY[@]}" > "$TS_UP_LOG" 2>&1 &
TS_UP_PID=$!

if [ -z "$TS_AUTHKEY" ]; then
    echo "Aviso: TS_AUTHKEY não foi definida. A aguardar link de autenticação manual..."
    auth_wait=0
    while [ "$auth_wait" -lt 60 ]; do
        auth_wait=$((auth_wait + 1))
        TS_LOGIN_URL=$(grep -oE 'https://login\.tailscale\.com/a/[A-Za-z0-9]+' "$TS_UP_LOG" 2>/dev/null | head -n1)
        if [ -n "$TS_LOGIN_URL" ]; then
            echo "Link de autenticação: $TS_LOGIN_URL"
            echo "Abra este link num browser para aprovar o dispositivo (também disponível na Web UI)."
            write_status "false" "$TS_LOGIN_URL"
            break
        fi
        if ! kill -0 "$TS_UP_PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done
fi

# Aguardar que o 'tailscale up' termine (sucesso = autenticado e configurado)
if ! wait "$TS_UP_PID"; then
    echo "Erro: 'tailscale up' falhou. Consulte os logs acima."
    write_status "false"
    exit 1
fi
unset TS_AUTHKEY

echo "=========================================================="
echo "  A Bridge Proton VPN -> Tailscale Exit Node está ATIVA! "
echo "=========================================================="
echo "IMPORTANTE: Lembre-se de ir ao painel do Tailscale (Tailscale Admin Console)"
echo "e aprovar este dispositivo como um Exit Node!"
echo "=========================================================="

write_status "true"

VPN_CHECK_INTERVAL="${VPN_CHECK_INTERVAL:-30}"
VPN_FAILURE_THRESHOLD="${VPN_FAILURE_THRESHOLD:-3}"
VPN_HEALTHCHECK_URL="${VPN_HEALTHCHECK_URL:-https://api.ipify.org}"
STATUS_UPDATE_INTERVAL="${STATUS_UPDATE_INTERVAL:-2}"
vpn_failures=0

status_writer() {
    while true; do
        write_status "true"
        sleep "$STATUS_UPDATE_INTERVAL"
    done
}

status_writer &
STATUS_WRITER_PID=$!

check_vpn_connectivity() {
    tailscale status >/dev/null 2>&1 || return 1
    ip addr show dev "$VPN_INTERFACE" 2>/dev/null | grep -q "inet " || return 1
    curl --interface "$VPN_INTERFACE" --fail --silent --show-error \
        --max-time 10 "$VPN_HEALTHCHECK_URL" >/dev/null
}

while true; do
    # Verificar se o daemon do Tailscale continua a correr
    if ! kill -0 "$TAILSCALED_PID" 2>/dev/null; then
        echo "Erro: O daemon do Tailscale (tailscaled) parou de responder. A reiniciar container..."
        write_status "false"
        exit 1
    fi

    # Se estiver no modo OpenVPN, monitorizar o processo openvpn
    if [ "$VPN_MODE" = "openvpn" ]; then
        if ! kill -0 "$OPENVPN_PID" 2>/dev/null; then
            echo "Erro: O processo OpenVPN caiu. A reiniciar container..."
            write_status "false"
            exit 1
        fi
    fi

    if check_vpn_connectivity; then
        vpn_failures=0
        write_status "true"
    else
        vpn_failures=$((vpn_failures + 1))
        echo "Aviso: teste de conectividade VPN falhou ($vpn_failures/$VPN_FAILURE_THRESHOLD)."
        write_status "false"
        if [ "$vpn_failures" -ge "$VPN_FAILURE_THRESHOLD" ]; then
            echo "Erro: a VPN não recuperou. A reiniciar o container..."
            exit 1
        fi
    fi
    sleep "$VPN_CHECK_INTERVAL"
done
