#!/usr/bin/env python3
"""Collect public job feeds through documented, public endpoints.

Himalayas is queried through its free public jobs API, which permits dashboard
use with source attribution. FL.ru's public RSS is also attempted because the
site exposes RSS subscriptions. No logins, anti-bot workarounds, scraping of
closed pages, proposal submissions, or financial actions are performed.
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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

FL_FEEDS = [
    ("FL.ru · все проекты", "https://www.fl.ru/rss/all.xml"),
    ("FL.ru · программирование", "https://www.fl.ru/rss/all.xml?category=5"),
    ("FL.ru · продвижение сайтов", "https://www.fl.ru/rss/all.xml?category=6"),
]
HIMALAYAS_SEARCHES = [
    ("Himalayas · свежие вакансии · пакет 1", {"limit": "20", "offset": "0"}),
    ("Himalayas · свежие вакансии · пакет 2", {"limit": "20", "offset": "20"}),
    ("Himalayas · свежие вакансии · пакет 3", {"limit": "20", "offset": "40"}),
    ("Himalayas · свежие вакансии · пакет 4", {"limit": "20", "offset": "60"}),
    ("Himalayas · свежие вакансии · пакет 5", {"limit": "20", "offset": "80"}),
    ("Himalayas · свежие вакансии · пакет 6", {"limit": "20", "offset": "100"}),
    ("Himalayas · свежие вакансии · пакет 7", {"limit": "20", "offset": "120"}),
    ("Himalayas · свежие вакансии · пакет 8", {"limit": "20", "offset": "140"}),
    ("Himalayas · свежие вакансии · пакет 9", {"limit": "20", "offset": "160"}),
    ("Himalayas · свежие вакансии · пакет 10", {"limit": "20", "offset": "180"}),
]
HIMALAYAS_API = "https://himalayas.app/jobs/api"
USER_AGENT = "FreelancePult/1.0 (+https://github.com/maximmiheyev28-ux/NovaAI-Android.)"
MAX_ITEMS = 100
MAX_DESCRIPTION = 1200

POSITIVE_RE = re.compile(
    r"текст|копирайт|стать[яи]|рерайт|описан|карточк|маркетплейс|товар|"
    r"таблиц|excel|google sheets|документ|презентац|контент|перевод|"
    r"расшифров|редакт|коррект|ввод данных|заполнение данных|ассистент|"
    r"помощник|виртуальн.{0,10}помощник|администратор|контент-менедж|"
    r"постинг|набор текста|оформлен|каталог|верстк|html|лендинг|"
    r"прост.{0,12}сайт|обработк.{0,12}данн|content writer|copywriter|"
    r"copywriting|data entry|virtual assistant|translation|translator|"
    r"transcription|proofread|copy edit|product listing|spreadsheet|"
    r"data annotat|social media|community manager|content moderation|"
    r"localization|localisation|editor|writing|blog post|article writer|"
    r"communications professional|communications|customer support|customer service|"
    r"user research|usability testing|game testing|screen recording|record video|"
    r"data labeling|data annotation|lead generation|research assistant|"
    r"собрать базу|сбор базы|сбор данных|видеозапис|играть в браузерную игру",
    re.I,
)
MOBILE_RE = re.compile(
    r"текст|копирайт|стать|описан|карточк|таблиц|excel|документ|"
    r"презентац|контент|перевод|расшифров|редакт|коррект|ввод данных|"
    r"заполнение данных|ассистент|помощник|постинг|набор текста|"
    r"каталог|проверка текст|content writer|copywriter|copywriting|"
    r"data entry|virtual assistant|translation|translator|transcription|"
    r"proofread|copy edit|product listing|spreadsheet|content moderation|"
    r"localization|localisation|editor|writing|blog post|article writer|"
    r"communications professional|communications|customer support|customer service|"
    r"user research|usability testing|game testing|screen recording|record video|"
    r"data labeling|data annotation|lead generation|research assistant|"
    r"собрать базу|сбор базы|сбор данных|видеозапис|играть в браузерную игру",
    re.I,
)
MOBILE_TITLE_RE = re.compile(
    r"контент|копирайт|стать|описан|карточк|таблиц|excel|документ|презентац|"
    r"перевод|расшифров|редакт|коррект|ввод данных|заполнение данных|помощник|"
    r"ассистент|виртуальн.{0,12}помощник|постинг|набор текста|редактор|корректор|"
    r"отч[её]т|собрать базу|сбор базы|сбор данных|опубликовать готовую статью|"
    r"communications professional|communications|customer support|customer service|"
    r"virtual assistant|data entry|translation|translator|transcription|copywriter|"
    r"content writer|proofread|copy edit|spreadsheet|product listing|user research|"
    r"usability testing|game testing|screen recording|record video|research assistant|"
    r"data labeling|data annotation|lead generation|social media|community manager|"
    r"survey|play.{0,25}game|играть в браузерную игру|видеозапис",
    re.I,
)
TECH_HEAVY_TITLE_RE = re.compile(
    r"senior|full.?stack|engineer|developer|разработчик|программир|рефакторинг|"
    r"php|python|java|c\+\+|qt|modbus|linux|opencart|wordpress|"
    r"верстк|дизайн.{0,12}(?:сайт|лендинг|макет)|лендинг|корпоративный сайт|"
    r"веб.?платформ|ии.?агент|ai.?agent|seo.?аудит|seo.?специалист|"
    r"настроить.{0,20}(?:crm|телефони|интеграц)|автоматизац.{0,20}(?:процесс|ии)",
    re.I,
)
CRITICAL_RISKS = [
    (re.compile(r"(?:оплат(?:ите|ить|а)\s+(?:доступ|регистрац|активац)|активац.{0,25}вывод|комисси.{0,25}для\s+вывода|внесите\s+(?:залог|оплату)|pay\s+(?:a\s+)?(?:fee|deposit|registration)|upfront\s+payment|activation\s+fee|unlock\s+(?:your\s+)?payment|withdrawal\s+fee)", re.I),
     "Требуют оплатить доступ, регистрацию или разблокировку выплаты."),
    (re.compile(r"\b(?:cvv|cvc|пин-код|код\s+из\s+sms|sms-код|пароль\s+от\s+банка|секретный\s+код|bank\s+password|one.time\s+code|sms\s+code)\b", re.I),
     "Запрашивают банковские секреты или код подтверждения."),
    (re.compile(r"(?:получ(?:ите|ить)\s+деньги.{0,50}перевест|перевед(?:ите|и)\s+деньги.{0,50}(?:дальше|треть|на\s+карту)|обналич(?:ить|ивание)|дроппер|receive\s+money.{0,60}transfer|transfer\s+funds\s+for\s+us|money\s+mule)", re.I),
     "Похоже на просьбу принимать или пересылать чужие деньги."),
    (re.compile(r"(?:установ(?:ите|ить).{0,30}\bapk\b|скачайте.{0,25}\bapk\b|удалённ.{0,15}(?:доступ|управлен)|anydesk|teamviewer|install.{0,30}(?:apk|remote.access)|remote\s+access\s+software)", re.I),
     "Просят установить APK или предоставить удалённый доступ."),
]
SOFT_RISKS = [
    (re.compile(r"оставить отзыв|отзыв.{0,15}(?:2gis|картах|карты)|write a review|post a review", re.I),
     "Если задача требует отзыв, он должен отражать реальный личный опыт; не размещай выдуманные отзывы."),
    (re.compile(r"плат(?:а|н(?:ый|ая|ого|ые)?).{0,20}отклик|покупк.{0,20}отклик|оплат.{0,20}отклик", re.I),
     "Проверь, является ли платный отклик официальной функцией площадки; не переводи деньги напрямую заказчику."),
    (re.compile(r"бесплатн.{0,25}тест|тестов.{0,25}задан.{0,25}бесплат|unpaid\s+test|free\s+test\s+task", re.I),
     "Есть риск объёмного бесплатного тестового задания."),
    (re.compile(r"гарантированн.{0,20}(?:доход|заработок)|доход\s+без\s+усилий|без\s+вложений.{0,30}доход|guaranteed\s+(?:income|earnings)|easy\s+money", re.I),
     "Обещают гарантированный заработок без чётких условий."),
    (re.compile(r"перейти\s+в\s+(?:telegram|телеграм|whatsapp)|напишите\s+в\s+(?:telegram|телеграм|whatsapp)|move\s+to\s+(?:telegram|whatsapp)", re.I),
     "Предлагают перейти во внешний мессенджер: проверяй правила и не раскрывай личные данные."),
]
MONEY_RE = re.compile(
    r"(?<!\w)(\d[\d\s\u00a0]{0,12})(?:\s*(?:[-–—]|до)\s*(\d[\d\s\u00a0]{0,12}))?\s*(₽|руб(?:\.|лей|ля|ль)?)(?!\w)",
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


def from_timestamp(value: Any) -> str:
    if isinstance(value, (int, float)) and value > 0:
        try:
            seconds = value / 1000 if value > 100_000_000_000 else value
            return dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return ""
    return parse_date(str(value or ""))


def parse_money(text: str) -> tuple[int, int, str]:
    match = MONEY_RE.search(text or "")
    if not match:
        return 0, 0, ""
    def number(value: str | None) -> int:
        return int(re.sub(r"[\s\u00a0]", "", value or "0") or "0")
    low = number(match.group(1))
    high = number(match.group(2)) if match.group(2) else low
    low, high = max(0, low), max(0, high)
    return low, max(low, high), match.group(0).strip()


def score_item(title: str, description: str, published_at: str, price_max: int) -> tuple[int, list[str], list[str], bool, bool]:
    text = (title + "\n" + description).lower()
    title_text = title.lower()
    description_text = description.lower()
    reasons: list[str] = []
    risks: list[str] = []
    critical = False
    for pattern, message in CRITICAL_RISKS:
        if pattern.search(text):
            risks.append(message)
            critical = True
    for pattern, message in SOFT_RISKS:
        if pattern.search(text):
            risks.append(message)

    title_match = bool(POSITIVE_RE.search(title_text))
    body_match = bool(POSITIVE_RE.search(description_text))
    matched = title_match or body_match
    mobile_title = bool(MOBILE_TITLE_RE.search(title_text))
    mobile_body = bool(MOBILE_RE.search(description_text))
    score = 18

    if title_match:
        score += 34
        reasons.append("Название совпадает с целевыми типами цифровых задач")
    elif body_match:
        score += 10
        reasons.append("Ключевые слова найдены только в описании; уточни реальную суть работы")

    if mobile_title:
        score += 26
        reasons.append("Название указывает на задачу, которую обычно можно выполнять с телефона")
    elif mobile_body:
        score += 5
        reasons.append("Телефонный сценарий не подтверждён названием, только отдельными словами описания")

    if price_max:
        score += 7
        reasons.append("В источнике указан бюджет или диапазон оплаты")

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

    if not mobile_title:
        score -= 10
    if TECH_HEAVY_TITLE_RE.search(title_text) and not mobile_title:
        score -= 24
        reasons.append("Вероятно требуются специальные навыки или работа за компьютером; оцени свои возможности")
    if risks:
        critical_count = sum(1 for _, message in CRITICAL_RISKS if message in risks)
        score -= 18 * critical_count + 7 * (len(risks) - critical_count)
    return max(0, min(100, score)), reasons, risks, matched, critical


def read_url(url: str, accept: str = "application/json") -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": accept,
    })
    with urllib.request.urlopen(request, timeout=25) as response:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise RuntimeError(f"HTTP {status}")
        return response.read(5_000_000)


def parse_fl_feed(feed_name: str, url: str) -> list[dict]:
    root = ET.fromstring(read_url(url, "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.5"))
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
        host = (parsed_url.hostname or "").lower()
        if parsed_url.scheme not in ("http", "https") or not (host == "fl.ru" or host.endswith(".fl.ru")):
            continue
        description = clean_text(child_text(node, "description") or child_text(node, "summary") or child_text(node, "content"))
        pub = child_text(node, "pubDate") or child_text(node, "published") or child_text(node, "updated") or child_text(node, "date")
        published_at = parse_date(pub)
        if published_at:
            try:
                age_hours = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(published_at.replace("Z", "+00:00"))).total_seconds() / 3600
                if age_hours > 24 * 14:
                    continue
            except (TypeError, ValueError):
                pass
        price_min, price_max, budget_text = parse_money(title + "\n" + description)
        date_match = DATE_RE.search(title + "\n" + description)
        score, reasons, risks, matched, critical = score_item(title, description, published_at, price_max)
        digest = hashlib.sha256(link.split("#")[0].encode("utf-8")).hexdigest()[:16]
        records.append({
            "id": digest, "title": title[:180], "link": link,
            "description": description[:MAX_DESCRIPTION], "publishedAt": published_at,
            "source": "FL.ru RSS", "feedName": feed_name, "score": score,
            "fitReasons": reasons, "riskFlags": risks, "criticalRisk": critical,
            "hasRelevantKeywords": matched, "budgetMin": price_min, "budgetMax": price_max,
            "budgetText": budget_text, "currency": "RUB" if budget_text else "",
            "deadlineHint": date_match.group(1) if date_match else "",
            "locationText": "Уточнить доступность и условия у заказчика",
            "paymentCheck": "Проверь возможность регистрации и вывода средств до начала работы.",
        })
    return records


def salary_label(job: dict) -> tuple[int, int, str]:
    low, high = job.get("minSalary"), job.get("maxSalary")
    currency = str(job.get("currency") or "").strip().upper()
    period = str(job.get("salaryPeriod") or "annual").strip().lower()
    periods = {"hourly": "в час", "weekly": "в неделю", "fortnightly": "за 2 недели", "monthly": "в месяц", "annual": "в год"}
    def fmt(value: Any) -> str:
        try:
            return f"{float(value):,.0f}".replace(",", " ")
        except (TypeError, ValueError):
            return ""
    if low is None and high is None:
        return 0, 0, ""
    low_num = int(float(low)) if low is not None else 0
    high_num = int(float(high)) if high is not None else low_num
    if low is not None and high is not None and low_num != high_num:
        amount = f"{fmt(low)}–{fmt(high)}"
    else:
        amount = fmt(high if high is not None else low)
    label = " ".join(part for part in (currency, amount, periods.get(period, period)) if part)
    return low_num, high_num, label


def parse_himalayas_search(name: str, params: dict[str, str]) -> list[dict]:
    url = HIMALAYAS_API + "?" + urllib.parse.urlencode(params)
    payload = json.loads(read_url(url).decode("utf-8"))
    rows = payload.get("jobs", [])
    if not isinstance(rows, list):
        raise ValueError("Response has no jobs array")
    records: list[dict] = []
    for job in rows:
        if not isinstance(job, dict):
            continue
        title = clean_text(str(job.get("title") or ""))
        description = clean_text(str(job.get("description") or job.get("excerpt") or ""))
        application_link = str(job.get("applicationLink") or "").strip()
        parsed_url = urlparse(application_link)
        if not title or parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            continue

        restrictions = job.get("locationRestrictions") or []
        restriction_names = []
        restriction_codes = []
        for restriction in restrictions:
            if isinstance(restriction, dict):
                name_value = str(restriction.get("name") or restriction.get("country") or restriction.get("alpha2") or "")
                code_value = str(restriction.get("alpha2") or restriction.get("code") or "").upper()
            else:
                name_value = str(restriction or "")
                code_value = name_value.upper()
            if name_value:
                restriction_names.append(name_value)
            if code_value:
                restriction_codes.append(code_value)
        # Empty location restrictions means worldwide; non-empty must include Russia.
        if restrictions and "RU" not in restriction_codes and not any("russia" in name.lower() or "росси" in name.lower() for name in restriction_names):
            continue

        timezone_restrictions = job.get("timezoneRestrictions") or []
        timezone_display = ""
        if timezone_restrictions:
            compatible_russia_timezones = []
            parsed_offsets = []
            for zone in timezone_restrictions:
                text_zone = str(zone).strip().upper().replace("GMT", "UTC")
                match_zone = re.search(r"(?:UTC)?\s*([+-])\s*(\d{1,2})(?::(\d{2}))?", text_zone)
                if not match_zone:
                    continue
                sign = 1 if match_zone.group(1) == "+" else -1
                offset = sign * (int(match_zone.group(2)) + (int(match_zone.group(3) or 0) / 60))
                parsed_offsets.append(offset)
                if 2 <= offset <= 12 and float(offset).is_integer():
                    compatible_russia_timezones.append(str(zone))
            if parsed_offsets and not compatible_russia_timezones:
                continue
            if not parsed_offsets:
                timezone_display = "Часовой пояс ограничен: " + ", ".join(str(x) for x in timezone_restrictions[:4]) + " (проверь совместимость)"
            elif len(timezone_restrictions) < 12:
                timezone_display = "Временные зоны: " + ", ".join(compatible_russia_timezones[:5]) + " (уточни часовой пояс работодателя)"

        location_text = "Worldwide (географические ограничения не указаны)" if not restrictions else ", ".join(restriction_names)
        if timezone_display:
            location_text += "; " + timezone_display

        published_at = from_timestamp(job.get("pubDate"))
        expiry_at = from_timestamp(job.get("expiryDate"))
        if expiry_at:
            try:
                if dt.datetime.fromisoformat(expiry_at.replace("Z", "+00:00")) < dt.datetime.now(dt.timezone.utc):
                    continue
            except ValueError:
                pass

        low, high, budget_text = salary_label(job)
        score, reasons, risks, matched, critical = score_item(title, description, published_at, high)
        if not matched:
            continue
        categories = job.get("categories") or []
        if not isinstance(categories, list):
            categories = []
        employment = str(job.get("employmentType") or "").strip()
        seniority = job.get("seniority") or []
        if isinstance(seniority, str):
            seniority = [seniority]
        company = clean_text(str(job.get("companyName") or ""))
        guid = str(job.get("guid") or application_link)
        digest = hashlib.sha256(guid.encode("utf-8")).hexdigest()[:16]
        records.append({
            "id": digest, "title": title[:180], "link": application_link,
            "description": description[:MAX_DESCRIPTION], "publishedAt": published_at,
            "source": "Himalayas", "feedName": name, "score": score,
            "fitReasons": reasons, "riskFlags": risks, "criticalRisk": critical,
            "hasRelevantKeywords": matched, "budgetMin": low, "budgetMax": high,
            "budgetText": budget_text, "currency": str(job.get("currency") or "").upper(),
            "deadlineHint": "",
            "companyName": company, "employmentType": employment,
            "seniority": ", ".join(str(x) for x in seniority),
            "categories": [str(x) for x in categories[:5]],
            "locationText": location_text,
            "expiryAt": expiry_at,
            "paymentCheck": "Способ выплаты на российскую карту не подтверждён источником; уточни валюту, доступный способ перевода и ограничения работодателя до отклика.",
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

    # This API explicitly provides public JSON job data for dashboard use and is cached daily.
    for name, params in HIMALAYAS_SEARCHES:
        try:
            rows = parse_himalayas_search(name, params)
            all_items.extend(rows)
            feed_status.append({
                "name": name, "url": HIMALAYAS_API + "?" + urllib.parse.urlencode(params),
                "ok": True, "items": len(rows), "source": "Himalayas public API",
            })
            successes += 1
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
            message = f"{name}: {type(exc).__name__}: {str(exc)[:180]}"
            errors.append(message)
            feed_status.append({"name": name, "url": HIMALAYAS_API, "ok": False, "error": message})
        time.sleep(0.3)

    # FL.ru advertises an RSS subscription, but its endpoints may refuse cloud-runner requests.
    # A 403 is recorded as a source limitation, never bypassed.
    for feed_name, feed_url in FL_FEEDS:
        try:
            rows = parse_fl_feed(feed_name, feed_url)
            all_items.extend(rows)
            feed_status.append({"name": feed_name, "url": feed_url, "ok": True, "items": len(rows), "source": "FL.ru public RSS"})
            successes += 1
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
            message = f"{feed_name}: {type(exc).__name__}: {str(exc)[:180]}"
            errors.append(message)
            feed_status.append({"name": feed_name, "url": feed_url, "ok": False, "error": message})
        time.sleep(0.3)

    if successes == 0:
        print("All permitted public job sources failed; existing jobs.json left untouched.", file=sys.stderr)
        print(json.dumps({"feeds": feed_status}, ensure_ascii=False), file=sys.stderr)
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
    candidates.sort(key=lambda item: (item["score"], item["publishedAt"] or ""), reverse=True)
    candidates = candidates[:MAX_ITEMS]

    payload = {
        "schemaVersion": 1,
        "source": "Himalayas public API + FL.ru public RSS when reachable",
        "checkedAt": checked_at,
        "apiUpdatedAt": "Himalayas API data is refreshed approximately once every 24 hours.",
        "maxAgeNotice": "The source does not confirm the employer will still accept applications; open the listing and verify all terms before applying.",
        "attribution": "Data by Himalayas (https://himalayas.app); FL.ru RSS where available.",
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
    if errors:
        print("Some sources were unavailable: " + json.dumps(errors, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
