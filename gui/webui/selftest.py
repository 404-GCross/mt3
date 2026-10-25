"""WebUI 接口自检：不需要模型、JAX、TensorFlow。

用一个假 runner 走完整状态机：令牌/跨站/上传上限/时长上限/队列满/取消/下载/
SSE/删除。跑法：

    python gui/webui/selftest.py

依赖：fastapi、uvicorn（只用到其中的 TestClient 需要 httpx）、python-multipart，
以及 mido（后处理）。
"""

from __future__ import annotations

import io
import math
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from webui import jobs as jobs_module  # noqa: E402
from webui import server as server_module  # noqa: E402

TOKEN = 'selftest-token'

PASSED = []


def check(name, condition, detail=''):
  if condition:
    PASSED.append(name)
    print('  ok   {}'.format(name))
    return True
  print('  FAIL {} {}'.format(name, detail))
  raise SystemExit(1)


def make_wav(seconds=0.2, rate=16000):
  buffer = io.BytesIO()
  with wave.open(buffer, 'wb') as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(rate)
    samples = b''.join(
        struct.pack('<h', int(1200 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(int(rate * seconds)))
    handle.writeframes(samples)
  return buffer.getvalue()


def write_fake_midi(path):
  """两个音符的合法 MIDI：验证下载与钢琴卷预览（不需要模型）。"""
  import mido

  mid = mido.MidiFile(type=0, ticks_per_beat=96)
  track = mido.MidiTrack()
  mid.tracks.append(track)
  track.append(mido.Message('note_on', note=60, velocity=80, time=0))
  track.append(mido.Message('note_on', note=64, velocity=80, time=48))
  track.append(mido.Message('note_off', note=60, velocity=0, time=48))
  track.append(mido.Message('note_off', note=64, velocity=0, time=48))
  mid.save(str(path))


def make_runner(delay=0.02, fail=False):
  """假 runner：把进度推完，写一个能下载的最小 MIDI。"""

  def runner(job, manager):
    job.log('fake runner')
    total = 4
    manager.update_progress(job, 0, total)
    for step in range(1, total + 1):
      time.sleep(delay)
      manager.update_progress(job, step, total)  # 取消时这里抛 JobCancelled
    if fail:
      raise RuntimeError('假失败')
    raw = job.directory / 'out.mid'
    write_fake_midi(raw)
    job.raw_path = raw
    job.notes = 2
    job.roll = jobs_module.midi_roll(raw)
    if job.rearrange:
      import midi_rearrange
      result = midi_rearrange.rearrange_file(
          raw, job.directory / 'out.rearranged.mid', **job.rearrange)
      job.rearranged_path = result.path
      job.rearrange_detail = result.detail()

  return runner


def make_client(runner=None, **overrides):
  temp = tempfile.mkdtemp(prefix='mt3-web-selftest-')
  config = server_module.WebConfig(
      token=TOKEN,
      checkpoint_dir=Path(temp) / 'checkpoints',
      jobs_dir=Path(temp) / 'jobs',
      device_info='fake:cpu',
      version='test',
      rearrange_defaults={
          'velocity': 30, 'min_ms': 30, 'chord_ms': 60, 'repeat_ms': 120,
          'drums': False},
  )
  for key, value in overrides.items():
    setattr(config, key, value)
  # 假的权重标记：上传接口会检查模型是否可用。
  for name in ('ismir2021', 'mt3'):
    marker = config.checkpoint_dir / name / 'checkpoint'
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b'fake')

  manager = jobs_module.JobManager(
      jobs_dir=config.jobs_dir,
      runner=runner or make_runner(),
      queue_limit=config.queue_limit,
      keep_hours=config.keep_hours,
      keep_models=config.keep_models)
  app = server_module.create_app(config, manager=manager)
  client = TestClient(app)
  client.cookies.set(server_module.COOKIE_NAME, config.token)
  return client, manager


def wait_for(client, job_id, timeout=20.0, headers=None):
  deadline = time.time() + timeout
  while time.time() < deadline:
    response = client.get('/api/jobs/{}'.format(job_id), headers=headers or {})
    assert response.status_code == 200, response.text
    job = response.json()
    if job['status'] in jobs_module.FINAL_STATUSES:
      return job
    time.sleep(0.05)
  raise AssertionError('任务超时未结束: {}'.format(job_id))


def upload(client, name='a.wav', payload=None, extra=None, headers=None):
  data = {
      'model': 'ismir2021',
      'rearrange': 'false',
      'drums': 'false',
      'velocity': '30',
      'min_ms': '30',
      'chord_ms': '60',
      'repeat_ms': '120',
  }
  data.update(extra or {})
  return client.post(
      '/api/jobs',
      files={'file': (name, payload if payload is not None else make_wav(),
                      'audio/wav')},
      data=data,
      headers=headers or {})


def test_auth():
  client, _ = make_client()
  client.cookies.clear()  # 从「没登录的浏览器」视角开始
  check('health 不需要令牌',
        client.get('/api/health').status_code == 200)
  check('未带令牌访问 /api/jobs 返回 401',
        client.get('/api/jobs').status_code == 401)
  check('错误的 ?t= 返回 401',
        client.get('/?t=wrong').status_code == 401)
  check('登录页提示需要令牌',
        '访问令牌' in client.get('/').text)

  login = client.get('/?t={}'.format(TOKEN), follow_redirects=False)
  check('正确令牌 303 + 下发 cookie',
        login.status_code == 303 and server_module.COOKIE_NAME in login.cookies)
  page = client.get('/')
  check('带 cookie 能打开界面',
        page.status_code == 200 and 'MT3 扒谱' in page.text)
  check('页面带 CSP 头',
        "default-src 'self'" in page.headers.get('content-security-policy', ''))

  info = client.get('/api/info')
  check('/api/info 返回设备与模型',
        info.status_code == 200 and 'fake:cpu' in info.json()['device'])
  check('Bearer 令牌也能用',
        client.get('/api/jobs',
                   headers={'Authorization': 'Bearer {}'.format(TOKEN)}
                   ).status_code == 200)


def test_limits():
  client, _ = make_client(max_bytes=100_000)
  response = upload(client, name='big.wav', payload=b'0' * 200_000)
  check('超过单文件上限返回 413', response.status_code == 413,
        response.text)
  check('413 的提示说得清上限', 'GB' in response.text or '上限' in response.text)

  response = upload(client, name='a.txt', payload=b'hello')
  check('不支持的扩展名返回 415', response.status_code == 415, response.text)

  response = upload(client, name='a.wav', payload=b'')
  check('空文件返回 400', response.status_code == 400, response.text)

  sample = Path(tempfile.mkdtemp(prefix='mt3-probe-')) / 'probe.wav'
  sample.write_bytes(make_wav(0.2))
  detected = server_module.probe_duration_minutes(sample)
  client, _ = make_client(max_minutes=0.0005)  # 0.03 秒
  response = upload(client, name='long.wav', payload=make_wav(0.2))
  if detected is None:
    # 环境里读不出时长（没装 soundfile/librosa）时服务端放行，交给 worker 报错。
    check('时长上限（环境里读不出时长，跳过）', response.status_code == 202)
  else:
    check('超过时长上限返回 413', response.status_code == 413, response.text)


def test_job_flow():
  client, _ = make_client()
  response = upload(client, extra={'rearrange': 'true', 'model': 'mt3'})
  check('上传返回 202', response.status_code == 202, response.text)
  job_id = response.json()['id']

  job = wait_for(client, job_id)
  check('任务完成', job['status'] == 'done', str(job))
  check('进度到 100%', job['percent'] == 100)

  check('任务带钢琴卷预览',
        isinstance(job['roll'], list) and len(job['roll']) == 2,
        str(job.get('roll')))
  check('钢琴卷坐标都在 0..1',
        all(0 <= value <= 1 for item in (job['roll'] or []) for value in item),
        str(job.get('roll')))

  raw = client.get('/api/jobs/{}/midi?kind=raw'.format(job_id))
  check('下载原始 MIDI', raw.status_code == 200 and len(raw.content) > 0)
  check('下载文件名来自上传名',
        'a.mid' in raw.headers.get('content-disposition', ''))

  rearranged = client.get('/api/jobs/{}/midi?kind=rearranged'.format(job_id))
  check('下载整理版 MIDI',
        rearranged.status_code == 200 and len(rearranged.content) > 0)

  bad = client.get('/api/jobs/{}/midi?kind=nope'.format(job_id))
  check('未知 kind 返回 400', bad.status_code == 400)

  events = client.get('/api/jobs/{}/events'.format(job_id))
  check('SSE 最后一条是最终状态',
        events.status_code == 200 and '"status": "done"' in events.text,
        events.text[:200])

  listed = client.get('/api/jobs').json()['jobs']
  check('任务出现在列表里', any(item['id'] == job_id for item in listed))

  deleted = client.delete('/api/jobs/{}'.format(job_id))
  check('删除任务返回 204', deleted.status_code == 204)
  check('删除后查不到',
        client.get('/api/jobs/{}'.format(job_id)).status_code == 404)


def test_cancel():
  client, _ = make_client(runner=make_runner(delay=0.4))
  response = upload(client)
  job_id = response.json()['id']
  time.sleep(0.15)
  cancelled = client.post('/api/jobs/{}/cancel'.format(job_id))
  check('取消接口返回 200', cancelled.status_code == 200, cancelled.text)
  job = wait_for(client, job_id)
  check('任务状态变成 cancelled', job['status'] == 'cancelled', str(job))


def test_queue_full():
  client, _ = make_client(runner=make_runner(delay=0.6), queue_limit=1)
  first = upload(client).json()['id']

  # 等第一个真正开跑，否则它还算在队列里。
  deadline = time.time() + 5
  while time.time() < deadline:
    if client.get('/api/jobs/{}'.format(first)).json()['status'] == 'running':
      break
    time.sleep(0.02)

  second = upload(client)
  check('队列没满时能继续提交', second.status_code == 202, second.text)
  third = upload(client)
  check('队列满返回 429', third.status_code == 429, third.text)

  client.post('/api/jobs/{}/cancel'.format(first))
  client.post('/api/jobs/{}/cancel'.format(second.json()['id']))
  wait_for(client, first)
  wait_for(client, second.json()['id'])


def test_failure():
  client, _ = make_client(runner=make_runner(fail=True))
  job_id = upload(client).json()['id']
  job = wait_for(client, job_id)
  check('runner 抛异常时任务 failed', job['status'] == 'failed', str(job))
  check('失败原因回传给页面', '假失败' in (job['error'] or ''))
  check('失败任务没有 MIDI 可下载',
        client.get('/api/jobs/{}/midi?kind=raw'.format(job_id)).status_code == 404)


def test_csrf():
  client, _ = make_client()
  token_header = {'Authorization': 'Bearer {}'.format(TOKEN)}
  response = upload(client, headers=dict(token_header,
                                         Origin='http://evil.example'))
  check('跨站 Origin 被拒绝', response.status_code == 403, response.text)

  response = client.post('/api/jobs', headers={
      'Authorization': 'Bearer {}'.format(TOKEN),
      'Sec-Fetch-Site': 'cross-site',
  })
  check('Sec-Fetch-Site: cross-site 被拒绝', response.status_code == 403)

  mixed = client.post('/api/jobs', headers={
      'Authorization': 'Bearer {}'.format(TOKEN),
      'Origin': 'http://testserver',
  })
  check('同源 Origin 放行（缺文件时是 422）', mixed.status_code == 422,
        str(mixed.status_code))


def main():
  print('WebUI 接口自检')
  for test in (test_auth, test_limits, test_job_flow, test_cancel,
               test_queue_full, test_failure, test_csrf):
    print('- {}'.format(test.__name__))
    test()
  print('\n自检通过（{} 项）'.format(len(PASSED)))
  return 0


if __name__ == '__main__':
  sys.exit(main())
