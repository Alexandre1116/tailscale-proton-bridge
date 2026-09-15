# Use a imagem oficial do Tailscale como base para copiar os binários atualizados
ARG TAILSCALE_VERSION=1.102.3
FROM tailscale/tailscale:v${TAILSCALE_VERSION} AS tailscale

# Use Alpine Linux para a imagem final (leve e segura)
FROM alpine:3.23.5

# Instalar dependências necessárias para OpenVPN, WireGuard e rede
RUN apk add --no-cache \
    ca-certificates \
    curl \
    iptables \
    ip6tables \
    iproute2 \
    wireguard-tools \
    wireguard-go \
    openvpn \
    openresolv \
    bash \
    procps

# Synology e alguns kernels NAS não suportam nftables neste contexto.
# Alpine 3.22 provides the nftables backend by default. Do not replace its
# working wrappers with links to *-legacy binaries, which are not installed
# in current Alpine releases.

# As imagens atuais publicam os binários em /usr/local/bin.
COPY --from=tailscale /usr/local/bin/tailscale /app/tailscale
COPY --from=tailscale /usr/local/bin/tailscaled /app/tailscaled

# Adicionar os binários ao PATH do sistema
ENV PATH="/app:${PATH}"

# Criar os diretórios para as configurações de VPN e Tailscale
RUN mkdir -p /etc/wireguard /etc/openvpn /var/lib/tailscale /var/run/tailscale

# Copiar o script de entrada
COPY entrypoint.sh /entrypoint.sh
COPY healthcheck.sh /healthcheck.sh
RUN chmod +x /entrypoint.sh
RUN chmod +x /healthcheck.sh
RUN test -x /app/tailscale -a -x /app/tailscaled

# Definir o entrypoint
ENTRYPOINT ["/entrypoint.sh"]
