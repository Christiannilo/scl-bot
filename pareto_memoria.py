#!/usr/bin/env python3
"""
Genera un gráfico Pareto de consumo de memoria RAM por servidor.
Lee los contenedores de Docker, mapea UUIDs a nombres vía MySQL,
y produce un PNG con barras + línea acumulada.

Uso:
    python3 /opt/scl-bot/pareto_memoria.py [--output PATH]

Si no se pasa --output, guarda en /opt/scl-bot/reports/pareto_memoria.png
"""

import subprocess
import os
import sys
import json
import tempfile
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# --- Mapeo de UUID -> nombre legible ---
# En vez de depender de MySQL desde el script, usamos el mapeo del bot
# que ya conoce los servidores por puerto. Pero aquí necesitamos los UUIDs
# de los contenedores Docker. Los obtenemos dinámicamente.

# Mapeo estático de UUID -> nombre (se actualiza solo si cambia)
# Se pobla dinámicamente desde MySQL abajo.
def get_server_names():
    """Obtiene UUID -> nombre desde la BD de Pterodactyl."""
    uuid_map = {}
    try:
        result = subprocess.run(
            ["mysql", "-u", "root", "panel", "-N", "-B", "-e",
             "SELECT s.uuid, CONCAT(s.name, ' (', se.name, ')') FROM servers s JOIN eggs se ON s.egg_id = se.id ORDER BY s.id;"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if "\t" in line:
                uid, name = line.split("\t", 1)
                uuid_map[uid] = name
    except Exception:
        pass
    return uuid_map


def get_container_memory():
    """Obtiene el uso de memoria por contenedor desde docker stats."""
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format",
         "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"],
        capture_output=True, text=True, timeout=10
    )
    containers = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        mem_str = parts[1].strip()
        # Parse "1.288GiB / 4.4GiB" or "898.1MiB / 4.4GiB"
        mem_used_str = mem_str.split("/")[0].strip()
        mb = parse_mem_to_mb(mem_used_str)
        if mb is not None:
            containers.append((name, mb))
    return containers


def parse_mem_to_mb(mem_str):
    """Convierte '1.288GiB' o '898.1MiB' o '5.223MiB' a MB (float)."""
    try:
        mem_str = mem_str.strip()
        if mem_str.endswith("GiB"):
            return float(mem_str[:-3]) * 1024
        elif mem_str.endswith("MiB"):
            return float(mem_str[:-3])
        elif mem_str.endswith("KiB"):
            return float(mem_str[:-3]) / 1024
        elif mem_str.endswith("GB"):
            return float(mem_str[:-2]) * 1024
        elif mem_str.endswith("MB"):
            return float(mem_str[:-2])
        else:
            return None
    except (ValueError, IndexError):
        return None


def get_disk_usage(uuid_map):
    """Obtiene uso de disco por servidor desde los volúmenes de Pterodactyl."""
    volumes_dir = "/var/lib/pterodactyl/volumes"
    disk_data = []
    if not os.path.isdir(volumes_dir):
        return disk_data
    for dirname in os.listdir(volumes_dir):
        path = os.path.join(volumes_dir, dirname)
        if not os.path.isdir(path):
            continue
        # Saltar directorios que no son servidores (.sftp, etc.)
        if dirname.startswith("."):
            continue
        try:
            result = subprocess.run(
                ["du", "-sh", path],
                capture_output=True, text=True, timeout=10
            )
            size_str = result.stdout.split("\t")[0].strip() if result.stdout else ""
            gb = parse_size_to_gb(size_str)
            if gb is not None:
                # Retornar (uuid, gb) para que el llamador pueda filtrar
                disk_data.append((dirname, gb))
        except Exception:
            pass
    return disk_data


def parse_size_to_gb(size_str):
    """Convierte '31G', '5.4G', '458M', '219M' a GB (float)."""
    try:
        size_str = size_str.strip()
        if size_str.endswith("G") or size_str.endswith("G"):
            return float(size_str[:-1].replace(",", "."))
        elif size_str.endswith("M"):
            return float(size_str[:-1].replace(",", ".")) / 1024
        elif size_str.endswith("K"):
            return float(size_str[:-1].replace(",", ".")) / (1024 * 1024)
        else:
            return None
    except (ValueError, IndexError):
        return None


