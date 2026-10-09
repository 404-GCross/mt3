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

"""MT3 扒谱工具：选择音频文件即可转成 MIDI。

GUI 模式（默认）::

    python gui/app.py

命令行模式（适合在无显示的服务器上先验证环境）::

    python gui/app.py --cli 音频.mp3 -o 输出.mid --model mt3
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parent
REPO_ROOT = GUI_DIR.parent
sys.path.insert(0, str(GUI_DIR))

AUDIO_FILE_TYPES = [
    ('音频文件', '*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.aiff *.aif *.opus'),
    ('WAV', '*.wav'),
    ('MP3', '*.mp3'),
    ('所有文件', '*.*'),
]

MODEL_CHOICES = [
    ('ismir2021', '钢琴（ISMIR2021，含力度）'),
    ('mt3', '多乐器（MT3：钢琴/吉他/贝斯/鼓等）'),
]

DEVICE_CHOICES = [
    ('auto', '自动（优先 NVIDIA GPU）'),
    ('cuda', 'NVIDIA GPU'),
    ('cpu', 'CPU'),
]

CHECKPOINT_DIR = Path(
    os.environ.get('MT3_CHECKPOINT_DIR', str(REPO_ROOT / 'checkpoints')))
CONFIG_DIR = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'mt3-transcriber'
CONFIG_FILE = CONFIG_DIR / 'config.json'


def load_config():
  try:
    return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
  except (OSError, ValueError):
    return {}


def save_config(values):
  CONFIG_DIR.mkdir(parents=True, exist_ok=True)
  CONFIG_FILE.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')


def open_path(path):
  """Open a file or directory with the platform's default application."""
  path = str(path)
  if sys.platform.startswith('darwin'):
    subprocess.Popen(['open', path])
  elif os.name == 'nt':
    os.startfile(path)  # type: ignore[attr-defined]
  else:
    subprocess.Popen(['xdg-open', path])


def missing_checkpoints(directory=CHECKPOINT_DIR):
  """Model types whose checkpoint has not been downloaded yet."""
  missing = []
  for model_type, _ in MODEL_CHOICES:
    if not (Path(directory) / model_type / 'checkpoint').exists():
      missing.append(model_type)
  return missing


def _venv_python_candidates():
  """Interpreters used by run.sh / install.sh, in priority order."""
  candidates = []
  env_dir = os.environ.get('MT3_VENV_DIR')
  if env_dir:
    candidates.append(Path(env_dir) / 'bin' / 'python')
  candidates.append(Path.home() / '.local/share/mt3-transcriber/venv/bin/python')
  candidates.append(REPO_ROOT / '.venv-mt3gui/bin/python')
  return candidates


def ensure_supported_interpreter():
  """Hand over to the private virtualenv when this Python lacks MT3 deps.

  Desktop launchers already use the virtualenv, but a user (or file manager)
  may start ``app.py`` with the system Python, which has Tkinter but not
  TensorFlow/JAX.  In that case restart inside the prepared virtualenv instead
  of failing later with a confusing ``ModuleNotFoundError``.
  """
  if os.environ.get('MT3_BOOTSTRAPPED') == '1':
    return
  import importlib.util

  if (importlib.util.find_spec('tensorflow') is not None
      and importlib.util.find_spec('jax') is not None):
    return

  current = Path(sys.executable).resolve()
  for candidate in _venv_python_candidates():
    if not candidate.exists():
      continue
    try:
      if candidate.resolve() == current:
        continue
    except OSError:
      continue
    os.environ['MT3_BOOTSTRAPPED'] = '1'
    print(f'[mt3] 使用虚拟环境解释器: {candidate}', file=sys.stderr)
    os.execv(str(candidate),
             [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]])


