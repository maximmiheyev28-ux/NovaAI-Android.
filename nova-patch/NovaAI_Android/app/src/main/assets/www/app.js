const $ = (s) => document.querySelector(s);
const chat = $("#chat"), promptBox = $("#prompt"), sendBtn = $("#sendBtn");
let mode = localStorage.getItem("nova.mode") || "solo";
let history = JSON.parse(localStorage.getItem("nova.history") || "[]");
let settings = JSON.parse(localStorage.getItem("nova.settings") || "{}");
let busy = false, callbackCounter = 0;
const pending = new Map();
const FREE_PROVIDERS = [
  {name:"Vireonix", endpoint:"https://vireonix.ai/v1/chat/completions", model:"auto"},
  {name:"BlockRun", endpoint:"https://blockrun.ai/api/v1/chat/completions", model:"nvidia/nemotron-3.5-lightning"}
];
window.__novaApiCallback = (id, raw) => {
  const done = pending.get(id); if (!done) return; pending.delete(id);
  try { done(JSON.parse(raw)); } catch (_) { done({ok:false,error:"Не удалось разобрать ответ API."}); }
};
function saveHistory(){ localStorage.setItem("nova.history", JSON.stringify(history.slice(-100))); }
function setMode(next){
  mode=next;localStorage.setItem("nova.mode",mode);
  document.querySelectorAll(".mode").forEach(b=>b.classList.toggle("active",b.dataset.mode===mode));
  $("#welcomeText").textContent=mode==="solo"?"Nova использует бесплатный облачный ИИ без ключа, если сервис доступен.":"Совет ИИ опрашивает доступные сервисы параллельно и выбирает ответ по эвристической оценке. Это не отдельная локальная языковая модель.";
  $("#composerNote").textContent=mode==="council"?"Автоматический опрос бесплатных сервисов":"Автоматическое подключение бесплатного ИИ";
  $("#connectionStatus").textContent=mode==="council"?"Совет: облачные сервисы":"Бесплатный ИИ · без ключа";
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
function makeMessages(userText){
  const system="Ты — Nova AI. Отвечай на языке пользователя, ясно, полезно и без выдуманных фактов. Если не уверен, обозначь неопределённость.";
  return [{role:"system",content:system},...history.slice(-12).filter(m=>m.role==="user"||m.role==="assistant").map(m=>({role:m.role,content:m.content})),{role:"user",content:userText}];
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
async function answerWithFreeServices(userText){
  const messages=makeMessages(userText);
  if(mode!=="council"){
    const first=await apiCall(FREE_PROVIDERS[0],messages);
    if(first.ok&&first.answer?.trim())return {answer:first.answer,source:first.provider,details:[first]};
    const second=await apiCall(FREE_PROVIDERS[1],messages);
    if(second.ok&&second.answer?.trim())return {answer:second.answer,source:second.provider,details:[first,second]};
    const configured=settings.endpoint&&settings.modelName?await apiCall({name:settings.providerName||"Моя модель",endpoint:settings.endpoint,model:settings.modelName,key:settings.apiKey},messages):null;
    if(configured?.ok&&configured.answer?.trim())return {answer:configured.answer,source:configured.provider,details:[first,second,configured]};
    return {error:"Бесплатные сервисы сейчас недоступны. "+[first,second,configured].filter(Boolean).map(x=>x.provider+": "+(x.error||"ошибка")).join("; ")};
  }
  const results=await Promise.all(FREE_PROVIDERS.map(p=>apiCall(p,messages)));
  const good=results.filter(r=>r.ok&&r.answer?.trim());
  if(!good.length)return {error:"Не удалось получить ответы ни от одного бесплатного сервиса. "+results.map(x=>x.provider+": "+(x.error||"ошибка")).join("; ")};
  good.sort((a,b)=>scoreAnswer(b.answer)-scoreAnswer(a.answer));
  const best=good[0];
  const details=good.map((r,i)=>"Ответ "+(i+1)+" ("+r.provider+") — "+Math.round(scoreAnswer(r.answer)*10)/10+" балла").join("\n");
  return {answer:best.answer,source:best.provider,details:results,meta:"Выбран ответ сервиса "+best.provider+". Эвристическая оценка не гарантирует фактическую точность.\n"+details};
}
async function send(text){
  text=text.trim();if(!text||busy)return;
  busy=true;sendBtn.disabled=true;promptBox.value="";promptBox.style.height="auto";addMessage("user",text,"Ты");
  const thinking=document.createElement("div");thinking.className="typing";thinking.textContent=mode==="council"?"Опрос бесплатных ИИ-сервисов…":"Nova получает ответ…";chat.append(thinking);chat.scrollTop=chat.scrollHeight;
  try{
    const result=await answerWithFreeServices(text);thinking.remove();
    if(result.answer){
      const label=mode==="council"?"Nova · Совет ИИ": "Nova · "+result.source;
      addMessage("assistant",result.answer,label);
      if(result.meta)addMessage("assistant",result.meta,"Как выбран ответ",false);
    }else addMessage("assistant",result.error||"Все сервисы временно недоступны.","Нет ответа");
  }catch(e){thinking.remove();addMessage("assistant","Ошибка: "+(e.message||String(e)),"Ошибка");}
  finally{thinking.remove();busy=false;sendBtn.disabled=false;promptBox.focus();}
}
document.querySelectorAll(".mode").forEach(b=>b.addEventListener("click",()=>setMode(b.dataset.mode)));
document.querySelectorAll(".suggestion").forEach(b=>b.addEventListener("click",()=>send(b.textContent)));
$("#composer").addEventListener("submit",e=>{e.preventDefault();send(promptBox.value);});
promptBox.addEventListener("input",()=>{promptBox.style.height="auto";promptBox.style.height=Math.min(promptBox.scrollHeight,140)+"px";});
promptBox.addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();$("#composer").requestSubmit();}});
$("#settingsOpen").addEventListener("click",()=>{$("#providerName").value=settings.providerName||"";$("#endpoint").value=settings.endpoint||"";$("#apiKey").value=settings.apiKey||"";$("#modelName").value=settings.modelName||"";$("#settingsDialog").showModal();});
$("#providerPreset").addEventListener("change",e=>{
 if(e.target.value==="openrouter"){$("#providerName").value="OpenRouter";$("#endpoint").value="https://openrouter.ai/api/v1/chat/completions";$("#modelName").value="openrouter/free";}
 else if(e.target.value==="groq"){$("#providerName").value="Groq";$("#endpoint").value="https://api.groq.com/openai/v1/chat/completions";$("#modelName").value="llama-3.3-70b-versatile";}
 else if(e.target.value==="custom")$("#providerName").value="Совместимый API";
});
$("#settingsClose").addEventListener("click",()=>$("#settingsDialog").close());
$("#settingsForm").addEventListener("submit",e=>{e.preventDefault();settings={providerName:$("#providerName").value.trim(),endpoint:$("#endpoint").value.trim(),apiKey:$("#apiKey").value,modelName:$("#modelName").value.trim()};localStorage.setItem("nova.settings",JSON.stringify(settings));$("#settingsDialog").close();setMode(mode);});
$("#forgetKey").addEventListener("click",()=>{settings.apiKey="";localStorage.setItem("nova.settings",JSON.stringify(settings));$("#apiKey").value="";});
$("#clearChat").addEventListener("click",()=>{if(confirm("Очистить историю чата?")){history=[];saveHistory();chat.innerHTML="";chat.append(makeWelcome());setMode(mode);}});
function makeWelcome(){const s=document.createElement("section");s.className="welcome";s.id="welcome";s.innerHTML='<div class="orb"><span>✦</span></div><h1>Чем займёмся?</h1><p id="welcomeText"></p><div class="suggestions"><button class="suggestion">Объясни, как работает нейросеть</button><button class="suggestion">Помоги составить план проекта</button><button class="suggestion">Придумай идею приложения</button></div>';s.querySelectorAll(".suggestion").forEach(b=>b.addEventListener("click",()=>send(b.textContent)));return s;}
$("#wipeAll").addEventListener("click",()=>{if(confirm("Удалить историю, настройки и локальные данные Nova?")){localStorage.removeItem("nova.history");localStorage.removeItem("nova.settings");localStorage.removeItem("nova.mode");history=[];settings={};$("#settingsDialog").close();location.reload();}});
if("serviceWorker"in navigator)window.addEventListener("load",()=>navigator.serviceWorker.register("/sw.js").catch(()=>{}));
setMode(mode);renderHistory();