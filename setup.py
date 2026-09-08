#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess

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
     Tailscale - Proton VPN Bridge Exit Node Setup CLI 🌐🛡️
==================================================================={RESET}
Este script irá guiá-lo na configuração da ponte para que o seu
tráfego Tailscale seja encaminhado de forma segura pelo Proton VPN.
"""
    print(banner)

def create_directories():
    print(f"{BLUE}A criar pastas de configuração...{RESET}")
    os.makedirs(os.path.join("vpn", "wireguard"), exist_ok=True)
    os.makedirs(os.path.join("vpn", "openvpn"), exist_ok=True)
    print(f"{GREEN}✓ Pastas './vpn/wireguard' e './vpn/openvpn' criadas.{RESET}")
    
    # Garantir que o ficheiro .env existe antes do Docker Compose para evitar que seja montado como diretório
    if not os.path.exists(".env") and os.path.exists(".env.example"):
        shutil.copyfile(".env.example", ".env")
        print(f"{GREEN}✓ Ficheiro base '.env' criado a partir de '.env.example'.{RESET}")
    print("")

def check_command(cmd):
    return shutil.which(cmd) is not None

def main():
    print_banner()
    create_directories()

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
    print(f"{GREEN}✓ Protocolo escolhido: {vpn_type.upper()}{RESET}\n")

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
        print(f"\n{YELLOW}⚠️  Aviso: Não encontrei o ficheiro de configuração em: {config_path}{RESET}")
        create_empty = input(f"{BLUE}Deseja criar um ficheiro vazio temporário para avançar o setup? (s/N): {RESET}").strip().lower()
        if create_empty == "s":
            with open(config_path, "w") as f:
                f.write("# Substitua este conteúdo pelo ficheiro real da Proton VPN")
            print(f"{GREEN}✓ Ficheiro vazio temporário criado em: {config_path}{RESET}")
        else:
            print(f"{RED}Por favor, coloque o ficheiro real na pasta antes de iniciar o container.{RESET}")
    else:
        print(f"{GREEN}✓ Ficheiro de configuração VPN detectado!{RESET}")

    print("")

    # 3. Configurações do Tailscale
    print(f"{BOLD}Passo 3: Configurar o Tailscale{RESET}")
    print("Para ligar automaticamente, é recomendada uma Auth Key.")
    print(f"Obtenha a chave em: {BLUE}https://login.tailscale.com/admin/settings/keys{RESET}")
    ts_key = input(f"{BLUE}Introduza a sua Tailscale Auth Key (opcional): {RESET}").strip()
    
    ts_hostname = input(f"{BLUE}Introduza o nome do dispositivo na Tailnet [protonvpn-bridge]: {RESET}").strip()
    if ts_hostname == "":
        ts_hostname = "protonvpn-bridge"

    print(f"{GREEN}✓ Configurações do Tailscale registadas.{RESET}\n")

    # 4. Criar o ficheiro .env
    print(f"{BOLD}Passo 4: Gerar o ficheiro de ambiente (.env){RESET}")
    env_content = f"""# Configurações geradas via Setup CLI
VPN_TYPE={vpn_type}
TS_AUTHKEY={ts_key}
TS_HOSTNAME={ts_hostname}
PROTONVPN_USER={proton_user}
PROTONVPN_PASSWORD={proton_pass}
TS_EXTRA_ARGS=
WEBUI_PORT=8080
WEBUI_USERNAME=admin
WEBUI_PASSWORD=
"""
    with open(".env", "w") as f:
        f.write(env_content)
    print(f"{GREEN}✓ Ficheiro '.env' atualizado com sucesso!{RESET}\n")

    # 5. Perguntar se quer rodar o container agora
    print(f"{BOLD}Passo 5: Inicialização do Docker{RESET}")
    docker_installed = check_command("docker")
    compose_installed = check_command("docker-compose") or (docker_installed and subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0)

    if not docker_installed:
        print(f"{RED}❌ Docker não detectado no sistema. Por favor, instale o Docker para poder correr este projeto.{RESET}")
        sys.exit(0)
    
    print(f"{GREEN}✓ Docker detectado.{RESET}")
    
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
            print(f"{GREEN}{BOLD}✓ O container foi iniciado com sucesso!{RESET}")
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
