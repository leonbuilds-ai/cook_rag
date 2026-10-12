/* Same-origin website. No CDN, browser framework or external font is required. */
'use strict';
const $ = (selector) => document.querySelector(selector);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {sessionId:null, messages:[], busy:false, serverId:null, lastActive:0, key:''};
let toastTimer, thinkingTimer;
function safeUrl(value) {
  try { const url = new URL(value, location.origin); return ['https:','http:'].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}
function inline(text) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]\n]+\]\([^\s)]+\)|\[S\d+\])/g;
  let out = '', start = 0;
  for (const match of text.matchAll(pattern)) {
    out += escapeHtml(text.slice(start, match.index));
    const token = match[0];
    if (token.startsWith('`')) out += `<code>${escapeHtml(token.slice(1,-1))}</code>`;
    else if (token.startsWith('**')) out += `<strong>${escapeHtml(token.slice(2,-2))}</strong>`;
    else if (/^\[S\d+\]$/.test(token)) out += `<button class="citation" data-source-id="${token.slice(1,-1)}" title="查看食谱原文">${token}</button>`;
    else { const parts = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/); const url = safeUrl(parts[2]);
      out += url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(parts[1])}</a>` : escapeHtml(parts[1]); }
    start = match.index + token.length;
  }
  return out + escapeHtml(text.slice(start));
}
function markdown(text) {
  const lines = String(text).replace(/\r/g,'').split('\n');
  let html = '', paragraph = [], list = null, code = null;
  const flush = () => { if (paragraph.length) { html += `<p>${paragraph.map(inline).join('<br>')}</p>`; paragraph = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().startsWith('```')) { flush(); closeList(); if (code !== null) {html += `<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`; code = null;} else code = []; continue; }
    if (code !== null) { code.push(line); continue; }
    if (!line.trim()) { flush(); closeList(); continue; }
    const heading = line.match(/^\s*(#{1,4})\s+(.+)$/);
    if (heading) { flush(); closeList(); const level = Math.min(heading[1].length,4); html += `<h${level}>${inline(heading[2])}</h${level}>`; continue; }
    if (/^\s*([-*_])\1\1+\s*$/.test(line)) { flush(); closeList(); html += '<hr>'; continue; }
    if (/^\s*>\s?/.test(line)) { flush(); closeList(); html += `<blockquote>${inline(line.replace(/^\s*>\s?/,''))}</blockquote>`; continue; }
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i+1])) {
      flush(); closeList(); const cells = (row) => row.trim().replace(/^\||\|$/g,'').split('|');
      html += `<div class="table-wrap"><table><thead><tr>${cells(line).map(c=>`<th>${inline(c.trim())}</th>`).join('')}</tr></thead><tbody>`;
      i += 2; for (; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) html += `<tr>${cells(lines[i]).map(c=>`<td>${inline(c.trim())}</td>`).join('')}</tr>`;
      html += '</tbody></table></div>'; i--; continue;
    }
    const item = line.match(/^\s*(?:([-*+])\s+|(\d+)[.、]\s+)(.+)$/);
    if (item) { flush(); const type = item[2] ? 'ol' : 'ul'; if (list !== type) {closeList(); html += type === 'ol' ? `<ol start="${Number(item[2])}">` : '<ul>'; list = type;} html += `<li>${inline(item[3])}</li>`; continue; }
    if (list && /^\s{2,}/.test(line)) { html += `<li>${inline(line.trim())}</li>`; continue; }
    closeList(); paragraph.push(line);
  }
  flush(); closeList(); if (code !== null) html += `<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`;
  return html;
}
function notify(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(()=>$('#toast').hidden = true,3500); }
function save() { try {sessionStorage.setItem('cook-chat',JSON.stringify({sessionId:state.sessionId,messages:state.messages.slice(-30),serverId:state.serverId,lastActive:state.lastActive}));} catch {} }
function scrollEnd() { const area = $('#scroll-area'); area.scrollTo({top:area.scrollHeight,behavior:'smooth'}); }
function showChat() { $('#welcome').hidden = true; $('#messages').hidden = false; }
function closeSidebar() { $('#sidebar').classList.remove('open'); $('#sidebar-shade').hidden = true; }
function resizeInput() { const input = $('#question'); input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight,135) + 'px'; }
function setBusy(busy) {
  state.busy = busy; $('#send-button').disabled = busy; $('#new-chat').disabled = busy;
  $('#messages').setAttribute('aria-busy',String(busy));
  $('#composer-hint').innerHTML = busy ? `${icon('clock')}正在准备回答，请稍等…` : `${icon('book')}好味道，从一个问题开始`;
}
function userMessage(question) {const row=document.createElement('article'); row.className='message user'; const bubble=document.createElement('div'); bubble.className='user-bubble'; bubble.textContent=question; row.append(bubble); $('#messages').append(row);}
function assistantFrame() {const row=document.createElement('article'); row.className='message assistant'; row.innerHTML=`<div class="assistant-heading"><span class="assistant-avatar">${icon('pot')}</span><strong>好好吃饭</strong><span>你的厨房小帮手</span></div><div class="answer-body"></div>`; $('#messages').append(row); return row;}
function paintAnswer(row, data) {
  const body=row.querySelector('.answer-body');
  const answer = String(data.answer || '').split('\n\n文档来源：')[0];
  body.innerHTML=`<div class="markdown">${markdown(answer)}</div>`;
  const sources = Array.isArray(data.sources) ? data.sources : [];
  if (sources.length) {const section=document.createElement('section'); section.className='sources'; section.innerHTML=`<div class="sources-label">${icon('book')}这份回答的食谱依据 · 点击查看原文</div><div class="source-chips"></div>`;
    for (const source of sources) { const button=document.createElement('button'); button.className='source-chip'; button.innerHTML=`<small>${escapeHtml(source.id)}</small><span>${escapeHtml(source.title)}</span>${icon('chevron')}`; button.addEventListener('click',()=>openRecipe(source)); section.querySelector('.source-chips').append(button); } body.append(section); }
  body.querySelectorAll('[data-source-id]').forEach(button=> {const source=sources.find(s=>s.id===button.dataset.sourceId); if(source) button.addEventListener('click',()=>openRecipe(source)); else {button.replaceWith(document.createTextNode(button.textContent));}});
  const actions=document.createElement('div'); actions.className='answer-actions'; const copy=document.createElement('button'); copy.className='text-button'; copy.innerHTML=`${icon('copy')}复制回答`; copy.addEventListener('click',async()=> {try {await navigator.clipboard.writeText(data.answer); notify('回答已复制');} catch {notify('无法自动复制，可以选中文字复制。');}}); actions.append(copy); body.append(actions);
  if(data.kind==='recommendation' && sources.length) {const followups=document.createElement('div'); followups.className='followups'; for(const source of sources) {const button=document.createElement('button'); button.textContent=`${source.title}怎么做？`; button.addEventListener('click',()=>sendQuestion(`${source.title}怎么做`)); followups.append(button);} body.append(followups);}
}
async function openRecipe(source) {
  $('#recipe-title').textContent=source.title; $('#recipe-content').textContent='正在读取食谱原文…'; $('#recipe-original').hidden=true; $('#recipe-dialog').showModal();
  try {const response=await fetch(`/recipes/${encodeURIComponent(source.document_id)}`,{headers:headers()}); const data=await response.json(); if(!response.ok) throw new Error(response.status===401?'请在访问设置中填写有效密钥。':'暂时无法读取食谱，请稍后再试。'); $('#recipe-title').textContent=data.title; $('#recipe-content').innerHTML=markdown(data.content); const url=safeUrl(data.source.url); if(data.source.url && url) {$('#recipe-original').href=url; $('#recipe-original').hidden=false;}}
  catch(error) {$('#recipe-content').textContent=error.message;}
}
function headers() {return {'Content-Type':'application/json',...(state.key ? {'Authorization':`Bearer ${state.key}`} : {})};}
function friendlyError(status) {return ({401:'访问验证未通过，请填写有效的访问密钥。',422:'问题为空或太长了，请调整后再试。',429:'厨房小帮手正在回答其他问题，稍后再试一下。',502:'暂时没能准备好答案，请稍后重试。',503:'食谱助手暂时未就绪，请稍后再试。'})[status] || '这次没能完成回答，请稍后重试。';}
async function sendQuestion(value, retry=false) {
  const question=String(value).trim(); if(!question) {$('#question').focus(); return;} if(state.busy) {notify('上一份回答还在准备中，请稍等。'); return;} if(question.length>2000) {notify('问题请控制在 2000 字以内。'); return;}
  closeSidebar(); showChat(); if(!retry) {userMessage(question); state.messages.push({role:'user',question});}
  if(state.messages.filter(m=>m.role==='user').length===1) $('#chat-title').textContent=question;
  $('#question').value=''; resizeInput(); setBusy(true); save();
  const row=assistantFrame(); row.querySelector('.answer-body').innerHTML='<div class="thinking"><span class="thinking-dots"><i></i><i></i><i></i></span><span class="thinking-label">正在找合适的食谱…</span></div>'; scrollEnd();
  thinkingTimer=setTimeout(()=> {const label=row.querySelector('.thinking-label'); if(label) label.textContent='正在整理做法与食谱来源…';},4000);
  try {const response=await fetch('/ask',{method:'POST',headers:headers(),body:JSON.stringify({question,...(state.sessionId ? {session_id:state.sessionId} : {})})});
    let data; try {data=await response.json();} catch {throw new Error('服务暂时没有响应，请稍后重试。');}
    if(!response.ok) { if(response.status===401) $('#settings-dialog').showModal(); throw new Error(friendlyError(response.status)); }
    state.sessionId=data.session_id; state.lastActive=Date.now(); paintAnswer(row,data); state.messages.push({role:'assistant',data}); save();
  } catch(error) {const body=row.querySelector('.answer-body'); body.innerHTML='<div class="error-message"><p></p><button class="text-button">重试这个问题</button></div>'; body.querySelector('p').textContent=error instanceof TypeError?'连接中断了，请确认食谱助手已经启动。':error.message; body.querySelector('button').addEventListener('click',()=> {if(!state.busy) {row.remove(); sendQuestion(question,true);}});}
  finally {clearTimeout(thinkingTimer); setBusy(false); scrollEnd(); $('#question').focus();}
}
function newChat(quiet=false) {if(state.busy) return; state.sessionId=null; state.messages=[]; state.lastActive=0; $('#messages').replaceChildren(); $('#messages').hidden=true; $('#welcome').hidden=false; $('#chat-title').textContent='还没想好？一起选道菜'; $('#question').value=''; resizeInput(); save(); closeSidebar(); $('#scroll-area').scrollTop=0; if(!quiet) notify('新的对话，新的好味道。'); $('#question').focus();}
async function checkHealth() {
  try { const response=await fetch('/health',{signal:AbortSignal.timeout(15000)}); const data=await response.json(); const ok=response.ok && data.status==='ready';
    $('#service-status').className='service-status '+(ok?'online':'offline'); $('#service-status').innerHTML=`<span></span>${ok?'食谱助手已就绪':'暂时未就绪'}`;
    if(ok) {$('#recipe-count').textContent=`${data.recipes} 份食谱`; $('#connection-settings').hidden=!data.auth_required;
      if(state.serverId && data.instance_id && state.serverId!==data.instance_id) {newChat(true); notify('服务已重启，已为你开启新的对话。');}
      else if(state.sessionId && Date.now()-state.lastActive > (data.session_ttl_seconds || 3600)*1000) {newChat(true); notify('上次对话已过期，已开启新的对话。');}
      state.serverId=data.instance_id || null; save();
    }
  } catch {$('#service-status').className='service-status offline'; $('#service-status').innerHTML='<span></span>连接暂时中断';}
}
$('#ask-form').addEventListener('submit',event=> {event.preventDefault(); sendQuestion($('#question').value);});
$('#question').addEventListener('input',resizeInput);
$('#question').addEventListener('keydown',event=> {if(event.key==='Enter' && !event.shiftKey && !event.isComposing && event.keyCode!==229) {event.preventDefault(); sendQuestion(event.currentTarget.value);}});
document.querySelectorAll('[data-question]').forEach(button=>button.addEventListener('click',()=>sendQuestion(button.dataset.question)));
$('#new-chat').addEventListener('click',()=>newChat()); $('#current-chat').addEventListener('click',()=>{closeSidebar(); scrollEnd();});
$('#menu-toggle').addEventListener('click',()=>{$('#sidebar').classList.add('open'); $('#sidebar-shade').hidden=false;}); $('#sidebar-shade').addEventListener('click',closeSidebar);
$('#close-recipe').addEventListener('click',()=>$('#recipe-dialog').close()); $('#close-settings').addEventListener('click',()=>$('#settings-dialog').close());
$('#connection-settings').addEventListener('click',()=>{$('#api-key').value=state.key; $('#settings-dialog').showModal(); closeSidebar();});
$('#settings-form').addEventListener('submit',event=> {event.preventDefault(); state.key=$('#api-key').value.trim(); try {sessionStorage.setItem('cook-key',state.key);} catch {} $('#settings-dialog').close(); notify('访问设置已保存，可重试刚才的问题。');});
for(const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click',event=> {if(event.target===dialog) {const rect=dialog.getBoundingClientRect(); if(event.clientX<rect.left || event.clientX>rect.right || event.clientY<rect.top || event.clientY>rect.bottom) dialog.close();}});
try {state.key=sessionStorage.getItem('cook-key') || ''; const cached=JSON.parse(sessionStorage.getItem('cook-chat') || 'null'); if(cached && Array.isArray(cached.messages)) {Object.assign(state,{sessionId:cached.sessionId,messages:cached.messages.slice(-30),serverId:cached.serverId,lastActive:cached.lastActive || 0}); if(state.messages.length) {showChat(); for(const message of state.messages) {if(message.role==='user') userMessage(String(message.question)); else if(message.role==='assistant' && message.data) paintAnswer(assistantFrame(),message.data);} $('#chat-title').textContent=state.messages.find(m=>m.role==='user')?.question || '本次对话'; scrollEnd();}}} catch {try {sessionStorage.removeItem('cook-chat');} catch {}}
checkHealth(); setInterval(checkHealth,45000);
