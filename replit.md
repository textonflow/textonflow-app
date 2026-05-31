# TextOnFlow — Documentación del Proyecto

## Descripción
Editor visual SaaS para agencias ManyChat. Permite personalizar imágenes con texto, stickers, logos, IA y variables dinámicas, generando URLs listas para usar en flujos ManyChat.

**Stack:** FastAPI + Jinja2 + Canvas API (frontend puro) + Supabase PostgreSQL  
**Producción:** Railway → www.textonflow.com  
**Deploy:** push vía GitHub REST API con `GITHUB_PERSONAL_ACCESS_TOKEN`

## Estructura de archivos clave

```
textonflow-api/
├── main.py              # Punto de entrada FastAPI — solo setup + routers
├── database.py          # Conexión Supabase + helpers SQL
├── auth.py              # JWT auth helpers
├── user_limits.py       # Lógica de límites por plan (free/starter/agency)
├── renderer.py          # Motor de renderizado de imágenes (Pillow)
├── stats.py             # Contador global de imágenes generadas
├── startup.py           # Inicialización al arrancar (tablas, configs)
├── models.py            # Modelos Pydantic compartidos
├── fonts.py             # Carga y mapeo de fuentes
├── routers/
│   ├── pages.py         # Páginas HTML + /api/stats (público) + /proxy-image
│   ├── render.py        # /generate-multi, /render-async, /render-jobs, /api/templates, etc.
│   ├── render_helpers.py# Helpers de render.py: _render_pil, _upload_output_to_supabase, etc.
│   ├── ai.py            # /api/generate-image, /api/edit-image, /api/inpaint, /api/assistant
│   ├── users.py         # /user/logos, /user/session/*, /user/track-copy
│   ├── batch.py         # /api/batch/from-url, /api/batch/preview-columns
│   ├── admin.py         # /api/admin/* (requiere X-Admin-Token)
│   └── mc.py            # /api/mc (integración ManyChat)
├── static/
│   ├── app.js           # Frontend principal (~508KB minificado)
│   ├── base.css         # Variables CSS, tokens, @font-face (fuentes Google+system)
│   ├── layout.css       # Canvas, paneles, toolbar, form-group, controls
│   ├── components.css   # AI panel, emoji picker, countdown, botones, inputs
│   ├── editor.css       # Mobile/responsive overrides, text layers, canvas handles
│   ├── i18n.js          # Traducciones ES/EN
│   └── temp/            # Archivos temporales runtime (api_templates/, timers/, tof_stats.json)
└── index.html           # App SPA principal + 9 bloques <script> inline (ver nota abajo)
```

## Arquitectura CSS — Single Source of Truth

### Archivos cargados (en orden):
1. `base.css` — Variables CSS (`--accent`, `--bg-surface`, etc.), tokens, @font-face (Google + system fonts)
2. `layout.css` — Canvas area, paneles, toolbar, form-group, controles de layout
3. `components.css` — AI panel, emoji picker, countdown, botones, inputs, modales
4. `editor.css` — Mobile/responsive overrides (breakpoints 768px, 480px, 360px), text layers, canvas handles

### Fuentes custom — dos conjuntos separados (NO duplicados):
- **base.css `@font-face`:** Google Fonts + system fonts (Inter, MeowScript, Mynerve, PassionOne, etc.)
- **index.html `<style>` Bloque 1:** Fuentes propias/compradas MYKOZ (Variex, Scholar, Geomanist, HF Bigcuat, Oishigo, TRT Burn, etc.) — ~24 familias únicas no disponibles en CDN

### Versiones de caché — UN solo número para TODOS los assets:
Todos los CSS y JS comparten la **misma versión** (`?v=NNN`). Hay un comentario
`<!-- ASSET_VERSION: vNNN ... -->` en index.html como recordatorio.

⚠️ Al modificar **cualquier** CSS o JS, subir ese único número en los 7 links/scripts
de index.html (los 4 CSS + i18n.js + app.js + artboards.js). Versión actual: **v303**.

### Bloques `<style>` inline en index.html (9 total — son intencionales):
- **CSS Bloque 1:** @font-face fuentes MYKOZ propias
- **CSS Bloque 2:** `#tof-rotate-overlay` + landscape override
- **CSS Bloque 3:** `#mobile-block-screen`
- **CSS Bloque 4:** `@keyframes tofModalIn` + `.tof-modal-overlay.active`
- **CSS Bloque 5:** `.tof-sk-row` (shortcuts overlay)

