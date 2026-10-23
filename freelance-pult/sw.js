const C="freelance-pult-v24", A=["./","./index.html","./manifest.webmanifest","./jobs.json"];
self.addEventListener("install",e=>e.waitUntil(caches.open(C).then(c=>c.addAll(A)).then(()=>self.skipWaiting())));
self.addEventListener("activate",e=>e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener("fetch",e=>{
 if(e.request.method!=="GET")return;
 const u=new URL(e.request.url);
 const dynamic=u.hostname==="raw.githubusercontent.com"||u.pathname.endsWith("/jobs.json");
 if(dynamic){
  const stableUrl=new URL(u.href);stableUrl.searchParams.delete("v");
  const stableRequest=new Request(stableUrl.href,{method:"GET",mode:"cors",credentials:"omit"});
  e.respondWith(fetch(e.request).then(r=>{
   if(r.ok){const copy=r.clone();caches.open(C).then(c=>c.put(stableRequest,copy)).catch(()=>{});}
   return r;
  }).catch(()=>caches.match(stableRequest).then(cached=>cached||caches.match("./jobs.json")).then(r=>r||new Response('{"items":[]}',{headers:{"Content-Type":"application/json"}}))));
  return;
 }
 e.respondWith(caches.match(e.request).then(hit=>hit||fetch(e.request).then(r=>{
  if(r.ok&&u.origin===self.location.origin&&["/","/index.html","/manifest.webmanifest"].includes(u.pathname)){
   const copy=r.clone();caches.open(C).then(c=>c.put(e.request,copy));
  }
  return r;
 }).catch(()=>caches.match("./index.html"))));
});

self.addEventListener("notificationclick",event=>{
 event.notification.close();
 const target=event.notification.data&&event.notification.data.url;
 event.waitUntil(clients.openWindow(target||"./"));
});

self.addEventListener("push",event=>{
 let data={};
 try{data=event.data?event.data.json():{}}catch(e){data={body:event.data?event.data.text():"Новые подходящие заказы найдены."}}
 const title=typeof data.title==="string"?data.title:"Найдены новые удалённые заказы";
 const body=typeof data.body==="string"?data.body:"Открой Фриланс-пульт и проверь свежие объявления.";
 const target=typeof data.url==="string"&&/^https:\/\//i.test(data.url)?data.url:"./";
 event.waitUntil(self.registration.showNotification(title,{body,tag:"freelance-pult-push",data:{url:target}}));
});
