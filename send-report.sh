#!/bin/bash
# send-report.sh — Envía el último reporte de servidores al grupo Lol de WhatsApp
# Uso: /opt/scl-bot/send-report.sh [fecha YYYY-MM-DD]
# Si no se especifica fecha, envía el reporte más reciente.

REPORTS_DIR="/opt/scl-bot/reports"
GROUP_JID="${SCL_WA_GROUP_JID:-}"
[ -n "$GROUP_JID" ] || { echo "ERROR: SCL_WA_GROUP_JID no definido (ver /root/.scl-bot.env)"; exit 1; }
BRIDGE_URL="http://localhost:3000/send"

# Determinar qué reporte enviar
if [ -n "$1" ]; then
    REPORT_FILE="$REPORTS_DIR/report_$1.txt"
else
    REPORT_FILE=$(ls -t "$REPORTS_DIR"/report_*.txt 2>/dev/null | head -1)
fi

if [ -z "$REPORT_FILE" ] || [ ! -f "$REPORT_FILE" ]; then
    echo "Error: No se encontró el reporte." >&2
    exit 1
fi

# Leer el contenido del reporte
MESSAGE=$(cat "$REPORT_FILE")

# Enviar al grupo Lol via bridge
RESPONSE=$(curl -s -X POST "$BRIDGE_URL" \
    -H "Content-Type: application/json" \
    -d "$(python3 -c "import json,sys; print(json.dumps({'chatId': '$GROUP_JID', 'message': sys.stdin.read()}))" <<< "$MESSAGE")")

# Verificar respuesta
if echo "$RESPONSE" | grep -q '"success"'; then
    echo "✓ Reporte enviado al grupo Lol: $(basename "$REPORT_FILE")"
    echo "  Archivo: $REPORT_FILE"
else
    echo "✗ Error al enviar: $RESPONSE" >&2
    exit 1
fi