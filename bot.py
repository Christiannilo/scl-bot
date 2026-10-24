#!/usr/bin/env python3
"""
SCL San Enrique - Game Server Telegram Bot
Commands: /status, /players, /ranking, /connect, /novedades, /changelog, /help
"""

import os
import json
import re
import socket
import struct
import logging
import subprocess
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)

# --- Config ---
BOT_TOKEN = os.environ.get("SCL_BOT_TOKEN", "")
ALLOWED_CHAT_IDS = set(int(x) for x in os.environ.get("SCL_ALLOWED_CHATS", "").split(",") if x.strip())
ADMIN_IDS = set(int(x) for x in os.environ.get("SCL_ADMINS", "").split(",") if x.strip())

# Dynamic IP detection
import urllib.request

def get_public_ip():
    """Get current public IP from ipify."""
    try:
        with urllib.request.urlopen('https://api.ipify.org', timeout=5) as r:
            return r.read().decode().strip()
    except:
        # Fallback: read from ip-monitor's file, or last known
        try:
            with open("/opt/scl-bot/current_ip.txt") as f:
                return f.read().strip()
        except:
            return "127.0.0.1"

def get_docker_container_ip_by_port(port):
    """Find the Docker container IP that has the given port published.
    Returns the container's internal IP (172.18.x.x) or None."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.ID}}\t{{.Ports}}"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            cid, ports_str = parts
            # Docker port format: IP:HOSTPORT->CONTAINERPORT/proto
            # Port can be a range: IP:8211-8212->8211-8212/tcp
            # Check if our port appears as the start of a host port range
            for token in ports_str.split(", "):
                # Extract host port part (between IP: and ->)
                if "->" not in token:
                    continue
                host_part = token.split("->")[0]  # e.g. "200.83.64.230:8211-8212"
                if ":" not in host_part:
                    continue
                host_ports = host_part.split(":")[1]  # e.g. "8211-8212"
                # Check if port matches (single port or within range)
                if "-" in host_ports:
                    p_start, p_end = host_ports.split("-")
                    if int(p_start) <= port <= int(p_end):
                        match = True
                    else:
                        match = False
                else:
                    match = (host_ports == str(port))
                if match:
                    ip_result = subprocess.run(
                        ["docker", "inspect", cid,
                         "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
                        capture_output=True, text=True, timeout=5
                    )
                    ip = ip_result.stdout.strip()
                    if ip:
                        return ip
    except Exception:
        pass
    return None

# Cache for Docker IP lookups (refreshed every query cycle)
_docker_ip_cache = {}

def resolve_host(host, port=None):
    """Resolve 'auto' to the Docker container IP when querying from the same host.
    Falls back to public IP if Docker IP lookup fails."""
    if host == "auto":
        # Try Docker internal IP first (avoids NAT loopback issues)
        if port and port in _docker_ip_cache:
            return _docker_ip_cache[port]
        if port:
            docker_ip = get_docker_container_ip_by_port(port)
            if docker_ip:
                _docker_ip_cache[port] = docker_ip
                return docker_ip
        return get_public_ip()
    return host

# Game servers: name, host, query_port, type, connect_info
# Game ports (internal VM IP for A2S queries)
GAME_HOST = get_public_ip()  # Dynamic — survives IP changes. ip-monitor.py restarts bot on change.
# Ports for A2S queries
SERVERS = [
    {"name": "Killing Floor 2", "short": "KF2", "host": "auto", "port": 27015, "type": "source", "emoji": "💀",
     "game_port": 7777,
     "notes": "También puedes buscar \"SCL_San_Enrique\" en el server browser de KF2"},
    {"name": "Killing Floor 2 2", "short": "KF22", "host": "auto", "port": 27016, "type": "source", "emoji": "💀",
     "game_port": 7778,
     "notes": "Segunda instancia KF2 — buscar \"SCL_San_Enrique KF2 2\" o IP:7778"},
    {"name": "Mordhau", "short": "Mordhau", "host": "auto", "port": 27025, "type": "source", "emoji": "⚔️",
     "game_port": 7787,
     "notes": "Buscar \"SCL_San_Enrique\" en el server browser de Mordhau"},
    {"name": "Enshrouded", "short": "Enshrouded", "host": "auto", "port": 15636, "type": "enshrouded", "emoji": "🏔️",
     "game_port": 15636,
     "slot_count": 6,
     "notes": "Enshrouded usa búsqueda directa por IP en el menú del juego"},
    {"name": "Palworld", "short": "Palworld", "host": "auto", "port": 8211, "type": "palworld", "emoji": "🐾",
     "game_port": 8211,
     "slot_count": 10,
     "rcon_port": 25575,
     "notes": "Buscar \"SCL_San_Enrique\" o conectarse por IP:8211"},
    {"name": "Palworld 2", "short": "Palworld2", "host": "auto", "port": 8213, "type": "palworld", "emoji": "🐾",
     "game_port": 8213,
     "slot_count": 10,
     "rcon_port": 25576,
     "notes": "Segunda instancia — buscar \"SCL_San_Enrique Palworld 2\" o IP:8213"},
    {"name": "Quake 2", "short": "Q2", "host": "auto", "port": 27910, "type": "q2", "emoji": "⚽",
     "game_port": 27910,
     "notes": "Kick mod (futbol)! Buscar \"SCL_San_Enrique\" en el server browser"},
    {"name": "Valheim", "short": "Valheim", "host": "auto", "port": 2457, "type": "valheim", "emoji": "⛵",
     "game_port": 2456, "slot_count": 6,
     "notes": "Crossplay activado — busca \"SCL_San_Enrique\" o usa join code"},
    {"name": "Paper MC", "short": "PaperMC", "host": "auto", "port": 25565, "type": "minecraft", "emoji": "⛏️",
     "game_port": 25565, "slot_count": 6,
     "notes": "Servidor Minecraft Paper — agrega la IP pública:25565 en Multiplayer"},
    {"name": "Project Zomboid", "short": "PZ", "host": "auto", "port": 16261, "type": "source", "emoji": "🧟",
     "game_port": 16261, "slot_count": 16,
     "notes": "Buscar \"SCL_San_Enrique\" en el server browser de Project Zomboid o conectar por IP:16261"},
    {"name": "DayZ", "short": "dayz", "host": "auto", "port": 27017, "type": "source", "emoji": "🧟‍♂️",
     "game_port": 2302, "slot_count": 20,
     "notes": "Buscar \"SCL_San_Enrique\" en el launcher de DayZ o conectar por IP:2302 (query 27017)"},
]

LOG_DIR_KF2 = "/home/kf2server/kf2-logs"
LOG_DIR_GAMES = "/home/kf2server/server-logs"
CHANGELOG_FILE = "/opt/scl-bot/changelog.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("scl-bot")

async def reply_md(update, text):
    """Reply with Markdown. If parsing fails, send as plain text (no duplicate)."""
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception:
        # Strip markdown formatting for plain text
        import re as _re
        plain = _re.sub(r'\*([^*]+)\*', r'\1', text)   # *bold* -> bold
        plain = _re.sub(r'_([^_]+)_', r'\1', plain)     # _italic_ -> italic
        plain = _re.sub(r'`([^`]+)`', r'\1', plain)      # `code` -> code
        await update.message.reply_text(plain)

def md_escape(text):
    """Escape special Markdown v1 characters."""
    if not text:
        return text
    for ch in ['\\', '_', '*', '[', '`']:
        text = text.replace(ch, '\\' + ch)
    return text

def sanitize_name(name):
    """Keep only alphanumeric characters from player names."""
    import re as _re
    return _re.sub(r'[^a-zA-Z0-9]', '', name)


# --- Changelog ---
def load_changelog():
    """Load changelog entries from JSON file."""
    path = Path(CHANGELOG_FILE)
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Failed to load changelog: {e}")
        return []

def save_changelog(entries):
    """Save changelog entries to JSON file."""
    path = Path(CHANGELOG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


# --- Enshrouded Docker Logs Query ---
ENSHROUDED_CONTAINER = "6440e54b"

def enshrouded_online_players():
    """Get currently online players from docker logs using state + recent events.
    A2S doesn't report players for Enshrouded."""
    from collections import Counter
    STATE_FILE = "/home/kf2server/server-logs/enshrouded-scraper-state.json"
    
    # Load previous state
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            known_online = set(state.get("online_players", []))
    except (FileNotFoundError, json.JSONDecodeError):
        known_online = set()
    
    cid_result = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"name={ENSHROUDED_CONTAINER}"],
        capture_output=True, text=True, timeout=5
    )
    cid = cid_result.stdout.strip()
    if not cid:
        # Container not running - clear state
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump({"online_players": []}, f)
        except Exception:
            pass
        return None
    
    try:
        result = subprocess.run(
            ["docker", "logs", "--since", "5m", cid],
            capture_output=True, text=True, timeout=10
        )
        log_text = result.stdout + result.stderr
    except Exception:
        return None
    
    logged_in = set()
    logged_out = set()
    for line in log_text.split('\n'):
        m = re.search(r"\[server\] Player '([^']+)' logged in", line)
        if m:
            logged_in.add(m.group(1))
        m = re.search(r"\[server\] Remove Player '([^']+)'", line)
        if m:
            logged_out.add(m.group(1))
    
    # Apply recent events to known state
    known_online.update(logged_in)
    known_online -= logged_out
    
    result_list = sorted(known_online)
    
    # Save state
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump({"online_players": result_list}, f)
    except Exception:
        pass
    
    return result_list

