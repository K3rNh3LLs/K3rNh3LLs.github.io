/**
 * counter.js — Compteurs de visites invisibles, visibles uniquement dans la console.
 * Aucun traqueur publicitaire, aucun cookie tiers, aucun élément visuel sur la page.
 */

(function () {
  'use strict';

  const NAMESPACE = 'davidberthelotte.ca';
  const KEY = 'visits';

  // 1. Compteur local (visites du navigateur actuel)
  let localVisits = 0;
  try {
    localVisits = parseInt(localStorage.getItem('local_visits') || '0', 10);
    if (isNaN(localVisits)) localVisits = 0;
    localVisits += 1;
    localStorage.setItem('local_visits', String(localVisits));
  } catch (e) {
    // localStorage indisponible (mode privé, etc.)
  }

  // 2. Compteur global (total anonyme via CountAPI)
  // CountAPI est un service gratuit et anonyme qui stocke uniquement un nombre.
  // Aucune donnée personnelle n'est collectée.
  function logGlobalCount() {
    fetch(`https://api.countapi.xyz/hit/${NAMESPACE}/${KEY}`)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data && typeof data.value === 'number') {
          console.log(
            '%c[Compteur]%c Visites globales : %c' + data.value,
            'color:#C9A76C; font-weight:bold;',
            'color:#B5A989;',
            'color:#E8C887; font-weight:bold;'
          );
        }
      })
      .catch(function () {
        // Service indisponible — silence total, aucune erreur visible
      });
  }

  // 3. Affichage console
  console.log(
    '%c[Compteur]%c Visites sur ce navigateur : %c' + localVisits,
    'color:#C9A76C; font-weight:bold;',
    'color:#B5A989;',
    'color:#E8C887; font-weight:bold;'
  );

  logGlobalCount();
})();
