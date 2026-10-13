"""Analyse approfondie d'un post TikTok : OCR (texte à l'écran) + transcription.

Phase 2, lourde et optionnelle. Récupère les numéros/contacts qui ne sont pas
dans le texte (bio/description) mais affichés dans la vidéo / le carrousel photo,
ou donnés à l'oral.

On passe par le navigateur Playwright (même session + cookies que la recherche)
plutôt que par yt-dlp : TikTok bloque yt-dlp (pas de session) et ne supporte pas
les posts « /photo/ ». Le navigateur, lui, a la session et voit tout.

Dépendances :
- ffmpeg                         : extraction images/audio d'une vidéo (apt)
- Pillow + pytesseract + tesseract-ocr (+ fra) : OCR                 (pip + apt)
- faster-whisper                 : transcription audio                (pip)

On télécharge uniquement le média demandé, dans un dossier temporaire supprimé
après. Même périmètre que tiktok.py : investigation autorisée, données publiques.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from scraper import ScrapeError
from enrich import describe_phones
from tiktok import extract_contacts, make_context


def _require(binary: str, apt_pkg: str):
    if shutil.which(binary) is None:
        raise ScrapeError(
            f"« {binary} » n'est pas installé. Installe-le avec : sudo apt install {apt_pkg}"
        )


def _walk(obj, cb):
    if isinstance(obj, dict):
        cb(obj)
        for v in obj.values():
            _walk(v, cb)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, cb)


def _clean_url(url: str) -> str:
    return (url or "").strip().split("?")[0].split("#")[0]


def _target_id(url: str) -> str | None:
    """Extrait l'ID du post depuis l'URL (/photo/<id> ou /video/<id>)."""
    m = re.search(r"/(?:photo|video|v)/(\d+)", url)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# Récupération du post via le navigateur
# --------------------------------------------------------------------------- #
def _find_item(data, target_id: str | None = None) -> dict | None:
    """Trouve l'objet 'itemStruct' du post dans un JSON.

    Si target_id est fourni, on ne renvoie QUE l'item dont l'id correspond
    (évite de confondre le post visé avec une vidéo recommandée chargée à côté).
    """
    hits: list[dict] = []

    def visit(d):
        if "imagePost" in d:                                   # post photo/carrousel
            hits.append(d)
        elif isinstance(d.get("video"), dict) and "desc" in d:  # post vidéo
            hits.append(d)

    _walk(data, visit)
    if not hits:
        return None
    if target_id:
        for h in hits:
            if str(h.get("id") or h.get("aweme_id") or "") == str(target_id):
                return h
        return None  # aucun item ne correspond au post visé
    return hits[0]


def _video_urls(item: dict) -> list[str]:
    v = item.get("video") or {}
    urls = []
    for key in ("playAddr", "downloadAddr"):
        if isinstance(v.get(key), str) and v[key]:
            urls.append(v[key])
    for br in v.get("bitrateInfo") or []:
        for u in (((br or {}).get("PlayAddr") or {}).get("UrlList") or []):
            urls.append(u)
    # dédoublonne en gardant l'ordre
    return list(dict.fromkeys(urls))


def _image_urls(item: dict) -> list[str]:
    urls = []
    for img in ((item.get("imagePost") or {}).get("images") or []):
        for u in (((img or {}).get("imageURL") or {}).get("urlList") or []):
            urls.append(u)
            break  # une seule résolution par image suffit
    return urls


def _read_item(page, target_id: str | None = None):
    el = page.query_selector("#__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if not el:
        return None
    try:
        data = json.loads(el.text_content() or "{}")
    except Exception:  # noqa: BLE001
        return None
    return _find_item(data, target_id)


def _fetch_post(url: str, work: Path, headless: bool = False) -> dict:
    """Ouvre le post et renvoie {desc, videos:[urls], images:[urls]} via le navigateur.

    Les pages de post sont protégées par captcha. En mode visible, on laisse à
    l'utilisateur le temps de résoudre le puzzle, puis on recharge la page (le
    contenu réel arrive une fois la vérification passée)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScrapeError(
            "Playwright manquant : ./.venv/bin/pip install playwright "
            "&& ./.venv/bin/playwright install chromium"
        ) from exc

    target_id = _target_id(url)

    with sync_playwright() as p:
        context = make_context(p, headless=headless)
        try:
            page = context.pages[0] if context.pages else context.new_page()

            # On intercepte les réponses JSON de l'API : quand le post se charge
            # (après résolution du captcha), le détail de l'item y passe. Bien plus
            # fiable que de relire le HTML (qui ne change pas) ou de recharger (ce
            # qui relancerait un captcha).
            blobs: list = []

            def on_response(r):
                u = r.url
                if "tiktok.com" in u and "/api/" in u:
                    try:
                        if "json" in (r.headers.get("content-type") or ""):
                            blobs.append(r.json())
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", on_response)

            def current_item():
                it = _read_item(page, target_id)
                if it:
                    return it
                for b in blobs:
                    f = _find_item(b, target_id)
                    if f:
                        return f
                return None

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(2500)

            # En visible on laisse le temps de résoudre le slider ; en headless on
            # tente court. On NE recharge PAS (ça relancerait le captcha).
            item = current_item()
            budget = 20 if headless else 170
            waited = 0
            while not item and waited < budget:
                page.wait_for_timeout(3000)
                waited += 3
                item = current_item()

            if not item:
                low = (page.content() or "").lower()
                blocked = any(m in low for m in (
                    "curseur pour", "puzzle", "captcha", "verify", "se connecter", "log in"
                ))
                if blocked:
                    raise ScrapeError(
                        "Captcha TikTok non franchi à temps. Assure-toi que « Navigateur invisible » "
                        "est DÉCOCHÉ : une fenêtre doit s'ouvrir. Résous le puzzle dès qu'elle "
                        "apparaît (tu as ~2,5 min), le contenu se charge ensuite tout seul. "
                        "Si tu l'as résolu et que ça échoue quand même, relance : le profil "
                        "retient souvent la vérification au 2e essai."
                    )
                raise ScrapeError(
                    "Impossible de lire ce post (lien privé/supprimé, ou format non reconnu)."
                )

            videos = _video_urls(item)
            images = _image_urls(item)
            desc = item.get("desc") or ""

            # Téléchargement via le contexte (cookies + referer appliqués)
            files = {"desc": desc, "video": None, "images": []}
            req = context.request
            headers = {"Referer": "https://www.tiktok.com/"}

            for u in videos:
                try:
                    r = req.get(u, headers=headers, timeout=60000)
                    if r.ok:
                        dest = work / "video.mp4"
                        dest.write_bytes(r.body())
                        files["video"] = dest
                        break
                except Exception:  # noqa: BLE001
                    continue

            for i, u in enumerate(images):
                try:
                    r = req.get(u, headers=headers, timeout=30000)
                    if r.ok:
                        dest = work / f"img_{i:03d}.jpg"
                        dest.write_bytes(r.body())
                        files["images"].append(dest)
                except Exception:  # noqa: BLE001
                    continue

            return files
        finally:
            context.close()


