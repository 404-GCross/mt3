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

转谱结果可选做一次「后处理」（规则化 MIDI 整理，见 gui/midi_rearrange.py）::

    python gui/app.py --cli 音频.mp3 --rearrange
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

# 后处理（MIDI 整理）默认值。阈值单位是毫秒，与 ppq/tempo 无关。
REARRANGE_FIELDS = [
    ('velocity', '最低力度', '0-127'),
    ('min_ms', '最低时值', 'ms'),
    ('chord_ms', '琶音整合', 'ms'),
    ('repeat_ms', '连续消除', 'ms'),
]

REARRANGE_DEFAULTS = {
    'enabled': False,
    'velocity': 30,
    'min_ms': 30,
    'chord_ms': 60,
    'repeat_ms': 120,
    'drums': False,
}

REARRANGE_HINT = '后处理结果另存为 xxx.rearranged.mid，不覆盖原始转谱文件。'

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


# 常见的中文字体族，按优先级排列；不同发行版包名/字体名略有差异。
CJK_FONT_CANDIDATES = (
    'Noto Sans CJK SC', 'Noto Sans CJK', 'Noto Sans SC',
    'Source Han Sans SC', 'Source Han Sans CN',
    'WenQuanYi Zen Hei', 'WenQuanYi Micro Hei', 'WQY Zen Hei',
    'Sarasa Gothic SC', 'Sarasa Term SC',
    'Droid Sans Fallback', 'AR PL UMing CN', 'AR PL UKai CN',
    'Microsoft YaHei', 'PingFang SC', 'Heiti SC', 'SimHei',
)


def configure_cjk_fonts(root):
  """Point Tk's default fonts at an installed CJK family.

  The Tk bundled with the standalone Python does not always fall back to a
  CJK font, which leaves Chinese labels blank (only ASCII and ``/`` visible).
  Returns the chosen family, or None when no CJK font is installed.
  """
  import tkinter.font as tkfont

  families = set(tkfont.families(root))
  override = os.environ.get('MT3_UI_FONT')
  if override:
    chosen = override
  else:
    chosen = next((name for name in CJK_FONT_CANDIDATES if name in families), None)
    if chosen is None:
      for name in sorted(families):
        lowered = name.lower()
        if any(key in lowered for key in
               ('cjk', 'wenquanyi', 'wqy', 'sarasa', 'source han',
                'noto sans sc', 'yahei', 'heiti')):
          chosen = name
          break
  if chosen is None:
    return None

  for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont',
               'TkCaptionFont', 'TkSmallCaptionFont', 'TkIconFont',
               'TkTooltipFont'):
    try:
      tkfont.nametofont(name).configure(family=chosen)
    except tk.TclError:
      pass
  # 让 ttk 控件也跟随 TkDefaultFont（部分主题会自带固定字体）。
  try:
    import tkinter.ttk as ttk
    ttk.Style(root).configure('.', font='TkDefaultFont')
  except tk.TclError:
    pass
  return chosen


