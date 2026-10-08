# MT3 Transcriber 分发与测试

## 本地运行

在 Linux + NVIDIA GPU 机器上：

```bash
./gui/install.sh
./gui/run.sh
```

安装脚本默认安装 CUDA 12 JAX。若目标机使用 CUDA 13：

```bash
JAX_CUDA=cuda13 ./gui/install.sh
```

GUI 中可选择：自动、NVIDIA GPU、CPU。命令行也支持：

```bash
./gui/run.sh --cli song.wav --model ismir2021 --device cuda -o song.mid
./gui/run.sh --cli song.wav --model ismir2021 --device cpu -o song.mid
./gui/run.sh --batch-input ./audio --batch-output ./midi --model ismir2021
```

## 自检

```bash
.venv-mt3gui/bin/python gui/selftest.py --model ismir2021
```

它会生成合成音频并执行完整的音频→MT3→MIDI流程。自检不代表实际音频准确率。

## 构建 deb/rpm

包只包含 GUI、MT3 源码和启动器，不内置 CUDA、Python wheel 或模型权重；首次运行会在用户目录建立私有环境并下载模型。

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

模型默认放在 `~/.cache/mt3-transcriber/checkpoints`，虚拟环境放在
`~/.local/share/mt3-transcriber/venv`。

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

`.github/workflows/packages.yml` 会执行静态检查、构建 deb/rpm、构建 Docker 镜像，且在 tag 触发时上传 GitHub Release 和 SHA256SUMS。

官方 GitHub Runner 没有 NVIDIA GPU，因此 GPU 推理仍需在目标 GPU 机器上执行 `selftest.py`。
