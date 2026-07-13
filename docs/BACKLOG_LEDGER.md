# Ledger immuable de backlog — K3rNh3LLs.github.io

> Port du système SESSION139 de **Gen by JRT** (`scripts/backlog.py` +
> `docs/audit/DEEP_AUDIT_LEDGER.json` + hook `check_backlog_consistency.py`),
> adapté à ce dépôt. Même contrat, mêmes invariants, mêmes statuts.

## Principe

- **`docs/audit/BACKLOG_LEDGER.json`** est la **source de vérité machine**
  du backlog. Personne — ni agent, ni humain, ni workflow — ne l'édite à la
  main : toutes les mutations passent par **`python3 scripts/backlog.py <cmd>`**.
- Chaque mutation est faite sous **verrou exclusif** (`fcntl.flock` sur
  `docs/audit/BACKLOG_LEDGER.lock`, non commité) avec **écriture atomique**
  (tempfile + `os.replace`) — plusieurs sessions/agents peuvent muter en
  parallèle sans corruption.
- Chaque événement est journalisé deux fois : dans l'`audit_log` de l'item
  **et** dans le journal global **append-only**
  `docs/audit/BACKLOG_AUDITLOG.jsonl` (le « ledger immuable » : on n'y
  réécrit jamais, on n'y supprime jamais).
- **`BACKLOG.md`** reste le document lisible : le bloc entre
  `<!--LEDGER:BEGIN-->` et `<!--LEDGER:END-->` est un **rendu généré**
  (`python3 scripts/backlog.py render`) — ne jamais l'éditer à la main.
  Le reste du fichier (notes de session, historique) reste éditable librement.

## Cycle de vie d'un item

```
open ──claim/next──▶ in_progress ──done──▶ done (commit_sha + branch obligatoires)
  ▲                        │
  │                        ├─release/reset──▶ open
  │                        └─defer──▶ deferred
  └── stale-release automatique si heartbeat > 60 min
verify CONFIRMED|REFUTED  (REFUTED sur un item non-done ⇒ status=refuted)
```

Statuts valides : `open`, `in_progress`, `done`, `deferred`, `hors_champ`, `refuted`.
Sévérités : `critical`→P0, `high`→P1, `medium`→P2, `low`→P4.

## Commandes

```bash
python3 scripts/backlog.py list [--status open] [--json]   # état courant
python3 scripts/backlog.py show <ID>                       # détail complet d'un item
python3 scripts/backlog.py next --severity=any             # réclamer le prochain item ouvert
python3 scripts/backlog.py claim <ID>                      # réclamer un item précis
python3 scripts/backlog.py heartbeat <ID>                  # garder la claim vivante (>60 min = libéré)
python3 scripts/backlog.py done <ID> <sha> <branche>       # marquer livré (sha obligatoire)
python3 scripts/backlog.py verify <ID> CONFIRMED|REFUTED --reason "..."
python3 scripts/backlog.py defer <ID> "raison"             # reporter avec raison
python3 scripts/backlog.py release <ID> | reset <ID>       # libérer / remettre à zéro
python3 scripts/backlog.py add-hors-champ "desc" fichier:ligne P2   # découverte hors mission
python3 scripts/backlog.py add-feedback "desc" fichier:ligne P1 --source bug --reporter nom
python3 scripts/backlog.py seed-from-audit --from findings.json     # semer depuis un audit
python3 scripts/backlog.py import-open-backlog             # importer les IDs ouverts de BACKLOG.md
python3 scripts/backlog.py seed-from-feedback              # importer les issues GitHub bug/feedback
python3 scripts/backlog.py render                          # regénérer le bloc LEDGER de BACKLOG.md
python3 scripts/backlog.py reconcile                       # vérifier les invariants + libérer les claims mortes
python3 scripts/backlog.py monthly-audit                   # récap mensuel + render + reconcile
```

## Invariants (bloquants au commit)

Le hook pre-commit exécute `python3 scripts/check_backlog_consistency.py` :

1. `done` ⇒ `commit_sha` présent.
2. `in_progress` ⇒ `owner_session` présent.
3. `refuted` ⇒ `verified == "REFUTED"`.
4. Statut ∈ {open, in_progress, done, deferred, hors_champ, refuted}, IDs uniques.
5. Si le ledger existe, le bloc `<!--LEDGER:BEGIN/END-->` doit être présent
   dans `BACKLOG.md` (rendu synchronisé).
6. (Legacy) toute ligne `- [ID] [Px] [FIXÉ]` doit référencer un commit réel ;
   toute ligne `[OUVERT]` ne doit pas avoir de commit de clôture sur HEAD.

Violation ⇒ commit bloqué. Ne pas contourner avec `--no-verify` : corriger le
ledger (`render`, `reconcile`, ou la vraie commande de mutation) à la place.

## Conventions

- **UID de session** : `CLAUDE_CODE_SESSION_ID` (env), sinon `manual`.
- **Tag de commit** : `LEDGER — <ID> — <résumé>` pour tout commit qui livre un item.
- **Format findings JSON** (pour `seed-from-audit`) : clés `confirmed` /
  `uncertain` / `refuted`, chaque item `{id, severity, file, line, summary,
  evidence?, failure_scenario?, axis?, verify_cmd?}`.
- **Commande de vérification par défaut** de ce dépôt : `echo Site statique GitHub Pages — verifier le rendu dans le navigateur apres deploiement`
  (surchargeable par item via `verify_cmd` dans le findings JSON).
- **Multi-agents (fourmis en worktrees)** : les fourmis appellent
  `scripts/backlog.py` via son **chemin absolu dans le worktree principal**
  pour muter le ledger principal ; seul le coordinateur commite le ledger et
  l'auditlog.

## Installation du hook (une fois après clone)

```bash
bash scripts/install-hooks.sh    # git config core.hooksPath .githooks
```
