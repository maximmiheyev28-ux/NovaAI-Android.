#!/usr/bin/env python3
"""Collect public FL.ru RSS projects and publish a small, ranked JSON feed.

Only reads RSS URLs exposed through FL.ru's public "Subscribe via RSS" links.
Does not log in, bypass access controls, submit proposals, or contact customers.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

FEEDS = [
    ("Все проекты FL.ru", "https://www.fl.ru/rss/all.xml"),
    ("Программирование", "https://www.fl.ru/rss/all.xml?category=5"),
    ("Продвижение сайтов", "https://www.fl.ru/rss/all.xml?category=6"),
]
USER_AGENT = "FreelancePult-RSSReader/1.0 (public RSS subscription; contact via repository issue)"
MAX_ITEMS = 120
MAX_DESCRIPTION = 1200

POSITIVE_RE = re.compile(
    r"текст|копирайт|стать[яи]|рерайт|описан|карточк|маркетплейс|товар|"
    r"таблиц|excel|google sheets|документ|презентац|контент|перевод|"
    r"расшифров|редакт|коррект|ввод данных|заполнение данных|ассистент|"
    r"помощник|виртуальн.{0,10}помощник|администратор|контент-менедж|"
    r"постинг|набор текста|оформлен|каталог|верстк|html|лендинг|"
    r"прост.{0,12}сайт|обработк.{0,12}данн",
    re.I,
)
MOBILE_RE = re.compile(
    r"текст|копирайт|стать|описан|карточк|таблиц|excel|документ|"
    r"презентац|контент|перевод|расшифров|редакт|коррект|ввод данных|"
    r"заполнение данных|ассистент|помощник|постинг|набор текста|"
    r"каталог|проверка текст",
    re.I,
)
CRITICAL_RISKS = [
    (re.compile(r"(?:оплат(?:ите|ить|а)\s+(?:доступ|регистрац|активац)|плата\s+за\s+отклик|активац.{0,25}вывод|комисси.{0,25}для\s+вывода|внесите\s+(?:залог|оплату))", re.I),
     "Требуют оплатить доступ, регистрацию или разблокировку выплаты."),
    (re.compile(r"\b(?:cvv|cvc|пин-код|код\s+из\s+sms|sms-код|пароль\s+от\s+банка|секретный\s+код)\b", re.I),
     "Запрашивают банковские секреты или код подтверждения."),
    (re.compile(r"(?:получ(?:ите|ить)\s+деньги.{0,50}перевест|перевед(?:ите|и)\s+деньги.{0,50}(?:дальше|треть|на\s+карту)|обналич(?:ить|ивание)|дроппер)", re.I),
     "Похоже на просьбу принимать или пересылать чужие деньги."),
    (re.compile(r"(?:установ(?:ите|ить).{0,30}\bapk\b|скачайте.{0,25}\bapk\b|удалённ.{0,15}(?:доступ|управлен)|anydesk|teamviewer)", re.I),
     "Просят установить APK или предоставить удалённый доступ."),
]
SOFT_RISKS = [
    (re.compile(r"бесплатн.{0,25}тест|тестов.{0,25}задан.{0,25}бесплат", re.I),
     "Есть риск объёмного бесплатного тестового задания."),
    (re.compile(r"гарантированн.{0,20}(?:доход|заработок)|доход\s+без\s+усилий|без\s+вложений.{0,30}доход", re.I),
     "Обещают гарантированный заработок без чётких условий."),
    (re.compile(r"перейти\s+в\s+(?:telegram|телеграм|whatsapp)|напишите\s+в\s+(?:telegram|телеграм|whatsapp)", re.I),
     "Предлагают перейти во внешний мессенджер: проверяй правила и не раскрывай личные данные."),
]
MONEY_RE = re.compile(
    r"(?<!\w)(\d[\d\s\u00a0]{0,12})(?:\s*(?:[-–—]|до|—)\s*(\d[\d\s\u00a0]{0,12}))?\s*(₽|руб(?:\.|лей|ля|ль)?)(?!\w)",
    re.I,
)
DATE_RE = re.compile(
    r"(?:срок(?:и)?(?:\s+выполнения)?|в\s+течение|за)\s*[:—-]?\s*"
    r"(\d+\s*(?:рабочих\s*)?(?:дн(?:я|ей)?|час(?:а|ов)?|недел(?:ю|и|ь)?))",
    re.I,
)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(node: ET.Element, key: str) -> str:
    for child in list(node):
        if local_name(child.tag) == key.lower():
            return "".join(child.itertext()).strip()
    return ""


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<br\s*/?>|</p\s*>|</li\s*>", "\n", value)
    value = re.sub(r"<[^>]*>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[\t\r\f\v ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def parse_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError):
            return ""


def parse_money(text: str) -> tuple[int, int, str]:
    match = MONEY_RE.search(text or "")
    if not match:
        return 0, 0, ""
    def number(value: str | None) -> int:
        return int(re.sub(r"[\s\u00a0]", "", value or "0") or "0")
    low = number(match.group(1))
    high = number(match.group(2)) if match.group(2) else low
    # Never invent a numeric amount when the feed only says "по договорённости".
    low, high = max(0, low), max(0, high)
    snippet = match.group(0).strip()
    return low, max(low, high), snippet


def score_item(title: str, description: str, published_at: str, price_min: int, price_max: int) -> tuple[int, list[str], list[str], bool]:
    text = (title + "\n" + description).lower()
    reasons: list[str] = []
    risks: list[str] = []
    for pattern, message in CRITICAL_RISKS:
        if pattern.search(text):
            risks.append(message)
    for pattern, message in SOFT_RISKS:
        if pattern.search(text):
            risks.append(message)
    match = bool(POSITIVE_RE.search(text))
    score = 22
    if match:
        score += 34
        reasons.append("Похоже на цифровую задачу из выбранных направлений")
    if MOBILE_RE.search(text):
        score += 12
        reasons.append("Возможно выполнить преимущественно с телефона")
    if price_max:
        score += 7
        reasons.append("В RSS указан денежный бюджет")
    if published_at:
        try:
            age_hours = max(0.0, (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(published_at.replace("Z", "+00:00"))).total_seconds() / 3600)
            if age_hours <= 24:
                score += 18
                reasons.append("Опубликовано в течение последних 24 часов")
            elif age_hours <= 72:
                score += 11
                reasons.append("Опубликовано в течение последних 3 дней")
            elif age_hours <= 168:
                score += 4
                reasons.append("Опубликовано в течение последней недели")
        except (TypeError, ValueError):
            pass
    if risks:
        score -= 18 * len([r for r in risks if r in [message for _, message in CRITICAL_RISKS]])
        score -= 7 * (len(risks) - len([r for r in risks if r in [message for _, message in CRITICAL_RISKS]]))
    score = max(0, min(100, score))
    return score, reasons, risks, match


def parse_feed(feed_name: str, url: str) -> list[dict]:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise RuntimeError(f"HTTP {status}")
        body = response.read(4_000_000)
    root = ET.fromstring(body)
    nodes = [node for node in root.iter() if local_name(node.tag) in ("item", "entry")]
    records: list[dict] = []
    for node in nodes:
        title = clean_text(child_text(node, "title"))
        link = child_text(node, "link").strip()
        if not link:
            for child in list(node):
                if local_name(child.tag) == "link":
                    link = child.attrib.get("href", "").strip()
                    if link:
                        break
        if not title or not link:
            continue
        parsed_url = urlparse(link)
        if parsed_url.scheme not in ("http", "https") or not (parsed_url.hostname or "").lower().endswith("fl.ru"):
            continue
        description = clean_text(child_text(node, "description") or child_text(node, "summary") or child_text(node, "content"))
        pub = child_text(node, "pubDate") or child_text(node, "published") or child_text(node, "updated") or child_text(node, "date")
        published_at = parse_date(pub)
        price_min, price_max, budget_text = parse_money(title + "\n" + description)
        date_match = DATE_RE.search(title + "\n" + description)
        score, reasons, risks, matched = score_item(title, description, published_at, price_min, price_max)
        critical = any(message for _, message in CRITICAL_RISKS if message in risks)
        digest = hashlib.sha256(link.split("#")[0].encode("utf-8")).hexdigest()[:16]
        records.append({
            "id": digest,
            "title": title[:180],
            "link": link,
            "description": description[:MAX_DESCRIPTION],
            "publishedAt": published_at,
            "publishedRaw": pub[:120],
            "source": "FL.ru RSS",
            "feedName": feed_name,
            "score": score,
            "fitReasons": reasons,
            "riskFlags": risks,
            "criticalRisk": critical,
            "hasRelevantKeywords": matched,
            "budgetMin": price_min,
            "budgetMax": price_max,
            "budgetText": budget_text,
            "deadlineHint": date_match.group(1) if date_match else "",
        })
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="freelance-pult/jobs.json")
    args = parser.parse_args()
    checked_at = now_iso()
    all_items: list[dict] = []
    feed_status: list[dict] = []
    errors: list[str] = []
    successes = 0
    for feed_name, feed_url in FEEDS:
        try:
            rows = parse_feed(feed_name, feed_url)
            all_items.extend(rows)
            feed_status.append({"name": feed_name, "url": feed_url, "ok": True, "items": len(rows)})
            successes += 1
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
            message = f"{feed_name}: {type(exc).__name__}: {str(exc)[:180]}"
            errors.append(message)
            feed_status.append({"name": feed_name, "url": feed_url, "ok": False, "error": message})
            time.sleep(1)
    if successes == 0:
        print("All permitted FL.ru RSS feeds failed; existing jobs.json left untouched.", file=sys.stderr)
        return 2

    dedup: dict[str, dict] = {}
    for item in all_items:
        key = item["link"].split("#")[0].rstrip("/")
        if key not in dedup or item["score"] > dedup[key]["score"]:
            dedup[key] = item

    all_unique = list(dedup.values())
    critical_count = sum(1 for item in all_unique if item["criticalRisk"])
    irrelevant_count = sum(1 for item in all_unique if not item["hasRelevantKeywords"])
    candidates = [
        item for item in all_unique
        if item["hasRelevantKeywords"] and not item["criticalRisk"] and item["score"] >= 40
    ]
    candidates.sort(key=lambda item: (
        item["score"],
        item["publishedAt"] or "",
    ), reverse=True)
    candidates = candidates[:MAX_ITEMS]

    payload = {
        "schemaVersion": 1,
        "source": "Публичная RSS-лента FL.ru",
        "checkedAt": checked_at,
        "maxAgeNotice": "RSS не подтверждает, что заказ всё ещё открыт; проверь объявление перед откликом.",
        "feeds": feed_status,
        "stats": {
            "fetchedUnique": len(all_unique),
            "matchedCandidates": len(candidates),
            "excludedCriticalRisk": critical_count,
            "excludedNoKeywordMatch": irrelevant_count,
            "feedErrors": errors,
        },
        "items": candidates,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["stats"], ensure_ascii=False))
    print(f"Wrote {len(candidates)} ranked candidates to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
