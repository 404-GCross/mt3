#!/usr/bin/env python3
# Copyright 2026 The MT3 Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Download MT3 pretrained checkpoints over public HTTPS.

No gsutil/gcloud required. Checkpoints are read from the public bucket
``gs://mt3/checkpoints`` via ``https://storage.googleapis.com/mt3/...``.

Usage::

    python gui/download_checkpoints.py              # 下载全部（约 340MB）
    python gui/download_checkpoints.py --models mt3 # 只下载多乐器模型
    python gui/download_checkpoints.py --list       # 只列出文件
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BUCKET_API = 'https://storage.googleapis.com/storage/v1/b/mt3/o'
PUBLIC_BASE = 'https://storage.googleapis.com/mt3/'
BUCKET_PREFIX = 'checkpoints/'

# 镜像：权重按模型打包挂在 <repo> 的 `checkpoints` Release 上（约 164 MiB/个），
# 国内可用 gh-proxy 加速；官方 GCS 不可达时自动切换。
MIRROR_REPO = '404-GCross/mt3'
MIRROR_TAG = 'checkpoints'
MIRROR_ASSET = 'mt3-checkpoints-{model}.tar.gz'
MIRROR_CONF = Path(
    os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) \
    / 'mt3-transcriber' / 'mirror.conf'


def read_mirror_conf():
  """Read ``KEY=VALUE`` lines from ``~/.config/mt3-transcriber/mirror.conf``."""
  values = {}
  try:
    lines = MIRROR_CONF.read_text(encoding='utf-8').splitlines()
  except OSError:
    return values
  for line in lines:
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
      continue
    key, _, value = line.partition('=')
    values[key.strip()] = value.strip().strip('"').strip("'")
  return values


def mirror_settings():
  """Return ``(gh_proxy, checkpoint_mirror)`` from env, then mirror.conf."""
  conf = read_mirror_conf()
  proxy = os.environ.get('MT3_GH_PROXY') or conf.get('MT3_GH_PROXY', '')
  mirror = (os.environ.get('MT3_CHECKPOINT_MIRROR')
            or conf.get('MT3_CHECKPOINT_MIRROR', ''))
  return proxy.rstrip('/'), mirror.rstrip('/')


def checkpoint_archive_url(model):
  """URL of the per-model tarball mirror, optionally behind gh-proxy."""
  proxy, mirror = mirror_settings()
  base = mirror or f'https://github.com/{MIRROR_REPO}/releases/download/{MIRROR_TAG}'
  url = f'{base}/{MIRROR_ASSET.format(model=model)}'
  return f'{proxy}/{url}' if proxy else url

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = Path(
    os.environ.get('MT3_CHECKPOINT_DIR', str(REPO_ROOT / 'checkpoints')))

ALL_MODELS = ('ismir2021', 'mt3')


def list_objects(prefix):
  """Yield (name, size) for all objects under a bucket prefix."""
  page_token = None
  while True:
    url = (f'{BUCKET_API}?prefix={urllib.parse.quote(prefix)}'
           f'&fields=items(name,size),nextPageToken&maxResults=1000')
    if page_token:
      url += f'&pageToken={urllib.parse.quote(page_token)}'
    with urllib.request.urlopen(url, timeout=60) as response:
      payload = json.load(response)
    for item in payload.get('items', []):
      if not item['name'].endswith('/'):
        yield item['name'], int(item['size'])
    page_token = payload.get('nextPageToken')
    if not page_token:
      break


def download_file(name, size, dest_root, retries=3):
  """Download one object; returns downloaded byte count (0 if unchanged)."""
  relative = name[len(BUCKET_PREFIX):]
  target = dest_root / relative
  part = target.with_name(target.name + '.part')

  if target.exists() and target.stat().st_size == size:
    return 0

  target.parent.mkdir(parents=True, exist_ok=True)
  url = PUBLIC_BASE + urllib.parse.quote(name)

  last_error = None
  for attempt in range(1, retries + 1):
    resume_at = part.stat().st_size if part.exists() else 0
    try:
      request = urllib.request.Request(url)
      if resume_at:
        request.add_header('Range', f'bytes={resume_at}-')
      with urllib.request.urlopen(request, timeout=120) as response:
        if resume_at and response.status != 206:
          # Server ignored the Range header; start over.
          resume_at = 0
        mode = 'ab' if resume_at else 'wb'
        with open(part, mode) as output:
          shutil.copyfileobj(response, output, length=256 * 1024)
      if part.stat().st_size != size:
        raise IOError(
            f'大小不符: {part.stat().st_size} != {size}')
      part.replace(target)
      return size
    except (urllib.error.URLError, IOError, OSError) as exc:
      last_error = exc
      if attempt < retries:
        time.sleep(2 * attempt)
  raise RuntimeError(f'下载失败 {name}: {last_error}')


