#!/bin/bash
# Watcher de crashes Palworld - sale con notify_on_complete al detectar el primer crash
C=f8a39b9c-a4eb-40c9-a6ee-c225959b14a8
LOGDIR=/opt/scl-bot/reports/palworld-crash-watch
mkdir -p "$LOGDIR"
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$LOGDIR/crash-$STAMP.txt"

journalctl -u wings -f -n 0 --no-pager 2>/dev/null | while read -r line; do
  if printf '%s' "$line" | grep -q "server=$C" && printf '%s' "$line" | grep -q 'crashed state'; then
    {
      echo "=== CRASH DETECTADO $(date -u) ==="
      echo "journal: $line"
      echo
      echo '=== ULTIMOS 10 MIN DE LOG DEL SERVER ==='
      docker logs "$C" --since 10m 2>&1 | tail -80
      echo
      echo '=== CARPETAS DE CRASH NUEVAS ==='
      ls -lat /var/lib/pterodactyl/volumes/$C/Pal/Saved/Crashes/ 2>/dev/null | head -4
    } > "$OUT"
    echo "capturado en $OUT"
    exit 0
  fi
done