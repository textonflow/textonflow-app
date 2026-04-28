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

from stats import _read_stats, _increment_images_generated

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

# ─── Router Render + Templates ─────────────────────────────────────────────────
from routers.render import render_router
app.include_router(render_router)

# ─── Router AI ─────────────────────────────────────────────────────────────────
from routers.ai import ai_router
app.include_router(ai_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
