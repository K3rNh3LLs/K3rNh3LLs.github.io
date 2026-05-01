#!/bin/bash
# sync_from_publication.sh — Synchronise manuellement le site avec le pipeline de publication
# Usage: bash scripts/sync_from_publication.sh
# À exécuter depuis le répertoire du site web

set -euo pipefail

PUBLICATION_DIR="/mnt/d/conscience_souveraine"
WEBSITE_DIR="/mnt/d/K3rnh3lls.github.io/K3rNh3LLs.github.io"

echo "[SYNC] === Synchronisation du site web avec le pipeline de publication ==="
echo "[SYNC] Date: $(date)"

# 1. Vérification
echo "[SYNC] Vérification des sources..."
for i in 1 2 3 4; do
    src="${PUBLICATION_DIR}/assets/cover/vol.${i} cover.jpg"
    if [ ! -f "$src" ]; then
        echo "[SYNC]   ✗ vol.${i} cover.jpg introuvable dans ${PUBLICATION_DIR}/assets/cover/"
    else
        echo "[SYNC]   ✓ vol.${i} cover.jpg trouvé"
    fi
done

# 2. Copie des couvertures
echo "[SYNC] Copie des couvertures..."
mkdir -p "${WEBSITE_DIR}/covers"
for i in 1 2 3 4; do
    src="${PUBLICATION_DIR}/assets/cover/vol.${i} cover.jpg"
    dst="${WEBSITE_DIR}/covers/vol${i}.jpg"
    if [ -f "$src" ]; then
        cp "$src" "$dst"
        echo "[SYNC]   ✓ covers/vol${i}.jpg mis à jour"
    fi
done

# 3. Métadonnées ISBN
echo "[SYNC] Extraction des ISBN..."
if [ -f "${PUBLICATION_DIR}/dist/d2d/d2d_all.json" ]; then
    node -e "
const fs = require('fs');
const d2d = JSON.parse(fs.readFileSync('${PUBLICATION_DIR}/dist/d2d/d2d_all.json', 'utf8'));
const map = {
    'cs-vol1-fr': {vol: 0, lang: 'fr'},
    'cs-vol1-en': {vol: 0, lang: 'en'},
    'cs-vol2-fr': {vol: 1, lang: 'fr'},
    'cs-vol2-en': {vol: 1, lang: 'en'},
    'cs-vol3-fr': {vol: 2, lang: 'fr'},
    'cs-vol3-en': {vol: 2, lang: 'en'},
    'cs-vol4-fr': {vol: 3, lang: 'fr'},
    'cs-vol4-en': {vol: 3, lang: 'en'},
};
const isbns = {};
d2d.forEach(item => {
    const m = map[item.sku];
    if (m) {
        const k = m.lang + '_' + m.vol;
        isbns[k] = {
            epub: item.book.isbn_epub,
            print: item.book.isbn_print || null,
            price: item.pricing?.base_price || null,
        };
    }
});
console.log(JSON.stringify(isbns, null, 2));
" > "${WEBSITE_DIR}/scripts/isbn_extracted.json"
    echo "[SYNC]   ✓ ISBN extraits vers scripts/isbn_extracted.json"
else
    echo "[SYNC]   ✗ dist/d2d/d2d_all.json introuvable"
fi

# 4. Commit et push
echo "[SYNC] Déploiement..."
cd "$WEBSITE_DIR"
git add -A
git diff --staged --quiet || {
    git commit -m "Auto-sync: mise à jour des couvertures et métadonnées depuis conscience_souveraine

Co-Authored-By: Claude Automation <noreply@anthropic.com>"
    git push origin main
    echo "[SYNC]   ✓ Push effectué"
}

echo "[SYNC] === Synchronisation terminée ==="
