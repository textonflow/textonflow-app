"""
cleanup.py — Limpieza automática de Supabase Storage.

Borra los renders generados (gen_*.jpg) con más de N días de antigüedad para
que el plan gratuito de Supabase (1 GB de Storage) no se llene nunca.

- SOLO borra archivos gen_* (imágenes ya enviadas a ManyChat; se necesitan
  minutos, no semanas). Las imágenes base subidas por el usuario (upload_*)
  NO se tocan: pueden estar referenciadas por templates guardados.
- Retención configurable con RENDER_RETENTION_DAYS (default: 3 días).
- Corre en un hilo de fondo una vez al arrancar y luego cada 24 h.
- Solo activo en producción (RAILWAY_ENVIRONMENT) o con ENABLE_STORAGE_CLEANUP=1.
"""
import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("textonflow")

RETENTION_DAYS = int(os.getenv("RENDER_RETENTION_DAYS", "3"))
_DELETE_BATCH  = 100
_LIST_PAGE     = 1000
_MAX_PAGES     = 50          # tope de seguridad: 50k objetos por corrida


def _sb_conf():
    from routers.render_helpers import _SB_URL, _SB_BUCKET, _sb_key
    return _SB_URL, _SB_BUCKET, _sb_key()


def _sb_request(method: str, url: str, key: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "null")


def run_storage_cleanup() -> dict:
    """Borra renders gen_* más viejos que RETENTION_DAYS. Devuelve resumen."""
    sb_url, bucket, key = _sb_conf()
    if not key:
        return {"ok": False, "error": "sin SUPABASE_SERVICE_ROLE_KEY"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    to_delete, kept, offset = [], 0, 0
    for _ in range(_MAX_PAGES):
        page = _sb_request("POST", f"{sb_url}/storage/v1/object/list/{bucket}", key, {
            "prefix": "", "limit": _LIST_PAGE, "offset": offset,
            "sortBy": {"column": "created_at", "order": "asc"},
        })
        if not page:
            break
        for obj in page:
            name = obj.get("name", "")
            created = obj.get("created_at") or ""
            try:
                ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                kept += 1
                continue
            if name.startswith("gen_") and ts < cutoff:
                to_delete.append(name)
            else:
                kept += 1
        if len(page) < _LIST_PAGE:
            break
        offset += _LIST_PAGE

    deleted = 0
    for i in range(0, len(to_delete), _DELETE_BATCH):
        batch = to_delete[i:i + _DELETE_BATCH]
        try:
            _sb_request("DELETE", f"{sb_url}/storage/v1/object/{bucket}", key,
                        {"prefixes": batch})
            deleted += len(batch)
        except Exception as e:
            logger.warning(f"🧹 Storage cleanup: fallo borrando lote: {e}")
    logger.info(f"🧹 Storage cleanup: {deleted} renders gen_* borrados "
                f"(>{RETENTION_DAYS} días), {kept} archivos conservados")
    return {"ok": True, "deleted": deleted, "kept": kept,
            "retention_days": RETENTION_DAYS}


def _loop():
    time.sleep(60)  # dejar que el arranque termine
    while True:
        try:
            run_storage_cleanup()
        except Exception as e:
            logger.warning(f"🧹 Storage cleanup error: {e}")
        time.sleep(24 * 3600)


def start_storage_cleanup_scheduler() -> None:
    """Arranca la limpieza diaria en un hilo de fondo (solo en producción)."""
    enabled = bool(os.getenv("RAILWAY_ENVIRONMENT")) or os.getenv("ENABLE_STORAGE_CLEANUP") == "1"
    if not enabled:
        logger.info("cleanup: limpieza de Storage desactivada (no es producción)")
        return
    threading.Thread(target=_loop, daemon=True, name="storage-cleanup").start()
    logger.info(f"🧹 Limpieza de Storage activada: renders gen_* >{RETENTION_DAYS} días, cada 24 h")
