"""
stats.py — Contador global de imágenes generadas (compartido entre main y render router).
"""
import json
import logging
import os
import threading

logger = logging.getLogger("textonflow")

_STORAGE_DIR = os.getenv("STORAGE_PATH", os.path.join("static", "temp"))
_STATS_FILE  = os.path.join(_STORAGE_DIR, "tof_stats.json")
_STATS_LOCK  = threading.Lock()


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
