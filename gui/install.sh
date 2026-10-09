#!/usr/bin/env bash
# MT3 扒谱工具 - 目标机安装脚本（Linux）
#
# 自带 Python：使用独立的 uv 自动安装 Python 3.12 并在私有虚拟环境里装依赖，
# 因此不依赖发行版自带的 Python 版本（例如 Fedora 44 的系统 Python 3.14 也能用）。
#
# 用法:
#   ./gui/install.sh                              # 默认按 CUDA 12 安装 JAX
#   JAX_CUDA=cuda13 ./gui/install.sh              # 新驱动/新 CUDA
#   JAX_CUDA=cpu ./gui/install.sh                 # 纯 CPU
#   MT3_DOWNLOAD_CHECKPOINTS=1 ./gui/install.sh   # 同时下载模型权重（约 340MB）
#
# 需要联网（uv/Python + pip 依赖，可选模型权重）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

PYTHON_VERSION="3.12"
JAX_CUDA="${JAX_CUDA:-cuda12}"
MT3_HOME="${MT3_HOME:-$HOME/.local/share/mt3-transcriber}"

if [ "$SCRIPT_DIR" = "/usr/share/mt3-transcriber/gui" ]; then
  VENV_DIR="${VENV_DIR:-$MT3_HOME/venv}"
  export MT3_CHECKPOINT_DIR="${MT3_CHECKPOINT_DIR:-$HOME/.cache/mt3-transcriber/checkpoints}"
else
  VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv-mt3gui}"
fi

UV_DIR="${UV_DIR:-$MT3_HOME/uv}"
UV_BIN="$UV_DIR/uv"
UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$MT3_HOME/python}"
UV_CACHE_DIR="${UV_CACHE_DIR:-$MT3_HOME/uv-cache}"
export UV_PYTHON_INSTALL_DIR UV_CACHE_DIR

log() { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; exit 1; }

have_dl() { command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; }
fetch() {  # fetch URL DEST
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$2" "$1"
  else
    wget -O "$2" "$1"
  fi
}

# ---------------------------------------------------------------- 系统依赖
# 只补真正缺失的运行时；Python 由 uv 提供，不依赖系统 Python 版本。
# MT3_SKIP_SYSDEPS=1 可完全跳过系统包检查（依赖已自行装好时使用）。
missing=()
command -v git >/dev/null 2>&1 || missing+=(git)
have_dl || missing+=(curl)

pkg_name() {
  case "$1" in
    git) echo git ;;
    curl) echo curl ;;
    libsndfile)
      if command -v dnf >/dev/null 2>&1 || command -v pacman >/dev/null 2>&1; then
        echo libsndfile
      else
        echo libsndfile1
      fi
      ;;
    *) echo "$1" ;;
  esac
}

if [ "${MT3_SKIP_SYSDEPS:-0}" != "1" ] && [ "${#missing[@]}" -gt 0 ]; then
  pkgs=()
  for item in "${missing[@]}"; do pkgs+=("$(pkg_name "$item")"); done
  warn "缺少系统包: ${pkgs[*]}"
  pm=""
  for candidate in apt-get dnf pacman zypper; do
    if command -v "$candidate" >/dev/null 2>&1; then pm="$candidate"; break; fi
  done
  install_cmd=""
  case "$pm" in
    apt-get) install_cmd="sudo apt-get install -y ${pkgs[*]}" ;;
    dnf)     install_cmd="sudo dnf install -y ${pkgs[*]}" ;;
    pacman)  install_cmd="sudo pacman -S --noconfirm ${pkgs[*]}" ;;
    zypper)  install_cmd="sudo zypper install -y ${pkgs[*]}" ;;
  esac
  if [ -n "$install_cmd" ] && [ "$(id -u)" -eq 0 ]; then
    log "自动安装系统依赖…"
    if [ "$pm" = apt-get ]; then
      apt-get update -qq || true
    fi
    # shellcheck disable=SC2086
    $install_cmd
  elif [ -n "$install_cmd" ]; then
    die "请先安装: $install_cmd"
  else
    die "请先安装这些依赖: ${pkgs[*]}"
  fi
fi

