"""清洗：时效窗、AI 关键词、跨源去重、启发式预打分。"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from .model import Article
from .util import has_cjk, normalize_url, similarity

CATEGORY_PATTERNS = [
    ("研究论文", r"arxiv|论文|预印本|benchmark|ablation"),
    ("融资并购", r"融资|估值|收购|并购|\bipo\b|亿元|亿美元|天使轮|[ab]轮融资|funding|acquire"),
    ("开源生态", r"开源|github|仓库|权重公开|open[- ]source"),
    ("模型发布", r"大模型|新模型|基础模型|gpt-?\d|gemini|claude|qwen|llama|deepseek|ernie|参数量|上下文窗口"),
    ("产品应用", r"发布|推出|上线|面世|功能|插件|助手|落地"),
]

HOT_WORDS = [
    "发布", "推出", "上线", "开源", "融资", "收购", "并购", "合作", "签约",
    "芯片", "算力", "模型", "智能体", "裁员", "离职", "任命", "IPO", "监管",
]


def build_keyword_matcher(keywords: list[str]):
    """英文短词按词边界匹配，避免 'claims' 命中 'AI' 这类误判。"""
    cjk = {k for k in keywords if not k.isascii()}
    latin = sorted({k.lower() for k in keywords if k.isascii()}, key=len, reverse=True)
    pattern = (
        re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(k) for k in latin) + r")(?![a-z0-9])")
        if latin
        else None
    )

    def match(text: str) -> bool:
        low = (text or "").lower()
        if any(k in low for k in cjk):
            return True
        return bool(pattern.search(low)) if pattern else False

    return match


def guess_category(article: Article) -> str:
    text = f"{article.title} {article.summary}".lower()
    for name, pattern in CATEGORY_PATTERNS:
        if re.search(pattern, text):
            return name
    return "行业动态"


def pre_score(article: Article, weight: float, now: datetime) -> float:
    base = 5.0 + (weight - 1.0) * 3.0
    age_hours = max(0.0, (now - article.published).total_seconds() / 3600)
    if age_hours <= 6:
        recency = 1.2
    elif age_hours <= 12:
        recency = 0.7
    elif age_hours <= 24:
        recency = 0.3
    else:
        recency = 0.0

    hits = sum(1 for w in HOT_WORDS if w in article.title)
    hot = min(1.0, hits * 0.35)

    extra = 0.0
    if article.extra.get("points", 0) >= 300:
        extra += 1.2
    elif article.extra.get("points", 0) >= 150:
        extra += 0.6
    if article.extra.get("stars", 0) >= 500:
        extra += 1.0
    elif article.extra.get("stars", 0) >= 200:
        extra += 0.6

    return round(min(9.6, base + recency + hot + extra), 2)


def clean(
    articles: list[Article],
    sources: list[dict],
    ai_keywords: list[str],
    window_start: datetime,
    window_end: datetime,
    near_dup_threshold: float = 0.72,
    language: str = "zh",
) -> list[Article]:
    logger = logging.getLogger("ai-daily")
    src_map = {s["id"]: s for s in sources}
    match_ai = build_keyword_matcher(ai_keywords)

    kept: list[Article] = []
    dropped_window = dropped_filter = dropped_lang = 0
    default_hours = max(1.0, (window_end - window_start).total_seconds() / 3600)

    for art in articles:
        art.url = normalize_url(art.url)
        if not art.url:
            continue
        source = src_map.get(art.source_id, {})
        # 部分源有独立时效窗：GitHub 取近 30 天新建，arXiv 放宽到 48 小时
        hours = float(source.get("window_hours") or default_hours)
        if not source.get("ignore_window") and not (
            window_end - timedelta(hours=hours) <= art.published <= window_end
        ):
            dropped_window += 1
            continue

        # 中文-only：GitHub / arXiv 先放行，由 rank 决定是否翻译或丢弃
        if (
            language == "zh"
            and art.source_id not in ("github", "arxiv")
            and not has_cjk(art.title)
        ):
            dropped_lang += 1
            continue

        if source.get("ai_filter") and not match_ai(f"{art.title} {art.summary}"):
            dropped_filter += 1
            continue

        if not art.category or art.category == "行业动态":
            art.category = guess_category(art)
        art.score = pre_score(art, float(source.get("weight", 1.0)), window_end)
        kept.append(art)

    # 精确去重
    seen: dict[str, Article] = {}
    for art in kept:
        current = seen.get(art.url)
        if current is None or art.score > current.score:
            seen[art.url] = art
    unique = list(seen.values())

    # 近似去重（同一事件多源转载）
    unique.sort(key=lambda a: (-a.score, -len(a.summary)))
    deduped: list[Article] = []
    for art in unique:
        duplicate = next(
            (k for k in deduped if similarity(k.title, art.title) >= near_dup_threshold),
            None,
        )
        if duplicate is not None:
            duplicate.extra.setdefault("mirrors", []).append(art.source)
            continue
        deduped.append(art)

    logger.info(
        "清洗：原始 %d → 时效窗剔除 %d → 非中文剔除 %d → 关键词剔除 %d → 去重后 %d",
        len(articles),
        dropped_window,
        dropped_lang,
        dropped_filter,
        len(deduped),
    )
    deduped.sort(key=lambda a: (-a.score, -a.published.timestamp()))
    return deduped
