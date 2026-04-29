"""
models.py — Pydantic schemas de TextOnFlow
Extraído de main.py (Paso 1 de desfragmentación, 2026-04-28)
"""
from typing import Dict, List, Optional
from pydantic import BaseModel

class TextField(BaseModel):
    text: str
    x: int
    y: int
    font_size: int = 60
    font_color: str = "#FFFFFF"
    rotation: int = 0
    skew_x: float = 0
    skew_y: float = 0
    # Tipo de relleno del fondo: "solid" | "gradient2" | "instagram"
    background_color_type: str = "solid"
    background_gradient_color2: str = "#FFFFFF"
    background_gradient_angle: int = 135
    # Tipo de borde: "solid" | "gradient2" | "instagram"
    background_stroke_type: str = "solid"
    background_stroke_gradient_color2: str = "#FFFFFF"
    background_stroke_gradient_angle: int = 135
    # Estilo de línea del borde: "solid" | "dashed" | "dotted"
    background_stroke_dash: str = "solid"
    line_spacing: int = 10
    alignment: str = "left"
    text_align: str = "center"
    font_name: str = "Arial-Bold"
    shadow_enabled: bool = False
    shadow_color: str = "#000000"
    shadow_opacity: int = 100
    shadow_offset_x: int = 2
    shadow_offset_y: int = 2
    shadow_blur: int = 0   # Gaussian blur radius (px) — 0 = sombra dura, >0 = sombra difusa
    shadow_blend_mode: str = "normal"  # Modos Photoshop: normal, multiply, darken, color_burn, linear_burn, overlay, soft_light, screen
    stroke_enabled: bool = False
    stroke_color: str = "#000000"
    stroke_opacity: int = 100
    stroke_width: int = 2
    background_enabled: bool = False
    background_color: str = "#000000"
    background_opacity: int = 80
    background_padding_top: Optional[int] = 10
    background_padding_right: Optional[int] = 10
    background_padding_bottom: Optional[int] = 10
    background_padding_left: Optional[int] = 10
    background_radius: int = 0
    background_stroke_color: str = "#FFFFFF"
    background_stroke_width: int = 0
    border_padding_top: Optional[int] = 10
    border_padding_right: Optional[int] = 20
    border_padding_bottom: Optional[int] = 10
    border_padding_left: Optional[int] = 20
    warp_style: str = "none"   # none|arc|arc_lower|arc_upper|arch|bulge|shell_lower|shell_upper|flag|wave|fish|rise|fisheye|inflate|squeeze|twist
    warp_bend: int = 0         # -100 a 100
    # ── Text Wrap automático ──────────────────────────────────────────────────
    text_wrap_enabled: bool = False   # Activa salto de línea automático por palabra
    text_wrap_padding: int = 60       # Margen L/R en px (el texto ocupa ancho - 2*padding)
    # ── Contador regresivo (opcional) ────────────────────────────────────────
    countdown_mode: Optional[str] = None            # "event" | "urgency"
    countdown_event_end_utc: Optional[str] = None   # "YYYY-MM-DDTHH:MM:SSZ"
    countdown_urgency_hours: Optional[float] = None
    countdown_ts_var: Optional[str] = None          # nombre del custom field ManyChat
    countdown_format: Optional[str] = "HH:MM:SS"   # "HH:MM:SS" | "DD:HH:MM:SS" | "HH:MM"
    countdown_expired_text: Optional[str] = None
    countdown_urgency_color: Optional[str] = None   # color cuando faltan N horas
    countdown_urgency_threshold_h: Optional[float] = 3.0  # horas umbral (default 3)

class CanvasShape(BaseModel):
    id: str = ""
    shape_type: str = "rect"   # rect | square | ellipse | circle | star12
    x: int = 0
    y: int = 0
    width: int = 100
    height: int = 100
    rotation: float = 0
    fill_color: str = "#667eea"
    fill_opacity: float = 0.8
    stroke_color: str = "#000000"
    stroke_width: int = 0
    stroke_opacity: float = 1.0
    z_index: int = 0
    cover_blur: int = 0

