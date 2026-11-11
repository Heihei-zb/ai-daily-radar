"""打分与成稿：LLM 两阶段（筛选 → 写作），无 key 时走规则兜底。"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .model import Article
from .util import ROOT, truncate

CODE_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


@dataclass
class RankResult:
    overview: str
    items: list[dict] = field(default_factory=list)
    repos: list[dict] = field(default_factory=list)
    papers: list[dict] = field(default_factory=list)
    used_llm: bool = False
    note: str = ""


class LLMClient:
    def __init__(self, cfg: dict):
        self.base_url = str(cfg.get("base_url", "")).rstrip("/")
        self.model = cfg.get("model", "deepseek-chat")
        self.temperature = float(cfg.get("temperature", 0.2))
        self.timeout = int(cfg.get("timeout", 150))
        self.api_key = os.environ.get(cfg.get("api_key_env", "LLM_API_KEY"), "").strip()
        self.enabled = bool(cfg.get("enabled", True)) and bool(self.api_key)
        self.usage = {"calls": 0, "prompt": 0, "completion": 0}

    def chat(self, system: str, user: str, max_tokens: int = 2200) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                used = data.get("usage") or {}
                self.usage["calls"] += 1
                self.usage["prompt"] += int(used.get("prompt_tokens", 0) or 0)
                self.usage["completion"] += int(used.get("completion_tokens", 0) or 0)
                return data["choices"][0]["message"]["content"]
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败: {last}")


def _load_prompt(name: str) -> str:
    return (ROOT / "config" / name).read_text(encoding="utf-8")


def _parse_json(text: str, opening: str) -> dict | list:
    cleaned = CODE_FENCE_RE.sub("", text).strip()
    start = cleaned.find(opening)
    closing = "]" if opening == "[" else "}"
    end = cleaned.rfind(closing)
    if start < 0 or end <= start:
        raise ValueError(f"未找到 JSON: {cleaned[:120]}")
    return json.loads(cleaned[start : end + 1])


def _score_block(client: LLMClient, arts: list[Article], batch_size: int = 18) -> dict[int, dict]:
    """分批打分。一次塞 45 条会让输出被 max_tokens 截断，JSON 直接解析失败。"""
    system = _load_prompt("prompt_rank.md")
    result: dict[int, dict] = {}

    for start in range(0, len(arts), batch_size):
        chunk = arts[start : start + batch_size]
        lines = []
        for idx, art in enumerate(chunk, 1):
            snippet = truncate(art.summary.replace("\n", " "), 80)
            lines.append(f"{idx}. [{art.source}] {art.title} | {snippet}")
        user = f"今天是 {time.strftime('%Y-%m-%d')}。候选条目：\n" + "\n".join(lines)

        rows = None
        for attempt in range(2):
            try:
                raw = client.chat(system, user, max_tokens=2600)
                rows = _parse_json(raw, "[")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 1:
                    raise RuntimeError(f"打分 JSON 解析失败: {str(exc)[:100]}") from exc
                time.sleep(1)

        for row in rows or []:
            try:
                local = int(row["i"])
                result[start + local] = {
                    "score": float(row.get("score", 5)),
                    "category": str(row.get("category", "行业动态")).strip(),
                    "reason": str(row.get("reason", "")).strip(),
                }
            except (KeyError, TypeError, ValueError):
                continue
    return result


def _digest_block(client: LLMClient, entries: list[dict], date_cn: str) -> dict:
    lines = [
        f"{e['i']}. [{e['kind']}] {e['article'].title} | 来源 {e['article'].source} | "
        f"{truncate(e['article'].summary.replace(chr(10), ' '), 220)}"
        for e in entries
    ]
    user = f"日期：{date_cn}\n类型说明：新闻＝行业事件，开源项目＝GitHub 仓库，论文＝arXiv 预印本。\n\n" + "\n".join(lines)
    raw = client.chat(_load_prompt("prompt_digest.md"), user, max_tokens=6000)
    return _parse_json(raw, "{")


def rank(
    articles: list[Article],
    cfg: dict,
    date_cn: str,
    logger: logging.Logger,
    force_no_llm: bool = False,
) -> RankResult:
    app = cfg["app"]
    max_items = int(app.get("max_items", 12))
    min_score = float(app.get("min_score", 5.0))
    max_input = int(cfg["llm"].get("max_input_items", 45))

    news = [a for a in articles if a.source_id not in ("github", "arxiv")]
    repos = sorted(
        [a for a in articles if a.source_id == "github"],
        key=lambda a: a.extra.get("stars", 0),
        reverse=True,
    )[:4]
    papers = sorted(
        [a for a in articles if a.source_id == "arxiv"],
        key=lambda a: a.published,
        reverse=True,
    )[:3]

    client = LLMClient(cfg["llm"])
    use_llm = client.enabled and not force_no_llm
    note = ""
    if force_no_llm:
        note = "已按参数关闭 LLM"
    elif not client.enabled:
        note = "未配置 LLM_API_KEY，使用规则兜底（摘要为原文，未翻译）"

    # 中文模式且没有大模型时，英文的 GitHub / arXiv 板块无法翻译，先隐藏
    language = str(app.get("language", "zh"))
    if language == "zh" and not use_llm:
        repos, papers = [], []
        note += "；GitHub 与 arXiv 为英文内容，接入大模型翻译后再展示"

    news = news[:max_input]
    if use_llm and news:
        try:
            scores = _score_block(client, news)
            for idx, art in enumerate(news, 1):
                hit = scores.get(idx)
                if hit:
                    art.score = round(hit["score"], 1)
                    art.category = hit["category"] or art.category
                    art.extra["reason"] = hit["reason"]
            news = [a for a in news if a.score >= min_score]
            logger.info("LLM 打分完成，%d 条过线", len(news))
        except Exception as exc:  # noqa: BLE001
            use_llm = False
            note = f"LLM 打分失败，已降级：{str(exc)[:80]}"
            logger.warning(note)

    news.sort(key=lambda a: (-a.score, -a.published.timestamp()))
    selected_news = news[:max_items]

    entries: list[dict] = []
    for art in selected_news:
        entries.append({"i": len(entries) + 1, "kind": "新闻", "article": art})
    for art in repos:
        entries.append({"i": len(entries) + 1, "kind": "开源项目", "article": art})
    for art in papers:
        entries.append({"i": len(entries) + 1, "kind": "论文", "article": art})

    overview = ""
    written: dict[int, dict] = {}
    if use_llm and entries:
        try:
            data = _digest_block(client, entries, date_cn)
            overview = str(data.get("overview", "")).strip()
            for row in data.get("items", []):
                try:
                    written[int(row["i"])] = row
                except (KeyError, TypeError, ValueError):
                    continue
            logger.info("LLM 成稿完成，%d 条", len(written))
        except Exception as exc:  # noqa: BLE001
            use_llm = False
            note = f"LLM 成稿失败，已降级：{str(exc)[:80]}"
            logger.warning(note)

    def build(entry: dict) -> dict:
        art: Article = entry["article"]
        row = written.get(entry["i"], {})
        summary = str(row.get("summary", "")).strip() or truncate(art.summary, 110)
        return {
            "title": str(row.get("title", "")).strip() or art.title,
            "summary": summary,
            "why": str(row.get("why", "")).strip(),
            "category": art.category,
            "source": art.source,
            "url": art.url,
            "time": art.published.strftime("%m-%d %H:%M"),
            "score": art.score,
            "extra": art.extra,
            "orig_title": art.title,
        }

    logger.info(
        "LLM 用量：%d 次调用 / 输入 %s tokens / 输出 %s tokens",
        client.usage["calls"],
        f"{client.usage['prompt']:,}",
        f"{client.usage['completion']:,}",
    )

    result = RankResult(overview=overview, used_llm=use_llm, note=note)
    # 大模型按「同一事件合并」的规则会主动少写条目，此时缺失即视为被合并，不再回填原文
    trust_written = use_llm and len(written) >= max(3, int(len(entries) * 0.6))

    def keep(entry: dict) -> bool:
        return entry["i"] in written or not trust_written

    result.items = [build(e) for e in entries if e["kind"] == "新闻" and keep(e)]
    result.repos = [build(e) for e in entries if e["kind"] == "开源项目" and keep(e)]
    result.papers = [build(e) for e in entries if e["kind"] == "论文" and keep(e)]

    if not result.overview:
        result.overview = _fallback_overview(result)
    return result


def _fallback_overview(result: RankResult) -> str:
    from collections import Counter

    counter = Counter(item["category"] for item in result.items)
    if not counter:
        return "今日无符合条件的事件。"
    top = "、".join(f"{name} {count} 条" for name, count in counter.most_common(2))
    return f"今日收录 {len(result.items)} 条，集中在{top}。"
