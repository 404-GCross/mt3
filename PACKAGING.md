# MT3 Transcriber 分发与测试

## 一键安装（Linux x86_64 / macOS Apple Silicon）

```bash
curl -fsSL https://gh-proxy.com/https://github.com/404-GCross/mt3/releases/latest/download/install.sh | bash
```

脚本自己判断系统：Debian/Ubuntu 走 `.deb`、Fedora/RHEL 走 `.rpm`、其他发行版退到免 root
的 AppImage；macOS 挂载 dmg 复制到 `/Applications`（并去掉隔离标记，所以不会有
Gatekeeper 弹窗）。下载和 SHA256 校验都在普通用户下完成，只有最后一条安装命令用 sudo。

| 参数 | 作用 |
|---|---|
| `--version 0.3.1` | 固定版本（默认取最新 release） |
| `--file ./xxx.deb` | 离线安装本地包（`.deb` / `.rpm` / `.AppImage` / `.dmg`） |
| `--appimage` | Linux 上强制用免 root 的 AppImage |
| `--cuda cuda13` | 装完顺手换 CUDA 版 JAX（默认只打印命令，不擅自下载 1GB） |
| `--mirror URL` | 下载前缀，等价于环境变量 `MT3_MIRROR` |
| `--dry-run` | 只打印解析到的版本、URL 和将执行的命令 |
| `--uninstall` | 卸载（保留用户数据） |
| `--yes` / `-y` | 不询问（非交互环境本来就不问） |

- 想先看脚本再跑：`curl -fsSLO <同一个 URL> && less install.sh && bash install.sh`
- 校验和来自同一 release 的 `SHA256SUMS`，对不上直接中止。
- 需要 root 的只有 `apt-get install ./x.deb` / `dnf install ./x.rpm`；AppImage 与 macOS
  分支不需要 root（macOS 写不进 `/Applications` 时退到 `~/Applications`）。
- 卸载不删 `~/.cache/mt3-transcriber`（任务与权重）和 `~/.config/mt3-transcriber`（WebUI 令牌）。

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

在 macOS 上（Linux/macOS 共用这一套脚本）会自动改用 CPU 版 JAX——macOS 没有
NVIDIA GPU/CUDA，也不使用实验性的 `jax-metal`。macOS 的默认路径见
「构建 macOS 应用包」一节。

模型权重默认**不随包分发**，首次打开界面或转谱时会提示下载（约 340MB；国内可走镜像，
见「国内镜像加速」）。
如需在安装时一并下载：

```bash
MT3_DOWNLOAD_CHECKPOINTS=1 ./gui/install.sh
```

GUI 中可选择：自动、NVIDIA GPU、CPU（macOS 上只列出「自动（CPU）」与「CPU」）。
命令行也支持：

```bash
./gui/run.sh --cli song.wav --model ismir2021 --device cuda -o song.mid
./gui/run.sh --cli song.wav --model mt3 --device cpu -o song.mid
./gui/run.sh --batch-input ./audio --batch-output ./midi --model mt3
```

> 环境变量：`MT3_HOME` 控制 uv/Python/venv 的安装位置（Linux 默认
> `~/.local/share/mt3-transcriber`，macOS 默认 `~/Library/Application Support/mt3-transcriber`）；
> `MT3_CHECKPOINT_DIR` 控制权重目录（默认 `~/.cache/mt3-transcriber/checkpoints`，
> macOS 为 `~/Library/Caches/mt3-transcriber/checkpoints`）。

## WebUI（浏览器 / 局域网）

除了桌面窗口，还可以把 MT3 跑成一个**局域网服务**：模型在服务器上算，Mac/手机/
另一台电脑只用浏览器。适合「Linux + NVIDIA 显卡当算力机，Mac 只负责看界面」。

```bash
./gui/run.sh --web --lan           # 源码目录
mt3-transcriber --web --lan        # deb/rpm 装好之后（也有「MT3 转谱服务（局域网）」图标）
```

