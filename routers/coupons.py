"""
coupons.py — Sistema de cupones con códigos de barras / QR.

Administra "lotes" de códigos (generados por Odoo, importados por CSV/Excel o
pegados a mano), reparte el siguiente código disponible a cada contacto de
ManyChat de forma atómica, y genera la imagen del código (Code128 / EAN13 / QR).

Dos tipos de cupón:
  - 'single' (Tipo A): lote de un solo uso. Cada persona recibe el siguiente
    código disponible y queda marcado como asignado, sin repetirse.
  - 'multi'  (Tipo B): código válido para N canjes. Se reparte igual, pero el
    código sólo pasa a 'redeemed' cuando se agotan los usos (canje manual).

El canje real ocurre en Odoo (lector). Aquí el conteo de Tipo B es un espejo
opcional que el usuario actualiza con el botón "registrar canje".
"""
import csv
import io
import logging
import os
import secrets
import uuid
from typing import Optional, List
from urllib.parse import quote

import psycopg2
import psycopg2.errors
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from database import get_db
from user_limits import _require_user

logger = logging.getLogger("textonflow")

coupons_router = APIRouter(prefix="/api/coupons", tags=["coupons"])

API_URL = os.getenv("PUBLIC_URL", "https://www.textonflow.com")

_VALID_TYPES   = {"single", "multi"}
_VALID_FORMATS = {"code128", "ean13", "qr"}

_MAX_VALUE_LEN          = 512               # largo máx del valor a codificar
_MAX_UPLOAD_BYTES       = 5 * 1024 * 1024   # 5 MB por archivo
_MAX_CODES_PER_UPLOAD   = 50_000            # filas máx a procesar de un archivo


# ─── Modelos ──────────────────────────────────────────────────────────────────

class CouponBatchCreate(BaseModel):
    name:           str = "Lote de cupones"
    coupon_type:    str = "single"      # 'single' (A) | 'multi' (B)
    barcode_format: str = "code128"     # 'code128' | 'ean13' | 'qr'
    default_uses:   int = 1             # usos por código (Tipo B)
    codes_text:     Optional[str] = ""  # códigos pegados (uno por línea / coma)
    codes:          Optional[List[str]] = None


class CouponRedeemRequest(BaseModel):
    uses: int = 1


# ─── Esquema (idempotente, estilo mc.py) ──────────────────────────────────────

def _ensure_coupon_tables(conn):
    """Crea las tablas de cupones si no existen (idempotente)."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS coupon_batches (
                    id             TEXT PRIMARY KEY,
                    user_id        TEXT,
                    name           TEXT NOT NULL DEFAULT 'Lote de cupones',
                    coupon_type    TEXT NOT NULL DEFAULT 'single',
                    barcode_format TEXT NOT NULL DEFAULT 'code128',
                    default_uses   INT  NOT NULL DEFAULT 1,
                    dispense_token TEXT UNIQUE NOT NULL,
                    created_at     TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS coupon_codes (
                    id             TEXT PRIMARY KEY,
                    batch_id       TEXT NOT NULL,
                    code           TEXT NOT NULL,
                    status         TEXT NOT NULL DEFAULT 'available',
                    assigned_to    TEXT,
                    assigned_at    TIMESTAMPTZ,
                    total_uses     INT NOT NULL DEFAULT 1,
                    remaining_uses INT NOT NULL DEFAULT 1,
                    redeemed_at    TIMESTAMPTZ,
                    created_at     TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_coupon_codes_batch_code
                ON coupon_codes(batch_id, code)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_coupon_codes_batch_status
                ON coupon_codes(batch_id, status)
            """)
            # Garantiza un único código por (lote, suscriptor): hace que el
            # reparto sea idempotente incluso bajo concurrencia (la 2ª asignación
            # del mismo suscriptor viola este índice y se reusa el existente).
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_coupon_codes_batch_subscriber
                ON coupon_codes(batch_id, assigned_to)
                WHERE assigned_to IS NOT NULL
            """)
    except Exception as e:
        logger.warning(f"_ensure_coupon_tables: {e}")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _db_or_503():
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible")
    _ensure_coupon_tables(conn)
    return conn


def _owned_batch(conn, batch_id: str, user_id: str) -> dict:
    """Devuelve el lote si pertenece al usuario, o lanza 404."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM coupon_batches WHERE id=%s", (batch_id,))
        batch = cur.fetchone()
    if not batch or str(batch.get("user_id")) != str(user_id):
        raise HTTPException(status_code=404, detail="Lote no encontrado")
    return dict(batch)


