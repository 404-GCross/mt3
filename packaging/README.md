# MT3 packages

各平台的打包脚本与产物：

| 脚本 | 产物 | 说明 |
|---|---|---|
| `build-runtime.sh` | `dist/mt3-runtime-<ver>.tar.zst` | 离线运行时（Python 3.12 + CPU 依赖），deb/rpm 复用 |
| `build-tcltk.sh` | `<dir>/` | 带 Xft 的 Tcl/Tk（Linux 中文界面用） |
| `build-deb.sh` | `dist/*.deb` | Debian/Ubuntu 离线包 |
| `build-rpm.sh` | `dist/*.rpm` | Fedora/RHEL 离线包 |
| `build-appimage.sh` | `dist/*.AppImage` | 单文件 AppImage（权重首次启动下载） |
| `build-macos.sh` | `dist/MT3 Transcriber.app`、`dist/*-arm64.dmg` | macOS（Apple Silicon）自包含应用 |
| `verify-macos.sh` | — | 验证 macOS 包：拖动后 / 只读卷下的启动与真·转谱 |

deb/rpm 与 macOS 应用包内置完整的离线运行时与两个模型权重，装好即可离线使用；
AppImage 只带运行时，权重首次启动时下载（把体积控制在 GitHub Release 的 2GiB
上限内）。Windows 尚未支持。

完整说明见 [../PACKAGING.md](../PACKAGING.md)。
