#!/usr/bin/env bash
# vigia-dns.sh — Vigía DNS de la LAN (scl-ubuntu-01) — v1.1 (2026-10-23)
# v1.1 (23-oct-26): sonda dig 3s→5s; confirmación de 2 ciclos consecutivos antes de alertar
#   una caída (elimina los pares OK→BROKEN→OK por upstream lento; caso Quad9 9.9.9.9
#   degradado — removido de pihole.toml esa fecha); ventana de gracia 00:00–00:05 por
#   reinicio de FTL de 'pihole flush' (cron medianoche, falso ftl=down); fallback de notify
#   ahora vía 149.112.112.112 (9.9.9.9 degradado: 705–2240ms y timeouts >3s).
# Vigila (cada 10 min vía crontab root):
#   1. pihole-FTL activo (systemd)
#   2. Resolución DNS vía Pi-hole (127.0.0.1:53) con dominio random — fuerza upstream completo
#   3. Decisor del local-router: >=3 decision_error en 60 min (pasivo, routes.jsonl)
#      (umbral 3 evita falsas alarmas por cold-load puntual del tev1 tras idle >2h)
#   4. (info) Forwarder DNS del ASUS (192.168.50.1) — alerta solo en transiciones
#   5. (info) Gateway .1 responde ping — alerta solo en transiciones
# Alerta SOLO en transiciones; primera corrida = baseline (anuncia instalación una vez).
# --heartbeat : envía resumen aunque todo esté OK (liveness diario).
# Envío robusto: si el DNS local está caído, api.telegram.org se resuelve vía 149.112.112.112
# (IP directa, sin depender de Pi-hole) y se usa curl --resolve.
set -uo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

DIR=/opt/scl-bot/vigia-dns
STATE_FILE=$DIR/state.json
ROUTES=/root/.hermes/plugin-data/local-router/routes.jsonl
PIHOLE=127.0.0.1
FWD=192.168.50.1
# Cargar entorno (SCL_BOT_TOKEN, SCL_ALERT_CHAT...) — única fuente de secretos
if [ -f /root/.scl-bot.env ]; then set -a; . /root/.scl-bot.env; set +a; fi
CHAT_ID="${VIGIA_CHAT_ID:-${SCL_ALERT_CHAT:-}}"

ts() { date '+%F %T'; }

# --- envío Telegram robusto a DNS caído ---
notify() {  # $1 = texto
  if [ -f /root/.scl-bot.env ]; then
    set -a; . /root/.scl-bot.env; set +a
  fi
  if [ -z "${SCL_BOT_TOKEN:-}" ]; then
    echo "[$(ts)] NOTIFY-FALLO: sin SCL_BOT_TOKEN"; return 1
  fi
  if [ -z "${CHAT_ID:-}" ]; then
    echo "[$(ts)] NOTIFY-FALLO: sin CHAT_ID (SCL_ALERT_CHAT)"; return 1
  fi
  local api="https://api.telegram.org/bot${SCL_BOT_TOKEN}/sendMessage" out ip
  out=$(curl -sf -m 12 -X POST "$api" --data-urlencode "chat_id=$CHAT_ID" \
        --data-urlencode "text=$1" 2>/dev/null || true)
  if echo "$out" | grep -q '"ok":true'; then echo "[$(ts)] notify OK"; return 0; fi
  # DNS local caído: resolver Telegram por IP pública y reintentar (curl --resolve)
  ip=$(dig +short @149.112.112.112 api.telegram.org A +time=3 +tries=1 2>/dev/null \
       | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | sort -R | sed -n '1p')
  if [ -n "$ip" ]; then
    out=$(curl -sf -m 12 --resolve "api.telegram.org:443:$ip" -X POST "$api" \
          --data-urlencode "chat_id=$CHAT_ID" --data-urlencode "text=$1" 2>/dev/null || true)
    if echo "$out" | grep -q '"ok":true'; then echo "[$(ts)] notify OK (vía --resolve $ip)"; return 0; fi
  fi
  echo "[$(ts)] NOTIFY-FALLO (no se pudo enviar): $1"
  return 1
}

