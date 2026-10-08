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

"""Standalone MT3 transcription backend for the desktop GUI.

Adapted from the official MT3 colab notebook:
https://github.com/magenta/mt3/blob/main/mt3/colab/music_transcription_with_transformers.ipynb

The model runs on GPU automatically when JAX is installed with CUDA support
(e.g. ``pip install "jax[cuda12]"``), otherwise it falls back to CPU.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Callable, Optional

# Must be set before JAX initializes. Prevents JAX from grabbing all GPU memory,
# so the desktop stays usable while a model is loaded.
os.environ.setdefault('XLA_PYTHON_CLIENT_PREALLOCATE', 'false')

import numpy as np
import tensorflow as tf

import gin
import jax
import librosa
import note_seq
import seqio
import t5
import t5x

from mt3 import metrics_utils
from mt3 import models
from mt3 import network
from mt3 import note_sequences
from mt3 import preprocessors
from mt3 import spectrograms
from mt3 import vocabularies


REPO_ROOT = Path(__file__).resolve().parent.parent
GIN_DIR = REPO_ROOT / 'mt3' / 'gin'
DEFAULT_CHECKPOINT_DIR = Path(
    os.environ.get('MT3_CHECKPOINT_DIR', str(REPO_ROOT / 'checkpoints')))

SAMPLE_RATE = spectrograms.DEFAULT_SAMPLE_RATE

MODEL_TYPES = ('ismir2021', 'mt3')
MODEL_LABELS = {
    'ismir2021': '钢琴（ISMIR2021，含力度）',
    'mt3': '多乐器（MT3，钢琴/吉他/贝斯/鼓等）',
}

# (done, total) with total=None when the segment count is unknown.
ProgressCallback = Callable[[int, Optional[int]], None]


class InferenceModel:
  """Wrapper of T5X model for music transcription."""

  def __init__(self, checkpoint_path, model_type='mt3', gin_dir=None,
               batch_size=8):
    if model_type not in MODEL_TYPES:
      raise ValueError('unknown model_type: %s' % model_type)

    gin_dir = Path(gin_dir) if gin_dir else GIN_DIR
    gin_files = [str(gin_dir / 'model.gin'),
                 str(gin_dir / f'{model_type}.gin')]

    # Model constants (kept in sync with the official colab).
    if model_type == 'ismir2021':
      num_velocity_bins = 127
      self.encoding_spec = note_sequences.NoteEncodingSpec
      self.inputs_length = 512
    else:
      num_velocity_bins = 1
      self.encoding_spec = note_sequences.NoteEncodingWithTiesSpec
      self.inputs_length = 256

    self.model_type = model_type
    self.batch_size = batch_size
    self.outputs_length = 1024
    self.sequence_length = {'inputs': self.inputs_length,
                            'targets': self.outputs_length}

    self.partitioner = t5x.partitioning.PjitPartitioner(num_partitions=1)

    # Build codecs and vocabularies.
    self.spectrogram_config = spectrograms.SpectrogramConfig()
    self.codec = vocabularies.build_codec(
        vocab_config=vocabularies.VocabularyConfig(
            num_velocity_bins=num_velocity_bins))
    self.vocabulary = vocabularies.vocabulary_from_codec(self.codec)
    self.output_features = {
        'inputs': seqio.ContinuousFeature(dtype=tf.float32, rank=2),
        'targets': seqio.Feature(vocabulary=self.vocabulary),
    }

    # Create a T5X model and restore the pretrained weights.
    self._parse_gin(gin_files)
    self.model = self._load_model()
    self.restore_from_checkpoint(str(checkpoint_path))

  @property
  def input_shapes(self):
    return {
        'encoder_input_tokens': (self.batch_size, self.inputs_length),
        'decoder_input_tokens': (self.batch_size, self.outputs_length),
    }

  def _parse_gin(self, gin_files):
    """Parse gin files used to train the model."""
    gin_bindings = [
        'from __gin__ import dynamic_registration',
        'from mt3 import vocabularies',
        'VOCAB_CONFIG=@vocabularies.VocabularyConfig()',
        'vocabularies.VocabularyConfig.num_velocity_bins=%NUM_VELOCITY_BINS',
    ]
    with gin.unlock_config():
      # Reset any config from a previously loaded model type.
      gin.clear_config()
      gin.parse_config_files_and_bindings(
          gin_files, gin_bindings, finalize_config=False)

  def _load_model(self):
    """Load up a T5X `Model` after parsing training gin config."""
    model_config = gin.get_configurable(network.T5Config)()
    module = network.Transformer(config=model_config)
    return models.ContinuousInputsEncoderDecoderModel(
        module=module,
        input_vocabulary=self.output_features['inputs'].vocabulary,
        output_vocabulary=self.output_features['targets'].vocabulary,
        optimizer_def=t5x.adafactor.Adafactor(decay_rate=0.8, step_offset=0),
        input_depth=spectrograms.input_depth(self.spectrogram_config))

  def restore_from_checkpoint(self, checkpoint_path):
    """Restore training state from checkpoint, resets self._predict_fn()."""
    train_state_initializer = t5x.utils.TrainStateInitializer(
        optimizer_def=self.model.optimizer_def,
        init_fn=self.model.get_initial_variables,
        input_shapes=self.input_shapes,
        partitioner=self.partitioner)

    restore_checkpoint_cfg = t5x.utils.RestoreCheckpointConfig(
        path=checkpoint_path, mode='specific', dtype='float32')

    train_state_axes = train_state_initializer.train_state_axes
    self._predict_fn = self._get_predict_fn(train_state_axes)
    self._train_state = train_state_initializer.from_checkpoint_or_scratch(
        [restore_checkpoint_cfg], init_rng=jax.random.PRNGKey(0))

  @functools.lru_cache()
  def _get_predict_fn(self, train_state_axes):
    """Generate a partitioned prediction function for decoding."""
    def partial_predict_fn(params, batch, decode_rng):
      return self.model.predict_batch_with_aux(
          params, batch, decoder_params={'decode_rng': None})
    return self.partitioner.partition(
        partial_predict_fn,
        in_axis_resources=(
            train_state_axes.params,
            t5x.partitioning.PartitionSpec('data',), None),
        out_axis_resources=t5x.partitioning.PartitionSpec('data',))

  def predict_tokens(self, batch, seed=0):
    """Predict tokens from preprocessed dataset batch."""
    prediction, _ = self._predict_fn(
        self._train_state.params, batch, jax.random.PRNGKey(seed))
    return self.vocabulary.decode_tf(prediction).numpy()

  def __call__(self, audio, progress_cb=None):
    """Infer note sequence from audio samples.

    Args:
      audio: 1-d numpy array of audio samples at 16kHz for a single example.
      progress_cb: optional callback ``(done, total)`` invoked after every
        decoded batch, where ``total`` is the number of audio segments.

    Returns:
      A note_seq.NoteSequence of the transcribed audio.
    """
    ds = self.audio_to_dataset(audio)
    ds = self.preprocess(ds)

    segments_ds = self.model.FEATURE_CONVERTER_CLS(pack=False)(
        ds, task_feature_lengths=self.sequence_length)
    total = int(tf.data.experimental.cardinality(segments_ds).numpy())
    if total < 0:
      total = None
    model_ds = segments_ds.batch(self.batch_size)

    example_iter = iter(ds.as_numpy_iterator())
    predictions = []
    done = 0
    for batch in model_ds.as_numpy_iterator():
      token_seqs = self.predict_tokens(batch)
      for tokens in token_seqs:
        example = next(example_iter)
        predictions.append(self.postprocess(tokens, example))
      done += len(token_seqs)
      if progress_cb is not None:
        progress_cb(done, total)

    result = metrics_utils.event_predictions_to_ns(
        predictions, codec=self.codec, encoding_spec=self.encoding_spec)
    return result['est_ns']

  def audio_to_dataset(self, audio):
    """Create a TF Dataset of spectrograms from input audio."""
    frames, frame_times = self._audio_to_frames(audio)
    return tf.data.Dataset.from_tensors({
        'inputs': frames,
        'input_times': frame_times,
    })

  def _audio_to_frames(self, audio):
    """Compute spectrogram frames from audio."""
    frame_size = self.spectrogram_config.hop_width
    padding = [0, frame_size - len(audio) % frame_size]
    audio = np.pad(audio, padding, mode='constant')
    frames = spectrograms.split_audio(audio, self.spectrogram_config)
    num_frames = len(audio) // frame_size
    times = np.arange(num_frames) / self.spectrogram_config.frames_per_second
    return frames, times

  def preprocess(self, ds):
    pp_chain = [
        functools.partial(
            t5.data.preprocessors.split_tokens_to_inputs_length,
            sequence_length=self.sequence_length,
            output_features=self.output_features,
            feature_key='inputs',
            additional_feature_keys=['input_times']),
        # Cache occurs here during training.
        preprocessors.add_dummy_targets,
        functools.partial(
            preprocessors.compute_spectrograms,
            spectrogram_config=self.spectrogram_config),
    ]
    for pp in pp_chain:
      ds = pp(ds)
    return ds

  def postprocess(self, tokens, example):
    tokens = self._trim_eos(tokens)
    start_time = example['input_times'][0]
    # Round down to nearest symbolic token step.
    start_time -= start_time % (1 / self.codec.steps_per_second)
    return {
        'est_tokens': tokens,
        'start_time': start_time,
        # Internal MT3 code expects raw inputs, not used here.
        'raw_inputs': [],
    }

  @staticmethod
  def _trim_eos(tokens):
    tokens = np.array(tokens, np.int32)
    if vocabularies.DECODED_EOS_ID in tokens:
      tokens = tokens[:np.argmax(tokens == vocabularies.DECODED_EOS_ID)]
    return tokens


class Transcriber:
  """Loads and caches MT3 models and transcribes audio files."""

  def __init__(self, checkpoint_dir=None, batch_size=8):
    self.checkpoint_dir = Path(
        checkpoint_dir if checkpoint_dir else DEFAULT_CHECKPOINT_DIR)
    self.batch_size = batch_size
    self._models = {}

  def checkpoint_path(self, model_type):
    return self.checkpoint_dir / model_type

  def is_available(self, model_type):
    return (self.checkpoint_path(model_type) / 'checkpoint').exists()

  def missing_checkpoints(self):
    return [m for m in MODEL_TYPES if not self.is_available(m)]

  def get_model(self, model_type):
    """Load model from checkpoint, caching it for later use."""
    if model_type not in MODEL_TYPES:
      raise ValueError('unknown model_type: %s' % model_type)
    if model_type in self._models:
      return self._models[model_type]

    path = self.checkpoint_path(model_type)
    if not self.is_available(model_type):
      raise FileNotFoundError(
          f'未找到 {model_type} 模型权重: {path}\n'
          f'请先运行: python gui/download_checkpoints.py')

    self._models[model_type] = InferenceModel(
        checkpoint_path=path, model_type=model_type, batch_size=self.batch_size)
    return self._models[model_type]

  @property
  def loaded_models(self):
    return sorted(self._models)

  def transcribe_file(self, audio_path, model_type, progress_cb=None):
    """Transcribe an audio file to a NoteSequence."""
    audio_path = Path(audio_path)
    if not audio_path.exists():
      raise FileNotFoundError(f'音频文件不存在: {audio_path}')

    model = self.get_model(model_type)
    audio = load_audio(audio_path)
    return model(audio, progress_cb=progress_cb)


def load_audio(path, sample_rate=SAMPLE_RATE):
  """Load an audio file as mono float32 samples at the model sample rate."""
  try:
    audio, _ = librosa.load(str(path), sr=sample_rate, mono=True)
  except Exception as exc:
    raise RuntimeError(
        f'无法读取音频文件 {path}: {exc}\n'
        f'建议转换为 WAV (16kHz/mono 最佳) 后重试。') from exc
  return audio.astype(np.float32)


def save_midi(note_sequence, path):
  """Write a NoteSequence to a Standard MIDI file."""
  note_seq.sequence_proto_to_midi_file(note_sequence, str(path))
  return str(path)


def device_summary():
  """Human-readable description of the devices JAX will use."""
  try:
    devices = jax.devices()
  except Exception as exc:  # pragma: no cover - very unusual.
    return f'未知设备 ({exc})'
  parts = []
  for device in devices:
    try:
      parts.append(f'{device.platform}:{device.device_kind}')
    except AttributeError:
      parts.append(str(device))
  return ', '.join(parts)


_TRANSCRIBER: Optional[Transcriber] = None


def configure_device(device='auto'):
  """Configure JAX device selection before creating/loading any model.

  ``auto`` leaves JAX's normal selection intact; ``cpu`` and ``cuda`` set
  ``JAX_PLATFORMS`` for the current process. This must be called before the
  first JAX operation.
  """
  if device not in ('auto', 'cpu', 'cuda'):
    raise ValueError(f'unknown device: {device}')
  if device == 'auto':
    return
  platform = 'cpu' if device == 'cpu' else 'cuda'
  # JAX is imported above because the backend needs it for model creation;
  # config.update is therefore more reliable than changing the environment.
  jax.config.update('jax_platforms', platform)
  try:
    available = jax.devices()
  except Exception as exc:
    raise RuntimeError(
        f'无法初始化 {device} 设备。请检查 NVIDIA 驱动/CUDA/JAX 安装。') from exc
  if device == 'cuda' and not any(d.platform == 'gpu' for d in available):
    raise RuntimeError(
        '已选择 NVIDIA GPU，但 JAX 未检测到 GPU。请安装匹配的 jax[cuda12] '
        '及 NVIDIA 驱动，或切换为“自动/CPU”。')


def get_transcriber(checkpoint_dir=None, batch_size=8, device='auto'):
  """Return a process-wide Transcriber singleton."""
  global _TRANSCRIBER
  if _TRANSCRIBER is None:
    configure_device(device)
    _TRANSCRIBER = Transcriber(
        checkpoint_dir=checkpoint_dir, batch_size=batch_size)
  return _TRANSCRIBER
