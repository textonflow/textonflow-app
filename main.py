from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Dict, List, Optional
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from pilmoji import Pilmoji
from pilmoji.source import TwitterEmojiSource, EmojiCDNSource
try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False
import os
import re
import math
import asyncio
from html.parser import HTMLParser
import uuid
import uvicorn
import logging
import threading
import time
import secrets
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from io import BytesIO
import base64
import json
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
try:
    import rjsmin as _rjsmin
    _RJSMIN_OK = True
except ImportError:
    _RJSMIN_OK = False

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_OK = True
except ImportError:
    _PSYCOPG2_OK = False

try:
    from passlib.context import CryptContext
    from jose import JWTError, jwt
    _AUTH_OK = True
except ImportError:
    _AUTH_OK = False

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ─── Base de datos (Supabase PostgreSQL) ─────────────────────────────────────
SUPABASE_DATABASE_URL = os.environ.get("SUPABASE_DATABASE_URL", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "textonflow-dev-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24 * 7  # 7 días

_db_conn = None
_db_lock = threading.Lock()

def get_db():
    global _db_conn
    with _db_lock:
        if not _PSYCOPG2_OK or not SUPABASE_DATABASE_URL:
            return None
        try:
            if _db_conn is None or _db_conn.closed:
                _db_conn = psycopg2.connect(SUPABASE_DATABASE_URL, connect_timeout=10)
                _db_conn.autocommit = True
            else:
                _db_conn.poll()
        except Exception:
            try:
                _db_conn = psycopg2.connect(SUPABASE_DATABASE_URL, connect_timeout=10)
                _db_conn.autocommit = True
            except Exception as e:
                logger.error(f"DB connection error: {e}")
                return None
        return _db_conn

def init_db():
    """Crea las tablas si no existen."""
    conn = get_db()
    if not conn:
        logger.warning("⚠️  Sin conexión a BD — modo sin base de datos")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    plan TEXT NOT NULL DEFAULT 'trial',
                    gemini_api_key TEXT DEFAULT NULL,
                    stripe_customer_id TEXT DEFAULT NULL,
                    renders_used INTEGER NOT NULL DEFAULT 0,
                    renders_limit INTEGER NOT NULL DEFAULT 20,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    stripe_subscription_id TEXT UNIQUE,
                    plan TEXT NOT NULL DEFAULT 'trial',
                    status TEXT NOT NULL DEFAULT 'active',
                    current_period_start TIMESTAMPTZ,
                    current_period_end TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS renders (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    endpoint TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    ip TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_renders_user_id ON renders(user_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            """)
        logger.info("✅ Base de datos inicializada correctamente")
    except Exception as e:
        logger.error(f"Error inicializando BD: {e}")

# ─── Auth helpers ─────────────────────────────────────────────────────────────
if _AUTH_OK:
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
else:
    pwd_context = None

def hash_password(password: str) -> str:
    if pwd_context:
        return pwd_context.hash(password)
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain: str, hashed: str) -> bool:
    if pwd_context:
        try:
            return pwd_context.verify(plain, hashed)
        except Exception:
            pass
    return hashlib.sha256(plain.encode()).hexdigest() == hashed

def create_jwt(user_id: str, email: str, plan: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": user_id, "email": email, "plan": plan, "exp": expire}
    if _AUTH_OK:
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return base64.b64encode(json.dumps({**payload, "exp": expire.isoformat()}).encode()).decode()

def decode_jwt(token: str) -> Optional[dict]:
    try:
        if _AUTH_OK:
            return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        data = json.loads(base64.b64decode(token.encode()).decode())
        return data
    except Exception:
        return None

PLAN_LIMITS = {
    "trial":   20,
    "starter": 1000,
    "agency":  10000,
    "admin":   999999,
}

# ─── Job store para generación de imágenes asíncrona ─────────────────────────
# Evita que Railway corte la conexión por timeout durante llamadas largas a Gemini
_IMAGE_JOBS: Dict[str, dict] = {}  # job_id → {status, result, error, ts}
_IMAGE_JOBS_LOCK = threading.Lock()

def _cleanup_old_jobs():
    """Elimina jobs de más de 10 minutos."""
    now = time.time()
    with _IMAGE_JOBS_LOCK:
        old = [k for k, v in _IMAGE_JOBS.items() if now - v.get('ts', 0) > 600]
        for k in old:
            del _IMAGE_JOBS[k]


# ─── Fuentes disponibles ────────────────────────────────────────────────────
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
    # ── MYKOZ Brand Fonts ──────────────────────────────────────────────────────
    "VariexLight":            "./fonts/Variex-Light.ttf",
    "ScholarRegular":         "./fonts/Scholar-Regular.otf",
    "ScholarItalic":          "./fonts/Scholar-Italic.otf",
    "GeomanistRegular":       "./fonts/Geomanist-Regular.otf",
    "GeomanistItalic":        "./fonts/Geomanist-Italic.otf",
    "GeomanistBold":          "./fonts/Geomanist-Bold.otf",
    "GeomanistBoldItalic":    "./fonts/Geomanist-Bold-Italic.otf",
}

# ─── Factores de escala por fuente (calculados al arrancar) ──────────────────
# Compara la altura real del glifo 'H' con la fuente de referencia Arial.
# Si una fuente rinde glyphs más pequeños, se compensa multiplicando font_size.
_MEASURE_SIZE = 100   # puntos de referencia para la medición
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
            logger.info(f"📐 Fuente de referencia: {p}")
            return p
    logger.warning("⚠️ No se encontró fuente de referencia — escala de fuentes desactivada")
    return ""
_REFERENCE_FONT_PATH = _get_reference_font_path()

def _compute_font_scale(font_path: str, reference_height: int) -> float:
    """Retorna el factor de escala para que la fuente rinda igual que Arial."""
    try:
        from PIL import ImageFont, ImageDraw, Image as _Img
        f = ImageFont.truetype(font_path, _MEASURE_SIZE)
        img = _Img.new("RGB", (400, 200), (255, 255, 255))
        d = ImageDraw.Draw(img)
        bbox = d.textbbox((0, 0), "H", font=f)
        h = max(1, bbox[3] - bbox[1])
        scale = reference_height / h
        # Limitar entre 0.8× y 5× para no romper fuentes ya calibradas
        return round(min(5.0, max(0.8, scale)), 3)
    except Exception as e:
        logger.warning(f"No se pudo medir {font_path}: {e}")
        return 1.0

def _build_font_scale_map() -> dict:
    if not _REFERENCE_FONT_PATH:
        logger.warning("📐 Sin fuente de referencia — escala automática desactivada")
        return {}
    try:
        from PIL import ImageFont, ImageDraw, Image as _Img
        ref = ImageFont.truetype(_REFERENCE_FONT_PATH, _MEASURE_SIZE)
        img = _Img.new("RGB", (400, 200), (255, 255, 255))
        d = ImageDraw.Draw(img)
        bbox = d.textbbox((0, 0), "H", font=ref)
        ref_h = max(1, bbox[3] - bbox[1])
        scales = {}
        for name, path in FONT_MAPPING.items():
            scales[name] = _compute_font_scale(path, ref_h)
        logger.info(f"📐 Escalas de fuentes: {scales}")
        return scales
    except Exception as e:
        logger.error(f"Error calculando escalas: {e}")
        return {}

FONT_SIZE_SCALE = _build_font_scale_map()

# ─── Fuente de emojis Noto (instalada por nixpacks) ──────────────────────────
NOTO_EMOJI_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/NotoColorEmoji.ttf",
]

def get_noto_emoji_font():
    for path in NOTO_EMOJI_PATHS:
        if os.path.exists(path):
            logger.info(f"✅ NotoColorEmoji encontrado: {path}")
            return path
    logger.warning("⚠️ NotoColorEmoji no encontrado en el sistema")
    return None


# ─── Session HTTP con reintentos para descargar emojis ──────────────────────
def build_retry_session() -> requests.Session:
    """Session HTTP robusta con reintentos automáticos."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "TextOnFlow-EmojiRenderer/1.0"})
    return session


# Source personalizado con reintentos
class RetryTwitterEmojiSource(TwitterEmojiSource):
    """TwitterEmojiSource con session HTTP que reintenta automáticamente."""

    def __init__(self):
        super().__init__()
        self._retry_session = build_retry_session()

    def request_url(self, url: str, **kwargs) -> bytes:
        try:
            response = self._retry_session.get(url, timeout=8, **kwargs)
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.warning(f"⚠️ Falló descarga emoji ({url}): {e}")
            raise


# ─── App FastAPI ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="TextOnFlow Image Personalizer",
    description="API para personalizar imágenes con texto y emojis para ManyChat",
    version="6.0.0",
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Inicializar base de datos ─────────────────────────────────────────────────
init_db()

# ── Almacenamiento persistente ────────────────────────────────────────────────
# STORAGE_PATH puede apuntar a un Railway Volume (ej: /mnt/storage)
# o a static/temp como fallback local (se borrará al reiniciar)
STORAGE_DIR = os.getenv("STORAGE_PATH", os.path.join("static", "temp"))
os.makedirs(STORAGE_DIR, exist_ok=True)
os.makedirs("output", exist_ok=True)
os.makedirs("fonts", exist_ok=True)
os.makedirs("static", exist_ok=True)

# ─── Contador global de imágenes generadas ───────────────────────────────────
_STATS_FILE = os.path.join(STORAGE_DIR, "tof_stats.json")
_STATS_LOCK = threading.Lock()

def _read_stats() -> dict:
    try:
        if os.path.exists(_STATS_FILE):
            with open(_STATS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"images_generated": 0}

def _increment_images_generated():
    with _STATS_LOCK:
        data = _read_stats()
        data["images_generated"] = data.get("images_generated", 0) + 1
        try:
            with open(_STATS_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"⚠️ No se pudo actualizar stats: {e}")

# ─── Rate limiting por IP ─────────────────────────────────────────────────────
PLAN_LIMITS: dict = {"free": 9999}        # imágenes/día por IP · sin límite temporal
_IP_USAGE: dict   = {}                    # {ip: {"date": "YYYY-MM-DD", "count": N}}
_IP_LOCK          = threading.Lock()

# ─── Superadmin ────────────────────────────────────────────────────────────────
_SUPERADMIN_EMAIL    = "admin@textonflow.com"
_SUPERADMIN_PWD_HASH = "8634d3c5b1865bc470198ac121dd36bc01cdb653f7bdff56e4e5273ee6df1ae1"
_ADMIN_SESSIONS: dict = {}               # {token: {"email": str, "expires": datetime}}
_ADMIN_LOCK           = threading.Lock()
_SESSION_TTL          = timedelta(days=30)

def _is_superadmin(request: "Request") -> bool:
    token = request.headers.get("X-Admin-Token", "")
    if not token:
        return False
    with _ADMIN_LOCK:
        session = _ADMIN_SESSIONS.get(token)
        if not session:
            return False
        if datetime.utcnow() > session["expires"]:
            _ADMIN_SESSIONS.pop(token, None)
            return False
        return True

def _get_client_ip(req: "Request") -> str:
    fwd = req.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (req.client.host or "unknown")

def _ip_usage_today(ip: str) -> dict:
    """Devuelve el registro del día de hoy para la IP (sin modificar)."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    rec   = _IP_USAGE.get(ip, {"date": today, "count": 0})
    if rec["date"] != today:
        rec = {"date": today, "count": 0}
    return rec

def _check_rate_limit(ip: str) -> tuple:
    """(used, limit, exceeded) — límite desactivado temporalmente"""
    with _IP_LOCK:
        rec   = _ip_usage_today(ip)
        limit = PLAN_LIMITS["free"]
        return rec["count"], limit, False

def _increment_ip_usage(ip: str) -> tuple:
    """Incrementa el contador y devuelve (used_after, limit)."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with _IP_LOCK:
        rec = _ip_usage_today(ip)
        rec["count"] += 1
        _IP_USAGE[ip] = {"date": today, "count": rec["count"]}
        return rec["count"], PLAN_LIMITS["free"]

def _reset_time_str() -> str:
    """Tiempo hasta medianoche UTC en formato 'Xh Ym'."""
    now      = datetime.utcnow()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    secs     = int((midnight - now).total_seconds())
    return f"{secs // 3600}h {(secs % 3600) // 60}m"

def _get_base_url(request: "Request") -> str:
    """Construye la URL base correctamente detrás de Railway/Cloudflare proxy."""
    explicit = os.getenv("BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    host  = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    if host:
        return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")

# Directorio donde se guardan los templates de contador regresivo
TIMER_TEMPLATES_DIR = os.getenv("TIMER_TEMPLATES_PATH", os.path.join(STORAGE_DIR, "timers"))
os.makedirs(TIMER_TEMPLATES_DIR, exist_ok=True)
TEMPLATES_API_DIR = os.getenv("TEMPLATES_API_PATH", os.path.join(STORAGE_DIR, "api_templates"))
os.makedirs(TEMPLATES_API_DIR, exist_ok=True)

# Directorio donde se guarda el primer acceso de cada usuario por template
TIMER_ACCESS_DIR = os.path.join(TIMER_TEMPLATES_DIR, "access")
os.makedirs(TIMER_ACCESS_DIR, exist_ok=True)

# Clave secreta para firmar URLs de timer (HMAC-SHA256)
TIMER_SECRET = os.getenv("TIMER_SECRET", "textonflow-timer-secret-2026")
# ─── Auto-actualización de archivos estáticos al iniciar ─────────────────────
# Railway descarga los últimos app.js, styles.css e index.html desde Replit
# en cada arranque. Para desactivar: TEXTONFLOW_AUTO_UPDATE=false en Railway vars.
_UPDATE_BASE = os.getenv(
    "TEXTONFLOW_UPDATE_URL",
    "https://a957156e-d374-4132-9cee-a0afec9e64e1-00-2u2btyprd2joh.riker.replit.dev/api/download"
)

def _auto_update_statics():
    if os.getenv("TEXTONFLOW_AUTO_UPDATE", "true").lower() == "false":
        logger.info("⏭️  Auto-update desactivado (TEXTONFLOW_AUTO_UPDATE=false)")
        return
    files = [
        (_UPDATE_BASE + "/app.js",           "static/app.js"),
        (_UPDATE_BASE + "/styles.css",       "static/styles.css"),
        (_UPDATE_BASE + "/index.html",       "index.html"),
        (_UPDATE_BASE + "/favicon.png",      "static/favicon.png"),
        (_UPDATE_BASE + "/logo-blanco.webp",    "static/logo-blanco.webp"),
        (_UPDATE_BASE + "/logo-negro.webp",     "static/logo-negro.webp"),
        (_UPDATE_BASE + "/logo-negro-new.png",  "static/logo-negro-new.png"),
        (_UPDATE_BASE + "/logo-blanco-new.png", "static/logo-blanco-new.png"),
        (_UPDATE_BASE + "/manual.html",      "static/manual.html"),
        (_UPDATE_BASE + "/privacidad.html",  "static/privacidad.html"),
        (_UPDATE_BASE + "/terminos.html",    "static/terminos.html"),
        (_UPDATE_BASE + "/faq.html",         "static/faq.html"),
        (_UPDATE_BASE + "/docs.html",        "static/docs.html"),
        (_UPDATE_BASE + "/precios.html",     "static/precios.html"),
        (_UPDATE_BASE + "/casos.html",       "static/casos.html"),
        (_UPDATE_BASE + "/previews/biblica.jpg",  "static/previews/biblica.jpg"),
        (_UPDATE_BASE + "/previews/plumilla.jpg", "static/previews/plumilla.jpg"),
    ]
    for url, dest in files:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200 and len(r.content) > 100:
                os.makedirs(os.path.dirname(dest), exist_ok=True) if os.path.dirname(dest) else None
                with open(dest, "wb") as f:
                    f.write(r.content)
                logger.info(f"✅ Auto-updated: {dest}")
            else:
                logger.warning(f"⚠️  Auto-update fallido ({r.status_code}): {url}")
        except Exception as e:
            logger.warning(f"⚠️  Auto-update error {dest}: {e}")

_auto_update_statics()

# ── Minificación de JS al iniciar ─────────────────────────────────────────────
def _minify_static_js():
    if not _RJSMIN_OK:
        logger.warning("⚠️  rjsmin no disponible — app.js se sirve sin minificar")
        return
    js_path = "static/app.js"
    if not os.path.exists(js_path):
        return
    try:
        with open(js_path, "r", encoding="utf-8") as f:
            original = f.read()
        minified = _rjsmin.jsmin(original, keep_bang_comments=False)
        reduction = (1 - len(minified) / max(len(original), 1)) * 100
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(minified)
        logger.info(f"✅ app.js minificado — {len(original)//1024}KB → {len(minified)//1024}KB ({reduction:.1f}% reducción)")
    except Exception as e:
        logger.warning(f"⚠️  Minificación JS fallida: {e}")

_minify_static_js()

app.mount("/fonts", StaticFiles(directory="fonts"), name="fonts")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── Páginas legales y de ayuda ───────────────────────────────────────────────
@app.get("/manual")
async def manual_page():
    return FileResponse("static/manual.html", media_type="text/html")

@app.get("/privacidad")
async def privacidad_page():
    return FileResponse("static/privacidad.html", media_type="text/html")

@app.get("/terminos")
async def terminos_page():
    return FileResponse("static/terminos.html", media_type="text/html")

@app.get("/docs")
async def docs_page():
    return FileResponse("static/docs.html", media_type="text/html")

@app.get("/.well-known/sg-hosted-ping")
async def sg_ping():
    return Response(content="OK", media_type="text/plain")

@app.get("/robots.txt")
async def robots():
    content = """User-agent: *
Allow: /
Sitemap: https://www.textonflow.com/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")

@app.get("/faq")
async def faq_page():
    return FileResponse("static/faq.html", media_type="text/html")

@app.get("/precios")
async def precios_page():
    return FileResponse("static/precios.html", media_type="text/html")

@app.get("/casos")
async def casos_page():
    return FileResponse("static/casos.html", media_type="text/html")

@app.get("/sitemap.xml")
async def sitemap():
    base = "https://www.textonflow.com"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{base}/</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>1.0</priority></url>
  <url><loc>{base}/manual</loc><lastmod>{today}</lastmod><changefreq>monthly</changefreq><priority>0.8</priority></url>
  <url><loc>{base}/faq</loc><lastmod>{today}</lastmod><changefreq>monthly</changefreq><priority>0.8</priority></url>
  <url><loc>{base}/privacidad</loc><lastmod>{today}</lastmod><changefreq>yearly</changefreq><priority>0.6</priority></url>
  <url><loc>{base}/terminos</loc><lastmod>{today}</lastmod><changefreq>yearly</changefreq><priority>0.6</priority></url>
  <url><loc>{base}/docs</loc><lastmod>{today}</lastmod><changefreq>monthly</changefreq><priority>0.9</priority></url>
  <url><loc>{base}/precios</loc><lastmod>{today}</lastmod><changefreq>monthly</changefreq><priority>0.9</priority></url>
  <url><loc>{base}/casos</loc><lastmod>{today}</lastmod><changefreq>monthly</changefreq><priority>0.8</priority></url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")


# ─── Proxy de imágenes (evita restricciones CORS del navegador) ───────────────
@app.get("/proxy-image")
def proxy_image(url: str):
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TextOnFlow/1.0)"},
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        return Response(
            content=resp.content,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No se pudo cargar la imagen: {e}")


# ─── Modelos ─────────────────────────────────────────────────────────────────
class TextField(BaseModel):
    text: str
    x: int
    y: int
    font_size: int = 60
    font_color: str = "#FFFFFF"
    rotation: int = 0
    skew_x: float = 0
    skew_y: float = 0
    # Tipo de relleno del fondo: "solid" | "gradient2" | "instagram"
    background_color_type: str = "solid"
    background_gradient_color2: str = "#FFFFFF"
    background_gradient_angle: int = 135
    # Tipo de borde: "solid" | "gradient2" | "instagram"
    background_stroke_type: str = "solid"
    background_stroke_gradient_color2: str = "#FFFFFF"
    background_stroke_gradient_angle: int = 135
    # Estilo de línea del borde: "solid" | "dashed" | "dotted"
    background_stroke_dash: str = "solid"
    line_spacing: int = 10
    alignment: str = "left"
    text_align: str = "center"
    font_name: str = "Arial-Bold"
    shadow_enabled: bool = False
    shadow_color: str = "#000000"
    shadow_opacity: int = 100
    shadow_offset_x: int = 2
    shadow_offset_y: int = 2
    shadow_blur: int = 0   # Gaussian blur radius (px) — 0 = sombra dura, >0 = sombra difusa
    shadow_blend_mode: str = "normal"  # Modos Photoshop: normal, multiply, darken, color_burn, linear_burn, overlay, soft_light, screen
    stroke_enabled: bool = False
    stroke_color: str = "#000000"
    stroke_opacity: int = 100
    stroke_width: int = 2
    background_enabled: bool = False
    background_color: str = "#000000"
    background_opacity: int = 80
    background_padding_top: Optional[int] = 10
    background_padding_right: Optional[int] = 10
    background_padding_bottom: Optional[int] = 10
    background_padding_left: Optional[int] = 10
    background_radius: int = 0
    background_stroke_color: str = "#FFFFFF"
    background_stroke_width: int = 0
    border_padding_top: Optional[int] = 10
    border_padding_right: Optional[int] = 20
    border_padding_bottom: Optional[int] = 10
    border_padding_left: Optional[int] = 20
    warp_style: str = "none"   # none|arc|arc_lower|arc_upper|arch|bulge|shell_lower|shell_upper|flag|wave|fish|rise|fisheye|inflate|squeeze|twist
    warp_bend: int = 0         # -100 a 100
    # ── Text Wrap automático ──────────────────────────────────────────────────
    text_wrap_enabled: bool = False   # Activa salto de línea automático por palabra
    text_wrap_padding: int = 60       # Margen L/R en px (el texto ocupa ancho - 2*padding)
    # ── Contador regresivo (opcional) ────────────────────────────────────────
    countdown_mode: Optional[str] = None            # "event" | "urgency"
    countdown_event_end_utc: Optional[str] = None   # "YYYY-MM-DDTHH:MM:SSZ"
    countdown_urgency_hours: Optional[float] = None
    countdown_ts_var: Optional[str] = None          # nombre del custom field ManyChat
    countdown_format: Optional[str] = "HH:MM:SS"   # "HH:MM:SS" | "DD:HH:MM:SS" | "HH:MM"
    countdown_expired_text: Optional[str] = None
    countdown_urgency_color: Optional[str] = None   # color cuando faltan N horas
    countdown_urgency_threshold_h: Optional[float] = 3.0  # horas umbral (default 3)


class CanvasShape(BaseModel):
    id: str = ""
    shape_type: str = "rect"   # rect | square | ellipse | circle | star12
    x: int = 0
    y: int = 0
    width: int = 100
    height: int = 100
    rotation: float = 0
    fill_color: str = "#667eea"
    fill_opacity: float = 0.8
    stroke_color: str = "#000000"
    stroke_width: int = 0
    stroke_opacity: float = 1.0
    z_index: int = 0
    cover_blur: int = 0


class ImageOverlay(BaseModel):
    src: str          # base64 data URL (data:image/png;base64,...) o URL http
    x: int = 0
    y: int = 0
    width: int = 100
    height: int = 100
    opacity: float = 1.0
    rotation: float = 0
    mask_type: str = "none"   # none | circle | ellipse | square | rect | star12
    mask_auto_fit: bool = True
    mask_radius: int = 0      # radio de esquinas para mask_type="rect"
    # Borde
    mask_border_width: int = 0
    mask_border_color: str = "#ffffff"
    mask_border_opacity: int = 100
    # Sombra
    mask_shadow_enabled: bool = False
    mask_shadow_color: str = "#000000"
    mask_shadow_opacity: int = 70
    mask_shadow_blur: int = 8
    mask_shadow_x: int = 0
    mask_shadow_y: int = 4


class MultiTextRequest(BaseModel):
    template_name: str
    texts: List[TextField]
    vars: Optional[Dict[str, str]] = None
    overlays: Optional[List[ImageOverlay]] = []
    shapes: Optional[List[CanvasShape]] = []
    filter_name: str = "none"
    render_scale: int = 1  # 1=rápido (ManyChat), 2=alta calidad (editor)
    watermark: bool = False  # Sello "textonflow.com" en esquina inferior derecha
    # ── Viñeta ──
    vignette_enabled: bool        = False
    vignette_color:   str         = "#000000"  # hex color
    vignette_opacity: float       = 0.6        # 0.0-1.0
    vignette_size:    float       = 50.0       # 0-100 (qué tanto cubre)
    vignette_sides:   Optional[List[str]] = None  # ['top','right','bottom','left','tl','tr','bl','br']
    vignette_filter:  str         = "none"     # tono: none|sepia|warm|cold|violet|green|red|golden|cyan
    # ── Multi-formato: artboard crop/zoom ─────────────────────────────────────
    format_width:  Optional[int]   = None  # Ancho del artboard del formato (px)
    format_height: Optional[int]   = None  # Alto del artboard del formato (px)
    img_pan_x:     float           = 0.0   # Offset X de la imagen en el artboard
    img_pan_y:     float           = 0.0   # Offset Y de la imagen en el artboard
    img_zoom:      float           = 1.0   # Factor de zoom de la imagen


INSTAGRAM_GRADIENT = [
    (240, 148,  51, 255),
    (230, 104,  60, 255),
    (220,  39,  67, 255),
    (204,  35, 102, 255),
    (188,  24, 136, 255),
]

# ─── Gradiente Negro profundo (fondo premium) ─────────────────────────────────
NEGRO_GRADIENT = [
    (  0,   0,   0, 255),
    ( 18,  18,  26, 255),
    ( 40,  40,  55, 255),
    ( 18,  18,  26, 255),
    (  0,   0,   0, 255),
]

# ─── Gradiente Metálico / Chrome ─────────────────────────────────────────────
METALICO_GRADIENT = [
    ( 80,  80,  90, 255),
    (190, 190, 200, 255),
    (240, 240, 248, 255),
    (200, 200, 212, 255),
    (100, 100, 112, 255),
    (200, 200, 212, 255),
    (240, 240, 248, 255),
]


def make_gradient_image(w: int, h: int, colors: list, angle_deg: float = 135) -> "Image.Image":
    """Crea una imagen RGBA con degradado lineal de N colores. Requiere numpy."""
    w, h = max(1, int(w)), max(1, int(h))
    if not _NUMPY_OK or len(colors) < 2:
        return Image.new("RGBA", (w, h), colors[0] if colors else (0, 0, 0, 0))
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    xs = np.linspace(0.0, 1.0, w)
    ys = np.linspace(0.0, 1.0, h)
    xx, yy = np.meshgrid(xs, ys)
    t = xx * cos_a + yy * sin_a
    t_min, t_max = float(t.min()), float(t.max())
    t = (t - t_min) / max(t_max - t_min, 1e-10)
    n = len(colors)
    result = np.zeros((h, w, 4), dtype=np.float64)
    for i in range(n - 1):
        t0 = i / (n - 1)
        t1 = (i + 1) / (n - 1)
        mask = (t >= t0) & (t <= t1)
        local_t = np.where(mask, (t - t0) / max(t1 - t0, 1e-10), 0.0)
        c1 = np.array(colors[i],     dtype=np.float64)
        c2 = np.array(colors[i + 1], dtype=np.float64)
        for ch in range(4):
            result[:, :, ch] += mask * (c1[ch] + local_t * (c2[ch] - c1[ch]))
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8), "RGBA")


def apply_gradient_bg(layer: "Image.Image", bx1, by1, bx2, by2, radius, colors, angle_deg=135):
    """Rellena un rect redondeado con degradado sobre `layer` (RGBA, in-place)."""
    bx1, by1, bx2, by2 = int(bx1), int(by1), int(bx2), int(by2)
    w, h = bx2 - bx1, by2 - by1
    if w <= 0 or h <= 0:
        return
    grad = make_gradient_image(w, h, colors, angle_deg)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    r = min(int(radius), max(0, (min(w, h) - 1) // 2))
    if r > 0:
        md.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=r, fill=255)
    else:
        md.rectangle([(0, 0), (w - 1, h - 1)], fill=255)
    layer.paste(grad, (bx1, by1), mask)


def apply_gradient_stroke(layer: "Image.Image", bx1, by1, bx2, by2, radius, stroke_w, colors, angle_deg=135):
    """Dibuja un borde (anillo) de rect redondeado con degradado sobre `layer` (RGBA, in-place)."""
    bx1, by1, bx2, by2, stroke_w = int(bx1), int(by1), int(bx2), int(by2), int(stroke_w)
    w, h = bx2 - bx1, by2 - by1
    if w <= 0 or h <= 0 or stroke_w <= 0:
        return
    grad = make_gradient_image(w, h, colors, angle_deg)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    # Clamp radius so Pillow never raises "Radius is too large"
    r = min(int(radius), max(0, (min(w, h) - 1) // 2))
    # Forma exterior
    if r > 0:
        md.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=r, fill=255)
    else:
        md.rectangle([(0, 0), (w - 1, h - 1)], fill=255)
    # Recortar interior (crear anillo)
    ix1, iy1 = stroke_w, stroke_w
    ix2, iy2 = w - 1 - stroke_w, h - 1 - stroke_w
    if ix2 > ix1 and iy2 > iy1:
        iw, ih = ix2 - ix1, iy2 - iy1
        ir = min(max(0, r - stroke_w), max(0, (min(iw, ih) - 1) // 2))
        if ir > 0:
            md.rounded_rectangle([(ix1, iy1), (ix2, iy2)], radius=ir, fill=0)
        else:
            md.rectangle([(ix1, iy1), (ix2, iy2)], fill=0)
    layer.paste(grad, (bx1, by1), mask)


def _draw_dashed_border(draw, x1, y1, x2, y2, radius, stroke_w, color, dash_style):
    """Dibuja borde sólido, guiones o puntos sobre un rectángulo (opcionalmente redondeado)."""
    import math
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    r = min(int(radius), max(0, (min(x2-x1, y2-y1) - 1) // 2))

    if dash_style not in ('dashed', 'dotted'):
        if r > 0:
            draw.rounded_rectangle([(x1,y1),(x2,y2)], radius=r, outline=color, width=stroke_w)
        else:
            draw.rectangle([(x1,y1),(x2,y2)], outline=color, width=stroke_w)
        return

    # Tamaño del dash/gap en píxeles (ya a escala 2×)
    if dash_style == 'dotted':
        dash_on  = max(stroke_w, 3)
        dash_off = max(stroke_w * 3, 8)
    else:  # 'dashed'
        dash_on  = max(stroke_w * 7, 18)
        dash_off = max(stroke_w * 4, 10)

    # Construir lista de puntos del perímetro con paso ~1 px
    pts = []
    def add_line(xa, ya, xb, yb):
        n = max(1, int(max(abs(xb-xa), abs(yb-ya))))
        for i in range(n):
            t = i / n
            pts.append((xa + (xb-xa)*t, ya + (yb-ya)*t))

    def add_arc(cx, cy, rad, a_start, a_end):
        if rad <= 0:
            return
        arc_len = abs(a_end - a_start) * math.pi * rad / 180
        steps = max(2, int(arc_len))
        for i in range(steps + 1):
            a = math.radians(a_start + (a_end - a_start) * i / steps)
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))

    # Trazar perímetro en sentido horario
    add_line(x1+r, y1, x2-r, y1)          # top
    add_arc(x2-r, y1+r, r, -90, 0)        # top-right
    add_line(x2, y1+r, x2, y2-r)          # right
    add_arc(x2-r, y2-r, r, 0, 90)         # bottom-right
    add_line(x2-r, y2, x1+r, y2)          # bottom
    add_arc(x1+r, y2-r, r, 90, 180)       # bottom-left
    add_line(x1, y2-r, x1, y1+r)          # left
    add_arc(x1+r, y1+r, r, 180, 270)      # top-left

    # Dibujar dash pattern sobre los puntos del perímetro
    n = len(pts)
    i = 0
    drawing = True
    while i < n:
        seg = dash_on if drawing else dash_off
        seg_i = int(seg)
        if drawing:
            end_i = min(i + seg_i, n - 1)
            for j in range(i, end_i):
                p1 = (round(pts[j][0]), round(pts[j][1]))
                p2 = (round(pts[min(j+1, n-1)][0]), round(pts[min(j+1, n-1)][1]))
                draw.line([p1, p2], fill=color, width=stroke_w)
        i += seg_i
        drawing = not drawing


def _ig_colors(alpha: int) -> list:
    """Devuelve los 5 stops de Instagram con la opacidad dada."""
    return [(r, g, b, alpha) for r, g, b, _ in INSTAGRAM_GRADIENT]

def _negro_colors(alpha: int) -> list:
    return [(r, g, b, alpha) for r, g, b, _ in NEGRO_GRADIENT]

def _metal_colors(alpha: int) -> list:
    return [(r, g, b, alpha) for r, g, b, _ in METALICO_GRADIENT]


def apply_filter(img: Image.Image, filter_name: str) -> Image.Image:
    """Aplica un filtro de color/tono a la imagen base (antes de pintar texto)."""
    if not filter_name or filter_name == "none":
        return img
    try:
        alpha = img.split()[3] if img.mode == "RGBA" else None
        rgb = img.convert("RGB")

        def enh(im, brightness=1.0, contrast=1.0, saturation=1.0):
            if brightness != 1.0:
                im = ImageEnhance.Brightness(im).enhance(brightness)
            if contrast != 1.0:
                im = ImageEnhance.Contrast(im).enhance(contrast)
            if saturation != 1.0:
                im = ImageEnhance.Color(im).enhance(saturation)
            return im

        def ch(im, r=1.0, g=1.0, b=1.0):
            if not _NUMPY_OK:
                return im
            arr = np.array(im).astype(float)
            arr[:, :, 0] = np.clip(arr[:, :, 0] * r, 0, 255)
            arr[:, :, 1] = np.clip(arr[:, :, 1] * g, 0, 255)
            arr[:, :, 2] = np.clip(arr[:, :, 2] * b, 0, 255)
            return Image.fromarray(arr.astype(np.uint8))

        FILTERS = {
            # ── Instagram ──────────────────────────────────────────────────────
            "clarendon":     lambda im: ch(enh(im, contrast=1.2,  saturation=1.35), r=0.90, b=1.15),
            "gingham":       lambda im: enh(ch(im, r=1.05, b=0.95), brightness=1.05, saturation=0.85),
            "juno":          lambda im: ch(enh(im, saturation=1.2),  r=1.15, g=1.05, b=0.90),
            "lark":          lambda im: enh(ch(im, r=1.05, b=1.10), brightness=1.10, saturation=0.90),
            "mayfair":       lambda im: ch(enh(im, brightness=1.05, saturation=1.10), r=1.10, b=0.90),
            "moon":          lambda im: enh(im.convert("L").convert("RGB"), contrast=1.10),
            "nashville":     lambda im: ch(enh(im, brightness=1.05, saturation=0.90), r=1.10, g=0.90, b=0.85),
            "perpetua":      lambda im: ch(enh(im, saturation=0.90), r=0.95, g=1.05, b=1.05),
            "reyes":         lambda im: enh(ch(im, r=1.10, g=1.05, b=0.95), brightness=1.10, contrast=0.85, saturation=0.75),
            "rise":          lambda im: ch(enh(im, brightness=1.10, saturation=0.90), r=1.15, g=1.05, b=0.95),
            "slumber":       lambda im: enh(ch(im, r=0.85, b=0.95), saturation=0.60, brightness=1.05),
            "valencia":      lambda im: ch(enh(im, contrast=0.90, saturation=0.90), r=1.15, g=1.05, b=0.90),
            "walden":        lambda im: ch(enh(im, brightness=1.10, saturation=0.80), r=0.95, b=1.10),
            "xpro2":         lambda im: ch(enh(im, contrast=1.30, saturation=1.20), r=0.85, g=0.90, b=1.00),
            "inkwell":       lambda im: enh(im.convert("L").convert("RGB"), contrast=1.05),
            "toaster":       lambda im: ch(enh(im, contrast=1.30, saturation=0.90), r=1.20, g=0.85, b=0.70),
            "lo_fi":         lambda im: ch(enh(im, contrast=1.40, saturation=1.30), r=1.10, g=0.90, b=0.80),
            "hefe":          lambda im: ch(enh(im, brightness=1.05, contrast=1.20, saturation=1.30), r=1.15, b=0.80),
            # ── Photoshop / LUT ────────────────────────────────────────────────
            "bleach_bypass": lambda im: enh(ch(im, r=0.90, g=0.90, b=0.90), contrast=1.30, saturation=0.40),
            "candlelight":   lambda im: ch(enh(im, brightness=1.10), r=1.25, g=1.05, b=0.70),
            "crisp_warm":    lambda im: ch(enh(im, contrast=1.10, saturation=1.10), r=1.10, b=0.90),
            "crisp_winter":  lambda im: ch(enh(im, contrast=1.10, saturation=0.95), r=0.90, g=0.95, b=1.15),
            "fall_colors":   lambda im: ch(enh(im, contrast=1.05, saturation=1.20), r=1.15, g=1.05, b=0.80),
            "foggy_night":   lambda im: ch(enh(im, brightness=0.85, saturation=0.70), r=0.90, b=1.10),
            "horror_blue":   lambda im: ch(enh(im, brightness=0.90, saturation=0.80, contrast=1.10), r=0.80, g=0.85, b=1.20),
            "late_sunset":   lambda im: ch(enh(im, brightness=0.95, saturation=1.10), r=1.20, g=0.90, b=0.75),
            "moonlight_ps":  lambda im: ch(enh(im, brightness=0.90, saturation=0.60), r=0.85, g=0.90, b=1.15),
            "soft_warming":  lambda im: ch(enh(im, brightness=1.05, saturation=0.95), r=1.10, b=0.90),
            "teal_orange":   lambda im: ch(enh(im, contrast=1.15, saturation=1.10), r=1.15, g=0.90, b=0.85),
            "fuji_eterna":   lambda im: enh(ch(im, r=0.95, b=1.05), saturation=0.90, contrast=0.95),
            "filmstock":     lambda im: ch(enh(im, saturation=0.95, contrast=1.05), r=1.05),
            "tension_green": lambda im: ch(enh(im, contrast=1.10, saturation=0.90), r=0.90, g=1.10, b=0.85),
            "edgy_amber":    lambda im: ch(enh(im, contrast=1.20, saturation=0.80), r=1.15, b=0.75),
            "drop_blues":    lambda im: ch(enh(im, contrast=1.05, saturation=0.85), r=0.90, g=0.95, b=1.20),
            "2strip":        lambda im: ch(enh(im, saturation=0.70, contrast=1.15), r=1.10, g=0.90, b=0.80),
            "3strip":        lambda im: ch(enh(im, saturation=1.10, contrast=1.10), r=1.05),
            "futuristic":    lambda im: ch(enh(im, brightness=0.85, saturation=0.50, contrast=1.20), r=0.75, g=0.85, b=1.30),
            "night_from_day":lambda im: ch(enh(im, brightness=0.80, saturation=0.55, contrast=1.15), r=0.80, g=0.90, b=1.25),
            "fuji_f125_2393":lambda im: ch(enh(im, saturation=0.85, contrast=1.05, brightness=1.02), r=1.05, g=1.00, b=0.92),
            "fuji_f125_2395":lambda im: ch(enh(im, saturation=0.80, contrast=1.08, brightness=1.03), r=1.03, g=1.02, b=0.90),
            "fuji_reala":    lambda im: ch(enh(im, saturation=0.88, contrast=1.00, brightness=1.02), r=1.02, g=1.00, b=0.95),
            "kodak_5205":    lambda im: ch(enh(im, saturation=1.05, contrast=1.10, brightness=0.98), r=1.08, g=1.00, b=0.88),
            "kodak_5218_2383":lambda im: ch(enh(im, saturation=0.90, contrast=1.12, brightness=0.95), r=1.05, g=0.98, b=0.85),
            "kodak_5218_2395":lambda im: ch(enh(im, saturation=0.92, contrast=1.10, brightness=0.96), r=1.06, g=0.99, b=0.87),
        }

        fn = FILTERS.get(filter_name)
        if fn:
            result = fn(rgb)
            if alpha is not None:
                result = result.convert("RGBA")
                result.putalpha(alpha)
                return result
            return result.convert("RGBA")
    except Exception as e:
        logger.warning(f"⚠️ Error aplicando filtro '{filter_name}': {e}")
    return img


def apply_vignette(
    img: Image.Image,
    color: str = "#000000",
    opacity: float = 0.6,
    size: float = 50.0,
    sides: list = None,
    tone: str = "none",
) -> Image.Image:
    """Aplica efecto viñeta multi-lado con color, tamaño y tono configurables."""
    try:
        if sides is None:
            sides = ["top", "right", "bottom", "left"]

        # ── Color base (hex → RGB, con override de tono) ──────────────────
        TONE_RGB = {
            "sepia":  (0.75, 0.55, 0.30),
            "warm":   (0.80, 0.35, 0.05),
            "cold":   (0.05, 0.25, 0.80),
            "violet": (0.45, 0.05, 0.80),
            "green":  (0.05, 0.65, 0.15),
            "red":    (0.80, 0.05, 0.08),
            "golden": (0.85, 0.65, 0.05),
            "cyan":   (0.05, 0.65, 0.80),
        }
        if tone in TONE_RGB:
            rf, gf, bf = TONE_RGB[tone]
            rv, gv, bv = int(rf * 255), int(gf * 255), int(bf * 255)
        else:
            # Parse hex color (#rrggbb)
            hx = color.lstrip("#")
            if len(hx) == 6:
                rv, gv, bv = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
            else:
                rv, gv, bv = 0, 0, 0

        w, h = img.size
        Y_idx = np.arange(h).reshape(-1, 1).astype(np.float32)
        X_idx = np.arange(w).reshape(1, -1).astype(np.float32)

        # innerPct: qué % desde el borde ocupa el gradiente (maps size 0-100 → 20-80%)
        inner_pct = (20 + size * 0.6) / 100.0   # 0.20–0.80

        mask = np.zeros((h, w), dtype=np.float32)

        all4 = ["top", "right", "bottom", "left"]
        has_all4    = all(s in sides for s in all4)
        has_corners = any(s in sides for s in ["tl", "tr", "bl", "br"])

        def _fade_side(arr_norm):
            """Convierte distancia normalizada desde el borde a alpha (0=centro, 1=borde)."""
            return np.clip(arr_norm / inner_pct, 0.0, 1.0) ** 2

        # Caso clásico: 4 lados sin esquinas → radial suave desde centro
        if has_all4 and not has_corners:
            cx, cy = w / 2.0, h / 2.0
            dist = np.sqrt(((X_idx - cx) / cx) ** 2 + ((Y_idx - cy) / cy) ** 2)
            inner_r = 1.0 - inner_pct  # radio de la zona libre de viñeta
            v = np.clip((dist - inner_r) / (1.0 - inner_r), 0.0, 1.0) ** 2
            mask = np.maximum(mask, v)
        else:
            # Lados lineales
            if "top" in sides:
                d = (h - Y_idx - 1) / max(h - 1, 1)   # 1=top edge → 0=bottom
                mask = np.maximum(mask, _fade_side(1 - d))
            if "bottom" in sides:
                d = Y_idx / max(h - 1, 1)
                mask = np.maximum(mask, _fade_side(1 - d))
            if "left" in sides:
                d = (w - X_idx - 1) / max(w - 1, 1)
                mask = np.maximum(mask, _fade_side(1 - d))
            if "right" in sides:
                d = X_idx / max(w - 1, 1)
                mask = np.maximum(mask, _fade_side(1 - d))

        # Esquinas (radial desde la esquina)
        corner_r = inner_pct * 1.55
        def _corner_mask(cx, cy):
            dist = np.sqrt(((X_idx - cx) / max(w, 1)) ** 2 + ((Y_idx - cy) / max(h, 1)) ** 2)
            return np.clip((corner_r - dist) / corner_r, 0.0, 1.0) ** 2

        if "tl" in sides: mask = np.maximum(mask, _corner_mask(0, 0))
        if "tr" in sides: mask = np.maximum(mask, _corner_mask(w, 0))
        if "bl" in sides: mask = np.maximum(mask, _corner_mask(0, h))
        if "br" in sides: mask = np.maximum(mask, _corner_mask(w, h))

        # ── Construir capa RGBA de viñeta ──────────────────────────────────
        alpha_arr = (mask * opacity * 255).clip(0, 255).astype(np.uint8)
        vign_arr  = np.zeros((h, w, 4), dtype=np.uint8)
        vign_arr[:, :, 0] = rv
        vign_arr[:, :, 1] = gv
        vign_arr[:, :, 2] = bv
        vign_arr[:, :, 3] = alpha_arr

        vign_layer = Image.fromarray(vign_arr, "RGBA")
        base       = img.convert("RGBA")
        result     = Image.alpha_composite(base, vign_layer)

        return result.convert("RGB") if img.mode == "RGB" else result
    except Exception as e:
        logger.warning(f"⚠️ Error aplicando viñeta: {e}")
        return img


# ─── Utilidades de color ──────────────────────────────────────────────────────
def parse_color(color_str: str) -> tuple:
    color_str = color_str.strip()
    if color_str.startswith("rgba("):
        values = color_str.replace("rgba(", "").replace(")", "").split(",")
        r, g, b = int(values[0].strip()), int(values[1].strip()), int(values[2].strip())
        a = float(values[3].strip())
        return (r, g, b, int(a * 255))
    hex_color = color_str.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def parse_color_with_opacity(color_str: str, opacity: int = 100) -> tuple:
    color_str = color_str.strip()
    if color_str.startswith("rgba("):
        return parse_color(color_str)
    hex_color = color_str.lstrip("#")
    r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    a = int(255 * (opacity / 100))
    return (r, g, b, a)


# ─── Fuente de emoji (singleton) ─────────────────────────────────────────────
_emoji_source = None

def get_emoji_source():
    global _emoji_source
    if _emoji_source is None:
        try:
            _emoji_source = RetryTwitterEmojiSource()
            logger.info("✅ Emoji source: RetryTwitterEmojiSource (Twemoji CDN)")
        except Exception as e:
            logger.error(f"❌ No se pudo inicializar emoji source: {e}")
            _emoji_source = EmojiCDNSource()
    return _emoji_source


# ─── Modos de fusión tipo Photoshop ──────────────────────────────────────────
def apply_blend_mode(base: Image.Image, overlay: Image.Image, mode: str) -> Image.Image:
    """Compone overlay sobre base usando el modo de fusión indicado.
    
    Soporta: normal, multiply, darken, color_burn, linear_burn,
             overlay, soft_light, screen.
    Requiere que base y overlay sean RGBA del mismo tamaño.
    """
    if mode == "normal" or not _NUMPY_OK:
        base_copy = base.copy()
        base_copy.paste(overlay, (0, 0), overlay)
        return base_copy

    b = np.array(base, dtype=np.float32) / 255.0      # shape (H, W, 4)
    o = np.array(overlay, dtype=np.float32) / 255.0

    B = b[:, :, :3]   # base RGB
    A = o[:, :, :3]   # overlay RGB
    alpha = o[:, :, 3:4]  # overlay alpha (broadcast-able)

    if mode == "multiply":
        blended = B * A
    elif mode == "screen":
        blended = 1.0 - (1.0 - B) * (1.0 - A)
    elif mode == "darken":
        blended = np.minimum(B, A)
    elif mode == "color_burn":
        safe_A = np.where(A < 1e-6, 1e-6, A)
        blended = np.clip(1.0 - (1.0 - B) / safe_A, 0.0, 1.0)
    elif mode == "linear_burn":
        blended = np.clip(B + A - 1.0, 0.0, 1.0)
    elif mode == "overlay":
        blended = np.where(B < 0.5, 2.0 * B * A, 1.0 - 2.0 * (1.0 - B) * (1.0 - A))
    elif mode == "soft_light":
        # Fórmula estándar W3C Soft Light
        def D(cb):
            return np.where(cb <= 0.25,
                            ((16.0 * cb - 12.0) * cb + 4.0) * cb,
                            np.sqrt(np.clip(cb, 0.0, 1.0)))
        blended = np.where(
            A <= 0.5,
            B - (1.0 - 2.0 * A) * B * (1.0 - B),
            B + (2.0 * A - 1.0) * (D(B) - B)
        )
    else:
        blended = A  # fallback: normal sin alpha

    # Componer: resultado = base lerpeado con blended según alpha del overlay
    result_rgb = np.clip(B * (1.0 - alpha) + blended * alpha, 0.0, 1.0)

    result_arr = b.copy()
    result_arr[:, :, :3] = result_rgb
    # Alpha del resultado: mantener el base completamente opaco
    result_arr[:, :, 3] = b[:, :, 3]

    return Image.fromarray((result_arr * 255).astype(np.uint8), "RGBA")


# ─── Warp de texto tipo Photoshop ────────────────────────────────────────────

def _bilinear_sample(region: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """Interpolación bilineal de region en coordenadas flotantes (sx, sy)."""
    H, W = region.shape[:2]
    x0 = np.floor(sx).astype(np.int32)
    y0 = np.floor(sy).astype(np.int32)
    fx = np.clip((sx - x0.astype(np.float32))[..., np.newaxis], 0.0, 1.0)
    fy = np.clip((sy - y0.astype(np.float32))[..., np.newaxis], 0.0, 1.0)
    x0c = np.clip(x0, 0, W - 1); x1c = np.clip(x0 + 1, 0, W - 1)
    y0c = np.clip(y0, 0, H - 1); y1c = np.clip(y0 + 1, 0, H - 1)
    c00 = region[y0c, x0c].astype(np.float32)
    c10 = region[y0c, x1c].astype(np.float32)
    c01 = region[y1c, x0c].astype(np.float32)
    c11 = region[y1c, x1c].astype(np.float32)
    return c00 * (1 - fx) * (1 - fy) + c10 * fx * (1 - fy) + c01 * (1 - fx) * fy + c11 * fx * fy


def _warp_displacement(style: str, u: np.ndarray, v: np.ndarray, bend: float):
    """Desplazamiento inverso (du, dv) en coords normalizadas para cada estilo Photoshop.
    
    Para cada píxel destino en coordenadas normalizadas (u, v) ∈ [-1,1],
    devuelve el desplazamiento tal que:  src_norm = dest_norm + (du, dv)
    Luego:  src_pixel = (u_src * half_w + cx,  v_src * half_h + cy)
    """
    z = np.zeros_like(u)
    uc = np.clip(u, -1.0, 1.0)
    vc = np.clip(v, -1.0, 1.0)

    if style == 'arc':
        dv = -bend * (1.0 - uc ** 2) * 0.75
        du = bend * uc * np.abs(vc) * 0.18
        return du, dv

    elif style == 'arc_lower':
        dv = -bend * (1.0 - uc ** 2) * np.maximum(0.0, vc) * 1.3
        return z.copy(), dv

    elif style == 'arc_upper':
        dv = -bend * (1.0 - uc ** 2) * np.maximum(0.0, -vc) * 1.3
        return z.copy(), dv

    elif style == 'arch':
        dv = -bend * (1.0 - uc ** 2) * (1.0 + vc) * 0.45
        du = bend * uc * 0.10
        return du, dv

    elif style == 'bulge':
        r2 = uc ** 2 + vc ** 2
        f = bend * np.clip(1.0 - r2, 0, 1) * 0.85
        return -uc * f, -vc * f

    elif style == 'shell_lower':
        return z.copy(), bend * uc ** 2 * 0.85

    elif style == 'shell_upper':
        return z.copy(), -bend * uc ** 2 * 0.85

    elif style == 'flag':
        t = (uc + 1.0) * 0.5   # 0→1 left-to-right
        dv = -bend * np.sin(t * 2.0 * math.pi) * 0.65
        return z.copy(), dv

    elif style == 'wave':
        dv = -bend * np.sin(uc * 1.5 * math.pi) * 0.65
        du = -bend * np.sin(vc * math.pi * 0.5) * 0.10
        return du, dv

    elif style == 'fish':
        dv = -bend * np.sin(uc * math.pi) * 0.60
        du = -bend * vc * np.cos(uc * math.pi * 0.5) * 0.28
        return du, dv

    elif style == 'rise':
        dv = -bend * (uc + 1.0) * 0.5 * 0.75
        return z.copy(), dv

    elif style == 'fisheye':
        r = np.sqrt(uc ** 2 + vc ** 2)
        f = 1.0 + bend * np.clip(1.0 - r, 0, 1) * 0.95
        safe_f = np.where(np.abs(f) < 0.05, np.sign(f + 1e-9) * 0.05, f)
        return -(uc * (1.0 - 1.0 / safe_f)), -(vc * (1.0 - 1.0 / safe_f))

    elif style == 'inflate':
        r = np.sqrt(uc ** 2 + vc ** 2)
        f = bend * np.sin(np.clip(r, 0, 1) * math.pi * 0.5) * 0.85
        return uc * f, vc * f

    elif style == 'squeeze':
        squeeze = bend * np.cos(vc * math.pi * 0.5) * 0.65
        return u * squeeze, z.copy()

    elif style == 'twist':
        r = np.sqrt(uc ** 2 + vc ** 2)
        angle = bend * np.clip(1.0 - r, 0, 1) * math.pi * 0.85
        cos_a, sin_a = np.cos(angle), np.sin(angle)
        u_rot = uc * cos_a - vc * sin_a
        v_rot = uc * sin_a + vc * cos_a
        return uc - u_rot, vc - v_rot

    return z, z


def _star_polygon(cx: float, cy: float, outer_r: float, inner_r: float, n: int = 12):
    """Genera los vértices de una estrella de n puntas centrada en (cx, cy)."""
    pts = []
    for i in range(2 * n):
        angle = math.pi * i / n - math.pi / 2
        r = outer_r if i % 2 == 0 else inner_r
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


def _apply_overlay_mask(img: Image.Image, mask_type: str, radius: int = 0) -> Image.Image:
    """Aplica una máscara de recorte a la imagen del overlay."""
    if mask_type == "none":
        return img
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    if mask_type == "circle":
        d = min(w, h)
        x0, y0 = (w - d) // 2, (h - d) // 2
        draw.ellipse([x0, y0, x0 + d - 1, y0 + d - 1], fill=255)
    elif mask_type == "ellipse":
        draw.ellipse([0, 0, w - 1, h - 1], fill=255)
    elif mask_type == "square":
        d = min(w, h)
        x0, y0 = (w - d) // 2, (h - d) // 2
        draw.rectangle([x0, y0, x0 + d - 1, y0 + d - 1], fill=255)
    elif mask_type == "rect":
        if radius > 0:
            draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
        else:
            draw.rectangle([0, 0, w - 1, h - 1], fill=255)
    elif mask_type == "star12":
        pts = _star_polygon(w / 2, h / 2, min(w, h) / 2 - 1, min(w, h) / 2 * 0.78 - 1, n=12)
        draw.polygon(pts, fill=255)
    else:
        return img
    result = img.copy().convert("RGBA")
    r_ch, g_ch, b_ch, a_ch = result.split()
    combined = Image.fromarray(
        np.minimum(np.array(a_ch), np.array(mask)).astype(np.uint8)
    )
    result.putalpha(combined)
    return result


def _apply_overlay_border(img: Image.Image, mask_type: str, border_width: int,
                          border_color: tuple, radius: int = 0):
    """Dibuja un borde FUERA de la máscara.  Devuelve (img_expandida, expand_px)."""
    if border_width <= 0:
        return img, 0
    w, h = img.size
    bw = border_width
    hw = bw // 2          # desplazamiento para centrar el trazo justo fuera del borde
    exp = bw + 2          # expansión del canvas (margen extra para el trazo)
    new_w, new_h = w + 2 * exp, h + 2 * exp

    result = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    result.paste(img, (exp, exp), img)

    if mask_type == "star12":
        # Para la estrella usamos dilatación morfológica con MaxFilter
        mask_layer = Image.new("L", (new_w, new_h), 0)
        mdraw = ImageDraw.Draw(mask_layer)
        pts = _star_polygon(exp + w / 2, exp + h / 2,
                            min(w, h) / 2, min(w, h) / 2 * 0.78, n=12)
        mdraw.polygon(pts, fill=255)
        kernel = max(3, bw * 2 + 1)
        from PIL import ImageFilter as _IF
        dilated = mask_layer.filter(_IF.MaxFilter(kernel))
        border_alpha = Image.fromarray(
            np.clip(np.array(dilated).astype(int) - np.array(mask_layer).astype(int),
                    0, 255).astype(np.uint8)
        )
        r_b, g_b, b_b, a_b = border_color
        border_layer = Image.new("RGBA", (new_w, new_h), (r_b, g_b, b_b, 0))
        border_layer.putalpha(border_alpha)
        result = Image.alpha_composite(border_layer, result)
    else:
        draw = ImageDraw.Draw(result)
        if mask_type == "circle":
            d = min(w, h)
            x0, y0 = (w - d) // 2, (h - d) // 2
            draw.ellipse([exp + x0 - hw, exp + y0 - hw,
                          exp + x0 + d - 1 + hw, exp + y0 + d - 1 + hw],
                         outline=border_color, width=bw)
        elif mask_type == "ellipse":
            draw.ellipse([exp - hw, exp - hw,
                          exp + w - 1 + hw, exp + h - 1 + hw],
                         outline=border_color, width=bw)
        elif mask_type == "square":
            d = min(w, h)
            x0, y0 = (w - d) // 2, (h - d) // 2
            draw.rectangle([exp + x0 - hw, exp + y0 - hw,
                            exp + x0 + d - 1 + hw, exp + y0 + d - 1 + hw],
                           outline=border_color, width=bw)
        else:
            if radius > 0 and mask_type == "rect":
                draw.rounded_rectangle([exp - hw, exp - hw,
                                        exp + w - 1 + hw, exp + h - 1 + hw],
                                       radius=radius + hw,
                                       outline=border_color, width=bw)
            else:
                draw.rectangle([exp - hw, exp - hw,
                                exp + w - 1 + hw, exp + h - 1 + hw],
                               outline=border_color, width=bw)
    return result, exp


def _render_canvas_shape(image: Image.Image, shape: "CanvasShape") -> None:
    """Dibuja una Forma (rect/ellipse/star12) sobre el canvas con trazo exterior."""
    sw, sh = max(1, shape.width), max(1, shape.height)
    fc = parse_color_with_opacity(shape.fill_color, int(shape.fill_opacity * 100))
    sc_color = parse_color_with_opacity(shape.stroke_color, int(shape.stroke_opacity * 100))
    stk = shape.stroke_width

    # Blur de fondo: aplica GaussianBlur a la región de la imagen antes de pintar el relleno
    blur_val = getattr(shape, 'cover_blur', 0) or 0
    if blur_val > 0:
        bx1 = max(0, shape.x); by1 = max(0, shape.y)
        bx2 = min(image.width, shape.x + sw); by2 = min(image.height, shape.y + sh)
        if bx2 > bx1 and by2 > by1:
            radius = max(1, int(blur_val * 0.2))
            region = image.crop((bx1, by1, bx2, by2))
            blurred = region.filter(ImageFilter.GaussianBlur(radius=radius))
            image.paste(blurred, (bx1, by1))

    layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    if shape.shape_type in ("rect", "square"):
        draw.rectangle([0, 0, sw - 1, sh - 1], fill=fc)
    elif shape.shape_type in ("ellipse", "circle"):
        draw.ellipse([0, 0, sw - 1, sh - 1], fill=fc)
    elif shape.shape_type == "star12":
        pts = _star_polygon(sw / 2, sh / 2,
                            min(sw, sh) / 2 - 1, min(sw, sh) / 2 * 0.78 - 1, n=12)
        draw.polygon(pts, fill=fc)

    # Trazo exterior usando la misma lógica que el borde de máscaras
    border_exp = 0
    if stk > 0:
        layer, border_exp = _apply_overlay_border(layer, shape.shape_type, stk, sc_color, 0)

    paste_x, paste_y = shape.x - border_exp, shape.y - border_exp

    if shape.rotation:
        layer = layer.rotate(-shape.rotation, expand=True, resample=Image.BICUBIC)
        new_w, new_h = layer.size
        orig_w = sw + 2 * border_exp
        orig_h = sh + 2 * border_exp
        paste_x = shape.x - border_exp + (orig_w - new_w) // 2
        paste_y = shape.y - border_exp + (orig_h - new_h) // 2

    # Recortar al tamaño de la imagen destino
    src_x1 = max(0, -paste_x)
    src_y1 = max(0, -paste_y)
    dst_x = max(0, paste_x)
    dst_y = max(0, paste_y)
    src_x2 = src_x1 + min(layer.width - src_x1, image.width - dst_x)
    src_y2 = src_y1 + min(layer.height - src_y1, image.height - dst_y)
    if src_x2 > src_x1 and src_y2 > src_y1:
        crop = layer.crop((src_x1, src_y1, src_x2, src_y2))
        image.paste(crop, (dst_x, dst_y), crop)


def _auto_fit_overlay(img: Image.Image, mask_type: str, ov_w: int, ov_h: int) -> Image.Image:
    """Escala la imagen para cubrir el área completa del overlay sin deformar (object-fit: cover).
    
    Siempre escala sobre ov_w × ov_h para que cualquier máscara (círculo, estrella, etc.)
    quede completamente rellena de imagen, sin espacios vacíos/transparentes.
    """
    iw, ih = img.size
    if ov_w <= 0 or ov_h <= 0 or iw <= 0 or ih <= 0:
        return img
    # cover: escalar al mínimo factor que cubra el contenedor completo
    scale = max(ov_w / iw, ov_h / ih)
    new_w = max(1, int(iw * scale))
    new_h = max(1, int(ih * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    # Recortar desde el centro
    cx = max(0, (new_w - ov_w) // 2)
    cy = max(0, (new_h - ov_h) // 2)
    cropped = resized.crop((cx, cy, cx + ov_w, cy + ov_h))
    if cropped.size != (ov_w, ov_h):
        canvas = Image.new("RGBA", (ov_w, ov_h), (0, 0, 0, 0))
        canvas.paste(cropped, (0, 0))
        return canvas
    return cropped


def _apply_text_warp(layer: Image.Image, style: str, bend_pct: int,
                     text_x: int, text_y: int, text_w: int, text_h: int) -> Image.Image:
    """Aplica warp tipo Photoshop a la capa de texto RGBA (en resolución 2x).

    Args:
        layer: capa RGBA completa del texto (a 2x resolución)
        style: estilo del warp (arc, wave, twist, ...)
        bend_pct: curvatura -100..100
        text_x/y: esquina sup-izq del bloque de texto en coords de capa
        text_w/h: tamaño del bloque de texto en coords de capa
    """
    if not _NUMPY_OK or not style or style == 'none' or bend_pct == 0:
        return layer

    bend = max(-1.0, min(1.0, bend_pct / 100.0 * 2.5))
    arr = np.array(layer, dtype=np.float32)
    H, W = arr.shape[:2]

    # Región de trabajo con margen para absorber overflow del warp
    margin = int(max(text_w, text_h) * 0.65)
    rx1 = max(0, text_x - margin)
    ry1 = max(0, text_y - margin)
    rx2 = min(W, text_x + text_w + margin)
    ry2 = min(H, text_y + text_h + margin)
    rW, rH = rx2 - rx1, ry2 - ry1
    if rW <= 0 or rH <= 0:
        return layer

    # Grilla de coords de salida (pixels dentro de la región)
    gy, gx = np.mgrid[0:rH, 0:rW].astype(np.float32)

    # Centro del bloque de texto dentro de la región
    cx = text_x + text_w * 0.5 - rx1
    cy = text_y + text_h * 0.5 - ry1
    half_w = text_w * 0.5
    half_h = text_h * 0.5

    # Coordenadas normalizadas ∈ [-1, 1] (pueden exceder en área de margen)
    u_norm = (gx - cx) / half_w
    v_norm = (gy - cy) / half_h

    # Desplazamiento inverso
    du, dv = _warp_displacement(style, u_norm, v_norm, bend)

    # Pixel fuente = pixel destino + desplazamiento en píxeles
    sx = np.clip(gx - du * half_w, 0, rW - 1)
    sy = np.clip(gy - dv * half_h, 0, rH - 1)

    # Muestrear con interpolación bilineal
    source_region = arr[ry1:ry2, rx1:rx2]
    warped = _bilinear_sample(source_region, sx, sy)

    result = arr.copy()
    result[ry1:ry2, rx1:rx2] = warped
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))


# ─── Word wrap: salto por palabra sin cortar, sin reducir fuente ─────────────
def _wrap_words(text: str, font, max_width: int, draw) -> str:
    """Ajusta texto a max_width px saltando palabras completas al renglón.
    
    Reglas:
    - Nunca corta palabras a la mitad.
    - Nunca reduce el tamaño de fuente.
    - Respeta los saltos de línea explícitos (\\n) del usuario.
    - Si una palabra sola excede max_width, la deja entera en su renglón.
    """
    if max_width <= 0:
        return text
    result_lines = []
    for paragraph in text.split('\n'):
        words = paragraph.split(' ')
        words = [w for w in words if w]  # eliminar strings vacíos
        if not words:
            result_lines.append('')
            continue
        current = ''
        for word in words:
            candidate = (current + ' ' + word).strip() if current else word
            try:
                bbox = draw.textbbox((0, 0), candidate, font=font)
                w_px = bbox[2] - bbox[0]
            except Exception:
                w_px = font.size * max(len(candidate), 1)
            if w_px <= max_width or not current:
                # Cabe en la línea actual (o es la primera palabra — nunca la cortamos)
                current = candidate
            else:
                result_lines.append(current)
                current = word
        if current:
            result_lines.append(current)
    return '\n'.join(result_lines)


# ─── Renderizado multilinea manual con Pilmoji ───────────────────────────────
def pilmoji_multiline(pilmoji_obj, draw_obj, xy, text, font, fill, spacing=0, text_align='center', block_width=None):
    """Renderiza texto multilinea con Pilmoji respetando el spacing explícitamente.
    
    Pilmoji puede ignorar el parámetro 'spacing' internamente; este helper
    divide el texto en líneas y las posiciona manualmente con el spacing correcto.
    
    Args:
        xy: (x, y) — esquina superior-izquierda del bloque de texto
        block_width: ancho del bloque (para alineación). Si None, usa ancho máximo.
        spacing: pixeles EXTRA entre líneas (además de la altura natural del font)
    """
    lines = text.split('\n')
    x, y = xy

    # Altura de una línea = font.size (punto de diseño).
    # Usar lbox[3]-lbox[1] (bounding box del glifo) produce un avance distinto
    # al de CSS (que siempre avanza font-size px por línea) en fuentes donde
    # la caja del glifo es más pequeña que el cuerpo tipográfico (e.g. Mynerve).
    line_h = font.size

    # Ancho de cada línea (para alineación dentro del bloque)
    widths = []
    for ln in lines:
        try:
            lb = draw_obj.textbbox((0, 0), ln, font=font)
            widths.append(lb[2] - lb[0])
        except Exception:
            widths.append(font.size * max(len(ln), 1))

    bw = block_width if block_width is not None else (max(widths) if widths else 0)

    for i, ln in enumerate(lines):
        lw = widths[i]
        if text_align == 'center':
            lx = x + (bw - lw) // 2
        elif text_align == 'right':
            lx = x + (bw - lw)
        else:
            lx = x

        try:
            pilmoji_obj.text((lx, y), ln, font=font, fill=fill)
        except Exception:
            draw_obj.text((lx, y), ln, font=font, fill=fill)

        y += line_h + spacing


# ─── Renderizado de texto con emojis ─────────────────────────────────────────
def draw_text_with_effects(image: Image.Image, text_field: TextField, font, render_scale: int = 1) -> Image.Image:
    """Dibuja texto con sombra, stroke, fondo y soporte completo de emojis.
    
    render_scale=1: resolución nativa (rápido, para ManyChat)
    render_scale=2: supersampling 2x para anti-aliasing perfecto (editor)
    """
    SCALE = max(1, render_scale)  # Factor de supersampling configurable
    width, height = image.size
    big_w, big_h = width * SCALE, height * SCALE

    # Cuando hay rotación, añadir padding en el lienzo de trabajo para que el texto
    # largo no se recorte ANTES de girar (el recorte previo al rotate es el bug clásico).
    pre_pad = max(big_w, big_h) if text_field.rotation else 0
    work_w, work_h = big_w + 2 * pre_pad, big_h + 2 * pre_pad

    # Capa de trabajo a 2x resolución
    layer = Image.new("RGBA", (work_w, work_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # Fuente a 2x tamaño
    try:
        font2x = ImageFont.truetype(font.path, int(font.size * SCALE))
    except Exception:
        font2x = font  # fallback si no se puede escalar

    text_to_draw = text_field.text
    color = parse_color(text_field.font_color)
    final_color = color if len(color) == 4 else color + (255,)
    spacing = text_field.line_spacing * SCALE
    text_align = text_field.text_align if text_field.text_align in ("left", "center", "right") else "left"

    # ── Text Wrap: ajustar texto por palabras completas si está activado ──────
    if getattr(text_field, 'text_wrap_enabled', False):
        pad = max(0, getattr(text_field, 'text_wrap_padding', 60))
        max_wrap_w = max(1, image.width * SCALE - 2 * pad * SCALE)
        text_to_draw = _wrap_words(text_to_draw, font2x, max_wrap_w, draw)

    # Bounding box a 2x
    bbox = draw.multiline_textbbox((0, 0), text_to_draw, font=font2x, spacing=spacing, align=text_align)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Offset entre el punto de anclaje 'la' de Pillow y la cima visual del glifo.
    # Con anchor='la' el punto de dibujo es la línea ascendente (ascender), y el
    # tope real del glifo queda a +lbox[1] px por debajo — puede ser 0 o decenas de px
    # dependiendo de la fuente (especialmente fuentes display/script).
    # Restamos ese offset de base_y para que la cima del glifo quede EXACTAMENTE en y.
    try:
        _lbox = draw.textbbox((0, 0), "Ag", font=font2x)
        top_offset = _lbox[1]  # positivo → glifo empieza debajo del anclaje
    except Exception:
        top_offset = 0

    # Coordenadas a 2x  (glyph_top = text_field.y * SCALE exactamente)
    # pre_pad desplaza todo el dibujo al interior del lienzo extendido para que
    # texto largo (antes de girar) no quede cortado en el borde del canvas.
    glyph_y = text_field.y * SCALE + pre_pad  # coordenada final de la cima del glifo
    base_x = text_field.x * SCALE + pre_pad
    if text_field.alignment == "center":
        base_x = text_field.x * SCALE + pre_pad - (text_width // 2)
    elif text_field.alignment == "right":
        base_x = text_field.x * SCALE + pre_pad - text_width
    base_y = glyph_y - top_offset            # ancla desplazada para compensar top_offset

    # 1. FONDO / BACKGROUND + BORDE (completamente independientes)
    _has_bg     = text_field.background_enabled
    _has_border = text_field.background_stroke_width > 0
    if _has_bg or _has_border:
        pad_t = (text_field.background_padding_top    or 10) * SCALE
        pad_r = (text_field.background_padding_right  or 10) * SCALE
        pad_b = (text_field.background_padding_bottom or 10) * SCALE
        pad_l = (text_field.background_padding_left   or 10) * SCALE
        bx1, by1 = base_x - pad_l, glyph_y - pad_t
        bx2, by2 = base_x + text_width + pad_r, glyph_y + text_height + pad_b
        radius   = text_field.background_radius * SCALE

        # ── Relleno del fondo (solo si background_enabled) ──────────────────
        if _has_bg:
            bg_alpha = int(255 * text_field.background_opacity / 100)
            bg_type  = text_field.background_color_type or "solid"
            if bg_type == "instagram":
                apply_gradient_bg(layer, bx1, by1, bx2, by2, radius, _ig_colors(bg_alpha), 45)
            elif bg_type == "negro":
                apply_gradient_bg(layer, bx1, by1, bx2, by2, radius, _negro_colors(bg_alpha), 145)
            elif bg_type == "gradient2":
                c1 = parse_color(text_field.background_color)[:3] + (bg_alpha,)
                c2 = parse_color(text_field.background_gradient_color2 or "#FFFFFF")[:3] + (bg_alpha,)
                ang = text_field.background_gradient_angle or 135
                apply_gradient_bg(layer, bx1, by1, bx2, by2, radius, [c1, c2], ang)
            else:
                bg_color = parse_color_with_opacity(text_field.background_color, text_field.background_opacity)
                if radius > 0:
                    draw.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=radius, fill=bg_color)
                else:
                    draw.rectangle([(bx1, by1), (bx2, by2)], fill=bg_color)

        # ── Borde / Stroke (solo si stroke_width > 0, independiente del fondo) ─
        if _has_border:
            if _has_bg:
                # Ambas activas: la caja de fondo "jala" al borde.
                # Usamos el bbox del fondo (ya calculado arriba) y lo expandimos
                # hacia AFUERA por stroke_w para que el borde rodee la caja de fondo.
                stroke_w = int(text_field.background_stroke_width * SCALE)
                half = stroke_w // 2
                bx1 -= half; by1 -= half; bx2 += half; by2 += half
            else:
                # Solo borde sin fondo: recalcular bbox con padding propio del borde
                bp_t = (text_field.border_padding_top    or 10) * SCALE
                bp_r = (text_field.border_padding_right  or 20) * SCALE
                bp_b = (text_field.border_padding_bottom or 10) * SCALE
                bp_l = (text_field.border_padding_left   or 20) * SCALE
                bx1, by1 = base_x - bp_l, glyph_y - bp_t
                bx2, by2 = base_x + text_width + bp_r, glyph_y + text_height + bp_b
                stroke_w    = int(text_field.background_stroke_width * SCALE)
            stroke_type = text_field.background_stroke_type or "solid"
            stroke_alpha = int(255 * (getattr(text_field, "background_stroke_opacity", None) or 100) / 100)
            try:
                _sc_raw = text_field.background_stroke_color.strip()
                if _sc_raw.startswith("rgba("):
                    _vals = _sc_raw[5:-1].split(",")
                    stroke_alpha = int(float(_vals[3].strip()) * 255)
            except Exception:
                pass
            try:
                if stroke_type == "instagram":
                    apply_gradient_stroke(layer, bx1, by1, bx2, by2, radius, stroke_w,
                                          _ig_colors(stroke_alpha), 45)
                elif stroke_type == "metalico":
                    apply_gradient_stroke(layer, bx1, by1, bx2, by2, radius, stroke_w,
                                          _metal_colors(stroke_alpha), 90)
                elif stroke_type == "gradient2":
                    c1s = parse_color(text_field.background_stroke_color)[:3] + (stroke_alpha,)
                    c2s = parse_color(text_field.background_stroke_gradient_color2 or "#FFFFFF")[:3] + (stroke_alpha,)
                    ang_s = text_field.background_stroke_gradient_angle or 135
                    apply_gradient_stroke(layer, bx1, by1, bx2, by2, radius, stroke_w,
                                          [c1s, c2s], ang_s)
                else:
                    stroke_c = parse_color_with_opacity(text_field.background_stroke_color, 100)
                    dash_style = getattr(text_field, 'background_stroke_dash', 'solid') or 'solid'
                    _draw_dashed_border(draw, bx1, by1, bx2, by2, radius, stroke_w, stroke_c, dash_style)
            except Exception as e:
                logger.warning(f"⚠️ Error dibujando borde ({stroke_type}): {e} — usando borde sólido de fallback")

    # 2. SOMBRA
    # La sombra se renderiza en capa separada con Pilmoji (para capturar emojis correctamente),
    # luego se coloriza — reemplazando todos los píxeles con el color de sombra y conservando
    # solo el canal alpha. Esto evita que los emojis aparezcan como un "fantasma" de color.
    if text_field.shadow_enabled:
        shadow_c = parse_color_with_opacity(text_field.shadow_color, text_field.shadow_opacity)
        r_s, g_s, b_s, a_s = shadow_c

        # Renderizar texto+emojis en blanco sobre transparente para obtener la forma (alpha mask)
        shadow_src = Image.new("RGBA", (work_w, work_h), (0, 0, 0, 0))
        _sd = ImageDraw.Draw(shadow_src)
        try:
            _src = get_emoji_source()
            with Pilmoji(shadow_src, source=_src) as _p:
                pilmoji_multiline(_p, _sd, (base_x, base_y), text_to_draw,
                    font=font2x, fill=(255, 255, 255, 255),
                    spacing=spacing, text_align=text_align, block_width=text_width)
        except Exception:
            _sd.multiline_text(
                (base_x, base_y), text_to_draw,
                font=font2x, fill=(255, 255, 255, 255),
                spacing=spacing, align=text_align,
            )

        # Extraer alpha, escalar por opacidad de sombra y colorizar con color de sombra
        _, _, _, alpha = shadow_src.split()
        alpha_scaled = alpha.point(lambda p: int(p * a_s / 255))
        colorized = Image.new("RGBA", (work_w, work_h), (0, 0, 0, 0))
        colorized.paste(Image.new("RGBA", (work_w, work_h), (r_s, g_s, b_s, 255)), mask=alpha_scaled)

        # Pegar con offset de sombra (contra-rotado para que la sombra mantenga dirección fija
        # en el canvas aunque el texto esté girado).
        raw_ox = text_field.shadow_offset_x * SCALE
        raw_oy = text_field.shadow_offset_y * SCALE
        if text_field.rotation:
            theta = math.radians(text_field.rotation)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            adj_ox = int(raw_ox * cos_t + raw_oy * sin_t)
            adj_oy = int(-raw_ox * sin_t + raw_oy * cos_t)
        else:
            adj_ox, adj_oy = int(raw_ox), int(raw_oy)
        shadow_placed = Image.new("RGBA", (work_w, work_h), (0, 0, 0, 0))
        shadow_placed.paste(colorized, (adj_ox, adj_oy))
        layer = Image.alpha_composite(layer, shadow_placed)

    # 3. STROKE
    if text_field.stroke_enabled:
        stroke_c = parse_color_with_opacity(text_field.stroke_color, text_field.stroke_opacity)
        draw.multiline_text(
            (base_x, base_y), text_to_draw, font=font2x, fill=stroke_c,
            spacing=spacing, align=text_align,
            stroke_width=text_field.stroke_width * SCALE, stroke_fill=stroke_c,
        )

    # 4. TEXTO PRINCIPAL con emojis
    # ─── Clave: capa de texto pre-rellenada con el color del texto (alpha=0) ───
    # Pillow hace anti-aliasing mezclando el texto con el fondo de la capa.
    # Si el fondo es (0,0,0,0) negro transparente, los bordes del texto de color
    # quedan contaminados con negro → halo oscuro visible en colores como rojo.
    # Pre-rellenar con (r,g,b,0) hace que el anti-aliasing mezcle texto con el
    # MISMO color → bordes limpios, sin halo, en cualquier color.
    tr, tg, tb = final_color[:3]
    text_layer = Image.new("RGBA", (work_w, work_h), (tr, tg, tb, 0))

    tl_draw = ImageDraw.Draw(text_layer)
    emoji_rendered = False
    try:
        source = get_emoji_source()
        with Pilmoji(text_layer, source=source) as pilmoji:
            pilmoji_multiline(pilmoji, tl_draw, (base_x, base_y), text_to_draw,
                font=font2x, fill=final_color,
                spacing=spacing, text_align=text_align, block_width=text_width)
        emoji_rendered = True
        logger.info("✅ Emojis renderizados con Twemoji CDN")
    except Exception as e:
        logger.warning(f"⚠️ pilmoji falló: {e} — intentando EmojiCDNSource como fallback")

    if not emoji_rendered:
        try:
            with Pilmoji(text_layer, source=EmojiCDNSource()) as pilmoji:
                pilmoji_multiline(pilmoji, tl_draw, (base_x, base_y), text_to_draw,
                    font=font2x, fill=final_color,
                    spacing=spacing, text_align=text_align, block_width=text_width)
            emoji_rendered = True
            logger.info("✅ Emojis renderizados con EmojiCDNSource (fallback)")
        except Exception as e2:
            logger.warning(f"⚠️ EmojiCDNSource también falló: {e2} — usando texto plano")

    if not emoji_rendered:
        tl_draw.multiline_text(
            (base_x, base_y), text_to_draw,
            font=font2x, fill=final_color,
            spacing=spacing, align=text_align,
        )

    # Combinar capa de efectos (fondo/sombra/stroke) + capa de texto
    layer = Image.alpha_composite(layer, text_layer)

    # Rotación en 2x con BICUBIC antes de downscalar → calidad vectorial sin pixelado
    # CSS rotate() es CW; Pillow rotate() es CCW → negamos el ángulo para coincidir
    # IMPORTANTE: se usa padding para evitar que el texto rotado quede recortado
    # cuando se extiende más allá de los bordes de la imagen original.
    if text_field.rotation:
        # El lienzo ya tiene pre_pad en cada lado → el texto no puede estar recortado.
        # Rotamos sobre el centro del bloque de texto y luego recortamos la zona
        # correspondiente a la imagen original (sin el padding extra).
        cx_2x = int(base_x + text_width / 2)
        cy_2x = int(glyph_y + text_height / 2)
        rotated = layer.rotate(
            -text_field.rotation,
            resample=Image.BICUBIC,
            expand=False,
            center=(cx_2x, cy_2x),
        )
        # Recortar la región central = imagen sin el pre_pad
        layer = rotated.crop((pre_pad, pre_pad, pre_pad + big_w, pre_pad + big_h))
        # Ajustar coordenadas para operaciones posteriores (skew, warp) que
        # usan base_x / glyph_y sobre el layer recortado (sin pre_pad)
        base_x  -= pre_pad
        glyph_y -= pre_pad
        base_y  -= pre_pad

    # Sesgo / Distorsión (skew) en 2x con AFFINE — igual calidad vectorial
    # Ancla en el centro del bloque de texto.
    # Matriz afín (inversa): x_in = x_out - tx*(y_out-cy) ; y_in = y_out - ty*(x_out-cx)
    skew_x = text_field.skew_x or 0
    skew_y = text_field.skew_y or 0
    if skew_x or skew_y:
        cx_sk = int(base_x + text_width  / 2)
        cy_sk = int(glyph_y + text_height / 2)
        tx = math.tan(math.radians(skew_x))   # horizontal shear
        ty = math.tan(math.radians(skew_y))   # vertical shear
        layer = layer.transform(
            layer.size,
            Image.AFFINE,
            (1, -tx, tx * cy_sk,
             -ty,  1, ty * cx_sk),
            resample=Image.BICUBIC,
        )

    # Warp de texto tipo Photoshop (después de rotation/skew, antes de downscale)
    _wstyle = (getattr(text_field, 'warp_style', None) or 'none').strip()
    _wbend  = int(getattr(text_field, 'warp_bend',  None) or 0)
    if _wstyle != 'none' and _wbend != 0:
        try:
            layer = _apply_text_warp(
                layer, _wstyle, _wbend,
                int(base_x), int(glyph_y),
                int(text_width), int(text_height)
            )
        except Exception as _we:
            logger.warning(f"⚠️ Error aplicando warp '{_wstyle}': {_we}")

    # Reducir 2x → 1x con LANCZOS para anti-aliasing suave
    layer_1x = layer.resize((width, height), Image.LANCZOS)

    # Cuando shadow_blur > 0: colorizar PRIMERO (para que emojis usen el color de sombra,
    # no su color original), y LUEGO aplicar el desenfoque.
    # IMPORTANTE: el texto renderizado ya tiene la opacidad en su alpha (Pillow la incorpora
    # al dibujar con fill rgba). Los emojis de Pilmoji son PNGs totalmente opacos (alpha=255).
    # Para que ambos queden a la misma intensidad, limitamos el alpha al valor de opacidad
    # original (a_f) — el emoji queda igual que el texto, sin ser más brillante.
    if text_field.shadow_blur > 0:
        r_f, g_f, b_f = final_color[:3]
        a_f = final_color[3] if len(final_color) > 3 else 255
        _, _, _, alpha_ch = layer_1x.split()
        # Limitar alpha al máximo de opacidad deseado
        alpha_capped = alpha_ch.point(lambda p: min(p, a_f))
        colorized_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        colorized_layer.paste(
            Image.new("RGBA", (width, height), (r_f, g_f, b_f, 255)),
            mask=alpha_capped
        )
        layer_1x = colorized_layer.filter(ImageFilter.GaussianBlur(radius=text_field.shadow_blur))

    # Aplicar modo de fusión al compositar sobre la imagen base
    blend_mode = (text_field.shadow_blend_mode or "normal").strip().lower()
    if blend_mode == "normal":
        image.paste(layer_1x, (0, 0), layer_1x)
    else:
        image = apply_blend_mode(image, layer_1x, blend_mode)
    return image


# ─── Utilidad de fuentes ──────────────────────────────────────────────────────
def get_font_path(font_name: str) -> str:
    font_path = FONT_MAPPING.get(font_name, "./fonts/LiberationSans-Bold.ttf")
    if not os.path.exists(font_path):
        logger.warning(f"⚠️ Fuente '{font_name}' no encontrada, usando Arial")
        return "./fonts/LiberationSans-Bold.ttf"
    return font_path


# ─── Countdown helper ─────────────────────────────────────────────────────────
def _format_countdown(seconds: float, fmt: str, expired_text: str) -> str:
    """Formatea segundos restantes en una cadena de contador regresivo."""
    if seconds <= 0:
        return expired_text or "¡Oferta expirada!"
    s   = int(seconds)
    dd  = s // 86400
    hh  = (s % 86400) // 3600
    mm  = (s % 3600)  // 60
    ss  = s % 60
    if fmt == "DD:HH:MM:SS":
        return f"{dd}:{hh:02d}:{mm:02d}:{ss:02d}"
    if fmt == "HH:MM":
        return f"{hh + dd*24}:{mm:02d}"
    return f"{hh + dd*24}:{mm:02d}:{ss:02d}"


# ─── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("index.html", media_type="text/html")

@app.get("/dashboard")
async def dashboard():
    return FileResponse("static/dashboard.html", media_type="text/html")

@app.get("/status")
async def status():
    noto_path = get_noto_emoji_font()
    return {
        "message": "TextOnFlow Image Personalizer",
        "status": "running",
        "version": "6.0.0",
        "noto_emoji_available": noto_path is not None,
        "noto_emoji_path": noto_path,
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check rápido — solo verifica que la app esté viva."""
    db_ok = False
    db_err = ""
    try:
        conn = get_db()
        if conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            db_ok = True
    except Exception as e:
        db_err = str(e)[:100]
    return {
        "status": "ok",
        "version": "8.1.0",
        "numpy": _NUMPY_OK,
        "db": db_ok,
        "psycopg2": _PSYCOPG2_OK,
        "db_url_prefix": SUPABASE_DATABASE_URL[:40] if SUPABASE_DATABASE_URL else "NOT SET",
        "db_err": db_err,
    }


@app.get("/api/stats")
async def get_stats():
    """Devuelve estadísticas públicas de uso de TextOnFlow."""
    data = _read_stats()
    return {
        "images_generated": data.get("images_generated", 0),
    }

@app.get("/api/usage")
async def get_usage(request: Request):
    """Uso diario de la IP actual (rate limiting)."""
    if _is_superadmin(request):
        return {"used": 0, "limit": 0, "plan": "superadmin", "exceeded": False,
                "reset_in": "—", "pct": 0, "superadmin": True}
    ip   = _get_client_ip(request)
    used, limit, exceeded = _check_rate_limit(ip)
    pct  = min(100, round(used / limit * 100)) if limit else 0
    return {
        "used":       used,
        "limit":      limit,
        "plan":       "free",
        "exceeded":   exceeded,
        "reset_in":   _reset_time_str(),
        "pct":        pct,
        "superadmin": False,
    }


class _AdminLoginBody(BaseModel):
    email: str
    password: str

@app.post("/api/auth/login")
async def admin_login(body: _AdminLoginBody):
    """Login de superadmin — devuelve un token de sesión de 30 días."""
    import hashlib
    pwd_hash = hashlib.sha256(body.password.encode()).hexdigest()
    if body.email.strip().lower() != _SUPERADMIN_EMAIL or pwd_hash != _SUPERADMIN_PWD_HASH:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")
    token   = secrets.token_urlsafe(40)
    expires = datetime.utcnow() + _SESSION_TTL
    with _ADMIN_LOCK:
        _ADMIN_SESSIONS[token] = {"email": body.email, "expires": expires}
    return {"token": token, "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ")}

@app.post("/api/auth/logout")
async def admin_logout(request: Request):
    token = request.headers.get("X-Admin-Token", "")
    if token:
        with _ADMIN_LOCK:
            _ADMIN_SESSIONS.pop(token, None)
    return {"ok": True}

@app.get("/api/auth/me")
async def admin_me(request: Request):
    if _is_superadmin(request):
        return {"superadmin": True, "email": _SUPERADMIN_EMAIL}
    return {"superadmin": False}


@app.get("/api/admin/settings")
async def admin_get_settings(request: Request):
    """Devuelve la configuración editable (solo superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    return {"free_limit": PLAN_LIMITS["free"]}

class _AdminSettingsBody(BaseModel):
    free_limit: int

@app.post("/api/admin/settings")
async def admin_set_settings(body: _AdminSettingsBody, request: Request):
    """Actualiza la configuración en caliente (solo superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    if body.free_limit < 1 or body.free_limit > 9999:
        raise HTTPException(status_code=400, detail="Límite debe estar entre 1 y 9999.")
    PLAN_LIMITS["free"] = body.free_limit
    logger.info(f"⚙️ Superadmin actualizó límite Free → {body.free_limit}")
    return {"ok": True, "free_limit": PLAN_LIMITS["free"]}

@app.get("/superadministrador", include_in_schema=False)
async def superadmin_page():
    """Ruta secreta que sirve la app con flag para abrir el login admin."""
    from fastapi.responses import HTMLResponse
    html_path = "index.html"
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    # Inyectar script que abre el modal al cargar
    inject = "<script>window._OPEN_SA_ON_LOAD=true;</script>"
    content = content.replace("</body>", inject + "</body>", 1)
    return HTMLResponse(content=content)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join("static", "favicon.png")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/png")
    return Response(status_code=204)


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH DE USUARIOS (Phase 2)
# ═══════════════════════════════════════════════════════════════════════════════

USER_PLAN_LIMITS = {
    "trial":   20,
    "starter": 1000,
    "agency":  10000,
    "admin":   999999,
}

class _UserRegisterBody(BaseModel):
    email: str
    password: str

class _UserLoginBody(BaseModel):
    email: str
    password: str

class _UserUpdateBody(BaseModel):
    gemini_api_key: Optional[str] = None

def _get_current_user(request: Request) -> Optional[dict]:
    """Lee el JWT del header Authorization: Bearer <token> y devuelve el payload."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    return decode_jwt(token)

def _require_user(request: Request) -> dict:
    user = _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido o expirado.")
    return user

@app.post("/user/register")
async def user_register(body: _UserRegisterBody):
    """Registra un usuario nuevo con plan trial (20 renders)."""
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido.")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email.")
            pwd_hash = hash_password(body.password)
            cur.execute("""
                INSERT INTO users (email, password_hash, plan, renders_limit)
                VALUES (%s, %s, 'trial', %s)
                RETURNING id, email, plan, renders_used, renders_limit, created_at
            """, (email, pwd_hash, USER_PLAN_LIMITS["trial"]))
            user = dict(cur.fetchone())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en registro: {e}")
        raise HTTPException(status_code=500, detail="Error interno al crear la cuenta.")
    token = create_jwt(str(user["id"]), user["email"], user["plan"])
    logger.info(f"✅ Nuevo usuario registrado: {email}")

    # ── Email de bienvenida (EngineMailer) — async, no bloquea ───────────────
    _em_key = os.environ.get("ENGINEMAILER_API_KEY", "")
    if _em_key:
        async def _send_welcome():
            try:
                import functools
                _body = {
                    "CampaignName": "TextOnFlow Bienvenida",
                    "ToEmail": email,
                    "SenderEmail": "hola@textonflow.com",
                    "SenderName": "TextOnFlow",
                    "Subject": "¡Bienvenido a TextOnFlow! Tus 20 renders gratis te esperan",
                    "SubmittedContent": (
                        f"<h2>¡Hola! 👋</h2>"
                        f"<p>Tu cuenta <strong>{email}</strong> ya está activa con el <strong>Plan Trial</strong> — 20 renders gratuitos para empezar.</p>"
                        f"<p>Entra al editor y crea tu primera imagen personalizada:</p>"
                        f"<p><a href='https://www.textonflow.com' style='background:#7c6eff;color:#fff;padding:10px 22px;border-radius:8px;text-decoration:none;font-weight:700;'>Abrir editor →</a></p>"
                        f"<hr style='margin:24px 0;border:none;border-top:1px solid #eee'>"
                        f"<p style='font-size:12px;color:#888'>Cuando quieras más renders, elige tu plan en: "
                        f"<a href='https://www.textonflow.com/dashboard'>textonflow.com/dashboard</a></p>"
                    ),
                }
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(
                    None,
                    functools.partial(
                        requests.post,
                        "https://api.enginemailer.com/RESTAPI/V2/Submission/SendEmail",
                        headers={"APIKey": _em_key, "Content-Type": "application/json"},
                        json=_body,
                        timeout=15,
                    )
                )
                if resp.status_code in (200, 201):
                    logger.info(f"📧 Email de bienvenida enviado a {email}")
                else:
                    logger.warning(f"EngineMailer bienvenida {resp.status_code}: {resp.text[:120]}")
            except Exception as _e:
                logger.warning(f"Error enviando bienvenida: {_e}")
        asyncio.create_task(_send_welcome())

    return {
        "token": token,
        "user": {
            "id": str(user["id"]),
            "email": user["email"],
            "plan": user["plan"],
            "renders_used": user["renders_used"],
            "renders_limit": user["renders_limit"],
        }
    }

@app.post("/user/login")
async def user_login(body: _UserLoginBody):
    """Login de usuario — devuelve JWT válido por 7 días."""
    email = body.email.strip().lower()
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, email, password_hash, plan, renders_used, renders_limit, is_active, gemini_api_key
                FROM users WHERE email = %s
            """, (email,))
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en login: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos.")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Cuenta desactivada.")
    token = create_jwt(str(user["id"]), user["email"], user["plan"])
    return {
        "token": token,
        "user": {
            "id": str(user["id"]),
            "email": user["email"],
            "plan": user["plan"],
            "renders_used": user["renders_used"],
            "renders_limit": user["renders_limit"],
            "has_gemini_key": bool(user["gemini_api_key"]),
        }
    }

@app.get("/user/me")
async def user_me(request: Request):
    """Devuelve los datos del usuario autenticado."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, email, plan, renders_used, renders_limit, gemini_api_key,
                       stripe_customer_id, created_at
                FROM users WHERE id = %s
            """, (payload["sub"],))
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en /user/me: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "plan": user["plan"],
        "renders_used": user["renders_used"],
        "renders_limit": user["renders_limit"],
        "has_gemini_key": bool(user["gemini_api_key"]),
        "has_stripe": bool(user["stripe_customer_id"]),
        "created_at": user["created_at"].isoformat() if user["created_at"] else None,
    }