class Mt3App:
  """Tkinter desktop UI."""

  def __init__(self):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    self.tk = tk
    self.ttk = ttk
    self.filedialog = filedialog
    self.messagebox = messagebox
    self.queue = queue.Queue()
    self.busy = False
    self._prompted_download = False
    self.last_output = None
    self.config = load_config()
    self.checkpoint_dir = Path(self.config.get('checkpoint_dir', CHECKPOINT_DIR))

    root = tk.Tk()
    self.root = root
    root.title('MT3 扒谱工具')
    root.minsize(640, 460)

    self.model_var = tk.StringVar(value=self.config.get('model', 'mt3'))
    self.device_var = tk.StringVar(value=self.config.get('device', 'auto'))
    self.batch_var = tk.StringVar(value=str(self.config.get('batch', 8)))
    self.checkpoint_var = tk.StringVar(value=str(self.checkpoint_dir))
    self.input_var = tk.StringVar()
    self.output_var = tk.StringVar()
    self.status_var = tk.StringVar(value='就绪')

    main = ttk.Frame(root, padding=10)
    main.pack(fill='both', expand=True)
    main.columnconfigure(1, weight=1)

    row = 0
    ttk.Label(main, text='模型：').grid(row=row, column=0, sticky='w')
    model_frame = ttk.Frame(main)
    model_frame.grid(row=row, column=1, columnspan=2, sticky='w')
    for model_type, label in MODEL_CHOICES:
      ttk.Radiobutton(
          model_frame, text=label, value=model_type,
          variable=self.model_var).pack(side='left', padx=(0, 12))
    row += 1

    ttk.Label(main, text='模型权重目录：').grid(row=row, column=0, sticky='w')
    ttk.Entry(main, textvariable=self.checkpoint_var).grid(row=row, column=1, sticky='ew', padx=(0, 6))
    ttk.Button(main, text='选择目录…', command=self.on_browse_checkpoint).grid(row=row, column=2, sticky='ew')
    row += 1
    self.model_status_var = tk.StringVar()
    ttk.Button(main, text='下载/刷新模型', command=self.on_download_models).grid(row=row, column=0, sticky='w')
    ttk.Label(main, textvariable=self.model_status_var).grid(row=row, column=1, columnspan=2, sticky='w')
    row += 1

    ttk.Label(main, text='设备：').grid(row=row, column=0, sticky='w')
    ttk.Combobox(
        main, textvariable=self.device_var,
        values=[label for _, label in DEVICE_CHOICES], state='readonly',
        width=28).grid(row=row, column=1, sticky='w')
    row += 1

    ttk.Label(main, text='Batch Size：').grid(row=row, column=0, sticky='w')
    ttk.Spinbox(main, from_=1, to=128, textvariable=self.batch_var, width=8).grid(row=row, column=1, sticky='w')
    row += 1

    ttk.Label(main, text='音频文件：').grid(row=row, column=0, sticky='w')
    ttk.Entry(main, textvariable=self.input_var).grid(
        row=row, column=1, sticky='ew', padx=(0, 6))
    ttk.Button(main, text='浏览…', command=self.on_browse_input).grid(
        row=row, column=2, sticky='ew')
    row += 1

    ttk.Label(main, text='输出 MIDI：').grid(row=row, column=0, sticky='w')
    ttk.Entry(main, textvariable=self.output_var).grid(
        row=row, column=1, sticky='ew', padx=(0, 6))
    ttk.Button(main, text='另存为…', command=self.on_browse_output).grid(
        row=row, column=2, sticky='ew')
    row += 1

    button_frame = ttk.Frame(main)
    button_frame.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(12, 6))
    self.start_button = ttk.Button(
        button_frame, text='开始转谱', command=self.on_start)
    self.start_button.pack(side='left')
    self.open_dir_button = ttk.Button(
        button_frame, text='打开输出目录', command=self.on_open_dir,
        state='disabled')
    self.open_dir_button.pack(side='left', padx=(8, 0))
    self.open_midi_button = ttk.Button(
        button_frame, text='打开 MIDI', command=self.on_open_midi,
        state='disabled')
    self.open_midi_button.pack(side='left', padx=(8, 0))
    row += 1

    self.progress = ttk.Progressbar(main, mode='determinate', maximum=100)
    self.progress.grid(row=row, column=0, columnspan=3, sticky='ew')
    row += 1

    ttk.Label(main, textvariable=self.status_var, anchor='w').grid(
        row=row, column=0, columnspan=3, sticky='ew', pady=(4, 6))
    row += 1

    log_frame = ttk.LabelFrame(main, text='日志')
    log_frame.grid(row=row, column=0, columnspan=3, sticky='nsew')
    main.rowconfigure(row, weight=1)
    log_frame.rowconfigure(0, weight=1)
    log_frame.columnconfigure(0, weight=1)
    self.log_text = tk.Text(log_frame, height=10, wrap='word', state='disabled')
    self.log_text.grid(row=0, column=0, sticky='nsew')
    scrollbar = ttk.Scrollbar(
        log_frame, orient='vertical', command=self.log_text.yview)
    scrollbar.grid(row=0, column=1, sticky='ns')
    self.log_text.configure(yscrollcommand=scrollbar.set)

    self.input_var.trace_add('write', self.on_input_changed)

    self.refresh_model_status()
    if missing_checkpoints(self.checkpoint_dir):
      self.log('尚未下载模型权重，可点击“下载/刷新模型”联网获取（约 340MB）。')
    self.log(f'模型权重目录: {CHECKPOINT_DIR}')

    self.root.after(100, self.poll_queue)
    self.root.after(400, self.prompt_download_if_needed)

  # ------------------------------------------------------------------ UI 事件

  def refresh_model_status(self):
    missing = missing_checkpoints(Path(self.checkpoint_var.get()))
    installed = [m for m, _ in MODEL_CHOICES if m not in missing]
    text = '已安装：' + (', '.join(installed) or '无')
    if missing:
      text += '；缺少：' + ', '.join(missing)
    self.model_status_var.set(text)

  def persist_config(self):
    try:
      batch = max(1, int(self.batch_var.get()))
    except ValueError:
      batch = 8
      self.batch_var.set('8')
    save_config({'model': self.model_var.get(), 'device': self.device_var.get(),
                 'batch': batch, 'checkpoint_dir': self.checkpoint_var.get()})

  def on_browse_checkpoint(self):
    path = self.filedialog.askdirectory(title='选择模型权重目录', initialdir=self.checkpoint_var.get())
    if path:
      self.checkpoint_var.set(path)
      self.refresh_model_status()
      self.persist_config()

  def prompt_download_if_needed(self):
    if self._prompted_download or self.busy:
      return
    self._prompted_download = True
    missing = missing_checkpoints(Path(self.checkpoint_var.get()))
    if not missing:
      return
    if self.messagebox.askyesno(
        '缺少模型权重',
        '尚未下载模型权重：' + ', '.join(missing)
        + '\n\n现在联网下载吗？（约 340MB，之后可在界面里手动下载）'):
      self.on_download_models()

  def on_download_models(self):
    if self.busy:
      return
    directory = Path(self.checkpoint_var.get())
    missing = missing_checkpoints(directory) or [m for m, _ in MODEL_CHOICES]
    self.busy = True
    self.start_button.configure(state='disabled')
    self.status_var.set('正在下载模型…')
    self.log('开始下载模型到: ' + str(directory))
    def download():
      try:
        import download_checkpoints
        download_checkpoints.download_models(
            directory, missing,
            progress=lambda text: self.queue.put(('log', text)))
        self.queue.put(('models_done',))
      except Exception as exc:
        self.queue.put(('error', f'{type(exc).__name__}: {exc}', traceback.format_exc()))
    threading.Thread(target=download, daemon=True).start()

  def on_browse_input(self):
    path = self.filedialog.askopenfilename(
        title='选择音频文件', filetypes=AUDIO_FILE_TYPES)
    if path:
      self.input_var.set(path)

  def on_browse_output(self):
    initial = Path(self.output_var.get()).name if self.output_var.get() else ''
    path = self.filedialog.asksaveasfilename(
        title='保存 MIDI', defaultextension='.mid',
        initialfile=initial,
        filetypes=[('MIDI 文件', '*.mid *.midi'), ('所有文件', '*.*')])
    if path:
      self.output_var.set(path)

  def on_input_changed(self, *_args):
    audio = self.input_var.get().strip()
    if audio:
      self.output_var.set(str(Path(audio).with_suffix('.mid')))

  def on_open_dir(self):
    if self.last_output:
      open_path(Path(self.last_output).parent)

  def on_open_midi(self):
    if self.last_output and Path(self.last_output).exists():
      open_path(self.last_output)

  def on_start(self):
    if self.busy:
      return

    audio = self.input_var.get().strip()
    output = self.output_var.get().strip()
    model_type = self.model_var.get()

    if not audio or not Path(audio).exists():
      self.messagebox.showerror('错误', '请选择有效的音频文件。')
      return
    if not output:
      self.messagebox.showerror('错误', '请指定输出 MIDI 路径。')
      return
    if Path(output).exists() and not self.messagebox.askyesno(
        '覆盖确认', f'文件已存在，是否覆盖？\n{output}'):
      return

    self.persist_config()
    missing = missing_checkpoints(Path(self.checkpoint_var.get()))
    if model_type in missing:
      if self.messagebox.askyesno(
          '缺少模型权重',
          f'{model_type} 的权重尚未下载。\n\n现在联网下载吗？'):
        self.on_download_models()
      return

    self.busy = True
    self.start_button.configure(state='disabled')
    self.open_dir_button.configure(state='disabled')
    self.open_midi_button.configure(state='disabled')
    self.set_progress(0, None)
    self.status_var.set('准备中…')
    self.log('=' * 60)
    self.log(f'模型: {dict(MODEL_CHOICES)[model_type]}')
    self.log(f'输入: {audio}')
    self.log(f'输出: {output}')

    thread = threading.Thread(
        target=self.worker, args=(audio, output, model_type), daemon=True)
    thread.start()

  # ------------------------------------------------------------------ 后台线程

  def worker(self, audio, output, model_type):
    q = self.queue
    start = time.time()
    try:
      q.put(('status', '正在加载依赖与模型（首次较慢，请耐心等待）…'))
      import backend  # 延迟导入，加快窗口启动

      q.put(('log', f'推理设备: {backend.device_summary()}'))
      device = self.device_var.get()
      device = dict((label, value) for value, label in DEVICE_CHOICES).get(
          device, device)
      transcriber = backend.get_transcriber(
          device=device, batch_size=max(1, int(self.batch_var.get())))

      def progress_cb(done, total):
        q.put(('progress', done, total))

      q.put(('status', f'正在转谱（{model_type}）…'))
      note_sequence = transcriber.transcribe_file(
          audio, model_type, progress_cb=progress_cb)

      q.put(('status', '正在写入 MIDI…'))
      backend.save_midi(note_sequence, output)

      notes = [n for n in note_sequence.notes if not n.is_drum]
      drums = [n for n in note_sequence.notes if n.is_drum]
      elapsed = time.time() - start
      q.put(('done', output, len(notes), len(drums), elapsed))
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI.
      q.put(('error', f'{type(exc).__name__}: {exc}', traceback.format_exc()))

  # ------------------------------------------------------------------ 队列轮询

  def poll_queue(self):
    try:
      while True:
        message = self.queue.get_nowait()
        self.handle_message(message)
    except queue.Empty:
      pass
    self.root.after(100, self.poll_queue)

  def handle_message(self, message):
    kind = message[0]
    if kind == 'status':
      self.status_var.set(message[1])
    elif kind == 'models_done':
      self.busy = False
      self.start_button.configure(state='normal')
      self.refresh_model_status()
      self.persist_config()
      self.status_var.set('模型已更新')
      self.log('模型下载完成。')
    elif kind == 'log':
      self.log(message[1])
    elif kind == 'progress':
      done, total = message[1], message[2]
      self.set_progress(done, total)
      if total:
        self.status_var.set(f'正在转谱… {done}/{total} 个片段')
      else:
        self.status_var.set(f'正在转谱… 已处理 {done} 个片段')
    elif kind == 'done':
      _, output, num_notes, num_drums, elapsed = message
      self.finish()
      self.last_output = output
      self.open_dir_button.configure(state='normal')
      self.open_midi_button.configure(state='normal')
      self.status_var.set(f'完成：{num_notes} 个音符')
      if num_drums:
        self.log(f'完成：旋律/和声音符 {num_notes} 个，鼓音符 {num_drums} 个，'
                 f'耗时 {elapsed:.1f} 秒')
      else:
        self.log(f'完成：{num_notes} 个音符，耗时 {elapsed:.1f} 秒')
      self.log(f'MIDI 已保存: {output}')
      self.messagebox.showinfo(
          '转谱完成',
          f'已生成 MIDI：\n{output}\n\n音符数：{num_notes}'
          f'{" + 鼓 " + str(num_drums) if num_drums else ""}\n'
          f'耗时：{elapsed:.1f} 秒')
    elif kind == 'error':
      _, summary, details = message
      self.finish()
      self.status_var.set('出错了')
      self.log(f'错误: {summary}')
      self.log(details)
      self.messagebox.showerror('转谱失败', summary)

  # ------------------------------------------------------------------ 小工具

  def finish(self):
    self.busy = False
    self.start_button.configure(state='normal')
    self.progress.stop()
    self.progress.configure(mode='determinate')

  def set_progress(self, done, total):
    if total:
      if str(self.progress.cget('mode')) != 'determinate':
        self.progress.stop()
        self.progress.configure(mode='determinate')
      self.progress.configure(maximum=total, value=min(done, total))
    else:
      if str(self.progress.cget('mode')) != 'indeterminate':
        self.progress.configure(mode='indeterminate')
        self.progress.start(12)

  def log(self, text):
    self.log_text.configure(state='normal')
    self.log_text.insert('end', text + '\n')
    self.log_text.see('end')
    self.log_text.configure(state='disabled')

  def run(self):
    self.root.mainloop()