# --------------------------------------------------------------------------- #
# OCR / transcription
# --------------------------------------------------------------------------- #
def _prep_variants(im):
    """Prépare des versions optimisées pour l'OCR : agrandi + contraste, et sa
    version inversée (texte clair sur fond sombre, très fréquent sur TikTok)."""
    from PIL import Image, ImageEnhance, ImageOps

    im = im.convert("L")
    w, h = im.size
    longest = max(w, h)
    if longest and longest < 1800:                      # agrandit les petites images
        f = 1800 / longest
        im = im.resize((int(w * f), int(h * f)), Image.LANCZOS)
    im = ImageOps.autocontrast(im)
    im = ImageEnhance.Sharpness(im).enhance(1.6)
    return [im, ImageOps.invert(im)]


def _ocr_one(path) -> str:
    import pytesseract
    from PIL import Image

    try:
        base = Image.open(path)
    except Exception:  # noqa: BLE001
        return ""
    out = []
    for variant in _prep_variants(base):
        for psm in (6, 11):                             # bloc de texte + texte épars
            try:
                t = pytesseract.image_to_string(variant, lang="fra+eng", config=f"--psm {psm}")
                if t.strip():
                    out.append(t)
            except Exception:  # noqa: BLE001
                pass
    return "\n".join(out)


# --- Mode précision : EasyOCR (deep-learning, meilleur sur le texte stylisé) --- #
_EASY_READER = None


