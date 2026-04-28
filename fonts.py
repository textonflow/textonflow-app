"""
fonts.py — Configuración de fuentes, escalas y sesión HTTP para TextOnFlow.
Importado por main.py. Solo depende de stdlib + Pillow + requests.
"""
import os
import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("textonflow")

try:
    from pilmoji.source import TwitterEmojiSource
    _PILMOJI_OK = True
except ImportError:
    TwitterEmojiSource = object
    _PILMOJI_OK = False

# ─── Fuentes disponibles ──────────────────────────────────────────────────────
FONT_MAPPING = {
    "Arial":                  "./fonts/LiberationSans-Regular.ttf",
    "Arial-Bold":             "./fonts/LiberationSans-Bold.ttf",
    "Arial-Italic":           "./fonts/LiberationSans-Italic.ttf",
    "Arial-BoldItalic":       "./fonts/LiberationSans-BoldItalic.ttf",
    "MeowScript":             "./fonts/MeowScript-Regular.ttf",
    "Mynerve":                "./fonts/Mynerve-Regular.ttf",
    "PlaywriteAUQLD":         "./fonts/PlaywriteAUQLD-Regular.ttf",
    "SpicyRice":              "./fonts/SpicyRice-Regular.ttf",
    "PassionOne":             "./fonts/PassionOne-Regular.ttf",
    "Doto":                   "./fonts/Doto-Regular.ttf",
    "HennyPenny":             "./fonts/HennyPenny-Regular.ttf",
    "RockSalt":               "./fonts/RockSalt-Regular.ttf",
    "Arkipelago":             "./fonts/Arkipelago-Regular.ttf",
    "HFBigcuat":              "./fonts/HFBigcuat-Regular.ttf",
    "HFBigcuatDoodle":        "./fonts/HFBigcuat-Doodle.ttf",
    "Oishigo":                "./fonts/Oishigo-Regular.ttf",
    "OraqleScript":           "./fonts/OraqleScript-Regular.ttf",
    "OraqleSwash":            "./fonts/OraqleSwash-Regular.otf",
    # ── MYKOZ Brand Fonts ────────────────────────────────────────────────────
    "VariexLight":            "./fonts/Variex-Light.ttf",
    "ScholarRegular":         "./fonts/Scholar-Regular.otf",
    "ScholarItalic":          "./fonts/Scholar-Italic.otf",
    "GeomanistRegular":       "./fonts/Geomanist-Regular.otf",
    "GeomanistItalic":        "./fonts/Geomanist-Italic.otf",
    "GeomanistBold":          "./fonts/Geomanist-Bold.otf",
    "GeomanistBoldItalic":    "./fonts/Geomanist-Bold-Italic.otf",
}

# ─── Factores de escala por fuente (calculados al arrancar) ──────────────────
_MEASURE_SIZE = 100
_REFERENCE_FONT_CANDIDATES = [
    "./fonts/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "./fonts/DejaVuSans-Bold.ttf",
    "./fonts/MeowScript-Regular.ttf",
]

def _get_reference_font_path() -> str:
    for p in _REFERENCE_FONT_CANDIDATES:
        if os.path.exists(p):
            logger.info(f"Fuente de referencia: {p}")
            return p
    logger.warning("Sin fuente de referencia — escala de fuentes desactivada")
    return ""

_REFERENCE_FONT_PATH = _get_reference_font_path()

def _compute_font_scale(font_path: str, reference_height: int) -> float:
    try:
        from PIL import ImageFont, ImageDraw, Image as _Img
        f    = ImageFont.truetype(font_path, _MEASURE_SIZE)
        img  = _Img.new("RGB", (400, 200), (255, 255, 255))
        d    = ImageDraw.Draw(img)
        bbox = d.textbbox((0, 0), "H", font=f)
        h    = max(1, bbox[3] - bbox[1])
        return round(min(5.0, max(0.8, reference_height / h)), 3)
    except Exception as e:
        logger.warning(f"No se pudo medir {font_path}: {e}")
        return 1.0

def _build_font_scale_map() -> dict:
    if not _REFERENCE_FONT_PATH:
        logger.warning("Sin fuente de referencia — escala automatica desactivada")
        return {}
    try:
        from PIL import ImageFont, ImageDraw, Image as _Img
        ref  = ImageFont.truetype(_REFERENCE_FONT_PATH, _MEASURE_SIZE)
        img  = _Img.new("RGB", (400, 200), (255, 255, 255))
        d    = ImageDraw.Draw(img)
        bbox = d.textbbox((0, 0), "H", font=ref)
        ref_h = max(1, bbox[3] - bbox[1])
        scales = {name: _compute_font_scale(path, ref_h) for name, path in FONT_MAPPING.items()}
        logger.info(f"Escalas de fuentes calculadas: {len(scales)} fuentes")
        return scales
    except Exception as e:
        logger.error(f"Error calculando escalas: {e}")
        return {}

FONT_SIZE_SCALE = _build_font_scale_map()

# ─── Fuente de emojis Noto ────────────────────────────────────────────────────
NOTO_EMOJI_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/NotoColorEmoji.ttf",
]

def get_noto_emoji_font():
    for path in NOTO_EMOJI_PATHS:
        if os.path.exists(path):
            logger.info(f"NotoColorEmoji encontrado: {path}")
            return path
    logger.warning("NotoColorEmoji no encontrado en el sistema")
    return None

# ─── Session HTTP con reintentos ──────────────────────────────────────────────
def build_retry_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    session.headers.update({"User-Agent": "TextOnFlow-EmojiRenderer/1.0"})
    return session

# ─── TwitterEmojiSource con reintentos ───────────────────────────────────────
class RetryTwitterEmojiSource(TwitterEmojiSource):
    def __init__(self):
        super().__init__()
        self._retry_session = build_retry_session()

    def request_url(self, url: str, **kwargs) -> bytes:
        try:
            response = self._retry_session.get(url, timeout=8, **kwargs)
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.warning(f"Fallo descarga emoji ({url}): {e}")
            raise
