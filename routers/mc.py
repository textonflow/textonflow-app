"""
mc.py — Integración directa con ManyChat (URL mágica).
Guarda una plantilla de diseño con ID único y la renderiza con variables de ManyChat.
"""
import json
import logging
import uuid
import os
import re
from datetime import datetime, timezone, timedelta

import httpx
import psycopg2.extras

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any, Optional

from database import get_db
from auth import decode_jwt, create_jwt

logger = logging.getLogger("textonflow")

mc_router = APIRouter(prefix="/api/mc", tags=["manychat"])

API_URL = os.getenv("PUBLIC_URL", "https://www.textonflow.com")


# ─── Modelos ──────────────────────────────────────────────────────────────────

class SaveTemplateRequest(BaseModel):
    payload: dict


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _extract_vars(obj: Any, found: set) -> None:
    """Extrae todos los nombres de variables {{var}} del payload (recursivo)."""
    if isinstance(obj, str):
        for m in re.finditer(r"\{{1,2}([a-zA-Z_]\w*)\}{1,2}", obj):
            found.add(m.group(1).strip())
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_vars(v, found)
    elif isinstance(obj, list):
        for i in obj:
            _extract_vars(i, found)


def _replace_vars(obj: Any, params: dict) -> Any:
    """Reemplaza {{variable}} en strings del payload con los query params de ManyChat.
    Acepta formatos válidos y malformados: {{var}}, {{var}, {var}."""
    if isinstance(obj, str):
        def replacer(m):
            key = m.group(1).strip()
            return str(params.get(key, m.group(0)))
        # Regex leniente: 1-2 llaves apertura, nombre de variable, 1-2 llaves cierre
        return re.sub(r"\{{1,2}([a-zA-Z_]\w*)\}{1,2}", replacer, obj)
    if isinstance(obj, dict):
        return {k: _replace_vars(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_vars(i, params) for i in obj]
    return obj


def _ensure_mc_templates_table(conn):
    """Crea la tabla mc_templates si no existe (idempotente)."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mc_templates (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT,
                    payload     JSONB NOT NULL,
                    created_at  TIMESTAMPTZ DEFAULT NOW(),
                    renders     INT DEFAULT 0
                )
            """)
    except Exception as e:
        logger.warning(f"_ensure_mc_templates_table: {e}")


def _get_user_jwt(conn, user_id: str) -> Optional[str]:
    """Devuelve un JWT fresco para user_id, o None si no se encuentra."""
    if not user_id:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, plan FROM users WHERE id=%s LIMIT 1",
                (user_id,),
            )
            user = cur.fetchone()
        if not user:
            return None
        return create_jwt(str(user["id"]), user["email"], user["plan"])
    except Exception as e:
        logger.warning(f"_get_user_jwt error: {e}")
        return None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@mc_router.post("/template")
async def save_mc_template(body: SaveTemplateRequest, request: Request):
    """
    Guarda el diseño actual como plantilla ManyChat.
    Devuelve una URL lista para pegar en el HTTP Request de ManyChat.
    """
    user_id = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            decoded = decode_jwt(auth[7:])
            user_id = decoded.get("sub")
        except Exception:
            pass

    template_id = uuid.uuid4().hex[:16]

    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible")

    _ensure_mc_templates_table(conn)

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mc_templates (id, user_id, payload) VALUES (%s, %s, %s)",
                (template_id, user_id, json.dumps(body.payload)),
            )
    except Exception as e:
        logger.error(f"save_mc_template INSERT error: {e}")
        raise HTTPException(status_code=500, detail="Error guardando plantilla")

    # Detectar variables {{var}} usadas en el payload para construir URL lista
    found_vars: set = set()
    _extract_vars(body.payload, found_vars)
    # Excluir keys que no son variables de ManyChat (son campos internos del payload)
    _internal_keys = {
        "template_name", "render_scale", "format_width", "format_height",
        "img_zoom", "img_pan_x", "img_pan_y", "project_name",
    }
    mc_vars = sorted(found_vars - _internal_keys)

    base_url = f"{API_URL}/api/mc/{template_id}/render"
    if mc_vars:
        qs = "&".join(v + "={{" + v + "}}" for v in mc_vars)
        render_url = f"{base_url}?{qs}"
    else:
        render_url = base_url

    return {
        "template_id": template_id,
        "render_url": render_url,
        "vars_detected": mc_vars,
        "instructions": (
            "Pega render_url en el paso HTTP Request de ManyChat (método GET). "
            "Las variables de ManyChat ya están incluidas en la URL."
        ),
    }


