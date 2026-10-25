#!/usr/bin/env bash
# 构建 macOS（Apple Silicon）应用包：MT3 Transcriber.app + .dmg
#
# .app 里自带全部内容（独立的 Python 3.12 + venv + 两个模型权重），双击即用：
# 不需要系统 Python、不需要终端、不需要联网。
#
# 产物:
#   dist/MT3 Transcriber.app
#   dist/mt3-transcriber-<version>-arm64.dmg
#
# 用法:
#   VERSION=0.2.0 CHECKPOINTS_DIR=$PWD/.checkpoints ./packaging/build-macos.sh
#
# 需要 macOS (Apple Silicon) + Xcode 命令行工具（clang/xcode-select --install）+
# 联网（uv/Python/pip 依赖）。建议在 GitHub Actions 的 macos-14 runner 上构建。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.0.0}"
ARCH="${ARCH:-arm64}"
OUT="${OUT:-$ROOT/dist}"
WORK="${WORK:-$ROOT/.macos}"
APP_NAME="${APP_NAME:-MT3 Transcriber}"
BUNDLE_ID="${BUNDLE_ID:-com.github.gcross.mt3-transcriber}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
CHECKPOINTS_DIR="${CHECKPOINTS_DIR:-$ROOT/.checkpoints}"
VOLUME_NAME="${VOLUME_NAME:-MT3 Transcriber}"

APP="$WORK/$APP_NAME.app"
RES="$APP/Contents/Resources"
RT="$RES/runtime"
VENV_DIR="$RT/venv"

log() { printf '\033[1;32m[macos]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[macos]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[macos]\033[0m %s\n' "$*" >&2; exit 1; }

fetch() {  # fetch URL DEST
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$2" "$1"
  else
    wget -O "$2" "$1"
  fi
}

[ "$(uname -s)" = "Darwin" ] || die "只能在 macOS 上构建 .app（当前: $(uname -s)）"
command -v clang >/dev/null 2>&1 \
  || die "需要 clang：xcode-select --install"
command -v hdiutil >/dev/null 2>&1 || die "找不到 hdiutil（应在 macOS 上）"
if [ "$(uname -m)" != "arm64" ]; then
  warn "当前架构 $(uname -m)，产物默认按 Apple Silicon（arm64）；交叉构建不受支持。"
fi

# ---------------------------------------------------------------- uv
rm -rf "$WORK"
mkdir -p "$WORK"
UV_BIN="${UV_BIN:-}"
if [ -z "$UV_BIN" ]; then
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
  else
    log "下载 uv…"
    mkdir -p "$WORK/uv"
    fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-apple-darwin.tar.gz" \
      "$WORK/uv.tar.gz"
    tar -xzf "$WORK/uv.tar.gz" -C "$WORK/uv"
    UV_BIN="$(find "$WORK/uv" -type f -name uv | head -1 || true)"
    [ -n "$UV_BIN" ] || die "uv 解压失败"
    chmod 755 "$UV_BIN"
  fi
fi
log "uv: $UV_BIN ($("$UV_BIN" --version))"

mkdir -p "$APP/Contents/MacOS" "$OUT"

export UV_PYTHON_INSTALL_DIR="$RT/python"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$WORK/uv-cache}"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"

# ---------------------------------------------------------------- Python + 依赖
log "安装 Python ${PYTHON_VERSION}（独立解释器，随包分发）…"
"$UV_BIN" python install "$PYTHON_VERSION"

log "创建虚拟环境: $VENV_DIR"
"$UV_BIN" venv --relocatable --python "$PYTHON_VERSION" \
  --python-preference only-managed "$VENV_DIR"
VENV_PY="$VENV_DIR/bin/python"

# macOS 上只支持 CPU（jax-metal 长期停更、算子不全，不随包分发）。
log "安装 JAX（CPU）…"
"$UV_BIN" pip install --python "$VENV_PY" jax

# t5x 依赖 `tensorflow-cpu`，但 PyPI 上**没有 macOS 的 tensorflow-cpu 轮子**（这个
# 包名只在 Linux/Windows 上有）。macOS 版的 `tensorflow` 本身就是 CPU-only、模块
# 完全等价，所以先装真 tensorflow，再装一个空的 `tensorflow-cpu` 兼容壳把依赖解析
# 喂饱；壳里没有任何 tensorflow 模块，`import tensorflow` 仍然解析到真包。
log "安装 TensorFlow（CPU）…"
"$UV_BIN" pip install --python "$VENV_PY" tensorflow
tf_version="$("$VENV_PY" -c 'import tensorflow; print(tensorflow.__version__)')"
tf_shim="$WORK/shims/tensorflow_cpu-${tf_version}-py3-none-any.whl"

