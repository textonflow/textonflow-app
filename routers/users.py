"""
routers/users.py — Endpoints de usuarios, proyectos y sesiones de imagen.
  /user/register, /user/login, /user/me, /user/usage, /user/webhook
  /projects/* (CRUD)
  /user/forgot-password, /user/reset-password, /user/can-export, /user/track-copy
  /user/session/open, /user/session/close, /api/admin/image-sessions
Montado en main.py con: app.include_router(users_router)
"""
import asyncio
import functools
import json
import logging
import os
import secrets
import threading
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras
import requests as _requests
from fastapi import APIRouter, HTTPException, Request

from auth import hash_password, verify_password, create_jwt, _is_superadmin, _get_client_ip
from database import get_db
from models import (
    _UserRegisterBody, _UserLoginBody, _UserUpdateBody,
    _WebhookBody, _ProjectCreate, _ProjectUpdate,
    _ForgotPasswordBody, _ResetPasswordBody,
    _SessionOpenBody, _SessionCloseBody,
)
from user_limits import (
    USER_PLAN_LIMITS, TRIAL_DAYS, JSON_EXPORT_PLANS,
    _require_user, _get_current_user,
)

logger = logging.getLogger("textonflow")

try:
    import stripe as _stripe_mod
    _STRIPE_OK = bool(os.environ.get("STRIPE_SECRET_KEY", ""))
except ImportError:
    _STRIPE_OK = False

users_router = APIRouter()

# ─── Registro ─────────────────────────────────────────────────────────────────

@users_router.post("/user/register")
async def user_register(body: _UserRegisterBody):
    """Registra un usuario nuevo con plan trial (20 renders)."""
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido.")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email.")
            pwd_hash = hash_password(body.password)
            cur.execute("""
                INSERT INTO users (email, password_hash, plan, renders_limit)
                VALUES (%s, %s, 'trial', %s)
                RETURNING id, email, plan, renders_used, renders_limit, created_at
            """, (email, pwd_hash, USER_PLAN_LIMITS["trial"]))
            user = dict(cur.fetchone())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en registro: {e}")
        raise HTTPException(status_code=500, detail="Error interno al crear la cuenta.")
    token = create_jwt(str(user["id"]), user["email"], user["plan"])
    logger.info(f"✅ Nuevo usuario registrado: {email}")

    # ── Email de bienvenida (EngineMailer) — async, no bloquea ───────────────
    _em_key = os.environ.get("ENGINEMAILER_API_KEY", "")
    if _em_key:
        async def _send_welcome():
            try:
                _body = {
                    "CampaignName": "TextOnFlow Bienvenida",
                    "ToEmail": email,
                    "SenderEmail": "hola@textonflow.com",
                    "SenderName": "TextOnFlow",
                    "Subject": "¡Bienvenido a TextOnFlow! Tus 20 renders gratis te esperan",
                    "SubmittedContent": (
                        f"<h2>¡Hola! 👋</h2>"
                        f"<p>Tu cuenta <strong>{email}</strong> ya está activa con el <strong>Plan Trial</strong> — 20 renders gratuitos para empezar.</p>"
                        f"<p>Entra al editor y crea tu primera imagen personalizada:</p>"
                        f"<p><a href='https://www.textonflow.com' style='background:#7c6eff;color:#fff;padding:10px 22px;border-radius:8px;text-decoration:none;font-weight:700;'>Abrir editor →</a></p>"
                        f"<hr style='margin:24px 0;border:none;border-top:1px solid #eee'>"
                        f"<p style='font-size:12px;color:#888'>Cuando quieras más renders, elige tu plan en: "
                        f"<a href='https://www.textonflow.com/dashboard'>textonflow.com/dashboard</a></p>"
                    ),
                }
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(
                    None,
                    functools.partial(
                        _requests.post,
                        "https://api.enginemailer.com/RESTAPI/V2/Submission/SendEmail",
                        headers={"APIKey": _em_key, "Content-Type": "application/json"},
                        json=_body,
                        timeout=15,
                    )
                )
                if resp.status_code in (200, 201):
                    logger.info(f"📧 Email de bienvenida enviado a {email}")
                else:
                    logger.warning(f"EngineMailer bienvenida {resp.status_code}: {resp.text[:120]}")
            except Exception as _e:
                logger.warning(f"Error enviando bienvenida: {_e}")
        asyncio.create_task(_send_welcome())

    return {
        "token": token,
        "user": {
            "id": str(user["id"]),
            "email": user["email"],
            "plan": user["plan"],
            "renders_used": user["renders_used"],
            "renders_limit": user["renders_limit"],
        }
    }