def _batch_counts(conn, batch_id: str) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                COUNT(*)                                      AS total,
                COUNT(*) FILTER (WHERE status='available')    AS available,
                COUNT(*) FILTER (WHERE status='assigned')     AS assigned,
                COUNT(*) FILTER (WHERE status='redeemed')     AS redeemed
            FROM coupon_codes WHERE batch_id=%s
        """, (batch_id,))
        row = cur.fetchone() or {}
    return {k: int(row.get(k) or 0) for k in ("total", "available", "assigned", "redeemed")}


def _normalize_codes(raw_codes: List[str]) -> List[str]:
    """Limpia y deduplica códigos preservando el orden."""
    seen, out = set(), []
    for c in raw_codes:
        c = (c or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _parse_codes_file(filename: str, content: bytes) -> List[dict]:
    """Extrae [{code, uses}] de un CSV o Excel exportado de Odoo.

    Detecta la columna del código por encabezado (code/codigo/cupon/coupon/barcode)
    o, si no hay encabezado reconocible, usa la primera columna. Para Tipo B busca
    una columna de usos (uses/usos/cantidad/qty).
    """
    name = (filename or "").lower()
    rows: List[List[str]] = []

    if name.endswith((".xlsx", ".xlsm", ".xls")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active
            for r in ws.iter_rows(values_only=True):
                rows.append(["" if v is None else str(v) for v in r])
                if len(rows) > _MAX_CODES_PER_UPLOAD + 1:
                    break
            wb.close()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"No se pudo leer el Excel: {e}")
    else:
        try:
            text = content.decode("utf-8-sig", errors="replace")
            # Detección simple de delimitador
            delimiter = ";" if (text.count(";") > text.count(",")) else ","
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            for r in reader:
                rows.append([("" if c is None else str(c)) for c in r])
                if len(rows) > _MAX_CODES_PER_UPLOAD + 1:
                    break
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"No se pudo leer el CSV: {e}")

    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return []

    code_col, uses_col = 0, None
    header = [(c or "").strip().lower() for c in rows[0]]
    _code_aliases = ("code", "codigo", "código", "cupon", "cupón", "coupon", "barcode", "qr")
    _uses_aliases = ("uses", "usos", "cantidad", "qty", "canjes")
    has_header = any(any(a in h for a in _code_aliases) for h in header)
    if has_header:
        for i, h in enumerate(header):
            if any(a in h for a in _code_aliases):
                code_col = i
                break
        for i, h in enumerate(header):
            if any(a in h for a in _uses_aliases):
                uses_col = i
                break
        data_rows = rows[1:]
    else:
        data_rows = rows

    out = []
    for r in data_rows:
        if code_col >= len(r):
            continue
        code = (r[code_col] or "").strip()
        if not code:
            continue
        uses = None
        if uses_col is not None and uses_col < len(r):
            try:
                uses = int(float((r[uses_col] or "").strip()))
            except Exception:
                uses = None
        out.append({"code": code, "uses": uses})
    return out


def _insert_codes(conn, batch_id: str, items: List[dict], default_uses: int) -> int:
    """Inserta códigos en el lote, ignorando duplicados. Devuelve cuántos se agregaron."""
    inserted = 0
    with conn.cursor() as cur:
        for it in items:
            code = it["code"]
            uses = it.get("uses")
            uses = default_uses if (uses is None or uses < 1) else uses
            try:
                cur.execute(
                    """
                    INSERT INTO coupon_codes (id, batch_id, code, total_uses, remaining_uses)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (batch_id, code) DO NOTHING
                    """,
                    (uuid.uuid4().hex, batch_id, code, uses, uses),
                )
                inserted += cur.rowcount or 0
            except Exception as e:
                logger.warning(f"_insert_codes skip '{code}': {e}")
    return inserted


def _image_url_for(code: str, fmt: str) -> str:
    return f"{API_URL}/api/coupons/image?value={quote(code, safe='')}&format={quote(fmt, safe='')}"


def _make_code_png(value: str, fmt: str) -> bytes:
    """Genera el PNG del código: QR (reutiliza qrcode) o barras (python-barcode)."""
    fmt = (fmt or "code128").lower()
    value = (value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="value requerido")

    if fmt == "qr":
        import qrcode
        from io import BytesIO
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=10, border=2)
        qr.add_data(value)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # Código de barras
    import barcode
    from barcode.writer import ImageWriter
    from io import BytesIO

    bc_name, bc_value = "code128", value
    if fmt == "ean13":
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) in (12, 13):
            bc_name, bc_value = "ean13", digits
        else:
            bc_name, bc_value = "code128", value  # fallback si no es EAN válido

    try:
        bc = barcode.get(bc_name, bc_value, writer=ImageWriter())
        buf = BytesIO()
        bc.write(buf, options={
            "module_height": 14.0,
            "module_width":  0.3,
            "font_size":     10,
            "text_distance": 3.5,
            "quiet_zone":    2.0,
            "write_text":    True,
            "background":    "white",
            "foreground":    "black",
        })
        return buf.getvalue()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No se pudo generar el código de barras: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  PANEL — requieren sesión (Authorization: Bearer)
# ═══════════════════════════════════════════════════════════════════════════════

@coupons_router.post("/batches")
async def create_batch(body: CouponBatchCreate, request: Request):
    """Crea un lote de cupones. Acepta códigos pegados (codes_text / codes)."""
    user = _require_user(request)
    user_id = str(user.get("sub"))

    coupon_type = body.coupon_type if body.coupon_type in _VALID_TYPES else "single"
    fmt = body.barcode_format if body.barcode_format in _VALID_FORMATS else "code128"
    default_uses = max(1, int(body.default_uses or 1)) if coupon_type == "multi" else 1

    conn = _db_or_503()
    batch_id = uuid.uuid4().hex[:16]
    token = secrets.token_urlsafe(18)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO coupon_batches
                    (id, user_id, name, coupon_type, barcode_format, default_uses, dispense_token)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (batch_id, user_id, (body.name or "Lote de cupones").strip(),
                 coupon_type, fmt, default_uses, token),
            )
    except Exception as e:
        logger.error(f"create_batch INSERT error: {e}")
        raise HTTPException(status_code=500, detail="Error creando el lote")

    # Códigos iniciales (pegados)
    raw = list(body.codes or [])
    if body.codes_text:
        for line in body.codes_text.replace(",", "\n").splitlines():
            raw.append(line)
    items = [{"code": c, "uses": None} for c in _normalize_codes(raw)]
    added = _insert_codes(conn, batch_id, items, default_uses) if items else 0

    return JSONResponse(_batch_dto(conn, batch_id, added=added))


@coupons_router.post("/batches/{batch_id}/upload")
async def upload_codes(batch_id: str, request: Request, file: UploadFile = File(...)):
    """Sube un CSV/Excel de Odoo y agrega sus códigos al lote."""
    user = _require_user(request)
    user_id = str(user.get("sub"))
    conn = _db_or_503()
    batch = _owned_batch(conn, batch_id, user_id)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Archivo demasiado grande (máx 5 MB)")
    items = _parse_codes_file(file.filename or "", content)
    if not items:
        raise HTTPException(status_code=400, detail="No se encontraron códigos en el archivo")

    # Deduplicar dentro del archivo preservando usos del primero
    seen, dedup = set(), []
    for it in items:
        if it["code"] in seen:
            continue
        seen.add(it["code"])
        dedup.append(it)

    added = _insert_codes(conn, batch_id, dedup, int(batch.get("default_uses") or 1))
    return JSONResponse(_batch_dto(conn, batch_id, added=added, found=len(dedup)))


@coupons_router.get("/batches")
async def list_batches(request: Request):
    """Lista los lotes del usuario con sus conteos."""
    user = _require_user(request)
    user_id = str(user.get("sub"))
    conn = _db_or_503()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT b.id, b.name, b.coupon_type, b.barcode_format, b.default_uses,
                       b.dispense_token, b.created_at,
                       COUNT(c.id)                                   AS total,
                       COUNT(c.id) FILTER (WHERE c.status='available') AS available,
                       COUNT(c.id) FILTER (WHERE c.status='assigned')  AS assigned,
                       COUNT(c.id) FILTER (WHERE c.status='redeemed')  AS redeemed
                FROM coupon_batches b
                LEFT JOIN coupon_codes c ON c.batch_id = b.id
                WHERE b.user_id = %s
                GROUP BY b.id
                ORDER BY b.created_at DESC
            """, (user_id,))
            rows = cur.fetchall()
    except Exception as e:
        logger.error(f"list_batches error: {e}")
        raise HTTPException(status_code=500, detail="Error listando lotes")

    out = []
    for r in rows:
        d = dict(r)
        d["dispense_url"] = _dispense_url(d["dispense_token"])
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        for k in ("total", "available", "assigned", "redeemed"):
            d[k] = int(d.get(k) or 0)
        out.append(d)
    return {"batches": out}