class ImageOverlay(BaseModel):
    src: str          # base64 data URL (data:image/png;base64,...) o URL http
    x: int = 0
    y: int = 0
    width: int = 100
    height: int = 100
    opacity: float = 1.0
    rotation: float = 0
    mask_type: str = "none"   # none | circle | ellipse | square | rect | star12
    mask_auto_fit: bool = True
    mask_radius: int = 0      # radio de esquinas para mask_type="rect"
    # Borde
    mask_border_width: int = 0
    mask_border_color: str = "#ffffff"
    mask_border_opacity: int = 100
    # Sombra
    mask_shadow_enabled: bool = False
    mask_shadow_color: str = "#000000"
    mask_shadow_opacity: int = 70
    mask_shadow_blur: int = 8
    mask_shadow_x: int = 0
    mask_shadow_y: int = 4

class MultiTextRequest(BaseModel):
    template_name: str
    texts: List[TextField]
    vars: Optional[Dict[str, str]] = None
    overlays: Optional[List[ImageOverlay]] = []
    shapes: Optional[List[CanvasShape]] = []
    filter_name: str = "none"
    render_scale: int = 1  # 1=rápido (ManyChat), 2=alta calidad (editor)
    watermark: bool = False  # Sello TextOnFlow sobre la imagen
    wm_corner:  str   = "br"      # tl | tr | bl | br
    wm_size:    int   = 22        # altura en px del logo (relativa a 1080px)
    wm_opacity: int   = 55        # 0-100
    wm_color:   str   = "#ffffff" # hex color del logo
    # ── Viñeta ──
    vignette_enabled: bool        = False
    vignette_color:   str         = "#000000"  # hex color
    vignette_opacity: float       = 0.6        # 0.0-1.0
    vignette_size:    float       = 50.0       # 0-100 (qué tanto cubre)
    vignette_sides:   Optional[List[str]] = None  # ['top','right','bottom','left','tl','tr','bl','br']
    vignette_filter:  str         = "none"     # tono: none|sepia|warm|cold|violet|green|red|golden|cyan
    # ── Multi-formato: artboard crop/zoom ─────────────────────────────────────
    format_width:  Optional[int]   = None  # Ancho del artboard del formato (px)
    format_height: Optional[int]   = None  # Alto del artboard del formato (px)
    img_pan_x:     float           = 0.0   # Offset X de la imagen en el artboard
    img_pan_y:     float           = 0.0   # Offset Y de la imagen en el artboard
    img_zoom:      float           = 1.0   # Factor de zoom de la imagen
    # ── Imagen base64 (opcional): el frontend la envía para evitar fetch externo ─
    template_image_b64: Optional[str] = None  # Base64 del JPEG/PNG de la plantilla
    # ── Metadata de contexto (para dashboard de estadísticas) ──────────────────
    project_name: Optional[str] = None  # Nombre del proyecto en el editor

class _AdminLoginBody(BaseModel):
    email: str
    password: str

class _AdminSettingsBody(BaseModel):
    free_limit: int

class _UserRegisterBody(BaseModel):
    email: str
    password: str

class _UserLoginBody(BaseModel):
    email: str
    password: str

class _UserUpdateBody(BaseModel):
    gemini_api_key: Optional[str] = None

class _WebhookBody(BaseModel):
    webhook_url: Optional[str] = None

class _ProjectCreate(BaseModel):
    name: str = "Sin título"
    canvas_json: dict = {}
    image_url: Optional[str] = None

class _ProjectUpdate(BaseModel):
    name: Optional[str] = None
    canvas_json: Optional[dict] = None
    image_url: Optional[str] = None

class _ForgotPasswordBody(BaseModel):
    email: str

class _ResetPasswordBody(BaseModel):
    token: str
    new_password: str

class _SessionOpenBody(BaseModel):
    session_key: str
    image_name: str
    image_type: str = "url"

class _SessionCloseBody(BaseModel):
    session_key: str

class _AdminUserActionBody(BaseModel):
    user_id: str

class _CheckoutBody(BaseModel):
    plan: str          # "starter" | "agency"
    success_url: Optional[str] = None
    cancel_url:  Optional[str] = None

class ApiTemplateRequest(BaseModel):
    name: str
    template_name: str
    texts: List[TextField] = []
    shapes: Optional[List[CanvasShape]] = []
    overlays: Optional[List[ImageOverlay]] = []
    filter_name: str = "none"
    render_scale: int = 2
    watermark: bool = False
    vignette_enabled: bool = False
    vignette_color: str = "#000000"
    vignette_opacity: float = 0.6
    vignette_size: float = 50.0
    vignette_sides: Optional[List[str]] = None
    vignette_filter: str = "none"
    format_width: Optional[int] = None
    format_height: Optional[int] = None
    img_pan_x: float = 0.0
    img_pan_y: float = 0.0
    img_zoom: float = 1.0

