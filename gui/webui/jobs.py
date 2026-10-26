"""WebUI 的任务队列：上传的音频排队、串行转谱、可选后处理。

三条设计约束：

- **模型不适合并发**：JAX 一次只跑一个任务，所以只有一个 worker 线程，任务串行。
- **模型加载很慢**：进程内缓存；``keep_models <= 1`` 时换模型会先卸载旧的，
  避免 GPU 显存里同时躺着两个模型。
- **HTTP 线程与 worker 线程共享状态**：所有读写都在 ``JobManager`` 的锁里。

这个模块**不导入** backend / JAX / TensorFlow（``make_runner`` 返回的函数里才
延迟导入），所以接口自检可以在只有 mido 的环境里跑。
"""

from __future__ import annotations

import collections
import os
import queue
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

#: 允许上传的扩展名（与命令行批量模式保持一致）。
AUDIO_EXTENSIONS = (
    '.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.aiff', '.aif', '.opus')

QUEUED = 'queued'
RUNNING = 'running'
DONE = 'done'
FAILED = 'failed'
CANCELLED = 'cancelled'
FINAL_STATUSES = (DONE, FAILED, CANCELLED)


class JobCancelled(Exception):
  """在 progress 回调里抛出，用来中断转谱。"""


class QueueFull(Exception):
  """队列已满（HTTP 层转成 429）。"""


@dataclass
class Job:
  """一个转谱任务。字段只在持有 ``JobManager._lock`` 时修改。"""

  id: str
  filename: str
  model: str
  directory: Path
  input_path: Path
  rearrange: Optional[dict] = None
  status: str = QUEUED
  done: int = 0
  total: Optional[int] = None
  error: Optional[str] = None
  raw_path: Optional[Path] = None
  rearranged_path: Optional[Path] = None
  rearrange_detail: Optional[str] = None
  notes: Optional[int] = None
  # 给页面画钢琴卷用的预览：[[x, y, w], ...]，都是 0..1（见 midi_roll）。
  roll: Optional[list] = None
  created: float = field(default_factory=time.time)
  started: Optional[float] = None
  finished: Optional[float] = None
  cancel_event: threading.Event = field(
      default_factory=threading.Event, repr=False)
  log_lines: collections.deque = field(
      default_factory=lambda: collections.deque(maxlen=200), repr=False)

  def log(self, message):
    stamp = time.strftime('%H:%M:%S')
    self.log_lines.append('{} {}'.format(stamp, message))

  @property
  def percent(self):
    if self.status == DONE:
      return 100
    if not self.total:
      return None
    return min(99, int(100 * self.done / self.total))

  def to_dict(self, queue_position=None):
    started = self.started or self.created
    end = self.finished if self.finished is not None else time.time()
    return {
        'id': self.id,
        'filename': self.filename,
        'model': self.model,
        'status': self.status,
        'done': self.done,
        'total': self.total,
        'percent': self.percent,
        'error': self.error,
        'notes': self.notes,
        'created': self.created,
        'started': self.started,
        'finished': self.finished,
        'elapsed': max(0.0, end - started),
        'raw': bool(self.raw_path and Path(self.raw_path).exists()),
        'rearranged': bool(
            self.rearranged_path and Path(self.rearranged_path).exists()),
        'rearrange_detail': self.rearrange_detail,
        'roll': self.roll,
        'queue_position': queue_position,
        'log': list(self.log_lines)[-20:],
    }


