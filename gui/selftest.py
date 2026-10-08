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

"""MT3 端到端自检：生成一段合成钢琴琶音并转写成 MIDI。

在目标机上装好依赖和权重后运行::

    .venv-mt3gui/bin/python gui/selftest.py

用于确认「音频 -> 模型 -> MIDI」整条链路可用，不评估转写准确率。
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

GUI_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(GUI_DIR))

SAMPLE_RATE = 16000


def synthesize_test_audio():
  """生成 C 大调琶音（正弦+谐波+衰减包络），约 3.7 秒。"""
  freqs = [261.63, 329.63, 392.00, 523.25]  # C4 E4 G4 C5
  chunks = []
  for freq in freqs:
    duration = 0.8
    t = np.arange(int(duration * SAMPLE_RATE)) / SAMPLE_RATE
    envelope = np.exp(-4.0 * t)
    tone = sum(
        np.sin(2 * math.pi * freq * (harmonic + 1) * t) / (harmonic + 1)
        for harmonic in range(4))
    chunks.append(0.4 * envelope * tone)
  audio = np.concatenate(chunks)
  audio = np.concatenate([audio, np.zeros(SAMPLE_RATE // 2)])
  audio = audio / np.max(np.abs(audio)) * 0.8
  return audio.astype(np.float32)


def write_wav(path, audio, sample_rate=SAMPLE_RATE):
  pcm = np.clip(audio * 32767.0, -32768, 32767).astype('<i2')
  with wave.open(str(path), 'wb') as file:
    file.setnchannels(1)
    file.setsampwidth(2)
    file.setframerate(sample_rate)
    file.writeframes(pcm.tobytes())


def main():
  parser = argparse.ArgumentParser(description='MT3 端到端自检')
  parser.add_argument(
      '--model', choices=['ismir2021', 'mt3'], default='ismir2021',
      help='优先使用的模型（缺失时自动回退到另一个）')
  args = parser.parse_args()

  import backend

  transcriber = backend.get_transcriber()
  model_type = args.model
  if not transcriber.is_available(model_type):
    available = [m for m in backend.MODEL_TYPES
                 if transcriber.is_available(m)]
    if not available:
      print('未找到任何模型权重。请先运行: python gui/download_checkpoints.py',
            file=sys.stderr)
      return 1
    print(f'{model_type} 权重缺失，改用 {available[0]}')
    model_type = available[0]

  print(f'推理设备: {backend.device_summary()}')

  work_dir = Path(tempfile.mkdtemp(prefix='mt3_selftest_'))
  wav_path = work_dir / 'test_arpeggio.wav'
  midi_path = work_dir / 'test_arpeggio.mid'

  audio = synthesize_test_audio()
  write_wav(wav_path, audio)
  print(f'测试音频: {wav_path} ({len(audio) / SAMPLE_RATE:.1f} 秒)')

  start = time.time()
  note_sequence = transcriber.transcribe_file(wav_path, model_type)
  backend.save_midi(note_sequence, midi_path)
  elapsed = time.time() - start

  import pretty_midi

  midi = pretty_midi.PrettyMIDI(str(midi_path))
  num_notes = sum(len(instrument.notes) for instrument in midi.instruments)

  print(f'模型: {model_type}，耗时 {elapsed:.1f} 秒')
  print(f'检测到音符: {num_notes}')
  print(f'MIDI: {midi_path}')

  if not midi_path.exists():
    print('自检失败: 未生成 MIDI', file=sys.stderr)
    return 1
  if num_notes == 0:
    print('提示: 未检测到音符。链路可用，但可换真实钢琴音频再验证。')
  print('自检通过')
  return 0


if __name__ == '__main__':
  sys.exit(main())
