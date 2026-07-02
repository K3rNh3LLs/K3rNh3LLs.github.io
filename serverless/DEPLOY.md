# Déploiement du Portier EPUB (Cloudflare Worker)

Ce guide vous mène de zéro à un service de livraison EPUB sécurisé fonctionnel.
Tout le code est prêt dans ce dossier `serverless/`. Vous n'avez qu'à configurer les comptes et lancer les commandes.

**Durée estimée : 30–45 min** (comptes + 6 commandes).

---

## 0. Prérequis (à créer)

| Compte / outil | Pourquoi | Lien |
|---|---|---|
| Compte **Cloudflare** (gratuit) | Héberger le Worker + R2 + D1 | https://dash.cloudflare.com/sign-up |
| Node.js 18+ | Exécuter `wrangler` | https://nodejs.org |
| Compte **Stripe** (déjà via le plugin) | Encaisser les paiements | https://dashboard.stripe.com |

Aucune carte de crédit requise pour Cloudflare (plan gratuit).
Plan gratuit Cloudflare : 100 000 req/jour (largement assez), R2 10 Go (vos EPUBs = 14 Mo), D1 5 GB.

---

## 1. Installer wrangler et s'authentifier

```bash
cd K3rNh3LLs.github.io/serverless
npm install
npx wrangler login          # ouvre le navigateur pour auth Cloudflare
```

Vérifier :
```bash
npx wrangler whoami
```

---

## 2. Créer les ressources Cloudflare (R2 + D1)

### 2a. Bucket R2 (stockage privé des EPUBs)
```bash
npx wrangler r2 bucket create conscience-souveraine-epubs
```
Vérifier qu'il est créé :
```bash
npx wrangler r2 bucket list
```

### 2b. Base D1 (journal des achats)
```bash
npx wrangler d1 create cs-purchases
```
→ La commande renvoie un `database_id` (ex: `abcd-1234-...`). **Copiez-le.**

Ouvrez `wrangler.toml` et remplacez :
```toml
database_id = "REMPLACER_PAR_D1_ID"
```
par votre vrai ID.

### 2c. Créer la table (local puis distant)
```bash
npm run db:init            # base locale (tests)
npm run db:init:remote     # base de production
```

---

## 3. Uploader les EPUBs vers R2 (privé)

```bash
npm run upload:epubs
```
Copie les 6 EPUBs (`books/tome-{1,2,3}-{fr,en}.epub`) vers `r2://conscience-souveraine-epubs/volN/...`.

Vérifier :
```bash
npx wrangler r2 object list conscience-souveraine-epubs
```

### ⚠ Rendre les EPUBs payants privés (CRITIQUE)
Tant qu'ils sont dans `books/` du repo GitHub, ils sont publics. Après l'upload R2 :

```bash
cd ..
git rm --cached books/tome-1-fr.epub books/tome-1-en.epub \
                books/tome-2-fr.epub books/tome-2-en.epub \
                books/tome-3-fr.epub books/tome-3-en.epub
```

Ajoutez au `.gitignore` du repo (à la racine `K3rNh3LLs.github.io/`) :
```
books/tome-1-*.epub
books/tome-2-*.epub
books/tome-3-*.epub
```
**Ne PAS retirer** `books/tome-4-*.epub` (Volume IV reste gratuit/public) ni `tome-4.html`.

Committez + poussez pour retirer les fichiers de l'historique futur. *(Pour nettoyer l'historique passé, il faudrait `git filter-repo` — optionnel, les URLs deviennent 404 une fois retirés du main.)*

---

## 4. Configurer les secrets Stripe

### 4a. Récupérer les clés Stripe
Dans le Dashboard Stripe → **Developers → API keys** :
- `Secret key` : `sk_live_...` (ou `sk_test_...` pour tester)

Dans **Developers → Webhooks** (ou via le plugin Stripe) :
- Créez un endpoint (étape 6) et récupérez le `Signing secret` : `whsec_...`
- Vous pouvez aussi créer le secret webhook via le plugin Stripe : il demandera l'URL de l'endpoint — donnez celle de l'étape 6.

### 4b. Générer la clé HMAC (pour signer les liens de téléchargement)
```bash
openssl rand -hex 32
```
→ une chaîne 64 caractères hex. Copiez-la.

### 4c. Injecter les 3 secrets (production)
```bash
npm run secret:stripe       # collez sk_live_... (ou sk_test_...)
npm run secret:webhook       # collez whsec_...
npm run secret:hmac          # collez le hex de openssl
```
Wrangler vous demande la valeur, la stocke chiffrée, n'apparaît jamais dans le code.

Pour le développement local, créez `.dev.vars` :
```bash
cp .dev.vars.example .dev.vars
# éditez .dev.vars avec vos vraies valeurs (NE JAMAIS committer ce fichier)
```

---

## 5. Déployer le Worker

```bash
npm run deploy
```
→ URL publiée : `https://cs-epub-portier.<votre-subdomain>.workers.dev`

Vérifier l'état :
```bash
curl https://cs-epub-portier.<votre-subdomain>.workers.dev/health
```
Doit renvoyer `{"ok":true,"checks":{"r2":"ok","d1":"ok","stripe_secret":"set","webhook_secret":"set","hmac_key":"set"}}`.

---

## 6. Configurer le webhook Stripe

Dashboard Stripe → **Developers → Webhooks → Add endpoint** :
- **Endpoint URL** : `https://cs-epub-portier.<votre-subdomain>.workers.dev/webhook`
  (ou `https://download.davidberthelotte.ca/webhook` si vous avez configuré le sous-domaine — voir étape 8)