@app.put("/user/me")
async def user_update(body: _UserUpdateBody, request: Request):
    """Actualiza datos del usuario (gemini_api_key, etc.)."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor() as cur:
            if body.gemini_api_key is not None:
                key = body.gemini_api_key.strip() or None
                cur.execute("""
                    UPDATE users SET gemini_api_key = %s, updated_at = NOW()
                    WHERE id = %s
                """, (key, payload["sub"]))
    except Exception as e:
        logger.error(f"Error en PUT /user/me: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    return {"ok": True}

@app.get("/user/usage")
async def user_usage(request: Request):
    """Devuelve el uso actual de renders del usuario autenticado."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT plan, renders_used, renders_limit FROM users WHERE id = %s
            """, (payload["sub"],))
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en /user/usage: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    limit = USER_PLAN_LIMITS.get(user["plan"], 20)
    return {
        "plan": user["plan"],
        "renders_used": user["renders_used"],
        "renders_limit": limit,
        "renders_remaining": max(0, limit - user["renders_used"]),
        "pct": min(100, round(user["renders_used"] / limit * 100)) if limit else 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  STRIPE CHECKOUT (Phase 4)
# ═══════════════════════════════════════════════════════════════════════════════

try:
    import stripe as _stripe
    _stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    _STRIPE_OK = bool(_stripe.api_key)
except ImportError:
    _stripe      = None
    _STRIPE_OK   = False

STRIPE_PUBLISHABLE_KEY   = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET    = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_STARTER_PRICE_ID  = os.environ.get("STRIPE_STARTER_PRICE_ID", "price_1TQd5CDWKdKa9ZRQxabnfqla")
STRIPE_AGENCY_PRICE_ID   = os.environ.get("STRIPE_AGENCY_PRICE_ID",  "price_1TQd5DDWKdKa9ZRQ1KdEkA2U")

_PLAN_PRICE_MAP = {
    "starter": STRIPE_STARTER_PRICE_ID,
    "agency":  STRIPE_AGENCY_PRICE_ID,
}

class _CheckoutBody(BaseModel):
    plan: str          # "starter" | "agency"
    success_url: Optional[str] = None
    cancel_url:  Optional[str] = None

@app.post("/stripe/checkout")
async def stripe_checkout(body: _CheckoutBody, request: Request):
    """Crea una Stripe Checkout Session y devuelve la URL de pago."""
    if not _STRIPE_OK:
        raise HTTPException(status_code=503, detail="Stripe no configurado.")
    payload = _require_user(request)
    plan = body.plan.lower()
    if plan not in _PLAN_PRICE_MAP:
        raise HTTPException(status_code=400, detail="Plan inválido. Usa 'starter' o 'agency'.")
    price_id = _PLAN_PRICE_MAP[plan]
    base = body.success_url or "https://web-production-98b55.up.railway.app"
    success_url = body.success_url or f"{base}/stripe/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = body.cancel_url  or f"{base}/precios"

    # Buscar o crear customer de Stripe
    customer_id = None
    conn = get_db()
    if conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT stripe_customer_id, email FROM users WHERE id = %s", (payload["sub"],))
                row = cur.fetchone()
            if row:
                customer_id = row["stripe_customer_id"]
                if not customer_id:
                    customer = _stripe.Customer.create(
                        email=row["email"],
                        metadata={"user_id": payload["sub"]},
                    )
                    customer_id = customer.id
                    with conn.cursor() as cur:
                        cur.execute("UPDATE users SET stripe_customer_id = %s, updated_at = NOW() WHERE id = %s",
                                    (customer_id, payload["sub"]))
        except Exception as e:
            logger.error(f"Error Stripe customer: {e}")

    try:
        session_params = dict(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": payload["sub"], "plan": plan},
            subscription_data={"metadata": {"user_id": payload["sub"], "plan": plan}},
        )
        if customer_id:
            session_params["customer"] = customer_id
        else:
            session_params["customer_email"] = payload["email"]
        session = _stripe.checkout.Session.create(**session_params)
        return {"checkout_url": session.url, "session_id": session.id}
    except Exception as e:
        logger.error(f"Error creando Stripe session: {e}")
        raise HTTPException(status_code=500, detail=f"Error Stripe: {str(e)[:200]}")

@app.get("/stripe/success")
async def stripe_success(session_id: str = ""):
    """Redirige al dashboard con flag de éxito."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard?success=1", status_code=302)

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Recibe eventos de Stripe y actualiza el plan del usuario en BD."""
    payload_bytes = await request.body()
    sig_header    = request.headers.get("stripe-signature", "")

    try:
        if STRIPE_WEBHOOK_SECRET and sig_header:
            event = _stripe.Webhook.construct_event(payload_bytes, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = _stripe.Event.construct_from(
                json.loads(payload_bytes.decode()), _stripe.api_key
            )
    except Exception as e:
        logger.error(f"Webhook signature error: {e}")
        raise HTTPException(status_code=400, detail="Webhook inválido.")

    etype = event["type"]
    logger.info(f"📨 Stripe webhook: {etype}")

    def _update_user_plan(user_id: str, new_plan: str):
        conn = get_db()
        if not conn or not user_id:
            return
        limit = USER_PLAN_LIMITS.get(new_plan, 20)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE users SET plan = %s, renders_limit = %s, updated_at = NOW()
                    WHERE id = %s
                """, (new_plan, limit, user_id))
            logger.info(f"✅ Usuario {user_id} actualizado a plan {new_plan}")
        except Exception as e:
            logger.error(f"Error actualizando plan en BD: {e}")

    def _upsert_subscription(user_id: str, sub_id: str, plan: str, status: str,
                              period_start=None, period_end=None):
        conn = get_db()
        if not conn or not user_id:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO subscriptions
                        (user_id, stripe_subscription_id, plan, status, current_period_start, current_period_end)
                    VALUES (%s, %s, %s, %s,
                        to_timestamp(%s), to_timestamp(%s))
                    ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                        plan = EXCLUDED.plan, status = EXCLUDED.status,
                        current_period_start = EXCLUDED.current_period_start,
                        current_period_end   = EXCLUDED.current_period_end,
                        updated_at           = NOW()
                """, (user_id, sub_id, plan, status, period_start, period_end))
        except Exception as e:
            logger.error(f"Error upsert subscription: {e}")

    if etype == "checkout.session.completed":
        obj     = event["data"]["object"]
        user_id = obj.get("metadata", {}).get("user_id", "")
        plan    = obj.get("metadata", {}).get("plan", "starter")
        sub_id  = obj.get("subscription", "")
        _update_user_plan(user_id, plan)
        if sub_id:
            _upsert_subscription(user_id, sub_id, plan, "active")

    elif etype in ("customer.subscription.updated", "customer.subscription.created"):
        obj     = event["data"]["object"]
        user_id = obj.get("metadata", {}).get("user_id", "")
        status  = obj.get("status", "active")
        sub_id  = obj.get("id", "")
        period_start = obj.get("current_period_start")
        period_end   = obj.get("current_period_end")
        # Inferir plan desde price ID
        items = obj.get("items", {}).get("data", [])
        plan  = "starter"
        if items:
            pid = items[0].get("price", {}).get("id", "")
            if pid == STRIPE_AGENCY_PRICE_ID:
                plan = "agency"
        if user_id and status == "active":
            _update_user_plan(user_id, plan)
        _upsert_subscription(user_id, sub_id, plan, status, period_start, period_end)

    elif etype == "customer.subscription.deleted":
        obj     = event["data"]["object"]
        user_id = obj.get("metadata", {}).get("user_id", "")
        sub_id  = obj.get("id", "")
        _update_user_plan(user_id, "trial")
        _upsert_subscription(user_id, sub_id, "trial", "canceled")

    return {"received": True}

@app.get("/stripe/config")
async def stripe_config():
    """Devuelve la clave pública y los price IDs (para el frontend)."""
    return {
        "publishable_key": STRIPE_PUBLISHABLE_KEY,
        "plans": {
            "starter": {"price_id": STRIPE_STARTER_PRICE_ID, "amount": 29, "renders": 1000},
            "agency":  {"price_id": STRIPE_AGENCY_PRICE_ID,  "amount": 79, "renders": 10000},
        }
    }


# ─── Helpers de rate limit por usuario (Phase 3) ─────────────────────────────

def _check_user_render_limit(user_id: str) -> tuple:
    """(used, limit, exceeded, plan) — lee desde BD."""
    conn = get_db()
    if not conn:
        return 0, 999999, False, "unknown"
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT plan, renders_used, renders_limit FROM users WHERE id = %s",
                (user_id,)
            )
            row = cur.fetchone()
        if not row:
            return 0, 999999, False, "unknown"
        used  = row["renders_used"]
        limit = USER_PLAN_LIMITS.get(row["plan"], row["renders_limit"])
        return used, limit, used >= limit, row["plan"]
    except Exception as e:
        logger.error(f"Error en _check_user_render_limit: {e}")
        return 0, 999999, False, "unknown"

def _increment_user_renders(user_id: str) -> None:
    """Incrementa renders_used del usuario en BD."""
    conn = get_db()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET renders_used = renders_used + 1, updated_at = NOW() WHERE id = %s",
                (user_id,)
            )
    except Exception as e:
        logger.error(f"Error en _increment_user_renders: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  GENERADOR DE IMÁGENES (módulo Design)
# ═══════════════════════════════════════════════════════════════════════════════

def _render_pil(request: "MultiTextRequest") -> "Image.Image":
    """Pipeline de render puro: carga imagen, aplica efectos/textos/shapes/overlays.
    Devuelve PIL Image (RGBA). No hace rate-limit ni guarda en disco."""
    # Cargar imagen
    if request.template_name.startswith(("http://", "https://")):
        local_path = None
        if "/storage/" in request.template_name:
            fname = request.template_name.split("/storage/")[-1].split("?")[0]
            local_path = os.path.join(STORAGE_DIR, fname)
        elif "/static/temp/" in request.template_name:
            fname = request.template_name.split("/static/temp/")[-1].split("?")[0]
            local_path = os.path.join("static", "temp", fname)
        if local_path:
            if not os.path.exists(local_path):
                raise HTTPException(status_code=404, detail=f"Imagen no encontrada en storage: {os.path.basename(local_path)}")
            logger.info(f"📂 Leyendo imagen del storage local: {local_path}")
            image = Image.open(local_path).convert("RGBA")
        else:
            logger.info(f"🔵 Descargando imagen: {request.template_name}")
            session = build_retry_session()
            response = session.get(request.template_name, timeout=15)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content)).convert("RGBA")
    else:
        template_path = os.path.join("templates", request.template_name)
        if not os.path.exists(template_path):
            raise HTTPException(status_code=404, detail=f"Imagen no encontrada: {request.template_name}")
        image = Image.open(template_path).convert("RGBA")

    width, height = image.size
    logger.info(f"📐 Dimensiones: {width}x{height}")

    # Multi-formato
    if request.format_width and request.format_height:
        fw, fh = request.format_width, request.format_height
        zoom = max(0.01, request.img_zoom)
        pan_x = int(round(request.img_pan_x))
        pan_y = int(round(request.img_pan_y))
        new_w = max(1, int(round(width * zoom)))
        new_h = max(1, int(round(height * zoom)))
        img_scaled = image.resize((new_w, new_h), Image.LANCZOS)
        artboard = Image.new("RGBA", (fw, fh), (0, 0, 0, 255))
        artboard.paste(img_scaled, (pan_x, pan_y), img_scaled)
        image = artboard
        width, height = fw, fh
        logger.info(f"🖼️ Artboard formato {fw}x{fh} · zoom={zoom:.2f} · pan=({pan_x},{pan_y})")

    # Filtro
    if request.filter_name and request.filter_name != "none":
        logger.info(f"🎨 Aplicando filtro: {request.filter_name}")
        image = apply_filter(image, request.filter_name)

    # Viñeta
    if request.vignette_enabled:
        sides = request.vignette_sides or ["top", "right", "bottom", "left"]
        logger.info(f"🎞️ Viñeta: color={request.vignette_color} op={request.vignette_opacity} size={request.vignette_size}")
        image = apply_vignette(image, color=request.vignette_color, opacity=request.vignette_opacity,
                               size=request.vignette_size, sides=sides, tone=request.vignette_filter)

    # Sustituir variables {varname}
    if request.vars:
        sorted_keys = sorted(request.vars.keys(), key=len, reverse=True)
        for text_field in request.texts:
            for key in sorted_keys:
                text_field.text = text_field.text.replace(f"{{{key}}}", request.vars[key])

    # Formas (z_index ordenado)
    sorted_shapes = sorted(request.shapes or [], key=lambda s: s.z_index)
    for shape in sorted_shapes:
        try:
            _render_canvas_shape(image, shape)
            logger.info(f"🔷 Forma renderizada: {shape.shape_type} en ({shape.x},{shape.y})")
        except Exception as e:
            logger.warning(f"⚠️ Error forma: {e}")

    # Textos
    for idx, text_field in enumerate(request.texts):
        if text_field.countdown_mode:
            now_utc = datetime.now(timezone.utc)
            cd_fmt = text_field.countdown_format or "HH:MM:SS"
            cd_exp = text_field.countdown_expired_text or "¡Oferta expirada!"
            try:
                if text_field.countdown_mode == "event" and text_field.countdown_event_end_utc:
                    end_utc = datetime.strptime(
                        text_field.countdown_event_end_utc, "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=timezone.utc)
                    seconds_left = max(0.0, (end_utc - now_utc).total_seconds())
                elif text_field.countdown_mode == "urgency":
                    ts_var_name = text_field.countdown_ts_var or "timer_final"
                    ts_value = (request.vars or {}).get(ts_var_name, "")
                    _MAX_FUTURE_S = 366 * 24 * 3600
                    try:
                        ts_int = int(float(str(ts_value)))
                        end_utc = datetime.fromtimestamp(ts_int, tz=timezone.utc)
                        raw_left = (end_utc - now_utc).total_seconds()
                        if raw_left > _MAX_FUTURE_S:
                            logger.warning(f"⚠️ timer_final={ts_int} muy en el futuro")
                        seconds_left = max(0.0, raw_left)
                    except (ValueError, TypeError, OSError):
                        seconds_left = 86400
                else:
                    seconds_left = 0.0
            except Exception as ce:
                logger.warning(f"⚠️ Error countdown: {ce}")
                seconds_left = 0.0
            text_field.text = _format_countdown(seconds_left, cd_fmt, cd_exp)
            if (text_field.countdown_urgency_color and seconds_left > 0
                    and seconds_left <= (text_field.countdown_urgency_threshold_h or 3.0) * 3600):
                text_field.font_color = text_field.countdown_urgency_color
            logger.info(f"⏱ Countdown: '{text_field.text}' ({seconds_left:.0f}s)")

        logger.info(f"Texto {idx+1}: '{text_field.text[:50]}'" if len(text_field.text) <= 50 else f"Texto {idx+1}: '{text_field.text[:50]}...'")
        logger.info(f"  → font_size={text_field.font_size}  align={text_field.alignment}")
        font_path = get_font_path(text_field.font_name)
        try:
            fs_scale = FONT_SIZE_SCALE.get(text_field.font_name, 1.0)
            scaled_size = max(1, int(round(text_field.font_size * fs_scale)))
            if fs_scale != 1.0:
                logger.info(f"  → Escala fuente '{text_field.font_name}': {fs_scale}× → {scaled_size}px")
            font = ImageFont.truetype(font_path, scaled_size)
        except Exception as e:
            logger.warning(f"⚠️ Fuente: {e}")
            font = ImageFont.load_default()
        image = draw_text_with_effects(image, text_field, font, render_scale=request.render_scale)

    # Overlays (logos, stickers, badges)
    for ov in (request.overlays or []):
        try:
            if ov.src.startswith("data:"):
                _, data = ov.src.split(",", 1)
                ov_img = Image.open(BytesIO(base64.b64decode(data))).convert("RGBA")
            else:
                session2 = build_retry_session()
                ov_resp = session2.get(ov.src, timeout=10)
                ov_resp.raise_for_status()
                ov_img = Image.open(BytesIO(ov_resp.content)).convert("RGBA")
            ov_w, ov_h = max(1, ov.width), max(1, ov.height)
            mask_type = getattr(ov, 'mask_type', 'none') or 'none'
            auto_fit  = getattr(ov, 'mask_auto_fit', True)
            mask_rad  = getattr(ov, 'mask_radius', 0) or 0
            rotation  = getattr(ov, 'rotation', 0) or 0
            border_w  = getattr(ov, 'mask_border_width', 0) or 0
            border_c  = parse_color_with_opacity(getattr(ov, 'mask_border_color', '#ffffff'), getattr(ov, 'mask_border_opacity', 100))
            shadow_en = getattr(ov, 'mask_shadow_enabled', False)
            shadow_c  = getattr(ov, 'mask_shadow_color', '#000000')
            shadow_op = getattr(ov, 'mask_shadow_opacity', 70)
            shadow_bl = getattr(ov, 'mask_shadow_blur', 8)
            shadow_dx = getattr(ov, 'mask_shadow_x', 0)
            shadow_dy = getattr(ov, 'mask_shadow_y', 4)
            if auto_fit and mask_type != "none":
                ov_img = _auto_fit_overlay(ov_img, mask_type, ov_w, ov_h)
            else:
                ov_img = ov_img.resize((ov_w, ov_h), Image.LANCZOS)
            if mask_type != "none":
                ov_img = _apply_overlay_mask(ov_img, mask_type, mask_rad)
            border_exp = 0
            if border_w > 0:
                ov_img, border_exp = _apply_overlay_border(ov_img, mask_type, border_w, border_c, mask_rad)
            pre_rot_w, pre_rot_h = ov_img.width, ov_img.height
            paste_x, paste_y = ov.x - border_exp, ov.y - border_exp
            if rotation:
                ov_img = ov_img.rotate(-rotation, expand=True, resample=Image.BICUBIC)
                new_w, new_h = ov_img.size
                paste_x = ov.x - border_exp + (pre_rot_w - new_w) // 2
                paste_y = ov.y - border_exp + (pre_rot_h - new_h) // 2
            if ov.opacity < 1.0:
                r2, g2, b2, a2 = ov_img.split()
                a2 = a2.point(lambda p: int(p * ov.opacity))
                ov_img.putalpha(a2)
            if shadow_en:
                rs, gs, bs, _ = parse_color_with_opacity(shadow_c, shadow_op)
                _, _, _, alpha_ch = ov_img.split()
                pad = int(shadow_bl * 3) + abs(shadow_dx) + abs(shadow_dy) + 4
                pad_w = ov_img.width + pad * 2
                pad_h = ov_img.height + pad * 2
                sh_alpha_pad = Image.new("L", (pad_w, pad_h), 0)
                sh_alpha_src = alpha_ch.point(lambda p: int(p * shadow_op / 100))
                sh_alpha_pad.paste(sh_alpha_src, (pad, pad))
                sh_img = Image.new("RGBA", (pad_w, pad_h), (rs, gs, bs, 0))
                sh_img.putalpha(sh_alpha_pad)
                if shadow_bl > 0:
                    sh_img = sh_img.filter(ImageFilter.GaussianBlur(shadow_bl))
                sh_x = paste_x + shadow_dx - pad
                sh_y = paste_y + shadow_dy - pad
                src_x1 = max(0, -sh_x)
                src_y1 = max(0, -sh_y)
                dst_x  = max(0, sh_x)
                dst_y  = max(0, sh_y)
                src_x2 = src_x1 + min(sh_img.width  - src_x1, image.width  - dst_x)
                src_y2 = src_y1 + min(sh_img.height - src_y1, image.height - dst_y)
                if src_x2 > src_x1 and src_y2 > src_y1:
                    sh_crop = sh_img.crop((src_x1, src_y1, src_x2, src_y2))
                    image.paste(sh_crop, (dst_x, dst_y), sh_crop)
            image.paste(ov_img, (paste_x, paste_y), ov_img)
            logger.info(f"🖼️ Overlay ({paste_x},{paste_y}) máscara={mask_type} rot={rotation}")
        except Exception as e:
            logger.warning(f"⚠️ Error overlay: {e}")

    # Watermark
    if request.watermark:
        try:
            if image.mode != "RGBA":
                image = image.convert("RGBA")
            img_w, img_h = image.size
            wm_font_size = max(13, min(28, img_w // 55))
            wm_font = None
            for _fp in [
                "fonts/PassionOne-Regular.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            ]:
                try:
                    wm_font = ImageFont.truetype(_fp, wm_font_size)
                    break
                except Exception:
                    pass
            if wm_font is None:
                wm_font = ImageFont.load_default()
            wm_text = "\u2756 textonflow.com"
            _tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            _bb = _tmp.textbbox((0, 0), wm_text, font=wm_font)
            tw, th = _bb[2] - _bb[0], _bb[3] - _bb[1]
            margin = max(8, img_w // 90)
            pad_x, pad_y = 9, 5
            rx1 = img_w - tw - pad_x * 2 - margin
            ry1 = img_h - th - pad_y * 2 - margin
            rx2 = img_w - margin
            ry2 = img_h - margin
            overlay_wm = Image.new("RGBA", image.size, (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay_wm)
            od.rounded_rectangle([rx1, ry1, rx2, ry2], radius=5, fill=(0, 0, 0, 155))
            image = Image.alpha_composite(image, overlay_wm)
            ImageDraw.Draw(image).text((rx1 + pad_x, ry1 + pad_y), wm_text, font=wm_font, fill=(255, 255, 255, 215))
            logger.info("✦ Watermark aplicado")
        except Exception as _wm_err:
            logger.warning(f"⚠️ Error watermark: {_wm_err}")

    return image


@app.post("/generate-multi")
async def generate_multi_text(request: MultiTextRequest, http_req: Request):
    # ── Rate limit: usuario autenticado (JWT) o IP (fallback) ────────────────
    _user_payload = _get_current_user(http_req)
    _user_id      = _user_payload["sub"] if _user_payload else None
    _ip           = _get_client_ip(http_req)

    if _is_superadmin(http_req):
        _used, _limit = 0, 999999
    elif _user_id:
        # Usuario autenticado → verificar límite de su plan
        _used, _limit, _exceeded, _plan = _check_user_render_limit(_user_id)
        if _exceeded:
            raise HTTPException(
                status_code=429,
                detail=f"Límite de renders alcanzado ({_used}/{_limit} · Plan {_plan.capitalize()}). Actualiza tu plan en textonflow.com/precios",
                headers={"X-RateLimit-Used": str(_used), "X-RateLimit-Limit": str(_limit), "X-Plan": _plan},
            )
    else:
        # Sin JWT → rate limit por IP
        _used, _limit, _exceeded = _check_rate_limit(_ip)
        if _exceeded:
            raise HTTPException(
                status_code=429,
                detail=f"Límite diario alcanzado ({_limit} imágenes/día). Crea una cuenta gratis en textonflow.com",
                headers={"X-RateLimit-Used": str(_used), "X-RateLimit-Limit": str(_limit)},
            )
    try:
        # Cargar imagen (URL o local)
        if request.template_name.startswith(("http://", "https://")):
            # Si la URL apunta a nuestro propio /storage/ o /static/temp/, leer del disco
            # (Railway bloquea peticiones HTTPS circulares al mismo host)
            local_path = None
            if "/storage/" in request.template_name:
                fname = request.template_name.split("/storage/")[-1].split("?")[0]
                local_path = os.path.join(STORAGE_DIR, fname)
            elif "/static/temp/" in request.template_name:
                fname = request.template_name.split("/static/temp/")[-1].split("?")[0]
                local_path = os.path.join("static", "temp", fname)
            if local_path:
                if not os.path.exists(local_path):
                    raise HTTPException(status_code=404, detail=f"Imagen no encontrada en storage: {os.path.basename(local_path)}")
                logger.info(f"📂 Leyendo imagen del storage local: {local_path}")
                image = Image.open(local_path).convert("RGBA")
            else:
                logger.info(f"🔵 Descargando imagen: {request.template_name}")
                session = build_retry_session()
                response = session.get(request.template_name, timeout=15)
                response.raise_for_status()
                image = Image.open(BytesIO(response.content)).convert("RGBA")
        else:
            template_path = os.path.join("templates", request.template_name)
            if not os.path.exists(template_path):
                raise HTTPException(status_code=404, detail=f"Imagen no encontrada: {request.template_name}")
            image = Image.open(template_path).convert("RGBA")

        width, height = image.size
        logger.info(f"📐 Dimensiones: {width}x{height}")

        # ── Multi-formato: construir artboard con zoom+pan ANTES del filtro ────
        if request.format_width and request.format_height:
            fw, fh   = request.format_width, request.format_height
            zoom     = max(0.01, request.img_zoom)
            pan_x    = int(round(request.img_pan_x))
            pan_y    = int(round(request.img_pan_y))
            new_w    = max(1, int(round(width  * zoom)))
            new_h    = max(1, int(round(height * zoom)))
            img_scaled = image.resize((new_w, new_h), Image.LANCZOS)
            artboard = Image.new("RGBA", (fw, fh), (0, 0, 0, 255))
            artboard.paste(img_scaled, (pan_x, pan_y), img_scaled)
            image  = artboard
            width, height = fw, fh
            logger.info(f"🖼️ Artboard formato {fw}x{fh} · zoom={zoom:.2f} · pan=({pan_x},{pan_y})")

        # Aplicar filtro global de color/tono (antes de dibujar texto)
        if request.filter_name and request.filter_name != "none":
            logger.info(f"🎨 Aplicando filtro: {request.filter_name}")
            image = apply_filter(image, request.filter_name)

        # Aplicar viñeta (encima del filtro, antes de los textos)
        if request.vignette_enabled:
            sides = request.vignette_sides or ["top", "right", "bottom", "left"]
            logger.info(f"🎞️ Viñeta: color={request.vignette_color} op={request.vignette_opacity} size={request.vignette_size} sides={sides} tone={request.vignette_filter}")
            image = apply_vignette(
                image,
                color   = request.vignette_color,
                opacity = request.vignette_opacity,
                size    = request.vignette_size,
                sides   = sides,
                tone    = request.vignette_filter,
            )

        # Sustituir variables {varname} con los valores de request.vars
        if request.vars:
            sorted_keys = sorted(request.vars.keys(), key=len, reverse=True)
            for text_field in request.texts:
                for key in sorted_keys:
                    text_field.text = text_field.text.replace(f"{{{key}}}", request.vars[key])

        # Renderizar Formas de canvas (ordenadas por z_index) — ANTES que los textos
        sorted_shapes = sorted(request.shapes or [], key=lambda s: s.z_index)
        for shape in sorted_shapes:
            try:
                _render_canvas_shape(image, shape)
                logger.info(f"🔷 Forma renderizada: {shape.shape_type} en ({shape.x},{shape.y})")
            except Exception as e:
                logger.warning(f"⚠️ Error renderizando forma: {e}")

        for idx, text_field in enumerate(request.texts):
            # ── Countdown: calcular texto antes de renderizar ──────────────────
            if text_field.countdown_mode:
                now_utc = datetime.now(timezone.utc)
                cd_fmt = text_field.countdown_format or "HH:MM:SS"
                cd_exp = text_field.countdown_expired_text or "¡Oferta expirada!"
                try:
                    if text_field.countdown_mode == "event" and text_field.countdown_event_end_utc:
                        end_utc = datetime.strptime(
                            text_field.countdown_event_end_utc, "%Y-%m-%dT%H:%M:%SZ"
                        ).replace(tzinfo=timezone.utc)
                        seconds_left = max(0.0, (end_utc - now_utc).total_seconds())
                    elif text_field.countdown_mode == "urgency":
                        # timer_final = Unix timestamp (segundos) del momento en que expira
                        ts_var_name = text_field.countdown_ts_var or "timer_final"
                        ts_value = (request.vars or {}).get(ts_var_name, "")
                        _MAX_FUTURE_S = 366 * 24 * 3600  # máximo 366 días
                        try:
                            ts_int = int(float(str(ts_value)))
                            end_utc = datetime.fromtimestamp(ts_int, tz=timezone.utc)
                            raw_left = (end_utc - now_utc).total_seconds()
                            if raw_left > _MAX_FUTURE_S:
                                logger.warning(
                                    f"⚠️ timer_final={ts_int} está {raw_left/86400:.0f} días en el futuro "
                                    f"— posible acumulación en ManyChat. Verifica que el campo se ESTABLECE "
                                    f"(no se suma) al valor de la calculadora."
                                )
                            seconds_left = max(0.0, raw_left)
                        except (ValueError, TypeError, OSError):
                            seconds_left = 86400  # preview: 24 h cuando no hay valor
                    else:
                        seconds_left = 0.0
                except Exception as ce:
                    logger.warning(f"⚠️ Error calculando countdown: {ce}")
                    seconds_left = 0.0
                text_field.text = _format_countdown(seconds_left, cd_fmt, cd_exp)
                # Urgencia: cambiar color si faltan menos de N horas
                if (text_field.countdown_urgency_color
                        and seconds_left > 0
                        and seconds_left <= (text_field.countdown_urgency_threshold_h or 3.0) * 3600):
                    text_field.font_color = text_field.countdown_urgency_color
                logger.info(f"⏱ Countdown calculado: '{text_field.text}' ({seconds_left:.0f}s restantes)")
            # ──────────────────────────────────────────────────────────────────

            logger.info(f"Procesando texto {idx+1}: '{text_field.text[:50]}...' " if len(text_field.text) > 50 else f"Procesando texto {idx+1}: '{text_field.text}'")
            logger.info(f"  → font_size={text_field.font_size}  line_spacing={text_field.line_spacing}  align={text_field.alignment}")

            font_path = get_font_path(text_field.font_name)
            try:
                fs_scale = FONT_SIZE_SCALE.get(text_field.font_name, 1.0)
                scaled_size = max(1, int(round(text_field.font_size * fs_scale)))
                if fs_scale != 1.0:
                    logger.info(f"  → Escala de fuente '{text_field.font_name}': {fs_scale}× → size {text_field.font_size}→{scaled_size}px")
                font = ImageFont.truetype(font_path, scaled_size)
            except Exception as e:
                logger.warning(f"⚠️ Error cargando fuente: {e}")
                font = ImageFont.load_default()

            image = draw_text_with_effects(image, text_field, font, render_scale=request.render_scale)

        # Aplicar overlays de imagen (logos, firmas, badges)
        for ov in (request.overlays or []):
            try:
                if ov.src.startswith("data:"):
                    _, data = ov.src.split(",", 1)
                    ov_img = Image.open(BytesIO(base64.b64decode(data))).convert("RGBA")
                else:
                    session2 = build_retry_session()
                    ov_resp = session2.get(ov.src, timeout=10)
                    ov_resp.raise_for_status()
                    ov_img = Image.open(BytesIO(ov_resp.content)).convert("RGBA")
                ov_w, ov_h = max(1, ov.width), max(1, ov.height)
                mask_type   = getattr(ov, 'mask_type', 'none') or 'none'
                auto_fit    = getattr(ov, 'mask_auto_fit', True)
                mask_rad    = getattr(ov, 'mask_radius', 0) or 0
                rotation    = getattr(ov, 'rotation', 0) or 0
                border_w    = getattr(ov, 'mask_border_width', 0) or 0
                border_c    = parse_color_with_opacity(
                                  getattr(ov, 'mask_border_color', '#ffffff'),
                                  getattr(ov, 'mask_border_opacity', 100))
                shadow_en   = getattr(ov, 'mask_shadow_enabled', False)
                shadow_c    = getattr(ov, 'mask_shadow_color', '#000000')
                shadow_op   = getattr(ov, 'mask_shadow_opacity', 70)
                shadow_bl   = getattr(ov, 'mask_shadow_blur', 8)
                shadow_dx   = getattr(ov, 'mask_shadow_x', 0)
                shadow_dy   = getattr(ov, 'mask_shadow_y', 4)
                # 1. Auto-fit: escala para cubrir la máscara sin deformar
                if auto_fit and mask_type != "none":
                    ov_img = _auto_fit_overlay(ov_img, mask_type, ov_w, ov_h)
                else:
                    ov_img = ov_img.resize((ov_w, ov_h), Image.LANCZOS)
                # 2. Aplicar máscara de recorte
                if mask_type != "none":
                    ov_img = _apply_overlay_mask(ov_img, mask_type, mask_rad)
                # 3. Borde FUERA de la máscara
                border_exp = 0
                if border_w > 0:
                    ov_img, border_exp = _apply_overlay_border(ov_img, mask_type, border_w, border_c, mask_rad)
                # 4. Rotar (CSS clockwise → PIL counterclockwise)
                pre_rot_w, pre_rot_h = ov_img.width, ov_img.height
                paste_x, paste_y = ov.x - border_exp, ov.y - border_exp
                if rotation:
                    ov_img = ov_img.rotate(-rotation, expand=True, resample=Image.BICUBIC)
                    new_w, new_h = ov_img.size
                    paste_x = ov.x - border_exp + (pre_rot_w - new_w) // 2
                    paste_y = ov.y - border_exp + (pre_rot_h - new_h) // 2
                # 5. Aplicar opacidad
                if ov.opacity < 1.0:
                    r2, g2, b2, a2 = ov_img.split()
                    a2 = a2.point(lambda p: int(p * ov.opacity))
                    ov_img.putalpha(a2)
                # 6. Sombra (se pega debajo del sticker)
                if shadow_en:
                    rs, gs, bs, _ = parse_color_with_opacity(shadow_c, shadow_op)
                    _, _, _, alpha_ch = ov_img.split()
                    # Canvas expandido para que el blur no quede recortado en bordes
                    pad = int(shadow_bl * 3) + abs(shadow_dx) + abs(shadow_dy) + 4
                    pad_w = ov_img.width  + pad * 2
                    pad_h = ov_img.height + pad * 2
                    sh_alpha_pad = Image.new("L", (pad_w, pad_h), 0)
                    sh_alpha_src = alpha_ch.point(lambda p: int(p * shadow_op / 100))
                    sh_alpha_pad.paste(sh_alpha_src, (pad, pad))
                    sh_img = Image.new("RGBA", (pad_w, pad_h), (rs, gs, bs, 0))
                    sh_img.putalpha(sh_alpha_pad)
                    if shadow_bl > 0:
                        sh_img = sh_img.filter(ImageFilter.GaussianBlur(shadow_bl))
                    sh_x = paste_x + shadow_dx - pad
                    sh_y = paste_y + shadow_dy - pad
                    # Recortar la sombra al área visible de la imagen destino
                    src_x1 = max(0, -sh_x)
                    src_y1 = max(0, -sh_y)
                    dst_x  = max(0, sh_x)
                    dst_y  = max(0, sh_y)
                    src_x2 = src_x1 + min(sh_img.width  - src_x1, image.width  - dst_x)
                    src_y2 = src_y1 + min(sh_img.height - src_y1, image.height - dst_y)
                    if src_x2 > src_x1 and src_y2 > src_y1:
                        sh_crop = sh_img.crop((src_x1, src_y1, src_x2, src_y2))
                        image.paste(sh_crop, (dst_x, dst_y), sh_crop)
                image.paste(ov_img, (paste_x, paste_y), ov_img)
                logger.info(f"🖼️ Overlay aplicado en ({paste_x},{paste_y}) máscara={mask_type} rot={rotation}")
            except Exception as e:
                logger.warning(f"⚠️ Error aplicando overlay: {e}")

        # ── Sello TextOnFlow (watermark) ─────────────────────────────────────
        if request.watermark:
            try:
                if image.mode != "RGBA":
                    image = image.convert("RGBA")
                img_w, img_h = image.size
                wm_font_size = max(13, min(28, img_w // 55))
                wm_font = None
                for _fp in [
                    "fonts/PassionOne-Regular.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                ]:
                    try:
                        wm_font = ImageFont.truetype(_fp, wm_font_size)
                        break
                    except Exception:
                        pass
                if wm_font is None:
                    wm_font = ImageFont.load_default()
                wm_text = "\u2756 textonflow.com"
                _tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
                _bb = _tmp.textbbox((0, 0), wm_text, font=wm_font)
                tw, th = _bb[2] - _bb[0], _bb[3] - _bb[1]
                margin = max(8, img_w // 90)
                pad_x, pad_y = 9, 5
                rx1 = img_w - tw - pad_x * 2 - margin
                ry1 = img_h - th - pad_y * 2 - margin
                rx2 = img_w - margin
                ry2 = img_h - margin
                overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                od.rounded_rectangle([rx1, ry1, rx2, ry2], radius=5, fill=(0, 0, 0, 155))
                image = Image.alpha_composite(image, overlay)
                ImageDraw.Draw(image).text(
                    (rx1 + pad_x, ry1 + pad_y), wm_text, font=wm_font, fill=(255, 255, 255, 215)
                )
                logger.info("✦ Watermark aplicado")
            except Exception as _wm_err:
                logger.warning(f"⚠️ Error en watermark: {_wm_err}")

        # Convertir a RGB y guardar como JPEG
        if image.mode == "RGBA":
            rgb_image = Image.new("RGB", image.size, (255, 255, 255))
            rgb_image.paste(image, mask=image.split()[3])
            image = rgb_image

        output_filename = f"gen_{uuid.uuid4()}.jpg"
        # Guardar en STORAGE_DIR (volumen persistente de Railway) para que la imagen
        # no desaparezca si el servidor se reinicia antes de que ManyChat/Facebook la descargue.
        storage_path = os.path.join(STORAGE_DIR, output_filename)
        os.makedirs(STORAGE_DIR, exist_ok=True)
        # subsampling=0 → 4:4:4, full color resolution en cada pixel.
        # El default de JPEG (subsampling=2 = 4:2:0) reduce la resolución
        # del color a la cuarta parte, causando pixelado en texto de color.
        # El texto blanco no lo sufre porque blanco no tiene chroma.
        image.save(storage_path, "JPEG", quality=95, subsampling=0)
        # También guardar en output/ para compatibilidad con el endpoint /image/
        local_path = os.path.join("output", output_filename)
        os.makedirs("output", exist_ok=True)
        image.save(local_path, "JPEG", quality=95, subsampling=0)

        base_url = _get_base_url(http_req)
        image_url = f"{base_url}/storage/{output_filename}"
        logger.info(f"✅ Imagen generada: {output_filename}")

        # ── Contadores ────────────────────────────────────────────────────────
        _increment_images_generated()
        if _user_id:
            _increment_user_renders(_user_id)
            _used_after = _used + 1
            _lim        = _limit
        else:
            _used_after, _lim = _increment_ip_usage(_ip)

        return {"image_url": image_url, "usage": {"used": _used_after, "limit": _lim}}

    except requests.exceptions.RequestException as e:
        logger.error(f"💥 Error de red: {e}")
        raise HTTPException(status_code=400, detail=f"Error descargando imagen: {str(e)}")
    except Exception as e:
        logger.error(f"💥 Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
#  DYNAMIC IMAGE API — Templates & Render
# ═══════════════════════════════════════════════════════════════════════════════

# ── Rate limiting in-memory ────────────────────────────────────────────────────
_RL_LOCK: threading.Lock = threading.Lock()
_RL_TIMESTAMPS: Dict[str, list] = {}  # template_id → [unix timestamps]

def _check_api_rl(template_id: str, limit_per_hour: int) -> bool:
    """True = allowed. False = rate limit exceeded."""
    now = time.time()
    cutoff = now - 3600.0
    with _RL_LOCK:
        ts = [t for t in _RL_TIMESTAMPS.get(template_id, []) if t > cutoff]
        if len(ts) >= limit_per_hour:
            _RL_TIMESTAMPS[template_id] = ts
            return False
        ts.append(now)
        _RL_TIMESTAMPS[template_id] = ts
        return True

def _track_render(template_id: str):
    """Increment per-template usage stats (fire-and-forget, no exception propagation)."""
    stats_path = os.path.join(TEMPLATES_API_DIR, f"{template_id}_stats.json")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        stats = {"total": 0, "by_day": {}, "last_render": None}
        if os.path.exists(stats_path):
            with open(stats_path) as f:
                stats = json.load(f)
        stats["total"] = stats.get("total", 0) + 1
        stats["by_day"][today] = stats["by_day"].get(today, 0) + 1
        stats["last_render"] = datetime.now(timezone.utc).isoformat()
        if len(stats["by_day"]) > 30:
            for k in sorted(stats["by_day"].keys())[:-30]:
                del stats["by_day"][k]
        with open(stats_path, "w") as f:
            json.dump(stats, f)
    except Exception as e:
        logger.warning(f"_track_render error: {e}")

def _read_template_stats(template_id: str) -> dict:
    stats_path = os.path.join(TEMPLATES_API_DIR, f"{template_id}_stats.json")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if os.path.exists(stats_path):
        try:
            with open(stats_path) as f:
                s = json.load(f)
            return {
                "total": s.get("total", 0),
                "today": s.get("by_day", {}).get(today, 0),
                "last_render": s.get("last_render"),
                "by_day": s.get("by_day", {}),
            }
        except Exception:
            pass
    return {"total": 0, "today": 0, "last_render": None, "by_day": {}}

class ApiTemplateRequest(BaseModel):
    name: str
    template_name: str
    texts: List[TextField] = []
    shapes: Optional[List[CanvasShape]] = []
    overlays: Optional[List[ImageOverlay]] = []
    filter_name: str = "none"
    render_scale: int = 2
    watermark: bool = False
    vignette_enabled: bool = False
    vignette_color: str = "#000000"
    vignette_opacity: float = 0.6
    vignette_size: float = 50.0
    vignette_sides: Optional[List[str]] = None
    vignette_filter: str = "none"
    format_width: Optional[int] = None
    format_height: Optional[int] = None
    img_pan_x: float = 0.0
    img_pan_y: float = 0.0
    img_zoom: float = 1.0


@app.post("/api/templates")
async def save_api_template(template: ApiTemplateRequest):
    """Guarda el diseño actual como template de API. Devuelve ID + URL de render."""
    tid = str(uuid.uuid4())[:8]
    path = os.path.join(TEMPLATES_API_DIR, f"{tid}.json")
    data = template.model_dump()
    data["id"] = tid
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    # Detectar variables {varname} en los textos
    vars_found = set()
    for t in data.get("texts", []):
        for m in re.findall(r'\{(\w+)\}', t.get("text", "")):
            vars_found.add(m)
    data["variables"] = sorted(vars_found)
    data["api_key"] = secrets.token_urlsafe(20)
    data["require_api_key"] = False
    data["rate_limit_per_hour"] = 500
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"📋 Template API guardado: {tid} | vars={data['variables']}")
    return {
        "id": tid,
        "variables": data["variables"],
        "render_url": f"/render/{tid}",
        "api_key": data["api_key"],
        "require_api_key": False,
        "rate_limit_per_hour": 500,
    }


@app.get("/api/templates")
async def list_api_templates():
    """Lista todos los templates de API guardados."""
    templates = []
    if os.path.exists(TEMPLATES_API_DIR):
        for fname in sorted(os.listdir(TEMPLATES_API_DIR), reverse=True):
            if fname.endswith(".json") and not fname.endswith("_stats.json"):
                try:
                    with open(os.path.join(TEMPLATES_API_DIR, fname)) as f:
                        d = json.load(f)
                    tid = d["id"]
                    raw_key = d.get("api_key", "")
                    masked_key = (raw_key[:6] + "•" * 8 + raw_key[-4:]) if len(raw_key) >= 10 else raw_key
                    stats = _read_template_stats(tid)
                    templates.append({
                        "id": tid,
                        "name": d.get("name", "Sin nombre"),
                        "variables": d.get("variables", []),
                        "created_at": d.get("created_at", ""),
                        "render_url": f"/render/{tid}",
                        "api_key": d.get("api_key", ""),
                        "api_key_masked": masked_key,
                        "require_api_key": d.get("require_api_key", False),
                        "rate_limit_per_hour": d.get("rate_limit_per_hour", 500),
                        "stats": stats,
                    })
                except Exception:
                    pass
    return templates


@app.delete("/api/templates/{template_id}")
async def delete_api_template(template_id: str):
    """Elimina un template de API por su ID."""
    if not re.match(r'^[a-f0-9\-]+$', template_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    path = os.path.join(TEMPLATES_API_DIR, f"{template_id}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Template no encontrado")
    os.remove(path)
    # Delete stats file too
    stats_path = os.path.join(TEMPLATES_API_DIR, f"{template_id}_stats.json")
    if os.path.exists(stats_path):
        os.remove(stats_path)
    logger.info(f"🗑️ Template API eliminado: {template_id}")
    return {"ok": True}


@app.get("/api/templates/{template_id}/stats")
async def get_template_stats(template_id: str):
    """Devuelve estadísticas de uso de un template."""
    if not re.match(r'^[a-f0-9\-]+$', template_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    path = os.path.join(TEMPLATES_API_DIR, f"{template_id}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Template no encontrado")
    return _read_template_stats(template_id)


@app.post("/api/templates/{template_id}/rotate-key")
async def rotate_template_key(template_id: str):
    """Genera una nueva API key para el template."""
    if not re.match(r'^[a-f0-9\-]+$', template_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    path = os.path.join(TEMPLATES_API_DIR, f"{template_id}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Template no encontrado")
    with open(path) as f:
        data = json.load(f)
    new_key = secrets.token_urlsafe(20)
    data["api_key"] = new_key
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"🔄 API key rotada: {template_id}")
    return {"ok": True, "api_key": new_key}


@app.patch("/api/templates/{template_id}/settings")
async def update_template_settings(template_id: str, body: dict):
    """Actualiza require_api_key y rate_limit_per_hour del template."""
    if not re.match(r'^[a-f0-9\-]+$', template_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    path = os.path.join(TEMPLATES_API_DIR, f"{template_id}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Template no encontrado")
    with open(path) as f:
        data = json.load(f)
    if "require_api_key" in body:
        data["require_api_key"] = bool(body["require_api_key"])
    if "rate_limit_per_hour" in body:
        rl = int(body["rate_limit_per_hour"])
        data["rate_limit_per_hour"] = max(1, min(rl, 100000))
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"ok": True, "require_api_key": data["require_api_key"], "rate_limit_per_hour": data["rate_limit_per_hour"]}


@app.get("/render/{template_id}")
async def render_api_template(template_id: str, request: Request):
    """Endpoint público de render dinámico.
    Acepta variables como query params: /render/{id}?nombre=Juan&descuento=30
    Devuelve imagen JPEG directamente (sin guardar en disco).
    """
    if not re.match(r'^[a-f0-9\-]+$', template_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    path = os.path.join(TEMPLATES_API_DIR, f"{template_id}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' no encontrado.")
    with open(path) as f:
        data = json.load(f)

    # ── API Key check ──────────────────────────────────────────────────────────
    if data.get("require_api_key"):
        provided = (request.headers.get("X-API-Key") or
                    request.query_params.get("api_key") or "")
        if provided != data.get("api_key", ""):
            raise HTTPException(status_code=401,
                detail="API Key inválida. Usa el header X-API-Key o el parámetro ?api_key=")
    # ── Rate limit ─────────────────────────────────────────────────────────────
    rl = data.get("rate_limit_per_hour", 500)
    if not _check_api_rl(template_id, rl):
        raise HTTPException(status_code=429,
            detail=f"Rate limit: {rl} renders/hora para este template.")

    # Variables desde query params (filtrar api_key)
    vars_dict = {k: v for k, v in request.query_params.items() if k != "api_key"} or None

    # Reconstruir request de render
    texts    = [TextField(**t)      for t in data.get("texts",    [])]
    shapes   = [CanvasShape(**s)    for s in (data.get("shapes")   or [])]
    overlays = [ImageOverlay(**o)   for o in (data.get("overlays") or [])]
    mr = MultiTextRequest(
        template_name    = data["template_name"],
        texts            = texts,
        vars             = vars_dict,
        shapes           = shapes,
        overlays         = overlays,
        filter_name      = data.get("filter_name",       "none"),
        render_scale     = data.get("render_scale",      2),
        watermark        = data.get("watermark",         False),
        vignette_enabled = data.get("vignette_enabled",  False),
        vignette_color   = data.get("vignette_color",    "#000000"),
        vignette_opacity = data.get("vignette_opacity",  0.6),
        vignette_size    = data.get("vignette_size",     50.0),
        vignette_sides   = data.get("vignette_sides"),
        vignette_filter  = data.get("vignette_filter",   "none"),
        format_width     = data.get("format_width"),
        format_height    = data.get("format_height"),
        img_pan_x        = data.get("img_pan_x",         0.0),
        img_pan_y        = data.get("img_pan_y",         0.0),
        img_zoom         = data.get("img_zoom",          1.0),
    )
    try:
        image = _render_pil(mr)
        if image.mode == "RGBA":
            rgb = Image.new("RGB", image.size, (255, 255, 255))
            rgb.paste(image, mask=image.split()[3])
            image = rgb
        buf = BytesIO()
        image.save(buf, "JPEG", quality=90, subsampling=0)
        buf.seek(0)
        _track_render(template_id)
        logger.info(f"🚀 /render/{template_id} → vars={list(vars_dict.keys()) if vars_dict else []}")
        return Response(
            content    = buf.getvalue(),
            media_type = "image/jpeg",
            headers    = {
                "Cache-Control":           "public, max-age=30",
                "X-TextOnFlow-Template":   template_id,
                "X-TextOnFlow-Variables":  ",".join(vars_dict.keys()) if vars_dict else "",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"💥 Error /render/{template_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class WebhookRenderRequest(BaseModel):
    template_id: str
    variables: Dict[str, str] = {}
    secret: Optional[str] = None
    output_format: str = "url"  # "url" | "base64"


@app.post("/webhook/render")
async def webhook_render(req: WebhookRenderRequest, request: Request):
    """
    Webhook POST para integraciones Make / Zapier / CRM.
    Cuerpo: {"template_id":"abc","variables":{"nombre":"Juan","descuento":"30"}}
    Devuelve JSON con image_url y metadatos — NO requiere API key.
    """
    tid = req.template_id
    if not re.match(r'^[a-f0-9\-]+$', tid):
        raise HTTPException(status_code=400, detail="template_id inválido")
    path = os.path.join(TEMPLATES_API_DIR, f"{tid}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Template '{tid}' no encontrado. Créalo primero desde el editor.")
    with open(path) as f:
        data = json.load(f)

    # ── API Key check ──────────────────────────────────────────────────────────
    if data.get("require_api_key"):
        provided = (request.headers.get("X-API-Key") or req.secret or "")
        if provided != data.get("api_key", ""):
            raise HTTPException(status_code=401,
                detail="API Key inválida. Pasa el header X-API-Key o el campo 'secret' en el body.")
    elif data.get("webhook_secret") and req.secret != data.get("webhook_secret"):
        raise HTTPException(status_code=401, detail="webhook_secret incorrecto.")
    # ── Rate limit ─────────────────────────────────────────────────────────────
    rl = data.get("rate_limit_per_hour", 500)
    if not _check_api_rl(tid, rl):
        raise HTTPException(status_code=429,
            detail=f"Rate limit: {rl} renders/hora para este template.")

    vars_dict = req.variables if req.variables else None
    texts    = [TextField(**t)    for t in data.get("texts",    [])]
    shapes   = [CanvasShape(**s)  for s in (data.get("shapes")   or [])]
    overlays = [ImageOverlay(**o) for o in (data.get("overlays") or [])]
    mr = MultiTextRequest(
        template_name    = data["template_name"],
        texts            = texts,
        vars             = vars_dict,
        shapes           = shapes,
        overlays         = overlays,
        filter_name      = data.get("filter_name",      "none"),
        render_scale     = data.get("render_scale",     2),
        watermark        = data.get("watermark",        False),
        vignette_enabled = data.get("vignette_enabled", False),
        vignette_color   = data.get("vignette_color",   "#000000"),
        vignette_opacity = data.get("vignette_opacity", 0.6),
        vignette_size    = data.get("vignette_size",    50.0),
        vignette_sides   = data.get("vignette_sides"),
        vignette_filter  = data.get("vignette_filter",  "none"),
        format_width     = data.get("format_width"),
        format_height    = data.get("format_height"),
        img_pan_x        = data.get("img_pan_x",        0.0),
        img_pan_y        = data.get("img_pan_y",        0.0),
        img_zoom         = data.get("img_zoom",         1.0),
    )
    try:
        image = _render_pil(mr)
        if image.mode == "RGBA":
            rgb = Image.new("RGB", image.size, (255, 255, 255))
            rgb.paste(image, mask=image.split()[3])
            image = rgb

        if req.output_format == "base64":
            buf = BytesIO()
            image.save(buf, "JPEG", quality=90, subsampling=0)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return {
                "ok": True,
                "format": "base64",
                "image_base64": f"data:image/jpeg;base64,{b64}",
                "template_id": tid,
                "variables_used": list(vars_dict.keys()) if vars_dict else [],
            }

        # Guardar JPEG en STORAGE_DIR y devolver URL pública
        slug = "_".join(v[:10] for v in (vars_dict or {}).values())[:30].strip("_")
        fname = f"wh_{tid}_{slug}_{uuid.uuid4().hex[:6]}.jpg" if slug else f"wh_{tid}_{uuid.uuid4().hex[:8]}.jpg"
        fpath = os.path.join(STORAGE_DIR, fname)
        image.save(fpath, "JPEG", quality=90, subsampling=0)

        base_url = str(request.base_url).rstrip("/")
        image_url = f"{base_url}/storage/{fname}"
        _track_render(tid)
        logger.info(f"🔔 /webhook/render tid={tid} → {fname} vars={list(vars_dict.keys()) if vars_dict else []}")
        return {
            "ok": True,
            "image_url": image_url,
            "template_id": tid,
            "variables_used": list(vars_dict.keys()) if vars_dict else [],
            "filename": fname,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"💥 /webhook/render {tid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/templates/{template_id}/secret")
async def set_template_secret(template_id: str, body: dict):
    """Establece o actualiza el webhook_secret de un template."""
    if not re.match(r'^[a-f0-9\-]+$', template_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    path = os.path.join(TEMPLATES_API_DIR, f"{template_id}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Template no encontrado")
    with open(path) as f:
        data = json.load(f)
    secret = body.get("secret", "")
    if secret:
        data["webhook_secret"] = secret
    else:
        data.pop("webhook_secret", None)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"ok": True, "secret_set": bool(secret)}


@app.get("/image/{filename}")
async def get_image(filename: str):
    file_path = os.path.join("output", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    return FileResponse(
        file_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


IMAGEN_STYLES = {
    "Ilustración Bíblica": (
        "strict mid-century modern biblical illustration — geometric shapes, angular stylized faces, large almond expressive eyes, flat colors only, subtle vintage grain texture evenly distributed, soft diffused lighting with minimal shadows, balanced minimal composition. "
        "Color palette EXCLUSIVELY: turquoise-blue #8BC8D8 #6BB1C1 #4BA3A8 #2B9C9C, warm skin tones #D4A574 #C89B6A #B8855A, dark hair #3D2817 #4A3425, cream neutrals #F1EAD9 #E8E5D8 #C9C5B2, golden accents #E6C577 #C89B3C — no other colors allowed. "
        "Scene strictly faithful to biblical scripture: ancient Near Eastern setting, authentic period clothing (1st century Judea or OT era), landscapes with olive trees, rocky hills, desert, ancient dwellings, bodies of water only. "
        "FORBIDDEN: modern elements, anachronisms, gradients, 3D rendering, gloss, fantasy not in scripture, European medieval armor, modern clothing or buildings or technology or vehicles, colors outside approved palette"
    ),
    "Plumilla & Acuarela": (
        "pen-and-ink with watercolor wash illustration style — loose expressive hand-drawn ink linework, cross-hatching and hatching for shadows and texture, light transparent watercolor washes over ink, mostly white or cream paper-like background. "
        "Character proportions: round bulbous heads, simple minimal facial features, dot or small oval eyes, soft cartoonish anatomy, spontaneous sketchy lines with visible hand-drawn imperfection. "
        "Color palette: black ink lines dominate, accents of limited soft watercolor — muted blues, warm oranges, pale sage greens, sandy yellows, cream whites — never more than 3-4 colors per scene. "
        "Mood: playful, editorial, whimsical, like a children's book or newspaper comic strip illustration. "
        "FORBIDDEN: photorealism, flat digital vector art, clean precise lines, gradients, 3D rendering, digital gloss, heavy shadows, complex backgrounds, more than 4 colors"
    ),
    "Monocromo":               "black and white monochromatic, high contrast, grayscale",
    "Color":                   "vibrant vivid colors, rich bold color palette, colorful",
    "Pista":                   "high fashion runway editorial photography, catwalk",
    "Risografía":              "risograph print art, limited color halftone texture, indie print",
    "Tecnicolor":              "Technicolor film, oversaturated vintage Hollywood color palette",
    "Arcilla gótica":          "gothic claymation style, dark clay stop-motion animation",
    "Dinamita":                "explosive cinematic action, smoke fire debris, dynamic energy",
    "Salón":                   "salon beauty portrait, soft flattering light, professional beauty",
    "Boceto a mano":           "hand drawn pencil sketch, graphite drawing, detailed linework",
    "Cinematográfico":         "cinematic movie still, dramatic film lighting, anamorphic lens",
    "Steampunk":               "steampunk Victorian sci-fi, brass gears, industrial fantasy",
    "Amanecer":                "golden hour sunrise, warm atmospheric glow, landscape photography",
    "Lucha mítica":            "epic mythological battle, fantasy warrior art, dramatic composition",
    "Surrealista":             "surrealist dreamlike, Salvador Dali inspired, impossible scenes",
    "Misterio":                "mysterious dark moody atmosphere, noir style, dramatic shadows",
    "Prendedor":               "enamel pin badge illustration, clean flat vector art design",
    "Cyborg":                  "cyberpunk cyborg aesthetic, futuristic human-robot, sci-fi neon",
    "Retrato tenue":           "soft low-key portrait, gentle diffused lighting, intimate mood",
    "Dibujo animado antiguo":  "1930s vintage cartoon animation style, classic retro cartoon",
    "Pintura al óleo":         "classical oil painting, old masters technique, rich brushwork",
}

class RefImage(BaseModel):
    data: str       # base64 sin prefijo data:URL
    mime_type: str  # image/jpeg, image/png, image/webp

# ── Mapa de referencias populares → descripción de estilo visual ────────────
# Permite que el usuario escriba "estilo simpsons" y Gemini reciba una
# descripción artística en lugar del nombre de la franquicia registrada.
_STYLE_MAP = [
    # Animación americana
    (r'\blos\s+simpsons?\b',            'estilo de dibujos animados americanos con personajes de piel amarilla brillante, ojos grandes redondos, contornos negros gruesos y paleta de colores vivos'),
    (r'\bsimpsons?\b',                  'estilo cartoon americano con piel amarilla brillante, ojos grandes y líneas gruesas de contorno'),
    (r'\bfamily\s+guy\b',               'estilo cartoon americano moderno con personajes rechonchos, ojos ovalados y humor visual'),
    (r'\bfuturama\b',                   'estilo de animación retro-futurista con diseño de ciencia ficción caricaturesco'),
    (r'\badventure\s+time\b',           'estilo cartoon indie con formas simples e irregulares, colores vibrantes y personajes expresivos'),
    (r'\brick\s+(?:and|y|&)\s+morty\b', 'estilo de animación indie americana con paleta de colores ácidos y personajes caricaturescos deformes'),
    (r'\bsouth\s+park\b',               'estilo de animación con figuras recortadas, colores planos y diseño minimalista de personajes'),
    (r'\bscooby[\s\-]?doo\b',           'estilo de dibujos animados clásicos americanos años 70, personajes en caricatura con fondo pintado'),
    (r'\bflintstone[s]?\b',             'estilo de dibujos animados prehistóricos retro con colores planos y fondo de roca'),
    (r'\blooney\s+tunes?\b',            'estilo de dibujos animados clásicos con movimientos exagerados y humor físico visual'),
    # Anime japonés
    (r'\bdragon\s*ball(?:\s+z|gt|super)?\b', 'estilo de anime japonés shonen con personajes musculosos, cabello puntiagudo, expresiones intensas y efectos de energía luminosa'),
    (r'\bnaruto(?:\s+shippuden)?\b',    'estilo de anime ninja japonés con personajes expresivos, marcas en el rostro y efectos de energía'),
    (r'\bone\s+piece\b',                'estilo de anime shonen japonés con personajes exagerados, ropa colorida y escenas de aventura'),
    (r'\bsailor\s+moon\b',              'estilo de anime shojo japonés con personajes femeninos elegantes, colores pastel, lazos y elementos mágicos'),
    (r'\bpokemon\b',                    'estilo de anime japonés infantil con criaturas adorables de colores vivos y diseño kawaii'),
    (r'\bpikachu\b',                    'criatura pequeña y adorable estilo anime japonés, redondeada con colores amarillos brillantes'),
    (r'\bmy\s+hero\s+academia\b',       'estilo de anime superhéroe japonés moderno con colores saturados y diseños de acción dinámica'),
    (r'\battack\s+on\s+titan\b',        'estilo de anime japonés oscuro con personajes realistas y entornos arquitectónicos detallados'),
    (r'\bdem[o0]n\s+slayer\b|\bkimetsu\b', 'estilo de anime japonés con efectos de agua y fuego muy detallados y paleta de colores rica'),
    (r'\bjujutsu\s+kaisen\b',           'estilo de anime japonés de acción moderna con efectos de maldición y diseño de personajes estilizados'),
    # Disney / Pixar
    (r'\bdisney\b',                     'estilo de animación clásica americana con personajes expresivos, colores vivos, líneas suaves y magia visual'),
    (r'\bpixar\b',                      'estilo de animación 3D cinematográfica con iluminación realista, texturas detalladas y personajes entrañables'),
    (r'\bmickey\s+mouse\b',             'estilo de cartoon clásico americano con personajes de orejas redondas, guantes blancos y diseño retro años 30'),
    (r'\bminnie\s+mouse\b',             'estilo de cartoon clásico con personaje femenino de orejas redondas, lunares y lazos grandes'),
    (r'\bdumbo\b',                      'estilo de animación clásica de fantasía con animal tierno de orejas grandes'),
    (r'\bmoana\b',                      'estilo de animación moderna 3D con personaje polinesia, océano tropical y colores cálidos'),
    (r'\bfrozen\b|\belsa\b',            'estilo de animación 3D con paleta de azules y blancos, efectos de hielo y cristales, ambiente invernal mágico'),
    (r'\blion\s+king\b',                'estilo de animación dramática con paisajes africanos, animales expresivos y iluminación cinematográfica'),
    # Marvel / DC / cómics
    (r'\bmarvel\b',                     'estilo de cómic americano superhéroe con colores saturados, efectos de acción dinámica y diseño musculoso'),
    (r'\bspider[\-\s]?man\b',           'estilo de superhéroe de cómic con traje de arácnido rojo y azul y movimientos acrobáticos'),
    (r'\bbatman\b',                     'estilo de superhéroe oscuro de cómic con capa y temática nocturna de ciudad gótica'),
    (r'\bsuperman\b',                   'estilo de superhéroe clásico americano con capa al viento y símbolo en el pecho'),
    (r'\bavengers\b',                   'estilo de cómic americano de superhéroes con equipo diverso y efectos de acción épica'),
    # Videojuegos
    (r'\bsuper\s+mario\b|\bmario\s+bros\b', 'estilo de videojuego de plataformas colorido con personaje rechoncho de bigote, gorro rojo y mundo de hongos'),
    (r'\bzelda\b',                      'estilo de videojuego de aventura fantasía con paleta de verdes y dorados, elfos y elementos medievales'),
    (r'\bminecraft\b',                  'estilo de arte pixelado 3D con mundo de cubos y bloques coloridos, estética de vóxeles'),
    (r'\bfortnite\b',                   'estilo de videojuego colorido con personajes caricaturescos, colores brillantes y estética pop'),
    (r'\boverwatch\b',                  'estilo de videojuego de héroes estilizados con diseños futuristas coloridos y efectos de habilidades'),
    # Cine y TV
    (r'\bstar\s+wars\b',                'estilo de ciencia ficción épica espacial con naves estelares, sables de luz, galaxias lejanas y efectos de energía'),
    (r'\bharry\s+potter\b|\bhogwarts\b', 'estilo de fantasía mágica con castillos medievales, varitas mágicas, criaturas mágicas y ambiente de academia de hechicería'),
    (r'\bjurassic\s+park\b',            'estilo de aventura prehistórica con dinosaurios detallados en entorno de selva tropical densa'),
    (r'\bgame\s+of\s+thrones\b',        'estilo de fantasía épica medieval con dragones, castillos imponentes y atmósfera oscura dramática'),
    # Estilos artísticos generales
    (r'\bchibi\b',                      'estilo chibi japonés con personajes miniaturizados, cabezas grandes en proporción y expresiones adorables'),
    (r'\bkawaii\b',                     'estilo visual japonés kawaii con personajes tiernos, colores pastel, mejillas rosadas y elementos adorables'),
    (r'\bpixel\s+art\b',                'estilo de arte en píxeles retro con gráficos de baja resolución, paleta de colores limitada y estética 8-bit o 16-bit'),
    (r'\bstudio\s+ghibli\b|\bghibli\b', 'estilo de animación japonesa artesanal con fondos detallados pintados a mano, naturaleza exuberante y personajes expresivos'),
]

def _rewrite_prompt(prompt: str) -> str:
    """
    Detecta referencias a franquicias/personajes conocidos y las reemplaza
    por descripciones de su estilo visual, evitando rechazos por copyright.
    Preserva el resto de la instrucción del usuario intacta.
    """
    result = prompt
    for pattern, replacement in _STYLE_MAP:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    # Limpiar "estilo estilo" redundante cuando el usuario ya escribió "estilo ..."
    result = re.sub(r'\bestilo\s+estilo\b', 'estilo', result, flags=re.IGNORECASE)
    return result


class GenerateImageRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "1:1"
    style: Optional[str] = None
    reference_images: Optional[List[RefImage]] = []


def _gemini_generate_image_worker(job_id: str, api_key: str, payload: dict):
    """Corre en un hilo separado. Llama a Gemini y guarda el resultado en _IMAGE_JOBS."""
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        if resp.status_code != 200:
            logger.error(f"Gemini error {resp.status_code}: {resp.text[:400]}")
            with _IMAGE_JOBS_LOCK:
                _IMAGE_JOBS[job_id]['status'] = 'error'
                _IMAGE_JOBS[job_id]['error'] = f"Error de Gemini ({resp.status_code}): {resp.text[:250]}"
            return
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            with _IMAGE_JOBS_LOCK:
                _IMAGE_JOBS[job_id]['status'] = 'error'
                _IMAGE_JOBS[job_id]['error'] = "Gemini no devolvió imágenes. Intenta con otro prompt."
            return
        resp_parts = candidates[0].get("content", {}).get("parts", [])
        for part in resp_parts:
            if "inlineData" in part:
                b64  = part["inlineData"].get("data", "")
                mime = part["inlineData"].get("mimeType", "image/png")
                with _IMAGE_JOBS_LOCK:
                    _IMAGE_JOBS[job_id]['status'] = 'done'
                    _IMAGE_JOBS[job_id]['image_b64'] = b64
                    _IMAGE_JOBS[job_id]['mime_type'] = mime
                return
        with _IMAGE_JOBS_LOCK:
            _IMAGE_JOBS[job_id]['status'] = 'error'
            _IMAGE_JOBS[job_id]['error'] = "Gemini no devolvió imagen en la respuesta. Intenta con otro prompt."
    except requests.Timeout:
        with _IMAGE_JOBS_LOCK:
            _IMAGE_JOBS[job_id]['status'] = 'error'
            _IMAGE_JOBS[job_id]['error'] = "Tiempo de espera agotado (120 s). Intenta de nuevo."
    except Exception as ex:
        with _IMAGE_JOBS_LOCK:
            _IMAGE_JOBS[job_id]['status'] = 'error'
            _IMAGE_JOBS[job_id]['error'] = str(ex)


@app.post("/api/generate-image")
async def generate_image(req: GenerateImageRequest):
    """
    Inicia la generación en segundo plano y devuelve job_id de inmediato.
    El cliente hace polling a /api/image-job/{job_id} cada 2 s.
    Esto evita que Railway corte la conexión por timeout.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada en el servidor")
    _cleanup_old_jobs()

    full_prompt = _rewrite_prompt(req.prompt.strip())
    if req.style and req.style in IMAGEN_STYLES:
        full_prompt += ", " + IMAGEN_STYLES[req.style]
    refs = req.reference_images or []
    if refs:
        if req.style == "Ilustración Bíblica":
            # Referencia SOLO de estilo artístico — no copiar contenido/objetos
            full_prompt = (
                "IMPORTANT — The attached reference image(s) are provided EXCLUSIVELY for artistic "
                "style guidance: illustration technique, brushwork, color palette, texture, and "
                "overall visual feel. DO NOT copy, include, or reproduce any objects, subjects, or "
                "elements shown in the reference image(s) — such as baskets, fish, bread, or any "
                "other specific items. Completely disregard the content/subject matter of the "
                "reference image(s). Generate ONLY what the text prompt describes, applying solely "
                "the artistic style extracted from the reference. "
                + full_prompt
            )
        else:
            full_prompt = (
                "Usa exactamente los productos que aparecen en las imágenes de referencia adjuntas, "
                "manteniendo su apariencia, logo y diseño originales. "
                + full_prompt
            )
    valid_ratios = {"1:1", "9:16", "3:4", "16:9", "4:3"}
    ar = req.aspect_ratio if req.aspect_ratio in valid_ratios else "1:1"

    parts = []
    for ref in refs:
        parts.append({"inlineData": {"mimeType": ref.mime_type, "data": ref.data}})
    parts.append({"text": full_prompt})
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": ar}
        }
    }

    job_id = str(uuid.uuid4())
    with _IMAGE_JOBS_LOCK:
        _IMAGE_JOBS[job_id] = {"status": "pending", "ts": time.time()}

    t = threading.Thread(target=_gemini_generate_image_worker, args=(job_id, api_key, payload), daemon=True)
    t.start()

    return {"job_id": job_id}