make_tensorflow_cpu_shim() {  # make_tensorflow_cpu_shim VERSION OUT_WHEEL
  "$VENV_PY" - "$1" "$2" <<'PY'
import base64
import hashlib
import os
import sys
import zipfile

version, out = sys.argv[1], sys.argv[2]
dist = f'tensorflow_cpu-{version}.dist-info'
files = [
    (f'{dist}/METADATA',
     'Metadata-Version: 2.1\n'
     'Name: tensorflow-cpu\n'
     f'Version: {version}\n'
     'Summary: macOS shim; the macOS "tensorflow" wheel is CPU-only.\n\n'),
    (f'{dist}/WHEEL',
     'Wheel-Version: 1.0\n'
     'Generator: mt3-packaging\n'
     'Root-Is-Purelib: true\n'
     'Tag: py3-none-any\n'),
    ('tensorflow_cpu/__init__.py',
     '\"\"\"macOS shim: use `tensorflow` (the macOS wheel is CPU-only).\"\"\"\n'),
]
record = []
for name, data in files:
    digest = base64.urlsafe_b64encode(
        hashlib.sha256(data.encode()).digest()).rstrip(b'=').decode()
    record.append(f'{name},sha256={digest},{len(data.encode())}')
record.append(f'{dist}/RECORD,,')

os.makedirs(os.path.dirname(out), exist_ok=True)
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for name, data in files:
        z.writestr(name, data)
    z.writestr(f'{dist}/RECORD', '\n'.join(record) + '\n')
PY
}

log "安装 tensorflow-cpu 兼容壳（${tf_version}）…"
make_tensorflow_cpu_shim "$tf_version" "$tf_shim"
"$UV_BIN" pip install --python "$VENV_PY" "$tf_shim"

log "安装 MT3 及依赖（flax/t5x/seqio/note-seq，约 5~15 分钟）…"
"$UV_BIN" pip install --python "$VENV_PY" "$ROOT"
# 清掉 setuptools 就地构建的产物。
rm -rf "$ROOT/build" "$ROOT"/*.egg-info "$ROOT"/mt3/*.egg-info

# uv 会在 python/ 下建一个绝对符号链接（cpython-3.12-darwin-arm64 ->
# cpython-3.12.x-darwin-arm64）。拖到别的目录后它会变成死链接，改成相对。
for link in "$RT"/python/*; do
  [ -L "$link" ] || continue
  target="$(readlink "$link")"
  case "$target" in
    /*) ln -sfn "$(basename "$target")" "$link" ;;
  esac
done
rm -rf "$RT/python/.temp" "$RT/python/.lock" "$RT/python/.gitignore"

# ---------------------------------------------------------------- 让 venv 可以整体搬动
# uv 生成的 venv 里记的是构建时的绝对路径（pyvenv.cfg 的 home、bin/python 符号
# 链接），换个目录就失效。macOS 的 .app 会被用户拖来拖去，甚至直接从只读的 .dmg
# 里启动，所以这里把它们改成**相对路径**：CPython 支持相对的 home，venv 于是能跟着
# .app 一起搬——启动时不需要改动包内任何文件（也就不会破坏签名/公证）。
pybin_rel="$(cd "$RT" && ls -d python/*/bin/python3.12 | head -1 || true)"
[ -n "$pybin_rel" ] || die "未找到运行时解释器"
pydir_rel="$(dirname "$pybin_rel")"                 # python/cpython-3.12.x-.../bin
pydir_rel="$(cd "$RT/$pydir_rel" && pwd -P)"        # 追掉别名符号链接，取物理路径
pydir_rel="${pydir_rel#"$RT"/}"                     # 相对 $RT

cat > "$VENV_DIR/pyvenv.cfg" <<EOF
home = ../$pydir_rel
implementation = CPython
version_info = 3.12
include-system-site-packages = false
relocatable = true
EOF
rm -f "$VENV_DIR/bin/python" "$VENV_DIR/bin/python3" "$VENV_DIR/bin/python3.12"
ln -s "../../$pydir_rel/python3.12" "$VENV_DIR/bin/python"
ln -s python "$VENV_DIR/bin/python3"
ln -s python "$VENV_DIR/bin/python3.12"

# runtime_repair.sh 认这个文件（macOS 上用不到，但要保持格式一致）。
printf '%s\n' "$pydir_rel/python3.12" > "$VENV_DIR/.mt3-python-relpath"
printf '%s\n' "$VERSION" > "$VENV_DIR/.mt3-runtime-version"
touch "$VENV_DIR/.mt3-installed"

