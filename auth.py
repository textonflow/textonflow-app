"""
auth.py — Autenticación JWT, hashing de contraseñas y rate-limiting para TextOnFlow.
Importado por main.py. Depende de database.py (sin importaciones circulares).
"""
import hashlib
import json
import base64
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from database import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_HOURS

# ─── Dependencias opcionales ──────────────────────────────────────────────────
try:
    from passlib.context import CryptContext
    from jose import jwt
    _AUTH_OK = True
except ImportError:
    _AUTH_OK = False

# ─── Contexto bcrypt ──────────────────────────────────────────────────────────
if _AUTH_OK:
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
else:
    pwd_context = None

# ─── Hashing de contraseñas ───────────────────────────────────────────────────
def hash_password(password: str) -> str:
    if pwd_context:
        return pwd_context.hash(password)
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain: str, hashed: str) -> bool:
    if pwd_context:
        try:
            return pwd_context.verify(plain, hashed)
        except Exception:
            pass
    return hashlib.sha256(plain.encode()).hexdigest() == hashed

# ─── JWT ──────────────────────────────────────────────────────────────────────
def create_jwt(user_id: str, email: str, plan: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": user_id, "email": email, "plan": plan, "exp": expire}
    if _AUTH_OK:
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return base64.b64encode(json.dumps({**payload, "exp": expire.isoformat()}).encode()).decode()

def decode_jwt(token: str) -> Optional[dict]:
    try:
        if _AUTH_OK:
            return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        data = json.loads(base64.b64decode(token.encode()).decode())
        return data
    except Exception:
        return None

# ─── Superadmin ───────────────────────────────────────────────────────────────
_SUPERADMIN_EMAIL    = "ruben@textonflow.com"
_SUPERADMIN_PWD_HASH = "8634d3c5b1865bc470198ac121dd36bc01cdb653f7bdff56e4e5273ee6df1ae1"
_ADMIN_SESSIONS: dict = {}
_ADMIN_LOCK           = threading.Lock()
_SESSION_TTL          = timedelta(days=30)

def _is_superadmin(request: "object") -> bool:
    token = request.headers.get("X-Admin-Token", "")
    if not token:
        return False
    with _ADMIN_LOCK:
        session = _ADMIN_SESSIONS.get(token)
        if not session:
            return False
        if datetime.utcnow() > session["expires"]:
            _ADMIN_SESSIONS.pop(token, None)
            return False
        return True

def _get_client_ip(req: "object") -> str:
    fwd = req.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (req.client.host or "unknown")

# ─── Rate limiting por IP (en memoria) ───────────────────────────────────────
PLAN_LIMITS: dict = {"free": 9999}
_IP_USAGE: dict   = {}
_IP_LOCK          = threading.Lock()

def _ip_usage_today(ip: str) -> dict:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    rec   = _IP_USAGE.get(ip, {"date": today, "count": 0})
    if rec["date"] != today:
        rec = {"date": today, "count": 0}
    return rec

def _check_rate_limit(ip: str) -> tuple:
    """(used, limit, exceeded) — límite desactivado temporalmente"""
    with _IP_LOCK:
        rec   = _ip_usage_today(ip)
        limit = PLAN_LIMITS["free"]
        return rec["count"], limit, False

def _increment_ip_usage(ip: str) -> tuple:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with _IP_LOCK:
        rec = _ip_usage_today(ip)
        rec["count"] += 1
        _IP_USAGE[ip] = {"date": today, "count": rec["count"]}
        return rec["count"], PLAN_LIMITS["free"]

# ─── Rate limiting por minuto (ventana deslizante 60 s) ──────────────────────
_MINUTE_BUCKETS: dict = {}
_MINUTE_LOCK          = threading.Lock()
_MINUTE_LIMITS        = {"trial": 4, "starter": 15, "agency": 40, "admin": 9999}

def _check_minute_limit(key: str, plan: str = "trial") -> tuple:
    """(allowed, used_this_min, limit_this_min)"""
    limit = _MINUTE_LIMITS.get(plan, 4)
    now   = time.time()
    with _MINUTE_LOCK:
        stamps = [t for t in _MINUTE_BUCKETS.get(key, []) if now - t < 60]
        used   = len(stamps)
        if used >= limit:
            _MINUTE_BUCKETS[key] = stamps
            return False, used, limit
        stamps.append(now)
        _MINUTE_BUCKETS[key] = stamps
        return True, used + 1, limit
