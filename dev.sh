#!/usr/bin/env bash
# Sobe a API e a interface juntas. Ctrl+C encerra as duas.
set -e
cd "$(dirname "$0")"

[ -s "$HOME/.nvm/nvm.sh" ] && source "$HOME/.nvm/nvm.sh"
[ -d web/node_modules ] || (cd web && npm install)

.venv/bin/uvicorn api.main:app --port 8000 --reload --reload-dir api --reload-dir engine --reload-dir samples &
API=$!
trap 'kill "$API" 2>/dev/null' EXIT

cd web
npm run dev
