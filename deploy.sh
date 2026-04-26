#!/bin/bash
# TextOnFlow — Deploy a Railway (ejecutar desde el directorio del proyecto Railway)

BASE="https://a957156e-d374-4132-9cee-a0afec9e64e1-00-2u2btyprd2joh.riker.replit.dev/api/download"
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "============================================"
echo "  TextOnFlow — Deploy a Railway"
echo "  Directorio: $DIR"
echo "============================================"

mkdir -p "$DIR/static"
EXT_DIR="$DIR/../textonflow-extension"
mkdir -p "$EXT_DIR/icons"

echo ""
echo "⬇️  Descargando archivos del servidor..."

curl -sf -o "$DIR/main.py"           "$BASE/main.py"           && echo "  ✅ main.py" || { echo "  ❌ Error: main.py"; exit 1; }
curl -sf -o "$DIR/index.html"        "$BASE/index.html"        && echo "  ✅ index.html" || { echo "  ❌ Error: index.html"; exit 1; }
curl -sf -o "$DIR/requirements.txt"  "$BASE/requirements.txt"  && echo "  ✅ requirements.txt" || { echo "  ❌ Error: requirements.txt"; exit 1; }
curl -sf -o "$DIR/nixpacks.toml"     "$BASE/nixpacks.toml"     && echo "  ✅ nixpacks.toml" || { echo "  ❌ Error: nixpacks.toml"; exit 1; }
curl -sf -o "$DIR/static/styles.css" "$BASE/styles.css"        && echo "  ✅ static/styles.css" || { echo "  ❌ Error: styles.css"; exit 1; }
curl -sf -o "$DIR/static/i18n.js"    "$BASE/i18n.js"           && echo "  ✅ static/i18n.js" || { echo "  ❌ Error: i18n.js"; exit 1; }
curl -sf -o "$DIR/static/app.js"     "$BASE/app.js"            && echo "  ✅ static/app.js" || { echo "  ❌ Error: app.js"; exit 1; }
curl -sf -o "$DIR/static/favicon.png"     "$BASE/favicon.png"      && echo "  ✅ static/favicon.png" || echo "  ⚠️  favicon.png no disponible (opcional)"
curl -sf -o "$DIR/static/logo-blanco.webp"    "$BASE/logo-blanco.webp"    && echo "  ✅ static/logo-blanco.webp" || echo "  ⚠️  logo-blanco.webp no disponible (opcional)"
curl -sf -o "$DIR/static/logo-negro.webp"     "$BASE/logo-negro.webp"     && echo "  ✅ static/logo-negro.webp" || echo "  ⚠️  logo-negro.webp no disponible (opcional)"
curl -sf -o "$DIR/static/logo-gris.webp"      "$BASE/logo-gris.webp"      && echo "  ✅ static/logo-gris.webp" || echo "  ⚠️  logo-gris.webp no disponible (opcional)"
curl -sf -o "$DIR/static/logo-negro-new.png"  "$BASE/logo-negro-new.png"  && echo "  ✅ static/logo-negro-new.png" || echo "  ⚠️  logo-negro-new.png no disponible (opcional)"
curl -sf -o "$DIR/static/logo-blanco-new.png" "$BASE/logo-blanco-new.png" && echo "  ✅ static/logo-blanco-new.png" || echo "  ⚠️  logo-blanco-new.png no disponible (opcional)"
curl -sf -o "$DIR/static/manual.html"     "$BASE/manual.html"      && echo "  ✅ static/manual.html" || echo "  ⚠️  manual.html no disponible"
curl -sf -o "$DIR/static/privacidad.html" "$BASE/privacidad.html"  && echo "  ✅ static/privacidad.html" || echo "  ⚠️  privacidad.html no disponible"
curl -sf -o "$DIR/static/terminos.html"   "$BASE/terminos.html"    && echo "  ✅ static/terminos.html" || echo "  ⚠️  terminos.html no disponible"
curl -sf -o "$DIR/static/faq.html"        "$BASE/faq.html"         && echo "  ✅ static/faq.html" || echo "  ⚠️  faq.html no disponible"
curl -sf -o "$DIR/static/casos.html"      "$BASE/casos.html"       && echo "  ✅ static/casos.html" || echo "  ⚠️  casos.html no disponible"

echo ""
echo "⬇️  Descargando fuentes tipográficas..."
mkdir -p "$DIR/fonts"
FONTS=(
  "LiberationSans-Regular.ttf"
  "LiberationSans-Bold.ttf"
  "LiberationSans-Italic.ttf"
  "LiberationSans-BoldItalic.ttf"
  "MeowScript-Regular.ttf"
  "Mynerve-Regular.ttf"
  "PlaywriteAUQLD-Regular.ttf"
  "SpicyRice-Regular.ttf"
  "PassionOne-Regular.ttf"
  "Doto-Regular.ttf"
  "HennyPenny-Regular.ttf"
  "RockSalt-Regular.ttf"
  "Arkipelago-Regular.ttf"
  "HFBigcuat-Regular.ttf"
  "HFBigcuat-Doodle.ttf"
  "Oishigo-Regular.ttf"
  "OraqleScript-Regular.ttf"
  "OraqleSwash-Regular.otf"
  "Variex-Light.ttf"
  "Scholar-Regular.otf"
  "Scholar-Italic.otf"
  "Geomanist-Regular.otf"
  "Geomanist-Italic.otf"
  "Geomanist-Bold.otf"
  "Geomanist-Bold-Italic.otf"
)
for FONT in "${FONTS[@]}"; do
  curl -sf -o "$DIR/fonts/$FONT" "$BASE/fonts/$FONT" && echo "  ✅ fonts/$FONT" || echo "  ⚠️  fonts/$FONT no disponible"
