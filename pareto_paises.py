#!/usr/bin/env python3
"""
Genera un gráfico Pareto de jugadores únicos por país.
Lee los reportes diarios de /opt/scl-bot/reports/report_*.txt, extrae la
línea de flags del total (la línea no vacía siguiente a "📈 N jugadores
únicos") y acumula IPs únicas por país (jugadores-día).

Formatos de flags soportados:
  - Agrupado:   🇧🇷×19 🇺🇸×9 ... (post-fix format_flags)
  - Individual: 🇨🇱🇨🇱🇵🇪        (formato antiguo, cada flag = 1 IP)

Deduplicación: si hay varios reportes de la misma fecha (p.ej.
report_2026-07-08_1859.txt), se conserva el último (mismo criterio que
plot_players.py).

Ventana móvil: por defecto sólo agrega los últimos 30 días (desde hoy
hacia atrás, por fecha de reporte). Con --days 0 o --days all usa todo
el histórico.

Uso:
    /opt/scl-bot/venv/bin/python3 /opt/scl-bot/pareto_paises.py [--text] [--days N] [--output PATH] [--top N]
"""

import os
import re
import sys
import glob
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPORTS_DIR = "/opt/scl-bot/reports"
DEFAULT_OUTPUT = os.path.join(REPORTS_DIR, "pareto_paises.png")

# Token: par de indicadores regionales + ×N opcional (× = U+00D7)
FLAG_TOKEN = re.compile(r'([\U0001F1E6-\U0001F1FF]{2})(?:\u00d7(\d+))?')

COUNTRY_NAMES = {
    "AR": "Argentina", "AU": "Australia", "AT": "Austria", "BE": "Bélgica",
    "AM": "Armenia", "BO": "Bolivia", "BR": "Brasil", "BY": "Bielorrusia",
    "CA": "Canadá", "CH": "Suiza", "CL": "Chile", "CN": "China",
    "CO": "Colombia", "CR": "Costa Rica", "CZ": "Chequia", "DE": "Alemania",
    "DK": "Dinamarca", "EC": "Ecuador", "EG": "Egipto", "ES": "España",
    "FI": "Finlandia", "FK": "Islas Malvinas", "FR": "Francia",
    "GB": "Reino Unido", "GE": "Georgia", "GT": "Guatemala", "HK": "Hong Kong",
    "HU": "Hungría", "ID": "Indonesia", "IE": "Irlanda", "IL": "Israel",
    "IN": "India", "IQ": "Irak", "IR": "Irán", "IT": "Italia", "JM": "Jamaica",
    "JP": "Japón", "KR": "Corea del Sur", "LB": "Líbano", "MA": "Marruecos",
    "MX": "México", "MY": "Malasia",
    "NL": "Países Bajos", "NO": "Noruega", "NZ": "Nueva Zelanda",
    "PA": "Panamá", "PE": "Perú", "PH": "Filipinas", "PL": "Polonia",
    "PT": "Portugal", "PR": "Puerto Rico", "PY": "Paraguay", "RO": "Rumania",
    "RU": "Rusia", "SA": "Arabia Saudita", "SE": "Suecia", "SV": "El Salvador",
    "TH": "Tailandia", "TR": "Turquía", "TW": "Taiwán", "UA": "Ucrania", "US": "Estados Unidos",
    "UY": "Uruguay", "VE": "Venezuela", "VN": "Vietnam", "ZA": "Sudáfrica",
}


def flag_to_iso(pair):
    """🇨🇱 -> 'CL' (par de indicadores regionales -> ISO2)."""
    return ''.join(chr(ord(c) - 0x1F1E6 + 0x41) for c in pair)


def parse_flag_line(line):
    """Retorna {ISO: count} si la línea es 100% tokens de flags; si no, None."""
    if not line.strip():
        return None
    # Quitar todos los tokens; si queda algo, no es una línea de flags
    remainder = FLAG_TOKEN.sub('', line).strip()
    counts = {}
    for m in FLAG_TOKEN.finditer(line):
        iso = flag_to_iso(m.group(1))
        n = int(m.group(2)) if m.group(2) else 1
        counts[iso] = counts.get(iso, 0) + n
    if not counts or remainder:
        return None
    return counts


