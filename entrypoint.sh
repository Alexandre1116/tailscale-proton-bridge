#!/bin/bash

# Terminar imediatamente em caso de erro não tratado
set -e

echo "============================================="
echo "   Tailscale - Proton VPN Bridge Exit Node   "
echo "============================================="

WG_RUNNING_CONF="/tmp/protonvpn.conf"

# Função de limpeza para encerramento gracioso
cleanup() {
    echo "Sinal de paragem recebido. A encerrar serviços..."
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

write_status() {
    local connected="$1"
    local auth_url="${2:-}"
    local ts_ip=""
    if [ "$connected" = "true" ]; then
        ts_ip=$(tailscale ip -4 2>/dev/null | head -n1)
    fi
    cat > "$STATUS_FILE" <<EOF
{
  "vpn_mode": "${VPN_MODE:-}",
  "vpn_interface": "${VPN_INTERFACE:-}",
  "connected": ${connected},
  "tailscale_ip": "${ts_ip}",
  "hostname": "${HOSTNAME:-}",
  "auth_url": "${auth_url}",
  "last_updated": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
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
for i in {1..30}; do
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
    sed 's/^\([[:space:]]*DNS[[:space:]]*=\)/# \1/gI' "$WG_CONF" > "$WG_RUNNING_CONF"
    DNS_SERVERS=$(grep -i "^[[:space:]]*DNS" "$WG_CONF" | head -n1 | cut -d'=' -f2 | tr ',' ' ')

    # Verificar se o módulo de kernel WireGuard está disponível na máquina hospedeira.
    # Caso contrário, forçar o wg-quick a usar a implementação em userspace (wireguard-go)
    if ! ip link add dev wg-test-link type wireguard >/dev/null 2>&1; then
        echo "Módulo de kernel do WireGuard não detectado. A usar wireguard-go (userspace)..."
        export WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go
    else
        ip link delete dev wg-test-link
    fi
    
    # Iniciar WireGuard
    wg-quick up "$WG_RUNNING_CONF"
    VPN_INTERFACE="protonvpn"

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
    VPN_INTERFACE="tun0"
    
    # Aguardar até que a interface tun0 esteja criada e tenha um IP
    echo "A aguardar pela interface tun0 do OpenVPN..."
    for i in {1..30}; do
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
sysctl -w net.ipv4.ip_forward=1

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
EXTRA_ARGS=""
if [ -n "$TS_EXTRA_ARGS" ]; then
    EXTRA_ARGS="$TS_EXTRA_ARGS"
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
    $EXTRA_ARGS > "$TS_UP_LOG" 2>&1 &
TS_UP_PID=$!

if [ -z "$TS_AUTHKEY" ]; then
    echo "Aviso: TS_AUTHKEY não foi definida. A aguardar link de autenticação manual..."
    for i in $(seq 1 60); do
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

echo "=========================================================="
echo "  A Bridge Proton VPN -> Tailscale Exit Node está ATIVA! "
echo "=========================================================="
echo "IMPORTANTE: Lembre-se de ir ao painel do Tailscale (Tailscale Admin Console)"
echo "e aprovar este dispositivo como um Exit Node!"
echo "=========================================================="

write_status "true"

# 6. Loop de Monitorização da Conexão
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

    # Verificar se a interface VPN continua ativa e com IP
    if ! ip addr show dev "$VPN_INTERFACE" 2>/dev/null | grep -q "inet "; then
        echo "Erro: A interface VPN ($VPN_INTERFACE) perdeu o endereço IP. A reiniciar container..."
        write_status "false"
        exit 1
    fi

    write_status "true"
    sleep 5
done