class WebhookRenderRequest(BaseModel):
    template_id: str
    variables: Dict[str, str] = {}
    secret: Optional[str] = None
    output_format: str = "url"  # "url" | "base64"

class RefImage(BaseModel):
    data: str       # base64 sin prefijo data:URL
    mime_type: str  # image/jpeg, image/png, image/webp

# ── Mapa de referencias populares → descripción de estilo visual ────────────
# Permite que el usuario escriba "estilo simpsons" y Gemini reciba una
# descripción artística en lugar del nombre de la franquicia registrada.

class GenerateImageRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "1:1"
    style: Optional[str] = None
    reference_images: Optional[List[RefImage]] = []

class GenerateTextRequest(BaseModel):
    text: str
    tone: str = "Profesional"

class EnhancePromptRequest(BaseModel):
    prompt: str
    no_text: bool = False

class SaveAIImageRequest(BaseModel):
    image_b64: str
    mime_type: str = "image/png"

class EditImageRequest(BaseModel):
    image_b64: str
    mime_type: str = "image/png"
    instruction: str
    reference_images: list = []

class QRRequest(BaseModel):
    text:        str
    dark_color:  str = "#000000"
    light_color: str = "#ffffff"
    bg_color:    str = "#ffffff"
    padding:     int = 20

class FeedbackRequest(BaseModel):
    name: str
    email: str
    message: str

class TimerStyle(BaseModel):
    font: str = "Doto"
    font_size: int = 52
    color: str = "#FFFFFF"
    x: float = 50.0            # porcentaje del ancho (0-100)
    y: float = 50.0            # porcentaje del alto  (0-100)
    alignment: str = "center"  # "left" | "center" | "right"
    format: str = "HH:MM:SS"   # "DD:HH:MM:SS" | "HH:MM:SS" | "HH:MM"
    expired_text: str = "¡Oferta expirada!"
    stroke_enabled: bool = True
    stroke_color: str = "#000000"
    stroke_width: int = 2
    shadow_enabled: bool = False
    shadow_color: str = "#000000"
    shadow_offset_x: float = 2.0
    shadow_offset_y: float = 2.0
    # Text wrap para el mensaje expirado
    expired_wrap_enabled: bool = True    # activo por defecto: siempre wrap el expirado
    expired_wrap_padding: int = 60       # margen L/R px
    expired_align: str = "center"        # alineación del mensaje expirado

class TimerTemplateCreate(BaseModel):
    template_name: str                  # nombre descriptivo
    base_image_url: str                 # URL de la imagen base (puede ser /storage/...)
    mode: str                           # "event" | "urgency"
    # Modo evento: fecha fija DD/MM/AAAA HH:MM (hora local → se guarda como UTC)
    event_date: Optional[str] = None    # "20/03/2026 18:00"
    event_tz: Optional[str] = "America/Mexico_City"
    # Modo urgencia: duración fija
    urgency_hours: Optional[float] = None
    style: TimerStyle = TimerStyle()
    # Imagen diferente para cuando el contador expira (opcional)
    expired_image_url: Optional[str] = None

class TimerTemplateResponse(BaseModel):
    template_id: str
    live_url_event: Optional[str] = None    # URL lista para copiar (modo evento)
    live_url_urgency: Optional[str] = None  # URL con variables (modo urgencia)
    preview_seconds: int                    # segundos restantes al guardar (debug)

class AssistantMessage(BaseModel):
    role: str
    content: str

class AssistantRequest(BaseModel):
    message: str
    history: List[AssistantMessage] = []

class TranscriptRequest(BaseModel):
    name: str
    email: str
    history: List[AssistantMessage] = []

class RatingRequest(BaseModel):
    rating: int

class DesignLayoutRequest(BaseModel):
    description: str
    canvas_width: int = 1080
    canvas_height: int = 1080
    context: Optional[str] = None

class CopySuggestionsRequest(BaseModel):
    current_text: str
    context: Optional[str] = None

class BrandKitRequest(BaseModel):
    image_url: str

class ABVariantsRequest(BaseModel):
    texts: list
    context: Optional[str] = None
