from fastapi import FastAPI, HTTPException, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Dict, List, Optional
from pydantic import BaseModel
from models import (
    TextField,
    CanvasShape,
    ImageOverlay,
    MultiTextRequest,
    _AdminLoginBody,
    _AdminSettingsBody,
    _UserRegisterBody,
    _UserLoginBody,
    _UserUpdateBody,
    _WebhookBody,
    _ProjectCreate,
    _ProjectUpdate,
    _ForgotPasswordBody,
    _ResetPasswordBody,
    _SessionOpenBody,
    _SessionCloseBody,
    _AdminUserActionBody,
    _CheckoutBody,
    ApiTemplateRequest,
    WebhookRenderRequest,
    RefImage,
    GenerateImageRequest,
    GenerateTextRequest,
    EnhancePromptRequest,
    SaveAIImageRequest,
    EditImageRequest,
    QRRequest,
    FeedbackRequest,
    TimerStyle,
    TimerTemplateCreate,
    TimerTemplateResponse,
    AssistantMessage,
    AssistantRequest,
    TranscriptRequest,
    RatingRequest,
    DesignLayoutRequest,
    CopySuggestionsRequest,
    BrandKitRequest,
    ABVariantsRequest,
)
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


logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ─── Base de datos (importado de database.py) ────────────────────────────────
from database import (
    SUPABASE_DATABASE_URL, JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_HOURS,
    get_db, init_db,
)

# ─── Auth helpers (importado de auth.py) ─────────────────────────────────────
from auth import (
    _AUTH_OK, hash_password, verify_password, create_jwt, decode_jwt,
    _is_superadmin, _get_client_ip,
    _check_rate_limit, _check_minute_limit, _increment_ip_usage,
    PLAN_LIMITS, _ADMIN_SESSIONS, _ADMIN_LOCK, _SESSION_TTL,
    _SUPERADMIN_EMAIL, _SUPERADMIN_PWD_HASH,
)

# ─── Fuentes y sesion HTTP (importado de fonts.py) ──────────────────────────
from fonts import (
    FONT_MAPPING, FONT_SIZE_SCALE, NOTO_EMOJI_PATHS,
    get_noto_emoji_font, build_retry_session, RetryTwitterEmojiSource,
)

# ─── App FastAPI ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="TextOnFlow API",
    description=(
        "API pública de TextOnFlow — personalización dinámica de imágenes para ManyChat.\n\n"
        "**Autenticación:** Bearer JWT (`Authorization: Bearer <token>`) obtenido en `/api/auth/login`.\n\n"
        "**Uso rápido:** `POST /generate-multi` con el JSON exportado desde el editor."
    ),
    version="7.0.0",
    contact={"name": "TextOnFlow Support", "url": "https://textonflow.com", "email": "hola@textonflow.com"},
    license_info={"name": "Privativo — solo clientes TextOnFlow"},
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_tags=[
        {"name": "render",    "description": "Generación y renderizado de imágenes"},
        {"name": "projects",  "description": "Proyectos guardados del usuario"},
        {"name": "auth",      "description": "Autenticación y registro"},
        {"name": "user",      "description": "Perfil y uso del usuario"},
        {"name": "admin",     "description": "Panel de superadministrador"},
        {"name": "webhooks",  "description": "Webhooks de salida por usuario"},
    ],
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

def _reset_time_str() -> str:
    """Tiempo hasta medianoche UTC en formato 'Xh Ym'."""
    now      = datetime.utcnow()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    secs     = int((midnight - now).total_seconds())
    return f"{secs // 3600}h {(secs % 3600) // 60}m"

from utils import _get_base_url

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


# ─── Motor de renderizado (importado de renderer.py) ────────────────────────
from renderer import (
    INSTAGRAM_GRADIENT, NEGRO_GRADIENT, METALICO_GRADIENT,
    make_gradient_image, apply_gradient_bg, apply_gradient_stroke,
    apply_filter, apply_vignette,
    parse_color, parse_color_with_opacity,
    get_emoji_source, apply_blend_mode,
    _apply_overlay_mask, _apply_overlay_border,
    _render_canvas_shape, _auto_fit_overlay,
    _wrap_words, draw_text_with_effects,
    get_font_path, _format_countdown,
)

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

@app.get("/admin-panel", include_in_schema=False)
async def admin_panel_page():
    """Panel de administración con gestión visual de usuarios."""
    panel_path = os.path.join("static", "admin-panel.html")
    if os.path.exists(panel_path):
        return FileResponse(panel_path, media_type="text/html")
    raise HTTPException(status_code=404, detail="Panel no encontrado.")

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

