#!/usr/bin/env python3
"""Fetch the public first page of Kwork projects and recommend only feasible deliverables."""
from __future__ import annotations
import argparse,datetime as dt,html,json,re,sys,urllib.error,urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin,urlsplit,urlunsplit
from collect_public_jobs import clean_text,parse_money,score_item,now_iso

URL="https://kwork.ru/projects"
UA="FreelancePult/1.0 (+https://github.com/maximmiheyev28-ux/NovaAI-Android.)"
PATH_RE=re.compile(r"^/projects/(\d+)/view/?$",re.I)
ABILITY=[
("text",re.compile(r"текст|копирайт|стать[яи]|рерайт|описан.{0,16}(?:товар|услуг|карточк)|контент|перевод|редакт|коррект|набор текста|copywrit|proofread|translation|article|blog post|rewrite|product description",re.I)),
("documents",re.compile(r"документ|word|docx|pdf|презентац|pptx|слайд|конспект|сводк|отч[её]т|резюме|summary|report writing|оформить файл",re.I)),
("data",re.compile(r"excel|google sheets|таблиц|обработк.{0,18}данн|ввод данных|сбор данных|csv|формул[ыа]|data entry|spreadsheet|data cleaning|data analysis",re.I)),
("code",re.compile(r"python|javascript|typescript|html|css|php|sql|программир|написать код|исправить код|скрипт|разработк.{0,20}(?:сайт|лендинг|бот)|создать сайт|сделать сайт|лендинг|бота|api|интеграц|автоматизац|парсер|wordpress|верстк",re.I)),
("research",re.compile(r"исследован|подбор.{0,18}информац|поиск.{0,18}информац|сравнен.{0,20}(?:товар|сервис|конкурент)|анализ данных|сбор информации|market research|research assistant|competitor research",re.I)),
("image",re.compile(r"логотип|баннер|обложк|иллюстрац|иконк|инфографик|карточк.{0,20}(?:товар|маркетплейс)|изображен|картинк|thumbnail|illustration|logo design|banner design",re.I))]
OUT=re.compile(r"находить потенциальных клиентов|найти потенциальных клиентов|привлечени[ея] клиентов|писать клиентам|пишите клиентам|отправлять клиентам|рассылк|обзвон|телефонн.{0,12}звон|накрут.{0,20}отзыв|оставить отзыв|фейков.{0,15}отзыв|курьер|приехать|выезд|в офисе|офлайн|монтаж видео|видеомонтаж|озвучк|диктор|запись голоса|3d модел|autocad|solidworks|revit|archicad|в figma|исходник.{0,20}(?:figma|photoshop|illustrator)|медицинск.{0,20}(?:диагноз|заключен|консультац)|юридическ.{0,20}консультац|вести бухгалтерию|создать аккаунт|зарегистрировать аккаунт|обойти защиту|код из sms|пароль от банка|перевести чужие деньги|принимать чужие деньги|обналич",re.I)
RISK=re.compile(r"оплат.{0,25}(?:доступ|регистрац|активац|разблокировк)|внесите залог|код из sms|sms-код|cvv|cvc|пароль от банка|перевести деньги.{0,30}(?:дальше|треть|на карту)|обналич|дроппер|установить.{0,25}\bapk\b|удалённ.{0,15}доступ|anydesk|teamviewer",re.I)
BLOCK={"script","style","noscript","svg"}; VOID={"area","base","br","col","embed","hr","img","input","link","meta","param","source","track","wbr"}; BLOCKS={"article","div","li","p","section","h1","h2","h3","h4","br","tr"}
class Node:
 def __init__(self,tag,attrs,parent=None): self.tag,self.attrs,self.parent=tag,attrs,parent; self.children=[]; self.cache=None
 def text(self):
  if self.cache is None:
   s=html.unescape("".join(x.text() if isinstance(x,Node) else x for x in self.children)); s=re.sub(r"[ \t\r\f\v]+"," ",s); s=re.sub(r" *\n *","\n",s); self.cache=re.sub(r"\n{3,}","\n\n",s).strip()
  return self.cache
class Parser(HTMLParser):
 def __init__(self): super().__init__(convert_charrefs=True); self.root=Node("root",{}); self.stack=[self.root]; self.skip=None; self.anchors=[]
 def handle_starttag(self,t,attrs):
  if self.skip:return
  if t in BLOCK:self.skip=t;return
  if t in BLOCKS:self.stack[-1].children.append("\n")
  n=Node(t,{str(k):str(v or "") for k,v in attrs},self.stack[-1]);self.stack[-1].children.append(n)
  if t=="a":self.anchors.append(n)
  if t not in VOID:self.stack.append(n)
 def handle_startendtag(self,t,a):
  self.handle_starttag(t,a)
  if t not in VOID:self.handle_endtag(t)
 def handle_endtag(self,t):
  if self.skip:
   if t==self.skip:self.skip=None
   return
  for i in range(len(self.stack)-1,0,-1):
   if self.stack[i].tag==t:
    del self.stack[i:]
    if t in BLOCKS:self.stack[-1].children.append("\n")
    return
 def handle_data(self,d):
  if not self.skip and d:self.stack[-1].children.append(d)