# ─── Login ────────────────────────────────────────────────────────────────────

@users_router.post("/user/login")
async def user_login(body: _UserLoginBody):
    """Login de usuario — devuelve JWT válido por 7 días."""
    email = body.email.strip().lower()
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, email, password_hash, plan, renders_used, renders_limit, is_active, gemini_api_key
                FROM users WHERE email = %s
            """, (email,))
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en login: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos.")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Cuenta desactivada.")
    token = create_jwt(str(user["id"]), user["email"], user["plan"])
    return {
        "token": token,
        "user": {
            "id": str(user["id"]),
            "email": user["email"],
            "plan": user["plan"],
            "renders_used": user["renders_used"],
            "renders_limit": user["renders_limit"],
            "has_gemini_key": bool(user["gemini_api_key"]),
        }
    }

# ─── Perfil ───────────────────────────────────────────────────────────────────

@users_router.get("/user/me")
async def user_me(request: Request):
    """Devuelve los datos del usuario autenticado."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, email, plan, renders_used, renders_limit, gemini_api_key,
                       stripe_customer_id, watermark_exempt, is_active, created_at
                FROM users WHERE id = %s
            """, (payload["sub"],))
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en /user/me: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    plan = user["plan"]
    limit = USER_PLAN_LIMITS.get(plan, user["renders_limit"])
    watermark_active = plan not in JSON_EXPORT_PLANS and not user.get("watermark_exempt", False)
    can_export_json = plan in JSON_EXPORT_PLANS

    # ── Info de trial basado en tiempo ────────────────────────────────────────
    trial_expires_at = None
    trial_days_remaining = None
    trial_expired = False
    if plan == "trial" and user["created_at"]:
        created = user["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        expires = created + timedelta(days=TRIAL_DAYS)
        trial_expires_at = expires.isoformat()
        remaining = (expires - datetime.now(timezone.utc)).days
        trial_days_remaining = max(0, remaining)
        trial_expired = remaining < 0

    return {
        "id": str(user["id"]),
        "email": user["email"],
        "plan": plan,
        "renders_used": user["renders_used"],
        "renders_limit": limit,
        "renders_remaining": max(0, limit - user["renders_used"]),
        "has_gemini_key": bool(user["gemini_api_key"]),
        "has_stripe": bool(user["stripe_customer_id"]),
        "watermark_active": watermark_active,
        "watermark_exempt": bool(user.get("watermark_exempt", False)),
        "can_export_json": can_export_json,
        "is_active": user.get("is_active", True),
        "created_at": user["created_at"].isoformat() if user["created_at"] else None,
        "trial_expires_at": trial_expires_at,
        "trial_days_remaining": trial_days_remaining,
        "trial_expired": trial_expired,
    }

@users_router.put("/user/me")
async def user_update(body: _UserUpdateBody, request: Request):
    """Actualiza datos del usuario (gemini_api_key, etc.)."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor() as cur:
            if body.gemini_api_key is not None:
                key = body.gemini_api_key.strip() or None
                cur.execute("""
                    UPDATE users SET gemini_api_key = %s, updated_at = NOW()
                    WHERE id = %s
                """, (key, payload["sub"]))
    except Exception as e:
        logger.error(f"Error en PUT /user/me: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    return {"ok": True}

@users_router.delete("/api/user/me", tags=["Usuarios"], summary="Eliminar cuenta (GDPR)")
async def delete_user_me(request: Request):
    """
    Elimina permanentemente la cuenta del usuario autenticado y todos sus datos asociados.
    Cancela la suscripción activa en Stripe si existe.
    Cumplimiento GDPR Art. 17 — Derecho de supresión.
    """
    payload = _require_user(request)
    user_id = payload["sub"]
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, stripe_customer_id, plan FROM users WHERE id = %s",
                (user_id,)
            )
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Error leyendo usuario para DELETE /api/user/me: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    stripe_customer_id = user.get("stripe_customer_id")
    if stripe_customer_id and _STRIPE_OK:
        try:
            import stripe as _s
            _s.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
            subs = _s.Subscription.list(customer=stripe_customer_id, status="active", limit=5)
            for sub in subs.get("data", []):
                try:
                    _s.Subscription.cancel(sub["id"])
                    logger.info(f"Stripe subscription {sub['id']} cancelada para usuario {user_id}")
                except Exception as stripe_err:
                    logger.warning(f"No se pudo cancelar suscripción Stripe {sub['id']}: {stripe_err}")
        except Exception as e:
            logger.warning(f"Error al consultar Stripe para DELETE /api/user/me user {user_id}: {e}")

    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM projects WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM subscriptions WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"Error eliminando usuario {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al eliminar la cuenta. Contacta a hola@textonflow.com.")

    logger.info(f"Cuenta eliminada por solicitud del usuario: {user.get('email', user_id)} (GDPR Art.17)")
    return {"ok": True, "message": "Cuenta eliminada permanentemente."}

