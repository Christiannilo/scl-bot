#!/bin/bash
# ============================================================================
# geofilter-update.sh — Refresco diario (04:17 + jitter) de los blocklists:
#   1. build-geofilter.py: baja LACNIC + FireHOL level1, aplica guardas
#      (minimos absolutos, colision con rangos propios) y escribe
#      geogame.nft + /etc/firewalld/ipsets/firehol_level1.xml
#   2. nft -f geogame.nft: transaccion atomica destroy+create (nunca queda
#      estado parcial; si falla, la tabla vigente se mantiene intacta)
#   3. Diff runtime del ipset firewalld (sin reload — evita el riesgo
#      documentado de re-init parcial de Docker tras reload)
# Log: /opt/scl-bot/geo-filter/update.log
# ============================================================================
set -u
LOG=/opt/scl-bot/geo-filter/update.log
say(){ echo "[$(date '+%F %T')] $*" >> "$LOG"; }
cd /opt/scl-bot/geo-filter || exit 1

PRE_FH=$(firewall-cmd --ipset=firehol_level1 --get-entries 2>/dev/null | wc -l)
PRE_UDP=$(nft list chain inet geogame forward 2>/dev/null | grep -m1 'udp dport' | grep -oE 'packets [0-9]+' | cut -d' ' -f2)

if ! python3 build-geofilter.py >>"$LOG" 2>&1; then
  say "GUARD-FAIL en builder: se conserva TODO el estado vigente (nft + ipset)"
  exit 0
fi

# Snapshot de contadores del ciclo completo (ANTES del refresh que los resetea).
# Este archivo es la fuente de totales exactos del blocks-report diario.
mkdir -p stats
nft -j list table inet geogame > "stats/counters-$(date +%F).json" 2>/dev/null || true
cp "stats/counters-$(date +%F).json" stats/last.json 2>/dev/null || true
ls -t stats/counters-*.json 2>/dev/null | tail -n +31 | xargs -r rm -f
say "snapshot de contadores: stats/counters-$(date +%F).json"

if ! nft -f geogame.nft >>"$LOG" 2>&1; then
  say "ERROR: nft -f fallo — tabla vigente se mantiene (transaccion atomica, nada parcial)"
  exit 1
fi

NEW_FH=$(grep -c '<entry>' /etc/firewalld/ipsets/firehol_level1.xml)
if [ "$NEW_FH" -lt $((PRE_FH * 80 / 100)) ]; then
  say "SHRINK-GUARD fh1: nuevo=$NEW_FH < 80% de $PRE_FH — ipset runtime sin tocar (nft ya actualizado)"
  exit 0
fi

sed -n 's/.*<entry>\(.*\)<\/entry>.*/\1/p' /etc/firewalld/ipsets/firehol_level1.xml | sort > /tmp/fh_new.$$
firewall-cmd --ipset=firehol_level1 --get-entries | sort > /tmp/fh_old.$$
A=$(comm -13 /tmp/fh_old.$$ /tmp/fh_new.$$)
D=$(comm -23 /tmp/fh_old.$$ /tmp/fh_new.$$)
rm -f /tmp/fh_old.$$ /tmp/fh_new.$$
na=0; nd=0
for e in $A; do firewall-cmd --ipset=firehol_level1 --add-entry="$e" >/dev/null 2>&1 && na=$((na+1)); done
for e in $D; do firewall-cmd --ipset=firehol_level1 --remove-entry="$e" >/dev/null 2>&1 && nd=$((nd+1)); done

POST_FH=$(firewall-cmd --ipset=firehol_level1 --get-entries 2>/dev/null | wc -l)
say "OK: nft atomico aplicado | ipset fh1: pre=$PRE_FH post=$POST_FH (+$na -$nd)"
[ -n "${PRE_UDP:-}" ] && say "   (nota: contadores nft se reinician en cada refresh — pre-refresh udp drops=$PRE_UDP)"
exit 0