dns_state() {  # $1 = servidor, $2 = dominio → imprime OK | BROKEN
  local st
  st=$(dig @"$1" "$2" +time=5 +tries=1 +noall +comments 2>/dev/null \
       | grep -oE 'status: [A-Z]+' | sed -n '1p' | cut -d' ' -f2)
  case "$st" in NOERROR|NXDOMAIN) echo OK ;; *) echo BROKEN ;; esac
}

# --- v1.1: ventana de gracia por reinicio de FTL a medianoche ---
# 'pihole flush once quiet' (/etc/cron.d/pihole, 00:00) reinicia FTL; sondear en 00:00–00:05
# produce falsos ftl=down/res=BROKEN por carrera de timing (visto 22-oct 00:00:04).
# VIGIA_GRACE_HHMM permite forzar la hora en pruebas.
HHMM="${VIGIA_GRACE_HHMM:-$(date +%H%M)}"
case "$HHMM" in
  000[0-5])
    echo "[$(ts)] ventana de mantenimiento FTL medianoche ($HHMM) — sin sondeo, estado intacto"
    exit 0
    ;;
esac

# --- checks ---
PROBE="vigia-$RANDOM.vigia-dns.cl"
FTL=$(systemctl is-active pihole-FTL 2>/dev/null || true); [ "$FTL" = "active" ] || FTL=down
RES=$(dns_state "$PIHOLE" "$PROBE")
DEC=$(/usr/bin/python3 - "$ROUTES" <<'PYEOF' 2>/dev/null || echo UNKNOWN
import json,sys,time,datetime
def ts(v):
    try: return float(v)
    except Exception:
        try: return datetime.datetime.fromisoformat(str(v).replace("Z","+00:00")).timestamp()
        except Exception: return 0.0
cut=time.time()-3600; n=0
try:
    for line in open(sys.argv[1]):
        if '"decision_error"' not in line: continue
        try: j=json.loads(line)
        except Exception: continue
        if j.get("event")=="decision_error" and ts(j.get("ts",0))>cut: n+=1
except FileNotFoundError:
    print("UNKNOWN"); raise SystemExit
print("DEAD" if n>=3 else "OK")
PYEOF
)
if [ "$DEC" = "UNKNOWN" ]; then
  DEC=$(jq -r '.decisor // "OK"' "$STATE_FILE" 2>/dev/null || echo OK)   # carry-forward
fi
FWD_ST=$(dns_state "$FWD" "fwd-$RANDOM.vigia-dns.cl")
if ping -c1 -W2 "$FWD" >/dev/null 2>&1; then PING=OK; else PING=BROKEN; fi

# --- estado y transiciones ---
prev=$(cat "$STATE_FILE" 2>/dev/null || echo '{}')
cur=$(jq -nc --arg ftl "$FTL" --arg res "$RES" --arg dec "$DEC" --arg fwd "$FWD_ST" --arg ping "$PING" \
      '{ftl:$ftl, res:$res, decisor:$dec, fwd:$fwd, ping:$ping, ts:now}')
echo "[$(ts)] estado: ftl=$FTL res=$RES decisor=$DEC fwd=$FWD_ST ping=$PING"

if [ -z "$(jq -r '.ftl // empty' <<<"$prev" 2>/dev/null)" ]; then
  # baseline: primera corrida (o state perdido) — anuncia y establece
  echo "$cur" > "$STATE_FILE"
  notify "🛰️ Vigía DNS instalado (scl-01, cada 10 min) — baseline:
• pihole-FTL: $FTL
• Resolución vía Pi-hole: $RES
• Decisor local-router: $DEC
• Forwarder ASUS .1: $FWD_ST (incidente conocido, en reparación)
• Gateway .1 (ping): $PING
Aviso solo en transiciones + heartbeat diario 08:10."
  exit 0
fi

