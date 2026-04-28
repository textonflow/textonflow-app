"""
routers/admin.py — Endpoints de administración de usuarios (/api/admin/*),
página reset-password y Stripe Checkout/Webhook.
Montado en main.py con: app.include_router(admin_router)
"""
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from auth import (
    _is_superadmin,
    _ADMIN_SESSIONS, _ADMIN_LOCK, _SESSION_TTL,
    _SUPERADMIN_EMAIL, _SUPERADMIN_PWD_HASH,
    PLAN_LIMITS,
)
from database import get_db
from models import _AdminLoginBody, _AdminSettingsBody, _AdminUserActionBody, _CheckoutBody
from user_limits import USER_PLAN_LIMITS

logger = logging.getLogger("textonflow")

admin_router = APIRouter()

# ─── Admin: gestión de usuarios ───────────────────────────────────────────────

@admin_router.get("/api/admin/users")
async def admin_list_users(request: Request, page: int = 1, limit: int = 200):
    """Lista todos los usuarios con stats completas (solo superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    offset = (page - 1) * limit
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, email, plan, renders_used, renders_limit, is_active,
                       COALESCE(is_paused, FALSE) AS is_paused,
                       watermark_exempt, json_exports_used,
                       COALESCE(json_copies, 0) AS json_copies,
                       last_active_at, created_at, updated_at
                FROM users ORDER BY created_at DESC LIMIT %s OFFSET %s
            """, (limit, offset))
            users = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) as total FROM users")
            total = cur.fetchone()["total"]
    except Exception as e:
        logger.error(f"Error en admin/users: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    for u in users:
        u["id"]             = str(u["id"])
        u["created_at"]     = u["created_at"].isoformat() if u.get("created_at") else None
        u["updated_at"]     = u["updated_at"].isoformat() if u.get("updated_at") else None
        u["last_active_at"] = u["last_active_at"].isoformat() if u.get("last_active_at") else None
    return {"users": users, "total": total, "page": page, "limit": limit}

@admin_router.get("/api/admin/stats")
async def admin_global_stats(request: Request):
    """Estadísticas globales del sistema (solo superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                  COUNT(*) AS total_users,
                  COUNT(*) FILTER (WHERE is_active) AS active_users,
                  COUNT(*) FILTER (WHERE NOT is_active) AS inactive_users,
                  COUNT(*) FILTER (WHERE plan = 'trial') AS trial_users,
                  COUNT(*) FILTER (WHERE plan = 'starter') AS starter_users,
                  COUNT(*) FILTER (WHERE plan = 'agency') AS agency_users,
                  COUNT(*) FILTER (WHERE plan = 'admin') AS admin_users,
                  COALESCE(SUM(renders_used), 0) AS total_renders,
                  COALESCE(SUM(json_copies), 0) AS total_json_copies,
                  COALESCE(SUM(json_exports_used), 0) AS total_json_exports,
                  COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS new_this_week,
                  COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS new_this_month,
                  COUNT(*) FILTER (WHERE last_active_at >= NOW() - INTERVAL '7 days') AS active_this_week
                FROM users
            """)
            stats = dict(cur.fetchone())
            cur.execute("""
                SELECT DATE(created_at) AS day, COUNT(*) AS renders
                FROM renders
                WHERE created_at >= NOW() - INTERVAL '30 days'
                GROUP BY DATE(created_at) ORDER BY day
            """)
            renders_by_day = [{"day": str(r["day"]), "renders": r["renders"]} for r in cur.fetchall()]
            cur.execute("""
                SELECT DATE(created_at) AS day, COUNT(*) AS users
                FROM users
                WHERE created_at >= NOW() - INTERVAL '30 days'
                GROUP BY DATE(created_at) ORDER BY day
            """)
            users_by_day = [{"day": str(r["day"]), "users": r["users"]} for r in cur.fetchall()]
            cur.execute("""
                SELECT email, plan, renders_used,
                       COALESCE(json_copies, 0) AS json_copies,
                       last_active_at
                FROM users ORDER BY renders_used DESC LIMIT 10
            """)
            top_users = []
            for r in cur.fetchall():
                row = dict(r)
                row["last_active_at"] = row["last_active_at"].isoformat() if row.get("last_active_at") else None
                top_users.append(row)
    except Exception as e:
        logger.error(f"Error en admin/stats: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    return {
        "stats": {k: int(v) for k, v in stats.items()},
        "renders_by_day": renders_by_day,
        "users_by_day": users_by_day,
        "top_users": top_users,
    }