def links(p):
 out=[]
 for a in p.anchors:
  href=a.attrs.get("href","").strip()
  if not href:continue
  u=urlsplit(urljoin(URL,href));host=(u.hostname or "").lower();m=PATH_RE.fullmatch(u.path);title=clean_text(a.text()).replace("\n"," ").strip() or a.attrs.get("title","").strip()
  if m and u.scheme=="https" and (host=="kwork.ru" or host.endswith(".kwork.ru")) and title and title.lower() not in {"подробнее","показать полностью"}:out.append((m.group(1),urlunsplit(("https","kwork.ru",u.path.rstrip("/"),"","")),a,title[:200]))
 return out
def card(a,title):
 mark=re.compile(r"осталось\s*:|предложени[йя]\s*:|покупатель\s*:|желаемый бюджет|цена\s*(?:до)?\s*:|₽|руб",re.I); c=[];n=a.parent;d=0
 while n is not None and d<12:
  t=n.text()
  if title.casefold() in t.casefold() and 70<=len(t)<=8000:
   if mark.search(t):return t
   c.append(t)
  n=n.parent;d+=1
 return min(c,key=len) if c else (a.parent.text() if a.parent else a.text())
def relative_date(t,now):
 m=re.search(r"(?:размещено|опубликовано|добавлено)\s*(\d+)\s*(мин(?:ут[уы]?)?\.?|м|час(?:а|ов)?\.?|ч\.?|дн(?:я|ей)?\.?)",t,re.I)
 if not m:return ""
 n=int(m.group(1));u=m.group(2).lower();delta=dt.timedelta(minutes=n) if u.startswith(("мин","м")) else dt.timedelta(hours=n) if u.startswith(("час","ч")) else dt.timedelta(days=n)
 return (now-delta).replace(microsecond=0).isoformat().replace("+00:00","Z")
def expiry(t,now):
 m=re.search(r"осталось\s*:\s*([^\n]{1,100})",t,re.I)
 if not m:return "",""
 label=clean_text(m.group(1));parts=re.findall(r"(\d+)\s*(дн(?:я|ей)?|д\.?|час(?:а|ов)?|ч\.?|мин(?:ут[уы]?)?\.?)",label,re.I)
 if not parts:return label,""
 delta=dt.timedelta()
 for n,u in parts:
  u=u.lower();v=int(n);delta+=dt.timedelta(days=v) if u.startswith("д") else dt.timedelta(hours=v) if u.startswith(("ч","час")) else dt.timedelta(minutes=v)
 return label,(now+delta).replace(microsecond=0).isoformat().replace("+00:00","Z")
def get_budget(t):
 m=re.search(r"(?:желаемый бюджет|цена\s*(?:до)?|бюджет|стоимость)\s*:?\s*[\s\S]{0,110}?\d[\d \u00a0]{0,12}\s*(?:₽|руб(?:\.|лей|ля|ль)?)(?!\w)",t,re.I)
 return parse_money(m.group(0) if m else t)
def classify(title,t):
 whole=title+"\n"+t
 if OUT.search(whole):return None,"",""
 found=[name for name,pat in ABILITY if pat.search(whole)]
 if not found:return None,"",""
 c=found[0];reason={"text":"Могу подготовить текстовый результат по ТЗ и проверить соответствие требованиям.","documents":"Могу оформить документ, отчёт или презентацию в готовый файл.","data":"Могу обработать данные и подготовить таблицу/CSV/XLSX с проверкой структуры и формул.","code":"Могу написать или исправить код и подготовить файлы проекта; публикация в чужом аккаунте потребует отдельного доступа.","research":"Могу исследовать доступные источники и оформить выводы со ссылками.","image":"Могу создать оригинальное изображение; редактируемый исходник в сторонней программе не обещается."}[c]
 deliver={"text":"готовый текст","documents":"DOCX/PDF/PPTX","data":"таблица или CSV/XLSX","code":"исходные файлы кода и инструкция","research":"отчёт со ссылками","image":"растровое изображение"}[c]
 return c,reason,deliver
