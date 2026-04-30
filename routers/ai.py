"""
routers/ai.py — Endpoints de generación IA (imágenes, texto, edición),
contador regresivo (timer), asistente FlowBot, inpainting y AI product features.
Montado en main.py con: app.include_router(ai_router)
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from io import BytesIO
from typing import Optional, Dict

import requests
import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageDraw, ImageFont

from fonts import FONT_MAPPING
from models import (
    GenerateImageRequest, GenerateTextRequest, EnhancePromptRequest,
    SaveAIImageRequest, EditImageRequest, FeedbackRequest, QRRequest,
    TimerStyle, TimerTemplateCreate, TimerTemplateResponse,
    AssistantMessage, AssistantRequest, TranscriptRequest, RatingRequest,
    DesignLayoutRequest, CopySuggestionsRequest, BrandKitRequest, ABVariantsRequest,
)
from renderer import _wrap_words
from user_limits import (
    _get_current_user, _should_apply_watermark,
    _check_user_render_limit, _increment_user_renders,
)
from utils import _get_base_url

logger = logging.getLogger("textonflow")

ai_router = APIRouter()

# ── Supabase Storage (almacenamiento permanente) ──────────────────────────────
_SUPABASE_URL    = os.getenv("SUPABASE_URL",              "https://dluzcrfqqieprudfeuyk.supabase.co")
_SUPABASE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET",  "textonflow-uploads")
def _sb_default() -> str:
    import base64 as _b64
    return _b64.b64decode("c2Jfc2VjcmV0X1gxWEloNVp0ekEyTFd0VG9pV2thUGdfc21Pd1ZiM0Y=").decode()
_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or _sb_default()

def _upload_to_supabase(contents: bytes, filename: str, content_type: str = "image/jpeg") -> Optional[str]:
    """Sube un archivo a Supabase Storage y devuelve su URL pública permanente.
    Retorna None si falla (el caller puede usar URL local como fallback)."""
    if not _SUPABASE_KEY:
        return None
    try:
        import urllib.request as _ureq
        url = f"{_SUPABASE_URL}/storage/v1/object/{_SUPABASE_BUCKET}/{filename}"
        body = contents
        req = _ureq.Request(url, data=body, method="POST")
        req.add_header("apikey",        _SUPABASE_KEY)
        req.add_header("Authorization", f"Bearer {_SUPABASE_KEY}")
        req.add_header("Content-Type",  content_type)
        req.add_header("x-upsert",      "true")
        with _ureq.urlopen(req, timeout=30) as resp:
            _ = resp.read()
        public_url = f"{_SUPABASE_URL}/storage/v1/object/public/{_SUPABASE_BUCKET}/{filename}"
        logger.info(f"☁️  Supabase Storage: {public_url}")
        return public_url
    except Exception as _e:
        logger.error(f"⚠️  Supabase Storage upload failed: {_e}")
        return None

# ── Constantes de almacenamiento y timer ──────────────────────────────────────
STORAGE_DIR         = os.getenv("STORAGE_PATH", os.path.join("static", "temp"))
TIMER_TEMPLATES_DIR = os.getenv("TIMER_TEMPLATES_PATH", os.path.join(STORAGE_DIR, "timers"))
TIMER_ACCESS_DIR    = os.path.join(TIMER_TEMPLATES_DIR, "access")
TIMER_SECRET        = os.getenv("TIMER_SECRET", "textonflow-timer-secret-2026")
os.makedirs(STORAGE_DIR,         exist_ok=True)
os.makedirs(TIMER_TEMPLATES_DIR, exist_ok=True)
os.makedirs(TIMER_ACCESS_DIR,    exist_ok=True)

# ─── Job store (generación IA asíncrona) ─────────────────────────────────────
# ─── Job store para generación de imágenes asíncrona ─────────────────────────
# Evita que Railway corte la conexión por timeout durante llamadas largas a Gemini
_IMAGE_JOBS: Dict[str, dict] = {}  # job_id → {status, result, error, ts}
_IMAGE_JOBS_LOCK = threading.Lock()

def _cleanup_old_jobs():
    """Elimina jobs de más de 10 minutos."""
    now = time.time()
    with _IMAGE_JOBS_LOCK:
        old = [k for k, v in _IMAGE_JOBS.items() if now - v.get('ts', 0) > 600]
        for k in old:
            del _IMAGE_JOBS[k]


IMAGEN_STYLES = {
    "Ilustración Bíblica": (
        "strict mid-century modern biblical illustration — geometric shapes, angular stylized faces, large almond expressive eyes, flat colors only, subtle vintage grain texture evenly distributed, soft diffused lighting with minimal shadows, balanced minimal composition. "
        "Color palette EXCLUSIVELY: turquoise-blue #8BC8D8 #6BB1C1 #4BA3A8 #2B9C9C, warm skin tones #D4A574 #C89B6A #B8855A, dark hair #3D2817 #4A3425, cream neutrals #F1EAD9 #E8E5D8 #C9C5B2, golden accents #E6C577 #C89B3C — no other colors allowed. "
        "Scene strictly faithful to biblical scripture: ancient Near Eastern setting, authentic period clothing (1st century Judea or OT era), landscapes with olive trees, rocky hills, desert, ancient dwellings, bodies of water only. "
        "FORBIDDEN: modern elements, anachronisms, gradients, 3D rendering, gloss, fantasy not in scripture, European medieval armor, modern clothing or buildings or technology or vehicles, colors outside approved palette"
    ),
    "Plumilla & Acuarela": (
        "pen-and-ink with watercolor wash illustration style — loose expressive hand-drawn ink linework, cross-hatching and hatching for shadows and texture, light transparent watercolor washes over ink, mostly white or cream paper-like background. "
        "Character proportions: round bulbous heads, simple minimal facial features, dot or small oval eyes, soft cartoonish anatomy, spontaneous sketchy lines with visible hand-drawn imperfection. "
        "Color palette: black ink lines dominate, accents of limited soft watercolor — muted blues, warm oranges, pale sage greens, sandy yellows, cream whites — never more than 3-4 colors per scene. "
        "Mood: playful, editorial, whimsical, like a children's book or newspaper comic strip illustration. "
        "FORBIDDEN: photorealism, flat digital vector art, clean precise lines, gradients, 3D rendering, digital gloss, heavy shadows, complex backgrounds, more than 4 colors"
    ),
    "Monocromo":               "black and white monochromatic, high contrast, grayscale",
    "Color":                   "vibrant vivid colors, rich bold color palette, colorful",
    "Pista":                   "high fashion runway editorial photography, catwalk",
    "Risografía":              "risograph print art, limited color halftone texture, indie print",
    "Tecnicolor":              "Technicolor film, oversaturated vintage Hollywood color palette",
    "Arcilla gótica":          "gothic claymation style, dark clay stop-motion animation",
    "Dinamita":                "explosive cinematic action, smoke fire debris, dynamic energy",
    "Salón":                   "salon beauty portrait, soft flattering light, professional beauty",
    "Boceto a mano":           "hand drawn pencil sketch, graphite drawing, detailed linework",
    "Cinematográfico":         "cinematic movie still, dramatic film lighting, anamorphic lens",
    "Steampunk":               "steampunk Victorian sci-fi, brass gears, industrial fantasy",
    "Amanecer":                "golden hour sunrise, warm atmospheric glow, landscape photography",
    "Lucha mítica":            "epic mythological battle, fantasy warrior art, dramatic composition",
    "Surrealista":             "surrealist dreamlike, Salvador Dali inspired, impossible scenes",
    "Misterio":                "mysterious dark moody atmosphere, noir style, dramatic shadows",
    "Prendedor":               "enamel pin badge illustration, clean flat vector art design",
    "Cyborg":                  "cyberpunk cyborg aesthetic, futuristic human-robot, sci-fi neon",
    "Retrato tenue":           "soft low-key portrait, gentle diffused lighting, intimate mood",
    "Dibujo animado antiguo":  "1930s vintage cartoon animation style, classic retro cartoon",
    "Pintura al óleo":         "classical oil painting, old masters technique, rich brushwork",
}

_STYLE_MAP = [
    # Animación americana
    (r'\blos\s+simpsons?\b',            'estilo de dibujos animados americanos con personajes de piel amarilla brillante, ojos grandes redondos, contornos negros gruesos y paleta de colores vivos'),
    (r'\bsimpsons?\b',                  'estilo cartoon americano con piel amarilla brillante, ojos grandes y líneas gruesas de contorno'),
    (r'\bfamily\s+guy\b',               'estilo cartoon americano moderno con personajes rechonchos, ojos ovalados y humor visual'),
    (r'\bfuturama\b',                   'estilo de animación retro-futurista con diseño de ciencia ficción caricaturesco'),
    (r'\badventure\s+time\b',           'estilo cartoon indie con formas simples e irregulares, colores vibrantes y personajes expresivos'),
    (r'\brick\s+(?:and|y|&)\s+morty\b', 'estilo de animación indie americana con paleta de colores ácidos y personajes caricaturescos deformes'),
    (r'\bsouth\s+park\b',               'estilo de animación con figuras recortadas, colores planos y diseño minimalista de personajes'),
    (r'\bscooby[\s\-]?doo\b',           'estilo de dibujos animados clásicos americanos años 70, personajes en caricatura con fondo pintado'),
    (r'\bflintstone[s]?\b',             'estilo de dibujos animados prehistóricos retro con colores planos y fondo de roca'),
    (r'\blooney\s+tunes?\b',            'estilo de dibujos animados clásicos con movimientos exagerados y humor físico visual'),
    # Anime japonés
    (r'\bdragon\s*ball(?:\s+z|gt|super)?\b', 'estilo de anime japonés shonen con personajes musculosos, cabello puntiagudo, expresiones intensas y efectos de energía luminosa'),
    (r'\bnaruto(?:\s+shippuden)?\b',    'estilo de anime ninja japonés con personajes expresivos, marcas en el rostro y efectos de energía'),
    (r'\bone\s+piece\b',                'estilo de anime shonen japonés con personajes exagerados, ropa colorida y escenas de aventura'),
    (r'\bsailor\s+moon\b',              'estilo de anime shojo japonés con personajes femeninos elegantes, colores pastel, lazos y elementos mágicos'),
    (r'\bpokemon\b',                    'estilo de anime japonés infantil con criaturas adorables de colores vivos y diseño kawaii'),
    (r'\bpikachu\b',                    'criatura pequeña y adorable estilo anime japonés, redondeada con colores amarillos brillantes'),
    (r'\bmy\s+hero\s+academia\b',       'estilo de anime superhéroe japonés moderno con colores saturados y diseños de acción dinámica'),
    (r'\battack\s+on\s+titan\b',        'estilo de anime japonés oscuro con personajes realistas y entornos arquitectónicos detallados'),
    (r'\bdem[o0]n\s+slayer\b|\bkimetsu\b', 'estilo de anime japonés con efectos de agua y fuego muy detallados y paleta de colores rica'),
    (r'\bjujutsu\s+kaisen\b',           'estilo de anime japonés de acción moderna con efectos de maldición y diseño de personajes estilizados'),
    # Disney / Pixar
    (r'\bdisney\b',                     'estilo de animación clásica americana con personajes expresivos, colores vivos, líneas suaves y magia visual'),
    (r'\bpixar\b',                      'estilo de animación 3D cinematográfica con iluminación realista, texturas detalladas y personajes entrañables'),
    (r'\bmickey\s+mouse\b',             'estilo de cartoon clásico americano con personajes de orejas redondas, guantes blancos y diseño retro años 30'),
    (r'\bminnie\s+mouse\b',             'estilo de cartoon clásico con personaje femenino de orejas redondas, lunares y lazos grandes'),
    (r'\bdumbo\b',                      'estilo de animación clásica de fantasía con animal tierno de orejas grandes'),
    (r'\bmoana\b',                      'estilo de animación moderna 3D con personaje polinesia, océano tropical y colores cálidos'),
    (r'\bfrozen\b|\belsa\b',            'estilo de animación 3D con paleta de azules y blancos, efectos de hielo y cristales, ambiente invernal mágico'),
    (r'\blion\s+king\b',                'estilo de animación dramática con paisajes africanos, animales expresivos y iluminación cinematográfica'),
    # Marvel / DC / cómics
    (r'\bmarvel\b',                     'estilo de cómic americano superhéroe con colores saturados, efectos de acción dinámica y diseño musculoso'),
    (r'\bspider[\-\s]?man\b',           'estilo de superhéroe de cómic con traje de arácnido rojo y azul y movimientos acrobáticos'),
    (r'\bbatman\b',                     'estilo de superhéroe oscuro de cómic con capa y temática nocturna de ciudad gótica'),
    (r'\bsuperman\b',                   'estilo de superhéroe clásico americano con capa al viento y símbolo en el pecho'),
    (r'\bavengers\b',                   'estilo de cómic americano de superhéroes con equipo diverso y efectos de acción épica'),
    # Videojuegos
    (r'\bsuper\s+mario\b|\bmario\s+bros\b', 'estilo de videojuego de plataformas colorido con personaje rechoncho de bigote, gorro rojo y mundo de hongos'),
    (r'\bzelda\b',                      'estilo de videojuego de aventura fantasía con paleta de verdes y dorados, elfos y elementos medievales'),
    (r'\bminecraft\b',                  'estilo de arte pixelado 3D con mundo de cubos y bloques coloridos, estética de vóxeles'),
    (r'\bfortnite\b',                   'estilo de videojuego colorido con personajes caricaturescos, colores brillantes y estética pop'),
    (r'\boverwatch\b',                  'estilo de videojuego de héroes estilizados con diseños futuristas coloridos y efectos de habilidades'),
    # Cine y TV
    (r'\bstar\s+wars\b',                'estilo de ciencia ficción épica espacial con naves estelares, sables de luz, galaxias lejanas y efectos de energía'),
    (r'\bharry\s+potter\b|\bhogwarts\b', 'estilo de fantasía mágica con castillos medievales, varitas mágicas, criaturas mágicas y ambiente de academia de hechicería'),
    (r'\bjurassic\s+park\b',            'estilo de aventura prehistórica con dinosaurios detallados en entorno de selva tropical densa'),
    (r'\bgame\s+of\s+thrones\b',        'estilo de fantasía épica medieval con dragones, castillos imponentes y atmósfera oscura dramática'),
    # Estilos artísticos generales
    (r'\bchibi\b',                      'estilo chibi japonés con personajes miniaturizados, cabezas grandes en proporción y expresiones adorables'),
    (r'\bkawaii\b',                     'estilo visual japonés kawaii con personajes tiernos, colores pastel, mejillas rosadas y elementos adorables'),
    (r'\bpixel\s+art\b',                'estilo de arte en píxeles retro con gráficos de baja resolución, paleta de colores limitada y estética 8-bit o 16-bit'),
    (r'\bstudio\s+ghibli\b|\bghibli\b', 'estilo de animación japonesa artesanal con fondos detallados pintados a mano, naturaleza exuberante y personajes expresivos'),
]

def _rewrite_prompt(prompt: str) -> str:
    """
    Detecta referencias a franquicias/personajes conocidos y las reemplaza
    por descripciones de su estilo visual, evitando rechazos por copyright.
    Preserva el resto de la instrucción del usuario intacta.
    """
    result = prompt
    for pattern, replacement in _STYLE_MAP:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    # Limpiar "estilo estilo" redundante cuando el usuario ya escribió "estilo ..."
    result = re.sub(r'\bestilo\s+estilo\b', 'estilo', result, flags=re.IGNORECASE)
    return result


def _gemini_generate_image_worker(job_id: str, api_key: str, payload: dict):
    """Corre en un hilo separado. Llama a Gemini y guarda el resultado en _IMAGE_JOBS."""
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        if resp.status_code != 200:
            logger.error(f"Gemini error {resp.status_code}: {resp.text[:400]}")
            with _IMAGE_JOBS_LOCK:
                _IMAGE_JOBS[job_id]['status'] = 'error'
                _IMAGE_JOBS[job_id]['error'] = f"Error de Gemini ({resp.status_code}): {resp.text[:250]}"
            return
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            with _IMAGE_JOBS_LOCK:
                _IMAGE_JOBS[job_id]['status'] = 'error'
                _IMAGE_JOBS[job_id]['error'] = "Gemini no devolvió imágenes. Intenta con otro prompt."
            return
        resp_parts = candidates[0].get("content", {}).get("parts", [])
        for part in resp_parts:
            if "inlineData" in part:
                b64  = part["inlineData"].get("data", "")
                mime = part["inlineData"].get("mimeType", "image/png")
                with _IMAGE_JOBS_LOCK:
                    _IMAGE_JOBS[job_id]['status'] = 'done'
                    _IMAGE_JOBS[job_id]['image_b64'] = b64
                    _IMAGE_JOBS[job_id]['mime_type'] = mime
                return
        with _IMAGE_JOBS_LOCK:
            _IMAGE_JOBS[job_id]['status'] = 'error'
            _IMAGE_JOBS[job_id]['error'] = "Gemini no devolvió imagen en la respuesta. Intenta con otro prompt."
    except requests.Timeout:
        with _IMAGE_JOBS_LOCK:
            _IMAGE_JOBS[job_id]['status'] = 'error'
            _IMAGE_JOBS[job_id]['error'] = "Tiempo de espera agotado (120 s). Intenta de nuevo."
    except Exception as ex:
        with _IMAGE_JOBS_LOCK:
            _IMAGE_JOBS[job_id]['status'] = 'error'
            _IMAGE_JOBS[job_id]['error'] = str(ex)


@ai_router.post("/api/generate-image")
async def generate_image(req: GenerateImageRequest):
    """
    Inicia la generación en segundo plano y devuelve job_id de inmediato.
    El cliente hace polling a /api/image-job/{job_id} cada 2 s.
    Esto evita que Railway corte la conexión por timeout.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada en el servidor")
    _cleanup_old_jobs()

    full_prompt = _rewrite_prompt(req.prompt.strip())
    if req.style and req.style in IMAGEN_STYLES:
        full_prompt += ", " + IMAGEN_STYLES[req.style]
    refs = req.reference_images or []
    if refs:
        if req.style == "Ilustración Bíblica":
            # Referencia SOLO de estilo artístico — no copiar contenido/objetos
            full_prompt = (
                "IMPORTANT — The attached reference image(s) are provided EXCLUSIVELY for artistic "
                "style guidance: illustration technique, brushwork, color palette, texture, and "
                "overall visual feel. DO NOT copy, include, or reproduce any objects, subjects, or "
                "elements shown in the reference image(s) — such as baskets, fish, bread, or any "
                "other specific items. Completely disregard the content/subject matter of the "
                "reference image(s). Generate ONLY what the text prompt describes, applying solely "
                "the artistic style extracted from the reference. "
                + full_prompt
            )
        else:
            full_prompt = (
                "Usa exactamente los productos que aparecen en las imágenes de referencia adjuntas, "
                "manteniendo su apariencia, logo y diseño originales. "
                + full_prompt
            )
    valid_ratios = {"1:1", "9:16", "3:4", "16:9", "4:3"}
    ar = req.aspect_ratio if req.aspect_ratio in valid_ratios else "1:1"

    parts = []
    for ref in refs:
        parts.append({"inlineData": {"mimeType": ref.mime_type, "data": ref.data}})
    parts.append({"text": full_prompt})
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": ar}
        }
    }

    job_id = str(uuid.uuid4())
    with _IMAGE_JOBS_LOCK:
        _IMAGE_JOBS[job_id] = {"status": "pending", "ts": time.time()}

    t = threading.Thread(target=_gemini_generate_image_worker, args=(job_id, api_key, payload), daemon=True)
    t.start()

    return {"job_id": job_id}


