"""Hacker News 抓取：Algolia 官方检索接口，按时间窗 + 点数取当日热帖。"""
from __future__ import annotations

import time

from ..model import Article
from ..util import http_get, to_tz

from datetime import datetime


def fetch(source: dict) -> list[Article]:
    now = int(time.time())
    window = int(source.get("window_hours", 26)) * 3600
    min_points = int(source.get("min_points", 60))
    limit = int(source.get("max_items", 25))

    resp = http_get(
        "https://hn.algolia.com/api/v1/search_by_date",
        params={
            "tags": "story",
            "numericFilters": f"created_at_i>{now - window},points>{min_points}",
            "hitsPerPage": limit,
        },
        timeout=25,
    )
    hits = resp.json().get("hits", [])

    out: list[Article] = []
    for hit in hits:
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        published = hit.get("created_at") or ""
        try:
            dt = to_tz(datetime.fromisoformat(published.replace("Z", "+00:00")))
        except ValueError:
            continue
        out.append(
            Article(
                title=title,
                url=url,
                source=source["name"],
                source_id=source["id"],
                published=dt,
                summary=f"Hacker News 首页热帖，{hit.get('points', 0)} 分、{hit.get('num_comments', 0)} 条讨论。",
                lang="en",
                extra={
                    "points": hit.get("points", 0),
                    "comments": hit.get("num_comments", 0),
                    "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                },
            )
        )
    return out
