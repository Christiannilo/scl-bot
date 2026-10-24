#!/bin/bash
# ============================================================================
# geo-list.sh — Gestión de listas manuales (allow/block) del sistema de
# blocklists de scl-01. Cada cambio regenera la tabla nft (atómico) y
# publica copias en texto plano para otros sistemas (bóveda :8089).
#
# Uso:
#   geo-list.sh add  allow|block <IP|CIDR> ["motivo"]
#   geo-list.sh del  allow|block <IP|CIDR>
#   geo-list.sh list [allow|block]
#
# Formato de archivo (portable: ipset / nft / iptables / pf lo consumen):
#   203.0.113.7/32          # 2026-10-22 motivo del allow
#   198.51.100.0/24         # 2026-10-22 rango ofensivo
# ============================================================================
set -u
BASE=/opt/scl-bot/geo-filter
ALLOW=$BASE/lists/allowlist.txt
BLOCK=$BASE/lists/blocklist.txt
PUBLISH=/opt/downloads/blocklists
LOG=$BASE/lists/changelog.log

say(){ echo "[$(date '+%F %T')] $*" >> "$LOG"; }

file_for(){ case "$1" in allow) echo "$ALLOW";; block) echo "$BLOCK";; *) echo "";; esac; }

apply_and_publish(){
  cd "$BASE" || return 1
  if ! python3 build-geofilter.py >>"$LOG" 2>&1; then
    echo "ERROR: builder rechazó el cambio (revisa $LOG) — tabla vigente intacta"; return 1
  fi
  if ! nft -c -f geogame.nft >>"$LOG" 2>&1; then
    echo "ERROR: sintaxis nft inválida (revisa $LOG) — tabla vigente intacta"; return 1
  fi
  nft -f geogame.nft >>"$LOG" 2>&1 || { echo "ERROR: nft -f falló"; return 1; }
  mkdir -p "$PUBLISH"
  for f in allowlist blocklist; do
    src=$BASE/lists/$f.txt
    { echo "# Lista $f — blocklists scl-01 — formato: CIDR por línea, '# comentario' permitido."
      echo "# Fuente de verdad: /opt/scl-bot/geo-filter/lists/$f.txt — actualizada: $(date '+%F %T')"
      cat "$src"; } > "$PUBLISH/$f.txt"
  done
  return 0
}

CMD="${1:-}"; LIST="${2:-}"; ENTRY="${3:-}"; REASON="${4:-}"

case "$CMD" in
  add|del)
    F=$(file_for "$LIST")
    [ -z "$F" ] && { echo "Uso: geo-list.sh add|del allow|block <IP|CIDR> [motivo]"; exit 1; }
    # normalizar a CIDR
    case "$ENTRY" in
      */*) C="$ENTRY";;
      *:*) echo "IPv6 no soportado (host sin IPv6 por diseño)"; exit 1;;
      *) C="$ENTRY/32";;
    esac
    if [ "$CMD" = add ]; then
      grep -qE "^${C}([[:space:]]|#|$)" "$F" && { echo "Ya estaba: $C en $LIST"; exit 0; }
      echo "$C # $(date '+%F %H:%M') ${REASON:-sin motivo}" >> "$F"
      say "ADD $LIST $C (${REASON:-sin motivo})"
    else
      grep -vE "^${C}([[:space:]]|#|$)" "$F" > "$F.tmp" && mv "$F.tmp" "$F"
      say "DEL $LIST $C"
    fi
    if apply_and_publish; then
      echo "OK: $LIST $C — tabla recargada y publicada en $PUBLISH"
    else
      exit 1
    fi
    ;;
  list)
    for f in "${LIST:-allow}" "${LIST:-block}"; do
      case "$f" in allow|block) echo "── $f ($(grep -cvE '^\s*#|^\s*$' $BASE/lists/$f.txt 2>/dev/null || echo 0) entradas)"; grep -vE '^\s*#|^\s*$' "$BASE/lists/$f.txt" 2>/dev/null || echo "  (vacía)";; esac
    done
    ;;
  *)
    echo "Uso: geo-list.sh add|del|list allow|block <IP|CIDR> [motivo]"; exit 1;;
esac