# 🕸️ Outil de Scraping

Un outil de web scraping avec interface graphique. On entre une URL, on choisit ce qu'on veut extraire, et on récupère les données en **CSV, Excel ou JSON**.

## Fonctionnalités

- **Interface web** (s'ouvre dans le navigateur, se partage facilement).
- **Deux moteurs** :
  - *Statique* (rapide) via `requests` + `BeautifulSoup` — pages HTML classiques.
  - *Dynamique* via **Playwright** (optionnel) — sites en JavaScript (React, infinite scroll…).
- **Deux modes de sélection** :
  - *Mode simple* : cases à cocher (titres, liens, images, paragraphes, listes).
  - *Mode avancé* : sélecteurs CSS personnalisés, avec extraction de blocs répétés (ex. une carte produit = une ligne).
- **Export** CSV / Excel / JSON + tableau affiché à l'écran.
- Respect optionnel du `robots.txt`.

## Installation & lancement

```bash
cd scraper-tool
chmod +x run.sh
./run.sh
```

Puis ouvre **http://127.0.0.1:8000** dans ton navigateur.

> Le premier lancement crée un environnement virtuel et installe les dépendances automatiquement.

### Installation manuelle (alternative)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd backend
uvicorn main:app --reload --port 8000
```

### Activer le mode dynamique (sites JavaScript)

```bash
source .venv/bin/activate
pip install playwright
playwright install chromium
```

Ensuite, coche « Site dynamique (JavaScript) » dans l'interface.

## Recherche TikTok OSINT (par mot-clé)

Section **[ #TIKTOK ]** de l'interface. On tape un mot-clé, l'outil cherche sur
TikTok et renvoie un tableau de comptes avec, dans une **colonne dédiée**, les
numéros de téléphone — plus une colonne « autres contacts » (Snap, Telegram,
WhatsApp, Insta, mail).

### Périmètre (important)

- **Données publiques uniquement** : pseudo, nom, bio (`signature`) et
  description des vidéos — ce que TikTok affiche sans connexion.
- Les numéros/contacts extraits sont ceux que les gens **affichent eux-mêmes**
  dans leur bio ou leurs vidéos. Le numéro privé rattaché au compte (celui de
  l'inscription) n'est **pas** accessible et n'est pas visé.
- Usage destiné à une **investigation autorisée**. Le cadre légal du traitement
  (rattachement à une enquête, autorisation, RGPD / directive police-justice)
  reste de la responsabilité de l'utilisateur, pas de l'outil.

### Comment ça marche

L'outil pilote un vrai navigateur (Playwright) sur la page de recherche TikTok
et **intercepte les réponses JSON** de l'API interne (plus robuste que de parser
le HTML). Il en tire bios + descriptions, puis extrait les contacts par motifs.

### TikTok bloque les robots : la bonne procédure

TikTok affiche souvent un **captcha** en navigateur invisible. Pour contourner :

1. Dans la section TikTok, **décoche « Navigateur invisible »**.
2. Lance la recherche : une fenêtre Chromium s'ouvre. Résous le captcha (et/ou
   connecte-toi à TikTok) **à la main**. Tu as ~2 min.
3. La session est **mémorisée** dans `.tiktok_profile/` : les recherches
   suivantes (même en invisible) passent beaucoup mieux, sans refaire le captcha.

> Le mode visible nécessite un affichage graphique. Sous WSL2, WSLg le fournit
> en général ; sinon lance l'outil depuis une session avec bureau.

### Injecter ses cookies (recommandé)

Le plus fiable pour éviter les blocages : utiliser ta **session connectée**.
Dans la section TikTok, déplie **« Mes cookies TikTok »** :

1. Connecte-toi à TikTok dans ton navigateur habituel.
2. Installe une extension type **Cookie-Editor**, ouvre-la sur `tiktok.com`,
   clique **Export** (format JSON).
3. Colle le JSON dans la zone et clique **Enregistrer mes cookies**.

L'indicateur confirme si une session de connexion (`sessionid`) est détectée.
Les cookies sont stockés en local dans `.tiktok_cookies.json` et réinjectés à
chaque recherche. Formats acceptés : JSON d'extension, ou en-tête brut
`nom=valeur; nom2=valeur2`.

### Enrichissement des numéros (« OSINT maison », propre)

Dès qu'un numéro est récupéré (recherche TikTok, analyse de post, **ou** scraping
classique avec l'option « Détecter + enrichir les numéros »), il est enrichi
hors-ligne via `phonenumbers` : **validité, pays, région, opérateur, type de
ligne** (mobile/fixe/VoIP). Un numéro bidon (ex. `07 00 00 00 00`) est signalé
« format non valide ».

Dans les résultats TikTok, si un **même numéro apparaît sur plusieurs comptes**,
c'est signalé (`⚠ aussi sur @autre`) — utile pour relier un réseau de comptes.

> Volontairement **exclu** : l'énumération de comptes (« ce numéro a-t-il un
> WhatsApp/Telegram ? » via l'abus des flux *mot de passe oublié*) et les bases
> de fuites. Techniques d'abus de service / données illégales ; en cadre
> judiciaire, ces infos s'obtiennent par **réquisition** à l'opérateur/plateforme.

### Fiches par compte (dossier imprimable → PDF)

Dans « Le butin », le bouton **Fiches (PDF)** ouvre une page imprimable : une
carte par compte/ligne (pseudo, numéro enrichi, contacts, bio, liens des posts).
Depuis cette page, **Imprimer / Enregistrer en PDF** du navigateur produit le
dossier. L'export **Excel** du tableau reste disponible à côté.

### Recherche d'un numéro dans l'historique

Tout résultat (recherche TikTok, et scraping avec enrichissement activé) est
enregistré en local dans `.osint_store.db` (SQLite). La section **[ #OSINT ]
Rechercher un numéro dans l'historique** retrouve ensuite **tous** les
comptes/posts où un numéro est apparu — les formats `06…` et `+33 6…` sont
reconnus comme le même numéro. Pratique pour relier des comptes entre plusieurs
recherches dans le temps.

### Robustesse

TikTok sert parfois une page « Une erreur est survenue / Réessayer » au premier
chargement : l'outil clique **Réessayer** / recharge automatiquement (jusqu'à
3 fois) avant d'abandonner.

### Options

| Option | Effet |
|---|---|
| Nombre de comptes max | plafonne les résultats (5 à 200) |
| Seulement les comptes avec un contact | masque ceux sans numéro/réseau détecté |
| Aller chercher la bio de chaque compte | visite chaque profil pour compléter les bios manquantes (plus lent) |
| Navigateur invisible | décoche-le pour résoudre un captcha / te connecter |

Les résultats s'affichent dans le même tableau que le scraping classique et
s'exportent en **CSV / Excel / JSON**.

> La recherche TikTok nécessite Playwright + Chromium :
> `./.venv/bin/pip install playwright && ./.venv/bin/playwright install chromium`

### Analyse d'une vidéo en profondeur (OCR + audio) — phase 2

Pour les numéros/contacts qui ne sont **pas dans le texte** (bio/description)
mais affichés à l'écran dans la vidéo ou donnés à l'oral. Déplie
**« Analyser une vidéo en profondeur »**, colle le lien d'une vidéo, coche
**OCR** (texte incrusté) et/ou **Transcription audio**, puis lance. L'outil
télécharge la vidéo (yt-dlp), lit le texte des images (Tesseract) et/ou
transcrit l'audio (Whisper), puis en extrait les contacts.

**Mode précision (EasyOCR)** : coche-le si Tesseract rate du texte stylisé
(polices fantaisie, texte sur photo). EasyOCR (deep-learning) lit bien mieux
ce type de visuel, mais c'est plus lent et ça tire PyTorch
(`./.venv/bin/pip install easyocr`, ~2 Go ; modèles téléchargés au 1er usage).
Tesseract reste le moteur par défaut.

C'est **lent** (dizaines de secondes par vidéo) et désactivé par défaut au-delà
de l'OCR. Prérequis binaires système (une seule fois) :

```bash
sudo apt install ffmpeg tesseract-ocr tesseract-ocr-fra
```

Les paquets Python (`yt-dlp`, `pillow`, `pytesseract`, `faster-whisper`) sont
dans `requirements.txt`. Le modèle Whisper se télécharge tout seul au 1er usage.

## Mode avancé : exemples de sélecteurs CSS

| Objectif | Conteneur répété | Champ / sélecteur | Récupérer |
|---|---|---|---|
| Liste de produits | `.product-card` | `.title` | Texte |
| Prix | `.product-card` | `.price` | Texte |
| Lien du produit | `.product-card` | `a` | Lien (href) |
| Image | `.product-card` | `img` | Image (src) |

Astuce : dans ton navigateur, clic droit → « Inspecter » pour trouver les classes/balises à utiliser.

## Utilisation responsable

Scraper des sites tiers peut être encadré par leurs conditions d'utilisation et par la loi (droit d'auteur, données personnelles/RGPD). N'utilise cet outil que sur des sites que tu es **autorisé** à extraire, respecte le `robots.txt` et évite de surcharger les serveurs.

## Structure

```
scraper-tool/
├── backend/
│   ├── main.py      # API FastAPI + service de la page
│   ├── scraper.py   # moteur de scraping générique (fetch + extraction)
│   └── tiktok.py    # recherche TikTok OSINT + extraction de contacts publics
├── frontend/
│   └── index.html   # interface web (HTML/CSS/JS autonome)
├── requirements.txt
├── run.sh
└── README.md
```
