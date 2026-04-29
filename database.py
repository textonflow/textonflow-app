"""
database.py — Conexión y esquema de Supabase PostgreSQL para TextOnFlow.
Importado por main.py para mantenerlo desacoplado del resto de la app.
"""
import os
import threading
import logging

logger = logging.getLogger("textonflow")

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_OK = True
except ImportError:
    _PSYCOPG2_OK = False

# ─── Constantes ───────────────────────────────────────────────────────────────
SUPABASE_DATABASE_URL = os.environ.get("SUPABASE_DATABASE_URL", "")
JWT_SECRET            = os.environ.get("JWT_SECRET", "textonflow-dev-secret-change-in-prod")
JWT_ALGORITHM         = "HS256"
JWT_EXPIRE_HOURS      = 24 * 7  # 7 días

# ─── Conexión (singleton con reconexión automática) ───────────────────────────
_db_conn = None
_db_lock = threading.Lock()

def get_db():
    global _db_conn
    with _db_lock:
        if not _PSYCOPG2_OK or not SUPABASE_DATABASE_URL:
            return None
        try:
            if _db_conn is None or _db_conn.closed:
                _db_conn = psycopg2.connect(SUPABASE_DATABASE_URL, connect_timeout=10)
                _db_conn.autocommit = True
            else:
                _db_conn.poll()
        except Exception:
            try:
                _db_conn = psycopg2.connect(SUPABASE_DATABASE_URL, connect_timeout=10)
                _db_conn.autocommit = True
            except Exception as e:
                logger.error(f"DB connection error: {e}")
                return None
        return _db_conn

# ─── Inicialización del esquema ───────────────────────────────────────────────
def init_db():
    """Crea las tablas si no existen."""
    conn = get_db()
    if not conn:
        logger.warning("⚠️  Sin conexión a BD — modo sin base de datos")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    plan TEXT NOT NULL DEFAULT 'trial',
                    gemini_api_key TEXT DEFAULT NULL,
                    stripe_customer_id TEXT DEFAULT NULL,
                    renders_used INTEGER NOT NULL DEFAULT 0,
                    renders_limit INTEGER NOT NULL DEFAULT 20,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    stripe_subscription_id TEXT UNIQUE,
                    plan TEXT NOT NULL DEFAULT 'trial',
                    status TEXT NOT NULL DEFAULT 'active',
                    current_period_start TIMESTAMPTZ,
                    current_period_end TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS renders (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    endpoint TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    ip TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token TEXT UNIQUE NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    used BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS image_sessions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    session_key TEXT NOT NULL UNIQUE,
                    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                    image_name TEXT NOT NULL,
                    image_type TEXT NOT NULL DEFAULT 'url',
                    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    closed_at TIMESTAMPTZ,
                    duration_seconds INTEGER,
                    ip TEXT
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_renders_user_id ON renders(user_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_image_sessions_opened ON image_sessions(opened_at);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_image_sessions_name ON image_sessions(image_name);
            """)
            # Columnas opcionales — se agregan si no existen (idempotente)
            for _col_sql in [
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS watermark_exempt BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS json_exports_used INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS json_copies INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_paused BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS webhook_url TEXT",
                # v248 — render events enriquecidos para dashboard
                "ALTER TABLE renders ADD COLUMN IF NOT EXISTS project_name TEXT",
                "ALTER TABLE renders ADD COLUMN IF NOT EXISTS template_id TEXT",
                "ALTER TABLE renders ADD COLUMN IF NOT EXISTS count INTEGER NOT NULL DEFAULT 1",
            ]:
                try:
                    cur.execute(_col_sql)
                except Exception:
                    pass
            # Tabla projects
            cur.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name       TEXT NOT NULL DEFAULT 'Sin título',
                    canvas_json JSONB NOT NULL DEFAULT '{}',
                    image_url  TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)
            """)
        logger.info("✅ Base de datos inicializada correctamente")
    except Exception as e:
        logger.error(f"Error inicializando BD: {e}")


def log_render_event(user_id: str, project_name: str = None, template_id: str = None, count: int = 1, endpoint: str = "generate-multi"):
    """Registra un evento de render en la tabla renders (para dashboard de estadísticas)."""
    conn = get_db()
    if not conn or not user_id:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO renders (user_id, endpoint, status, project_name, template_id, count)
                VALUES (%s, %s, 'ok', %s, %s, %s)
                """,
                (user_id, endpoint, project_name, template_id, count),
            )
    except Exception as e:
        logger.warning(f"log_render_event error: {e}")


def get_user_render_stats(user_id: str) -> dict:
    """Devuelve estadísticas de renders para un usuario (usado por el dashboard)."""
    conn = get_db()
    if not conn:
        return {"total_month": 0, "total_all": 0, "by_day": [], "by_project": []}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT COALESCE(SUM(count),0) AS total FROM renders WHERE user_id=%s AND created_at >= date_trunc('month', NOW())",
                (user_id,),
            )
            total_month = int(cur.fetchone()["total"])

            cur.execute(
                "SELECT COALESCE(SUM(count),0) AS total FROM renders WHERE user_id=%s",
                (user_id,),
            )
            total_all = int(cur.fetchone()["total"])

            cur.execute(
                """
                SELECT TO_CHAR(DATE(created_at),'YYYY-MM-DD') AS day, COALESCE(SUM(count),0) AS renders
                FROM renders
                WHERE user_id=%s AND created_at >= NOW() - INTERVAL '30 days'
                GROUP BY DATE(created_at)
                ORDER BY DATE(created_at)
                """,
                (user_id,),
            )
            by_day = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT COALESCE(project_name,'Sin nombre') AS project, COALESCE(SUM(count),0) AS renders
                FROM renders
                WHERE user_id=%s AND created_at >= date_trunc('month', NOW())
                GROUP BY project_name
                ORDER BY renders DESC
                LIMIT 10
                """,
                (user_id,),
            )
            by_project = [dict(r) for r in cur.fetchall()]

        return {
            "total_month": total_month,
            "total_all": total_all,
            "by_day": by_day,
            "by_project": by_project,
        }
    except Exception as e:
        logger.error(f"get_user_render_stats error: {e}")
        return {"total_month": 0, "total_all": 0, "by_day": [], "by_project": []}