VALHEIM_CONTAINER = "32e0bff9-e9bd-4258-bb0a-c3ab81eb93cd"

# Minecraft Server List Ping (SLP) protocol
def mc_status_ping(host, port, timeout=5):
    """Query Minecraft server using SLP protocol."""
    try:
        import json as json_mod
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        
        protocol_version = 47
        handshake_id = b'\x00'
        version = _encode_varint_mc(protocol_version)
        address = _encode_string_mc(host)
        port_bytes = struct.pack('>H', port)
        state = _encode_varint_mc(1)
        handshake_data = handshake_id + version + address + port_bytes + state
        handshake_packet = _encode_varint_mc(len(handshake_data)) + handshake_data
        
        status_data = b'\x00'
        status_packet = _encode_varint_mc(len(status_data)) + status_data
        
        sock.sendall(handshake_packet + status_packet)
        
        # Read response
        response = _read_mc_packet(sock)
        sock.close()
        
        if response is None:
            return None
        
        data = json_mod.loads(response)
        
        desc = data.get('description', {})
        name = desc.get('text', '') if isinstance(desc, dict) else str(desc)
        
        return {
            'name': name,
            'players': data.get('players', {}).get('online', 0),
            'max_players': data.get('players', {}).get('max', 0),
            'map': 'Minecraft',
            'version': data.get('version', {}).get('name', 'unknown'),
        }
    except Exception:
        return None

def _encode_varint_mc(value):
    result = b''
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        result += bytes([byte])
        if not value:
            break
    return result

def _encode_string_mc(s):
    encoded = s.encode('utf-8')
    return _encode_varint_mc(len(encoded)) + encoded

def _read_mc_packet(sock):
    """Read a Minecraft protocol packet from socket."""
    length = 0
    for i in range(5):
        byte = sock.recv(1)
        if not byte:
            return None
        byte = byte[0]
        length |= (byte & 0x7F) << (7 * i)
        if not (byte & 0x80):
            break
    
    if length == 0:
        return None
    
    # Read packet ID
    packet_id_data = b''
    for i in range(5):
        byte = sock.recv(1)
        if not byte:
            return None
        byte_val = byte[0]
        packet_id_data += byte
        if not (byte_val & 0x80):
            break
    
    remaining = length - len(packet_id_data)
    data = b''
    while len(data) < remaining:
        chunk = sock.recv(remaining - len(data))
        if not chunk:
            break
        data += chunk
    
    str_len = 0
    pos = 0
    for i in range(5):
        if pos >= len(data):
            return None
        byte = data[pos]
        pos += 1
        str_len |= (byte & 0x7F) << (7 * i)
        if not (byte & 0x80):
            break
    
    json_str = data[pos:pos+str_len].decode('utf-8')
    return json_str

# Minecraft Server List Ping (SLP) protocol
def mc_status_ping(host, port, timeout=5):
    """Query Minecraft server using SLP protocol."""
    try:
        import json as json_mod
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        
        protocol_version = 47
        handshake_id = b'\x00'
        version = _mc_encode_varint(protocol_version)
        address = _mc_encode_string(host)
        port_bytes = struct.pack('>H', port)
        state = _mc_encode_varint(1)
        handshake_data = handshake_id + version + address + port_bytes + state
        handshake_packet = _mc_encode_varint(len(handshake_data)) + handshake_data
        
        status_data = b'\x00'
        status_packet = _mc_encode_varint(len(status_data)) + status_data
        
        sock.sendall(handshake_packet + status_packet)
        
        response = _mc_read_packet(sock)
        sock.close()
        
        if response is None:
            return None
        
        data = json_mod.loads(response)
        
        desc = data.get('description', {})
        name = desc.get('text', '') if isinstance(desc, dict) else str(desc)
        
        return {
            'name': name,
            'players': data.get('players', {}).get('online', 0),
            'max_players': data.get('players', {}).get('max', 0),
            'map': 'Minecraft',
            'version': data.get('version', {}).get('name', 'unknown'),
        }
    except Exception:
        return None

