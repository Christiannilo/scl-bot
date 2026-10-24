#!/usr/bin/env python3
"""
build-geofilter.py — Genera la tabla nft 'inet geogame' (geofiltro SA en puertos de juego
+ FireHOL level1 para todo lo entrante WAN) y el ipset XML de firewalld para level1.
Uso diario via geofilter-update.service.

Fuentes (verificadas 22-oct-26; ET añadida 23-oct-26):
  - LACNIC delegated (oficial, diario): ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-extended-latest
  - FireHOL level1 (diario): raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset
  - ET Open blocklist (diario): rules.emergingthreats.net/fwrules/emerging-Block-IPs.txt (Spamhaus/DShield/abuse.ch)

Precedencia por orden de reglas: established/related > rangos internos > WG 51820 > manual_block >
fh1 drop > et4 drop > geo juegos > meter flood UDP (sesiones NEW >10/s por IP).
Guardas: sa4 >= 14000 rangos, fh1 >= 500, et4 >= 400; colisiones con rangos propios se ELIMINAN de fh1 y et4.
Exit codes: 0 OK | 2 guard-fail (NO sobreescribe nada)
"""
import ipaddress, os, re, sys, urllib.request

SA_COUNTRIES = {"AR", "BO", "BR", "CL", "CO", "EC", "PE", "PY", "UY", "VE", "GY", "SR"}
LACNIC_URL = "https://ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-extended-latest"
FIREHOL_URL = "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset"
ET_URL = "https://rules.emergingthreats.net/fwrules/emerging-Block-IPs.txt"

# Puertos de juego dentro del geofiltro (tcp+udp). Fuera: query KF2 27015-27017 (trackers),
# Jellyfin 8096, web 8082, 1985, LocalSend 53317, filebrowser 8089, WG 51820, Transmission 51413.
GAME_PORTS = sorted({15001, 2302, 2305, 2456, 2457, 7787, 7777, 7778, 7779,
                     8211, 8212, 8213, 8214, 15636, 15637, 16261, 16262,
                     25565, 25566, 25575, 25576, 27910, 27911, 27920})

MIN_SA, MIN_FH1, MIN_ET = 14000, 500, 400
OWN_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "200.74.86.0/24", "100.64.0.0/10")]

OUT_DIR = "/opt/scl-bot/geo-filter"
NFT_FILE = os.path.join(OUT_DIR, "geogame.nft")
XML_FILE = "/etc/firewalld/ipsets/firehol_level1.xml"
LISTS_DIR = os.path.join(OUT_DIR, "lists")
ALLOW_FILE = os.path.join(LISTS_DIR, "allowlist.txt")
BLOCK_FILE = os.path.join(LISTS_DIR, "blocklist.txt")
PUBLISH_DIR = "/opt/downloads/blocklists"


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def build_sa4():
    nets = []
    for line in fetch(LACNIC_URL).splitlines():
        parts = line.split("|")
        if (len(parts) >= 7 and parts[1] in SA_COUNTRIES and parts[2] == "ipv4"
                and parts[6].split("*")[0].strip() in ("allocated", "assigned")):
            start, count = parts[3], int(parts[4])
            if count <= 0 or (count & (count - 1)) != 0:
                continue  # RIR garantiza potencias de 2; saltar anomalías
            nets.append(f"{start}/{32 - count.bit_length() + 1}")
    return sorted(set(nets), key=ipaddress.ip_network)


def build_fh1():
    kept, dropped_own, skipped_v6 = [], 0, 0
    for line in fetch(FIREHOL_URL).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            skipped_v6 += 1
            continue
        try:
            net = ipaddress.ip_network(line, strict=False)
        except ValueError:
            continue
        if any(net.overlaps(own) for own in OWN_NETS):
            dropped_own += 1
            continue
        kept.append(net)
    return sorted(set(kept)), dropped_own, skipped_v6


def build_et():
    """ET Open blocklist (Spamhaus DROP + DShield + abuse.ch). El feed trae reglas iptables;
    extraemos las IPs/CIDRs con regex de token. Devuelve (nets, colisiones propias)."""
    kept, dropped_own = [], 0
    for line in fetch(ET_URL).splitlines():
        if line.lstrip().startswith("#"):
            continue
        for tok in re.findall(r"\b\d+(?:\.\d+){3}(?:/\d+)?\b", line):
            try:
                net = ipaddress.ip_network(tok, strict=False)
            except ValueError:
                continue
            if any(net.overlaps(own) for own in OWN_NETS):
                dropped_own += 1
                continue
            kept.append(net)
    return sorted(set(kept)), dropped_own


def parse_manual_list(path):
    """Lee una lista manual (CIDR/IP por línea, '# comentario' permitido). Devuelve nets validadas."""
    nets = []
    if not os.path.exists(path):
        return nets
    with open(path) as f:
        for line in f:
            entry = line.split("#", 1)[0].strip()
            if not entry:
                continue
            try:
                nets.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                print(f"WARN: entrada invalida en {os.path.basename(path)}: {entry}", file=sys.stderr)
    return sorted(set(nets))


def fmt_elements(nets, per_line=8):
    out = []
    for i in range(0, len(nets), per_line):
        line = ", ".join(str(n) for n in nets[i:i + per_line])
        out.append(line + ("," if i + per_line < len(nets) else ""))
    return ("\n    " + "\n    ".join(out))


