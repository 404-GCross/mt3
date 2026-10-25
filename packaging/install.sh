#!/usr/bin/env bash
# MT3 Transcriber 一键安装（Linux x86_64 / macOS Apple Silicon）
#
# 用法：
#   curl -fsSL https://gh-proxy.com/https://github.com/404-GCross/mt3/releases/latest/download/install.sh | bash
#   bash install.sh --dry-run                 # 只打印解析到的版本、URL 和将执行的命令
#   bash install.sh --version 0.3.1           # 固定版本
#   bash install.sh --file ./xxx.deb --yes    # 离线：装本地包
#   bash install.sh --cuda cuda13             # 装完顺手换成 CUDA 版 JAX
#   bash install.sh --uninstall
#
# 设计原则：
#   - 下载和校验都在普通用户下完成，只有最后一条安装命令用 sudo。
#   - 校验和对不上、架构不对、系统不认识——一律停下，不猜。
#   - 不静默开服务、不改防火墙、不删用户数据。

set -euo pipefail

REPO="404-GCross/mt3"
BUNDLE_ID="com.github.gcross.mt3-transcriber"

VERSION=""
LOCAL_FILE=""
DRY_RUN=0
ASSUME_YES=0
UNINSTALL=0
FORCE_MODE=""
CUDA=""
MIRROR="${MT3_MIRROR:-}"
INSTALL_PATH=""

say() { printf '\033[1;32m[mt3]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[mt3]\033[0m %s\n' "$*" >&2; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m[mt3] %s\033[0m\n' "$*" >&2; exit 1; }

run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '   %s\n' "$*"
    return 0
  fi
  "$@"
}

usage() {
  cat <<'EOF'
MT3 Transcriber 一键安装（Linux x86_64 / macOS Apple Silicon）

  --version X        指定版本（默认取最新 release）
  --file PATH        安装本地包（.deb / .rpm / .AppImage / .dmg），不下载
  --appimage         Linux 上强制用免 root 的 AppImage
  --cuda WHICH       cpu | cuda12 | cuda13（默认不装，只打印命令）
  --mirror URL       下载前缀，例如 https://gh-proxy.com/
  --dry-run          只打印会做什么，不下载、不安装
  --yes, -y          不询问（非交互环境本来就不问）
  --uninstall        卸载（保留用户数据）
  -h, --help         看这个

环境变量：MT3_MIRROR 等价于 --mirror。
EOF
}

# ------------------------------------------------------------------ 系统识别

detect_os() {
  local os
  os="${MT3_TEST_OS:-$(uname -s)}"
  case "$os" in
    Darwin) OS=macos ;;
    Linux) OS=linux ;;
    *) die "只支持 Linux 和 macOS（当前 ${os}）" ;;
  esac
}

detect_arch() {
  local arch
  arch="${MT3_TEST_ARCH:-$(uname -m)}"
  case "${OS}:${arch}" in
    linux:x86_64 | linux:amd64) ARCH=x86_64 ;;
    macos:arm64 | macos:aarch64) ARCH=arm64 ;;
    linux:aarch64 | linux:arm64)
      die "Linux 只发 x86_64 包；arm64 服务器请用 Docker 镜像或源码安装" ;;
    macos:x86_64)
      die "macOS 只支持 Apple Silicon（M 系列），Intel Mac 没有可用的 TensorFlow 轮子" ;;
    *) die "不支持的架构：${arch}" ;;
  esac
}