@admin_router.post("/api/admin/users/toggle-active")
async def admin_toggle_active(body: _AdminUserActionBody, request: Request):
    """Activa o desactiva la cuenta de un usuario (superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE users SET is_active = NOT is_active, updated_at = NOW() WHERE id = %s "
                "RETURNING id, email, is_active", (body.user_id,)
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en toggle-active: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    action = "activado" if row["is_active"] else "desactivado"
    logger.info(f"👤 Admin {action} usuario {row['email']}")
    return {"ok": True, "user_id": str(row["id"]), "email": row["email"], "is_active": row["is_active"]}

@admin_router.post("/api/admin/users/toggle-paused")
async def admin_toggle_paused(body: _AdminUserActionBody, request: Request):
    """Pausa o reanuda la cuenta de un usuario sin desactivarla (superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE users SET is_paused = NOT COALESCE(is_paused, FALSE), updated_at = NOW() WHERE id = %s "
                "RETURNING id, email, is_paused", (body.user_id,)
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en toggle-paused: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    action = "pausado" if row["is_paused"] else "reanudado"
    logger.info(f"⏸ Admin {action} usuario {row['email']}")
    return {"ok": True, "user_id": str(row["id"]), "email": row["email"], "is_paused": row["is_paused"]}

@admin_router.delete("/api/admin/users/delete")
async def admin_delete_user(body: _AdminUserActionBody, request: Request):
    """Elimina permanentemente un usuario (superadmin). El propio superadmin no puede eliminarse."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, email, plan FROM users WHERE id = %s", (body.user_id,))
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="Usuario no encontrado.")
            if target["email"].lower() == "ruben@textonflow.com" or target["plan"] == "admin":
                raise HTTPException(status_code=403, detail="No se puede eliminar al superadministrador.")
            cur.execute("DELETE FROM users WHERE id = %s RETURNING id, email", (body.user_id,))
            deleted = cur.fetchone()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en admin/delete-user: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    logger.info(f"🗑 Admin eliminó usuario {deleted['email']}")
    return {"ok": True, "deleted_email": deleted["email"]}

@admin_router.post("/api/admin/users/toggle-watermark")
async def admin_toggle_watermark(body: _AdminUserActionBody, request: Request):
    """Otorga o revoca la exención de watermark a un usuario (superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE users SET watermark_exempt = NOT watermark_exempt, updated_at = NOW() "
                "WHERE id = %s RETURNING id, email, watermark_exempt, plan", (body.user_id,)
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en toggle-watermark: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    status = "exento" if row["watermark_exempt"] else "con marca"
    logger.info(f"🖼️ Admin watermark {status} → {row['email']}")
    return {"ok": True, "user_id": str(row["id"]), "email": row["email"],
            "watermark_exempt": row["watermark_exempt"], "plan": row["plan"]}

@admin_router.post("/api/admin/users/reset-renders")
async def admin_reset_renders(body: _AdminUserActionBody, request: Request):
    """Reinicia el contador de renders de un usuario (superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE users SET renders_used = 0, updated_at = NOW() WHERE id = %s "
                "RETURNING id, email", (body.user_id,)
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"Error en reset-renders: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    logger.info(f"🔄 Admin reinició renders de {row['email']}")
    return {"ok": True, "user_id": str(row["id"]), "email": row["email"], "renders_used": 0}

@admin_router.get("/reset-password", include_in_schema=False)
async def reset_password_page():
    """Página para restablecer la contraseña vía token."""
    html = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Restablecer contraseña — TextOnFlow</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0f0f17;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#1a1a2e;border:1px solid #2d2d44;border-radius:16px;padding:40px;width:100%;max-width:420px}
h2{color:#a78bfa;margin-bottom:8px;font-size:1.5rem}
p{color:#94a3b8;font-size:.875rem;margin-bottom:24px}
label{display:block;font-size:.8rem;color:#94a3b8;margin-bottom:6px}
input{width:100%;background:#0f0f17;border:1px solid #374151;border-radius:8px;padding:10px 14px;color:#e2e8f0;font-size:.9rem;margin-bottom:16px}
button{width:100%;background:#7c3aed;color:#fff;border:none;border-radius:8px;padding:12px;font-size:.95rem;cursor:pointer;font-weight:600}
button:hover{background:#6d28d9}
.msg{margin-top:16px;padding:12px;border-radius:8px;font-size:.85rem;text-align:center}
.msg.ok{background:#064e3b;color:#6ee7b7}
.msg.err{background:#7f1d1d;color:#fca5a5}
</style>
</head>
<body>
<div class="card">
  <h2>🔑 Nueva contraseña</h2>
  <p>Ingresa tu nueva contraseña para TextOnFlow.</p>
  <form id="form">
    <label>Nueva contraseña</label>
    <input type="password" id="pw1" placeholder="Mínimo 8 caracteres" required minlength="8">
    <label>Confirmar contraseña</label>
    <input type="password" id="pw2" placeholder="Repite la contraseña" required minlength="8">
    <button type="submit" id="btn">Restablecer contraseña</button>
  </form>
  <div id="msg" class="msg" style="display:none"></div>
</div>
<script>
const token = new URLSearchParams(location.search).get('token');
if(!token){document.getElementById('form').innerHTML='<p style="color:#f87171">Enlace inválido. Solicita uno nuevo.</p>';}
document.getElementById('form').addEventListener('submit',async e=>{
  e.preventDefault();
  const pw1=document.getElementById('pw1').value;
  const pw2=document.getElementById('pw2').value;
  if(pw1!==pw2){show('Las contraseñas no coinciden.','err');return;}
  document.getElementById('btn').disabled=true;
  document.getElementById('btn').textContent='Guardando...';
  try{
    const r=await fetch('/user/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,new_password:pw1})});
    const d=await r.json();
    if(r.ok){show('✅ Contraseña actualizada. Redirigiendo...','ok');setTimeout(()=>location.href='/',2500);}
    else{show(d.detail||'Error al restablecer.','err');document.getElementById('btn').disabled=false;document.getElementById('btn').textContent='Restablecer contraseña';}
  }catch(err){show('Error de conexión.','err');document.getElementById('btn').disabled=false;document.getElementById('btn').textContent='Restablecer contraseña';}
});
function show(txt,type){const m=document.getElementById('msg');m.textContent=txt;m.className='msg '+type;m.style.display='block';}
</script>
</body></html>"""
    return HTMLResponse(content=html)

# ═══════════════════════════════════════════════════════════════════════════════
#  STRIPE CHECKOUT (Phase 4)
# ═══════════════════════════════════════════════════════════════════════════════

try:
    import stripe as _stripe
    _stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    _STRIPE_OK = bool(_stripe.api_key)
except ImportError:
    _stripe      = None
    _STRIPE_OK   = False

STRIPE_PUBLISHABLE_KEY   = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET    = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_STARTER_PRICE_ID  = os.environ.get("STRIPE_STARTER_PRICE_ID", "price_1TQd5CDWKdKa9ZRQxabnfqla")
STRIPE_AGENCY_PRICE_ID   = os.environ.get("STRIPE_AGENCY_PRICE_ID",  "price_1TQd5DDWKdKa9ZRQ1KdEkA2U")

_PLAN_PRICE_MAP = {
    "starter": STRIPE_STARTER_PRICE_ID,
    "agency":  STRIPE_AGENCY_PRICE_ID,
}

@admin_router.post("/stripe/checkout")
async def stripe_checkout(body: _CheckoutBody, request: Request):
    """Crea una Stripe Checkout Session y devuelve la URL de pago."""
    if not _STRIPE_OK:
        raise HTTPException(status_code=503, detail="Stripe no configurado.")
    from user_limits import _require_user
    payload = _require_user(request)
    plan = body.plan.lower()
    if plan not in _PLAN_PRICE_MAP:
        raise HTTPException(status_code=400, detail="Plan inválido. Usa 'starter' o 'agency'.")
    price_id = _PLAN_PRICE_MAP[plan]
    base = body.success_url or os.environ.get("BASE_URL", "https://www.textonflow.com").rstrip("/")
    success_url = body.success_url or f"{base}/stripe/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = body.cancel_url  or f"{base}/precios"

    customer_id = None
    conn = get_db()
    if conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT stripe_customer_id, email FROM users WHERE id = %s", (payload["sub"],))
                row = cur.fetchone()
            if row:
                customer_id = row["stripe_customer_id"]
                if not customer_id:
                    customer = _stripe.Customer.create(
                        email=row["email"],
                        metadata={"user_id": payload["sub"]},
                    )
                    customer_id = customer.id
                    with conn.cursor() as cur:
                        cur.execute("UPDATE users SET stripe_customer_id = %s, updated_at = NOW() WHERE id = %s",
                                    (customer_id, payload["sub"]))
        except Exception as e:
            logger.error(f"Error Stripe customer: {e}")

    try:
        session_params = dict(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": payload["sub"], "plan": plan},
            subscription_data={"metadata": {"user_id": payload["sub"], "plan": plan}},
        )
        if customer_id:
            session_params["customer"] = customer_id
        else:
            session_params["customer_email"] = payload["email"]
        session = _stripe.checkout.Session.create(**session_params)
        return {"checkout_url": session.url, "session_id": session.id}
    except Exception as e:
        logger.error(f"Error creando Stripe session: {e}")
        raise HTTPException(status_code=500, detail=f"Error Stripe: {str(e)[:200]}")

@admin_router.get("/stripe/success")
async def stripe_success(session_id: str = ""):
    """Redirige al dashboard con flag de éxito."""
    return RedirectResponse(url="/dashboard?success=1", status_code=302)

@admin_router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Recibe eventos de Stripe y actualiza el plan del usuario en BD."""
    payload_bytes = await request.body()
    sig_header    = request.headers.get("stripe-signature", "")

    try:
        if STRIPE_WEBHOOK_SECRET and sig_header:
            event = _stripe.Webhook.construct_event(payload_bytes, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = _stripe.Event.construct_from(
                json.loads(payload_bytes.decode()), _stripe.api_key
            )
    except Exception as e:
        logger.error(f"Webhook signature error: {e}")
        raise HTTPException(status_code=400, detail="Webhook inválido.")

    etype = event["type"]
    logger.info(f"📨 Stripe webhook: {etype}")

    def _update_user_plan(user_id: str, new_plan: str):
        conn = get_db()
        if not conn or not user_id:
            return
        limit = USER_PLAN_LIMITS.get(new_plan, 20)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE users SET plan = %s, renders_limit = %s, updated_at = NOW()
                    WHERE id = %s
                """, (new_plan, limit, user_id))
            logger.info(f"✅ Usuario {user_id} actualizado a plan {new_plan}")
        except Exception as e:
            logger.error(f"Error actualizando plan en BD: {e}")

    def _upsert_subscription(user_id: str, sub_id: str, plan: str, status: str,
                              period_start=None, period_end=None):
        conn = get_db()
        if not conn or not user_id:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO subscriptions
                        (user_id, stripe_subscription_id, plan, status, current_period_start, current_period_end)
                    VALUES (%s, %s, %s, %s,
                        to_timestamp(%s), to_timestamp(%s))
                    ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                        plan = EXCLUDED.plan, status = EXCLUDED.status,
                        current_period_start = EXCLUDED.current_period_start,
                        current_period_end   = EXCLUDED.current_period_end,
                        updated_at           = NOW()
                """, (user_id, sub_id, plan, status, period_start, period_end))
        except Exception as e:
            logger.error(f"Error upsert subscription: {e}")

    if etype == "checkout.session.completed":
        obj     = event["data"]["object"]
        user_id = obj.get("metadata", {}).get("user_id", "")
        plan    = obj.get("metadata", {}).get("plan", "starter")
        sub_id  = obj.get("subscription", "")
        _update_user_plan(user_id, plan)
        if sub_id:
            _upsert_subscription(user_id, sub_id, plan, "active")

    elif etype in ("customer.subscription.updated", "customer.subscription.created"):
        obj     = event["data"]["object"]
        user_id = obj.get("metadata", {}).get("user_id", "")
        status  = obj.get("status", "active")
        sub_id  = obj.get("id", "")
        period_start = obj.get("current_period_start")
        period_end   = obj.get("current_period_end")
        items = obj.get("items", {}).get("data", [])
        plan  = "starter"
        if items:
            pid = items[0].get("price", {}).get("id", "")
            if pid == STRIPE_AGENCY_PRICE_ID:
                plan = "agency"
        if user_id and status == "active":
            _update_user_plan(user_id, plan)
        _upsert_subscription(user_id, sub_id, plan, status, period_start, period_end)

    elif etype == "customer.subscription.deleted":
        obj     = event["data"]["object"]
        user_id = obj.get("metadata", {}).get("user_id", "")
        sub_id  = obj.get("id", "")
        _update_user_plan(user_id, "trial")
        _upsert_subscription(user_id, sub_id, "trial", "canceled")

    return {"received": True}

@admin_router.get("/stripe/config")
async def stripe_config():
    """Devuelve la clave pública y los price IDs (para el frontend)."""
    return {
        "publishable_key": STRIPE_PUBLISHABLE_KEY,
        "plans": {
            "starter": {"price_id": STRIPE_STARTER_PRICE_ID, "amount": 29, "renders": 1000},
            "agency":  {"price_id": STRIPE_AGENCY_PRICE_ID,  "amount": 79, "renders": 10000},
        }
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH DE SUPERADMIN (/api/auth/*)
# ═══════════════════════════════════════════════════════════════════════════════

@admin_router.post("/api/auth/login")
async def admin_login(body: _AdminLoginBody):
    """Login de superadmin — devuelve un token de sesión de 30 días."""
    pwd_hash = hashlib.sha256(body.password.encode()).hexdigest()
    if body.email.strip().lower() != _SUPERADMIN_EMAIL or pwd_hash != _SUPERADMIN_PWD_HASH:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")
    token   = secrets.token_urlsafe(40)
    expires = datetime.utcnow() + _SESSION_TTL
    with _ADMIN_LOCK:
        _ADMIN_SESSIONS[token] = {"email": body.email, "expires": expires}
    return {"token": token, "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ")}

@admin_router.post("/api/auth/logout")
async def admin_logout(request: Request):
    token = request.headers.get("X-Admin-Token", "")
    if token:
        with _ADMIN_LOCK:
            _ADMIN_SESSIONS.pop(token, None)
    return {"ok": True}

@admin_router.get("/api/auth/me")
async def admin_me(request: Request):
    if _is_superadmin(request):
        return {"superadmin": True, "email": _SUPERADMIN_EMAIL}
    return {"superadmin": False}


# ─── Configuración editable del plan free ─────────────────────────────────────

@admin_router.get("/api/admin/settings")
async def admin_get_settings(request: Request):
    """Devuelve la configuración editable (solo superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    return {"free_limit": PLAN_LIMITS["free"]}

@admin_router.post("/api/admin/settings")
async def admin_set_settings(body: _AdminSettingsBody, request: Request):
    """Actualiza la configuración en caliente (solo superadmin)."""
    if not _is_superadmin(request):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    if body.free_limit < 1 or body.free_limit > 9999:
        raise HTTPException(status_code=400, detail="Límite debe estar entre 1 y 9999.")
    PLAN_LIMITS["free"] = body.free_limit
    logger.info(f"⚙️ Superadmin actualizó límite Free → {body.free_limit}")
    return {"ok": True, "free_limit": PLAN_LIMITS["free"]}
