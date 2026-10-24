#!/data/data/com.termux/files/usr/bin/bash
# Estado-Claudio — resumen visible (widget tasks)
pgrep -x sshd >/dev/null && S1="OK :8022" || S1="CAIDO"
pgrep -f 'serve[r]\.py' >/dev/null && S2="corriendo" || S2="detenido"
echo "sshd:    $S1"
echo "Claudio: $S2"
echo "log:     ~/boot-claudio.log"