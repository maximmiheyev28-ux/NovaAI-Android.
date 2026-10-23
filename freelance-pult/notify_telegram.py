#!/usr/bin/env python3
"""Send a Telegram alert for newly discovered fresh public job listings."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ALLOWED_HOSTS = {
    "Himalayas": ("himalayas.app",),
    "Jobicy": ("jobicy.com",),
    "Remote OK": ("remoteok.com",),
    "We Work Remotely": ("weworkremotely.com",),
}
MIN_SCORE = 65
MAX_AGE_HOURS = 72
MAX_PER_MESSAGE = 5


def parse_time(value: object) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def canonical_link(value: str) -> str:
    parts = urlsplit(value.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def trusted_source_link(item: dict) -> bool:
    link = str(item.get("link") or "")
    parts = urlsplit(link)
    if parts.scheme != "https" or not parts.hostname:
        return False
    allowed = ALLOWED_HOSTS.get(str(item.get("source") or ""))
    if not allowed:
        return False
    host = parts.hostname.lower()
    return any(host == domain or host.endswith("." + domain) for domain in allowed)


def select_new_items(previous: dict, current: dict, now: dt.datetime | None = None) -> list[dict]:
    """Select unseen, fresh, high-relevance items from explicitly allowed public sources."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    old_links = set()
    for item in previous.get("items") or []:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "")
        if link:
            old_links.add(canonical_link(link))

    candidates = []
    for item in current.get("items") or []:
        if not isinstance(item, dict) or not item.get("title") or not trusted_source_link(item):
            continue
        if item.get("criticalRisk") or not item.get("hasRelevantKeywords"):
            continue
        try:
            score = int(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score < MIN_SCORE:
            continue
        published = parse_time(item.get("publishedAt"))
        if published is None:
            continue
        age = (now - published).total_seconds() / 3600
        if age < 0 or age > MAX_AGE_HOURS:
            continue
        if canonical_link(str(item["link"])) in old_links:
            continue
        candidates.append(item)

    candidates.sort(key=lambda item: (int(item.get("score") or 0), str(item.get("publishedAt") or "")), reverse=True)
    return candidates[:MAX_PER_MESSAGE]


def format_message(items: list[dict]) -> str:
    chunks = ["🆕 Новые подходящие удалённые заказы"]
    for item in items:
        title = " ".join(str(item.get("title") or "Без названия").split())[:180]
        source = " ".join(str(item.get("source") or "Источник не указан").split())[:60]
        score = int(item.get("score") or 0)
        budget = " ".join(str(item.get("budgetText") or "").split())[:80]
        location = " ".join(str(item.get("locationText") or "").split())[:90]
        link = str(item.get("link") or "")
        chunks.append(
            f"\n\n{title}\nИсточник: {source} · оценка {score}/100"
            + (f"\nОплата: {budget}" if budget else "")
            + (f"\nГеография: {location}" if location else "")
            + f"\n{link}"
        )
    chunks.append("\n\nПеред откликом проверь оригинальную карточку, географию, доступность выплаты и срок.")
    return "".join(chunks)[:3900]


def send_telegram(token: str, chat_id: str, text: str) -> None:
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode("utf-8")
    request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram returned HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Telegram request failed ({type(exc).__name__})") from None
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected the message: {str(result.get('description') or 'unknown error')[:200]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", required=True)
    parser.add_argument("--current", required=True)
    args = parser.parse_args()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram notification skipped: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are not configured.")
        return 0

    try:
        previous = json.loads(Path(args.previous).read_text(encoding="utf-8"))
        current = json.loads(Path(args.current).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Unable to read feed snapshots ({type(exc).__name__}).", file=sys.stderr)
        return 2

    items = select_new_items(previous, current)
    if not items:
        print("No new alert-worthy jobs.")
        return 0
    try:
        send_telegram(token, chat_id, format_message(items))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Telegram alert sent for {len(items)} new listing(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
