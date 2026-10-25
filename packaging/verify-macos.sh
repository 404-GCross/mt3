#!/usr/bin/env bash
# 验证 macOS 应用包（.app / .dmg）能不能真的用：
#
#   1. 结构：启动器是 Mach-O、包内解释器/权重/gin 配置齐全、venv 用相对路径；
#   2. 复制到别处（模拟用户把 .app 拖到「应用程序」），用包内启动器跑真·转谱
#      （两个模型，都开后处理）；
#   3. WebUI：用包内服务起 --web --lan，HTTP 上传音频、等转谱、下载 MIDI；
#   4. 后处理自检 + 端到端自检（合成琶音 -> 模型 -> MIDI）+ Tk 冒烟测试；
#   5. 目标是 .dmg 时挂载后从**只读卷**启动一次，确认能跑、且包内文件一个都没动。
#
# 用法: packaging/verify-macos.sh [dist/MT3 Transcriber.app | dist/*.dmg]
# 缺省自动找 dist/*.app。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
target="${1:-}"
if [ -z "$target" ]; then
  target="$(ls -d "$ROOT"/dist/*.app 2>/dev/null | head -1 || true)"
fi
if [ -z "$target" ] || [ ! -e "$target" ]; then
  echo "用法: packaging/verify-macos.sh <MT3 Transcriber.app | *.dmg>" >&2
  exit 1
fi
target="$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"

log() { printf '\033[1;36m[verify]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[verify]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[verify]\033[0m %s\n' "$*" >&2; exit 1; }

# 启动器把日志写到 ~/Library/Logs（Finder 启动时看不到终端），失败时贴出来。
logfile="$HOME/Library/Logs/mt3-transcriber.log"
show_log() {
  if [ -f "$logfile" ]; then
    echo "--- $logfile (最后 25 行) ---" >&2
    tail -n 25 "$logfile" >&2
    echo "--- 日志结束 ---" >&2
  fi
}

[ "$(uname -s)" = "Darwin" ] || die "只能在 macOS 上验证（当前: $(uname -s)）"

work="$(mktemp -d "${TMPDIR:-/tmp}/mt3-verify.XXXXXX")"
mounted=""
web_pid=""
cleanup() {
  if [ -n "$web_pid" ]; then
    kill "$web_pid" 2>/dev/null || true
  fi
  if [ -n "$mounted" ]; then
    hdiutil detach "$mounted" -quiet 2>/dev/null \
      || hdiutil detach "$mounted" -force -quiet 2>/dev/null || true
  fi
  rm -rf "$work"
}
trap cleanup EXIT

# 别污染验证者真实的用户目录（正常情况下根本不会往这里写东西）。
export MT3_USER_HOME="$work/Library/Application Support/mt3-transcriber"
export MT3_USER_CACHE="$work/Library/Caches/mt3-transcriber"

# 应用包内容的指纹：文件清单 + 大小/时间戳。用来确认「运行时不改动包内文件」。
fingerprint() {
  find "$1" \( -type f -o -type l \) -exec stat -f '%N %z %m' {} + 2>/dev/null \
    | LC_ALL=C sort | cksum
}

case "$target" in
  *.dmg)
    size="$(stat -f%z "$target")"
    log "dmg 体积: $((size / 1024 / 1024)) MiB（GitHub Release 单文件上限 2 GiB）"
    [ "$size" -lt 2147483648 ] || die "dmg 超过 2 GiB，传不上 GitHub Release"
    [ "$size" -gt 300000000 ] || die "dmg 太小，里面大概没装东西"
    mountpoint="$work/vol"
    mkdir -p "$mountpoint"
    log "挂载 ${target}（只读）"
    hdiutil attach "$target" -nobrowse -readonly -mountpoint "$mountpoint" >/dev/null
    mounted="$mountpoint"
    app="$(ls -d "$mountpoint"/*.app 2>/dev/null | head -1 || true)"
    [ -n "$app" ] || die "dmg 里没有 .app"
    ;;
  *.app)
    app="$target"
    ;;
  *)
    die "不认识的目标: $target"
    ;;
esac
log "应用包: $(basename "$app")"

