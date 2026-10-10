#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-amd64}"
OUT="$ROOT/dist"
PKG="$ROOT/.package/mt3-transcriber"
rm -rf "$PKG" "$OUT"
find "$ROOT/mt3" "$ROOT/gui" "$ROOT/packaging" -type d -name __pycache__ -prune -exec rm -rf {} +
mkdir -p "$PKG/DEBIAN" "$PKG/usr/share/mt3-transcriber" \
  "$PKG/usr/share/applications" "$PKG/usr/bin" \
  "$PKG/usr/share/icons/hicolor/256x256/apps"
cp -a "$ROOT/mt3" "$ROOT/gui" "$ROOT/packaging" "$PKG/usr/share/mt3-transcriber/"
# 随包分发带 Xft 的 Tcl/Tk（由 packaging/build-tcltk.sh 生成），修复中文空白。
TCLTK_DIR="${TCLTK_DIR:-}"
if [ -n "$TCLTK_DIR" ] && [ -d "$TCLTK_DIR/lib" ]; then
  cp -a "$TCLTK_DIR" "$PKG/usr/share/mt3-transcriber/tcltk"
else
  echo "warning: TCLTK_DIR 未设置；包内将不含 Xft Tcl/Tk，中文可能显示为空白。" >&2
fi
find "$PKG" -type d -exec chmod 755 {} +
cat > "$PKG/usr/bin/mt3-transcriber" <<'EOF'
#!/usr/bin/env bash
exec /usr/share/mt3-transcriber/gui/run.sh "$@"
EOF
chmod 755 "$PKG/usr/share/mt3-transcriber/gui/"*.sh
cp "$ROOT/packaging/mt3-transcriber.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/mt3-transcriber.png" \
  "$PKG/usr/share/icons/hicolor/256x256/apps/"
cat > "$PKG/DEBIAN/control" <<EOF
Package: mt3-transcriber
Version: $VERSION
Section: sound
Priority: optional
Architecture: $ARCH
Maintainer: MT3 Contributors
Description: MT3 music transcription desktop application
 Audio to MIDI transcription with piano and multi-instrument models.
Depends: git, curl, ca-certificates, libsndfile1, libx11-6, libxext6, libxft2, libfontconfig1, libxrender1, libxss1
EOF
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/usr/bin/env bash
set -e
echo "MT3 Transcriber 已安装。首次使用请运行: mt3-transcriber"
echo "首次启动会安装 Python 依赖并下载模型权重。"
EOF
chmod 755 "$PKG/DEBIAN/postinst"
mkdir -p "$OUT"
dpkg-deb --build --root-owner-group "$PKG" "$OUT/mt3-transcriber_${VERSION}_${ARCH}.deb"
echo "Built $OUT/mt3-transcriber_${VERSION}_${ARCH}.deb"
