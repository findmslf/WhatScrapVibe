"""Comparaison de photos de profil par empreinte perceptuelle (pHash).

Télécharge les avatars publics et compare leurs empreintes : deux comptes avec
la même photo (ou une variante proche) = très probablement la même personne.
Travaille sur des images publiques déjà référencées dans les résultats.
"""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.tiktok.com/",
}


def _phash(url: str):
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        return None
    try:
        r = requests.get(url, headers=_HEADERS, timeout=10)
        if not r.ok:
            return None
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        return imagehash.phash(im)
    except Exception:  # noqa: BLE001
        return None


def compare_avatars(items: list[dict], max_distance: int = 8) -> dict:
    """items = [{compte, avatar_url, url?}]. Regroupe les photos quasi identiques.

    max_distance : distance de Hamming max entre empreintes (0 = identique ;
    <=8 sur 64 bits = très proche).
    """
    try:
        import imagehash  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        from scraper import ScrapeError
        raise ScrapeError("Comparaison indisponible : ./.venv/bin/pip install imagehash pillow") from exc

    cand = [it for it in (items or []) if it.get("avatar_url")]

    def work(it):
        return (it, _phash(it["avatar_url"]))

    with ThreadPoolExecutor(max_workers=8) as ex:
        hashed = [(it, h) for it, h in ex.map(work, cand) if h is not None]

    groups, used = [], set()
    for i in range(len(hashed)):
        if i in used:
            continue
        it1, h1 = hashed[i]
        grp = [it1]
        used.add(i)
        for j in range(i + 1, len(hashed)):
            if j in used:
                continue
            it2, h2 = hashed[j]
            if (h1 - h2) <= max_distance:            # distance de Hamming
                grp.append(it2)
                used.add(j)
        if len(grp) > 1:
            groups.append({
                "comptes": [{"compte": g.get("compte", ""), "url": g.get("url", "")} for g in grp],
                "taille": len(grp),
            })

    groups.sort(key=lambda g: g["taille"], reverse=True)
    return {"analysees": len(hashed), "demandees": len(cand), "groupes": groups}