def _mc_encode_varint(value):
    result = b''
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        result += bytes([byte])
        if not value:
            break
    return result

def _mc_encode_string(s):
    encoded = s.encode('utf-8')
    return _mc_encode_varint(len(encoded)) + encoded

def _mc_read_packet(sock):
    """Read a Minecraft protocol packet from socket."""
    length = 0
    for i in range(5):
        byte = sock.recv(1)
        if not byte:
            return None
        byte = byte[0]
        length |= (byte & 0x7F) << (7 * i)
        if not (byte & 0x80):
            break
    
    if length == 0:
        return None
    
    # Read packet ID
    packet_id_data = b''
    for i in range(5):
        byte = sock.recv(1)
        if not byte:
            return None
        byte_val = byte[0]
        packet_id_data += byte
        if not (byte_val & 0x80):
            break
    
    remaining = length - len(packet_id_data)
    data = b''
    while len(data) < remaining:
        chunk = sock.recv(remaining - len(data))
        if not chunk:
            break
        data += chunk
    
    str_len = 0
    pos = 0
    for i in range(5):
        if pos >= len(data):
            return None
        byte = data[pos]
        pos += 1
        str_len |= (byte & 0x7F) << (7 * i)
        if not (byte & 0x80):
            break
    
    json_str = data[pos:pos+str_len].decode('utf-8')
    return json_str

def valheim_status_fallback():
    """Get Valheim status from docker logs when A2S fails (e.g. crossplay enabled)."""
    cid_result = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"name={VALHEIM_CONTAINER}"],
        capture_output=True, text=True, timeout=5
    )
    cid = cid_result.stdout.strip()
    if not cid:
        return None  # Container not running

    try:
        result = subprocess.run(
            ["docker", "logs", "--since", "2m", cid],
            capture_output=True, text=True, timeout=10
        )
        log_text = result.stdout + result.stderr
    except Exception:
        return None

    # Look for "is active" line which confirms server is up
    for line in log_text.split('\n'):
        if 'is active' in line and 'player' in line:
            m = re.search(r'(\d+)\s*player', line)
            players = int(m.group(1)) if m else 0
            return {
                "name": "SCL_San_Enrique",
                "map": "Valheim",
                "game": "valheim",
                "players": players,
                "max_players": 6
            }

    # Container running but no active line yet — still booting
    return None

# --- A2S Query ---
def palworld_status(game_port=8211, rcon_port=25575, name="Palworld"):
    """Check Palworld server status via docker logs."""
    # Find Palworld container dynamically by published game port
    cid = None
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.ID}}\t{{.Ports}}"],
            capture_output=True, text=True, timeout=5
        )
        port_str = str(game_port)
        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) == 2 and (f":{port_str}->" in parts[1] or f":{port_str}-" in parts[1] and f"->{port_str}" in parts[1]):
                cid = parts[0]
                break
    except:
        pass
    if not cid:
        return None
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", "20", cid],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr
        # Check if server is running
        if "Running Palworld dedicated server" in output or "Setting breakpad" in output:
            # Try to get player count from RCON
            players = 0
            try:
                rcon_result = subprocess.run(
                    ["docker", "exec", cid,
                     "bash", "-c", f"echo 'showplayers' | nc -w 2 localhost {rcon_port} 2>/dev/null || true"],
                    capture_output=True, text=True, timeout=5
                )
                # Parse RCON output if available
                if rcon_result.stdout.strip():
                    lines = [l for l in rcon_result.stdout.strip().split('\n') if l.strip() and ',' in l]
                    players = max(0, len(lines) - 1)  # subtract header
            except:
                pass
            return {
                "name": name,
                "map": "",
                "game": "Palworld",
                "players": players,
                "max_players": 10,
                "online": True,
            }
    except Exception:
        pass
    return None

