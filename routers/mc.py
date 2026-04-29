"""
mc.py — Integración directa con ManyChat (URL mágica).
Guarda una plantilla de diseño con ID único y la renderiza con variables de ManyChat.
"""
import json
import logging
import uuid
import os
import re
import httpx
import base64

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Any

from database import get_db
from auth import decode_jwt

logger = logging.getLogger("textonflow")

mc_router = APIRouter(prefix="/api/mc", tags=["manychat"])

API_URL = os.getenv("PUBLIC_URL", "https://www.textonflow.com")


# ─── Modelos ──────────────────────────────────────────────────────────────────

class SaveTemplateRequest(BaseModel):
    payload: dict


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _replace_vars(obj: Any, params: dict) -> Any:
    """Reemplaza {{variable}} en strings del payload JSON con los query params."""
    if isinstance(obj, str):
        def replacer(m):
            key = m.group(1).strip()
            return str(params.get(key, m.group(0)))
        return re.sub(r"\{\{([^}]+)\}\}", replacer, obj)
    if isinstance(obj, dict):
        return {k: _replace_vars(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_vars(i, params) for i in obj]
    return obj


# ─── Endpoints ────────────────────────────────────────────────────────────────

@mc_router.post("/template")
async def save_mc_template(body: SaveTemplateRequest, request: Request):
    """
    Guarda el diseño actual como plantilla ManyChat.
    Devuelve una URL lista para pegar en el HTTP Request de ManyChat.
    """
    # Auth opcional — guardamos con user_id si está logueado
    user_id = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = decode_jwt(auth[7:])
            user_id = payload.get("sub")
        except Exception:
            pass

    template_id = str(uuid.uuid4()).replace("-", "")[:16]

    conn = get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mc_templates (
                        id TEXT PRIMARY KEY,
                        user_id TEXT,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        renders INT DEFAULT 0
                    )
                    """,
                )
                cur.execute(
                    "INSERT INTO mc_templates (id, user_id, payload) VALUES (%s, %s, %s)",
                    (template_id, user_id, json.dumps(body.payload)),
                )
        except Exception as e:
            logger.error(f"save_mc_template DB error: {e}")
            raise HTTPException(status_code=500, detail="Error guardando plantilla")
    else:
        raise HTTPException(status_code=503, detail="Base de datos no disponible")

    render_url = f"{API_URL}/api/mc/{template_id}/render"
    return {
        "template_id": template_id,
        "render_url": render_url,
        "example": render_url + "?text=Hola%20{{nombre}}&image_url={{foto_url}}",
        "instructions": (
            "Pega render_url en el paso HTTP Request de ManyChat (método GET). "
            "Agrega tus variables de ManyChat como query params: "
            "?text={{nombre}}&image_url={{foto}} etc."
        ),
    }


@mc_router.get("/{template_id}/render")
async def render_mc_template(template_id: str, request: Request):
    """
    Renderiza una plantilla guardada reemplazando las variables de ManyChat.
    ManyChat llama este endpoint como HTTP Request (GET) y recibe la URL de la imagen.
    Todos los query params se usan para reemplazar {{variable}} en el payload.
    """
    params = dict(request.query_params)

    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Base de datos no disponible")

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM mc_templates WHERE id=%s", (template_id,))
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"render_mc_template DB read error: {e}")
        raise HTTPException(status_code=500, detail="Error leyendo plantilla")

    if not row:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    payload = dict(row["payload"] if hasattr(row["payload"], "keys") else json.loads(row["payload"]))

    # Override image_url si viene como param
    if "image_url" in params:
        payload["template_name"] = params["image_url"]

    # Reemplazar todas las {{variables}} en el payload
    payload = _replace_vars(payload, params)
    payload["render_scale"] = 1

    # Llamamos al propio /generate-multi internamente
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{API_URL}/generate-multi",
                json=payload,
                headers={"X-MC-Internal": "1"},
            )
        if resp.status_code != 200:
            detail = resp.json().get("detail", f"Error {resp.status_code}")
            raise HTTPException(status_code=resp.status_code, detail=detail)

        data = resp.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"render_mc_template render error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Actualizar contador de renders
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE mc_templates SET renders=renders+1 WHERE id=%s", (template_id,))
    except Exception:
        pass

    # ManyChat espera el resultado en el body — devolvemos image_url en raíz
    image_url = data.get("url") or data.get("image_url") or ""
    return JSONResponse({
        "success": True,
        "image_url": image_url,
        "url": image_url,          # alias por compatibilidad
        "template_id": template_id,
    })
