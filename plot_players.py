#!/usr/bin/env python3
"""Genera un gráfico de jugadores únicos por día desde los reportes diarios."""
import re
import os
import glob
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

REPORTS_DIR = "/opt/scl-bot/reports"
OUTPUT_PATH = "/opt/scl-bot/reports/players_timeline.png"


def parse_reports():
    """Parsea todos los reportes y devuelve lista de dicts con date, servers, total_unique, total_sessions."""
    files = glob.glob(f"{REPORTS_DIR}/report_*.txt")
    data = []

    for f in sorted(files):
        fname = os.path.basename(f)
        m = re.match(r'report_(\d{4}-\d{2}-\d{2})', fname)
        if not m:
            continue
        date_str = m.group(1)
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        content = open(f, errors='replace').read()
        lines = content.split('\n')

        servers = {}
        current_server = None
        for line in lines:
            srv_match = re.match(r'\s*[^\w\s]+\s+(.+?)\s+—', line)
            if srv_match:
                current_server = srv_match.group(1).strip()
                if 'sin actividad' in line:
                    servers[current_server] = 0
                    current_server = None
                continue
            if current_server:
                uniq_m = re.search(r'(\d+)\s+únicos', line)
                if uniq_m:
                    servers[current_server] = int(uniq_m.group(1))
                    current_server = None

        total_m = re.search(r'📈\s+(\d+)\s+jugadores únicos\s+\|\s+(\d+)\s+sesiones', content)
        total_unique = int(total_m.group(1)) if total_m else 0
        total_sessions = int(total_m.group(2)) if total_m else 0

        data.append({
            'date': date,
            'date_str': date_str,
            'servers': servers,
            'total_unique': total_unique,
            'total_sessions': total_sessions,
        })

    # Deduplicate: keep last entry per date
    by_date = {}
    for d in data:
        by_date[d['date_str']] = d
    return sorted(by_date.values(), key=lambda x: x['date'])


def generate_graph(output_path=OUTPUT_PATH):
    """Genera el gráfico de jugadores únicos por día."""
    data = parse_reports()
    if not data:
        raise ValueError("No se encontraron reportes para graficar.")

    dates = [d['date'] for d in data]
    total = [d['total_unique'] for d in data]

    fig, ax = plt.subplots(figsize=(14, 6))

    ax.plot(dates, total, 'o-', color='#e74c3c', linewidth=2, markersize=5, label='Jugadores únicos')
    ax.fill_between(dates, 0, total, alpha=0.15, color='#e74c3c')

    # Annotate max
    max_val = max(total)
    max_idx = total.index(max_val)
    ax.annotate(f'Máx: {max_val}', xy=(dates[max_idx], max_val), xytext=(15, 15),
                textcoords='offset points', fontsize=10, fontweight='bold',
                arrowprops=dict(arrowstyle='->', color='#333'), color='#333')

    # Average line
    avg = sum(total) / len(total)
    ax.axhline(y=avg, color='#3498db', linestyle='--', alpha=0.5, linewidth=1.5)
    ax.text(dates[-1], avg, f' Promedio: {avg:.0f}', color='#3498db', fontsize=10, va='bottom')

    ax.set_ylabel('Jugadores únicos', fontsize=12)
    ax.set_xlabel('Fecha', fontsize=12)
    start_str = data[0]['date_str']
    end_str = data[-1]['date_str']
    ax.set_title(f'SCL San Enrique — Jugadores únicos por día ({start_str} → {end_str})',
                 fontsize=14, fontweight='bold')
    ax.grid(axis='y', alpha=0.3)
    ax.set_ylim(bottom=0)
    ax.legend(loc='upper left', framealpha=0.9)

    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d-%b'))
    ax.xaxis.set_minor_locator(mdates.DayLocator())
    plt.xticks(rotation=45)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    return output_path, len(data), avg, max_val


if __name__ == '__main__':
    path, n, avg, mx = generate_graph()
    print(f"Saved to {path} ({n} días, promedio={avg:.0f}, máx={mx})")