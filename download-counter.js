/**
 * download-counter.js — Suivi anonyme des téléchargements via CounterAPI.dev.
 * Les statistiques sont invisibles et uniquement logguées en console pour l'administrateur.
 */

(function () {
  'use strict';

  const NAMESPACE = 'davidberthelotte.ca';

  /**
   * Incrémente le compteur de téléchargement pour une clé donnée.
   * Appelé par le onclick des liens de téléchargement.
   */
  function trackDownload(key) {
    // Utilisation de l'API sécurisée HTTPS pour éviter les avertissements mixed-content
    fetch('https://api.counterapi.dev/v1/' + NAMESPACE + '/' + key + '/up')
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data && typeof data.count === 'number') {
          console.log(
            '%c[Download]%c ' + key + ' : %c' + data.count,
            'color:#C9A76C; font-weight:bold;',
            'color:#B5A989;',
            'color:#E8C887; font-weight:bold;'
          );
        }
      })
      .catch(function () {
        // Échec silencieux si l'API est indisponible
      });
  }

  // Exposer globalement pour les onclick dans le HTML
  window.trackDownload = trackDownload;

})();