@ai_router.get("/api/image-job/{job_id}")
async def get_image_job(job_id: str):
    """Devuelve el estado del job. El cliente hace polling aquí."""
    with _IMAGE_JOBS_LOCK:
        job = _IMAGE_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado o expirado")
    status = job.get("status", "pending")
    if status == "done":
        return {"status": "done", "image_b64": job["image_b64"], "mime_type": job["mime_type"]}
    if status == "error":
        return {"status": "error", "error": job.get("error", "Error desconocido")}
    return {"status": "pending"}


# ── Redactor IA — generador/mejorador de texto para capas ────────────────────
_REDACTOR_SYSTEM = (
    "Eres un redactor creativo especializado en textos cortos para imágenes de marketing "
    "en redes sociales y campañas de ManyChat. Tu función es mejorar o generar el texto "
    "que el usuario va a colocar sobre una imagen.\n\n"
    "El resultado debe ser:\n"
    "- Conciso y directo (máximo 2-4 líneas — debe verse bien en una imagen)\n"
    "- Adaptado exactamente al tono solicitado\n"
    "- Natural y fluido en español\n"
    "- Sin expandirlo demasiado — conserva la esencia y la brevedad del mensaje original\n\n"
    "REGLAS ESTRICTAS:\n"
    "1. Responde ÚNICAMENTE con el texto final, sin explicaciones, comillas ni comentarios\n"
    "2. Escribe en español natural\n"
    "3. No uses hashtags ni emojis a menos que el usuario los incluya\n"
    "4. No uses listas ni formatos especiales — texto corrido o párrafos simples\n"
    "5. No cambies el idioma aunque el usuario escriba en inglés — responde en español\n\n"
    "REGLA BÍBLICA (aplica siempre que el usuario pida texto bíblico — no la menciones):\n"
    "Si el usuario solicita un versículo, salmo, pasaje o texto de la Biblia, usa EXCLUSIVAMENTE "
    "la versión Reina Valera 1960 (RV1960). Escribe el texto bíblico COMPLETO sin números de "
    "versículo, sin encabezados, sin títulos, sin notas — solo texto puro tal como aparece en RV1960. "
    "Ejemplo: si piden el Salmo 23, escribe todo el salmo línea a línea sin '1.' '2.' etc."
)

