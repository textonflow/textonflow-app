"""
renderer.py — Motor completo de renderizado de imágenes para TextOnFlow.
Importado por main.py. Depende de: PIL, numpy, pilmoji, fonts.py, models.py.
"""
import os
import math
import logging

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

try:
    from pilmoji import Pilmoji
    from pilmoji.source import EmojiCDNSource
except ImportError:
    Pilmoji = None
    EmojiCDNSource = None

from fonts import FONT_MAPPING, RetryTwitterEmojiSource
from models import TextField, CanvasShape

logger = logging.getLogger("textonflow")

# ─── Gradientes predefinidos ──────────────────────────────────────────────────
INSTAGRAM_GRADIENT = [
    (240, 148,  51, 255),
    (230, 104,  60, 255),
    (220,  39,  67, 255),
    (204,  35, 102, 255),
    (188,  24, 136, 255),
]

NEGRO_GRADIENT = [
    (  0,   0,   0, 255),
    ( 18,  18,  26, 255),
    ( 40,  40,  55, 255),
    ( 18,  18,  26, 255),
    (  0,   0,   0, 255),
]

METALICO_GRADIENT = [
    ( 80,  80,  90, 255),
    (190, 190, 200, 255),
    (240, 240, 248, 255),
    (200, 200, 212, 255),
    (100, 100, 112, 255),
    (200, 200, 212, 255),
    (240, 240, 248, 255),
]


def make_gradient_image(w: int, h: int, colors: list, angle_deg: float = 135) -> "Image.Image":
    """Crea una imagen RGBA con degradado lineal de N colores. Requiere numpy."""
    w, h = max(1, int(w)), max(1, int(h))
    if not _NUMPY_OK or len(colors) < 2:
        return Image.new("RGBA", (w, h), colors[0] if colors else (0, 0, 0, 0))
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    xs = np.linspace(0.0, 1.0, w)
    ys = np.linspace(0.0, 1.0, h)
    xx, yy = np.meshgrid(xs, ys)
    t = xx * cos_a + yy * sin_a
    t_min, t_max = float(t.min()), float(t.max())
    t = (t - t_min) / max(t_max - t_min, 1e-10)
    n = len(colors)
    result = np.zeros((h, w, 4), dtype=np.float64)
    for i in range(n - 1):
        t0 = i / (n - 1)
        t1 = (i + 1) / (n - 1)
        mask = (t >= t0) & (t <= t1)
        local_t = np.where(mask, (t - t0) / max(t1 - t0, 1e-10), 0.0)
        c1 = np.array(colors[i],     dtype=np.float64)
        c2 = np.array(colors[i + 1], dtype=np.float64)
        for ch in range(4):
            result[:, :, ch] += mask * (c1[ch] + local_t * (c2[ch] - c1[ch]))
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8), "RGBA")


