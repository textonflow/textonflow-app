"""
utils.py — Utilidades compartidas sin dependencias internas.
"""
import os


def _get_base_url(request) -> str:
    """Construye la URL base correctamente detrás de Railway/Cloudflare proxy."""
    explicit = os.getenv("BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    host  = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    if host:
        return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")
