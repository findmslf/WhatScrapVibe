"""Moteur de scraping : récupération HTML (statique ou dynamique) + extraction.

- Statique  : requests
- Dynamique : Playwright (optionnel, chargé seulement si demandé/installé)

L'extraction renvoie toujours une liste de lignes (list[dict]) pour un export
homogène en CSV / Excel / JSON.
"""
from __future__ import annotations

import re
import time
import urllib.robotparser
from itertools import zip_longest
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ScraperTool/1.0; +interface graphique)"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


class ScrapeError(Exception):
    """Erreur « propre » renvoyée à l'interface."""


# --------------------------------------------------------------------------- #
# Récupération du HTML
# --------------------------------------------------------------------------- #
def fetch_static(url: str, timeout: int = 20) -> str:
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError(f"Impossible de récupérer la page : {exc}") from exc
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def fetch_dynamic(url: str, timeout: int = 45, wait_selector: str | None = None) -> str:
    """Rendu via navigateur headless (Playwright). Optionnel.

    On attend `domcontentloaded` (fiable) plutôt que `networkidle` : beaucoup de
    sites gardent des connexions ouvertes (pubs, sockets, sondage) et n'atteignent
    jamais le silence réseau, ce qui provoquait un timeout.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScrapeError(
            "Le mode dynamique (JavaScript) nécessite Playwright. "
            "Installe-le avec : ./.venv/bin/pip install playwright "
            "&& ./.venv/bin/playwright install chromium"
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"])
            page.set_default_timeout(timeout * 1000)
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")

            if wait_selector:
                # On attend l'élément demandé ; s'il n'apparaît pas, on prend
                # quand même le HTML tel quel plutôt que d'échouer.
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout * 1000)
                except PWTimeout:
                    pass
            else:
                # Laisse le JS s'exécuter : silence réseau au mieux, sinon pause fixe.
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except PWTimeout:
                    pass
                page.wait_for_timeout(1200)

            html = page.content()
            browser.close()
            return html
    except Exception as exc:  # noqa: BLE001 - on remonte un message lisible
        raise ScrapeError(f"Erreur lors du rendu dynamique : {exc}") from exc


# --------------------------------------------------------------------------- #
# robots.txt (optionnel mais recommandé)
# --------------------------------------------------------------------------- #
def robots_allows(url: str) -> bool:
    """True si robots.txt autorise (ou est introuvable/illisible)."""
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(DEFAULT_HEADERS["User-Agent"], url)
    except Exception:  # noqa: BLE001 - en cas de doute, on n'empêche pas
        return True


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def _node_value(node, attr: str, base_url: str) -> str:
    """Récupère le texte ou un attribut d'un élément."""
    if attr in ("", "text"):
        return node.get_text(strip=True)
    if attr == "html":
        return node.decode_contents().strip()
    value = node.get(attr, "")
    # Rend les liens/images absolus
    if attr in ("href", "src") and value:
        value = urljoin(base_url, value)
    return value


SIMPLE_FIELDS = {
    "titres": ("h1, h2, h3", "text"),
    "liens": ("a[href]", "href"),
    "textes_liens": ("a[href]", "text"),
    "images": ("img[src]", "src"),
    "paragraphes": ("p", "text"),
    "listes": ("li", "text"),
}


def extract_simple(soup: BeautifulSoup, fields: list[str], base_url: str) -> list[dict]:
    """Mode simple : une colonne par type de champ coché, lignes alignées."""
    columns: dict[str, list[str]] = {}
    for field in fields:
        if field not in SIMPLE_FIELDS:
            continue
        selector, attr = SIMPLE_FIELDS[field]
        values = [_node_value(n, attr, base_url) for n in soup.select(selector)]
        columns[field] = [v for v in values if v]

    if not columns:
        return []

    rows = []
    for combo in zip_longest(*columns.values(), fillvalue=""):
        rows.append(dict(zip(columns.keys(), combo)))
    return rows


