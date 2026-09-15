#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess
import getpass

# Códigos de cores ANSI para um terminal bonito
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

def print_banner():
    banner = f"""
{BLUE}{BOLD}===================================================================
     Tailscale - Proton VPN Bridge Exit Node Setup CLI
==================================================================={RESET}
Este script irá guiá-lo na configuração da ponte para que o seu
tráfego Tailscale seja encaminhado de forma segura pelo Proton VPN.
"""
    print(banner)

def create_directories():
    print(f"{BLUE}A criar pastas de configuração...{RESET}")
    os.makedirs(os.path.join("vpn", "wireguard"), exist_ok=True)
    os.makedirs(os.path.join("vpn", "openvpn"), exist_ok=True)
    os.makedirs("secrets", exist_ok=True)
    print(f"{GREEN}Pastas './vpn/wireguard' e './vpn/openvpn' criadas.{RESET}")
    
    # Garantir que o ficheiro .env existe antes do Docker Compose para evitar que seja montado como diretório
    env_created = False
    if not os.path.exists(".env") and os.path.exists(".env.example"):
        shutil.copyfile(".env.example", ".env")
        env_created = True
        print(f"{GREEN}Ficheiro base '.env' criado a partir de '.env.example'.{RESET}")
    print("")
    return env_created


