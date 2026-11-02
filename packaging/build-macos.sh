#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${ANYLOC_PYTHON:-$ROOT/.venv/bin/python}"
VERSION="${ANYLOC_VERSION:-0.1.0}"
ARCH="$(uname -m)"
OUT="$ROOT/dist"
DMG="$OUT/AnyLoc-macOS-${VERSION}-${ARCH}.dmg"
export ANYLOC_VERSION="$VERSION"

if [[ ! -x "$PYTHON" ]]; then
  echo "先运行 README 中的源码安装命令。" >&2
  exit 1
fi
uv pip install --python "$PYTHON" --only-binary cryptography -r packaging/requirements-build.txt

BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp/}anyloc-build.XXXXXX")"
trap '"$PYTHON" -c "import shutil,sys; shutil.rmtree(sys.argv[1])" "$BUILD_DIR"' EXIT
mkdir -p "$OUT" "$BUILD_DIR/image"
"$PYTHON" -m PyInstaller packaging/AnyLoc.spec --noconfirm --clean \
  --workpath "$BUILD_DIR/build" --distpath "$BUILD_DIR/dist"
APP="$BUILD_DIR/dist/AnyLoc.app"
codesign --verify --deep --strict "$APP"
"$PYTHON" packaging/smoke-macos.py "$APP"
ditto "$APP" "$BUILD_DIR/image/AnyLoc.app"
cp packaging/USAGE.txt "$BUILD_DIR/image/USAGE.txt"
ln -s /Applications "$BUILD_DIR/image/Applications"
hdiutil create -volname "AnyLoc ${VERSION}" -fs HFS+ \
  -srcfolder "$BUILD_DIR/image" -ov -format UDZO "$DMG"
hdiutil verify "$DMG"
if [[ -d "$OUT/AnyLoc.app" ]]; then
  mv "$OUT/AnyLoc.app" "$BUILD_DIR/previous.app"
fi
ditto "$APP" "$OUT/AnyLoc.app"
(cd "$OUT" && shasum -a 256 "${DMG:t}" > "${DMG:t}.sha256")
echo "App: $OUT/AnyLoc.app"
echo "DMG: $DMG"