### Bloques `<script>` inline en index.html (⚠️ deuda técnica):
Hay 1043 líneas de JS en 9 bloques `<script>` inline — funciones que NO están en app.js:
- **Block 4 (91 líneas):** `launchConfetti()`
- **Block 5 (207 líneas):** Auth modal completo (`openAuthModal`, `closeAuthModal`, `authForgotSubmit`, etc.)
- **Block 6 (160 líneas):** Proyectos (`openProjectsModal`, `openProject`, `saveCurrentProject`, etc.)
- **Block 7 (532 líneas):** IA CORE (`iacSetTab`, `iacApplyBrandKit`, `iacGenerateDesign`, etc.)
- **Block 8 (13 líneas):** `cdHelpModal`, `cdHelpTab`
- **Block 9 (9 líneas):** `toggleShortcuts`, `closeShortcuts`

Estas funciones son accesibles globalmente (scripts non-module), pero su mantenimiento es difícil al estar fuera de app.js. Migración pendiente.

### `styles.css` ELIMINADO (era el monolito viejo):
Antes existía `static/styles.css` (4712 líneas) que se cargaba **primero** y los 4
split files lo sobreescribían. Un análisis a nivel de **propiedad** demostró que de
sus 955 bloques, **953 estaban 100% muertos** (cada propiedad ya redefinida por
base/layout/components/editor.css, sin `!important` que bloqueara) y solo 2 reglas
eran únicas vivas (`.var-chip-date` / `:hover`), que se migraron a `components.css`.
`styles.css` fue eliminado por completo. **Single source of truth: los 4 split files.**

### Duplicados CSS restantes (dentro de los split files):
Los selectores que aún aparecen varias veces siguen patrones válidos:
- **Media query override:** misma clase, distintos breakpoints.
- **Theme override:** `body.tof-bg-dark .canvas-area { ... }`.
No eliminar sin verificar el contexto (la herramienta de análisis está en el historial).

## Endpoints API — Mapa completo

### Públicos (sin auth):
- `GET /api/stats` — contador global de imágenes generadas
- `GET /proxy-image?url=...` — proxy de imágenes externas
- `GET /api/timer/{template_id}` — datos de contador regresivo
- `POST /api/timer/save` — guardar configuración de timer
- `GET /api/qr` — generar QR
- `GET /api/map-preview` — preview de mapa

### Con JWT (Authorization: Bearer <token>):
- `POST /api/auth/login` / `POST /api/auth/logout`
- `GET /api/usage` — uso del usuario autenticado
- `GET|POST|DELETE /api/templates` — templates de API del usuario
- `POST /api/generate-image` — generación con IA
- `POST /api/edit-image`, `POST /api/inpaint` — edición con IA
- `POST /api/save-ai-image` — guardar imagen IA a Supabase
- `GET|POST|DELETE /user/logos` — logos del cliente
- `POST /user/session/open|close` — tracking de sesión
- `POST /api/batch/from-url` — generación masiva

### Con X-Admin-Token (superadmin):
- `GET|POST /api/admin/settings`
- `GET /api/admin/users`, `GET /api/admin/stats`
- `POST /api/admin/users/toggle-active|toggle-paused|toggle-watermark|reset-renders|delete`
- `GET /api/admin/image-sessions`

### Render (BYOK con api_key):
- `GET /api/render/{template_id}` — render vía URL con variables
- `POST /api/render-stats` — estadísticas de render (renombrado de /api/stats en render.py)

## Auditoría de Código Limpio — Mayo 2026 (Sesión 3 — Limpieza ejecutada)

| Item | Antes | Después |
|------|-------|---------|
| `static/app.js.bak` (524 KB, en git) | Trackeado | Eliminado ✅ |
| JS muertos no cargados (ai-panel, auth, projects, feedback, shortcuts, cd-help) | 6 archivos / ~1108 líneas | Eliminados (local + GitHub) ✅ |
| `_DOWNLOAD_FILES` en pages.py | Listaba 6 JS borrados | Limpiado ✅ |
| `styles.css` (monolito viejo) | 4712 líneas, 953 reglas muertas | Eliminado; 2 reglas vivas migradas a components.css ✅ |
| Cache-busting `?v=` | Fragmentado (264/250/302/6) + 4 CSS sin versión | Unificado a v303 en los 7 assets ✅ |
| Warning Canvas2D `willReadFrequently` (canvas de warp) | Presente | Corregido ✅ |