def filter_game_servers(containers, uuid_map):
    """Filtra solo contenedores que corresponden a servidores de juego conocidos."""
    filtered = []
    for name, mb in containers:
        # El nombre del contenedor es el UUID del servidor
        if name in uuid_map:
            server_name = uuid_map[name]
            # Limpiar el nombre: "SCL_San_Enrique (Killing Floor 2)" -> "KF2"
            short = simplify_name(server_name)
            filtered.append((short, mb))
        # Saltar panel, wings, etc.
    return filtered


def simplify_name(full_name):
    """Simplifica el nombre del servidor para mostrarlo en el gráfico.
    full_name viene de la BD: CONCAT(s.name, ' (', se.name, ')')"""
    # El formato es "ServerName (EggName)"
    # Extraer el egg name entre paréntesis
    egg = ""
    server = full_name
    if "(" in full_name and ")" in full_name:
        egg = full_name.split("(")[1].split(")")[0].strip()
        server = full_name.split("(")[0].strip()

    # Mapeo por egg type
    egg_lower = egg.lower()
    server_lower = server.lower()

    if "palworld" in egg_lower:
        if "2" in server_lower or "palworld 2" in server_lower:
            return "Palworld 2"
        return "Palworld"
    if "killing floor" in egg_lower or "kf2" in egg_lower:
        # Distinguir instancias por nombre de servidor
        if "gamerpc" in server_lower or "mazapanchito cl kf2" in server_lower:
            return "KF2 (PC)"
        if " 2" in server_lower or server_lower.endswith("2"):
            return "KF2 2"
        return "KF2"
    if "mordhau" in egg_lower:
        return "Mordhau"
    if "enshrouded" in egg_lower:
        return "Enshrouded"
    if "quake" in egg_lower:
        return "Quake 2"
    if "valheim" in egg_lower:
        return "Valheim"
    if "paper" in egg_lower:
        return "PaperMC"
    # Fallback
    return server[:20]


def make_pareto(ax, data, ylabel, title, unit_suffix):
    """Dibuja un gráfico Pareto en el axis dado."""
    if not data:
        ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title, fontsize=14, fontweight="bold")
        return

    names = [d[0] for d in data]
    values = [d[1] for d in data]
    total = sum(values)
    cumulative = np.cumsum(values) / total * 100

    bar_colors = [
        "#2196F3", "#4CAF50", "#FF9800", "#E91E63",
        "#9C27B0", "#00BCD4", "#FF5722", "#795548",
        "#607D8B", "#8BC34A",
    ]

    # Barras
    bars = ax.bar(names, values, color=bar_colors[:len(names)],
                  edgecolor="white", linewidth=0.8, width=0.65)
    ax.set_ylabel(ylabel, fontsize=12, fontweight="bold")
    ax.set_xlabel("Servidor", fontsize=12, fontweight="bold")

    # Etiquetas de valor en las barras
    for bar, val in zip(bars, values):
        if val >= 1:
            label = f"{val:,.0f}{unit_suffix}"
        else:
            label = f"{val:.2f}{unit_suffix}"
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + total * 0.008,
                label, ha="center", va="bottom",
                fontsize=9, fontweight="bold")

    # Línea acumulada
    ax2 = ax.twinx()
    ax2.plot(names, cumulative, color="#D32F2F",
             marker="o", linewidth=2.5, markersize=7, zorder=5)
    ax2.set_ylabel("Acumulado (%)", fontsize=12, fontweight="bold", color="#D32F2F")
    ax2.set_ylim(0, 115)
    ax2.axhline(80, color="#FF6F00", linestyle="--", linewidth=1.5, alpha=0.8)
    ax2.text(len(names) - 0.5, 82, "80%",
             color="#FF6F00", fontsize=10, fontweight="bold")

    for i, pct in enumerate(cumulative):
        ax2.text(i, pct + 2.5, f"{pct:.0f}%",
                 ha="center", fontsize=8, color="#D32F2F", fontweight="bold")

    ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
    ax.set_ylim(0, max(values) * 1.18)
    ax.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax.tick_params(axis="x", rotation=25)
    ax.yaxis.grid(True, alpha=0.3)
    return ax2