启动后打印带令牌的地址：

```
  本机：    http://127.0.0.1:8765/?t=…
  局域网：  http://192.168.1.201:8765/?t=…
```

把这个链接发到手机/Mac 上打开即可（首次访问会把令牌换成 cookie，之后页面里不再带
`?t=`）。

- **令牌**：默认保存在 `~/.config/mt3-transcriber/web_token`（macOS 同路径），
  `--new-token` 重新生成，`--token XXX` 可临时指定。**没有令牌连本机也打不开**——
  否则任意网页都能用表单 POST 打 `127.0.0.1`（CSRF / DNS rebinding）。
- **只监听本机**是默认；`--lan` 等价于 `--host 0.0.0.0`。端口默认 8765，被占用会
  自动往后找，也可以用 `--port`。
- **上限**：单文件 1 GiB、时长 30 分钟、队列 8 个（`--max-bytes` / `--max-minutes` /
  `--queue`；上传时会临时占用约两倍文件大小的磁盘空间）。结果留在
  `~/.cache/mt3-transcriber/web/`（macOS 为
  `~/Library/Caches/mt3-transcriber/web/`），默认保留 24 小时（`--keep-hours`）；
  **服务重启会清空已完成的任务**，想要的文件请先下载。
- **设备**是服务端启动参数（`--device auto|cuda|cpu`），页面上只读显示；JAX 的平台
  启动后不能切换，要换设备就重启服务。
- GPU 上默认只缓存一个模型（`--keep-models 1`），避免两个模型同时占显存。
- 页面上可以对每个任务单独选模型、是否做后处理以及四个阈值；支持多文件排队、取消，
  下载原始 MIDI 与 `.rearranged.mid`。
- AppImage 没有桌面图标，直接 `./mt3-transcriber-*.AppImage --web --lan`。
- macOS：`open -a "/Applications/MT3 Transcriber.app" --args --web --lan`，
  地址与令牌会打在 `~/Library/Logs/mt3-transcriber.log` 里（`tail -f` 那个文件）。

防火墙不开的话手机连不上：

```bash
# Fedora / RHEL
sudo firewall-cmd --add-port=8765/tcp --permanent && sudo firewall-cmd --reload
# Debian / Ubuntu
sudo ufw allow 8765/tcp
```

**安全边界**：明文 HTTP，只应在家里/工作室的局域网用，**不要做端口转发或 DMZ**。
拿到令牌的人可以上传音频、占用算力、下载自己任务的 MIDI；读不到服务器上的其它文件，
也改不了设备和配置。

**自检**（不需要模型）：`python gui/webui/selftest.py`，需要
`fastapi uvicorn python-multipart httpx`。

## 国内镜像加速（可选）

安装时最慢的两段是 GitHub（uv/Python 下载、flax/t5x 等 git 依赖）和 PyPI
（jax/tensorflow wheel）。设置两个环境变量即可全部走镜像：

```bash
export MT3_GH_PROXY=https://gh-proxy.com                        # GitHub Releases/Raw/git clone
export MT3_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple   # PyPI wheel
mt3-transcriber
```

想以后都生效，写进配置文件（每行 `KEY=VALUE`，环境变量优先）：

```bash
mkdir -p ~/.config/mt3-transcriber
cat > ~/.config/mt3-transcriber/mirror.conf <<'EOF'
MT3_GH_PROXY=https://gh-proxy.com
MT3_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
EOF
```

安装重试顺序为「镜像 → 镜像+HTTP/1.1 → 直连+HTTP/1.1」，镜像抽风时会自动回退。
安装包本身也可以走代理（把原始 URL 加前缀）：

```bash
sudo dnf install -y https://gh-proxy.com/https://github.com/404-GCross/mt3/releases/download/v0.1.3/mt3-transcriber-0.1.3-1.x86_64.rpm
```

