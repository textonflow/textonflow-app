"""
monitoring.py — Chequeo diario de salud de TextOnFlow.

Revisa lo crítico para que las imágenes lleguen a ManyChat:
  1) Base de datos (Supabase Postgres)
  2) Almacenamiento de imágenes (Supabase Storage)
  3) Motor de imágenes (PIL + fuentes)
  4) La app responde en su dominio público

Envía un correo:
  - En cada arranque: SOLO si algo falla (avisa al instante si un deploy queda roto).
  - Una vez al día (09:00 America/Mexico_City): SIEMPRE, con el resultado del checklist.

Se activa solo en producción (Railway) o con ENABLE_HEALTHCHECK=1.
"""
import io
import os
import threading
import time
import logging
from datetime import datetime, timedelta, timezone

import requests
from PIL import Image, ImageDraw, ImageFont

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

logger = logging.getLogger("textonflow")

PUBLIC_URL    = os.getenv("PUBLIC_URL",   "https://www.textonflow.com").rstrip("/")
SUPABASE_URL  = os.getenv("SUPABASE_URL", "https://dluzcrfqqieprudfeuyk.supabase.co").rstrip("/")
SB_BUCKET     = os.getenv("SUPABASE_STORAGE_BUCKET", "textonflow-uploads")
ALERT_EMAIL   = os.getenv("ALERT_EMAIL", "hola@textonflow.com")
EM_KEY        = os.getenv("ENGINEMAILER_API_KEY", "")
try:
    CHECK_HOUR = int(os.getenv("HEALTHCHECK_HOUR", "9"))   # hora local del reporte diario
except (ValueError, TypeError):
    CHECK_HOUR = 9


# ─── Checks individuales ──────────────────────────────────────────────────────

def _check_db() -> dict:
    try:
        from database import get_db
        conn = get_db()
        if not conn:
            return {"name": "Base de datos", "ok": False, "detail": "Sin conexión"}
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"name": "Base de datos", "ok": True, "detail": "Responde correctamente"}
    except Exception as e:
        return {"name": "Base de datos", "ok": False, "detail": str(e)[:160]}


def _check_storage() -> dict:
    """Confirma que Supabase Storage está accesible (donde viven las imágenes)."""
    url = f"{SUPABASE_URL}/storage/v1/object/public/{SB_BUCKET}/__healthcheck_probe__.txt"
    try:
        r = requests.get(url, timeout=15)
        # 200/400/404 = el servicio respondió (el archivo no existe, pero está vivo).
        if r.status_code < 500:
            return {"name": "Almacenamiento de imágenes (Supabase)", "ok": True,
                    "detail": f"Accesible (HTTP {r.status_code})"}
        return {"name": "Almacenamiento de imágenes (Supabase)", "ok": False,
                "detail": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"name": "Almacenamiento de imágenes (Supabase)", "ok": False, "detail": str(e)[:160]}


def _check_render_engine() -> dict:
    """Renderiza una imagen de prueba en memoria (sin guardar ni contar render)."""
    try:
        img = Image.new("RGB", (240, 90), (124, 110, 255))
        draw = ImageDraw.Draw(img)
        font_path = os.path.join("fonts", "LiberationSans-Regular.ttf")
        try:
            font = ImageFont.truetype(font_path, 28)
        except Exception:
            font = ImageFont.load_default()
        draw.text((12, 30), "TextOnFlow OK", fill=(255, 255, 255), font=font)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        size = buf.tell()
        if size <= 0:
            return {"name": "Motor de imágenes", "ok": False, "detail": "Render vacío"}
        return {"name": "Motor de imágenes", "ok": True, "detail": f"Render de prueba OK ({size} bytes)"}
    except Exception as e:
        return {"name": "Motor de imágenes", "ok": False, "detail": str(e)[:160]}


def _check_public_app() -> dict:
    """Confirma que la app responde en su dominio público."""
    try:
        r = requests.get(f"{PUBLIC_URL}/health", timeout=25)
        if r.status_code == 200:
            return {"name": "App en línea (dominio público)", "ok": True,
                    "detail": f"{PUBLIC_URL} responde (HTTP 200)"}
        return {"name": "App en línea (dominio público)", "ok": False,
                "detail": f"{PUBLIC_URL} → HTTP {r.status_code}"}
    except Exception as e:
        return {"name": "App en línea (dominio público)", "ok": False, "detail": str(e)[:160]}


def run_healthcheck() -> list:
    """Ejecuta todos los chequeos y devuelve la lista de resultados."""
    return [
        _check_db(),
        _check_storage(),
        _check_render_engine(),
        _check_public_app(),
    ]


# ─── Email ────────────────────────────────────────────────────────────────────

