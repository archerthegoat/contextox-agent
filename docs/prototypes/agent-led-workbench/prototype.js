/* Local synthetic interaction prototype. No network, model, or file-content access. */
'use strict';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escapeHtml = text => String(text).replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[character]);

const initialRequest = '我想按地区统计订单金额。';
const initialAnswer = '只统计已支付订单，退款订单不纳入，按支付时间归属日期。';
const defaultAnswers = {
  status: '仅纳入已支付（paid）订单',
  refunds: '已退款（refunded）订单不纳入',
  time: '按 paid_at，使用北京时间 UTC+08:00',
  basis: '用户在当前对话明确给出的业务规则；字段和时区来自 notes.md。'
};
const labels = ['明确目标', '理解资料', '澄清口径', '整理成果'];
const sourceNames = {orders: 'orders.csv', customers: 'customers.csv', notes: 'notes.md'};
const state = {
  scene: 'entry', following: true, view: 'intro', connected: true, running: false,
  approved: false, saved: false, version: 1, messages: [], pending: '',
  sources: ['orders', 'customers', 'notes'], timer: null, resumeFailed: false,
  answers: {...defaultAnswers}
};

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { $('#toast').hidden = true; }, 3500);
}

function addMessage(role, body) { state.messages.push({role, body}); }
function agent(body) { addMessage('agent', body); }
function user(text) { addMessage('user', escapeHtml(text)); }

function nextActions(title, actions) {
  return `<div class="next-actions"><div class="next-actions-copy"><small>建议下一步</small><strong>${title}</strong></div><div class="next-action-buttons">${actions.join('')}</div></div>`;
}

function welcome() {
  return `<p>你好，我可以和你一起把资料里的业务口径弄清楚。</p>
    <p>本轮已经选中 3 份公开示例资料。你可以直接说目标，也可以先让我解释这些资料记录了什么。</p>
    ${nextActions('先从资料或一个具体问题开始', [
      '<button class="primary" data-action="understand-sources">先了解这 3 份资料</button>',
      `<button data-prompt="${initialRequest}">整理地区订单金额</button>`,
      '<button class="quiet" data-action="sources">调整资料</button>'
    ])}`;
}

function discussion() {
  return `<p>这 3 份资料分别是订单表、客户表和口径说明。两张表可以通过 <button class="citation" data-view="relations">customer_id</button> 关联：订单表记录交易，客户表补充所在地区。</p>
    <p>订单里有已支付、已退款和待支付三种样例。金额单位是人民币元，但税费、折扣口径尚未确定。<button class="citation" data-view="notes">查看资料说明</button></p>
    ${nextActions('选择你接下来想完成的事', [
      `<button class="primary" data-prompt="${initialRequest}">按地区整理订单金额</button>`,
      '<button data-prompt="先解释一下两张表的关系。">继续解释表关系</button>',
      '<button class="quiet" data-action="sources">调整资料范围</button>'
    ])}`;
}

function clarification() {
  return `<p>字段和关联关系已经找到了。“订单金额”还有 3 个会改变结果的业务问题，需要你来确定。</p>
    <ul><li>哪些订单状态需要计入？</li><li>已退款订单如何处理？</li><li>用哪个时间字段归属日期？</li></ul>
    ${nextActions('回答后，我会先整理成卡片给你核对', [
      `<button class="primary" data-prompt="${initialAnswer}">使用示例回答</button>`,
      '<button data-prompt="为什么需要确认退款规则？">为什么要确认退款规则</button>',
      '<button class="quiet" data-view="questions">查看问题影响</button>'
    ])}`;
}