alerts=""
pend_prev=$(jq -r '.pending // empty' <<<"$prev" 2>/dev/null)
# --- v1.1: confirmación de 2 ciclos — una caída alerta solo si persiste al ciclo siguiente ---
# pend_conf = campos con caída sin confirmar (1er ciclo caído, silencio)
# conf_down = campos confirmados caídos (alerta 🚨 ya enviada; sin repeticiones)
# Recuperación ✅ solo se anuncia si la caída llegó a confirmarse.
pend_arr=$(jq -r '.pend_conf // [] | join(",")' <<<"$prev" 2>/dev/null)
conf_arr=$(jq -r '.conf_down // [] | join(",")' <<<"$prev" 2>/dev/null)
healthy() { [ "$1" = "OK" ] || [ "$1" = "active" ]; }
in_list() { case ",$2," in *",$1,"*) return 0 ;; esac; return 1; }
fields=(ftl res decisor fwd ping)
names=("pihole-FTL" "Resolución DNS (.27)" "Decisor local-router" "Forwarder ASUS (.1)" "Gateway .1 (ping)")
new_pend=""; new_conf=""
for i in "${!fields[@]}"; do
  field=${fields[$i]}; name=${names[$i]}
  o=$(jq -r ".$field // \"?\"" <<<"$prev" 2>/dev/null)
  n=$(jq -r ".$field // \"?\"" <<<"$cur" 2>/dev/null)
  if healthy "$n"; then
    if [ "$o" != "?" ] && [ -n "$o" ] && ! healthy "$o" && in_list "$field" "$conf_arr"; then
      alerts="${alerts}✅ $name: $o → $n"$'\n'
    fi
    # caída transitoria (solo pend_conf) que se recupera: silencio
  elif in_list "$field" "$conf_arr"; then
    new_conf="${new_conf}${new_conf:+,}$field"  # sigue confirmado caído — persistir, sin alerta repetida
  elif in_list "$field" "$pend_arr"; then
    alerts="${alerts}🚨 $name: $o → $n (confirmado, 2º ciclo consecutivo)"$'\n'
    new_conf="${new_conf}${new_conf:+,}$field"
  else
    new_pend="${new_pend}${new_pend:+,}$field"  # primer ciclo caído — silencio
  fi
done
# enriquecer cur con las listas para el próximo ciclo (vacías se omiten)
cur=$(jq -c --arg p "$new_pend" --arg c "$new_conf" \
  '. + {pend_conf:(($p|split(","))|map(select(length>0))), conf_down:(($c|split(","))|map(select(length>0)))}' \
  <<<"$cur" 2>/dev/null || echo "$cur")

pend_hdr=""
if [ -n "$pend_prev" ]; then
  pend_hdr="📎 Alerta encolada (el corte de red impidió enviarla en su momento) — reenvío:
$pend_prev
— — —
"
fi

if [ -n "$alerts" ] || [ -n "$pend_prev" ]; then
  msg="$pend_hdr"
  if [ -n "$alerts" ]; then
    msg="${msg}⚠️ Vigía DNS — cambio de estado:
$alerts (scl-01, $(date '+%d-%b %H:%M'))"
  else
    msg="${msg}⚠️ Vigía DNS — reenvío de alerta encolada (sin cambios nuevos de estado)"
  fi
  if notify "$msg"; then
    echo "$cur" | jq -c 'del(.pending)' > "$STATE_FILE"
  else
    combined="$pend_prev"
    if [ -n "$alerts" ]; then
      [ -n "$combined" ] && combined="$combined
---
"
      combined="${combined}⚠️ Vigía DNS — cambio de estado:
$alerts (scl-01, $(date '+%d-%b %H:%M'))"
    fi
    combined=${combined:0:3000}
    echo "$cur" | jq -c --arg pend "$combined" '.pending = $pend' > "$STATE_FILE"
  fi
elif [ "${1:-}" = "--heartbeat" ]; then
  hb="🫀 Vigía DNS OK — FTL:$FTL · resolución:$RES · decisor:$DEC · fwd .1:$FWD_ST · ping:$PING"
  if [ -n "$pend_prev" ]; then
    hb="$hb

$pend_hdr"
  fi
  if notify "$hb"; then
    echo "$cur" | jq -c 'del(.pending)' > "$STATE_FILE"
  elif [ -n "$pend_prev" ]; then
    echo "$cur" | jq -c --arg pend "$pend_prev" '.pending = $pend' > "$STATE_FILE"
  else
    echo "$cur" | jq -c 'del(.pending)' > "$STATE_FILE"
  fi
else
  echo "$cur" | jq -c 'del(.pending)' > "$STATE_FILE"
fi
exit 0