"""
routers/render_helpers.py — Funciones auxiliares del motor de renderizado.
Helpers de PIL, Supabase, Mapbox, webhooks, rate-limiting y templates.
Importado por render.py (y otros módulos que necesiten _render_pil, etc.)
"""
"""
routers/render.py — Motor de renderizado PIL + templates + endpoints de imagen.
Incluye: /generate-multi, /render-async, /render-jobs, /api/templates/*, 
         /render/{template_id}, /webhook/render, /image/{filename}.
Montado en main.py con: app.include_router(render_router)
"""
import base64
import concurrent.futures as _futures
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Dict, Optional

import requests
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    np = None
    _NUMPY_OK = False

from auth import (
    _is_superadmin, _get_client_ip,
    _check_rate_limit, _check_minute_limit, _increment_ip_usage,
)
from database import get_db, log_render_event, get_user_render_stats
from fonts import (
    FONT_MAPPING, FONT_SIZE_SCALE, NOTO_EMOJI_PATHS,
    get_noto_emoji_font, build_retry_session, RetryTwitterEmojiSource,
)
from models import (
    TextField, CanvasShape, ImageOverlay, MultiTextRequest,
    ApiTemplateRequest, WebhookRenderRequest,
)
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
from stats import _increment_images_generated
from user_limits import (
    USER_PLAN_LIMITS, TRIAL_DAYS,
    _get_current_user, _require_user,
    _should_apply_watermark, _check_user_render_limit, _increment_user_renders,
    _get_user_id_by_render_key,
)
from utils import _get_base_url

logger = logging.getLogger("textonflow")

# ── Globals compartidos ────────────────────────────────────────────────


# ── Watermark logo helper ──────────────────────────────────────────────────────
def _apply_wm_logo(image: Image.Image,
                   corner: str = "br", size_px: int = 22,
                   opacity_pct: int = 55, color_hex: str = "#ffffff",
                   pill: bool = True) -> Image.Image:
    """Pega el logo de TextOnFlow sobre la imagen con posición y estilo configurables."""
    try:
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        img_w, img_h = image.size
        scale = min(img_w, img_h) / 1080.0
        logo_h = max(20, int(size_px * scale))

        # Busca el logo blanco (mejor sobre cualquier fondo)
        logo = None
        for _lp in ["static/logo-blanco-new.png",
                    "textonflow-api/static/logo-blanco-new.png",
                    "/app/static/logo-blanco-new.png",
                    os.path.join(os.path.dirname(__file__), "..", "static", "logo-blanco-new.png")]:
            if os.path.exists(_lp):
                logo = Image.open(_lp).convert("RGBA")
                break
        if logo is None:
            logger.warning("⚠️ Watermark: logo-blanco-new.png no encontrado en ninguna ruta")
            return image

        ow, oh = logo.size
        logo_w = max(1, int(ow * logo_h / oh))
        logo = logo.resize((logo_w, logo_h), Image.LANCZOS)

        # Tint del logo al color elegido
        hex_c = color_hex.lstrip("#")
        try:
            rt = int(hex_c[0:2], 16); gt = int(hex_c[2:4], 16); bt = int(hex_c[4:6], 16)
        except Exception:
            rt, gt, bt = 255, 255, 255
        logo_op = max(0, min(100, opacity_pct)) / 100.0
        px = [(rt, gt, bt, int(a * logo_op)) if a > 0 else (0, 0, 0, 0)
              for (r, g, b, a) in logo.getdata()]
        logo.putdata(px)

        margin = max(10, int(img_w * 0.018))
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))

        if pill:
            # Fondo pill oscuro semitransparente
            pad_x = max(6, int(logo_h * 0.4))
            pad_y = max(4, int(logo_h * 0.25))
            pill_w = logo_w + pad_x * 2
            pill_h = logo_h + pad_y * 2
            pill_alpha = int(160 * logo_op)
            pill_img = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
            pill_draw = ImageDraw.Draw(pill_img)
            radius = pill_h // 2
            # Color de pill: oscuro si logo es claro, claro si logo es oscuro
            hex_c2 = color_hex.lstrip("#")
            try:
                _rl = int(hex_c2[0:2],16)/255; _gl = int(hex_c2[2:4],16)/255; _bl = int(hex_c2[4:6],16)/255
                _lum = 0.2126*_rl + 0.7152*_gl + 0.0722*_bl
                pill_rgb = (255,255,255) if _lum < 0.5 else (0,0,0)
            except Exception:
                pill_rgb = (0,0,0)
            pill_draw.rounded_rectangle([(0,0),(pill_w-1,pill_h-1)], radius=radius,
                                        fill=(*pill_rgb, pill_alpha))
            px_pos = margin if corner in ("tl","bl") else img_w - pill_w - margin
            py_pos = margin if corner in ("tl","tr") else img_h - pill_h - margin
            overlay.paste(pill_img, (px_pos, py_pos), pill_img)
            overlay.paste(logo, (px_pos + pad_x, py_pos + pad_y), logo)
        else:
            # Sin fondo: logo directo
            logo_w2, logo_h2 = logo.size
            px_pos = margin if corner in ("tl","bl") else img_w - logo_w2 - margin
            py_pos = margin if corner in ("tl","tr") else img_h - logo_h2 - margin
            overlay.paste(logo, (px_pos, py_pos), logo)

        image = Image.alpha_composite(image, overlay)
        logger.info("✦ Watermark logo aplicado corner=%s size=%s op=%s pill=%s",
                    corner, size_px, opacity_pct, pill)
    except Exception as _e:
        logger.warning("⚠️ Watermark error: %s", _e)
    return image

