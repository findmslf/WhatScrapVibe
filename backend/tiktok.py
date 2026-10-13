"""Module TikTok : recherche par mot-clé + extraction de contacts PUBLICS.

Périmètre (volontairement limité) :
- On ne récupère QUE ce que TikTok affiche publiquement, sans connexion :
  le pseudo, le nom, la bio (`signature`) et la description des vidéos.
- Les numéros extraits sont ceux que les gens AFFICHENT eux-mêmes dans leur
  bio ou dans la description de leurs vidéos. Le numéro privé rattaché à un
  compte (celui de l'inscription) N'EST PAS accessible et n'est pas visé ici.

Destiné à de la veille / investigation OSINT sur données publiques. Le cadre
légal du traitement (enquête, autorisation) relève de l'utilisateur, pas de
l'outil.

Technique : on pilote un vrai navigateur (Playwright) sur la page de recherche
et on intercepte les réponses JSON de l'API interne de TikTok. C'est bien plus
robuste que de parser le HTML (qui change tout le temps), et ces réponses
contiennent déjà la bio et la description.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

# Profil de navigateur persistant : les cookies/session y sont gardés entre les
# recherches. Résous le captcha (ou connecte-toi) UNE fois en mode visible, et
# les recherches suivantes passent beaucoup mieux.
_PROFILE_DIR = Path(__file__).resolve().parent.parent / ".tiktok_profile"

# Cookies injectés par l'utilisateur (export de son navigateur connecté).
_COOKIES_FILE = Path(__file__).resolve().parent.parent / ".tiktok_cookies.json"

_SAMESITE = {
    "no_restriction": "None", "none": "None", "unspecified": "Lax",
    "lax": "Lax", "strict": "Strict",
}


def _normalize_cookie(c: dict) -> dict | None:
    """Ramène un cookie (format extension/JSON) au format attendu par Playwright."""
    name = c.get("name") or c.get("Name")
    value = c.get("value") or c.get("Value")
    if not name or value is None:
        return None
    domain = c.get("domain") or c.get("Domain") or ".tiktok.com"
    out = {
        "name": str(name),
        "value": str(value),
        "domain": str(domain),
        "path": str(c.get("path") or "/"),
    }
    exp = c.get("expirationDate") or c.get("expires") or c.get("expiry")
    if exp and float(exp) > 0:
        out["expires"] = int(float(exp))
    if "httpOnly" in c:
        out["httpOnly"] = bool(c["httpOnly"])
    if "secure" in c:
        out["secure"] = bool(c["secure"])
    ss = c.get("sameSite")
    if isinstance(ss, str):
        out["sameSite"] = _SAMESITE.get(ss.lower(), "Lax")
    return out


def parse_cookies(raw: str) -> list[dict]:
    """Accepte : JSON (export d'extension type Cookie-Editor), ou en-tête brut
    « nom=valeur; nom2=valeur2 » copié depuis les outils développeur."""
    raw = (raw or "").strip()
    if not raw:
        return []
    cookies: list[dict] = []
    if raw[0] in "[{":
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("cookies", [data])
        for c in data:
            n = _normalize_cookie(c)
            if n:
                cookies.append(n)
    else:
        # chaîne d'en-tête Cookie : nom=valeur; nom2=valeur2
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                if k:
                    cookies.append({"name": k, "value": v.strip(), "domain": ".tiktok.com", "path": "/"})
    return cookies


def save_cookies(raw: str) -> int:
    cookies = parse_cookies(raw)
    if not cookies:
        raise ScrapeError("Aucun cookie valide trouvé. Colle l'export JSON de ton extension de cookies.")
    _COOKIES_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
    return len(cookies)


def _load_cookies() -> list[dict]:
    try:
        return json.loads(_COOKIES_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - pas de cookies = pas grave
        return []


def cookies_status() -> dict:
    c = _load_cookies()
    has_session = any(x.get("name") == "sessionid" for x in c)
    return {"count": len(c), "logged_in": has_session}

from scraper import ScrapeError  # message d'erreur « propre » pour l'interface
from enrich import describe_phone  # enrichissement OSINT des numéros (pays/opérateur/type)


# --------------------------------------------------------------------------- #
# Extraction de contacts dans un texte libre (bio / description)
# --------------------------------------------------------------------------- #

# Candidat « numéro » : suite de chiffres avec séparateurs usuels (espace, . - / )
# et préfixe international optionnel. On valide/filtre ensuite pour éviter les
# faux positifs (compteurs de vues, dates…).
_PHONE_CAND = re.compile(
    r"(?:(?:\+|00)\s?\d{1,3}[\s.\-/]?)?"   # indicatif pays optionnel : +33 / 0033
    r"(?:\(?0\)?[\s.\-/]?)?"               # (0) optionnel après l'indicatif
    r"(?:\d[\s.\-/]?){7,13}\d"             # le corps du numéro
)


def _fmt_fr(d: str) -> str:
    """Formate un numéro FR national (10 chiffres) en 06 12 34 56 78."""
    return " ".join(d[i : i + 2] for i in range(0, 10, 2))


# Confusions OCR classiques lettre -> chiffre (pour rattraper les numéros mal lus)
_OCR_DIGIT = str.maketrans({
    "O": "0", "o": "0", "Q": "0",
    "I": "1", "l": "1", "|": "1", "!": "1",
    "Z": "2",
    "S": "5",
    "G": "6",
    "B": "8",
})

# Classes « chiffre » tolérantes OCR (les chiffres peuvent être des lettres-sosies)
_DG = r"[0-9OoQIl!|ZSGB]"          # un chiffre (ou sa lettre-sosie)
_NZ = r"[1-9IlZSGB!|]"             # un chiffre NON nul (ou sosie), ex: 2e chiffre FR
_SEP = r"[\s.\-/]*"                # séparateurs usuels (espace, . - /), 0 ou plus

# Numéros FR de longueur EXACTE (évite d'avaler plusieurs numéros collés) :
#  national  = 0 + [1-9] + 8 chiffres  (ex: 06 12 34 56 78)
#  +33/0033  = indicatif + [1-9] + 8 chiffres
_RE_FR_NAT = re.compile(rf"(?<![A-Za-z0-9])[0Oo]{_SEP}{_NZ}(?:{_SEP}{_DG}){{8}}")
_RE_FR_INTL = re.compile(rf"(?<![A-Za-z0-9])(?:\+|00){_SEP}33{_SEP}{_NZ}(?:{_SEP}{_DG}){{8}}")
# International générique (préfixe + explicite) : 8 à 14 chiffres.
_RE_INTL = re.compile(rf"(?<![A-Za-z0-9])\+{_SEP}(?:{_DG}{_SEP}){{7,13}}{_DG}")


def extract_phones(text: str) -> list[str]:
    """Numéros plausibles affichés dans le texte, tolérant aux erreurs d'OCR.

    Cible des numéros de longueur exacte pour ne pas fusionner plusieurs numéros
    (ou des chiffres parasites) en une suite trop longue qui serait rejetée.
    """
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: str):
        d = re.sub(r"\D", "", raw.translate(_OCR_DIGIT))
        plus = raw.lstrip().startswith("+") or d.startswith("00")
        v = None
        if plus:
            dd = d[2:] if d.startswith("00") else d
            if dd.startswith("33") and len(dd) == 11:
                v = "+33 " + _fmt_fr("0" + dd[2:])
            elif 8 <= len(dd) <= 14:
                v = "+" + dd
        elif len(d) == 10 and d[0] == "0" and d[1] != "0":
            v = _fmt_fr(d)
        if v and v not in seen:
            seen.add(v)
            out.append(v)

    for rgx in (_RE_FR_INTL, _RE_INTL, _RE_FR_NAT):
        for m in rgx.finditer(text):
            add(m.group(0))
    return out


# Réseaux / messageries souvent mis à côté d'un deal
# NB : les alias courts (sc, tg, wa, ig) sont encadrés de \b pour éviter de
# matcher à l'intérieur d'un mot (ex. « Big » contient « ig »).
_RE_SNAP = re.compile(r"(?:snap(?:chat)?|\bsc\b|👻)\s*[:=@]*\s*@?([a-zA-Z0-9._\-]{3,30})", re.I)
_RE_TG = re.compile(r"(?:t[ée]l[ée]gram|telegram|\btg\b|t\.me/)\s*[:=@/]*\s*@?([a-zA-Z0-9_]{4,32})", re.I)
_RE_WA = re.compile(r"(?:whats?app|\bwsp\b|\bwa\b)\s*[:=@]*\s*(\+?\d[\d\s.\-]{6,}\d)", re.I)
_RE_IG = re.compile(r"(?:insta(?:gram)?|\big\b)\s*[:=@]*\s*@?([a-zA-Z0-9._]{2,30})", re.I)
_RE_MAIL = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")
# @mentions (hors adresses mail) et liens (dont les plateformes de contact usuelles)
_RE_MENTION = re.compile(r"(?<![\w@./])@([a-zA-Z0-9._]{2,30})")
_RE_LINK = re.compile(
    r"(?:https?://|www\.)[^\s)>\]]+"
    r"|(?:linktr\.ee|beacons\.ai|t\.me|wa\.me|snapchat\.com/add|instagram\.com|telegram\.me|cash\.app)/[^\s)>\]]+",
    re.I,
)

# mots qui ne sont pas des pseudos (réduit le bruit sur snap/insta)
_STOP = {"chat", "story", "code", "moi", "me", "add", "ajoute", "pro", "story"}


def _handles(pattern: re.Pattern, text: str) -> list[str]:
    res, seen = [], set()
    for m in pattern.finditer(text or ""):
        h = m.group(1).strip(" .@_-")
        if h and h.lower() not in _STOP and h.lower() not in seen:
            seen.add(h.lower())
            res.append(h)
    return res


def extract_contacts(text: str) -> dict:
    """Renvoie tous les contacts publics détectés dans un texte."""
    text = text or ""
    mentions, seen = [], set()
    for m in _RE_MENTION.finditer(text):
        h = m.group(1).strip(" .").lower()
        if h and h not in seen:
            seen.add(h)
            mentions.append(h)
    links = list(dict.fromkeys(m.group(0).rstrip(".,") for m in _RE_LINK.finditer(text)))
    return {
        "telephones": extract_phones(text),
        "snapchat": _handles(_RE_SNAP, text),
        "telegram": _handles(_RE_TG, text),
        "whatsapp": [re.sub(r"\s", "", m.group(1)) for m in _RE_WA.finditer(text)],
        "instagram": _handles(_RE_IG, text),
        "emails": list(dict.fromkeys(_RE_MAIL.findall(text))),
        "mentions": mentions,
        "links": links,
    }


# --------------------------------------------------------------------------- #
# Fouille des réponses JSON interceptées
# --------------------------------------------------------------------------- #
def _walk(obj, cb):
    """Parcourt récursivement un JSON et appelle cb sur chaque dict."""
    if isinstance(obj, dict):
        cb(obj)
        for v in obj.values():
            _walk(v, cb)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, cb)


def _harvest(blobs: list) -> dict[str, dict]:
    """Agrège les comptes trouvés dans les réponses JTON (clé = pseudo).

    Un compte = { pseudo, nom, bio, descriptions[], urls_videos[] }.
    """
    authors: dict[str, dict] = {}

    def ensure(uid: str, nick: str = "", sig: str = ""):
        a = authors.get(uid)
        if a is None:
            a = {"pseudo": uid, "nom": nick, "bio": sig, "descriptions": [], "videos": []}
            authors[uid] = a
        else:
            if nick and not a["nom"]:
                a["nom"] = nick
            if sig and not a["bio"]:
                a["bio"] = sig
        return a

    def visit(d: dict):
        # Objet "auteur" : présent dans les items vidéo ET les résultats de comptes
        author = d.get("author") if isinstance(d.get("author"), dict) else None
        uinfo = None
        # résultats de recherche "comptes"
        for k in ("user_info", "userInfo", "user"):
            if isinstance(d.get(k), dict) and d[k].get("uniqueId"):
                uinfo = d[k]
                break

        if author and author.get("uniqueId"):
            a = ensure(author["uniqueId"], author.get("nickname", ""), author.get("signature", ""))
            desc = d.get("desc")
            if isinstance(desc, str) and desc.strip():
                a["descriptions"].append(desc.strip())
            vid = d.get("id") or d.get("aweme_id")
            if vid:
                a["videos"].append(f"https://www.tiktok.com/@{author['uniqueId']}/video/{vid}")

        if uinfo and uinfo.get("uniqueId"):
            ensure(uinfo["uniqueId"], uinfo.get("nickname", ""), uinfo.get("signature", ""))

    for b in blobs:
        try:
            _walk(b, visit)
        except Exception:  # noqa: BLE001 - un blob malformé ne doit pas tout casser
            continue
    return authors


def _bio_from_profile_html(html: str) -> str:
    """Extrait la bio depuis le JSON embarqué d'une page profil TikTok."""
    m = re.search(
        r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        html or "",
        re.S,
    )
    if not m:
        return ""
    try:
        data = json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return ""
    found = {"sig": ""}

    def visit(d):
        if not found["sig"]:
            u = d.get("user") if isinstance(d.get("user"), dict) else None
            if u and isinstance(u.get("signature"), str) and u["signature"].strip():
                found["sig"] = u["signature"].strip()

    _walk(data, visit)
    return found["sig"]


# --------------------------------------------------------------------------- #
# Recherche
# --------------------------------------------------------------------------- #
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_STEALTH = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"


def make_context(p, headless: bool = True):
    """Crée un contexte navigateur TikTok prêt à l'emploi : profil persistant,
    stealth léger et cookies de l'utilisateur injectés. Partagé par la recherche
    et l'analyse de média."""
    _PROFILE_DIR.mkdir(exist_ok=True)
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(_PROFILE_DIR),
        headless=headless,
        user_agent=_UA,
        locale="fr-FR",
        timezone_id="Europe/Paris",
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    context.add_init_script(_STEALTH)
    saved = _load_cookies()
    if saved:
        try:
            context.add_cookies(saved)
        except Exception:  # noqa: BLE001 - un cookie mal formé ne bloque pas tout
            pass
    return context


def search_tiktok(payload: dict) -> dict:
    """Recherche TikTok par mot-clé et renvoie un tableau de comptes + contacts."""
    keyword = (payload.get("keyword") or "").strip()
    if not keyword:
        raise ScrapeError("Entre un mot-clé à rechercher.")

    try:
        max_results = int(payload.get("max_results") or 40)
    except (TypeError, ValueError):
        max_results = 40
    max_results = max(5, min(max_results, 200))

    fetch_profiles = bool(payload.get("fetch_profiles"))
    headless = payload.get("headless", True)
    only_with_contact = bool(payload.get("only_with_contact"))

    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScrapeError(
            "La recherche TikTok nécessite Playwright. Installe-le avec : "
            "./.venv/bin/pip install playwright && ./.venv/bin/playwright install chromium"
        ) from exc

    blobs: list = []
    search_url = f"https://www.tiktok.com/search?q={quote(keyword)}"

    try:
        with sync_playwright() as p:
            # Contexte persistant (cookies réutilisés d'une session à l'autre).
            context = make_context(p, headless=bool(headless))
            browser = context  # pour context.close() en fin de fonction
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(resp):
                u = resp.url
                if "/api/search/" not in u and "/api/recommend/item_list" not in u:
                    return
                try:
                    ct = (resp.headers.get("content-type") or "")
                    if "json" in ct:
                        blobs.append(resp.json())
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)

            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            except PWTimeout:
                pass

            # Bannière cookies éventuelle
            for sel in ('button:has-text("Tout accepter")', 'button:has-text("Accept all")'):
                try:
                    page.click(sel, timeout=2500)
                    break
                except Exception:  # noqa: BLE001
                    pass

            # TikTok sert parfois une page « Une erreur est survenue / Réessayer ».
            # On clique Réessayer (ou on recharge) quelques fois tant qu'aucun
            # résultat n'est arrivé.
            for _ in range(3):
                page.wait_for_timeout(2500)
                if _harvest(blobs):
                    break
                clicked = False
                for sel in ('button:has-text("Réessayer")', 'button:has-text("Retry")'):
                    try:
                        page.click(sel, timeout=1500)
                        clicked = True
                        break
                    except Exception:  # noqa: BLE001
                        pass
                if not clicked:
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                    except Exception:  # noqa: BLE001
                        pass

            # Laisse le temps aux premiers résultats d'arriver. En mode visible,
            # on patiente longtemps pour que l'utilisateur puisse passer un captcha.
            wait_budget = 20 if headless else 120
            waited = 0
            while waited < wait_budget and not _harvest(blobs):
                page.wait_for_timeout(1500)
                waited += 1.5

            # Scroll pour charger plus de résultats, tant que ça grossit
            stable = 0
            for _ in range(40):
                harvested = len(_harvest(blobs))
                if harvested >= max_results:
                    break
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1600)
                if len(_harvest(blobs)) <= harvested:
                    stable += 1
                    if stable >= 3:
                        break
                else:
                    stable = 0

            authors = _harvest(blobs)

            if not authors:
                browser.close()
                raise ScrapeError(
                    "Aucun résultat récupéré. TikTok a probablement bloqué le robot "
                    "(captcha/connexion requise). Essaie en décochant « navigateur invisible » "
                    "pour résoudre le captcha à la main, ou relance plus tard."
                )

            # Optionnel : visiter chaque profil pour compléter les bios manquantes
            if fetch_profiles:
                todo = [a for a in authors.values() if not a["bio"]][:40]
                for a in todo:
                    try:
                        page.goto(
                            f"https://www.tiktok.com/@{a['pseudo']}",
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                        page.wait_for_timeout(600)
                        a["bio"] = _bio_from_profile_html(page.content())
                    except Exception:  # noqa: BLE001
                        continue

            browser.close()
    except ScrapeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ScrapeError(f"Erreur pendant la recherche TikTok : {exc}") from exc

    # --- Construction des lignes du tableau ---
    # 1re passe : extraction des contacts par compte
    prelim = []
    for a in authors.values():
        c = extract_contacts("\n".join([a["bio"]] + a["descriptions"]))
        prelim.append((a, c))

    # Recoupement : un même numéro présent sur plusieurs comptes = réseau lié
    num_accounts: dict[str, set] = {}
    for a, c in prelim:
        for ph in c["telephones"]:
            num_accounts.setdefault(ph, set()).add("@" + a["pseudo"])

    rows: list[dict] = []
    for a, c in prelim:
        autres = []
        for label, vals in (
            ("Snap", c["snapchat"]), ("Telegram", c["telegram"]),
            ("WhatsApp", c["whatsapp"]), ("Insta", c["instagram"]), ("Mail", c["emails"]),
            ("Mentions", ["@" + m for m in c["mentions"]]), ("Liens", c["links"]),
        ):
            if vals:
                autres.append(f"{label}: " + ", ".join(vals))

        tel_parts = []
        for ph in c["telephones"]:
            desc = describe_phone(ph)                         # pays · type · opérateur
            liES = sorted(num_accounts[ph] - {"@" + a["pseudo"]})
            if liES:
                desc += " ⚠ aussi sur " + ", ".join(liES)      # comptes liés
            tel_parts.append(desc)

        rows.append({
            "compte": "@" + a["pseudo"],
            "nom": a["nom"],
            "telephone": " / ".join(tel_parts),
            "autres_contacts": " | ".join(autres),
            "bio": a["bio"],
            "nb_videos": len(a["videos"]),
            "exemple_description": a["descriptions"][0] if a["descriptions"] else "",
            "url_compte": f"https://www.tiktok.com/@{a['pseudo']}",
            "exemple_video": a["videos"][0] if a["videos"] else "",
        })

    if only_with_contact:
        rows = [r for r in rows if r["telephone"] or r["autres_contacts"]]

    # les comptes avec un téléphone d'abord, puis ceux avec d'autres contacts
    rows.sort(key=lambda r: (r["telephone"] == "", r["autres_contacts"] == ""))
    rows = rows[:max_results]

    columns = [
        "compte", "nom", "telephone", "autres_contacts", "bio",
        "nb_videos", "exemple_description", "url_compte", "exemple_video",
    ]
    return {
        "keyword": keyword,
        "count": len(rows),
        "columns": columns,
        "rows": rows,
    }
