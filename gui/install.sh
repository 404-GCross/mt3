#!/usr/bin/env bash
# MT3 扒谱工具 - 目标机安装脚本（Linux + NVIDIA GPU）
#
# 用法:
#   ./gui/install.sh                 # 默认按 CUDA 12 安装 JAX
#   JAX_CUDA=cuda13 ./gui/install.sh # 新驱动/新 CUDA 可选 cuda13
#   SKIP_CHECKPOINTS=1 ./gui/install.sh
#
# 需要联网（pip 依赖 + 预训练权重约 340MB）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
if [ "$SCRIPT_DIR" = "/usr/share/mt3-transcriber/gui" ]; then
  VENV_DIR="${VENV_DIR:-$HOME/.local/share/mt3-transcriber/venv}"
  export MT3_CHECKPOINT_DIR="${MT3_CHECKPOINT_DIR:-$HOME/.cache/mt3-transcriber/checkpoints}"
else
  VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv-mt3gui}"
fi
JAX_CUDA="${JAX_CUDA:-cuda12}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

log() { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; exit 1; }

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "未找到 $PYTHON_BIN"
command -v git >/dev/null 2>&1 || die "未找到 git（pip 需要从 GitHub 拉取依赖）"
if ! ls "$(git --exec-path)"/git-remote-https >/dev/null 2>&1; then
  die "当前 git 缺少 HTTPS 支持（找不到 git-remote-https），无法拉取依赖。\
请安装完整版 git（如 sudo apt-get install git）并确保它在 PATH 最前面。"
fi

"$PYTHON_BIN" - <<'PY' || die "Python 版本过低。flax 主分支要求 Python >= 3.12，请安装 3.12（推荐）或 3.13"
import sys
sys.exit(0 if sys.version_info >= (3, 12) else 1)
PY

"$PYTHON_BIN" - <<'PY' || warn "Python >= 3.13 可能缺少部分依赖的预编译包，推荐 3.12"
import sys
sys.exit(0 if sys.version_info < (3, 13) else 1)
PY

# ---------------------------------------------------------------- 系统依赖检查
missing_pkgs=()
"$PYTHON_BIN" -c 'import tkinter' >/dev/null 2>&1 || missing_pkgs+=(python3-tk)
"$PYTHON_BIN" -c 'import ensurepip' >/dev/null 2>&1 || missing_pkgs+=(python3-venv)
if command -v ldconfig >/dev/null 2>&1 && \
   ! ldconfig -p 2>/dev/null | grep -q libsndfile; then
  missing_pkgs+=(libsndfile1)
fi
if [ "${#missing_pkgs[@]}" -gt 0 ]; then
  warn "缺少系统包: ${missing_pkgs[*]}"
  if command -v apt-get >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
    apt-get update -qq && apt-get install -y "${missing_pkgs[@]}"
  else
    die "请先安装: sudo apt-get install -y ${missing_pkgs[*]}"
  fi
fi

# ------------------------------------------------------------------ GPU 检查
if command -v nvidia-smi >/dev/null 2>&1; then
  log "检测到 GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
else
  warn "未检测到 nvidia-smi，可能缺少 NVIDIA 驱动。"
  warn "仍可安装，但推理会退回 CPU，速度非常慢。"
  reply=n
  read -r -p "继续安装？[y/N] " reply || true
  [[ "$reply" =~ ^[Yy]$ ]] || exit 1
fi

# ------------------------------------------------------------------ 虚拟环境
if [ ! -d "$VENV_DIR" ]; then
  log "创建虚拟环境: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel

log "安装 JAX（$JAX_CUDA）…"
pip install "jax[$JAX_CUDA]"

log "安装 MT3 及依赖（tensorflow/flax/t5x/seqio/note-seq，约 5~15 分钟）…"
pip install -e "$REPO_DIR"

if [ "${SKIP_CHECKPOINTS:-0}" != "1" ]; then
  log "下载预训练权重（约 340MB）…"
  python "$SCRIPT_DIR/download_checkpoints.py"
else
  warn "已跳过权重下载（SKIP_CHECKPOINTS=1）"
fi

log "环境自检…"
python - <<'PY'
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
log "  端到端自检:     $VENV_DIR/bin/python $SCRIPT_DIR/selftest.py"