# ─── User limits y auth helpers (importado de user_limits.py) ─────────────
from user_limits import (
    USER_PLAN_LIMITS, TRIAL_DAYS, JSON_EXPORT_PLANS,
    _get_current_user, _require_user,
    _get_user_profile, _should_apply_watermark,
    _check_user_render_limit, _increment_user_renders,
)

# ─── User routes (importado de routers/users.py) ────────────────────────────
from routers.users import users_router
app.include_router(users_router)

# ─── Admin routes + Stripe (importado de routers/admin.py) ──────────────────
from routers.admin import admin_router
app.include_router(admin_router)

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


# ─── Webhook de salida por usuario ───────────────────────────────────────────
def _fire_user_webhook(user_id: str, image_url: str, template: str) -> None:
    """Lanza un POST al webhook_url del usuario en segundo plano (no bloquea la respuesta)."""
    def _do():
        conn = get_db()
        if not conn:
            return
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT webhook_url FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
            if not row or not row["webhook_url"]:
                return
            url = row["webhook_url"]
            payload = {
                "event": "render.done",
                "image_url": image_url,
                "template": template,
                "ts": datetime.utcnow().isoformat() + "Z",
            }
            resp = requests.post(url, json=payload, timeout=8)
            logger.info(f"🔔 Webhook → {url} [{resp.status_code}]")
        except Exception as e:
            logger.warning(f"⚠️ Webhook error ({user_id}): {e}")
    threading.Thread(target=_do, daemon=True).start()


# ─── Cola de renderizado simplificada (T005) ──────────────────────────────────
import concurrent.futures as _futures
_RENDER_JOBS: dict = {}          # job_id → {status, result, error, created_at}
_RENDER_EXECUTOR = _futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="tof-render")

def _run_render_job(job_id: str, req_data: dict, auth_header: str) -> None:
    """Ejecuta el render en un hilo del pool y guarda el resultado en _RENDER_JOBS."""
    _RENDER_JOBS[job_id]["status"] = "processing"
    try:
        port = int(os.environ.get("PORT", 8000))
        hdrs = {"Content-Type": "application/json"}
        if auth_header:
            hdrs["Authorization"] = auth_header
        resp = requests.post(
            f"http://127.0.0.1:{port}/generate-multi",
            json=req_data, headers=hdrs, timeout=120
        )
        if resp.status_code == 200:
            _RENDER_JOBS[job_id].update({"status": "done", "result": resp.json()})
        else:
            _RENDER_JOBS[job_id].update({"status": "error", "error": resp.text[:500]})
        logger.info(f"✅ Job {job_id} → HTTP {resp.status_code}")
    except Exception as e:
        _RENDER_JOBS[job_id].update({"status": "error", "error": str(e)})
        logger.error(f"💥 Job {job_id} falló: {e}")


