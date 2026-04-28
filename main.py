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
                       stripe_customer_id, watermark_exempt, is_active, created_at
                FROM users WHERE id = %s
            """, (payload["sub"],))
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en /user/me: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    plan = user["plan"]
    limit = USER_PLAN_LIMITS.get(plan, user["renders_limit"])
    watermark_active = plan not in JSON_EXPORT_PLANS and not user.get("watermark_exempt", False)
    can_export_json = plan in JSON_EXPORT_PLANS

    # ── Info de trial basado en tiempo ────────────────────────────────────────
    trial_expires_at = None
    trial_days_remaining = None
    trial_expired = False
    if plan == "trial" and user["created_at"]:
        from datetime import timezone as _tz
        created = user["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=_tz.utc)
        expires = created + timedelta(days=TRIAL_DAYS)
        trial_expires_at = expires.isoformat()
        remaining = (expires - datetime.now(_tz.utc)).days
        trial_days_remaining = max(0, remaining)
        trial_expired = remaining < 0

    return {
        "id": str(user["id"]),
        "email": user["email"],
        "plan": plan,
        "renders_used": user["renders_used"],
        "renders_limit": limit,
        "renders_remaining": max(0, limit - user["renders_used"]),
        "has_gemini_key": bool(user["gemini_api_key"]),
        "has_stripe": bool(user["stripe_customer_id"]),
        "watermark_active": watermark_active,
        "watermark_exempt": bool(user.get("watermark_exempt", False)),
        "can_export_json": can_export_json,
        "is_active": user.get("is_active", True),
        "created_at": user["created_at"].isoformat() if user["created_at"] else None,
        "trial_expires_at": trial_expires_at,
        "trial_days_remaining": trial_days_remaining,
        "trial_expired": trial_expired,
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

@app.delete("/api/user/me", tags=["Usuarios"], summary="Eliminar cuenta (GDPR)")
async def delete_user_me(request: Request):
    """
    Elimina permanentemente la cuenta del usuario autenticado y todos sus datos asociados.
    Cancela la suscripción activa en Stripe si existe.
    Cumplimiento GDPR Art. 17 — Derecho de supresión.
    """
    payload = _require_user(request)
    user_id = payload["sub"]
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")

    # Obtener datos del usuario antes de eliminar
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, stripe_customer_id, plan FROM users WHERE id = %s",
                (user_id,)
            )
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Error leyendo usuario para DELETE /api/user/me: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    # Cancelar suscripción Stripe si existe
    stripe_customer_id = user.get("stripe_customer_id")
    if stripe_customer_id and _STRIPE_OK:
        try:
            import stripe as _s
            _s.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
            subs = _s.Subscription.list(customer=stripe_customer_id, status="active", limit=5)
            for sub in subs.get("data", []):
                try:
                    _s.Subscription.cancel(sub["id"])
                    logger.info(f"Stripe subscription {sub['id']} cancelada para usuario {user_id}")
                except Exception as stripe_err:
                    logger.warning(f"No se pudo cancelar suscripción Stripe {sub['id']}: {stripe_err}")
        except Exception as e:
            logger.warning(f"Error al consultar Stripe para DELETE /api/user/me user {user_id}: {e}")

    # Eliminar todos los datos del usuario de la base de datos
    try:
        with conn.cursor() as cur:
            # Eliminar proyectos
            cur.execute("DELETE FROM projects WHERE user_id = %s", (user_id,))
            # Eliminar suscripciones registradas localmente
            cur.execute("DELETE FROM subscriptions WHERE user_id = %s", (user_id,))
            # Eliminar usuario
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"Error eliminando usuario {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al eliminar la cuenta. Contacta a hola@textonflow.com.")

    logger.info(f"Cuenta eliminada por solicitud del usuario: {user.get('email', user_id)} (GDPR Art.17)")
    return {"ok": True, "message": "Cuenta eliminada permanentemente."}


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
                SELECT plan, renders_used, renders_limit, created_at FROM users WHERE id = %s
            """, (payload["sub"],))
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en /user/usage: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    plan  = user["plan"]
    limit = USER_PLAN_LIMITS.get(plan, 20)

    # ── Trial basado en tiempo ─────────────────────────────────────────────────
    trial_days_remaining = None
    trial_expired = False
    if plan == "trial" and user["created_at"]:
        from datetime import timezone as _tz
        created = user["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=_tz.utc)
        expires = created + timedelta(days=TRIAL_DAYS)
        remaining = (expires - datetime.now(_tz.utc)).days
        trial_days_remaining = max(0, remaining)
        trial_expired = remaining < 0

    return {
        "plan": plan,
        "renders_used": user["renders_used"],
        "renders_limit": limit,
        "renders_remaining": None if plan == "trial" else max(0, limit - user["renders_used"]),
        "pct": 0 if plan == "trial" else (min(100, round(user["renders_used"] / limit * 100)) if limit else 0),
        "trial_days_remaining": trial_days_remaining,
        "trial_expired": trial_expired,
    }


# ─── Webhook URL del usuario ──────────────────────────────────────────────────

@app.get("/user/webhook", tags=["webhooks"],
         summary="Obtener webhook URL configurado",
         response_description="URL de webhook del usuario o null")
async def get_user_webhook(request: Request):
    """Devuelve el webhook_url configurado para el usuario autenticado."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT webhook_url FROM users WHERE id = %s", (payload["sub"],))
        row = cur.fetchone()
    return {"webhook_url": row["webhook_url"] if row else None}

@app.put("/user/webhook", tags=["webhooks"],
         summary="Configurar webhook URL",
         response_description="Confirmación de actualización")
async def set_user_webhook(body: _WebhookBody, request: Request):
    """
    Guarda o borra el webhook_url del usuario.  
    TextOnFlow hará POST a esa URL tras cada render exitoso con el payload:
    `{"event":"render.done","image_url":"...","template":"...","ts":"..."}`
    """
    payload = _require_user(request)
    url = (body.webhook_url or "").strip() or None
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="La URL debe empezar con http:// o https://")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET webhook_url = %s, updated_at = NOW() WHERE id = %s",
                    (url, payload["sub"]))
    return {"ok": True, "webhook_url": url}


# ─── Proyectos ─────────────────────────────────────────────────────────────────

@app.post("/projects", tags=["projects"], status_code=201,
          summary="Crear proyecto")
async def create_project(body: _ProjectCreate, request: Request):
    """Crea un nuevo proyecto guardando el estado completo del canvas."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            INSERT INTO projects (user_id, name, canvas_json, image_url)
            VALUES (%s, %s, %s, %s)
            RETURNING id, name, image_url, created_at, updated_at
        """, (payload["sub"], body.name[:120], json.dumps(body.canvas_json), body.image_url))
        row = cur.fetchone()
    return {
        "id": str(row["id"]), "name": row["name"],
        "image_url": row["image_url"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }

@app.get("/projects", tags=["projects"],
         summary="Listar proyectos del usuario")
async def list_projects(request: Request, limit: int = 50, offset: int = 0):
    """Devuelve los proyectos del usuario ordenados por actualización desc."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, name, image_url, created_at, updated_at
            FROM projects WHERE user_id = %s
            ORDER BY updated_at DESC LIMIT %s OFFSET %s
        """, (payload["sub"], min(limit, 200), offset))
        rows = cur.fetchall()
    return {"projects": [
        {"id": str(r["id"]), "name": r["name"], "image_url": r["image_url"],
         "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()}
        for r in rows
    ], "total": len(rows)}

@app.get("/projects/{project_id}", tags=["projects"],
         summary="Obtener proyecto con canvas completo")
async def get_project(project_id: str, request: Request):
    """Devuelve el proyecto completo incluyendo canvas_json para restaurar el editor."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, name, canvas_json, image_url, created_at, updated_at
            FROM projects WHERE id = %s AND user_id = %s
        """, (project_id, payload["sub"]))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    return {
        "id": str(row["id"]), "name": row["name"],
        "canvas_json": row["canvas_json"],
        "image_url": row["image_url"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }

@app.put("/projects/{project_id}", tags=["projects"],
         summary="Actualizar proyecto")
async def update_project(project_id: str, body: _ProjectUpdate, request: Request):
    """Actualiza nombre, canvas o imagen de un proyecto existente."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    updates, vals = [], []
    if body.name is not None:
        updates.append("name = %s"); vals.append(body.name[:120])
    if body.canvas_json is not None:
        updates.append("canvas_json = %s"); vals.append(json.dumps(body.canvas_json))
    if body.image_url is not None:
        updates.append("image_url = %s"); vals.append(body.image_url)
    if not updates:
        raise HTTPException(status_code=422, detail="Sin cambios.")
    updates.append("updated_at = NOW()")
    vals += [project_id, payload["sub"]]
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE projects SET {', '.join(updates)} WHERE id = %s AND user_id = %s",
            vals
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    return {"ok": True}

@app.delete("/projects/{project_id}", tags=["projects"],
            summary="Eliminar proyecto")
async def delete_project(project_id: str, request: Request):
    """Elimina un proyecto del usuario."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id = %s AND user_id = %s",
                    (project_id, payload["sub"]))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    return {"ok": True}


# ─── Recuperación de contraseña ───────────────────────────────────────────────

@app.post("/user/forgot-password")
async def user_forgot_password(body: _ForgotPasswordBody):
    """Genera token de reset y envía email. Siempre responde OK (no revela si email existe)."""
    email = body.email.strip().lower()
    conn = get_db()
    if not conn:
        return {"ok": True}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, email FROM users WHERE email = %s AND is_active = TRUE", (email,))
            user = cur.fetchone()
        if not user:
            return {"ok": True}  # silencioso por seguridad
        reset_token = secrets.token_urlsafe(32)
        with conn.cursor() as cur:
            # Invalida tokens anteriores del mismo usuario
            cur.execute("UPDATE password_resets SET used = TRUE WHERE user_id = %s AND used = FALSE", (user["id"],))
            cur.execute("""
                INSERT INTO password_resets (user_id, token, expires_at)
                VALUES (%s, %s, NOW() + INTERVAL '1 hour')
            """, (user["id"], reset_token))
    except Exception as e:
        logger.error(f"Error en forgot-password: {e}")
        return {"ok": True}

    base = os.environ.get("BASE_URL", "https://www.textonflow.com").rstrip("/")
    reset_url = f"{base}/reset-password?token={reset_token}"
    em_key = os.getenv("ENGINEMAILER_API_KEY", "")
    if em_key:
        def _send_reset():
            try:
                import requests as _req
                _req.post(
                    "https://api.enginemailer.com/RESTAPI/V2/Submission/SendEmail",
                    json={
                        "UserKey": em_key,
                        "ToEmail": email,
                        "ToName": email.split("@")[0],
                        "SenderEmail": "noreply@textonflow.com",
                        "SenderName": "TextOnFlow",
                        "Subject": "Recupera tu contraseña — TextOnFlow",
                        "HTMLContent": f"""<div style="font-family:Arial,sans-serif;max-width:500px;margin:auto">
<h2 style="color:#7c3aed">Recuperar contraseña</h2>
<p>Hola, recibimos una solicitud para restablecer tu contraseña de TextOnFlow.</p>
<p><a href="{reset_url}" style="background:#7c3aed;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block">Restablecer contraseña</a></p>
<p style="color:#888;font-size:12px">Este enlace expira en 1 hora. Si no solicitaste este cambio, ignora este email.</p>
</div>""",
                    }, timeout=10
                )
            except Exception as _e:
                logger.warning(f"Email reset no enviado: {_e}")
        import threading as _thr
        _thr.Thread(target=_send_reset, daemon=True).start()
    logger.info(f"🔑 Reset solicitado para {email}")
    return {"ok": True}

@app.post("/user/reset-password")
async def user_reset_password(body: _ResetPasswordBody):
    """Valida el token y actualiza la contraseña."""
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT pr.id, pr.user_id, pr.expires_at, pr.used
                FROM password_resets pr
                WHERE pr.token = %s
            """, (body.token,))
            rec = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en reset-password: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not rec:
        raise HTTPException(status_code=400, detail="Token inválido o expirado.")
    if rec["used"]:
        raise HTTPException(status_code=400, detail="Este enlace ya fue usado.")
    if rec["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="El enlace expiró. Solicita uno nuevo.")
    new_hash = hash_password(body.new_password)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password_hash = %s, updated_at = NOW() WHERE id = %s",
                        (new_hash, rec["user_id"]))
            cur.execute("UPDATE password_resets SET used = TRUE WHERE id = %s", (rec["id"],))
    except Exception as e:
        logger.error(f"Error actualizando contraseña: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    logger.info(f"✅ Contraseña actualizada para user_id={rec['user_id']}")
    return {"ok": True, "message": "Contraseña actualizada correctamente."}

@app.get("/user/can-export")
async def user_can_export(request: Request):
    """Indica si el usuario puede exportar JSON (solo planes pagados)."""
    payload = _get_current_user(request)
    if not payload:
        return {"can_export": False, "reason": "auth_required"}
    plan = payload.get("plan", "trial")
    if plan in JSON_EXPORT_PLANS:
        return {"can_export": True, "plan": plan}
    return {"can_export": False, "reason": "upgrade_required", "plan": plan}

@app.post("/user/track-copy")
async def user_track_copy(request: Request):
    """Registra que el usuario dio clic en 'Copiar JSON'. Requiere JWT."""
    payload = _get_current_user(request)
    if not payload:
        return {"ok": False}
    user_id = payload.get("sub") or payload.get("user_id")
    conn = get_db()
    if not conn:
        return {"ok": False}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET json_copies = COALESCE(json_copies,0) + 1, "
                "last_active_at = NOW(), updated_at = NOW() WHERE id = %s",
                (user_id,)
            )
    except Exception as e:
        logger.warning(f"track-copy error: {e}")
    return {"ok": True}