detect_distro() {
  if [ "$OS" = macos ]; then
    DISTRO=macos
    PACKAGE=""
    return
  fi
  local distro
  distro="${MT3_TEST_DISTRO:-}"
  if [ -z "$distro" ] && [ -r /etc/os-release ]; then
    # 在子 shell 里 source，避免覆盖本脚本的 VERSION。
    distro="$(. /etc/os-release >/dev/null 2>&1
              printf '%s %s' "${ID:-}" "${ID_LIKE:-}")"
  fi
  distro="$(printf '%s' "$distro" | tr 'A-Z' 'a-z')"
  DISTRO="${distro:-unknown}"
  case " ${distro} " in
    *" debian "* | *" ubuntu "* | *" linuxmint "* | *" pop "*) PACKAGE=deb ;;
    *" fedora "* | *" rhel "* | *" centos "* | *" rocky "* | *" almalinux "* | *" nobara "*)
      PACKAGE=rpm ;;
    *) PACKAGE="" ;;
  esac
}

check_macos_version() {
  local version major
  version="${MT3_TEST_MACOS:-$(sw_vers -productVersion 2>/dev/null || true)}"
  [ -n "$version" ] || die "读不到 macOS 版本"
  major="${version%%.*}"
  case "$major" in
    '' | *[!0-9]*) die "读不懂 macOS 版本：${version}" ;;
  esac
  [ "$major" -ge 12 ] || die "需要 macOS 12 (Monterey) 或更新（当前 ${version}）"
}

# ------------------------------------------------------------------ 参数

while [ $# -gt 0 ]; do
  case "$1" in
    --version)
      [ $# -ge 2 ] || die "--version 需要一个版本号"
      VERSION="${2#v}"
      shift 2
      ;;
    --file)
      [ $# -ge 2 ] || die "--file 需要一个路径"
      LOCAL_FILE="$2"
      shift 2
      ;;
    --appimage)
      FORCE_MODE=appimage
      shift
      ;;
    --cuda)
      [ $# -ge 2 ] || die "--cuda 需要 cpu / cuda12 / cuda13"
      CUDA="$2"
      shift 2
      ;;
    --mirror)
      [ $# -ge 2 ] || die "--mirror 需要一个前缀 URL"
      MIRROR="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --yes | -y)
      ASSUME_YES=1
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "未知参数：$1（--help 看用法）"
      ;;
  esac
done

case "$CUDA" in
  '' | cpu | cuda12 | cuda13) ;;
  *) die "--cuda 只能是 cpu / cuda12 / cuda13" ;;
esac

# ------------------------------------------------------------------ 检测

step "检查系统"
detect_os
detect_arch
detect_distro
if [ "$OS" = macos ]; then
  check_macos_version
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
else
  warn "当前是 root，安装会直接写系统目录。"
fi

if [ -n "$LOCAL_FILE" ]; then
  [ -f "$LOCAL_FILE" ] || die "文件不存在：${LOCAL_FILE}"
  LOCAL_FILE="$(cd "$(dirname "$LOCAL_FILE")" && pwd)/$(basename "$LOCAL_FILE")"
  case "$LOCAL_FILE" in
    *.deb) MODE=deb ;;
    *.rpm) MODE=rpm ;;
    *.AppImage) MODE=appimage ;;
    *.dmg) MODE=dmg ;;
    *) die "认不出的包类型：${LOCAL_FILE}" ;;
  esac
elif [ "$OS" = macos ]; then
  MODE=dmg
elif [ -n "$FORCE_MODE" ]; then
  MODE="$FORCE_MODE"
elif [ "$PACKAGE" = deb ] || [ "$PACKAGE" = rpm ]; then
  MODE="$PACKAGE"
else
  MODE=appimage
  warn "没认出发行版（${DISTRO}），改用免 root 的 AppImage。"
fi

if [ "$OS" = macos ] && [ "$MODE" != dmg ]; then
  die "macOS 只能装 .dmg（当前 ${MODE}）"
fi
if [ "$OS" = linux ] && [ "$MODE" = dmg ]; then
  die "Linux 不能装 .dmg"
fi

say "系统：${OS} / ${ARCH}（${DISTRO}）"
say "方式：${MODE}"

# ------------------------------------------------------------------ 卸载

detach() {
  hdiutil detach "$1" -quiet 2>/dev/null \
    || hdiutil detach "$1" -force -quiet 2>/dev/null || true
}

