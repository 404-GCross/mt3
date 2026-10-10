#!/usr/bin/env bash
# 构建 MT3 Transcriber 的独立 AppImage。
#
# 内置：uv 提供的 Python 3.12 + 程序代码 + CPU 版依赖（jax/tensorflow/...）。
# 不内置模型权重（首次启动时由 GUI 提示下载），因此可以控制体积、
# 作为 GitHub Release 的单个附件（< 2 GiB）发布。
#
# 用法:
#   VERSION=0.1.0 ./packaging/build-appimage.sh
#
# 需要联网（uv + Python + pip 依赖）。建议在较老的发行版（如 ubuntu:22.04）里
# 构建，以获得更宽的 glibc 兼容性。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-x86_64}"
OUT="$ROOT/dist"
WORK="$ROOT/.appimage"
APPDIR="$WORK/MT3Transcriber.AppDir"
UV_PYTHON_INSTALL_DIR="$APPDIR/usr/python"
UV_CACHE_DIR="$WORK/uv-cache"
UV_BIN="$WORK/uv"
export UV_PYTHON_INSTALL_DIR UV_CACHE_DIR

log() { printf '\033[1;32m[appimage]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[appimage]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[appimage]\033[0m %s\n' "$*" >&2; exit 1; }

fetch() {  # fetch URL DEST
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$2" "$1"
  else
    wget -O "$2" "$1"
  fi
}

rm -rf "$WORK"
mkdir -p "$APPDIR/usr/share/mt3-transcriber" "$APPDIR/usr/bin" "$OUT"

# ---------------------------------------------------------------- 程序代码
log "复制程序代码…"
find "$ROOT/mt3" "$ROOT/gui" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
cp -a "$ROOT/mt3" "$ROOT/gui" "$APPDIR/usr/share/mt3-transcriber/"
for f in setup.py setup.cfg LICENSE README.md; do
  [ -e "$ROOT/$f" ] && cp -a "$ROOT/$f" "$APPDIR/usr/share/mt3-transcriber/"
done
SOURCE_DIR="$APPDIR/usr/share/mt3-transcriber"

# ---------------------------------------------------------------- uv + Python
log "下载 uv…"
uv_arch="$ARCH-unknown-linux-gnu"
fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-${uv_arch}.tar.gz" "$WORK/uv.tar.gz"
tar -xzf "$WORK/uv.tar.gz" -C "$WORK"
found="$(find "$WORK" -maxdepth 2 -type f -name uv | head -1 || true)"
[ -n "$found" ] || die "uv 解压失败"
cp "$found" "$UV_BIN"
chmod 755 "$UV_BIN"

log "安装 Python 3.12（随 AppImage 分发的独立解释器）…"
"$UV_BIN" python install 3.12

log "创建可重定位虚拟环境并安装依赖（CPU）…"
"$UV_BIN" venv --relocatable --python 3.12 --python-preference only-managed "$APPDIR/usr/venv"
VENV_PY="$APPDIR/usr/venv/bin/python"
"$UV_BIN" pip install --python "$VENV_PY" jax
"$UV_BIN" pip install --python "$VENV_PY" "$SOURCE_DIR"

# 内置一份带 Xft 的 Tcl/Tk（uv 自带的 Tcl/Tk 未启用 Xft，中文会显示为空白），
# 并把字体栈依赖一并复制进来，做到目标机零依赖。
log "内置带 Xft 的 Tcl/Tk（字体支持）…"
TCLTK_DIR="${TCLTK_DIR:-}"
if [ -z "$TCLTK_DIR" ] || [ ! -d "$TCLTK_DIR/lib" ]; then
  TCLTK_DIR="$WORK/tcltk"
  "$ROOT/packaging/build-tcltk.sh" "$TCLTK_DIR"
fi
TCLTK_DEST="$APPDIR/usr/share/mt3-transcriber/tcltk"
mkdir -p "$(dirname "$TCLTK_DEST")"
cp -a "$TCLTK_DIR" "$TCLTK_DEST"
# 用带 Xft 的库覆盖自带的（AppImage 只读，只能复制而不能符号链接）；依赖库
# （libXft/fontconfig/freetype/...）复制进 tcltk/lib，运行时经 LD_LIBRARY_PATH 找到。
"$VENV_PY" "$ROOT/gui/fix_tk_system.py" --python "$VENV_PY" \
  --mode copy --source "$TCLTK_DEST" --deps-dir "$TCLTK_DEST/lib" \
  || warn "内置 Tcl/Tk 失败；AppImage 的中文可能显示为空白。"

