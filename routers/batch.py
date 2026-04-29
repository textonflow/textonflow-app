"""
routers/batch.py — Generación masiva de imágenes desde CSV / Google Sheets.
Endpoint: POST /api/batch/from-url
"""
import csv
import io
import json
import logging
import os
import re
import threading
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from auth import _get_client_ip
from database import log_render_event
from user_limits import (
    _get_current_user, _check_user_render_limit, _require_user,
)

logger = logging.getLogger("textonflow")
batch_router = APIRouter()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fetch_csv_from_sheets(sheet_url: str) -> str:
    """Descarga CSV de una Google Sheet pública.
    Acepta URLs en cualquier formato de Google Sheets y las normaliza."""
    # Extrae el ID del spreadsheet
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", sheet_url)
    if not m:
        raise HTTPException(status_code=400, detail="URL de Google Sheets inválida. Ejemplo: https://docs.google.com/spreadsheets/d/ID/edit")
    sheet_id = m.group(1)
    # Extrae el GID (página) si está presente
    gid_m = re.search(r"[#&?]gid=([0-9]+)", sheet_url)
    gid = gid_m.group(1) if gid_m else "0"
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    try:
        resp = requests.get(csv_url, timeout=15, headers={"User-Agent": "TextOnFlow/1.0"})
        if resp.status_code == 403:
            raise HTTPException(
                status_code=400,
                detail="La hoja de cálculo no es pública. Ve a Compartir → 'Cualquier persona con el enlace puede ver'."
            )
        resp.raise_for_status()
        return resp.text
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error descargando hoja: {str(e)}")


def _fetch_csv_from_url(url: str) -> str:
    """Descarga CSV genérico desde una URL."""
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "TextOnFlow/1.0"})
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error descargando CSV: {str(e)}")


def _parse_csv(csv_text: str) -> tuple[list, list]:
    """Parsea CSV y devuelve (headers, rows)."""
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    headers = reader.fieldnames or []
    rows = list(reader)
    return list(headers), rows


def _apply_vars_to_texts(texts: list, row: dict, column_map: dict) -> list:
    """Aplica variables de una fila a los textos del template.
    column_map: {variable_name: column_name} — ej: {"nombre": "Nombre"}
    También reemplaza {{variable}} en los textos."""
    import copy
    result = copy.deepcopy(texts)
    for tf in result:
        t = tf.get("text", "")
        # Reemplaza {{var}} con el valor de la columna mapeada
        for var_name, col_name in column_map.items():
            value = row.get(col_name, "")
            t = t.replace(f"{{{{{var_name}}}}}", value)
            t = t.replace(f"[{var_name}]", value)
        # También reemplaza directamente con nombres de columnas
        for col_name, value in row.items():
            t = t.replace(f"{{{{{col_name}}}}}", value)
        tf["text"] = t
    return result


# ─── Request / Response models ────────────────────────────────────────────────

class BatchFromUrlRequest(BaseModel):
    source_url: str                          # URL de Google Sheets o CSV directo
    template_json: Dict[str, Any]            # JSON completo del diseño (igual que /generate-multi)
    column_map: Optional[Dict[str, str]] = {} # {variable: columna} — mapeo de variables
    output_prefix: str = "imagen"            # Prefijo para nombres de archivo en ZIP
    max_rows: int = 100                      # Límite de filas a procesar


class PreviewColumnsRequest(BaseModel):
    source_url: str


# ─── Endpoints ────────────────────────────────────────────────────────────────

@batch_router.post("/api/batch/preview-columns")
async def preview_columns(body: PreviewColumnsRequest, request: Request):
    """Descarga la hoja y devuelve headers + primeras 5 filas para mapear columnas."""
    _require_user(request)
    if "docs.google.com" in body.source_url:
        csv_text = _fetch_csv_from_sheets(body.source_url)
    else:
        csv_text = _fetch_csv_from_url(body.source_url)
    headers, rows = _parse_csv(csv_text)
    preview = rows[:5]
    return {
        "columns": headers,
        "preview_rows": preview,
        "total_rows": len(rows),
    }


@batch_router.post("/api/batch/from-url")
async def batch_from_url(body: BatchFromUrlRequest, request: Request):
    """Genera imágenes para cada fila de un CSV / Google Sheet y devuelve un ZIP."""
    user = _require_user(request)
    user_id = user["sub"]

    # Check de límites: el batch cuenta como N renders
    _used, _limit, _exceeded, _plan = _check_user_render_limit(user_id)
    if _exceeded:
        raise HTTPException(status_code=429, detail="Límite de renders alcanzado. Actualiza tu plan.")

    # Descargar y parsear CSV
    if "docs.google.com" in body.source_url:
        csv_text = _fetch_csv_from_sheets(body.source_url)
    else:
        csv_text = _fetch_csv_from_url(body.source_url)

    headers, rows = _parse_csv(csv_text)
    if not rows:
        raise HTTPException(status_code=400, detail="El CSV está vacío o no tiene datos.")

    rows = rows[: body.max_rows]
    n = len(rows)

    if _used + n > _limit:
        available = max(0, _limit - _used)
        raise HTTPException(
            status_code=429,
            detail=f"No tienes suficientes renders disponibles. Necesitas {n}, tienes {available} restantes."
        )

    # Importar el render PIL (late import para evitar circular)
    from routers.render import _render_pil
    from models import MultiTextRequest
    from PIL import Image
    from stats import _increment_images_generated
    from user_limits import _increment_user_renders

    # Template base
    template_json = body.template_json
    project_name  = template_json.get("project_name") or "batch"

    errors: list = []
    zip_buf = BytesIO()

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, row in enumerate(rows):
            row_label = f"{idx + 1:03d}"
            # Obtener nombre de archivo de la primera columna si existe
            first_col_val = next(iter(row.values()), "") if row else ""
            safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", first_col_val)[:30] or row_label
            filename = f"{body.output_prefix}_{row_label}_{safe_name}.jpg"

            try:
                # Construir request modificando los textos con variables de la fila
                req_data = {**template_json}
                texts_with_vars = _apply_vars_to_texts(
                    template_json.get("texts", []),
                    row,
                    body.column_map or {},
                )
                req_data["texts"] = texts_with_vars
                req_data.pop("project_name", None)

                req = MultiTextRequest(**req_data)
                img: Image.Image = _render_pil(req)

                img_buf = BytesIO()
                if img.mode == "RGBA":
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[3])
                    img = bg
                img.save(img_buf, format="JPEG", quality=92)
                zf.writestr(filename, img_buf.getvalue())

                # Contadores
                _increment_images_generated()
                _increment_user_renders(user_id)

            except Exception as e:
                logger.warning(f"batch row {idx}: {e}")
                errors.append({"row": idx + 1, "error": str(e)})

    # Log del batch en la tabla renders
    rendered_ok = n - len(errors)
    threading.Thread(
        target=log_render_event,
        args=(user_id,),
        kwargs={"project_name": project_name, "count": rendered_ok, "endpoint": "batch"},
        daemon=True,
    ).start()

    zip_buf.seek(0)
    headers_resp = {
        "Content-Disposition": f"attachment; filename=textonflow-batch-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.zip",
        "Cache-Control": "no-store",
        "X-Batch-Rendered": str(rendered_ok),
        "X-Batch-Errors": str(len(errors)),
    }

    return Response(
        content=zip_buf.getvalue(),
        media_type="application/zip",
        headers=headers_resp,
    )