# ─── Uso ──────────────────────────────────────────────────────────────────────

@users_router.get("/user/usage")
async def user_usage(request: Request):
    """Devuelve el uso actual de renders del usuario autenticado."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT plan, renders_used, renders_limit, created_at FROM users WHERE id = %s
            """, (payload["sub"],))
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en /user/usage: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    plan  = user["plan"]
    limit = USER_PLAN_LIMITS.get(plan, 20)

    # ── Trial basado en tiempo ─────────────────────────────────────────────────
    trial_days_remaining = None
    trial_expired = False
    if plan == "trial" and user["created_at"]:
        created = user["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        expires = created + timedelta(days=TRIAL_DAYS)
        remaining = (expires - datetime.now(timezone.utc)).days
        trial_days_remaining = max(0, remaining)
        trial_expired = remaining < 0

    return {
        "plan": plan,
        "renders_used": user["renders_used"],
        "renders_limit": limit,
        "renders_remaining": None if plan == "trial" else max(0, limit - user["renders_used"]),
        "pct": 0 if plan == "trial" else (min(100, round(user["renders_used"] / limit * 100)) if limit else 0),
        "trial_days_remaining": trial_days_remaining,
        "trial_expired": trial_expired,
    }

# ─── Webhook URL del usuario ──────────────────────────────────────────────────

@users_router.get("/user/webhook", tags=["webhooks"],
                  summary="Obtener webhook URL configurado",
                  response_description="URL de webhook del usuario o null")
async def get_user_webhook(request: Request):
    """Devuelve el webhook_url configurado para el usuario autenticado."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT webhook_url FROM users WHERE id = %s", (payload["sub"],))
        row = cur.fetchone()
    return {"webhook_url": row["webhook_url"] if row else None}

@users_router.put("/user/webhook", tags=["webhooks"],
                  summary="Configurar webhook URL",
                  response_description="Confirmación de actualización")
async def set_user_webhook(body: _WebhookBody, request: Request):
    """
    Guarda o borra el webhook_url del usuario.
    TextOnFlow hará POST a esa URL tras cada render exitoso con el payload:
    `{"event":"render.done","image_url":"...","template":"...","ts":"..."}`
    """
    payload = _require_user(request)
    url = (body.webhook_url or "").strip() or None
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="La URL debe empezar con http:// o https://")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET webhook_url = %s, updated_at = NOW() WHERE id = %s",
                    (url, payload["sub"]))
    return {"ok": True, "webhook_url": url}

# ─── Proyectos ─────────────────────────────────────────────────────────────────

@users_router.post("/projects", tags=["projects"], status_code=201,
                   summary="Crear proyecto")
async def create_project(body: _ProjectCreate, request: Request):
    """Crea un nuevo proyecto guardando el estado completo del canvas."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            INSERT INTO projects (user_id, name, canvas_json, image_url)
            VALUES (%s, %s, %s, %s)
            RETURNING id, name, image_url, created_at, updated_at
        """, (payload["sub"], body.name[:120], json.dumps(body.canvas_json), body.image_url))
        row = cur.fetchone()
    return {
        "id": str(row["id"]), "name": row["name"],
        "image_url": row["image_url"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }

@users_router.get("/projects", tags=["projects"],
                  summary="Listar proyectos del usuario")
async def list_projects(request: Request, limit: int = 50, offset: int = 0):
    """Devuelve los proyectos del usuario ordenados por actualización desc."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, name, image_url, created_at, updated_at
            FROM projects WHERE user_id = %s
            ORDER BY updated_at DESC LIMIT %s OFFSET %s
        """, (payload["sub"], min(limit, 200), offset))
        rows = cur.fetchall()
    return {"projects": [
        {"id": str(r["id"]), "name": r["name"], "image_url": r["image_url"],
         "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()}
        for r in rows
    ], "total": len(rows)}

@users_router.get("/projects/{project_id}", tags=["projects"],
                  summary="Obtener proyecto con canvas completo")
async def get_project(project_id: str, request: Request):
    """Devuelve el proyecto completo incluyendo canvas_json para restaurar el editor."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, name, canvas_json, image_url, created_at, updated_at
            FROM projects WHERE id = %s AND user_id = %s
        """, (project_id, payload["sub"]))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    return {
        "id": str(row["id"]), "name": row["name"],
        "canvas_json": row["canvas_json"],
        "image_url": row["image_url"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }

@users_router.put("/projects/{project_id}", tags=["projects"],
                  summary="Actualizar proyecto")
async def update_project(project_id: str, body: _ProjectUpdate, request: Request):
    """Actualiza nombre, canvas o imagen de un proyecto existente."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    updates, vals = [], []
    if body.name is not None:
        updates.append("name = %s"); vals.append(body.name[:120])
    if body.canvas_json is not None:
        updates.append("canvas_json = %s"); vals.append(json.dumps(body.canvas_json))
    if body.image_url is not None:
        updates.append("image_url = %s"); vals.append(body.image_url)
    if not updates:
        raise HTTPException(status_code=422, detail="Sin cambios.")
    updates.append("updated_at = NOW()")
    vals += [project_id, payload["sub"]]
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE projects SET {', '.join(updates)} WHERE id = %s AND user_id = %s",
            vals
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    return {"ok": True}

@users_router.delete("/projects/{project_id}", tags=["projects"],
                     summary="Eliminar proyecto")
async def delete_project(project_id: str, request: Request):
    """Elimina un proyecto del usuario."""
    payload = _require_user(request)
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id = %s AND user_id = %s",
                    (project_id, payload["sub"]))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    return {"ok": True}