def extract_advanced(
    soup: BeautifulSoup,
    fields: list[dict],
    base_url: str,
    container: str | None = None,
) -> list[dict]:
    """Mode avancé : sélecteurs CSS personnalisés.

    fields : [{"name": "titre", "selector": ".title", "attr": "text"}, ...]
    container : sélecteur d'un bloc répété (ex: carte produit). Si fourni,
    chaque bloc devient une ligne et les sélecteurs sont relatifs au bloc.
    """
    clean_fields = [f for f in fields if f.get("selector")]
    if not clean_fields:
        raise ScrapeError("Aucun sélecteur CSS valide fourni.")

    rows: list[dict] = []

    if container:
        blocks = soup.select(container)
        if not blocks:
            raise ScrapeError(f"Aucun élément trouvé pour le conteneur « {container} ».")
        for block in blocks:
            row = {}
            for f in clean_fields:
                name = f.get("name") or f["selector"]
                node = block.select_one(f["selector"])
                row[name] = _node_value(node, f.get("attr", "text"), base_url) if node else ""
            rows.append(row)
    else:
        # Chaque champ est extrait globalement puis les colonnes sont alignées.
        columns: dict[str, list[str]] = {}
        for f in clean_fields:
            name = f.get("name") or f["selector"]
            nodes = soup.select(f["selector"])
            columns[name] = [_node_value(n, f.get("attr", "text"), base_url) for n in nodes]
        for combo in zip_longest(*columns.values(), fillvalue=""):
            rows.append(dict(zip(columns.keys(), combo)))

    return rows


# --------------------------------------------------------------------------- #
# Point d'entrée
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Détecteur de page anti-bot / captcha (Cloudflare & co)
# --------------------------------------------------------------------------- #
_ANTIBOT_MARKERS = [
    "just a moment",
    "checking your browser before accessing",
    "performing security verification",
    "verification successful",
    "verifying you are human",
    "please enable javascript and cookies",
    "enable cookies to continue",
    "/cdn-cgi/challenge-platform/",
    "challenges.cloudflare.com/turnstile",
    "cf-browser-verification",
    "cf_chl_opt",
    "cf-chl-",
    "_cf_chl",
    "ddos protection by",
]


def _antibot_reason(html: str, soup) -> str | None:
    """Renvoie un message si la page reçue est un écran de challenge, sinon None."""
    low = (html or "").lower()
    hit = any(mark in low for mark in _ANTIBOT_MARKERS)
    # "attention required" + cloudflare = page de blocage Cloudflare classique
    if not hit and "attention required" in low and "cloudflare" in low:
        hit = True
    if not hit:
        return None

    if "cloudflare" in low or "cdn-cgi" in low or "turnstile" in low:
        vendor = "Cloudflare"
    elif "hcaptcha" in low:
        vendor = "hCaptcha"
    elif "recaptcha" in low or "g-recaptcha" in low:
        vendor = "reCAPTCHA"
    else:
        vendor = "un anti-bot"
    return (
        f"Ce site est protégé par {vendor} : le vrai contenu ne s'est pas chargé, "
        "on a seulement reçu la page de vérification. Impossible de scraper cette page. "
        "Ce site bloque volontairement les robots."
    )


# mots/indices qui désignent un lien « page suivante »
_NEXT_TEXTS = ("suivant", "suivante", "next", "older", "page suivante", "»", "›", "→", ">>")
_NEXT_SELECTORS = (
    'a[rel="next"]', "a.next", "li.next a", ".next > a", ".pagination-next a",
    "a.pagination__next", "a.page-link[rel=next]", ".next a",
)


def _find_next_url(soup, base_url: str, next_selector: str | None):
    """Trouve l'URL de la page suivante (sélecteur fourni, sinon auto-détection)."""
    # 1) sélecteur explicite
    if next_selector:
        el = soup.select_one(next_selector)
        if el:
            href = el.get("href") or (el.find_parent("a") or {}).get("href")
            if href:
                return urljoin(base_url, href)
        return None

    # 2) <link rel="next"> dans le head
    link = soup.find("link", rel=lambda v: v and "next" in (v if isinstance(v, str) else " ".join(v)).lower())
    if link and link.get("href"):
        return urljoin(base_url, link["href"])

    # 3) sélecteurs classiques
    for sel in _NEXT_SELECTORS:
        el = soup.select_one(sel)
        if el and el.get("href"):
            return urljoin(base_url, el["href"])

    # 4) un <a> dont le texte ou l'aria-label ressemble à « suivant »
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ", strip=True) + " " + (a.get("aria-label") or "")).lower()
        rel = a.get("rel") or []
        if "next" in (rel if isinstance(rel, list) else [rel]):
            return urljoin(base_url, a["href"])
        if label and any(t == label.strip() or label.strip().startswith(t) for t in _NEXT_TEXTS):
            return urljoin(base_url, a["href"])
    return None