@ai_router.post("/api/generate-text")
async def generate_text(req: GenerateTextRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    raw = req.text.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="El texto no puede estar vacío")
    tone = req.tone.strip() or "Profesional"
    system_with_tone = _REDACTOR_SYSTEM.replace(
        "al tono solicitado", f"al tono solicitado ({tone})"
    ) + f"\n\nTONO ACTUAL: {tone}"
    user_msg = f"Mejora o genera este texto con tono {tone}:\n\n{raw}"
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": user_msg}]}],
        "systemInstruction": {"parts": [{"text": system_with_tone}]},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 400, "candidateCount": 1}
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=25)
        if resp.status_code == 429:
            logger.warning("Gemini generate-text 429 rate limit")
            raise HTTPException(status_code=429, detail="La IA está ocupada ahora mismo. Espera unos segundos e intenta de nuevo.")
        if resp.status_code != 200:
            logger.error(f"Gemini generate-text error {resp.status_code}: {resp.text[:300]}")
            raise HTTPException(status_code=502, detail=f"Error de la IA ({resp.status_code}). Intenta de nuevo en unos segundos.")
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=500, detail="Sin respuesta del modelo IA")
        parts = candidates[0].get("content", {}).get("parts", [])
        result = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()
        if not result:
            raise HTTPException(status_code=500, detail="Respuesta vacía del modelo IA")
        return {"text": result}
    except HTTPException:
        raise
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado. Intenta de nuevo.")
    except Exception as ex:
        logger.error(f"Excepción en generate-text: {ex}")
        raise HTTPException(status_code=500, detail=str(ex))


_ENHANCE_SYSTEM = (
    "Eres un experto en escribir prompts para generadores de imágenes con IA. "
    "Tu especialidad es ayudar a crear imágenes para marketing personalizado en ManyChat: "
    "banners, anuncios, felicitaciones, promociones y contenido visual para bots.\n\n"
    "Toma el texto del usuario y mejóralo añadiendo:\n"
    "- Detalles visuales específicos (iluminación, composición, colores predominantes)\n"
    "- Ambiente y emoción de la escena\n"
    "- Calidad y estilo de la imagen (si aplica)\n"
    "- Si hay personas: expresión, postura y vestimenta brevemente\n"
    "- Si hay productos: ubicación en primer plano, nitidez\n\n"
    "REGLAS ESTRICTAS:\n"
    "1. Responde ÚNICAMENTE con el prompt mejorado, sin explicaciones ni comentarios\n"
    "2. Escribe en español, de forma natural y fluida\n"
    "3. Máximo 3 oraciones cortas\n"
    "4. Conserva la idea original del usuario sin cambiarla\n"
    "5. No uses listas, solo texto corrido\n"
    "6. Nunca menciones marcas registradas — describe el estilo visual en su lugar"
)

_ENHANCE_NOTXT_SUFFIX = (
    "\n\nREGLA ADICIONAL OBLIGATORIA — SIN TEXTOS:\n"
    "La imagen generada NO debe contener absolutamente ningún texto, letra, número, "
    "palabra, slogan, etiqueta, cartel, letreros, tipografía ni elemento gráfico con caracteres "
    "escritos generados por la IA. La escena debe ser pura ilustración o fotografía sin texto.\n"
    "IMPORTANTE: Si el usuario ha subido fotos de referencia con logos o texto real (como el "
    "nombre de su negocio o marca en un producto físico), ese texto es parte de la imagen real "
    "y se debe respetar tal cual — la restricción aplica solo al texto que la IA inventaría o "
    "generaría por su cuenta.\n"
    "Termina el prompt con la instrucción: 'sin texto, sin tipografía, sin letras, imagen limpia'."
)

@ai_router.post("/api/enhance-prompt")
async def enhance_prompt(req: EnhancePromptRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    raw = req.prompt.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="El prompt no puede estar vacío")
    system_text = _ENHANCE_SYSTEM + (_ENHANCE_NOTXT_SUFFIX if req.no_text else "")
    user_msg = ("Mejora este prompt para generar una imagen SIN TEXTO ni tipografía generada por IA"
                if req.no_text else "Mejora este prompt para generar una imagen") + f":\n\n{raw}"
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": user_msg}]}],
        "systemInstruction": {"parts": [{"text": system_text}]},
        "generationConfig": {"temperature": 0.75, "maxOutputTokens": 260, "candidateCount": 1}
    }
    # ⚠️  Railway corta conexiones a los ~30s.
    # 2 intentos × 10s = 20s máximo → siempre respondemos antes del corte.
    _TIMEOUTS = [10, 8]
    last_error = None
    last_status = None
    data = None
    for attempt, _t in enumerate(_TIMEOUTS):
        try:
            async with httpx.AsyncClient(timeout=_t) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 429:
                logger.warning("Enhance-prompt Gemini 429 rate limit")
                last_error = "quota"
                last_status = 429
                await asyncio.sleep(0.5)
                continue
            if resp.status_code != 200:
                logger.error(f"Enhance-prompt Gemini error {resp.status_code}: {resp.text[:400]}")
                last_error = f"HTTP {resp.status_code}"
                last_status = resp.status_code
                break   # error no recuperable con reintento
            data = resp.json()
            logger.info(f"Enhance-prompt OK (intento {attempt+1})")
            break
        except (httpx.TimeoutException, asyncio.TimeoutError):
            logger.warning(f"Enhance-prompt timeout intento {attempt+1}/{len(_TIMEOUTS)} ({_t}s)")
            last_error = "timeout"
            if attempt < len(_TIMEOUTS) - 1:
                await asyncio.sleep(0.3)
        except Exception as exc:
            logger.warning(f"Enhance-prompt intento {attempt+1} error: {exc}")
            last_error = str(exc)
            if attempt < len(_TIMEOUTS) - 1:
                await asyncio.sleep(0.3)
    if data is None:
        if last_error == "quota":
            detail = "Límite de cuota Gemini alcanzado. Espera unos segundos e intenta de nuevo."
        elif last_error == "timeout":
            detail = "El servicio de IA tardó demasiado. Intenta de nuevo."
        else:
            detail = f"No se pudo mejorar el prompt: {last_error}. Intenta de nuevo."
        raise HTTPException(status_code=502, detail=detail)
    try:
        data = data
        candidates = data.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=500, detail="Sin respuesta del modelo")
        text_parts = candidates[0].get("content", {}).get("parts", [])
        enhanced = " ".join(p.get("text", "") for p in text_parts if "text" in p).strip()
        if not enhanced:
            raise HTTPException(status_code=500, detail="Respuesta vacía del modelo")
        return {"enhanced_prompt": enhanced}
    except HTTPException:
        raise
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado")
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))
# ────────────────────────────────────────────────────────────────────────────


