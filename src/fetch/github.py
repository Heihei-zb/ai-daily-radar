"""GitHub 抓取：按「汽车行业 / AI 视频 / 数据分析」等关注方向检索仓库。

口径：走 Search API（github.com/trending 在部分网络下不可达）。
每个方向用主题词检索，按 star 排序；同一方向每天最多上 1 个，保证四张卡片覆盖不同领域。
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone

from ..model import Article
from ..util import http_get, normalize_url, to_tz, truncate

AI_TOPICS = {
    "ai", "llm", "llms", "agent", "agents", "ai-agents", "gpt", "chatgpt", "rag",
    "langchain", "transformer", "diffusion", "machine-learning", "deep-learning",
    "multimodal", "embeddings", "inference", "fine-tuning", "computer-vision", "nlp",
    "copilot", "chatbot", "openai", "prompt-engineering", "generative-ai", "llmops",
}
AI_WORD_RE = re.compile(
    r"\b(ai|llm|agent|agents|gpt|rag|diffusion|transformer|multimodal|inference|"
    r"embeddings|fine-?tuning|chatbot|copilot|neural|autonomous|text-to-sql)\b",
    re.IGNORECASE,
)
BLOCK_WORDS = (
    "awesome", "curated list", "tutorial", "course", "roadmap", "textbook",
    "sample code", "learning", "面试", "教程", "笔记", "学习",
)


def _is_ai(item: dict) -> bool:
    topics = {t.lower() for t in (item.get("topics") or [])}
    if topics & AI_TOPICS:
        return True
    text = f"{item.get('name', '')} {item.get('description') or ''}"
    if "人工智能" in text or "大模型" in text or "智能体" in text:
        return True
    return bool(AI_WORD_RE.search(text))


def _blocked(item: dict) -> bool:
    name = (item.get("name") or "").lower()
    desc = (item.get("description") or "").lower()
    if name.startswith("awesome") or name.endswith("-notes"):
        return True
    return any(word in desc for word in BLOCK_WORDS)


def _search(query: str, per_page: int = 20) -> list[dict]:
    """未登录搜索限 10 次/分钟，且会触发二级限流，失败退避重试一次。"""
    for attempt in range(2):
        try:
            resp = http_get(
                "https://api.github.com/search/repositories",
                params={"q": query, "sort": "stars", "order": "desc", "per_page": per_page},
                headers={"Accept": "application/vnd.github+json"},
                timeout=25,
                retries=0,
            )
            if resp.status_code == 200:
                return resp.json().get("items", [])
        except Exception:  # noqa: BLE001
            pass
        if attempt == 0:
            time.sleep(10)
    return []


def _match_focus(item: dict, focus: list[dict]) -> tuple[str | None, int, int, int]:
    """返回 (方向名, 得分, 关键词命中数, 主题命中数)。"""
    topics = {t.lower() for t in (item.get("topics") or [])}
    text = f"{item.get('name', '')} {item.get('description') or ''}".lower()
    best = (None, 0, 0, 0)
    for group in focus:
        topic_hits = len(topics & {t.lower() for t in group.get("topics", [])})
        kw_hits = sum(1 for kw in group.get("keywords", []) if kw.lower() in text)
        score = topic_hits * 3 + kw_hits
        if score > best[1]:
            best = (group["name"], score, kw_hits, topic_hits)
    return best


def fetch(source: dict) -> list[Article]:
    days = int(source.get("days", 30))
    min_stars = int(source.get("min_stars", 800))
    picks = int(source.get("picks", 4))
    focus = source.get("focus") or []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    pool: dict[str, dict] = {}
    for group in focus:
        recent = group.get("recent_days")
        prefix = ""
        if recent:
            since_recent = (datetime.now(timezone.utc) - timedelta(days=int(recent))).strftime("%Y-%m-%d")
            prefix = f"created:>{since_recent} "
        for query in group.get("queries", []):
            for item in _search(prefix + query):
                pool.setdefault(item["full_name"], item)
            time.sleep(4.5)

    # 方向池不够时，用近 30 天新项目兜底
    if len(pool) < picks * 4:
        for item in _search(f"created:>{since} stars:>{min_stars}"):
            pool.setdefault(item["full_name"], item)

    # 分桶
    exclude = source.get("_exclude") or set()
    buckets: dict[str, list[dict]] = {g["name"]: [] for g in focus}
    buckets["通用 AI"] = []
    for item in pool.values():
        if _blocked(item) or (item.get("stargazers_count", 0) < 60):
            continue
        if hashlib.sha1(normalize_url(item["html_url"]).encode("utf-8")).hexdigest() in exclude:
            continue  # 近几天上过榜，换下一个
        name, score, kw_hits, topic_hits = _match_focus(item, focus)
        # 光有泛主题（如 topic:automotive 里的 Android Auto）不算，必须描述里真的提到相关词
        if name and (kw_hits >= 1 or topic_hits >= 2):
            buckets[name].append(item)
        elif not name and _is_ai(item):
            buckets["通用 AI"].append(item)

    for bucket in buckets.values():
        # 先按「是否 AI 原生」再按 star 排序，避免通用 BI 工具把 AI 项目挤掉
        bucket.sort(key=lambda x: (_is_ai(x), x.get("stargazers_count", 0)), reverse=True)

    # 每个方向先取 1 个，再轮转补齐，避免同一方向占两个名额
    selected: list[tuple[dict, str]] = []
    used: set[str] = set()
    for group in focus:
        name = group["name"]
        if buckets[name]:
            item = buckets[name][0]
            selected.append((item, name))
            used.add(item["full_name"])

    while len(selected) < picks:
        added = False
        for group in focus:
            if len(selected) >= picks:
                break
            name = group["name"]
            item = next((x for x in buckets[name] if x["full_name"] not in used), None)
            if item:
                selected.append((item, name))
                used.add(item["full_name"])
                added = True
        if not added:
            break

    for item in buckets["通用 AI"]:
        if len(selected) >= picks:
            break
        if item["full_name"] not in used:
            selected.append((item, "通用 AI"))
            used.add(item["full_name"])

    out: list[Article] = []
    for item, group_name in selected[:picks]:
        out.append(
            Article(
                title=item["full_name"],
                url=item["html_url"],
                source="GitHub",
                source_id=source["id"],
                published=to_tz(
                    datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                ),
                summary=truncate(item.get("description") or "（无描述）", 160),
                category="开源生态",
                lang="en",
                extra={
                    "stars": item.get("stargazers_count", 0),
                    "forks": item.get("forks_count", 0),
                    "language": item.get("language") or "—",
                    "topics": (item.get("topics") or [])[:4],
                    "focus": group_name,
                },
            )
        )
    return out
