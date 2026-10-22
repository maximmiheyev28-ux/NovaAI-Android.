const $ = (s) => document.querySelector(s);
const chat = $("#chat"), promptBox = $("#prompt"), sendBtn = $("#sendBtn");
let mode = localStorage.getItem("nova.mode") || "solo";
let history = JSON.parse(localStorage.getItem("nova.history") || "[]");
let settings = JSON.parse(localStorage.getItem("nova.settings") || "{}");
let busy = false, callbackCounter = 0;
const pending = new Map();
const FREE_PROVIDERS = []; // Providers are configured explicitly; no anonymous endpoint is silently trusted.

window.__novaApiCallback = (id, raw) => {
  const done = pending.get(id); if (!done) return; pending.delete(id);
  try { done(JSON.parse(raw)); } catch (_) { done({ok:false,error:"Не удалось разобрать ответ API."}); }
};
function saveHistory(){ localStorage.setItem("nova.history", JSON.stringify(history.slice(-100))); }
function setMode(next){
  mode=next;localStorage.setItem("nova.mode",mode);
  document.querySelectorAll(".mode").forEach(b=>b.classList.toggle("active",b.dataset.mode===mode));
  const welcomeText=$("#welcomeText"); if(welcomeText) welcomeText.textContent=mode==="solo"?"Локальная нейросеть Qwen запускается на устройстве. При первом запросе потребуется скачать модель (~1,07 ГБ); затем она работает без интернета.":"Добавь до трёх совместимых API в настройках: Nova опросит их параллельно и покажет сравнение ответов.";
  const composerNote=$("#composerNote"); if(composerNote) composerNote.textContent=mode==="council"?"Параллельный опрос настроенных моделей":"Локальная модель на устройстве";
  const connectionStatus=$("#connectionStatus"); if(connectionStatus) if(mode==="council"){const count=getProviders().length;connectionStatus.textContent="Совет ИИ · "+count+" моделей";}else{try{const st=JSON.parse(window.NovaBridge?.getLocalModelStatus?.()||"{}");connectionStatus.textContent=st.ready?"Локальная модель · готова":"Локальная модель · загрузится при первом запросе";}catch(_){connectionStatus.textContent="Локальная модель";}}
}
function addMessage(role,content,label,persist=true){
  $("#welcome")?.remove();
  const wrap=document.createElement("div");wrap.className="message "+role;
  const lab=document.createElement("div");lab.className="label";lab.textContent=label||(role==="user"?"Ты":"Nova");
  const bubble=document.createElement("div");bubble.className="bubble";bubble.textContent=content;
  wrap.append(lab,bubble);chat.append(wrap);chat.scrollTop=chat.scrollHeight;
  if(persist){history.push({role,content,label:lab.textContent,mode,at:Date.now()});saveHistory();}
}
function renderHistory(){if(!history.length)return;$("#welcome")?.remove();history.forEach(m=>addMessage(m.role,m.content,m.label,false));}
function apiCall(provider,messages){
  return new Promise(resolve=>{
    if(!window.NovaBridge?.chat){resolve({ok:false,provider:provider.name,error:"Сетевой модуль недоступен"});return;}
    const id="nova-"+(++callbackCounter);
    const timeout=setTimeout(()=>{pending.delete(id);resolve({ok:false,provider:provider.name,error:"Тайм-аут"});},45000);
    pending.set(id,r=>{clearTimeout(timeout);r.provider=provider.name;resolve(r);});
    try{window.NovaBridge.chat(JSON.stringify({endpoint:provider.endpoint,apiKey:provider.key||"",modelName:provider.model,messages}),id);}
    catch(e){clearTimeout(timeout);pending.delete(id);resolve({ok:false,provider:provider.name,error:e.message||"Ошибка запроса"});}
  });
}
window.__novaLocalProgress = (id, message) => {
  const active = document.querySelector(".typing");
  if (active) active.textContent = String(message || "Локальная модель работает…");
};
function localCall(messages){
  return new Promise(resolve=>{
    if(!window.NovaBridge?.localChat){resolve({ok:false,error:"Модуль локальной нейросети недоступен в этой сборке."});return;}
    const id="nova-local-"+(++callbackCounter);
    const timeout=setTimeout(()=>{pending.delete(id);resolve({ok:false,error:"Локальная модель работает слишком долго. Проверь свободную память устройства и повтори запрос."});},20*60*1000);
    pending.set(id,result=>{clearTimeout(timeout);resolve(result);});
    try{window.NovaBridge.localChat(JSON.stringify({messages}),id);}
    catch(e){clearTimeout(timeout);pending.delete(id);resolve({ok:false,error:e.message||"Ошибка локальной модели"});}
  });
}
function makeMessages(userText){
  const system="Ты — Nova AI. Отвечай на языке пользователя, ясно, полезно и без выдуманных фактов. Если не уверен, обозначь неопределённость.";
  return [{role:"system",content:system},...history.slice(-13,-1).filter(m=>m.role==="user"||m.role==="assistant").map(m=>({role:m.role,content:m.content})),{role:"user",content:userText}];
}
function scoreAnswer(answer){
  const s=answer.trim();if(!s)return -100;
  let score=Math.min(s.length,4000)/100;
  if(s.length<80)score-=2;
  if(/\b(не знаю|не могу помочь|ошибка|error)\b/i.test(s))score-=1;
  if((s.match(/\n/g)||[]).length>=2)score+=1;
  if(/\b(например|потому что|во-первых|итого|вывод)\b/i.test(s))score+=1;
  if(/(.)\1{8,}/.test(s))score-=3;
  return score;
}
function getProviders(){
  const list=Array.isArray(settings.providers)?settings.providers:[];
  if(!list.length&&settings.endpoint&&settings.modelName) return [{name:settings.providerName||"Настроенный API",endpoint:settings.endpoint,model:settings.modelName,key:settings.apiKey||""}];
  return list.filter(p=>p&&p.endpoint&&p.model).slice(0,3);
}
function escapeText(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
async function answerWithFreeServices(userText){
  const messages=makeMessages(userText), providers=getProviders();
  if(mode==="solo"){
    const local=await localCall(messages);
    if(local?.ok&&local.answer?.trim()) return {answer:local.answer,source:local.provider||"Локальная Qwen 1.5B",local:true};
    if(providers.length){
      const fallback=await apiCall(providers[0],messages);
      if(fallback.ok&&fallback.answer?.trim()) return {answer:fallback.answer,source:fallback.provider,meta:"Локальная модель недоступна; ответ получен от настроенного облачного API."};
      return {error:(local?.error||"Локальная модель недоступна.")+"\nРезервный API: "+(fallback.error||"нет ответа")};
    }
    return {error:local?.error||"Локальная модель не ответила."};
  }
  if(providers.length<2) return {error:"Для режима «Совет ИИ» настрой минимум две совместимые модели. Локальная модель работает отдельно в режиме Nova."};
  const results=await Promise.all(providers.map(p=>apiCall(p,messages).catch(e=>({ok:false,provider:p.name,error:e.message||"Ошибка запроса"}))));
  const good=results.filter(r=>r.ok&&r.answer&&r.answer.trim());
  if(!good.length) return {error:"Ни одна модель не ответила. "+results.map(r=>(r.provider||"Модель")+": "+(r.error||"пустой ответ")).join(" · ")};
  const ranked=good.map(r=>({...r,score:scoreAnswer(r.answer)})).sort((a,b)=>b.score-a.score);
  const sections=ranked.map(r=>"Модель: "+r.provider+"\n"+r.answer);
  const meta="Сравнение "+good.length+" из "+providers.length+" ответивших моделей. Порядок рассчитан по эвристике длины и структуры, а не по проверке фактической точности.\n\n"+sections.join("\n\n──────────\n\n");
  return {answer:ranked[0].answer,source:ranked[0].provider,meta};
}
async function send(text){
  text=text.trim();if(!text||busy)return;
  busy=true;sendBtn.disabled=true;promptBox.value="";promptBox.style.height="auto";addMessage("user",text,"Ты");
  const thinking=document.createElement("div");thinking.className="typing";thinking.textContent=mode==="council"?"Параллельный опрос моделей…":"Запуск локальной нейросети…";chat.append(thinking);chat.scrollTop=chat.scrollHeight;
  try{
    const result=await answerWithFreeServices(text);thinking.remove();
    if(result.answer){
      const label=mode==="council"?"Nova · Совет ИИ": "Nova · "+result.source;
      addMessage("assistant",result.answer,label);
      if(result.meta)addMessage("assistant",result.meta,result.local?"Резервный режим":"Сравнение моделей",false);
    }else addMessage("assistant",result.error||"Все сервисы временно недоступны.","Нет ответа");
  }catch(e){thinking.remove();addMessage("assistant","Ошибка: "+(e.message||String(e)),"Ошибка");}
  finally{thinking.remove();busy=false;sendBtn.disabled=false;promptBox.focus();}
}
document.querySelectorAll(".mode").forEach(b=>b.addEventListener("click",()=>setMode(b.dataset.mode)));
document.querySelectorAll(".suggestion").forEach(b=>b.addEventListener("click",()=>send(b.textContent)));
$("#composer").addEventListener("submit",e=>{e.preventDefault();send(promptBox.value);});
promptBox.addEventListener("input",()=>{promptBox.style.height="auto";promptBox.style.height=Math.min(promptBox.scrollHeight,140)+"px";});
promptBox.addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();$("#composer").requestSubmit();}});
$("#settingsOpen").addEventListener("click",()=>{const p=getProviders();const first=p[0]||{};$("#providerName").value=first.name||settings.providerName||"";$("#endpoint").value=first.endpoint||settings.endpoint||"";$("#apiKey").value=first.key||settings.apiKey||"";$("#modelName").value=first.model||settings.modelName||"";$("#endpoint2").value=p[1]?.endpoint||"";$("#providerName2").value=p[1]?.name||"";$("#apiKey2").value=p[1]?.key||"";$("#modelName2").value=p[1]?.model||"";$("#endpoint3").value=p[2]?.endpoint||"";$("#providerName3").value=p[2]?.name||"";$("#apiKey3").value=p[2]?.key||"";$("#modelName3").value=p[2]?.model||"";$("#settingsDialog").showModal();});
$("#providerPreset").addEventListener("change",e=>{
 if(e.target.value==="openrouter"){$("#providerName").value="OpenRouter";$("#endpoint").value="https://openrouter.ai/api/v1/chat/completions";$("#modelName").value="openrouter/free";}
 else if(e.target.value==="groq"){$("#providerName").value="Groq";$("#endpoint").value="https://api.groq.com/openai/v1/chat/completions";$("#modelName").value="llama-3.3-70b-versatile";}
 else if(e.target.value==="custom")$("#providerName").value="Совместимый API";
});
$("#settingsClose").addEventListener("click",()=>$("#settingsDialog").close());
$("#settingsForm").addEventListener("submit",e=>{e.preventDefault();settings={providers:[{name:$("#providerName").value.trim()||"Модель 1",endpoint:$("#endpoint").value.trim(),key:$("#apiKey").value,model:$("#modelName").value.trim()},{name:$("#providerName2").value.trim()||"Модель 2",endpoint:$("#endpoint2").value.trim(),key:$("#apiKey2").value,model:$("#modelName2").value.trim()},{name:$("#providerName3").value.trim()||"Модель 3",endpoint:$("#endpoint3").value.trim(),key:$("#apiKey3").value,model:$("#modelName3").value.trim()}].filter(p=>p.endpoint&&p.model)};localStorage.setItem("nova.settings",JSON.stringify(settings));$("#settingsDialog").close();setMode(mode);});
$("#forgetKey").addEventListener("click",()=>{settings.apiKey="";if(Array.isArray(settings.providers))settings.providers.forEach(p=>p.key="");localStorage.setItem("nova.settings",JSON.stringify(settings));["apiKey","apiKey2","apiKey3"].forEach(id=>$("#"+id).value="");});
$("#clearChat").addEventListener("click",()=>{if(confirm("Очистить историю чата?")){history=[];saveHistory();chat.innerHTML="";chat.append(makeWelcome());setMode(mode);}});
function makeWelcome(){const s=document.createElement("section");s.className="welcome";s.id="welcome";s.innerHTML='<div class="orb"><span>✦</span></div><h1>Чем займёмся?</h1><p id="welcomeText"></p><div class="suggestions"><button class="suggestion">Объясни, как работает нейросеть</button><button class="suggestion">Помоги составить план проекта</button><button class="suggestion">Придумай идею приложения</button></div>';s.querySelectorAll(".suggestion").forEach(b=>b.addEventListener("click",()=>send(b.textContent)));return s;}
$("#wipeAll").addEventListener("click",()=>{if(confirm("Удалить историю, настройки и локальные данные Nova?")){localStorage.removeItem("nova.history");localStorage.removeItem("nova.settings");localStorage.removeItem("nova.mode");history=[];settings={};$("#settingsDialog").close();location.reload();}});
if("serviceWorker"in navigator)window.addEventListener("load",()=>navigator.serviceWorker.register("/sw.js").catch(()=>{}));
setMode(mode);renderHistory();