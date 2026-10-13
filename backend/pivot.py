"""Pivot pseudo (style Sherlock/Maigret), version propre.

Vérifie l'existence d'un pseudo sur d'autres plateformes en consultant
UNIQUEMENT des pages publiques de profil (une requête HTTP GET, pas d'abus de
flux « mot de passe oublié », pas de connexion). À utiliser avec mesure et dans
un cadre autorisé — c'est de la reconnaissance active sur des services tiers.

Chaque site est vérifié par une règle explicite (statut HTTP ou présence/absence
d'un marqueur). Certaines plateformes (Instagram, X/Twitter, Facebook) bloquent
les requêtes sans connexion : on ne les tente pas plutôt que de donner un faux
résultat.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# Liste VOLONTAIREMENT restreinte aux plateformes qui distinguent fiablement
# un compte existant d'un compte inexistant via une simple requête publique.
# (Instagram, X/Twitter, Facebook, Pinterest, YouTube... renvoient 200 même pour
#  un pseudo bidon ou exigent une connexion -> écartés pour éviter les faux positifs.)
# type: "status"  -> existe si 200, absent si 404
#       "exists"   -> existe si le marqueur est présent (sinon absent)
_SITES = [
    {"name": "GitHub", "url": "https://github.com/{u}", "type": "status"},
    {"name": "Telegram", "url": "https://t.me/{u}", "type": "exists", "marker": "tgme_page_title"},
    {"name": "Snapchat", "url": "https://www.snapchat.com/add/{u}", "type": "status"},
    {"name": "Linktree", "url": "https://linktr.ee/{u}", "type": "status"},
    {"name": "Cash App", "url": "https://cash.app/${u}", "type": "status"},
    {"name": "GitLab", "url": "https://gitlab.com/{u}", "type": "status"},
]

_VALID_USER = re.compile(r"^[A-Za-z0-9._\-]{2,32}$")


def _check(site: dict, user: str) -> dict:
    url = site["url"].format(u=user)
    res = {"site": site["name"], "url": url, "etat": "incertain"}
    try:
        r = requests.get(url, headers=_HEADERS, timeout=8, allow_redirects=True)
        if site["type"] == "status":
            res["etat"] = "existe" if r.status_code == 200 else ("absent" if r.status_code == 404 else "incertain")
        elif site["type"] == "exists":
            res["etat"] = "existe" if site["marker"].lower() in r.text.lower() else "absent"
        elif site["type"] == "absent":
            res["etat"] = "absent" if site["marker"].lower() in r.text.lower() else "existe"
    except Exception:  # noqa: BLE001
        res["etat"] = "erreur"
    return res


def pivot_username(user: str) -> dict:
    """Vérifie le pseudo sur toutes les plateformes (en parallèle)."""
    user = (user or "").strip().lstrip("@")
    if not _VALID_USER.match(user):
        return {"user": user, "error": "Pseudo invalide (2-32 caractères, lettres/chiffres/._-)."}

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_check, s, user) for s in _SITES]
        for f in as_completed(futs):
            results.append(f.result())

    # d'abord les comptes trouvés
    order = {"existe": 0, "incertain": 1, "erreur": 2, "absent": 3}
    results.sort(key=lambda r: (order.get(r["etat"], 9), r["site"]))
    return {"user": user, "results": results}
