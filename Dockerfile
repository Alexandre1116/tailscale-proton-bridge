# Use a imagem oficial do Tailscale como base para copiar os binários atualizados
FROM tailscale/tailscale:latest AS tailscale

# Use Alpine Linux para a imagem final (leve e segura)
FROM alpine:latest

# Instalar dependências necessárias para OpenVPN, WireGuard e rede
RUN apk add --no-cache \
    ca-certificates \
    iptables \
    iproute2 \
    wireguard-tools \
    wireguard-go \
    openvpn \
    openresolv \
    bash \
    procps

# Copiar os binários oficiais do Tailscale para a imagem final
COPY --from=tailscale /app/tailscale /app/tailscale
COPY --from=tailscale /app/tailscaled /app/tailscaled

# Adicionar os binários ao PATH do sistema
ENV PATH="/app:${PATH}"

# Criar os diretórios para as configurações de VPN e Tailscale
RUN mkdir -p /etc/wireguard /etc/openvpn /var/lib/tailscale /var/run/tailscale

# Copiar o script de entrada
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Definir o entrypoint
ENTRYPOINT ["/entrypoint.sh"]