def collect_reports(reports_dir=REPORTS_DIR, days=30):
    """Devuelve {fecha: path} con deduplicación por fecha (último gana).

    Si days > 0, sólo incluye reportes de los últimos `days` días
    (fecha de hoy hacia atrás por fecha del reporte).
    """
    cutoff = None
    if days and days > 0:
        cutoff = datetime.now() - timedelta(days=days)
        cutoff = cutoff.strftime("%Y-%m-%d")
    by_date = {}
    for f in glob.glob(os.path.join(reports_dir, "report_*.txt")):
        base = os.path.basename(f)
        m = re.search(r'report_(\d{4}-\d{2}-\d{2})(?:_(\d{4}))?\.txt$', base)
        if not m:
            continue
        key = m.group(1)
        if cutoff and key < cutoff:
            continue
        if key not in by_date or f > by_date[key]:
            by_date[key] = f
    return by_date


def parse_report(path):
    """Extrae (uniques_totales, {ISO: count}) de un reporte."""
    with open(path, errors="replace") as fh:
        lines = fh.read().splitlines()
    uniques = None
    flags = None
    for i, line in enumerate(lines):
        if line.startswith("📈"):
            m = re.search(r'📈\s+(\d+)\s+jugadores únicos', line)
            if m:
                uniques = int(m.group(1))
            # La línea de flags es la siguiente no vacía
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    flags = parse_flag_line(lines[j])
                    break
            break
    return uniques, flags


def aggregate(by_date):
    """Acumula por país. Retorna (totals, days_with_data, uniques_sum, geo_missing)."""
    totals = {}
    days_with_data = 0
    uniques_sum = 0
    geo_missing = 0
    for date in sorted(by_date):
        uniques, flags = parse_report(by_date[date])
        if not flags:
            continue
        days_with_data += 1
        for iso, n in flags.items():
            totals[iso] = totals.get(iso, 0) + n
        if uniques is not None:
            uniques_sum += uniques
            geo_missing += uniques - sum(flags.values())
    return totals, days_with_data, uniques_sum, geo_missing


def make_chart(totals, days, dates, output_path, top_n=15, days_label=None):
    """Dibuja el Pareto: top-N barras + 'Otros', línea acumulada, línea 80%."""
    sorted_items = sorted(totals.items(), key=lambda x: -x[1])
    top = sorted_items[:top_n]
    rest = sorted_items[top_n:]

    labels = [COUNTRY_NAMES.get(iso, iso) for iso, _ in top]
    values = [v for _, v in top]
    if rest:
        labels.append(f"Otros ({len(rest)})")
        values.append(sum(v for _, v in rest))

    total = sum(values)
    cumulative = np.cumsum(values) / total * 100

    bar_colors = [
        "#2196F3", "#4CAF50", "#FF9800", "#E91E63",
        "#9C27B0", "#00BCD4", "#FF5722", "#795548",
        "#607D8B", "#8BC34A", "#3F51B5", "#CDDC39",
        "#7C4DFF", "#FF5252", "#00BFA5", "#9E9E9E",
    ]

    fig, ax = plt.subplots(figsize=(13, 7))
    bars = ax.bar(labels, values,
                  color=bar_colors[:len(labels)] or "#2196F3",
                  edgecolor="white", linewidth=0.8, width=0.65)
    ax.set_ylabel("IPs únicas acumuladas (jugadores-día)",
                  fontsize=12, fontweight="bold")
    ax.set_xlabel("País", fontsize=12, fontweight="bold")

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + total * 0.008,
                f"{val:,}".replace(",", "."),
                ha="center", va="bottom", fontsize=9, fontweight="bold")

    ax2 = ax.twinx()
    ax2.plot(labels, cumulative, color="#D32F2F",
             marker="o", linewidth=2.5, markersize=7, zorder=5)
    ax2.set_ylabel("Acumulado (%)", fontsize=12, fontweight="bold",
                   color="#D32F2F")
    ax2.set_ylim(0, 115)
    ax2.axhline(80, color="#FF6F00", linestyle="--", linewidth=1.5, alpha=0.8)
    ax2.text(len(labels) - 0.5, 82, "80%", color="#FF6F00",
             fontsize=10, fontweight="bold")

    for i, pct in enumerate(cumulative):
        ax2.text(i, pct + 2.5, f"{pct:.0f}%", ha="center", fontsize=8,
                 color="#D32F2F", fontweight="bold")

    rango = f"{dates[0]} → {dates[-1]}" if dates else ""
    ventana = "últimos 30 días" if days_label == 30 else (
        "histórico completo" if not days_label else f"últimos {days_label} días")
    ax.set_title("Pareto — Jugadores por país · SCL San Enrique",
                 fontsize=14, fontweight="bold", pad=24)
    fig.text(0.5, 0.935,
             f"IPs únicas por día acumuladas · {ventana} · {days} reportes con actividad · {rango}",
             ha="center", fontsize=10, color="#555555")

    ax.set_ylim(0, max(values) * 1.18)
    ax.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax.tick_params(axis="x", rotation=30)
    ax.yaxis.grid(True, alpha=0.3)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def text_summary(totals, days, uniques_sum, geo_missing, dates, days_label=None):
    """Resumen en texto: tabla top con % y acumulado."""
    sorted_items = sorted(totals.items(), key=lambda x: -x[1])
    total = sum(v for _, v in sorted_items)
    ventana = "últimos 30 días" if days_label == 30 else (
        "histórico completo" if not days_label else f"últimos {days_label} días")
    lines = [f"Total IPs únicas acumuladas (jugadores-día): {total:,}"]
    lines.append(f"Reportes con actividad: {days} · {ventana} · "
                 f"Período: {dates[0]} → {dates[-1]}")
    if geo_missing > 0:
        lines.append(f"IPs sin geolocalizar (VPN/privadas): {geo_missing:,} "
                     f"({geo_missing / uniques_sum * 100:.1f}% de {uniques_sum:,})")
    lines.append("")
    cum = 0
    for iso, v in sorted_items:
        pct = v / total * 100
        cum += v
        cpct = cum / total * 100
        name = COUNTRY_NAMES.get(iso, iso)
        lines.append(f"{iso} {name:<18} {v:>6,}  {pct:5.1f}%  acum {cpct:5.1f}%")
    # ¿Cuántos países hacen el 80%?
    c = 0
    acc = 0
    for iso, v in sorted_items:
        acc += v
        c += 1
        if acc / total >= 0.80:
            break
    lines.append("")
    lines.append(f"→ {c} países concentran el 80% de las IPs únicas "
                 f"(de {len(sorted_items)} países totales)")
    return "\n".join(lines)


