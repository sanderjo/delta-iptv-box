#!/usr/bin/env python3
"""
delta_iptv_stb_status.py — vind IPTV set-top boxes op het netwerk en lees de status uit.

Werkwijze:
1. Delta-box uitlezen (standaard): ontdek de box via mDNS/Cast-discovery
   (_googlecast._tcp, herkenbaar aan model 'DFN-Amigo7N' of naam 'DELTA') en
   lees het Chromecast 'eureka_info'-endpoint uit op poort 8008/8443.
2. LAN-scan (--scan): scan het hele /24-subnet en classificeer set-top boxes
   op basis van MAC-OUI en poort-fingerprint:
     - Amino Aria 7x : Entone-OUI (00:03:E6) en/of dropbear-SSH op 10022
                       met HTTP-control op 56789/56790.
     - Delta/Cast    : open Chromecast-poort 8008 met leesbaar eureka_info.

Gebruik:
    python3 delta_iptv_stb_status.py                # Delta-box zoeken + uitlezen
    python3 delta_iptv_stb_status.py 192.168.1.172  # direct een bekend IP uitlezen
    python3 delta_iptv_stb_status.py --scan         # heel het LAN scannen naar STB's
    python3 delta_iptv_stb_status.py --scan --subnet 192.168.1
    python3 delta_iptv_stb_status.py --json         # ook JSON tonen
    python3 delta_iptv_stb_status.py --raw          # alleen kale JSON

Afhankelijkheid voor Delta-discovery: zeroconf (pip install zeroconf).
De LAN-scan (--scan) en het uitlezen van een bekend IP werken met puur stdlib.
"""

import argparse
import json
import re
import socket
import ssl
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

# Namen/modellen waaraan we de Delta-box herkennen bij discovery.
MODEL_MATCH = "DFN-Amigo7N"
NAME_MATCH = "DELTA"

# --- Herkenning van set-top boxes bij een LAN-scan -------------------------
#
# MAC-OUI's (eerste 3 octetten) van bekende STB-fabrikanten.
OUIS = {
    "00:03:e6": "Entone",                      # Entone -> Amino (Aria-lijn)
    "98:c9:be": "Shenzhen SDMC Technology",    # SDMC   -> Delta DFN-Amigo7N
}

# Poort-fingerprint van de Amino Aria (Entone-platform):
#   10022 = dropbear SSH (beheerkanaal), 56789/56790 = kale HTTP-control-servers.
AMINO_SIG_PORTS = [10022, 56789, 56790]
# Chromecast/Android-TV STB (o.a. Delta Smart-box) luistert hier:
CAST_PORTS = [8008, 8009]


def discover(timeout=8.0):
    """Zoek de Delta-box via mDNS. Geeft (ip, naam, model) of None."""
    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except ImportError:
        sys.stderr.write(
            "Module 'zeroconf' ontbreekt. Installeer met: pip install zeroconf\n"
            "Of geef het IP rechtstreeks mee: python3 delta_iptv_stb_status.py 192.168.1.172\n"
        )
        return None

    import time

    found = {}

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=3000)
            if not info:
                return
            props = {
                k.decode(errors="ignore"): (v.decode(errors="ignore") if v else "")
                for k, v in info.properties.items()
            }
            model = props.get("md", "")
            fname = props.get("fn", "")
            if MODEL_MATCH.lower() in model.lower() or NAME_MATCH.lower() in fname.lower():
                for addr in info.parsed_addresses():
                    if ":" not in addr:  # alleen IPv4
                        found.setdefault(addr, (fname or name, model))

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    ServiceBrowser(zc, "_googlecast._tcp.local.", Listener())
    try:
        deadline = time.time() + timeout
        while time.time() < deadline and not found:
            time.sleep(0.25)
    finally:
        zc.close()

    if not found:
        return None
    ip, (name, model) = next(iter(found.items()))
    return ip, name, model


