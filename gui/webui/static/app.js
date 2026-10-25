'use strict';

const $ = (id) => document.getElementById(id);

const STATUS_TEXT = {
  queued: '排队中',
  running: '转谱中',
  done: '完成',
  failed: '失败',
  cancelled: '已取消',
};

const state = {
  info: null,
  jobs: [],
  pending: [],
  uploading: false,
  xhr: null,
};

// ------------------------------------------------------------------ 小工具

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function fmtBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

function fmtDuration(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return `${minutes} 分 ${rest} 秒`;
  return `${Math.floor(minutes / 60)} 时 ${minutes % 60} 分`;
}

function errorMessage(xhr) {
  try {
    const body = JSON.parse(xhr.responseText);
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((item) => item.msg || JSON.stringify(item)).join('；');
    }
  } catch (err) {
    // 不是 JSON：退回状态码。
  }
  return `HTTP ${xhr.status}${xhr.status === 401 ? '（令牌失效，请重新用带 ?t= 的链接打开）' : ''}`;
}

async function api(path, options) {
  const response = await fetch(path, options || {});
  if (response.status === 401) {
    location.reload();
    throw new Error('需要访问令牌');
  }
  return response;
}

// ------------------------------------------------------------------ 初始化

function fillInfo(info) {
  state.info = info;
  $('devicePill').textContent = `设备：${info.device || '未知'}`;
  $('versionPill').textContent = info.version && info.version !== 'dev'
    ? `v${info.version}` : '开发版';

  const select = $('model');
  select.replaceChildren();
  const labels = { ismir2021: '钢琴（ismir2021）', mt3: '多乐器（mt3）' };
  for (const [name, available] of Object.entries(info.models || {})) {
    select.append(el('option', {
      value: name,
      disabled: !available,
      text: labels[name] ? `${labels[name]}${available ? '' : '（权重缺失）'}` : name,
    }));
  }
  const available = Object.entries(info.models || {}).filter(([, ok]) => ok);
  if (available.length) {
    const preferred = info.models.mt3 ? 'mt3' : available[0][0];
    select.value = preferred;
  }

  const limits = info.limits || {};
  $('limits').textContent =
    `单文件 ≤ ${fmtBytes(limits.max_bytes)}，时长 ≤ ${limits.max_minutes} 分钟，` +
    `队列 ${limits.queue_limit} 个，结果保留 ${limits.keep_hours} 小时；` +
    '本页只应在局域网内使用，不要做端口转发。';

  const defaults = info.rearrange_defaults || {};
  if (defaults.velocity !== undefined) $('velocity').value = defaults.velocity;
  if (defaults.min_ms !== undefined) $('minMs').value = defaults.min_ms;
  if (defaults.chord_ms !== undefined) $('chordMs').value = defaults.chord_ms;
  if (defaults.repeat_ms !== undefined) $('repeatMs').value = defaults.repeat_ms;

  updateHint();
}

function updateHint() {
  const parts = [];
  if ($('model').value === 'mt3') {
    parts.push('mt3 不预测力度（所有音符恒为 100），所以“力度阈值”只对钢琴模型有效。');
  }
  if (!$('rearrange').checked) {
    parts.push('不勾选时只输出原始 MIDI；勾选后会另存一份 .rearranged.mid，原文件保留。');
  }
  $('rearrangeHint').textContent = parts.join(' ');
}

// ------------------------------------------------------------------ 文件选择

function updateStartButton() {
  const disabled = !state.pending.length || state.uploading ||
    !state.info || !Object.values(state.info.models || {}).some(Boolean);
  $('start').disabled = disabled;
  $('start').textContent = state.pending.length
    ? `上传并开始（${state.pending.length} 个）` : '上传并开始';
}

function setPending(files) {
  const list = Array.from(files || []);
  state.pending = list;
  $('uploadStatus').textContent = list.length
    ? list.map((file) => file.name).join('、') : '';
  updateStartButton();
}

function showUploadProgress(percent, text) {
  const bar = $('uploadProgress');
  bar.hidden = false;
  bar.value = percent;
  $('uploadStatus').textContent = text;
}

// ------------------------------------------------------------------ 上传

function uploadOne(file, index, total) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file);
    form.append('model', $('model').value);
    form.append('rearrange', $('rearrange').checked ? 'true' : 'false');
    form.append('drums', $('drums').checked ? 'true' : 'false');
    form.append('velocity', $('velocity').value || '30');
    form.append('min_ms', $('minMs').value || '30');
    form.append('chord_ms', $('chordMs').value || '60');
    form.append('repeat_ms', $('repeatMs').value || '120');

    const xhr = new XMLHttpRequest();
    state.xhr = xhr;
    xhr.open('POST', '/api/jobs');
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round((100 * event.loaded) / event.total);
      showUploadProgress(percent,
        `第 ${index}/${total} 个：${file.name}（${fmtBytes(event.loaded)} / ${fmtBytes(event.total)}）`);
    };
    xhr.onload = () => {
      state.xhr = null;
      if (xhr.status === 202) resolve();
      else reject(new Error(errorMessage(xhr)));
    };
    xhr.onerror = () => {
      state.xhr = null;
      reject(new Error('网络错误'));
    };
    xhr.onabort = () => {
      state.xhr = null;
      reject(new Error('已取消上传'));
    };
    xhr.send(form);
  });
}