def generate_paises(output_path, days=30, top_n=15):
    """API para el bot: genera el Pareto de países con ventana móvil.

    Retorna (output_path, stats_dict) donde stats_dict incluye:
    total, days_with_data, uniques_sum, geo_missing, rango, ventana.
    """
    by_date = collect_reports(days=days)
    totals, n_days, uniques_sum, geo_missing = aggregate(by_date)
    if not totals:
        raise RuntimeError("No se encontraron datos de flags en los reportes.")
    dates = sorted(by_date.keys())
    days_label = days if days else None
    make_chart(totals, n_days, dates, output_path, top_n=top_n,
               days_label=days_label)
    stats = {
        "total": sum(totals.values()),
        "days": n_days,
        "uniques_sum": uniques_sum,
        "geo_missing": geo_missing,
        "rango": f"{dates[0]} → {dates[-1]}" if dates else "",
        "ventana": days,
        "n_countries": len(totals),
        "text": text_summary(totals, n_days, uniques_sum, geo_missing,
                             dates, days_label=days_label),
    }
    return output_path, stats


def main():
    args = sys.argv[1:]
    show_text = "--text" in args
    top_n = 15
    if "--top" in args:
        top_n = int(args[args.index("--top") + 1])
    days = 30
    if "--days" in args:
        raw = args[args.index("--days") + 1]
        days = 0 if raw.lower() == "all" else int(raw)
    output = DEFAULT_OUTPUT
    if "--output" in args:
        output = args[args.index("--output") + 1]

    by_date = collect_reports(days=days)
    totals, n_days, uniques_sum, geo_missing = aggregate(by_date)
    if not totals:
        print("No se encontraron datos de flags en los reportes.")
        sys.exit(1)

    dates = sorted(by_date.keys())
    days_label = days if days else None
    make_chart(totals, n_days, dates, output, top_n=top_n,
               days_label=days_label)
    print(f"OK -> {output}")
    if show_text:
        print(text_summary(totals, n_days, uniques_sum, geo_missing, dates,
                           days_label=days_label))


if __name__ == "__main__":
    main()