def _normalize_price(val: str) -> str:
    """Extrait un nombre propre d'un prix : '£1,234.56' -> '1234.56', '1 234,56 €' -> '1234.56'."""
    m = re.search(r"\d[\d ., ]*\d|\d", val)
    if not m:
        return val
    num = m.group(0).replace(" ", "").replace(" ", "")
    if "," in num and "." in num:
        # le dernier séparateur est le décimal
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        # virgule seule : décimale si 1-2 chiffres derrière, sinon séparateur de milliers
        num = num.replace(",", ".") if re.search(r",\d{1,2}$", num) else num.replace(",", "")
    return num


def _clean_rows(rows: list[dict], payload: dict) -> list[dict]:
    clean_text = payload.get("clean_text", True)
    price_num = payload.get("price_to_number", False)
    drop_empty = payload.get("drop_empty", True)
    dedupe = payload.get("dedupe", True)

    out: list[dict] = []
    for row in rows:
        new = {}
        for k, v in row.items():
            s = "" if v is None else str(v)
            if clean_text:
                s = re.sub(r"\s+", " ", s).strip()
            if price_num and _has_price(s):
                s = _normalize_price(s)
            new[k] = s
        out.append(new)

    if drop_empty:
        out = [r for r in out if any(v != "" for v in r.values())]

    if dedupe:
        seen = set()
        uniq = []
        for r in out:
            key = tuple(sorted(r.items()))
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        out = uniq

    return out


def _fetch_soup(url: str, dynamic: bool, wait_selector: str | None):
    html = fetch_dynamic(url, wait_selector=wait_selector) if dynamic else fetch_static(url)
    return html, BeautifulSoup(html, "lxml")


def _extract_rows(soup, payload: dict, base_url: str) -> list[dict]:
    if payload.get("mode", "simple") == "advanced":
        return extract_advanced(
            soup,
            payload.get("fields", []),
            base_url=base_url,
            container=(payload.get("container") or "").strip() or None,
        )
    return extract_simple(soup, payload.get("simple_fields", []), base_url=base_url)


def run_scrape(payload: dict) -> dict:
    url = (payload.get("url") or "").strip()
    if not url:
        raise ScrapeError("L'URL est obligatoire.")
    if not urlparse(url).scheme:
        url = "https://" + url

    respect = payload.get("respect_robots", True)
    if respect and not robots_allows(url):
        raise ScrapeError(
            "Le fichier robots.txt de ce site interdit le scraping de cette page. "
            "Désactive l'option « Respecter robots.txt » si tu es autorisé à continuer."
        )

    dynamic = bool(payload.get("dynamic"))
    wait_selector = payload.get("wait_selector") or None

    paginate = bool(payload.get("paginate"))
    if not paginate:
        html, soup = _fetch_soup(url, dynamic, wait_selector)
        blocked = _antibot_reason(html, soup)
        if blocked:
            raise ScrapeError(blocked)
        rows = _clean_rows(_extract_rows(soup, payload, url), payload)
        return {
            "url": url, "count": len(rows), "pages": 1,
            "columns": list(rows[0].keys()) if rows else [], "rows": rows,
        }

    # --- Pagination : on suit les pages suivantes ---
    try:
        max_pages = int(payload.get("max_pages") or 5)
    except (TypeError, ValueError):
        max_pages = 5
    max_pages = max(1, min(max_pages, 50))
    next_selector = (payload.get("next_selector") or "").strip() or None
    delay = 1.0  # pause polie entre les pages

    all_rows: list[dict] = []
    visited: set[str] = set()
    cur = url
    pages = 0

    while cur and pages < max_pages and cur not in visited:
        visited.add(cur)
        if respect and not robots_allows(cur):
            break
        html, soup = _fetch_soup(cur, dynamic, wait_selector)
        blocked = _antibot_reason(html, soup)
        if blocked:
            if pages == 0:
                raise ScrapeError(blocked)
            break  # page intermédiaire bloquée : on garde ce qu'on a
        all_rows.extend(_extract_rows(soup, payload, cur))
        pages += 1

        nxt = _find_next_url(soup, cur, next_selector)
        if not nxt or nxt in visited or pages >= max_pages:
            break
        cur = nxt
        time.sleep(delay)

    all_rows = _clean_rows(all_rows, payload)
    return {
        "url": url, "count": len(all_rows), "pages": pages,
        "columns": list(all_rows[0].keys()) if all_rows else [], "rows": all_rows,
    }