@coupons_router.get("/batches/{batch_id}")
async def batch_detail(batch_id: str, request: Request, limit: int = 200):
    """Detalle de un lote con sus códigos (máx. `limit`)."""
    user = _require_user(request)
    user_id = str(user.get("sub"))
    conn = _db_or_503()
    batch = _owned_batch(conn, batch_id, user_id)

    limit = max(1, min(int(limit or 200), 2000))
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, code, status, assigned_to, assigned_at,
                   total_uses, remaining_uses, redeemed_at, created_at
            FROM coupon_codes WHERE batch_id=%s
            ORDER BY created_at, id
            LIMIT %s
        """, (batch_id, limit))
        codes = []
        for r in cur.fetchall():
            d = dict(r)
            for tk in ("assigned_at", "redeemed_at", "created_at"):
                d[tk] = d[tk].isoformat() if d.get(tk) else None
            d["image_url"] = _image_url_for(d["code"], batch["barcode_format"])
            codes.append(d)

    dto = _batch_dto(conn, batch_id)
    dto["codes"] = codes
    return JSONResponse(dto)


@coupons_router.delete("/batches/{batch_id}")
async def delete_batch(batch_id: str, request: Request):
    user = _require_user(request)
    user_id = str(user.get("sub"))
    conn = _db_or_503()
    _owned_batch(conn, batch_id, user_id)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM coupon_codes WHERE batch_id=%s", (batch_id,))
            cur.execute("DELETE FROM coupon_batches WHERE id=%s", (batch_id,))
    except Exception as e:
        logger.error(f"delete_batch error: {e}")
        raise HTTPException(status_code=500, detail="Error eliminando el lote")
    return {"success": True}


@coupons_router.post("/codes/{code_id}/redeem")
async def redeem_code(code_id: str, body: CouponRedeemRequest, request: Request):
    """Registra un canje manual (espejo de Odoo). Decrementa usos en Tipo B."""
    user = _require_user(request)
    user_id = str(user.get("sub"))
    conn = _db_or_503()

    n = max(1, int(body.uses or 1))
    # Canje atómico en un solo statement (evita lost-update bajo concurrencia):
    # la resta y el cambio de estado se calculan en la BD con el row-lock del UPDATE.
    # La pertenencia se valida en el mismo WHERE vía join al lote del usuario.
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            UPDATE coupon_codes c
            SET remaining_uses = GREATEST(0, c.remaining_uses - %s),
                status = CASE WHEN c.remaining_uses - %s <= 0 THEN 'redeemed' ELSE 'assigned' END,
                redeemed_at = CASE WHEN c.remaining_uses - %s <= 0 THEN NOW() ELSE c.redeemed_at END
            FROM coupon_batches b
            WHERE c.id = %s AND b.id = c.batch_id AND b.user_id = %s
            RETURNING c.remaining_uses, c.status
        """, (n, n, n, code_id, user_id))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Código no encontrado")

    return {"success": True, "code_id": code_id,
            "remaining_uses": int(row["remaining_uses"]), "status": row["status"]}


