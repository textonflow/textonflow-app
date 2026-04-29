"""
main.py — Punto de entrada de TextOnFlow API.
Orquesta la app: setup, middlewares, directorios, startup y routers.
Toda la lógica de negocio vive en los módulos especializados.
"""
import logging
import os

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import init_db
from startup import run_startup

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ─── App FastAPI ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="TextOnFlow API",
    description=(
        "API pública de TextOnFlow — personalización dinámica de imágenes para ManyChat.\n\n"
        "**Autenticación:** Bearer JWT (`Authorization: Bearer <token>`) obtenido en `/api/auth/login`.\n\n"
        "**Uso rápido:** `POST /generate-multi` con el JSON exportado desde el editor."
    ),
    version="7.0.0",
    contact={"name": "TextOnFlow Support", "url": "https://textonflow.com", "email": "hola@textonflow.com"},
    license_info={"name": "Privativo — solo clientes TextOnFlow"},
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_tags=[
        {"name": "render",    "description": "Generación y renderizado de imágenes"},
        {"name": "projects",  "description": "Proyectos guardados del usuario"},
        {"name": "auth",      "description": "Autenticación y registro"},
        {"name": "user",      "description": "Perfil y uso del usuario"},
        {"name": "admin",     "description": "Panel de superadministrador"},
        {"name": "webhooks",  "description": "Webhooks de salida por usuario"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Base de datos ────────────────────────────────────────────────────────────
init_db()

# ─── Directorios de almacenamiento persistente ────────────────────────────────
# STORAGE_PATH puede apuntar a un Railway Volume (/mnt/storage)
# o a static/temp como fallback local (se borra al reiniciar).
STORAGE_DIR       = os.getenv("STORAGE_PATH",         os.path.join("static", "temp"))
TIMER_TEMPLATES_DIR = os.getenv("TIMER_TEMPLATES_PATH", os.path.join(STORAGE_DIR, "timers"))
TEMPLATES_API_DIR   = os.getenv("TEMPLATES_API_PATH",   os.path.join(STORAGE_DIR, "api_templates"))
TIMER_ACCESS_DIR    = os.path.join(TIMER_TEMPLATES_DIR, "access")

for _dir in [STORAGE_DIR, TIMER_TEMPLATES_DIR, TEMPLATES_API_DIR, TIMER_ACCESS_DIR,
             "output", "fonts", "static"]:
    os.makedirs(_dir, exist_ok=True)

# ─── Tareas de arranque: auto-update estáticos + minificación JS ──────────────
run_startup()

# ─── Archivos estáticos montados ─────────────────────────────────────────────
app.mount("/fonts",  StaticFiles(directory="fonts"),  name="fonts")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── Routers ──────────────────────────────────────────────────────────────────
from routers.users  import users_router;  app.include_router(users_router)
from routers.admin  import admin_router;  app.include_router(admin_router)
from routers.render import render_router; app.include_router(render_router)
from routers.ai     import ai_router;     app.include_router(ai_router)
from routers.batch  import batch_router;  app.include_router(batch_router)
from routers.pages  import pages_router;  app.include_router(pages_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
