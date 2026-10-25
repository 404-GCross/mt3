#!/usr/bin/env bash
# 启动 MT3 扒谱工具。
#
# 用法:
#   ./gui/run.sh                                  # 图形界面（默认）
#   ./gui/run.sh --cli 音频.mp3 -o 输出.mid       # 命令行模式
#   ./gui/run.sh --cli 音频.mp3 --model ismir2021 # 钢琴模型
#
# deb/rpm 包内置了离线运行时（runtime.tar.zst）与模型权重：首次启动解压到
# ~/.local/share/mt3-transcriber，之后完全离线。源码目录运行时若没有内置运行时，
# 则退回 gui/install.sh 联网安装。
#
# macOS 的 MT3 Transcriber.app 把运行时与权重直接放在包内
# （Contents/Resources/），不需要解压，也不需要改动包内文件：包内 venv 用的是
# 相对路径，因此随便拖到哪、甚至从只读的 .dmg 里直接启动都能用。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv-mt3gui}"

IS_DARWIN=0
[ "$(uname -s)" = "Darwin" ] && IS_DARWIN=1

INSTALLED=0
[ "$SCRIPT_DIR" = "/usr/share/mt3-transcriber/gui" ] && INSTALLED=1

# macOS 应用包：gui/ 位于 MT3 Transcriber.app/Contents/Resources/gui。
APP_RES=""
case "$SCRIPT_DIR" in
  */Contents/Resources/gui) APP_RES="${SCRIPT_DIR%/gui}" ;;
esac
[ -n "$APP_RES" ] && INSTALLED=1

# 可写目录：MT3_USER_HOME 是 deb/rpm 展开运行时的位置；MT3_USER_CACHE 是权重下载
# 的兜底位置（包内权重缺失或不可写时用）。macOS 用系统惯例的路径。
if [ "$IS_DARWIN" = 1 ]; then
  MT3_USER_HOME="${MT3_USER_HOME:-$HOME/Library/Application Support/mt3-transcriber}"
  MT3_USER_CACHE="${MT3_USER_CACHE:-$HOME/Library/Caches/mt3-transcriber}"
else
  MT3_USER_HOME="${MT3_USER_HOME:-$HOME/.local/share/mt3-transcriber}"
  MT3_USER_CACHE="${MT3_USER_CACHE:-$HOME/.cache/mt3-transcriber}"
fi

RUNTIME_TAR="${MT3_RUNTIME_TARBALL:-}"
if [ -n "$APP_RES" ]; then
  # .app 自带运行时：原地使用，不解压。
  MT3_HOME="${MT3_HOME:-$APP_RES/runtime}"
  VENV_DIR="${MT3_VENV_DIR:-$MT3_HOME/venv}"
elif [ "$INSTALLED" = 1 ] || [ -n "$RUNTIME_TAR" ]; then
  MT3_HOME="${MT3_HOME:-$MT3_USER_HOME}"
  # deb/rpm 把运行时放在 /usr/share/mt3-transcriber/runtime.tar.zst（gui/ 的上一级）。
  if [ -z "$RUNTIME_TAR" ]; then
    RUNTIME_TAR="$REPO_DIR/runtime.tar.zst"
  fi
  VENV_DIR="${MT3_VENV_DIR:-$MT3_HOME/venv}"
  # 内置运行时固定展开到 $MT3_HOME/venv，指向别处的 MT3_VENV_DIR 在这里没有意义。
  if [ -f "$RUNTIME_TAR" ] && [ "$VENV_DIR" != "$MT3_HOME/venv" ]; then
    echo "提示: 内置运行时固定展开到 $MT3_HOME/venv（忽略 MT3_VENV_DIR=${VENV_DIR}）" >&2
    VENV_DIR="$MT3_HOME/venv"
  fi
fi