done

echo ""
echo "⬇️  Actualizando previews de estilos IA..."
mkdir -p "$DIR/static/previews"
curl -sf -o "$DIR/static/previews/biblica.jpg"  "$BASE/previews/biblica.jpg"  && echo "  ✅ static/previews/biblica.jpg" || echo "  ⚠️  biblica.jpg no disponible"
curl -sf -o "$DIR/static/previews/plumilla.jpg" "$BASE/previews/plumilla.jpg" && echo "  ✅ static/previews/plumilla.jpg" || echo "  ⚠️  plumilla.jpg no disponible"

echo ""
echo "⬇️  Actualizando extensión de Chrome..."
curl -sf -o "$EXT_DIR/manifest.json" "$BASE/ext-manifest" && echo "  ✅ extensión/manifest.json" || echo "  ⚠️  manifest.json no disponible"
curl -sf -o "$EXT_DIR/background.js" "$BASE/ext-background" && echo "  ✅ extensión/background.js" || echo "  ⚠️  background.js no disponible"
curl -sf -o "$EXT_DIR/popup.html"    "$BASE/ext-popup-html" && echo "  ✅ extensión/popup.html" || echo "  ⚠️  popup.html no disponible"
curl -sf -o "$EXT_DIR/popup.js"      "$BASE/ext-popup-js"   && echo "  ✅ extensión/popup.js" || echo "  ⚠️  popup.js no disponible"
curl -sf -o "$EXT_DIR/popup.css"     "$BASE/ext-popup-css"  && echo "  ✅ extensión/popup.css" || echo "  ⚠️  popup.css no disponible"
curl -sf -o "$EXT_DIR/icons/icon16.png"  "$BASE/ext-icon16"  && echo "  ✅ extensión/icons/icon16.png" || echo "  ⚠️  icon16.png no disponible"
curl -sf -o "$EXT_DIR/icons/icon48.png"  "$BASE/ext-icon48"  && echo "  ✅ extensión/icons/icon48.png" || echo "  ⚠️  icon48.png no disponible"
curl -sf -o "$EXT_DIR/icons/icon128.png" "$BASE/ext-icon128" && echo "  ✅ extensión/icons/icon128.png" || echo "  ⚠️  icon128.png no disponible"

echo "3.12" > "$DIR/.python-version" && echo "  ✅ .python-version (forzando Python 3.12)"

# ──────────────────────────────────────────────────────────────────
# PYARMOR — Ofuscación de main.py (protección de código)
# Requiere: pip install pyarmor (instalación local única)
# Si no está instalado, se sube main.py sin ofuscar (advertencia).
# ──────────────────────────────────────────────────────────────────
echo ""
echo "🔐 Verificando PyArmor para ofuscación de código..."

PYARMOR_BIN=$(which pyarmor 2>/dev/null || python3 -m pyarmor --version 2>/dev/null && echo "python3 -m pyarmor" || echo "")

if command -v pyarmor &>/dev/null; then
    echo "  ✅ PyArmor encontrado — ofuscando main.py..."
    cd "$DIR"
    rm -rf dist/
    pyarmor gen main.py 2>&1 | grep -v "^$" | sed 's/^/     /'
    if [ -f "dist/main.py" ]; then
        cp main.py main.py.original_backup
        cp dist/main.py main.py
        # Copiar el runtime de PyArmor junto al proyecto
        RUNTIME_DIR=$(ls -d dist/pyarmor_runtime_* 2>/dev/null | head -1)
        if [ -n "$RUNTIME_DIR" ]; then
            RUNTIME_NAME=$(basename "$RUNTIME_DIR")
            cp -r "$RUNTIME_DIR" "$DIR/$RUNTIME_NAME"
            echo "  ✅ main.py ofuscado + runtime $RUNTIME_NAME copiado"
        else
            echo "  ✅ main.py ofuscado (sin directorio de runtime detectado)"
        fi
        rm -rf dist/
    else
        echo "  ⚠️  PyArmor no generó dist/main.py — subiendo sin ofuscar"
        cp main.py.original_backup main.py 2>/dev/null || true
    fi
    cd "$DIR"
else
    echo "  ℹ️  PyArmor no instalado — se sube sin ofuscar (opcional)"
fi

echo ""
echo "🔍 Versión a subir:"
grep -o 'app.js?v=[0-9]*' "$DIR/index.html"

echo ""
echo "🚀 Subiendo a Railway..."
cd "$DIR" && railway up

echo ""
echo "============================================"
echo "  ✅ Deploy completado."
echo "  Después de este deploy, Railway descargará"
echo "  los archivos actualizados automáticamente."
echo "============================================"
echo ""
echo "📦 Para subir la extensión al Chrome Web Store:"
echo "  1. Crea el ZIP con este comando:"
echo "     cd $EXT_DIR/.. && zip -r textonflow-extension.zip textonflow-extension/ -x '*.DS_Store'"
echo "  2. Sube el archivo textonflow-extension.zip al Chrome Web Store"
echo "     (carpeta: $(dirname $EXT_DIR))"
echo "============================================"
