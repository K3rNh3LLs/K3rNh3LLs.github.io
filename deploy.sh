#!/bin/bash
# deploy_website.sh — Déploie le site web sur GitHub Pages
# Usage: bash deploy_website.sh
# À exécuter depuis /mnt/d/K3rnh3lls.github.io/K3rNh3LLs.github.io

set -e

echo "[DEPLOY] Déploiement du site web sur GitHub Pages..."

git add -A
git commit -m "$(cat <<'EOF'
Mise à jour du site web — vitrine multilingue Conscience Souveraine

- Site prioritairement en français avec switch EN/ES
- Intégration des 4 couvertures de volumes
- Affichage des ISBN EPUB et Print
- Boutons de pré-commande par mailto
- Suppression du lien ResearchGate
- CSS et JS externalisés

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)" || echo "[DEPLOY] Aucun changement à committer"

echo "[DEPLOY] Push vers origin/main..."
git push origin main

echo "[DEPLOY] Déploiement terminé. Le site sera visible sur https://davidberthelotte.ca dans quelques minutes."
