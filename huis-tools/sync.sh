#!/bin/sh
# Huis inspo <-> vangenderingen.nl/huis. Uitleg: huis-tools/LEESMIJ.md of `huis-tools/sync.sh --help`.
# Zoekt een Python met `cryptography`; anders maakt hij eenmalig een eigen omgeving.
HIER="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/Library/Application Support/huis-site/venv"
for PY in python3 /usr/local/bin/python3.13 /usr/bin/python3 "$VENV/bin/python"; do
  if command -v "$PY" >/dev/null 2>&1 && "$PY" -c "import cryptography.hazmat.primitives.ciphers.aead" 2>/dev/null; then
    exec "$PY" -I "$HIER/sync.py" "$@"
  fi
done
echo "Eenmalig een Python-omgeving met cryptography maken in $VENV …"
python3 -m venv "$VENV" && "$VENV/bin/pip" install -q cryptography || exit 1
exec "$VENV/bin/python" -I "$HIER/sync.py" "$@"
