#!/usr/bin/env python3
"""patch_go_rail.py — añade los headers del rail OpenCode Go a PocketStrike server.py.

Inserta User-Agent + x-opencode-session (condicional a "opencode.ai" en base_url)
en cada sitio de construcción de headers del camino OpenAI-compatible. Inerte
para cualquier otro proveedor (Ollama incluido).

- Anti-doble-aplicado: marcador POCKETSTRIKE OPENCODE GO RAIL v1.
- Aborta sin tocar nada si el ancla no matchea.
- Backup server.py.bak-gorail-<ts> antes del swap atómico.
- Verifica sintaxis con compile() antes de escribir.
"""
import os
import shutil
import sys
import time

P = os.path.expanduser("~/PocketStrike-AI/server.py")

ANCHOR = (
    '            headers = {"Content-Type": "application/json"}\n'
    '            if api_key:\n'
    '                headers["Authorization"] = f"Bearer {api_key}"\n'
)
ADD = (
    '            headers = {"Content-Type": "application/json"}\n'
    '            if api_key:\n'
    '                headers["Authorization"] = f"Bearer {api_key}"\n'
    '            # --- POCKETSTRIKE OPENCODE GO RAIL v1 ---\n'
    '            if "opencode.ai" in (base_url or ""):\n'
    '                headers["User-Agent"] = "PocketStrike-AI/1.0 (cfnd)"\n'
    '                headers["x-opencode-session"] = "pocketstrike-cfnd"\n'
    '            # --- END POCKETSTRIKE OPENCODE GO RAIL v1 ---\n'
)
MARKER = "POCKETSTRIKE OPENCODE GO RAIL v1"

src = open(P, encoding="utf-8").read()
if MARKER in src:
    print("YA ESTA PARCHEADO — nada que hacer.")
    sys.exit(0)

n = src.count(ANCHOR)
if n == 0:
    print("ANCLA NO ENCONTRADA — ABORT sin tocar server.py")
    sys.exit(1)
print("ancla encontrada %d vez/veces" % n)

new = src.replace(ANCHOR, ADD)
compile(new, P, "exec")  # syntax check — revienta si quedó mal

bak = P + ".bak-gorail-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(P, bak)
tmp = P + ".gorail.tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write(new)
os.replace(tmp, P)
print("PARCHE OK: %d sitio(s), backup %s" % (n, os.path.basename(bak)))