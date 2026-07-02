-- Conscience Souveraine — journal des achats EPUB (Cloudflare D1)
-- À exécuter :  wrangler d1 execute cs-purchases --file schema.sql  (local)
--           puis wrangler d1 execute cs-purchases --remote --file schema.sql  (prod)

CREATE TABLE IF NOT EXISTS purchases (
  session_id   TEXT PRIMARY KEY,           -- Stripe Checkout Session ID (ex: cs_test_...)
  vol         INTEGER NOT NULL,             -- 1 | 2 | 3
  lang        TEXT NOT NULL,                -- 'fr' | 'en'
  email       TEXT,                          -- email acheteur (si fourni par Stripe)
  amount_paid INTEGER,                       -- montant en centimes (ex: 299 = 2.99 USD)
  currency    TEXT,                          -- 'usd', 'cad'...
  status      TEXT NOT NULL DEFAULT 'paid',
  claimed_at  INTEGER,                       -- timestamp 1ère réclamation /dl (ms) — null si non réclamé
  created_at  INTEGER NOT NULL               -- timestamp webhook/claim (ms)
);

CREATE INDEX IF NOT EXISTS idx_purchases_status ON purchases(status);
CREATE INDEX IF NOT EXISTS idx_purchases_claimed ON purchases(claimed_at);
CREATE INDEX IF NOT EXISTS idx_purchases_created ON purchases(created_at);

-- Vue de comptabilité (optionnelle) : ventes par volume/langue
CREATE VIEW IF NOT EXISTS v_sales_summary AS
  SELECT vol, lang,
         COUNT(*) AS sales,
         SUM(CASE WHEN claimed_at IS NOT NULL THEN 1 ELSE 0 END) AS claimed,
         SUM(amount_paid) AS total_cents
  FROM purchases
  GROUP BY vol, lang;