模型权重默认从 `storage.googleapis.com` 下载；官方源不可达时会自动改用本仓库
`checkpoints` Release 的镜像包（每个模型约 164 MiB，同样受 `MT3_GH_PROXY` 加速）：

```bash
export MT3_CHECKPOINT_SOURCE=mirror          # 跳过官方源，直接用镜像
export MT3_CHECKPOINT_MIRROR=<base_url>      # 自建镜像：<base_url>/mt3-checkpoints-<model>.tar.gz
python gui/download_checkpoints.py --source mirror
```

维护者可用 `Publish checkpoint mirror` workflow 重新生成镜像 Release。

## 后处理（MIDI 整理，可选）

转谱结果可以再做一遍规则化整理（不需要联网、不需要模型），清理模型输出里
常见的三类毛刺，并把被打碎的重复起音接回去：

| 步骤 | 规则 | 默认 |
|---|---|---|
| 删弱音 | 力度 `<= velocity` 的音符整对删除 | 30（0-127） |
| 删极短音 | 真实音长（按下到抬起）短于 `min_ms` 的音符删除 | 30 ms |
| 连续消除 | 同音高、与上一次起音间隔 `< repeat_ms` 且力度 `< 2×阈值` 的重复起音合并，并把前一个音的结束延长到后一个音的结束 | 120 ms |
| 琶音整合 | 同一通道内，起始时间与组内首个起音相距 `<= chord_ms` 的音符吸附到该起音上 | 60 ms |

阈值一律是**毫秒**，内部按文件自身的 tempo map 换算成秒再比较，因此不依赖 ppq
（MT3 输出是 220，人类钢琴谱常见的 384 也照样跑）。整理结果**永远另存**为
`xxx.rearranged.mid`，不覆盖原始转谱文件；**默认关闭**。打击乐通道
（channel 10）默认跳过，可用「包含鼓轨」打开。

界面里在「后处理（MIDI 整理）」区勾选启用（转谱后自动整理），或转谱完成后点
「整理当前 MIDI…」对已有结果再整理一次（不用重新转谱）。命令行：

```bash
python gui/app.py --cli 歌曲.mp3 --model ismir2021 --rearrange \
    --rearrange-velocity 30 --rearrange-min-ms 30 \
    --rearrange-chord-ms 60 --rearrange-repeat-ms 120
```

也可以单独整理一个已有 MIDI（只需要 `mido`）：

```bash
python gui/midi_rearrange.py 转谱.mid --chord-ms 80
python gui/midi_rearrange.py --selftest      # 合成 MIDI 行为自检，19 项
```

