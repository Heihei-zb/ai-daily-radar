"""RSS / Atom 抓取适配器。"""
from __future__ import annotations

from datetime import datetime, timezone

import feedparser

from ..model import Article
from ..util import http_get, now_tz, strip_html, to_tz, truncate


def _entry_time(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            return to_tz(datetime(*parsed[:6], tzinfo=timezone.utc))
    return now_tz()


def _entry_summary(entry) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    if not raw and entry.get("content"):
        raw = entry["content"][0].get("value", "")
    return truncate(strip_html(raw), 320)


def fetch(source: dict) -> list[Article]:
    resp = http_get(source["url"], timeout=25)
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"feed 解析失败: {parsed.get('bozo_exception')}")

    out: list[Article] = []
    for entry in parsed.entries:
        title = strip_html(entry.get("title", ""))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        out.append(
            Article(
                title=title,
                url=link,
                source=source["name"],
                source_id=source["id"],
                published=_entry_time(entry),
                summary=_entry_summary(entry),
                lang="zh" if any("\u4e00" <= ch <= "\u9fff" for ch in title) else "en",
            )
        )
    return out