def check_fonts():
  """Headless smoke test that Tk can actually see a CJK font.

  Used by CI against the packaged AppImage to catch the "Chinese labels are
  blank" regression (bundled Tk without Xft, or a missing bundled font).
  Returns a process exit code.
  """
  import importlib.util

  if importlib.util.find_spec('tkinter') is None:
    print('FAIL: tkinter is not available', file=sys.stderr)
    return 1
  import tkinter as tk
  import tkinter.font as tkfont

  root = tk.Tk()
  root.withdraw()
  chosen = configure_cjk_fonts(root)
  families = sorted(tkfont.families(root))
  default_font = tkfont.nametofont('TkDefaultFont')
  width = default_font.measure('中文测试')

  print(f'tk default family: {default_font.actual("family")}')
  print(f'cjk family chosen: {chosen}')
  print(f'measure("中文测试") = {width}')
  if chosen is None:
    print('FAIL: no CJK font family is visible to Tk', file=sys.stderr)
    print('first families: ' + ', '.join(families[:40]), file=sys.stderr)
    return 1
  if width <= 0:
    print('FAIL: CJK text renders with zero width', file=sys.stderr)
    return 1
  print('OK: CJK font available to Tk')
  return 0


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
    self.last_rearranged = None
    self.config = load_config()
    self.checkpoint_dir = Path(self.config.get('checkpoint_dir', CHECKPOINT_DIR))

    root = tk.Tk()
    self.root = root
    self.cjk_font = configure_cjk_fonts(root)
    root.title('MT3 扒谱工具')
    root.minsize(640, 460)

    self.model_var = tk.StringVar(value=self.config.get('model', 'mt3'))
    self.device_var = tk.StringVar(value=self.config.get('device', 'auto'))
    self.batch_var = tk.StringVar(value=str(self.config.get('batch', 8)))
    self.checkpoint_var = tk.StringVar(value=str(self.checkpoint_dir))
    self.input_var = tk.StringVar()
    self.output_var = tk.StringVar()
    self.status_var = tk.StringVar(value='就绪')

    # 后处理（MIDI 整理）设置，从上次的配置恢复。
    self.rearrange_enabled = tk.BooleanVar(value=bool(
        self.config.get('rearrange_enabled', REARRANGE_DEFAULTS['enabled'])))
    self.rearrange_drums = tk.BooleanVar(value=bool(
        self.config.get('rearrange_drums', REARRANGE_DEFAULTS['drums'])))
    self.rearrange_vars = {}
    for key, _, _ in REARRANGE_FIELDS:
      self.rearrange_vars[key] = tk.StringVar(
          value=str(self.config.get(f'rearrange_{key}', REARRANGE_DEFAULTS[key])))

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

    post = ttk.LabelFrame(main, text='后处理（MIDI 整理）')
    post.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(8, 0))
    row += 1

    head = ttk.Frame(post)
    head.grid(row=0, column=0, columnspan=2, sticky='w', padx=10, pady=(4, 2))
    ttk.Checkbutton(head, text='启用（转谱后自动整理）',
                    variable=self.rearrange_enabled).pack(side='left')
    ttk.Checkbutton(head, text='包含鼓轨（默认跳过）',
                    variable=self.rearrange_drums).pack(side='left', padx=(14, 0))

    for index, (key, label, unit) in enumerate(REARRANGE_FIELDS):
      cell = ttk.Frame(post)
      cell.grid(row=1 + index // 2, column=index % 2, sticky='w',
                padx=(10, 10), pady=(0, 2))
      ttk.Label(cell, text=f'{label}（{unit}）').pack(side='left')
      ttk.Entry(cell, textvariable=self.rearrange_vars[key], width=7).pack(
          side='left', padx=(4, 0))

    actions = ttk.Frame(post)
    actions.grid(row=3, column=0, columnspan=2, sticky='w', padx=10, pady=(4, 6))
    ttk.Button(actions, text='恢复默认',
               command=self.on_reset_rearrange).pack(side='left')
    self.rearrange_button = ttk.Button(
        actions, text='整理当前 MIDI…', command=self.on_rearrange_current,
        state='disabled')
    self.rearrange_button.pack(side='left', padx=(8, 0))
    self.open_rearranged_button = ttk.Button(
        actions, text='打开整理版', command=self.on_open_rearranged,
        state='disabled')
    self.open_rearranged_button.pack(side='left', padx=(8, 0))
    ttk.Label(post, text=REARRANGE_HINT).grid(
        row=4, column=0, columnspan=2, sticky='w', padx=10, pady=(0, 6))

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
    self.log('界面字体: ' + (self.cjk_font or '(未找到中文字体)'))
    if self.cjk_font is None:
      self.root.after(300, self.warn_missing_cjk_font)

    self.root.after(100, self.poll_queue)
    self.root.after(400, self.prompt_download_if_needed)

  # ------------------------------------------------------------------ UI 事件

  def warn_missing_cjk_font(self):
    """Tell the user to install a CJK font when Chinese text stays blank."""
    self.messagebox.showwarning(
        '缺少中文字体 / Missing CJK font',
        '未找到可用的中文字体，界面里中文可能显示为空白。\n\n'
        '请安装任一中文字体后重新打开：\n'
        '  Fedora:  sudo dnf install google-noto-sans-cjk-fonts\n'
        '  Debian:  sudo apt install fonts-noto-cjk\n'
        '  Arch:    sudo pacman -S noto-fonts-cjk\n'
        '  openSUSE: sudo zypper install noto-sans-cjk-fonts\n\n'
        'No CJK font found; Chinese labels may be blank.')

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
    values = {'model': self.model_var.get(), 'device': self.device_var.get(),
              'batch': batch, 'checkpoint_dir': self.checkpoint_var.get(),
              'rearrange_enabled': bool(self.rearrange_enabled.get()),
              'rearrange_drums': bool(self.rearrange_drums.get())}
    for key, value in self.rearrange_params().items():
      if key != 'drums':
        values[f'rearrange_{key}'] = value
    save_config(values)

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

  # ------------------------------------------------------------------ 后处理

  def rearrange_params(self):
    """读取后处理参数；非法输入回退到默认值并把界面纠正回合法值。"""
    limits = {'velocity': (0, 127), 'min_ms': (0, 10000),
              'chord_ms': (0, 10000), 'repeat_ms': (0, 10000)}
    values = {}
    for key, (low, high) in limits.items():
      try:
        value = int(float(self.rearrange_vars[key].get().strip()))
      except ValueError:
        value = REARRANGE_DEFAULTS[key]
      value = max(low, min(high, value))
      self.rearrange_vars[key].set(str(value))
      values[key] = value
    values['drums'] = bool(self.rearrange_drums.get())
    return values

  def on_reset_rearrange(self):
    for key, _, _ in REARRANGE_FIELDS:
      self.rearrange_vars[key].set(str(REARRANGE_DEFAULTS[key]))
    self.rearrange_enabled.set(REARRANGE_DEFAULTS['enabled'])
    self.rearrange_drums.set(REARRANGE_DEFAULTS['drums'])

  def on_open_rearranged(self):
    if self.last_rearranged and Path(self.last_rearranged).exists():
      open_path(self.last_rearranged)

  def on_rearrange_current(self):
    """对上一次转谱结果再整理一次，不必重新转谱。"""
    if self.busy:
      return
    target = self.last_output or self.output_var.get().strip()
    if not target or not Path(target).exists():
      self.messagebox.showerror('错误', '还没有可整理的 MIDI，请先转谱。')
      return
    params = self.rearrange_params()
    self.persist_config()
    self.busy = True
    self.rearrange_button.configure(state='disabled')
    self.set_progress(0, None)
    self.status_var.set('正在整理 MIDI…')
    self.log(f'整理: {target}')

    def work():
      try:
        import midi_rearrange
        self.queue.put(('rearrange_done',
                        midi_rearrange.rearrange_file(target, **params)))
      except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        self.queue.put(('error', f'{type(exc).__name__}: {exc}',
                        traceback.format_exc()))

    threading.Thread(target=work, daemon=True).start()

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
    self.rearrange_button.configure(state='disabled')
    self.set_progress(0, None)
    self.status_var.set('准备中…')
    self.log('=' * 60)
    self.log(f'模型: {dict(MODEL_CHOICES)[model_type]}')
    self.log(f'输入: {audio}')
    self.log(f'输出: {output}')

    # 后处理参数在主线程里读好，再交给工作线程（tk 变量不能跨线程访问）。
    rearrange_params = None
    if self.rearrange_enabled.get():
      rearrange_params = self.rearrange_params()
      self.log('后处理: 已启用（' + '，'.join(
          f'{label}={rearrange_params[key]}' for key, label, _ in REARRANGE_FIELDS)
          + ('，含鼓轨' if rearrange_params['drums'] else '') + '）')

    thread = threading.Thread(
        target=self.worker, args=(audio, output, model_type, rearrange_params),
        daemon=True)
    thread.start()

  # ------------------------------------------------------------------ 后台线程

  def worker(self, audio, output, model_type, rearrange_params=None):
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

      rearranged = None
      if rearrange_params is not None:
        q.put(('status', '正在整理 MIDI（后处理）…'))
        try:
          import midi_rearrange
        except ImportError as exc:
          raise RuntimeError(
              '缺少 mido 依赖，无法做后处理（pip install mido）') from exc
        rearranged = midi_rearrange.rearrange_file(output, **rearrange_params)

      notes = [n for n in note_sequence.notes if not n.is_drum]
      drums = [n for n in note_sequence.notes if n.is_drum]
      elapsed = time.time() - start
      q.put(('done', output, len(notes), len(drums), elapsed, rearranged))
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
      _, output, num_notes, num_drums, elapsed, rearranged = message
      self.finish()
      self.last_output = output
      self.open_dir_button.configure(state='normal')
      self.open_midi_button.configure(state='normal')
      self.rearrange_button.configure(state='normal')
      self.status_var.set(f'完成：{num_notes} 个音符')
      if num_drums:
        self.log(f'完成：旋律/和声音符 {num_notes} 个，鼓音符 {num_drums} 个，'
                 f'耗时 {elapsed:.1f} 秒')
      else:
        self.log(f'完成：{num_notes} 个音符，耗时 {elapsed:.1f} 秒')
      self.log(f'MIDI 已保存: {output}')
      extra = ''
      if rearranged is not None:
        self.last_rearranged = str(rearranged.path)
        self.open_rearranged_button.configure(state='normal')
        self.log(f'整理版 MIDI: {rearranged.path}')
        self.log('后处理：' + rearranged.detail())
        extra = f'\n\n整理版：\n{rearranged.path}\n{rearranged.summary()}'
      self.messagebox.showinfo(
          '转谱完成',
          f'已生成 MIDI：\n{output}\n\n音符数：{num_notes}'
          f'{" + 鼓 " + str(num_drums) if num_drums else ""}\n'
          f'耗时：{elapsed:.1f} 秒{extra}')
    elif kind == 'rearrange_done':
      result = message[1]
      self.finish()
      self.last_rearranged = str(result.path)
      self.open_rearranged_button.configure(state='normal')
      self.status_var.set('MIDI 整理完成')
      self.log(f'整理版 MIDI: {result.path}')
      self.log('后处理：' + result.detail())
      self.messagebox.showinfo(
          '整理完成', f'已生成：\n{result.path}\n\n{result.summary()}')
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
    ready = bool(self.last_output) and Path(self.last_output).exists()
    self.rearrange_button.configure(state='normal' if ready else 'disabled')

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


