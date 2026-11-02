#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${ANYLOC_PYTHON:-$ROOT/.venv/bin/python}"
PYINSTALLER="$ROOT/.venv/bin/pyinstaller"
VERSION="${ANYLOC_VERSION:-0.1.0}"
ARCH="$(uname -m)"
OUT="$ROOT/dist"
DMG="$OUT/AnyLoc-macOS-${VERSION}-${ARCH}.dmg"

if [[ ! -x "$PYTHON" ]]; then
  echo "找不到 Python: $PYTHON" >&2
  exit 1
fi
if [[ ! -x "$PYINSTALLER" ]]; then
  echo "正在安装 PyInstaller..."
  uv pip install --python "$PYTHON" pyinstaller
fi

rm -rf "$ROOT/build" "$OUT/AnyLoc.app" "$DMG" "$OUT/AnyLoc-dmg"
mkdir -p "$OUT/AnyLoc-dmg"

echo "正在构建 AnyLoc.app（$ARCH）..."
"$PYINSTALLER" "$ROOT/packaging/AnyLoc.spec" --noconfirm --clean

if [[ ! -d "$OUT/AnyLoc.app" ]]; then
  echo "构建失败：没有生成 $OUT/AnyLoc.app" >&2
  exit 1
fi

cp -R "$OUT/AnyLoc.app" "$OUT/AnyLoc-dmg/"
cp "$ROOT/packaging/USAGE.txt" "$OUT/AnyLoc-dmg/"
ln -s /Applications "$OUT/AnyLoc-dmg/Applications"

echo "正在生成 DMG..."
hdiutil create \
  -volname "AnyLoc ${VERSION}" \
  -srcfolder "$OUT/AnyLoc-dmg" \
  -ov \
  -format UDZO \
  "$DMG" >/dev/null

rm -rf "$OUT/AnyLoc-dmg"
echo "完成："
echo "  App: $OUT/AnyLoc.app"
echo "  DMG: $DMG"
du -sh "$OUT/AnyLoc.app" "$DMG"