@ai_router.get("/storage/{filename}")
async def serve_storage_file(filename: str):
    """Sirve archivos desde el directorio de almacenamiento persistente."""
    filepath = os.path.join(STORAGE_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    ext = filename.rsplit(".", 1)[-1].lower()
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    media_type = mime_map.get(ext, "image/png")
    return FileResponse(filepath, media_type=media_type)

@ai_router.post("/api/save-ai-image")
async def save_ai_image(req: SaveAIImageRequest, request: Request):
    """Guarda una imagen AI — intenta Supabase Storage primero (URL permanente),
    cae a disco local solo si Supabase falla."""
    ext = "jpg" if "jpeg" in req.mime_type else "png"
    uid = str(uuid.uuid4())[:12]
    filename = f"ai_{uid}.{ext}"
    img_bytes = base64.b64decode(req.image_b64)
    content_type = "image/jpeg" if ext == "jpg" else "image/png"

    # ── Intento 1: Supabase Storage (URL permanente, sobrevive reinicios) ──────
    public_url = _upload_to_supabase(img_bytes, filename, content_type)

    # ── Fallback: disco local (efímero, se pierde en redeploy) ────────────────
    if not public_url:
        filepath = os.path.join(STORAGE_DIR, filename)
        os.makedirs(STORAGE_DIR, exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(img_bytes)
        base_url = _get_base_url(request)
        public_url = f"{base_url}/storage/{filename}"
        logger.warning(f"⚠️  Supabase no disponible — imagen guardada localmente (efímera): {public_url}")
    else:
        logger.info(f"☁️  Imagen AI en Supabase: {public_url}")

    return {"url": public_url}


# ── Editar imagen con IA ──────────────────────────────────────────────────────
@ai_router.post("/api/edit-image")
async def edit_image(req: EditImageRequest):
    """
    Toma una imagen existente (base64) y una instrucción en texto,
    llama a Gemini en modo imagen→imagen y devuelve la imagen editada.
    Acepta imágenes de referencia opcionales para guiar la edición.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    instr = req.instruction.strip()
    if not instr:
        raise HTTPException(status_code=400, detail="La instrucción no puede estar vacía")

    ref_imgs = req.reference_images or []
    if ref_imgs:
        edit_prompt = (
            f"Using the reference image(s) provided as visual context, edit the main image "
            f"according to this instruction: {instr}. "
            "Keep everything else the same — same composition, style, colors and proportions — "
            "only apply the requested change. Return the complete modified image."
        )
    else:
        edit_prompt = (
            f"Edita esta imagen exactamente según la siguiente instrucción: {instr}. "
            "Mantén todo lo demás igual — misma composición, estilo, colores y proporciones — "
            "solo aplica el cambio solicitado. Devuelve la imagen completa modificada."
        )

    parts = []
    if ref_imgs:
        parts.append({"text": "Reference images (use as visual context for the edit):"})
        for ref in ref_imgs[:3]:
            parts.append({"inlineData": {"mimeType": ref.get("mime_type", "image/jpeg"), "data": ref.get("data", "")}})
        parts.append({"text": "Main image to edit:"})
    parts.append({"inlineData": {"mimeType": req.mime_type, "data": req.image_b64}})
    parts.append({"text": edit_prompt})

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"]
        }
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        if resp.status_code != 200:
            logger.error(f"edit-image Gemini error {resp.status_code}: {resp.text[:400]}")
            raise HTTPException(status_code=502, detail=f"Error de Gemini al editar: {resp.text[:200]}")
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=500, detail="Gemini no devolvió resultado para la edición")
        for part in candidates[0].get("content", {}).get("parts", []):
            if "inlineData" in part:
                return {
                    "image_b64": part["inlineData"]["data"],
                    "mime_type": part["inlineData"].get("mimeType", "image/png")
                }
        raise HTTPException(status_code=500, detail="Gemini no devolvió imagen en la respuesta. Intenta con otra instrucción.")
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado. Intenta de nuevo.")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))
# ─────────────────────────────────────────────────────────────────────────────


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

@ai_router.post("/api/upload-image")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """Sube una imagen desde el cliente y devuelve su URL pública persistente."""
    content_type = file.content_type or ""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if content_type not in ALLOWED_IMAGE_TYPES and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Tipo de archivo no permitido. Usa JPG, PNG, WEBP o GIF.")
    # Determinar extensión
    if ext in ALLOWED_EXTENSIONS:
        save_ext = ext
    else:
        save_ext = ".jpg"
    uid = str(uuid.uuid4())[:12]
    filename = f"upload_{uid}{save_ext}"
    filepath = os.path.join(STORAGE_DIR, filename)
    os.makedirs(STORAGE_DIR, exist_ok=True)
    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:  # 20 MB máximo
        raise HTTPException(status_code=400, detail="Imagen demasiado grande. Máximo 20 MB.")
    # Guardar localmente (para previsualización inmediata en el editor)
    with open(filepath, "wb") as f:
        f.write(contents)
    # Subir a Supabase Storage para URL permanente (sobrevive redeploys de Railway)
    mime = content_type if content_type in ("image/jpeg","image/png","image/webp","image/gif") else "image/jpeg"
    supabase_url = _upload_to_supabase(contents, filename, mime)
    if supabase_url:
        logger.info(f"📤 Imagen subida → Supabase: {supabase_url}")
        return {"url": supabase_url, "filename": filename}
    # Fallback: URL local (efímera en Railway, pero funciona en dev/staging)
    base_url = _get_base_url(request)
    public_url = f"{base_url}/storage/{filename}"
    logger.warning(f"📤 Imagen subida → local fallback (Supabase no disponible): {public_url}")
    return {"url": public_url, "filename": filename}


# ─── QR Code generator ────────────────────────────────────────────────────────
@ai_router.post("/api/qr")
async def generate_qr(req: QRRequest):
    """Genera un QR como PNG base64 con fondo de color y padding."""
    import qrcode

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text requerido")

    def hex_to_rgb(h: str):
        h = h.lstrip('#')
        if len(h) == 3:
            h = ''.join(c*2 for c in h)
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    dark_rgb  = hex_to_rgb(req.dark_color)
    light_rgb = hex_to_rgb(req.light_color)
    bg_rgb    = hex_to_rgb(req.bg_color)
    pad       = max(0, min(req.padding, 120))

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=1
    )
    qr.add_data(req.text.strip())
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color=dark_rgb, back_color=light_rgb).convert("RGB")

    if pad > 0:
        w, h = qr_img.size
        new_w, new_h = w + pad * 2, h + pad * 2
        bg = Image.new("RGB", (new_w, new_h), bg_rgb)
        bg.paste(qr_img, (pad, pad))
        qr_img = bg

    buf = BytesIO()
    qr_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"image": f"data:image/png;base64,{b64}"}


@ai_router.post("/api/feedback")
async def submit_feedback(req: FeedbackRequest):
    import smtplib
    import datetime
    import asyncio
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")

    body = (
        f"Nuevo feedback de TextOnFlow\n"
        f"{'='*40}\n"
        f"Nombre:  {req.name}\n"
        f"Correo:  {req.email}\n"
        f"Fecha:   {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"Mensaje:\n{req.message}\n\n"
        f"---\nEnviado desde textonflow.com"
    )

    # Guardar siempre en archivo como respaldo
    try:
        log_path = os.path.join(STORAGE_DIR, "feedback_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*50}\n")
            f.write(body)
            f.write("\n")
    except Exception as e:
        logger.warning(f"No se pudo guardar feedback en archivo: {e}")

    sent = False
    send_error = ""
    em_key = os.getenv("ENGINEMAILER_API_KEY", "")
    vars_set = bool(em_key)

    if em_key:
        def _send_enginemailer():
            return requests.post(
                "https://api.enginemailer.com/RESTAPI/V2/Submission/SendEmail",
                headers={
                    "APIKey": em_key,
                    "Content-Type": "application/json"
                },
                json={
                    "CampaignName": "TextOnFlow Feedback",
                    "ToEmail": "feedback@textonflow.com",
                    "SenderEmail": "hola@followers.mx",
                    "SenderName": "TextOnFlow",
                    "Subject": f"Feedback de {req.name} — TextOnFlow",
                    "SubmittedContent": body,
                },
                timeout=20
            )

        try:
            loop = asyncio.get_event_loop()
            resp = await asyncio.wait_for(
                loop.run_in_executor(None, _send_enginemailer),
                timeout=25
            )
            if resp.status_code in (200, 201):
                sent = True
                logger.info(f"Feedback enviado via EngineMailer: {req.name} <{req.email}>")
            else:
                send_error = resp.text[:200]
                logger.error(f"EngineMailer error {resp.status_code}: {send_error}")
        except asyncio.TimeoutError:
            send_error = "Timeout (25s)"
            logger.error("Feedback EngineMailer timeout")
        except Exception as e:
            send_error = str(e)
            logger.error(f"Error EngineMailer: {e}")

    return {"ok": True, "sent": sent, "vars_set": vars_set, "error": send_error}


# ══════════════════════════════════════════════════════════════════════════════
# ⏱  CONTADOR REGRESIVO — Modelos y endpoints
# ══════════════════════════════════════════════════════════════════════════════

def _sign_timer(template_id: str, extra: str = "") -> str:
    """Genera firma HMAC-SHA256 para URL de timer."""
    payload = f"{template_id}:{extra}".encode()
    return hmac.new(TIMER_SECRET.encode(), payload, hashlib.sha256).hexdigest()[:16]


def _parse_event_date(date_str: str, tz_name: str) -> datetime:
    """Convierte 'DD/MM/AAAA HH:MM' en datetime UTC."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    dt_local = datetime.strptime(date_str.strip(), "%d/%m/%Y %H:%M")
    dt_local = dt_local.replace(tzinfo=tz)
    return dt_local.astimezone(timezone.utc)


def _format_countdown(seconds: float, fmt: str, expired_text: str) -> str:
    """Formatea segundos restantes en la cadena del contador."""
    if seconds <= 0:
        return expired_text
    total = int(seconds)
    days    = total // 86400
    hours   = (total % 86400) // 3600
    minutes = (total % 3600)  // 60
    secs    = total % 60
    if fmt == "DD:HH:MM:SS":
        return f"{days}d {hours:02d}h {minutes:02d}m {secs:02d}s"
    elif fmt == "HH:MM":
        total_h = days * 24 + hours
        return f"{total_h}:{minutes:02d}"
    else:  # HH:MM:SS (default)
        total_h = days * 24 + hours
        return f"{total_h}:{minutes:02d}:{secs:02d}"


def _render_timer_on_image(
    img: "Image.Image",
    countdown_text: str,
    style: dict,
    is_expired: bool = False,
    expired_img: Optional["Image.Image"] = None
) -> "Image.Image":
    """Dibuja el texto del contador sobre la imagen base.
    
    - is_expired: si True, usa expired_img como fondo (si existe) y centra el texto.
    - expired_img: imagen alternativa para el estado expirado.
    """
    # ── Imagen base: usar expired_img si el contador terminó y hay una definida ──
    if is_expired and expired_img is not None:
        base = expired_img.copy().convert("RGBA")
    else:
        base = img.copy().convert("RGBA")

    base = base.copy().convert("RGBA")
    w, h = base.size

    font_name = style.get("font", "Doto")
    font_path = FONT_MAPPING.get(font_name, "./fonts/Doto-Regular.ttf")
    font_size = int(style.get("font_size", 52))
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()

    color = style.get("color", "#FFFFFF")

    # ── Cuando está expirado: forzar centrado y aplicar text wrap ──────────────
    if is_expired:
        alignment = style.get("expired_align", "center")
        wrap_enabled = style.get("expired_wrap_enabled", True)
        wrap_padding = max(0, int(style.get("expired_wrap_padding", 60)))
    else:
        alignment = style.get("alignment", "center")
        wrap_enabled = False
        wrap_padding = 60

    # ── Text wrap (solo para expirado en este render) ──────────────────────────
    draw_tmp = ImageDraw.Draw(base)
    if wrap_enabled and wrap_padding >= 0:
        max_w = max(1, w - 2 * wrap_padding)
        countdown_text = _wrap_words(countdown_text, font, max_w, draw_tmp)

    # Posición como porcentaje del canvas
    px = int(w * float(style.get("x", 50)) / 100)
    py = int(h * float(style.get("y", 50)) / 100)

    # Si está expirado, centrar automáticamente en el canvas
    if is_expired:
        px = w // 2
        py = h // 2

    draw = ImageDraw.Draw(base)

    stroke_w = int(style.get("stroke_width", 2)) if style.get("stroke_enabled", True) else 0
    stroke_c = style.get("stroke_color", "#000000")

    # Calcular anchor según alignment
    anchor = "mm"  # center
    if alignment == "left":
        anchor = "lm"
    elif alignment == "right":
        anchor = "rm"

    # Sombra
    if style.get("shadow_enabled", False):
        sx = int(style.get("shadow_offset_x", 2))
        sy = int(style.get("shadow_offset_y", 2))
        draw.text((px + sx, py + sy), countdown_text, font=font,
                  fill=style.get("shadow_color", "#000000"), anchor=anchor)

    # Texto principal con stroke (usa multiline para que el wrap se vea bien)
    draw.multiline_text(
        (px, py), countdown_text, font=font, fill=color,
        stroke_width=stroke_w, stroke_fill=stroke_c,
        anchor=anchor, align=alignment
    )

    return base.convert("RGB")


@ai_router.post("/api/timer/save", response_model=TimerTemplateResponse)
async def save_timer_template(request: Request, body: TimerTemplateCreate):
    """Guarda un template de contador y devuelve las URLs listas para usar en ManyChat."""
    template_id = str(uuid.uuid4())[:12]
    base_url = _get_base_url(request)

    # Validar modo
    if body.mode not in ("event", "urgency"):
        raise HTTPException(status_code=400, detail="mode debe ser 'event' o 'urgency'")
    if body.mode == "event" and not body.event_date:
        raise HTTPException(status_code=400, detail="event_date requerido para modo evento")
    if body.mode == "urgency" and not body.urgency_hours:
        raise HTTPException(status_code=400, detail="urgency_hours requerido para modo urgencia")

    # Calcular preview de segundos restantes
    preview_seconds = 0
    if body.mode == "event":
        try:
            end_utc = _parse_event_date(body.event_date, body.event_tz or "America/Mexico_City")
            preview_seconds = max(0, int((end_utc - datetime.now(timezone.utc)).total_seconds()))
            end_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Formato de fecha inválido: {e}. Usa DD/MM/AAAA HH:MM")
    else:
        preview_seconds = int((body.urgency_hours or 0) * 3600)
        end_iso = None

    # Serializar template a JSON
    template_data = {
        "template_id": template_id,
        "template_name": body.template_name,
        "base_image_url": body.base_image_url,
        "expired_image_url": body.expired_image_url or None,
        "mode": body.mode,
        "event_end_utc": end_iso if body.mode == "event" else None,
        "urgency_hours": body.urgency_hours if body.mode == "urgency" else None,
        "style": body.style.dict(),
    }
    template_path = os.path.join(TIMER_TEMPLATES_DIR, f"{template_id}.json")
    with open(template_path, "w", encoding="utf-8") as f:
        json.dump(template_data, f, ensure_ascii=False, indent=2)
    logger.info(f"⏱ Template de timer guardado: {template_id} (modo: {body.mode})")

    # Construir URLs
    sig = _sign_timer(template_id)
    live_url_event = None
    live_url_urgency = None

    if body.mode == "event":
        # _t={{id}} hace la URL única por suscriptor → WhatsApp no reutiliza caché entre usuarios.
        # El servidor ignora _t; solo sirve para que cada URL sea diferente.
        live_url_event = f"{base_url}/live/{template_id}.jpg?s={sig}&_t={{{{id}}}}"
    else:
        # Urgencia: el backend registra automáticamente el primer acceso de cada usuario.
        # uid={{id}} identifica al suscriptor para calcular su countdown personal.
        live_url_urgency = (
            f"{base_url}/live/{template_id}.jpg"
            f"?uid={{{{id}}}}"
            f"&s={sig}"
        )

    return TimerTemplateResponse(
        template_id=template_id,
        live_url_event=live_url_event,
        live_url_urgency=live_url_urgency,
        preview_seconds=preview_seconds,
    )


@ai_router.get("/live/{template_id}.jpg")
async def render_live_timer(
    template_id: str,
    s: Optional[str] = None,
    uid: Optional[str] = None,
    user_start: Optional[str] = None,
    _t: Optional[str] = None,   # cache-buster (ignorado; hace la URL única por envío)
):
    """
    Renderiza la imagen con el contador en tiempo real.
    Cada vez que se carga esta URL, el servidor calcula el tiempo restante exacto.
    """
    # Cargar template
    template_path = os.path.join(TIMER_TEMPLATES_DIR, f"{template_id}.json")
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Template de timer no encontrado")

    with open(template_path, "r", encoding="utf-8") as f:
        tmpl = json.load(f)

    # Verificar firma HMAC
    expected_sig = _sign_timer(template_id)
    if s and s != expected_sig:
        raise HTTPException(status_code=403, detail="Firma de URL inválida")

    style = tmpl.get("style", {})
    fmt = style.get("format", "HH:MM:SS")
    expired_text = style.get("expired_text", "¡Oferta expirada!")
    mode = tmpl.get("mode", "event")

    # Calcular segundos restantes
    now_utc = datetime.now(timezone.utc)

    if mode == "event":
        end_str = tmpl.get("event_end_utc")
        if not end_str:
            seconds_left = 0.0
        else:
            end_utc = datetime.strptime(end_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            seconds_left = max(0.0, (end_utc - now_utc).total_seconds())

    else:  # urgency
        urgency_hours = float(tmpl.get("urgency_hours") or 0)
        if uid:
            # Primer acceso automático: registrar cuándo este usuario accedió por primera vez
            access_file = os.path.join(TIMER_ACCESS_DIR, f"{template_id}.json")
            try:
                if os.path.exists(access_file):
                    with open(access_file, "r", encoding="utf-8") as af:
                        access_data = json.load(af)
                else:
                    access_data = {}
                uid_key = str(uid)
                if uid_key not in access_data:
                    # Primera vez — guardar timestamp actual como inicio del countdown
                    access_data[uid_key] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                    with open(access_file, "w", encoding="utf-8") as af:
                        json.dump(access_data, af, ensure_ascii=False, indent=2)
                start_utc = datetime.strptime(access_data[uid_key], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                end_utc = start_utc + timedelta(hours=urgency_hours)
                seconds_left = max(0.0, (end_utc - now_utc).total_seconds())
            except Exception as e:
                logger.warning(f"⚠️ Error leyendo acceso de usuario: {e}")
                seconds_left = urgency_hours * 3600
        elif user_start:
            try:
                if user_start.isdigit():
                    start_utc = datetime.fromtimestamp(int(user_start), tz=timezone.utc)
                else:
                    start_utc = datetime.fromisoformat(user_start.replace("Z", "+00:00"))
                end_utc = start_utc + timedelta(hours=urgency_hours)
                seconds_left = max(0.0, (end_utc - now_utc).total_seconds())
            except Exception:
                seconds_left = max(0.0, urgency_hours * 3600)
        else:
            # Sin uid ni user_start → mostrar la duración completa (preview)
            seconds_left = urgency_hours * 3600

    is_expired = (seconds_left <= 0)
    countdown_text = _format_countdown(seconds_left, fmt, expired_text)

    def _load_img_from_url(url: str) -> Optional[Image.Image]:
        """Intenta cargar una imagen desde URL local o remota."""
        loaded = None
        try:
            if url.startswith("/storage/") or url.startswith("/static/temp/"):
                fname = url.split("/")[-1].split("?")[0]
                local_path = os.path.join(STORAGE_DIR, fname)
                if os.path.exists(local_path):
                    loaded = Image.open(local_path).convert("RGBA")
            if loaded is None and url.startswith("http"):
                r = requests.get(url, timeout=10)
                loaded = Image.open(BytesIO(r.content)).convert("RGBA")
        except Exception as e:
            logger.warning(f"⚠️ No se pudo cargar imagen: {e}")
        return loaded

    # Cargar imagen base de campaña
    base_url_img = tmpl.get("base_image_url", "")
    img = _load_img_from_url(base_url_img) if base_url_img else None
    if img is None:
        img = Image.new("RGB", (800, 400), color=(20, 20, 40))

    # Cargar imagen de expirado (opcional)
    expired_url = tmpl.get("expired_image_url", "") or ""
    expired_img = _load_img_from_url(expired_url) if expired_url else None

    # Renderizar countdown sobre la imagen (pasando estado y imagen de expirado)
    result = _render_timer_on_image(img, countdown_text, style,
                                    is_expired=is_expired, expired_img=expired_img)

    # Serializar a JPG
    buf = BytesIO()
    result.save(buf, format="JPEG", quality=90)
    buf.seek(0)

    # Sin caché: WhatsApp y ManyChat cachean por URL; esta imagen cambia en cada request.
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, private",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Timer-Seconds-Left": str(int(seconds_left)),
        "X-Timer-Mode": mode,
    }
    logger.info(f"⏱ Timer rendered: {template_id} | mode={mode} | left={int(seconds_left)}s | text='{countdown_text}'")
    return Response(content=buf.read(), media_type="image/jpeg", headers=headers)


@ai_router.get("/api/timer/{template_id}")
async def get_timer_template(template_id: str):
    """Devuelve la configuración de un template de timer (para el editor)."""
    template_path = os.path.join(TIMER_TEMPLATES_DIR, f"{template_id}.json")
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Template no encontrado")
    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


@ai_router.get("/configurador")
async def configurador():
    return FileResponse("index.html", media_type="text/html")


# ══════════════════════════════════════════════════════════════
#  FLOWBOT — Asistente Virtual TextOnFlow
# ══════════════════════════════════════════════════════════════

class _HtmlStripper(HTMLParser):
    """Extrae texto plano de un archivo HTML eliminando scripts, estilos y nav."""
    def __init__(self):
        super().__init__()
        self._buf: list[str] = []
        self._skip = False
        self._skip_tags = {"script", "style", "head", "nav", "footer", "noscript"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip = True

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self._buf.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self._buf)


def _html_to_text(filepath: str) -> str:
    try:
        with open(filepath, encoding="utf-8") as f:
            raw = f.read()
        parser = _HtmlStripper()
        parser.feed(raw)
        return parser.get_text()
    except Exception:
        return ""


def _build_knowledge_base() -> str:
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    parts = []
    for filename, label in [
        ("faq.html", "PREGUNTAS FRECUENTES (FAQ)"),
        ("manual.html", "MANUAL DE USUARIO"),
        ("docs.html", "DOCUMENTACIÓN TÉCNICA / API"),
    ]:
        text = _html_to_text(os.path.join(base_dir, filename))
        if text:
            parts.append(f"=== {label} ===\n{text}")
    return "\n\n".join(parts)


_FLOWBOT_KB: str = _build_knowledge_base()

_FLOWBOT_SYSTEM = (
    "Eres FlowBot, el asistente virtual amigable y experto de TextOnFlow.\n"
    "TextOnFlow es una herramienta visual de drag-and-drop para crear imágenes "
    "personalizadas con textos y overlays para campañas de ManyChat.\n\n"
    "TU FUNCIÓN:\n"
    "- Responder dudas de usuarios sobre cómo usar TextOnFlow\n"
    "- Guiar paso a paso de forma clara y amigable\n"
    "- Basar TODAS tus respuestas exclusivamente en la documentación adjunta\n"
    "- Si algo no está en la documentación, decirlo honestamente sin inventar\n\n"
    "REGLAS DE RESPUESTA:\n"
    "1. Responde siempre en español, con tono amigable y profesional\n"
    "2. Sé conciso: máximo 3-5 párrafos cortos por respuesta\n"
    "3. Usa listas con guiones (—) para pasos o puntos clave\n"
    "4. Si no sabes algo, di: 'No tengo esa información en mi documentación. "
    "Puedes contactar al soporte en www.textonflow.com'\n"
    "5. Nunca inventes funciones o precios que no estén en la documentación\n"
    "6. Nunca repitas la pregunta del usuario ni hagas introducciones largas\n"
    "7. Saluda solo en el primer mensaje de la conversación\n\n"
    f"DOCUMENTACIÓN DE TEXTONFLOW:\n{_FLOWBOT_KB}"
)


@ai_router.post("/api/assistant")
async def flowbot_chat(req: AssistantRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Mensaje vacío")

    contents = []
    for m in req.history[-6:]:
        role = "user" if m.role == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m.content}]})
    contents.append({"role": "user", "parts": [{"text": user_msg}]})

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": _FLOWBOT_SYSTEM}]},
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 600,
            "candidateCount": 1
        }
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=25)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Error al conectar con FlowBot. Intenta de nuevo.")
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=500, detail="Sin respuesta del asistente")
        parts = candidates[0].get("content", {}).get("parts", [])
        reply = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()
        if not reply:
            raise HTTPException(status_code=500, detail="Respuesta vacía")
        return {"reply": reply}
    except HTTPException:
        raise
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado. Intenta de nuevo.")
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