def cli_rearrange_params(args):
  """把命令行开关转成 midi_rearrange 的参数。"""
  return {
      'velocity': args.rearrange_velocity,
      'min_ms': args.rearrange_min_ms,
      'chord_ms': args.rearrange_chord_ms,
      'repeat_ms': args.rearrange_repeat_ms,
      'drums': bool(args.rearrange_drums),
  }


def run_rearrange_cli(output, args):
  """整理一个转谱输出并打印统计（命令行模式）。"""
  import midi_rearrange

  result = midi_rearrange.rearrange_file(output, **cli_rearrange_params(args))
  print(f'整理版 MIDI: {result.path}')
  print('后处理：' + result.detail())
  return result


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
  if args.rearrange:
    run_rearrange_cli(output, args)


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
    if args.rearrange:
      run_rearrange_cli(str(output), args)


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
  parser.add_argument('--rearrange', action='store_true',
                      help='转谱后做一次后处理（另存 <输出>.rearranged.mid）')
  parser.add_argument('--rearrange-velocity', type=int,
                      default=REARRANGE_DEFAULTS['velocity'], metavar='N',
                      help=f'后处理：力度阈值，<= 该值的音符删除（默认 {REARRANGE_DEFAULTS["velocity"]}）')
  parser.add_argument('--rearrange-min-ms', type=int,
                      default=REARRANGE_DEFAULTS['min_ms'], metavar='MS',
                      help=f'后处理：最小时值（毫秒，默认 {REARRANGE_DEFAULTS["min_ms"]}）')
  parser.add_argument('--rearrange-chord-ms', type=int,
                      default=REARRANGE_DEFAULTS['chord_ms'], metavar='MS',
                      help=f'后处理：琶音整合窗口（毫秒，默认 {REARRANGE_DEFAULTS["chord_ms"]}）')
  parser.add_argument('--rearrange-repeat-ms', type=int,
                      default=REARRANGE_DEFAULTS['repeat_ms'], metavar='MS',
                      help=f'后处理：连续消除窗口（毫秒，默认 {REARRANGE_DEFAULTS["repeat_ms"]}）')
  parser.add_argument('--rearrange-drums', action='store_true',
                      help='后处理：连打击乐通道一起整理（默认跳过）')
  parser.add_argument('--check-fonts', action='store_true',
                      help=argparse.SUPPRESS)
  args = parser.parse_args()

  if args.check_fonts:
    sys.exit(check_fonts())

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