# ─── Recuperación de contraseña ───────────────────────────────────────────────

@users_router.post("/user/forgot-password")
async def user_forgot_password(body: _ForgotPasswordBody):
    """Genera token de reset y envía email. Siempre responde OK (no revela si email existe)."""
    email = body.email.strip().lower()
    conn = get_db()
    if not conn:
        return {"ok": True}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, email FROM users WHERE email = %s AND is_active = TRUE", (email,))
            user = cur.fetchone()
        if not user:
            return {"ok": True}
        reset_token = secrets.token_urlsafe(32)
        with conn.cursor() as cur:
            cur.execute("UPDATE password_resets SET used = TRUE WHERE user_id = %s AND used = FALSE", (user["id"],))
            cur.execute("""
                INSERT INTO password_resets (user_id, token, expires_at)
                VALUES (%s, %s, NOW() + INTERVAL '1 hour')
            """, (user["id"], reset_token))
    except Exception as e:
        logger.error(f"Error en forgot-password: {e}")
        return {"ok": True}

    base = os.environ.get("BASE_URL", "https://www.textonflow.com").rstrip("/")
    reset_url = f"{base}/reset-password?token={reset_token}"
    em_key = os.getenv("ENGINEMAILER_API_KEY", "")
    if em_key:
        def _send_reset():
            try:
                _requests.post(
                    "https://api.enginemailer.com/RESTAPI/V2/Submission/SendEmail",
                    json={
                        "UserKey": em_key,
                        "ToEmail": email,
                        "ToName": email.split("@")[0],
                        "SenderEmail": "noreply@textonflow.com",
                        "SenderName": "TextOnFlow",
                        "Subject": "Recupera tu contraseña — TextOnFlow",
                        "HTMLContent": f"""<div style="font-family:Arial,sans-serif;max-width:500px;margin:auto">
<h2 style="color:#7c3aed">Recuperar contraseña</h2>
<p>Hola, recibimos una solicitud para restablecer tu contraseña de TextOnFlow.</p>
<p><a href="{reset_url}" style="background:#7c3aed;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block">Restablecer contraseña</a></p>
<p style="color:#888;font-size:12px">Este enlace expira en 1 hora. Si no solicitaste este cambio, ignora este email.</p>
</div>""",
                    }, timeout=10
                )
            except Exception as _e:
                logger.warning(f"Email reset no enviado: {_e}")
        threading.Thread(target=_send_reset, daemon=True).start()
    logger.info(f"🔑 Reset solicitado para {email}")
    return {"ok": True}