function answerCard() {
  return `<div class="answer-card" data-answer-card="true">
    <header><div><small>采用前核对</small><h3>这 3 条业务规则准确吗？</h3></div><span class="review-badge">等待你确认</span></header>
    <label class="field">计入哪些订单状态<input id="answer-status" value="${escapeHtml(state.answers.status)}"></label>
    <label class="field">退款如何处理<input id="answer-refunds" value="${escapeHtml(state.answers.refunds)}"></label>
    <label class="field">日期如何归属<input id="answer-time" value="${escapeHtml(state.answers.time)}"></label>
    <div class="notice">仍未知：金额是否包含税费及折扣。这里会继续保留未知，不替你决定。</div>
    <details class="technical-details"><summary>查看回答来源与版本</summary><p>这份回答来自本次对话，目前是第 ${state.version} 版。修改任何内容都会形成需要重新确认的新版本。</p><label class="field">回答依据<textarea id="answer-basis" rows="2">${escapeHtml(state.answers.basis)}</textarea></label></details>
    <div class="card-actions"><button class="primary" data-action="confirm">确认这 3 条并继续</button><button data-action="save-answer">仅保存，稍后继续</button></div>
    <div class="footnote" id="save-note">3 条回答已经填写；点击确认后才会采用并继续分析。</div>
  </div>`;
}

function resultMessage() {
  return `<p>你确认的规则已经写入候选成果。<button class="citation" data-view="result">查看本轮变化</button></p>
    <p>现在可以按地区汇总已支付订单，并按支付时间归属日期。金额中的税费、折扣仍未知，因此成果还需要核对。</p>
    ${nextActions('检查结果，或继续补充业务口径', [
      '<button class="primary" data-view="result">查看本轮变化</button>',
      '<button data-prompt="金额包含税费，但暂时不确定折扣怎么处理。">继续补充口径</button>',
      '<button class="quiet" data-view="notes">核对资料依据</button>'
    ])}`;
}

function failedMessage() {
  return `<p>你的业务回答已经确认并保留，但后续分析没有启动。</p>
    <div class="answer-card"><div class="receipt"><span>业务回答</span><strong>已确认</strong></div><div class="receipt"><span>后续分析</span><strong>尚未运行</strong></div><div class="notice error">合成故障：连接暂时不可用。重试不会重复确认已经保存的回答。</div><button class="primary" data-action="retry">重试后续分析</button><details class="technical-details"><summary>技术详情</summary><p>回答版本 1 的批准回执已保存；后续 Run 未创建。</p></details></div>`;
}

function setScene(scene) {
  clearTimeout(state.timer);
  Object.assign(state, {
    scene, following: true, view: 'intro', running: false, approved: false,
    saved: false, version: 1, messages: [], pending: '', resumeFailed: false,
    sources: ['orders', 'customers', 'notes'], connected: scene !== 'connect',
    answers: {...defaultAnswers}
  });
  $('#message').value = '';
  $('#source-scope').open = false;
  $('#scenario').value = scene;
  $$('[data-source]').forEach(input => { input.checked = true; });

  if (scene === 'entry') agent(welcome());
  if (scene === 'discussion') { user('先帮我看看这三份资料。'); agent(discussion()); state.view = 'relations'; }
  if (['analysis', 'question', 'answer', 'result', 'failed', 'unknown', 'cancelled'].includes(scene)) {
    user(initialRequest);
    agent('<p>好，我会从订单、客户和资料说明开始，整理按地区汇总需要的字段与规则。过程会同步显示在中间。</p>');
    state.view = 'activity';
  }
  if (scene === 'analysis') { state.running = true; agent('<p>正在核对资料字段和关联关系。你仍可以编辑下一条消息，或停止这轮分析。</p>'); }
  if (['question', 'answer', 'result', 'failed'].includes(scene)) { agent(clarification()); state.view = 'questions'; }
  if (scene === 'answer') { user(initialAnswer); agent('<p>我把你的回答整理成了下面的卡片。请修改或确认后再继续。</p>' + answerCard()); }
  if (scene === 'result') { user(initialAnswer); state.approved = true; state.view = 'result'; agent(resultMessage()); }
  if (scene === 'failed') { user(initialAnswer); state.approved = true; state.resumeFailed = true; state.view = 'recovery'; agent(failedMessage()); }
  if (scene === 'unknown') { state.view = 'unknown'; agent('<div class="notice error">连接中断，暂时无法确认这次分析有没有完成。请先核对结果，再决定下一步。</div><button class="primary" data-action="reconcile">核对执行结果</button>'); }
  if (scene === 'cancelled') { state.view = 'cancelled'; agent('<p>这轮分析已经停止。资料、对话和下一条草稿都还在。</p><div class="next-action-buttons"><button class="primary" data-prompt="继续按地区整理订单金额。">继续分析</button></div>'); }
  if (scene === 'connect') {
    state.pending = '先帮我看看这三份资料。';
    $('#message').value = state.pending;
    agent(welcome());
    agent('<div class="notice">还没有连接模型。刚才的消息已保留，连接后可以继续。</div><button class="primary" data-action="settings">连接模型</button>');
  }
  render();
}

