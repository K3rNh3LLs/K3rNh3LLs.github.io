/**
 * download-counter.js — Compteurs de téléchargements via CountAPI.
 * Aucun traqueur publicitaire, aucun cookie tiers.
 */

(function () {
  'use strict';

  const NAMESPACE = 'davidberthelotte.ca';

  function trackDownload(key) {
    fetch('https://api.countapi.xyz/hit/' + NAMESPACE + '/' + key)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        console.log(
          '%c[Download]%c ' + key + ' : %c' + data.value,
          'color:#C9A76C; font-weight:bold;',
          'color:#B5A989;',
          'color:#E8C887; font-weight:bold;'
        );
      })
      .catch(function () {});
  }

  function getDownloadCount(key, callback) {
    fetch('https://api.countapi.xyz/get/' + NAMESPACE + '/' + key)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        callback(data.value || 0);
      })
      .catch(function () {
        callback(0);
      });
  }

  // Exposer globalement pour les onclick
  window.trackDownload = trackDownload;
  window.getDownloadCount = getDownloadCount;

  // Affichage des compteurs sur la page tome-4
  document.addEventListener('DOMContentLoaded', function () {
    var keys = [
      { key: 'tome4-fr-pdf', id: 'count-tome4-fr-pdf' },
      { key: 'tome4-en-pdf', id: 'count-tome4-en-pdf' },
      { key: 'tome4-fr-epub', id: 'count-tome4-fr-epub' },
      { key: 'tome4-en-epub', id: 'count-tome4-en-epub' }
    ];

    keys.forEach(function (item) {
      var el = document.getElementById(item.id);
      if (!el) return;
      getDownloadCount(item.key, function (count) {
        var lang = 'en';
        try {
          lang = localStorage.getItem('lang') || navigator.language.slice(0, 2) || 'en';
        } catch (e) {}
        var labels = {
          fr: 'Téléchargements',
          en: 'Downloads',
          es: 'Descargas'
        };
        var label = labels[lang] || labels['en'];
        el.textContent = label + ': ' + count;
      });
    });
  });
})();