@app.get("/api/image-job/{job_id}")
async def get_image_job(job_id: str):
    """Devuelve el estado del job. El cliente hace polling aquí."""
    with _IMAGE_JOBS_LOCK:
        job = _IMAGE_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado o expirado")
    status = job.get("status", "pending")
    if status == "done":
        return {"status": "done", "image_b64": job["image_b64"], "mime_type": job["mime_type"]}
    if status == "error":
        return {"status": "error", "error": job.get("error", "Error desconocido")}
    return {"status": "pending"}


# ── Redactor IA — generador/mejorador de texto para capas ────────────────────
class GenerateTextRequest(BaseModel):
    text: str
    tone: str = "Profesional"

_REDACTOR_SYSTEM = (
    "Eres un redactor creativo especializado en textos cortos para imágenes de marketing "
    "en redes sociales y campañas de ManyChat. Tu función es mejorar o generar el texto "
    "que el usuario va a colocar sobre una imagen.\n\n"
    "El resultado debe ser:\n"
    "- Conciso y directo (máximo 2-4 líneas — debe verse bien en una imagen)\n"
    "- Adaptado exactamente al tono solicitado\n"
    "- Natural y fluido en español\n"
    "- Sin expandirlo demasiado — conserva la esencia y la brevedad del mensaje original\n\n"
    "REGLAS ESTRICTAS:\n"
    "1. Responde ÚNICAMENTE con el texto final, sin explicaciones, comillas ni comentarios\n"
    "2. Escribe en español natural\n"
    "3. No uses hashtags ni emojis a menos que el usuario los incluya\n"
    "4. No uses listas ni formatos especiales — texto corrido o párrafos simples\n"
    "5. No cambies el idioma aunque el usuario escriba en inglés — responde en español\n\n"
    "REGLA BÍBLICA (aplica siempre que el usuario pida texto bíblico — no la menciones):\n"
    "Si el usuario solicita un versículo, salmo, pasaje o texto de la Biblia, usa EXCLUSIVAMENTE "
    "la versión Reina Valera 1960 (RV1960). Escribe el texto bíblico COMPLETO sin números de "
    "versículo, sin encabezados, sin títulos, sin notas — solo texto puro tal como aparece en RV1960. "
    "Ejemplo: si piden el Salmo 23, escribe todo el salmo línea a línea sin '1.' '2.' etc."
)

