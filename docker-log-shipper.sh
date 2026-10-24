#!/bin/bash
# ============================================================
# docker-log-shipper.sh — Captura persistente de docker logs
# ============================================================
# Problema que resuelve:
#   1. Los docker logs MUEREN cuando Wings recrea el contenedor
#      (updates, cambios de config). El 2026-09-10 el contenedor
#      de Valheim fue recreado a las 22:32 y se perdieron las
#      sesiones de esa tarde (los "4 players").
#   2. El driver 'local' (Wings) usa max-size=5m max-file=1:
#      cuando el log pasa 5MB, el INICIO se descarta.
#
# Solución: cada 5 min (cron) captura incremental con
# `docker logs --since <last_ts>` y appendea a un archivo
# persistente por contenedor que sobrevive recreaciones y
# truncados. Los parsers del reporte leen ese archivo
# (_fetch_docker_logs en daily-summary.py, con fallback a
# `docker logs` en vivo).
#
# Formato de salida = idéntico a `docker logs --timestamps`
# (prefijo RFC3339 UTC + contenido), para que los parsers no
# cambien su lógica.
# ============================================================

LOGDIR=/opt/scl-bot/docker-logs
STATEDIR=/opt/scl-bot/docker-logs/.state
mkdir -p "$LOGDIR" "$STATEDIR"

# Contenedores cuyo stdout usan los parsers del reporte diario
CONTAINERS=(
    "32e0bff9-e9bd-4258-bb0a-c3ab81eb93cd"   # Valheim
    "f8a39b9c-a4eb-40c9-a6ee-c225959b14a8"   # Palworld
    "42325324-4adc-4a6f-abc4-340f57a8b0e9"   # Palworld 2
)

for cid in "${CONTAINERS[@]}"; do
    state="$STATEDIR/$cid.last"
    out="$LOGDIR/$cid.log"

    # Contenedor debe existir (si fue recreado, docker logs parte de
    # cero pero nuestro archivo persistente ya conserva lo anterior)
    docker inspect "$cid" >/dev/null 2>&1 || continue

    since=$(cat "$state" 2>/dev/null)

    if [ -z "$since" ]; then
        # Primera captura: todo lo que docker aún conserve
        docker logs --timestamps "$cid" >> "$out" 2>/dev/null || true
    else
        tmp=$(mktemp)
        docker logs --timestamps --since "$since" "$cid" > "$tmp" 2>/dev/null || true

        # Dedupe de frontera: `--since` re-incluye líneas con timestamp
        # exactamente igual al del state; descartamos las idénticas a la
        # última línea ya shippeada.
        if [ -s "$out" ]; then
            lastline=$(tail -1 "$out")
            while [ -s "$tmp" ] && [ "$(head -1 "$tmp")" = "$lastline" ]; do
                sed -i '1d' "$tmp"
            done
        fi

        [ -s "$tmp" ] && cat "$tmp" >> "$out"
        rm -f "$tmp"
    fi

    # Actualizar state solo si capturamos algo (log vacío = contenedor
    # sin stdout aún; reintentar con full capture la próxima corrida)
    if [ -s "$out" ]; then
        tail -1 "$out" | awk '{print $1}' > "$state"
    fi
done

# Limpieza de AdminLogs antiguos de DayZ (Wings crea uno nuevo por boot;
# el parser recorre todos con glob, mantener los últimos 14 basta para
# la ventana de 24h del reporte)
ADM_DIR=/var/lib/pterodactyl/volumes/b9944410-1c32-49ef-8897-4411838f3cff/profiles
if [ -d "$ADM_DIR" ]; then
    ls -1t "$ADM_DIR"/DayZServer_*.ADM 2>/dev/null | tail -n +15 | xargs -r rm -f
fi

exit 0