do_uninstall() {
  if [ "$OS" = macos ]; then
    local target="/Applications/MT3 Transcriber.app"
    [ -d "$target" ] || target="${HOME}/Applications/MT3 Transcriber.app"
    if [ -d "$target" ]; then
      run rm -rf "$target"
    else
      warn "没找到已安装的 MT3 Transcriber.app"
    fi
  elif [ "$MODE" = appimage ]; then
    run rm -f "${HOME}/.local/bin/mt3-transcriber" \
      "${HOME}/.local/share/applications/mt3-transcriber.desktop" \
      "${HOME}/.local/share/icons/hicolor/256x256/apps/mt3-transcriber.png"
  elif [ "$MODE" = deb ]; then
    if command -v apt-get >/dev/null 2>&1 || [ "$DRY_RUN" = 1 ]; then
      run $SUDO apt-get remove -y mt3-transcriber
    else
      die "找不到 apt-get，无法卸载 .deb"
    fi
  elif [ "$MODE" = rpm ]; then
    if command -v dnf >/dev/null 2>&1; then
      run $SUDO dnf remove -y mt3-transcriber
    elif command -v yum >/dev/null 2>&1; then
      run $SUDO yum remove -y mt3-transcriber
    elif [ "$DRY_RUN" = 1 ]; then
      run $SUDO dnf remove -y mt3-transcriber
    else
      die "找不到 dnf / yum，无法卸载 .rpm"
    fi
  fi
  say "用户数据没动：~/.cache/mt3-transcriber、~/.config/mt3-transcriber（要清就手动 rm）。"
}

if [ "$UNINSTALL" = 1 ]; then
  step "卸载"
  do_uninstall
  exit 0
fi

# ------------------------------------------------------------------ 版本与下载

resolve_version() {
  [ -n "$VERSION" ] && return 0
  local tag=""
  tag="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)" || true
  if [ -z "$tag" ]; then
    local location=""
    location="$(curl -fsSI "https://github.com/${REPO}/releases/latest" 2>/dev/null \
      | awk 'tolower($1) == "location:" { print $2 }' | tr -d '\r' | head -1)" || true
    tag="${location##*/}"
  fi
  case "$tag" in
    v[0-9]*) VERSION="${tag#v}" ;;
    *) die "解析不到最新版本，请用 --version 0.3.1 指定" ;;
  esac
}

asset_name() {
  case "$MODE" in
    deb) printf 'mt3-transcriber_%s_amd64.deb\n' "$VERSION" ;;
    rpm) printf 'mt3-transcriber-%s-1.x86_64.rpm\n' "$VERSION" ;;
    appimage) printf 'mt3-transcriber-%s-x86_64.AppImage\n' "$VERSION" ;;
    dmg) printf 'mt3-transcriber-%s-arm64.dmg\n' "$VERSION" ;;
  esac
}

sha256_check() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c -
  else
    shasum -a 256 -c -
  fi
}

download_asset() {
  local name="$1"
  local url="https://github.com/${REPO}/releases/download/v${VERSION}/${name}"
  say "下载 ${name}"
  if [ "$DRY_RUN" = 1 ]; then
    say "   ${MIRROR}${url}"
    return 0
  fi
  mkdir -p "${WORK}/dist"
  curl -fL --retry 3 --progress-bar -o "${WORK}/dist/${name}" "${MIRROR}${url}" \
    || die "下载失败：${url}（国内可加 --mirror https://gh-proxy.com/）"

  say "校验 SHA256"
  curl -fsSL -o "${WORK}/SHA256SUMS" \
    "${MIRROR}https://github.com/${REPO}/releases/download/v${VERSION}/SHA256SUMS" \
    || die "下载 SHA256SUMS 失败"
  ( cd "${WORK}" && grep -F "dist/${name}" SHA256SUMS | sha256_check ) \
    || die "SHA256 校验失败，已中止：${name}"
}