def fetch_eureka(ip):
    """Lees eureka_info op 8008 (http) of 8443 (https). Geeft dict of None."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # 8443 (https) eerst: die geeft ook het 'detail'-blok (fabrikant/model).
    # 8008 (http) als fallback; die is breder open maar zonder detail.
    endpoints = [
        f"https://{ip}:8443/setup/eureka_info?options=detail",
        f"http://{ip}:8008/setup/eureka_info?options=detail",
    ]
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "delta-status/1.0"})
            kw = {"timeout": 5}
            if url.startswith("https"):
                kw["context"] = ctx
            with urllib.request.urlopen(req, **kw) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            continue
    return None


# --- LAN-scan naar set-top boxes -------------------------------------------

def local_prefix():
    """Bepaal het lokale /24-subnet, bv '192.168.1'. Geeft (eigen_ip, prefix)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip, ip.rsplit(".", 1)[0]


def tcp_open(ip, port, timeout=0.6):
    """True als TCP-poort open is. Een geslaagde connect vult meteen de ARP-tabel."""
    try:
        with socket.create_connection((ip, port), timeout):
            return True
    except Exception:
        return False


def grab_banner(ip, port, timeout=1.5, nbytes=80):
    """Lees de eerste bytes die de server stuurt (bv. een SSH-banner)."""
    try:
        with socket.create_connection((ip, port), timeout) as c:
            c.settimeout(timeout)
            raw = c.recv(nbytes).decode("utf-8", errors="replace")
            # Alleen de eerste (tekst)regel; daarna volgt vaak binaire KEX-data.
            return raw.splitlines()[0].strip() if raw.strip() else ""
    except Exception:
        return ""