def send_health_email(results: list, reason: str = "diario") -> bool:
    """Envía el reporte del checklist por correo vía EngineMailer."""
    if not EM_KEY:
        logger.info("monitoring: sin ENGINEMAILER_API_KEY, no se envía correo")
        return False

    all_ok = all(r["ok"] for r in results)
    icon   = "🟢" if all_ok else "🔴"
    estado = "TODO BIEN" if all_ok else "HAY UN PROBLEMA"
    fecha  = datetime.now(_tz()).strftime("%d/%m/%Y %H:%M")

    filas = "".join(
        f"<tr>"
        f"<td style='padding:8px 12px;font-size:20px'>{'✅' if r['ok'] else '❌'}</td>"
        f"<td style='padding:8px 12px;font-weight:600'>{r['name']}</td>"
        f"<td style='padding:8px 12px;color:#666;font-size:13px'>{r['detail']}</td>"
        f"</tr>"
        for r in results
    )

    aviso = "" if all_ok else (
        "<p style='background:#fff3f3;border:1px solid #ffd0d0;border-radius:8px;"
        "padding:12px 16px;color:#c0392b'><strong>Acción sugerida:</strong> vuelve a desplegar desde tu Mac "
        "(<code>cd ~/textonflow-deploy &amp;&amp; git pull &amp;&amp; railway up</code>) y revisa "
        "<code>railway logs</code>.</p>"
    )

    body = {
        "CampaignName": "TextOnFlow Healthcheck",
        "ToEmail": ALERT_EMAIL,
        "SenderEmail": "hola@textonflow.com",
        "SenderName": "TextOnFlow Monitor",
        "Subject": f"{icon} TextOnFlow — chequeo {reason}: {estado}",
        "SubmittedContent": (
            f"<h2 style='margin:0 0 4px'>{icon} {estado}</h2>"
            f"<p style='color:#888;margin:0 0 16px'>Chequeo {reason} · {fecha}</p>"
            f"<table style='border-collapse:collapse;width:100%;max-width:560px'>{filas}</table>"
            f"<div style='margin-top:16px'>{aviso}</div>"
            f"<hr style='margin:24px 0;border:none;border-top:1px solid #eee'>"
            f"<p style='font-size:12px;color:#aaa'>Revisión automática de TextOnFlow. "
            f"Para ver el detalle en cualquier momento: <a href='{PUBLIC_URL}/health/full'>{PUBLIC_URL}/health/full</a></p>"
        ),
    }
    try:
        resp = requests.post(
            "https://api.enginemailer.com/RESTAPI/V2/Submission/SendEmail",
            headers={"APIKey": EM_KEY, "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info(f"📧 Reporte de salud enviado a {ALERT_EMAIL} ({reason}, {'OK' if all_ok else 'FALLO'})")
            return True
        logger.warning(f"EngineMailer healthcheck {resp.status_code}: {resp.text[:120]}")
        return False
    except Exception as e:
        logger.warning(f"Error enviando reporte de salud: {e}")
        return False


# ─── Programador (scheduler) ──────────────────────────────────────────────────

def _tz():
    if ZoneInfo:
        try:
            return ZoneInfo("America/Mexico_City")
        except Exception:
            pass
    return timezone.utc


def _seconds_until(hour: int) -> float:
    now = datetime.now(_tz())
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _today_key(prefix: str) -> str:
    return f"{prefix}-{datetime.now(_tz()).strftime('%Y-%m-%d')}"


def _claim_send(key: str) -> bool:
    """True solo para el PRIMER proceso que reclama esta clave hoy.

    Evita correos duplicados si Railway corre varios workers/réplicas. Si no hay
    BD, devuelve True (preferimos avisar a quedarnos callados).
    """
    try:
        from database import get_db
        conn = get_db()
        if not conn:
            return True
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS healthcheck_sent "
                "(k TEXT PRIMARY KEY, sent_at TIMESTAMPTZ DEFAULT now())"
            )
            cur.execute(
                "INSERT INTO healthcheck_sent (k) VALUES (%s) ON CONFLICT (k) DO NOTHING",
                (key,),
            )
            return cur.rowcount == 1
    except Exception as e:
        logger.warning(f"healthcheck dedup error: {e}")
        return True


def _loop():
    # Chequeo de arranque: avisa SOLO si algo falla (deploy roto → correo inmediato).
    # Reintenta una vez tras 30s para no dar falsas alarmas por calentamiento.
    time.sleep(25)
    try:
        results = run_healthcheck()
        if any(not r["ok"] for r in results):
            time.sleep(30)
            results = run_healthcheck()
            failed = [r for r in results if not r["ok"]]
            if failed and _claim_send(_today_key("startup")):
                logger.warning(f"⚠️ Healthcheck de arranque con fallos: {[r['name'] for r in failed]}")
                send_health_email(results, reason="de arranque")
            elif failed:
                logger.info("healthcheck arranque: fallo ya notificado hoy por otro proceso")
            else:
                logger.info("✅ Healthcheck de arranque: se recuperó tras el reintento")
        else:
            logger.info("✅ Healthcheck de arranque: todo bien")
    except Exception as e:
        logger.warning(f"Healthcheck de arranque error: {e}")

    # Reporte diario: SIEMPRE (deduplicado para que llegue UN solo correo al día).
    while True:
        try:
            time.sleep(_seconds_until(CHECK_HOUR))
            results = run_healthcheck()
            if _claim_send(_today_key("daily")):
                send_health_email(results, reason="diario")
            else:
                logger.info("healthcheck diario: ya enviado hoy por otro proceso")
        except Exception as e:
            logger.warning(f"Healthcheck diario error: {e}")
            time.sleep(3600)  # reintenta en 1h si algo raro pasa


def start_health_scheduler() -> None:
    """Arranca el chequeo diario en un hilo de fondo (solo en producción)."""
    enabled = bool(os.getenv("RAILWAY_ENVIRONMENT")) or os.getenv("ENABLE_HEALTHCHECK") == "1"
    if not enabled:
        logger.info("monitoring: chequeo diario desactivado (no es producción)")
        return
    if not EM_KEY:
        logger.info("monitoring: sin ENGINEMAILER_API_KEY, chequeo diario desactivado")
        return
    threading.Thread(target=_loop, daemon=True, name="tof-healthcheck").start()
    logger.info(f"✅ Chequeo diario de salud activado → {ALERT_EMAIL} (09:00 America/Mexico_City)")