def run_cli(args):
  """Headless transcription for testing on a remote machine."""
  import backend

  transcriber = backend.get_transcriber(device=args.device, batch_size=args.batch)
  print(f'推理设备: {backend.device_summary()}')

  last = [-1]

  def progress_cb(done, total):
    if total:
      percent = int(100 * done / total)
      if percent != last[0]:
        last[0] = percent
        print(f'\r转谱进度: {done}/{total} ({percent}%)', end='', flush=True)
    else:
      print(f'\r已处理 {done} 个片段', end='', flush=True)

  start = time.time()
  note_sequence = transcriber.transcribe_file(
      args.cli, args.model, progress_cb=progress_cb)
  print()

  output = args.output or str(Path(args.cli).with_suffix('.mid'))
  backend.save_midi(note_sequence, output)
  notes = [n for n in note_sequence.notes if not n.is_drum]
  drums = [n for n in note_sequence.notes if n.is_drum]
  print(f'完成: {len(notes)} 个音符'
        + (f' + {len(drums)} 个鼓音符' if drums else '')
        + f'，耗时 {time.time() - start:.1f} 秒')
  print(f'MIDI 已保存: {output}')


def run_batch(args):
  """Transcribe every supported audio file in a directory."""
  import backend

  input_dir = Path(args.batch_input)
  output_dir = Path(args.batch_output)
  output_dir.mkdir(parents=True, exist_ok=True)
  extensions = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac',
                '.aiff', '.aif', '.opus'}
  files = sorted(p for p in input_dir.iterdir()
                 if p.is_file() and p.suffix.lower() in extensions)
  if not files:
    raise FileNotFoundError(f'目录中没有支持的音频文件: {input_dir}')
  transcriber = backend.get_transcriber(
      device=args.device, batch_size=args.batch)
  print(f'设备: {backend.device_summary()}，共 {len(files)} 个文件')
  for index, audio_path in enumerate(files, 1):
    output = output_dir / f'{audio_path.stem}.mid'
    print(f'[{index}/{len(files)}] {audio_path.name}')
    ns = transcriber.transcribe_file(audio_path, args.model)
    backend.save_midi(ns, output)
    print(f'  -> {output}')


