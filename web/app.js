/* Same-origin website. No CDN, browser framework or external font is required. */
'use strict';
const $ = (selector) => document.querySelector(selector);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {chatId:null, messages:[], chats:[], busy:false, key:'', controller:null, trash:false, renameId:null, autoScroll:true};
let toastTimer, renderTimer;
function safeUrl(value) {
  try { const url = new URL(value, location.origin); return ['https:','http:'].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}
function inline(text) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|!?\[[^\]\n]*\]\([^\s)]+\)|\[S\d+\])/g;
  let out = '', start = 0;
  for (const match of text.matchAll(pattern)) {
    out += escapeHtml(text.slice(start, match.index));
    const token = match[0];
    if (token.startsWith('`')) out += `<code>${escapeHtml(token.slice(1,-1))}</code>`;
    else if (token.startsWith('**')) out += `<strong>${escapeHtml(token.slice(2,-2))}</strong>`;
    else if (/^\[S\d+\]$/.test(token)) out += `<button class="citation" data-source-id="${token.slice(1,-1)}" title="查看食谱原文">${token}</button>`;
    else if(token.startsWith('![')) {const parts=token.match(/^!\[([^\]]*)\]\(([^)]+)\)$/); const url=safeUrl(parts[2]); out+=url && new URL(url).origin===location.origin ? `<img class="recipe-inline-image" src="${escapeHtml(url)}" alt="${escapeHtml(parts[1] || '食谱原图')}" loading="lazy">` : escapeHtml(parts[1]);}
    else { const parts = token.match(/^\[([^\]]*)\]\(([^)]+)\)$/); const url = safeUrl(parts[2]);
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
function notify(message, action=null) {
  const toast=$('#toast'); toast.replaceChildren(document.createTextNode(message));
  if(action) {const button=document.createElement('button'); button.textContent=action.label; button.onclick=action.run; toast.append(button);}
  toast.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>toast.hidden=true,action?8000:3500);
}
function headers() {return {'Content-Type':'application/json',...(state.key?{'Authorization':`Bearer ${state.key}`}:{})};}
function friendlyError(status) {return ({401:'请填写有效的访问密钥。',404:'这段对话已不存在，请开启新对话。',409:'上一个回答还在停止中，请稍后重试。',422:'请检查问题或标题是否为空、过长。',429:'正在回答其他问题，请稍后再试。',502:'回答暂时未能完成，请重试。',503:'食谱助手暂时未就绪，请稍后再试。'})[status] || '操作未完成，请稍后重试。';}
async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:headers()});
  let data; try {data=await response.json();} catch {throw new Error('服务暂时没有响应。');}
  if(!response.ok) {if(response.status===401) $('#settings-dialog').showModal(); throw new Error(friendlyError(response.status));}
  return data;
}
function selectId(id) {state.chatId=id; try {if(id) localStorage.setItem('cook-active-chat',id); else localStorage.removeItem('cook-active-chat');} catch {}}
function closeSidebar() {$('#sidebar').classList.remove('open'); $('#sidebar-shade').hidden=true;}
function resizeInput() {const input=$('#question'); input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,135)+'px';}
function scrollEnd(force=false) {if(force || state.autoScroll) $('#scroll-area').scrollTo({top:$('#scroll-area').scrollHeight,behavior:force?'smooth':'auto'});}
function setBusy(busy) {
  state.busy=busy; $('#new-chat').disabled=busy; $('#messages').setAttribute('aria-busy',String(busy));
  $('#send-button').innerHTML=icon(busy?'stop':'arrow'); $('#send-button').setAttribute('aria-label',busy?'停止生成':'发送问题');
  $('#send-button').classList.toggle('is-stop',busy);
  $('#composer-hint').innerHTML=busy?`${icon('clock')}正在回答 · 点击方块停止`:`${icon('book')}好味道，从一个问题开始`;
}
function userMessage(question) {const row=document.createElement('article'); row.className='message user'; const bubble=document.createElement('div'); bubble.className='user-bubble'; bubble.textContent=question; row.append(bubble); $('#messages').append(row);}
function assistantFrame() {const row=document.createElement('article'); row.className='message assistant'; row.innerHTML=`<div class="assistant-heading"><span class="assistant-avatar">${icon('pot')}</span><strong>好好吃饭</strong><span>你的厨房小帮手</span></div><div class="answer-body"></div>`; $('#messages').append(row); return row;}
function bindCitations(element,sources) {element.querySelectorAll('[data-source-id]').forEach(button=>{const source=sources.find(s=>s.id===button.dataset.sourceId); if(source) button.onclick=()=>openRecipe(source); else button.disabled=true;});}
function paintText(row,answer,sources=[],live=false) {
  const body=row.querySelector('.answer-body'); let content=body.querySelector('.markdown');
  if(!content) {body.replaceChildren(); content=document.createElement('div'); content.className='markdown'; body.append(content);}
  content.innerHTML=markdown(String(answer || '').split('\n\n文档来源：')[0]);
  content.classList.toggle('streaming-text',live); bindCitations(content,sources);
}
function imageCard(source,button) {
  const image=source.image;
  if(image?.url && safeUrl(image.url) && new URL(safeUrl(image.url)).origin===location.origin) {
    const img=document.createElement('img'); img.src=image.url; img.alt=source.title+'食谱原图'; img.loading='lazy'; img.width=320; img.height=240;
    img.onerror=()=>{const placeholder=document.createElement('div'); placeholder.className='recipe-cover-empty'; placeholder.textContent='原图暂不可用'; img.replaceWith(placeholder);}; button.append(img);
  } else {const empty=document.createElement('div'); empty.className='recipe-cover-empty'; empty.innerHTML=icon('book')+'<span>暂无原图</span>'; button.append(empty);}
}
function paintAnswer(row,data,status='complete',question='',topic=null) {
  const body=row.querySelector('.answer-body'); body.replaceChildren(); const sources=data.sources || [];
  paintText(row,data.answer,sources);
  if(sources.length) {
    const section=document.createElement('section'); section.className='sources'; section.innerHTML=`<div class="sources-label">${icon('book')}食谱来源 · 点击查看原文</div>`;
    if(data.kind==='recommendation') {const grid=document.createElement('div'); grid.className='recipe-card-grid'; grid.classList.toggle('without-photos',sources.every(s=>!s.image?.url));
      for(const source of sources) {const card=document.createElement('button'); card.className='recipe-card'; imageCard(source,card);
        const label=document.createElement('div'); label.className='recipe-card-label'; label.innerHTML=`<strong>${escapeHtml(source.title)}</strong><small>${escapeHtml(source.image?.url?source.image.label:'查看原文')} ${icon('chevron')}</small>`; card.append(label); card.onclick=()=>openRecipe(source); grid.append(card);} section.append(grid);
    } else {const chips=document.createElement('div'); chips.className='source-chips'; for(const source of sources) {const button=document.createElement('button'); button.className='source-chip'; button.innerHTML=`<small>${escapeHtml(source.id)}</small>${escapeHtml(source.title)}${icon('chevron')}`; button.onclick=()=>openRecipe(source); chips.append(button);} section.append(chips);}
    body.append(section);
  }
  const actions=document.createElement('div'); actions.className='answer-actions';
  const copy=document.createElement('button'); copy.className='text-button'; copy.innerHTML=icon('copy')+'复制回答'; copy.onclick=async()=>{try{await navigator.clipboard.writeText(data.answer || ''); notify('回答已复制');}catch{notify('可以选中文字复制。');}}; actions.append(copy);
  if(status!=='complete') {const note=document.createElement('span'); note.className='incomplete-note'; note.textContent=status==='streaming'?'正在生成…':status==='stopped'?'已停止生成':status==='interrupted'?'服务重启，回答未完成':'回答未完成'; actions.append(note);}
  if(question) {const retry=document.createElement('button'); retry.className='text-button'; retry.innerHTML=icon('arrow')+'重新回答'; retry.onclick=()=>sendQuestion(question,topic); actions.append(retry);}
  body.append(actions);
  if(data.kind==='recommendation' && sources.length && status==='complete') {
    const followups=document.createElement('div'); followups.className='followups'; for(const source of sources) {const button=document.createElement('button'); button.textContent=source.title+'怎么做？'; button.onclick=()=>sendQuestion(source.title+'怎么做'); followups.append(button);}
    const more=document.createElement('button'); more.className='change-batch'; more.textContent='换一批推荐'; more.onclick=()=>sendQuestion(question || '晚餐吃什么',topic); followups.append(more); body.append(followups);
    if(data.recommendation_reset) {const note=document.createElement('p'); note.className='recommendation-note'; note.textContent='这一类菜谱已经看完，重新为你挑选。'; body.append(note);}
  }
}
function paintChat(chat,resetView=true) {
  $('#messages').replaceChildren(); state.messages=chat.messages || [];
  $('#welcome').hidden=!!state.messages.length; $('#messages').hidden=!state.messages.length;
  let question='';
  for(const message of state.messages) {if(message.role==='user') {question=message.content; userMessage(question);} else {const data={...message.payload,answer:message.content}; paintAnswer(assistantFrame(),data,message.status,question,message.payload?.topic);}}
  if(resetView) state.autoScroll=true; scrollEnd(resetView);
}
function dayLabel(value) {
  const today=new Date(); const date=new Date(value); const toDay=d=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(d);
  if(toDay(date)===toDay(today)) return '今天'; today.setDate(today.getDate()-1); return toDay(date)===toDay(today)?'昨天':'更早';
}
function renderHistory() {
  const list=$('#chat-list'); list.replaceChildren(); const query=$('#history-search').value.trim().toLowerCase();
  const chats=state.chats.filter(c=>c.title.toLowerCase().includes(query)); let group='';
  if(!chats.length) {const empty=document.createElement('p'); empty.className='history-empty'; empty.textContent=query?'没有匹配的对话':state.trash?'最近删除中没有对话':'还没有对话，先聊聊今天吃什么。'; list.append(empty);}
  for(const chat of chats) {
    const currentGroup=dayLabel(chat.updated_at); if(currentGroup!==group) {const label=document.createElement('div'); label.className='history-group'; label.textContent=currentGroup; list.append(label); group=currentGroup;}
    const row=document.createElement('div'); row.className='history-row'+(chat.id===state.chatId?' selected':'');
    const select=document.createElement('button'); select.className='history-select'; select.textContent=chat.title; select.title=chat.title; select.onclick=()=>state.trash?restoreChat(chat.id):openChat(chat.id); row.append(select);
    if(state.trash) {const restore=document.createElement('button'); restore.className='history-control'; restore.textContent='恢复'; restore.onclick=()=>restoreChat(chat.id); row.append(restore);}
    else {
      const rename=document.createElement('button'); rename.className='history-control'; rename.innerHTML=icon('edit'); rename.title='重命名'; rename.setAttribute('aria-label','重命名 '+chat.title); rename.onclick=()=>{if(state.busy) return notify('请先停止生成。'); state.renameId=chat.id; $('#chat-name').value=chat.title; $('#rename-dialog').showModal();}; row.append(rename);
      const remove=document.createElement('button'); remove.className='history-control'; remove.innerHTML=icon('trash'); remove.title='删除'; remove.setAttribute('aria-label','删除 '+chat.title); remove.onclick=()=>deleteChat(chat.id); row.append(remove);
    }
    list.append(row);
  }
  $('.history-heading > span').textContent=state.trash?'最近删除':'历史对话'; $('#show-trash').setAttribute('aria-label',state.trash?'返回历史对话':'查看最近删除'); $('#show-trash').title=state.trash?'返回历史对话':'查看最近删除';
}
async function refreshHistory() {try{state.chats=(await api('/chats'+(state.trash?'?trash=true':''))).chats; renderHistory();}catch(error){$('#chat-list').textContent=error.message;}}
async function openChat(id) {
  if(state.busy) return notify('请先停止当前回答，再切换对话。');
  try {let chat=await api('/chats/'+id); selectId(id); paintChat(chat); renderHistory(); closeSidebar();
    if(chat.generating){setBusy(true);
      try{while(chat.generating){await new Promise(resolve=>setTimeout(resolve,1000)); chat=await api('/chats/'+id); paintChat(chat,false);}}
      finally{setBusy(false); await refreshHistory();}
    }
  }catch(error){notify(error.message);}
}
function newChat(quiet=false) {if(state.busy) return notify('请先停止当前回答。'); selectId(null); state.messages=[]; $('#messages').replaceChildren(); $('#messages').hidden=true; $('#welcome').hidden=false; $('#question').value=''; resizeInput(); renderHistory(); closeSidebar(); $('#scroll-area').scrollTop=0; if(!quiet) notify('新的对话，新的好味道。'); $('#question').focus();}
async function deleteChat(id) {if(state.busy) return notify('请先停止生成。'); try{await api('/chats/'+id,{method:'DELETE'}); if(state.chatId===id) newChat(true); await refreshHistory(); notify('对话已移到最近删除。',{label:'撤销',run:()=>restoreChat(id)});}catch(error){notify(error.message);}}
async function restoreChat(id) {try{await api('/chats/'+id+'/restore',{method:'POST'}); state.trash=false; await refreshHistory(); await openChat(id); notify('对话已恢复。');}catch(error){notify(error.message);}}
async function openRecipe(source) {
  $('#recipe-title').textContent=source.title; $('#recipe-content').textContent='正在读取原文…'; $('#recipe-original').hidden=true; $('#recipe-dialog').showModal();
  try{const data=await api('/recipes/'+encodeURIComponent(source.document_id)); $('#recipe-title').textContent=data.title; $('#recipe-content').innerHTML=markdown(data.display_content || data.content);
    if(data.image?.url && !$('#recipe-content img')) {const img=document.createElement('img'); img.className='recipe-inline-image cover-photo'; img.src=data.image.url; img.alt=data.title+'食谱原图'; $('#recipe-content').prepend(img);}
    const url=safeUrl(data.source.url); if(data.source.url && url) {$('#recipe-original').href=url; $('#recipe-original').hidden=false;}
  }catch(error){$('#recipe-content').textContent=error.message;}
}
async function readEvents(response,onEvent) {
  const reader=response.body.getReader(); const decoder=new TextDecoder(); let buffer='';
  try{for(;;){const {value,done}=await reader.read(); buffer+=done?decoder.decode():decoder.decode(value,{stream:true}); let end;
    while((end=buffer.indexOf('\n\n'))!==-1){const block=buffer.slice(0,end); buffer=buffer.slice(end+2); if(block.startsWith(':')) continue;
      let name='message'; const data=[]; for(const line of block.split('\n')) {if(line.startsWith('event:')) name=line.slice(6).trim(); if(line.startsWith('data:')) data.push(line.slice(5).trimStart());}
      if(data.length) onEvent(name,JSON.parse(data.join('\n')));
    } if(done) break;}
  }finally{reader.releaseLock();}
}
async function sendQuestion(value,topic=null) {
  const question=String(value).trim(); if(!question) return $('#question').focus(); if(state.busy) return notify('上一份回答正在准备中，可以先停止生成。'); if(question.length>2000) return notify('问题请控制在 2000 字以内。');
  closeSidebar(); state.autoScroll=true; let row,answer='',info={sources:[],kind:'answer'},done=false,stopped=false;
  setBusy(true); state.controller=new AbortController(); $('#question').value=''; resizeInput();
  try{
    if(!state.chatId) {const chat=await api('/chats',{method:'POST'}); selectId(chat.id);}
    $('#welcome').hidden=true; $('#messages').hidden=false; userMessage(question); row=assistantFrame(); row.querySelector('.answer-body').innerHTML='<div class="thinking"><span class="thinking-dots"><i></i><i></i><i></i></span><span>正在查找食谱…</span></div>'; scrollEnd();
    const response=await fetch('/ask/stream',{method:'POST',headers:headers(),signal:state.controller.signal,body:JSON.stringify({question,chat_id:state.chatId,...(topic?{topic}:{})})});
    if(!response.ok){if(response.status===401) $('#settings-dialog').showModal(); throw new Error(friendlyError(response.status));}
    await readEvents(response,(name,data)=>{
      if(name==='start'){refreshHistory();}
      else if(name==='metadata'){info={...data,topic:data.topic || topic};}
      else if(name==='delta'){answer+=data.text; if(!renderTimer) renderTimer=setTimeout(()=>{renderTimer=null; paintText(row,answer,info.sources,true); scrollEnd();},60);}
      else if(name==='done'){done=true; clearTimeout(renderTimer); renderTimer=null; answer=data.answer; info={...data,topic:data.topic || topic}; paintAnswer(row,info,'complete',question,info.topic); scrollEnd();}
      else if(name==='error') throw new Error(data.message);
    });
    if(!done) throw new Error('连接中断，回答未完成。');
  }catch(error){clearTimeout(renderTimer); renderTimer=null;
    stopped=error.name==='AbortError';
    if(row){paintAnswer(row,{...info,answer},stopped?'stopped':'error',question,topic);
      if(!stopped){const note=document.createElement('div'); note.className='error-message'; note.textContent=error instanceof TypeError?'连接中断，请确认服务正在运行。':error.message; row.querySelector('.answer-body').append(note);}}
    else notify(error.message);
  }finally{clearTimeout(renderTimer); renderTimer=null; state.controller=null;
    if(stopped && state.chatId){$('#composer-hint').innerHTML=icon('clock')+'正在结束上一轮回答…'; $('#send-button').disabled=true;
      for(let attempt=0;attempt<60;attempt++){try{const chat=await api('/chats/'+state.chatId); if(!chat.generating) break;}catch{break;} await new Promise(resolve=>setTimeout(resolve,500));}
      $('#send-button').disabled=false;
    }
    setBusy(false); await refreshHistory(); scrollEnd(); $('#question').focus();}
}
async function checkHealth() {
  try{const response=await fetch('/health',{signal:AbortSignal.timeout(15000)}); const data=await response.json(); const ok=response.ok && data.status==='ready';
    $('#service-status').className='service-status '+(ok?'online':'offline'); $('#service-status').innerHTML=`<span></span>${ok?'食谱助手已就绪':'暂时未就绪'}`;
    if(ok){$('#recipe-count').textContent=data.recipes+' 份食谱'; $('#connection-settings').hidden=!data.auth_required;}
  }catch{$('#service-status').className='service-status offline'; $('#service-status').innerHTML='<span></span>连接暂时中断';}
}
$('#ask-form').addEventListener('submit',event=>{event.preventDefault(); if(state.busy){state.controller?.abort(); if(state.chatId) api('/chats/'+state.chatId+'/stop',{method:'POST'}).catch(()=>{});} else sendQuestion($('#question').value);});
$('#question').addEventListener('input',resizeInput); $('#question').addEventListener('keydown',event=>{if(event.key==='Enter' && !event.shiftKey && !event.isComposing && event.keyCode!==229){event.preventDefault(); if(!state.busy) sendQuestion(event.currentTarget.value);}});
document.querySelectorAll('[data-question]').forEach(button=>button.onclick=()=>sendQuestion(button.dataset.question,button.dataset.topic || null));
$('#new-chat').onclick=()=>newChat(); $('#history-search').oninput=renderHistory; $('#show-trash').onclick=()=>{state.trash=!state.trash; refreshHistory();};
$('#menu-toggle').onclick=()=>{$('#sidebar').classList.add('open'); $('#sidebar-shade').hidden=false;}; $('#sidebar-shade').onclick=closeSidebar;
$('#close-recipe').onclick=()=>$('#recipe-dialog').close(); $('#close-settings').onclick=()=>$('#settings-dialog').close(); $('#close-rename').onclick=()=>$('#rename-dialog').close();
$('#connection-settings').onclick=()=>{$('#api-key').value=state.key; $('#settings-dialog').showModal(); closeSidebar();};
$('#settings-form').onsubmit=event=>{event.preventDefault(); state.key=$('#api-key').value.trim(); try{sessionStorage.setItem('cook-key',state.key);}catch{} $('#settings-dialog').close(); refreshHistory(); notify('访问设置已保存。');};
$('#rename-form').onsubmit=async event=>{event.preventDefault(); try{await api('/chats/'+state.renameId,{method:'PATCH',body:JSON.stringify({title:$('#chat-name').value})}); $('#rename-dialog').close(); await refreshHistory();}catch(error){notify(error.message);}};
$('#scroll-area').addEventListener('scroll',()=>{const area=$('#scroll-area'); state.autoScroll=area.scrollHeight-area.scrollTop-area.clientHeight<90; $('#jump-bottom').hidden=state.autoScroll || $('#messages').hidden;},{passive:true}); $('#jump-bottom').onclick=()=>{state.autoScroll=true; scrollEnd(true);};
for(const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect(); if(event.clientX<rect.left || event.clientX>rect.right || event.clientY<rect.top || event.clientY>rect.bottom) dialog.close();}});
async function boot(){
  try{state.key=sessionStorage.getItem('cook-key') || ''; state.chatId=localStorage.getItem('cook-active-chat');}catch{}
  await refreshHistory(); await checkHealth();
  if(!state.chatId){try{const old=JSON.parse(sessionStorage.getItem('cook-chat') || 'null'); const turns=[]; let question='';
    for(const message of old?.messages || []){if(message.role==='user') question=message.question; else if(question && message.data?.answer){turns.push({question,answer:message.data.answer,document_ids:(message.data.sources || []).map(s=>s.document_id)}); question='';}}
    if(turns.length){const chat=await api('/chats/import',{method:'POST',body:JSON.stringify({turns})}); selectId(chat.id); sessionStorage.removeItem('cook-chat'); await refreshHistory(); notify('上次对话已保存到历史记录。');}
  }catch{/* Keep the old tab data if service or auth is unavailable. */}}
  if(state.chatId){if(state.chats.some(c=>c.id===state.chatId)) await openChat(state.chatId); else selectId(null);}
}
boot(); setInterval(checkHealth,45000);
