"""导入 GUI / 转谱所需的全部依赖，缺任何一个都以非零状态退出。

安装器（gui/install.sh）、离线运行时（packaging/build-runtime.sh）、AppImage 与
Docker 构建都用它做冒烟测试。这类问题只有真正 import 才会暴露：t5x 的 setup.py
没有声明 `tensorboard`，但 t5x/checkpoints.py 在 import 阶段就用它。

用法: python gui/import_check.py
"""

from __future__ import annotations

import sys


def main() -> int:
  # 与 gui/backend.py 的顶层 import 保持一致。
  import gin  # noqa: F401
  import jax
  import librosa  # noqa: F401
  import midi_rearrange  # noqa: F401  # 后处理模块（同时验证它依赖的 mido）
  import note_seq  # noqa: F401
  import seqio  # noqa: F401
  import t5  # noqa: F401
  import tensorflow as tf
  import t5x  # noqa: F401
  from mt3 import metrics_utils  # noqa: F401
  from mt3 import models  # noqa: F401
  from mt3 import network  # noqa: F401
  from mt3 import note_sequences  # noqa: F401
  from mt3 import preprocessors  # noqa: F401
  from mt3 import spectrograms  # noqa: F401
  from mt3 import vocabularies  # noqa: F401

  print('  jax        :', jax.__version__, jax.devices())
  print('  tensorflow :', tf.__version__)
  print('  gin / librosa / mido / note_seq / seqio / t5 / t5x: OK')
  print('  后处理模块 : OK')
  print('  mt3 模块   : OK')
  print('OK: 依赖完整')
  return 0


if __name__ == '__main__':
  sys.exit(main())
