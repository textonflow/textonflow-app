import logging
import os
import requests
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from auth import _is_superadmin, _get_client_ip, _check_rate_limit, PLAN_LIMITS
from database import SUPABASE_DATABASE_URL, get_db
from fonts import get_noto_emoji_font
from stats import _read_stats

try:
    import numpy as _np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

try:
    import psycopg2 as _psycopg2
    _PSYCOPG2_OK = True
except ImportError:
    _PSYCOPG2_OK = False

logger = logging.getLogger(__name__)

pages_router = APIRouter()


def _reset_time_str() -> str:
    """Tiempo hasta medianoche UTC en formato 'Xh Ym'."""
    now      = datetime.utcnow()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    secs     = int((midnight - now).total_seconds())
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


# ─── Página raíz y dashboard ─────────────────────────────────────────────────

@pages_router.get("/")
async def root():
    return FileResponse("index.html", media_type="text/html")

@pages_router.get("/dashboard")
async def dashboard():
    return FileResponse("static/dashboard.html", media_type="text/html")


# ─── Status y health ─────────────────────────────────────────────────────────

@pages_router.get("/status")
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


@pages_router.get("/health")
async def health():
    """Health check rápido — solo verifica que la app esté viva."""
    db_ok  = False
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


# ─── API pública: stats y usage ───────────────────────────────────────────────

@pages_router.get("/api/stats")
async def get_stats():
    """Devuelve estadísticas públicas de uso de TextOnFlow."""
    data = _read_stats()
    return {
        "images_generated": data.get("images_generated", 0),
    }

@pages_router.get("/api/usage")
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


# ─── Páginas legales y de ayuda ───────────────────────────────────────────────

@pages_router.get("/manual")
async def manual_page():
    return FileResponse("static/manual.html", media_type="text/html")

@pages_router.get("/privacidad")
async def privacidad_page():
    return FileResponse("static/privacidad.html", media_type="text/html")

@pages_router.get("/terminos")
async def terminos_page():
    return FileResponse("static/terminos.html", media_type="text/html")

@pages_router.get("/docs")
async def docs_page():
    return FileResponse("static/docs.html", media_type="text/html")

@pages_router.get("/faq")
async def faq_page():
    return FileResponse("static/faq.html", media_type="text/html")

@pages_router.get("/precios")
async def precios_page():
    return FileResponse("static/precios.html", media_type="text/html")

@pages_router.get("/casos")
async def casos_page():
    return FileResponse("static/casos.html", media_type="text/html")


# ─── Archivos de sistema ──────────────────────────────────────────────────────

@pages_router.get("/.well-known/sg-hosted-ping")
async def sg_ping():
    return Response(content="OK", media_type="text/plain")

@pages_router.get("/robots.txt")
async def robots():
    content = """User-agent: *
Allow: /
Sitemap: https://www.textonflow.com/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")

@pages_router.get("/sitemap.xml")
async def sitemap():
    base  = "https://www.textonflow.com"
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

@pages_router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join("static", "favicon.png")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/png")
    return Response(status_code=204)


# ─── Proxy de imágenes (evita restricciones CORS del navegador) ───────────────

@pages_router.get("/proxy-image")
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


# ─── Páginas de administración ────────────────────────────────────────────────

@pages_router.get("/admin-panel", include_in_schema=False)
async def admin_panel_page():
    """Panel de administración con gestión visual de usuarios."""
    panel_path = os.path.join("static", "admin-panel.html")
    if os.path.exists(panel_path):
        return FileResponse(panel_path, media_type="text/html")
    raise HTTPException(status_code=404, detail="Panel no encontrado.")

@pages_router.get("/superadministrador", include_in_schema=False)
async def superadmin_page():
    """Ruta secreta que sirve la app con flag para abrir el login admin."""
    html_path = "index.html"
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    inject = "<script>window._OPEN_SA_ON_LOAD=true;</script>"
    content = content.replace("</body>", inject + "</body>", 1)
    return HTMLResponse(content=content)
