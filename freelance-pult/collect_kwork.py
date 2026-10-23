#!/usr/bin/env python3
"""Fetch five public pages of Kwork projects and recommend only feasible deliverables."""
from __future__ import annotations
import argparse,datetime as dt,html,json,re,sys,urllib.error,urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin,urlsplit,urlunsplit
from collect_public_jobs import clean_text,parse_money,score_item,now_iso

URL="https://kwork.ru/projects"
UA="FreelancePult/1.0 (+https://github.com/maximmiheyev28-ux/NovaAI-Android.)"
PATH_RE=re.compile(r"^/projects/(\d+)(?:/view)?/?$",re.I)
ABILITY=[
("text",re.compile(r"текст|копирайт|seo[- ]?текст|сео[- ]?текст|стать[яи]|рерайт|описан.{0,16}(?:товар|услуг|карточк)|контент|пост.{0,15}(?:соцсет|телеграм|вк|instagram|инстаграм)|соцсет.{0,20}(?:пост|контент|публикац)|рекламн.{0,20}(?:текст|объявлен|слоган)|продающ.{0,15}текст|лендинг.{0,20}текст|текст.{0,20}лендинг|коммерческ.{0,15}предложен|рассылк.{0,20}(?:текст|письм)|email.{0,15}(?:текст|рассылк)|письм.{0,20}(?:клиент|делов|продаж)|контент.?план|нейминг|назван.{0,20}(?:бренд|компан|продукт)|редакт|коррект|набор текста|поздравлени[ея]|стихотвор|пожелан|сценари[йя]|слоган|девиз|написать письмо|письмо для|copywrit|proofread|translation|article|blog post|rewrite|product description|greeting message|poem|slogan|email copy|seo content|social media post|ad copy|landing page copy|sales copy|newsletter|content plan|product listing",re.I)),
("documents",re.compile(r"документ|word|docx|pdf|презентац|pptx|слайд|конспект|сводк|отч[её]т|резюме|summary|report writing|оформить файл",re.I)),
("data",re.compile(r"excel|google sheets|таблиц|обработк.{0,18}данн|ввод данных|сбор данных|csv|формул[ыа]|data entry|spreadsheet|data cleaning|data analysis",re.I)),
("code",re.compile(r"python|javascript|typescript|html|css|php|sql|программир|написать код|исправить код|скрипт|разработк.{0,20}(?:сайт|лендинг|бот)|создать сайт|сделать сайт|лендинг|бота|api|интеграц|автоматизац|парсер|wordpress|верстк",re.I)),
("research",re.compile(r"исследован|подбор.{0,18}информац|поиск.{0,18}информац|сравнен.{0,20}(?:товар|сервис|конкурент)|анализ данных|сбор информации|market research|research assistant|competitor research",re.I)),
("image",re.compile(r"логотип|баннер|обложк|иллюстрац|иконк|инфографик|карточк.{0,20}(?:товар|маркетплейс)|изображен|картинк|thumbnail|illustration|logo design|banner design",re.I))]
OUT=re.compile(r"трафик.{0,30}конверси|конверс.{0,25}(?:увелич|гарантир|рост)|контекстн.{0,12}реклам|таргетированн.{0,12}реклам|привлечени[ея] клиентов|поиск потенциальных клиентов|находить потенциальных клиентов|найти потенциальных клиентов|писать клиентам|пишите клиентам|отправлять клиентам|рассылк|обзвон|телефонн.{0,12}звон|накрут.{0,20}отзыв|оставить отзыв|фейков.{0,15}отзыв|курьер|приехать|выезд|в офисе|офлайн|монтаж видео|видеомонтаж|озвучк|диктор|запись голоса|3d модел|autocad|solidworks|revit|archicad|\\bdxf\\b|\\bdwg\\b|\\bcad\\b|чертеж|чертёж|лекал|плоттер|раскрой|архитектурн.{0,20}(?:проект|план|чертеж|чертёж)|проектировани.{0,20}(?:здани|дом|сооружени)|в figma|figma.{0,35}исходник|исходник.{0,35}(?:figma|photoshop|illustrator|coreldraw)|медицинск.{0,20}(?:диагноз|заключен|консультац)|юридическ.{0,20}консультац|вести бухгалтерию|создать аккаунт|зарегистрировать аккаунт|обойти защиту|код из sms|пароль от банка|перевести чужие деньги|принимать чужие деньги|обналич|\bcm\s*y?\s*k\b|cmyk|для широкоформатной печати|для печати на баннере|4\s*[xх×]\s*3\s*метр|печат.{0,20}(?:баннер|плакат|наружн)|полный ребрендинг|ребрендинг.{0,30}сайт|модернизац.{0,30}сайт|сохранени.{0,20}(?:позици|seo)|seo.{0,50}(?:позици|аудит|продвиж)|фирменн.{0,20}стил|бренд.?гайд|brand.?book|brand.?guide|brand identity|дизайн.?систем.{0,20}(?:бренд|бренда)|перенос карты.{0,40}gta|gta san andreas|crmp.{0,30}(?:карт|сборк)|плагин.{0,20}конфликт|сохранить все текущие системы",re.I)
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
  if m and u.scheme in {"https","http"} and (host=="kwork.ru" or host.endswith(".kwork.ru")) and title and title.lower() not in {"подробнее","показать полностью"}:out.append((m.group(1),"https://kwork.ru/projects/"+m.group(1)+"/view",a,title[:200]))
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
 title_text=title.lower()
 whole=title+"\n"+t
 if OUT.search(whole):return None,"excluded_by_out_of_scope_filter",""
 # Prefer intent visible in the title. Description-only matches are a fallback
 # only for titles that do not identify a work type.
 title_matches=[name for name,pat in ABILITY if pat.search(title_text)]
 found=title_matches or [name for name,pat in ABILITY if pat.search(t)]
 if not found:return None,"no_supported_deliverable_match",""
 # Long custom website development is not an honest recommendation unless the
 # actual deliverable can be generated without platform/account access.
 c=found[0]
 reason={"text":"Могу подготовить текстовый результат по ТЗ и проверить соответствие требованиям.","documents":"Могу оформить документ, отчёт или презентацию в готовый файл.","data":"Могу обработать данные и подготовить таблицу/CSV/XLSX с проверкой структуры и формул.","code":"Могу написать или исправить код и подготовить файлы проекта; публикация в чужом аккаунте потребует отдельного доступа.","research":"Могу исследовать доступные источники и оформить выводы со ссылками.","image":"Могу создать оригинальное изображение; редактируемый исходник в сторонней программе не обещается."}[c]
 deliver={"text":"готовый текст","documents":"DOCX/PDF/PPTX","data":"таблица или CSV/XLSX","code":"исходные файлы кода и инструкция","research":"отчёт со ссылками","image":"растровое изображение"}[c]
 return c,reason,deliver

