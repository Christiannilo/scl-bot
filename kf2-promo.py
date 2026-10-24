#!/usr/bin/env python3
"""kf2-promo — Saludos y promoción Mazapanchito en los servidores KF2.

Daemon ligero que vigila Launch.log de ambos KF2 y anuncia vía WebAdmin:

  * Join de jugador real  → saludo personalizado en el chat.
  * Fin de partida        → mensaje promocional (solo al final, mínima intrusión).

Arquitectura (validada en pruebas 14/15-sep-2026):
  * Launch.log mezcla encodings POR LÍNEA: ASCII/UTF-8, regiones UTF-16LE
    (dejan un \x00 huérfano al partir por \n) y letras latinas en 1 byte
    (latin-1: ñ=0xF1, ó=0xF3). decode_log_line() maneja los tres casos y
    preserva tildes/eñes en los nombres (antes: 'tamaño' → 'tamao').
  * WebAdmin login: usuario "Admin", contraseña en TEXTO PLANO (BasicWebAdminAuth
    no soporta sha1 — el hash $sha1$ es rechazado).
  * Chat: POST a /ServerAdmin/current/chat con {rnd, message}.
  * Fin de partida: transición CurrentWave N→1 con bInProgress True→False en
    telemetría SetGameServerInstanceTags del log.
  * La rotación diaria (06:10) trunca el log y reinicia los servidores: el daemon
    detecta inode/inode-less truncation y resetea offsets; el login de WebAdmin
    se renueva si la sesión caduca.

Mensajes: solo 1 promo por partida (al final) + saludos por join. Sin spam.
"""
import hashlib
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
PANEL_ID_1 = "48261dfb-5c02-4630-af84-afa325b59b95"   # www.mazapanchito.cl (7777)
PANEL_ID_2 = "6047cf29-ebb3-47e1-bba7-a3b98e4e14d6"   # www.mazapanchito.cl 2 (7778)
VOLUME_BASE = "/var/lib/pterodactyl/volumes"
WEBADMIN_USER = os.environ.get("KF2_WEBADMIN_USER", "Admin")
WEBADMIN_PASS = os.environ.get("KF2_WEBADMIN_PASS", "")
if not WEBADMIN_PASS:
    sys.exit("KF2_WEBADMIN_PASS no definido (ver /root/.scl-bot.env)")

POLL_INTERVAL = 5          # segundos entre lecturas de log
RECONNECT_INTERVAL = 30    # reintentos WebAdmin si está caído
MAX_MSG_LEN = 120          # KF2 chat no muestra mensajes eternos
COOLDOWN_JOIN_S = 60 * 3   # no re-saludar al mismo playerid dentro de 3 min
MAX_GREET_AGE_S = 60 * 15  # saludo en cola caduca si nunca inicia una wave

# Plantillas de mensajes. {name} = nombre del jugador. ≤120 chars (HUD KF2).
JOIN_MSGS = [
    "¡Bienvenido {name}! Mazapanchito: mazapan+manjar+chocolate. 15% OFF "
    "PLACERPARAUNGAMER www.mazapanchito.cl",
    "Hola {name}! Prueba Mazapanchito (mazapan+manjar+chocolate) 15% OFF: "
    "PLACERPARAUNGAMER www.mazapanchito.cl",
    "Dosh y manjar, {name}! Mazapanchito 15% OFF con PLACERPARAUNGAMER "
    "en www.mazapanchito.cl",
]
ENDGAME_MSGS = [
    "¡Gran partida! Celebra con Mazapanchito: 15% OFF con PLACERPARAUNGAMER "
    "en www.mazapanchito.cl",
    "Wave final superada! Mazapanchito 15% OFF con PLACERPARAUNGAMER "
    "en www.mazapanchito.cl",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kf2-promo] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("kf2-promo")