WORK=""
cleanup() {
  if [ -n "$WORK" ]; then
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT
WORK="$(mktemp -d "${TMPDIR:-/tmp}/mt3-install.XXXXXX")"

step "确定版本"
if [ -n "$LOCAL_FILE" ]; then
  say "本地文件：${LOCAL_FILE}"
else
  resolve_version
  say "版本：${VERSION}"
fi

if [ "$ASSUME_YES" != 1 ] && [ -t 0 ]; then
  printf '[mt3] 继续？[Y/n] '
  read -r answer
  case "$answer" in
    n | N | no | NO) say "已取消"; exit 0 ;;
  esac
fi

if [ -n "$LOCAL_FILE" ]; then
  PACKAGE_PATH="$LOCAL_FILE"
else
  NAME="$(asset_name)"
  download_asset "$NAME"
  PACKAGE_PATH="${WORK}/dist/${NAME}"
  if [ "$DRY_RUN" = 0 ]; then
    say "已下载并校验：${NAME}"
  fi
fi

# ------------------------------------------------------------------ 安装

install_deb_or_rpm() {
  local path="$1"
  if [ "$MODE" = deb ]; then
    if command -v apt-get >/dev/null 2>&1; then
      run $SUDO apt-get install -y "$path"
    elif command -v apt >/dev/null 2>&1; then
      run $SUDO apt install -y "$path"
    elif [ "$DRY_RUN" = 1 ]; then
      # dry-run 可能在别的发行版上跑：只打印，不要求本机真的装了 apt。
      run $SUDO apt-get install -y "$path"
    else
      die "找不到 apt / apt-get，无法安装 .deb"
    fi
  else
    if command -v dnf >/dev/null 2>&1; then
      run $SUDO dnf install -y "$path"
    elif command -v yum >/dev/null 2>&1; then
      run $SUDO yum install -y "$path"
    elif [ "$DRY_RUN" = 1 ]; then
      run $SUDO dnf install -y "$path"
    else
      die "找不到 dnf / yum，无法安装 .rpm"
    fi
  fi
}

install_appimage() {
  local path="$1"
  local bin="${HOME}/.local/bin"
  local target="${bin}/mt3-transcriber"
  local icon_dir="${HOME}/.local/share/icons/hicolor/256x256/apps"
  local desktop="${HOME}/.local/share/applications/mt3-transcriber.desktop"

  say "安装到 ${target}（不需要 root）"
  run mkdir -p "$bin"
  run cp "$path" "$target"
  run chmod 755 "$target"
  INSTALL_PATH="$target"
  if [ "$DRY_RUN" = 1 ]; then
    return 0
  fi

  # 图标从 AppImage 里取；失败也不影响命令行使用。
  (
    cd "$WORK"
    "$target" --appimage-extract mt3-transcriber.png \
      && mkdir -p "$icon_dir" \
      && cp squashfs-root/mt3-transcriber.png "$icon_dir/mt3-transcriber.png"
  ) >/dev/null 2>&1 || true

  if [ -f "${icon_dir}/mt3-transcriber.png" ]; then
    mkdir -p "$(dirname "$desktop")"
    cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=MT3 Transcriber
Name[zh_CN]=MT3 扒谱
Comment=AI music transcription to MIDI
Comment[zh_CN]=把音频转写成 MIDI
Exec=${target} %F
Icon=mt3-transcriber
Terminal=false
Categories=AudioVideo;Audio;Music;
MimeType=audio/mpeg;audio/x-wav;audio/flac;audio/ogg;audio/mp4;
EOF
    if command -v update-desktop-database >/dev/null 2>&1; then
      update-desktop-database "$(dirname "$desktop")" >/dev/null 2>&1 || true
    fi
  fi
  case ":${PATH}:" in
    *":${bin}:"*) ;;
    *) warn "${bin} 不在 PATH 里；加进 PATH 或直接用完整路径运行。" ;;
  esac
}