function stage() {
  if (['entry', 'discussion', 'connect'].includes(state.scene)) return 0;
  if (['question', 'answer', 'failed'].includes(state.scene)) return 2;
  if (state.scene === 'result') return 3;
  return 1;
}

function updateTaskStatus() {
  const statuses = {
    entry: '可以开始', discussion: '正在讨论', connect: '等待连接', analysis: '正在理解资料',
    question: '需要你回答 3 件事', answer: state.saved ? '回答已保存，等待确认' : '回答等待确认',
    result: '候选成果等待核对', failed: '回答已确认，分析待恢复', unknown: '需要先核对结果', cancelled: '已停止'
  };
  $('#task-status').textContent = statuses[state.scene] || '正在讨论';
  $('#task-status').className = `badge ${['question', 'answer', 'failed', 'unknown'].includes(state.scene) ? 'amber' : 'blue'}`;
}

function summaryForScene() {
  const sourceResult = `${state.sources.length} 份资料已选入本次对话`;
  return {
    entry: ['还没有开始分析', '在右侧说目标，或先让 Agent 了解资料', sourceResult],
    discussion: ['正在理解资料记录了什么', '选择一个具体业务问题', '已找到订单与客户的关联字段'],
    connect: ['消息已保留', '连接模型后继续发送', sourceResult],
    analysis: ['正在核对字段与资料关系', '可以等待，也可以随时停止', '本轮还没有形成业务结论'],
    question: ['已找到字段与关联关系', '回答 3 个会改变统计结果的问题', '候选关系已形成，业务规则尚未采用'],
    answer: ['自然语言回答已整理', '核对卡片后确认并继续', '修改不会直接变成已确认结论'],
    result: ['候选成果已经更新', '核对变化，继续补充仍未知的口径', '3 条业务规则已采用，1 项仍未知'],
    failed: ['业务回答已经确认', '重试后续分析', '已确认内容不会被重复提交'],
    unknown: ['连接中断，结果暂时未知', '先核对上一次执行结果', '不会自动重试或重复执行'],
    cancelled: ['分析已经停止', '继续讨论，或重新发起分析', '资料与对话均已保留']
  }[state.scene];
}

function renderSummary() {
  const [now, next, result] = summaryForScene();
  $('#work-summary').innerHTML = [
    ['正在做什么', now], ['需要你做什么', next], ['已经得到什么', result]
  ].map(([label, value], index) => `<div class="summary-item ${index === 1 ? 'needs-action' : ''}"><small>${label}</small><strong>${value}</strong></div>`).join('');
}

function render() {
  $('#scenario').value = state.scene;
  state.messages = state.messages.map(message => message.body.includes('data-answer-card="true"')
    ? {...message, body: '<p>请核对或修改这份业务回答。</p>' + answerCard()}
    : message);
  $('#messages').innerHTML = state.messages.map(message => `<div class="message ${message.role}">${message.role === 'agent' ? '<div class="sender">数契</div>' : ''}${message.body}</div>`).join('');
  const renderedMessages = $$('#messages .message');
  renderedMessages.slice(0, -1).forEach(message => message.querySelectorAll('.next-actions').forEach(actions => {
    actions.hidden = true;
    actions.setAttribute('aria-hidden', 'true');
    actions.querySelectorAll('button').forEach(button => { button.disabled = true; button.tabIndex = -1; });
  }));
  const currentStep = stage();
  $('#progress').innerHTML = labels.map((label, index) => `<li class="${index === currentStep ? 'current' : index < currentStep ? 'past' : ''}"${index === currentStep ? ' aria-current="step"' : ''}><span class="step-index">${index < currentStep ? '✓' : index + 1}</span>${label}</li>`).join('');
  const noTask = ['entry', 'discussion', 'connect'].includes(state.scene);
  $('#task-title').textContent = noTask ? '从一个问题开始' : '按地区理解订单金额';
  updateTaskStatus();
  renderSummary();
  $('#agent-status').textContent = state.running ? '正在分析，可以随时停止' : noTask ? '一起把问题弄清楚' : state.scene === 'result' ? '成果已更新，仍有未知事项' : '对话与资料会持续保留';
  $('#send').disabled = state.running || state.scene === 'unknown';
  $('#send').hidden = state.running;
  $('#stop').hidden = !state.running;
  $('#composer-notice').textContent = state.running ? '分析进行中。可以编辑下一条草稿，停止或结束后再发送。' : state.scene === 'unknown' ? '请先核对上次执行结果。草稿会保留，不会自动重试。' : '';
  $('#scope-label').textContent = `本次对话使用 ${state.sources.length} 份资料`;
  $('#follow-label').textContent = state.following ? '这里会跟随对话展示相关内容' : '你正在手动查看资料，新进展不会切换此页';
  $('#follow').hidden = state.following;
  renderContent();
  $('#messages').scrollTop = $('#messages').scrollHeight;
}