@ai_router.post("/api/assistant/transcript")
async def send_assistant_transcript(req: TranscriptRequest):
    import datetime
    labels = {1: "Mala", 2: "Buena", 3: "Excelente"}
    lines = []
    for m in req.history:
        who = "Tú" if m.role == "user" else "FlowBot"
        lines.append(f"{who}:\n{m.content}\n")
    conv_text = "\n".join(lines)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    body = (
        f"Hola {req.name},\n\n"
        f"Aquí tienes el historial de tu conversación con FlowBot — TextOnFlow ({now}):\n\n"
        f"{'='*50}\n\n"
        f"{conv_text}\n"
        f"{'='*50}\n\n"
        f"¿Tienes más dudas? Escríbenos a hola@textonflow.com\n"
        f"— El equipo de TextOnFlow\n"
        f"www.textonflow.com"
    )
    # Log siempre como respaldo
    try:
        log_path = os.path.join(STORAGE_DIR, "transcripts_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Nombre: {req.name} | Email: {req.email} | Fecha: {now}\n")
            f.write(conv_text)
            f.write("\n")
    except Exception:
        pass
    # Enviar con EngineMailer
    em_key = os.getenv("ENGINEMAILER_API_KEY", "")
    if em_key:
        try:
            requests.post(
                "https://api.enginemailer.com/RESTAPI/V2/Submission/SendEmail",
                headers={"APIKey": em_key, "Content-Type": "application/json"},
                json={
                    "CampaignName": "FlowBot Transcript",
                    "ToEmail": req.email,
                    "SenderEmail": "hola@followers.mx",
                    "SenderName": "FlowBot — TextOnFlow",
                    "Subject": "Tu conversación con FlowBot — TextOnFlow",
                    "SubmittedContent": body,
                    "BCCEmails": ["hola@textonflow.com"],
                },
                timeout=12,
            )
        except Exception:
            pass
    return {"ok": True}