class JobManager:
  """串行执行任务的队列。"""

  def __init__(self, jobs_dir, runner: Callable[['Job', 'JobManager'], None],
               queue_limit=8, keep_hours=24.0, keep_models=1):
    self.jobs_dir = Path(jobs_dir)
    self.queue_limit = max(1, int(queue_limit))
    self.keep_hours = float(keep_hours)
    self.keep_models = int(keep_models)
    self._runner = runner
    self._jobs = {}
    self._queue = queue.Queue()
    self._lock = threading.Lock()
    self._last_sweep = 0.0

    # 上一次运行留下的目录没有元数据可恢复，直接清掉（结果本来就该下载走）。
    if self.jobs_dir.exists():
      for child in self.jobs_dir.iterdir():
        if child.is_dir():
          shutil.rmtree(child, ignore_errors=True)
    self.jobs_dir.mkdir(parents=True, exist_ok=True)

    self._worker = threading.Thread(
        target=self._loop, name='mt3-web-worker', daemon=True)
    self._worker.start()

  # ------------------------------------------------------------------ 提交/查询

  def new_directory(self):
    """给即将上传的任务分一个 id 与目录。"""
    job_id = uuid.uuid4().hex[:12]
    directory = self.jobs_dir / job_id
    directory.mkdir(parents=True, exist_ok=False)
    return job_id, directory

  def submit(self, job: Job):
    """入队；队列满时抛 :class:`QueueFull`。"""
    with self._lock:
      queued = sum(1 for item in self._jobs.values() if item.status == QUEUED)
      if queued >= self.queue_limit:
        raise QueueFull(
            '队列已满（{} 个等待中），等手上的任务完成再传。'.format(
                self.queue_limit))
      self._jobs[job.id] = job
    job.log('已进入队列')
    self._queue.put(job.id)
    self._sweep()
    return job.to_dict()

  def discard(self, job_id):
    """上传失败/被拒时丢掉占位目录。"""
    with self._lock:
      self._jobs.pop(job_id, None)
    directory = self.jobs_dir / job_id
    shutil.rmtree(directory, ignore_errors=True)

  def get(self, job_id):
    with self._lock:
      job = self._jobs.get(job_id)
      if job is None:
        return None
      return job.to_dict(self._positions_locked().get(job_id))

  def path(self, job_id, kind):
    """任务结果的落盘路径（``raw`` 或 ``rearranged``）。"""
    with self._lock:
      job = self._jobs.get(job_id)
      if job is None:
        return None
      return job.raw_path if kind == 'raw' else job.rearranged_path

  def clone_for_retry(self, job_id, model, rearrange):
    """用同一个输入文件再跑一遍（硬链接复用，不复制、不重新上传）。

    Returns:
      新任务的快照（``JobManager.submit`` 的返回值）。

    Raises:
      KeyError: 原任务不存在。
      FileNotFoundError: 输入文件已经被清理。
      QueueFull: 队列满。
    """
    with self._lock:
      source = self._jobs.get(job_id)
      if source is None:
        raise KeyError(job_id)
      input_path = Path(source.input_path)
      filename = source.filename

    if not input_path.exists():
      raise FileNotFoundError(
          '输入文件已被清理（超过保留时间），请重新上传。')

    new_id, directory = self.new_directory()
    target = directory / ('input' + input_path.suffix)
    try:
      # 硬链接：同一个 inode，删掉任何一个任务都不影响另一个。
      os.link(input_path, target)
    except OSError:
      shutil.copyfile(input_path, target)

    job = Job(id=new_id, filename=filename, model=model, directory=directory,
              input_path=target, rearrange=rearrange)
    job.log('从 {} 重新转谱（复用已上传的文件）'.format(job_id))
    try:
      return self.submit(job)
    except BaseException:
      self.discard(new_id)
      raise

  def queued_count(self):
    with self._lock:
      return sum(1 for item in self._jobs.values() if item.status == QUEUED)

  def list(self):
    self._sweep()
    with self._lock:
      positions = self._positions_locked()
      ordered = sorted(
          self._jobs.values(), key=lambda item: item.created, reverse=True)
      return [item.to_dict(positions.get(item.id)) for item in ordered]

  def _positions_locked(self):
    queued = sorted(
        (item for item in self._jobs.values() if item.status == QUEUED),
        key=lambda item: item.created)
    return {item.id: index + 1 for index, item in enumerate(queued)}

  # ------------------------------------------------------------------ 取消/删除

  def cancel(self, job_id):
    with self._lock:
      job = self._jobs.get(job_id)
      if job is None:
        return None
      if job.status == QUEUED:
        job.status = CANCELLED
        job.finished = time.time()
        job.log('已取消（还没开始）')
      elif job.status == RUNNING:
        job.cancel_event.set()
        job.log('正在取消：当前片段结束后停止')
      return job.to_dict(self._positions_locked().get(job_id))

  def delete(self, job_id):
    with self._lock:
      job = self._jobs.get(job_id)
      if job is None:
        return False
      if job.status == RUNNING:
        raise RuntimeError('任务正在运行，请先取消再删除。')
      del self._jobs[job_id]
    shutil.rmtree(job.directory, ignore_errors=True)
    return True

  # ------------------------------------------------------------------ worker

  def update_progress(self, job, done, total):
    """worker 的进度回调（也负责检查取消）。"""
    with self._lock:
      job.done = int(done)
      job.total = int(total) if total else None
      cancelled = job.cancel_event.is_set()
    if cancelled:
      raise JobCancelled()

  def _loop(self):
    while True:
      job_id = self._queue.get()
      if job_id is None:  # 关停信号
        return
      with self._lock:
        job = self._jobs.get(job_id)
        if job is None or job.status != QUEUED:
          continue
        if job.cancel_event.is_set():
          job.status = CANCELLED
          job.finished = time.time()
          continue
        job.status = RUNNING
        job.started = time.time()
      job.log('开始转谱')
      try:
        self._runner(job, self)
      except JobCancelled:
        with self._lock:
          job.status = CANCELLED
          job.finished = time.time()
        job.log('已取消（原始 MIDI 未生成）')
      except Exception as exc:  # noqa: BLE001 - 任务失败要回报给页面
        with self._lock:
          job.status = FAILED
          job.finished = time.time()
          job.error = str(exc)
        job.log('失败: {}'.format(exc))
        for line in traceback.format_exc(limit=4).strip().splitlines():
          job.log(line)
      else:
        with self._lock:
          job.status = DONE
          job.finished = time.time()
        job.log('完成')

  # ------------------------------------------------------------------ 清理

  def _sweep(self):
    now = time.time()
    if now - self._last_sweep < 60:
      return
    self._last_sweep = now
    horizon = now - self.keep_hours * 3600
    with self._lock:
      stale = [
          item for item in self._jobs.values()
          if item.status in FINAL_STATUSES
          and (item.finished or item.created) < horizon
      ]
      for item in stale:
        del self._jobs[item.id]
    for item in stale:
      shutil.rmtree(item.directory, ignore_errors=True)


