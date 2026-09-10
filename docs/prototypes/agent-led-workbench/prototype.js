/* Local synthetic interaction prototype. No network, model, or file-content access. */
'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (text) => String(text).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = { scene:'entry', following:true, view:'intro', connected:true, running:false, approved:false, saved:false, version:1, messages:[], pending:'', sources:['orders','customers','notes'], timer:null, resumeFailed:false };
const initialRequest = '我想按地区统计订单金额。';
const defaultAnswers = {status:'仅纳入已支付（paid）订单',refunds:'已退款（refunded）订单不纳入',time:'按 paid_at，使用北京时间 UTC+08:00',basis:'用户在当前对话明确给出的业务规则；字段和时区来自 notes.md。'};
state.answers={...defaultAnswers};
const initialAnswer = '只统计已支付订单，退款订单不纳入，按支付时间归属日期。';
const labels = ['明确目标','理解资料','澄清口径','整理成果'];
const sourceNames = {orders:'orders.csv',customers:'customers.csv',notes:'notes.md'};
const toast = (message) => { $('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,3500); };
function addMessage(role, body) { state.messages.push({role,body}); }
function agent(body) {addMessage('agent',body);}
function user(text) {addMessage('user',escapeHtml(text));}
const welcome = () => '<p>你好，有什么想一起弄清楚的？</p><p>可以先聊业务问题，也可以从手头的资料开始。我会把相关资料和整理过程放在左边，方便你随时核对。</p><div class="suggestions"><button data-prompt="先帮我看看这两张表。">先帮我看看这两张表</button><button data-prompt="我想按地区统计订单金额。">按地区统计订单金额</button></div>';
const discussion = () => '<p>这两张表可以通过 <button class="citation" data-view="relations">customer_id</button> 关联：一张记录订单，一张补充客户所在地区。</p><p>订单里有已支付、已退款和待支付三种样例。金额单位是人民币元，但税费、折扣口径尚未确定。<button class="citation" data-view="notes">资料说明 ↗</button></p><p>你想先弄清字段关系，还是按地区整理订单金额？</p><div class="suggestions"><button data-prompt="我想按地区统计订单金额。">按地区整理订单金额</button><button data-prompt="先解释一下两张表的关系。">解释两张表的关系</button></div>';
const clarification = () => '<p>可以关联资料了。不过，“订单金额”还有三处业务口径需要你确定：</p><ul><li>哪些订单状态需要计入？</li><li>已退款订单如何处理？</li><li>用哪个时间字段归属日期？</li></ul><p>你可以直接告诉我，我会整理成一份回答供你核对。</p><div class="suggestions"><button data-prompt="只统计已支付订单，退款订单不纳入，按支付时间归属日期。">使用示例回答</button><button data-prompt="为什么需要确认退款规则？">为什么要确认退款规则？</button></div>';
function answerCard() {return '<div class="answer-card"><h3>核对这份业务回答</h3><div class="meta">待确认 · 回答版本 <span id="answer-version">'+state.version+'</span> · 来源：本次用户回答</div><label class="field">计入哪些订单状态<input id="answer-status" value="'+escapeHtml(state.answers.status)+'"></label><label class="field">退款如何处理<input id="answer-refunds" value="'+escapeHtml(state.answers.refunds)+'"></label><label class="field">日期如何归属<input id="answer-time" value="'+escapeHtml(state.answers.time)+'"></label><label class="field">回答依据<textarea id="answer-basis" rows="2">'+escapeHtml(state.answers.basis)+'</textarea></label><div class="notice">仍未知：金额是否包含税费及折扣。保留未知，不据此推断业务结论。</div><div class="card-actions"><button class="primary" data-action="confirm">确认并继续</button><button data-action="save-answer">仅保存，稍后继续</button></div><div class="footnote" id="save-note">自然语言回答尚未批准。确认针对上面显示的精确版本。</div></div>';}
function setScene(scene) {
 clearTimeout(state.timer); state.answers={...defaultAnswers}; Object.assign(state,{scene,following:true,view:'intro',running:false,approved:false,saved:false,version:1,messages:[],pending:'',resumeFailed:false,sources:['orders','customers','notes'],connected:scene!=='connect'}); $('#message').value='';$('#source-scope').open=false;$('#scenario').value=scene;$$('[data-source]').forEach(input=>input.checked=true);
 if(scene==='entry'){agent(welcome());}
 if(scene==='discussion'){user('先帮我看看这两张表。');agent(discussion());state.view='relations';}
 if(['analysis','question','answer','result','failed','unknown','cancelled'].includes(scene)){user(initialRequest);agent('<p>好，我会从订单和客户资料开始，整理出按地区汇总所需的字段与口径。相关过程会同步显示在工作区。</p>');state.view='activity';}
 if(scene==='analysis'){state.running=true;agent('<p>正在核对两个资料的字段和关联关系……</p>');}
 if(['question','answer','result','failed'].includes(scene)){agent(clarification());state.view='questions';}
 if(scene==='answer'){user(initialAnswer);agent('<p>我把你的回答整理如下。你可以直接修改，再确认继续。</p>'+answerCard());}
 if(scene==='result'){user(initialAnswer);state.approved=true;state.view='result';agent(resultMessage());}
 if(scene==='failed'){user(initialAnswer);state.approved=true;state.resumeFailed=true;state.view='recovery';agent(failedMessage());}
 if(scene==='unknown'){state.view='unknown';agent('<div class="notice error">连接中断，暂时无法确认这次分析的结果。请先核对结果，再决定下一步。</div><button data-action="reconcile">核对执行结果</button>');}
 if(scene==='cancelled'){state.view='cancelled';agent('<p>这次分析已停止。已有资料和对话保留，你可以继续讨论。</p><div class="suggestions"><button data-prompt="继续按地区整理订单金额。">继续分析</button></div>');}
 if(scene==='connect'){state.pending='先帮我看看这两张表。';$('#message').value=state.pending;agent(welcome());agent('<div class="notice">还没有连接模型。你的消息已保留，连接后可以继续。</div><button data-action="settings">连接模型</button>');}
 render();
}
function resultMessage(){return '<p>回答已确认，草案已更新。<button class="citation" data-view="result">查看本次变化 ↗</button></p><p>已纳入你确认的订单状态、退款处理与日期归属规则。金额中的税费、折扣仍未知，所以目前保留为<strong>不完整的候选草案</strong>。</p><p>你可以检查左侧草案，也可以继续补充口径。</p>';}
function failedMessage(){return '<p>业务回答已经确认，但后续分析尚未启动。</p><div class="answer-card"><div class="receipt"><span>回答版本 1 的批准</span><span>已成功保存</span></div><div class="receipt"><span>后续分析</span><span>启动失败 · 尚未运行</span></div><div class="notice error">合成故障：连接暂时不可用。已确认的回答会保留。</div><button class="primary" data-action="retry">重试后续分析</button></div>';}
function stage(){if(state.scene==='entry'||state.scene==='discussion'||state.scene==='connect')return 0;if(['question','answer','failed'].includes(state.scene))return 2;if(state.scene==='result')return 3;return 1;}
function render(){
 state.messages=state.messages.map(m=>m.body.includes('id="answer-version"')?{...m,body:'<p>请核对或修改这份业务回答。</p>'+answerCard()}:m);
 $('#messages').innerHTML=state.messages.map(m=>'<div class="message '+m.role+'">'+(m.role==='agent'?'<div class="sender">数契</div>':'')+m.body+'</div>').join('');
 $('#messages').scrollTop=$('#messages').scrollHeight;
 const step=stage();$('#progress').innerHTML=labels.map((label,i)=>'<li class="'+(i===step?'current':i<step?'past':'')+'"'+(i===step?' aria-current="step"':'')+'><span class="step-index">'+(i<step?'✓':i+1)+'</span>'+label+'</li>').join('');
 const noTask=['entry','discussion','connect'].includes(state.scene);$('#task-title').textContent=noTask?'从一个问题开始':'按地区理解订单金额';
 const statuses={entry:'尚未形成任务',discussion:'讨论中',connect:'等待连接',analysis:'正在理解资料',question:'3 个问题待回答',answer:state.saved?'回答已保存 · 待确认':'待确认回答',result:'候选草案 · 不完整',failed:'已批准 · 续接失败',unknown:'执行结果未知',cancelled:'已停止'};
 $('#task-status').textContent=statuses[state.scene]||'讨论中';$('#task-status').className='badge '+(['question','answer','failed','unknown'].includes(state.scene)?'amber':'blue');
 $('#agent-status').textContent=state.running?'正在分析 · 可以停止':noTask?'一起把问题弄清楚':state.scene==='result'?'草案已更新，仍有未知事项':'对话与资料持续保留';
 $('#send').disabled=state.running||state.scene==='unknown';$('#send').hidden=state.running;$('#stop').hidden=!state.running;
 $('#composer-notice').textContent=state.running?'分析进行中。你可以编辑下一条草稿，停止或结束后再发送。':state.scene==='unknown'?'请先核对上次执行结果。消息草稿会保留，不自动重试。':'';
 $('#scope-label').textContent='本次对话使用 '+state.sources.length+' 份资料';$('#follow-label').textContent=state.following?'跟随对话展示相关内容':'你正在查看资料 · 新进展不会切换此视图';$('#follow').hidden=state.following;
 renderContent();
}
function sheet(title,body){return '<section class="sheet"><div class="sheet-head">'+title+'</div><div class="sheet-body">'+body+'</div></section>';}
function renderContent(){let html=''; const view=state.view;
 if(view==='intro')html='<div class="empty-intro"><img class="intro-logo" src="./assets/contextox-mark.png" alt=""><div class="section-kicker">从讨论到清晰的业务口径</div><h2>说出问题，<br>一起找到有依据的答案。</h2><p class="lede">在右侧告诉 Agent 你想弄清什么。这里会随着对话，展示资料、待确认的问题和逐步形成的成果。</p><div class="intro-steps"><div class="intro-row"><span>01</span><div>从你的问题开始<p>不必预先定义任务，先聊业务背景。</p></div></div><div class="intro-row"><span>02</span><div>随时核对资料依据<p>让字段、关系和未知事项有处可查。</p></div></div><div class="intro-row"><span>03</span><div>关键口径，由你确认<p>自然语言补充，整理后再核对采用。</p></div></div></div></div>';
 if(view==='orders'||view==='customers'){const orders=view==='orders';const headers=orders?['order_id','customer_id','amount','status']:['customer_id','customer_name','region'];const rows=orders?[['O001','C001','120.00','paid'],['O002','C002','80.50','paid'],['O003','C001','50.00','refunded'],['O004','C003','199.00','pending'],['O005','C002','60.00','paid'],['O006','C003','320.00','paid']]:[['C001','示例客户甲','华东'],['C002','示例客户乙','华南'],['C003','示例客户丙','华东']];html='<div class="section-kicker">资料预览 · 公开合成示例</div><h2>'+sourceNames[view]+'</h2><p class="lede">示例版本 1 · '+rows.length+' 行 · '+(orders?'一行代表一笔订单':'一行代表一个客户')+'</p><div class="sheet table-scroll"><table><thead><tr>'+headers.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('')+'</tbody></table></div><p class="footnote">来自仓库公开 demo。订单支付时间请查看资料说明；本页表格仅预览部分字段。没有真实客户数据。</p><button class="text-button" data-view="relations">查看关联字段 →</button>';}
 if(view==='notes')html='<div class="section-kicker">资料说明 · 公开合成示例</div><h2>notes.md</h2>'+sheet('资料明确说明的内容','<p>orders.csv 一行代表一笔订单，customers.csv 一行代表一个客户。通过 <code>customer_id</code> 连接。</p><p>amount 的币种为人民币，单位为元；是否包含税费及折扣尚未确定。</p><p>paid_at 带有北京时间 UTC+08:00 的偏移；未支付订单可以为空。</p><p>按地区汇总金额时，纳入哪些订单状态、退款如何处理以及日期归属字段，需要业务确认。</p>');
 if(view==='relations')html='<div class="section-kicker">理解资料</div><h2>从订单连接到地区</h2><p class="lede">两份资料通过客户标识关联。名称只用于展示，不用于确认唯一客户。</p>'+sheet('可核对的关联关系','<div class="relationship"><div>orders.csv<small>customer_id · 多笔订单</small></div><span>→</span><div>customers.csv<small>customer_id · 一个客户</small></div></div><div class="note">使用 customers.region 作为地区维度。关联字段来自公开资料说明。</div><button class="text-button" data-view="orders">查看订单资料 →</button>')+sheet('仍需要业务确认','<p>状态范围、退款处理与日期归属尚未确定。金额中的税费及折扣也尚未知。</p>');
 if(view==='questions')html='<div class="section-kicker">澄清口径</div><h2>先确定这 3 件事</h2><p class="lede">不同的规则会改变统计结果。你可以在右侧一次回答，也可以逐项讨论。</p>'+sheet('按地区汇总订单金额',[['订单状态','已支付、已退款、待支付，哪些应纳入？'],['退款处理','是否排除退款，或需要扣减退款金额？'],['日期归属','按支付时间还是其他时间字段归属？']].map((q,i)=>'<div class="question-row"><span class="q-index">0'+(i+1)+'</span><div>'+q[0]+'<p>'+q[1]+'</p></div></div>').join(''))+'<div class="note">金额税费及折扣信息仍未知。讨论不会自动批准业务事实。</div>';
 if(view==='activity')html='<div class="section-kicker">理解资料</div><h2>正在梳理字段与关系</h2><p class="lede">分析会停在需要你判断的地方，等待你的回答。</p>'+sheet('本轮进展','<div class="activity"><span class="activity-dot"></span><div>读取资料范围<small>orders.csv、customers.csv、notes.md · 示例版本 1</small></div></div><div class="activity"><span class="activity-dot"></span><div>核对关联标识<small>customer_id 在两个资料中均出现</small></div></div><div class="activity pending"><span class="activity-dot"></span><div>整理需要确认的业务口径<small>正在分析 · 合成状态</small></div></div>')+'<button data-action="finish-analysis">演示：收到分析结果</button><p class="footnote">此按钮是原型时间控制，不属于正式产品操作。</p>';
 if(view==='result')html='<div class="section-kicker">整理成果 · 候选版本 2</div><h2>订单金额口径草案</h2><p class="lede">你刚确认的规则已纳入，仍有未知事项需要保留。</p>'+sheet('本次更新','<div class="receipt"><span>订单状态</span><span>'+escapeHtml(state.answers.status)+'</span></div><div class="receipt"><span>退款处理</span><span>'+escapeHtml(state.answers.refunds)+'</span></div><div class="receipt"><span>日期归属</span><span>'+escapeHtml(state.answers.time)+'</span></div><div class="receipt"><span>地区维度</span><span>customers.region</span></div>')+'<div class="notice">候选草案 · partial<br>金额是否包含税费及折扣尚未确定。本轮没有发布正式 Contract，也没有完成 Mission。</div><button class="text-button" data-view="notes">核对资料依据 →</button>';
 if(['recovery','unknown','cancelled'].includes(view))html='<div class="section-kicker">当前进展</div><h2>'+({recovery:'回答已保留，分析尚未启动',unknown:'先核对上次执行结果',cancelled:'分析已停止'}[view])+'</h2><p class="lede">'+({recovery:'重试只作用于后续分析，不会重复批准已保存的回答。',unknown:'连接中断不等于执行失败。结果确认之前，不发起第二次分析。',cancelled:'当前对话和资料范围保留，下一条消息由你决定何时发送。'}[view])+'</p>'+sheet('已知状态','<div class="receipt"><span>回答批准</span><span>'+(state.approved?'成功':'未发生')+'</span></div><div class="receipt"><span>本轮分析</span><span>'+({recovery:'未启动',unknown:'未知',cancelled:'cancelled'}[view])+'</span></div><div class="receipt"><span>正式成果发布</span><span>未发生</span></div>');
 $('#content').innerHTML=html;
}
function followView(view){if(state.following)state.view=view;}
function showView(view){state.following=false;state.view=view;$('#app').classList.remove('nav-open');if(innerWidth<=960)setMobile('workspace');renderContent();$('#follow-label').textContent='你正在查看资料 · 新进展不会切换此视图';$('#follow').hidden=false;}
function finishAnalysis(){if(!state.running)return;clearTimeout(state.timer);state.running=false;state.scene=state.approved?'result':'question';followView(state.approved?'result':'questions');agent(state.approved?resultMessage():clarification());$('#scenario').value=state.scene;render();}
function startAnalysis(){state.running=true;state.scene='analysis';followView('activity');agent('<p>正在核对资料、整理'+(state.approved?'已确认的业务规则':'字段与业务问题')+'……</p>');$('#scenario').value='analysis';render();state.timer=setTimeout(finishAnalysis,3000);}
function sendMessage(){if(state.running||state.scene==='unknown')return;const text=$('#message').value.trim();if(!text)return;if(!state.connected){state.pending=text;agent('<div class="notice">还没有连接模型。消息保留在输入区，连接后可以发送。</div><button data-action="settings">连接模型</button>');render();return;}if(!state.sources.includes('orders')||!state.sources.includes('customers')){toast('这个合成场景需要 orders.csv 和 customers.csv，请在资料范围中选中。');$('#source-scope').open=true;return;}$('#message').value='';state.pending='';user(text);
 if(['question','answer'].includes(state.scene)){if(/为什么|解释|如何|什么意思/.test(text)){agent('<p>退款是否纳入会改变汇总口径。例如，示例订单 O003 的状态是 refunded。如果不知道你的处理规则，我不能替你决定它应计入还是扣减。</p><p>你可以一次告诉我订单状态、退款处理和日期归属。<button class="citation" data-view="orders">查看样例订单 ↗</button></p>');}else if(text===initialAnswer){state.scene='answer';agent('<p>以下是固定示例回答的整理卡片，请核对或修改。</p>'+answerCard());}else{agent('<p>收到。这是固定场景原型，暂不理解任意回答，也不会自动补齐缺失口径。请使用示例回答查看完整核对流程，或继续说明未知事项。</p><div class="suggestions"><button data-prompt="'+initialAnswer+'">使用完整示例回答</button></div>');}}
 else if(/按地区|统计|整理订单金额/.test(text)){startAnalysis();return;}
 else if(/看看|关系|字段|表/.test(text)){state.scene='discussion';followView('relations');agent(discussion());}
 else{agent('<p>消息已显示在本页对话中。此原型只演示公开示例的固定流程，不调用真实 AI。你可以用下面的示例继续体验。</p><div class="suggestions"><button data-prompt="先帮我看看这两张表。">先讨论资料</button><button data-prompt="我想按地区统计订单金额。">开始整理订单金额</button></div>');}
 render();
}
$('#composer').addEventListener('submit',event=>{event.preventDefault();sendMessage();});$('#message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();sendMessage();}});
$('#scenario').addEventListener('change',event=>setScene(event.target.value));
$('#stop').addEventListener('click',()=>{clearTimeout(state.timer);state.running=false;state.scene='cancelled';followView('cancelled');agent('<p>分析已停止，当前草稿和资料保留。没有自动发送下一条消息。</p>');render();});
$('#follow').addEventListener('click',()=>{state.following=true;state.view=({entry:'intro',discussion:'relations',connect:'intro',analysis:'activity',question:'questions',answer:'questions',result:'result',failed:'recovery',unknown:'unknown',cancelled:'cancelled'})[state.scene];render();});
$('#expand').addEventListener('click',()=>{const expanded=$('#app').classList.toggle('expanded');$('#expand').textContent=expanded?'恢复双栏':'展开';$('#expand').setAttribute('aria-pressed',String(expanded));$('#expand').setAttribute('aria-label',expanded?'恢复双栏':'展开对话');});
function setMobile(target){$('#app').classList.toggle('mobile-workspace',target==='workspace');$$('[data-mobile]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mobile===target)));}
$('#nav-toggle').addEventListener('click',()=>$('#app').classList.toggle('nav-open'));$$('[data-mobile]').forEach(b=>b.addEventListener('click',()=>setMobile(b.dataset.mobile)));
$('#workspace-choice').addEventListener('click',()=>toast('原型仅有一个本地工作区，资料不会跨工作区使用。'));
$$('[data-source]').forEach(input=>input.addEventListener('change',()=>{state.sources=$$('[data-source]:checked').map(el=>el.dataset.source);$('#scope-label').textContent='本次对话使用 '+state.sources.length+' 份资料';toast('当前对话资料范围已更新（仅本页合成状态）。');}));
$('#file').addEventListener('change',event=>{const file=event.target.files[0];if(file)$('#file-notice').textContent='已选择文件名：'+file.name+'。原型不读取、不上传、不加入分析。';});
document.addEventListener('input',event=>{if(event.target.closest('.answer-card')&&event.target.matches('input,textarea')){state.answers[event.target.id.replace('answer-','')]=event.target.value;state.version++;state.saved=false;const version=$('#answer-version');if(version)version.textContent=state.version;$('#save-note').textContent='内容已修改：需确认新版本 '+state.version+'，此前保存不代表批准。';}});
document.addEventListener('click',event=>{const target=event.target.closest('button');if(!target)return;if(target.dataset.view){showView(target.dataset.view);return;}if(target.dataset.prompt){$('#message').value=target.dataset.prompt;sendMessage();return;}const action=target.dataset.action;
 if(action==='new'){setScene('entry');$('#app').classList.remove('nav-open');setMobile('chat');}
 if(action==='recent')setScene('discussion');
 if(action==='settings'){ $('#mock-connected').value=state.connected?'yes':'no';$('#save-send').disabled=!$('#message').value.trim();$('#settings').showModal();}
 if(action==='sources')$('#source-scope').open=!$('#source-scope').open;
 if(action==='attach')$('#file').click();
 if(action==='finish-analysis')finishAnalysis();
 if(action==='save-answer'){state.saved=true;$('#save-note').textContent='回答版本 '+state.version+' 已保存（合成状态）。尚未批准，也未开始后续分析。';toast('回答已保存，稍后仍需确认。');}
 if(action==='confirm'){const fields=$$('.answer-card input, .answer-card textarea');if(fields.some(el=>!el.value.trim())){toast('请填写完整回答；未知可以明确写为“未知”。');return;}if(state.approved||state.running)return;state.approved=true;target.disabled=true;const summary=fields.map(el=>escapeHtml(el.value)).join('；');state.messages=state.messages.map(m=>m.body.includes('id="answer-version"')?{role:'agent',body:'<div class="answer-card"><h3>回答版本 '+state.version+' 已确认</h3><p>'+summary+'</p><span class="meta">批准已保存 · 合成回执</span></div>'}:m);startAnalysis();}
 if(action==='retry'){if(!state.resumeFailed||state.running)return;state.resumeFailed=false;startAnalysis();}
 if(action==='reconcile'){state.scene='cancelled';followView('cancelled');agent('<p>核对完成（合成回执）：上次分析已停止，没有草案更新。现在可以继续发送消息。</p>');render();}
});
$('#save-settings').addEventListener('click',()=>{state.connected=$('#mock-connected').value==='yes';$('#settings').close();toast(state.connected?'合成连接设置已保存，未发送消息。':'合成状态已设为未连接，消息保留。');});
$('#save-send').addEventListener('click',()=>{state.connected=$('#mock-connected').value==='yes';$('#settings').close();if(state.connected)sendMessage();else toast('尚未连接。消息已保留，未发送。');});
function sidebarWidth(){return innerWidth>1160?212:180;}
function bounds(){const available=innerWidth-sidebarWidth()-7;return {min:360,max:Math.max(360,available-360),default:Math.min(640,Math.max(360,available*.44))};}
let savedWidth;try{savedWidth=Number(localStorage.getItem('contextox-prototype-agent-width'))||null;}catch{savedWidth=null;}
function applyWidth(width,persist=false){const b=bounds();const value=Math.round(Math.min(b.max,Math.max(b.min,width)));document.documentElement.style.setProperty('--agent-width',value+'px');$('#splitter').setAttribute('aria-valuenow',value);$('#splitter').setAttribute('aria-valuemax',Math.round(b.max));if(persist){savedWidth=value;try{localStorage.setItem('contextox-prototype-agent-width',String(value));}catch{/* Preference persistence is optional. */}}}
const splitter=$('#splitter');splitter.addEventListener('pointerdown',event=>{if(event.button!==0)return;splitter.setPointerCapture(event.pointerId);splitter.classList.add('dragging');});splitter.addEventListener('pointermove',event=>{if(splitter.hasPointerCapture(event.pointerId))applyWidth(innerWidth-event.clientX,true);});for(const name of ['pointerup','pointercancel'])splitter.addEventListener(name,event=>{if(splitter.hasPointerCapture(event.pointerId))splitter.releasePointerCapture(event.pointerId);splitter.classList.remove('dragging');});splitter.addEventListener('dblclick',()=>{savedWidth=null;try{localStorage.removeItem('contextox-prototype-agent-width');}catch{}applyWidth(bounds().default);});splitter.addEventListener('keydown',event=>{const width=Number(splitter.getAttribute('aria-valuenow'));if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();applyWidth(width+(event.key==='ArrowLeft'?24:-24),true);}if(event.key==='Home'){event.preventDefault();applyWidth(bounds().default,true);}});window.addEventListener('resize',()=>applyWidth(savedWidth||bounds().default));
applyWidth(savedWidth||bounds().default);setScene('entry');