# 内置一款中文字体，这样即使目标机没装任何 CJK 字体也能显示中文。
log "内置中文字体（Noto Sans CJK）…"
font_src=""
for candidate in \
  /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
  /usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf \
  /usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc; do
  if [ -e "$candidate" ]; then font_src="$candidate"; break; fi
done
mkdir -p "$APPDIR/usr/share/fonts"
if [ -n "$font_src" ]; then
  cp "$font_src" "$APPDIR/usr/share/fonts/"
  log "字体: $(basename "$font_src")"
else
  warn "未找到 Noto CJK 字体；AppImage 的中文可能显示为空白。"
  warn "构建机请先安装: sudo apt-get install fonts-noto-cjk"
fi

# ---------------------------------------------------------------- 运行时文件
log "写入 AppRun / desktop / 图标…"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
export MT3_CHECKPOINT_DIR="${MT3_CHECKPOINT_DIR:-$HOME/.cache/mt3-transcriber/checkpoints}"
export XLA_PYTHON_CLIENT_PREALLOCATE="${XLA_PYTHON_CLIENT_PREALLOCATE:-false}"
# 让 fontconfig 扫描包内字体。fontconfig 并不读取 XDG_DATA_DIRS，所以还要写一份
# 配置，把包内的 usr/share/fonts 显式加进去（并沿用系统配置使系统字体仍可用）。
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
if [ -z "${FONTCONFIG_FILE:-}" ] && [ -d "$HERE/usr/share/fonts" ]; then
  font_conf="${XDG_CACHE_HOME}/mt3-transcriber/fonts.conf"
  mkdir -p "$(dirname "$font_conf")"
  cat > "$font_conf" <<FC
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>${HERE}/usr/share/fonts</dir>
  <cachedir>${XDG_CACHE_HOME}/fontconfig</cachedir>
</fontconfig>
FC
  export FONTCONFIG_FILE="$font_conf"
fi
# 包内带 Xft 的 Tcl/Tk 及其字体栈依赖。
TCLTK_LIB="$HERE/usr/share/mt3-transcriber/tcltk/lib"
if [ -d "$TCLTK_LIB" ]; then
  export LD_LIBRARY_PATH="$TCLTK_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
for d in "$HERE"/usr/python/*/lib; do
  if [ -d "$d" ]; then
    export LD_LIBRARY_PATH="$d${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi
done
if [ -z "${TCL_LIBRARY:-}" ] && [ -d "$TCLTK_LIB/tcl9.0" ]; then
  export TCL_LIBRARY="$TCLTK_LIB/tcl9.0"
fi
if [ -z "${TK_LIBRARY:-}" ] && [ -d "$TCLTK_LIB/tk9.0" ]; then
  export TK_LIBRARY="$TCLTK_LIB/tk9.0"
fi
# 兜底：指向自带脚本目录。
if [ -z "${TCL_LIBRARY:-}" ]; then
  for d in "$HERE"/usr/python/*/lib/tcl8.6 "$HERE"/usr/python/*/lib/tcl9.0; do
    if [ -d "$d" ]; then export TCL_LIBRARY="$d"; break; fi
  done
fi
if [ -z "${TK_LIBRARY:-}" ]; then
  for d in "$HERE"/usr/python/*/lib/tk8.6 "$HERE"/usr/python/*/lib/tk9.0; do
    if [ -d "$d" ]; then export TK_LIBRARY="$d"; break; fi
  done
fi
exec "$HERE/usr/venv/bin/python" \
  "$HERE/usr/share/mt3-transcriber/gui/app.py" "$@"
EOF
chmod 755 "$APPDIR/AppRun"

cat > "$APPDIR/mt3-transcriber.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=MT3 Transcriber
Comment=AI music transcription to MIDI
Exec=mt3-transcriber
Icon=mt3-transcriber
Terminal=false
Categories=AudioVideo;Audio;Music;
EOF

cp "$ROOT/packaging/mt3-transcriber.png" "$APPDIR/mt3-transcriber.png"

# ---------------------------------------------------------------- 打包
log "下载 appimagetool…"
fetch "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${ARCH}.AppImage" \
  "$WORK/appimagetool"
chmod 755 "$WORK/appimagetool"

TARGET="$OUT/mt3-transcriber-${VERSION}-${ARCH}.AppImage"
log "生成 $TARGET"
ARCH="$ARCH" "$WORK/appimagetool" --appimage-extract-and-run "$APPDIR" "$TARGET"

log "完成: $TARGET"
ls -lh "$TARGET"