@app.post("/generate-multi")
async def generate_multi_text(request: MultiTextRequest, http_req: Request):
    # ── Rate limit: usuario autenticado (JWT) o IP (fallback) ────────────────
    _user_payload = _get_current_user(http_req)
    _user_id      = _user_payload["sub"] if _user_payload else None
    _ip           = _get_client_ip(http_req)

    _plan = "admin"
    if _is_superadmin(http_req):
        _used, _limit = 0, 999999
    elif _user_id:
        # Usuario autenticado → verificar límite de su plan
        _used, _limit, _exceeded, _plan = _check_user_render_limit(_user_id)
        if _exceeded:
            raise HTTPException(
                status_code=429,
                detail=f"Tu periodo de prueba de {TRIAL_DAYS} días ha expirado. Activa tu plan en textonflow.com/precios para seguir generando imágenes." if _plan == "trial" else f"Límite de renders alcanzado ({_used}/{_limit} · Plan {_plan.capitalize()}). Actualiza tu plan en textonflow.com/precios",
                headers={"X-RateLimit-Used": str(_used), "X-RateLimit-Limit": str(_limit), "X-Plan": _plan},
            )
        # ── Rate limit por minuto ───────────────────────────────────────────
        _min_ok, _min_used, _min_lim = _check_minute_limit(_user_id, _plan)
        if not _min_ok:
            raise HTTPException(
                status_code=429,
                detail=f"Demasiados renders por minuto ({_min_used}/{_min_lim} por min · Plan {_plan.capitalize()}). Espera unos segundos.",
                headers={"X-RateLimit-MinUsed": str(_min_used), "X-RateLimit-MinLimit": str(_min_lim)},
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
        # Rate limit por minuto para IPs anónimas (2/min)
        _min_ok, _min_used, _min_lim = _check_minute_limit(f"ip:{_ip}", "trial")
        if not _min_ok:
            raise HTTPException(status_code=429, detail="Demasiados renders por minuto. Crea una cuenta gratis para más velocidad.")
    try:
        # Cargar imagen (URL o local)
        # ── Prioridad 1: base64 enviada por el frontend (evita fetch externo) ──────
        if request.template_image_b64:
            try:
                import base64 as _b64
                _raw = _b64.b64decode(request.template_image_b64)
                image = Image.open(BytesIO(_raw)).convert("RGBA")
                logger.info(f"🟢 Imagen recibida en base64 ({len(_raw)//1024} KB) — sin fetch externo")
            except Exception as _b64err:
                logger.warning(f"⚠️ Error decodificando base64, intentando URL: {_b64err}")
                image = None
        else:
            image = None

        if image is None:
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
                    _img_headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                        "Referer": "https://manychat.com/",
                    }
                    response = session.get(request.template_name, timeout=15, headers=_img_headers)
                    logger.info(f"🔵 Respuesta imagen: {response.status_code} Content-Type={response.headers.get('Content-Type','?')}")
                    if response.status_code == 404:
                        raise HTTPException(status_code=400, detail="La imagen ya no está disponible en el servidor origen (404). Descarga la imagen y vuelve a subirla directamente al editor.")
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "")
                    if "text/" in content_type or "html" in content_type:
                        raise HTTPException(status_code=400, detail="La URL no apunta a una imagen válida (se recibió HTML). Descarga la imagen y súbela directamente al editor.")
                    try:
                        image = Image.open(BytesIO(response.content)).convert("RGBA")
                    except Exception as img_err:
                        raise HTTPException(status_code=400, detail=f"No se pudo leer la imagen: {img_err}. Verifica que la URL sea una imagen válida.")
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
        # Se aplica si: (a) el request lo pide, (b) plan trial sin exención admin
        _apply_wm = request.watermark or _should_apply_watermark(_user_id)
        if _apply_wm:
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

        # ── Webhook de salida (async, no bloquea la respuesta) ────────────────
        if _user_id:
            _fire_user_webhook(_user_id, image_url, request.template_name)

        return {"image_url": image_url, "usage": {"used": _used_after, "limit": _lim}}

    except requests.exceptions.RequestException as e:
        logger.error(f"💥 Error de red: {e}")
        raise HTTPException(status_code=400, detail=f"Error descargando imagen: {str(e)}")
    except Exception as e:
        logger.error(f"💥 Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ─── Render asíncrono con job_id (T005) ──────────────────────────────────────

@app.post("/render-async", tags=["render"], status_code=202,
          summary="Enviar render a la cola (respuesta inmediata)",
          response_description="job_id para consultar el estado luego")
async def render_async_endpoint(
    request: MultiTextRequest,
    http_req: Request,
    background_tasks: BackgroundTasks,
):
    """
    Encola el render y devuelve un **job_id** de inmediato (HTTP 202).  
    Usa `GET /render-jobs/{job_id}` para obtener el resultado cuando esté listo.  
    Útil para integraciones Make/Zapier donde el tiempo de respuesta es limitado.
    """
    job_id = str(uuid.uuid4())
    auth_header = http_req.headers.get("Authorization", "")
    _RENDER_JOBS[job_id] = {
        "status": "queued",
        "result": None,
        "error": None,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    # Limpia jobs viejos (> 1 h) para no acumular memoria
    _cutoff = time.time() - 3600
    for jid in list(_RENDER_JOBS.keys()):
        if jid != job_id:
            ts_str = _RENDER_JOBS[jid].get("created_at", "")
            try:
                from datetime import timezone as _tz
                ts_clean = ts_str.replace("Z", "+00:00")
                ts = datetime.fromisoformat(ts_clean).replace(tzinfo=_tz.utc).timestamp()
                if ts < _cutoff:
                    del _RENDER_JOBS[jid]
            except Exception:
                pass
    _RENDER_EXECUTOR.submit(_run_render_job, job_id, request.model_dump(), auth_header)
    return {"job_id": job_id, "status": "queued", "poll_url": f"/render-jobs/{job_id}"}


@app.get("/render-jobs/{job_id}", tags=["render"],
         summary="Consultar estado de un render asíncrono")
async def get_render_job(job_id: str, request: Request):
    """
    Devuelve el estado del job: `queued` → `processing` → `done` | `error`.  
    Cuando `status == "done"`, el campo `result` contiene `{image_url, usage}`.
    """
    job = _RENDER_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404,
            detail="Job no encontrado. Puede haber expirado (TTL 1 h) o nunca existió.")
    return {"job_id": job_id, **job}


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

# ─── Router AI ─────────────────────────────────────────────────────────────────
from routers.ai import ai_router
app.include_router(ai_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
