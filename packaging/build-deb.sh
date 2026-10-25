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
# setup.py/setup.cfg 必须在，install.sh 才能 `pip install /usr/share/mt3-transcriber`
# 从而装齐 tensorflow/t5x/seqio/note-seq 等依赖（没有内置运行时时的兜底路径）。
cp -a "$ROOT/setup.py" "$ROOT/setup.cfg" "$PKG/usr/share/mt3-transcriber/"
# run.sh 用它判断内置运行时是否与包同版本（版本不同则重新解压）。
printf '%s\n' "$VERSION" > "$PKG/usr/share/mt3-transcriber/VERSION"
# 随包分发带 Xft 的 Tcl/Tk（由 packaging/build-tcltk.sh 生成），修复中文空白。
TCLTK_DIR="${TCLTK_DIR:-}"
if [ -n "$TCLTK_DIR" ] && [ -d "$TCLTK_DIR/lib" ]; then
  cp -a "$TCLTK_DIR" "$PKG/usr/share/mt3-transcriber/tcltk"
else
  echo "warning: TCLTK_DIR 未设置；包内将不含 Xft Tcl/Tk，中文可能显示为空白。" >&2
fi
# 内置「离线运行时」（Python + 依赖，见 packaging/build-runtime.sh）。有它则首次
# 启动无需联网；没有则退回 gui/install.sh 联网安装。
runtime_src="${RUNTIME_TARBALL:-}"
if [ -z "$runtime_src" ]; then
  runtime_src="$(ls "$ROOT"/dist/mt3-runtime*.tar.zst 2>/dev/null | head -1 || true)"
fi
if [ -n "$runtime_src" ] && [ -f "$runtime_src" ]; then
  cp "$runtime_src" "$PKG/usr/share/mt3-transcriber/runtime.tar.zst"
  echo "runtime: $(du -h "$runtime_src" | cut -f1)  源: $runtime_src"
else
  echo "warning: 未找到 runtime.tar.zst；包内不含离线运行时，首次启动需联网安装。" >&2
fi
# 内置模型权重（mt3 / ismir2021），首次启动无需下载。
checkpoints_src="${CHECKPOINTS_DIR:-$ROOT/.checkpoints}"
if [ -d "$checkpoints_src" ]; then
  mkdir -p "$PKG/usr/share/mt3-transcriber/checkpoints"
  cp -a "$checkpoints_src"/. "$PKG/usr/share/mt3-transcriber/checkpoints/"
  echo "checkpoints: $(du -sh "$checkpoints_src" | cut -f1)"
else
  echo "warning: 未找到 ${checkpoints_src}；包内不含模型权重。" >&2
fi
find "$PKG" -type d -exec chmod 755 {} +
cat > "$PKG/usr/bin/mt3-transcriber" <<'EOF'
#!/usr/bin/env bash
exec /usr/share/mt3-transcriber/gui/run.sh "$@"
EOF
chmod 755 "$PKG/usr/share/mt3-transcriber/gui/"*.sh
cp "$ROOT/packaging/mt3-transcriber.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/mt3-transcriber-web.desktop" "$PKG/usr/share/applications/"
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
Depends: zstd, ca-certificates, libsndfile1, libx11-6, libxext6, libxft2, libfontconfig1, libxrender1, libxss1
Recommends: git, curl
EOF
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/usr/bin/env bash
set -e
echo "MT3 Transcriber 已安装（含离线运行时与模型权重）。"
echo "运行 mt3-transcriber 即可使用；首次启动会在 ~/.local/share/mt3-transcriber 展开运行时，不需要联网。"
EOF
chmod 755 "$PKG/DEBIAN/postinst"
mkdir -p "$OUT"
dpkg-deb --build --root-owner-group "$PKG" "$OUT/mt3-transcriber_${VERSION}_${ARCH}.deb"
echo "Built $OUT/mt3-transcriber_${VERSION}_${ARCH}.deb"
# 暂存目录含 1.4GB 运行时/权重，打包完成后清掉（保留 dist 里的成品）。
rm -rf "$ROOT/.package"
