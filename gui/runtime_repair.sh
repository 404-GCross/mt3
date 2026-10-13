#!/usr/bin/env bash
# 把运行时校正到当前路径。
#
# 运行时的两种来源：
#   * Linux deb/rpm/AppImage：首次启动把内置 runtime.tar.zst 解压到 $MT3_HOME；
#   * macOS .app：运行时就在包内（Contents/Resources/runtime），用户可能随意拖动。
#
# uv 创建的 venv 里记录的是构建时的绝对路径（pyvenv.cfg 的 home、bin/python 符号
# 链接、bin/ 下脚本的 shebang）。换到别的目录后这些都会失效，这里统一改成当前
# 路径。脚本在 Linux 与 macOS 上都能跑（GNU/BSD sed 差异见下面）。
#
# 用法: gui/runtime_repair.sh <MT3_HOME>

set -euo pipefail

home="${1:-}"
if [ -z "$home" ]; then
  echo "用法: runtime_repair.sh <MT3_HOME>" >&2
  exit 1
fi
home="$(cd "$home" && pwd -P)"
venv="$home/venv"
[ -d "$venv" ] || { echo "找不到虚拟环境: $venv" >&2; exit 1; }

# ---------------------------------------------------------------- 平台兼容
# GNU sed 用 `sed -i EXPRE file`，BSD/macOS sed 需要 `sed -i '' EXPRE file`。
sed_inplace() {  # sed_inplace FILE EXPR
  if sed --version >/dev/null 2>&1; then
    sed -i "$2" "$1"
  else
    sed -i '' "$2" "$1"
  fi
}

# macOS 的 readlink 没有 -f；用 POSIX 方式解析符号链接（逐级 readlink + pwd -P）。
resolve_path() {  # resolve_path PATH
  local target="$1" link
  while [ -L "$target" ]; do
    link="$(readlink "$target")"
    case "$link" in
      /*) target="$link" ;;
      *) target="$(dirname "$target")/$link" ;;
    esac
  done
  if [ ! -d "$(dirname "$target")" ]; then
    printf '%s\n' "$target"
    return 0
  fi
  printf '%s/%s\n' "$(cd "$(dirname "$target")" && pwd -P)" "$(basename "$target")"
}

# ---------------------------------------------------------------- 解释器
# 构建脚本记下的解释器相对位置（python/cpython-3.12.x-.../bin/python3.12）。
pybin=""
if [ -f "$venv/.mt3-python-relpath" ]; then
  rel="$(cat "$venv/.mt3-python-relpath")"
  [ -x "$home/$rel" ] && pybin="$home/$rel"
fi
if [ -z "$pybin" ]; then
  pybin="$(ls -d "$home"/python/*/bin/python3.12 2>/dev/null | head -1 || true)"
fi
if [ -z "$pybin" ] || [ ! -x "$pybin" ]; then
  echo "找不到运行时解释器（$home/python 不完整）" >&2
  exit 1
fi
pybin="$(resolve_path "$pybin")"
py_dir="$(dirname "$pybin")"

# ---------------------------------------------------------------- venv 校正
cat > "$venv/pyvenv.cfg" <<EOF
home = $py_dir
implementation = CPython
version_info = 3.12
include-system-site-packages = false
relocatable = true
EOF

# 先删后建：有些系统上 `ln -sfn` 会顺着已有符号链接把文件建到链接目标里。
rm -f "$venv/bin/python" "$venv/bin/python3" "$venv/bin/python3.12"
ln -s "$pybin" "$venv/bin/python"
ln -s python "$venv/bin/python3"
ln -s python "$venv/bin/python3.12"

# bin/ 下脚本的 shebang 指向构建时的 venv 路径，改成当前路径。只处理文本脚本，
# 免得读二进制（会有 "ignored null byte" 噪音）。
for f in "$venv/bin"/*; do
  [ -f "$f" ] || continue
  if [ -L "$f" ]; then continue; fi
  if LC_ALL=C head -c 128 "$f" 2>/dev/null | LC_ALL=C grep -aq '^#!.*python'; then
    sed_inplace "$f" "1s|^#!.*|#!$venv/bin/python|"
  fi
done
if [ -f "$venv/bin/activate" ]; then
  sed_inplace "$venv/bin/activate" "s|^VIRTUAL_ENV=.*|VIRTUAL_ENV=\"$venv\"|" || true
fi

"$venv/bin/python" -c 'import sys' >/dev/null 2>&1 \
  || { echo "运行时校正后仍无法启动: $venv/bin/python" >&2; exit 1; }