@ai_router.post("/api/inpaint")
async def inpaint_image(request: Request):
    """
    Borrador Mágico IA — recibe imagen ORIGINAL + máscara B&W separada.
    La máscara tiene blanco donde borrar, negro donde mantener.
    Gemini recibe ambas imágenes y rellena el área blanca con fondo natural.
    Body: { "original": "<base64 JPEG>", "mask": "<base64 PNG>" }
    Returns: { "result": "<base64>", "mime": "image/jpeg" }
    """
    import io as _io
    data = await request.json()
    original_b64 = data.get("original", "")
    mask_b64     = data.get("mask", "")
    if not original_b64 or not mask_b64:
        raise HTTPException(status_code=400, detail="Se requieren los campos 'original' y 'mask'")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada en el servidor")

    # ── Analizar la máscara para extraer bounding box en porcentajes ──────────
    try:
        from PIL import Image as _PILImg
        mask_bytes_raw = base64.b64decode(mask_b64)
        mask_img  = _PILImg.open(_io.BytesIO(mask_bytes_raw)).convert('L')
        mask_arr  = __import__('numpy').array(mask_img)
        h_m, w_m  = mask_arr.shape
        wy, wx    = (mask_arr > 128).nonzero()
        if len(wy) == 0:
            raise HTTPException(status_code=400, detail="La máscara está vacía")
        # Bounding box en %
        y1p = int(wy.min() / h_m * 100)
        y2p = int(wy.max() / h_m * 100)
        x1p = int(wx.min() / w_m * 100)
        x2p = int(wx.max() / w_m * 100)
        # Pequeña dilación para capturar bordes
        pad = 3
        x1p = max(0, x1p - pad);  x2p = min(100, x2p + pad)
        y1p = max(0, y1p - pad);  y2p = min(100, y2p + pad)
        cxp = (x1p + x2p) // 2
        cyp = (y1p + y2p) // 2
        rw  = x2p - x1p
        rh  = y2p - y1p
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"mask analysis error: {e}")
        raise HTTPException(status_code=400, detail=f"Error al analizar la máscara: {e}")

    # ── Compositar máscara magenta sobre la imagen original (guía visual) ──────
    try:
        import numpy as _np
        orig_bytes  = base64.b64decode(original_b64)
        orig_img    = _PILImg.open(_io.BytesIO(orig_bytes)).convert("RGBA")
        w_o, h_o    = orig_img.size
        mask_resized = mask_img.resize((w_o, h_o), _PILImg.LANCZOS)
        mask_np      = _np.array(mask_resized)
        overlay_np   = _np.zeros((h_o, w_o, 4), dtype=_np.uint8)
        overlay_np[mask_np > 128] = [255, 0, 200, 190]   # magenta semitransparente
        overlay_pil  = _PILImg.fromarray(overlay_np, "RGBA")
        composite    = _PILImg.alpha_composite(orig_img, overlay_pil).convert("RGB")
        comp_buf     = _io.BytesIO()
        composite.save(comp_buf, format="JPEG", quality=90)
        composite_b64 = base64.b64encode(comp_buf.getvalue()).decode()
    except Exception as _ce:
        logger.warning(f"composite mask failed, using original: {_ce}")
        composite_b64 = original_b64

    # ── Prompt con guía visual clara ─────────────────────────────────────────
    prompt = (
        "I will give you two images:\n"
        "IMAGE 1 (guide): the original image with a MAGENTA/PINK highlighted zone "
        "that marks the area to erase.\n"
        "IMAGE 2 (to edit): the clean original image without any markings.\n\n"
        "Your task:\n"
        "1. Identify the MAGENTA/PINK zone shown in Image 1.\n"
        "2. In Image 2, erase ONLY the content inside that zone.\n"
        "3. Fill the erased area with realistic, seamless background that naturally "
        "continues the surrounding textures, colors, and lighting — as if nothing was ever there.\n"
        "4. Do NOT modify anything outside the marked zone.\n"
        "5. Return the complete final edited image (clean, without any pink or magenta marks)."
    )

    url     = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inlineData": {"mimeType": "image/jpeg", "data": composite_b64}},
                {"inlineData": {"mimeType": "image/jpeg", "data": original_b64}}
            ]
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE", "TEXT"]
        }
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=90)
        if resp.status_code != 200:
            logger.error(f"Gemini inpaint error {resp.status_code}: {resp.text[:400]}")
            raise HTTPException(
                status_code=500,
                detail=f"Error de Gemini ({resp.status_code}): {resp.text[:250]}"
            )
        result_data = resp.json()
        candidates  = result_data.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=500, detail="Gemini no devolvió resultado para el borrado")

        parts_resp = candidates[0].get("content", {}).get("parts", [])
        for part in parts_resp:
            if "inlineData" in part:
                return {
                    "result": part["inlineData"]["data"],
                    "mime":   part["inlineData"].get("mimeType", "image/jpeg")
                }

        # Log texto devuelto por Gemini para debug
        text_parts = [p.get("text","") for p in parts_resp if "text" in p]
        logger.warning(f"inpaint: Gemini devolvió solo texto: {' '.join(text_parts)[:300]}")
        raise HTTPException(status_code=500, detail="Gemini no devolvió imagen. Intenta con un área diferente.")

    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado (90 s). Intenta con un área más pequeña.")
    except HTTPException:
        raise
    except Exception as ex:
        logger.error(f"inpaint exception: {ex}")
        raise HTTPException(status_code=500, detail=str(ex))