def mac_for(ip):
    """Zoek het MAC-adres van een IP in de ARP/neighbour-tabel (Linux: ip neigh)."""
    for cmd in (["ip", "neigh", "show", ip], ["arp", "-n", ip]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
        except Exception:
            continue
        m = re.search(r"([0-9a-f]{2}(?::[0-9a-f]{2}){5})", out, re.I)
        if m:
            return m.group(1).lower()
    return None


def classify_host(ip):
    """Onderzoek één host en classificeer als STB. Geeft dict of None."""
    open_ports = [p for p in AMINO_SIG_PORTS if tcp_open(ip, p)]
    cast = tcp_open(ip, 8008)
    if not open_ports and not cast:
        return None  # geen STB-signaal

    mac = mac_for(ip)
    oui = mac[:8] if mac else None
    vendor = OUIS.get(oui)

    # --- Amino Aria (Entone-platform) ---
    if 10022 in open_ports:
        banner = grab_banner(ip, 10022)
        dropbear = "dropbear" in banner.lower()
        full_sig = all(p in open_ports for p in AMINO_SIG_PORTS)
        if (vendor == "Entone" and full_sig) or (full_sig and dropbear):
            conf = "hoog"
        elif vendor == "Entone" or (dropbear and len(open_ports) >= 2):
            conf = "middel"
        else:
            conf = "laag"
        return {
            "ip": ip,
            "type": "Amino Aria 7x",
            "confidence": conf,
            "mac": mac,
            "vendor": vendor,
            "open_ports": open_ports,
            "banner": banner,
        }

    # --- Chromecast/Android-TV STB (o.a. Delta Smart-box) ---
    if cast:
        info = fetch_eureka(ip) or {}
        detail = info.get("detail", {}) or {}
        name = info.get("name", "")
        model = detail.get("model_name", "")
        man = detail.get("manufacturer", "")
        label = "Delta Smart-box" if "DELTA" in name.upper() or model == "DFN-Amigo7N" else "Chromecast/Android-TV STB"
        return {
            "ip": ip,
            "type": label,
            "confidence": "hoog" if model else "middel",
            "mac": mac,
            "vendor": vendor,
            "open_ports": [p for p in CAST_PORTS if tcp_open(ip, p)],
            "cast_name": name,
            "cast_model": f"{man} {model}".strip(),
            "cast_fw": info.get("cast_build_revision", ""),
            "ethernet": info.get("ethernet_connected"),
        }
    return None


def scan_lan(prefix, workers=64):
    """Scan prefix.1..254 en geef een lijst met gevonden STB's."""
    hosts = [f"{prefix}.{i}" for i in range(1, 255)]
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(classify_host, hosts):
            if res:
                results.append(res)
    results.sort(key=lambda r: tuple(int(x) for x in r["ip"].split(".")))
    return results


def print_scan(prefix, results):
    print(f"\n=== LAN-scan naar IPTV set-top boxes ===")
    print(f"Subnet: {prefix}.0/24\n")
    if not results:
        print("Geen set-top boxes gevonden.\n")
        return
    for i, r in enumerate(results, 1):
        vend = f"  ({r['vendor']})" if r.get("vendor") else ""
        print(f"[{i}] {r['ip']}  ->  {r['type']}   (zekerheid: {r['confidence']})")
        print(f"    MAC          : {r.get('mac') or '?'}{vend}")
        ports = ", ".join(str(p) for p in r.get("open_ports", []))
        print(f"    Open poorten : {ports or '-'}")
        if r.get("banner"):
            print(f"    SSH-banner   : {r['banner']}")
        if r.get("cast_name"):
            print(f"    Cast-naam    : {r['cast_name']}")
        if r.get("cast_model"):
            print(f"    Cast-model   : {r['cast_model']}")
        if r.get("cast_fw"):
            eth = "ethernet" if r.get("ethernet") else "wifi/anders"
            print(f"    Cast-firmware: {r['cast_fw']}  ({eth})")
        print()


def fmt_uptime(seconds):
    try:
        return str(timedelta(seconds=int(seconds)))
    except Exception:
        return str(seconds)


def print_status(ip, info):
    detail = info.get("detail", {}) or {}
    rows = [
        ("Naam", info.get("name", "?")),
        ("IP-adres", info.get("ip_address", ip)),
        ("Fabrikant", detail.get("manufacturer", "?")),
        ("Model", detail.get("model_name", "?")),
        ("Online", "ja" if info.get("connected") else "nee"),
        ("Verbinding", "bedraad (ethernet)" if info.get("ethernet_connected") else "wifi/anders"),
        ("Cast-firmware", info.get("cast_build_revision", "?")),
        ("Build", info.get("build_version", "?")),
        ("Taal", info.get("locale", "?")),
        ("Update beschikbaar", "ja" if info.get("has_update") else "nee"),
        ("Uptime", fmt_uptime(info.get("uptime", 0))),
        ("Setup-status", info.get("setup_state", "?")),
        ("UDN", info.get("ssdp_udn", "?")),
    ]
    width = max(len(k) for k, _ in rows)
    print(f"\n=== Status Delta TV-box ({ip}) ===")
    for k, v in rows:
        print(f"  {k.ljust(width)} : {v}")
    print()


def main():
    ap = argparse.ArgumentParser(description="Vind IPTV set-top boxes en lees de status uit.")
    ap.add_argument("ip", nargs="?", help="IP van de Delta-box (overslaat discovery)")
    ap.add_argument("--scan", action="store_true",
                    help="scan het hele LAN naar set-top boxes (Amino/Entone + Delta/Cast)")
    ap.add_argument("--subnet", help="te scannen /24-prefix bij --scan, bv 192.168.1 (default: auto)")
    ap.add_argument("--json", action="store_true", help="toon ook de volledige JSON")
    ap.add_argument("--raw", action="store_true", help="toon alleen de kale JSON")
    ap.add_argument("--timeout", type=float, default=8.0, help="discovery-timeout in seconden")
    args = ap.parse_args()

    if args.scan:
        prefix = args.subnet or local_prefix()[1]
        if not args.raw:
            print(f"LAN-scan op {prefix}.0/24 ... (even geduld)", file=sys.stderr)
        results = scan_lan(prefix)
        if args.raw or args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        if not args.raw:
            print_scan(prefix, results)
        return

    ip = args.ip
    if not ip:
        if not args.raw:
            print("Zoeken naar de Delta TV-box via mDNS ...", file=sys.stderr)
        res = discover(timeout=args.timeout)
        if not res:
            print("Geen Delta-box gevonden. Geef het IP handmatig mee.", file=sys.stderr)
            sys.exit(1)
        ip, name, model = res
        if not args.raw:
            print(f"Gevonden: {name} ({model}) op {ip}", file=sys.stderr)

    info = fetch_eureka(ip)
    if info is None:
        print(f"Kon eureka_info niet uitlezen op {ip} (poort 8008/8443).", file=sys.stderr)
        sys.exit(2)

    if args.raw:
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return

    print_status(ip, info)
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