@users_router.post("/user/reset-password")
async def user_reset_password(body: _ResetPasswordBody):
    """Valida el token y actualiza la contraseña."""
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT pr.id, pr.user_id, pr.expires_at, pr.used
                FROM password_resets pr
                WHERE pr.token = %s
            """, (body.token,))
            rec = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en reset-password: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not rec:
        raise HTTPException(status_code=400, detail="Token inválido o expirado.")
    if rec["used"]:
        raise HTTPException(status_code=400, detail="Este enlace ya fue usado.")
    if rec["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="El enlace expiró. Solicita uno nuevo.")
    new_hash = hash_password(body.new_password)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password_hash = %s, updated_at = NOW() WHERE id = %s",
                        (new_hash, rec["user_id"]))
            cur.execute("UPDATE password_resets SET used = TRUE WHERE id = %s", (rec["id"],))
    except Exception as e:
        logger.error(f"Error actualizando contraseña: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    logger.info(f"✅ Contraseña actualizada para user_id={rec['user_id']}")
    return {"ok": True, "message": "Contraseña actualizada correctamente."}

@users_router.get("/user/can-export")
async def user_can_export(request: Request):
    """Indica si el usuario puede exportar JSON (solo planes pagados)."""
    payload = _get_current_user(request)
    if not payload:
        return {"can_export": False, "reason": "auth_required"}
    plan = payload.get("plan", "trial")
    if plan in JSON_EXPORT_PLANS:
        return {"can_export": True, "plan": plan}
    return {"can_export": False, "reason": "upgrade_required", "plan": plan}

@users_router.post("/user/track-copy")
async def user_track_copy(request: Request):
    """Registra que el usuario dio clic en 'Copiar JSON'. Requiere JWT."""
    payload = _get_current_user(request)
    if not payload:
        return {"ok": False}
    user_id = payload.get("sub") or payload.get("user_id")
    conn = get_db()
    if not conn:
        return {"ok": False}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET json_copies = COALESCE(json_copies,0) + 1, "
                "last_active_at = NOW(), updated_at = NOW() WHERE id = %s",
                (user_id,)
            )
    except Exception as e:
        logger.warning(f"track-copy error: {e}")
    return {"ok": True}

# ─── Image Session Tracking ───────────────────────────────────────────────────

@users_router.post("/user/session/open")
async def image_session_open(body: _SessionOpenBody, request: Request):
    """Registra apertura de una sesión de imagen (anónimo o autenticado)."""
    conn = get_db()
    if not conn:
        return {"ok": True}
    user_id = None
    try:
        payload = _get_current_user(request)
        if payload:
            user_id = payload.get("sub") or payload.get("user_id")
    except Exception:
        pass
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO image_sessions (session_key, user_id, image_name, image_type, ip)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_key) DO NOTHING
            """, (body.session_key, user_id, body.image_name[:500], body.image_type, _get_client_ip(request)))
        conn.commit()
    except Exception as e:
        logger.warning(f"session/open error: {e}")
    return {"ok": True}