# ---------------------------------------------------------------- 结构
moved="$work/MT3 Transcriber.app"
# ditto 是 macOS 上复制 .app 的正确工具（保留符号链接、扩展属性、权限）。
ditto "$app" "$moved"
res="$moved/Contents/Resources"
test -f "$res/VERSION" || die "缺少 VERSION"
test -f "$res/checkpoints/mt3/checkpoint" || die "缺少 mt3 权重"
test -f "$res/checkpoints/ismir2021/checkpoint" || die "缺少 ismir2021 权重"
test -d "$res/runtime/venv" || die "缺少随包运行时"
test -d "$res/runtime/python" || die "缺少随包解释器"
test -f "$res/gui/app.py" || die "缺少 gui/app.py"
# backend.py 用 <Resources>/mt3/gin 下的 gin 配置，缺了模型加载不了。
test -f "$res/mt3/gin/model.gin" || die "缺少 mt3/gin/model.gin"
test -f "$res/mt3/gin/ismir2021.gin" || die "缺少 mt3/gin 配置"
case "$(file "$moved/Contents/MacOS/mt3-transcriber")" in
  *Mach-O*) ;;
  *) die "启动器不是 Mach-O 可执行文件" ;;
esac
codesign -v "$moved/Contents/MacOS/mt3-transcriber" 2>/dev/null \
  || die "启动器没有有效签名（Apple Silicon 上无法运行）"
case "$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' \
        "$moved/Contents/Info.plist" 2>/dev/null || true)" in
  12.*) ;;
  *) die "Info.plist 的 LSMinimumSystemVersion 不是 12.x" ;;
esac
# venv 必须是相对路径：.app 被拖走、或从只读卷启动时，不改包内文件就要能用。
grep -q '^home = \.\./' "$res/runtime/venv/pyvenv.cfg" \
  || die "venv 的 pyvenv.cfg home 不是相对路径（.app 拖动后会失效）"