# 随包带一份 uv，方便用户自行给包内 venv 补装东西（可选）。
mkdir -p "$RT/uv"
cp "$UV_BIN" "$RT/uv/uv"
chmod 755 "$RT/uv/uv"

# ---------------------------------------------------------------- 组装 .app
log "组装 $APP_NAME.app…"
find "$ROOT/mt3" "$ROOT/gui" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
cp -a "$ROOT/gui" "$RES/"
cp -a "$ROOT/mt3" "$RES/"
cp -a "$ROOT/setup.py" "$RES/"
if [ -e "$ROOT/setup.cfg" ]; then
  cp -a "$ROOT/setup.cfg" "$RES/"
fi
printf '%s\n' "$VERSION" > "$RES/VERSION"

if [ -d "$CHECKPOINTS_DIR" ]; then
  mkdir -p "$RES/checkpoints"
  cp -a "$CHECKPOINTS_DIR"/. "$RES/checkpoints/"
  log "checkpoints: $(du -sh "$CHECKPOINTS_DIR" | cut -f1)"
else
  warn "未找到 ${CHECKPOINTS_DIR}：包内不含模型权重，首次使用需联网下载。"
fi

# 预编译字节码：运行时用 PYTHONDONTWRITEBYTECODE=1 启动，包内一个文件也不写
# （从只读卷运行、以及将来做签名/公证都不会受影响），预编译保证启动仍然快。
log "预编译字节码…"
"$VENV_PY" -m compileall -q "$RES/gui" "$RES/mt3" \
  "$VENV_DIR/lib/python3.12/site-packages" >/dev/null 2>&1 \
  || warn "部分源码预编译失败（不影响使用，只是启动稍慢）。"

# 图标：用已有的 PNG 生成 .icns（sips + iconutil 是 macOS 自带工具）。
if [ -f "$ROOT/packaging/mt3-transcriber.png" ]; then
  iconset="$WORK/icon.iconset"
  rm -rf "$iconset" && mkdir -p "$iconset"
  # iconutil 只认这套文件名，别加 64（会报错）。
  for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$ROOT/packaging/mt3-transcriber.png" \
      --out "$iconset/icon_${size}x${size}.png" >/dev/null
    sips -z "$((size * 2))" "$((size * 2))" "$ROOT/packaging/mt3-transcriber.png" \
      --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$iconset" -o "$RES/MT3Transcriber.icns"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>MT3 Transcriber</string>
  <key>CFBundleDisplayName</key><string>MT3 扒谱</string>
  <key>CFBundleExecutable</key><string>mt3-transcriber</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleIconFile</key><string>MT3Transcriber</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>LSApplicationCategoryType</key><string>public.app-category.music</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>MT3 Contributors. Apache-2.0.</string>
  <key>CFBundleDocumentTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeName</key><string>音频文件</string>
      <key>CFBundleTypeRole</key><string>Viewer</string>
      <key>LSHandlerRank</key><string>Alternate</string>
      <key>LSItemContentTypes</key>
      <array>
        <string>public.audio</string>
        <string>public.mp3</string>
        <string>public.mpeg-4-audio</string>
      </array>
    </dict>
  </array>
</dict>
</plist>
PLIST

printf 'APPL????' > "$APP/Contents/PkgInfo"