function sheet(title, body) {
  return `<section class="sheet"><div class="sheet-head">${title}</div><div class="sheet-body">${body}</div></section>`;
}

function renderContent() {
  let html = '';
  const view = state.view;
  if (view === 'intro') html = `<div class="empty-intro"><img class="intro-logo" src="./assets/contextox-mark.png" alt=""><div class="section-kicker">从对话开始</div><h2>说出问题，一起找到有依据的答案。</h2><p class="lede">右侧负责讨论和推进；这里负责解释当前过程、展示资料依据和核对成果。</p><div class="intro-steps"><div class="intro-row"><span>01</span><div>先说你想解决什么<p>不用学习任务、运行或模型配置。</p></div></div><div class="intro-row"><span>02</span><div>Agent 找资料、说明关系<p>遇到会改变结论的地方再问你。</p></div></div><div class="intro-row"><span>03</span><div>关键规则由你确认<p>回答先整理成卡片，确认后才采用。</p></div></div></div></div>`;
  if (view === 'orders' || view === 'customers') {
    const orders = view === 'orders';
    const headers = orders ? ['order_id', 'customer_id', 'amount', 'status'] : ['customer_id', 'customer_name', 'region'];
    const rows = orders ? [['O001', 'C001', '120.00', 'paid'], ['O002', 'C002', '80.50', 'paid'], ['O003', 'C001', '50.00', 'refunded'], ['O004', 'C003', '199.00', 'pending'], ['O005', 'C002', '60.00', 'paid'], ['O006', 'C003', '320.00', 'paid']] : [['C001', '示例客户甲', '华东'], ['C002', '示例客户乙', '华南'], ['C003', '示例客户丙', '华东']];
    html = `<div class="section-kicker">资料预览 · 公开合成示例</div><h2>${sourceNames[view]}</h2><p class="lede">${rows.length} 行 · ${orders ? '一行代表一笔订单' : '一行代表一个客户'}</p><div class="sheet table-scroll"><table><thead><tr>${headers.map(header => `<th>${header}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(value => `<td>${value}</td>`).join('')}</tr>`).join('')}</tbody></table></div><p class="footnote">来自仓库公开示例，没有真实客户数据。版本和解析信息可在技术详情中查看。</p><details class="technical-details"><summary>技术详情</summary><p>资料版本 1；这里只预览用于演示的固定字段。</p></details><button class="text-button" data-view="relations">查看这些资料怎样关联</button>`;
  }
  if (view === 'notes') html = `<div class="section-kicker">资料说明 · 公开合成示例</div><h2>notes.md</h2>${sheet('资料已经明确的内容', '<p>orders.csv 一行代表一笔订单，customers.csv 一行代表一个客户。通过 <code>customer_id</code> 连接。</p><p>amount 的币种为人民币，单位为元；是否包含税费及折扣尚未确定。</p><p>paid_at 带有北京时间 UTC+08:00 的偏移；未支付订单可以为空。</p><p>按地区汇总金额时，纳入哪些订单状态、退款如何处理以及日期归属字段，需要业务确认。</p>')}`;
  if (view === 'relations') html = `<div class="section-kicker">理解资料</div><h2>订单可以连接到客户所在地区</h2><p class="lede">两份资料都包含客户标识。客户名称只用于展示，不用于判断唯一客户。</p>${sheet('已经找到的关系', '<div class="relationship"><div>订单资料<small>customer_id · 多笔订单</small></div><span>连接到</span><div>客户资料<small>customer_id · 一个客户</small></div></div><div class="note">可以使用 customers.region 作为地区维度。这条关系来自资料说明，仍需在正式资料中核对重复键和基数。</div><button class="text-button" data-view="orders">核对订单资料</button>')}${sheet('还需要你确认', '<p>状态范围、退款处理与日期归属尚未确定。金额中的税费及折扣也尚未知。</p>')}`;
  if (view === 'questions') html = `<div class="section-kicker">需要你确认</div><h2>这 3 个选择会改变统计结果</h2><p class="lede">可以在右侧一次回答，也可以先问为什么。</p>${sheet('按地区汇总订单金额', [['订单状态', '已支付、已退款、待支付，哪些应纳入？'], ['退款处理', '排除退款，还是把退款金额扣除？'], ['日期归属', '按支付时间，还是其他时间字段？']].map((question, index) => `<div class="question-row"><span class="q-index">0${index + 1}</span><div>${question[0]}<p>${question[1]}</p></div></div>`).join(''))}<div class="note">金额中的税费及折扣信息仍未知。讨论不会自动把这些内容变成已确认结论。</div>`;
  if (view === 'activity') html = `<div class="section-kicker">正在理解资料</div><h2>梳理字段、关系和业务问题</h2><p class="lede">分析会停在需要你判断的地方，等待你的回答。</p>${sheet('本轮正在做的事', '<div class="activity"><span class="activity-dot"></span><div>读取本次选择的资料<small>orders.csv、customers.csv、notes.md</small></div></div><div class="activity"><span class="activity-dot"></span><div>核对关联标识<small>两个资料中都出现 customer_id</small></div></div><div class="activity pending"><span class="activity-dot"></span><div>整理需要确认的业务口径<small>仍在分析</small></div></div>')}<button data-action="finish-analysis">原型控制：显示分析结果</button><p class="footnote">此按钮只用于切换合成场景，正式产品会在结果返回后自动更新。</p>`;
  if (view === 'result') html = `<div class="section-kicker">候选成果等待核对</div><h2>按地区汇总订单金额</h2><p class="lede">你确认的 3 条规则已经采用。下方只显示会影响业务理解的内容。</p>${sheet('本轮采用的规则', `<div class="receipt"><span>订单状态</span><strong>${escapeHtml(state.answers.status)}</strong></div><div class="receipt"><span>退款处理</span><strong>${escapeHtml(state.answers.refunds)}</strong></div><div class="receipt"><span>日期归属</span><strong>${escapeHtml(state.answers.time)}</strong></div><div class="receipt"><span>地区维度</span><strong>客户资料中的 region</strong></div>`)}${sheet('资料关系', '<div class="relationship"><div>订单资料<small>customer_id</small></div><span>连接到</span><div>客户资料<small>customer_id</small></div></div>')}<div class="notice">仍未知：金额是否包含税费及折扣。候选成果不会把这一项自动补全。</div><button class="text-button" data-view="notes">核对资料依据</button><details class="technical-details"><summary>技术详情</summary><p>候选定义版本 2 · 状态 partial · 未发布 Contract · 未完成 Mission。</p></details>`;
  if (['recovery', 'unknown', 'cancelled'].includes(view)) {
    const title = {recovery: '回答已保留，分析尚未启动', unknown: '先核对上次执行结果', cancelled: '分析已停止'}[view];
    const description = {recovery: '重试只作用于后续分析，不会重复确认已经保存的回答。', unknown: '连接中断不代表执行失败。结果确认之前，不发起第二次分析。', cancelled: '当前对话和资料范围已经保留，下一条消息由你决定何时发送。'}[view];
    const analysis = {recovery: '尚未启动', unknown: '结果未知', cancelled: '已经停止'}[view];
    html = `<div class="section-kicker">当前进展</div><h2>${title}</h2><p class="lede">${description}</p>${sheet('现在可以确定的状态', `<div class="receipt"><span>业务回答</span><strong>${state.approved ? '已确认' : '未确认'}</strong></div><div class="receipt"><span>本轮分析</span><strong>${analysis}</strong></div><div class="receipt"><span>正式成果</span><strong>尚未发布</strong></div>`)}<details class="technical-details"><summary>技术详情</summary><p>合成回执仅用于演示失败恢复，不代表真实执行记录。</p></details>`;
  }
  $('#content').innerHTML = html;
}

