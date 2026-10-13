"""Stockage local des résultats (SQLite) pour pouvoir rechercher un numéro
à travers tout l'historique de scraping.

Local, hors-ligne, dans .osint_store.db à la racine du projet. Même périmètre
que le reste : données publiques collectées dans un cadre autorisé.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from tiktok import extract_contacts, extract_phones

_DB = Path(__file__).resolve().parent.parent / ".osint_store.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB)
    c.execute(
        """CREATE TABLE IF NOT EXISTS findings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, source TEXT, query TEXT,
            compte TEXT, nom TEXT, telephone TEXT, autres_contacts TEXT,
            bio TEXT, url TEXT, phones TEXT, selectors TEXT DEFAULT '', data TEXT)"""
    )
    # Migration douce pour les bases créées avant l'ajout de `selectors`
    cols = [r[1] for r in c.execute("PRAGMA table_info(findings)").fetchall()]
    if "selectors" not in cols:
        c.execute("ALTER TABLE findings ADD COLUMN selectors TEXT DEFAULT ''")
    return c


def _selectors(text: str) -> set[str]:
    """Identifiants normalisés (préfixés par type) servant à relier les comptes."""
    c = extract_contacts(text)
    sel: set[str] = set()
    for p in c["telephones"]:
        k = _canon(p)
        if k:
            sel.add("tel:" + k)
    for e in c["emails"]:
        sel.add("mail:" + e.lower())
    for s in c["snapchat"]:
        sel.add("snap:" + s.lower())
    for t in c["telegram"]:
        sel.add("tg:" + t.lower())
    for i in c["instagram"]:
        sel.add("ig:" + i.lower())
    for w in c["whatsapp"]:
        k = _canon(w)
        if k:
            sel.add("tel:" + k)
    for m in c["mentions"]:
        sel.add("men:" + m.lower())
    for link in c["links"]:
        norm = re.sub(r"^https?://(www\.)?", "", link.lower()).rstrip("/")
        if norm:
            sel.add("link:" + norm)
    return sel


def _canon(num: str) -> str:
    """Forme canonique d'un numéro pour comparer malgré 0/+33 : 9 chiffres utiles."""
    d = re.sub(r"\D", "", num or "")
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("33"):
        d = d[2:]
    d = d.lstrip("0")
    return d[-9:] if len(d) >= 9 else d


def save(source: str, query: str, rows: list[dict]) -> int:
    """Enregistre chaque ligne, avec les numéros détectés en forme canonique."""
    if not rows:
        return 0
    c = _conn()
    ts = time.time()
    n = 0
    for r in rows:
        text = " ".join(str(v) for v in r.values())
        canons = {_canon(p) for p in extract_phones(text)}
        canons.discard("")
        phones = " " + " ".join(sorted(canons)) + " " if canons else ""
        sel = _selectors(text)
        selectors = " " + " ".join(sorted(sel)) + " " if sel else ""
        c.execute(
            "INSERT INTO findings(ts,source,query,compte,nom,telephone,"
            "autres_contacts,bio,url,phones,selectors,data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ts, source, query,
                str(r.get("compte", "")), str(r.get("nom", "")),
                str(r.get("telephone", "") or r.get("telephones_detectes", "")),
                str(r.get("autres_contacts", "")), str(r.get("bio", ""))[:1000],
                str(r.get("url_compte", "") or r.get("lien", "") or r.get("url", "")),
                phones, selectors, json.dumps(r, ensure_ascii=False),
            ),
        )
        n += 1
    c.commit()
    c.close()
    return n


def search_number(q: str) -> list[dict]:
    """Retrouve toutes les occurrences d'un numéro dans l'historique."""
    canon = _canon(q)
    if not canon:
        return []
    c = _conn()
    rows = c.execute(
        "SELECT ts,source,query,compte,nom,telephone,autres_contacts,url "
        "FROM findings WHERE phones LIKE ? ORDER BY ts DESC",
        ("% " + canon + " %",),
    ).fetchall()
    c.close()

    out, seen = [], set()
    for ts, source, query, compte, nom, tel, autres, url in rows:
        key = (compte, tel, url)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)),
            "source": source, "recherche": query, "compte": compte,
            "nom": nom, "telephone": tel, "autres_contacts": autres, "url": url,
        })
    return out


_SEL_LABEL = {
    "tel": "téléphone", "mail": "e-mail", "snap": "Snap", "tg": "Telegram",
    "ig": "Insta", "men": "mention", "link": "lien",
}


def _pretty_sel(s: str) -> str:
    typ, _, val = s.partition(":")
    return f"{_SEL_LABEL.get(typ, typ)}: {val}"


def linked_accounts(min_size: int = 2) -> list[dict]:
    """Regroupe les comptes reliés par un identifiant partagé (union-find).

    Renvoie des grappes : comptes liés + les identifiants qui les relient.
    """
    c = _conn()
    rows = c.execute(
        "SELECT compte, url, source, selectors FROM findings WHERE selectors != ''"
    ).fetchall()
    c.close()

    # noeud = compte (sinon url, sinon rien)
    acc_sel: dict[str, set] = {}
    acc_meta: dict[str, dict] = {}
    for compte, url, source, selectors in rows:
        node = compte or url
        if not node:
            continue
        sels = {s for s in selectors.split(" ") if s}
        # Le propre pseudo du compte devient un sélecteur « mention » : ainsi un
        # autre compte qui le cite (@ce_compte) lui est relié.
        handle = (compte or "").lstrip("@").lower()
        if handle:
            sels.add("men:" + handle)
        if not sels:
            continue
        acc_sel.setdefault(node, set()).update(sels)
        acc_meta.setdefault(node, {"compte": compte, "url": url, "source": source})

    # union-find : deux comptes fusionnent s'ils partagent un sélecteur
    parent: dict[str, str] = {a: a for a in acc_sel}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    sel_to_accounts: dict[str, list] = {}
    for acc, sels in acc_sel.items():
        for s in sels:
            sel_to_accounts.setdefault(s, []).append(acc)
    for s, accs in sel_to_accounts.items():
        for other in accs[1:]:
            union(accs[0], other)

    clusters: dict[str, list] = {}
    for acc in acc_sel:
        clusters.setdefault(find(acc), []).append(acc)

    out = []
    for members in clusters.values():
        if len(members) < min_size:
            continue
        # identifiants effectivement partagés dans la grappe
        shared = sorted(
            _pretty_sel(s) for s, accs in sel_to_accounts.items()
            if len(set(accs) & set(members)) > 1
        )
        out.append({
            "comptes": [
                {"compte": acc_meta[m]["compte"], "url": acc_meta[m]["url"], "source": acc_meta[m]["source"]}
                for m in sorted(members)
            ],
            "partages": shared,
            "taille": len(members),
        })
    out.sort(key=lambda g: g["taille"], reverse=True)
    return out


def stats() -> dict:
    c = _conn()
    total = c.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
    withnum = c.execute("SELECT COUNT(*) FROM findings WHERE phones != ''").fetchone()[0]
    c.close()
    return {"total": total, "avec_numero": withnum}
