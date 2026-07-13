#!/bin/bash
# install-hooks.sh — active les hooks Git du projet (.githooks/).
# A executer une fois apres clone : bash scripts/install-hooks.sh

set -e

cd "$(dirname "$0")/.."

if [ ! -d .githooks ]; then
  echo "ERREUR : repertoire .githooks introuvable" >&2
  exit 1
fi

git config core.hooksPath .githooks
chmod +x .githooks/pre-commit

echo "Hooks actives : core.hooksPath = .githooks"
echo "Hook installe :"
ls -la .githooks/