install_macos_dmg() {
  local dmg="$1"
  local mount="${WORK}/mnt"
  local target="/Applications/MT3 Transcriber.app"

  say "挂载 ${dmg##*/} 并复制到 /Applications"
  run mkdir -p "$mount"
  if [ "$DRY_RUN" = 1 ]; then
    say "   hdiutil attach ... ; cp -a \"MT3 Transcriber.app\" /Applications/"
    return 0
  fi

  hdiutil attach "$dmg" -nobrowse -readonly -mountpoint "$mount" >/dev/null \
    || die "挂载 dmg 失败"
  local source="${mount}/MT3 Transcriber.app"
  if [ ! -d "$source" ]; then
    detach "$mount"
    die "dmg 里没有 MT3 Transcriber.app"
  fi

  if [ -e "$target" ]; then
    local existing=""
    existing="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
      "${target}/Contents/Info.plist" 2>/dev/null || true)"
    if [ "$existing" != "$BUNDLE_ID" ]; then
      detach "$mount"
      die "${target} 已存在且不是 MT3 Transcriber，先手动处理它"
    fi
    rm -rf "$target"
  fi

  if ! cp -a "$source" /Applications/ 2>/dev/null; then
    target="${HOME}/Applications/MT3 Transcriber.app"
    mkdir -p "${HOME}/Applications"
    rm -rf "$target"
    cp -a "$source" "${HOME}/Applications/" || { detach "$mount"; die "复制 .app 失败"; }
    warn "没有写 /Applications 的权限，装到了 ${target}"
  fi
  detach "$mount"
  xattr -dr com.apple.quarantine "$target" 2>/dev/null || true
  INSTALL_PATH="$target"
}

step "安装"
case "$MODE" in
  deb | rpm) install_deb_or_rpm "$PACKAGE_PATH" ;;
  appimage) install_appimage "$PACKAGE_PATH" ;;
  dmg) install_macos_dmg "$PACKAGE_PATH" ;;
  *) die "内部错误：未知安装方式 ${MODE}" ;;
esac

# ------------------------------------------------------------------ 收尾

step "完成"
if [ "$OS" = macos ]; then
  say "打开应用：open -a \"${INSTALL_PATH}\""
  say "（也可以双击「应用程序」里的 MT3 Transcriber）"
else
  say "启动界面：mt3-transcriber"
fi
say "局域网服务：mt3-transcriber --web --lan（会在终端打印带令牌的链接）"

if [ "$OS" = linux ] && command -v nvidia-smi >/dev/null 2>&1; then
  if [ "$MODE" = appimage ]; then
    warn "检测到 NVIDIA 显卡，但 AppImage 不支持原地换 CUDA 版 JAX；需要 GPU 请改用 deb/rpm。"
  elif [ -n "$CUDA" ] && [ "$CUDA" != cpu ]; then
    step "换装 ${CUDA} 版 JAX"
    if [ "$DRY_RUN" = 1 ] || [ -x /usr/share/mt3-transcriber/gui/install_cuda_jax.sh ]; then
      run $SUDO env "JAX_CUDA=${CUDA}" \
        /usr/share/mt3-transcriber/gui/install_cuda_jax.sh
    else
      warn "找不到 install_cuda_jax.sh；手动运行："
      warn "  sudo JAX_CUDA=${CUDA} /usr/share/mt3-transcriber/gui/install_cuda_jax.sh"
    fi
  else
    warn "检测到 NVIDIA 显卡；包内默认是 CPU 版 JAX。要 GPU 加速："
    warn "  sudo JAX_CUDA=cuda13 /usr/share/mt3-transcriber/gui/install_cuda_jax.sh"
  fi
fi

if [ "$DRY_RUN" = 1 ]; then
  say "（--dry-run：以上命令都没有真正执行）"
fi
