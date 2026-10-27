# Alerte Goethe B2 (Goethe-Institut Marokko)

Robot GitHub Actions qui lit la page
https://www.goethe.de/ins/ma/fr/spr/prf/gzb2.cfm (onglet **Adultes**)
et envoie un e-mail des qu'une date n'est plus « Délai de réservation expiré ».
Il ne réserve jamais rien.

- Lancement automatique toutes les 5 minutes ; chaque exécution vérifie la page chaque minute pendant 20 minutes.
- Un seul e-mail par changement (l'état est gardé dans `state.json`).
- Secrets à créer dans *Settings → Secrets and variables → Actions* :
  - `GMAIL_USER` : votre adresse Gmail
  - `GMAIL_APP_PASSWORD` : un mot de passe d'application Google (16 lettres)
- Test : onglet *Actions* → *Alerte Goethe B2* → *Run workflow* → cocher « Envoyer seulement un e-mail de test ».
