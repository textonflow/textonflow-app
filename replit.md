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
│   ├── render.py        # /api/generate-multi, /api/usage, /api/templates, /api/render-stats
│   ├── ai.py            # /api/generate-image, /api/edit-image, /api/inpaint, /api/assistant
│   ├── users.py         # /user/logos, /user/session/*, /user/track-copy
│   ├── batch.py         # /api/batch/from-url, /api/batch/preview-columns
│   ├── admin.py         # /api/admin/* (requiere X-Admin-Token)
│   └── mc.py            # /api/mc (integración ManyChat)
├── static/
│   ├── app.js           # Frontend principal (~1650 líneas minificadas)
│   ├── styles.css       # Estilos principales (~4500 líneas)
│   ├── i18n.js          # Traducciones ES/EN
│   └── temp/            # Archivos temporales runtime (api_templates/, timers/, tof_stats.json)
└── index.html           # App SPA principal (~3730 líneas)
```

## Arquitectura CSS — Single Source of Truth

### Archivos cargados (en orden):
1. `/static/styles.css?v=NNN` — **ÚNICO archivo CSS externo**
2. 3 bloques `<style>` inline en index.html (intencionales, no duplican styles.css):
   - Bloque 1: `@font-face` para fuentes custom (Variex, Scholar, Geomanist, etc.)
   - Bloque 2: `#tof-rotate-overlay` (overlay de rotación móvil) + `@media landscape`
   - Bloque 3: `@keyframes tofModalIn` + `.tof-modal-overlay.active` (modal genérico)

### Versiones de caché (mantener sincronizadas manualmente):
- `styles.css?v=262` — línea 13 de index.html
- `i18n.js?v=250` — línea 2138 de index.html  
- `app.js?v=279` — línea 2139 de index.html

⚠️ Al modificar cualquiera de estos archivos, incrementar su `?v=` correspondiente.

### Patrón de "duplicados" CSS — Son intencionales:
`styles.css` tiene ~61 selectores que aparecen múltiples veces. Casi todos siguen uno de estos patrones válidos:
- **Media query override:** misma clase, distintos breakpoints (`@media (max-width: 768px)`)
- **Theme override:** `body.tof-bg-dark .canvas-area { ... }` — tema oscuro/claro/gris
- **Responsive compacto:** ajuste de padding/font-size para pantallas pequeñas

No son errores. No eliminar sin verificar el contexto.

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

## Auditoría de Código Limpio — Mayo 2026

### Resultados

| Item | Antes | Después |
|------|-------|---------|
| Imágenes temp en `output/` | 6 archivos (650 KB) | 0 ✅ |
| Imágenes temp en `static/temp/` | 11 archivos (gen_*.jpg + upload_*.png) | 0 ✅ |
| Imports Python sin usar | `List` + `_get_client_ip` en batch.py | Eliminados ✅ |
| `console.log` en producción | 0 reales (solo `console.error` en catch válido) | — ✅ |
| Archivos `.backup`/`.old`/`.bak` | 0 | — ✅ |
| CSS "duplicados" | 61 selectores | Revisados: todos intencionales ✅ |
| Conflicto `/api/stats` duplicado | render.py sobreescribía al público de pages.py | Renombrado a `/api/render-stats` ✅ |

### Falsos positivos descartados:
- `smtplib`/`MIMEText`/`MIMEMultipart` en `ai.py` → imports locales dentro de función, usados
- `_np` en `pages.py` → import condicional con `try/except`, usado con `_NUMPY_OK`
- `_get_current_user` en `batch.py` → sí usado (vía `_require_user` que lo llama internamente)
- CSS "duplicados" → revisados, todos son media query overrides o theme variants

### Arquitectura de fuentes de verdad:
- **Auth:** `auth.py` → JWT tokens, `_get_client_ip`
- **Límites de usuario:** `user_limits.py` → `_get_current_user`, `_require_user`, `_check_user_render_limit`
- **Estadísticas:** `stats.py` → `tof_stats.json` (contador persistente local)
- **DB:** `database.py` → Supabase PostgreSQL (conexión única)
- **Logos:** tabla `user_logos` en Supabase (migrada desde localStorage en v277)

## Preferencias del usuario
- Responder siempre en **español**
- Ejecutar mejoras UX/UI una por una, probando cada una antes de continuar
- Push a GitHub usando `GITHUB_PERSONAL_ACCESS_TOKEN` (REST API, no git CLI)
- Mapping de archivos: local `textonflow-api/X` → GitHub root `X`
- Versión `?v=` del archivo modificado debe incrementarse en cada push