# ═══════════════════════════════════════════════════════════════════════════════
#  IMAGEN — pública (ManyChat la consume por URL)
# ═══════════════════════════════════════════════════════════════════════════════

@coupons_router.get("/image")
def coupon_image(value: str, format: str = "code128"):
    """Genera la imagen del código (Code128 / EAN13 / QR) a partir del valor."""
    if len(value or "") > _MAX_VALUE_LEN:
        raise HTTPException(status_code=400, detail=f"value demasiado largo (máx {_MAX_VALUE_LEN})")
    png = _make_code_png(value, format)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


# ═══════════════════════════════════════════════════════════════════════════════
#  MANYCHAT — reparto del siguiente cupón (auth por dispense_token en la URL)
# ═══════════════════════════════════════════════════════════════════════════════

@coupons_router.get("/dispense/{dispense_token}")
async def dispense_next(dispense_token: str, request: Request):
    """Entrega el siguiente cupón disponible del lote y su imagen.

    ManyChat lo llama como External Request (GET). Pasa `subscriber_id` (o
    `contact_id`) para que un reintento no consuma un código nuevo (idempotente).
    Respuesta con el mismo formato que las integraciones ManyChat actuales.
    """
    params = dict(request.query_params)
    subscriber = (params.get("subscriber_id") or params.get("contact_id")
                  or params.get("id") or "").strip() or None

    conn = _db_or_503()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM coupon_batches WHERE dispense_token=%s", (dispense_token,))
        batch = cur.fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Lote no encontrado")

    fmt = batch["barcode_format"]

    # Idempotencia: si este suscriptor ya tiene un código, devuélvelo
    if subscriber:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT code, remaining_uses FROM coupon_codes
                WHERE batch_id=%s AND assigned_to=%s AND status IN ('assigned','redeemed')
                ORDER BY assigned_at DESC NULLS LAST LIMIT 1
            """, (batch["id"], subscriber))
            existing = cur.fetchone()
        if existing:
            code = existing["code"]
            img = _image_url_for(code, fmt)
            return JSONResponse({
                "success": True, "code": code, "image_url": img, "url": img,
                "remaining_uses": int(existing["remaining_uses"]),
                "reused": True, "batch_id": batch["id"],
            })

    # Reparto atómico del siguiente disponible (a prueba de concurrencia).
    # El índice único parcial (batch_id, assigned_to) impide que un mismo
    # suscriptor obtenga dos códigos en una carrera: la 2ª asignación viola el
    # índice; en ese caso devolvemos el código que ya tomó (idempotente).
    row = None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                UPDATE coupon_codes
                SET status='assigned', assigned_to=%s, assigned_at=NOW()
                WHERE id = (
                    SELECT id FROM coupon_codes
                    WHERE batch_id=%s AND status='available'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING code, remaining_uses
            """, (subscriber, batch["id"]))
            row = cur.fetchone()
    except psycopg2.errors.UniqueViolation:
        try:
            conn.rollback()
        except Exception:
            pass
        if subscriber:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT code, remaining_uses FROM coupon_codes
                    WHERE batch_id=%s AND assigned_to=%s
                    ORDER BY assigned_at DESC NULLS LAST LIMIT 1
                """, (batch["id"], subscriber))
                existing = cur.fetchone()
            if existing:
                img = _image_url_for(existing["code"], fmt)
                return JSONResponse({
                    "success": True, "code": existing["code"], "image_url": img, "url": img,
                    "remaining_uses": int(existing["remaining_uses"]),
                    "reused": True, "batch_id": batch["id"],
                })

    if not row:
        return JSONResponse(
            {"success": False, "error": "no_codes",
             "message": "No quedan cupones disponibles en este lote."},
            status_code=409,
        )

    code = row["code"]
    img = _image_url_for(code, fmt)
    return JSONResponse({
        "success": True, "code": code, "image_url": img, "url": img,
        "remaining_uses": int(row["remaining_uses"]), "reused": False,
        "batch_id": batch["id"],
    })


# ─── Helpers de presentación ──────────────────────────────────────────────────

def _dispense_url(token: str) -> str:
    return f"{API_URL}/api/coupons/dispense/{token}?subscriber_id={{{{contact_id}}}}"


def _batch_dto(conn, batch_id: str, added: int = None, found: int = None) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM coupon_batches WHERE id=%s", (batch_id,))
        b = dict(cur.fetchone())
    counts = _batch_counts(conn, batch_id)
    b["created_at"] = b["created_at"].isoformat() if b.get("created_at") else None
    b["dispense_url"] = _dispense_url(b["dispense_token"])
    b.update(counts)
    if added is not None:
        b["added"] = added
    if found is not None:
        b["found"] = found
    return b
