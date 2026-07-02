/**
 * Conscience Souveraine — Portier EPUB (Cloudflare Worker)
 *
 * Rôle : livrer les EPUBs des Vol I/II/III uniquement après paiement Stripe,
 * via un lien à durée limitée signé HMAC. Les EPUBs sont stockés dans R2 (privé),
 * jamais dans le repo GitHub Pages public.
 *
 * Endpoints :
 *   GET  /                  — page d'information (sans secret)
 *   GET  /health            — check (R2 + D1 + secrets)
 *   POST /webhook           — Stripe webhook (vérifie signature, journalise l'achat en D1)
 *   GET  /claim?session_id=…— après paiement : vérifie session via API Stripe, émet un token, redirige vers /thanks
 *   GET  /dl/:token          — vérifie token HMAC + expiration, stream l'EPUB depuis R2
 *   GET  /thanks?token=…    — page de remerciement HTML avec bouton de téléchargement
 *
 * Secrets (wrangler secret put) :
 *   STRIPE_SECRET_KEY     sk_live_... ou sk_test_...
 *   STRIPE_WEBHOOK_SECRET whsec_...
 *   DOWNLOAD_HMAC_KEY    32 octets hex aléatoires (openssl rand -hex 32)
 *
 * Vars (wrangler.toml) :
 *   SITE_ORIGIN          https://davidberthelotte.ca
 *   PUBLISHABLE_BASE      https://download.davidberthelotte.ca (ce worker)
 *
 * Bindings :
 *   R2  EPUBS   bucket conscience-souveraine-epubs
 *   D1  DB      base cs-purchases
 *
 * Licence : UBLinx Open Innovation v1.0 — David Berthelotte 2026
 */

// ---------------------------------------------------------------------------
// Configuration & helpers
// ---------------------------------------------------------------------------

const EPUB_KEYS = {
  // vol -> { fr, en}  clés d'objet R2 (upload_epubs.sh crée ces clés)
  1: { fr: 'vol1/tome-1-fr.epub', en: 'vol1/tome-1-en.epub' },
  2: { fr: 'vol2/tome-2-fr.epub', en: 'vol2/tome-2-en.epub' },
  3: { fr: 'vol3/tome-3-fr.epub', en: 'vol3/tome-3-en.epub' },
};

const TOKEN_TTL_SECONDS = 24 * 60 * 60; // 24 h
const CONTENT_TYPE_EPUB = 'application/epub+zip';

// CORS : le worker accepte les appels depuis le site uniquement (informationnel,
// les endpoints fonctionnels ne sont pas appelés en cross-origin par le navigateur).
const ALLOWED_ORIGIN = env => env.SITE_ORIGIN || 'https://davidberthelotte.ca';

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Stripe-Signature',
    'Vary': 'Origin',
  };
}

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', ...extra },
  });
}