# ─── Image Session Tracking ───────────────────────────────────────────────────

@app.post("/user/session/open")
async def image_session_open(body: _SessionOpenBody, request: Request):
    """Registra apertura de una sesión de imagen (anónimo o autenticado)."""
    conn = get_db()
    if not conn:
        return {"ok": True}
    user_id = None
    try:
        payload = _get_current_user(request)
        if payload:
            user_id = payload.get("sub") or payload.get("user_id")
    except Exception:
        pass
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO image_sessions (session_key, user_id, image_name, image_type, ip)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_key) DO NOTHING
            """, (body.session_key, user_id, body.image_name[:500], body.image_type, _get_client_ip(request)))
        conn.commit()
    except Exception as e:
        logger.warning(f"session/open error: {e}")
    return {"ok": True}

@app.post("/user/session/close")
async def image_session_close(body: _SessionCloseBody, request: Request):
    """Registra cierre de una sesión de imagen y calcula duración."""
    conn = get_db()
    if not conn:
        return {"ok": True}
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE image_sessions
                SET closed_at = NOW(),
                    duration_seconds = GREATEST(0, EXTRACT(EPOCH FROM (NOW() - opened_at))::INTEGER)
                WHERE session_key = %s AND closed_at IS NULL
            """, (body.session_key,))
        conn.commit()
    except Exception as e:
        logger.warning(f"session/close error: {e}")
    return {"ok": True}