def extract_state_data(page):
 m=re.search(r"window\.stateData\s*=\s*",page,re.I)
 if not m:return None
 raw=page[m.end():]; depth=0; quoted=False; escaped=False
 for i,ch in enumerate(raw):
  if quoted:
   if escaped:escaped=False
   elif ch=="\\":escaped=True
   elif ch=='"':quoted=False
   continue
  if ch=='"':quoted=True
  elif ch=="{":depth+=1
  elif ch=="}":
   depth-=1
   if depth==0:
    try:
     obj=json.loads(raw[:i+1])
     return obj if isinstance(obj,dict) else None
    except json.JSONDecodeError:return None
 return None

def normalize_date(value):
 if value is None or value=="":return ""
 if isinstance(value,(int,float)):
  try:
   seconds=float(value)
   if seconds>100_000_000_000:seconds/=1000
   return dt.datetime.fromtimestamp(seconds,tz=dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
  except (OverflowError,OSError,ValueError):return ""
 text=str(value).strip()
 try:
  parsed=dt.datetime.fromisoformat(text.replace("Z","+00:00"))
  if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=dt.timezone.utc)
  return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
 except (TypeError,ValueError):return ""

def structured_projects(page):
 state=extract_state_data(page)
 if not state:return []
 data=state.get("wantsListData") or {}
 wants=data.get("wants") or []
 if not isinstance(wants,list):return []
 records=[]
 for want in wants:
  if not isinstance(want,dict) or not want.get("id"):continue
  pid=str(want.get("id")).strip()
  if not pid.isdigit():continue
  title=clean_text(str(want.get("name") or want.get("title") or "")).replace("\n"," ").strip()
  if not title:continue
  description=clean_text(str(want.get("description") or want.get("body") or ""))[:4000]
  dates=want.get("wantDates") if isinstance(want.get("wantDates"),dict) else {}
  published=normalize_date(dates.get("dateCreate") or want.get("createdAt") or want.get("dateCreate"))
  price=want.get("priceLimit")
  try:price_num=max(0,int(float(price or 0)))
  except (TypeError,ValueError,OverflowError):price_num=0
  budget_text=("до "+format(price_num,",").replace(",", " ")+" ₽") if price_num else ""
  proposals=None
  for key in ("responsesCount","offersCount","proposalCount","responses","countResponses"):
   val=want.get(key)
   try:
    if val is not None:proposals=int(val);break
   except (TypeError,ValueError):pass
  buyer=str(want.get("username") or want.get("userName") or want.get("buyerName") or "")
  closed=bool(want.get("isClosed") or want.get("isArchived") or want.get("closedAt"))
  records.append({
   "pid":pid,"link":"https://kwork.ru/projects/"+pid+"/view","title":title,
   "text":title+"\n"+description,"description":description,"publishedAt":published,
   "budgetMin":0,"budgetMax":price_num,"budgetText":budget_text,
   "proposalCount":proposals,"buyer":buyer,"closed":closed,
   "expiryAt":normalize_date(want.get("expiresAt") or dates.get("dateFinish")),
   "deadlineHint":""
  })
 return records

