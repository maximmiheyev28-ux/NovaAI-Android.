const C="freelance-pult-v4", A=["./","./index.html","./manifest.webmanifest","./jobs.json"];
self.addEventListener("install",e=>e.waitUntil(caches.open(C).then(c=>c.addAll(A))));
self.addEventListener("activate",e=>e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k))))));
self.addEventListener("fetch",e=>{
 if(e.request.method!=="GET")return;
 const u=new URL(e.request.url);
 const dynamic=u.hostname==="raw.githubusercontent.com"||u.pathname.endsWith("/jobs.json");
 if(dynamic){
  e.respondWith(fetch(e.request).catch(()=>caches.match("./jobs.json").then(r=>r||new Response('{"items":[]}',{headers:{"Content-Type":"application/json"}}))));
  return;
 }
 e.respondWith(caches.match(e.request).then(hit=>hit||fetch(e.request).then(r=>{
  if(r.ok&&u.origin===self.location.origin&&["/","/index.html","/manifest.webmanifest"].includes(u.pathname)){
   const copy=r.clone();caches.open(C).then(c=>c.put(e.request,copy));
  }
  return r;
 }).catch(()=>caches.match("./index.html"))));
});