def main():
    os.makedirs("/etc/firewalld/ipsets", exist_ok=True)
    sa4 = build_sa4()
    if len(sa4) < MIN_SA:
        print(f"GUARD-FAIL: sa4={len(sa4)} < {MIN_SA} — no se escribe nada")
        return 2
    fh1, dropped_own, skipped_v6 = build_fh1()
    if len(fh1) < MIN_FH1:
        print(f"GUARD-FAIL: fh1={len(fh1)} < {MIN_FH1} — no se escribe nada")
        return 2
    et4, et_own = build_et()
    if len(et4) < MIN_ET:
        print(f"GUARD-FAIL: et4={len(et4)} < {MIN_ET} — no se escribe nada")
        return 2

    ports = ", ".join(str(p) for p in GAME_PORTS)
    internal_in = "10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8"
    internal_fw = "10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16"

    m_allow = parse_manual_list(ALLOW_FILE)
    m_block = parse_manual_list(BLOCK_FILE)

    def _set_block(name, nets, comment):
        s = f"  # {comment}\n  set {name} {{\n    type ipv4_addr\n    flags interval\n"
        if nets:
            s += f"    elements = {{ {fmt_elements(nets)}\n    }}\n"
        s += "  }\n"
        return s

    sets_manual = (
        _set_block("manual_allow", m_allow, f"manual_allow: {len(m_allow)} entradas (lists/allowlist.txt)")
        + _set_block("manual_block", m_block, f"manual_block: {len(m_block)} entradas (lists/blocklist.txt)")
    )

    nft = f"""#!/usr/sbin/nft -f
# Generado por build-geofilter.py — geofiltro SA (puertos de juego) + FireHOL level1 (todo lo entrante WAN)
# Aplicar: nft -f {NFT_FILE}  (transaccion atomica: destroy+create)
destroy table inet geogame
table inet geogame {{
  # sa4: {len(sa4)} rangos SA (LACNIC: AR BO BR CL CO EC PE PY UY VE GY SR)
  set sa4 {{
    type ipv4_addr
    flags interval
    auto-merge
    elements = {{ {fmt_elements(sa4)}
    }}
  }}
  # fh1: {len(fh1)} rangos FireHOL level1 (colisiones propias removidas: {dropped_own})
  set fh1 {{
    type ipv4_addr
    flags interval
    auto-merge
    elements = {{ {fmt_elements(fh1)}
    }}
  }}
  # et4: {len(et4)} rangos ET Open (colisiones propias removidas: {et_own})
  set et4 {{
    type ipv4_addr
    flags interval
    auto-merge
    elements = {{ {fmt_elements(et4)}
    }}
  }}
{sets_manual}  chain input {{
    type filter hook input priority -10; policy accept;
    ct state established,related accept
    ip saddr @manual_allow accept
    ip saddr {{ {internal_in} }} accept
    udp dport 51820 accept
    ip saddr @manual_block counter limit rate 10/second burst 20 packets log prefix "MB-DROP " drop
    ip saddr @fh1 counter limit rate 10/second burst 20 packets log prefix "FH1-DROP " drop
    ip saddr @et4 counter limit rate 10/second burst 20 packets log prefix "ET-DROP " drop
    iif != "br-wan" accept
    ip saddr != @sa4 tcp dport {{ {ports} }} counter limit rate 10/second burst 20 packets log prefix "GEOT-DROP " drop
    ip saddr != @sa4 udp dport {{ {ports} }} counter limit rate 10/second burst 20 packets log prefix "GEOU-DROP " drop
    ct state new udp dport {{ {ports} }} meter flood_in {{ ip saddr limit rate over 10/second burst 20 packets }} counter limit rate 1/second log prefix "FLD-DROP " drop
  }}
  chain forward {{
    type filter hook forward priority -10; policy accept;
    ct state established,related accept
    ip saddr @manual_allow accept
    ip saddr {{ {internal_fw} }} accept
    udp dport 51820 accept
    ip saddr @manual_block counter limit rate 10/second burst 20 packets log prefix "MB-DROP " drop
    ip saddr @fh1 counter limit rate 10/second burst 20 packets log prefix "FH1-DROP " drop
    ip saddr @et4 counter limit rate 10/second burst 20 packets log prefix "ET-DROP " drop
    iif != "br-wan" accept
    ip saddr != @sa4 tcp dport {{ {ports} }} counter limit rate 10/second burst 20 packets log prefix "GEOT-DROP " drop
    ip saddr != @sa4 udp dport {{ {ports} }} counter limit rate 10/second burst 20 packets log prefix "GEOU-DROP " drop
    ct state new udp dport {{ {ports} }} meter flood_fw {{ ip saddr limit rate over 10/second burst 20 packets }} counter limit rate 1/second log prefix "FLD-DROP " drop
  }}
}}
"""
    tmp = NFT_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(nft)
    os.replace(tmp, NFT_FILE)

    xml = '<?xml version="1.0" encoding="utf-8"?>\n<ipset type="hash:net">\n'
    xml += "  <short>FireHOL level1</short>\n"
    xml += "  <description>Threat feed FireHOL level1 (Spamhaus DROP + DShield + bogons). Actualizacion diaria por geofilter-update.service; rangos propios excluidos.</description>\n"
    xml += '  <option name="family" value="inet"/>\n'
    for n in sorted(fh1):
        xml += f"  <entry>{n if '/' in str(n) else str(n) + '/32'}</entry>\n"
    xml += "</ipset>\n"
    tmp = XML_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(xml)
    os.replace(tmp, XML_FILE)

    print(f"OK: sa4={len(sa4)} rangos | fh1={len(fh1)} rangos | et4={len(et4)} rangos "
          f"(propios removidos: fh1 {dropped_own}/et {et_own}, v6 saltados: {skipped_v6})")
    print(f"puertos geo ({len(GAME_PORTS)}): {ports}")
    return 0


if __name__ == "__main__":
    sys.exit(main())