function html(body, status = 200) {
  return new Response(body, { status, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}

// Web Crypto : HMAC-SHA256
async function hmac(key, msg) {
  const k = await crypto.subtle.importKey('raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', k, msg);
  return sig; // ArrayBuffer
}

function bufToB64url(buf) {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function b64urlToBuf(s) {
  s = s.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  const bin = atob(s);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

async function signToken(env, payload) {
  const body = bufToB64url(new TextEncoder().encode(JSON.stringify(payload)).buffer);
  const key = new TextEncoder().encode(env.DOWNLOAD_HMAC_KEY);
  const sig = await hmac(key, new TextEncoder().encode(body));
  return body + '.' + bufToB64url(sig);
}

async function verifyToken(env, token) {
  const parts = String(token).split('.');
  if (parts.length !== 2) return null;
  const [body, sig] = parts;
  const key = new TextEncoder().encode(env.DOWNLOAD_HMAC_KEY);
  const expected = await hmac(key, new TextEncoder().encode(body));
  const got = new Uint8Array(b64urlToBuf(sig));
  const exp = new Uint8Array(expected);
  if (got.length !== exp.length) return null;
  let ok = 0;
  for (let i = 0; i < got.length; i++) ok |= got[i] ^ exp[i];
  if (ok !== 0) return null; // signature invalide
  let payload;
  try { payload = JSON.parse(new TextDecoder().decode(b64urlToBuf(body))); }
  catch { return null; }
  if (typeof payload.exp !== 'number' || Date.now() > payload.exp) return null;
  return payload;
}

// Vérification signature webhook Stripe (t=...,v1=...)
async function verifyStripeSignature(env, payload, signatureHeader) {
  const parts = String(signatureHeader || '').split(',');
  let t = null, v1 = null;
  for (const p of parts) {
    const [k, v] = p.split('=');
    if (k === 't') t = v;
    if (k === 'v1') v1 = v;
  }
  if (!t || !v1) return false;
  const key = new TextEncoder().encode(env.STRIPE_WEBHOOK_SECRET);
  const sig = await hmac(key, new TextEncoder().encode(`${t}.${payload}`));
  const expected = Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2, '0')).join('');
  return expected === v1;
}

// ---------------------------------------------------------------------------
// D1 : journal des achats (anti-rejeu + traçabilité)
// ---------------------------------------------------------------------------

async function recordPurchase(db, { sessionId, vol, lang, email, amount, currency }) {
  await db.prepare(
    `INSERT OR IGNORE INTO purchases (session_id, vol, lang, email, amount_paid, currency, created_at, status)
     VALUES (?, ?, ?, ?, ?, ?, ?, 'paid')`
  ).bind(sessionId, vol, lang, email || null, amount || null, currency || null, Date.now()).run();
}

async function markClaimed(db, sessionId) {
  await db.prepare(
    `UPDATE purchases SET claimed_at = ? WHERE session_id = ? AND claimed_at IS NULL`
  ).bind(Date.now(), sessionId).run();
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

async function handleWebhook(request, env) {
  const raw = await request.text();
  const sig = request.headers.get('Stripe-Signature');
  if (!await verifyStripeSignature(env, raw, sig)) {
    return json({ error: 'invalid_signature' }, 400);
  }
  let evt;
  try { evt = JSON.parse(raw); } catch { return json({ error: 'bad_json' }, 400); }

  if (evt.type === 'checkout.session.completed') {
    const s = evt.data.object;
    const meta = s.metadata || {};
    const vol = parseInt(meta.vol, 10);
    const lang = meta.lang;
    if (!EPUB_KEYS[vol] || !EPUB_KEYS[vol][lang]) {
      return json({ error: 'bad_metadata', meta }, 200); // 200 pour éviter Stripe de retenter indéfiniment
    }
    await recordPurchase(env.DB, {
      sessionId: s.id,
      vol, lang,
      email: s.customer_details?.email || s.customer_email || null,
      amount: s.amount_total,
      currency: s.currency,
    });
  }
  return json({ received: true });
}

async function handleClaim(request, env) {
  const url = new URL(request.url);
  const sessionId = url.searchParams.get('session_id');
  if (!sessionId) return html(claimErrorPage('missing_session'), 400);

  // Vérifie la session auprès de Stripe (source de vérité du paiement)
  const r = await fetch(`https://api.stripe.com/v1/checkout/sessions/${encodeURIComponent(sessionId)}`, {
    headers: { Authorization: `Bearer ${env.STRIPE_SECRET_KEY}` },
  });
  if (!r.ok) {
    const txt = await r.text();
    return html(claimErrorPage('stripe_lookup_failed', txt), 502);
  }
  const session = await r.json();
  if (session.payment_status !== 'paid') {
    return html(claimErrorPage('not_paid', session.payment_status), 402);
  }
  const vol = parseInt(session.metadata?.vol, 10);
  const lang = session.metadata?.lang;
  if (!EPUB_KEYS[vol] || !EPUB_KEYS[vol][lang]) {
    return html(claimErrorPage('bad_metadata'), 400);
  }
  // Journalise (idempotent) au cas où le webhook ne serait pas encore arrivé
  await recordPurchase(env.DB, {
    sessionId: session.id, vol, lang,
    email: session.customer_details?.email || session.customer_email || null,
    amount: session.amount_total, currency: session.currency,
  });

  const token = await signToken(env, {
    sid: session.id,
    vol, lang,
    exp: Date.now() + TOKEN_TTL_SECONDS * 1000,
  });
  return Response.redirect(`${new URL(request.url).origin}/thanks?token=${token}`, 302);
}

async function handleDownload(request, env, token) {
  const payload = await verifyToken(env, token);
  if (!payload) return html(claimErrorPage('invalid_or_expired_token'), 403);

  const vol = parseInt(payload.vol, 10);
  const lang = payload.lang;
  const key = EPUB_KEYS[vol]?.[lang];
  if (!key) return html(claimErrorPage('bad_token_payload'), 400);

  const obj = await env.EPUBS.get(key);
  if (!obj) return html(claimErrorPage('epub_missing'), 404);

  // Marque la première réclamation (anti-rejeu / stats)
  try { await markClaimed(env.DB, payload.sid); } catch {}

  const filename = `Conscience_Souveraine_Vol${vol}_${lang === 'fr' ? 'FR' : 'EN'}.epub`;
  return new Response(obj.body, {
    headers: {
      'Content-Type': CONTENT_TYPE_EPUB,
      'Content-Disposition': `attachment; filename="${filename}"`,
      'Content-Length': obj.size,
      'Cache-Control': 'private, no-store',
    },
  });
}

async function handleThanks(request, env) {
  const url = new URL(request.url);
  const token = url.searchParams.get('token');
  if (!token) return html(claimErrorPage('missing_token'), 400);
  const payload = await verifyToken(env, token);
  if (!payload) return html(claimErrorPage('invalid_or_expired_token'), 403);

  const vol = parseInt(payload.vol, 10);
  const lang = payload.lang;
  const expDate = new Date(payload.exp).toLocaleString(lang === 'fr' ? 'fr-CA' : 'en-CA');
  const label = lang === 'fr'
    ? `Volume ${['I','II','III','IV'][vol-1]} — numérique (EPUB)`
    : `Volume ${['I','II','III','IV'][vol-1]} — digital (EPUB)`;
  const dlUrl = `${url.origin}/dl/${token}`;

  const titleTxt = lang === 'fr' ? 'Merci — votre téléchargement' : 'Thank you — your download';
  const btnTxt = lang === 'fr' ? '⬇ Télécharger l’EPUB' : '⬇ Download the EPUB';
  const expTxt = lang === 'fr' ? `Lien valide jusqu’au ${expDate}.` : `Link valid until ${expDate}.`;
  const noteTxt = lang === 'fr'
    ? 'Conservez ce lien : il reste actif 24 h. Au-delà, contactez berthelotte.d@gmail.com avec votre numéro de session Stripe.'
    : 'Keep this link: it stays active for 24 h. After that, contact berthelotte.d@gmail.com with your Stripe session ID.';

  return html(`<!DOCTYPE html><html lang="${lang}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${titleTxt}</title>
<style>
  body{margin:0;background:#0B1A2F;color:#E8E0D0;font-family:Georgia,serif;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:2rem;box-sizing:border-box}
  .card{max-width:560px;background:#142844;border-top:3px solid #C9A76C;border-radius:6px;padding:2.5rem;text-align:center}
  h1{color:#E8C887;font-family:'Spectral',serif;margin:0 0 .5rem;font-size:1.5rem}
  .vol{color:#B5A989;margin:0 0 1.75rem}
  a.btn{display:inline-block;background:#C9A76C;color:#0B1A2F;text-decoration:none;font-weight:700;padding:.9rem 1.6rem;border-radius:4px;margin:.5rem 0;font-size:1.05rem}
  a.btn:hover{background:#E8C887}
  .exp{color:#B5A989;font-size:.85rem;margin-top:1.25rem}
  .note{color:#7d7257;font-size:.78rem;margin-top:.75rem;line-height:1.5}
</style></head><body><div class="card">
<h1>${titleTxt}</h1>
<p class="vol">${label}</p>
<a class="btn" href="${dlUrl}">${btnTxt}</a>
<p class="exp">${expTxt}</p>
<p class="note">${noteTxt}</p>
</div></body></html>`);
}

async function handleHealth(env) {
  const out = { ok: true, checks: {} };
  try {
    const o = await env.EPUBS.head('vol1/tome-1-fr.epub');
    out.checks.r2 = o ? 'ok' : 'bucket_ok_object_missing';
  } catch (e) { out.checks.r2 = 'error:' + e.message; out.ok = false; }
  try {
    await env.DB.prepare('SELECT 1').run();
    out.checks.d1 = 'ok';
  } catch (e) { out.checks.d1 = 'error:' + e.message; out.ok = false; }
  out.checks.stripe_secret = env.STRIPE_SECRET_KEY ? 'set' : 'MISSING';
  out.checks.webhook_secret = env.STRIPE_WEBHOOK_SECRET ? 'set' : 'MISSING';
  out.checks.hmac_key = env.DOWNLOAD_HMAC_KEY ? 'set' : 'MISSING';
  if (out.checks.stripe_secret === 'MISSING' || out.checks.webhook_secret === 'MISSING' || out.checks.hmac_key === 'MISSING') out.ok = false;
  return json(out, out.ok ? 200 : 500);
}

function indexPage(env) {
  return html(`<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portier EPUB — Conscience Souveraine</title>
<style>body{margin:0;background:#0B1A2F;color:#E8E0D0;font-family:Georgia,serif;padding:2rem;line-height:1.6}
.card{max-width:640px;margin:2rem auto;background:#142844;border-top:3px solid #C9A76C;border-radius:6px;padding:2rem}
code{background:#07111f;padding:.15rem .35rem;border-radius:3px;color:#E8C887;font-family:ui-monospace,monospace}
h1{color:#E8C887;font-family:Spectral,serif}</style></head><body><div class="card">
<h1>Portier EPUB — Conscience Souveraine</h1>
<p>Ce service délivre les versions numériques (EPUB) des Vol I, II et III après paiement Stripe vérifié. Les fichiers sont stockés en accès privé (Cloudflare R2) et servis via un lien signé à durée limitée.</p>
<p><b>Endpoints</b></p>
<ul>
<li><code>GET /health</code> — état du service</li>
<li><code>POST /webhook</code> — webhook Stripe (signature vérifiée)</li>
<li><code>GET /claim?session_id=…</code> — réclamation après paiement</li>
<li><code>GET /dl/:token</code> — téléchargement (token HMAC, 24 h)</li>
<li><code>GET /thanks?token=…</code> — page de remerciement</li>
</ul>
<p style="color:#B5A989;font-size:.85rem">David Berthelotte — 2026 — Licence UBLinx Open Innovation v1.0</p>
</div></body></html>`);
}

function claimErrorPage(reason, detail) {
  const msgs = {
    missing_session: 'Session de paiement manquante.',
    stripe_lookup_failed: 'Impossible de vérifier le paiement auprès de Stripe.',
    not_paid: 'Le paiement n’est pas confirmé.',
    bad_metadata: 'Métadonnées de session invalides (volume/langue).',
    missing_token: 'Jeton de téléchargement manquant.',
    invalid_or_expired_token: 'Lien invalide ou expiré (valide 24 h).',
    bad_token_payload: 'Volume ou langue non reconnu dans le jeton.',
    epub_missing: 'Le fichier EPUB n’est pas disponible côté serveur.',
  };
  return `<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Erreur — Portier EPUB</title>
<style>body{margin:0;background:#0B1A2F;color:#E8E0D0;font-family:Georgia,serif;padding:2rem}
.card{max-width:560px;margin:2rem auto;background:#142844;border-top:3px solid #b5404a;border-radius:6px;padding:2rem}
h1{color:#E8C887;margin:0 0 .75rem;font-family:Spectral,serif;font-size:1.3rem}
pre{background:#07111f;padding:.75rem;border-radius:4px;color:#B5A989;overflow:auto;font-size:.8rem}</style>
</head><body><div class="card"><h1>Erreur de téléchargement</h1>
<p>${msgs[reason] || reason}</p>
${detail ? `<pre>${String(detail).slice(0, 500)}</pre>` : ''}
<p style="color:#B5A989;font-size:.85rem;margin-top:1.5rem">Contact : berthelotte.d@gmail.com — indiquez votre numéro de session Stripe.</p>
</div></body></html>`;
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const origin = ALLOWED_ORIGIN(env);
    const cors = corsHeaders(origin);

    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });

    try {
      if (path === '/' && request.method === 'GET') return indexPage(env);
      if (path === '/health' && request.method === 'GET') return handleHealth(env);

      if (path === '/webhook' && request.method === 'POST') {
        return await handleWebhook(request, env);
      }
      if (path === '/claim' && request.method === 'GET') {
        return await handleClaim(request, env);
      }
      if (path.startsWith('/dl/') && request.method === 'GET') {
        const token = path.slice('/dl/'.length);
        return await handleDownload(request, env, token);
      }
      if (path === '/thanks' && request.method === 'GET') {
        return await handleThanks(request, env);
      }
      return json({ error: 'not_found', path }, 404, cors);
    } catch (e) {
      console.error('worker error', e);
      return json({ error: 'internal', message: e.message }, 500, cors);
    }
  },
};