function followView(view) { if (state.following) state.view = view; }
function showView(view) {
  state.following = false;
  state.view = view;
  $('#app').classList.remove('nav-open');
  if (innerWidth <= 960) setMobile('workspace');
  renderContent();
  $('#follow-label').textContent = '你正在手动查看资料，新进展不会切换此页';
  $('#follow').hidden = false;
}

function finishAnalysis() {
  if (!state.running) return;
  clearTimeout(state.timer);
  state.running = false;
  state.scene = state.approved ? 'result' : 'question';
  followView(state.approved ? 'result' : 'questions');
  agent(state.approved ? resultMessage() : clarification());
  $('#scenario').value = state.scene;
  render();
}

function startAnalysis() {
  state.running = true;
  state.scene = 'analysis';
  followView('activity');
  agent(`<p>正在核对资料、整理${state.approved ? '已经确认的业务规则' : '字段关系与业务问题'}。</p>`);
  $('#scenario').value = 'analysis';
  render();
  state.timer = setTimeout(finishAnalysis, 3000);
}

function sendMessage() {
  if (state.running || state.scene === 'unknown') return;
  const text = $('#message').value.trim();
  if (!text) return;
  if (!state.connected) {
    state.pending = text;
    agent('<div class="notice">还没有连接模型。消息保留在输入区，连接后可以继续发送。</div><button class="primary" data-action="settings">连接模型</button>');
    render();
    return;
  }
  if (!state.sources.includes('orders') || !state.sources.includes('customers')) {
    toast('这个合成场景需要订单和客户资料，请先在资料范围中选中。');
    $('#source-scope').open = true;
    return;
  }
  $('#message').value = '';
  state.pending = '';
  user(text);
  if (['question', 'answer'].includes(state.scene)) {
    if (/为什么|解释|如何|什么意思/.test(text)) {
      agent('<p>退款是否纳入会直接改变汇总结果。例如，示例订单 O003 的状态是 refunded。如果不知道你的处理规则，我不能替你决定它应该计入、排除还是扣减。</p><p><button class="citation" data-view="orders">查看这笔样例订单</button></p>');
    } else if (text === initialAnswer) {
      state.scene = 'answer';
      agent('<p>我已把这句话拆成 3 条可核对的业务规则。确认前都不会采用。</p>' + answerCard());
    } else {
      agent(`<p>收到。这个固定原型不会猜测你没有说清的内容。你可以使用公开示例回答体验完整核对流程。</p>${nextActions('继续体验回答与确认', [`<button class="primary" data-prompt="${initialAnswer}">使用完整示例回答</button>`])}`);
    }
  } else if (state.scene === 'discussion' && /怎么做|如何开始|下一步/.test(text)) {
    agent(`<p>下一步只要选择一个具体结果，不需要创建任务或另点运行。你可以直接按下面的示例开始。</p>${nextActions('选择这次要整理的业务结果', [`<button class="primary" data-prompt="${initialRequest}">按地区整理订单金额</button>`, '<button data-prompt="先解释一下两张表的关系。">继续解释表关系</button>'])}`);
  } else if (state.scene === 'discussion' && /看看|了解|关系|字段|资料|表/.test(text)) {
    agent(`<p>这 3 份资料已经说明过：订单表记录交易，客户表补充地区，说明文件记录已知口径。现在还需要你选一个想得到的结果。</p>${nextActions('继续选择业务目标', [`<button class="primary" data-prompt="${initialRequest}">按地区整理订单金额</button>`, '<button data-prompt="先解释一下两张表的关系。">继续解释表关系</button>'])}`);
  } else if (/按地区|统计|整理订单金额/.test(text)) {
    startAnalysis();
    return;
  } else if (/看看|了解|关系|字段|资料|表/.test(text)) {
    state.scene = 'discussion';
    followView('relations');
    agent(discussion());
  } else {
    agent(`<p>这条消息已留在当前对话。为了继续这个固定示例，请选择一个具体结果；原型不会把“规范一下”之类的模糊目标直接当成业务任务。</p>${nextActions('选择一个可体验的下一步', [`<button class="primary" data-prompt="${initialRequest}">按地区整理订单金额</button>`, '<button data-action="understand-sources">重新说明资料</button>'])}`);
  }
  render();
}