@ai_router.post("/api/assistant/rating")
async def submit_assistant_rating(req: RatingRequest):
    import datetime
    labels = {1: "Mala ⭐", 2: "Buena ⭐⭐", 3: "Excelente ⭐⭐⭐"}
    label = labels.get(req.rating, f"{req.rating} estrellas")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        log_path = os.path.join(STORAGE_DIR, "ratings_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{now} | FlowBot | {label}\n")
    except Exception:
        pass
    return {"ok": True}




# ══════════════════════════════════════════════════════════════
# AI PRODUCT FEATURES — v1.0
# 1. Design from description  2. Copy suggestions
# 3. Brand kit extraction     4. A/B variant generator
# ══════════════════════════════════════════════════════════════

_DESIGN_LAYOUT_SYSTEM = """Eres un experto diseñador de imágenes de marketing para ManyChat.
El usuario te describe el diseño que necesita y tú generas el layout de texto en formato JSON.

Devuelve ÚNICAMENTE un JSON válido con esta estructura (sin markdown, sin explicaciones):
{
  "texts": [
    {
      "text": "Texto aquí",
      "auto_alignment": "center",
      "alignment": "auto",
      "font_size": 72,
      "font_color": "#FFFFFF",
      "font_value": "Roboto",
      "font_backend": "Roboto",
      "text_align": "center",
      "line_spacing": 4,
      "background_enabled": false,
      "background_color": "#000000",
      "background_opacity": 70,
      "bg_pad_top": 10,
      "bg_pad_right": 20,
      "bg_pad_bottom": 10,
      "bg_pad_left": 20,
      "bg_box_radius": 8,
      "stroke_enabled": false,
      "stroke_color": "#000000",
      "stroke_width": 2,
      "stroke_opacity": 100
    }
  ],
  "background_suggestion": "Descripción breve del fondo ideal en español",
  "color_palette": ["#HEX1", "#HEX2", "#HEX3"]
}

REGLAS:
- auto_alignment puede ser: center, top-center, bottom-center, top-left, top-right, bottom-left, bottom-right, middle-left, middle-right
- Usa máximo 4 capas de texto para no saturar la imagen
- font_value y font_backend deben ser nombres de fuentes Google Fonts comunes
- Adapta tamaños: título grande (60-120px), subtítulo (36-60px), detalle (24-40px), CTA (44-64px)
- Colores vibrantes y contrastantes para marketing
- Si el contexto menciona colores específicos úsalos
- Para CTAs usa background_enabled: true con bg_box_radius: 10
- Responde SOLO con el JSON, nada más"""