def parse_kwork_html(page,checked_at=None,previous=None):
 stamp=checked_at or now_iso();now=dt.datetime.fromisoformat(stamp.replace("Z","+00:00"));p=Parser();p.feed(page);p.close();found=links(p)
 if not found:raise ValueError("Kwork public page has no project links; feed left unchanged.")
 prev={str(i.get("link","")).rstrip("/"):i for i in (previous or {}).get("items",[]) if isinstance(i,dict)}
 items={};skip=0;risks_n=0
 for pid,url,a,title in found:
  if pid in items:continue
  t=card(a,title);desc=re.sub(re.escape(title)," ",t,count=1,flags=re.I)
  desc=re.sub(r"(?:покупатель\s*:|размещено проектов на бирже\s*:|нанято\s*:|осталось\s*:|предложений\s*:|желаемый бюджет\s*:|допустимый\s*:|цена\s*(?:до)?\s*:).*"," ",desc,flags=re.I);desc=clean_text(desc)[:1200]
  cat,reason,deliver=classify(title,t)
  if not cat:skip+=1;continue
  pub=relative_date(t,now);deadline,expires=expiry(t,now);low,high,btxt=get_budget(t)
  _,_,riskflags,_,critical=score_item(title,t,pub,high)
  if critical or RISK.search(t):risks_n+=1;continue
  if re.search(r"ист[её]к|заверш[её]н|закрыт",t,re.I):continue
  titlepat=next(pat for name,pat in ABILITY if name==cat);score=72 if titlepat.search(title) else 64
  if high:score+=7
  if pub:
   age=(now-dt.datetime.fromisoformat(pub.replace("Z","+00:00"))).total_seconds()/3600
   if 0<=age<=24:score+=10
   elif 0<=age<=72:score+=5
  if riskflags:score-=min(15,5*len(riskflags))
  if re.search(r"срочно|сегодня|за час|максимально быстро",title+"\n"+t,re.I):score-=4
  old=prev.get(url.rstrip("/"),{});proposal=re.search(r"предложений\s*:\s*(\d+)",t,re.I);buyer=re.search(r"покупатель\s*:\s*([^\n·]{1,80})",t,re.I)
  items[pid]={"id":"kwork-"+pid,"kworkProjectId":pid,"title":title,"link":url,"description":desc or t[:1200],"publishedAt":pub,
   "firstSeenAt":old.get("firstSeenAt") or old.get("discoveredAt") or stamp,"expiryAt":expires,"deadlineHint":deadline,"source":"Kwork",
   "feedName":"Kwork public projects page","score":max(50,min(98,score)),"fitReasons":["Основная задача входит в доступные форматы выполнения",reason],
   "riskFlags":riskflags,"criticalRisk":False,"hasRelevantKeywords":True,"recommendation":"recommend","recommendationReason":reason,
   "executionCategory":cat,"deliverable":deliver,"budgetMin":low,"budgetMax":high,"budgetText":btxt,"currency":"RUB" if btxt else "",
   "proposalCount":int(proposal.group(1)) if proposal else None,"companyName":buyer.group(1).strip() if buyer else "",
   "locationText":"Проект размещён на Kwork; проверь доступность условий и выплаты в своём аккаунте.",
   "paymentCheck":"Оплата — через Kwork. Не плати за регистрацию или разблокировку выплаты."}
 out=sorted(items.values(),key=lambda x:(x["score"],x.get("publishedAt") or "",-int(x.get("proposalCount") or 999)),reverse=True)
 return {"schemaVersion":1,"source":"Kwork public projects page","checkedAt":stamp,
  "maxAgeNotice":"Обновляется каждые 30 минут по публичной первой странице Kwork /projects. Если дата публикации неизвестна, указывается время первого обнаружения. Оценка означает, что результат можно подготовить доступными инструментами ChatGPT, а не гарантию найма или оплаты.",
  "attribution":"Источник: публичная страница проектов Kwork (https://kwork.ru/projects). Открой оригинал перед откликом.",
  "feeds":[{"name":"Kwork public projects page","url":URL,"ok":True,"items":len(found),"source":"Kwork"}],
  "stats":{"sourceListingsFound":len(found),"fetchedUnique":len(found),"freshCandidates":len(out),"matchedCandidates":len(out),"excludedNotExecutable":skip,"excludedCriticalRisk":risks_n,"carriedFromPrevious":0,"feedErrors":[]},"items":out}
def fetch_page():
 req=urllib.request.Request(URL,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8","Accept-Language":"ru,en;q=0.7"})
 with urllib.request.urlopen(req,timeout=30) as r:
  host=(urlsplit(r.geturl()).hostname or "").lower()
  if not(host=="kwork.ru" or host.endswith(".kwork.ru")):raise RuntimeError("Public request redirected away from Kwork.")
  raw=r.read(5_000_001)
  if len(raw)>5_000_000:raise RuntimeError("Kwork page exceeded the size limit.")
 return raw.decode("utf-8",errors="replace")
def main():
 p=argparse.ArgumentParser();p.add_argument("--output",default="freelance-pult/jobs.json");p.add_argument("--previous",default="/tmp/previous-jobs.json");a=p.parse_args()
 try:
  prev=json.loads(Path(a.previous).read_text(encoding="utf-8")) if Path(a.previous).exists() else {}
  payload=parse_kwork_html(fetch_page(),previous=prev)
 except (urllib.error.URLError,urllib.error.HTTPError,TimeoutError,OSError,RuntimeError,ValueError,json.JSONDecodeError) as e:
  print(f"Kwork feed not refreshed: {type(e).__name__}: {str(e)[:240]}",file=sys.stderr);return 2
 out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps(payload["stats"],ensure_ascii=False));print(f"Wrote {len(payload['items'])} Kwork recommendations to {out}");return 0
if __name__=="__main__":raise SystemExit(main())
