#!/usr/bin/env python3
"""
delta_iptv_stb_status.py — vind de Delta IPTV-box op het netwerk en lees de status uit.

Werkwijze:
1. Ontdek de box via mDNS/Cast-discovery (_googlecast._tcp).
   Herkent de Delta-box aan model 'DFN-Amigo7N' of een naam met 'DELTA'.
2. Lees het Chromecast 'eureka_info'-endpoint uit op poort 8008 (http)
   of 8443 (https), zonder authenticatie, en toon de status.

Gebruik:
    python3 delta_iptv_stb_status.py                # automatisch zoeken + uitlezen
    python3 delta_iptv_stb_status.py 192.168.1.172  # direct een bekend IP uitlezen
    python3 delta_iptv_stb_status.py --json         # ruwe JSON tonen
    python3 delta_iptv_stb_status.py --raw          # alleen de kale JSON, niets anders

Afhankelijkheid voor automatisch zoeken: zeroconf  (pip install zeroconf)
Alleen stdlib nodig als je zelf een IP meegeeft.
"""

import argparse
import json
import socket
import ssl
import sys
import urllib.request
from datetime import timedelta

# Namen/modellen waaraan we de Delta-box herkennen bij discovery.
MODEL_MATCH = "DFN-Amigo7N"
NAME_MATCH = "DELTA"


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
    ap = argparse.ArgumentParser(description="Vind de Delta TV-box en lees de status uit.")
    ap.add_argument("ip", nargs="?", help="IP van de box (overslaat discovery)")
    ap.add_argument("--json", action="store_true", help="toon ook de volledige JSON")
    ap.add_argument("--raw", action="store_true", help="toon alleen de kale JSON")
    ap.add_argument("--timeout", type=float, default=8.0, help="discovery-timeout in seconden")
    args = ap.parse_args()

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
