#!/data/data/com.termux/files/usr/bin/bash
# Despliegue del parche de parser PocketStrike en el Redmi (via SSH).
# Aborta sin tocar el server vivo si el parche o el smoke test fallan.
set -e
PSDIR="$HOME/PocketStrike-AI"

echo "== 1. Backup y parche =="
cd "$PSDIR"
python3 ~/patch_ps.py server.py

echo "== 2. Smoke test =="
python3 - <<'PYEOF'
import re, ast
src = open('server.py.patched').read()
assert 'POCKETSTRIKE PARSER FIX' in src, 'marker ausente'
compile(src, 'server_patched', 'exec')
ns = {'re': re, 'ast': ast}
blk = src.split('# --- POCKETSTRIKE PARSER FIX v1 ---')[1].split('# --- FIN POCKETSTRIKE PARSER FIX v1 ---')[0]
exec(blk, ns)
code = "x = 0  # fix \"\nfound.add(body[start:idx+len(ext)])"
esc = code.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
tc = '[TOOL_CALL: write_file_content(file_path="t.py", content="' + esc + '")]'
r = ns['extract_tool_call'](tc)
assert r, 'extractor fallo'
kw = ns['parse_arguments_fixed'](r[1])
assert kw.get('content') == code, 'contenido truncado: %r' % kw.get('content')
assert kw.get('file_path') == 't.py'
print('SMOKE OK: parser fix funcional')
PYEOF

echo "== 3. Swap y reinicio =="
cp server.py.patched server.py
pkill -9 -f 'serve[r]\.py' || true
sleep 1
nohup python3 -u server.py > "$HOME/pocketstrike.log" 2>&1 &
sleep 5
if pgrep -fl 'serve[r]\.py' > /dev/null; then
  echo "PROCESO VIVO"
else
  echo "PROCESO MUERTO — log:"
  tail -c 600 "$HOME/pocketstrike.log"
  exit 1
fi
echo "== 4. Log inicial =="
tail -c 400 "$HOME/pocketstrike.log"