def human_bytes(num):
  for unit in ('B', 'KB', 'MB', 'GB'):
    if num < 1024 or unit == 'GB':
      return f'{num:.1f} {unit}' if unit != 'B' else f'{int(num)} B'
    num /= 1024


def download_models_from_gcs(dest, models, progress=None):
  """Download selected models from the official Google Cloud bucket."""
  models = list(models)
  jobs = []
  for model in models:
    objects = list(list_objects(f'{BUCKET_PREFIX}{model}/'))
    if not objects:
      raise RuntimeError(f'远端未找到模型: {model}')
    jobs.extend(objects)
  dest = Path(dest)
  dest.mkdir(parents=True, exist_ok=True)
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
    futures = [executor.submit(download_file, name, size, dest)
               for name, size in jobs]
    for index, future in enumerate(futures, 1):
      future.result()
      if progress:
        progress(f'模型下载进度: {index}/{len(futures)}')
  for model in models:
    marker = dest / model / 'checkpoint'
    if not marker.exists():
      raise RuntimeError(f'模型下载完成但缺少标记文件: {marker}')


def download_url(url, target, retries=3):
  """Download ``url`` to ``target`` (resumes a partial ``.part`` file)."""
  target = Path(target)
  target.parent.mkdir(parents=True, exist_ok=True)
  part = target.with_name(target.name + '.part')

  last_error = None
  for attempt in range(1, retries + 1):
    resume_at = part.stat().st_size if part.exists() else 0
    try:
      request = urllib.request.Request(url)
      if resume_at:
        request.add_header('Range', f'bytes={resume_at}-')
      with urllib.request.urlopen(request, timeout=120) as response:
        if resume_at and response.status != 206:
          resume_at = 0
        mode = 'ab' if resume_at else 'wb'
        with open(part, mode) as output:
          shutil.copyfileobj(response, output, length=256 * 1024)
      part.replace(target)
      return target
    except (urllib.error.URLError, IOError, OSError) as exc:
      last_error = exc
      if attempt < retries:
        time.sleep(2 * attempt)
  raise RuntimeError(f'下载失败 {url}: {last_error}')


def download_models_from_archive(dest, models, progress=None):
  """Fetch per-model tarballs from the release mirror and extract them."""
  models = list(models)
  dest = Path(dest)
  dest.mkdir(parents=True, exist_ok=True)
  for index, model in enumerate(models, 1):
    marker = dest / model / 'checkpoint'
    if marker.exists():
      if progress:
        progress(f'{model}: 已存在，跳过（{index}/{len(models)}）')
      continue
    note = f'（{index}/{len(models)}）'
    if progress:
      progress(f'下载镜像包: {model} {note} …')
    archive = dest / f'.{model}.tar.gz'
    download_url(checkpoint_archive_url(model), archive)
    if progress:
      progress(f'解压镜像包: {model} {note} …')
    with tarfile.open(archive, 'r:gz') as handle:
      try:
        handle.extractall(dest, filter='data')
      except TypeError:  # Python < 3.11.4 没有 filter 参数
        handle.extractall(dest)
    archive.unlink(missing_ok=True)
    if not marker.exists():
      raise RuntimeError(f'镜像包解压后缺少标记文件: {marker}')


def download_models(dest, models, progress=None):
  """Download selected models for use by the GUI.

  ``MT3_CHECKPOINT_SOURCE=gcs|mirror`` forces one source.  Default is auto:
  prefer the GitHub mirror when ``MT3_GH_PROXY``/``MT3_CHECKPOINT_MIRROR`` is
  configured（国内通常已经用它加速），otherwise try the official bucket first
  and fall back to the mirror.
  """
  models = list(models)
  source = (os.environ.get('MT3_CHECKPOINT_SOURCE') or '').strip().lower()
  proxy, mirror = mirror_settings()

  def via_gcs():
    download_models_from_gcs(dest, models, progress)

  def via_archive():
    download_models_from_archive(dest, models, progress)

  if source == 'gcs':
    via_gcs()
    return
  if source in ('mirror', 'archive', 'github'):
    via_archive()
    return

  if mirror or proxy:
    try:
      via_archive()
      return
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
      if progress:
        progress(f'镜像源不可用（{exc}）；改用官方源…')
  try:
    via_gcs()
  except (urllib.error.URLError, OSError, RuntimeError) as exc:
    if progress:
      progress(f'官方源不可用（{exc}）；改用 GitHub 镜像…')
    via_archive()


