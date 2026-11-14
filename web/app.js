const statusNode = document.querySelector('#status');
const filters = document.querySelector('#filters');
const designForm = document.querySelector('#design');
let current = null;
let selectedDesign = null;
let requestGeneration = 0;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function text(tag, value, className) {
  const node = document.createElement(tag);
  node.textContent = typeof value === 'object' ? JSON.stringify(value) : String(value ?? '未知');
  if (className) node.className = className;
  return node;
}

function percent(value) {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`;
}

function fixed(value, digits = 4) {
  return value === null || value === undefined ? '—' : Number(value).toFixed(digits);
}

function table(container, rows) {
  container.replaceChildren();
  if (!rows.length) {
    container.append(text('p', '当前范围没有记录'));
    return;
  }
  const columns = Array.from(new Set(rows.flatMap(row => Object.keys(row))));
  const grid = document.createElement('table');
  const head = document.createElement('tr');
  for (const column of columns) head.append(text('th', column));
  const thead = document.createElement('thead');
  thead.append(head);
  grid.append(thead);
  const body = document.createElement('tbody');
  for (const row of rows) {
    const line = document.createElement('tr');
    for (const column of columns) line.append(text('td', row[column]));
    body.append(line);
  }
  grid.append(body);
  container.append(grid);
}

function description(container, pairs) {
  container.replaceChildren();
  for (const [key, amount] of pairs) container.append(text('dt', key), text('dd', amount));
}

function render(value, savedTitle = '') {
  current = value;
  description(document.querySelector('#summary'), Object.entries(value.result.summary));
  document.querySelector('#notes').replaceChildren(...value.result.notes.map(note => text('li', note)));
  document.querySelector('#digest').textContent = `资料指纹：${value.digest}`;
  table(document.querySelector('#results'), value.result.rows);
  statusNode.textContent = savedTitle ? `正在查看保存的分析：${savedTitle}` : '分析已更新';
  filters.elements.cutoff.value = value.options.cutoff;
  filters.elements.group.value = value.options.group;
}

async function refresh() {
  const generation = ++requestGeneration;
  const query = new URLSearchParams(new FormData(filters));
  statusNode.textContent = '正在分析';
  try {
    const result = await api(`/api/view?${query}`);
    if (generation === requestGeneration) render(result);
  } catch (error) {
    if (generation === requestGeneration) {
      current = null;
      statusNode.textContent = error.message;
    }
  }
}

async function history() {
  const entries = await api('/api/analyses');
  const list = document.querySelector('#history');
  list.replaceChildren();
  for (const entry of entries) {
    const item = document.createElement('li');
    const button = text('button', entry.title);
    button.type = 'button';
    button.addEventListener('click', async () => {
      const generation = ++requestGeneration;
      try {
        const saved = await api(`/api/analyses/${entry.id}`);
        if (generation === requestGeneration) render(saved, saved.title);
      } catch (error) {
        statusNode.textContent = error.message;
      }
    });
    item.append(button, text('small', entry.created_at));
    list.append(item);
  }
}

/* ---- 确认性分析 -------------------------------------------------------- */

function designOptions() {
  const form = designForm.elements;
  const roles = {};
  for (const checkbox of document.querySelectorAll('#metric-roles input[data-metric]')) {
    if (checkbox.checked) roles[checkbox.dataset.metric] = checkbox.dataset.role;
  }
  return {
    cutoff: Number(form.cutoff.value),
    group: form.group.value,
    control: form.control.value,
    treatments: Array.from(form.treatments.selectedOptions).map(option => option.value),
    metrics: document.querySelectorAll('#metric-roles input[data-metric]:checked').length
      ? Array.from(new Set(Array.from(document.querySelectorAll('#metric-roles input[data-metric]:checked')).map(node => node.dataset.metric)))
      : [],
    roles,
    alpha: Number(form.alpha.value),
    max_looks: Number(form.max_looks.value),
    min_observed: Number(form.min_observed.value),
    max_missing_rate: Number(form.max_missing_rate.value),
  };
}

async function loadDesigns() {
  const entries = await api('/api/designs');
  const list = document.querySelector('#designs');
  list.replaceChildren();
  if (!entries.length) {
    list.append(text('li', '还没有保存的计划', 'hint'));
    return;
  }
  for (const entry of entries) {
    const item = document.createElement('li');
    const button = text('button', entry.title);
    button.type = 'button';
    button.addEventListener('click', () => openDesign(entry.id));
    const badges = [];
    if (!entry.confirmatory) badges.push('探索');
    else if (entry.superseded_by) badges.push('已被替换');
    else if (entry.looks_remaining === 0) badges.push('查看额度用尽');
    else badges.push(`剩余查看 ${entry.looks_remaining}/${entry.design.max_looks}`);
    item.append(button, text('small', `${entry.created_at} · ${badges.join(' · ')}`));
    if (entry.note) item.append(text('p', entry.note, 'hint'));
    list.append(item);
  }
}

async function openDesign(id) {
  selectedDesign = await api(`/api/designs/${id}`);
  const detail = document.querySelector('#design-detail');
  detail.hidden = false;
  document.querySelector('#detail-title').textContent = selectedDesign.title;
  document.querySelector('#detail-note').textContent = selectedDesign.note;
  const design = selectedDesign.design;
  description(document.querySelector('#detail-meta'), [
    ['对照', design.control],
    ['待评方案', design.treatments.join('、')],
    ['人群', design.group || '全部'],
    ['指标', design.metrics.map(item => `${item.name}（${item.role === 'benefit' ? '收益' : '保护'}，${item.direction === 'higher' ? '越大越好' : '越小越好'}，门槛 ${item.margin}）`).join('；')],
    ['错误率', `单侧 α=${design.alpha}，最多 ${design.max_looks} 次查看，当前已用 ${selectedDesign.looks_used} 次`],
    ['可继续查看', selectedDesign.can_look ? '是' : '否'],
    ['保存时资料指纹', selectedDesign.digest],
  ]);
  document.querySelector('#look-cutoff').value = design.planned_at_cutoff;
  document.querySelector('#look-cutoff').min = design.planned_at_cutoff;
  document.querySelector('#preview-output').replaceChildren();
  document.querySelector('#look-output').replaceChildren();
  const schedule = await api(`/api/designs/${id}/schedule`);
  table(document.querySelector('#schedule'), schedule.schedule.map(row => ({
    查看次序: row.look,
    信息比例: row.information_fraction,
    累计α: row.cumulative_alpha,
    本次α: row.incremental_alpha,
  })));
  await renderReadiness();
  renderLookList();
}

async function renderReadiness() {
  const container = document.querySelector('#readiness');
  if (!selectedDesign.can_look) {
    container.replaceChildren(text('p', '该计划不能再消耗确认性查看额度；可在描述性浏览中继续查阅。', 'hint'));
    return;
  }
  try {
    const preview = await api(`/api/designs/${selectedDesign.id}/preview?cutoff=${encodeURIComponent(document.querySelector('#look-cutoff').value)}`);
    container.replaceChildren(text('p', preview.warning, 'hint'));
    table(container, preview.readiness);
  } catch (error) {
    container.replaceChildren(text('p', error.message));
  }
}

function renderLookList() {
  const list = document.querySelector('#looks');
  list.replaceChildren();
  for (const look of selectedDesign.looks) {
    const item = document.createElement('li');
    const button = text('button', `第 ${look.look_index} 次查看 @ ${look.cutoff}`);
    button.type = 'button';
    button.addEventListener('click', () => showLook(selectedDesign.id, look.look_index));
    item.append(button, text('small', `α=${look.error_budget.alpha_per_comparison.toFixed(5)} · ${look.decision.label} · 指纹 ${look.digest.slice(0, 12)}`));
    list.append(item);
  }
  if (!selectedDesign.looks.length) list.append(text('li', '还没有打开过结果', 'hint'));
}

async function showLook(designId, index) {
  const value = await api(`/api/designs/${designId}/looks/${index}`);
  renderLook(value);
}

function renderLook(value) {
  const result = value.result;
  const container = document.querySelector('#look-output');
  container.replaceChildren();
  const heading = text('h3', `第 ${result.look_index} 次查看 · 时点 ${result.cutoff} · ${result.decision.label}`);
  const decision = document.createElement('p');
  decision.append(text('strong', `结论：${result.decision.label}。`), text('span', result.decision.explanation));
  const meta = document.createElement('dl');
  description(meta, [
    ['错误率分配', `本次每个比较 α=${result.error_budget.alpha_per_comparison.toFixed(6)}，临界值 z=${result.error_budget.critical_value.toFixed(3)}`],
    ['累计α', `${result.error_budget.cumulative_alpha.toFixed(6)}（预算 ${result.error_budget.alpha}）`],
    ['比较数', result.error_budget.comparisons],
    ['资料指纹', value.digest],
    ['数据范围', `时点 ${result.cutoff}，人群 ${result.group || '全部'}，计划 ${value.design_id}`],
  ]);
  const arms = document.createElement('ul');
  for (const arm of result.decision.arms) {
    arms.append(text('li', `${arm.arm}：${{recommendable: '通过', waiting: '等待', not_met: '未通过'}[arm.status]} — ${arm.reasons.join('；')}`));
  }
  const comparisonRows = result.comparisons.map(row => ({
    方案: row.arm,
    指标: row.name,
    角色: row.role === 'benefit' ? '收益' : '保护',
    效应: fixed(row.effect),
    方向: row.direction === 'higher' ? '越大越好' : '越小越好',
    业务门槛: row.margin,
    调整后区间: row.effect_range
      ? `[${fixed(row.effect_range.lower)}, ${row.effect_range.upper === null ? '∞' : fixed(row.effect_range.upper)}]`
      : '未判定',
    '调整阈值z': fixed(row.critical_value, 3),
    '调整后z': fixed(row.z, 3),
    判定: row.verdict_label || row.status_label || '未判定',
    说明: row.reasons.join('；') || '—',
  }));
  const summaries = result.summaries.map(row => ({
    方案: row.arm,
    指标: row.metric,
    分组: row.assigned,
    成熟: row.mature,
    观测: row.observed,
    缺失: row.missing,
    null: row.null,
    待可见: row.pending,
    缺失率: percent(row.missing_rate),
    均值: fixed(row.average, 4),
    状态: row.status_label,
  }));
  const strength = text('p', result.decision.fully_accrued
    ? `数据范围：当前人群与指标在时点 ${result.decision.accrual_end} 已全部成熟，再等资料不会增加观测；剩余计划查看 ${result.decision.looks_remaining} 次。`
    : `数据范围：当前人群与指标最晚在时点 ${result.decision.accrual_end} 成熟；剩余计划查看 ${result.decision.looks_remaining} 次。`, 'hint');
  const relation = document.createElement('div');
  relation.className = 'scroll';
  table(relation, comparisonRows);
  const summaryBox = document.createElement('div');
  summaryBox.className = 'scroll';
  table(summaryBox, summaries);
  container.append(heading, decision, strength, meta, text('h3', '方案判定'), arms, text('h3', '效应范围与调整后检验'), relation, text('h3', '成熟度与缺失（不补零）'), summaryBox);
  for (const note of result.notes) container.append(text('p', note, 'hint'));
  container.append(text('p', `记录编号 ${value.look_id ?? value.id} · 计划 ${value.design_id} · 打开时间 ${value.created_at} · 资料指纹 ${value.digest}`, 'hint'));
}

designForm.addEventListener('submit', async event => {
  event.preventDefault();
  const options = designOptions();
  if (!options.metrics.length) {
    document.querySelector('#design-note').textContent = '至少选择一个指标';
    return;
  }
  try {
    const saved = await api('/api/designs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title: designForm.elements.title.value, preregistered: designForm.elements.preregistered.checked, options}),
    });
    designForm.elements.preregistered.checked = false;
    await loadDesigns();
    await openDesign(saved.id);
    document.querySelector('#design-note').textContent = saved.note;
  } catch (error) {
    document.querySelector('#design-note').textContent = error.message;
  }
});

document.querySelector('#preview').addEventListener('click', renderReadiness);

document.querySelector('#look').addEventListener('click', async () => {
  if (!selectedDesign) return;
  const cutoff = Number(document.querySelector('#look-cutoff').value);
  if (!confirm(`打开第 ${selectedDesign.looks_used + 1} 次查看并占用确认性额度，时点 ${cutoff}？`)) return;
  try {
    const value = await api(`/api/designs/${selectedDesign.id}/looks`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cutoff}),
    });
    await openDesign(selectedDesign.id);
    renderLook(value);
    if (value.reused) document.querySelector('#look-output').prepend(text('p', '该时点与资料此前已打开，直接返回原记录，不重复消耗额度。', 'hint'));
  } catch (error) {
    document.querySelector('#look-output').replaceChildren(text('p', error.message));
  }
});

/* ---- 既有交互 ---------------------------------------------------------- */

filters.addEventListener('submit', event => {
  event.preventDefault();
  refresh();
});

document.querySelector('#save').addEventListener('submit', async event => {
  event.preventDefault();
  if (!current) return;
  try {
    const saved = await api('/api/analyses', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: event.target.elements.title.value, options: current.options})});
    render(saved, saved.title);
    await history();
  } catch (error) {
    statusNode.textContent = error.message;
  }
});

document.querySelector('#source').addEventListener('click', async () => {
  try {
    const data = await api(`/api/source?table=${encodeURIComponent(document.querySelector('#tables').value)}`);
    table(document.querySelector('#raw'), data.rows);
  } catch (error) {
    statusNode.textContent = error.message;
  }
});

async function start() {
  const catalog = await api('/api/catalog');
  document.querySelector('#description').textContent = catalog.description;
  filters.elements.cutoff.min = catalog.min_cutoff;
  filters.elements.cutoff.max = catalog.max_cutoff;
  filters.elements.cutoff.value = catalog.default_cutoff;
  designForm.elements.cutoff.min = catalog.min_cutoff;
  designForm.elements.cutoff.max = catalog.max_cutoff;
  designForm.elements.cutoff.value = catalog.default_cutoff;
  const groups = [['', '全部'], ...catalog.groups.map(group => [group, group])];
  for (const [key, label] of groups) {
    for (const form of [filters, designForm]) {
      const option = text('option', label);
      option.value = key;
      form.elements.group.append(option);
    }
  }
  for (const arm of catalog.arms) {
    const option = text('option', arm);
    option.value = arm;
    designForm.elements.control.append(option);
    const treatment = text('option', arm);
    treatment.value = arm;
    designForm.elements.treatments.append(treatment);
  }
  if (catalog.arms.includes('control')) designForm.elements.control.value = 'control';
  for (const option of designForm.elements.treatments.options) {
    option.selected = option.value !== designForm.elements.control.value;
  }
  const roles = document.querySelector('#metric-roles');
  for (const metric of catalog.metrics) {
    const label = document.createElement('label');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = true;
    checkbox.dataset.metric = metric.metric;
    checkbox.dataset.role = metric.direction === 'lower' ? 'guardrail' : 'benefit';
    const role = document.createElement('select');
    for (const [value, name] of [['benefit', '收益（越大越好）'], ['guardrail', '保护（越小越好）']]) {
      const option = text('option', name);
      option.value = value;
      role.append(option);
    }
    role.addEventListener('change', () => { checkbox.dataset.role = role.value; });
    const choice = text('label', '角色');
    choice.append(role);
    label.append(checkbox, text('span', metric.name), choice);
    roles.append(label);
  }
  for (const name of Object.keys(catalog.tables)) {
    const option = text('option', `${name}（${catalog.tables[name]}）`);
    option.value = name;
    document.querySelector('#tables').append(option);
  }
  await refresh();
  await history();
  await loadDesigns();
}

start().catch(error => { statusNode.textContent = error.message; });
