#!/usr/bin/env bash
# 给已有的 MT3 环境换装 CUDA 版 JAX（GPU 转谱）。
#
# deb/rpm 内置的运行时是 CPU 版 JAX；装了 NVIDIA 驱动、想做 GPU 转谱时跑一次本
# 脚本即可（会写入 ~/.local/share/mt3-transcriber，不碰系统）。
#
# 用法:
#   mt3-transcriber 需要先装好 NVIDIA 驱动（nvidia-smi 可用）
#   ./gui/install_cuda_jax.sh              # CUDA 12（推荐，兼容面广）
#   JAX_CUDA=cuda13 ./gui/install_cuda_jax.sh
#
# 国内加速：MT3_GH_PROXY / MT3_PIP_INDEX，或写进
# ~/.config/mt3-transcriber/mirror.conf（与环境变量同义，环境变量优先）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JAX_CUDA="${JAX_CUDA:-cuda12}"
MT3_HOME="${MT3_HOME:-$HOME/.local/share/mt3-transcriber}"
VENV_DIR="${VENV_DIR:-$MT3_HOME/venv}"

log() { printf '\033[1;32m[cuda]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[cuda]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[cuda]\033[0m %s\n' "$*" >&2; exit 1; }

[ -x "$VENV_DIR/bin/python" ] || die "未找到 MT3 环境: $VENV_DIR（先运行 mt3-transcriber）"
if ! command -v nvidia-smi >/dev/null 2>&1; then
  warn "未检测到 nvidia-smi；没有 NVIDIA 驱动时装了 CUDA 版 JAX 也只能用 CPU。"
fi

# ---------------------------------------------------------------- 镜像源（可选）
mirror_conf="${XDG_CONFIG_HOME:-$HOME/.config}/mt3-transcriber/mirror.conf"
if [ -f "$mirror_conf" ]; then
  # shellcheck disable=SC1090
  . "$mirror_conf"
fi
MT3_GH_PROXY="${MT3_GH_PROXY:-}"
MT3_PIP_INDEX="${MT3_PIP_INDEX:-}"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"
if [ -n "$MT3_PIP_INDEX" ]; then
  log "PyPI 镜像: $MT3_PIP_INDEX"
  : "${UV_DEFAULT_INDEX:=$MT3_PIP_INDEX}"
  : "${UV_INDEX_URL:=$MT3_PIP_INDEX}"
  export UV_DEFAULT_INDEX UV_INDEX_URL
fi

fetch() {  # fetch URL DEST
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$2" "$1"
  else
    wget -O "$2" "$1"
  fi
}

# ---------------------------------------------------------------- uv
UV="$(command -v uv || true)"
if [ -z "$UV" ] && [ -x "$MT3_HOME/uv/uv" ]; then
  UV="$MT3_HOME/uv/uv"
fi
if [ -z "$UV" ]; then
  log "下载 uv…"
  mkdir -p "$MT3_HOME/uv"
  case "$(uname -m)" in
    x86_64|amd64) uv_arch="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) uv_arch="aarch64-unknown-linux-gnu" ;;
    *) die "暂不支持的 CPU 架构: $(uname -m)" ;;
  esac
  url="https://github.com/astral-sh/uv/releases/latest/download/uv-${uv_arch}.tar.gz"
  [ -n "$MT3_GH_PROXY" ] && url="${MT3_GH_PROXY%/}/$url"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  fetch "$url" "$tmp/uv.tar.gz"
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  found="$(find "$tmp" -type f -name uv | head -1 || true)"
  [ -n "$found" ] || die "uv 解压失败"
  cp "$found" "$MT3_HOME/uv/uv"
  chmod 755 "$MT3_HOME/uv/uv"
  UV="$MT3_HOME/uv/uv"
fi
log "uv: $UV"

# ---------------------------------------------------------------- 安装 CUDA 版 JAX
log "安装 jax[$JAX_CUDA]（约 3GB，首次较慢）…"
"$UV" pip install --python "$VENV_DIR/bin/python" "jax[$JAX_CUDA]"

log "验证…"
"$VENV_DIR/bin/python" - <<'PY'
import jax
devices = jax.devices()
print('  jax', jax.__version__, 'devices:', devices)
if not any(d.platform == 'gpu' for d in devices):
  print('  ⚠ 没有检测到 GPU：请确认 NVIDIA 驱动与 CUDA 版本（可能需要 JAX_CUDA=cuda13）。')
PY
log "完成。现在可以在界面里把「设备」选成 NVIDIA GPU 了。"