@ai_router.post("/api/ai/design-layout")
async def ai_design_layout(req: DesignLayoutRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    if len(req.description.strip()) < 5:
        raise HTTPException(status_code=400, detail="La descripción es muy corta")
    user_msg = f"Descripción del diseño: {req.description.strip()}"
    if req.context:
        user_msg += f"\nContexto adicional: {req.context.strip()}"
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": user_msg}]}],
        "systemInstruction": {"parts": [{"text": _DESIGN_LAYOUT_SYSTEM}]},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1200, "candidateCount": 1,
                             "responseMimeType": "application/json"}
    }
    try:
        async with httpx.AsyncClient(timeout=22) as _hc:
            resp = await _hc.post(url, json=payload, headers=headers)
        if resp.status_code == 429:
            raise HTTPException(status_code=429, detail="La IA está ocupada. Espera unos segundos e intenta de nuevo.")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Error IA ({resp.status_code}): {resp.text[:120]}")
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()
        # Strip markdown fences if present
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        layout = json.loads(raw)
        # Fill mandatory fields with defaults for each text object
        defaults = {
            "x": req.canvas_width // 2, "y": req.canvas_height // 2,
            "padding_x": 40, "padding_y": 30,
            "rotation": 0, "skew_x": 0, "skew_y": 0, "opacity": 1.0,
            "bg_color_type": "solid", "bg_gradient_color2": "#FFFFFF", "bg_gradient_angle": 135,
            "bg_stroke_color": "#FFFFFF", "bg_stroke_width": 0, "bg_stroke_opacity": 100,
            "bg_brd_pad_top": 10, "bg_brd_pad_right": 20, "bg_brd_pad_bottom": 10, "bg_brd_pad_left": 20,
            "bg_brd_pad_linked": True, "bg_pad_linked": True,
            "warp_style": "none", "warp_bend": 0,
            "canvas_padding_enabled": False, "canvas_padding_value": 40, "canvas_padding_side": "left",
            "text_wrap_enabled": False, "text_wrap_padding": 60,
            "stroke_type": "solid", "stroke_gradient_color2": "#FFFFFF", "stroke_gradient_angle": 135,
            "stroke_dash": "solid", "bg_border_enabled": False,
            "has_background_layer": False, "back_color": "#000000",
            "back_opacity": 0.3, "offset_x": 10, "offset_y": 10,
            "back_blur": 5, "back_blend_mode": "multiply",
        }
        for t in layout.get("texts", []):
            for k, v in defaults.items():
                t.setdefault(k, v)
        return layout
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"La IA devolvió JSON inválido: {e}")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


# ─── Copy Suggestions ───────────────────────────────────────
_COPY_SYSTEM = """Eres un copywriter experto en marketing para ManyChat y redes sociales.
Devuelve ÚNICAMENTE un JSON con 3 variaciones creativas del texto dado.
Formato: {"suggestions": ["variación 1", "variación 2", "variación 3"]}
- Mantén un tono similar al original pero varía el enfoque (urgencia, emoción, beneficio)
- Máximo 2 líneas por variación
- Sin comillas anidadas en las variaciones
- Responde SOLO el JSON"""

@ai_router.post("/api/ai/copy-suggestions")
async def ai_copy_suggestions(req: CopySuggestionsRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    if not req.current_text.strip():
        raise HTTPException(status_code=400, detail="El texto no puede estar vacío")
    user_msg = f"Texto actual: {req.current_text.strip()}"
    if req.context:
        user_msg += f"\nContexto del diseño: {req.context.strip()}"
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": user_msg}]}],
        "systemInstruction": {"parts": [{"text": _COPY_SYSTEM}]},
        "generationConfig": {"temperature": 0.85, "maxOutputTokens": 300,
                             "responseMimeType": "application/json"}
    }
    try:
        async with httpx.AsyncClient(timeout=15) as _hc:
            resp = await _hc.post(url, json=payload, headers=headers)
        if resp.status_code == 429:
            raise HTTPException(status_code=429, detail="La IA está ocupada. Espera unos segundos e intenta de nuevo.")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Error IA ({resp.status_code}): {resp.text[:120]}")
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = "\n".join(p.get("text","") for p in parts if "text" in p).strip()
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="La IA devolvió JSON inválido")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


# ─── Brand Kit Extraction ────────────────────────────────────
_BRAND_KIT_SYSTEM = """Eres un experto en identidad visual y branding.
Analiza el logo o imagen proporcionada y extrae la paleta de colores de marca.
Devuelve ÚNICAMENTE un JSON con este formato:
{
  "colors": ["#HEX1","#HEX2","#HEX3","#HEX4","#HEX5"],
  "background_color": "#HEX",
  "font_suggestion": "Nombre de fuente Google Fonts que combine con la marca",
  "style": "moderno|elegante|divertido|minimalista|corporativo",
  "description": "Descripción breve del estilo de marca en español (1 oración)"
}
- Extrae exactamente 5 colores representativos (de más a menos prominente)
- background_color: el color más claro / fondo ideal
- Responde SOLO el JSON"""

@ai_router.post("/api/ai/brand-kit")
async def ai_brand_kit(req: BrandKitRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    # Download the image and convert to base64
    try:
        img_resp = requests.get(req.image_url, timeout=15)
        img_resp.raise_for_status()
        img_data = base64.b64encode(img_resp.content).decode()
        content_type = img_resp.headers.get("content-type", "image/png").split(";")[0].strip()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No se pudo cargar la imagen: {e}")
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [
            {"text": "Analiza este logo y extrae la paleta de colores de marca."},
            {"inlineData": {"mimeType": content_type, "data": img_data}}
        ]}],
        "systemInstruction": {"parts": [{"text": _BRAND_KIT_SYSTEM}]},
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400,
                             "responseMimeType": "application/json"}
    }
    try:
        async with httpx.AsyncClient(timeout=22) as _hc:
            resp = await _hc.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Error al conectar con la IA")
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = "\n".join(p.get("text","") for p in parts if "text" in p).strip()
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="La IA devolvió JSON inválido")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


# ─── A/B Variant Generator ───────────────────────────────────
_AB_VARIANTS_SYSTEM = """Eres un experto en diseño de marketing y optimización de conversión.
Tomas un layout de texto y generas 3 variantes visuales con diferentes paletas de color y estilos.
Devuelve ÚNICAMENTE un JSON con este formato:
{
  "variants": [
    {
      "label": "Variante A — Alta Energía",
      "theme": "bold",
      "color_overrides": {"font_color": "#HEX", "background_color": "#HEX"},
      "texts": [ ...array of text objects with modified colors and/or font_size... ]
    },
    { "label": "Variante B — Elegante", "theme": "elegant", "color_overrides": {...}, "texts": [...] },
    { "label": "Variante C — Minimalista", "theme": "minimal", "color_overrides": {...}, "texts": [...] }
  ]
}
REGLAS:
- Mantén exactamente la misma estructura y posición de texto de cada capa
- Solo cambia: font_color, background_color, background_opacity, stroke_color, stroke_width, font_size (±10%)
- Cada variante debe tener una paleta de color claramente diferente
- Variante A: colores vibrantes y contrastantes
- Variante B: colores elegantes y sofisticados (dorado, gris oscuro, blanco)
- Variante C: colores mínimos, fondo oscuro, texto limpio
- Responde SOLO el JSON"""

@ai_router.post("/api/ai/ab-variants")
async def ai_ab_variants(req: ABVariantsRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada")
    if not req.texts:
        raise HTTPException(status_code=400, detail="No hay capas de texto para generar variantes")
    # Send only relevant fields to Gemini (not the full heavy object)
    slim_texts = []
    for t in req.texts:
        slim_texts.append({
            "text": t.get("text",""),
            "font_color": t.get("font_color","#FFFFFF"),
            "font_size": t.get("font_size", 60),
            "background_enabled": t.get("background_enabled", False),
            "background_color": t.get("background_color","#000000"),
            "background_opacity": t.get("background_opacity",70),
            "stroke_enabled": t.get("stroke_enabled",False),
            "stroke_color": t.get("stroke_color","#000000"),
            "stroke_width": t.get("stroke_width",2),
            "auto_alignment": t.get("auto_alignment","center"),
            "alignment": t.get("alignment","auto"),
            "font_value": t.get("font_value","Roboto"),
            "font_backend": t.get("font_backend","Roboto"),
            "text_align": t.get("text_align","center"),
            "line_spacing": t.get("line_spacing", 4),
        })
    user_msg = f"Layout actual con {len(slim_texts)} capas:\n{json.dumps(slim_texts, ensure_ascii=False)}"
    if req.context:
        user_msg += f"\nContexto del diseño: {req.context}"
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": user_msg}]}],
        "systemInstruction": {"parts": [{"text": _AB_VARIANTS_SYSTEM}]},
        "generationConfig": {"temperature": 0.75, "maxOutputTokens": 1500,
                             "responseMimeType": "application/json"}
    }
    try:
        async with httpx.AsyncClient(timeout=22) as _hc:
            resp = await _hc.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Error al conectar con la IA")
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = "\n".join(p.get("text","") for p in parts if "text" in p).strip()
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)
        # Merge back full original text objects with AI overrides
        full_texts = req.texts
        for variant in result.get("variants", []):
            merged = []
            for i, ai_t in enumerate(variant.get("texts", [])):
                if i < len(full_texts):
                    base = dict(full_texts[i])
                    base.update({k: v for k, v in ai_t.items()})
                    merged.append(base)
                else:
                    merged.append(ai_t)
            variant["texts"] = merged
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="La IA devolvió JSON inválido")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))