def a2s_info(host, port, timeout=5):
    """Query A2S_INFO using quakestat."""
    try:
        result = subprocess.run(
            ["quakestat", "-a2s", f"{host}:{port}", "-R"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.split('\n'):
            # Parse header line: ADDRESS PLAYERS MAP RESPONSE TIME NAME
            # e.g.: 200.83.64.230:27015     0/6   0/0  KF-BIOTICSLAB     15 / 0  kf2 SCL_San_Enrique
            m = re.search(r'(\d+)/(\d+)\s+(\d+)/(\d+)\s+(\S+)', line)
            if m:
                players = int(m.group(1))
                max_players = int(m.group(2))
                bots = int(m.group(3))
                mapname = m.group(5)
                # Extract server name (last column)
                parts = line.split()
                name = ' '.join(parts[7:]) if len(parts) > 7 else 'Unknown'
                return {"name": name, "map": mapname, "game": "", "players": players, "max_players": max_players}
            # Try without bots column
            m = re.search(r'(\d+)/(\d+)\s+(\S+)', line)
            if m:
                players = int(m.group(1))
                max_players = int(m.group(2))
                mapname = m.group(3)
                parts = line.split()
                name = ' '.join(parts[5:]) if len(parts) > 5 else 'Unknown'
                return {"name": name, "map": mapname, "game": "", "players": players, "max_players": max_players}
        return None
    except Exception as e:
        log.warning(f"quakestat A2S query failed for {host}:{port}: {e}")
        return None

def q2_info(host, port, timeout=5):
    """Query Q2Pro server using native Quake 2 status protocol."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        server = (host, port)
        sock.sendto(b'\xff\xff\xff\xffstatus', server)
        data, addr = sock.recvfrom(4096)
        sock.close()
        if not data or not data.startswith(b'\xff\xff\xff\xffprint\n'):
            return None
        text = data[len(b'\xff\xff\xff\xffprint\n'):].decode('utf-8', errors='replace')
        parts = text.split('\n', 1)
        info_str = parts[0]
        info = {}
        tokens = [t for t in info_str.split('\\') if t]
        i = 0
        while i < len(tokens) - 1:
            key = tokens[i]
            value = tokens[i + 1]
            info[key] = value
            i += 2
        players = 0
        if len(parts) > 1:
            for line in parts[1].strip().split('\n'):
                line = line.strip()
                if line and line[0].lstrip('-').isdigit():
                    players += 1
        result = {"name": info.get('hostname', 'unknown'), "map": info.get('mapname', 'unknown'),
                "game": info.get('gamedir', info.get('game', 'baseq2')), "players": players,
                "max_players": int(info.get('maxclients', '0'))}
        # Kick mod info
        if 'kck_gametype' in info:
            result['kick_mode'] = True
            result['kck_gametype'] = info.get('kck_gametype', '0')
            result['kck_scorelimit'] = info.get('kck_scorelimit', '0')
            gametypes = {'0': 'DM', '1': 'TDM', '2': 'Football', '3': 'TDM no goals', '4': 'TDM+goals'}
            result['kick_gametype_name'] = gametypes.get(info.get('kck_gametype', '0'), 'Unknown')
        else:
            result['kick_mode'] = False
        return result
    except Exception as e:
        log.warning(f"Q2 query failed for {host}:{port}: {e}")
        return None

def a2s_players(host, port, timeout=5):
    """Query A2S_PLAYER using quakestat."""
    try:
        result = subprocess.run(
            ["quakestat", "-a2s", f"{host}:{port}", "-P"],
            capture_output=True, text=True, timeout=10
        )
        players = []
        for line in result.stdout.split('\n'):
            # quakestat -P player lines: \t<score> frags    <duration> <name>
            # e.g.: "\t178 frags    20m41s scheletz"
            if not line.startswith('\t'):
                continue
            line = line.strip()
            if not line:
                continue
            # Match: score, "frags", duration, name
            m = re.match(r'(-?\d+)\s+frags\s+(\S+)\s+(.+)', line)
            if m:
                score = int(m.group(1))
                duration_str = m.group(2)
                name = m.group(3).strip()
                # Parse duration like "20m41s" or "1h15m" or "45s"
                duration = 0
                dm = re.match(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?', duration_str)
                if dm:
                    h = int(dm.group(1) or 0)
                    mi = int(dm.group(2) or 0)
                    s = int(dm.group(3) or 0)
                    duration = h * 3600 + mi * 60 + s
                players.append({"name": name, "score": score, "duration": duration})
        return players
    except Exception as e:
        log.warning(f"quakestat A2S players query failed for {host}:{port}: {e}")
        return []

# --- KF2 WebAdmin Scraper ---
def get_kf2_details():
    """Get detailed KF2 info from WebAdmin."""
    try:
        import urllib.request
        url = f"http://{get_public_ip()}:8082/ServerAdmin/current/info"
        import base64
        credentials = base64.b64encode(b"admin:cld94hucc3CMNpKmc9t3").decode()
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {credentials}", "User-Agent": "SCL-Bot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('utf-8', errors='replace')
        import re
        map_m = re.search(r'<dd class="gs_map">(.*?)</dd>', html)
        wave_m = re.search(r'<dd class="gs_wave">(.*?)</dd>', html)
        pc_m = re.search(r'<dd class="gs_players">(.*?)</dd>', html)
        result = {}
        if map_m: result['map'] = map_m.group(1).strip()
        if wave_m: result['wave'] = wave_m.group(1).strip()
        if pc_m: result['players_str'] = pc_m.group(1).strip()
        # Parse player table
        rows = re.findall(r'<tr class="(even|odd)">(.*?)</tr>', html, re.DOTALL)
        players = []
        for cls, row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 7:
                name = re.sub(r'<[^>]+>', '', cells[1]).strip()
                perk = re.sub(r'<[^>]+>', '', cells[2]).strip()
                kills = re.sub(r'<[^>]+>', '', cells[5]).strip()
                if name:
                    players.append({"name": name, "perk": perk, "kills": kills})
        result['players_detail'] = players
        return result
    except Exception as e:
        log.warning(f"KF2 WebAdmin query failed: {e}")
        return None

# --- Daily Log Parsing ---
def parse_daily_log(game, date_str=None):
    """Parse daily scraper logs for ranking data."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    if game == "kf2":
        logfile = Path(LOG_DIR_KF2) / f"players-{date_str}.log"
    elif game == "mordhau":
        logfile = Path(LOG_DIR_GAMES) / f"mordhau-{date_str}.log"
    elif game == "enshrouded":
        logfile = Path(LOG_DIR_GAMES) / f"enshrouded-{date_str}.log"
    elif game == "papermc":
        logfile = Path(LOG_DIR_GAMES) / f"paper-mc-{date_str}.log"
    elif game == "q2":
        logfile = Path(LOG_DIR_GAMES) / f"quake2-{date_str}.log"
    elif game == "valheim":
        logfile = Path(LOG_DIR_GAMES) / f"valheim-{date_str}.log"
    else:
        return None
    
    if not logfile.exists():
        return None
    
    import re
    from collections import defaultdict
    
    data = {"date": date_str, "game": game, "snapshots": 0, "unique_players": set(), 
            "player_counts": defaultdict(int), "player_perks": defaultdict(set), "maps": set(), "peak": 0}
    
    with open(logfile, errors='replace') as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            # KF2 player lines have leading spaces (check raw line)
            if raw_line.startswith('  ') and '|' in line and not line.startswith('['):
                parts = line.split('|')
                name = parts[0].strip()
                perk = parts[1].strip() if len(parts) > 1 else "?"
                if name:
                    data["unique_players"].add(name)
                    data["player_counts"][name] += 1
                    data["player_perks"][name].add(perk)
                continue
            m = re.match(r'\[(.+?)\]\s+Map:(.+?)\s+Wave:(.+?)\s+Players:(\d+)/(\d+)', line)
            if m:
                ts, mapname, wave, online, maxp = m.groups()
                data["snapshots"] += 1
                data["maps"].add(mapname.strip())
                online = int(online)
                data["peak"] = max(data["peak"], online)
                continue
            m2 = re.match(r'\[(.+?)\]\s+Map:(.+?)\s+Players:(\d+)/(\d+)', line)
            if m2:
                ts, mapname, online, maxp = m2.groups()
                data["snapshots"] += 1
                data["maps"].add(mapname.strip())
                online = int(online)
                data["peak"] = max(data["peak"], online)
                continue
            m3 = re.match(r'\[(.+?)\]\s+Players:(\d+)/(\d+)(?:\s+\((.+)\))?', line)
            if m3:
                ts, online, maxp, names_str = m3.groups()
                data["snapshots"] += 1
                online = int(online)
                data["peak"] = max(data["peak"], online)
                if names_str:
                    for name in names_str.split(','):
                        name = name.strip()
                        if name:
                            data["unique_players"].add(name)
                            data["player_counts"][name] += 1
                continue
            # Mordhau player line format
            if '|' in line and not line.startswith('['):
                parts = line.split('|')
                name = parts[0].strip()
                perk = parts[1].strip() if len(parts) > 1 else "?"
                if name:
                    data["unique_players"].add(name)
                    data["player_counts"][name] += 1
                    data["player_perks"][name].add(perk)
    
    data["unique_players"] = data["unique_players"]
    return data

# --- Command Handlers ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start and /help."""
    text = (
        "🎮 *SCL San Enrique — Game Server Bot*\n\n"
        "Comandos disponibles:\n"
        "/status — Estado en vivo de todos los servidores\n"
        "/players — Jugadores actuales en cada servidor\n"
        "/connect — Cómo conectarse a los servidores\n"
        "/ranking — Top jugadores del día\n"
        "/ranking _juego_ — Ranking de un juego específico (kf2/kf2pc/mordhau/enshrouded/palworld/valheim/q2/papermc/pz)\n"
        "/novedades — Últimos 5 cambios\n"
        "/changelog — Historial completo de cambios\n"
        "/whitelist — Gestionar whitelist de Minecraft (admin)\n"
        "/report — Generar resumen diario on-demand (admin)\n"
        "/pareto — Gráfico Pareto de RAM y disco por servidor (admin)\n"
        "/paises — Top países de jugadores, últimos 30 días (admin)\n"
        "/help — Esta ayuda\n\n"
        "Servidores monitoreados:\n"
        "💀 Killing Floor 2\n"
        "💀 Killing Floor 2 2\n"
        "⚔️ Mordhau\n"
        "🏔️ Enshrouded\n"
        "🐾 Palworld\n"
        "🐾 Palworld 2\n"
        "⚽ Quake 2 (Kick mod)\n"
        "⛵ Valheim\n"
        "⛏️ Paper MC (Minecraft)\n"
        "🧟 Project Zomboid"
    )
    await reply_md(update, text)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status — show live server status."""
    _docker_ip_cache.clear()  # Refresh Docker IP lookups each cycle
    lines = ["🎮 *Estado de Servidores — SCL San Enrique*\n"]
    
    # Get current public IP dynamically
    public_ip = get_public_ip()
    lines.append(f"📡 IP Pública: `{public_ip}`\n")
    
    for srv in SERVERS:
        if srv.get("type") == "q2":
            info = q2_info(resolve_host(srv["host"], srv["port"]), srv["port"])
        elif srv.get("type") == "enshrouded":
            info = a2s_info(resolve_host(srv["host"], srv["port"]), srv["port"])
            if info:
                # Override A2S data with real values (A2S is broken for Enshrouded)
                online = enshrouded_online_players()
                if online is not None:
                    info["players"] = len(online)
                    info["online_names"] = online
                info["max_players"] = srv.get("slot_count", 6)
            else:
                # A2S failed — Enshrouded doesn't always open the query port.
                # Use container status + log scraper as fallback.
                online = enshrouded_online_players()
                if online is not None:
                    info = {
                        "name": srv["name"],
                        "map": "",
                        "game": "",
                        "players": len(online),
                        "max_players": srv.get("slot_count", 6),
                        "online_names": online,
                    }
        elif srv.get("type") == "valheim":
            info = a2s_info(resolve_host(srv["host"], srv["port"]), srv["port"])
            if info:
                # A2S reports wrong max_players (shows server slot count, not config)
                info["max_players"] = srv.get("slot_count", 6)
            else:
                # A2S failed — check docker logs as fallback (crossplay breaks A2S)
                info = valheim_status_fallback()
        elif srv.get("type") == "minecraft":
            info = mc_status_ping(resolve_host(srv["host"], srv["port"]), srv["port"])
            if info:
                info["max_players"] = srv.get("slot_count", 6)
        elif srv.get("type") == "palworld":
            info = palworld_status(
                game_port=srv["game_port"],
                rcon_port=srv.get("rcon_port", 25575),
                name=srv["name"],
            )
        else:
            info = a2s_info(resolve_host(srv["host"], srv["port"]), srv["port"])
        if info:
            status = "🟢 Online" if info["players"] >= 0 else "🔴 Offline"
            lines.append(f'{srv["emoji"]} *{md_escape(srv["name"])}* — {status}')
            # Show version for Minecraft
            if srv.get("type") == "minecraft" and info.get("version"):
                lines.append(f'   Version: {md_escape(info["version"])}')
            # Show Kick mod info for Q2
            if info.get('kick_mode'):
                lines.append(f'   ⚽ Mod: Kick ({info.get("kick_gametype_name", "?")})')
                lines.append(f'   Mapa: {md_escape(info["map"])}')
            else:
                # Enshrouded has no map field in A2S
                if srv.get("type") != "enshrouded":
                    lines.append(f'   Mapa: {md_escape(info["map"])}')
            lines.append(f'   Jugadores: {info["players"]}/{info["max_players"]}')
            # Show player names for Enshrouded (A2S can't)
            if srv.get("type") == "enshrouded" and info.get("online_names"):
                lines.append(f'   Online: {", ".join(sanitize_name(n) for n in info["online_names"])}')
        else:
            lines.append(f'{srv["emoji"]} *{md_escape(srv["name"])}* — 🔴 Sin respuesta')
        lines.append("")
    
    lines.append("Usa /connect para ver cómo conectarte")
    
    await reply_md(update, "\n".join(lines))

async def players_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /players — show current players on each server."""
    _docker_ip_cache.clear()  # Refresh Docker IP lookups each cycle
    lines = ["👥 *Jugadores Actuales*\n"]
    any_players = False
    
    for srv in SERVERS:
        if srv.get("type") == "q2":
            info = q2_info(resolve_host(srv["host"], srv["port"]), srv["port"])
        elif srv.get("type") == "enshrouded":
            info = a2s_info(resolve_host(srv["host"], srv["port"]), srv["port"])
            if info:
                online = enshrouded_online_players()
                if online is not None:
                    info["players"] = len(online)
                    info["online_names"] = online
                info["max_players"] = srv.get("slot_count", 6)
            else:
                # A2S failed — use container status + log scraper as fallback.
                online = enshrouded_online_players()
                if online is not None:
                    info = {
                        "name": srv["name"],
                        "map": "",
                        "game": "",
                        "players": len(online),
                        "max_players": srv.get("slot_count", 6),
                        "online_names": online,
                    }
        elif srv.get("type") == "valheim":
            info = a2s_info(resolve_host(srv["host"], srv["port"]), srv["port"])
            if info:
                info["max_players"] = srv.get("slot_count", 6)
            else:
                info = valheim_status_fallback()
        elif srv.get("type") == "minecraft":
            info = mc_status_ping(resolve_host(srv["host"], srv["port"]), srv["port"])
            if info:
                info["max_players"] = srv.get("slot_count", 6)
        elif srv.get("type") == "palworld":
            info = palworld_status(
                game_port=srv["game_port"],
                rcon_port=srv.get("rcon_port", 25575),
                name=srv["name"],
            )
        else:
            info = a2s_info(resolve_host(srv["host"], srv["port"]), srv["port"])
        if info and info["players"] > 0:
            any_players = True
            if srv.get("type") == "enshrouded":
                lines.append(f'{srv["emoji"]} *{md_escape(srv["name"])}* — {info["players"]}/{info["max_players"]}')
            else:
                lines.append(f'{srv["emoji"]} *{md_escape(srv["name"])}* — {info["players"]}/{info["max_players"]} en {md_escape(info["map"])}')
            # Show Enshrouded player names from docker logs
            if srv.get("type") == "enshrouded" and info.get("online_names"):
                for name in info["online_names"]:
                    lines.append(f'   • {sanitize_name(name)}')
            else:
                players = a2s_players(srv["host"], srv["port"])
                if players:
                    for p in players[:10]:
                        mins = int(p["duration"] // 60)
                        lines.append(f'   • {sanitize_name(p["name"])} ({mins}m, score: {p["score"]})')
                elif srv["short"] == "KF2":
                    # Try WebAdmin for richer data
                    kf2 = get_kf2_details()
                    if kf2 and kf2.get("players_detail"):
                        for p in kf2["players_detail"][:10]:
                            lines.append(f'   • {sanitize_name(p["name"])} | {p["perk"]} | Kills: {p["kills"]}')
            lines.append("")
        elif info:
            lines.append(f'{srv["emoji"]} *{md_escape(srv["name"])}* — 0/{info["max_players"]} 😴')
            lines.append("")
        else:
            lines.append(f'{srv["emoji"]} *{md_escape(srv["name"])}* — sin respuesta')
            lines.append("")
    
    if not any_players:
        lines.append("No hay jugadores conectados ahora. ¡Sé el primero! 🎯")
    
    await reply_md(update, "\n".join(lines))

async def connect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /connect — show how to connect to each server with inline buttons."""
    args = context.args if context.args else []
    
    # Filter by game if specified
    servers = SERVERS
    if args:
        game_arg = args[0].lower()
        servers = [s for s in SERVERS if s["short"].lower().startswith(game_arg) or s["name"].lower() == game_arg]
        if not servers:
            await update.message.reply_text("Juego no reconocido. Usa: kf2, kf2pc, mordhau, enshrouded, palworld, palworld2, valheim, q2, papermc, pz, dayz")
            return

    # Get current public IP dynamically
    public_ip = get_public_ip()

    # Build text with connection info
    lines = ["🔗 *Cómo Conectarse — SCL San Enrique*\n"]

    for srv in servers:
        if srv.get("type") == "palworld":
            lines.append(f'{srv["emoji"]} {md_escape(srv["name"])} — `{public_ip}:{srv["game_port"]}`')
            lines.append(f'   {srv["notes"]}')
        else:
            lines.append(f'{srv["emoji"]} {md_escape(srv["name"])} — `{public_ip}:{srv["game_port"]}`')
    
    lines.append("")
    lines.append("💡 Busca \"SCL_San_Enrique\" en el server browser de cada juego.")
    lines.append("📡 IP detectada dinámicamente.")
    
    # Add Minecraft instructions if PaperMC is in the list
    papermc = any(s.get("type") == "minecraft" for s in servers)
    if papermc:
        lines.append("")
        lines.append("⛏️ *Minecraft:*")
        lines.append("1. Descarga el paquete portátil con el botón de abajo")
        lines.append("2. Descomprime el ZIP y ejecuta `start.bat`")
        lines.append("3. Java 21 ya viene incluido — no instala nada")
        lines.append("4. En Multiplayer, agrega: `{public_ip}:25565`")
        lines.append("5. Conectate a \"SCL_San_Enrique\"")
    
    # Build inline buttons for client downloads
    buttons = []
    for srv in servers:
        if srv.get("type") == "minecraft":
            buttons.append([
                InlineKeyboardButton("⛏️ PaperMC 1.21.11 Portátil (.zip)", url=f"http://{public_ip}:8088/downloads/PaperMC-1.21.11-Portable.zip")
            ])
        elif srv.get("type") == "q2":
            buttons.append([
                InlineKeyboardButton("⚽ Q2 + Kick mod (Windows)", url=f"http://{public_ip}:8088/downloads/q2pro-kick-full.zip")
            ])
    
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def ranking_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ranking — show daily player rankings."""
    args = context.args if context.args else []
    today = datetime.now().strftime("%Y-%m-%d")
    
    games = [("kf2", "KF2", "💀"), ("mordhau", "Mordhau", "⚔️"), ("enshrouded", "Enshrouded", "🏔️"), ("valheim", "Valheim", "⛵"), ("q2", "Quake 2", "⚽"), ("papermc", "Paper MC", "⛏️")]
    
    # Filter by game if specified
    if args:
        game_arg = args[0].lower()
        games = [(g, n, e) for g, n, e in games if g.startswith(game_arg) or n.lower() == game_arg]
        if not games:
            await update.message.reply_text("Juego no reconocido. Usa: kf2, mordhau, enshrouded, valheim, q2, papermc, pz")
            return
    
    lines = [f"📊 *Ranking del Día — {today}*\n"]
    has_data = False
    
    for game_key, game_name, emoji in games:
        data = parse_daily_log(game_key, today)
        if data is None:
            # Try yesterday
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            data = parse_daily_log(game_key, yesterday)
        
        if data and data["unique_players"]:
            has_data = True
            lines.append(f"{emoji} *{game_name}* — Peak: {data['peak']}, Jugadores únicos: {len(data['unique_players'])}")
            top = sorted(data["player_counts"].items(), key=lambda x: -x[1])[:5]
            for name, count in top:
                perks = ", ".join(data["player_perks"].get(name, set()))
                if perks and perks != "?":
                    lines.append(f"   • {sanitize_name(name)} ({count} snapshots, {perks})")
                else:
                    lines.append(f"   • {sanitize_name(name)} ({count} snapshots)")
            lines.append("")
        elif data:
            lines.append(f"{emoji} *{game_name}* — Sin jugadores hoy 😴")
            lines.append("")
    
    if not has_data:
        lines.append("No hay datos disponibles todavía. ¡Vuelve más tarde!")
    
    await reply_md(update, "\n".join(lines))

async def novedades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /novedades — show latest 5 changelog entries."""
    entries = load_changelog()
    
    if not entries:
        await reply_md(update,
            "📋 *Novedades — SCL San Enrique*\n\n"
            "No hay novedades registradas todavía.\n"
            "¡Vuelve pronto para ver los cambios!")
        return
    
    # Show only the latest 5 entries (JSON is newest-first)
    latest = entries[:5]
    
    lines = ["📋 *Novedades — SCL San Enrique*\n"]
    
    for entry in latest:
        date = entry.get("date", "")
        category = entry.get("category", "")
        title = entry.get("title", "")
        body = entry.get("body", "")
        
        cat_emoji = {"new": "🆕", "update": "🔧", "fix": "🐛", "remove": "❌", "info": "📢"}.get(category, "📋")
        
        lines.append(f"{cat_emoji} *{md_escape(title)}* — {date}")
        if body:
            for bl in body.split('\n'):
                lines.append(f"  {bl}")
        lines.append("")
    
    if len(entries) > 5:
        lines.append(f"_...y {len(entries) - 5} entradas más. Usa /changelog para ver todo._")
    
    lines.append("Usa /connect para ver cómo conectarte a los servidores")
    
    await reply_md(update, "\n".join(lines))

async def changelog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /changelog — show full changelog."""
    entries = load_changelog()
    
    if not entries:
        await reply_md(update,
            "📜 *Changelog — SCL San Enrique*\n\n"
            "No hay entradas registradas todavía.")
        return
    
    # Show all entries newest first
    lines = ["📜 *Changelog — SCL San Enrique*\n"]
    
    for entry in entries:
        date = entry.get("date", "")
        category = entry.get("category", "")
        title = entry.get("title", "")
        body = entry.get("body", "")
        
        cat_emoji = {"new": "🆕", "update": "🔧", "fix": "🐛", "remove": "❌", "info": "📢"}.get(category, "📋")
        
        lines.append(f"{cat_emoji} *{md_escape(title)}* — {date}")
        if body:
            for bl in body.split('\n'):
                lines.append(f"  {bl}")
        lines.append("")
    
    lines.append("Usa /connect para ver cómo conectarte a los servidores")
    
    await reply_md(update, "\n".join(lines))

# --- Whitelist (Minecraft PaperMC) ---
WHITELIST_PATH = "/var/lib/pterodactyl/volumes/7052e797-3532-46bf-bb9d-7a19a69e2ef0/whitelist.json"
MINECRAFT_CONTAINER = "7052e797-3532-46bf-bb9d-7a19a69e2ef0"
RCON_HOST = None  # se detecta dinámicamente
RCON_PORT = 25575
RCON_PASSWORD = "q2RconAdm1n"

def offline_uuid(username):
    """Genera el UUID offline de Minecraft (MD5 de 'OfflinePlayer:nombre')."""
    import hashlib, uuid as uuid_mod
    name = "OfflinePlayer:" + username
    md5 = hashlib.md5(name.encode("utf-8")).digest()
    b = bytearray(md5)
    b[6] = (b[6] & 0x0f) | 0x30
    b[8] = (b[8] & 0x3f) | 0x80
    return str(uuid_mod.UUID(bytes=bytes(b)))

def get_container_ip():
    """Obtiene la IP del container de PaperMC para RCON."""
    try:
        result = subprocess.run(
            ["docker", "inspect", MINECRAFT_CONTAINER,
             "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
            capture_output=True, text=True, timeout=5
        )
        ip = result.stdout.strip()
        return ip if ip else None
    except:
        return None

def rcon_command(command):
    """Envía un comando via RCON al servidor de Minecraft."""
    ip = get_container_ip()
    if not ip:
        return None, "No se pudo obtener la IP del container"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((ip, RCON_PORT))

        def send_packet(req_id, type_id, body):
            payload = struct.pack("<ii", req_id, type_id) + body.encode("utf-8") + b"\x00\x00"
            sock.send(struct.pack("<i", len(payload)) + payload)
            length = sock.recv(4)
            total = struct.unpack("<i", length)[0]
            data = b""
            while len(data) < total:
                data += sock.recv(total - len(data))
            return data[8:-2].decode("utf-8", errors="replace")

        send_packet(1, 3, RCON_PASSWORD)  # login
        resp = send_packet(2, 2, command)  # command
        sock.close()
        return True, resp
    except Exception as e:
        return None, str(e)

def load_whitelist():
    """Carga la whitelist desde el archivo JSON."""
    try:
        with open(WHITELIST_PATH, "r") as f:
            return json.load(f)
    except:
        return []

def save_whitelist(wl):
    """Guarda la whitelist en el archivo JSON."""
    with open(WHITELIST_PATH, "w") as f:
        json.dump(wl, f, indent=2)

async def whitelist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /whitelist — manage Minecraft whitelist (admin only)."""
    user_id = update.effective_user.id
    args = context.args if context.args else []

    # Solo admins pueden gestionar la whitelist
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Solo los administradores pueden gestionar la whitelist.")
        return

    if not args:
        # Mostrar lista actual
        wl = load_whitelist()
        lines = [f"⛏️ *Whitelist Minecraft — {len(wl)} jugadores*\n"]
        for entry in wl:
            lines.append(f"  • {entry['name']}")
        lines.append("")
        lines.append("Comandos:")
        lines.append("`/whitelist add <nombre>` — Agregar jugador")
        lines.append("`/whitelist remove <nombre>` — Quitar jugador")
        lines.append("`/whitelist reload` — Recargar en el servidor")
        await reply_md(update, "\n".join(lines))
        return

    action = args[0].lower()

    if action == "add":
        if len(args) < 2:
            await update.message.reply_text("Uso: `/whitelist add <nombre>`", parse_mode="Markdown")
            return
        username = args[1]
        wl = load_whitelist()
        if any(e["name"].lower() == username.lower() for e in wl):
            await update.message.reply_text(f"⚠️ {username} ya está en la whitelist.")
            return
        uid = offline_uuid(username)
        wl.append({"uuid": uid, "name": username})
        save_whitelist(wl)
        # Recargar en el servidor
        ok, resp = rcon_command("whitelist reload")
        if ok:
            await update.message.reply_text(
                f"✅ {username} agregado a la whitelist.\n"
                f"UUID: `{uid}`\n"
                f"Whitelist recargada: {resp}"
            )
        else:
            await update.message.reply_text(
                f"✅ {username} agregado a la whitelist.\n"
                f"UUID: `{uid}`\n"
                f"⚠️ No se pudo recargar via RCON ({resp}). Usa `/whitelist reload`."
            )

    elif action == "remove":
        if len(args) < 2:
            await update.message.reply_text("Uso: `/whitelist remove <nombre>`", parse_mode="Markdown")
            return
        username = args[1]
        wl = load_whitelist()
        before = len(wl)
        wl = [e for e in wl if e["name"].lower() != username.lower()]
        if len(wl) == before:
            await update.message.reply_text(f"⚠️ {username} no está en la whitelist.")
            return
        save_whitelist(wl)
        ok, resp = rcon_command("whitelist reload")
        if ok:
            await update.message.reply_text(f"❌ {username} removido de la whitelist.\nWhitelist recargada.")
        else:
            await update.message.reply_text(
                f"❌ {username} removido de la whitelist.\n"
                f"⚠️ No se pudo recargar via RCON ({resp})."
            )

    elif action == "reload":
        ok, resp = rcon_command("whitelist reload")
        if ok:
            await update.message.reply_text(f"✅ Whitelist recargada: {resp}")
        else:
            await update.message.reply_text(f"⚠️ Error al recargar: {resp}")

    else:
        await update.message.reply_text(
            "Comandos: `/whitelist add <nombre>`, `/whitelist remove <nombre>`, `/whitelist reload`",
            parse_mode="Markdown"
        )

# --- Report (daily-summary) ---
def _load_daily_summary_module():
    """Carga dinámicamente daily-summary.py sin renombrar el archivo."""
    spec = importlib.util.spec_from_file_location("daily_summary", "/opt/scl-bot/daily-summary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /report — muestra el último reporte diario guardado (no genera uno nuevo).
    El reporte se genera automáticamente por cron a las 06:10 AM y se guarda en
    /opt/scl-bot/reports/report_YYYY-MM-DD.txt"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Solo los administradores pueden ver el reporte.")
        return

    report_dir = "/opt/scl-bot/reports"
    if not os.path.isdir(report_dir):
        await update.message.reply_text("⚠️ No hay reportes guardados todavía.")
        return

    # Find the most recent report file
    import glob
    reports = sorted(glob.glob(os.path.join(report_dir, "report_*.txt")),
                     key=os.path.getmtime, reverse=True)
    if not reports:
        await update.message.reply_text("⚠️ No hay reportes guardados todavía.")
        return

    latest = reports[0]
    try:
        with open(latest, "r") as f:
            summary = f.read()
        report_name = os.path.basename(latest)
        await reply_md(update, f"📋 Reporte guardado ({report_name}):\n\n{summary}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error al leer reporte: {e}")

async def pareto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /pareto — genera un gráfico Pareto de RAM y disco por servidor."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Solo los administradores pueden usar este comando.")
        return

    await update.message.reply_text("📊 Generando Pareto de memoria y disco...")

    try:
        # Importar el generador de Pareto
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pareto_memoria", "/opt/scl-bot/pareto_memoria.py"
        )
        pareto_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pareto_mod)

        # Generar el gráfico
        output_path = "/opt/scl-bot/reports/pareto_memoria_live.png"
        path, ram_data, disk_data = pareto_mod.generate_pareto(output_path, include_disk=True)

        # Enviar imagen sin caption
        with open(path, "rb") as photo:
            await update.message.reply_photo(photo=photo)

    except Exception as e:
        log.error(f"Error generating Pareto: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Error al generar Pareto: {e}")


async def grafico_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /grafico — genera un gráfico de jugadores únicos por día desde los reportes."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Solo los administradores pueden usar este comando.")
        return

    await update.message.reply_text("📊 Generando gráfico de jugadores únicos...")

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "plot_players", "/opt/scl-bot/plot_players.py"
        )
        plot_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plot_mod)

        output_path = "/opt/scl-bot/reports/players_timeline.png"
        path, n_days, avg, mx = plot_mod.generate_graph(output_path)

        with open(path, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=f"📊 {n_days} días | Promedio: {avg:.0f} | Máx: {mx} jugadores únicos"
            )

    except Exception as e:
        log.error(f"Error generating players graph: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Error al generar gráfico: {e}")


async def paises_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /paises — Pareto de países de jugadores, ventana móvil 30 días."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Solo los administradores pueden usar este comando.")
        return

    # /paises [N] — ventana de días opcional (default 30, 0=all)
    days = 30
    if context.args:
        try:
            raw = context.args[0].lower()
            days = 0 if raw in ("all", "todo") else max(0, int(raw))
        except ValueError:
            pass

    ventana = "histórico completo" if days == 0 else f"últimos {days} días"
    await update.message.reply_text(f"🌍 Generando Pareto de países ({ventana})...")

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pareto_paises", "/opt/scl-bot/pareto_paises.py"
        )
        paises_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(paises_mod)

        output_path = "/opt/scl-bot/reports/pareto_paises_live.png"
        path, stats = paises_mod.generate_paises(output_path, days=days)

        n = stats["n_countries"]
        total = stats["total"]
        caption = (
            f"🌍 Pareto de países · {ventana}\n"
            f"{stats['rango']} · {stats['days']} reportes con actividad\n"
            f"{total:,} IPs-día · {n} países"
        ).replace(",", ".")

        with open(path, "rb") as photo:
            await update.message.reply_photo(photo=photo, caption=caption)

    except Exception as e:
        log.error(f"Error generating countries Pareto: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Error al generar Pareto de países: {e}")


# --- Main ---
def main():
    log.info("Starting SCL San Enrique Bot...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("players", players_cmd))
    app.add_handler(CommandHandler("connect", connect_cmd))
    app.add_handler(CommandHandler("ranking", ranking_cmd))
    app.add_handler(CommandHandler("novedades", novedades_cmd))
    app.add_handler(CommandHandler("changelog", changelog_cmd))
    app.add_handler(CommandHandler("chismes", novedades_cmd))  # easter egg alias
    app.add_handler(CommandHandler("whitelist", whitelist_cmd))
    app.add_handler(CommandHandler("wl", whitelist_cmd))  # alias corto
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("pareto", pareto_cmd))
    app.add_handler(CommandHandler("grafico", grafico_cmd))
    app.add_handler(CommandHandler("paises", paises_cmd))

    log.info("Bot is running!")
    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()