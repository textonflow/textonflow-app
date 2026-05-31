import logging
import os
import requests
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from auth import _is_superadmin, _get_client_ip, _check_rate_limit
from database import SUPABASE_DATABASE_URL, get_db, _PSYCOPG2_OK
from fonts import get_noto_emoji_font
from stats import _read_stats

try:
    import numpy as _np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

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


@pages_router.get("/integraciones")
async def integraciones_page():
    return FileResponse("static/integraciones.html", media_type="text/html")


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

_PROXY_MAX_BYTES = 25 * 1024 * 1024  # 25 MB — evita OOM por archivos enormes

def _is_safe_public_url(target: str) -> bool:
    """True solo si target es http(s) y resuelve a una IP pública.
    Bloquea loopback/privadas/link-local/reservadas para evitar SSRF."""
    import ipaddress as _ip
    import socket as _socket
    from urllib.parse import urlparse as _urlparse
    try:
        p = _urlparse(target)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = _socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            addr = _ip.ip_address(ip_str)
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True

@pages_router.get("/proxy-image")
def proxy_image(url: str):
    import re as _re

    # ── Cortocircuito: si la URL apunta a nuestro propio /storage/ o /static/temp/
    # leer directamente del disco en lugar de hacer HTTP circular a nosotros mismos
    _STORAGE_DIR = os.getenv("STORAGE_PATH", os.path.join("static", "temp"))
    _self_pat = _re.compile(r"https?://(?:www\.)?textonflow\.com(?::\d+)?/storage/(.+?)(?:\?.*)?$")
    _temp_pat = _re.compile(r"https?://(?:www\.)?textonflow\.com(?::\d+)?/static/temp/(.+?)(?:\?.*)?$")
    m = _self_pat.match(url) or _temp_pat.match(url)
    if m:
        fname = m.group(1).lstrip("/")
        # Primero busca en STORAGE_DIR, luego en static/temp.
        # IMPORTANTE: resolvemos la ruta real y exigimos que quede DENTRO del
        # directorio base — si no, un payload con "../" permitiría leer archivos
        # arbitrarios del servidor (path traversal / file disclosure).
        candidates = []
        for base in (_STORAGE_DIR, os.path.join("static", "temp")):
            base_real = os.path.realpath(base)
            fpath = os.path.realpath(os.path.join(base, fname))
            if fpath == base_real or fpath.startswith(base_real + os.sep):
                candidates.append(fpath)
        for fpath in candidates:
            if os.path.exists(fpath):
                ext = fname.rsplit(".", 1)[-1].lower()
                mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                        "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/png")
                with open(fpath, "rb") as fh:
                    return Response(content=fh.read(), media_type=mime,
                                    headers={"Cache-Control": "public, max-age=3600"})
        # Archivo ya no existe localmente (redeploy borró el storage efímero)
        raise HTTPException(
            status_code=400,
            detail="La imagen ya no está disponible (el servidor se reinició y borró el storage temporal). "
                   "Por favor re-sube la imagen base en el editor."
        )

    # ── URL externa: descarga con protección SSRF + límite de tamaño ──────────
    from urllib.parse import urljoin as _urljoin
    try:
        cur_url = url
        resp = None
        # Seguimos redirects manualmente, validando cada salto (un redirect a una
        # IP interna podría saltarse la validación si dejáramos requests seguirlos)
        for _ in range(5):
            if not _is_safe_public_url(cur_url):
                raise HTTPException(status_code=400, detail="URL no permitida.")
            resp = requests.get(
                cur_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; TextOnFlow/1.0)"},
                timeout=15,
                allow_redirects=False,
                stream=True,
            )
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                nxt = _urljoin(cur_url, resp.headers["Location"])
                resp.close()
                cur_url = nxt
                continue
            break
        else:
            raise HTTPException(status_code=400, detail="Demasiadas redirecciones.")

        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        chunks, total = [], 0
        for chunk in resp.iter_content(8192):
            if not chunk:
                continue
            total += len(chunk)
            if total > _PROXY_MAX_BYTES:
                resp.close()
                raise HTTPException(status_code=400, detail="La imagen es demasiado grande (máx. 25 MB).")
            chunks.append(chunk)
        resp.close()
        return Response(
            content=b"".join(chunks),
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No se pudo cargar la imagen: {e}")


# ─── Endpoint de descarga de estáticos (usado por Railway en startup) ─────────

_DOWNLOAD_FILES = {
    # ── JS / Frontend ─────────────────────────────────────────────────────────
    "app.js":         "static/app.js",
    # ── HTML ──────────────────────────────────────────────────────────────────
    "index.html":       "index.html",
    "manual.html":      "static/manual.html",
    "privacidad.html":  "static/privacidad.html",
    "terminos.html":    "static/terminos.html",
    "faq.html":         "static/faq.html",
    "docs.html":        "static/docs.html",
    "precios.html":     "static/precios.html",
    "casos.html":       "static/casos.html",
    # ── CSS ───────────────────────────────────────────────────────────────────
    "base.css":         "static/base.css",
    "layout.css":       "static/layout.css",
    "components.css":   "static/components.css",
    "editor.css":       "static/editor.css",
    # ── Assets ────────────────────────────────────────────────────────────────
    "favicon.png":           "static/favicon.png",
    "logo-blanco.webp":      "static/logo-blanco.webp",
    "logo-negro.webp":       "static/logo-negro.webp",
    "logo-negro-new.png":    "static/logo-negro-new.png",
    "logo-blanco-new.png":   "static/logo-blanco-new.png",
    "previews/biblica.jpg":  "static/previews/biblica.jpg",
    "previews/plumilla.jpg": "static/previews/plumilla.jpg",
    # NOTA: los routers Python (users.py, render.py, ai.py, etc.) NO se exponen
    # aquí — eran descargables sin autenticación, filtrando el código del backend.
    # El despliegue toma los archivos desde el repo de GitHub, no desde este endpoint.
}

@pages_router.get("/api/download")
async def download_index():
    return {"files": list(_DOWNLOAD_FILES.keys()), "status": "ok"}

@pages_router.get("/api/download/{filepath:path}")
async def download_static(filepath: str):
    """Sirve archivos estáticos para que Railway los descargue en startup."""
    local = _DOWNLOAD_FILES.get(filepath)
    if not local or not os.path.exists(local):
        raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {filepath}")
    return FileResponse(local)


# ─── Páginas de administración ────────────────────────────────────────────────

@pages_router.get("/batch", include_in_schema=False)
async def batch_page():
    """Generador masivo desde Google Sheets / CSV."""
    path = os.path.join("static", "batch.html")
    if os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    raise HTTPException(status_code=404, detail="Página no encontrada")

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
