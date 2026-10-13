# 📒 Mémo de reprise — WhatScrapVibe

Doc pour reprendre le projet sur une autre machine (ou après une pause).
Dépôt : `https://github.com/findmslf/WhatScrapVibe` (privé).

## C'est quoi
Outil d'investigation OSINT sur **données publiques** TikTok (cadre : forces de
l'ordre autorisées). Recherche par mot-clé → tableau de comptes → extraction des
contacts publics (numéro, Snap, Telegram, Insta, mail) depuis les bios et les
vidéos (texte + OCR + audio) → enrichissement + liaison de comptes.
Interface rétro « Scrap-O-Rap » (FastAPI + Playwright + page HTML autonome).

## Ce qui est déjà fait
- **Recherche TikTok** par mot-clé (interception des réponses JSON de l'API).
- **Cookies TikTok** injectables (bouton « Mes cookies ») → moins de blocages.
- **Analyse de post** (vidéo ou photo) : OCR du texte incrusté (Tesseract ou
  EasyOCR « mode précision »), transcription audio (Whisper). Nécessite navigateur
  visible pour passer le captcha slider la 1re fois.
- **Extraction + enrichissement des numéros** (pays/opérateur/type via
  `phonenumbers`), tolérante aux erreurs d'OCR.
- **Historique** local SQLite + recherche d'un numéro dans tout l'historique.
- **Liaison de comptes** : corrélation (identifiants partagés), @mentions+liens,
  pivot pseudo (pages publiques), comparaison de photos de profil (pHash).
- **Export** CSV / Excel / JSON + **fiches PDF** (dossier par compte).
- Scraping web générique (mode d'origine) toujours là.

## Installation sur une nouvelle machine
```bash
git clone https://github.com/findmslf/WhatScrapVibe.git
cd WhatScrapVibe
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/playwright install chromium
sudo apt install -y ffmpeg tesseract-ocr tesseract-ocr-fra   # OCR + audio
# EasyOCR (mode précision, optionnel, gros) : ./.venv/bin/pip install easyocr
./run.sh    # puis http://127.0.0.1:8000
```
Environnement de dev d'origine : WSL2 (WSLg fournit l'affichage pour le
navigateur visible). Linux avec bureau sinon.

## À refaire à la main (volontairement PAS dans le dépôt)
- **Re-saisir les cookies TikTok** (bouton « Mes cookies ») : ils ne sont jamais
  commités (fichier `.tiktok_cookies.json` ignoré par git).
- La base d'historique `.osint_store.db` repart vide (données perso, jamais commitée).

## Points d'attention
- **Fichiers sensibles** (dans `.gitignore`, à NE JAMAIS pousser) :
  `.tiktok_cookies.json`, `.tiktok_profile/`, `.osint_store.db`, `.venv/`.
- **Captcha TikTok** : pages de post protégées par un slider. Décocher
  « Navigateur invisible » pour le résoudre à la main ; le profil retient ensuite.
- **Ligne rouge maintenue** : PAS d'énumération de comptes via abus des flux
  « mot de passe oublié » (type Epieos/OSINT Industries), PAS de bases de fuites.
  Uniquement de la donnée publique / des pages publiques.

## Idées / pistes d'amélioration (non faites)
- Tester toute la chaîne OCR→contact sur de vrais posts de dealers.
- Fiabiliser le pivot (plus de plateformes fiables, markers de contenu).
- Option fournisseur tiers (Apify) pour la recherche si le scraping maison casse.
- Graphe visuel des comptes liés.

## Reprendre avec Claude Code
La conversation d'origine est locale (ne se transfère pas). Sur la nouvelle
machine : ouvrir ce dossier avec Claude Code et dire « lis HANDOFF.md » — tout
le contexte du projet est là.