def apply_gradient_bg(layer: "Image.Image", bx1, by1, bx2, by2, radius, colors, angle_deg=135):
    """Rellena un rect redondeado con degradado sobre `layer` (RGBA, in-place)."""
    bx1, by1, bx2, by2 = int(bx1), int(by1), int(bx2), int(by2)
    w, h = bx2 - bx1, by2 - by1
    if w <= 0 or h <= 0:
        return
    grad = make_gradient_image(w, h, colors, angle_deg)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    r = min(int(radius), max(0, (min(w, h) - 1) // 2))
    if r > 0:
        md.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=r, fill=255)
    else:
        md.rectangle([(0, 0), (w - 1, h - 1)], fill=255)
    layer.paste(grad, (bx1, by1), mask)


def apply_gradient_stroke(layer: "Image.Image", bx1, by1, bx2, by2, radius, stroke_w, colors, angle_deg=135):
    """Dibuja un borde (anillo) de rect redondeado con degradado sobre `layer` (RGBA, in-place)."""
    bx1, by1, bx2, by2, stroke_w = int(bx1), int(by1), int(bx2), int(by2), int(stroke_w)
    w, h = bx2 - bx1, by2 - by1
    if w <= 0 or h <= 0 or stroke_w <= 0:
        return
    grad = make_gradient_image(w, h, colors, angle_deg)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    r = min(int(radius), max(0, (min(w, h) - 1) // 2))
    if r > 0:
        md.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=r, fill=255)
    else:
        md.rectangle([(0, 0), (w - 1, h - 1)], fill=255)
    ix1, iy1 = stroke_w, stroke_w
    ix2, iy2 = w - 1 - stroke_w, h - 1 - stroke_w
    if ix2 > ix1 and iy2 > iy1:
        iw, ih = ix2 - ix1, iy2 - iy1
        ir = min(max(0, r - stroke_w), max(0, (min(iw, ih) - 1) // 2))
        if ir > 0:
            md.rounded_rectangle([(ix1, iy1), (ix2, iy2)], radius=ir, fill=0)
        else:
            md.rectangle([(ix1, iy1), (ix2, iy2)], fill=0)
    layer.paste(grad, (bx1, by1), mask)


def _draw_dashed_border(draw, x1, y1, x2, y2, radius, stroke_w, color, dash_style):
    """Dibuja borde sólido, guiones o puntos sobre un rectángulo (opcionalmente redondeado)."""
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    r = min(int(radius), max(0, (min(x2-x1, y2-y1) - 1) // 2))

    if dash_style not in ('dashed', 'dotted'):
        if r > 0:
            draw.rounded_rectangle([(x1,y1),(x2,y2)], radius=r, outline=color, width=stroke_w)
        else:
            draw.rectangle([(x1,y1),(x2,y2)], outline=color, width=stroke_w)
        return

    if dash_style == 'dotted':
        dash_on  = max(stroke_w, 3)
        dash_off = max(stroke_w * 3, 8)
    else:
        dash_on  = max(stroke_w * 7, 18)
        dash_off = max(stroke_w * 4, 10)

    pts = []
    def add_line(xa, ya, xb, yb):
        n = max(1, int(max(abs(xb-xa), abs(yb-ya))))
        for i in range(n):
            t = i / n
            pts.append((xa + (xb-xa)*t, ya + (yb-ya)*t))

    def add_arc(cx, cy, rad, a_start, a_end):
        if rad <= 0:
            return
        arc_len = abs(a_end - a_start) * math.pi * rad / 180
        steps = max(2, int(arc_len))
        for i in range(steps + 1):
            a = math.radians(a_start + (a_end - a_start) * i / steps)
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))

    add_line(x1+r, y1, x2-r, y1)
    add_arc(x2-r, y1+r, r, -90, 0)
    add_line(x2, y1+r, x2, y2-r)
    add_arc(x2-r, y2-r, r, 0, 90)
    add_line(x2-r, y2, x1+r, y2)
    add_arc(x1+r, y2-r, r, 90, 180)
    add_line(x1, y2-r, x1, y1+r)
    add_arc(x1+r, y1+r, r, 180, 270)

    n = len(pts)
    i = 0
    drawing = True
    while i < n:
        seg = dash_on if drawing else dash_off
        seg_i = int(seg)
        if drawing:
            end_i = min(i + seg_i, n - 1)
            for j in range(i, end_i):
                p1 = (round(pts[j][0]), round(pts[j][1]))
                p2 = (round(pts[min(j+1, n-1)][0]), round(pts[min(j+1, n-1)][1]))
                draw.line([p1, p2], fill=color, width=stroke_w)
        i += seg_i
        drawing = not drawing


def _ig_colors(alpha: int) -> list:
    return [(r, g, b, alpha) for r, g, b, _ in INSTAGRAM_GRADIENT]

def _negro_colors(alpha: int) -> list:
    return [(r, g, b, alpha) for r, g, b, _ in NEGRO_GRADIENT]

def _metal_colors(alpha: int) -> list:
    return [(r, g, b, alpha) for r, g, b, _ in METALICO_GRADIENT]


def apply_filter(img: Image.Image, filter_name: str) -> Image.Image:
    """Aplica un filtro de color/tono a la imagen base (antes de pintar texto)."""
    if not filter_name or filter_name == "none":
        return img
    try:
        alpha = img.split()[3] if img.mode == "RGBA" else None
        rgb = img.convert("RGB")

        def enh(im, brightness=1.0, contrast=1.0, saturation=1.0):
            if brightness != 1.0:
                im = ImageEnhance.Brightness(im).enhance(brightness)
            if contrast != 1.0:
                im = ImageEnhance.Contrast(im).enhance(contrast)
            if saturation != 1.0:
                im = ImageEnhance.Color(im).enhance(saturation)
            return im

        def ch(im, r=1.0, g=1.0, b=1.0):
            if not _NUMPY_OK:
                return im
            arr = np.array(im).astype(float)
            arr[:, :, 0] = np.clip(arr[:, :, 0] * r, 0, 255)
            arr[:, :, 1] = np.clip(arr[:, :, 1] * g, 0, 255)
            arr[:, :, 2] = np.clip(arr[:, :, 2] * b, 0, 255)
            return Image.fromarray(arr.astype(np.uint8))

        FILTERS = {
            # ── Instagram ────────────────────────────────────────────────────
            "clarendon":     lambda im: ch(enh(im, contrast=1.2,  saturation=1.35), r=0.90, b=1.15),
            "gingham":       lambda im: enh(ch(im, r=1.05, b=0.95), brightness=1.05, saturation=0.85),
            "juno":          lambda im: ch(enh(im, saturation=1.2),  r=1.15, g=1.05, b=0.90),
            "lark":          lambda im: enh(ch(im, r=1.05, b=1.10), brightness=1.10, saturation=0.90),
            "mayfair":       lambda im: ch(enh(im, brightness=1.05, saturation=1.10), r=1.10, b=0.90),
            "moon":          lambda im: enh(im.convert("L").convert("RGB"), contrast=1.10),
            "nashville":     lambda im: ch(enh(im, brightness=1.05, saturation=0.90), r=1.10, g=0.90, b=0.85),
            "perpetua":      lambda im: ch(enh(im, saturation=0.90), r=0.95, g=1.05, b=1.05),
            "reyes":         lambda im: enh(ch(im, r=1.10, g=1.05, b=0.95), brightness=1.10, contrast=0.85, saturation=0.75),
            "rise":          lambda im: ch(enh(im, brightness=1.10, saturation=0.90), r=1.15, g=1.05, b=0.95),
            "slumber":       lambda im: enh(ch(im, r=0.85, b=0.95), saturation=0.60, brightness=1.05),
            "valencia":      lambda im: ch(enh(im, contrast=0.90, saturation=0.90), r=1.15, g=1.05, b=0.90),
            "walden":        lambda im: ch(enh(im, brightness=1.10, saturation=0.80), r=0.95, b=1.10),
            "xpro2":         lambda im: ch(enh(im, contrast=1.30, saturation=1.20), r=0.85, g=0.90, b=1.00),
            "inkwell":       lambda im: enh(im.convert("L").convert("RGB"), contrast=1.05),
            "toaster":       lambda im: ch(enh(im, contrast=1.30, saturation=0.90), r=1.20, g=0.85, b=0.70),
            "lo_fi":         lambda im: ch(enh(im, contrast=1.40, saturation=1.30), r=1.10, g=0.90, b=0.80),
            "hefe":          lambda im: ch(enh(im, brightness=1.05, contrast=1.20, saturation=1.30), r=1.15, b=0.80),
            # ── Photoshop / LUT ──────────────────────────────────────────────
            "bleach_bypass": lambda im: enh(ch(im, r=0.90, g=0.90, b=0.90), contrast=1.30, saturation=0.40),
            "candlelight":   lambda im: ch(enh(im, brightness=1.10), r=1.25, g=1.05, b=0.70),
            "crisp_warm":    lambda im: ch(enh(im, contrast=1.10, saturation=1.10), r=1.10, b=0.90),
            "crisp_winter":  lambda im: ch(enh(im, contrast=1.10, saturation=0.95), r=0.90, g=0.95, b=1.15),
            "fall_colors":   lambda im: ch(enh(im, contrast=1.05, saturation=1.20), r=1.15, g=1.05, b=0.80),
            "foggy_night":   lambda im: ch(enh(im, brightness=0.85, saturation=0.70), r=0.90, b=1.10),
            "horror_blue":   lambda im: ch(enh(im, brightness=0.90, saturation=0.80, contrast=1.10), r=0.80, g=0.85, b=1.20),
            "late_sunset":   lambda im: ch(enh(im, brightness=0.95, saturation=1.10), r=1.20, g=0.90, b=0.75),
            "moonlight_ps":  lambda im: ch(enh(im, brightness=0.90, saturation=0.60), r=0.85, g=0.90, b=1.15),
            "soft_warming":  lambda im: ch(enh(im, brightness=1.05, saturation=0.95), r=1.10, b=0.90),
            "teal_orange":   lambda im: ch(enh(im, contrast=1.15, saturation=1.10), r=1.15, g=0.90, b=0.85),
            "fuji_eterna":   lambda im: enh(ch(im, r=0.95, b=1.05), saturation=0.90, contrast=0.95),
            "filmstock":     lambda im: ch(enh(im, saturation=0.95, contrast=1.05), r=1.05),
            "tension_green": lambda im: ch(enh(im, contrast=1.10, saturation=0.90), r=0.90, g=1.10, b=0.85),
            "edgy_amber":    lambda im: ch(enh(im, contrast=1.20, saturation=0.80), r=1.15, b=0.75),
            "drop_blues":    lambda im: ch(enh(im, contrast=1.05, saturation=0.85), r=0.90, g=0.95, b=1.20),
            "2strip":        lambda im: ch(enh(im, saturation=0.70, contrast=1.15), r=1.10, g=0.90, b=0.80),
            "3strip":        lambda im: ch(enh(im, saturation=1.10, contrast=1.10), r=1.05),
            "futuristic":    lambda im: ch(enh(im, brightness=0.85, saturation=0.50, contrast=1.20), r=0.75, g=0.85, b=1.30),
            "night_from_day":lambda im: ch(enh(im, brightness=0.80, saturation=0.55, contrast=1.15), r=0.80, g=0.90, b=1.25),
            "fuji_f125_2393":lambda im: ch(enh(im, saturation=0.85, contrast=1.05, brightness=1.02), r=1.05, g=1.00, b=0.92),
            "fuji_f125_2395":lambda im: ch(enh(im, saturation=0.80, contrast=1.08, brightness=1.03), r=1.03, g=1.02, b=0.90),
            "fuji_reala":    lambda im: ch(enh(im, saturation=0.88, contrast=1.00, brightness=1.02), r=1.02, g=1.00, b=0.95),
            "kodak_5205":    lambda im: ch(enh(im, saturation=1.05, contrast=1.10, brightness=0.98), r=1.08, g=1.00, b=0.88),
            "kodak_5218_2383":lambda im: ch(enh(im, saturation=0.90, contrast=1.12, brightness=0.95), r=1.05, g=0.98, b=0.85),
            "kodak_5218_2395":lambda im: ch(enh(im, saturation=0.92, contrast=1.10, brightness=0.96), r=1.06, g=0.99, b=0.87),
        }

        fn = FILTERS.get(filter_name)
        if fn:
            result = fn(rgb)
            if alpha is not None:
                result = result.convert("RGBA")
                result.putalpha(alpha)
                return result
            return result.convert("RGBA")
    except Exception as e:
        logger.warning(f"Error aplicando filtro '{filter_name}': {e}")
    return img


def apply_vignette(
    img: Image.Image,
    color: str = "#000000",
    opacity: float = 0.6,
    size: float = 50.0,
    sides: list = None,
    tone: str = "none",
) -> Image.Image:
    """Aplica efecto viñeta multi-lado con color, tamaño y tono configurables."""
    try:
        if sides is None:
            sides = ["top", "right", "bottom", "left"]

        TONE_RGB = {
            "sepia":  (0.75, 0.55, 0.30),
            "warm":   (0.80, 0.35, 0.05),
            "cold":   (0.05, 0.25, 0.80),
            "violet": (0.45, 0.05, 0.80),
            "green":  (0.05, 0.65, 0.15),
            "red":    (0.80, 0.05, 0.08),
            "golden": (0.85, 0.65, 0.05),
            "cyan":   (0.05, 0.65, 0.80),
        }
        if tone in TONE_RGB:
            rf, gf, bf = TONE_RGB[tone]
            rv, gv, bv = int(rf * 255), int(gf * 255), int(bf * 255)
        else:
            hx = color.lstrip("#")
            if len(hx) == 6:
                rv, gv, bv = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
            else:
                rv, gv, bv = 0, 0, 0

        w, h = img.size
        Y_idx = np.arange(h).reshape(-1, 1).astype(np.float32)
        X_idx = np.arange(w).reshape(1, -1).astype(np.float32)

        inner_pct = (20 + size * 0.6) / 100.0

        mask = np.zeros((h, w), dtype=np.float32)

        all4 = ["top", "right", "bottom", "left"]
        has_all4    = all(s in sides for s in all4)
        has_corners = any(s in sides for s in ["tl", "tr", "bl", "br"])

        def _fade_side(arr_norm):
            return np.clip(arr_norm / inner_pct, 0.0, 1.0) ** 2

        if has_all4 and not has_corners:
            cx, cy = w / 2.0, h / 2.0
            dist = np.sqrt(((X_idx - cx) / cx) ** 2 + ((Y_idx - cy) / cy) ** 2)
            inner_r = 1.0 - inner_pct
            v = np.clip((dist - inner_r) / (1.0 - inner_r), 0.0, 1.0) ** 2
            mask = np.maximum(mask, v)
        else:
            if "top" in sides:
                d = (h - Y_idx - 1) / max(h - 1, 1)
                mask = np.maximum(mask, _fade_side(1 - d))
            if "bottom" in sides:
                d = Y_idx / max(h - 1, 1)
                mask = np.maximum(mask, _fade_side(1 - d))
            if "left" in sides:
                d = (w - X_idx - 1) / max(w - 1, 1)
                mask = np.maximum(mask, _fade_side(1 - d))
            if "right" in sides:
                d = X_idx / max(w - 1, 1)
                mask = np.maximum(mask, _fade_side(1 - d))

        corner_r = inner_pct * 1.55
        def _corner_mask(cx, cy):
            dist = np.sqrt(((X_idx - cx) / max(w, 1)) ** 2 + ((Y_idx - cy) / max(h, 1)) ** 2)
            return np.clip((corner_r - dist) / corner_r, 0.0, 1.0) ** 2

        if "tl" in sides: mask = np.maximum(mask, _corner_mask(0, 0))
        if "tr" in sides: mask = np.maximum(mask, _corner_mask(w, 0))
        if "bl" in sides: mask = np.maximum(mask, _corner_mask(0, h))
        if "br" in sides: mask = np.maximum(mask, _corner_mask(w, h))

        alpha_arr = (mask * opacity * 255).clip(0, 255).astype(np.uint8)
        vign_arr  = np.zeros((h, w, 4), dtype=np.uint8)
        vign_arr[:, :, 0] = rv
        vign_arr[:, :, 1] = gv
        vign_arr[:, :, 2] = bv
        vign_arr[:, :, 3] = alpha_arr

        vign_layer = Image.fromarray(vign_arr, "RGBA")
        base       = img.convert("RGBA")
        result     = Image.alpha_composite(base, vign_layer)

        return result.convert("RGB") if img.mode == "RGB" else result
    except Exception as e:
        logger.warning(f"Error aplicando viñeta: {e}")
        return img


# ─── Utilidades de color ──────────────────────────────────────────────────────
def parse_color(color_str: str) -> tuple:
    color_str = color_str.strip()
    if color_str.startswith("rgba("):
        values = color_str.replace("rgba(", "").replace(")", "").split(",")
        r, g, b = int(values[0].strip()), int(values[1].strip()), int(values[2].strip())
        a = float(values[3].strip())
        return (r, g, b, int(a * 255))
    hex_color = color_str.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def parse_color_with_opacity(color_str: str, opacity: int = 100) -> tuple:
    color_str = color_str.strip()
    if color_str.startswith("rgba("):
        return parse_color(color_str)
    hex_color = color_str.lstrip("#")
    r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    a = int(255 * (opacity / 100))
    return (r, g, b, a)


# ─── Fuente de emoji (singleton) ─────────────────────────────────────────────
_emoji_source = None

def get_emoji_source():
    global _emoji_source
    if _emoji_source is None:
        try:
            _emoji_source = RetryTwitterEmojiSource()
            logger.info("Emoji source: RetryTwitterEmojiSource (Twemoji CDN)")
        except Exception as e:
            logger.error(f"No se pudo inicializar emoji source: {e}")
            _emoji_source = EmojiCDNSource()
    return _emoji_source


# ─── Modos de fusión tipo Photoshop ──────────────────────────────────────────
def apply_blend_mode(base: Image.Image, overlay: Image.Image, mode: str) -> Image.Image:
    """Compone overlay sobre base usando el modo de fusión indicado."""
    if mode == "normal" or not _NUMPY_OK:
        base_copy = base.copy()
        base_copy.paste(overlay, (0, 0), overlay)
        return base_copy

    b = np.array(base, dtype=np.float32) / 255.0
    o = np.array(overlay, dtype=np.float32) / 255.0

    B = b[:, :, :3]
    A = o[:, :, :3]
    alpha = o[:, :, 3:4]

    if mode == "multiply":
        blended = B * A
    elif mode == "screen":
        blended = 1.0 - (1.0 - B) * (1.0 - A)
    elif mode == "darken":
        blended = np.minimum(B, A)
    elif mode == "color_burn":
        safe_A = np.where(A < 1e-6, 1e-6, A)
        blended = np.clip(1.0 - (1.0 - B) / safe_A, 0.0, 1.0)
    elif mode == "linear_burn":
        blended = np.clip(B + A - 1.0, 0.0, 1.0)
    elif mode == "overlay":
        blended = np.where(B < 0.5, 2.0 * B * A, 1.0 - 2.0 * (1.0 - B) * (1.0 - A))
    elif mode == "soft_light":
        def D(cb):
            return np.where(cb <= 0.25,
                            ((16.0 * cb - 12.0) * cb + 4.0) * cb,
                            np.sqrt(np.clip(cb, 0.0, 1.0)))
        blended = np.where(
            A <= 0.5,
            B - (1.0 - 2.0 * A) * B * (1.0 - B),
            B + (2.0 * A - 1.0) * (D(B) - B)
        )
    else:
        blended = A

    result_rgb = np.clip(B * (1.0 - alpha) + blended * alpha, 0.0, 1.0)
    result_arr = b.copy()
    result_arr[:, :, :3] = result_rgb
    result_arr[:, :, 3]  = b[:, :, 3]
    return Image.fromarray((result_arr * 255).astype(np.uint8), "RGBA")


# ─── Warp de texto tipo Photoshop ─────────────────────────────────────────────
def _bilinear_sample(region: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    H, W = region.shape[:2]
    x0 = np.floor(sx).astype(np.int32)
    y0 = np.floor(sy).astype(np.int32)
    fx = np.clip((sx - x0.astype(np.float32))[..., np.newaxis], 0.0, 1.0)
    fy = np.clip((sy - y0.astype(np.float32))[..., np.newaxis], 0.0, 1.0)
    x0c = np.clip(x0, 0, W - 1); x1c = np.clip(x0 + 1, 0, W - 1)
    y0c = np.clip(y0, 0, H - 1); y1c = np.clip(y0 + 1, 0, H - 1)
    c00 = region[y0c, x0c].astype(np.float32)
    c10 = region[y0c, x1c].astype(np.float32)
    c01 = region[y1c, x0c].astype(np.float32)
    c11 = region[y1c, x1c].astype(np.float32)
    return c00 * (1 - fx) * (1 - fy) + c10 * fx * (1 - fy) + c01 * (1 - fx) * fy + c11 * fx * fy


def _warp_displacement(style: str, u: np.ndarray, v: np.ndarray, bend: float):
    z  = np.zeros_like(u)
    uc = np.clip(u, -1.0, 1.0)
    vc = np.clip(v, -1.0, 1.0)

    if style == 'arc':
        dv = -bend * (1.0 - uc ** 2) * 0.75
        du = bend * uc * np.abs(vc) * 0.18
        return du, dv
    elif style == 'arc_lower':
        dv = -bend * (1.0 - uc ** 2) * np.maximum(0.0, vc) * 1.3
        return z.copy(), dv
    elif style == 'arc_upper':
        dv = -bend * (1.0 - uc ** 2) * np.maximum(0.0, -vc) * 1.3
        return z.copy(), dv
    elif style == 'arch':
        dv = -bend * (1.0 - uc ** 2) * (1.0 + vc) * 0.45
        du = bend * uc * 0.10
        return du, dv
    elif style == 'bulge':
        r2 = uc ** 2 + vc ** 2
        f  = bend * np.clip(1.0 - r2, 0, 1) * 0.85
        return -uc * f, -vc * f
    elif style == 'shell_lower':
        return z.copy(), bend * uc ** 2 * 0.85
    elif style == 'shell_upper':
        return z.copy(), -bend * uc ** 2 * 0.85
    elif style == 'flag':
        t  = (uc + 1.0) * 0.5
        dv = -bend * np.sin(t * 2.0 * math.pi) * 0.65
        return z.copy(), dv
    elif style == 'wave':
        dv = -bend * np.sin(uc * 1.5 * math.pi) * 0.65
        du = -bend * np.sin(vc * math.pi * 0.5) * 0.10
        return du, dv
    elif style == 'fish':
        dv = -bend * np.sin(uc * math.pi) * 0.60
        du = -bend * vc * np.cos(uc * math.pi * 0.5) * 0.28
        return du, dv
    elif style == 'rise':
        dv = -bend * (uc + 1.0) * 0.5 * 0.75
        return z.copy(), dv
    elif style == 'fisheye':
        r = np.sqrt(uc ** 2 + vc ** 2)
        f = 1.0 + bend * np.clip(1.0 - r, 0, 1) * 0.95
        safe_f = np.where(np.abs(f) < 0.05, np.sign(f + 1e-9) * 0.05, f)
        return -(uc * (1.0 - 1.0 / safe_f)), -(vc * (1.0 - 1.0 / safe_f))
    elif style == 'inflate':
        r = np.sqrt(uc ** 2 + vc ** 2)
        f = bend * np.sin(np.clip(r, 0, 1) * math.pi * 0.5) * 0.85
        return uc * f, vc * f
    elif style == 'squeeze':
        squeeze = bend * np.cos(vc * math.pi * 0.5) * 0.65
        return u * squeeze, z.copy()
    elif style == 'twist':
        r = np.sqrt(uc ** 2 + vc ** 2)
        angle = bend * np.clip(1.0 - r, 0, 1) * math.pi * 0.85
        cos_a, sin_a = np.cos(angle), np.sin(angle)
        u_rot = uc * cos_a - vc * sin_a
        v_rot = uc * sin_a + vc * cos_a
        return uc - u_rot, vc - v_rot
    return z, z


def _star_polygon(cx: float, cy: float, outer_r: float, inner_r: float, n: int = 12):
    pts = []
    for i in range(2 * n):
        angle = math.pi * i / n - math.pi / 2
        r = outer_r if i % 2 == 0 else inner_r
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


def _apply_overlay_mask(img: Image.Image, mask_type: str, radius: int = 0) -> Image.Image:
    if mask_type == "none":
        return img
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    if mask_type == "circle":
        d = min(w, h)
        x0, y0 = (w - d) // 2, (h - d) // 2
        draw.ellipse([x0, y0, x0 + d - 1, y0 + d - 1], fill=255)
    elif mask_type == "ellipse":
        draw.ellipse([0, 0, w - 1, h - 1], fill=255)
    elif mask_type == "square":
        d = min(w, h)
        x0, y0 = (w - d) // 2, (h - d) // 2
        draw.rectangle([x0, y0, x0 + d - 1, y0 + d - 1], fill=255)
    elif mask_type == "rect":
        if radius > 0:
            draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
        else:
            draw.rectangle([0, 0, w - 1, h - 1], fill=255)
    elif mask_type == "star12":
        pts = _star_polygon(w / 2, h / 2, min(w, h) / 2 - 1, min(w, h) / 2 * 0.78 - 1, n=12)
        draw.polygon(pts, fill=255)
    else:
        return img
    result = img.copy().convert("RGBA")
    r_ch, g_ch, b_ch, a_ch = result.split()
    combined = Image.fromarray(
        np.minimum(np.array(a_ch), np.array(mask)).astype(np.uint8)
    )
    result.putalpha(combined)
    return result


def _apply_overlay_border(img: Image.Image, mask_type: str, border_width: int,
                          border_color: tuple, radius: int = 0):
    """Dibuja un borde FUERA de la máscara. Devuelve (img_expandida, expand_px)."""
    if border_width <= 0:
        return img, 0
    w, h = img.size
    bw  = border_width
    hw  = bw // 2
    exp = bw + 2
    new_w, new_h = w + 2 * exp, h + 2 * exp

    result = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    result.paste(img, (exp, exp), img)

    if mask_type == "star12":
        mask_layer = Image.new("L", (new_w, new_h), 0)
        mdraw = ImageDraw.Draw(mask_layer)
        pts = _star_polygon(exp + w / 2, exp + h / 2,
                            min(w, h) / 2, min(w, h) / 2 * 0.78, n=12)
        mdraw.polygon(pts, fill=255)
        kernel = max(3, bw * 2 + 1)
        from PIL import ImageFilter as _IF
        dilated = mask_layer.filter(_IF.MaxFilter(kernel))
        border_alpha = Image.fromarray(
            np.clip(np.array(dilated).astype(int) - np.array(mask_layer).astype(int),
                    0, 255).astype(np.uint8)
        )
        r_b, g_b, b_b, a_b = border_color
        border_layer = Image.new("RGBA", (new_w, new_h), (r_b, g_b, b_b, 0))
        border_layer.putalpha(border_alpha)
        result = Image.alpha_composite(border_layer, result)
    else:
        draw = ImageDraw.Draw(result)
        if mask_type == "circle":
            d = min(w, h)
            x0, y0 = (w - d) // 2, (h - d) // 2
            draw.ellipse([exp + x0 - hw, exp + y0 - hw,
                          exp + x0 + d - 1 + hw, exp + y0 + d - 1 + hw],
                         outline=border_color, width=bw)
        elif mask_type == "ellipse":
            draw.ellipse([exp - hw, exp - hw,
                          exp + w - 1 + hw, exp + h - 1 + hw],
                         outline=border_color, width=bw)
        elif mask_type == "square":
            d = min(w, h)
            x0, y0 = (w - d) // 2, (h - d) // 2
            draw.rectangle([exp + x0 - hw, exp + y0 - hw,
                            exp + x0 + d - 1 + hw, exp + y0 + d - 1 + hw],
                           outline=border_color, width=bw)
        else:
            if radius > 0 and mask_type == "rect":
                draw.rounded_rectangle([exp - hw, exp - hw,
                                        exp + w - 1 + hw, exp + h - 1 + hw],
                                       radius=radius + hw,
                                       outline=border_color, width=bw)
            else:
                draw.rectangle([exp - hw, exp - hw,
                                exp + w - 1 + hw, exp + h - 1 + hw],
                               outline=border_color, width=bw)
    return result, exp


def _render_canvas_shape(image: Image.Image, shape: "CanvasShape") -> None:
    """Dibuja una Forma (rect/ellipse/star12) sobre el canvas con trazo exterior."""
    sw, sh = max(1, shape.width), max(1, shape.height)
    fc = parse_color_with_opacity(shape.fill_color, int(shape.fill_opacity * 100))
    sc_color = parse_color_with_opacity(shape.stroke_color, int(shape.stroke_opacity * 100))
    stk = shape.stroke_width

    blur_val = getattr(shape, 'cover_blur', 0) or 0
    if blur_val > 0:
        bx1 = max(0, shape.x); by1 = max(0, shape.y)
        bx2 = min(image.width, shape.x + sw); by2 = min(image.height, shape.y + sh)
        if bx2 > bx1 and by2 > by1:
            radius = max(1, int(blur_val * 0.2))
            region  = image.crop((bx1, by1, bx2, by2))
            blurred = region.filter(ImageFilter.GaussianBlur(radius=radius))
            image.paste(blurred, (bx1, by1))

    layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(layer)

    if shape.shape_type in ("rect", "square"):
        draw.rectangle([0, 0, sw - 1, sh - 1], fill=fc)
    elif shape.shape_type in ("ellipse", "circle"):
        draw.ellipse([0, 0, sw - 1, sh - 1], fill=fc)
    elif shape.shape_type == "star12":
        pts = _star_polygon(sw / 2, sh / 2,
                            min(sw, sh) / 2 - 1, min(sw, sh) / 2 * 0.78 - 1, n=12)
        draw.polygon(pts, fill=fc)

    border_exp = 0
    if stk > 0:
        layer, border_exp = _apply_overlay_border(layer, shape.shape_type, stk, sc_color, 0)

    paste_x, paste_y = shape.x - border_exp, shape.y - border_exp

    if shape.rotation:
        layer = layer.rotate(-shape.rotation, expand=True, resample=Image.BICUBIC)
        new_w, new_h = layer.size
        orig_w = sw + 2 * border_exp
        orig_h = sh + 2 * border_exp
        paste_x = shape.x - border_exp + (orig_w - new_w) // 2
        paste_y = shape.y - border_exp + (orig_h - new_h) // 2

    src_x1 = max(0, -paste_x)
    src_y1 = max(0, -paste_y)
    dst_x  = max(0, paste_x)
    dst_y  = max(0, paste_y)
    src_x2 = src_x1 + min(layer.width  - src_x1, image.width  - dst_x)
    src_y2 = src_y1 + min(layer.height - src_y1, image.height - dst_y)
    if src_x2 > src_x1 and src_y2 > src_y1:
        crop = layer.crop((src_x1, src_y1, src_x2, src_y2))
        image.paste(crop, (dst_x, dst_y), crop)


def _auto_fit_overlay(img: Image.Image, mask_type: str, ov_w: int, ov_h: int) -> Image.Image:
    """Escala la imagen para cubrir el área completa del overlay sin deformar (object-fit: cover)."""
    iw, ih = img.size
    if ov_w <= 0 or ov_h <= 0 or iw <= 0 or ih <= 0:
        return img
    scale = max(ov_w / iw, ov_h / ih)
    new_w = max(1, int(iw * scale))
    new_h = max(1, int(ih * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    cx = max(0, (new_w - ov_w) // 2)
    cy = max(0, (new_h - ov_h) // 2)
    cropped = resized.crop((cx, cy, cx + ov_w, cy + ov_h))
    if cropped.size != (ov_w, ov_h):
        canvas = Image.new("RGBA", (ov_w, ov_h), (0, 0, 0, 0))
        canvas.paste(cropped, (0, 0))
        return canvas
    return cropped


def _apply_text_warp(layer: Image.Image, style: str, bend_pct: int,
                     text_x: int, text_y: int, text_w: int, text_h: int) -> Image.Image:
    """Aplica warp tipo Photoshop a la capa de texto RGBA (en resolución 2x)."""
    if not _NUMPY_OK or not style or style == 'none' or bend_pct == 0:
        return layer

    bend = max(-1.0, min(1.0, bend_pct / 100.0 * 2.5))
    arr  = np.array(layer, dtype=np.float32)
    H, W = arr.shape[:2]

    margin = int(max(text_w, text_h) * 0.65)
    rx1 = max(0, text_x - margin)
    ry1 = max(0, text_y - margin)
    rx2 = min(W, text_x + text_w + margin)
    ry2 = min(H, text_y + text_h + margin)
    rW, rH = rx2 - rx1, ry2 - ry1
    if rW <= 0 or rH <= 0:
        return layer

    gy, gx = np.mgrid[0:rH, 0:rW].astype(np.float32)

    cx = text_x + text_w * 0.5 - rx1
    cy = text_y + text_h * 0.5 - ry1
    half_w = text_w * 0.5
    half_h = text_h * 0.5

    u_norm = (gx - cx) / half_w
    v_norm = (gy - cy) / half_h

    du, dv = _warp_displacement(style, u_norm, v_norm, bend)

    sx = np.clip(gx - du * half_w, 0, rW - 1)
    sy = np.clip(gy - dv * half_h, 0, rH - 1)

    source_region = arr[ry1:ry2, rx1:rx2]
    warped = _bilinear_sample(source_region, sx, sy)

    result = arr.copy()
    result[ry1:ry2, rx1:rx2] = warped
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))


# ─── Word wrap ────────────────────────────────────────────────────────────────
def _wrap_words(text: str, font, max_width: int, draw) -> str:
    """Ajusta texto a max_width px saltando palabras completas al renglón."""
    if max_width <= 0:
        return text
    result_lines = []
    for paragraph in text.split('\n'):
        words = paragraph.split(' ')
        words = [w for w in words if w]
        if not words:
            result_lines.append('')
            continue
        current = ''
        for word in words:
            candidate = (current + ' ' + word).strip() if current else word
            try:
                bbox = draw.textbbox((0, 0), candidate, font=font)
                w_px = bbox[2] - bbox[0]
            except Exception:
                w_px = font.size * max(len(candidate), 1)
            if w_px <= max_width or not current:
                current = candidate
            else:
                result_lines.append(current)
                current = word
        if current:
            result_lines.append(current)
    return '\n'.join(result_lines)


# ─── Renderizado multilinea manual con Pilmoji ────────────────────────────────
def pilmoji_multiline(pilmoji_obj, draw_obj, xy, text, font, fill, spacing=0, text_align='center', block_width=None):
    """Renderiza texto multilinea con Pilmoji respetando el spacing explícitamente."""
    lines = text.split('\n')
    x, y  = xy
    line_h = font.size

    widths = []
    for ln in lines:
        try:
            lb = draw_obj.textbbox((0, 0), ln, font=font)
            widths.append(lb[2] - lb[0])
        except Exception:
            widths.append(font.size * max(len(ln), 1))

    bw = block_width if block_width is not None else (max(widths) if widths else 0)

    for i, ln in enumerate(lines):
        lw = widths[i]
        if text_align == 'center':
            lx = x + (bw - lw) // 2
        elif text_align == 'right':
            lx = x + (bw - lw)
        else:
            lx = x

        try:
            pilmoji_obj.text((lx, y), ln, font=font, fill=fill)
        except Exception:
            draw_obj.text((lx, y), ln, font=font, fill=fill)

        y += line_h + spacing


# ─── Renderizado de texto con emojis ─────────────────────────────────────────
def draw_text_with_effects(image: Image.Image, text_field: TextField, font, render_scale: int = 1) -> Image.Image:
    """Dibuja texto con sombra, stroke, fondo y soporte completo de emojis."""
    SCALE  = max(1, render_scale)
    width, height = image.size
    big_w, big_h  = width * SCALE, height * SCALE

    pre_pad = max(big_w, big_h) if text_field.rotation else 0
    work_w, work_h = big_w + 2 * pre_pad, big_h + 2 * pre_pad

    layer = Image.new("RGBA", (work_w, work_h), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(layer)

    try:
        font2x = ImageFont.truetype(font.path, int(font.size * SCALE))
    except Exception:
        font2x = font

    text_to_draw = text_field.text
    color        = parse_color(text_field.font_color)
    final_color  = color if len(color) == 4 else color + (255,)
    spacing      = text_field.line_spacing * SCALE
    text_align   = text_field.text_align if text_field.text_align in ("left", "center", "right") else "left"

    if getattr(text_field, 'text_wrap_enabled', False):
        pad = max(0, getattr(text_field, 'text_wrap_padding', 60))
        max_wrap_w   = max(1, image.width * SCALE - 2 * pad * SCALE)
        text_to_draw = _wrap_words(text_to_draw, font2x, max_wrap_w, draw)

    bbox        = draw.multiline_textbbox((0, 0), text_to_draw, font=font2x, spacing=spacing, align=text_align)
    text_width  = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    try:
        _lbox      = draw.textbbox((0, 0), "Ag", font=font2x)
        top_offset = _lbox[1]
    except Exception:
        top_offset = 0

    glyph_y = text_field.y * SCALE + pre_pad
    base_x  = text_field.x * SCALE + pre_pad
    if text_field.alignment == "center":
        base_x = text_field.x * SCALE + pre_pad - (text_width // 2)
    elif text_field.alignment == "right":
        base_x = text_field.x * SCALE + pre_pad - text_width
    base_y = glyph_y - top_offset

    # 1. FONDO / BACKGROUND + BORDE
    _has_bg     = text_field.background_enabled
    _has_border = text_field.background_stroke_width > 0
    if _has_bg or _has_border:
        pad_t = (text_field.background_padding_top    or 10) * SCALE
        pad_r = (text_field.background_padding_right  or 10) * SCALE
        pad_b = (text_field.background_padding_bottom or 10) * SCALE
        pad_l = (text_field.background_padding_left   or 10) * SCALE
        bx1, by1 = base_x - pad_l, glyph_y - pad_t
        bx2, by2 = base_x + text_width + pad_r, glyph_y + text_height + pad_b
        radius   = text_field.background_radius * SCALE

        if _has_bg:
            bg_alpha = int(255 * text_field.background_opacity / 100)
            bg_type  = text_field.background_color_type or "solid"
            if bg_type == "instagram":
                apply_gradient_bg(layer, bx1, by1, bx2, by2, radius, _ig_colors(bg_alpha), 45)
            elif bg_type == "negro":
                apply_gradient_bg(layer, bx1, by1, bx2, by2, radius, _negro_colors(bg_alpha), 145)
            elif bg_type == "gradient2":
                c1 = parse_color(text_field.background_color)[:3] + (bg_alpha,)
                c2 = parse_color(text_field.background_gradient_color2 or "#FFFFFF")[:3] + (bg_alpha,)
                ang = text_field.background_gradient_angle or 135
                apply_gradient_bg(layer, bx1, by1, bx2, by2, radius, [c1, c2], ang)
            else:
                bg_color = parse_color_with_opacity(text_field.background_color, text_field.background_opacity)
                if radius > 0:
                    draw.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=radius, fill=bg_color)
                else:
                    draw.rectangle([(bx1, by1), (bx2, by2)], fill=bg_color)

        if _has_border:
            if _has_bg:
                stroke_w = int(text_field.background_stroke_width * SCALE)
                half = stroke_w // 2
                bx1 -= half; by1 -= half; bx2 += half; by2 += half
            else:
                bp_t = (text_field.border_padding_top    or 10) * SCALE
                bp_r = (text_field.border_padding_right  or 20) * SCALE
                bp_b = (text_field.border_padding_bottom or 10) * SCALE
                bp_l = (text_field.border_padding_left   or 20) * SCALE
                bx1, by1 = base_x - bp_l, glyph_y - bp_t
                bx2, by2 = base_x + text_width + bp_r, glyph_y + text_height + bp_b
                stroke_w = int(text_field.background_stroke_width * SCALE)
            stroke_type  = text_field.background_stroke_type or "solid"
            stroke_alpha = int(255 * (getattr(text_field, "background_stroke_opacity", None) or 100) / 100)
            try:
                _sc_raw = text_field.background_stroke_color.strip()
                if _sc_raw.startswith("rgba("):
                    _vals = _sc_raw[5:-1].split(",")
                    stroke_alpha = int(float(_vals[3].strip()) * 255)
            except Exception:
                pass
            try:
                if stroke_type == "instagram":
                    apply_gradient_stroke(layer, bx1, by1, bx2, by2, radius, stroke_w,
                                          _ig_colors(stroke_alpha), 45)
                elif stroke_type == "metalico":
                    apply_gradient_stroke(layer, bx1, by1, bx2, by2, radius, stroke_w,
                                          _metal_colors(stroke_alpha), 90)
                elif stroke_type == "gradient2":
                    c1s = parse_color(text_field.background_stroke_color)[:3] + (stroke_alpha,)
                    c2s = parse_color(text_field.background_stroke_gradient_color2 or "#FFFFFF")[:3] + (stroke_alpha,)
                    ang_s = text_field.background_stroke_gradient_angle or 135
                    apply_gradient_stroke(layer, bx1, by1, bx2, by2, radius, stroke_w,
                                          [c1s, c2s], ang_s)
                else:
                    stroke_c   = parse_color_with_opacity(text_field.background_stroke_color, 100)
                    dash_style = getattr(text_field, 'background_stroke_dash', 'solid') or 'solid'
                    _draw_dashed_border(draw, bx1, by1, bx2, by2, radius, stroke_w, stroke_c, dash_style)
            except Exception as e:
                logger.warning(f"Error dibujando borde ({stroke_type}): {e} — borde solido de fallback")

    # 2. SOMBRA
    if text_field.shadow_enabled:
        shadow_c = parse_color_with_opacity(text_field.shadow_color, text_field.shadow_opacity)
        r_s, g_s, b_s, a_s = shadow_c

        shadow_src = Image.new("RGBA", (work_w, work_h), (0, 0, 0, 0))
        _sd = ImageDraw.Draw(shadow_src)
        try:
            _src = get_emoji_source()
            with Pilmoji(shadow_src, source=_src) as _p:
                pilmoji_multiline(_p, _sd, (base_x, base_y), text_to_draw,
                    font=font2x, fill=(255, 255, 255, 255),
                    spacing=spacing, text_align=text_align, block_width=text_width)
        except Exception:
            _sd.multiline_text(
                (base_x, base_y), text_to_draw,
                font=font2x, fill=(255, 255, 255, 255),
                spacing=spacing, align=text_align,
            )

        _, _, _, alpha = shadow_src.split()
        alpha_scaled = alpha.point(lambda p: int(p * a_s / 255))
        colorized = Image.new("RGBA", (work_w, work_h), (0, 0, 0, 0))
        colorized.paste(Image.new("RGBA", (work_w, work_h), (r_s, g_s, b_s, 255)), mask=alpha_scaled)

        raw_ox = text_field.shadow_offset_x * SCALE
        raw_oy = text_field.shadow_offset_y * SCALE
        if text_field.rotation:
            theta = math.radians(text_field.rotation)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            adj_ox = int(raw_ox * cos_t + raw_oy * sin_t)
            adj_oy = int(-raw_ox * sin_t + raw_oy * cos_t)
        else:
            adj_ox, adj_oy = int(raw_ox), int(raw_oy)
        shadow_placed = Image.new("RGBA", (work_w, work_h), (0, 0, 0, 0))
        shadow_placed.paste(colorized, (adj_ox, adj_oy))
        layer = Image.alpha_composite(layer, shadow_placed)

    # 3. STROKE
    if text_field.stroke_enabled:
        stroke_c = parse_color_with_opacity(text_field.stroke_color, text_field.stroke_opacity)
        draw.multiline_text(
            (base_x, base_y), text_to_draw, font=font2x, fill=stroke_c,
            spacing=spacing, align=text_align,
            stroke_width=text_field.stroke_width * SCALE, stroke_fill=stroke_c,
        )

    # 4. TEXTO PRINCIPAL con emojis
    tr, tg, tb = final_color[:3]
    text_layer = Image.new("RGBA", (work_w, work_h), (tr, tg, tb, 0))
    tl_draw    = ImageDraw.Draw(text_layer)
    emoji_rendered = False

    try:
        source = get_emoji_source()
        with Pilmoji(text_layer, source=source) as pilmoji:
            pilmoji_multiline(pilmoji, tl_draw, (base_x, base_y), text_to_draw,
                font=font2x, fill=final_color,
                spacing=spacing, text_align=text_align, block_width=text_width)
        emoji_rendered = True
        logger.info("Emojis renderizados con Twemoji CDN")
    except Exception as e:
        logger.warning(f"pilmoji fallo: {e} — intentando EmojiCDNSource como fallback")

    if not emoji_rendered:
        try:
            with Pilmoji(text_layer, source=EmojiCDNSource()) as pilmoji:
                pilmoji_multiline(pilmoji, tl_draw, (base_x, base_y), text_to_draw,
                    font=font2x, fill=final_color,
                    spacing=spacing, text_align=text_align, block_width=text_width)
            emoji_rendered = True
            logger.info("Emojis renderizados con EmojiCDNSource (fallback)")
        except Exception as e2:
            logger.warning(f"EmojiCDNSource tambien fallo: {e2} — usando texto plano")

    if not emoji_rendered:
        tl_draw.multiline_text(
            (base_x, base_y), text_to_draw,
            font=font2x, fill=final_color,
            spacing=spacing, align=text_align,
        )

    layer = Image.alpha_composite(layer, text_layer)

    if text_field.rotation:
        cx_2x = int(base_x + text_width / 2)
        cy_2x = int(glyph_y + text_height / 2)
        rotated = layer.rotate(
            -text_field.rotation,
            resample=Image.BICUBIC,
            expand=False,
            center=(cx_2x, cy_2x),
        )
        layer   = rotated.crop((pre_pad, pre_pad, pre_pad + big_w, pre_pad + big_h))
        base_x  -= pre_pad
        glyph_y -= pre_pad
        base_y  -= pre_pad

    skew_x = text_field.skew_x or 0
    skew_y = text_field.skew_y or 0
    if skew_x or skew_y:
        cx_sk = int(base_x + text_width  / 2)
        cy_sk = int(glyph_y + text_height / 2)
        tx = math.tan(math.radians(skew_x))
        ty = math.tan(math.radians(skew_y))
        layer = layer.transform(
            layer.size,
            Image.AFFINE,
            (1, -tx, tx * cy_sk,
             -ty,  1, ty * cx_sk),
            resample=Image.BICUBIC,
        )

    _wstyle = (getattr(text_field, 'warp_style', None) or 'none').strip()
    _wbend  = int(getattr(text_field, 'warp_bend',  None) or 0)
    if _wstyle != 'none' and _wbend != 0:
        try:
            layer = _apply_text_warp(
                layer, _wstyle, _wbend,
                int(base_x), int(glyph_y),
                int(text_width), int(text_height)
            )
        except Exception as _we:
            logger.warning(f"Error aplicando warp '{_wstyle}': {_we}")

    layer_1x = layer.resize((width, height), Image.LANCZOS)

    if text_field.shadow_blur > 0:
        r_f, g_f, b_f = final_color[:3]
        a_f = final_color[3] if len(final_color) > 3 else 255
        _, _, _, alpha_ch = layer_1x.split()
        alpha_capped = alpha_ch.point(lambda p: min(p, a_f))
        colorized_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        colorized_layer.paste(
            Image.new("RGBA", (width, height), (r_f, g_f, b_f, 255)),
            mask=alpha_capped
        )
        layer_1x = colorized_layer.filter(ImageFilter.GaussianBlur(radius=text_field.shadow_blur))

    blend_mode = (text_field.shadow_blend_mode or "normal").strip().lower()
    if blend_mode == "normal":
        image.paste(layer_1x, (0, 0), layer_1x)
    else:
        image = apply_blend_mode(image, layer_1x, blend_mode)
    return image


# ─── Utilidad de fuentes ──────────────────────────────────────────────────────
def get_font_path(font_name: str) -> str:
    font_path = FONT_MAPPING.get(font_name, "./fonts/LiberationSans-Bold.ttf")
    if not os.path.exists(font_path):
        logger.warning(f"Fuente '{font_name}' no encontrada, usando Arial")
        return "./fonts/LiberationSans-Bold.ttf"
    return font_path


# ─── Countdown helper ─────────────────────────────────────────────────────────
def _format_countdown(seconds: float, fmt: str, expired_text: str) -> str:
    """Formatea segundos restantes en una cadena de contador regresivo."""
    if seconds <= 0:
        return expired_text or "¡Oferta expirada!"
    s  = int(seconds)
    dd = s // 86400
    hh = (s % 86400) // 3600
    mm = (s % 3600)  // 60
    ss = s % 60
    if fmt == "DD:HH:MM:SS":
        return f"{dd}:{hh:02d}:{mm:02d}:{ss:02d}"
    if fmt == "HH:MM":
        return f"{hh + dd*24}:{mm:02d}"
    return f"{hh + dd*24}:{mm:02d}:{ss:02d}"