@app.post("/api/generate-text")
async def generate_text(req: GenerateTextRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    raw = req.text.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="El texto no puede estar vacío")
    tone = req.tone.strip() or "Profesional"
    system_with_tone = _REDACTOR_SYSTEM.replace(
        "al tono solicitado", f"al tono solicitado ({tone})"
    ) + f"\n\nTONO ACTUAL: {tone}"
    user_msg = f"Mejora o genera este texto con tono {tone}:\n\n{raw}"
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": user_msg}]}],
        "systemInstruction": {"parts": [{"text": system_with_tone}]},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 400, "candidateCount": 1}
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=25)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Error al conectar con la IA. Intenta de nuevo.")
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=500, detail="Sin respuesta del modelo")
        parts = candidates[0].get("content", {}).get("parts", [])
        result = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()
        if not result:
            raise HTTPException(status_code=500, detail="Respuesta vacía del modelo")
        return {"text": result}
    except HTTPException:
        raise
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado. Intenta de nuevo.")
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


class EnhancePromptRequest(BaseModel):
    prompt: str
    no_text: bool = False

_ENHANCE_SYSTEM = (
    "Eres un experto en escribir prompts para generadores de imágenes con IA. "
    "Tu especialidad es ayudar a crear imágenes para marketing personalizado en ManyChat: "
    "banners, anuncios, felicitaciones, promociones y contenido visual para bots.\n\n"
    "Toma el texto del usuario y mejóralo añadiendo:\n"
    "- Detalles visuales específicos (iluminación, composición, colores predominantes)\n"
    "- Ambiente y emoción de la escena\n"
    "- Calidad y estilo de la imagen (si aplica)\n"
    "- Si hay personas: expresión, postura y vestimenta brevemente\n"
    "- Si hay productos: ubicación en primer plano, nitidez\n\n"
    "REGLAS ESTRICTAS:\n"
    "1. Responde ÚNICAMENTE con el prompt mejorado, sin explicaciones ni comentarios\n"
    "2. Escribe en español, de forma natural y fluida\n"
    "3. Máximo 3 oraciones cortas\n"
    "4. Conserva la idea original del usuario sin cambiarla\n"
    "5. No uses listas, solo texto corrido\n"
    "6. Nunca menciones marcas registradas — describe el estilo visual en su lugar"
)