case "$(readlink "$res/runtime/venv/bin/python")" in
  ../*) ;;
  *) die "venv/bin/python 不是相对符号链接" ;;
esac
log "版本: $(cat "$res/VERSION")"

launcher="$moved/Contents/MacOS/mt3-transcriber"
venv_py="$res/runtime/venv/bin/python"
[ -x "$venv_py" ] || die "包内解释器不可执行: $venv_py"

# ---------------------------------------------------------------- 真·转谱（拖动后）
# 两秒 440Hz 正弦，用包内解释器生成（纯 stdlib，与依赖无关）。
wav="$work/tone.wav"
"$venv_py" - "$wav" <<'PY'
import math, struct, sys, wave
rate = 16000
frames = b''.join(
    struct.pack('<h', int(0.5 * 32767 * math.sin(2 * math.pi * 440 * i / rate)))
    for i in range(rate * 2))
with wave.open(sys.argv[1], 'wb') as f:
  f.setnchannels(1)
  f.setsampwidth(2)
  f.setframerate(rate)
  f.writeframes(frames)
PY

before="$(fingerprint "$moved")"
for model in ismir2021 mt3; do
  out="$work/out-$model.mid"
  log "启动器转谱（${model}，包已被拖到别处）…"
  "$launcher" --cli "$wav" -o "$out" --model "$model" --rearrange || {
    show_log
    die "转谱失败（${model}）"
  }
  test -s "$out" || die "没有生成 MIDI: $out"
  test -s "${out%.mid}.rearranged.mid" || die "没有生成整理后的 MIDI"
  size="$(stat -f%z "$out")"
  rsize="$(stat -f%z "${out%.mid}.rearranged.mid")"
  log "  -> $(basename "$out") ${size} 字节，整理版 ${rsize} 字节"
done

# ---------------------------------------------------------------- WebUI（局域网服务）
# 真起一次服务：上传 -> 队列 -> 转谱 -> 下载，顺便确认令牌校验与 --lan。
web_token="verify-token"
web_port=18765
while lsof -nP -iTCP:"$web_port" -sTCP:LISTEN >/dev/null 2>&1; do
  web_port=$((web_port + 1))
done
log "启动 WebUI（--web --lan，端口 ${web_port}）…"
"$res/gui/run.sh" --web --lan --port "$web_port" --token "$web_token" \
  --max-bytes 50000000 --max-minutes 5 --keep-hours 1 \
  >"$work/web.log" 2>&1 &
web_pid=$!

web_ready=0
for _ in $(seq 1 300); do
  if curl -fsS "http://127.0.0.1:${web_port}/api/health" >/dev/null 2>&1; then
    web_ready=1
    break
  fi
  kill -0 "$web_pid" 2>/dev/null || break
  sleep 0.5
done
if [ "$web_ready" != 1 ]; then
  tail -n 40 "$work/web.log" >&2 || true
  die "WebUI 没有起来"
fi

code="$(curl -s -o /dev/null -w '%{http_code}' \
  "http://127.0.0.1:${web_port}/api/jobs")"
[ "$code" = 401 ] || die "WebUI 没有校验令牌（HTTP ${code}）"

web_job="$(
  curl -fsS -H "Authorization: Bearer ${web_token}" \
    -F "file=@$wav" -F model=ismir2021 -F rearrange=true \
    "http://127.0.0.1:${web_port}/api/jobs" \
  | "$venv_py" -c 'import json, sys; print(json.load(sys.stdin)["id"])'
)" || die "WebUI 上传失败"
log "  任务 ${web_job} 已入队，等待转谱…"

web_status=""
for _ in $(seq 1 900); do
  web_status="$(
    curl -fsS -H "Authorization: Bearer ${web_token}" \
      "http://127.0.0.1:${web_port}/api/jobs/${web_job}" \
    | "$venv_py" -c 'import json, sys; print(json.load(sys.stdin)["status"])'
  )" || break
  case "$web_status" in
    done|failed|cancelled) break ;;
  esac
  sleep 1
done
if [ "$web_status" != "done" ]; then
  curl -s -H "Authorization: Bearer ${web_token}" \
    "http://127.0.0.1:${web_port}/api/jobs/${web_job}" >&2 || true
  tail -n 40 "$work/web.log" >&2 || true
  die "WebUI 转谱失败（状态: ${web_status:-未知}）"
fi

for kind in raw rearranged; do
  out="$work/web-${kind}.mid"
  curl -fsS -H "Authorization: Bearer ${web_token}" -o "$out" \
    "http://127.0.0.1:${web_port}/api/jobs/${web_job}/midi?kind=${kind}" \
    || die "WebUI 下载失败（${kind}）"
  test -s "$out" || die "WebUI 下载的 MIDI 是空的（${kind}）"
done
log "  WebUI 上传/转谱/下载通过（$(stat -f%z "$work/web-raw.mid") 字节，整理版 $(stat -f%z "$work/web-rearranged.mid") 字节）"

kill "$web_pid" 2>/dev/null || true
wait "$web_pid" 2>/dev/null || true
web_pid=""

[ "$(fingerprint "$moved")" = "$before" ] \
  || die "运行后包内文件被改动了（应该一个都不写）"

# ---------------------------------------------------------------- 自检
log "后处理自检…"
"$venv_py" "$res/gui/midi_rearrange.py" --selftest >/dev/null
log "端到端自检（合成琶音 -> 模型 -> MIDI）…"
"$venv_py" "$res/gui/selftest.py" --model ismir2021

log "Tk 冒烟测试（--check-fonts）…"
if "$launcher" --check-fonts; then
  log "  Tk 正常。"
else
  warn "  Tk 冒烟测试失败（无图形会话时会这样）；请在有桌面的机器上再试一次。"
fi
if [ -f "$logfile" ]; then
  tail -n 5 "$logfile" | sed 's/^/    /' || true
fi

# ---------------------------------------------------------------- 只读卷（dmg）
if [ -n "$mounted" ]; then
  log "从只读卷启动（${app}）…"
  mount_before="$(fingerprint "$app")"
  if ! "$app/Contents/MacOS/mt3-transcriber" --cli "$wav" \
       -o "$work/ro.mid" --model ismir2021 >/dev/null 2>&1; then
    show_log
    die "从只读卷启动/转谱失败"
  fi
  test -s "$work/ro.mid" || die "只读卷转谱没有产出 MIDI"
  [ "$(fingerprint "$app")" = "$mount_before" ] \
    || die "只读卷上的包被改动了（应该一个都不写）"
  if [ -n "$(ls -A "$MT3_USER_HOME" 2>/dev/null || true)" ]; then
    die "从只读卷运行后往 $MT3_USER_HOME 里写了东西（应该什么都不写）"
  fi
  log "  只读卷可直接运行，且没有复制运行时、没有改动包内文件。"
fi

log "全部通过。"
