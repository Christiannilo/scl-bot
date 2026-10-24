#!/usr/bin/env python3
"""
SCL San Enrique — Resumen Diario de Uso
Genera un reporte compacto de actividad de jugadores por servidor.
Se ejecuta via cron a las 09:00 AM Santiago.
Envía el resumen por Telegram al chat principal.
"""

import os
import re
import json
import struct
import urllib.request
import urllib.parse

def get_public_ip():
    """Get current public IP from ipify, with fallback to current_ip.txt."""
    try:
        with urllib.request.urlopen('https://api.ipify.org', timeout=10) as r:
            return r.read().decode().strip()
    except:
        try:
            with open("/opt/scl-bot/current_ip.txt") as f:
                return f.read().strip()
        except:
            return "127.0.0.1"
import logging
from socket import socket, AF_INET, SOCK_DGRAM
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

# GeoIP local (MaxMind GeoLite2-City, sin rate limit ni internet)
import geoip2.database
_GEOIP_READER = None

def _get_geoip_reader():
    global _GEOIP_READER
    if _GEOIP_READER is None:
        try:
            _GEOIP_READER = geoip2.database.Reader("/opt/scl-bot/GeoLite2-City.mmdb")
        except Exception:
            _GEOIP_READER = False  # marcar como no disponible
    return _GEOIP_READER

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("daily-summary")

# --- Config ---
SERVER_IP = None  # Dynamic — set at runtime via get_public_ip()
TELEGRAM_CHAT_ID = int(os.environ.get("SCL_ALERT_CHAT", "0") or 0)
GEO_CACHE = {}  # IP -> {country, countryCode}
MAX_FLAGS_DISPLAY = 10  # After this, group with counts

# Token del bot desde entorno (única fuente de secretos: /root/.scl-bot.env)
BOT_TOKEN = os.environ.get("SCL_BOT_TOKEN", "")

# Server definitions
SERVERS = [
    {
        "name": "KF2", "emoji": "💀", "port": 27015, "game_port": 7777,
        "type": "source", "log_path": "/var/lib/pterodactyl/volumes/48261dfb-5c02-4630-af84-afa325b59b95/KFGame/Logs/Launch.log",
        "webadmin_url": "http://DYNAMIC_IP:8082/ServerAdmin",  # IP set at runtime
        "webadmin_user": os.environ.get("KF2_WEBADMIN_USER", "admin"),
        "webadmin_pass": os.environ.get("KF2_WEBADMIN_PASS", ""),
        "server_id": 4,  # Panel server ID for restart
        "remote": False,
    },
    {
        "name": "KF2-2", "emoji": "💀", "port": 27016, "game_port": 7778,
        "type": "source", "log_path": "/var/lib/pterodactyl/volumes/6047cf29-ebb3-47e1-bba7-a3b98e4e14d6/KFGame/Logs/Launch.log",
        "server_id": 15,  # Panel server ID for restart
        "remote": False,
    },
    {
        "name": "Mordhau", "emoji": "⚔️", "port": 27025, "game_port": 7787,
        "type": "mordhau",
        "log_path": "/var/lib/pterodactyl/volumes/e804f1e6-c3e2-4a8d-90c3-bcdd5ae4b61e/Mordhau/Saved/Logs/Mordhau.log",
    },
    {
        "name": "Enshrouded", "emoji": "🏔️", "port": 15636, "game_port": 15636,
        "type": "enshrouded", "log_path": "/var/lib/pterodactyl/volumes/6440e54b-b3ca-42a2-aaa3-4fa5e76745bb/logs/enshrouded_server.log",
    },
    {
        "name": "DayZ", "emoji": "🧟‍♂️", "port": 27017, "game_port": 2302,
        "type": "dayz",
        "log_path": "/var/lib/pterodactyl/volumes/b9944410-1c32-49ef-8897-4411838f3cff/profiles/DayZServer_*.ADM",
    },
    {
        "name": "Quake 2", "emoji": "⚽️", "port": 27910, "game_port": 27910,
        "type": "q2", "log_path": None,
    },
    {
        "name": "Valheim", "emoji": "⛵️", "port": 2457, "game_port": 2456,
        "type": "valheim", "log_path": "/var/lib/pterodactyl/volumes/32e0bff9-e9bd-4258-bb0a-c3ab81eb93cd",
        "container_id": "32e0bff9-e9bd-4258-bb0a-c3ab81eb93cd",  # Docker logs used as log source
    },
    {
        "name": "PaperMC", "emoji": "⛏️", "port": 25565, "game_port": 25565,
        "type": "minecraft",
        "log_path": "/var/lib/pterodactyl/volumes/7052e797-3532-46bf-bb9d-7a19a69e2ef0/logs/latest.log",
    },
    {
        "name": "Palworld", "emoji": "🌴", "port": 8211, "game_port": 8211,
        "type": "palworld", "log_path": None,
        "container_id": "f8a39b9c-a4eb-40c9-a6ee-c225959b14a8",  # Docker logs used as log source
    },
    {
        "name": "Palworld 2", "emoji": "🌴", "port": 8213, "game_port": 8213,
        "type": "palworld", "log_path": None,
        "container_id": "42325324-4adc-4a6f-abc4-340f57a8b0e9",  # Docker logs used as log source
    },
    {
        "name": "Project Zomboid", "emoji": "🧟", "port": 16261, "game_port": 16261,
        "type": "source", "log_path": None,
    },
]

MC_BOT_SERVICES = ["mc-guard-bot", "mc-builder-bot", "mc-miner-bot", "mc-farmer-bot", "mc-scout-bot", "mc-ferbot"]
MC_BOT_NAMES = {
    "mc-guard-bot": "GuardBot", "mc-builder-bot": "BuilderBot", "mc-miner-bot": "MinerBot",
    "mc-farmer-bot": "FarmerBot", "mc-scout-bot": "ScoutBot", "mc-ferbot": "FerBot",
}
# Bots that appear in MC logs (not real players)
MC_BOT_USERNAMES = {"GuardBot", "BuilderBot", "MinerBot", "FarmerBot", "ScoutBot", "FerBot"}


# --- A2S Query ---
def a2s_query(ip, port):
    """Returns (online, mapname, players, max_players) or (False, None, 0, 0)."""
    try:
        s = socket(AF_INET, SOCK_DGRAM)
        s.settimeout(5)
        req = b'\xff\xff\xff\xff\x54Source Engine Query\x00'
        s.sendto(req, (ip, port))
        data, _ = s.recvfrom(4096)
        if data[4:5] == b'\x41':
            challenge = data[5:9]
            req2 = b'\xff\xff\xff\xff\x54Source Engine Query\x00' + challenge
            s.sendto(req2, (ip, port))
            data, _ = s.recvfrom(8192)
        if data[4:5] == b'\x49':
            pkt = data[5:]
            pos = 1
            name_end = pkt.index(b'\x00', pos)
            pos = name_end + 1
            map_end = pkt.index(b'\x00', pos)
            mapname = pkt[pos:map_end].decode('utf-8', errors='replace')
            pos = map_end + 1
            pos = pkt.index(b'\x00', pos) + 1  # folder
            pos = pkt.index(b'\x00', pos) + 1  # game
            pos += 2  # appid (extended)
            players = pkt[pos]
            max_players = pkt[pos + 1]
            s.close()
            return True, mapname, players, max_players
        s.close()
    except Exception:
        pass
    return False, None, 0, 0


# --- GeoIP ---
def geo_lookup(ip):
    """Look up country for an IP using local MaxMind GeoLite2 database. Returns (country_name, country_code) or (None, None)."""
    if ip in GEO_CACHE:
        return GEO_CACHE[ip]
    # Skip private/local IPs
    if ip.startswith("127.") or ip.startswith("192.168.") or ip.startswith("10."):
        result = ("Local", "LOCAL")
        GEO_CACHE[ip] = result
        return result
    # 172.16.0.0/12 is private (172.16.x - 172.31.x), not all 172.x
    if ip.startswith("172."):
        parts = ip.split(".")
        if len(parts) >= 2 and 16 <= int(parts[1]) <= 31:
            result = ("Local", "LOCAL")
            GEO_CACHE[ip] = result
            return result
    reader = _get_geoip_reader()
    if reader:
        try:
            resp = reader.city(ip)
            result = (resp.country.name, resp.country.iso_code)
        except Exception:
            result = (None, None)
    else:
        # Fallback a ip-api.com si la base local no está disponible
        try:
            url = f"http://ip-api.com/json/{ip}?fields=country,countryCode"
            with urllib.request.urlopen(url, timeout=5) as r:
                d = json.loads(r.read())
                result = (d.get("country"), d.get("countryCode"))
        except Exception:
            result = (None, None)
    GEO_CACHE[ip] = result
    return result


def country_flag(code):
    """Convert country code to flag emoji."""
    if not code or len(code) != 2:
        return "❓"
    try:
        return chr(0x1F1E6 + ord(code[0].upper()) - ord('A')) + chr(0x1F1E6 + ord(code[1].upper()) - ord('A'))
    except Exception:
        return "❓"


def format_flags(ip_list):
    """Format a list of IPs into flag emojis, always grouped by country with counts.
    Shows ×N only when N > 1 (e.g. 🇧🇷×8, not 🇧🇷×1)."""
    countries = []
    for ip in ip_list:
        _, code = geo_lookup(ip)
        if code and code != "LOCAL":
            countries.append(code)
        else:
            countries.append("??")

    # Always group by country with counts
    counts = defaultdict(int)
    for c in countries:
        counts[c] += 1
    parts = []
    for c, count in sorted(counts.items(), key=lambda x: -x[1]):
        if c == "??":
            parts.append(f"❓×{count}" if count > 1 else "❓")
        else:
            parts.append(f"{country_flag(c)}×{count}" if count > 1 else country_flag(c))
    return " ".join(parts)


