#!/bin/sh
# Empaqueta la herramienta en un unico fichero ejecutable.
#
#   ./build.sh            -> dist/connector.pyz  +  dist/connector.pyz.sha256
#   ./build.sh 1.2.0      -> sella esa version en el binario
#
# El .pyz es un ZIP con el codigo dentro y un shebang delante: no lleva Python,
# asi que el servidor necesita su propio Python 3.10+, pero no requiere pip,
# venv ni permisos de root.
#
# Se publica junto a su SHA-256 porque el participante se descarga un ejecutable
# y lo corre: tiene que poder comprobar que es el que le dijimos.
set -eu
cd "$(dirname "$0")"

# La version, por orden de preferencia: la que nos pasen (el workflow pasa el
# tag de la release), el tag de git mas cercano, o el marcador de build local.
# Va sellada dentro del binario porque el participante se lo descarga una vez y
# lo guarda: sin esto, ni el ni nosotros sabemos que build esta corriendo.
VERSION="${1:-${CONNECTOR_VERSION:-}}"
if [ -z "$VERSION" ]; then
    VERSION="$(git describe --tags --match 'connector-v*' --dirty 2>/dev/null || true)"
fi
VERSION="${VERSION#connector-v}"
[ -n "$VERSION" ] || VERSION="0.0.0+dev"

rm -rf dist && mkdir -p dist

# Se empaqueta una copia, no src/: sellar la version no debe tocar el arbol de
# trabajo ni dejar el repo sucio despues de construir.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R src/. "$STAGE"
find "$STAGE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
sed "s|^__version__ = .*|__version__ = \"$VERSION\"|" \
    src/bitds_connector/__init__.py > "$STAGE/bitds_connector/__init__.py"

python3 -m zipapp "$STAGE" -o dist/connector.pyz -p '/usr/bin/env python3' -c
chmod +x dist/connector.pyz
(cd dist && sha256sum connector.pyz > connector.pyz.sha256)
echo "dist/connector.pyz          $(du -h dist/connector.pyz | cut -f1)  v$VERSION"
echo "dist/connector.pyz.sha256   $(cut -c1-16 dist/connector.pyz.sha256)..."
