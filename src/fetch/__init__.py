"""信源调度：并发抓取，单源失败不影响整体。"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..model import Article
from . import arxiv, github, hn, rss

FETCHERS = {
    "rss": rss.fetch,
    "hn": hn.fetch,
    "arxiv": arxiv.fetch,
    "github": github.fetch,
}


def fetch_all(sources: list[dict], max_workers: int = 8) -> tuple[list[Article], list[dict]]:
    logger = logging.getLogger("ai-daily")
    articles: list[Article] = []
    report: list[dict] = []

    enabled = [s for s in sources if s.get("enabled", True)]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(FETCHERS[s["type"]], s): s
            for s in enabled
            if s["type"] in FETCHERS
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                items = future.result()
                articles.extend(items)
                report.append(
                    {"id": source["id"], "name": source["name"], "count": len(items), "ok": True}
                )
                logger.info("抓取 %-12s %2d 条", source["name"], len(items))
            except Exception as exc:  # noqa: BLE001
                report.append(
                    {
                        "id": source["id"],
                        "name": source["name"],
                        "count": 0,
                        "ok": False,
                        "error": str(exc)[:120],
                    }
                )
                logger.warning("抓取 %-12s 失败：%s", source["name"], str(exc)[:120])

    report.sort(key=lambda r: (not r["ok"], -r["count"]))
    return articles, report
