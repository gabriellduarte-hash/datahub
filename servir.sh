#!/usr/bin/env bash
# O hub num endereço só: exporta a interface e a API entrega tudo na porta 8000.
# É como ele roda no GitHub Codespaces. Para programar, prefira ./dev.sh.
#
#   ./servir.sh                 exporta a interface (se ainda não existe) e sobe
#   ./servir.sh --reconstruir   exporta de novo, depois de mudar a interface
set -e
cd "$(dirname "$0")"
[ -s "$HOME/.nvm/nvm.sh" ] && source "$HOME/.nvm/nvm.sh"

if [ "$1" = "--reconstruir" ] || [ ! -f web/out/index.html ]; then
  (cd web && { [ -d node_modules ] || npm ci; } && DATAHUB_EXPORTAR=1 NEXT_PUBLIC_API_URL= npm run build)
fi

echo "datahub em http://localhost:${PORT:-8000}"
DATAHUB_WEB="$PWD/web/out" exec .venv/bin/python -m uvicorn api.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