# ----------------------------------------------------------------------------
# WebAdmin client
# ----------------------------------------------------------------------------
class WebAdminClient:
    def __init__(self, ip, port, user=WEBADMIN_USER, password=WEBADMIN_PASS, label=""):
        self.base = f"http://{ip}:{port}/ServerAdmin"
        self.user = user
        self.password = password
        self.label = label or f"{ip}:{port}"
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj))
        self.logged_in = False
        self._rnd = None

    def _get(self, path):
        return self.op.open(self.base + path, timeout=10).read().decode(
            "utf-8", errors="replace")

    def _post(self, path, fields):
        data = urllib.parse.urlencode(fields).encode()
        return self.op.open(self.base + path, data=data, timeout=10).read().decode(
            "utf-8", errors="replace")

    def login(self):
        html = self._get("/")
        m = re.search(r'name="token" value="([0-9A-Fa-f]+)"', html)
        if not m:
            raise RuntimeError(f"{self.label}: login page sin token")
        # BasicWebAdminAuth no soporta sha1 → contraseña en texto plano
        resp = self._post("/", {"token": m.group(1), "username": self.user,
                                "password": self.password, "remember": "-1"})
        if "loginform" in resp:
            raise RuntimeError(f"{self.label}: login rechazado")
        self.logged_in = True
        log.info(f"{self.label}: WebAdmin login OK")

    def _ensure(self):
        if not self.logged_in:
            self.login()

    def _is_login_page(self, html):
        return "loginform" in html

    def say(self, message):
        """Envía mensaje al chat del servidor. Re-login si la sesión caducó."""
        if len(message) > MAX_MSG_LEN:
            message = message[:MAX_MSG_LEN - 1] + "…"
        for attempt in (1, 2):
            try:
                self._ensure()
                chat = self._get("/current/chat")
                if self._is_login_page(chat):
                    self.logged_in = False
                    continue
                m = re.search(r'name="rnd" value="(\d+)"', chat)
                if not m:
                    raise RuntimeError("chat page sin rnd")
                self._post("/current/chat", {"rnd": m.group(1), "message": message})
                return True
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError,
                    socket.timeout, OSError) as e:
                self.logged_in = False
                if attempt == 2:
                    log.warning(f"{self.label}: say() falló: {e}")
                    return False
        return False


# ----------------------------------------------------------------------------
# Log tailer con detección de rotación/truncate
# ----------------------------------------------------------------------------
RE_JOIN = re.compile(r"Join succeeded:\s*(.+?)\s+playerid=(0x[0-9A-Fa-f]+)")
RE_TAGS = re.compile(r'Server/SetGameServerInstanceTags with data: (\{"LobbyId.*\})')
RE_WAVE = re.compile(r'"CurrentWave":"(\d+)"')


def decode_log_line(raw):
    """Decodifica una línea binaria de Launch.log preservando tildes/eñes.

    Encodings mixtos por línea (comportamiento de UE3):
      * texto ASCII/UTF-8 plano,
      * regiones UTF-16LE: al partir por b"\n" la línea siguiente hereda un
        \x00 inicial huérfano (el \n en UTF-16 es 0A 00 y el split corta
        entre ambos bytes),
      * letras latinas sueltas en 1 byte (latin-1).
    """
    if raw.startswith(b"\x00"):
        raw = raw[1:]               # nul huérfano heredado de la línea UTF-16 previa
    if b"\x00" in raw:              # el contenido mismo es UTF-16LE
        line = raw.decode("utf-16-le", errors="replace")
    else:
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            line = raw.decode("latin-1")   # nunca falla: 1 byte = 1 char
    return line.replace("\r", "").replace("\x00", "")


