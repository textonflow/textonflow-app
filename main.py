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

# ─── Tareas de arranque (auto-update estáticos + minificación JS) ─────────────
from startup import run_startup
run_startup()

app.mount("/fonts", StaticFiles(directory="fonts"), name="fonts")
app.mount("/static", StaticFiles(directory="static"), name="static")




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

# ─── Router Páginas (estáticas, health, stats, proxy-image) ─────────────────
from routers.pages import pages_router
app.include_router(pages_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
