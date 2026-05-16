"""
startup.py — Arranque limpio de TextOnFlow.
Los archivos estáticos se sirven directamente del build (no se descargan).
"""
import logging
import os

logger = logging.getLogger(__name__)


def run_startup():
    """Prepara directorios necesarios al arrancar."""
    dirs = ["static", "static/temp", "fonts", "output", "routers"]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    logger.info("✅ TextOnFlow iniciado — archivos servidos desde el build")