class LogTailer:
    """Tail binario de Launch.log; tolera truncate (rotación 06:10) y recreate."""

    def __init__(self, path):
        self.path = path
        self.inode = None
        self.size = 0
        self.buffer = b""
        self.rotated = False  # se leyó por el watcher tras una rotación

    def _open_state(self):
        try:
            st = os.stat(self.path)
            self.inode = st.st_ino
            self.size = st.st_size
        except FileNotFoundError:
            self.inode, self.size = None, 0

    def start_from_end(self):
        """Posición inicial: final del archivo (solo eventos nuevos)."""
        self._open_state()
        log.info(f"tailer: {os.path.basename(os.path.dirname(self.path))} "
                 f"desde offset {self.size} (inode {self.inode})")

    def read_new_lines(self):
        """Devuelve lista de líneas nuevas (str, decodificadas tolerante)."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return []
        # Rotación/recreate: reiniciar buffer
        if self.inode is not None and st.st_ino != self.inode:
            log.info("tailer: log recreado (rotación) — reset")
            self.buffer = b""
            self.rotated = True
            self._open_state()
            return []
        # Truncate (rotación in-place): reiniciar
        if st.st_size < self.size:
            log.info("tailer: log truncado (rotación) — reset")
            self.buffer = b""
            self.size = 0
            self.rotated = True
        if st.st_size == self.size:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.size)
                chunk = f.read(st.st_size - self.size)
        except OSError:
            return []
        self.size = st.st_size
        self.buffer += chunk
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()  # última línea puede estar incompleta
        out = []
        for raw in lines:
            line = decode_log_line(raw)
            if line.strip():
                out.append(line)
        return out


# ----------------------------------------------------------------------------
# Watcher por servidor
# ----------------------------------------------------------------------------
class KF2Watcher:
    def __init__(self, panel_id, port, label):
        self.panel_id = panel_id
        self.port = port
        self.label = label
        self.volume = os.path.join(VOLUME_BASE, panel_id)
        self.log_path = os.path.join(self.volume, "KFGame", "Logs", "Launch.log")
        self.tailer = LogTailer(self.log_path)
        self.client = None
        self.client_ip = None
        # Estado de partida: (wave, in_progress) — None hasta ver telemetría
        self.game_state = None
        # Anti-spam joins: playerid → último saludo (epoch)
        self.last_greet = {}
        self.msg_cursor = {"join": 0, "end": 0}
        # Saludos en cola: esperan al inicio de una wave para enviarse
        # (con Join succeeded el jugador sigue en selección de personaje y
        # el chat del HUD no se renderiza — no lo ve). Al iniciar la wave
        # ya está en el mapa. (playerid, deadline_epoch, mensaje)
        self.greet_queue = []
        # Cola de reintento para says fallidos (WebAdmin se reinicia al
        # cambiar de mapa y puede rechazar por unos segundos)
        self.pending = []  # [(deadline_epoch, message)]

    # -- container IP (dinámica; cambia al reiniciar contenedor) ----------
    def get_container_ip(self):
        try:
            r = subprocess.run(
                ["docker", "inspect", "-f",
                 "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                 self.panel_id],
                capture_output=True, text=True, timeout=10)
            ip = r.stdout.strip()
            return ip or None
        except Exception:
            return None

    def ensure_client(self):
        ip = self.get_container_ip()
        if not ip:
            return None
        if self.client and self.client_ip == ip:
            return self.client
        self.client = WebAdminClient(ip, self.port, label=self.label)
        self.client_ip = ip
        return self.client

    # -- eventos ------------------------------------------------------------
    def process_line(self, line):
        # 1) Join de jugador real
        m = RE_JOIN.search(line)
        if m:
            name_raw, player_id = m.group(1), m.group(2)
            name = self.sanitize_name(name_raw)
            if name and player_id not in ("0x0000000000000000",):
                self.on_join(name, player_id)
            return
        # 2) Telemetría de estado de juego (fin de partida)
        m = RE_TAGS.search(line)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                return
            tags = data.get("Tags") or {}
            wave = tags.get("CurrentWave")
            prog = tags.get("bInProgress")
            if wave is None or prog is None:
                return
            # Tras rotación/reinicio del server, descartar estado previo:
            # la primera telemetría solo establece baseline (sin promo).
            if self.tailer.rotated:
                self.tailer.rotated = False
                self.game_state = (wave, prog)
                log.info(f"{self.label}: baseline tras rotación: wave={wave} prog={prog}")
                return
            prev = self.game_state
            self.game_state = (wave, prog)
            if prev is None:
                return
            # --- INICIO DE WAVE: liberar saludos encolados -----------------
            # Una wave "inicia" cuando:
            #  a) la partida arranca: False→True (lobby → wave 1), o
            #  b) avanza la wave con la partida en progreso (N→N+1),
            #  En ambos casos los jugadores ya están en el mapa viendo el
            #  chat del HUD (no en la pantalla de selección de personaje).
            wave_started = (
                (prev[1] == "False" and prog == "True")
                or (prog == "True" and wave.isdigit() and prev[0].isdigit()
                    and int(wave) == int(prev[0]) + 1)
            )
            if wave_started:
                self.flush_greet_queue()
            # --- Fin de partida: partida en progreso → terminó (wave vuelve a 1
            # o bInProgress pasa a False). Solo cuenta si la partida tuvo
            # sustancia (alcanzó wave >= 4) — evita spam por joins fugaces
            # de 1 jugador que entra y se va en wave 1.
            if prev[1] == "True" and (prog == "False" or wave == "1"):
                try:
                    reached_wave = int(prev[0])
                except ValueError:
                    reached_wave = 0
                if reached_wave >= 4:
                    self.on_game_end()

    @staticmethod
    def sanitize_name(raw):
        """Limpia basura de decodificación; conserva tildes, ñ y puntuación
        común de nicks (^ " ' ! ? etc.). El nombre viaja urlencoded en el POST
        del chat, así que no hay riesgo con caracteres sueltos."""
        cleaned = re.sub(r"[^\w\s\-\.\\\[\]=\^\"'!?¿¡@#%&*+~_:;,|()/{}<>]",
                         "", raw, flags=re.UNICODE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        # Nicks reales de Steam siempre traen letras/dígitos ASCII; sin ellos
        # es basura de decodificación (p.ej. 'ÏÄ' de un nombre no-latin).
        if len(cleaned) >= 2 and re.search(r"[A-Za-z0-9]", cleaned):
            return cleaned
        return None

    def say_now(self, message):
        """Envía inmediatamente; encola para reintento si falla."""
        client = self.ensure_client()
        if not client:
            self.pending.append((time.time() + 90, message))
            return
        if not client.say(message):
            self.pending.append((time.time() + 90, message))

    def flush_pending(self):
        """Reintenta mensajes en cola (WebAdmin puede tardar en volver tras
        un cambio de mapa). Descarta los que caducaron (>5 min)."""
        if not self.pending:
            return
        client = self.ensure_client()
        if not client:
            return
        still = []
        for deadline, msg in self.pending:
            if time.time() - deadline > 210:  # caducó
                log.warning(f"{self.label}: mensaje caducado descartado: {msg[:40]}…")
                continue
            if client.say(msg):
                log.info(f"{self.label}: say reintentado OK: {msg[:40]}…")
            else:
                still.append((deadline, msg))
        self.pending = still

    def on_join(self, name, player_id):
        now = time.time()
        last = self.last_greet.get(player_id, 0)
        if now - last < COOLDOWN_JOIN_S:
            return
        self.last_greet[player_id] = now
        tmpl = JOIN_MSGS[self.msg_cursor["join"] % len(JOIN_MSGS)]
        self.msg_cursor["join"] += 1
        # Recorta el nombre (no el mensaje) para no superar los 120 chars del
        # HUD: la URL y el código PLACERPARAUNGAMER nunca salen cortados.
        full_name = name
        overhead = len(tmpl.format(name=""))
        avail = MAX_MSG_LEN - overhead
        if len(name) > avail:
            name = name[:avail - 1].rstrip() + "…"
        msg = tmpl.format(name=name)
        # ENCOLAR, no enviar: con Join succeeded el jugador sigue en la
        # pantalla de selección de personaje — el chat del HUD no se renderiza
        # ahí y el mensaje se pierde. Se envía al iniciar la próxima wave.
        self.greet_queue.append((player_id, now + MAX_GREET_AGE_S, msg))
        log.info(f"{self.label}: JOIN {full_name!r} → saludo en cola ({len(self.greet_queue)} pendientes)")
        # Limpieza periódica del anti-spam
        if len(self.last_greet) > 200:
            cutoff = now - COOLDOWN_JOIN_S
            self.last_greet = {k: v for k, v in self.last_greet.items() if v > cutoff}

    def flush_greet_queue(self):
        """Envía los saludos encolados al iniciar una wave (jugador ya en mapa).
        Expira los saludos de jugadores que nunca alcanzaron a jugar."""
        if not self.greet_queue:
            return
        now = time.time()
        due = []
        for player_id, deadline, msg in self.greet_queue:
            if now > deadline:
                log.info(f"{self.label}: saludo caducado descartado (jugador nunca vio una wave)")
                continue
            due.append(msg)
        self.greet_queue = []
        # Enviar individual: mantiene la personalización por nombre (≤3 líneas
        # por wave en la práctica — la cola solo acumula joins desde la wave
        # anterior y el cooldown anti-spam filtra repeticiones).
        for msg in due:
            log.info(f"{self.label}: WAVE INICIA → liberando saludo")
            self.say_now(msg)

    def on_game_end(self):
        msg = ENDGAME_MSGS[self.msg_cursor["end"] % len(ENDGAME_MSGS)]
        self.msg_cursor["end"] += 1
        log.info(f"{self.label}: FIN DE PARTIDA → promo")
        self.say_now(msg)


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------
WATCHERS = []


def main():
    global WATCHERS
    WATCHERS = [
        KF2Watcher(PANEL_ID_1, 8082, "KF2-1"),
        KF2Watcher(PANEL_ID_2, 8083, "KF2-2"),
    ]
    for w in WATCHERS:
        w.tailer.start_from_end()

    stop = {"flag": False}

    def _stop(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info(f"Daemon iniciado — {len(WATCHERS)} servidores, "
             f"poll {POLL_INTERVAL}s")
    while not stop["flag"]:
        for w in WATCHERS:
            try:
                lines = w.tailer.read_new_lines()
                for line in lines:
                    w.process_line(line)
                w.flush_pending()
            except Exception as e:
                log.warning(f"{w.label}: error en ciclo: {e}")
        time.sleep(POLL_INTERVAL)
    log.info("Daemon detenido")


if __name__ == "__main__":
    main()