_ENHANCE_NOTXT_SUFFIX = (
    "\n\nREGLA ADICIONAL OBLIGATORIA — SIN TEXTOS:\n"
    "La imagen generada NO debe contener absolutamente ningún texto, letra, número, "
    "palabra, slogan, etiqueta, cartel, letreros, tipografía ni elemento gráfico con caracteres "
    "escritos generados por la IA. La escena debe ser pura ilustración o fotografía sin texto.\n"
    "IMPORTANTE: Si el usuario ha subido fotos de referencia con logos o texto real (como el "
    "nombre de su negocio o marca en un producto físico), ese texto es parte de la imagen real "
    "y se debe respetar tal cual — la restricción aplica solo al texto que la IA inventaría o "
    "generaría por su cuenta.\n"
    "Termina el prompt con la instrucción: 'sin texto, sin tipografía, sin letras, imagen limpia'."
)

@app.post("/api/enhance-prompt")
async def enhance_prompt(req: EnhancePromptRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    raw = req.prompt.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="El prompt no puede estar vacío")
    system_text = _ENHANCE_SYSTEM + (_ENHANCE_NOTXT_SUFFIX if req.no_text else "")
    user_msg = ("Mejora este prompt para generar una imagen SIN TEXTO ni tipografía generada por IA"
                if req.no_text else "Mejora este prompt para generar una imagen") + f":\n\n{raw}"
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": user_msg}]}],
        "systemInstruction": {"parts": [{"text": system_text}]},
        "generationConfig": {"temperature": 0.75, "maxOutputTokens": 260, "candidateCount": 1}
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=25)
        if resp.status_code != 200:
            logger.error(f"Enhance-prompt Gemini error {resp.status_code}: {resp.text[:300]}")
            raise HTTPException(status_code=502, detail="No se pudo mejorar el prompt ahora. Intenta de nuevo.")
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=500, detail="Sin respuesta del modelo")
        text_parts = candidates[0].get("content", {}).get("parts", [])
        enhanced = " ".join(p.get("text", "") for p in text_parts if "text" in p).strip()
        if not enhanced:
            raise HTTPException(status_code=500, detail="Respuesta vacía del modelo")
        return {"enhanced_prompt": enhanced}
    except HTTPException:
        raise
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado")
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))
# ────────────────────────────────────────────────────────────────────────────