# ── Supabase Storage (imágenes de salida permanentes) ─────────────────────────
_SB_URL    = os.getenv("SUPABASE_URL",              "https://dluzcrfqqieprudfeuyk.supabase.co")
_SB_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET",  "textonflow-uploads")
def _sb_key() -> str:
    import base64 as _b64
    return os.getenv("SUPABASE_SERVICE_ROLE_KEY") or _b64.b64decode(
        "c2Jfc2VjcmV0X1gxWEloNVp0ekEyTFd0VG9pV2thUGdfc21Pd1ZiM0Y=").decode()

def _upload_output_to_supabase(filepath: str, filename: str) -> Optional[str]:
    """Sube la imagen renderizada a Supabase Storage. Retorna URL pública o None si falla."""
    try:
        import urllib.request as _ureq
        key = _sb_key()
        if not key:
            return None
        with open(filepath, "rb") as f:
            data = f.read()
        url = f"{_SB_URL}/storage/v1/object/{_SB_BUCKET}/{filename}"
        req = _ureq.Request(url, data=data, method="POST")
        req.add_header("apikey",        key)
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Content-Type",  "image/jpeg")
        req.add_header("x-upsert",      "true")
        with _ureq.urlopen(req, timeout=30) as r:
            r.read()
        public_url = f"{_SB_URL}/storage/v1/object/public/{_SB_BUCKET}/{filename}"
        logger.info(f"☁️  Output → Supabase: {public_url}")
        return public_url
    except Exception as _e:
        logger.warning(f"⚠️  Output Supabase upload failed (usando URL local): {_e}")
        return None