# 包版本（deb/rpm 写入的 /usr/share/mt3-transcriber/VERSION）；内置运行时按此
# 打戳，升级后会自动重新展开。
PKG_VERSION=""
if [ -f "$REPO_DIR/VERSION" ]; then
  PKG_VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || true)"
fi

runtime_is_current() {
  [ -x "$VENV_DIR/bin/python" ] || return 1
  [ -e "$VENV_DIR/.mt3-installed" ] || return 1
  if [ -n "$PKG_VERSION" ] && [ -f "$VENV_DIR/.mt3-runtime-version" ]; then
    [ "$(cat "$VENV_DIR/.mt3-runtime-version")" = "$PKG_VERSION" ] || return 1
  fi
  return 0
}

# .app 自带的运行时：就地使用，不需要解压，也不需要改动包内文件。
# packaging/build-macos.sh 把 venv 的 pyvenv.cfg home 与 bin/python 都写成了相对
# 路径，所以 .app 被拖到哪、甚至直接从只读的 .dmg 里启动，解释器都能自己找到
# 包内的 Python。运行时不写包内任何东西（PYTHONDONTWRITEBYTECODE，见下），
# 因此也可以安全地签名/公证。
if [ -n "$APP_RES" ]; then
  # 不写 __pycache__：只读卷上写不了，签名过的包也不该被改动（字节码已在构建
  # 时预编译好，直接命中缓存）。
  export PYTHONDONTWRITEBYTECODE=1
fi

if [ "${MT3_FORCE_RUNTIME:-0}" = 1 ] || ! runtime_is_current; then
  if [ -f "$RUNTIME_TAR" ]; then
    if ! tar --zstd -tf "$RUNTIME_TAR" >/dev/null 2>&1; then
      echo "无法读取内置运行时（需要 zstd）：$RUNTIME_TAR" >&2
      echo "  Fedora: sudo dnf install zstd    Debian/Ubuntu: sudo apt install zstd" >&2
      exit 1
    fi
    echo "正在展开内置运行时（离线，约 1GB，需要 1~2 分钟）…" >&2
    mkdir -p "$MT3_HOME"
    avail_kb="$(df -Pk "$MT3_HOME" 2>/dev/null | awk 'NR==2{print $4}')"
    if [ -n "$avail_kb" ] && [ "$avail_kb" -lt 2000000 ]; then
      echo "磁盘空间不足：$MT3_HOME 需要约 1.5GB，当前只剩 $((avail_kb / 1024))MB。" >&2
      exit 1
    fi
    # 旧的、不完整的 venv 直接重建，避免残留文件干扰（只删名为 venv 的目录）。
    case "${VENV_DIR##*/}" in
      venv)
        rm -rf "$VENV_DIR"
        ;;
      *)
        echo "拒绝删除非 venv 目录: ${VENV_DIR}（请检查 MT3_VENV_DIR）" >&2
        exit 1
        ;;
    esac
    tar --zstd -xf "$RUNTIME_TAR" -C "$MT3_HOME"
    if [ -f "$SCRIPT_DIR/runtime_repair.sh" ]; then
      MT3_HOME="$MT3_HOME" bash "$SCRIPT_DIR/runtime_repair.sh" "$MT3_HOME" >&2 || true
    fi
    if "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1; then
      # 记为「本包版本已展开」：同一版本不再重复解压（打包时版本与运行时一致）。
      [ -n "$PKG_VERSION" ] && printf '%s\n' "$PKG_VERSION" \
        > "$VENV_DIR/.mt3-runtime-version"
      touch "$VENV_DIR/.mt3-installed"
      echo "内置运行时已就绪: $VENV_DIR" >&2
    else
      echo "内置运行时展开后不可用，改为联网安装…" >&2
      RUNTIME_TAR=""
    fi
  fi
fi