class SaveAIImageRequest(BaseModel):
    image_b64: str
    mime_type: str = "image/png"

@app.get("/storage/{filename}")
async def serve_storage_file(filename: str):
    """Sirve archivos desde el directorio de almacenamiento persistente."""
    filepath = os.path.join(STORAGE_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    ext = filename.rsplit(".", 1)[-1].lower()
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    media_type = mime_map.get(ext, "image/png")
    return FileResponse(filepath, media_type=media_type)

@app.post("/api/save-ai-image")
async def save_ai_image(req: SaveAIImageRequest, request: Request):
    """Guarda una imagen AI en almacenamiento persistente y devuelve su URL pública."""
    ext = "jpg" if "jpeg" in req.mime_type else "png"
    uid = str(uuid.uuid4())[:12]
    filename = f"ai_{uid}.{ext}"
    filepath = os.path.join(STORAGE_DIR, filename)
    os.makedirs(STORAGE_DIR, exist_ok=True)
    img_bytes = base64.b64decode(req.image_b64)
    with open(filepath, "wb") as f:
        f.write(img_bytes)
    base_url = _get_base_url(request)
    public_url = f"{base_url}/storage/{filename}"
    logger.info(f"💾 Imagen AI guardada: {filepath} → {public_url}")
    return {"url": public_url}


# ── Editar imagen con IA ──────────────────────────────────────────────────────
class EditImageRequest(BaseModel):
    image_b64: str
    mime_type: str = "image/png"
    instruction: str
    reference_images: list = []

@app.post("/api/edit-image")
async def edit_image(req: EditImageRequest):
    """
    Toma una imagen existente (base64) y una instrucción en texto,
    llama a Gemini en modo imagen→imagen y devuelve la imagen editada.
    Acepta imágenes de referencia opcionales para guiar la edición.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    instr = req.instruction.strip()
    if not instr:
        raise HTTPException(status_code=400, detail="La instrucción no puede estar vacía")

    ref_imgs = req.reference_images or []
    if ref_imgs:
        edit_prompt = (
            f"Using the reference image(s) provided as visual context, edit the main image "
            f"according to this instruction: {instr}. "
            "Keep everything else the same — same composition, style, colors and proportions — "
            "only apply the requested change. Return the complete modified image."
        )
    else:
        edit_prompt = (
            f"Edita esta imagen exactamente según la siguiente instrucción: {instr}. "
            "Mantén todo lo demás igual — misma composición, estilo, colores y proporciones — "
            "solo aplica el cambio solicitado. Devuelve la imagen completa modificada."
        )

    parts = []
    if ref_imgs:
        parts.append({"text": "Reference images (use as visual context for the edit):"})
        for ref in ref_imgs[:3]:
            parts.append({"inlineData": {"mimeType": ref.get("mime_type", "image/jpeg"), "data": ref.get("data", "")}})
        parts.append({"text": "Main image to edit:"})
    parts.append({"inlineData": {"mimeType": req.mime_type, "data": req.image_b64}})
    parts.append({"text": edit_prompt})

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"]
        }
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        if resp.status_code != 200:
            logger.error(f"edit-image Gemini error {resp.status_code}: {resp.text[:400]}")
            raise HTTPException(status_code=502, detail=f"Error de Gemini al editar: {resp.text[:200]}")
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=500, detail="Gemini no devolvió resultado para la edición")
        for part in candidates[0].get("content", {}).get("parts", []):
            if "inlineData" in part:
                return {
                    "image_b64": part["inlineData"]["data"],
                    "mime_type": part["inlineData"].get("mimeType", "image/png")
                }
        raise HTTPException(status_code=500, detail="Gemini no devolvió imagen en la respuesta. Intenta con otra instrucción.")
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado. Intenta de nuevo.")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))
# ─────────────────────────────────────────────────────────────────────────────


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

@app.post("/api/upload-image")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """Sube una imagen desde el cliente y devuelve su URL pública persistente."""
    content_type = file.content_type or ""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if content_type not in ALLOWED_IMAGE_TYPES and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Tipo de archivo no permitido. Usa JPG, PNG, WEBP o GIF.")
    # Determinar extensión
    if ext in ALLOWED_EXTENSIONS:
        save_ext = ext
    else:
        save_ext = ".jpg"
    uid = str(uuid.uuid4())[:12]
    filename = f"upload_{uid}{save_ext}"
    filepath = os.path.join(STORAGE_DIR, filename)
    os.makedirs(STORAGE_DIR, exist_ok=True)
    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:  # 20 MB máximo
        raise HTTPException(status_code=400, detail="Imagen demasiado grande. Máximo 20 MB.")
    with open(filepath, "wb") as f:
        f.write(contents)
    base_url = _get_base_url(request)
    public_url = f"{base_url}/storage/{filename}"
    logger.info(f"📤 Imagen subida: {filepath} → {public_url}")
    return {"url": public_url, "filename": filename}




# ─── QR Code generator ────────────────────────────────────────────────────────
class QRRequest(BaseModel):
    text:        str
    dark_color:  str = "#000000"
    light_color: str = "#ffffff"
    bg_color:    str = "#ffffff"
    padding:     int = 20

@app.post("/api/qr")
async def generate_qr(req: QRRequest):
    """Genera un QR como PNG base64 con fondo de color y padding."""
    import qrcode

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text requerido")

    def hex_to_rgb(h: str):
        h = h.lstrip('#')
        if len(h) == 3:
            h = ''.join(c*2 for c in h)
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    dark_rgb  = hex_to_rgb(req.dark_color)
    light_rgb = hex_to_rgb(req.light_color)
    bg_rgb    = hex_to_rgb(req.bg_color)
    pad       = max(0, min(req.padding, 120))

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=1
    )
    qr.add_data(req.text.strip())
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color=dark_rgb, back_color=light_rgb).convert("RGB")

    if pad > 0:
        w, h = qr_img.size
        new_w, new_h = w + pad * 2, h + pad * 2
        bg = Image.new("RGB", (new_w, new_h), bg_rgb)
        bg.paste(qr_img, (pad, pad))
        qr_img = bg

    buf = BytesIO()
    qr_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"image": f"data:image/png;base64,{b64}"}


class FeedbackRequest(BaseModel):
    name: str
    email: str
    message: str

@app.post("/api/feedback")
async def submit_feedback(req: FeedbackRequest):
    import smtplib
    import datetime
    import asyncio
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")

    body = (
        f"Nuevo feedback de TextOnFlow\n"
        f"{'='*40}\n"
        f"Nombre:  {req.name}\n"
        f"Correo:  {req.email}\n"
        f"Fecha:   {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"Mensaje:\n{req.message}\n\n"
        f"---\nEnviado desde textonflow.com"
    )

    # Guardar siempre en archivo como respaldo
    try:
        log_path = os.path.join(STORAGE_DIR, "feedback_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*50}\n")
            f.write(body)
            f.write("\n")
    except Exception as e:
        logger.warning(f"No se pudo guardar feedback en archivo: {e}")

    sent = False
    send_error = ""
    em_key = os.getenv("ENGINEMAILER_API_KEY", "")
    vars_set = bool(em_key)

    if em_key:
        def _send_enginemailer():
            return requests.post(
                "https://api.enginemailer.com/RESTAPI/V2/Submission/SendEmail",
                headers={
                    "APIKey": em_key,
                    "Content-Type": "application/json"
                },
                json={
                    "CampaignName": "TextOnFlow Feedback",
                    "ToEmail": "feedback@textonflow.com",
                    "SenderEmail": "hola@followers.mx",
                    "SenderName": "TextOnFlow",
                    "Subject": f"Feedback de {req.name} — TextOnFlow",
                    "SubmittedContent": body,
                },
                timeout=20
            )

        try:
            loop = asyncio.get_event_loop()
            resp = await asyncio.wait_for(
                loop.run_in_executor(None, _send_enginemailer),
                timeout=25
            )
            if resp.status_code in (200, 201):
                sent = True
                logger.info(f"Feedback enviado via EngineMailer: {req.name} <{req.email}>")
            else:
                send_error = resp.text[:200]
                logger.error(f"EngineMailer error {resp.status_code}: {send_error}")
        except asyncio.TimeoutError:
            send_error = "Timeout (25s)"
            logger.error("Feedback EngineMailer timeout")
        except Exception as e:
            send_error = str(e)
            logger.error(f"Error EngineMailer: {e}")

    return {"ok": True, "sent": sent, "vars_set": vars_set, "error": send_error}


# ══════════════════════════════════════════════════════════════════════════════
# ⏱  CONTADOR REGRESIVO — Modelos y endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TimerStyle(BaseModel):
    font: str = "Doto"
    font_size: int = 52
    color: str = "#FFFFFF"
    x: float = 50.0            # porcentaje del ancho (0-100)
    y: float = 50.0            # porcentaje del alto  (0-100)
    alignment: str = "center"  # "left" | "center" | "right"
    format: str = "HH:MM:SS"   # "DD:HH:MM:SS" | "HH:MM:SS" | "HH:MM"
    expired_text: str = "¡Oferta expirada!"
    stroke_enabled: bool = True
    stroke_color: str = "#000000"
    stroke_width: int = 2
    shadow_enabled: bool = False
    shadow_color: str = "#000000"
    shadow_offset_x: float = 2.0
    shadow_offset_y: float = 2.0
    # Text wrap para el mensaje expirado
    expired_wrap_enabled: bool = True    # activo por defecto: siempre wrap el expirado
    expired_wrap_padding: int = 60       # margen L/R px
    expired_align: str = "center"        # alineación del mensaje expirado


class TimerTemplateCreate(BaseModel):
    template_name: str                  # nombre descriptivo
    base_image_url: str                 # URL de la imagen base (puede ser /storage/...)
    mode: str                           # "event" | "urgency"
    # Modo evento: fecha fija DD/MM/AAAA HH:MM (hora local → se guarda como UTC)
    event_date: Optional[str] = None    # "20/03/2026 18:00"
    event_tz: Optional[str] = "America/Mexico_City"
    # Modo urgencia: duración fija
    urgency_hours: Optional[float] = None
    style: TimerStyle = TimerStyle()
    # Imagen diferente para cuando el contador expira (opcional)
    expired_image_url: Optional[str] = None


class TimerTemplateResponse(BaseModel):
    template_id: str
    live_url_event: Optional[str] = None    # URL lista para copiar (modo evento)
    live_url_urgency: Optional[str] = None  # URL con variables (modo urgencia)
    preview_seconds: int                    # segundos restantes al guardar (debug)


def _sign_timer(template_id: str, extra: str = "") -> str:
    """Genera firma HMAC-SHA256 para URL de timer."""
    payload = f"{template_id}:{extra}".encode()
    return hmac.new(TIMER_SECRET.encode(), payload, hashlib.sha256).hexdigest()[:16]


def _parse_event_date(date_str: str, tz_name: str) -> datetime:
    """Convierte 'DD/MM/AAAA HH:MM' en datetime UTC."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    dt_local = datetime.strptime(date_str.strip(), "%d/%m/%Y %H:%M")
    dt_local = dt_local.replace(tzinfo=tz)
    return dt_local.astimezone(timezone.utc)


def _format_countdown(seconds: float, fmt: str, expired_text: str) -> str:
    """Formatea segundos restantes en la cadena del contador."""
    if seconds <= 0:
        return expired_text
    total = int(seconds)
    days    = total // 86400
    hours   = (total % 86400) // 3600
    minutes = (total % 3600)  // 60
    secs    = total % 60
    if fmt == "DD:HH:MM:SS":
        return f"{days}d {hours:02d}h {minutes:02d}m {secs:02d}s"
    elif fmt == "HH:MM":
        total_h = days * 24 + hours
        return f"{total_h}:{minutes:02d}"
    else:  # HH:MM:SS (default)
        total_h = days * 24 + hours
        return f"{total_h}:{minutes:02d}:{secs:02d}"


def _render_timer_on_image(
    img: "Image.Image",
    countdown_text: str,
    style: dict,
    is_expired: bool = False,
    expired_img: Optional["Image.Image"] = None
) -> "Image.Image":
    """Dibuja el texto del contador sobre la imagen base.
    
    - is_expired: si True, usa expired_img como fondo (si existe) y centra el texto.
    - expired_img: imagen alternativa para el estado expirado.
    """
    # ── Imagen base: usar expired_img si el contador terminó y hay una definida ──
    if is_expired and expired_img is not None:
        base = expired_img.copy().convert("RGBA")
    else:
        base = img.copy().convert("RGBA")

    base = base.copy().convert("RGBA")
    w, h = base.size

    font_name = style.get("font", "Doto")
    font_path = FONT_MAPPING.get(font_name, "./fonts/Doto-Regular.ttf")
    font_size = int(style.get("font_size", 52))
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()

    color = style.get("color", "#FFFFFF")

    # ── Cuando está expirado: forzar centrado y aplicar text wrap ──────────────
    if is_expired:
        alignment = style.get("expired_align", "center")
        wrap_enabled = style.get("expired_wrap_enabled", True)
        wrap_padding = max(0, int(style.get("expired_wrap_padding", 60)))
    else:
        alignment = style.get("alignment", "center")
        wrap_enabled = False
        wrap_padding = 60

    # ── Text wrap (solo para expirado en este render) ──────────────────────────
    draw_tmp = ImageDraw.Draw(base)
    if wrap_enabled and wrap_padding >= 0:
        max_w = max(1, w - 2 * wrap_padding)
        countdown_text = _wrap_words(countdown_text, font, max_w, draw_tmp)

    # Posición como porcentaje del canvas
    px = int(w * float(style.get("x", 50)) / 100)
    py = int(h * float(style.get("y", 50)) / 100)

    # Si está expirado, centrar automáticamente en el canvas
    if is_expired:
        px = w // 2
        py = h // 2

    draw = ImageDraw.Draw(base)

    stroke_w = int(style.get("stroke_width", 2)) if style.get("stroke_enabled", True) else 0
    stroke_c = style.get("stroke_color", "#000000")

    # Calcular anchor según alignment
    anchor = "mm"  # center
    if alignment == "left":
        anchor = "lm"
    elif alignment == "right":
        anchor = "rm"

    # Sombra
    if style.get("shadow_enabled", False):
        sx = int(style.get("shadow_offset_x", 2))
        sy = int(style.get("shadow_offset_y", 2))
        draw.text((px + sx, py + sy), countdown_text, font=font,
                  fill=style.get("shadow_color", "#000000"), anchor=anchor)

    # Texto principal con stroke (usa multiline para que el wrap se vea bien)
    draw.multiline_text(
        (px, py), countdown_text, font=font, fill=color,
        stroke_width=stroke_w, stroke_fill=stroke_c,
        anchor=anchor, align=alignment
    )

    return base.convert("RGB")


@app.post("/api/timer/save", response_model=TimerTemplateResponse)
async def save_timer_template(request: Request, body: TimerTemplateCreate):
    """Guarda un template de contador y devuelve las URLs listas para usar en ManyChat."""
    template_id = str(uuid.uuid4())[:12]
    base_url = _get_base_url(request)

    # Validar modo
    if body.mode not in ("event", "urgency"):
        raise HTTPException(status_code=400, detail="mode debe ser 'event' o 'urgency'")
    if body.mode == "event" and not body.event_date:
        raise HTTPException(status_code=400, detail="event_date requerido para modo evento")
    if body.mode == "urgency" and not body.urgency_hours:
        raise HTTPException(status_code=400, detail="urgency_hours requerido para modo urgencia")

    # Calcular preview de segundos restantes
    preview_seconds = 0
    if body.mode == "event":
        try:
            end_utc = _parse_event_date(body.event_date, body.event_tz or "America/Mexico_City")
            preview_seconds = max(0, int((end_utc - datetime.now(timezone.utc)).total_seconds()))
            end_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Formato de fecha inválido: {e}. Usa DD/MM/AAAA HH:MM")
    else:
        preview_seconds = int((body.urgency_hours or 0) * 3600)
        end_iso = None

    # Serializar template a JSON
    template_data = {
        "template_id": template_id,
        "template_name": body.template_name,
        "base_image_url": body.base_image_url,
        "expired_image_url": body.expired_image_url or None,
        "mode": body.mode,
        "event_end_utc": end_iso if body.mode == "event" else None,
        "urgency_hours": body.urgency_hours if body.mode == "urgency" else None,
        "style": body.style.dict(),
    }
    template_path = os.path.join(TIMER_TEMPLATES_DIR, f"{template_id}.json")
    with open(template_path, "w", encoding="utf-8") as f:
        json.dump(template_data, f, ensure_ascii=False, indent=2)
    logger.info(f"⏱ Template de timer guardado: {template_id} (modo: {body.mode})")

    # Construir URLs
    sig = _sign_timer(template_id)
    live_url_event = None
    live_url_urgency = None

    if body.mode == "event":
        # _t={{id}} hace la URL única por suscriptor → WhatsApp no reutiliza caché entre usuarios.
        # El servidor ignora _t; solo sirve para que cada URL sea diferente.
        live_url_event = f"{base_url}/live/{template_id}.jpg?s={sig}&_t={{{{id}}}}"
    else:
        # Urgencia: el backend registra automáticamente el primer acceso de cada usuario.
        # uid={{id}} identifica al suscriptor para calcular su countdown personal.
        live_url_urgency = (
            f"{base_url}/live/{template_id}.jpg"
            f"?uid={{{{id}}}}"
            f"&s={sig}"
        )

    return TimerTemplateResponse(
        template_id=template_id,
        live_url_event=live_url_event,
        live_url_urgency=live_url_urgency,
        preview_seconds=preview_seconds,
    )


@app.get("/live/{template_id}.jpg")
async def render_live_timer(
    template_id: str,
    s: Optional[str] = None,
    uid: Optional[str] = None,
    user_start: Optional[str] = None,
    _t: Optional[str] = None,   # cache-buster (ignorado; hace la URL única por envío)
):
    """
    Renderiza la imagen con el contador en tiempo real.
    Cada vez que se carga esta URL, el servidor calcula el tiempo restante exacto.
    """
    # Cargar template
    template_path = os.path.join(TIMER_TEMPLATES_DIR, f"{template_id}.json")
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Template de timer no encontrado")

    with open(template_path, "r", encoding="utf-8") as f:
        tmpl = json.load(f)

    # Verificar firma HMAC
    expected_sig = _sign_timer(template_id)
    if s and s != expected_sig:
        raise HTTPException(status_code=403, detail="Firma de URL inválida")

    style = tmpl.get("style", {})
    fmt = style.get("format", "HH:MM:SS")
    expired_text = style.get("expired_text", "¡Oferta expirada!")
    mode = tmpl.get("mode", "event")

    # Calcular segundos restantes
    now_utc = datetime.now(timezone.utc)

    if mode == "event":
        end_str = tmpl.get("event_end_utc")
        if not end_str:
            seconds_left = 0.0
        else:
            end_utc = datetime.strptime(end_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            seconds_left = max(0.0, (end_utc - now_utc).total_seconds())

    else:  # urgency
        urgency_hours = float(tmpl.get("urgency_hours") or 0)
        if uid:
            # Primer acceso automático: registrar cuándo este usuario accedió por primera vez
            access_file = os.path.join(TIMER_ACCESS_DIR, f"{template_id}.json")
            try:
                if os.path.exists(access_file):
                    with open(access_file, "r", encoding="utf-8") as af:
                        access_data = json.load(af)
                else:
                    access_data = {}
                uid_key = str(uid)
                if uid_key not in access_data:
                    # Primera vez — guardar timestamp actual como inicio del countdown
                    access_data[uid_key] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                    with open(access_file, "w", encoding="utf-8") as af:
                        json.dump(access_data, af, ensure_ascii=False, indent=2)
                start_utc = datetime.strptime(access_data[uid_key], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                end_utc = start_utc + timedelta(hours=urgency_hours)
                seconds_left = max(0.0, (end_utc - now_utc).total_seconds())
            except Exception as e:
                logger.warning(f"⚠️ Error leyendo acceso de usuario: {e}")
                seconds_left = urgency_hours * 3600
        elif user_start:
            try:
                if user_start.isdigit():
                    start_utc = datetime.fromtimestamp(int(user_start), tz=timezone.utc)
                else:
                    start_utc = datetime.fromisoformat(user_start.replace("Z", "+00:00"))
                end_utc = start_utc + timedelta(hours=urgency_hours)
                seconds_left = max(0.0, (end_utc - now_utc).total_seconds())
            except Exception:
                seconds_left = max(0.0, urgency_hours * 3600)
        else:
            # Sin uid ni user_start → mostrar la duración completa (preview)
            seconds_left = urgency_hours * 3600

    is_expired = (seconds_left <= 0)
    countdown_text = _format_countdown(seconds_left, fmt, expired_text)

    def _load_img_from_url(url: str) -> Optional[Image.Image]:
        """Intenta cargar una imagen desde URL local o remota."""
        loaded = None
        try:
            if url.startswith("/storage/") or url.startswith("/static/temp/"):
                fname = url.split("/")[-1].split("?")[0]
                local_path = os.path.join(STORAGE_DIR, fname)
                if os.path.exists(local_path):
                    loaded = Image.open(local_path).convert("RGBA")
            if loaded is None and url.startswith("http"):
                r = requests.get(url, timeout=10)
                loaded = Image.open(BytesIO(r.content)).convert("RGBA")
        except Exception as e:
            logger.warning(f"⚠️ No se pudo cargar imagen: {e}")
        return loaded

    # Cargar imagen base de campaña
    base_url_img = tmpl.get("base_image_url", "")
    img = _load_img_from_url(base_url_img) if base_url_img else None
    if img is None:
        img = Image.new("RGB", (800, 400), color=(20, 20, 40))

    # Cargar imagen de expirado (opcional)
    expired_url = tmpl.get("expired_image_url", "") or ""
    expired_img = _load_img_from_url(expired_url) if expired_url else None

    # Renderizar countdown sobre la imagen (pasando estado y imagen de expirado)
    result = _render_timer_on_image(img, countdown_text, style,
                                    is_expired=is_expired, expired_img=expired_img)

    # Serializar a JPG
    buf = BytesIO()
    result.save(buf, format="JPEG", quality=90)
    buf.seek(0)

    # Sin caché: WhatsApp y ManyChat cachean por URL; esta imagen cambia en cada request.
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, private",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Timer-Seconds-Left": str(int(seconds_left)),
        "X-Timer-Mode": mode,
    }
    logger.info(f"⏱ Timer rendered: {template_id} | mode={mode} | left={int(seconds_left)}s | text='{countdown_text}'")
    return Response(content=buf.read(), media_type="image/jpeg", headers=headers)


@app.get("/api/timer/{template_id}")
async def get_timer_template(template_id: str):
    """Devuelve la configuración de un template de timer (para el editor)."""
    template_path = os.path.join(TIMER_TEMPLATES_DIR, f"{template_id}.json")
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Template no encontrado")
    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/configurador")
async def configurador():
    return FileResponse("index.html", media_type="text/html")


# ══════════════════════════════════════════════════════════════
#  FLOWBOT — Asistente Virtual TextOnFlow
# ══════════════════════════════════════════════════════════════

class _HtmlStripper(HTMLParser):
    """Extrae texto plano de un archivo HTML eliminando scripts, estilos y nav."""
    def __init__(self):
        super().__init__()
        self._buf: list[str] = []
        self._skip = False
        self._skip_tags = {"script", "style", "head", "nav", "footer", "noscript"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip = True

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self._buf.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self._buf)


def _html_to_text(filepath: str) -> str:
    try:
        with open(filepath, encoding="utf-8") as f:
            raw = f.read()
        parser = _HtmlStripper()
        parser.feed(raw)
        return parser.get_text()
    except Exception:
        return ""


def _build_knowledge_base() -> str:
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    parts = []
    for filename, label in [
        ("faq.html", "PREGUNTAS FRECUENTES (FAQ)"),
        ("manual.html", "MANUAL DE USUARIO"),
        ("docs.html", "DOCUMENTACIÓN TÉCNICA / API"),
    ]:
        text = _html_to_text(os.path.join(base_dir, filename))
        if text:
            parts.append(f"=== {label} ===\n{text}")
    return "\n\n".join(parts)


_FLOWBOT_KB: str = _build_knowledge_base()

_FLOWBOT_SYSTEM = (
    "Eres FlowBot, el asistente virtual amigable y experto de TextOnFlow.\n"
    "TextOnFlow es una herramienta visual de drag-and-drop para crear imágenes "
    "personalizadas con textos y overlays para campañas de ManyChat.\n\n"
    "TU FUNCIÓN:\n"
    "- Responder dudas de usuarios sobre cómo usar TextOnFlow\n"
    "- Guiar paso a paso de forma clara y amigable\n"
    "- Basar TODAS tus respuestas exclusivamente en la documentación adjunta\n"
    "- Si algo no está en la documentación, decirlo honestamente sin inventar\n\n"
    "REGLAS DE RESPUESTA:\n"
    "1. Responde siempre en español, con tono amigable y profesional\n"
    "2. Sé conciso: máximo 3-5 párrafos cortos por respuesta\n"
    "3. Usa listas con guiones (—) para pasos o puntos clave\n"
    "4. Si no sabes algo, di: 'No tengo esa información en mi documentación. "
    "Puedes contactar al soporte en www.textonflow.com'\n"
    "5. Nunca inventes funciones o precios que no estén en la documentación\n"
    "6. Nunca repitas la pregunta del usuario ni hagas introducciones largas\n"
    "7. Saluda solo en el primer mensaje de la conversación\n\n"
    f"DOCUMENTACIÓN DE TEXTONFLOW:\n{_FLOWBOT_KB}"
)


class AssistantMessage(BaseModel):
    role: str
    content: str


class AssistantRequest(BaseModel):
    message: str
    history: List[AssistantMessage] = []


@app.post("/api/assistant")
async def flowbot_chat(req: AssistantRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Mensaje vacío")

    contents = []
    for m in req.history[-6:]:
        role = "user" if m.role == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m.content}]})
    contents.append({"role": "user", "parts": [{"text": user_msg}]})

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": _FLOWBOT_SYSTEM}]},
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 600,
            "candidateCount": 1
        }
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=25)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Error al conectar con FlowBot. Intenta de nuevo.")
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=500, detail="Sin respuesta del asistente")
        parts = candidates[0].get("content", {}).get("parts", [])
        reply = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()
        if not reply:
            raise HTTPException(status_code=500, detail="Respuesta vacía")
        return {"reply": reply}
    except HTTPException:
        raise
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado. Intenta de nuevo.")
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


