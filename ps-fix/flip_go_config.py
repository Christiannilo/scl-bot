#!/usr/bin/env python3
"""flip_go_config.py — cambia config.json de PocketStrike al rail OpenCode Go.

Re-valida la key en vivo (1 micro-request) ANTES de tocar nada; solo si HTTP 200
hace el flip: base_url -> https://opencode.ai/zen/go/v1, model -> deepseek-v4.1-flash,
provider_name actualizado. provider sigue "custom". Backup config.json.bak-ollama-<ts>.
Aborta sin cambios si la key no está puesta o la re-validación falla.
"""
import json
import os
import shutil
import sys
import time

import requests

CFG = os.path.expanduser("~/PocketStrike-AI/config.json")

c = json.load(open(CFG, encoding="utf-8"))
k = c.get("api_key", "")
if len(k) < 20:
    print("X config.json NO tiene la key de Go (sigue dummy). Corre set_go_key.py primero.")
    sys.exit(1)

print("Re-validando la key contra opencode.ai antes del flip...")
try:
    r = requests.post(
        "https://opencode.ai/zen/go/v1/chat/completions",
        json={
            "model": "deepseek-v4.1-flash",
            "messages": [{"role": "user", "content": "Responde solo con la palabra: pong"}],
            "max_tokens": 2500,
        },
        headers={
            "Authorization": "Bearer " + k,
            "x-opencode-session": "pocketstrike-setkey-validate",
            "User-Agent": "PocketStrike-AI/1.0 (set_go_key)",
        },
        timeout=90,
    )
except Exception as e:
    print("X Error de red: %s — NO flipo nada." % e)
    sys.exit(1)

if r.status_code != 200:
    print("X Re-validación HTTP %d — la key no sirve, NO flipo nada." % r.status_code)
    print("  " + r.text[:200])
    sys.exit(1)
print("OK key re-validada (HTTP 200)")

bak = CFG + ".bak-ollama-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(CFG, bak)

c["base_url"] = "https://opencode.ai/zen/go/v1"
c["model"] = "deepseek-v4.1-flash"
c["provider_name"] = "OpenCode Go directo (opencode.ai)"

tmp = CFG + ".flip.tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(c, f, indent=2, ensure_ascii=False)
os.replace(tmp, CFG)
os.chmod(CFG, 0o600)

print("OK FLIP aplicado:")
print("   provider   =", c["provider"])
print("   base_url   =", c["base_url"])
print("   model      =", c["model"])
print("   provider_name =", c["provider_name"])
print("   rollback   : cp %s config.json && reiniciar bot" % os.path.basename(bak))