@app.get("/api/admin/image-sessions")
async def admin_image_sessions(request: Request):
    """Reporte de sesiones por imagen — solo superadmin."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # KPIs globales
            cur.execute("""
                SELECT
                  COUNT(*) AS total_sessions,
                  COUNT(*) FILTER (WHERE opened_at >= NOW() - INTERVAL '24 hours') AS sessions_today,
                  COUNT(*) FILTER (WHERE opened_at >= NOW() - INTERVAL '7 days') AS sessions_week,
                  ROUND(AVG(duration_seconds) FILTER (WHERE duration_seconds IS NOT NULL AND duration_seconds < 7200))::INTEGER AS avg_duration_sec,
                  COUNT(DISTINCT image_name) AS unique_images
                FROM image_sessions
            """)
            kpis = dict(cur.fetchone())
            # Top imágenes por número de aperturas
            cur.execute("""
                SELECT image_name, image_type,
                  COUNT(*) AS total_opens,
                  COUNT(*) FILTER (WHERE opened_at >= NOW() - INTERVAL '24 hours') AS opens_today,
                  ROUND(AVG(duration_seconds) FILTER (WHERE duration_seconds IS NOT NULL AND duration_seconds < 7200))::INTEGER AS avg_duration_sec,
                  MAX(opened_at) AS last_opened
                FROM image_sessions
                GROUP BY image_name, image_type
                ORDER BY total_opens DESC
                LIMIT 50
            """)
            top_images = []
            for r in cur.fetchall():
                row = dict(r)
                row["last_opened"] = row["last_opened"].isoformat() if row.get("last_opened") else None
                top_images.append(row)
            # Aperturas por día (últimos 30 días)
            cur.execute("""
                SELECT DATE(opened_at) AS day, COUNT(*) AS opens
                FROM image_sessions
                WHERE opened_at >= NOW() - INTERVAL '30 days'
                GROUP BY DATE(opened_at) ORDER BY day
            """)
            by_day = [{"day": str(r["day"]), "opens": r["opens"]} for r in cur.fetchall()]
            # Últimas 100 sesiones
            cur.execute("""
                SELECT s.session_key, s.image_name, s.image_type,
                  s.opened_at, s.closed_at, s.duration_seconds, s.ip,
                  u.email AS user_email
                FROM image_sessions s
                LEFT JOIN users u ON s.user_id = u.id
                ORDER BY s.opened_at DESC LIMIT 100
            """)
            recent = []
            for r in cur.fetchall():
                row = dict(r)
                row["opened_at"] = row["opened_at"].isoformat() if row.get("opened_at") else None
                row["closed_at"] = row["closed_at"].isoformat() if row.get("closed_at") else None
                recent.append(row)
    except Exception as e:
        logger.error(f"admin_image_sessions error: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    return {"kpis": {k: (int(v) if v is not None else 0) for k, v in kpis.items()}, "top_images": top_images, "by_day": by_day, "recent": recent}

# ─── Admin: gestión de usuarios ───────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_list_users(request: Request, page: int = 1, limit: int = 200):
    """Lista todos los usuarios con stats completas (solo superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    offset = (page - 1) * limit
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, email, plan, renders_used, renders_limit, is_active,
                       COALESCE(is_paused, FALSE) AS is_paused,
                       watermark_exempt, json_exports_used,
                       COALESCE(json_copies, 0) AS json_copies,
                       last_active_at, created_at, updated_at
                FROM users ORDER BY created_at DESC LIMIT %s OFFSET %s
            """, (limit, offset))
            users = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) as total FROM users")
            total = cur.fetchone()["total"]
    except Exception as e:
        logger.error(f"Error en admin/users: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    for u in users:
        u["id"]             = str(u["id"])
        u["created_at"]     = u["created_at"].isoformat() if u.get("created_at") else None
        u["updated_at"]     = u["updated_at"].isoformat() if u.get("updated_at") else None
        u["last_active_at"] = u["last_active_at"].isoformat() if u.get("last_active_at") else None
    return {"users": users, "total": total, "page": page, "limit": limit}

@app.get("/api/admin/stats")
async def admin_global_stats(request: Request):
    """Estadísticas globales del sistema (solo superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                  COUNT(*) AS total_users,
                  COUNT(*) FILTER (WHERE is_active) AS active_users,
                  COUNT(*) FILTER (WHERE NOT is_active) AS inactive_users,
                  COUNT(*) FILTER (WHERE plan = 'trial') AS trial_users,
                  COUNT(*) FILTER (WHERE plan = 'starter') AS starter_users,
                  COUNT(*) FILTER (WHERE plan = 'agency') AS agency_users,
                  COUNT(*) FILTER (WHERE plan = 'admin') AS admin_users,
                  COALESCE(SUM(renders_used), 0) AS total_renders,
                  COALESCE(SUM(json_copies), 0) AS total_json_copies,
                  COALESCE(SUM(json_exports_used), 0) AS total_json_exports,
                  COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS new_this_week,
                  COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS new_this_month,
                  COUNT(*) FILTER (WHERE last_active_at >= NOW() - INTERVAL '7 days') AS active_this_week
                FROM users
            """)
            stats = dict(cur.fetchone())
            # Renders por día (últimos 30 días)
            cur.execute("""
                SELECT DATE(created_at) AS day, COUNT(*) AS renders
                FROM renders
                WHERE created_at >= NOW() - INTERVAL '30 days'
                GROUP BY DATE(created_at) ORDER BY day
            """)
            renders_by_day = [{"day": str(r["day"]), "renders": r["renders"]} for r in cur.fetchall()]
            # Nuevos usuarios por día (últimos 30 días)
            cur.execute("""
                SELECT DATE(created_at) AS day, COUNT(*) AS users
                FROM users
                WHERE created_at >= NOW() - INTERVAL '30 days'
                GROUP BY DATE(created_at) ORDER BY day
            """)
            users_by_day = [{"day": str(r["day"]), "users": r["users"]} for r in cur.fetchall()]
            # Top usuarios por renders
            cur.execute("""
                SELECT email, plan, renders_used,
                       COALESCE(json_copies, 0) AS json_copies,
                       last_active_at
                FROM users ORDER BY renders_used DESC LIMIT 10
            """)
            top_users = []
            for r in cur.fetchall():
                row = dict(r)
                row["last_active_at"] = row["last_active_at"].isoformat() if row.get("last_active_at") else None
                top_users.append(row)
    except Exception as e:
        logger.error(f"Error en admin/stats: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    return {
        "stats": {k: int(v) for k, v in stats.items()},
        "renders_by_day": renders_by_day,
        "users_by_day": users_by_day,
        "top_users": top_users,
    }

@app.post("/api/admin/users/toggle-active")
async def admin_toggle_active(body: _AdminUserActionBody, request: Request):
    """Activa o desactiva la cuenta de un usuario (superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE users SET is_active = NOT is_active, updated_at = NOW() WHERE id = %s "
                "RETURNING id, email, is_active", (body.user_id,)
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en toggle-active: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    action = "activado" if row["is_active"] else "desactivado"
    logger.info(f"👤 Admin {action} usuario {row['email']}")
    return {"ok": True, "user_id": str(row["id"]), "email": row["email"], "is_active": row["is_active"]}

@app.post("/api/admin/users/toggle-paused")
async def admin_toggle_paused(body: _AdminUserActionBody, request: Request):
    """Pausa o reanuda la cuenta de un usuario sin desactivarla (superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE users SET is_paused = NOT COALESCE(is_paused, FALSE), updated_at = NOW() WHERE id = %s "
                "RETURNING id, email, is_paused", (body.user_id,)
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en toggle-paused: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    action = "pausado" if row["is_paused"] else "reanudado"
    logger.info(f"⏸ Admin {action} usuario {row['email']}")
    return {"ok": True, "user_id": str(row["id"]), "email": row["email"], "is_paused": row["is_paused"]}

@app.delete("/api/admin/users/delete")
async def admin_delete_user(body: _AdminUserActionBody, request: Request):
    """Elimina permanentemente un usuario (superadmin). El propio superadmin no puede eliminarse."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, email, plan FROM users WHERE id = %s", (body.user_id,))
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="Usuario no encontrado.")
            if target["email"].lower() == "ruben@textonflow.com" or target["plan"] == "admin":
                raise HTTPException(status_code=403, detail="No se puede eliminar al superadministrador.")
            cur.execute("DELETE FROM users WHERE id = %s RETURNING id, email", (body.user_id,))
            deleted = cur.fetchone()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en admin/delete-user: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    logger.info(f"🗑 Admin eliminó usuario {deleted['email']}")
    return {"ok": True, "deleted_email": deleted["email"]}

@app.post("/api/admin/users/toggle-watermark")
async def admin_toggle_watermark(body: _AdminUserActionBody, request: Request):
    """Otorga o revoca la exención de watermark a un usuario (superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE users SET watermark_exempt = NOT watermark_exempt, updated_at = NOW() "
                "WHERE id = %s RETURNING id, email, watermark_exempt, plan", (body.user_id,)
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en toggle-watermark: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    status = "exento" if row["watermark_exempt"] else "con marca"
    logger.info(f"🖼️ Admin watermark {status} → {row['email']}")
    return {"ok": True, "user_id": str(row["id"]), "email": row["email"],
            "watermark_exempt": row["watermark_exempt"], "plan": row["plan"]}

@app.post("/api/admin/users/reset-renders")
async def admin_reset_renders(body: _AdminUserActionBody, request: Request):
    """Reinicia el contador de renders de un usuario (superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE users SET renders_used = 0, updated_at = NOW() WHERE id = %s "
                "RETURNING id, email", (body.user_id,)
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en reset-renders: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    logger.info(f"🔄 Admin reinició renders de {row['email']}")
    return {"ok": True, "user_id": str(row["id"]), "email": row["email"], "renders_used": 0}

@app.get("/reset-password", include_in_schema=False)
async def reset_password_page():
    """Página para restablecer la contraseña vía token."""
    from fastapi.responses import HTMLResponse
    html = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Restablecer contraseña — TextOnFlow</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0f0f17;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#1a1a2e;border:1px solid #2d2d44;border-radius:16px;padding:40px;width:100%;max-width:420px}
h2{color:#a78bfa;margin-bottom:8px;font-size:1.5rem}
p{color:#94a3b8;font-size:.875rem;margin-bottom:24px}
label{display:block;font-size:.8rem;color:#94a3b8;margin-bottom:6px}
input{width:100%;background:#0f0f17;border:1px solid #374151;border-radius:8px;padding:10px 14px;color:#e2e8f0;font-size:.9rem;margin-bottom:16px}
button{width:100%;background:#7c3aed;color:#fff;border:none;border-radius:8px;padding:12px;font-size:.95rem;cursor:pointer;font-weight:600}
button:hover{background:#6d28d9}
.msg{margin-top:16px;padding:12px;border-radius:8px;font-size:.85rem;text-align:center}
.msg.ok{background:#064e3b;color:#6ee7b7}
.msg.err{background:#7f1d1d;color:#fca5a5}
</style>
</head>
<body>
<div class="card">
  <h2>🔑 Nueva contraseña</h2>
  <p>Ingresa tu nueva contraseña para TextOnFlow.</p>
  <form id="form">
    <label>Nueva contraseña</label>
    <input type="password" id="pw1" placeholder="Mínimo 8 caracteres" required minlength="8">
    <label>Confirmar contraseña</label>
    <input type="password" id="pw2" placeholder="Repite la contraseña" required minlength="8">
    <button type="submit" id="btn">Restablecer contraseña</button>
  </form>
  <div id="msg" class="msg" style="display:none"></div>
</div>
<script>
const token = new URLSearchParams(location.search).get('token');
if(!token){document.getElementById('form').innerHTML='<p style="color:#f87171">Enlace inválido. Solicita uno nuevo.</p>';}
document.getElementById('form').addEventListener('submit',async e=>{
  e.preventDefault();
  const pw1=document.getElementById('pw1').value;
  const pw2=document.getElementById('pw2').value;
  if(pw1!==pw2){show('Las contraseñas no coinciden.','err');return;}
  document.getElementById('btn').disabled=true;
  document.getElementById('btn').textContent='Guardando...';
  try{
    const r=await fetch('/user/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,new_password:pw1})});
    const d=await r.json();
    if(r.ok){show('✅ Contraseña actualizada. Redirigiendo...','ok');setTimeout(()=>location.href='/',2500);}
    else{show(d.detail||'Error al restablecer.','err');document.getElementById('btn').disabled=false;document.getElementById('btn').textContent='Restablecer contraseña';}
  }catch(err){show('Error de conexión.','err');document.getElementById('btn').disabled=false;document.getElementById('btn').textContent='Restablecer contraseña';}
});
function show(txt,type){const m=document.getElementById('msg');m.textContent=txt;m.className='msg '+type;m.style.display='block';}
</script>
</body></html>"""
    return HTMLResponse(content=html)

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
    base = body.success_url or os.environ.get("BASE_URL", "https://www.textonflow.com").rstrip("/")
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


# ══════════════════════════════════════════════════════════════
# AI PRODUCT FEATURES — v1.0
# 1. Design from description  2. Copy suggestions
# 3. Brand kit extraction     4. A/B variant generator
# ══════════════════════════════════════════════════════════════

_DESIGN_LAYOUT_SYSTEM = """Eres un experto diseñador de imágenes de marketing para ManyChat.
El usuario te describe el diseño que necesita y tú generas el layout de texto en formato JSON.

Devuelve ÚNICAMENTE un JSON válido con esta estructura (sin markdown, sin explicaciones):
{
  "texts": [
    {
      "text": "Texto aquí",
      "auto_alignment": "center",
      "alignment": "auto",
      "font_size": 72,
      "font_color": "#FFFFFF",
      "font_value": "Roboto",
      "font_backend": "Roboto",
      "text_align": "center",
      "line_spacing": 4,
      "background_enabled": false,
      "background_color": "#000000",
      "background_opacity": 70,
      "bg_pad_top": 10,
      "bg_pad_right": 20,
      "bg_pad_bottom": 10,
      "bg_pad_left": 20,
      "bg_box_radius": 8,
      "stroke_enabled": false,
      "stroke_color": "#000000",
      "stroke_width": 2,
      "stroke_opacity": 100
    }
  ],
  "background_suggestion": "Descripción breve del fondo ideal en español",
  "color_palette": ["#HEX1", "#HEX2", "#HEX3"]
}

REGLAS:
- auto_alignment puede ser: center, top-center, bottom-center, top-left, top-right, bottom-left, bottom-right, middle-left, middle-right
- Usa máximo 4 capas de texto para no saturar la imagen
- font_value y font_backend deben ser nombres de fuentes Google Fonts comunes
- Adapta tamaños: título grande (60-120px), subtítulo (36-60px), detalle (24-40px), CTA (44-64px)
- Colores vibrantes y contrastantes para marketing
- Si el contexto menciona colores específicos úsalos
- Para CTAs usa background_enabled: true con bg_box_radius: 10
- Responde SOLO con el JSON, nada más"""

@app.post("/api/ai/design-layout")
async def ai_design_layout(req: DesignLayoutRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    if len(req.description.strip()) < 5:
        raise HTTPException(status_code=400, detail="La descripción es muy corta")
    user_msg = f"Descripción del diseño: {req.description.strip()}"
    if req.context:
        user_msg += f"\nContexto adicional: {req.context.strip()}"
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": user_msg}]}],
        "systemInstruction": {"parts": [{"text": _DESIGN_LAYOUT_SYSTEM}]},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1200, "candidateCount": 1,
                             "responseMimeType": "application/json"}
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Error al conectar con la IA")
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()
        # Strip markdown fences if present
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        layout = json.loads(raw)
        # Fill mandatory fields with defaults for each text object
        defaults = {
            "x": req.canvas_width // 2, "y": req.canvas_height // 2,
            "padding_x": 40, "padding_y": 30,
            "rotation": 0, "skew_x": 0, "skew_y": 0, "opacity": 1.0,
            "bg_color_type": "solid", "bg_gradient_color2": "#FFFFFF", "bg_gradient_angle": 135,
            "bg_stroke_color": "#FFFFFF", "bg_stroke_width": 0, "bg_stroke_opacity": 100,
            "bg_brd_pad_top": 10, "bg_brd_pad_right": 20, "bg_brd_pad_bottom": 10, "bg_brd_pad_left": 20,
            "bg_brd_pad_linked": True, "bg_pad_linked": True,
            "warp_style": "none", "warp_bend": 0,
            "canvas_padding_enabled": False, "canvas_padding_value": 40, "canvas_padding_side": "left",
            "text_wrap_enabled": False, "text_wrap_padding": 60,
            "stroke_type": "solid", "stroke_gradient_color2": "#FFFFFF", "stroke_gradient_angle": 135,
            "stroke_dash": "solid", "bg_border_enabled": False,
            "has_background_layer": False, "back_color": "#000000",
            "back_opacity": 0.3, "offset_x": 10, "offset_y": 10,
            "back_blur": 5, "back_blend_mode": "multiply",
        }
        for t in layout.get("texts", []):
            for k, v in defaults.items():
                t.setdefault(k, v)
        return layout
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"La IA devolvió JSON inválido: {e}")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


# ─── Copy Suggestions ───────────────────────────────────────
_COPY_SYSTEM = """Eres un copywriter experto en marketing para ManyChat y redes sociales.
Devuelve ÚNICAMENTE un JSON con 3 variaciones creativas del texto dado.
Formato: {"suggestions": ["variación 1", "variación 2", "variación 3"]}
- Mantén un tono similar al original pero varía el enfoque (urgencia, emoción, beneficio)
- Máximo 2 líneas por variación
- Sin comillas anidadas en las variaciones
- Responde SOLO el JSON"""

@app.post("/api/ai/copy-suggestions")
async def ai_copy_suggestions(req: CopySuggestionsRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    if not req.current_text.strip():
        raise HTTPException(status_code=400, detail="El texto no puede estar vacío")
    user_msg = f"Texto actual: {req.current_text.strip()}"
    if req.context:
        user_msg += f"\nContexto del diseño: {req.context.strip()}"
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": user_msg}]}],
        "systemInstruction": {"parts": [{"text": _COPY_SYSTEM}]},
        "generationConfig": {"temperature": 0.85, "maxOutputTokens": 300,
                             "responseMimeType": "application/json"}
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Error al conectar con la IA")
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = "\n".join(p.get("text","") for p in parts if "text" in p).strip()
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="La IA devolvió JSON inválido")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


# ─── Brand Kit Extraction ────────────────────────────────────
_BRAND_KIT_SYSTEM = """Eres un experto en identidad visual y branding.
Analiza el logo o imagen proporcionada y extrae la paleta de colores de marca.
Devuelve ÚNICAMENTE un JSON con este formato:
{
  "colors": ["#HEX1","#HEX2","#HEX3","#HEX4","#HEX5"],
  "background_color": "#HEX",
  "font_suggestion": "Nombre de fuente Google Fonts que combine con la marca",
  "style": "moderno|elegante|divertido|minimalista|corporativo",
  "description": "Descripción breve del estilo de marca en español (1 oración)"
}
- Extrae exactamente 5 colores representativos (de más a menos prominente)
- background_color: el color más claro / fondo ideal
- Responde SOLO el JSON"""

@app.post("/api/ai/brand-kit")
async def ai_brand_kit(req: BrandKitRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    # Download the image and convert to base64
    try:
        img_resp = requests.get(req.image_url, timeout=15)
        img_resp.raise_for_status()
        img_data = base64.b64encode(img_resp.content).decode()
        content_type = img_resp.headers.get("content-type", "image/png").split(";")[0].strip()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No se pudo cargar la imagen: {e}")
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [
            {"text": "Analiza este logo y extrae la paleta de colores de marca."},
            {"inlineData": {"mimeType": content_type, "data": img_data}}
        ]}],
        "systemInstruction": {"parts": [{"text": _BRAND_KIT_SYSTEM}]},
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400,
                             "responseMimeType": "application/json"}
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Error al conectar con la IA")
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = "\n".join(p.get("text","") for p in parts if "text" in p).strip()
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="La IA devolvió JSON inválido")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


# ─── A/B Variant Generator ───────────────────────────────────
_AB_VARIANTS_SYSTEM = """Eres un experto en diseño de marketing y optimización de conversión.
Tomas un layout de texto y generas 3 variantes visuales con diferentes paletas de color y estilos.
Devuelve ÚNICAMENTE un JSON con este formato:
{
  "variants": [
    {
      "label": "Variante A — Alta Energía",
      "theme": "bold",
      "color_overrides": {"font_color": "#HEX", "background_color": "#HEX"},
      "texts": [ ...array of text objects with modified colors and/or font_size... ]
    },
    { "label": "Variante B — Elegante", "theme": "elegant", "color_overrides": {...}, "texts": [...] },
    { "label": "Variante C — Minimalista", "theme": "minimal", "color_overrides": {...}, "texts": [...] }
  ]
}
REGLAS:
- Mantén exactamente la misma estructura y posición de texto de cada capa
- Solo cambia: font_color, background_color, background_opacity, stroke_color, stroke_width, font_size (±10%)
- Cada variante debe tener una paleta de color claramente diferente
- Variante A: colores vibrantes y contrastantes
- Variante B: colores elegantes y sofisticados (dorado, gris oscuro, blanco)
- Variante C: colores mínimos, fondo oscuro, texto limpio
- Responde SOLO el JSON"""

@app.post("/api/ai/ab-variants")
async def ai_ab_variants(req: ABVariantsRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    if not req.texts:
        raise HTTPException(status_code=400, detail="No hay capas de texto para generar variantes")
    # Send only relevant fields to Gemini (not the full heavy object)
    slim_texts = []
    for t in req.texts:
        slim_texts.append({
            "text": t.get("text",""),
            "font_color": t.get("font_color","#FFFFFF"),
            "font_size": t.get("font_size", 60),
            "background_enabled": t.get("background_enabled", False),
            "background_color": t.get("background_color","#000000"),
            "background_opacity": t.get("background_opacity",70),
            "stroke_enabled": t.get("stroke_enabled",False),
            "stroke_color": t.get("stroke_color","#000000"),
            "stroke_width": t.get("stroke_width",2),
            "auto_alignment": t.get("auto_alignment","center"),
            "alignment": t.get("alignment","auto"),
            "font_value": t.get("font_value","Roboto"),
            "font_backend": t.get("font_backend","Roboto"),
            "text_align": t.get("text_align","center"),
            "line_spacing": t.get("line_spacing", 4),
        })
    user_msg = f"Layout actual con {len(slim_texts)} capas:\n{json.dumps(slim_texts, ensure_ascii=False)}"
    if req.context:
        user_msg += f"\nContexto del diseño: {req.context}"
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": user_msg}]}],
        "systemInstruction": {"parts": [{"text": _AB_VARIANTS_SYSTEM}]},
        "generationConfig": {"temperature": 0.75, "maxOutputTokens": 1500,
                             "responseMimeType": "application/json"}
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=35)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Error al conectar con la IA")
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = "\n".join(p.get("text","") for p in parts if "text" in p).strip()
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)
        # Merge back full original text objects with AI overrides
        full_texts = req.texts
        for variant in result.get("variants", []):
            merged = []
            for i, ai_t in enumerate(variant.get("texts", [])):
                if i < len(full_texts):
                    base = dict(full_texts[i])
                    base.update({k: v for k, v in ai_t.items()})
                    merged.append(base)
                else:
                    merged.append(ai_t)
            variant["texts"] = merged
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="La IA devolvió JSON inválido")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))