def midi_roll(path, max_notes=400):
  """把 MIDI 压成给页面画钢琴卷的小数组：``[[x, y, w], ...]``，都是 0..1。

  x = 起点 / 总时长，y = 音高（21..108 归一到 0..1），w = 时值 / 总时长。
  纯预览用：解析失败或没有音符就返回空列表。
  """
  try:
    import mido
    mid = mido.MidiFile(str(path))
  except Exception:  # noqa: BLE001 - 预览而已，失败就不画
    return []

  notes = []
  for track in mid.tracks:
    tick = 0
    active = {}
    for message in track:
      tick += message.time
      if message.type == 'note_on' and message.velocity > 0:
        active[(message.channel, message.note)] = tick
      elif message.type in ('note_off', 'note_on'):
        start = active.pop((message.channel, message.note), None)
        if start is not None and tick > start:
          notes.append((start, tick, message.note))
  if not notes:
    return []

  end = max(item[1] for item in notes)
  if end <= 0:
    return []
  if len(notes) > max_notes:
    step = len(notes) / float(max_notes)
    notes = [notes[int(index * step)] for index in range(max_notes)]

  roll = []
  for start, stop, pitch in notes:
    roll.append([
        round(start / end, 3),
        round(max(0.0, min(1.0, (pitch - 21) / 87.0)), 3),
        round(max(0.002, min(1.0, (stop - start) / end)), 3),
    ])
  return roll


def make_runner(config):
  """返回真正跑模型的 runner；``backend`` 在这里才导入（自检不需要它）。"""

  def runner(job, manager):
    import backend
    import midi_rearrange

    transcriber = backend.get_transcriber(
        device=config.device, batch_size=config.batch_size)

    if manager.keep_models <= 1 and job.model not in transcriber.loaded_models:
      transcriber.unload_models()

    job.log('推理设备: {}'.format(backend.device_summary()))
    if job.model not in transcriber.loaded_models:
      job.log('加载模型 {}（首次较慢）…'.format(job.model))
      transcriber.get_model(job.model)
      job.log('模型就绪')
    if job.cancel_event.is_set():
      raise JobCancelled()

    logged = [-1]

    def progress(done, total):
      manager.update_progress(job, done, total)
      if total:
        percent = int(100 * done / total)
        if percent >= logged[0] + 10:
          logged[0] = percent
          job.log('转谱进度: {}/{} ({}%)'.format(done, total, percent))

    note_sequence = transcriber.transcribe_file(
        job.input_path, job.model, progress_cb=progress)

    raw = job.directory / 'out.mid'
    backend.save_midi(note_sequence, raw)
    job.raw_path = raw
    job.notes = len(note_sequence.notes)
    job.roll = midi_roll(raw)
    job.log('原始 MIDI: {}（{} 个音符）'.format(raw.name, job.notes))

    if job.rearrange:
      job.log('整理 MIDI…')
      result = midi_rearrange.rearrange_file(
          raw, job.directory / 'out.rearranged.mid', **job.rearrange)
      job.rearranged_path = result.path
      job.rearrange_detail = result.detail()
      job.log('整理版 MIDI: {}'.format(result.path.name))

  return runner