思路来自 [xRoye/MIDIRearrange](https://github.com/xRoye/MIDIRearrange)，感谢其整理
思路。该仓库（及其 fork）没有 LICENSE，因此这里是**独立实现**、没有复制其代码，
并顺手修掉了原版的几个问题：逐音轨逐通道处理（原版只读 `tracks[1]`）、保留
`program_change` 等非音符事件（原版会丢，导致所有音轨变成默认钢琴音色）、
`note_off` 使用音符自己的通道（原版硬编码通道 0）、按真实起止算音长、默认跳过
鼓轨、报告每一步删改了多少音符。

> 注意：整理是**有损**的——会删音符、会把琶音拍平。真人演奏的快速经过音、
> 装饰音可能被误判成「极短音」，所以默认关闭且永远另存。日志里会报告每一步
> 改了多少个音符，方便判断阈值是否合适。

## 自检

依赖层冒烟测试：把 GUI 需要的依赖（gin/jax/tensorflow/librosa/mido/note_seq/
seqio/t5/t5x 以及 `mt3` 各模块）全部 import 一遍，缺任何一项都非零退出。安装器、
`build-runtime.sh`、AppImage 与 Docker 构建都跑它——`t5x` 隐式依赖 `tensorboard`
这类问题只有真正 import 才会暴露：

```bash
python gui/import_check.py
```

端到端自检：

```bash
.venv-mt3gui/bin/python gui/selftest.py --model ismir2021
```

它会生成合成音频并执行完整的音频→MT3→MIDI 流程，然后用真实转谱结果跑一遍
后处理，最后跑合成 MIDI 的整理自检。自检不代表实际音频准确率。

只验后处理（不加载模型、不需要权重）：

```bash
.venv-mt3gui/bin/python gui/selftest.py --rearrange-only
```

## 构建 deb / rpm（离线包，约 1.4GB）

包内置**完整的离线运行时 + 两个模型权重**：安装后即可用，不联网、不依赖系统
Python。

| 路径 | 内容 | 体积 |
|---|---|---|
| `/usr/share/mt3-transcriber/runtime.tar.zst` | Python 3.12 + venv（CPU 版 jax/tensorflow/t5x/...）+ uv | ~1.0GB |
| `/usr/share/mt3-transcriber/checkpoints/` | mt3 / ismir2021 权重 | 328MB |
| `/usr/share/mt3-transcriber/tcltk/` | 带 Xft 的 Tcl/Tk（中文界面） | 7MB |
| `gui/`、`mt3/`、`setup.*`、`VERSION` | 启动脚本、联网安装兜底 | 10MB |

首次启动时 `run.sh` 把运行时解压到 `~/.local/share/mt3-transcriber`（1~2 分钟），
之后每次直接使用。以下情况会自动重新解压：包内 `VERSION` 与运行时版本戳不一致
（升级了包）、环境不完整、或设了 `MT3_FORCE_RUNTIME=1`。权重放在只读的
`/usr/share/mt3-transcriber/checkpoints` 下直接读取，不复制进用户目录。

构建顺序（先 Xft Tcl/Tk，再运行时，最后打包）：

```bash
./packaging/build-tcltk.sh .tcltk
VERSION=0.2.0 ./packaging/build-runtime.sh          # → dist/mt3-runtime-0.2.0.tar.zst
python gui/download_checkpoints.py --dir .checkpoints   # 可选：内置权重
TCLTK_DIR="$PWD/.tcltk" VERSION=0.2.0 \
  RUNTIME_TARBALL="$PWD/dist/mt3-runtime-0.2.0.tar.zst" \
  CHECKPOINTS_DIR="$PWD/.checkpoints" \
  ./packaging/build-deb.sh
# RPM 同理，需在 Fedora/RHEL 或装了 rpm 的环境：
TCLTK_DIR="$PWD/.tcltk" VERSION=0.2.0 \
  RUNTIME_TARBALL="$PWD/dist/mt3-runtime-0.2.0.tar.zst" \
  CHECKPOINTS_DIR="$PWD/.checkpoints" \
  ./packaging/build-rpm.sh
```

`build-runtime.sh` 需要联网装依赖，默认 `JAX_CUDA=cpu`（`cuda12/cuda13` 会让包再大
3GB+，不建议）。缺 runtime/权重时脚本只告警，退回「首次启动联网安装」的老行为。

安装：

```bash
sudo apt install ./dist/mt3-transcriber_0.2.0_amd64.deb
# 或
sudo dnf install ./dist/mt3-transcriber-0.2.0-1.x86_64.rpm
mt3-transcriber
```

运行时依赖只有 `zstd` 与 X11/字体运行库（`libsndfile`、`libX11`、`libXext`、
`libXft`、`fontconfig`、`libXrender`、`libXss`）；`git`/`curl` 只是建议项（联网
兜底安装时才需要）。Python、Tcl/Tk、模型权重都在包内。

**GPU**：包内是 CPU 版 JAX。装好 NVIDIA 驱动后跑一次

```bash
/usr/share/mt3-transcriber/gui/install_cuda_jax.sh          # CUDA 12
JAX_CUDA=cuda13 /usr/share/mt3-transcriber/gui/install_cuda_jax.sh
```

它用运行时自带的 uv 把 `jax[cuda12]` 装进用户目录的 venv（约 3GB，国内可先配好
`MT3_PIP_INDEX` 镜像），之后在界面里把「设备」选成 NVIDIA GPU 即可。

## 中文字体 / Tk 显示空白

uv / python-build-standalone 自带的 Tk 是**不带 Xft/fontconfig** 编译的，因此看不到
系统字体，中文等非 ASCII 文字会显示为空白——只剩 core X 字体（ASCII 和 `/`、`:`）。
这不是缺字体，装 `google-noto-sans-cjk-fonts` 也没用。而且自带的是 **Tcl/Tk 9.0**
（库名 `libtcl9.0.so` 与上游 Tk 9 的 `libtcl9tk9.0.so`），多数发行版只提供 8.6，
无法直接替换。

解决办法是编译一份**启用 Xft 的 Tcl/Tk 9.0** 并随包分发：

```bash
# 需要: build-essential pkg-config zlib1g-dev libx11-dev libxss-dev libxext-dev \
#       libxft-dev libfontconfig1-dev libxrender-dev
./packaging/build-tcltk.sh .tcltk
```

`gui/fix_tk_system.py` 会用它覆盖 uv 自带的 `libtcl9.0.so` / `libtcl9tk9.0.so`
（AppImage 里还会把 `libXft`/fontconfig 等依赖一并复制进包）。安装脚本自动执行：

- deb/rpm：包内附带 `/usr/share/mt3-transcriber/tcltk`，安装时复制进 venv；
- git 源码安装：若系统已有 Tcl/Tk 9.0 则换成系统的，否则中文可能空白（可先用上面
  的命令编一份，再用 `--source` 指过去）。

> **注意**：不要用发行版自带的 Tcl/Tk 去替换。uv 的 `_tkinter` 是连着它自带的
> Tcl 9.0.x 一起编的，会引用 `TclBN_mp_to_ubin` 之类的符号；Fedora 的
> `libtcl9tk9.0.so`（libtommath 走系统版）并不导出这些符号，加载时会报
> `undefined symbol: TclBN_mp_to_ubin`。只有**同源的上游 Tcl/Tk 9.0.x**（也就是
> 上面这份自编的）才能配得上。Debian/Ubuntu 连 9.0 都没有，更不行。

手动修复（安装后随时可跑）。新版 RPM/AppImage 升级后 `run.sh` 每次启动都会幂等
校正，一般无需手跑；没有随包 Tcl/Tk 时，可下载 Release 里的
`mt3-transcriber-tcltk.tar.gz` 解到 `~/.local/share/mt3-transcriber/tcltk`：

```bash
mkdir -p ~/.local/share/mt3-transcriber/tcltk
tar -xzf mt3-transcriber-tcltk.tar.gz -C ~/.local/share/mt3-transcriber/tcltk
~/.local/share/mt3-transcriber/venv/bin/python \
  /usr/share/mt3-transcriber/gui/fix_tk_system.py \
  --python ~/.local/share/mt3-transcriber/venv/bin/python \
  --source ~/.local/share/mt3-transcriber/tcltk
```

还原原状加 `--reverse`。参见 python-build-standalone#740。

## 构建 AppImage（独立、双击即用）

AppImage 内置 Python 3.12 + 程序 + CPU 依赖 + **带 Xft 的 Tcl/Tk + Noto Sans CJK
中文字体**，因此**不依赖系统的 Python、Tcl/Tk 或中文字体**，也看不到终端；模型
权重首次启动时下载，从而把体积控制在 2GiB 以内，作为 GitHub Release 的单个附件。

```bash
./packaging/build-tcltk.sh .tcltk
TCLTK_DIR="$PWD/.tcltk" VERSION=0.2.0 ./packaging/build-appimage.sh
# 产物: dist/mt3-transcriber-0.2.0-x86_64.AppImage
```

- 构建机需要 `fonts-noto-cjk` 以及 Xft/fontconfig 运行库（`libxft2 libfontconfig1
  libfreetype6 libxrender1 libx11-6`）；脚本会把它们复制进包。
- 建议在 `ubuntu:22.04` 等较老的发行版里构建，以获得更宽的 glibc 兼容性；
- 运行需要 FUSE（Fedora 默认有）；没有时可执行
  `./mt3-transcriber-*.AppImage --appimage-extract-and-run`；
- GPU：包内是 CPU 依赖，且 AppImage 内的 venv 在只读挂载点上、无法原地换装 CUDA
  版 JAX。需要 GPU 请用 deb/rpm（`gui/install_cuda_jax.sh`）或 Docker；也可以先
  `./mt3-transcriber-*.AppImage --appimage-extract` 到可写目录再自行安装。
- 自检中文渲染：`./mt3-transcriber-*.AppImage --check-fonts`（CI 会用 Xvfb 跑它）。

## 构建 macOS 应用包（Apple Silicon）

`MT3 Transcriber.app` 是自包含应用：包内带独立 Python 3.12 + 依赖 + 两个模型
权重，双击即用——不需要系统 Python、不需要终端、不需要联网。

| 路径 | 内容 |
|---|---|
| `Contents/Resources/runtime/` | Python 3.12 + venv（CPU 版 jax/tensorflow/t5x/…，相对路径、可随包整体搬动）+ uv |
| `Contents/Resources/checkpoints/` | mt3 / ismir2021 权重 |
| `Contents/Resources/gui/`、`mt3/`、`setup.*`、`VERSION` | 程序与启动脚本 |
| `Contents/MacOS/mt3-transcriber` | 启动器（Mach-O，转交 `gui/run.sh`） |
| `dist/mt3-transcriber-<version>-arm64.dmg` | 分发给用户的磁盘映像（.app + 「应用程序」快捷方式） |

```bash
python gui/download_checkpoints.py --dir .checkpoints
VERSION=0.2.0 CHECKPOINTS_DIR="$PWD/.checkpoints" ./packaging/build-macos.sh
# 产物: dist/MT3 Transcriber.app、dist/mt3-transcriber-0.2.0-arm64.dmg
bash packaging/verify-macos.sh dist/*.dmg   # 验证「拖动后」和「只读卷」两种启动方式
```

- **只支持 Apple Silicon（arm64），不支持 Intel Mac**：TensorFlow / jaxlib 的
  macOS 轮子只有 arm64；Rosetta 也只能在 x86 侧跑 x86，无法反向执行 arm64 应用。
- 系统下限 **macOS 12 (Monterey)**：TensorFlow 的 macOS 轮子标的是 `macosx_12_0`，
  jaxlib 是 `macosx_11_0`，两者在 macOS 12 上都能用；`Info.plist` 里写
  `LSMinimumSystemVersion=12.0`。
- **纯 CPU 推理**。`jax-metal` 长期停更、算子不全且会算出错误结果，因此不随包
  分发；CPU 上转写一首歌通常需要几分钟。
- 构建机需要 macOS + Xcode 命令行工具（`xcode-select --install`，提供 clang /
  sips / iconutil / hdiutil）。推荐用 CI 的 `macos-14` runner。
- **包内运行时是可搬动的**：`build-macos.sh` 把 venv 的 `pyvenv.cfg`（`home`）与
  `bin/python*` 都改写成**相对路径**，所以用户把 `.app` 拖到哪、甚至直接从挂载的
  `.dmg` 里启动，包内解释器都能自己找到 `Contents/Resources/runtime/python`。
  运行时不需要解压、不需要复制、也不需要校正——启动时**不写包内任何文件**
  （`PYTHONDONTWRITEBYTECODE=1`，字节码在构建时已预编译），因此将来做签名/公证也
  不会有「应用改自己导致签名失效」的问题。
- macOS 上的路径：配置在 `~/.config/mt3-transcriber/config.json`（遵循
  `XDG_CONFIG_HOME`），权重下载的兜底目录在 `~/Library/Caches/mt3-transcriber`，
  日志（Finder 启动时没有终端可看）在 `~/Library/Logs/mt3-transcriber.log`。
- 中文字体用系统自带的苹方/黑体（macOS 上 Tk 走 CoreText），不需要 Xft 版
  Tcl/Tk，也不需要 `gui/fix_tk_system.py`。
- 未公证的应用首次打开会被 Gatekeeper 拦一次，需要告诉用户：
  macOS 12–14 用「右键 → 打开 → 打开」；macOS 15+ 在「系统设置 → 隐私与安全性」
  里点「仍要打开」；或者执行
  `xattr -dr com.apple.quarantine "/Applications/MT3 Transcriber.app"`。
  设了 `APPLE_SIGN_IDENTITY` 时 `build-macos.sh` 会做正式签名（便于后续公证）。
- App 内自带 uv（`Contents/Resources/runtime/uv/uv`），用户想自行补装依赖时可用。
- macOS 的 PyPI 上**没有 `tensorflow-cpu` 的轮子**（这个包名只有 Linux/Windows
  有），而 t5x 依赖它。macOS 版的 `tensorflow` 本身就是 CPU-only、模块完全等价，
  所以 `build-macos.sh` 先装真的 `tensorflow`，再造一个空的 `tensorflow-cpu`
  兼容壳（版本号对齐）把依赖解析喂饱；壳里没有任何 tensorflow 模块，
  `import tensorflow` 仍然解析到真包。

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
git tag v0.2.0
git push origin v0.2.0
```

`.github/workflows/packages.yml` 会执行静态检查，然后并行准备三份共享产物——带
Xft 的 Tcl/Tk（`tcltk`）、离线运行时（`runtime`，Python + CPU 依赖）、模型权重
（`checkpoints`）——再据此构建 deb/rpm/AppImage 与 macOS 的 `.app`/`.dmg`、构建
Docker 镜像作为验证，并在 tag 触发时上传 GitHub Release 和 SHA256SUMS。`runtime`
job 里会另跑一次「解压到别的目录再 import」的验证，确保内置运行时换路径后仍可用。

`macos` job（`macos-14`，arm64）会完整跑一遍 `packaging/build-macos.sh`，再用
`packaging/verify-macos.sh dist/*.dmg` 验证：先检查包结构（Mach-O 启动器、有效签名、
`LSMinimumSystemVersion`、venv 的相对路径、gin 配置与权重），再把 `.app` 复制到别处
（模拟用户拖到「应用程序」）用包内启动器真跑两次转谱（`ismir2021` 与 `mt3`，含后处理）、
确认包内文件一个都没被改动、跑后处理与端到端自检、做一次 Tk 冒烟测试，最后挂载 dmg
**从只读卷直接启动并转谱**，确认既不复制运行时也不写任何文件。
`.github/workflows/build-test-packages.yml` 里的 `build-macos` job 在每次 push 时做
同样的事（不发布）。

macOS 的包体积：`.dmg` 实测约 1.5GB，`app` 解开后约 2.3GB；GitHub Release 单文件
上限 2GiB，`verify-macos.sh` 会检查 dmg 没有超限。

后处理（`gui/midi_rearrange.py`）在多处都会被验证：`test`/`checks` job 里装上
`mido` 跑一遍合成 MIDI 自检；`runtime` job 里用**解压后的真实运行时**再跑一遍
（`--selftest`）；macOS 构建里也会跑一遍（`build-macos.sh` 与 `verify-macos.sh`），
因此能确认 `mido` 确实随包可用。

官方 GitHub Runner 没有 NVIDIA GPU，因此 GPU 推理仍需在目标 GPU 机器上执行
`selftest.py`。