# libsndfile 可选：pip 的 soundfile wheel 一般自带该库，缺失只提示不阻断。
# 注意不要用 `ldconfig -p | grep -q`：在 set -o pipefail 下 grep 提前退出会让
# ldconfig 收到 SIGPIPE 而整条管道判为失败，导致已安装也被误报。
if [ "${MT3_SKIP_SYSDEPS:-0}" != "1" ] && command -v ldconfig >/dev/null 2>&1; then
  ldconfig_out="$(ldconfig -p 2>/dev/null || true)"
  case "$ldconfig_out" in
    *libsndfile*) : ;;
    *) warn "未检测到系统 libsndfile（pip 的 soundfile 通常自带，可忽略）。" ;;
  esac
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  gpu_name="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
  log "检测到 GPU: ${gpu_name:-未知}"
else
  warn "未检测到 nvidia-smi；若使用 GPU 请先装好 NVIDIA 驱动，否则会退回 CPU。"
fi

# ---------------------------------------------------------------- uv + Python
UV="$(command -v uv || true)"
[ -n "$UV" ] || UV="$UV_BIN"
if [ ! -x "$UV" ]; then
  mkdir -p "$UV_DIR"
  log "下载 uv（独立的 Python 安装器）…"
  case "$(uname -m)" in
    x86_64|amd64) uv_arch="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) uv_arch="aarch64-unknown-linux-gnu" ;;
    *) die "暂不支持的 CPU 架构: $(uname -m)" ;;
  esac
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-${uv_arch}.tar.gz" "$tmp/uv.tar.gz"
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  found="$(find "$tmp" -type f -name uv | head -1 || true)"
  [ -n "$found" ] || die "uv 解压失败"
  cp "$found" "$UV_BIN"
  chmod 755 "$UV_BIN"
  UV="$UV_BIN"
fi
log "uv: $UV"

log "准备 Python ${PYTHON_VERSION}（uv 管理，不修改系统 Python）…"
"$UV" python install "$PYTHON_VERSION"

# 已有虚拟环境若解释器版本不符则重建（例如被系统 3.14 建坏）。
if [ -x "$VENV_DIR/bin/python" ]; then
  have_version="$("$VENV_DIR/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
  if [ "$have_version" != "$PYTHON_VERSION" ]; then
    warn "已有虚拟环境使用 Python ${have_version:-未知}，重建为 ${PYTHON_VERSION}"
    rm -rf "$VENV_DIR"
  fi
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  log "创建虚拟环境: $VENV_DIR"
  "$UV" venv --python "$PYTHON_VERSION" --python-preference only-managed "$VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"

# ---------------------------------------------------------------- 安装依赖
if [ "$JAX_CUDA" = "cpu" ]; then
  log "安装 JAX（CPU）…"
  "$UV" pip install --python "$VENV_PY" jax
else
  log "安装 JAX（$JAX_CUDA）…"
  "$UV" pip install --python "$VENV_PY" "jax[$JAX_CUDA]"
fi

log "安装 MT3 及依赖（tensorflow/flax/t5x/seqio/note-seq，约 5~15 分钟）…"
# 非 editable 安装，避免向只读的 /usr/share 写入文件。
"$UV" pip install --python "$VENV_PY" "$REPO_DIR"

# ---------------------------------------------------------------- 模型权重
if [ "${MT3_DOWNLOAD_CHECKPOINTS:-0}" = "1" ]; then
  log "下载预训练权重（约 340MB）…"
  "$VENV_PY" "$SCRIPT_DIR/download_checkpoints.py"
else
  warn "已跳过模型权重下载；首次打开图形界面或转谱时会提示下载。"
fi

# ---------------------------------------------------------------- 自检
log "环境自检…"
"$VENV_PY" - <<'PY'
import jax
import tensorflow as tf
import t5x
import note_seq
import librosa
print('  jax devices:', jax.devices())
print('  tensorflow :', tf.__version__)
print('  t5x / note_seq / librosa: OK')
PY

log "安装完成。"
log "  启动图形界面:   $SCRIPT_DIR/run.sh"
log "  无显示环境:     $SCRIPT_DIR/run.sh --cli 音频.mp3"
log "  端到端自检:     $VENV_PY $SCRIPT_DIR/selftest.py"