@mc_router.get("/{template_id}/render")
async def render_mc_template(template_id: str, request: Request):
    """
    Renderiza una plantilla guardada reemplazando las variables de ManyChat.
    ManyChat llama este endpoint como HTTP Request (GET).
    Devuelve: {"success": true, "image_url": "https://..."}
    """
    params = dict(request.query_params)

    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible")

    _ensure_mc_templates_table(conn)

    # ── Leer plantilla de BD ────────────────────────────────────────────────
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT payload, user_id FROM mc_templates WHERE id=%s",
                (template_id,),
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"render_mc_template SELECT error: {e}")
        raise HTTPException(status_code=500, detail="Error leyendo plantilla")

    if not row:
        raise HTTPException(status_code=404, detail=f"Plantilla '{template_id}' no encontrada")

    # psycopg2 devuelve JSONB como dict directamente con RealDictCursor
    raw_payload = row["payload"]
    if isinstance(raw_payload, str):
        raw_payload = json.loads(raw_payload)
    payload = dict(raw_payload)

    stored_user_id = row.get("user_id")

    # ── Aplicar variables de ManyChat ───────────────────────────────────────
    if "image_url" in params:
        payload["template_name"] = params["image_url"]

    payload = _replace_vars(payload, params)
    payload["render_scale"] = 1

    # Pasar todos los query params como vars al motor de render
    # (necesario para countdown urgency: timer_final, etc.)
    if params:
        existing = payload.get("vars") or {}
        merged = {**existing, **{k: str(v) for k, v in params.items()}}
        payload["vars"] = merged

    # ── Obtener token JWT del dueño del template ────────────────────────────
    jwt_token = _get_user_jwt(conn, stored_user_id) if stored_user_id else None

    headers = {"Content-Type": "application/json"}
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"

    # ── Llamar a /generate-multi internamente ───────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{API_URL}/generate-multi",
                json=payload,
                headers=headers,
            )

        if resp.status_code != 200:
            try:
                detail = resp.json().get("detail", f"Error {resp.status_code}")
            except Exception:
                detail = f"Error {resp.status_code}"
            logger.error(f"generate-multi interno devolvió {resp.status_code}: {detail}")
            raise HTTPException(status_code=resp.status_code, detail=detail)

        data = resp.json()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"render_mc_template llamada render error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # ── Actualizar contador de renders ──────────────────────────────────────
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mc_templates SET renders = renders + 1 WHERE id=%s",
                (template_id,),
            )
    except Exception:
        pass

    image_url = data.get("url") or data.get("image_url") or ""

    # ManyChat espera el JSON con image_url en la raíz para el "Mapeo de respuesta"
    return JSONResponse({
        "success": True,
        "image_url": image_url,
        "url": image_url,
        "template_id": template_id,
    })


# ─── Fecha dinámica para ManyChat (External Request) ──────────────────────────
# Devuelve la fecha del momento de la consulta. ManyChat llama este endpoint en
# cada conversación, así {{fecha_actual}} siempre es el día en que se escanea el
# QR, sin tener que regenerar nada.

_MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
_DIAS_ES = [
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo",
]


def _ahora(tz: str):
    """Devuelve datetime actual en la zona pedida.
    Usa zoneinfo si está disponible; si no, cae a offset fijo (México = UTC-6,
    sin horario de verano desde 2022)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz))
    except Exception:
        offset_h = -6  # America/Mexico_City por defecto
        m = re.match(r"^UTC([+-]\d{1,2})$", tz.upper().replace(" ", ""))
        if m:
            offset_h = int(m.group(1))
        return datetime.now(timezone(timedelta(hours=offset_h)))


@mc_router.get("/fecha", tags=["manychat"])
async def fecha_actual(tz: str = "America/Mexico_City"):
    """Fecha de hoy lista para ManyChat.

    Ejemplo de uso en ManyChat (External Request → GET):
      https://www.textonflow.com/api/mc/fecha
    Luego mapea el campo `fecha_actual` a un User Field y úsalo en el mensaje:
      "Hola {{first_name}}, pasa por tu MYKOZ hoy {{fecha_actual}}..."

    Parámetro opcional `tz` (ej. ?tz=America/Bogota) para cambiar la zona horaria.
    """
    ahora = _ahora(tz)
    dia = ahora.day
    mes_nombre = _MESES_ES[ahora.month - 1]
    dia_semana = _DIAS_ES[ahora.weekday()]
    anio = ahora.year

    return JSONResponse({
        "success": True,
        # Lo principal: "28 de mayo de 2026"
        "fecha_actual": f"{dia} de {mes_nombre} de {anio}",
        # Variantes por si las necesitas en ManyChat
        "fecha_corta": ahora.strftime("%d/%m/%Y"),
        "fecha_larga": f"{dia_semana} {dia} de {mes_nombre} de {anio}",
        "dia": f"{dia:02d}",
        "mes": mes_nombre,
        "mes_numero": f"{ahora.month:02d}",
        "anio": str(anio),
        "dia_semana": dia_semana,
        "hora": ahora.strftime("%H:%M"),
        "iso": ahora.isoformat(),
        "tz": tz,
    })
