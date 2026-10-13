#!/usr/bin/env bash
# Lance l'outil de scraping en local.
set -e
cd "$(dirname "$0")"

# Trouve un interpréteur Python (3.10+). Ordre : python3, python, Python autonome local.
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ] && [ -x "$HOME/python/bin/python3" ]; then
  PY="$HOME/python/bin/python3"
fi
if [ -z "$PY" ]; then
  echo "✗ Python introuvable. Installe Python 3.10+ (ex. 'sudo apt install python3 python3-venv')."
  exit 1
fi

# Crée l'environnement virtuel au premier lancement
if [ ! -d ".venv" ]; then
  echo "→ Création de l'environnement virtuel avec $PY..."
  "$PY" -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi

echo "→ Démarrage sur http://127.0.0.1:8000"
cd backend
../.venv/bin/uvicorn main:app --reload --port 8000