# 没有可用运行时（源码运行 / 包不完整）：联网安装或修复。
if [ ! -x "$VENV_DIR/bin/python" ] || [ ! -e "$VENV_DIR/.mt3-installed" ]; then
  if [ -n "$APP_RES" ]; then
    # .app 里一切都是自带的，走到这里说明包本身坏了：重新装一份，别在包内搞半成品。
    echo "应用包内的运行时不可用: $VENV_DIR" >&2
    echo "请重新下载安装 MT3 Transcriber.app。" >&2
    exit 1
  fi
  if [ -f "$SCRIPT_DIR/install.sh" ]; then
    echo "正在安装 MT3 环境（需要联网；国内可用 MT3_GH_PROXY / MT3_PIP_INDEX 加速）…" >&2
    VENV_DIR="$VENV_DIR" MT3_HOME="${MT3_HOME:-}" bash "$SCRIPT_DIR/install.sh"
  else
    echo "未找到可用的 MT3 环境: $VENV_DIR" >&2
    exit 1
  fi
fi
[ -x "$VENV_DIR/bin/python" ] || { echo "MT3 环境不可用: $VENV_DIR" >&2; exit 1; }

# 内置模型权重（只读）优先；用户显式指定 MT3_CHECKPOINT_DIR 时尊重用户选择。
if [ "$INSTALLED" = 1 ] && [ -z "${MT3_CHECKPOINT_DIR:-}" ]; then
  if [ -n "$APP_RES" ]; then
    sys_ckpt="$APP_RES/checkpoints"
  else
    sys_ckpt="/usr/share/mt3-transcriber/checkpoints"
  fi
  # 只有在两个模型都随包内置时才用它（只读）；缺任何一个都退回可写的用户目录。
  if [ -f "$sys_ckpt/mt3/checkpoint" ] && [ -f "$sys_ckpt/ismir2021/checkpoint" ]; then
    export MT3_CHECKPOINT_DIR="$sys_ckpt"
  else
    export MT3_CHECKPOINT_DIR="$MT3_USER_CACHE/checkpoints"
  fi
elif [ -n "${MT3_CHECKPOINT_DIR:-}" ]; then
  export MT3_CHECKPOINT_DIR
fi

# 不预先占满显存，避免影响桌面/其他程序。
export XLA_PYTHON_CLIENT_PREALLOCATE="${XLA_PYTHON_CLIENT_PREALLOCATE:-false}"

# 优先使用随包安装的、带 Xft 的 Tcl/Tk 脚本目录（deb/rpm 安装到
# /usr/share/mt3-transcriber/tcltk）；否则退回 uv 自带的脚本目录，配合
# gui/fix_tk_system.py 换成的系统库使用。macOS 上的 Tk 是原生 Aqua + CoreText，
# 中文直接可用，不需要这套 Xft 修补。
MT3_TCLTK_DIR="${MT3_TCLTK_DIR:-/usr/share/mt3-transcriber/tcltk}"
if [ "$IS_DARWIN" = 0 ]; then
  if [ -z "${TCL_LIBRARY:-}" ] && [ -d "$MT3_TCLTK_DIR/lib/tcl9.0" ]; then
    export TCL_LIBRARY="$MT3_TCLTK_DIR/lib/tcl9.0"
  fi
  if [ -z "${TK_LIBRARY:-}" ] && [ -d "$MT3_TCLTK_DIR/lib/tk9.0" ]; then
    export TK_LIBRARY="$MT3_TCLTK_DIR/lib/tk9.0"
  fi

  # 随包自带的 Xft Tcl/Tk 存在时，确保 venv 里的库已经指向它。install.sh 只在
  # 首次安装时运行，升级 RPM/AppImage 或重新展开运行时后这里每次启动幂等地校正，
  # 免得中文界面又变空白。
  if [ -d "$MT3_TCLTK_DIR/lib" ] && [ -f "$SCRIPT_DIR/fix_tk_system.py" ]; then
    "$VENV_DIR/bin/python" "$SCRIPT_DIR/fix_tk_system.py" \
      --python "$VENV_DIR/bin/python" --source "$MT3_TCLTK_DIR" --quiet || true
  fi
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