async function startUploads() {
  if (state.uploading || !state.pending.length) return;
  const queue = state.pending.slice();
  state.pending = [];
  state.uploading = true;
  $('abortUpload').hidden = false;
  $('uploadProgress').hidden = false;
  updateStartButton();

  const errors = [];
  for (let index = 0; index < queue.length; index += 1) {
    const file = queue[index];
    try {
      await uploadOne(file, index + 1, queue.length);
    } catch (err) {
      errors.push(`${file.name}：${err.message}`);
      if (err.message === '已取消上传') break;
    }
  }

  state.uploading = false;
  $('abortUpload').hidden = true;
  $('uploadProgress').hidden = true;
  $('file').value = '';
  $('uploadStatus').textContent = errors.length
    ? errors.join('；')
    : `已提交 ${queue.length} 个文件。`;
  await refreshJobs();
  updateStartButton();
}

// ------------------------------------------------------------------ 任务列表

async function cancelJob(id) {
  await api(`/api/jobs/${id}/cancel`, { method: 'POST' });
  await refreshJobs();
}

async function deleteJob(id) {
  await api(`/api/jobs/${id}`, { method: 'DELETE' });
  await refreshJobs();
}

function renderJob(job) {
  const row = el('div', { class: 'row' },
    el('strong', { text: job.filename }),
    el('span', { class: 'pill', text: job.model }),
    el('span', {
      class: 'status',
      dataset: { s: job.status },
      text: STATUS_TEXT[job.status] || job.status,
    }),
    job.queue_position
      ? el('span', { class: 'muted', text: `第 ${job.queue_position} 位` }) : null,
  );

  const meta = [];
  if (job.status === 'running' || job.status === 'done' || job.status === 'cancelled') {
    meta.push(`耗时 ${fmtDuration(job.elapsed)}`);
  }
  if (job.total) meta.push(`片段 ${job.done}/${job.total}`);
  if (job.notes !== null && job.notes !== undefined) meta.push(`${job.notes} 个音符`);

  const card = el('li', { class: 'job' }, row);

  if (job.status === 'running' || job.status === 'queued') {
    const bar = el('progress', { max: 100 });
    if (job.percent !== null) bar.value = job.percent;
    card.append(bar);
  }
  if (meta.length) {
    card.append(el('div', { class: 'muted', text: meta.join(' · ') }));
  }
  if (job.error) {
    card.append(el('div', { class: 'error', text: job.error }));
  }

  const actions = [];
  if (job.raw) {
    actions.push(el('a', {
      class: 'link', href: `/api/jobs/${job.id}/midi?kind=raw`,
      download: '', text: '下载 MIDI',
    }));
  }
  if (job.rearranged) {
    actions.push(el('a', {
      class: 'link', href: `/api/jobs/${job.id}/midi?kind=rearranged`,
      download: '', text: '下载整理版',
    }));
  }
  if (job.status === 'queued' || job.status === 'running') {
    actions.push(el('button', {
      class: 'link', text: '取消',
      onclick: () => cancelJob(job.id).catch((err) => alert(err.message)),
    }));
  }
  if (job.status === 'done' || job.status === 'failed' || job.status === 'cancelled') {
    actions.push(el('button', {
      class: 'link', text: '删除',
      onclick: () => deleteJob(job.id).catch((err) => alert(err.message)),
    }));
  }
  if (job.rearrange_detail) {
    actions.push(el('span', { class: 'muted', text: job.rearrange_detail }));
  }
  if (actions.length) card.append(el('div', { class: 'row' }, ...actions));

  if (job.log && job.log.length) {
    card.append(el('details', { class: 'log' },
      el('summary', { text: '日志' }),
      el('pre', { text: job.log.join('\n') }),
    ));
  }
  return card;
}

function renderJobs(jobs) {
  state.jobs = jobs;
  const list = $('jobs');
  list.replaceChildren(...jobs.map(renderJob));
  $('emptyJobs').hidden = jobs.length > 0;
  const active = jobs.filter((job) => job.status === 'running' ||
    job.status === 'queued').length;
  $('queuePill').textContent = active ? `${active} 个进行中` : '';
}

async function refreshJobs() {
  try {
    const response = await api('/api/jobs');
    const body = await response.json();
    renderJobs(body.jobs || []);
  } catch (err) {
    // 网络抖动时保留上一屏，下一轮再试。
  }
}

// ------------------------------------------------------------------ 启动

function bind() {
  const drop = $('drop');
  const input = $('file');

  drop.addEventListener('click', () => input.click());
  drop.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      input.click();
    }
  });
  drop.addEventListener('dragover', (event) => {
    event.preventDefault();
    drop.classList.add('over');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (event) => {
    event.preventDefault();
    drop.classList.remove('over');
    setPending(event.dataTransfer.files);
  });
  input.addEventListener('change', () => setPending(input.files));

  $('start').addEventListener('click', () => {
    startUploads().catch((err) => {
      $('uploadStatus').textContent = err.message;
    });
  });
  $('abortUpload').addEventListener('click', () => {
    if (state.xhr) state.xhr.abort();
  });
  $('rearrange').addEventListener('change', updateHint);
  $('model').addEventListener('change', updateHint);

  for (const event of ['dragover', 'drop']) {
    document.addEventListener(event, (e) => {
      if (!drop.contains(e.target)) e.preventDefault();
    });
  }

  setInterval(() => {
    if (!document.hidden) refreshJobs();
  }, 1500);
}

async function main() {
  bind();
  try {
    const response = await api('/api/info');
    fillInfo(await response.json());
  } catch (err) {
    $('uploadStatus').textContent = `无法读取服务信息：${err.message}`;
  }
  updateStartButton();
  await refreshJobs();
}

main();