def generate_pareto(output_path=None, include_disk=True):
    """Genera el gráfico Pareto y lo guarda como PNG. Retorna la ruta."""
    if output_path is None:
        output_path = "/opt/scl-bot/reports/pareto_memoria.png"

    uuid_map = get_server_names()
    containers = get_container_memory()

    # Filtrar solo servidores de juego
    ram_data = filter_game_servers(containers, uuid_map)
    # Ordenar de mayor a menor
    ram_data.sort(key=lambda x: x[1], reverse=True)

    if include_disk:
        disk_raw = get_disk_usage(uuid_map)
        # Filtrar solo volúmenes que corresponden a servidores conocidos
        disk_data = []
        for uid, gb in disk_raw:
            # Solo incluir volúmenes cuyo UUID está en la BD de Pterodactyl
            if uid in uuid_map:
                short = simplify_name(uuid_map[uid])
                disk_data.append((short, gb))
        disk_data.sort(key=lambda x: x[1], reverse=True)

    # Crear figura
    if include_disk and disk_data:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 13))
        make_pareto(ax1, ram_data, "RAM (MB)", "Pareto - Consumo de RAM por Servidor", " MB")
        make_pareto(ax2, disk_data, "Disco (GB)", "Pareto - Consumo de Disco por Servidor", " GB")
        fig.tight_layout(pad=3.0)
    else:
        fig, ax1 = plt.subplots(figsize=(12, 6.5))
        make_pareto(ax1, ram_data, "RAM (MB)", "Pareto - Consumo de RAM por Servidor", " MB")
        fig.tight_layout()

    # Marca de agua con timestamp
    fig.text(0.99, 0.01,
             f"Generado: {datetime.now().strftime('%d-%m-%Y %H:%M')}",
             ha="right", va="bottom", fontsize=8, color="gray", alpha=0.7)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close()

    # También retornar los datos para el resumen de texto
    return output_path, ram_data, disk_data if include_disk else []


def format_text_summary(ram_data, disk_data):
    """Genera un resumen de texto con los datos del Pareto."""
    lines = []
    total_ram = sum(v for _, v in ram_data) if ram_data else 0
    lines.append(f"📊 **Pareto de Memoria** — {datetime.now().strftime('%d-%m-%Y %H:%M')}")
    lines.append("")
    lines.append(f"**RAM total consumida: {total_ram:,.0f} MB (~{total_ram/1024:.1f} GB)**")
    lines.append("")

    if ram_data:
        cumul = 0
        for name, mb in ram_data:
            pct = mb / total_ram * 100 if total_ram else 0
            cumul += pct
            marker = " ← 80%" if abs(cumul - 80) < pct / 2 and cumul <= 85 else ""
            lines.append(f"• {name}: {mb:,.0f} MB ({pct:.0f}%) — acum: {cumul:.0f}%{marker}")

    if disk_data:
        total_disk = sum(v for _, v in disk_data)
        lines.append("")
        lines.append(f"**Disco total consumido: {total_disk:,.1f} GB**")
        cumul = 0
        for name, gb in disk_data:
            pct = gb / total_disk * 100 if total_disk else 0
            cumul += pct
            marker = " ← 80%" if abs(cumul - 80) < pct / 2 and cumul <= 85 else ""
            lines.append(f"• {name}: {gb:.1f} GB ({pct:.0f}%) — acum: {cumul:.0f}%{marker}")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pareto de memoria por servidor")
    parser.add_argument("--output", "-o", default=None, help="Ruta de salida del PNG")
    parser.add_argument("--ram-only", action="store_true", help="Solo RAM, sin disco")
    parser.add_argument("--text", action="store_true", help="Imprimir resumen de texto")
    args = parser.parse_args()

    path, ram, disk = generate_pareto(args.output, include_disk=not args.ram_only)
    print(f"Guardado: {path}")

    if args.text:
        print()
        print(format_text_summary(ram, disk))