# --------------------------------------------------------------------------- #
# Auto-parser : Trombonne analyse la page et propose conteneur + champs
# --------------------------------------------------------------------------- #
# prix : accepte "£51.77" (devise avant) ET "51,77 €" (devise après)
PRICE_RE = re.compile(
    r"(?:€|\$|£)\s?\d[\d.,\s ]*"            # devise avant : £51.77
    r"|\d[\d.,\s ]*\s?(?:€|£|\$)"            # devise apres (symbole) : 19,90 €
    r"|\d[\d.,\s ]*\s?(?:eur|usd|chf)\b",    # devise apres (lettres) : 19.90 EUR
    re.I,
)
_GENERIC_CLASSES = {"row", "col", "container", "wrapper", "item", "list", "clearfix", "active"}


def _has_price(txt: str) -> bool:
    return bool(txt and PRICE_RE.search(txt))


def _sig(el) -> str:
    classes = el.get("class") or []
    return el.name + ("." + ".".join(sorted(classes)) if classes else "")


def _useful_classes(el):
    return [c for c in (el.get("class") or []) if len(c) > 1 and c.lower() not in _GENERIC_CLASSES]


def _rel_selector(el) -> str:
    """Sélecteur simple et relatif pour un élément dans un bloc."""
    useful = _useful_classes(el)
    if useful:
        return "." + useful[0]
    return el.name


def _richness(el) -> float:
    """Note la richesse « carte de contenu » (pour distinguer d'une nav de liens)."""
    score = 0.0
    if el.find(["h1", "h2", "h3", "h4", "h5", "h6"]):
        score += 2
    if el.find("img"):
        score += 1.5
    txt = el.get_text(" ", strip=True)
    if _has_price(txt):
        score += 2
    if el.find("a", href=True):
        score += 1
    if len(txt) > 20:
        score += 1
    return score


def _scan_groups(soup):
    """Liste les groupes d'éléments frères répétés (>=3), triés par richesse x nombre.

    On pondère fort la richesse moyenne (carré) pour qu'un menu de nav (plein de
    liens mais pauvre) ne batte pas une grille de cartes produits.
    """
    cands = []
    for parent in soup.find_all(True):
        groups: dict[str, list] = {}
        for child in parent.find_all(recursive=False):
            if getattr(child, "name", None):
                groups.setdefault(_sig(child), []).append(child)
        for sig, items in groups.items():
            if len(items) < 3:
                continue
            avg = sum(_richness(i) for i in items) / len(items)
            if avg < 1:
                continue
            cands.append({
                "sig": sig, "count": len(items), "avg": avg,
                "score": len(items) * (avg ** 2), "item": items[0],
            })
    cands.sort(key=lambda c: c["score"], reverse=True)
    return cands


def _refine_container(item):
    """Si le bloc n'a pas de classe utile, descend vers un unique enfant distinctif."""
    if _useful_classes(item):
        return item
    child_els = [c for c in item.find_all(recursive=False) if getattr(c, "name", None)]
    if len(child_els) == 1 and _useful_classes(child_els[0]):
        return child_els[0]
    return item


def _container_selector(items) -> str:
    el = items[0]
    useful = _useful_classes(el)
    if useful:
        return el.name + "." + ".".join(useful[:2])
    parent = el.parent
    if parent is not None and getattr(parent, "name", None):
        pid = parent.get("id")
        if pid:
            return f"#{pid} > {el.name}"
        pcl = _useful_classes(parent)
        if pcl:
            return f".{pcl[0]} > {el.name}"
    return el.name