> Análisis CSS a nivel de propiedad: 953/955 bloques de styles.css estaban 100% sobreescritos por los split files (0 parciales, 0 bloqueados por `!important`). Code review: PASS.

## Auditoría de Código Limpio — Mayo 2026 (Sesión 2 — Auditoría profunda)

### Tabla resumen

| Item | Antes | Después |
|------|-------|---------|
| Archivos `.backup`/`.old`/`.bak` | 0 | — ✅ |
| `console.log` en producción (app.js) | 0 | — ✅ |
| TODO/FIXME/HACK en JS y Python | 0 | — ✅ |
| Imports Python sin usar (main.py, render.py, render_helpers.py) | 0 detectados | — ✅ |
| fetch() huérfanos (sin endpoint Python) | 2 (Google Drive API — externas, esperado) | — ✅ |
| `@media (max-width: 768px)` en editor.css | **6 bloques** dispersos | **2 bloques** consolidados ✅ |
| Bloques CSS inline en index.html | 5 bloques | 5 (todos únicos, intencionales) ✅ |
| @font-face duplicados entre index.html y base.css | 0 duplicados (conjuntos distintos) | — ✅ |
| CSS duplicado entre archivos | 130 pares detectados | Revisados: todos son overrides móviles ✅ |
| Conflictos CSS base.css vs editor.css | 28 "conflictos" | Intencionales (editor.css overrides móvil) ✅ |
| Scripts inline en index.html | 1043 líneas en 9 bloques `<script>` | Documentados como deuda técnica ⚠️ |

### Falsos positivos descartados:
- **CSS "duplicados" entre archivos** → son overrides `!important` móviles en editor.css. Intencionales.
- **`@font-face` inline en index.html** → fuentes MYKOZ propias, distintas a las de base.css. No hay overlap.
- **`.acc-header` / `.acc-badge` duplicados en base.css** → solo 1 ocurrencia cada uno (falsa alarma del análisis automático).
- **Selectores `#555"]` en components.css** → CSS de atributo válido: `span[style*="color:#555"]`. No están rotos.
- **100 funciones onclick "sin window.*"** → la mayoría son: (a) `async function` top-level (globales automáticamente), (b) definidas en bloques `<script>` inline de index.html, (c) en i18n.js. Ninguna es realmente inaccesible.

### Deuda técnica documentada (sin riesgo inmediato):
- **1043 líneas de JS en `<script>` inline en index.html** — Auth modal (207 líneas), Proyectos (160), IA CORE (532), etc. Funcionan correctamente pero dificultan el mantenimiento. Migrar a archivos `.js` separados en el futuro.
- **`?v=` de caché no centralizado** — `i18n.js?v=250` y `app.js?v=284` son hardcoded. Bajo riesgo; actualizar manualmente al modificar cada archivo.

### Sesión anterior (Mayo 2026):
| Item | Antes | Después |
|------|-------|---------|
| Imágenes temp en `output/` | 6 archivos (650 KB) | 0 ✅ |
| Imágenes temp en `static/temp/` | 11 archivos (gen_*.jpg + upload_*.png) | 0 ✅ |
| Imports Python sin usar | `List` + `_get_client_ip` en batch.py | Eliminados ✅ |
| CSS "duplicados" | 61 selectores | Revisados: todos intencionales ✅ |
| Conflicto `/api/stats` duplicado | render.py sobreescribía al público de pages.py | Renombrado a `/api/render-stats` ✅ |

### Arquitectura de fuentes de verdad:
- **Auth:** `auth.py` → JWT tokens, `_get_client_ip`
- **Límites de usuario:** `user_limits.py` → `_get_current_user`, `_require_user`, `_check_user_render_limit`
- **Estadísticas:** `stats.py` → `tof_stats.json` (contador persistente local)
- **DB:** `database.py` → Supabase PostgreSQL (conexión única)
- **Logos:** tabla `user_logos` en Supabase (migrada desde localStorage en v277)
- **Helpers de render:** `render_helpers.py` → `_render_pil`, `_upload_output_to_supabase`, `_fetch_mapbox_tile`, etc.

## Preferencias del usuario
- Responder siempre en **español**
- Ejecutar mejoras UX/UI una por una, probando cada una antes de continuar
- Push a GitHub usando `GITHUB_PERSONAL_ACCESS_TOKEN` (REST API, no git CLI)
- Mapping de archivos: local `textonflow-api/X` → GitHub root `X`
- Versión `?v=` del archivo modificado debe incrementarse en cada push