- **Events to send** : `checkout.session.completed`
- Récupérez le `whsec_...` et re-injectez-le si vous aviez utilisé une valeur temporaire à l'étape 4.

*(Alternative : créer le webhook via le plugin Stripe installé — `mcp__plugin_stripe_stripe__authenticate` puis les outils webhook.)*

---

## 7. Créer les Stripe Payment Links (6 liens : Vol×langue)

Pour chaque combinaison (Vol 1/2/3 × FR/EN), créez un **Payment Link** dans Dashboard Stripe → **Product catalog → Payment Links** (ou via l'API / plugin).

Pour **chaque** Payment Link, configurez :
- **Product** : le volume correspondant (prix en USD : Vol1/2 = 2.99, Vol3 = 4.99 — ou vos prix)
- **Metadata** (section *Advanced options → Metadata*) — **OBLIGATOIRE** :
  - `vol` = `1` (ou 2, 3)
  - `lang` = `fr` (ou `en`)
- **After payment → Success URL** :
  `https://cs-epub-portier.<votre-subdomain>.workers.dev/claim?session_id={CHECKOUT_SESSION_ID}`
  (Le placeholder `{CHECKOUT_SESSION_ID}` est remplacé automatiquement par Stripe.)

Récupérez l'URL de chaque Payment Link (ex: `https://buy.stripe.com/abc123XYZ...`).

### 7b. Injecter les liens dans le site
Dans `K3rNh3LLs.github.io/i18n.js`, remplacez les placeholders par les vraies URLs :
```js
// Volume I FR
stripe_epub_url: "https://buy.stripe.com/LIEN_VOL1_FR",
// Volume I EN
stripe_epub_url: "https://buy.stripe.com/LIEN_VOL1_EN",
// ... etc pour Vol 2 et 3, FR et EN
```
Les 6 valeurs à remplacer (cherchez `TODO_URL_STRIPE_VOL` dans i18n.js). Une fois les URLs réelles mises, les boutons s'activent automatiquement (le `btn-disabled` est retiré quand `href` ne commence plus par `TODO_`).

---

## 8. (Optionnel) Sous-domaine propre `download.davidberthelotte.ca`

1. Dans le Dashboard Cloudflare, zone **davidberthelotte.ca** doit être gérée par Cloudflare (nameservers CF). Si le domaine pointe vers GitHub Pages via `K3rNh3LLs.github.io/CNAME`, ajoutez quand même la zone dans CF pour le routing Workers (ou utilisez un autre sous-domaine CF déjà géré).
2. **Workers & Pages → votre worker → Settings → Triggers → Add Custom Domain** : `download.davidberthelotte.ca`.
3. Mettez à jour l'`success_url` des Payment Links et l'endpoint webhook Stripe avec ce domaine propre.

---

## 9. Tester de bout en bout

### Test (mode sk_test_)
1. Utilisez `sk_test_` et une carte test Stripe : `4242 4242 4242 4242`, n'importe quelle date future, n'importe quel CVC.
2. Ouvrez le Payment Link test → payez.
3. Vous êtes redirigé vers `/claim?session_id=...` → `/thanks?token=...` → bouton **Télécharger l'EPUB**.
4. Le téléchargement se lance (EPUB depuis R2).
5. Vérifiez le journal : `npx wrangler d1 execute cs-purchases --remote --command "SELECT * FROM purchases ORDER BY created_at DESC LIMIT 5;"`

### Production
- Basculez les secrets en `sk_live_` et `whsec_` du webhook live.
- Vérifiez `/health` après chaque changement de secret.

---

## 10. Surveillance

```bash
npm run tail                # logs temps réel
npx wrangler d1 execute cs-purchases --remote --command "SELECT vol, lang, COUNT(*) FROM purchases GROUP BY vol, lang;"
```

Le plan gratuit Cloudflare suffit largement : 100k req/jour, R2 10 Go (14 Mo utilisés), D1 5 GB.

---

## Récapitulatif des commandes essentielles

```bash
cd K3rNh3LLs.github.io/serverless
npm install
npx wrangler login
npx wrangler r2 bucket create conscience-souveraine-epubs
npx wrangler d1 create cs-purchases          # → copier l'ID dans wrangler.toml
npm run db:init:remote
npm run upload:epubs
npm run secret:stripe && npm run secret:webhook && npm run secret:hmac
npm run deploy
```
Puis : configurer webhook Stripe + créer 6 Payment Links + injecter leurs URLs dans `i18n.js`.

---

## Dépannage

| Symptôme | Cause / fix |
|---|---|
| `/health` → `r2: error` | Bucket non créé ou mal nommé dans `wrangler.toml` |
| `/health` → `stripe_secret: MISSING` | Secret non injecté : `npm run secret:stripe` |
| `/claim` → `not_paid` | Session Stripe non payée (mode test ? utilisez carte `4242…`) |
| `/claim` → `bad_metadata` | Metadata `vol`/`lang` oubliée sur le Payment Link Stripe |
| `/dl/:token` → `invalid_or_expired_token` | Lien > 24 h, ou `DOWNLOAD_HMAC_KEY` changé depuis émission |
| Stripe webhook `400 invalid_signature` | `STRIPE_WEBHOOK_SECRET` incorrect (recréé ?) ou payload modifié |
| EPUB 404 sur le site après retrait du repo | Normal — le téléchargement passe par le Worker maintenant, pas par `/books/` |

Pour tout souci : `berthelotte.d@gmail.com` — indiquez le `session_id` Stripe et la sortie de `/health`.