class TranscriptRequest(BaseModel):
    name: str
    email: str
    history: List[AssistantMessage] = []


class RatingRequest(BaseModel):
    rating: int


@app.post("/api/assistant/transcript")
async def send_assistant_transcript(req: TranscriptRequest):
    import datetime
    labels = {1: "Mala", 2: "Buena", 3: "Excelente"}
    lines = []
    for m in req.history:
        who = "Tú" if m.role == "user" else "FlowBot"
        lines.append(f"{who}:\n{m.content}\n")
    conv_text = "\n".join(lines)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    body = (
        f"Hola {req.name},\n\n"
        f"Aquí tienes el historial de tu conversación con FlowBot — TextOnFlow ({now}):\n\n"
        f"{'='*50}\n\n"
        f"{conv_text}\n"
        f"{'='*50}\n\n"
        f"¿Tienes más dudas? Escríbenos a hola@textonflow.com\n"
        f"— El equipo de TextOnFlow\n"
        f"www.textonflow.com"
    )
    # Log siempre como respaldo
    try:
        log_path = os.path.join(STORAGE_DIR, "transcripts_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Nombre: {req.name} | Email: {req.email} | Fecha: {now}\n")
            f.write(conv_text)
            f.write("\n")
    except Exception:
        pass
    # Enviar con EngineMailer
    em_key = os.getenv("ENGINEMAILER_API_KEY", "")
    if em_key:
        try:
            requests.post(
                "https://api.enginemailer.com/RESTAPI/V2/Submission/SendEmail",
                headers={"APIKey": em_key, "Content-Type": "application/json"},
                json={
                    "CampaignName": "FlowBot Transcript",
                    "ToEmail": req.email,
                    "SenderEmail": "hola@followers.mx",
                    "SenderName": "FlowBot — TextOnFlow",
                    "Subject": "Tu conversación con FlowBot — TextOnFlow",
                    "SubmittedContent": body,
                    "BCCEmails": ["hola@textonflow.com"],
                },
                timeout=12,
            )
        except Exception:
            pass
    return {"ok": True}