@users_router.post("/user/session/close")
async def image_session_close(body: _SessionCloseBody, request: Request):
    """Registra cierre de una sesión de imagen y calcula duración."""
    conn = get_db()
    if not conn:
        return {"ok": True}
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE image_sessions
                SET closed_at = NOW(),
                    duration_seconds = GREATEST(0, EXTRACT(EPOCH FROM (NOW() - opened_at))::INTEGER)
                WHERE session_key = %s AND closed_at IS NULL
            """, (body.session_key,))
        conn.commit()
    except Exception as e:
        logger.warning(f"session/close error: {e}")
    return {"ok": True}

@users_router.get("/api/admin/image-sessions")
async def admin_image_sessions(request: Request):
    """Reporte de sesiones por imagen — solo superadmin."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                  COUNT(*) AS total_sessions,
                  COUNT(*) FILTER (WHERE opened_at >= NOW() - INTERVAL '24 hours') AS sessions_today,
                  COUNT(*) FILTER (WHERE opened_at >= NOW() - INTERVAL '7 days') AS sessions_week,
                  ROUND(AVG(duration_seconds) FILTER (WHERE duration_seconds IS NOT NULL AND duration_seconds < 7200))::INTEGER AS avg_duration_sec,
                  COUNT(DISTINCT image_name) AS unique_images
                FROM image_sessions
            """)
            kpis = dict(cur.fetchone())
            cur.execute("""
                SELECT image_name, image_type,
                  COUNT(*) AS total_opens,
                  COUNT(*) FILTER (WHERE opened_at >= NOW() - INTERVAL '24 hours') AS opens_today,
                  ROUND(AVG(duration_seconds) FILTER (WHERE duration_seconds IS NOT NULL AND duration_seconds < 7200))::INTEGER AS avg_duration_sec,
                  MAX(opened_at) AS last_opened
                FROM image_sessions
                GROUP BY image_name, image_type
                ORDER BY total_opens DESC
                LIMIT 50
            """)
            top_images = []
            for r in cur.fetchall():
                row = dict(r)
                row["last_opened"] = row["last_opened"].isoformat() if row.get("last_opened") else None
                top_images.append(row)
            cur.execute("""
                SELECT DATE(opened_at) AS day, COUNT(*) AS opens
                FROM image_sessions
                WHERE opened_at >= NOW() - INTERVAL '30 days'
                GROUP BY DATE(opened_at) ORDER BY day
            """)
            by_day = [{"day": str(r["day"]), "opens": r["opens"]} for r in cur.fetchall()]
            cur.execute("""
                SELECT s.session_key, s.image_name, s.image_type,
                  s.opened_at, s.closed_at, s.duration_seconds, s.ip,
                  u.email AS user_email
                FROM image_sessions s
                LEFT JOIN users u ON s.user_id = u.id
                ORDER BY s.opened_at DESC LIMIT 100
            """)
            recent = []
            for r in cur.fetchall():
                row = dict(r)
                row["opened_at"] = row["opened_at"].isoformat() if row.get("opened_at") else None
                row["closed_at"] = row["closed_at"].isoformat() if row.get("closed_at") else None
                recent.append(row)
    except Exception as e:
        logger.error(f"admin_image_sessions error: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    return {"kpis": {k: (int(v) if v is not None else 0) for k, v in kpis.items()},
            "top_images": top_images, "by_day": by_day, "recent": recent}
