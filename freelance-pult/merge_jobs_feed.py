#!/usr/bin/env python3
"""Merge today's feed with a short-lived last-good snapshot when sources fail."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def remove_stale_auto_package(item: dict, now: dt.datetime) -> dict:
    """Drop cloud-generated material once the source listing is older than 72 hours."""
    if "autoPackage" not in item:
        return item
    published = parse_time(item.get("publishedAt") or item.get("firstSeenAt") or item.get("discoveredAt"))
    if published is None or (now - published).total_seconds() < 0 or (now - published).total_seconds() > 72 * 3600:
        item.pop("autoPackage", None)
    return item


def blocked_cached_item(item: dict) -> bool:
    """Carry only Kwork listings; never resurrect records from old sources."""
    source = str(item.get("source") or "").strip().lower()
    link = str(item.get("link") or "").strip().lower()
    try:
        host = (urlsplit(link).hostname or "").lower()
    except ValueError:
        host = ""
    if source != "kwork" or not (host == "kwork.ru" or host.endswith(".kwork.ru")):
        return True

    geo = str(item.get("locationText") or "").lower().strip()
    if not geo:
        return False
    if re.search(r"worldwide|anywhere|global|весь мир|по всему миру|russia|росси|cis|снг", geo):
        return False
    if re.search(r"europe|european|emea", geo):
        return False
    if re.search(r"not specified|location unknown|geography unknown|unknown|не указана|не указан|не указано|география не указана|уточн|не подтвержд|remote[- ]first|^remote$", geo):
        return False
    return True



def too_old_to_carry(item: dict, now: dt.datetime) -> bool:
    """Do not resurrect stale project-based gigs; longer remote jobs get a wider window."""
    published = parse_time(item.get("publishedAt") or item.get("firstSeenAt") or item.get("discoveredAt"))
    if published is None:
        return False
    return (now - published).total_seconds() > 14 * 86400


def url_key(value: str) -> str:
    try:
        parts = urlsplit(value)
        # UTM parameters are tracking only and should not create duplicate cards.
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    except Exception:
        return value.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", default="freelance-pult/jobs.json")
    parser.add_argument("--previous", default="/tmp/previous-jobs.json")
    parser.add_argument("--output", default="freelance-pult/jobs.json")
    parser.add_argument("--cache-hours", type=float, default=36)
    args = parser.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    current_path = Path(args.current)
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current_items = current.get("items") or []
    previous = {}
    previous_path = Path(args.previous)
    if previous_path.exists():
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = {}

    prior_checked = parse_time(previous.get("checkedAt"))
    fresh_map: dict[str, dict] = {}
    for item in current_items:
        if not isinstance(item, dict) or not item.get("link"):
            continue
        item = dict(item)
        item["lastSeenAt"] = current.get("checkedAt") or now.isoformat().replace("+00:00", "Z")
        item.pop("cachedFromAt", None)
        key = url_key(str(item["link"]))
        if key not in fresh_map or int(item.get("score") or 0) > int(fresh_map[key].get("score") or 0):
            fresh_map[key] = item

    carried = 0
    previous_items = previous.get("items") or []
    for original in previous_items:
        if not isinstance(original, dict) or not original.get("link") or original.get("criticalRisk"):
            continue
        # Apply current geography policy to cached entries too; do not resurrect restricted markets.
        if blocked_cached_item(original):
            continue
        key = url_key(str(original["link"]))
        if key in fresh_map:
            continue
        if too_old_to_carry(original, now):
            continue
        cached_time = parse_time(original.get("cachedFromAt") or original.get("lastSeenAt")) or prior_checked
        if cached_time is None or (now - cached_time).total_seconds() > args.cache_hours * 3600:
            continue
        published = parse_time(original.get("publishedAt"))
        expiry = parse_time(original.get("expiryAt"))
        if expiry and expiry < now:
            continue
        item = dict(original)
        item["cachedFromAt"] = (original.get("cachedFromAt") or original.get("lastSeenAt") or
                                (prior_checked.isoformat().replace("+00:00", "Z") if prior_checked else ""))
        item["lastSeenAt"] = item["cachedFromAt"]
        fresh_map[key] = item
        carried += 1

    merged = list(fresh_map.values())
    merged.sort(key=lambda item: (
        int(item.get("score") or 0),
        str(item.get("publishedAt") or ""),
    ), reverse=True)
    merged = merged[:100]
    # Do not retain generated drafts/plans once their source listing is older than 72 hours.
    for item in merged:
        remove_stale_auto_package(item, now)

    stats = current.get("stats") or {}
    stats["freshCandidates"] = len(current_items)
    stats["carriedFromPrevious"] = carried
    stats["matchedCandidates"] = len(merged)
    current["stats"] = stats
    current["items"] = merged
    current["maxAgeNotice"] = (
        "Показываются только проекты Kwork. Если публичная страница временно не отвечает, последняя выборка хранится максимум 36 часов, "
        "если срок проекта не истёк. Проверь оригинальную карточку перед откликом."
    )
    current["attribution"] = (
        "Источник: официальная публичная страница проектов Kwork (kwork.ru/projects). Открой оригинальную карточку перед откликом."
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "fresh": len(current_items),
        "carried": carried,
        "published": len(merged),
        "feedErrors": len(stats.get("feedErrors") or []),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
