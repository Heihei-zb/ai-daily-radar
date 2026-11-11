"""arXiv 抓取：按分类取最新投稿。"""
from __future__ import annotations

from datetime import datetime, timezone

import feedparser

from ..model import Article
from ..util import http_get, strip_html, to_tz, truncate


def fetch(source: dict) -> list[Article]:
    cats = source.get("categories") or ["cs.AI"]
    limit = int(source.get("max_items", 10))
    query = " OR ".join(f"cat:{c}" for c in cats)

    resp = http_get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": limit * 2,
        },
        timeout=30,
    )
    parsed = feedparser.parse(resp.content)

    out: list[Article] = []
    for entry in parsed.entries:
        title = strip_html(entry.get("title", ""))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        parsed_time = entry.get("published_parsed")
        published = (
            to_tz(datetime(*parsed_time[:6], tzinfo=timezone.utc))
            if parsed_time
            else None
        )
        if not published:
            continue
        out.append(
            Article(
                title=title,
                url=link.replace("http://", "https://"),
                source="arXiv",
                source_id=source["id"],
                published=published,
                summary=truncate(strip_html(entry.get("summary", "")), 360),
                category="研究论文",
                lang="en",
                extra={
                    "authors": [
                        a.get("name", "") for a in entry.get("authors", [])[:3]
                    ],
                    "arxiv_id": entry.get("id", "").rsplit("/", 1)[-1],
                },
            )
        )
    return out[:limit]