def _detect_fields(item, steps=None) -> list[dict]:
    fields: list[dict] = []
    seen = set()

    def log(msg):
        if steps is not None:
            steps.append(msg)

    def add(name, sel, attr):
        if sel and (sel, attr) not in seen:
            fields.append({"name": name, "selector": sel, "attr": attr})
            seen.add((sel, attr))

    h = item.find(["h1", "h2", "h3", "h4", "h5"])
    if h:
        add("titre", _rel_selector(h), "text")
        log(f'   un titre ? oui -> "{_rel_selector(h)}"')
    else:
        log("   un titre ? aucun h1-h5 dans le bloc")

    if item.find("a", href=True):
        add("lien", "a", "href")
        log('   un lien ? oui -> "a" (href)')
    else:
        log("   un lien ? aucun")

    img = item.find("img")
    if img and (img.get("src") or img.get("data-src") or img.get("srcset")):
        add("image", "img", "src")
        log('   une image ? oui -> "img" (src)')
    else:
        log("   une image ? aucune")

    # prix : petit texte qui ressemble à un montant
    price_found = False
    for el in item.find_all(True):
        if el.find(True):  # on veut une feuille
            continue
        txt = el.get_text(" ", strip=True)
        if txt and len(txt) <= 40 and _has_price(txt):
            add("prix", _rel_selector(el), "text")
            log(f'   un prix ? oui -> "{_rel_selector(el)}" ({txt})')
            price_found = True
            break
    if not price_found:
        log("   un prix ? aucun montant detecte")

    # pas de titre trouvé -> on prend le texte le plus long
    if not any(f["name"] == "titre" for f in fields):
        best_txt = None
        for el in item.find_all(True):
            if el.find(True):
                continue
            t = el.get_text(strip=True)
            if t and (best_txt is None or len(t) > len(best_txt[1])):
                best_txt = (el, t)
        if best_txt:
            add("texte", _rel_selector(best_txt[0]), "text")
            log(f'   pas de titre -> je prends le texte le plus long -> "{_rel_selector(best_txt[0])}"')

    return fields[:6]


def run_analyze(payload: dict) -> dict:
    url = (payload.get("url") or "").strip()
    if not url:
        raise ScrapeError("L'URL est obligatoire.")
    if not urlparse(url).scheme:
        url = "https://" + url

    if payload.get("respect_robots", True) and not robots_allows(url):
        raise ScrapeError(
            "robots.txt interdit cette page. Désactive « Respecter robots.txt » si tu es autorisé."
        )

    steps = []
    dyn = bool(payload.get("dynamic"))
    steps.append(
        "Je telecharge la page ("
        + ("navigateur JS, ca prend un peu plus de temps..." if dyn else "mode statique rapide...")
        + ")"
    )
    if dyn:
        html = fetch_dynamic(url, wait_selector=payload.get("wait_selector") or None)
    else:
        html = fetch_static(url)

    soup = BeautifulSoup(html, "lxml")

    blocked = _antibot_reason(html, soup)
    if blocked:
        raise ScrapeError(blocked)

    total = len(soup.find_all(True))
    steps.append(f"Page recue. Je lis le HTML : {total} balises a analyser.")
    steps.append("Je cherche les groupes d'elements freres qui se repetent (au moins 3 identiques)...")

    cands = _scan_groups(soup)

    if cands:
        steps.append(f"Trouve : {len(cands)} groupe(s) repete(s). Mes meilleurs candidats :")
        for c in cands[:3]:
            steps.append(f"   - {c['sig']}  ->  {c['count']} blocs, richesse {c['avg']:.1f}/7.5")
        best = cands[0]
        item = _refine_container(best["item"])
        container = _container_selector([item])
        steps.append(f'Le plus riche gagne : "{container}" ({best["count"]} blocs). 1 bloc = 1 ligne.')
        steps.append("J'ouvre un bloc et je cherche les colonnes utiles :")
        fields = _detect_fields(item, steps) or [{"name": "texte", "selector": item.name, "attr": "text"}]
        steps.append(f"Fini ! Je remplis le conteneur + {len(fields)} colonne(s). Verifie et lance :)")
        return {
            "url": url, "container": container, "fields": fields,
            "count": best["count"], "mode": "container", "steps": steps,
        }

    # Pas de bloc répété clair -> suggestions globales (sans conteneur)
    steps.append("Aucun bloc clairement repete. Je passe en suggestions globales (toute la page) :")
    fields = []
    if soup.select("h1, h2, h3"):
        fields.append({"name": "titre", "selector": "h1, h2, h3", "attr": "text"})
        steps.append("   des titres (h1-h3) ? oui")
    if soup.select("a[href]"):
        fields.append({"name": "lien", "selector": "a[href]", "attr": "href"})
        steps.append("   des liens ? oui")
    if soup.select("img[src]"):
        fields.append({"name": "image", "selector": "img[src]", "attr": "src"})
        steps.append("   des images ? oui")
    if not fields:
        fields = [{"name": "texte", "selector": "p", "attr": "text"}]
        steps.append("   rien de flagrant -> je propose les paragraphes")
    steps.append("Fini ! Verifie les cases et lance.")
    return {"url": url, "container": "", "fields": fields, "count": 0, "mode": "global", "steps": steps}
