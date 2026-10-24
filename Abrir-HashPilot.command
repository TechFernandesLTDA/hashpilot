#!/bin/zsh
set -e
cd -- "$(dirname -- "$0")"
if ! command -v mise >/dev/null 2>&1; then
  echo "Instale/configure o mise antes de abrir o HashPilot."
  exit 1
fi
exec mise exec -- python monitor.py "$@"
