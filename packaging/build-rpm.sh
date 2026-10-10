#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.1.0}"
rm -rf "$ROOT/.rpmbuild" "$ROOT/dist"
find "$ROOT/mt3" "$ROOT/gui" "$ROOT/packaging" -type d -name __pycache__ -prune -exec rm -rf {} +
mkdir -p "$ROOT/.rpmbuild"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
mkdir -p "$ROOT/.rpmbuild/SOURCES/mt3-transcriber"
cp -a "$ROOT/mt3" "$ROOT/gui" "$ROOT/packaging" "$ROOT/.rpmbuild/SOURCES/mt3-transcriber/"
# setup.py/setup.cfg 必须在，install.sh 才能 pip install 本包并装齐依赖。
cp -a "$ROOT/setup.py" "$ROOT/setup.cfg" "$ROOT/.rpmbuild/SOURCES/mt3-transcriber/"
# 随包分发带 Xft 的 Tcl/Tk（由 packaging/build-tcltk.sh 生成），修复中文空白。
TCLTK_DIR="${TCLTK_DIR:-}"
if [ -n "$TCLTK_DIR" ] && [ -d "$TCLTK_DIR/lib" ]; then
  cp -a "$TCLTK_DIR" "$ROOT/.rpmbuild/SOURCES/mt3-transcriber/tcltk"
else
  echo "warning: TCLTK_DIR 未设置；包内将不含 Xft Tcl/Tk，中文可能显示为空白。" >&2
fi
find "$ROOT/.rpmbuild/SOURCES/mt3-transcriber" -type d -exec chmod 755 {} +
tar -C "$ROOT/.rpmbuild/SOURCES" -czf "$ROOT/.rpmbuild/SOURCES/mt3-transcriber.tar.gz" mt3-transcriber
rm -rf "$ROOT/.rpmbuild/SOURCES/mt3-transcriber"
cp "$ROOT/packaging/mt3-transcriber.spec" "$ROOT/.rpmbuild/SPECS/"
rpmbuild --define "_topdir $ROOT/.rpmbuild" --define "version $VERSION" \
  -ba "$ROOT/.rpmbuild/SPECS/mt3-transcriber.spec"
mkdir -p "$ROOT/dist"
find "$ROOT/.rpmbuild/RPMS" -name '*.rpm' -exec cp {} "$ROOT/dist/" \;
echo "Built RPMs in $ROOT/dist"
