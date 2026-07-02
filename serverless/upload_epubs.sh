#!/usr/bin/env bash
# upload_epubs.sh — Copie les EPUBs Vol I/II/III (FR+EN) du repo vers le bucket R2 privé.
#
# Prérequis :
#   - wrangler installé (npm i -g wrangler) et authentifié (wrangler login)
#   - bucket créé :  wrangler r2 bucket create conscience-souveraine-epubs
#
# Après upload R2 réussi, RETIREZ les EPUBs du repo GitHub (sinon ils restent publics) :
#   cd .. && git rm --cached books/tome-1-fr.epub books/tome-1-en.epub \
#                       books/tome-2-fr.epub books/tome-2-en.epub \
#                       books/tome-3-fr.epub books/tome-3-en.epub
#   Puis ajoutez `books/tome-*.epub` (sauf vol4) au .gitignore du repo.
#   ⚠ NE PAS retirer les EPUBs du Volume IV (tome-4-*.epub) : ils restent publics (gratuit).

set -euo pipefail

WEBSITE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUCKET="conscience-souveraine-epubs"
BOOKS_DIR="${WEBSITE_DIR}/books"

echo "[UPLOAD] Bucket : ${BUCKET}"
echo "[UPLOAD] Source  : ${BOOKS_DIR}"
echo ""

declare -a FILES=(
  "tome-1-fr.epub:vol1/tome-1-fr.epub"
  "tome-1-en.epub:vol1/tome-1-en.epub"
  "tome-2-fr.epub:vol2/tome-2-fr.epub"
  "tome-2-en.epub:vol2/tome-2-en.epub"
  "tome-3-fr.epub:vol3/tome-3-fr.epub"
  "tome-3-en.epub:vol3/tome-3-en.epub"
)

for pair in "${FILES[@]}"; do
  src="${BOOKS_DIR}/${pair%%:*}"
  dst="${pair##*:}"
  if [ ! -f "$src" ]; then
    echo "  ✗ MANQUANT : $src — exécutez d'abord la copie EPUB depuis conscience_souveraine"
    exit 1
  fi
  echo "  ↑ ${src##*/}  →  r2://${BUCKET}/${dst}"
  wrangler r2 object put "${BUCKET}/${dst}" --file "$src" --content-type "application/epub+zip" --remote
done

echo ""
echo "[UPLOAD] Terminé. Vérifiez :"
echo "  wrangler r2 object list ${BUCKET}"
echo ""
echo "[SÉCURITÉ] Retirez maintenant les EPUBs payants du repo public (voir en-tête de ce script)."
echo "  cd ..  && git rm --cached books/tome-1-fr.epub books/tome-1-en.epub \\"
echo "                          books/tome-2-fr.epub books/tome-2-en.epub \\"
echo "                          books/tome-3-fr.epub books/tome-3-en.epub"