# ── Mapbox Static Images helper ───────────────────────────────────────────────
def _fetch_mapbox_tile(location: str, zoom: int, style: str,
                       width: int, height: int, marker: bool,
                       vars_dict: dict = None,
                       from_location: str = None) -> "Image.Image":
    """Geocodifica `location` y descarga el tile estático de Mapbox.
    Soporta variables ManyChat: {{ciudad}}, {{cp}}, etc."""
    import urllib.parse as _up

    mapbox_key = os.getenv("MAPBOX_API_KEY", "")
    if not mapbox_key:
        raise ValueError("MAPBOX_API_KEY no configurada en Railway")

    # Resolver variables ManyChat {{var}} en la location
    if vars_dict:
        for k in sorted(vars_dict.keys(), key=len, reverse=True):
            location = location.replace(f"{{{{{k}}}}}", vars_dict[k])
            location = location.replace(f"{{{k}}}", vars_dict[k])
    location = location.strip()
    if not location:
        raise ValueError("La ubicación del mapa está vacía")

    # Detectar si ya son coordenadas lon,lat o lat,lon
    _coord = re.match(r'^(-?\d+\.?\d*),\s*(-?\d+\.?\d*)$', location)
    if _coord:
        a, b = float(_coord.group(1)), float(_coord.group(2))
        if -90 <= a <= 90 and -180 <= b <= 180:
            lon_s, lat_s = str(b), str(a)   # lat,lon → lon,lat
        else:
            lon_s, lat_s = str(a), str(b)
    else:
        # Geocodificar con Mapbox Geocoding API
        _enc = _up.quote(location)
        _geo_url = (f"https://api.mapbox.com/geocoding/v5/mapbox.places/"
                    f"{_enc}.json?access_token={mapbox_key}&limit=1")
        _sess = build_retry_session()
        _gr = _sess.get(_geo_url, timeout=12)
        _gr.raise_for_status()
        _feats = _gr.json().get("features", [])
        if not _feats:
            raise ValueError(f"Ubicación no encontrada: {location}")
        _center = _feats[0]["center"]   # [lon, lat]
        lon_s, lat_s = str(_center[0]), str(_center[1])

    w = max(1, min(1280, width))
    h = max(1, min(1280, height))

    if marker:
        _overlay = f"pin-l+e74c3c({lon_s},{lat_s})"
        _map_url = (f"https://api.mapbox.com/styles/v1/mapbox/{style}/static/"
                    f"{_overlay}/{lon_s},{lat_s},{zoom},0/{w}x{h}"
                    f"?access_token={mapbox_key}&logo=false&attribution=false")
    else:
        _map_url = (f"https://api.mapbox.com/styles/v1/mapbox/{style}/static/"
                    f"{lon_s},{lat_s},{zoom},0/{w}x{h}"
                    f"?access_token={mapbox_key}&logo=false&attribution=false")

    _sess2 = build_retry_session()
    _mr = _sess2.get(_map_url, timeout=15)
    _mr.raise_for_status()
    logger.info(f"🗺️ Mapbox tile OK ({w}×{h}) para '{location}'")
    tile = Image.open(BytesIO(_mr.content)).convert("RGBA")

    # ── Barra de distancia (opcional) ────────────────────────────────────────
    if from_location:
        try:
            _fl = from_location
            if vars_dict:
                for _k in sorted(vars_dict.keys(), key=len, reverse=True):
                    _fl = _fl.replace(f"{{{{{_k}}}}}", vars_dict[_k])
                    _fl = _fl.replace(f"{{{_k}}}", vars_dict[_k])
            _fl = _fl.strip()
            _fc = re.match(r'^(-?\d+\.?\d*),\s*(-?\d+\.?\d*)$', _fl)
            if _fc:
                _fa, _fb = float(_fc.group(1)), float(_fc.group(2))
                if -90 <= _fa <= 90 and -180 <= _fb <= 180:
                    _from_lon, _from_lat = str(_fb), str(_fa)
                else:
                    _from_lon, _from_lat = str(_fa), str(_fb)
            else:
                _geo2_url = (f"https://api.mapbox.com/geocoding/v5/mapbox.places/"
                             f"{_up.quote(_fl)}.json?access_token={mapbox_key}&limit=1")
                _gr2 = build_retry_session().get(_geo2_url, timeout=10)
                _feats2 = _gr2.json().get("features", [])
                if not _feats2:
                    raise ValueError(f"No se encontró: {_fl}")
                _c2 = _feats2[0]["center"]
                _from_lon, _from_lat = str(_c2[0]), str(_c2[1])

            # Mapbox Directions API
            _dirs_url = (f"https://api.mapbox.com/directions/v5/mapbox/driving/"
                         f"{_from_lon},{_from_lat};{lon_s},{lat_s}"
                         f"?access_token={mapbox_key}&overview=false")
            _dr = build_retry_session().get(_dirs_url, timeout=12)
            _routes = _dr.json().get("routes", [])
            if _routes:
                _dist_m  = _routes[0]["distance"]
                _dur_s   = _routes[0]["duration"]
                _dist_km = _dist_m / 1000
                _dur_min = int(_dur_s / 60)
                _label   = f"📍  {_dist_km:.1f} km  ·  ~{_dur_min} min en auto"

                # Dibujar barra semi-transparente en la parte inferior del tile
                from PIL import ImageDraw as _IDraw
                _bar_h = max(26, tile.height // 9)
                _bar   = Image.new("RGBA", (tile.width, _bar_h), (10, 10, 20, 210))
                tile.paste(_bar, (0, tile.height - _bar_h), _bar)
                _draw  = _IDraw.Draw(tile)
                _fsize = max(10, _bar_h - 10)
                try:
                    _fnt = ImageFont.truetype(
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _fsize)
                except Exception:
                    _fnt = ImageFont.load_default()
                _draw.text(
                    (tile.width // 2, tile.height - _bar_h // 2),
                    _label, fill=(255, 255, 255, 255),
                    font=_fnt, anchor="mm"
                )
                logger.info(f"📏 Distancia dibujada: {_label}")
        except Exception as _de:
            logger.warning(f"⚠️ Distancia omitida: {_de}")

    return tile


# ── Constantes de almacenamiento ───────────────────────────────────────────────
STORAGE_DIR       = os.getenv("STORAGE_PATH", os.path.join("static", "temp"))
TEMPLATES_API_DIR = os.getenv("TEMPLATES_API_PATH", os.path.join(STORAGE_DIR, "api_templates"))
os.makedirs(STORAGE_DIR,       exist_ok=True)
os.makedirs(TEMPLATES_API_DIR, exist_ok=True)
os.makedirs("output",          exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════════
#  GENERADOR DE IMÁGENES (módulo Design)
# ═══════════════════════════════════════════════════════════════════════════════

def _render_pil(request: "MultiTextRequest") -> "Image.Image":
    """Pipeline de render puro: carga imagen, aplica efectos/textos/shapes/overlays.
    Devuelve PIL Image (RGBA). No hace rate-limit ni guarda en disco."""
    # Cargar imagen
    # ── SUPABASE: descarga directa siempre, sin chequeo de storage local ─────────
    if "supabase.co" in request.template_name:
        session = build_retry_session()
        _sb_auth_headers = {
            "User-Agent": "TextOnFlow/1.0",
            "Accept": "image/*,*/*;q=0.8",
            "apikey": _sb_key(),
            "Authorization": f"Bearer {_sb_key()}",
        }
        response = session.get(request.template_name, timeout=20, headers=_sb_auth_headers)
        if response.status_code in (400, 404) or (
            response.status_code == 200
            and "application/json" in response.headers.get("Content-Type", "")
            and b"not found" in response.content.lower()
        ):
            fname = request.template_name.split("/")[-1].split("?")[0]
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Imagen no encontrada en Supabase Storage: '{fname}'. "
                    f"La imagen fue eliminada o el enlace es inválido. "
                    f"Ábrela en el editor, vuelve a subirla y actualiza el template en ManyChat."
                ),
            )
        response.raise_for_status()
        ct = response.headers.get("Content-Type", "")
        if "text/" in ct or "application/json" in ct:
            fname = request.template_name.split("/")[-1].split("?")[0]
            raise HTTPException(
                status_code=400,
                detail=f"La URL de Supabase no devolvió una imagen válida para '{fname}' (Content-Type: {ct}). Vuelve a subir la imagen en el editor.",
            )
        image = Image.open(BytesIO(response.content)).convert("RGBA")
        logger.info(f"☁️ _render_pil Supabase OK ({len(response.content)//1024} KB)")
    elif request.template_name.startswith(("http://", "https://")):
        local_path = None
        if "/storage/" in request.template_name:
            fname = request.template_name.split("/storage/")[-1].split("?")[0]
            local_path = os.path.join(STORAGE_DIR, fname)
        elif "/static/temp/" in request.template_name:
            fname = request.template_name.split("/static/temp/")[-1].split("?")[0]
            local_path = os.path.join("static", "temp", fname)
        if local_path:
            if os.path.exists(local_path):
                logger.info(f"📂 Leyendo imagen del storage local: {local_path}")
                image = Image.open(local_path).convert("RGBA")
            else:
                # Archivo no encontrado localmente (storage efímero en Railway) → fallback HTTP
                logger.warning(f"⚠️ Archivo local no encontrado ({local_path}), descargando via HTTP: {request.template_name}")
                try:
                    session = build_retry_session()
                    response = session.get(request.template_name, timeout=15,
                                           headers={"User-Agent": "TextOnFlow/1.0", "Accept": "image/*,*/*;q=0.8"})
                    if response.status_code == 404:
                        _fname = os.path.basename(local_path)
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"La imagen '{_fname}' ya no existe en el servidor "
                                f"(fue eliminada tras un redeploy). "
                                f"Abre el editor, vuelve a subir la imagen y actualiza el template en ManyChat."
                            ),
                        )
                    response.raise_for_status()
                    image = Image.open(BytesIO(response.content)).convert("RGBA")
                except HTTPException:
                    raise
                except Exception as _fe:
                    _fname = os.path.basename(local_path)
                    raise HTTPException(
                        status_code=400,
                        detail=f"Imagen '{_fname}' no encontrada localmente ni descargable: {_fe}. Re-sube la imagen en el editor.",
                    )
        else:
            logger.info(f"🔵 Descargando imagen: {request.template_name}")
            session = build_retry_session()
            response = session.get(request.template_name, timeout=15)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content)).convert("RGBA")
    elif request.template_name.startswith("/storage/") or request.template_name.startswith("/static/temp/"):
        # URL relativa local (generada cuando Supabase no está disponible)
        if request.template_name.startswith("/storage/"):
            fname = request.template_name[len("/storage/"):].split("?")[0]
            local_path = os.path.join(STORAGE_DIR, fname)
        else:
            fname = request.template_name[len("/static/temp/"):].split("?")[0]
            local_path = os.path.join("static", "temp", fname)
        if os.path.exists(local_path):
            logger.info(f"📂 Cargando imagen local temporal: {local_path}")
            image = Image.open(local_path).convert("RGBA")
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"La imagen temporal '{fname}' ya no está disponible (el servidor se reinició). "
                    "Por favor vuelve a subir la imagen base en el editor."
                ),
            )
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

    # Ajustes de imagen: brillo / contraste / saturación
    if request.img_brightness != 100.0:
        image = ImageEnhance.Brightness(image).enhance(request.img_brightness / 100.0)
    if request.img_contrast != 100.0:
        image = ImageEnhance.Contrast(image).enhance(request.img_contrast / 100.0)
    if request.img_saturation != 100.0:
        image = ImageEnhance.Color(image).enhance(request.img_saturation / 100.0)

    # Viñeta
    if request.vignette_enabled:
        sides = request.vignette_sides or ["top", "right", "bottom", "left"]
        logger.info(f"🎞️ Viñeta: color={request.vignette_color} op={request.vignette_opacity} size={request.vignette_size}")
        image = apply_vignette(image, color=request.vignette_color, opacity=request.vignette_opacity,
                               size=request.vignette_size, sides=sides, tone=request.vignette_filter)

    # Sustituir variables {varname} y {{varname}} (formato ManyChat).
    # Variables de fecha reservadas ({{fecha_actual}}, etc.) se resuelven solas
    # en cada render; las del usuario tienen prioridad.
    from fecha_utils import build_date_vars
    merged_vars = {**build_date_vars(), **(request.vars or {})}
    sorted_keys = sorted(merged_vars.keys(), key=len, reverse=True)
    for text_field in request.texts:
        for key in sorted_keys:
            text_field.text = text_field.text.replace(f"{{{{{key}}}}}", merged_vars[key])
            text_field.text = text_field.text.replace(f"{{{key}}}", merged_vars[key])

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
        if getattr(text_field, 'visible', True) is False:
            continue
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

    # Overlays (logos, stickers, badges, mapas)
    for ov in (request.overlays or []):
        try:
            map_location = getattr(ov, 'map_location', None)
            if map_location:
                ov_img = _fetch_mapbox_tile(
                    location=map_location,
                    zoom=getattr(ov, 'map_zoom', 13),
                    style=getattr(ov, 'map_style', 'streets-v12'),
                    width=max(1, ov.width),
                    height=max(1, ov.height),
                    marker=getattr(ov, 'map_marker', True),
                    vars_dict=getattr(request, 'vars', None) or {},
                    from_location=getattr(ov, 'map_from_location', None) or None,
                )
            elif ov.src.startswith("data:"):
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

    # Watermark logo
    if request.watermark:
        image = _apply_wm_logo(
            image,
            corner=getattr(request, "wm_corner", "br"),
            size_px=getattr(request, "wm_size", 22),
            opacity_pct=getattr(request, "wm_opacity", 55),
            color_hex=getattr(request, "wm_color", "#ffffff"),
            pill=getattr(request, "wm_pill", True),
        )

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
_RENDER_JOBS: dict = {}          # job_id → {status, result, error, created_at}
_RENDER_EXECUTOR = _futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="tof-render")

def _rj_update(job_id, pct: int, msg: str) -> None:
    """Actualiza el progreso de un render job en _RENDER_JOBS (no falla si no existe)."""
    if job_id and job_id in _RENDER_JOBS:
        _RENDER_JOBS[job_id].update({"progress": pct, "progress_msg": msg})


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