def parse_kwork_html(page,checked_at=None,previous=None):
 stamp=checked_at or now_iso()
 now=dt.datetime.fromisoformat(stamp.replace("Z","+00:00"))
 structured=structured_projects(page)
 raw_records=list(structured)
 if not raw_records:
  parser=Parser();parser.feed(page);parser.close()
  anchors=links(parser)
  for pid,url,a,title in anchors:
   txt=card(a,title);published=relative_date(txt,now);label,expires=expiry(txt,now);low,high,btxt=get_budget(txt)
   prop=re.search(r"предложений\s*:\s*(\d+)",txt,re.I);buyer=re.search(r"покупатель\s*:\s*([^\n·]{1,80})",txt,re.I)
   raw_records.append({"pid":pid,"link":url,"title":title,"text":txt,
    "description":clean_text(re.sub(re.escape(title)," ",txt,count=1,flags=re.I))[:1200],
    "publishedAt":published,"budgetMin":low,"budgetMax":high,"budgetText":btxt,
    "proposalCount":int(prop.group(1)) if prop else None,"buyer":buyer.group(1).strip() if buyer else "",
    "closed":bool(re.search(r"ист[её]к|заверш[её]н|закрыт",txt,re.I)),"expiryAt":expires,"deadlineHint":label})
 if not raw_records:
  page_title=re.search(r"<title[^>]*>(.*?)</title>",page,re.I|re.S)
  title_text=clean_text(page_title.group(1))[:100] if page_title else "(no title)"
  hrefs=re.findall(r"""href\s*=\s*["']([^"']+)["']""",page,re.I)
  project_paths=[urlsplit(h).path for h in hrefs if "project" in h.lower() or "want" in h.lower()]
  raise ValueError("Kwork public page has no parseable project data; bytes="+str(len(page))+
   "; title="+repr(title_text)+"; stateData="+str("window.stateData" in page)+
   "; pathMentions="+str(page.lower().count("/projects/"))+"; hrefSamples="+repr(project_paths[:8]))
 prior={str(i.get("link","")).rstrip("/"):i for i in (previous or {}).get("items",[]) if isinstance(i,dict)}
 items={};skip=0;risks_n=0;closed_n=0
 excluded_reasons={};excluded_examples=[]
 def note_exclusion(reason,row):
  excluded_reasons[reason]=excluded_reasons.get(reason,0)+1
  if len(excluded_examples)<20:
   excluded_examples.append({"id":str(row.get("pid") or "")[:32],"title":str(row.get("title") or "")[:200],"reason":reason})
 for row in raw_records:
  pid=row["pid"]
  if pid in items:continue
  title=row["title"]; txt=row["text"]; description=row["description"]
  category,reason,deliverable=classify(title,txt)
  if not category:
   skip+=1;note_exclusion(reason or "no_supported_deliverable_match",row);continue
  published=row["publishedAt"]
  low=row["budgetMin"];high=row["budgetMax"];budget_text=row["budgetText"]
  _,_,riskflags,_,critical=score_item(title,txt,published,high)
  if critical or RISK.search(title+"\n"+txt):
   risks_n+=1;note_exclusion("critical_or_high_risk",row);continue
  if row["closed"]:
   closed_n+=1;note_exclusion("closed_listing",row);continue
  titlepat=next(pat for name,pat in ABILITY if name==category)
  score=72 if titlepat.search(title) else 64
  if high:score+=7
  if published:
   try:
    age=(now-dt.datetime.fromisoformat(published.replace("Z","+00:00"))).total_seconds()/3600
    if 0<=age<=24:score+=10
    elif 0<=age<=72:score+=5
   except (TypeError,ValueError):pass
  if riskflags:score-=min(15,5*len(riskflags))
  if re.search(r"срочно|сегодня|за час|максимально быстро",title+"\n"+txt,re.I):score-=4
  link=row["link"];old=prior.get(link.rstrip("/"),{})
  score=max(50,min(98,score))
  items[pid]={"id":"kwork-"+pid,"kworkProjectId":pid,"title":title,"link":link,"description":description,
   "publishedAt":published,"firstSeenAt":old.get("firstSeenAt") or old.get("discoveredAt") or stamp,
   "expiryAt":row["expiryAt"],"deadlineHint":row["deadlineHint"],"source":"Kwork",
   "feedName":"Kwork public projects page","score":score,
   "fitReasons":["Основная задача входит в доступные форматы выполнения",reason],
   "riskFlags":riskflags,"criticalRisk":False,"hasRelevantKeywords":True,
   "recommendation":"recommend","recommendationReason":reason,"executionCategory":category,"deliverable":deliverable,
   "budgetMin":low,"budgetMax":high,"budgetText":budget_text,"currency":"RUB" if budget_text else "",
   "proposalCount":row["proposalCount"],"companyName":row["buyer"],
   "locationText":"Проект размещён на Kwork; проверь доступность условий и выплаты в своём аккаунте.",
   "paymentCheck":"Оплата — через Kwork. Не плати за регистрацию или разблокировку выплаты."}
 out=sorted(items.values(),key=lambda x:(x["score"],x.get("publishedAt") or "",-int(x.get("proposalCount") or 999)),reverse=True)
 return {"schemaVersion":1,"source":"Kwork public projects page","checkedAt":stamp,
  "maxAgeNotice":"Обновляется каждые 30 минут по публичной первой странице Kwork /projects. Если дата публикации отсутствует, показывается время первого обнаружения. Рекомендация означает, что результат можно подготовить доступными инструментами ChatGPT; это не гарантия найма или оплаты.",
  "attribution":"Источник: публичная страница проектов Kwork (https://kwork.ru/projects). Открой оригинал перед откликом.",
  "feeds":[{"name":"Kwork public projects page","url":URL,"ok":True,"items":len(raw_records),"source":"Kwork"}],
  "stats":{"sourceListingsFound":len(raw_records),"fetchedUnique":len(raw_records),"freshCandidates":len(out),"matchedCandidates":len(out),
   "excludedNotExecutable":skip,"excludedCriticalRisk":risks_n,"excludedClosed":closed_n,
   "excludedReasons":excluded_reasons,"carriedFromPrevious":0,"feedErrors":[]},"items":out,
  "diagnostics":{"excludedExamples":excluded_examples,"excludedReasons":excluded_reasons}}