def _easyocr_reader():
    global _EASY_READER
    if _EASY_READER is None:
        try:
            import easyocr
        except ImportError as exc:
            raise ScrapeError(
                "Mode précision indisponible : ./.venv/bin/pip install easyocr"
            ) from exc
        # 1er appel : télécharge les modèles (~100 Mo). CPU (pas de GPU requis).
        _EASY_READER = easyocr.Reader(["fr", "en"], gpu=False, verbose=False)
    return _EASY_READER


def _ocr_one_easy(path) -> str:
    reader = _easyocr_reader()
    try:
        lines = reader.readtext(str(path), detail=0, paragraph=True)
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(lines)


def _ocr_images(images: list[Path], precision: bool = False) -> str:
    if precision:
        engine = _ocr_one_easy
    else:
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            raise ScrapeError(
                "OCR indisponible : ./.venv/bin/pip install pillow pytesseract "
                "&& sudo apt install tesseract-ocr tesseract-ocr-fra"
            ) from exc
        _require("tesseract", "tesseract-ocr tesseract-ocr-fra")
        engine = _ocr_one

    seen: set[str] = set()
    pieces: list[str] = []
    for img in images:
        for line in engine(img).splitlines():
            line = line.strip()
            key = line.lower()
            if len(line) >= 2 and key not in seen:
                seen.add(key)
                pieces.append(line)
    return "\n".join(pieces)


def _video_frames(video: Path, work: Path, fps: float, max_seconds: int) -> list[Path]:
    _require("ffmpeg", "ffmpeg")
    frames = work / "frames"
    frames.mkdir(exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-t", str(max_seconds), "-i", str(video),
         "-vf", f"fps={fps}", str(frames / "f_%04d.png")],
        check=False, timeout=180,
    )
    return sorted(frames.glob("*.png"))


def _transcribe(video: Path, work: Path, model_size: str, max_seconds: int) -> str:
    _require("ffmpeg", "ffmpeg")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ScrapeError("Transcription indisponible : ./.venv/bin/pip install faster-whisper") from exc

    wav = work / "audio.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-t", str(max_seconds), "-i", str(video),
         "-ac", "1", "-ar", "16000", str(wav)],
        check=False, timeout=180,
    )
    if not wav.exists():
        return ""
    model = WhisperModel(model_size, device="cpu", compute_type="int8")  # léger, CPU
    segments, _ = model.transcribe(str(wav), language="fr")
    return " ".join(seg.text.strip() for seg in segments).strip()


# --------------------------------------------------------------------------- #
# Point d'entrée
# --------------------------------------------------------------------------- #
def analyze_video(payload: dict) -> dict:
    url = _clean_url(payload.get("url"))
    if not url:
        raise ScrapeError("Donne l'URL d'un post TikTok (vidéo ou photo).")

    do_ocr = payload.get("ocr", True)
    do_audio = bool(payload.get("audio"))
    try:
        max_seconds = max(5, min(int(payload.get("max_seconds") or 60), 180))
    except (TypeError, ValueError):
        max_seconds = 60
    try:
        fps = max(0.2, min(float(payload.get("fps") or 1), 2.0))
    except (TypeError, ValueError):
        fps = 1.0
    model_size = payload.get("whisper_model") or "small"

    headless = bool(payload.get("headless"))
    precision = bool(payload.get("precision"))

    work = Path(tempfile.mkdtemp(prefix="tk_media_"))
    try:
        post = _fetch_post(url, work, headless=headless)
        is_photo = bool(post["images"]) and not post["video"]

        ocr_text = ""
        transcript = ""
        if do_ocr:
            if is_photo:
                ocr_text = _ocr_images(post["images"], precision)
            elif post["video"]:
                ocr_text = _ocr_images(_video_frames(post["video"], work, fps, max_seconds), precision)
        if do_audio and post["video"]:
            transcript = _transcribe(post["video"], work, model_size, max_seconds)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    full_text = "\n".join([post.get("desc", ""), ocr_text, transcript])
    contacts = extract_contacts(full_text)
    contacts["telephones"] = describe_phones(contacts["telephones"])  # enrichissement OSINT
    return {
        "url": url,
        "type": "photo" if is_photo else "video",
        "desc": post.get("desc", ""),
        "ocr_text": ocr_text,
        "transcript": transcript,
        "contacts": contacts,
    }
