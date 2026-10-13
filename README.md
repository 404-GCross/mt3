# MT3: Multi-Task Multitrack Music Transcription

MT3 is a multi-instrument automatic music transcription model that uses the [T5X framework](https://github.com/google-research/t5x).

This is not an officially supported Google product.

## 桌面版（本仓库的 GUI / 安装包）

本仓库在 MT3 之上做了一层 Tkinter 图形界面与可分发的安装包：双击运行、不需要
终端、不需要系统 Python（自带 Python 3.12 运行时），支持钢琴模型（`ismir2021`）
与多乐器模型（`mt3`）。Linux 上可选 NVIDIA GPU 加速；macOS 为 Apple Silicon
（arm64）纯 CPU 版。

转谱结果还可以做一次可选的**后处理（MIDI 整理）**：删弱音、删极短音、合并琶音
与重复起音，另存为 `xxx.rearranged.mid`（默认关闭、不覆盖原始结果）。

安装包（deb / rpm / AppImage / Docker / macOS .dmg）、镜像加速与构建说明见
[PACKAGING.md](PACKAGING.md)。

## Transcribe your own audio

Use our [colab notebook](https://colab.research.google.com/github/magenta/mt3/blob/main/mt3/colab/music_transcription_with_transformers.ipynb) to
transcribe audio files of your choosing.  You can use a pretrained checkpoint from
either a) the piano transcription model described in [our ISMIR 2021 paper](https://archives.ismir.net/ismir2021/paper/000030.pdf)
or b) the multi-instrument transcription model described in
[our ICLR 2022 paper](https://openreview.net/pdf?id=iMSjopcOn0p).


## Train a model

For now, we do not (easily) support training.  If you like, you can try to
follow the [T5X training instructions](https://github.com/google-research/t5x#training)
and use one of the tasks defined in [tasks.py](mt3/tasks.py).
