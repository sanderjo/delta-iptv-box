#!/usr/bin/env bash
# Wrapper: draait delta_iptv_stb_status.py via de vaste venv.
# Maakt de venv aan en installeert dependencies als die nog ontbreekt.
# Alle argumenten worden doorgegeven, bv:  ./run.sh --json   of   ./run.sh 192.168.1.172
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
PY="$VENV/bin/python"

if [[ ! -x "$PY" ]]; then
    echo "Venv ontbreekt, aanmaken ..." >&2
    python3 -m venv "$VENV"
    "$VENV/bin/pip" -q install --upgrade pip >/dev/null
    "$VENV/bin/pip" -q install -r "$DIR/requirements.txt"
fi

exec "$PY" "$DIR/delta_iptv_stb_status.py" "$@"
