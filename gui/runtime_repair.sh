#!/usr/bin/env bash
# 把 runtime.tar.zst 解压出来的 Python 运行时校正到当前路径。
#
# uv 创建的 venv 里记录的是构建时的绝对路径（pyvenv.cfg 的 home、bin/python 符号
# 链接、bin/ 下脚本的 shebang）。换到用户目录后这些都会失效，这里统一改成当前
# 路径。解压一次即可，run.sh 首次启动时自动调用。
#
# 用法: gui/runtime_repair.sh <MT3_HOME>

set -euo pipefail

home="${1:-}"
if [ -z "$home" ]; then
  echo "用法: runtime_repair.sh <MT3_HOME>" >&2
  exit 1
fi
venv="$home/venv"
[ -d "$venv" ] || { echo "找不到虚拟环境: $venv" >&2; exit 1; }

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
pybin="$(readlink -f "$pybin")"
py_dir="$(dirname "$pybin")"

cat > "$venv/pyvenv.cfg" <<EOF
home = $py_dir
implementation = CPython
version_info = 3.12
include-system-site-packages = false
relocatable = true
EOF

ln -sfn "$pybin" "$venv/bin/python"
ln -sfn python "$venv/bin/python3"
ln -sfn python "$venv/bin/python3.12"

# bin/ 下脚本的 shebang 指向构建时的 venv 路径，改成当前路径。只处理文本脚本，
# 免得读二进制（会有 "ignored null byte" 噪音）。
for f in "$venv/bin"/*; do
  [ -f "$f" ] || continue
  if [ -L "$f" ]; then continue; fi
  if LC_ALL=C head -c 128 "$f" 2>/dev/null | LC_ALL=C grep -aq '^#!.*python'; then
    sed -i "1s|^#!.*|#!$venv/bin/python|" "$f"
  fi
done
if [ -f "$venv/bin/activate" ]; then
  sed -i "s|^VIRTUAL_ENV=.*|VIRTUAL_ENV=\"$venv\"|" "$venv/bin/activate" || true
fi

"$venv/bin/python" -c 'import sys' >/dev/null 2>&1 \
  || { echo "运行时校正后仍无法启动: $venv/bin/python" >&2; exit 1; }
