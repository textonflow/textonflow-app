"""
fecha_utils.py — Fecha/hora dinámica para TextOnFlow.

Variables reservadas que se resuelven SOLAS en cada render (y en el endpoint
/api/mc/fecha). No requieren que el usuario las pase: siempre representan el
momento exacto en que se genera la imagen / se lee el QR, así que se actualizan
en cada lectura sin tener que regenerar nada.
"""
import re
from datetime import datetime, timezone, timedelta

DEFAULT_TZ = "America/Mexico_City"

_MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
_DIAS_ES = [
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo",
]


def ahora(tz: str = DEFAULT_TZ) -> datetime:
    """Datetime actual en la zona pedida.
    Usa zoneinfo si está disponible; si no, cae a offset fijo
    (México = UTC-6, sin horario de verano desde 2022)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz))
    except Exception:
        offset_h = -6  # America/Mexico_City por defecto
        m = re.match(r"^UTC([+-]\d{1,2})$", tz.upper().replace(" ", ""))
        if m:
            offset_h = int(m.group(1))
        return datetime.now(timezone(timedelta(hours=offset_h)))


def build_date_vars(tz: str = DEFAULT_TZ) -> dict:
    """Diccionario de variables de fecha listas para sustituir en los textos.
    Todos los valores son strings."""
    n = ahora(tz)
    dia = n.day
    mes_nombre = _MESES_ES[n.month - 1]
    dia_semana = _DIAS_ES[n.weekday()]
    anio = str(n.year)
    larga = f"{dia_semana} {dia} de {mes_nombre} de {anio}"
    actual = f"{dia} de {mes_nombre} de {anio}"
    corta = n.strftime("%d/%m/%Y")
    hora = n.strftime("%H:%M")

    return {
        # Principal: "28 de mayo de 2026"
        "fecha_actual": actual,
        "fecha": actual,
        "fecha_hoy": actual,
        "hoy": actual,
        # Variantes
        "fecha_corta": corta,          # 28/05/2026
        "fecha_larga": larga,          # jueves 28 de mayo de 2026
        "dia": f"{dia:02d}",
        "dia_numero": str(dia),
        "mes": mes_nombre,
        "mes_numero": f"{n.month:02d}",
        "anio": anio,
        "año": anio,
        "dia_semana": dia_semana,
        "hora": hora,
        "fecha_hora": f"{actual} {hora}",
        "timestamp": n.isoformat(),
        "iso": n.isoformat(),
    }


# Nombres reservados (para no listarlos como variables "requeridas" del template)
RESERVED_DATE_KEYS = set(build_date_vars())


def fecha_payload(tz: str = DEFAULT_TZ) -> dict:
    """Respuesta JSON para el endpoint /api/mc/fecha."""
    data = build_date_vars(tz)
    data["success"] = True
    data["tz"] = tz
    return data
