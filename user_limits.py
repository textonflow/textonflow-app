"""
user_limits.py — Constantes de planes, helpers de autenticación y límites de render por usuario.
Importado por main.py. Depende de database.py y auth.py (sin circularidad).
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import Request, HTTPException

from database import get_db
from auth import decode_jwt

logger = logging.getLogger("textonflow")

# ─── Límites por plan ─────────────────────────────────────────────────────────
USER_PLAN_LIMITS = {
    "trial":   99999,   # ilimitado — la restricción es de 7 días desde registro
    "starter": 1000,
    "agency":  10000,
    "admin":   999999,
}
TRIAL_DAYS = 7   # duración del trial en días
JSON_EXPORT_PLANS = {"starter", "agency", "admin"}  # planes que permiten export JSON

# ─── Auth helpers ─────────────────────────────────────────────────────────────
def _get_current_user(request: Request) -> Optional[dict]:
    """Lee el JWT del header Authorization: Bearer <token> y devuelve el payload."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    return decode_jwt(token)

def _require_user(request: Request) -> dict:
    user = _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido o expirado.")
    return user

# ─── Helpers de rate limit por usuario (Phase 3) ─────────────────────────────
def _get_user_profile(user_id: str) -> dict:
    """Devuelve {plan, renders_used, renders_limit, watermark_exempt, json_exports_used, is_active}."""
    conn = get_db()
    if not conn:
        return {"plan": "unknown", "renders_used": 0, "renders_limit": 999999,
                "watermark_exempt": False, "json_exports_used": 0, "is_active": True}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT plan, renders_used, renders_limit, watermark_exempt, json_exports_used, is_active "
                "FROM users WHERE id = %s", (user_id,)
            )
            row = cur.fetchone()
        return dict(row) if row else {"plan": "unknown", "renders_used": 0, "renders_limit": 999999,
                                      "watermark_exempt": False, "json_exports_used": 0, "is_active": True}
    except Exception as e:
        logger.error(f"Error en _get_user_profile: {e}")
        return {"plan": "unknown", "renders_used": 0, "renders_limit": 999999,
                "watermark_exempt": False, "json_exports_used": 0, "is_active": True}

def _should_apply_watermark(user_id: Optional[str]) -> bool:
    """True si el render debe llevar sello TextOnFlow (plan trial sin exención)."""
    if not user_id:
        return True
    profile = _get_user_profile(user_id)
    if profile["plan"] in JSON_EXPORT_PLANS or profile["plan"] == "admin":
        return False
    return not profile.get("watermark_exempt", False)

def _check_user_render_limit(user_id: str) -> tuple:
    """(used, limit, exceeded, plan) — lee desde BD.
    Para plan trial: el límite es temporal (TRIAL_DAYS días desde created_at),
    no de conteo. Si el trial expiró, exceeded=True independientemente del conteo.
    """
    conn = get_db()
    if not conn:
        return 0, 999999, False, "unknown"
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT plan, renders_used, renders_limit, created_at FROM users WHERE id = %s",
                (user_id,)
            )
            row = cur.fetchone()
        if not row:
            return 0, 999999, False, "unknown"
        plan = row["plan"]
        used = row["renders_used"]

        if plan == "trial":
            if row["created_at"]:
                created = row["created_at"]
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                elapsed_days = (datetime.now(timezone.utc) - created).days
                expired = elapsed_days >= TRIAL_DAYS
            else:
                expired = False
            limit = USER_PLAN_LIMITS["trial"]
            return used, limit, expired, plan

        limit = USER_PLAN_LIMITS.get(plan, row["renders_limit"])
        return used, limit, used >= limit, plan
    except Exception as e:
        logger.error(f"Error en _check_user_render_limit: {e}")
        return 0, 999999, False, "unknown"

def _increment_user_renders(user_id: str) -> None:
    """Incrementa renders_used del usuario en BD."""
    conn = get_db()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET renders_used = renders_used + 1, "
                "last_active_at = NOW(), updated_at = NOW() WHERE id = %s",
                (user_id,)
            )
    except Exception as e:
        logger.error(f"Error en _increment_user_renders: {e}")