def write_secret(name, value):
    if not value:
        return False
    path = os.path.join("secrets", name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return True

def check_command(cmd):
    return shutil.which(cmd) is not None

def main():
    print_banner()
    env_created = create_directories()

    # 1. Escolha do tipo de VPN
    print(f"{BOLD}Passo 1: Selecionar o Protocolo de VPN{RESET}")
    print("1) WireGuard (Recomendado - Mais rápido, menor uso de CPU)")
    print("2) OpenVPN (Alternativa)")
    
    vpn_choice = ""
    while vpn_choice not in ["1", "2"]:
        vpn_choice = input(f"{BLUE}Escolha uma opção (1 ou 2) [1]: {RESET}").strip()
        if vpn_choice == "":
            vpn_choice = "1"

    vpn_type = "wireguard" if vpn_choice == "1" else "openvpn"
    print(f"{GREEN}Protocolo escolhido: {vpn_type.upper()}{RESET}\n")

    # 2. Configurações da VPN
    proton_user = ""
    proton_pass = ""
    config_file_name = "protonvpn.conf" if vpn_type == "wireguard" else "protonvpn.ovpn"
    config_dir = os.path.join("vpn", "wireguard" if vpn_type == "wireguard" else "openvpn")
    config_path = os.path.join(config_dir, config_file_name)

    print(f"{BOLD}Passo 2: Configurar o Proton VPN{RESET}")
    if vpn_type == "wireguard":
        print(f"1. Aceda a: {BLUE}https://account.proton.me{RESET} -> Downloads")
        print("2. Selecione 'WireGuard configuration'.")
        print("3. Crie uma configuração para Linux, ative 'VPN Accelerator', escolha um servidor e faça download.")
        print(f"4. Coloque o ficheiro descarregado na pasta: {BOLD}{config_path}{RESET}")
    else:
        print(f"1. Aceda a: {BLUE}https://account.proton.me{RESET} -> Downloads")
        print("2. Selecione 'OpenVPN configuration files' e faça download de um servidor Free (UDP recomendado).")
        print(f"3. Coloque o ficheiro descarregado na pasta: {BOLD}{config_path}{RESET}")
        print("\nPara o OpenVPN, precisará das credenciais especiais (diferentes do seu login normal).")
        print("Obtenha-as na mesma página sob 'OpenVPN / IKEv2 username and password'.")
        proton_user = input(f"{BLUE}Introduza o utilizador OpenVPN do Proton: {RESET}").strip()
        proton_pass = input(f"{BLUE}Introduza a palavra-passe OpenVPN do Proton: {RESET}").strip()

    input(f"\n{YELLOW}Pressione ENTER depois de colocar o ficheiro de configuração na pasta para continuar...{RESET}")

    # Verificar se o ficheiro de configuração foi colocado
    if not os.path.isfile(config_path):
        print(f"\n{YELLOW}Aviso: Não encontrei o ficheiro de configuração em: {config_path}{RESET}")
        create_empty = input(f"{BLUE}Deseja criar um ficheiro vazio temporário para avançar o setup? (s/N): {RESET}").strip().lower()
        if create_empty == "s":
            with open(config_path, "w") as f:
                f.write("# Substitua este conteúdo pelo ficheiro real da Proton VPN")
            print(f"{GREEN}Ficheiro vazio temporário criado em: {config_path}{RESET}")
        else:
            print(f"{RED}Por favor, coloque o ficheiro real na pasta antes de iniciar o container.{RESET}")
    else:
        print(f"{GREEN}Ficheiro de configuração VPN detectado.{RESET}")

    print("")

    # 3. Configurações do Tailscale
    print(f"{BOLD}Passo 3: Configurar o Tailscale{RESET}")
    print("Para ligar automaticamente, é recomendada uma Auth Key.")
    print(f"Obtenha a chave em: {BLUE}https://login.tailscale.com/admin/settings/keys{RESET}")
    ts_key = input(f"{BLUE}Introduza a sua Tailscale Auth Key (opcional): {RESET}").strip()
    
    ts_hostname = input(f"{BLUE}Introduza o nome do dispositivo na Tailnet [protonvpn-bridge]: {RESET}").strip()
    if ts_hostname == "":
        ts_hostname = "protonvpn-bridge"

    print(f"{GREEN}Configurações do Tailscale registadas.{RESET}\n")

    # 4. Configurar e proteger a Web UI
    print(f"{BOLD}Passo 4: Configurar a Web UI{RESET}")
    webui_username = input(f"{BLUE}Utilizador da Web UI [admin]: {RESET}").strip() or "admin"
    webui_password = ""
    while not webui_password:
        webui_password = getpass.getpass(f"{BLUE}Palavra-passe da Web UI: {RESET}").strip()
        if not webui_password:
            print(f"{RED}A palavra-passe não pode ficar vazia.{RESET}")

    webui_protocol = input(f"{BLUE}Protocolo da Web UI (http/https) [http]: {RESET}").strip().lower() or "http"
    while webui_protocol not in ("http", "https"):
        webui_protocol = input(f"{BLUE}Escolha http ou https: {RESET}").strip().lower()

    webui_access = input(f"{BLUE}Acesso à Web UI (all/tailnet) [all]: {RESET}").strip().lower() or "all"
    while webui_access not in ("all", "tailnet"):
        webui_access = input(f"{BLUE}Escolha all ou tailnet: {RESET}").strip().lower()
    webui_bind_address = "0.0.0.0"
    if webui_access == "tailnet":
        webui_bind_address = input(
            f"{BLUE}IP Tailscale IPv4 do host (100.x.y.z): {RESET}"
        ).strip()
        if not webui_bind_address:
            print(f"{RED}Indique o IP Tailscale do host para restringir a porta.{RESET}")
            sys.exit(1)

    # 5. Criar o ficheiro .env
    print(f"{BOLD}Passo 5: Gerar o ficheiro de ambiente (.env){RESET}")
    if os.path.exists(".env") and not env_created:
        replace_env = input(f"{YELLOW}Já existe um .env. Substituí-lo? (s/N): {RESET}").strip().lower()
        if replace_env != "s":
            print(f"{YELLOW}Setup cancelado para preservar o .env existente.{RESET}")
            sys.exit(0)

    ts_authkey_file = ""
    if write_secret("ts_authkey", ts_key):
        ts_authkey_file = "/run/secrets/ts_authkey"
    proton_user_file = ""
    proton_password_file = ""
    if write_secret("protonvpn_user", proton_user):
        proton_user_file = "/run/secrets/protonvpn_user"
    if write_secret("protonvpn_password", proton_pass):
        proton_password_file = "/run/secrets/protonvpn_password"
    write_secret("webui_password", webui_password)

    env_content = f"""# Configurações geradas via Setup CLI
VPN_TYPE={vpn_type}
TS_AUTHKEY=
TS_AUTHKEY_FILE={ts_authkey_file}
TS_HOSTNAME={ts_hostname}
PROTONVPN_USER=
PROTONVPN_PASSWORD=
PROTONVPN_USER_FILE={proton_user_file}
PROTONVPN_PASSWORD_FILE={proton_password_file}
TS_EXTRA_ARGS=
WEBUI_PORT=8080
WEBUI_USERNAME={webui_username}
WEBUI_PASSWORD=
WEBUI_PASSWORD_FILE=/run/secrets/webui_password
WEBUI_PROTOCOL={webui_protocol}
WEBUI_ACCESS_MODE={webui_access}
WEBUI_BIND_ADDRESS={webui_bind_address}
WEBUI_CERT_FILE=/certs/fullchain.pem
WEBUI_KEY_FILE=/certs/privkey.pem
VPN_CHECK_INTERVAL=30
VPN_FAILURE_THRESHOLD=3
VPN_HEALTHCHECK_URL=https://api.ipify.org
 """
    with open(".env", "w") as f:
        f.write(env_content)
    try:
        os.chmod(".env", 0o600)
    except OSError:
        pass
    print(f"{GREEN}Ficheiro '.env' atualizado com sucesso.{RESET}\n")

    # 5. Perguntar se quer rodar o container agora
    print(f"{BOLD}Passo 6: Inicialização do Docker{RESET}")
    docker_installed = check_command("docker")
    compose_installed = check_command("docker-compose") or (docker_installed and subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0)

    if not docker_installed:
        print(f"{RED}Docker não detectado no sistema. Instale o Docker para correr este projeto.{RESET}")
        sys.exit(0)
    
    print(f"{GREEN}Docker detectado.{RESET}")
    
    run_now = input(f"\n{BLUE}Deseja construir e iniciar o container Docker agora? (s/N): {RESET}").strip().lower()
    if run_now == "s":
        print(f"\n{BLUE}A executar 'docker compose up -d --build'...{RESET}")
        try:
            # Tentar usar 'docker compose' e falhar para 'docker-compose' se necessário
            result = subprocess.run(["docker", "compose", "up", "-d", "--build"], check=False)
            if result.returncode != 0:
                print(f"{YELLOW}A tentar comando legado 'docker-compose'...{RESET}")
                subprocess.run(["docker-compose", "up", "-d", "--build"], check=True)
            
            print(f"\n{GREEN}==================================================================={RESET}")
            print(f"{GREEN}{BOLD}O container foi iniciado com sucesso.{RESET}")
            print(f"Para ver os logs do container, execute: {BOLD}docker compose logs -f{RESET}")
            print(f"Lembre-se de ir ao painel da Tailscale para {BOLD}aprovar o Exit Node{RESET}!")
            print(f"{GREEN}==================================================================={RESET}")
        except Exception as e:
            print(f"{RED}Erro ao tentar correr o Docker Compose: {e}{RESET}")
            print(f"Pode iniciar manualmente com: {BOLD}docker compose up -d --build{RESET}")
    else:
        print(f"\nConfiguração concluída! Para iniciar o container mais tarde, execute:")
        print(f"{BOLD}docker compose up -d --build{RESET}")

if __name__ == "__main__":
    main()
