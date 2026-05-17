"""
startup.py — Tareas que se ejecutan una vez al arrancar el servidor:
  1. _auto_update_statics(): descarga app.js, styles.css e index.html
     desde Replit hacia Railway (para mantener el frontend actualizado).
  2. _minify_static_js(): minifica app.js con rjsmin si está disponible.

Importado en main.py con: from startup import run_startup; run_startup()
"""
import logging
import os

import requests

try:
    import rjsmin as _rjsmin
    _RJSMIN_OK = True
except ImportError:
    _RJSMIN_OK = False

logger = logging.getLogger(__name__)


def _is_html_error_page(content: bytes, dest: str) -> bool:
    """Devuelve True si el contenido parece una página HTML de error (ej: Replit sleep page).
    
    Previene sobreescribir CSS/JS con HTML cuando Replit está caído o en modo sleep.
    Solo aplica a archivos .css y .js — los .html son descargados sin restricción.
    """
    if dest.endswith('.html'):
        return False
    stripped = content.lstrip()[:20].lower()
    return stripped.startswith(b'<html') or stripped.startswith(b'<!doc')


# ─── URL base desde donde Railway descarga los estáticos de Replit ─────────────
_UPDATE_BASE = os.getenv(
    "TEXTONFLOW_UPDATE_URL",
    "https://a957156e-d374-4132-9cee-a0afec9e64e1-00-2u2btyprd2joh.riker.replit.dev/api/download"
)


def _auto_update_statics() -> None:
    """Descarga los archivos estáticos actualizados desde Replit al arrancar.

    Para desactivar: TEXTONFLOW_AUTO_UPDATE=false en las vars de Railway.
    """
    if os.getenv("TEXTONFLOW_AUTO_UPDATE", "true").lower() == "false":
        logger.info("⏭️  Auto-update desactivado (TEXTONFLOW_AUTO_UPDATE=false)")
        return

    files = [
        # ── JS / Frontend ─────────────────────────────────────────────────────
        (_UPDATE_BASE + "/app.js",                "static/app.js"),
        (_UPDATE_BASE + "/feedback.js",           "static/feedback.js"),
        (_UPDATE_BASE + "/auth.js",               "static/auth.js"),
        (_UPDATE_BASE + "/projects.js",           "static/projects.js"),
        (_UPDATE_BASE + "/ai-panel.js",           "static/ai-panel.js"),
        (_UPDATE_BASE + "/cd-help.js",            "static/cd-help.js"),
        (_UPDATE_BASE + "/shortcuts.js",          "static/shortcuts.js"),
        # ── HTML ──────────────────────────────────────────────────────────────
        (_UPDATE_BASE + "/index.html",            "index.html"),
        (_UPDATE_BASE + "/manual.html",           "static/manual.html"),
        (_UPDATE_BASE + "/privacidad.html",       "static/privacidad.html"),
        (_UPDATE_BASE + "/terminos.html",         "static/terminos.html"),
        (_UPDATE_BASE + "/faq.html",              "static/faq.html"),
        (_UPDATE_BASE + "/docs.html",             "static/docs.html"),
        (_UPDATE_BASE + "/precios.html",          "static/precios.html"),
        (_UPDATE_BASE + "/casos.html",            "static/casos.html"),
        # ── CSS ───────────────────────────────────────────────────────────────
        (_UPDATE_BASE + "/base.css",              "static/base.css"),
        (_UPDATE_BASE + "/layout.css",            "static/layout.css"),
        (_UPDATE_BASE + "/components.css",        "static/components.css"),
        (_UPDATE_BASE + "/editor.css",            "static/editor.css"),
        # ── Assets ────────────────────────────────────────────────────────────
        (_UPDATE_BASE + "/favicon.png",           "static/favicon.png"),
        (_UPDATE_BASE + "/logo-blanco.webp",      "static/logo-blanco.webp"),
        (_UPDATE_BASE + "/logo-negro.webp",       "static/logo-negro.webp"),
        (_UPDATE_BASE + "/logo-negro-new.png",    "static/logo-negro-new.png"),
        (_UPDATE_BASE + "/logo-blanco-new.png",   "static/logo-blanco-new.png"),
        (_UPDATE_BASE + "/previews/biblica.jpg",  "static/previews/biblica.jpg"),
        (_UPDATE_BASE + "/previews/plumilla.jpg", "static/previews/plumilla.jpg"),
        # ── Python routers ────────────────────────────────────────────────────
        (_UPDATE_BASE + "/users.py",              "routers/users.py"),
        (_UPDATE_BASE + "/render.py",             "routers/render.py"),
        (_UPDATE_BASE + "/render_helpers.py",     "routers/render_helpers.py"),
        (_UPDATE_BASE + "/mc.py",                 "routers/mc.py"),
        (_UPDATE_BASE + "/ai.py",                 "routers/ai.py"),
        (_UPDATE_BASE + "/batch.py",              "routers/batch.py"),
    ]

    for url, dest in files:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200 and len(r.content) > 100:
                # Validar que CSS/JS no sea una página HTML de error (ej: Replit sleep page)
                if _is_html_error_page(r.content, dest):
                    logger.warning(f"⚠️  Auto-update abortado — respuesta HTML en lugar de {dest}")
                    continue
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(r.content)
                logger.info(f"✅ Auto-updated: {dest}")
            else:
                logger.warning(f"⚠️  Auto-update fallido ({r.status_code}): {url}")
        except Exception as e:
            logger.warning(f"⚠️  Auto-update error {dest}: {e}")


def _minify_static_js() -> None:
    """Minifica static/app.js con rjsmin si está disponible."""
    if not _RJSMIN_OK:
        logger.warning("⚠️  rjsmin no disponible — app.js se sirve sin minificar")
        return
    js_path = "static/app.js"
    if not os.path.exists(js_path):
        return
    try:
        with open(js_path, "r", encoding="utf-8") as f:
            original = f.read()
        minified  = _rjsmin.jsmin(original, keep_bang_comments=False)
        reduction = (1 - len(minified) / max(len(original), 1)) * 100
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(minified)
        logger.info(
            f"✅ app.js minificado — {len(original)//1024}KB → "
            f"{len(minified)//1024}KB ({reduction:.1f}% reducción)"
        )
    except Exception as e:
        logger.warning(f"⚠️  Minificación JS fallida: {e}")


def run_startup() -> None:
    """Punto de entrada único: ejecuta todas las tareas de arranque en orden."""
    _auto_update_statics()
    _minify_static_js()
