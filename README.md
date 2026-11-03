# delta-iptv-box

Vind de **Delta IPTV set-top box** (SDMC **DFN-Amigo7N**, "DELTA Smart-box",
Model Number: **DV9651**) op je lokale netwerk en lees de status uit — zonder inloggen.

De box is een Android TV / IPTV-ontvanger met ingebouwde Chromecast. Via de
Chromecast-`eureka_info`-API (poort 8443/8008) is apparaatinformatie openbaar
opvraagbaar: naam, model, netwerkverbinding, firmware, uptime en taal.

## Wat het toont

```
=== Status Delta TV-box (192.168.1.172) ===
  Naam               : DELTA Smart-box
  IP-adres           : 192.168.1.172
  Fabrikant          : SDMC
  Model              : DFN-Amigo7N
  Online             : ja
  Verbinding         : bedraad (ethernet)
  Cast-firmware      : 3.72.446070
  Build              : 446070
  Taal               : nl-NL
  Update beschikbaar : nee
  Uptime             : 5 days, 5:14:10
  Setup-status       : 60
  UDN                : bcc0493e-39e9-1dbe-1638-cce452df409a
```

## Hoe het werkt

1. **Ontdekken** — zoekt de box via mDNS/Cast-discovery (`_googlecast._tcp`) en
   herkent hem aan model `DFN-Amigo7N` of een naam met `DELTA`.
2. **Uitlezen** — haalt `eureka_info` op via `https://<ip>:8443` (met fabrikant/model)
   en valt terug op `http://<ip>:8008`.
3. **Tonen** — print de status, of de ruwe JSON.

## Gebruik

Met de meegeleverde wrapper (maakt zo nodig zelf een `.venv` aan):

```bash
./run.sh                 # automatisch zoeken + uitlezen
./run.sh 192.168.1.172   # direct een bekend IP
./run.sh --json          # ook de volledige JSON
./run.sh --raw           # alleen de kale JSON (handig voor pipes)
```

Of rechtstreeks met Python:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python delta_iptv_stb_status.py
```

## Afhankelijkheden

- Python 3
- [`zeroconf`](https://pypi.org/project/zeroconf/) — alleen nodig voor automatisch
  zoeken. Geef je zelf een IP mee, dan is puur de standaardbibliotheek genoeg.

## Opmerkingen

- Dit leest alleen **apparaatstatus**. Welke zender/app op dat moment op het scherm
  draait is via het netwerk niet op te vragen (daarvoor zou ADB open moeten staan,
  en dat is bij deze provider-box dicht).
- Wat er actief naar de box *gecast* wordt, is wel zichtbaar via het Cast-protocol
  op poort 8009 (bv. met [`catt`](https://github.com/skorokithakis/catt) of
  [`pychromecast`](https://github.com/home-assistant-libs/pychromecast)).

## Disclaimer

Alleen bedoeld voor gebruik op je eigen netwerk en je eigen apparaat.