# --- Minecraft log parser ---
def parse_mc_log(log_path, target_date):
    """Parse Minecraft log for player sessions. Returns list of (name, ip, join_time, leave_time)."""
    sessions = []
    if not log_path or not os.path.exists(log_path):
        return sessions

    # Minecraft log format: [HH:MM:SS] [Server thread/INFO]: PlayerName[/IP:port] logged in with...
    #                        [HH:MM:SS] [Server thread/INFO]: PlayerName left the game
    # Date comes from the log file header or we assume current date

    try:
        with open(log_path, errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return sessions

    active_players = {}  # name -> (ip, join_time)

    for line in lines:
        # Parse join: PlayerName[/IP:port] logged in with entity id
        m = re.search(r'\[(\d{2}:\d{2}:\d{2})\].*?(\w+)\[/(?:\d+\.\d+\.\d+\.\d+):(\d+)\].*?logged in', line)
        if m:
            time_str, name, ip_port = m.group(1), m.group(2), line
            ip_match = re.search(r'/(\d+\.\d+\.\d+\.\d+):', line)
            ip = ip_match.group(1) if ip_match else None
            # Skip bots
            if name in MC_BOT_USERNAMES:
                continue
            # Try to extract date from line (MC logs usually have date in first lines)
            active_players[name] = {"ip": ip, "join": time_str}

        # Parse leave: PlayerName left the game
        m = re.search(r'\[(\d{2}:\d{2}:\d{2})\].*?(\w+) left the game', line)
        if m:
            time_str, name = m.group(1), m.group(2)
            if name in active_players:
                session = active_players.pop(name)
                sessions.append({
                    "name": name,
                    "ip": session["ip"],
                    "join": session["join"],
                    "leave": time_str,
                })

    # Players still connected (no leave event)
    for name, data in active_players.items():
        if name not in MC_BOT_USERNAMES:
            sessions.append({
                "name": name,
                "ip": data["ip"],
                "join": data["join"],
                "leave": None,  # still connected
            })

    return sessions


# --- Palworld log parser (Docker logs) ---
def _utc_to_local(utc_ts_str):
    """Convert a Palworld log timestamp from UTC to local host time.
    Input format: 'YYYY-MM-DD HH:MM:SS' (UTC).
    Returns same format string in local time.
    DST-aware via astimezone() — Chile is UTC-3 in summer (sep-mar), UTC-4 in winter."""
    try:
        from datetime import timezone
        dt_utc = datetime.strptime(utc_ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return _utc_dt_to_local(dt_utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return utc_ts_str


# --- Valheim log parser (Docker logs) ---
PERSISTENT_DOCKER_LOGS = "/opt/scl-bot/docker-logs"  # capturados por docker-log-shipper.sh (cron */5)


def _fetch_docker_logs(container_id):
    """Devuelve el texto de logs de un contenedor priorizando los archivos
    persistentes del shipper (sobreviven recreaciones del contenedor y
    truncados del driver local), con fallback a `docker logs` en vivo.

    Lee el archivo actual {cid}.log MÁS los rotados por logrotate
    ({cid}.log-YYYYMMDD[.gz]), concatenados en orden cronológico, para
    que una sesión que cruza una rotación siga completa.

    Formato idéntico en todos los casos: prefijo RFC3339 UTC por línea
    (el que produce `docker logs --timestamps`)."""
    import glob as _glob
    import gzip
    import subprocess
    base = os.path.join(PERSISTENT_DOCKER_LOGS, container_id)
    paths = _glob.glob(base + ".log") + _glob.glob(base + ".log-*")
    if paths:
        # Orden cronológico real (mtime se conserva tras rename de logrotate)
        paths = sorted(paths, key=os.path.getmtime)
        chunks = []
        for p in paths:
            try:
                if p.endswith(".gz"):
                    with gzip.open(p, "rt", errors="replace") as f:
                        chunks.append(f.read())
                else:
                    with open(p, errors="replace") as f:
                        chunks.append(f.read())
            except Exception as e:
                log.warning(f"_fetch_docker_logs: read {p} failed: {e}")
        raw = "".join(chunks)
        if raw:
            return raw
    # Fallback: docker logs en vivo (comportamiento histórico)
    try:
        result = subprocess.run(
            ["docker", "logs", "--timestamps", container_id],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout
    except Exception as e:
        log.error(f"_fetch_docker_logs: docker logs failed: {e}")
        return ""


def _utc_dt_to_local(dt_utc):
    """datetime aware UTC -> naive datetime en timezone del host (DST-aware).
    Más robusto que un offset fijo -4: Chile cambia a UTC-3 en DST (sep-mar)."""
    return dt_utc.astimezone().replace(tzinfo=None)


def _docker_ts_to_local(utc_iso_str):
    """Docker log prefix '2026-09-11T01:33:30.057461309Z' -> naive datetime host-local."""
    try:
        s = utc_iso_str.rstrip("Z")
        if "." in s:
            date_part, frac = s.split(".")
            frac = (frac + "000000")[:6]
            s = f"{date_part}.{frac}"
        fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in s else "%Y-%m-%dT%H:%M:%S"
        dt_utc = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        return _utc_dt_to_local(dt_utc)
    except Exception:
        return None


def parse_valheim_log(container_id, target_date):
    """Parse Valheim sessions from Docker container stdout (`docker logs --timestamps`).

    Valheim Dedicated no escribe log de juego en el volumen (solo stdout),
    igual que Palworld. Eventos de sesión (stdout, reloj contenedor = UTC):

      2026-09-11T01:33:30Z 09/11/2026 01:33:30: Got connection SteamID 76561198071375442
      2026-09-11T01:33:57Z 09/11/2026 01:33:57: Got character ZDOID from Nilo : 2672107023:1
      2026-09-11T01:39:20Z 09/11/2026 01:39:20: Closing socket 76561198071375442

    - 'Got connection' abre la conexión (SteamID, sin nombre aún)
    - 'Got character ZDOID from <nombre>' = jugador cargó personaje (join real)
    - 'Closing socket <steamid>' = desconexión (leave)
    - ZDOID repetido con sesión abierta = respawn/portal, no nueva sesión

    El ZDOID no trae SteamID: se asigna FIFO al connect abierto más antiguo
    dentro de 5 minutos. Sin IP en stdout -> ip=None (sin flags, sin GeoIP).
    Ventana 24h (ayer 06:00 local -> ahora), igual que Palworld.

    Los logs se leen desde el archivo persistente del shipper si existe
    (ver _fetch_docker_logs) — protege contra pérdida de historial cuando
    Wings recrea el contenedor (updates).
    """
    import subprocess
    sessions = []

    now = datetime.now()
    window_start = (now - timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)

    raw = _fetch_docker_logs(container_id)
    if not raw:
        log.error(f"parse_valheim_log: no docker logs available for {container_id}")
        return sessions

    events = []  # (dt_local, kind, payload)
    for line in raw.splitlines():
        line = line.strip()
        m = re.match(r"^(\S+Z)\s", line)
        if not m:
            continue
        dt_local = _docker_ts_to_local(m.group(1))
        if dt_local is None:
            continue

        mm = re.search(r"Got connection SteamID (\d+)", line)
        if mm:
            events.append((dt_local, "connect", mm.group(1)))
            continue
        mm = re.search(r"Got character ZDOID from (.+?)\s*:\s*\d+", line)
        if mm:
            events.append((dt_local, "join", mm.group(1).strip()))
            continue
        mm = re.search(r"Closing socket (\d+)", line)
        if mm:
            events.append((dt_local, "leave", mm.group(1)))

    events.sort(key=lambda x: x[0])

    conns = []  # {steamid, connect_dt, name, join_dt, leave_dt}
    for dt, kind, payload in events:
        if kind == "connect":
            conns.append({"steamid": payload, "connect_dt": dt, "name": None,
                          "join_dt": None, "leave_dt": None})
        elif kind == "join":
            # mismo nombre aún conectado -> respawn/portal, no nueva sesión
            if any(c["name"] == payload and c["leave_dt"] is None for c in conns):
                continue
            # FIFO: connect abierto más antiguo sin nombre, dentro de 5 min
            for c in conns:
                if c["name"] is None and c["leave_dt"] is None:
                    gap = (dt - c["connect_dt"]).total_seconds()
                    if 0 <= gap <= 300:
                        c["name"] = payload
                        c["join_dt"] = dt
                        break
            else:
                # join sin connect visible (docker logs rotados por max-size) -> huérfano
                conns.append({"steamid": None, "connect_dt": dt, "name": payload,
                              "join_dt": dt, "leave_dt": None})
        elif kind == "leave":
            for c in conns:
                if c["steamid"] == payload and c["leave_dt"] is None:
                    c["leave_dt"] = dt
                    break

    for c in conns:
        if c["join_dt"] is None:
            continue  # conectó pero nunca cargó personaje (password/kick) — no sesión
        if not (window_start <= c["join_dt"] <= now):
            continue
        sessions.append({
            "name": c["name"],
            "ip": None,
            "join": c["join_dt"].strftime("%Y-%m-%d %H:%M:%S"),
            "leave": c["leave_dt"].strftime("%Y-%m-%d %H:%M:%S") if c["leave_dt"] else None,
        })
    return sessions


def calc_duration_valheim(join_str, leave_str):
    """Calculate duration in minutes from Valheim timestamps (YYYY-MM-DD HH:MM:SS)."""
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        j = datetime.strptime(join_str, fmt)
        l = datetime.strptime(leave_str or datetime.now().strftime(fmt), fmt)
        delta = (l - j).total_seconds()
        if delta < 0:
            delta += 86400
        return round(delta / 60.0)
    except Exception:
        return 0


# --- DayZ AdminLog (.ADM) parser ---
def parse_dayz_adm_log(log_glob, target_date):
    """Parse DayZ sessions from AdminLog .ADM files (formato Bohemia):

      16:21:58 Player "Survivor" is connected (steamID=76561198042763113)
      16:22:45 Player "Survivor"(id=AbCdEfGh12345678) has been disconnected

    El ADM solo trae HH:MM:SS (reloj contenedor = UTC). Anclaje de fecha:
    el último evento del archivo ~= mtime del archivo (se escribe en vivo);
    se camina hacia atrás desde ahí restando un día por cada cruce de
    medianoche detectado (hhmm retrocede respecto al evento siguiente).
    Match de sesión por nombre (el id puede diferir entre connect steamID=
    y disconnect id=Unknown). Sin IP en ADM -> ip=None (sin flags).
    Ventana 24h (ayer 06:00 local -> ahora), igual que Palworld.
    """
    import glob as _glob
    sessions = []
    files = sorted(_glob.glob(log_glob))
    if not files:
        log.warning(f"parse_dayz_adm_log: no .ADM files match {log_glob}")
        return sessions

    now = datetime.now()
    window_start = (now - timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)

    for adm_path in files:
        try:
            with open(adm_path, errors="replace") as f:
                raw = f.read()
        except Exception as e:
            log.error(f"parse_dayz_adm_log: read {adm_path} failed: {e}")
            continue

        # 1) Recolectar eventos (hhmm, kind, name, pid) en orden de archivo.
        #    Formato REAL del ADM Bohemia (verificado contra el log 2026-09-16):
        #      21:00:23 | Player "Survivor" (id=x4Cj...= pos=<9721.3, 2094.0, 16.8>) is connected
        #      21:32:29 | Player "Survivor" (id=... pos=<...>) has been disconnected
        #    Nota: el verbo va DESPUÉS del paréntesis, hay separador " | " tras la
        #    hora, y el id (base64 con '=') es IGUAL en connect y disconnect — más
        #    fiable que el nombre (personajes nuevos se llaman todos "Survivor").
        ev_pat = re.compile(
            r"^(\d{2}:\d{2}:\d{2})\s*\|\s*Player \"([^\"]+)\"\s*\(\s*(?:steamID=|id=)([\w+=/-]+)"
        )
        raw_events = []
        for line in raw.splitlines():
            m = ev_pat.search(line)
            if not m:
                continue
            if line.rstrip().endswith("is connected"):
                raw_events.append((m.group(1), "join", m.group(2), m.group(3)))
            elif "has been disconnected" in line:
                raw_events.append((m.group(1), "leave", m.group(2), m.group(3)))
        if not raw_events:
            continue

        # 2) Anclar el ÚLTIMO evento al mtime del archivo (UTC): el ADM se
        #    escribe en vivo, así que último evento ~= mtime. Si el hhmm del
        #    último evento > hhmm del mtime, el evento fue el día anterior.
        mtime_utc = datetime.fromtimestamp(os.path.getmtime(adm_path), tz=timezone.utc).replace(tzinfo=None)
        last_hhmm = raw_events[-1][0]
        last_date = mtime_utc.date()
        if datetime.strptime(last_hhmm, "%H:%M:%S").time() > mtime_utc.time():
            last_date = last_date - timedelta(days=1)

        # 3) Caminar hacia atrás: cada cruce de medianoche (hhmm retrocede)
        #    resta un día respecto al evento siguiente.
        dts = []
        prev_hhmm = None
        date_cursor = last_date
        for hhmm, kind, name, pid in reversed(raw_events):
            if prev_hhmm is not None and hhmm > prev_hhmm:
                date_cursor = date_cursor - timedelta(days=1)  # cruzó medianoche
            prev_hhmm = hhmm
            dt_utc = datetime.strptime(f"{date_cursor} {hhmm}", "%Y-%m-%d %H:%M:%S")
            dts.append((_utc_dt_to_local(dt_utc.replace(tzinfo=timezone.utc)), kind, name, pid))
        dts.reverse()

        # 4) Armar sesiones: join abre, leave cierra la más antigua del mismo
        #    player-id (el nombre puede repetirse: todos los personajes nuevos
        #    se llaman "Survivor"; el id base64 sí es único por jugador).
        open_sessions = []
        for dt, kind, name, pid in dts:
            if kind == "join":
                open_sessions.append({"name": name, "pid": pid, "join_dt": dt, "leave_dt": None})
            else:
                for s in open_sessions:
                    if s["pid"] == pid and s["leave_dt"] is None:
                        s["leave_dt"] = dt
                        break

        for s in open_sessions:
            if not (window_start <= s["join_dt"] <= now):
                continue
            sessions.append({
                "name": s["name"],
                "ip": None,
                "join": s["join_dt"].strftime("%Y-%m-%d %H:%M:%S"),
                "leave": s["leave_dt"].strftime("%Y-%m-%d %H:%M:%S") if s["leave_dt"] else None,
            })
    return sessions


def calc_duration_dayz(join_str, leave_str):
    """Calculate duration in minutes from DayZ timestamps (YYYY-MM-DD HH:MM:SS)."""
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        j = datetime.strptime(join_str, fmt)
        l = datetime.strptime(leave_str or datetime.now().strftime(fmt), fmt)
        delta = (l - j).total_seconds()
        if delta < 0:
            delta += 86400
        return round(delta / 60.0)
    except Exception:
        return 0


def parse_palworld_log(container_id, target_date):
    """Parse Palworld server logs from Docker container stdout for player sessions.

    Log format (stdout via `docker logs`):
      [2026-07-10 16:58:06] [LOG] HolaChao1234 200.83.78.60 connected the server. (User id: steam_76561198071375442)
      [2026-07-10 16:58:13] [LOG] Nilo joined the server. (User id: steam_76561198071375442, Player id: ...)
      [2026-07-10 17:01:05] [LOG] Nilo left the server. (User id: steam_76561198071375442)

    'connected' = TCP connection established (has IP, but player name not yet known)
    'joined'    = Player fully loaded into the game (name is the in-game name)
    'left'      = Player disconnected

    Strategy: match 'connected' (has IP) with 'joined' (has name) by User id to build
    sessions with IP + name + join timestamp, then match 'left' by User id for leave.

    IMPORTANT: Docker logs accumulate since container start (no rotation). We filter
    to only sessions within the last 24h window (06:00 yesterday to now), so
    historical data doesn't inflate the report.

    IMPORTANT: The internal Palworld log timestamps (e.g. [2026-08-10 23:22:32]) are
    in UTC. We convert them to local Santiago time (UTC-4) before filtering and
    storing, so the report shows correct local times and the date filter matches.
    """
    import subprocess
    sessions = []

    # The report runs at 06:10 AM, summarizing the previous ~24h of activity.
    # KF2 achieves this via daily log rotation; Palworld uses Docker logs with
    # a time-window filter. We include sessions that started between 06:00
    # yesterday and 06:10 today (a slightly wider window for safety).
    now = datetime.now()
    window_start = (now - timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    window_end = now

    # Fetch docker logs (prioriza archivo persistente del shipper — ver
    # _fetch_docker_logs; protege contra recreaciones de contenedor)
    raw = _fetch_docker_logs(container_id)
    if not raw:
        log.error(f"parse_palworld_log: no docker logs available for {container_id}")
        return sessions

    # Parse events
    # connect_events: user_id -> {ip, ts}  (ts in local time)
    connect_events = {}
    # join_events: list of {name, user_id, ts}  (ts in local time)
    join_events = []
    # leave_events: list of {user_id, ts}  (ts in local time)
    leave_events = []

    for line in raw.splitlines():
        line = line.strip()
        # Connected: "[2026-07-10 16:58:06] [LOG] HolaChao1234 200.83.78.60 connected the server. (User id: steam_76561198071375442)"
        m = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*?connected the server.*?User id: (\w+)', line)
        if m:
            ts_utc = m.group(1)
            user_id = m.group(2)
            ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)\s+connected', line)
            ip = ip_match.group(1) if ip_match else None
            connect_events[user_id] = {"ip": ip, "ts": _utc_to_local(ts_utc)}
            continue

        # Joined: "[2026-07-10 16:58:13] [LOG] Nilo joined the server. (User id: steam_76561198071375442, Player id: ...)"
        m = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*?(\S+) joined the server.*?User id: (\w+)', line)
        if m:
            ts_utc = m.group(1)
            name = m.group(2)
            user_id = m.group(3)
            # Convert to local time before filtering
            ts_local = _utc_to_local(ts_utc)
            # Only include sessions within the 24h window
            try:
                join_dt = datetime.strptime(ts_local, "%Y-%m-%d %H:%M:%S")
                if window_start <= join_dt <= window_end:
                    join_events.append({"name": name, "user_id": user_id, "ts": ts_local})
            except ValueError:
                pass
            continue

        # Left: "[2026-07-10 17:01:05] [LOG] Nilo left the server. (User id: steam_76561198071375442)"
        m = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*?left the server.*?User id: (\w+)', line)
        if m:
            ts_utc = m.group(1)
            user_id = m.group(2)
            leave_events.append({"user_id": user_id, "ts": _utc_to_local(ts_utc)})
            continue

    # Sort leave events by timestamp for chronological matching
    leave_events.sort(key=lambda x: x["ts"])

    used_leaves = set()
    for je in join_events:
        user_id = je["user_id"]
        join_ts = je["ts"]
        session = {
            "name": je["name"],
            "ip": connect_events.get(user_id, {}).get("ip"),
            "join": join_ts,
            "leave": None,
        }

        # Find the first unused leave for this user_id with ts >= join_ts
        for idx, le in enumerate(leave_events):
            if (idx,) in used_leaves:
                continue
            if le["user_id"] != user_id:
                continue
            if le["ts"] >= join_ts:
                session["leave"] = le["ts"]
                used_leaves.add((idx,))
                break

        sessions.append(session)

    return sessions


def calc_duration_palworld(join_str, leave_str):
    """Calculate duration in minutes from Palworld timestamps (YYYY-MM-DD HH:MM:SS)."""
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        j = datetime.strptime(join_str, fmt)
        l = datetime.strptime(leave_str or datetime.now().strftime(fmt), fmt)
        delta = (l - j).total_seconds()
        if delta < 0:
            delta += 86400  # crossed midnight (shouldn't happen with full dates)
        return round(delta / 60.0)
    except Exception:
        return 0


def calc_duration_relative(join_str, leave_str):
    """Calculate duration for relative timestamps (hours from server start).
    Format: H:MM:SS or HH:MM:SS or HHH:MM:SS"""
    def parse_ts(ts):
        parts = ts.split(":")
        if len(parts) >= 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2].split(",")[0])
        return 0
    
    try:
        j = parse_ts(join_str)
        l = parse_ts(leave_str or "0:00:00")
        delta = l - j
        if delta < 0:
            delta = 0
        return int(delta // 60)
    except Exception:
        return 0


def calc_duration(join_str, leave_str):
    """Calculate duration in minutes from HH:MM:SS strings (clock time)."""
    try:
        j = datetime.strptime(join_str, "%H:%M:%S")
        l = datetime.strptime(leave_str or datetime.now().strftime("%H:%M:%S"), "%H:%M:%S")
        delta = (l - j).total_seconds()
        if delta < 0:
            delta += 86400  # crossed midnight
        return int(delta // 60)
    except Exception:
        return 0


def calc_duration_kf2(join_ts, leave_ts):
    """Calculate duration in minutes from KF2 engine timestamps (seconds as float).

    KF2 logs use [NNNN.NI] = seconds since engine start, not wall clock time.
    If leave_ts is None, we can't calculate duration (player still connected or
    log rotated before they left) — return 0.
    """
    try:
        j = float(join_ts)
        l = float(leave_ts) if leave_ts else 0
        if l == 0:
            return 0
        delta = (l - j) / 60.0
        return round(max(delta, 0))
    except Exception:
        return 0


# --- Remote log fetcher ---
def fetch_remote_log(srv):
    """Fetch a log file from a remote host via sshpass/ssh+sudo cat.
    Uses sudo because the log is owned by 'pterodactyl' (mode 600)."""
    import subprocess
    tmp_path = f"/tmp/remote_log_{srv['name']}.log"
    try:
        result = subprocess.run(
            ["sshpass", "-p", srv["remote_log_pass"],
             "ssh", "-o", "StrictHostKeyChecking=no",
             f"{srv['remote_log_user']}@{srv['remote_log_host']}",
             f"sudo cat {srv['remote_log_path']}"],
            capture_output=True, timeout=30
        )
        if result.returncode == 0 and result.stdout:
            with open(tmp_path, "wb") as f:
                f.write(result.stdout)
            return tmp_path
        else:
            log.error(f"fetch_remote_log: ssh failed rc={result.returncode} stderr={result.stderr.decode(errors='replace')[:200]}")
    except Exception as e:
        log.error(f"fetch_remote_log exception: {e}")
    return None


# --- Log rotation + server restart ---
MAX_BACKUPS = 7  # Keep last 7 daily backups


def _clean_old_backups(log_dir, prefix="Launch-backup-", max_keep=MAX_BACKUPS):
    """Remove old backup files, keeping only the most recent max_keep.
    Matches both 'Launch-backup-YYYY...' and 'Launch-backup-manual-YYYY...' patterns."""
    import glob
    pattern = os.path.join(log_dir, f"{prefix}*.log")
    backups = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    for old in backups[max_keep:]:
        try:
            os.remove(old)
            log.info(f"Removed old backup: {old}")
        except Exception:
            pass


def _clean_old_backups_remote(ssh_cmd, log_dir, prefix="Launch-backup-", max_keep=MAX_BACKUPS):
    """Remove old backup files on a remote host via SSH."""
    import subprocess
    cmd = f"ls -t {log_dir}/{prefix}*.log 2>/dev/null | tail -n +{max_keep + 1} | xargs rm -f"
    subprocess.run(ssh_cmd + [cmd], capture_output=True, timeout=15)


def rotate_and_restart_kf2(servers_to_rotate):
    """Back up KF2 logs, restart servers, then truncate logs for fresh daily tracking.

    Flow per server: backup → restart via Panel PHP → wait → truncate.
    Both local and remote KF2 use Panel PHP API for restart (per-container,
    doesn't affect other servers on the node).

    Remote log backup/truncate is done via SSH (file is owned by pterodactyl, mode 600).
    """
    import subprocess
    timestamp = datetime.now().strftime("%Y.%m.%d-%H.%M.%S")

    for srv in servers_to_rotate:
        try:
            is_remote = srv.get("remote")
            server_id = srv["server_id"]

            # Determine log path and SSH access
            if is_remote:
                host = srv["remote_log_host"]
                user = srv["remote_log_user"]
                passwd = srv["remote_log_pass"]
                log_path = srv["remote_log_path"]
                log_dir = log_path.rsplit("/", 1)[0]
                ssh_base = ["sshpass", "-p", passwd, "ssh", "-o", "StrictHostKeyChecking=no",
                            f"{user}@{host}"]
                backup_name = f"Launch-backup-{timestamp}.log"

                # 1. Backup the current log via SSH
                subprocess.run(
                    ssh_base + [f"sudo cp {log_path} {log_dir}/{backup_name} 2>/dev/null; echo BACKUP_DONE"],
                    capture_output=True, timeout=20
                )
                log.info(f"{srv['name']}: backup created ({backup_name})")
            else:
                log_path = srv["log_path"]
                log_dir = log_path.rsplit("/", 1)[0]
                backup_path = f"{log_dir}/Launch-backup-{timestamp}.log"
                subprocess.run(["cp", log_path, backup_path], capture_output=True)
                log.info(f"{srv['name']}: backup created ({os.path.basename(backup_path)})")

            # 2. Restart via Panel PHP API (works for both local and remote servers)
            result = subprocess.run(
                ["php", "artisan", "tinker", "--execute",
                 f"use Pterodactyl\\Models\\Server; use Pterodactyl\\Repositories\\Wings\\DaemonPowerRepository; "
                 f"$s = Server::find({server_id}); $r = app(DaemonPowerRepository::class); "
                 f"$r->setServer($s); $r->send('restart'); echo 'RESTARTED';"],
                capture_output=True, timeout=20, cwd="/var/www/pterodactyl"
            )
            log.info(f"{srv['name']}: restart signal sent via Panel PHP (rc={result.returncode})")

            # 3. Wait for the container to restart and begin writing fresh log
            import time
            time.sleep(8)

            # 4. Truncate the log (clears old content + startup lines from new boot)
            if is_remote:
                subprocess.run(
                    ssh_base + [f"sudo truncate -s 0 {log_path}; echo TRUNCATED"],
                    capture_output=True, timeout=15
                )
            else:
                open(log_path, "w").close()
            log.info(f"{srv['name']}: log truncated")

            # 5. Clean old backups (keep last 7)
            if is_remote:
                _clean_old_backups_remote(ssh_base, log_dir)
            else:
                _clean_old_backups(log_dir)

        except Exception as e:
            log.error(f"Failed to rotate/restart {srv['name']}: {e}")


def rotate_enshrouded_log():
    """Truncate the Enshrouded log after the daily report to prevent stale data."""
    import subprocess
    log_path = "/var/lib/pterodactyl/volumes/6440e54b-b3ca-42a2-aaa3-4fa5e76745bb/logs/enshrouded_server.log"
    try:
        timestamp = datetime.now().strftime("%Y.%m.%d-%H.%M.%S")
        backup = log_path.replace(".log", f"-backup-{timestamp}.log")
        subprocess.run(["cp", log_path, backup], capture_output=True)
        # Truncate (keep last 100 lines for context)
        with open(log_path, errors="replace") as f:
            lines = f.readlines()
        with open(log_path, "w") as f:
            f.writelines(lines[-100:])
        log.info("Enshrouded log rotated")
    except Exception as e:
        log.error(f"Failed to rotate Enshrouded log: {e}")


def rotate_mordhau_log():
    """Back up and truncate the Mordhau UE4 log after the daily report.

    UE4 auto-rotates on restart, but we don't restart Mordhau daily (PlayFab auth
    is fragile). Instead, back up and truncate manually like Enshrouded.
    Keep last 200 lines for startup context (UE4 writes header on boot).
    """
    log_path = "/var/lib/pterodactyl/volumes/e804f1e6-c3e2-4a8d-90c3-bcdd5ae4b61e/Mordhau/Saved/Logs/Mordhau.log"
    try:
        if not os.path.exists(log_path):
            log.warning("Mordhau log not found, skipping rotation")
            return
        timestamp = datetime.now().strftime("%Y.%m.%d-%H.%M.%S")
        backup = log_path.replace("Mordhau.log", f"Mordhau-backup-{timestamp}.log")
        import subprocess
        subprocess.run(["cp", log_path, backup], capture_output=True)
        # Truncate (keep last 200 lines for context)
        with open(log_path, errors="replace") as f:
            lines = f.readlines()
        with open(log_path, "w") as f:
            f.writelines(lines[-200:])
        # Clean old backups (keep last 7)
        log_dir = os.path.dirname(log_path)
        _clean_old_backups(log_dir, prefix="Mordhau-backup-")
        log.info("Mordhau log rotated")
    except Exception as e:
        log.error(f"Failed to rotate Mordhau log: {e}")


# --- Steam name resolver ---
_STEAM_CACHE = {}
def resolve_steam_name(steamid):
    """Resolve a Steam ID to a username using the public profile XML API."""
    if steamid in _STEAM_CACHE:
        return _STEAM_CACHE[steamid]
    try:
        url = f"https://steamcommunity.com/profiles/{steamid}/?xml=1"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = r.read().decode("utf-8", errors="replace")
            m = re.search(r"<steamID><!\[CDATA\[(.+?)\]\]>", data)
            if m:
                name = m.group(1)
                _STEAM_CACHE[steamid] = name
                return name
    except Exception:
        pass
    name = f"Steam:{steamid[-8:]}"
    _STEAM_CACHE[steamid] = name
    return name


# --- Enshrouded log parser ---
def parse_enshrouded_log(log_path, target_date):
    """Parse Enshrouded server log for player sessions.
    Log format: [I HH:MM:SS,mmm] [online] Session accepted with peer (steamid:XXX)
               [I HH:MM:SS,mmm] [SessionPlayer] 'Reserve' (up)!
               [I HH:MM:SS,mmm] [online] Disconnecting peer 0(N)
    Timestamps are hours from server start, not actual time.
    """
    sessions = []
    if not log_path or not os.path.exists(log_path):
        return sessions

    try:
        # Only read last 5000 lines to avoid parsing days of accumulated logs
        with open(log_path, errors="replace") as f:
            all_lines = f.readlines()
        lines = all_lines[-5000:]
    except Exception:
        return sessions

    # Track active peers: peer_number -> {steamid, join_ts}
    active_peers = {}

    for line in lines:
        # Parse join: "Session accepted with peer (steamid:76561198071375442)"
        # and "Added peer 0(N) (steamid:76561198071375442)"
        m = re.search(r'Added peer \d+\((\d+)\).*steamid:(\d+)', line)
        if m:
            peer_num = m.group(1)
            steamid = m.group(2)
            ts_match = re.search(r'\[.\s+(\d+:\d+:\d+)', line)
            active_peers[peer_num] = {"steamid": steamid, "join_ts": ts_match.group(1) if ts_match else "0:00:00"}

        # Parse leave: "Disconnecting peer 0(N)"
        m = re.search(r'Disconnecting peer \d+\((\d+)\)', line)
        if m:
            peer_num = m.group(1)
            if peer_num in active_peers:
                ts_match = re.search(r'\[.\s+(\d+:\d+:\d+)', line)
                session = active_peers.pop(peer_num)
                # Try to resolve Steam username
                name = resolve_steam_name(session["steamid"])
                sessions.append({
                    "name": name,
                    "ip": None,  # Enshrouded doesn't log IPs
                    "steamid": session["steamid"],
                    "join": session["join_ts"],
                    "leave": ts_match.group(1) if ts_match else None,
                })

    # Still connected
    for peer_num, data in active_peers.items():
        name = resolve_steam_name(data["steamid"])
        sessions.append({
            "name": name,
            "ip": None,
            "steamid": data["steamid"],
            "join": data["join_ts"],
            "leave": None,
        })

    return sessions


# --- Mordhau log parser (UE4) ---
def parse_mordhau_log(log_path, target_date):
    """Parse Mordhau UE4 server log for player sessions.

    Log format (UE4, timestamps [YYYY.MM.DD-HH.MM.SS:mmm]):
      Join:  NotifyAcceptedConnection: Name: HRD_Castello, ... RemoteAddr: IP:PORT,
             Name: IpConnection_XXX, Driver: GameNetDriver ..., PC: NULL, Owner: NULL,
             UniqueId: INVALID
      IP change: Updated IP address for connection. ... Name: IpConnection_XXX,
             ... UniqueId: MordhauOnlineSubsystem:HEXID, Old Address = OLD_IP:PORT
      Leave: UChannel::CleanUp: ... RemoteAddr: IP:PORT, Name: IpConnection_XXX,
             Driver: GameNetDriver ..., PC: BP_HordePlayerController_C_XXX, Owner: ...,
             UniqueId: MordhauOnlineSubsystem:HEXID

    Non-player connections (server browser beacon) use:
      Driver: IpNetDriver_XXX (not GameNetDriver) and Owner: MordhauBeaconClient_XXX

    Players are identified by their stable PlayFab UniqueId (MordhauOnlineSubsystem:HEX),
    not by IP — IPs can change mid-session (VPN/WARP/CGNAT). The connect IP is used
    for geo/flag purposes; if an IP change event occurs, the updated IP replaces it.

    The log accumulates since last restart (UE4 auto-rotates on restart).
    Filter sessions to only those that STARTED today.
    """
    sessions = []
    if not log_path or not os.path.exists(log_path):
        return sessions

    # target_date comes as DD-MM (from generate_summary), but log timestamps
    # use YYYY.MM.DD. Build the date prefix for filtering.
    # target_date = "10-08" → date_prefix = "2026.08.10"
    try:
        day, month = target_date.split("-")
        date_prefix = f"{datetime.now().year}.{month}.{day}"
    except Exception:
        date_prefix = datetime.now().strftime("%Y.%m.%d")

    try:
        with open(log_path, errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return sessions

    # Track active GameNetDriver connections by IpConnection name
    active_conns = {}  # IpConnection_XXX -> {ip, join_ts, map, uid}

    conn_re = re.compile(
        r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\].*NotifyAcceptedConnection: Name: (\w+).*'
        r'RemoteAddr: ([\d.]+):\d+.*Name: (IpConnection_\d+).*Driver: (GameNetDriver|IpNetDriver_\d+)'
    )
    # Extract UniqueId from disconnect lines (connect lines have UniqueId: INVALID)
    disconnect_re = re.compile(
        r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\].*UChannel::CleanUp.*'
        r'RemoteAddr: ([\d.]+):\d+.*Name: (IpConnection_\d+).*Driver: (GameNetDriver|IpNetDriver_\d+)'
        r'.*PC: (\w+).*UniqueId: (\S+)'
    )
    # IP change mid-session: "Updated IP address for connection. ... Name: IpConnection_XXX,
    # ... UniqueId: MordhauOnlineSubsystem:HEXID, Old Address = OLD_IP:PORT"
    ip_update_re = re.compile(
        r'Updated IP address for connection.*Name: (IpConnection_\d+).*UniqueId: (\S+?)[,\s]'
        r'.*Old Address = ([\d.]+):\d+'
    )

    for line in lines:
        # IP change mid-session
        m = ip_update_re.search(line)
        if m:
            conn_name, uid, old_ip = m.groups()
            if conn_name in active_conns:
                # The "Updated IP address" line's RemoteAddr is the NEW ip;
                # "Old Address" is the previous one. The conn's current ip
                # (from connect) should already be the old one — update to new.
                # The new IP is in the RemoteAddr of this line.
                new_ip_match = re.search(r'RemoteAddr: ([\d.]+):\d+', line)
                if new_ip_match:
                    active_conns[conn_name]["ip"] = new_ip_match.group(1)
                if uid and uid != "INVALID":
                    active_conns[conn_name]["uid"] = uid
            continue

        # Connection
        m = conn_re.search(line)
        if m:
            ts, mapname, ip, conn_name, driver = m.groups()
            if driver == "GameNetDriver":
                active_conns[conn_name] = {"ip": ip, "join_ts": ts, "map": mapname, "uid": None}
            continue

        # Disconnection
        m = disconnect_re.search(line)
        if m:
            ts, ip, conn_name, driver, pc, uid = m.groups()
            if driver == "GameNetDriver" and pc != "NULL" and "PlayerController" in pc:
                if conn_name in active_conns:
                    conn = active_conns.pop(conn_name)
                    # Use the stable UniqueId as player identifier
                    # Shorten MordhauOnlineSubsystem:HEXID to just the hex (first 8 chars)
                    # Fall back to connect IP if uid not captured
                    if uid and uid != "INVALID":
                        # Extract just the hex part: MordhauOnlineSubsystem:9F6ADF29... → 9F6ADF29
                        player_id = uid.split(":")[-1][:8] if ":" in uid else uid[:8]
                    else:
                        player_id = conn["ip"]
                    # Only include sessions that started on the target date
                    if conn["join_ts"].startswith(date_prefix):
                        sessions.append({
                            "name": player_id,
                            "ip": conn["ip"],  # connect IP (updated if IP change occurred)
                            "join": conn["join_ts"],
                            "leave": ts,
                            "map": conn["map"],
                        })
            continue

    return sessions


def calc_duration_mordhau(join_str, leave_str):
    """Calculate duration in minutes from Mordhau UE4 timestamps (YYYY.MM.DD-HH.MM.SS:mmm)."""
    try:
        fmt = "%Y.%m.%d-%H.%M.%S:%f"
        j = datetime.strptime(join_str, fmt)
        l = datetime.strptime(leave_str or datetime.now().strftime(fmt), fmt)
        delta = (l - j).total_seconds()
        if delta < 0:
            delta += 86400  # crossed midnight
        return int(delta // 60)
    except Exception:
        return 0


# --- KF2 log parser ---
def parse_kf2_log(log_path, target_date):
    """Parse KF2 Launch.log for player sessions. Returns list of sessions.

    KF2 log structure for real players:
    1. "Player login with options ?Name=NAME?...?party1=0xSTEAMID"  (login request)
    2. "PlayerController.CheckPrivileges failed"                      (normal, always present)
    3. "Join succeeded: NAME playerid=0xSTEAMID"                     (actual join)
    4. "CleanUp: clean up connection: RemoteAddr: IP:PORT, PlayerId: 0xSTEAMID" (disconnect)

    Bots/scanners appear as:
    - "NotifyAcceptingConnection: Server TheWorld refused" + CleanUp with PlayerId 0x0000000000000000
    - They never reach "Join succeeded"

    Strategy: use "Join succeeded" to identify real players + their PlayerIds,
    then match CleanUp events by PlayerId to get IPs and leave timestamps.

    NOTE: target_date is not used for filtering. KF2 logs use engine timestamps
    (seconds since server start), not wall-clock dates. The log is rotated daily
    by the report script (backup + truncate + restart), so the log contents
    represent one day of activity by construction.
    """
    sessions = []
    if not log_path or not os.path.exists(log_path):
        return sessions

    try:
        with open(log_path, errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return sessions

    # --- Pass 1: Parse "Join succeeded" events (real players only) ---
    # join_events: list of {name, player_id, join_ts}
    join_events = []

    for line in lines:
        # "Join succeeded: Woof playerid=0x011000016A2F93CE"
        m = re.search(r'Join succeeded:\s*(.+?)\s+playerid=(0x[0-9A-Fa-f]+)', line)
        if m:
            name = m.group(1).strip()
            player_id = m.group(2)
            ts_match = re.search(r'\[(\d+\.\d+)\]', line)
            join_ts = ts_match.group(1) if ts_match else "0"
            join_events.append({
                "name": name,
                "player_id": player_id,
                "join_ts": join_ts,
            })

    # --- Pass 2: Parse CleanUp events with real PlayerIds ---
    # cleanups: player_id -> list of {ip, leave_ts}
    cleanups = defaultdict(list)

    for line in lines:
        m = re.search(
            r'CleanUp: clean up connection: RemoteAddr: (\d+\.\d+\.\d+\.\d+):\d+.*PlayerId: (0x[0-9A-Fa-f]+)',
            line)
        if not m:
            continue

        ip = m.group(1)
        player_id = m.group(2)

        # Skip bots: PlayerId 0x0000000000000000
        if player_id == "0x0000000000000000":
            continue

        ts_match = re.search(r'\[(\d+\.\d+)\]', line)
        leave_ts = ts_match.group(1) if ts_match else None
        cleanups[player_id].append({"ip": ip, "leave_ts": leave_ts})

    # --- Pass 3: Match joins to cleanups by PlayerId ---
    # Each join creates a session; the first unused cleanup for this player
    # that occurs at or after the join time is the leave.
    used_cleanups = set()  # track (player_id, index) used

    # Sort cleanups by timestamp per player_id for chronological matching
    for pid in cleanups:
        cleanups[pid].sort(key=lambda x: float(x["leave_ts"]) if x["leave_ts"] else 0)

    for je in join_events:
        pid = je["player_id"]
        join_t = float(je["join_ts"])
        session = {
            "name": je["name"],
            "ip": None,
            "join_ts": je["join_ts"],
            "leave_ts": None,
        }

        # Find the first unused cleanup for this player_id with leave_ts >= join_ts
        if pid in cleanups:
            for idx, cl in enumerate(cleanups[pid]):
                if (pid, idx) in used_cleanups:
                    continue
                leave_t = float(cl["leave_ts"]) if cl["leave_ts"] else 0
                if leave_t >= join_t:
                    session["ip"] = cl["ip"]
                    session["leave_ts"] = cl["leave_ts"]
                    used_cleanups.add((pid, idx))
                    break

        sessions.append(session)

    return sessions


# --- MC Bots activity ---
def get_mc_bot_activity():
    """Get activity count for each MC bot from journalctl."""
    import subprocess
    results = {}
    for svc in MC_BOT_SERVICES:
        try:
            result = subprocess.run(
                ["journalctl", "-u", svc, "--no-pager", "--since", "today"],
                capture_output=True, text=True, timeout=10
            )
            # Count meaningful events (not just startup messages)
            count = len([l for l in result.stdout.splitlines()
                       if any(k in l for k in ["CHAT", "Spawned", "death", "kicked", "Engaging",
                                               "Collecting", "mining", "exploring", "harvest",
                                               "building", "unstuck", "respawn"])])
            results[svc] = count
        except Exception:
            results[svc] = 0
    return results


# --- Telegram ---
def send_telegram(text):
    """Send message via Telegram bot API.
    Sanitizes control characters and Markdown-breaking characters from player names.
    Tries Markdown first, falls back to plain text if Markdown parsing fails."""
    if not BOT_TOKEN:
        log.error("No bot token found")
        return False

    # Remove control characters (null bytes, etc) that Telegram rejects
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    # Try with Markdown first
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    try:
        with urllib.request.urlopen(url, data, timeout=15) as r:
            d = json.loads(r.read())
            return d.get("ok", False)
    except Exception:
        pass

    # Fallback: send as plain text (no Markdown parsing)
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }).encode()
    try:
        with urllib.request.urlopen(url, data, timeout=15) as r:
            d = json.loads(r.read())
            return d.get("ok", False)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


# --- Update checker ---
STEAMCMD = "/usr/games/steamcmd"
PTERO_VOLUMES = "/var/lib/pterodactyl/volumes"

# Servidores Steam a monitorear: (nombre, appid, uuid, usa_proton)
STEAM_SERVERS = [
    ("Palworld", 2394010, "f8a39b9c-a4eb-40c9-a6ee-c225959b14a8", False),
    ("Palworld 2", 2394010, "42325324-4adc-4a6f-abc4-340f57a8b0e9", False),
    ("KF2", 232130, "48261dfb-5c02-4630-af84-afa325b59b95", False),
    ("KF2 2", 232130, "6047cf29-ebb3-47e1-bba7-a3b98e4e14d6", False),
    ("Mordhau", 629800, "e804f1e6-c3e2-4a8d-90c3-bcdd5ae4b61e", False),
    ("Valheim", 896660, "32e0bff9-e9bd-4258-bb0a-c3ab81eb93cd", False),
    ("Enshrouded", 2278520, "6440e54b-b3ca-42a2-aaa3-4fa5e76745bb", True),
    ("Project Zomboid", 380870, "e70aacdb-e1cc-41a8-9818-44d905f9ca0c", False),
    # DayZ: 5th element = steam user (app 223350 requires account login, NOT anonymous)
    ("DayZ", 223350, "b9944410-1c32-49ef-8897-4411838f3cff", False, "perroil"),
]

# PaperMC: (nombre, uuid, version actual, jar path)
PAPERMC_SERVER = {
    "name": "PaperMC",
    "uuid": "7052e797-3532-46bf-bb9d-7a19a69e2ef0",
    "volume": "/var/lib/pterodactyl/volumes/7052e797-3532-46bf-bb9d-7a19a69e2ef0",
}


def get_installed_buildid(volume_path, appid):
    """Lee el buildid del appmanifest de Steam en el volumen del servidor.
    Si buildid es 0 (instalacion via Proton/wine incompleta), usa TargetBuildID como fallback."""
    manifest = os.path.join(volume_path, "steamapps", f"appmanifest_{appid}.acf")
    if not os.path.exists(manifest):
        return None
    try:
        buildid = None
        target_build = None
        with open(manifest) as f:
            for line in f:
                line = line.strip()
                m = re.search(r'"buildid"\s+"(\d+)"', line)
                if m:
                    buildid = m.group(1)
                m2 = re.search(r'"TargetBuildID"\s+"(\d+)"', line)
                if m2:
                    target_build = m2.group(1)
        # Si buildid es 0 o None, usar TargetBuildID
        if buildid and buildid != "0":
            return buildid
        if target_build and target_build != "0":
            return target_build
        return buildid
    except Exception:
        pass
    return None


def get_latest_buildid(appid):
    """Consulta la API de Steam para el buildid más reciente del app."""
    try:
        url = f"https://api.steamcmd.net/v1/info/{appid}"
        req = urllib.request.Request(url, headers={"User-Agent": "SCL-Bot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        app_info = data.get("data", {}).get(str(appid), {})
        depots = app_info.get("depots", {})
        branches = depots.get("branches", {})
        public = branches.get("public", {})
        return public.get("buildid")
    except Exception as e:
        log.error(f"get_latest_buildid: {e}")
    return None


def get_latest_papermc():
    """Consulta la API v3 de PaperMC para la última versión estable.
    Returns (version, build_id, jar_url, sha256) or None."""
    try:
        UA = "SCL-Bot/1.0 (mailto:christian@nilo.cl)"
        headers = {"User-Agent": UA}

        # Obtener versiones disponibles
        url = "https://fill.papermc.io/v3/projects/paper"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())

        versions = list(data["versions"].keys())
        if not versions:
            return None
        latest_version = versions[0]  # DESC order

        # Obtener builds estables para esa version
        url2 = f"https://fill.papermc.io/v3/projects/paper/versions/{latest_version}/builds"
        req2 = urllib.request.Request(url2, headers=headers)
        with urllib.request.urlopen(req2, timeout=15) as r2:
            builds = json.loads(r2.read())

        stable = [b for b in builds if b.get("channel") == "STABLE"]
        if not stable:
            return None
        latest = stable[0]
        dl = latest["downloads"]["server:default"]
        return (latest_version, latest["id"], dl["url"], dl["checksums"]["sha256"])
    except Exception as e:
        log.error(f"get_latest_papermc: {e}")
    return None


def get_installed_papermc_version(volume_path):
    """Lee la version actual de PaperMC desde los logs del servidor."""
    # El log dice: "Loading Paper X.Y-build for Minecraft X.Y"
    # O revisar el nombre del jar
    log_path = os.path.join(volume_path, "logs", "latest.log")
    if os.path.exists(log_path):
        try:
            with open(log_path, errors="replace") as f:
                for line in f:
                    m = re.search(r'Loading Paper\s+(\S+)', line)
                    if m:
                        return m.group(1).split("-")[0]  # e.g. "26.2" from "26.2-87"
        except Exception:
            pass
    return None


def stop_start_server(uuid, action):
    """Envia señal de stop/start/restart a Wings via PHP tinker."""
    import subprocess
    try:
        result = subprocess.run(
            ["php", "artisan", "tinker", "--execute",
             f"use Pterodactyl\\Models\\Server; use Pterodactyl\\Repositories\\Wings\\DaemonPowerRepository; "
             f"$s = Server::where('uuid', '{uuid}')->first(); $r = app(DaemonPowerRepository::class); "
             f"$r->setServer($s); $r->send('{action}'); echo 'OK';"],
            capture_output=True, timeout=20, cwd="/var/www/pterodactyl"
        )
        return result.returncode == 0 and b"OK" in result.stdout
    except Exception as e:
        log.error(f"stop_start_server: {e}")
    return False


def get_steam_credentials(server_id=18):
    """Lee STEAM_USER/STEAM_PASS del panel DB (server_variables del server DayZ).
    Evita hardcodear credenciales en este script."""
    try:
        out = subprocess.run(
            ["mysql", "panel", "-N", "-B", "-e",
             f"SELECT ev.env_variable, sv.variable_value FROM server_variables sv "
             f"JOIN egg_variables ev ON sv.variable_id=ev.id "
             f"WHERE sv.server_id={server_id} AND ev.env_variable IN ('STEAM_USER','STEAM_PASS')"],
            capture_output=True, text=True, timeout=10
        )
        creds = dict(line.split("\t", 1) for line in out.stdout.strip().splitlines() if "\t" in line)
        return creds.get("STEAM_USER"), creds.get("STEAM_PASS")
    except Exception as e:
        log.error(f"get_steam_credentials: {e}")
        return None, None


def check_and_apply_updates():
    """Revisa si hay actualizaciones disponibles para Palworld y PaperMC.
    Si las hay, las aplica y devuelve un mensaje. Si no, devuelve None."""
    import subprocess
    import time
    updates = []

    # --- Steam servers ---
    for entry in STEAM_SERVERS:
        name, appid, uuid, uses_proton = entry[0], entry[1], entry[2], entry[3]
        steam_user = entry[4] if len(entry) > 4 else None
        volume = os.path.join(PTERO_VOLUMES, uuid)
        installed = get_installed_buildid(volume, appid)
        latest = get_latest_buildid(appid)

        if not installed or not latest:
            log.warning(f"{name}: no se pudo verificar build (installed={installed}, latest={latest})")
            continue

        if installed == latest:
            log.info(f"{name}: build {installed} es la última, sin updates")
            continue

        log.info(f"{name}: update disponible build {installed} -> {latest}")
        updates.append(f"🔄 {name}: build {installed} → {latest}")

        # Aplicar update: detener, SteamCMD, iniciar
        stop_start_server(uuid, "stop")
        time.sleep(8)

        try:
            env = None
            if steam_user:
                # Apps que requieren cuenta (DayZ): login con credenciales del panel
                # y HOME persistente con sentry Steam Guard (/opt/steam-perroil)
                su, sp = get_steam_credentials()
                if su and sp:
                    login_cmd = ["+login", su, sp]
                    env = dict(os.environ, HOME="/opt/steam-perroil")
                else:
                    updates.append(f"   ⚠️ Sin credenciales Steam en panel para {name}")
                    log.error(f"{name}: STEAM_USER/STEAM_PASS no disponibles")
                    stop_start_server(uuid, "start")
                    time.sleep(5)
                    continue
            else:
                login_cmd = ["+login", "anonymous"]
            cmd = [STEAMCMD, f"+force_install_dir", volume] + login_cmd
            if uses_proton:
                cmd.append("+@sSteamCmdForcePlatformType")
                cmd.append("windows")
            cmd += ["+app_update", str(appid), "validate", "+quit"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            if "fully installed" in result.stdout:
                new_build = get_installed_buildid(volume, appid)
                subprocess.run(["chown", "-R", "pterodactyl:pterodactyl", volume],
                               capture_output=True, timeout=30)
                updates.append(f"   ✅ Actualizado a build {new_build}")
                log.info(f"{name}: actualizado a build {new_build}")
            else:
                updates.append(f"   ⚠️ SteamCMD no confirmó instalación")
                log.error(f"{name}: steamcmd output: {result.stdout[-200:]}")
        except Exception as e:
            updates.append(f"   ⚠️ Error: {e}")
            log.error(f"{name}: update failed: {e}")

        stop_start_server(uuid, "start")
        time.sleep(5)

    # --- PaperMC ---
    pmc = PAPERMC_SERVER
    installed_ver = get_installed_papermc_version(pmc["volume"])
    latest_info = get_latest_papermc()

    if installed_ver and latest_info:
        latest_ver, latest_build, jar_url, sha256 = latest_info
        # Comparar version (e.g. "26.2" vs "26.2")
        if installed_ver != latest_ver:
            log.info(f"PaperMC: update disponible {installed_ver} -> {latest_ver}")
            updates.append(f"🔄 PaperMC: {installed_ver} → {latest_ver} (build {latest_build})")

            # Descargar nuevo jar
            import hashlib
            try:
                UA = "SCL-Bot/1.0 (mailto:christian@nilo.cl)"
                req = urllib.request.Request(jar_url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=120) as r:
                    jar_data = r.read()

                if hashlib.sha256(jar_data).hexdigest() == sha256:
                    # Detener servidor
                    stop_start_server(pmc["uuid"], "stop")
                    time.sleep(8)

                    # Backup jar viejo
                    jar_path = os.path.join(pmc["volume"], "server.jar")
                    if os.path.exists(jar_path):
                        os.rename(jar_path, jar_path + ".bak")

                    # Escribir nuevo jar
                    with open(jar_path, "wb") as f:
                        f.write(jar_data)
                    subprocess.run(["chown", "pterodactyl:pterodactyl", jar_path],
                                   capture_output=True)
                    subprocess.run(["chmod", "+x", jar_path], capture_output=True)

                    # Iniciar servidor
                    stop_start_server(pmc["uuid"], "start")
                    time.sleep(5)
                    updates.append(f"   ✅ Actualizado a Paper {latest_ver}-{latest_build}")
                    log.info(f"PaperMC: actualizado a {latest_ver}-{latest_build}")
                else:
                    updates.append(f"   ⚠️ SHA256 mismatch, descarta descarga")
                    log.error("PaperMC: SHA256 mismatch")
            except Exception as e:
                updates.append(f"   ⚠️ Error descargando: {e}")
                log.error(f"PaperMC: update failed: {e}")
                # Asegurar que el servidor vuelva a arrancar
                stop_start_server(pmc["uuid"], "start")
        else:
            log.info(f"PaperMC: {installed_ver} es la última versión")
    else:
        log.warning(f"PaperMC: no se pudo verificar (installed={installed_ver}, latest={latest_info})")

    if updates:
        return "\n".join(updates)
    return None


# --- Main ---
def generate_summary():
    global SERVER_IP
    SERVER_IP = get_public_ip()
    # Fix webadmin URL with current IP
    for s in SERVERS:
        if "webadmin_url" in s and "DYNAMIC_IP" in s["webadmin_url"]:
            s["webadmin_url"] = s["webadmin_url"].replace("DYNAMIC_IP", SERVER_IP)
    today = datetime.now().strftime("%d-%m")
    lines = [f"📊 Resumen SCL San Enrique [{today}]", "━" * 28, ""]

    total_unique = set()
    total_sessions = 0
    total_minutes = 0
    all_ips = []
    all_country_codes = []

    for srv in SERVERS:
        # A2S query for current status
        online, mapname, players, max_players = a2s_query(SERVER_IP, srv["port"])

        # Parse logs for sessions
        sessions = []
        if srv["type"] == "minecraft":
            sessions = parse_mc_log(srv["log_path"], today)
        elif srv["type"] == "source" and "KF2" in srv["name"]:
            # KF2 - local or remote log
            log_path = srv["log_path"]
            if srv.get("remote"):
                log_path = fetch_remote_log(srv)
            if log_path:
                sessions = parse_kf2_log(log_path, today)
        elif srv["type"] == "enshrouded":
            sessions = parse_enshrouded_log(srv["log_path"], today)
        elif srv["type"] == "mordhau":
            sessions = parse_mordhau_log(srv["log_path"], today)
        elif srv["type"] == "palworld":
            sessions = parse_palworld_log(srv["container_id"], today)
        elif srv["type"] == "valheim":
            sessions = parse_valheim_log(srv["container_id"], today)
        elif srv["type"] == "dayz":
            sessions = parse_dayz_adm_log(srv["log_path"], today)

        # Calculate stats
        unique_names = set(s["name"] for s in sessions if s["name"])
        peak = max((1 for _ in sessions), default=0)  # rough peak

        # Duration calculation depends on server type:
        # - Enshrouded: relative timestamps (H:MM:SS from server start)
        # - KF2: engine seconds (float, e.g. 7287.46) — uses join_ts/leave_ts fields
        # - Others (MC): clock time HH:MM:SS — uses join/leave fields
        is_kf2 = "KF2" in srv["name"]
        is_palworld = srv["type"] == "palworld"
        is_mordhau = srv["type"] == "mordhau"
        is_valheim = srv["type"] == "valheim"
        is_dayz = srv["type"] == "dayz"
        if srv["type"] == "enshrouded":
            total_mins = sum(calc_duration_relative(s["join"], s.get("leave")) for s in sessions if "join" in s)
            player_times = defaultdict(int)
            for s in sessions:
                player_times[s["name"]] += calc_duration_relative(s.get("join", "0:00:00"), s.get("leave"))
        elif is_kf2:
            total_mins = sum(calc_duration_kf2(s["join_ts"], s.get("leave_ts")) for s in sessions)
            player_times = defaultdict(int)
            for s in sessions:
                player_times[s["name"]] += calc_duration_kf2(s.get("join_ts", "0"), s.get("leave_ts"))
        elif is_mordhau:
            total_mins = sum(calc_duration_mordhau(s["join"], s.get("leave")) for s in sessions)
            player_times = defaultdict(int)
            for s in sessions:
                player_times[s["name"]] += calc_duration_mordhau(s.get("join", ""), s.get("leave"))
        elif is_palworld:
            total_mins = sum(calc_duration_palworld(s["join"], s.get("leave")) for s in sessions if "join" in s)
            player_times = defaultdict(int)
            for s in sessions:
                player_times[s["name"]] += calc_duration_palworld(s.get("join", ""), s.get("leave"))
        elif is_valheim or is_dayz:
            calc_fn = calc_duration_valheim if is_valheim else calc_duration_dayz
            total_mins = sum(calc_fn(s["join"], s.get("leave")) for s in sessions if "join" in s)
            player_times = defaultdict(int)
            for s in sessions:
                player_times[s["name"]] += calc_fn(s.get("join", ""), s.get("leave"))
        else:
            total_mins = sum(calc_duration(s["join"], s.get("leave")) for s in sessions if "join" in s)
            player_times = defaultdict(int)
            for s in sessions:
                player_times[s["name"]] += calc_duration(s.get("join", "00:00:00"), s.get("leave"))
        player_ips = [s["ip"] for s in sessions if s["ip"] and s["ip"] != SERVER_IP]
        # Unique IPs per player for consistent flag counting
        player_unique_ips = list(set(player_ips))

        # For non-log servers, use A2S current count as peak
        if not sessions and online:
            peak = players

        # Accumulate totals
        total_unique.update(unique_names)
        total_sessions += len(sessions)
        total_minutes += total_mins
        all_ips.extend(player_unique_ips)

        # No sessions = sin actividad; distinguish live-but-empty from offline
        if not sessions:
            if online:
                mp = max_players if max_players else "?"
                lines.append(f"{srv['emoji']} {srv['name']} — sin actividad (online {players}/{mp})")
            else:
                lines.append(f"{srv['emoji']} {srv['name']} — sin actividad (offline)")
            lines.append("")
            continue

        # Map name if available (skip server names and generic names)
        map_info = ""
        if mapname and mapname not in ("SCL_San_Enrique", "SCLSanEnrique", ""):
            map_info = f" — {mapname}"

        # Get max_players: from A2S or fallback to known values
        if not max_players or max_players == 0:
            max_players = {"PaperMC": 10, "KF2": 6, "Mordhau": 48,
                          "Enshrouded": 6, "Quake 2": 16, "Valheim": 10,
                          "Palworld": 10, "Palworld 2": 10, "Project Zomboid": 16,
                          "DayZ": 20}.get(srv["name"], "?")

        # Calculate peak from sessions (count overlapping sessions)
        if sessions:
            # Peak: max simultaneous players using sweep line
            # KF2 timestamps are float strings (seconds since engine start) — must sort numerically
            events = []
            for s in sessions:
                if is_kf2:
                    join_t = float(s.get("join_ts", "0"))
                    events.append((join_t, 1))
                    if s.get("leave_ts"):
                        events.append((float(s["leave_ts"]), -1))
                else:
                    events.append((s.get("join", "00:00:00"), 1))
                    if s.get("leave"):
                        events.append((s["leave"], -1))
            events.sort()
            current = 0
            peak = 0
            for _, delta in events:
                current += delta
                peak = max(peak, current)
            peak = max(peak, 1)

        unique_count = len(unique_names)
        # Format duration
        if total_mins >= 60:
            dur = f"{total_mins // 60}h{total_mins % 60:02d}m"
        else:
            dur = f"{total_mins}m"

        # Top player by duration (player_times already calculated above)
        top_player = max(player_times, key=player_times.get, default=None)
        top_time = player_times.get(top_player, 0)
        top_str = ""
        if top_player:
            if top_time >= 60:
                top_str = f" | Top: {top_player} {top_time//60}h{top_time%60:02d}m"
            else:
                top_str = f" | Top: {top_player} {top_time}m"

        # Flags — use unique IPs for consistency with totals
        flags_str = ""
        if player_unique_ips:
            flags_str = f"\n   {format_flags(player_unique_ips)}"

        # Bot info for MC
        bot_str = ""
        if srv["type"] == "minecraft":
            bot_activity = get_mc_bot_activity()
            active_bots = sum(1 for v in bot_activity.values() if v > 0)
            bot_str = f" | Bots: {active_bots}/6"

        # Format output line — KF2 peak has no denominator (mod VS can raise max to 12)
        peak_display = f"peak {peak}" if is_kf2 else f"peak {peak}/{max_players}"
        lines.append(f"{srv['emoji']} {srv['name']}{map_info}")
        lines.append(f"   {unique_count} únicos | {peak_display} | {len(sessions)} sesiones | {dur}{top_str}{bot_str}{flags_str}")
        lines.append("")

    # Totals
    lines.append("━" * 28)

    # Total flags
    if all_ips:
        # Deduplicate IPs by player name to avoid counting same person multiple times
        # Actually just show unique IPs
        unique_ips = list(set(all_ips))
        flags = format_flags(unique_ips)
        # Count by country
        country_counts = defaultdict(int)
        for ip in unique_ips:
            _, code = geo_lookup(ip)
            if code and code != "LOCAL":
                country_counts[code] += 1
            else:
                country_counts["??"] += 1
        flag_summary = " ".join(
            f"{country_flag(c) if c != '??' else '❓'}{'×'+str(cnt) if cnt > 1 else ''}"
            for c, cnt in sorted(country_counts.items(), key=lambda x: -x[1])
        )
    else:
        flag_summary = ""

    dur_total = f"{total_minutes // 60}h{total_minutes % 60:02d}m" if total_minutes >= 60 else f"{total_minutes}m"
    lines.append(f"📈 {len(total_unique)} jugadores únicos | {total_sessions} sesiones | {dur_total}")
    if flag_summary:
        lines.append(flag_summary)

    return "\n".join(lines)


if __name__ == "__main__":
    summary = generate_summary()
    print(summary)

    # Save daily report to /opt/scl-bot/reports/report_YYYY-MM-DD.txt
    report_dir = "/opt/scl-bot/reports"
    os.makedirs(report_dir, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    report_path = os.path.join(report_dir, f"report_{today_str}.txt")
    with open(report_path, "w") as f:
        f.write(summary)
    print(f"Report saved to {report_path}")

    print("\n--- Sending to Telegram ---")
    if send_telegram(summary):
        print("Sent OK")
    else:
        print("Send FAILED")
    
    # Rotate KF2 logs and restart servers for fresh daily tracking
    print("\n--- Rotating KF2 logs ---")
    kf2_servers = [s for s in SERVERS if "KF2" in s["name"]]
    rotate_and_restart_kf2(kf2_servers)
    # Rotate Enshrouded log too
    rotate_enshrouded_log()
    # Rotate Mordhau log (no restart — PlayFab auth is fragile)
    rotate_mordhau_log()
    print("Rotation done")

    # Check for server updates (Palworld, PaperMC)
    print("\n--- Checking for updates ---")
    update_msg = check_and_apply_updates()
    if update_msg:
        print(f"Updates found:\n{update_msg}")
        send_telegram(f"🔔 Actualizaciones de servidores:\n\n{update_msg}")
    else:
        print("No updates available")