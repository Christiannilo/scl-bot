#!/usr/bin/env python3
"""
IP Change Monitor — Checks public IP every 10 minutes.
If IP changes:
  1. Updates Pterodactyl panel allocations in MySQL (old IP → new IP)
  2. Updates Palworld PUBLIC_IP server variable
  3. Opens firewall ports for game servers on the new IP
  4. Restarts Wings (containers re-attach with new IP)
  5. Starts all game servers via Wings API
  6. Restarts the SCL bot (picks up new IP via get_public_ip())
  7. Sends Telegram alert with summary
"""
import urllib.request
import urllib.parse
import subprocess
import json
import time
import os
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ip-monitor")

IP_FILE = "/opt/scl-bot/current_ip.txt"
BOT_TOKEN = os.environ.get("SCL_BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.environ.get("SCL_ALERT_CHAT", "0") or 0)

# Wings API token (from /etc/pterodactyl/config.yml)
WINGS_TOKEN = os.environ.get("WINGS_TOKEN", "")
WINGS_API = "http://localhost:8081/api/servers"

# Node 3 (GamerPC VM) — IP is dynamic via VTR DHCP, detected via ARP scan
NODE3_MAC = "00:15:5d:56:38:00"  # Hyper-V VM MAC — fixed
NODE3_SSH_USER = "christian"
NODE3_SSH_PASS = "Yoplait.1337$"
NODE3_SERVER_UUID = "a816f6f9-9fd6-438a-80b8-1613db970d7e"  # KF2 GamerPC

# Game ports to open on firewall (port/proto pairs)
GAME_PORTS = [
    # Valheim
    (2456, "udp"), (2457, "udp"), (2456, "tcp"), (2457, "tcp"),
    # KF2
    (7777, "udp"), (27015, "udp"), (8082, "tcp"),
    # KF2 2
    (7778, "udp"), (27016, "udp"),
    # Mordhau
    (7787, "udp"), (27025, "udp"), (15001, "tcp"),
    # Enshrouded
    (15636, "udp"), (15637, "udp"), (15636, "tcp"), (15637, "tcp"),
    # Palworld
    (8211, "udp"), (8212, "udp"), (25575, "tcp"), (25575, "udp"),
    # Palworld 2
    (8213, "udp"), (8214, "udp"), (25576, "tcp"), (25576, "udp"),
    # Quake 2
    (27910, "udp"), (27911, "udp"),
    # PaperMC
    (25565, "tcp"), (25566, "tcp"),
    # Project Zomboid
    (16261, "tcp"), (16261, "udp"), (16262, "udp"),
    # VEIN
    (7779, "udp"), (7779, "tcp"), (27920, "udp"), (27920, "tcp"), (1985, "tcp"),
    # DayZ
    (2302, "udp"), (2302, "tcp"), (2305, "udp"), (2305, "tcp"), (27017, "udp"), (27017, "tcp"),
]

# Server UUIDs to start after Wings restart (node 2 only — main server)
SERVER_UUIDS = [
    "48261dfb-5c02-4630-af84-afa325b59b95",  # KF2
    "6047cf29-ebb3-47e1-bba7-a3b98e4e14d6",  # KF2 2
    "e804f1e6-c3e2-4a8d-90c3-bcdd5ae4b61e",  # Mordhau
    "6440e54b-b3ca-42a2-aaa3-4fa5e76745bb",  # Enshrouded
    "f8a39b9c-a4eb-40c9-a6ee-c225959b14a8",  # Palworld
    "42325324-4adc-4a6f-abc4-340f57a8b0e9",  # Palworld 2
    "c4eea955-0335-48f8-b1c1-659407435258",  # Quake 2
    "32e0bff9-e9bd-4258-bb0a-c3ab81eb93cd",  # Valheim
    "7052e797-3532-46bf-bb9d-7a19a69e2ef0",  # PaperMC
    # PZ e70aacdb removido 2026-09-21: suspendido (RAM/desuso), no auto-arrancar
    "a5481942-d449-4a60-9dfb-1c07f99f98ed",  # VEIN
    "b9944410-1c32-49ef-8897-4411838f3cff",  # DayZ
]

SERVER_NAMES = {
    "48261dfb": "KF2",
    "6047cf29": "KF2 2",
    "e804f1e6": "Mordhau",
    "6440e54b": "Enshrouded",
    "f8a39b9c": "Palworld",
    "42325324": "Palworld 2",
    "c4eea955": "Quake 2",
    "32e0bff9": "Valheim",
    "7052e797": "PaperMC",
    "e70aacdb": "Project Zomboid",
    "a5481942": "VEIN",
    "b9944410": "DayZ",
}


def get_public_ip():
    try:
        with urllib.request.urlopen('https://api.ipify.org', timeout=10) as r:
            return r.read().decode().strip()
    except:
        return None


def run(cmd, timeout=30):
    """Run a shell command, return (success, output)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)


def detect_node3_ip():
    """Find Node3's current IP by ARP-scanning the WAN subnet for its MAC."""
    ok, out = run("arp-scan -I br-wan 200.83.64.0/24 2>/dev/null | grep -i " + NODE3_MAC, timeout=10)
    if ok and out:
        # Output format: "IP\tMAC\tVendor"
        parts = out.strip().split()
        if parts:
            return parts[0]
    # Fallback: try old IP
    return None


def update_node3(old_node3_ip, new_node3_ip):
    """Update Node3: panel allocations, FQDN, Wings config, firewall rich rule, restart Wings."""
    log.info(f"Updating Node3: {old_node3_ip} → {new_node3_ip}")
    # Update allocations in panel DB
    run(f'mysql -u root panel -e "UPDATE allocations SET ip=\'{new_node3_ip}\' WHERE ip=\'{old_node3_ip}\';"')
    # Update node FQDN
    run(f'mysql -u root panel -e "UPDATE nodes SET fqdn=\'{new_node3_ip}\' WHERE name=\'scl-ubuntu-02\';"')
    # Update firewall rich rule on main server
    run(f'firewall-cmd --zone=external --remove-rich-rule=\'rule family="ipv4" source address="{old_node3_ip}" port port="443" protocol="tcp" accept\' --permanent 2>/dev/null')
    run(f'firewall-cmd --zone=external --add-rich-rule=\'rule family="ipv4" source address="{new_node3_ip}" port port="443" protocol="tcp" accept\' --permanent')
    run('firewall-cmd --reload')
    # Update Wings config on Node3 and restart
    run(f'sshpass -p "{NODE3_SSH_PASS}" ssh -o StrictHostKeyChecking=no {NODE3_SSH_USER}@{new_node3_ip} '
        f'"sudo sed -i \'s|remote: https://.*:443|remote: https://{new_node3_ip}:443|\' /etc/pterodactyl/config.yml; '
        f'sudo systemctl restart wings"')
    # Update bot.py KF2PC host
    run(f"sed -i 's/{old_node3_ip}/{new_node3_ip}/g' /opt/scl-bot/bot.py")
    # Update daily-summary.py remote_log_host
    run(f"sed -i 's/{old_node3_ip}/{new_node3_ip}/g' /opt/scl-bot/daily-summary.py")
    log.info("Node3 updated successfully")


def update_panel_allocations(old_ip, new_ip):
    """Update all Pterodactyl allocations from old IP to new IP."""
    log.info(f"Updating panel allocations: {old_ip} → {new_ip}")
    # Idempotente: normalizar CUALQUIER IP vieja, no solo la del archivo.
    # (Bug 2026-09-09: el UPDATE WHERE ip=old_ip no encontró filas porque el
    # panel quedaba con una IP aún más antigua, y fallaba en silencio.)
    ok, out = run(f'mysql -u root panel -e "UPDATE allocations SET ip=\'{new_ip}\' WHERE node_id = 2 AND ip != \'{new_ip}\'; SELECT ROW_COUNT();"')
    if not ok:
        log.error(f"Failed to update allocations: {out}")
        return False
    log.info(f"Allocations updated: {out.strip().splitlines()[-1] if out else '?'} rows")

    # Also update Palworld PUBLIC_IP server variable
    ok, out = run(
        f'mysql -u root panel -e '
        f'"UPDATE server_variables sv '
        f'JOIN egg_variables ev ON sv.variable_id = ev.id '
        f'SET sv.variable_value=\'{new_ip}\' '
        f'WHERE ev.env_variable=\'PUBLIC_IP\' AND sv.variable_value=\'{old_ip}\';"'
    )
    if ok:
        log.info("Palworld PUBLIC_IP updated")
    else:
        log.warning(f"Failed to update Palworld PUBLIC_IP: {out}")

    return True


def update_firewall(new_ip):
    """Open all game ports on the firewall for the new IP."""
    log.info("Opening firewall ports for game servers")
    opened = 0
    for port, proto in GAME_PORTS:
        ok, _ = run(f"firewall-cmd --zone=public --add-port={port}/{proto} --permanent")
        if ok:
            opened += 1
    run("firewall-cmd --reload")
    log.info(f"Opened {opened}/{len(GAME_PORTS)} firewall ports")


def restart_wings_and_servers():
    """Restart Wings, wait for it to be ready, then start all game servers."""
    log.info("Stopping all running game containers")
    # FIX 2026-09-21: solo contenedores de Pterodactyl (label). Antes era
    # 'docker ps -q' a secas y mataba TODO Docker (firecrawl, etc.) en cada
    # cambio de IP. Los game servers llevan label ContainerType=server_process.
    run(
        'docker ps -q --filter "label=ContainerType=server_process" '
        "| xargs -I{} docker stop {} 2>/dev/null",
        timeout=120,
    )

    log.info("Restarting Wings")
    run("systemctl restart wings", timeout=30)
    time.sleep(10)

    ok, _ = run("systemctl is-active wings")
    if not ok:
        log.error("Wings failed to start!")
        return False

    log.info("Starting all game servers via Wings API")
    for uuid in SERVER_UUIDS:
        short = uuid[:8]
        name = SERVER_NAMES.get(short, short)
        ok, out = run(
            f'curl -s -X POST "{WINGS_API}/{uuid}/power" '
            f'-H "Authorization: Bearer {WINGS_TOKEN}" '
            f'-H "Content-Type: application/json" '
            f'-d \'{{"action":"start"}}\'',
            timeout=15
        )
        if ok:
            log.info(f"  ✅ {name}: start signal sent")
        else:
            log.warning(f"  ⚠️ {name}: {out}")

    return True


def restart_bot():
    """Restart the SCL Telegram bot."""
    log.info("Restarting SCL bot")
    ok, out = run("systemctl restart scl-bot", timeout=15)
    if ok:
        log.info("Bot restarted")
    else:
        log.error(f"Failed to restart bot: {out}")


def send_telegram_alert(old_ip, new_ip, steps):
    """Send detailed alert via Telegram bot API."""
    steps_text = "\n".join(f"  {'✅' if s else '❌'} {n}" for n, s in steps)
    message = (
        f"⚠️ *Cambio de IP Detectado — Auto-recuperación*\n\n"
        f"IP anterior: `{old_ip}`\n"
        f"IP nueva: `{new_ip}`\n\n"
        f"Acciones ejecutadas:\n{steps_text}\n\n"
        f"Los servidores deberían estar en línea en ~5 min.\n"
        f"Usa /status para verificar."
    )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": ADMIN_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }).encode()
    try:
        with urllib.request.urlopen(url, data, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        log.error(f"Failed to send alert: {e}")
        return False


def main():
    current_ip = get_public_ip()
    if not current_ip:
        log.error("Failed to get public IP, skipping check")
        return

    ip_file = Path(IP_FILE)
    if ip_file.exists():
        old_ip = ip_file.read_text().strip()
        if old_ip != current_ip:
            log.info(f"IP changed: {old_ip} -> {current_ip}")
            log.info("Starting automatic recovery procedure...")

            steps = []
            # Step 1: Update panel allocations
            steps.append(("Panel DB allocations actualizadas", update_panel_allocations(old_ip, current_ip)))

            # Step 2: Open firewall ports
            ok_fw = True
            try:
                update_firewall(current_ip)
            except Exception as e:
                ok_fw = False
                log.error(f"Firewall update failed: {e}")
            steps.append(("Firewall ports abiertos", ok_fw))

            # Step 3: Restart Wings + start servers
            ok_wings = True
            try:
                ok_wings = restart_wings_and_servers()
            except Exception as e:
                ok_wings = False
                log.error(f"Wings restart failed: {e}")
            steps.append(("Wings reiniciado + servidores iniciados", ok_wings))

            # Step 4: Restart bot
            ok_bot = True
            try:
                restart_bot()
            except Exception as e:
                ok_bot = False
                log.error(f"Bot restart failed: {e}")
            steps.append(("Bot reiniciado", ok_bot))

            # Step 5: Send alert
            send_telegram_alert(old_ip, current_ip, steps)
            log.info("Automatic recovery complete. Alert sent.")

            # Step 6: Check Node3 IP (GamerPC VM — separate DHCP on WAN)
            node3_ip = detect_node3_ip()
            if node3_ip:
                # Read last known Node3 IP from file
                node3_file = Path("/opt/scl-bot/node3_ip.txt")
                if node3_file.exists():
                    old_node3 = node3_file.read_text().strip()
                    if old_node3 != node3_ip:
                        log.info(f"Node3 IP changed: {old_node3} -> {node3_ip}")
                        try:
                            update_node3(old_node3, node3_ip)
                            log.info("Node3 recovery complete")
                        except Exception as e:
                            log.error(f"Node3 update failed: {e}")
                else:
                    log.info(f"First Node3 detection: {node3_ip}")
                node3_file.write_text(node3_ip)
            else:
                log.warning("Could not detect Node3 IP — PC Juegos may be off")
        else:
            log.info(f"IP unchanged: {current_ip}")
    else:
        log.info(f"First run, saving IP: {current_ip}")

    ip_file.write_text(current_ip)


if __name__ == "__main__":
    main()