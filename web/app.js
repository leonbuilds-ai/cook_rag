/* Same-origin website. No CDN, browser framework or external font is required. */
'use strict';
const $ = (selector) => document.querySelector(selector);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {chatId:null, messages:[], chats:[], busy:false, key:'', controller:null, turn:null, observer:null, trash:false, renameId:null, autoScroll:true, suggested:[], seen:{}, suggestionsLoaded:false};
let toastTimer;
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
  state.busy=busy; $('#new-chat').disabled=false; $('#messages').setAttribute('aria-busy',String(busy));
  $('#send-button').innerHTML=icon(busy?'stop':'arrow'); $('#send-button').setAttribute('aria-label',busy?'停止生成':'发送问题');
  $('#send-button').classList.toggle('is-stop',busy);
  $('#composer-hint').innerHTML=busy?`${icon('clock')}正在回答 · 点击方块停止`:`${icon('book')}好味道，从一个问题开始`;
}
function userMessage(question) {const row=document.createElement('article'); row.className='message user'; const bubble=document.createElement('div'); bubble.className='user-bubble'; bubble.textContent=question; row.append(bubble); $('#messages').append(row);}
function assistantFrame() {const row=document.createElement('article'); row.className='message assistant'; row.innerHTML=`<div class="assistant-heading"><span class="assistant-avatar">${icon('pot')}</span><strong>好好吃饭</strong><span>你的厨房小帮手</span></div><div class="answer-body"></div>`; $('#messages').append(row); return row;}
function bindCitations(element,sources) {element.querySelectorAll('[data-source-id]').forEach(button=>{const source=sources.find(s=>s.id===button.dataset.sourceId); if(source) button.onclick=()=>openRecipe(source); else button.disabled=true;});}
function visibleAnswer(answer) {return String(answer || '').split('\n\n文档来源：')[0].replace(/\s*\[S\d+\]/gi,'');}
function recommendationTopic(question) {if(/早餐/.test(question)) return 'breakfast'; if(/清淡/.test(question)) return 'light'; if(/饮品|饮料|喝什么/.test(question)) return 'drinks'; if(/简单|快手/.test(question)) return 'quick'; if(/晚餐/.test(question)) return 'dinner'; return null;}
function saveSeen() {try{localStorage.setItem('cook-recommend-seen',JSON.stringify(state.seen));}catch{}}
function stopGeneration() {
  const turn=state.turn, chatId=turn?.chatId || state.chatId;
  state.observer=null;
  if(turn){turn.stopped=true; clearTimeout(turn.renderTimer); turn.controller.abort();
    if(turn.row) paintAnswer(turn.row,{...turn.info,answer:turn.answer},'stopped',turn.question,turn.topic);}
  state.turn=null; state.controller=null; setBusy(false);
  if(chatId) api('/chats/'+chatId+'/stop',{method:'POST'}).catch(()=>notify('停止请求未送达，请检查服务连接。'));
}
function paintText(row,answer,sources=[],live=false) {
  const body=row.querySelector('.answer-body'); let content=body.querySelector('.markdown');
  if(!content) {body.replaceChildren(); content=document.createElement('div'); content.className='markdown'; body.append(content);}
  content.innerHTML=markdown(visibleAnswer(answer));
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
    const section=document.createElement('details'); section.className='sources'; section.innerHTML=`<summary class="sources-label">${icon('book')}查看食谱依据 · ${sources.length} 份原文</summary>`;
    if(data.kind==='recommendation') {const grid=document.createElement('div'); grid.className='recipe-card-grid'; grid.classList.toggle('without-photos',sources.every(s=>!s.image?.url));
      for(const source of sources) {const card=document.createElement('button'); card.className='recipe-card'; imageCard(source,card);
        const label=document.createElement('div'); label.className='recipe-card-label'; label.innerHTML=`<strong>${escapeHtml(source.title)}</strong><small>${escapeHtml(source.image?.url?source.image.label:'查看原文')} ${icon('chevron')}</small>`; card.append(label); card.onclick=()=>openRecipe(source); grid.append(card);} section.append(grid);
    } else {const chips=document.createElement('div'); chips.className='source-chips'; for(const source of sources) {const button=document.createElement('button'); button.className='source-chip'; button.innerHTML=`${escapeHtml(source.title)}${icon('chevron')}`; button.onclick=()=>openRecipe(source); chips.append(button);} section.append(chips);}
    body.append(section);
  }
  const actions=document.createElement('div'); actions.className='answer-actions';
  const copy=document.createElement('button'); copy.className='text-button'; copy.innerHTML=icon('copy')+'复制回答'; copy.onclick=async()=>{try{await navigator.clipboard.writeText(visibleAnswer(data.answer)); notify('回答已复制');}catch{notify('可以选中文字复制。');}}; actions.append(copy);
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
  if(state.busy) stopGeneration();
  try {let chat=await api('/chats/'+id); selectId(id); paintChat(chat); renderHistory(); closeSidebar();
    if(chat.generating){setBusy(true); state.observer=id;
      try{while(chat.generating && state.observer===id && state.chatId===id){await new Promise(resolve=>setTimeout(resolve,500)); if(state.observer!==id) break; chat=await api('/chats/'+id); if(state.observer===id) paintChat(chat,false);}}
      finally{if(state.observer===id){state.observer=null; setBusy(false);} await refreshHistory();}
    }
  }catch(error){notify(error.message);}
}
function newChat(quiet=false) {if(state.busy) stopGeneration(); state.observer=null; selectId(null); state.messages=[]; $('#messages').replaceChildren(); $('#messages').hidden=true; $('#welcome').hidden=false; $('#question').value=''; resizeInput(); renderHistory(); closeSidebar(); $('#scroll-area').scrollTop=0; if(!quiet) notify('新的对话，新的好味道。'); $('#question').focus();}
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
  topic=topic || recommendationTopic(question);
  const turn={question,topic,chatId:state.chatId,controller:new AbortController(),row:null,answer:'',info:{sources:[],kind:'answer'},done:false,stopped:false,renderTimer:null};
  state.turn=turn; state.controller=turn.controller; closeSidebar(); state.autoScroll=true; setBusy(true); $('#question').value=''; resizeInput();
  try {
    if(!turn.chatId){const chat=await api('/chats',{method:'POST'}); turn.chatId=chat.id; if(turn.stopped) return; selectId(chat.id);}
    if(turn.stopped) return;
    $('#welcome').hidden=true; $('#messages').hidden=false; userMessage(question); turn.row=assistantFrame(); turn.row.querySelector('.answer-body').innerHTML='<div class="thinking"><span class="thinking-dots"><i></i><i></i><i></i></span><span>正在查找食谱…</span></div>'; scrollEnd();
    const key=topic || question;
    const response=await fetch('/ask/stream',{method:'POST',headers:headers(),signal:turn.controller.signal,body:JSON.stringify({question,chat_id:turn.chatId,exclude_ids:state.seen[key] || [],...(topic?{topic}:{})})});
    if(!response.ok){if(response.status===401) $('#settings-dialog').showModal(); throw new Error(friendlyError(response.status));}
    await readEvents(response,(name,data)=>{
      if(turn.stopped || state.turn!==turn) return;
      if(name==='start') refreshHistory();
      else if(name==='metadata') turn.info={...data,topic:data.topic || topic};
      else if(name==='delta'){turn.answer+=data.text; if(!turn.renderTimer) turn.renderTimer=setTimeout(()=>{turn.renderTimer=null; if(!turn.stopped && state.turn===turn){paintText(turn.row,turn.answer,turn.info.sources,true); scrollEnd();}},60);}
      else if(name==='done'){turn.done=true; clearTimeout(turn.renderTimer); turn.answer=data.answer; turn.info={...data,topic:data.topic || topic}; paintAnswer(turn.row,turn.info,'complete',question,turn.info.topic); scrollEnd();
        if(data.kind==='recommendation'){const seenKey=data.topic || key; const previous=data.recommendation_reset?[]:(state.seen[seenKey] || []); state.seen[seenKey]=[...new Set([...previous,...data.sources.map(source=>source.document_id)])].slice(-500); saveSeen();}}
      else if(name==='error') throw new Error(data.message);
    });
    if(!turn.done && !turn.stopped) throw new Error('连接中断，回答未完成。');
  }catch(error){clearTimeout(turn.renderTimer);
    if(!turn.stopped){if(turn.row){paintAnswer(turn.row,{...turn.info,answer:turn.answer},'error',question,topic); const note=document.createElement('div'); note.className='error-message'; note.textContent=error instanceof TypeError?'连接中断，请确认服务正在运行。':error.message; turn.row.querySelector('.answer-body').append(note);} else notify(error.message);}
  }finally{clearTimeout(turn.renderTimer);
    if(state.turn===turn){state.turn=null; state.controller=null; setBusy(false); scrollEnd(); $('#question').focus();}
    refreshHistory();
  }
}
async function loadSuggestions() {
  const button=$('#refresh-suggestions'); button.disabled=true;
  try{const data=await api('/suggestions',{method:'POST',body:JSON.stringify({exclude_ids:state.suggested})});
    const cards=[...document.querySelectorAll('.starter-card')];
    const labels={breakfast:'早餐，吃点好的',light:'今天想清淡一点',quick:'学一道拿手好菜'};
    data.suggestions.forEach((item,index)=>{const card=cards[index]; if(!card) return; card.dataset.question=item.question; delete card.dataset.topic; card.querySelector('strong').textContent=item.title; card.querySelector('p').textContent=labels[item.topic]; card.querySelector('.card-link').innerHTML='看看怎么做 '+icon('chevron');});
    state.suggested=[...new Set([...(data.reset_topics.length?[]:state.suggested),...data.suggestions.map(item=>item.document_id)])].slice(-500);
    state.suggestionsLoaded=true; try{localStorage.setItem('cook-home-seen',JSON.stringify(state.suggested));}catch{}
  }catch(error){notify(error.message);}finally{button.disabled=false;}
}
async function checkHealth() {
  try{const response=await fetch('/health',{signal:AbortSignal.timeout(15000)}); const data=await response.json(); const ok=response.ok && data.status==='ready';
    $('#service-status').className='service-status '+(ok?'online':'offline'); $('#service-status').innerHTML=`<span></span>${ok?'食谱助手已就绪':'暂时未就绪'}`;
    if(ok){$('#recipe-count').textContent=data.recipes+' 份食谱'; $('#connection-settings').hidden=!data.auth_required; if(!state.suggestionsLoaded) loadSuggestions();}
  }catch{$('#service-status').className='service-status offline'; $('#service-status').innerHTML='<span></span>连接暂时中断';}
}
$('#ask-form').addEventListener('submit',event=>{event.preventDefault(); if(state.busy){stopGeneration();} else sendQuestion($('#question').value);});
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
$('#refresh-suggestions').onclick=loadSuggestions;
async function boot(){
  try{state.suggested=JSON.parse(localStorage.getItem('cook-home-seen') || '[]'); state.seen=JSON.parse(localStorage.getItem('cook-recommend-seen') || '{}');}catch{}
  try{state.key=sessionStorage.getItem('cook-key') || ''; state.chatId=localStorage.getItem('cook-active-chat');}catch{}
  await refreshHistory(); await checkHealth();
  if(!state.chatId){try{const old=JSON.parse(sessionStorage.getItem('cook-chat') || 'null'); const turns=[]; let question='';
    for(const message of old?.messages || []){if(message.role==='user') question=message.question; else if(question && message.data?.answer){turns.push({question,answer:message.data.answer,document_ids:(message.data.sources || []).map(s=>s.document_id)}); question='';}}
    if(turns.length){const chat=await api('/chats/import',{method:'POST',body:JSON.stringify({turns})}); selectId(chat.id); sessionStorage.removeItem('cook-chat'); await refreshHistory(); notify('上次对话已保存到历史记录。');}
  }catch{/* Keep the old tab data if service or auth is unavailable. */}}
  if(state.chatId){if(state.chats.some(c=>c.id===state.chatId)) await openChat(state.chatId); else selectId(null);}
}
let libraryDocuments=[], libraryTimer;
function renderDocuments(){
  const query=$('#document-search').value.trim(); const list=$('#document-list'); list.replaceChildren();
  const docs=libraryDocuments.filter(d=>d.status==='ready' && d.title.includes(query));
  const heading=document.createElement('p'); heading.className='library-note'; heading.textContent=`${docs.length} 份可查食谱`; list.append(heading);
  for(const doc of docs){const button=document.createElement('button'); button.className='document-row'; button.textContent=doc.title;
    button.onclick=()=>{openRecipe({document_id:doc.id,title:doc.title});}; list.append(button);}
}
async function refreshLibrary(){
  clearTimeout(libraryTimer);
  try{const [catalog,tasks]=await Promise.all([api('/documents'),api('/jobs')]); libraryDocuments=catalog.documents; renderDocuments();
    const list=$('#job-list'); list.replaceChildren();
    if(!tasks.jobs.length) list.textContent='还没有上传记录。';
    for(const job of tasks.jobs){const row=document.createElement('div'); row.className='job-row';
      const title=document.createElement('span'); const doc=libraryDocuments.find(d=>d.id===job.document_id); title.textContent=(doc?.title || '食谱')+' · '+job.stage; row.append(title);
      const progress=document.createElement('progress'); progress.max=100; progress.value=job.progress; progress.setAttribute('aria-label','入库进度'); row.append(progress);
      if(job.status==='failed'){const note=document.createElement('p'); note.textContent=job.error || '入库失败'; row.append(note); const retry=document.createElement('button'); retry.textContent='重试入库'; retry.onclick=async()=>{try{await api('/jobs/'+job.id+'/retry',{method:'POST'}); refreshLibrary();}catch(error){notify(error.message);}}; row.append(retry);}
      list.append(row);
    }
    if($('#library-dialog').open && tasks.jobs.some(j=>['queued','running'].includes(j.status))) libraryTimer=setTimeout(refreshLibrary,1500);
  }catch(error){$('#upload-status').textContent=error.message;}
}
$('#open-library').onclick=()=>{$('#library-dialog').showModal(); closeSidebar(); refreshLibrary();};
$('#close-library').onclick=()=>{$('#library-dialog').close(); clearTimeout(libraryTimer);};
$('#document-search').oninput=renderDocuments;
$('#upload-form').onsubmit=async event=>{event.preventDefault(); const file=$('#recipe-file').files[0]; if(!file) return;
  if(file.size>50*1024*1024) return $('#upload-status').textContent='文件不能超过 50 MiB。';
  const button=$('#upload-button'); button.disabled=true; $('#upload-status').textContent='正在上传…';
  try{const form=new FormData(); form.append('file',file); form.append('category',$('#recipe-category').value);
    const response=await fetch('/documents/upload',{method:'POST',headers:state.key?{'Authorization':`Bearer ${state.key}`}:{},body:form});
    const result=await response.json(); if(!response.ok) throw new Error(result.error?.message || friendlyError(response.status));
    $('#upload-status').textContent=result.duplicate?'这份食谱已经入库，可直接提问。':'上传成功，正在后台入库。可以先继续聊天。'; $('#recipe-file').value=''; await refreshLibrary();
  }catch(error){$('#upload-status').textContent=error.message;}finally{button.disabled=false;}
};
boot(); setInterval(checkHealth,45000);
