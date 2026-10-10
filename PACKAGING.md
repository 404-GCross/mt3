# MT3 Transcriber 分发与测试

## 本地运行

脚本会使用独立的 `uv` 自动安装 Python 3.12 和依赖，**不依赖系统 Python 版本**，也不需要 root。

```bash
./gui/install.sh          # 首次：装 Python 3.12 + 依赖（JAX/TensorFlow/...）
./gui/run.sh              # 启动图形界面
```

安装脚本默认安装 CUDA 12 的 JAX；纯 CPU 或 CUDA 13：

```bash
JAX_CUDA=cpu ./gui/install.sh
JAX_CUDA=cuda13 ./gui/install.sh
```

模型权重默认**不随包分发**，首次打开界面或转谱时会提示下载（约 340MB）。
如需在安装时一并下载：

```bash
MT3_DOWNLOAD_CHECKPOINTS=1 ./gui/install.sh
```

GUI 中可选择：自动、NVIDIA GPU、CPU。命令行也支持：

```bash
./gui/run.sh --cli song.wav --model ismir2021 --device cuda -o song.mid
./gui/run.sh --cli song.wav --model mt3 --device cpu -o song.mid
./gui/run.sh --batch-input ./audio --batch-output ./midi --model mt3
```

> 环境变量：`MT3_HOME` 控制 uv/Python/venv 的安装位置（默认
> `~/.local/share/mt3-transcriber`）；`MT3_CHECKPOINT_DIR` 控制权重目录
> （默认 `~/.cache/mt3-transcriber/checkpoints`）。

## 自检

```bash
.venv-mt3gui/bin/python gui/selftest.py --model ismir2021
```

它会生成合成音频并执行完整的音频→MT3→MIDI 流程。自检不代表实际音频准确率。

## 构建 deb / rpm

包只包含 GUI、MT3 源码和启动器，不内置 Python、wheel 或模型权重；首次运行会
用 `uv` 在用户目录建立私有 3.12 环境，并提示下载模型。

```bash
VERSION=0.1.0 ./packaging/build-deb.sh
VERSION=0.1.0 ./packaging/build-rpm.sh   # 在 Fedora/RHEL 环境
```

安装后：

```bash
sudo apt install ./dist/mt3-transcriber_0.1.0_amd64.deb
# 或
sudo dnf install ./dist/mt3-transcriber-0.1.0-1.x86_64.rpm
mt3-transcriber
```

依赖仅为 `git`、`curl`、`libsndfile`、`tcl`、`tk`（Python 由 uv 自带）。

## 中文字体 / Tk 显示空白

uv 自带的 Tk 是**不带 Xft/fontconfig** 编译的，因此看不到系统字体，中英文以外
的文字（如中文）会显示为空白——只剩 ASCII 和 `/`、`:`。这不是缺字体，装
`google-noto-sans-cjk-fonts` 也没用。

安装脚本会运行 `gui/fix_tk_system.py`，把 uv 自带的 `libtcl/libtk` 换（符号
链接）成系统带 Xft 的版本，并放宽 `init.tcl` 的 Tcl 版本校验。需要系统里有
Tcl/Tk 8.6：

```bash
sudo dnf install tcl tk            # Fedora
sudo apt install libtcl8.6 libtk8.6  # Debian/Ubuntu
sudo pacman -S tcl tk              # Arch
```

手动修复（安装后随时可跑）：

```bash
~/.local/share/mt3-transcriber/venv/bin/python \
  /usr/share/mt3-transcriber/gui/fix_tk_system.py \
  --python ~/.local/share/mt3-transcriber/venv/bin/python
```

还原原状加 `--reverse`。参见 python-build-standalone#740。

## 构建 AppImage（独立、双击即用）

AppImage 内置 Python 3.12 + 程序 + CPU 依赖，**不需要系统 Python、看不到终端**；
模型权重首次启动时下载，因此可以控制在 2GiB 以内，作为 GitHub Release 的单个附件。

```bash
VERSION=0.1.0 ./packaging/build-appimage.sh
# 产物: dist/mt3-transcriber-0.1.0-x86_64.AppImage
```

- 建议在 `ubuntu:22.04` 等较老的发行版里构建，以获得更宽的 glibc 兼容性；
- 运行需要 FUSE（Fedora 默认有）；没有时可执行
  `./mt3-transcriber-*.AppImage --appimage-extract-and-run`；
- GPU：包内是 CPU 依赖，使用 GPU 的机器会在首次运行/转谱时按需安装 CUDA 版 JAX
  （写入用户目录，不改动只读的 AppImage）。

## Docker

```bash
docker build -t mt3-transcriber .
docker run --rm --gpus all -it --env DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$PWD:/opt/mt3" mt3-transcriber
```

Docker 镜像适合 GPU 主机和 CLI；桌面 GUI 还需要正确配置 X11/Wayland 转发。

## GitHub Actions

推送版本标签即可构建并发布：

```bash
git tag v0.1.0
git push origin v0.1.0
```

`.github/workflows/packages.yml` 会执行静态检查，构建 deb/rpm/AppImage，构建 Docker
镜像作为验证，并在 tag 触发时上传 GitHub Release 和 SHA256SUMS。

官方 GitHub Runner 没有 NVIDIA GPU，因此 GPU 推理仍需在目标 GPU 机器上执行
`selftest.py`。
