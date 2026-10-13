#!/usr/bin/env python3
"""检查 shell 脚本里「`$VAR` 紧跟多字节字符」的写法。

macOS 自带的是 bash 3.2，它把多字节字符的**字节**当成变量名的一部分，于是

    log "安装（$ver）…"      # 会被解析成变量名 `ver）` → unbound variable

在 Linux（bash 5）上完全没问题，只在 macOS 上炸——所以单独做一个静态检查。
写成 ``"${ver}"`` 即可。

用法: python packaging/check-shell-utf8.py [文件…]
缺省检查 gui/*.sh、packaging/*.sh 与 .github/workflows/*.yml 里的 run 块。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# `$name` 后面直接跟一个非 ASCII 字节（UTF-8 多字节字符的首字节）。
BAD = re.compile(rb'\$[A-Za-z_][A-Za-z0-9_]*(?=[\x80-\xff])')


def targets(argv: list[str]) -> list[Path]:
  if argv:
    return [Path(a) for a in argv]
  files = sorted(ROOT.glob('gui/*.sh')) + sorted(ROOT.glob('packaging/*.sh'))
  files += sorted(ROOT.glob('.github/workflows/*.yml'))
  return files


def main() -> int:
  problems = 0
  for path in targets(sys.argv[1:]):
    if not path.is_file():
      continue
    for lineno, line in enumerate(path.read_bytes().split(b'\n'), 1):
      for match in BAD.finditer(line):
        problems += 1
        name = match.group(0).decode('utf-8', 'replace')
        text = line.decode('utf-8', 'replace').strip()
        print(f'{path}:{lineno}: {name} 后面紧跟多字节字符，请写成 '
              f'${{{name[1:]}}}\n    {text}')
  if problems:
    print(f'\n发现 {problems} 处；macOS 的 bash 3.2 会把它当成 '
          f'变量名的一部分（unbound variable）。', file=sys.stderr)
    return 1
  print('shell 变量名检查通过。')
  return 0


if __name__ == '__main__':
  sys.exit(main())