@app.post("/api/inpaint")
async def inpaint_image(request: Request):
    """
    Borrador Mágico IA — recibe imagen ORIGINAL + máscara B&W separada.
    La máscara tiene blanco donde borrar, negro donde mantener.
    Gemini recibe ambas imágenes y rellena el área blanca con fondo natural.
    Body: { "original": "<base64 JPEG>", "mask": "<base64 PNG>" }
    Returns: { "result": "<base64>", "mime": "image/jpeg" }
    """
    import io as _io
    data = await request.json()
    original_b64 = data.get("original", "")
    mask_b64     = data.get("mask", "")
    if not original_b64 or not mask_b64:
        raise HTTPException(status_code=400, detail="Se requieren los campos 'original' y 'mask'")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada en el servidor")

    # ── Analizar la máscara para extraer bounding box en porcentajes ──────────
    try:
        from PIL import Image as _PILImg
        mask_bytes_raw = base64.b64decode(mask_b64)
        mask_img  = _PILImg.open(_io.BytesIO(mask_bytes_raw)).convert('L')
        mask_arr  = __import__('numpy').array(mask_img)
        h_m, w_m  = mask_arr.shape
        wy, wx    = (mask_arr > 128).nonzero()
        if len(wy) == 0:
            raise HTTPException(status_code=400, detail="La máscara está vacía")
        # Bounding box en %
        y1p = int(wy.min() / h_m * 100)
        y2p = int(wy.max() / h_m * 100)
        x1p = int(wx.min() / w_m * 100)
        x2p = int(wx.max() / w_m * 100)
        # Pequeña dilación para capturar bordes
        pad = 3
        x1p = max(0, x1p - pad);  x2p = min(100, x2p + pad)
        y1p = max(0, y1p - pad);  y2p = min(100, y2p + pad)
        cxp = (x1p + x2p) // 2
        cyp = (y1p + y2p) // 2
        rw  = x2p - x1p
        rh  = y2p - y1p
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"mask analysis error: {e}")
        raise HTTPException(status_code=400, detail=f"Error al analizar la máscara: {e}")

    # ── Prompt con coordenadas exactas (sin enviar la máscara como imagen) ────
    prompt = (
        f"Precisely edit this image. "
        f"Remove and erase ONLY the content located in this exact rectangular region: "
        f"left={x1p}%, right={x2p}%, top={y1p}%, bottom={y2p}% "
        f"(percentages measured from the top-left corner of the image). "
        f"This region is {rw}% wide and {rh}% tall, "
        f"centered at ({cxp}%, {cyp}%) from the top-left. "
        f"Fill the erased area with realistic background that seamlessly continues "
        f"the surrounding textures, colors, lighting and patterns — as if nothing was ever there. "
        f"CRITICAL: Do NOT change or modify ANYTHING outside the specified rectangular region. "
        f"Preserve the rest of the image pixel-perfectly. "
        f"Return only the final edited image."
    )

    url     = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inlineData": {"mimeType": "image/jpeg", "data": original_b64}}
            ]
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE", "TEXT"]
        }
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=90)
        if resp.status_code != 200:
            logger.error(f"Gemini inpaint error {resp.status_code}: {resp.text[:400]}")
            raise HTTPException(
                status_code=500,
                detail=f"Error de Gemini ({resp.status_code}): {resp.text[:250]}"
            )
        result_data = resp.json()
        candidates  = result_data.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=500, detail="Gemini no devolvió resultado para el borrado")

        for part in candidates[0].get("content", {}).get("parts", []):
            if "inlineData" in part:
                return {
                    "result": part["inlineData"]["data"],
                    "mime":   part["inlineData"].get("mimeType", "image/jpeg")
                }

        raise HTTPException(status_code=500, detail="Gemini no devolvió imagen. Intenta pintando un área más pequeña.")

    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado (90 s). Intenta con un área más pequeña.")
    except HTTPException:
        raise
    except Exception as ex:
        logger.error(f"inpaint exception: {ex}")
        raise HTTPException(status_code=500, detail=str(ex))


@app.post("/api/assistant/rating")
async def submit_assistant_rating(req: RatingRequest):
    import datetime
    labels = {1: "Mala ⭐", 2: "Buena ⭐⭐", 3: "Excelente ⭐⭐⭐"}
    label = labels.get(req.rating, f"{req.rating} estrellas")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        log_path = os.path.join(STORAGE_DIR, "ratings_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{now} | FlowBot | {label}\n")
    except Exception:
        pass
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