def page_listing_ids(page):
 rows=structured_projects(page)
 if rows:return {str(row["pid"]) for row in rows}
 parser=Parser();parser.feed(page);parser.close()
 return {pid for pid,_,_,_ in links(parser)}

def parse_kwork_pages(pages,checked_at=None,previous=None):
 if not pages:raise ValueError("No Kwork pages were fetched.")
 stamp=checked_at or now_iso()
 all_listing_ids=set();listing_count=0;items_by_id={}
 excluded_reasons={};excluded_examples=[]
 totals={"excludedNotExecutable":0,"excludedCriticalRisk":0,"excludedClosed":0}
 for number,page in enumerate(pages,1):
  page_ids=page_listing_ids(page)
  if not page_ids:raise ValueError("Kwork page "+str(number)+" has no parseable public project listings.")
  listing_count+=len(page_ids);all_listing_ids.update(page_ids)
  payload=parse_kwork_html(page,checked_at=stamp,previous=previous)
  for item in payload["items"]:
   key=str(item.get("kworkProjectId") or item.get("id") or "")
   if key:items_by_id.setdefault(key,item)
  stats=payload["stats"]
  for key in totals:totals[key]+=int(stats.get(key) or 0)
  for reason,count in (stats.get("excludedReasons") or {}).items():
   excluded_reasons[reason]=excluded_reasons.get(reason,0)+int(count or 0)
  excluded_examples.extend(payload.get("diagnostics",{}).get("excludedExamples",[]))
 items=sorted(items_by_id.values(),key=lambda x:(x.get("score",0),x.get("publishedAt") or "",-int(x.get("proposalCount") or 999)),reverse=True)
 return {"schemaVersion":1,"source":"Kwork public projects page","checkedAt":stamp,
  "maxAgeNotice":"Обновляется каждые 30 минут по первым "+str(len(pages))+" публичным страницам общего списка Kwork /projects. Это не полный каталог. Если дата публикации отсутствует, показывается время первого обнаружения. Рекомендация означает, что результат можно подготовить доступными инструментами ChatGPT; это не гарантия найма или оплаты.",
  "attribution":"Источник: публичные страницы проектов Kwork (https://kwork.ru/projects). Открой оригинал перед откликом.",
  "feeds":[{"name":"Kwork public projects pages","url":URL,"ok":True,"items":len(all_listing_ids),"pages":len(pages),"source":"Kwork"}],
  "stats":{"sourceListingsFound":listing_count,"fetchedUnique":len(all_listing_ids),"freshCandidates":len(items),"matchedCandidates":len(items),
   **totals,"excludedReasons":excluded_reasons,"carriedFromPrevious":0,"feedErrors":[]},
  "items":items,"diagnostics":{"excludedExamples":excluded_examples[:20],"excludedReasons":excluded_reasons}}

def fetch_page(page=1):
 raise RuntimeError("Automated Kwork collection is disabled: Kwork's published terms prohibit automated robots/software from collecting listing data. Open Kwork manually, choose one project, then paste its brief into Freelance Pult or ChatGPT.")

def main():
 print("Kwork automatic collection is disabled to comply with Kwork's published terms. The collector will not log in, scrape, apply, message clients, or submit deliverables.",file=sys.stderr)
 print("Compliant workflow: open Kwork manually, review a specific project, and provide its requirements in Freelance Pult/ChatGPT for preparation and two-pass quality review.",file=sys.stderr)
 return 2
if __name__=="__main__":raise SystemExit(main())