def _download_via_mirror(args, models):
  """CLI helper: fetch models from the GitHub release mirror."""
  if args.list:
    for model in models:
      print(checkpoint_archive_url(model))
    return
  dest = Path(args.dir)
  print(f'保存到: {dest}\n从 GitHub 镜像下载（每个模型约 164 MiB）…')
  download_models_from_archive(
      dest, models, progress=lambda message: print(message))
  for model in models:
    marker = dest / model / 'checkpoint'
    if marker.exists():
      print(f'✓ {model}: {marker}')
    else:
      print(f'✗ {model}: 缺少 {marker}', file=sys.stderr)
      sys.exit(1)
  print('全部完成。现在可以运行: python gui/app.py')


def main():
  parser = argparse.ArgumentParser(
      description='下载 MT3 预训练 checkpoint（公开 HTTPS，无需 gsutil）')
  parser.add_argument(
      '--dir', default=str(DEFAULT_DEST),
      help=f'保存目录（默认 {DEFAULT_DEST}）')
  parser.add_argument(
      '--models', default=','.join(ALL_MODELS),
      help=f'要下载的模型，逗号分隔（默认 {",".join(ALL_MODELS)}）')
  parser.add_argument('--jobs', type=int, default=8, help='并行下载数（默认 8）')
  parser.add_argument('--list', action='store_true', help='仅列出远端文件')
  parser.add_argument(
      '--source', choices=('auto', 'gcs', 'mirror'), default='auto',
      help='下载源：auto（默认，官方源失败自动切镜像）/ gcs / mirror')
  args = parser.parse_args()

  models = [m.strip() for m in args.models.split(',') if m.strip()]
  unknown = [m for m in models if m not in ALL_MODELS]
  if unknown:
    parser.error(f'未知模型: {", ".join(unknown)}（可选: {", ".join(ALL_MODELS)}）')

  jobs = []
  if args.source == 'mirror':
    _download_via_mirror(args, models)
    return
  for model in models:
    prefix = f'{BUCKET_PREFIX}{model}/'
    try:
      objects = list(list_objects(prefix))
    except urllib.error.URLError as exc:
      if args.source == 'gcs':
        print(f'无法访问下载源（需要联网）: {exc}', file=sys.stderr)
        sys.exit(1)
      print(f'官方源不可用（{exc}），改用 GitHub 镜像…', file=sys.stderr)
      _download_via_mirror(args, models)
      return
    total = sum(size for _, size in objects)
    jobs.extend((name, size) for name, size in objects)
    print(f'{model}: {len(objects)} 个文件, {human_bytes(total)}')
    if not objects:
      print(f'警告: 远端未找到 {prefix}', file=sys.stderr)
      sys.exit(1)

  if args.list:
    for name, size in jobs:
      print(f'{size:>12}  {name}')
    return

  dest = Path(args.dir)
  dest.mkdir(parents=True, exist_ok=True)
  print(f'保存到: {dest}\n开始下载（{args.jobs} 线程并行）…')

  lock = threading.Lock()
  state = {'done_files': 0, 'done_bytes': 0, 'failed': []}
  grand_total = sum(size for _, size in jobs)
  start = time.time()

  def report():
    elapsed = max(time.time() - start, 1e-6)
    percent = 100 * state['done_bytes'] / grand_total if grand_total else 100
    speed = human_bytes(state['done_bytes'] / elapsed)
    print(f'\r进度: {state["done_files"]}/{len(jobs)} 个文件  '
          f'{human_bytes(state["done_bytes"])}/{human_bytes(grand_total)}  '
          f'({percent:.1f}%)  {speed}/s', end='', flush=True)

  only_missing = []

  def worker(item):
    name, size = item
    try:
      downloaded = download_file(name, size, dest)
    except RuntimeError as exc:
      with lock:
        state['failed'].append(str(exc))
      return
    with lock:
      state['done_files'] += 1
      state['done_bytes'] += downloaded if downloaded else size
      report()

  # Count already-present files toward the total up front.
  for name, size in jobs:
    relative = Path(name[len(BUCKET_PREFIX):])
    target = dest / relative
    if target.exists() and target.stat().st_size == size:
      state['done_bytes'] += size
      state['done_files'] += 1
    else:
      only_missing.append((name, size))
  report()

  if only_missing:
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.jobs) as executor:
      list(executor.map(worker, only_missing))
  print()

  if state['failed']:
    print(f'有 {len(state["failed"])} 个文件下载失败:', file=sys.stderr)
    for failure in state['failed']:
      print(f'  {failure}', file=sys.stderr)
    sys.exit(1)

  for model in models:
    marker = dest / model / 'checkpoint'
    if marker.exists():
      print(f'✓ {model}: {marker}')
    else:
      print(f'✗ {model}: 缺少 {marker}', file=sys.stderr)
      sys.exit(1)
  print('全部完成。现在可以运行: python gui/app.py')


if __name__ == '__main__':
  try:
    main()
  except BrokenPipeError:
    # Allow commands such as `--list | head` to exit quietly.
    pass
