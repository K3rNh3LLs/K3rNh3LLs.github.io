# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

Site statique GitHub Pages de K3rNh3LLs (`index.html`, `v-tore.html`,
`tome-4.html`, `books/`, `covers/`, `style.css`, `i18n.js`, compteurs de
téléchargement `counter.js`/`download-counter.js` + `serverless/`).
Synchronisation du contenu de publication via
`bash scripts/sync_from_publication.sh` ; déploiement via `deploy.sh`.

Vérification : site statique — ouvrir les pages dans le navigateur après
modification (pas de suite de tests automatisée).

## Ledger immuable de backlog

Ce dépôt utilise le même système de ledger immuable de backlog que Gen by JRT
(port du SESSION139). Documentation complète : `docs/BACKLOG_LEDGER.md`.

- **Source de vérité machine** : `docs/audit/BACKLOG_LEDGER.json` — ne JAMAIS
  l'éditer à la main. Toute mutation passe par `python3 scripts/backlog.py <cmd>`
  (claim/next/done/verify/defer/render/reconcile…), sous flock + écriture atomique.
- **Journal append-only** : `docs/audit/BACKLOG_AUDITLOG.jsonl` — rien n'y est
  jamais réécrit ni supprimé.
- **Rendu lisible** : le bloc `<!--LEDGER:BEGIN-->…<!--LEDGER:END-->` dans
  `BACKLOG.md` est généré par `python3 scripts/backlog.py render` — ne pas
  l'éditer à la main.
- **Gate pre-commit** : `.githooks/pre-commit` exécute
  `scripts/check_backlog_consistency.py` (invariants done⇒commit_sha,
  in_progress⇒owner_session, refuted⇒REFUTED, bloc render présent).
  Violation = commit bloqué.
- **Activation après clone** : `bash scripts/install-hooks.sh`.
- **Tag de commit** : `LEDGER — <ID> — <résumé>` pour tout commit livrant un item.

## Conventions

- Auteur : David Berthelotte
- Ne JAMAIS mentionner JRT Inc. dans le code ou les commentaires