def main():
  ensure_supported_interpreter()
  parser = argparse.ArgumentParser(
      description='MT3 扒谱工具：音频转 MIDI（钢琴/多乐器）')
  parser.add_argument(
      '--cli', metavar='音频文件',
      help='命令行模式：转写指定音频文件（无界面）')
  parser.add_argument('-o', '--output', help='输出 MIDI 路径（默认同名 .mid）')
  parser.add_argument(
      '--model', choices=[m for m, _ in MODEL_CHOICES], default='mt3',
      help='模型：ismir2021=钢琴，mt3=多乐器（默认）')
  parser.add_argument(
      '--device', choices=['auto', 'cuda', 'cpu'], default='auto',
      help='设备：auto/cuda/cpu（默认 auto）')
  parser.add_argument(
      '--batch', type=int, default=8, metavar='N',
      help='推理 batch size（默认 8）')
  parser.add_argument('--batch-input', metavar='DIR',
                      help='批量模式输入目录')
  parser.add_argument('--batch-output', metavar='DIR',
                      help='批量模式输出目录')
  args = parser.parse_args()

  if args.batch_input:
    if not args.batch_output:
      parser.error('--batch-input 必须同时指定 --batch-output')
    run_batch(args)
    return
  if args.cli:
    run_cli(args)
    return

  import importlib.util

  if importlib.util.find_spec('tkinter') is None:
    print('未安装 Tkinter，无法启动图形界面。', file=sys.stderr)
    print('Debian/Ubuntu: sudo apt install python3-tk', file=sys.stderr)
    print('Fedora:        sudo dnf install python3-tkinter', file=sys.stderr)
    print('或使用命令行模式: python gui/app.py --cli 音频.mp3', file=sys.stderr)
    sys.exit(1)

  Mt3App().run()


if __name__ == '__main__':
  main()
