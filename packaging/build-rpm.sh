#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.1.0}"
rm -rf "$ROOT/.rpmbuild" "$ROOT/dist"
find "$ROOT/mt3" "$ROOT/gui" "$ROOT/packaging" -type d -name __pycache__ -prune -exec rm -rf {} +
mkdir -p "$ROOT/.rpmbuild"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
mkdir -p "$ROOT/.rpmbuild/SOURCES/mt3-transcriber"
cp -a "$ROOT/mt3" "$ROOT/gui" "$ROOT/packaging" "$ROOT/.rpmbuild/SOURCES/mt3-transcriber/"
# setup.py/setup.cfg 必须在，install.sh 才能 pip install 本包并装齐依赖
# （没有内置运行时时的兜底路径）。
cp -a "$ROOT/setup.py" "$ROOT/setup.cfg" "$ROOT/.rpmbuild/SOURCES/mt3-transcriber/"
# run.sh 用它判断内置运行时是否与包同版本（版本不同则重新解压）。
printf '%s\n' "$VERSION" > "$ROOT/.rpmbuild/SOURCES/mt3-transcriber/VERSION"
# 随包分发带 Xft 的 Tcl/Tk（由 packaging/build-tcltk.sh 生成），修复中文空白。
TCLTK_DIR="${TCLTK_DIR:-}"
if [ -n "$TCLTK_DIR" ] && [ -d "$TCLTK_DIR/lib" ]; then
  cp -a "$TCLTK_DIR" "$ROOT/.rpmbuild/SOURCES/mt3-transcriber/tcltk"
else
  echo "warning: TCLTK_DIR 未设置；包内将不含 Xft Tcl/Tk，中文可能显示为空白。" >&2
fi
# 内置「离线运行时」（Python + 依赖，见 packaging/build-runtime.sh）。
runtime_src="${RUNTIME_TARBALL:-}"
if [ -z "$runtime_src" ]; then
  runtime_src="$(ls "$ROOT"/dist/mt3-runtime*.tar.zst 2>/dev/null | head -1 || true)"
fi
if [ -n "$runtime_src" ] && [ -f "$runtime_src" ]; then
  cp "$runtime_src" "$ROOT/.rpmbuild/SOURCES/mt3-transcriber/runtime.tar.zst"
  echo "runtime: $(du -h "$runtime_src" | cut -f1)  源: $runtime_src"
else
  echo "warning: 未找到 runtime.tar.zst；包内不含离线运行时，首次启动需联网安装。" >&2
fi
# 内置模型权重（mt3 / ismir2021），首次启动无需下载。
checkpoints_src="${CHECKPOINTS_DIR:-$ROOT/.checkpoints}"
if [ -d "$checkpoints_src" ]; then
  mkdir -p "$ROOT/.rpmbuild/SOURCES/mt3-transcriber/checkpoints"
  cp -a "$checkpoints_src"/. "$ROOT/.rpmbuild/SOURCES/mt3-transcriber/checkpoints/"
  echo "checkpoints: $(du -sh "$checkpoints_src" | cut -f1)"
else
  echo "warning: 未找到 ${checkpoints_src}；包内不含模型权重。" >&2
fi
find "$ROOT/.rpmbuild/SOURCES/mt3-transcriber" -type d -exec chmod 755 {} +
tar -C "$ROOT/.rpmbuild/SOURCES" --use-compress-program='gzip -1' \
  -cf "$ROOT/.rpmbuild/SOURCES/mt3-transcriber.tar.gz" mt3-transcriber
rm -rf "$ROOT/.rpmbuild/SOURCES/mt3-transcriber"
cp "$ROOT/packaging/mt3-transcriber.spec" "$ROOT/.rpmbuild/SPECS/"
rpmbuild --define "_topdir $ROOT/.rpmbuild" --define "version $VERSION" \
  -ba "$ROOT/.rpmbuild/SPECS/mt3-transcriber.spec"
mkdir -p "$ROOT/dist"
find "$ROOT/.rpmbuild/RPMS" -name '*.rpm' -exec cp {} "$ROOT/dist/" \;
echo "Built RPMs in $ROOT/dist"
# 清掉巨大的中间产物（源码 tarball、BUILD 树），给 CI 释放磁盘。
rm -rf "$ROOT/.rpmbuild/BUILD" "$ROOT/.rpmbuild/SOURCES/mt3-transcriber.tar.gz"
