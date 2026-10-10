#!/usr/bin/env bash
# 启动 MT3 扒谱工具。
#
# 用法:
#   ./gui/run.sh                                  # 图形界面（默认）
#   ./gui/run.sh --cli 音频.mp3 -o 输出.mid       # 命令行模式
#   ./gui/run.sh --cli 音频.mp3 --model ismir2021 # 钢琴模型

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv-mt3gui}"

# Installed deb/rpm packages keep the private environment in the user's home.
if [ ! -x "$VENV_DIR/bin/python" ] && [ "$SCRIPT_DIR" = "/usr/share/mt3-transcriber/gui" ]; then
  VENV_DIR="${MT3_VENV_DIR:-$HOME/.local/share/mt3-transcriber/venv}"
  export MT3_CHECKPOINT_DIR="${MT3_CHECKPOINT_DIR:-$HOME/.cache/mt3-transcriber/checkpoints}"
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  if [ -x "$SCRIPT_DIR/install.sh" ]; then
    echo "首次运行，正在安装 MT3 环境…" >&2
    VENV_DIR="$VENV_DIR" "$SCRIPT_DIR/install.sh"
  else
    echo "未找到虚拟环境: $VENV_DIR" >&2
    exit 1
  fi
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "MT3 环境安装失败: $VENV_DIR" >&2
  exit 1
fi

# 不预先占满显存，避免影响桌面/其他程序。
export XLA_PYTHON_CLIENT_PREALLOCATE="${XLA_PYTHON_CLIENT_PREALLOCATE:-false}"

# 优先使用随包安装的、带 Xft 的 Tcl/Tk 脚本目录（deb/rpm 安装到
# /usr/share/mt3-transcriber/tcltk）；否则退回 uv 自带的脚本目录，配合
# gui/fix_tk_system.py 换成的系统库使用。
MT3_TCLTK_DIR="${MT3_TCLTK_DIR:-/usr/share/mt3-transcriber/tcltk}"
if [ -z "${TCL_LIBRARY:-}" ] && [ -d "$MT3_TCLTK_DIR/lib/tcl9.0" ]; then
  export TCL_LIBRARY="$MT3_TCLTK_DIR/lib/tcl9.0"
fi
if [ -z "${TK_LIBRARY:-}" ] && [ -d "$MT3_TCLTK_DIR/lib/tk9.0" ]; then
  export TK_LIBRARY="$MT3_TCLTK_DIR/lib/tk9.0"
fi

if [ -z "${TCL_LIBRARY:-}" ] || [ -z "${TK_LIBRARY:-}" ]; then
  read -r TCL_DIR TK_DIR < <("$VENV_DIR/bin/python" - <<'PY'
import os
import sys
tcl = tk = ''
for name in ('tcl8.6', 'tcl8.7', 'tcl9.0'):
  candidate = os.path.join(sys.base_prefix, 'lib', name)
  if os.path.isdir(candidate):
    tcl = candidate
    break
for name in ('tk8.6', 'tk8.7', 'tk9.0'):
  candidate = os.path.join(sys.base_prefix, 'lib', name)
  if os.path.isdir(candidate):
    tk = candidate
    break
print(tcl, tk)
PY
)
  [ -n "${TCL_LIBRARY:-}" ] || { [ -n "${TCL_DIR:-}" ] && export TCL_LIBRARY="$TCL_DIR"; }
  [ -n "${TK_LIBRARY:-}" ] || { [ -n "${TK_DIR:-}" ] && export TK_LIBRARY="$TK_DIR"; }
fi

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/app.py" "$@"
