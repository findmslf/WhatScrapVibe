"""API FastAPI pour l'outil de scraping + service de la page web."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scraper import ScrapeError, run_analyze, run_scrape
import store
from enrich import describe_phones
from media import analyze_video
from tiktok import cookies_status, extract_phones, save_cookies, search_tiktok

app = FastAPI(title="Outil de Scraping", version="1.0")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# --------------------------------------------------------------------------- #
# Modèles de requête
# --------------------------------------------------------------------------- #
class ScrapeRequest(BaseModel):
    url: str
    mode: str = "simple"                 # "simple" | "advanced"
    simple_fields: list[str] = []        # pour le mode simple
    fields: list[dict] = []              # pour le mode avancé
    container: str | None = None         # sélecteur de bloc répété (avancé)
    dynamic: bool = False                # rendu JavaScript via Playwright
    wait_selector: str | None = None     # attendre cet élément (dynamique)
    respect_robots: bool = True
    paginate: bool = False               # suivre les pages suivantes
    max_pages: int = 5                   # limite de pages
    next_selector: str | None = None     # sélecteur du lien « suivant » (optionnel)
    clean_text: bool = True              # trim + espaces propres
    dedupe: bool = True                  # enlever les doublons
    drop_empty: bool = True              # enlever les lignes vides
    price_to_number: bool = False        # "£51.77" -> "51.77"
    enrich_phones: bool = False          # détecter + enrichir les numéros dans les résultats


class AnalyzeRequest(BaseModel):
    url: str
    dynamic: bool = False
    respect_robots: bool = True
    wait_selector: str | None = None


class TikTokRequest(BaseModel):
    keyword: str
    max_results: int = 40
    fetch_profiles: bool = False        # visiter chaque profil pour compléter les bios
    headless: bool = True               # navigateur invisible (décocher pour résoudre un captcha)
    only_with_contact: bool = False     # ne garder que les comptes avec un contact détecté


class CookiesRequest(BaseModel):
    raw: str


class VideoRequest(BaseModel):
    url: str
    ocr: bool = True                     # lire le texte incrusté à l'écran
    precision: bool = False              # OCR EasyOCR (deep-learning) au lieu de Tesseract
    audio: bool = False                  # transcrire l'audio (Whisper)
    headless: bool = False               # posts gated par captcha -> visible par défaut
    max_seconds: int = 60                # limite de durée analysée
    fps: float = 1.0                     # images par seconde pour l'OCR
    whisper_model: str = "small"


class NumberSearchRequest(BaseModel):
    num: str


class ExportRequest(BaseModel):
    format: str                          # "csv" | "xlsx" | "json"
    rows: list[dict]
    filename: str = "scraping"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
def _enrich_rows(result: dict):
    """Détecte les numéros dans chaque ligne et ajoute une colonne enrichie."""
    rows = result.get("rows") or []
    added = False
    for row in rows:
        phones = extract_phones(" ".join(str(v) for v in row.values()))
        if phones:
            row["telephones_detectes"] = " / ".join(describe_phones(phones))
            added = True
    if added and "telephones_detectes" not in result.get("columns", []):
        result.setdefault("columns", []).append("telephones_detectes")


@app.post("/api/scrape")
def scrape(req: ScrapeRequest):
    try:
        result = run_scrape(req.model_dump())
        if req.enrich_phones:
            _enrich_rows(result)
            try:
                store.save("scrape", req.url, result.get("rows", []))
            except Exception:  # noqa: BLE001 - le stockage ne doit jamais casser le scrape
                pass
        return result
    except ScrapeError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": f"Erreur inattendue : {exc}"})


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    try:
        return run_analyze(req.model_dump())
    except ScrapeError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": f"Erreur inattendue : {exc}"})


@app.post("/api/tiktok")
def tiktok(req: TikTokRequest):
    try:
        result = search_tiktok(req.model_dump())
        try:
            store.save("tiktok", req.keyword, result.get("rows", []))
        except Exception:  # noqa: BLE001 - le stockage ne doit jamais casser la recherche
            pass
        return result
    except ScrapeError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": f"Erreur inattendue : {exc}"})


@app.post("/api/tiktok/cookies")
def tiktok_cookies(req: CookiesRequest):
    try:
        n = save_cookies(req.raw)
        return {"saved": n, **cookies_status()}
    except ScrapeError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": f"Cookies illisibles : {exc}"})


@app.get("/api/tiktok/cookies")
def tiktok_cookies_status():
    return cookies_status()


@app.post("/api/tiktok/video")
def tiktok_video(req: VideoRequest):
    try:
        return analyze_video(req.model_dump())
    except ScrapeError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": f"Erreur inattendue : {exc}"})


@app.post("/api/osint/search")
def osint_search(req: NumberSearchRequest):
    try:
        return {"num": req.num, "results": store.search_number(req.num), "stats": store.stats()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": f"Erreur recherche : {exc}"})


@app.get("/api/osint/links")
def osint_links():
    try:
        return {"clusters": store.linked_accounts()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": f"Erreur corrélation : {exc}"})


@app.get("/api/osint/stats")
def osint_stats():
    try:
        return store.stats()
    except Exception:  # noqa: BLE001
        return {"total": 0, "avec_numero": 0}


@app.post("/api/export")
def export(req: ExportRequest):
    rows = req.rows
    columns = list(rows[0].keys()) if rows else []
    name = req.filename or "scraping"

    if req.format == "json":
        data = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
        )

    if req.format == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        data = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM -> Excel lit l'UTF-8
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )

    if req.format == "xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:
            return JSONResponse(
                status_code=400,
                content={"error": "Export Excel indisponible : pip install openpyxl"},
            )
        wb = Workbook()
        ws = wb.active
        ws.title = "Scraping"
        ws.append(columns)
        for row in rows:
            ws.append([row.get(c, "") for c in columns])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )

    return JSONResponse(status_code=400, content={"error": "Format inconnu."})


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/guide")
def guide():
    return FileResponse(FRONTEND_DIR / "guide.html")


# Sert les éventuels fichiers statiques (css/js) à côté de index.html
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
