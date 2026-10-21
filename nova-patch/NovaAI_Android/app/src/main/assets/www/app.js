const $ = (s) => document.querySelector(s);
const chat = $("#chat"), promptBox = $("#prompt"), sendBtn = $("#sendBtn");
let mode = localStorage.getItem("nova.mode") || "solo";
let history = JSON.parse(localStorage.getItem("nova.history") || "[]");
let settings = JSON.parse(localStorage.getItem("nova.settings") || "{}");
let busy = false, callbackCounter = 0;
const pending = new Map();
window.__novaApiCallback = (id, raw) => {
  const done = pending.get(id); if (!done) return; pending.delete(id);
  try { done(JSON.parse(raw)); } catch (_) { done({ok:false,error:"Не удалось разобрать ответ API."}); }
};
function saveHistory(){ localStorage.setItem("nova.history", JSON.stringify(history.slice(-100))); }
function setMode(next){
  mode = next; localStorage.setItem("nova.mode", mode);
  document.querySelectorAll(".mode").forEach(b=>b.classList.toggle("active",b.dataset.mode===mode));
  $("#welcomeText").textContent = mode==="solo" ? "Nova — персональный ИИ-помощник. Подключи модель в настройках, чтобы начать диалог." : "Совет ИИ отправляет запрос подключённой модели. Несколько независимых моделей пока не подключены.";
  $("#composerNote").textContent = settings.endpoint && settings.modelName ? "Модель: "+settings.modelName : "Для ответов ИИ укажи API и модель в настройках";
  $("#connectionStatus").textContent = settings.endpoint && settings.modelName ? "Модель настроена" : "Требуется подключение модели";
}
function addMessage(role, content, label, persist=true){
  $("#welcome")?.remove();
  const wrap=document.createElement("div"); wrap.className="message "+role;
  const lab=document.createElement("div"); lab.className="label"; lab.textContent=label || (role==="user"?"Ты":mode==="council"?"Nova · Совет ИИ":"Nova");
  const bubble=document.createElement("div"); bubble.className="bubble"; bubble.textContent=content;
  wrap.append(lab,bubble); chat.append(wrap); chat.scrollTop=chat.scrollHeight;
  if(persist){history.push({role,content,label:lab.textContent,mode,at:Date.now()});saveHistory();}
}
function renderHistory(){ if(!history.length)return; $("#welcome")?.remove(); history.forEach(m=>addMessage(m.role,m.content,m.label,false)); }
function callConfiguredModel(){
  if(!settings.endpoint || !settings.modelName) return Promise.resolve({ok:false,notConfigured:true});
  if(!window.NovaBridge || !window.NovaBridge.chat) return Promise.resolve({ok:false,error:"Сетевой модуль недоступен. Установи актуальную сборку Nova AI."});
  const system = mode==="council"
    ? "Ты — Nova AI в режиме Совет ИИ. Ты одна подключённая модель, если не указано обратное. Не притворяйся, что опрошены другие модели. Сравни варианты и обозначай неопределённость."
    : "Ты — Nova AI, персональный ИИ-помощник. Отвечай на языке пользователя, ясно и по существу. Рассуждай внимательно, не выдумывай факты и обозначай неопределённость. Помогай с обучением, программированием, анализом и творческими задачами.";
  const messages=[{role:"system",content:system},...history.slice(-12).filter(m=>m.role==="user"||m.role==="assistant").map(m=>({role:m.role,content:m.content}))];
  const id="nova-"+(++callbackCounter);
  return new Promise(resolve=>{
    const timeout=setTimeout(()=>{pending.delete(id);resolve({ok:false,error:"Истекло время ожидания ответа API."});},65000);
    pending.set(id,result=>{clearTimeout(timeout);resolve(result);});
    try { window.NovaBridge.chat(JSON.stringify({endpoint:settings.endpoint,apiKey:settings.apiKey||"",modelName:settings.modelName,messages}),id); }
    catch(e){clearTimeout(timeout);pending.delete(id);resolve({ok:false,error:e.message||"Ошибка сетевого модуля"});}
  });
}
async function send(text){
  text=text.trim(); if(!text||busy)return;
  busy=true;sendBtn.disabled=true;promptBox.value="";promptBox.style.height="auto";addMessage("user",text,"Ты");
  const thinking=document.createElement("div");thinking.className="typing";thinking.textContent="Nova формирует ответ…";chat.append(thinking);chat.scrollTop=chat.scrollHeight;
  try{
    const result=await callConfiguredModel();thinking.remove();
    if(result.ok) addMessage("assistant",result.answer,mode==="council"?"Nova · Совет ИИ":"Nova");
    else if(result.notConfigured) addMessage("assistant","Чтобы Nova отвечала как настоящий ИИ, подключи модель в ⚙ Настройки. Выбери совместимый API, укажи endpoint, название модели и API-ключ провайдера. Без языковой модели я не буду подменять ответ заранее написанным текстом.","Настройка подключения");
    else addMessage("assistant","Не удалось получить ответ: "+result.error+"\n\nПроверь endpoint, идентификатор модели, API-ключ, интернет и лимиты провайдера.","Ошибка подключения");
  }catch(e){thinking.remove();addMessage("assistant","Ошибка: "+(e.message||String(e)),"Ошибка");}
  finally{thinking.remove();busy=false;sendBtn.disabled=false;promptBox.focus();}
}
document.querySelectorAll(".mode").forEach(b=>b.addEventListener("click",()=>setMode(b.dataset.mode)));
document.querySelectorAll(".suggestion").forEach(b=>b.addEventListener("click",()=>send(b.textContent)));
$("#composer").addEventListener("submit",e=>{e.preventDefault();send(promptBox.value);});
promptBox.addEventListener("input",()=>{promptBox.style.height="auto";promptBox.style.height=Math.min(promptBox.scrollHeight,140)+"px";});
promptBox.addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();$("#composer").requestSubmit();}});
$("#settingsOpen").addEventListener("click",()=>{
  $("#providerName").value=settings.providerName||"";$("#endpoint").value=settings.endpoint||"";$("#apiKey").value=settings.apiKey||"";$("#modelName").value=settings.modelName||"";
  $("#settingsDialog").showModal();
});
$("#providerPreset").addEventListener("change",e=>{
  if(e.target.value==="openrouter"){$("#providerName").value="OpenRouter";$("#endpoint").value="https://openrouter.ai/api/v1/chat/completions";$("#modelName").value="openrouter/free";}
  else if(e.target.value==="groq"){$("#providerName").value="Groq";$("#endpoint").value="https://api.groq.com/openai/v1/chat/completions";$("#modelName").value="llama-3.3-70b-versatile";}
  else if(e.target.value==="custom"){$("#providerName").value="Совместимый API";}
});
$("#settingsClose").addEventListener("click",()=>$("#settingsDialog").close());
$("#settingsForm").addEventListener("submit",e=>{
  e.preventDefault();settings={providerName:$("#providerName").value.trim(),endpoint:$("#endpoint").value.trim(),apiKey:$("#apiKey").value,modelName:$("#modelName").value.trim()};
  localStorage.setItem("nova.settings",JSON.stringify(settings));$("#settingsDialog").close();setMode(mode);
});
$("#forgetKey").addEventListener("click",()=>{settings.apiKey="";localStorage.setItem("nova.settings",JSON.stringify(settings));$("#apiKey").value="";});
$("#clearChat").addEventListener("click",()=>{if(confirm("Очистить историю чата?")){history=[];saveHistory();chat.innerHTML="";chat.append(makeWelcome());setMode(mode);}});
function makeWelcome(){
  const s=document.createElement("section");s.className="welcome";s.id="welcome";
  s.innerHTML='<div class="orb"><span>✦</span></div><h1>Чем займёмся?</h1><p id="welcomeText"></p><div class="suggestions"><button class="suggestion">Объясни, как работает нейросеть</button><button class="suggestion">Помоги составить план проекта</button><button class="suggestion">Придумай идею приложения</button></div>';
  s.querySelectorAll(".suggestion").forEach(b=>b.addEventListener("click",()=>send(b.textContent)));return s;
}
$("#wipeAll").addEventListener("click",()=>{if(confirm("Удалить историю, настройки и локальные данные Nova?")){localStorage.removeItem("nova.history");localStorage.removeItem("nova.settings");localStorage.removeItem("nova.mode");history=[];settings={};$("#settingsDialog").close();location.reload();}});
if("serviceWorker" in navigator)window.addEventListener("load",()=>navigator.serviceWorker.register("/sw.js").catch(()=>{}));
setMode(mode);renderHistory();