#!/data/data/com.termux/files/usr/bin/bash
# restart_ps.sh — reinicio seguro de PocketStrike en el Poco X3 Pro.
# Matarr el proceso con el truco de corchetes (no matchear la sesion SSH propia),
# arrancar con nohup y verificar vida. Log: ~/pocketstrike.log
set -u
cd ~/PocketStrike-AI || { echo "NO_DIR"; exit 1; }
pkill -9 -f 'serve[r]\.py'
sleep 2
nohup python3 -u server.py > ~/pocketstrike.log 2>&1 &
sleep 8
if pgrep -f 'serve[r]\.py' >/dev/null; then
  echo "BOT ARRIBA (pid $(pgrep -f 'serve[r]\.py'))"
else
  echo "BOT NO ARRANCO — tail del log:"
  tail -20 ~/pocketstrike.log | sed -E 's/[A-Za-z0-9_:-]{30,}/<MASKED>/g'
  exit 1
fi