$('#composer').addEventListener('submit', event => { event.preventDefault(); sendMessage(); });
$('#message').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(); }
});
$('#scenario').addEventListener('change', event => setScene(event.target.value));
$('#stop').addEventListener('click', () => {
  clearTimeout(state.timer);
  state.running = false;
  state.scene = 'cancelled';
  followView('cancelled');
  agent('<p>分析已停止。当前草稿和资料都已保留，没有自动发送下一条消息。</p>');
  render();
});
$('#follow').addEventListener('click', () => {
  state.following = true;
  state.view = ({entry: 'intro', discussion: 'relations', connect: 'intro', analysis: 'activity', question: 'questions', answer: 'questions', result: 'result', failed: 'recovery', unknown: 'unknown', cancelled: 'cancelled'})[state.scene];
  render();
});
$('#expand').addEventListener('click', () => {
  const expanded = $('#app').classList.toggle('expanded');
  $('#expand').textContent = expanded ? '恢复双栏' : '展开对话';
  $('#expand').setAttribute('aria-pressed', String(expanded));
  $('#expand').setAttribute('aria-label', expanded ? '恢复双栏' : '展开对话');
});

function setMobile(target) {
  $('#app').classList.toggle('mobile-workspace', target === 'workspace');
  $$('[data-mobile]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mobile === target)));
}

$('#nav-toggle').addEventListener('click', () => $('#app').classList.toggle('nav-open'));
$$('[data-mobile]').forEach(button => button.addEventListener('click', () => setMobile(button.dataset.mobile)));
$('#workspace-choice').addEventListener('click', () => toast('原型只有一个本地工作区，资料不会跨工作区使用。'));
$$('[data-source]').forEach(input => input.addEventListener('change', () => {
  state.sources = $$('[data-source]:checked').map(element => element.dataset.source);
  $('#scope-label').textContent = `本次对话使用 ${state.sources.length} 份资料`;
  toast('当前对话的资料范围已更新。');
}));
$('#file').addEventListener('change', event => {
  const file = event.target.files[0];
  if (file) $('#file-notice').textContent = `已选择文件名：${file.name}。原型不会读取或上传文件。`;
});
document.addEventListener('input', event => {
  if (event.target.closest('.answer-card') && event.target.matches('input,textarea')) {
    state.answers[event.target.id.replace('answer-', '')] = event.target.value;
    state.version += 1;
    state.saved = false;
    updateTaskStatus();
    const saveNote = $('#save-note');
    if (saveNote) saveNote.textContent = '内容已修改，需要重新确认后才会采用。';
  }
});
document.addEventListener('click', event => {
  const target = event.target.closest('button');
  if (!target) return;
  if (target.dataset.view) { showView(target.dataset.view); return; }
  if (target.dataset.prompt) { $('#message').value = target.dataset.prompt; sendMessage(); return; }
  const action = target.dataset.action;
  if (action === 'new') { setScene('entry'); $('#app').classList.remove('nav-open'); setMobile('chat'); }
  if (action === 'recent') setScene('discussion');
  if (action === 'settings') { $('#mock-connected').value = state.connected ? 'yes' : 'no'; $('#save-send').disabled = !$('#message').value.trim(); $('#settings').showModal(); }
  if (action === 'sources') $('#source-scope').open = !$('#source-scope').open;
  if (action === 'understand-sources') { $('#message').value = '先帮我看看这三份资料。'; sendMessage(); }
  if (action === 'attach') $('#file').click();
  if (action === 'finish-analysis') finishAnalysis();
  if (action === 'save-answer') {
    state.saved = true;
    updateTaskStatus();
    $('#save-note').textContent = '回答已经保存，但尚未确认，也没有开始后续分析。';
    toast('回答已保存，稍后仍可继续确认。');
  }
  if (action === 'confirm') {
    const fields = $$('.answer-card input, .answer-card textarea');
    if (fields.some(field => !field.value.trim())) { toast('请填写完整回答；不知道的内容可以明确写“未知”。'); return; }
    if (state.approved || state.running) return;
    state.approved = true;
    target.disabled = true;
    const summary = fields.slice(0, 3).map(field => escapeHtml(field.value)).join('；');
    state.messages = state.messages.map(message => message.body.includes('data-answer-card="true"') ? {
      role: 'agent',
      body: `<div class="answer-card confirmed"><h3>业务回答已确认</h3><p>${summary}</p><details class="technical-details"><summary>技术详情</summary><p>精确回答版本 ${state.version} 的批准回执已保存。</p></details></div>`
    } : message);
    startAnalysis();
  }
  if (action === 'retry' && state.resumeFailed && !state.running) { state.resumeFailed = false; startAnalysis(); }
  if (action === 'reconcile') {
    state.scene = 'cancelled';
    followView('cancelled');
    agent('<p>核对完成（合成回执）：上次分析已经停止，没有产生新的候选成果。现在可以继续发送消息。</p>');
    render();
  }
});

$('#save-settings').addEventListener('click', () => {
  state.connected = $('#mock-connected').value === 'yes';
  $('#settings').close();
  toast(state.connected ? '连接设置已保存，消息还没有发送。' : '当前仍未连接，消息已保留。');
});
$('#save-send').addEventListener('click', () => {
  state.connected = $('#mock-connected').value === 'yes';
  $('#settings').close();
  if (state.connected) sendMessage(); else toast('尚未连接。消息已保留，没有发送。');
});

function sidebarWidth() { return innerWidth > 1160 ? 224 : 188; }
function bounds() {
  const available = innerWidth - sidebarWidth() - 7;
  return {min: 360, max: Math.max(360, available - 360), default: Math.min(640, Math.max(360, available * 0.44))};
}
let savedWidth;
try { savedWidth = Number(localStorage.getItem('contextox-prototype-agent-width')) || null; } catch { savedWidth = null; }
function applyWidth(width, persist = false) {
  const allowed = bounds();
  const value = Math.round(Math.min(allowed.max, Math.max(allowed.min, width)));
  document.documentElement.style.setProperty('--agent-width', `${value}px`);
  $('#splitter').setAttribute('aria-valuenow', value);
  $('#splitter').setAttribute('aria-valuemax', Math.round(allowed.max));
  if (persist) {
    savedWidth = value;
    try { localStorage.setItem('contextox-prototype-agent-width', String(value)); } catch { /* Preference persistence is optional. */ }
  }
}
const splitter = $('#splitter');
splitter.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  splitter.setPointerCapture(event.pointerId);
  splitter.classList.add('dragging');
});
splitter.addEventListener('pointermove', event => {
  if (splitter.hasPointerCapture(event.pointerId)) applyWidth(innerWidth - event.clientX, true);
});
for (const name of ['pointerup', 'pointercancel']) splitter.addEventListener(name, event => {
  if (splitter.hasPointerCapture(event.pointerId)) splitter.releasePointerCapture(event.pointerId);
  splitter.classList.remove('dragging');
});
splitter.addEventListener('dblclick', () => {
  savedWidth = null;
  try { localStorage.removeItem('contextox-prototype-agent-width'); } catch { /* Preference persistence is optional. */ }
  applyWidth(bounds().default);
});
splitter.addEventListener('keydown', event => {
  const width = Number(splitter.getAttribute('aria-valuenow'));
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault();
    applyWidth(width + (event.key === 'ArrowLeft' ? 24 : -24), true);
  }
  if (event.key === 'Home') { event.preventDefault(); applyWidth(bounds().default, true); }
});
window.addEventListener('resize', () => applyWidth(savedWidth || bounds().default));

applyWidth(savedWidth || bounds().default);
setScene('entry');
