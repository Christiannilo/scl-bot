# SCL San Enrique — Bots y automatización para servidores de juego

Monorepo de la operación de **SCL_San_Enrique**: servidores de juego sobre Pterodactyl
(KF2, Mordhau, Enshrouded, DayZ, Valheim, PaperMC, Palworld, Quake 2, Zomboid) con
bot de Telegram, monitoreo de DNS, resúmenes diarios y geo-filtrado nftables.

Todo el código corre en producción en un host Ubuntu 24.04 ("scl-01") con Pi-hole,
firewalld, nftables y Docker.

## Componentes

- **`bot.py`** — Bot de Telegram de la comunidad (scl-bot.service): estado de servidores,
  comandos de administración, anuncios.
- **`daily-summary.py`** — Resumen diario matinal: jugadores por servidor y país
  (GeoLite2), rotación de logs de KF2, chequeo de actualizaciones de los servidores.
  Corre vía cron a las 06:10.
- **`ip-monitor.py`** — Monitor de IP pública dinámica: detecta cambios, notifica y
  reinicia servidores vía la API de Wings (Pterodactyl).
- **`kf2-promo.py`** — Daemon de KF2: saluda jugadores y promociona el clan leyendo los
  logs del servidor vía WebAdmin (kf2-promo.service).
- **`vigia-dns/`** — Vigía DNS de la LAN: sondea cada 10 min el estado de pihole-FTL,
  la resolución vía Pi-hole (dominio random, fuerza upstream completo), el decisor del
  local-router, el forwarder del router y el gateway. Alerta por Telegram solo en
  transiciones confirmadas (2 ciclos consecutivos), con ventana de gracia para el
  reinicio de medianoche de FTL y envío robusto que no depende del DNS local.
- **`geo-filter/`** — Geo-filtrado nftables (tabla `geogame`): permite solo las regiones
  configuradas en los puertos de juego, con listas manuales allow/block.
- **`scripts/`** y utilidades — Watcher de crashes de Palworld, shipper de logs Docker,
  envío de reportes por WhatsApp, análisis Pareto de países/memoria, gráficos de jugadores.
- **`ps-fix/`** — Utilidades de diagnóstico y parche para el bot de PocketStrike
  (OpenCode Go rail). Las configs WireGuard con llaves privadas **no** están en el repo.

## Configuración (entorno)

Ningún secreto vive en el código: todo se carga desde un archivo de entorno
(`.scl-bot.env`, fuera del repo) con `set -a; . .scl-bot.env; set +a`, o desde
`EnvironmentFile=` de systemd.

| Variable | Uso |
|---|---|
| `SCL_BOT_TOKEN` | Token del bot de Telegram |
| `SCL_ALERT_CHAT` | Chat ID para notificaciones (ip-monitor, daily-summary, vigía DNS) |
| `SCL_ADMINS` | IDs de administradores del bot |
| `SCL_ALLOWED_CHATS` | Chats permitidos del bot |
| `KF2_WEBADMIN_USER` / `KF2_WEBADMIN_PASS` | WebAdmin de KF2 |
| `WINGS_TOKEN` | Token de la API de Wings (Pterodactyl) |

## Qué NO está en el repo y por qué

- `GeoLite2-City.mmdb` — licencia de MaxMind y tamaño; descárgala con tu license key.
- `reports/`, `player_data.json`, `docker-logs/`, IPs de runtime — datos de jugadores y de red.
- `om-pkg/` — copia vendored de OpenManus (terceros).
- `fw-audit-2026-10/` — artefactos de auditoría con detalles de infraestructura.
- `*.conf` de WireGuard — contienen llaves privadas.
- Respaldos (`*.bak*`), logs y estado runtime.

## Licencia

MIT — ver [LICENSE](LICENSE).
