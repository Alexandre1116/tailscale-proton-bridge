# Proton VPN to Tailscale Bridge Exit Node

**English** | [Português](README.pt.md)

[![Docker Build](https://img.shields.io/badge/docker-ready-blue.svg?logo=docker)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tailscale](https://img.shields.io/badge/Tailscale-Exit_Node-informational?logo=tailscale)](https://tailscale.com)
[![Proton VPN](https://img.shields.io/badge/Proton_VPN-WireGuard_%2F_OpenVPN-purple?logo=protonvpn)](https://protonvpn.com)
[![Multi-Arch](https://img.shields.io/badge/arch-amd64%20%7C%20arm64-brightgreen)](#)

Current release: [`v0.1.0`](https://github.com/Alexandre1116/tailscale-proton-bridge/releases/tag/v0.1.0)

Route all your private Tailscale network traffic through **Proton VPN** (including the **100% Free tier**) via a lightweight Docker exit node.

Whenever any device on your Tailnet (smartphone, laptop, tablet) connects to this Exit Node, its internet traffic is routed through Proton VPN servers.

```mermaid
graph LR
    subgraph Tailnet ["Your Tailscale Network (Tailnet)"]
        A[📱 Phone] -->|Encrypted Tailscale Mesh| C[🐳 Docker Bridge Exit Node]
        B[💻 Laptop] -->|Encrypted Tailscale Mesh| C
    end

    subgraph DockerBridge ["Container Security Layer"]
        C -->|iptables NAT & Kill Switch| D[🔒 WireGuard / OpenVPN]
    end

    subgraph ProtonNetwork ["Proton VPN Network"]
        D -->|Encrypted Tunnel| E[🌍 Proton VPN Server]
    end

        E -->|Public IP via Proton| F[🌐 Public Internet]
```

---

## Features

- **WireGuard & OpenVPN Support**: Optimized for WireGuard (faster speed, minimal CPU usage) with full OpenVPN fallback.
- **Works with Proton VPN Free**: No paid plan required. Supports Proton VPN Free tier server configurations.
- **Forwarding kill switch**: Drops forwarded traffic unless it uses the VPN interface. Validate DNS and IPv6 behaviour on the client you use.
- **Non-Privileged Container**: Operates without `privileged: true`, using only minimum Linux capabilities (`NET_ADMIN`, `NET_RAW`).
- **Interactive Web UI**: Modern web dashboard with drag-and-drop file upload, setup wizard, and real-time status monitoring.
- **Interactive Setup CLI**: Guided terminal wizard (`python setup.py`) to configure your environment in seconds.
- **Multi-Architecture**: Out-of-the-box support for `amd64` (x86_64 PCs & servers) and `arm64` (Raspberry Pi 4/5, Synology NAS, Apple Silicon).
- **Native Healthchecks**: Docker health checks monitoring Tailscale daemon and VPN interface connectivity.

This beta is intended for validation on the compatibility targets below. Test
the VPN-down fail-closed behaviour before using the bridge for production
traffic.

---

## Prerequisites

1. **Proton VPN Account** (Free or Paid tier).
2. **Tailscale Account** with access to the Tailscale admin console.
3. **Docker & Docker Compose** installed on your host (Linux PC, Server, Synology/QNAP NAS, or Raspberry Pi).

---

## Quick Start (CLI Wizard)

An interactive Python setup script is included to automatically create directories, configure `.env`, and start the containers:

```bash
python setup.py
```

Follow the prompts on your terminal. If you prefer manual setup, follow the guide below.

---

## Manual Step-by-Step Setup

### Step 1: Clone Repository & Prepare Directories

```bash
git clone https://github.com/Alexandre1116/tailscale-proton-bridge.git
cd tailscale-proton-bridge
```

The directories `./vpn/wireguard` and `./vpn/openvpn` are pre-tracked in Git.

---

### Step 2: Download Proton VPN Configuration

#### Option A: WireGuard (Recommended)
1. Sign in to [account.protonvpn.com](https://account.protonvpn.com).
2. Go to **Downloads** -> **WireGuard configuration**.
3. Choose a configuration name, select platform **Linux**, enable **VPN Accelerator**, and pick a server (e.g. Free server).
4. Click **Create** and **Download** the `.conf` file.
5. Save or rename the downloaded file to:
   ```
   ./vpn/wireguard/protonvpn.conf
   ```

#### Option B: OpenVPN (Alternative)
1. In [account.protonvpn.com](https://account.protonvpn.com), go to **Downloads** -> **OpenVPN configuration files**.
2. Select **Linux**, **UDP**, and download a server profile.
3. Save or rename it to:
   ```
   ./vpn/openvpn/protonvpn.ovpn
   ```
4. On the same downloads page, copy your **OpenVPN / IKEv2 username and password** (these are different from your regular login credentials). Either:
   - Put them in `.env` (`PROTONVPN_USER` and `PROTONVPN_PASSWORD`), **OR**
   - Save them to `./vpn/openvpn/credentials.txt` (username on line 1, password on line 2).

---

### Step 3: Configure `.env`

Copy the template file:
```bash
cp .env.example .env
```

Edit `.env` with your settings:
- **`TS_AUTHKEY`**: Generated in Tailscale Admin Console (**Settings -> Keys -> Generate Auth Key**). For a long-lived bridge, use a reusable, pre-authorized key. Leave blank to authenticate via the login URL shown in the logs and Web UI.
- **`VPN_TYPE`**: `wireguard`, `openvpn`, or `auto` (auto-detects configuration file).
- **`TS_HOSTNAME`**: Name for your device on your Tailnet (default: `protonvpn-bridge`).
- **`WEBUI_USERNAME`** & **`WEBUI_PASSWORD`**: Required credentials for the Web UI. The Web UI refuses to start with an empty password.

---

### Step 4: Build & Launch Container

```bash
docker compose up -d --build
```

View live container logs:
```bash
docker compose logs -f
```

---

### Step 5: Approve Exit Node in Tailscale

Tailscale requires manual admin approval for nodes advertising as exit nodes:

1. Open your [Tailscale Admin Console](https://login.tailscale.com/admin/machines).
2. Find your node (e.g., `protonvpn-bridge`).
3. Click the **three dots (...)** next to the machine and choose **Edit route settings**.
4. Check **Use as exit node** and click **Save**.

Your Proton VPN Exit Node is now live!

---

## Web UI Dashboard

A lightweight web dashboard is available at the configured bind address and
port, for example `http://<host-ip>:8080`:

1. **Authentication**: Uses HTTP Basic Auth (`WEBUI_USERNAME` and `WEBUI_PASSWORD`). `WEBUI_PASSWORD` must be set before starting the Web UI.
2. **First Run Wizard**: Automatically launches a 4-step wizard with drag-and-drop file upload for `.conf` or `.ovpn` files.
3. **Manual Login Link**: If no `TS_AUTHKEY` is provided, a clickable login link appears dynamically in the header.
4. **Apply Changes**: Because the Web UI does not access the Docker socket for security reasons, apply saved modifications by recreating both services:
   ```bash
   docker compose up -d --build vpn-tailscale-bridge webui
   ```

---

## How to Use on Your Devices

1. Open the **Tailscale** client on your smartphone, tablet, or laptop.
2. Connect to your Tailnet.
3. Select **Exit Node** and choose `protonvpn-bridge`.
4. Check [ipinfo.io](https://ipinfo.io) or [dnsleaktest.com](https://dnsleaktest.com) to confirm your public IP and DNS reflect your chosen Proton VPN server.

---

## Security & Architecture Details

- **Minimal Privileges**: Runs with specific Linux capabilities (`NET_ADMIN` and `NET_RAW`) and `/dev/net/tun` rather than full `privileged: true`.
- **Forwarding kill switch**:
  - `iptables -P FORWARD DROP`: Default policy drops all routed packets. Packets are forwarded strictly through the VPN interface (`protonvpn` / `tun0`).
  - Container-level IPv6 disabling and `ip6tables -P FORWARD DROP` protect forwarded traffic. Validate DNS and IPv6 behaviour on the client you use.
- **Docker DNS Compatibility**: Transparently manages `/etc/resolv.conf` to avoid `openresolv` bind-mount collisions common in Docker environments.
- **Auto-Healing**: Monitors Tailscale, the VPN interface, and a real HTTPS request through the VPN. After consecutive failures, the container exits and Docker restarts it (`restart: unless-stopped`).
- **Image Scanning**: CI scans both images with Trivy. Critical findings fail the build; high-severity findings from upstream images remain visible in the scan output for review.

---

### Web UI transport and access

Configure these variables in `.env` before starting the containers:

```dotenv
WEBUI_USERNAME=admin
WEBUI_PASSWORD=choose-a-strong-password
WEBUI_PROTOCOL=http
WEBUI_ACCESS_MODE=all
WEBUI_BIND_ADDRESS=0.0.0.0
```

For HTTPS, put the certificate and key at `webui/certs/fullchain.pem` and
`webui/certs/privkey.pem`, then set `WEBUI_PROTOCOL=https`. The certificate
must match the hostname or IP used by the browser. A self-signed certificate
will show a browser warning and is not suitable for a public deployment.
The mounted certificate files must be readable by the Web UI container user (UID 1000).

To restrict the published port to the Tailnet, set `WEBUI_ACCESS_MODE=tailnet`
and use the host's Tailscale IPv4 address:

```dotenv
WEBUI_ACCESS_MODE=tailnet
WEBUI_BIND_ADDRESS=100.101.102.103
```

For bridge credentials, create files under `secrets/` with permissions that
allow only the container user that needs them to read them, and use the file
variables instead of putting values in `.env`:

```dotenv
TS_AUTHKEY_FILE=/run/secrets/ts_authkey
PROTONVPN_USER_FILE=/run/secrets/protonvpn_user
PROTONVPN_PASSWORD_FILE=/run/secrets/protonvpn_password
WEBUI_PASSWORD_FILE=/run/secrets/webui_password
```

The bridge runs as root inside its container and can read its mounted secret
files. The Web UI runs as UID 1000, so `webui_password` must be readable by
that UID. Never commit the `secrets/` directory contents.

The `tailnet` mode also allows the usual Tailscale IPv4 and IPv6 ranges. Binding
to a specific Tailscale IP is the stronger restriction. The Web UI writes VPN
credentials and `.env`, so do not publish this port directly to the Internet.

### Compatibility matrix

| Platform | Status | Notes |
|---|---|---|
| Debian/Ubuntu Linux with Docker Engine | Supported | Requires `/dev/net/tun`, IPv4 forwarding, and network permissions. |
| Synology DSM 7.x with Container Manager | Supported with validation | Confirm `/dev/net/tun`, the available iptables backend, and the NAS architecture. |
| Raspberry Pi 4/5, 64-bit Raspberry Pi OS | Supported | CI covers `arm64`; test OpenVPN performance on the target model. |
| Docker Desktop on Windows/macOS | Bridge not supported | The UI may build, but the exit node needs the TUN device and Linux host networking. |

The integration test needs a second container running Tailscale. See
`tests/integration/test_exit_node.py` and set `TS_CLIENT_CONTAINER` and
`TS_EXIT_NODE` before running it.

The local checks are:

```bash
python -m pip install -r webui/requirements-dev.txt pip-audit
pytest -q
shellcheck entrypoint.sh healthcheck.sh webui/entrypoint.sh webui/healthcheck.sh
pip-audit -r webui/requirements.txt
```

## License

This project is open-source software licensed under the [MIT License](LICENSE).
