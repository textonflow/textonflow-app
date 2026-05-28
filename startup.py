"""
startup.py — Tareas que se ejecutan una vez al arrancar el servidor:
  1. _minify_static_js(): minifica app.js con rjsmin si está disponible.

Los archivos estáticos y routers viven directamente en el repo de GitHub
y Railway los despliega desde allí. No se requiere descarga externa.
"""
import logging
import os

try:
    import rjsmin as _rjsmin
    _RJSMIN_OK = True
except ImportError:
    _RJSMIN_OK = False

logger = logging.getLogger(__name__)


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
    _minify_static_js()