# 启动器：必须是 Mach-O（macOS 才会当成正经的 GUI 应用，也才能签名）。它负责
# 定位包内资源、丢掉 LaunchServices 传来的 -psn_xxx、把输出落到日志文件，然后
# 交给 gui/run.sh（bash 3.2 即可，见脚本内注释）。
cat > "$WORK/launcher.c" <<'C'
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
  char exe[PATH_MAX];
  uint32_t size = sizeof(exe);
  if (_NSGetExecutablePath(exe, &size) != 0) {
    fprintf(stderr, "mt3: 无法定位应用可执行文件\n");
    return 1;
  }
  char *slash = strrchr(exe, '/');
  if (slash == NULL) {
    return 1;
  }
  *slash = '\0';  /* .../Contents/MacOS */
  char script[PATH_MAX];
  if (snprintf(script, sizeof(script), "%s/../Resources/gui/run.sh", exe)
      >= (int)sizeof(script)) {
    fprintf(stderr, "mt3: 路径过长\n");
    return 1;
  }

  /* 双击启动时工作目录是 /，文件对话框会落在根目录；回到用户主目录。 */
  const char *home = getenv("HOME");
  if (home != NULL) {
    if (chdir(home) != 0) {
      /* 无关紧要，继续。 */
    }
  }

  /* Finder 启动的进程没有标准输出可看：把日志写进 ~/Library/Logs。 */
  if (home != NULL && !isatty(STDOUT_FILENO)) {
    char library[PATH_MAX];
    char logs[PATH_MAX];
    char logfile[PATH_MAX];
    if (snprintf(library, sizeof(library), "%s/Library", home)
        < (int)sizeof(library)) {
      (void)mkdir(library, 0755);
    }
    if (snprintf(logs, sizeof(logs), "%s/Library/Logs", home)
        < (int)sizeof(logs)) {
      (void)mkdir(logs, 0755);
      if (snprintf(logfile, sizeof(logfile), "%s/mt3-transcriber.log", logs)
          < (int)sizeof(logfile)) {
        int fd = open(logfile, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (fd >= 0) {
          (void)dup2(fd, STDOUT_FILENO);
          (void)dup2(fd, STDERR_FILENO);
          if (fd > STDERR_FILENO) {
            (void)close(fd);
          }
        }
      }
    }
  }

  /* Finder 里的中文/非 ASCII 路径与输出都按 UTF-8 处理。 */
  setenv("PYTHONUTF8", "1", 0);

  char **args = (char **)calloc((size_t)argc + 3, sizeof(char *));
  if (args == NULL) {
    return 1;
  }
  int n = 0;
  args[n++] = (char *)"/bin/bash";
  args[n++] = script;
  for (int i = 1; i < argc; i++) {
    if (strncmp(argv[i], "-psn_", 5) == 0) {
      continue;  /* 旧版 LaunchServices 的进程序列号参数。 */
    }
    args[n++] = argv[i];
  }
  args[n] = NULL;

  execv("/bin/bash", args);
  fprintf(stderr, "mt3: 无法启动 %s: %s\n", script, strerror(errno));
  return 1;
}
C
clang -O2 -Wall -Wextra -o "$APP/Contents/MacOS/mt3-transcriber" "$WORK/launcher.c" \
  || die "launcher 编译失败"
chmod 755 "$APP/Contents/MacOS/mt3-transcriber"

# 让包内脚本可执行（git 在某些文件系统上会丢掉 +x）。
chmod 755 "$RES/gui/"*.sh

# ---------------------------------------------------------------- 自检
log "自检（导入 + 后处理）…"
"$VENV_PY" "$RES/gui/import_check.py" || die "包内依赖不完整。"
"$VENV_PY" "$RES/gui/midi_rearrange.py" --selftest

# ---------------------------------------------------------------- 签名（可选）
# 没有 Apple Developer 账号时只能做 ad-hoc 签名：用户首次打开仍需「右键 → 打开」。
# 包内运行时是相对路径、启动时不改包内文件，所以签名不会被运行过程破坏。
if [ -n "${APPLE_SIGN_IDENTITY:-}" ]; then
  log "签名（${APPLE_SIGN_IDENTITY}）…"
  codesign --force --deep --options runtime --timestamp \
    --sign "$APPLE_SIGN_IDENTITY" "$APP" || die "codesign 失败"
  codesign --verify --deep --strict "$APP" || die "签名校验失败"
else
  # 签名不完整（例如某个第三方 .so 没被签过）比不签更糟：Gatekeeper 会报
  # “已损坏”而不是“未识别开发者”。校验不过就把签名信息去掉。
  if codesign --force --sign - "$APP" >/dev/null 2>&1 \
     && codesign --verify --strict "$APP" >/dev/null 2>&1; then
    log "ad-hoc 签名完成。"
  else
    warn "ad-hoc 签名不完整，去掉签名信息（首次打开需右键 → 打开）。"
    rm -rf "$APP/Contents/_CodeSignature"
  fi
fi

# ---------------------------------------------------------------- dmg
log "生成 dmg…"
stage="$WORK/dmg"
rm -rf "$stage" && mkdir -p "$stage"
# 硬链接：瞬间完成，不额外占磁盘（后续不再修改 .app）。
cp -al "$APP" "$stage/"
ln -s /Applications "$stage/Applications"
dmg="$OUT/mt3-transcriber-${VERSION}-${ARCH}.dmg"
rm -f "$dmg"
hdiutil create -volname "$VOLUME_NAME" -srcfolder "$stage" -ov -format UDZO \
  "$dmg" >/dev/null || die "hdiutil 创建 dmg 失败"
hdiutil verify "$dmg" >/dev/null 2>&1 || warn "dmg 校验未通过（仍然继续）。"

log "完成:"
du -sh "$APP" | sed 's/^/  app: /'
ls -lh "$dmg" | awk '{print "  dmg: " $9 "  " $5}'
